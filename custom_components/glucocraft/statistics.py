# License: AGPL-3.0-only (LICENSE).
"""Time-weighted statistics calculations for glucose data.

All statistics are computed from RAW readings only (not smoothed). The mean, the SD, the CV and the percentages (the
time in each band, the combined bands and sensor active) are kept unrounded and rounded once where they are shown
(units.fmt and the formatters, Home Assistant's display precision, the cards and the panels), so that no value is
rounded twice: an exact 69.45% is shown as 69% (whole) or 69.5% (one decimal), never as 70% from a stored 69.5. Every
rule (the goals, the consensus targets, the enough-data rule) compares the unrounded value. The GMI, the GRI and the
lowest and highest value keep the one decimal they are shown with. A single reading gives its value as the mean and no
SD or CV (one value has no spread).
Each reading's weight is the duration (seconds) until the next reading, or its own reading interval when the next
interval is a gap (gaps.py, the one gap rule). A limit reading (LO / HI, limits.py) tells only that glucose lies beyond
the sensor's range, so it is never used as a number: the mean, the SD, the CV, the GMI and the lowest and highest
value come from the measured readings alone, and are None for a period with limit readings only. It still counts as a
category: very low or very high in the time in each band and the GRI, and as covered time in sensor active. Each period
states its limit readings and the time they represent (limit_low / limit_high, limit_low_seconds / limit_high_seconds).
The enough-data rule (enough_data): a period's statistics are shown only with sensor active at least 70% of the period,
compared before rounding (const.ENOUGH_DATA_PCT). The statistics are always computed; every consumer asks enough_data
before it shows a value.

Performance: the callers find each period by bisection (coordinator._compute_all_stats, storage.window_indices) and
pass the stored reading intervals (storage.reading_intervals), the store's weights and its readings' positions on the
glucose scale (band_flags), each made once per statistics pass. The *_sets functions compute several range sets at
once (the ranges in use and the consensus ranges): only the six band sums depend on the ranges. The numbers are
identical to filtering the whole list and computing each range set on its own.
"""

from __future__ import annotations

import bisect
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import compress

from .const import (
    DEFAULT_RANGE_CRITICAL_HIGH,
    DEFAULT_RANGE_CRITICAL_LOW,
    DEFAULT_RANGE_HIGH,
    DEFAULT_RANGE_LOW,
    DEFAULT_RANGE_TIGHT_HIGH,
    DEFAULT_RANGE_TIGHT_LOW,
    ENOUGH_DATA_PCT,
    GMI_MIN_PERIOD_SECONDS,
)
from .gaps import local_intervals, window_weights
from .gaps import weights as gap_weights
from .limits import LIMIT_HIGH, LIMIT_LOW, scale_position
from .storage import DEFAULT_INTERVAL_SECONDS, GlucoseReading
from .units import gmi_mmol_mol, gmi_percent, round_half_up

# The terms. The range arguments (range_ideal_low, range_critical_low ..., as const.AGP_RANGES) and the PeriodStats
# fields (pct_time_in_ideal_range, time_critical_low ...) keep their internal names, because other modules and the
# entities read them by name; the comments use the consensus terms they stand for: "ideal" is the tight range,
# "critical" low / high is very low / very high, "target range" is the time in range (const.PUBLIC_KEYS maps the
# names where they are shown).

# The band flags of one range set (band_flags): six lists, one per band from very low to very high, of one flag per
# reading (in the band or not)
type BandFlags = list[list[bool]]
# The band flags of several range sets, one BandFlags per set in the order of the sets
type RangeSetFlags = Sequence[BandFlags]


def sensor_active(covered: float, period: float) -> float:
    """Sensor active (%): the covered seconds (the time the readings represent by the gap rule, gaps.py) as a share of
    the period, unrounded, at most 100; 0 for a period without length. It measures the data received, not the time the
    sensor was worn. The one formula of sensor active (the statistics, the CGM Report's periods and days)."""
    return min(100.0, 100.0 * covered / period) if period > 0 else 0.0


