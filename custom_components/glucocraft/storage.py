# License: AGPL-3.0-only (LICENSE).
"""Persistent binary storage for glucose readings.

Storage version 3 format:
- 16-byte header: GC02 magic (4) + version (1) + flags (1) + detected_interval (2) + reserved (8)
- 10 bytes per reading: timestamp (4, uint32) + raw (2, uint16) + reserved (2, uint16) + limit (1, uint8: 0 none,
  1 low = LO, 2 high = HI) + source (1, uint8: 0 unknown, 1 primary, 2 secondary)

A LO / HI reading is stored at the source's limit (limits.py). The record's time is an unsigned 32-bit count of
seconds, which ends on 2106-02-07; a reader that takes the field as a signed 32-bit number ends on 2038-01-19.

Raw values are stored as mg/dL * 10 (e.g., 120.5 mg/dL = 1205). The reserved field of a record is written as 0 and its
value is never used: a file with other values there (written by an earlier version) loads unchanged and is written in
full, with the field 0, at its next save. A record is a plausible reading when its time is from 2015 on and not more
than a year after the clock, its value 20 to 500 mg/dL and its limit and source bytes valid codes (_plausible); any
other record is left out. The end of that span is judged from the records as well: readings later than a year after the
clock that end the file in time order are a clock that is behind, not damage, so they are kept (_latest_plausible) and
the coordinator's clock rule (clock_behind) applies. A file of a newer format version is neither read nor written
(problem "newer": a newer GlucoCraft wrote it). A file with an older version or a damaged header is recovered when its
body parses as plausible readings; a file with a valid header of which records other than a last one cut short were left
out (a zeroed or overwritten part) is recovered as well. A recovered file is kept as an exact copy (.corrupt-<epoch>)
and saved again from its readings (problem "recovered"); a file that cannot be recovered is kept aside as that copy
and GlucoCraft starts with an empty store (problem "damaged"). All are reported in Repairs.

Saving. The file always holds exactly what a full write of the stored readings gives (the tests compare both byte by
byte). The common change, new readings after the newest saved one, is appended to the end of the file (and a changed
detected interval written in place, 2 bytes at offset 6), then fsynced: the readings already in the file are never
written again, so a loss of power during an append can only cut the new records short, and the loader leaves out a
record cut short. Every other change (a purge, a reading inserted before or replacing a saved one, the readings later
than the clock discarded, older history merged, a recovered file, the first save) writes the whole file
to a temporary file, fsyncs it and replaces the old file, so that a loss of power keeps one whole file. Before an
append the file's size, header and last saved record are checked against what was saved; any difference (the file
changed or was removed outside GlucoCraft, an earlier append failed) leads to a full write instead. Appending relies
on what every journaling file system and SQLite rely on: a write past the end of a file does not change the bytes
before it, and the new end of the file is recorded only after the data are written. A save that fails (a full,
read-only or failing storage) keeps the readings in memory and sets save_error until a save succeeds (the coordinator
reports it in Repairs and on the Data history sensor); the save after a failure writes the whole file.
"""

from __future__ import annotations

import bisect
import logging
import operator
import os
import struct
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from pathlib import Path

from .const import (
    DEFAULT_RETENTION_DAYS,
    INTERVAL_WINDOW,
    SOURCE_PRIMARY,
    SOURCE_SECONDARY,
    SOURCE_UNKNOWN,
    STORAGE_HEADER_SIZE,
    STORAGE_MAGIC,
    STORAGE_READING_SIZE,
    STORAGE_VERSION,
    STORED_MGDL,
)
from .gaps import local_intervals, median_interval
from .limits import LIMIT_CODES, LIMIT_FROM_CODE
from .units import round_half_up

_LOGGER = logging.getLogger(__name__)

_RECORD = struct.Struct("<IHHBB")  # timestamp, raw x 10, reserved (0), limit code, source (STORAGE_READING_SIZE)
_PACK_RECORD = _RECORD.pack
_INTERVAL_AT = slice(6, 8)  # the detected interval in the header (uint16, little-endian)

# A plausible record (_plausible): a time from 2015-01-01 on and not more than a year after the clock (or, when the
# clock is behind, up to the newest of the readings that end the file in time order: _latest_plausible), a value of 20
# to 500 mg/dL, a valid limit code and a valid source code. Header recovery: a body counts as readings when at least
# _RECOVERY_MIN_SHARE of its records are plausible. GlucoCraft rule.
_RECOVERY_MIN_SHARE = 0.95
EARLIEST_PLAUSIBLE_TS = 1_420_070_400  # 2015-01-01 00:00 UTC (also the CSV import's earliest time, export.py)
_LATEST_AFTER_CLOCK = 366 * 86400
# Readings later than a year after the clock count as a clock that is behind when at least this many of them (or all
# the file's readings) end the file in time order; fewer, or out of order, are damage
_CLOCK_RUN_MIN = 3
_SOURCE_CODES = frozenset((SOURCE_UNKNOWN, SOURCE_PRIMARY, SOURCE_SECONDARY))
_LOWEST_X10, _HIGHEST_X10 = (int(mg * 10) for mg in STORED_MGDL)  # const.STORED_MGDL as stored (value x 10)

