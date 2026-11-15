# License: AGPL-3.0-only (LICENSE).
"""The rules of the CGM Report's numbers and the summaries' comparisons, each with the case that tells it apart.

Rules under test:
  * one sensor active formula (statistics.sensor_active); the Data Quality page shows the period's sensor active on
    its side of the 70% and 50% rules, as page 1 does (2,821 five-minute readings in 14 days: 69.97% everywhere);
  * the band percentages are multiplied before the division, so an exact 4.00% stays 4.0 in every split of the two
    bands below 70 mg/dL, and is judged and shown as 4.0;
  * the report's percentages beside a consensus target keep its side of it (69.9653% -> 69.97%, 3.9683% -> 3.97%);
  * the comparison with the previous period subtracts the values as shown, with their decimals (69.97% - 50.0%);
  * the summaries compare 14 with 90 days only when both have data for at least half of them, from the values as
    shown (70.5% and 65.4%: 5.1 points, marked), and name the GRI rule below 70% sensor active;
  * a custom GMI goal in mmol/mol by the GMI rule on both report paths (4.3% -> 24, 9.7% -> 82 mmol/mol);
  * extended high-glucose events: an event begun long before the period is not counted again; a gap-ended event
    ends at the end of its last reading (24 five-minute readings: 120 minutes);
  * one table of the goal directions (const.GOAL_OPS), the consensus zones from const.AGP_RANGES (the PDF and the
    page's data), Change Over Time as a dataclass.
"""

from __future__ import annotations

import bisect
import io
import re
from datetime import datetime

import pytest
from glucocraft import change as ch
from glucocraft import const
from glucocraft import notification as N
from glucocraft import report as R
from glucocraft import statistics as st
from glucocraft.regional import Regional
from glucocraft.storage import GlucoseReading
from glucocraft.units import gmi_goal_mmol_mol, shown_against

from conftest import LA, PERIOD_KEYS, Rd, stats_record

NOW = int(datetime(2026, 10, 22, 9, 0, tzinfo=LA).timestamp())
START14 = int(datetime(2026, 10, 8, tzinfo=LA).timestamp())


def _report(rd: list, days: int = 14, **kw) -> dict:
    return R.build_report(rd, 300, LA, days, False, [], now=NOW, **kw)


# ------------------------------------------------------------------------------------------------ sensor active
def test_one_sensor_active_formula():
    assert st.sensor_active(0, 0) == 0.0 and st.sensor_active(10, -1) == 0.0
    assert st.sensor_active(4000, 3600) == 100.0  # at most 100
    assert st.sensor_active(1500, 3600) == 100.0 * 1500 / 3600
    s = st.compute_period_stats([GlucoseReading(k * 300, 120.0) for k in range(12)], "x", 7200, 300)
    assert s.data_availability == s.sensor_active_exact == st.sensor_active(3600, 7200) == 50.0


def test_data_quality_shows_sensor_active_on_its_side_of_the_70_percent_rule():
    rd = [Rd(START14 + 300 * i, 150.0) for i in range(2821)]  # 2821 x 300 s / 14 days = 69.9653%
    d = _report(rd)
    assert d["standard"]["active"] == d["period"]["active"] == d["quality"]["active"] == 69.97
    assert d["standard"]["enough_data"] is False
    rd = [Rd(START14 + 300 * i, 150.0) for i in range(2822)]  # 69.9901%
    assert _report(rd)["quality"]["active"] == 69.99


