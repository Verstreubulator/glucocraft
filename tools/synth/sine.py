# License: AGPL-3.0-only (LICENSE).
"""Deterministic sine-wave series: three peaks and three valleys per local day. All data are artificial.

The value at time t is offset + amplitude x sin(2 pi x 3 x (t - start) / 1 day), written in the source unit at the
source precision (mg/dL whole numbers, mmol/L one decimal), one reading every `interval` seconds from a local
midnight, without gaps, noise or jitter. The deviation from the offset is what is rounded (halves away from zero, the
same for a deviation up and down), so the readings of each cycle pair up exactly around the offset. Offset and amplitude are given in the source unit, so that a wave can be
shifted up and down to lie inside one glucose range or to cross several (RANGES, in both units).

Because the wave is known exactly, so are its statistics: expected.sine_statistics gives the closed-form values (the
time in each range from the arcsine, the mean, the standard deviation amplitude / sqrt(2)), and the tests compare the
independent calculator on the sampled series with them, and GlucoCraft with the calculator.

Usage:
  from tools.synth import sine
  sc = sine.scenario("tight_range", "mmol/L", 300)
  series = sine.series(105, 30, "mg/dL", 60, start, end)
"""

from __future__ import annotations

import math
from datetime import datetime
from fractions import Fraction

from .common import DEFAULT_TZ, UNIT_MMOLL, UNITS, Series, SourceState, round_half_up, unit_name, value_text
from .scenarios import DATA_START, DELAY, NOW, Scenario

PEAKS_PER_DAY = 3
INTERVALS = (60, 300, 900)  # the reading intervals of the tests: 1, 5 and 15 minutes

# (offset, amplitude) per source unit. Each "in one range" wave keeps its rounded values inside that range with a
# margin of at least one display step; the crossing waves pass through several ranges. LO is 40 mg/dL / 2.2 mmol/L
# and below, HI above 500 mg/dL: no wave reaches them.
RANGES: dict[str, dict[str, tuple[float, float]]] = {
    "very_low": {"mg/dL": (45, 4), UNIT_MMOLL: (2.5, 0.2)},  # 41-49 mg/dL, 2.3-2.7 mmol/L
    "low": {"mg/dL": (62, 6), UNIT_MMOLL: (3.4, 0.3)},  # 56-68, 3.1-3.7
    "tight_range": {"mg/dL": (105, 30), UNIT_MMOLL: (5.8, 1.6)},  # 75-135, 4.2-7.4
    "in_range_above_tight": {"mg/dL": (160, 15), UNIT_MMOLL: (8.9, 0.8)},  # 145-175, 8.1-9.7
    "high": {"mg/dL": (215, 30), UNIT_MMOLL: (12.0, 1.5)},  # 185-245, 10.5-13.5
    "very_high": {"mg/dL": (320, 60), UNIT_MMOLL: (17.8, 3.3)},  # 260-380, 14.5-21.1
    "low_to_high": {"mg/dL": (140, 100), UNIT_MMOLL: (7.8, 5.5)},  # 40-240, 2.3-13.3
    "every_range": {"mg/dL": (220, 179), UNIT_MMOLL: (12.2, 9.9)},  # 41-399, 2.3-22.1
}
NAMES = tuple(RANGES)


CYCLE = 86400 // PEAKS_PER_DAY  # seconds


def _sine(seconds: int) -> float:
    """sin(2 pi x seconds / CYCLE), from the exact phase reduced to the first quarter cycle: values half a cycle apart
    are then exact negatives of each other (the floating-point sine of the unreduced angles differs in the last digit,
    enough to round a half up on one side and down on the other)."""
    phase = Fraction(seconds % CYCLE, CYCLE)
    sign = 1.0
    if phase >= Fraction(1, 2):
        phase, sign = phase - Fraction(1, 2), -1.0
    if phase > Fraction(1, 4):
        phase = Fraction(1, 2) - phase
    return sign * math.sin(2 * math.pi * float(phase))


def value_at(seconds: int, offset: float, amplitude: float) -> float:
    """The wave's value `seconds` (whole) after its start, in the unit of offset and amplitude."""
    return offset + amplitude * _sine(seconds)


def reading_text(seconds: int, offset: float, amplitude: float, unit: str) -> str:
    """The state text of the reading `seconds` after the start: the offset plus the deviation rounded to the source
    precision (an offset with more decimals than the source shows is rounded with it)."""
    decimals = 1 if unit_name(unit) == UNIT_MMOLL else 0
    deviation = round_half_up(value_at(seconds, 0.0, amplitude), decimals)
    return value_text(offset + deviation, unit)


def series(
    offset: float,
    amplitude: float,
    unit: str,
    interval: int,
    start: datetime,
    end: datetime,
    tz: str = DEFAULT_TZ,
    delay: int = DELAY,
) -> Series:
    """The wave from `start` (a local midnight, time zone aware) up to `end`, one reading every `interval` seconds."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be time zone aware")
    if not (isinstance(interval, int) and interval > 0 and 86400 % (PEAKS_PER_DAY * interval) == 0):
        raise ValueError("the interval must divide a third of a day (whole cycles per day): 60, 300 or 900 s")
    if not (math.isfinite(offset) and math.isfinite(amplitude) and amplitude >= 0):
        raise ValueError("offset and amplitude must be finite, the amplitude 0 or more")
    unit = unit_name(unit)
    t0, t_end = int(start.timestamp()), int(end.timestamp())
    states = [
        SourceState(recorded=t + delay, state=reading_text(t - t0, offset, amplitude, unit), measured=t)
        for t in range(t0, t_end, interval)
    ]
    return Series(
        unit=unit,
        interval=interval,
        tz=tz,
        states=states,
        description=f"Sine wave {offset:g} +/- {amplitude:g} {unit}, {PEAKS_PER_DAY} peaks per day "
        "(tools/synth/sine.py); artificial.",
        meta={"offset": offset, "amplitude": amplitude, "peaks_per_day": PEAKS_PER_DAY},
    )


def scenario(name: str, unit: str, interval: int) -> Scenario:
    """The wave RANGES[name] in `unit` as a scenario: 15 days of data (the scenarios' DATA_START to NOW), evaluated at
    NOW (a local midnight, no daylight-saving change nearby), so that every period holds whole cycles."""
    if name not in RANGES:
        raise ValueError(f"unknown sine range {name!r}; known: {', '.join(NAMES)}")
    unit = unit_name(unit)
    offset, amplitude = RANGES[name][unit]
    s = series(offset, amplitude, unit, interval, DATA_START, NOW)
    return Scenario(
        name=f"sine_{name}_{interval // 60}min",
        unit=unit,
        interval=interval,
        description=s.description,
        states=s.states,
        now=int(NOW.timestamp()),
        tags=("sine",),
    )


def all_scenarios() -> list[Scenario]:
    """Every sine range in both units at every interval."""
    return [scenario(n, u, i) for n in NAMES for u in UNITS for i in INTERVALS]
