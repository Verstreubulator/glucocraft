# License: AGPL-3.0-only (LICENSE).
"""config_flow.py and the translations: the setup screens and the Configure menu, the international settings
with "Automatic" first, their labels and option texts; no glucose notification setting; services.yaml.

Rules under test (the settings are organized by topic, with an explanation under every field):
- Configure is a menu of eight screens in a fixed order; each screen saves only its own fields. Glucose ranges and
  Custom goals are in the menu only while the optional feature Custom ranges and goals is on.
- The two collapsed sections (the second source, the graph range colors) are flattened before saving; a
  selector cleared inside a section stores None, as at the top level.
- The weekday of the weekly summary is offered as the text "0" to "6" with translated names and stored as an int.
- The weekly-pages range set is offered only while Custom ranges and goals is on and custom ranges or goals are set.
- Setup stores the two optional features as off; they are not on any setup screen, and the setup has no ranges and
  goals screens.
- A full setup stores the keys and values it stored before the reorganization (recorded below), without the ranges and
  goals (set only in Configure, once Custom ranges and goals is on) and with the two optional features off.
- Every field has a label and an explanation (data_description) in strings.json; strings.json equals
  translations/en.json byte for byte.

The voluptuous and selector stubs of ha_stub return opaque objects, so the schema tests replace them in flow_schemas
(where the forms are built) with recording stand-ins (monkeypatch) that keep each field's key, default and suggested value.
"""

from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest
from glucocraft import config_flow as cf
from glucocraft import const as C
from glucocraft import flow_schemas as fs
from glucocraft import regional as rg

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "glucocraft"
STRINGS = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))
OPTIONS = STRINGS["options"]["step"]
SETUP = STRINGS["config"]["step"]

# The settings screen of each international setting (Configure)
REGIONAL_SCREEN = {
    C.CONF_FIRST_WEEKDAY: "units",
    C.CONF_DECIMAL_SEPARATOR: "units",
    C.CONF_A1C_UNIT: "units",
    C.CONF_PAPER_SIZE: "report",
}

# What a full setup with the defaults stores: the keys and values, which a change of the screens must keep (stored
# settings never change their keys).
SETUP_BEFORE = {
    "a1c_unit": "auto",
    "date_format": "auto",
    "decimal_separator": "auto",
    "trend_display": "arrow",
    "first_weekday": "auto",
    "glucose_entity": "sensor.cgm",
    "goal_cv": 36,
    "goal_gmi": None,
    "goal_tar": 25,
    "goal_tar_critical": 5,
    "goal_tbr": 4,
    "goal_tbr_critical": 1,
    "goal_time_in_ideal_range": None,
    "goal_time_in_target_range": 70,
    "notify_enabled": True,
    "notify_time": "08:00",
    "paper_size": "auto",
    "phone_notify_targets": [],
    "phone_notify_types": [],
    "range_critical_high": 250,
    "range_critical_low": 54,
    "range_high": 180,
    "range_low": 70,
    "range_tight_high": 140,
    "range_tight_low": 70,
    "secondary_glucose_entity": None,
    "secondary_sensor_lower_limit": "auto",
    "secondary_sensor_upper_limit": "auto",
    "sensor_expiry_entity": None,
    "sensor_lower_limit": "auto",
    "sensor_upper_limit": "auto",
    "show_dashboard": True,
    "stale_threshold": 15,
    "time_format": "auto",
    "unit": "mg/dL",
}


# ------------------------------------------------------------------------------------------------ recording stand-ins
_NO_DEFAULT = object()


class _Key:
    """vol.Optional / vol.Required: the key, its default and its suggested value."""

    def __init__(self, key, default=_NO_DEFAULT, description=None, required=False):
        self.key, self.default, self.required = key, default, required
        self.suggested = (description or {}).get("suggested_value")


class _Section:
    """homeassistant.data_entry_flow.section: the nested schema and its options."""

    def __init__(self, schema, options):
        self.schema, self.options = schema, options


class _Vol:
    """A stand-in for voluptuous that keeps each field's key, default and description readable."""

    @staticmethod
    def Optional(key, default=_NO_DEFAULT, description=None):
        """An optional field."""
        return _Key(key, default, description)

    @staticmethod
    def Required(key, default=_NO_DEFAULT, description=None):
        """A required field."""
        return _Key(key, default, description, required=True)

    @staticmethod
    def Schema(fields):
        """The fields as a dict."""
        return dict(fields)

    @staticmethod
    def All(*validators):
        """The validators, kept for inspection."""
        return ("All", validators)

    @staticmethod
    def Range(**kw):
        """The span, kept for inspection."""
        return ("Range", kw)

    @staticmethod
    def In(options):
        """The choices, kept for inspection."""
        return ("In", options)