# ------------------------------------------------------------------------------------------------ band percentages
@pytest.mark.parametrize("n", [25, 50, 100, 250, 500, 1000, 2500, 4000])
def test_every_split_of_an_exact_four_percent_stays_four(n):
    below = n * 4 // 100  # 4.00% of the readings below 70 mg/dL, every split between low and very low
    for very_low in range(below + 1):
        values = [50.0] * very_low + [60.0] * (below - very_low) + [120.0] * (n - below)
        rd = [GlucoseReading(k * 300, v) for k, v in enumerate(values)]
        s = st.compute_period_stats(rd, "x", n * 300, 300, weights=[300.0] * n)
        assert s.time_below_low == 4.0, (n, very_low)
        assert shown_against(s.time_below_low, 4, 1) == (4.0, 1)
        assert s.pct_time_in_target_range == 96.0
        assert not const.COMPARE["<"](s.time_below_low, 4)  # "less than 4%" is not met by exactly 4%


# ------------------------------------------------------------------------------------------------ beside the targets
def test_report_percentages_beside_their_consensus_targets():
    # 4032 five-minute readings in 14 days: 2821 in range (69.9653%), 160 below 70 mg/dL (3.9683%), the rest above 180
    rd = []
    for i in range(4032):
        v = 120.0 if i < 2821 else 60.0 if i < 2821 + 160 else 200.0
        rd.append(Rd(START14 + 300 * i, v))
    s = _report(rd)["standard"]
    assert s["tir"] == 69.97 and s["below_70"] == 3.97  # not 70.0 beside ">70%", not 4.0 beside "<4%"
    assert s["low"] == 4.0  # no target of its own: one decimal
    assert R._beside_target("cv", 36.04) == 36.04 and R._beside_target("cv", 35.5) == 35.5  # "36% or less"


def test_the_previous_period_difference_keeps_the_decimals_shown():
    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")
    from glucocraft import report_pdf as P

    from node_runner import NODE
    from test_report_outputs import _data, _page_html

    d = _data(30, False)
    d["period"].update({"active": 69.97})
    d["previous"].update({"active": 50.0, "compare_ok": True, "tir": 70.0})
    pdf, _ = P.render(d, True, False)
    shown = [" ".join(" ".join((p.extract_text() or "").split()) for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)]
    if NODE:
        html = re.sub(r'<span class="sr">.*?</span>', "", _page_html(d))
        shown.append(" ".join(re.sub(r"<[^>]+>", " ", html).split()))
    for text in shown:
        row = text[text.find("Sensor active", text.find("Comparison with")) :][:90]
        assert "69.97% 50% +19.97 percentage points" in row, row  # not +20 from a second rounding


# ------------------------------------------------------------------------------------------------ the summaries
@pytest.fixture
def coord(make_coordinator):
    def make(a: dict, b: dict):
        c = make_coordinator(
            current_glucose=112.0,
            period_stats={k: stats_record() for k in PERIOD_KEYS},
            period_stats_agp={k: stats_record() for k in PERIOD_KEYS},
        )
        c.period_stats_agp["14d"] = stats_record(**a)
        c.period_stats_agp["90d"] = stats_record(**b)
        return c

    return make


def test_the_comparison_needs_enough_data_in_both_periods(coord):
    low = coord({"data_availability": 3.0, "pct_time_in_target_range": 100.0}, {"data_availability": 0.5})
    assert N._tir_comparison(low, "14d", "90d", always=True) == []
    assert "percentage points" not in N._build_daily_summary(low) + N._build_weekly_summary(low)
    one = coord({"data_availability": 80.0, "pct_time_in_target_range": 90.0}, {"data_availability": 69.99})
    assert N._tir_comparison(one, "14d", "90d", always=True) == []
    both = coord({"data_availability": 70.0, "pct_time_in_target_range": 90.0}, {"data_availability": 70.0})
    # 90.0% and the 90 days' 71.0%: 19.0 points
    assert "19.0 percentage points higher" in " ".join(N._tir_comparison(both, "14d", "90d", always=True))


