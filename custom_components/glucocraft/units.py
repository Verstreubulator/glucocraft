# License: AGPL-3.0-only (LICENSE).
"""Glucose units: mg/dL and mmol/L, the conversion factor and the one rounding rule.

GlucoCraft stores and calculates in mg/dL. Readings from a source in mmol/L are converted when they arrive; every value
shown passes through from_mgdl / fmt: mg/dL as whole numbers, mmol/L with one decimal (rates of change with two).
Rounding: halves away from zero (round_half_up), once from the stored value. A rounded zero is always 0 (never "-0"
or "-0.0": round_half_up never returns a negative zero). A percentage shown next to a rule it is compared with keeps
the side of the rule it is on (shown_against: 69.96% next to "at least 70%" is shown as 69.96%, not 70.0%).

Factor: 1 mmol/L = 18.016 mg/dL (glucose, molar mass 180.16 g/mol). The consensus thresholds are used as published in
both units (Battelino et al., 2019; tight range Battelino et al., 2023): 54 / 70 / 140 / 180 / 250 mg/dL = 3.0 / 3.9 /
7.8 / 10.0 / 13.9 mmol/L; the calculations use the mg/dL values, so the consensus statistics are the same in both
units.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

UNIT_MGDL = "mg/dL"
UNIT_MMOLL = "mmol/L"
MGDL_PER_MMOLL = 18.016

# The published consensus thresholds in both units (Battelino et al., 2019; 140 mg/dL: Battelino et al., 2023)
CONSENSUS_MMOLL = {54: 3.0, 70: 3.9, 140: 7.8, 180: 10.0, 250: 13.9}
MGDL_FROM_CONSENSUS = {v: k for k, v in CONSENSUS_MMOLL.items()}

# The consensus thresholds and ranges that the CGM Report's fixed texts name, by their placeholders: {t54} ... {t250}
# and the ranges {t54_69}, {t70_140}, {t70_180}, {t181_250}, as published in both units (mg/dL, mmol/L; Battelino et
# al., 2019; the tight range, Battelino et al., 2023). The one table of the report page and the PDF
# (report_threshold_texts); the decimal separator is applied where they are shown.
REPORT_THRESHOLDS: dict[str, tuple[str, str]] = {
    **{f"t{mg}": (str(mg), f"{mm:.1f}") for mg, mm in CONSENSUS_MMOLL.items()},
    "t54_69": ("54–69", "3.0–3.8"),
    "t70_140": ("70–140", "3.9–7.8"),
    "t70_180": ("70–180", "3.9–10.0"),
    "t181_250": ("181–250", "10.1–13.9"),
}


def report_threshold_texts(unit: str | None) -> dict[str, str]:
    """Return the values of the report texts' threshold placeholders (REPORT_THRESHOLDS) and of {unit} in `unit`."""
    k = 1 if is_mmol(unit) else 0
    return {**{name: pair[k] for name, pair in REPORT_THRESHOLDS.items()}, "unit": UNIT_MMOLL if k else UNIT_MGDL}


def round_half_up(x: float, decimals: int = 0) -> float:
    """Round halves away from zero, as the cards and panels round (round() rounds halves to even: 30.5 -> 30).

    The 1e-9 absorbs binary float error at an exact half (337.8 mg/dL = 18.75 mmol/L). This is the one rounding rule
    of GlucoCraft. A value that rounds to zero is +0.0 (adding 0.0 turns -0.0 into 0.0), so that no text shows "-0"
    or "-0.0" (-0.04 rounds to 0.0, not to -0.0).
    """
    m = 10**decimals
    return math.copysign(math.floor(abs(x) * m + 0.5 + 1e-9) / m, x) + 0.0


# The decimals of a percentage whose rounded value would equal a rule it does not meet or that it passes
EDGE_DECIMALS = 2


def shown_against(value: float, thresholds: float | Iterable[float], decimals: int = 1) -> tuple[float, int]:
    """Return a value as it is shown next to a rule it is compared with, and its number of decimals: (shown, decimals).

    The value is rounded once to `decimals` (halves away from zero). When that equals a threshold although the value
    itself does not (a sensor active of 69.96% is 70.0% with one decimal, beside "less than 70%"), it is shown with two
    decimals (69.96%), so that the number shown is on the same side of the threshold as the value. When two decimals
    still give the threshold (69.996%), the last decimal is moved to the value's side (69.99%; 70.004% is 70.01%).
    GlucoCraft rule. thresholds: one threshold or several (each rule the value is shown with, for example 70% and 50%).
    """
    rules = [float(thresholds)] if isinstance(thresholds, (int, float)) else [float(x) for x in thresholds]
    shown = round_half_up(value, decimals)
    if not any(shown == rule != value for rule in rules):
        return shown, decimals
    places = max(EDGE_DECIMALS, decimals + 1)
    shown = round_half_up(value, places)
    step = 10.0**-places
    for rule in rules:
        if shown == rule != value:
            shown = round_half_up(rule - step if value < rule else rule + step, places)
    return shown, places


