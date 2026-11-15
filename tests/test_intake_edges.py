# License: AGPL-3.0-only (LICENSE).
"""The edges of the reading intake: measurement times, the live 24-hour rule, the log of refused readings, the readings
file of a newer version, the report cache key, the reading lock and the boundaries of the calculation rules.

Rules under test:
- A measurement time that does not exist ("2026-02-30T10:00:00", a 61st second, month 13, an offset beyond 24 hours)
  is no measurement time on the live, setup, report and import paths; it never raises (Home Assistant's parser raises
  ValueError for such texts).
- The history import parses the recorded states in recording order, each with the newest measurement time before it,
  as the live path does: both occurrences of the repeated hour of a daylight-saving fall-back keep their times, also
  across the day parts of the recorder read and with a state recorded twice.
- A live reading is refused only when it was measured more than 24 hours before the system clock and before the newest
  stored reading: a clock far ahead never refuses the source's correctly timed readings. Exactly 24 hours is kept.
- A refused reading, a time attribute without a usable time and a state that is no reading are logged once per source
  and kind (INFO, the source's role, never its entity id), again only after the source's next good reading of the kind.
- A reading measured before the warm-up started does not end it, not even one second before.
- A readings file of a newer format version is neither read nor written; a Repairs notice says so.
- The CGM Report's cache key changes when a reading up to the report's last reading is replaced, inserted or removed,
  not when a new reading follows it.
- The statistics pass and the CSV export hold the reading lock only to copy the store; a pass from an older copy than
  the last one computed is not assigned.
- Boundaries: the clock margin (6 minutes ahead is "clock behind", 4 is not), the 5-minute window of the 5-minute
  change, the rounding rule's float allowance, the local interval's upper middle and its minimum of 3 intervals, the
  median's minimum, the 10-interval window.
- No log line of the package names a source entity id or a notify action's error text.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import threading
import time
import types
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from glucocraft import coordinator as co
from glucocraft import gaps
from glucocraft import history_import as hi
from glucocraft import source_notices as sn
from glucocraft.const import INTERVAL_WINDOW, STORAGE_VERSION
from glucocraft.report_service import GlucoCraftReports
from glucocraft.storage import GlucoCraftStorage
from glucocraft.units import round_half_up

from conftest import REPO, add_reading
from test_data_path import source_coordinator, state

PKG = REPO / "custom_components" / "glucocraft"
DAY = 86400
IMPOSSIBLE = ("2026-11-01T23:59:60", "2026-02-30T10:00:00", "2026-13-01T10:00:00", "2026-11-01T10:00:00+25:00")


class FakeIssues:
    """Records the issue-registry calls (homeassistant.helpers.issue_registry)."""

    IssueSeverity = types.SimpleNamespace(WARNING="warning", ERROR="error")

    def __init__(self):
        self.open: dict[str, dict] = {}

    def async_create_issue(self, hass, domain, issue_id, **kw):
        self.open[issue_id] = kw

    def async_delete_issue(self, hass, domain, issue_id):
        self.open.pop(issue_id, None)

    def async_get(self, hass):
        return self


@pytest.fixture
def issues(monkeypatch):
    fake = FakeIssues()
    monkeypatch.setattr(co, "ir", fake)
    monkeypatch.setattr(sn, "ir", fake)  # the timer's source notices (source_notices.py)
    return fake


@pytest.fixture
def strict_parser(monkeypatch):
    """Home Assistant's parser: a well-formed text of a time that does not exist raises ValueError."""

    def parse(text):
        return datetime.fromisoformat(text) if len(text) > 10 else None

    monkeypatch.setattr(co.dt_util, "parse_datetime", parse)


@pytest.fixture
def clock(monkeypatch):
    """Set the system clock (epoch s) the coordinator reads."""

    def set_to(now: int) -> int:
        monkeypatch.setattr(co.time, "time", lambda: float(now))
        return now

    return set_to


def fresh(tmp_path: Path, name: str, **kw):
    """A coordinator with its own empty readings file (sensor.p in use)."""
    folder = tmp_path / name
    folder.mkdir()
    return source_coordinator(folder, **kw)


