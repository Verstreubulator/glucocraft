# License: AGPL-3.0-only (LICENSE).
"""Config flow and options flow for GlucoCraft.

The setup shows the disclaimer, then asks what to do with readings an earlier installation kept (only when there are
any; kept.py), the sources and the units and notifications, and creates the entry only at the end; the two optional
features start off (Custom ranges and goals, Separate statistics entities). The options flow (Configure) is a menu
of screens by topic, each of which saves only its own fields; the Glucose ranges and Custom goals
screens are in the menu only while Custom ranges and goals is on (Configure > Optional features). Every field has a
short label and an explanation under it (strings.json "data" and "data_description"); the second source and the
graph range colors are collapsed sections, flattened before saving, so that every setting keeps its stored key.

Every setting, from the setup and from Configure alike, is kept in the entry's data, so that there is one place to
read and one module that validates it (settings.py); the entry's options stay empty. This is not Home Assistant's
usual data / options split, so the options flow writes the entry's data itself, reloads the entry itself (there is no
update listener) and ends with an empty options entry (_async_finish).

This module holds the two flows, their steps and the checks the steps run on the input (each error has its text in
strings.json "error"). The forms are built in flow_schemas.py; the entity-registry lookups, the printer address check
and the phones' delivery test are in flow_helpers.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import (
    BAND_COLORS,
    CARD_BACKGROUND_THEME,
    CONF_BAND_COLORS,
    CONF_CARD_BACKGROUND,
    CONF_CUSTOM_RANGES_AND_GOALS,
    CONF_DATE_FORMAT,
    CONF_DISPLAY_CARD,
    CONF_FLASH_NO_CURRENT_DATA,
    CONF_GLUCOSE_ENTITY,
    CONF_GOAL_GMI,
    CONF_GOAL_TAR,
    CONF_GOAL_TAR_CRITICAL,
    CONF_GOAL_TBR,
    CONF_GOAL_TBR_CRITICAL,
    CONF_GOAL_TIME_IN_IDEAL_RANGE,
    CONF_NO_DATA_NOTICE_DELAY,
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_TIME,
    CONF_NOTIFY_WEEKLY_DAY,
    CONF_PANEL_BACKGROUND,
    CONF_PANEL_FEED,
    CONF_PANEL_WEBHOOK_ID,
    CONF_PHONE_TARGETS,
    CONF_PHONE_TEST,
    CONF_PHONE_TYPES,
    CONF_PRIMARY_TIME_ATTR,
    CONF_PRINTER,
    CONF_RANGE_CRITICAL_HIGH,
    CONF_RANGE_CRITICAL_LOW,
    CONF_REGENERATE_PANEL_WEBHOOK,
    CONF_SECONDARY_ENTITY,
    CONF_SECONDARY_TIME_ATTR,
    CONF_SENSOR_EXPIRY_ENTITY,
    CONF_SHOW_DASHBOARD,
    CONF_STALE_THRESHOLD,
    CONF_TEXT_OUTLINE,
    CONF_TIME_FORMAT,
    CONF_TREND_DISPLAY,
    CONF_UNIT,
    CONF_WEEKLY_PAGES,
    CONF_ZONE_COLORS,
    DEFAULT_CARD_BACKGROUND,
    DEFAULT_NOTIFY_WEEKLY_DAY,
    DEFAULT_STALE_THRESHOLD,
    DISCLAIMER,
    DOMAIN,
    ENTRY_MINOR_VERSION,
    ENTRY_VERSION,
    TREND_DISPLAY_ARROW,
    ZONE_COLORS,
    zone_colors,
)
from .contrast import contrast_note, shared_colors_note
from .flow_helpers import _send_phone_test, _source_platform, is_glucocraft_entity, printer_address_valid
from .flow_schemas import (
    CONF_BAND_COLORS_RESET,
    CONF_COLORS_RESET,
    FEATURE_SWITCHES,
    FIELD_CARD_BACKGROUND_COLOR,
    FIELD_KEPT_READINGS,
    KEPT_DELETE,
    KEPT_KEEP,
    LIMIT_SETTINGS,
    PRIMARY_LIMITS,
    SECONDARY_LIMITS,
    SECTION_SECOND_SOURCE,
    _disclaimer_schema,
    _displays_schema,
    _features_schema,
    _finish_schema,
    _flatten,
    _goals_schema,
    _kept_schema,
    _notifications_schema,
    _range_shown,
    _ranges_schema,
    _report_schema,
    _rgb_to_hex,
    _section_errors,
    _shown,
    _sources_schema,
    _units_schema,
    _weekly_pages_offered,
)
from .i18n import async_setup as async_setup_language
from .i18n import t
from .kept import DATA_FOLDER, KEPT_NOTICE_ID, kept_summary
from .kept import discard as discard_kept
from .limits import LIMIT_AUTO, resolve, threshold_span
from .panel_feed import new_webhook_id
from .regional import regional_from_settings
from .settings import (
    DEFAULT_NOTIFY_TIME,
    GOAL_LIMITS,
    RANGE_DEFAULTS,
    RANGE_LIMITS,
    RANGE_ORDER,
    RANGE_PAIRS,
    REGIONAL_OPTIONS,
    band_colors,
    custom_ranges_on,
    known_entry_data,
    sensor_limit_conflicts,
    setting,
    setting_default,
    stored_ranges,
    time_of_day_valid,
)
from .unit_registry import apply_display_unit
from .units import UNIT_MGDL, fmt, non_glucose_unit, round_half_up, to_mgdl_setting, unit_from_attribute

# ---- the sources: never one of GlucoCraft's own entities (flow_helpers.py says why)
SOURCE_FIELDS = (CONF_GLUCOSE_ENTITY, CONF_SECONDARY_ENTITY, CONF_SENSOR_EXPIRY_ENTITY)


def _source_errors(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, str]:
    """Per source field: "entity_not_found" for a primary source that does not exist, "source_is_glucocraft" for any
    of GlucoCraft's own entities as a glucose source and "expiry_is_glucocraft" as the CGM sensor expiry entity (its
    own error: the reason differs), "source_unit_not_glucose" for a glucose source (primary or second) whose state
    states a unit other than mg/dL or mmol/L (a temperature, a percentage: its values would be read as glucose; a
    source without a unit stays allowed, some integrations state none), "secondary_same_as_primary" for a second
    source that is the primary source itself (the coordinator would ignore it, and the settings would show a second
    source that does nothing)."""
    errors: dict[str, str] = {}
    primary = user_input.get(CONF_GLUCOSE_ENTITY)
    if CONF_GLUCOSE_ENTITY in user_input and (not primary or hass.states.get(primary) is None):
        errors[CONF_GLUCOSE_ENTITY] = "entity_not_found"
    for key in (CONF_GLUCOSE_ENTITY, CONF_SECONDARY_ENTITY):
        source = hass.states.get(user_input[key]) if key not in errors and user_input.get(key) else None
        if source is not None and non_glucose_unit(source.attributes.get("unit_of_measurement")):
            errors[key] = "source_unit_not_glucose"
    for key in SOURCE_FIELDS:
        if is_glucocraft_entity(hass, user_input.get(key)):
            errors[key] = "expiry_is_glucocraft" if key == CONF_SENSOR_EXPIRY_ENTITY else "source_is_glucocraft"
    secondary = user_input.get(CONF_SECONDARY_ENTITY)
    if secondary and secondary == primary and CONF_SECONDARY_ENTITY not in errors:
        errors[CONF_SECONDARY_ENTITY] = "secondary_same_as_primary"
    return errors


def _stale_minutes(values: dict[str, Any]) -> int:
    """No current data after, as stored: whole minutes (the number box returns a float), halves away from zero; the
    default when the field is missing."""
    value = values.get(CONF_STALE_THRESHOLD)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_STALE_THRESHOLD
    return int(round_half_up(float(value)))


# ---- the ranges
def _ranges_to_mgdl(user_input: dict[str, Any], unit: str, stored: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the range limits from the screen (display unit) in mg/dL (whole from mg/dL, one decimal from mmol/L).

    A field whose value was not changed keeps its stored value; otherwise saving an unchanged form in mmol/L would
    move values (100 mg/dL is shown as 5.6 mmol/L, which converts back to 101). Every value stays within its mg/dL
    span (16.7 mmol/L would otherwise store 301, above the 300 maximum).
    """
    out: dict[str, Any] = {}
    for k, v in user_input.items():
        if k not in RANGE_LIMITS:
            out[k] = v
        elif abs(float(v) - float(_range_shown(stored, k, unit))) < 1e-6:
            out[k] = (stored or {}).get(k, RANGE_DEFAULTS[k])
        else:
            out[k] = to_mgdl_setting(v, unit, *RANGE_LIMITS[k])
    return out


