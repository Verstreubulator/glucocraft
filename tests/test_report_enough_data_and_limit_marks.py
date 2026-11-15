# License: AGPL-3.0-only (LICENSE).
"""The CGM Report: enough data for a period's values, the LO / HI marks at the graphs' edges, and the report's
smaller rules (page and PDF alike).

Rules under test (expected numbers computed by hand in the comments):
  * a period's values only with sensor active at least 70%, unrounded, for every period of the report (page 1, the
    period, the previous period, the 14-day blocks, the weeks, the days, the event summary, the appendices); one note
    per period with its sensor active and, for a period that ends with the report, about how much more data it needs
    with no gaps (more_needed); the graphs are drawn either way;
  * readings at the sensor's limit marked along the bottom (LO) or top (HI) edge of the daily profiles and the glucose
    profile, in runs of 10-minute slots and 15-minute bins (daily_limits, profile_limits), with the letters once per
    run and a sentence in the chart's description;
  * a report period that does not exist is refused with its text, never replaced by the 14-day report;
  * the mmol/L axis labels in one format per axis; a whole day per day is "24 h"; the events chart's scale holds the
    54 mg/dL line its Notes name; a dash is read "not available" and a unit in a PDF table cell is read in words; the
    weekly charts' descriptions give the table's decimals; the laboratory table's difference cell as in the PDF; the
    daily profiles in calendar weeks on the page; the two-sided print choice; the license notices of the PDF on the
    page; a render is the same bytes twice; the gap rule's median as built.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from glucocraft import const, gaps, i18n
from glucocraft import report as R
from glucocraft import report_service as RS
from glucocraft.regional import Regional
from glucocraft.units import report_threshold_texts

from conftest import LA, Rd

reportlab = pytest.importorskip("reportlab")
pypdf = pytest.importorskip("pypdf")
from glucocraft import report_pdf as P

from node_runner import needs_node, node_json
from test_accessibility_checks import REPORT_PARTS_JS, STUBS

REPO = Path(__file__).resolve().parents[1]
REPORT_JS = (REPO / "custom_components" / "glucocraft" / "www" / "glucocraft-report.js").read_text(encoding="utf-8")
NOW_LOCAL = datetime(2026, 10, 22, 9, 0, tzinfo=LA)
NOW = int(NOW_LOCAL.timestamp())
DAY = 86400


def local(*args) -> int:
    return int(datetime(*args, tzinfo=LA).timestamp())


def readings(start: int, end: int, value=120.0, step: int = 300, lo=None) -> list[Rd]:
    """Readings every `step` seconds from start to before end; lo: (from, to) of LO readings (40 mg/dL, "low")."""
    out = []
    for t in range(start, end, step):
        if lo and lo[0] <= t < lo[1]:
            out.append(Rd(t, 40.0, "low"))
        else:
            out.append(Rd(t, value))
    return out


def with_page_keys(d: dict, days: int, unit: str = "mg/dL") -> dict:
    """The report data as report_service sends them to the page and the PDF."""
    rg = Regional()
    d.update(
        {
            "label": i18n.t(f"report.period.{days}"),
            "sources": ["Test source"],
            "created": rg.stamp_year(NOW_LOCAL),
            "created_ts": NOW // 60 * 60,
            "unit": unit,
            "thresholds": report_threshold_texts(unit),
            **rg.as_dict(),
            "integration_version": "0.0.46",
            "ifcc": {"factor": 10.929, "offset": 2.15},
            "license": RS.license_texts(),
            "lab_limits": {"percent": [3.0, 20.0], "mmol_mol": [9.0, 195.0]},
        }
    )
    return d


def pdf_text(d: dict, full: bool) -> str:
    """The PDF's text without any whitespace (line breaks do not matter)."""
    pdf, _pages = P.render(d, True, full)
    text = " ".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    return re.sub(r"\s+", "", text.replace(P.NB, " "))


def flat(text: str) -> str:
    return re.sub(r"\s+", "", text)


