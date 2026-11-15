# License: AGPL-3.0-only (LICENSE).
"""The synthetic-data generator and the profile fit (tools/synth/generate.py, profile.py, example_profile.py).

Rules under test: the same profile, seed and options give the same series; the source unit and its precision
(mg/dL whole numbers, mmol/L one decimal); the reading intervals 1 / 5 / 15 minutes; lengths from hours to more than
200 days; a time-zone-aware start; LO / HI as texts or Dexcom codes; the modifiers; the profile fit recovers the
characteristics it was generated from; a fitted profile is never written into the repository.
"""

from __future__ import annotations

import importlib
import itertools
import json
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
X = importlib.import_module("tools.synth.example_profile")
common = importlib.import_module("tools.synth.common")

LA = ZoneInfo("America/Los_Angeles")
START = datetime(2026, 3, 1, tzinfo=LA)


def gen(**kw):
    kw.setdefault("start", START)
    kw.setdefault("hours", 48)
    return G.generate(None, **kw)


def numbers(series) -> list[float]:
    out = []
    for s in series.states:
        try:
            out.append(float(s.state))
        except ValueError:
            pass
    return out


def test_same_seed_same_series_and_different_seed_differs():
    a, b, c = gen(seed=11), gen(seed=11), gen(seed=12)
    assert [(s.recorded, s.measured, s.state) for s in a.states] == [
        (s.recorded, s.measured, s.state) for s in b.states
    ]
    assert [s.state for s in a.states] != [s.state for s in c.states]


def test_series_round_trip_json(tmp_path):
    a = gen(seed=3, hours=6)
    a.save(tmp_path / "s.json")
    b = common.Series.load(tmp_path / "s.json")
    assert b.to_json() == a.to_json() and b.to_json()["artificial"] is True


@pytest.mark.parametrize("unit, decimals", [("mg/dL", 0), ("mmol/L", 1)])
def test_units_and_precision(unit, decimals):
    s = gen(seed=5, unit=unit, hours=72)
    texts = [x.state for x in s.states if x.state.replace(".", "", 1).isdigit()]
    assert texts and all((len(t.split(".")[1]) if "." in t else 0) == decimals for t in texts)
    assert s.unit == unit


def test_mmol_series_is_the_mgdl_series_converted():
    """The same seed in both units: every mmol/L value is the mg/dL value's conversion to one decimal (within the
    rounding of the whole-number mg/dL value)."""
    mg, mm = gen(seed=8, hours=24, aligned=True), gen(seed=8, hours=24, aligned=True, unit="mmol/L")
    pairs = [
        (float(a.state), float(b.state))
        for a, b in zip(mg.states, mm.states, strict=False)
        if a.state.isdigit() and b.state.replace(".", "", 1).isdigit()
    ]
    assert len(pairs) > 1000
    assert all(abs(a / common.MGDL_PER_MMOLL - b) <= 0.08 for a, b in pairs)


@pytest.mark.parametrize("interval", [60, 300, 900])
def test_reading_intervals(interval):
    s = gen(seed=2, interval=interval, hours=48, aligned=True, modifiers=["no_gaps"])
    t = [x.measured for x in s.states if x.measured is not None]
    assert statistics.median(b - a for a, b in itertools.pairwise(t)) == interval
    assert all((x - t[0]) % interval == 0 for x in t)


def test_length_over_200_days_and_hours():
    long = G.generate(None, start=START, hours=24 * 205, interval=900, seed=1)
    t = [x.measured for x in long.states if x.measured]
    assert t[-1] - t[0] > 200 * 86400
    short = gen(hours=3, seed=1, modifiers=["no_gaps"])
    assert 150 <= len(short.states) <= 180


def test_start_must_be_time_zone_aware_and_interval_supported():
    with pytest.raises(ValueError):
        G.generate(None, start=datetime(2026, 3, 1), hours=1)
    with pytest.raises(ValueError):
        gen(interval=120)
    with pytest.raises(ValueError):
        gen(modifiers=["no_such_modifier"])


