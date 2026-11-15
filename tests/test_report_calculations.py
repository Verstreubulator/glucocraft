# License: AGPL-3.0-only (LICENSE).
"""The report's calculations, each with a case that tells a right rule from a plausible wrong one.

Rules under test:
  * a hypoglycemia event is found once over the report's whole minute grid and counted once, in the
    period, 14-day block, day or calendar day in which it starts (Danne et al., 2017, Section 4, for the event; the
    research appendix's step 6 for the counting);
  * an event's level in the event list is 2 only when a level 2 event (15 consecutive minutes below
    54 mg/dL) lies inside it;
  * daily profiles use 10-minute slots of the local clock on the 25-hour and the 23-hour day;
  * time below 70 and above 180 are calculated and rounded once;
  * one GMI mmol/mol rule (Bergenstal et al., 2018, from the mean), the GRI zone of the
    displayed GRI, the report's GRI from the unrounded bands (Klonoff et al., 2023);
  * the 70% sensor-active rule compares the unrounded value (Battelino et al., 2019, Table 2);
  * the weekly trend's weeks are 7 local days;
  * LO / HI readings in the hourly log and as an event's lowest value are marked as limit readings;
  * the trend pages' 4-week direction (coordinator._directions);
  * the Weekly statistics sensor's 14-day GMI and the API's GMI rule.
"""

from __future__ import annotations

import types
from datetime import datetime

import pytest
from glucocraft import change as ch
from glucocraft import coordinator as co
from glucocraft import report as R
from glucocraft import statistics as st
from glucocraft.regional import Regional
from glucocraft.storage import GlucoseReading
from glucocraft.units import gmi_mmol_mol, round_half_up

from conftest import LA, Rd


def local(*args) -> int:
    return int(datetime(*args, tzinfo=LA).timestamp())


def readings(start: int, end: int, value, step: int = 60) -> list[Rd]:
    """One reading every `step` seconds from start to end; value(t) -> mg/dL, or (mg/dL, limit flag)."""
    out = []
    for t in range(start, end, step):
        v = value(t)
        out.append(Rd(t, *v) if isinstance(v, tuple) else Rd(t, float(v)))
    return out


def low_between(a: int, b: int, level: float = 60.0, base: float = 120.0):
    """A value function: `level` from a to b (epoch seconds, b exclusive), `base` elsewhere."""
    return lambda t: level if a <= t < b else base


# The report of 2026-10-22 09:00 (Los Angeles): the 14-day period is 10/08-10/21, the 30-day period 09/22-10/21
NOW = local(2026, 10, 22, 9, 0)


def report(rd, days: int, full: bool = False, now: int = NOW) -> dict:
    return R.build_report(
        rd,
        60 if len(rd) > 1 and rd[1].timestamp - rd[0].timestamp == 60 else 300,
        LA,
        days,
        full,
        [],
        now=now,
        regional=Regional("24", "YMD"),
    )