class _Selector:
    """homeassistant.helpers.selector: every call records its name and arguments."""

    SelectSelectorMode = NumberSelectorMode = types.SimpleNamespace(DROPDOWN="dropdown", LIST="list", BOX="box")

    def __getattr__(self, name):
        return lambda *args, **kw: (name, args, kw)


@pytest.fixture
def schemas(monkeypatch):
    monkeypatch.setattr(fs, "vol", _Vol)
    monkeypatch.setattr(fs, "section", _Section)
    monkeypatch.setattr(fs, "selector", _Selector())


def _fields(schema: dict) -> tuple[dict[str, _Key], dict[str, dict[str, _Key]]]:
    """The top-level fields of a schema and, per section, the section's fields (in order)."""
    top, sections = {}, {}
    for key, value in schema.items():
        if isinstance(value, _Section):
            sections[key.key] = {k.key: k for k in value.schema}
        else:
            top[key.key] = key
    return top, sections


def _all_fields(schema: dict) -> set[str]:
    top, sections = _fields(schema)
    return set(top) | {k for sec in sections.values() for k in sec}


def _defaults_input(schema: dict) -> dict:
    """What the form sends when nothing is changed: each default; a field with only a suggested value counts as
    cleared (missing); each section as its own nested dict."""
    out: dict = {}
    for key, value in schema.items():
        if isinstance(value, _Section):
            out[key.key] = {k.key: k.default for k in value.schema if k.default is not _NO_DEFAULT}
        elif key.default is not _NO_DEFAULT:
            out[key.key] = key.default
    return out


def _hass(states=("sensor.cgm", "sensor.cgm2"), config_dir: Path | None = None):
    """A Home Assistant stand-in: the source states, the notify actions, the executor and a configuration folder (by
    default one without GlucoCraft's folder, so that no readings kept by an earlier installation are found)."""
    state = types.SimpleNamespace(attributes={"unit_of_measurement": "mg/dL"})
    folder = config_dir or Path(__file__).parent / "no_configuration_folder"

    async def run(fn, *args):
        return fn(*args)

    return types.SimpleNamespace(
        states=types.SimpleNamespace(get=lambda e: state if e in states else None),
        services=types.SimpleNamespace(async_services_for_domain=lambda d: {}, has_service=lambda *a: False),
        config=types.SimpleNamespace(path=lambda *p: str(Path(folder, *p)), country="US", language="en"),
        async_add_executor_job=run,
    )


def _options_flow(data: dict | None = None):
    """A Configure flow on an entry with `data`; returns the flow, the forms shown and the updates saved."""
    flow = cf.GlucoCraftOptionsFlow(types.SimpleNamespace(data=dict(data or _stored())))
    flow.hass = _hass()
    flow._updates, flow._range_base = {}, None
    shown, saved = [], []
    flow.async_show_form = lambda **kw: (shown.append(kw), ("form", kw))[1]
    flow.async_show_menu = lambda **kw: ("menu", kw)

    async def finish():
        saved.append(dict(flow._updates))
        return ("done", {})

    flow._async_finish = finish
    return flow, shown, saved


# the keys of the ranges and goals, which the setup does not store
RANGE_AND_GOAL_KEYS = {k for k in SETUP_BEFORE if k.startswith(("range_", "goal_"))}
FEATURES_ON = {C.CONF_CUSTOM_RANGES_AND_GOALS: True, C.CONF_SEPARATE_STATISTICS: True}


def _stored() -> dict:
    """An entry with both optional features on (every Configure screen in the menu)."""
    return {**SETUP_BEFORE, "notify_weekly_day": 6, **FEATURES_ON}


def _setup_flow():
    flow = cf.GlucoCraftConfigFlow()
    flow._range_base = None
    flow.hass = _hass()
    flow._async_current_entries = lambda include_ignore=None: []
    shown, created = [], []
    flow.async_show_form = lambda **kw: (shown.append(kw), ("form", kw))[1]
    flow.async_create_entry = lambda **kw: (created.append(kw["data"]), ("entry", kw))[1]
    return flow, shown, created


def _run_setup(flow, shown, source: dict | None = None) -> None:
    asyncio.run(flow.async_step_user({}))
    schema = shown[-1]["data_schema"]
    source_input = {**_defaults_input(schema), "glucose_entity": "sensor.cgm", **(source or {})}
    asyncio.run(flow.async_step_source(source_input))
    assert shown[-1]["step_id"] == "finish"  # no ranges and goals screens
    asyncio.run(flow.async_step_finish(_defaults_input(shown[-1]["data_schema"])))


