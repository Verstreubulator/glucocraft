# License: AGPL-3.0-only (LICENSE).
"""The sources, the limit stretches and their notices (each expected number worked out by hand).

Rules under test:
- A source without its own measurement time that delivers exactly the same value for 12 readings or more, over at
  least 60 minutes (first to last), opens the stuck-source notice with its text; a different value or a reading
  with its own time closes it. With a measurement-time attribute a repeated value with the same time is no new reading.
- A warm-up ends by itself 24 hours after it began; from 3 hours into a warm-up the no-data notice is due, with its own
  text.
- The current stretch at the sensor's limit: its first reading (a data gap starts a new stretch) and the newest measured
  reading before it, kept across a restart (the store); the stretches of a period and the panel file's GCR1 block.
- The Configuration sensor's attention: an open Repairs notice that was not ignored.
- A newest reading more than 5 minutes ahead of the clock is untrusted and always raises the clock notice.
- A report period that does not exist is refused with 400 and the refusal's text.
- The diagnostics redact the custom ranges and goals and say whether they are set; they name the country.
- The expiry entity, the sensor limits set by hand and the phones without anything to send are pointed out.
"""

from __future__ import annotations

import json
import struct
import time
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from glucocraft import config_flow as cf
from glucocraft import const as C
from glucocraft import coordinator as co
from glucocraft import diagnostics as dg
from glucocraft import http_api
from glucocraft import notification as nt
from glucocraft import panel_feed as pf
from glucocraft import sensor as se
from glucocraft import source_notices as sn
from glucocraft.limit_runs import current_limit_run, last_measured_before, limit_runs
from glucocraft.maintenance import MaintenanceIssues
from glucocraft.storage import GlucoseReading

from conftest import add_reading
from test_clock_and_sources import FakeIssues
from test_data_path import source_coordinator, state
from test_diagnostics import _redact

STRINGS = json.loads(
    (Path(__file__).resolve().parents[1] / "custom_components/glucocraft/strings.json").read_text(encoding="utf-8")
)


class Issues(FakeIssues):
    """FakeIssues whose registry entries carry their fields as attributes, as Home Assistant's IssueEntry does."""

    def async_create_issue(self, hass, domain, issue_id, **kw):
        """Record an open Repairs issue."""
        super().async_create_issue(hass, domain, issue_id, **kw)
        self.issues[(domain, issue_id)] = types.SimpleNamespace(**kw)


@pytest.fixture
def issues(monkeypatch):
    fake = Issues()
    monkeypatch.setattr(co, "ir", fake)
    monkeypatch.setattr(sn, "ir", fake)  # the timer's source notices (source_notices.py)
    return fake


# ------------------------------------------------------------------------------------------------ a stuck source
STUCK = f"{co.STUCK_SOURCE_ISSUE_PREFIX}sensor.p"


def test_thirteen_identical_readings_over_60_minutes_open_the_notice(tmp_path, issues):
    # 5-minute readings: 12 readings span 55 minutes (11 intervals), the 13th spans 60 minutes: the notice opens then
    c = source_coordinator(tmp_path)
    t0 = 1_800_000_000
    for k in range(12):
        c._check_stuck_source("sensor.p", 120.0, None, t0 + 300 * k, False)
    assert STUCK not in issues.open
    c._check_stuck_source("sensor.p", 120.0, None, t0 + 300 * 12, False)
    notice = issues.open[STUCK]
    assert notice["translation_key"] == "source_stuck"
    assert notice["translation_placeholders"] == {"entity": "sensor.p", "count": "13", "minutes": "60"}
    # a different value closes it, and a new run starts from 1
    c._check_stuck_source("sensor.p", 121.0, None, t0 + 300 * 13, False)
    assert STUCK not in issues.open and c._stuck["sensor.p"][1] == 1


def test_twelve_readings_over_an_hour_are_enough_and_a_reading_with_its_own_time_closes_it(tmp_path, issues):
    # readings 6 minutes apart: 12 readings span 66 minutes: open at the 12th
    c = source_coordinator(tmp_path)
    t0 = 1_800_000_000
    for k in range(12):
        c._check_stuck_source("sensor.p", 120.0, None, t0 + 360 * k, False)
    assert issues.open[STUCK]["translation_placeholders"]["count"] == "12"
    assert issues.open[STUCK]["translation_placeholders"]["minutes"] == "66"
    c._check_stuck_source("sensor.p", 120.0, None, t0 + 360 * 12, True)
    assert STUCK not in issues.open


