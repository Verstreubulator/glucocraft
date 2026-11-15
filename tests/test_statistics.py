# License: AGPL-3.0-only (LICENSE).
"""Time-weighted statistics (statistics.py), Change Over Time values (change.py) and the zone / binary-sensor /
statistics agreement.

Rules under test (the one gap rule of gaps.py): each reading weighs the seconds until the next reading, or its own
reading interval when that interval is a gap (longer than max(15 min, 3 x interval)); the last reading one interval;
sensor active = the same covered time / period; consensus bands: very low < 54, low 54-69, in range 70-180
inclusive, high 181-250, very high > 250; GMI = 3.31 + 0.02392 x mean (Bergenstal et al., 2018); GRI = 3.0 VLow + 2.4 Low + 1.6 VHigh +
0.8 High, capped at 100 (Klonoff et al., 2023); every value rounded half away from zero to one decimal.
"""

from __future__ import annotations

import math
import random
import time
from types import SimpleNamespace

import pytest
from glucocraft import change as ch
from glucocraft import coordinator as co
from glucocraft import statistics as st
from glucocraft.storage import GlucoseReading
from glucocraft.units import UNIT_MMOLL, round_half_up, to_mgdl, to_mgdl_setting

from conftest import AGP, stats_record


def one_value(g: float, ranges: dict = AGP, n: int = 10, interval: int = 300):
    """Statistics of n readings all at g (mg/dL)."""
    rd = [GlucoseReading(1000 + interval * i, g) for i in range(n)]
    return st.compute_period_stats(rd, "x", n * interval, interval, **ranges)


# ------------------------------------------------------------------------------------------------ empty / short
class TestShortInput:
    def test_empty(self):
        assert st.compute_period_stats([], "x", 3600, 300) is None

    def test_single_reading(self):
        """One reading: its value is the mean; one value has no spread, so there is no SD and no CV."""
        s = st.compute_period_stats([GlucoseReading(1, 100)], "x", 60, 60)
        assert s.reading_count == 1 and s.mean == 100 and s.std_dev is None and s.cv is None

    def test_two_readings_enough(self):
        s = st.compute_period_stats([GlucoseReading(0, 100), GlucoseReading(300, 100)], "x", 600, 300)
        assert s is not None and s.reading_count == 2 and s.mean == 100

    def test_zero_interval_and_same_time(self):
        rd = [GlucoseReading(5, 100), GlucoseReading(5, 120)]
        assert st.compute_period_stats(rd, "x", 600, 0) is None  # total weight 0

    def test_zero_period_gives_zero_availability(self):
        s = st.compute_period_stats([GlucoseReading(0, 100), GlucoseReading(300, 100)], "x", 0, 300)
        assert s.data_availability == 0.0


# ------------------------------------------------------------------------------------------------ bands
class TestConsensusBands:
    @pytest.mark.parametrize(
        "g, field",
        [
            (20, "time_critical_low"),
            (53.9, "time_critical_low"),
            (54, "time_low"),
            (69.9, "time_low"),
            (70, "pct_time_in_target_range"),
            (140, "pct_time_in_target_range"),
            (180, "pct_time_in_target_range"),
            (180.1, "time_high"),
            (250, "time_high"),
            (250.1, "time_critical_high"),
            (500, "time_critical_high"),
        ],
    )
    def test_boundaries(self, g, field):
        s = one_value(g)
        assert getattr(s, field) == 100.0, (g, field, s)

    @pytest.mark.parametrize("g, in_tight", [(69.9, False), (70, True), (140, True), (140.1, False)])
    def test_tight_range_inclusive(self, g, in_tight):
        assert (one_value(g).pct_time_in_ideal_range == 100.0) is in_tight

    def test_bands_partition_the_time(self):
        rng = random.Random(20261022)
        rd = [GlucoseReading(i * 300, round(rng.uniform(30, 420), 1)) for i in range(2000)]
        s = st.compute_period_stats(rd, "x", 2000 * 300, 300, **AGP)
        total = s.time_critical_low + s.time_low + s.pct_time_in_target_range + s.time_high + s.time_critical_high
        assert total == pytest.approx(100.0, abs=1e-9)  # five unrounded shares of the same time

    def test_custom_ranges_used(self):
        custom = {**AGP, "range_low": 80, "range_high": 160}
        assert one_value(75, custom).time_low == 100.0
        assert one_value(170, custom).time_high == 100.0
        assert one_value(75).pct_time_in_target_range == 100.0  # consensus default arguments


