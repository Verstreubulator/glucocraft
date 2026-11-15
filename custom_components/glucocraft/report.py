# License: AGPL-3.0-only (LICENSE).
"""The calculations of the CGM Report; report_pdf.py draws them.

The report uses the consensus ranges only (Battelino et al., 2019; tight range Battelino et al., 2023), never the
ranges in the settings (those appear only in the optional custom appendix). The report
period is full local days ending at the last midnight. Page 1 is always the 14-day summary; a longer
chosen period adds a longer-term overview (14-day blocks, the period's AGP curve, the previous period).

Data rules are GlucoCraft's (one system with the Statistics page):
  * weights in seconds: each reading represents the seconds until the next reading, or its own reading interval when
    the next interval is a gap (gaps.weights, the one gap rule: longer than max(15 minutes, 3 x the reading interval of
    that stretch)), clipped to the period. Every percentage, mean, SD, CV, GMI and GRI, the night and day shares, the
    2-hour patterns and each day's mean and data % come from statistics.compute_period_stats, the calculation of the
    statistics sensors, with these seconds as the weights; time without data is left out of all of them;
  * display bins: the AGP percentile curve (15-minute time-of-day bins) and the daily profiles (10-minute slots of the
    local clock) take one value per whole minute that a reading covers (the minute grid). These bins only place the
    values for drawing; they weigh nothing in the statistics;
  * sensor active: the seconds the readings represent (the same rule), as a share of the period;
  * enough data: a period's statistics (time in ranges, mean, SD, CV, GMI, GRI, lowest and highest value, the events,
    the comparison and every other value of the period) are given only when its sensor active, unrounded, is at least
    70 % (Battelino et al., 2019, Table 2, for 14 days; for every period length a GlucoCraft rule); below it they are
    None and the period carries enough_data False, its sensor active and, for a period that ends at the last
    midnight, about how much more data would be needed with no gaps (more_needed). The graphs (the glucose profile,
    the daily profiles and the hourly log of measured values) are drawn from the data either way;
  * readings at the sensor's limit (LO / HI) have no value: they are left out of the mean, SD, CV, GMI, the lowest
    and highest value and the curves, and count as very low / very high in the time in ranges and the events
    (statistics.compute_period_stats); their number and time are given per period (limit_readings, limit_seconds),
    and the curves mark where they lie (profile_limits, daily_limits). No consensus document addresses them; the
    report software of Abbott Diabetes Care shows them at the limit (Abbott Diabetes Care, 2018, p. 7). GlucoCraft
    rule;
  * GMI only for 14 days or more with at least 70 % data (Battelino et al., 2019; Bergenstal et al., 2018);
  * GRI (Klonoff et al., 2023) on the consensus bands, for 14 days or more with at least 70 % data;
  * the 6-month report has no previous period (with it, the report would need 2 x 180 = 360 days of readings, and
    its minute grid would begin EVENT_LOOKBACK_DAYS earlier still; the retention is 200 days);
    a laboratory A1C whose 90-day window starts before the oldest day kept has no CGM comparison.

Sections besides the summary: the high-glucose section (time above 180 / 250 at night and by day, highs_detail), the
data quality page (sensor active per day, gaps, LO / HI readings, sensor changes and warm-ups, data_quality), the
26-week weekly trend for the 90-day and 6-month reports (weekly_trend: the weekly statistics of
statistics.compute_weekly_trends on the consensus ranges, with the enough-data rule: a week with less than 70 %
sensor active has no values), the full report's summary of
the hypoglycemia events per period (low_event_summary), the share of the readings from the second source
(second_source_share, for About This Report), and the two optional
appendices: "Custom ranges and goals" (custom_appendix: the ranges and goals in the settings, met / not met only with
the 14-day / 70 % rule) and "Measures defined for research" (extended_high_events: Battelino et al., 2023, p. 48, with
GlucoCraft's method in steps 1-6).

build_report(...) runs in the executor, one build at a time (_BUILD_LOCK); it reads only the reading list it is given.
"""

from __future__ import annotations

import bisect
import math
import threading
import time
from datetime import datetime, timedelta, tzinfo
from typing import Any

from .const import (
    AGP_RANGES,
    COMPARE,
    CONSENSUS_TARGET_VALUES,
    DEFAULT_RETENTION_DAYS,
    ENOUGH_DATA_PCT,
    FALLBACK_INTERVAL_SECONDS,
    GMI_MIN_DATA_PCT,
    GMI_MIN_PERIOD_SECONDS,
    SOURCE_SECONDARY,
    public_agp_ranges,
)
from .gaps import gap_threshold, local_intervals
from .gaps import weights as gap_weights
from .i18n import t
from .regional import Regional, a1c_to_mmol_mol, month_year, weekday_name
from .statistics import PeriodStats, compute_period_stats, compute_weekly_trends, enough_data, gmi_shown, sensor_active
from .units import gmi_goal_mmol_mol, round_half_up, shown_against

NO_PREVIOUS_DAYS = 180  # the 6-month report: no previous period
# ENOUGH_DATA_PCT (const.py): a period's statistics are given only with at least this sensor active, compared before
# rounding: the 70 % of Battelino et al. (2019, Table 2) for 14-day periods, applied to every period length (GlucoCraft
# rule). The GMI's data rule (GMI_MIN_DATA_PCT, statistics.PeriodStats.sensor_active_ok) is defined from it, so that
# one rule holds everywhere.
# the consensus thresholds the Report page's zones and chart lines use (report data "zones"): very low, low, high and
# very high; the tight range is not drawn on the page
PAGE_ZONES = ("range_very_low", "range_low", "range_high", "range_very_high")
# laboratory A1C compared with the GMI (Bergenstal et al., 2018) of the 90 days before each result (the 90-day window
# is a GlucoCraft rule)
LAB_WINDOW_DAYS = 90

PCTS = (5, 25, 50, 75, 95)
# The display bins of the curves (minute grid): the AGP curve's 15-minute bins of the time of day, each bin's
# percentiles only from at least AGP_MIN_VALUES values of its pooled window, and the daily profiles' 10-minute slots of
# each day's local clock. Two minutes of the grid more than GRID_STEP_S seconds apart lie across a data gap.
AGP_BINS = 96
AGP_MIN_VALUES = 3
DAY_SLOT_MINUTES = 10
DAY_SLOTS = 1440 // DAY_SLOT_MINUTES
GRID_STEP_S = 60
STANDARD_DAYS = 14  # the AGP standard (Battelino et al., 2019)
BLOCK_DAYS = 14  # longer periods are shown in consecutive 14-day blocks
# The consensus ranges (mg/dL) in the keys of the custom appendix, which compares the ranges in the settings with them
CONSENSUS_RANGES = {
    "very_low": AGP_RANGES["range_critical_low"],
    "low": AGP_RANGES["range_low"],
    "tight_low": AGP_RANGES["range_ideal_low"],
    "tight_high": AGP_RANGES["range_ideal_high"],
    "high": AGP_RANGES["range_high"],
    "very_high": AGP_RANGES["range_critical_high"],
}
# The limits of the two hypoglycemia levels: below the consensus low limit (level 1, 70 mg/dL) and below the very low
# limit (level 2, 54 mg/dL) (Battelino et al., 2019; Danne et al., 2017, Section 4)
LEVEL1_LIMIT = AGP_RANGES["range_low"]
LEVEL2_LIMIT = AGP_RANGES["range_critical_low"]
EVENT_MIN = 15  # hypoglycemia event (Danne et al., 2017, Section 4): starts after at least 15
# consecutive minutes below the limit (level 1: 70 mg/dL; level 2: 54 mg/dL), ends
# after at least 15 consecutive minutes at or above EVENT_END (every level); see _low_scan
EVENT_END = 70  # the end limit of every event: "readings for 15 min at >=70 mg/dL" (Danne et al., 2017, Section 4)
# night = 00:00-05:59 local ("nocturnal (0000 h to 0559 h)", Battelino et al., 2023, Panel 2; Danne et al., 2017 call
# it the sleep block)
NIGHT_END_HOUR = 6
MINUTES_PER_PERCENT = 1440 / 100  # 1% of time = 14.4 minutes per day (the time per day beside a percentage)
TREND_WEEKS = 26  # the weekly trend of the 90-day and 6-month reports
TREND_FROM_DAYS = 90  # reports of this length and longer carry the weekly trend
# the periods of the full report's hypoglycemia event summary (low_event_summary): those inside the report's period
EVENT_SUMMARY_DAYS = (14, 30, 90, 180)
# Extended high-glucose events (research appendix; Battelino et al., 2023, p. 48): start after 120 minutes above 250
# mg/dL, end with 15 minutes at or below 180 mg/dL (GlucoCraft's method, extended_high_events)
XHIGH_LIMIT, XHIGH_END, XHIGH_START_S, XHIGH_END_S = 250, 180, 120 * 60, 15 * 60
# The minute grid of a report starts this many days before the earliest period it shows, so that a hypoglycemia event
# already running when a period, block or day begins is found with its own start (and is then counted where it starts,
# not again at the window's first minute)
EVENT_LOOKBACK_DAYS = 1


# ------------------------------------------------------------------ the minute grid

# The regional settings of the report being built (regional.py): module state, as report_pdf keeps the settings of
# its render. build_report sets them for the time of the build and resets them to the defaults after it, and holds
# _BUILD_LOCK meanwhile, so that builds run one at a time whoever calls them (they run in Home Assistant's executor
# threads). A plain lock: build_report never builds again from inside a build.
_REGIONAL = Regional()
_BUILD_LOCK = threading.Lock()


def _rg() -> Regional:
    """Return the regional settings of the report being built (the defaults outside build_report)."""
    return _REGIONAL


def _r(x: float, decimals: int = 0):
    """Round halves away from zero (units.round_half_up), as every display does; whole numbers as int."""
    v = round_half_up(x, decimals)
    return int(v) if decimals == 0 else v


def _one_decimal(x: float | None) -> float | None:
    """A percentage of the statistics (unrounded, statistics.PeriodStats) as the report shows it: one decimal, rounded
    once."""
    return None if x is None else round_half_up(x, 1)


def _active_shown(active: float) -> float:
    """Sensor active as the report shows it beside the 70 % rule it is compared with (unrounded): one decimal, or two
    where one decimal would show the rule's value that it does not meet (units.shown_against: 69.96%)."""
    return shown_against(active, (ENOUGH_DATA_PCT,), 1)[0]