def test_the_notice_survives_a_reload_while_the_value_stays(tmp_path, issues):
    c = source_coordinator(tmp_path)
    t0 = 1_800_000_000
    for k in range(13):
        c._check_stuck_source("sensor.p", 120.0, None, t0 + 300 * k, False)
    assert STUCK in issues.open
    c2 = source_coordinator(tmp_path / "reloaded")
    c2._check_stuck_source("sensor.p", 120.0, None, t0 + 300 * 13, False)  # the same value: kept open
    assert STUCK in issues.open
    c3 = source_coordinator(tmp_path / "again")
    c3._check_stuck_source("sensor.p", 118.0, None, t0 + 300 * 14, False)  # another value: closed
    assert STUCK not in issues.open


def test_the_approved_stuck_text_is_word_for_word():
    issue = STRINGS["issues"]["source_stuck"]
    assert issue["title"] == "Glucose source may have stopped: the same value for over an hour"
    assert issue["description"].startswith(
        "The glucose source {entity} has delivered exactly the same value for its last {count} readings, over "
        "{minutes} minutes."
    )
    assert "2. In Developer tools > States, open {entity}" in issue["description"]
    assert issue["description"].endswith("This notice closes by itself when the value changes.")


def test_with_a_measurement_time_a_repeated_value_is_no_new_reading(tmp_path, issues):
    """Step 2 of the notice: with the measurement-time attribute set, the same value with the same time is the same
    measurement (no reading is added), and a state written again (state reported) starts no job at all."""
    c = source_coordinator(tmp_path)
    c._time_attr = {"sensor.p": "measured_at"}
    measured = int(time.time()) - 120
    st = state("120", measured_at=datetime.fromtimestamp(measured, UTC).isoformat())
    ts, from_source = c._reading_time("sensor.p", st)
    assert (ts, from_source) == (measured, True)
    assert c._process_reading(120.0, ts, "sensor.p", "P", True) == co.ReadingResult.STORED
    assert c._process_reading(120.0, ts, "sensor.p", "P", True) == co.ReadingResult.UNCHANGED
    assert c.storage.reading_count == 1
    jobs = []
    c.hass = types.SimpleNamespace(
        async_add_executor_job=lambda *a: jobs.append(a), states=c.hass.states, config=c.hass.config
    )
    c._watch_source = True
    reported = types.SimpleNamespace(state="120", attributes=st.attributes, last_reported=datetime.now(UTC))
    c._async_state_reported(types.SimpleNamespace(data={"entity_id": "sensor.p", "new_state": reported}))
    assert jobs == [] and c._stuck == {}


# ------------------------------------------------------------------------------------------------ warm-up
def test_a_warm_up_ends_24_hours_after_it_began(tmp_path):
    c = source_coordinator(tmp_path)
    began = 1_800_000_000
    c.sensor_warming_up, c.warmup_started_at = True, began
    c._end_overlong_warmup(began + 24 * 3600 - 1)
    assert c.sensor_warming_up is True
    c._end_overlong_warmup(began + 24 * 3600)
    assert c.sensor_warming_up is False and c.warmup_started_at == 0


def test_the_no_data_notice_runs_from_three_hours_into_a_warm_up(tmp_path):
    c = source_coordinator(tmp_path)
    began = int(time.time()) - 3 * 3600
    c.sensor_warming_up, c.warmup_started_at, c.no_current_data, c.last_reading_ts = True, began, True, 0
    assert nt.no_data_notice_due(c, began + 3 * 3600 - 1) is False  # 2 h 59 min 59 s
    assert nt.no_data_notice_due(c, began + 3 * 3600) is True  # also without any reading
    when = c.regional.clock(datetime.fromtimestamp(began, UTC).astimezone(nt.dt_util.get_default_time_zone()))
    assert nt.no_data_notice_message(c, began + 3 * 3600) == (
        f"The sensor warm-up began at {when} and no reading has arrived since. A warm-up usually takes much less time "
        "than this. Check the CGM sensor and its app."
    )
    assert nt._outage_key(c) == began  # the warm-up's notice is one of its own


