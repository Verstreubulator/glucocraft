# License: AGPL-3.0-only (LICENSE).
"""A seeded generator of realistic, ARTIFICIAL CGM series built from a profile (profile.py, example_profile.py).

The model, per reading:
  1. time of day: the profile's 5th to 95th percentiles at the reading's local clock time (interpolated between the
     15-minute bins);
  2. a unit-variance AR(2) process z on a 5-minute grid (read every minute by interpolation), mapped onto that time
     of day through the percentiles (profile.z_to_value). Its 5-minute autocorrelation is the profile's coefficient
     exactly; when the profile holds the autocorrelation at longer lags, the second root is fitted to them (ar_roots).
     The body bends smoothly below 62 mg/dL towards 58 and above 240 towards 246 (unless the profile's own 5th / 95th
     percentiles lie beyond 54 / 250 mg/dL): the very low and very high excursions come from step 3;
  3. excursions injected at the profile's rates (lows below 70 and 54 mg/dL, highs above 180 and 250 mg/dL, by day and
     by night), with durations and extremes drawn from the profile's percentiles. The rates the body already produces
     (with the measurement noise, at the series' interval) are measured once per profile on fixed random streams and
     subtracted, so that the total rate follows the profile for every seed. An excursion's time beyond its limit
     includes part of its ramps; the plateau is shortened by that part, and lies at least two noise standard
     deviations beyond the limit. One excursion at a time: the rates are raised by the share of time the excursions
     take. Short dips (4-12 minutes, from a value below 75 mg/dL) make up the profile's time below 54 mg/dL that its
     excursions do not account for;
  4. measurement noise (normal; the part of the profile's noise that the body does not already show);
  5. the reading-interval jitter, single missed readings, data gaps and no-reading states ("unavailable") at the
     profile's rates; LO / HI below 40 / above the upper limit (texts, or the Dexcom codes 39 / 401).
Values are written at the source's precision: mg/dL whole numbers, mmol/L one decimal.

The same profile, seed and options always give the same series. Usage (from the repository root):
  python -m tools.synth.generate --days 30 --interval 5 --unit mmol/L --seed 7 --out series.json
  python -m tools.synth.generate --hours 6 --modifier hypo_prone --profile PROFILE.json --out series.json
"""

from __future__ import annotations

import argparse
import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from .common import (
    DEFAULT_TZ,
    DEXCOM_HI_CODE,
    DEXCOM_LO_CODE,
    HI_TEXT,
    LO_TEXT,
    MGDL_PER_MMOLL,
    UNIT_MGDL,
    Series,
    SourceState,
    mgdl_text,
    unit_name,
)
from .example_profile import example_profile
from .profile import BIN_MINUTES, BINS, NIGHT_END_HOUR, PCTS, load_profile, validate_profile, z_to_value

INTERVALS = (60, 300, 900)  # the supported reading intervals, seconds
LO_LIMIT = 40.0  # a value below this is written as LO
RAMP_MIN = 20.0  # minutes for an excursion to reach its extreme, and to leave it (at most)
RAMP_MIN_SHORT = 8.0  # the shortest ramp, minutes (a short excursion has shorter ramps)
MAX_RAMP_SLOPE = 4.0  # mg/dL per minute: the steepest point of a ramp is never steeper (CGM rates of change)
REFRACTORY_MIN = 30.0  # quiet minutes after an excursion before the next can start
# mg/dL: the body bends below the low knee towards the floor and above the high knee towards the ceiling, so that the
# very low and very high excursions come from the injection, with the profile's rates and durations (the knees move
# out to the profile's own 5th / 95th percentiles where those lie beyond 54 / 250 mg/dL)
BODY_LOW_KNEE, BODY_HIGH_KNEE, BODY_BEND = 62.0, 240.0, (4.0, 6.0)
CALIBRATION_DAYS = 28  # days per calibration stream
CALIBRATION_STREAMS = 4  # independent streams: the body's rates are measured over 112 days, the same for every seed
# The excursion classes: the limit each is measured against, whether it lies below the limit, and the extremes drawn
EXCURSION_CLASSES = {
    "below_54": (54.0, True, (40.0, 53.0)),
    "below_70": (70.0, True, (55.0, 69.0)),
    "above_250": (250.0, False, (255.0, 380.0)),
    "above_180": (180.0, False, (185.0, 250.0)),
}
# Short dips below 54 mg/dL (shorter than an excursion's 15 minutes) make up the profile's time below 54 that its
# excursions do not account for; minutes beyond the limit drawn uniformly from DIP_MINUTES
DIP_KEY = "dip_below_54"
DIP_MINUTES = (4.0, 12.0)
DIP_BASE_MAX = 75.0  # mg/dL: a dip starts only from a value below this (a short dip comes from an existing low)
INJECTED_CLASSES = {
    "below_54": EXCURSION_CLASSES["below_54"],
    DIP_KEY: (54.0, True, (48.0, 53.0)),
    **{k: v for k, v in EXCURSION_CLASSES.items() if k != "below_54"},
}

