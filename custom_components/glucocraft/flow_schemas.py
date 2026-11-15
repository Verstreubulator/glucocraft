# License: AGPL-3.0-only (LICENSE).
"""The forms of the settings screens (config_flow.py): one schema per screen and the field helpers they share.

Each schema shows the stored settings as GlucoCraft uses them (settings.setting, settings.stored_ranges), so that a
value of the wrong type or outside its span, from a hand-edited entry, is shown as its default and never stops a
screen from opening or a screen saved unchanged from being accepted. After a field error a screen shows the values
just entered instead (the step passes them in `current`). The free texts (the measurement-time attributes, the
printer address) are shown as stored or entered (_text_shown). The keys that are not settings (the primary source)
are shown as stored.

The field labels, explanations and option texts are in strings.json ("data", "data_description", "selector"); a field
of a collapsed section arrives nested under the section's key and is flattened before it is checked (_flatten), so
that every setting keeps its stored key.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
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
    CONF_PAPER_SIZE,
    CONF_PHONE_TARGETS,
    CONF_PHONE_TEST,
    CONF_PHONE_TYPES,
    CONF_PRIMARY_TIME_ATTR,
    CONF_PRINTER,
    CONF_REGENERATE_PANEL_WEBHOOK,
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
    DISPLAY_CARD_RECTANGULAR,
    DISPLAY_CARD_SQUARE,
    NO_DATA_NOTICE_DELAYS,
    ON_REMOVAL_OPTIONS,
    PHONE_TYPES,
    SENSOR_LOWER_LIMIT_OPTIONS,
    SENSOR_UPPER_LIMIT_OPTIONS,
    TREND_DISPLAY_OPTIONS,
    WEEKLY_PAGES_CONSENSUS,
    WEEKLY_PAGES_PERSONAL,
    valid_hex_color,
    zone_colors,
)
from .flow_helpers import glucocraft_entity_ids
from .i18n import t
from .limits import mmol_limit
from .settings import (
    DEFAULT_NOTIFY_TIME,
    GOAL_LIMITS,
    RANGE_DEFAULTS,
    RANGE_LIMITS,
    RANGE_ORDER,
    REGIONAL_OPTIONS,
    STALE_THRESHOLD_LIMITS,
    TIME_FORMAT_OPTIONS,
    UNIT_OPTIONS,
    custom_ranges_on,
    setting,
    setting_default,
    stored_custom_values,
)
from .units import UNIT_MGDL, UNIT_MMOLL, from_mgdl, round_half_up


# ---- the field helpers
def _regional_schema(current: dict[str, Any], keys: tuple[str, ...]) -> dict:
    """Return the form fields of the regional settings `keys`, in that order (each "auto" by default; texts in
    strings.json)."""
    return {
        vol.Optional(k, default=setting(current, k)): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=REGIONAL_OPTIONS[k], translation_key=k, mode=selector.SelectSelectorMode.DROPDOWN
            )
        )
        for k in keys
    }


def _choice(key: str, options: list[str], default: str) -> dict:
    """A dropdown of `options` with the option texts of strings.json "selector" `key`."""
    return {
        vol.Optional(key, default=default): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=options, translation_key=key, mode=selector.SelectSelectorMode.DROPDOWN
            )
        )
    }


def _text_shown(current: dict[str, Any], key: str) -> str:
    """A free text field's value as the form shows it: the text as stored or just entered, surrounding spaces
    included, so that a screen saved unchanged stores the same text; the setting's default for a value that is not a
    text."""
    value = current.get(key)
    return value if isinstance(value, str) else setting(current, key)


def _box(lo: float, hi: float, unit: str, step: float = 1) -> selector.NumberSelector:
    """Return a number box that shows its value and unit (sliders in the settings dialog show no number)."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=lo, max=hi, step=step, mode=selector.NumberSelectorMode.BOX, unit_of_measurement=unit
        )
    )


# The unit symbol of the No current data after box (whole minutes)
UNIT_MINUTES = "min"