def refusal_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name.startswith("glucocraft") and r.levelno == logging.INFO]


# ------------------------------------------------------------------------------------------------ impossible times
class TestImpossibleTimes:
    @pytest.mark.parametrize("text", IMPOSSIBLE)
    def test_the_parser_raises_and_the_reading_has_no_measurement_time(self, strict_parser, text):
        with pytest.raises(ValueError):
            co.dt_util.parse_datetime(text)
        assert co._parse_reading_time(text) is None
        assert co._parse_reading_time(text, after=1_790_000_000) is None

    @pytest.mark.parametrize("error", [ValueError, OverflowError])
    def test_an_error_of_the_conversion_is_no_measurement_time(self, monkeypatch, error):
        def parse(text):
            raise error("out of range")

        monkeypatch.setattr(co.dt_util, "parse_datetime", parse)
        assert co._parse_reading_time("2026-11-01T10:00:00") is None

    @pytest.mark.parametrize("text", IMPOSSIBLE)
    def test_live_setup_and_report_paths_take_the_arrival_time(self, tmp_path, strict_parser, clock, text):
        now = clock(1_793_000_000)
        c = source_coordinator(tmp_path)
        c._time_attr["sensor.p"] = "measured"
        st = state("120", measured=text)
        assert c._reading_time("sensor.p", st) == (now, False)  # the live and the setup path
        c.hass.states.d["sensor.p"] = st
        assert c.has_measurement_time("sensor.p") is False  # the report's question

    @pytest.mark.parametrize("text", IMPOSSIBLE)
    def test_the_history_import_uses_the_recording_time(self, strict_parser, text):
        recorded = 1_793_000_000
        st = types.SimpleNamespace(
            state="120", attributes={"when": text}, last_updated=datetime.fromtimestamp(recorded, UTC)
        )
        no_time = [0]
        rows = hi.rows_from_states([st], False, True, {}, "when", co._parse_reading_time, no_time=no_time)
        assert rows == [(recorded, 120.0, None)] and no_time == [1]


# ------------------------------------------------------------------------------------------------ the fall-back hour
FALL_BACK = {
    "America/Los_Angeles": (2025, 11, 2),
    "Europe/Berlin": (2025, 10, 26),
    "Australia/Adelaide": (2026, 4, 5),
}


def _fall_back_series(zone: str) -> tuple[ZoneInfo, list[int], list[str]]:
    """60 five-minute readings from local midnight of the fall-back date: true times and naive local texts."""
    tz = ZoneInfo(zone)
    start = int(datetime(*FALL_BACK[zone], 0, 0, tzinfo=tz).timestamp())
    true_ts = [start + 37 + 300 * k for k in range(60)]
    naive = [datetime.fromtimestamp(t, tz).replace(tzinfo=None).isoformat() for t in true_ts]
    return tz, true_ts, naive


def _recorded(text: str, recorded: int):
    return types.SimpleNamespace(
        state="120",
        attributes={"measurement_timestamp": text},
        last_updated=datetime.fromtimestamp(recorded, UTC),
        last_changed=datetime.fromtimestamp(recorded, UTC),
    )


