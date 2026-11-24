# License: AGPL-3.0-only (LICENSE).
"""Edge cases of the sources, the settings and the states: GlucoCraft says so when something is wrong, and follows
what it can.

Rules under test:
- A source's new entity id is followed: primary, second source and expiry entity, one change for a device rename, what
  is kept per source goes with it, and another integration's entity or a second change back starts nothing.
- A source stating mmol/L with values above 33.3 opens a notice after 10 in a row; the readings are used all the same;
  the next value at or below 33.3 closes it.
- A learned limit is forgotten when a Nightscout source shows another device, and only then; the device is kept across
  restarts; a limit set by hand always wins over a learned one.
- A measurement-time attribute set in the settings opens a notice after 10 readings in a row without a usable time; the
  readings keep the arrival time; a usable time closes it; a source without a name set is not concerned.
- A primary whose new values keep replacing its own reading opens a notice after 10 minutes; what becomes a reading does
  not change; a reading added or the values stopping closes it; ordinary sources and a second source's copies never
  open it.
- A change of Home Assistant's core configuration publishes the states at once.
- The period's minimum and maximum states are rounded like the mean and SD; the last reading and the delta stay exact.
- A badly formed notification time is refused with a field error on both screens; the two good forms stay good.
- A note names the zones and bands that share a color; near low and near high alone are no finding; the default palettes
  give no note, and the save is never refused.
- The printer address refuses a host that cannot exist; every good form stays good.
- Deleting with "keep" says that the settings are not kept, in all three places.
"""

from __future__ import annotations

import json
import time
import types
from typing import ClassVar

import pytest
from glucocraft import config_flow as cf
from glucocraft import const as C
from glucocraft import contrast as K
from glucocraft import coordinator as co
from glucocraft import sensor as se
from glucocraft import settings as st
from glucocraft import source_notices as sn
from glucocraft import source_rename as S
from glucocraft.i18n import t

from conftest import REPO
from test_clock_and_sources import FakeIssues
from test_data_path import source_coordinator, state

PKG = REPO / "custom_components" / "glucocraft"
STRINGS = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))


@pytest.fixture
def issues(monkeypatch):
    fake = FakeIssues()
    monkeypatch.setattr(co, "ir", fake)
    monkeypatch.setattr(sn, "ir", fake)  # the timer's source notices (source_notices.py)
    return fake