# The sensor's measurement range per source (limits.py): "Automatic" first, then the values in mg/dL
# (the option texts in strings.json "selector" give both units). The secondary pair uses the same option texts.
LIMIT_SETTINGS = {
    CONF_SENSOR_LOWER_LIMIT: SENSOR_LOWER_LIMIT_OPTIONS,
    CONF_SENSOR_UPPER_LIMIT: SENSOR_UPPER_LIMIT_OPTIONS,
    CONF_SECONDARY_SENSOR_LOWER_LIMIT: SENSOR_LOWER_LIMIT_OPTIONS,
    CONF_SECONDARY_SENSOR_UPPER_LIMIT: SENSOR_UPPER_LIMIT_OPTIONS,
}
_LIMIT_TRANSLATION = {
    CONF_SECONDARY_SENSOR_LOWER_LIMIT: CONF_SENSOR_LOWER_LIMIT,
    CONF_SECONDARY_SENSOR_UPPER_LIMIT: CONF_SENSOR_UPPER_LIMIT,
}
PRIMARY_LIMITS = (CONF_SENSOR_LOWER_LIMIT, CONF_SENSOR_UPPER_LIMIT)
SECONDARY_LIMITS = (CONF_SECONDARY_SENSOR_LOWER_LIMIT, CONF_SECONDARY_SENSOR_UPPER_LIMIT)


def _limit_schema(current: dict[str, Any], keys: tuple[str, ...]) -> dict:
    """Return the form fields of the sensor limit settings `keys` (each "auto" by default)."""
    return {
        vol.Optional(k, default=setting(current, k)): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=LIMIT_SETTINGS[k],
                translation_key=_LIMIT_TRANSLATION.get(k, k),
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
        for k in keys
    }


def _source_selector(hass: HomeAssistant) -> selector.EntitySelector:
    """A sensor selector without GlucoCraft's own entities (flow_helpers.glucocraft_entity_ids)."""
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor", exclude_entities=glucocraft_entity_ids(hass))
    )


# ---- the collapsed sections (homeassistant.data_entry_flow.section). A section's fields arrive nested under the
# section's key; _flatten puts them back at the top level, so the stored keys never change. A selector cleared inside a
# section is missing from the nested input, as at the top level, and is stored as None by the step.
SECTION_SECOND_SOURCE = "second_source"
SECTION_GRAPH_COLORS = "graph_colors"
SECTIONS = (SECTION_SECOND_SOURCE, SECTION_GRAPH_COLORS)
SECOND_SOURCE_FIELDS = (CONF_SECONDARY_ENTITY, *SECONDARY_LIMITS, CONF_SECONDARY_TIME_ATTR)


def _section(fields: dict, collapsed: bool = True) -> section:
    """A section of the form with `fields`, collapsed unless stated otherwise."""
    return section(vol.Schema(fields), {"collapsed": collapsed})


def _flatten(user_input: dict[str, Any]) -> dict[str, Any]:
    """The input with the fields of every section at the top level and without the section keys."""
    out: dict[str, Any] = {}
    for key, value in user_input.items():
        if key in SECTIONS:
            out.update(value if isinstance(value, dict) else {})
        else:
            out[key] = value
    return out


def _section_errors(errors: dict[str, str]) -> dict[str, str]:
    """The errors of the fields inside the Second glucose source section under the section's key: the form shows an
    error of a section above that section, which the step then shows open."""
    return {(SECTION_SECOND_SOURCE if k in SECOND_SOURCE_FIELDS else k): v for k, v in errors.items()}


# ---- the display colors (stored as "#RRGGBB"; the color picker works in RGB): the seven zone colors with their reset
# box, the seven graph range colors with theirs (in the collapsed section Graph range colors), the card background and
# the wall-panel background
CONF_COLORS_RESET = "colors_reset"
CONF_BAND_COLORS_RESET = "band_colors_reset"
# The card background is "theme" or a color. A Home Assistant form cannot show or hide a field by another field's
# value, so the choice is a list of two options, "Home Assistant theme" and "The color below", followed by a color
# picker that is used only with the second; one color picker alone could not express "the theme", and a text field
# for "theme" or "#RRGGBB" would ask the user to type a code. The picker's field is not stored: the step stores the
# chosen color, or "theme", as card_background.
CARD_BACKGROUND_COLOR = "color"
CARD_BACKGROUND_OPTIONS = [CARD_BACKGROUND_THEME, CARD_BACKGROUND_COLOR]
FIELD_CARD_BACKGROUND_COLOR = "card_background_color"


