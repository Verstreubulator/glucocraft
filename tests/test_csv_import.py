# License: AGPL-3.0-only (LICENSE).
"""The CSV import (export.read_readings_csv / read_lab_csv, coordinator.import_readings, the action
glucocraft.import_csv).

Rules under test:
- A round trip, export -> the readings deleted -> import, gives back every reading as it was stored: time, value,
  LO / HI flag and source; the laboratory results come back as entered (percent, or mmol/mol): the laboratory export
  holds each result in both units and the unit it was entered in (entered_unit), which the import reads.
- The readings export is read one row at a time from the open file; only the readings are kept.
- Both display units and both decimal separators are read: an export in mmol/L, and an export saved again by a
  spreadsheet program with the decimal comma and semicolons; without glucose_mg_dl the value in the display unit.
- Readings already stored win: a reading within 30 s of a stored one is never imported, a stored one never changed or
  doubled; a laboratory result of a date already stored is kept, and counted.
- Only readings within the retention are taken; a row that is not a plausible reading is left out and counted; a file
  without the export's columns is refused. A laboratory row that cannot be read, or whose result lab_entry refuses
  (a date that does not exist, a value outside the span), is left out and counted; the notification says so.
- The action names the file in glucocraft_exports only (no folder), defaults to the newest export, states the counts
  in a notification and returns them; it is an administrator action.
"""

from __future__ import annotations

import asyncio
import time
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest
from glucocraft import const as C
from glucocraft import export as EX
from glucocraft.storage import GlucoseReading

from conftest import LA, add_reading, real_coordinator
from test_entity_ids import _load_init

NOW = int(time.time()) // 300 * 300


def _stored(c, n=40):
    """n readings every 5 minutes up to NOW, with a LO and a HI reading and both sources."""
    for k in range(n):
        ts = NOW - (n - k) * 300
        limit, value = ("low", 40.0) if k == 5 else ("high", 500.0) if k == 6 else (None, 100.0 + k + 0.4)
        add_reading(c.storage, ts, value, limit, C.SOURCE_SECONDARY if k % 7 == 0 else C.SOURCE_PRIMARY)


def _key(readings):
    return [(r.timestamp, r.raw, r.limit, r.source) for r in readings]


# ------------------------------------------------------------------------------------------------ the round trip
@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
def test_round_trip_export_delete_import(tmp_path, unit):
    c = real_coordinator(tmp_path / "a")
    _stored(c)
    before = _key(c.storage.readings)
    lab = [{"date": "2026-01-10", "a1c": 7.1}, {"date": "2026-04-02", "a1c": 6.68, "a1c_mmol_mol": 49}]
    result = EX.write_export(str(tmp_path / "x"), list(c.storage.readings), unit, LA, lab)
    readings_file, lab_file = result["files"]
    assert EX.lab_file_of(readings_file) == lab_file and EX.newest_export(str(tmp_path / "x")) == readings_file
    fresh = real_coordinator(tmp_path / "b")  # a new installation: no readings
    rows, left_out = EX.read_readings_csv(EX.read_text(str(tmp_path / "x" / readings_file)))
    assert left_out == 0
    counts = fresh.import_readings(rows, NOW)
    assert counts == {"imported": len(before), "already_stored": 0, "not_kept": 0}
    assert _key(fresh.storage.readings) == before
    assert fresh.current_glucose == c.storage.readings[-1].raw  # the current values follow the import
    assert EX.read_lab_csv(EX.read_text(str(tmp_path / "x" / lab_file))) == (
        [("2026-01-10", 7.1, "percent"), ("2026-04-02", 49.0, "mmol_mol")],
        0,
    )
    # imported again: everything is already stored, nothing changes
    assert fresh.import_readings(rows, NOW) == {"imported": 0, "already_stored": len(before), "not_kept": 0}
    assert _key(fresh.storage.readings) == before


