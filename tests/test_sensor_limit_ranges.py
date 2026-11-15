# License: AGPL-3.0-only (LICENSE).
"""A sensor limit never replaces the range settings with the defaults, and the sensor comes first in Configure.

Rules under test (settings.ranges, settings.sensor_limit_conflicts, config_flow GlucoCraftOptionsFlow):
- When the source or its limit changes after the ranges were saved, only the forced limit changes: the very-low limit
  is raised to the sensor's lower limit (the very-high limit lowered to its upper limit); the other five stay as
  stored and the default ranges are never used because of a sensor limit. When that makes the very-low limit equal to
  the low limit, the low band is empty: the sensor reports every reading below its limit as LO, which is always very
  low. No time is ever negative, and every output (zone, binary sensors, statistics, summary, report, panel file)
  uses the stored limits except the forced one. The Repairs notice names the one value that differs.
- Configure > Glucose sources: a change whose sensor limits no longer fit the stored custom ranges is not saved at
  once; the Glucose ranges screen follows with the field errors, and both are saved together once the ranges fit.
- Unchanged: a stored set out of order (edited by hand) uses the default ranges as a whole (settings.stored_ranges).
The stored ranges are in use only while the optional feature Custom ranges and goals is on: every entry here has it on.
"""

from __future__ import annotations

import asyncio
import time
import types

import pytest
from glucocraft import config_flow as cf
from glucocraft import const as C
from glucocraft import coordinator as co
from glucocraft import limits as L
from glucocraft import settings as S
from glucocraft import source_notices as sn

from node_runner import needs_node
from test_clock_and_sources import FakeIssues
from test_config_flow_options import (
    _defaults_input,
    _options_flow,
    _stored,
    schemas,  # noqa: F401 (the fixture)
)
from test_dashboard_card_states import _band_key
from test_settings_harness import PRIMARY, build, series, tz_patch  # noqa: F401 (the fixture)
from test_settings_harness import entry as default_entry

pytestmark = pytest.mark.usefixtures("tz_patch")

VLOW, LOW, TLOW = C.CONF_RANGE_CRITICAL_LOW, C.CONF_RANGE_LOW, C.CONF_RANGE_TIGHT_LOW
THIGH, HIGH, VHIGH = C.CONF_RANGE_TIGHT_HIGH, C.CONF_RANGE_HIGH, C.CONF_RANGE_CRITICAL_HIGH
# the collision: very low 45 and low 50, and a sensor whose readings stop at 50 (its lower limit set to 50)
STORED = {VLOW: 45, LOW: 50, TLOW: 60, THIGH: 150, HIGH: 200, VHIGH: 300}
SPAN = (50, 400)


def entry(**changes):
    """The default entry with Custom ranges and goals on, the collision's ranges and `changes`."""
    return default_entry(**{C.CONF_CUSTOM_RANGES_AND_GOALS: True, **STORED, **changes})


@pytest.fixture
def issues(monkeypatch):
    fake = FakeIssues()
    monkeypatch.setattr(co, "ir", fake)
    monkeypatch.setattr(sn, "ir", fake)  # the timer's source notices (source_notices.py)
    return fake


def _with_lo_last(now: int):
    """A day of the artificial series whose newest reading is LO, stored at the lower limit of 50 mg/dL."""
    rows = series(now, 1)
    rows[-1] = type(rows[-1])(rows[-1].timestamp, 50.0, L.LIMIT_LOW, rows[-1].source)
    return rows


# ------------------------------------------------------------------------------------------------ (1) the collision
def test_only_the_forced_limit_changes_never_the_default_ranges():
    data = entry()
    in_use = S.ranges(data, SPAN)
    assert in_use == {**STORED, VLOW: 50}  # the five others as stored; no default range anywhere
    assert in_use != S.RANGE_DEFAULTS and in_use[LOW] == in_use[VLOW] == 50  # the low band is empty
    assert S.sensor_limit_conflicts(data, SPAN) == {VLOW: (45, 50)}  # the notice names the value in use
    assert S.ranges(data) == STORED  # without the sensor's limits: as stored
    assert data[VLOW] == 45  # the stored settings are never rewritten


def test_the_high_side_and_both_sides_at_once():
    """The very-high limit lowered to the upper limit; with both limits forced, only those two change."""
    data = entry(**{VHIGH: 400})
    assert S.ranges(data, (40, 400)) == {**STORED, VHIGH: 400}
    data = entry(**{VLOW: 35, LOW: 70, TLOW: 70})
    assert S.ranges(data, (50, 400)) == {**STORED, VLOW: 50, LOW: 70, TLOW: 70}