# Scenario modifiers: multipliers of the profile's rates and scales, and options.
#   body: the AR(1) amplitude; lows / highs: excursion rates; gaps; missed: single missed readings; noise;
#   limits: LO / HI frequency; shift: mg/dL added to the time-of-day baseline; style: "dexcom" writes 39 / 401.
MODIFIERS: dict[str, dict] = {
    "stable": {"body": 0.6, "lows": 0.3, "highs": 0.3},
    "variable": {"body": 1.4, "lows": 2.0, "highs": 2.0},
    "hypo_prone": {"lows": 4.0},
    "hyper_prone": {"highs": 3.0, "shift": 25.0},
    "dropouts": {"gaps": 6.0},
    "no_gaps": {"gaps": 0.0, "missed": 0.0, "no_reading": 0.0},
    "noisy": {"noise": 3.0},
    "limits": {"limits": 20.0},
    "dexcom_codes": {"style": "dexcom"},
}


@dataclass
class _Event:
    """An injected excursion: start (epoch s), plateau minutes, extreme (mg/dL), ramp minutes (each side)."""

    start: float
    minutes: float
    extreme: float
    ramp: float = RAMP_MIN

    def weight(self, t: float) -> float:
        """How far the value is pulled to the extreme at time t: 0 outside, 1 on the plateau (cosine ramps)."""
        m = (t - self.start) / 60.0
        r = self.ramp
        if m < 0 or m > self.minutes + 2 * r:
            return 0.0
        if m < r:
            return 0.5 - 0.5 * math.cos(math.pi * m / r)
        if m <= r + self.minutes:
            return 1.0
        return 0.5 + 0.5 * math.cos(math.pi * (m - r - self.minutes) / r)

    @property
    def end(self) -> float:
        return self.start + (self.minutes + 2 * self.ramp) * 60

    @classmethod
    def shaped(cls, start: float, beyond_min: float, extreme: float, base: float, limit: float) -> _Event:
        """An excursion from `base` towards `extreme` whose time beyond `limit` is `beyond_min` minutes.

        On a cosine ramp the value is beyond the limit while the weight exceeds w = (base - limit) / (base - extreme),
        that is for the share 1 - arccos(1 - 2w) / pi of the ramp. The ramps take up to half of the time beyond the
        limit (never steeper than MAX_RAMP_SLOPE), and the plateau the rest.
        """
        span = abs(base - extreme)
        w = min(1.0, max(0.0, (base - limit) / (base - extreme))) if span > 1e-9 else 0.0
        share = 1.0 - math.acos(1.0 - 2.0 * w) / math.pi  # of each ramp, beyond the limit
        shortest = max(RAMP_MIN_SHORT, math.pi / 2 * span / MAX_RAMP_SLOPE)
        ramp = min(RAMP_MIN, max(shortest, beyond_min / (4 * share) if share > 0 else RAMP_MIN))
        return cls(start, max(0.0, beyond_min - 2 * ramp * share), extreme, ramp)


def _modifiers(names) -> dict:
    if isinstance(names, str):  # one name, not its letters
        names = (names,)
    out = {
        "body": 1.0,
        "lows": 1.0,
        "highs": 1.0,
        "gaps": 1.0,
        "missed": 1.0,
        "noise": 1.0,
        "limits": 1.0,
        "no_reading": 1.0,
        "shift": 0.0,
        "style": "text",
    }
    for name in names or ():
        if name not in MODIFIERS:
            raise ValueError(f"unknown modifier {name!r}; known: {', '.join(sorted(MODIFIERS))}")
        for k, v in MODIFIERS[name].items():
            if k in ("shift", "style"):
                out[k] = v if k == "style" else out[k] + v
            else:
                out[k] *= v
    return out


