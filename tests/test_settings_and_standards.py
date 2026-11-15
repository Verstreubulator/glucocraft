# License: AGPL-3.0-only (LICENSE).
"""The settings screens, the display colors and the Home Assistant / HACS standards (config_flow.py, const.py,
strings.json, services.yaml, sensor.py, binary_sensor.py, entity.py, panel_feed.py, http_api.py, frontend.py).

Rules under test:
- GlucoCraft's own entities are not offered as sources and are rejected; the primary source can be changed in the
  settings.
- A printer address with a user name or password, a wrong scheme or no host is rejected.
- The display colors: the default palette (lows red, highs blue), a color per zone in the settings, the colors in
  use published as "zone_colors" and "zone_color", and the zone word "zone_label" outside the range.
- The Date format choices are lower case (hassfest) and published as the codes the displays read ("MDY").
- Every selector option key is a valid translation key; every action selector has its option texts.
- The settings texts: the custom-ranges statement, "more than" for the time-in-range goal, no product name outside
  the allowed places, statements of what the code is designed to do.
- The wall-panel address exists only once created in the settings and is never put into a notification; the HTTP
  views need a login, the panel webhook is local only and GET only.
- One device of the type service; no limit on parallel updates; the neutral entity states and names.
- The license is AGPL-3.0-only; prebuilt panel firmware is not distributed; the first line of every source file is
  the license line.
"""

from __future__ import annotations

import ast
import json
import re
import types
from pathlib import Path

import pytest
import yaml
from glucocraft import config_flow as cf
from glucocraft import const as C
from glucocraft import flow_helpers as fh
from glucocraft import flow_schemas as fs
from glucocraft import panel_feed as pf

from conftest import FakeRegistry
from test_trend_and_configuration import settings_coordinator

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "custom_components" / "glucocraft"
STRINGS = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))
BLOB = json.dumps(STRINGS, ensure_ascii=False)


# ------------------------------------------------------------------------------------------------ sources
@pytest.fixture
def registry(monkeypatch):
    reg = FakeRegistry(
        {
            "sensor.glucocraft_last_glucose_reading": {"platform": "glucocraft"},
            "sensor.libre": {"platform": "librelink"},
            "sensor.glucocraft_status": {"platform": "glucocraft"},
        }
    )
    monkeypatch.setattr(fh, "er", types.SimpleNamespace(async_get=lambda hass: reg))  # the registry lookups
    return reg


def _hass(*existing, units: dict | None = None):
    """A stand-in Home Assistant whose states are `existing` (in mg/dL, or in the unit `units` names for the entity)."""

    def get(entity_id):
        if entity_id not in existing:
            return None
        unit = (units or {}).get(entity_id, "mg/dL")
        return types.SimpleNamespace(state="120", attributes={} if unit is None else {"unit_of_measurement": unit})

    return types.SimpleNamespace(states=types.SimpleNamespace(get=get))


def test_own_entities_excluded_and_rejected(registry):
    hass = _hass("sensor.libre", "sensor.glucocraft_last_glucose_reading")
    assert fh.glucocraft_entity_ids(hass) == ["sensor.glucocraft_last_glucose_reading", "sensor.glucocraft_status"]
    errors = cf._source_errors(
        hass,
        {
            cf.CONF_GLUCOSE_ENTITY: "sensor.glucocraft_last_glucose_reading",
            cf.CONF_SECONDARY_ENTITY: "sensor.glucocraft_status",
        },
    )
    assert errors == {cf.CONF_GLUCOSE_ENTITY: "source_is_glucocraft", cf.CONF_SECONDARY_ENTITY: "source_is_glucocraft"}
    assert cf._source_errors(hass, {cf.CONF_GLUCOSE_ENTITY: "sensor.libre"}) == {}
    assert cf._source_errors(hass, {cf.CONF_GLUCOSE_ENTITY: "sensor.gone"}) == {
        cf.CONF_GLUCOSE_ENTITY: "entity_not_found"
    }
    for section in ("config", "options"):
        assert "source_is_glucocraft" in STRINGS[section]["error"]