# ------------------------------------------------------------------------------------------------ events counted where they start
class TestEventsCountedWhereTheyStart:
    def test_an_event_across_midnight_is_one_event_on_its_start_day(self):
        # 50 minutes at 60 mg/dL from 10/15 23:40 to 10/16 00:30
        rd = readings(local(2026, 9, 1), NOW, low_between(local(2026, 10, 15, 23, 40), local(2026, 10, 16, 0, 30)))
        d = report(rd, 14, full=True)
        october = next(m for m in d["monthly"] if m["month"].startswith("October"))  # the period's days of October
        lows = {day[0]: day[2] for day in october["days"]}
        assert lows[15] == 1 and lows[16] == 0  # not: 1 on 10/15 and 1 on 10/16
        week = {row[0]: row[2] for row in d["days"]["week"]}
        assert sum(week.values()) == 1 and week["Thu 10-15"] == 1
        assert d["lows"]["events"] == 1 and d["lows"]["list"][0][1] == 50

    def test_an_event_eight_minutes_before_midnight_is_counted_on_that_day(self):
        rd = readings(local(2026, 9, 1), NOW, low_between(local(2026, 10, 12, 23, 52), local(2026, 10, 13, 0, 40)))
        d = report(rd, 14, full=True)
        october = next(m for m in d["monthly"] if m["month"].startswith("October"))  # the period's days of October
        lows = {day[0]: day[2] for day in october["days"]}
        assert lows[12] == 1 and lows[13] == 0  # not: counted on 10/13

    def test_an_event_from_the_day_before_the_period_is_not_counted_in_it(self):
        # 40 minutes from 10/07 23:50: the day before the 14-day period; inside the 30-day period
        rd = readings(local(2026, 8, 1), NOW, low_between(local(2026, 10, 7, 23, 50), local(2026, 10, 8, 0, 30)))
        d = report(rd, 30)
        d14 = report(rd, 14)
        assert d14["lows"]["events"] == 0  # not: 1, "Thu 10-08 00:00", 30 minutes
        assert d14["lows"]["list"] == []
        # the full report's summary by period: the same rule (the 14-day column without it, the 30-day one with it)
        by_days = {c["days"]: c["events"] for c in report(rd, 30, full=True)["low_events"]}
        assert by_days == {14: 0, 30: 1}
        assert d["lows"]["events"] == 1
        label, minutes = d["lows"]["list"][0][:2]
        assert label == "Wed 10-07 23:50" and minutes == 40  # its own start and duration
        # each event in exactly one 14-day block: the block 09/24-10/07 counts it, the blocks add up to the period
        blocks = {b["period_start"]: b["events"] for b in d["blocks"]}
        assert blocks["2026-09-24"] == 1 and blocks["2026-10-08"] == 0
        assert sum(blocks.values()) == d["lows"]["events"]

    def test_daily_and_monthly_counts_add_up_to_the_period(self):
        # an event every night at 23:45 for 40 minutes: every one crosses midnight
        def value(t):
            lt = datetime.fromtimestamp(t, LA)
            return 60.0 if (lt.hour == 23 and lt.minute >= 45) or (lt.hour == 0 and lt.minute < 25) else 120.0

        d = report(readings(local(2026, 8, 1), NOW, value, step=300), 30, full=True)
        assert sum(row[2] for row in d["days"]["week"]) == d["lows"]["events"] == 30
        assert all(row[2] == 1 for row in d["days"]["week"])
        assert sum(b["events"] for b in d["blocks"]) == 30


# ------------------------------------------------------------------------------------------------ the event level
class TestEventLevel:
    def test_one_value_below_54_does_not_make_a_level_2_event(self):
        a = local(2026, 10, 13, 14, 0)
        rd = readings(
            local(2026, 9, 1), NOW, lambda t: 50.0 if a + 600 <= t < a + 900 else 62.0 if a <= t < a + 1800 else 120.0
        )
        lw = report(rd, 14)["lows"]
        assert lw["events_54"] == 0 and len(lw["list"]) == 1
        row = lw["list"][0]
        assert row[2] == 50 and row[4] == 1  # not: level 2 from the lowest value alone

    def test_fifteen_minutes_below_54_make_the_event_level_2(self):
        a = local(2026, 10, 13, 2, 0)
        rd = readings(
            local(2026, 9, 1), NOW, lambda t: 50.0 if a + 300 <= t < a + 1500 else 62.0 if a <= t < a + 2400 else 120.0
        )
        lw = report(rd, 14)["lows"]
        assert lw["events"] == 1 and lw["events_54"] == 1 and lw["events_night"] == 1
        assert lw["list"][0][4] == 2


# ------------------------------------------------------------------------------------------------ daily profiles on the local clock
def _by_local_hour(t: int) -> float:
    """100 + 10 x the local hour: every reading shows the clock hour it was taken in."""
    return 100.0 + 10 * datetime.fromtimestamp(t, LA).hour


class TestDailyProfilesOnTheLocalClock:
    def test_fall_back_day(self):
        now = local(2026, 11, 5, 9, 0)  # the 14 days 10/22-11/04 include 11/01 (25 hours)
        d = report(readings(local(2026, 9, 20), now, _by_local_hour, step=300), 14, now=now)["standard"]
        k = d["daily_labels"].index("Sun 11-01")
        row = d["daily"][k]
        assert len(row) == 144
        assert row[30] == 150 and row[60] == 200  # 05:00 and 10:00 at their clock time
        assert row[143] == 330  # 23:50: the last hour is kept
        assert row[6] == 110 and row[11] == 110  # the repeated 01:00 hour shares its slots

    def test_spring_forward_day(self):
        now = local(2027, 3, 18, 9, 0)  # the 14 days 03/04-03/17 include 03/14 (23 hours)
        d = report(readings(local(2027, 2, 1), now, _by_local_hour, step=300), 14, now=now)["standard"]
        k = d["daily_labels"].index("Sun 03-14")
        row = d["daily"][k]
        assert row[30] == 150 and row[143] == 330  # not: 05:00 showing 06:00 and 23:50 empty
        assert all(row[s] is None for s in range(12, 18))  # 02:00-02:59 does not exist that day