# ------------------------------------------------------------------------------------------------ international settings
def test_regional_options_auto_first_and_resolvable():
    assert set(cf.REGIONAL_OPTIONS) == {
        C.CONF_FIRST_WEEKDAY,
        C.CONF_DECIMAL_SEPARATOR,
        C.CONF_A1C_UNIT,
        C.CONF_PAPER_SIZE,
    }
    assert all(opts[0] == "auto" for opts in cf.REGIONAL_OPTIONS.values())
    assert cf.REGIONAL_OPTIONS[C.CONF_FIRST_WEEKDAY][1:] == list(rg.WEEK_CHOICES)
    assert cf.REGIONAL_OPTIONS[C.CONF_DECIMAL_SEPARATOR][1:] == list(rg.DEC_CHOICES)
    for choice in cf.REGIONAL_OPTIONS[C.CONF_A1C_UNIT][1:]:
        assert rg.resolve_a1c_unit(choice, "US") == choice
    for choice in cf.REGIONAL_OPTIONS[C.CONF_PAPER_SIZE][1:]:
        assert rg.resolve_paper_size(choice, "US") == choice


@pytest.mark.parametrize("key", list(cf.REGIONAL_OPTIONS))
def test_every_option_has_a_text_and_a_label(key):
    assert set(STRINGS["selector"][key]["options"]) == set(cf.REGIONAL_OPTIONS[key])
    assert STRINGS["selector"][key]["options"]["auto"].startswith("Automatic")
    step = OPTIONS[REGIONAL_SCREEN[key]]
    assert key in step["data"] and key in step["data_description"]


def test_regional_settings_listed_once():
    assert C.REGIONAL_SETTINGS == (
        "time_format",
        "date_format",
        "first_weekday",
        "decimal_separator",
        "a1c_unit",
        "paper_size",
    )


def test_no_glucose_notification_settings():
    """GlucoCraft sends no high or low glucose notifications: no switch, no
    section and no phone type for them on any screen. The phones can receive the summaries and the no-data notice, a
    notice about GlucoCraft's data, never about glucose."""
    texts = json.dumps(STRINGS)
    assert "alert_" not in texts and "test_notifications" not in texts and "notify_low_enabled" not in texts
    assert "sections" not in OPTIONS["notifications"]
    assert list(STRINGS["selector"]["phone_notify_types"]["options"]) == list(C.PHONE_TYPES)
    assert list(C.PHONE_TYPES) == ["summaries", "no_data_notice"]


def test_services_yaml_lab_unit():
    text = (PKG / "services.yaml").read_text(encoding="utf-8")
    assert "unit:" in text and '"mmol_mol"' in text and "max: 195" in text
    f = STRINGS["services"]["add_lab_a1c"]["fields"]
    assert "unit" in f and "mmol/mol" in f["a1c"]["description"]


def test_the_delivery_test_is_its_own_message_alone():
    """The delivery test is an operational message: its title and its text, nothing added after it."""
    src = (PKG / "flow_helpers.py").read_text(encoding="utf-8")  # the delivery test (_send_phone_test)
    assert '{"title": t("notify.phone_test.title"), "message": t("notify.phone_test.message")}' in src
    assert "notify.last_line" not in src
    from glucocraft.i18n import t

    assert t("notify.phone_test.title") == "GlucoCraft delivery test"
    assert "no glucose value" in t("notify.phone_test.message")
    assert "notifications are not alarms" not in src


# ------------------------------------------------------------------------------------------------ the Configure menu
MENU = ["sources", "units", "features", "ranges", "goals", "displays", "notifications", "report"]


def test_configure_menu_order_and_steps():
    flow, _, _ = _options_flow()
    kind, kw = asyncio.run(flow.async_step_init())
    assert kind == "menu" and kw["step_id"] == "init" and kw["menu_options"] == MENU
    assert list(OPTIONS["init"]["menu_options"]) == MENU
    assert list(OPTIONS) == ["init", *MENU]
    for step in MENU:
        assert f"async_step_{step}" in vars(cf.GlucoCraftOptionsFlow), step
    for old in ("general", "colors", "weekly_pages"):  # screens that are not in the menu
        assert f"async_step_{old}" not in vars(cf.GlucoCraftOptionsFlow), old
    assert list(SETUP) == ["user", "kept", "source", "finish"]  # kept: only when an earlier installation kept readings
    assert not {"ranges", "goals"} & set(vars(cf.GlucoCraftConfigFlow))  # no setup steps for them
    assert SETUP["finish"]["title"] == "Units and Notifications"


@pytest.mark.parametrize("custom", [False, True])
def test_the_ranges_and_goals_screens_follow_the_custom_feature(custom):
    """Glucose ranges and Custom goals are in the menu only while Custom ranges and goals is on; Optional features
    always."""
    flow, _, _ = _options_flow({**_stored(), C.CONF_CUSTOM_RANGES_AND_GOALS: custom})
    _, kw = asyncio.run(flow.async_step_init())
    assert kw["menu_options"] == [s for s in MENU if custom or s not in ("ranges", "goals")]
    assert "features" in kw["menu_options"]