class TestFallBackImport:
    @pytest.mark.parametrize("zone", list(FALL_BACK))
    def test_every_reading_of_the_repeated_hour_is_kept_as_live(self, monkeypatch, zone):
        tz, true_ts, naive = _fall_back_series(zone)
        monkeypatch.setattr(co.dt_util, "DEFAULT_TIME_ZONE", tz)
        # the live path: each reading with the source's newest measurement time before it
        live, after = [], None
        for text in naive:
            ts = co._parse_reading_time(text, after)
            live.append(ts)
            after = ts if after is None else max(after, ts)
        rows = hi.rows_from_states(
            [_recorded(s, t + 20) for s, t in zip(naive, true_ts, strict=True)],
            False,
            True,
            {},
            "",
            co._parse_reading_time,
            true_ts[-1] + 120,
        )
        assert live == true_ts
        assert [r[0] for r in rows] == true_ts  # all 60, each at its own time

    @pytest.mark.parametrize("zone", list(FALL_BACK))
    def test_a_state_recorded_twice_keeps_its_time(self, monkeypatch, zone):
        """An attribute that changed without a new measurement records the same time twice, in either hour."""
        tz, true_ts, naive = _fall_back_series(zone)
        monkeypatch.setattr(co.dt_util, "DEFAULT_TIME_ZONE", tz)
        states = []
        for s, t in zip(naive, true_ts, strict=True):
            states += [_recorded(s, t + 20), _recorded(s, t + 40)]
        rows = hi.rows_from_states(states, False, True, {}, "", co._parse_reading_time, true_ts[-1] + 120)
        assert [r[0] for r in rows] == true_ts

    @pytest.mark.parametrize("zone", list(FALL_BACK))
    def test_the_day_parts_of_the_recorder_read_carry_the_newest_time(self, monkeypatch, zone):
        """Read in parts of 30 minutes (the repeated hour falls across parts, and a state at a part's boundary is
        read twice): the rows are those of one read."""
        tz, true_ts, naive = _fall_back_series(zone)
        monkeypatch.setattr(co.dt_util, "DEFAULT_TIME_ZONE", tz)
        monkeypatch.setattr(hi, "_FETCH_SECONDS", 1800)
        states = [_recorded(s, t + 20) for s, t in zip(naive, true_ts, strict=True)]
        now = true_ts[-1] + 120

        async def fetch(start, end):
            return [s for s in states if start <= int(s.last_updated.timestamp()) <= end]

        def to_rows(part, newest):
            return hi.rows_from_states(part, False, True, {}, "", co._parse_reading_time, now, newest)

        rows = asyncio.run(hi.async_rows_kept(fetch, to_rows, now, tz, 1))
        assert [r[0] for r in rows] == true_ts


# ------------------------------------------------------------------------------------------------ the 24-hour rule
class TestLiveLookback:
    """A reading is refused only when it is more than 24 hours older than the clock and the newest stored reading."""

    AGES = ((DAY - 60, True), (DAY - 1, True), (DAY, True), (DAY + 1, False), (DAY + 3600, False))

    @pytest.mark.parametrize("age, stored", AGES)
    def test_against_the_clock_with_nothing_stored(self, tmp_path, clock, age, stored):
        now = clock(1_793_000_000)
        c = fresh(tmp_path, "c")
        result = c._process_reading(120.0, now - age, None, "P", True)
        assert (result == co.ReadingResult.STORED) is stored

    @pytest.mark.parametrize("age, stored", AGES)
    def test_against_the_newest_stored_reading_with_the_clock_a_year_ahead(self, tmp_path, clock, age, stored):
        true_now = 1_793_000_000
        c = fresh(tmp_path, "c")
        add_reading(c.storage, true_now, 110.0)
        clock(true_now + 365 * DAY)
        result = c._process_reading(120.0, true_now - age, None, "P", True)
        assert (result == co.ReadingResult.STORED) is stored

    def test_a_clock_far_ahead_keeps_the_correctly_timed_readings(self, tmp_path, clock):
        true_now = 1_793_000_000
        c = fresh(tmp_path, "c")
        for k in range(12):
            add_reading(c.storage, true_now - 3600 + 300 * k, 110.0)
        c._src_last_ts["sensor.p"] = c.storage.newest_timestamp
        clock(true_now + 365 * DAY)
        assert c._process_reading(122.0, true_now, "sensor.p", "P", True) == co.ReadingResult.STORED
        assert c.storage.newest_timestamp == true_now

    def test_a_reading_newer_than_the_newest_stored_is_kept_after_an_outage(self, tmp_path, clock):
        now = clock(1_793_000_000)
        c = fresh(tmp_path, "c")
        add_reading(c.storage, now - 3 * DAY, 110.0)
        assert c._process_reading(120.0, now - DAY - 3600, None, "P", True) == co.ReadingResult.STORED
        assert c._process_reading(120.0, now - 4 * DAY - 3600, None, "P", True) == co.ReadingResult.UNCHANGED

    def test_the_refusal_is_logged_once_per_episode_by_role(self, tmp_path, clock, caplog):
        caplog.set_level(logging.INFO, logger="glucocraft")
        now = clock(1_793_000_000)
        c = fresh(tmp_path, "c", secondary="sensor.secret_name")
        add_reading(c.storage, now - 60, 110.0)
        for k in range(3):
            c._process_reading(120.0, now - 2 * DAY + k, "sensor.secret_name", "S", True)
        lines = [m for m in refusal_lines(caplog) if "24 hours" in m]
        assert len(lines) == 1 and "the second source" in lines[0] and "secret" not in lines[0]
        # the primary (no measurement-order check): refused twice, logged once; a reading within 24 hours ends the
        # episode, and the next refusal is logged again
        for ts in (now - 2 * DAY, now - 2 * DAY + 1, now - 30, now - 2 * DAY + 2):
            c._process_reading(120.0, ts, None, "P", True)
        lines = [m for m in refusal_lines(caplog) if "24 hours" in m]
        assert len(lines) == 3 and lines[1] == lines[2] and "the primary source" in lines[2]


