# License: AGPL-3.0-only (LICENSE).
"""Measurement times off the whole minute (tools/synth/scenarios.sub_minute_times), as a real CGM records them.

Every other scenario measures on the whole minute; the real history (tests/test_synth_real_history.py) does not. The
CGM Report, the statistics sensors and the independent calculator weigh each reading by its seconds (the CGM Report's
minute grid only places values in the display bins of the AGP curve and the daily profiles):

  * Low events follow the readings' own times: an event starts with its first reading below the limit, at that
    reading's measurement time, and lasts the seconds its readings represent (docs/SYSTEM_DESIGN.md, hypoglycemia
    events). A first reading at hh:mm:01 whose interval ends exactly at the next whole minute covers no minute of the
    grid and still starts the event; the scenario's lows of 14, 15, 16 and 40 minutes check it (EVENT_START_KEYS must
    agree).
  * The report's percentages, mean, SD, CV, GMI, GRI, days with data and the night and day shares weigh each reading
    by the seconds it represents in the period, as the statistics sensors do: every value agrees with the independent
    calculator, for both source units and in both display units.
  * A known finding would be a strict expected failure (KNOWN, REASONS, EXPECTED_FAILURES; none at present), so that
    the run fails once it is fixed and the marker is removed.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

import test_synth_scenarios as T

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
S = importlib.import_module("tools.synth.scenarios")

# The comparison keys of the low events, and those of each known finding (a pattern on the comparison key; also used
# by the real-history tests). None at present: every value agrees.
EVENT_START_KEYS = re.compile(r"^lows\.(events|events_54|events_night|mean_duration_min|longest_min|list|lowest)")
KNOWN: dict[str, re.Pattern] = {}
REASONS: dict[str, str] = {}
EXPECTED_FAILURES: set[tuple[str, str]] = set()  # (finding, source unit)


def known_finding(key: str) -> str | None:
    """The known finding a disagreeing comparison key belongs to, or None."""
    return next((f for f, pattern in KNOWN.items() if pattern.search(key)), None)


_RUNS: dict[str, tuple] = {}


def disagreements(unit: str, du: str, tmp_root: Path) -> list[tuple[str, object, object]]:
    if unit not in _RUNS:
        sc = S.sub_minute_times(unit)
        folder = tmp_root / f"sub_minute_{unit.replace('/', '')}"
        folder.mkdir(parents=True, exist_ok=True)
        _RUNS[unit] = (T.GlucoCraftRun(sc, folder), T.ExpectedRun(sc))
    gc, ex = _RUNS[unit]
    rows = T.compare_store(gc, ex) + T.compare_statistics(gc, ex, du) + T.compare_report(gc, ex, du)
    return [(k, a, b) for k, a, b in rows if a != b]


@pytest.fixture(scope="session")
def sub_root(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("sub_minute")


def test_measurement_times_are_off_the_whole_minute():
    sc = S.sub_minute_times("mg/dL")
    seconds = {s.measured % 60 for s in sc.states}
    assert seconds >= {0, 1, 2, 30, 31, 45, 58, 59}
    assert max(s.recorded for s in sc.states) < sc.now


@pytest.mark.parametrize("unit, du", [(u, d) for u in T.SOURCE_UNITS for d in T.DISPLAY_UNITS])
def test_low_events_start_at_their_first_reading(unit, du, sub_root):
    """Every low event starts at its first reading below the limit, also when that reading covers no whole minute of
    the grid, and lasts the seconds its readings represent (Danne et al., 2017; docs/SYSTEM_DESIGN.md)."""
    rows = [(k, a, b) for k, a, b in disagreements(unit, du, sub_root) if EVENT_START_KEYS.search(k)]
    assert not rows, "\n".join(f"[{unit}, {du} display] {k}: GlucoCraft {a!r}, expected {b!r}" for k, a, b in rows)


@pytest.mark.parametrize("unit, du", [(u, d) for u in T.SOURCE_UNITS for d in T.DISPLAY_UNITS])
def test_every_value_outside_the_known_findings_agrees(unit, du, sub_root):
    bad = [(k, a, b) for k, a, b in disagreements(unit, du, sub_root) if not known_finding(k)]
    assert not bad, "\n".join(f"[{unit}, {du} display] {k}: GlucoCraft {a!r}, expected {b!r}" for k, a, b in bad)


@pytest.mark.parametrize(
    "finding, unit, du",
    [
        pytest.param(f, u, d, marks=pytest.mark.xfail(strict=True, reason=REASONS[f]), id=f"{f}-{u}-show_{d}")
        for f, u in sorted(EXPECTED_FAILURES)
        for d in T.DISPLAY_UNITS
    ],
)
def test_known_finding(finding, unit, du, sub_root):
    bad = [(k, a, b) for k, a, b in disagreements(unit, du, sub_root) if known_finding(k) == finding]
    assert not bad, "\n".join(
        f"{finding} [{unit}, {du} display] {k}: GlucoCraft {a!r}, expected {b!r}" for k, a, b in bad
    )


@pytest.mark.parametrize("unit, du", [(u, d) for u in T.SOURCE_UNITS for d in T.DISPLAY_UNITS])
def test_no_finding_appears_where_none_is_expected(unit, du, sub_root):
    """A finding that newly appears in a case is a change to look at."""
    found = {known_finding(k) for k, _, _ in disagreements(unit, du, sub_root)} - {None}
    assert found <= {f for f, u in EXPECTED_FAILURES if u == unit}, found