def test_a_spreadsheet_copy_with_the_decimal_comma_and_semicolons():
    text = (
        "local_time;utc_time;glucose_mg_dl;glucose_display;display_unit;source;limit\n"
        "2026-10-22T09:00:00-07:00;2026-10-22T16:00:00Z;112,4;6,2;mmol/L;primary;\n"
        "2026-10-22T09:05:00-07:00;;;6,9;mmol/L;secondary;\n"  # no stored value, no UTC time: the display unit
        "2026-10-22T09:10:00-07:00;2026-10-22T16:10:00Z;40,0;2,2;mmol/L;;low\n"
    )
    rows, left_out = EX.read_readings_csv(text)
    t0 = int(datetime(2026, 10, 22, 16, 0, tzinfo=UTC).timestamp())
    assert left_out == 0
    assert _key(rows) == [
        (t0, 112.4, None, C.SOURCE_PRIMARY),
        (t0 + 300, 124.3, None, C.SOURCE_SECONDARY),  # 6.9 mmol/L x 18.016, one decimal
        (t0 + 600, 40.0, "low", C.SOURCE_UNKNOWN),
    ]


def test_rows_that_are_no_reading_are_left_out_and_other_files_refused():
    head = ",".join(EX.READING_COLUMNS) + "\n"
    rows, left_out = EX.read_readings_csv(
        head
        + "2026-10-22T09:00:00-07:00,2026-10-22T16:00:00Z,120.0,120,mg/dL,primary,\n"
        + "x,not a time,120.0,120,mg/dL,primary,\n"  # no time
        + "2026-10-22T09:05:00-07:00,2026-10-22T16:05:00Z,900.0,900,mg/dL,primary,\n"  # beyond 500 mg/dL
        + "2026-10-22T09:10:00-07:00,2026-10-22T16:10:00Z,120.0,120,mg/dL,primary,maybe\n"  # unknown flag
        + "2014-10-22T09:10:00-07:00,2014-10-22T16:10:00Z,120.0,120,mg/dL,primary,\n"  # before 2015
        + ",,,,,,\n"  # an empty row: not counted
    )
    assert len(rows) == 1 and left_out == 4
    with pytest.raises(EX.NotAnExport):
        EX.read_readings_csv("date,value\n2026-10-22,7\n")
    with pytest.raises(EX.NotAnExport):
        EX.read_readings_csv("")


def test_stored_readings_win_and_only_the_retention_is_taken(tmp_path):
    c = real_coordinator(tmp_path)
    add_reading(c.storage, NOW - 600, 150.0, None, C.SOURCE_PRIMARY)
    rows = [
        GlucoseReading(NOW - 600 + 20, 99.0, None, C.SOURCE_SECONDARY),  # within 30 s of a stored reading
        GlucoseReading(NOW - 300, 101.0, None, C.SOURCE_PRIMARY),
        GlucoseReading(NOW - 300, 102.0, None, C.SOURCE_PRIMARY),  # the same time again: the first is taken
        GlucoseReading(NOW - 210 * 86400, 120.0, None, C.SOURCE_PRIMARY),  # older than the retention
        GlucoseReading(NOW + 3600, 120.0, None, C.SOURCE_PRIMARY),  # later than the clock
    ]
    counts = c.import_readings(rows, NOW)
    assert counts == {"imported": 1, "already_stored": 2, "not_kept": 2}
    assert _key(c.storage.readings) == [(NOW - 600, 150.0, None, C.SOURCE_PRIMARY), (NOW - 300, 101.0, None, 1)]
    assert not c.storage.dirty  # saved


def test_the_laboratory_results_of_a_stored_date_are_kept(tmp_path):
    from glucocraft import report_service as RS

    reports = RS.GlucoCraftReports.__new__(RS.GlucoCraftReports)
    reports._store, reports._cache = types.SimpleNamespace(async_save=lambda d: asyncio.sleep(0)), {}
    reports._lab = [{"date": "2026-01-10", "a1c": 6.0}]
    counts = asyncio.run(
        reports.async_import_lab(
            [
                ("2026-01-10", 7.1, "percent"),  # a stored date: kept as it is
                ("2026-04-02", 49, "mmol_mol"),
                ("x", 7, "percent"),  # not a date
                ("2026-02-30", 7, "percent"),  # a date that does not exist
                ("2026-05-01", 25, "percent"),  # outside 3-20%
            ],
            not_read=2,  # rows export.read_lab_csv could not read
        )
    )
    assert counts == {"lab_imported": 1, "lab_already_stored": 1, "lab_not_read": 5}
    assert reports._lab == [
        {"date": "2026-01-10", "a1c": 6.0},
        {"date": "2026-04-02", "a1c": 6.63, "a1c_mmol_mol": 49},
    ]