# ------------------------------------------------------------------------------------------------ the other refusals
class TestRefusalLog:
    def test_a_state_that_is_no_reading_is_logged_once(self, tmp_path, caplog):
        caplog.set_level(logging.INFO, logger="glucocraft")
        c = source_coordinator(tmp_path)
        for text in ("???", "5", "???"):
            assert c._source_reading("sensor.p", state(text)) is None
        assert len([m for m in refusal_lines(caplog) if "no reading" in m]) == 1
        assert c._source_reading("sensor.p", state("120")) == (120.0, None)
        assert c._source_reading("sensor.p", state("???")) is None
        lines = [m for m in refusal_lines(caplog) if "no reading" in m]
        assert len(lines) == 2 and all("the primary source" in m and "sensor.p" not in m for m in lines)

    def test_a_configured_time_attribute_without_a_usable_time_is_logged_once(self, tmp_path, clock, caplog):
        caplog.set_level(logging.INFO, logger="glucocraft")
        clock(1_793_000_000)
        c = source_coordinator(tmp_path)
        c._time_attr["sensor.p"] = "measured"
        for value in (None, "inf", "2026-11-01", float("nan")):  # missing, infinite, a date only, NaN
            assert c._reading_time("sensor.p", state("120", measured=value))[1] is False
        assert len([m for m in refusal_lines(caplog) if "measurement-time attribute" in m]) == 1
        assert c._reading_time("sensor.p", state("120", measured=1_792_999_000)) == (1_792_999_000, True)
        c._reading_time("sensor.p", state("120"))
        assert len([m for m in refusal_lines(caplog) if "measurement-time attribute" in m]) == 2

    def test_a_source_without_a_time_attribute_is_not_logged(self, tmp_path, caplog):
        caplog.set_level(logging.INFO, logger="glucocraft")
        c = source_coordinator(tmp_path)
        assert c._reading_time("sensor.p", state("120"))[1] is False
        assert c._reading_time("sensor.p", state("120", measurement_timestamp="not a time"))[1] is False
        assert len([m for m in refusal_lines(caplog) if "measurement-time attribute" in m]) == 1

    def test_the_import_counts_the_states_without_a_usable_time(self, monkeypatch, caplog):
        caplog.set_level(logging.INFO, logger="glucocraft")
        recorded = int(time.time()) - 3600

        async def rows_kept(fetch, to_rows, now, tz, days):
            to_rows([_recorded("not a time", recorded), _recorded("2026-02-30T10:00:00", recorded + 300)], None)
            return []

        monkeypatch.setattr(hi, "async_rows_kept", rows_kept)
        source = types.SimpleNamespace(state="120", attributes={"unit_of_measurement": "mg/dL"})
        hass = types.SimpleNamespace(states=types.SimpleNamespace(get=lambda e: source))
        asyncio.run(hi.async_import_history(hass, "sensor.p", None, parse_time=co._parse_reading_time))
        assert any("2 recorded states" in m for m in refusal_lines(caplog))