def page_html(d: dict, config: str = "{}") -> str:
    """The Report page drawn from the report data (its whole HTML)."""
    return node_json(
        STUBS
        + REPORT_PARTS_JS
        + REPORT_JS
        + f"\nconst card = new (customElements.get('glucocraft-report'))(); card.setConfig({config});\n"
        + f"card._hass = {{ states: {{}} }}; card._data = {json.dumps(d)}; card.draw();\n"
        + "process.stdout.write('\\u0001' + JSON.stringify(REPORT_PARTS.map((s) => "
        + "card.shadowRoot.querySelector(s).innerHTML).join('')));\n"
    )


def page_eval(expr: str, data: dict | None = None) -> object:
    """Evaluate expr (an async function body) after the page's module (its helpers in scope; `data` given)."""
    return node_json(
        STUBS
        + REPORT_PARTS_JS
        + REPORT_JS
        + f"\nconst data = {json.dumps(data or {})};\n"
        + "process.stdout.write('\\u0001' + JSON.stringify(await (async () => { "
        + expr
        + " })()));\n"
    )


# ------------------------------------------------------------------------------------------------ more data needed
@pytest.mark.parametrize(
    "period, covered, expected",
    [
        (30 * DAY, 0.23 * 30 * DAY, ["days", 14]),  # 70% of 30 days = 21 days; 21 - 6.9 = 14.1 -> about 14
        (14 * DAY, 0, ["days", 10]),  # 9.8 days -> 10 (halves up, whole days from one day on)
        (14 * DAY, 2821 * 300, ["hours", 1]),  # 846,720 - 846,300 = 420 s: less than a day, at least one hour
        (14 * DAY, 0.7 * 14 * DAY - 5400, ["hours", 2]),  # 1.5 hours -> 2
        (14 * DAY, 846720, None),  # exactly 70% (846,720 s): enough
    ],
)
def test_more_data_needed(period, covered, expected):
    assert R.more_needed(period, covered) == expected


def test_runs_of_limit_slots():
    kinds = [set(), {"low"}, {"low", "high"}, {"high"}, set(), {"low"}]
    assert R._limit_runs(kinds) == [[1, 3, "low"], [2, 4, "high"], [5, 6, "low"]]


# ------------------------------------------------------------------------------------------------ the LO / HI marks
LO_FROM, LO_TO = local(2026, 10, 13, 3), local(2026, 10, 13, 5)  # 03:00 to 05:00 on day 5 of the 14 days


@pytest.fixture(scope="module")
def lo_report() -> dict:
    rd = readings(local(2026, 10, 8), local(2026, 10, 22), lo=(LO_FROM, LO_TO))
    return with_page_keys(R.build_report(rd, 300, LA, 14, False, [], now=NOW), 14)


def test_limit_runs_of_the_daily_profiles_and_the_glucose_profile(lo_report):
    s = lo_report["standard"]
    # 03:00 is 10-minute slot 18 and 15-minute bin 12; the LO readings cover to 05:00: slot 30 and bin 20 (exclusive)
    assert s["daily_limits"][5] == [[18, 30, "low"]]
    assert all(runs == [] for k, runs in enumerate(s["daily_limits"]) if k != 5)
    assert s["profile_limits"] == [[12, 20, "low"]]
    # the line still breaks: no value in the limit slots, values beside them
    assert s["daily"][5][18:30] == [None] * 12 and s["daily"][5][17] == 120.0 and s["daily"][5][30] == 120.0
    # the 24 LO readings stay a category: 7,200 s of 1,209,600 s = 0.595% very low (0.6), in range 99.405% (99.4)
    assert s["limit_readings"] == {"low": 24, "high": 0} and s["very_low"] == 0.6 and s["tir"] == 99.4
    assert s["enough_data"] is True and s["more_needed"] is None


def test_the_pdf_marks_the_runs_and_names_them_in_the_description(lo_report, monkeypatch):
    calls = []
    real = P.Report.limit_marks

    def spy(self, x, w, y_bottom, y_top, runs, slots, labels=True):
        calls.append((slots, [list(r) for r in runs or []]))
        return real(self, x, w, y_bottom, y_top, runs, slots, labels)

    monkeypatch.setattr(P.Report, "limit_marks", spy)
    P.render(lo_report, True, False)
    assert (96, [[12, 20, "low"]]) in calls  # the glucose profile of page 1
    assert (144, [[18, 30, "low"]]) in calls  # the daily profile of 10/13
    said = P._alt_day("Tuesday, October 13", lo_report["standard"]["daily"][5], [[18, 30, "low"]])
    assert said.endswith("Readings at the sensor's limit, which have no value: below the sensor's lower limit.")
    assert P._alt_day("Monday", [120.0] * 144) == P._alt_day("Monday", [120.0] * 144, [])


