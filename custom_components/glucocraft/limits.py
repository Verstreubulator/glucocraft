# License: AGPL-3.0-only (LICENSE).
"""Readings outside the CGM sensor's measurement range (LO / HI) and the source's lower / upper limit.
 A CGM system measures only within a range (for example 40-400 mg/dL for Dexcom G6 / G7 and US / Canadian FreeStyle
Libre, 40-500 mg/dL for FreeStyle Libre outside the US, 50-400 mg/dL for Medtronic Simplera / Guardian 4, 40-450 mg/dL
for Sibionics GS1). Outside it the system shows "LO" / "HI" (or "LOW" / "HIGH", "Below 50 mg/dL" / "Above 400 mg/dL"),
and the integrations that bring the data into Home Assistant pass that on as a text or as a code number (Dexcom Share:
39 = LOW, 401 = HIGH). GlucoCraft stores such a reading at the source's limit, marked as a limit reading ("low" /
"high"). Such a reading tells only that glucose lies beyond what the sensor can measure: it is never used as a number
(left out of the mean, SD, CV, GMI, lowest and highest value, the trend and the graphs) and it still counts as a
category (very low or very high in the time in ranges and the GRI, and in sensor active). The graphs mark it along their
bottom or top edge, and its time is stated with the statistics, in the notifications and in the report (no consensus
document addresses such readings, so this is a GlucoCraft rule). It is shown as "LO" / "HI", never as a number.

classify() decides, for one source state: no reading (a gap), a limit reading (low / high, stored at the limit), a
measured reading, or the FreeStyle Libre warm-up marker. Everything is decided in mg/dL: a state in mmol/L is first
converted with 18.016 to mg/dL with one decimal (units.to_mgdl), as it is stored, and then compared with the limits in
mg/dL. So a source that shows its upper limit in mmol/L is read as HI (27.8 mmol/L = 500.8 mg/dL, above 500; 25.0
mmol/L = 450.4 mg/dL, above 450), and 2.2 mmol/L (39.6 mg/dL) as LO below a lower limit of 40 mg/dL.

The limits per source are a setting ("auto" or a value). Automatic: what the source itself shows ("Below 50" / "Above
400" texts, the Dexcom code 401), the delivering integration (Dexcom 400, CareLink 400 and lower limit 50), the
Nightscout device (a Dexcom uploader: 400); otherwise 40 / 500, so that a real reading is never turned into "HI".

LO and HI depend on how the limit was decided (and 39 mg/dL or 2.2 mmol/L and less is LO for every source, except the
no-reading codes: 12.99 mg/dL and less, xDrip+'s 38, and anything below 39 from Nightscout; classify). With an
Automatic limit, only a reading below the lower limit is LO and only a reading above the upper limit is HI: a sensor may
measure the limit value itself. With a limit set by hand, a reading at the limit is LO or HI as well: it is set for a
source that never reports a value beyond a fixed one and sends that value instead of LO or HI (the Repairs notices
sensor_floor and sensor_cap name it), so a reading at that value means "at or below the lower limit" or "at or above
the upper limit". "At the limit" is compared at the precision of the limits: whole mg/dL for a source in mg/dL, one
decimal for a source in mmol/L (mmol_limit: 50 mg/dL = 2.8 mmol/L, 400 mg/dL = 22.2 mmol/L).

The very-low threshold of the ranges stays at or above the sensor's lower limit and the very-high threshold at or below
its upper limit (threshold_span; the stricter of two sources), so that a LO reading is always very low and a HI reading
always very high; scale_position places a LO / HI reading beyond every threshold.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from .i18n import t
from .units import MGDL_PER_MMOLL, UNIT_MMOLL, round_half_up, to_mgdl

LIMIT_LOW = "low"
LIMIT_HIGH = "high"
LIMIT_FLAGS = (LIMIT_LOW, LIMIT_HIGH)  # the limit flags a stored reading can carry (None: a measured value)
LIMIT_CODES = {None: 0, LIMIT_LOW: 1, LIMIT_HIGH: 2}  # the storage byte
LIMIT_FROM_CODE = {v: k for k, v in LIMIT_CODES.items()}

LOWER_CHOICES = (40, 50)  # mg/dL (2.2 / 2.8 mmol/L)
UPPER_CHOICES = (400, 450, 500)  # mg/dL (22.2 / 25.0 / 27.8 mmol/L)
DEFAULT_LOWER = 40
DEFAULT_UPPER = 500  # the widest range found: a real reading is never turned into "HI"
LIMIT_AUTO = "auto"

# Codes that are no reading (Nightscout error codes 1-12 stored in the mg/dL field, xDrip+'s error marker 38) and the
# FreeStyle Libre warm-up marker (0xFFFF, read as 6553.5 or 65535). Sources: the published source code of Nightscout
# (cgm-remote-monitor, lib/plugins/errorcodes.js) and of xDrip+ (BgReading.java, BG_READING_ERROR_VALUE); the
# Documentation lists them in its section Readings Outside the Sensor's Range (LO / HI).
ERROR_CODE_MAX = 12
XDRIP_ERROR = 38
DEXCOM_LOW_CODE = 39  # Dexcom Share / Nightscout: LOW (<= 39 is LO for every source, after the no-reading codes above)
DEXCOM_HIGH_CODE = 401  # Dexcom Share / Nightscout: HIGH
WARMUP_MARKER = 6553.4  # 6553.5 / 65535 and above
LO_MMOL = 2.2  # 39 and 40 mg/dL both read 2.2 mmol/L: LO for every lower limit, as the Dexcom code 39 in mg/dL

# Out-of-range words. English (LO / LOW, HI / HIGH: FreeStyle Libre, Dexcom, Eversense, Accu-Chek SmartGuide) and the
# translations an integration may pass on (the localized displays of Dexcom CGM systems, e.g. BAS / ELEVE; GlucoCraft
# list, not verified for every system). Compared in lower case without accents.
LOW_WORDS = frozenset(
    {"lo", "low", "bas", "niedrig", "bajo", "basso", "laag", "baixo", "lag", "lav", "matala", "niski", "nizka", "nizky"}
)
HIGH_WORDS = frozenset(
    {"hi", "high", "eleve", "haut", "hoch", "alto", "hoog", "hog", "hoj", "hoy", "korkea", "wysoki", "vysoka", "vysoky"}
)
NO_READING_TEXTS = frozenset({"", "unavailable", "unknown", "none", "null", "nan", "---", "--", "-"})
_NUM = r"([0-9]+(?:[.,][0-9]+)?)"
_UNIT = r"\s*(mg\s*/\s*dl|mmol\s*/\s*l)?"
_BELOW = re.compile(r"^(?:below|under|less than|lower than|<)\s*" + _NUM + _UNIT)
_ABOVE = re.compile(r"^(?:above|over|more than|greater than|higher than|>)\s*" + _NUM + _UNIT)
_ACCENTS = str.maketrans("éèêëáàâäíìîïóòôöúùûüåøçñ", "eeeeaaaaiiiioooouuuuaocn")


@dataclass
class Classified:
    """What one source state is: kind = "none" (no reading: a gap), "warmup", "low" / "high" (a limit reading, stored at
    `value`, the limit) or "reading" (measured, `value` mg/dL). learned_lower / learned_upper: a limit the source itself
    showed ("Below 50 mg/dL", "Above 400 mg/dL", the Dexcom code 401)."""

    kind: str
    value: float | None = None
    learned_lower: int | None = None
    learned_upper: int | None = None

    @property
    def limit(self) -> str | None:
        """Return "low" / "high" for a limit reading, else None."""
        return self.kind if self.kind in (LIMIT_LOW, LIMIT_HIGH) else None


def scale_position(value: float, limit: str | None) -> float:
    """Return a reading's position on the glucose scale, for comparisons with a threshold (mg/dL).

    A LO reading is stored at the sensor's lower limit, and its true value lies below that limit; every threshold lies
    at or above it, because the very-low threshold is kept at or above the sensor's lower limit (threshold_span). So a
    LO reading lies below every threshold (minus infinity) and a HI reading above every threshold (plus infinity): LO is
    always very low and HI always very high, also at a threshold equal to the stored value and for a reading stored
    under an earlier limit. Every other reading lies at its value. The one rule for the zone, the binary sensors, the
    statistics and the report; the stored limit itself is never used as a value.
    """
    return -math.inf if limit == LIMIT_LOW else math.inf if limit == LIMIT_HIGH else value


def limit_minutes(seconds: float) -> int:
    """Return the whole minutes of limit readings' time, up: any time above zero is at least 1 minute."""
    return math.ceil(seconds / 60) if seconds > 0 else 0


