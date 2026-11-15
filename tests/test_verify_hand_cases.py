# License: AGPL-3.0-only (LICENSE).
"""Hand-worked cases: each value computed on paper from the published definitions, then checked against BOTH the
independent calculator (tools/synth/expected.py) and GlucoCraft's real code.

A disagreement with expected.py alone would point at the calculator; a disagreement with both at the definition. The
arithmetic of each case is in its comment. Sources: Battelino et al. (2019) bands and targets; Battelino et al. (2023)
tight range 70-140 and the extended high-glucose endpoint (p. 48); Bergenstal et al. (2018) GMI and its Table 1;
Klonoff et al. (2023) GRI; Danne et al. (2017) Section 4, hypoglycemia events; docs/SYSTEM_DESIGN.md (time weighting
4.2, unit rule 4.3, the gap rule 4.5, events 6.4 (with the research appendix's extended high-glucose method), GRI
zone 6.5).
"""

from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from glucocraft import gaps
from glucocraft import limits as L
from glucocraft import report as R
from glucocraft import statistics as st
from glucocraft import units as U
from glucocraft.const import AGP_RANGES
from glucocraft.storage import GlucoCraftStorage, GlucoseReading

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
E = importlib.import_module("tools.synth.expected")
common = importlib.import_module("tools.synth.common")
rhu = common.round_half_up

LA = ZoneInfo("America/Los_Angeles")
T0 = 1_780_000_000 - 1_780_000_000 % 3600  # a whole hour


# ------------------------------------------------------------------------------------------------------ helpers
def seq(parts, step=300, start=T0):
    """[(mg/dL, count)] -> rows (time, mg/dL, None), `step` seconds apart."""
    out, t = [], start
    for v, n in parts:
        for _ in range(n):
            out.append((t, v, None))
            t += step
    return out


def gc(rows):
    return [GlucoseReading(t, float(v), lim) for t, v, lim in rows]


def ex(rows):
    return E.Store.build([E.Reading(t, Fraction(str(v)), lim) for t, v, lim in rows])


def both_stats(rows, period, interval=300):
    """GlucoCraft's statistics sensor values and the calculator's for one window ending at the last reading."""
    s = st.compute_period_stats(gc(rows), "t", period, interval, **AGP_RANGES)
    e = E.statistics_sensor(ex(rows), rows[-1][0], period)
    return s, e


def r1(x):
    return None if x is None else rhu(x, 1)


# ------------------------------------------------------------------------------- H1-H3 the bands, both units
def test_h1_bands_mean_sd_cv_gmi_gri_at_70_and_180():
    # 69, 70, 180, 181 mg/dL, 5 min apart, each weighs 300 s (1200 s): 70 and 180 are in range (Battelino 2019:
    # 70-180 inclusive), 69 is 54-69, 181 is 181-250. TIR 2/4 = 50.0; low 25.0; high 25.0; TITR (70-140): 25.0.
    # mean 500/4 = 125.0; SD sqrt((56^2+55^2+55^2+56^2)/4) = sqrt(3080.5) = 55.50 -> 55.5; CV 55.50/125 = 44.40%
    # GMI 3.31 + 0.02392 x 125 = 6.30 -> 6.3; mmol/mol 12.71 + 4.70587 x 125/18.016 = 45.36 -> 45
    # GRI 2.4 x 25 + 0.8 x 25 = 80.0 -> zone D (A <= 20 < B <= 40 < C <= 60 < D <= 80 < E); hypo 0.8 x 25 = 20.0;
    # hyper 0.5 x 25 = 12.5
    rows = seq([(69, 1), (70, 1), (180, 1), (181, 1)])
    s, e = both_stats(rows, 1200)
    want = {
        "tir": 50.0,
        "low": 25.0,
        "high": 25.0,
        "very_low": 0.0,
        "very_high": 0.0,
        "titr": 25.0,
        "below_70": 25.0,
        "above_180": 25.0,
        "mean": 125.0,
        "sd": 55.5,
        "cv": 44.4,
        "gmi": 6.3,
        "gri": 80.0,
        "gri_hypo": 20.0,
        "gri_hyper": 12.5,
        "active": 100.0,
    }
    got_gc = {
        "tir": s.pct_time_in_target_range,
        "low": s.time_low,
        "high": s.time_high,
        "very_low": s.time_critical_low,
        "very_high": s.time_critical_high,
        "titr": s.pct_time_in_ideal_range,
        # the mean, the SD, the CV and the percentages are kept unrounded (rounded once where shown): compared at one
        # decimal
        "below_70": s.time_below_low,
        "above_180": s.time_above_high,
        "mean": r1(s.mean),
        "sd": r1(s.std_dev),
        "cv": r1(s.cv),
        "gmi": s.gmi,
        "gri": s.gri,
        "gri_hypo": s.gri_hypo,
        "gri_hyper": s.gri_hyper,
        "active": s.data_availability,
    }
    assert got_gc == want
    assert {k: r1(e[k if k != "active" else "active"]) for k in want} == want
    assert s.gri_zone == E.gri_zone(r1(e["gri"])) == "D"
    assert s.gmi_mmol_mol == e["gmi_mmol_mol"] == 45


