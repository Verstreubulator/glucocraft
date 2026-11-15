# License: AGPL-3.0-only (LICENSE).
"""GC02 storage (storage.py), the history import (history_import.py) and how readings reach the storage
(coordinator: measurement times, daylight-saving fall-back, the second source, repeated states).

Rules under test: 16-byte header "GC02", version 3, detected interval (uint16 LE); 10 bytes per reading (uint32 time,
uint16 raw x 10, uint16 reserved (written as 0, its value never used), uint8 limit, uint8 source, little-endian); a file
with values in the reserved field loads unchanged and is written in full at its next save; a file of another version is
not read as readings (it is kept aside as damaged); x10 rounded half away from zero; sorted, no duplicate times,
readings later than the clock kept, 20-500 mg/dL only; a damaged file is kept aside, never overwritten, and the readings
of a file with a damaged header are recovered; the history import accepts only rows outside the stored span, in time
order, and counts only accepted rows. Saving (TestAppendSave): new readings are appended, every other change writes
the whole file, and after every save the file is byte for byte what a full write of the same readings gives, also
after a loss of power during an append, a damaged tail, a temporary file left behind or a failed write; the reading
intervals are recomputed only from the first changed reading and equal a full computation.
"""

from __future__ import annotations

import asyncio
import os
import random
import struct
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from glucocraft import coordinator as co
from glucocraft import gaps
from glucocraft import history_import as hi
from glucocraft import storage as storage_mod
from glucocraft.const import (
    STORAGE_HEADER_SIZE,
    STORAGE_MAGIC,
    STORAGE_READING_SIZE,
    STORAGE_VERSION,
)
from glucocraft.storage import GlucoCraftStorage, GlucoseReading

from conftest import LA, add_reading, local_ts, real_coordinator

T0 = 1_700_000_000


def reopen(s: GlucoCraftStorage) -> GlucoCraftStorage:
    s2 = GlucoCraftStorage(str(s._dir), s._path.stem.removeprefix("glucocraft_"))
    s2.load()
    return s2


def write_raw(path, header: bytes, rows: list[tuple[int, int, int]], tail: bytes = b"") -> None:
    """A file of version 3 records (time, raw x 10, reserved field), each without a limit and from the primary."""
    path.write_bytes(header + b"".join(struct.pack("<IHHBB", *r, 0, 1) for r in rows) + tail)


def header(interval: int = 300, magic: bytes = STORAGE_MAGIC, version: int = STORAGE_VERSION) -> bytes:
    """A header (version 3 by default)."""
    h = bytearray(STORAGE_HEADER_SIZE)
    h[:4] = magic
    h[4] = version
    struct.pack_into("<H", h, 6, interval)
    return bytes(h)


# ------------------------------------------------------------------------------------------------ the file format
class TestFileFormat:
    def test_constants(self):
        assert STORAGE_MAGIC == b"GC02" and STORAGE_VERSION == 3
        assert STORAGE_HEADER_SIZE == 16 and STORAGE_READING_SIZE == 10 == struct.calcsize("<IHHBB")

    def test_a_version_2_file_is_not_read_as_readings(self, tmp_path):
        """Only version 3 is read: a file of the 8-byte records of version 2 is kept aside as damaged (GlucoCraft's
        first published format is version 3)."""
        s = GlucoCraftStorage(str(tmp_path), "v2")
        body = b"".join(struct.pack("<IHH", T0 + i * 300, 1000 + i, 0) for i in range(41))
        s._path.write_bytes(header(version=2) + body)
        s.load()
        assert s.readings == [] and s.problem == "damaged"

    def test_header_and_record_bytes(self, storage):
        storage.detected_interval = 60
        storage._readings = [GlucoseReading(T0, 120.5)]
        storage.save()
        data = storage._path.read_bytes()
        assert len(data) == 16 + 10
        assert data[:4] == b"GC02" and data[4] == 3
        assert data[6:8] == b"\x3c\x00"  # 60, little-endian
        assert data[16:20] == struct.pack("<I", T0)
        assert struct.unpack("<HH", data[20:24]) == (1205, 0)  # raw x 10; the reserved field 0
        assert data[24:26] == b"\x00\x00"  # no limit, source unknown

    def test_round_trip(self, storage):
        rows = [GlucoseReading(T0 + 60 * i, 40 + i * 0.7) for i in range(500)]
        storage._readings = list(rows)
        storage.detected_interval = 60
        storage.save()
        back = reopen(storage)
        assert back.detected_interval == 60 and back.reading_count == 500
        for a, b in zip(rows, back.readings, strict=False):
            assert a.timestamp == b.timestamp
            assert b.raw == pytest.approx(a.raw, abs=0.05)

    @pytest.mark.parametrize("raw, stored", [(120.25, 120.3), (120.35, 120.4), (120.24, 120.2), (70.05, 70.1)])
    def test_save_rounds_half_up(self, storage, raw, stored):
        storage._readings = [GlucoseReading(T0, raw)]
        storage.save()
        back = reopen(storage).readings[0]
        assert back.raw == stored

    def test_a_file_with_values_in_the_reserved_field_loads_and_is_written_with_0(self, tmp_path, monkeypatch):
        """A file with values in the reserved field loads unchanged (the values are not used), and its next save
        writes it in full with the field 0, the bytes of a full write."""
        s = GlucoCraftStorage(str(tmp_path), "old")
        write_raw(s._path, header(60), [(T0 + 60 * i, 1000 + i, 990 + i) for i in range(20)])
        s.load()
        assert s.problem is None and s.reading_count == 20
        assert [(r.timestamp, r.raw, r.limit, r.source) for r in s.readings] == [
            (T0 + 60 * i, (1000 + i) / 10, None, 1) for i in range(20)
        ]
        assert not hasattr(s.readings[0], "smoothed")
        whole = count_whole_writes(monkeypatch, s)
        s.insert_reading(T0 + 60 * 20, 130, None, 1)
        s.save()
        data = s._path.read_bytes()
        assert len(whole) == 1 and data == full_write(s, tmp_path / "ref")
        body = data[STORAGE_HEADER_SIZE:]
        assert not any(body[6::STORAGE_READING_SIZE]) and not any(body[7::STORAGE_READING_SIZE])
        assert reopen(s).reading_count == 21

    def test_atomic_write_leaves_no_temp_file(self, storage):
        storage._readings = [GlucoseReading(T0, 100)]
        storage.save()
        assert storage._path.exists() and not storage._path.with_suffix(".tmp").exists()
        assert storage.dirty is False

    def test_empty_storage_saves_header_only(self, storage):
        storage.save()
        assert storage._path.read_bytes() == header(300, version=STORAGE_VERSION)