def test_own_entity_as_the_expiry_entity_has_its_own_error(registry):
    """GlucoCraft's own entity in the CGM sensor expiry field gets an error that names that field, not the glucose
    source's reason ("its value is GlucoCraft's own last reading")."""
    hass = _hass("sensor.libre", "sensor.glucocraft_status")
    errors = cf._source_errors(
        hass, {cf.CONF_GLUCOSE_ENTITY: "sensor.libre", cf.CONF_SENSOR_EXPIRY_ENTITY: "sensor.glucocraft_status"}
    )
    assert errors == {cf.CONF_SENSOR_EXPIRY_ENTITY: "expiry_is_glucocraft"}
    for section in ("config", "options"):
        text = STRINGS[section]["error"]["expiry_is_glucocraft"]
        assert "CGM sensor expiry entity" in text and "glucose source" not in text


# The units of the integrations GlucoCraft names (Dexcom, LibreLink, CareLink, Nightscout) and a source without a unit
GLUCOSE_UNITS = ["mg/dL", "mg/dl", "MG/DL", " mg / dL ", "mmol/L", "mmol/l", "MMOL/L", None, ""]
OTHER_UNITS = ["°F", "°C", "%", "W", "mg/m³", "mg/L", "µg/m³", "ppm"]


@pytest.mark.parametrize("unit", GLUCOSE_UNITS + OTHER_UNITS)
def test_a_source_in_a_unit_other_than_mg_dl_or_mmol_l_is_refused(registry, unit):
    """A glucose source (primary or second) that states a unit other than mg/dL or mmol/L is refused at setup and in
    Configure; the whole unit text is compared, so "mg/m³" (formaldehyde) and "mg/L" (water) are no mg/dL. A source
    without a unit stays allowed (it counts as mg/dL, as live)."""
    units = {"sensor.p": unit, "sensor.s": unit}
    hass = _hass("sensor.p", "sensor.s", units=units)
    refused = unit in OTHER_UNITS
    errors = cf._source_errors(hass, {cf.CONF_GLUCOSE_ENTITY: "sensor.p", cf.CONF_SECONDARY_ENTITY: "sensor.s"})
    expected = {cf.CONF_GLUCOSE_ENTITY: "source_unit_not_glucose", cf.CONF_SECONDARY_ENTITY: "source_unit_not_glucose"}
    assert errors == (expected if refused else {})
    # the expiry entity is not a glucose source: its unit is not checked
    assert cf._source_errors(hass, {cf.CONF_GLUCOSE_ENTITY: "sensor.p", cf.CONF_SENSOR_EXPIRY_ENTITY: "sensor.s"}) == (
        {cf.CONF_GLUCOSE_ENTITY: "source_unit_not_glucose"} if refused else {}
    )
    for section in ("config", "options"):
        assert "source_unit_not_glucose" in STRINGS[section]["error"]


def test_primary_source_can_be_changed():
    src = (PKG / "flow_schemas.py").read_text(encoding="utf-8")  # the forms
    sources = src[src.index("def _sources_schema(") : src.index("def _units_schema(")]
    assert "vol.Required(CONF_GLUCOSE_ENTITY" in sources and "CONF_SENSOR_EXPIRY_ENTITY" in sources
    assert 'EntitySelectorConfig(domain="sensor")' not in src  # every source selector excludes our own
    step = STRINGS["options"]["step"]["sources"]  # Configure > Glucose sources
    assert "keeps the stored readings" in step["data_description"]["glucose_entity"]
    assert "direction_entity" not in step["data"]  # no trend direction entity: GlucoCraft's own trend is used


