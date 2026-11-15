# License: AGPL-3.0-only (LICENSE).
"""The CGM Report, its HTTP API and the registration of GlucoCraft's cards and dashboard: each value shown on its side
of the rule printed beside it, and every failure stated (a warning in the log, and a Repairs notice where the user
must act), never silent.

Rules under test:
- A day's sensor active in the Daily Summary and in the Data Quality strip is shown as every sensor active beside the
  70% rule (report._active_shown): one decimal, two where one decimal would show 70% for a day below it (69.97%); the
  PDF writes it with the decimal separator in use.
- A warm-up that the coordinator ended by its 24-hour rule (no reading measured after its start) is recorded with that
  end and named as such on the Data Quality page, never as a sensor change without a warm-up.
- The report endpoints read full and color as 0 / 1 (also true / false, on / off, yes / no) and refuse any other value
  with 400, as a period that does not exist.
- A copy of a report PDF that cannot be kept is logged as a warning; the PDF is still returned, and the print notice
  says why the copy is missing.
- Card resources: a resource whose update fails is a failed registration (a warning and the Repairs notice
  lovelace_resources_failed); only a resource whose path begins with /glucocraft/ or /glucocraft_v/ is GlucoCraft's;
  a card file that cannot be read is logged.
- GlucoCraft's dashboard: a registration that fails opens the Repairs notice dashboard_failed with the reason; one
  that succeeds, and the dashboard's removal, close it.
"""

from __future__ import annotations

import asyncio
import logging
import types
from datetime import datetime, timedelta

import pytest
from glucocraft import const as C
from glucocraft import frontend, http_api, strategy_dashboard
from glucocraft import report as R
from glucocraft import report_service as RS
from glucocraft.regional import Regional

import ha_stub
from conftest import LA, Rd

NOW_LOCAL = datetime(2026, 10, 22, 9, 0, tzinfo=LA)
NOW = NOW_LOCAL.timestamp()
MIDNIGHT = NOW_LOCAL.replace(hour=0)


def _issue(issue_id):
    return ha_stub.ISSUE_REGISTRY.issues.get((C.DOMAIN, issue_id))


# ------------------------------------------------------------------------------------------------ the 70% rule
def _short_day_readings():
    """16 days of 30-second readings at 120 mg/dL; the day 3 days before the last midnight has its first 2,015
    readings only (2,015 x 30 s = 69.965% of the day), then a gap to the next midnight."""
    short_from = int((MIDNIGHT - timedelta(days=3)).timestamp())
    short_to = short_from + 2015 * 30
    next_day = int((MIDNIGHT - timedelta(days=2)).timestamp())
    start = int((MIDNIGHT - timedelta(days=16)).timestamp())
    return [Rd(t, 120.0) for t in range(start, int(NOW), 30) if not short_to <= t < next_day], short_from


@pytest.mark.slow
def test_a_day_below_the_70_percent_rule_never_shows_70_percent():
    readings, short_from = _short_day_readings()
    data = R.build_report(readings, 30, LA, 14, True, [], now=NOW)
    week = data["days"]["week"]
    assert [w[3] for w in week].count(100.0) == 13
    short = next(w for w in week if w[3] != 100.0)
    # below the rule: no mean, no events, and its sensor active with the two decimals that keep it below 70%
    assert short[1] is None and short[2] is None and short[3] == 69.97
    date = datetime.fromtimestamp(short_from, LA).strftime("%Y-%m-%d")
    strip = {d["date"]: d["active"] for d in data["quality"]["per_day"]}
    assert strip[date] == 69.97 and list(strip.values()).count(100.0) == 13


@pytest.mark.slow
def test_the_daily_summary_writes_sensor_active_with_the_decimal_separator_in_use(monkeypatch):
    pytest.importorskip("reportlab")
    from glucocraft import report_pdf as P

    from test_pdf_layout_and_regional import Recorder

    readings, _ = _short_day_readings()
    rg = Regional("24", "DMY", 0, ",", "both", "a4")
    data = R.build_report(readings, 30, LA, 14, True, [], now=NOW, regional=rg)
    data.update({"label": "14 Days", "sources": ["Test source"], "created": "x", "unit": "mg/dL", **rg.as_dict()})
    rec = Recorder(monkeypatch)
    P.render(data, True, True)
    drawn = [s for *_, s in rec.items]
    assert "69,97%" in drawn and "69.97%" not in drawn


# ------------------------------------------------------------------------------------------------ warm-ups
def _reports(coord):
    saved = []
    hass = types.SimpleNamespace(async_create_task=lambda coro: saved.append(asyncio.run(coro)))
    return RS.GlucoCraftReports(hass, coord), saved