@needs_node
def test_the_page_marks_the_runs_and_lays_the_days_out_in_calendar_weeks(lo_report):
    html = page_html(lo_report)
    # slot 18 of 144 = 12.5% from the left; the marks from x 12.5 to 20.8 (30 / 144) at the bottom edge (60 - 1.6)
    assert 'class="lim lo" style="left:12.5%">LO</span>' in html
    assert '<line x1="12.5" x2="20.8" y1="58.4" y2="58.4"' in html
    # the glucose profile: its LO letters inside the chart, and its description with the limit sentence
    assert '<text class="lim"' in html and "which have no value: below the sensor&#39;s lower limit." in html.replace(
        "'", "&#39;"
    )
    # 10/08/2026 is a Thursday: four empty cells before it in a week from Sunday (first_weekday 6, the default
    # regional settings in the United States), as the PDF lays the rows out
    assert lo_report["first_weekday"] == 6 and lo_report["standard"]["daily_weekdays"][0] == 3
    assert '<div class="days"><div></div><div></div><div></div><div></div><div class="day">' in html
    assert 'aria-labelledby="r-daily-14"' in html


# ------------------------------------------------------------------------------------------------ enough data
@pytest.fixture(scope="module")
def short_report() -> dict:
    """A 30-day full report with data in its last 7 days only (10/15 to 10/21, 120 mg/dL every 5 minutes)."""
    rd = readings(local(2026, 10, 15), local(2026, 10, 22))
    custom = {"ranges": {}, "goals": {"time_in_range": {"op": ">", "value": 70}}}
    d = R.build_report(rd, 300, LA, 30, True, [], now=NOW, custom=custom, research=True)
    return with_page_keys(d, 30)


def test_no_value_of_a_period_without_enough_data(short_report):
    d = short_report
    p, s = d["period"], d["standard"]
    # 7 days of 30 = 604,800 / 2,592,000 s = 23.33% (23.3); of 14 = 50.0%
    assert (p["active"], p["enough_data"], s["active"], s["enough_data"]) == (23.3, False, 50.0, False)
    # 70% of 30 days = 1,814,400 s, less 604,800 = 1,209,600 s = 14 days; of 14 days 846,720 - 604,800 = 241,920 s
    # = 2.8 days -> about 3
    assert p["more_needed"] == ["days", 14] and s["more_needed"] == ["days", 3]
    assert all(p[k] is None for k in R._PERIOD_VALUES) and all(s[k] is None for k in R._PERIOD_VALUES)
    assert p["days_with_data"] == 7 and s["profile"]["p50"][40] == 120.0  # the graphs keep their data
    assert d["previous"]["more_needed"] is None  # a past period: no estimate
    assert d["lows"] == {"enough_data": False, "list": []} and d["highs"] == {"enough_data": False}
    assert d["low_events"] == [
        {"days": 14, "active": 50.0, "enough_data": False},
        {"days": 30, "active": 23.3, "enough_data": False},
    ]
    # the blocks, oldest first: 09/22-09/23 (2 days), 09/24-10/07 and 10/08-10/21 (7 of 14 days)
    assert [(b["days"], b["active"], b["enough_data"], b["events"], b["tir"]) for b in d["blocks"]] == [
        (2, 0.0, False, None, None),
        (14, 0.0, False, None, None),
        (14, 50.0, False, None, None),
    ]
    # every period dict carries its limit readings, a fact of the data given with or without enough data (the API's
    # documented shape: docs/ENTITIES_AND_API.md)
    assert all(set(b) >= {"period_start", "enough_data", "limit_readings", "limit_seconds"} for b in d["blocks"])
    days = d["days"]
    assert days["enough_data"] is False and days["mean"] is None and days["pattern_2h"] == [None] * 12
    # a day is a period of its own: the 7 days with data have their mean and events, the 23 before them none
    assert days["week"][-1][1:] == [120.0, 0, 100] and days["week"][0][1:] == [None, None, 0]
    october = d["monthly"][-1]["days"]
    assert october[14][1:] == [120.0, 0, 100] and october[0][1:] == [None, None, 0]  # 10/15 and 10/01
    assert d["research_appendix"] == {"events": None, "total_minutes": None, "longest_minutes": None}
    ca = d["custom_appendix"]
    assert ca["stats"]["enough_data"] is False and ca["comparable"] is False and "mean" not in ca["stats"]
    assert d["quality"]["active"] == 23.3 and d["quality"]["no_data_minutes"] == 23 * 1440


