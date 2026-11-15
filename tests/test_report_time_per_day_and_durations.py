# License: AGPL-3.0-only (LICENSE).
"""The CGM Report's time per day, durations, comparison rule and conditional texts.

Rules under test:
  * the time per day beside the percentages, "(1 h 12 min)": calculated from the unrounded share (1% of time = 14.4
    minutes per day) and rounded once to whole minutes; beside the band labels and under the four summary values of
    page 1 and beside the values of the longer-term overview, on the page and in the PDF, read in words;
  * durations in words in running text ("2 hours 5 minutes"); the unit symbols only in the full report's event summary
    table and in the time per day, read in words, with the key line "Durations: d, days; h, hours; min, minutes." in
    About This Report;
  * the comparison with the previous period only when both periods have at least 50% sensor active; otherwise "No
    comparison: less than 50% sensor active in this period." (or the previous period's text);
  * the clause on the custom appendix (data sufficiency) only with the appendix, and the laboratory sentence naming the
    results only when there are results;
  * the untimed second source's readings are described at the time they were received.
"""

from __future__ import annotations

import io

import pytest
from glucocraft import i18n
from glucocraft import report as R
from glucocraft.units import round_half_up

from conftest import LA, Rd
from node_runner import needs_node

reportlab = pytest.importorskip("reportlab")
from glucocraft import report_pdf as P

from test_report_outputs import NOW, _data, _page, _page_html

PERCENT_KEYS = ("very_low", "low", "tir", "titr", "high", "very_high", "below_70", "above_180")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _pdf_text(data: dict, full: bool) -> str:
    pypdf = pytest.importorskip("pypdf")
    pdf, _ = P.render(data, True, full)
    return _flat(" ".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(pdf)).pages))


# ------------------------------------------------------------------------------------------------ time per day
def test_time_per_day_from_the_unrounded_share():
    """Each percentage's time per day is its unrounded share of 1,440 minutes, rounded once to whole minutes: within
    half a minute of the shown percentage's time plus its rounding (0.05% = 0.72 minutes)."""
    d = _data(30, False)
    for period in (d["standard"], d["period"]):
        pd = period["per_day"]
        assert set(pd) == set(PERCENT_KEYS)
        for key in PERCENT_KEYS:
            assert isinstance(pd[key], int)
            assert abs(pd[key] - period[key] * 14.4) <= 0.72 + 0.5, (key, pd[key], period[key])
        # the bands of the bar add up to a day, give or take the rounding of each
        bands = pd["very_low"] + pd["low"] + pd["tir"] + pd["high"] + pd["very_high"]
        assert abs(bands - 1440) <= 3


def test_time_per_day_of_whole_and_empty_bands():
    """Readings all in range: 24 hours in range and in tight range, 0 minutes elsewhere; without data no times."""
    now = NOW
    rd = [Rd(t, 120.0) for t in range(now - 20 * 86400, now - 3600, 300)]
    st = R.build_report(rd, 300, LA, 14, False, [], now=now)["standard"]
    assert st["per_day"]["tir"] == st["per_day"]["titr"] == 1440
    assert st["per_day"]["very_low"] == st["per_day"]["above_180"] == 0
    assert "per_day" not in R.build_report([], 300, LA, 14, False, [], now=now)["standard"]


def test_the_shown_and_the_spoken_time_per_day():
    assert P._per_day(72) == ("(1 h 12 min)", "(1 hour 12 minutes)")
    assert P._per_day(1008) == ("(16 h 48 min)", "(16 hours 48 minutes)")
    assert P._per_day(60) == ("(1 h)", "(1 hour)")
    assert P._per_day(1) == ("(1 min)", "(1 minute)")
    assert P._per_day(0) == ("(0 min)", "(0 minutes)")
    assert P._per_day(None) == ("", "")
    # 1% of time = 14.4 minutes per day: 5% is 72 minutes, rounded halves away from zero
    assert round_half_up(5.0 * R.MINUTES_PER_PERCENT) == 72


@pytest.mark.slow
def test_pdf_page_1_and_the_overview_show_the_time_per_day():
    d = _data(30, False)
    text = _pdf_text(d, False)
    st, p = d["standard"], d["period"]
    for key in ("very_high", "high", "tir", "low", "very_low", "above_180", "titr", "below_70"):
        assert P._per_day(st["per_day"][key])[0] in text, key
    for key in ("tir", "titr", "below_70", "very_low", "above_180", "very_high"):
        assert f"{P._f(p[key])} {P._per_day(p['per_day'][key])[0]}" in text, key
    assert _flat(i18n.t("report.overview.per_day_note", minutes="14.4")) in text


@pytest.mark.slow
def test_pdf_reads_the_time_per_day_and_the_symbols_in_words():
    d = _data(30, True)
    pdf, _ = P.render(d, True, True)
    # the page content is compressed: the words are found in the /ActualText of the structure elements
    for key in ("tir", "very_high", "below_70"):
        assert P._dur_words(d["standard"]["per_day"][key]).encode() in pdf, key
    longest = d["low_events"][0]["longest_min"]
    assert P._dur_words(longest).encode() in pdf  # the event summary's symbols, read in words