def below(value: float, limit: str | None, threshold: float) -> bool:
    """Return True when a reading lies below `threshold` (mg/dL); a LO reading always does (scale_position)."""
    return scale_position(value, limit) < threshold


def above(value: float, limit: str | None, threshold: float) -> bool:
    """Return True when a reading lies above `threshold` (mg/dL); a HI reading always does (scale_position)."""
    return scale_position(value, limit) > threshold


def _nearest(mg: float, choices: tuple[int, ...], tolerance: float = 2.0) -> int | None:
    """Return the choice nearest to `mg` when it is within `tolerance` mg/dL (a limit a source shows as 22.2 mmol/L
    is 400 mg/dL, not 399.96), else None."""
    best = min(choices, key=lambda c: abs(c - mg))
    return best if abs(best - mg) <= tolerance else None


def _text_number(num: str, unit_text: str | None, source_unit: str | None) -> float:
    """A number in an out-of-range text -> mg/dL (unit from the text, else the source's, else by size)."""
    v = float(num.replace(",", "."))
    mmol = ("mmol" in unit_text) if unit_text else (source_unit == UNIT_MMOLL or v < 35)
    return v * MGDL_PER_MMOLL if mmol else v


def mmol_limit(mg: float) -> float:
    """A limit in mmol/L as the sources show it: one decimal (40 -> 2.2, 400 -> 22.2, 450 -> 25.0, 500 -> 27.8)."""
    return round_half_up(mg / MGDL_PER_MMOLL, 1)


