# License: AGPL-3.0-only (LICENSE).
"""The stored settings of the config entry, read and validated in one place.

Home Assistant hands GlucoCraft the entry's data as it is stored. An entry written by an earlier version, or edited by
hand, can hold a value of the wrong type ("70" instead of 70, None, a list) or outside the span the settings screens
allow. Every consumer therefore reads a setting through this module, never from the stored data directly:

- setting(data, key) returns one setting: the stored value when it has the setting's type and lies within its span,
  otherwise the setting's default. A missing key is the default as well. setting_default(key) is that default.
- ranges(data) returns the six range limits. They are validated as a set: each limit within its span, and the six in
  the order the settings screen requires (RANGE_PAIRS); otherwise the default ranges (the consensus ranges) are used,
  because a single limit replaced by its default could leave the set out of order. With the sensor's limits
  (limits.threshold_span) the very-low threshold is at least the sensor's lower limit and the very-high threshold at
  most its upper limit; only that threshold changes, never the other five and never to the defaults, and it can leave
  the low (or the high) band empty (ranges()). sensor_limit_conflicts(data, span) names every value in use that
  differs from the stored one.
- band_colors(data) returns the seven graph range colors, each validated like any other setting.
- The optional feature Custom ranges and goals (CONF_CUSTOM_RANGES_AND_GOALS, off in a new installation): while it is
  off, ranges() returns the default ranges and setting() the default of each goal and of the weekly pages' range set,
  so that every output uses the consensus ranges and targets; the stored values are kept and are in use again when it
  is turned on. stored_custom_values(data) tells whether the stored ranges or goals differ from the defaults, whatever
  the switch.
- notify_hour_minute(text) returns the hour and minute of the summaries' time, as the scheduler needs them.

A value is never repaired by guessing (the text "70" is not read as the number 70): a value of the wrong type is treated
as missing. There are deliberate exceptions: the no-data notice's delay and the four sensor limit settings come from
select boxes, which store their choice as a text, so the text "30" or "400" is read as that choice (_notice_delay,
_sensor_limit); and a stored no-current-data limit above the highest allowed is read as the highest, not as the default
(_stale_threshold). Reading never changes the stored entry; every save keeps only the keys GlucoCraft knows
(known_entry_data). The keys that are not settings (the primary source and the wall-panel address) are read by their
consumers as stored. The zone colors are validated by const.zone_colors.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from typing import Any

from .const import (
    BAND_COLORS,
    CARD_BACKGROUND_THEME,
    CONF_A1C_UNIT,
    CONF_BAND_COLORS,
    CONF_CARD_BACKGROUND,
    CONF_CUSTOM_RANGES_AND_GOALS,
    CONF_DATE_FORMAT,
    CONF_DECIMAL_SEPARATOR,
    CONF_DISPLAY_CARD,
    CONF_FIRST_WEEKDAY,
    CONF_FLASH_NO_CURRENT_DATA,
    CONF_GLUCOSE_ENTITY,
    CONF_GOAL_CV,
    CONF_GOAL_GMI,
    CONF_GOAL_TAR,
    CONF_GOAL_TAR_CRITICAL,
    CONF_GOAL_TBR,
    CONF_GOAL_TBR_CRITICAL,
    CONF_GOAL_TIME_IN_IDEAL_RANGE,
    CONF_GOAL_TIME_IN_TARGET_RANGE,
    CONF_NO_DATA_NOTICE,
    CONF_NO_DATA_NOTICE_DELAY,
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_TIME,
    CONF_NOTIFY_WEEKLY_DAY,
    CONF_NOTIFY_WEEKLY_ENABLED,
    CONF_ON_REMOVAL,
    CONF_PANEL_BACKGROUND,
    CONF_PANEL_FEED,
    CONF_PANEL_WEBHOOK_ID,
    CONF_PAPER_SIZE,
    CONF_PHONE_TARGETS,
    CONF_PHONE_TYPES,
    CONF_PRIMARY_TIME_ATTR,
    CONF_PRINTER,
    CONF_RANGE_CRITICAL_HIGH,
    CONF_RANGE_CRITICAL_LOW,
    CONF_RANGE_HIGH,
    CONF_RANGE_LOW,
    CONF_RANGE_TIGHT_HIGH,
    CONF_RANGE_TIGHT_LOW,
    CONF_SECONDARY_ENTITY,
    CONF_SECONDARY_SENSOR_LOWER_LIMIT,
    CONF_SECONDARY_SENSOR_UPPER_LIMIT,
    CONF_SECONDARY_TIME_ATTR,
    CONF_SENSOR_EXPIRY_ENTITY,
    CONF_SENSOR_LOWER_LIMIT,
    CONF_SENSOR_UPPER_LIMIT,
    CONF_SEPARATE_STATISTICS,
    CONF_SHOW_DASHBOARD,
    CONF_STALE_THRESHOLD,
    CONF_TEXT_OUTLINE,
    CONF_TIME_FORMAT,
    CONF_TREND_DISPLAY,
    CONF_UNIT,
    CONF_WEEKLY_PAGES,
    CONF_ZONE_COLORS,
    DATE_FORMAT_OPTIONS,
    DEFAULT_CARD_BACKGROUND,
    DEFAULT_GOAL_CV,
    DEFAULT_GOAL_GMI,
    DEFAULT_GOAL_TAR,
    DEFAULT_GOAL_TAR_CRITICAL,
    DEFAULT_GOAL_TBR,
    DEFAULT_GOAL_TBR_CRITICAL,
    DEFAULT_GOAL_TIME_IN_IDEAL_RANGE,
    DEFAULT_GOAL_TIME_IN_TARGET_RANGE,
    DEFAULT_NO_DATA_NOTICE_DELAY,
    DEFAULT_NOTIFY_WEEKLY_DAY,
    DEFAULT_PANEL_BACKGROUND,
    DEFAULT_PHONE_TYPES,
    DEFAULT_RANGE_CRITICAL_HIGH,
    DEFAULT_RANGE_CRITICAL_LOW,
    DEFAULT_RANGE_HIGH,
    DEFAULT_RANGE_LOW,
    DEFAULT_RANGE_TIGHT_HIGH,
    DEFAULT_RANGE_TIGHT_LOW,
    DEFAULT_STALE_THRESHOLD,
    DISPLAY_CARD_RECTANGULAR,
    DISPLAY_CARD_SQUARE,
    NO_DATA_NOTICE_DELAYS,
    ON_REMOVAL_KEEP,
    ON_REMOVAL_OPTIONS,
    PHONE_TYPES,
    TREND_DISPLAY_ARROW,
    TREND_DISPLAY_OPTIONS,
    WEEKLY_PAGES_CONSENSUS,
    WEEKLY_PAGES_PERSONAL,
    valid_hex_color,
)
from .limits import LIMIT_AUTO, LOWER_CHOICES, UPPER_CHOICES, setting_value
from .regional import (
    A1C_BOTH,
    A1C_MMOL_MOL,
    A1C_PERCENT,
    DEC_CHOICES,
    PAPER_A4,
    PAPER_LETTER,
    TIME_12,
    TIME_24,
    WEEK_CHOICES,
)
from .units import UNIT_MGDL, UNIT_MMOLL

# ---------------------------------------------------------------------------------------------- the choices
UNIT_OPTIONS = (UNIT_MGDL, UNIT_MMOLL)
DISPLAY_CARD_OPTIONS = (DISPLAY_CARD_SQUARE, DISPLAY_CARD_RECTANGULAR)
WEEKLY_PAGES_OPTIONS = (WEEKLY_PAGES_CONSENSUS, WEEKLY_PAGES_PERSONAL)
TIME_FORMAT_OPTIONS = ["auto", TIME_12, TIME_24]
# The regional settings with a selector of their own: "auto" first, then the explicit choices (regional.py resolves
# them). The time and date formats have their own lists (TIME_FORMAT_OPTIONS, const.DATE_FORMAT_OPTIONS).
REGIONAL_OPTIONS = {
    CONF_FIRST_WEEKDAY: ["auto", *WEEK_CHOICES],
    CONF_DECIMAL_SEPARATOR: ["auto", *DEC_CHOICES],
    CONF_A1C_UNIT: ["auto", A1C_PERCENT, A1C_MMOL_MOL, A1C_BOTH],
    CONF_PAPER_SIZE: ["auto", PAPER_LETTER, PAPER_A4],
}

# ---------------------------------------------------------------------------------------------- the spans
# The range limits (mg/dL): the span of each limit, its default, and the order of the six
RANGE_LIMITS = {
    CONF_RANGE_CRITICAL_LOW: (30, 70),
    CONF_RANGE_LOW: (50, 100),
    CONF_RANGE_TIGHT_LOW: (50, 100),
    CONF_RANGE_TIGHT_HIGH: (100, 200),
    CONF_RANGE_HIGH: (120, 300),
    CONF_RANGE_CRITICAL_HIGH: (180, 400),
}
RANGE_DEFAULTS = {
    CONF_RANGE_CRITICAL_LOW: DEFAULT_RANGE_CRITICAL_LOW,
    CONF_RANGE_LOW: DEFAULT_RANGE_LOW,
    CONF_RANGE_TIGHT_LOW: DEFAULT_RANGE_TIGHT_LOW,
    CONF_RANGE_TIGHT_HIGH: DEFAULT_RANGE_TIGHT_HIGH,
    CONF_RANGE_HIGH: DEFAULT_RANGE_HIGH,
    CONF_RANGE_CRITICAL_HIGH: DEFAULT_RANGE_CRITICAL_HIGH,
}
RANGE_ORDER = tuple(RANGE_LIMITS)  # ascending
# The order the settings screen requires, pair by pair: (lower key, higher key, strictly lower). "<" between very low
# and low, between the tight range's two ends, and between high and very high; "<=" where the consensus limits are
# equal (low = tight range from = 70 mg/dL) or may be (a tight range that ends at the high limit).
RANGE_PAIRS = (
    (CONF_RANGE_CRITICAL_LOW, CONF_RANGE_LOW, True),
    (CONF_RANGE_LOW, CONF_RANGE_TIGHT_LOW, False),
    (CONF_RANGE_TIGHT_LOW, CONF_RANGE_TIGHT_HIGH, True),
    (CONF_RANGE_TIGHT_HIGH, CONF_RANGE_HIGH, False),
    (CONF_RANGE_HIGH, CONF_RANGE_CRITICAL_HIGH, True),
)
# The goals (percent; GMI in % with one decimal): the span of each goal's number box. Every goal in its span can be
# met: the time in range is compared as "more than", so its span ends at 99%, and the time below the very-low limit as
# "less than", so its span starts at 1%.
GOAL_LIMITS = {
    CONF_GOAL_TIME_IN_TARGET_RANGE: (50, 99),
    CONF_GOAL_TIME_IN_IDEAL_RANGE: (20, 100),
    CONF_GOAL_TBR: (1, 20),
    CONF_GOAL_TBR_CRITICAL: (1, 10),
    CONF_GOAL_TAR: (5, 50),
    CONF_GOAL_TAR_CRITICAL: (1, 20),
    CONF_GOAL_CV: (15, 50),
    CONF_GOAL_GMI: (4.0, 12.0),
}
# The default goals: the consensus targets; the two goals without a published target start empty (const.py)
GOAL_DEFAULTS = {
    CONF_GOAL_TIME_IN_TARGET_RANGE: DEFAULT_GOAL_TIME_IN_TARGET_RANGE,
    CONF_GOAL_TIME_IN_IDEAL_RANGE: DEFAULT_GOAL_TIME_IN_IDEAL_RANGE,
    CONF_GOAL_TBR: DEFAULT_GOAL_TBR,
    CONF_GOAL_TBR_CRITICAL: DEFAULT_GOAL_TBR_CRITICAL,
    CONF_GOAL_TAR: DEFAULT_GOAL_TAR,
    CONF_GOAL_TAR_CRITICAL: DEFAULT_GOAL_TAR_CRITICAL,
    CONF_GOAL_CV: DEFAULT_GOAL_CV,
    CONF_GOAL_GMI: DEFAULT_GOAL_GMI,
}
STALE_THRESHOLD_LIMITS = (5, 30)  # the no-current-data limit, whole minutes
DEFAULT_NOTIFY_TIME = "08:00"


# ---------------------------------------------------------------------------------------------- the readers
class _Invalid:
    """The marker a reader returns for a value it does not accept (the setting's default is used instead)."""


_INVALID = _Invalid()
_TIME_OF_DAY = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


def _number(value: Any) -> float | None:
    """Return a stored number (int or float, finite), None for anything else (bool and text included)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _within(lo: float, hi: float, whole: bool = False) -> Callable[[Any], Any]:
    """A reader of a number within [lo, hi]; with whole=True only a whole number, returned as an int."""

    def read(value: Any) -> Any:
        """Return the value if it is a number within [lo, hi] (as an int when whole=True), else _INVALID."""
        v = _number(value)
        if v is None or not lo <= v <= hi or (whole and not float(v).is_integer()):
            return _INVALID
        return int(v) if whole else v

    return read


def _stale_threshold(value: Any) -> Any:
    """The no-current-data limit: whole minutes within STALE_THRESHOLD_LIMITS; a stored whole number above the highest
    is read as the highest, so that the limit never exceeds it."""
    lo, hi = STALE_THRESHOLD_LIMITS
    v = _number(value)
    if v is not None and float(v).is_integer() and v > hi:
        return hi
    return _within(lo, hi, whole=True)(value)


def _goal(lo: float, hi: float) -> Callable[[Any], Any]:
    """A reader of a goal: None is "no goal set"; otherwise a number within [lo, hi]."""
    number = _within(lo, hi)
    return lambda value: None if value is None else number(value)


def _switch(value: Any) -> Any:
    """A switch is True or False; anything else (the text "false" included) is not a switch."""
    return value if isinstance(value, bool) else _INVALID


def _one_of(options) -> Callable[[Any], Any]:
    """A reader of one of the texts in `options`."""
    return lambda value: value if isinstance(value, str) and value in options else _INVALID


def _sensor_limit(choices: tuple[int, ...]) -> Callable[[Any], Any]:
    """A reader of a sensor limit setting: one of `choices` (as the select's text "400" or a number), else Automatic."""

    def read(value: Any) -> Any:
        """Return the limit as its text ("400") when the value is one of `choices` (as a text or a number), else
        _INVALID."""
        v = None if isinstance(value, bool) else setting_value(value, choices)
        return str(v) if v is not None else _INVALID

    return read


def _entity(value: Any) -> Any:
    """An entity id: a non-empty text (an empty text or None is "none chosen")."""
    return value.strip() if isinstance(value, str) and value.strip() else _INVALID


def _text(value: Any) -> Any:
    """A free text without surrounding spaces (a time attribute, the printer address)."""
    return value.strip() if isinstance(value, str) else _INVALID


def _time_of_day(value: Any) -> Any:
    """A time of day as the time selector stores it: "HH:MM" or "HH:MM:SS"."""
    m = _TIME_OF_DAY.match(value) if isinstance(value, str) else None
    if not m or int(m[1]) > 23 or int(m[2]) > 59 or int(m[3] or 0) > 59:
        return _INVALID
    return value


def time_of_day_valid(value: Any) -> bool:
    """True for a time of day as the time selector stores it (the settings screens refuse any other with a field error;
    a value stored some other way falls back to the default, as every setting does)."""
    return _time_of_day(value) is not _INVALID


def _weekday(value: Any) -> Any:
    """A day of the week as stored: an int, 0 = Monday ... 6 = Sunday."""
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 6 else _INVALID


def _text_list(value: Any) -> Any:
    """A list of texts (the notify actions); entries that are not texts are left out."""
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else _INVALID


def _notice_delay(value: Any) -> Any:
    """The no-data notice's delay: one of NO_DATA_NOTICE_DELAYS minutes (an int, or the selector's text "30")."""
    # isascii() first: isdigit() is also True for digits such as "²" that int() cannot read
    if isinstance(value, str) and value.isascii() and value.isdigit():
        value = int(value)
    valid = isinstance(value, int) and not isinstance(value, bool) and value in NO_DATA_NOTICE_DELAYS
    return value if valid else _INVALID


def _phone_types(value: Any) -> Any:
    """A list of the notification types that go to the phones; unknown types are left out."""
    return [v for v in value if isinstance(v, str) and v in PHONE_TYPES] if isinstance(value, list) else _INVALID


def _color(value: Any) -> Any:
    """A color as "#RRGGBB" (six hexadecimal digits), returned in upper case so that every consumer compares colors
    the same way; a color name, a short form or a list of numbers is not a stored color."""
    return value.upper() if valid_hex_color(value) else _INVALID


def _card_background(value: Any) -> Any:
    """The display card's background: "theme" (the Home Assistant theme) or a color."""
    return value if value == CARD_BACKGROUND_THEME else _color(value)


# Every setting except the six ranges: (default, reader)
_SETTINGS: dict[str, tuple[Any, Callable[[Any], Any]]] = {
    # sources
    CONF_SECONDARY_ENTITY: (None, _entity),
    CONF_SENSOR_EXPIRY_ENTITY: (None, _entity),
    CONF_PRIMARY_TIME_ATTR: ("", _text),
    CONF_SECONDARY_TIME_ATTR: ("", _text),
    CONF_SENSOR_LOWER_LIMIT: (LIMIT_AUTO, _sensor_limit(LOWER_CHOICES)),
    CONF_SENSOR_UPPER_LIMIT: (LIMIT_AUTO, _sensor_limit(UPPER_CHOICES)),
    CONF_SECONDARY_SENSOR_LOWER_LIMIT: (LIMIT_AUTO, _sensor_limit(LOWER_CHOICES)),
    CONF_SECONDARY_SENSOR_UPPER_LIMIT: (LIMIT_AUTO, _sensor_limit(UPPER_CHOICES)),
    CONF_STALE_THRESHOLD: (DEFAULT_STALE_THRESHOLD, _stale_threshold),
    # goals
    **{key: (default, _goal(*GOAL_LIMITS[key])) for key, default in GOAL_DEFAULTS.items()},
    # units and formats
    CONF_UNIT: (UNIT_MGDL, _one_of(UNIT_OPTIONS)),
    CONF_TIME_FORMAT: ("auto", _one_of(TIME_FORMAT_OPTIONS)),
    CONF_DATE_FORMAT: ("auto", _one_of(DATE_FORMAT_OPTIONS)),
    **{key: ("auto", _one_of(options)) for key, options in REGIONAL_OPTIONS.items()},
    # displays
    CONF_DISPLAY_CARD: (DISPLAY_CARD_SQUARE, _one_of(DISPLAY_CARD_OPTIONS)),
    CONF_TREND_DISPLAY: (TREND_DISPLAY_ARROW, _one_of(TREND_DISPLAY_OPTIONS)),
    CONF_WEEKLY_PAGES: (WEEKLY_PAGES_CONSENSUS, _one_of(WEEKLY_PAGES_OPTIONS)),
    **{key: (BAND_COLORS[zone], _color) for zone, key in CONF_BAND_COLORS.items()},
    CONF_CARD_BACKGROUND: (DEFAULT_CARD_BACKGROUND, _card_background),
    CONF_PANEL_BACKGROUND: (DEFAULT_PANEL_BACKGROUND, _color),
    CONF_TEXT_OUTLINE: (False, _switch),
    CONF_FLASH_NO_CURRENT_DATA: (True, _switch),
    CONF_PANEL_FEED: (False, _switch),
    # notifications
    CONF_NOTIFY_ENABLED: (True, _switch),
    CONF_NOTIFY_TIME: (DEFAULT_NOTIFY_TIME, _time_of_day),
    CONF_NOTIFY_WEEKLY_ENABLED: (False, _switch),
    CONF_NOTIFY_WEEKLY_DAY: (DEFAULT_NOTIFY_WEEKLY_DAY, _weekday),
    CONF_NO_DATA_NOTICE: (False, _switch),
    CONF_NO_DATA_NOTICE_DELAY: (DEFAULT_NO_DATA_NOTICE_DELAY, _notice_delay),
    CONF_PHONE_TARGETS: ([], _text_list),
    CONF_PHONE_TYPES: (DEFAULT_PHONE_TYPES, _phone_types),
    # report and dashboard
    CONF_PRINTER: ("", _text),
    CONF_SHOW_DASHBOARD: (True, _switch),
    CONF_ON_REMOVAL: (ON_REMOVAL_KEEP, _one_of(ON_REMOVAL_OPTIONS)),
    # the optional features: off unless turned on
    CONF_CUSTOM_RANGES_AND_GOALS: (False, _switch),
    CONF_SEPARATE_STATISTICS: (False, _switch),
}
# Every key GlucoCraft stores in its entry: the settings, the six range limits, the zone colors (const.zone_colors), the
# primary source and the wall-panel address. A stored key that is not one of these is read by nothing, the diagnostics
# redact its value (diagnostics.py), and the next save drops it (known_entry_data).
ENTRY_KEYS = frozenset(
    {
        *_SETTINGS,
        *RANGE_LIMITS,
        *CONF_ZONE_COLORS.values(),
        CONF_GLUCOSE_ENTITY,
        CONF_PANEL_WEBHOOK_ID,
    }
)


def known_entry_data(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return the entry data to save: every key of ENTRY_KEYS with its value as given, without any other key.

    Every writer of the entry's data (the Configure screens, the display unit taken from the entity settings, the
    wall-panel address) saves through this, so that a key no current code reads is not written back with every save.
    """
    return {key: value for key, value in data.items() if key in ENTRY_KEYS}


# The settings in effect only while Custom ranges and goals is on: the eight goals and the weekly pages' range set (the
# six range limits are read with ranges()). While it is off, each is its default and the stored value is kept.
_CUSTOM_ONLY = frozenset({*GOAL_DEFAULTS, CONF_WEEKLY_PAGES})


def setting(data: Mapping[str, Any] | None, key: str) -> Any:
    """Return one stored setting, validated: the stored value when it has the setting's type and lies within its span,
    otherwise (also when it is missing) the setting's default. A goal and the weekly pages' range set are their
    defaults while Custom ranges and goals is off (_CUSTOM_ONLY). The range limits are read with ranges()."""
    default, read = _SETTINGS[key]
    stored = (data or {}).get(key, _INVALID)
    if key in _CUSTOM_ONLY and not custom_ranges_on(data):
        stored = _INVALID
    value = _INVALID if stored is _INVALID else read(stored)
    if value is _INVALID:
        value = default
    return list(value) if isinstance(value, list) else value


def setting_default(key: str) -> Any:
    """Return the default of one setting: the value a new installation starts with (setting() without a stored
    value)."""
    return setting(None, key)


def custom_ranges_on(data: Mapping[str, Any] | None) -> bool:
    """True while the optional feature Custom ranges and goals is on."""
    return setting(data, CONF_CUSTOM_RANGES_AND_GOALS)


def _ordered(values: Mapping[str, float]) -> bool:
    """True when the six range limits are in the order of RANGE_PAIRS."""
    return all(values[lo] < values[hi] if strict else values[lo] <= values[hi] for lo, hi, strict in RANGE_PAIRS)


def ranges(data: Mapping[str, Any] | None, span: tuple[float, float] | None = None) -> dict[str, float]:
    """Return the six stored range limits (mg/dL), validated as a set.

    Each limit is used when it is a number within its span (RANGE_LIMITS), otherwise its default. When the six are then
    not in the order the settings screen requires (RANGE_PAIRS: very low < low <= tight range from < tight range to <=
    high < very high), the default ranges are used as a whole, so that every output works on a set the screen could
    have saved. While Custom ranges and goals is off, the default ranges are used whatever is stored.

    span: the sensor's limits that the very-low and the very-high threshold must keep (limits.threshold_span), so that
    a LO reading is always very low and a HI reading always very high. A stored threshold outside them, saved before the
    source or its limit changed, is used at the sensor's limit (sensor_limit_conflicts names it); the other five limits
    stay as stored, and the default ranges are never used because of a sensor limit. The very-low threshold can then
    equal the low limit (very low 45 and low 50 with a lower limit of 50: both 50), and the very-high threshold the
    high limit: that band is empty, and rightly so, because the sensor reports every reading beyond its limit as LO or
    HI, which is always very low or very high (limits.scale_position). Every consumer counts a reading in the first
    band it lies in (very low below the very-low threshold, low below the low limit, ...), so an empty band holds no
    reading and no time. The low limit in use is never below the very-low threshold and the high limit never above the
    very-high threshold (max / min below), so that no two limits in use are in descending order; with the spans of
    RANGE_LIMITS and limits.LOWER_CHOICES / UPPER_CHOICES (the lowest low limit equals the highest lower limit, 50; the
    highest high limit, 300, lies below the lowest upper limit, 400) this never changes the stored low or high limit.
    With the same spans the very-high threshold (at most 400) never exceeds an upper limit (at least 400), so only the
    very-low threshold is moved in practice; the very-high clamp is a guard for a wider span or a lower upper limit.
    """
    d = data or {}
    out = stored_ranges(d) if custom_ranges_on(d) else dict(RANGE_DEFAULTS)
    if span is not None:
        out[CONF_RANGE_CRITICAL_LOW] = max(out[CONF_RANGE_CRITICAL_LOW], span[0])
        out[CONF_RANGE_CRITICAL_HIGH] = min(out[CONF_RANGE_CRITICAL_HIGH], span[1])
        out[CONF_RANGE_LOW] = max(out[CONF_RANGE_LOW], out[CONF_RANGE_CRITICAL_LOW])
        out[CONF_RANGE_HIGH] = min(out[CONF_RANGE_HIGH], out[CONF_RANGE_CRITICAL_HIGH])
    return out


def stored_ranges(data: Mapping[str, Any]) -> dict[str, float]:
    """The six stored range limits, each validated against its span and the six as a set (ranges()), whether Custom
    ranges and goals is on or off and without the sensor's limits: what the Glucose ranges screen shows and keeps."""
    out = {key: _within(lo, hi)(data.get(key, _INVALID)) for key, (lo, hi) in RANGE_LIMITS.items()}
    out = {key: RANGE_DEFAULTS[key] if value is _INVALID else value for key, value in out.items()}
    return out if _ordered(out) else dict(RANGE_DEFAULTS)


def stored_custom_values(data: Mapping[str, Any] | None) -> bool:
    """True when the stored ranges or goals, validated as they would be used, differ from the defaults (the consensus
    ranges and targets), whether Custom ranges and goals is on or off."""
    with_custom = {**(data or {}), CONF_CUSTOM_RANGES_AND_GOALS: True}
    return ranges(with_custom) != RANGE_DEFAULTS or any(
        setting(with_custom, key) != default for key, default in GOAL_DEFAULTS.items()
    )


def sensor_limit_conflicts(data: Mapping[str, Any] | None, span: tuple[float, float]) -> dict[str, tuple[float, float]]:
    """Return the range limits in use that differ from the stored ones because of the sensor's limits (ranges() with
    span): {key: (stored, in use)}, empty when the stored ones fit. Each of the six is compared; with the spans of
    RANGE_LIMITS only the very-low and the very-high threshold can differ (ranges()), and each has its Repairs notice
    (coordinator.RANGE_ISSUE_IDS). In practice only the very-low threshold does: the very-high threshold (at most 400)
    never exceeds an upper limit (at least 400, limits.UPPER_CHOICES), so its clamp and its notice are a guard for a
    wider span or a lower upper limit that the current spans never reach."""
    stored, used = ranges(data), ranges(data, span)
    return {key: (stored[key], used[key]) for key in RANGE_ORDER if stored[key] != used[key]}


def band_colors(data: Mapping[str, Any] | None) -> dict[str, str]:
    """Return the graph range color of each of the seven zones ("#RRGGBB", upper case): the stored color when it is
    valid, otherwise the zone's default (const.BAND_COLORS)."""
    return {zone: setting(data, key) for zone, key in CONF_BAND_COLORS.items()}


def notify_hour_minute(notify_time: str) -> tuple[int, int]:
    """Return the hour and minute of the summaries' time as setting(data, CONF_NOTIFY_TIME) returns it ("HH:MM" or
    "HH:MM:SS")."""
    hour, minute = notify_time.split(":")[:2]
    return int(hour), int(minute)