def test_the_comparison_subtracts_the_values_as_shown(coord):
    # 70.46% and 65.44% are shown as 70.5% and 65.4%: 5.1 points (marked), not 5.0 from the unrounded 5.02
    c = coord({"pct_time_in_target_range": 70.46}, {"pct_time_in_target_range": 65.44})
    text = " ".join(N._tir_comparison(c, "14d", "90d", always=False))
    assert "last 14 days compared with the last 90 days: 5.1 percentage points higher." in text
    assert "For clinical trials" in text  # the 5-point mark from the shown difference
    # 70.44% and 65.46% are shown as 70.4% and 65.5%: 4.9 points, not marked (unrounded 4.98)
    c = coord({"pct_time_in_target_range": 70.44}, {"pct_time_in_target_range": 65.46})
    assert N._tir_comparison(c, "14d", "90d", always=False) == []


def test_the_gri_needs_enough_data_as_the_gmi_does(coord):
    c = coord({"data_availability": 60.0}, {})
    text = "\n".join(N._consensus_lines(c, "14d", 14 * 86400, "14 days", set()))
    assert "Klonoff" not in text and "Bergenstal" not in text
    c = coord({"data_availability": 70.0}, {})
    text = "\n".join(N._consensus_lines(c, "14d", 14 * 86400, "14 days", set()))
    assert "(Klonoff et al., 2023)" in text and "unlike GMI" not in text


# ------------------------------------------------------------------------------------------------ the GMI goal
@pytest.mark.parametrize("goal, mm", [(4.3, 24), (9.7, 82), (4.0, 20), (12.0, 108)])
def test_the_gmi_goal_in_mmol_mol_by_the_gmi_rule(goal, mm):
    # the laboratory master equation gives 23 and 83 for 4.3% and 9.7%
    assert gmi_goal_mmol_mol(goal) == mm
    rd = [Rd(START14 + 300 * i, 150.0) for i in range(4032)]
    d = _report(rd, custom={"ranges": {}, "goals": {"gmi": {"op": "<", "value": goal}}})
    row = next(r for r in d["custom_appendix"]["rows"] if r["key"] == "gmi")
    assert row["goal_mmol_mol"] == mm
    pytest.importorskip("reportlab")
    from glucocraft import report_pdf as P

    P._RG["rg"] = Regional(a1c_unit="mmol_mol")
    try:
        assert P._gmi_main(row["goal"], row["goal_mmol_mol"]) == f"{mm} mmol/mol"
    finally:
        P._RG["rg"] = Regional()


def test_the_report_page_shows_the_gmi_goal_from_the_data():
    pytest.importorskip("reportlab")
    from test_report_outputs import REPORT_JS, _page

    out = _page("GC.a1c = 'mmol_mol'; return [rA1c(9.7, 82), rA1c(4.3, 24)];")
    assert out == ["82 mmol/mol", "24 mmol/mol"]
    assert "rA1c(r.goal, r.goal_mmol_mol)" in REPORT_JS  # the appendix's goal with its value from the report data


# ------------------------------------------------------------------------------------------------ extended highs
def _xhigh(rd: list, s_ts: int, e_ts: int) -> list[tuple[int, int]]:
    return R.extended_high_runs(rd, 300, s_ts, e_ts)[0]


def test_an_extended_high_event_begun_before_the_look_back_is_not_counted_again():
    s_ts = START14
    rd = [Rd(t, 300.0) for t in range(s_ts - 52 * 3600, s_ts - 49 * 3600, 300)]  # an event 52 hours before
    rd += [Rd(t, 220.0) for t in range(s_ts - 49 * 3600, s_ts + 3600, 300)]  # 181-250: the event goes on
    rd += [Rd(t, 300.0) for t in range(s_ts + 3600, s_ts + 4 * 3600, 300)]  # above 250 for 3 hours in the period
    rd += [Rd(t, 120.0) for t in range(s_ts + 4 * 3600, s_ts + 6 * 3600, 300)]
    assert _xhigh(rd, s_ts, s_ts + 14 * 86400) == []
    # ended before the period (15 minutes at or below 180), the same 3 hours are a new event
    rd2 = [r for r in rd if not (s_ts - 3 * 3600 <= r.timestamp < s_ts - 3 * 3600 + 900)]
    rd2 += [Rd(t, 150.0) for t in range(s_ts - 3 * 3600, s_ts - 3 * 3600 + 900, 300)]
    rd2.sort(key=lambda r: r.timestamp)
    assert _xhigh(rd2, s_ts, s_ts + 14 * 86400) == [(s_ts + 3600, 3 * 3600)]
    # ended by a data gap before the period
    rd3 = [r for r in rd if not (s_ts - 3 * 3600 <= r.timestamp < s_ts - 2 * 3600)]
    assert _xhigh(rd3, s_ts, s_ts + 14 * 86400) == [(s_ts + 3600, 3 * 3600)]


