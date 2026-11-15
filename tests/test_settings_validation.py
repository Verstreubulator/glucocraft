# License: AGPL-3.0-only (LICENSE).
"""The settings in the backend: the two CGM Report options on the print action, the range
order and the nested goals checked per field in the setup and options screens, the weekly statistics on the
enough-data rule (a week counts only with sensor active at least 70% of it), and the sensor-active label in the entity
attributes."""

from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest
import yaml
from glucocraft import config_flow as cf
from glucocraft import const as C
from glucocraft import statistics as st
from glucocraft.storage import GlucoseReading
from glucocraft.units import UNIT_MGDL

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "glucocraft"
STRINGS = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------------------ report options
def test_strings_and_translation_are_byte_identical():
    assert (PKG / "strings.json").read_bytes() == (PKG / "translations" / "en.json").read_bytes()


def test_report_option_names():
    assert C.REPORT_OPTIONS == ("include_custom_appendix", "include_research_appendix")


def test_print_action_offers_both_appendices_off_by_default():
    fields = yaml.safe_load((PKG / "services.yaml").read_text(encoding="utf-8"))["print_report"]["fields"]
    texts = STRINGS["services"]["print_report"]["fields"]
    for key, name in (
        ("include_custom_appendix", "Include the custom ranges and goals appendix"),
        ("include_research_appendix", "Include the research measures appendix"),
    ):
        assert fields[key]["default"] is False and "boolean" in fields[key]["selector"]
        assert fields[key]["name"] == texts[key]["name"] == name
        assert fields[key]["description"] == texts[key]["description"]
    custom = texts["include_custom_appendix"]["description"]
    assert (
        "shows the statistics calculated with the ranges and goals in the settings, labeled as the user's own "
        "settings; the main report always uses the consensus ranges"
    ) in custom
    research = texts["include_research_appendix"]["description"]
    assert (
        "shows measures that research publications define but the clinical-care standards do not; no targets"
        in research
    )


def test_print_action_schema_accepts_the_options():
    src = (PKG / "__init__.py").read_text(encoding="utf-8")
    assert "vol.Optional(k, default=False): cv.boolean for k in REPORT_OPTIONS" in src


# ------------------------------------------------------------------------------------------------ range order
ALL = cf.RANGE_ORDER


@pytest.mark.parametrize(
    "change, wrong",
    [
        ({}, set()),  # the consensus defaults
        ({C.CONF_RANGE_CRITICAL_LOW: 70}, {C.CONF_RANGE_CRITICAL_LOW, C.CONF_RANGE_LOW}),
        ({C.CONF_RANGE_LOW: 75}, {C.CONF_RANGE_LOW, C.CONF_RANGE_TIGHT_LOW}),  # low above tight low
        ({C.CONF_RANGE_TIGHT_LOW: 140}, {C.CONF_RANGE_TIGHT_LOW, C.CONF_RANGE_TIGHT_HIGH}),
        ({C.CONF_RANGE_TIGHT_HIGH: 190}, {C.CONF_RANGE_TIGHT_HIGH, C.CONF_RANGE_HIGH}),
        ({C.CONF_RANGE_HIGH: 260}, {C.CONF_RANGE_HIGH, C.CONF_RANGE_CRITICAL_HIGH}),
        ({C.CONF_RANGE_LOW: 70, C.CONF_RANGE_TIGHT_LOW: 70}, set()),  # equal: allowed
        ({C.CONF_RANGE_TIGHT_HIGH: 180}, set()),  # equal: allowed
    ],
)
def test_range_errors_per_field(change, wrong):
    errors = cf._range_errors(change, {})
    assert {k for k in errors if k != "base"} == wrong
    assert ("base" in errors) is bool(wrong)


def test_every_error_code_has_a_text():
    """The ranges and goals errors of Configure (the setup has no ranges and goals screens, so none of its own)."""
    texts = STRINGS["options"]["error"]
    codes = {f"{k}_order" for k in ALL} | {f"{k}_order" for k, _ in cf.GOAL_PAIRS} | {"range_order"}
    assert codes <= set(texts)
    assert not codes & set(STRINGS["config"]["error"])
    for code in codes:
        assert texts[code].endswith(".") and "'" not in texts[code].replace("user's", "")  # no contractions


def _options_flow(data):
    flow = cf.GlucoCraftOptionsFlow(types.SimpleNamespace(data=dict(data)))
    flow._updates, flow._range_base = {}, None
    flow.async_show_form = lambda **kw: ("form", kw)
    finished = []

    async def finish():
        finished.append(dict(flow._updates))
        return ("done", {})

    flow._async_finish = finish
    return flow, finished


