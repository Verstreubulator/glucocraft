# License: AGPL-3.0-only (LICENSE).
"""The published rules of the CGM Report and of Change Over Time at their exact boundaries, on both sides.

Rules under test (each case lies exactly on the boundary, so that a rule moved by one step fails):
- The CGM Report's 70% rule (Battelino et al., 2019, Table 2): exactly 70.0% sensor active meets it, in the report
  period, in the custom appendix and in the 90 days before a laboratory result; the custom goals compare the unrounded
  value (3.95% meets "below 4%", although it is shown as 4.0 with one decimal).
- The laboratory comparison: the GMI of the 90 local days ending on the laboratory date (GlucoCraft rule); a window
  that starts on the oldest day the retention keeps is still compared.
- Hypoglycemia events (Danne et al., 2017, Section 4): only readings at or above 70 mg/dL count towards the end of an
  event, also of a level 2 event (69.9 mg/dL does not); an interval of exactly the gap rule's 15 minutes is no gap
  and does not end an event, one second more is a gap and ends it.
- Change Over Time (the enough-data rule): a window with sensor active exactly 70% of it has values, one with a minute
  less has none; a window with a single reading has none, whatever its reading interval; the hours of data are
  rounded, halves up.
- The independent calculator (tools/synth/expected.py) stores a Dexcom 401 at the upper limit in use, as limits.py.
"""

from __future__ import annotations

import importlib
from datetime import datetime
from fractions import Fraction

import pytest
from glucocraft import change as ch
from glucocraft import limits as L
from glucocraft import report as R
from glucocraft.const import AGP_RANGES
from glucocraft.regional import Regional
from glucocraft.units import round_half_up

from conftest import LA, Rd, add_reading

E = importlib.import_module("tools.synth.expected")


def local(*args) -> int:
    return int(datetime(*args, tzinfo=LA).timestamp())


# The 14-day report of 10/22 09:00 (Los Angeles): the period 10/08-10/21, 14 days of 86,400 s (no daylight-saving
# change); 70% of it is 846,720 s, exactly 14,112 one-minute readings
NOW = local(2026, 10, 22, 9, 0)
START = local(2026, 10, 8)
SEVENTY_PCT_MINUTES = 14 * 1440 * 7 // 10


def minutes(count: int, value=lambda t: 150.0, start: int = START) -> list[Rd]:
    """`count` one-minute readings from `start`; value(t) -> mg/dL."""
    return [Rd(start + 60 * i, float(value(start + 60 * i))) for i in range(count)]


def report(rd: list[Rd], custom: dict | None = None) -> dict:
    return R.build_report(rd, 60, LA, 14, False, [], now=NOW, regional=Regional("24", "YMD"), custom=custom)


# ------------------------------------------------------------------------------------------------ the 70% rule
class TestSeventyPercentExactly:
    def test_the_report_period(self):
        s = report(minutes(SEVENTY_PCT_MINUTES))["standard"]
        assert s["active"] == 70.0 and s["enough_data"] is True and s["gmi"] is not None
        below = report(minutes(SEVENTY_PCT_MINUTES - 1))["standard"]
        assert below["enough_data"] is False and below["gmi"] is None and below["tir"] is None

    def test_the_custom_appendix(self):
        custom = {"ranges": {}, "goals": {"time_in_range": {"op": ">", "value": 50}}}
        ca = report(minutes(SEVENTY_PCT_MINUTES), custom)["custom_appendix"]
        assert ca["stats"]["active"] == 70.0 and ca["comparable"] is True
        assert next(r for r in ca["rows"] if r["key"] == "time_in_range")["met"] is True
        ca = report(minutes(SEVENTY_PCT_MINUTES - 1), custom)["custom_appendix"]
        assert ca["comparable"] is False

    def test_a_custom_goal_compares_the_unrounded_value(self):
        # 797 of 20,160 minutes at 65 mg/dL: 3.953% below 70, shown as 3.95 beside the goal "below 4%" that it meets
        low = range(START + 3600, START + 3600 + 797 * 60)
        rd = minutes(14 * 1440, lambda t: 65.0 if t in low else 150.0)
        ca = report(rd, {"ranges": {}, "goals": {"time_low": {"op": "<", "value": 4}}})["custom_appendix"]
        row = next(r for r in ca["rows"] if r["key"] == "time_low")
        assert row["met"] is True and row["value"] == 3.95


