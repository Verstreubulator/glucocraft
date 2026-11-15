# License: AGPL-3.0-only (LICENSE).
"""GlucoCraft follows a change of its sources' entity ids.

A new entity id for the glucose source, the second source or the sensor expiry entity (a user's rename, or a new name
for the device, which can change the ids of all its entities) is still the same sensor, so there is nothing for the
user to decide: the entity registry's event names the old and the new id of one entry, and the setting follows. A
source that is really gone keeps its missing-source notice (coordinator._check_source_notices).

How: a change of a configured id waits until no entity id has changed for SETTLE_SECONDS (a new name for a device
changes the ids one by one, so that two sources of one device give one change); then the coordinator carries over what
is kept per source (coordinator.follow_source_renames), the entry takes the new ids and reloads, as after a change in
the settings. The readings stay in their file throughout, and the recorder keeps the history under the new id.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later

from .const import CONF_GLUCOSE_ENTITY, CONF_SECONDARY_ENTITY, CONF_SENSOR_EXPIRY_ENTITY
from .settings import known_entry_data

SOURCE_KEYS = (CONF_GLUCOSE_ENTITY, CONF_SECONDARY_ENTITY, CONF_SENSOR_EXPIRY_ENTITY)
SETTLE_SECONDS = 2


def followed_data(data: Mapping[str, Any], renames: Mapping[str, str]) -> dict[str, Any]:
    """Return the entry's data with the renamed sources' new ids."""
    return {**data, **{k: renames[data[k]] for k in SOURCE_KEYS if data.get(k) in renames}}


def add_rename(pending: dict[str, str], configured: Mapping[str, Any], old: str, new: str) -> None:
    """Add one change of an id to `pending` ({id in the settings: id now}), which stays as it is for an entity that is
    not a source. A second change of one id continues the first; back at the id in the settings, nothing is left to
    do."""
    start = next((first for first, now in pending.items() if now == old), None)
    if start is None and old in [configured.get(k) for k in SOURCE_KEYS]:
        start = old
    if start is None:
        return
    if new == start:
        del pending[start]
    else:
        pending[start] = new


@callback
def async_setup_source_rename_watch(hass: HomeAssistant, entry: ConfigEntry, coordinator: Any) -> None:
    """Follow the sources' new ids until the entry unloads."""
    pending: dict[str, str] = {}
    cancel = None

    @callback
    def _settled(_now) -> None:
        """Carry the settings over to the ids now, after the wait."""
        nonlocal cancel
        cancel = None
        renames = dict(pending)
        pending.clear()
        if not renames:
            return
        coordinator.follow_source_renames(renames)
        hass.config_entries.async_update_entry(entry, data=known_entry_data(followed_data(entry.data, renames)))
        hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))

    @callback
    def _registry_updated(event) -> None:
        """Any changed id restarts the wait while a source's change is waiting: a device's ids change one by one."""
        nonlocal cancel
        data = event.data
        if data.get("action") != "update" or "old_entity_id" not in data:
            return
        add_rename(pending, entry.data, data["old_entity_id"], data["entity_id"])
        if cancel is not None:
            cancel()
            cancel = None
        if pending:
            cancel = async_call_later(hass, SETTLE_SECONDS, _settled)

    @callback
    def _stop_waiting() -> None:
        """Cancel a wait that is running (the entry unloads)."""
        if cancel is not None:
            cancel()

    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _registry_updated))
    entry.async_on_unload(_stop_waiting)