def _range_errors(ranges: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, str]:
    """The fields out of order (the order of settings.RANGE_PAIRS, checked pair by pair), each with its own error text
    (strings.json "error": "<field>_order"); both fields of every pair out of order are marked, plus the summary
    "range_order" (base). Empty when the order is correct."""
    v = {k: float(ranges.get(k, (base or {}).get(k, RANGE_DEFAULTS[k]))) for k in RANGE_ORDER}
    errors: dict[str, str] = {}
    for lo, hi, strict in RANGE_PAIRS:
        if (v[lo] >= v[hi]) if strict else (v[lo] > v[hi]):
            errors[lo] = f"{lo}_order"
            errors[hi] = f"{hi}_order"
    if errors:
        errors["base"] = "range_order"
    return errors


# ---- the sensor's limits: the very-low threshold lies at or above the glucose sources' lower limit and the very-high
# threshold at or below their upper limit (limits.threshold_span), so that a LO reading is always very low and a HI
# reading always very high. The form compares at the precision it shows, so that the limit itself is accepted in either
# unit (40 mg/dL is shown as 2.2 mmol/L, which converts back to 39.6 mg/dL).
LIMIT_ERRORS = {
    CONF_RANGE_CRITICAL_LOW: "range_critical_low_below_sensor_limit",
    CONF_RANGE_CRITICAL_HIGH: "range_critical_high_above_sensor_limit",
}