# Interval detection (detect_interval): the newest readings looked at, the fewest readings it needs, and the
# intervals that count, in seconds. The coordinator uses the first two as well (re-detection, its own interval rule).
DETECT_READINGS = 50
DETECT_MIN_READINGS = 10
_DETECT_SHORTEST = 31  # 30 s or less: a repeated reading, not the CGM's interval
_DETECT_SHORT_LONGEST = 899  # a 1- to 5-minute CGM (and any CGM faster than every 15 minutes)
_DETECT_SLOW_LONGEST = 3600  # a CGM that reads every 15 minutes up to every hour; longer intervals are outages
# The reading interval used until one is detected or read from the file's header: 5 minutes, the interval GlucoCraft
# is designed for at the least (readings every 5 minutes or more often)
DEFAULT_INTERVAL_SECONDS = 300

# reading_intervals cache: from this many readings on, the interval at each reading depends only on the readings
# before it and up to the 12th (gaps.local_intervals fills a window from the readings after it only near the start),
# so the cached intervals of an unchanged first part stay valid
_IV_STABLE = INTERVAL_WINDOW + 2


def retention_start(ref: int, tz: tzinfo, days: int = DEFAULT_RETENTION_DAYS) -> int:
    """Return the first moment the retention keeps: the local midnight `days` days before the local day of `ref`.

    Whole local days, so a daylight-saving day keeps its real length (23 or 25 hours). The purge removes the readings
    before this moment, and the history import reads the recorder from it, so that the oldest stored day is complete.
    """
    midnight = datetime.fromtimestamp(ref, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((midnight - timedelta(days=int(days))).timestamp())


def _latest_plausible(body: bytes) -> int | None:
    """Return the latest plausible record time of a file body (version 3 records): a year after the clock.

    None (no upper bound) while the clock itself is before 2015 (a host started without its time). The bound is raised
    to the newest reading when the readings later than it end the file in time order (_clock_behind_end): the clock
    is behind, not the readings, and no reading is ever dropped because of the clock alone. In both cases the stored
    readings stay plausible records and the coordinator's clock rule (clock_behind) applies.
    """
    now = int(time.time())
    if now < EARLIEST_PLAUSIBLE_TS:
        return None
    latest = now + _LATEST_AFTER_CLOCK
    return _clock_behind_end(body, latest) or latest


def _clock_behind_end(body: bytes, latest: int) -> int | None:
    """Return the newest time of the readings later than `latest` when they are a clock that is behind, else None.

    They are when every record that is plausible apart from its time (_plausible without an upper bound) and later
    than `latest` comes after all the others in the file, in increasing time order, and there are at least
    _CLOCK_RUN_MIN of them or they are all the file's readings. A record with a time far in the future among the
    readings (overwritten bytes) breaks that order and stays damage.
    """
    size = STORAGE_READING_SIZE
    whole = memoryview(body)[: len(body) - len(body) % size]
    run = 0
    readings = 0
    newest = 0
    for ts, raw_x10, _reserved, limit_code, source in _RECORD.iter_unpack(whole):
        if not _plausible(ts, raw_x10, limit_code, source, None):
            continue
        readings += 1
        if ts > latest:
            if ts <= newest:
                return None  # out of time order
            run += 1
            newest = ts
        elif run:
            return None  # a reading within the span after a later one
    return newest if run and (run >= _CLOCK_RUN_MIN or run == readings) else None


def _plausible(ts: int, raw_x10: int, limit_code: int, source: int, latest: int | None) -> bool:
    """Return True for a record that is a plausible reading (the rule above _RECOVERY_MIN_SHARE)."""
    return (
        EARLIEST_PLAUSIBLE_TS <= ts
        and (latest is None or ts <= latest)
        and _LOWEST_X10 <= raw_x10 <= _HIGHEST_X10
        and limit_code in LIMIT_FROM_CODE
        and source in _SOURCE_CODES
    )


def _parse_records(body: bytes, latest: int | None) -> tuple[list[GlucoseReading], list[int]]:
    """Return the readings of a file body (version 3 records) and the indices of the whole records left out.

    Every record that is not a plausible reading (_plausible) is left out: an empty or zeroed record, an error marker
    (0xFFFF = 6553.5), a value outside 20-500 mg/dL, a time outside the plausible span (`latest`: _latest_plausible),
    an unknown limit or source code. A last record cut short (a loss of power during an append) is left out as well; it
    is not a whole record, so it is not among the indices.
    """
    size = STORAGE_READING_SIZE
    whole = memoryview(body)[: len(body) - len(body) % size]
    out: list[GlucoseReading] = []
    left_out: list[int] = []
    for i, (ts, raw_x10, _reserved, limit_code, source) in enumerate(_RECORD.iter_unpack(whole)):
        if _plausible(ts, raw_x10, limit_code, source, latest):
            out.append(GlucoseReading(ts, raw_x10 / 10.0, LIMIT_FROM_CODE[limit_code], source))
        else:
            left_out.append(i)
    return out, left_out


def _damaged_records(body: bytes, left_out: list[int]) -> int:
    """Return how many of the whole records left out (_parse_records) show damage.

    Not counted: the zeroed records at the end of the file and the last record, which a loss of power during an append
    can leave unwritten (a file system that extended the file before its data reached the disk); their readings were
    never saved. Every other record left out (zeroed or overwritten bytes among the readings, a tail of other bytes)
    is damage.
    """
    size = STORAGE_READING_SIZE
    end = len(body) // size
    zero = bytes(size)
    left = set(left_out)
    while end and end - 1 in left and body[(end - 1) * size : end * size] == zero:
        end -= 1
    last = len(body) // size - 1
    return sum(1 for i in left_out if i < end and i != last)


def _x10(value: float) -> int:
    """A value in mg/dL as the stored uint16 (x 10), halves away from zero (Python's round() rounds halves to even),
    as every display rounds."""
    return max(0, min(65535, int(round_half_up(value * 10))))


def _pack(readings: Sequence[GlucoseReading]) -> bytes:
    """The version 3 records of `readings`, in order (the reserved field 0)."""
    return b"".join(
        [
            _PACK_RECORD(
                r.timestamp,
                _x10(r.raw),
                0,
                LIMIT_CODES.get(r.limit, 0),
                max(0, min(255, int(r.source or 0))),
            )
            for r in readings
        ]
    )


def _recover_body(body: bytes) -> list[GlucoseReading] | None:
    """The readings of a body whose header is damaged, when its records are plausible readings (at least
    _RECOVERY_MIN_SHARE of them); None otherwise."""
    size = STORAGE_READING_SIZE
    count = len(body) // size
    if count == 0 or len(body) % size:
        return None
    readings, _ = _parse_records(body, _latest_plausible(body))
    return readings if len(readings) / count >= _RECOVERY_MIN_SHARE else None


def _fsync_folder(folder: Path) -> None:
    """Make a replaced file's new name durable (POSIX: fsync the folder). Windows cannot open a folder for this; there
    it is skipped."""
    if os.name != "posix":
        return
    try:
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as err:
        _LOGGER.debug("The storage folder could not be synced: %s", err)


def _window_indices(readings: Sequence[GlucoseReading], start: int, end: int | None) -> tuple[int, int]:
    """(i, j): readings[i:j] are those with start <= ts < end (end None: to the newest), by bisection."""
    i = bisect.bisect_left(readings, start, key=lambda r: r.timestamp)
    j = len(readings) if end is None else bisect.bisect_left(readings, end, key=lambda r: r.timestamp)
    return i, j


class ReadingsCopy:
    """A copy of the stored readings and their reading intervals (GlucoCraftStorage.copy), with the storage's reading
    methods that the statistics use, so that a statistics pass computes from it without the coordinator's lock while
    new readings are stored."""

    __slots__ = ("intervals", "readings")

    def __init__(self, readings: list[GlucoseReading], intervals: list[int]) -> None:
        """Keep the copied readings and their reading intervals (aligned with them)."""
        self.readings = readings
        self.intervals = intervals

    def reading_intervals(self, _fallback: int | None = None) -> list[int]:
        """The reading interval at every copied reading, as copied (GlucoCraftStorage.reading_intervals)."""
        return self.intervals

    def window_indices(self, start: int, end: int | None = None) -> tuple[int, int]:
        """(i, j): readings[i:j] are those with start <= ts < end (end None: to the newest), by bisection."""
        return _window_indices(self.readings, start, end)


@dataclass(slots=True)
class GlucoseReading:
    """A single glucose reading."""

    timestamp: int  # Unix epoch seconds
    raw: float  # mg/dL
    limit: str | None = None  # "low" (LO) / "high" (HI): stored at the sensor's limit; None = measured
    source: int = SOURCE_UNKNOWN  # const.SOURCE_*: 1 primary, 2 secondary, 0 not known


class GlucoCraftStorage:
    """Binary storage for glucose readings.

    Change the stored readings only with the methods of this class (insert_reading, replace_at, merge_history,
    fill_after, add_readings, drop_after, purge_before): they record from which reading on the store changed, which the
    save (append or full write) and the reading-interval cache rely on. A list assigned to the store as a whole (the
    tests do this) is always written in full.
    """

    def __init__(self, storage_dir: str, entry_id: str, read_only: bool = False) -> None:
        """Initialize storage. No file is touched here (the coordinator is built in the event loop); the folder is
        created by the first save, which runs in the executor.

        read_only: the file is only read, and nothing in the folder changes (kept.kept_summary): a damaged file is
        neither moved aside nor copied (problem is set all the same), a temporary file is not removed, and nothing is
        saved."""
        self._read_only = read_only
        self._dir = Path(storage_dir)
        self._path = self._dir / f"glucocraft_{entry_id}.bin"
        # Why the stored file could not be used as it is, for the Repairs notice and the Data history sensor:
        # None (no problem), "recovered" (the header was damaged, the readings were recovered and a copy of the
        # damaged file kept), "damaged" (the file could not be parsed and was kept aside; GlucoCraft starts empty),
        # "unreadable" (the file could not be read; it is not overwritten until GlucoCraft starts again: a restart of
        # Home Assistant, or a reload of the entry, which saving its settings also does) or "newer" (the file is in a
        # format version newer than this version reads; it is neither read nor overwritten). A new storage is made at
        # every start of the entry, so a block lasts as long as this one.
        self.problem: str | None = None
        self.kept_file: str | None = None  # the name of the copy kept aside (recovered or damaged)
        # Why the last save failed (the operating system's text, "No space left on device"), None after a save that
        # succeeded: the coordinator's Repairs notice storage_save_failed and the Data history sensor show it. Logged
        # once when saving starts to fail and once when it works again, not at every save.
        self.save_error: str | None = None
        self._detected_interval: int = DEFAULT_INTERVAL_SECONDS
        self._flags: int = 0
        self._readings: list[GlucoseReading] = []
        self._loaded = False
        self.dirty = False  # unsaved changes (the coordinator's timer saves them)
        # Counts every change of the stored readings other than new readings after the newest one (a reading inserted
        # before it, replaced or removed): with the count and the newest time of the readings up to a moment, it tells
        # whether those readings changed (the CGM Report's cache, report_service._readings_key)
        self.edits = 0
        self._save_blocked = read_only  # the file could not be read (or is only read): never overwrite it
        # What the file holds: the records of _disk_list[:_on_disk] (-1: not known, the next save writes the whole
        # file) after the header _disk_header. _changed() lowers it when a saved reading changes.
        self._on_disk = -1
        self._disk_list: list[GlucoseReading] | None = None
        self._disk_header = b""
        # reading_intervals cache: _iv[:_iv_keep] are valid for the readings of _iv_list
        self._iv: list[int] = []
        self._iv_keep = 0
        self._iv_list: list[GlucoseReading] | None = None

    @property
    def detected_interval(self) -> int:
        """Return detected CGM interval in seconds."""
        return self._detected_interval

    @detected_interval.setter
    def detected_interval(self, value: int) -> None:
        """Set the detected interval (written to the file header at the next save)."""
        self._detected_interval = value

    @property
    def readings(self) -> list[GlucoseReading]:
        """Return all readings."""
        if not self._loaded:
            self.load()
        return self._readings

    @property
    def reading_count(self) -> int:
        """Return number of stored readings."""
        return len(self._readings)

    @property
    def path(self) -> Path:
        """The readings file (removed with the integration)."""
        return self._path

    @property
    def newest_timestamp(self) -> int:
        """The measurement time of the newest stored reading (0 when there is none)."""
        return self._readings[-1].timestamp if self._readings else 0

    def _changed(self, first: int, before: int | None = None) -> None:
        """Record that the stored readings changed from index `first` on (inserted, replaced or removed there).

        A change before the end of the saved readings makes the next save a full write; the reading intervals are
        recomputed from `first` on. before: the number of readings before the change, when it is known; a change at or
        after that index adds new readings after the newest one, any other change counts in `edits`.
        """
        if before is None or first < before:
            self.edits += 1
        if first < self._on_disk:
            self._on_disk = -1
        self._iv_keep = min(self._iv_keep, first)
        self.dirty = True

    def load(self) -> None:
        """Load the readings from disk (storage version 3)."""
        self._readings = []
        self._loaded = True
        self._on_disk = -1

        tmp_path = self._path.with_suffix(".tmp")
        if not self._path.exists():
            _LOGGER.debug("No storage file found at %s", self._path)
            self._remove_temporary(tmp_path)
            return

        try:
            data = self._path.read_bytes()
        except OSError as err:
            # a read error must never lead to the file being overwritten (the data in it would be lost)
            self._save_blocked = True
            self.problem = "unreadable"
            _LOGGER.error(
                "GlucoCraft storage could not be read (%s); it will not be overwritten until GlucoCraft starts again "
                "(a restart of Home Assistant, or a reload of GlucoCraft)",
                err,
            )
            return
        # A temporary file left by a full write that a loss of power or a crash interrupted before it replaced the
        # file: the readings file is the last complete save, so the temporary file is removed.
        self._remove_temporary(tmp_path)

        clean = False  # a version 3 file with a valid header: the next save can append when nothing was left out
        body = b""
        if len(data) >= STORAGE_HEADER_SIZE and data[:4] == STORAGE_MAGIC and data[4] > STORAGE_VERSION:
            # A file written by a newer version of GlucoCraft (this version was installed over it): its format is not
            # known here, so it is neither read nor written, and GlucoCraft starts with an empty history in memory.
            # Never recovered as a damaged file, which would rewrite it in this version's format.
            self._save_blocked = True
            self.problem = "newer"
            _LOGGER.error(
                "GlucoCraft storage is in format version %d, newer than this version reads (%d); it is not read and "
                "not overwritten",
                data[4],
                STORAGE_VERSION,
            )
            return
        if len(data) < STORAGE_HEADER_SIZE or data[:4] != STORAGE_MAGIC or data[4] != STORAGE_VERSION:
            # A damaged header (or a file in another format). The records after it are often intact (a write error
            # on an SD card typically hits the first bytes): when the body parses as plausible readings they are
            # recovered and a new header is written at the next save. The damaged file is always kept as a copy (never
            # silently replaced).
            readings = _recover_body(data[STORAGE_HEADER_SIZE:]) if len(data) > STORAGE_HEADER_SIZE else None
            if readings is None:
                self._quarantine(f"unreadable header ({len(data)} bytes)")
                return
            self._keep_copy(data, f"damaged header, {len(readings)} readings recovered")
            self.problem = "recovered"
            self._readings = readings
            self.dirty = True  # the next save writes the recovered readings with a new header
        else:
            self._flags = data[5]
            self._detected_interval = struct.unpack("<H", data[_INTERVAL_AT])[0] or DEFAULT_INTERVAL_SECONDS
            body = data[STORAGE_HEADER_SIZE:]
            self._readings, left_out = _parse_records(body, _latest_plausible(body))
            # The file is exactly what a full write of its readings gives (so the next save can append) when it has
            # whole records, none left out and the times increasing (checked below): the x10 values read back to the
            # same bytes (test_storage checks every uint16).
            size = STORAGE_READING_SIZE
            clean = len(body) % size == 0 and not left_out
            # A file written by an earlier version can hold values in the reserved field (bytes 6-7 of a record). They
            # are not used; such a file is written in full at the next save, which writes the field as 0.
            clean = clean and not any(body[6::size]) and not any(body[7::size])
            self._disk_header = data[:STORAGE_HEADER_SIZE]
            # Records left out other than those a loss of power during an append can leave (_damaged_records): a part
            # of the file was zeroed or overwritten. The readings that could be read are recovered, as for a damaged
            # header, and the file is kept as a copy, never silently replaced.
            damaged = _damaged_records(body, left_out)
            if damaged:
                if not self._readings:  # not one readable record: kept aside as a damaged file
                    self._quarantine(f"none of {len(left_out)} records readable")
                    return
                self._keep_copy(data, f"{damaged} records not readable, {len(self._readings)} readings recovered")
                self.problem = "recovered"
                self.dirty = True  # the next save writes the recovered readings in full

        # Sorted, one reading per time. Readings later than the system clock are KEPT (a year after it, or more when
        # they end the file in time order: _latest_plausible): when the clock is behind, they are correct and the clock
        # is not, and a deletion based only on the clock would be permanent. The coordinator stores no new readings and
        # purges nothing while the clock is earlier than the newest stored reading (GlucoCraftCoordinator.clock_behind),
        # and a Repairs notice lets the user discard them when the clock is right.
        times = [r.timestamp for r in self._readings]
        if not all(map(operator.lt, times, times[1:])):  # not already sorted with one reading per time
            clean = False
            self._readings.sort(key=lambda r: r.timestamp)
            self._readings = [
                r for i, r in enumerate(self._readings) if i == 0 or r.timestamp != self._readings[i - 1].timestamp
            ]
        # Any other file (a record left out or cut short, unsorted or repeated times, a recovered file) is written in
        # full at the next save, so that appends always follow complete, valid records
        if clean:
            self._on_disk = len(self._readings)
            self._disk_list = self._readings

        _LOGGER.debug(
            "Loaded %d readings from storage (interval=%ds)",
            len(self._readings),
            self._detected_interval,
        )

    def _remove_temporary(self, tmp_path: Path) -> None:
        """Remove a temporary file left behind (it is never read); not by a read-only storage."""
        if self._read_only:
            return
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError as err:
            _LOGGER.debug("A temporary storage file could not be removed: %s", err)

    def _kept_path(self) -> Path:
        """Return the path for a copy of a damaged file: <file>.corrupt-<epoch>."""
        return self._path.with_name(f"{self._path.name}.corrupt-{int(time.time())}")

    def _quarantine(self, why: str) -> None:
        """Keep a damaged file as .corrupt-<epoch> and start empty (a read-only storage leaves the file as it is)."""
        bad = self._kept_path()
        self.problem = "damaged"
        if self._read_only:
            _LOGGER.debug("GlucoCraft storage %s damaged (%s); only read, left as it is", self._path.name, why)
            return
        try:
            self._path.replace(bad)
            self.kept_file = bad.name
            _LOGGER.error("GlucoCraft storage damaged (%s): kept as %s, starting with an empty history", why, bad.name)
        except OSError as err:
            self._save_blocked = True
            _LOGGER.error(
                "GlucoCraft storage damaged (%s) and could not be moved aside (%s): not overwritten", why, err
            )

    def _keep_copy(self, data: bytes, why: str) -> None:
        """Keep an exact copy of a damaged file whose readings were recovered. Without the copy nothing is overwritten
        (saving stays blocked until GlucoCraft starts again: a restart of Home Assistant, or a reload of the entry). A
        read-only storage keeps no copy."""
        if self._read_only:
            _LOGGER.debug("GlucoCraft storage %s damaged (%s); only read, no copy kept", self._path.name, why)
            return
        copy = self._kept_path()
        try:
            copy.write_bytes(data)
            self.kept_file = copy.name
            _LOGGER.error("GlucoCraft storage damaged (%s): a copy is kept as %s", why, copy.name)
        except OSError as err:
            self._save_blocked = True
            _LOGGER.error("GlucoCraft storage damaged (%s) and no copy could be kept (%s): not overwritten", why, err)

    def stored_files(self) -> list[Path]:
        """Every file this storage wrote: the readings file, its temporary file and the copies of damaged files."""
        if not self._dir.is_dir():
            return []
        return sorted(
            p
            for p in self._dir.iterdir()
            if p.name == self._path.name
            or p.name.startswith(f"{self._path.name}.corrupt-")
            or p == self._path.with_suffix(".tmp")
        )

    def _header(self) -> bytes:
        """The file header for the current settings (version 3)."""
        header = bytearray(STORAGE_HEADER_SIZE)
        header[:4] = STORAGE_MAGIC
        header[4] = STORAGE_VERSION
        header[5] = self._flags
        struct.pack_into("<H", header, _INTERVAL_AT.start, self._detected_interval)
        return bytes(header)

    def save(self) -> None:
        """Write the readings to disk: new readings after the saved ones are appended, any other change writes the
        whole file (atomic: temporary file, fsync, replace). See the module docstring."""
        if self._save_blocked:
            return
        if not self._loaded and self._path.exists():
            # nothing was read from this file, so writing would replace readings that were never loaded
            _LOGGER.warning("GlucoCraft storage was not loaded; the file is not overwritten")
            return
        header = self._header()
        on_disk = self._on_disk if self._readings is self._disk_list and self._on_disk <= len(self._readings) else -1
        same_header = header[:6] == self._disk_header[:6] and header[8:] == self._disk_header[8:]
        if on_disk >= 0 and same_header and self._append(on_disk, header):
            return
        self._write_whole(header)

    def _append(self, on_disk: int, header: bytes) -> bool:
        """Append the readings after the first `on_disk` to the file and write a changed interval in place; return
        False when the file is not as it was saved (then the caller writes it whole)."""
        size = STORAGE_READING_SIZE
        end = STORAGE_HEADER_SIZE + on_disk * size
        new = _pack(self._readings[on_disk:])
        interval_changed = header[_INTERVAL_AT] != self._disk_header[_INTERVAL_AT]
        try:
            with open(self._path, "r+b") as fh:
                if os.fstat(fh.fileno()).st_size != end or fh.read(STORAGE_HEADER_SIZE) != self._disk_header:
                    return False
                if on_disk:
                    fh.seek(end - size)
                    if fh.read(size) != _pack(self._readings[on_disk - 1 : on_disk]):
                        return False
                if new or interval_changed:
                    fh.seek(end)
                    fh.write(new)
                    if interval_changed:
                        fh.seek(_INTERVAL_AT.start)
                        fh.write(header[_INTERVAL_AT])
                    fh.flush()
                    os.fsync(fh.fileno())
        except FileNotFoundError:
            return False
        except OSError as err:
            # the readings saved before are untouched; the end of the file is not known now, so the next save writes
            # the whole file (dirty stays set)
            self._save_failed(err)
            return True
        self._saved(header)
        return True

    def _write_whole(self, header: bytes) -> None:
        """Write the header and every reading to a temporary file, fsync it and replace the file with it."""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError as err:
            self._save_failed(err)
            return
        tmp_path = self._path.with_suffix(".tmp")
        try:
            with open(tmp_path, "wb") as fh:
                fh.write(header)
                fh.write(_pack(self._readings))
                fh.flush()
                os.fsync(fh.fileno())  # on disk before it replaces the old file (a power cut keeps one whole file)
            tmp_path.replace(self._path)
        except OSError as err:
            self._save_failed(err)
            self._remove_temporary(tmp_path)
            return
        _fsync_folder(self._dir)
        self._saved(header)

    def _save_failed(self, err: OSError) -> None:
        """Record a failed save: the next save writes the whole file, and save_error holds the reason (logged once
        per episode: a save every minute would otherwise log the same error every minute)."""
        self._on_disk = -1
        if self.save_error is None:
            _LOGGER.error("GlucoCraft storage could not be written (%s); the readings are kept in memory", err)
        self.save_error = err.strerror or err.__class__.__name__

    def _saved(self, header: bytes) -> None:
        """Record that the file now holds every reading after `header` (and that saving works again)."""
        self._on_disk = len(self._readings)
        self._disk_list = self._readings
        self._disk_header = header
        self.dirty = False
        if self.save_error is not None:
            self.save_error = None
            _LOGGER.info("GlucoCraft storage is written again")

    def merge_history(self, rows: list[tuple], margin: int = 30, source: int = SOURCE_PRIMARY) -> int:
        """Merge history from Home Assistant's recorder in time order; return the number of rows accepted.

        Only rows outside the stored span (more than `margin` s before the first or after the last stored reading)
        are taken: inside it the stored readings, with the sources' own measurement times, are kept. rows: (time,
        mg/dL) or (time, mg/dL, limit); the history is the primary source's (source).
        """
        if not self._loaded:
            self.load()
        rows = sorted(rows, key=lambda r: (r[0], r[1]))
        first = self._readings[0].timestamp if self._readings else None
        last = self._readings[-1].timestamp if self._readings else None
        older: list[GlucoseReading] = []
        newer: list[GlucoseReading] = []
        seen: set[int] = set()
        for row in rows:
            ts, value = row[0], row[1]
            limit = row[2] if len(row) > 2 else None
            if ts in seen:
                continue  # one reading per time
            reading = GlucoseReading(timestamp=ts, raw=value, limit=limit, source=source)
            if first is None or ts < first - margin:
                older.append(reading)
            elif ts > last + margin:
                newer.append(reading)
            else:
                continue
            seen.add(ts)
        if older or newer:
            self._replace_list(older + self._readings + newer, 0 if older else len(self._readings))
        return len(older) + len(newer)

    def fill_after(self, rows: list[tuple], after: int, margin: int = 30, source: int = SOURCE_PRIMARY) -> int:
        """Insert the recorder's rows later than `after` that no stored reading is within `margin` s of; return the
        number inserted.

        The catch-up after the clock was behind (coordinator.async_catch_up): the readings refused meanwhile are in
        the recorder, and live readings may already be stored after them, so the rows are placed among the stored
        readings by time, and a row within `margin` of a stored reading is the same measurement (the stored one is
        kept). rows: (time, mg/dL) or (time, mg/dL, limit), as for merge_history.
        """
        readings = [
            GlucoseReading(row[0], row[1], row[2] if len(row) > 2 else None, source)
            for row in sorted(rows, key=lambda r: (r[0], r[1]))
            if row[0] > after
        ]
        return self.add_readings(readings, margin)

    def add_readings(self, readings: list[GlucoseReading], margin: int = 30) -> int:
        """Insert the readings that no stored reading is within `margin` s of, each placed by its time; return the
        number inserted.

        A reading within `margin` of a stored one is the same measurement: the stored reading is kept as it is (never
        replaced, never doubled). Of several given readings with the same time, the first is taken. Used by the
        catch-up after the clock was behind (fill_after) and by the CSV import (coordinator.import_readings).
        """
        if not self._loaded:
            self.load()
        new: dict[int, GlucoseReading] = {}
        for reading in readings:
            ts = reading.timestamp
            if ts in new:
                continue
            _, dist = self.nearest(ts)
            if dist is not None and dist <= margin:
                continue
            new[ts] = reading
        if not new:
            return 0
        first = bisect.bisect_left(self._readings, min(new), key=lambda r: r.timestamp)
        tail = sorted([*self._readings[first:], *new.values()], key=lambda r: r.timestamp)
        self._replace_list(self._readings[:first] + tail, first)
        return len(new)

    def nearest(self, ts: int) -> tuple[int, int | None]:
        """Index and distance (s) of the stored reading closest in time to ts; (-1, None) when empty."""
        if not self._readings:
            return -1, None
        i = bisect.bisect_left(self._readings, ts, key=lambda r: r.timestamp)
        best = -1
        for j in (i - 1, i):
            if 0 <= j < len(self._readings) and (
                best < 0 or abs(self._readings[j].timestamp - ts) < abs(self._readings[best].timestamp - ts)
            ):
                best = j
        return best, abs(self._readings[best].timestamp - ts)

    def insert_reading(self, timestamp: int, raw: float, limit: str | None = None, source: int = SOURCE_UNKNOWN) -> int:
        """Insert in time order (a gap filled by the other source can be older than the newest). Returns its index."""
        if not self._loaded:
            self.load()
        i = bisect.bisect_left(self._readings, timestamp, key=lambda r: r.timestamp)
        reading = GlucoseReading(timestamp, raw, limit, source)
        before = len(self._readings)
        if i < before and self._readings[i].timestamp == timestamp:
            self._readings[i] = reading
        else:
            self._readings.insert(i, reading)
        self._changed(i, before)
        return i

    def replace_at(
        self,
        index: int,
        timestamp: int,
        raw: float,
        limit: str | None = None,
        source: int = SOURCE_UNKNOWN,
    ) -> int:
        """The same measurement from the preferred source replaces the stored one (never a second reading)."""
        del self._readings[index]
        self._changed(index)
        return self.insert_reading(timestamp, raw, limit, source)

    def window(self, start: int, end: int) -> list[GlucoseReading]:
        """Readings with start <= ts < end (sorted), found by bisection."""
        i, j = self.window_indices(start, end)
        return self._readings[i:j]

    def window_indices(self, start: int, end: int | None = None) -> tuple[int, int]:
        """(i, j): readings[i:j] are those with start <= ts < end (end None: to the newest), by bisection."""
        return _window_indices(self._readings, start, end)

    def copy(self, fallback: int | None = None) -> ReadingsCopy:
        """Return a copy of the readings and their reading intervals (reading_intervals(fallback)); take it under the
        coordinator's lock, then read it without the lock."""
        return ReadingsCopy(list(self.readings), list(self.reading_intervals(fallback)))

    def reading_intervals(self, fallback: int | None = None) -> list[int]:
        """The reading interval at every stored reading (gaps.local_intervals; aligned with readings).

        Cached: the editing methods record from which reading on the store changed (_changed), and only the intervals
        from there on are computed again (a reading's interval depends on the 10 before it, or, near the start, on the
        ones after it). The result is identical to computing every interval (the tests compare both).
        """
        if not self._loaded:
            self.load()
        fb = int(fallback or self._detected_interval or DEFAULT_INTERVAL_SECONDS)
        n = len(self._readings)
        keep = min(self._iv_keep, len(self._iv), n) if self._readings is self._iv_list else 0
        if keep < _IV_STABLE:
            self._iv = local_intervals([r.timestamp for r in self._readings], fb)
        elif keep < n:
            # the readings from the first one that changed, with the window of intervals before it
            tail = [r.timestamp for r in self._readings[keep - INTERVAL_WINDOW :]]
            self._iv = self._iv[:keep] + local_intervals(tail, fb, start=INTERVAL_WINDOW)
        elif len(self._iv) > n:
            self._iv = self._iv[:n]
        self._iv_keep = n
        self._iv_list = self._readings
        return self._iv

    def drop_after(self, ts: int) -> int:
        """Remove readings newer than ts. Used only when the user confirms it in the Repairs notice about readings
        later than the system clock (never automatically). Returns count removed."""
        return self._remove(lambda r: r.timestamp > ts)

    def detect_interval(self) -> int:
        """Detect the CGM's reading interval from the stored readings, keep it in the header and return it.

        The median (gaps.median_interval) of the intervals between the last 50 readings that are longer than 30 s
        and shorter than 15 minutes. Only when the readings have no such interval (a source that reads every 15
        minutes or less often), the median of their intervals from 15 minutes to one hour is used, so that such a
        source is shown with its real interval (900 s for a 15-minute source). The first rule decides whenever a
        shorter interval exists, so the interval of a 1- to 5-minute source, and with it the gap rule, does not
        depend on intervals of 15 minutes or more (outages, or 15-minute history before live readings). With fewer
        than 10 readings, or no interval in either range, the interval stays as it was.
        """
        if len(self._readings) < DETECT_MIN_READINGS:
            return self._detected_interval
        times = [r.timestamp for r in self._readings[-DETECT_READINGS:]]
        window = DETECT_READINGS - 1
        median = median_interval(
            times, window=window, shortest=_DETECT_SHORTEST, longest=_DETECT_SHORT_LONGEST, minimum=1
        )
        if median is None:
            median = median_interval(
                times, window=window, shortest=_DETECT_SHORT_LONGEST + 1, longest=_DETECT_SLOW_LONGEST, minimum=1
            )
        if median is None:
            return self._detected_interval
        self._detected_interval = median
        return median

    def purge_before(self, before_ts: int) -> int:
        """Remove readings older than a timestamp. Returns count removed."""
        if not self._loaded:
            self.load()
        removed = self._remove(lambda r: r.timestamp < before_ts)
        if removed > 0:
            _LOGGER.debug("Purged %d old readings", removed)
        return removed

    def _remove(self, drop) -> int:
        """Remove the readings for which `drop` is true; return the number removed."""
        first = next((i for i, r in enumerate(self._readings) if drop(r)), None)
        if first is None:
            return 0
        before = len(self._readings)
        self._replace_list(self._readings[:first] + [r for r in self._readings[first:] if not drop(r)], first)
        return before - len(self._readings)

    def _replace_list(self, readings: list[GlucoseReading], first: int) -> None:
        """Use a new list of readings whose first `first` are those of the current list (a caller that holds the
        current list keeps it unchanged, as before); the save and the interval cache carry over to it."""
        if self._disk_list is self._readings:
            self._disk_list = readings
        if self._iv_list is self._readings:
            self._iv_list = readings
        before = len(self._readings)
        self._readings = readings
        self._changed(first, before)
