# License: AGPL-3.0-only (LICENSE).
"""The very-low and the very-high threshold within the glucose sensor's limits (each sensor's own limit).

Rules under test (docs/SYSTEM_DESIGN.md, ranges; the Documentation, Readings Outside the Sensor's Range):
- The very-low threshold lies at or above the source's resolved lower limit and the very-high threshold at or below its
  resolved upper limit (limits.resolve: Automatic or the setting, per source); with a second source the stricter of
  the two applies (limits.threshold_span).
- The ranges screen (Configure; the setup has none) refuses a value outside them with a field error, in either unit,
  and compares at the precision it shows: 2.2 mmol/L is accepted for 40 mg/dL and stored as 40.
- A stored threshold that no longer fits (the source or its limit changed) is used at the sensor's limit
  (settings.ranges), and a Repairs notice says so; it closes by itself when the setting fits again.
- So a LO reading is always very low and a HI reading always very high, on every output; also a LO reading stored
  under an earlier, higher limit.
The stored ranges are in use only while the optional feature Custom ranges and goals is on: every entry here has it on.
"""

from __future__ import annotations

import asyncio
import json
import time
import types
from pathlib import Path

import pytest
from glucocraft import config_flow as cf
from glucocraft import const as C
from glucocraft import coordinator as co
from glucocraft import limits as L
from glucocraft import settings as S
from glucocraft import source_notices as sn

from test_clock_and_sources import FakeIssues
from test_config_flow_options import (
    _defaults_input,
    _options_flow,
    _stored,
    schemas,  # noqa: F401 (the fixture)
)
from test_settings_harness import PRIMARY, build, series, state, tz_patch  # noqa: F401 (the fixture)
from test_settings_harness import entry as default_entry

pytestmark = pytest.mark.usefixtures("tz_patch")

VLOW, VHIGH = C.CONF_RANGE_CRITICAL_LOW, C.CONF_RANGE_CRITICAL_HIGH
STRINGS = json.loads((Path(cf.__file__).parent / "strings.json").read_text(encoding="utf-8"))


def entry(**changes):
    """The default entry with Custom ranges and goals on (the stored ranges in use) and `changes`."""
    return default_entry(**{C.CONF_CUSTOM_RANGES_AND_GOALS: True, **changes})


@pytest.fixture
def issues(monkeypatch):
    fake = FakeIssues()
    monkeypatch.setattr(co, "ir", fake)
    monkeypatch.setattr(sn, "ir", fake)  # the timer's source notices (source_notices.py)
    return fake


# ------------------------------------------------------------------------------------------------ the span
def test_sensor_limit_span_per_source_and_the_stricter_of_two():
    automatic = L.resolve("auto", "auto", None)
    medtronic = L.resolve("auto", "auto", "carelink")
    dexcom = L.resolve("auto", "auto", "dexcom")
    assert L.threshold_span(automatic) == (40, 500)
    assert L.threshold_span(medtronic) == (50, 400)
    assert L.threshold_span(L.resolve("50", "450", None)) == (50, 450)
    assert L.threshold_span(automatic, medtronic) == (50, 400)  # the higher lower limit, the lower upper limit
    assert L.threshold_span(dexcom, L.resolve("auto", "450", None)) == (40, 400)
    assert L.threshold_span() == (L.DEFAULT_LOWER, L.DEFAULT_UPPER)
    assert L.threshold_span(None, automatic) == (40, 500)


def test_the_defaults_fit_every_sensor():
    """54 / 250 mg/dL lie within every sensor limit GlucoCraft knows."""
    for lower in L.LOWER_CHOICES:
        for upper in L.UPPER_CHOICES:
            assert S.sensor_limit_conflicts({}, (lower, upper)) == {}
            assert S.ranges({}, (lower, upper)) == S.ranges({})


# ------------------------------------------------------------------------------------------------ settings.ranges
@pytest.mark.parametrize(
    "stored, span, used",
    [
        ({VLOW: 35}, (40, 500), {VLOW: 40}),
        ({VLOW: 45}, (50, 400), {VLOW: 50}),
        ({VLOW: 40}, (40, 500), {VLOW: 40}),
        ({VLOW: 50}, (50, 400), {VLOW: 50}),
        ({VLOW: 60}, (50, 400), {VLOW: 60}),
        ({VHIGH: 400}, (40, 400), {VHIGH: 400}),
    ],
)
def test_a_threshold_outside_the_sensor_limits_is_used_at_the_limit(stored, span, used):
    data = entry(**stored)
    in_use = S.ranges(data, span)
    assert {k: in_use[k] for k in used} == used
    conflicts = S.sensor_limit_conflicts(data, span)
    assert conflicts == {k: (stored[k], used[k]) for k in used if stored[k] != used[k]}