# --------------------------------------------------------------------------------------------- a source's new entity id
class TestSourceRename:
    DATA: ClassVar = {
        "glucose_entity": "sensor.p",
        "secondary_glucose_entity": "sensor.s",
        "sensor_expiry_entity": "sensor.x",
    }

    @pytest.mark.parametrize("old", ["sensor.p", "sensor.s", "sensor.x"])
    def test_every_source_setting_follows(self, old):
        pending = {}
        S.add_rename(pending, self.DATA, old, "sensor.new")
        assert pending == {old: "sensor.new"}
        out = S.followed_data(self.DATA, pending)
        assert sorted(out.values()) == sorted("sensor.new" if v == old else v for v in self.DATA.values())

    def test_another_entity_starts_nothing(self):
        pending = {}
        S.add_rename(pending, self.DATA, "sensor.other", "sensor.other2")
        assert pending == {}

    def test_a_second_change_continues_the_first_and_back_is_nothing(self):
        pending = {}
        S.add_rename(pending, self.DATA, "sensor.p", "sensor.a")
        S.add_rename(pending, self.DATA, "sensor.a", "sensor.b")
        assert pending == {"sensor.p": "sensor.b"}
        S.add_rename(pending, self.DATA, "sensor.b", "sensor.p")
        assert pending == {}

    def test_a_setting_not_used_is_left_alone(self):
        data = {"glucose_entity": "sensor.p", "secondary_glucose_entity": None}
        assert S.followed_data(data, {"sensor.p": "sensor.n"}) == {
            "glucose_entity": "sensor.n",
            "secondary_glucose_entity": None,
        }

    def test_what_is_kept_goes_with_the_source(self, tmp_path):
        c = source_coordinator(tmp_path)
        c._learned_limits = {"sensor.p": {"upper": 400}, "sensor.other": {"upper": 450}}
        c._learned_device = {"sensor.p": "share2"}
        c.follow_source_renames({"sensor.p": "sensor.n"})
        assert c._learned_limits == {"sensor.n": {"upper": 400}, "sensor.other": {"upper": 450}}
        assert c._learned_device == {"sensor.n": "share2"}
        assert c.active_source_entity == "sensor.n"

    def _watch(self, monkeypatch, data):
        """The watch on a stand-in Home Assistant: returns the event sender, the waiting timer list and the calls."""
        listeners, timers, calls = {}, [], []

        def later(_hass, _seconds, fn):
            timers.append(fn)
            return lambda: timers.remove(fn)

        monkeypatch.setattr(S, "async_call_later", later)
        monkeypatch.setattr(S, "known_entry_data", dict)
        entries = types.SimpleNamespace(
            async_update_entry=lambda e, data: (calls.append(("update", data)), setattr(e, "data", data)),
            async_reload=lambda entry_id: ("reload", entry_id),
        )
        hass = types.SimpleNamespace(
            config_entries=entries,
            async_create_task=calls.append,
            bus=types.SimpleNamespace(async_listen=lambda _n, fn: listeners.setdefault("fn", fn) and (lambda: None)),
        )
        entry = types.SimpleNamespace(entry_id="e1", data=dict(data), unload=[])
        entry.async_on_unload = entry.unload.append
        coordinator = types.SimpleNamespace(follow_source_renames=lambda r: calls.append(("follow", r)))
        S.async_setup_source_rename_watch(hass, entry, coordinator)

        def send(old, new, action="update"):
            listeners["fn"](types.SimpleNamespace(data={"action": action, "entity_id": new, "old_entity_id": old}))

        return send, timers, calls, entry

    def test_a_device_rename_gives_one_change_and_one_reload(self, monkeypatch):
        send, timers, calls, entry = self._watch(monkeypatch, self.DATA)
        send("sensor.p", "sensor.p2")
        send("sensor.unrelated", "sensor.unrelated2")  # the device's other ids restart the wait
        send("sensor.x", "sensor.x2")
        assert len(timers) == 1 and calls == []
        timers.pop()(None)
        assert next(c for c in calls if isinstance(c, tuple)) == (
            "follow",
            {"sensor.p": "sensor.p2", "sensor.x": "sensor.x2"},
        )
        assert entry.data["glucose_entity"] == "sensor.p2" and entry.data["sensor_expiry_entity"] == "sensor.x2"
        assert entry.data["secondary_glucose_entity"] == "sensor.s"
        assert calls.count(("reload", "e1")) == 1

    def test_nothing_without_a_source(self, monkeypatch):
        send, timers, calls, _entry = self._watch(monkeypatch, self.DATA)
        send("sensor.unrelated", "sensor.unrelated2")
        assert timers == [] and calls == []

    def test_there_and_back_reloads_nothing(self, monkeypatch):
        send, timers, calls, _entry = self._watch(monkeypatch, self.DATA)
        send("sensor.p", "sensor.p2")
        send("sensor.p2", "sensor.p")
        assert timers == [] and calls == []


