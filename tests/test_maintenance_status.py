# License: AGPL-3.0-only (LICENSE).
"""The Maintenance status (maintenance.py): what is counted, its one published source and the dashboard's two views.

Rules under test:
- Counted, one issue each: a wall panel offline for 10 minutes (while its offline notice is shown), an open
  GlucoCraft Repairs issue (active and not ignored; its title in Home Assistant's language), a failed CSV export
  (until an export succeeds). Not counted: no current data, the sensor expiry.
- One source: the Configuration sensor's attribute maintenance_issues; a change writes the states again at once.
- The Start page's Maintenance button: green with the wrench and "No issues", or red with an alert icon and
  "1 issue" / "N issues", named "Maintenance: no issues" / "Maintenance: N issues"; never color alone.
- The Maintenance page's hero card "Maintenance issues": only while there are issues, each with where to resolve it.
"""

from __future__ import annotations

import asyncio
import json
import textwrap
import time
import types

import pytest
from glucocraft import maintenance as M

from node_runner import needs_node
from test_accessibility_checks import JS, UI_STATES
from test_dashboard_cards import _ui
from test_trend_and_configuration import settings_coordinator


# ------------------------------------------------------------------------------------------------ what is counted
def test_each_kind_is_one_issue_in_a_fixed_order():
    changes = []
    m = M.MaintenanceIssues(types.SimpleNamespace(), lambda: changes.append(1))
    assert m.issues() == []
    m.export_failed("[Errno 28] No space left on device")
    m.panel_offline("d2", "panel a")
    m.panel_offline("d1", "Hall panel")
    m._set("_repairs", ["Glucose source updates every 15 minutes"])
    assert m.issues() == [
        {"kind": "panel_offline", "name": "Hall panel"},
        {"kind": "panel_offline", "name": "panel a"},
        {"kind": "repairs", "title": "Glucose source updates every 15 minutes"},
        {"kind": "csv_export", "reason": "[Errno 28] No space left on device"},
    ]
    assert len(changes) == 4
    # back online, an export that succeeds: no longer counted; an unchanged value reports no change
    m.panel_back("d2")
    m.export_succeeded()
    m.export_succeeded()
    m.panel_back("d9")
    assert [i["kind"] for i in m.issues()] == ["panel_offline", "repairs"] and len(changes) == 6


def test_open_repairs_issues_are_counted_with_their_titles(monkeypatch):
    issue = lambda iid, key, active=True, dismissed=None, ph=None: types.SimpleNamespace(  # noqa: E731
        issue_id=iid, translation_key=key, active=active, dismissed_version=dismissed, translation_placeholders=ph
    )
    registry = {
        ("glucocraft", "slow_source_sensor.cgm"): issue("slow_source_sensor.cgm", "slow_source", ph={"minutes": 15}),
        ("glucocraft", "clock_behind"): issue("clock_behind", "clock_behind", dismissed="2026.10.0"),  # ignored
        ("glucocraft", "storage_damaged"): issue("storage_damaged", "storage_damaged", active=False),  # not active
        ("glucocraft", "new_issue"): issue("new_issue", "new_issue"),  # no title known: its key
        ("other", "slow_source"): issue("slow_source", "slow_source"),  # another integration's
    }
    monkeypatch.setattr(M.ir, "async_get", lambda hass: types.SimpleNamespace(issues=registry))
    asked = []

    async def translations(hass, language, category, integrations):
        asked.append((language, category, set(integrations)))
        return {"component.glucocraft.issues.slow_source.title": "Glucose source updates every {minutes} minutes"}

    monkeypatch.setattr(M.translation, "async_get_translations", translations)
    m = M.MaintenanceIssues(types.SimpleNamespace(config=types.SimpleNamespace(language="en")), lambda: None)
    asyncio.run(m.async_refresh_repairs())
    assert m.issues() == [
        {"kind": "repairs", "title": "Glucose source updates every 15 minutes"},
        {"kind": "repairs", "title": "new_issue"},
    ]
    assert asked == [("en", "issues", {"glucocraft"})]


def test_repairs_are_read_again_after_a_change_of_glucocrafts_only(monkeypatch):
    listeners, tasks = {}, []
    hass = types.SimpleNamespace(
        bus=types.SimpleNamespace(async_listen=lambda kind, fn: listeners.setdefault(kind, fn) and (lambda: None)),
        async_create_task=lambda coro: tasks.append(coro.close()),
    )
    m = M.MaintenanceIssues(hass, lambda: None)
    m.async_start()
    assert len(tasks) == 1  # read once at the start
    changed = listeners[M.ir.EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED]
    changed(types.SimpleNamespace(data={"domain": "other", "issue_id": "x"}))
    assert len(tasks) == 1
    changed(types.SimpleNamespace(data={"domain": "glucocraft", "issue_id": "slow_source_sensor.cgm"}))
    assert len(tasks) == 2


def test_no_current_data_and_the_sensor_expiry_are_not_counted(tmp_path):
    c = settings_coordinator(tmp_path)
    c.current_glucose, c.last_reading_ts, c.no_current_data = 120.0, int(time.time()) - 3 * 3600, True
    assert c.no_current_data and c.configuration()["maintenance_issues"] == []
    assert {M.KIND_PANEL_OFFLINE, M.KIND_REPAIRS, M.KIND_CSV_EXPORT} == {"panel_offline", "repairs", "csv_export"}


