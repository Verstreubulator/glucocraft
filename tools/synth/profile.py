# License: AGPL-3.0-only (LICENSE).
"""Fit a synthetic-data profile from a glucose entity's recorded history in Home Assistant (read-only).

The profile holds CHARACTERISTICS only, never readings: the time-of-day percentiles per 15 minutes, the distribution of
the rate of change, the autocorrelation of the residuals (AR(1)), the measurement noise, how often and how long lows
(< 70, < 54 mg/dL) and highs (> 180, > 250 mg/dL) occur by day and by night, the reading-interval distribution, how
often and how long data gaps occur, and how often LO / HI and no-reading states occur. generate.py builds artificial
series from it.

A profile fitted from a real person's history is personal health information. Keep it outside the repository; this
tool refuses to write into a folder that contains GlucoCraft's source. The repository's default is the artificial
example profile (example_profile.py).

Home Assistant is only read: GET /api/states (to find the source) and GET /api/history/period (the history, in chunks).
The URL and token come from a JSON file ({"url": ..., "token": ...}); the token is never printed.

Usage (from the repository root):
  python -m tools.synth.profile --connection CONNECTION.json --find
  python -m tools.synth.profile --connection CONNECTION.json --entity sensor.cgm_glucose --days 200 --out PROFILE.json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from bisect import bisect_right
from collections import Counter
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import DEFAULT_TZ, MGDL_PER_MMOLL, UNIT_MGDL, UNIT_MMOLL, inside_repository, load_connection, unit_name

PROFILE_KIND = "glucocraft-synth-profile"
PROFILE_VERSION = 1
BIN_MINUTES = 15
BINS = 24 * 60 // BIN_MINUTES
PCTS = (5, 25, 50, 75, 95)
GRID_SECONDS = 300  # the regular grid on which the time-of-day profile and the AR(1) fit are made
NIGHT_END_HOUR = 6  # night = 00:00-05:59 local, as in GlucoCraft's report
EXCURSION_MIN_SECONDS = 15 * 60  # an excursion counts once it lasts 15 minutes (the event length of Danne et al., 2017)
# The standard-normal quantiles of the profile's percentiles 5 / 25 / 50 / 75 / 95: a value is mapped to z (and back)
# piecewise linearly through these knots, so that z has the profile's distribution at each time of day
Z_KNOTS = (-1.6448536269514722, -0.6744897501960817, 0.0, 0.6744897501960817, 1.6448536269514722)
TAIL_SLOPE = 0.5  # beyond the 5th / 95th percentile: half the slope of the outer segment
TIME_ATTRIBUTES = ("measurement_timestamp", "dateString", "date", "timestamp", "last_reading", "reading_time")
NO_READING = frozenset({"", "unavailable", "unknown", "none", "null", "nan", "---", "--", "-"})
LOW_WORDS = frozenset({"lo", "low"})
HIGH_WORDS = frozenset({"hi", "high"})

# The excursion classes: (key, predicate on mg/dL, extreme function)
EXCURSIONS = (
    ("below_70", lambda v: v < 70, min),
    ("below_54", lambda v: v < 54, min),
    ("above_180", lambda v: v > 180, max),
    ("above_250", lambda v: v > 250, max),
)


# ------------------------------------------------------------------------------------------------ the REST API (GET)
def _get(url: str, token: str, timeout: int = 180, retries: int = 2):
    """GET a Home Assistant REST path and return the parsed JSON. Stops at once on 401 / 403."""
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, method="GET"
    )
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as err:
            if err.code in (401, 403):
                raise SystemExit(f"Home Assistant refused the request ({err.code}); check the token") from err
            if attempt == retries:
                raise
        except urllib.error.URLError:
            if attempt == retries:
                raise
        time.sleep(5 * (attempt + 1))
    return None


def find_candidates(base: str, token: str) -> list[dict]:
    """Return the entities that look like a glucose source: entity id, unit and whether a measurement time exists."""
    out = []
    for s in _get(f"{base}/api/states", token) or []:
        attrs = s.get("attributes") or {}
        uom = str(attrs.get("unit_of_measurement") or "").lower().replace(" ", "")
        if uom not in ("mg/dl", "mmol/l"):
            continue
        out.append(
            {
                "entity_id": s["entity_id"],
                "unit": attrs.get("unit_of_measurement"),
                "time_attribute": next((a for a in TIME_ATTRIBUTES if a in attrs), None),
            }
        )
    return out


def fetch_history(
    base: str,
    token: str,
    entity_id: str,
    start: datetime,
    end: datetime,
    chunk_days: float = 2,
    progress=None,
    pause: float = 0.0,
) -> list[dict]:
    """Return every recorded state of one entity between start and end (UTC datetimes), read in chunks, one request
    at a time, `pause` seconds apart (a long read stays gentle on the Home Assistant it reads).

    significant_changes_only=0 keeps the rows in which only the attributes changed (a new reading with an unchanged
    value changes its measurement time attribute only).
    """
    rows: list[dict] = []
    t = start
    while t < end:
        t2 = min(end, t + timedelta(days=chunk_days))
        query = urllib.parse.urlencode(
            {"filter_entity_id": entity_id, "end_time": t2.isoformat(), "significant_changes_only": "0"}
        )
        data = _get(f"{base}/api/history/period/{urllib.parse.quote(t.isoformat())}?{query}", token)
        chunk = data[0] if data else []
        # the first row of a chunk can be the state that was current at the chunk's start: keep rows inside only
        rows.extend(r for r in chunk if (u := _utc(r.get("last_updated"))) is not None and t <= u < t2)
        if progress:
            progress(t2, len(rows))
        t = t2
        if pause and t < end:
            time.sleep(pause)
    return rows


def _utc(text: str | None) -> datetime | None:
    """A Home Assistant time text as an aware UTC datetime (None for none)."""
    return datetime.fromisoformat(text).astimezone(UTC) if text else None


def _epoch(value) -> int | None:
    """Epoch seconds from an ISO text or a number of seconds / milliseconds; None otherwise."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) or str(value).replace(".", "", 1).isdigit():
        num = float(value)
        if num > 1e12:
            num /= 1000.0
        return int(num) if num > 1e9 else None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return int(dt.timestamp()) if dt.tzinfo else None