def test_h2_bands_at_54_and_250_and_the_gri_cap():
    # 53, 54, 250, 251: 53 < 54 very low; 54 in 54-69; 250 in 181-250; 251 > 250 very high; each 25%
    # GRI = 3 x 25 + 2.4 x 25 + 1.6 x 25 + 0.8 x 25 = 195 -> capped at 100, zone E; hypo 25 + 0.8 x 25 = 45.0;
    # hyper 25 + 0.5 x 25 = 37.5; mean 608/4 = 152.0; GMI 3.31 + 3.63584 = 6.9; mmol/mol 12.71 + 39.70 = 52.41 -> 52
    s, e = both_stats(seq([(53, 1), (54, 1), (250, 1), (251, 1)]), 1200)
    assert (s.time_critical_low, s.time_low, s.time_high, s.time_critical_high, s.pct_time_in_target_range) == (
        25.0,
        25.0,
        25.0,
        25.0,
        0.0,
    )
    assert [r1(e[k]) for k in ("very_low", "low", "high", "very_high", "tir")] == [25.0, 25.0, 25.0, 25.0, 0.0]
    assert s.gri == r1(e["gri"]) == 100.0 and s.gri_zone == "E"
    assert (s.gri_hypo, s.gri_hyper) == (r1(e["gri_hypo"]), r1(e["gri_hyper"])) == (45.0, 37.5)
    assert s.mean == r1(e["mean"]) == 152.0 and s.gmi == r1(e["gmi"]) == 6.9
    assert s.gmi_mmol_mol == e["gmi_mmol_mol"] == 52


def test_h3_mmol_source_on_the_published_pairs():
    # mmol/L 3.8 / 3.9 / 7.8 / 7.9 / 10.0 / 10.1: 3.9, 7.8, 10.0 are the published pairs of 70 / 140 / 180 (Battelino
    # 2019 / 2023) and stay on the in-range side; the others x 18.016 to one decimal: 3.8 -> 68.46 -> 68.5,
    # 7.9 -> 142.33 -> 142.3, 10.1 -> 181.96 -> 182.0. TIR 4/6 = 66.7; TITR 2/6 = 33.3; low 16.7; high 16.7
    # mean 782.8/6 = 130.47 -> 130.5 mg/dL, shown 130.4667/18.016 = 7.24 -> 7.2 mmol/L
    texts = ["3.8", "3.9", "7.8", "7.9", "10.0", "10.1"]
    mg = [68.5, 70.0, 140.0, 142.3, 180.0, 182.0]
    assert [L.classify(t, U.UNIT_MMOLL).value for t in texts] == mg
    assert [float(E.classify(t, "mmol/L")[1]) for t in texts] == mg
    rows = [(T0 + 300 * i, v, None) for i, v in enumerate(mg)]
    s, e = both_stats(rows, 1800)
    shown = tuple(r1(v) for v in (s.pct_time_in_target_range, s.pct_time_in_ideal_range, s.time_low, s.time_high))
    assert shown == (66.7, 33.3, 16.7, 16.7)
    assert [r1(e[k]) for k in ("tir", "titr", "low", "high")] == [66.7, 33.3, 16.7, 16.7]
    assert r1(s.mean) == 130.5 and U.fmt(s.mean, U.UNIT_MMOLL) == U.fmt(float(e["mean"]), U.UNIT_MMOLL) == "7.2"