# ------------------------------------------------------------------------------------------------ one source
def test_one_published_source_written_again_at_once(tmp_path, monkeypatch):
    c = settings_coordinator(tmp_path)
    written = []
    monkeypatch.setattr(c, "async_update_listeners", lambda: written.append(c.configuration()["maintenance_issues"]))
    c.maintenance._on_change = c.async_update_listeners
    c.maintenance.panel_offline("d1", "Hall panel")
    assert written == [[{"kind": "panel_offline", "name": "Hall panel"}]]


def test_a_failed_export_is_an_issue_until_an_export_succeeds(monkeypatch, tmp_path):
    from test_entity_units_and_recorder import Failed, _action_hass, _load_init

    init = _load_init()
    monkeypatch.setattr(init, "HomeAssistantError", Failed)
    m = M.MaintenanceIssues(types.SimpleNamespace(), lambda: None)
    fail = [True]

    def locked(fn):
        return fn()

    def write_export(*args):
        if fail[0]:
            raise OSError(28, "No space left on device")
        return {"files": ["glucocraft-readings_x.csv"], "readings": 0}

    monkeypatch.setattr(init, "write_export", write_export)

    coordinator = types.SimpleNamespace(
        reports=types.SimpleNamespace(lab_results=[]),
        display_unit="mg/dL",
        storage=types.SimpleNamespace(readings=[]),
        locked=locked,
        maintenance=m,
    )
    monkeypatch.setattr(init, "_coordinator_for", lambda hass: coordinator)
    monkeypatch.setattr(init, "export_message", lambda result: "saved")
    with pytest.raises(Failed):
        asyncio.run(init._handle_export(_action_hass(tmp_path), types.SimpleNamespace(data={})))
    assert m.issues() == [{"kind": "csv_export", "reason": "the disk is full"}]
    fail[0] = False
    asyncio.run(init._handle_export(_action_hass(tmp_path), types.SimpleNamespace(data={})))
    assert m.issues() == []


# ------------------------------------------------------------------------------------------------ the dashboard
def _pages(issues) -> str:
    states = json.loads(json.dumps(UI_STATES))
    if issues is not None:
        states["sensor.glucocraft_configuration"]["attributes"]["maintenance_issues"] = issues
    return _ui(
        textwrap.dedent(f"""
        const p = new (customElements.get("glucocraft-pages"))(); p.setConfig({{}});
        p._hass = {{ states: {json.dumps(states)} }}; p.draw();
        return p.shadowRoot.innerHTML;
    """)
    )


def _button(html: str) -> str:
    start = html.index('href="/glucocraft-home/maintenance"')
    return html[html.rindex("<a ", 0, start) : html.index("</a>", start)]


@needs_node
def test_the_maintenance_button_shows_the_status_never_by_color_alone():
    none = _button(_pages([]))
    assert 'aria-label="Maintenance: no issues"' in none and 'icon="mdi:wrench"' in none
    assert "#43A047" in none and '<div class="bl">No issues</div>' in none
    one = _button(_pages([{"kind": "csv_export", "reason": "x"}]))
    assert 'aria-label="Maintenance: 1 issue"' in one and '<div class="bl">1 issue</div>' in one
    two = _button(_pages([{"kind": "panel_offline", "name": "A"}, {"kind": "repairs", "title": "B"}]))
    assert 'aria-label="Maintenance: 2 issues"' in two and '<div class="bl">2 issues</div>' in two
    assert 'icon="mdi:alert-circle-outline"' in two and "#E53935" in two and "mdi:wrench" not in two
    # before GlucoCraft publishes a status: the plain button, as Settings Status and Documentation
    plain = _button(_pages(None))
    assert 'aria-label="Open Maintenance"' in plain and 'class="bl"' not in plain and "#43A047" not in plain


def _hero(issues) -> str:
    states = json.loads(json.dumps(UI_STATES))
    states["sensor.glucocraft_configuration"]["attributes"]["maintenance_issues"] = issues
    return _ui(
        textwrap.dedent(f"""
        const c = new (customElements.get("glucocraft-maintenance-issues"))(); c.setConfig({{}});
        c._hass = {{ states: {json.dumps(states)} }}; c.draw();
        return [c.style.display, c.shadowRoot.innerHTML];
    """)
    )


@needs_node
def test_the_hero_card_lists_each_issue_and_where_to_resolve_it():
    display, html = _hero([])
    assert display == "none" and html == ""
    display, html = _hero(
        [
            {"kind": "panel_offline", "name": "Hall <panel>"},
            {"kind": "repairs", "title": "Glucose source updates every 15 minutes"},
            {"kind": "csv_export", "reason": "[Errno 28] No space left on device"},
        ]
    )
    assert display == "" and 'role="region" aria-label="Maintenance issues"' in html
    assert ">Maintenance issues</div>" in html and ">3 issues</div>" in html and html.count("<li>") == 3
    assert (
        "<b>Wall panel Hall &lt;panel&gt;:</b> offline for 10 minutes or more" in html and "GlucoCraft Panels" in html
    )
    assert "<b>Repairs:</b> Glucose source updates every 15 minutes." in html and 'href="/config/repairs"' in html
    assert "<b>CSV export:</b> the last export failed ([Errno 28] No space left on device)" in html


def test_the_maintenance_page_opens_with_the_hero_card():
    src = JS["strategy"]
    page = src[src.index('sub(gcT("strategy.page.maintenance")') : src.index("Settings Status (read-only")]
    assert page.index('type: "custom:glucocraft-maintenance-issues"') < page.index('view: "status", hero: true')
    assert '["glucocraft-maintenance-issues", GlucoCraftMaintenanceIssues]' in JS["ui"]