# ------------------------------------------------------------------------------------------------ loading rules
class TestLoad:
    def test_missing_file_is_empty(self, tmp_path):
        s = GlucoCraftStorage(str(tmp_path), "none")
        assert s.readings == [] and s.detected_interval == 300

    def test_filters_values_zero_times_duplicates_and_sorts(self, tmp_path):
        s = GlucoCraftStorage(str(tmp_path), "f")
        write_raw(
            s._path,
            header(60),
            [
                (T0 + 120, 1200, 0),
                (T0, 1000, 0),
                (0, 1100, 0),  # unsorted, a zero time
                (T0 + 60, 199, 0),
                (T0 + 61, 200, 0),  # 19.9 dropped, 20.0 kept
                (T0 + 180, 5000, 0),
                (T0 + 181, 5001, 0),  # 500.0 kept, 500.1 dropped
                (T0 + 240, 0xFFFF, 0),  # sensor error marker
                (T0 + 120, 1300, 0),  # duplicate time: one kept
            ],
            tail=b"\x01\x02\x03",
        )  # a truncated record at the end is ignored
        s.load()
        assert [r.timestamp for r in s.readings] == [T0, T0 + 61, T0 + 120, T0 + 180]
        assert [r.raw for r in s.readings][:2] == [100.0, 20.0] and s.readings[-1].raw == 500.0
        assert s.detected_interval == 60

    def test_future_readings_kept(self, tmp_path):
        """A reading later than the clock is kept: when the clock is behind, the reading is right and the clock is
        wrong, and a deletion saved at the next save would be permanent."""
        s = GlucoCraftStorage(str(tmp_path), "fut")
        now = int(time.time())
        write_raw(s._path, header(), [(now - 600, 1000, 0), (now + 3600, 1000, 0)])
        s.load()
        assert [r.timestamp for r in s.readings] == [now - 600, now + 3600]
        assert s.newest_timestamp == now + 3600
        s.save()  # the next save keeps both readings
        again = GlucoCraftStorage(str(tmp_path), "fut")
        again.load()
        assert [r.timestamp for r in again.readings] == [now - 600, now + 3600]

    def test_zero_interval_in_header_defaults(self, tmp_path):
        s = GlucoCraftStorage(str(tmp_path), "z")
        write_raw(s._path, header(0), [(T0, 1000, 0)])
        s.load()
        assert s.detected_interval == 300

    @pytest.mark.parametrize(
        "bad",
        [
            b"",
            b"GC0",
            header(magic=b"GC01") + b"\xff" * 8,
            header(version=2) + bytes(range(8)),  # an older version: no version-2 reader is left
            b"XXXX" + bytes(12) + b"\x01" * 8,
        ],
    )
    def test_damaged_file_kept_aside(self, tmp_path, bad):
        s = GlucoCraftStorage(str(tmp_path), "bad")
        s._path.write_bytes(bad + struct.pack("<IHH", T0, 1000, 0))
        s.load()
        assert s.readings == [] and s.problem == "damaged"
        kept = [p for p in os.listdir(tmp_path) if ".corrupt-" in p]
        assert len(kept) == 1 and not s._path.exists() and s.kept_file == kept[0]
        s._readings = [GlucoseReading(T0, 100)]
        s.save()  # a new file; the damaged one is untouched
        assert s._path.exists() and len([p for p in os.listdir(tmp_path) if ".corrupt-" in p]) == 1

    @pytest.mark.parametrize("bad", [header(magic=b"GC01"), header(version=2), b"XXXX" + bytes(12)])
    def test_damaged_header_readings_recovered(self, tmp_path, bad):
        """A damaged header with intact records: the readings are recovered, a copy of the damaged file is kept and
        the next save writes a new header."""
        s = GlucoCraftStorage(str(tmp_path), "rec")
        recs = [(T0 + i * 300, 1000 + i, 0, 0, 1) for i in range(40)]
        original = bad + b"".join(struct.pack("<IHHBB", *r) for r in recs)
        s._path.write_bytes(original)
        s.load()
        assert [r.timestamp for r in s.readings] == [T0 + i * 300 for i in range(40)]
        assert s.problem == "recovered" and s.dirty
        kept = [p for p in os.listdir(tmp_path) if ".corrupt-" in p]
        assert kept == [s.kept_file] and (tmp_path / kept[0]).read_bytes() == original
        s.save()
        again = GlucoCraftStorage(str(tmp_path), "rec")
        again.load()
        assert again.problem is None and len(again.readings) == 40

    def test_save_blocked_after_read_error(self, storage):
        storage._save_blocked = True
        storage._readings = [GlucoseReading(T0, 100)]
        storage.save()
        assert not storage._path.exists()


# ------------------------------------------------------------------------------------------------ editing
class TestEditing:
    def test_insert_reading_keeps_order_and_replaces_same_time(self, storage):
        for t in (T0, T0 + 120, T0 + 240):
            add_reading(storage, t, 100)
        assert storage.insert_reading(T0 + 60, 105) == 1
        assert storage.insert_reading(T0 + 120, 130) == 2
        assert [(r.timestamp - T0, r.raw) for r in storage.readings] == [(0, 100), (60, 105), (120, 130), (240, 100)]

    def test_replace_at(self, storage):
        for t in (T0, T0 + 300, T0 + 600):
            add_reading(storage, t, 100)
        idx = storage.replace_at(1, T0 + 290, 101)
        assert idx == 1 and [r.timestamp - T0 for r in storage.readings] == [0, 290, 600]

    def test_nearest(self, storage):
        assert storage.nearest(T0) == (-1, None)
        for t in (T0, T0 + 300, T0 + 600):
            add_reading(storage, t, 100)
        assert storage.nearest(T0 + 140) == (0, 140)
        assert storage.nearest(T0 + 160) == (1, 140)
        assert storage.nearest(T0 + 10_000) == (2, 10_000 - 600)
        assert storage.nearest(T0 - 5) == (0, 5)

    def test_window_is_half_open(self, storage):
        for t in range(0, 600, 60):
            add_reading(storage, T0 + t, 100)
        win = storage.window(T0 + 60, T0 + 180)
        assert [r.timestamp - T0 for r in win] == [60, 120]
        assert storage.window(T0 + 1000, T0 + 2000) == []

    def test_drop_after(self, storage):
        for t in range(0, 600, 60):
            add_reading(storage, T0 + t, 100)
        storage.dirty = False
        assert storage.drop_after(T0 + 300) == 4 and storage.dirty
        assert storage.drop_after(T0 + 300) == 0


# ------------------------------------------------------------------------------------------------ retention
class TestPurge:
    def test_purge_before(self, storage):
        for d in range(200):
            add_reading(storage, T0 + d * 86400, 100)
        storage.dirty = False
        cutoff = T0 + 20 * 86400  # 180 days kept
        assert storage.purge_before(cutoff) == 20
        assert storage.readings[0].timestamp == cutoff and storage.reading_count == 180 and storage.dirty

    def test_purge_nothing(self, storage):
        add_reading(storage, T0, 100)
        storage.dirty = False
        assert storage.purge_before(T0) == 0 and not storage.dirty

    def test_purge_everything_and_save(self, storage):
        add_reading(storage, T0, 100)
        assert storage.purge_before(T0 + 1) == 1
        storage.save()
        assert reopen(storage).readings == []