# ------------------------------------------------------------------------------------------------ the warm-up
class TestWarmUpEnd:
    @pytest.mark.parametrize("offset, ends", [(-59, False), (-1, False), (0, False), (1, True)])
    def test_only_a_reading_measured_after_the_start_ends_it(self, tmp_path, clock, offset, ends):
        now = clock(1_793_000_000)
        c = source_coordinator(tmp_path)
        c.sensor_warming_up, c.warmup_started_at = True, now - 600
        c._process_reading(120.0, now - 600 + offset, None, "P", True)
        assert c.sensor_warming_up is not ends


# ------------------------------------------------------------------------------------------------ a newer file
class TestNewerFile:
    def _newer_file(self, tmp_path) -> GlucoCraftStorage:
        s = GlucoCraftStorage(str(tmp_path), "t")
        s.load()
        for k in range(20):
            s.insert_reading(1_793_000_000 + 300 * k, 110.0 + k, None, 1)
        s.save()
        data = bytearray(s.path.read_bytes())
        data[4] = STORAGE_VERSION + 1
        s.path.write_bytes(bytes(data))
        return s

    def test_it_is_neither_read_nor_written(self, tmp_path):
        original = self._newer_file(tmp_path).path.read_bytes()
        s = GlucoCraftStorage(str(tmp_path), "t")
        s.load()
        assert s.problem == "newer" and s.readings == [] and s.kept_file is None
        s.insert_reading(1_794_000_000, 120.0)
        s.save()
        assert s.path.read_bytes() == original
        assert [p.name for p in tmp_path.iterdir()] == [s.path.name]  # no copy, no temporary file

    def test_an_older_version_is_still_recovered(self, tmp_path):
        s = self._newer_file(tmp_path)
        data = bytearray(s.path.read_bytes())
        data[4] = STORAGE_VERSION - 1
        s.path.write_bytes(bytes(data))
        again = GlucoCraftStorage(str(tmp_path), "t")
        again.load()
        assert again.problem == "recovered" and len(again.readings) == 20

    def test_the_repairs_notice_has_its_own_text(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        c.storage.problem, c.storage.kept_file = "newer", None
        c._check_storage_problem()
        assert issues.open[co.STORAGE_ISSUE_ID]["translation_key"] == "storage_newer"
        texts = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))["issues"]["storage_newer"]
        assert "{folder}" in texts["description"] and texts["title"]


# ------------------------------------------------------------------------------------------------ the report cache
class TestReportCacheKey:
    def test_a_replaced_inserted_or_removed_reading_changes_the_key(self, storage):
        for k in range(10):
            storage.insert_reading(1_793_000_000 + 300 * k, 110.0, None, 2)
        until = storage.readings[7].timestamp

        def key():
            return GlucoCraftReports._readings_key(storage.readings, until, storage.edits)

        before = key()
        storage.insert_reading(1_793_000_000 + 300 * 10, 111.0, None, 1)  # a new reading after the newest
        assert key() == before
        storage.replace_at(3, storage.readings[3].timestamp, 125.0, None, 1)  # the primary's copy of a measurement
        replaced = key()
        assert replaced != before
        storage.insert_reading(1_793_000_000 + 150, 112.0, None, 2)  # a late fill
        assert key() != replaced
        filled = key()
        storage.purge_before(1_793_000_000 + 1)
        assert key() != filled

    def test_appends_do_not_count_as_edits(self, storage):
        for k in range(5):
            storage.insert_reading(1_793_000_000 + 300 * k, 110.0)
        storage.merge_history([(1_793_000_000 + 300 * k, 110.0) for k in range(5, 8)])
        assert storage.edits == 0
        storage.insert_reading(1_793_000_000 + 300 * 7, 112.0)  # the same time again: replaced
        assert storage.edits == 1