def _hex_to_rgb(value: str) -> list[int]:
    return [int(value[i : i + 2], 16) for i in (1, 3, 5)]


def _rgb_to_hex(value: Any) -> str | None:
    """[r, g, b] from the color picker as "#RRGGBB"; None for anything else."""
    if (
        isinstance(value, (list, tuple))
        and len(value) == 3
        and all(isinstance(c, (int, float)) and not isinstance(c, bool) and 0 <= c <= 255 for c in value)
    ):
        return "#{:02X}{:02X}{:02X}".format(*(round(c) for c in value))
    if valid_hex_color(value):
        return str(value).upper()
    return None


def _picker(color: str) -> tuple[list[int], selector.ColorRGBSelector]:
    """A color picker's default (the color as [r, g, b]) and the picker."""
    return _hex_to_rgb(color), selector.ColorRGBSelector()


def _weekly_pages_offered(d: dict[str, Any]) -> bool:
    """True when the weekly pages' range set is a choice: Custom ranges and goals is on and the stored ranges or goals
    differ from the consensus defaults (otherwise both range sets are the consensus one)."""
    return custom_ranges_on(d) and stored_custom_values(d)


# ---- the screens of the setup
def _disclaimer_schema() -> vol.Schema:
    """The first setup screen: no field (the disclaimer is its description; Submit continues)."""
    return vol.Schema({})


# The setup's question about readings kept by an earlier installation (kept.py): the field and its two answers (not
# stored; config_flow.async_step_kept)
FIELD_KEPT_READINGS = "kept_readings"
KEPT_KEEP, KEPT_DELETE = "keep", "delete"


def _kept_schema() -> vol.Schema:
    """Readings kept by an earlier installation: keep them (the default) or delete them."""
    return vol.Schema(
        {
            vol.Required(FIELD_KEPT_READINGS, default=KEPT_KEEP): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[KEPT_KEEP, KEPT_DELETE],
                    translation_key=FIELD_KEPT_READINGS,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        }
    )


def _finish_schema(hass: HomeAssistant, unit: str) -> vol.Schema:
    """The last setup screen: the display unit (`unit`: the source's own unit, or mg/dL), the daily summary, the
    dashboard in the sidebar and the phones, each at the setting's default."""
    return vol.Schema(
        {
            vol.Optional(CONF_UNIT, default=unit): vol.In(list(UNIT_OPTIONS)),
            vol.Optional(CONF_NOTIFY_ENABLED, default=setting_default(CONF_NOTIFY_ENABLED)): bool,
            vol.Optional(CONF_NOTIFY_TIME, default=DEFAULT_NOTIFY_TIME): selector.TimeSelector(),
            vol.Optional(CONF_SHOW_DASHBOARD, default=setting_default(CONF_SHOW_DASHBOARD)): bool,
            **_phone_schema(hass, {}),
        }
    )


