# License: AGPL-3.0-only (LICENSE).
"""An entity whose id was changed: the Repairs notice, the fix flow and the display card.

Rules under test:
- A change of an id is the user's choice and is never undone by itself: a change found at
  setup and a change made while GlucoCraft runs both stay, and the fixable notice names them; for a change made while
  GlucoCraft runs, shortly after, once for a whole device rename. A change of another integration's entity, and any
  other registry update, starts nothing. The notice of earlier versions, which undid a change, is deleted at setup.
- That notice opens while an entity's id is not the one it was created with, lists the first few and how many more, and
  closes by itself when every id is the original one.
- The fix flow changes nothing until it is confirmed; it then gives the original ids back; an original id another
  entity has is left alone and the notice stays open.
- The display card is never green without a value, and nothing else changes: the kept value while GlucoCraft's
  entities are unavailable, a warm-up, an expired sensor.
"""

from __future__ import annotations

import asyncio
import json
import types

import pytest
from glucocraft import renamed_entities as R
from glucocraft import repairs, stale_entities

import ha_stub
from conftest import REPO
from node_runner import needs_node
from test_accessibility import _card

PKG = REPO / "custom_components" / "glucocraft"

ISSUE = ("glucocraft", R.ENTITY_IDS_ISSUE_ID)
REG = ha_stub.ENTITY_REGISTRY
ISSUES = ha_stub.ISSUE_REGISTRY


def entity(key: str, entity_id: str):
    """A created entity, as GlucoCraftEntity leaves it for stale_entities.record_created."""
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


def flow(hass, issue_id=R.ENTITY_IDS_ISSUE_ID):
    """The fix flow of the notice, with the small part of RepairsFlow it uses."""
    f = asyncio.run(repairs.async_create_fix_flow(hass, issue_id, {"entry_id": "e1"}))
    f.hass = hass
    f.async_show_form = lambda **kw: {"type": "form", **kw}
    f.async_abort = lambda **kw: {"type": "abort", **kw}
    f.async_create_entry = lambda **kw: {"type": "create_entry", **kw}
    return f


def test_the_entity_keeps_the_id_it_was_created_with():
    src = (PKG / "entity.py").read_text(encoding="utf-8")
    assert "self._expected_entity_id = self.entity_id" in src


def test_no_notice_while_every_id_is_the_original_one(hass):
    R.async_update_issue(hass, "e1")
    assert ISSUE not in ISSUES.issues


def test_a_changed_id_opens_a_fixable_notice_naming_both_ids(hass):
    REG.async_update_entity("sensor.glucocraft_a", new_entity_id="sensor.mine")
    R.async_update_issue(hass, "e1")
    issue = ISSUES.issues[ISSUE]
    assert issue.is_fixable is True and issue.severity == "error" and issue.data == {"entry_id": "e1"}
    assert issue.translation_key == "entity_ids_changed"
    text = issue.translation_placeholders["changed"]
    assert "sensor.mine" in text and "sensor.glucocraft_a" in text and "sensor.glucocraft_b" not in text


def test_the_notice_closes_by_itself_when_the_id_is_back(hass):
    REG.async_update_entity("sensor.glucocraft_a", new_entity_id="sensor.mine")
    R.async_update_issue(hass, "e1")
    REG.async_update_entity("sensor.mine", new_entity_id="sensor.glucocraft_a")
    R.async_update_issue(hass, "e1")
    assert ISSUE not in ISSUES.issues


def test_an_entity_of_another_integration_is_not_looked_at(hass):
    REG.put("sensor.other", "e2_x", platform="other", config_entry_id="e2")
    REG.async_update_entity("sensor.other", new_entity_id="sensor.other_2")
    assert R.changed_entity_ids(hass, "e1") == []


def test_the_list_shows_the_first_few_then_how_many_more():
    changed = [(f"sensor.n{i}", f"sensor.glucocraft_{i}") for i in range(R.SHOWN_IDS + 3)]
    lines = R.changed_text(changed).split("\n")
    assert len(lines) == R.SHOWN_IDS + 1 and "3 more" in lines[-1]
    assert "more" not in R.changed_text(changed[: R.SHOWN_IDS])


def later_on(monkeypatch):
    """Keep the waits instead of starting them: (the waiting functions, the cancelled ones)."""
    timers, cancelled = [], []

    def later(_hass, _delay, fn):
        timers.append(fn)
        return lambda: cancelled.append(fn)

    monkeypatch.setattr(R, "async_call_later", later)
    return timers, cancelled