def at_lower_limit(value: float, source_unit: str | None, lower: float) -> bool:
    """Return True when a measured value (in the source's unit) lies at or below the lower limit `lower` (mg/dL),
    compared at the precision of the limits: whole mg/dL, or mmol/L with one decimal as the sources show the limit
    (mmol_limit). Used only for a lower limit set by hand (classify)."""
    if source_unit == UNIT_MMOLL:
        return round_half_up(value, 1) <= mmol_limit(lower)
    return round_half_up(value) <= lower


def at_upper_limit(value: float, source_unit: str | None, upper: float) -> bool:
    """Return True when a measured value (in the source's unit) lies at or above the upper limit `upper` (mg/dL),
    compared at the precision of the limits: whole mg/dL, or mmol/L with one decimal as the sources show the limit
    (mmol_limit). Used only for an upper limit set by hand (classify)."""
    if source_unit == UNIT_MMOLL:
        return round_half_up(value, 1) >= mmol_limit(upper)
    return round_half_up(value) >= upper


def stored_limit_values(limit: float) -> tuple[float, ...]:
    """The stored values (mg/dL, one decimal) of a reading that lies exactly at a limit given in mg/dL, compared at the
    precision of the limits: the limit itself from a source in mg/dL, and the limit's mmol/L value (mmol_limit)
    converted as a reading in mmol/L is stored (400 -> 22.2 mmol/L -> 400.0; 450 -> 25.0 mmol/L -> 450.4; 500 -> 27.8
    mmol/L -> 500.8)."""
    return tuple(sorted({float(limit), to_mgdl(mmol_limit(limit), UNIT_MMOLL)}))


