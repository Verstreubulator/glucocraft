# License: AGPL-3.0-only (LICENSE).
"""The statistics pass computes several range sets at once and each band once per store, with identical numbers.

coordinator._compute_all_stats computes the values that do not depend on the ranges (mean, SD, CV, GMI, lowest and
highest value, sensor active, limit readings) once per window for the ranges in use and the consensus ranges together,
and the readings' bands once per pass (statistics.compute_period_stats_sets, band_flags; change.compute_change_sets).
Here the earlier calculation, one window and one range set at a time, is kept as the reference (the _ref_* functions
are the code before that change, verbatim apart from their names and the later formulas of the percentages, multiplied
before the division, and of sensor active, statistics.sensor_active), and every result must be EQUAL to it: == on every
PeriodStats field and every Change Over Time value, never approximately.

The data: about six months of generated readings (tools/synth/generate.py) from a mmol/L source, so that the stored
values have decimals and the sums depend on their order: 15-, 5- and 1-minute stretches one after the other, the
generator's dropouts, LO / HI readings, and both daylight-saving changes of the time zone.
"""

from __future__ import annotations

import bisect
import importlib
import itertools
import math
import sys
import time
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from glucocraft import change as ch
from glucocraft import history_import as hi
from glucocraft import statistics as st
from glucocraft.const import AGP_RANGES, ENOUGH_DATA_PCT
from glucocraft.gaps import local_intervals, window_weights
from glucocraft.gaps import weights as gap_weights
from glucocraft.limits import scale_position
from glucocraft.statistics import PeriodStats, gri
from glucocraft.units import gmi_mmol_mol, gmi_percent, round_half_up

from conftest import real_coordinator

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
generate = importlib.import_module("tools.synth.generate").generate

TZ = ZoneInfo("America/Los_Angeles")
END = datetime(2026, 3, 20, tzinfo=TZ)  # 182 days back: both daylight-saving changes (November, March) fall inside
CUSTOM = {
    "range_low": 75,
    "range_high": 160,
    "range_ideal_low": 80,
    "range_ideal_high": 130,
    "range_critical_low": 60,
    "range_critical_high": 220,
}
RANGE_SETS = {"consensus": dict(AGP_RANGES), "custom": CUSTOM}


# ------------------------------------------------------------------------------------- the reference (earlier code)
def _r(x: float, decimals: int = 1) -> float:
    return round_half_up(x, decimals)


def _ref_compute_period_stats(
    readings,
    period_label,
    period_seconds,
    detected_interval,
    range_low=70,
    range_high=180,
    range_ideal_low=70,
    range_ideal_high=140,
    range_critical_low=54,
    range_critical_high=250,
    intervals=None,
    weights=None,
):
    if not readings:
        return None

    if weights is None:
        ts = [r.timestamp for r in readings]
        iv = list(intervals) if intervals is not None else local_intervals(ts, detected_interval)
        weights = gap_weights(ts, iv)

    total_weight = sum(weights)
    if total_weight == 0:
        return None

    covered_time = total_weight
    data_availability = min(100.0, 100.0 * covered_time / period_seconds) if period_seconds > 0 else 0.0
    lim_low = [w for r, w in zip(readings, weights, strict=False) if getattr(r, "limit", None) == "low"]
    lim_high = [w for r, w in zip(readings, weights, strict=False) if getattr(r, "limit", None) == "high"]

    # the values from the measured readings alone (a reading at the sensor's limit is no value)
    measured = [(r.raw, w) for r, w in zip(readings, weights, strict=False) if getattr(r, "limit", None) is None]
    measured_weight = sum(w for _, w in measured)
    mean = std_dev = cv = gmi = min_value = max_value = None
    if measured_weight > 0:
        mean = sum(v * w for v, w in measured) / measured_weight
        if len(measured) >= 2:
            std_dev = math.sqrt(sum(w * (v - mean) ** 2 for v, w in measured) / measured_weight)
            cv = (std_dev / mean * 100) if mean > 0 else 0.0
        gmi = gmi_percent(mean)
        min_value = min(v for v, _ in measured)
        max_value = max(v for v, _ in measured)

    pos = [scale_position(r.raw, getattr(r, "limit", None)) for r in readings]
    vlow, low, tlow = range_critical_low, range_low, range_ideal_low
    thigh, high, vhigh = range_ideal_high, range_high, range_critical_high
    pw = list(zip(pos, weights, strict=False))
    time_critical_low_w = sum(w for p, w in pw if p < vlow)
    time_low_w = sum(w for p, w in pw if vlow <= p < low)
    time_in_range_w = sum(w for p, w in pw if low <= p <= high)
    time_in_ideal_w = sum(w for p, w in pw if tlow <= p <= thigh)
    time_high_w = sum(w for p, w in pw if high < p <= vhigh)
    time_critical_high_w = sum(w for p, w in pw if p > vhigh)

    pct_time_low = 100.0 * time_low_w / total_weight
    pct_time_high = 100.0 * time_high_w / total_weight
    pct_crit_low = 100.0 * time_critical_low_w / total_weight
    pct_crit_high = 100.0 * time_critical_high_w / total_weight

    gri_score, gri_hypo, gri_hyper, _ = gri(pct_crit_low, pct_time_low, pct_time_high, pct_crit_high)

    return PeriodStats(
        period_label=period_label,
        period_seconds=period_seconds,
        reading_count=len(readings),
        # the percentages and the CV unrounded (every display rounds once); GMI, lowest and highest one decimal
        data_availability=data_availability,
        mean=mean,
        std_dev=std_dev,
        cv=cv,
        gmi=_r(gmi, 1) if gmi is not None else None,
        min_value=_r(min_value, 1) if min_value is not None else None,
        max_value=_r(max_value, 1) if max_value is not None else None,
        pct_time_in_target_range=100.0 * time_in_range_w / total_weight,
        pct_time_in_ideal_range=100.0 * time_in_ideal_w / total_weight,
        time_low=pct_time_low,
        time_high=pct_time_high,
        time_critical_low=pct_crit_low,
        time_critical_high=pct_crit_high,
        gri=gri_score,
        gri_hypo=gri_hypo,
        gri_hyper=gri_hyper,
        covered_seconds=covered_time,
        limit_low=len(lim_low),
        limit_high=len(lim_high),
        limit_low_seconds=sum(lim_low),
        limit_high_seconds=sum(lim_high),
        time_below_low=100.0 * (time_low_w + time_critical_low_w) / total_weight,
        time_above_high=100.0 * (time_high_w + time_critical_high_w) / total_weight,
        gmi_mmol_mol=gmi_mmol_mol(mean) if mean is not None else None,
    )