# ------------------------------------------------------------------------------- H4-H6 time weighting, the gap rule
def test_h4_time_weighting_15_minutes_is_no_gap():
    # 100 @0, 200 @300, 100 @1200, 100 @1500: the 900 s after the 200 is no gap (max(15 min, 3 x 300)), so the 200
    # weighs 900 s: above 180 = 900 / 1800 = 50.0; mean (100x300 + 200x900 + 100x300 + 100x300) / 1800 = 150.0
    rows = [(T0, 100, None), (T0 + 300, 200, None), (T0 + 1200, 100, None), (T0 + 1500, 100, None)]
    s, e = both_stats(rows, 1800)
    assert s.time_above_high == r1(e["above_180"]) == 50.0
    assert s.mean == r1(e["mean"]) == 150.0 and s.covered_seconds == e["covered_s"] == 1800


def test_h5_time_weighting_16_minutes_is_a_gap():
    # the same with 960 s after the 200: a gap, so the 200 weighs its own interval (300 s): above 180 = 300 / 1200
    # = 25.0; mean 1500 x 100 / 1200... = (100+200+100+100) x 300 / 1200 = 125.0; covered 1200 of a 1860 s period
    # (64.516 -> 64.5)
    rows = [(T0, 100, None), (T0 + 300, 200, None), (T0 + 1260, 100, None), (T0 + 1560, 100, None)]
    s, e = both_stats(rows, 1860)
    assert s.time_above_high == r1(e["above_180"]) == 25.0
    assert s.mean == r1(e["mean"]) == 125.0
    assert r1(s.data_availability) == r1(e["active"]) == 64.5


@pytest.mark.parametrize("jump, weight", [(2700, 2700), (2760, 900)])
def test_h6_fifteen_minute_source_threshold_is_45_minutes(jump, weight):
    # 12 readings 900 s apart, then `jump`: the reading interval there is 900 s (the 10 intervals before), the
    # threshold max(900, 3 x 900) = 2700: 2700 s is no gap (the reading weighs 2700), 2760 s is a gap (900)
    ts = [T0 + 900 * i for i in range(12)]
    ts += [ts[-1] + jump + 900 * i for i in range(12)]
    iv = gaps.local_intervals(ts, 900)
    assert gaps.weights(ts, iv)[11] == weight
    assert E.Store.build([E.Reading(t, Fraction(100)) for t in ts]).spans[11] == weight


# ------------------------------------------------------------------------------- H7-H10 sensor active and GMI
def test_h7_exactly_70_percent_meets_the_rule():
    # 14112 one-minute readings in 14 days: 846720 s of 1209600 = 70.000 % exactly; ">= 70 %" (Battelino 2019)
    rows = seq([(150, 14112)], step=60)
    s, e = both_stats(rows, 14 * 86400, interval=60)
    assert s.sensor_active_exact == 70.0 and e["active"] == 70
    assert s.sensor_active_ok and e["gmi_shown"]
    assert st.gmi_shown(s, 14 * 86400) == r1(e["gmi"]) == 6.9


def test_h8_no_gmi_below_14_days():
    rows = seq([(150, 13 * 288)])
    s, e = both_stats(rows, 13 * 86400)
    assert s.data_availability == 100.0
    assert st.gmi_shown(s, 13 * 86400) is None and not e["gmi_shown"]


@pytest.mark.parametrize(
    "mean, gmi", [(100, 5.7), (125, 6.3), (150, 6.9), (175, 7.5), (200, 8.1), (250, 9.3), (300, 10.5), (350, 11.7)]
)
def test_h9_gmi_equals_bergenstal_2018_table(mean, gmi):
    # Bergenstal et al. (2018), Table 1 (CGM mean glucose mg/dL -> GMI %)
    s, e = both_stats(seq([(mean, 20)]), 20 * 300)
    assert s.gmi == r1(e["gmi"]) == gmi


def test_h10_gmi_slope_has_five_digits():
    # 248.4 mg/dL: 3.31 + 0.02392 x 248.4 = 9.2517 -> 9.3 (with 0.0239 it would be 9.2468 -> 9.2)
    s, e = both_stats(seq([(248.4, 20)]), 20 * 300)
    assert s.gmi == r1(e["gmi"]) == 9.3


@pytest.mark.parametrize("mmol, mmol_mol", [(5, 36), (7, 46), (10, 60), (14, 79)])
def test_h10b_gmi_mmol_mol_equals_bergenstal_table(mmol, mmol_mol):
    # Bergenstal Table 1, mmol/L -> mmol/mol: 5 -> 36.2, 7 -> 45.7, 10 -> 59.8, 14 -> 78.6 (whole numbers shown)
    assert U.gmi_mmol_mol(mmol * U.MGDL_PER_MMOLL) == mmol_mol