def test_coordinator_zone_time_low_and_the_repairs_notice(tmp_path, issues):
    now = int(time.time())
    run = build(tmp_path, entry(**{C.CONF_SENSOR_LOWER_LIMIT: "50"}), readings=_with_lo_last(now), now=now)
    c = run.c
    assert c.sensor_span() == (50, 500)  # the lower limit set by hand, the upper limit Automatic
    used = (c.range_critical_low, c.range_low, c.range_ideal_low, c.range_ideal_high, c.range_high)
    assert used + (c.range_critical_high,) == (50, 50, 60, 150, 200, 300)
    assert c.glucose_zone == "very_low"  # the LO reading
    for label, st in c.period_stats.items():
        if st is None:
            continue
        assert st.time_low == 0, label  # no reading lies in the empty low band; never negative
        assert st.time_below_low == pytest.approx(st.time_critical_low), label
        total = st.time_critical_low + st.pct_time_in_target_range + st.time_high + st.time_critical_high
        assert total == pytest.approx(100.0), label  # every reading in exactly one band
    assert c.period_stats["6h"].time_critical_low > 0
    c._apply_ranges()
    notice = issues.open["range_below_sensor_limit"]["translation_placeholders"]
    assert notice == {"stored": "45 mg/dL", "limit": "50 mg/dL", "used": "50 mg/dL"}
    assert "range_above_sensor_limit" not in issues.open


def test_a_lo_reading_is_very_low_and_below_low_on_every_sensor(tmp_path):
    now = int(time.time())
    run = build(tmp_path, entry(**{C.CONF_SENSOR_LOWER_LIMIT: "50"}), readings=_with_lo_last(now), now=now)
    assert run.sensor("is_critical_low")["state"] is True
    assert run.sensor("is_low")["state"] is True
    assert run.sensor("in_range")["state"] is False
    assert L.below(50.0, L.LIMIT_LOW, 50) and not L.below(50.0, None, 50)  # a measured 50 is not very low


def test_a_measured_reading_at_the_limit_is_in_range_as_with_the_stored_low_limit(tmp_path):
    """With the lower limit Automatic (CareLink: 50) a reading of 50 is measured: in range, as with the stored low
    limit of 50; neither low nor very low."""
    now = int(time.time())
    rows = series(now, 1)
    rows[-1] = type(rows[-1])(rows[-1].timestamp, 50.0, None, rows[-1].source)
    run = build(tmp_path, entry(**{C.CONF_SENSOR_LOWER_LIMIT: "50"}), readings=rows, now=now)
    assert run.c.glucose_zone not in ("very_low", "low")
    assert run.sensor("in_range")["state"] is True
    assert run.sensor("is_low")["state"] is False and run.sensor("is_critical_low")["state"] is False


# ------------------------------------------------------------------------------------------------ (2) every output
def test_every_output_uses_the_stored_limits_except_the_forced_one(tmp_path):
    now = int(time.time())
    run = build(tmp_path, entry(**{C.CONF_SENSOR_LOWER_LIMIT: "50"}), readings=series(now, 15), now=now)
    expected = {"very_low": 50, "low": 50, "tight_low": 60, "tight_high": 150, "high": 200, "very_high": 300}
    # the ranges sensor (the cards and the panels read it) and the Configuration sensor (the dashboard)
    attrs = run.sensor("using_custom_ranges")["attributes"]
    assert {k: attrs[f"range_{k}"] for k in expected} == expected
    assert run.config()["ranges"] == {**run.config()["ranges"], **expected}
    # the binary sensors judge by the same limits
    thresholds = {k: run.sensor(k)["attributes"]["threshold"] for k in ("is_critical_low", "is_low", "is_high")}
    assert thresholds == {"is_critical_low": 50, "is_low": 50, "is_high": 200}
    assert run.sensor("is_critical_high")["attributes"]["threshold"] == 300
    # the panel file: very low, very high, low, high, tight range from, tight range to (panel_feed._RANGES_FMT)
    assert tuple(run.feed()["ranges"]) == (50, 300, 50, 200, 60, 150)
    # the daily summary's custom line: in range 50 to 200, below 50
    daily = run.daily()
    assert "in range 50 to 200" in daily and "below 50:" in daily
    # the CGM Report's custom appendix
    report = run.report(custom=True)
    appendix = report["custom_appendix"]
    assert {k: appendix["ranges"][k] for k in expected} == expected
    stats = appendix["stats"]
    assert stats["very_low"] > 0 and stats["below_low"] == stats["very_low"]  # nothing in the empty low band