def _ref_compute_weekly_trends(
    readings,
    num_weeks=26,
    detected_interval=300,
    range_low=70,
    range_high=180,
    range_ideal_low=70,
    range_ideal_high=140,
    range_critical_low=54,
    range_critical_high=250,
    intervals=None,
    now=None,
    spans=None,
    windows=None,
):
    week_seconds = 7 * 86400
    if windows is None:
        now = int(time.time()) if now is None else int(now)
        windows = [(now - (k + 1) * week_seconds, now - k * week_seconds) for k in range(num_weeks)]
    results = []
    ts = [r.timestamp for r in readings]
    iv = list(intervals) if intervals is not None else local_intervals(ts, detected_interval)
    sp = spans if spans is not None else gap_weights(ts, iv)

    for week_num, (start_ts, end_ts) in enumerate(windows):
        i, j = bisect.bisect_left(ts, start_ts), bisect.bisect_left(ts, end_ts)
        week_readings = readings[i:j]

        stats = _ref_compute_period_stats(
            week_readings,
            f"week_{week_num}",
            int(end_ts - start_ts),
            detected_interval,
            range_low,
            range_high,
            range_ideal_low,
            range_ideal_high,
            range_critical_low,
            range_critical_high,
            weights=window_weights(sp, iv, i, j),
        )
        if stats is not None and stats.sensor_active_exact < ENOUGH_DATA_PCT:
            stats = None  # the enough-data rule
        results.append(stats)

    return results


def _ref_compute_change(storage, interval, now, ranges, intervals=None, spans=None):
    interval = int(interval or 60)
    iv = intervals if intervals is not None else storage.reading_intervals(interval)
    readings = storage.readings
    sp = spans if spans is not None else gap_weights([r.timestamp for r in readings], iv)

    def stats(start, end, label):
        i, j = storage.window_indices(start, end)
        if j - i < 2:
            return None
        return _ref_compute_period_stats(
            readings[i:j], label, end - start, interval, **ranges, weights=window_weights(sp, iv, i, j)
        )

    agp = dict(ranges) == ch.AGP_RANGES  # the GRI on the consensus ranges only
    weeks14, weeks7, ends, stats14 = [], [], [], []
    for w in range(ch.WEEKS):
        end = now - w * 7 * ch.DAY
        ends.append(end)
        s14 = stats(end - 14 * ch.DAY, end, f"w14_{w}")
        stats14.append(s14 if s14 is not None and s14.sensor_active_exact >= ENOUGH_DATA_PCT else None)
        weeks14.append(ch._values(s14, 14, agp))
        weeks7.append(ch._values(stats(end - 7 * ch.DAY, end, f"w7_{w}"), 7, agp))
    compare = {
        name: ch._values(stats(now - b * ch.DAY, now - a * ch.DAY, name), b - a, agp) for name, a, b in ch.COMPARE
    }
    return ch.ChangeOverTime(weeks14, weeks7, ends, compare, stats14)