def more_needed(period_seconds: int, covered_seconds: float) -> list | None:
    """About how much more data a period that ends now would need, with no gaps, to reach the 70 % rule: the 70 % of
    its length less the time its readings cover, as ["days", n] (from one day on, whole days, halves up) or ["hours",
    n] (less than a day, whole hours, at least 1); None when the period has enough. An estimate: as the period moves
    on, its oldest part leaves it (the note says "about" and "with no gaps")."""
    need = ENOUGH_DATA_PCT / 100.0 * period_seconds - covered_seconds
    if need <= 0:
        return None
    if need >= 86400:
        return ["days", _r(need / 86400)]
    return ["hours", max(1, _r(need / 3600))]


def _limit_runs(kinds: list[set]) -> list[list]:
    """Return the runs of consecutive slots that hold readings at the sensor's limit: [[first slot, slot after the
    last, "low" | "high"], ...], ordered by their first slot (a slot with both kinds is in a run of each). `kinds`: the
    limit kinds of each slot (a set per slot). The page and the PDF mark each run along the graph's bottom (LO) or top
    (HI) edge."""
    runs = []
    for kind in ("low", "high"):
        start = None
        for i, ks in enumerate([*kinds, set()]):
            if kind in ks and start is None:
                start = i
            elif kind not in ks and start is not None:
                runs.append([start, i, kind])
                start = None
    return sorted(runs)


# The report's percentages that are shown beside a consensus target (page 1, the overview, the 14-day blocks, the
# previous period, the night and day of the low and high sections), by the target's goal key (const.GOAL_OPS)
_TARGET_OF = {
    "tir": "time_in_range",
    "below_70": "time_low",
    "very_low": "time_very_low",
    "above_180": "time_high",
    "very_high": "time_very_high",
    "cv": "cv",
}


# The statistics of a period (period_report): all None when the period does not have enough data
_PERIOD_VALUES = (
    "very_low",
    "low",
    "tir",
    "titr",
    "high",
    "very_high",
    "below_70",
    "above_180",
    "mean",
    "sd",
    "cv",
    "min",
    "max",
    "gmi",
    "gmi_mmol_mol",
    "gri",
    "gri_hypo",
    "gri_hyper",
    "gri_zone",
)


def _beside_target(key: str, x: float | None) -> float | None:
    """A percentage of the statistics (unrounded) as the report shows it beside its consensus target (_TARGET_OF; the
    targets of Battelino et al., 2019): one decimal, rounded once, or two where one decimal would show the target's
    value on the wrong side of it (units.shown_against: 69.97% beside "more than 70%", 3.97% beside "less than 4%")."""
    if x is None:
        return None
    return shown_against(x, CONSENSUS_TARGET_VALUES[_TARGET_OF[key]], 1)[0]


def _spans(ts_list: list, iv: list, i: int, j: int) -> list[int]:
    """Return the seconds readings i to j - 1 represent, as in the whole list (gaps.weights, the one gap rule): the time
    to the next reading, or the reading's own interval before a gap and for the last reading. The reading after j - 1
    takes part, so that each reading's time to the next is that of the whole list. Measurement times are whole
    seconds, so the spans are too."""
    return [int(w) for w in gap_weights(ts_list[i : j + 1], iv[i : j + 1])[: j - i]]


def _prepared(readings: list, interval: int, ts: list | None = None, iv: list | None = None) -> tuple[list, list]:
    """The reading times and the reading interval at each reading (gaps.local_intervals) unless given."""
    ts = ts if ts is not None else [r.timestamp for r in readings]
    iv = iv if iv is not None else local_intervals(ts, int(interval or FALLBACK_INTERVAL_SECONDS))
    return ts, iv


def _clipped(ts_list: list, iv: list, start_ts: int, end_ts: int):
    """Yield (reading index, from, to) for the readings whose time reaches into start_ts to end_ts: the seconds each
    represents (_spans), clipped to the window. The reading before the start can reach into it."""
    i = max(0, bisect.bisect_left(ts_list, start_ts) - 1)
    j = bisect.bisect_left(ts_list, end_ts)
    for k, span in zip(range(i, j), _spans(ts_list, iv, i, j), strict=True):
        a, e = max(ts_list[k], start_ts), min(ts_list[k] + span, end_ts)
        if e > a:
            yield k, a, e


