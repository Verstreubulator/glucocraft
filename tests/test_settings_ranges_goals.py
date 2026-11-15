# License: AGPL-3.0-only (LICENSE).
"""The six ranges and the eight goals: every range limit at its span's ends and the default, in both units; every
goal at its bounds and the optional goals cleared; the edge cases (minimum spacing, equal limits, mmol/L limits that
round across the mg/dL boundary, LO / HI readings at a range limit); and pairwise combinations of ranges, goals,
weekly pages, display word and unit.

The outputs checked: the zone, the binary sensors, the statistics, the Configuration attributes, the HTTP API, the
panel feed (RANGES, the GCX1 targets and lines), the summaries and the CGM Report (the main
report on the consensus ranges, the custom appendix on the ranges and goals in the settings). Every entry here has the
optional feature Custom ranges and goals on (entry below); tests/test_optional_features.py covers it off.
"""

from __future__ import annotations

import time

import pytest
from glucocraft import const as C
from glucocraft import http_api  # the API publishes unrounded values (http_api._glucose)
from glucocraft.limits import DEFAULT_LOWER
from glucocraft.units import from_mgdl, round_half_up, to_mgdl, to_mgdl_setting

from test_settings_harness import (
    assert_pairs_covered,
    build,
    pairwise,
    series,
    state,
    tz_patch,  # noqa: F401 (the fixture)
)
from test_settings_harness import entry as default_entry


def entry(**changes):
    """The default entry with Custom ranges and goals on (the ranges and goals in the settings are in use) and
    `changes`."""
    return default_entry(**{C.CONF_CUSTOM_RANGES_AND_GOALS: True, **changes})


pytestmark = pytest.mark.usefixtures("tz_patch")

SPAN = {  # mg/dL: the span of each limit (the setting screens' number boxes)
    C.CONF_RANGE_CRITICAL_LOW: (30, 70),
    C.CONF_RANGE_LOW: (50, 100),
    C.CONF_RANGE_TIGHT_LOW: (50, 100),
    C.CONF_RANGE_TIGHT_HIGH: (100, 200),
    C.CONF_RANGE_HIGH: (120, 300),
    C.CONF_RANGE_CRITICAL_HIGH: (180, 400),
}
ORDER = (
    C.CONF_RANGE_CRITICAL_LOW,
    C.CONF_RANGE_LOW,
    C.CONF_RANGE_TIGHT_LOW,
    C.CONF_RANGE_TIGHT_HIGH,
    C.CONF_RANGE_HIGH,
    C.CONF_RANGE_CRITICAL_HIGH,
)
PUBLIC = dict(zip(ORDER, ("very_low", "low", "tight_low", "tight_high", "high", "very_high"), strict=True))
FEED_ORDER = (  # GLU2 RANGES: critical_min, critical_max, normal_min, normal_max, ideal_min, ideal_max
    C.CONF_RANGE_CRITICAL_LOW,
    C.CONF_RANGE_CRITICAL_HIGH,
    C.CONF_RANGE_LOW,
    C.CONF_RANGE_HIGH,
    C.CONF_RANGE_TIGHT_LOW,
    C.CONF_RANGE_TIGHT_HIGH,
)
DEFAULTS = {k: v for k, v in zip(ORDER, (54, 70, 70, 140, 180, 250), strict=True)}
# The lowest value of each limit in use: the very-low limit cannot be below the sensor's lower limit, 40 mg/dL for a
# source without a limit of its own (tests/test_range_sensor_limits.py tests the stored values below it)
LOWEST = {**{k: lo for k, (lo, _hi) in SPAN.items()}, C.CONF_RANGE_CRITICAL_LOW: DEFAULT_LOWER}


def _ordered(ranges: dict) -> bool:
    v = [ranges[k] for k in ORDER]
    return v[0] < v[1] <= v[2] < v[3] <= v[4] < v[5]


