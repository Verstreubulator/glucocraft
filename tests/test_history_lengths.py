# License: AGPL-3.0-only (LICENSE).
"""The recorder history GlucoCraft finds at its setup, with its length as a test parameter.

Home Assistant keeps 10 days of history by default, but a recorder can keep any number of days, so GlucoCraft's
history import must take what exists up to its 200-day retention, never assume 10 days, and leave the oldest stored
day complete. The histories (tools/synth/scenarios.history): none, 1 day, 10 days, 30 days, six months, more than six
months; each without gaps from a midnight, with gaps (dropouts and a two-day gap), and with a partial first day; each
with a mg/dL and a mmol/L source.

Each history goes through GlucoCraft's real import path, as in tests/test_synth_scenarios.py: the recorded states,
read for exactly the local days the retention keeps (history_import.async_rows_kept), parsed as live readings
(rows_from_states) and stored (storage.merge_history); then the statistics sensors and the CGM Report are compared
with the independent calculator (tools/synth/expected.py), every value, in both display units.

Two optional sources, each skipped with the reason when it is not available:
  * a real Home Assistant recorder (tests/test_history_lengths_recorder.py);
  * the real history of GLUCOCRAFT_REAL_HISTORY (tools/synth/real_history.py), cut to each length (below).
"""

from __future__ import annotations

import importlib
import itertools
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import test_synth_scenarios as T
from test_synth_sub_minute_times import known_finding

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
S = importlib.import_module("tools.synth.scenarios")
E = importlib.import_module("tools.synth.expected")
common = importlib.import_module("tools.synth.common")

CASES = [(n, v, u) for n, v in S.histories() for u in T.SOURCE_UNITS]
RETENTION_DAYS = 200


def case_id(length: str, variant: str, unit: str) -> str:
    return f"{length}-{variant}-{unit}"


def _marks(length: str) -> list:
    return [pytest.mark.slow] if S.HISTORY_LENGTHS[length] >= 182 else []


_RUNS: dict[tuple[str, str, str], tuple] = {}


def runs(length: str, variant: str, unit: str, tmp_root: Path):
    """The history, GlucoCraft's run and the calculator's run, computed once per test session."""
    key = (length, variant, unit)
    if key not in _RUNS:
        sc = S.history(length, variant, unit)
        folder = tmp_root / f"{sc.name}_{unit.replace('/', '')}"
        folder.mkdir(parents=True, exist_ok=True)
        _RUNS[key] = (sc, T.GlucoCraftRun(sc, folder), T.ExpectedRun(sc))
    return _RUNS[key]


