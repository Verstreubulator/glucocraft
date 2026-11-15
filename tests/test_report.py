# License: AGPL-3.0-only (LICENSE).
"""The CGM Report: calculations (report.py), the PDF (report_pdf.py), printing over IPP and the time and date
formats (regional.py).

Rules under test: the seconds each reading represents, cut at every quarter hour, and the minute grid of the
display bins, both on local time per 15 minutes (half-hour and 45-minute time zones); full local days, also across daylight-saving changes (23- and
25-hour days); the monthly summary covering every calendar month of the period; GMI only for 14 days or more with
at least 70 % data; hypoglycemia events after 15 consecutive minutes below the limit, ending after 15 consecutive minutes at
or above 70 mg/dL (Danne et al., 2017, Section 4); PDF labels in the display unit and the time format; IPP replies
parsed without exceptions.
"""

from __future__ import annotations

import http.server
import io
import math
import socket
import ssl
import struct
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import pytest
from glucocraft import regional as rg
from glucocraft import report as R
from glucocraft import report_pdf as P
from glucocraft import statistics as st
from glucocraft.regional import Regional
from glucocraft.units import fmt, round_half_up

from conftest import KATHMANDU, KOLKATA, LA, Rd


def series(start: int, end: int, step: int = 300, value=lambda t: 120.0) -> list[Rd]:
    return [Rd(t, float(value(t))) for t in range(start, end, step)]


# ------------------------------------------------------------------------------------------------ minute grid
class TestMinuteGrid:
    @pytest.mark.parametrize("tz", [KOLKATA, KATHMANDU, LA], ids=["+05:30", "+05:45", "LA"])
    def test_time_of_day_bins_are_local(self, tz):
        s0 = int(datetime(2026, 10, 1, 0, 0, tzinfo=tz).timestamp())
        g = R.minute_grid([Rd(s0 + 60 * k, 100.0) for k in range(24 * 60)], 60, s0, s0 + 86400, tz)
        assert len(g["mins"]) == 1440
        for m, b in zip(g["mins"], g["tod"], strict=True):
            local = datetime.fromtimestamp(m, tz)
            assert b == local.hour * 4 + local.minute // 15
        assert g["tod"][30] == 2 and g["tod"][15] == 1 and g["tod"][0] == 0
        for a, _, _, b, d in g["_parts"]:
            local = datetime.fromtimestamp(a, tz)
            assert b == local.hour * 4 + local.minute // 15 and d == local.date()

    def test_each_reading_covers_until_the_next_or_one_interval_before_a_gap(self):
        # the one gap rule (gaps.py): 3300 s > max(900, 3 x 300) is a gap, so the reading before it covers only its
        # own interval (300 s)
        g = R.minute_grid([Rd(0, 100.0), Rd(300, 200.0), Rd(3600, 150.0)], 300, 0, 4000, LA)
        assert g["vals"].count(100.0) == 5 and g["vals"].count(200.0) == 5
        assert g["vals"].count(150.0) == 5  # the last: one interval

    def test_window_edges(self):
        g = R.minute_grid([Rd(0, 100.0), Rd(600, 110.0)], 600, 300, 900, LA)
        assert g["mins"] == [300, 360, 420, 480, 540, 600, 660, 720, 780, 840]

    def test_the_parts_are_the_seconds_cut_at_each_quarter_hour(self):
        # readings off the whole minute: each represents its seconds to the next reading (the statistics sensors'
        # weights), cut at 900 and 1800 s; the minute grid of the display bins holds only the whole minutes
        g = R.minute_grid([Rd(880, 100.0), Rd(1000, 110.0), Rd(1801, 120.0)], 60, 0, 3600, LA)
        assert [(a, e, k) for a, e, k, _, _ in g["_parts"]] == [
            (880, 900, 0),
            (900, 1000, 0),
            (1000, 1800, 1),
            (1800, 1801, 1),
            (1801, 1861, 2),
        ]
        assert g["mins"] == [*range(900, 1801, 60), 1860]
        # a window off the cuts keeps only its own seconds
        assert [p[:2] for p in R._within(g, 950, 1800)] == [(950, 1000), (1000, 1800)]

    def test_empty(self):
        g = R.minute_grid([], 300, 0, 86400, LA)
        assert {k: g[k] for k in ("mins", "vals", "tod", "_parts")} == {"mins": [], "vals": [], "tod": [], "_parts": []}

    @pytest.mark.parametrize(
        "day, minutes",
        [((2026, 11, 1), 1500), ((2026, 3, 8), 1380), ((2026, 10, 22), 1440)],
        ids=["fall-back-25h", "spring-forward-23h", "normal"],
    )
    def test_dst_days(self, day, minutes):
        a = datetime(*day, tzinfo=LA)
        s, e = int(a.timestamp()), int((a + timedelta(days=1)).timestamp())
        g = R.minute_grid(series(s - 600, e + 600, 60), 60, s, e, LA)
        assert len(g["mins"]) == minutes and {p[4] for p in g["_parts"]} == {a.date()}
        assert sum(e - b for b, e, *_ in g["_parts"]) == 60 * minutes
        assert min(g["tod"]) == 0 and max(g["tod"]) == 95


