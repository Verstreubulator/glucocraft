# License: AGPL-3.0-only (LICENSE).
"""The CGM Report's data sources, its hypoglycemia events by period and its section layout, in the PDF and on the
report page alike.

- the data sources: every page header names the glucose sources ("Glucose data: <primary>; second source:
  <second>."), and About This Report names them in its text (one source, or both with how the second fills gaps,
  the clause for a second source without measurement times and the share of the period's readings it delivered,
  from the stored source byte, rounded once);
- the full report's hypoglycemia events by period: one column per period of 14 / 30 / 90 / 180 days inside the
  report's period with its sensor active; the counts, level 2, events per day, median and longest duration, the
  starts in eight 3-hour blocks and at night; nothing in the summary; the period's lowest and highest value in the
  Longer-Term Overview, a reading at the sensor's limit as LO / HI;
- the section layout: every section a caption line, its data and Notes at its end (the Notes after every table and
  figure of the section in the tagged PDF's reading order; continued under "<section> (continued)" on the next
  page); About This Report keeps the general method only, each section's explanation stands once, in its Notes.
"""

from __future__ import annotations

import io
import json
import re
from datetime import datetime, timedelta

import pytest
from glucocraft import report as R
from glucocraft.regional import Regional

from conftest import LA, Rd

NOW_LOCAL = datetime(2026, 10, 22, 9, 0, tzinfo=LA)
NOW = NOW_LOCAL.timestamp()
END = NOW_LOCAL.replace(hour=0)  # the report's last midnight


def ts(day_offset: int, hour: float) -> int:
    """Epoch s at local hour `hour` of the day END - day_offset days (no daylight-saving change in these 200 days
    before October 22, 2026)."""
    return int((END - timedelta(days=day_offset)).timestamp() + hour * 3600)


# four level 1 events at known times: (day offset, start hour, minutes, value); the second reaches level 2
EVENTS = ((3, 1 + 10 / 60, 30, 60.0), (20, 13, 45, 50.0), (60, 22.5, 20, 65.0), (100, 7, 60, 62.0))


def _with_events(source=lambda t: 1) -> list[Rd]:
    lows = {}
    for day, hour, minutes, value in EVENTS:
        start = ts(day, hour)
        lows.update({t: value for t in range(start, start + minutes * 60, 300)})
    return [Rd(t, lows.get(t, 120.0), None, source(t)) for t in range(ts(199, 0), int(NOW), 300)]


READINGS = _with_events()


# ------------------------------------------------------------------------------------------------ events by period
def test_hypoglycemia_events_by_period_one_column_per_period_inside_the_report():
    cols = R.build_report(READINGS, 300, LA, 180, True, [], now=NOW)["low_events"]
    assert [c["days"] for c in cols] == [14, 30, 90, 180]
    assert all(c["active"] == 100.0 for c in cols)  # the column header's sensor active, as period_report shows it
    assert [c["events"] for c in cols] == [1, 2, 3, 4]
    assert [c["level2"] for c in cols] == [0, 1, 1, 1]  # the 45 minutes at 50 mg/dL
    assert [c["per_day"] for c in cols] == [0.07, 0.07, 0.03, 0.02]  # two decimals: 1 / 14, 2 / 30, 3 / 90, 4 / 180
    assert [c["median_min"] for c in cols] == [30, 38, 30, 38]  # 37.5 (the mean of the two middle) rounded once
    assert [c["longest_min"] for c in cols] == [30, 45, 45, 60]
    # the starts in the 3-hour blocks of the local clock: 01:10 (block 0), 13:00 (4), 22:30 (7), 07:00 (2)
    assert cols[-1]["blocks"] == [1, 0, 1, 0, 1, 0, 0, 1] and sum(cols[-1]["blocks"]) == cols[-1]["events"]
    assert [c["night"] for c in cols] == [1, 1, 1, 1]  # 00:00-05:59 only the first
    assert "lowest" not in json.dumps(cols)  # no lowest value: an event's lowest value is not the period's minimum


def test_events_by_period_only_in_the_full_report_and_only_periods_inside_it():
    assert "low_events" not in R.build_report(READINGS, 300, LA, 90, False, [], now=NOW)
    assert [c["days"] for c in R.build_report(READINGS, 300, LA, 14, True, [], now=NOW)["low_events"]] == [14]
    assert [c["days"] for c in R.build_report(READINGS, 300, LA, 90, True, [], now=NOW)["low_events"]] == [14, 30, 90]