# ------------------------------------------------------------------------------------------------ combined bands rounded once
def test_combined_bands_are_rounded_once():
    # 1.95% low (54-69) and 1.95% very low (<54): each rounds to 2.0, together 3.9% (not 4.0, which reads as not
    # meeting "less than 4%")
    vals = [60.0] * 195 + [50.0] * 195 + [300.0] * 195 + [200.0] * 195 + [120.0] * (10000 - 780)
    rd = [GlucoseReading(1_000_000 + 300 * i, v) for i, v in enumerate(vals)]
    s = st.compute_period_stats(
        rd,
        "x",
        10000 * 300,
        300,
        range_low=70,
        range_high=180,
        range_ideal_low=70,
        range_ideal_high=140,
        range_critical_low=54,
        range_critical_high=250,
    )

    # the bands are unrounded (1.95% each); the combined band is 3.9% rounded once, not 4.0% from two shown 2.0%
    def shown(x):
        """A percentage as a display shows it (one decimal, rounded once)."""
        return round_half_up(x, 1)

    assert shown(s.time_low) == shown(s.time_critical_low) == 2.0 and shown(s.time_below_low) == 3.9
    assert shown(s.time_high) == shown(s.time_critical_high) == 2.0 and shown(s.time_above_high) == 3.9
    v = ch._values(s, 14, True)
    assert shown(v["below"]) == 3.9 and shown(v["above"]) == 3.9


def test_a_record_built_by_hand_fills_the_combined_bands(make_stats):
    s = make_stats(time_low=1.2, time_critical_low=0.3, time_high=10.0, time_critical_high=2.5)
    assert s.time_below_low == 1.5 and s.time_above_high == 12.5


# ------------------------------------------------------------------------------------------------ one GMI and GRI rule
class TestOneGmiAndGriRule:
    def test_gmi_in_mmol_mol_from_the_mean(self, make_stats):
        # mean 72.0 mg/dL: GMI 5.0%; Bergenstal et al. (2018) give 32 mmol/mol, the master equation on 5.0% gives 31
        s = make_stats(mean=72.0, gmi=5.0)
        assert s.gmi_mmol_mol == 32 == gmi_mmol_mol(72.0)
        assert Regional(a1c_unit="mmol_mol").a1c(s.gmi, s.gmi_mmol_mol) == "32 mmol/mol"
        v = ch._values(make_stats(mean=72.0, gmi=5.0, data_availability=95.0), 14, True)
        assert v["gmi"] == 5.0 and v["gmi_mmol_mol"] == 32

    def test_the_report_publishes_the_gmi_in_mmol_mol_of_its_mean(self):
        rd = readings(local(2026, 9, 1), NOW, lambda t: 72.0, step=300)
        s = report(rd, 14)["standard"]
        assert s["gmi"] == 5.0 and s["gmi_mmol_mol"] == 32

    @pytest.mark.parametrize(
        "gri, zone",
        [(20.0, "A"), (20.1, "B"), (40.0, "B"), (40.1, "C"), (80.0, "D"), (80.1, "E"), (0.0, "A"), (100.0, "E")],
    )
    def test_gri_zone_boundaries(self, gri, zone, make_stats):
        assert st.gri_zone(gri) == zone and make_stats(gri=gri).gri_zone == zone

    def test_the_zone_belongs_to_the_displayed_gri(self):
        # 1.6 x 12.52 = 20.03: shown as 20.0, zone A (not zone B from the unrounded value)
        assert st.gri(0, 0, 0, 12.52) == (20.0, 0.0, 12.5, "A")
        assert st.gri(0, 0, 0, 12.6)[0] == 20.2 and st.gri(0, 0, 0, 12.6)[3] == "B"

    def test_the_report_gri_comes_from_the_unrounded_bands(self):
        # 42 of 4032 five-minute readings below 54 mg/dL: very low 1.0417% (shown 1.04 beside its target "<1%");
        # GRI 3 x 1.0417 = 3.125 -> 3.1
        start = local(2026, 10, 8)
        rd = [Rd(start + 300 * i, 50.0 if i < 42 else 120.0) for i in range(4032)]
        s = report(rd, 14)["standard"]
        assert s["very_low"] == 1.04 and s["gri"] == 3.1  # not 3.0, from a rounded 1.0
        stats = st.compute_period_stats(
            [GlucoseReading(r.timestamp, r.raw) for r in rd], "x", 14 * 86400, 300, **co.AGP_RANGES
        )
        assert stats.gri == s["gri"]  # the dashboard's GRI for the same readings


