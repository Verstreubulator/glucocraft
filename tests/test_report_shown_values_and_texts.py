# License: AGPL-3.0-only (LICENSE).
"""The CGM Report's shown values and texts on the page (www/glucocraft-report.js) and in the PDF (report_pdf.py).

Rules under test:
  * the custom appendix shows each percentage as GlucoCraft rounded it on its side of the goal (69.97% beside "at
    least 70%" and "not met"), on the page and in the PDF;
  * the page shows the whole report when only the last 14 days have no data, with the note above the 14-day page;
  * a time-in-ranges bar without values is its frame only;
  * the heading "Hypoglycemia Events by Period" stays with its table; no y label of a chart overprints an hour label;
  * a warm-up of less than a minute is a sensor change without a warm-up;
  * the page's laboratory entry takes percent or mmol/mol within the action's limits and sends the unit;
  * the page's overview has the consensus targets of the PDF's overview;
  * a laboratory difference is rounded before it is signed; durations use the catalogue's words;
  * the printer's reply is described in words, never by a program error's name; a reason is cut at a word;
  * the report cache of the custom appendix follows the ranges and goals in the settings;
  * a print whose PDF cannot be made is stated in the print notification;
  * the print action's full report names every section the full report adds;
  * the 14-day / 70% condition is cited as the condition of its source and labeled as GlucoCraft's rule.
"""

from __future__ import annotations

import asyncio
import http.client
import io
import json
import re
import types
from pathlib import Path

import pytest
from glucocraft import i18n
from glucocraft import report as R
from glucocraft import report_service as RS
from glucocraft.regional import Regional
from glucocraft.units import shown_against

from conftest import LA
from js_i18n import catalogue
from node_runner import needs_node
from test_report_outputs import NOW, READINGS, _data, _page, _page_html

reportlab = pytest.importorskip("reportlab")
from glucocraft import report_pdf as P

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "custom_components" / "glucocraft"


def _pdf_pages(data: dict, full: bool) -> list[str]:
    """The text of each page of the PDF of `data`, with its whitespace collapsed."""
    pypdf = pytest.importorskip("pypdf")
    pdf, _ = P.render(data, True, full)
    return [
        " ".join((p.extract_text() or "").replace(" ", " ").split()) for p in pypdf.PdfReader(io.BytesIO(pdf)).pages
    ]


def _custom_data() -> dict:
    """A 30-day report with the custom appendix whose time in range is 69.965%: shown 70.0% to one decimal, 69.97%
    beside a goal of at least 70% (not met)."""
    d = _data(30, False)
    d["include_custom_appendix"] = True
    rg = {"very_low": 54, "low": 70, "tight_low": 70, "tight_high": 140, "high": 180, "very_high": 250}
    row = {
        "key": "time_in_range",
        "label": "Time in range",
        "value": 69.97,
        "goal_op": ">=",
        "goal": 70.0,
        "met": False,
    }
    d["custom_appendix"] = {
        "ranges": rg,
        "stats": {"tir": 70.0, "active": 99.0},
        "rows": [row],
        "comparable": True,
        "ranges_differ": False,
    }
    return d


# ------------------------------------------------------------------------------------------------ custom appendix
@pytest.mark.slow
def test_custom_appendix_value_on_the_page_and_in_the_pdf():
    assert shown_against(69.965, 70.0, 1)[0] == 69.97  # the row's value as report.custom_appendix rounds it
    d = _custom_data()
    text = " ".join(_pdf_pages(d, False))
    row = text[text.find("Time in range (70–180)") :][:80]
    assert row.startswith("Time in range (70–180) 69.97% at least 70% not met"), row
    html = re.sub(r'<span class="sr">.*?</span>', "", _page_html(d))
    shown = " ".join(re.sub(r"<[^>]+>", " ", html[html.find('id="r-custom"') :]).split())
    assert "Time in range (70–180) 69.97% at least 70% not met" in shown, shown[:400]


