# License: AGPL-3.0-only (LICENSE).
"""Every edge-case scenario (tools/synth/scenarios.py) through GlucoCraft's real code, compared with an independent
calculation (tools/synth/expected.py), for a mg/dL and a mmol/L source and in both display units.

GlucoCraft's side runs the code paths of a real installation:
  * the recorded states -> history_import.rows_from_states (limits.classify: units, LO / HI, no-reading states, one
    reading per measurement time) -> storage.merge_history and detect_interval (history_import._store_rows); for the
    second-source scenario the live path instead: coordinator._source_reading and _process_reading per state, in
    the order the states arrive;
  * the statistics sensors: statistics.compute_period_stats per period with the store's weights (gaps.weights /
    window_weights), as coordinator._compute_all_stats, and the GMI rule statistics.gmi_shown;
  * the CGM Report: report.build_report (with the research appendix), and the weekly trend for the 6-month report;
  * the display: units.fmt, the rule every surface uses for a glucose value (the PDF formats the report's values
    with it).

Each comparison is (scenario, source unit, display unit, value). A disagreement fails with both values. Known
disagreements (FINDINGS) are strict expected failures: they are reported, not fixed here, and the test fails once
GlucoCraft changes so that the values agree (then remove the entry).
"""

from __future__ import annotations

import asyncio
import bisect
import importlib
import re
import sys
import types
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from glucocraft import coordinator as co
from glucocraft import history_import as hi
from glucocraft import report as R
from glucocraft import statistics as st
from glucocraft import units as U
from glucocraft.const import AGP_RANGES, DEFAULT_RETENTION_DAYS
from glucocraft.gaps import weights as gap_weights
from glucocraft.gaps import window_weights
from glucocraft.storage import GlucoCraftStorage

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
E = importlib.import_module("tools.synth.expected")
S = importlib.import_module("tools.synth.scenarios")
common = importlib.import_module("tools.synth.common")
rhu = common.round_half_up

SOURCE_UNITS = (common.UNIT_MGDL, common.UNIT_MMOLL)
DISPLAY_UNITS = (U.UNIT_MGDL, U.UNIT_MMOLL)
PERIODS = (("24h", 86400), ("7d", 7 * 86400), ("14d", 14 * 86400))
LONG_PERIODS = (("90d", 90 * 86400), ("180d", 180 * 86400))


# Known disagreements between GlucoCraft and the definitions, reported and not fixed here. Each names the values it
# covers (a pattern on the comparison key), where it occurs, the case its strict expected failure checks, and which
# side is right.
@dataclass(frozen=True)
class Finding:
    """A known disagreement, expected to fail strictly until it is fixed."""

    id: str
    reason: str
    keys: str  # regular expression on the comparison key
    scenarios: tuple[str, ...] | None = None  # None: any scenario
    units: tuple[str, ...] | None = None  # source units; None: both
    cases: tuple[tuple[str, str, str], ...] = ()

    def covers(self, name: str, unit: str, key: str) -> bool:
        """True when the finding applies to this scenario, unit and comparison key."""
        return (
            (self.scenarios is None or name in self.scenarios)
            and (self.units is None or unit in self.units)
            and re.search(self.keys, key) is not None
        )


FINDINGS: tuple[Finding, ...] = ()  # none at present: every scenario agrees with the definitions


# ------------------------------------------------------------------------------------------- GlucoCraft's side
def _state(s, unit: str):
    """A recorded state as Home Assistant's history returns it."""
    attrs = {"unit_of_measurement": unit}
    if s.measured is not None:
        attrs["measurement_timestamp"] = s.measured
    t = datetime.fromtimestamp(s.recorded, UTC)
    return types.SimpleNamespace(state=s.state, attributes=attrs, last_updated=t, last_changed=t)


