# License: AGPL-3.0-only (LICENSE).
"""The display unit, set the way Home Assistant sets units: in the entity settings of the glucose sensors.

GlucoCraft's glucose sensors report mg/dL with the blood glucose device class; Home Assistant converts their states,
history display and long-term statistics to the unit chosen in each entity's settings. GlucoCraft's one "Display unit"
setting writes that choice to all of its glucose sensors at once, and the dashboard, display card, panels, report and
notifications follow the unit of the Last glucose reading sensor. A unit chosen there in Home Assistant's entity
settings is copied to the other glucose sensors, so they stay in one unit.

The sensors suggest no unit of their own (no suggested_unit_of_measurement): Home Assistant scales a sensor's
suggested display precision from the suggested unit, or without one from the native unit, to the unit in use
(SensorEntity._get_adjusted_display_precision). With mg/dL as that base, the precision 0 (whole mg/dL) becomes one
decimal in mmol/L (the mg/dL : mmol/L ratio is about 18). Suggesting the display unit instead would make mmol/L the
base, and Home Assistant would then show mmol/L values without decimals. The unit of a fresh installation therefore
comes from the entity settings, which keep_display_unit writes at setup.
"""

from __future__ import annotations

from collections.abc import Iterator

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, STAT_PERIODS
from .units import UNIT_MGDL, UNIT_MMOLL

# the sensors with the blood glucose device class (unique id = f"{entry_id}_{key}")
CORE_GLUCOSE_KEYS = ("current_glucose", "delta_5min")
PERIOD_GLUCOSE_KEYS = ("mean", "std_dev", "min", "max")
GLUCOSE_UNITS = (UNIT_MGDL, UNIT_MMOLL)


def glucose_sensor_keys() -> list[str]:
    """Return the unique id keys of every sensor with the blood glucose device class."""
    return list(CORE_GLUCOSE_KEYS) + [f"{label}_{k}" for label, _ in STAT_PERIODS for k in PERIOD_GLUCOSE_KEYS]


def current_glucose_entity_id(hass: HomeAssistant, entry_id: str) -> str | None:
    """Return the entity id of the Last glucose reading sensor (unique id key current_glucose), or None."""
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry_id}_current_glucose")


def _glucose_entries(reg: er.EntityRegistry, entry_id: str) -> Iterator[er.RegistryEntry]:
    """The registry entries of the entry's glucose sensors that exist (an optional feature's may not)."""
    for key in glucose_sensor_keys():
        entity_id = reg.async_get_entity_id("sensor", DOMAIN, f"{entry_id}_{key}")
        if entity_id and (entry := reg.async_get(entity_id)) is not None:
            yield entry


def _settings_unit(entry: er.RegistryEntry) -> str | None:
    """The glucose unit chosen in an entity's settings, or None."""
    unit = (entry.options.get("sensor") or {}).get("unit_of_measurement")
    return unit if unit in GLUCOSE_UNITS else None


def shown_unit(hass: HomeAssistant, entry_id: str) -> str | None:
    """Return the unit Home Assistant shows the Last glucose reading sensor in, from its registry entry: the unit of
    its entity settings; None while it has none (or no entry yet).

    Read from the registry, not from the sensor's state: the registry holds a new unit the moment it is chosen, while
    the state carries it only once the sensor has been written again. After a reload (an options save) the binary
    sensors are set up before the sensors, so a state-based unit would give them the unit of the earlier state.
    """
    entity_id = current_glucose_entity_id(hass, entry_id)
    entry = er.async_get(hass).async_get(entity_id) if entity_id else None
    return _settings_unit(entry) if entry is not None else None


def _set_unit(reg: er.EntityRegistry, entry: er.RegistryEntry, unit: str) -> None:
    """Write `unit` as the unit of an entity's settings (the other sensor options are kept)."""
    reg.async_update_entity_options(
        entry.entity_id, "sensor", {**(entry.options.get("sensor") or {}), "unit_of_measurement": unit}
    )


def apply_display_unit(hass: HomeAssistant, entry_id: str, unit: str) -> int:
    """Set the display unit of every GlucoCraft glucose sensor (entity settings); returns how many changed."""
    unit = UNIT_MMOLL if unit == UNIT_MMOLL else UNIT_MGDL
    reg = er.async_get(hass)
    changed = 0
    for entry in _glucose_entries(reg, entry_id):
        if (entry.options.get("sensor") or {}).get("unit_of_measurement") == unit:
            continue
        _set_unit(reg, entry, unit)
        changed += 1
    return changed


def keep_display_unit(hass: HomeAssistant, entry_id: str, unit: str) -> int:
    """Write a unit into the entity settings of every glucose sensor that has none there yet; returns how many.

    Run at every setup, after the platforms (__init__.async_setup_entry); idempotent. A sensor without a unit in its
    entity settings gets the display unit setting `unit` (a fresh installation, whose setting is the source's unit, and
    the sensors of an optional feature turned on since). A unit already chosen in the entity settings is never changed,
    so no sensor changes its unit at a restart.
    """
    unit = UNIT_MMOLL if unit == UNIT_MMOLL else UNIT_MGDL
    reg = er.async_get(hass)
    written = 0
    for entry in _glucose_entries(reg, entry_id):
        if _settings_unit(entry):
            continue
        _set_unit(reg, entry, unit)
        written += 1
    return written