def range_cases():
    """Each limit at both ends of its span and one value between, where the order allows it."""
    out = []
    for key, (_lo, hi) in SPAN.items():
        lo = LOWEST[key]
        for value in (lo, (lo + hi) // 2, hi):
            r = {**DEFAULTS, key: value}
            # keep the order: move the neighbours just enough
            i = ORDER.index(key)
            for j in range(i + 1, 6):
                strict = j in (1, 3, 5)
                need = r[ORDER[j - 1]] + (1 if strict else 0)
                r[ORDER[j]] = max(r[ORDER[j]], need)
            for j in range(i - 1, -1, -1):
                strict = j in (0, 2, 4)
                need = r[ORDER[j + 1]] - (1 if strict else 0)
                r[ORDER[j]] = min(r[ORDER[j]], need)
            if _ordered(r) and all(LOWEST[k] <= r[k] <= SPAN[k][1] for k in ORDER):
                out.append(pytest.param(r, id=f"{key}={value}"))
    return out


def zone_of(mg: float, r: dict) -> str:
    """The zone, written out from the documented rule (const.py ZONES; coordinator.glucose_zone)."""
    if mg < r[C.CONF_RANGE_CRITICAL_LOW]:
        return "very_low"
    if mg < r[C.CONF_RANGE_LOW]:
        return "low"
    if mg < r[C.CONF_RANGE_TIGHT_LOW]:
        return "near_low"
    if mg <= r[C.CONF_RANGE_TIGHT_HIGH]:
        return "tight_range"
    if mg <= r[C.CONF_RANGE_HIGH]:
        return "near_high"
    if mg <= r[C.CONF_RANGE_CRITICAL_HIGH]:
        return "high"
    return "very_high"


def _set_current(run, mg, limit=None):
    c = run.c
    c.current_glucose, c.current_limit = float(mg), limit
    c.last_reading_ts, c.no_current_data = int(time.time()) - 60, False


@pytest.mark.parametrize("ranges", range_cases())
@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
def test_each_range_limit_on_every_output(tmp_path, ranges, unit):
    run = build(tmp_path, entry(**ranges, unit=unit))
    c = run.c
    custom = ranges != DEFAULTS
    cfg = run.config()
    assert cfg["ranges"] == {PUBLIC[k]: ranges[k] for k in ORDER}
    assert c.uses_custom_ranges() is custom and run.sensor("configuration")["state"] == (
        "custom_ranges" if custom else "consensus_ranges"
    )
    assert run.sensor("using_custom_ranges")["state"] is custom
    api = run.api_data("24h")["ranges"]
    assert {k: api[PUBLIC[k]] for k in ORDER} == {k: http_api._glucose(v, unit) for k, v in ranges.items()}
    assert api["is_default"] is (not custom)
    assert run.feed()["ranges"] == tuple(float(ranges[k]) for k in FEED_ORDER)
    # the zone and the binary sensors at each limit: below, on, above
    for key in ORDER:
        for mg in (ranges[key] - 1, ranges[key], ranges[key] + 1):
            _set_current(run, mg)
            assert c.glucose_zone == zone_of(mg, ranges), (key, mg)
            assert run.sensor("is_low")["state"] is (mg < ranges[C.CONF_RANGE_LOW])
            assert run.sensor("is_critical_low")["state"] is (mg < ranges[C.CONF_RANGE_CRITICAL_LOW])
            assert run.sensor("is_high")["state"] is (mg > ranges[C.CONF_RANGE_HIGH])
            assert run.sensor("is_critical_high")["state"] is (mg > ranges[C.CONF_RANGE_CRITICAL_HIGH])
            assert run.sensor("in_range")["state"] is (ranges[C.CONF_RANGE_LOW] <= mg <= ranges[C.CONF_RANGE_HIGH])
            assert run.sensor("in_tight_range")["state"] is (
                ranges[C.CONF_RANGE_TIGHT_LOW] <= mg <= ranges[C.CONF_RANGE_TIGHT_HIGH]
            )
    # the statistics on the ranges in use; the consensus statistics never change
    s = c.period_stats["14d"]
    total = s.time_critical_low + s.time_low + s.pct_time_in_target_range + s.time_high + s.time_critical_high
    assert abs(total - 100) <= 0.3
    agp = c.period_stats_agp["14d"]
    assert run.sensor("14d_consensus")["state"] == round_half_up(agp.pct_time_in_target_range, 1)  # state: one decimal
    # the summary names the ranges in the settings only when they are custom
    assert ("Custom ranges:" in run.daily()) is custom
    # the report: the main report always on the consensus ranges, the custom appendix on these
    rep = run.report(14, custom=True)
    assert rep["custom_appendix"]["ranges"] == {PUBLIC[k]: ranges[k] for k in ORDER}
    assert rep["custom_appendix"]["ranges_differ"] is custom


def test_the_main_report_never_uses_the_ranges_in_the_settings(tmp_path):
    now = int(time.time())
    base = build(tmp_path / "a", now=now)
    other = build(tmp_path / "b", entry(range_low=80, range_high=160, range_critical_high=220), now=now)
    a, b = base.report(30, full=True), other.report(30, full=True)
    for key in ("standard", "period", "previous", "lows", "highs", "monthly"):
        assert a[key] == b[key], key
    assert base.c.period_stats_agp == other.c.period_stats_agp


# ------------------------------------------------------------------------------------------------ a settings change
def _limit_sensors(run, keys=("is_low", "in_range", "in_tight_range")) -> dict:
    """The states of the binary sensors `keys` and the threshold and limit source of `keys[0]`, as written now."""
    attrs = run.sensor(keys[0])["attributes"]
    return {**{k: run.sensor(k)["state"] for k in keys}, "threshold": attrs["threshold"], "from": attrs["limit_source"]}


def test_the_range_sensors_follow_a_range_change_at_once(tmp_path):
    """A range saved in Configure, and the optional feature Custom ranges and goals turned off, reach the binary sensors
    at the reload (every options save ends with one) from the stored current value, without waiting for a new
    reading."""
    now = int(time.time())
    run = build(tmp_path, entry(), readings=series(now, 1, value=lambda i: 67.0))
    consensus = {
        "is_low": True,
        "in_range": False,
        "in_tight_range": False,
        "threshold": 70.0,
        "from": "consensus ranges",
    }
    assert _limit_sensors(run) == consensus
    lower = {C.CONF_RANGE_LOW: 65, C.CONF_RANGE_TIGHT_LOW: 65}
    after = run.reload(entry(**lower))
    assert after.c.storage.readings[-1].timestamp == run.c.storage.readings[-1].timestamp  # no new reading
    assert _limit_sensors(after) == {
        "is_low": False,
        "in_range": True,
        "in_tight_range": True,
        "threshold": 65.0,
        "from": "custom ranges",
    }
    # the feature off: the consensus ranges again, whatever is stored
    assert _limit_sensors(after.reload(entry(**lower, **{C.CONF_CUSTOM_RANGES_AND_GOALS: False}))) == consensus


def test_the_range_sensors_follow_a_sensor_limit_change_at_once(tmp_path):
    """A sensor lower limit saved in Configure moves a very-low threshold below it to the limit (settings.ranges), and
    Glucose very low follows at the reload from the stored current value."""
    now = int(time.time())
    data = entry(**{C.CONF_RANGE_CRITICAL_LOW: 45})
    run = build(tmp_path, data, readings=series(now, 1, value=lambda i: 48.0))
    keys = ("is_critical_low", "is_low")
    expected = {"is_critical_low": False, "is_low": True, "threshold": 45.0, "from": "custom ranges"}
    assert _limit_sensors(run, keys) == expected
    after = run.reload({**data, C.CONF_SENSOR_LOWER_LIMIT: "50"})
    assert _limit_sensors(after, keys) == {**expected, "is_critical_low": True, "threshold": 50.0}


# ------------------------------------------------------------------------------------------------ minimum spacing
MIN_SPACING = {
    C.CONF_RANGE_CRITICAL_LOW: 69,
    C.CONF_RANGE_LOW: 70,
    C.CONF_RANGE_TIGHT_LOW: 70,  # equal to the low limit (allowed)
    C.CONF_RANGE_TIGHT_HIGH: 180,  # equal to the high limit (allowed)
    C.CONF_RANGE_HIGH: 180,
    C.CONF_RANGE_CRITICAL_HIGH: 181,
}


@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
def test_ranges_at_the_minimum_spacing(tmp_path, unit):
    run = build(tmp_path, entry(**MIN_SPACING, unit=unit))
    c = run.c
    zones = set()
    for mg in range(40, 320):
        _set_current(run, mg)
        zones.add(c.glucose_zone)
        assert c.glucose_zone == zone_of(mg, MIN_SPACING)
    assert "near_low" not in zones and "near_high" not in zones  # empty when the limits are equal
    assert {"very_low", "low", "tight_range", "high", "very_high"} <= zones
    s = c.period_stats["14d"]
    assert s.pct_time_in_ideal_range == s.pct_time_in_target_range  # the tight range is the range
    total = s.time_critical_low + s.time_low + s.pct_time_in_target_range + s.time_high + s.time_critical_high
    assert abs(total - 100) <= 0.3
    for d in run.feed()["ext"]["standards"][-1]["desc"]:
        assert len(d) < 64
    assert (
        run.report(14, custom=True)["custom_appendix"]["stats"]["tir"]
        == run.report(14, custom=True)["custom_appendix"]["stats"]["titr"]
    )


# ------------------------------------------------------------------------------------------------ mmol/L limits
@pytest.mark.parametrize("entered", [3.0, 3.9, 4.0, 4.4, 7.8, 8.0, 10.0, 10.1, 13.9, 14.0])
def test_mmol_limits_stored_as_a_reading_of_that_value_is(entered):
    """A limit entered in mmol/L is stored as the mg/dL value a reading of the same mmol/L value is stored as, so a
    reading equal to the limit lies on the same side as the limit everywhere (consensus values: their pairs)."""
    stored = to_mgdl_setting(entered, "mmol/L")
    assert stored == to_mgdl(entered, "mmol/L")
    assert from_mgdl(stored, "mmol/L") == entered


@pytest.mark.parametrize("limit_mmol,reading_mmol", [(4.0, 4.0), (4.4, 4.4), (8.0, 8.0), (10.1, 10.1)])
def test_a_mmol_reading_on_a_mmol_limit_is_on_the_in_range_side(tmp_path, limit_mmol, reading_mmol):
    low = limit_mmol < 7
    stored = to_mgdl_setting(limit_mmol, "mmol/L")
    key = C.CONF_RANGE_LOW if low else C.CONF_RANGE_HIGH
    data = entry(**{key: stored}, unit="mmol/L")
    if low:
        data[C.CONF_RANGE_TIGHT_LOW] = max(stored, 70)
    else:
        data[C.CONF_RANGE_TIGHT_HIGH] = min(stored, 140)
    run = build(tmp_path, data)
    value, _ = run.c._source_reading("sensor.p", state(reading_mmol, unit_of_measurement="mmol/L"))
    _set_current(run, value)
    assert run.sensor("in_range")["state"] is True
    assert run.c.glucose_zone in ("tight_range", "near_high", "near_low")
    assert run.sensor("is_low")["state"] is False and run.sensor("is_high")["state"] is False  # on the limit


# ------------------------------------------------------------------------------------------------ LO / HI at a limit
@pytest.mark.parametrize(
    "case",
    [
        pytest.param(("high", "400", 250, 400.0), id="HI 400 above a very-high limit of 250"),
        pytest.param(("low", "40", 54, 40.0), id="LO 40 below a very-low limit of 54"),
        pytest.param(("high", "400", 400, 400.0), id="HI 400 at a very-high limit of 400"),
        pytest.param(("low", "40", 40, 40.0), id="LO 40 at a very-low limit of 40"),
        pytest.param(("low", "50", 50, 50.0), id="LO 50 at a very-low limit of 50"),
    ],
)
def test_a_limit_reading_lands_in_the_same_zone_everywhere(tmp_path, case):
    kind, setting, very, value = case
    key = C.CONF_SENSOR_UPPER_LIMIT if kind == "high" else C.CONF_SENSOR_LOWER_LIMIT
    rkey = C.CONF_RANGE_CRITICAL_HIGH if kind == "high" else C.CONF_RANGE_CRITICAL_LOW
    data = entry(**{key: setting, rkey: very})
    if kind == "low" and very > 54:
        data[C.CONF_RANGE_LOW] = max(70, very + 1)
    now = int(time.time())
    rows = series(now, 1)
    last = rows[-1]
    rows[-1] = type(last)(last.timestamp, value, kind, last.source)
    run = build(tmp_path, data, readings=rows, now=now)
    zone = "very_high" if kind == "high" else "very_low"
    assert run.c.glucose_zone == zone
    binary = "is_critical_high" if kind == "high" else "is_critical_low"
    assert run.sensor(binary)["state"] is True, "binary sensor"
    s = run.c.period_stats["6h"]
    assert (s.time_critical_high if kind == "high" else s.time_critical_low) > 0, "statistics"


# ------------------------------------------------------------------------------------------------ goals
GOALS = {  # key -> (span, default, op, configuration key)
    C.CONF_GOAL_TIME_IN_TARGET_RANGE: ((50, 99), 70, ">", "time_in_range"),
    C.CONF_GOAL_TIME_IN_IDEAL_RANGE: ((20, 100), None, ">=", "time_in_tight_range"),
    C.CONF_GOAL_TBR: ((1, 20), 4, "<", "time_low"),
    C.CONF_GOAL_TBR_CRITICAL: ((1, 10), 1, "<", "time_very_low"),
    C.CONF_GOAL_TAR: ((5, 50), 25, "<", "time_high"),
    C.CONF_GOAL_TAR_CRITICAL: ((1, 20), 5, "<", "time_very_high"),
    C.CONF_GOAL_CV: ((15, 50), 36, "<=", "cv"),
    C.CONF_GOAL_GMI: ((4.0, 12.0), None, "<", "gmi"),
}
PANEL_GOAL = {  # GCX1 page -> goal key (the nine weekly pages; pages without a goal: mean, SD, lowest / highest)
    0: C.CONF_GOAL_TIME_IN_TARGET_RANGE,
    1: C.CONF_GOAL_TIME_IN_IDEAL_RANGE,
    3: C.CONF_GOAL_CV,
    4: C.CONF_GOAL_GMI,
    5: C.CONF_GOAL_TBR,
    6: C.CONF_GOAL_TAR,
}
OPS = {">": 1, ">=": 2, "<": 3, "<=": 4}


def goal_cases():
    out = []
    for key, ((lo, hi), default, _op, _pub) in GOALS.items():
        values = [lo, hi]
        if default is None:
            values.append(None)  # the optional goals cleared
        for v in values:
            out.append(pytest.param(key, v, id=f"{key}={v}"))
    return out


@pytest.mark.parametrize("key,value", goal_cases())
def test_each_goal_on_every_output(tmp_path, key, value):
    data = entry(**{key: value})
    # nested goals: the inner band's goal never above the outer band's
    if key == C.CONF_GOAL_TBR_CRITICAL and value is not None:
        data[C.CONF_GOAL_TBR] = max(data[C.CONF_GOAL_TBR], value)
    if key == C.CONF_GOAL_TAR_CRITICAL and value is not None:
        data[C.CONF_GOAL_TAR] = max(data[C.CONF_GOAL_TAR], value)
    if key == C.CONF_GOAL_TBR and value is not None:
        data[C.CONF_GOAL_TBR_CRITICAL] = min(data[C.CONF_GOAL_TBR_CRITICAL], value)
    if key == C.CONF_GOAL_TAR and value is not None:
        data[C.CONF_GOAL_TAR_CRITICAL] = min(data[C.CONF_GOAL_TAR_CRITICAL], value)
    run = build(tmp_path, data, days=20)
    _span, _default, op, pub = GOALS[key]
    cfg = run.config()
    if value is None:
        assert pub not in cfg["goals"]  # no goal set
    else:
        assert cfg["goals"][pub] == {"op": op, "value": value}
    custom = any(data.get(k) != GOALS[k][1] for k in GOALS)
    assert cfg["goals_custom"] is custom and run.c.goals_custom() is custom
    assert cfg["weekly_pages"]["offered"] is custom
    assert run.api_stats()["goals"][pub] == value
    # the panel feed: the custom standard's target (offered only with custom goals or ranges)
    stds = run.feed()["ext"]["standards"]
    custom_std = next((s for s in stds if s["kind"] == 1), None)
    assert (custom_std is not None) is custom
    if custom_std is not None:
        for page, gkey in PANEL_GOAL.items():
            gv = data.get(gkey)
            assert custom_std["targets"][page] == ((OPS[GOALS[gkey][2]], float(gv)) if gv is not None else (0, 0.0))
            assert ("No goal in the settings" in custom_std["desc"][page]) is (gv is None)
    # the custom appendix: the goal, the operator and met / not met (14 days, >= 70 % active: compared)
    rep = run.report(14, custom=True)
    row = next(r for r in rep["custom_appendix"]["rows"] if r["key"] == pub)
    assert row["goal"] == value and row["goal_op"] == (op if value is not None else None)
    assert (row["met"] is None) is (value is None or row["value"] is None)
    if row["met"] is not None:
        ops = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}
        assert row["met"] is ops[op](row["value"], value)