@needs_node
def test_page_shows_the_time_per_day_and_reads_it_in_words():
    d = _data(30, False)
    html = _page_html(d)
    st, p = d["standard"], d["period"]
    for key in ("very_high", "high", "tir", "titr", "low", "very_low", "above_180", "below_70"):
        shown, said = P._per_day(st["per_day"][key])
        assert f'<span aria-hidden="true">{shown}</span><span class="sr">{said}</span>' in html, key
    for key in ("tir", "very_high"):
        assert P._per_day(p["per_day"][key])[0] in html
    assert i18n.t("report.overview.per_day_note", minutes="14.4") in html
    # the page's own formatter gives the PDF's texts
    assert _page("return [rDur(72), rDurW(72), rDurW(1500), rDurW(1)];") == [
        "1 h 12 min",
        "1 hour 12 minutes",
        "1 day 1 hour",
        "1 minute",
    ]


# ------------------------------------------------------------------------------------------------ durations
def test_durations_in_words_and_in_symbols():
    assert [P._dur_words(m) for m in (0, 1, 45, 60, 125, 1440, 1500, 3000)] == [
        "0 minutes",
        "1 minute",
        "45 minutes",
        "1 hour",
        "2 hours 5 minutes",
        "1 day",
        "1 day 1 hour",
        "2 days 2 hours",
    ]
    assert [P._dur(m) for m in (45, 125, 1500)] == ["45 min", "2 h 5 min", "1 d 1 h"]
    assert P._dur_words(None) == P._dur(None) == "-"


@pytest.mark.slow
def test_running_text_writes_durations_in_words_and_about_gives_the_key():
    d = _data(30, True)
    text = _pdf_text(d, True)
    q = d["quality"]
    assert P._dur_words(q["no_data_minutes"]) in text
    key = _flat(i18n.t("report.about.durations.lead") + " " + i18n.t("report.about.durations.body"))
    assert key == "Durations: d, days; h, hours; min, minutes." and key in text
    # the event summary keeps the symbols
    assert P._dur(d["low_events"][0]["longest_min"]) in text


@needs_node
def test_page_durations_in_words_and_the_key_in_about():
    d = _data(30, True)
    html = _page_html(d)
    assert "<b>Durations:</b> d, days; h, hours; min, minutes." in html
    assert f"<b>{P._dur_words(d['quality']['no_data_minutes'])}</b>" in html
    # the events chart's marker names the duration in words
    first = d["lows"]["list"][0]
    assert f"{first[0]}: {P._dur_words(first[1])}, level" in html


# ------------------------------------------------------------------------------------------------ the comparison
@pytest.mark.slow
def test_no_comparison_when_this_period_has_less_than_seventy_percent():
    d = _data(30, False)
    d["period"]["enough_data"] = False
    this = i18n.t("report.change.not_available_this", active=f"{d['period']['active']:g}%")
    assert this in _pdf_text(d, False)
    d["period"]["enough_data"] = True
    d["previous"].update({"enough_data": False, "active": 41.5})
    text = _pdf_text(d, False)
    assert (
        "No comparison: this period" not in text
        and "has 41.5% sensor active (data received), less than the 70%" in text
    )


@needs_node
def test_page_compares_only_when_both_periods_have_enough_data():
    d = _data(30, False)
    d["period"]["enough_data"] = False
    html = _page_html(d)
    this = i18n.t("report.change.not_available_this", active=f"{d['period']['active']:g}%")
    assert this in html and i18n.t("report.change.this_period") not in html
    d["period"]["enough_data"] = True
    assert i18n.t("report.change.this_period") in _page_html(d)


def test_the_comparison_rule_is_stated_for_both_periods():
    assert "both have at least 70% sensor active" in i18n.t("report.change.notes")
    assert i18n.t("report.change.not_available_this", active="23%") == (
        "No comparison: this period has 23% sensor active (data received), less than the 70% that the values of a "
        "period need."
    )


# ------------------------------------------------------------------------------------------------ conditional texts
@pytest.mark.slow
def test_the_custom_appendix_clause_and_the_laboratory_sentence_only_where_they_apply():
    from test_pdf_layout_and_regional import CUSTOM
    from test_pdf_layout_and_regional import _data as data_with

    plain = _flat(P._ctx(P.ctx("sufficiency")))
    custom = _flat(P._ctx(P.ctx("sufficiency_custom")))
    assert "custom appendix" not in plain and "custom appendix" in custom
    without = _pdf_text(_data(30, False), False)
    assert plain in without and "custom appendix compares" not in without
    assert custom in _pdf_text(data_with(30, False, custom=CUSTOM), False)
    # the laboratory sentence: with results it names "the laboratory results in this section"
    results = "This explains why the laboratory results in this section can differ"
    assert results in without
    d = _data(30, False)
    d["lab"]["results"] = []
    d["lab"]["average_difference"] = None
    text = _pdf_text(d, False)
    assert results not in text and "a laboratory result entered in this section can differ" in text


@needs_node
def test_page_conditional_texts():
    d = _data(30, False)
    html = _page_html(d)
    assert "custom appendix compares" not in html and "laboratory results in this section" in html
    d["lab"]["results"] = []
    d["include_custom_appendix"] = True
    html = _page_html(d)
    assert "custom appendix compares" in html and "a laboratory result entered in this section" in html


def test_the_statement_and_the_untimed_second_source():
    assert "Where colors or the words met / not met appear, they are arithmetic comparisons" in i18n.t(
        "report.statement"
    )
    untimed = i18n.t("report.about.sources.body_untimed", primary="A", second="B")
    assert "each at the time it was received (this source sends no measurement time)" in untimed
    assert "each at its own measurement time" in i18n.t("report.about.sources.body", primary="A", second="B")


@pytest.mark.slow
def test_monthly_summary_heading_names_the_period():
    d = _data(30, True)
    text = _pdf_text(d, True)
    assert i18n.t("report.monthly.title", period="30 Days") in text
    assert all(m["month"] in text for m in d["monthly"])