# ------------------------------------------------------------------------------------------------ small pieces
def low_events(segs: list, limit: float = R.LEVEL1_LIMIT) -> list:
    """The hypoglycemia events of the readings `segs` (from, to, value): report.report_events on a grid of them."""
    return R.report_events({"_segs": segs}, limit)


def active_in(readings: list, interval: int, start: int, end: int) -> float:
    """Sensor active of the readings between start and end as the report calculates it (the period's statistics)."""
    g = R.minute_grid(readings, interval, start, end, LA)
    _, s = R._period(g, start, end)
    return s.sensor_active_exact if s is not None else 0.0


def test_coverage_pct():
    assert active_in([], 300, 0, 3600) == 0.0
    assert active_in(series(0, 3600), 300, 0, 3600) == 100.0
    half = series(0, 1800)
    assert active_in(half, 300, 0, 3600) == 50.0
    gap = [Rd(0, 1), Rd(900, 1), Rd(2700, 1)]  # 15 min covered, 30 min a gap (the reading before it: 300 s)
    assert active_in(gap, 300, 0, 3600) == st.sensor_active(900 + 300 + 300, 3600) == 100.0 * 1500 / 3600
    assert st.sensor_active(0, 0) == 0.0 and st.sensor_active(4000, 3600) == 100.0


@pytest.mark.parametrize(
    "args, zone",
    [
        ((0, 0, 0, 0), "A"),
        ((0, 0, 0, 12.5), "A"),
        ((0, 0, 0, 12.6), "B"),
        ((0, 0, 50, 0), "B"),
        ((0, 0, 75, 0), "C"),
        ((0, 0, 100, 0), "D"),
        ((0, 0, 0, 100), "E"),
        ((50, 0, 0, 0), "E"),
    ],
)
def test_gri_zones(args, zone):
    """gri(very low %, low %, high %, very high %): zones A 0-20, B 21-40, C 41-60, D 61-80, E 81-100."""
    g, _hypo, _hyper, z = st.gri(*args)
    assert z == zone and 0 <= g <= 100


def test_gri_formula():
    g, hypo, hyper, _z = st.gri(1.0, 3.0, 20.0, 5.0)
    assert hypo == pytest.approx(1 + 0.8 * 3) and hyper == pytest.approx(5 + 0.5 * 20)
    assert g == pytest.approx(round(3.0 * 1 + 2.4 * 3 + 1.6 * 5 + 0.8 * 20, 1))


def test_percentile_interpolation():
    v = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert R._pct(v, 50) == 3.0 and R._pct(v, 0) == 1.0 and R._pct(v, 100) == 5.0
    assert R._pct(v, 25) == 2.0 and R._pct(v, 5) == pytest.approx(1.2)
    assert R._pct([7.0], 95) == 7.0