def test_the_features_screen_stores_both_switches(schemas):
    """Optional features: two switches shown as stored (missing = off) and saved as booleans, nothing else."""
    flow, shown, saved = _options_flow({k: v for k, v in _stored().items() if k not in FEATURES_ON})
    top, sections = _fields(_screen(flow, shown, "features"))
    assert list(top) == ["custom_ranges_and_goals", "separate_statistics"] and not sections
    assert [f.default for f in top.values()] == [False, False]
    asyncio.run(flow.async_step_features({"custom_ranges_and_goals": True, "separate_statistics": False}))
    assert saved[-1] == {"custom_ranges_and_goals": True, "separate_statistics": False}
    flow, shown, saved = _options_flow()
    assert [f.default for f in _fields(_screen(flow, shown, "features"))[0].values()] == [True, True]
    asyncio.run(flow.async_step_features({}))  # nothing ticked: both off
    assert saved[-1] == {"custom_ranges_and_goals": False, "separate_statistics": False}


def _screen(flow, shown, step: str):
    """Show a Configure screen; return its schema."""
    asyncio.run(getattr(flow, f"async_step_{step}")())
    assert shown[-1]["step_id"] == step
    return shown[-1]["data_schema"]


def test_the_goal_boxes_hold_only_goals_that_can_be_met(schemas):
    """Time in range is compared as "more than" and the time below the very-low limit as "less than": their number
    boxes span 50% to 99% and 1% to 10%; every other goal keeps its span."""
    from glucocraft.settings import GOAL_LIMITS

    assert GOAL_LIMITS[C.CONF_GOAL_TIME_IN_TARGET_RANGE] == (50, 99)
    assert GOAL_LIMITS[C.CONF_GOAL_TBR_CRITICAL] == (1, 10)
    flow, shown, _ = _options_flow(_stored())
    schema = _screen(flow, shown, "goals")
    for key, (lo, hi) in GOAL_LIMITS.items():
        _name, (config,), _ = _selector_of(schema, key)
        assert (config[2]["min"], config[2]["max"]) == (lo, hi), key


@pytest.mark.parametrize(
    "key, stored, shown", [(C.CONF_GOAL_TIME_IN_TARGET_RANGE, 100, 70), (C.CONF_GOAL_TBR_CRITICAL, 0, 1)]
)
def test_the_goals_screen_shows_a_goal_outside_its_span_as_its_default(schemas, key, stored, shown):
    """A goal stored outside the span of its box is shown as its default, so that the screen opens and saves without an
    error."""
    flow, shown_, saved = _options_flow({**_stored(), key: stored})
    schema = _screen(flow, shown_, "goals")
    assert _fields(schema)[0][key].default == shown and not shown_[-1]["errors"]
    kind, _ = asyncio.run(flow.async_step_goals(_defaults_input(schema)))
    assert kind == "done" and saved[-1][key] == shown


@pytest.mark.parametrize("step", MENU)
def test_each_configure_screen_saves_only_its_own_fields(schemas, step):
    stored = {**_stored(), C.CONF_GOAL_TBR: 3}  # a custom goal: the weekly pages are offered
    flow, shown, saved = _options_flow(stored)
    schema = _screen(flow, shown, step)
    fields = _all_fields(schema)
    kind, _ = asyncio.run(getattr(flow, f"async_step_{step}")(_defaults_input(schema)))
    assert kind == "done", shown[-1].get("errors")
    updates = saved[-1]
    assert updates and set(updates) <= fields, set(updates) - fields
    assert not set(updates) & set(fs.SECTIONS)  # flattened: no section key is stored
    # the fields of every other screen are left as stored
    others = set().union(*(_all_fields(_screen(*_options_flow(stored)[:2], s)) for s in MENU if s != step))
    assert not set(updates) & (others - fields)


def test_the_screens_hold_their_fields(schemas):
    flow, shown, _ = _options_flow({**_stored(), C.CONF_GOAL_GMI: 7.0})
    top, sections = _fields(_screen(flow, shown, "sources"))
    assert list(top) == [
        "glucose_entity",
        "sensor_expiry_entity",
        "sensor_lower_limit",
        "sensor_upper_limit",
        "primary_time_attribute",
        "stale_threshold",
    ]
    assert list(sections) == ["second_source"]
    assert list(sections["second_source"]) == [
        "secondary_glucose_entity",
        "secondary_sensor_lower_limit",
        "secondary_sensor_upper_limit",
        "secondary_time_attribute",
    ]
    assert list(_fields(_screen(flow, shown, "units"))[0]) == [
        "unit",
        "a1c_unit",
        "time_format",
        "date_format",
        "first_weekday",
        "decimal_separator",
    ]
    top, sections = _fields(_screen(flow, shown, "displays"))
    assert list(top) == [
        "display_card",
        "trend_display",
        *C.CONF_ZONE_COLORS.values(),
        "colors_reset",
        "card_background",
        "card_background_color",
        "panel_background",
        "text_outline",
        "flash_no_current_data",
        "weekly_pages",
        "regenerate_panel_webhook",
    ]
    assert list(_fields(_screen(flow, shown, "features"))[0]) == ["custom_ranges_and_goals", "separate_statistics"]
    assert list(sections) == ["graph_colors"]
    assert list(sections["graph_colors"]) == [*C.CONF_BAND_COLORS.values(), "band_colors_reset"]
    top, sections = _fields(_screen(flow, shown, "notifications"))
    assert list(top) == [
        "notify_enabled",
        "notify_time",
        "notify_weekly_enabled",
        "notify_weekly_day",
        "no_data_notice",
        "no_data_notice_delay",
        "phone_notify_targets",
        "phone_notify_types",
        "phone_notify_test",
    ]
    assert sections == {}
    assert list(_fields(_screen(flow, shown, "report"))[0]) == [
        "paper_size",
        "printer_address",
        "show_dashboard",
        "on_removal",
    ]