# ------------------------------------------------------------------------------------------------ interval detection
class TestDetectInterval:
    def test_needs_ten_readings(self, storage):
        for i in range(9):
            add_reading(storage, T0 + 60 * i, 100)
        assert storage.detect_interval() == 300

    @pytest.mark.parametrize("step", [60, 120, 300])
    def test_median(self, storage, step):
        for i in range(60):
            add_reading(storage, T0 + step * i + (7 if i % 9 == 0 else 0), 100)
        assert abs(storage.detect_interval() - step) <= 7

    def test_ignores_gaps_and_bursts(self, storage):
        t = T0
        for i in range(50):
            t += 60 if i % 5 else 3600  # a long gap every fifth reading
            add_reading(storage, t, 100)
            add_reading(storage, t + 5, 100)  # a 5-second repeat
        assert storage.detect_interval() == 55

    @pytest.mark.parametrize("minutes", [1, 5, 15])
    def test_the_real_interval_up_to_15_minutes(self, storage, minutes):
        """A 1-, 5- or 15-minute source is detected with its own interval (a 15-minute source as 900 s, not 300 s)."""
        for i in range(60):
            add_reading(storage, T0 + 60 * minutes * i, 100)
        assert storage.detect_interval() == 60 * minutes
        storage.save()
        assert reopen(storage).detected_interval == 60 * minutes  # kept in the file header

    def test_15_minute_source_with_outages(self, storage):
        t = T0
        for i in range(60):
            t += 3600 if i % 10 == 9 else 900  # an hour without data every tenth reading
            add_reading(storage, t, 100)
        assert storage.detect_interval() == 900

    def test_mixed_history_15_minute_history_then_1_minute_readings(self, storage):
        """15-minute history followed by 1-minute live readings: the 1-minute interval, as before."""
        t = T0
        for _ in range(40):
            t += 900
            add_reading(storage, t, 100)
        for _ in range(12):
            t += 60
            add_reading(storage, t, 100)
        assert storage.detect_interval() == 60
        assert gaps.gap_threshold(storage.detected_interval) == 900

    @pytest.mark.parametrize("step", [60, 120, 300])
    def test_1_to_5_minute_sources_keep_the_earlier_rule(self, storage, step):
        """With outages of exactly 15 minutes and longer, a 1- to 5-minute source gets what the earlier rule gave:
        the median of the intervals from 31 s to 899 s; the gap threshold is unchanged."""
        rng = random.Random(step)
        t = T0
        for _ in range(200):
            t += rng.choice([step] * 8 + [step + 3, 900, 1800, 7200])
            add_reading(storage, t, 100)
            if len(storage.readings) >= 10:
                times = [r.timestamp for r in storage.readings[-50:]]
                earlier = gaps.median_interval(times, window=49, shortest=31, longest=899, minimum=1)
                assert storage.detect_interval() == earlier
                assert gaps.gap_threshold(storage.detected_interval) == gaps.gap_threshold(earlier) == 900


# ------------------------------------------------------------------------------------------------ history merge
class TestMergeHistory:
    def test_only_outside_the_stored_span_in_order(self, storage):
        storage._readings = [GlucoseReading(1000, 100), GlucoseReading(1060, 101)]
        n = storage.merge_history([(500, 90), (2000, 120), (400, 80), (1030, 99), (400, 81), (975, 95), (1085, 96)])
        assert n == 3  # 400, 500, 2000; 975 / 1030 / 1085 inside the span + margin
        assert [r.timestamp for r in storage.readings] == [400, 500, 1000, 1060, 2000]
        assert storage.readings[0].raw == 80  # the first of two rows with the same time

    def test_margin_boundary(self, storage):
        storage._readings = [GlucoseReading(1000, 100)]
        assert storage.merge_history([(970, 1), (969, 2), (1030, 3), (1031, 4)]) == 2
        assert [r.timestamp for r in storage.readings] == [969, 1000, 1031]

    def test_into_empty_storage(self, storage):
        assert storage.merge_history([(30, 1), (10, 2), (20, 3)]) == 3
        assert [r.timestamp for r in storage.readings] == [10, 20, 30]

    def test_nothing_accepted(self, storage):
        storage._readings = [GlucoseReading(1000, 100), GlucoseReading(2000, 100)]
        storage.dirty = False
        assert storage.merge_history([]) == 0
        assert storage.merge_history([(1500, 1)]) == 0 and not storage.dirty


class TestHistoryImport:
    @pytest.fixture
    def importer(self, monkeypatch, fake_hass):
        async def run(fn, *a):
            return fn(*a)

        def make(rows, unit="mmol/L"):
            monkeypatch.setattr(hi, "get_instance", lambda h: SimpleNamespace(async_add_executor_job=run))
            monkeypatch.setattr(hi, "_get_history", lambda h, e, a, b: rows)
            return fake_hass(states={"sensor.x": SimpleNamespace(attributes={"unit_of_measurement": unit})})

        return make

    def test_import_converts_filters_counts_and_saves(self, importer, storage):
        now = int(time.time())
        storage._readings = [GlucoseReading(now - 30, 150)]  # a live reading came first
        rows = [
            SimpleNamespace(state=v, attributes={}, last_updated=datetime.fromtimestamp(now - 3600 + 300 * i, UTC))
            for i, v in enumerate(["5.0", "33.4", "unknown", "abc", "10.0", "1.0", "6.1", "unavailable", ""])
        ]
        hass = importer(rows)
        lock = threading.Lock()
        n = asyncio.run(hi.async_import_history(hass, "sensor.x", storage, lock=lock))
        # sensor limits (limits.py): 33.4 mmol/L (601.7 mg/dL) is HI, stored at the upper limit 500; 1.0 (18 mg/dL) is LO,
        # stored at 40; "abc" is no reading
        assert n == 5
        assert [r.raw for r in storage.readings] == [90.1, 500.0, 180.0, 40.0, 109.9, 150]  # 10.0 snaps to 180
        assert [r.limit for r in storage.readings] == [None, "high", None, "low", None, None]
        assert {r.source for r in storage.readings[:5]} == {1}  # the primary's history
        assert not lock.locked() and storage._path.exists()

    def test_mgdl_source(self, importer, storage):
        now = int(time.time())
        rows = [
            SimpleNamespace(state=v, attributes={}, last_updated=datetime.fromtimestamp(now - 600 + 60 * i, UTC))
            for i, v in enumerate(["120.25", "500", "500.1", "19.9", "20"])
        ]
        n = asyncio.run(hi.async_import_history(importer(rows, "mg/dL"), "sensor.x", storage))
        # sensor limits: 500.1 above the upper limit (500) is HI at 500; 19.9 and 20 (<= 39) are LO at 40
        assert n == 5 and [r.raw for r in storage.readings] == [120.3, 500.0, 500.0, 40.0, 40.0]
        assert [r.limit for r in storage.readings] == [None, None, "high", "low", "low"]

    def test_nothing_found(self, importer, storage):
        assert asyncio.run(hi.async_import_history(importer([]), "sensor.x", storage)) == 0
        assert not storage._path.exists()