def gri_zone(gri: float | None) -> str | None:
    """Return the Glycemia Risk Index zone of a GRI value.

    Klonoff et al. (2023) define zones A to E as the quintiles of the GRI, from the lowest (first to 20th percentile) to
    the highest (81st to 100th percentile); the GRI estimates that percentile, so the zone is taken from the GRI value:
    A up to 20, B up to 40, C up to 60, D up to 80, E above 80 (GlucoCraft rule). Pass the value as it is displayed
    (rounded to one decimal), so that the zone shown next to a number always belongs to that number: 20.0 is zone A and
    20.1 is zone B on every surface."""
    if gri is None:
        return None
    return "A" if gri <= 20 else "B" if gri <= 40 else "C" if gri <= 60 else "D" if gri <= 80 else "E"


def gri(very_low: float, low: float, high: float, very_high: float) -> tuple[float, float, float, str | None]:
    """Return the Glycemia Risk Index of the time in the four bands (Klonoff et al., 2023): (GRI capped at 100,
    hypoglycemia component, hyperglycemia component, zone), each value rounded once to one decimal.

    Pass the unrounded percentages of the time very low (below 54 mg/dL), low (54 to 69), high (181 to 250) and very
    high (above 250). GRI = (3.0 x very low) + (2.4 x low) + (1.6 x very high) + (0.8 x high); hypoglycemia component =
    very low + (0.8 x low); hyperglycemia component = very high + (0.5 x high). The zone belongs to the GRI as
    displayed (gri_zone). The one calculation of the statistics sensors and the CGM Report."""
    score = round_half_up(min(100.0, (3.0 * very_low) + (2.4 * low) + (1.6 * very_high) + (0.8 * high)), 1)
    hypo, hyper = round_half_up(very_low + (0.8 * low), 1), round_half_up(very_high + (0.5 * high), 1)
    return score, hypo, hyper, gri_zone(score)


@dataclass
class PeriodStats:
    """Statistics for a time period: glucose values in mg/dL, percentages in percent, each unrounded unless noted."""

    period_label: str
    period_seconds: int
    reading_count: int
    data_availability: float  # sensor active: % of the period for which readings were received (unrounded)

    # from the measured readings only; each None for a period with limit readings only
    mean: float | None  # mg/dL, unrounded
    std_dev: float | None  # mg/dL, unrounded; None for a single measured reading
    cv: float | None  # coefficient of variation (%), unrounded; None for a single measured reading
    gmi: float | None  # glucose management indicator (%), one decimal
    min_value: float | None  # mg/dL, one decimal (as stored)
    max_value: float | None  # mg/dL, one decimal (as stored)

    # the time in each band (%), unrounded
    pct_time_in_target_range: float  # time in range (%): low limit to high limit
    pct_time_in_ideal_range: float  # time in tight range (%)
    time_low: float  # low (%): from the very-low limit to below the low limit
    time_high: float  # high (%): above the high limit up to the very-high limit
    time_critical_low: float  # very low (%)
    time_critical_high: float  # very high (%)

    # Glycemia Risk Index (Klonoff et al., 2023)
    # GRI = (3.0 x very low) + (2.4 x low) + (1.6 x very high) + (0.8 x high)
    # Score 0-100; zones A (best quintile) to E (worst quintile), see gri_zone()
    # Derived from rankings by 330 clinicians of 225 14-day CGM tracings (r = 0.95 with the clinician rankings)
    gri: float  # glycemia risk index (0-100)
    gri_hypo: float  # hypoglycemia component: very low + 0.8 x low
    gri_hyper: float  # hyperglycemia component: very high + 0.5 x high

    # The combined bands that the consensus targets apply to: all time below the low limit (level 1 and level 2
    # together, target less than 4%) and all time above the high limit (target less than 25%; Battelino et al.,
    # 2019), unrounded. A display rounds the combined value once; adding the two rounded parts can differ by 0.1
    # point and change the comparison with the target.
    time_below_low: float
    time_above_high: float
    # GMI in mmol/mol from the unrounded mean glucose (Bergenstal et al., 2018), the one rule for every surface
    gmi_mmol_mol: float | None

    # the covered time (s; the sum of the weights, "sensor active" x period) and the readings at the sensor's limit
    # (LO / HI): how many, and the seconds they represent. They are left out of every value (they have none)
    covered_seconds: float
    limit_low: int = 0
    limit_high: int = 0
    limit_low_seconds: float = 0.0
    limit_high_seconds: float = 0.0

    @property
    def gri_zone(self) -> str | None:
        """The GRI zone of the displayed GRI value (gri_zone())."""
        return gri_zone(self.gri)

    @property
    def sensor_active_exact(self) -> float:
        """Sensor active (%) before rounding: the covered time as a share of the period (sensor_active)."""
        return sensor_active(self.covered_seconds, self.period_seconds)

    @property
    def sensor_active_ok(self) -> bool:
        """True when sensor active is at least 70% (Battelino et al., 2019, Table 2), compared before rounding: 69.96%
        is shown as 70.0% with one decimal but does not meet the rule (units.shown_against shows it as 69.96%). The
        enough-data rule of every period (enough_data)."""
        return self.sensor_active_exact >= ENOUGH_DATA_PCT

    @property
    def limit_readings(self) -> dict[str, int]:
        """{"low": n, "high": n}: readings at the sensor's limit (LO / HI) in the period."""
        return {"low": self.limit_low, "high": self.limit_high}

    @property
    def limit_seconds(self) -> dict[str, int]:
        """{"low": s, "high": s}: the whole seconds those readings represent, up (time above zero stays above zero)."""
        return {"low": math.ceil(self.limit_low_seconds), "high": math.ceil(self.limit_high_seconds)}