def test_every_section_is_collapsed(schemas):
    flow, shown, _ = _options_flow()
    for step, name in (
        ("sources", "second_source"),
        ("displays", "graph_colors"),
    ):
        schema = _screen(flow, shown, step)
        section = next(v for k, v in schema.items() if k.key == name)
        assert section.options == {"collapsed": True}, step
    setup, setup_shown, _ = _setup_flow()
    asyncio.run(setup.async_step_user({}))
    section = next(v for k, v in setup_shown[-1]["data_schema"].items() if k.key == "second_source")
    assert section.options == {"collapsed": True}


# ------------------------------------------------------------------------------------------------ sections flattened
def test_section_input_is_flattened(schemas):
    assert cf._flatten(
        {
            "glucose_entity": "sensor.cgm",
            "second_source": {"secondary_glucose_entity": "sensor.cgm2", "secondary_sensor_lower_limit": "50"},
            "graph_colors": {"band_color_low": "#123456"},
        }
    ) == {
        "glucose_entity": "sensor.cgm",
        "secondary_glucose_entity": "sensor.cgm2",
        "secondary_sensor_lower_limit": "50",
        "band_color_low": "#123456",
    }
    assert cf._flatten({"second_source": None, "a": 1}) == {"a": 1}
    flow, shown, saved = _options_flow()
    user_input = _defaults_input(_screen(flow, shown, "sources"))
    user_input["second_source"]["secondary_glucose_entity"] = "sensor.cgm2"
    user_input["second_source"]["secondary_time_attribute"] = "measured_at"
    asyncio.run(flow.async_step_sources(user_input))
    assert saved[-1]["secondary_glucose_entity"] == "sensor.cgm2"
    assert saved[-1]["secondary_time_attribute"] == "measured_at"
    assert "second_source" not in saved[-1]


def test_a_cleared_second_source_stores_none(schemas):
    stored = {**_stored(), "secondary_glucose_entity": "sensor.cgm2", "secondary_time_attribute": "measured_at"}
    flow, shown, saved = _options_flow(stored)
    schema = _screen(flow, shown, "sources")
    second = _fields(schema)[1]["second_source"]
    assert second["secondary_glucose_entity"].suggested == "sensor.cgm2"  # shown as stored
    user_input = _defaults_input(schema)  # the cleared selectors are missing from the section's input
    assert "secondary_glucose_entity" not in user_input["second_source"]
    asyncio.run(flow.async_step_sources(user_input))
    assert saved[-1]["secondary_glucose_entity"] is None
    assert saved[-1]["secondary_time_attribute"] == ""
    assert saved[-1]["sensor_expiry_entity"] is None and "direction_entity" not in saved[-1]
    # in setup as well
    setup, setup_shown, created = _setup_flow()
    _run_setup(setup, setup_shown)
    assert created[-1]["secondary_glucose_entity"] is None


def test_source_errors_stay_on_their_fields(schemas, monkeypatch):
    monkeypatch.setattr(cf, "is_glucocraft_entity", lambda hass, e: e == "sensor.glucocraft_status")
    flow, shown, saved = _options_flow()
    user_input = _defaults_input(_screen(flow, shown, "sources"))
    user_input["second_source"]["secondary_glucose_entity"] = "sensor.glucocraft_status"
    kind, kw = asyncio.run(flow.async_step_sources(user_input))
    assert kind == "form" and saved == []
    assert kw["errors"] == {"second_source": "source_is_glucocraft"}  # shown above the section
    section = next(v for k, v in kw["data_schema"].items() if k.key == "second_source")
    assert section.options == {"collapsed": False}  # opened, so that the field is visible
    user_input = _defaults_input(_screen(flow, shown, "sources"))
    user_input["glucose_entity"] = "sensor.gone"
    kind, kw = asyncio.run(flow.async_step_sources(user_input))
    assert kw["errors"] == {"glucose_entity": "entity_not_found"}
    # the printer address on its own screen
    user_input = _defaults_input(_screen(flow, shown, "report"))
    kind, kw = asyncio.run(flow.async_step_report({**user_input, "printer_address": "ipp://u:p@printer/ipp"}))
    assert kind == "form" and kw["errors"] == {"printer_address": "printer_invalid"} and saved == []
    asyncio.run(flow.async_step_report({**user_input, "printer_address": " 192.168.1.20 "}))
    assert saved[-1]["printer_address"] == "192.168.1.20"
    # setup: the same errors
    setup, _setup_shown, _ = _setup_flow()
    asyncio.run(setup.async_step_user({}))
    kind, kw = asyncio.run(
        setup.async_step_source(
            {"glucose_entity": "sensor.cgm", "second_source": {"secondary_glucose_entity": "sensor.glucocraft_status"}}
        )
    )
    assert kind == "form" and kw["step_id"] == "source" and kw["errors"] == {"second_source": "source_is_glucocraft"}
    for code in ("source_is_glucocraft", "entity_not_found", "printer_invalid"):
        assert code in STRINGS["options"]["error"], code