# ------------------------------------------------------------------------------------------------ the 70% rule before rounding
class TestSeventyPercentBeforeRounding:
    # 2821 five-minute readings in 14 days: 2821 x 300 s / 14 days = 69.965%, shown as 70.0%
    START = local(2026, 10, 8)

    def _rd(self):
        return [Rd(self.START + 300 * i, 150.0) for i in range(2821)]

    def test_report(self):
        s = report(self._rd(), 14)["standard"]
        assert s["active"] == 69.97 and s["enough_data"] is False  # beside the 70% rule: two decimals, not 70.0
        assert s["gmi"] is None and s["gmi_mmol_mol"] is None  # not GMI 6.9%
        # not enough data: no value of the period at all (the 70% rule of every period), the graph's data stay
        assert all(s[k] is None for k in ("tir", "mean", "sd", "cv", "gri", "min", "max")) and "per_day" not in s
        assert s["profile"]["p50"][40] == 150.0 and s["days_with_data"] == 10
        # 70% of 14 days = 846,720 s; the readings cover 2821 x 300 = 846,300 s: 420 s more, less than a day, so
        # whole hours, at least one
        assert s["more_needed"] == ["hours", 1]

    def test_statistics_and_change_over_time(self):
        rd = [GlucoseReading(r.timestamp, r.raw) for r in self._rd()]
        s = st.compute_period_stats(rd, "x", 14 * 86400, 300, **co.AGP_RANGES)
        assert round_half_up(s.data_availability, 1) == 70.0 and not s.sensor_active_ok  # unrounded: 69.965
        assert 69.96 < s.sensor_active_exact < 69.97
        values = ch._values(s, 14, True)
        assert values is None or values["gmi"] is None  # no values without enough data (change.py)

    def test_custom_appendix_is_not_compared(self):
        d = R.build_report(
            self._rd(),
            300,
            LA,
            14,
            False,
            [],
            now=NOW,
            custom={"ranges": {}, "goals": {"time_in_range": {"op": ">", "value": 50}}},
        )
        ca = d["custom_appendix"]
        assert ca["stats"]["active"] == 69.97 and ca["comparable"] is False and ca["stats"].get("gmi") is None
        assert ca["stats"]["enough_data"] is False and ca["stats"].get("tir") is None
        assert ca["rows"][0]["value"] is None and ca["rows"][0]["met"] is None


# ------------------------------------------------------------------------------------------------ the 70% rule of the previous period
class TestPreviousPeriodSeventyPercent:
    """A previous period has values (and is compared) only with at least 70% sensor active, compared before rounding,
    the rule of every period: 70% of 14 days is 846,720 s = 2822.4 five-minute readings, so 2822 readings (846,600 s,
    69.990%) are not enough and 2823 (846,900 s, 70.015%) are."""

    PREVIOUS = local(2026, 9, 24)  # the previous period of the 14-day report of 10/22: 09/24-10/07

    def _report(self, previous_readings: int) -> dict:
        rd = [Rd(self.PREVIOUS + 300 * i, 150.0) for i in range(previous_readings)]
        rd += readings(local(2026, 10, 8), local(2026, 10, 22), lambda t: 120.0, step=300)
        return report(rd, 14)["previous"]

    def test_just_below_seventy_percent_has_no_values(self):
        p = self._report(2822)
        assert p["active"] == 69.99 and p["enough_data"] is False  # beside the 70% rule: two decimals
        assert p["tir"] is None and p["mean"] is None and p["more_needed"] is None  # a past period: no estimate

    def test_seventy_percent_has_its_values(self):
        p = self._report(2823)
        # 70.0149% is 70.0 with one decimal, the rule's own value, so it is shown with two: 70.01 (units.shown_against)
        assert p["active"] == 70.01 and p["enough_data"] is True and p["tir"] == 100.0 and p["mean"] == 150.0

    def test_the_six_month_report_names_the_days_a_comparison_would_need(self):
        """The 6-month report has no previous period: with one it would need twice its length, 360 days of readings
        (its minute grid would begin one day earlier still, for the hypoglycemia events)."""
        d = report(readings(local(2026, 10, 1), local(2026, 10, 22), lambda t: 120.0, step=300), 180)
        assert d["previous"] is None and R.NO_PREVIOUS_DAYS * 2 == 360 and R.EVENT_LOOKBACK_DAYS == 1


