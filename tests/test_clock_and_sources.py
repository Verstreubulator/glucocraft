# License: AGPL-3.0-only (LICENSE).
"""The system clock, GlucoCraft's own entities as sources, the history import and the storage notices.

Rules under test:
- A wrong system clock never deletes stored readings. While the clock is earlier than the newest stored reading, no
  reading is stored, nothing is purged, the status is "no current data" and a fixable Repairs notice explains it; only
  the user's confirmation discards the later readings. A clock that jumps ahead cannot purge more than the retention
  allows relative to the stored data. Three consecutive live readings measured more than an hour before the clock
  open the Repairs warning that the clock may be ahead; the first reading within the hour closes it.
- A GlucoCraft entity is never a source: the settings exclude and reject it, and the coordinator ignores it with a
  Repairs notice.
- The history import reads every recorded state with its attributes (also a repeated value), takes each reading's
  measurement time as the live path does and keeps one row per measurement time; it decides LO / HI as the live path
  does, with a reading at a limit set by hand LO / HI.
- A damaged or unreadable readings file is reported in Repairs; the "source without a unit" notice closes once the
  source reports a unit.
- A readings file that cannot be written is a Repairs notice and a Data history attribute until a save succeeds; a
  source that states a unit other than mg/dL or mmol/L gives no reading, with a notice; a configured source that does
  not exist is a notice once Home Assistant has started.
- After a clock-behind episode (and after the readings later than the clock were discarded), the readings refused
  meanwhile are imported from the recorder once, placed by their measurement times; no stored reading changes.
- A reading that gets the lock after the shutdown has begun is not stored; with one source, the same-measurement
  window is 15 s, so a faster source after a slower history loses no reading.
- The log of the source in use (Home Assistant's log-when-unavailable rule): one warning when it becomes unavailable or
  sends no reading for longer than the no-current-data limit, one info line when it delivers current readings again,
  nothing repeated while either state lasts, nothing before Home Assistant has started, and never the entity id.
"""

from __future__ import annotations

import logging
import time
import types
from datetime import UTC, datetime

import pytest
from glucocraft import const as C
from glucocraft import coordinator as co
from glucocraft import history_import as hi
from glucocraft import limits as L
from glucocraft import source_notices as sn

from conftest import add_reading
from test_data_path import source_coordinator, state


class FakeIssues:
    """Records the issue-registry calls (homeassistant.helpers.issue_registry)."""

    IssueSeverity = types.SimpleNamespace(WARNING="warning", ERROR="error")

    def __init__(self):
        self.open: dict[str, dict] = {}
        self.issues = {}

    def async_create_issue(self, hass, domain, issue_id, **kw):
        """Record an open Repairs issue."""
        self.open[issue_id] = kw
        self.issues[(domain, issue_id)] = kw

    def async_delete_issue(self, hass, domain, issue_id):
        """Close a Repairs issue."""
        self.open.pop(issue_id, None)
        self.issues.pop((domain, issue_id), None)

    def async_get(self, hass):
        """The issue registry (this stand-in itself)."""
        return self


@pytest.fixture
def issues(monkeypatch):
    fake = FakeIssues()
    monkeypatch.setattr(co, "ir", fake)
    monkeypatch.setattr(sn, "ir", fake)  # the timer's source notices (source_notices.py)
    return fake


def _with_readings(c, start: int, count: int, step: int = 300, value: float = 120.0):
    for i in range(count):
        add_reading(c.storage, start + i * step, value)
    c.last_reading_ts = c.storage.newest_timestamp