@pytest.fixture(scope="session")
def history_root(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("histories")


def _measured(sc) -> list[int]:
    """The measurement times of the readings in the history (LO / HI included), as the calculator reads them."""
    return [r.ts for r in E.readings_from_states(sc.states, sc.unit, sc.limits)]


def _local_midnight(ts: int, tz: str) -> bool:
    d = datetime.fromtimestamp(ts, ZoneInfo(tz))
    return (d.hour, d.minute, d.second) == (0, 0, 0)


# ------------------------------------------------------------------------------------------ the histories
def test_every_length_and_variant_is_built_as_described():
    for length, variant, unit in CASES:
        sc = S.history(length, variant, unit)
        days = S.HISTORY_LENGTHS[length]
        measured = _measured(sc)
        if not days:
            assert sc.states == []
            continue
        first = datetime.fromtimestamp(measured[0], ZoneInfo(sc.tz))
        start = datetime.fromtimestamp(sc.now, ZoneInfo(sc.tz)) - timedelta(days=days)
        assert first.date() == start.date(), case_id(length, variant, unit)
        if variant == "partial_first_day":
            assert (first.hour, first.minute) == (13, 20)
        else:
            assert (first.hour, first.minute) == (0, 0)
        if variant == "whole_days":
            assert all(b - a == 300 for a, b in itertools.pairwise(measured))
        if variant == "with_gaps" and days >= 3:
            assert max(b - a for a, b in itertools.pairwise(measured)) >= 2 * 86400


# ----------------------------------------------------------------------------------------- the import
@pytest.mark.parametrize("length, variant, unit", [pytest.param(*c, marks=_marks(c[0]), id=case_id(*c)) for c in CASES])
def test_import_takes_what_exists_up_to_the_retention(length, variant, unit, history_root):
    """Never 10 days by assumption: every reading of a history up to 200 days is stored; of a longer history, the
    whole local days from the retention's first midnight, the oldest of them complete."""
    sc, gc, ex = runs(length, variant, unit, history_root)
    measured = _measured(sc)
    stored = [r.timestamp for r in gc.readings]
    expected = [r.ts for r in ex.store.readings]
    assert stored == expected
    if not measured:
        assert stored == []
        return
    days = S.HISTORY_LENGTHS[length]
    assert _local_midnight(gc.cutoff, sc.tz)
    if days <= RETENTION_DAYS:
        assert stored[0] == measured[0]  # the oldest recorded reading is kept
        assert len(stored) == len([t for t in measured if t >= gc.cutoff])
    else:
        assert stored[0] >= gc.cutoff > measured[0]
        if variant == "whole_days":
            assert stored[0] == gc.cutoff and stored[1] - stored[0] == 300  # the oldest stored day is complete
        # the retention keeps 200 local days before the day of the newest reading (a 23- or 25-hour day included)
        zone = ZoneInfo(sc.tz)
        newest = datetime.fromtimestamp(min(sc.now, measured[-1]), zone).date()
        assert (newest - datetime.fromtimestamp(gc.cutoff, zone).date()).days == RETENTION_DAYS
    if days > 10:
        assert stored[-1] - stored[0] > 10 * 86400  # more than the recorder's default


@pytest.mark.parametrize(
    "length, variant, unit, du",
    [pytest.param(*c, d, marks=_marks(c[0]), id=f"{case_id(*c)}-show_{d}") for c in CASES for d in T.DISPLAY_UNITS],
)
def test_statistics_and_report_agree_with_the_calculator(length, variant, unit, du, history_root):
    _, gc, ex = runs(length, variant, unit, history_root)
    rows = T.compare_store(gc, ex) + T.compare_statistics(gc, ex, du) + T.compare_report(gc, ex, du)
    bad = [(k, a, b) for k, a, b in rows if a != b]
    assert not bad, "\n".join(
        f"{case_id(length, variant, unit)} [{du} display] {k}: GlucoCraft {a!r}, expected {b!r}" for k, a, b in bad
    )


# ------------------------------------------------------------------------------- the real history (optional)
REAL = os.environ.get("GLUCOCRAFT_REAL_HISTORY", "")
REAL_SKIP = (
    (
        "the real-data tests are optional: set GLUCOCRAFT_REAL_HISTORY to a history file written by "
        "python -m tools.synth.real_history (outside the repository)"
    )
    if not REAL
    else (f"GLUCOCRAFT_REAL_HISTORY names a file that does not exist: {REAL}" if not Path(REAL).is_file() else "")
)
REAL_LENGTHS = (1, 10, 30, 182, 240)


@pytest.mark.skipif(bool(REAL_SKIP), reason=REAL_SKIP or "available")
@pytest.mark.parametrize("days", REAL_LENGTHS, ids=[f"{d}_days" for d in REAL_LENGTHS])
def test_real_history_cut_to_each_length(days, tmp_path):
    """The real history as a recorder that keeps `days` days: the import and every compared value agree. On failure
    only the names of the values that differ are shown, never a value (personal health information)."""
    series = common.Series.load(REAL)
    sc = S.from_series(series, f"real_{days}_days", days=days, report_days=180 if days >= 182 else 14)
    gc, ex = T.GlucoCraftRun(sc, tmp_path), T.ExpectedRun(sc)
    keys = []
    for du in T.DISPLAY_UNITS:
        rows = T.compare_store(gc, ex) + T.compare_statistics(gc, ex, du) + T.compare_report(gc, ex, du)
        keys += [f"{k} [{du} display]" for k, a, b in rows if a != b and not known_finding(k)]
    assert not keys, f"{len(keys)} values differ (values not shown): " + ", ".join(keys)