def rename(hass, old, new):
    """Change an id as Home Assistant does: the registry first, then its event."""
    REG.async_update_entity(old, new_entity_id=new)
    hass.bus.listeners[R.er.EVENT_ENTITY_REGISTRY_UPDATED](
        types.SimpleNamespace(data={"action": "update", "entity_id": new, "old_entity_id": old})
    )


OLD_PUT_BACK = ("glucocraft", R.OLD_PUT_BACK_ISSUE_ID)


def test_a_change_found_at_setup_stays_for_the_user(hass, entry, monkeypatch):
    timers, _ = later_on(monkeypatch)
    REG.async_update_entity("sensor.glucocraft_a", new_entity_id="sensor.mine")
    R.async_setup_entity_id_watch(hass, entry)
    assert ISSUE in ISSUES.issues  # at setup, without any event
    assert "sensor.mine" in REG.entities and timers == []


def test_a_live_change_stays_and_the_notice_names_it(hass, entry, monkeypatch):
    timers, _ = later_on(monkeypatch)
    R.async_setup_entity_id_watch(hass, entry)
    rename(hass, "sensor.glucocraft_a", "sensor.mine")
    assert ISSUE not in ISSUES.issues  # not before the wait
    timers[-1](None)
    assert "sensor.mine" in REG.entities and "sensor.glucocraft_a" not in REG.entities  # never undone by itself
    notice = ISSUES.issues[ISSUE]
    assert notice.is_fixable is True and notice.severity == "error"
    text = notice.translation_placeholders["changed"]
    assert "sensor.glucocraft_a" in text and "sensor.mine" in text and "sensor.glucocraft_b" not in text


def test_a_whole_device_rename_gives_one_notice(hass, entry, monkeypatch):
    """77 ids, as many as the install-test Home Assistant has; each change starts the wait again."""
    timers, cancelled = later_on(monkeypatch)
    made = [entity(f"n{i}", f"sensor.glucocraft_n{i}") for i in range(75)]
    stale_entities.record_created(hass, entry, "sensor", made)
    for e in made:
        REG.put(e.entity_id, e.unique_id, platform="glucocraft", config_entry_id="e1")
    R.async_setup_entity_id_watch(hass, entry)
    originals = [e.entity_id for e in made] + ["sensor.glucocraft_a", "sensor.glucocraft_b"]
    for original in originals:
        rename(hass, original, original.replace("glucocraft", "renamed"))
    assert len(timers) == 77 and cancelled == timers[:-1]
    assert ISSUE not in ISSUES.issues
    timers[-1](None)
    assert not any(original in REG.entities for original in originals)
    lines = ISSUES.issues[ISSUE].translation_placeholders["changed"].split("\n")
    assert len(lines) == R.SHOWN_IDS + 1 and "72 more" in lines[-1]


def test_a_later_change_is_added_to_the_notice(hass, entry, monkeypatch):
    timers, _ = later_on(monkeypatch)
    REG.async_update_entity("sensor.glucocraft_a", new_entity_id="sensor.mine")
    R.async_setup_entity_id_watch(hass, entry)
    rename(hass, "sensor.glucocraft_b", "sensor.mine_b")
    timers[-1](None)
    lines = ISSUES.issues[ISSUE].translation_placeholders["changed"].split("\n")
    assert len(lines) == 2 and "sensor.glucocraft_a" in lines[0] and "sensor.glucocraft_b" in lines[1]
    assert {"sensor.mine", "sensor.mine_b"} <= set(REG.entities)


def test_changing_the_id_back_by_hand_closes_the_notice(hass, entry, monkeypatch):
    timers, _ = later_on(monkeypatch)
    R.async_setup_entity_id_watch(hass, entry)
    rename(hass, "sensor.glucocraft_a", "sensor.mine")
    timers[-1](None)
    assert ISSUE in ISSUES.issues
    rename(hass, "sensor.mine", "sensor.glucocraft_a")
    timers[-1](None)
    assert ISSUE not in ISSUES.issues


def test_the_setup_deletes_the_notice_of_the_old_undoing(hass, entry, monkeypatch):
    """A notice entity_ids_put_back left by an earlier version (persistent, so restored at the start)."""
    later_on(monkeypatch)
    ISSUES.create(*OLD_PUT_BACK, translation_key="entity_ids_put_back", is_persistent=True)
    R.async_setup_entity_id_watch(hass, entry)
    assert OLD_PUT_BACK not in ISSUES.issues


