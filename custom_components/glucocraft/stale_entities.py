# License: AGPL-3.0-only (LICENSE).
"""Removal of the entity-registry entries of entities GlucoCraft no longer creates.

Home Assistant keeps the registry entry of an entity that its integration stops creating: it is shown as unavailable
("no longer provided"). At every start GlucoCraft therefore removes the registry entries of its config entry whose
unique id this setup did not create: the entities of a feature removed in a later version and those of an optional
feature that is off (Configure > Optional features). One rule, without a list of removed entities.

How: async_setup_entry calls start_setup before the platforms are set up; each platform reports the entities it creates
(record_created); afterwards async_remove_stale_entities removes every other registry entry of the entry. It removes
nothing unless every platform reported, so that a platform whose setup failed never has its entities removed. The
recorded history and the long-term statistics of a removed entity stay in Home Assistant's recorder (they are kept by
entity id): an optional feature turned on again creates the same entities under the same entity ids (the unique ids
never change), and their history continues.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, PLATFORMS
from .renamed_entities import record_expected, start_recording

_LOGGER = logging.getLogger(__name__)

# hass.data key: per entry id, the unique ids each platform created in the setup running now ({platform: {ids}})
_DATA_CREATED = f"{DOMAIN}_created_unique_ids"


@callback
def start_setup(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Start recording the entities this setup creates (before the platforms are set up)."""
    hass.data.setdefault(_DATA_CREATED, {})[entry.entry_id] = {}
    start_recording(hass, entry)


@callback
def record_created(hass: HomeAssistant, entry: ConfigEntry, platform: str, entities: Iterable) -> None:
    """Record the unique ids of the entities a platform creates (called by each platform's async_setup_entry after it
    has set up without an error)."""
    created = hass.data.get(_DATA_CREATED, {}).get(entry.entry_id)
    if created is not None:
        created[platform] = {e.unique_id for e in entities}  # GlucoCraftEntity.unique_id (entity.py)
    record_expected(hass, entry, entities)  # the ids they were created with (renamed_entities.py)


@callback
def async_remove_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> int:
    """Remove the registry entries of the entry whose unique id this setup did not create; return how many.

    Only when every platform (const.PLATFORMS) reported its entities; otherwise nothing is removed. Logged at info
    with the count.
    """
    created = hass.data.get(_DATA_CREATED, {}).pop(entry.entry_id, None) or {}
    if set(created) != set(PLATFORMS):
        return 0
    registry = er.async_get(hass)
    stale = [
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.unique_id not in created.get(e.entity_id.partition(".")[0], set())
    ]
    for entity_id in stale:
        registry.async_remove(entity_id)
    if stale:
        _LOGGER.info("GlucoCraft: removed %d entities that this version does not create", len(stale))
    return len(stale)