# ------------------------------------------------------------------------------------------------ the weekday
def test_the_weekday_is_stored_as_an_int(schemas):
    flow, shown, saved = _options_flow({**_stored(), "notify_weekly_day": 2})
    schema = _screen(flow, shown, "notifications")
    day = _fields(schema)[0]["notify_weekly_day"]
    assert day.default == "2"  # the stored int, shown as its option
    _, (config,), _ = next(v for k, v in schema.items() if k.key == "notify_weekly_day")
    assert config[2]["options"] == fs.WEEKDAY_OPTIONS == ["0", "1", "2", "3", "4", "5", "6"]
    assert config[2]["translation_key"] == "notify_weekly_day"
    names = STRINGS["selector"]["notify_weekly_day"]["options"]
    assert list(names) == fs.WEEKDAY_OPTIONS
    assert list(names.values()) == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    user_input = _defaults_input(schema)
    for value, stored in (("4", 4), ("0", 0), ("6", 6)):
        asyncio.run(flow.async_step_notifications({**user_input, "notify_weekly_day": value}))
        assert saved[-1]["notify_weekly_day"] == stored and type(saved[-1]["notify_weekly_day"]) is int
    asyncio.run(flow.async_step_notifications({**user_input, "notify_weekly_day": "9"}))
    assert saved[-1]["notify_weekly_day"] == 2  # not a day: the stored value is kept
    assert cf._weekday(None) == C.DEFAULT_NOTIFY_WEEKLY_DAY and cf._weekday(True) == C.DEFAULT_NOTIFY_WEEKLY_DAY


# ------------------------------------------------------------------------------------------------ weekly pages
def test_the_weekly_pages_field_appears_only_with_custom_values(schemas):
    assert "weekly_pages" not in _all_fields(cf._displays_schema(_stored()))
    for custom in ({C.CONF_RANGE_HIGH: 200}, {C.CONF_GOAL_TBR: 3}, {C.CONF_GOAL_GMI: 7.0}):
        assert "weekly_pages" in _all_fields(cf._displays_schema({**_stored(), **custom})), custom
        # Custom ranges and goals off: the stored values are not in use, the choice is not offered
        off = {**_stored(), **custom, C.CONF_CUSTOM_RANGES_AND_GOALS: False}
        assert "weekly_pages" not in _all_fields(cf._displays_schema(off)), custom
    # without custom values a sent weekly-pages value is not stored
    flow, shown, saved = _options_flow()
    user_input = _defaults_input(_screen(flow, shown, "displays"))
    asyncio.run(flow.async_step_displays({**user_input, "weekly_pages": "personal"}))
    assert "weekly_pages" not in saved[-1]
    flow, shown, saved = _options_flow({**_stored(), C.CONF_RANGE_HIGH: 200})
    user_input = _defaults_input(_screen(flow, shown, "displays"))
    asyncio.run(flow.async_step_displays({**user_input, "weekly_pages": "personal"}))
    assert saved[-1]["weekly_pages"] == "personal"
    assert "regenerate_panel_webhook" not in saved[-1]  # only when the box is ticked


def test_the_wall_panel_address_is_in_the_description(schemas):
    flow, shown, _ = _options_flow({**_stored(), C.CONF_PANEL_WEBHOOK_ID: "glucocraft_panel_abc"})
    _screen(flow, shown, "displays")
    assert "glucocraft_panel_abc" in shown[-1]["description_placeholders"]["panel_address"]
    assert "{panel_address}" in OPTIONS["displays"]["description"]
    assert "*Create a new wall-panel address*" in cf._panel_address_text({})
    assert OPTIONS["displays"]["data"]["regenerate_panel_webhook"] == "Create a new wall-panel address"