# ------------------------------------------------------------------------------------------------ page with no 14 days
@needs_node
def test_page_shows_the_report_when_only_the_last_14_days_are_empty():
    d = _data(30, False)
    d["standard"].update({k: None for k in ("tir", "titr", "low", "very_low", "high", "very_high", "active")})
    d["standard"].update({"days_with_data": 0, "below_70": None, "above_180": None})
    html = _page_html(d)
    note, whole = catalogue("frontend")["report_page.no_data_14_days"], catalogue("frontend")["report_page.no_data"]
    assert note in html and whole not in html  # the note above the 14-day page
    assert i18n.t("report.overview.title", period="30 Days") in html  # and the rest of the report
    assert i18n.t("report.change.title") in html and i18n.t("report.about.title") in html
    # no data in the whole period either: the note of the whole report alone, which does not name the last 14 days
    d["period"].update({"tir": None, "days_with_data": 0})
    html = _page_html(d)
    assert whole in html and note not in html and i18n.t("report.about.title") not in html
    assert "14 days;" not in whole and "report period" in whole


# ------------------------------------------------------------------------------------------------ the empty bar
@needs_node
def test_time_in_ranges_bar_without_values_is_its_frame_only():
    keys = ("very_low", "low", "titr", "tir", "high", "very_high")
    empty = _page("return card.tirBar(data);", {k: None for k in keys})
    assert 'fill="none"' in empty and "#9B1C1F" not in empty and "#52B964" not in empty
    full = _page("return card.tirBar(data);", dict(zip(keys, (1.0, 3.0, 40.0, 80.0, 12.0, 4.0), strict=True)))
    assert "#9B1C1F" in full and full.count("<rect") == 6


@pytest.mark.slow
def test_pdf_bar_without_values_draws_no_band():
    fitz = pytest.importorskip("pymupdf")
    d = _data(14, False)
    d["standard"].update({k: None for k in ("tir", "titr", "low", "very_low", "high", "very_high")})
    pdf, _ = P.render(d, True, False)
    page = fitz.open(stream=pdf, filetype="pdf")[0]
    # the bar's bands are 38 points wide; the range key's swatches are 9 points wide
    bands = [p for p in page.get_drawings() if p.get("fill") and abs(p["rect"].width - 38) < 0.5]
    assert not bands


# ------------------------------------------------------------------------------------------------ layout of the PDF
@pytest.mark.slow
@pytest.mark.parametrize(
    "days, unit, rgl",
    [
        # cases whose heading fell at a page foot, without its table, while it kept only 40 points of the section
        (14, "mg/dL", Regional("12", "MDY", 0, ".", "both", "a4")),
        (14, "mmol/L", Regional("24", "DMY", 0, ",", "both", "a4")),
        (30, "mmol/L", Regional("24", "DMY", 0, ",", "percent", "letter")),
    ],
)
def test_events_by_period_heading_stays_with_its_table(days, unit, rgl):
    d = _data(days, True, unit, rgl)
    # the heading with the start of its caption (the section's own heading, not a mention of its name)
    head = f"{i18n.t('report.events.title')} {i18n.t('report.events.caption')[:30]}"
    page = next(p for p in _pdf_pages(d, True) if head in p)
    assert P._L(i18n.t("report.events.row.events")).replace(" ", " ") in page  # its first row, on the same page
    assert "Starts at night" in page  # and its last row


@pytest.mark.slow
@pytest.mark.parametrize(
    "days, full, unit, rgl",
    [
        # cases whose Longer-Term Overview heading fell at a page foot, without its summary table (A4)
        (30, False, "mg/dL", Regional("12", "MDY", 6, ".", "percent", "a4")),
        (90, True, "mmol/L", Regional("24", "DMY", 0, ",", "both", "a4")),
        (180, False, "mg/dL", Regional("12", "MDY", 6, ".", "percent", "a4")),
    ],
)
def test_longer_term_overview_heading_stays_with_its_table(days, full, unit, rgl):
    d = _data(days, full, unit, rgl)
    head = i18n.t("report.overview.title", period=d["label"])
    page = next(p for p in _pdf_pages(d, full) if head in p)
    assert P._L(i18n.t("report.overview.row.sd")).replace(" ", " ") in page  # a row of its table, on the same page


@pytest.mark.slow
@pytest.mark.parametrize("paper", ["letter", "a4"])
def test_page_one_insufficient_profile_line_clears_the_caption(paper):
    """Without a profile, page 1's "insufficient data" line stands below the profile's caption, whatever its length."""
    fitz = pytest.importorskip("pymupdf")
    d = _data(14, False, "mg/dL", Regional("12", "MDY", 0, ".", "percent", paper))
    d["standard"]["profile"] = {}
    pdf, _ = P.render(d, True, False)
    page = fitz.open(stream=pdf, filetype="pdf")[0]
    lines = [
        (b, " ".join(s["text"] for s in ln["spans"]))
        for blk in page.get_text("dict")["blocks"]
        for ln in blk.get("lines", [])
        for b in [fitz.Rect(ln["bbox"])]
    ]
    insufficient = next(b for b, text in lines if text.startswith("Insufficient data"))
    others = [b for b, text in lines if not text.startswith("Insufficient data")]
    assert not any(insufficient.intersects(b) and (insufficient & b).height > 0.5 for b in others)