# ---- the screens of Configure (and the sources screen of the setup)
def _sources_schema(
    hass: HomeAssistant, current: dict[str, Any], *, setup: bool, second_open: bool = False
) -> vol.Schema:
    """Glucose sources: the primary source, the CGM sensor expiry entity, its sensor lower and upper limit and
    (Configure only) its measurement-time attribute; the collapsed section Second glucose source;
    No current data after. GlucoCraft's own entities are not offered. `current`: the stored values, or after an error
    the values just entered."""
    src = _source_selector(hass)

    def suggested(value: Any) -> dict:
        """Return the field description that suggests `value`."""
        return {"suggested_value": value}

    # the primary source is not a setting: shown as stored (settings.py)
    if setup:
        glucose = vol.Required(CONF_GLUCOSE_ENTITY, description=suggested(current.get(CONF_GLUCOSE_ENTITY)))
    else:
        glucose = vol.Required(CONF_GLUCOSE_ENTITY, default=current.get(CONF_GLUCOSE_ENTITY))
    fields: dict = {
        glucose: src,
        vol.Optional(
            CONF_SENSOR_EXPIRY_ENTITY, description=suggested(setting(current, CONF_SENSOR_EXPIRY_ENTITY))
        ): src,
        **_limit_schema(current, PRIMARY_LIMITS),
    }
    second: dict = {
        vol.Optional(CONF_SECONDARY_ENTITY, description=suggested(setting(current, CONF_SECONDARY_ENTITY))): src,
        **_limit_schema(current, SECONDARY_LIMITS),
    }
    if not setup:  # the measurement-time attributes: Configure only (blank = detected automatically)
        fields[
            vol.Optional(CONF_PRIMARY_TIME_ATTR, description=suggested(_text_shown(current, CONF_PRIMARY_TIME_ATTR)))
        ] = selector.TextSelector()
        second[
            vol.Optional(
                CONF_SECONDARY_TIME_ATTR, description=suggested(_text_shown(current, CONF_SECONDARY_TIME_ATTR))
            )
        ] = selector.TextSelector()
    fields[vol.Required(SECTION_SECOND_SOURCE)] = _section(second, collapsed=not second_open)
    # a number box with its unit, so that the screen shows that the value is in minutes; stored as whole minutes
    # (config_flow._stale_minutes)
    fields[vol.Optional(CONF_STALE_THRESHOLD, default=setting(current, CONF_STALE_THRESHOLD))] = _box(
        *STALE_THRESHOLD_LIMITS, UNIT_MINUTES
    )
    return vol.Schema(fields)


def _units_schema(current: dict[str, Any]) -> vol.Schema:
    """Units and formats: display unit, A1C and GMI unit, time format, date format, first day of the week and decimal
    separator (each "auto" by default, except the display unit)."""
    return vol.Schema(
        {
            vol.Optional(CONF_UNIT, default=setting(current, CONF_UNIT)): vol.In(list(UNIT_OPTIONS)),
            **_regional_schema(current, (CONF_A1C_UNIT,)),
            **_choice(CONF_TIME_FORMAT, TIME_FORMAT_OPTIONS, setting(current, CONF_TIME_FORMAT)),
            **_choice(CONF_DATE_FORMAT, list(DATE_FORMAT_OPTIONS), setting(current, CONF_DATE_FORMAT)),
            **_regional_schema(current, (CONF_FIRST_WEEKDAY, CONF_DECIMAL_SEPARATOR)),
        }
    )


# The two optional features (Configure > Optional features), each a switch that is off in a new installation. Off,
# Custom ranges and goals uses the consensus ranges and targets everywhere and keeps the stored ranges and goals;
# Separate statistics entities adds one entity per range-independent statistic and period. The entities of a feature
# that is turned off are not created and are removed from the entity registry at the reload (stale_entities.py); they
# come back with the same entity ids.
FEATURE_SWITCHES = (CONF_CUSTOM_RANGES_AND_GOALS, CONF_SEPARATE_STATISTICS)


def _features_schema(current: dict[str, Any]) -> vol.Schema:
    """Optional features: one switch per feature (FEATURE_SWITCHES)."""
    return vol.Schema({vol.Optional(key, default=setting(current, key)): bool for key in FEATURE_SWITCHES})


def _shown(mg: float, unit: str) -> float:
    """A value (mg/dL) as the ranges form shows it: mmol/L one decimal (consensus values as published), mg/dL whole."""
    return from_mgdl(mg, unit) if unit == UNIT_MMOLL else int(round_half_up(float(mg)))


def _range_shown(d: dict[str, Any] | None, k: str, unit: str) -> float:
    """A limit (mg/dL) of `d` (the validated stored ranges, or the values just entered) as the form shows it
    (_shown)."""
    return _shown((d or {}).get(k, RANGE_DEFAULTS[k]), unit)


