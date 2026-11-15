# License: AGPL-3.0-only (LICENSE).
"""The realistic generator (tools/synth/generate.py) against the profile it is built from: how often and how long the
lows and highs occur, and how much the time above 180 mg/dL varies between seeds.

Method: generate several seeds, fit a profile again from each series with profile.fit (the same fit as for a real
history), pool the seeds and compare with the profile. Stated tolerances:
  * excursion rate per week, by class (below 70 / 54, above 180 / 250) and by day / night: within 40 % or three
    Poisson standard deviations of the expected count (3 / sqrt(count)), whichever is wider; classes with fewer
    than 10 expected events are not judged (their count is mostly chance);
  * excursion duration (median and 90th percentile, mean over the seeds): within 35 %;
  * time beyond each limit, by day / night: within 40 % (classes judged as above);
  * time above 180 mg/dL of each seed: within 25 % of the profile's (the seeds no longer differ by half).

The default run uses a profile fitted from a generated series of the artificial example profile: it has every
field a fitted profile has (the example profile itself is invented and not self-consistent). With the environment
variable GLUCOCRAFT_SYNTH_PROFILE naming a profile fitted from a real history (python -m tools.synth.profile, kept
outside the repository), the same checks run against it; on a failure only relative differences are shown, never a
value of the profile.
"""

from __future__ import annotations

import importlib
import itertools
import math
import os
import random
import statistics
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
G = importlib.import_module("tools.synth.generate")
P = importlib.import_module("tools.synth.profile")

TZ = "America/Los_Angeles"
START = datetime(2026, 1, 5, tzinfo=ZoneInfo(TZ))
CLASSES = ("below_70", "below_54", "above_180", "above_250")
PARTS = ("day", "night")
RATE_TOL, DURATION_TOL, TIME_TOL, SEED_TOL = 0.40, 0.35, 0.40, 0.25
MIN_EXPECTED = 10


def refit(profile: dict | None, seed: int, days: float, interval: int) -> dict:
    """Generate `days` days and fit a profile from the series again."""
    s = G.generate(profile, start=START, hours=24 * days, interval=interval, seed=seed, tz=TZ)
    rows = [{"state": x.state, "attributes": {"measurement_timestamp": x.measured}} for x in s.states]
    readings, counts = P.parse_states(rows)
    return P.fit(readings, TZ, counts=counts)


def roundtrip_profile() -> dict:
    """A profile fitted from 200 days generated from the example profile (marked artificial)."""
    return refit(None, 999, 200, 300) | {"artificial": True}


def compare(profile: dict, fits: list[dict], days: float) -> list[str]:
    """The characteristics outside their tolerance, as relative differences only."""
    out = []

    def rel(a: float, b: float) -> float:
        return (b - a) / a

    for key in CLASSES:
        info = profile["excursions"][key]
        judged = False
        for part in PARTS:
            want = info[part]["per_week"]
            expected_n = want * days / 7 * len(fits)
            if expected_n < MIN_EXPECTED:
                continue
            judged = True
            got = statistics.mean(f["excursions"][key][part]["per_week"] for f in fits)
            tol = max(RATE_TOL, 3 / math.sqrt(expected_n))
            if abs(rel(want, got)) > tol:
                out.append(f"{key} {part} events per week {rel(want, got):+.0%} (tolerance {tol:.0%})")
            t_want = info[part].get("time_pct")
            if t_want:
                t_got = statistics.mean(f["excursions"][key][part]["time_pct"] for f in fits)
                if abs(rel(t_want, t_got)) > max(TIME_TOL, tol):
                    out.append(f"{key} {part} time {rel(t_want, t_got):+.0%} (tolerance {max(TIME_TOL, tol):.0%})")
        if not judged:
            continue
        for q in ("p50", "p90"):
            want = info["duration_min"].get(q)
            got = [f["excursions"][key]["duration_min"][q] for f in fits if f["excursions"][key]["duration_min"][q]]
            if want and got and abs(rel(want, statistics.mean(got))) > DURATION_TOL:
                out.append(f"{key} duration {q} {rel(want, statistics.mean(got)):+.0%} (tolerance {DURATION_TOL:.0%})")
    want = profile["summary"]["above_180_pct"]
    for k, f in enumerate(fits):
        d = rel(want, f["summary"]["above_180_pct"])
        if abs(d) > SEED_TOL:
            out.append(f"time above 180 of seed {k + 1} {d:+.0%} (tolerance {SEED_TOL:.0%})")
    return out


def test_excursions_follow_a_fitted_profile():
    profile = roundtrip_profile()
    days = 120
    fits = [refit(profile, seed, days, 300) for seed in (1, 2, 3)]
    bad = compare(profile, fits, days)
    assert not bad, "\n".join(bad)


LOCAL = os.environ.get("GLUCOCRAFT_SYNTH_PROFILE", "")
LOCAL_SKIP = (
    (
        "optional: set GLUCOCRAFT_SYNTH_PROFILE to a profile fitted from a real history (python -m "
        "tools.synth.profile, outside the repository)"
    )
    if not LOCAL
    else (f"GLUCOCRAFT_SYNTH_PROFILE names a file that does not exist: {LOCAL}" if not Path(LOCAL).is_file() else "")
)