# ------------------------------------------------------------------------------- H11-H13 GRI zone, combined bands
@pytest.mark.parametrize(
    "gri, zone",
    [
        (0.0, "A"),
        (20.0, "A"),
        (20.1, "B"),
        (40.0, "B"),
        (40.1, "C"),
        (60.0, "C"),
        (60.1, "D"),
        (80.0, "D"),
        (80.1, "E"),
        (100.0, "E"),
    ],
)
def test_h11_gri_zone_boundaries(gri, zone):
    # Klonoff et al. (2023): zones A-E are the quintiles 0-20, 21-40, 41-60, 61-80, 81-100 (GlucoCraft: by the
    # displayed one-decimal value, SYSTEM_DESIGN.md 6.5)
    assert st.gri_zone(gri) == E.gri_zone(gri) == zone


def test_h12_gri_zone_belongs_to_the_displayed_value():
    # 501 of 2000 readings at 200 mg/dL: high 25.05 %, GRI 0.8 x 25.05 = 20.04 -> shown 20.0 -> zone A (the unrounded
    # 20.04 would be zone B)
    rows = seq([(200, 501), (100, 1499)])
    s, e = both_stats(rows, 2000 * 300)
    assert s.gri == r1(e["gri"]) == 20.0 and s.gri_zone == E.gri_zone(r1(e["gri"])) == "A"
    assert st.gri(0.0, 0.0, 25.05, 0.0) == (20.0, 0.0, 12.5, "A")


def test_h13_combined_band_rounded_once():
    # 1 of 80 readings at 50 and 1 at 60: very low 1.25 -> 1.3, low 1.25 -> 1.3 (halves away from zero), but below 70
    # combined 2.5 (the consensus target <4 % applies to the combined band, Battelino 2019), never 1.3 + 1.3 = 2.6
    rows = seq([(50, 1), (60, 1), (100, 78)])
    s, e = both_stats(rows, 80 * 300)
    assert (r1(s.time_critical_low), r1(s.time_low), r1(s.time_below_low)) == (1.3, 1.3, 2.5)
    assert (r1(e["very_low"]), r1(e["low"]), r1(e["below_70"])) == (1.3, 1.3, 2.5)


# ------------------------------------------------------------------------------- H14-H17 LO / HI and source states
def test_h14_lo_hi_are_a_category_and_no_value():
    # LO and HI keep their share of the time (very low 25, very high 25) and give no value: the mean, the
    # minimum and the maximum come from the two measured readings of 100 mg/dL
    texts = ["LO", "100", "HI", "100"]
    rows = []
    for i, t in enumerate(texts):
        c = L.classify(t, None)
        rows.append((T0 + 300 * i, c.value, c.limit))
    states = [common.SourceState(T0 + 300 * i + 30, t, T0 + 300 * i) for i, t in enumerate(texts)]
    assert [(r.ts, float(r.mg), r.limit) for r in E.readings_from_states(states, "mg/dL")] == rows
    s, e = both_stats(rows, 1200)
    assert s.mean == r1(e["mean"]) == 100.0
    assert (s.min_value, s.max_value) == (r1(e["min"]), r1(e["max"])) == (100.0, 100.0)
    assert (s.time_critical_low, s.time_critical_high) == (r1(e["very_low"]), r1(e["very_high"])) == (25.0, 25.0)
    assert (s.limit_low, s.limit_high) == (e["limits"]["low"], e["limits"]["high"]) == (1, 1)


@pytest.mark.parametrize(
    "text, kw, kind, mg",
    [
        ("39", {}, "low", 40.0),  # Dexcom code 39 = LOW, at the lower limit
        ("40", {}, "reading", 40.0),
        ("401", {"upper": 400, "dexcom_type": True}, "high", 400.0),  # Dexcom code 401 = HIGH
        ("401", {}, "reading", 401.0),  # another source: a measured 401
        ("400", {"upper": 400}, "reading", 400.0),  # Automatic 400: only above the limit is HI
        ("400", {"upper": 400, "upper_set": True}, "high", 400.0),  # set by hand: at the limit is HI
        ("399", {"upper": 400, "upper_set": True}, "reading", 399.0),
        ("Below 50 mg/dL", {"lower": 50}, "low", 50.0),  # Medtronic
        ("49", {"lower": 50}, "low", 50.0),
        ("50", {"lower": 50}, "reading", 50.0),  # Automatic 50: only below the limit is LO
        ("50", {"lower": 50, "lower_set": True}, "low", 50.0),  # set by hand: at the limit is LO
        ("51", {"lower": 50, "lower_set": True}, "reading", 51.0),
        ("40", {"lower_set": True}, "low", 40.0),
    ],
)
def test_h15_dexcom_and_medtronic_limits(text, kw, kind, mg):
    lower, upper = kw.get("lower", 40), kw.get("upper", 500)
    dexcom, lower_set, upper_set = kw.get("dexcom_type", False), kw.get("lower_set", False), kw.get("upper_set", False)
    c = L.classify(text, None, lower, upper, dexcom_type=dexcom, lower_set=lower_set, upper_set=upper_set)
    e = E.classify(text, "mg/dL", lower, upper, dexcom, lower_set=lower_set, upper_set=upper_set)
    assert (c.kind if c.kind != "reading" else "reading", c.value) == (kind, mg)
    assert (e[0], float(e[1])) == (kind, mg)


