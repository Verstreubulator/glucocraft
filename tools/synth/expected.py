# License: AGPL-3.0-only (LICENSE).
"""An independent calculator of the values GlucoCraft should show for a series of source states.

Written from the published definitions and GlucoCraft's documented rules, NOT from GlucoCraft's statistics code
(nothing here imports the integration), so that the tests can compare the two:

  * consensus ranges and targets: Battelino et al. (2019); the tight range 70-140 mg/dL: Battelino et al. (2023);
  * GMI = 3.31 + 0.02392 x mean glucose (mg/dL), and 12.71 + 4.70587 x mean glucose (mmol/L) in mmol/mol
    (Bergenstal et al., 2018); shown only for 14 days or more with at least 70% sensor active (unrounded);
  * GRI = 3.0 x very low % + 2.4 x low % + 1.6 x very high % + 0.8 x high %, capped at 100, zone A-E by the GRI as
    displayed (Klonoff et al., 2023; zones from the value: GlucoCraft rule, docs/SYSTEM_DESIGN.md section 6.5);
  * hypoglycemia events: start after 15 consecutive minutes below the limit (70 / 54 mg/dL), end after 15
    consecutive minutes at or above 70 mg/dL for both levels (Danne et al., 2017, Section 4); a data gap ends an event
    (docs/SYSTEM_DESIGN.md sections 4.5 and 6.4); each counted once, where it starts;
  * extended high-glucose events: the method of the CGM Report's appendix "Measures defined for research", steps 1-6
    (Battelino et al., 2023, p. 48; docs/SYSTEM_DESIGN.md section 6.4);
  * the time weighting and "sensor active" (docs/SYSTEM_DESIGN.md section 4.2), the gap rule max(15 min, 3 x the
    reading interval) with the reading interval per stretch (section 4.5), the detected interval (section 2);
  * a single reading: its value is the mean, and there is no SD and no CV (one value has no spread; section 4.2);
  * mmol/L: converted with 18.016 to mg/dL with one decimal, the consensus values as published pairs (section 4.3);
  * LO / HI (the Documentation, "Readings Outside the Sensor's Range"): values in mmol/L are first converted to mg/dL;
    LO = the texts LO / LOW / "Below ...", 39 mg/dL (2.2 mmol/L) or less, below the lower limit, or at a lower limit
    set by hand; HI = HI / HIGH / "Above ...", above the upper limit, at an upper limit set by hand, or exactly 401 from
    a Dexcom-type source ("at the limit": whole mg/dL, or one decimal in mmol/L); stored and counted at the limit;
  * the CGM Report's periods: full local days ending at the last midnight; night = 00:00-05:59 local;
  * the retention: 200 days, whole local days;
  * sine waves (sine.py): the closed-form time in each range, mean and SD of a wave over whole cycles
    (sine_statistics).

All time arithmetic is in whole seconds and all percentages and means are exact fractions; values are rounded once,
halves away from zero, at the precision GlucoCraft displays.
"""

from __future__ import annotations

import bisect
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, getcontext
from fractions import Fraction
from itertools import pairwise
from zoneinfo import ZoneInfo

from .common import UNIT_MMOLL, SourceState, round_half_up, unit_name

getcontext().prec = 40

FACTOR = Decimal("18.016")
PAIRS_MMOL = {Decimal("3.0"): 54, Decimal("3.9"): 70, Decimal("7.8"): 140, Decimal("10.0"): 180, Decimal("13.9"): 250}
VERY_LOW, LOW, TIGHT_HIGH, HIGH, VERY_HIGH = 54, 70, 140, 180, 250
GAP_FLOOR, GAP_FACTOR, WINDOW, MIN_INTERVALS = 900, 3, 10, 3
DEFAULT_INTERVAL = 300
EVENT_S = 15 * 60
XHIGH_START_S, XHIGH_END_S = 120 * 60, 15 * 60
NIGHT_END_HOUR = 6
RETENTION_DAYS = 200
WEEK_MIN_PCT = 70  # the enough-data rule: sensor active at least 70% of the week
NO_READING = {"", "unavailable", "unknown", "none", "null", "nan", "---", "--", "-"}
LOW_WORDS, HIGH_WORDS = {"lo", "low"}, {"hi", "high"}


