# License: AGPL-3.0-only (LICENSE).
"""The enough-data rule: a period's statistics are shown only with sensor active at least 70% of the period.

Rules under test (each expected number worked out by hand in the test):
- statistics.enough_data compares the unrounded sensor active with 70%: 70.0% exactly meets it, 69.995% (shown as 70.0%
  with one decimal) does not; the GRI follows the rule as the GMI does.
- With too few data every statistic sensor of the period is unknown, while sensor active and the reading count stay;
  the period summary sensors and the statistics API carry enough_data false, sensor_active, period_seconds and
  covered_seconds, and every value null; the readings at the sensor's limit are stated either way.
- The wall-panel file writes such a period's STATS block and its GCM1 extremes as zeros.
- The summaries show a period only with enough data; otherwise one line says how much the sensor has covered and how
  much more is needed.
- The weekly windows follow the same rule.
"""

from __future__ import annotations

import struct
import time

from glucocraft import change as ch
from glucocraft import http_api
from glucocraft import notification as nt
from glucocraft import panel_feed as pf
from glucocraft import sensor as se
from glucocraft import statistics as st
from glucocraft.storage import GlucoseReading

from conftest import add_reading, stats_record
from test_data_path import source_coordinator

SIX_HOURS = 6 * 3600


def _coordinator_with(tmp_path, count: int, step: int = 300, value: float = 120.0, limit=None):
    """A coordinator whose store holds `count` readings `step` seconds apart, the newest one `step` seconds ago, with
    the statistics computed at `now`."""
    c = source_coordinator(tmp_path)
    now = int(time.time())
    first = now - step * count
    for k in range(count):
        add_reading(c.storage, first + k * step, value, limit)
    c._current_from_store(now)
    c._stats_pass(now)
    return c, now


# ------------------------------------------------------------------------------------------------ the rule itself
def test_seventy_percent_exactly_meets_the_rule_and_just_below_does_not():
    period = 14 * 86400  # 1,209,600 s
    exact = stats_record(period_seconds=period, data_availability=70.0, covered_seconds=0.7 * period)
    below = stats_record(period_seconds=period, data_availability=69.995, covered_seconds=0.69995 * period)
    assert exact.sensor_active_exact == 70.0 and st.enough_data(exact)
    # 69.995% is shown as 70.0% with one decimal, but the unrounded value is compared
    assert round(below.sensor_active_exact, 3) == 69.995 and not st.enough_data(below)
    assert not st.enough_data(None)


def test_the_gri_needs_enough_data_as_the_gmi_does():
    period = 14 * 86400
    ok = stats_record(period_seconds=period, data_availability=70.0, covered_seconds=0.7 * period)
    low = stats_record(period_seconds=period, data_availability=20.0, covered_seconds=0.2 * period)
    assert st.gri_shown(ok, period) == ok.gri and st.gmi_shown(ok, period) == ok.gmi
    assert st.gri_shown(low, period) is None and st.gmi_shown(low, period) is None


# ------------------------------------------------------------------------------------------------ the 6-hour period
def test_fifty_readings_of_five_minutes_are_not_enough_for_six_hours(tmp_path):
    # 50 readings x 300 s = 15,000 s of 21,600 s = 69.44%: not enough; the statistic sensors are unknown
    c, _now = _coordinator_with(tmp_path, 50)
    s = c.period_stats["6h"]
    assert s.covered_seconds == 15000 and round(s.sensor_active_exact, 2) == 69.44
    for cls in (se.GlucoCraftPeriodMean, se.GlucoCraftPeriodCV, se.GlucoCraftPeriodTIR, se.GlucoCraftPeriodMin):
        assert _sensor(cls, c, "6h").native_value is None
    # sensor active and the reading count are not statistics: they stay
    assert _sensor(se.GlucoCraftPeriodDataAvailability, c, "6h").native_value == 69.4
    assert _sensor(se.GlucoCraftPeriodReadingCount, c, "6h").native_value == 50
    a = _sensor(se.GlucoCraftPeriodConsensus, c, "6h").extra_state_attributes
    assert a["has_data"] is True and a["enough_data"] is False
    assert a["period_seconds"] == SIX_HOURS and a["covered_seconds"] == 15000
    assert a["mean"] is None and a["time_70_180"] is None and a["min_value"] is None and a["gri"] is None
    assert a["readings"] == 50 and a["limit_readings"] == {"low": 0, "high": 0}
    assert _sensor(se.GlucoCraftPeriodConsensus, c, "6h").native_value is None


