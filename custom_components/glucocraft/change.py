# License: AGPL-3.0-only (LICENSE).
"""Change Over Time: progress against the consensus targets and against the user's own goals.

For each of the two range sets - the consensus ranges and the custom ranges (those in the settings) - over 26 weeks:
  * weeks14: the 14 days ending at each week's end (week w ends at now - w x 7 days); the values the targets and
    colors use (14 days is the consensus period, Battelino et al., 2019);
  * weeks7: the 7-day values of the same weeks, shown only as faint dots (too short for a comparison);
  * compare: the last 4 weeks, 9-13 weeks ago and 22-26 weeks ago.

One metric set for both range sets: tir / titr (in range / tight range), below / very_low (all time below the low
limit / below the very-low limit), above / very_high, mean, sd, cv, gmi and gmi_mmol_mol (14 days or more with at least
70% sensor active only, Bergenstal et al., 2018, from the mean glucose), gri (the GRI: on the consensus ranges only, the
only bands it is defined on (Klonoff et al., 2023), and for 14 days or more, statistics.gri_shown; None otherwise), min,
max (measured values), active (sensor active %), hours (hours of data). The percentages and the CV are unrounded
(statistics.PeriodStats): a display rounds each once. Runs in the executor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .const import AGP_RANGES, FALLBACK_INTERVAL_SECONDS
from .gaps import weights, window_weights
from .statistics import (
    PeriodStats,
    band_flags,
    compute_period_stats_sets,
    enough_data,
    gmi_shown,
    gri_shown,
    window_flags,
)
from .units import round_half_up

DAY = 86400
WEEKS = 26
# The comparison windows of Change Over Time (ChangeOverTime.compare); a window without enough data is None (_values)
COMPARE = (("last_4_weeks", 0, 28), ("weeks_9_13_ago", 63, 91), ("weeks_22_26_ago", 154, 182))  # days back: start, end


@dataclass
class ChangeOverTime:
    """The Change Over Time data of one standard (compute_change_sets), newest week first.

    weeks14, weeks7: each week's values (_values) of the 14 and the 7 days ending at its end; week_ends: those ends
    (epoch seconds); compare: the values of each window of COMPARE by its name; stats14: the 14-day statistics
    themselves (None for a window without enough data, statistics.enough_data), which the coordinator sends to the
    display card and the panels as the weekly values. The Change Over Time entity publishes all but stats14."""

    weeks14: list[dict[str, Any] | None] = field(default_factory=list)
    weeks7: list[dict[str, Any] | None] = field(default_factory=list)
    week_ends: list[int] = field(default_factory=list)
    compare: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    stats14: list[PeriodStats | None] = field(default_factory=list)


def _values(s, period_days: int, consensus: bool) -> dict[str, Any] | None:
    """Return a window's values; None without enough data (sensor active below 70% of the window, compared before
    rounding: statistics.enough_data; covered time by the gap rule, gaps.py). consensus: the window is on the
    consensus ranges (only then can it have a GRI)."""
    if s is None or not enough_data(s):
        return None
    # GMI only for 14 days or more with at least 70% sensor active, compared before rounding (statistics.gmi_shown)
    gmi = gmi_shown(s, period_days * DAY)
    return {
        # the percentages and the CV unrounded: a display rounds each once
        "tir": s.pct_time_in_target_range,
        "titr": s.pct_time_in_ideal_range,
        # the combined bands, calculated once (not the sum of two rounded parts)
        "below": s.time_below_low,
        "very_low": s.time_critical_low,
        "above": s.time_above_high,
        "very_high": s.time_critical_high,
        # the glucose values (mg/dL) as calculated: the mean and the SD unrounded, the lowest and highest as stored;
        # the cards round them once, in the display unit (a value rounded to whole mg/dL first would be rounded twice
        # in mmol/L)
        "mean": s.mean,
        "sd": s.std_dev,
        "cv": s.cv,
        "gmi": gmi,  # one decimal, as the GMI is shown
        "gmi_mmol_mol": s.gmi_mmol_mol if gmi is not None else None,
        "gri": gri_shown(s, period_days * DAY) if consensus else None,
        "min": s.min_value,
        "max": s.max_value,
        "active": s.data_availability,
        # at least 70% sensor active, compared before rounding (statistics.sensor_active_ok): the 70% rules read this
        # (69.96% is 70.0% with one decimal but does not meet the rule)
        "active_ok": s.sensor_active_ok,
        "hours": round_half_up(s.covered_seconds / 3600, 0),
    }


def compute_change_sets(
    storage,
    interval: int,
    now: int,
    range_sets,
    intervals: list[int] | None = None,
    spans: list[float] | None = None,
    flags: list | None = None,
) -> list[ChangeOverTime]:
    """Return the Change Over Time data for each range set of `range_sets` (dicts of compute_period_stats's range
    arguments), in that order.

    intervals: storage.reading_intervals() (computed here when not given), so that every window judges its gaps by the
    readings around it; spans: the store's weights (gaps.weights), computed here when not given. Every window is
    found, and its range-independent values computed, once for all sets (statistics.compute_period_stats_sets). flags:
    each set's band flags of the store (statistics.band_flags), computed here when not given.
    """
    interval = int(interval or FALLBACK_INTERVAL_SECONDS)
    iv = intervals if intervals is not None else storage.reading_intervals(interval)
    readings = storage.readings
    sp = spans if spans is not None else weights([r.timestamp for r in readings], iv)
    if flags is None:
        flags = [band_flags(readings, **ranges) for ranges in range_sets]
    none = [None] * len(range_sets)

    def stats(start: int, end: int, label: str) -> list:
        """Return the statistics of each range set for the readings from start to end (None for each set when the
        window has fewer than two readings)."""
        i, j = storage.window_indices(start, end)
        if j - i < 2:
            return none
        return compute_period_stats_sets(
            readings[i:j],
            label,
            end - start,
            interval,
            range_sets,
            weights=window_weights(sp, iv, i, j),
            flags=window_flags(flags, i, j),
        )

    out = [ChangeOverTime() for _ in range_sets]
    consensus = [dict(ranges) == AGP_RANGES for ranges in range_sets]  # the sets that can have a GRI
    for w in range(WEEKS):
        end = now - w * 7 * DAY
        sets14, sets7 = stats(end - 14 * DAY, end, f"w14_{w}"), stats(end - 7 * DAY, end, f"w7_{w}")
        for o, s14, s7, agp in zip(out, sets14, sets7, consensus, strict=True):
            o.week_ends.append(end)
            o.stats14.append(s14 if enough_data(s14) else None)
            o.weeks14.append(_values(s14, 14, agp))
            o.weeks7.append(_values(s7, 7, agp))
    for name, a, b in COMPARE:
        for o, s, agp in zip(out, stats(now - b * DAY, now - a * DAY, name), consensus, strict=True):
            o.compare[name] = _values(s, b - a, agp)
    return out
