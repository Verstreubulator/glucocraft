# License: AGPL-3.0-only (LICENSE).
"""Deterministic sine waves (tools/synth/sine.py): three peaks and three valleys per day, shifted to lie inside each
glucose range or to cross several, every 1, 5 and 15 minutes, with mg/dL and mmol/L sources.

Three checks:
  * the wave itself: three peaks and three valleys per local day, at the offset plus and minus the amplitude;
  * the independent calculator (tools/synth/expected.py) on the sampled series against the closed-form values
    (expected.sine_statistics). A wave inside one range has exactly 100 % of its time there; the mean of a mg/dL wave
    is exactly its offset (the readings of a cycle pair up symmetrically around it). The stated bounds, and why they
    hold: a reading stands for its whole interval, so the time beyond a range limit is off by at most one interval per
    crossing, six crossings per day (600 x interval / 86400 percent per limit, twice that for a range between two
    limits); a value rounded to the source precision is off by at most half a step, which moves the mean and the
    standard deviation by at most that much (0.5 mg/dL; for mmol/L the half step of 0.05 mmol/L is 0.9 mg/dL, plus
    the conversion to one decimal of mg/dL and the published pairs, at most 0.6 mg/dL: 1.5 mg/dL in all);
  * GlucoCraft's real code against the calculator, every statistics value, the CGM Report and the stored readings,
    exactly (the comparison of tests/test_synth_scenarios.py), in both display units.
"""

from __future__ import annotations

import importlib
import math
import sys
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import test_synth_scenarios as T

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
E = importlib.import_module("tools.synth.expected")
W = importlib.import_module("tools.synth.sine")
common = importlib.import_module("tools.synth.common")

TZ = ZoneInfo(common.DEFAULT_TZ)
CASES = [(n, u, i) for n in W.NAMES for u in T.SOURCE_UNITS for i in W.INTERVALS]
INSIDE = {
    "very_low": "very_low",
    "low": "low",
    "tight_range": "titr",
    "in_range_above_tight": "tir",
    "high": "high",
    "very_high": "very_high",
}


def _id(name: str, unit: str, interval: int) -> str:
    return f"{name}-{unit}-{interval // 60}min"


_RUNS: dict[tuple[str, str, int], tuple] = {}


def runs(name: str, unit: str, interval: int, tmp_root: Path):
    """The scenario, GlucoCraft's run and the calculator's run, computed once per test session."""
    key = (name, unit, interval)
    if key not in _RUNS:
        sc = W.scenario(name, unit, interval)
        folder = tmp_root / f"{sc.name}_{unit.replace('/', '')}"
        folder.mkdir(parents=True, exist_ok=True)
        _RUNS[key] = (sc, T.GlucoCraftRun(sc, folder), T.ExpectedRun(sc))
    return _RUNS[key]