def _ref_pass(c, now: int) -> dict:
    """The statistics pass as computed before: each period, the 26 weeks and Change Over Time, one range set at a
    time (coordinator._compute_all_stats before the change)."""
    readings = c.storage.readings
    iv = c.storage.reading_intervals(c.detected_interval_seconds)
    ts = [r.timestamp for r in readings]
    sp = gap_weights(ts, iv)
    personal = {k: getattr(c, k) for k in AGP_RANGES}
    out = {"period_stats": {}, "period_stats_agp": {}}
    for label, seconds in st_periods():
        i = bisect.bisect_left(ts, now - seconds)
        w = window_weights(sp, iv, i, len(readings))
        out["period_stats"][label] = _ref_compute_period_stats(
            readings[i:], label, seconds, c.detected_interval_seconds, **personal, weights=w
        )
        out["period_stats_agp"][label] = _ref_compute_period_stats(
            readings[i:], label, seconds, c.detected_interval_seconds, **AGP_RANGES, weights=w
        )
    weekly = {
        "num_weeks": 26,
        "detected_interval": c.detected_interval_seconds,
        "intervals": iv,
        "now": now,
        "spans": sp,
    }
    out["weekly_trends"] = _ref_compute_weekly_trends(readings, **weekly, **personal)
    out["weekly_trends_agp"] = _ref_compute_weekly_trends(readings, **weekly)
    out["consensus"] = _ref_compute_change(c.storage, c.detected_interval_seconds, now, AGP_RANGES, iv, sp)
    out["personal"] = _ref_compute_change(c.storage, c.detected_interval_seconds, now, personal, iv, sp)
    return out


def st_periods():
    from glucocraft.const import STAT_PERIODS

    return STAT_PERIODS


# ------------------------------------------------------------------------------------------------------ the data
def _state(s):
    """A generated state as Home Assistant's history returns it."""
    attrs = {"unit_of_measurement": "mmol/L"}
    if s.measured is not None:
        attrs["measurement_timestamp"] = s.measured
    t = datetime.fromtimestamp(s.recorded, UTC)
    return types.SimpleNamespace(state=s.state, attributes=attrs, last_updated=t, last_changed=t)


@pytest.fixture(scope="module")
def rows():
    """About 182 days of generated mmol/L readings: 60 days at 15 minutes, 90 days at 5 minutes, then 32 days at 1
    minute, with the generator's dropouts and LO / HI readings, as the history import stores them."""
    start = END - timedelta(days=182)
    stretches = ((start, 60, 900), (start + timedelta(days=60), 90, 300), (start + timedelta(days=150), 32, 60))
    states = []
    for k, (begin, days, interval) in enumerate(stretches):
        series = generate(
            None,
            start=begin,
            hours=days * 24,
            interval=interval,
            unit="mmol/L",
            seed=11 + k,
            modifiers=("dropouts", "limits"),
        )
        states += series.states
    out = hi.rows_from_states([_state(s) for s in states], True, True, {}, now=int(END.timestamp()))
    assert {lim for _, _, lim in out} == {None, "low", "high"}  # LO and HI readings are part of the data
    return out


@pytest.fixture(scope="module")
def store_coordinator(rows, tmp_path_factory):
    c = real_coordinator(tmp_path_factory.mktemp("pass"))
    assert c.storage.merge_history(rows) == len(rows)
    c.detected_interval_seconds = c.storage.detect_interval()
    return c


def _set_ranges(c, ranges: dict) -> None:
    for key, value in ranges.items():
        setattr(c, key, value)


# ------------------------------------------------------------------------------------------------------ the tests
def test_the_data_cover_every_case(store_coordinator):
    """Mixed reading intervals, gaps, LO / HI readings and decimals in the stored values (the sums depend on order)."""
    rd = store_coordinator.storage.readings
    deltas = {b.timestamp - a.timestamp for a, b in itertools.pairwise(rd)}
    assert any(55 <= d <= 65 for d in deltas) and any(295 <= d <= 305 for d in deltas)
    assert any(895 <= d <= 905 for d in deltas) and max(deltas) > 3600  # every interval and real gaps
    assert any(r.limit == "low" for r in rd) and any(r.limit == "high" for r in rd)
    assert any(r.raw != int(r.raw) for r in rd)
    assert (rd[-1].timestamp - rd[0].timestamp) / 86400 > 180