@pytest.mark.parametrize(
    "text, lower, kind, mg",
    [
        ("2.2", 40, "low", 40.0),
        ("2.3", 40, "reading", 41.4),
        ("Below 2.8 mmol/L", 50, "low", 50.0),
        ("2.7", 50, "low", 50.0),
        ("2.8", 50, "reading", 50.4),
    ],
)
def test_h16_mmol_lower_limits(text, lower, kind, mg):
    # 2.2 mmol/L is what 39 and 40 mg/dL both read: LO; 2.3 x 18.016 = 41.44 -> 41.4; 2.8 x 18.016 = 50.44 -> 50.4
    c = L.classify(text, U.UNIT_MMOLL, lower, 500)
    e = E.classify(text, "mmol/L", lower, 500)
    assert (c.kind, c.value) == (e[0], float(e[1])) == (kind, mg)


@pytest.mark.parametrize(
    "text, upper, upper_set, kind, mg",
    [
        ("22.2", 400, True, "high", 400.0),  # the 400 mg/dL limit in mmol/L, one decimal: at the limit
        ("22.2", 400, False, "reading", 400.0),  # Automatic: 22.2 x 18.016 = 399.96 -> 400.0, not above
        ("22.1", 400, True, "reading", 398.2),
        ("25.0", 450, True, "high", 450.0),
        ("27.7", 500, True, "reading", 499.0),
    ],
)
def test_h16b_mmol_upper_limit_set_by_hand(text, upper, upper_set, kind, mg):
    c = L.classify(text, U.UNIT_MMOLL, 40, upper, upper_set=upper_set)
    e = E.classify(text, "mmol/L", 40, upper, upper_set=upper_set)
    assert (c.kind, c.value) == (e[0], float(e[1])) == (kind, mg)


@pytest.mark.parametrize(
    "text, lower, lower_set, kind, mg",
    [
        ("2.8", 50, True, "low", 50.0),  # the 50 mg/dL limit in mmol/L, one decimal: at the limit
        ("2.8", 50, False, "reading", 50.4),  # Automatic: 2.8 x 18.016 = 50.44 -> 50.4, not below
        ("2.84", 50, True, "low", 50.0),  # 2.8 at one decimal
        ("2.9", 50, True, "reading", 52.2),
        ("2.7", 50, True, "low", 50.0),
        ("2.3", 40, True, "reading", 41.4),
    ],
)
def test_h16c_mmol_lower_limit_set_by_hand(text, lower, lower_set, kind, mg):
    c = L.classify(text, U.UNIT_MMOLL, lower, 500, lower_set=lower_set)
    e = E.classify(text, "mmol/L", lower, 500, lower_set=lower_set)
    assert (c.kind, c.value) == (e[0], float(e[1])) == (kind, mg)


@pytest.mark.parametrize("text", ["12", "38", "0", "-5", "unavailable", "unknown", "", "nan", "65535", "6553.5"])
def test_h17_error_codes_and_no_reading_states_are_no_glucose(text):
    # Nightscout error codes 1-12, xDrip+'s 38, the FreeStyle Libre warm-up marker, no-reading texts
    assert L.classify(text, None).kind in ("none", "warmup")
    assert E.classify(text, "mg/dL")[0] == "none"


# ------------------------------------------------------------------------------- H18-H22 hypoglycemia events
def _events(rows, limit):
    g = R.minute_grid(gc(rows), 60, rows[0][0], rows[-1][0] + 60, LA)
    got = R.report_events(g, limit)
    store = ex(rows)
    want = [(s, d, float(v)) for s, d, v in E.hypo_events(store.segments(rows[0][0], rows[-1][0] + 60), limit)]
    return got, want