def threshold_span_of(hass: HomeAssistant, data: dict[str, Any], entry: ConfigEntry | None = None) -> tuple[int, int]:
    """The span of the very-low and the very-high threshold for the sources in `data` (limits.threshold_span): the
    running GlucoCraft's of `entry` when there is one (it also knows the limits the sources have shown themselves),
    otherwise resolved from the sensor limit settings and each source's integration (limits.resolve)."""
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is not None and hasattr(coordinator, "sensor_span"):
        return coordinator.sensor_span()
    # the primary source is not a setting: read as stored (settings.py)
    primary, secondary = data.get(CONF_GLUCOSE_ENTITY), setting(data, CONF_SECONDARY_ENTITY)
    sources = [(primary, PRIMARY_LIMITS)] + (
        [(secondary, SECONDARY_LIMITS)] if secondary not in (None, primary) else []
    )
    return threshold_span(
        *(resolve(setting(data, lo), setting(data, hi), _source_platform(hass, e)) for e, (lo, hi) in sources if e)
    )


def unsaved_threshold_span(hass: HomeAssistant, data: dict[str, Any], entry: ConfigEntry) -> tuple[int, int]:
    """The span of the very-low and the very-high threshold for sources and sensor limit settings not yet saved (`data`,
    Configure > Glucose sources): judged by the running GlucoCraft of `entry` when there is one (sensor_span(data): it
    also knows each source entity's integration and the limits it has shown itself), otherwise resolved from the
    settings and each source's integration (threshold_span_of without the entry)."""
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is not None and hasattr(coordinator, "sensor_span"):
        return coordinator.sensor_span(data)
    return threshold_span_of(hass, data)


def _sensor_limit_errors(
    ranges: dict[str, Any], base: dict[str, Any] | None, unit: str, span: tuple[float, float]
) -> dict[str, str]:
    """The very-low threshold below the sensors' lower limit and the very-high threshold above their upper limit,
    each with its own error text (strings.json "error"), compared at the precision the form shows. The very-high check
    is a guard the current spans never reach: the very-high box ends at 400 mg/dL, the lowest upper limit
    (settings.sensor_limit_conflicts)."""
    v = {k: float(ranges.get(k, (base or {}).get(k, RANGE_DEFAULTS[k]))) for k in LIMIT_ERRORS}
    errors: dict[str, str] = {}
    if _shown(v[CONF_RANGE_CRITICAL_LOW], unit) < _shown(span[0], unit):
        errors[CONF_RANGE_CRITICAL_LOW] = LIMIT_ERRORS[CONF_RANGE_CRITICAL_LOW]
    if _shown(v[CONF_RANGE_CRITICAL_HIGH], unit) > _shown(span[1], unit):
        errors[CONF_RANGE_CRITICAL_HIGH] = LIMIT_ERRORS[CONF_RANGE_CRITICAL_HIGH]
    return errors


def _at_sensor_limits(ranges: dict[str, Any], span: tuple[float, float]) -> dict[str, Any]:
    """The range limits to store: a threshold that equals the sensor's limit as shown is stored as the limit itself
    (2.2 mmol/L as 40 mg/dL), so that it fits the limit exactly (_sensor_limit_errors has accepted it)."""
    out = dict(ranges)
    if CONF_RANGE_CRITICAL_LOW in out:
        out[CONF_RANGE_CRITICAL_LOW] = max(out[CONF_RANGE_CRITICAL_LOW], span[0])
    if CONF_RANGE_CRITICAL_HIGH in out:
        out[CONF_RANGE_CRITICAL_HIGH] = min(out[CONF_RANGE_CRITICAL_HIGH], span[1])
    return out


def _limit_placeholders(unit: str, span: tuple[float, float]) -> dict[str, str]:
    """The sensors' limits in the display unit, for the error texts of the ranges form."""
    return {"lower_limit": fmt(span[0], unit, with_unit=True), "upper_limit": fmt(span[1], unit, with_unit=True)}


# ---- the goals. The nested goals keep the custom goals within sensible bounds: the time below the low limit
# includes the time below the very-low limit, and the time above the high limit includes the time above the very-high
# limit (the consensus targets are nested in the same way, Battelino et al., 2019), so the goal for the inner band
# cannot be higher than the goal for the outer band. The number boxes keep each goal within its span.
GOAL_PAIRS = ((CONF_GOAL_TBR_CRITICAL, CONF_GOAL_TBR), (CONF_GOAL_TAR_CRITICAL, CONF_GOAL_TAR))


def _goal_errors(goals: dict[str, Any]) -> dict[str, str]:
    """The goals out of bounds, each with its own error text (strings.json "error": "<field>_order")."""
    errors: dict[str, str] = {}
    for inner, outer in GOAL_PAIRS:
        a, b = goals.get(inner), goals.get(outer)
        if a is not None and b is not None and float(a) > float(b):
            errors[inner] = f"{inner}_order"
    return errors