def classify(
    state: object,
    source_unit: str | None,
    lower: float = DEFAULT_LOWER,
    upper: float = DEFAULT_UPPER,
    dexcom_type: bool = False,
    nightscout: bool = False,
    lower_set: bool = False,
    upper_set: bool = False,
) -> Classified:
    """One source state (text or number, in the source's unit) -> Classified. lower / upper: the source's resolved
    limits in mg/dL. dexcom_type: a Dexcom source (401 = HIGH); nightscout: a Nightscout source (anything below 39 is an
    error code); lower_set: the lower limit was set by hand (not Automatic), so a reading at the limit is LO as well;
    upper_set: the upper limit was set by hand (not Automatic), so a reading at the limit is HI as well (see the module
    docstring)."""
    if state is None or isinstance(state, bool):
        return Classified("none")
    if isinstance(state, (int, float)):
        text, v = None, float(state)
    else:
        text = str(state).strip().lower().translate(_ACCENTS)
        if text in NO_READING_TEXTS or text.startswith("?"):
            return Classified("none")
        try:
            v = float(text.replace(",", ".")) if text.count(",") <= 1 and "." not in text else float(text)
            text = None
        except ValueError:
            v = None
    if text is not None:
        word = re.sub(r"[^a-z]", "", text)
        if word in LOW_WORDS:
            return Classified(LIMIT_LOW, float(lower))
        if word in HIGH_WORDS:
            return Classified(LIMIT_HIGH, float(upper))
        m = _BELOW.match(text)
        if m:
            learned = _nearest(_text_number(m.group(1), m.group(2), source_unit), LOWER_CHOICES)
            return Classified(LIMIT_LOW, float(learned or lower), learned_lower=learned)
        m = _ABOVE.match(text)
        if m:
            learned = _nearest(_text_number(m.group(1), m.group(2), source_unit), UPPER_CHOICES)
            return Classified(LIMIT_HIGH, float(learned or upper), learned_upper=learned)
        return Classified("none")
    if v is None or math.isnan(v):
        return Classified("none")
    if v >= WARMUP_MARKER:
        return Classified("warmup")
    mmol = source_unit == UNIT_MMOLL
    mg = to_mgdl(v, UNIT_MMOLL if mmol else None)
    # 12.99 mg/dL and less is no reading: the error codes 1-12, also with a fraction (a value that is not a whole
    # number, from a source in mmol/L for example), and zero or less. No CGM measures that low, so no real reading is
    # lost. The bound is not ERROR_CODE_MAX itself: then 12.5 would pass on and be read as LO, a reading.
    if mg <= ERROR_CODE_MAX + 0.99:
        return Classified("none")
    if not mmol and v == XDRIP_ERROR:
        return Classified("none")
    if nightscout and mg < DEXCOM_LOW_CODE:
        return Classified("none")
    # the limits are compared with the value in mg/dL as stored (mg), also for a source in mmol/L
    if (
        (v <= LO_MMOL if mmol else v <= DEXCOM_LOW_CODE)
        or mg < lower
        or (lower_set and at_lower_limit(v, source_unit, lower))
    ):
        return Classified(LIMIT_LOW, float(lower))
    if dexcom_type and not mmol and v == DEXCOM_HIGH_CODE:
        return Classified(LIMIT_HIGH, float(upper), learned_upper=400)
    if mg > upper or (upper_set and at_upper_limit(v, source_unit, upper)):
        return Classified(LIMIT_HIGH, float(upper))
    return Classified("reading", min(float(upper), max(float(lower), mg)))


# ---------------------------------------------------------------------------------------------- the source's limits
def is_dexcom_type(domain: str | None, device: str | None) -> bool:
    """A Dexcom source: the Dexcom integration, or Nightscout fed by a Dexcom uploader (share2, xDrip+ with Dexcom)."""
    d = (device or "").lower()
    return domain == "dexcom" or (domain == "nightscout" and ("share" in d or "dexcom" in d))