def test_no_unused_event_fields():
    """Every event field of the report data is shown (the PDF and the page read them)."""
    out = R.build_report(READINGS, 300, LA, 30, True, [], now=NOW, research=True)
    assert "standard_lows" not in out  # the 14-day page shows no hypoglycemia section of its own
    assert set(out["blocks"][0]) >= {"events"} and not {"events_night", "events_54"} & set(out["blocks"][0])
    assert "list" not in out["research_appendix"]  # the appendix shows the count, the total and the longest event


def test_lowest_and_highest_value_leave_lo_and_hi_out():
    rd = [Rd(t, 120.0) for t in range(ts(40, 0), int(NOW), 300)]
    for k, value, limit in ((-2000, 40.0, "low"), (-3000, 400.0, "high"), (-4000, 55.4, None)):  # in the 30 days
        rd[k] = Rd(rd[k].timestamp, value, limit)
    p = R.build_report(rd, 300, LA, 30, False, [], now=NOW)["period"]
    assert (p["min"], p["max"]) == (55.4, 120.0) and "min_is_lo" not in p and "max_is_hi" not in p
    assert p["limit_readings"] == {"low": 1, "high": 1} and p["limit_seconds"] == {"low": 300, "high": 300}
    measured = [Rd(r.timestamp, r.raw) for r in rd]  # the same values, measured (no limit flag)
    p = R.build_report(measured, 300, LA, 30, False, [], now=NOW)["period"]
    assert (p["min"], p["max"]) == (40.0, 400.0)


# ------------------------------------------------------------------------------------------------ the data sources
def test_the_share_of_the_second_source_from_the_source_byte():
    period = (ts(14, 0), int(END.timestamp()))
    second = set(range(period[0], period[0] + 100 * 300, 300))  # 100 of the 4,032 readings of the 14 days
    rd = _with_events(lambda t: 2 if t in second else 1)
    assert R.build_report(rd, 300, LA, 14, False, [], now=NOW)["second_source_pct"] == 2.5  # 2.48% rounded once
    one = _with_events(lambda t: 2 if t == period[0] else 1)
    assert R.second_source_share(one, *period) == 0.02  # 0.025%: never "0.0%" when the second source delivered
    assert R.second_source_share(READINGS, *period) == 0.0  # the primary only (and readings without a source byte)
    assert R.second_source_share(READINGS, 0, 1) is None  # no readings


# ------------------------------------------------------------------------------------------------ the PDF
reportlab = pytest.importorskip("reportlab")
pypdf = pytest.importorskip("pypdf")
from glucocraft import i18n
from glucocraft import report_pdf as P


def _report(days=30, full=True, sources=("Test source",), timed=None, rd=READINGS, rg=None):
    rg = rg or Regional()
    d = R.build_report(rd, 300, LA, days, full, [], now=NOW, regional=rg)
    d.update(
        {
            "label": i18n.t(f"report.period.{days}"),
            "sources": list(sources),
            "second_source_timed": timed,
            "created": "x",
            "unit": "mg/dL",
            **rg.as_dict(),
            "integration_version": "0",
        }
    )
    return d


def _pdf_text(d, full=True) -> str:
    pdf, _pages = P.render(d, True, full)
    pages = pypdf.PdfReader(io.BytesIO(pdf)).pages
    return " ".join(" ".join((p.extract_text() or "").split()) for p in pages).replace(" ", " ")


def test_source_line_and_about_item():
    one = _report(sources=("Libre 3 Glucose",))
    assert P.source_line(one) == "Glucose data: Libre 3 Glucose."
    assert P.data_sources(one) == ("Data source.", "Libre 3 Glucose.")
    two = _report(sources=("Libre 3 Glucose", "sensor.nightscout_glucose"), timed=True)
    two["second_source_pct"] = 2.5
    assert P.source_line(two) == "Glucose data: Libre 3 Glucose; second source: sensor.nightscout_glucose."
    lead, text = P.data_sources(two)
    assert lead == "Data sources." and text == (
        "Primary source: Libre 3 Glucose. Second source: sensor.nightscout_glucose. The second source fills only gaps "
        "in the primary source's readings, each at its own measurement time; a reading measured by both is counted "
        "once, from the primary source. In this period, 2.5% of the readings came from the second source."
    )
    two["second_source_timed"] = False  # a second source without measurement times
    assert P.data_sources(two)[1].endswith(
        "from the primary source; it is used only after the primary source has delivered no reading for 5 minutes or "
        "three reading intervals, whichever is longer. In this period, 2.5% of the readings came from the second source."
    )