def epoch_seconds(number: float) -> int | None:
    """Return a number as epoch seconds: a number above 1e12 is milliseconds; None for a number that is not finite or
    not above 1e9 s (September 2001), which is no measurement time. The one rule of the live readings and the history
    import."""
    if not math.isfinite(number):
        return None
    if number > 1e12:
        number /= 1000.0
    return int(number) if number > 1e9 else None


def is_mmol(unit: str | None) -> bool:
    """Return True for mmol/L."""
    return unit == UNIT_MMOLL


# The glucose unit texts a source may state, compared as a whole in lower case without spaces: mg/dL and mmol/L as the
# CGM integrations write them ("mg/dL", "mg/dl", "mmol/L", "mmol/l") and the spelled-out forms. A unit that only
# contains "mg" or "mmol" is another unit ("mg/m³" of a formaldehyde sensor, "mg/L" of a water sensor).
GLUCOSE_UNIT_TEXTS = {
    "mg/dl": UNIT_MGDL,
    "mg/decilitre": UNIT_MGDL,
    "mg/deciliter": UNIT_MGDL,
    "mmol/l": UNIT_MMOLL,
    "mmol/litre": UNIT_MMOLL,
    "mmol/liter": UNIT_MMOLL,
}


def stated_unit(unit_of_measurement: str | None) -> str | None:
    """Return the unit text an entity states (stripped), or None when it states none."""
    text = str(unit_of_measurement or "").strip()
    return text or None


def unit_from_attribute(unit_of_measurement: str | None) -> str | None:
    """Return the glucose unit of a source entity from its unit_of_measurement (GLUCOSE_UNIT_TEXTS); None when it
    states none or a unit that is not a glucose unit (non_glucose_unit tells the two apart)."""
    text = stated_unit(unit_of_measurement)
    return GLUCOSE_UNIT_TEXTS.get(text.lower().replace(" ", "")) if text else None


def non_glucose_unit(unit_of_measurement: str | None) -> str | None:
    """Return the unit text of an entity that states a unit other than mg/dL or mmol/L ("°F", "%", "mg/m³"), else
    None (a glucose unit, or no unit at all: a source without a unit counts as mg/dL)."""
    text = stated_unit(unit_of_measurement)
    return text if text and unit_from_attribute(text) is None else None


# A source without a unit counts as mg/dL, but a value below UNITLESS_MMOL_BELOW from it looks like mmol/L: such a value
# is not used (live readings and the history import alike), and UNITLESS_MMOL_RUN of them in a row open the Repairs
# notice about the missing unit (coordinator._source_reading). GlucoCraft rule.
UNITLESS_MMOL_BELOW = 30
UNITLESS_MMOL_RUN = 10
# A source that states mmol/L with values above MMOL_TOO_HIGH (33.3 mmol/L = 600 mg/dL, above the highest sensor limit
# GlucoCraft knows, 500 mg/dL) probably states the wrong unit: MMOL_TOO_HIGH_RUN of them in a row open the Repairs
# notice source_mmol_too_high. Such a value is used all the same (coordinator._check_mmol_too_high). GlucoCraft rule.
MMOL_TOO_HIGH = 33.3
MMOL_TOO_HIGH_RUN = 10
# The opposite mistake: a source that states mg/dL with values below MGDL_TOO_LOW (no sensor GlucoCraft knows measures
# that low: the lowest lower limit is 40 mg/dL) probably sends mmol/L: MGDL_TOO_LOW_RUN of them in a row open the
# Repairs notice source_mgdl_too_low. They are treated as mg/dL all the same (coordinator._check_mgdl_too_low).
# GlucoCraft rule.
MGDL_TOO_LOW = 30
MGDL_TOO_LOW_RUN = 10


def looks_like_mmol(number: float) -> bool:
    """True for a value of a source without a unit that looks like mmol/L (above 0, below UNITLESS_MMOL_BELOW)."""
    return 0 < number < UNITLESS_MMOL_BELOW


def to_mgdl(value: float, unit: str | None) -> float:
    """Convert a source value in its unit to mg/dL with one decimal (the storage precision).

    A reading equal to a published consensus threshold becomes its published mg/dL pair, so it falls on the same side
    of the threshold as in mmol/L (10.0 mmol/L is in range; 10.0 x 18.016 = 180.2 would count as high).
    """
    if not is_mmol(unit):
        return round_half_up(float(value), 1)  # one decimal, as stored, so every consumer sees the same value
    v1 = round_half_up(float(value), 1)  # halves away from zero
    snapped = MGDL_FROM_CONSENSUS.get(v1) if abs(v1 - float(value)) < 1e-9 else None
    return float(snapped) if snapped is not None else round_half_up(value * MGDL_PER_MMOLL, 1)


def from_mgdl(value: float | None, unit: str | None, decimals: int | None = None) -> float | None:
    """Convert mg/dL to the display unit; consensus thresholds map to their published mmol/L values (70 -> 3.9).

    decimals: mg/dL default 1 (values stay as calculated), mmol/L default 1.
    """
    if value is None:
        return None
    if not is_mmol(unit):
        return round_half_up(value, 1 if decimals is None else decimals)
    if decimals is None and float(value).is_integer() and int(value) in CONSENSUS_MMOLL:
        return CONSENSUS_MMOLL[int(value)]
    return round_half_up(value / MGDL_PER_MMOLL, 1 if decimals is None else decimals)