@pytest.mark.parametrize(
    "address, ok",
    [
        ("", True),
        ("192.168.1.20", True),
        ("printer.local", True),
        ("ipp://printer.local/ipp/print", True),
        ("ipps://printer.local:631/ipp/print", True),
        ("http://192.0.2.10:631/ipp", True),
        ("ipp://user:secret@192.0.2.10/ipp/print", False),
        ("user@printer.local", False),
        ("ftp://printer", False),
        ("ipp://", False),
        ("ipp://printer:99999/x", False),
        ("ipp://printer:abc/x", False),
    ],
)
def test_printer_address(address, ok):
    assert cf.printer_address_valid(address) is ok


# ------------------------------------------------------------------------------------------------ colors
def test_default_palette_lows_red_highs_blue():
    assert C.ZONE_COLORS == {
        "very_low": "#C62828",
        "low": "#EF5350",
        "near_low": "#FFC107",
        "tight_range": "#4CAF50",
        "near_high": "#FFC107",
        "high": "#42A5F5",
        "very_high": "#1565C0",
    }
    assert set(C.CONF_ZONE_COLORS.values()) == {f"color_{z}" for z in C.ZONE_COLORS}


def test_zone_colors_follow_the_settings():
    assert C.zone_colors({}) == {z: c.upper() for z, c in C.ZONE_COLORS.items()}
    chosen = C.zone_colors({"color_low": "#ff00aa", "color_high": "blue", "color_very_low": None})
    assert chosen["low"] == "#FF00AA" and chosen["high"] == "#42A5F5" and chosen["very_low"] == "#C62828"


def test_color_options_round_trip():
    stored = cf._colors_from_input({"color_low": [255, 0, 170], "color_high": [66, 165, 245]})
    assert stored["color_low"] == "#FF00AA" and stored["color_high"] is None  # the default is not stored
    reset = cf._colors_from_input({"colors_reset": True})
    assert all(reset[k] is None for k in C.CONF_ZONE_COLORS.values())
    assert fs._hex_to_rgb("#C62828") == [198, 40, 40]
    texts = STRINGS["options"]["step"]["displays"]  # Configure > Display card and wall panels
    assert set(texts["data"]) == set(C.CONF_ZONE_COLORS.values()) | {
        "colors_reset",
        "trend_display",
        "display_card",
        "card_background",
        "card_background_color",
        "panel_background",
        "text_outline",
        "flash_no_current_data",
        "weekly_pages",
        "regenerate_panel_webhook",
    }
    graph = texts["sections"]["graph_colors"]  # the collapsed section Graph range colors
    assert set(graph["data"]) == set(C.CONF_BAND_COLORS.values()) | {"band_colors_reset"}
    assert "displays" in STRINGS["options"]["step"]["init"]["menu_options"]


def test_colors_and_zone_word_published(tmp_path):
    c = settings_coordinator(tmp_path, {"color_high": "#123456"})
    assert c.configuration()["zone_colors"]["high"] == "#123456"
    c.current_glucose, c.current_limit = 200.0, None
    assert (c.glucose_zone, c.glucose_zone_color, c.glucose_zone_label) == ("high", "#123456", "High")
    expected = {48: "Very low", 60: "Low", 69.9: "Low", 70: None, 120: None, 160: None, 181: "High", 251: "Very high"}
    for value, word in expected.items():
        c.current_glucose = value
        assert c.glucose_zone_label == word, value
    c.current_glucose = None
    assert c.glucose_zone_label is None and c.glucose_zone_color is None


# ------------------------------------------------------------------------------------------------ date format
def test_date_format_lower_case_choices_and_codes(tmp_path):
    assert set(STRINGS["selector"]["date_format"]["options"]) == set(C.DATE_FORMAT_OPTIONS)
    assert [C.date_format_code(v) for v in ("mdy", "DMY", "ymd", "auto", None, "x")] == [
        "MDY",
        "DMY",
        "YMD",
        "auto",
        "auto",
        "auto",
    ]
    c = settings_coordinator(tmp_path, {"date_format": "dmy"}, country="US")
    cfg = c.configuration()
    assert cfg["date_format"] == "DMY" and cfg["date_format_setting"] == "DMY"  # the displays read the codes
    assert C.date_format_code("YMD") == C.date_format_code("ymd") == "YMD"  # either case reads as the code