# ------------------------------------------------------------------------------------------------ reading times
class TestReadingTimes:
    """The measurement time of a reading (coordinator._local_naive_ts / _parse_reading_time)."""

    def test_fall_back_second_occurrence(self):
        naive = datetime(2026, 11, 1, 1, 30)  # 01:30 happens twice in America/Los_Angeles
        first = local_ts(LA, 2026, 11, 1, 1, 30, fold=0)
        second = local_ts(LA, 2026, 11, 1, 1, 30, fold=1)
        assert second - first == 3600
        assert co._local_naive_ts(naive, LA) == first  # no history: first
        assert co._local_naive_ts(naive, LA, after=first - 60, now=second + 600) == first
        assert co._local_naive_ts(naive, LA, after=first + 1800, now=second + 600) == second  # PDT 02:00 seen
        assert co._local_naive_ts(naive, LA, after=first + 1800, now=first + 1900) == first  # 2nd in the future
        # the same measurement again keeps its time, in either occurrence
        assert co._local_naive_ts(naive, LA, after=second, now=second + 600) == second
        assert co._local_naive_ts(naive, LA, after=first, now=second + 600) == first
        # older than both occurrences: the first (a reading older than the last one, refused as such)
        assert co._local_naive_ts(naive, LA, after=second + 60, now=second + 600) == first

    def test_whole_repeated_hour_is_kept(self):
        """A source with local times through the repeated hour: every reading gets a new, increasing time."""
        last = local_ts(LA, 2026, 11, 1, 0, 55)
        stamps = []
        wall = [(0, 55), *[(1, m) for m in range(0, 60, 5)], *[(1, m) for m in range(0, 60, 5)], (2, 0)]
        now_after = local_ts(LA, 2026, 11, 1, 3, 0)
        for h, m in wall:
            ts = co._local_naive_ts(datetime(2026, 11, 1, h, m), LA, after=last, now=now_after)
            stamps.append(ts)
            last = max(last, ts)
        assert stamps[1:] == sorted(stamps[1:]) and len(set(stamps[1:])) == len(stamps) - 1
        assert stamps[-1] - stamps[1] == 2 * 3600  # 01:00 PDT ... 02:00 PST = two real hours

    def test_spring_forward_gap_does_not_crash(self):
        ts = co._local_naive_ts(datetime(2026, 3, 8, 2, 30), LA)  # does not exist in LA
        assert local_ts(LA, 2026, 3, 8, 1, 59) < ts < local_ts(LA, 2026, 3, 8, 3, 31)

    def test_not_ambiguous(self):
        plain = datetime(2026, 10, 22, 8, 0)
        p = int(plain.replace(tzinfo=LA).timestamp())
        assert co._local_naive_ts(plain, LA, after=p + 10, now=p + 100) == p

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("2026-11-01T01:30:00+00:00", int(datetime(2026, 11, 1, 1, 30, tzinfo=UTC).timestamp())),
            ("2026-10-22T08:00:00-07:00", int(datetime(2026, 10, 22, 15, 0, tzinfo=UTC).timestamp())),
            ("1761148800000", 1761148800),
            ("1761148800", 1761148800),
            (1761148800.9, 1761148800),
            ("12345", None),
            ("2026-10-22", None),
            ("not a time", None),
            (None, None),
            (True, None),
            ("", None),
        ],
    )
    def test_parse(self, value, expected):
        assert co._parse_reading_time(value) == expected

    def test_parse_naive_in_ha_time_zone(self):
        assert co._parse_reading_time("2026-10-22T08:00:00") == local_ts(LA, 2026, 10, 22, 8, 0)
        first = local_ts(LA, 2026, 11, 1, 1, 30)
        assert co._parse_reading_time("2026-11-01T01:30:00", after=first + 1800) in (first, first + 3600)

    def test_parse_datetime_object(self):
        d = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)
        assert co._parse_reading_time(d) == int(d.timestamp())


# ------------------------------------------------------------------------------------------------ the coordinator
def bare_coordinator(tmp_path) -> co.GlucoCraftCoordinator:
    """A coordinator with the primary source sensor.p and the second source sensor.s, neither in use yet."""
    return real_coordinator(tmp_path, _secondary_entity="sensor.s", _time_attr={"sensor.p": "", "sensor.s": ""})


class TestTwoSources:
    def test_no_interleave_while_primary_is_healthy(self, tmp_path):
        """A faster second source is not stored between the primary's readings."""
        c = bare_coordinator(tmp_path)
        base = int(time.time()) - 4 * 3600
        flips = set()
        for t in range(0, 3600, 60):  # an hour: primary every 300 s, secondary every 60 s
            if t % 300 == 0:
                c._process_reading(100 + t / 600, base + t, "sensor.p", "P", True)
            c._process_reading(100 + t / 600, base + t + 20, "sensor.s", "S", True)
            flips.add(c.active_source_entity)
        stamps = [r.timestamp - base for r in c.storage.readings]
        assert all(s % 300 == 0 for s in stamps), stamps
        assert flips == {"sensor.p"}

    def test_secondary_takes_over_after_one_interval_and_hands_back(self, tmp_path):
        c = bare_coordinator(tmp_path)
        base = int(time.time()) - 4 * 3600
        for t in range(0, 3600, 300):
            c._process_reading(100, base + t, "sensor.p", "P", True)
        for t in range(3600, 5400, 60):  # the primary stops
            c._process_reading(110, base + t + 20, "sensor.s", "S", True)
        assert c.active_source_entity == "sensor.s" and c.second_source_active
        later = [r.timestamp - base for r in c.storage.readings if r.timestamp - base > 3300]
        assert later and min(later) >= 3300 + 300
        c._process_reading(111, base + 5400, "sensor.p", "P", True)
        assert c.active_source_entity == "sensor.p" and not c.second_source_active


class TestStateReported:
    """A source without a measurement-time attribute that writes the same value again."""

    def make(self, tmp_path):
        c = bare_coordinator(tmp_path)
        calls = []
        c.hass = SimpleNamespace(async_add_executor_job=lambda *a: calls.append(a))
        c._watch_source = True  # Home Assistant has started
        return c, calls

    @staticmethod
    def event(entity, value="120", attrs=None, reported=None):
        st = SimpleNamespace(
            state=value, attributes={"unit_of_measurement": "mg/dL", **(attrs or {})}, last_reported=reported
        )
        return SimpleNamespace(data={"entity_id": entity, "new_state": st})

    def test_dedupe_by_time(self, tmp_path):
        c, calls = self.make(tmp_path)
        now = int(time.time())
        rep = datetime.fromtimestamp(now, UTC)
        c._src_last_ts["sensor.p"] = now - 100  # 100 s < 3/4 x 300 s: the same reading re-written
        c._async_state_reported(self.event("sensor.p", reported=rep))
        assert calls == []
        c._src_last_ts["sensor.p"] = now - 240  # a new reading with an unchanged value
        c._async_state_reported(self.event("sensor.p", reported=rep))
        assert len(calls) == 1 and calls[0][1:] == ("sensor.p", "sensor.p", 120.0, now, False, None)

    def test_with_measurement_time_is_the_same_measurement(self, tmp_path):
        c, calls = self.make(tmp_path)
        now = int(time.time())
        c._src_last_ts["sensor.p"] = now - 400
        c._async_state_reported(
            self.event(
                "sensor.p", attrs={"measurement_timestamp": now - 400}, reported=datetime.fromtimestamp(now, UTC)
            )
        )
        assert calls == []

    @pytest.mark.parametrize(
        "entity, value", [("sensor.other", "120"), ("sensor.p", "unavailable"), ("sensor.p", "unknown")]
    )
    def test_ignored(self, tmp_path, entity, value):
        c, calls = self.make(tmp_path)
        c._src_last_ts["sensor.p"] = 0
        c._async_state_reported(self.event(entity, value, reported=datetime.now(UTC)))
        assert calls == []


# ------------------------------------------------------------------------------------------------ saving: append
def full_write(s: GlucoCraftStorage, folder) -> bytes:
    """The bytes a full write of the readings of `s` gives (a fresh store in another folder, the same header)."""
    folder.mkdir(exist_ok=True)
    ref = GlucoCraftStorage(str(folder), "ref")
    ref._loaded = True
    ref._flags = s._flags
    ref.detected_interval = s.detected_interval
    ref._readings = [GlucoseReading(r.timestamp, r.raw, r.limit, r.source) for r in s.readings]
    ref._write_whole(ref._header())
    return ref._path.read_bytes()