def fmt(value: float | None, unit: str | None, with_unit: bool = False) -> str:
    """Return a glucose value as text: mg/dL whole number, mmol/L one decimal; '---' for none."""
    if value is None:
        return "---"
    # mg/dL rounded once from the stored value (70.45 -> 70; rounding to one decimal first would give 70.5 -> 71)
    text = f"{from_mgdl(value, unit):.1f}" if is_mmol(unit) else f"{round_half_up(value):.0f}"
    return f"{text} {UNIT_MMOLL if is_mmol(unit) else UNIT_MGDL}" if with_unit else text


def to_mgdl_setting(value: float, unit: str | None, lo: float | None = None, hi: float | None = None) -> float:
    """Convert a range limit entered in the display unit to mg/dL for storage.

    Whole mg/dL when entered in mg/dL, one decimal when entered in mmol/L (8.9 mmol/L = 160.3 mg/dL, the same value a
    reading of 8.9 is stored as, so it classifies the same everywhere). The published consensus values snap to their
    pairs (7.8 mmol/L -> 140 mg/dL, although 7.8 x 18.016 = 140.5). lo / hi: the allowed mg/dL span (16.7 mmol/L must
    not store 300.9 above a 300 mg/dL maximum).
    """
    if is_mmol(unit):
        v = round_half_up(float(value), 1)
        mg = float(MGDL_FROM_CONSENSUS.get(v, round_half_up(v * MGDL_PER_MMOLL, 1)))
    else:
        mg = round_half_up(float(value))
    if lo is not None:
        mg = max(float(lo), mg)
    if hi is not None:
        mg = min(float(hi), mg)
    return int(mg) if float(mg).is_integer() else mg


# The glucose management indicator, as published (Bergenstal et al., 2018, Figure 1 and Table 1): two equations, each
# for the mean glucose in its own unit,
#   GMI (%)        = 3.31 + 0.02392 x [mean glucose in mg/dL]
#   GMI (mmol/mol) = 12.71 + 4.70587 x [mean glucose in mmol/L]
# The paper states no factor between the two units; its Table 1 gives the mmol/mol values for means stated in mmol/L
# (5 mmol/L -> 36.2 mmol/mol). The mmol/mol equation therefore takes the mean glucose in mmol/L as GlucoCraft shows it
# (the mean in mg/dL / 18.016, the one factor of GlucoCraft), so that a source in mmol/L gets the equation applied to
# its own mean. Both are unrounded; every display rounds once (GMI % one decimal, mmol/mol a whole number).
# The coefficients of the two equations above, as published (Bergenstal et al., 2018):
GMI_PERCENT_BASE = 3.31  # the GMI (%) equation: the constant
GMI_PERCENT_PER_MGDL = 0.02392  # the GMI (%) equation: the factor of the mean glucose in mg/dL
GMI_MMOL_MOL_BASE = 12.71  # the GMI (mmol/mol) equation: the constant
GMI_MMOL_MOL_PER_MMOLL = 4.70587  # the GMI (mmol/mol) equation: the factor of the mean glucose in mmol/L


def gmi_percent(mean_mgdl: float | None) -> float | None:
    """GMI in percent from the mean glucose in mg/dL, unrounded (Bergenstal et al., 2018)."""
    return None if mean_mgdl is None else GMI_PERCENT_BASE + GMI_PERCENT_PER_MGDL * mean_mgdl


def gmi_mmol_mol_exact(mean_mgdl: float | None) -> float | None:
    """GMI in mmol/mol from the mean glucose in mmol/L (the mean in mg/dL / 18.016), unrounded (Bergenstal et al.,
    2018)."""
    return None if mean_mgdl is None else GMI_MMOL_MOL_BASE + GMI_MMOL_MOL_PER_MMOLL * (mean_mgdl / MGDL_PER_MMOLL)


def gmi_mmol_mol(mean_mgdl: float | None) -> float | None:
    """GMI in mmol/mol as shown: a whole number, rounded once from gmi_mmol_mol_exact (halves away from zero)."""
    exact = gmi_mmol_mol_exact(mean_mgdl)
    return None if exact is None else round_half_up(exact, 0)


def gmi_goal_mmol_mol(gmi_pct: float | None) -> float | None:
    """A GMI given in percent (the custom GMI goal) in mmol/mol by the GMI rule, as the GMI values are shown: the GMI in
    mmol/mol of the mean glucose whose GMI in percent it is (the inverse of gmi_percent, then gmi_mmol_mol; Bergenstal
    et al., 2018), a whole number. Not the IFCC-NGSP master equation of laboratory A1C results: the two can differ by
    one (9.7%: 82, not 83 mmol/mol)."""
    return None if gmi_pct is None else gmi_mmol_mol((float(gmi_pct) - GMI_PERCENT_BASE) / GMI_PERCENT_PER_MGDL)