def test_fifty_one_readings_are_enough(tmp_path):
    # 51 x 300 s = 15,300 s of 21,600 s = 70.83%: enough; the mean of identical readings is their value
    c, _now = _coordinator_with(tmp_path, 51)
    assert c.period_stats["6h"].covered_seconds == 15300
    assert _sensor(se.GlucoCraftPeriodMean, c, "6h").native_value == 120
    a = _sensor(se.GlucoCraftPeriodConsensus, c, "6h").extra_state_attributes
    assert a["enough_data"] is True and a["mean"] == 120.0 and a["time_70_180"] == 100.0


def test_the_statistics_api_carries_the_rule(tmp_path):
    c, _now = _coordinator_with(tmp_path, 50)
    out = http_api._period_json(c.period_stats["6h"], SIX_HOURS, "mg/dL")
    assert out["enough_data"] is False and out["period_seconds"] == SIX_HOURS and out["covered_seconds"] == 15000
    assert round(out["sensor_active"], 4) == round(15000 / 21600 * 100, 4)  # 69.4444...: unrounded
    for key in ("mean", "std_dev", "cv", "gmi", "min", "max", "time_in_range", "time_below_low", "time_above_high"):
        assert out[key] is None, key
    assert out["reading_count"] == 50 and out["limit_seconds"] == {"low": 0, "high": 0}
    c2, _ = _coordinator_with(tmp_path / "b", 51)
    out2 = http_api._period_json(c2.period_stats["6h"], SIX_HOURS, "mg/dL")
    assert out2["enough_data"] is True and out2["mean"] == 120.0 and out2["time_in_range"] == 100.0


def test_the_panel_file_writes_a_period_without_enough_data_as_zeros(tmp_path):
    c, _now = _coordinator_with(tmp_path, 50)
    data = pf.build_glu2(c, delta=False)
    stats_offset = struct.unpack_from("<I", data, 8)[0]
    six_h = struct.unpack_from("<10fHH", data, stats_offset)  # the first period of STATS
    assert six_h == (0.0,) * 10 + (0, 0)
    # GCM1 (60 bytes before the 60-byte GCL2 trailer): the 6-hour minimum and maximum are zeros as well
    gcm1 = data[-120:-60]
    assert gcm1[-4:] == b"GCM1" and struct.unpack_from("<2f", gcm1, 0) == (0.0, 0.0)
    c2, _ = _coordinator_with(tmp_path / "b", 51)
    six_h2 = struct.unpack_from("<10fHH", pf.build_glu2(c2, delta=False), stats_offset)
    assert six_h2[0] == 120.0 and six_h2[10:] == (120, 120)


# ------------------------------------------------------------------------------------------------ the summaries
def test_the_not_enough_line_of_thirty_days_with_23_percent(tmp_path):
    # the decision's own example: 30 days with 23% covered. 23% of 30 days = 6.9 days; 70% = 21 days; 21 - 6.9 = 14.1
    # days still needed, rounded to about 14
    c = source_coordinator(tmp_path)
    period = 30 * 86400
    c.period_stats = {"30d": stats_record(period_seconds=period, data_availability=23.0, covered_seconds=0.23 * period)}
    assert nt.not_enough_line(c, "30d") == (
        "Last 30 days: not enough data yet. GlucoCraft shows a period's statistics once the sensor has covered at "
        "least 70% of it. So far it has covered 23%. With no gaps, about 14 more days are needed."
    )


