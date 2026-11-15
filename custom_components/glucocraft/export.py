# License: AGPL-3.0-only (LICENSE).
"""CSV export and import of the stored readings: the action glucocraft.export_csv and the Maintenance page's
button, and the action glucocraft.import_csv, which reads such an export back in.

GlucoCraft keeps readings for the retention period (200 days), so an export is the way to keep them longer
or to use them elsewhere. Files go to <config>/glucocraft_exports/ (not reachable from the web); the last 12 exports of
each kind are kept.

  glucocraft-readings_<YYYY-MM-DD_HHMMSS>.csv   one row per stored reading, oldest first:
      local_time       ISO 8601 with the UTC offset, in Home Assistant's time zone
      utc_time         ISO 8601, UTC ("Z")
      glucose_mg_dl    the stored value, mg/dL, one decimal; empty for a LO / HI reading (a reading at the sensor's
                       limit has no measured value)
      glucose_display  the value in the display unit, as GlucoCraft shows it (mg/dL whole, mmol/L one decimal; a LO / HI
                       reading as LO / HI, never as the limit's number)
      display_unit     mg/dL or mmol/L
      source           primary / secondary ("" for a reading of unknown source)
      limit            low (LO) / high (HI) for a reading at the sensor's limit, else ""
  glucocraft-lab-a1c_<same stamp>.csv           the laboratory A1C results, only when results exist:
      date             the date of the blood test (YYYY-MM-DD)
      a1c_percent      the result in percent (a result entered in mmol/mol converted, as stored)
      a1c_mmol_mol     the result in mmol/mol (a result entered in percent converted with the IFCC-NGSP master
                       equation, whole mmol/mol, as the CGM Report shows it)
      entered_unit     percent / mmol_mol: the unit the result was entered in, in which the import reads it

Numbers always use the decimal point (data files), UTF-8, comma-separated, a header row. The column names of the
header row are the catalogue's export.column.<column> (the names above in English).

Import (read_readings_csv, read_lab_csv): an export of either display unit, also after it was saved again by a
spreadsheet program with the decimal comma (and then usually semicolons between the columns). The columns are found by
their names in the language in use or in English. The readings file is read one row at a time, keeping only the
readings (an export of 200 days of 1-minute readings has 288,000 rows). A reading's value is glucose_mg_dl, the stored
value; without it, glucose_display in display_unit, and for a LO / HI reading (the limit column) the lower / upper
limit of the glucose sources in use (coordinator.sensor_span), as a LO / HI reading is stored at the sensor's limit.
The time is utc_time, else local_time (with its UTC offset). A row that is not a plausible reading (a time before 2015,
a value outside 20-500 mg/dL, an unknown limit flag) is left out and counted.
"""

from __future__ import annotations

import csv
import io
import itertools
import logging
import math
import os
import re
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, tzinfo
from typing import TypedDict

from .const import DEFAULT_RETENTION_DAYS, EXPORT_DIR, EXPORT_KEEP, SOURCE_NAMES, SOURCE_UNKNOWN, STORED_MGDL
from .i18n import t, tp
from .limits import DEFAULT_LOWER, DEFAULT_UPPER, LIMIT_FLAGS, LIMIT_LOW
from .regional import a1c_to_mmol_mol
from .storage import EARLIEST_PLAUSIBLE_TS, GlucoseReading
from .units import fmt, round_half_up, to_mgdl, unit_from_attribute

READINGS_PREFIX = "glucocraft-readings_"
LAB_PREFIX = "glucocraft-lab-a1c_"
# The names write_export gives its files (prefix, time stamp, .csv) and their temporary files (.tmp): the removal
# deletes only these, never another file in the folder (__init__._remove_data_files)
EXPORT_FILE_NAME = re.compile(
    rf"({re.escape(READINGS_PREFIX)}|{re.escape(LAB_PREFIX)})[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}_[0-9]{{6}}[.]csv([.]tmp)?"
)
_LOGGER = logging.getLogger(__name__)
# The columns of each file, by their key; the header row names them in the language in use (export.column.<key>)
READING_COLUMNS = ("local_time", "utc_time", "glucose_mg_dl", "glucose_display", "display_unit", "source", "limit")
LAB_COLUMNS = ("date", "a1c_percent", "a1c_mmol_mol", "entered_unit")
# The units a laboratory result is entered in (the action add_lab_a1c; the column entered_unit)
LAB_PERCENT, LAB_MMOL_MOL = "percent", "mmol_mol"