def auto_upper(domain: str | None, device: str | None = None, learned: int | None = None) -> tuple[int, str]:
    """Return the automatic upper limit and what decided it.

    The reason is a generic text (limits.reason.*), because it is shown on the dashboard; the systems are named only
    in the Documentation section Readings Outside the Sensor's Range.
    """
    if learned in UPPER_CHOICES:
        return int(learned), t("limits.reason.source")
    if domain in ("dexcom", "carelink"):
        return 400, t("limits.reason.integration")
    if domain == "nightscout" and is_dexcom_type(domain, device):
        return 400, t("limits.reason.uploader")
    return DEFAULT_UPPER, t("limits.reason.default_upper")


def auto_lower(domain: str | None, learned: int | None = None) -> tuple[int, str]:
    """The automatic lower limit and what decided it: 40, or 50 for Medtronic ("Below 50 mg/dL", CareLink)."""
    if learned in LOWER_CHOICES:
        return int(learned), t("limits.reason.source")
    if domain == "carelink":
        return 50, t("limits.reason.integration")
    return DEFAULT_LOWER, t("limits.reason.default")


def lower_set_by_hand(resolved: Mapping[str, Any]) -> bool:
    """Return True when the resolved limits of a source (resolve) have a lower limit set by hand, not Automatic."""
    return resolved.get("lower_setting", LIMIT_AUTO) != LIMIT_AUTO


def upper_set_by_hand(resolved: Mapping[str, Any]) -> bool:
    """Return True when the resolved limits of a source (resolve) have an upper limit set by hand, not Automatic."""
    return resolved.get("upper_setting", LIMIT_AUTO) != LIMIT_AUTO


def setting_value(setting: object, choices: tuple[int, ...]) -> int | None:
    """A stored limit setting ("auto", "400", 400) -> the value in mg/dL, None for Automatic (or anything unknown)."""
    try:
        v = int(float(str(setting)))
    except (TypeError, ValueError):
        return None
    return v if v in choices else None


class ResolvedLimits(TypedDict):
    """The resolved limits of one source (resolve)."""

    lower: int  # mg/dL
    upper: int  # mg/dL
    lower_setting: int | str  # the setting: "auto" or the value in mg/dL
    upper_setting: int | str
    lower_reason: str | None  # what Automatic used; None when the limit is set by hand
    upper_reason: str | None


def resolve(
    setting_lower: object,
    setting_upper: object,
    domain: str | None,
    device: str | None = None,
    learned_lower: int | None = None,
    learned_upper: int | None = None,
) -> ResolvedLimits:
    """The resolved limits of one source (settings: "auto" or the value; reasons: what Automatic used, None when
    set)."""
    lo, hi = setting_value(setting_lower, LOWER_CHOICES), setting_value(setting_upper, UPPER_CHOICES)
    alo, alo_r = auto_lower(domain, learned_lower)
    ahi, ahi_r = auto_upper(domain, device, learned_upper)
    return ResolvedLimits(
        lower=lo if lo is not None else alo,
        upper=hi if hi is not None else ahi,
        lower_setting=lo if lo is not None else LIMIT_AUTO,
        upper_setting=hi if hi is not None else LIMIT_AUTO,
        lower_reason=None if lo is not None else alo_r,
        upper_reason=None if hi is not None else ahi_r,
    )


def threshold_span(*sources: Mapping[str, Any] | None) -> tuple[int, int]:
    """Return the span of the very-low and the very-high threshold, from the resolved limits of the sources (resolve):
    (the lowest very-low threshold, the highest very-high threshold), in mg/dL.

    The very-low threshold lies at or above the sensor's lower limit and the very-high threshold at or below its upper
    limit, so that a LO reading is always very low and a HI reading always very high, on every surface. With a second
    source the stricter limits of the two apply: the higher lower limit and the lower upper limit. Without a source
    the widest limits (DEFAULT_LOWER, DEFAULT_UPPER). GlucoCraft rule.
    """
    found = [s for s in sources if s]
    return (
        max((int(s["lower"]) for s in found), default=DEFAULT_LOWER),
        min((int(s["upper"]) for s in found), default=DEFAULT_UPPER),
    )