@dataclass(frozen=True)
class Reading:
    """A stored reading: measurement time (s), mg/dL (exact), "low" / "high" for a limit reading."""

    ts: int
    mg: Fraction
    limit: str | None = None


# ----------------------------------------------------------------------------------------------- source states
def to_mgdl(text: str, unit: str) -> Fraction:
    """A numeric state text in the source unit -> mg/dL with one decimal (consensus values as published pairs)."""
    d = Decimal(text.replace(",", "."))
    if unit_name(unit) == UNIT_MMOLL:
        one = d.quantize(Decimal("0.1"), rounding="ROUND_HALF_UP")
        if one == d and one in PAIRS_MMOL:
            return Fraction(PAIRS_MMOL[one])
        d = d * FACTOR
    return Fraction(d.quantize(Decimal("0.1"), rounding="ROUND_HALF_UP"))


def _nearest(mg: Fraction, choices: tuple[int, ...]) -> int | None:
    best = min(choices, key=lambda c: abs(c - mg))
    return best if abs(best - mg) <= 2 else None


def classify(
    text: str,
    unit: str,
    lower: int = 40,
    upper: int = 500,
    dexcom: bool = False,
    nightscout: bool = False,
    lower_set: bool = False,
    upper_set: bool = False,
) -> tuple[str, Fraction | None]:
    """One state -> ("none" | "reading" | "low" | "high", mg/dL). lower_set / upper_set: the lower / upper limit was
    set by hand. See the module docstring for the rules."""
    t = str(text).strip().lower()
    if t in NO_READING or t.startswith("?"):
        return "none", None
    m = re.match(r"^(below|above)\s*([0-9]+(?:[.,][0-9]+)?)\s*(mg\s*/\s*dl|mmol\s*/\s*l)?", t)
    if m:
        u = m.group(3) or ("mmol/L" if unit_name(unit) == UNIT_MMOLL else "mg/dL")
        mg = to_mgdl(m.group(2), "mmol/L" if "mmol" in u else "mg/dL")
        if m.group(1) == "below":
            return "low", Fraction(_nearest(mg, (40, 50)) or lower)
        return "high", Fraction(_nearest(mg, (400, 450, 500)) or upper)
    word = re.sub(r"[^a-z]", "", t)
    if word in LOW_WORDS:
        return "low", Fraction(lower)
    if word in HIGH_WORDS:
        return "high", Fraction(upper)
    try:
        value = Decimal(t.replace(",", "."))
    except ArithmeticError:
        return "none", None
    if not value.is_finite():
        return "none", None  # nan / inf: never glucose
    if value >= Decimal("6553.5"):
        return "none", None  # the warm-up marker
    mmol = unit_name(unit) == UNIT_MMOLL
    mg = to_mgdl(t, unit)
    if mg <= 0 or mg < 13 or (not mmol and value == 38) or (nightscout and mg < 39):
        return "none", None  # error codes, never glucose
    if (mmol and value <= Decimal("2.2")) or (not mmol and value <= 39) or mg < lower:
        return "low", Fraction(lower)
    if lower_set and _at_limit(value, mmol, lower) <= 0:
        return "low", Fraction(lower)
    if dexcom and not mmol and value == 401:
        return "high", Fraction(upper)  # stored at the upper limit in use, as every HI reading
    if mg > upper:
        return "high", Fraction(upper)
    if upper_set and _at_limit(value, mmol, upper) >= 0:
        return "high", Fraction(upper)
    return "reading", mg


def _at_limit(value: Decimal, mmol: bool, limit: int) -> int:
    """Compare a value (in the source's unit) with a limit (mg/dL) at the limit's precision: whole mg/dL, or the limit
    in mmol/L with one decimal. Returns -1 (below), 0 (at the limit) or 1 (above)."""
    shown, at = (
        (round_half_up(value, 1), round_half_up(Decimal(limit) / FACTOR, 1)) if mmol else (round_half_up(value), limit)
    )
    return (shown > at) - (shown < at)