def test_nothing_is_undone_anywhere():
    """The watch only checks; only the fix flow, on confirmation, changes an id."""
    src = (PKG / "renamed_entities.py").read_text(encoding="utf-8")
    watch = src[src.index("def async_setup_entity_id_watch") :]
    assert "async_put_back" not in watch and "async_update_entity" not in watch
    assert "async_put_back(" in (PKG / "repairs.py").read_text(encoding="utf-8")


def test_the_entity_has_public_properties_for_the_watch():
    src = (PKG / "entity.py").read_text(encoding="utf-8")
    assert "def unique_id(self) -> str:" in src and "def expected_entity_id(self) -> str:" in src
    for name in ("stale_entities.py", "renamed_entities.py"):
        text = (PKG / name).read_text(encoding="utf-8")
        assert "._attr_unique_id" not in text and "._expected_entity_id" not in text, name


@pytest.mark.parametrize(
    "data",
    [
        {"action": "update", "entity_id": "sensor.glucocraft_a"},  # no change of the id
        {"action": "create", "entity_id": "sensor.glucocraft_a"},
        {"action": "remove", "entity_id": "sensor.gone", "old_entity_id": "sensor.gone_2"},
        {"action": "update", "entity_id": "sensor.other", "old_entity_id": "sensor.other_2"},  # another integration
    ],
)
def test_the_watch_ignores_other_registry_updates(hass, entry, monkeypatch, data):
    timers = []
    monkeypatch.setattr(R, "async_call_later", lambda *a: timers.append(a) or (lambda: None))
    REG.put("sensor.other", "e2_x", platform="other", config_entry_id="e2")
    R.async_setup_entity_id_watch(hass, entry)
    hass.bus.listeners[R.er.EVENT_ENTITY_REGISTRY_UPDATED](types.SimpleNamespace(data=data))
    assert timers == []


def test_unloading_stops_a_waiting_check(hass, entry, monkeypatch):
    cancelled = []
    monkeypatch.setattr(R, "async_call_later", lambda *a: lambda: cancelled.append(1))
    R.async_setup_entity_id_watch(hass, entry)
    hass.bus.listeners[R.er.EVENT_ENTITY_REGISTRY_UPDATED](
        types.SimpleNamespace(
            data={"action": "update", "entity_id": "sensor.glucocraft_a", "old_entity_id": "sensor.x"}
        )
    )
    for stop in entry.on_unload:
        stop()
    assert cancelled == [1]


def test_the_fix_flows_are_chosen_by_the_issue(hass):
    assert type(flow(hass)).__name__ == "EntityIdsRepairFlow"
    assert type(flow(hass, "clock_behind")).__name__ == "ClockRepairFlow"


def test_the_fix_flow_changes_nothing_until_it_is_confirmed(hass):
    REG.async_update_entity("sensor.glucocraft_a", new_entity_id="sensor.mine")
    R.async_update_issue(hass, "e1")
    result = asyncio.run(flow(hass).async_step_init())
    assert result["type"] == "form" and result["step_id"] == "confirm"
    assert "sensor.mine" in result["description_placeholders"]["changed"]
    assert "sensor.mine" in REG.entities and ISSUE in ISSUES.issues


def test_the_confirmation_gives_the_original_ids_back_and_closes_the_notice(hass):
    REG.async_update_entity("sensor.glucocraft_a", new_entity_id="sensor.mine")
    REG.async_update_entity("sensor.glucocraft_b", new_entity_id="sensor.mine_b")
    R.async_update_issue(hass, "e1")
    result = asyncio.run(flow(hass).async_step_confirm({}))
    assert result["type"] == "create_entry"
    assert {"sensor.glucocraft_a", "sensor.glucocraft_b"} <= set(REG.entities) and ISSUE not in ISSUES.issues


def test_an_original_id_another_entity_has_is_left_alone_and_the_notice_stays(hass):
    REG.async_update_entity("sensor.glucocraft_a", new_entity_id="sensor.mine")
    REG.async_update_entity("sensor.glucocraft_b", new_entity_id="sensor.mine_b")
    REG.put("sensor.glucocraft_a", "other_x", platform="other", config_entry_id="e2")
    R.async_update_issue(hass, "e1")
    result = asyncio.run(flow(hass).async_step_confirm({}))
    assert result["type"] == "abort" and result["reason"] == "id_taken"
    assert "sensor.mine" in result["description_placeholders"]["blocked"]
    assert "sensor.glucocraft_b" in REG.entities  # the other one was put back
    assert REG.entities["sensor.glucocraft_a"].unique_id == "other_x" and "sensor.mine" in REG.entities
    assert ISSUE in ISSUES.issues