def parse_states(rows: list[dict], time_attribute: str = "") -> tuple[list[tuple[int, float, str]], dict]:
    """Return the recorded states as readings [(measurement time, mg/dL, kind)] and counts of what was left out.

    kind is "reading", "low" (LO, counted at 40 mg/dL) or "high" (HI, counted at 500 mg/dL). One reading per
    measurement time (the last recorded). No-reading states (unavailable, unknown, error texts) are counted only.
    """
    by_time: dict[int, tuple[int, float, str]] = {}
    counts = Counter()
    for r in rows:
        attrs = r.get("attributes") or {}
        text = str(r.get("state", "")).strip().lower()
        names = [time_attribute] if time_attribute else list(TIME_ATTRIBUTES)
        ts = next((t for t in (_epoch(attrs.get(a)) for a in names) if t is not None), None)
        if ts is None:
            ts = _epoch(r.get("last_updated"))
        if text in NO_READING:
            counts["no_reading"] += 1
            continue
        unit = UNIT_MMOLL if "mmol" in str(attrs.get("unit_of_measurement") or "").lower() else UNIT_MGDL
        if text in LOW_WORDS:
            kind, mg = "low", 40.0
        elif text in HIGH_WORDS:
            kind, mg = "high", 500.0
        else:
            try:
                v = float(text.replace(",", "."))
            except ValueError:
                counts["other_text"] += 1
                continue
            mg = v * MGDL_PER_MMOLL if unit == UNIT_MMOLL else v
            if mg <= 12.99 or mg > 6000:
                counts["error_code"] += 1
                continue
            kind = "reading"
        if ts is None:
            counts["no_time"] += 1
            continue
        by_time[ts] = (ts, mg, kind)
        counts[unit] += 1
    return [by_time[t] for t in sorted(by_time)], dict(counts)