_OPTIONAL_GOALS = (CONF_GOAL_TIME_IN_IDEAL_RANGE, CONF_GOAL_GMI)  # no published target: no default goal


def _stored_goals(data: dict[str, Any]) -> dict[str, Any]:
    """The stored goals as GlucoCraft uses them (settings.setting): each within its span, so that the screen never
    shows a value its number box refuses (a goal stored outside its span is shown as its default)."""
    on = {**data, CONF_CUSTOM_RANGES_AND_GOALS: True}
    return {key: setting(on, key) for key in GOAL_LIMITS}


def _whole(user_input: dict[str, Any]) -> dict[str, Any]:
    """Return the input with whole numbers (number boxes return floats; GMI keeps one decimal), halves away from 0."""
    return {
        k: (round_half_up(float(v), 1) if k == CONF_GOAL_GMI else int(round_half_up(float(v))))
        if isinstance(v, (int, float)) and not isinstance(v, bool)
        else v
        for k, v in user_input.items()
    }


# ---- the display colors
def _palette_from_input(
    user_input: dict[str, Any], keys: dict[str, str], defaults: dict[str, str], reset: str
) -> dict[str, str | None]:
    """The colors of one palette to store: "#RRGGBB" per zone, None for a zone at its default and for every zone when
    the reset box is ticked (a color stored as None is removed on saving, and a missing color is the default, so a
    changed default reaches every zone the user did not change)."""
    if user_input.get(reset):
        return dict.fromkeys(keys.values())
    out: dict[str, str | None] = {}
    for zone, key in keys.items():
        chosen = _rgb_to_hex(user_input.get(key))
        out[key] = None if chosen is None or chosen == defaults[zone].upper() else chosen
    return out


def _colors_from_input(user_input: dict[str, Any], stored: dict[str, Any] | None = None) -> dict[str, str | None]:
    """The colors to store from the flattened input of the Display card and wall panels screen: the zone colors, the
    graph range colors and the two backgrounds (card_background "theme" or "#RRGGBB"; a color picker left empty keeps
    the stored background)."""
    out = {
        **_palette_from_input(user_input, CONF_ZONE_COLORS, ZONE_COLORS, CONF_COLORS_RESET),
        **_palette_from_input(user_input, CONF_BAND_COLORS, BAND_COLORS, CONF_BAND_COLORS_RESET),
    }
    if user_input.get(CONF_CARD_BACKGROUND) == CARD_BACKGROUND_THEME:
        out[CONF_CARD_BACKGROUND] = CARD_BACKGROUND_THEME
    else:
        stored_card = setting(stored, CONF_CARD_BACKGROUND)
        fallback = DEFAULT_CARD_BACKGROUND if stored_card == CARD_BACKGROUND_THEME else stored_card
        out[CONF_CARD_BACKGROUND] = _rgb_to_hex(user_input.get(FIELD_CARD_BACKGROUND_COLOR)) or fallback
    panel = _rgb_to_hex(user_input.get(CONF_PANEL_BACKGROUND))
    out[CONF_PANEL_BACKGROUND] = panel or setting(stored, CONF_PANEL_BACKGROUND)
    return out


def _panel_address_text(data: dict[str, Any]) -> str:
    """The wall-panel address as the settings screen shows it (administrators only): the full address with its id,
    or that none has been created. The id is never put into a notification, which every user can read. The address is
    not a setting: read as stored (settings.py)."""
    webhook_id = data.get(CONF_PANEL_WEBHOOK_ID)
    if not webhook_id:
        return t("config_flow.panel_address.none")
    return t("config_flow.panel_address.set", webhook_id=webhook_id)


# ---- the notifications
def _weekday(value: Any, fallback: int = DEFAULT_NOTIFY_WEEKLY_DAY) -> int:
    """The day of the weekly summary as the stored int (0-6), from the selector's text ("0" to "6",
    flow_schemas.WEEKDAY_OPTIONS); `fallback` for anything else."""
    try:
        day = int(value)
    except (TypeError, ValueError):
        return fallback
    return day if 0 <= day <= 6 and not isinstance(value, bool) else fallback


def _phone_errors(values: dict[str, Any]) -> dict[str, str]:
    """The phone fields' error: phones chosen under *Also send notifications to* with nothing chosen under
    *Notifications to send there* would receive nothing, while the delivery test said that they work."""
    if values.get(CONF_PHONE_TARGETS) and not values.get(CONF_PHONE_TYPES, setting_default(CONF_PHONE_TYPES)):
        return {CONF_PHONE_TYPES: "phone_types_empty"}
    return {}


def _notification_errors(values: dict[str, Any]) -> dict[str, str]:
    """The errors of the notification fields, the same on the last setup screen and on Configure > Notifications: a
    time of the summaries that the time selector cannot have sent, otherwise the phone fields' error
    (_phone_errors)."""
    if not time_of_day_valid(values.get(CONF_NOTIFY_TIME, DEFAULT_NOTIFY_TIME)):
        return {CONF_NOTIFY_TIME: "notify_time_invalid"}
    return _phone_errors(values)