def test_a_threshold_raised_to_the_low_limit_keeps_the_other_stored_limits():
    """A very-low limit raised to the sensor's limit of 50 meets a low limit of 50: only the very-low limit changes,
    and the low band is empty (never the default ranges; tests/test_sensor_limit_ranges.py)."""
    data = entry(**{VLOW: 45, C.CONF_RANGE_LOW: 50, C.CONF_RANGE_TIGHT_LOW: 70})
    assert S.ranges(data, (50, 400)) == {**S.ranges(data), VLOW: 50}
    assert S.sensor_limit_conflicts(data, (50, 400)) == {VLOW: (45, 50)}


# ------------------------------------------------------------------------------------------------ the coordinator
def test_the_coordinator_uses_the_limit_and_opens_a_repairs_notice(tmp_path, issues):
    run = build(tmp_path, entry(**{VLOW: 35}))
    c = run.c
    assert c.range_critical_low == 40 and run.config()["ranges"]["very_low"] == 40
    c._apply_ranges()
    notice = issues.open["range_below_sensor_limit"]
    assert notice["translation_key"] == "range_below_sensor_limit" and notice["is_fixable"] is False
    assert notice["translation_placeholders"] == {"stored": "35 mg/dL", "limit": "40 mg/dL", "used": "40 mg/dL"}
    assert "range_above_sensor_limit" not in issues.open


def test_the_notice_follows_a_limit_the_source_shows_and_closes_when_it_fits(tmp_path, issues):
    """A source that shows "Below 50 mg/dL" has the lower limit 50: a very-low limit of 45 is used at 50 from then on;
    with a fitting setting the notice closes."""
    run = build(tmp_path, entry(**{VLOW: 45}))
    c = run.c
    c._apply_ranges()
    assert c.range_critical_low == 45 and "range_below_sensor_limit" not in issues.open
    assert c._source_reading(PRIMARY, state("Below 50 mg/dL")) == (50.0, L.LIMIT_LOW)
    assert c.range_critical_low == 50
    assert issues.open["range_below_sensor_limit"]["translation_placeholders"]["used"] == "50 mg/dL"
    c._settings = {**c._settings, VLOW: 54}  # the setting changed in Configure (the reload reads it again)
    c._apply_ranges()
    assert c.range_critical_low == 54 and "range_below_sensor_limit" not in issues.open


def test_the_notice_in_mmol_l(tmp_path, issues):
    run = build(tmp_path, entry(**{VLOW: 35}, unit="mmol/L"))
    run.c._config_unit = "mmol/L"
    run.c._apply_ranges()
    assert issues.open["range_below_sensor_limit"]["translation_placeholders"] == {
        "stored": "1.9 mmol/L",
        "limit": "2.2 mmol/L",
        "used": "2.2 mmol/L",
    }


def test_the_notices_have_texts():
    for issue_id in co.RANGE_ISSUE_IDS.values():
        text = STRINGS["issues"][issue_id]["description"]
        assert all(p in text for p in ("{stored}", "{limit}", "{used}")) and "closes by itself" in text


# ------------------------------------------------------------------------------------------------ LO / HI always beyond
@pytest.mark.parametrize("lower, very_low", [("40", 40), ("40", 54), ("50", 50), ("50", 70)])
def test_lo_is_very_low_everywhere_for_every_allowed_threshold(tmp_path, lower, very_low):
    data = entry(**{C.CONF_SENSOR_LOWER_LIMIT: lower, VLOW: very_low})
    if very_low >= 70:
        data.update({C.CONF_RANGE_LOW: 80, C.CONF_RANGE_TIGHT_LOW: 80})
    now = int(time.time())
    rows = series(now, 1)
    rows[-1] = type(rows[-1])(rows[-1].timestamp, float(lower), L.LIMIT_LOW, rows[-1].source)
    run = build(tmp_path, data, readings=rows, now=now)
    assert run.c.range_critical_low == very_low
    assert run.c.glucose_zone == "very_low"
    assert run.sensor("is_critical_low")["state"] is True
    assert run.c.period_stats["6h"].time_critical_low > 0


@pytest.mark.parametrize("upper, very_high", [("400", 250), ("400", 400), ("450", 400)])
def test_hi_is_very_high_everywhere(tmp_path, upper, very_high):
    data = entry(**{C.CONF_SENSOR_UPPER_LIMIT: upper, VHIGH: very_high})
    now = int(time.time())
    rows = series(now, 1)
    rows[-1] = type(rows[-1])(rows[-1].timestamp, float(upper), L.LIMIT_HIGH, rows[-1].source)
    run = build(tmp_path, data, readings=rows, now=now)
    assert run.c.glucose_zone == "very_high"
    assert run.sensor("is_critical_high")["state"] is True
    assert run.c.period_stats["6h"].time_critical_high > 0