# ------------------------------------------------------------------------------------------------ setup
def test_setup_stores_the_features_as_off(schemas):
    flow, shown, created = _setup_flow()
    _run_setup(flow, shown)
    finish = _all_fields(shown[-1]["data_schema"])
    assert finish == {
        "unit",
        "notify_enabled",
        "notify_time",
        "show_dashboard",
        "phone_notify_targets",
        "phone_notify_types",
        "phone_notify_test",
    }
    assert not finish & set(FEATURES_ON)
    for k in FEATURES_ON:
        assert created[-1][k] is False, k
    assert not any(k.startswith(("alert_", "notify_very_", "notify_low", "notify_high")) for k in created[-1])
    assert "direction_entity" not in created[-1]


def test_no_current_data_after_is_on_the_source_screen(schemas):
    flow, shown, created = _setup_flow()
    asyncio.run(flow.async_step_user({}))
    top, sections = _fields(shown[-1]["data_schema"])
    assert list(top) == [
        "glucose_entity",
        "sensor_expiry_entity",
        "sensor_lower_limit",
        "sensor_upper_limit",
        "stale_threshold",
    ]
    assert top["stale_threshold"].default == C.DEFAULT_STALE_THRESHOLD
    assert list(sections["second_source"]) == [
        "secondary_glucose_entity",
        "secondary_sensor_lower_limit",
        "secondary_sensor_upper_limit",
    ]
    flow, shown, created = _setup_flow()
    _run_setup(flow, shown, {"stale_threshold": 20})
    assert created[-1]["stale_threshold"] == 20


def _selector_of(schema: dict, name: str):
    """The selector of the top-level field `name`."""
    return next(value for key, value in schema.items() if not isinstance(value, _Section) and key.key == name)


def test_no_current_data_after_is_a_number_box_in_minutes(schemas):
    """No current data after: a number box that shows the unit (min) and keeps the span 5 to 30 minutes, on the setup
    screen and in Configure; the box's number (a float) is stored as whole minutes."""
    flow, shown, created = _setup_flow()
    asyncio.run(flow.async_step_user({}))
    setup_box = _selector_of(shown[-1]["data_schema"], "stale_threshold")
    options, options_shown, saved = _options_flow()
    options_box = _selector_of(_screen(options, options_shown, "sources"), "stale_threshold")
    for box in (setup_box, options_box):
        name, (config,), _ = box
        assert name == "NumberSelector" and config[0] == "NumberSelectorConfig"
        assert config[2] == {"min": 5, "max": 30, "step": 1, "mode": "box", "unit_of_measurement": "min"}
    flow, shown, created = _setup_flow()
    _run_setup(flow, shown, {"stale_threshold": 20.0})
    assert created[-1]["stale_threshold"] == 20 and type(created[-1]["stale_threshold"]) is int
    schema = _screen(options, options_shown, "sources")
    asyncio.run(options.async_step_sources({**_defaults_input(schema), "stale_threshold": 25.0}))
    assert saved[-1]["stale_threshold"] == 25 and type(saved[-1]["stale_threshold"]) is int
    assert cf._stale_minutes({}) == C.DEFAULT_STALE_THRESHOLD  # a missing field: the default


def test_a_full_setup_stores_the_same_keys_and_values_as_before(schemas):
    """As before, without the ranges and goals (their defaults apply until they are set in Configure) and with the two
    optional features off."""
    flow, shown, created = _setup_flow()
    _run_setup(flow, shown)
    data = dict(created[-1])
    expected = {k: v for k, v in SETUP_BEFORE.items() if k not in RANGE_AND_GOAL_KEYS}
    expected.update({k: False for k in FEATURES_ON})
    assert set(data) == set(expected), set(data) ^ set(expected)
    assert data == expected


# ------------------------------------------------------------------------------------------------ the texts
def _screens():
    """Every setup and Configure screen with fields: (flow, step id, its texts, its schema)."""
    out = []
    setup, setup_shown, _ = _setup_flow()
    asyncio.run(setup.async_step_user({}))
    out.append(("config", "source", setup_shown[-1]["data_schema"]))
    asyncio.run(setup.async_step_finish())
    out.append(("config", "finish", setup_shown[-1]["data_schema"]))
    flow, shown, _ = _options_flow({**_stored(), C.CONF_GOAL_TBR: 3})
    out += [("options", step, _screen(flow, shown, step)) for step in MENU]
    return out


def test_every_field_has_a_label_and_an_explanation(schemas):
    for flow, step, schema in _screens():
        texts = STRINGS[flow]["step"][step]
        top, sections = _fields(schema)
        assert texts["title"] and texts["description"].strip(), (flow, step)
        assert set(texts["data"]) == set(top), (flow, step, set(texts["data"]) ^ set(top))
        assert set(texts["data_description"]) == set(top), (flow, step)
        assert set(texts.get("sections", {})) == set(sections), (flow, step)
        groups = [(texts["data"], texts["data_description"])]
        for name, fields in sections.items():
            sec = texts["sections"][name]
            assert sec["name"] and sec["description"].strip(), (flow, step, name)
            assert set(sec["data"]) == set(fields) == set(sec["data_description"]), (flow, step, name)
            groups.append((sec["data"], sec["data_description"]))
        for labels, notes in groups:
            for key, label in labels.items():
                # a short label: the setting's name only (the longest: "Fade the words when there is no current
                # reading", 47 characters); the explanation is under the field
                assert len(label) <= 47 and not label.endswith("."), (flow, step, key, label)
                note = notes[key]
                assert note.endswith(".") and len(note) > len(label), (flow, step, key)


