# License: AGPL-3.0-only (LICENSE).
"""Diagnostics of GlucoCraft: what is needed to look into a problem, without glucose data and without personal data.

Home Assistant offers the file on the integration's page (Settings > Devices & services > GlucoCraft > the menu (three
dots) > Download diagnostics). It contains the settings, the GlucoCraft version, how many readings are stored and over
which time span, the reading interval, the state of the sources, the active repair issues, the state of the wall-panel
feed and the language.

It contains no glucose value, no reading, no laboratory result and no personal data. The entity ids of the sources,
the notify actions of the phones, the printer address and the id of the wall-panel address are redacted, and so is the
value of every stored key that GlucoCraft does not know (read by nothing; its value may name an entity). The custom
range limits and the custom goals are redacted as well (ranges set by a clinician describe what a person's care team
considers low and high); in their place the file says whether custom ranges are in use and whether custom goals are
set. A repair issue whose id ends with a source's entity id is listed with the source's role ("primary_source",
"secondary_source") instead; the names of the wall panels are left out. Readings are only counted, never read for their
values. Home Assistant's country and the regional formats it resolves to are included, because the formats and some
defaults follow the country.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import (
    CONF_GLUCOSE_ENTITY,
    CONF_PANEL_FEED,
    CONF_PANEL_WEBHOOK_ID,
    CONF_PHONE_TARGETS,
    CONF_PRINTER,
    CONF_SECONDARY_ENTITY,
    CONF_SENSOR_EXPIRY_ENTITY,
    DOMAIN,
    SOURCE_NAMES,
)
from .coordinator import SOURCE_ISSUE_PREFIXES, GlucoCraftConfigEntry, GlucoCraftCoordinator
from .expiry import parse_expiry
from .gaps import gap_threshold
from .i18n import configured_language, language
from .panel_notice import panel_device_ids
from .settings import ENTRY_KEYS, GOAL_DEFAULTS, RANGE_DEFAULTS, ranges, setting
from .storage import GlucoCraftStorage
from .units import round_half_up

# The settings whose values identify the household or give access: the source entities, the phones' notify actions,
# the printer's address and the id of the wall-panel address (which serves the stored history on the local network);
# and the custom range limits and goals, which describe what a person's care team considers low and high.
TO_REDACT = frozenset(
    {
        CONF_GLUCOSE_ENTITY,
        CONF_SECONDARY_ENTITY,
        CONF_SENSOR_EXPIRY_ENTITY,
        CONF_PHONE_TARGETS,
        CONF_PRINTER,
        CONF_PANEL_WEBHOOK_ID,
        *RANGE_DEFAULTS,
        *GOAL_DEFAULTS,
    }
)


def redact_settings(data: Any) -> dict[str, Any]:
    """Return the stored settings with TO_REDACT redacted and the value of every key that GlucoCraft does not know
    (settings.ENTRY_KEYS) redacted as well: such a key is read by nothing, and its value may name an entity or a
    person."""
    redacted = async_redact_data(dict(data or {}), TO_REDACT)
    return {key: value if key in ENTRY_KEYS else REDACTED for key, value in redacted.items()}


def custom_flags(data: Any) -> dict[str, bool]:
    """Return what the diagnostics say in place of the redacted ranges and goals: whether the ranges in use differ
    from the consensus ranges, and whether any goal differs from its default (the consensus target)."""
    d = dict(data or {})
    in_use = ranges(d)
    return {
        "custom_ranges_in_use": any(in_use[key] != default for key, default in RANGE_DEFAULTS.items()),
        "custom_goals_set": any(setting(d, key) != default for key, default in GOAL_DEFAULTS.items()),
    }


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: GlucoCraftConfigEntry) -> dict[str, Any]:
    """Return the diagnostics of the GlucoCraft entry (Home Assistant's diagnostics platform)."""
    coordinator: GlucoCraftCoordinator | None = getattr(entry, "runtime_data", None)
    out: dict[str, Any] = {
        "version": coordinator.integration_version if coordinator else None,
        "loaded": coordinator is not None,
        "settings": redact_settings(entry.data),
        # GlucoCraft keeps every setting in the entry's data, so the options are empty; they are shown anyway, so that a
        # report proves it (anything there was written by something other than GlucoCraft)
        "options": redact_settings(entry.options),
        "custom": custom_flags(entry.data),
        "language": {"home_assistant": configured_language(hass), "in_use": language()},
        # the formats and some defaults follow Home Assistant's country: the country and what the regional settings
        # resolve to
        "regional": {
            "country": hass.config.country,
            "resolved": coordinator.regional.as_dict() if coordinator else None,
        },
    }
    if coordinator is not None:
        # counted under the coordinator's lock in the executor, as the export reads the readings
        out["stored_readings"] = await hass.async_add_executor_job(
            coordinator.locked, stored_readings_summary, coordinator.storage
        )
        out["reading_interval"] = _reading_interval(coordinator)
        out["sources"] = _sources(hass, coordinator, entry)
        out["status"] = _status(coordinator)
    out["repair_issues"] = active_issue_ids(hass, entry)
    out["panel_feed"] = _panel_feed(hass, entry)
    return out


def stored_readings_summary(storage: GlucoCraftStorage) -> dict[str, Any]:
    """Return how many readings are stored, per source and at the sensor's limits, and the time span they cover."""
    readings = storage.readings
    by_source = Counter(SOURCE_NAMES.get(r.source) or "unknown" for r in readings)
    oldest = readings[0].timestamp if readings else None
    newest = readings[-1].timestamp if readings else None
    return {
        "count": len(readings),
        "by_source": {name: by_source.get(name, 0) for name in ("primary", "secondary", "unknown")},
        "limit_readings": sum(1 for r in readings if r.limit),
        "oldest": _iso(oldest),
        "newest": _iso(newest),
        "span_days": round_half_up((newest - oldest) / 86400, 1) if readings else 0,
        "storage_problem": storage.problem,
    }


def _reading_interval(coordinator: GlucoCraftCoordinator) -> dict[str, Any]:
    """Return the interval detected in the stored readings and the interval and gap threshold of the source in use."""
    active = coordinator.reading_interval()
    return {
        "detected_seconds": coordinator.detected_interval_seconds,
        "source_in_use_seconds": active,
        "gap_threshold_seconds": gap_threshold(active),
    }


def _sources(hass: HomeAssistant, coordinator: GlucoCraftCoordinator, entry: GlucoCraftConfigEntry) -> dict[str, Any]:
    """Return the state of each configured source, by role, without its entity id and without its value."""
    primary = entry.data.get(CONF_GLUCOSE_ENTITY)
    secondary = setting(entry.data, CONF_SECONDARY_ENTITY)
    out: dict[str, Any] = {
        "primary": _glucose_source(hass, coordinator, primary),
        "secondary": _glucose_source(hass, coordinator, secondary),
    }
    expiry = setting(entry.data, CONF_SENSOR_EXPIRY_ENTITY)
    # with the form GlucoCraft read its value in (expiry.parse_expiry: the code, never the value)
    out["sensor_expiry"] = (
        {**_availability(hass, expiry), "format": parse_expiry(hass.states.get(expiry))[1]} if expiry else None
    )
    return out


def _glucose_source(hass: HomeAssistant, coordinator: GlucoCraftCoordinator, entity_id: str | None) -> dict | None:
    """Return one glucose source's state: availability, unit, delivering integration, limits, interval and time."""
    if not entity_id:
        return None
    summary = coordinator.source_summary(entity_id)
    limits = summary["limits"]
    return {
        "in_use": entity_id == coordinator.active_source_entity,
        **_availability(hass, entity_id),
        "integration": summary["integration"],
        "sensor_limits": {
            "lower": limits["lower"],
            "upper": limits["upper"],
            "lower_setting": limits["lower_setting"],
            "upper_setting": limits["upper_setting"],
        },
        "reading_interval_seconds": coordinator.reading_interval(entity_id),
        "last_measurement": _iso(summary["last_measurement"]),
    }


def _availability(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    """Return whether an entity exists and has a state, and its unit (never the state itself)."""
    state = hass.states.get(entity_id)
    return {
        "exists": state is not None,
        "available": state is not None and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN),
        "unit": state.attributes.get("unit_of_measurement") if state is not None else None,
    }


def _status(coordinator: GlucoCraftCoordinator) -> dict[str, Any]:
    """Return the status that the displays show and the facts it is made from (no value)."""
    last = coordinator.last_reading_ts
    return {
        "display_status": coordinator.display_status,
        "no_current_data": coordinator.no_current_data,
        "no_current_data_minutes": coordinator.stale_minutes,
        "sensor_warming_up": coordinator.sensor_warming_up,
        "second_source_active": coordinator.second_source_active,
        "last_reading": _iso(last or None),
        "last_reading_age_seconds": int(dt_util.utcnow().timestamp()) - last if last else None,
        "clock_behind": coordinator.clock_behind(),
    }


def active_issue_ids(hass: HomeAssistant, entry: GlucoCraftConfigEntry) -> list[str]:
    """Return the ids of GlucoCraft's active repair issues, sorted. An id that ends with a source's entity id (the
    source notices, coordinator.SOURCE_ISSUE_PREFIXES) ends with the source's role instead, or is redacted."""
    roles = {
        entry.data.get(CONF_GLUCOSE_ENTITY): "primary_source",
        setting(entry.data, CONF_SECONDARY_ENTITY): "secondary_source",
    }
    roles.pop(None, None)
    out = []
    for (domain, issue_id), issue in ir.async_get(hass).issues.items():
        if domain != DOMAIN or not issue.active:
            continue
        for prefix in SOURCE_ISSUE_PREFIXES:
            if issue_id.startswith(prefix):
                issue_id = prefix + roles.get(issue_id[len(prefix) :], REDACTED)
                break
        out.append(issue_id)
    return sorted(out)


def _panel_feed(hass: HomeAssistant, entry: GlucoCraftConfigEntry) -> dict[str, Any]:
    """Return the state of the wall-panel feed and the panels' models and versions (not their names); the id of the
    wall-panel address is redacted."""
    registry = dr.async_get(hass)
    panels = [registry.async_get(device_id) for device_id in panel_device_ids(hass)]
    return {
        "enabled": setting(entry.data, CONF_PANEL_FEED),
        "address_created": bool(entry.data.get(CONF_PANEL_WEBHOOK_ID)),
        "webhook_id": REDACTED if entry.data.get(CONF_PANEL_WEBHOOK_ID) else None,
        "panels": [{"model": d.model, "version": d.sw_version} for d in panels if d is not None],
    }


def _iso(timestamp: int | None) -> str | None:
    """Return an epoch time as UTC ISO 8601 text, or None."""
    return dt_util.utc_from_timestamp(timestamp).isoformat() if timestamp else None
