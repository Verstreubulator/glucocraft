# License: AGPL-3.0-only (LICENSE).
"""Spoken text: glucose values, units, trends and limit readings written out for screen readers and voice assistants.

Screen readers and text-to-speech mispronounce unit symbols ("mg/dL", "mmol/L") and arrows, so every spoken text
writes them out (docs/ACCESSIBILITY.md rule 4). This module is the Python form of the cards' shared spoken-text
functions (gcSpeak, gcSpeakTrend, gcSpeakLimit, gcSpeakText in www/glucocraft-shared.js), with the same words (the
catalogue keys spoken.* of backend.json and frontend.json), and it is used by every Python surface that speaks: the
Spoken summary sensor, the notifications and the CGM Report PDF (speak_comparisons, the words of a plus, minus or
comparison sign).

spoken_summary() builds the state of sensor.glucocraft_spoken_summary, one ready-to-speak sentence of at most 255
characters (the length of a Home Assistant state), in the order of the cards' screen-reader text: the data status when
there is a problem, then the value with its unit written out (a LO / HI reading in words) and the zone word, the time
of the reading and the trend words, for example
"Glucose 112 milligrams per deciliter, tight range, measured at 7:46 AM, changing slowly."
The sentence names the time of the reading, not its age: the state changes only with a reading or a status, so an age
in it would become wrong while the state stands. A reading of an earlier day is named with its day
(reading_when: "yesterday at 9:45 PM", "on October 22 at 9:45 PM"), so that the time is never taken for today's.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from typing import Any

from .const import TREND_KEYS, TREND_LIBRE_BANDS, ZONES
from .i18n import t, tp
from .limits import LIMIT_FLAGS
from .units import UNIT_MMOLL, fmt, round_half_up

# unit -> its catalogue id: spoken.unit.<id>.one / .other ("{number} milligram(s) per deciliter"), the cards' ids
SPOKEN_UNITS = {
    "mg/dL": "mg_dl",
    "mmol/L": "mmol_l",
    "mg/dL/min": "mg_dl_min",
    "mmol/L/min": "mmol_l_min",
    "%": "percent",
    "mmol/mol": "mmol_mol",
    "min": "minute",
    "h": "hour",
    "d": "day",
}
# GlucoCraft's trend words and arrows (const.TREND_LIBRE_BANDS) -> the trend's key (spoken.trend.<key>): "Falling
# Quickly" and "↓" are both "falling quickly"; an unknown trend has no words
_TREND_OF = {
    **{word.lower(): TREND_KEYS[word] for _, word, _ in TREND_LIBRE_BANDS},
    **{arrow: TREND_KEYS[word] for _, word, arrow in TREND_LIBRE_BANDS},
}
# A comparison sign before a number -> its words (spoken.compare.<key>, the cards' GC_SPOKEN_COMPARE_KEYS): screen
# readers skip "<" and ">" at their usual punctuation level, and "<4%" would then be heard as "4%". The one Python
# table: speak_comparisons (the PDF's texts, which may write "≤" / "≥") and the summaries' target lines
# (notification._assess_target, which use the ASCII goal signs of const.GOAL_OPS only)
SPOKEN_COMPARISONS = {
    "<": "less_than",
    ">": "more_than",
    "<=": "at_most",
    "≤": "at_most",
    ">=": "at_least",
    "≥": "at_least",
}
# the sign and the value it applies to: a number with an optional unit symbol (a step of the cards' gcSpeakText)
_COMPARISON = re.compile(r"(<=|>=|[<>≤≥])\s?(\d+(?:[.,]\d+)?(?:\s?(?:%|mg/dL/min|mmol/L/min|mmol/mol|mg/dL|mmol/L))?)")
# A plus or minus sign before a number (the first step of the cards' gcSpeakText): screen readers skip "+" and read "-"
# as "dash" or not at all, so a difference would be heard without its direction. A sign counts only at the start of the
# text or after a space, "(" or ":", so that a hyphen between two numbers ("22-26") or inside a word stays as it is.
_SIGN = re.compile(r"(^|[\s(:])([+\-−])(\d+(?:[.,]\d+)?)")


def speak_number(value: float, decimals: int | None = None, dec: str = ".", sign: bool = False) -> str:
    """A number as spoken: `decimals` places (None: as given; halves away from zero), the decimal separator in use,
    "minus" for a negative number and, with sign=True, "plus" for a positive one. A value that rounds to zero is
    "0" or "0.0", never "minus 0.0"."""
    shown = value if decimals is None else round_half_up(value, decimals)
    a = abs(shown)
    text = (str(a) if decimals is None else f"{a:.{decimals}f}").replace(".", "," if dec == "," else ".")
    if shown < 0:
        return t("spoken.number.negative", number=text)
    return t("spoken.number.positive", number=text) if sign and shown > 0 else text


def speak(value: float, unit: str = "", decimals: int | None = None, dec: str = ".", sign: bool = False) -> str:
    """A value with its unit written out: speak(62, "mg/dL") = "62 milligrams per deciliter". The plural category
    follows the number as shown ("1 minute", "1.0 minutes"); a unit not in the table is spoken as it is written."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return t("spoken.not_available")
    number = speak_number(value, decimals, dec, sign)
    if not unit:
        return number
    if unit not in SPOKEN_UNITS:
        return t("spoken.number_unit", number=number, unit=unit)
    shown = decimals if decimals is not None else len(str(abs(value)).partition(".")[2].rstrip("0"))
    return tp(f"spoken.unit.{SPOKEN_UNITS[unit]}", abs(value), shown, number=number)


