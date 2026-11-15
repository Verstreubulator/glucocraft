# License: AGPL-3.0-only (LICENSE).
"""Binary sensor platform for GlucoCraft.

Binary sensors for the range conditions (below or above the limits in use), no current data, the sensor
warm-up and the use of custom ranges.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import public_agp_ranges
from .coordinator import GlucoCraftCoordinator
from .entity import GlucoCraftEntity
from .i18n import t
from .limits import above, below
from .stale_entities import record_created
from .units import from_mgdl

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

# The entities are updated by the coordinator, never polled one by one: no limit on parallel updates
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up GlucoCraft binary sensors (the coordinator is the entry's runtime data)."""
    coordinator: GlucoCraftCoordinator = entry.runtime_data
    entities = build_entities(coordinator, entry)
    async_add_entities(entities)
    record_created(hass, entry, "binary_sensor", entities)  # for the removal of stale entries (stale_entities.py)


def build_entities(coordinator: GlucoCraftCoordinator, entry: ConfigEntry) -> list[BinarySensorEntity]:
    """Return every GlucoCraft binary sensor of the entry."""
    return [
        GlucoCraftLow(coordinator, entry),
        GlucoCraftHigh(coordinator, entry),
        GlucoCraftVeryLow(coordinator, entry),
        GlucoCraftVeryHigh(coordinator, entry),
        GlucoCraftNoCurrentData(coordinator, entry),
        GlucoCraftSensorWarmUp(coordinator, entry),
        GlucoCraftInRange(coordinator, entry),
        GlucoCraftInTightRange(coordinator, entry),
        GlucoCraftUsingCustomRanges(coordinator, entry),
    ]


def limit_source(coordinator: GlucoCraftCoordinator) -> str:
    """Return the range set the limits come from: "consensus ranges", or "custom ranges" while custom ranges are in
    use (the answer of Using custom ranges; while the optional feature Custom ranges and goals is off, the consensus
    ranges are used whatever is stored)."""
    return t("entity.limit_source.custom" if coordinator.uses_custom_ranges() else "entity.limit_source.consensus")


class GlucoCraftBinaryBase(GlucoCraftEntity, BinarySensorEntity):
    """Base of every GlucoCraft binary sensor (entity.GlucoCraftEntity: unique id, entity id, device)."""

    _platform_domain = "binary_sensor"