NOTE_30 = (
    "30 Days: not enough data yet. The report shows a period's statistics once the readings received cover at least "
    "70% of it (sensor active). So far they cover 23.3%. With no gaps, about 14 more days are needed."
)
NOTE_14 = (
    "14 Days: not enough data yet. The report shows a period's statistics once the readings received cover at least "
    "70% of it (sensor active). So far they cover 50%. With no gaps, about 3 more days are needed."
)
SHORT_30 = "Not enough data: sensor active (data received) 23.3% of 30 days; the values of a period are shown from 70%."


def test_the_pdf_states_the_note_of_each_period_and_its_sensor_active(short_report):
    text = pdf_text(short_report, True)
    assert flat(NOTE_14) in text and flat(NOTE_30) in text
    assert text.count(flat(SHORT_30)) >= 4  # Hypoglycemia, Hyperglycemia, Daily Patterns, the research appendix
    assert flat(i18n.t("report.about.enough.body")) in text


@needs_node
def test_the_page_states_the_same_notes(short_report):
    html = page_html(short_report)
    text = flat(re.sub(r"<[^>]+>", "", html).replace("&#39;", "'").replace("&amp;", "&"))
    assert flat(NOTE_14) in text and flat(NOTE_30) in text
    assert text.count(flat(SHORT_30)) >= 4
    assert flat(i18n.t("report.about.enough.body")) in text
    assert "undefined" not in html and ">null<" not in html


def test_weeks_without_enough_data_keep_their_sensor_active():
    # week 10/12-10/18 full; week 10/19-10/25 with 3 days (259,200 / 604,800 s = 42.86%, shown 42.9); 10/05-10/11 none
    rd = readings(local(2026, 10, 12), local(2026, 10, 22))
    weeks = R.weekly_trend(rd, 300, datetime(2026, 10, 26, tzinfo=LA), weeks=3)
    assert [(w["week_end"], w["active"], w["enough_data"], w["tir"]) for w in weeks] == [
        ("2026-10-11", None, False, None),
        ("2026-10-18", 100.0, True, 100.0),
        ("2026-10-25", 42.9, False, None),
    ]


def test_one_sensor_active_everywhere_and_the_report_keys_that_are_shown():
    # 2015 five-minute readings in 14 days = 49.975%: one rule now (70%), so 50.0 on every surface, the custom
    # appendix included (it showed 50% beside 49.98% elsewhere)
    rd = [Rd(local(2026, 10, 8) + 300 * i, 150.0) for i in range(2015)]
    d = R.build_report(rd, 300, LA, 14, False, [], now=NOW, custom={"ranges": {}, "goals": {}})
    assert d["standard"]["active"] == d["quality"]["active"] == d["custom_appendix"]["stats"]["active"] == 50.0
    assert set(d["zones"]) == {"range_very_low", "range_low", "range_high", "range_very_high"}


# ------------------------------------------------------------------------------------------------ report periods
def test_a_report_period_that_does_not_exist_is_refused():
    assert RS.period_refused(14) is None and RS.period_refused(180) is None
    assert RS.period_refused(60) == (
        "This report period is not available: 60 days. The CGM Report can be created for 14, 30, 90 or 180 days. "
        "Check the card's or automation's settings."
    )
    reports = RS.GlucoCraftReports.__new__(RS.GlucoCraftReports)
    with pytest.raises(RS.ReportPeriodError) as err:
        asyncio.run(reports.async_data(60))
    assert str(err.value) == RS.period_refused(60)