def minute_grid(
    readings: list,
    interval: int,
    start_ts: int,
    end_ts: int,
    tz: tzinfo,
    ts: list | None = None,
    iv: list | None = None,
) -> dict:
    """The readings between start_ts and end_ts, with the seconds each represents, and the minute grid of the display
    bins.

    Each reading represents the seconds until the next reading, or its own reading interval when the next interval is
    a gap (gaps.weights), clipped to the window. Two views of the same seconds:
      _segs   per reading: (from, to, value, limit flag), for the hypoglycemia events, which follow the readings' own
              times;
      _parts  the same seconds cut at every quarter hour of the epoch: (from, to, reading index, local 15-minute
              time-of-day bin 0-95, local date). Every UTC offset is a multiple of 15 minutes, so every local midnight,
              06:00 and full hour is such a cut, and each part lies in one local day and one time-of-day bin. The
              statistics of a period, a day, the night and the day, and an hour weigh these seconds (_stats);
      _starts the start of each part, and _seg_starts that of each reading's segment, for bisection.
    The minute grid, for the display bins only (the AGP curve, the daily profiles): parallel lists with one entry per
    whole minute that a reading covers:
      mins  the epoch seconds of the minute;
      vals  the glucose value (mg/dL) of the reading that covers it (a reading at the sensor's limit has no value and
            no entry here);
      tod   the local 15-minute time-of-day bin, 0-95;
      lod   the local minute of the day on the clock, 0-1439 (on the fall-back day the repeated hour has the same
            minutes twice; on the spring-forward day the skipped hour has none).
    The minutes of the readings at the sensor's limit, for the marks at the graphs' edges, in the same form: lim_mins,
    lim_tod, lim_lod and lim_kind ("low" / "high").
    Also _readings, _ts / _iv (the reading times and intervals, for data_quality's gaps), _limits / _raw (per reading,
    for limit_counts and the hourly log) and _interval (the detected reading interval)."""
    ts_list, iv = _prepared(readings, interval, ts, iv)
    # local time of day per 15 minutes, not per UTC hour: in half-hour (and 45-minute) time zones a UTC hour spans two
    # local hours. Every UTC offset is a multiple of 15 minutes, so within one quarter hour the local clock advances
    # minute by minute with the epoch.
    q_cache: dict[int, tuple[int, int, Any]] = {}

    def quarter(q: int) -> tuple[int, int, Any]:
        """(time-of-day bin, local minute of the day, local date) of the quarter hour starting at epoch q."""
        if q not in q_cache:
            d = datetime.fromtimestamp(q, tz)
            q_cache[q] = (d.hour * 4 + d.minute // 15, d.hour * 60 + d.minute, d.date())
        return q_cache[q]

    segs: list[tuple[int, int, float, str | None]] = []
    parts: list[tuple[int, int, int, int, Any]] = []
    mins: list[int] = []
    vals: list[float] = []
    tod: list[int] = []
    lod: list[int] = []
    lim: dict[str, list] = {"lim_mins": [], "lim_tod": [], "lim_lod": [], "lim_kind": []}
    for k, a, e in _clipped(ts_list, iv, start_ts, end_ts):
        v, flag = readings[k].raw, readings[k].limit
        segs.append((a, e, v, flag))
        x = a
        while x < e:
            q = x - x % 900
            y = min(e, q + 900)
            b, q_min, d = quarter(q)
            parts.append((x, y, k, b, d))
            for m in range(-(-x // 60) * 60, y, 60):  # the whole minutes this part covers
                if flag is None:
                    mins.append(m)
                    vals.append(v)
                    tod.append(b)
                    lod.append(q_min + (m - q) // 60)
                else:  # a reading at the sensor's limit: no value, only its place for the marks
                    lim["lim_mins"].append(m)
                    lim["lim_tod"].append(b)
                    lim["lim_lod"].append(q_min + (m - q) // 60)
                    lim["lim_kind"].append(flag)
            x = y
    return {
        "mins": mins,
        "vals": vals,
        "tod": tod,
        "lod": lod,
        **lim,
        "_readings": readings,
        "_interval": int(interval or FALLBACK_INTERVAL_SECONDS),
        "_ts": ts_list,
        "_iv": iv,
        "_limits": [r.limit for r in readings],
        "_raw": [r.raw for r in readings],
        "_segs": segs,
        "_seg_starts": [s[0] for s in segs],
        "_parts": parts,
        "_starts": [p[0] for p in parts],
    }


def _within(g: dict, s_ts: int, e_ts: int) -> list[tuple[int, int, int, int, Any]]:
    """Return the parts of the grid (minute_grid "_parts") between s_ts and e_ts. A period of the report starts and
    ends at a local midnight, which is a cut of the parts; a part that reaches over another boundary keeps only its
    seconds inside."""
    parts, starts = g["_parts"], g["_starts"]
    i, j = max(0, bisect.bisect_right(starts, s_ts) - 1), bisect.bisect_left(starts, e_ts)
    out = parts[i:j]
    if out and out[0][0] < s_ts:
        _, e, *rest = out[0]
        out[:1] = [(s_ts, e, *rest)] if e > s_ts else []
    if out and out[-1][1] > e_ts:
        a, _, *rest = out[-1]
        out[-1] = (a, e_ts, *rest)
    return out


def _stats(g: dict, parts: list, period_seconds: int, ranges: dict | None = None) -> PeriodStats | None:
    """Return the statistics of parts of the grid (_within): statistics.compute_period_stats, the calculation of the
    statistics sensors, with each reading weighted by the seconds of its parts. period_seconds: the length of the
    period, for sensor active (0 for a time-of-day bin, which has none). ranges: compute_period_stats's range
    arguments (the consensus ranges, AGP_RANGES, unless given). None without data."""
    rs: list = []
    w: list[float] = []
    last = None
    for a, e, k, *_ in parts:  # the parts of one reading are consecutive
        if k != last:
            rs.append(g["_readings"][k])
            w.append(0.0)
            last = k
        w[-1] += e - a
    return compute_period_stats(rs, "report", period_seconds, g["_interval"], weights=w, **(ranges or AGP_RANGES))


def _period(g: dict, s_ts: int, e_ts: int) -> tuple[list, PeriodStats | None]:
    """Return the parts (_within) and the statistics on the consensus ranges (_stats) of the period from s_ts to e_ts.
    Kept in the grid, because a period, a 14-day block, the full report's pages and the monthly summary can share a
    window (the same day appears on the day pages and in the monthly summary)."""
    key = f"_period_{s_ts}_{e_ts}"
    if key not in g:
        parts = _within(g, s_ts, e_ts)
        g[key] = (parts, _stats(g, parts, e_ts - s_ts))
    return g[key]


def limit_counts(g: dict, s_ts: int, e_ts: int) -> tuple[dict[str, int], dict[str, int]]:
    """The readings at the sensor's limit (LO / HI) between s_ts and e_ts: ({"low": n, "high": n}, the whole seconds
    they represent in that time, up, as statistics.PeriodStats.limit_seconds)."""
    ts, lim = g.get("_ts") or [], g.get("_limits") or []
    i, j = bisect.bisect_left(ts, s_ts), bisect.bisect_left(ts, e_ts)
    counts = {"low": 0, "high": 0}
    for k in range(i, j):
        if lim[k] in counts:
            counts[lim[k]] += 1
    seconds = {"low": 0, "high": 0}
    segs, starts = g.get("_segs") or [], g.get("_seg_starts") or []
    for a, e, _, flag in segs[max(0, bisect.bisect_right(starts, s_ts) - 1) : bisect.bisect_left(starts, e_ts)]:
        if flag in seconds:
            seconds[flag] += max(0, min(e, e_ts) - max(a, s_ts))
    return counts, {k: math.ceil(v) for k, v in seconds.items()}


def _pct(sorted_vals: list[float], p: float) -> float:
    """Return the p-th percentile of sorted values, interpolated linearly between the two nearest ranks."""
    k = (len(sorted_vals) - 1) * p / 100.0
    lo = math.floor(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


# ------------------------------------------------------------------ one period
def period_report(
    g: dict, start_local: datetime, ndays: int, daily: bool = False, profile: bool = True, ends_now: bool = False
) -> dict:
    """Consensus statistics, the AGP percentile profile (profile=True) and (daily=True) daily profiles for ndays local
    days from start_local.

    The statistics weigh each reading by the seconds it represents in the period (_stats: the statistics sensors'
    calculation), and are given only when the period has enough data (sensor active at least 70 %, unrounded;
    ENOUGH_DATA_PCT): below it every statistic is None, enough_data is False and, for a period that ends at the last
    midnight (ends_now), more_needed says about how much more data it would need. The AGP curve and the daily profiles
    are display bins of the minute grid, drawn either way: one value per whole minute, placed in 15-minute bins of the
    time of day (percentiles) and 10-minute slots of each day's clock (means), only to draw the curves, with the runs of
    bins and slots that hold readings at the sensor's limit (profile_limits, daily_limits). Their values are kept
    unrounded (mg/dL): the page and the PDF round each once, in the display unit."""
    end_local = start_local + timedelta(days=ndays)
    s_ts, e_ts = int(start_local.timestamp()), int(end_local.timestamp())
    parts, st = _period(g, s_ts, e_ts)
    lim_n, lim_s = limit_counts(g, s_ts, e_ts)
    # sensor active: the covered seconds of the statistics (the gap rule) as a share of the period. The 70% rule
    # compares the unrounded value; it is shown rounded once, on its side of the rule (_active_shown)
    active = st.sensor_active_exact if st is not None else 0.0
    covered = st.covered_seconds if st is not None else 0.0
    enough = st is not None and st.sensor_active_ok
    rep: dict[str, Any] = {
        "period_start": start_local.strftime("%Y-%m-%d"),
        "period_end": (end_local - timedelta(days=1)).strftime("%Y-%m-%d"),
        "days": ndays,
        "active": _active_shown(active),
        "enough_data": enough,
        "more_needed": more_needed(e_ts - s_ts, covered) if ends_now else None,
        # readings at the sensor's limit (LO / HI): left out of the values, counted in the time in ranges (GlucoCraft
        # rule); their number and time are a fact of the data, given with or without enough data
        "limit_readings": lim_n,
        "limit_seconds": lim_s,
        "days_with_data": len({p[4] for p in parts}),
    }
    if st is None:
        rep["tir"] = None
        return rep
    # the statistics with enough data; without it every statistic of the period is None (the time in ranges, the
    # metrics, the lowest and highest value)
    rep.update(_period_values(st, ndays) if enough else dict.fromkeys(_PERIOD_VALUES))
    if profile:
        rep["profile"], rep["profile_limits"] = _agp_profile(g, s_ts, e_ts)
    if daily:
        rep.update(_daily_profiles(g, start_local, ndays))
    return rep


def _period_values(st: PeriodStats, ndays: int) -> dict[str, Any]:
    """Return the statistics of a period with enough data (period_report): the time in ranges, the metrics, the
    lowest and highest value, the time per day of each percentage, the GMI and the GRI."""
    # the percentages rounded once for display, to one decimal (each from the unrounded seconds, as on the
    # statistics sensors); those with a consensus target on its side of the target (_beside_target)
    values: dict[str, Any] = {
        "very_low": _beside_target("very_low", st.time_critical_low),
        "low": _one_decimal(st.time_low),
        "tir": _beside_target("tir", st.pct_time_in_target_range),
        "titr": _one_decimal(st.pct_time_in_ideal_range),
        "high": _one_decimal(st.time_high),
        "very_high": _beside_target("very_high", st.time_critical_high),
        "below_70": _beside_target("below_70", st.time_below_low),
        "above_180": _beside_target("above_180", st.time_above_high),
        # the mean and the SD unrounded: every display rounds them once, in its unit (units.fmt)
        "mean": st.mean,
        "sd": st.std_dev,
        "cv": _beside_target("cv", st.cv),
        # the lowest and highest measured value as stored (mg/dL, one decimal), not rounded to whole mg/dL here
        # (a value would then be rounded twice in mmol/L): rounded once where shown, in the display unit
        "min": st.min_value,
        "max": st.max_value,
    }
    # the time per day each percentage represents (1% of time = 14.4 minutes per day): from the unrounded share,
    # rounded once to whole minutes, shown beside the percentages of page 1 and the overview
    values["per_day"] = {
        key: _r(pct * MINUTES_PER_PERCENT)
        for key, pct in (
            ("very_low", st.time_critical_low),
            ("low", st.time_low),
            ("tir", st.pct_time_in_target_range),
            ("titr", st.pct_time_in_ideal_range),
            ("high", st.time_high),
            ("very_high", st.time_critical_high),
            ("below_70", st.time_below_low),
            ("above_180", st.time_above_high),
        )
        if pct is not None
    }
    gmi = gmi_shown(st, ndays * 86400)  # 14 days or more (local days) with at least 70% sensor active
    values["gmi"] = gmi
    # GMI in mmol/mol from the same mean (Bergenstal et al., 2018), for the A1C / GMI unit setting
    values["gmi_mmol_mol"] = st.gmi_mmol_mol if gmi is not None else None
    # the GRI over 14 days or more, from the unrounded band percentages (statistics.gri), as on the dashboard
    if ndays * 86400 >= GMI_MIN_PERIOD_SECONDS:
        values["gri"], values["gri_hypo"] = st.gri, st.gri_hypo
        values["gri_hyper"], values["gri_zone"] = st.gri_hyper, st.gri_zone
    return values


def _agp_profile(g: dict, s_ts: int, e_ts: int) -> tuple[dict[str, list], list[list]]:
    """Return the AGP curve of the period from s_ts to e_ts (a display bin: {"p5": [...], ..., "p95": [...]}, one
    value per 15-minute bin of the time of day) and the runs of the bins that hold readings at the sensor's limit
    (profile_limits)."""
    mins, vals, tod = g["mins"], g["vals"], g["tod"]
    i0, i1 = bisect.bisect_left(mins, s_ts), bisect.bisect_left(mins, e_ts)
    # percentiles per 15-minute time-of-day bin of the minute grid, pooled with both neighboring bins (a 45-minute
    # window; GlucoCraft rule, stated in the profile's Notes)
    bins: list[list[float]] = [[] for _ in range(AGP_BINS)]
    for j in range(i0, i1):
        bins[tod[j]].append(vals[j])
    prof: dict[str, list] = {f"p{p}": [] for p in PCTS}
    for b in range(AGP_BINS):
        pool = sorted(bins[(b - 1) % AGP_BINS] + bins[b] + bins[(b + 1) % AGP_BINS])
        for p in PCTS:
            prof[f"p{p}"].append(_pct(pool, p) if len(pool) >= AGP_MIN_VALUES else None)
    # the 15-minute bins of the time of day in which readings at the sensor's limit lie (any day of the period):
    # marked along the profile's bottom (LO) or top (HI) edge; the bins themselves are not pooled
    lmins = g.get("lim_mins") or []
    kinds: list[set] = [set() for _ in range(AGP_BINS)]
    for j in range(bisect.bisect_left(lmins, s_ts), bisect.bisect_left(lmins, e_ts)):
        kinds[g["lim_tod"][j]].add(g["lim_kind"][j])
    return prof, _limit_runs(kinds)


def _daily_profiles(g: dict, start_local: datetime, ndays: int) -> dict[str, list]:
    """Return the daily profiles of the ndays local days from start_local (period_report daily=True): each day's
    label, its spoken name, its 10-minute slot means, the slots where the line breaks, the runs of slots that hold
    readings at the sensor's limit, and its weekday.

    Each day's profile has DAY_SLOTS ten-minute slots of the local clock, midnight to midnight. A minute's slot comes
    from its local clock time (lod), not from the time elapsed since midnight: on the 25-hour fall-back day the
    repeated hour falls into the same slots, and on the 23-hour spring-forward day the skipped hour stays empty (so the
    line breaks there), and every other reading is drawn at its clock time."""
    mins, vals, lod = g["mins"], g["vals"], g["lod"]
    lmins = g.get("lim_mins") or []
    labels, said, rows, breaks, day_limits = [], [], [], [], []
    for k in range(ndays):
        d0 = start_local + timedelta(days=k)
        a_ts, z_ts = int(d0.timestamp()), int((d0 + timedelta(days=1)).timestamp())
        a, z = bisect.bisect_left(mins, a_ts), bisect.bisect_left(mins, z_ts)
        # the 10-minute slots of the day that hold readings at the sensor's limit (their runs: daily_limits)
        kinds = [set() for _ in range(DAY_SLOTS)]
        for j in range(bisect.bisect_left(lmins, a_ts), bisect.bisect_left(lmins, z_ts)):
            kinds[g["lim_lod"][j] // DAY_SLOT_MINUTES].add(g["lim_kind"][j])
        day_limits.append(_limit_runs(kinds))
        slots: list[list[float]] = [[] for _ in range(DAY_SLOTS)]
        brk: set[int] = set()
        for j in range(a, z):
            s = lod[j] // DAY_SLOT_MINUTES
            slots[s].append(vals[j])
            # a data gap (the minute grid has no minute there, gaps.py) between two 10-minute slots: the line breaks
            # before the later slot even when no slot is empty (a gap of 15 to 19 minutes)
            if j > a and mins[j] - mins[j - 1] > GRID_STEP_S:
                s0 = lod[j - 1] // DAY_SLOT_MINUTES
                if s > s0:
                    brk.add(s)
        labels.append(_rg().weekday_numeric(d0))
        # the same day as a screen reader reads it, in words: "Wednesday, October 14" (the figures' descriptions)
        said.append(t("report.alt.day_name", weekday=weekday_name(d0.weekday()), date=_rg().day_month(d0, long=True)))
        rows.append([sum(x) / len(x) if x else None for x in slots])  # unrounded: rounded once where shown
        breaks.append(sorted(brk))
    return {
        "daily_labels": labels,
        "daily_said": said,
        "daily": rows,
        "daily_breaks": breaks,
        "daily_limits": day_limits,
        # the weekday of each day (0 = Monday): the page and the PDF lay the days out in calendar rows from the first
        # day of the week in use
        "daily_weekdays": [(start_local + timedelta(days=k)).weekday() for k in range(ndays)],
    }


# ------------------------------------------------------------------ hypoglycemia events
def _low_scan(
    segs: list, limit: float, settle_from: int | None = None
) -> tuple[list[tuple[int, int, float]], int | None]:
    """Return the hypoglycemia events [(start epoch, duration in seconds, lowest value)] of the readings `segs`, each
    (from, to, value, ...): the seconds a reading represents (minute_grid "_segs"), in time order; and the time at which
    everything that began before settle_from is decided: the start of the first segment at or after settle_from before
    which no event and no run below the limit that began before settle_from is still open (None without settle_from,
    or when the readings end first). The CGM Report depends on the readings up to that time only (its cache key,
    report_service).

    An event is a run below `limit` that lasts at least EVENT_MIN minutes; it ends after EVENT_MIN consecutive minutes
    at or above EVENT_END (70 mg/dL) for every level. Source: the clinical consensus, Danne et al., 2017,
    International Consensus on Use of Continuous Glucose Monitoring, Section 4: "readings below the threshold for at
    least 15 min is considered an event", and it ends after "readings for 15 min at >=70 mg/dL (3.9 mmol/L)" - one end
    rule for every level (level 1: limit 70; level 2: limit 54). Where sources differ, the results follow the
    clinical-care standards, and newer research is given only as context (the ADA Standards of Care 2026 and
    Battelino et al., 2019 define no start / end rule). Context only, not used: Battelino et al., 2023, Table 4
    (classification for clinical trials) ends a level 2 event after 15 consecutive minutes at >=54 mg/dL.
    So a level 2 event goes on while glucose is back between 54 and 69 mg/dL, and every level 2 event lies inside a
    level 1 event (level 2 events are also counted as level 1 events; a second call with limit 54). A reading between
    the limit and EVENT_END inside an event neither ends it nor counts towards its end; before an event it breaks the
    run below the limit.

    The times are the readings' own: an event starts with its first reading below the limit, at that reading's
    measurement time (a reading at 10:30:40 starts it at 10:30:40), and the minutes are the time the readings
    represent, to the second, so that a first reading that covers less than a whole minute of the minute grid still
    starts the event. The duration ends at the first reading of the 15 minutes at or above 70 mg/dL. GlucoCraft rule
    (no consensus rule exists; the gap rule, gaps.py): a data gap - an interval between readings longer than max(15
    minutes, 3 x the reading interval of that stretch), where a reading represents only its own interval - ends an
    event at the end of its last reading and restarts a run below the limit."""
    need = EVENT_MIN * 60
    events: list[tuple[int, int, float]] = []
    in_event, ev_start, nadir = False, 0, math.inf
    run_start, run_s = None, 0  # the run below the limit
    ok_start, ok_s = None, 0  # the run at or above EVENT_END inside an event
    prev_end = None
    settled = None
    for a, e, v, *_ in segs:
        if prev_end is not None and a > prev_end:  # a data gap (the gap rule): the event ends
            if in_event:
                events.append((ev_start, prev_end - ev_start, nadir))
            in_event, run_start, run_s, ok_start, ok_s, nadir = False, None, 0, None, 0, math.inf
        if settled is None and settle_from is not None and a >= settle_from:
            begun = ev_start if in_event else run_start  # the open event, else the open run below the limit
            if begun is None or begun >= settle_from:
                settled = a
        prev_end = e
        if v < limit:
            ok_start, ok_s = None, 0
            if run_start is None:
                run_start, run_s = a, 0
            run_s += e - a
            nadir = min(nadir, v)
            if not in_event and run_s >= need:
                in_event, ev_start = True, run_start
            continue
        run_start, run_s = None, 0
        if not in_event:
            nadir = math.inf
            continue
        if v < EVENT_END:  # between the limit and 70: the event goes on
            ok_start, ok_s = None, 0
            continue
        if ok_start is None:  # at or above 70 mg/dL counts towards the end
            ok_start, ok_s = a, 0
        ok_s += e - a
        if ok_s >= need:
            events.append((ev_start, ok_start - ev_start, nadir))
            in_event, ok_start, ok_s, nadir = False, None, 0, math.inf
    if in_event:
        events.append((ev_start, prev_end - ev_start, nadir))
    return events, settled


def report_events(g: dict, limit: float = LEVEL1_LIMIT) -> list[tuple[int, int, float]]:
    """The hypoglycemia events of the whole grid's readings (_low_scan over "_segs"), found once per report and kept
    in the grid: [(start epoch, duration in seconds, lowest value)], in time order. Each event therefore has its true
    start, duration and lowest value, also when it crosses the boundary of a period, a 14-day block or a day;
    events_in() then counts it once, in the window where it starts (the rule of the research appendix, method step 6,
    applied to the consensus events). Also kept: the starts (for bisection) and the time at which every event and run
    that began before the grid's "_settle_from" is decided (_low_scan; build_report's reads_until)."""
    key = f"_events_{limit:g}"
    if key not in g:
        events, settled = _low_scan(g.get("_segs") or [], limit, g.get("_settle_from"))
        g[key], g[f"{key}_starts"], g[f"{key}_settled"] = events, [e[0] for e in events], settled
    return g[key]


def _events_settled(g: dict, limit: float) -> int | None:
    """The time from which the events (report_events) of a limit need no later reading (_low_scan)."""
    report_events(g, limit)
    return g[f"_events_{limit:g}_settled"]


def events_in(g: dict, s_ts: int, e_ts: int, limit: float = LEVEL1_LIMIT) -> list[tuple[int, int, float]]:
    """The events (report_events) that start in [s_ts, e_ts); an event may end after e_ts."""
    events = report_events(g, limit)
    starts = g[f"_events_{limit:g}_starts"]
    return events[bisect.bisect_left(starts, s_ts) : bisect.bisect_left(starts, e_ts)]


def _event_level(g: dict, ev: tuple[int, int, float]) -> int:
    """2 when a level 2 event (15 consecutive minutes below 54 mg/dL, Danne et al., 2017, Section 4) lies inside this
    level 1 event, else 1. Not taken from the lowest value: a single value below 54 does not make a level 2 event."""
    start, seconds, _ = ev
    return 2 if events_in(g, start, start + seconds, LEVEL2_LIMIT) else 1


def _level2_counted_at(g: dict) -> list[int]:
    """The time at which each level 2 event is counted, in time order: the start of the level 1 event it lies in.

    Level 2 events are also counted as level 1 events, so a level 2 event is counted in the window of its level 1
    event: a level 1 event that begins before a window and falls below 54 mg/dL only inside it is counted, with its
    level 2 event, in the earlier window (the rule of events_in). Every level 2 event lies inside a level 1 event
    (_low_scan); the event's own start is the fallback."""
    if "_level2_at" not in g:
        level1 = report_events(g, LEVEL1_LIMIT)
        starts1 = g[f"_events_{LEVEL1_LIMIT:g}_starts"]
        at = []
        for start, _, _ in report_events(g, LEVEL2_LIMIT):
            k = bisect.bisect_right(starts1, start) - 1
            inside = k >= 0 and start < level1[k][0] + level1[k][1]
            at.append(level1[k][0] if inside else start)
        g["_level2_at"] = sorted(at)
    return g["_level2_at"]


def _lowest_is_limit(g: dict, ev: tuple[int, int, float]) -> bool:
    """True when the lowest value of an event is a reading at the sensor's lower limit (LO): its true value is unknown
    and lower, so it is shown as LO, not as a measured number. Only the event's own readings are looked at: the
    segments are in time order and do not overlap, so they are found by bisection."""
    start, seconds, nadir = ev
    segs, starts = g.get("_segs") or [], g.get("_seg_starts") or []
    first = max(0, bisect.bisect_right(starts, start) - 1)  # the segment that contains the start
    last = bisect.bisect_left(starts, start + seconds)
    return any(v == nadir and flag == "low" for a, e, v, flag in segs[first:last] if a < start + seconds and e > start)


def _night_and_day(g: dict, s_ts: int, e_ts: int) -> tuple[PeriodStats | None, PeriodStats | None]:
    """Return the statistics (_stats) of the night (00:00-05:59 local) and of the day (06:00-23:59) between s_ts and
    e_ts: each reading weighs the seconds it represents in that part of the day. None for a part without data. Kept in
    the grid, because the hypoglycemia and the high-glucose sections both use them."""
    key = f"_night_day_{s_ts}_{e_ts}"
    if key not in g:
        parts = _within(g, s_ts, e_ts)
        night = [p for p in parts if p[3] < NIGHT_END_HOUR * 4]
        day = [p for p in parts if p[3] >= NIGHT_END_HOUR * 4]
        g[key] = (_stats(g, night, 0), _stats(g, day, 0))
    return g[key]


def _enough(g: dict, s_ts: int, e_ts: int) -> bool:
    """True when the period from s_ts to e_ts has enough data for its statistics: sensor active at least 70 %,
    unrounded (ENOUGH_DATA_PCT; statistics.PeriodStats.sensor_active_ok)."""
    st = _period(g, s_ts, e_ts)[1]
    return st is not None and st.sensor_active_ok


def lows_detail(g: dict, start_local: datetime, ndays: int, tz: tzinfo) -> dict:
    """Time below 70 / 54 by night (00:00-06:00) and day, level 1 / level 2 events, events per week, durations. The
    events are those that start in the period (events_in), each with its own start, duration and lowest value. None
    of them (and no event list) when the period does not have enough data (_enough)."""
    s_ts = int(start_local.timestamp())
    e_ts = int((start_local + timedelta(days=ndays)).timestamp())
    if not _enough(g, s_ts, e_ts):
        return {"enough_data": False, "list": []}
    night, day = _night_and_day(g, s_ts, e_ts)
    ev = events_in(g, s_ts, e_ts, LEVEL1_LIMIT)
    # the level 2 events counted in the period: each where its level 1 event starts (_level2_counted_at)
    at = _level2_counted_at(g)
    events_54 = bisect.bisect_left(at, e_ts) - bisect.bisect_left(at, s_ts)
    weeks = ndays / 7.0
    longest = max(ev, key=lambda e: e[1]) if ev else None

    def when(ts):
        """An epoch time as a local datetime."""
        return datetime.fromtimestamp(ts, tz)

    return {
        "enough_data": True,
        # beside the consensus targets in the section's table (_beside_target)
        "night_below_70": _beside_target("below_70", night.time_below_low) if night else None,
        "day_below_70": _beside_target("below_70", day.time_below_low) if day else None,
        "night_below_54": _beside_target("very_low", night.time_critical_low) if night else None,
        "day_below_54": _beside_target("very_low", day.time_critical_low) if day else None,
        "events": len(ev),
        "events_night": sum(1 for e in ev if when(e[0]).hour < NIGHT_END_HOUR),
        "events_per_week": _r(len(ev) / weeks, 1),
        "events_54": events_54,
        "events_54_per_week": _r(events_54 / weeks, 1),
        "mean_duration_min": _r(sum(e[1] for e in ev) / 60 / len(ev)) if ev else None,
        "longest_min": _r(longest[1] / 60) if longest else None,
        "longest_when": _rg().stamp(when(longest[0])) if longest else None,
        # one row per level 1 event: [start label, minutes, lowest value (mg/dL), local hour of day as a decimal,
        # level (2 when a level 2 event lies inside it), lowest value is a LO reading]
        "list": [
            [
                _rg().stamp(when(e[0])),
                _r(e[1] / 60),  # whole minutes
                e[2],  # as stored; rounded once where shown
                _r(when(e[0]).hour + when(e[0]).minute / 60, 2),
                _event_level(g, e),
                _lowest_is_limit(g, e),
            ]
            for e in ev
        ],
    }


def low_event_summary(g: dict, end_local: datetime, ndays: int, tz: tzinfo) -> list[dict]:
    """Return the full report's summary of the hypoglycemia events: one column per period of EVENT_SUMMARY_DAYS that
    lies inside the report's period (a 14-day report: one column), each ending at the report's last midnight.

    Per column: the period's days and sensor active (shown as in period_report), the level 1 events that start in it
    (events_in, Danne et al., 2017, Section 4), those of them in which a level 2 event lies (_event_level), the events
    per day (two decimals: one event in 30 days is 0.03, not 0.0), the median and the longest duration (whole minutes),
    the event starts in the eight 3-hour blocks of the local clock (00:00-02:59 ... 21:00-23:59) and at night
    (00:00-05:59; Battelino et al., 2023, Panel 2). Counts and durations only: no lowest value (an event's lowest value
    is not the period's minimum), no targets. A column whose period does not have enough data (_enough) has its days
    and sensor active only (enough_data False)."""
    out = []
    for days in (d for d in EVENT_SUMMARY_DAYS if d <= ndays):
        start = end_local - timedelta(days=days)
        s_ts, e_ts = int(start.timestamp()), int(end_local.timestamp())
        _, st = _period(g, s_ts, e_ts)
        active = st.sensor_active_exact if st is not None else 0.0
        if not _enough(g, s_ts, e_ts):
            out.append({"days": days, "active": _active_shown(active), "enough_data": False})
            continue
        ev = events_in(g, s_ts, e_ts, LEVEL1_LIMIT)
        hours = [datetime.fromtimestamp(e[0], tz).hour for e in ev]
        lengths = sorted(e[1] for e in ev)  # seconds
        half = len(lengths) // 2
        out.append(
            {
                "days": days,
                "active": _active_shown(active),
                "enough_data": True,
                "events": len(ev),
                "level2": sum(1 for e in ev if _event_level(g, e) == 2),
                "per_day": _r(len(ev) / days, 2),
                # the median: the middle duration, or the mean of the two middle ones
                "median_min": _r((lengths[half] + lengths[~half]) / 2 / 60) if ev else None,
                "longest_min": _r(lengths[-1] / 60) if ev else None,
                "blocks": [sum(1 for h in hours if h // 3 == k) for k in range(8)],
                "night": sum(1 for h in hours if h < NIGHT_END_HOUR),
            }
        )
    return out


def second_source_share(readings: list, s_ts: int, e_ts: int) -> float | None:
    """Return the share (%) of the readings between s_ts and e_ts that came from the second source (the source byte
    stored with each reading, const.SOURCE_SECONDARY), as shown: one decimal, rounded once, and never 0.0% or 100.0%
    unless it is exactly that (units.shown_against, the rule of the percentages). None without readings."""
    ts = [r.timestamp for r in readings]
    i, j = bisect.bisect_left(ts, s_ts), bisect.bisect_left(ts, e_ts)
    if i == j:
        return None
    share = 100.0 * sum(1 for r in readings[i:j] if r.source == SOURCE_SECONDARY) / (j - i)
    return shown_against(share, (0.0, 100.0), 1)[0]


# ------------------------------------------------------------------ high glucose
def highs_detail(g: dict, start_local: datetime, ndays: int) -> dict:
    """Return the time above 180 and above 250 mg/dL by night (00:00-05:59) and day (06:00-23:59).

    The night is the hypoglycemia section's (Danne et al., 2017, Section 7; Battelino et al., 2023, Panel 2).
    Consensus measures only; the main report counts no high-glucose events. None of them when the period does not
    have enough data (_enough).
    """
    s_ts = int(start_local.timestamp())
    e_ts = int((start_local + timedelta(days=ndays)).timestamp())
    if not _enough(g, s_ts, e_ts):
        return {"enough_data": False}
    night, day = _night_and_day(g, s_ts, e_ts)
    return {
        "enough_data": True,
        # beside the consensus targets in the section's table (_beside_target)
        "night_above_180": _beside_target("above_180", night.time_above_high) if night else None,
        "day_above_180": _beside_target("above_180", day.time_above_high) if day else None,
        "night_above_250": _beside_target("very_high", night.time_critical_high) if night else None,
        "day_above_250": _beside_target("very_high", day.time_critical_high) if day else None,
    }


# ------------------------------------------------------------------ data quality
def data_quality(g: dict, start_local: datetime, ndays: int, tz: tzinfo, events: list | None = None) -> dict:
    """Return the values of the data quality page.

    Sensor active per local day (the same rule as the period's sensor active: the time the readings represent,
    gaps.py, summed from the grid's parts, which are cut at every local midnight), the data gaps (intervals between two
    readings longer than the gap rule allows) that overlap the period, the time without data, the readings at the
    sensor's limit (LO / HI) and the sensor changes and warm-ups GlucoCraft detected (events: [{"start": epoch s,
    "end": epoch s | None[, "limit": True]}], report_service). A warm-up that ended less than a minute after its start
    (a second source went on delivering readings) is listed as a sensor change without a warm-up: it would round to
    "0 min". A warm-up that ended after 24 hours without a reading (limit: coordinator.WARMUP_MAX_SECONDS) keeps that
    end and carries limit True, so that the page and the PDF name the cause."""
    ts, iv = g.get("_ts") or [], g.get("_iv") or []
    s_ts = int(start_local.timestamp())
    e_ts = int((start_local + timedelta(days=ndays)).timestamp())
    parts, ps = _period(g, s_ts, e_ts)
    covered: dict[Any, int] = {}  # the seconds the readings represent, per local date
    for a, e, _k, _b, day in parts:
        covered[day] = covered.get(day, 0) + e - a
    per_day = []
    for k in range(ndays):
        d0 = start_local + timedelta(days=k)
        length = int((d0 + timedelta(days=1)).timestamp()) - int(d0.timestamp())  # 23 / 25 hours on DST days
        per_day.append(
            {
                "date": d0.strftime("%Y-%m-%d"),
                "label": _rg().numeric(d0),
                "weekday": d0.weekday(),
                # a day's sensor active, as in the Daily Summary (_day_values): on its side of the 70% rule
                "active": _active_shown(sensor_active(covered.get(d0.date(), 0), length)),
            }
        )
    # gaps: the interval between two consecutive readings is a gap when it is longer than max(15 minutes, 3 x the
    # reading interval of that stretch) (gaps.py, the one gap rule); a gap is counted when it overlaps the period
    gaps = []
    i = max(0, bisect.bisect_left(ts, s_ts) - 1)
    for k in range(i, len(ts) - 1):
        if ts[k] >= e_ts:
            break
        d = ts[k + 1] - ts[k]
        if d > gap_threshold(iv[k]) and ts[k + 1] > s_ts:
            gaps.append((ts[k], ts[k + 1]))
    longest = max(gaps, key=lambda x: x[1] - x[0]) if gaps else None
    # the period's sensor active, as in period_report (the same statistics), shown on its side of the 70% rule
    active = ps.sensor_active_exact if ps is not None else 0.0

    def when(ts):
        """An epoch time as the report writes a date and time ("Wed 10/22 7:46 AM")."""
        return _rg().stamp(datetime.fromtimestamp(ts, tz))

    lim_n, lim_s = limit_counts(g, s_ts, e_ts)
    ev_out = []
    for e in sorted(events or [], key=lambda x: x.get("start") or 0):
        st, en = int(e.get("start") or 0), e.get("end")
        if not st or not (s_ts <= st < e_ts):
            continue
        if en and int(en) - st < 60 and not e.get("limit"):  # no warm-up time of a whole minute: a sensor change only
            ev_out.append({"start": when(st), "end": None, "minutes": None, "warmup": False})
            continue
        shown = {
            "start": when(st),
            "end": when(int(en)) if en else None,
            "minutes": _r((int(en) - st) / 60) if en else None,
            "warmup": True,
        }
        if e.get("limit"):  # ended by the 24-hour rule, without a reading measured after its start
            shown["limit"] = True
        ev_out.append(shown)
    return {
        "per_day": per_day,
        "lead": _rg().lead_days(start_local.weekday()),
        "active": _active_shown(active),
        "no_data_minutes": _r((e_ts - s_ts) * (100.0 - active) / 100.0 / 60),
        "gaps": len(gaps),
        "longest_gap_minutes": _r((longest[1] - longest[0]) / 60) if longest else None,
        "longest_gap_from": when(longest[0]) if longest else None,
        # the end as a clock time when the gap ends on the day it began
        "longest_gap_to": (
            _rg().clock(datetime.fromtimestamp(longest[1], tz))
            if datetime.fromtimestamp(longest[0], tz).date() == datetime.fromtimestamp(longest[1], tz).date()
            else when(longest[1])
        )
        if longest
        else None,
        "limit_readings": lim_n,
        "limit_seconds": lim_s,
        "sensor_events": ev_out,
    }


# ------------------------------------------------------------------ the weekly trend
def weekly_trend(
    readings: list, interval: int, end_local: datetime, iv: list | None = None, weeks: int = TREND_WEEKS
) -> list[dict]:
    """Return the weekly statistics of the `weeks` weeks of 7 days ending at end_local, oldest first.

    The values are those of statistics.compute_weekly_trends (as the panels' weekly statistics), here on the consensus
    ranges; end_local is the report's last midnight. Per week: time in range, CV, mean and sensor active. A week is a
    period of its own: without enough data (sensor active at least 70 %, unrounded; ENOUGH_DATA_PCT) it has no values
    (tir None; statistics.compute_weekly_trends gives no statistics for it) and keeps its sensor active, the time its
    readings cover (_clipped; enough_data False); a week without data has neither (active None). Each week runs from
    local midnight to local midnight, the dates its row is labeled with (167 or 169 hours across a daylight-saving
    change).
    """
    bounds = [(end_local - timedelta(days=7 * (k + 1)), end_local - timedelta(days=7 * k)) for k in range(weeks)]
    stats = compute_weekly_trends(
        readings,
        detected_interval=int(interval or FALLBACK_INTERVAL_SECONDS),
        intervals=iv,
        windows=[(int(a.timestamp()), int(b.timestamp())) for a, b in bounds],
        **AGP_RANGES,
    )
    ts, iv = _prepared(readings, interval, None, iv)
    out = []
    for (start, end), s in zip(bounds, stats, strict=False):
        row = {
            "week_end": (end - timedelta(days=1)).strftime("%Y-%m-%d"),
            "tir": None,
            "cv": None,
            "mean": None,
            "active": None,
            "enough_data": False,
        }
        wa, wb = int(start.timestamp()), int(end.timestamp())
        covered = sum(e - a for _, a, e in _clipped(ts, iv, wa, wb))
        if covered > 0:  # the week's sensor active, with or without enough data
            row["active"] = _active_shown(sensor_active(covered, wb - wa))
        if s is not None and s.sensor_active_ok:
            row.update(
                {
                    "tir": _one_decimal(s.pct_time_in_target_range),
                    "cv": _one_decimal(s.cv),
                    "mean": s.mean,  # unrounded: the page and the PDF round it once, in the display unit
                    "enough_data": True,
                }
            )
        out.append(row)
    return list(reversed(out))


# ------------------------------------------------------------------ appendix: custom ranges and goals
GOAL_ROWS = (  # (goal key in the configuration, value key); each row's label is report.goal.<goal key>
    ("time_in_range", "tir"),
    ("time_in_tight_range", "titr"),
    ("time_low", "below_low"),
    ("time_very_low", "very_low"),
    ("time_high", "above_high"),
    ("time_very_high", "very_high"),
    ("cv", "cv"),
    ("gmi", "gmi"),
)


def goal_label(key: str) -> str:
    """Return the label of a goal row of the custom appendix ("Time in range")."""
    return t(f"report.goal.{key}")


def custom_appendix(g: dict, start_local: datetime, ndays: int, custom: dict) -> dict:
    """The statistics of the report period with the ranges in the settings and the custom goals (the user's own
    settings; the main report always uses the consensus ranges). custom = {"ranges": {very_low, low, tight_low,
    tight_high, high, very_high} (mg/dL), "goals": {key: {"op", "value"}}} (coordinator.configuration()). Time below
    range and time above range are the combined bands (as the goals and the consensus targets: level 1 + level 2).
    The values only with enough data (sensor active at least 70 %, unrounded), as every period of the report; met /
    not met only for 14 days or more with enough data (the 14-day / 70 % rule)."""
    rg = {**CONSENSUS_RANGES, **{k: v for k, v in (custom.get("ranges") or {}).items() if v is not None}}
    s_ts = int(start_local.timestamp())
    e_ts = int((start_local + timedelta(days=ndays)).timestamp())
    # the statistics sensors' calculation on the ranges in the settings: each reading weighs its seconds in the period,
    # at its position on the glucose scale (limits.scale_position: a LO reading is always very low and a HI reading
    # always very high, as on the sensors and in the notifications)
    ps = _stats(
        g,
        _within(g, s_ts, e_ts),
        e_ts - s_ts,
        {
            "range_low": rg["low"],
            "range_high": rg["high"],
            "range_ideal_low": rg["tight_low"],
            "range_ideal_high": rg["tight_high"],
            "range_critical_low": rg["very_low"],
            "range_critical_high": rg["very_high"],
        },
    )
    # sensor active: the covered seconds as a share of the period (the same with any ranges); the 70% rule compares the
    # unrounded value
    exact = ps.sensor_active_exact if ps is not None else 0.0
    active_ok = ps is not None and ps.sensor_active_ok
    st: dict[str, Any] = {"active": _active_shown(exact), "enough_data": active_ok}
    # the percentages unrounded: the goals compare these, and they are shown rounded once (below)
    unrounded: dict[str, float | None] = {}
    if ps is not None and active_ok:
        unrounded = {
            "tir": ps.pct_time_in_target_range,
            "titr": ps.pct_time_in_ideal_range,
            "below_low": ps.time_below_low,
            "very_low": ps.time_critical_low,
            "above_high": ps.time_above_high,
            "very_high": ps.time_critical_high,
            "cv": ps.cv,
        }
        st.update({key: _one_decimal(value) for key, value in unrounded.items()})
        st["gmi"] = gmi_shown(ps, ndays * 86400)  # 14 days or more (local days) with at least 70% sensor active
        st["gmi_mmol_mol"] = ps.gmi_mmol_mol if st["gmi"] is not None else None
    comparable = ndays >= STANDARD_DAYS and active_ok
    goals = custom.get("goals") or {}
    rows = []
    for key, vkey in GOAL_ROWS:
        goal = goals.get(key) or {}
        op, target = goal.get("op"), goal.get("value")
        # the unrounded value is compared with the goal; it is shown rounded once, on its side of the goal (69.96%
        # beside a goal of >70%, units.shown_against). The GMI is compared and shown with its one decimal.
        value = unrounded.get(vkey, st.get(vkey))
        shown = st.get(vkey)
        met = None
        if comparable and value is not None and target is not None and op in COMPARE:
            met = bool(COMPARE[op](value, target))
        if vkey in unrounded and value is not None and target is not None:
            shown = shown_against(value, target, 1)[0]
        rows.append(
            {
                "key": key,
                "label": goal_label(key),
                "value": shown,
                "goal_op": op if target is not None else None,
                "goal": target,
                # the GMI goal (entered in percent) in mmol/mol by the GMI rule, as the GMI values beside it
                "goal_mmol_mol": gmi_goal_mmol_mol(target) if key == "gmi" and target is not None else None,
                "met": met,
            }
        )
    differs = rg != CONSENSUS_RANGES
    return {
        "ranges": rg,
        "stats": st,
        "rows": rows,
        "comparable": comparable,
        "ranges_differ": differs,
    }


# ------------------------------------------------------------------ appendix: measures defined for research
def extended_high_events(
    readings: list, interval: int, s_ts: int, e_ts: int, tz: tzinfo, ts: list | None = None, iv: list | None = None
) -> dict:
    """Return the extended high-glucose events of the research appendix (count, total and longest duration).

    The definition is Battelino et al., 2023, p. 48: sensor glucose above 250 mg/dL lasting at least 120 minutes,
    ending when glucose returns to 180 mg/dL or below for 15 minutes. GlucoCraft's method, as the appendix states it:
      1. the readings received, mg/dL (a HI reading is stored at the sensor's upper limit);
      2. start: a run of consecutive readings, each above 250, becomes an event once the time it covers reaches 120
         minutes (each reading covers the time until the next reading, or its own reading interval before a gap); the
         event starts at the run's first reading;
      3. readings from 181 to 250 do not end an event;
      4. end: at the first reading of the first run of readings at or below 180 that covers 15 minutes; the duration
         is the time from the start to that reading;
      5. a data gap (the gap rule, gaps.py) ends a running event at the end of its last reading before the gap (that
         reading's own reading interval after it, as for every reading before a gap); a run above 250 interrupted by a
         gap is an event only if it had already covered 120 minutes;
      6. each event is counted once, in the period in which it starts (also when it continues past midnight).
    Returns {"events": n, "total_minutes", "longest_minutes", "longest_when"}: the appendix shows these values, no
    list of the events."""
    return _extended_high(readings, interval, s_ts, e_ts, tz, ts, iv)[0]


def _extended_high(
    readings: list, interval: int, s_ts: int, e_ts: int, tz: tzinfo, ts: list | None = None, iv: list | None = None
) -> tuple[dict, int | None]:
    """Return extended_high_events, and the measurement time of the first reading at or after e_ts at which no event
    or run above 250 mg/dL is open (the events need no later reading; None when the readings end first)."""
    events, reads_until = extended_high_runs(readings, interval, s_ts, e_ts, ts, iv)
    longest = max(events, key=lambda e: e[1]) if events else None

    def when(ts):
        """An epoch time as the report writes a date and time ("Wed 10/22 7:46 AM")."""
        return _rg().stamp(datetime.fromtimestamp(ts, tz))

    return {
        "events": len(events),
        "total_minutes": _r(sum(e[1] for e in events) / 60),
        "longest_minutes": _r(longest[1] / 60) if longest else None,
        "longest_when": when(longest[0]) if longest else None,
    }, reads_until


def extended_high_runs(
    readings: list, interval: int, s_ts: int, e_ts: int, ts: list | None = None, iv: list | None = None
) -> tuple[list[tuple[int, int]], int | None]:
    """Return the extended high-glucose events that start between s_ts and e_ts, [(start epoch, duration in seconds)],
    by the method of extended_high_events, and the time from which they need no later reading (_extended_high).

    The scan starts at a reading before which no event and no run above 250 mg/dL can be open (_xhigh_scan_start), so
    that an event that began before the period, however long before, keeps its own start and is not counted again
    in the period."""
    ts, iv = _prepared(readings, interval, ts, iv)
    n = len(ts)
    reads_until = None
    k0 = _xhigh_scan_start(readings, ts, iv, bisect.bisect_left(ts, s_ts))
    spans = _spans(ts, iv, k0, n)
    events: list[tuple[int, int]] = []
    mode, run_start, run_cov, ev_start, low_start, low_cov = "idle", 0, 0, 0, None, 0
    for k in range(k0, n):
        if ts[k] >= e_ts and mode == "idle":
            reads_until = ts[k]
            break  # only events starting in the period are needed
        v = readings[k].raw
        gap_after = k + 1 >= n or ts[k + 1] - ts[k] > gap_threshold(iv[k])
        span = spans[k - k0]
        if mode in ("idle", "run"):
            if v > XHIGH_LIMIT:
                if mode == "idle":
                    mode, run_start, run_cov = "run", ts[k], 0
                run_cov += span
                if run_cov >= XHIGH_START_S:
                    mode, ev_start, low_start, low_cov = "event", run_start, None, 0
            else:
                mode = "idle"
        else:
            if v <= XHIGH_END:
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
            if mode == "event":  # ends at the end of its last reading (step 5), as a hypoglycemia event does
                events.append((ev_start, ts[k] + span - ev_start))
            mode, low_start = "idle", None
    return [e for e in events if s_ts <= e[0] < e_ts], reads_until


def _xhigh_scan_start(readings: list, ts: list, iv: list, k: int) -> int:
    """Return the index of the reading at or before reading k from which the extended high-glucose scan can start with
    nothing open: the first reading after a data gap (the gap rule ends every event and run), or the first reading of
    consecutive readings at or below 180 mg/dL that cover 15 minutes (they end any event, and no run above 250 can
    pass them), the latest such reading; 0 when there is none (the first reading)."""
    covered = 0
    while k > 0:
        if k < len(ts) and ts[k] - ts[k - 1] > gap_threshold(iv[k - 1]):
            return k  # a data gap before reading k
        if readings[k - 1].raw > XHIGH_END:
            covered = 0
        else:
            # reading k - 1 is followed by reading k without a gap: it covers the time until it
            covered += (ts[k] - ts[k - 1]) if k < len(ts) else iv[k - 1]
            if covered >= XHIGH_END_S:
                return k - 1
        k -= 1
    return 0


# ------------------------------------------------------------------ the longer-term overview and full-report pages
def blocks(g: dict, end_local: datetime, ndays: int) -> list[dict]:
    """Consecutive 14-day blocks ending at end_local, oldest first (a shorter first block is labeled as such). The
    table shows no glucose profile per block, so none is calculated."""
    out = []
    k = 0
    while k * BLOCK_DAYS < ndays:
        days = min(BLOCK_DAYS, ndays - k * BLOCK_DAYS)
        start = end_local - timedelta(days=k * BLOCK_DAYS + days)
        r = period_report(g, start, days, profile=False)
        out.append(
            {
                key: r.get(key)
                for key in (
                    "period_start",
                    "period_end",
                    "days",
                    "tir",
                    "titr",
                    "below_70",
                    "very_low",
                    "above_180",
                    "very_high",
                    "mean",
                    "cv",
                    "gmi",
                    "gmi_mmol_mol",
                    "gri",
                    "gri_zone",
                    "active",
                    "enough_data",
                    # the block's readings at the sensor's limit (LO / HI), a fact of the data given with or without
                    # enough data, as for every period
                    "limit_readings",
                    "limit_seconds",
                )
            }
            # the level 1 events that start in the block (events_in), the table's Events column; none without enough
            # data in the block
            | {
                "events": len(events_in(g, int(start.timestamp()), int((start + timedelta(days=days)).timestamp())))
                if r["enough_data"]
                else None
            }
        )
        k += 1
    return list(reversed(out))


def day_pages(g: dict, start_local: datetime, ndays: int) -> dict:
    """Full report: 2-hour patterns, a per-day table (mean, low events, data %) and an hourly log. Each log row is
    [hourly minimum, hourly maximum, the hour has a LO reading, the hour has a HI reading] (24 entries each). The
    minimum and maximum are measured values (None without one); LO and HI count only as the hour's category. Each event is
    counted on the day it starts. The means and the data % weigh each reading by its seconds (_stats); an hour's
    minimum and maximum come from the readings that represent time in that hour. Enough data (sensor active at least
    70 %, unrounded): the patterns and the period's mean need it for the period, a day's mean and events for that day;
    the hourly log shows the measured values either way."""
    raw, flags = g["_raw"], g["_limits"]
    s_ts = int(start_local.timestamp())
    e_ts = int((start_local + timedelta(days=ndays)).timestamp())
    parts, whole = _period(g, s_ts, e_ts)
    enough = whole is not None and whole.sensor_active_ok
    # the 2-hour patterns: the time-weighted mean of each 2-hour block of the local clock over all days
    blocks2: list[list] = [[] for _ in range(12)]
    for x in parts:
        blocks2[x[3] // 8].append(x)
    pattern = [_stats(g, b, 0) if enough else None for b in blocks2]
    week, log = [], []
    for k in range(ndays):
        d0 = start_local + timedelta(days=k)
        a_ts, z_ts = int(d0.timestamp()), int((d0 + timedelta(days=1)).timestamp())
        mean, data_pct, dparts, events = _day_values(g, a_ts, z_ts)
        hmin: list = [None] * 24
        hmax: list = [None] * 24
        lo_lim, hi_lim = [False] * 24, [False] * 24
        for _, _, i, b, _ in dparts:
            hr, v, flag = b // 4, raw[i], flags[i]
            if flag == "low":
                lo_lim[hr] = True
            elif flag == "high":
                hi_lim[hr] = True
            else:
                hmin[hr] = v if hmin[hr] is None else min(hmin[hr], v)
                hmax[hr] = v if hmax[hr] is None else max(hmax[hr], v)
        week.append([_rg().weekday_numeric(d0), mean, events, data_pct])
        # the hour's minimum and maximum as stored (mg/dL, one decimal): rounded once where shown, in the display unit
        log.append([hmin, hmax, lo_lim, hi_lim])
    return {
        # the means unrounded: the page and the PDF round them once, in the display unit
        "pattern_2h": [b.mean if b else None for b in pattern],
        "mean": whole.mean if enough else None,
        "enough_data": enough,
        "week": week,
        "log": log,
    }


def _day_values(g: dict, a_ts: int, z_ts: int) -> tuple[float | None, float, list, int | None]:
    """Return the mean (mg/dL, unrounded: shown rounded once), the data %, the parts (_within) and the level 1 events
    that begin (events_in) of one local day from a_ts to z_ts. The data % is the time the readings represent as a
    share of the day's real length (23 / 25 hours on daylight-saving days), the rule of sensor active, shown on its
    side of the 70 % rule (_active_shown: 69.96, never 70 for a day below it). A day is a period of its own: its mean
    and events only with enough data (sensor active at least 70 %, unrounded), else None."""
    parts, s = _period(g, a_ts, z_ts)
    if s is None:
        return None, 0.0, parts, None
    if not s.sensor_active_ok:
        return None, _active_shown(s.sensor_active_exact), parts, None
    return s.mean, _active_shown(s.sensor_active_exact), parts, len(events_in(g, a_ts, z_ts))


def daily_values(readings: list, interval: int, tz: tzinfo, days: int, now: float) -> list[dict]:
    """Return the mean, the lowest and the highest value of each of the last `days` local calendar days, today (so
    far) included, oldest first: {"date": "YYYY-MM-DD", "mean", "min", "max" (mg/dL, unrounded: shown rounded once)},
    the values None for a day without enough data (statistics.enough_data: at least 70% sensor active, the rule of
    every statistic; today judged by the part of the day so far). The dashboard's daily chart (Custom Ranges;
    /api/glucocraft/data?daily=N).

    Each mean weighs every reading by the seconds it represents in the day (the one gap rule, gaps.weights, as every
    statistic: _clipped, statistics.compute_period_stats); the lowest and the highest value are those of the readings
    that represent time in the day, as stored. `readings` must reach at least a day before the first day, so that the
    reading interval of its first stretch is judged by the readings before it."""
    today = datetime.fromtimestamp(now, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    ts, iv = _prepared(readings, interval)
    out = []
    for k in range(days - 1, -1, -1):
        d0 = today - timedelta(days=k)
        a_ts, z_ts = int(d0.timestamp()), int((d0 + timedelta(days=1)).timestamp())
        z_ts = min(z_ts, int(now))  # today: the part of the day so far is the period its sensor active is judged by
        rs, w = [], []
        for i, a, e in _clipped(ts, iv, a_ts, z_ts):
            rs.append(readings[i])
            w.append(float(e - a))
        st = compute_period_stats(
            rs, "daily", z_ts - a_ts, int(interval or FALLBACK_INTERVAL_SECONDS), weights=w, **AGP_RANGES
        )
        shown = st if enough_data(st) else None
        out.append(
            {
                "date": d0.strftime("%Y-%m-%d"),
                "mean": shown.mean if shown else None,
                "min": shown.min_value if shown else None,
                "max": shown.max_value if shown else None,
            }
        )
    return out


def monthly(g: dict, start_local: datetime, end_local: datetime) -> list[dict]:
    """Full report: every calendar month the report period (start_local to end_local, local midnights) touches, oldest
    first, with each day's mean, low events (each counted on the day it starts) and data %. A month only partly in
    the period has only the period's days and is labeled with their first and last date ("May 10 – May 31, 2026");
    a whole month by its name ("June 2026")."""
    out = []
    m0 = start_local.replace(day=1)
    while m0 < end_local:
        y, mth = m0.year, m0.month
        m1 = m0.replace(year=y + 1, month=1) if mth == 12 else m0.replace(month=mth + 1)
        first, last = max(m0, start_local), min(m1, end_local)  # the period's days of this month
        days, dcur = [], first
        while dcur < last:
            a_ts, z_ts = int(dcur.timestamp()), int((dcur + timedelta(days=1)).timestamp())
            mean, data_pct, _, events = _day_values(g, a_ts, z_ts)
            days.append([dcur.day, mean, events, data_pct])
            dcur += timedelta(days=1)
        whole = first == m0 and last == m1
        label = (
            month_year(m0)
            if whole
            else t(
                "report.date_range",
                start=_rg().day_month(first, long=True),
                end=_rg().day_month(last - timedelta(days=1), long=True, year=True),
            )
        )
        # first_weekday: the weekday of the month's first day shown (0 = Monday); the page and the PDF place it in a
        # calendar whose rows start on the first day of the week in use
        out.append({"month": label, "first_weekday": first.weekday(), "days": days})
        m0 = m1
    return out


# ------------------------------------------------------------------ laboratory A1C compared with GMI
def _lab_window(day: str, tz: tzinfo) -> tuple[datetime, datetime] | None:
    """The 90 local days ending on `day` (inclusive): (start, end) local midnights; None for a malformed date."""
    try:
        d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=tz)
    except ValueError:
        return None
    end = d + timedelta(days=1)
    return end - timedelta(days=LAB_WINDOW_DAYS), end


def _gmi_before(
    readings: list, interval: int, day: str, tz: tzinfo, ts: list | None = None, iv: list | None = None
) -> tuple[float | None, float, float | None]:
    """Return (GMI, sensor active %, GMI in mmol/mol) of the 90 local days ending on `day` (inclusive).

    The GMI only with >= 70 % data; both GMI values from the same mean (Bergenstal et al., 2018). Each reading weighs
    the seconds it represents in the window (_clipped), as in _stats; no minute grid is needed for one statistic.
    """
    win = _lab_window(day, tz)
    if win is None:
        return None, 0.0, None
    start, end = win
    s_ts, e_ts = int(start.timestamp()), int(end.timestamp())
    ts, iv = _prepared(readings, interval, ts, iv)
    rs, w = [], []
    for k, a, e in _clipped(ts, iv, s_ts, e_ts):
        rs.append(readings[k])
        w.append(float(e - a))
    st = compute_period_stats(
        rs, "report", e_ts - s_ts, int(interval or FALLBACK_INTERVAL_SECONDS), weights=w, **AGP_RANGES
    )
    active = st.sensor_active_exact if st is not None else 0.0
    shown = _active_shown(active)  # on its side of the 70% rule
    if st is None or active < GMI_MIN_DATA_PCT:
        return None, shown, None
    return st.gmi, shown, st.gmi_mmol_mol


def no_comparison_note(retention_days: int) -> str:
    """Return the text shown for a laboratory result without a CGM comparison."""
    return t("report.lab.no_comparison", days=int(retention_days))


def lab_payload(
    rows: list[dict],
    readings: list,
    interval: int,
    tz: tzinfo,
    retention_days: int | None = None,
    kept_from: datetime | None = None,
    ts: list | None = None,
    iv: list | None = None,
) -> dict:
    """Return the laboratory results with the GMI of the 90 days before each.

    A result entered in mmol/mol keeps that value (a1c_mmol_mol); otherwise it is converted (IFCC-NGSP master
    equation). A result whose 90-day window starts before kept_from (the oldest local midnight the retention keeps)
    has no CGM comparison: no_cgm_comparison true, comparison_note "no CGM comparison (data older than 200 days)".
    """
    retention_days = int(retention_days or DEFAULT_RETENTION_DAYS)
    ts, iv = _prepared(readings, interval, ts, iv)
    out = []
    for r in sorted(rows, key=lambda x: x.get("date", "")):
        win = _lab_window(r.get("date", ""), tz)
        too_old = bool(win is not None and kept_from is not None and win[0] < kept_from)
        if too_old:
            gmi, active, gmi_mm = None, None, None
        else:
            gmi, active, gmi_mm = _gmi_before(readings, interval, r["date"], tz, ts, iv)
        a1c_mm = r.get("a1c_mmol_mol")
        if a1c_mm is None and r.get("a1c") is not None:
            a1c_mm = round_half_up(a1c_to_mmol_mol(r["a1c"]))
        out.append(
            {
                **r,
                "a1c_mmol_mol": a1c_mm,
                "gmi_90d": gmi,
                "gmi_90d_mmol_mol": gmi_mm,
                "cgm_active_90d": active,
                "no_cgm_comparison": too_old,
                "comparison_note": no_comparison_note(retention_days) if too_old else None,
            }
        )
    gaps = [_r(r["a1c"] - r["gmi_90d"], 1) for r in out if r.get("gmi_90d") is not None]
    # the same differences in mmol/mol: laboratory A1C in mmol/mol minus the GMI in mmol/mol (from the mean glucose)
    gaps_mm = [
        r["a1c_mmol_mol"] - r["gmi_90d_mmol_mol"]
        for r in out
        if r.get("gmi_90d_mmol_mol") is not None and r.get("a1c_mmol_mol") is not None
    ]
    return {
        "results": out,
        "average_difference": _r(sum(gaps) / len(gaps), 1) if gaps else None,
        "average_difference_mmol_mol": _r(sum(gaps_mm) / len(gaps_mm)) if gaps_mm else None,
        "retention_days": retention_days,
    }


# ------------------------------------------------------------------ the whole report
def build_report(
    readings: list,
    interval: int,
    tz: tzinfo,
    ndays: int,
    full: bool,
    lab_rows: list[dict],
    now: float | None = None,
    regional: Regional | None = None,
    intervals: list | None = None,
    retention_days: int | None = None,
    custom: dict | None = None,
    research: bool = False,
    sensor_events: list | None = None,
) -> dict:
    """Everything the Report page and the PDF show, for the ndays full local days ending at the last midnight.
    intervals: the reading interval at each reading (storage.reading_intervals; computed when not given).
    retention_days: the retention in use (laboratory results older than it allows have no CGM comparison).
    custom: the ranges and goals in the settings ({"ranges", "goals"}, coordinator.configuration()) when the appendix
    "Custom ranges and goals" is chosen (include_custom_appendix); None = not included. research: the appendix
    "Measures defined for research" (include_research_appendix). sensor_events: the sensor changes and warm-ups
    GlucoCraft detected ([{"start", "end"}], report_service).

    The minute grid reaches past the last midnight to the newest reading, so that a hypoglycemia event running at
    that midnight keeps its whole duration and lowest value (it is counted in the period in which it starts, as the
    extended high-glucose events are). "reads_until" is therefore the measurement time of the newest reading the
    report depends on: the first reading at or after the last midnight at which no event, and no run below a limit,
    that began before that midnight is still open. None when the report can change with any new reading (no such
    reading yet, or a laboratory result whose 90 days end after the last midnight). report_service keys its cache on
    it and removes it from the data.

    One build at a time (_BUILD_LOCK): the regional settings are set for the build and reset to the defaults after it.
    """
    global _REGIONAL
    with _BUILD_LOCK:
        _REGIONAL = regional or Regional()
        try:
            return _build(
                readings,
                interval,
                tz,
                ndays,
                full,
                lab_rows,
                now=now,
                intervals=intervals,
                retention_days=retention_days,
                custom=custom,
                research=research,
                sensor_events=sensor_events,
            )
        finally:
            _REGIONAL = Regional()


def _build(
    readings: list,
    interval: int,
    tz: tzinfo,
    ndays: int,
    full: bool,
    lab_rows: list[dict],
    *,
    now: float | None,
    intervals: list | None,
    retention_days: int | None,
    custom: dict | None,
    research: bool,
    sensor_events: list | None,
) -> dict:
    """Build the report data (build_report, which sets the regional settings and holds the build lock)."""
    now = now or time.time()
    interval = int(interval or FALLBACK_INTERVAL_SECONDS)
    retention_days = int(retention_days or DEFAULT_RETENTION_DAYS)
    end_local = datetime.fromtimestamp(now, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    span = max(ndays, STANDARD_DAYS)
    has_previous = ndays < NO_PREVIOUS_DAYS
    # this period (and the previous one); the monthly summary shows only the period's days
    first = end_local - timedelta(days=(2 if has_previous else 1) * span)
    first -= timedelta(days=EVENT_LOOKBACK_DAYS)  # events running when the earliest window begins keep their start
    ts, iv = _prepared(readings, interval, None, intervals)
    end_ts = int(end_local.timestamp())
    # to the newest reading: its own seconds end at most one day after it (every reading interval is shorter)
    g = minute_grid(readings, interval, int(first.timestamp()), max(end_ts, ts[-1] + 86400 if ts else 0), tz, ts, iv)
    g["_settle_from"] = end_ts
    start = end_local - timedelta(days=ndays)
    std_start = end_local - timedelta(days=STANDARD_DAYS)
    out: dict[str, Any] = {
        "ndays": ndays,
        "full": full,
        "standard": period_report(g, std_start, STANDARD_DAYS, daily=True, ends_now=True),
        "period": period_report(g, start, ndays, daily=full, ends_now=True),
        # the 6-month report has no previous period (it would need 2 x 180 = 360 days of readings)
        "previous": period_report(g, start - timedelta(days=ndays), ndays) if has_previous else None,
        "lows": lows_detail(g, start, ndays, tz),
        "lab": lab_payload(
            lab_rows, readings, interval, tz, retention_days, end_local - timedelta(days=retention_days), ts, iv
        ),
        "retention_days": retention_days,
        # the high-glucose section and the data quality page
        "highs": highs_detail(g, start, ndays),
        "quality": data_quality(g, start, ndays, tz, sensor_events),
        # the share of the period's readings that came from the second source (About This Report, Data sources)
        "second_source_pct": second_source_share(readings, int(start.timestamp()), end_ts),
        "include_custom_appendix": custom is not None,
        "include_research_appendix": bool(research),
        # the consensus ranges (mg/dL) of the Report page's zones and chart lines: the table the PDF uses too
        # (const.AGP_RANGES, with the user-visible keys range_very_low ... range_very_high); the four the page draws
        "zones": {k: v for k, v in public_agp_ranges().items() if k in PAGE_ZONES},
    }
    if ndays >= TREND_FROM_DAYS:  # the 26-week weekly trend: the 90-day and 6-month reports
        out["weekly"] = weekly_trend(readings, interval, end_local, iv)
    if custom is not None:
        out["custom_appendix"] = custom_appendix(g, start, ndays, custom)
    # the newest reading each part depends on (see above); the weekly trend, the gaps and every period's last reading
    # need no reading after the first one at or after the last midnight, which every settled time is
    until = [_events_settled(g, limit) for limit in (LEVEL1_LIMIT, LEVEL2_LIMIT)]
    if research:
        out["research_appendix"], research_until = _extended_high(
            readings, interval, int(start.timestamp()), end_ts, tz, ts, iv
        )
        until.append(research_until)
        if not out["period"]["enough_data"]:  # the period's values need enough data, the research measures too
            out["research_appendix"] = {"events": None, "total_minutes": None, "longest_minutes": None}
    if ndays > STANDARD_DAYS:
        out["blocks"] = blocks(g, end_local, ndays)
    if full:
        out["low_events"] = low_event_summary(g, end_local, ndays, tz)
        out["days"] = day_pages(g, start, ndays)
        out["monthly"] = monthly(g, start, end_local)
    lab_after = any((w := _lab_window(r.get("date", ""), tz)) is not None and w[1] > end_local for r in lab_rows)
    out["reads_until"] = None if lab_after or None in until else max(until)
    return out