def test_the_ranges_screen_names_the_unit():
    """The ranges are entered in Configure only (the setup has no ranges screen), in the display unit."""
    sentence = "Values are entered in the display unit (mg/dL or mmol/L; GlucoCraft stores them in mg/dL)."
    assert sentence in OPTIONS["ranges"]["description"] and "during setup" not in OPTIONS["ranges"]["description"]
    assert "ranges" not in SETUP and "goals" not in SETUP


def test_the_last_setup_screen_names_configure():
    assert (
        "Every setting can be changed later under Settings > Devices & services > GlucoCraft > Configure."
        in SETUP["finish"]["description"]
    )
    # where the optional features, which start off, are turned on
    assert "Configure > Optional features" in SETUP["finish"]["description"]
    old = ("Sources, notifications", "Advanced Options", "Display Colors and Word", "Weekly Pages on the Display Card")
    raw = (PKG / "strings.json").read_text(encoding="utf-8")
    assert not any(name in raw for name in old)


def test_strings_equal_the_english_translation():
    assert (PKG / "strings.json").read_bytes() == (PKG / "translations" / "en.json").read_bytes()


@pytest.mark.parametrize(
    "stored, shown", [("unknown_layout", "square"), ("rectangular", "rectangular"), (None, "square")]
)
def test_the_display_card_form_shows_a_valid_choice(schemas, stored, shown):
    """Configure > Display card and wall panels offers the square and the rectangular card, and its default is the
    setting in use: a stored value that is not one of the options shows the default, square."""
    schema = cf._displays_schema({C.CONF_DISPLAY_CARD: stored} if stored else {})
    top, _ = _fields(schema)
    assert top[C.CONF_DISPLAY_CARD].default == shown
    field = next(v for k, v in schema.items() if getattr(k, "key", None) == C.CONF_DISPLAY_CARD)
    name, (config,), _ = field
    assert name == "SelectSelector" and config[2]["options"] == [C.DISPLAY_CARD_SQUARE, C.DISPLAY_CARD_RECTANGULAR]


def test_the_no_data_notice_and_the_throb_setting_are_saved(schemas):
    """Configure > Notifications: the no-data notice (off by default) and its delay (30 minutes by default, stored as
    whole minutes); Configure > Display card and wall panels: Fade the words when there is no current reading (on by
    default)."""
    flow, shown, saved = _options_flow()
    defaults = _defaults_input(_screen(flow, shown, "notifications"))
    assert defaults["no_data_notice"] is False and defaults["no_data_notice_delay"] == "30"
    asyncio.run(flow.async_step_notifications({**defaults, "no_data_notice": True, "no_data_notice_delay": "120"}))
    assert saved[-1]["no_data_notice"] is True and saved[-1]["no_data_notice_delay"] == 120
    flow, shown, saved = _options_flow()
    defaults = _defaults_input(_screen(flow, shown, "displays"))
    assert defaults["flash_no_current_data"] is True
    asyncio.run(flow.async_step_displays({**defaults, "flash_no_current_data": False}))
    assert saved[-1]["flash_no_current_data"] is False
    assert (
        list(STRINGS["selector"]["no_data_notice_delay"]["options"])
        == fs.NOTICE_DELAY_OPTIONS
        == [
            "15",
            "30",
            "60",
            "120",
        ]
    )


# ------------------------------------------------------------------------------------------------ the notification time
def test_a_badly_formed_notification_time_is_refused_on_configure(schemas):
    """A field error, nothing saved; the two forms the time selector stores stay good."""
    flow, shown, saved = _options_flow()
    defaults = _defaults_input(_screen(flow, shown, "notifications"))
    for bad in ("7:5", "25:00", "07:60"):
        kind, kw = asyncio.run(flow.async_step_notifications({**defaults, "notify_time": bad}))
        assert kind == "form" and kw["errors"] == {"notify_time": "notify_time_invalid"}
    assert saved == []
    for good in ("07:05", "07:05:30"):
        asyncio.run(flow.async_step_notifications({**defaults, "notify_time": good}))
        assert saved[-1]["notify_time"] == good


def test_a_badly_formed_notification_time_is_refused_in_the_setup(schemas):
    flow, _shown, created = _setup_flow()
    kind, kw = asyncio.run(flow.async_step_finish({"notify_time": "7:5"}))
    assert kind == "form" and kw["errors"] == {"notify_time": "notify_time_invalid"} and created == []