def _ranges_schema(defaults: dict[str, Any] | None = None, unit: str = UNIT_MGDL) -> vol.Schema:
    """Return the ranges form, in ascending order, in the display unit (the values are stored in mg/dL)."""
    mm = unit == UNIT_MMOLL

    def box(k: str):
        """The number box of one range limit, with its span in the display unit (mmol/L with one decimal)."""
        lo, hi = RANGE_LIMITS[k]
        return _box(
            mmol_limit(lo) if mm else lo,
            mmol_limit(hi) if mm else hi,
            UNIT_MMOLL if mm else UNIT_MGDL,
            0.1 if mm else 1,
        )

    return vol.Schema({vol.Optional(k, default=_range_shown(defaults, k, unit)): box(k) for k in RANGE_ORDER})


def _goals_schema(goals: dict[str, Any]) -> vol.Schema:
    """The goals, in percent: `goals` holds every goal (config_flow._stored_goals, or the values just entered)."""
    return vol.Schema(
        {
            vol.Optional(CONF_GOAL_TIME_IN_TARGET_RANGE, default=goals[CONF_GOAL_TIME_IN_TARGET_RANGE]): _box(
                *GOAL_LIMITS[CONF_GOAL_TIME_IN_TARGET_RANGE], "%"
            ),
            # no published target: empty unless the user enters a goal; a cleared box = no goal
            vol.Optional(
                CONF_GOAL_TIME_IN_IDEAL_RANGE, description={"suggested_value": goals.get(CONF_GOAL_TIME_IN_IDEAL_RANGE)}
            ): _box(*GOAL_LIMITS[CONF_GOAL_TIME_IN_IDEAL_RANGE], "%"),
            vol.Optional(CONF_GOAL_TBR, default=goals[CONF_GOAL_TBR]): _box(*GOAL_LIMITS[CONF_GOAL_TBR], "%"),
            vol.Optional(CONF_GOAL_TBR_CRITICAL, default=goals[CONF_GOAL_TBR_CRITICAL]): _box(
                *GOAL_LIMITS[CONF_GOAL_TBR_CRITICAL], "%"
            ),
            vol.Optional(CONF_GOAL_TAR, default=goals[CONF_GOAL_TAR]): _box(*GOAL_LIMITS[CONF_GOAL_TAR], "%"),
            vol.Optional(CONF_GOAL_TAR_CRITICAL, default=goals[CONF_GOAL_TAR_CRITICAL]): _box(
                *GOAL_LIMITS[CONF_GOAL_TAR_CRITICAL], "%"
            ),
            vol.Optional(CONF_GOAL_CV, default=goals[CONF_GOAL_CV]): _box(*GOAL_LIMITS[CONF_GOAL_CV], "%"),
            vol.Optional(CONF_GOAL_GMI, description={"suggested_value": goals.get(CONF_GOAL_GMI)}): _box(
                *GOAL_LIMITS[CONF_GOAL_GMI], "%", 0.1
            ),
        }
    )