def _coordinator(storage: GlucoCraftStorage, secondary: str | None):
    """The real coordinator with only the attributes the reading path uses (as tests/test_data_path.py does)."""
    c = co.GlucoCraftCoordinator.__new__(co.GlucoCraftCoordinator)
    c.__dict__.update(
        _glucose_entity="sensor.primary",
        _secondary_entity=secondary,
        _src_last_ts={},
        _src_times={},
        _src_last_value={},
        _older_logged=set(),
        _refusals_logged=set(),
        sensor_warming_up=False,
        warmup_started_at=0,
        last_reading_ts=0,
        storage=storage,
        detected_interval_seconds=300,
        active_source_entity="sensor.primary",
        second_source_active=False,
        active_source="P",
        current_glucose=None,
        no_current_data=True,
        _stale_minutes=15,
        _time_attr={"sensor.primary": "", **({secondary: ""} if secondary else {})},
        _unitless_low={},
        _settings={},
        _config_unit="mg/dL",
        entry_id="synth",
        _expiry_entity=None,
        _src_domain={},
        _src_device={},
        _learned_limits={},
        _learned_device={},
        _mmol_high_run={},
        _mgdl_low_run={},
        _no_time_run={},
        _replacing_run=None,
        _cap_findings={},
        _unit_issue_open=set(),
        _not_glucose_open=set(),
        _catch_up_after=None,
    )
    c.hass = types.SimpleNamespace(
        states=types.SimpleNamespace(get=lambda e: None), config=types.SimpleNamespace(country="US", language="en")
    )
    c._schedule_state_save = lambda: None
    return c


def _live(sc, storage: GlucoCraftStorage) -> None:
    """Feed both sources' states to the coordinator in arrival order, with the clock at each arrival."""
    c = _coordinator(storage, "sensor.secondary")
    arrivals = sorted(
        [(s.recorded, 0, s) for s in sc.states] + [(s.recorded, 1, s) for s in sc.secondary], key=lambda x: (x[0], x[1])
    )
    for recorded, src, s in arrivals:
        entity = "sensor.secondary" if src else "sensor.primary"
        with mock.patch.object(co.time, "time", return_value=float(recorded)):
            got = c._source_reading(entity, _state(s, sc.unit))
            if got is not None and s.measured is not None:
                c._process_reading(got[0], s.measured, entity, entity, True, got[1])
    storage.detected_interval = c.detected_interval_seconds


class GlucoCraftRun:
    """GlucoCraft's results for one scenario."""

    def __init__(self, sc, folder: Path):
        self.sc = sc
        storage = GlucoCraftStorage(str(folder), "synth")
        storage.load()
        if sc.secondary:
            _live(sc, storage)
        else:
            # the history import reads the recorder for exactly the local days the retention keeps
            # (history_import.async_rows_kept, as async_import_history calls it); the recorder is the scenario's states
            async def fetch(start, end):
                return [_state(s, sc.unit) for s in sc.states if start <= s.recorded <= end]

            def to_rows(states, newest):
                return hi.rows_from_states(
                    states, sc.unit == common.UNIT_MMOLL, True, sc.limits, now=sc.now, after=newest
                )

            rows = asyncio.run(hi.async_rows_kept(fetch, to_rows, sc.now, ZoneInfo(sc.tz), DEFAULT_RETENTION_DAYS))
            hi._store_rows(storage, rows)
        self.imported_first = storage.readings[0].timestamp if storage.readings else None
        c = _coordinator(storage, None)
        self.cutoff = c.retention_cutoff(sc.now)
        storage.purge_before(self.cutoff)
        self.storage = storage
        self.readings = storage.readings
        self.detected = storage.detected_interval
        self.iv = storage.reading_intervals(self.detected)
        ts = [r.timestamp for r in self.readings]
        sp = gap_weights(ts, self.iv)
        self.stats = {}
        periods = PERIODS + (LONG_PERIODS if sc.report_days > 14 else ())
        for label, seconds in periods:
            i = bisect.bisect_left(ts, sc.now - seconds)
            self.stats[label] = st.compute_period_stats(
                self.readings[i:],
                label,
                seconds,
                self.detected,
                **AGP_RANGES,
                weights=window_weights(sp, self.iv, i, len(self.readings)),
            )
        self.report = R.build_report(
            self.readings,
            self.detected,
            ZoneInfo(sc.tz),
            sc.report_days,
            False,
            [],
            now=sc.now,
            intervals=self.iv,
            research=True,
        )