# ------------------------------------------------------------------------------------------------ weekly trend weeks
def test_weekly_trend_weeks_are_seven_local_days():
    # 200 mg/dL before 10/26 00:00, 100 mg/dL from then; the report ends 11/02 00:00 (11/01 is the 25-hour day)
    cut = local(2026, 10, 26)
    rd = readings(local(2026, 10, 1), local(2026, 11, 2), lambda t: 200.0 if t < cut else 100.0, step=300)
    end_local = datetime(2026, 11, 2, tzinfo=LA)
    rows = {w["week_end"]: w for w in R.weekly_trend(rd, 300, end_local)}
    week = rows["2026-10-25"]  # the week of 10/19 to 10/25
    assert week["mean"] == 200 and week["tir"] == 0.0  # not 199 and 0.6 (one hour of 10/26)
    assert rows["2026-11-01"]["mean"] == 100 and rows["2026-11-01"]["tir"] == 100.0  # 10/26 to 11/01


# ------------------------------------------------------------------------------------------------ LO / HI in the hourly log and the event list
def test_lo_and_hi_readings_are_marked_and_no_value_in_the_hourly_log_and_stay_in_the_event_list():
    lo_a, hi_a = local(2026, 10, 13, 3, 0), local(2026, 10, 13, 15, 0)

    def value(t):
        if lo_a <= t < lo_a + 1800:
            return 40.0, "low"  # LO, stored at the sensor's lower limit
        if hi_a <= t < hi_a + 600:
            return 400.0, "high"  # HI, stored at the upper limit
        return 120.0

    d = report(readings(local(2026, 9, 1), NOW, value, step=300), 14, full=True)
    day = [row[0] for row in d["days"]["week"]].index("Tue 10-13")
    mins, maxs, lo, hi = d["days"]["log"][day]
    # 03:30 to 04:00 and 15:10 to 16:00 are measured at 120: the hour's numbers; the marks say that the hour has a limit
    assert (mins[3], maxs[3]) == (120, 120) and lo[3] is True and lo[4] is False
    assert (mins[15], maxs[15]) == (120, 120) and hi[15] is True and hi[14] is False
    ev = d["lows"]["list"]
    assert len(ev) == 1 and ev[0][2] == 40 and ev[0][5] is True


def test_period_stats_leave_limit_readings_out_of_the_values():
    rd = [
        GlucoseReading(1000 + 300 * i, v, limit=f)
        for i, (v, f) in enumerate([(40.0, "low"), (120.0, None), (400.0, "high"), (130.0, None)])
    ]
    s = st.compute_period_stats(rd, "x", 1200, 300, **co.AGP_RANGES)
    assert (s.min_value, s.max_value, s.mean) == (120.0, 130.0, 125.0)  # (120 + 130) / 2
    assert s.limit_seconds == {"low": 300, "high": 300} and (s.time_critical_low, s.time_critical_high) == (25.0, 25.0)


