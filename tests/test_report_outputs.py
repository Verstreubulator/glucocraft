# License: AGPL-3.0-only (LICENSE).
"""The CGM Report's outputs: the PDF (report_pdf.py), the report page (www/glucocraft-report.js), printing and the
date-format setting.

Rules under test:
  * no PDF text below 8 pt;
  * no text below 14 px on the report page; opaque hourly-log cells with fixed text colors;
  * LO / HI shown as LO / HI in the hourly log (page and PDF) and as an event's lowest value;
  * the events chart colors a marker by the event's level; the page and the PDF share one caption;
  * the page draws the daily profiles' line breaks from the report data (daily_breaks);
  * the page shows a GMI in mmol/mol from the report data (Bergenstal et al., 2018), not converted again;
  * the color key without an instruction, "extended hyperglycemia", "sensor active";
  * a user name and password in the printer address are removed and never repeated in a message;
  * the date-format setting in lower case; report_pdf imported with report_service, not inside an action.
"""

from __future__ import annotations

import io
import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from glucocraft import i18n
from glucocraft import regional as rg
from glucocraft import report as R
from glucocraft.regional import Regional
from glucocraft.units import report_threshold_texts

from conftest import LA, Rd

reportlab = pytest.importorskip("reportlab")
from glucocraft import report_pdf as P

from js_i18n import catalogue
from node_runner import NODE, needs_node, node_json

REPO = Path(__file__).resolve().parents[1]
WWW = REPO / "custom_components" / "glucocraft" / "www"
REPORT_JS = (WWW / "glucocraft-report.js").read_text(encoding="utf-8")
NOW_LOCAL = datetime(2026, 10, 22, 9, 0, tzinfo=LA)
NOW = int(NOW_LOCAL.timestamp())


