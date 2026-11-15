# License: AGPL-3.0-only (LICENSE).
"""The Configure screens on Home Assistant's options flow.

Rules under test:
- The menu lists the screens; Glucose ranges and Custom goals only while Custom ranges and goals is on.
- Every screen opens and saves its own fields unchanged; GlucoCraft reloads and stays loaded.
- Both optional features on: 134 entities; off again: 28.
- The display unit switch: every glucose sensor changes to mmol/L at once, shown with one decimal; and back.
- A printer address with a user name is refused on its field.
"""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from conftest import LAST_READING, display_precision, entity_ids, new_reading, run_setup, save_screen, set_source
from custom_components.glucocraft.settings import ranges, setting

RANGE_KEYS = (
    "range_critical_low",
    "range_low",
    "range_tight_low",
    "range_tight_high",
    "range_high",
    "range_critical_high",
)
BOTH_ON = {"custom_ranges_and_goals": True, "separate_statistics": True}


def _in_effect(data, key: str):
    """A setting as GlucoCraft reads it (settings.py): a default stored explicitly, or an empty text stored as "" by
    Configure where the setup stored nothing, is the same setting."""
    if key in RANGE_KEYS:
        return ranges(data)[key]
    try:
        return setting(data, key)
    except KeyError:  # not a setting with a default (the sources, the wall-panel address)
        return data.get(key) or None


async def _installed(hass: HomeAssistant):
    set_source(hass, "100")
    return await run_setup(hass)


async def test_the_menu_shows_the_custom_screens_only_while_the_feature_is_on(hass: HomeAssistant):
    entry = await _installed(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["sources", "units", "features", "displays", "notifications", "report"]
    hass.config_entries.options.async_abort(result["flow_id"])
    await save_screen(hass, entry, "features", {"custom_ranges_and_goals": True, "separate_statistics": False})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == [
        "sources",
        "units",
        "features",
        "ranges",
        "goals",
        "displays",
        "notifications",
        "report",
    ]
    hass.config_entries.options.async_abort(result["flow_id"])


async def test_every_screen_saves_its_fields_and_glucocraft_reloads(hass: HomeAssistant):
    entry = await _installed(hass)
    await save_screen(hass, entry, "features", {"custom_ranges_and_goals": True, "separate_statistics": False})
    for screen in ("sources", "units", "ranges", "goals", "displays", "notifications", "report"):
        before = dict(entry.data)
        result = await save_screen(hass, entry, screen, {})  # every field as shown: nothing stored changes
        assert result["type"] is FlowResultType.CREATE_ENTRY, (screen, result)
        assert entry.state is ConfigEntryState.LOADED, screen
        keys = set(before) | set(entry.data)
        changed = {k for k in keys if _in_effect(before, k) != _in_effect(entry.data, k)}
        assert not changed, (screen, {k: (before.get(k), entry.data.get(k)) for k in changed})


async def test_both_optional_features_give_134_entities(hass: HomeAssistant):
    entry = await _installed(hass)
    assert len(entity_ids(hass, entry)) == 28
    await save_screen(hass, entry, "features", BOTH_ON)
    assert entry.data["custom_ranges_and_goals"] is True and entry.data["separate_statistics"] is True
    assert len(entity_ids(hass, entry)) == 134
    await save_screen(hass, entry, "features", {"custom_ranges_and_goals": False, "separate_statistics": False})
    assert len(entity_ids(hass, entry)) == 28


def _glucose_sensors(hass: HomeAssistant, entry) -> list[er.RegistryEntry]:
    """The registry entries of the entry's sensors with the blood glucose device class."""
    entries = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    return [e for e in entries if e.original_device_class == "blood_glucose_concentration"]


@pytest.mark.parametrize("stats", [False, True])
async def test_the_display_unit_switch(hass: HomeAssistant, freezer, stats: bool):
    """Every glucose sensor: Last Glucose Reading and 5-Minute Delta, and with Separate statistics
    entities the mean, SD, minimum and maximum of each of the 7 periods."""
    entry = await _installed(hass)
    if stats:
        await save_screen(hass, entry, "features", {"separate_statistics": True})
    glucose = _glucose_sensors(hass, entry)
    assert len(glucose) == 2 + 7 * 4 * stats
    assert all(e.options["sensor"]["unit_of_measurement"] == "mg/dL" for e in glucose)
    await new_reading(hass, freezer, "121")
    assert float(hass.states.get(LAST_READING).state) == 121 and display_precision(hass, LAST_READING) == 0
    await save_screen(hass, entry, "units", {"unit": "mmol/L"})
    assert entry.data["unit"] == "mmol/L"
    state = hass.states.get(LAST_READING)
    assert state.attributes["unit_of_measurement"] == "mmol/L" and round(float(state.state), 1) == 6.7
    assert display_precision(hass, LAST_READING) == 1
    glucose = _glucose_sensors(hass, entry)
    assert len(glucose) == 2 + 7 * 4 * stats
    assert all(e.options["sensor"]["unit_of_measurement"] == "mmol/L" for e in glucose)
    assert all(display_precision(hass, e.entity_id) == 1 for e in glucose)
    await save_screen(hass, entry, "units", {"unit": "mg/dL"})
    state = hass.states.get(LAST_READING)
    assert state.attributes["unit_of_measurement"] == "mg/dL" and float(state.state) == 121
    assert display_precision(hass, LAST_READING) == 0


async def test_a_printer_address_with_a_user_name_is_refused(hass: HomeAssistant):
    entry = await _installed(hass)
    result = await save_screen(hass, entry, "report", {"printer_address": "ipp://user@printer.local/ipp/print"})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"printer_address": "printer_invalid"}
    hass.config_entries.options.async_abort(result["flow_id"])
    assert "printer_address" not in entry.data or not entry.data["printer_address"]