@pytest.mark.slow
def test_pdf_names_the_sources_in_the_header_and_in_about_this_report():
    d = _report(days=14, full=False, sources=("Primary CGM", "Backup CGM"), timed=False)
    d["second_source_pct"] = 12.5
    text = _pdf_text(d, False)
    line = "Glucose data: Primary CGM; second source: Backup CGM."
    assert text.count(line) >= 2  # page 1 (as text) and the headers of the further pages
    about = text[text.index("About This Report") :]
    assert "Data sources. Primary source: Primary CGM. Second source: Backup CGM." in about
    assert "whichever is longer. In this period, 12.5% of the readings came from the second source." in about
    assert "Home Assistant" not in text  # the report names no system, only the sources' names


# ------------------------------------------------------------------------------------------------ the section layout
LEADS_MOVED = (
    "Ranges and targets:",
    "Names of the ranges:",
    "CV target:",
    "Metrics:",
    "Glucose profile:",
    "Hypoglycemia event:",
    "Night and day:",
)


@pytest.mark.slow
def test_each_explanation_once_in_its_section_and_about_keeps_the_general_method():
    d = _report()
    text = _pdf_text(d)
    about = text.index("About This Report For development and testing only.")
    for lead in LEADS_MOVED:
        assert text.count(lead) == 1 and text.index(lead) < about, lead  # in the Notes of its section, once
    general = ("This report:", "Targets and assessments:", "Data source.", "Report period:", "Data:", "Rounding:")
    for lead in general:
        assert lead in text[about:], lead
    assert text.count("Notes ") >= 10
    # the events by period: after Data Quality, before the full report's other pages
    assert (
        text.index("Data Quality — 30 Days")
        < text.index("Hypoglycemia Events by Period")
        < text.index("Daily Patterns — 30 Days")
    )
    head = text[text.index("Hypoglycemia Events by Period") :]
    assert re.search(r"Period 14 Days 30 Days Sensor active \(data received\) [\d.]+% [\d.]+%", head)
    for row in ("Level 1 events (below 70 mg/dL)", "Of these, reached level 2 (below 54 mg/dL)", "Events per day"):
        assert row in head
    assert "Starts 12:00 AM–2:59 AM" in head and "Starts at night (12:00 AM–5:59 AM)" in head
    # the Longer-Term Overview: the period's lowest and highest value
    assert re.search(r"Lowest value [\d.]+ mg/dL", text) and re.search(r"Highest value [\d.]+ mg/dL", text)


@pytest.mark.slow
def test_lo_and_hi_leave_the_overviews_lowest_and_highest_value():
    rd = [Rd(t, 120.0) for t in range(ts(40, 0), int(NOW), 300)]
    rd[-2000], rd[-3000] = Rd(rd[-2000].timestamp, 40.0, "low"), Rd(rd[-3000].timestamp, 400.0, "high")
    text = _pdf_text(_report(full=False, rd=rd), False)
    assert "Lowest value 120 mg/dL" in text and "Highest value 120 mg/dL" in text


@pytest.mark.slow
def test_notes_continue_under_the_section_title_on_the_next_page():
    pytest.importorskip("pikepdf")
    from test_pdf_tagging import Tagged

    pdf, _pages = P.render(_report(days=14, full=False), True, False)
    # page 1's Notes start at its foot and continue at the top of page 2 under a running head (an artifact, which
    # screen readers skip; the reading order of the Notes is checked in test_pdf_tagging)
    assert "CGM Report: Continuous Glucose Monitoring (continued)" in Tagged(pdf).artifact_text


# ------------------------------------------------------------------------------------------------ the report page
from node_runner import needs_node
from test_report_outputs import _page_html


@needs_node
def test_page_has_the_same_layout_sources_and_events_by_period():
    d = _report(sources=("Primary CGM", "Backup CGM"), timed=False)
    d["second_source_pct"] = 12.5
    html = _page_html(d)
    plain = " ".join(re.sub(r"<[^>]+>", " ", html).split()).replace("&amp;", "&")
    assert "Glucose data: Primary CGM; second source: Backup CGM." in plain
    assert "Data sources. Primary source: Primary CGM. Second source: Backup CGM." in plain
    assert "In this period, 12.5% of the readings came from the second source." in plain
    about = plain.index("About This Report For development and testing only.")
    for lead in LEADS_MOVED:
        assert plain.count(lead) == 1 and plain.index(lead) < about, lead
    assert "Rounding:" in plain[about:]
    # every Notes block closes its section: no table or chart after it before the next section's heading
    sections = re.split(r'<h2 class="h2[^"]*"', html)
    noted = [s for s in sections if 'class="notes"' in s]
    assert len(noted) >= 12
    for s in noted:
        rest = s[s.index('class="notes"') :]
        assert "<table" not in rest and "<svg" not in rest
    assert "Hypoglycemia Events by Period" in plain and "Lowest value" in plain and "Highest value" in plain
    assert re.search(r"Starts at night \(12:00 AM–5:59 AM\)", plain)