def test_options_ranges_screen_marks_the_wrong_fields():
    flow, finished = _options_flow({})
    shown = {k: cf._range_shown(None, k, UNIT_MGDL) for k in ALL}
    kind, kw = asyncio.run(flow.async_step_ranges({**shown, C.CONF_RANGE_HIGH: 260}))
    assert kind == "form" and finished == []
    assert kw["errors"] == {
        "base": "range_order",
        C.CONF_RANGE_HIGH: "range_high_order",
        C.CONF_RANGE_CRITICAL_HIGH: "range_critical_high_order",
    }
    kind, _ = asyncio.run(flow.async_step_ranges(shown))
    assert kind == "done" and finished[-1][C.CONF_RANGE_HIGH] == 180


# ------------------------------------------------------------------------------------------------ nested goals
def _goals(**kw):
    g = {
        C.CONF_GOAL_TIME_IN_TARGET_RANGE: 70,
        C.CONF_GOAL_TBR: 4,
        C.CONF_GOAL_TBR_CRITICAL: 1,
        C.CONF_GOAL_TAR: 25,
        C.CONF_GOAL_TAR_CRITICAL: 5,
        C.CONF_GOAL_CV: 36,
    }
    g.update(kw)
    return g


@pytest.mark.parametrize(
    "change, wrong",
    [
        ({}, set()),
        ({C.CONF_GOAL_TBR_CRITICAL: 4}, set()),  # equal: allowed
        ({C.CONF_GOAL_TBR_CRITICAL: 5}, {C.CONF_GOAL_TBR_CRITICAL}),
        ({C.CONF_GOAL_TAR_CRITICAL: 20, C.CONF_GOAL_TAR: 10}, {C.CONF_GOAL_TAR_CRITICAL}),
    ],
)
def test_goal_errors(change, wrong):
    assert set(cf._goal_errors(_goals(**change))) == wrong


def test_options_goals_screen_refuses_nested_goals_out_of_order():
    flow, finished = _options_flow({})
    kind, kw = asyncio.run(flow.async_step_goals(_goals(**{C.CONF_GOAL_TBR_CRITICAL: 6})))
    assert kind == "form" and kw["errors"] == {C.CONF_GOAL_TBR_CRITICAL: "goal_tbr_critical_order"} and finished == []
    kind, _ = asyncio.run(flow.async_step_goals(_goals()))
    assert kind == "done" and finished[-1][C.CONF_GOAL_TBR_CRITICAL] == 1
    assert finished[-1][C.CONF_GOAL_GMI] is None  # a cleared optional goal: no goal


# ------------------------------------------------------------------------------------------------ week rule
def test_weekly_statistics_use_the_enough_data_rule():
    """A week counts only with sensor active at least 70% of it (the enough-data rule): 70% of 7 days is 117.6 hours,
    which 5-minute readings cover with 1411.2 readings, so 1412 readings (each weighing 300 s: 423,600 s = 70.04%)
    count and 1411 (423,300 s = 69.99%) do not."""
    now = 1_800_000_000
    week = 7 * 86400
    rd = [GlucoseReading(t, 120) for t in range(now - week, now, 300)]  # week 0: full
    rd = [GlucoseReading(now - 2 * week + 300 * k, 110) for k in range(1411)] + rd  # 69.99%
    rd = [GlucoseReading(now - 3 * week + 300 * k, 100) for k in range(1412)] + rd  # 70.04%
    weeks = st.compute_weekly_trends(rd, num_weeks=4, detected_interval=300, now=now)
    assert weeks[0] is not None and weeks[0].mean == 120
    assert weeks[1] is None  # 1411 x 300 s / 604,800 s = 69.99%: not enough data
    assert weeks[2] is not None and weeks[2].mean == 100 and weeks[2].covered_seconds == 1412 * 300
    assert weeks[3] is None
    assert C.ENOUGH_DATA_PCT == 70.0


# ------------------------------------------------------------------------------------------------ sensor active
def test_sensor_active_label_and_sentence():
    assert C.sensor_active_label() == "Sensor active (data received)"  # the CGM Report's form
    assert C.sensor_active_note() == (
        "Close to, but not the same as, the CGM system's own figure: only the readings received are counted."
    )
    from glucocraft import sensor as S

    ent = object.__new__(S.GlucoCraftPeriodDataAvailability)
    assert ent.extra_state_attributes == {"label": C.sensor_active_label(), "note": C.sensor_active_note()}
    assert {"label", "note"} <= S.GlucoCraftPeriodDataAvailability._unrecorded_attributes