@pytest.mark.parametrize("below, n_events", [(15, 1), (14, 0)])
def test_h18_h19_event_starts_after_15_minutes(below, n_events):
    # Danne et al. (2017), Section 4: "readings below the threshold for at least 15 min is considered an event"
    got, want = _events(seq([(100, 5), (60, below), (100, 30)], step=60), 70)
    assert got == want and len(got) == n_events
    if n_events:
        assert got[0] == (T0 + 300, 15 * 60, 60.0)


def test_h20_level_2_ends_at_70_not_54():
    # 50 x 20 min, 65 x 30, 70 x 15: the level 1 event and the level 2 event both end only after 15 minutes at
    # >= 70 (Danne 2017: "End of a CGM event: readings for 15 min at >=70 mg/dL"): both 50 minutes, not 20
    rows = seq([(50, 20), (65, 30), (70, 15), (100, 10)], step=60)
    for limit in (70, 54):
        got, want = _events(rows, limit)
        assert got == want == [(T0, 50 * 60, 50.0)]


def test_h21_the_end_needs_15_consecutive_minutes():
    # 60 x 15, 70 x 14, 60 x 1, 70 x 15: the single 60 restarts the 15 minutes: one event of 15 + 14 + 1 = 30 minutes
    got, want = _events(seq([(60, 15), (70, 14), (60, 1), (70, 15), (100, 5)], step=60), 70)
    assert got == want == [(T0, 30 * 60, 60.0)]


def test_h22_a_gap_ends_an_event():
    # 60 x 30 min, then 31 minutes without data (a gap), then 60 x 10 and 100 x 30: the gap ends the first event after
    # 30 minutes (its last reading covers its own minute); the 10 minutes after the gap are no event
    rows = seq([(60, 30)], step=60) + seq([(60, 10), (100, 30)], step=60, start=T0 + 29 * 60 + 31 * 60)
    got, want = _events(rows, 70)
    assert got == want == [(T0, 30 * 60, 60.0)]


# ------------------------------------------------------------------------------- H23 night and day in the report
def _report_rows(start: datetime, end: datetime, step=300, value=100, override=None, skip=None):
    rows, t = [], int(start.timestamp())
    while t < int(end.timestamp()):
        if not (skip and skip[0] <= t < skip[1]):
            v = override(t) if override else None
            rows.append((t, value if v is None else v, None))
        t += step
    return rows


def test_h23_night_is_00_00_to_05_59():
    # June (no daylight-saving change), 5-minute readings at 100, two lows at 60: day 3 05:55-06:30 (35 min, 5 of
    # them at night) and day 8 06:00-06:30 (30 min, all by day). Events: 2, at night 1 (it starts at 05:55); mean
    # duration (35 + 30) / 2 = 32.5 -> 33. Time below 70 at night 5 / (14 x 360) = 0.099 -> 0.1 %; by day
    # 60 / (14 x 1080) = 0.397 -> 0.4 %
    now = datetime(2026, 6, 15, tzinfo=LA)
    start = now - timedelta(days=14)
    lows = [(datetime(2026, 6, 4, 5, 55, tzinfo=LA), 35), (datetime(2026, 6, 9, 6, 0, tzinfo=LA), 30)]

    def low(t):
        return 60 if any(int(a.timestamp()) <= t < int(a.timestamp()) + m * 60 for a, m in lows) else None

    rows = _report_rows(start - timedelta(days=1), now, override=low)
    rep = R.build_report(gc(rows), 300, LA, 14, False, [], now=int(now.timestamp()))
    e = E.report(ex(rows), LA, int(now.timestamp()), 14)
    lw = rep["lows"]
    assert (lw["events"], lw["events_night"], lw["mean_duration_min"]) == (2, 1, 33)
    assert (e["lows"]["events"], e["lows"]["events_night"], int(rhu(e["lows"]["mean_minutes"]))) == (2, 1, 33)
    assert (lw["night_below_70"], lw["day_below_70"]) == (0.1, 0.4)
    assert (r1(e["night_day"]["night_below_70"]), r1(e["night_day"]["day_below_70"])) == (0.1, 0.4)