def written_whole(s: GlucoCraftStorage) -> bytes:
    """What a full write of the readings of `s` writes (_write_whole: the header, then every record), in memory."""
    return s._header() + storage_mod._pack(s.readings)


def count_whole_writes(monkeypatch, s: GlucoCraftStorage) -> list[int]:
    """Count the full writes of `s` (a list that grows by one per full write)."""
    calls: list[int] = []
    real = s._write_whole

    def spy(header):
        calls.append(len(s._readings))
        return real(header)

    monkeypatch.setattr(s, "_write_whole", spy)
    return calls


def filled(tmp_path, count: int = 30, step: int = 60, name: str = "a") -> GlucoCraftStorage:
    """A saved store of `count` readings, one every `step` seconds from T0."""
    s = GlucoCraftStorage(str(tmp_path), name)
    s.load()
    for i in range(count):
        s.insert_reading(T0 + step * i, 100 + i % 50 + 0.25, None, 1)
    s.save()
    return s


class TestAppendSave:
    def test_new_readings_are_appended(self, tmp_path, monkeypatch):
        s = filled(tmp_path)
        before = s._path.read_bytes()
        whole = count_whole_writes(monkeypatch, s)
        for i in range(30, 33):
            s.insert_reading(T0 + 60 * i, 120.0, "high" if i == 31 else None, 2)
        s.save()
        data = s._path.read_bytes()
        assert whole == [] and not s.dirty
        assert data[: len(before)] == before and len(data) == len(before) + 3 * STORAGE_READING_SIZE
        assert data == full_write(s, tmp_path / "ref")
        assert [r.timestamp for r in reopen(s).readings] == [T0 + 60 * i for i in range(33)]

    def test_a_save_without_changes_writes_nothing(self, tmp_path, monkeypatch):
        s = filled(tmp_path)
        before = s._path.read_bytes()
        whole = count_whole_writes(monkeypatch, s)
        s.save()
        assert whole == [] and s._path.read_bytes() == before

    @pytest.mark.parametrize(
        "change",
        [
            lambda s: s.insert_reading(T0 + 90, 150, None, 2),  # a gap filled by the other source
            lambda s: s.insert_reading(T0 + 60, 150, None, 1),  # the same time again
            lambda s: s.replace_at(29, T0 + 60 * 29 + 5, 150, None, 1),  # the primary's copy
            lambda s: s.purge_before(T0 + 600),
            lambda s: s.drop_after(T0 + 600),
            lambda s: s.merge_history([(T0 - 3600, 90.0)]),
        ],
        ids=["insert", "same-time", "replace", "purge", "drop-after", "older-history"],
    )
    def test_other_changes_write_the_whole_file(self, tmp_path, monkeypatch, change):
        s = filled(tmp_path)
        whole = count_whole_writes(monkeypatch, s)
        change(s)
        s.insert_reading(T0 + 60 * 40, 130, None, 1)
        s.save()
        assert len(whole) == 1 and s._path.read_bytes() == full_write(s, tmp_path / "ref")
        s.insert_reading(T0 + 60 * 41, 131, None, 1)
        s.save()  # and the next new reading is appended again
        assert len(whole) == 1 and s._path.read_bytes() == full_write(s, tmp_path / "ref")

    def test_newer_history_is_appended(self, tmp_path, monkeypatch):
        s = filled(tmp_path)
        whole = count_whole_writes(monkeypatch, s)
        assert s.merge_history([(T0 + 60 * 40, 90.0), (T0 + 60 * 41, 91.0, "low")]) == 2
        s.save()
        assert whole == [] and s._path.read_bytes() == full_write(s, tmp_path / "ref")

    def test_a_changed_interval_is_written_in_place(self, tmp_path, monkeypatch):
        s = filled(tmp_path)
        whole = count_whole_writes(monkeypatch, s)
        assert s.detect_interval() == 60 and s._path.read_bytes()[6:8] == struct.pack("<H", 300)
        s.save()
        assert whole == [] and s._path.read_bytes() == full_write(s, tmp_path / "ref")
        assert reopen(s).detected_interval == 60

    def test_a_list_assigned_as_a_whole_is_written_whole(self, tmp_path, monkeypatch):
        s = filled(tmp_path)
        whole = count_whole_writes(monkeypatch, s)
        s._readings = [GlucoseReading(T0 + 5, 99.0)]
        s.save()
        assert len(whole) == 1 and [r.timestamp for r in reopen(s).readings] == [T0 + 5]

    def test_the_first_save_is_written_whole_then_appended_to(self, tmp_path, monkeypatch):
        s = GlucoCraftStorage(str(tmp_path), "first")
        s.load()
        for i in range(20):
            s.insert_reading(T0 + 60 * i, 100 + i, None, 1)
        whole = count_whole_writes(monkeypatch, s)
        s.save()
        assert len(whole) == 1 and s._path.read_bytes()[4] == STORAGE_VERSION
        s.insert_reading(T0 + 60 * 20, 100, None, 1)
        s.save()
        assert len(whole) == 1 and s._path.read_bytes() == full_write(s, tmp_path / "ref")

    def test_a_recovered_file_is_written_whole_then_appended_to(self, tmp_path, monkeypatch):
        s = GlucoCraftStorage(str(tmp_path), "rec")
        recs = [(T0 + i * 300, 1000 + i, 0, 0, 1) for i in range(40)]
        s._path.write_bytes(b"XXXX" + bytes(12) + b"".join(struct.pack("<IHHBB", *r) for r in recs))
        s.load()
        assert s.problem == "recovered"
        whole = count_whole_writes(monkeypatch, s)
        s.save()
        s.insert_reading(T0 + 300 * 40, 100, None, 1)
        s.save()
        assert len(whole) == 1 and s._path.read_bytes() == full_write(s, tmp_path / "ref")

    @pytest.mark.parametrize("damage", ["removed", "longer", "last-record", "header"])
    def test_a_file_changed_outside_is_written_whole(self, tmp_path, monkeypatch, damage):
        """Before an append the file's size, header and last saved record are checked."""
        s = filled(tmp_path)
        data = bytearray(s._path.read_bytes())
        if damage == "removed":
            s._path.unlink()
        elif damage == "longer":
            s._path.write_bytes(bytes(data) + b"\x01\x02\x03")
        elif damage == "last-record":
            data[-3] ^= 0xFF
            s._path.write_bytes(bytes(data))
        else:
            data[10] = 7  # a reserved byte
            s._path.write_bytes(bytes(data))
        whole = count_whole_writes(monkeypatch, s)
        s.insert_reading(T0 + 60 * 40, 130, None, 1)
        s.save()
        assert len(whole) == 1 and s._path.read_bytes() == full_write(s, tmp_path / "ref")

    def test_a_value_changed_on_the_last_saved_reading_is_not_lost(self, tmp_path):
        """Not done by GlucoCraft (a stored reading is changed only through the storage's methods), but caught by the
        check of the last saved record."""
        s = filled(tmp_path)
        s.readings[-1].raw = 77.7
        s.insert_reading(T0 + 60 * 40, 130, None, 1)
        s.save()
        assert s._path.read_bytes() == full_write(s, tmp_path / "ref")

    def test_random_sequences_equal_a_full_write(self, tmp_path, monkeypatch):
        """1,000 random sequences of the editing methods, saves and restarts: after every save the file is byte for
        byte what a full write gives, and the reading intervals always equal a full computation. (fsync is left out
        here for speed: it does not change what the file holds.)"""
        monkeypatch.setattr(os, "fsync", lambda fd: None)
        paths = {"append": 0, "whole": 0}
        real_append, real_whole = GlucoCraftStorage._append, GlucoCraftStorage._write_whole

        def append(self, *a):
            done = real_append(self, *a)
            paths["append"] += done
            return done

        def whole(self, *a):
            paths["whole"] += 1
            return real_whole(self, *a)

        monkeypatch.setattr(GlucoCraftStorage, "_append", append)
        monkeypatch.setattr(GlucoCraftStorage, "_write_whole", whole)
        rng = random.Random(20261029)
        for seq in range(1000):
            s = GlucoCraftStorage(str(tmp_path), f"p{seq}")
            s.load()
            t_next = T0
            for _ in range(rng.randint(5, 40)):
                op = rng.random()
                n = s.reading_count
                if op < 0.40:  # a new reading (the common case)
                    t_next += rng.choice([1, 60, 60, 60, 300, 900, 4000])
                    limit = rng.choice([None, None, "low", "high"])
                    s.insert_reading(t_next, rng.uniform(20, 500), limit, 1)
                elif op < 0.50 and n:  # out of order: a gap filled, or the same time again
                    if rng.random() < 0.7:
                        t = rng.randint(s.readings[0].timestamp - 600, s.readings[-1].timestamp)
                    else:
                        t = rng.choice(s.readings).timestamp
                    s.insert_reading(t, rng.uniform(20, 500), None, 2)
                elif op < 0.56 and n:
                    k = rng.randrange(n)
                    s.replace_at(k, s.readings[k].timestamp + rng.randint(-20, 20), 150.0, None, 1)
                elif op < 0.60 and n:
                    s.purge_before(rng.choice(s.readings).timestamp + rng.choice([0, 1]))
                elif op < 0.63 and n:
                    s.drop_after(rng.choice(s.readings).timestamp)
                elif op < 0.70:
                    first = s.readings[0].timestamp if n else T0
                    count = rng.randint(0, 8)
                    s.merge_history(
                        [(first + rng.randint(-20_000, 40_000), rng.uniform(40, 400)) for _ in range(count)]
                    )
                    t_next = max(t_next, s.newest_timestamp)
                elif op < 0.74:
                    s.detected_interval = rng.choice([60, 300, 900, s.detected_interval])
                elif op < 0.77:
                    s.detect_interval()
                elif op < 0.80 and n:
                    t_next = max(t_next, s.newest_timestamp) + 30
                    add_reading(s, t_next, 111.1, None, 1)
                elif op < 0.84:
                    s.save()
                    s = reopen(s)  # a restart
                else:
                    s.save()
                    assert s._path.read_bytes() == written_whole(s), seq
                assert s.reading_intervals(300) == gaps.local_intervals([r.timestamp for r in s.readings], 300), seq
            s.save()
            assert s._path.read_bytes() == written_whole(s), seq
            again = reopen(s)
            assert [(r.timestamp, r.limit, r.source) for r in again.readings] == [
                (r.timestamp, r.limit, r.source) for r in s.readings
            ]
        assert paths["append"] > 2000 and paths["whole"] > 2000, paths  # both ways of saving were exercised

    def test_the_coordinator_path_equals_a_full_write(self, tmp_path):
        """Two sources, a gap filled by the second source, the primary's copy replacing it, saves in between."""
        c = bare_coordinator(tmp_path)
        base = int(time.time()) - 4 * 3600
        steps = [(t, "sensor.p", 100 + t / 600) for t in range(0, 3600, 300)]
        steps += [(t + 20, "sensor.s", 110.0) for t in range(3600, 5400, 60)]
        steps += [(5400, "sensor.p", 111.0), (5420, "sensor.p", 112.0), (3920, "sensor.p", 109.0)]
        for k, (t, entity, value) in enumerate(steps):
            c._process_reading(value, base + t, entity, entity, True)
            if k % 3 == 0:
                c.storage.save()
                assert c.storage._path.read_bytes() == full_write(c.storage, tmp_path / "ref"), k
        c.storage.save()
        assert c.storage._path.read_bytes() == full_write(c.storage, tmp_path / "ref")
        assert reopen(c.storage).reading_count == c.storage.reading_count