# ------------------------------------------------------------------------------------------------ limit stretches
def _rd(ts, raw=120.0, limit=None):
    return GlucoseReading(ts, raw, limit)


def test_limit_runs_end_where_the_last_reading_stops_representing_time():
    # 5-minute readings: measured, LO, LO, LO, measured -> one LO stretch from the 2nd reading to the 5th (the 4th
    # reading represents 300 s to the 5th): 300 to 1200. Then HI at 1500, a 40-minute gap (a gap: more than 15
    # minutes), HI at 3900 -> two HI stretches: 1500 to 1800 (its own interval after the gap) and 3900 to 4200
    rd = [_rd(0), _rd(300, 40, "low"), _rd(600, 40, "low"), _rd(900, 40, "low"), _rd(1200)]
    rd += [_rd(1500, 500, "high"), _rd(3900, 500, "high")]
    iv = [300] * len(rd)
    assert limit_runs(rd, iv) == [(300, 1200, "low"), (1500, 1800, "high"), (3900, 4200, "high")]
    assert limit_runs(rd, iv, since=600) == [(600, 1200, "low"), (1500, 1800, "high"), (3900, 4200, "high")]
    assert current_limit_run(rd, iv) == 6  # the newest stretch begins at the last reading (after the gap)
    assert last_measured_before(rd, 6).timestamp == 1200


def test_the_current_stretch_on_the_last_glucose_reading_sensor(tmp_path):
    # measured 52 mg/dL, then LO for 4 readings: the stretch began at the first LO, the last measured value is 52
    c = source_coordinator(tmp_path)
    now = int(time.time())
    t0 = now - 1500
    for k, (value, limit) in enumerate([(52.0, None), (40.0, "low"), (40.0, "low"), (40.0, "low"), (40.0, "low")]):
        add_reading(c.storage, t0 + 300 * k, value, limit)
    c.storage.save()
    c._current_from_store(now)
    e = se.GlucoCraftCurrentGlucose.__new__(se.GlucoCraftCurrentGlucose)
    e.coordinator = c
    a = e.extra_state_attributes
    assert (a["limit_since"], a["last_measured_mgdl"], a["last_measured_ts"]) == (t0 + 300, 52.0, t0)
    # a restart: the same store gives the same stretch
    c2 = source_coordinator(tmp_path)
    c2._current_from_store(now)
    assert (c2.limit_since, c2.last_measured_mgdl, c2.last_measured_ts) == (t0 + 300, 52.0, t0)
    # a measured reading ends it: no stretch, the newest measured reading is the current one
    assert c._process_reading(61.0, t0 + 1500, "sensor.p", "P", True) == co.ReadingResult.STORED
    assert (c.limit_since, c.last_measured_mgdl, c.last_measured_ts) == (None, 61.0, t0 + 1500)


def test_a_data_gap_starts_a_new_stretch(tmp_path):
    c = source_coordinator(tmp_path)
    now = int(time.time())
    t0 = now - 6000
    for k in range(5):  # 5-minute readings to establish the interval
        add_reading(c.storage, t0 + 300 * k, 100.0)
    add_reading(c.storage, t0 + 1500, 40.0, "low")
    add_reading(c.storage, t0 + 1500 + 1800, 40.0, "low")  # 30 minutes later: a gap (longer than 15 minutes)
    c._current_from_store(now)
    assert c.limit_since == t0 + 3300 and c.last_measured_ts == t0 + 1200


def test_the_panel_file_carries_the_stretches_in_gcr1(tmp_path):
    c = source_coordinator(tmp_path)
    now = int(time.time())
    t0 = now - 3000
    rows = [(120.0, None)] * 3 + [(40.0, "low")] * 2 + [(120.0, None)] * 2 + [(500.0, "high")] + [(120.0, None)] * 2
    for k, (value, limit) in enumerate(rows):
        add_reading(c.storage, t0 + 300 * k, value, limit)
    c._current_from_store(now)
    c._stats_pass(now)
    data = pf.build_glu2(c, delta=False)
    gcr_end = len(data) - 120  # GCL2 (60) and GCM1 (60) follow it
    assert data[gcr_end - 4 : gcr_end] == b"GCR1"
    # two stretches of 9 bytes: the uint16 count sits 6 + 9 x 2 = 24 bytes before the end of the block
    assert struct.unpack_from("<H", data, gcr_end - 24)[0] == 2
    runs = [struct.unpack_from("<IIB", data, gcr_end - 22 + 9 * k) for k in range(2)]
    assert runs == [(t0 + 900, t0 + 1500, 1), (t0 + 2100, t0 + 2400, 2)]
    # the readings themselves stay out of READINGS: 10 readings, 3 of them at the limit
    assert struct.unpack_from("<I", data, 20)[0] == 7


