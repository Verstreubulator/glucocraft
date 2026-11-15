# License: AGPL-3.0-only (LICENSE).
"""Repairs for GlucoCraft: the fix flows of the two GlucoCraft notices that are fixed by a choice in Repairs: stored
readings later than the system clock, and entities whose id was changed."""

from __future__ import annotations

import time
from typing import Any

import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .coordinator import CLOCK_ISSUE_ID, loaded_coordinator
from .renamed_entities import ENTITY_IDS_ISSUE_ID, async_put_back, async_update_issue, changed_entity_ids, changed_text
from .source_notices import clock_stamp

CONF_DISCARD = "discard"


class ClockRepairFlow(RepairsFlow):
    """Stored readings are later than the system clock. Either the clock is behind (then correcting the clock closes
    the notice by itself) or the readings were stored while the clock was ahead; only in that case the user discards
    them here. Nothing is ever deleted without this confirmation. After the discard, the readings the recorder
    received while new readings were refused are imported (coordinator.async_catch_up). While GlucoCraft is not
    loaded, nothing can be discarded: the flow says so (abort not_loaded) and the notice stays open."""

    def __init__(self, entry_id: str | None) -> None:
        """Take the entry of the notice, as every fix flow does (async_create_fix_flow); this flow does not need it:
        there is at most one GlucoCraft entry, and its coordinator is found by loaded_coordinator."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        """Start with the confirmation step."""
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        """Show the newest reading and the clock; discard the later readings when the user confirms. Without a loaded
        GlucoCraft the flow ends with not_loaded, before and after the confirmation alike, and the notice stays."""
        coordinator = loaded_coordinator(self.hass)
        if coordinator is None:
            return self.async_abort(reason="not_loaded")
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_DISCARD) is True:
                await self.hass.async_add_executor_job(coordinator.discard_readings_after_clock)
                coordinator.async_update_listeners()
                self.hass.async_create_task(coordinator.async_catch_up())
                ir.async_delete_issue(self.hass, DOMAIN, CLOCK_ISSUE_ID)
                return self.async_create_entry(title="", data={})
            errors["base"] = "discard_required"
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Required(CONF_DISCARD, default=False): bool}),
            description_placeholders=clock_placeholders(coordinator),
            errors=errors,
        )


class EntityIdsRepairFlow(RepairsFlow):
    """Entities have ids other than the ones they were created with (renamed_entities.py). The confirmation gives them
    their original ids; an original id that another entity has is left alone, and the notice stays open for it."""

    def __init__(self, entry_id: str | None) -> None:
        """Remember the entry of the notice."""
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        """Start with the confirmation step."""
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        """List the changed ids; put them back when the user confirms."""
        changed = changed_entity_ids(self.hass, self._entry_id)
        if not changed:  # put back by other means since the notice opened
            if self._entry_id is not None:
                async_update_issue(self.hass, self._entry_id)
            return self.async_create_entry(title="", data={})
        if user_input is None:
            return self.async_show_form(
                step_id="confirm",
                data_schema=vol.Schema({}),
                description_placeholders={"changed": changed_text(changed)},
            )
        blocked = async_put_back(self.hass, self._entry_id)
        if blocked:
            return self.async_abort(reason="id_taken", description_placeholders={"blocked": changed_text(blocked)})
        return self.async_create_entry(title="", data={})


def clock_placeholders(coordinator: Any) -> dict[str, str]:
    """Return the newest stored reading and the clock, as the notice shows them (the date and time format in use)."""
    rg = coordinator.regional
    return {"newest": clock_stamp(rg, coordinator.storage.newest_timestamp), "now": clock_stamp(rg, int(time.time()))}


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None) -> RepairsFlow:
    """Return the fix flow of a GlucoCraft issue: the changed ids (ENTITY_IDS_ISSUE_ID) or the clock
    (CLOCK_ISSUE_ID)."""
    entry_id = (data or {}).get("entry_id")
    return EntityIdsRepairFlow(entry_id) if issue_id == ENTITY_IDS_ISSUE_ID else ClockRepairFlow(entry_id)