def test_a_gap_ended_extended_high_event_lasts_the_time_its_readings_cover():
    s_ts = START14 + 86400
    rd = [Rd(s_ts - 3600 + k * 300, 120.0) for k in range(12)]
    rd += [Rd(s_ts + k * 300, 300.0) for k in range(24)]  # 24 readings above 250: 120 minutes, then a gap
    rd += [Rd(s_ts + 24 * 300 + 3600 + k * 300, 120.0) for k in range(12)]
    assert _xhigh(rd, START14, START14 + 14 * 86400) == [(s_ts, 120 * 60)]
    ev = R.extended_high_events(rd, 300, START14, START14 + 14 * 86400, LA)
    assert ev["events"] == 1 and ev["longest_minutes"] == 120


# ------------------------------------------------------------------------------------------------ one table each
def test_one_table_of_goal_directions_and_consensus_targets(make_coordinator):
    assert const.GOAL_OPS == {
        "time_in_range": ">",
        "time_in_tight_range": ">=",
        "time_low": "<",
        "time_very_low": "<",
        "time_high": "<",
        "time_very_high": "<",
        "cv": "<=",
        "gmi": "<",
    }
    from glucocraft import panel_feed as PF

    assert PF.CONSENSUS_TARGETS == ((">", 70), None, None, ("<=", 36), None, ("<", 4), ("<", 25), None, None)
    for key, (a, b) in {">": (2, 1), ">=": (1, 1), "<": (1, 2), "<=": (1, 1)}.items():
        assert const.COMPARE[key](a, b)


def test_the_consensus_zones_come_from_one_table():
    pytest.importorskip("reportlab")
    from glucocraft import report_pdf as P

    r = const.AGP_RANGES
    assert (P.VLOW, P.LOW, P.HIGH, P.VHIGH) == (
        r["range_critical_low"],
        r["range_low"],
        r["range_high"],
        r["range_critical_high"],
    )
    # the four thresholds the page draws (its zones and chart lines), from the same table
    zones = {k: v for k, v in const.public_agp_ranges().items() if "tight" not in k}
    assert _report([])["zones"] == zones and len(zones) == 4


def test_change_over_time_is_a_dataclass():
    class Store:
        """The three things compute_change_sets reads of the store: 20 days of five-minute readings."""

        def __init__(self):
            self.readings = [GlucoseReading(NOW - 20 * 86400 + k * 300, 120.0) for k in range(20 * 288)]

        def reading_intervals(self, interval):
            return [300] * len(self.readings)

        def window_indices(self, start, end):
            ts = [r.timestamp for r in self.readings]
            return bisect.bisect_left(ts, start), bisect.bisect_left(ts, end)

    (cot,) = ch.compute_change_sets(Store(), 300, NOW, (dict(const.AGP_RANGES),))
    assert isinstance(cot, ch.ChangeOverTime) and len(cot.weeks14) == len(cot.stats14) == len(cot.week_ends) == 26
    assert cot.stats14[0].sensor_active_exact == 100.0 and cot.weeks14[0]["active_ok"] is True
    assert set(cot.compare) == {name for name, _, _ in ch.COMPARE}
    assert cot.week_ends[1] == NOW - 7 * 86400