# ------------------------------------------------------------------------------------------------ laboratory A1C
LAB_DAY = "2026-10-20"
WINDOW_START = datetime(2026, 7, 23, tzinfo=LA)  # the 90 local days ending on 10/20 (no daylight-saving change)


def five_minutes(count: int, value=lambda t: 150.0, start: int | None = None) -> list[Rd]:
    """`count` five-minute readings from `start` (the window's first midnight unless given)."""
    s = int(WINDOW_START.timestamp()) if start is None else start
    return [Rd(s + 300 * i, float(value(s + 300 * i))) for i in range(count)]


def lab(rd: list[Rd], kept_from: datetime | None = None) -> dict:
    return R.lab_payload([{"date": LAB_DAY, "a1c": 6.5}], rd, 300, LA, kept_from=kept_from)["results"][0]


class TestLaboratoryComparison:
    # 70% of 90 days is 5,443,200 s: exactly 18,144 five-minute readings
    def test_exactly_seventy_percent_gives_the_gmi(self):
        r = lab(five_minutes(18144))
        assert r["cgm_active_90d"] == 70.0 and r["gmi_90d"] is not None
        assert lab(five_minutes(18143))["gmi_90d"] is None

    def test_the_window_is_90_local_days(self):
        # 300 mg/dL on the window's first day, 100 mg/dL on the 89 days after it and on the day before it: the mean of
        # 90 days is 9200 / 90 = 102.2 mg/dL (GMI 5.8%); a window of 89 days would see 100 mg/dL (GMI 5.7%)
        first = int(WINDOW_START.timestamp())
        rd = five_minutes(91 * 288, lambda t: 300.0 if first <= t < first + 86400 else 100.0, start=first - 86400)
        r = lab(rd)
        assert r["cgm_active_90d"] == 100.0 and round_half_up(r["gmi_90d"], 1) == 5.8

    def test_a_window_from_the_oldest_day_kept_is_compared(self):
        rd = five_minutes(90 * 288)
        assert lab(rd, kept_from=WINDOW_START)["no_cgm_comparison"] is False
        later = datetime(2026, 7, 24, tzinfo=LA)
        assert lab(rd, kept_from=later)["no_cgm_comparison"] is True


# ------------------------------------------------------------------------------------------------ hypoglycemia events
EVENT_AT = local(2026, 10, 13, 14, 0)


def lows(rd: list[Rd]) -> dict:
    return report(rd)["lows"]


class TestEventEnd:
    def test_readings_just_below_70_do_not_end_a_level_2_event(self):
        # 20 minutes at 50, 15 minutes at 69.9, 20 minutes at 50, then 120 mg/dL: one level 1 event of 55 minutes and
        # one level 2 event in it (69.9 mg/dL does not count towards the end), not two level 2 events
        def value(t):
            if EVENT_AT + 1200 <= t < EVENT_AT + 2100:
                return 69.9
            return 50.0 if EVENT_AT <= t < EVENT_AT + 3300 else 120.0

        lw = lows(minutes(14 * 1440, value))
        assert (lw["events"], lw["events_54"]) == (1, 1) and lw["list"][0][1] == 55 and lw["list"][0][4] == 2

    @pytest.mark.parametrize("interval, events", [(900, 1), (901, 2)])
    def test_an_interval_of_the_gap_rule_does_not_end_an_event(self, interval, events):
        # one-minute readings at 60 mg/dL for 20 minutes, one interval of `interval` seconds, 20 minutes more at 60
        before = [Rd(EVENT_AT + 60 * i, 60.0) for i in range(20)]
        after_at = before[-1].timestamp + interval
        after = [Rd(after_at + 60 * i, 60.0) for i in range(20)]
        rd = minutes(1440 * 6, start=EVENT_AT - 6 * 86400) + before + after
        # to the report's last midnight, so that the period has enough data for its events (the 70% rule)
        rest = (local(2026, 10, 22) - after[-1].timestamp - 60) // 60
        rd += minutes(rest, start=after[-1].timestamp + 60, value=lambda t: 120.0)
        assert lows(rd)["events"] == events


