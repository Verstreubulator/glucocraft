# License: AGPL-3.0-only (LICENSE).
"""The other settings: the report printer, the dashboard in the sidebar, and the wall-panel address (created on request,
kept, made anew).

The outputs checked: the report data and the print action (the IPP call), the dashboard registration, the panel
webhook and its notice, the Configuration attributes (the dashboard's Settings page reads them) and the Repairs issue.
"""

from __future__ import annotations

import asyncio
import logging
import re
import types

import pytest
from glucocraft import const as C
from glucocraft import panel_feed as PF
from glucocraft import report_pdf
from glucocraft.i18n import t

from test_entity_ids import _load_init
from test_settings_harness import (
    build,
    entry,
    tz_patch,  # noqa: F401 (the fixture)
)

pytestmark = pytest.mark.usefixtures("tz_patch")


# ------------------------------------------------------------------------------------------------ printer
@pytest.mark.parametrize(
    "stored,target",
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("printer.local", "printer.local"),
        ("  192.168.1.20  ", "192.168.1.20"),
        ("ipp://printer.local:631/ipp/print", "ipp://printer.local:631/ipp/print"),
        ("ipps://printer.local/ipp/print", "ipps://printer.local/ipp/print"),
    ],
)
@pytest.mark.parametrize("color,two_sided", [(True, True), (False, False)])
def test_the_report_printer(tmp_path, monkeypatch, stored, target, color, two_sided):
    data = entry(**({C.CONF_PRINTER: stored} if stored is not None else {}))
    run = build(tmp_path, data)
    assert run.report(14)["printer"] is (target is not None)
    calls = []

    def fake_print(address, pdf, job, color_, two_sided_):
        calls.append((address, job, color_, two_sided_, pdf[:4]))
        return True, "accepted by the printer"

    monkeypatch.setattr(report_pdf, "ipp_print", fake_print)
    monkeypatch.setattr(run.c.reports, "_keep", lambda name, pdf: None)
    pytest.importorskip("reportlab")
    result = asyncio.run(run.c.reports.async_print(14, False, color, two_sided))
    if target is None:
        assert result == {
            "ok": False,
            "message": "No printer address is set in GlucoCraft's settings (Configure > Report and dashboard > "
            "Report printer); the PDF can be downloaded on the CGM Report page of the GlucoCraft dashboard (while "
            "*Dashboard in the sidebar* is on).",
        }
        assert calls == []
    else:
        assert result["ok"] is True and calls == [(target, "GlucoCraft CGM Report 14 Days", color, two_sided, b"%PDF")]
        assert result["message"].startswith("Printed: accepted by the printer.")


# ------------------------------------------------------------------------------------------------ show in sidebar
@pytest.mark.parametrize("stored,shown", [(None, True), (True, True), (False, False)])
def test_show_the_dashboard_in_the_sidebar(tmp_path, monkeypatch, stored, shown):
    init = _load_init()
    calls = []

    async def setup(hass):
        calls.append("setup")

    async def remove(hass):
        calls.append("remove")

    async def frontend(hass):
        return None

    monkeypatch.setattr(init, "async_setup_strategy_dashboard", setup)
    monkeypatch.setattr(init, "async_remove_strategy_dashboard", remove)
    monkeypatch.setattr(init, "async_register_frontend", frontend)
    data = entry(**({C.CONF_SHOW_DASHBOARD: stored} if stored is not None else {}))
    run = build(tmp_path, data)
    run.hass.data[f"{C.DOMAIN}_views_registered"] = True
    ent = types.SimpleNamespace(entry_id="t", data=data, async_on_unload=lambda f: None, runtime_data=run.c)
    asyncio.run(init._async_setup_frontend(run.hass, ent))
    assert calls == (["setup"] if shown else ["remove"])
    assert run.config()["dashboard"] is shown


# ------------------------------------------------------------------------------------------------ panel address
def _register(monkeypatch, run, data):
    """Run the panel webhook registration as async_setup_entry does (after the coordinator): returns (id, entry,
    registered ids, notifications)."""
    registered, notes = [], []
    monkeypatch.setattr(
        PF.webhook, "async_register", lambda hass, dom, name, wid, handler, **kw: registered.append((wid, kw))
    )
    monkeypatch.setattr(PF.persistent_notification, "async_create", lambda hass, msg, **kw: notes.append((msg, kw)))
    ent = types.SimpleNamespace(entry_id="t", data=dict(data), runtime_data=run.c)

    def update(e, data):
        e.data = data

    run.hass.config_entries = types.SimpleNamespace(async_update_entry=update)
    wid = PF.async_register_panel_webhook(run.hass, ent)
    return wid, ent, registered, notes


def test_no_panel_address_without_wall_panels(tmp_path, monkeypatch):
    run = build(tmp_path)
    wid, ent, registered, notes = _register(monkeypatch, run, run.data)
    assert wid is None and registered == [] and notes == [] and C.CONF_PANEL_WEBHOOK_ID not in ent.data
    assert run.config()["panel_feed"] is False


