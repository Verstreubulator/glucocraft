# License: AGPL-3.0-only (LICENSE).
"""The rounding rules of every backend text: a value is rounded once from its unrounded value, no text shows a
negative zero, and a value shown next to a rule it is compared with stays on its side of the rule.

Rules under test:
- statistics.PeriodStats keeps the percentages and the CV unrounded; they reach the period sensors' states and
  attributes, the HTTP API, Change Over Time and the panel feed (GLU2 STATS and TRENDS float32) unrounded, so that every
  display rounds once (an exact 69.45% is 69% whole and 69.5% with one decimal, never 70% from a stored 69.5);
- no formatter writes "-0" or "-0.0" (units, regional, the spoken numbers, the notifications, the report and the PDF);
- units.shown_against: where one decimal would show a rule's value that the value does not meet or passes, two
  decimals ("Sensor active 69.96%"), and with two decimals still on the rule, the last one moved to the value's side.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

import pytest
from glucocraft import notification as N
from glucocraft import panel_feed as PF
from glucocraft import report as R
from glucocraft import report_pdf as RP
from glucocraft import sensor as se
from glucocraft import spoken
from glucocraft import statistics as st
from glucocraft.regional import Regional
from glucocraft.storage import GlucoseReading
from glucocraft.units import UNIT_MGDL, UNIT_MMOLL, fmt, round_half_up, shown_against

from conftest import PERIOD_KEYS, FakeCoordinator, stats_record


@dataclass
class _RegionalCoordinator(FakeCoordinator):
    """A FakeCoordinator with regional settings of the test's choice."""

    rg: Regional = None

    @property
    def regional(self):
        """The regional settings of the test, else the defaults."""
        return self.rg or Regional()


# ------------------------------------------------------------------------------------------------ negative zero
@pytest.mark.parametrize("x", [-0.004, -0.04, -0.4, -0.0, -0.049999])
def test_round_half_up_gives_no_negative_zero(x):
    for decimals in (0, 1, 2):
        r = round_half_up(x, decimals)
        if r == 0:
            assert math.copysign(1.0, r) == 1.0 and not str(r).startswith("-"), (x, decimals, r)


def test_no_formatter_writes_a_negative_zero():
    rg, comma = Regional(), Regional(decimal_separator=",")
    texts = [
        fmt(-0.4, UNIT_MGDL),
        fmt(-0.4, UNIT_MMOLL),
        rg.num(-0.04, 1),
        rg.num(-0.004, 2),
        comma.num(-0.04, 1),
        rg.g(-0.0),
        rg.dec("-0"),
        rg.dec("-0.0"),
        comma.dec("-0.00"),
        rg.dec(str(round_half_up(-0.04, 1))),
        spoken.speak_number(-0.04, 1),
        spoken.speak_number(-0.4, 0),
        spoken.speak(-0.04, "%", 1),
        N._pct(rg, -0.04),
        RP._signed(-0.04, 1),
        RP._signed(-0.4, 0),
    ]
    for text in texts:
        assert "-0" not in text and "minus" not in text, texts
    assert comma.dec("-0.0") == "0,0" and rg.dec("-0.5") == "-0.5"  # a real negative number keeps its sign
    assert spoken.speak_number(-0.06, 1) == "minus 0.1"


# ------------------------------------------------------------------------------------------------ two decimals
@pytest.mark.parametrize(
    "value, rule, shown",
    [
        (69.96, 70, (69.96, 2)),  # below the rule, one decimal would show the rule
        (69.996, 70, (69.99, 2)),  # two decimals still show the rule: the last one moves to the value's side
        (70.004, 70, (70.01, 2)),
        (70.04, 70, (70.04, 2)),  # above a rule of ">70%": one decimal would show the rule
        (70.0, 70, (70.0, 1)),  # exactly the rule: one decimal
        (69.94, 70, (69.9, 1)),  # one decimal is already below the rule
        (3.96, 4, (3.96, 2)),
        (49.97, (70, 50), (49.97, 2)),  # one of several rules
        (55.55, (70, 50), (55.6, 1)),
    ],
)
def test_shown_against_a_rule(value, rule, shown):
    assert shown_against(value, rule) == shown


