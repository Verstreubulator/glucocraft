# License: AGPL-3.0-only (LICENSE).
"""The independent calculator (tools/synth/expected.py) against hand-computed tiny cases.

Each case is small enough to compute on paper; the numbers in the comments are that computation. These tests keep
the reference itself honest, so that a disagreement in test_synth_scenarios.py points at GlucoCraft or at a
documented rule, not at the calculator.
"""

from __future__ import annotations

import importlib
import sys
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
E = importlib.import_module("tools.synth.expected")
common = importlib.import_module("tools.synth.common")

LA = ZoneInfo("America/Los_Angeles")
T0 = 1_780_000_000 - 1_780_000_000 % 3600  # a whole hour


def store(values, step=300, start=T0) -> E.Store:
    return E.Store.build([E.Reading(start + i * step, Fraction(v)) for i, v in enumerate(values)])


def minutes(values_per_minute: list[tuple[float, int]], start=T0) -> E.Store:
    """1-minute readings: [(value, how many minutes)]."""
    out, t = [], start
    for v, n in values_per_minute:
        for _ in range(n):
            out.append(E.Reading(t, Fraction(v)))
            t += 60
    return E.Store.build(out)


# ------------------------------------------------------------------------------------------- source states
@pytest.mark.parametrize(
    "text, unit, kind, mg",
    [
        ("120", "mg/dL", "reading", 120),
        ("3.9", "mmol/L", "reading", 70),
        ("10.0", "mmol/L", "reading", 180),
        ("7.8", "mmol/L", "reading", 140),
        ("3.0", "mmol/L", "reading", 54),
        ("13.9", "mmol/L", "reading", 250),
        ("7.9", "mmol/L", "reading", Fraction("142.3")),  # 7.9 x 18.016 = 142.33
        ("3.8", "mmol/L", "reading", Fraction("68.5")),  # 68.46
        ("LO", "mg/dL", "low", 40),
        ("39", "mg/dL", "low", 40),
        ("40", "mg/dL", "reading", 40),
        ("2.2", "mmol/L", "low", 40),
        ("2.3", "mmol/L", "reading", Fraction("41.4")),
        ("500", "mg/dL", "reading", 500),
        ("501", "mg/dL", "high", 500),
        ("HI", "mg/dL", "high", 500),
        ("27.8", "mmol/L", "high", 500),  # 500.8 > 500: the Documentation's rule
        ("unavailable", "mg/dL", "none", None),
        ("unknown", "mmol/L", "none", None),
        ("38", "mg/dL", "none", None),
        ("5", "mg/dL", "none", None),
        ("65535", "mg/dL", "none", None),
    ],
)
def test_classify(text, unit, kind, mg):
    got = E.classify(text, unit)
    assert got == (kind, None if mg is None else Fraction(mg))


def test_classify_dexcom_and_medtronic_limits():
    assert E.classify("401", "mg/dL", upper=400, dexcom=True) == ("high", 400)
    assert E.classify("401", "mg/dL") == ("reading", 401)  # 500 default: a measured 401
    assert E.classify("400", "mg/dL", upper=400) == ("reading", 400)
    assert E.classify("Below 50 mg/dL", "mg/dL", lower=50) == ("low", 50)
    assert E.classify("49", "mg/dL", lower=50) == ("low", 50)
    assert E.classify("50", "mg/dL", lower=50) == ("reading", 50)  # Automatic: only below the limit is LO
    assert E.classify("50", "mg/dL", lower=50, lower_set=True) == ("low", 50)  # set by hand: at the limit as well
    assert E.classify("2.8", "mmol/L", lower=50, lower_set=True) == ("low", 50)
    assert E.classify("Above 22.2 mmol/L", "mmol/L", upper=400) == ("high", 400)


def test_one_reading_per_measurement_time_the_last_recorded():
    st = [common.SourceState(100, "120", 50), common.SourceState(130, "140", 50), common.SourceState(120, "90", 10)]
    assert E.readings_from_states(st, "mg/dL") == [E.Reading(10, Fraction(90)), E.Reading(50, Fraction(140))]