def enough_data(stats: PeriodStats | None) -> bool:
    """Return True when a period's statistics may be shown: sensor active at least 70% of the period, compared before
    rounding (const.ENOUGH_DATA_PCT). Battelino et al. (2019, Table 2) give 70% for 14-day periods; applying it to every
    period length (hours, days, weeks, comparison windows) is a GlucoCraft rule. The one home of the enough-data rule:
    without enough data, the mean, SD, CV, GMI, GRI, the time in ranges and the lowest and highest value are not shown;
    the current reading, the graphs' readings and the time at the sensor's limit are not affected."""
    return stats is not None and stats.sensor_active_ok


def gmi_shown(stats: PeriodStats | None, period_seconds: int) -> float | None:
    """Return the GMI as every display shows it: only over 14 days or more with at least 70% sensor active, compared
    before rounding (Battelino et al., 2019), else None. The one home of the GMI rule."""
    if stats is None or not enough_data(stats) or period_seconds < GMI_MIN_PERIOD_SECONDS:
        return None
    return stats.gmi if stats.gmi else None


def gri_shown(stats: PeriodStats | None, period_seconds: int) -> float | None:
    """Return the GRI as every display shows it: only over 14 days or more with at least 70% sensor active, as the GMI
    (Klonoff et al., 2023, built the GRI on 14-day tracings; the 70%: enough_data), else None. `stats` must be on the
    consensus bands, the only bands the GRI is defined on (Klonoff et al., 2023). The one home of the GRI period
    rule."""
    if stats is None or not enough_data(stats) or period_seconds < GMI_MIN_PERIOD_SECONDS:
        return None
    return stats.gri


def band_flags(
    readings: Sequence[GlucoseReading],
    range_low: float = DEFAULT_RANGE_LOW,
    range_high: float = DEFAULT_RANGE_HIGH,
    range_ideal_low: float = DEFAULT_RANGE_TIGHT_LOW,
    range_ideal_high: float = DEFAULT_RANGE_TIGHT_HIGH,
    range_critical_low: float = DEFAULT_RANGE_CRITICAL_LOW,
    range_critical_high: float = DEFAULT_RANGE_CRITICAL_HIGH,
) -> BandFlags:
    """Return, for one range set, the six bands of the time in range as a flag per reading (in the band or not).

    The bands, by each reading's position on the glucose scale p (limits.scale_position, so that a LO reading is always
    very low and a HI reading always very high, as in the zone and the binary sensors): very low p < very low; low
    very low <= p < low; in range low <= p <= high; tight range tight low <= p <= tight high; high high < p <= very
    high; very high p > very high. A caller that computes several windows of one store makes
    the flags once for the store and passes each window's slices (window_flags).
    """
    pos = [scale_position(r.raw, r.limit) for r in readings]
    vlow, low, tlow = range_critical_low, range_low, range_ideal_low
    thigh, high, vhigh = range_ideal_high, range_high, range_critical_high
    return [
        [p < vlow for p in pos],
        [vlow <= p < low for p in pos],
        [low <= p <= high for p in pos],
        [tlow <= p <= thigh for p in pos],
        [high < p <= vhigh for p in pos],
        [p > vhigh for p in pos],
    ]