def test_the_data_api_carries_the_stretches(tmp_path):
    c = source_coordinator(tmp_path)
    now = int(time.time())
    t0 = now - 1800
    for k, (value, limit) in enumerate([(120.0, None), (40.0, "low"), (40.0, "low"), (120.0, None)]):
        add_reading(c.storage, t0 + 300 * k, value, limit)
    readings, runs = http_api._chart_readings(c, now - 6 * 3600, 6 * 3600, "mg/dL")
    assert runs == [[t0 + 300, t0 + 900, "low"]]
    assert [r["t"] for r in readings] == [t0, t0 + 900]


# ------------------------------------------------------------------------------------------------ attention
def test_attention_follows_the_open_repairs_notices():
    m = MaintenanceIssues(types.SimpleNamespace(), lambda: None)
    assert m.attention is False
    m._set("_repairs", ["Glucose source not found"])
    assert m.attention is True
    m.panel_offline("d1", "Panel A")  # an offline panel is no Repairs notice
    m._set("_repairs", [])
    assert m.attention is False


def test_the_configuration_carries_attention(tmp_path):
    c = source_coordinator(tmp_path)
    c.maintenance._repairs = ["x"]
    assert c.configuration()["attention"] is True


# ------------------------------------------------------------------------------------------------ the clock
def test_a_reading_ahead_of_the_clock_is_untrusted_and_raises_the_clock_notice(tmp_path, issues):
    c = source_coordinator(tmp_path)
    now = int(time.time())
    add_reading(c.storage, now + 301, 120.0)  # 5 minutes and 1 second ahead
    c.last_reading_ts = now + 301
    assert c.time_untrusted_at(now) is True and c.time_untrusted_at(now + 1) is False
    c._check_clock(now)
    assert co.CLOCK_ISSUE_ID in issues.open
    t = se.GlucoCraftLastReading.__new__(se.GlucoCraftLastReading)
    t.coordinator = c
    assert t.extra_state_attributes["time_untrusted"] is True
    assert t.extra_state_attributes["last_reading_limit"] is None


def test_exactly_five_minutes_ahead_is_still_trusted(tmp_path, issues):
    c = source_coordinator(tmp_path)
    now = int(time.time())
    add_reading(c.storage, now + 300, 120.0)
    c.last_reading_ts = now + 300
    assert c.time_untrusted_at(now) is False
    c._check_clock(now)
    assert co.CLOCK_ISSUE_ID not in issues.open


# ------------------------------------------------------------------------------------------------ report periods
@pytest.mark.parametrize("days", ["60", "abc", "0"])
def test_a_report_period_that_does_not_exist_is_refused(days, tmp_path, monkeypatch):
    monkeypatch.setattr(http_api, "web", types.SimpleNamespace(Response=lambda **kw: types.SimpleNamespace(**kw)))
    c = source_coordinator(tmp_path)
    request = types.SimpleNamespace(query={"days": days})
    _days, _options, refused = http_api._report_query(request, c)
    assert refused is not None and refused.status == 400
    assert refused.text == (
        f"This report period is not available: {days} days. The CGM Report can be created for 14, 30, 90 or 180 "
        "days. Check the card's or automation's settings."
    )


@pytest.mark.parametrize("query, days", [({}, 14), ({"days": "30"}, 30), ({"days": "180"}, 180)])
def test_a_report_period_that_exists_is_taken(query, days, tmp_path):
    c = source_coordinator(tmp_path)
    got, _options, refused = http_api._report_query(types.SimpleNamespace(query=query), c)
    assert refused is None and got == days