# ------------------------------------------------------------------------------------------------ the 4-week direction
class TestFourWeekDirection:
    """coordinator._directions: the mean of the last 4 weeks against the 4 weeks before, Stable within 2% of the
    page's scale (TREND_DIRECTION_SHARE; GlucoCraft rule)."""

    @staticmethod
    def _directions(weeks, gmi14=(), gri14=()):
        ns = types.SimpleNamespace(
            trend_week_values=co.GlucoCraftCoordinator.trend_week_values, trend_gmi14=list(gmi14)
        )
        return co.GlucoCraftCoordinator._directions(ns, weeks, list(gri14))

    def test_increasing_decreasing_stable(self, make_stats):
        up = [make_stats(pct_time_in_target_range=80.0)] * 4 + [make_stats(pct_time_in_target_range=70.0)] * 4
        down = list(reversed(up))
        flat = [make_stats(pct_time_in_target_range=70.9)] * 4 + [make_stats(pct_time_in_target_range=70.0)] * 4
        assert self._directions(up)["tir"] == "Increasing"
        assert self._directions(down)["tir"] == "Decreasing"
        assert self._directions(flat)["tir"] == "Stable"  # 0.9 points <= 2% of the 50-100 scale (1.0)

    def test_not_enough_weeks_or_empty_weeks(self, make_stats):
        assert self._directions([make_stats()] * 7)["tir"] is None
        assert self._directions([None] * 4 + [make_stats()] * 4)["tir"] is None

    def test_gmi_uses_the_14_day_gmi_of_each_week(self, make_stats):
        weeks = [make_stats()] * 8
        assert self._directions(weeks, [7.0] * 4 + [6.0] * 4)["gmi"] == "Increasing"
        assert self._directions(weeks)["gmi"] is None  # no 14-day GMI: no direction

    def test_gri_uses_the_14_day_gri_of_each_week(self, make_stats):
        """As the GMI: the 14-day GRI at each week's end (coordinator.trend_gri14), never the week's own value; the
        weeks on the ranges in the settings have no GRI (an empty list), so no GRI direction."""
        weeks = [make_stats(gri=10.0)] * 8
        assert self._directions(weeks, gri14=[30.0] * 4 + [20.0] * 4)["gri"] == "Increasing"
        assert self._directions(weeks)["gri"] is None


# ------------------------------------------------------------------------------------------------ the 14-day GMI of the weekly statistics
def test_weekly_statistics_sensor_uses_the_14_day_gmi(make_stats):
    from glucocraft import sensor as se

    # a week: 7 days covered in full has enough data, 1 hour of it has not (the enough-data rule)
    weeks = [
        make_stats(gmi=8.8, period_seconds=7 * 86400, covered_seconds=7 * 86400.0),
        make_stats(period_seconds=7 * 86400, covered_seconds=3600.0),
    ]
    c = types.SimpleNamespace(
        weekly_trends=weeks,
        weekly_trends_agp=weeks,
        weekly_trends_at=None,
        trend_gmi14=[6.4, 6.5],
        trend_gri14=[21.3, 22.0],
        trend_directions={},
        detected_interval_seconds=300,
    )
    ns = types.SimpleNamespace(coordinator=c)
    ns._has_data = types.MethodType(se.GlucoCraftWeeklyStatistics._has_data, ns)
    attrs = se.GlucoCraftWeeklyStatistics.extra_state_attributes.fget(ns)
    first, second = attrs["weeks"]
    assert first["has_data"] is True and first["gmi"] == 6.4  # the 14-day GMI at the week's end, not 8.8
    assert first["consensus"]["gmi"] == 6.4
    # the GRI as the GMI: the 14-day GRI at the week's end (on the consensus bands), not the week's own
    assert first["gri"] == first["consensus"]["gri"] == 21.3 and weeks[0].gri != 21.3
    assert "gri_hypo" not in first and "gri_hyper" not in first
    assert second == {"has_data": False}  # 1 hour of data: less than the enough-data rule's 70% of the week


def test_api_period_gmi_rule(make_stats):
    from glucocraft import http_api

    ok = make_stats(gmi=6.9, data_availability=95.0)
    assert http_api._period_json(ok, 14 * 86400, "mg/dL")["gmi"] == 6.9
    assert http_api._period_json(ok, 7 * 86400, "mg/dL")["gmi"] is None  # less than 14 days
    low = make_stats(gmi=6.9, data_availability=60.0)
    assert http_api._period_json(low, 14 * 86400, "mg/dL")["gmi"] is None  # less than 70% sensor active


# ------------------------------------------------------------------------------------------------ the report's last midnight
LAST_MIDNIGHT = local(2026, 10, 22)