def _range_set(
    range_low: float,
    range_high: float,
    range_ideal_low: float,
    range_ideal_high: float,
    range_critical_low: float,
    range_critical_high: float,
) -> dict[str, float]:
    """One range set as the *_sets functions take it: the six range arguments by name (as const.AGP_RANGES)."""
    return {
        "range_low": range_low,
        "range_high": range_high,
        "range_ideal_low": range_ideal_low,
        "range_ideal_high": range_ideal_high,
        "range_critical_low": range_critical_low,
        "range_critical_high": range_critical_high,
    }


def window_flags(flags: RangeSetFlags, i: int, j: int) -> list[BandFlags]:
    """Return the window [i:j] of a store's band flags, for each range set (flags: band_flags per set)."""
    return [[band[i:j] for band in bands] for bands in flags]


def compute_period_stats(
    readings: list[GlucoseReading],
    period_label: str,
    period_seconds: int,
    detected_interval: int,
    range_low: float = DEFAULT_RANGE_LOW,
    range_high: float = DEFAULT_RANGE_HIGH,
    range_ideal_low: float = DEFAULT_RANGE_TIGHT_LOW,
    range_ideal_high: float = DEFAULT_RANGE_TIGHT_HIGH,
    range_critical_low: float = DEFAULT_RANGE_CRITICAL_LOW,
    range_critical_high: float = DEFAULT_RANGE_CRITICAL_HIGH,
    intervals: Sequence[int] | None = None,
    weights: Sequence[float] | None = None,
) -> PeriodStats | None:
    """Compute time-weighted statistics for a set of readings.

    Each reading's weight = seconds until the next reading, or its own reading interval when that interval is a gap
    (longer than max(15 min, 3 x the reading interval); gaps.py). The last reading weighs its own reading interval.
    intervals: the reading interval at each reading (aligned; storage.reading_intervals, so a stretch is judged by the
    readings around it, also outside the window); None = from these readings, detected_interval when they give none.
    weights: the weights themselves (gaps.window_weights from the store's, the coordinator's fast path; or the seconds
    each reading represents inside a period of the CGM Report, report.py).
    """
    ranges = _range_set(
        range_low, range_high, range_ideal_low, range_ideal_high, range_critical_low, range_critical_high
    )
    return compute_period_stats_sets(
        readings, period_label, period_seconds, detected_interval, (ranges,), intervals, weights
    )[0]


