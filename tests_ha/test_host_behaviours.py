# License: AGPL-3.0-only (LICENSE).
"""What Home Assistant itself does with GlucoCraft: setup refusals, the entity registry, the binary sensors, the
actions, the diagnostics, the HTTP views and the webhook, reloads and an entry of a newer version.

Rules under test:
- The setup refuses a second source that is the primary source itself, and a source whose unit is not a glucose unit.
- The entity registry: exactly the technical entities are diagnostic; the Spoken summary and the five binary sensors
  that no display reads are disabled by default.
- A binary sensor's state is "on" or "off"; the range binary sensors state their threshold in the display unit, also
  after the unit is switched.
- An action called while GlucoCraft is not loaded is refused with a message, not an exception of the code.
- The diagnostics redact the source entities.
- The HTTP views need a Home Assistant login; the wall-panel address answers HEAD without the file.
- Three reloads leave the same number of event listeners as one setup.
- An entry written by a newer GlucoCraft is not set up.
"""

from __future__ import annotations

import json

import pytest
from homeassistant.components.diagnostics import REDACTED
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from conftest import DOMAIN, SOURCE, new_reading, run_setup, save_screen, set_source
from custom_components.glucocraft import diagnostics
from custom_components.glucocraft.const import ENTRY_MINOR_VERSION, ENTRY_VERSION

BELOW = "binary_sensor.glucocraft_glucose_below_range"
NO_DATA = "binary_sensor.glucocraft_no_current_data"


async def _source_screen(hass: HomeAssistant) -> dict:
    flow = hass.config_entries.flow
    result = await flow.async_init(DOMAIN, context={"source": "user"})
    result = await flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "source", result
    return result


async def test_a_second_source_equal_to_the_primary_is_refused(hass: HomeAssistant):
    set_source(hass)
    result = await _source_screen(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"glucose_entity": SOURCE, "second_source": {"secondary_glucose_entity": SOURCE}}
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "source"
    assert "secondary_same_as_primary" in result["errors"].values()


async def test_a_source_with_a_unit_other_than_glucose_is_refused(hass: HomeAssistant):
    set_source(hass, "21.5", unit="°C")
    result = await _source_screen(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"glucose_entity": SOURCE, "second_source": {}}
    )
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"glucose_entity": "source_unit_not_glucose"}


async def test_the_registry_categories_and_the_entities_disabled_by_default(hass: HomeAssistant):
    set_source(hass)
    entry = await run_setup(hass)
    entries = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    diagnostic = {e.entity_id for e in entries if e.entity_category is EntityCategory.DIAGNOSTIC}
    assert diagnostic == {
        "sensor.glucocraft_configuration",
        "sensor.glucocraft_active_source",
        "sensor.glucocraft_data_history",
        "sensor.glucocraft_sensor_expiry",
        "binary_sensor.glucocraft_sensor_warm_up",
    }
    disabled = {e.entity_id for e in entries if e.disabled_by is er.RegistryEntryDisabler.INTEGRATION}
    assert disabled == {
        "sensor.glucocraft_spoken_summary",
        "binary_sensor.glucocraft_glucose_above_range",
        "binary_sensor.glucocraft_glucose_very_low",
        "binary_sensor.glucocraft_glucose_very_high",
        "binary_sensor.glucocraft_glucose_in_range",
        "binary_sensor.glucocraft_glucose_in_tight_range",
    }


async def test_binary_states_and_the_threshold_after_a_unit_switch(hass: HomeAssistant, freezer):
    set_source(hass, "100")
    entry = await run_setup(hass)
    await new_reading(hass, freezer, "60")
    below = hass.states.get(BELOW)
    assert below.state == "on" and hass.states.get(NO_DATA).state == "off"
    assert (below.attributes["threshold"], below.attributes["unit"]) == (70, "mg/dL")
    await save_screen(hass, entry, "units", {"unit": "mmol/L"})
    below = hass.states.get(BELOW)
    assert below.state == "on" and (below.attributes["threshold"], below.attributes["unit"]) == (3.9, "mmol/L")


async def test_an_action_while_glucocraft_is_not_loaded_is_refused(hass: HomeAssistant):
    set_source(hass)
    entry = await run_setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "export_csv", {}, blocking=True)
    assert err.value.translation_key == "not_loaded"


async def test_the_diagnostics_redact_the_sources(hass: HomeAssistant):
    set_source(hass)
    entry = await run_setup(hass)
    out = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
    assert out["settings"]["glucose_entity"] == REDACTED
    assert SOURCE not in json.dumps(out, default=str)


async def test_the_views_need_a_login_and_the_address_answers_head_without_the_file(
    hass: HomeAssistant, hass_client, hass_client_no_auth, freezer
):
    set_source(hass, "100")
    entry = await run_setup(hass)
    await new_reading(hass, freezer, "110")
    anonymous = await hass_client_no_auth()
    assert (await anonymous.get("/api/glucocraft/stats")).status == 401
    assert (await (await hass_client()).get("/api/glucocraft/stats")).status == 200
    await save_screen(hass, entry, "displays", {"regenerate_panel_webhook": True})
    response = await anonymous.head(f"/api/webhook/{entry.data['panel_webhook_id']}", params={"file": "backfill"})
    assert response.status == 200 and not await response.read()


async def test_reloads_leave_no_listener_behind(hass: HomeAssistant):
    set_source(hass)
    entry = await run_setup(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    once = hass.bus.async_listeners()  # after the first start's one-time listeners have run
    for _ in range(3):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    after = hass.bus.async_listeners()
    grown = {event: n for event, n in after.items() if n > once.get(event, 0)}
    assert not grown, grown  # no event has more listeners (a listener left by an unload would add one each time)


async def test_an_entry_of_a_newer_glucocraft_is_not_set_up(hass: HomeAssistant, caplog):
    set_source(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=ENTRY_VERSION + 1,
        minor_version=ENTRY_MINOR_VERSION,
        data={"glucose_entity": SOURCE},
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert hass.states.get("sensor.glucocraft_last_glucose_reading") is None
    caplog.clear()  # the error this test expects