class _RangeBinarySensor(GlucoCraftBinaryBase):
    """A glucose value beyond one limit in use: below it (_below True) or above it. The limit is the coordinator's
    attribute _limit_attr (range_low, ...), in mg/dL. No device class: On / Off (the SAFETY device class would show
    "Unsafe" / "Safe", an assessment)."""

    _limit_attr: str
    _below: bool

    @property
    def is_on(self) -> bool | None:
        """Return True beyond the limit; None while there is no current data."""
        # the stored value against the stored limit, as the zone and the statistics decide it (limits.below / above)
        c = self.coordinator
        g = c.fresh_glucose  # None (unknown) while the data are stale
        if g is None:
            return None
        limit = getattr(c, self._limit_attr)
        return below(g, c.current_limit, limit) if self._below else above(g, c.current_limit, limit)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the limit in the display unit, the unit and where the limit comes from."""
        unit = self.coordinator.display_unit
        return {
            "threshold": from_mgdl(getattr(self.coordinator, self._limit_attr), unit),
            "unit": unit,
            "limit_source": limit_source(self.coordinator),
        }


class GlucoCraftLow(_RangeBinarySensor):
    """Glucose below range: below the low limit, very low included (unique id key is_low).

    The name says "below range" because "time low" is only the band from the very-low to the low limit.
    """

    _limit_attr, _below = "range_low", True

    def __init__(self, coordinator, entry):
        """Initialize the binary sensor."""
        super().__init__(coordinator, entry, "is_low", "glucose_below_range")


class GlucoCraftHigh(_RangeBinarySensor):
    """Glucose above range: above the high limit, very high included (unique id key is_high)."""

    _limit_attr, _below = "range_high", False
    # read by no GlucoCraft page, card or panel, so off until enabled in Home Assistant
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, entry):
        """Initialize the binary sensor."""
        super().__init__(coordinator, entry, "is_high", "glucose_above_range")


class GlucoCraftVeryLow(_RangeBinarySensor):
    """Glucose is below the very-low limit (level 2 hypoglycemia on the consensus ranges)."""

    _limit_attr, _below = "range_critical_low", True
    # read by no GlucoCraft page, card or panel, so off until enabled in Home Assistant
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, entry):
        """Initialize the binary sensor."""
        super().__init__(coordinator, entry, "is_critical_low", "glucose_very_low")


class GlucoCraftVeryHigh(_RangeBinarySensor):
    """Glucose is above the very-high limit (level 2 hyperglycemia on the consensus ranges)."""

    _limit_attr, _below = "range_critical_high", False
    # read by no GlucoCraft page, card or panel, so off until enabled in Home Assistant
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, entry):
        """Initialize the binary sensor."""
        super().__init__(coordinator, entry, "is_critical_high", "glucose_very_high")


class GlucoCraftNoCurrentData(GlucoCraftBinaryBase):
    """No current data: no reading, none for longer than the no-current-data limit, or one later than the clock
    (coordinator.no_current_data, the one fact every display and the trend values follow; unique id key is_stale)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    # no entity category: it says whether the glucose value shown is current, which is part of reading it

    def __init__(self, coordinator, entry):
        """Initialize the binary sensor."""
        super().__init__(coordinator, entry, "is_stale", "no_current_data")

    @property
    def is_on(self) -> bool:
        """Return True while there is no current data."""
        return self.coordinator.no_current_data

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the no-current-data limit.

        No attribute follows the clock: the age of the last reading would change at every minute tick, and Home
        Assistant's recorder writes a row for every change. Every display counts the age itself from the reading's own
        time (the Last reading time sensor).
        """
        # the no-current-data setting (options flow), so panels and cards use the same limit
        return {"threshold_minutes": self.coordinator.stale_minutes}


class GlucoCraftSensorWarmUp(GlucoCraftBinaryBase):
    """Sensor warm-up: the CGM sensor is warming up, no valid readings yet (unique id key sensor_warmup).

    On when (a) the optional expiry entity's expiry moves more than a day later (a new sensor; any CGM with an
    expiry entity), or (b) the source sends the Libre warm-up marker 0xFFFF. Off at the first valid reading measured
    after the warm-up began, or by itself 24 hours after it began (coordinator.WARMUP_MAX_SECONDS). Attribute started_at
    = when GlucoCraft saw the warm-up start (UTC ISO).
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC  # the state of the CGM sensor (the device), not a glucose value

    def __init__(self, coordinator, entry):
        """Initialize the binary sensor."""
        super().__init__(coordinator, entry, "sensor_warmup", "sensor_warm_up")

    @property
    def is_on(self) -> bool:
        """Return True while the CGM sensor is warming up."""
        return self.coordinator.sensor_warming_up

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return when GlucoCraft saw the warm-up start (UTC ISO)."""
        ts = self.coordinator.warmup_started_at
        return {"started_at": dt_util.utc_from_timestamp(ts).replace(microsecond=0).isoformat() if ts else None}


class GlucoCraftInRange(GlucoCraftBinaryBase):
    """Glucose is in range on the ranges in use (low limit to high limit). Its icon in icons.json is neutral
    (arrows left and right): a check mark would read as an assessment."""

    # read by no GlucoCraft page, card or panel, so off until enabled in Home Assistant
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, entry):
        """Initialize the binary sensor."""
        super().__init__(coordinator, entry, "in_range", "glucose_in_range")

    @property
    def is_on(self) -> bool | None:
        """Return True from the low to the high limit; None while there is no current data."""
        c = self.coordinator
        g = c.fresh_glucose  # None (unknown) while the data are stale
        if g is None:
            return None
        return not below(g, c.current_limit, c.range_low) and not above(g, c.current_limit, c.range_high)


class GlucoCraftInTightRange(GlucoCraftBinaryBase):
    """Glucose is in the tight range."""

    # read by no GlucoCraft page, card or panel, so off until enabled in Home Assistant
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, entry):
        """Initialize the binary sensor."""
        super().__init__(coordinator, entry, "in_tight_range", "glucose_in_tight_range")

    @property
    def is_on(self) -> bool | None:
        """Return True in the tight range; None while there is no current data."""
        c = self.coordinator
        g = c.fresh_glucose  # None (unknown) while the data are stale
        if g is None:
            return None
        return not below(g, c.current_limit, c.range_ideal_low) and not above(g, c.current_limit, c.range_ideal_high)


class GlucoCraftUsingCustomRanges(GlucoCraftBinaryBase):
    """On when the ranges in the settings differ from the consensus ranges (the defaults); always off while the
    optional feature Custom ranges and goals is off, because the consensus ranges are then in use.

    The one answer to "consensus or custom ranges" for every display. It is computed from the stored mg/dL values
    (low / high / tight low / tight high / very low / very high), so it does not depend on the display unit: the cards
    and panels read this entity instead of comparing display-unit values with mg/dL constants, which would give
    wrong results in mmol/L.

    Consensus ranges (attributes consensus_*): low=70, high=180, very_low=54, very_high=250, tight_low=70,
    tight_high=140 (Battelino et al., 2019; Battelino et al., 2023).
    """

    # (no entity category: it says which ranges the glucose values are shown on, which is part of reading them)

    def __init__(self, coordinator, entry):
        """Initialize the binary sensor."""
        super().__init__(coordinator, entry, "using_custom_ranges", "using_custom_ranges")

    @property
    def is_on(self) -> bool:
        """Return True when any range differs from the consensus ranges."""
        return self.coordinator.uses_custom_ranges()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the ranges in use and the consensus ranges (mg/dL, keys in the consensus terms)."""
        c = self.coordinator
        return {
            "range_low": c.range_low,
            "range_high": c.range_high,
            "range_very_low": c.range_critical_low,
            "range_very_high": c.range_critical_high,
            "range_tight_low": c.range_ideal_low,
            "range_tight_high": c.range_ideal_high,
            # the consensus ranges (const.AGP_RANGES) under the same public names: consensus_low,
            # consensus_very_low, ...
            **{f"consensus_{k[len('range_') :]}": v for k, v in public_agp_ranges().items()},
        }