# ------------------------------------------------------------------------------------------------------ fitting
def _pct(sorted_vals: list[float], p: float) -> float | None:
    """The p-th percentile of sorted values (linear interpolation), None for no values."""
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p / 100.0
    lo = math.floor(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _pcts(values: list[float], ps=(10, 25, 50, 75, 90)) -> dict[str, float | None]:
    s = sorted(values)
    return {f"p{p}": (round(_pct(s, p), 3) if s else None) for p in ps}


def _knots(q) -> list[float]:
    """The five percentiles made strictly increasing (a flat stretch would make the mapping ambiguous)."""
    out = [float(q[0])]
    for x in q[1:]:
        out.append(max(float(x), out[-1] + 0.01))
    return out


def z_to_value(z: float, q) -> float:
    """Map a standard-normal z onto the percentiles q = (p5, p25, p50, p75, p95), piecewise linearly."""
    v = _knots(q)
    if z <= Z_KNOTS[0]:
        return v[0] + (z - Z_KNOTS[0]) * TAIL_SLOPE * (v[1] - v[0]) / (Z_KNOTS[1] - Z_KNOTS[0])
    if z >= Z_KNOTS[-1]:
        return v[-1] + (z - Z_KNOTS[-1]) * TAIL_SLOPE * (v[-1] - v[-2]) / (Z_KNOTS[-1] - Z_KNOTS[-2])
    k = bisect_right(Z_KNOTS, z) - 1
    return v[k] + (z - Z_KNOTS[k]) * (v[k + 1] - v[k]) / (Z_KNOTS[k + 1] - Z_KNOTS[k])


def value_to_z(x: float, q) -> float:
    """The inverse of z_to_value."""
    v = _knots(q)
    if x <= v[0]:
        return Z_KNOTS[0] + (x - v[0]) / (TAIL_SLOPE * (v[1] - v[0]) / (Z_KNOTS[1] - Z_KNOTS[0]))
    if x >= v[-1]:
        return Z_KNOTS[-1] + (x - v[-1]) / (TAIL_SLOPE * (v[-1] - v[-2]) / (Z_KNOTS[-1] - Z_KNOTS[-2]))
    k = bisect_right(v, x) - 1
    return Z_KNOTS[k] + (x - v[k]) * (Z_KNOTS[k + 1] - Z_KNOTS[k]) / (v[k + 1] - v[k])


def _bin(ts: int, tz: ZoneInfo) -> tuple[int, bool]:
    """The local 15-minute time-of-day bin of a time and whether it is night (00:00-05:59)."""
    d = datetime.fromtimestamp(ts, tz)
    return (d.hour * 60 + d.minute) // BIN_MINUTES, d.hour < NIGHT_END_HOUR


def fit(
    readings: list[tuple[int, float, str]],
    tz_name: str = DEFAULT_TZ,
    unit: str = UNIT_MGDL,
    precision: int = 0,
    counts: dict | None = None,
) -> dict:
    """Fit a profile from readings [(measurement time, mg/dL, kind)] sorted by time. See the module docstring."""
    if len(readings) < 100:
        raise ValueError("at least 100 readings are needed to fit a profile")
    if any(not (math.isfinite(float(r[0])) and math.isfinite(float(r[1]))) for r in readings):
        raise ValueError("a reading with a time or value that is not a finite number cannot be fitted")
    readings = sorted(readings, key=lambda r: r[0])  # parse_states returns them sorted; any other caller may not
    tz = ZoneInfo(tz_name)
    ts = [r[0] for r in readings]
    vals = [r[1] for r in readings]
    deltas = [b - a for a, b in pairwise(ts)]
    median_iv = int(statistics.median([d for d in deltas if 0 < d <= 3600]) or 60)
    threshold = max(900, 3 * median_iv)
    span_days = (ts[-1] - ts[0]) / 86400.0
    spans = [d if d <= threshold else median_iv for d in deltas] + [median_iv]
    covered_s = float(sum(spans))
    weeks = covered_s / (7 * 86400.0)

    # --- reading intervals and gaps
    normal = [d for d in deltas if 0 < d <= threshold]
    step = 5 if median_iv <= 120 else 30
    hist = Counter(int(round(d / step) * step) for d in normal)
    top = sorted(hist.items(), key=lambda kv: -kv[1])[:40]
    intervals = {
        "median_s": median_iv,
        "histogram": [[s, round(c / len(normal), 6)] for s, c in sorted(top)],
        "missed_share": round(sum(1 for d in normal if d > 1.5 * median_iv) / max(1, len(normal)), 6),
        "jitter_sd_s": round(
            statistics.pstdev([d - median_iv for d in normal if 0.5 * median_iv <= d <= 1.5 * median_iv])
            if len(normal) > 1
            else 0.0,
            3,
        ),
    }
    gaps = [d for d in deltas if d > threshold]
    gap_info = {
        "threshold_s": threshold,
        "count": len(gaps),
        "per_week": round(len(gaps) / max(weeks, 1e-9), 4),
        "length_min": _pcts([g / 60 for g in gaps]) | {"max": round(max(gaps) / 60, 1) if gaps else None},
    }

    # --- the regular grid: the value of the reading that covers each grid time (gaps stay empty)
    grid_t: list[int] = []
    grid_v: list[float | None] = []
    k = 0
    g = -(-ts[0] // GRID_SECONDS) * GRID_SECONDS
    while g <= ts[-1]:
        k = bisect_right(ts, g, lo=max(0, k - 1)) - 1
        ok = k >= 0 and g < ts[k] + spans[k]
        grid_t.append(g)
        grid_v.append(vals[k] if ok else None)
        g += GRID_SECONDS

    # --- time-of-day percentiles per 15 minutes
    per_bin: list[list[float]] = [[] for _ in range(BINS)]
    bins = [_bin(t, tz)[0] for t in grid_t]
    for b, v in zip(bins, grid_v, strict=False):
        if v is not None:
            per_bin[b].append(v)
    tod: dict[str, list] = {f"p{p}": [] for p in PCTS} | {"mean": []}
    for b in range(BINS):
        pool = sorted(per_bin[(b - 1) % BINS] + per_bin[b] + per_bin[(b + 1) % BINS])  # 45-minute window
        for p in PCTS:
            tod[f"p{p}"].append(round(_pct(pool, p), 2) if pool else None)
        tod["mean"].append(round(sum(pool) / len(pool), 2) if pool else None)

    # --- residuals, their z-scores and the AR(1) fit on the 5-minute grid
    q_of = [[tod[f"p{p}"][b] for p in PCTS] for b in range(BINS)]
    z = [None if v is None else value_to_z(v, q_of[b]) for b, v in zip(bins, grid_v, strict=False)]
    resid = [None if v is None else v - tod["p50"][b] for b, v in zip(bins, grid_v, strict=False)]

    def ar1(series: list[float | None]) -> tuple[float, float]:
        pairs = [(a, b) for a, b in pairwise(series) if a is not None and b is not None]
        sxx = sum(a * a for a, _ in pairs)
        phi = sum(a * b for a, b in pairs) / sxx if sxx else 0.0
        innov = statistics.pstdev([b - phi * a for a, b in pairs]) if len(pairs) > 1 else 0.0
        return phi, innov

    def acf(series: list[float | None], lag: int) -> float | None:
        xs = [x for x in series if x is not None]
        if len(xs) < lag + 10:
            return None
        m = sum(xs) / len(xs)
        var = sum((x - m) ** 2 for x in xs) / len(xs)
        pairs = [(a, b) for a, b in zip(series, series[lag:], strict=False) if a is not None and b is not None]
        if not pairs or not var:
            return None
        return sum((a - m) * (b - m) for a, b in pairs) / len(pairs) / var

    phi_z, innov_z = ar1(z)
    phi_r, innov_r = ar1(resid)
    zs = [x for x in z if x is not None]
    variability = {
        "grid_seconds": GRID_SECONDS,
        "z_mapping": "piecewise linear through the time-of-day percentiles 5 / 25 / 50 / 75 / 95 at the normal "
        "quantiles (profile.value_to_z)",
        "z_sd": round(statistics.pstdev(zs), 4),
        "ar1_phi": round(phi_z, 5),
        "ar1_innovation_sd": round(innov_z, 5),
        "acf_z": {
            f"{lag * GRID_SECONDS // 60}min": (round(a, 4) if (a := acf(z, lag)) is not None else None)
            for lag in (1, 3, 6, 12, 24, 48)
        },
        "residual_mgdl_ar1_phi": round(phi_r, 5),
        "residual_mgdl_innovation_sd": round(innov_r, 3),
        "residual_mgdl_sd": round(statistics.pstdev([r for r in resid if r is not None]), 3),
    }

    # --- the rate of change: over the 5-minute grid and between consecutive readings
    roc5 = [(b - a) / (GRID_SECONDS / 60) for a, b in pairwise(grid_v) if a is not None and b is not None]
    roc_raw = [
        (vals[i + 1] - vals[i]) / (deltas[i] / 60)
        for i in range(len(deltas))
        if 0 < deltas[i] <= threshold and readings[i][2] == readings[i + 1][2] == "reading"
    ]
    rps = (1, 5, 10, 25, 50, 75, 90, 95, 99)
    rate = {
        "unit": "mg/dL/min",
        "per_5min": _pcts(roc5, rps) | {"sd": round(statistics.pstdev(roc5), 4) if roc5 else None},
        "between_readings": _pcts(roc_raw, rps) | {"sd": round(statistics.pstdev(roc_raw), 4) if roc_raw else None},
    }

    # --- noise: the second difference of evenly spaced consecutive readings, var(d) = 1.5 x noise variance
    dd = []
    for i in range(1, len(readings) - 1):
        a, b = deltas[i - 1], deltas[i]
        if (
            abs(a - median_iv) <= 0.1 * median_iv
            and abs(b - median_iv) <= 0.1 * median_iv
            and all(readings[j][2] == "reading" for j in (i - 1, i, i + 1))
        ):
            dd.append(vals[i] - (vals[i - 1] + vals[i + 1]) / 2)
    noise = {
        "sd_mgdl": round(math.sqrt(sum(d * d for d in dd) / len(dd) / 1.5), 4) if dd else 0.0,
        "method": "second difference of evenly spaced readings: var = 1.5 x noise variance",
    }

    # --- excursions: runs of consecutive readings beyond a limit, without a gap, lasting at least 15 minutes
    covered_by_part = {"day": 0.0, "night": 0.0}
    for t, s in zip(ts, spans, strict=False):
        covered_by_part["night" if _bin(t, tz)[1] else "day"] += s
    excursions = {}
    for key, pred, ext in EXCURSIONS:
        runs: list[tuple[int, float, float]] = []  # (start, seconds, extreme)
        time_in = {"day": 0.0, "night": 0.0}
        start, dur, extreme = None, 0.0, None
        for i, (t, v) in enumerate(zip(ts, vals, strict=False)):
            if pred(v):
                time_in["night" if _bin(t, tz)[1] else "day"] += spans[i]
                if start is None:
                    start, dur, extreme = t, 0.0, v
                dur += spans[i]
                extreme = ext(extreme, v)
            elif start is not None:
                runs.append((start, dur, extreme))
                start = None
            if start is not None and (i == len(ts) - 1 or deltas[i] > threshold):
                runs.append((start, dur, extreme))
                start = None
        events = [r for r in runs if r[1] >= EXCURSION_MIN_SECONDS]
        info = {}
        for part in ("day", "night"):
            n = sum(1 for r in events if _bin(r[0], tz)[1] == (part == "night"))
            hours = covered_by_part[part] / 3600
            info[part] = {
                "count": n,
                "per_covered_hour": round(n / hours, 6) if hours else 0.0,
                "per_week": round(n / max(weeks, 1e-9), 4),
                "time_pct": round(100 * time_in[part] / covered_by_part[part], 3) if hours else 0.0,
            }
        info["duration_min"] = _pcts([r[1] / 60 for r in events])
        info["extreme_mgdl"] = _pcts([r[2] for r in events])
        excursions[key] = info

    # --- LO / HI and no-reading states
    kinds = Counter(r[2] for r in readings)
    counts = counts or {}
    limits = {
        "lo_per_week": round(kinds["low"] / max(weeks, 1e-9), 4),
        "hi_per_week": round(kinds["high"] / max(weeks, 1e-9), 4),
        "at_or_below_40_per_week": round(sum(1 for v in vals if v <= 40) / max(weeks, 1e-9), 4),
        "at_or_above_400_per_week": round(sum(1 for v in vals if v >= 400) / max(weeks, 1e-9), 4),
    }
    no_reading = {
        "per_week": round(counts.get("no_reading", 0) / max(weeks, 1e-9), 4),
        "count": int(counts.get("no_reading", 0)),
    }

    # --- summary statistics (time-weighted), for the record only
    tw = sum(spans)
    mean = sum(v * s for v, s in zip(vals, spans, strict=False)) / tw
    sd = math.sqrt(sum(s * (v - mean) ** 2 for v, s in zip(vals, spans, strict=False)) / tw)

    def band(f):
        """The share of the time (%, two decimals) whose value meets f."""
        return round(100 * sum(s for v, s in zip(vals, spans, strict=False) if f(v)) / tw, 2)

    summary = {
        "days_spanned": round(span_days, 2),
        "readings": len(readings),
        "sensor_active_pct": round(100 * covered_s / max(1, ts[-1] - ts[0] + median_iv), 2),
        "mean_mgdl": round(mean, 2),
        "sd_mgdl": round(sd, 2),
        "cv_pct": round(100 * sd / mean, 2),
        "tir_70_180_pct": band(lambda v: 70 <= v <= 180),
        "below_70_pct": band(lambda v: v < 70),
        "below_54_pct": band(lambda v: v < 54),
        "above_180_pct": band(lambda v: v > 180),
        "above_250_pct": band(lambda v: v > 250),
    }

    return {
        "kind": PROFILE_KIND,
        "version": PROFILE_VERSION,
        "artificial": False,
        "description": "Characteristics fitted from a recorded glucose history. Personal health information: keep "
        "this file outside the repository and never share it.",
        "fitted_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "source": {"unit": unit, "precision": precision, "median_interval_s": median_iv, "tz": tz_name},
        "summary": summary,
        "time_of_day": {"bin_minutes": BIN_MINUTES, "window": "each bin pooled with both neighbours (45 minutes)"}
        | tod,
        "variability": variability,
        "rate_of_change": rate,
        "noise": noise,
        "excursions": excursions,
        "intervals": intervals,
        "gaps": gap_info,
        "limits": limits,
        "no_reading_states": no_reading,
    }


# ---------------------------------------------------------------------------------------------- saving and CLI
def save_profile(profile: dict, out: str | Path) -> Path:
    """Write a profile as JSON. A fitted (not artificial) profile is never written inside the repository."""
    path = Path(out).expanduser()
    if not profile.get("artificial") and inside_repository(path):
        raise SystemExit(f"refusing to write a fitted profile inside the repository: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=1), encoding="utf-8")
    return path


def load_profile(path: str | Path) -> dict:
    """Read a profile written by save_profile() and check its kind and its values (validate_profile)."""
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if data.get("kind") != PROFILE_KIND:
        raise ValueError(f"{path} is not a {PROFILE_KIND} file")
    return validate_profile(data)


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate_profile(profile: dict) -> dict:
    """Refuse a profile that would generate silently wrong data; return it unchanged when it is valid.

    Checked: the five time-of-day percentile lists (96 finite mg/dL values each, 0-1000, p5 <= p25 <= ... <= p95 per
    bin), the AR(1) coefficient (0 <= phi < 1), and every rate, share, length and standard deviation the generator
    reads (finite, not negative).
    """
    tod = profile.get("time_of_day") or {}
    for p in PCTS:
        arr = tod.get(f"p{p}")
        if not isinstance(arr, list) or len(arr) != BINS or not all(_finite(v) and 0 <= v <= 1000 for v in arr):
            raise ValueError(f"profile time_of_day.p{p}: {BINS} finite mg/dL values (0-1000) are needed")
    for b in range(BINS):
        col = [tod[f"p{p}"][b] for p in PCTS]
        if any(y < x for x, y in pairwise(col)):
            raise ValueError(f"profile time_of_day bin {b}: the percentiles must not decrease (p5 <= ... <= p95)")
    phi = (profile.get("variability") or {}).get("ar1_phi", 0.96)
    if not (_finite(phi) and 0 <= phi < 1):
        raise ValueError("profile variability.ar1_phi must be at least 0 and below 1")

    def nonneg(value, name):
        if value is not None and not (_finite(value) and value >= 0):
            raise ValueError(f"profile {name} must be a finite number of 0 or more")

    for key, info in (profile.get("excursions") or {}).items():
        for part in ("day", "night"):
            for f in ("per_covered_hour", "per_week"):
                nonneg(((info or {}).get(part) or {}).get(f), f"excursions.{key}.{part}.{f}")
        for f in ("duration_min", "extreme_mgdl"):
            for q, v in ((info or {}).get(f) or {}).items():
                nonneg(v, f"excursions.{key}.{f}.{q}")
    iv = profile.get("intervals") or {}
    for f in ("median_s", "missed_share", "jitter_sd_s"):
        nonneg(iv.get(f), f"intervals.{f}")
    if iv.get("missed_share") is not None and iv["missed_share"] > 1:
        raise ValueError("profile intervals.missed_share is a share (0-1)")
    gp = profile.get("gaps") or {}
    nonneg(gp.get("per_week"), "gaps.per_week")
    for q, v in (gp.get("length_min") or {}).items():
        nonneg(v, f"gaps.length_min.{q}")
    nonneg((profile.get("noise") or {}).get("sd_mgdl"), "noise.sd_mgdl")
    nonneg((profile.get("no_reading_states") or {}).get("per_week"), "no_reading_states.per_week")
    for f, v in (profile.get("limits") or {}).items():
        nonneg(v, f"limits.{f}")
    return profile


def main(argv: list[str] | None = None) -> int:
    """Fit a profile from a recorded history (command line); return the exit code."""
    ap = argparse.ArgumentParser(prog="python -m tools.synth.profile", description=__doc__.split("\n\n")[0])
    ap.add_argument("--connection", required=True, help='JSON file with {"url": ..., "token": ...}')
    ap.add_argument("--find", action="store_true", help="list the entities that look like a glucose source")
    ap.add_argument("--entity", help="the source entity, for example sensor.cgm_glucose")
    ap.add_argument("--time-attribute", default="", help="the measurement-time attribute (default: the known names)")
    ap.add_argument("--days", type=float, default=200, help="how far back to read (default 200)")
    ap.add_argument("--chunk-days", type=float, default=2, help="days per history request (default 2)")
    ap.add_argument("--tz", default=DEFAULT_TZ, help=f"local time zone of the history (default {DEFAULT_TZ})")
    ap.add_argument("--out", help="where to write the profile (outside the repository)")
    args = ap.parse_args(argv)
    base, token = load_connection(args.connection)
    if args.find:
        for c in find_candidates(base, token):
            print(f"{c['entity_id']}  unit={c['unit']}  time_attribute={c['time_attribute']}")
        return 0
    if not args.entity or not args.out:
        ap.error("--entity and --out are required (or use --find)")
    out = Path(args.out).expanduser()
    if inside_repository(out):
        ap.error(f"--out must be outside the repository: {out}")
    end = datetime.now(UTC)
    start = end - timedelta(days=args.days)

    def progress(t: datetime, n: int) -> None:
        print(f"\rread up to {t:%Y-%m-%d}: {n} states", end="", file=sys.stderr, flush=True)

    rows = fetch_history(base, token, args.entity, start, end, args.chunk_days, progress)
    print(file=sys.stderr)
    readings, counts = parse_states(rows, args.time_attribute)
    units = [u for u in (UNIT_MGDL, UNIT_MMOLL) if counts.get(u)]
    unit = unit_name(units[0]) if units else UNIT_MGDL
    precision = 1 if unit == UNIT_MMOLL else 0
    profile = fit(readings, args.tz, unit, precision, counts)
    path = save_profile(profile, out)
    s = profile["summary"]
    print(
        f"profile written to {path}: {s['readings']} readings over {s['days_spanned']} days, "
        f"interval {profile['intervals']['median_s']} s, {profile['gaps']['count']} gaps"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