@pytest.mark.parametrize("key", [C.CONF_GOAL_TBR, C.CONF_GOAL_CV, C.CONF_GOAL_TIME_IN_TARGET_RANGE])
def test_a_required_goal_stored_as_none_is_no_goal_everywhere(tmp_path, key):
    """None is "no goal set" for every goal (the screens offer it only for the two optional goals); every output
    agrees."""
    run = build(tmp_path, entry(**{key: None}), days=20)
    pub = GOALS[key][3]
    assert pub not in run.config()["goals"] and run.api_stats()["goals"][pub] is None
    row = next(r for r in run.report(14, custom=True)["custom_appendix"]["rows"] if r["key"] == pub)
    assert row["goal"] is None and row["met"] is None
    std = next(s for s in run.feed()["ext"]["standards"] if s["kind"] == 1)
    page = next((p for p, k in PANEL_GOAL.items() if k == key), None)
    if page is not None:
        assert std["targets"][page] == (0, 0.0) and "No goal in the settings" in std["desc"][page]


@pytest.mark.parametrize(
    "key, stored, used",
    [
        (C.CONF_GOAL_TIME_IN_TARGET_RANGE, 100, 70),
        (C.CONF_GOAL_TIME_IN_TARGET_RANGE, 99.6, 70),
        (C.CONF_GOAL_TBR_CRITICAL, 0, 1),
        (C.CONF_GOAL_TBR_CRITICAL, 0.5, 1),
        (C.CONF_GOAL_TIME_IN_TARGET_RANGE, 101, 70),
        (C.CONF_GOAL_TBR_CRITICAL, -1, 1),
    ],
)
def test_a_goal_stored_outside_its_span_is_its_default(tmp_path, key, stored, used):
    """A goal stored outside the span of its box (100% time in range, 0% time below the very-low limit) is read as the
    goal's default, the consensus target, without an error, on every output; the stored entry itself is not changed.
    No earlier, wider span is read (settings.GOAL_LIMITS is the only span)."""
    from glucocraft.settings import setting

    data = entry(**{key: stored})
    assert setting(data, key) == used and data[key] == stored
    run = build(tmp_path, data, days=20)
    assert run.config()["goals"][GOALS[key][3]] == {"op": GOALS[key][2], "value": used}
    assert run.api_stats()["goals"][GOALS[key][3]] == used
    row = next(r for r in run.report(14, custom=True)["custom_appendix"]["rows"] if r["key"] == GOALS[key][3])
    assert row["goal"] == used


