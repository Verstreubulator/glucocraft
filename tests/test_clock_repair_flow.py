# License: AGPL-3.0-only (LICENSE).
"""The fix flow of the clock notice (repairs.ClockRepairFlow): stored readings later than the system clock.

Rules under test: nothing is discarded without the ticked box; a confirmed discard discards, closes the notice and
starts the catch-up; while GlucoCraft is not loaded the flow says so (abort not_loaded), never reports a success, and the
notice stays open.
"""

from __future__ import annotations

import asyncio
import json
import types

import pytest
from glucocraft import repairs

import ha_stub
from conftest import REPO

ISSUES = ha_stub.ISSUE_REGISTRY
ISSUE = ("glucocraft", repairs.CLOCK_ISSUE_ID)


class _Coordinator:
    """The part of the coordinator the flow uses."""

    def __init__(self):
        self.discarded = 0
        self.caught_up = 0
        self.regional = types.SimpleNamespace()
        self.storage = types.SimpleNamespace(newest_timestamp=0)

    def discard_readings_after_clock(self):
        """Count the discard."""
        self.discarded += 1

    def async_update_listeners(self):
        """Nothing to update here."""

    async def async_catch_up(self):
        """Count the catch-up."""
        self.caught_up += 1


@pytest.fixture
def flow(monkeypatch):
    """Factory: the clock fix flow with the coordinator `coordinator` loaded (None: GlucoCraft not loaded)."""
    ISSUES.issues.clear()

    def make(coordinator):
        monkeypatch.setattr(repairs, "loaded_coordinator", lambda hass: coordinator)
        monkeypatch.setattr(repairs, "clock_placeholders", lambda c: {"newest": "n", "now": "c"})

        async def executor(fn, *args):
            return fn(*args)

        tasks = []  # the tasks the flow starts, run after it (the flow itself runs in asyncio.run)
        hass = types.SimpleNamespace(async_add_executor_job=executor, async_create_task=tasks.append, tasks=tasks)
        ISSUES.issues[ISSUE] = object()  # the open notice
        f = asyncio.run(repairs.async_create_fix_flow(hass, repairs.CLOCK_ISSUE_ID, {"entry_id": "e1"}))
        f.hass = hass
        f.async_show_form = lambda **kw: {"type": "form", **kw}
        f.async_abort = lambda **kw: {"type": "abort", **kw}
        f.async_create_entry = lambda **kw: {"type": "create_entry", **kw}
        return f

    return make


def test_the_box_must_be_ticked(flow):
    c = _Coordinator()
    f = flow(c)
    assert asyncio.run(f.async_step_init())["type"] == "form"
    result = asyncio.run(f.async_step_confirm({repairs.CONF_DISCARD: False}))
    assert result["type"] == "form" and result["errors"] == {"base": "discard_required"}
    assert c.discarded == 0 and ISSUE in ISSUES.issues


def test_a_confirmed_discard_discards_and_closes_the_notice(flow):
    c = _Coordinator()
    f = flow(c)
    result = asyncio.run(f.async_step_confirm({repairs.CONF_DISCARD: True}))
    for task in f.hass.tasks:
        asyncio.run(task)
    assert result["type"] == "create_entry"
    assert c.discarded == 1 and c.caught_up == 1 and ISSUE not in ISSUES.issues


@pytest.mark.parametrize("user_input", [None, {repairs.CONF_DISCARD: True}], ids=["opened", "confirmed"])
def test_not_loaded_says_so_and_keeps_the_notice(flow, user_input):
    """Without a loaded GlucoCraft nothing can be discarded: the flow ends with not_loaded, not with a success."""
    result = asyncio.run(flow(None).async_step_confirm(user_input))
    assert result == {"type": "abort", "reason": "not_loaded"}
    assert ISSUE in ISSUES.issues


def test_the_abort_reason_has_a_text_in_both_catalogues():
    for name in ("strings.json", "translations/en.json"):
        data = json.loads((REPO / "custom_components" / "glucocraft" / name).read_text(encoding="utf-8"))
        text = data["issues"]["clock_behind"]["fix_flow"]["abort"]["not_loaded"]
        assert "not running" in text and "nothing can be discarded" in text
