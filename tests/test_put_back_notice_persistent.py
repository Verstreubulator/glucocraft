# License: AGPL-3.0-only (LICENSE).
"""The notice that GlucoCraft put an entity id back (renamed_entities.async_put_back_notice).

Rules under test:
- The notice is persistent: it stays, also across a restart, until the user dismisses it, so that a change undone
  shortly before a restart is still reported. It is not fixable and a warning.
- Nothing at setup closes it: a new setup of the watch leaves an open put-back notice as it is.
- A further change undone later replaces it (deleted first, so it shows again after the user dismissed it), listing
  every id put back since the setup.
- The entities give the watch their unique id and the id they were created with through public properties
  (GlucoCraftEntity.unique_id, GlucoCraftEntity.expected_entity_id).
"""

from __future__ import annotations

import types

import pytest
from glucocraft import renamed_entities as R
from glucocraft import stale_entities

import ha_stub
from conftest import REPO

PKG = REPO / "custom_components" / "glucocraft"
PUT_BACK = ("glucocraft", R.PUT_BACK_ISSUE_ID)
REG = ha_stub.ENTITY_REGISTRY
ISSUES = ha_stub.ISSUE_REGISTRY


def entity(key: str, entity_id: str):
    """A created entity, as GlucoCraftEntity shows itself to stale_entities.record_created."""
    return types.SimpleNamespace(unique_id=f"e1_{key}", expected_entity_id=entity_id, entity_id=entity_id)


@pytest.fixture
def entry():
    """The config entry; what it would release at its unload is kept in on_unload."""
    e = types.SimpleNamespace(entry_id="e1", on_unload=[])
    e.async_on_unload = e.on_unload.append
    return e


@pytest.fixture
def hass(entry):
    """A Home Assistant with two GlucoCraft entities recorded and registered."""
    REG.entities.clear()
    ISSUES.issues.clear()
    listeners = {}

    def listen(name, fn):
        listeners[name] = fn
        return lambda: None

    h = types.SimpleNamespace(data={}, bus=types.SimpleNamespace(listeners=listeners, async_listen=listen))
    stale_entities.start_setup(h, entry)
    made = [entity("a", "sensor.glucocraft_a"), entity("b", "sensor.glucocraft_b")]
    stale_entities.record_created(h, entry, "sensor", made)
    for e in made:
        REG.put(e.entity_id, e.unique_id, platform="glucocraft", config_entry_id="e1")
    return h


def later_on(monkeypatch):
    """Keep the waits instead of starting them: the waiting functions."""
    timers = []

    def later(_hass, _delay, fn):
        timers.append(fn)
        return lambda: None

    monkeypatch.setattr(R, "async_call_later", later)
    return timers


def rename(hass, old, new):
    """Change an id as Home Assistant does: the registry first, then its event."""
    REG.async_update_entity(old, new_entity_id=new)
    hass.bus.listeners[R.er.EVENT_ENTITY_REGISTRY_UPDATED](
        types.SimpleNamespace(data={"action": "update", "entity_id": new, "old_entity_id": old})
    )


def test_the_put_back_notice_is_persistent(hass, entry, monkeypatch):
    timers = later_on(monkeypatch)
    R.async_setup_entity_id_watch(hass, entry)
    rename(hass, "sensor.glucocraft_a", "sensor.mine")
    timers[-1](None)
    notice = ISSUES.issues[PUT_BACK]
    assert notice.is_persistent is True and notice.is_fixable is False and notice.severity == "warning"
    assert "sensor.mine" in notice.translation_placeholders["changed"]


def test_a_new_setup_leaves_the_notice_open(hass, entry, monkeypatch):
    """A restart: the watch is set up again and the notice, restored by Home Assistant, stays."""
    timers = later_on(monkeypatch)
    R.async_setup_entity_id_watch(hass, entry)
    rename(hass, "sensor.glucocraft_a", "sensor.mine")
    timers[-1](None)
    stale_entities.start_setup(hass, entry)
    stale_entities.record_created(
        hass, entry, "sensor", [entity("a", "sensor.glucocraft_a"), entity("b", "sensor.glucocraft_b")]
    )
    R.async_setup_entity_id_watch(hass, entry)
    assert PUT_BACK in ISSUES.issues and ISSUES.issues[PUT_BACK].is_persistent is True


def test_a_later_change_lists_every_id_put_back_since_the_setup(hass, entry, monkeypatch):
    timers = later_on(monkeypatch)
    R.async_setup_entity_id_watch(hass, entry)
    rename(hass, "sensor.glucocraft_a", "sensor.mine_a")
    timers[-1](None)
    rename(hass, "sensor.glucocraft_b", "sensor.mine_b")
    timers[-1](None)
    text = ISSUES.issues[PUT_BACK].translation_placeholders["changed"]
    assert "sensor.mine_a" in text and "sensor.mine_b" in text
    assert ISSUES.issues[PUT_BACK].is_persistent is True


def test_the_entity_has_public_properties_for_the_watch():
    src = (PKG / "entity.py").read_text(encoding="utf-8")
    assert "def unique_id(self) -> str:" in src and "def expected_entity_id(self) -> str:" in src
    for name in ("stale_entities.py", "renamed_entities.py"):
        text = (PKG / name).read_text(encoding="utf-8")
        assert "._attr_unique_id" not in text and "._expected_entity_id" not in text, name