def _header(columns: tuple[str, ...]) -> list[str]:
    """Return the header row: the name of each column in the language in use."""
    return [t(f"export.column.{c}") for c in columns]


def export_message(result: ExportResult) -> str:
    """Return the notification text of an export: how many readings, where, the file names and how many are kept."""
    return tp(
        "export.done",
        result["readings"],
        folder=EXPORT_DIR,
        files=t("export.file_separator").join(result["files"]),
        keep=EXPORT_KEEP,
    )


def readings_csv(readings: list, unit: str, tz: tzinfo) -> str:
    """The readings as CSV text (see the module text)."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(_header(READING_COLUMNS))
    for r in readings:
        utc = datetime.fromtimestamp(r.timestamp, UTC)
        w.writerow(
            (
                utc.astimezone(tz).isoformat(),
                utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "" if r.limit else f"{r.raw:.1f}",  # a reading at the sensor's limit has no measured value
                _display(r, unit),
                unit,
                SOURCE_NAMES.get(r.source, ""),
                r.limit or "",
            )
        )
    return out.getvalue()


def _display(reading: GlucoseReading, unit: str) -> str:
    """Return a reading as every display shows it: "LO" / "HI" for a reading at the sensor's limit (the limit's number
    is no measured value), otherwise the value in the display unit (units.fmt)."""
    return t(f"limit.{reading.limit}") if reading.limit in LIMIT_FLAGS else fmt(reading.raw, unit)


def lab_csv(rows: list[dict]) -> str:
    """Return the laboratory A1C results as CSV text, oldest first: each result in both units and the unit it was
    entered in. A result is stored with a1c_mmol_mol only when it was entered in mmol/mol (report_service.lab_entry);
    a result entered in percent is converted with the master equation, whole mmol/mol, as report.lab_payload does."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(_header(LAB_COLUMNS))
    for r in sorted(rows, key=lambda x: x.get("date", "")):
        pct, mm = r.get("a1c"), r.get("a1c_mmol_mol")
        entered = LAB_MMOL_MOL if mm is not None else LAB_PERCENT
        if mm is None and pct is not None:
            mm = round_half_up(a1c_to_mmol_mol(pct))
        w.writerow((r.get("date", ""), "" if pct is None else f"{pct:g}", "" if mm is None else f"{mm:g}", entered))
    return out.getvalue()


