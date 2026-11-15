# License: AGPL-3.0-only (LICENSE).
"""A clock more than a year behind the stored readings (storage._latest_plausible).

Rules under test: no reading is ever dropped because of the clock alone. Readings later than a year after the clock
that end the file in time order are a clock that is behind, not damage: they load, the file is not kept aside as a
copy, nothing is marked for a full write, and the coordinator's clock rule (clock_behind) applies. Damage is still
found while the clock is behind (zeroed records among the readings, a damaged header), and a single record far in the
future among the readings stays damage when the clock is right.
"""

from __future__ import annotations

import os
import struct

from glucocraft import storage as storage_mod
from glucocraft.const import STORAGE_HEADER_SIZE, STORAGE_MAGIC, STORAGE_READING_SIZE, STORAGE_VERSION
from glucocraft.storage import GlucoCraftStorage

from conftest import real_coordinator

T_READINGS = 1_790_000_000  # the readings' own time (2026)
TWO_YEARS = 2 * 365 * 86400


def _header() -> bytes:
    h = bytearray(STORAGE_HEADER_SIZE)
    h[:4] = STORAGE_MAGIC
    h[4] = STORAGE_VERSION
    struct.pack_into("<H", h, 6, 300)
    return bytes(h)


def _records(count: int, start: int = T_READINGS, step: int = 300) -> bytes:
    """`count` version 3 records, one every `step` seconds from `start`, measured, from the primary source."""
    return b"".join(struct.pack("<IHHBB", start + i * step, 1000 + i % 50, 0, 0, 1) for i in range(count))


def _kept(folder) -> list[str]:
    return [p for p in os.listdir(folder) if ".corrupt-" in p]


def _clock(monkeypatch, now: int) -> None:
    monkeypatch.setattr(storage_mod.time, "time", lambda: now)


def test_readings_two_years_after_the_clock_are_kept(tmp_path, monkeypatch):
    """The clock two years back: every reading loads, no copy is kept, no problem is reported, and the file can still
    be appended to; after a save and a reload they are all there."""
    _clock(monkeypatch, T_READINGS - TWO_YEARS)
    s = GlucoCraftStorage(str(tmp_path), "clock")
    s._path.write_bytes(_header() + _records(100))
    s.load()
    assert s.reading_count == 100 and s.problem is None and s.kept_file is None and not s.dirty
    assert s._on_disk == 100  # a clean file: the next new reading is appended
    assert _kept(tmp_path) == []
    s.save()
    again = GlucoCraftStorage(str(tmp_path), "clock")
    again.load()
    assert again.reading_count == 100 and again.problem is None
    assert again.newest_timestamp == T_READINGS + 99 * 300


def test_damage_is_still_found_while_the_clock_is_behind(tmp_path, monkeypatch):
    """Zeroed records among readings two years after the clock: the readings that could be read are recovered (none
    of the others is counted as damage) and the file is kept as a copy."""
    _clock(monkeypatch, T_READINGS - TWO_YEARS)
    s = GlucoCraftStorage(str(tmp_path), "dmg")
    body = _records(40) + bytes(5 * STORAGE_READING_SIZE) + _records(55, start=T_READINGS + 45 * 300)
    s._path.write_bytes(_header() + body)
    s.load()
    assert s.reading_count == 95 and s.problem == "recovered" and len(_kept(tmp_path)) == 1


def test_a_damaged_header_is_recovered_while_the_clock_is_behind(tmp_path, monkeypatch):
    """A damaged header before readings two years after the clock: the readings are recovered, not kept aside."""
    _clock(monkeypatch, T_READINGS - TWO_YEARS)
    s = GlucoCraftStorage(str(tmp_path), "hdr")
    s._path.write_bytes(b"XXXX" + bytes(12) + _records(40))
    s.load()
    assert s.reading_count == 40 and s.problem == "recovered"


def test_a_few_records_far_in_the_future_stay_damage(tmp_path, monkeypatch):
    """With the clock right, fewer than three records more than a year after it at the end of the file, or one among
    the readings, are damage (overwritten bytes), not a clock that is behind."""
    _clock(monkeypatch, T_READINGS + 100 * 300)
    far = T_READINGS + TWO_YEARS
    for name, body in (
        ("end", _records(30) + _records(2, start=far)),
        ("middle", _records(10) + _records(1, start=far) + _records(20, start=T_READINGS + 10 * 300)),
        ("order", _records(30) + _records(1, start=far + 600) + _records(3, start=far)),
    ):
        s = GlucoCraftStorage(str(tmp_path / name), name)
        os.makedirs(s._dir)
        s._path.write_bytes(_header() + body)
        s.load()
        assert s.problem == "recovered", name
        assert all(r.timestamp < far for r in s.readings), name


def test_the_coordinator_reports_the_clock(tmp_path, monkeypatch):
    """The readings kept are later than the clock, so the coordinator's clock rule applies (no new reading stored,
    nothing purged, the clock_behind notice)."""
    now = T_READINGS - TWO_YEARS
    _clock(monkeypatch, now)
    (tmp_path / "glucocraft_t.bin").write_bytes(_header() + _records(50))
    c = real_coordinator(tmp_path)
    assert c.storage.reading_count == 50 and c.storage.problem is None
    assert c.clock_behind(now)