# --------------------------------------------------------------------------------------------- mmol/L values above 33.3
class TestMmolTooHigh:
    ISSUE = f"{co.MMOL_TOO_HIGH_ISSUE_PREFIX}sensor.p"

    def test_after_ten_in_a_row_and_still_used(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        for _ in range(9):
            assert c._source_reading("sensor.p", state("110", "mmol/L")) == (500.0, "high")
        assert self.ISSUE not in issues.open
        assert c._source_reading("sensor.p", state("110", "mmol/L")) == (500.0, "high")
        assert issues.open[self.ISSUE]["translation_key"] == "source_mmol_too_high"

    def test_the_run_is_broken_and_the_notice_closes(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        for _ in range(9):
            c._source_reading("sensor.p", state("110", "mmol/L"))
        c._source_reading("sensor.p", state("6.0", "mmol/L"))
        for _ in range(9):
            c._source_reading("sensor.p", state("110", "mmol/L"))
        assert self.ISSUE not in issues.open
        c._source_reading("sensor.p", state("110", "mmol/L"))
        assert self.ISSUE in issues.open
        c._source_reading("sensor.p", state("33.3", "mmol/L"))  # at the limit: not above it
        assert self.ISSUE not in issues.open

    def test_a_correct_unit_closes_it(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        for _ in range(10):
            c._source_reading("sensor.p", state("110", "mmol/L"))
        c._source_reading("sensor.p", state("110", "mg/dL"))
        assert self.ISSUE not in issues.open

    def test_the_first_reading_after_a_reload_closes_a_notice_left_open(self, tmp_path, issues):
        issues.async_create_issue(None, "glucocraft", self.ISSUE)
        source_coordinator(tmp_path)._source_reading("sensor.p", state("6.0", "mmol/L"))
        assert self.ISSUE not in issues.open

    def test_mg_dl_values_are_not_concerned(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        for _ in range(12):
            c._source_reading("sensor.p", state("450"))
        assert issues.open == {}

    def test_the_limit_is_600_mg_dl(self):
        assert co.MMOL_TOO_HIGH * 18.016 == pytest.approx(600, abs=0.1)
        assert co.MMOL_TOO_HIGH_RUN == 10


# ------------------------------------------------------------------------------------------ mmol/L values labeled mg/dL
class TestMgdlTooLow:
    """mmol/L values labeled mg/dL: the opposite of the case above. Only a notice; a source's codes are not a wrong
    unit."""

    ISSUE = f"{co.MGDL_TOO_LOW_ISSUE_PREFIX}sensor.p"

    def test_after_ten_in_a_row_and_treated_as_before(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        for _ in range(9):
            assert c._source_reading("sensor.p", state("18.2", "mg/dL")) == (40.0, "low")
        assert self.ISSUE not in issues.open
        assert c._source_reading("sensor.p", state("18.2", "mg/dL")) == (40.0, "low")
        assert issues.open[self.ISSUE]["translation_key"] == "source_mgdl_too_low"

    def test_values_treated_as_no_reading_count_too(self, tmp_path, issues):
        """The two ways classify sorts these values make one run: a mmol/L stream crosses from one to the other."""
        c = source_coordinator(tmp_path)
        for value in ("5.5", "5.9", "12.5", "15.5", "25.5", "6.1", "7.3", "8.8", "9.4", "10.2"):
            c._source_reading("sensor.p", state(value, "mg/dL"))
        assert self.ISSUE in issues.open
        assert c._source_reading("sensor.p", state("5.5", "mg/dL")) is None

    def test_the_run_is_broken_and_the_notice_closes(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        for _ in range(9):
            c._source_reading("sensor.p", state("18.2", "mg/dL"))
        c._source_reading("sensor.p", state("120", "mg/dL"))
        for _ in range(9):
            c._source_reading("sensor.p", state("18.2", "mg/dL"))
        assert self.ISSUE not in issues.open
        c._source_reading("sensor.p", state("18.2", "mg/dL"))
        assert self.ISSUE in issues.open
        c._source_reading("sensor.p", state("29.9", "mg/dL"))
        assert self.ISSUE in issues.open
        c._source_reading("sensor.p", state("30", "mg/dL"))  # the limit: not below it
        assert self.ISSUE not in issues.open

    def test_a_correct_unit_closes_it(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        for _ in range(10):
            c._source_reading("sensor.p", state("6.1", "mg/dL"))
        c._source_reading("sensor.p", state("6.1", "mmol/L"))
        assert self.ISSUE not in issues.open

    @pytest.mark.parametrize("value", ["0", "1", "5", "12", "LO", "unknown"])
    def test_a_code_or_a_text_neither_counts_nor_closes(self, tmp_path, issues, value):
        """A failing sensor sending its code for hours is not a wrong unit."""
        c = source_coordinator(tmp_path)
        for _ in range(30):
            c._source_reading("sensor.p", state(value, "mg/dL"))
        assert issues.open.get(self.ISSUE) is None
        for _ in range(10):
            c._source_reading("sensor.p", state("18.2", "mg/dL"))
        assert self.ISSUE in issues.open
        c._source_reading("sensor.p", state(value, "mg/dL"))
        assert self.ISSUE in issues.open

    def test_a_code_does_not_break_a_run(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        for i in range(10):
            c._source_reading("sensor.p", state("18.2", "mg/dL"))
            if i == 4:
                c._source_reading("sensor.p", state("6", "mg/dL"))
        assert self.ISSUE in issues.open

    def test_a_whole_number_from_nightscout_is_a_code(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        for _ in range(12):
            c._check_mgdl_too_low("sensor.p", "mg/dL", 20.0, True)
        assert self.ISSUE not in issues.open
        for _ in range(10):
            c._check_mgdl_too_low("sensor.p", "mg/dL", 20.0, False)
        assert self.ISSUE in issues.open

    def test_the_first_reading_after_a_reload_closes_a_notice_left_open(self, tmp_path, issues):
        issues.async_create_issue(None, "glucocraft", self.ISSUE)
        source_coordinator(tmp_path)._source_reading("sensor.p", state("120", "mg/dL"))
        assert self.ISSUE not in issues.open

    def test_real_readings_and_a_source_without_a_unit_are_not_concerned(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        for value in (39, 40, 55, 120, 400, 500):
            for _ in range(11):
                c._source_reading("sensor.p", state(str(value), "mg/dL"))
        for _ in range(11):
            c._source_reading("sensor.q", state("6.1", None))
        assert self.ISSUE not in issues.open and f"{co.MGDL_TOO_LOW_ISSUE_PREFIX}sensor.q" not in issues.open

    def test_the_limit_is_below_every_sensor_limit(self):
        assert co.MGDL_TOO_LOW == 30 and co.MGDL_TOO_LOW_RUN == 10


# --------------------------------------------------------------------------------------- a learned limit and the device
class TestLearnedLimitAndDevice:
    def nightscout(self, tmp_path):
        c = source_coordinator(tmp_path)
        c._src_domain["sensor.p"] = "nightscout"
        c._apply_ranges = lambda: None
        return c

    def test_another_device_forgets_the_limit(self, tmp_path):
        c = self.nightscout(tmp_path)
        c._learned_limits["sensor.p"] = {"upper": 400}
        c._learned_device["sensor.p"] = "share2"
        c._source_reading("sensor.p", state("120", device="share2"))
        assert c._learned_limits["sensor.p"] == {"upper": 400}  # the same device keeps it
        assert c._source_reading("sensor.p", state("450", device="libre-receiver")) == (450.0, None)
        assert "sensor.p" not in c._learned_limits and c._learned_device["sensor.p"] == "libre-receiver"

    def test_a_limit_from_before_this_rule_takes_the_device_seen_first(self, tmp_path):
        c = self.nightscout(tmp_path)
        c._learned_limits["sensor.p"] = {"upper": 400}
        c._source_reading("sensor.p", state("120", device="share2"))
        assert c._learned_limits["sensor.p"] == {"upper": 400} and c._learned_device["sensor.p"] == "share2"

    def test_only_nightscout(self, tmp_path):
        c = source_coordinator(tmp_path)
        c._src_domain["sensor.p"] = "other"
        c._learned_limits["sensor.p"] = {"upper": 400}
        c._learned_device["sensor.p"] = "a"
        c._source_reading("sensor.p", state("120", device="b"))
        assert c._learned_limits["sensor.p"] == {"upper": 400}

    def test_kept_across_restarts(self, tmp_path):
        c = self.nightscout(tmp_path)
        c._learned_device["sensor.p"] = "share2"
        assert c._state_data()["learned_device"] == {"sensor.p": "share2"}

    def test_a_limit_learned_keeps_its_device(self, tmp_path):
        c = self.nightscout(tmp_path)
        c._source_reading("sensor.p", state("Above 400 mg/dL", device="share2"))
        assert c._learned_limits["sensor.p"]["upper"] == 400 and c._learned_device["sensor.p"] == "share2"

    def test_a_limit_set_by_hand_wins_over_a_learned_one(self, tmp_path):
        c = source_coordinator(tmp_path, {"sensor_upper_limit": "500"})
        c._learned_limits["sensor.p"] = {"upper": 400}
        assert c._source_reading("sensor.p", state("450")) == (450.0, None)


# --------------------------------------------------------------------------------------- the measurement-time attribute
class TestTimeAttribute:
    ISSUE = f"{co.TIME_ATTRIBUTE_ISSUE_PREFIX}sensor.p"

    def test_after_ten_in_a_row_and_a_usable_time_closes_it(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        c._time_attr["sensor.p"] = "does_not_exist"
        for _ in range(9):
            c._check_time_attribute("sensor.p", False)
        assert self.ISSUE not in issues.open
        c._check_time_attribute("sensor.p", False)
        kw = issues.open[self.ISSUE]
        assert kw["translation_key"] == "source_time_attribute"
        assert kw["translation_placeholders"] == {"entity": "sensor.p", "attribute": "does_not_exist"}
        c._check_time_attribute("sensor.p", True)
        assert self.ISSUE not in issues.open

    def test_a_run_broken_by_a_usable_time_starts_again(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        c._time_attr["sensor.p"] = "attr"
        for _ in range(9):
            c._check_time_attribute("sensor.p", False)
        c._check_time_attribute("sensor.p", True)
        for _ in range(9):
            c._check_time_attribute("sensor.p", False)
        assert issues.open == {}

    def test_without_a_name_set_nothing(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        for _ in range(12):
            c._check_time_attribute("sensor.p", False)
        assert issues.open == {}

    def test_clearing_the_name_closes_it(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        c._time_attr["sensor.p"] = "attr"
        for _ in range(10):
            c._check_time_attribute("sensor.p", False)
        assert self.ISSUE in issues.open
        c._time_attr["sensor.p"], c._no_time_run = "", {}  # the reload after the setting is cleared
        c._check_time_attribute("sensor.p", False)
        assert self.ISSUE not in issues.open

    def test_the_second_source_too(self, tmp_path, issues):
        c = source_coordinator(tmp_path, secondary="sensor.s")
        c._time_attr["sensor.s"] = "attr"
        for _ in range(10):
            c._check_time_attribute("sensor.s", False)
        assert f"{co.TIME_ATTRIBUTE_ISSUE_PREFIX}sensor.s" in issues.open

    def test_the_arrival_time_stays_the_fallback(self, tmp_path):
        c = source_coordinator(tmp_path)
        c._time_attr["sensor.p"] = "does_not_exist"
        now = int(time.time())
        ts, from_source = c._reading_time("sensor.p", state("120"))
        assert from_source is False and abs(ts - now) < 5


# ------------------------------------------------------------------------------------------------ a fast source
class TestFastSource:
    ISSUE = f"{co.FAST_SOURCE_ISSUE_PREFIX}sensor.p"

    def _feed(self, c, start, seconds, step, first_value=100.0):
        for k, ts in enumerate(range(start, start + seconds + 1, step)):
            c._process_reading(first_value + k % 30, ts, "sensor.p", "P", True, None)

    def test_after_ten_minutes_of_replacing_nothing_added(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        start = int(time.time()) - 640
        self._feed(c, start, 630, 10)
        assert c.storage.reading_count == 1  # what becomes a reading is what it was
        c._check_source_notices()
        assert issues.open[self.ISSUE]["translation_key"] == "fast_source"

    def test_under_ten_minutes_nothing(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        self._feed(c, int(time.time()) - 300, 290, 10)
        c._check_source_notices()
        assert issues.open == {}

    def test_a_reading_added_closes_it(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        start = int(time.time()) - 700
        self._feed(c, start, 630, 10)
        c._check_source_notices()
        assert self.ISSUE in issues.open
        c._process_reading(120.0, start + 690, "sensor.p", "P", True, None)  # 60 s later: a new reading
        assert c.storage.reading_count == 2
        c._check_source_notices()
        assert self.ISSUE not in issues.open

    def test_the_values_stopping_closes_it(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        start = int(time.time()) - 1300
        self._feed(c, start, 630, 10)
        c._check_source_notices()  # the run ended more than 5 minutes ago
        assert self.ISSUE not in issues.open

    @pytest.mark.parametrize("step", [16, 60, 300])
    def test_an_ordinary_source_never(self, tmp_path, issues, step):
        c = source_coordinator(tmp_path)
        start = int(time.time()) - 40 * step
        self._feed(c, start, 39 * step, step)
        c._check_source_notices()
        assert self.ISSUE not in issues.open and c.storage.reading_count == 40

    def test_a_second_sources_copies_are_not_a_run(self, tmp_path, issues):
        c = source_coordinator(tmp_path, secondary="sensor.s")
        start = int(time.time()) - 6000
        for k in range(20):  # the second source's reading, then the primary's copy of that measurement
            ts = start + k * 300
            c.storage.insert_reading(ts, 120.0, None, C.SOURCE_SECONDARY)
            c._process_reading(121.0, ts + 5, "sensor.p", "P", True, None)
            assert c._replacing_run is None
        c._check_source_notices()
        assert self.ISSUE not in issues.open


# ------------------------------------------------------------------------------------------ a core configuration change
def test_a_core_configuration_change_publishes_at_once(tmp_path):
    c = source_coordinator(tmp_path)
    called = []
    c.async_update_listeners = lambda: called.append(1)
    c.async_core_config_updated(None)
    assert called == [1]


def test_the_listener_is_released_with_the_others():
    src = (PKG / "__init__.py").read_text(encoding="utf-8")
    assert (
        "entry.async_on_unload(hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, coordinator.async_core_config_updated))"
        in src
    )


# ------------------------------------------------------------------------------------- the period's minimum and maximum
@pytest.mark.parametrize(
    "cls, field",
    [(se.GlucoCraftPeriodMin, "min_value"), (se.GlucoCraftPeriodMax, "max_value")],
)
def test_the_minimum_and_maximum_states_are_rounded_like_the_mean(cls, field):
    entity = cls.__new__(cls)
    entity.coordinator = types.SimpleNamespace(display_unit="mmol/L")
    entity._get_stats = lambda: types.SimpleNamespace(**{field: 500.0}, sensor_active_ok=True)
    assert entity.native_value == se._state_glucose(500.0, "mmol/L")
    assert entity.native_value / 18.016 == pytest.approx(27.8, abs=1e-6)  # what Home Assistant then shows and stores
    entity.coordinator.display_unit = "mg/dL"
    entity._get_stats = lambda: types.SimpleNamespace(**{field: 123.4}, sensor_active_ok=True)
    assert entity.native_value == 123
    entity._get_stats = lambda: None
    assert entity.native_value is None


# ------------------------------------------------------------------------------------------------ the notification time
@pytest.mark.parametrize(
    "value, ok",
    [
        ("07:05", True),
        ("07:05:30", True),
        ("23:59", True),
        ("00:00", True),
        ("7:5", False),
        ("25:00", False),
        ("07:60", False),
        ("", False),
        (None, False),
        (705, False),
    ],
)
def test_time_of_day_valid(value, ok):
    assert st.time_of_day_valid(value) is ok


def test_both_screens_have_the_error_text():
    for flow in ("config", "options"):
        assert STRINGS[flow]["error"]["notify_time_invalid"]
    src = (PKG / "config_flow.py").read_text(encoding="utf-8")
    # one check (_notification_errors), run by the last setup screen and by Configure > Notifications
    assert src.count('{CONF_NOTIFY_TIME: "notify_time_invalid"}') == 1
    for step in ("async_step_notifications", "async_step_finish"):
        body = src[src.index(f"async def {step}(") :]
        end = body.find("\n    async def ", 1)
        assert "_notification_errors(user_input)" in (body if end < 0 else body[:end]), step


# ----------------------------------------------------------------------------------------------- colors that are shared
class TestSharedColors:
    ZONES: ClassVar = {
        "very_low": "#111111",
        "low": "#222222",
        "near_low": "#333333",
        "tight_range": "#444444",
        "near_high": "#555555",
        "high": "#666666",
        "very_high": "#777777",
    }

    def test_the_default_palettes_give_no_note(self):
        assert K.shared_colors_note(C.ZONE_COLORS, C.BAND_COLORS) == ""

    def test_near_low_and_near_high_alone_are_no_finding(self):
        zones = {**self.ZONES, "near_high": self.ZONES["near_low"]}
        assert K.shared_colors_note(zones, self.ZONES) == ""

    def test_a_zone_sharing_a_color_is_named(self):
        zones = {**self.ZONES, "low": self.ZONES["tight_range"].lower()}  # the case makes no difference
        note = K.shared_colors_note(zones, self.ZONES)
        assert note == " " + t(
            "contrast.shared_zone", zones=f"{t('contrast.zone.low')}, {t('contrast.zone.tight_range')}"
        )

    def test_a_third_zone_with_near_low_and_near_high_is_a_finding(self):
        zones = {**self.ZONES, "near_high": "#333333", "tight_range": "#333333"}
        assert t("contrast.zone.tight_range") in K.shared_colors_note(zones, self.ZONES)

    def test_a_band_is_named_as_a_band(self):
        bands = {**self.ZONES, "high": self.ZONES["very_high"]}
        note = K.shared_colors_note(self.ZONES, bands)
        assert t("contrast.band.high") in note and t("contrast.band.very_high") in note

    def test_two_groups_give_two_sentences(self):
        zones = {**self.ZONES, "low": "#AAAAAA", "tight_range": "#AAAAAA", "high": "#BBBBBB", "very_high": "#BBBBBB"}
        assert K.shared_colors_note(zones, self.ZONES).count("same color") == 2


# ------------------------------------------------------------------------------------------------ the printer address
@pytest.mark.parametrize(
    "address, ok",
    [
        ("not an address", False),
        ("999.1.1.1", False),
        ("1.2.3", False),
        ("ipp://999.1.1.1/ipp/print", False),
        ("a" * 300, False),
        ("a" * 64, False),
        (".".join(["a" * 60] * 5), False),  # 304 characters
        ("-printer.local", False),
        ("printer.local-", False),  # a label that ends with a hyphen
        ("print$er", False),
        ("192.168.77.250", True),
        (" 192.168.77.250 ", True),
        ("a" * 63, True),
        ("printer.local", True),
        ("printer.local.", True),
        ("HP_Laser.local", True),
        ("ipp://192.168.77.250:631/ipp/print", True),
        ("http://192.168.77.250/x", True),
        ("ipps://printer.local:631/ipp/print", True),
        ("[fe80::1]", True),
        ("ipp://[2001:db8::1]:631/ipp/print", True),
        ("ipp://[fe80::1]/x", True),
        ("ipp://[zz::1]/x", False),
    ],
)
def test_the_printer_host_can_exist(address, ok):
    assert cf.printer_address_valid(address) is ok


# ------------------------------------------------------------------------------------------------ deleting with keep
def test_keep_says_the_settings_are_not_kept():
    backend = json.loads((PKG / "i18n" / "en" / "backend.json").read_text(encoding="utf-8"))
    assert "settings were not" in backend["notify.kept.message"]
    assert "settings" in STRINGS["config"]["step"]["kept"]["data_description"]["kept_readings"]
    docs = (PKG / "i18n" / "en" / "documentation.json").read_text(encoding="utf-8")
    assert docs.count("a reinstall starts with the default settings") >= 2


# ------------------------------------------------------------------------------------------------ the texts
@pytest.mark.parametrize(
    "issue, placeholders",
    [
        ("source_mmol_too_high", {"entity"}),
        ("source_mgdl_too_low", {"entity"}),
        ("source_time_attribute", {"entity", "attribute"}),
        ("fast_source", {"entity"}),
    ],
)
def test_the_notices_have_their_texts(issue, placeholders):
    text = STRINGS["issues"][issue]
    assert text["title"] and text["description"]
    for name in placeholders:
        assert "{" + name + "}" in text["description"]
    assert "alarm" not in text["description"].lower()
    docs = (PKG / "i18n" / "en" / "documentation.json").read_text(encoding="utf-8")
    assert text["title"] in docs


def test_the_two_copies_of_the_texts_agree():
    assert json.loads((PKG / "translations" / "en.json").read_text(encoding="utf-8")) == STRINGS