class ExpectedRun:
    """The independent calculator's results for one scenario."""

    def __init__(self, sc):
        tz = ZoneInfo(sc.tz)
        if sc.secondary:

            def arrivals(states):
                out = []
                for s in states:
                    kind, mg = E.classify(s.state, sc.unit, **_limit_args(sc.limits))
                    if kind != "none" and s.measured is not None:
                        out.append(
                            (s.recorded, s.measured, E.Reading(s.measured, mg, kind if kind != "reading" else None))
                        )
                return out

            readings = E.merge_sources(arrivals(sc.states), arrivals(sc.secondary))
        else:
            readings = E.readings_from_states(sc.states, sc.unit, sc.limits)
        store = E.Store.build(readings)
        self.cutoff = E.retention_cutoff(store, tz, sc.now)
        self.store = E.Store.build([r for r in readings if r.ts >= self.cutoff])
        periods = PERIODS + (LONG_PERIODS if sc.report_days > 14 else ())
        self.stats = {label: E.statistics_sensor(self.store, sc.now, seconds) for label, seconds in periods}
        self.report = E.report(self.store, tz, sc.now, sc.report_days)
        self.weekly = E.weekly(self.store, tz, sc.now) if sc.report_days >= 90 else None


def _limit_args(limits: dict) -> dict:
    return {
        "lower": limits.get("lower", 40),
        "upper": limits.get("upper", 500),
        "dexcom": limits.get("dexcom_type", False),
        "nightscout": limits.get("nightscout", False),
        "lower_set": limits.get("lower_setting", "auto") != "auto",
        "upper_set": limits.get("upper_setting", "auto") != "auto",
    }


_CACHE: dict[tuple[str, str], tuple] = {}


def runs(name: str, unit: str, tmp_root: Path):
    """Both sides for (scenario, source unit), computed once per test session."""
    key = (name, unit)
    if key not in _CACHE:
        sc = S.build(name, unit)
        folder = tmp_root / f"{name}_{unit.replace('/', '')}"
        folder.mkdir(parents=True, exist_ok=True)
        _CACHE[key] = (sc, GlucoCraftRun(sc, folder), ExpectedRun(sc))
    return _CACHE[key]