def _displays_schema(current: dict[str, Any]) -> vol.Schema:
    """Display card and wall panels: the card layout and how the trend is shown; one color picker per zone,
    set to the color in use, and a box that restores the default palette; the collapsed section Graph range colors
    (one picker per graph band and a box that restores the default graph colors); the display card background (the
    Home Assistant theme or a color) and the wall-panel background; Outline text; Fade the words when there is no
    current reading; the range set of the weekly pages,
    only while it can differ (_weekly_pages_offered); the box that creates a new wall-panel address. Every color is
    shown as the validated setting in use (settings.setting), so that a stored value that is not valid shows its
    default."""
    fields: dict = {
        vol.Optional(CONF_DISPLAY_CARD, default=setting(current, CONF_DISPLAY_CARD)): (
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[DISPLAY_CARD_SQUARE, DISPLAY_CARD_RECTANGULAR],
                    translation_key=CONF_DISPLAY_CARD,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        ),
    }
    # the trend in the TREND column of every display: the arrow (default) or the trend word (the zone word is always
    # under the glucose value)
    fields[vol.Optional(CONF_TREND_DISPLAY, default=setting(current, CONF_TREND_DISPLAY))] = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(TREND_DISPLAY_OPTIONS),
            translation_key=CONF_TREND_DISPLAY,
            mode=selector.SelectSelectorMode.LIST,
        )
    )
    in_use = zone_colors(current)
    for zone, key in CONF_ZONE_COLORS.items():
        default, picker = _picker(in_use[zone])
        fields[vol.Optional(key, default=default)] = picker
    fields[vol.Optional(CONF_COLORS_RESET, default=False)] = bool
    # the graph range colors, collapsed: by default the CGM Report's colors, which most users keep
    graph: dict = {}
    for key in CONF_BAND_COLORS.values():
        default, picker = _picker(setting(current, key))
        graph[vol.Optional(key, default=default)] = picker
    graph[vol.Optional(CONF_BAND_COLORS_RESET, default=False)] = bool
    fields[vol.Required(SECTION_GRAPH_COLORS)] = _section(graph)
    # the backgrounds: the display card's (the theme, or the color below) and the LCD wall panels'
    card = setting(current, CONF_CARD_BACKGROUND)
    theme = card == CARD_BACKGROUND_THEME
    fields[vol.Optional(CONF_CARD_BACKGROUND, default=CARD_BACKGROUND_THEME if theme else CARD_BACKGROUND_COLOR)] = (
        selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=CARD_BACKGROUND_OPTIONS,
                translation_key=CONF_CARD_BACKGROUND,
                mode=selector.SelectSelectorMode.LIST,
            )
        )
    )
    default, picker = _picker(DEFAULT_CARD_BACKGROUND if theme else card)
    fields[vol.Optional(FIELD_CARD_BACKGROUND_COLOR, default=default)] = picker
    default, picker = _picker(setting(current, CONF_PANEL_BACKGROUND))
    fields[vol.Optional(CONF_PANEL_BACKGROUND, default=default)] = picker
    # Outline text: off by default; a 1-pixel edge around the graph labels and the text in a zone color
    fields[vol.Optional(CONF_TEXT_OUTLINE, default=setting(current, CONF_TEXT_OUTLINE))] = bool
    # Fade the words when there is no current reading: on by default; off, the words in the value's place stay still
    fields[vol.Optional(CONF_FLASH_NO_CURRENT_DATA, default=setting(current, CONF_FLASH_NO_CURRENT_DATA))] = bool
    if _weekly_pages_offered(current):  # otherwise the weekly pages use the consensus ranges
        fields[vol.Optional(CONF_WEEKLY_PAGES, default=setting(current, CONF_WEEKLY_PAGES))] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[WEEKLY_PAGES_CONSENSUS, WEEKLY_PAGES_PERSONAL],
                translation_key=CONF_WEEKLY_PAGES,
                mode=selector.SelectSelectorMode.LIST,
            )
        )
    # Wall panels: create a new wall-panel address (an earlier address stops working)
    fields[vol.Optional(CONF_REGENERATE_PANEL_WEBHOOK, default=False)] = bool
    return vol.Schema(fields)


# Notify actions that are not offered as targets: Home Assistant's own notifications (GlucoCraft always writes there),
# the entity-based send_message (it needs a target entity), and the bare notify.notify. A legacy notify integration
# set up without a name registers notify.notify; Home Assistant's mobile_app integration does, and sends it to every
# phone with the Companion app (none when no phone is set up), and another integration may register it instead. The
# phones are offered one by one, so notify.notify would only duplicate them, or send to a service its name does not
# show. A saved choice of any of them is kept (below).
_NOT_OFFERED = frozenset({"persistent_notification", "send_message", "notify"})