def test_local_time_of_day_follows_the_profile():
    """The example profile's breakfast peak: readings near 09:00 are higher than near 03:00 (local time, also across
    the daylight-saving change on March 8). 90 days: over three weeks the slow day-to-day swings alone can hide half
    of the difference."""
    s = G.generate(None, start=START, hours=24 * 90, interval=300, seed=4, modifiers=["no_gaps"])
    by_hour = {3: [], 9: []}
    for x in s.states:
        if x.measured and x.state.isdigit():
            h = datetime.fromtimestamp(x.measured, LA).hour
            if h in by_hour:
                by_hour[h].append(float(x.state))
    assert statistics.mean(by_hour[9]) > statistics.mean(by_hour[3]) + 20


def test_limits_texts_and_dexcom_codes():
    texts = {
        x.state for x in G.generate(None, start=START, hours=24 * 28, seed=9, modifiers=["limits", "hypo_prone"]).states
    }
    assert "LO" in texts
    codes = {
        x.state
        for x in G.generate(
            None, start=START, hours=24 * 28, seed=9, modifiers=["limits", "hypo_prone", "dexcom_codes"]
        ).states
    }
    assert "39" in codes and "LO" not in codes


def test_modifiers_change_the_series():
    base = numbers(gen(seed=6, hours=24 * 14))
    hyper = numbers(gen(seed=6, hours=24 * 14, modifiers=["hyper_prone"]))
    assert statistics.mean(hyper) > statistics.mean(base) + 15
    dropouts = gen(seed=6, hours=24 * 14, modifiers=["dropouts"]).meta["counts"]["gaps"]
    assert dropouts > gen(seed=6, hours=24 * 14).meta["counts"]["gaps"]
    assert gen(seed=6, hours=24 * 14, modifiers=["no_gaps"]).meta["counts"]["gaps"] == 0


def test_fit_recovers_the_characteristics():
    """A profile fitted from a generated series is close to the profile it was generated from."""
    s = G.generate(None, start=START, hours=24 * 42, seed=21)
    rows = [{"state": x.state, "attributes": {"measurement_timestamp": x.measured}} for x in s.states]
    readings, counts = P.parse_states(rows)
    fitted = P.fit(readings, counts=counts)
    ex = X.example_profile()
    assert fitted["intervals"]["median_s"] == 60
    assert 0.9 < fitted["variability"]["ar1_phi"] < 0.99
    p50 = [abs(a - b) for a, b in zip(fitted["time_of_day"]["p50"], ex["time_of_day"]["p50"], strict=False)]
    assert statistics.median(p50) < 15
    assert fitted["artificial"] is False and fitted["gaps"]["count"] > 0
    assert set(fitted) >= {"time_of_day", "rate_of_change", "noise", "excursions", "intervals", "gaps", "limits"}


def test_z_mapping_round_trip():
    q = [60, 100, 130, 170, 240]
    for z in (-3.0, -1.645, -0.3, 0.0, 0.7, 1.645, 2.5):
        assert abs(P.value_to_z(P.z_to_value(z, q), q) - z) < 1e-9
    assert P.z_to_value(0.0, q) == 130


def test_example_profile_is_marked_artificial():
    ex = X.example_profile()
    assert ex["artificial"] is True and "Artificial" in ex["description"]
    assert len(ex["time_of_day"]["p50"]) == P.BINS


def test_fitted_profile_never_written_into_the_repository(tmp_path):
    fitted = {"kind": P.PROFILE_KIND, "artificial": False}
    with pytest.raises(SystemExit):
        P.save_profile(fitted, REPO / "tools" / "synth" / "fitted.json")
    assert not (REPO / "tools" / "synth" / "fitted.json").exists()
    out = P.save_profile(fitted, tmp_path / "profile.json")
    assert json.loads(out.read_text())["kind"] == P.PROFILE_KIND


def test_no_fitted_profile_in_the_repository():
    """Only artificial profiles may be in the repository."""
    for path in REPO.rglob("*.json"):
        if ".git" in path.parts or "node_modules" in path.parts:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("kind") == P.PROFILE_KIND:
            assert data.get("artificial") is True, path
