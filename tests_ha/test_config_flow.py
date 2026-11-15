# License: AGPL-3.0-only (LICENSE).
"""The setup flow on Home Assistant's flow manager.

Rules under test:
- The disclaimer is the first screen, as plain text with no field; Submit continues, and the entry records nothing
  of it.
- A source that does not exist is refused on its field; GlucoCraft's own entities are not offered (the entity
  selector refuses them).
- The display unit starts as the source's own unit (mmol/L for a source in mmol/L).
- One installation per Home Assistant: a second setup is aborted.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from conftest import DOMAIN, LAST_READING, SOURCE, run_setup, set_source


async def test_the_disclaimer_is_the_first_screen_and_submit_continues(hass: HomeAssistant):
    set_source(hass)
    flow = hass.config_entries.flow
    result = await flow.async_init(DOMAIN, context={"source": "user"})
    assert result["step_id"] == "user" and result["description_placeholders"]["disclaimer"].startswith(
        "**For development and testing only.**"
    )
    result = await flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "source"
    assert not hass.config_entries.async_entries(DOMAIN)
    flow.async_abort(result["flow_id"])


async def test_a_setup_records_no_acceptance(hass: HomeAssistant):
    set_source(hass)
    entry = await run_setup(hass)
    assert entry.title == "GlucoCraft" and entry.data["glucose_entity"] == SOURCE
    assert not {"terms_version", "terms_accepted_at", "disclaimer_accepted"} & set(entry.data)
    assert entry.data["unit"] == "mg/dL"
    assert entry.data["custom_ranges_and_goals"] is False and entry.data["separate_statistics"] is False


async def test_a_missing_source_or_glucocrafts_own_entity_is_refused(hass: HomeAssistant):
    set_source(hass)
    await run_setup(hass)
    # GlucoCraft's own entities exist only once it is installed: tested in Configure > Glucose sources
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "sources"})
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {"glucose_entity": LAST_READING, "second_source": {}}
        )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"glucose_entity": "sensor.does_not_exist", "second_source": {}}
    )
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"glucose_entity": "entity_not_found"}
    hass.config_entries.options.async_abort(result["flow_id"])
    assert entry.data["glucose_entity"] == SOURCE


async def test_the_setup_source_screen_refuses_a_missing_source(hass: HomeAssistant):
    flow = hass.config_entries.flow
    result = await flow.async_init(DOMAIN, context={"source": "user"})
    result = await flow.async_configure(result["flow_id"], {})
    result = await flow.async_configure(result["flow_id"], {"glucose_entity": SOURCE, "second_source": {}})
    assert result["step_id"] == "source" and result["errors"] == {"glucose_entity": "entity_not_found"}
    flow.async_abort(result["flow_id"])


async def test_the_display_unit_starts_as_the_sources_unit(hass: HomeAssistant):
    set_source(hass, "6.7", "mmol/L")
    entry = await run_setup(hass)
    assert entry.data["unit"] == "mmol/L"


async def test_a_second_installation_is_aborted(hass: HomeAssistant):
    set_source(hass)
    await run_setup(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "single_instance_allowed"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