@needs_node
def test_the_graphs_band_key_has_no_item_for_the_empty_low_band():
    """The dashboard's band key under the graphs: no "50–49" for the empty low band (as the Documentation's Colors
    table, which leaves out a span whose end lies below its start); the bands drawn have no height there."""
    ranges = {"very_low": 50, "low": 50, "tight_low": 60, "tight_high": 150, "high": 200, "very_high": 300}
    assert _band_key(ranges) == ["under 50", "50–59", "60–150", "151–200", "201–300", "over 300"]


def test_the_low_and_high_limits_can_never_be_moved_by_a_sensor_limit():
    """The spans make the very-low limit the only one a lower limit can move and the very-high limit the only one an
    upper limit can move: the lowest low limit is the highest lower limit, the highest high limit lies below the lowest
    upper limit. A change here needs Repairs notice texts for the low and the high limit (sensor_limit_conflicts)."""
    assert max(L.LOWER_CHOICES) <= S.RANGE_LIMITS[LOW][0]
    assert min(L.UPPER_CHOICES) > S.RANGE_LIMITS[HIGH][1]
    lowest_low = {**STORED, VLOW: 30, LOW: 50, TLOW: 50}
    highest_high = {**STORED, HIGH: 300, VHIGH: 400}
    for stored in (lowest_low, highest_high):
        data = entry(**stored)
        for span in ((lo, hi) for lo in L.LOWER_CHOICES for hi in L.UPPER_CHOICES):
            assert set(S.sensor_limit_conflicts(data, span)) <= {VLOW, VHIGH}
            assert set(S.sensor_limit_conflicts(data, span)) <= set(co.RANGE_ISSUE_IDS)


# ------------------------------------------------------------------------------------------------ (3) sensor first
def _flow(monkeypatch, platforms=None, **stored):
    """A Configure flow on an entry with the collision's ranges (very low 45, low 50); `platforms`: the integration
    of each source entity (carelink: lower limit 50)."""
    monkeypatch.setattr(cf, "_source_platform", lambda hass, e: (platforms or {}).get(e))
    return _options_flow({**_stored(), **STORED, **stored})


def _sources(flow, shown, **values):
    asyncio.run(flow.async_step_sources())
    assert shown[-1]["step_id"] == "sources"
    return asyncio.run(flow.async_step_sources({**_defaults_input(shown[-1]["data_schema"]), **values}))


def _ranges_input(shown, **values):
    return {**_defaults_input(shown[-1]["data_schema"]), **values}


@pytest.mark.usefixtures("schemas")
def test_a_source_with_a_higher_lower_limit_leads_to_the_ranges_screen(monkeypatch):
    flow, shown, saved = _flow(monkeypatch, {"sensor.cgm2": "carelink"})
    kind, kw = _sources(flow, shown, glucose_entity="sensor.cgm2")
    assert kind == "form" and kw["step_id"] == "ranges" and not saved  # nothing saved yet
    assert kw["errors"] == {VLOW: "range_critical_low_below_sensor_limit"}
    assert kw["description_placeholders"]["lower_limit"] == "50 mg/dL"  # the new source's limit
    # the screen says why it opened, as its first paragraph
    assert kw["description_placeholders"]["source_note"].startswith(
        "The glucose source or its sensor limit was changed"
    )
    # 50 meets the low limit of 50: the existing order errors, still nothing saved
    asyncio.run(flow.async_step_ranges(_ranges_input(shown, **{VLOW: 50})))
    assert shown[-1]["errors"][VLOW] == "range_critical_low_order" and not saved
    # a fitting set: the source and the ranges are saved together
    asyncio.run(flow.async_step_ranges(_ranges_input(shown, **{VLOW: 50, LOW: 55})))
    assert len(saved) == 1
    assert saved[0]["glucose_entity"] == "sensor.cgm2" and saved[0][VLOW] == 50 and saved[0][LOW] == 55


@pytest.mark.usefixtures("schemas")
def test_a_lower_limit_set_by_hand_leads_to_the_ranges_screen(monkeypatch):
    flow, shown, saved = _flow(monkeypatch)
    kind, kw = _sources(flow, shown, sensor_lower_limit="50")
    assert (
        kind == "form" and kw["step_id"] == "ranges" and kw["errors"] == {VLOW: "range_critical_low_below_sensor_limit"}
    )
    asyncio.run(flow.async_step_ranges(_ranges_input(shown, **{VLOW: 49})))
    assert shown[-1]["errors"] == {VLOW: "range_critical_low_below_sensor_limit"} and not saved
    asyncio.run(flow.async_step_ranges(_ranges_input(shown, **{VLOW: 50, LOW: 60})))
    assert saved[-1]["sensor_lower_limit"] == "50" and saved[-1][VLOW] == 50