def test_goals_at_their_bounds_together(tmp_path):
    """Every goal at the end of its span that is hardest to meet: published and compared, never an error."""
    data = entry(
        goal_time_in_target_range=99,
        goal_time_in_ideal_range=100,
        goal_tbr=1,
        goal_tbr_critical=1,
        goal_tar=5,
        goal_tar_critical=1,
        goal_cv=15,
        goal_gmi=4.0,
        weekly_pages="personal",
    )
    run = build(tmp_path, data, days=20)
    rows = run.report(14, custom=True)["custom_appendix"]["rows"]
    assert all(r["met"] is False for r in rows)
    assert run.c.weekly_pages_custom is True
    std = run.feed()["ext"]["standards"][0]
    assert std["kind"] == 1 and std["targets"][0] == (1, 99.0) and std["targets"][4] == (3, 4.0)
    assert std["desc"][4].endswith("Goal <4.0%")  # the GMI page's short goal form (panel_feed)


# ------------------------------------------------------------------------------------------------ pairwise
RANGE_SETS = {
    "default": {},
    "custom": {
        C.CONF_RANGE_LOW: 80,
        C.CONF_RANGE_TIGHT_LOW: 90,
        C.CONF_RANGE_HIGH: 160,
        C.CONF_RANGE_CRITICAL_HIGH: 220,
    },
    "min_spacing": MIN_SPACING,
    "span_ends": {
        C.CONF_RANGE_CRITICAL_LOW: DEFAULT_LOWER,
        C.CONF_RANGE_LOW: 50,
        C.CONF_RANGE_TIGHT_LOW: 100,
        C.CONF_RANGE_TIGHT_HIGH: 200,
        C.CONF_RANGE_HIGH: 300,
        C.CONF_RANGE_CRITICAL_HIGH: 400,
    },
}
GOAL_SETS = {
    "default": {},
    "custom": {C.CONF_GOAL_TIME_IN_TARGET_RANGE: 80, C.CONF_GOAL_CV: 30, C.CONF_GOAL_GMI: 6.5},
    "optional_set": {C.CONF_GOAL_TIME_IN_IDEAL_RANGE: 50, C.CONF_GOAL_GMI: 7.0},
    "bounds": {C.CONF_GOAL_TBR: 20, C.CONF_GOAL_TBR_CRITICAL: 10, C.CONF_GOAL_TAR: 50, C.CONF_GOAL_TAR_CRITICAL: 20},
}
PAIR_PARAMS = {
    "ranges": list(RANGE_SETS),
    "goals": list(GOAL_SETS),
    "weekly_pages": [None, "consensus", "personal"],
    "trend_display": ["arrow", "word"],
    "unit": ["mg/dL", "mmol/L"],
}
PAIR_ROWS = pairwise(PAIR_PARAMS)