def compute_period_stats_sets(
    readings: list[GlucoseReading],
    period_label: str,
    period_seconds: int,
    detected_interval: int,
    range_sets: Sequence[dict[str, float]],
    intervals: Sequence[int] | None = None,
    weights: Sequence[float] | None = None,
    flags: RangeSetFlags | None = None,
) -> list[PeriodStats | None]:
    """Return compute_period_stats for each range set of `range_sets` (dicts of its six range arguments, as
    const.AGP_RANGES), in that order.

    Only the six band sums depend on the ranges: the other values (the mean, SD, CV and GMI and the lowest and highest
    value, from the measured readings; sensor active and the limit readings) are computed once for all sets. flags: each
    set's band flags of these readings (window_flags of the store's band_flags), None = from the readings. Each result
    is identical to compute_period_stats with its set alone (the same sums of the same values in the same order).
    """
    if not readings:
        return [None] * len(range_sets)

    if weights is None:
        ts = [r.timestamp for r in readings]
        iv = list(intervals) if intervals is not None else local_intervals(ts, detected_interval)
        weights = gap_weights(ts, iv)

    total_weight = sum(weights)
    if total_weight == 0:
        return [None] * len(range_sets)

    # Sensor active (data_availability): the share of the period that the readings cover by the gap rule (the weights,
    # gaps.py)
    covered_time = total_weight
    data_availability = sensor_active(covered_time, period_seconds)
    # The limit readings (LO / HI): counted with the seconds they represent, and left out of every value below
    limit_counts = {LIMIT_LOW: 0, LIMIT_HIGH: 0}
    limit_seconds = {LIMIT_LOW: 0.0, LIMIT_HIGH: 0.0}
    measured: list[tuple[float, float]] = []
    # strict: each reading has exactly one weight; a mismatch is a caller's error, which must fail here rather than
    # quietly leave readings out while total_weight still counts their time
    for r, w in zip(readings, weights, strict=True):
        if r.limit is None:
            measured.append((r.raw, w))
        elif r.limit in limit_counts:
            limit_counts[r.limit] += 1
            limit_seconds[r.limit] += w

    # Time-weighted mean, over the time the measured readings represent
    measured_weight = sum(w for _, w in measured)
    mean = std_dev = cv = gmi = low = high = None
    if measured_weight > 0:
        mean = sum(v * w for v, w in measured) / measured_weight

        # Time-weighted standard deviation and CV (coefficient of variation): none for a single reading
        if len(measured) >= 2:
            std_dev = math.sqrt(sum(w * (v - mean) ** 2 for v, w in measured) / measured_weight)
            cv = (std_dev / mean * 100) if mean > 0 else 0.0

        # GMI (Glucose Management Indicator) = 3.31 + 0.02392 x mean glucose (mg/dL) (units.gmi_percent)
        gmi = round_half_up(gmi_percent(mean), 1)
        low = round_half_up(min(v for v, _ in measured), 1)
        high = round_half_up(max(v for v, _ in measured), 1)

    # the values that do not depend on the ranges: the same for every range set
    common = {
        "period_label": period_label,
        "period_seconds": period_seconds,
        "reading_count": len(readings),
        "data_availability": data_availability,
        "mean": mean,
        "std_dev": std_dev,
        "cv": cv,
        "gmi": gmi,
        "min_value": low,
        "max_value": high,
        "covered_seconds": covered_time,
        "limit_low": limit_counts[LIMIT_LOW],
        "limit_high": limit_counts[LIMIT_HIGH],
        "limit_low_seconds": limit_seconds[LIMIT_LOW],
        "limit_high_seconds": limit_seconds[LIMIT_HIGH],
        "gmi_mmol_mol": gmi_mmol_mol(mean),
    }
    if flags is None:
        flags = [band_flags(readings, **ranges) for ranges in range_sets]
    return [_with_bands(common, bands, weights, total_weight) for bands in flags]


def _with_bands(common: dict, bands: BandFlags, weights: Sequence[float], total_weight: float) -> PeriodStats:
    """Return the PeriodStats of one range set: the time in its six bands (band_flags), the percentages (unrounded)
    and the GRI, with the values `common` to every range set.

    A band's time is the sum of its readings' weights in their order: the same sum as
    sum(w for p, w in zip(positions, weights) if <p in the band>), with the loop run by itertools.compress.
    """
    vlow_w, low_w, in_range_w, tight_w, high_w, vhigh_w = (sum(compress(weights, band)) for band in bands)

    def pct(w: float) -> float:
        """The share of the time in percent, multiplied before the division: an exact share stays exact (4.0, not
        3.9999999999999996 from w / total x 100), so that a value at a target is judged and shown as itself."""
        return 100.0 * w / total_weight

    pct_low, pct_high, pct_very_low, pct_very_high = pct(low_w), pct(high_w), pct(vlow_w), pct(vhigh_w)

    # the Glycemia Risk Index (Klonoff et al., 2023): gri()
    gri_score, gri_hypo, gri_hyper, _ = gri(pct_very_low, pct_low, pct_high, pct_very_high)

    return PeriodStats(
        **common,
        pct_time_in_target_range=pct(in_range_w),
        pct_time_in_ideal_range=pct(tight_w),
        time_low=pct_low,
        time_high=pct_high,
        time_critical_low=pct_very_low,
        time_critical_high=pct_very_high,
        gri=gri_score,
        gri_hypo=gri_hypo,
        gri_hyper=gri_hyper,
        # the combined bands from the sum of their seconds, not the sum of two percentages
        time_below_low=pct(low_w + vlow_w),
        time_above_high=pct(high_w + vhigh_w),
    )