def test_a_panel_address_on_request(tmp_path, monkeypatch):
    """CONF_PANEL_FEED without an address: a first id is made, stored and registered local-only (the handler answers
    only GET, test below); its notice never contains the id and names no earlier address."""
    data = entry(**{C.CONF_PANEL_FEED: True})
    run = build(tmp_path, data)
    wid, ent, registered, notes = _register(monkeypatch, run, data)
    assert re.fullmatch(r"glucocraft_panel_[0-9a-f]{32}", wid)
    assert ent.data[C.CONF_PANEL_WEBHOOK_ID] == wid
    assert registered == [(wid, {"local_only": True, "allowed_methods": ("GET", "POST", "PUT")})]
    assert len(notes) == 1 and wid not in notes[0][0]
    assert notes[0][0] == t("panel_feed.address.first") and "earlier" not in notes[0][0]


def test_an_existing_panel_address_is_kept(tmp_path, monkeypatch):
    data = entry(**{C.CONF_PANEL_WEBHOOK_ID: "glucocraft_panel_" + "a" * 32})
    run = build(tmp_path, data)
    wid, _ent, registered, notes = _register(monkeypatch, run, data)
    assert wid == "glucocraft_panel_" + "a" * 32 and notes == [] and registered[0][0] == wid
    assert run.config()["panel_feed"] is True


def _create_address(monkeypatch, data):
    """config_flow._async_finish with *Create a new wall-panel address* ticked: returns the saved data and the
    notifications."""
    from glucocraft import config_flow as CF

    notes, saved = [], []
    monkeypatch.setattr(PF.persistent_notification, "async_create", lambda hass, msg, **kw: notes.append((msg, kw)))

    async def reload(entry_id):
        return True

    flow = CF.GlucoCraftOptionsFlow(types.SimpleNamespace(data=dict(data), entry_id="t"))
    flow.hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(async_update_entry=lambda e, data: saved.append(data), async_reload=reload)
    )
    flow._updates = {C.CONF_REGENERATE_PANEL_WEBHOOK: True}
    flow.async_create_entry = lambda **kw: ("entry", kw)
    asyncio.run(flow._async_finish())
    return saved[-1], notes


def test_the_first_panel_address_names_no_earlier_one(monkeypatch):
    data, notes = _create_address(monkeypatch, entry())
    wid = data[C.CONF_PANEL_WEBHOOK_ID]
    assert re.fullmatch(r"glucocraft_panel_[0-9a-f]{32}", wid) and data[C.CONF_PANEL_FEED] is True
    assert C.CONF_REGENERATE_PANEL_WEBHOOK not in data
    assert [m for m, _ in notes] == [t("panel_feed.address.first")]  # an operational notice: nothing added
    assert wid not in notes[0][0] and "earlier" not in notes[0][0]


def test_regenerating_makes_a_different_address(monkeypatch):
    """A new address replaces the earlier one at once; its notice says that the earlier one stops working."""
    old = "glucocraft_panel_" + "b" * 32
    data, notes = _create_address(monkeypatch, entry(**{C.CONF_PANEL_WEBHOOK_ID: old, C.CONF_PANEL_FEED: True}))
    wid = data[C.CONF_PANEL_WEBHOOK_ID]
    assert re.fullmatch(r"glucocraft_panel_[0-9a-f]{32}", wid) and wid != old
    assert [m for m, _ in notes] == [t("panel_feed.address.replaced")]
    assert "The earlier wall-panel address stops working" in notes[0][0] and wid not in notes[0][0]
    assert notes[0][1]["notification_id"] == "glucocraft_panel_webhook"


class _Request:
    """A webhook request: its method and the file asked for."""

    def __init__(self, method, file=None):
        self.method = method
        self.query = {} if file is None else {"file": file}


def _handler(monkeypatch, tmp_path):
    """The registered wall-panel handler (aiohttp's Response as a dict) and the webhook id."""
    handlers = []
    monkeypatch.setattr(
        PF.webhook, "async_register", lambda hass, dom, name, wid, handler, **kw: handlers.append((wid, handler))
    )
    monkeypatch.setattr(PF.persistent_notification, "async_create", lambda *a, **kw: None)
    monkeypatch.setattr(PF, "web", types.SimpleNamespace(Response=lambda **kw: kw))
    run = build(tmp_path)
    run.c.locked = lambda fn, *a: fn(*a)
    ent = types.SimpleNamespace(entry_id="t", data=entry(**{C.CONF_PANEL_WEBHOOK_ID: "glucocraft_panel_" + "c" * 32}))
    ent.runtime_data = run.c
    PF.async_register_panel_webhook(run.hass, ent)
    ((wid, handler),) = handlers
    return run, wid, lambda request: asyncio.run(handler(run.hass, wid, request))


