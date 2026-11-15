# License: AGPL-3.0-only (LICENSE).
"""The base of every GlucoCraft entity: the coordinator, the unique id, the entity id that follows the name, and the
one GlucoCraft device.

GlucoCraft has one device per installation, a service device (it calculates and displays data from other
integrations; it is not hardware). Every sensor and binary sensor belongs to it.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GlucoCraftCoordinator
from .entity_ids import ID_PREFIX
from .i18n import t


def device_info(coordinator: GlucoCraftCoordinator) -> dict[str, Any]:
    """The one GlucoCraft device of the coordinator's entry. Its software version is the integration version from
    manifest.json (coordinator.integration_version, set before the platforms create the entities), the same for the
    sensors and the binary sensors."""
    return {
        "identifiers": {(DOMAIN, coordinator.entry_id)},
        "name": "GlucoCraft",
        "manufacturer": "GlucoCraft",
        "model": t("entity.device.model"),
        "entry_type": DeviceEntryType.SERVICE,
        "sw_version": coordinator.integration_version or None,
    }


class GlucoCraftEntity(CoordinatorEntity[GlucoCraftCoordinator]):
    """A GlucoCraft entity of the platform named by the class attribute `_platform_domain` ("sensor" /
    "binary_sensor").

    The unique id is f"{entry_id}_{key}" and never changes, so a rename keeps the entity's history and settings. The
    name is translated: strings.json "entity" -> <platform> -> <translation key> (the key itself unless given), with
    `placeholders` filled in ("{period} mean"). The entity id is explicit and the same in every language:
    <platform>.glucocraft_<object_id>, the object id of the English name (entity_ids.object_id; tests/test_entity_ids.py
    checks the rule).

    The icon comes from icons.json by the same translation key (entity -> <platform> -> <translation key>), with an
    icon per state where the state names a different situation (the status, the sensor expiry, no current data, the
    warm-up); no entity sets an icon in the code."""

    _attr_has_entity_name = True
    _platform_domain = "sensor"

    def __init__(
        self,
        coordinator: GlucoCraftCoordinator,
        entry: ConfigEntry,
        key: str,
        object_id: str,
        translation_key: str | None = None,
        placeholders: dict[str, str] | None = None,
    ) -> None:
        """Set the unique id, the translated name, the explicit entity id and the device."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = translation_key or key
        if placeholders:
            self._attr_translation_placeholders = placeholders
        self.entity_id = f"{self._platform_domain}.{ID_PREFIX}{object_id}"
        self._expected_entity_id = self.entity_id  # kept for the check of changed ids (renamed_entities.py)
        self._attr_device_info = device_info(coordinator)

    @property
    def unique_id(self) -> str:
        """Return the unique id, f"{entry_id}_{key}" (as Home Assistant's Entity.unique_id; it never changes)."""
        return self._attr_unique_id

    @property
    def expected_entity_id(self) -> str:
        """Return the entity id the entity was created with, before Home Assistant can replace it with the registry's
        id: the check of changed ids compares the registry with it (renamed_entities.py)."""
        return self._expected_entity_id