def test_a_warm_up_ended_by_the_24_hour_rule_is_recorded_with_that_end():
    start = int(NOW) - 30 * 3600
    coord = types.SimpleNamespace(
        warmup_started_at=start,
        last_reading_ts=start - 600,  # the newest reading was measured before the warm-up began
        async_add_listener=lambda f: None,
        WARMUP_MAX_SECONDS=24 * 3600,
    )
    reports, saved = _reports(coord)
    reports.track_warmup()
    coord.warmup_started_at = 0  # coordinator._end_overlong_warmup
    reports.track_warmup()
    assert reports._events == [{"start": start, "end": start + 24 * 3600, "limit": True}] and len(saved) == 2
    # a warm-up ended by a reading measured after it began keeps that reading's time, without the mark
    coord.warmup_started_at = start + 86400
    reports.track_warmup()
    coord.warmup_started_at, coord.last_reading_ts = 0, start + 86400 + 900
    reports.track_warmup()
    assert reports._events[-1] == {"start": start + 86400, "end": start + 86400 + 900}


@pytest.mark.slow
def test_the_data_quality_page_names_a_warm_up_ended_by_the_24_hour_rule():
    readings = [Rd(t, 120.0) for t in range(int(NOW) - 16 * 86400, int(NOW), 300)]
    start = int((MIDNIGHT - timedelta(days=4)).timestamp()) + 8 * 3600
    events = [
        {"start": start, "end": start + 24 * 3600, "limit": True},
        {"start": start - 86400, "end": start - 86400 + 30},  # less than a minute: a sensor change
    ]
    rg = Regional()
    data = R.build_report(readings, 300, LA, 14, False, [], now=NOW, sensor_events=events, regional=rg)
    shown = sorted(data["quality"]["sensor_events"], key=lambda e: bool(e.get("limit")))
    assert shown[0]["warmup"] is False and "limit" not in shown[0]
    assert shown[1]["warmup"] is True and shown[1]["limit"] is True and shown[1]["minutes"] == 1440
    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")
    import io

    from glucocraft import report_pdf as P

    data.update({"label": "14 Days", "sources": ["Test source"], "created": "x", "unit": "mg/dL", **rg.as_dict()})
    pdf, _pages = P.render(data, True, False)
    text = " ".join(" ".join((p.extract_text() or "").split()) for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    text = text.replace(P.NB, " ")
    assert f"warm-up from {shown[1]['start']}, ended after 24 hours without a reading" in text
    assert f"sensor change at {shown[0]['start']}, without a warm-up time" in text


# ------------------------------------------------------------------------------------------------ the API flags
@pytest.mark.parametrize(
    "query,want",
    [
        ({}, {"full": False, "color": True}),
        ({"full": "", "color": ""}, {"full": False, "color": True}),
        ({"full": "1", "color": "0"}, {"full": True, "color": False}),
        ({"full": "true", "color": "false"}, {"full": True, "color": False}),
        ({"full": " Yes ", "color": "OFF"}, {"full": True, "color": False}),
        ({"full": "on", "color": "no"}, {"full": True, "color": False}),
    ],
)
def test_full_and_color_are_read_by_one_rule(query, want):
    flags, refused = http_api._flags(types.SimpleNamespace(query=query), {"full": False, "color": True})
    assert refused is None and flags == want


@pytest.mark.parametrize("query", [{"full": "2"}, {"color": "grey"}, {"full": "1", "color": "maybe"}])
def test_any_other_value_of_full_or_color_is_refused(monkeypatch, query):
    monkeypatch.setattr(http_api, "web", types.SimpleNamespace(Response=lambda **kw: kw))
    _flags, refused = http_api._flags(types.SimpleNamespace(query=query), {"full": False, "color": True})
    name = next(k for k, v in query.items() if v not in ("1",))
    assert refused == {
        "status": 400,
        "text": f"{name} must be 0 or 1 (also accepted: true or false, on or off, yes or no).",
    }


def test_the_flag_words_are_the_appendix_words():
    assert RS.report_options({"include_custom_appendix": "Yes"}) == (True, False)
    assert set(RS.REPORT_FLAG_ON).isdisjoint(RS.REPORT_FLAG_OFF) and "1" in RS.REPORT_FLAG_ON


# ------------------------------------------------------------------------------------------------ the kept copy
def _kept_reports(tmp_path, folder_is_a_file=False):
    hass = types.SimpleNamespace(config=types.SimpleNamespace(path=lambda *p: str(tmp_path.joinpath(*p))))
    reports = RS.GlucoCraftReports.__new__(RS.GlucoCraftReports)
    reports.hass = hass
    if folder_is_a_file:  # the folder cannot be made: a file of its name is in the way
        (tmp_path / RS.REPORT_DIR).write_text("x")
    return reports


def test_a_copy_that_cannot_be_kept_is_logged_and_its_reason_returned(tmp_path, caplog):
    reports = _kept_reports(tmp_path, folder_is_a_file=True)
    with caplog.at_level(logging.WARNING):
        reason = reports._keep(f"{RS.REPORT_PREFIX}2026-10-22_0900_14d_summary_color.pdf", b"%PDF")
    assert reason
    assert any(
        "could not keep a copy of the report" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records
    )
    assert _kept_reports(tmp_path / "ok")._keep(f"{RS.REPORT_PREFIX}x.pdf", b"%PDF") is None  # a kept copy


def test_the_print_notice_says_why_the_copy_is_missing():
    printed = RS.print_message(True, "sent to the printer", 2, "x.pdf", "No space left on device")
    assert printed == (
        "Printed: sent to the printer. 2 pages; the PDF could not be kept in the folder glucocraft_reports "
        "(No space left on device)."
    )
    assert RS.print_message(False, "printer not reachable", 1, "y.pdf", "Permission denied").startswith(
        "Not printed: printer not reachable. 1 page; the PDF could not be kept"
    )
    assert RS.print_message(True, "sent to the printer", 1, "z.pdf").endswith("1 page; PDF kept as z.pdf.")


def test_the_pdf_is_returned_and_printed_without_its_kept_copy(tmp_path, monkeypatch):
    reports = _kept_reports(tmp_path, folder_is_a_file=True)

    async def executor(fn, *args):
        return fn(*args)

    async def data(*args):
        return {"ndays": 14}

    reports.hass.async_add_executor_job = executor
    reports.async_data = data
    reports.coordinator = types.SimpleNamespace(settings={C.CONF_PRINTER: "printer.local"})
    from glucocraft import report_pdf as P

    monkeypatch.setattr(P, "render", lambda d, color, full: (b"%PDF-1.4", 3))
    monkeypatch.setattr(P, "ipp_print", lambda *a: (True, "sent to the printer"))
    pdf, pages, name, not_kept = asyncio.run(reports.async_pdf(14, False, True))
    assert pdf == b"%PDF-1.4" and pages == 3 and name.endswith("_14d_summary_color.pdf") and not_kept
    result = asyncio.run(reports.async_print(14, False, True, True))
    assert result["ok"] is True and result["kept"] is False and result["file"].endswith(".pdf")
    assert "the PDF could not be kept in the folder glucocraft_reports" in result["message"]


# ------------------------------------------------------------------------------------------------ card resources
class _Resources:
    def __init__(self, items, fail_update=False):
        self.items, self.fail_update, self.loaded, self.deleted = list(items), fail_update, True, []

    def async_items(self):
        return list(self.items)

    async def async_create_item(self, data):
        self.items.append({"id": str(len(self.items) + 100), **data})

    async def async_update_item(self, res_id, data):
        if self.fail_update:
            raise RuntimeError("storage is read-only")
        next(i for i in self.items if i["id"] == res_id).update(data)

    async def async_delete_item(self, res_id):
        self.deleted.append(res_id)
        self.items = [i for i in self.items if i["id"] != res_id]


def _old_resources():
    return [{"id": str(k), "url": f"/glucocraft_v/old/{card}"} for k, card in enumerate(frontend.CARD_FILES)]


def test_a_resource_update_that_fails_opens_the_notice(caplog):
    resources = _Resources(_old_resources(), fail_update=True)
    hass = types.SimpleNamespace(data={"lovelace": types.SimpleNamespace(resources=resources)})
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(frontend._async_register_lovelace_resources(hass, "/glucocraft_v/new")) is False
    issue = _issue(C.RESOURCES_ISSUE_ID)
    assert issue is not None and issue.is_persistent is False
    assert issue.translation_placeholders["error"].startswith(
        "these resources could not be updated: glucocraft-display-card.js (storage is read-only); "
    )
    warning = next(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert "/glucocraft/glucocraft-ui.js" in warning and "JavaScript module" in warning
    resources.fail_update = False  # the next start that updates them closes the notice
    assert asyncio.run(frontend._async_register_lovelace_resources(hass, "/glucocraft_v/new")) is True
    assert _issue(C.RESOURCES_ISSUE_ID) is None
    assert {i["url"] for i in resources.items} == {f"/glucocraft_v/new/{card}" for card in frontend.CARD_FILES}


def test_a_collection_that_cannot_update_a_stale_resource_opens_the_notice():
    full = _Resources(_old_resources())
    # a collection without updates (a changed internal)
    resources = types.SimpleNamespace(
        loaded=True, async_items=full.async_items, async_create_item=full.async_create_item
    )
    hass = types.SimpleNamespace(data={"lovelace": types.SimpleNamespace(resources=resources)})
    assert asyncio.run(frontend._async_register_lovelace_resources(hass, "/glucocraft_v/new")) is False
    error = _issue(C.RESOURCES_ISSUE_ID).translation_placeholders["error"]
    assert "glucocraft-ui.js (Home Assistant does not allow it to be updated)" in error


def test_only_resources_below_glucocraft_s_paths_are_glucocraft_s():
    items = [
        {"id": "1", "url": "/glucocraft/glucocraft-ui.js"},
        {"id": "2", "url": "/glucocraft_v/abc/glucocraft-report.js?v=1"},
        {"id": "3", "url": "http://homeassistant.local/glucocraft/glucocraft-docs.js"},
        {"id": "4", "url": "/local/glucocraft/glucocraft-ui.js"},  # the user's own copy
        {"id": "5", "url": "/hacsfiles/glucocraft/glucocraft-strategy.js"},
        {"id": "6", "url": "/glucocraft-extra/glucocraft-ui.js"},
    ]
    assert [r[2] for r in frontend._glucocraft_resources(items)] == ["1", "2", "3"]
    resources = _Resources(items)
    hass = types.SimpleNamespace(data={"lovelace": types.SimpleNamespace(resources=resources)})
    asyncio.run(frontend.async_unregister_lovelace_resources(hass))
    assert resources.deleted == ["1", "2", "3"]


def test_a_card_file_that_cannot_be_read_is_logged(tmp_path, monkeypatch, caplog):
    unreadable = tmp_path / "glucocraft-ui.js"
    unreadable.write_text("x")
    monkeypatch.setattr(frontend, "PACKAGE_DIR", tmp_path)
    monkeypatch.setattr(frontend, "_hashed_files", lambda: [unreadable])
    original = type(unreadable).read_bytes

    def read_bytes(self):
        if self == unreadable:
            raise PermissionError("Permission denied")
        return original(self)

    monkeypatch.setattr(type(unreadable), "read_bytes", read_bytes)
    with caplog.at_level(logging.WARNING):
        assert len(frontend._compute_content_hash()) == 10
    assert any("glucocraft-ui.js cannot be read" in r.getMessage() for r in caplog.records)


# ------------------------------------------------------------------------------------------------ the dashboard
def test_a_dashboard_that_cannot_be_registered_opens_a_notice_and_a_good_one_closes_it(monkeypatch, caplog):
    hass = types.SimpleNamespace(data={})
    monkeypatch.setattr(strategy_dashboard, "_lovelace_bits", lambda: ("lovelace_data", object))
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(strategy_dashboard.async_setup_strategy_dashboard(hass)) is False
    issue = _issue(strategy_dashboard.DASHBOARD_ISSUE_ID)
    assert issue.translation_key == "dashboard_failed" and issue.is_persistent is False
    not_ready = "Home Assistant's dashboards were not loaded yet"
    assert issue.translation_placeholders == {"error": not_ready}
    assert any(f"not registered: {not_ready}" in r.getMessage() for r in caplog.records)

    def broken():
        raise ImportError("no LovelaceConfig")

    monkeypatch.setattr(strategy_dashboard, "_lovelace_bits", broken)
    assert asyncio.run(strategy_dashboard.async_setup_strategy_dashboard(hass)) is False
    assert _issue(strategy_dashboard.DASHBOARD_ISSUE_ID).translation_placeholders == {"error": "no LovelaceConfig"}

    class Config:
        def __init__(self, hass, url_path, config):
            self.config = config

    panels = []
    hass.data["lovelace_data"] = types.SimpleNamespace(dashboards={})
    monkeypatch.setattr(strategy_dashboard, "_lovelace_bits", lambda: ("lovelace_data", Config))
    monkeypatch.setattr(
        strategy_dashboard,
        "frontend",
        types.SimpleNamespace(
            async_register_built_in_panel=lambda hass, kind, **kw: panels.append(kw["frontend_url_path"]),
            async_remove_panel=lambda hass, path, warn_if_unknown=False: panels.remove(path),
        ),
    )
    assert asyncio.run(strategy_dashboard.async_setup_strategy_dashboard(hass)) is True
    assert _issue(strategy_dashboard.DASHBOARD_ISSUE_ID) is None and panels == [strategy_dashboard.URL_PATH]
    # the setting turned off (or the entry unloaded): the dashboard and any notice of it are removed
    ha_stub.ISSUE_REGISTRY.create(C.DOMAIN, strategy_dashboard.DASHBOARD_ISSUE_ID)
    asyncio.run(strategy_dashboard.async_remove_strategy_dashboard(hass))
    assert _issue(strategy_dashboard.DASHBOARD_ISSUE_ID) is None and panels == []
