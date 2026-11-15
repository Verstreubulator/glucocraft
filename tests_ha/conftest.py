# License: AGPL-3.0-only (LICENSE).
"""Fixtures and helpers of the tests on Home Assistant itself: the recorder (an in-memory database) and the persistent
notifications, as every Home Assistant has them; a config folder of each test's own (GlucoCraft writes its readings
file, exports and reports there); a source entity; the setup flow run to its end; the Configure screens, saved the way
the frontend sends a form (every field with its shown value, changed fields on top)."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, section
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_fire_time_changed

# The repository's custom_components folder, imported before Home Assistant mounts the test package's own one (the
# repository root is on the path: pytest.ini)
import custom_components  # noqa: F401

DOMAIN = "glucocraft"
SOURCE = "sensor.test_cgm_glucose"
LAST_READING = "sensor.glucocraft_last_glucose_reading"


@pytest.fixture(autouse=True)
async def own_config_folder(recorder_mock, enable_custom_integrations, hass: HomeAssistant, tmp_path, caplog):
    """The recorder (requested before hass, as Home Assistant's fixtures require), custom_components/glucocraft from
    the repository and the persistent notifications; every file GlucoCraft writes goes to the test's own folder.
    Afterwards: GlucoCraft logged no error."""
    assert await async_setup_component(hass, "persistent_notification", {})
    hass.config.config_dir = str(tmp_path)
    yield tmp_path
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR and "glucocraft" in r.name]
    assert not errors, errors


def set_source(hass: HomeAssistant, value: Any = "120", unit: str | None = "mg/dL") -> None:
    """The source entity's state, as a CGM integration writes it."""
    attrs: dict[str, Any] = {"state_class": "measurement"}
    if unit is not None:
        attrs["unit_of_measurement"] = unit
    hass.states.async_set(SOURCE, value, attrs)


async def new_reading(hass: HomeAssistant, freezer, value: Any, unit: str | None = "mg/dL") -> None:
    """The source's next reading, 5 minutes after the one before (the clock moves on, as with a CGM). GlucoCraft
    stores it in an executor job started from the state listener, which Home Assistant counts as a background task,
    and hands the update back to the event loop (call_soon_threadsafe): both are waited for."""
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    set_source(hass, value, unit)
    await hass.async_block_till_done(wait_background_tasks=True)
    await hass.async_block_till_done()


async def run_setup(hass: HomeAssistant, unit: str | None = None, kept: str | None = None) -> ConfigEntry:
    """The whole setup flow: the disclaimer screen, the answer about readings an earlier installation kept (`kept`:
    "keep" or "delete", only when there are some), the source chosen, the last screen (with `unit` when given); the
    created entry, loaded."""
    flow = hass.config_entries.flow
    result = await flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await flow.async_configure(result["flow_id"], {})
    if kept is not None:
        assert result["type"] is FlowResultType.FORM and result["step_id"] == "kept", result
        result = await flow.async_configure(result["flow_id"], {"kept_readings": kept})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "source"
    result = await flow.async_configure(result["flow_id"], {"glucose_entity": SOURCE, "second_source": {}})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "finish"
    finish: dict[str, Any] = {"notify_enabled": False, "show_dashboard": False}
    if unit is not None:
        finish["unit"] = unit
    result = await flow.async_configure(result["flow_id"], finish)
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()
    return result["result"]


async def open_screen(hass: HomeAssistant, entry: ConfigEntry, screen: str) -> dict:
    """Open Configure and choose `screen` from its menu; the screen's form."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU and result["step_id"] == "init"
    assert screen in result["menu_options"], result["menu_options"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": screen})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == screen, result
    return result


def shown_values(schema) -> dict[str, Any]:
    """The value of every field as the form shows it: its default, else its suggested value (fields with neither
    are left out, as the frontend leaves out an empty field); a section's fields nested under its key."""
    values: dict[str, Any] = {}
    for key, value in schema.schema.items():
        if isinstance(value, section):
            values[str(key)] = shown_values(value.schema)
            continue
        shown = key.default() if callable(getattr(key, "default", None)) else None
        if shown is None:
            shown = (getattr(key, "description", None) or {}).get("suggested_value")
        if shown is not None:
            values[str(key)] = shown
    return values


async def save_screen(hass: HomeAssistant, entry: ConfigEntry, screen: str, changes: dict[str, Any]) -> dict:
    """Open `screen` in Configure and save it with `changes` on top of the shown values (a section's changes are
    merged into the section); the flow's last result (GlucoCraft reloads)."""
    form = await open_screen(hass, entry, screen)
    values = shown_values(form["data_schema"])
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(values.get(key), dict):
            values[key] = {**values[key], **value}
        else:
            values[key] = value
    result = await hass.config_entries.options.async_configure(form["flow_id"], values)
    await hass.async_block_till_done()
    return result


def display_precision(hass: HomeAssistant, entity_id: str) -> int | None:
    """The number of decimals the frontend shows for a sensor (Home Assistant's display precision in the registry)."""
    options = er.async_get(hass).async_get(entity_id).options.get("sensor") or {}
    return options.get("display_precision", options.get("suggested_display_precision"))


def entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> set[str]:
    """The entity ids of the entry in Home Assistant's entity registry."""
    return {e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}