def test_the_summary_shows_sensor_active_beside_the_70_percent_rule(fake_hass):
    """ "Sensor active 70.0%: below the 70%" is not written: 69.96% is shown with two decimals, also with the comma."""
    for rg, text, one in ((Regional(), "69.96", "70.0"), (Regional(decimal_separator=","), "69,96", "70,0")):
        stats = {k: stats_record(period_label=k, data_availability=69.96) for k in PERIOD_KEYS}
        c = _RegionalCoordinator(period_stats=stats, period_stats_agp=dict(stats), rg=rg, hass=fake_hass())
        lines = " ".join(N._overview_lines(c, c.period_stats["14d"], set()))
        assert f"{text}%" in lines and f"{one}%" not in lines, lines
        assert lines.count(f"{text}%") == 1  # the values line (a summary shows no section without enough data)
        # every period is shown beside the 70% rule (the enough-data rule): also below 14 days
        short = " ".join(N._overview_lines(c, c.period_stats["7d"], set()))
        assert f"{text}%" in short and f"{one}%" not in short


def test_the_consensus_targets_compare_and_show_the_unrounded_value():
    rg = Regional()
    # 70.04% meets ">70%": shown as 70.04%, not as 70.0% beside "met"
    line = N._assess_target(70.04, 70, ">", "Time in range", "source", rg, marked=True)
    assert "70.04%" in line and "more than 70%" in line and "not met" not in line
    line = N._assess_target(3.96, 4, "<", "Time below range", "source", rg, marked=True)
    assert "3.96%" in line and "not met" not in line
    line = N._assess_target(25.04, 25, "<", "Time above range", "source", rg, marked=True)
    assert "25.04%" in line and "not met" in line
    assert "33.3%" in N._assess_target(33.333, 36, "<=", "CV", "source", rg, marked=True)  # rounded once


def test_the_report_shows_sensor_active_beside_its_rule():
    # beside the enough-data rule (70%, compared unrounded): two decimals where one would show 70.0%
    assert R._active_shown(69.96) == 69.96
    assert R._active_shown(70.04) == 70.04
    assert R._active_shown(49.98) == 50.0  # no 50% rule any more: one decimal
    assert R._active_shown(85.04) == 85.0
    assert R._one_decimal(69.45) == 69.5 and R._one_decimal(None) is None


# ------------------------------------------------------------------------------------------------ percentages and CV unrounded, rounded once by every display
def _period(values_and_minutes, period_seconds=None):
    """The statistics of 5-minute readings: (value, count) pairs in order."""
    t, rd = 1_800_000_000, []
    for value, count in values_and_minutes:
        for _ in range(count):
            rd.append(GlucoseReading(t, float(value)))
            t += 300
    return st.compute_period_stats(rd, "x", period_seconds or len(rd) * 300, 300)


def test_the_statistics_keep_the_percentages_unrounded():
    s = _period([(120, 1389), (200, 611)])  # 69.45% in range
    assert s.pct_time_in_target_range == pytest.approx(69.45) and s.pct_time_in_target_range != 69.5
    assert round_half_up(s.pct_time_in_target_range) == 69  # rounded once (whole): 69, not 70 from 69.5
    assert s.time_above_high == pytest.approx(30.55) and s.cv != round_half_up(s.cv, 1)
    assert s.gmi == round_half_up(s.gmi, 1) and s.gri == round_half_up(s.gri, 1)  # one decimal, as shown


def test_the_sensors_and_the_panel_feed_carry_the_unrounded_values():
    s = _period([(120, 1389), (200, 611)])  # 2000 readings of 5 minutes covering their whole period
    coordinator = type("C", (), {})()
    coordinator.period_stats = coordinator.period_stats_agp = {"14d": s}
    e = se.GlucoCraftPeriodConsensus.__new__(se.GlucoCraftPeriodConsensus)
    e.coordinator, e._period = coordinator, "14d"
    a = e.extra_state_attributes
    assert a["time_70_180"] == s.pct_time_in_target_range and a["sensor_active"] == s.data_availability
    # the state is rounded once to one decimal from the exact value (the recorder rows); the attributes stay exact
    assert a["cv"] == s.cv and e.native_value == round_half_up(s.pct_time_in_target_range, 1) == 69.5
    assert se.GlucoCraftPeriodConsensus._attr_suggested_display_precision == 1  # Home Assistant rounds once
    record = PF._period_record(s, 14 * 86400)
    values = struct.unpack("<10fHH", record)
    assert values[4] == pytest.approx(s.pct_time_in_target_range, abs=1e-5)  # float32 of the unrounded value
    assert values[2] == pytest.approx(s.cv, abs=1e-5)
    assert round_half_up(values[4]) == 69 and round_half_up(values[4], 1) == 69.5