# ------------------------------------------------------------------------------------------------ the reading lock
class TestReadingLock:
    def _filled(self, tmp_path, clock):
        now = clock(1_793_000_000)
        c = source_coordinator(tmp_path)
        for k in range(2000):
            add_reading(c.storage, now - 300 * (2000 - k), 100.0 + k % 90, None, 1)
        c.detected_interval_seconds = 300
        return c, now

    def test_the_statistics_are_computed_without_the_lock(self, tmp_path, clock, monkeypatch):
        c, now = self._filled(tmp_path, clock)
        held = []
        real = co.compute_period_stats_sets

        def spy(*args, **kw):
            held.append(c._lock.locked())
            return real(*args, **kw)

        monkeypatch.setattr(co, "compute_period_stats_sets", spy)
        c._stats_pass(now)
        assert held and not any(held)
        outside = dict(c.period_stats)
        c.change = {}
        c.locked(c._compute_all_stats, now)
        assert c.period_stats == outside and c.weekly_trends

    def test_a_pass_from_an_older_copy_is_not_assigned(self, tmp_path, clock):
        c, now = self._filled(tmp_path, clock)
        old = c.locked(c._stats_copy)
        add_reading(c.storage, now, 250.0, None, 1)
        new = c.locked(c._stats_copy)
        c._compute_all_stats(now, new)
        computed = c.period_stats
        c._compute_all_stats(now, old)
        assert c.period_stats is computed

    def test_the_export_is_written_without_the_lock(self, tmp_path, monkeypatch):
        from test_entity_ids import _load_init
        from test_entity_units_and_recorder import _action_hass

        init = _load_init()
        lock = threading.Lock()
        seen = []

        def locked(fn):
            with lock:
                return fn()

        def write_export(folder, readings, unit, tz, lab):
            seen.append((lock.locked(), len(readings)))
            return {"files": ["x.csv"], "readings": len(readings)}

        coordinator = types.SimpleNamespace(
            reports=types.SimpleNamespace(lab_results=[]),
            display_unit="mg/dL",
            storage=types.SimpleNamespace(readings=[1, 2, 3]),
            locked=locked,
            maintenance=types.SimpleNamespace(export_failed=lambda r: None, export_succeeded=lambda: None),
        )
        monkeypatch.setattr(init, "_coordinator_for", lambda hass: coordinator)
        monkeypatch.setattr(init, "write_export", write_export)
        monkeypatch.setattr(init, "export_message", lambda result: "saved")
        asyncio.run(init._handle_export(_action_hass(tmp_path), types.SimpleNamespace(data={})))
        assert seen == [(False, 3)]