# ------------------------------------------------------------------------------- H24-H28 extended high-glucose events
def _xhigh(rows):
    got, _ = R.extended_high_runs(gc(rows), 60, rows[0][0] - 1, rows[-1][0] + 60)
    want = E.extended_high_events(ex(rows), rows[-1][0] + 60)
    return [d // 60 for _, d in got], [d // 60 for _, d in want]


@pytest.mark.parametrize(
    "parts, minutes",
    [
        ([(251, 120), (150, 30)], [120]),  # exactly 120 minutes above 250: an event
        ([(251, 119), (150, 30)], []),  # 119: none
        (
            [(260, 130), (170, 14), (200, 10), (170, 15), (150, 10)],
            [154],
        ),  # ends at the first reading of the 15-min run
        ([(260, 130), (180, 15), (150, 10)], [130]),  # 180 is "at or below 180" (p. 48)
    ],
)
def test_h24_h26_extended_high_start_and_end(parts, minutes):
    got, want = _xhigh(seq(parts, step=60))
    assert got == want == minutes


def test_h27_a_gap_ends_an_extended_high_event_at_its_last_reading():
    # 260 x 125 min, 31 minutes without data, 260 x 60: the event ends at the end of its last reading before the gap
    # (minute 125: the reading at minute 124 counts for its own minute, step 5); the 60 minutes after the gap are no
    # event
    rows = seq([(260, 125)], step=60) + seq([(260, 60), (150, 20)], step=60, start=T0 + 124 * 60 + 31 * 60)
    got, want = _xhigh(rows)
    assert got == want == [125]


def test_h28_a_run_cut_by_a_gap_before_120_minutes_is_no_event():
    rows = seq([(260, 100)], step=60) + seq([(260, 100), (150, 20)], step=60, start=T0 + 99 * 60 + 31 * 60)
    got, want = _xhigh(rows)
    assert got == want == []


# ------------------------------------------------------------------------------- H29-H31 sensor active and gaps
def test_h29_the_23_hour_day_is_complete():
    # 5-minute readings up to midnight after March 8, 2026 (daylight saving begins: a 23-hour day). The 14-day period
    # and March 8 itself are 100.0 % active: measured against their real length (23 h), not 24 h (95.8 %)
    now = datetime(2026, 3, 9, tzinfo=LA)
    rows = _report_rows(now - timedelta(days=15), now)
    rep = R.build_report(gc(rows), 300, LA, 14, False, [], now=int(now.timestamp()))
    e = E.report(ex(rows), LA, int(now.timestamp()), 14)
    assert rep["period"]["active"] == r1(e["period"]["active"]) == 100.0
    assert rep["quality"]["per_day"][-1]["active"] == r1(e["quality"]["per_day"][-1]) == 100.0


def test_h30_the_reading_before_midnight_reaches_into_the_day():
    # 5-minute readings 100 s before each 5-minute mark: the reading at 23:58:20 covers 00:00:00-00:03:20, so every
    # day is 100.0 % active (without it 99.8 %)
    now = datetime(2026, 6, 15, tzinfo=LA)
    rows = [(t - 100, v, lim) for t, v, lim in _report_rows(now - timedelta(days=15), now + timedelta(minutes=5))]
    rep = R.build_report(gc(rows), 300, LA, 14, False, [], now=int(now.timestamp()))
    e = E.report(ex(rows), LA, int(now.timestamp()), 14)
    assert [d["active"] for d in rep["quality"]["per_day"]] == [r1(x) for x in e["quality"]["per_day"]] == [100.0] * 14


def test_h31_a_gap_across_the_period_start_is_counted_with_its_whole_length():
    # no data from 23:00 before the period's first day to 01:00 on it: one gap of the period, 120 minutes long (60 of
    # them inside the period)
    now = datetime(2026, 6, 15, tzinfo=LA)
    start = int((now - timedelta(days=14)).timestamp())
    rows = _report_rows(now - timedelta(days=15), now, skip=(start - 3600 + 300, start + 3600))
    rep = R.build_report(gc(rows), 300, LA, 14, False, [], now=int(now.timestamp()))
    e = E.report(ex(rows), LA, int(now.timestamp()), 14)
    q = rep["quality"]
    assert (q["gaps"], q["longest_gap_minutes"]) == (1, 120)
    assert (e["quality"]["gaps"], e["quality"]["longest_gap_minutes"]) == (1, 120)


# ------------------------------------------------------------------------------- H32-H34 the week rule, intervals
@pytest.mark.parametrize("minutes, counted", [(7056, True), (7055, False)])
def test_h32_week_rule_at_least_70_percent(minutes, counted):
    # a week counts with sensor active at least 70% (the enough-data rule): 70% of 604,800 s is 423,360 s, which
    # 7056 one-minute readings cover exactly (7056 x 60 s); 7055 cover 423,300 s = 69.99%
    now = datetime(2026, 6, 15, tzinfo=LA)
    b = int(now.timestamp())
    a = int((now - timedelta(days=7)).timestamp())
    rows = seq([(120, minutes)], step=60, start=a + 86400)
    weeks = st.compute_weekly_trends(gc(rows), detected_interval=60, windows=[(a, b)], **AGP_RANGES)
    e = E.weekly(ex(rows), LA, b, weeks=1)
    # (the calculator keeps a week below the rule with its sensor active, "enough" False)
    assert (weeks[0] is not None) == (e[0] is not None and e[0]["enough"]) == counted


def test_h33_reading_interval_never_includes_the_interval_judged():
    # intervals 900, 900, [2400 judged at reading 2], 300 x 5, 900, 900, 300. The interval at reading 2: the 2 before
    # (900, 900) filled with the 8 after the judged one (300 x 5, 900, 900, 300): 300 x 6, 900 x 4 -> upper middle
    # 300 -> threshold 900 -> the 2400 s is a gap and reading 2 weighs 300 s. Counting the judged 2400 in (and one
    # interval after it less) would give 300 x 5, 900 x 4, 2400 -> 900 -> threshold 2700 -> no gap.
    d = [900, 900, 2400, 300, 300, 300, 300, 300, 900, 900, 300]
    ts = [T0]
    for x in d:
        ts.append(ts[-1] + x)
    assert gaps.local_intervals(ts, 300)[2] == E.intervals(ts, 300)[2] == 300
    assert gaps.weights(ts, gaps.local_intervals(ts, 300))[2] == 300.0
    assert E.Store.build([E.Reading(t, Fraction(100)) for t in ts]).spans[2] == 300


def test_h34_detected_interval_is_the_upper_middle(tmp_path):
    # 11 readings: intervals 60 x 5 and 120 x 5 -> 10 intervals, upper middle (index 5) = 120 s
    ts = [T0 + 60 * i for i in range(6)] + [T0 + 300 + 120 * i for i in range(1, 6)]
    storage = GlucoCraftStorage(str(tmp_path), "verify")
    storage.load()
    storage.merge_history([(t, 100.0, None) for t in ts])
    assert storage.detect_interval() == E.detected_interval(ts) == 120


# ------------------------------------------------------------------------------- H35-H36 units and rounding
def test_h35_gmi_mmol_mol_rounds_an_exact_half_up():
    # a mean whose GMI in mmol/mol is exactly 46.5 in floating point: halves away from zero -> 47 (round() gives 46)
    mean = 129.36197557518588
    assert 12.71 + 4.70587 * (mean / U.MGDL_PER_MMOLL) == 46.5
    assert U.gmi_mmol_mol(mean) == 47


@pytest.mark.parametrize(
    "call, want",
    [
        (lambda: U.to_mgdl(5.5, U.UNIT_MMOLL), 99.1),  # 5.5 x 18.016 = 99.088
        (lambda: U.to_mgdl(10.0, U.UNIT_MMOLL), 180.0),  # the published pair, not 180.16
        (lambda: U.from_mgdl(70, U.UNIT_MMOLL), 3.9),  # 70 / 18.016 = 3.885 -> the pair 3.9
        (lambda: U.fmt(70.45, U.UNIT_MGDL), "70"),  # rounded once from the stored value
        (lambda: U.fmt(70.5, U.UNIT_MGDL), "71"),  # halves away from zero (format() would give 70)
        (lambda: U.fmt(337.8, U.UNIT_MMOLL), "18.8"),  # 337.8 / 18.016 = 18.75 exactly: up
        (lambda: float(E.to_mgdl("5.5", "mmol/L")), 99.1),
        (lambda: float(E.to_mgdl("10.0", "mmol/L")), 180.0),
    ],
)
def test_h36_unit_rule(call, want):
    assert call() == want


def test_h37_retention_whole_local_days():
    # 200 local days before February 15, 2026 is July 30, 2025 (16 + 31 + 30 + 31 + 30 + 31 + 31 = 200 days back)
    now = int(datetime(2026, 2, 16, tzinfo=LA).timestamp())
    store = E.Store.build([E.Reading(now - 300, Fraction(100))])
    assert E.retention_cutoff(store, LA, now) == int(datetime(2025, 7, 30, tzinfo=LA).timestamp())