# ------------------------------------------------------------------------------------------------ weighting
class TestTimeWeighting:
    def test_weight_is_time_to_next_reading(self):
        # 100 for 60 s, then 200 for 240 s, then 200 (last, one interval)
        rd = [GlucoseReading(0, 100), GlucoseReading(60, 200), GlucoseReading(300, 200)]
        s = st.compute_period_stats(rd, "x", 600, 300)
        assert s.mean == pytest.approx(round((100 * 60 + 200 * 240 + 200 * 300) / 600, 1))

    def test_reading_before_a_gap_weighs_one_interval(self):
        rd = [GlucoseReading(0, 100), GlucoseReading(3600, 200)]
        s = st.compute_period_stats(rd, "x", 3600, 300)
        assert s.mean == pytest.approx(150.0)  # (100 x 300 + 200 x 300) / 600: one interval each

    def test_burst_does_not_outweigh_steady_readings(self):
        steady = [GlucoseReading(i * 300, 100) for i in range(12)]  # one hour at 100
        burst = [GlucoseReading(3600 + i * 10, 200) for i in range(30)]  # 300 s of 200, 30 readings
        s = st.compute_period_stats(steady + burst, "x", 4200, 300)
        assert s.mean < 130  # by count it would be 171

    def test_std_dev_and_cv(self):
        rd = [GlucoseReading(i * 300, 100 if i % 2 else 200) for i in range(100)]
        s = st.compute_period_stats(rd, "x", 30000, 300)
        assert s.mean == 150.0 and s.std_dev == 50.0 and s.cv == pytest.approx(100 / 3)  # unrounded

    def test_constant_glucose(self):
        s = one_value(150)
        assert s.std_dev == 0 and s.cv == 0 and s.min_value == s.max_value == 150

    def test_gmi_formula(self):
        for g in (80, 120, 154, 200):
            assert one_value(g).gmi == round_half_up(3.31 + 0.02392 * g, 1)

    def test_min_max(self):
        rd = [GlucoseReading(i * 300, v) for i, v in enumerate([120, 62.5, 300.45, 99])]
        s = st.compute_period_stats(rd, "x", 1200, 300)
        assert s.min_value == 62.5 and s.max_value == 300.5  # one decimal, half away from zero


# ------------------------------------------------------------------------------------------------ sensor active
class TestDataAvailability:
    def test_full_coverage(self):
        rd = [GlucoseReading(i * 60, 100) for i in range(360)]  # 6 h of 1-minute readings
        assert st.compute_period_stats(rd, "6h", 6 * 3600, 60).data_availability == 100.0

    def test_never_above_100(self):
        rd = [GlucoseReading(i * 60, 100) for i in range(720)]  # 12 h of data in a 6 h period
        assert st.compute_period_stats(rd, "6h", 6 * 3600, 60).data_availability == 100.0

    def test_gap_above_threshold_not_covered(self):
        # 1-minute CGM: the threshold is the 15-minute floor; a 16-minute gap is not covered, a 15-minute one is
        def avail(gap):
            rd = [GlucoseReading(0, 100), GlucoseReading(gap, 100)]
            return st.compute_period_stats(rd, "x", 3600, 60).data_availability

        # unrounded: every display rounds once
        assert avail(900) == pytest.approx((900 + 60) / 3600 * 100)
        assert avail(960) == pytest.approx((60 + 60) / 3600 * 100)  # the reading before the gap covers 60 s

    def test_threshold_scales_with_interval(self):
        rd = [GlucoseReading(0, 100), GlucoseReading(1800, 100)]  # 30 min, a 10-minute CGM
        assert st.compute_period_stats(rd, "x", 3600, 600).data_availability == pytest.approx((1800 + 600) / 3600 * 100)


# ------------------------------------------------------------------------------------------------ GRI
class TestGri:
    def test_all_very_low_capped(self):
        s = one_value(40)
        assert s.gri == 100.0 and s.gri_hypo == 100.0 and s.gri_hyper == 0.0

    def test_all_low_capped(self):
        s = one_value(60)
        assert s.gri == 100.0 and s.gri_hypo == 80.0

    def test_all_in_range_zero(self):
        assert one_value(120).gri == 0.0

    def test_mixed(self):
        # 10 % very low (one of ten equal-weight readings: the last one weighs one interval too), rest in range
        rd = [GlucoseReading(i * 300, 40 if i == 0 else 120) for i in range(10)]
        s = st.compute_period_stats(rd, "x", 3000, 300)
        assert s.time_critical_low == 10.0 and s.gri == 30.0 and s.gri_hypo == 10.0

    def test_high_components(self):
        rd = [GlucoseReading(i * 300, 200 if i < 5 else 300) for i in range(10)]  # 50 % high, 50 % very high
        s = st.compute_period_stats(rd, "x", 3000, 300)
        assert s.gri == pytest.approx(min(100, 1.6 * 50 + 0.8 * 50))
        assert s.gri_hyper == pytest.approx(50 + 0.5 * 50)