def test_a_date_that_does_not_exist_is_refused():
    """lab_entry checks the date's shape and that it exists: the import path has no field check before it."""
    from glucocraft import report_service as RS

    for date in ("2026-02-30", "2026-13-01", "2026-1-5", "2026-00-10"):
        with pytest.raises(ValueError, match="year-month-day"):
            RS.lab_entry(date, 7.0)
    assert RS.lab_entry("2028-02-29", 7.0) == {"date": "2028-02-29", "a1c": 7.0}  # a leap day


def test_a_date_later_than_today_is_refused():
    """A blood test lies in the past: lab_entry refuses a date after today (the add action and the import pass today in
    Home Assistant's time zone); today itself is accepted."""
    from glucocraft import report_service as RS

    with pytest.raises(ValueError, match="later than today"):
        RS.lab_entry("2026-12-24", 6.4, today="2026-11-15")
    assert RS.lab_entry("2026-11-15", 6.4, today="2026-11-15") == {"date": "2026-11-15", "a1c": 6.4}


def test_unreadable_laboratory_rows_are_counted_and_the_notice_states_them():
    text = (
        "date,a1c_percent,a1c_mmol_mol,entered_unit\n2026-01-10,7.1,54,percent\n2026-01-11,abc,,percent\n\n2026-01-12\n"
    )
    assert EX.read_lab_csv(text) == ([("2026-01-10", 7.1, "percent")], 2)  # the empty row is skipped, not counted
    base = {"file": "f.csv", "imported": 3, "already_stored": 0, "not_kept": 0, "not_read": 0}
    message = EX.import_message({**base, "lab_imported": 0, "lab_already_stored": 2, "lab_not_read": 1})
    assert "laboratory A1C results imported" not in message
    assert "2 laboratory A1C results of dates already stored were not imported" in message
    assert "1 laboratory A1C row could not be read or holds an invalid date or value, and was left out." in message
    quiet = EX.import_message({**base, "lab_imported": 0, "lab_already_stored": 0, "lab_not_read": 0})
    assert "laboratory" not in quiet


def test_the_laboratory_export_holds_both_units_and_the_entered_unit():
    """Every result in percent and in mmol/mol (a result entered in percent converted with the master equation, whole
    mmol/mol, as the CGM Report shows it), and the unit it was entered in, which the import reads."""
    text = EX.lab_csv([{"date": "2026-04-02", "a1c": 6.68, "a1c_mmol_mol": 49}, {"date": "2026-01-10", "a1c": 7.1}])
    assert text.splitlines() == [
        "date,a1c_percent,a1c_mmol_mol,entered_unit",
        "2026-01-10,7.1,54,percent",  # 10.929 x (7.1 - 2.15) = 54.1
        "2026-04-02,6.68,49,mmol_mol",
    ]
    # the import takes each result in its entered unit, although both cells are filled
    assert EX.read_lab_csv(text) == ([("2026-01-10", 7.1, "percent"), ("2026-04-02", 49.0, "mmol_mol")], 0)
    # an export without the column (written before it existed): a filled mmol/mol cell means mmol/mol
    old = "date,a1c_percent,a1c_mmol_mol\n2026-01-10,7.1,\n2026-04-02,6.68,49\n"
    assert EX.read_lab_csv(old) == ([("2026-01-10", 7.1, "percent"), ("2026-04-02", 49.0, "mmol_mol")], 0)


def test_the_readings_file_is_read_one_row_at_a_time(tmp_path):
    """The import reads the readings export from the open file row by row and keeps only the readings:
    no list of the file's rows or of its text."""
    c = real_coordinator(tmp_path / "a")
    _stored(c)
    result = EX.write_export(str(tmp_path / "x"), list(c.storage.readings), "mg/dL", LA)
    path = str(tmp_path / "x" / result["files"][0])
    from_text = EX.read_readings_csv(EX.read_text(path))
    assert EX.read_readings_file(path) == from_text and _key(from_text[0]) == _key(c.storage.readings)
    assert not isinstance(EX._rows("a,b\n1,2\n"), list)  # an iterator of rows

    consumed = []

    def lines():
        """The file's lines, noting how far the reader has got."""
        for line in EX.read_text(path).splitlines(keepends=True):
            consumed.append(line)
            yield line

    rows = EX._rows(lines())
    next(rows)
    assert len(consumed) == 1  # the header only: nothing is read ahead
    readings, left_out = EX.read_readings_csv(lines())
    assert left_out == 0 and _key(readings) == _key(c.storage.readings)
    # the action reads the file through read_readings_file
    init_text = (Path(EX.__file__).parent / "__init__.py").read_text(encoding="utf-8")
    assert "read_readings_file(" in init_text and "read_readings_csv(read_text" not in init_text