def _from_pcts(p: dict, u: float, lo: float, hi: float) -> float:
    """The value at the quantile u (0-1) of the distribution through the percentiles p10 / p25 / p50 / p75 / p90 that
    the profile holds (piecewise linear between them; the tails reach lo / hi; kept within [lo, hi])."""
    knots = [(0.0, lo)]
    for q in (10, 25, 50, 75, 90):
        if p.get(f"p{q}") is not None:
            knots.append((q / 100, min(hi, max(knots[-1][1], float(p[f"p{q}"])))))
    if len(knots) == 1:
        knots.append((0.5, (lo + hi) / 2))
    knots.append((1.0, max(hi, knots[-1][1])))
    for (u0, v0), (u1, v1) in pairwise(knots):
        if u <= u1:
            return v0 + (v1 - v0) * (u - u0) / (u1 - u0)
    return knots[-1][1]


def _duration_bounds(p: dict, shortest: float) -> tuple[float, float]:
    """The range an excursion's time beyond its limit is drawn from: from `shortest` to twice the 90th percentile."""
    return shortest, max(shortest + 1.0, 2 * float(p.get("p90") or p.get("p50") or 40))


def _mean_from_pcts(p: dict, lo: float, hi: float) -> float:
    """The mean of the distribution _from_pcts draws from (the midpoint rule over 400 quantiles)."""
    return sum(_from_pcts(p, (i + 0.5) / 400, lo, hi) for i in range(400)) / 400


def _lognormal(rng: random.Random, p: dict, lo: float, hi: float, default: float) -> float:
    """A duration drawn from a lognormal through the median and the 90th percentile, clipped to [lo, hi]."""
    p50 = float(p.get("p50") or default)
    p90 = float(p.get("p90") or 2 * p50)
    sigma = max(0.05, (math.log(max(p90, p50 + 1e-6)) - math.log(p50)) / 1.2816)
    return min(hi, max(lo, rng.lognormvariate(math.log(p50), sigma)))


def ar2_acf(r1: float, r2: float, k: int) -> float:
    """The autocorrelation at lag k of a stationary AR(2) process with the real roots r1 != r2 (|r| < 1)."""
    return (r1 ** (k + 1) * (1 - r2 * r2) - r2 ** (k + 1) * (1 - r1 * r1)) / ((r1 - r2) * (1 + r1 * r2))