# ------------------------------------------------------------------------------------------------ translations
TRANSLATION_KEY = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")


def test_every_selector_option_key_is_a_valid_translation_key():
    for name, sel in STRINGS["selector"].items():
        assert TRANSLATION_KEY.match(name), name
        for key in sel["options"]:
            assert TRANSLATION_KEY.match(key), (name, key)


def test_action_selectors_are_translated():
    services = yaml.safe_load((PKG / "services.yaml").read_text(encoding="utf-8"))
    for service in services.values():
        for field in (service.get("fields") or {}).values():
            select = (field.get("selector") or {}).get("select")
            if select:
                key = select.get("translation_key")
                assert key in STRINGS["selector"], field
                assert set(map(str, select["options"])) == set(STRINGS["selector"][key]["options"])


def test_a_fixable_issue_has_its_text_in_the_fix_flow_only():
    assert "terms_of_use" not in STRINGS["issues"]  # no Terms of Use, no acceptance
    assert "description" not in STRINGS["issues"]["clock_behind"]
    for issue, text in STRINGS["issues"].items():
        assert ("fix_flow" in text) != ("description" in text), issue


# ------------------------------------------------------------------------------------------------ wording
CUSTOM = (
    "The defaults are the international consensus ranges and targets (Battelino et al., 2019). Ranges and goals "
    "that differ from them are custom settings."
)


def test_custom_ranges_statement_replaces_the_instruction():
    assert "Consult your healthcare team" not in BLOB
    for step in ("ranges", "goals"):  # Configure only: the setup has no ranges and goals screens
        assert step not in STRINGS["config"]["step"], step
        assert STRINGS["options"]["step"][step]["description"].startswith(CUSTOM + "\n\n"), step
        assert STRINGS["options"]["step"][step]["description"].count("The defaults are") == 1


def test_goal_labels_match_the_operators(tmp_path):
    goals = settings_coordinator(tmp_path).configuration()["goals"]
    words = {">": "more than", ">=": "at least", "<": "less than", "<=": "at most"}
    labels = STRINGS["options"]["step"]["goals"]["data"]
    assert labels["goal_time_in_target_range"] == f"Time in range: {words[goals['time_in_range']['op']]}"
    assert labels["goal_cv"] == f"Coefficient of variation (CV): {words[goals['cv']['op']]}"


def test_no_android_and_designed_to():
    assert "Android" not in BLOB
    assert "One test notification is sent" not in BLOB and "takes over when it stops" not in BLOB
    assert "never shown as HI" not in BLOB and "The decision is the user's: It" not in BLOB
    assert "Secondary source:" not in BLOB


# ------------------------------------------------------------------------------------------------ panel feed
class _Entries:
    """Home Assistant's config entries: records each data update."""

    def __init__(self):
        self.updates = []

    def async_update_entry(self, entry, data):
        """Store the entry's new data and record it."""
        entry.data = data
        self.updates.append(data)


def _feed(data, monkeypatch):
    created = []
    monkeypatch.setattr(
        pf.persistent_notification, "async_create", lambda hass, message, **kw: created.append(message), raising=False
    )
    hass = types.SimpleNamespace(config_entries=_Entries())
    entry = types.SimpleNamespace(data=dict(data))
    return pf.ensure_webhook_id(hass, entry), entry, created


def test_no_panel_address_at_the_first_setup(monkeypatch):
    webhook_id, entry, created = _feed({}, monkeypatch)
    assert webhook_id is None and created == [] and C.CONF_PANEL_WEBHOOK_ID not in entry.data