def minute_readings(vals, starts=None):
    """One reading per minute (from, to, value): the readings `segs` of low_events (report.report_events)."""
    starts = starts if starts is not None else [60 * i for i in range(len(vals))]
    return [(t, t + 60, float(v)) for t, v in zip(starts, vals, strict=True)]


def in_minutes(events):
    """The events with their duration in whole minutes (low_events gives seconds)."""
    return [(s, d / 60, v) for s, d, v in events]


class TestLowEvents:
    """Danne et al. (2017), Section 4: an event starts after 15 consecutive minutes below the limit (70 or 54 mg/dL)
    and ends after 15 consecutive minutes at or above 70 mg/dL, for every level."""

    @staticmethod
    def events(pattern):
        return in_minutes(low_events(minute_readings(pattern)))

    def test_14_minutes_is_not_an_event(self):
        assert self.events([100] * 10 + [60] * 14 + [100] * 30) == []

    def test_15_minutes_is_an_event(self):
        ev = self.events([100] * 10 + [60] * 15 + [100] * 30)
        assert len(ev) == 1 and ev[0][0] == 600 and ev[0][1] == 15 and ev[0][2] == 60

    def test_short_recovery_joins_two_dips(self):
        ev = self.events([100] * 5 + [60] * 20 + [100] * 10 + [55] * 20 + [100] * 30)
        assert len(ev) == 1 and ev[0][2] == 55

    def test_15_minute_recovery_separates(self):
        ev = self.events([100] * 5 + [60] * 20 + [100] * 15 + [65] * 20 + [100] * 30)
        assert len(ev) == 2

    def test_level_2_limit(self):
        assert len(low_events(minute_readings([50.0] * 20 + [100.0] * 40), 54)) == 1
        assert low_events(minute_readings([60.0] * 20 + [100.0] * 40), 54) == []

    # Danne et al. (2017), Section 4, the treatment standard: every event, level 2
    # included, ends after >= 15 consecutive min at >= 70 mg/dL. Context only: Battelino et al. (2023), Table 4
    # (clinical trials) ends level 2 at >= 54 mg/dL; that rule is not used.
    @staticmethod
    def events_at(pattern, limit):
        return in_minutes(low_events(minute_readings(pattern), limit))

    def test_level_2_ends_at_70_not_54(self):
        # 20 min at 50, 15 min at 60 (>= 54 but < 70), then 30 min at 100: the level 2 event continues through the
        # 60s and ends only after 15 min at >= 70, like the level 1 event
        p = [100] * 5 + [50] * 20 + [60] * 15 + [100] * 30
        ev2 = self.events_at(p, 54)
        assert len(ev2) == 1 and ev2[0][0] == 300 and ev2[0][1] == 35 and ev2[0][2] == 50
        ev1 = self.events_at(p, 70)
        assert ev1 == ev2

    def test_level_2_continues_between_54_and_69(self):
        # two dips below 54 with 15 min at 60 between them: one level 2 event and one level 1 event
        p = [100] * 5 + [50] * 20 + [60] * 15 + [50] * 20 + [100] * 30
        ev2 = self.events_at(p, 54)
        assert len(ev2) == 1 and ev2[0][1] == 55 and ev2[0][2] == 50
        assert len(self.events_at(p, 70)) == 1

    def test_level_2_values_between_54_and_69_restart_the_end_count(self):
        # 14 min at 100, 1 min at 60, 14 min at 100: never 15 consecutive minutes at >= 70, so the event goes on
        p = [100] * 5 + [50] * 20 + [100] * 14 + [60] + [100] * 14 + [100] * 16
        ev2 = self.events_at(p, 54)
        assert len(ev2) == 1 and ev2[0][1] == 20 + 14 + 1  # to the last minute before the 15 at >= 70

    def test_level_2_two_events_separated_by_15_minutes_at_70(self):
        p = [100] * 5 + [50] * 20 + [70] * 15 + [50] * 20 + [100] * 30
        assert len(self.events_at(p, 54)) == 2
        assert len(self.events_at(p, 70)) == 2

    def test_limit_value_counts_as_at_or_above(self):
        # a reading equal to the limit is not below it; only >= 70 counts towards the end (Section 4: ">=70 mg/dL")
        assert self.events_at([54] * 30, 54) == [] and self.events_at([70] * 30, 70) == []
        p1 = [100] * 5 + [60] * 20 + [70] * 15 + [60] * 20 + [100] * 30
        assert len(self.events_at(p1, 70)) == 2
        p2 = [100] * 5 + [50] * 20 + [54] * 15 + [50] * 20 + [100] * 30
        assert len(self.events_at(p2, 54)) == 1

    def test_level_2_start_needs_15_minutes_below_54(self):
        # 14 min below 54 inside a long level 1 event: a level 1 event, no level 2 event
        p = [100] * 5 + [65] * 10 + [50] * 14 + [65] * 10 + [100] * 30
        assert self.events_at(p, 54) == []
        assert len(self.events_at(p, 70)) == 1

    def test_level_2_start_run_broken_by_a_value_between_54_and_69(self):
        # before an event, a reading at or above 54 breaks the run below 54: 10 + 10 minutes is not an event
        p = [100] * 5 + [50] * 10 + [60] + [50] * 10 + [100] * 30
        assert self.events_at(p, 54) == []

    def test_event_running_at_the_end(self):
        ev = self.events([100] * 5 + [60] * 20)
        assert len(ev) == 1 and ev[0][1] == 20

    def test_long_gap_ends_an_event(self):
        starts = [60 * i for i in range(20)] + [60 * i for i in range(60, 80)]
        ev = low_events(minute_readings([60.0] * 40, starts))
        assert len(ev) == 2

    def test_a_first_reading_shorter_than_a_minute_starts_the_event(self):
        """The event starts at its first reading below the limit, at that reading's own time, also when the reading
        covers less than a whole minute (10:30:01 to 10:31:00), and lasts the seconds its readings represent."""
        segs = [(0, 601, 100.0), (601, 660, 60.0)] + [(660 + 60 * i, 720 + 60 * i, 60.0) for i in range(15)]
        segs.append((1560, 2460, 100.0))
        assert low_events(segs) == [(601, 1560 - 601, 60.0)]
        # 14 minutes 59 seconds below the limit is not yet an event
        assert low_events([(0, 601, 100.0), (601, 660, 60.0), (660, 1500, 60.0), (1500, 2400, 100.0)]) == []