def readings_from_states(states: list[SourceState], unit: str, limits: dict | None = None) -> list[Reading]:
    """The stored readings of a source's recorded states: one per measurement time (the last recorded), sorted."""
    lim = limits or {}
    by_time: dict[int, Reading] = {}
    for s in sorted(states, key=lambda x: x.recorded):
        kind, mg = classify(
            s.state,
            unit,
            lim.get("lower", 40),
            lim.get("upper", 500),
            lim.get("dexcom_type", False),
            lim.get("nightscout", False),
            lower_set=lim.get("lower_setting", "auto") != "auto",
            upper_set=lim.get("upper_setting", "auto") != "auto",
        )
        if kind == "none" or s.measured is None:
            continue
        by_time[s.measured] = Reading(s.measured, mg, kind if kind in ("low", "high") else None)
    return [by_time[t] for t in sorted(by_time)]


# ------------------------------------------------------------------------------------------------ the gap rule
def _upper_median(xs: list[int]) -> int:
    return sorted(xs)[len(xs) // 2]


def detected_interval(ts: list[int]) -> int:
    """The source's detected interval (docs/SYSTEM_DESIGN.md section 2): the median of the intervals between the
    last 50 readings that are over 30 s and under 15 minutes; only when there is none, of those from 15 minutes to
    one hour; 300 s with fewer than 10 readings or no such interval."""
    if len(ts) < 10:
        return DEFAULT_INTERVAL
    last = ts[-50:]
    d = [b - a for a, b in pairwise(last)]
    short = [x for x in d if 30 < x < 900]
    slow = [x for x in d if 900 <= x <= 3600]
    return _upper_median(short) if short else _upper_median(slow) if slow else DEFAULT_INTERVAL


def intervals(ts: list[int], fallback: int) -> list[int]:
    """The reading interval at each reading: the median (upper middle) of the 10 intervals before it, filled with
    the intervals after it, never the interval being judged; the fallback with fewer than 3."""
    d = [b - a for a, b in pairwise(ts)]
    out = []
    for i in range(len(ts)):
        before = d[max(0, i - WINDOW) : i]
        after = d[i + 1 : i + 1 + WINDOW - len(before)] if len(before) < WINDOW else []
        cand = [x for x in before + after if x > 0]
        out.append(_upper_median(cand) if len(cand) >= MIN_INTERVALS else fallback)
    return out


def threshold(interval: int) -> int:
    """The longest interval that is not a gap."""
    return max(GAP_FLOOR, GAP_FACTOR * interval)


@dataclass
class Store:
    """Readings with their reading intervals and the time each represents."""

    readings: list[Reading]
    detected: int = DEFAULT_INTERVAL
    iv: list[int] = field(default_factory=list)
    spans: list[int] = field(default_factory=list)

    @classmethod
    def build(cls, readings: list[Reading]) -> Store:
        """The store of readings in strictly increasing measurement time (refused otherwise: an unsorted list or a
        repeated time would give negative or zero weights without any error)."""
        ts = [r.ts for r in readings]
        if any(not isinstance(t, int) or isinstance(t, bool) for t in ts):
            raise ValueError("measurement times must be whole seconds (int)")
        if any(b <= a for a, b in pairwise(ts)):
            raise ValueError("readings must be in strictly increasing measurement time (one per time)")
        det = detected_interval(ts)
        iv = intervals(ts, det)
        spans = []
        for k in range(len(ts)):
            if k + 1 < len(ts) and ts[k + 1] - ts[k] <= threshold(iv[k]):
                spans.append(ts[k + 1] - ts[k])
            else:
                spans.append(iv[k])
        return cls(readings, det, iv, spans)

    @property
    def ts(self) -> list[int]:
        """The measurement times, in order."""
        return [r.ts for r in self.readings]

    def segments(self, start: int, end: int, clipped: bool = True) -> list[tuple[int, int, Reading]]:
        """The covered time in [start, end) as (from, to, reading).

        clipped=True (the CGM Report, "sensor active"): every reading's time, clipped to the window, including the
        reading before the window that reaches into it. clipped=False (the statistics sensors): the readings measured
        in the window, each weighted by its time, the window's last reading by its own interval (section 4.2)."""
        ts = self.ts
        i, j = bisect.bisect_left(ts, start), bisect.bisect_left(ts, end)
        out = []
        if clipped:
            for k in range(max(0, i - 1), j):
                a, b = max(ts[k], start), min(ts[k] + self.spans[k], end)
                if b > a:
                    out.append((a, b, self.readings[k]))
        else:
            for k in range(i, j):
                w = self.spans[k] if k < j - 1 else self.iv[k]
                out.append((ts[k], ts[k] + w, self.readings[k]))
        return out


# ---------------------------------------------------------------------------------------------- the metrics
def _sqrt(f: Fraction) -> Decimal:
    return (Decimal(f.numerator) / Decimal(f.denominator)).sqrt()


def gri_zone(gri: float | None) -> str | None:
    """The zone of a displayed GRI value: A up to 20, B up to 40, C up to 60, D up to 80, E above."""
    if gri is None:
        return None
    return "A" if gri <= 20 else "B" if gri <= 40 else "C" if gri <= 60 else "D" if gri <= 80 else "E"


def metrics(segs: list[tuple[int, int, Reading]], period_s: int) -> dict:
    """Exact metrics of weighted segments over a period of period_s seconds (None values without data).

    The time in each range and the GRI take every reading; the mean, SD, CV, GMI, minimum and maximum the measured
    readings only: a reading at the sensor's limit has no value (a GlucoCraft rule), so a period with limit readings alone has
    the time in each range and None for these."""
    if period_s < 0:
        raise ValueError("the period cannot be negative")
    covered = sum(b - a for a, b, _ in segs)
    out: dict = {
        "covered_s": covered,
        "active": Fraction(min(100 * covered, 100 * period_s), period_s) if period_s else Fraction(0),
    }
    if not covered:
        return out | {"mean": None}

    def pct(f) -> Fraction:
        return Fraction(100 * sum(b - a for a, b, r in segs if f(r.mg)), covered)

    measured = [(a, b, r) for a, b, r in segs if r.limit is None]
    weight = sum(b - a for a, b, _ in measured)
    values: dict = dict.fromkeys(("mean", "sd", "cv", "gmi", "gmi_mmol_mol", "min", "max"))
    if weight:
        mean = sum((b - a) * r.mg for a, b, r in measured) / weight
        var = sum((b - a) * (r.mg - mean) ** 2 for a, b, r in measured) / weight
        sd = _sqrt(var) if len({r.ts for _, _, r in measured}) > 1 else None  # a single reading has no spread
        mean_d = Decimal(mean.numerator) / Decimal(mean.denominator)
        values = {
            "mean": mean,
            "sd": sd,
            "cv": sd / mean_d * 100 if mean and sd is not None else None,
            "gmi": Fraction("3.31") + Fraction("0.02392") * mean,
            "gmi_mmol_mol": round_half_up(Decimal("12.71") + Decimal("4.70587") * mean_d / FACTOR, 0),
            "min": min(r.mg for _, _, r in measured),
            "max": max(r.mg for _, _, r in measured),
        }
    vlow, low = pct(lambda v: v < VERY_LOW), pct(lambda v: VERY_LOW <= v < LOW)
    high, vhigh = pct(lambda v: HIGH < v <= VERY_HIGH), pct(lambda v: v > VERY_HIGH)
    gri = min(Fraction(100), 3 * vlow + Fraction(12, 5) * low + Fraction(8, 5) * vhigh + Fraction(4, 5) * high)
    return (
        out
        | values
        | {
            "very_low": vlow,
            "low": low,
            "tir": pct(lambda v: LOW <= v <= HIGH),
            "titr": pct(lambda v: LOW <= v <= TIGHT_HIGH),
            "high": high,
            "very_high": vhigh,
            "below_70": pct(lambda v: v < LOW),
            "above_180": pct(lambda v: v > HIGH),
            "gri": gri,
            "gri_hypo": vlow + Fraction(4, 5) * low,
            "gri_hyper": vhigh + Fraction(1, 2) * high,
        }
    )


def limit_counts(store: Store, start: int, end: int, segs: list[tuple[int, int, Reading]] | None = None) -> dict:
    """The LO / HI readings measured in [start, end), and the whole seconds (up) they stand for in the segments."""
    rs = [r for r in store.readings if start <= r.ts < end and r.limit]
    return {
        "low": sum(1 for r in rs if r.limit == "low"),
        "high": sum(1 for r in rs if r.limit == "high"),
        "low_s": sum(b - a for a, b, r in segs or [] if r.limit == "low"),
        "high_s": sum(b - a for a, b, r in segs or [] if r.limit == "high"),
    }


def gmi_shown(period_s: int, active: Fraction) -> bool:
    """GMI is shown for 14 days or more with at least 70% sensor active, compared before rounding."""
    return period_s >= 14 * 86400 and active >= 70


def statistics_sensor(store: Store, now: int, period_s: int) -> dict:
    """The values of a statistics period ending now (the statistics sensors' method: reading windows)."""
    segs = store.segments(now - period_s, now + 1, clipped=False) if store.readings else []
    m = metrics(segs, period_s)
    lim = limit_counts(store, now - period_s, now + 1, segs)
    m["readings"] = len(segs)
    m["limits"] = lim
    m["gmi_shown"] = m.get("mean") is not None and gmi_shown(period_s, m["active"])
    return m


# ------------------------------------------------------------------------------------- local days and the night
def local_midnight(ts: int, tz: ZoneInfo, days_back: int = 0) -> int:
    """The local midnight that starts the day of ts, days_back days earlier (whole local days)."""
    d = datetime.fromtimestamp(ts, tz).date() - timedelta(days=days_back)
    return int(datetime(d.year, d.month, d.day, tzinfo=tz).timestamp())


def day_start(date_, tz: ZoneInfo) -> int:
    """The epoch seconds of a local date's midnight."""
    return int(datetime(date_.year, date_.month, date_.day, tzinfo=tz).timestamp())


def _boundaries(start: int, end: int, tz: ZoneInfo) -> list[int]:
    """Every local midnight and 06:00 between start and end."""
    out = []
    d = datetime.fromtimestamp(start, tz).date() - timedelta(days=1)
    last = datetime.fromtimestamp(end, tz).date() + timedelta(days=1)
    while d <= last:
        for h in (0, NIGHT_END_HOUR):
            t = int(datetime(d.year, d.month, d.day, h, tzinfo=tz).timestamp())
            if start < t < end:
                out.append(t)
        d += timedelta(days=1)
    return sorted(out)


def split(segs: list[tuple[int, int, Reading]], cuts: list[int]) -> list[tuple[int, int, Reading]]:
    """The segments cut at the given times."""
    out = []
    for a, b, r in segs:
        i = bisect.bisect_right(cuts, a)
        while i < len(cuts) and cuts[i] < b:
            out.append((a, cuts[i], r))
            a = cuts[i]
            i += 1
        out.append((a, b, r))
    return out


def is_night(ts: int, tz: ZoneInfo) -> bool:
    """True for a time in the night block, 00:00 to 05:59 local."""
    return datetime.fromtimestamp(ts, tz).hour < NIGHT_END_HOUR


# ------------------------------------------------------------------------------------------------- events
def hypo_events(segs: list[tuple[int, int, Reading]], limit: int) -> list[tuple[int, int, Fraction]]:
    """Hypoglycemia events [(start, seconds, lowest mg/dL)] over contiguous covered segments in time order."""
    events = []
    run_start = ok_start = None
    run_cov = ok_cov = 0
    in_event, ev_start, nadir, prev_end = False, 0, None, None
    for a, b, r in segs:
        v = r.mg
        if prev_end is not None and a > prev_end:  # no data in between: a gap ends the event
            if in_event:
                events.append((ev_start, prev_end - ev_start, nadir))
            run_start = ok_start = None
            run_cov = ok_cov = 0
            in_event, nadir = False, None
        if v < limit:
            ok_start, ok_cov = None, 0
            if run_start is None:
                run_start, run_cov = a, 0
                if not in_event:
                    nadir = None
            run_cov += b - a
            nadir = v if nadir is None else min(nadir, v)
            if not in_event and run_cov >= EVENT_S:
                in_event, ev_start = True, run_start
        else:
            run_start, run_cov = None, 0
            if in_event:
                if v >= LOW:
                    if ok_start is None:
                        ok_start, ok_cov = a, 0
                    ok_cov += b - a
                    if ok_cov >= EVENT_S:
                        events.append((ev_start, ok_start - ev_start, nadir))
                        in_event, ok_start, ok_cov = False, None, 0
                else:
                    ok_start, ok_cov = None, 0
        prev_end = b
    if in_event:
        events.append((ev_start, prev_end - ev_start, nadir))
    return events


def extended_high_events(store: Store, upto: int) -> list[tuple[int, int]]:
    """Extended high-glucose events [(start, seconds)] of the readings measured before `upto`: steps 1-5 of the
    appendix method (a run above 250 covering 120 minutes starts an event at its first reading; readings from 181 to
    250 do not end it; it ends at the first reading of the first run at or below 180 that covers 15 minutes; a gap,
    or the end of the data, ends it at the end of its last reading before the gap, which covers its own interval)."""
    ts = store.ts
    n = bisect.bisect_left(ts, upto)
    events = []
    mode, run_start, run_cov, ev_start, low_start, low_cov = "idle", 0, 0, 0, None, 0
    for k in range(n):
        v = store.readings[k].mg
        gap_after = k + 1 >= n or ts[k + 1] - ts[k] > threshold(store.iv[k])
        span = store.spans[k]
        if mode != "event":
            if v > VERY_HIGH:
                if mode == "idle":
                    mode, run_start, run_cov = "run", ts[k], 0
                run_cov += span
                if run_cov >= XHIGH_START_S:
                    mode, ev_start, low_start, low_cov = "event", run_start, None, 0
            else:
                mode = "idle"
        else:
            if v <= HIGH:
                if low_start is None:
                    low_start, low_cov = ts[k], 0
                low_cov += span
                if low_cov >= XHIGH_END_S:
                    events.append((ev_start, low_start - ev_start))
                    mode, low_start = "idle", None
                    continue
            else:
                low_start, low_cov = None, 0
        if gap_after:
            if mode == "event":
                events.append((ev_start, ts[k] + span - ev_start))
            mode, low_start = "idle", None
    return events


# ----------------------------------------------------------------------------------------------- the CGM report
def report(store: Store, tz: ZoneInfo, now: int, ndays: int) -> dict:
    """The report values for the ndays full local days ending at the last midnight before `now`."""
    if int(ndays) != ndays or ndays < 1:
        raise ValueError("a report covers one or more whole days")
    end = local_midnight(now, tz)
    start = local_midnight(end, tz, ndays)
    segs = store.segments(start, end) if store.readings else []
    m = metrics(segs, end - start)
    out = {"start": start, "end": end, "period": m, "limits": limit_counts(store, start, end, segs)}
    if m["covered_s"]:
        out["days_with_data"] = len(
            {datetime.fromtimestamp(a, tz).date() for a, b, _ in split(segs, _boundaries(start, end, tz))}
        )
        out["gmi_shown"] = ndays >= 14 and m["active"] >= 70
        out["gri_shown"] = ndays >= 14
    # lows and highs by night (00:00-05:59) and day
    parts = {"night": [0, 0, 0, 0, 0], "day": [0, 0, 0, 0, 0]}  # covered, <70, <54, >180, >250
    for a, b, r in split(segs, _boundaries(start, end, tz)):
        p = parts["night" if is_night(a, tz) else "day"]
        w = b - a
        p[0] += w
        p[1] += w * (r.mg < LOW)
        p[2] += w * (r.mg < VERY_LOW)
        p[3] += w * (r.mg > HIGH)
        p[4] += w * (r.mg > VERY_HIGH)
    out["night_day"] = {
        f"{part}_{name}": (Fraction(100 * p[i], p[0]) if p[0] else None)
        for part, p in parts.items()
        for i, name in ((1, "below_70"), (2, "below_54"), (3, "above_180"), (4, "above_250"))
    }
    # hypoglycemia events over all data up to the period's end, counted where they start
    allsegs = store.segments(store.readings[0].ts, end) if store.readings else []
    ev70 = hypo_events(allsegs, LOW)
    ev54 = hypo_events(allsegs, VERY_LOW)
    in70 = [e for e in ev70 if start <= e[0] < end]
    in54 = [e for e in ev54 if start <= e[0] < end]
    rows = []
    for s, dur, nadir in in70:
        level = 2 if any(s <= e[0] < s + dur for e in ev54) else 1
        lo_vals = {r.mg for a, b, r in allsegs if r.limit == "low" and a < s + dur and b > s}
        d = datetime.fromtimestamp(s, tz)
        rows.append(
            {
                "start": s,
                "minutes": Fraction(dur, 60),
                "nadir": nadir,
                "level": level,
                "nadir_is_lo": nadir in lo_vals,
                "hour": Fraction(d.hour * 60 + d.minute, 60),
            }
        )
    out["lows"] = {
        "events": len(in70),
        "events_54": len(in54),
        "events_night": sum(1 for e in in70 if is_night(e[0], tz)),
        "mean_minutes": Fraction(sum(e[1] for e in in70), 60 * len(in70)) if in70 else None,
        "longest_minutes": Fraction(max(e[1] for e in in70), 60) if in70 else None,
        "list": rows,
    }
    xh = [e for e in extended_high_events(store, end) if start <= e[0] < end]
    out["extended_high"] = {
        "events": len(xh),
        "total_minutes": Fraction(sum(e[1] for e in xh), 60),
        "longest_minutes": Fraction(max(e[1] for e in xh), 60) if xh else None,
    }
    out["quality"] = quality(store, tz, start, end)
    return out


def quality(store: Store, tz: ZoneInfo, start: int, end: int) -> dict:
    """Sensor active per local day, and the data gaps that overlap the period."""
    per_day = []
    d = datetime.fromtimestamp(start, tz).date()
    while day_start(d, tz) < end:
        a, b = day_start(d, tz), day_start(d + timedelta(days=1), tz)
        cov = sum(y - x for x, y, _ in store.segments(a, b)) if store.readings else 0
        per_day.append(Fraction(100 * cov, b - a))
        d += timedelta(days=1)
    ts = store.ts
    gaps = [
        (ts[k], ts[k + 1])
        for k in range(len(ts) - 1)
        if ts[k + 1] - ts[k] > threshold(store.iv[k]) and ts[k + 1] > start and ts[k] < end
    ]
    return {
        "per_day": per_day,
        "gaps": len(gaps),
        "longest_gap_minutes": Fraction(max(b - a for a, b in gaps), 60) if gaps else None,
    }


def weekly(store: Store, tz: ZoneInfo, now: int, weeks: int = 26) -> list[dict | None]:
    """The weekly statistics of `weeks` weeks of 7 local days ending at the last midnight, newest first; a week with
    sensor active below 70% of it has none (the week rule: the enough-data rule) but keeps its sensor active
    ("enough" False); a week without data is None."""
    end = local_midnight(now, tz)
    out = []
    for k in range(weeks):
        b = local_midnight(end, tz, 7 * k)
        a = local_midnight(end, tz, 7 * (k + 1))
        segs = store.segments(a, b, clipped=False)
        m = metrics(segs, b - a)
        m["enough"] = m["covered_s"] * 100 >= WEEK_MIN_PCT * (b - a)
        out.append(m if m["covered_s"] else None)
    return out


def retention_cutoff(store: Store, tz: ZoneInfo, now: int) -> int:
    """The first moment kept: local midnight 200 days before the day of the earlier of now and the newest reading."""
    ref = min(now, store.readings[-1].ts) if store.readings else now
    return local_midnight(ref, tz, RETENTION_DAYS)


def merge_sources(primary: list[tuple[int, int, Reading]], secondary: list[tuple[int, int, Reading]]) -> list[Reading]:
    """Merge two sources by measurement time (docs/SYSTEM_DESIGN.md section 3).

    primary / secondary: [(arrival time, measured, reading)]. The same measurement (within half the shorter known
    interval) is never stored twice and the primary's copy wins; the second source fills only real gaps (no stored
    reading within 3/4 of the primary's interval). A source's interval is the median of its recent intervals, known
    once 3 exist; the primary's is 300 s until then.
    """
    arrivals = sorted(
        [(t, 0, m, r) for t, m, r in primary] + [(t, 1, m, r) for t, m, r in secondary], key=lambda x: (x[0], x[1])
    )
    stored: list[Reading] = []
    times: dict[int, list[int]] = {0: [], 1: []}

    def known(src: int) -> int | None:
        t = times[src][-21:]
        d = [b - a for a, b in pairwise(t) if 20 <= b - a <= 900]
        return _upper_median(d) if len(d) >= 3 else None

    for _, src, measured, r in arrivals:
        if times[src] and measured <= times[src][-1]:
            continue  # the same measurement again, or an older one
        times[src].append(measured)
        kp, ks = known(0), known(1)
        ip = kp or DEFAULT_INTERVAL
        same = max(15, min(kp, ks) // 2) if kp and ks else 15
        tsl = [x.ts for x in stored]
        i = bisect.bisect_left(tsl, measured)
        near = [j for j in (i - 1, i) if 0 <= j < len(stored)]
        best = min(near, key=lambda j: abs(stored[j].ts - measured)) if near else None
        dist = abs(stored[best].ts - measured) if best is not None else None
        if src == 0:
            if dist is not None and dist <= same:
                del stored[best]
        elif dist is not None and dist < 0.75 * ip:
            continue
        stored.insert(bisect.bisect_left([x.ts for x in stored], measured), Reading(measured, r.mg, r.limit))
    return stored


# ------------------------------------------------------------------------------------ closed-form sine waves
# A reading counts in the range of its value rounded to the source precision. On the exact (unrounded) wave the range
# limits therefore lie half a display step from the consensus values: below 54 mg/dL is below 53.5 (shown as 53 or
# less), in mmol/L below 2.95 (shown as 2.9 or less; 3.0 is 54 mg/dL by the published pairs).
SINE_LIMITS = {
    "mg/dL": {VERY_LOW: 53.5, LOW: 69.5, TIGHT_HIGH: 140.5, HIGH: 180.5, VERY_HIGH: 250.5},
    UNIT_MMOLL: {VERY_LOW: 2.95, LOW: 3.85, TIGHT_HIGH: 7.85, HIGH: 10.05, VERY_HIGH: 13.95},
}


def _share_below(level: float, offset: float, amplitude: float) -> float:
    """The share of time (0-1) that offset + amplitude x sin(theta), theta over whole cycles, is below level."""
    if amplitude == 0:
        return 1.0 if offset < level else 0.0
    x = (level - offset) / amplitude
    return 0.0 if x <= -1 else 1.0 if x >= 1 else 0.5 + math.asin(x) / math.pi


def sine_statistics(offset: float, amplitude: float, unit: str) -> dict:
    """The closed-form statistics of the wave offset + amplitude x sin(...) over whole cycles (tools/synth/sine.py,
    offset and amplitude in the source unit), as GlucoCraft should show them for the sampled series.

    The time in each range in percent (each reading counted in the range of its rounded value, SINE_LIMITS), the mean
    (the offset, in mg/dL), the standard deviation (amplitude / sqrt(2): the mean of sin^2 over whole cycles is 1/2),
    the CV, the GMI and the GRI from these. The sampled series differs from them only by the sampling (a reading
    stands for its whole interval) and the rounding to the source precision; tests/test_synth_sine.py states the
    bounds.
    """
    unit = unit_name(unit)
    limits = SINE_LIMITS["mg/dL" if unit != UNIT_MMOLL else UNIT_MMOLL]
    below = {k: 100 * _share_below(v, offset, amplitude) for k, v in limits.items()}
    factor = float(FACTOR) if unit == UNIT_MMOLL else 1.0
    mean, sd = offset * factor, amplitude * factor / math.sqrt(2)
    vlow, low = below[VERY_LOW], below[LOW] - below[VERY_LOW]
    high, vhigh = below[VERY_HIGH] - below[HIGH], 100 - below[VERY_HIGH]
    return {
        "very_low": vlow,
        "low": low,
        "tir": below[HIGH] - below[LOW],
        "titr": below[TIGHT_HIGH] - below[LOW],
        "high": high,
        "very_high": vhigh,
        "below_70": below[LOW],
        "above_180": 100 - below[HIGH],
        "mean": mean,
        "sd": sd,
        "cv": 100 * sd / mean,
        "gmi": 3.31 + 0.02392 * mean,
        "gri": min(100.0, 3.0 * vlow + 2.4 * low + 1.6 * vhigh + 0.8 * high),
    }