def test_panel_address_created_on_request_without_the_id_in_the_notification(monkeypatch):
    webhook_id, entry, created = _feed({C.CONF_PANEL_FEED: True}, monkeypatch)
    assert re.fullmatch(r"glucocraft_panel_[0-9a-f]{32}", webhook_id)
    assert entry.data[C.CONF_PANEL_WEBHOOK_ID] == webhook_id
    assert len(created) == 1 and webhook_id not in created[0] and "not an alarm" not in created[0]
    assert "earlier" not in created[0]  # the first address: no earlier one stops working
    assert webhook_id in cf._panel_address_text(entry.data)  # shown on the settings screen (admins)


def test_existing_panel_address_kept(monkeypatch):
    webhook_id, _, created = _feed({C.CONF_PANEL_WEBHOOK_ID: "glucocraft_panel_old"}, monkeypatch)
    assert webhook_id == "glucocraft_panel_old" and created == []


def test_security_properties_of_the_views_and_the_webhook():
    tree = ast.parse((PKG / "http_api.py").read_text(encoding="utf-8"))
    views = [
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef)
        and any(getattr(b, "id", getattr(b, "attr", "")) == "HomeAssistantView" for b in n.bases)
    ]
    assert len(views) >= 5
    for view in views:
        auth = [s for s in view.body if isinstance(s, ast.Assign) and s.targets[0].id == "requires_auth"]
        assert auth and auth[0].value.value is True, view.name
    src = (PKG / "panel_feed.py").read_text(encoding="utf-8")
    assert "local_only=True, allowed_methods=_HANDLED_METHODS" in src and "secrets.token_hex(16)" in src
    # read-only: every method but GET is refused by the handler itself (tests/test_settings_other.py)
    assert 'if request.method != "GET":\n            return web.Response(status=405' in src


# ------------------------------------------------------------------------------------------------ entities
def test_one_service_device_and_parallel_updates():
    from glucocraft import binary_sensor as bs
    from glucocraft import entity
    from glucocraft import sensor as se

    info = entity.device_info(types.SimpleNamespace(entry_id="e1", integration_version="0.0.1"))
    assert info["identifiers"] == {("glucocraft", "e1")} and "entry_type" in info
    assert info["sw_version"] == "0.0.1"  # the manifest version, the same for both platforms (no module state)
    assert entity.device_info(types.SimpleNamespace(entry_id="e1", integration_version=""))["sw_version"] is None
    assert not hasattr(entity, "set_version") and "_VERSION" not in vars(entity)
    assert "DeviceEntryType.SERVICE" in (PKG / "entity.py").read_text(encoding="utf-8")
    assert se.PARALLEL_UPDATES == 0 and bs.PARALLEL_UPDATES == 0
    for mod in ("sensor.py", "binary_sensor.py"):
        text = (PKG / mod).read_text(encoding="utf-8")
        assert "entry.runtime_data" in text and "hass.data[DOMAIN]" not in text


def test_neutral_entity_names_and_states(tmp_path):
    src = (PKG / "sensor.py").read_text(encoding="utf-8")
    names = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))["entity"]["sensor"]
    assert "period_risk_score" not in names and "smoothed" not in names and "delta" not in names
    assert "Risk Score" not in json.dumps(names)
    assert 'return "active"' not in src.lower() and 'return "not_expired"' in src
    assert names["sensor_expiry"]["state"]["not_expired"] == "Not expired"
    assert "mdi:check-circle" not in (PKG / "binary_sensor.py").read_text(encoding="utf-8")
    assert C.status_label("current") == "Current data"