# ------------------------------------------------------------------------------------------------ the action
def _action(init, tmp_path, monkeypatch, data):
    c = real_coordinator(tmp_path / "c")
    c.reports = types.SimpleNamespace(
        async_import_lab=lambda lab, not_read=0: asyncio.sleep(
            0, {"lab_imported": len(lab), "lab_already_stored": 0, "lab_not_read": not_read}
        )
    )
    c.async_set_updated_data = lambda data: None
    monkeypatch.setattr(init, "_coordinator_for", lambda hass: c)
    calls = []

    async def run(fn, *args):
        return fn(*args)

    async def call(domain, service, data=None, **kw):
        calls.append(data)

    hass = types.SimpleNamespace(
        config=types.SimpleNamespace(path=lambda *p: str(Path(tmp_path, *p))),
        async_add_executor_job=run,
        services=types.SimpleNamespace(async_call=call),
    )
    return asyncio.run(init._handle_import(hass, types.SimpleNamespace(data=data))), calls, c


def test_the_action_reads_the_newest_export_and_states_the_count(tmp_path, monkeypatch):
    init = _load_init()
    source = real_coordinator(tmp_path / "s")
    _stored(source, 12)
    folder = str(tmp_path / C.EXPORT_DIR)
    EX.write_export(folder, list(source.storage.readings)[:3], "mg/dL", LA, now=datetime(2026, 1, 1, tzinfo=UTC))
    newest = EX.write_export(folder, list(source.storage.readings), "mg/dL", LA, [{"date": "2026-01-10", "a1c": 7.1}])
    result, calls, c = _action(init, tmp_path, monkeypatch, {})
    assert result == {
        "file": newest["files"][0],
        "imported": 12,
        "already_stored": 0,
        "not_kept": 0,
        "not_read": 0,
        "lab_imported": 1,
        "lab_already_stored": 0,
        "lab_not_read": 0,
    }
    assert len(c.storage.readings) == 12
    note = calls[0]
    assert note["notification_id"] == "glucocraft_import" and note["title"] == "GlucoCraft import"
    assert note["message"].startswith(f"12 readings imported from the file {newest['files'][0]} in the folder ")
    assert "1 laboratory A1C result imported" in note["message"]
    assert "not an alarm" not in note["message"]  # an operational notice: nothing added after its text


@pytest.mark.parametrize("name", ["../secrets.csv", "sub/x.csv", "missing.csv", "notes.txt"])
def test_the_action_names_a_file_in_the_export_folder_only(tmp_path, monkeypatch, name):
    init = _load_init()
    raised = []

    class SVE(Exception):
        def __init__(self, **kw):
            raised.append(kw)

    monkeypatch.setattr(init, "ServiceValidationError", SVE)
    with pytest.raises(SVE):
        _action(init, tmp_path, monkeypatch, {"file": name})
    assert raised[0]["translation_key"] == "import_no_file"


def test_the_action_refuses_a_file_that_is_not_an_export(tmp_path, monkeypatch):
    init = _load_init()
    raised = []

    class SVE(Exception):
        def __init__(self, **kw):
            raised.append(kw)

    monkeypatch.setattr(init, "ServiceValidationError", SVE)
    (tmp_path / C.EXPORT_DIR).mkdir()
    (tmp_path / C.EXPORT_DIR / "other.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(SVE):
        _action(init, tmp_path, monkeypatch, {"file": "other.csv"})
    assert raised[0]["translation_key"] == "import_not_an_export"


def test_the_action_is_registered_for_administrators_with_texts():
    import json

    import yaml

    init = _load_init()
    assert "import_csv" in init.SERVICES and init._HANDLERS["import_csv"][0] is init._handle_import
    pkg = Path(__file__).resolve().parents[1] / "custom_components" / "glucocraft"
    strings = json.loads((pkg / "strings.json").read_text(encoding="utf-8"))
    services = yaml.safe_load((pkg / "services.yaml").read_text(encoding="utf-8"))
    assert services["import_csv"]["description"] == strings["services"]["import_csv"]["description"]
    assert {"import_no_file", "import_not_an_export", "import_failed"} <= set(strings["exceptions"])
    icons = json.loads((pkg / "icons.json").read_text(encoding="utf-8"))
    assert icons["services"]["import_csv"]["service"].startswith("mdi:")
