# License: AGPL-3.0-only (LICENSE).
"""Setup, the entity set, the units and precision of the glucose sensors, unload and removal on Home Assistant
itself.

Rules under test:
- A new installation has 28 entities; the actions are registered.
- The glucose sensors are in the display unit, set in their entity settings, and Home Assistant shows them at its own
  display precision: whole mg/dL, and one decimal in mmol/L (Home Assistant scales the precision from mg/dL, the
  native unit; unit_registry.py).
- Unloading leaves the entities unavailable and keeps the entry; a reload loads it again.
- Removal deletes the entry and its entities. With Delete everything no file of GlucoCraft is left; with Keep the readings
  for a reinstall (the default) the readings stay in the folder glucocraft, a notification says so, and the next setup
  asks whether to keep (adopt) or delete them.
- An export read back in after a reinstall (glucocraft.import_csv): readings already stored win.
"""

from __future__ import annotations

import pytest
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from conftest import (
    DOMAIN,
    LAST_READING,
    display_precision,
    entity_ids,
    new_reading,
    run_setup,
    save_screen,
    set_source,
)


async def test_a_new_installation_has_28_entities_and_the_actions(hass: HomeAssistant):
    set_source(hass)
    entry = await run_setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    ids = entity_ids(hass, entry)
    assert len(ids) == 28, sorted(ids)
    assert sum(i.startswith("binary_sensor.") for i in ids) == 9
    assert LAST_READING in ids
    for action in ("send_daily_summary", "add_lab_a1c", "print_report", "export_csv", "import_csv"):
        assert hass.services.has_service(DOMAIN, action), action


async def test_mg_dl_is_shown_in_whole_numbers(hass: HomeAssistant, freezer):
    set_source(hass, "100")
    await run_setup(hass)
    await new_reading(hass, freezer, "123")
    state = hass.states.get(LAST_READING)
    assert state.attributes["unit_of_measurement"] == "mg/dL"
    assert float(state.state) == 123
    assert er.async_get(hass).async_get(LAST_READING).options["sensor"]["unit_of_measurement"] == "mg/dL"
    assert display_precision(hass, LAST_READING) == 0


async def test_mmol_l_is_shown_with_one_decimal(hass: HomeAssistant, freezer):
    """A source in mmol/L, stored as mg/dL, is shown in mmol/L with one decimal, not as a whole number (Home
    Assistant's own precision rule, which a stand-in cannot model)."""
    set_source(hass, "5.5", "mmol/L")
    await run_setup(hass)
    await new_reading(hass, freezer, "6.7", "mmol/L")
    state = hass.states.get(LAST_READING)
    assert state.attributes["unit_of_measurement"] == "mmol/L"
    assert round(float(state.state), 1) == 6.7
    registry = er.async_get(hass)
    for entity_id in (LAST_READING, "sensor.glucocraft_5_minute_delta"):
        assert registry.async_get(entity_id).options["sensor"]["unit_of_measurement"] == "mmol/L", entity_id
        assert display_precision(hass, entity_id) == 1, entity_id


async def test_unload_and_reload(hass: HomeAssistant):
    set_source(hass)
    entry = await run_setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get(LAST_READING).state == STATE_UNAVAILABLE
    assert hass.config_entries.async_entries(DOMAIN) == [entry]
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(LAST_READING).state != STATE_UNAVAILABLE


async def test_removal_with_delete_everything_leaves_no_entry_entity_or_file(
    hass: HomeAssistant, own_config_folder, freezer
):
    set_source(hass)
    entry = await run_setup(hass)
    await save_screen(hass, entry, "report", {"on_removal": "delete"})
    await new_reading(hass, freezer, "130")
    await hass.services.async_call(DOMAIN, "export_csv", {}, blocking=True, return_response=True)
    await hass.async_block_till_done()
    folders = [p.name for p in own_config_folder.iterdir() if p.name.startswith("glucocraft")]
    assert "glucocraft_exports" in folders, folders
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.config_entries.async_entries(DOMAIN)
    assert not entity_ids(hass, entry)
    assert not [p.name for p in own_config_folder.iterdir() if p.name.startswith("glucocraft")]


@pytest.mark.parametrize("answer", ["keep", "delete"])
async def test_removal_keeps_the_readings_and_a_reinstall_asks(hass: HomeAssistant, own_config_folder, freezer, answer):
    """The default, Keep the readings for a reinstall: the readings file stays in the folder glucocraft under its kept
    name and a notification says so; the next setup asks, and keeps (adopts) or deletes them."""
    set_source(hass)
    entry = await run_setup(hass)
    for value in ("130", "140", "150"):
        await new_reading(hass, freezer, value)
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    folder = own_config_folder / "glucocraft"
    assert sorted(p.name for p in folder.iterdir()) == [
        "glucocraft_kept.bin",
        "glucocraft_kept_lab_a1c.json",
        "glucocraft_kept_sensor_events.json",
    ]
    assert not (own_config_folder / "glucocraft_exports").exists()
    assert "glucocraft_kept_readings" in persistent_notification._async_get_or_create_notifications(hass)
    again = await run_setup(hass, kept=answer)
    names = sorted(p.name for p in folder.iterdir()) if folder.exists() else []
    if answer == "keep":
        assert names == [f"glucocraft_{again.entry_id}.bin"]
    else:
        assert "glucocraft_kept.bin" not in names
    assert "glucocraft_kept_readings" not in persistent_notification._async_get_or_create_notifications(hass)


async def test_import_reads_an_export_back_in(hass: HomeAssistant, own_config_folder, freezer):
    set_source(hass)
    entry = await run_setup(hass)
    for value in ("130", "140", "150"):
        await new_reading(hass, freezer, value)
    exported = await hass.services.async_call(DOMAIN, "export_csv", {}, blocking=True, return_response=True)
    text = (own_config_folder / "glucocraft_exports" / exported["files"][0]).read_text(encoding="utf-8")
    await save_screen(hass, entry, "report", {"on_removal": "delete"})
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    (own_config_folder / "glucocraft_exports").mkdir()
    # the export, copied out before the removal, copied back in after the reinstall
    (own_config_folder / "glucocraft_exports" / exported["files"][0]).write_text(text, encoding="utf-8")
    await run_setup(hass)
    result = await hass.services.async_call(DOMAIN, "import_csv", {}, blocking=True, return_response=True)
    assert (
        result["file"] == exported["files"][0] and result["imported"] + result["already_stored"] == exported["readings"]
    )