@pytest.mark.usefixtures("schemas")
def test_a_fitting_change_saves_as_before(monkeypatch):
    # a source change whose limits fit the stored ranges
    flow, shown, saved = _flow(monkeypatch)
    kind, _ = _sources(flow, shown, glucose_entity="sensor.cgm2")
    assert kind == "done" and saved[-1]["glucose_entity"] == "sensor.cgm2" and VLOW not in saved[-1]
    # a very-low limit at or above the new limit already fits
    flow, shown, saved = _flow(monkeypatch, **{VLOW: 50, LOW: 60})
    kind, _ = _sources(flow, shown, sensor_lower_limit="50")
    assert kind == "done" and saved[-1]["sensor_lower_limit"] == "50"
    # Custom ranges and goals off: the default ranges are in use, and they fit every sensor
    flow, shown, saved = _flow(monkeypatch, **{C.CONF_CUSTOM_RANGES_AND_GOALS: False})
    kind, _ = _sources(flow, shown, sensor_lower_limit="50")
    assert kind == "done" and saved[-1]["sensor_lower_limit"] == "50"


@pytest.mark.usefixtures("schemas")
def test_a_misfit_that_was_there_before_does_not_hold_up_another_change(monkeypatch):
    """The limits stay as they are (the Repairs notice already says so): another sources change saves at once."""
    flow, shown, saved = _flow(monkeypatch, sensor_lower_limit="50")
    kind, _ = _sources(flow, shown, sensor_lower_limit="50", stale_threshold=20.0)
    assert kind == "done" and saved[-1][C.CONF_STALE_THRESHOLD] == 20


@pytest.mark.usefixtures("schemas")
def test_the_menu_opens_the_ranges_screen_as_before(monkeypatch):
    """From the menu, the ranges screen opens without errors and checks the limits in use."""
    flow, shown, saved = _flow(monkeypatch, **{VLOW: 54, LOW: 70, TLOW: 70})
    asyncio.run(flow.async_step_ranges())
    assert shown[-1]["step_id"] == "ranges" and not shown[-1]["errors"]
    assert shown[-1]["description_placeholders"]["source_note"] == ""  # opened from the menu: no note
    asyncio.run(flow.async_step_ranges(_ranges_input(shown, **{VLOW: 45})))
    assert saved[-1][VLOW] == 45 and "glucose_entity" not in saved[-1]


@pytest.mark.usefixtures("schemas")
def test_the_running_glucocraft_judges_the_new_settings(monkeypatch):
    """With GlucoCraft running, its sensor_span(data) judges the settings entered (it knows the limits a source has
    shown itself); its sensor_span() the settings in use."""
    flow, shown, saved = _flow(monkeypatch)
    asked = []

    def sensor_span(data=None):
        asked.append(data)
        return (40, 500) if data is None else (50, 400)

    flow._entry.runtime_data = types.SimpleNamespace(sensor_span=sensor_span)
    kind, kw = _sources(flow, shown, stale_threshold=20.0)
    assert kind == "form" and kw["step_id"] == "ranges" and not saved
    assert any(d is not None and d[C.CONF_STALE_THRESHOLD] == 20 for d in asked)


def test_the_coordinator_judges_unsaved_settings_with_what_it_knows(tmp_path):
    run = build(tmp_path, entry())
    c = run.c
    assert c.sensor_span() == (40, 500)
    assert c.sensor_span({**run.data, C.CONF_SENSOR_LOWER_LIMIT: "50"}) == (50, 500)
    c._learned_limits[PRIMARY] = {"lower": 50}  # the source showed "Below 50 mg/dL"
    assert c.sensor_span(dict(run.data)) == c.sensor_span() == (50, 500)
    second = {**run.data, C.CONF_SECONDARY_ENTITY: "sensor.other", C.CONF_SECONDARY_SENSOR_UPPER_LIMIT: "400"}
    assert c.sensor_span(second) == (50, 400)  # the stricter of the two sources
    assert c.sensor_span({**second, C.CONF_SECONDARY_ENTITY: PRIMARY}) == (50, 500)  # the same source twice


# ------------------------------------------------------------------------------------------------ (4) unchanged rule
def test_a_hand_edited_set_out_of_order_still_uses_the_default_ranges():
    data = entry(**{VLOW: 60, LOW: 55})  # very low above low: no settings screen saves this
    assert S.stored_ranges(data) == S.RANGE_DEFAULTS
    assert S.ranges(data) == S.RANGE_DEFAULTS
    assert S.ranges(data, SPAN) == S.RANGE_DEFAULTS  # the defaults fit every sensor's limits
    assert S.sensor_limit_conflicts(data, SPAN) == {}