class TestPowerLossAndDamage:
    def test_power_loss_during_an_append_at_every_byte(self, tmp_path):
        """A file cut anywhere inside the appended records loads the saved readings and every complete new record;
        the next save writes the whole file again (no append after a record cut short)."""
        s = filled(tmp_path, count=12)
        saved = s._path.read_bytes()
        for i in range(12, 15):
            s.insert_reading(T0 + 60 * i, 130 + i, "low" if i == 13 else None, 2)
        s.save()
        complete = s._path.read_bytes()
        for cut in range(len(saved), len(complete) + 1):
            s._path.write_bytes(complete[:cut])
            back = reopen(s)
            whole_records = (cut - len(saved)) // STORAGE_READING_SIZE
            assert [r.timestamp for r in back.readings] == [T0 + 60 * i for i in range(12 + whole_records)], cut
            back.insert_reading(T0 + 60 * 20, 140, None, 1)
            back.save()
            assert back._path.read_bytes() == full_write(back, tmp_path / "ref"), cut

    def test_a_zero_filled_tail_loses_no_saved_reading(self, tmp_path):
        """Zeroed records at the end (a file system that extended the file without its data) are left out."""
        s = filled(tmp_path, count=12)
        s._path.write_bytes(s._path.read_bytes() + bytes(3 * STORAGE_READING_SIZE))
        back = reopen(s)
        assert back.reading_count == 12 and back._on_disk == -1
        back.insert_reading(T0 + 60 * 20, 140, None, 1)
        back.save()
        assert back._path.read_bytes() == full_write(back, tmp_path / "ref")

    @pytest.mark.parametrize("tmp_content", ["complete", "partial", "empty"])
    def test_a_temporary_file_left_behind_is_ignored_and_removed(self, tmp_path, tmp_content):
        s = filled(tmp_path, count=12)
        tmp = s._path.with_suffix(".tmp")
        other = filled(tmp_path / "other", count=20)._path.read_bytes()
        tmp.write_bytes({"complete": other, "partial": other[:57], "empty": b""}[tmp_content])
        back = reopen(s)
        assert back.reading_count == 12 and not tmp.exists() and back.problem is None

    def test_a_temporary_file_without_a_readings_file_is_removed(self, tmp_path):
        s = GlucoCraftStorage(str(tmp_path), "first")
        s._path.with_suffix(".tmp").write_bytes(b"GC02")
        s.load()
        assert s.readings == [] and not s._path.with_suffix(".tmp").exists()

    def test_save_blocked_blocks_appends(self, tmp_path):
        s = filled(tmp_path)
        before = s._path.read_bytes()
        s._save_blocked = True
        s.insert_reading(T0 + 60 * 40, 130, None, 1)
        s.save()
        assert s._path.read_bytes() == before and s.dirty

    def test_a_read_error_never_leads_to_a_write(self, tmp_path, monkeypatch):
        s = filled(tmp_path)
        before = s._path.read_bytes()
        s2 = GlucoCraftStorage(str(tmp_path), "a")
        real = type(s2._path).read_bytes

        def fail(self):
            if self == s2._path:
                raise OSError("I/O error")
            return real(self)

        monkeypatch.setattr(type(s2._path), "read_bytes", fail)
        s2.load()
        assert s2.problem == "unreadable" and s2._on_disk == -1
        s2.insert_reading(T0 + 60 * 40, 130, None, 1)
        s2.save()
        monkeypatch.undo()
        assert s._path.read_bytes() == before

    def test_a_store_that_was_never_loaded_does_not_overwrite_the_file(self, tmp_path):
        s = filled(tmp_path)
        before = s._path.read_bytes()
        s2 = GlucoCraftStorage(str(tmp_path), "a")
        s2._readings = [GlucoseReading(T0, 100.0)]
        s2.save()
        assert s._path.read_bytes() == before

    def test_a_failed_append_is_followed_by_a_whole_write(self, tmp_path, monkeypatch):
        """The append fails after part of its bytes reached the file: the saved readings are intact, the readings
        stay unsaved, and the next save writes the whole file."""
        s = filled(tmp_path)
        saved = s._path.read_bytes()
        real_fsync = os.fsync

        def fail(fd):
            os.ftruncate(fd, len(saved) + 4)  # part of the first new record reached the disk
            raise OSError("disk full")

        monkeypatch.setattr(os, "fsync", fail)
        s.insert_reading(T0 + 60 * 40, 130, None, 1)
        s.save()
        monkeypatch.setattr(os, "fsync", real_fsync)
        assert s.dirty and s._on_disk == -1
        assert s._path.read_bytes()[: len(saved)] == saved and reopen(s).reading_count == 30
        whole = count_whole_writes(monkeypatch, s)
        s.save()
        assert len(whole) == 1 and s._path.read_bytes() == full_write(s, tmp_path / "ref") and not s.dirty

    def test_a_failed_whole_write_keeps_the_file_and_cleans_up(self, tmp_path, monkeypatch):
        """A temporary file that cannot be removed after a failed write does not raise."""
        s = filled(tmp_path)
        before = s._path.read_bytes()
        s.purge_before(T0 + 600)

        def fail(fd):
            raise OSError("I/O error")

        def locked(self, missing_ok=False):
            raise PermissionError("locked")

        monkeypatch.setattr(os, "fsync", fail)
        monkeypatch.setattr(type(s._path), "unlink", locked)
        s.save()  # no exception
        monkeypatch.undo()
        assert s._path.read_bytes() == before and s.dirty
        s.save()
        assert s._path.read_bytes() == full_write(s, tmp_path / "ref")
        assert not s._path.with_suffix(".tmp").exists()

    @pytest.mark.parametrize(
        "odd, kept",
        [
            ((T0 + 60 * 12, 9999, 0, 0, 1), 12),  # a value out of range: left out
            ((T0 + 60 * 12, 1000, 0, 3, 1), 12),  # an unknown limit byte: not a plausible record, left out
            ((T0 + 5, 1000, 0, 0, 1), 13),  # an earlier time at the end
            ((T0 + 60 * 11, 1000, 0, 0, 1), 12),  # a repeated time
        ],
        ids=["out-of-range", "limit-byte", "unsorted", "repeated"],
    )
    def test_a_file_unlike_a_full_write_is_written_whole_next(self, tmp_path, odd, kept):
        """A loaded file that is not exactly what a full write of its readings gives is never appended to."""
        s = GlucoCraftStorage(str(tmp_path), "odd")
        recs = [(T0 + 60 * i, 1000 + i, 0, 0, 1) for i in range(12)] + [odd]
        s._path.write_bytes(header(60, version=STORAGE_VERSION) + b"".join(struct.pack("<IHHBB", *r) for r in recs))
        s.load()
        assert s.reading_count == kept and s._on_disk == -1
        s.insert_reading(T0 + 60 * 20, 140, None, 1)
        s.save()
        assert s._path.read_bytes() == full_write(s, tmp_path / "ref")

    def test_a_clean_file_is_exactly_a_full_write(self, tmp_path):
        """The load's check (whole records, none left out, valid limit bytes, the reserved field 0, increasing times)
        means the file is what a full write of the loaded readings gives: every stored uint16 reads back to the same
        bytes. A value in the reserved field makes the file not clean (written in full at the next save)."""
        assert all(storage_mod._x10(k / 10.0) == k for k in range(65536))
        s = GlucoCraftStorage(str(tmp_path), "clean")
        recs = [(T0 + 60 * i, 200 + 157 * i, 0, i % 3, i % 3) for i in range(30)]
        body = b"".join(struct.pack("<IHHBB", *r) for r in recs)
        s._path.write_bytes(header(60, version=STORAGE_VERSION) + body)
        s.load()
        assert s._on_disk == 30 and storage_mod._pack(s.readings) == body
        recs[29] = (*recs[29][:2], 1, *recs[29][3:])
        s._path.write_bytes(header(60, version=STORAGE_VERSION) + b"".join(struct.pack("<IHHBB", *r) for r in recs))
        s.load()
        assert s.reading_count == 30 and s._on_disk == -1