class TestEventsAtTheLastMidnight:
    """An event running at the report's last midnight keeps its whole duration and lowest value (the minute grid reaches
    to the newest reading), and the report says up to which reading it depends (reads_until, the cache key)."""

    def test_an_event_running_at_the_last_midnight_keeps_its_whole_duration(self):
        # 60 minutes at 60 mg/dL from 23:30 to 00:30 (not cut at midnight: 30 minutes), lowest 55 after midnight
        def value(t):
            return 55.0 if LAST_MIDNIGHT + 600 <= t < LAST_MIDNIGHT + 900 else 60.0

        rd = readings(
            local(2026, 9, 1), NOW, lambda t: value(t) if LAST_MIDNIGHT - 1800 <= t < LAST_MIDNIGHT + 1800 else 120.0
        )
        lw = report(rd, 14)["lows"]
        assert lw["events"] == 1 and lw["list"][0][1] == 60 and lw["list"][0][2] == 55.0

    def test_an_event_that_becomes_one_after_midnight_is_counted_where_it_starts(self):
        # below 70 from 23:55: an event only after midnight (15 minutes), counted in the period
        rd = readings(local(2026, 9, 1), NOW, low_between(LAST_MIDNIGHT - 300, LAST_MIDNIGHT + 1500))
        lw = report(rd, 14)["lows"]
        assert lw["events"] == 1 and lw["list"][0][1] == 30

    def test_reads_until_follows_an_open_event_and_stops_at_the_first_reading_after_it(self):
        quiet = report(readings(local(2026, 9, 1), NOW, lambda t: 120.0), 14)
        assert quiet["reads_until"] == LAST_MIDNIGHT  # the first reading at or after the last midnight
        rd = readings(local(2026, 9, 1), NOW, low_between(LAST_MIDNIGHT - 1800, LAST_MIDNIGHT + 1800))
        until = report(rd, 14)["reads_until"]
        assert LAST_MIDNIGHT + 1800 < until < NOW  # after the 15 minutes at or above 70 that end the event
        assert (
            report(rd[: rd.index(next(r for r in rd if r.timestamp == LAST_MIDNIGHT + 600))], 14)["reads_until"] is None
        )
        assert report(readings(local(2026, 9, 1), LAST_MIDNIGHT - 300, lambda t: 120.0), 14)["reads_until"] is None

    def test_a_report_without_new_readings_needs_no_rebuild_after_the_event(self):
        """The report data are the same whatever arrives after reads_until."""
        rd = readings(local(2026, 9, 1), NOW, low_between(LAST_MIDNIGHT - 1800, LAST_MIDNIGHT + 1800))
        full = report(rd, 30, full=True)
        until = full.pop("reads_until")
        cut = report([r for r in rd if r.timestamp <= until], 30, full=True)
        cut.pop("reads_until", None)
        assert cut == full


class TestLevel2CountedWithItsLevel1Event:
    def test_a_level_2_event_inside_a_level_1_event_from_the_previous_period(self):
        """Below 70 from 20 minutes before the period's first midnight, below 54 only after it: the level 1 event
        starts in the previous period, so its level 2 event is counted there too (not 0 level 1 and 1 level 2)."""
        first = local(2026, 10, 8)  # the 14-day period's first midnight

        def value(t):
            return 50.0 if first <= t < first + 1800 else 62.0 if first - 1200 <= t < first + 2400 else 120.0

        d = report(readings(local(2026, 9, 1), NOW, value), 14)
        assert (d["lows"]["events"], d["lows"]["events_54"]) == (0, 0)
        assert (report(readings(local(2026, 9, 1), NOW, value), 30)["lows"]["events_54"]) == 1  # both in 30 days
        # the full report's summary: "of which reached level 2" in the column where the level 1 event starts
        cols = report(readings(local(2026, 9, 1), NOW, value), 30, full=True)["low_events"]
        assert [(c["days"], c["events"], c["level2"]) for c in cols] == [(14, 0, 0), (30, 1, 1)]


def test_lowest_is_limit_bisection_equals_the_whole_grid():
    """_lowest_is_limit looks at the event's own segments (bisection); the result equals the scan of every segment."""

    def value(t):
        lt = datetime.fromtimestamp(t, LA)
        if lt.hour == 3 and lt.minute < 30:
            return (40.0, "low") if lt.minute < 5 and lt.day % 2 else 50.0
        return 120.0

    rd = readings(local(2026, 9, 1), NOW, value, step=300)
    g = R.minute_grid(rd, 300, rd[0].timestamp, rd[-1].timestamp + 300, LA)
    events = R.report_events(g)
    assert len(events) > 20
    whole = [
        any(v == ev[2] and f == "low" for a, e, v, f in g["_segs"] if a < ev[0] + ev[1] and e > ev[0]) for ev in events
    ]
    assert [R._lowest_is_limit(g, ev) for ev in events] == whole and any(whole) and not all(whole)