def _notify_targets(hass: HomeAssistant, chosen: list[str]) -> list[selector.SelectOptionDict]:
    """Every notify action Home Assistant has but _NOT_OFFERED (phones from the Companion app first), plus any chosen
    one that is missing now (a phone being re-paired), so a saved choice is never dropped silently."""
    available = hass.services.async_services_for_domain("notify")
    names = (set(available) - _NOT_OFFERED) | set(chosen or [])

    def label(n: str) -> str:
        """The notify action's name, a phone of the Companion app by its device name; one missing now is marked."""
        name = t("config_flow.notify_target.phone", name=n[len("mobile_app_") :]) if n.startswith("mobile_app_") else n
        return name if n in available else t("config_flow.notify_target.unavailable", name=name)

    return [
        selector.SelectOptionDict(value=n, label=label(n))
        for n in sorted(names, key=lambda n: (not n.startswith("mobile_app_"), n))
    ]


def _phone_schema(hass: HomeAssistant, d: dict[str, Any]) -> dict:
    """Phone notifications: the same fields at first setup and in the options."""
    targets = setting(d, CONF_PHONE_TARGETS)
    return {
        vol.Optional(CONF_PHONE_TARGETS, default=targets): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=_notify_targets(hass, targets),
                multiple=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Optional(CONF_PHONE_TYPES, default=setting(d, CONF_PHONE_TYPES)): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(PHONE_TYPES),
                translation_key=CONF_PHONE_TYPES,  # the labels: strings.json selector.phone_notify_types
                multiple=True,
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
        vol.Optional(CONF_PHONE_TEST, default=False): bool,
    }


# The day of the weekly summary: stored as an int, 0 = Monday to 6 = Sunday (__init__.py); the selector offers the
# same numbers as text, so that strings.json "selector" names the days and they can be translated
# (config_flow._weekday reads the choice back).
WEEKDAY_OPTIONS = [str(day) for day in range(7)]


# The no-data notice's delays as the selector offers them: the minutes as text, so that strings.json "selector" names
# them; stored as an int
NOTICE_DELAY_OPTIONS = [str(minutes) for minutes in NO_DATA_NOTICE_DELAYS]


def _notifications_schema(hass: HomeAssistant, current: dict[str, Any]) -> vol.Schema:
    """Notifications: the daily and the weekly summary, the no-data notice and its delay, and the phones."""
    return vol.Schema(
        {
            vol.Optional(CONF_NOTIFY_ENABLED, default=setting(current, CONF_NOTIFY_ENABLED)): bool,
            vol.Optional(CONF_NOTIFY_TIME, default=setting(current, CONF_NOTIFY_TIME)): selector.TimeSelector(),
            vol.Optional(CONF_NOTIFY_WEEKLY_ENABLED, default=setting(current, CONF_NOTIFY_WEEKLY_ENABLED)): bool,
            **_choice(CONF_NOTIFY_WEEKLY_DAY, WEEKDAY_OPTIONS, str(setting(current, CONF_NOTIFY_WEEKLY_DAY))),
            vol.Optional(CONF_NO_DATA_NOTICE, default=setting(current, CONF_NO_DATA_NOTICE)): bool,
            **_choice(
                CONF_NO_DATA_NOTICE_DELAY, NOTICE_DELAY_OPTIONS, str(setting(current, CONF_NO_DATA_NOTICE_DELAY))
            ),
            **_phone_schema(hass, current),
        }
    )


def _report_schema(current: dict[str, Any]) -> vol.Schema:
    """Report and dashboard: the PDF paper size, the report printer (optional: a network printer that accepts PDF over
    IPP), the dashboard in the sidebar and what deleting GlucoCraft keeps (kept.py)."""
    return vol.Schema(
        {
            **_regional_schema(current, (CONF_PAPER_SIZE,)),
            vol.Optional(CONF_PRINTER, description={"suggested_value": _text_shown(current, CONF_PRINTER)}): (
                selector.TextSelector()
            ),
            vol.Optional(CONF_SHOW_DASHBOARD, default=setting(current, CONF_SHOW_DASHBOARD)): bool,
            vol.Optional(CONF_ON_REMOVAL, default=setting(current, CONF_ON_REMOVAL)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(ON_REMOVAL_OPTIONS),
                    translation_key=CONF_ON_REMOVAL,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
        }
    )