def compute_weekly_trends(
    readings: list[GlucoseReading],
    num_weeks: int = 26,
    detected_interval: int = DEFAULT_INTERVAL_SECONDS,
    range_low: float = DEFAULT_RANGE_LOW,
    range_high: float = DEFAULT_RANGE_HIGH,
    range_ideal_low: float = DEFAULT_RANGE_TIGHT_LOW,
    range_ideal_high: float = DEFAULT_RANGE_TIGHT_HIGH,
    range_critical_low: float = DEFAULT_RANGE_CRITICAL_LOW,
    range_critical_high: float = DEFAULT_RANGE_CRITICAL_HIGH,
    intervals: Sequence[int] | None = None,
    now: int | None = None,
    spans: Sequence[float] | None = None,
    windows: Sequence[tuple[int, int]] | None = None,
) -> list[PeriodStats | None]:
    """Compute stats for each of the last N weeks (readings sorted by time; each week found by bisection).
    intervals: the reading interval at each reading (aligned with readings), None = from the readings; spans: the
    readings' weights over the whole list (gaps.weights), None = computed here.
    windows: explicit (start, end) epoch seconds of each week, newest first, used instead of num_weeks and now. The
    CGM Report passes weeks of 7 local days (local midnight to local midnight), which are 167 or 169 hours long across
    a daylight-saving change; without windows each week is 7 x 24 hours counted back from now.
    The week rule is the enough-data rule (enough_data; as the weekly pages of the display card and the panels and
    Change Over Time): a week counts only with sensor active at least 70% of the week (covered time by the gap rule); a
    week with less is None, so every consumer of the weekly statistics shows it as a week without data."""
    ranges = _range_set(
        range_low, range_high, range_ideal_low, range_ideal_high, range_critical_low, range_critical_high
    )
    return compute_weekly_trends_sets(
        readings, (ranges,), num_weeks, detected_interval, intervals, now, spans, windows
    )[0]


def compute_weekly_trends_sets(
    readings: list[GlucoseReading],
    range_sets: Sequence[dict[str, float]],
    num_weeks: int = 26,
    detected_interval: int = DEFAULT_INTERVAL_SECONDS,
    intervals: Sequence[int] | None = None,
    now: int | None = None,
    spans: Sequence[float] | None = None,
    windows: Sequence[tuple[int, int]] | None = None,
    flags: RangeSetFlags | None = None,
) -> list[list[PeriodStats | None]]:
    """Return compute_weekly_trends for each range set of `range_sets`, in that order (compute_period_stats_sets).

    The weeks, their weights and the week rule (covered time, the same for every set) are found once; flags: each
    set's band flags over the whole list (band_flags), None = computed here.
    """
    week_seconds = 7 * 86400
    if windows is None:
        now = int(time.time()) if now is None else int(now)
        windows = [(now - (k + 1) * week_seconds, now - k * week_seconds) for k in range(num_weeks)]
    results: list[list[PeriodStats | None]] = [[] for _ in range_sets]
    ts = [r.timestamp for r in readings]
    iv = list(intervals) if intervals is not None else local_intervals(ts, detected_interval)
    sp = spans if spans is not None else gap_weights(ts, iv)
    if flags is None:
        flags = [band_flags(readings, **ranges) for ranges in range_sets]

    for week_num, (start_ts, end_ts) in enumerate(windows):
        i, j = bisect.bisect_left(ts, start_ts), bisect.bisect_left(ts, end_ts)
        per_set = compute_period_stats_sets(
            readings[i:j],
            f"week_{week_num}",
            int(end_ts - start_ts),
            detected_interval,
            range_sets,
            weights=window_weights(sp, iv, i, j),
            flags=window_flags(flags, i, j),
        )
        for out, stats in zip(results, per_set, strict=True):
            out.append(stats if enough_data(stats) else None)  # the week rule: the enough-data rule

    return results
