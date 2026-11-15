# License: AGPL-3.0-only (LICENSE).
"""Notices about GlucoCraft wall panels: when one is added, and when one is offline for 10 minutes.

Added: the dashboard's display card is chosen independently of the panels, so the notice for a new panel explains
which card the dashboard shows and where to change it.

GlucoCraft panels register in Home Assistant with the manufacturer "glucocraft" (the first part of the ESPHome project
name); the model names the board and its resolution ("waveshare-lcd7-800x480", "waveshare-p4-7b-1024x600",
"guition4848s040-480x480"). The first time a panel appears, a persistent notification names its shape, the display card
the dashboard shows and where to change it. Panels present when this runs for the first time are recorded without a
notice.

Panels are the user's own devices: when a panel's device is deleted from Home Assistant, the panel is forgotten (its id
leaves the store of panels already announced) and its offline notice is removed.
"""

from __future__ import annotations

import re

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DISPLAY_CARD,
    DISPLAY_CARD_RECTANGULAR,
    DISPLAY_CARD_SQUARE,
    DOMAIN,
)
from .i18n import t
from .maintenance import MaintenanceIssues
from .settings import setting

_RES = re.compile(r"(\d{3,4})x(\d{3,4})")

# The notices of this module, each named by its prefix and the panel's device id, and the suffix of the store of the
# panels already announced (.storage/glucocraft.<entry id>_<suffix>): the removal deletes them (__init__.py)
ADDED_PREFIX = "glucocraft_panel_added_"
OFFLINE_PREFIX = "glucocraft_panel_offline_"
NOTIFICATION_PREFIXES = (ADDED_PREFIX, OFFLINE_PREFIX)
STORE_SUFFIXES = ("panels",)


def panel_shape(model: str | None) -> tuple[str, str]:
    """Return ("rectangular" | "square", "800 × 480") from a panel's model name."""
    m = _RES.search(model or "")
    if m and m.group(1) != m.group(2):
        return DISPLAY_CARD_RECTANGULAR, f"{m.group(1)} × {m.group(2)}"
    return DISPLAY_CARD_SQUARE, f"{m.group(1)} × {m.group(2)}" if m else "480 × 480"


# The rectangular display card has the layout of the 800 x 480 panels; the rectangular panels of other sizes (1024 x
# 600, 480 x 320) have the same pages in a layout close to it
RECTANGULAR_CARD_SIZE = "800 × 480"


def card_fit(card: str, shape: str, size: str) -> str:
    """Return how the dashboard's display card fits a panel: "matches" (the card has the panel's layout), "close"
    (a rectangular panel of another size than the rectangular card) or "differs" (another shape)."""
    if shape != card:
        return "differs"
    return "close" if shape == DISPLAY_CARD_RECTANGULAR and size != RECTANGULAR_CARD_SIZE else "matches"


def _is_panel(device: dr.DeviceEntry | None) -> bool:
    """True for a GlucoCraft wall panel (ESPHome, manufacturer "glucocraft"), not GlucoCraft's own device
    (identifiers in this domain)."""
    return (
        device is not None
        and (device.manufacturer or "").lower() == "glucocraft"
        and not any(i[0] == DOMAIN for i in device.identifiers)
    )


def _panels(hass: HomeAssistant) -> dict[str, dr.DeviceEntry]:
    """Return the GlucoCraft wall panels in the device registry, by device id: the devices of the ESPHome entries
    (a wall panel is an ESPHome device), through dr.async_entries_for_config_entry, not the registry's devices
    mapping, which Home Assistant deprecates."""
    registry = dr.async_get(hass)
    return {
        d.id: d
        for entry in hass.config_entries.async_entries("esphome")
        for d in dr.async_entries_for_config_entry(registry, entry.entry_id)
        if _is_panel(d)
    }


def _concerns_panels(hass: HomeAssistant, event: Event) -> bool:
    """True for a device registry update that can concern a wall panel: a removed device (it may have been one, and
    its entry is gone) or a created or updated device that is a GlucoCraft panel. Every other device update is left
    alone, so the devices are not scanned at each registry update."""
    data = event.data
    if data.get("action") == "remove":
        return True
    return _is_panel(dr.async_get(hass).async_get(data.get("device_id")))


def panel_device_ids(hass: HomeAssistant) -> list[str]:
    """Return the device ids of the GlucoCraft wall panels (their notices are named after them)."""
    return sorted(_panels(hass))


