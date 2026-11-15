# License: AGPL-3.0-only (LICENSE).
"""The stretches of readings at the sensor's limit (LO / HI): where the graphs mark them, and the current one.

A reading at the sensor's limit has no value, so no graph draws it as a point and no statistic uses its number; the
graphs mark where such readings were instead (a run of small marks along the bottom or the top edge). A stretch (a
"limit run") is an unbroken run of limit readings of the same kind: it ends at a measured reading, at a reading of the
other kind, or at a data gap by the gap rule (gaps.py), and a new stretch starts after it. It runs from its first
reading to the end of the time its last reading represents (the time to the next reading, or that reading's own
reading interval when a gap follows or it is the newest), the same time the period statistics count as time at the
limit (statistics.PeriodStats.limit_seconds). This method is a GlucoCraft rule.
"""

from __future__ import annotations

from collections.abc import Sequence

from .gaps import gap_threshold
from .limits import LIMIT_FLAGS
from .storage import GlucoseReading


def _joined(readings: Sequence[GlucoseReading], intervals: Sequence[int], k: int) -> bool:
    """True when reading k continues the limit run of reading k - 1: the same kind, and no gap between them."""
    prev, cur = readings[k - 1], readings[k]
    return prev.limit == cur.limit and cur.timestamp - prev.timestamp <= gap_threshold(intervals[k - 1])


def _represented(readings: Sequence[GlucoseReading], intervals: Sequence[int], k: int) -> int:
    """The seconds reading k represents: to the next reading, or its own reading interval when a gap follows or it is
    the last one (gaps.weights)."""
    if k + 1 < len(readings):
        delta = readings[k + 1].timestamp - readings[k].timestamp
        if delta <= gap_threshold(intervals[k]):
            return delta
    return int(intervals[k])


def limit_runs(
    readings: Sequence[GlucoseReading], intervals: Sequence[int], since: int = 0, until: int | None = None
) -> list[tuple[int, int, str]]:
    """Return the limit runs that lie in [since, until) as (start, end, "low" | "high"), oldest first.

    readings: the stored readings, oldest first; intervals: the reading interval at each one (aligned;
    storage.reading_intervals). A run that began before `since` is cut at `since` (and one past `until` at `until`), so
    that a period's graph marks only its own part of it.
    """
    out: list[tuple[int, int, str]] = []
    n = len(readings)
    k = 0
    while k < n:
        r = readings[k]
        if r.limit not in LIMIT_FLAGS:
            k += 1
            continue
        first = k
        while k + 1 < n and _joined(readings, intervals, k + 1):
            k += 1
        start = readings[first].timestamp
        end = readings[k].timestamp + _represented(readings, intervals, k)
        start, end = max(start, since), end if until is None else min(end, until)
        if end > start:
            out.append((start, end, r.limit))
        k += 1
    return out


def current_limit_run(readings: Sequence[GlucoseReading], intervals: Sequence[int]) -> int | None:
    """Return the index of the first reading of the run of limit readings the newest reading belongs to, or None when
    the newest reading is measured (or there is none). The run is walked back from the newest reading."""
    if not readings or readings[-1].limit not in LIMIT_FLAGS:
        return None
    k = len(readings) - 1
    while k > 0 and _joined(readings, intervals, k):
        k -= 1
    return k


def last_measured_before(readings: Sequence[GlucoseReading], index: int) -> GlucoseReading | None:
    """Return the newest measured reading at or before `index` (None when there is none)."""
    for k in range(min(index, len(readings) - 1), -1, -1):
        if readings[k].limit not in LIMIT_FLAGS:
            return readings[k]
    return None