# ------------------------------------------------------------------------------------------------ the whole report
NOW_LOCAL = datetime(2026, 10, 22, 9, 0, tzinfo=LA)
NOW = NOW_LOCAL.timestamp()


@pytest.fixture(scope="module")
def year_of_data():
    start = int(NOW) - 220 * 86400
    return [
        Rd(t, 120 + 80 * math.sin(t / 7000.0) - (40 if (t // 3600) % 24 == 3 else 0))
        for t in range(start, int(NOW) - 3600, 300)
    ]


class TestBuildReport:
    def test_periods_end_at_last_local_midnight(self, year_of_data):
        out = R.build_report(year_of_data, 300, LA, 30, False, [], now=NOW, regional=Regional())
        assert out["standard"]["period_end"] == "2026-10-21" and out["standard"]["period_start"] == "2026-10-08"
        assert out["period"]["days"] == 30 and out["period"]["period_end"] == "2026-10-21"
        assert out["previous"]["period_end"] == (datetime(2026, 9, 21)).strftime("%Y-%m-%d")
        assert "monthly" not in out and len(out["blocks"]) == 3  # 14 + 14 + 2 days
        assert [b["days"] for b in out["blocks"]] == [2, 14, 14]

    def test_bands_sum_and_gmi(self, year_of_data):
        p = R.build_report(year_of_data, 300, LA, 14, False, [], now=NOW)["standard"]
        assert p["very_low"] + p["low"] + p["tir"] + p["high"] + p["very_high"] == pytest.approx(100, abs=0.3)
        # time below 70 is the combined band rounded once, not the sum of its two rounded parts
        start = int(datetime(2026, 10, 8, tzinfo=LA).timestamp())
        end = int(datetime(2026, 10, 22, tzinfo=LA).timestamp())
        _, exact = R._period(R.minute_grid(year_of_data, 300, start - 86400, end, LA), start, end)
        assert p["below_70"] == round_half_up(exact.time_below_low, 1)
        assert p["gmi"] is not None and p["gri_zone"] in "ABCDE" and p["active"] >= 70
        assert len(p["profile"]["p50"]) == 96 and len(p["daily"]) == 14

    def test_no_gmi_with_too_little_data(self):
        start = int(NOW) - 14 * 86400
        sparse = [Rd(t, 120.0) for t in range(start, int(NOW), 300) if (t // 86400) % 2 == 0]  # every other day
        p = R.build_report(sparse, 300, LA, 14, False, [], now=NOW)["standard"]
        assert p["active"] < 70 and p["gmi"] is None

    def test_empty(self):
        out = R.build_report([], 300, LA, 14, False, [], now=NOW)
        assert out["standard"]["tir"] is None and out["period"]["tir"] is None

    @pytest.mark.parametrize(
        "now_local, ndays, months",
        [
            # (the report's creation time, its length, the months shown: label, first and last day of the month shown)
            (datetime(2026, 11, 5, 9, 0, tzinfo=LA), 180, None),
            (
                datetime(2026, 10, 22, 9, 0, tzinfo=LA),
                30,
                [("September 22 – September 30, 2026", 22, 30), ("October 1 – October 21, 2026", 1, 21)],
            ),
            (datetime(2026, 11, 1, 9, 0, tzinfo=LA), 30, [("October 2 – October 31, 2026", 2, 31)]),
            (datetime(2026, 10, 1, 9, 0, tzinfo=LA), 30, [("September 2026", 1, 30)]),
        ],
    )
    def test_monthly_covers_every_month_of_the_period(self, now_local, ndays, months):
        """The full report's monthly summary covers every calendar month the period touches, oldest first, with only
        the period's days; a partial month is labeled with its first and last day."""
        now = now_local.timestamp()
        rds = [Rd(t, 120.0) for t in range(int(now) - 220 * 86400, int(now) - 3600, 300)]
        out = R.build_report(rds, 300, LA, ndays, True, [], now=now, regional=Regional())
        mo = out["monthly"]
        days = [(m["month"], m["days"][0][0], m["days"][-1][0]) for m in mo]
        if months is not None:
            assert days == months
        # every day of the period exactly once, in order, and nothing outside it
        assert sum(len(m["days"]) for m in mo) == ndays
        assert out["period"]["period_start"].endswith(f"-{mo[0]['days'][0][0]:02d}")
        assert out["period"]["period_end"].endswith(f"-{mo[-1]['days'][-1][0]:02d}")
        # each month's first_weekday is the weekday of its first day shown
        first = datetime.strptime(out["period"]["period_start"], "%Y-%m-%d")
        assert mo[0]["first_weekday"] == first.weekday()
        # the 6-month report ending 2026-11-04 starts on 2026-05-09: 7 months, May and November partial
        if ndays == 180:
            assert len(mo) == 7 and mo[0]["month"] == "May 9 – May 31, 2026" and mo[1]["month"] == "June 2026"
            assert mo[-1]["month"] == "November 1 – November 4, 2026"

    def test_lab_a1c_compared_with_gmi(self, year_of_data):
        rows = [{"date": "2026-10-01", "a1c": 6.4}, {"date": "2020-01-01", "a1c": 7.0}, {"date": "bad", "a1c": 5}]
        lab = R.build_report(year_of_data, 300, LA, 14, False, rows, now=NOW)["lab"]
        by_date = {r["date"]: r for r in lab["results"]}
        assert by_date["2026-10-01"]["gmi_90d"] is not None and by_date["2026-10-01"]["cgm_active_90d"] >= 70
        assert by_date["2020-01-01"]["gmi_90d"] is None and by_date["bad"]["gmi_90d"] is None
        assert len(lab["results"]) == 3
        assert lab["average_difference"] == pytest.approx(round(6.4 - by_date["2026-10-01"]["gmi_90d"], 1))

    def test_fall_back_day_in_the_full_report(self):
        now_local = datetime(2026, 11, 3, 9, 0, tzinfo=LA)
        now = now_local.timestamp()
        rds = [Rd(t, 120.0) for t in range(int(now) - 20 * 86400, int(now), 60)]
        out = R.build_report(rds, 60, LA, 14, True, [], now=now)
        labels = out["standard"]["daily_labels"]
        assert len(labels) == 14 and len(set(labels)) == 14
        assert any("11/1" in x for x in labels)

    def test_daily_data_pct_never_above_100_on_a_25_hour_day(self):
        now_local = datetime(2026, 11, 3, 9, 0, tzinfo=LA)
        now = now_local.timestamp()
        rds = [Rd(t, 120.0) for t in range(int(now) - 20 * 86400, int(now), 60)]
        out = R.build_report(rds, 60, LA, 14, True, [], now=now)
        assert all(row[3] <= 100 for row in out["days"]["week"])
        assert all(d[3] <= 100 for m in out["monthly"] for d in m["days"])


class TestReportRounding:
    """Halves are rounded away from zero everywhere (Python's round() would round them to even)."""

    def test_mean_half_rounds_up(self):
        start = int(NOW) - 15 * 86400
        p = R.build_report([Rd(t, 120.5) for t in range(start, int(NOW), 300)], 300, LA, 14, False, [], now=NOW)[
            "standard"
        ]
        assert p["mean"] == 120.5  # unrounded in the report data
        assert fmt(p["mean"], "mg/dL") == "121"  # rounded once where it is shown, halves away from zero

    def test_gri_component_half_rounds_up(self):
        assert st.gri(0.25, 0, 0, 0)[1] == 0.3


# ------------------------------------------------------------------------------------------------ regional formats
class TestRegional:
    @pytest.mark.parametrize(
        "h, m, t12, t24",
        [
            (0, 0, "12:00 AM", "00:00"),
            (7, 46, "7:46 AM", "07:46"),
            (12, 0, "12:00 PM", "12:00"),
            (23, 5, "11:05 PM", "23:05"),
        ],
    )
    def test_clock(self, h, m, t12, t24):
        d = datetime(2026, 10, 22, h, m)
        assert Regional("12").clock(d) == t12 and Regional("24").clock(d) == t24

    @pytest.mark.parametrize(
        "h, l12, l24", [(0, "12am", "00:00"), (6, "6am", "06:00"), (12, "12pm", "12:00"), (18, "6pm", "18:00")]
    )
    def test_hour_labels(self, h, l12, l24):
        assert Regional("12").hour(h) == l12 and Regional("24").hour(h) == l24

    def test_dates(self):
        d = datetime(2026, 10, 22, 7, 46)
        assert Regional("12", "MDY").numeric(d) == "10/22" and Regional("12", "MDY").numeric(d, True) == "10/22/2026"
        assert Regional("24", "DMY").numeric(d) == "22/10" and Regional("24", "DMY").numeric(d, True) == "22/10/2026"
        assert Regional("24", "YMD").numeric(d) == "10-22" and Regional("24", "YMD").numeric(d, True) == "2026-10-22"
        assert (
            Regional("12", "MDY").day_month(d) == "Oct 22" and Regional("24", "DMY").day_month(d, True) == "22 October"
        )
        assert Regional("12", "MDY").stamp(d) == "Thu 10/22 7:46 AM"
        assert Regional("24", "DMY").stamp_year(d) == "Thu 22/10/2026 07:46"

    @pytest.mark.parametrize(
        "country, time_, date_",
        [
            ("US", "12", "MDY"),
            ("DE", "24", "DMY"),
            ("GB", "24", "DMY"),
            ("CA", "12", "YMD"),
            ("JP", "24", "YMD"),
            ("AU", "12", "DMY"),
            (None, "12", "MDY"),
            ("in", "12", "DMY"),
        ],
    )
    def test_automatic_from_country(self, country, time_, date_):
        assert rg.resolve_time("auto", country) == time_ and rg.resolve_date("auto", country) == date_

    def test_explicit_choice_wins(self):
        assert rg.resolve_time("24", "US") == "24" and rg.resolve_date("DMY", "US") == "DMY"
        assert rg.resolve_time("bogus", "DE") == "24"


# ------------------------------------------------------------------------------------------------ the PDF
@pytest.mark.slow
@pytest.mark.parametrize("unit, tf", [("mmol/L", "24"), ("mg/dL", "12")])
def test_pdf_labels_follow_unit_and_time_format(year_of_data, unit, tf):
    pypdf = pytest.importorskip("pypdf")
    data = R.build_report(year_of_data, 300, LA, 30, True, [], now=NOW, regional=Regional(tf, "DMY"))
    data.update(
        {"label": "30 Days", "sources": ["Test"], "created": "x", "unit": unit, "time_format": tf, "date_format": "DMY"}
    )
    pdf, pages = P.render(data, True, True)
    assert pdf[:5] == b"%PDF-" and pages >= 5
    text = "\n".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    if unit == "mmol/L":
        # one form of the night everywhere: 00:00–05:59 / 12:00 AM–5:59 AM
        assert "12pm" not in text and "Night (00:00–05:59)" in text and "Night (00:00–06:00)" not in text
        assert "above 10.0" in text and "below 3.9" in text
        assert "above 180" not in text and "below 70" not in text
    else:
        assert (
            "Night (12:00 AM–5:59 AM)" in text and "Day (6:00 AM–11:59 PM)" in text and "Night (12am-6am)" not in text
        )


@pytest.mark.slow
def test_pdf_grayscale_and_summary(year_of_data):
    data = R.build_report(year_of_data, 300, LA, 14, False, [], now=NOW)
    data.update(
        {
            "label": "14 Days",
            "sources": ["Test"],
            "created": "x",
            "unit": "mg/dL",
            "time_format": "12",
            "date_format": "MDY",
        }
    )
    pdf, pages = P.render(data, False, False)
    assert pdf[:5] == b"%PDF-" and 2 <= pages <= 7  # 7: About This Report states the whole reading-interval rule


# ------------------------------------------------------------------------------------------------ IPP printing
def ipp_attr(tag, name, val):
    return bytes([tag]) + struct.pack(">H", len(name)) + name + struct.pack(">H", len(val)) + val


OK_REPLY = (
    struct.pack(">BBHI", 2, 0, 0x0000, 1)
    + b"\x01"
    + b"\x02"
    + ipp_attr(0x21, b"job-id", struct.pack(">i", 42))
    + b"\x03"
)


class TestIpp:
    def test_normal_reply(self):
        assert P.parse_ipp_reply(OK_REPLY) == (0, 42)

    def test_every_truncation_parses_without_exception(self):
        for cut in range(len(OK_REPLY)):
            status, job = P.parse_ipp_reply(OK_REPLY[:cut])
            assert job in (None, 42)
            assert status is None if cut < 8 else status == 0

    def test_garbage_does_not_raise(self):
        for blob in (b"\xff" * 64, b"\x02\x00\x00\x00\x00\x00\x00\x01" + b"\x41\xff\xff", bytes(range(256))):
            P.parse_ipp_reply(blob)

    def test_error_status(self):
        assert P.parse_ipp_reply(struct.pack(">BBHI", 2, 0, 0x0400, 1) + b"\x03")[0] == 0x0400

    @pytest.mark.parametrize(
        "target, want",
        [
            ("192.168.1.5", ("ipp://192.168.1.5:631/ipp/print", "http://192.168.1.5:631/ipp/print")),
            ("printer.local:8631", ("ipp://printer.local:8631/ipp/print", "http://printer.local:8631/ipp/print")),
            ("fe80::1", ("ipp://[fe80::1]:631/ipp/print", "http://[fe80::1]:631/ipp/print")),
            ("[fe80::1]", ("ipp://[fe80::1]:631/ipp/print", "http://[fe80::1]:631/ipp/print")),
            ("[fe80::1]:632", ("ipp://[fe80::1]:632/ipp/print", "http://[fe80::1]:632/ipp/print")),
            ("ipps://[2001:db8::2]/ipp/x", ("ipps://[2001:db8::2]:631/ipp/x", "https://[2001:db8::2]:631/ipp/x")),
            (" ipp://printer/ipp/print ", ("ipp://printer:631/ipp/print", "http://printer:631/ipp/print")),
            ("http://printer:80/ipp", ("ipp://printer:80/ipp", "http://printer:80/ipp")),
        ],
    )
    def test_printer_url(self, target, want):
        assert P.printer_url(target) == want

    @staticmethod
    def serve(handler):
        srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    def test_http_error_is_not_unreachable(self):
        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(500)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = self.serve(H)
        try:
            ok, msg = P.ipp_print(f"127.0.0.1:{srv.server_port}", b"%PDF", "t", timeout=5)
        finally:
            srv.shutdown()
        assert not ok and "HTTP status 500" in msg and "not reachable" not in msg

    @pytest.mark.parametrize(
        "body, ok, words",
        [
            (b"\x02\x00", False, "incomplete"),
            (OK_REPLY, True, "job 42"),
            (struct.pack(">BBHI", 2, 0, 0x0400, 1) + b"\x03", False, "0x0400"),
        ],
    )
    def test_replies(self, body, ok, words):
        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(200)
                self.send_header("Content-Type", "application/ipp")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = self.serve(H)
        try:
            got_ok, msg = P.ipp_print(f"127.0.0.1:{srv.server_port}", b"%PDF", "t", timeout=5)
        finally:
            srv.shutdown()
        assert got_ok is ok and words in msg

    def test_a_reply_that_is_not_ipp_and_a_markdown_reason(self):
        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.path.endswith("html"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<html>hello</html>")
                else:
                    self.send_response(500, "[x](https://example.invalid/a) ![i](u)")
                    self.end_headers()

            def log_message(self, *a):
                pass

        srv = self.serve(H)
        try:
            html = P.ipp_print(f"ipp://127.0.0.1:{srv.server_port}/html", b"%PDF", "t", timeout=5)
            md = P.ipp_print(f"127.0.0.1:{srv.server_port}", b"%PDF", "t", timeout=5)
        finally:
            srv.shutdown()
        assert html == (False, "the reply was not an Internet Printing Protocol reply")
        assert (
            not md[0]
            and "HTTP status 500" in md[1]
            and md[1].endswith("(HTTP status 500 x https example invalid a i u)")  # no period kept
        )

    def test_no_reply_within_the_time_out_and_no_secure_connection(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)  # accepts the connection, never answers
        port = listener.getsockname()[1]
        try:
            assert P.ipp_print(f"127.0.0.1:{port}", b"%PDF", "t", timeout=1) == (
                False,
                "no reply from the printer within 1 seconds",
            )
        finally:
            listener.close()

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = self.serve(H)  # plain HTTP: an ipps:// address (any case) cannot connect securely
        try:
            got = P.ipp_print(f"IPPS://127.0.0.1:{srv.server_port}/ipp/print", b"%PDF", "t", timeout=5)
        finally:
            srv.shutdown()
        assert got == (False, "the printer did not accept a secure (ipps) connection")
        assert P.printer_url("IPPS://printer/ipp/print")[1] == "https://printer:631/ipp/print"

    def test_untrusted_certificate(self, monkeypatch):
        def fail(r, timeout):
            raise urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed"))

        monkeypatch.setattr(urllib.request, "urlopen", fail)
        assert P.ipp_print("ipps://printer", b"%PDF", "t") == (False, "the printer's certificate is not trusted")

    def test_unreachable(self):
        ok, msg = P.ipp_print("127.0.0.1:9", b"%PDF", "t", timeout=2)
        # refused at once, or (Windows retries a refused connection) no reply within the time-out
        assert not ok and ("not reachable" in msg or msg == "no reply from the printer within 2 seconds")