# ------------------------------------------------------------------------------------------------ the clock
class TestClock:
    def test_clock_behind_stores_nothing_and_deletes_nothing(self, tmp_path):
        c = source_coordinator(tmp_path)
        now = int(time.time())
        _with_readings(c, now - 3600 + 2 * 3600, 12)  # readings up to 2 h "ahead" of the clock
        before = [r.timestamp for r in c.storage.readings]
        assert c.clock_behind(now)
        assert c._process_reading(130.0, now, "sensor.p", "P", True) == 0
        assert [r.timestamp for r in c.storage.readings] == before
        assert c._purge_whole_days(now) == 0
        assert [r.timestamp for r in c.storage.readings] == before

    def test_clock_within_margin_is_not_behind(self, tmp_path):
        c = source_coordinator(tmp_path)
        now = int(time.time())
        _with_readings(c, now - 3600, 13)  # the newest reading at now
        assert not c.clock_behind(now)
        assert not c.clock_behind(now - c.CLOCK_MARGIN_SECONDS)
        assert c.clock_behind(now - c.CLOCK_MARGIN_SECONDS - 1)

    def test_status_is_no_current_data_while_the_clock_is_behind(self, tmp_path):
        c = source_coordinator(tmp_path)
        now = int(time.time())
        c.last_reading_ts = now + 3600
        assert c._no_current_data_at(now) is True
        c.last_reading_ts = now - 60
        assert c._no_current_data_at(now) is False

    def test_forward_jump_purges_only_relative_to_the_stored_data(self, tmp_path):
        c = source_coordinator(tmp_path)
        now = int(time.time())
        _with_readings(c, now - 10 * 86400, 10 * 24 * 12)  # 10 days, the newest at about now
        count = c.storage.reading_count
        assert c._purge_whole_days(now + 365 * 86400) == 0  # a clock one year ahead removes nothing
        assert c.storage.reading_count == count

    def test_notice_created_and_closed(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        now = int(time.time())
        _with_readings(c, now + 2 * 3600, 3)
        c._check_clock(now)
        kw = issues.open[co.CLOCK_ISSUE_ID]
        assert kw["is_fixable"] is True and kw["translation_key"] == "clock_behind"
        assert set(kw["translation_placeholders"]) == {"newest", "now"} and kw["data"] == {"entry_id": "e1"}
        c._check_clock(now + 3 * 3600)  # the clock caught up: the notice closes by itself
        assert co.CLOCK_ISSUE_ID not in issues.open

    def test_clock_ahead_warning_after_three_late_readings(self, tmp_path, issues):
        """The warning opens at the third consecutive live reading measured more than an hour before the clock (a
        measurement sent again is not counted twice), once per episode, and closes with the first reading within the
        hour."""
        c = source_coordinator(tmp_path)
        now = int(time.time())
        late = now - c.CLOCK_AHEAD_SECONDS - 1
        c._check_clock_ahead("sensor.p", late - 600, now)
        c._check_clock_ahead("sensor.p", late - 300, now)
        c._check_clock_ahead("sensor.p", late - 300, now)  # the same measurement again
        assert co.CLOCK_AHEAD_ISSUE_ID not in issues.open
        c._check_clock_ahead("sensor.p", late, now)
        kw = issues.open[co.CLOCK_AHEAD_ISSUE_ID]
        assert kw["is_fixable"] is False and kw["severity"] == "warning" and kw["translation_key"] == "clock_ahead"
        assert set(kw["translation_placeholders"]) == {"newest", "now"}
        assert kw["translation_placeholders"]["newest"] == co.clock_stamp(c.regional, late)
        issues.open.clear()
        c._check_clock_ahead("sensor.p", late + 60, now + 60)  # still late: not created again in this episode
        assert co.CLOCK_AHEAD_ISSUE_ID not in issues.open
        issues.open[co.CLOCK_AHEAD_ISSUE_ID] = kw
        c._check_clock_ahead("sensor.p", now + 60 - c.CLOCK_AHEAD_SECONDS, now + 60)  # exactly one hour: within it
        assert co.CLOCK_AHEAD_ISSUE_ID not in issues.open and c._clock_ahead_count == 0

    def test_clock_ahead_counts_only_consecutive_late_readings(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        now = int(time.time())
        late = now - 2 * 3600
        for k, measured in enumerate((late, late + 300, now - 60, late + 900, late + 1200)):
            c._check_clock_ahead("sensor.p", measured, now)
            assert co.CLOCK_AHEAD_ISSUE_ID not in issues.open, k  # the reading within the hour started a new count

    def test_clock_ahead_warning_left_open_by_a_reload_is_closed(self, tmp_path, issues):
        """A new coordinator (a reload of the entry) does not know of a warning left open: its first reading within
        the hour deletes it."""
        issues.open[co.CLOCK_AHEAD_ISSUE_ID] = {}
        c = source_coordinator(tmp_path)
        now = int(time.time())
        c._check_clock_ahead("sensor.p", now - 60, now)
        assert co.CLOCK_AHEAD_ISSUE_ID not in issues.open

    def test_clock_ahead_is_judged_from_the_readings_own_measurement_times(self, tmp_path, issues):
        """The state listener passes each reading with its own measurement time; a reading without one (stored at the
        clock's time) is not judged."""
        c = source_coordinator(tmp_path)
        c.hass.async_add_executor_job = lambda *args: None
        now = int(time.time())
        event = TestSourceLog._event
        for k in range(3):
            when = datetime.fromtimestamp(now - 3 * 3600 + k * 300, UTC).isoformat()
            c._async_state_changed(event("sensor.p", state(str(120 + k), measurement_timestamp=when)))
        assert co.CLOCK_AHEAD_ISSUE_ID in issues.open
        c._async_state_changed(event("sensor.p", state("130")))  # no measurement time: not judged
        assert co.CLOCK_AHEAD_ISSUE_ID in issues.open
        when = datetime.fromtimestamp(now - 120, UTC).isoformat()
        c._async_state_changed(event("sensor.p", state("131", measurement_timestamp=when)))
        assert co.CLOCK_AHEAD_ISSUE_ID not in issues.open

    def test_discard_only_on_confirmation(self, tmp_path):
        c = source_coordinator(tmp_path)
        c._lock = __import__("threading").Lock()
        now = int(time.time())
        _with_readings(c, now - 3600, 6)
        _with_readings(c, now + 3600, 3)
        assert c.discard_readings_after_clock() == 3
        assert c.storage.newest_timestamp <= now and not c.clock_behind()
        assert c.last_reading_ts == c.storage.newest_timestamp

    @pytest.mark.parametrize("kept", [6, 0])
    def test_live_reading_stored_after_the_discard(self, tmp_path, kept):
        """After the Repairs fix the next live reading is stored: the source's last measurement time and its
        recent times are reset with the discarded readings, so a current reading is not "older than the last one".
        Also when no reading is left (kept = 0)."""
        c = source_coordinator(tmp_path)
        now = int(time.time())
        if kept:
            _with_readings(c, now - 3600, kept)
        ahead = now + 365 * 86400  # readings stored while the clock was a year ahead
        _with_readings(c, ahead - 9 * 300, 10)
        # as async_setup restores the newest stored reading, and as _process_reading records each source's times
        c._src_last_ts["sensor.p"] = c.storage.newest_timestamp
        c._src_times["sensor.p"] = [r.timestamp for r in c.storage.readings][-21:]
        c.active_source_entity = "sensor.p"
        assert c.clock_behind(now)
        assert c.discard_readings_after_clock() == 10
        assert c._src_last_ts["sensor.p"] <= now
        assert all(t <= now for t in c._src_times["sensor.p"])
        assert c._process_reading(135.0, now, "sensor.p", "P", True) == 2
        assert c._process_reading(140.0, now + 300, "sensor.p", "P", True) == 2
        assert c.storage.reading_count == kept + 2 and c.current_glucose == 140.0
        assert c.last_reading_ts == now + 300

    def test_discard_sets_the_current_values_from_the_newest_kept_reading(self, tmp_path):
        """The current value and the no-current-data flag follow the newest reading kept."""
        c = source_coordinator(tmp_path)
        now = int(time.time())
        _with_readings(c, now - 3000, 10, value=110.0)  # the newest kept reading 300 s before now
        _with_readings(c, now + 86400, 3, value=200.0)
        c.no_current_data = True
        assert c.discard_readings_after_clock() == 3
        assert c.current_glucose == 110.0
        assert c.no_current_data is False

    def test_an_older_reading_is_logged_once_per_source(self, tmp_path, caplog):
        """A reading older than the source's last measurement is dropped; the first one is logged (INFO, without the
        entity id), the next ones are not."""
        c = source_coordinator(tmp_path)
        now = int(time.time())
        assert c._process_reading(120.0, now, "sensor.p", "P", True) == 2
        with caplog.at_level(logging.INFO, logger=co.__name__):
            assert c._process_reading(121.0, now - 600, "sensor.p", "P", True) == 0
            assert c._process_reading(122.0, now - 900, "sensor.p", "P", True) == 0
        lines = [r for r in caplog.records if "older" in r.getMessage()]
        assert len(lines) == 1 and lines[0].levelno == logging.INFO
        assert "sensor.p" not in lines[0].getMessage()


# ------------------------------------------------------------------------------------------------ own entities
class TestOwnEntities:
    def test_coordinator_ignores_a_glucocraft_entity(self, tmp_path, issues):
        c = source_coordinator(tmp_path, secondary="sensor.glucocraft_last_glucose_reading")
        c._src_domain = {"sensor.glucocraft_last_glucose_reading": "glucocraft", "sensor.p": "libre"}
        assert c._source_reading("sensor.glucocraft_last_glucose_reading", state("140")) is None
        issue = f"{co.OWN_SOURCE_ISSUE_PREFIX}sensor.glucocraft_last_glucose_reading"
        assert issues.open[issue]["translation_key"] == "source_is_glucocraft"
        assert c._source_reading("sensor.p", state("140")) == (140.0, None)

    def _reported(self, c, value="121", when=None):
        """A source writing an unchanged value: the jobs it starts."""
        calls = []
        c.hass = types.SimpleNamespace(
            async_add_executor_job=lambda *a: calls.append(a), states=c.hass.states, config=c.hass.config
        )
        st = types.SimpleNamespace(
            state=value, attributes={"unit_of_measurement": "mg/dL"}, last_reported=when or datetime.now(UTC)
        )
        c._async_state_reported(types.SimpleNamespace(data={"entity_id": "sensor.p", "new_state": st}))
        return calls

    def test_a_value_written_again_while_home_assistant_starts_is_no_reading(self, tmp_path, issues):
        """A template sensor on a restored helper writes its old value again at the start event: that is no reading."""
        c = source_coordinator(tmp_path)
        c._src_last_ts["sensor.p"] = int(time.time()) - 600
        c._watch_source = False
        assert self._reported(c) == []

    def test_after_the_start_an_unchanged_value_is_a_reading_again(self, tmp_path, issues):
        """Steady glucose: without this it would look like no current data."""
        c = source_coordinator(tmp_path)
        c._src_last_ts["sensor.p"] = int(time.time()) - 600
        c._watch_source = False
        c._async_ha_started()
        assert len(self._reported(c)) == 1

    def test_state_reported_of_own_entity_adds_no_reading(self, tmp_path, issues):
        """A rewrite of GlucoCraft's own unchanged state (a state-reported event) does not become a new reading."""
        c = source_coordinator(tmp_path, secondary="sensor.glucocraft_last_glucose_reading")
        c._src_domain = {"sensor.glucocraft_last_glucose_reading": "glucocraft"}
        calls = []
        c.hass = types.SimpleNamespace(
            async_add_executor_job=lambda *a: calls.append(a), states=c.hass.states, config=c.hass.config
        )
        st = types.SimpleNamespace(
            state="140", attributes={"unit_of_measurement": "mg/dL"}, last_reported=datetime.now(UTC)
        )
        for _ in range(3):
            c._async_state_reported(
                types.SimpleNamespace(data={"entity_id": "sensor.glucocraft_last_glucose_reading", "new_state": st})
            )
        assert calls == []


# ------------------------------------------------------------------------------------------------ history import
def _recorded(value, updated: int, **attrs):
    return types.SimpleNamespace(
        state=value,
        attributes=attrs,
        last_updated=datetime.fromtimestamp(updated, UTC),
        last_changed=datetime.fromtimestamp(updated - 3600, UTC),
    )


class TestHistoryImport:
    def test_repeated_values_are_kept_with_their_measurement_time(self):
        base = 1_760_000_000
        states = [_recorded("120", base + i * 300 + 40, measurement_timestamp=base + i * 300) for i in range(6)]
        rows = hi.rows_from_states(states, False, True, {}, "", co._parse_reading_time)
        assert [r[0] for r in rows] == [base + i * 300 for i in range(6)]  # the measurement time, not arrival
        assert [r[1] for r in rows] == [120.0] * 6  # repeated values are not lost

    def test_last_updated_without_a_time_attribute(self):
        base = 1_760_000_000
        rows = hi.rows_from_states([_recorded("120", base), _recorded("120", base + 300)], False, True, {})
        assert [r[0] for r in rows] == [base, base + 300]  # last_updated, not last_changed

    def test_one_row_per_measurement_time_the_last_one_kept(self):
        base = 1_760_000_000
        states = [_recorded("120", base + 10, timestamp=base), _recorded("121", base + 20, timestamp=base)]
        assert hi.rows_from_states(states, False, True, {}) == [(base, 121.0, None)]

    @pytest.mark.parametrize(
        "is_mmol, values", [(False, ("50", "51", "399", "400")), (True, ("2.8", "2.9", "22.1", "22.2"))]
    )
    def test_limits_set_by_hand_as_live(self, is_mmol, values):
        """The import decides each state as a live one (limits.classify): with limits set by hand a reading at the lower
        limit is LO and one at the upper limit HI; with Automatic limits of the same values both are measured."""
        base = 1_760_000_000
        states = [_recorded(v, base + i * 300) for i, v in enumerate(values)]
        by_hand = {**L.resolve("50", "400", None), "dexcom_type": False, "nightscout": False}
        rows = hi.rows_from_states(states, is_mmol, True, by_hand)
        assert [r[2] for r in rows] == ["low", None, None, "high"]
        assert (rows[0][1], rows[3][1]) == (50.0, 400.0)
        automatic = {**L.resolve("auto", "auto", "carelink"), "dexcom_type": False, "nightscout": False}  # 50 / 400
        assert (automatic["lower"], automatic["upper"]) == (50, 400)
        assert [r[2] for r in hi.rows_from_states(states, is_mmol, True, automatic)] == [None] * 4

    def test_configured_time_attribute_and_mmol(self):
        base = 1_760_000_000
        states = [_recorded("6.0", base + 50, when=base, measurement_timestamp=base - 999)]
        rows = hi.rows_from_states(states, True, True, {}, "when")
        assert rows[0][0] == base and 107 <= rows[0][1] <= 109

    def test_reads_every_row_with_attributes(self, monkeypatch):
        seen = {}

        def fake(hass, start, end, entity_ids, **kw):
            seen.update(kw, entity_ids=entity_ids)
            return {"sensor.p": ["x"]}

        monkeypatch.setattr(hi, "get_significant_states", fake)
        assert hi._get_history(None, "sensor.p", None, None) == ["x"]
        assert seen["significant_changes_only"] is False and seen["no_attributes"] is False
        assert seen["include_start_time_state"] is False and seen["entity_ids"] == ["sensor.p"]


# ------------------------------------------------------------------------------------------------ notices
class TestNotices:
    def test_storage_problem_notice(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        c.storage.problem, c.storage.kept_file = "damaged", "glucocraft_t.bin.corrupt-1"
        c._check_storage_problem()
        kw = issues.open[co.STORAGE_ISSUE_ID]
        assert kw["translation_key"] == "storage_damaged"
        assert kw["translation_placeholders"]["file"] == "glucocraft_t.bin.corrupt-1"
        c.storage.problem = None
        c._check_storage_problem()
        assert co.STORAGE_ISSUE_ID not in issues.open

    @pytest.mark.parametrize(
        "problem, kept, key",
        [
            ("damaged", None, "storage_damaged_not_kept"),  # not moved aside: saving blocked until the restart
            ("recovered", None, "storage_recovered_not_kept"),  # no copy kept: saving blocked until the restart
            ("recovered", "glucocraft_t.bin.corrupt-1", "storage_recovered"),
            ("unreadable", None, "storage_unreadable"),  # never a kept file
        ],
    )
    def test_storage_problem_notice_without_a_kept_copy(self, tmp_path, issues, problem, kept, key):
        c = source_coordinator(tmp_path)
        c.storage.problem, c.storage.kept_file = problem, kept
        c._check_storage_problem()
        assert issues.open[co.STORAGE_ISSUE_ID]["translation_key"] == key

    def test_unit_notice_closes_once_the_source_has_a_unit(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        for _ in range(10):
            c._source_reading("sensor.p", types.SimpleNamespace(state="6.1", attributes={}))
        assert "source_unit_sensor.p" in issues.open
        c._source_reading("sensor.p", state("6.1", "mmol/L"))
        assert "source_unit_sensor.p" not in issues.open

    def test_former_source_notices_removed(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        issues.async_create_issue(None, "glucocraft", "slow_source_sensor.old")
        issues.async_create_issue(None, "glucocraft", "slow_source_sensor.p")
        issues.async_create_issue(None, "glucocraft", "terms_of_use_e1")
        c._clear_former_source_issues()
        assert set(issues.open) == {"slow_source_sensor.p", "terms_of_use_e1"}


# ------------------------------------------------------------------------------------------------ the source log
class TestSourceLog:
    @pytest.fixture
    def log(self, caplog):
        caplog.set_level(logging.INFO, logger=co._LOGGER.name)
        return lambda: [(r.levelname, r.getMessage()) for r in caplog.records if r.name == co._LOGGER.name]

    @staticmethod
    def _current(tmp_path):
        """A coordinator whose source in use (sensor.p) is available and sent a reading a minute ago."""
        c = source_coordinator(tmp_path)
        c.hass.states.d["sensor.p"] = state("120")
        now = int(time.time())
        assert c._process_reading(120.0, now - 60, "sensor.p", "P", True) == 2
        assert c.no_current_data is False
        return c, now

    @staticmethod
    def _event(entity_id, new_state):
        return types.SimpleNamespace(data={"entity_id": entity_id, "new_state": new_state, "old_state": None})

    def test_nothing_is_logged_before_home_assistant_has_started(self, tmp_path, log):
        c, _ = self._current(tmp_path)
        c.hass.states.d["sensor.p"] = state("unavailable")
        c._async_state_changed(self._event("sensor.p", state("unavailable")))
        c._set_stale(True)
        assert log() == []

    def test_unavailable_once_then_recovered_once(self, tmp_path, log):
        c, now = self._current(tmp_path)
        c._async_ha_started()
        assert log() == []  # delivering: nothing to log
        c.hass.states.d["sensor.p"] = state("unavailable")
        c._async_state_changed(self._event("sensor.p", state("unavailable")))
        assert log() == [
            ("WARNING", "GlucoCraft: the primary source, the source in use, is unavailable (no current data)")
        ]
        for _ in range(3):  # the minute timer while it lasts, the data also becoming stale: no repeat
            c._set_stale(True)
        c._async_state_changed(self._event("sensor.p", state("unknown")))
        c.hass.states.d["sensor.p"] = state("120")  # back, but no new reading yet: the same outage
        c._set_stale(True)
        assert len(log()) == 1
        assert c._process_reading(121.0, now, "sensor.p", "P", True) == 2
        c._set_stale(False)
        assert log()[1:] == [("INFO", "GlucoCraft: the primary source delivers current readings again")]

    def test_no_current_data_once_then_recovered_once(self, tmp_path, log):
        c, now = self._current(tmp_path)
        c.last_reading_ts = now - 3600  # an hour without a reading, the source still available
        c._set_stale(c._no_current_data_at(now))
        assert log() == []  # Home Assistant has not started yet
        c._async_ha_started()
        c._set_stale(True)
        c._set_stale(True)
        minutes = c._stale_minutes
        assert log() == [
            (
                "WARNING",
                f"GlucoCraft: no new reading from the primary source for more than {minutes} minutes (no current data)",
            )
        ]
        assert c._process_reading(118.0, now, "sensor.p", "P", True) == 2
        assert c._process_reading(117.0, now + 60, "sensor.p", "P", True) == 2
        assert log()[1:] == [("INFO", "GlucoCraft: the primary source delivers current readings again")]

    def test_an_outage_is_logged_once_across_reloads(self, tmp_path, log):
        """A settings save reloads the entry (a new coordinator): the outage already logged is not logged again; its
        end is logged once, and the next outage again."""
        c, now = self._current(tmp_path)
        c.last_reading_ts = now - 3600
        c._async_ha_started()
        c._set_stale(True)
        assert len(log()) == 1
        for i in range(2):  # two reloads during the same outage: a new coordinator on the same Home Assistant
            (tmp_path / f"reload{i}").mkdir()
            r, _ = self._current(tmp_path / f"reload{i}")
            r.hass.data = c.hass.data
            r.last_reading_ts = now - 3600
            r._async_ha_started()
            r._set_stale(True)
        assert len(log()) == 1
        assert r._process_reading(118.0, now, "sensor.p", "P", True) == 2
        assert log()[-1] == ("INFO", "GlucoCraft: the primary source delivers current readings again")
        r.last_reading_ts = now - 3600 + 60  # a later outage (another last reading): logged
        r._set_stale(True)
        assert [level for level, _ in log()] == ["WARNING", "INFO", "WARNING"]

    def test_the_second_source_is_named_by_its_role(self, tmp_path, log):
        c = source_coordinator(tmp_path, secondary="sensor.s")
        c.hass.states.d.update({"sensor.p": state("unavailable"), "sensor.s": state("119")})
        c._async_ha_started()
        assert log()[-1][0] == "WARNING" and "primary source" in log()[-1][1]
        assert c._process_reading(119.0, int(time.time()), "sensor.s", "S", False) == 2
        assert log()[-1] == ("INFO", "GlucoCraft: the second source delivers current readings again")
        assert all("sensor." not in message for _, message in log())  # never the entity id


def test_period_stats_agp_never_empty_while_rebuilt(tmp_path):
    """The consensus statistics are assigned once: a reader never sees an empty dictionary in between."""
    import inspect

    src = inspect.getsource(co.GlucoCraftCoordinator._compute_all_stats)
    assert "self.period_stats_agp = {}" not in src and "self.period_stats_agp = agp_stats" in src
    # the same for the statistics on the ranges in use: assigned once, never filled period by period
    assert "self.period_stats[" not in src and "self.period_stats = period_stats" in src


# ------------------------------------------------------------------------------------------------ parsers, import, setup
class TestTimeParsers:
    @pytest.mark.parametrize("value", ["inf", "-inf", "1e400", "nan", float("inf"), float("nan")])
    def test_an_infinite_or_nan_number_is_no_time(self, value):
        """No OverflowError from the live parser or the import's."""
        assert co._parse_reading_time(value) is None
        assert hi._epoch(value) is None

    @pytest.mark.parametrize("text", ["2026-10-28", "20261028", " 2026-10-28 "])
    def test_a_date_without_a_time_is_no_measurement_time(self, text, monkeypatch):
        """Home Assistant's parser reads a date as its midnight; the coordinator does not pass it a text without
        a time part, so a date is never taken as a measurement time (with the real parser as with the stub)."""
        calls = []

        def as_ciso8601(value):
            """Home Assistant's parser (ciso8601): a date gives its midnight."""
            calls.append(value)
            return datetime.fromisoformat(value)

        monkeypatch.setattr(co.dt_util, "parse_datetime", as_ciso8601)
        assert co._parse_reading_time(text) is None
        assert calls == []
        assert co._parse_reading_time("2026-10-28T08:15:00") is not None and calls == ["2026-10-28T08:15:00"]

    def test_a_time_with_a_date_is_still_read(self):
        assert co._parse_reading_time("2026-10-28T08:15:00+00:00") == int(
            datetime(2026, 10, 28, 8, 15, tzinfo=UTC).timestamp()
        )
        assert co._parse_reading_time(1_793_000_000_123) == 1_793_000_000


class TestImportUnit:
    @pytest.mark.parametrize("uom, kept", [("mg/dL", True), ("mmol/L", True), ("xyz", False), ("", False)])
    def test_the_import_reads_the_unit_as_the_live_path(self, monkeypatch, uom, kept):
        """An unknown unit text is no unit, so a value under 30 is left out (as live, _source_reading)."""
        import asyncio

        base = int(time.time()) - 3600
        got = []

        async def rows_kept(fetch, to_rows, now, tz, days):
            got.extend(to_rows([_recorded("25", base + 30, timestamp=base)], None))
            return []

        monkeypatch.setattr(hi, "async_rows_kept", rows_kept)
        source = types.SimpleNamespace(state="25", attributes={"unit_of_measurement": uom})
        hass = types.SimpleNamespace(states=types.SimpleNamespace(get=lambda e: source))
        assert asyncio.run(hi.async_import_history(hass, "sensor.p", None)) == 0
        assert bool(got) is kept


class TestClockNoticeOnce:
    def test_the_notice_is_created_once_per_episode(self, tmp_path, issues, monkeypatch):
        """While the clock stays behind, the notice is not rewritten every minute."""
        created = []
        original = issues.async_create_issue
        monkeypatch.setattr(issues, "async_create_issue", lambda *a, **kw: (created.append(a[2]), original(*a, **kw)))
        c = source_coordinator(tmp_path)
        now = int(time.time())
        _with_readings(c, now + 2 * 3600, 3)
        for minute in range(5):
            c._check_clock(now + 60 * minute)
        assert created.count(co.CLOCK_ISSUE_ID) == 1 and co.CLOCK_ISSUE_ID in issues.open
        c._check_clock(now + 3 * 3600)  # caught up: closed
        assert co.CLOCK_ISSUE_ID not in issues.open
        c._check_clock(now)  # behind again: a new episode, a new notice
        assert created.count(co.CLOCK_ISSUE_ID) == 2 and co.CLOCK_ISSUE_ID in issues.open


class TestReadingJob:
    def test_an_error_is_logged_with_the_source_s_role(self, tmp_path, caplog, monkeypatch):
        """An error in a reading job nobody waits for is logged at once, without the entity id."""
        c = source_coordinator(tmp_path)

        def broken(*args):
            raise RuntimeError("boom")

        monkeypatch.setattr(c, "_process_locked", broken)
        with caplog.at_level(logging.ERROR, logger=co.__name__):
            c._process_and_notify("sensor.p", "P", 120.0, int(time.time()))
        [record] = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert record.getMessage() == "GlucoCraft: a reading of the primary source was not processed"
        assert record.exc_info is not None


class TestSmoothedLevel:
    def test_the_smoothed_level_is_not_stored(self, tmp_path):
        """The smoothed level stays inside the coordinator: a stored reading has no smoothed value, and the reserved
        field of its record in the file is 0."""
        c = source_coordinator(tmp_path)
        now = int(time.time())
        for k, v in enumerate((120.0, 121.0, 121.0)):
            assert c._process_reading(v, now - 600 + 300 * k, "sensor.p", "P", True) == 2
        assert not hasattr(c.storage.readings[-1], "smoothed")
        c.storage.save()
        body = c.storage.path.read_bytes()[C.STORAGE_HEADER_SIZE :]
        size = C.STORAGE_READING_SIZE
        assert len(body) == 3 * size
        assert all(body[i + 6 : i + 8] == bytes(2) for i in range(0, len(body), size))


class TestSetup:
    @staticmethod
    def _setup(tmp_path, monkeypatch, fake_import):
        import asyncio

        from test_settings_harness import entry, make_hass

        monkeypatch.setattr(co, "async_import_history", fake_import)
        hass = make_hass(tmp_path)
        c = co.GlucoCraftCoordinator(hass, "t", entry(glucose_entity="sensor.p"))
        c.hass = hass
        c._schedule_state_save = lambda: None
        asyncio.run(c.async_setup())
        return c

    def test_imported_readings_give_the_current_value_at_once(self, tmp_path, monkeypatch):
        """After a history import on an empty store, the newest imported reading is the current value."""
        now = int(time.time())

        async def fake_import(hass, entity_id, storage, **kw):
            rows = [(now - 60 - 300 * k, 100.0 + k, None) for k in range(30)][::-1]
            return storage.merge_history(rows)

        c = self._setup(tmp_path, monkeypatch, fake_import)
        assert c.storage.reading_count == 30
        assert c.last_reading_ts == now - 60 and c.current_glucose == 100.0
        assert c.no_current_data is False and c.display_status == "current"
        assert c.active_source_entity == "sensor.p" and c._src_last_ts["sensor.p"] == now - 60

    def test_each_stored_reading_is_saved_at_once(self, tmp_path, monkeypatch):
        """A stored reading is saved at once, under the lock (an append of its record, storage.py), so that a loss of
        power loses no stored reading; the timer then has nothing to save. Other changes are saved by the timer."""
        import asyncio

        async def no_import(*args, **kw):
            return 0

        c = self._setup(tmp_path, monkeypatch, no_import)
        saves = []
        real = c.storage.save

        def save():
            saves.append(c._lock.locked())
            real()

        monkeypatch.setattr(c.storage, "save", save)
        now = int(time.time())
        for minute in range(3):
            assert c._process_locked("sensor.p", "P", 120.0 + minute, now - 120 + 60 * minute, True) == 2
            assert saves == [True] * (minute + 1) and not c.storage.dirty
        assert c._process_locked("sensor.p", "P", 125.0, now - 600, True) == 0  # an older reading: nothing saved
        asyncio.run(c._async_tick())
        assert len(saves) == 3  # nothing new: no save
        c.storage.dirty = True  # a change that is not a stored reading (a purge, a discard): the timer saves it
        asyncio.run(c._async_tick())
        assert saves[3:] == [True] and not c.storage.dirty

    def test_the_purge_at_setup_runs_under_the_lock(self, tmp_path, monkeypatch):
        """The whole-day purge at setup runs as on the timer: in the executor, under the lock."""
        seen = []
        original = co.GlucoCraftCoordinator._purge_whole_days

        def purge(self, now):
            seen.append(self._lock.locked())
            return original(self, now)

        monkeypatch.setattr(co.GlucoCraftCoordinator, "_purge_whole_days", purge)
        from glucocraft.storage import GlucoCraftStorage

        old = int(time.time()) - 400 * 86400
        st = GlucoCraftStorage(str(tmp_path / "glucocraft"), "t")
        st.load()
        for k in range(20):
            add_reading(st, old + 300 * k, 120.0)
        for k in range(20):
            add_reading(st, int(time.time()) - 6000 + 300 * k, 120.0)
        st.save()

        async def no_import(*args, **kw):
            return 0

        c = self._setup(tmp_path, monkeypatch, no_import)
        assert seen and seen[0] is True
        assert c.storage.reading_count == 20  # the readings 400 days old were purged


# ------------------------------------------------------------------------------------------------ intake and storage
def _setup_coordinator(tmp_path, monkeypatch):
    """A coordinator set up as at a start (TestSetup), with an empty store and no history import."""

    async def no_import(*args, **kw):
        return 0

    return TestSetup._setup(tmp_path, monkeypatch, no_import)


class TestSaveFailedNotice:
    def test_a_failing_save_opens_the_notice_and_a_good_save_closes_it(self, tmp_path, monkeypatch, issues):
        import asyncio
        import os

        from glucocraft import sensor as se

        c = _setup_coordinator(tmp_path, monkeypatch)
        history = se.GlucoCraftDataHistory.__new__(se.GlucoCraftDataHistory)
        history.coordinator = c
        now = int(time.time())
        assert c._process_locked("sensor.p", "P", 120.0, now - 120, True) == 2
        real = os.fsync

        def full(fd):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(os, "fsync", full)
        assert c._process_locked("sensor.p", "P", 121.0, now - 60, True) == 2  # stored, kept in memory
        asyncio.run(c._async_tick())
        kw = issues.open[co.STORAGE_SAVE_ISSUE_ID]
        assert kw["translation_key"] == "storage_save_failed" and kw["severity"] == "error"
        assert kw["translation_placeholders"] == {
            "folder": str(c.storage.path.parent),
            "error": "No space left on device",
        }
        assert history.extra_state_attributes["storage_problem"] == "save_failed"
        monkeypatch.setattr(os, "fsync", real)
        asyncio.run(c._async_tick())  # the timer's save succeeds: both close
        assert co.STORAGE_SAVE_ISSUE_ID not in issues.open
        assert history.extra_state_attributes["storage_problem"] is None
        assert c.storage.reading_count == 2 and not c.storage.dirty


class TestNotGlucoseUnit:
    @pytest.mark.parametrize("unit", ["°F", "%", "W", "mg/m³", "mg/L"])
    def test_no_reading_and_a_notice_until_the_unit_is_a_glucose_unit(self, tmp_path, issues, unit):
        c = source_coordinator(tmp_path)
        issue = f"{co.NOT_GLUCOSE_UNIT_ISSUE_PREFIX}sensor.p"
        for _ in range(3):
            assert c._source_reading("sensor.p", state("72", unit)) is None
        kw = issues.open[issue]
        assert kw["translation_key"] == "source_unit_not_glucose"
        assert kw["translation_placeholders"] == {"entity": "sensor.p", "unit": unit}
        assert c._source_reading("sensor.p", state("120", "mg/dL")) == (120.0, None)
        assert issue not in issues.open

    def test_a_source_without_a_unit_keeps_the_unitless_rule(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        assert c._source_reading("sensor.p", types.SimpleNamespace(state="120", attributes={})) == (120.0, None)
        assert c._source_reading("sensor.p", state("120", "")) == (120.0, None)
        assert issues.open == {}

    def test_the_prefixes_are_distinct(self):
        """Each source notice id has exactly one prefix (the clean-up and the diagnostics find it by its prefix)."""
        prefixes = co.SOURCE_ISSUE_PREFIXES
        assert len(set(prefixes)) == len(prefixes)
        assert not any(a != b and a.startswith(b) for a in prefixes for b in prefixes)


class TestMissingSource:
    def test_a_notice_once_home_assistant_has_started(self, tmp_path, issues):
        c = source_coordinator(tmp_path, secondary="sensor.s")
        c.hass.states.d["sensor.s"] = state("120")
        c._check_source_notices()
        assert issues.open == {}  # before the start: the integrations may still be loading
        c._watch_source = True
        c._check_source_notices()
        kw = issues.open[f"{co.SOURCE_MISSING_ISSUE_PREFIX}sensor.p"]
        assert kw["translation_key"] == "source_missing" and kw["translation_placeholders"] == {"entity": "sensor.p"}
        assert f"{co.SOURCE_MISSING_ISSUE_PREFIX}sensor.s" not in issues.open
        c.hass.states.d["sensor.p"] = state("unavailable")  # the entity exists again (any state)
        c._check_source_notices()
        assert issues.open == {}

    def test_removed_with_a_source_change(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        issues.async_create_issue(None, "glucocraft", f"{co.SOURCE_MISSING_ISSUE_PREFIX}sensor.old")
        c._clear_former_source_issues()
        assert issues.open == {}


class TestCatchUp:
    """A clock-behind episode of 10 readings: refused while it lasts, imported once the clock is right."""

    @staticmethod
    def _recorder(monkeypatch, states):
        calls = []

        async def run(fn, *a):
            return fn(*a)

        def get_history(hass, entity_id, start, end):
            calls.append((start, end))
            return [s for s in states if start <= s.last_updated <= end]

        monkeypatch.setattr(hi, "get_instance", lambda hass: types.SimpleNamespace(async_add_executor_job=run))
        monkeypatch.setattr(hi, "_get_history", get_history)
        monkeypatch.setattr(co, "async_import_history", hi.async_import_history)
        return calls

    @staticmethod
    def _recorded(value, measured, recorded):
        return types.SimpleNamespace(
            state=str(value),
            attributes={"unit_of_measurement": "mg/dL", "timestamp": measured},
            last_updated=datetime.fromtimestamp(recorded, UTC),
            last_changed=datetime.fromtimestamp(recorded, UTC),
        )

    def test_the_episode_is_filled_in_and_nothing_stored_changes(self, tmp_path, monkeypatch, issues):
        import asyncio

        c = _setup_coordinator(tmp_path, monkeypatch)
        base = (int(time.time()) // 300) * 300 - 4 * 3600
        for k in range(12):
            add_reading(c.storage, base - 300 * (11 - k), 110.0 + k)
        c.storage.save()
        before = [(r.timestamp, r.raw, r.limit, r.source) for r in c.storage.readings]
        clock = [base - 3600]  # the clock an hour behind the newest stored reading
        monkeypatch.setattr(co.time, "time", lambda: clock[0])
        c.hass.states.d["sensor.p"] = state("130")
        for k in range(1, 11):
            assert c._process_locked("sensor.p", "P", 130.0 + k, base + 300 * k, True) == 0  # refused
        assert c._catch_up_after == base and c.storage.reading_count == 12
        # the recorder holds the stored readings (recorded on time) and the episode's (at the clock's wrong time)
        states = [self._recorded(110 + k, base - 300 * (11 - k), base - 300 * (11 - k) + 20) for k in range(12)]
        states += [self._recorded(130 + k, base + 300 * k, base - 3600 + 300 * k) for k in range(1, 11)]
        calls = self._recorder(monkeypatch, states)
        clock[0] = base + 3360  # the clock is right again; a live reading arrives first
        assert c._process_locked("sensor.p", "P", 150.0, base + 3300, True) == 2
        asyncio.run(c._async_tick())
        assert co.CLOCK_ISSUE_ID not in issues.open and c._catch_up_after is None
        stored = [(r.timestamp, r.raw, r.limit, r.source) for r in c.storage.readings]
        assert stored[:12] == before  # no stored reading changed
        assert [r[0] for r in stored[12:]] == [base + 300 * k for k in range(1, 12)]
        assert [r[1] for r in stored[12:22]] == [130.0 + k for k in range(1, 11)] and stored[22][1] == 150.0
        assert all((end - start).total_seconds() <= 86400 + 1 for start, end in calls)  # read one day at a time
        n = len(calls)
        asyncio.run(c._async_tick())
        assert len(calls) == n  # once per episode
        assert reopen_count(c) == 23

    def test_after_the_discard(self, tmp_path, monkeypatch, issues):
        """The readings were stored while the clock was a day ahead: after the discard, the readings refused since
        are imported, from the newest reading kept on."""
        import asyncio

        c = _setup_coordinator(tmp_path, monkeypatch)
        now = (int(time.time()) // 300) * 300
        for k in range(12):
            add_reading(c.storage, now - 7200 - 300 * (11 - k), 110.0)  # kept
        for k in range(3):
            add_reading(c.storage, now + 86400 + 300 * k, 200.0)  # stored while the clock was ahead
        c.storage.save()
        c.hass.states.d["sensor.p"] = state("130")
        for k in range(1, 7):
            assert c._process_locked("sensor.p", "P", 130.0, now - 7200 + 300 * k, True) == 0
        states = [self._recorded(130 + k, now - 7200 + 300 * k, now - 7200 + 300 * k + 30) for k in range(1, 7)]
        self._recorder(monkeypatch, states)
        assert c.discard_readings_after_clock() == 3
        assert asyncio.run(c.async_catch_up()) == 6
        assert c.storage.reading_count == 18 and c.last_reading_ts == now - 7200 + 1800
        assert c._src_last_ts["sensor.p"] == now - 7200 + 1800


def reopen_count(c) -> int:
    """The readings in the coordinator's file as a new load reads them."""
    from glucocraft.storage import GlucoCraftStorage

    s = GlucoCraftStorage(str(c.storage.path.parent), "t")
    s.load()
    return s.reading_count


class TestShutdownGuard:
    def test_a_reading_after_the_shutdown_is_not_stored(self, tmp_path, monkeypatch):
        c = _setup_coordinator(tmp_path, monkeypatch)
        now = int(time.time())
        assert c._process_locked("sensor.p", "P", 120.0, now - 60, True) == 2
        c._shut_down = True  # the first step of async_shutdown, before its final save
        updates = []
        monkeypatch.setattr(c, "async_set_updated_data", lambda data: updates.append(data))
        c._process_and_notify("sensor.p", "P", 121.0, now, True)
        assert c._process_locked("sensor.p", "P", 122.0, now, True) == 0  # also when it got past the first check
        assert c.storage.reading_count == 1 and updates == []


class TestSameMeasurementWindow:
    @pytest.mark.parametrize("history_step", [300, 900])
    def test_a_faster_source_after_a_slower_history_loses_no_reading(self, tmp_path, history_step):
        """With one source, readings 60 s apart after a 5- or 15-minute history are each stored."""
        c = source_coordinator(tmp_path)
        now = int(time.time())
        start = now - 3600 - 30 * history_step
        for k in range(30):
            add_reading(c.storage, start + history_step * k, 120.0)
        c.detected_interval_seconds = c.storage.detect_interval()
        assert c.detected_interval_seconds == history_step
        first = c.storage.newest_timestamp + 60
        for k in range(12):
            assert c._process_reading(120.0 + k, first + 60 * k, "sensor.p", "P", True) == 2
            assert c.storage.reading_count == 31 + k

    def test_with_two_sources_the_window_waits_for_the_live_intervals(self, tmp_path):
        """With two sources, the stored history's interval no longer widens the window: 15 s until both live
        intervals are known."""
        c = source_coordinator(tmp_path, secondary="sensor.s")
        now = int(time.time())
        for k in range(30):
            add_reading(c.storage, now - 3 * 3600 + 900 * k, 120.0)
        c.detected_interval_seconds = c.storage.detect_interval()
        assert c._source_interval("sensor.p", known_only=True) is None
        assert c._source_interval("sensor.p") == 900  # the history's interval elsewhere (the gap rules)


class TestImportFollowsTheUnitRule:
    def test_a_source_in_another_unit_imports_nothing(self, monkeypatch):
        import asyncio

        called = []

        async def rows_kept(*a):
            called.append(a)
            return []

        monkeypatch.setattr(hi, "async_rows_kept", rows_kept)
        source = types.SimpleNamespace(state="72", attributes={"unit_of_measurement": "°F"})
        hass = types.SimpleNamespace(states=types.SimpleNamespace(get=lambda e: source))
        assert asyncio.run(hi.async_import_history(hass, "sensor.p", None)) == 0
        assert called == []


class TestImportByDay:
    def test_day_parts_give_the_rows_of_one_read(self):
        """The recorder read one day at a time gives exactly the rows of one read of the whole span, also for a
        state recorded on a boundary and a measurement recorded twice across one."""
        import asyncio
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Los_Angeles")
        now = 1_760_000_000
        start = now - 12 * 86400
        states = [_recorded(str(100 + i % 90), start + 600 * i, timestamp=start + 600 * i - 30) for i in range(1700)]
        boundary = start + 86400  # recorded exactly where the first part ends
        states.append(_recorded("150", boundary, timestamp=boundary - 30))
        states.append(_recorded("151", boundary + 5, timestamp=boundary - 30))  # the same measurement, recorded again
        states.sort(key=lambda s: s.last_updated)
        spans = []

        async def fetch(a, b):
            spans.append((a, b))
            lo, hi_ = datetime.fromtimestamp(a, UTC), datetime.fromtimestamp(b, UTC)
            return [s for s in states if lo <= s.last_updated <= hi_]

        def to_rows(part, newest):
            return hi.rows_from_states(part, False, True, {}, now=now, after=newest)

        got = asyncio.run(hi.async_rows_kept(fetch, to_rows, now, tz, 200))
        first = hi.retention_start(now, tz, 200)
        whole = [r for r in hi.rows_from_states(states, False, True, {}, now=now) if r[0] >= first]
        assert got == whole and len(spans) > 12
        assert all(b - a <= 86400 + 1 for a, b in spans)
        assert (boundary - 30, 151.0, None) in got