@pytest.mark.parametrize("method", ["POST", "PUT"])
def test_the_panel_address_refuses_other_methods_without_logging_the_id(monkeypatch, tmp_path, caplog, method):
    """Home Assistant logs the webhook id for a method the webhook does not accept; GlucoCraft accepts POST and PUT
    itself and refuses them (405), so the address stays read-only and the id stays out of the log."""
    _run, wid, handle = _handler(monkeypatch, tmp_path)
    with caplog.at_level(logging.DEBUG):
        response = handle(_Request(method, "backfill"))
    assert response == {"status": 405, "headers": {"Allow": "GET"}}
    assert wid not in caplog.text


@pytest.mark.parametrize("which", ["backfill", "delta", None])
def test_the_panel_address_serves_its_files_to_get(monkeypatch, tmp_path, which):
    _run, _wid, handle = _handler(monkeypatch, tmp_path)
    response = handle(_Request("GET", which))
    assert response["content_type"] == "application/octet-stream" and response["body"][:4] == b"GLU2"
    assert handle(_Request("GET", "other")) == {"status": 404}


def test_a_failed_panel_file_is_logged_without_the_id(monkeypatch, tmp_path, caplog):
    """An error raised by the handler would be logged by Home Assistant with the webhook id: GlucoCraft logs it
    itself, without the id, and answers 500."""
    _run, wid, handle = _handler(monkeypatch, tmp_path)

    def broken(coordinator, delta):
        raise RuntimeError("broken")

    monkeypatch.setattr(PF, "build_glu2", broken)
    with caplog.at_level(logging.DEBUG):
        response = handle(_Request("GET", "delta"))
    assert response == {"status": 500}
    assert "could not build the wall-panel file delta" in caplog.text and wid not in caplog.text


def test_the_configuration_shows_the_new_panel_address_at_once(tmp_path, monkeypatch):
    """The dashboard's Settings page reads configuration()["panel_feed"]; the address is made after the coordinator
    copied the entry's data, so the attribute should follow the entry."""
    data = entry(**{C.CONF_PANEL_FEED: True})
    run = build(tmp_path, data)
    _register(monkeypatch, run, data)
    assert run.config()["panel_feed"] is True


# ------------------------------------------------------------------------------------------------ texts and behavior
def test_the_settings_texts_state_what_the_settings_do():
    """The settings texts say what the code does: the dashboard switch removes the whole dashboard (so no text promises
    a dashboard page without that condition), the zone word under the value is shown in every setting, so color is never
    the only cue, and the custom ranges reach the binary sensors and the zone attribute as well."""
    import json

    from conftest import REPO

    strings = json.loads((REPO / "custom_components" / "glucocraft" / "strings.json").read_text(encoding="utf-8"))
    options = strings["options"]["step"]
    for where in (strings["config"]["step"]["finish"], options["report"]):
        assert where["data_description"]["show_dashboard"].startswith(
            "Adds the GlucoCraft dashboard and shows it in Home Assistant's sidebar. Off: the dashboard and all its "
            "pages are removed"
        )
    printer = options["report"]["data_description"]["printer_address"]
    assert "always" not in printer and "while *Dashboard in the sidebar* (below) is on" in printer
    assert "while the setting Dashboard in the sidebar is on" in strings["exceptions"]["print_no_printer"]["message"]
    assert (
        "terms_of_use" not in strings["issues"] and strings["config"]["step"]["user"]["description"] == "{disclaimer}"
    )
    assert (
        "Color is never the only cue: the zone word under the glucose value names the zone in every setting."
        in options["displays"]["description"]
    )
    assert "by color only" not in options["displays"]["description"]
    feature = options["features"]["data_description"]["custom_ranges_and_goals"]
    assert "the zone attribute of the Last glucose reading sensor" in feature and "range binary sensors" in feature
    assert "the range bands of their graphs" in feature
    # the ranges screen points to the Documentation, whose Setup names the entities that use the ranges
    assert "Documentation: *Setup*" in options["ranges"]["description"]
    doc_file = REPO / "custom_components" / "glucocraft" / "i18n" / "en" / "documentation.json"
    setup = "".join(json.loads(doc_file.read_text(encoding="utf-8"))["docs.section.setup.body"])
    assert "the zone attribute of the Last glucose reading sensor" in setup and "Glucose in tight range" in setup
    # the very-low limit starts at the sensor's lower limit (40 or 50 mg/dL), never at the box's 30
    from glucocraft import limits as L
    from glucocraft import settings as ST

    assert min(L.LOWER_CHOICES) > ST.RANGE_LIMITS[C.CONF_RANGE_CRITICAL_LOW][0]
    assert (
        "Allowed: from the glucose sensor's lower limit" in options["ranges"]["data_description"]["range_critical_low"]
    )