def speak_comparisons(text: str) -> str:
    """A text with every plus or minus sign and every comparison sign before a number in words, the rest as written:
    "-0.6 percentage points" -> "minus 0.6 percentage points", "Consensus target <4%" -> "Consensus target less than
    4%", "≤36%" -> "at most 36%"."""

    def sign(m: re.Match[str]) -> str:
        """Return the matched sign before a number in words."""
        word = t("spoken.number.positive" if m.group(2) == "+" else "spoken.number.negative", number=m.group(3))
        return m.group(1) + word

    text = _SIGN.sub(sign, text)
    return _COMPARISON.sub(lambda m: t(f"spoken.compare.{SPOKEN_COMPARISONS[m.group(1)]}", value=m.group(2)), text)


def trend_key(trend: str | None) -> str | None:
    """The key of a trend word or arrow ("Falling Quickly" or "↓" -> "falling_quickly"); None when unknown."""
    text = str(trend or "").strip()
    return _TREND_OF.get(text.lower()) or _TREND_OF.get(text)


def speak_trend(trend: str | None) -> str:
    """A trend word or arrow as spoken words ("Falling Quickly" -> "falling quickly"); "" when unknown."""
    key = trend_key(trend)
    return t(f"spoken.trend.{key}") if key else ""


def speak_limit(limit: str | None) -> str:
    """A LO / HI reading in words ("below the sensor's lower limit"); "" for a measured value. The CGM system measured
    no value, so the words say where the reading lies (spoken.limit.<flag>)."""
    return t(f"spoken.limit.{limit}") if limit in LIMIT_FLAGS else ""


def speak_glucose(mg: float, unit: str, limit: str | None = None, dec: str = ".") -> str:
    """A glucose value as spoken, in the display unit and rounded once as it is shown (mg/dL whole, mmol/L one
    decimal): "112 milligrams per deciliter", "6.2 millimoles per liter"; a LO / HI reading in words."""
    if limit in LIMIT_FLAGS:
        return speak_limit(limit)
    mmol = unit == UNIT_MMOLL
    return speak(float(fmt(mg, unit)), UNIT_MMOLL if mmol else "mg/dL", 1 if mmol else 0, dec)


def reading_when(local: datetime, today: date, regional: Any) -> str:
    """When a reading was measured, as spoken, in the time and date format settings (regional.Regional): "at 9:45 PM"
    for a reading of today, "yesterday at 9:45 PM", otherwise "on October 22 at 9:45 PM" ("on 22 October ..." with
    the day first), with the year when it is not the current year ("on October 22, 2025 at 9:45 PM")."""
    clock = regional.clock(local)
    day = local.date()
    if day == today:
        return t("spoken.when.today", time=clock)
    if day == today - timedelta(days=1):
        return t("spoken.when.yesterday", time=clock)
    named = regional.day_month(local, long=True, year=day.year != today.year)
    return t("spoken.when.date", date=named, time=clock)


def spoken_summary(
    *,
    status: str,
    current: bool,
    mg: float | None,
    limit: str | None,
    zone: str | None,
    trend: str | None,
    reading_when: str | None,
    unit: str,
    dec: str = ".",
) -> str:
    """The Spoken summary sentence, a shorter form of the cards' screen-reader description (no 5-minute delta, no
    page sentence), in the order of docs/ACCESSIBILITY.md rule 4: the data status when there is a problem ("Sensor
    expired.", "No current data."; during a sensor warm-up "Sensor warming up." alone), then the value with its unit and
    the zone word, the time of the reading and the trend words. While there is no current data (`current` False: the
    no-current-data flag), no value, zone or trend is said. After the sensor's stated expiry "Sensor expired." comes
    first, and while readings still arrive the value, the zone and the trend follow it (the trend follows the data, as
    on the cards).

    The reading is said as a list of phrases ("Glucose 112 milligrams per deciliter", "tight range", "measured at
    7:46 AM", "changing slowly"), each its own catalogue text, joined in this order by the list separator.

    status: coordinator.display_status (the status sentence only); current: there is current data (not
    coordinator.no_current_data); mg: the reading (mg/dL) and limit its LO / HI flag; zone: its zone (const.ZONES);
    trend: the trend word; reading_when: when the reading was measured, as reading_when() says it ("at 7:46 AM",
    "yesterday at 9:45 PM"); unit: the display unit; dec: the decimal separator in use.
    """
    if status == "warm_up":
        return t("spoken.summary.warm_up")
    if mg is None or status == "no_data":
        return t("spoken.summary.no_reading")
    sentences = [t("spoken.summary.expired")] if status == "expired" else []
    if not current:
        sentences.append(t("spoken.summary.no_current_data"))
        if reading_when:
            sentences.append(t("spoken.summary.last_reading", when=reading_when))
        return t("spoken.sentence_separator").join(sentences)
    phrases = [t("spoken.summary.glucose", value=speak_glucose(mg, unit, limit, dec))]
    if zone in ZONES:
        phrases.append(t(f"spoken.zone.{zone}"))
    if reading_when:
        phrases.append(t("spoken.summary.measured", when=reading_when))
    trend_words = speak_trend(trend)
    if trend_words:
        phrases.append(trend_words)
    sentences.append(t("spoken.summary.reading", phrases=t("spoken.list_separator").join(phrases)))
    return t("spoken.sentence_separator").join(sentences)