def test_a_lo_reading_stored_under_an_earlier_higher_limit_is_still_very_low():
    """LO stored at 50 (a source whose readings stopped at 50), a very-low limit of 45 now: still below it."""
    assert L.below(50.0, L.LIMIT_LOW, 45) and not L.above(50.0, L.LIMIT_LOW, 400)
    assert L.above(400.0, L.LIMIT_HIGH, 450) and not L.below(400.0, L.LIMIT_HIGH, 40)
    assert L.below(40.0, L.LIMIT_LOW, 40) and not L.below(40.0, None, 40)


# ------------------------------------------------------------------------------------------------ the ranges screen
def _ranges_input(shown, **values):
    return {**_defaults_input(shown[-1]["data_schema"]), **values}


@pytest.mark.parametrize(
    "unit, entered, stored",
    [("mg/dL", 40, 40), ("mg/dL", 41, 41), ("mmol/L", 2.2, 40), ("mmol/L", 2.3, 41.4)],
)
@pytest.mark.usefixtures("schemas")
def test_configure_accepts_the_sensor_limit_in_either_unit(unit, entered, stored):
    flow, shown, saved = _options_flow({**_stored(), C.CONF_UNIT: unit})
    asyncio.run(flow.async_step_ranges())
    asyncio.run(flow.async_step_ranges(_ranges_input(shown, **{VLOW: entered})))
    assert saved and saved[-1][VLOW] == stored


@pytest.mark.parametrize("unit, entered", [("mg/dL", 39), ("mg/dL", 30), ("mmol/L", 2.1), ("mmol/L", 1.7)])
@pytest.mark.usefixtures("schemas")
def test_configure_refuses_a_very_low_limit_below_the_sensor(unit, entered):
    flow, shown, saved = _options_flow({**_stored(), C.CONF_UNIT: unit})
    asyncio.run(flow.async_step_ranges())
    asyncio.run(flow.async_step_ranges(_ranges_input(shown, **{VLOW: entered})))
    assert not saved
    assert shown[-1]["errors"] == {VLOW: "range_critical_low_below_sensor_limit"}
    limit = "40 mg/dL" if unit == "mg/dL" else "2.2 mmol/L"
    assert shown[-1]["description_placeholders"]["lower_limit"] == limit


@pytest.mark.usefixtures("schemas")
def test_configure_uses_each_sources_own_limit():
    """With a second source whose lower limit is set to 50, the stricter limit applies: 45 is refused, 50 kept."""
    data = {**_stored(), C.CONF_SECONDARY_ENTITY: "sensor.cgm2", C.CONF_SECONDARY_SENSOR_LOWER_LIMIT: "50"}
    flow, shown, saved = _options_flow(data)
    asyncio.run(flow.async_step_ranges())
    asyncio.run(flow.async_step_ranges(_ranges_input(shown, **{VLOW: 45})))
    assert shown[-1]["errors"] == {VLOW: "range_critical_low_below_sensor_limit"} and not saved
    asyncio.run(flow.async_step_ranges(_ranges_input(shown, **{VLOW: 50})))
    assert saved[-1][VLOW] == 50


@pytest.mark.usefixtures("schemas")
def test_configure_asks_the_running_glucocraft_for_the_limits():
    """In Configure the running GlucoCraft's span is used (it knows the limits a source has shown itself)."""
    flow, shown, saved = _options_flow()
    flow._entry.runtime_data = types.SimpleNamespace(sensor_span=lambda: (50, 400))
    asyncio.run(flow.async_step_ranges())
    asyncio.run(flow.async_step_ranges(_ranges_input(shown, **{VLOW: 49})))
    assert shown[-1]["errors"] == {VLOW: "range_critical_low_below_sensor_limit"} and not saved


def test_the_very_high_limit_is_refused_above_the_sensor():
    """The screen's very-high box ends at 400 mg/dL, the lowest upper limit; the rule is checked all the same."""
    errors = cf._sensor_limit_errors({VHIGH: 380.0}, None, "mg/dL", (40, 350))
    assert errors == {VHIGH: "range_critical_high_above_sensor_limit"}
    assert cf._sensor_limit_errors({VHIGH: 350.0}, None, "mg/dL", (40, 350)) == {}
    assert cf._sensor_limit_errors({VHIGH: 22.2 * 18.016}, None, "mmol/L", (40, 400)) == {}
    assert cf._at_sensor_limits({VHIGH: 400.2, VLOW: 39.6}, (40, 400)) == {VHIGH: 400, VLOW: 40}


def test_the_field_explanations_name_the_rule():
    step = STRINGS["options"]["step"]["ranges"]  # Configure only: the setup has no ranges screen
    assert "{lower_limit}" in step["description"] and "{upper_limit}" in step["description"]
    assert "LO reading is always very low" in step["data_description"][VLOW]
    assert "HI reading is always very high" in step["data_description"][VHIGH]
    for key in cf.LIMIT_ERRORS.values():
        assert key in STRINGS["options"]["error"]
        assert key not in STRINGS["config"]["error"]