def ar_roots(variability: dict) -> tuple[float, float]:
    """The two real roots (r1, r2) of the AR(2) body process z on the 5-minute grid.

    The lag-1 autocorrelation is the profile's 5-minute coefficient phi exactly: (r1 + r2) / (1 + r1 x r2) = phi. With
    r1 = 0 the process is the AR(1) process with coefficient phi. When the profile holds the autocorrelation of z at
    longer lags (profile.fit, "acf_z"), r1 is the value in [0, phi) whose autocorrelation is closest to the profile's
    at those lags (least squares over a grid): a smoother process, whose autocorrelation falls more slowly over
    minutes and faster over hours than an AR(1) process with the same phi. A single AR(1) process remembers too long:
    its lows and highs last too long and come too seldom.
    """
    phi = float(variability.get("ar1_phi", 0.96))
    lags = []
    for key, value in (variability.get("acf_z") or {}).items():
        minutes = key.removesuffix("min")
        if value is not None and minutes.isdigit() and int(minutes) > 5 and int(minutes) % 5 == 0:
            lags.append((int(minutes) // 5, float(value)))
    if not lags or not 0 < phi < 1:
        return 0.0, phi

    def roots(r1: float) -> tuple[float, float]:
        return r1, (phi - r1) / (1 - phi * r1)

    def error(r: tuple[float, float]) -> float:
        acf = (lambda k: r[1] ** k) if r[0] == 0 else (lambda k: ar2_acf(r[0], r[1], k))
        return sum((acf(k) - v) ** 2 for k, v in lags)

    candidates = [r for r in (roots(phi * i / 200) for i in range(200)) if r[0] == 0 or abs(r[0] - r[1]) > 1e-6]
    return min(candidates, key=error)


class _Model:
    """The time-of-day baseline and the z mapping of a profile."""

    def __init__(self, profile: dict, tz: ZoneInfo, mods: dict):
        tod = profile["time_of_day"]
        self.pcts = [tod[f"p{p}"] for p in PCTS]
        self.tz = tz
        self.shift = float(mods["shift"])
        var = profile.get("variability") or {}
        self.roots = ar_roots(var)
        self.amp = float(mods["body"])  # z is unit-variance: the mapping gives the profile's distribution
        self._q: dict[int, tuple[list[float], bool]] = {}
        lowest, highest = min(tod["p5"]) + self.shift, max(tod["p95"]) + self.shift
        self.low_knee = BODY_LOW_KNEE if lowest >= 54 else lowest
        self.high_knee = BODY_HIGH_KNEE if highest <= 250 else highest
        # what the body's excursion rates depend on (the calibration is kept per key)
        self.key = (tuple(map(tuple, self.pcts)), str(tz), self.shift, self.amp, self.roots)

    def _at(self, t: int) -> tuple[list[float], bool]:
        """(the five percentiles, night) at a local time, interpolated between the 15-minute bins' centers."""
        q = t - t % 900
        if q not in self._q:
            d = datetime.fromtimestamp(q, self.tz)
            pos = (d.hour * 60 + d.minute) / BIN_MINUTES - 0.5  # bin centers
            b0 = math.floor(pos) % BINS
            b1 = (b0 + 1) % BINS
            f = pos - math.floor(pos)

            def lerp(arr, b0=b0, b1=b1, f=f):
                """The value between the two neighbouring bins."""
                return arr[b0] + (arr[b1] - arr[b0]) * f

            self._q[q] = ([lerp(arr) for arr in self.pcts], d.hour < NIGHT_END_HOUR)
        return self._q[q]

    def value(self, t: int, z: float) -> tuple[float, bool]:
        """The body value (mg/dL) at time t for the AR state z, and whether t is at night.

        Beyond the knees the value bends smoothly (exponentially, with the slope 1 at the knee) towards a bound
        BODY_BEND mg/dL further out and never reaches it: the slow body process would otherwise make the very low and
        very high excursions too long and too rare (and, being rare, their rate measured by the calibration too
        uncertain), and those are injected with the profile's rates and durations instead.
        """
        q, night = self._at(t)
        v = z_to_value(z * self.amp, q) + self.shift
        low, high = BODY_BEND
        if v < self.low_knee:
            v = self.low_knee - low + low * math.exp((v - self.low_knee) / low)
        elif v > self.high_knee:
            v = self.high_knee + high - high * math.exp((self.high_knee - v) / high)
        return v, night


class _Knots:
    """The unit-variance AR(2) process z on a 5-minute grid from t0, read at any time by Catmull-Rom interpolation.

    z(k) = a1 x z(k-1) + a2 x z(k-2) + e(k) with a1 = r1 + r2, a2 = -r1 x r2 (ar_roots) and the innovation variance
    that keeps the variance at 1; the first two knots are drawn with the process' own lag-1 correlation, so the
    process is stationary from its start. The profile's coefficients are fitted on a 5-minute grid. A process
    evaluated every minute would be rough (a new random step each minute); interpolating the 5-minute knots keeps a
    1-minute series smooth, as CGM data are.
    """

    STEP = 300

    def __init__(self, rng: random.Random, roots: tuple[float, float], t0: int):
        self.rng, self.t0 = rng, t0
        r1, r2 = roots
        self.a1, self.a2 = r1 + r2, -r1 * r2
        rho1 = self.a1 / (1 - self.a2)
        self.innov = math.sqrt(max(0.0, (1 + self.a2) * ((1 - self.a2) ** 2 - self.a1**2) / (1 - self.a2)))
        self.z = [rng.gauss(0, 1)]
        self.z.append(rho1 * self.z[0] + math.sqrt(1 - rho1 * rho1) * rng.gauss(0, 1))

    def _knot(self, k: int) -> float:
        while len(self.z) <= k + 1:
            self.z.append(self.a1 * self.z[-1] + self.a2 * self.z[-2] + self.innov * self.rng.gauss(0, 1))
        return self.z[max(0, k + 1)]  # knot -1 exists (the first random value)

    def at(self, t: float) -> float:
        x = (t - self.t0) / self.STEP
        k = math.floor(x)
        f = x - k
        p0, p1, p2, p3 = (self._knot(k + j) for j in (-1, 0, 1, 2))
        return 0.5 * (
            2 * p1 + (-p0 + p2) * f + (2 * p0 - 5 * p1 + 4 * p2 - p3) * f * f + (-p0 + 3 * p1 - 3 * p2 + p3) * f * f * f
        )


_CALIBRATION: dict[tuple, dict[tuple[str, str], float]] = {}


def _calibrate(model: _Model, t0: int, interval: int, noise_sd: float) -> dict[tuple[str, str], float]:
    """What the body alone produces, by day and by night: the rate per minute of excursions of 15 minutes or more of
    each class (key (class, part)), and the share of time below DIP_BASE_MAX (key (DIP_KEY, part)).

    Measured at the series' reading interval with its measurement noise (noise splits a long run near a limit into
    several), on CALIBRATION_STREAMS fixed random streams: the result depends on the profile, the modifiers, the
    interval and the noise only, never on the series' seed (a short, seed-dependent run made the injected rates, and
    so the time above 180 mg/dL, differ by half between seeds). Kept per model, start day, interval and noise.
    """
    start = t0 - t0 % 86400
    key = (model.key, start, interval, round(noise_sd, 6))
    if key in _CALIBRATION:
        return _CALIBRATION[key]
    counts = {(k, p): 0 for k in EXCURSION_CLASSES for p in ("day", "night")}
    minutes = {"day": 0.0, "night": 0.0}
    low_minutes = {"day": 0.0, "night": 0.0}
    step_min = interval / 60
    for stream in range(CALIBRATION_STREAMS):
        knots = _Knots(random.Random(f"calibration-{stream}"), model.roots, start)
        noise = random.Random(f"calibration-noise-{stream}")
        run: dict[str, tuple[int, bool] | None] = dict.fromkeys(EXCURSION_CLASSES)
        for i in range(CALIBRATION_DAYS * 86400 // interval):
            t = start + i * interval
            v, night = model.value(t, knots.at(t))
            part = "night" if night else "day"
            minutes[part] += step_min
            low_minutes[part] += step_min if v < DIP_BASE_MAX else 0.0
            v += noise.gauss(0, noise_sd) if noise_sd else 0.0
            for k, (limit, below, _) in EXCURSION_CLASSES.items():
                if (v < limit) if below else (v > limit):
                    if run[k] is None:
                        run[k] = (t, night)
                elif run[k] is not None:
                    first, at_night = run[k]
                    if t - first >= 900:
                        counts[(k, "night" if at_night else "day")] += 1
                    run[k] = None
    result = {(k, p): counts[(k, p)] / max(1.0, minutes[p]) for (k, p) in counts}
    result.update({(DIP_KEY, p): low_minutes[p] / max(1.0, minutes[p]) for p in minutes})
    _CALIBRATION[key] = result
    return result


def _body_noise(model: _Model, seed: int, t0: int, interval: int, unit: str, jitter_for: dict, aligned: bool) -> float:
    """The noise the noise-free body shows to the profile's noise estimator (profile.fit, "noise"): the second
    difference of evenly spaced readings at the source precision, var = 1.5 x noise variance; 2 days are enough."""
    rng = random.Random(f"{seed}-body-noise")
    knots = _Knots(random.Random(f"{seed}-body-noise-ar"), model.roots, t0)
    jitter = (
        0.0
        if aligned
        else float(jitter_for.get("jitter_sd_s", 0.0)) * interval / float(jitter_for.get("median_s") or 60)
    )
    vals = []
    for i in range(int(2 * 86400 // interval)):
        t = t0 + i * interval
        t += round(max(-interval / 3, min(interval / 3, rng.gauss(0, jitter)))) if jitter else 0
        v, _ = model.value(t, knots.at(t))
        vals.append(float(mgdl_text(v, unit)) * (MGDL_PER_MMOLL if unit != UNIT_MGDL else 1.0))
    dd = [vals[i] - (vals[i - 1] + vals[i + 1]) / 2 for i in range(1, len(vals) - 1)]
    return math.sqrt(sum(d * d for d in dd) / len(dd) / 1.5) if dd else 0.0


def generate(
    profile: dict | None = None,
    *,
    start: datetime,
    hours: float,
    interval: int = 60,
    unit: str = UNIT_MGDL,
    seed: int = 0,
    tz: str = DEFAULT_TZ,
    modifiers=(),
    aligned: bool = False,
    upper: float = 500.0,
    emit_no_reading: bool = True,
    delay: int = 30,
) -> Series:
    """Return an artificial series of `hours` hours from `start` (an aware datetime).

    interval: 60, 300 or 900 seconds. unit: the source's unit (mg/dL or mmol/L). modifiers: names from MODIFIERS.
    aligned: no interval jitter (readings exactly every `interval` seconds). upper: the sensor's upper limit (a value
    above it is HI; 400 with the dexcom_codes modifier). emit_no_reading: write "unavailable" states at gap starts and
    at the profile's rate. delay: seconds between a measurement and its recording.
    """
    if start.tzinfo is None:
        raise ValueError("start must be time zone aware")
    if interval not in INTERVALS:
        raise ValueError(f"interval must be one of {INTERVALS} seconds")
    if not (isinstance(hours, (int, float)) and math.isfinite(hours) and hours >= 0):
        raise ValueError("hours must be a finite number of 0 or more")
    if not (isinstance(upper, (int, float)) and math.isfinite(upper) and LO_LIMIT < upper <= 1000):
        raise ValueError(f"upper must be a sensor limit in mg/dL above {LO_LIMIT:g} (400, 450 or 500)")
    if not (isinstance(delay, int) and delay >= 0):
        raise ValueError("delay must be whole seconds, 0 or more (a state is never recorded before it is measured)")
    unit = unit_name(unit)
    profile = validate_profile(profile) if profile else example_profile()
    mods = _modifiers(modifiers)
    zone = ZoneInfo(tz)
    model = _Model(profile, zone, mods)
    rng = random.Random(f"{seed}-series")
    t0 = int(start.timestamp())
    n = int(hours * 3600 // interval)
    dexcom = mods["style"] == "dexcom"
    if dexcom:
        upper = min(upper, 400.0)

    ivs = profile.get("intervals") or {}
    # the profile's noise is measured from the second difference of real readings, which also holds the glucose
    # curve's own curvature, the interval jitter and the rounding to the source precision; the body here already
    # has those, so only the remainder is added (Pythagoras: the variances add)
    noise_sd = float((profile.get("noise") or {}).get("sd_mgdl", 0.0)) * mods["noise"]
    if noise_sd:
        # measured at the precision of the history the profile was fitted from (not the output unit), so that the
        # same seed gives the same series in both units
        src_unit = unit_name((profile.get("source") or {}).get("unit") or UNIT_MGDL)
        body_sd = _body_noise(model, seed, t0, interval, src_unit, jitter_for=ivs, aligned=aligned)
        noise_sd = math.sqrt(max(0.0, noise_sd * noise_sd - body_sd * body_sd))

    # rates per minute, by class and day / night: the profile's, less what the body produces
    exc = profile.get("excursions") or {}
    body = _calibrate(model, t0, interval, noise_sd)

    def target(key: str, part: str, factor: float) -> float:
        return float(((exc.get(key) or {}).get(part) or {}).get("per_covered_hour", 0.0)) / 60.0 * factor

    # an excursion's time beyond its limit is drawn from the profile's duration percentiles, at least 15 minutes plus
    # one reading interval (otherwise an excursion of exactly 15 minutes is measured as one reading short of it)
    shortest = 15.0 + interval / 60

    def durations(key: str) -> tuple[dict, float, float]:
        """The duration percentiles of a class (40 / 80 minutes at the 50th / 90th without any) and their range."""
        d = (exc.get(key) or {}).get("duration_min") or {"p50": 40.0, "p90": 80.0}
        return (d, *_duration_bounds(d, shortest))

    def beyond_minutes(key: str) -> float:
        """The mean time beyond the limit of an injected excursion of this class."""
        return sum(DIP_MINUTES) / 2 if key == DIP_KEY else _mean_from_pcts(*durations(key))

    inject: dict[tuple[str, str], float] = {}
    for part in ("day", "night"):
        lo54 = max(0.0, target("below_54", part, mods["lows"]) - body[("below_54", part)])
        # the time below 54 mg/dL that the excursions of 15 minutes or more do not account for: short dips (the body
        # does not reach below BODY_FLOOR), started only while the body is below DIP_BASE_MAX: their rate per such
        # minute is higher by that share of time
        share54 = float(((exc.get("below_54") or {}).get(part) or {}).get("time_pct", 0.0)) / 100 * mods["lows"]
        dips = max(0.0, share54 - lo54 * beyond_minutes("below_54")) / beyond_minutes(DIP_KEY)
        low_share = body[(DIP_KEY, part)]
        # every very low excursion is also below 70 mg/dL for 15 minutes or more on its ramps; a dip lies in a low
        lo70 = max(0.0, target("below_70", part, mods["lows"]) - body[("below_70", part)] - lo54)
        hi250 = max(0.0, target("above_250", part, mods["highs"]) - body[("above_250", part)])
        hi180 = max(0.0, target("above_180", part, mods["highs"]) - body[("above_180", part)] - hi250)
        rates = {"below_54": lo54, DIP_KEY: dips, "below_70": lo70, "above_250": hi250, "above_180": hi180}
        # an excursion can start only while none is running: the rates per free minute are higher by the share of
        # time the excursions take (beyond the limit, the ramps outside it and the quiet time after), so that the rates
        # per minute of the whole series follow the profile
        busy = min(0.8, sum(r * (beyond_minutes(k) + RAMP_MIN + REFRACTORY_MIN) for k, r in rates.items()))
        for k, r in rates.items():
            per_free_minute = r / (1.0 - busy)
            if k == DIP_KEY:
                per_free_minute = per_free_minute / low_share if low_share > 0 else 0.0
            inject[(k, part)] = per_free_minute
    lim = profile.get("limits") or {}

    def limit_share(key: str, per_week_key: str) -> float:
        per_week = sum(float(((exc.get(key) or {}).get(p) or {}).get("per_week", 0.0)) for p in ("day", "night"))
        return min(1.0, float(lim.get(per_week_key, 0.0)) * mods["limits"] / per_week) if per_week else 0.0

    p_lo, p_hi = limit_share("below_54", "lo_per_week"), limit_share("above_250", "hi_per_week")

    gp = profile.get("gaps") or {}
    jitter = 0.0 if aligned else float(ivs.get("jitter_sd_s", 0.0)) * interval / float(ivs.get("median_s") or 60)
    p_missed = float(ivs.get("missed_share", 0.0)) * mods["missed"]
    gap_rate = float(gp.get("per_week", 0.0)) * mods["gaps"] / (7 * 1440)  # per minute
    gap_min_len = max(15 * 60, 3 * interval) + interval  # a real gap by the gap rule
    # the profile counts every no-reading state, also the one at a gap's start, which is written below at each gap:
    # only the rest are written at random (otherwise every gap's state would be counted twice)
    nr_per_week = float((profile.get("no_reading_states") or {}).get("per_week", 0.0)) * mods["no_reading"]
    if emit_no_reading:
        nr_per_week = max(0.0, nr_per_week - float(gp.get("per_week", 0.0)) * mods["gaps"])
    nr_rate = nr_per_week / (7 * 1440)
    knots = _Knots(random.Random(f"{seed}-ar"), model.roots, t0)
    event: _Event | None = None
    quiet_until = 0.0
    gap_until = -1
    states: list[SourceState] = []
    last_recorded = -1
    counts = {"readings": 0, "lo": 0, "hi": 0, "gaps": 0, "no_reading": 0, "missed": 0, "excursions": 0}
    step_min = interval / 60.0

    def record(measured: int | None, text: str, at: int) -> None:
        nonlocal last_recorded
        at = max(at, last_recorded + 1)
        last_recorded = at
        states.append(SourceState(recorded=at, state=text, measured=measured))

    for i in range(n):
        t = t0 + i * interval
        v, night = model.value(t, knots.at(t))
        part = "night" if night else "day"
        # excursions
        if event is not None and t >= event.end:
            event, quiet_until = None, t + REFRACTORY_MIN * 60
        if event is None and t >= quiet_until:
            for key, (limit, below, bounds) in INJECTED_CLASSES.items():
                if key == DIP_KEY and v >= DIP_BASE_MAX:
                    continue
                if rng.random() < inject[(key, part)] * step_min:
                    if key == DIP_KEY:
                        minutes, extreme = rng.uniform(*DIP_MINUTES), rng.uniform(*bounds)
                    else:
                        d, lo, hi = durations(key)
                        minutes = _from_pcts(d, rng.random(), lo, hi)
                        extreme = _from_pcts((exc.get(key) or {}).get("extreme_mgdl") or {}, rng.random(), *bounds)
                    # the plateau lies at least two noise standard deviations beyond the limit: the measurement noise
                    # would otherwise split an excursion near its limit into pieces shorter than 15 minutes
                    margin = 2 * noise_sd + 1
                    extreme = min(extreme, limit - margin) if below else max(extreme, limit + margin)
                    if key == "below_54" and rng.random() < p_lo:
                        extreme = 34.0
                    if key == "above_250" and rng.random() < p_hi:
                        extreme = upper + 25.0
                    event = _Event.shaped(t, minutes, extreme, v, limit)
                    counts["excursions"] += 1
                    break
        if event is not None:
            w = event.weight(t)
            v = v * (1 - w) + event.extreme * w
        v += rng.gauss(0, noise_sd) if noise_sd else 0.0
        # gaps, missed readings, no-reading states
        if t < gap_until:
            continue
        if rng.random() < gap_rate * step_min:
            length = _lognormal(rng, gp.get("length_min") or {}, gap_min_len / 60, 24 * 60, 35) * 60
            gap_until = t + int(length)
            counts["gaps"] += 1
            if emit_no_reading:
                record(None, "unavailable", t + delay)
                counts["no_reading"] += 1
            continue
        if rng.random() < p_missed:
            counts["missed"] += 1
            continue
        measured = t + (round(max(-interval / 3, min(interval / 3, rng.gauss(0, jitter)))) if jitter else 0)
        if emit_no_reading and rng.random() < nr_rate * step_min:
            record(None, "unavailable", measured + delay - 1)
            counts["no_reading"] += 1
        if v < LO_LIMIT:
            text = DEXCOM_LO_CODE if dexcom else LO_TEXT
            counts["lo"] += 1
        elif v > upper:
            text = DEXCOM_HI_CODE if dexcom else HI_TEXT
            counts["hi"] += 1
        else:
            text = mgdl_text(v, unit)
        record(measured, text, measured + delay)
        counts["readings"] += 1

    return Series(
        unit=unit,
        interval=interval,
        tz=tz,
        states=states,
        description="Artificial CGM series (tools/synth/generate.py); not a real person's data.",
        meta={
            "seed": seed,
            "modifiers": list(modifiers or ()),
            "start": start.isoformat(),
            "hours": hours,
            "profile_artificial": bool(profile.get("artificial")),
            "upper": upper,
            "counts": counts,
        },
    )


def main(argv: list[str] | None = None) -> int:
    """Generate a series (command line); return the exit code."""
    ap = argparse.ArgumentParser(prog="python -m tools.synth.generate", description=__doc__.split("\n\n")[0])
    length = ap.add_mutually_exclusive_group(required=True)
    length.add_argument("--hours", type=float, help="length in hours")
    length.add_argument("--days", type=float, help="length in days (more than 200 is fine)")
    ap.add_argument("--start", help="local start, ISO (default: the length before now)")
    ap.add_argument("--tz", default=DEFAULT_TZ, help=f"time zone (default {DEFAULT_TZ})")
    ap.add_argument("--interval", type=int, choices=(1, 5, 15), default=1, help="reading interval in minutes")
    ap.add_argument("--unit", default=UNIT_MGDL, help="mg/dL (default) or mmol/L")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--modifier", action="append", default=[], choices=sorted(MODIFIERS))
    ap.add_argument("--profile", help="a profile JSON (default: the artificial example profile)")
    ap.add_argument("--aligned", action="store_true", help="no interval jitter")
    ap.add_argument("--upper", type=float, default=500.0, help="the sensor's upper limit in mg/dL (default 500)")
    ap.add_argument("--out", required=True, help="the series JSON to write")
    args = ap.parse_args(argv)
    hours = args.hours if args.hours is not None else args.days * 24
    zone = ZoneInfo(args.tz)
    if args.start:
        start = datetime.fromisoformat(args.start)
        start = start if start.tzinfo else start.replace(tzinfo=zone)
    else:
        start = (datetime.now(UTC) - timedelta(hours=hours)).astimezone(zone).replace(second=0, microsecond=0)
    profile = load_profile(args.profile) if args.profile else None
    series = generate(
        profile,
        start=start,
        hours=hours,
        interval=args.interval * 60,
        unit=args.unit,
        seed=args.seed,
        tz=args.tz,
        modifiers=args.modifier,
        aligned=args.aligned,
        upper=args.upper,
    )
    series.save(args.out)
    c = series.meta["counts"]
    print(
        f"{args.out}: {c['readings']} readings, {c['gaps']} gaps, {c['lo']} LO, {c['hi']} HI, "
        f"{c['no_reading']} no-reading states ({series.unit}, every {args.interval} min)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