def test_the_pairwise_rows_cover_every_pair():
    assert_pairs_covered(PAIR_PARAMS, PAIR_ROWS)


@pytest.mark.parametrize("row", PAIR_ROWS, ids=lambda r: "-".join(str(v) for v in r.values()))
def test_pairwise_ranges_goals_pages_trend_unit(tmp_path, row):
    ranges = {**DEFAULTS, **RANGE_SETS[row["ranges"]]}
    data = entry(**ranges, **GOAL_SETS[row["goals"]], trend_display=row["trend_display"], unit=row["unit"])
    if row["weekly_pages"]:
        data[C.CONF_WEEKLY_PAGES] = row["weekly_pages"]
    run = build(tmp_path, data, days=40)
    unit = row["unit"]
    cfg = run.config()
    offered = row["ranges"] != "default" or row["goals"] != "default"
    shown = row["weekly_pages"] == "personal" and offered
    # one set of ranges on every output
    assert cfg["ranges"] == {PUBLIC[k]: ranges[k] for k in ORDER}
    assert run.feed()["ranges"] == tuple(float(ranges[k]) for k in FEED_ORDER)
    api = run.api_stats()["ranges"]
    assert {k: api[PUBLIC[k]] for k in ORDER} == {k: http_api._glucose(v, unit) for k, v in ranges.items()}
    rep = run.report(14, custom=True)
    assert rep["custom_appendix"]["ranges"] == cfg["ranges"]
    # one set of goals
    goals = {GOALS[k][3]: data.get(k) for k in GOALS}
    assert run.api_stats()["goals"] == goals
    assert {k: v["value"] for k, v in cfg["goals"].items()} == {k: v for k, v in goals.items() if v is not None}
    assert {r["key"]: r["goal"] for r in rep["custom_appendix"]["rows"]} == goals
    # the weekly pages, the zone word under the value and the trend word
    assert cfg["weekly_pages"]["offered"] is offered and cfg["weekly_pages"]["shown"] == (
        "custom" if shown else "consensus"
    )
    stds = run.feed()["ext"]["standards"]
    assert stds[0]["kind"] == (1 if shown else 0) and len(stds) == (2 if offered else 1)
    un = "mmol/L" if unit == "mmol/L" else "mg/dL"
    for s in stds:
        assert un in s["desc"][0] and all(len(d) < 64 for d in s["desc"])
    _set_current(run, 120)
    run.c._trend_description = "Rising"
    a = run.sensor("current_glucose")["attributes"]
    # the zone word under the value with either choice of Trend shown as; the trend word on the Trend arrow sensor
    assert run.config()["trend_display"] == row["trend_display"]
    assert a["display_word"] == {"tight_range": "Tight range"}.get(a["zone"], a["display_word"]) and a["display_word"]
    assert run.sensor("trend_arrow")["attributes"]["word"] == "Rising"