class TestRecordPlausibility:
    """Records that are no plausible reading (a time from 2015 on and at most a year after the clock, 20-500 mg/dL,
    a valid limit and source code) are left out; a file with a valid header from which more than the records a loss of
    power can leave unwritten were left out is recovered: a copy is kept, the problem is "recovered" and the readings
    that could be read are written again in full."""

    @staticmethod
    def _file(tmp_path, count: int = 100):
        s = filled(tmp_path, count=count)
        return s, s._path.read_bytes()

    def test_zeroed_records_in_the_middle(self, tmp_path):
        s, data = self._file(tmp_path)
        start = STORAGE_HEADER_SIZE + 40 * STORAGE_READING_SIZE
        damaged = data[:start] + bytes(20 * STORAGE_READING_SIZE) + data[start + 20 * STORAGE_READING_SIZE :]
        s._path.write_bytes(damaged)
        back = reopen(s)
        assert back.reading_count == 80 and back.problem == "recovered" and back.dirty
        assert back.kept_file and (tmp_path / back.kept_file).read_bytes() == damaged  # an exact copy
        back.save()
        assert back._path.read_bytes() == full_write(back, tmp_path / "ref")  # the 80 readings, written in full
        assert reopen(back).problem is None

    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_a_garbage_tail(self, tmp_path, seed):
        s, data = self._file(tmp_path)
        s._path.write_bytes(data + random.Random(seed).randbytes(512))
        back = reopen(s)
        assert back.problem == "recovered" and back.kept_file
        latest = int(time.time()) + 366 * 86400
        for r in back.readings:  # no reading outside the plausibility rule
            assert storage_mod.EARLIEST_PLAUSIBLE_TS <= r.timestamp <= latest and 20 <= r.raw <= 500
            assert r.limit in (None, "low", "high") and r.source in (0, 1, 2)
        assert [r.timestamp for r in back.readings[:100]] == [r.timestamp for r in s.readings]

    @pytest.mark.parametrize(
        "record",
        [
            (1_000, 1200, 0, 0, 1),  # a time before 2015
            (2_000_000_000 + 400 * 86400, 1200, 0, 0, 1),  # more than a year after the clock
            (T0 + 60 * 50, 1200, 0, 7, 1),  # an unknown limit code
            (T0 + 60 * 50, 1200, 0, 0, 9),  # an unknown source code
            (T0 + 60 * 50, 199, 0, 0, 1),  # below 20 mg/dL
        ],
        ids=["before-2015", "after-clock", "limit-code", "source-code", "value"],
    )
    def test_each_rule_leaves_a_record_out(self, tmp_path, record, monkeypatch):
        s, data = self._file(tmp_path, count=30)
        monkeypatch.setattr(storage_mod.time, "time", lambda: 2_000_000_000)
        bad = struct.pack("<IHHBB", *record)
        cut = STORAGE_HEADER_SIZE + 10 * STORAGE_READING_SIZE
        s._path.write_bytes(data[:cut] + bad + data[cut:])
        back = reopen(s)
        assert back.reading_count == 30 and back.problem == "recovered"

    def test_a_clock_before_2015_sets_no_upper_bound(self, tmp_path, monkeypatch):
        """A host started without its time (the clock in 1970): the stored readings stay plausible."""
        s, _ = self._file(tmp_path, count=30)
        monkeypatch.setattr(storage_mod.time, "time", lambda: 86400)
        back = reopen(s)
        assert back.reading_count == 30 and back.problem is None and back._on_disk == 30

    def test_nothing_readable_is_kept_aside_as_damaged(self, tmp_path):
        s, data = self._file(tmp_path, count=30)
        s._path.write_bytes(data[:STORAGE_HEADER_SIZE] + bytes(30 * STORAGE_READING_SIZE) + b"\xff" * 10)
        back = reopen(s)
        assert back.problem == "damaged" and back.readings == [] and back.kept_file and not back._path.exists()

    def test_a_zeroed_or_cut_end_is_no_damage(self, tmp_path):
        """What a loss of power during an append leaves at the end (zeroed records, the last record) is left out
        without a notice: those readings were never saved."""
        s, data = self._file(tmp_path, count=30)
        for tail in (bytes(3 * STORAGE_READING_SIZE), b"\x07" * 10, b"\x07" * 4):
            s._path.write_bytes(data + tail)
            back = reopen(s)
            assert back.reading_count == 30 and back.problem is None and back._on_disk == -1, tail