def test_opening_the_fix_flow_after_the_ids_are_back_closes_the_notice(hass):
    ISSUES.create(*ISSUE, translation_key="entity_ids_changed")
    assert asyncio.run(flow(hass).async_step_init())["type"] == "create_entry"
    assert ISSUE not in ISSUES.issues


def test_the_documentation_describes_the_notice_and_no_undoing():
    docs = (PKG / "i18n" / "en" / "documentation.json").read_text(encoding="utf-8")
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "Keep GlucoCraft's entity identifiers" in docs and "Do not rename" not in docs
    assert docs.count("Entity ids of GlucoCraft were changed") >= 3  # Entity Names, Notifications and Troubleshooting
    for text in (docs, readme, (PKG / "strings.json").read_text(encoding="utf-8")):
        assert "put its entity ids back" not in text and "within seconds" not in text
    assert "leaves a\nchanged id as it is" in readme


def test_the_texts_say_what_the_fix_does_and_what_it_leaves():
    text = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))["issues"]["entity_ids_changed"]
    body = text["fix_flow"]["step"]["confirm"]["description"]
    assert "{changed}" in body and "history" in body and "automation" in body
    assert "{blocked}" in text["fix_flow"]["abort"]["id_taken"]


# ------------------------------------------------------------------------------------------------ the display card
GONE = 'delete states["sensor.glucocraft_last_glucose_reading"];'
SHOW = 'card._render(); return { svg: svg.innerHTML, label: svg.attrs["aria-label"] };'
GREEN = "#43a047"  # the status color of "current", as the card draws it
TAGS = ["glucocraft-display-card", "glucocraft-display-card-wide"]


@needs_node
@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("compact", [0, 360])
def test_the_card_is_not_green_when_the_value_is_gone_at_load(tag, compact):
    """The page is opened after the rename: nothing kept."""
    out = _card(
        tag,
        value=127,
        zone="in_range",
        label="",
        compact=compact,
        extra=f"{GONE} card._kept = {{}}; card._lastMg = {{}}; {SHOW}",
    )["extra"]
    assert out["label"].startswith("No current data.") and ">No current" in out["svg"]
    assert GREEN not in out["svg"] and ">127</text>" not in out["svg"]


@needs_node
@pytest.mark.parametrize("tag", TAGS)
def test_a_value_kept_from_earlier_is_only_the_last_reading(tag):
    """The page was open during the rename: the kept number never stays in the value's place with a fresh age."""
    out = _card(tag, value=127, zone="in_range", label="", extra=f"{GONE} {SHOW}")["extra"]
    assert out["label"].startswith("No current data.") and "Last reading: 127 mg/dL" in out["svg"]
    assert GREEN not in out["svg"] and ">127</text>" not in out["svg"]


@needs_node
@pytest.mark.parametrize("tag", TAGS)
def test_the_card_with_a_value_stays_green(tag):
    out = _card(tag, value=127, zone="in_range", label="")
    assert GREEN in out["svg"] and ">No current" not in out["svg"] and ">127</text>" in out["svg"]


@needs_node
@pytest.mark.parametrize("tag", TAGS)
def test_while_unavailable_the_value_stays_kept(tag):
    """A restart or a reload: GlucoCraft is not answering, so the rule does not apply."""
    out = _card(
        tag,
        value=127,
        zone="in_range",
        label="",
        extra=(
            'for (const k of ["sensor.glucocraft_last_glucose_reading", "sensor.glucocraft_status"]) '
            'states[k] = { state: "unavailable", last_updated: "a", attributes: {} };' + SHOW
        ),
    )["extra"]
    assert ">No current" not in out["svg"] and ">127</text>" in out["svg"]


@needs_node
@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("status,words", [("warm_up", "Sensor warming up"), ("expired", None)])
def test_a_warm_up_and_an_expired_sensor_are_left_as_they_are(tag, status, words):
    out = _card(tag, status=status, value=127, zone="in_range", label="", extra=f"{GONE} {SHOW}")["extra"]
    assert not out["label"].startswith("No current data") and GREEN not in out["svg"]
    if words:
        assert out["label"].startswith(words)