@pytest.fixture(scope="session")
def sine_root(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("sine")


# ------------------------------------------------------------------------------------------------ the wave
@pytest.mark.parametrize("unit", T.SOURCE_UNITS)
def test_three_peaks_and_three_valleys_per_day(unit):
    offset, amplitude = W.RANGES["low_to_high"][unit]
    start = datetime(2026, 6, 1, tzinfo=TZ)
    s = W.series(offset, amplitude, unit, 60, start, start + timedelta(days=2))
    # the readings with runs of one value merged (a rounded peak spans several readings): (first index, value)
    runs = [(0, float(s.states[0].state))]
    for i, x in enumerate(s.states):
        if float(x.state) != runs[-1][1]:
            runs.append((i, float(x.state)))
    values = [v for _, v in runs]
    peaks = [runs[k][0] for k in range(1, len(runs) - 1) if values[k - 1] < values[k] > values[k + 1]]
    valleys = [runs[k][0] for k in range(1, len(runs) - 1) if values[k - 1] > values[k] < values[k + 1]]
    values = [float(x.state) for x in s.states]
    assert len(peaks) == len(valleys) == 6
    assert {values[i] for i in peaks} == {offset + amplitude} and {values[i] for i in valleys} == {offset - amplitude}
    # the first peak around a twelfth of a day (02:00), the first valley around a quarter (06:00), local time: the
    # rounded top spans a few minutes on each side
    first_peak = datetime.fromtimestamp(s.states[peaks[0]].measured, TZ)
    first_valley = datetime.fromtimestamp(s.states[valleys[0]].measured, TZ)
    assert abs((first_peak - start).total_seconds() - 2 * 3600) <= 15 * 60
    assert abs((first_valley - start).total_seconds() - 6 * 3600) <= 15 * 60


def test_sine_refuses_what_would_not_be_whole_cycles():
    start = datetime(2026, 6, 1, tzinfo=TZ)
    with pytest.raises(ValueError):
        W.series(100, 10, "mg/dL", 420, start, start + timedelta(days=1))
    with pytest.raises(ValueError):
        W.series(100, -10, "mg/dL", 300, start, start + timedelta(days=1))
    with pytest.raises(ValueError):
        W.scenario("no_such_range", "mg/dL", 300)


@pytest.mark.parametrize("name", list(INSIDE))
def test_each_range_wave_stays_where_it_is_meant_to(name):
    """The "inside one range" waves never leave their range, in either unit (after rounding and conversion)."""
    for unit in T.SOURCE_UNITS:
        sc = W.scenario(name, unit, 300)
        readings = E.readings_from_states(sc.states, sc.unit)
        assert readings and all(r.limit is None for r in readings)
        m = E.metrics([(r.ts, r.ts + 300, r) for r in readings], 300 * len(readings))
        assert m[INSIDE[name]] == 100, (name, unit)


# ----------------------------------------------------------------------------- calculator vs closed form
@pytest.mark.parametrize("name, unit, interval", [pytest.param(*c, id=_id(*c)) for c in CASES])
def test_calculator_agrees_with_the_closed_form(name, unit, interval):
    sc = W.scenario(name, unit, interval)
    store = E.Store.build(E.readings_from_states(sc.states, sc.unit))
    got = E.statistics_sensor(store, sc.now, 14 * 86400)
    offset, amplitude = W.RANGES[name][sc.unit]
    want = E.sine_statistics(offset, amplitude, sc.unit)
    per_limit = 600 * interval / 86400  # percent: one interval per crossing, six per day
    if name in INSIDE:
        assert got[INSIDE[name]] == 100 and want[INSIDE[name]] == 100
    for key in ("very_low", "low", "tir", "titr", "high", "very_high"):
        bound = 0 if name in INSIDE else 2 * per_limit
        assert abs(float(got[key]) - want[key]) <= bound + 1e-9, (key, float(got[key]), want[key], bound)
    for key in ("below_70", "above_180"):
        assert abs(float(got[key]) - want[key]) <= (0 if name in INSIDE else per_limit) + 1e-9, key
    step = 0.5 if sc.unit == common.UNIT_MGDL else 1.5
    if sc.unit == common.UNIT_MGDL:
        assert got["mean"] == Fraction(offset)  # exactly: the readings pair up around the offset
    assert abs(float(got["mean"]) - want["mean"]) <= (0 if sc.unit == common.UNIT_MGDL else 0.6)
    assert abs(float(got["sd"]) - want["sd"]) <= step
    assert got["readings"] == 14 * 86400 // interval and got["active"] == 100


def test_closed_form_values_of_a_crossing_wave():
    """Hand check of expected.sine_statistics: 140 +/- 100 mg/dL is below 69.5 mg/dL for the share
    1/2 + asin(-0.705) / pi of the time."""
    s = E.sine_statistics(140, 100, "mg/dL")
    assert math.isclose(s["below_70"], 100 * (0.5 + math.asin(-0.705) / math.pi))
    assert math.isclose(s["very_low"] + s["low"] + s["tir"] + s["high"] + s["very_high"], 100)
    assert s["very_high"] == 0 and s["mean"] == 140 and math.isclose(s["sd"], 100 / math.sqrt(2))


# --------------------------------------------------------------------------------- GlucoCraft vs calculator
@pytest.mark.parametrize(
    "name, unit, interval, du", [pytest.param(*c, d, id=f"{_id(*c)}-show_{d}") for c in CASES for d in T.DISPLAY_UNITS]
)
def test_glucocraft_agrees_with_the_calculator(name, unit, interval, du, sine_root):
    _, gc, ex = runs(name, unit, interval, sine_root)
    rows = T.compare_store(gc, ex) + T.compare_statistics(gc, ex, du) + T.compare_report(gc, ex, du)
    bad = [(k, a, b) for k, a, b in rows if a != b]
    assert not bad, "\n".join(
        f"{name} [{unit}, {interval} s, {du} display] {k}: GlucoCraft {a!r}, expected {b!r}" for k, a, b in bad
    )