def test_the_not_enough_line_counts_hours_for_short_periods(tmp_path):
    # 24 hours with 50% covered: 16.8 h needed, 12 h covered, 4.8 h more: about 5 hours. One hour: the singular
    c = source_coordinator(tmp_path)
    day = 86400
    c.period_stats = {"24h": stats_record(period_seconds=day, data_availability=50.0, covered_seconds=0.5 * day)}
    assert nt.not_enough_line(c, "24h").endswith(
        "So far it has covered 50%. With no gaps, about 5 more hours are needed."
    )
    c.period_stats = {"24h": stats_record(period_seconds=day, data_availability=68.0, covered_seconds=0.68 * day)}
    # 16.8 - 16.32 = 0.48 h: at least 1, in the singular; 68% is shown as 68%
    assert nt.not_enough_line(c, "24h").endswith(
        "So far it has covered 68%. With no gaps, about 1 more hour is needed."
    )
    # 69.6% would show as 70% in whole percent: it is shown with two decimals, on its side of the rule
    c.period_stats = {"24h": stats_record(period_seconds=day, data_availability=69.6, covered_seconds=0.696 * day)}
    assert "So far it has covered 69.60%." in nt.not_enough_line(c, "24h")


def test_a_daily_summary_without_enough_data_shows_the_line_only(tmp_path):
    c, _now = _coordinator_with(tmp_path, 100)  # 100 x 300 s = 30,000 s of 86,400 s = 34.72%
    text = nt._build_daily_summary(c)
    assert "Last 24 hours: not enough data yet." in text and "So far it has covered 35%." in text
    # 70% of 86,400 s = 60,480 s; 60,480 - 30,000 = 30,480 s = 8.47 h: about 8 hours
    assert "With no gaps, about 8 more hours are needed." in text
    assert "Consensus targets" not in text and "Mean:" not in text


def test_the_limit_line_states_the_number_and_the_time(tmp_path):
    # 3 LO readings of 5 minutes and 1 HI reading among 300 measured ones: "below the sensor's lower limit: 3 readings
    # (15 minutes)" and the HI one in the singular (5 minutes); the time in words, as the summaries write every
    # duration; the newest reading weighs its own interval (300 s)
    c = source_coordinator(tmp_path)
    now = int(time.time())
    first = now - 300 * 304
    rows = (
        [(None, 120.0)] * 150 + [("low", 40.0)] * 3 + [(None, 120.0)] * 100 + [("high", 500.0)] + [(None, 120.0)] * 50
    )
    for k, (limit, value) in enumerate(rows):
        add_reading(c.storage, first + 300 * k, value, limit)
    c._current_from_store(now)
    c._stats_pass(now)
    s = c.period_stats["24h"]
    assert s.limit_readings == {"low": 3, "high": 1} and s.limit_seconds == {"low": 900, "high": 300}
    line = " ".join(nt._overview_lines(c, s, set()))
    assert "below the sensor's lower limit: 3 readings (15 minutes)" in line
    assert "above the sensor's upper limit: 1 reading (5 minutes)" in line


# ------------------------------------------------------------------------------------------------ the weekly windows
def test_change_over_time_windows_follow_the_rule():
    period = 14 * 86400
    ok = stats_record(period_seconds=period, data_availability=70.0, covered_seconds=0.7 * period)
    low = stats_record(period_seconds=period, data_availability=69.99, covered_seconds=0.6999 * period)
    assert ch._values(ok, 14, True)["tir"] == ok.pct_time_in_target_range
    assert ch._values(low, 14, True) is None  # shown as a gap


def test_weekly_trends_leave_a_week_below_seventy_percent_empty():
    # one week of 2016 five-minute readings is 100%; 1411 of them are 69.99% and 1412 are 70.04% (see the week rule)
    now = 1_800_000_000
    week = 7 * 86400
    full = [GlucoseReading(t, 120) for t in range(now - week, now, 300)]
    assert len(full) == 2016
    weeks = st.compute_weekly_trends(full[-1411:], num_weeks=1, detected_interval=300, now=now)
    assert weeks == [None]
    weeks = st.compute_weekly_trends(full[-1412:], num_weeks=1, detected_interval=300, now=now)
    assert weeks[0] is not None and weeks[0].covered_seconds == 1412 * 300


def _sensor(cls, c, period: str):
    """A period sensor of the coordinator `c`, without Home Assistant's entity set-up."""
    e = cls.__new__(cls)
    e.coordinator = c
    e._period = period
    return e