@pytest.mark.parametrize("now_after_last", [120, 3 * 86400 + 4321])
@pytest.mark.parametrize("standard", sorted(RANGE_SETS))
def test_statistics_pass_identical_to_one_set_at_a_time(store_coordinator, standard, now_after_last):
    """Every period, every week and Change Over Time on both range sets, as the earlier calculation gives them."""
    c = store_coordinator
    _set_ranges(c, RANGE_SETS[standard])
    assert c.uses_custom_ranges() is (standard == "custom")
    now = c.storage.readings[-1].timestamp + now_after_last
    c.change = {}
    c._compute_all_stats(now)
    ref = _ref_pass(c, now)
    for label, seconds in st_periods():
        assert (ref["period_stats"][label] is None) is (seconds < now_after_last)  # empty only after the readings
        assert c.period_stats[label] == ref["period_stats"][label], label
        assert c.period_stats_agp[label] == ref["period_stats_agp"][label], label
    assert len(c.weekly_trends) == 26 and any(w is not None for w in c.weekly_trends)
    assert c.weekly_trends == ref["weekly_trends"]
    assert c.weekly_trends_agp == ref["weekly_trends_agp"]
    assert c.change["consensus"] == ref["consensus"]
    assert c.change["personal"] == ref["personal"]
    if standard == "custom":  # the two standards really differ in this data
        assert c.period_stats["90d"] != c.period_stats_agp["90d"]
        assert c.change["personal"].weeks14 != c.change["consensus"].weeks14


@pytest.mark.parametrize("standard", sorted(RANGE_SETS))
def test_the_single_set_functions_are_unchanged(store_coordinator, standard):
    """compute_period_stats (with and without given weights) and compute_weekly_trends with the CGM Report's local
    weeks give the earlier results; so does compute_change_sets with one range set."""
    c = store_coordinator
    ranges = RANGE_SETS[standard]
    rd = c.storage.readings
    iv = c.storage.reading_intervals(c.detected_interval_seconds)
    sp = gap_weights([r.timestamp for r in rd], iv)
    end = int(END.timestamp())
    for days in (1, 3, 14, 45):
        i, j = c.storage.window_indices(end - days * 86400, end)
        w = window_weights(sp, iv, i, j)
        args = (rd[i:j], "w", days * 86400, c.detected_interval_seconds)
        assert st.compute_period_stats(*args, **ranges, weights=w) == _ref_compute_period_stats(
            *args, **ranges, weights=w
        )
        assert st.compute_period_stats(*args, **ranges) == _ref_compute_period_stats(*args, **ranges)
    weeks = []  # 7 local days each, newest first, as the CGM Report passes them
    day = END
    for _ in range(26):
        first = day - timedelta(days=7)
        first = datetime(first.year, first.month, first.day, tzinfo=TZ)
        weeks.append((int(first.timestamp()), int(day.timestamp())))
        day = first
    kw = {"detected_interval": c.detected_interval_seconds, "intervals": iv, "spans": sp, "windows": weeks}
    assert st.compute_weekly_trends(rd, **kw, **ranges) == _ref_compute_weekly_trends(rd, **kw, **ranges)
    assert ch.compute_change_sets(c.storage, c.detected_interval_seconds, end, (ranges,))[0] == _ref_compute_change(
        c.storage, c.detected_interval_seconds, end, ranges
    )


def test_sets_give_each_set_alone():
    """compute_period_stats_sets returns one result per range set, each equal to that set alone, also for one
    reading and for no reading."""
    from glucocraft.storage import GlucoseReading

    rd = [
        GlucoseReading(1_800_000_000 + 300 * k, v, lim, 1)
        for k, (v, lim) in enumerate(
            [(40.0, "low"), (61.3, None), (70.0, None), (180.0, None), (250.4, None), (500.0, "high"), (139.9, None)]
        )
    ]
    sets = (RANGE_SETS["consensus"], CUSTOM)
    for part in (rd, rd[:1], []):
        both = st.compute_period_stats_sets(part, "p", 7200, 300, sets)
        assert both == [st.compute_period_stats(part, "p", 7200, 300, **s) for s in sets]
        assert both == [_ref_compute_period_stats(part, "p", 7200, 300, **s) for s in sets]