def _readings():
    """60 days of five-minute readings: a nightly low (one level 2 night a week), one LO and one HI hour."""
    out = []
    lo_at = int(datetime(2026, 10, 13, 3, tzinfo=LA).timestamp())
    hi_at = int(datetime(2026, 10, 14, 16, tzinfo=LA).timestamp())
    for t in range(NOW - 60 * 86400, NOW - 3600, 300):
        lt = datetime.fromtimestamp(t, LA)
        if lo_at <= t < lo_at + 1800:
            out.append(Rd(t, 40.0, "low"))
        elif hi_at <= t < hi_at + 1800:
            out.append(Rd(t, 400.0, "high"))
        elif lt.hour == 4 and lt.minute < 40:
            out.append(Rd(t, 50.0 if lt.weekday() == 2 else 62.0))
        else:
            out.append(Rd(t, 120.0 + 70.0 * ((t // 3600) % 7 == 0)))
    return out


READINGS = _readings()


def _data(days=30, full=True, unit="mg/dL", rgl=None):
    rgl = rgl or Regional()
    d = R.build_report(READINGS, 300, LA, days, full, [{"date": "2026-09-01", "a1c": 7.1}], now=NOW, regional=rgl)
    d.update(
        {
            "label": f"{days} Days",
            "sources": ["Test source"],
            "created": rgl.stamp_year(NOW_LOCAL),
            "unit": unit,
            "thresholds": report_threshold_texts(unit),  # as report_service.async_data sends them
            **rgl.as_dict(),
            "integration_version": "0.0.2",
        }
    )
    return d


# ------------------------------------------------------------------------------------------------ the PDF
@pytest.mark.slow
@pytest.mark.parametrize(
    "paper,unit,full,color",
    [("letter", "mg/dL", True, True), ("a4", "mmol/L", True, False), ("letter", "mg/dL", False, False)],
)
def test_no_pdf_text_below_8_points(paper, unit, full, color):
    fitz = pytest.importorskip("pymupdf")
    rgl = Regional("24" if unit == "mmol/L" else "12", "DMY", 0, ",", "both", paper)
    pdf, _ = P.render(_data(30, full, unit, rgl), color, full)
    sizes = []
    for page in fitz.open(stream=pdf, filetype="pdf"):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if span["text"].strip():
                        sizes.append((round(span["size"], 2), span["text"][:40]))
    small = [s for s in sizes if s[0] < P.MIN_PT - 0.01]
    assert not small, small[:10]  # the smallest text is 8 pt


@pytest.mark.slow
def test_pdf_hourly_log_and_events_chart():
    pypdf = pytest.importorskip("pypdf")
    data = _data(30, True)
    pdf, _ = P.render(data, True, True)
    text = " ".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    flat = " ".join(text.split())
    assert (
        "LO and HI: a reading below (LO) or above (HI) the CGM sensor's measurement range, whose value is not known."
        in flat
    )
    assert re.search(r"\bLO\b", text) and re.search(r"\bHI\b", text)
    assert " ".join(P._scatter_caption().split())[:120] in flat
    levels = {row[4] for row in data["lows"]["list"]}
    assert levels == {1, 2}  # one level 2 night a week, the others level 1


def test_printer_address_without_user_and_password():
    assert P.printer_url("ipp://printuser:S3cretPW@192.0.2.10/ipp/print") == (
        "ipp://192.0.2.10:631/ipp/print",
        "http://192.0.2.10:631/ipp/print",
    )
    assert P.printer_url("user@printer.local:8631") == (
        "ipp://printer.local:8631/ipp/print",
        "http://printer.local:8631/ipp/print",
    )
    # an address that cannot be parsed: the message names the problem, never the address or the password
    ok, msg = P.ipp_print("ipp://printuser:S3cretPW@192.0.2.10:abc/ipp/print", b"%PDF-", "job", timeout=2)
    assert not ok and "S3cretPW" not in msg and "192.0.2.10" not in msg
    assert msg == "the printer address in GlucoCraft's settings is not valid"


def test_pdf_wording():
    definition = i18n.t("report.research.definition")
    assert "hyperglycaemia" not in definition.replace("hyperglycaemic event rate", "")
    assert "extended hyperglycemia as 90" in definition
    src = (REPO / "custom_components" / "glucocraft" / "report_pdf.py").read_text(encoding="utf-8")
    src += (REPO / "custom_components" / "glucocraft" / "i18n" / "en" / "report.json").read_text(encoding="utf-8")
    assert "sensor data" not in src and "70% data" not in src


def test_report_service_imports_report_pdf_with_the_module():
    src = (REPO / "custom_components" / "glucocraft" / "report_service.py").read_text(encoding="utf-8")
    assert src.count("from . import report_pdf") == 1
    assert re.search(r"^from \. import report_pdf$", src, re.MULTILINE)  # at module level, not inside an action


# ------------------------------------------------------------------------------------------------ the date format
@pytest.mark.parametrize(
    "stored, resolved", [("mdy", "MDY"), ("dmy", "DMY"), ("ymd", "YMD"), ("DMY", "DMY"), ("auto", "DMY")]
)
def test_date_format_setting_in_lower_case(stored, resolved):
    assert rg.resolve_date(stored, "DE") == resolved  # Germany: day-month-year when Automatic
    if stored != "auto":
        assert Regional(date_format=stored).date == resolved


# ------------------------------------------------------------------------------------------------ the report page
def _page(expr: str, data: dict | None = None) -> object:
    """Evaluate expr inside glucocraft-report.js (its module-level helpers and the card class, as `card`) with minimal
    browser stand-ins; `data` is available as the report data."""
    if not NODE:
        pytest.skip("node not available")
    stubs = (
        "globalThis.window = globalThis; globalThis.HTMLElement = class {}; const __reg = {};\n"
        "globalThis.customElements = { get: (n) => __reg[n], define: (n, c) => { __reg[n] = c; } };\n"
        "globalThis.ResizeObserver = class { observe() {} disconnect() {} };\n"
    )
    tail = (
        "\nconst card = new (customElements.get('glucocraft-report'))();\n"
        f"const data = {json.dumps(data or {})};\n"
        "process.stdout.write('\\u0001' + JSON.stringify((() => { " + expr + " })()));\n"
    )
    return node_json(stubs + REPORT_JS + tail)


def test_page_gmi_in_mmol_mol_from_the_report_data():
    # GMI 5.0% of a mean of 72.0 mg/dL: 32 mmol/mol (Bergenstal et al., 2018); the page converts nothing itself (the
    # report data carry every value in mmol/mol): without it, "-" in mmol/mol alone and the percent alone with both
    out = _page(
        "GC.a1c = 'mmol_mol'; const a = rA1c(5.0, 32), b = rA1c(5.0); GC.a1c = 'both'; "
        "return [a, b, rA1c(5.0, 32), rA1c(5.0), rA1cDiff(0.4, 5), rA1cDiff(0.4), rA1cDiff(0.4, 5, true)];"
    )
    pts = "+0.4 percentage points"
    assert out == [
        "32 mmol/mol",
        "-",
        "5.0% (32 mmol/mol)",
        "5.0%",
        f"{pts} (+5 mmol/mol)",
        pts,
        "+0.4 (+5 mmol/mol)",  # the laboratory table's cell, as the PDF's (the Notes name the unit)
    ]
    assert "10.929" not in REPORT_JS and "2.15" not in REPORT_JS  # the master equation stays in regional.py


def test_page_draws_the_daily_breaks_of_the_report_data():
    vals = [120] * 144
    out = _page(
        "const r = {daily: [data.v, data.v], daily_labels: ['a', 'b'], daily_breaks: [[30], []]};"
        "const html = card.dailyProfiles(r, 'Daily', []);"
        'const paths = [...html.matchAll(/<path d="([^"]*)"/g)].map((m) => m[1]);'
        "return [paths[0].split('M').length - 1, paths[3].split('M').length - 1];",
        {"v": vals},
    )
    assert out == [2, 1]  # day 1: the line starts again at slot 30; day 2: one line


def test_page_hourly_log_shows_lo_and_hi_and_events_by_level():
    d = _data(30, True)
    out = _page(
        "const log = card.fullSections(data); const ev = card.eventsChart(data.lows.list);"
        "return [log.includes('<br>LO</td>'), log.includes('>HI<br>'),"
        '(ev.match(/fill="#9B1C1F"/g) || []).length,'
        "(ev.match(/fill=\"#E5352B\"/g) || []).length, ev.includes('lowest LO')];",
        d,
    )
    n2 = sum(1 for row in d["lows"]["list"] if row[4] == 2)
    assert out[0] and out[1] and out[4]
    assert out[2] == n2 and out[3] == len(d["lows"]["list"]) - n2  # the marker color is the event's level


def test_page_styles_and_wording():
    sizes = [float(s) for s in re.findall(r"font-size:\s*([\d.]+)px", REPORT_JS)]
    assert sizes and min(sizes) >= 14  # the smallest text is 14 px
    assert '"#000"' in REPORT_JS and 'RC[k] + (k === "vlow" ? "" : "cc")' not in REPORT_JS  # opaque log cells
    # the page's texts: its own (report_page.*) and those it shares with the PDF (report.json)
    page = {**catalogue("frontend"), **catalogue("report")}
    texts = json.dumps({k: v for k, v in page.items() if k.startswith(("report_page.", "report."))}, ensure_ascii=False)
    joined = re.sub(r'[`"]\s*\+\s*\n?\s*[`"]', "", REPORT_JS) + texts
    assert "attention required" not in joined
    assert "sensor data" not in joined and "70% data" not in joined
    assert "hyperglycaemia as" not in joined and "extended hyperglycemia as 90" in joined
    assert "binary?file=backfill" not in REPORT_JS  # the line breaks come from the report data
    assert "either version 3 of the License, or (at your option)" not in joined  # AGPL-3.0-only
    assert '"report.lows.scatter_caption"' in REPORT_JS  # one caption for the page and the PDF (mg/dL)
    assert P._L(page["report.lows.scatter_caption"]) == P._scatter_caption()  # the same text, its thresholds filled


# ------------------------------------------------------------------------------------------------ zones and keys
ZONE_CASES = [
    (53.9, "vlow"),
    (54, "low"),
    (69.9, "low"),
    (70, "target"),
    (180, "target"),
    (180.1, "high"),
    (250, "high"),
    (250.1, "vhigh"),
]


def test_cell_zones_are_the_inclusive_ranges_of_every_statistic():
    """The daily patterns' cells are shaded by the zone of their mean as every statistic counts it: 70 to 180 mg/dL
    inclusive is in range, so a mean of exactly 180 mg/dL is not shaded (Battelino et al., 2019)."""
    assert [P.zone_key(v) for v, _ in ZONE_CASES] == [z for _, z in ZONE_CASES]
    readings = [Rd(NOW - 600 + 300 * k, v) for k, v in enumerate((180.0, 250.0))]
    st = R.compute_period_stats(readings, "t", 600, 300)
    assert st.pct_time_in_target_range == 50.0 and st.time_high == 50.0  # 180 in range, 250 high
    # the page's zones from the report data's one table (report.build_report "zones", const.AGP_RANGES)
    zones = R.build_report([], 300, LA, 14, False, [], now=NOW)["zones"]
    js = f"rSetZones(data.zones); return {json.dumps([v for v, _ in ZONE_CASES])}.map((v) => rZone(v));"
    assert _page(js, {"zones": zones}) == [z for _, z in ZONE_CASES]


@pytest.mark.slow
def test_six_month_pdf_states_why_it_has_no_comparison():
    pypdf = pytest.importorskip("pypdf")
    data = _data(180, False)
    assert data["previous"] is None
    pdf, _ = P.render(data, True, False)
    text = " ".join(" ".join((p.extract_text() or "").split()) for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    note = " ".join(i18n.t("report.change.six_months", days=360, retention=200).split())
    assert "Comparison with the Previous Period" in text and note in text


def _page_html(data: dict) -> str:
    """The Report page drawn from the report data (its whole HTML)."""
    from test_accessibility_checks import REPORT_PARTS_JS, STUBS

    return node_json(
        STUBS
        + REPORT_PARTS_JS
        + REPORT_JS
        + "\nconst card = new (customElements.get('glucocraft-report'))(); card.setConfig({});\n"
        + f"card._hass = {{ states: {{}} }}; card._data = {json.dumps(data)}; card.draw();\n"
        + "process.stdout.write('\\u0001' + JSON.stringify(REPORT_PARTS.map((s) => card.shadowRoot.querySelector(s).innerHTML).join('')));\n"
    )


@needs_node
def test_page_low_glucose_summary_heads_each_period_with_its_sensor_active():
    """The full report's low-glucose summary on the page: two head rows of column headers, the periods and
    their sensor active, then one row per count with its row header (as in the PDF)."""
    html = _page_html(_data(30, True))
    table = html[html.index('aria-labelledby="r-events"') :]
    rows = re.findall(r"<tr>(.*?)</tr>", table[: table.index("</table>")], re.DOTALL)
    assert "<td" not in rows[0] and "<td" not in rows[1]
    assert rows[0].count('<th scope="col"') == rows[1].count('<th scope="col"') >= 2
    assert rows[2].startswith('<th scope="row"')


@needs_node
def test_page_comparison_rules_six_months_and_the_seventy_percent_rule():
    """The page names the 360 days a 6-month comparison would need (twice the report's length), and compares a
    previous period only when the backend's rule before rounding says so (enough_data), not by the shown 70.0%."""
    six = _page_html(_data(180, False))
    assert " ".join(i18n.t("report.change.six_months", days=360, retention=200).split()) in " ".join(six.split())
    d = _data(30, False)
    d["previous"].update({"active": 69.99, "enough_data": False})
    html = _page_html(d)
    assert "has 69.99% sensor active (data received), less than the 70%" in html
    assert i18n.t("report.change.this_period") not in html
    d["previous"]["enough_data"] = True
    assert i18n.t("report.change.this_period") in _page_html(d)


def test_hour_labels_and_their_key_on_the_page_and_in_the_pdf():
    """The daily log's hour labels are the same on the page and in the PDF (12a, 6a, 12p, 6p in the 12-hour format),
    with one key line for the shortened labels."""
    d = _data(30, True)
    html = _page("GC.time = '12'; return card.fullSections(data);", d)
    key = i18n.t(
        "report.log.hours_12",
        h0="12a",
        t0="12:00 AM",
        h6="6a",
        t6="6:00 AM",
        h12="12p",
        t12="12:00 PM",
        h18="6p",
        t18="6:00 PM",
    )
    assert key in html and ">6a<" in html and ">6p<" in html
    html24 = _page("GC.time = '24'; return card.fullSections(data);", d)
    assert "12a" not in html24 and i18n.t("report.log.hours_12").split(":")[0] not in html24


@needs_node
@pytest.mark.parametrize(
    "unit, means, below, mean_diff, below_diff",
    [
        ("mg/dL", (121.5, 120.4), (2.5, 2.5), "+2 mg/dL", "0 percentage points"),  # 122 - 120; the same %: no sign
        ("mg/dL", (98.4, 97.6), (3.0, 2.5), "0 mg/dL", "+0.5 percentage points"),  # both shown as 98
        ("mmol/L", (120.4, 119.4), (1.5, 2.0), "+0.1 mmol/L", "-0.5 percentage points"),  # 6.7 - 6.6
        ("mmol/L", (120.0, 119.9), (2.0, 2.0), "0.0 mmol/L", "0 percentage points"),  # both 6.7
    ],
)
def test_comparison_differences_are_those_of_the_values_shown_on_the_page_and_in_the_pdf(
    unit, means, below, mean_diff, below_diff
):
    """Every difference of the comparison table is the difference of the two values as shown, with no sign for zero,
    the same on the page and in the PDF (the difference of the shown values, rounded once)."""
    pypdf = pytest.importorskip("pypdf")
    d = _data(30, False, unit)
    d["period"].update({"mean": means[0], "below_70": below[0]})
    d["previous"].update({"mean": means[1], "below_70": below[1], "compare_ok": True, "tir": 70.0})
    shown_html = re.sub(r'<span class="sr">.*?</span>', "", _page_html(d))  # as shown (the spoken forms aside)
    html = " ".join(re.sub(r"<[^>]+>", " ", shown_html).split())
    pdf, _ = P.render(d, True, False)
    text = " ".join(" ".join((p.extract_text() or "").split()) for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    for shown in (html, text):
        row = shown[shown.find("Mean glucose", shown.find("Comparison with")) :][:60]
        assert re.match(rf"Mean glucose \S+ {re.escape(unit)} \S+ {re.escape(unit)} {re.escape(mean_diff)}", row), row
        row = shown[shown.find("Time below", shown.find("Comparison with")) :][:60]
        assert re.match(rf"Time below \S+ \S+% \S+% {re.escape(below_diff)}", row), row


def test_page_reflows_to_320_pixels_and_its_scrolling_tables_are_reachable():
    """WCAG 1.4.10 and 2.1.1 (measured in a browser at 320 CSS pixels: no sideways scrolling of the page): no grid
    column is wider than the card, and every table that may be wider scrolls in a region the keyboard reaches, named
    by its heading."""
    for grid in ("grid2", "cal", "grid3"):
        assert re.search(rf"\.{grid} {{[^}}]*minmax\(min\(\d+px, 100%\), 1fr\)", REPORT_JS), grid
    assert ".scroll { overflow-x: auto; position: relative; }" in REPORT_JS  # the .sr texts stay inside it
    assert '<div class="scroll">' not in REPORT_JS  # every scrolling box is rScroll's region
    ids = re.findall(r'rScroll\("(r-[a-z]+)"', REPORT_JS)
    assert len(ids) == len(set(ids)) >= 10
    # the laboratory results (six columns with the Remove button) scroll in their own region on a narrow screen
    assert 'rScroll("r-lab", `<table class="t">' in REPORT_JS
    for i in ids:
        assert f'id="{i}"' in REPORT_JS, i


# ------------------------------------------------------------------------------------------------ the thresholds
def _every_text() -> dict[str, str]:
    """Every text of the CGM Report's catalogue, and the Report page's own texts that name a threshold."""
    page = catalogue("frontend")
    texts = {k: "".join(v) if isinstance(v, list) else v for k, v in catalogue("report").items()}
    return {**texts, **{k: page[k] for k in ("report_page.tir.above", "report_page.tir.below")}}


def _filled(text: str, values: dict[str, str]) -> str:
    """The text with each threshold placeholder replaced by its value, every other character as written."""
    return P.THRESHOLD_PLACEHOLDER.sub(lambda m: values.get(m[1], m[0]), text)


def test_the_threshold_table_is_the_published_pairs():
    """One table for the PDF and the page (units.REPORT_THRESHOLDS): the consensus thresholds as published in both
    units (54 / 70 / 140 / 180 / 250 mg/dL = 3.0 / 3.9 / 7.8 / 10.0 / 13.9 mmol/L) and the ranges between them; every
    placeholder the texts use is in it."""
    from glucocraft.units import CONSENSUS_MMOLL, REPORT_THRESHOLDS

    for mg, mm in CONSENSUS_MMOLL.items():
        assert REPORT_THRESHOLDS[f"t{mg}"] == (str(mg), f"{mm:.1f}")
    assert REPORT_THRESHOLDS["t54_69"] == ("54–69", "3.0–3.8") and REPORT_THRESHOLDS["t181_250"][1] == "10.1–13.9"
    used = {m[1] for text in _every_text().values() for m in re.finditer(r"\{(t(?:54|70|140|180|250)\w*)\}", text)}
    assert used and used <= set(REPORT_THRESHOLDS)
    assert report_threshold_texts("mmol/L")["unit"] == "mmol/L" and report_threshold_texts("mg/dL")["t70"] == "70"
    # no scan for numbers is left: neither the page's nor the PDF's list of ranges to replace
    assert "RL_PAIRS" not in REPORT_JS and "_PAIRS" not in Path(P.__file__).read_text(encoding="utf-8")


@pytest.mark.parametrize("dec", [".", ","])
def test_every_report_text_in_mmol_changes_no_digit_outside_its_placeholders(monkeypatch, dec):
    """Rendering every text of the report in mmol/L (the PDF's _L) fills the threshold placeholders from the table and
    leaves every other character, every other number included, as written: no text is scanned for numbers."""
    monkeypatch.setitem(P._U, "mmol", True)
    monkeypatch.setitem(P._RG, "rg", Regional(decimal_separator=dec))
    values = {k: v.replace(".", dec) for k, v in report_threshold_texts("mmol/L").items()}
    for key, text in _every_text().items():
        rendered = P._L(text)
        assert rendered == _filled(text, values), key
        # the digits outside the placeholders, in order, are those of the text as written: the rendered text without
        # the values of its placeholders has exactly the digits of the text without its placeholders
        remaining = rendered
        for m in P.THRESHOLD_PLACEHOLDER.finditer(text):
            remaining = remaining.replace(values.get(m[1], m[0]), "", 1)
        assert re.sub(r"\D", "", remaining) == re.sub(r"\D", "", P.THRESHOLD_PLACEHOLDER.sub("", text)), key


@needs_node
def test_every_report_text_on_the_page_in_mmol_changes_no_digit_outside_its_placeholders():
    """The same on the Report page (rL): the placeholders filled from the table sent with the report data; nothing else
    changed (the decimal point here; the decimal comma applies to the whole text, as before)."""
    texts = _every_text()
    out = _page(
        "RT_THR = data.thr; return Object.fromEntries(Object.entries(data.texts).map(([k, t]) => [k, rL(t)]));",
        {"thr": report_threshold_texts("mmol/L"), "texts": texts},
    )
    values = report_threshold_texts("mmol/L")
    assert {k: _filled(t, values) for k, t in texts.items()} == out