@pytest.mark.slow
@pytest.mark.parametrize("paper", ["letter", "a4"])
def test_no_y_label_overprints_an_hour_label(paper):
    fitz = pytest.importorskip("pymupdf")
    for days, full in ((30, False), (30, True)):
        d = _data(days, full, "mmol/L", Regional("12", "MDY", 0, ".", "percent", paper))
        # one event with the lowest value 42 mg/dL: the events chart's scale then starts at 36.0 mg/dL, just below the
        # grid line of 2.0 mmol/L, whose label would overprint the first hour label
        d["lows"]["list"] = [["Wed 10/14 4:00 AM", 30, 42.0, 4.0, 1, False]]
        pdf, _ = P.render(d, True, full)
        for page in fitz.open(stream=pdf, filetype="pdf"):
            words = page.get_text("words")
            hours = [fitz.Rect(w[:4]) for w in words if w[4] in ("12am", "12AM")]
            for h in hours:
                others = [w for w in words if w[4] not in ("12am", "12AM") and (fitz.Rect(w[:4]) & h).get_area() > 0.5]
                assert not others, (page.number, others)


# ------------------------------------------------------------------------------------------------ warm-ups
def test_a_warm_up_under_a_minute_is_a_sensor_change():
    start = int(READINGS[-1000].timestamp)  # inside the 14 days
    events = [{"start": start, "end": start + 30}, {"start": start + 600, "end": start + 690}]
    out = R.build_report(READINGS, 300, LA, 14, False, [], now=NOW, sensor_events=events)
    ev = out["quality"]["sensor_events"]
    assert [e["warmup"] for e in ev] == [False, True]
    assert ev[0]["minutes"] is None and ev[1]["minutes"] == 2  # 1.5 minutes, rounded once


# ------------------------------------------------------------------------------------------------ laboratory entry
@needs_node
def test_page_lab_entry_takes_percent_or_mmol_mol():
    d = _data(30, False)
    d["lab_limits"] = {"percent": [3.0, 20.0], "mmol_mol": [9.0, 195.0]}
    out = _page(
        "card._data = data; card._lab = { date: '', a1c: '', unit: '', invalid: [] };"
        "GC.a1c = 'mmol_mol'; const mm = card.labSection(data);"
        "card._lab = { date: '', a1c: '40', unit: 'mmol_mol', invalid: ['date', 'a1c'] }; const bad = card.labSection(data);"
        "GC.a1c = 'both'; card._lab = { date: '', a1c: '', unit: '', invalid: [] }; const pct = card.labSection(data);"
        "return [mm, bad, pct];",
        d,
    )
    mm, bad, pct = out
    assert '<select id="labunit">' in mm and '<option value="mmol_mol" selected>mmol/mol</option>' in mm
    assert 'min="9" max="195" step="1"' in mm
    assert '<option value="percent" selected>percent</option>' in pct and 'min="3" max="20" step="0.1"' in pct
    assert i18n.t("report.lab.error.no_date") in bad
    assert i18n.t("report.lab.error.range", low="9", high="195", unit="mmol/mol") in bad
    assert 'id="laba1c" min="9"' in bad and 'aria-invalid="true"' in bad
    # the limits are those of the action add_lab_a1c
    assert {k: list(v) for k, v in RS.LAB_LIMITS.items()} == d["lab_limits"]
    js = (PKG / "www" / "glucocraft-report.js").read_text(encoding="utf-8")
    assert "{ date, a1c, unit }" in js  # the unit is sent with the result


# ------------------------------------------------------------------------------------------------ overview targets
@needs_node
def test_page_overview_has_the_consensus_targets_of_the_pdf():
    html = re.sub(r'<span class="sr">.*?</span>', "", _page_html(_data(30, False)))  # as shown
    part = html[html.find('id="r-overview"') :]
    part = part[: part.find("</table>")]
    target = i18n.t("report.overview.consensus_target")
    row = re.sub(r"<[^>]+>", "|", part[part.find(target) :])
    cells = [c for c in row.split("|") if c.strip()]
    none = i18n.t("report.overview.target.none")
    cv = i18n.t("report.overview.target.cv")
    want = [target, "&gt;70%", none, "&lt;4%", "&lt;1%", "&lt;25%", "&lt;5%", none, none, cv, none, none, none, none]
    assert cells == want, cells