@pytest.fixture(scope="session")
def tmp_root(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("synth")


# ----------------------------------------------------------------------------------------------- comparisons
def r1(x):
    """An exact expected value rounded once to one decimal (None stays None)."""
    return None if x is None else rhu(x, 1)


def r0(x):
    return None if x is None else int(rhu(x, 0))


def shown(mg, unit: str) -> str:
    """The expected display text of an exact mg/dL value: mg/dL whole numbers, mmol/L one decimal, rounded once;
    "---" for no value (units.fmt)."""
    if mg is None:
        return "---"
    if unit == U.UNIT_MMOLL:
        mmol = (Decimal(mg.numerator) / Decimal(mg.denominator) if isinstance(mg, Fraction) else Decimal(mg)) / Decimal(
            "18.016"
        )
        return f"{rhu(mmol, 1):.1f}"
    return f"{int(rhu(mg, 0))}"


def compare_statistics(gc: GlucoCraftRun, ex: ExpectedRun, du: str) -> list[tuple]:
    out = []
    for label, s in gc.stats.items():
        e = ex.stats[label]
        seconds = dict(PERIODS + LONG_PERIODS)[label]
        if not e["covered_s"] or s is None:
            out.append((f"stats.{label}.computed", s is not None, bool(e["covered_s"])))
            continue
        # the mean, the SD, the CV and the percentages are kept unrounded (rounded once where shown): compared at one
        # decimal, and at a whole number as the cards and the panels show the percentages
        pairs = {
            "active": (r1(s.data_availability), r1(e["active"])),
            "active_ok": (s.sensor_active_ok, e["active"] >= 70),
            "tir": (r1(s.pct_time_in_target_range), r1(e["tir"])),
            "tir_whole": (rhu(s.pct_time_in_target_range, 0), rhu(e["tir"], 0)),
            "titr": (r1(s.pct_time_in_ideal_range), r1(e["titr"])),
            "below_70": (r1(s.time_below_low), r1(e["below_70"])),
            "below_54": (r1(s.time_critical_low), r1(e["very_low"])),
            "low_54_69": (r1(s.time_low), r1(e["low"])),
            "high_181_250": (r1(s.time_high), r1(e["high"])),
            "above_180": (r1(s.time_above_high), r1(e["above_180"])),
            "above_250": (r1(s.time_critical_high), r1(e["very_high"])),
            "mean": (r1(s.mean), r1(e["mean"])),
            "sd": (r1(s.std_dev), r1(e["sd"])),
            "cv": (r1(s.cv), r1(e["cv"])),
            "gmi": (st.gmi_shown(s, seconds), r1(e["gmi"]) if e["gmi_shown"] else None),
            "gri": (s.gri, r1(e["gri"])),
            "gri_zone": (s.gri_zone, E.gri_zone(r1(e["gri"]))),
            "gri_hypo": (s.gri_hypo, r1(e["gri_hypo"])),
            "gri_hyper": (s.gri_hyper, r1(e["gri_hyper"])),
            "min": (s.min_value, r1(e["min"])),
            "max": (s.max_value, r1(e["max"])),
            "limit_low": (s.limit_low, e["limits"]["low"]),
            "limit_high": (s.limit_high, e["limits"]["high"]),
            "limit_seconds": (s.limit_seconds, {"low": e["limits"]["low_s"], "high": e["limits"]["high_s"]}),
            "readings": (s.reading_count, e["readings"]),
            f"mean[{du}]": (U.fmt(s.mean, du), shown(e["mean"], du)),
            f"sd[{du}]": (U.fmt(s.std_dev, du), shown(e["sd"], du)),
        }
        if e["gmi_shown"]:
            pairs["gmi_mmol_mol"] = (s.gmi_mmol_mol, e["gmi_mmol_mol"])
        out += [(f"stats.{label}.{k}", a, b) for k, (a, b) in pairs.items()]
    return out


# The consensus target each report percentage is shown beside (Battelino et al., 2019): the report shows it on its side
# of the target, with two decimals where one would show the target's value (units.shown_against)
REPORT_TARGETS = {"tir": 70, "below_70": 4, "very_low": 1, "above_180": 25, "very_high": 5, "cv": 36}
for _when in ("night", "day"):
    REPORT_TARGETS.update(
        {f"{_when}_below_70": 4, f"{_when}_below_54": 1, f"{_when}_above_180": 25, f"{_when}_above_250": 5}
    )


def beside_target(key: str, value: float | None) -> float | None:
    """A report percentage as shown: one decimal, rounded once; beside its consensus target on its side of it."""
    if value is None:
        return None
    target = REPORT_TARGETS.get(key)
    return r1(value) if target is None else U.shown_against(float(value), target)[0]


def compare_report(gc: GlucoCraftRun, ex: ExpectedRun, du: str) -> list[tuple]:
    out = []
    rep, e = gc.report, ex.report
    p, m = rep["period"], e["period"]
    # beside the 70% rule: two decimals where one would show the rule (units.shown_against)
    enough = m["active"] >= 70  # the enough-data rule: statistics only with sensor active at least 70%, unrounded
    out.append(("report.active", p["active"], U.shown_against(float(m["active"]), 70)[0]))
    out.append(("report.enough_data", p["enough_data"], enough))
    out.append(("report.limit_readings", p["limit_readings"], {"low": e["limits"]["low"], "high": e["limits"]["high"]}))
    out.append(
        ("report.limit_seconds", p["limit_seconds"], {"low": e["limits"]["low_s"], "high": e["limits"]["high_s"]})
    )
    if not m["covered_s"] or p.get("tir") is None:
        out.append(("report.computed", p.get("tir") is not None, bool(m["covered_s"]) and enough))
    else:
        for k in ("very_low", "low", "tir", "titr", "high", "very_high", "below_70", "above_180", "cv"):
            out.append((f"report.{k}", p[k], beside_target(k, m[k])))
        # the report's mean and SD are unrounded, its lowest and highest value as stored (mg/dL, one decimal); the
        # page and the PDF round each once, in the display unit
        out += [
            ("report.mean", r0(p["mean"]), r0(m["mean"])),
            ("report.sd", r1(p["sd"]), r1(m["sd"])),
            ("report.min", p["min"], r1(m["min"])),
            ("report.max", p["max"], r1(m["max"])),
            (f"report.min[{du}]", U.fmt(p["min"], du), shown(m["min"], du)),
            (f"report.max[{du}]", U.fmt(p["max"], du), shown(m["max"], du)),
            ("report.gmi", p["gmi"], r1(m["gmi"]) if e["gmi_shown"] else None),
            ("report.gmi_mmol_mol", p["gmi_mmol_mol"], m["gmi_mmol_mol"] if e["gmi_shown"] else None),
            ("report.days_with_data", p["days_with_data"], e["days_with_data"]),
            (f"report.mean[{du}]", U.fmt(p["mean"], du), shown(m["mean"], du)),
            (f"report.sd[{du}]", U.fmt(p["sd"], du), shown(m["sd"], du)),
        ]
        if e["gri_shown"]:
            out += [
                ("report.gri", p["gri"], r1(m["gri"])),
                ("report.gri_zone", p["gri_zone"], E.gri_zone(r1(m["gri"]))),
            ]
    # the lows, the highs and the research measures are statistics of the period too: without enough data none
    if not enough:
        out.append(("lows", rep["lows"], {"enough_data": False, "list": []}))
        out.append(("highs", rep["highs"], {"enough_data": False}))
        if "research_appendix" in rep:
            none = {"events": None, "total_minutes": None, "longest_minutes": None}
            out.append(("extended_high", rep["research_appendix"], none))
    else:
        lows, el = rep["lows"], e["lows"]
        out += [
            ("lows.events", lows["events"], el["events"]),
            ("lows.events_54", lows["events_54"], el["events_54"]),
            ("lows.events_night", lows["events_night"], el["events_night"]),
            ("lows.mean_duration_min", lows["mean_duration_min"], r0(el["mean_minutes"])),
            ("lows.longest_min", lows["longest_min"], r0(el["longest_minutes"])),
        ]
        got = [[row[1], row[2], row[3], row[4], row[5]] for row in lows["list"]]
        # the lowest value as stored (mg/dL, one decimal; rounded once where shown, compared below)
        want = [
            [r0(x["minutes"]), r1(x["nadir"]), float(rhu(x["hour"], 2)), x["level"], x["nadir_is_lo"]]
            for x in el["list"]
        ]
        out.append(("lows.list[minutes, lowest, hour, level, lowest is LO]", got, want))
        out.append(
            (
                f"lows.lowest[{du}]",
                [U.fmt(row[2], du) for row in lows["list"]],
                [shown(x["nadir"], du) for x in el["list"]],
            )
        )
        nd = e["night_day"]
        for k in ("night_below_70", "day_below_70", "night_below_54", "day_below_54"):
            out.append((f"lows.{k}", lows[k], beside_target(k, nd[k])))
        for k in ("night_above_180", "day_above_180", "night_above_250", "day_above_250"):
            out.append((f"highs.{k}", rep["highs"][k], beside_target(k, nd[k])))
        ra, ex_h = rep["research_appendix"], e["extended_high"]
        out += [
            ("extended_high.events", ra["events"], ex_h["events"]),
            ("extended_high.total_minutes", ra["total_minutes"], r0(ex_h["total_minutes"])),
            ("extended_high.longest_minutes", ra["longest_minutes"], r0(ex_h["longest_minutes"])),
        ]
    q, eq = rep["quality"], e["quality"]
    out += [
        ("quality.per_day_active", [d["active"] for d in q["per_day"]], [r1(x) for x in eq["per_day"]]),
        ("quality.gaps", q["gaps"], eq["gaps"]),
        ("quality.longest_gap_minutes", q["longest_gap_minutes"], r0(eq["longest_gap_minutes"])),
    ]
    if ex.weekly is not None:
        got = [(w["tir"], w["cv"], r0(w["mean"]), w["active"]) for w in reversed(rep["weekly"])]
        # a week without enough data has no values and keeps its sensor active; a week without data has neither
        want = [
            (r1(w["tir"]), r1(w["cv"]), r0(w["mean"]), r1(w["active"]))
            if w and w["enough"]
            else (None, None, None, r1(w["active"]) if w else None)
            for w in ex.weekly
        ]
        out.append(("weekly[tir, cv, mean, active]", got, want))
    return out


def compare_store(gc: GlucoCraftRun, ex: ExpectedRun) -> list[tuple]:
    out = [
        ("store.retention_cutoff", gc.cutoff, ex.cutoff),
        (
            "store.first_reading",
            gc.readings[0].timestamp if gc.readings else None,
            ex.store.readings[0].ts if ex.store.readings else None,
        ),
        ("store.readings", len(gc.readings), len(ex.store.readings)),
        ("store.detected_interval", gc.detected, ex.store.detected),
    ]
    mism = [
        (a.timestamp, a.raw, a.limit, b.ts, float(b.mg), b.limit)
        for a, b in zip(gc.readings, ex.store.readings, strict=False)
        if (a.timestamp, a.raw, a.limit) != (b.ts, float(b.mg), b.limit)
    ]
    out.append(("store.first_mismatching_reading", mism[:1], []))
    return out


def disagreements(name: str, unit: str, du: str, tmp_root: Path) -> list[tuple]:
    _, gc, ex = runs(name, unit, tmp_root)
    rows = compare_store(gc, ex) + compare_statistics(gc, ex, du) + compare_report(gc, ex, du)
    return [(k, a, b) for k, a, b in rows if a != b]


def _known(name: str, unit: str, key: str) -> Finding | None:
    return next((f for f in FINDINGS if f.covers(name, unit, key)), None)


CASES = [(n, u, d) for n in S.NAMES for u in SOURCE_UNITS for d in DISPLAY_UNITS]


SLOW = {"retention_over_200_days"}  # builds a 6-month report


def _marks(name: str) -> list:
    return [pytest.mark.slow] if name in SLOW else []


@pytest.mark.parametrize(
    "name, unit, du", [pytest.param(n, u, d, marks=_marks(n), id=f"{n}-{u}-show_{d}") for n, u, d in CASES]
)
def test_scenario_agrees_with_expected(name, unit, du, tmp_root):
    """GlucoCraft's values equal the independent calculation, except the known findings (tested below)."""
    bad = [(k, a, b) for k, a, b in disagreements(name, unit, du, tmp_root) if not _known(name, unit, k)]
    assert not bad, "\n".join(
        f"{name} [{unit} source, {du} display] {k}: GlucoCraft {a!r}, expected {b!r}" for k, a, b in bad
    )


def _finding_params():
    return [
        pytest.param(
            f,
            n,
            u,
            d,
            marks=[pytest.mark.xfail(strict=True, reason=f.reason), *_marks(n)],
            id=f"{f.id}-{n}-{u}-show_{d}",
        )
        for f in FINDINGS
        for n, u, d in f.cases
    ]


@pytest.mark.parametrize("finding, name, unit, du", _finding_params())
def test_known_finding(finding, name, unit, du, tmp_root):
    """A known disagreement: fails (as expected) while GlucoCraft and the definitions disagree in that case."""
    bad = [(k, a, b) for k, a, b in disagreements(name, unit, du, tmp_root) if finding.covers(name, unit, k)]
    assert not bad, "\n".join(f"{finding.id}: {k}: GlucoCraft {a!r}, expected {b!r}" for k, a, b in bad)


def test_every_finding_case_is_a_scenario():
    """The findings name real scenarios and units."""
    for f in FINDINGS:
        for n, u, d in f.cases:
            assert n in S.NAMES and u in SOURCE_UNITS and d in DISPLAY_UNITS