# ------------------------------------------------------------------------------------------------ boundaries
class TestBoundaries:
    @pytest.mark.parametrize("ahead, behind", [(240, False), (300, False), (301, True), (360, True)])
    def test_the_clock_margin(self, tmp_path, clock, issues, ahead, behind):
        """A stored reading 6 minutes ahead of the clock means the clock is behind; 4 minutes does not (5 minutes,
        CLOCK_MARGIN_SECONDS)."""
        now = clock(1_793_000_000)
        c = source_coordinator(tmp_path)
        add_reading(c.storage, now + ahead, 120.0)
        assert c.clock_behind(now) is behind
        c._check_clock(now)
        assert (co.CLOCK_ISSUE_ID in issues.open) is behind

    def test_the_5_minute_change_compares_with_the_level_5_minutes_before(self, tmp_path):
        """1-minute readings rising 1 mg/dL per minute: the smoothed level changes by exactly 5 mg/dL in 5 minutes."""
        c = source_coordinator(tmp_path)
        base = 1_793_000_000
        for k in range(41):
            add_reading(c.storage, base + 60 * k, 100.0 + k)
        last = base + 60 * 40
        assert c._adaptive(last)[2] == 5.0
        c._apply_adaptive(last)
        c.no_current_data = False
        assert c.change_5min == 5.0

    @pytest.mark.parametrize("value, decimals, shown", [(1.005, 2, 1.01), (0.285, 2, 0.29), (1.0049, 2, 1.0)])
    def test_the_rounding_rule_rounds_an_exact_half_written_in_binary_up(self, value, decimals, shown):
        assert round_half_up(value, decimals) == shown

    def test_the_local_interval_is_the_upper_middle(self):
        ts = [0, 60, 120, 420, 720, 780]  # intervals 60, 60, 300, 300, 60
        # at the reading 720: the intervals before it (60, 60, 300, 300) and after the next (none): upper middle 300
        assert gaps.local_intervals(ts, 999)[4] == 300

    def test_the_local_interval_needs_3_intervals(self):
        assert gaps.local_intervals([0, 60, 120, 180, 240], 999)[0] == 60  # 3 intervals after the next one
        assert gaps.local_intervals([0, 60, 120, 180], 999)[0] == 999  # 2: the fallback

    def test_the_median_interval_needs_its_minimum(self):
        assert gaps.median_interval([0, 60, 120, 180]) == 60  # exactly 3 intervals
        assert gaps.median_interval([0, 60, 120]) is None

    def test_the_interval_window_is_10_intervals(self):
        assert INTERVAL_WINDOW == 10
        ts = [0]
        for d in [1000, *[60] * 5, *[300] * 4]:  # the 10 intervals before the reading 10
            ts.append(ts[-1] + d)
        ts.append(ts[-1] + 60)
        # all 10: 60 x5, 300 x4, 1000 -> upper middle 300; a window of 9 would leave out the 1000 (60)
        assert gaps.local_intervals(ts, 999)[10] == 300
        times = [0, 1000, *[1000 + 60 * k for k in range(1, 6)], *[1300 + 300 * k for k in range(1, 5)]]
        assert gaps.median_interval(times) == 300  # the newest 10 intervals, the 1000 among them


# ------------------------------------------------------------------------------------------------ the log
ENTITY_NAMES = {"entity_id", "entity", "src", "source_entity"}
ENTITY_ATTRIBUTES = {"entity_id", "_glucose_entity", "_secondary_entity", "active_source_entity", "_expiry_entity"}


def _logger_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "_LOGGER"
        ):
            yield node


def _names_an_entity(arg: ast.AST) -> bool:
    """True when the argument holds a source entity id (anything inside self._role(...) is the role, not the id)."""
    stack = [arg]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_role":
            continue
        if isinstance(node, ast.Name) and node.id in ENTITY_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in ENTITY_ATTRIBUTES:
            return True
        stack.extend(ast.iter_child_nodes(node))
    return False


def _calls_notify(nodes) -> bool:
    for node in nodes:
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "async_call"
                and sub.args
                and isinstance(sub.args[0], ast.Constant)
                and sub.args[0].value == "notify"
            ):
                return True
    return False


def test_no_log_line_names_a_source_entity_id_or_a_notify_error_text():
    found, scanned = [], 0
    for path in sorted(PKG.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _logger_calls(tree):
            scanned += 1
            if any(_names_an_entity(a) for a in call.args[1:]):
                found.append(f"{path.name}:{call.lineno} entity id")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try) or not _calls_notify(node.body):
                continue
            for handler in node.handlers:
                for sub in handler.body:
                    for call in _logger_calls(sub):
                        if any(isinstance(a, ast.Name) and a.id == handler.name for a in call.args[1:]):
                            found.append(f"{path.name}:{call.lineno} notify error text")
    assert not found, found
    assert scanned > 50  # the scan sees the package's log calls


def test_the_debug_lines_name_the_role(tmp_path, caplog, issues):
    caplog.set_level(logging.DEBUG, logger="glucocraft")
    c = source_coordinator(tmp_path, secondary="sensor.secret_name")
    add_reading(c.storage, int(time.time()) - 60, 120.0)
    c._restore_newest({"active_source_entity": "sensor.secret_name"})
    c._learn_limits("sensor.secret_name", types.SimpleNamespace(learned_lower=None, learned_upper=400))
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "secret" not in text and "the second source" in text
