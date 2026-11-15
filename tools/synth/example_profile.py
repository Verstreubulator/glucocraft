# License: AGPL-3.0-only (LICENSE).
"""The neutral example profile: ARTIFICIAL characteristics, not fitted from any person's data.

The numbers are invented to look like a plausible 1-minute CGM history of an adult with diabetes: a flat night, a
rise before waking, three meal peaks, a few lows and highs per week, short Bluetooth dropouts. They are not a clinical
reference and say nothing about any real person. generate.py uses this profile unless it is given a fitted one.
"""

from __future__ import annotations

import math

from .profile import BIN_MINUTES, BINS, PROFILE_KIND, PROFILE_VERSION

# Daily pattern: (local hour of the peak, height in mg/dL, width in hours)
_BUMPS = ((7.0, 18.0, 1.2), (8.75, 42.0, 1.0), (13.25, 32.0, 1.1), (19.25, 46.0, 1.3))
_BASE = 128.0


def _bump(hour: float) -> float:
    """The daily pattern's rise above the base at a local hour (wrapping around midnight)."""
    total = 0.0
    for peak, height, width in _BUMPS:
        d = min(abs(hour - peak), 24 - abs(hour - peak))
        total += height * math.exp(-0.5 * (d / width) ** 2)
    return total


def _per_week(per_week: float, hours_per_week: float) -> dict:
    return {"per_week": per_week, "per_covered_hour": round(per_week / hours_per_week, 6)}


def example_profile() -> dict:
    """Return the artificial example profile (the same structure as profile.fit's)."""
    tod: dict[str, list[float]] = {k: [] for k in ("p5", "p25", "p50", "p75", "p95", "mean")}
    for b in range(BINS):
        hour = (b + 0.5) * BIN_MINUTES / 60
        rise = _bump(hour)
        p50 = _BASE + rise
        tod["p50"].append(round(p50, 2))
        tod["p25"].append(round(p50 - 24 - 0.25 * rise, 2))
        tod["p75"].append(round(p50 + 30 + 0.35 * rise, 2))
        tod["p5"].append(round(p50 - 52 - 0.35 * rise, 2))
        tod["p95"].append(round(p50 + 88 + 0.6 * rise, 2))
        tod["mean"].append(round(p50 + 6, 2))
    day_h, night_h = 18 * 7, 6 * 7  # covered hours per week by day (06:00-23:59) and by night
    return {
        "kind": PROFILE_KIND,
        "version": PROFILE_VERSION,
        "artificial": True,
        "description": "Artificial example profile (invented values, not fitted from any person's data).",
        "source": {"unit": "mg/dL", "precision": 0, "median_interval_s": 60, "tz": "America/Los_Angeles"},
        "summary": {},
        "time_of_day": {"bin_minutes": BIN_MINUTES} | tod,
        "variability": {"grid_seconds": 300, "z_sd": 1.0, "ar1_phi": 0.96, "ar1_innovation_sd": 0.28},
        "rate_of_change": {"unit": "mg/dL/min"},
        "noise": {"sd_mgdl": 1.0},
        "excursions": {
            "below_70": {
                "day": _per_week(3.0, day_h),
                "night": _per_week(1.5, night_h),
                "duration_min": {"p10": 17, "p25": 22, "p50": 35, "p75": 55, "p90": 85},
                "extreme_mgdl": {"p10": 52, "p25": 57, "p50": 62, "p75": 65, "p90": 68},
            },
            "below_54": {
                "day": _per_week(0.8, day_h),
                "night": _per_week(0.5, night_h),
                "duration_min": {"p10": 16, "p25": 19, "p50": 25, "p75": 40, "p90": 60},
                "extreme_mgdl": {"p10": 42, "p25": 45, "p50": 48, "p75": 51, "p90": 53},
            },
            "above_180": {
                "day": _per_week(10.0, day_h),
                "night": _per_week(2.0, night_h),
                "duration_min": {"p10": 30, "p25": 55, "p50": 100, "p75": 160, "p90": 240},
                "extreme_mgdl": {"p10": 192, "p25": 205, "p50": 225, "p75": 250, "p90": 280},
            },
            "above_250": {
                "day": _per_week(2.0, day_h),
                "night": _per_week(0.5, night_h),
                "duration_min": {"p10": 20, "p25": 35, "p50": 60, "p75": 100, "p90": 150},
                "extreme_mgdl": {"p10": 258, "p25": 268, "p50": 285, "p75": 310, "p90": 340},
            },
        },
        "intervals": {
            "median_s": 60,
            "histogram": [[55, 0.04], [60, 0.92], [65, 0.04]],
            "missed_share": 0.01,
            "jitter_sd_s": 2.0,
        },
        "gaps": {
            "threshold_s": 900,
            "per_week": 3.0,
            "length_min": {"p10": 16, "p25": 20, "p50": 35, "p75": 80, "p90": 180, "max": 600},
        },
        "limits": {"lo_per_week": 0.05, "hi_per_week": 0.05},
        "no_reading_states": {"per_week": 2.0},
    }