def test_frontend_yaml_mode_loads_the_cards_as_modules(monkeypatch):
    import asyncio
    import sys

    from glucocraft import frontend as fe

    added = []
    monkeypatch.setattr(
        sys.modules["homeassistant.components.frontend"],
        "add_extra_js_url",
        lambda hass, url: added.append(url),
        raising=False,
    )
    removed = []
    monkeypatch.setattr(
        sys.modules["homeassistant.components.frontend"],
        "remove_extra_js_url",
        lambda hass, url: removed.append(url),
        raising=False,
    )
    hass = types.SimpleNamespace(data={"lovelace": types.SimpleNamespace(resources=types.SimpleNamespace())})
    base = "/glucocraft_v/0123456789"
    asyncio.run(fe._async_register_lovelace_resources(hass, base))
    assert added == [f"{base}/{card}" for card in fe.CARD_FILES]
    asyncio.run(fe._async_register_lovelace_resources(hass, base))  # once per run
    assert len(added) == len(fe.CARD_FILES)
    asyncio.run(fe.async_unregister_lovelace_resources(hass))  # the unload removes exactly the URLs added
    assert removed == added and "glucocraft_extra_js" not in hass.data


# ------------------------------------------------------------------------------------------------ license
def test_license_is_version_3_only():
    assert C.LICENSE_NAME.endswith("(AGPL-3.0-only)")
    assert "any later version" not in " ".join(C.GNU_NOTICE)
    assert "version 3 of the License" in C.GNU_NOTICE[0]


def test_prebuilt_panel_firmware_is_not_distributed():
    readme = " ".join((REPO / "README.md").read_text(encoding="utf-8").split())
    assert (
        "Prebuilt wall-panel firmware is not distributed: users build it from the published example panel "
        "configurations; the ESPHome software it contains has its own license."
    ) in readme


# The first line of every source file of the integration, the dashboard files, the tests and the published tools, and
# of the example panel configurations (with their headers, the text catalogue, the generated texts and the panel tools).
LICENSE_LINE = "License: AGPL-3.0-only (LICENSE)."
PANEL_SOURCES = (
    "glucocraft-panel.yaml",
    "glucocraft-waveshare-lcd5.yaml",
    "glucocraft-waveshare-p4-7b.yaml",
    "glucocraft-waveshare-lcd7.yaml",
    "glucocraft-guition-35.yaml",
    "glucocraft_panel_binary.h",
    "gc_units.h",
    "lvgl_compat.h",
    "i18n/en.yaml",
    "i18n/generated/en/gc_text.h",
    "i18n/generated/en/texts.yaml",
    "tools/make_panel_texts.py",
    "tools/worst_case_fit.py",
    "tools/make_glucocraft_landscape.py",
)
# The published tools of the repository (the test suite's own files are found by a glob).
PUBLISHED_TOOLS = ("tools/i18n/make_pseudo.py", "tools/check_pdf_ua.py", "tools/make_brand_icon.py")


def _first_line(path: Path) -> str:
    return path.read_text(encoding="utf-8").split("\n", 1)[0]


def test_every_source_file_starts_with_the_license_line():
    files = [*sorted(PKG.glob("*.py")), *sorted((PKG / "www").glob("*.js"))]
    assert len(files) > 30
    for path in files:
        mark = "# " if path.suffix == ".py" else "// "
        assert _first_line(path) == mark + LICENSE_LINE, path.name


def test_every_test_and_published_tool_starts_with_the_license_line():
    files = [
        *sorted((REPO / "tests").rglob("*.py")),
        *sorted((REPO / "tools" / "synth").rglob("*.py")),
        *(REPO / name for name in PUBLISHED_TOOLS),
    ]
    files = [p for p in files if "__pycache__" not in p.parts]
    assert len(files) > 100
    for path in files:
        assert _first_line(path) == "# " + LICENSE_LINE, path.relative_to(REPO).as_posix()


def test_every_panel_source_file_starts_with_the_license_line():
    from conftest import NO_PANELS, PANELS

    if PANELS is None:
        pytest.skip(NO_PANELS)
    for name in PANEL_SOURCES:
        mark = "// " if name.endswith(".h") else "# "
        assert _first_line(PANELS / name) == mark + LICENSE_LINE, name