@needs_node
def test_the_page_shows_the_refusal_text():
    refusal = RS.period_refused(60)
    out = page_eval(
        "const card = new (customElements.get('glucocraft-report'))(); card.setConfig({ days: 60 });"
        "card._hass = { states: {}, fetchWithAuth: async () => ({ ok: false, status: 400, text: async () => "
        f"{json.dumps(refusal)} }}) }}; await card.load(); return [card._alert, card._days];"
    )
    assert out == [refusal, 60]


# ------------------------------------------------------------------------------------------------ smaller rules
def test_mmol_axis_labels_in_one_format(monkeypatch):
    d = with_page_keys(R.build_report([], 300, LA, 14, False, [], now=NOW), 14, "mmol/L")
    rp = P.Report(d, True)
    rp.new_page()
    drawn = []
    monkeypatch.setattr(P.Report, "text", lambda self, x, y, s, *a, **k: drawn.append(s))
    # 108 to 144 mg/dL = 6.0 to 8.0 mmol/L: a step of 0.5 (36 / 18.016 / 5 = 0.40), every label with one decimal
    rp.axes_box(100, 100, 300, 100, 108, 144, xlabels=False)
    assert drawn == ["6.0", "6.5", "7.0", "7.5"]


@needs_node
def test_page_mmol_axis_labels_in_one_format():
    # a flat profile of 120 mg/dL: rFit gives 105.7 to 134.3 mg/dL, a step of 0.5 mmol/L (28.6 / 18.016 / 5 = 0.32)
    out = page_eval(
        "GC.mmol = true; rSetZones({range_very_low: 54, range_low: 70, range_high: 180, range_very_high: 250});"
        "const card = new (customElements.get('glucocraft-report'))(); card.setConfig({});"
        "const p = {p5: Array(96).fill(110), p25: Array(96).fill(115), p50: Array(96).fill(120),"
        " p75: Array(96).fill(125), p95: Array(96).fill(130)};"
        "const svg = card.agp({profile: p}, 'Profile');"
        'return [...svg.matchAll(/text-anchor="end">([^<]*)</g)].map((m) => m[1]);'
    )
    assert out == ["6.0", "6.5", "7.0"]


def test_a_whole_day_per_day_is_24_hours():
    assert P._per_day(1440) == ("(24 h)", "(24 hours)")
    assert P._per_day(1439) == ("(23 h 59 min)", "(23 hours 59 minutes)")


def test_pdf_cells_are_read_in_words():
    assert P._speak_units("102 mg/dL") == "102 milligrams per deciliter"
    assert P._speak_units("7.0%") == "7.0 percent" and P._speak_units("53 mmol/mol") == "53 millimoles per mole"
    assert P._speak_units("2 h 5 min") == "2 hours 5 minutes" and P._speak_units("1 d") == "1 day"
    assert P._speak_units("Wed 10/22") == "Wed 10/22" and P._speak_units("6.2") == "6.2"


@needs_node
def test_page_reads_a_dash_as_not_available_and_the_weekly_charts_with_the_tables_decimals():
    out = page_eval(
        "GC.mmol = false; const card = new (customElements.get('glucocraft-report'))(); card.setConfig({});"
        "const wk = [{week_end: '2026-10-11', tir: 46.8, cv: 30, mean: 140, active: 100},"
        " {week_end: '2026-10-18', tir: 52, cv: 31.5, mean: 150, active: 100}];"
        "const html = card.weekly({weekly: wk});"
        'return [rSay(\'-\'), [...html.matchAll(/aria-label="([^"]*)"/g)].map((m) => m[1])];'
    )
    assert out[0] == '<span aria-hidden="true">-</span><span class="sr">not available</span>'
    assert "Time in range by week, 2 weeks with values: from 46.8 to 52 percent." in out[1]
    assert "Coefficient of variation by week, 2 weeks with values: from 30 to 31.5 percent." in out[1]
    assert P._alt_weekly("tir", [46.8, 52]) == "Time in range by week, 2 weeks with values: from 46.8 to 52 percent."