class TestSaveError:
    """A save that fails sets save_error (the coordinator's notice and the Data history sensor read it), logs once per
    episode, and the next save that succeeds clears it."""

    @staticmethod
    def _lines(caplog, level):
        return [r.getMessage() for r in caplog.records if r.name == storage_mod._LOGGER.name and r.levelname == level]

    def test_a_failing_append_and_whole_write(self, tmp_path, monkeypatch, caplog):
        caplog.set_level("INFO", logger=storage_mod._LOGGER.name)
        s = filled(tmp_path)

        def full(fd):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(os, "fsync", full)
        for k in range(3):  # an append, then whole writes: one log line for the episode
            s.insert_reading(T0 + 60 * (40 + k), 130, None, 1)
            s.save()
            assert s.save_error == "No space left on device" and s.dirty
        assert len(self._lines(caplog, "ERROR")) == 1
        monkeypatch.undo()
        caplog.clear()
        s.save()
        assert s.save_error is None and not s.dirty and self._lines(caplog, "ERROR") == []
        assert self._lines(caplog, "INFO") == ["GlucoCraft storage is written again"]
        assert reopen(s).reading_count == 33

    def test_a_file_that_cannot_be_written(self, tmp_path):
        """A real failure, no stand-in: the temporary file of the full write cannot be created (a folder holds its
        name), as on a full or read-only storage."""
        s = filled(tmp_path)
        s.purge_before(T0 + 600)  # a change written in full
        blocker = s._path.with_suffix(".tmp")
        blocker.mkdir()
        s.save()
        assert s.save_error and s.dirty and reopen(s).reading_count == 30  # the saved readings are untouched
        blocker.rmdir()
        s.save()
        assert s.save_error is None and reopen(s).reading_count == 20

    @pytest.mark.skipif(os.name == "posix" and os.geteuid() == 0, reason="the superuser can write a read-only file")
    def test_a_read_only_file(self, tmp_path):
        s = filled(tmp_path)
        os.chmod(s._path, 0o444)
        try:
            s.insert_reading(T0 + 60 * 40, 130, None, 1)
            s.save()
            assert s.save_error and s.dirty
        finally:
            os.chmod(s._path, 0o644)
        s.save()
        assert s.save_error is None and reopen(s).reading_count == 31


class TestFillAfter:
    def test_rows_placed_by_time_and_stored_readings_kept(self, tmp_path):
        s = filled(tmp_path, count=12, step=300)
        before = [(r.timestamp, r.raw, r.limit, r.source) for r in s.readings]
        newest = s.newest_timestamp
        s.insert_reading(newest + 3300, 150, None, 1)  # a live reading stored after the gap
        rows = [(T0 + 300, 99.0, None)]  # before `after`: not taken
        rows += [(newest + 300 * k, 120.0 + k, None) for k in range(1, 11)]  # the gap
        rows += [(newest + 3300 + 10, 151.0, None)]  # the live reading's own measurement (within 30 s): kept as stored
        assert s.fill_after(rows, newest) == 10
        assert [(r.timestamp, r.raw, r.limit, r.source) for r in s.readings[:12]] == before
        assert [r.timestamp for r in s.readings[12:]] == [newest + 300 * k for k in range(1, 12)]
        assert s.readings[-1].raw == 150
        s.save()
        assert s._path.read_bytes() == full_write(s, tmp_path / "ref")
        assert s.fill_after(rows, newest) == 0  # again: nothing new


class TestSavePerReading:
    @pytest.mark.parametrize("step", [60, 300])
    def test_one_fsync_per_stored_reading(self, tmp_path, monkeypatch, step):
        """A stored reading is saved at once: one append, one fsync, for a 1-minute and a 5-minute source."""
        c = real_coordinator(tmp_path)
        now = int(time.time())
        assert c._process_reading(120.0, now - 30 * step, "sensor.p", "P", True) == 2  # the first save: in full
        syncs = []
        real = os.fsync
        monkeypatch.setattr(os, "fsync", lambda fd: (syncs.append(fd), real(fd)))
        for k in range(1, 21):
            assert c._process_reading(120.0 + k % 5, now - (30 - k) * step, "sensor.p", "P", True) == 2
            assert len(syncs) == k and not c.storage.dirty
        assert reopen(c.storage).reading_count == 21


class TestReadingObjects:
    def test_readings_have_slots(self):
        """About 40 bytes less per reading (no __dict__)."""
        r = GlucoseReading(T0, 100.0)
        assert not hasattr(r, "__dict__")
        r.raw = 99.5  # the declared fields stay writable
        with pytest.raises(AttributeError):
            r.other = 1

    def test_interval_cache_recomputes_only_the_changed_part(self, tmp_path, monkeypatch):
        """After a new reading only its interval is computed (from a window of the readings before it)."""
        s = filled(tmp_path, count=200)
        s.reading_intervals(300)
        seen: list[int] = []
        real = gaps.local_intervals

        def spy(ts, fallback, start=0, **kw):
            seen.append(len(ts) - start)
            return real(ts, fallback, start=start, **kw)

        monkeypatch.setattr("glucocraft.storage.local_intervals", spy)
        s.insert_reading(T0 + 60 * 200, 120, None, 1)
        assert s.reading_intervals(300) == real([r.timestamp for r in s.readings], 300) and seen == [1]
        s.reading_intervals(300)
        assert seen == [1]  # nothing changed: nothing computed