# ------------------------------------------------------------------------------------------------ diagnostics
def test_the_diagnostics_redact_the_custom_ranges_and_goals(monkeypatch):
    monkeypatch.setattr(dg, "async_redact_data", _redact)
    monkeypatch.setattr(dg, "REDACTED", "**REDACTED**")
    data = {
        C.CONF_RANGE_CRITICAL_LOW: 60,
        C.CONF_GOAL_TBR: 3,
        C.CONF_CUSTOM_RANGES_AND_GOALS: True,
        C.CONF_UNIT: "mg/dL",
    }
    out = dg.redact_settings(data)
    assert out[C.CONF_RANGE_CRITICAL_LOW] == dg.REDACTED and out[C.CONF_GOAL_TBR] == dg.REDACTED
    assert out[C.CONF_UNIT] == "mg/dL"
    assert dg.custom_flags(data) == {"custom_ranges_in_use": True, "custom_goals_set": True}
    assert dg.custom_flags({C.CONF_CUSTOM_RANGES_AND_GOALS: True}) == {
        "custom_ranges_in_use": False,
        "custom_goals_set": False,
    }


# ------------------------------------------------------------------------------------------------ the expiry entity
def _expiry_coordinator(tmp_path, value):
    c = source_coordinator(tmp_path)
    c._expiry_entity = "sensor.expiry"
    c._watch_source = True
    if value is not None:
        c.hass.states.d["sensor.expiry"] = types.SimpleNamespace(state=value, attributes={})
    return c


def test_an_expiry_entity_that_does_not_exist_is_pointed_out(tmp_path, issues):
    c = _expiry_coordinator(tmp_path, None)
    c._check_expiry_entity()
    assert issues.open[co.EXPIRY_ISSUE_ID]["translation_key"] == "expiry_entity_missing"


def test_an_expiry_long_past_while_readings_arrive_is_pointed_out(tmp_path, issues):
    past = (datetime.now(UTC) - timedelta(days=1, minutes=1)).isoformat()
    c = _expiry_coordinator(tmp_path, past)
    c.no_current_data = False
    c._check_expiry_entity()
    assert issues.open[co.EXPIRY_ISSUE_ID]["translation_key"] == "expiry_entity_past"
    c.no_current_data = True  # the readings stopped: the expiry may be right
    c._check_expiry_entity()
    assert co.EXPIRY_ISSUE_ID not in issues.open


def test_an_unrecognized_expiry_value_ten_checks_in_a_row(tmp_path, issues):
    c = _expiry_coordinator(tmp_path, "soon")
    for _ in range(9):
        c._check_expiry_entity()
    assert co.EXPIRY_ISSUE_ID not in issues.open
    c._check_expiry_entity()
    assert issues.open[co.EXPIRY_ISSUE_ID]["translation_key"] == "expiry_entity_unrecognized"


# ------------------------------------------------------------------------------------------------ the sensor limits
def test_a_number_below_a_lower_limit_set_by_hand_is_pointed_out(tmp_path, issues):
    c = source_coordinator(tmp_path, config={C.CONF_SENSOR_LOWER_LIMIT: "50"})
    assert c._source_reading("sensor.p", state("45")) == (50.0, "low")
    notice = issues.open[f"{co.HAND_LOW_ISSUE_PREFIX}sensor.p"]
    assert notice["translation_key"] == "hand_limit_low"
    assert notice["translation_placeholders"] == {"entity": "sensor.p", "value": "45 mg/dL", "limit": "50 mg/dL"}


def test_the_cgm_low_code_and_a_reading_at_the_limit_are_not_counted(tmp_path, issues):
    c = source_coordinator(tmp_path, config={C.CONF_SENSOR_LOWER_LIMIT: "50"})
    c._source_reading("sensor.p", state("39"))  # the CGM's own LO code
    c._source_reading("sensor.p", state("50"))  # at the limit set by hand: LO by design
    assert f"{co.HAND_LOW_ISSUE_PREFIX}sensor.p" not in issues.open