# ------------------------------------------------------------------------------------------------ rounding
@pytest.mark.parametrize("x, out", [(2.25, 2.3), (0.05, 0.1), (70.25, 70.3), (-0.05, -0.1), (99.95, 100.0)])
def test_r_rounds_halves_away_from_zero(x, out):
    assert round_half_up(x, 1) == out


# ------------------------------------------------------------------------------------------------ weekly trends
def test_weekly_trends(monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr(time, "time", lambda: now)
    rd = [GlucoseReading(t, 120) for t in range(now - 3 * 7 * 86400, now, 300)]  # 3 weeks of data
    weeks = st.compute_weekly_trends(rd, num_weeks=26, detected_interval=300)
    assert len(weeks) == 26
    assert all(w is not None and w.mean == 120 for w in weeks[:3])
    assert all(w is None for w in weeks[3:])
    assert weeks[0].period_seconds == 7 * 86400 and weeks[0].period_label == "week_0"


# ------------------------------------------------------------------------------------------------ Change Over Time
class TestChangeValues:
    def test_values_unrounded_and_combined_bands(self, make_stats):
        # the combined bands come from PeriodStats.time_below_low / time_above_high (unrounded, as the statistics keep
        # them), not from adding the two parts (tests/test_verify_hand_cases.py and tests/test_report_calculations.py
        # check the statistics themselves)
        s = make_stats(
            reading_count=1000,
            pct_time_in_target_range=70.25,
            pct_time_in_ideal_range=50.0,
            time_low=1,
            time_critical_low=0.25,
            time_high=2.0,
            time_critical_high=0.0,
            mean=120.5,
            std_dev=30.0,
            cv=25.0,
            gmi=6.2,
            data_availability=90.0,
            gri=10.0,
            min_value=60.5,
            max_value=200.5,
            time_below_low=1.3,
            time_above_high=2.0,
        )
        v = ch._values(s, 14, True)
        assert v["tir"] == 70.25  # unrounded: the cards round it once
        # the glucose values are not rounded here: the cards round them once, in the display unit (72.5 mg/dL is
        # 4.0 mmol/L; rounded to 73 mg/dL first, it would be shown as 4.1)
        assert v["mean"] == 120.5 and v["sd"] == 30.0 and v["min"] == 60.5 and v["max"] == 200.5
        assert v["below"] == 1.3 and v["above"] == 2.0  # level 1 + level 2
        assert v["gmi"] == 6.2 and v["hours"] == 302  # 90% sensor active of 14 days: 302.4 hours of data

    def test_gmi_rule(self, make_stats):
        base = {
            "reading_count": 5000,
            "pct_time_in_target_range": 70,
            "pct_time_in_ideal_range": 50,
            "time_low": 1,
            "time_critical_low": 0,
            "time_high": 2,
            "time_critical_high": 0,
            "mean": 120,
            "std_dev": 30,
            "cv": 25,
            "gmi": 6.2,
            "gri": 10,
            "min_value": 60,
            "max_value": 200,
        }
        assert ch._values(make_stats(**base, data_availability=70.0), 14, True)["gmi"] == 6.2
        assert ch._values(make_stats(**base, data_availability=69.9), 14, True) is None  # not enough data
        assert ch._values(make_stats(**base, data_availability=95.0), 7, True)["gmi"] is None

    def test_gri_rule(self, make_stats):
        """The GRI only for 14 days or more with enough data, and only on the consensus ranges."""
        base = {
            "reading_count": 5000,
            "pct_time_in_target_range": 70,
            "pct_time_in_ideal_range": 50,
            "time_low": 1,
            "time_critical_low": 0,
            "time_high": 2,
            "time_critical_high": 0,
            "mean": 120,
            "std_dev": 30,
            "cv": 25,
            "gmi": 6.2,
            "gri": 10,
            "min_value": 60,
            "max_value": 200,
        }
        assert ch._values(make_stats(**base, data_availability=70.0), 14, True)["gri"] == 10
        assert ch._values(make_stats(**base, data_availability=40.0), 28, True) is None  # not enough data
        assert ch._values(make_stats(**base, data_availability=95.0), 7, True)["gri"] is None
        assert ch._values(make_stats(**base, data_availability=95.0), 14, False)["gri"] is None
        s = make_stats(**base, data_availability=40.0)  # not enough data: no GRI, as no GMI
        assert st.gri_shown(s, 14 * 86400) is None and st.gri_shown(None, 0) is None
        s = make_stats(**base, data_availability=70.0)
        assert st.gri_shown(s, 14 * 86400) == 10 and st.gri_shown(s, 7 * 86400) is None

    def test_too_little_data_is_a_gap(self):
        s = stats_record(reading_count=71, covered_seconds=71 * 300)  # less than 6 h of data
        assert ch._values(s, 14, True) is None
        assert ch._values(None, 14, True) is None


# ------------------------------------------------------------------------------------------------ one value everywhere
def _zone(g: float, r: dict) -> str | None:
    ns = SimpleNamespace(current_glucose=g, current_limit=None, **r)
    return co.GlucoCraftCoordinator.glucose_zone.fget(ns)


@pytest.mark.parametrize(
    "ranges",
    [
        AGP,
        {**AGP, "range_high": to_mgdl_setting(8.9, UNIT_MMOLL)},  # 160.3 (mmol/L limits are stored with one decimal)
        {**AGP, "range_high": 160},  # a whole-number limit, as earlier versions stored it
    ],
    ids=["consensus", "custom-8.9-mmol", "custom-160-int"],
)
@pytest.mark.parametrize("mmol", [2.9, 3.0, 3.8, 3.9, 8.8, 8.9, 9.0, 10.0, 10.1, 13.9, 14.0])
def test_zone_binary_sensor_and_statistics_agree(ranges, mmol):
    """A reading falls on the same side of a limit in the zone color, the binary sensors and the statistics."""
    g = to_mgdl(mmol, UNIT_MMOLL)
    zone = _zone(g, ranges)
    s = one_value(g, ranges)
    high_bs = g > ranges["range_high"]  # binary_sensor glucose_high
    assert high_bs == (s.time_high + s.time_critical_high > 0) == (zone in ("high", "very_high"))
    low_bs = g < ranges["range_low"]
    assert low_bs == (s.time_low + s.time_critical_low > 0) == (zone in ("low", "very_low"))
    in_bs = ranges["range_low"] <= g <= ranges["range_high"]
    assert in_bs == (s.pct_time_in_target_range == 100.0)


def test_zone_at_a_custom_mmol_limit():
    custom = {**AGP, "range_high": to_mgdl_setting(8.9, UNIT_MMOLL)}
    assert _zone(to_mgdl(8.9, UNIT_MMOLL), custom) == "near_high"  # 8.9 against a high limit of 8.9: not high
    assert _zone(to_mgdl(9.0, UNIT_MMOLL), custom) == "high"


@pytest.mark.parametrize(
    "g, zone",
    [
        (53.9, "very_low"),
        (54, "low"),
        (69.9, "low"),
        (70, "tight_range"),
        (140, "tight_range"),
        (140.1, "near_high"),
        (180, "near_high"),
        (180.1, "high"),
        (250, "high"),
        (250.1, "very_high"),
        (None, None),
    ],
)
def test_zone_boundaries(g, zone):
    assert _zone(g, AGP) == zone


def test_zone_near_low():
    assert _zone(75, {**AGP, "range_ideal_low": 80}) == "near_low"


def test_gmi_shown_rule():
    s = stats_record(data_availability=70.0, gmi=6.5)
    assert st.gmi_shown(s, 14 * 86400) == 6.5
    assert st.gmi_shown(s, 14 * 86400 - 1) is None
    assert st.gmi_shown(stats_record(data_availability=69.9, gmi=6.5), 90 * 86400) is None
    assert st.gmi_shown(stats_record(data_availability=99, gmi=0), 90 * 86400) is None
    assert st.gmi_shown(None, 90 * 86400) is None


def test_no_float_noise_in_results():
    rng = random.Random(7)
    rd = [GlucoseReading(i * 60, rng.uniform(40, 400)) for i in range(5000)]
    s = st.compute_period_stats(rd, "x", 5000 * 60, 60)
    # the mean, the SD, the CV and the percentages are kept unrounded (rounded once where they are shown); the GMI
    # and the GRI have one decimal
    for name in ("gmi", "gri"):
        v = getattr(s, name)
        assert math.isclose(v * 10, round(v * 10), abs_tol=1e-6), (name, v)
    weights = 5000 * 60
    assert s.data_availability == min(100.0, s.covered_seconds / weights * 100)