@needs_node
def test_the_events_chart_draws_the_54_line_its_notes_name():
    # lowest values of 68 mg/dL: the scale of 68, 70 and 54 mg/dL is 47.7 to 76.3 (rFit), so the 54 line is drawn
    out = page_eval(
        "rSetZones({range_very_low: 54, range_low: 70, range_high: 180, range_very_high: 250});"
        "const card = new (customElements.get('glucocraft-report'))(); card.setConfig({});"
        "return card.eventsChart([['Wed 10/14 3:10 AM', 20, 68, 3.2, 1, false]]);"
    )
    assert 'stroke-dasharray="4 3"' in out


@needs_node
def test_page_patterns_table_print_sides_lab_cell_and_license(lo_report):
    d = json.loads(json.dumps(lo_report))
    d.update(full=True, days=R.day_pages(*_grid_and_start()), monthly=[], low_events=[])
    d["lab"] = {
        "results": [
            {
                "date": "2026-10-01",
                "a1c": 7.4,
                "a1c_mmol_mol": 57,
                "gmi_90d": 7.0,
                "gmi_90d_mmol_mol": 53,
                "cgm_active_90d": 95.0,
            }
        ],
        "average_difference": 0.4,
        "average_difference_mmol_mol": 4,
    }
    d["a1c_unit"] = "both"
    html = page_html(d)
    patterns = html[html.index('<table class="pat">') :]
    patterns = patterns[: patterns.index("</table>")]
    assert patterns.count('<th scope="col">') == 12 and "12:00 AM" in patterns  # each hour read as its full time
    assert 'data-act="two:1" aria-pressed="true"' in html and 'data-act="two:0" aria-pressed="false"' in html
    assert ">+0.4 (+4 mmol/mol)<" in html  # the laboratory cell as in the PDF; the Notes name the unit
    assert '<th scope="col"><span class="sr">Remove</span></th>' in html  # the laboratory table's last column
    for text in (const.COPYRIGHT, *const.GNU_NOTICE, const.LICENSE_URL):
        assert text in html.replace("&#39;", "'")
    assert html.count('<caption class="sr">') >= 10  # every results table named for screen readers


def _grid_and_start():
    rd = readings(local(2026, 10, 8), local(2026, 10, 22), lo=(LO_FROM, LO_TO))
    g = R.minute_grid(rd, 300, local(2026, 10, 7), local(2026, 10, 23), LA)
    return g, datetime(2026, 10, 8, tzinfo=LA), 14


def test_a_render_is_the_same_bytes_twice_with_the_creation_time(lo_report):
    a, _ = P.render(lo_report, True, False)
    b, _ = P.render(lo_report, True, False)
    assert a == b
    assert b"D:20261022160000+00'00'" in a  # 09:00 Pacific daylight time = 16:00 UTC


def test_the_pdf_tables_carry_their_names_and_the_log_its_day_header(lo_report):
    d = json.loads(json.dumps(lo_report))
    d.update(full=True, days=R.day_pages(*_grid_and_start()), monthly=[], low_events=[])
    pdf, _ = P.render(d, True, True)
    assert pdf.count(b"/S /Table") == pdf.count(b"/S /Table /T (") >= 10  # every table of the report has its name
    assert b"/T (Daily Log" in pdf


def test_the_gap_rule_text_names_the_median_as_built():
    # ten intervals before the reading: five of 900 s and five of 300 s; the higher of the two middle values is 900
    ts = [k * 900 for k in range(7)] + [5400 + k * 300 for k in range(1, 7)]
    assert gaps.local_intervals(ts, 300)[11] == 900  # sorted, 300 x5 and 900 x5: the upper middle value
    assert "the median of the 10 intervals before the reading (the higher of the two middle values;" in i18n.t(
        "report.about.data.body"
    )


def test_wording_of_this_build():
    assert "(Abbott Diabetes Care, 2018, p. 7)" in i18n.t("report.about.limit.body")
    assert "The results here are calculated with the ranges and targets" in i18n.t("report.page1.notes.groups.body")
    assert "counted at the limit value" not in (R.__doc__ or "") and "p. 9" not in (R.__doc__ or "")
    # the report states the number and the time of the limit readings: 2 LO readings of 10 minutes
    note = P._limit_note({"limit_readings": {"low": 2, "high": 0}, "limit_seconds": {"low": 600, "high": 0}})
    assert note.startswith("2 readings at the sensor's limit (LO 2 (10 minutes)), left out of the values")