async def async_setup_panel_notice(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Notify once for each new wall panel: its shape, the display card the dashboard shows and where to change it."""
    store: Store = Store(hass, 1, f"glucocraft.{entry.entry_id}_panels")
    data = await store.async_load()
    if data is None:  # first run: the panels already there need no notice
        known = set(_panels(hass))
        await store.async_save({"known": sorted(known)})
    else:
        # a panel whose device was deleted while GlucoCraft was not running is forgotten as well
        stored = set(data.get("known", []))
        known = stored & set(_panels(hass))
        if known != stored:
            await store.async_save({"known": sorted(known)})

    @callback
    def _check(event: Event | None = None) -> None:
        """Create the notice for every panel not seen before and remember it; forget a panel whose device was
        deleted."""
        if event is not None and event.data.get("action") == "remove":
            if event.data.get("device_id") in known:
                known.discard(event.data["device_id"])
                hass.async_create_task(store.async_save({"known": sorted(known)}))
            return
        if event is not None and not _concerns_panels(hass, event):
            return
        new = [d for i, d in _panels(hass).items() if i not in known]
        if not new:
            return
        card = setting(entry.data, CONF_DISPLAY_CARD)
        if card != DISPLAY_CARD_RECTANGULAR:  # setting() already reads anything else as the default, square
            card = DISPLAY_CARD_SQUARE
        for d in new:
            known.add(d.id)
            shape, size = panel_shape(d.model)
            card_txt = t(f"panel_notice.card.{card}")
            paragraphs = [
                t(
                    "panel_notice.added.connected",
                    name=d.name_by_user or d.name,
                    size=size,
                    shape=t(f"panel_notice.shape.{shape}"),
                ),
                t(f"panel_notice.added.{card_fit(card, shape, size)}", card=card_txt)
                + " "
                + t("panel_notice.added.where"),
            ]
            persistent_notification.async_create(
                hass,
                "\n\n".join(paragraphs),
                title=t("panel_notice.added.title"),
                notification_id=f"{ADDED_PREFIX}{d.id}",
            )
        hass.async_create_task(store.async_save({"known": sorted(known)}))

    _check()
    entry.async_on_unload(hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, _check))


# ---------------------------------------------------------------------------------------------- offline panels
# A panel that has lost power, Wi-Fi or its connection can keep showing old data (a frozen panel keeps its last
# frame). When a GlucoCraft panel's Status entity is off or unavailable for 10 minutes, a persistent notification says
# so (notify-only, no alarm); it is removed when the panel is back or its device is deleted. While the notice is shown,
# the panel counts as an issue of the Maintenance status (maintenance.py).
OFFLINE_AFTER_S = 600


def _panel_status_entities(hass: HomeAssistant) -> dict[str, dr.DeviceEntry]:
    """Return each panel's Status (connectivity) entity id -> its device.

    The Status entity is recognized by the "_status" ending of its entity id: the panel firmware names the entity
    "Status", and Home Assistant builds the id from the device name and that name. A user who renames the entity id
    to one without that ending stops its offline notice without a word (an offline panel then no longer counts in the
    Maintenance status either), and a second binary sensor of a panel whose id ends with "_status" would be watched as
    well. Matching the device class or the unique id instead needs a fixed promise from the panel firmware, so the id
    ending stays the rule; _entity_changed below uses the same ending."""
    reg = er.async_get(hass)
    out = {}
    for dev_id, dev in _panels(hass).items():
        for ent in er.async_entries_for_device(reg, dev_id):
            if ent.domain == "binary_sensor" and ent.entity_id.endswith("_status"):
                out[ent.entity_id] = dev
    return out


async def async_setup_panel_offline(
    hass: HomeAssistant, entry: ConfigEntry, maintenance: MaintenanceIssues | None = None
) -> None:
    """Notify when a wall panel has been offline for 10 minutes, and remove the notice when it is back or its device
    is deleted; maintenance (the coordinator's Maintenance status) counts the panels while their notice is shown."""
    timers: dict[str, object] = {}
    watched: dict[str, dr.DeviceEntry] = {}
    unsub: list = []

    def _name(dev: dr.DeviceEntry) -> str:
        """Return a panel's name as Home Assistant shows it (the user's name when given)."""
        return dev.name_by_user or dev.name

    def _nid(dev: dr.DeviceEntry) -> str:
        """Return the notification id of a panel's offline notice."""
        return f"{OFFLINE_PREFIX}{dev.id}"

    @callback
    def _notify(entity_id: str, _now=None) -> None:
        """Create the offline notice, unless the panel is connected again."""
        timers.pop(entity_id, None)
        dev = watched.get(entity_id)
        st = hass.states.get(entity_id)
        if not dev or (st and st.state == "on"):
            return
        name = _name(dev)
        persistent_notification.async_create(
            hass,
            t("panel_notice.offline.lcd", name=name),
            title=t("panel_notice.offline.title"),
            notification_id=_nid(dev),
        )
        if maintenance is not None:
            maintenance.panel_offline(dev.id, name)

    @callback
    def _dismiss(dev: dr.DeviceEntry) -> None:
        """Remove a panel's offline notice (it is back, or its device was deleted)."""
        persistent_notification.async_dismiss(hass, _nid(dev))
        if maintenance is not None:
            maintenance.panel_back(dev.id)

    @callback
    def _evaluate(entity_id: str) -> None:
        """Start the 10-minute timer for a disconnected panel, or cancel it and remove the notice."""
        st = hass.states.get(entity_id)
        dev = watched.get(entity_id)
        if not dev:
            return
        if st and st.state == "on":  # connected: cancel the timer, remove the notice
            if cancel := timers.pop(entity_id, None):
                cancel()
            _dismiss(dev)
        elif entity_id not in timers:
            # the time the panel has already been offline counts, so that a reload of GlucoCraft (every saved setting)
            # does not start the 10 minutes again; a @callback: a plain lambda would run in the executor, outside the
            # event loop
            since = getattr(st, "last_changed", None)
            offline_s = (dt_util.utcnow() - since).total_seconds() if since else 0
            timers[entity_id] = async_call_later(
                hass, max(0, OFFLINE_AFTER_S - offline_s), callback(lambda now, e=entity_id: _notify(e, now))
            )

    @callback
    def _changed(event: Event) -> None:
        """Evaluate a panel whose Status entity changed."""
        _evaluate(event.data["entity_id"])

    @callback
    def _rescan(event: Event | None = None) -> None:
        """Watch the Status entities of the panels now in the registry. A Status entity no longer there stops its
        timer; a panel whose device was deleted loses its offline notice as well. A panel renamed in Home Assistant
        keeps its devices' entries up to date, so that a notice already shown and the Maintenance status take the new
        name at once. A device registry update that concerns no panel (_concerns_panels) changes nothing."""
        if event is not None and not _concerns_panels(hass, event):
            return
        found = _panel_status_entities(hass)
        if set(found) == set(watched):
            renamed = [e for e, dev in found.items() if _name(dev) != _name(watched[e])]
            watched.update(found)
            for entity_id in renamed:
                if entity_id not in timers:  # offline past its 10 minutes: the notice is shown again, by its new name
                    _notify(entity_id)
            return
        for entity_id in set(watched) - set(found):
            if cancel := timers.pop(entity_id, None):
                cancel()
        remaining = {dev.id for dev in found.values()}
        for dev in {dev.id: dev for dev in watched.values() if dev.id not in remaining}.values():
            _dismiss(dev)
        watched.clear()
        watched.update(found)
        while unsub:
            unsub.pop()()
        unsub.append(async_track_state_change_event(hass, list(watched), _changed))
        for e in watched:
            _evaluate(e)

    @callback
    def _entity_changed(event: Event) -> None:
        """Rescan when an entity registry update can concern a panel's Status entity: a Status entity added, removed
        or renamed (its id ends with _status), or a watched one changed. Every other entity of Home Assistant is
        left alone, so the devices are not scanned at each registry update."""
        data = event.data
        ids = [i for i in (data.get("entity_id"), data.get("old_entity_id")) if i]
        if any(i.endswith("_status") or i in watched for i in ids):
            _rescan()

    def _cleanup() -> None:
        """Stop the listeners and the timers (unload)."""
        while unsub:
            unsub.pop()()
        for cancel in list(timers.values()):
            cancel()
        timers.clear()

    _rescan()
    entry.async_on_unload(hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, _rescan))
    # a new panel's Status entity is registered after its device, so entity registry updates also trigger a rescan
    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _entity_changed))
    entry.async_on_unload(_cleanup)