# ------------------------------------------------------------------------------------------- the gap rule
def test_interval_and_gap_rule():
    # 12 readings 5 minutes apart: every interval at a reading is 300 s, the threshold 900 s
    s = store([100] * 12)
    assert s.detected == 300 and set(s.iv) == {300} and E.threshold(300) == 900
    # 1-minute readings with one 15-minute and one 16-minute interval
    t = [T0 + 60 * i for i in range(20)] + [T0 + 60 * 19 + 900] + [T0 + 60 * 19 + 900 + 60 * i for i in range(1, 20)]
    t += [t[-1] + 960] + [t[-1] + 960 + 60 * i for i in range(1, 12)]
    s = E.Store.build([E.Reading(x, Fraction(100)) for x in t])
    d = {t[k + 1] - t[k]: s.spans[k] for k in range(len(t) - 1)}
    assert d[900] == 900  # 15 minutes: no gap, the reading represents them
    assert d[960] == 60  # 16 minutes: a gap, the reading represents its own interval


def test_detected_interval():
    assert E.detected_interval([0, 300, 600]) == 300  # fewer than 10 readings: default
    assert E.detected_interval([60 * i for i in range(20)]) == 60
    assert E.detected_interval([900 * i for i in range(20)]) == 900  # only 15-minute intervals
    assert E.detected_interval([900 * i for i in range(20)] + [17100 + 60 * i for i in range(1, 3)]) == 60


# ------------------------------------------------------------------------------------------- the metrics
def test_three_readings_by_hand():
    # 60, 120, 200 mg/dL 5 minutes apart: each represents 300 s (fewer than 3 intervals: the detected 300 s)
    s = store([60, 120, 200])
    m = E.metrics(s.segments(T0, T0 + 900), 900)
    assert m["mean"] == Fraction(380, 3)  # 126.67
    assert m["below_70"] == m["tir"] == m["above_180"] == Fraction(100, 3)
    assert m["very_low"] == 0 and m["low"] == Fraction(100, 3) and m["high"] == Fraction(100, 3)
    # GRI = 2.4 x 33.33 + 0.8 x 33.33 = 106.7, capped at 100: zone E
    assert m["gri"] == 100 and E.gri_zone(common.round_half_up(m["gri"], 1)) == "E"
    assert common.round_half_up(m["gmi"], 1) == 6.3  # 3.31 + 0.02392 x 126.67 = 6.34
    # SD: sqrt(((60-126.67)^2 + (120-126.67)^2 + (200-126.67)^2) / 3) = 57.3
    assert common.round_half_up(m["sd"], 1) == 57.3
    assert common.round_half_up(m["cv"], 1) == 45.3  # 57.35 / 126.67 = 45.28
    assert m["gmi_mmol_mol"] == 46  # 12.71 + 4.70587 x 7.031 = 45.8


def test_time_weighting():
    # 100 for 10 minutes (readings every minute) then one reading of 200 followed 15 minutes later by 100:
    # the 200 represents 15 minutes (no gap), so 15 of 26 minutes are above 180
    s = minutes([(100, 10)])
    rs = s.readings + [E.Reading(T0 + 600, Fraction(200)), E.Reading(T0 + 1500, Fraction(100))]
    s = E.Store.build(rs)
    m = E.metrics(s.segments(T0, T0 + 26 * 60), 26 * 60)
    assert m["above_180"] == Fraction(15 * 100, 26)


def test_sensor_active_and_the_70_percent_rule():
    period = 14 * 86400
    assert not E.gmi_shown(period, Fraction(100 * 14111, 20160))  # 69.995%: shown as 70.0, not met
    assert E.gmi_shown(period, Fraction(100 * 14113, 20160))
    assert not E.gmi_shown(13 * 86400, Fraction(100))  # less than 14 days