# ------------------------------------------------------------------------------------------------ Change Over Time
NOW_STORE = 1_800_000_000


def change(tmp_path, times: list[int]):
    """The consensus Change Over Time of a store holding a reading at 150 mg/dL at each time."""
    from glucocraft.storage import GlucoCraftStorage

    s = GlucoCraftStorage(str(tmp_path), "t")
    s.load()
    for t in times:
        add_reading(s, t, 150.0)
    return ch.compute_change_sets(s, 60, NOW_STORE, (AGP_RANGES,))[0]


def minute_times(count: int) -> list[int]:
    start = NOW_STORE - 86400
    return [start + 60 * i for i in range(count)]


def minutes_before_now(count: int) -> list[int]:
    """One-minute readings ending one minute before NOW_STORE: count x 60 s of covered time."""
    return [NOW_STORE - 60 * (count - i) for i in range(count)]


class TestChangeOverTimeWeekRule:
    """The windows follow the enough-data rule: sensor active at least 70% of the window. 70% of 7 days is 423,360 s,
    7056 one-minute readings; of 14 days 14,112 readings."""

    def test_exactly_seventy_percent_of_seven_days(self, tmp_path):
        out = change(tmp_path, minutes_before_now(7056))
        assert out.weeks7[0] is not None and out.weeks7[0]["hours"] == 118  # 117.6 hours
        # the same readings are 35% of 14 days and 17.5% of 28 days: not enough there
        assert out.weeks14[0] is None and out.stats14[0] is None and out.compare["last_4_weeks"] is None

    def test_one_minute_less(self, tmp_path):
        out = change(tmp_path, minutes_before_now(7055))
        assert out.weeks7[0] is None and out.weeks14[0] is None and out.stats14[0] is None

    def test_seventy_percent_of_fourteen_days(self, tmp_path):
        out = change(tmp_path, minutes_before_now(14112))
        assert out.weeks14[0] is not None and out.stats14[0] is not None and out.weeks7[0]["hours"] == 168

    def test_hours_of_data_are_rounded_half_up(self, tmp_path):
        assert change(tmp_path, minutes_before_now(7110)).weeks7[0]["hours"] == 119  # 118.5 hours

    def test_a_window_with_one_reading_has_no_values(self, tmp_path):
        # a reading every 8 days: each 7-day window holds one reading, whose own interval (8 days) is not a gap
        out = change(tmp_path, [NOW_STORE - 86400 - k * 8 * 86400 for k in range(24, -1, -1)])
        assert out.weeks7[0] is None
        assert out.weeks14[0] is not None  # two readings: the window is computed


# ------------------------------------------------------------------------------------------------ the oracle
@pytest.mark.parametrize("upper, upper_set", [(400, False), (350, True), (500, False)])
def test_the_calculator_stores_a_dexcom_401_at_the_upper_limit_in_use(upper, upper_set):
    kind, mg = E.classify("401", "mg/dL", upper=upper, dexcom=True, upper_set=upper_set)
    real = L.classify("401", "mg/dL", upper=upper, dexcom_type=True, upper_set=upper_set)
    assert (kind, mg) == ("high", Fraction(upper)) and (real.kind, real.value) == ("high", float(upper))
