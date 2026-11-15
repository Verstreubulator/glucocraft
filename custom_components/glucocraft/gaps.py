# License: AGPL-3.0-only (LICENSE).
"""The gap rule and the reading interval, shared by the statistics, "sensor active", the report and every graph.

A gap is an interval between two readings that is longer than max(15 minutes, 3 x the reading interval). The reading
interval is decided per stretch, from the readings around it: the median of the 10 intervals before the reading (filled
with the intervals after it when fewer than 10 exist), never including the interval being judged. So it adapts when
1-minute readings resume after 15-minute history (the history of FreeStyle Libre systems arrives in 15-minute steps),
and when a second source with another interval fills a stretch. Why these numbers (GlucoCraft rule; no consensus rule
exists): 15 minutes is the "no current data" default and the stale-data warning of Nightscout; 3 intervals allow short
sync dropouts (two missed readings) without counting the sensor as inactive; the 15-minute floor keeps 1-minute systems
from losing time over normal Bluetooth delays.

Each reading represents the time until the next reading, or, when that interval is a gap (or it is the last reading),
its own reading interval. The statistics weight every reading by that time, and "sensor active" is that covered time
divided by the period. The wall panels compute the same rule from the readings they have.

``median_interval`` is the one function for a source's reading interval: the coordinator (each source's own interval,
the gap threshold shown in the configuration) and the storage (the detected interval saved in the file header) call
it with the window and filter they need.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

from .const import GAP_FLOOR_SECONDS, GAP_INTERVAL_FACTOR, INTERVAL_WINDOW

MIN_INTERVALS = 3  # fewer intervals around a reading: the detected interval is used (a median of 1 or 2 says little)


def gap_threshold(interval: float | None) -> int:
    """Return the longest interval that is not a gap: max(15 min, 3 x the reading interval)."""
    return max(GAP_FLOOR_SECONDS, GAP_INTERVAL_FACTOR * int(interval or 0))


def median_interval(
    times: Sequence[int],
    window: int | None = INTERVAL_WINDOW,
    shortest: int = 1,
    longest: int | None = None,
    minimum: int = MIN_INTERVALS,
) -> int | None:
    """Return the median of the newest intervals of a sorted list of measurement times (whole seconds).

    window: how many of the newest intervals are looked at (None: all of them). shortest, longest: the intervals that
    count, in seconds, both inclusive (longest None: no upper limit; the default shortest of 1 s drops repeated
    times). minimum: with fewer counted intervals the result is None. For an even count the upper middle value is
    the median.
    """
    t = list(times) if window is None else list(times[-window - 1 :])
    d = [b - a for a, b in pairwise(t) if b - a >= shortest and (longest is None or b - a <= longest)]
    if len(d) < minimum:
        return None
    return sorted(d)[len(d) // 2]


def local_intervals(ts: Sequence[int], fallback: int, start: int = 0, window: int = INTERVAL_WINDOW) -> list[int]:
    """Return the reading interval at each reading ts[start:] (ts sorted).

    The interval at a reading is the median (upper middle) of the `window` intervals before it, filled with the
    intervals after the next one when fewer exist; `fallback` (the detected interval) with fewer than 3. The interval
    that starts at the reading itself is never included, because that is the interval the gap rule judges.
    """
    n = len(ts)
    d = [ts[k + 1] - ts[k] for k in range(n - 1)]
    out: list[int] = []
    for i in range(start, n):
        cands = d[max(0, i - window) : i]
        if len(cands) < window:
            cands = cands + d[i + 1 : i + 1 + window - len(cands)]
        cands = [c for c in cands if c > 0]
        out.append(sorted(cands)[len(cands) // 2] if len(cands) >= MIN_INTERVALS else int(fallback or 0))
    return out


def window_weights(spans: Sequence[float], iv: Sequence[int], i: int, j: int) -> list[float]:
    """Return weights() of the window [i:j] of a store, taken from the store's own weights.

    spans = weights(all times, all intervals). The result is identical to computing the window on its own, because
    inside the window the next reading is the store's next reading; only the window's last reading weighs its own
    interval.
    """
    if j <= i:
        return []
    return [*spans[i : j - 1], float(iv[j - 1])]


def weights(ts: Sequence[int], iv: Sequence[int]) -> list[float]:
    """Return the seconds each reading of a window represents (ts and iv aligned).

    That is the time to the next reading of the window, or the reading's own reading interval when that time is a gap;
    the window's last reading weighs its own reading interval. The gap threshold of each reading interval is found once
    (a store has few distinct intervals).
    """
    n = len(ts)
    out: list[float] = []
    thresholds: dict[int, int] = {}
    for k in range(n):
        if k + 1 < n:
            delta = ts[k + 1] - ts[k]
            limit = thresholds.get(iv[k])
            if limit is None:
                limit = thresholds[iv[k]] = gap_threshold(iv[k])
            out.append(float(delta) if delta <= limit else float(iv[k]))
        else:
            out.append(float(iv[k]))
    return out