# The Configure menu (options flow), in this order; each screen saves only its own fields. The screens of
# CUSTOM_SCREENS are shown only while Custom ranges and goals is on (options_menu).
OPTIONS_MENU = ("sources", "units", "features", "ranges", "goals", "displays", "notifications", "report")
CUSTOM_SCREENS = ("ranges", "goals")


def options_menu(data: dict[str, Any]) -> list[str]:
    """The Configure menu for the stored settings `data`: OPTIONS_MENU without CUSTOM_SCREENS while Custom ranges and
    goals is off."""
    return [step for step in OPTIONS_MENU if step not in CUSTOM_SCREENS or custom_ranges_on(data)]


class GlucoCraftOptionsFlow(OptionsFlow):
    """Handle GlucoCraft options (reconfigure after setup)."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize."""
        self._entry = config_entry
        self._updates: dict[str, Any] = {}  # the changes the screen saves (_async_finish)
        self._range_base: dict[str, Any] | None = None  # the ranges form after an order error
        # The sensors' limits of the Glucose sources screen's input while the Glucose ranges screen follows it (sensor
        # first, then the ranges: async_step_sources); None when the ranges screen is opened from the menu
        self._ranges_span: tuple[int, int] | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the first screen: a menu of the screens by topic, each of which saves only its own fields.

        A menu shows every screen at once, where a chain of screens would give no sign of the later ones.
        """
        await async_setup_language(self.hass)  # the texts this flow writes itself (i18n.py)
        self._updates = {}
        self._range_base = None
        self._ranges_span = None
        return self.async_show_menu(step_id="init", menu_options=options_menu(dict(self._entry.data)))

    async def async_step_sources(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Glucose sources. The primary source can be changed here; the stored readings are kept (they belong to the
        entry, not to the source entity).

        Sensor first, then the ranges: when the sources or sensor limit settings entered change the sensors' limits
        (limits.threshold_span) so that the stored custom ranges no longer fit them (settings.sensor_limit_conflicts;
        only while Custom ranges and goals is on), nothing is saved yet and the Glucose ranges screen follows, with
        the field errors on the limits that do not fit; the sources and the ranges are saved together once the ranges
        fit (async_step_ranges). Closing the screen saves neither."""
        errors: dict[str, str] = {}
        values = _flatten(user_input or {})
        if user_input is not None:
            errors = _section_errors(_source_errors(self.hass, values))
            if not errors:
                self._updates = dict(values)
                if CONF_STALE_THRESHOLD in values:
                    self._updates[CONF_STALE_THRESHOLD] = _stale_minutes(values)
                # A cleared selector is simply missing from user_input: store None so "no entity" sticks
                for key in (CONF_SENSOR_EXPIRY_ENTITY, CONF_SECONDARY_ENTITY):
                    self._updates.setdefault(key, None)
                self._updates.setdefault(CONF_PRIMARY_TIME_ATTR, "")
                self._updates.setdefault(CONF_SECONDARY_TIME_ATTR, "")
                span = self._ranges_misfit({**self._entry.data, **self._updates})
                if span is not None:
                    self._ranges_span, self._range_base = span, None
                    return await self.async_step_ranges()
                return await self._async_finish()

        current = {**self._entry.data, **values}  # after an error: the values just entered
        return self.async_show_form(
            step_id="sources",
            data_schema=_sources_schema(self.hass, current, setup=False, second_open=SECTION_SECOND_SOURCE in errors),
            errors=errors,
        )

    def _ranges_misfit(self, new_data: dict[str, Any]) -> tuple[int, int] | None:
        """The sensors' limits of `new_data` (unsaved_threshold_span) when they differ from the limits in use and the
        stored custom ranges do not fit them (settings.sensor_limit_conflicts), else None. None while Custom ranges and
        goals is off: the default ranges are then in use, and they fit every sensor's limits. A threshold that already
        did not fit before (its Repairs notice is open) does not hold up a change that leaves the limits as they are."""
        if not custom_ranges_on(new_data):
            return None
        span = unsaved_threshold_span(self.hass, new_data, self._entry)
        if span == threshold_span_of(self.hass, dict(self._entry.data), self._entry):
            return None
        return span if sensor_limit_conflicts(new_data, span) else None

    async def async_step_units(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Units and formats (saved on their own). A new display unit is applied to every glucose sensor at once."""
        if user_input is not None:
            self._updates = dict(user_input)
            return await self._async_finish()
        return self.async_show_form(step_id="units", data_schema=_units_schema(dict(self._entry.data)))

    async def async_step_features(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Optional features (saved on their own): Custom ranges and goals, and Separate statistics entities.

        Turning a feature off removes its entities at the reload and keeps the stored ranges and goals and the recorded
        history; turning it on again creates the same entities, whose history continues.
        """
        if user_input is not None:
            self._updates = {key: user_input.get(key) is True for key in FEATURE_SWITCHES}
            return await self._async_finish()
        return self.async_show_form(step_id="features", data_schema=_features_schema(dict(self._entry.data)))

    async def async_step_ranges(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Glucose ranges (saved on their own), in the display unit.

        After the Glucose sources screen (async_step_sources: the ranges no longer fit the sensors' limits entered
        there), the form is checked against those limits, opens with the field errors on the stored limits that do not
        fit, and saves the sources' and the ranges' changes together once the ranges fit."""
        data = dict(self._entry.data)
        unit = setting(data, CONF_UNIT)
        # what the form shows: the stored values, or after an error the values just entered. The stored values are
        # read validated (settings.stored_ranges): a hand-edited entry can hold a range of the wrong type (None, a
        # list), which the form's number boxes and the order check could not use
        base = self._range_base or {**data, **stored_ranges(data)}
        errors: dict[str, str] = {}
        span = self._ranges_span or threshold_span_of(self.hass, data, self._entry)
        if user_input is None and self._ranges_span is not None:
            errors = _sensor_limit_errors({}, base, unit, span)  # why the screen follows the sources screen
        if user_input is not None:
            ranges = _ranges_to_mgdl(user_input, unit, base)
            errors = {**_range_errors(ranges, base), **_sensor_limit_errors(ranges, base, unit, span)}  # per field
            if not errors:
                self._updates.update(_at_sensor_limits(ranges, span))
                return await self._async_finish()
            base = self._range_base = {**base, **ranges}

        return self.async_show_form(
            step_id="ranges",
            data_schema=_ranges_schema(base, unit),
            errors=errors,
            description_placeholders={
                **_limit_placeholders(unit, span),
                # after the Glucose sources screen: why the ranges screen opened, as its first paragraph
                "source_note": (
                    t("config_flow.ranges.source_changed") + "\n\n" if self._ranges_span is not None else ""
                ),
            },
        )

    async def async_step_goals(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Custom goals (saved on their own)."""
        stored = _stored_goals(self._entry.data)
        goals_shown = stored
        errors: dict[str, str] = {}
        if user_input is not None:
            goals = _whole(user_input)
            errors = _goal_errors({**stored, **goals})
            if not errors:
                self._updates.update(goals)
                for k in _OPTIONAL_GOALS:  # a cleared box (key absent) = no goal
                    self._updates[k] = goals.get(k)
                return await self._async_finish()
            goals_shown = {**stored, **goals}  # the form shows the values just entered

        return self.async_show_form(
            step_id="goals",
            data_schema=_goals_schema(goals_shown),
            errors=errors,
        )

    async def async_step_displays(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Display card and wall panels: the card layout, how the trend is shown, the zone colors, the graph range
        colors, the backgrounds, Outline text, Fade the words when there is no current reading, the range set of the
        weekly pages (only while it can differ) and a new wall-panel address. The description shows the contrast of the
        saved colors (contrast.contrast_note), which changes no color."""
        data = dict(self._entry.data)
        if user_input is not None:
            values = _flatten(user_input)
            self._updates = _colors_from_input(values, data)
            self._updates[CONF_TREND_DISPLAY] = setting(values, CONF_TREND_DISPLAY)
            self._updates[CONF_TEXT_OUTLINE] = values.get(CONF_TEXT_OUTLINE) is True
            self._updates[CONF_FLASH_NO_CURRENT_DATA] = values.get(CONF_FLASH_NO_CURRENT_DATA) is not False
            self._updates[CONF_DISPLAY_CARD] = values.get(CONF_DISPLAY_CARD, setting(data, CONF_DISPLAY_CARD))
            if CONF_WEEKLY_PAGES in values and _weekly_pages_offered(data):
                self._updates[CONF_WEEKLY_PAGES] = values[CONF_WEEKLY_PAGES]
            if values.get(CONF_REGENERATE_PANEL_WEBHOOK):
                self._updates[CONF_REGENERATE_PANEL_WEBHOOK] = True
            return await self._async_finish()
        zones, bands = zone_colors(data), band_colors(data)
        return self.async_show_form(
            step_id="displays",
            data_schema=_displays_schema(data),
            description_placeholders={
                "contrast_note": contrast_note(
                    zones, bands, setting(data, CONF_CARD_BACKGROUND), setting(data, CONF_PANEL_BACKGROUND)
                )
                + shared_colors_note(zones, bands),
                "panel_address": _panel_address_text(data),
            },
        )

    async def async_step_notifications(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Notifications: the daily and the weekly summary, the no-data notice and its delay, and the phones (the
        screen has no section, so its input is used as it arrives)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _notification_errors(user_input)
            if not errors:
                self._updates = dict(user_input)
                if CONF_NOTIFY_WEEKLY_DAY in self._updates:  # stored as an int, 0 = Monday to 6 = Sunday
                    self._updates[CONF_NOTIFY_WEEKLY_DAY] = _weekday(
                        self._updates[CONF_NOTIFY_WEEKLY_DAY], setting(self._entry.data, CONF_NOTIFY_WEEKLY_DAY)
                    )
                if CONF_NO_DATA_NOTICE_DELAY in self._updates:  # stored as an int, minutes
                    self._updates[CONF_NO_DATA_NOTICE_DELAY] = setting(self._updates, CONF_NO_DATA_NOTICE_DELAY)
                return await self._async_finish()
        return self.async_show_form(
            step_id="notifications", data_schema=_notifications_schema(self.hass, dict(self._entry.data)), errors=errors
        )

    async def async_step_report(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Report and dashboard: the PDF paper size, the report printer, the dashboard in the sidebar and what deleting
        GlucoCraft keeps."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not printer_address_valid(user_input.get(CONF_PRINTER)):
                errors[CONF_PRINTER] = "printer_invalid"
            else:
                self._updates = dict(user_input)
                self._updates[CONF_PRINTER] = str(user_input.get(CONF_PRINTER) or "").strip()
                return await self._async_finish()

        current = {**self._entry.data, **(user_input or {})}  # after an error: the values just entered
        return self.async_show_form(step_id="report", data_schema=_report_schema(current), errors=errors)

    async def _async_finish(self) -> ConfigFlowResult:
        """Store the options in the entry's data and reload GlucoCraft.

        The settings live in the entry's data, not in its options (the module docstring says why). So this writes the
        data and reloads the entry here; the empty options entry it returns only closes the flow.
        """
        new_data = {**self._entry.data, **self._updates}
        for k in (*CONF_ZONE_COLORS.values(), *CONF_BAND_COLORS.values()):  # a zone without a color of its own
            if new_data.get(k) is None:  # uses its palette's default
                new_data.pop(k, None)
        if new_data.pop(CONF_REGENERATE_PANEL_WEBHOOK, False):
            # a new id at once, replacing the earlier one if there is one (its notification says which); the reload
            # registers it, and the settings screen shows it
            replaced = bool(new_data.get(CONF_PANEL_WEBHOOK_ID))
            new_data[CONF_PANEL_WEBHOOK_ID] = new_webhook_id(self.hass, replaced=replaced)
            new_data[CONF_PANEL_FEED] = True
        if new_data.pop(CONF_PHONE_TEST, False):
            self.hass.async_create_task(_send_phone_test(self.hass, new_data.get(CONF_PHONE_TARGETS, [])))
        # only the keys GlucoCraft knows (settings.ENTRY_KEYS): any other stored key is not written back
        new_data = known_entry_data(new_data)
        self.hass.config_entries.async_update_entry(self._entry, data=new_data)
        if CONF_UNIT in self._updates:  # the Display unit setting: every glucose sensor's entity settings at once
            apply_display_unit(self.hass, self._entry.entry_id, new_data[CONF_UNIT])
        await self.hass.config_entries.async_reload(self._entry.entry_id)
        return self.async_create_entry(title="", data={})


class GlucoCraftConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for GlucoCraft."""

    # The entry format (const.ENTRY_VERSION / ENTRY_MINOR_VERSION; __init__.async_migrate_entry)
    VERSION = ENTRY_VERSION
    MINOR_VERSION = ENTRY_MINOR_VERSION

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> GlucoCraftOptionsFlow:
        """Get the options flow."""
        return GlucoCraftOptionsFlow(config_entry)

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}
        self._kept: tuple[int, int, int] | None = None  # readings kept by an earlier installation (kept.kept_summary)
        self._kept_choice = KEPT_KEEP
        self._unit_read = True  # the source's unit was read (async_step_source); else the finish screen says so

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the first step of the setup: the disclaimer (const.DISCLAIMER) as plain text, with no field; Submit
        continues. Nothing is stored on this step, and the entry is created only at the end of the setup.
        """
        # One GlucoCraft installation in each Home Assistant (manifest.json single_config_entry; Home Assistant refuses
        # a second flow itself, this is the same rule for a flow started in another way)
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            # readings kept by an earlier installation that was deleted: keep them or start fresh
            self._kept = await self.hass.async_add_executor_job(kept_summary, self.hass.config.path(DATA_FOLDER))
            if self._kept:
                return await self.async_step_kept()
            return await self.async_step_source()
        return self.async_show_form(
            step_id="user", data_schema=_disclaimer_schema(), description_placeholders={"disclaimer": DISCLAIMER}
        )

    async def async_step_kept(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Readings kept by an earlier installation (kept.py): keep them (the default) or delete them and start fresh.

        The choice takes effect only when the setup ends (async_step_finish): kept data are adopted at the first
        start, or deleted then; a setup that is not finished changes nothing.
        """
        if user_input is not None:
            self._kept_choice = user_input.get(FIELD_KEPT_READINGS, KEPT_KEEP)
            return await self.async_step_source()
        count, first, last = self._kept
        rg = regional_from_settings({}, self.hass.config.country, self.hass.config.language)
        tz = dt_util.get_default_time_zone()

        def day(ts: int) -> str:
            """Return the local date of an epoch time in the numeric form of the regional settings, with the year."""
            return rg.numeric(datetime.fromtimestamp(ts, tz), year=True)

        return self.async_show_form(
            step_id="kept",
            data_schema=_kept_schema(),
            description_placeholders={"count": str(count), "first": day(first), "last": day(last)},
        )

    async def async_step_source(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The glucose sources, the optional entities and No current data after. GlucoCraft's own entities are
        neither offered nor accepted."""
        errors: dict[str, str] = {}
        values = _flatten(user_input or {})

        if user_input is not None:
            errors = _section_errors(_source_errors(self.hass, values))
            if not errors:
                entity_id = values[CONF_GLUCOSE_ENTITY]
                self._data[CONF_GLUCOSE_ENTITY] = entity_id
                self._data[CONF_SENSOR_EXPIRY_ENTITY] = values.get(CONF_SENSOR_EXPIRY_ENTITY)
                self._data[CONF_SECONDARY_ENTITY] = values.get(CONF_SECONDARY_ENTITY)
                for k in LIMIT_SETTINGS:  # the sensor's measurement range per source
                    self._data[k] = str(values.get(k) or LIMIT_AUTO)
                self._data[CONF_STALE_THRESHOLD] = _stale_minutes(values)
                # the display unit starts as the source's own unit; changeable on the last setup screen, which says
                # so when the unit could not be read (no state, or no glucose unit): mg/dL is then only a start
                st = self.hass.states.get(entity_id)
                unit = unit_from_attribute(st.attributes.get("unit_of_measurement")) if st else None
                self._unit_read = unit is not None
                self._data[CONF_UNIT] = unit or UNIT_MGDL
                return await self.async_step_finish()

        return self.async_show_form(
            step_id="source",
            data_schema=_sources_schema(self.hass, values, setup=True, second_open=SECTION_SECOND_SOURCE in errors),
            errors=errors,
        )

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The last setup screen, Units and Notifications: the display unit, the daily summary, the dashboard in the
        sidebar and the phones. Every other setting starts at its default and is changed in Configure."""
        await async_setup_language(self.hass)  # the texts this flow writes itself (i18n.py)
        errors = _notification_errors(user_input) if user_input is not None else {}
        if user_input is not None and not errors:
            self._data[CONF_UNIT] = user_input.get(CONF_UNIT, self._data.get(CONF_UNIT, UNIT_MGDL))
            self._data[CONF_NOTIFY_ENABLED] = user_input.get(CONF_NOTIFY_ENABLED, setting_default(CONF_NOTIFY_ENABLED))
            self._data[CONF_NOTIFY_TIME] = user_input.get(CONF_NOTIFY_TIME, DEFAULT_NOTIFY_TIME)
            self._data[CONF_SHOW_DASHBOARD] = user_input.get(CONF_SHOW_DASHBOARD, setting_default(CONF_SHOW_DASHBOARD))
            # the international settings start "auto" (Home Assistant's country; changeable in Configure)
            for k in (CONF_TIME_FORMAT, CONF_DATE_FORMAT, *REGIONAL_OPTIONS):
                self._data.setdefault(k, setting_default(k))
            # the trend in the TREND column: the arrow (changeable in Configure > Display card and wall panels)
            self._data.setdefault(CONF_TREND_DISPLAY, TREND_DISPLAY_ARROW)
            # the optional features: stored explicitly, off (the consensus ranges and targets, no separate statistics
            # entities); turned on in Configure > Optional features, where the ranges and goals are then set
            for k in FEATURE_SWITCHES:
                self._data[k] = False
            self._data[CONF_PHONE_TARGETS] = user_input.get(CONF_PHONE_TARGETS, setting_default(CONF_PHONE_TARGETS))
            self._data[CONF_PHONE_TYPES] = user_input.get(CONF_PHONE_TYPES, setting_default(CONF_PHONE_TYPES))
            if user_input.get(CONF_PHONE_TEST):
                self.hass.async_create_task(_send_phone_test(self.hass, list(self._data[CONF_PHONE_TARGETS])))
            # kept readings the user chose to delete: deleted now, when the setup ends (kept ones are adopted at the
            # first start, __init__._async_adopt_kept)
            if self._kept_choice == KEPT_DELETE:
                await self.hass.async_add_executor_job(discard_kept, self.hass.config.path(DATA_FOLDER))
            if self._kept:  # the notification of the removal that kept them has served its purpose
                persistent_notification.async_dismiss(self.hass, KEPT_NOTICE_ID)

            return self.async_create_entry(
                title="GlucoCraft",
                data=self._data,
            )

        return self.async_show_form(
            step_id="finish",
            data_schema=_finish_schema(self.hass, self._data.get(CONF_UNIT, UNIT_MGDL)),
            errors=errors,
            # the unit of the source could not be read: the finish screen says that mg/dL is only a start
            description_placeholders={"unit_note": "" if self._unit_read else t("config_flow.unit_not_read")},
        )