def test_readings_flat_at_the_automatic_upper_limit_are_pointed_out(tmp_path, issues):
    # three runs of three readings at exactly 500 mg/dL (the automatic upper limit), sent as numbers
    c = source_coordinator(tmp_path)
    now = int(time.time())
    t = now - 300 * 30
    for _ in range(3):
        for value in (500.0, 500.0, 500.0, 300.0):
            add_reading(c.storage, t, value, None, C.SOURCE_PRIMARY)
            t += 300
    c._cap_findings = c._scan_sensor_cap()
    assert c._cap_findings[C.SOURCE_PRIMARY]["upper"] == 500.0
    c._check_source_notices()
    notice = issues.open[f"{co.AT_UPPER_ISSUE_PREFIX}sensor.p"]
    assert notice["translation_key"] == "sensor_at_upper_limit"
    assert notice["translation_placeholders"]["value_mgdl"] == "500"
    assert f"{co.SENSOR_CAP_ISSUE_PREFIX}sensor.p" not in issues.open


# ------------------------------------------------------------------------------------------------ the phones
def test_phones_with_nothing_to_send_are_refused():
    assert cf._phone_errors({C.CONF_PHONE_TARGETS: ["notify.mobile_app_x"], C.CONF_PHONE_TYPES: []}) == {
        C.CONF_PHONE_TYPES: "phone_types_empty"
    }
    assert cf._phone_errors({C.CONF_PHONE_TARGETS: [], C.CONF_PHONE_TYPES: []}) == {}
    assert cf._phone_errors({C.CONF_PHONE_TARGETS: ["notify.x"], C.CONF_PHONE_TYPES: ["summaries"]}) == {}
    assert "phone_types_empty" in STRINGS["config"]["error"] and "phone_types_empty" in STRINGS["options"]["error"]


def test_the_slow_source_text_is_the_approved_one():
    issue = STRINGS["issues"]["slow_source"]
    assert issue["title"] == "Glucose source is too slow for live display"
    assert issue["description"].startswith(
        "The glucose source {entity} delivers a reading about every {minutes} minutes. GlucoCraft needs a reading "
        "every 5 minutes or more often to show a current value, so this source is not supported for live display."
    )
    assert issue["description"].endswith("This notice closes by itself when the readings arrive more often.")


def test_the_translations_are_a_copy_of_strings():
    root = Path(__file__).resolve().parents[1] / "custom_components/glucocraft"
    assert (root / "translations/en.json").read_bytes() == (root / "strings.json").read_bytes()


def test_a_number_above_an_upper_limit_set_by_hand_is_pointed_out(tmp_path, issues):
    # upper limit set by hand to 400: a 450 is shown as HI; the Dexcom code 401 alone is not counted
    c = source_coordinator(tmp_path, config={C.CONF_SENSOR_UPPER_LIMIT: "400"})
    c._src_domain = {"sensor.p": "dexcom"}
    assert c._source_reading("sensor.p", state("401")) == (400.0, "high")
    assert f"{co.HAND_HIGH_ISSUE_PREFIX}sensor.p" not in issues.open
    assert c._source_reading("sensor.p", state("450")) == (400.0, "high")
    notice = issues.open[f"{co.HAND_HIGH_ISSUE_PREFIX}sensor.p"]
    assert notice["translation_placeholders"] == {"entity": "sensor.p", "value": "450 mg/dL", "limit": "400 mg/dL"}


def test_readings_flat_at_the_automatic_lower_limit_are_pointed_out(tmp_path, issues):
    # three runs of three readings at exactly 40 mg/dL (the automatic lower limit), sent as numbers
    c = source_coordinator(tmp_path)
    t = int(time.time()) - 300 * 30
    for _ in range(3):
        for value in (40.0, 40.0, 40.0, 90.0):
            add_reading(c.storage, t, value, None, C.SOURCE_PRIMARY)
            t += 300
    c._cap_findings = c._scan_sensor_cap()
    c._check_source_notices()
    notice = issues.open[f"{co.AT_LOWER_ISSUE_PREFIX}sensor.p"]
    assert notice["translation_key"] == "sensor_at_lower_limit"
    assert notice["translation_placeholders"]["value_mmol"] == "2.2"
    # set by hand to 40: the readings at 40 are LO from then on, and the notice closes
    c._settings[C.CONF_SENSOR_LOWER_LIMIT] = "40"
    c._check_source_notices()
    assert f"{co.AT_LOWER_ISSUE_PREFIX}sensor.p" not in issues.open