def _write(path: str, text: str) -> None:
    """Write a text to a file through a temporary file, so that the file is either complete or absent."""
    with open(path + ".tmp", "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(path + ".tmp", path)


def _exports(names: list[str], prefix: str) -> list[str]:
    """Return the names among `names` that write_export gave one kind of export (`prefix`, EXPORT_FILE_NAME, not its
    temporary file), sorted, so oldest first (the names sort by their time stamp).

    The exact name, not only the prefix: a file the user copied into the folder under another name (say
    glucocraft-readings_backup.csv) would sort after every time stamp, and so be taken as the newest export and fill
    one of the kept places."""
    return sorted(f for f in names if f.startswith(prefix) and f.endswith(".csv") and EXPORT_FILE_NAME.fullmatch(f))


def _prune(folder: str, prefix: str, keep: int = EXPORT_KEEP) -> None:
    """Delete the older exports of one kind, keeping the newest `keep`. A file that cannot be deleted stays (the
    export itself has succeeded) and is logged, so that a folder that keeps growing can be traced. A file with another
    name is never deleted (_exports)."""
    old = _exports(os.listdir(folder), prefix)
    for f in old[:-keep] if keep > 0 else old:
        try:
            os.remove(os.path.join(folder, f))
        except OSError as err:
            _LOGGER.debug("Export: could not remove the old export %s: %s", f, err)


class ExportResult(TypedDict):
    """What one export wrote (write_export)."""

    folder: str  # the folder of the files
    files: list[str]  # the file names: the readings, then the laboratory results (when there are any)
    readings: int  # the number of readings exported


def write_export(
    folder: str, readings: list, unit: str, tz: tzinfo, lab_rows: list[dict] | None = None, now: datetime | None = None
) -> ExportResult:
    """Write one export (executor) and return what it wrote."""
    os.makedirs(folder, exist_ok=True)
    stamp = (now or datetime.now(tz)).astimezone(tz).strftime("%Y-%m-%d_%H%M%S")
    files = []
    name = f"{READINGS_PREFIX}{stamp}.csv"
    _write(os.path.join(folder, name), readings_csv(readings, unit, tz))
    files.append(name)
    if lab_rows:
        lab_name = f"{LAB_PREFIX}{stamp}.csv"
        _write(os.path.join(folder, lab_name), lab_csv(lab_rows))
        files.append(lab_name)
    _prune(folder, READINGS_PREFIX)
    _prune(folder, LAB_PREFIX)
    return ExportResult(folder=folder, files=files, readings=len(readings))


# ---------------------------------------------------------------------------------------------- the import
# A plausible reading of an import: a time from 2015 on, a value of 20 to 500 mg/dL (const.STORED_MGDL), the stored
# readings' rule (storage._plausible)
_LOWEST, _HIGHEST = STORED_MGDL
_SOURCE_CODES = {name: code for code, name in SOURCE_NAMES.items()}


class NotAnExport(ValueError):
    """A file that is not one of GlucoCraft's CSV exports (its header row lacks the columns the import needs)."""


def _rows(source: str | Iterable[str]) -> Iterator[list[str]]:
    """Return the rows of a CSV text or of an open CSV file (its lines), one at a time: comma-, semicolon- or
    tab-separated, whichever the header row uses most (a spreadsheet program that writes the decimal comma separates
    the columns with semicolons). Only the header line is looked at before the rows are read."""
    lines = iter(io.StringIO(source) if isinstance(source, str) else source)
    header = next(lines, "").lstrip("\ufeff")
    delimiter = max((",", ";", "\t"), key=header.count)
    return csv.reader(itertools.chain((header,), lines), delimiter=delimiter)


def _columns(header: list[str], keys: tuple[str, ...]) -> dict[str, int]:
    """Return the index of each known column of a header row, found by its name in the language in use or in
    English (the column's key)."""
    names = {name: key for key in keys for name in (key, t(f"export.column.{key}"))}
    return {names[h.strip()]: i for i, h in enumerate(header) if h.strip() in names}


def _number(text: str) -> float:
    """Return a number written with the decimal point or the decimal comma; raise ValueError for anything else."""
    text = text.strip()
    value = float(text.replace(",", ".") if "," in text and "." not in text else text)
    if not math.isfinite(value):
        raise ValueError(text)
    return value


def _cell(row: list[str], col: dict[str, int], key: str) -> str:
    """Return a row's cell of a column, "" when the file has no such column."""
    return row[col[key]].strip() if key in col else ""


def _reading(row: list[str], col: dict[str, int], span: tuple[float, float]) -> GlucoseReading:
    """Return one row as a reading; raise ValueError (or IndexError) for a row that is not a plausible reading.

    span: the lower and the upper limit (mg/dL) a LO / HI reading is stored at when the row has no glucose_mg_dl."""
    when = _cell(row, col, "utc_time") or _cell(row, col, "local_time")
    stamp = datetime.fromisoformat(when)  # "Z" as well (Python 3.11 and later)
    if stamp.tzinfo is None:
        raise ValueError(when)  # a time without its offset is not a moment
    ts = int(stamp.timestamp())
    stored = _cell(row, col, "glucose_mg_dl")
    limit = _cell(row, col, "limit") or None
    if stored:
        mg = round_half_up(_number(stored), 1)
    elif limit in LIMIT_FLAGS:  # glucose_display holds LO / HI: the reading is stored at the sensor's limit
        mg = float(span[0] if limit == LIMIT_LOW else span[1])
    else:
        unit = unit_from_attribute(_cell(row, col, "display_unit"))
        if unit is None:
            raise ValueError("unit")
        mg = to_mgdl(_number(_cell(row, col, "glucose_display")), unit)
    if ts < EARLIEST_PLAUSIBLE_TS or not _LOWEST <= mg <= _HIGHEST or (limit is not None and limit not in LIMIT_FLAGS):
        raise ValueError(when)
    source = _SOURCE_CODES.get(_cell(row, col, "source"), SOURCE_UNKNOWN)
    return GlucoseReading(ts, mg, limit, source)


def read_readings_csv(
    source: str | Iterable[str], span: tuple[float, float] = (DEFAULT_LOWER, DEFAULT_UPPER)
) -> tuple[list[GlucoseReading], int]:
    """Return the readings of a readings export, oldest first, and the number of rows left out (see the module text).

    source: the file's text, or the open file (its lines), whose rows are then read one at a time (read_readings_file).

    span: the lower and the upper limit of the glucose sources in use (coordinator.sensor_span), the value of a LO / HI
    row without glucose_mg_dl; without a source, the widest limits.

    Raise NotAnExport when the header row lacks a time column (utc_time or local_time) or a value (glucose_mg_dl,
    or glucose_display with display_unit).
    """
    rows = _rows(source)
    col = _columns(next(rows, []), READING_COLUMNS)
    timed = "utc_time" in col or "local_time" in col
    valued = "glucose_mg_dl" in col or {"glucose_display", "display_unit"} <= set(col)
    if not (timed and valued):
        raise NotAnExport
    out: list[GlucoseReading] = []
    left_out = 0
    for row in rows:
        if not any(cell.strip() for cell in row):
            continue
        try:
            out.append(_reading(row, col, span))
        except (ValueError, IndexError):
            left_out += 1
    out.sort(key=lambda r: r.timestamp)
    return out, left_out


def read_lab_csv(text: str) -> tuple[list[tuple[str, float, str]], int]:
    """Return the laboratory A1C results of a laboratory export, (date, value, "percent" or "mmol_mol") each in the
    unit it was entered in (the column entered_unit), so that it is stored as it was, and the number of rows that
    could not be read. A row without a valid entered_unit (a file made by hand or by another program, or with that
    column left out): a filled a1c_mmol_mol cell means mmol/mol, otherwise the a1c_percent cell is read. A row that
    cannot be read is left out and counted (report_service.lab_entry checks each result again; the import states both
    counts); an empty row is skipped."""
    rows = _rows(text)
    col = _columns(next(rows, []), LAB_COLUMNS)
    out = []
    unreadable = 0
    for row in rows:
        if not any(cell.strip() for cell in row):
            continue
        try:
            date, mm, entered = (
                _cell(row, col, "date"),
                _cell(row, col, "a1c_mmol_mol"),
                _cell(row, col, "entered_unit"),
            )
            if entered not in (LAB_PERCENT, LAB_MMOL_MOL):
                entered = LAB_MMOL_MOL if mm else LAB_PERCENT
            column = "a1c_mmol_mol" if entered == LAB_MMOL_MOL else "a1c_percent"
            out.append((date, _number(_cell(row, col, column)), entered))
        except (ValueError, IndexError):
            unreadable += 1
    return out, unreadable


def read_readings_file(
    path: str, span: tuple[float, float] = (DEFAULT_LOWER, DEFAULT_UPPER)
) -> tuple[list[GlucoseReading], int]:
    """Read a readings export from its file one row at a time (executor; UTF-8, a byte order mark allowed): only the
    readings are kept in memory, never the file's text or its rows (read_readings_csv)."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return read_readings_csv(fh, span)


def lab_file_of(readings_file: str) -> str | None:
    """Return the name of the laboratory export written with a readings export (the same time stamp), else None."""
    if readings_file.startswith(READINGS_PREFIX):
        return LAB_PREFIX + readings_file[len(READINGS_PREFIX) :]
    return None


def newest_export(folder: str) -> str | None:
    """Return the name of the newest readings export in `folder` (the names sort by their time stamp), else None. Only
    a name write_export gives counts (_exports); a file under another name is imported only when the action names it."""
    try:
        names = _exports(os.listdir(folder), READINGS_PREFIX)
    except OSError:
        return None
    return names[-1] if names else None


def read_text(path: str) -> str:
    """Return a CSV file's text (executor); UTF-8, a byte order mark allowed."""
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


def import_message(result: dict) -> str:
    """Return the notification text of an import: the readings imported, those already stored (kept as they are),
    those outside the days GlucoCraft keeps, the rows that could not be read, the laboratory results imported, those
    of a date already stored (kept as they are) and the laboratory rows left out (unreadable, or refused by
    report_service.lab_entry) (each sentence only when its count is not zero, the first always)."""
    parts = [tp("import.done", result["imported"], file=result["file"], folder=EXPORT_DIR)]
    for key, count, params in (
        ("import.already_stored", result["already_stored"], {}),
        ("import.not_kept", result["not_kept"], {"days": DEFAULT_RETENTION_DAYS}),
        ("import.not_read", result["not_read"], {}),
        ("import.lab", result["lab_imported"], {}),
        ("import.lab_already_stored", result["lab_already_stored"], {}),
        ("import.lab_not_read", result["lab_not_read"], {}),
    ):
        if count:
            parts.append(tp(key, count, **params))
    return " ".join(parts)
