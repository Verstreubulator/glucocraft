# License: AGPL-3.0-only (LICENSE).
"""The notice about GlucoCraft entities whose entity id was changed.

Every GlucoCraft entity has one known id (entity_ids.py): the cards, the dashboard and the wall panels find the
entities by those ids, and a panel's ids are compiled into its firmware. Home Assistant lets a user change an entity's
id, and a new name for a device can change the ids of all its entities. Those consumers then lose the entity without
any error, and Home Assistant has no way to lock an id. So a change made while GlucoCraft is running is undone within
seconds, and the Repairs issue entity_ids_put_back says so. A changed id that cannot be undone that way stays for the
user: one found at setup (the user may have pointed something of their own at it since) or one whose original id
another entity has. The fixable Repairs issue entity_ids_changed names those, and its fix flow (repairs.py) puts them
back on confirmation.

How: each entity keeps the id it was created with (entity.py, expected_entity_id, before Home Assistant can replace
it with the registry's id); each platform reports its entities (stale_entities.record_created, one call site). The
check compares those ids with the registry, by the unique id, which never changes. It runs at every setup and shortly
after a change of an id of one of GlucoCraft's entities: a device rename changes the ids one by one, and the wait gives
one final notice. entity_ids_changed closes by itself when every id is the original one; entity_ids_put_back is
persistent: it stays, also across a restart, until the user dismisses it, so that a change undone shortly before a
restart is still reported.
"""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN
from .i18n import t, tp

ENTITY_IDS_ISSUE_ID = "entity_ids_changed"
PUT_BACK_ISSUE_ID = "entity_ids_put_back"
SHOWN_IDS = 5  # the ids a notice lists; then "and N more"
SETTLE_SECONDS = 2  # the wait after a change of an id, so that a device rename gives one final notice

# hass.data key: per entry id, the id each entity was created with ({unique id: entity id})
_DATA_EXPECTED = f"{DOMAIN}_expected_entity_ids"


@callback
def start_recording(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Forget the ids of an earlier setup (before the platforms are set up)."""
    hass.data.setdefault(_DATA_EXPECTED, {})[entry.entry_id] = {}


@callback
def record_expected(hass: HomeAssistant, entry: ConfigEntry, entities: Iterable) -> None:
    """Record the id each of a platform's entities was created with."""
    expected = hass.data.get(_DATA_EXPECTED, {}).get(entry.entry_id)
    if expected is not None:
        expected.update({e.unique_id: e.expected_entity_id for e in entities})


@callback
def changed_entity_ids(hass: HomeAssistant, entry_id: str | None) -> list[tuple[str, str]]:
    """Return (current id, original id) of every entity whose id is not the one it was created with, sorted."""
    expected = hass.data.get(_DATA_EXPECTED, {}).get(entry_id) or {}
    registry = er.async_get(hass)
    return sorted(
        (e.entity_id, expected[e.unique_id])
        for e in er.async_entries_for_config_entry(registry, entry_id)
        if e.unique_id in expected and e.entity_id != expected[e.unique_id]
    )


def changed_text(changed: list[tuple[str, str]]) -> str:
    """Return the ids as the notice lists them: one line each, the first few, then how many more."""
    lines = [t("repairs.entity_ids.line", current=cur, original=orig) for cur, orig in changed[:SHOWN_IDS]]
    if len(changed) > SHOWN_IDS:
        lines.append(tp("repairs.entity_ids.more", len(changed) - SHOWN_IDS))
    return "\n".join(lines)


def put_back_text(put_back: dict[str, str]) -> str:
    """Return the ids a put-back notice lists ({original id: the id it was changed to}), like changed_text."""
    pairs = sorted(put_back.items())
    lines = [t("repairs.entity_ids.put_back_line", original=orig, current=cur) for orig, cur in pairs[:SHOWN_IDS]]
    if len(pairs) > SHOWN_IDS:
        lines.append(tp("repairs.entity_ids.more", len(pairs) - SHOWN_IDS))
    return "\n".join(lines)


@callback
def async_update_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Open the notice while an id differs, with the ids it lists now; close it when none does."""
    changed = changed_entity_ids(hass, entry_id)
    if not changed:
        ir.async_delete_issue(hass, DOMAIN, ENTITY_IDS_ISSUE_ID)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        ENTITY_IDS_ISSUE_ID,
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ENTITY_IDS_ISSUE_ID,
        translation_placeholders={"changed": changed_text(changed)},
        data={"entry_id": entry_id},
    )


@callback
def async_put_back_notice(hass: HomeAssistant, put_back: dict[str, str]) -> None:
    """Say which ids were put back. Persistent: kept across a restart until the user dismisses it. Deleted first, so
    that a further change shows again after the user dismissed it."""
    ir.async_delete_issue(hass, DOMAIN, PUT_BACK_ISSUE_ID)
    ir.async_create_issue(
        hass,
        DOMAIN,
        PUT_BACK_ISSUE_ID,
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=PUT_BACK_ISSUE_ID,
        translation_placeholders={"changed": put_back_text(put_back)},
    )


@callback
def async_put_back(hass: HomeAssistant, entry_id: str | None, only: set[str] | None = None) -> list[tuple[str, str]]:
    """Give every changed entity (of `only`, by current id, when given) its original id; return those that could not
    have it, because another entity has that id (that other entity and the changed entity are left as they are). The
    notice is brought up to date."""
    registry = er.async_get(hass)
    blocked = []
    for current, original in changed_entity_ids(hass, entry_id):
        if only is not None and current not in only:
            continue
        try:
            registry.async_update_entity(current, new_entity_id=original)
        except ValueError:
            blocked.append((current, original))
    if entry_id is not None:
        async_update_issue(hass, entry_id)
    return blocked


@callback
def async_setup_entity_id_watch(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Check the ids now, and undo every change of the id of one of GlucoCraft's entities, until the entry unloads."""
    cancel = None
    live: set[str] = set()  # the ids entities were changed to since the last check
    put_back: dict[str, str] = {}  # since this setup: {original id: the id it was changed to}, for the notice

    @callback
    def _settled(_now) -> None:
        """Undo the changes made during the wait, and only those: a change found at setup stays for the user."""
        nonlocal cancel
        cancel = None
        only = set(live)
        live.clear()
        changed = [pair for pair in changed_entity_ids(hass, entry.entry_id) if pair[0] in only]
        blocked = async_put_back(hass, entry.entry_id, only) if changed else []
        done = {original: current for current, original in changed if (current, original) not in blocked}
        if done:
            put_back.update(done)
            async_put_back_notice(hass, put_back)
        async_update_issue(hass, entry.entry_id)

    @callback
    def _registry_updated(event) -> None:
        """A changed id of an entity of this entry: undo after the wait, which starts again at every further one."""
        nonlocal cancel
        data = event.data
        if data.get("action") != "update" or "old_entity_id" not in data:
            return
        changed = er.async_get(hass).async_get(data["entity_id"])
        if changed is None or changed.config_entry_id != entry.entry_id:
            return
        live.add(data["entity_id"])
        if cancel is not None:
            cancel()
        cancel = async_call_later(hass, SETTLE_SECONDS, _settled)

    @callback
    def _stop_waiting() -> None:
        """Cancel a wait that is running (the entry unloads)."""
        if cancel is not None:
            cancel()

    async_update_issue(hass, entry.entry_id)
    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _registry_updated))
    entry.async_on_unload(_stop_waiting)