# ------------------------------------------------------------------------------------------------ links, redraws
@needs_node
def test_documentation_links_use_the_dashboard_path_and_the_body_follows_the_data():
    out = _page(
        "globalThis.location = { pathname: '/my-own-dashboard/report' }; card.attachShadow = () => {};"
        "card.setConfig({}); const fixed = rDocUrl('colors');"
        "card.setConfig({ base: '/elsewhere/' }); const own = rDocUrl('references');"
        # the settings come from the report data: the Configuration sensor's attributes, absent while GlucoCraft
        # reloads, change nothing the page shows
        "globalThis.glucocraftI18n.language = 'en'; card._data = data;"
        "card._hass = { states: { 'sensor.glucocraft_configuration': { attributes: { decimal_separator: '.' } } } };"
        "const before = card.intlSig(); card._hass = { states: {} }; const during = card.intlSig();"
        "return [fixed, own, before === during, card.retention(data), RC.target, RC.vlow];",
        {"decimal_separator": ",", "a1c_unit": "percent", "first_weekday": 0, "retention_days": 200},
    )
    assert out == [
        "/glucocraft-home/documentation#colors",
        "/elsewhere/documentation#references",
        True,
        200,
        "#1F8F45",
        "#9B1C1F",
    ]


# ------------------------------------------------------------------------------------------------ differences, durations
@needs_node
def test_lab_difference_is_rounded_before_it_is_signed():
    out = _page("GC.a1c = 'percent'; return [rA1cDiff(0.03), rA1cDiff(-0.03), rA1cDiff(0.06), rA1cDiff(-0.06)];")
    signed = ["0.0", "0.0", "+0.1", "-0.1"]
    assert out == [f"{v} percentage points" for v in signed]
    assert [P._signed(v, 1) for v in (0.03, -0.03, 0.06, -0.06)] == signed


@needs_node
def test_page_hypoglycemia_durations_use_the_catalogue():
    """The mean and the longest duration of the Hypoglycemia section in words, the same on the page and in the PDF
    (running text: no unit symbols)."""
    d = _data(30, False)
    d["lows"].update({"mean_duration_min": 42, "longest_min": 95, "longest_when": "Wed 10/14 4:00 AM"})
    html = _page("return card.lowsSection(data);", d)
    assert P._dur_words(42) == "42 minutes" and P._dur_words(95) == "1 hour 35 minutes"
    longest = i18n.t("report.lows.longest", duration=P._dur_words(95), when="Wed 10/14 4:00 AM")
    assert i18n.t("report.lows.duration_value", mean=P._dur_words(42), longest=longest) in html


# ------------------------------------------------------------------------------------------------ printing
def test_printer_replies_are_described_in_words():
    no_reply = i18n.t("report.print.reason.no_valid_reply")
    assert P._network_reason(http.client.RemoteDisconnected("closed"), 90) == no_reply
    assert P._network_reason(http.client.BadStatusLine("x"), 90) == no_reply
    assert P._network_reason(http.client.IncompleteRead(b""), 90) == no_reply
    assert P._network_reason(OSError(), 90) == i18n.t("report.print.reason.unreachable_no_detail")
    refused = ConnectionRefusedError(111, "Connection refused")
    assert P._network_reason(refused, 90) == i18n.t("report.print.reason.unreachable", reason="Connection refused")
    # a long reason is cut at a word, at most 60 characters
    long = "No connection could be made because the target machine actively refused it"
    cut = P._plain(long)
    assert len(cut) <= 60 and long.startswith(cut) and long[len(cut)] == " "
    for text in (no_reply, P._network_reason(OSError(), 90)):
        assert not re.search(r"[A-Z][a-z]+[A-Z][a-z]+", text)  # no program error's class name
    # a printer's reason phrase is free text: nothing in it can become a link, an image or emphasis in the notification
    # (Home Assistant's Markdown turns an address that starts with "www." into a link)
    for reply in ("Jam see www.evil.example now", "[x](http://evil.example)", "*Jam* `x` ![i](y) mail@host"):
        plain = P._plain(reply)
        assert "www." not in plain and not re.search(r"[\[\]()*`!:/@<>_.]", plain), plain