@pytest.mark.slow
@pytest.mark.skipif(bool(LOCAL_SKIP), reason=LOCAL_SKIP or "available")
def test_excursions_follow_the_local_fitted_profile():
    profile = P.load_profile(LOCAL)
    interval = min(G.INTERVALS, key=lambda x: abs(x - int(profile["intervals"]["median_s"])))
    days = 200
    fits = [refit(profile, seed, days, interval) for seed in (1, 2, 3, 4)]
    bad = compare(profile, fits, days)
    assert not bad, "\n".join(bad)


# ------------------------------------------------------------------------------------- the generator's parts
def test_body_process_matches_the_profiles_autocorrelation():
    """Without longer lags the body is the AR(1) process; with them, its 5-minute coefficient stays exact and the
    autocorrelation at the longer lags comes closer to the profile's than an AR(1) process with the same coefficient."""
    assert G.ar_roots({"ar1_phi": 0.96}) == (0.0, 0.96)
    acf = {"5min": 0.96, "30min": 0.72, "60min": 0.45, "120min": 0.18, "240min": 0.03}
    r1, r2 = G.ar_roots({"ar1_phi": 0.96, "acf_z": acf})
    assert math.isclose((r1 + r2) / (1 + r1 * r2), 0.96)
    fitted = sum((G.ar2_acf(r1, r2, int(k[:-3]) // 5) - v) ** 2 for k, v in acf.items() if k != "5min")
    single = sum((0.96 ** (int(k[:-3]) // 5) - v) ** 2 for k, v in acf.items() if k != "5min")
    assert fitted < single / 4


def test_knots_have_unit_variance_and_the_lag_one_correlation():
    knots = G._Knots(random.Random(5), (0.5, 0.88), 0)
    z = [knots._knot(k) for k in range(60000)]
    m = statistics.fmean(z)
    var = statistics.fmean((x - m) ** 2 for x in z)
    lag1 = statistics.fmean((a - m) * (b - m) for a, b in itertools.pairwise(z)) / var
    assert abs(var - 1) < 0.05 and abs(lag1 - (0.5 + 0.88) / (1 + 0.5 * 0.88)) < 0.01


@pytest.mark.parametrize(
    "base, extreme, limit, minutes",
    [(110, 48, 54, 25), (65, 45, 54, 20), (150, 290, 250, 60), (100, 60, 70, 15), (120, 52, 54, 40)],
)
def test_an_excursion_is_beyond_its_limit_for_the_drawn_minutes(base, extreme, limit, minutes):
    """The plateau is shortened by the ramps' time beyond the limit (on a flat body)."""
    ev = G._Event.shaped(0.0, minutes, extreme, base, limit)
    values = [base + (extreme - base) * ev.weight(s) for s in range(int(ev.end) + 1)]
    beyond = sum(1 for v in values if (v < limit if extreme < limit else v > limit))
    assert abs(beyond / 60 - minutes) < 0.5
    assert ev.ramp >= G.RAMP_MIN_SHORT


def test_body_stays_inside_the_very_low_and_very_high_limits():
    """The body bends below 62 / above 240 mg/dL and never goes beyond 58 / 246: those excursions are injected."""
    model = G._Model(G.example_profile(), ZoneInfo(TZ), G._modifiers(()))
    model.high_knee = G.BODY_HIGH_KNEE
    values = [model.value(1_767_600_000 + 900 * k, z)[0] for k in range(96) for z in (-12, -6, -3, 3, 6, 12)]
    assert min(values) >= G.BODY_LOW_KNEE - G.BODY_BEND[0] and max(values) <= G.BODY_HIGH_KNEE + G.BODY_BEND[1]
    # and a profile whose own percentiles lie beyond 250 mg/dL keeps them
    assert G._Model(G.example_profile(), ZoneInfo(TZ), G._modifiers(())).high_knee > 250


def test_calibration_does_not_depend_on_the_seed():
    a = G.generate(None, start=START, hours=24, interval=300, seed=1)
    b = G.generate(None, start=START, hours=24, interval=300, seed=2)
    model = G._Model(G.example_profile(), ZoneInfo(TZ), G._modifiers(()))
    assert a.states != b.states
    assert G._calibrate(model, int(START.timestamp()), 300, 1.0) is G._calibrate(
        model, int(START.timestamp()), 300, 1.0
    )


def test_excursions_last_at_least_15_minutes_plus_one_interval():
    """An excursion of exactly 15 minutes would be measured one reading short of 15 minutes and not counted."""
    for interval in G.INTERVALS:
        lo, hi = G._duration_bounds({"p50": 20, "p90": 40}, 15 + interval / 60)
        assert lo == 15 + interval / 60 and hi == 80
        assert G._from_pcts({"p10": 15, "p50": 20, "p90": 40}, 0.0, lo, hi) == lo