def test_reading_window_and_clipped_window():
    # readings at 0 and 300 s (interval 300): the statistics window [0, 300] counts both, the last for its own 300 s
    s = store([100, 200])
    stats = E.metrics(s.segments(T0, T0 + 301, clipped=False), 600)
    clipped = E.metrics(s.segments(T0 + 150, T0 + 450), 300)
    assert stats["covered_s"] == 600 and clipped["covered_s"] == 300
    assert clipped["mean"] == 150  # 150 s of each


# ------------------------------------------------------------------------------------------- events
def test_hypoglycemia_event_15_minutes_starts_14_does_not():
    assert len(E.hypo_events(minutes([(100, 5), (60, 15), (100, 30)]).segments(T0, T0 + 50 * 60), 70)) == 1
    assert E.hypo_events(minutes([(100, 5), (60, 14), (100, 30)]).segments(T0, T0 + 49 * 60), 70) == []


def test_hypoglycemia_event_end_and_level_2():
    # 50 for 20 min, 65 for 30 (level 2 continues), 70 for 14, 60 for 5, 70 for 15: both events last 69 minutes
    s = minutes([(50, 20), (65, 30), (70, 14), (60, 5), (70, 15), (100, 20)])
    segs = s.segments(T0, T0 + 104 * 60)
    assert E.hypo_events(segs, 70) == [(T0, 69 * 60, 50)]
    assert E.hypo_events(segs, 54) == [(T0, 69 * 60, 50)]


def test_a_gap_ends_an_event():
    rs = [E.Reading(T0 + 60 * i, Fraction(60)) for i in range(30)] + [
        E.Reading(T0 + 60 * 29 + 1860 + 60 * i, Fraction(60)) for i in range(10)
    ]
    s = E.Store.build(rs)
    assert E.hypo_events(s.segments(T0, T0 + 7200), 70) == [(T0, 1800, 60)]


def test_extended_high_events():
    assert E.extended_high_events(minutes([(251, 120), (150, 30)]), T0 + 10**6) == [(T0, 120 * 60)]
    assert E.extended_high_events(minutes([(251, 119), (150, 30)]), T0 + 10**6) == []
    # 130 above 250, 14 at 170, 10 at 200, 15 at 170: ends at the first reading of the 15-minute run
    s = minutes([(260, 130), (170, 14), (200, 10), (170, 15), (150, 10)])
    assert E.extended_high_events(s, T0 + 10**6) == [(T0, 154 * 60)]


# ------------------------------------------------------------------------------------------- days, retention, sources
def test_local_days_and_retention():
    now = int(datetime(2026, 2, 16, tzinfo=LA).timestamp())
    assert E.local_midnight(now - 1, LA) == int(datetime(2026, 2, 15, tzinfo=LA).timestamp())
    s = E.Store.build([E.Reading(now - 60, Fraction(100))])
    # 200 local days before February 15 is July 30 (local midnight, daylight time)
    assert E.retention_cutoff(s, LA, now) == int(datetime(2025, 7, 30, tzinfo=LA).timestamp())
    spring = int(datetime(2026, 3, 8, tzinfo=LA).timestamp())
    assert E.local_midnight(spring + 86400, LA) - spring == 23 * 3600


def test_merge_sources():
    def rd(t, v):
        return E.Reading(t, Fraction(v))

    primary = [(T0 + 60 * i + 30, T0 + 60 * i, rd(T0 + 60 * i, 100)) for i in range(10)]
    # the secondary every 5 minutes, 40 s after the primary's minute: the same data, never stored
    secondary = [(T0 + 300 * i + 70, T0 + 300 * i + 40, rd(T0 + 300 * i + 40, 101)) for i in range(2)]
    assert [r.ts for r in E.merge_sources(primary, secondary)] == [T0 + 60 * i for i in range(10)]
    # the primary stops after 10 minutes: the secondary's readings fill the outage
    later = [(T0 + 900 + 300 * i + 70, T0 + 900 + 300 * i + 40, rd(T0 + 900 + 300 * i + 40, 101)) for i in range(3)]
    merged = E.merge_sources(primary, later)
    assert [r.ts for r in merged][-3:] == [T0 + 940, T0 + 1240, T0 + 1540]