def test_a_print_whose_pdf_cannot_be_made_is_notified(monkeypatch):
    from test_entity_ids import _load_init

    init = _load_init()

    class Failed(Exception):
        def __init__(self, **kw):
            super().__init__()
            self.kw = kw

    monkeypatch.setattr(init, "HomeAssistantError", Failed)

    async def render_error(*args):
        raise ValueError("render error")

    coordinator = types.SimpleNamespace(reports=types.SimpleNamespace(async_print=render_error))
    monkeypatch.setattr(init, "_coordinator_for", lambda hass: coordinator)
    calls = []

    async def call(*a, **kw):
        calls.append(a)

    hass = types.SimpleNamespace(services=types.SimpleNamespace(async_call=call))
    with pytest.raises(Failed) as err:
        asyncio.run(init._handle_print(hass, types.SimpleNamespace(data={})))
    assert err.value.kw["translation_key"] == "print_pdf_failed"
    assert calls and calls[0][:2] == ("persistent_notification", "create")
    assert calls[0][2]["message"] == i18n.t("report.print.pdf_failed", reason="render error")


def test_print_action_names_every_section_of_the_full_report():
    services = (PKG / "services.yaml").read_text(encoding="utf-8")
    strings = (PKG / "strings.json").read_text(encoding="utf-8")
    full_only = (
        i18n.t("report.events.title"),
        i18n.t("report.patterns.title", period="").split(" — ")[0],
        i18n.t("report.days.title", period="").split(" — ")[0],
        i18n.t("report.log.title", period="").split(" — ")[0],
        i18n.t("report.monthly.title").split(" — ")[0],
    )
    for text in (services, strings, i18n.t("report.about.forms.body")):
        full = text[text.find("Full report") :] if "Full report" in text else text[text.find("The full report") :]
        for name in full_only:
            assert name in full, name
    # render() adds these sections to the full report, in this order
    src = (PKG / "report_pdf.py").read_text(encoding="utf-8")
    body = src[src.find("    if full:\n        rp.event_summary()") :][:400]
    assert [m for m in ("event_summary", "patterns", "day_table", "daily_log", "monthly") if f"rp.{m}()" in body] == [
        "event_summary",
        "patterns",
        "day_table",
        "daily_log",
        "monthly",
    ]


# ------------------------------------------------------------------------------------------------ the report cache
def test_custom_appendix_cache_follows_the_settings():
    reports = RS.GlucoCraftReports.__new__(RS.GlucoCraftReports)
    reports._lab = []
    reports.coordinator = types.SimpleNamespace(display_unit="mg/dL", regional=Regional())
    a = {"ranges": {"low": 70, "high": 180}, "goals": {"time_in_range": {"op": ">", "value": 70}}}
    b = {"ranges": {"low": 72, "high": 180}, "goals": {"time_in_range": {"op": ">", "value": 70}}}
    custom, plain = (14, False, True, False), (14, False, False, False)
    assert reports._key(custom, a) != reports._key(custom, b)  # a learned limit changes the ranges in use
    assert reports._key(plain, a) == reports._key(plain, b)  # the main report does not use them
    assert reports._key(custom, a)[0] == reports._key(plain, b)[0]  # the same base: the slots of one day stay


# ------------------------------------------------------------------------------------------------ texts
def test_the_14_day_70_percent_condition_is_cited_as_a_condition():
    texts = json.loads((PKG / "i18n" / "en" / "report.json").read_text(encoding="utf-8"))
    for key in ("report.custom.notes", "report.lab.method", "report.metrics.sources"):
        assert "70% sensor active (the condition of Battelino et al., 2019)" in texts[key], key
        assert "GlucoCraft rule" in texts[key], key
    # About This Report holds the general method only: the GMI condition stands with the metrics, not again there
    assert "GMI is shown only" not in texts["report.about.data.body"]
    assert "a GlucoCraft method" not in json.dumps(texts)
    assert "within the chart's scale" in texts["report.profile.lines"]  # a line outside the scale is not drawn
    for key in ("report.events.row.events", "report.events.row.level2", "report.lows.level1", "report.lows.level2"):
        assert texts[key].endswith(" {unit})"), key  # the threshold with its unit
    # the Data Quality Notes point to About This Report for the gap rule and sensor active
    assert "About This Report" in texts["report.quality.gap_rule"]
    assert "15 minutes or three reading intervals" not in texts["report.quality.gap_rule"]
