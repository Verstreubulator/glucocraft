# License: AGPL-3.0-only (LICENSE).
"""The dashboard and the Documentation: the Custom Ranges page shows where the custom ranges and goals make the
results differ from the consensus ranges; Published Findings without medications; Consensus Ranges without a
duplicated item; the CGM Report's color key; the re-arm rule text; American spelling in the dashboard files."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from conftest import repo_doc
from js_i18n import catalogue, catalogue_text, documentation_section, documentation_text
from js_i18n import text as i18n_text
from node_runner import NODE, node_json

REPO = Path(__file__).resolve().parents[1]
WWW = REPO / "custom_components" / "glucocraft" / "www"
UI = (WWW / "glucocraft-ui.js").read_text(encoding="utf-8")
STRAT = (WWW / "glucocraft-strategy.js").read_text(encoding="utf-8")
STRAT_TEXT = catalogue_text("strategy.")  # the dashboard's English texts (i18n/en/frontend.json)
DOCS = (WWW / "glucocraft-docs.js").read_text(encoding="utf-8")
DOC_TEXT = documentation_text()  # the Documentation's English text (i18n/en/documentation.json)
REPORT = (WWW / "glucocraft-report.js").read_text(encoding="utf-8")
# the Report page's own English texts (report_page.* in i18n/en/frontend.json)
REPORT_PAGE = {k: v for k, v in catalogue("frontend").items() if k.startswith("report_page.")}


def _flat(js: str) -> str:
    return re.sub(r'"\s*\+\s*\n?\s*"', "", js)


def _ui(expr: str) -> object:
    """Evaluate expr inside glucocraft-ui.js (its module-level helpers) with minimal browser stand-ins."""
    if not NODE:
        pytest.skip("node not available")
    stubs = (
        "globalThis.window = globalThis; globalThis.HTMLElement = class {}; const __reg = {};\n"
        "globalThis.customElements = { get: (n) => __reg[n], define: (n, c) => { __reg[n] = c; } };\n"
        "globalThis.ResizeObserver = class { observe() {} disconnect() {} };\n"
    )
    src = stubs + UI + "\nprocess.stdout.write('\\u0001' + JSON.stringify((() => { " + expr + " })()));\n"
    return node_json(src)


CFG = {
    "ranges": {"very_low": 54, "low": 75, "tight_low": 70, "tight_high": 150, "high": 200, "very_high": 250},
    "consensus_ranges": {
        "range_very_low": 54,
        "range_low": 70,
        "range_tight_low": 70,
        "range_tight_high": 140,
        "range_high": 180,
        "range_very_high": 250,
    },
    "goals": {
        "time_in_range": {"op": ">", "value": 70},
        "time_in_tight_range": {"op": ">=", "value": 50},
        "time_low": {"op": "<", "value": 6},
        "time_very_low": {"op": "<", "value": 1},
        "time_high": {"op": "<", "value": 25},
        "time_very_high": {"op": "<", "value": 5},
        "cv": {"op": "<=", "value": 36},
    },
}
CONS = {
    **CFG,
    "ranges": {"very_low": 54, "low": 70, "tight_low": 70, "tight_high": 140, "high": 180, "very_high": 250},
    "goals": {
        "time_in_range": {"op": ">", "value": 70},
        "time_low": {"op": "<", "value": 4},
        "time_very_low": {"op": "<", "value": 1},
        "time_high": {"op": "<", "value": 25},
        "time_very_high": {"op": "<", "value": 5},
        "cv": {"op": "<=", "value": 36},
    },
}


# ------------------------------------------------------------------------------------------------ the limit notes
def test_limit_segments_in_mg_dl():
    out = _ui(
        f"const c = {json.dumps(CFG)}; return [gcLimDiff(c), gcLimitSegments(c, 'low'), "
        "gcLimitSegments(c, 'high'), gcLimitSegments(c, 'tight_high')];"
    )
    assert out[0] == ["low", "tight_high", "high"]
    assert out[1] == ["Readings of 70–74 mg/dL count as low here and as in range on Consensus Ranges."]
    assert out[2] == ["Readings of 181–200 mg/dL count as in range here and as high on Consensus Ranges."]
    assert out[3] == ["Readings of 141–150 mg/dL are in the tight range here and outside it on Consensus Ranges."]


def test_limit_segments_lower_than_consensus_and_across_two_bands():
    c = {**CFG, "ranges": {**CFG["ranges"], "very_low": 50, "low": 60}}
    out = _ui(f"const c = {json.dumps(c)}; return [gcLimitSegments(c, 'very_low'), gcLimitSegments(c, 'low')];")
    assert out[0] == ["Readings of 50–53 mg/dL count as low here and as very low on Consensus Ranges."]
    assert out[1] == ["Readings of 60–69 mg/dL count as in range here and as low on Consensus Ranges."]


def test_limit_segments_in_mmol_l_with_a_decimal_comma():
    c = {**CFG, "ranges": {**CFG["ranges"], "low": 75.7, "high": 200.0, "tight_high": 149.5}}
    out = _ui(
        "GC.mmol = true; GC.dec = ','; const c = "
        + json.dumps(c)
        + "; return [gcLimitSegments(c, 'low'), gcLimitSegments(c, 'tight_high'), gcCustomDiffHtml(c)];"
    )
    assert out[0] == ["Readings of 3,9–4,1 mmol/L count as low here and as in range on Consensus Ranges."]
    assert out[1] == ["Readings of 7,9–8,3 mmol/L are in the tight range here and outside it on Consensus Ranges."]
    assert "below 4,2 mmol/L with the custom ranges; below 3,9 mmol/L with the consensus ranges" in out[2]
    assert "undefined" not in out[2] and "NaN" not in out[2]


# ------------------------------------------------------------------------------------------------ goals
def test_goal_texts():
    out = _ui(
        "return [gcGoalText('time_low', {op: '<', value: 6}), gcGoalText('time_in_range', {op: '>', value: 70}), "
        "gcGoalText('gmi', {op: '<', value: 7}), gcGoalText('time_in_tight_range', {op: '>=', value: 50}), "
        "gcGoalText('time_in_tight_range', undefined)];"
    )
    assert out[0] == "Custom goal: less than 6% · consensus target: less than 4% (Battelino et al., 2019)"
    assert out[1] == "Custom goal: more than 70%, equal to the consensus target (Battelino et al., 2019)"
    assert out[2] == "Custom goal: less than 7.0%; no published target"
    assert out[3] == "Custom goal: at least 50%; no consensus target for clinical care"
    assert out[4] == "No goal set"


# ------------------------------------------------------------------------------------------------ the box at the top
def test_differences_box_with_custom_ranges():
    html = _ui(f"return gcCustomDiffHtml({json.dumps(CFG)});")
    assert "Differences from the consensus ranges are marked on this page." in html
    assert "3 of 6 range limits differ from the consensus ranges" in html
    assert "Readings of 70–74 mg/dL count as low here and as in range on Consensus Ranges." in html
    assert "This changes the time in range and the time below the low limit." in html
    assert "GRI" not in html  # the GRI is on the consensus ranges only
    assert "consensus target: less than 4% (Battelino et al., 2019)" in html
    assert "no consensus target for clinical care" in html
    assert (
        "Mean glucose, standard deviation, coefficient of variation (CV) and glucose management indicator (GMI) do "
        "not depend on the ranges" in html
    )
    assert "undefined" not in html and "NaN" not in html


def test_differences_box_when_equal():
    html = _ui(f"return gcCustomDiffHtml({json.dumps(CONS)});")
    assert "<b>Equal to the consensus ranges.</b>" in html
    assert "Range limits that differ" not in html and "differ from the consensus targets" not in html
    assert _ui("return gcCustomDiffHtml({});") == '<div class="p">Waiting for GlucoCraft…</div>'


def test_a_goal_without_a_consensus_target_is_a_difference():
    # time in tight range and GMI have no consensus target: a goal for either adds a ✓ / ✗ that Consensus Ranges
    # never shows, so the box does not say that every result is equal
    tight = {**CONS, "goals": {**CONS["goals"], "time_in_tight_range": {"op": ">=", "value": 50}}}
    html = _ui(f"return gcCustomDiffHtml({json.dumps(tight)});")
    assert "Equal to the consensus ranges." not in html and "every result on this page" not in html
    assert html.startswith(
        '<div class="p"><b>The range limits are equal to the consensus ranges.</b> 1 custom goal without a consensus '
        "target is set; its ✓ / ✗ is shown on this page and not on <i>Consensus Ranges</i>.</div>"
    )
    assert "Custom goals without a published target" in html
    both = {**tight, "goals": {**tight["goals"], "gmi": {"op": "<", "value": 7}}}
    assert "2 custom goals without a consensus target are set" in _ui(f"return gcCustomDiffHtml({json.dumps(both)});")
    # with a range limit that differs as well (one that no goal with a consensus target depends on): those goals are
    # equal, the others are counted
    limits = {**tight, "ranges": {**CONS["ranges"], "tight_high": 150}}
    html = _ui(f"return gcCustomDiffHtml({json.dumps(limits)});")
    assert "1 of 6 range limits differ from the consensus ranges; the custom goals with a consensus target are " in html
    assert "1 custom goal without a consensus target is set" in html
    assert "the custom goals are equal to the consensus targets" not in html


def test_affected_results_only_range_dependent():
    out = _ui(
        f"const c = {json.dumps(CFG)}; return ['tir', 'titr', 'below', 'very_low', 'above', 'very_high', 'gri', "
        "'mean', 'sd', 'cv', 'gmi'].map((m) => gcAffected(c, m));"
    )
    assert out == [True, True, True, False, True, False, False, False, False, False, False]  # never the GRI
    assert _ui("return gcBoth('6.2%', '4.1%');") == "6.2% with the custom ranges · 4.1% with the consensus ranges"


def test_custom_ranges_page_has_the_box_and_the_context():
    s = _flat(STRAT)
    assert s.count('type: "custom:glucocraft-custom-diff"') == 1
    assert '["glucocraft-custom-diff", GlucoCraftCustomDiff]' in UI
    # the Custom Ranges page's builder: from its sub-page to the Report page's
    body = s[s.index('sub(gcT("strategy.page.custom")') : s.index('sub(gcT("strategy.page.report")')]
    assert "custom:glucocraft-custom-diff" in body
    assert 'gcT("strategy.custom.about", { ...cx' in body
    assert "{cx_cv} {cx_cv_primary} {cx_gmi}" in i18n_text("strategy.custom.about")


# ------------------------------------------------------------------------------------------------ other items
def test_consensus_page_states_other_groups_once():
    s = STRAT_TEXT
    assert "the consensus lists other targets for older or high-risk individuals" not in s
    assert "These targets are those listed for adults" not in s
    assert (
        "(Danne et al., 2017, Section 4). This explains the targets in the rows and tiles of the time below 70 mg/dL"
        in s
    )
    assert s.count("The consensus also publishes different targets for some groups") == 1


def test_published_findings_name_no_medication():
    findings = documentation_section("studies")
    for word in ("insulin", "metformin", "sulfonylurea", "pharmacotherapy", "intensive therapy", "treatment group"):
        assert word not in findings.lower(), word
    assert "{cite:holman2008}" not in findings and "{cite:riddle2021}" not in findings
    assert "{cite:nathan2014|EDIC/Metabolic Memory section, para. 1}" in findings
    left = documentation_section("left-out")
    assert "{cite:holman2008}" in left and "{cite:riddle2021}" in left and "{cite:monnier2017}" in left


def test_report_color_key():
    assert REPORT.count("rColorKey") == 2  # defined once, drawn once at the end of the page
    key = REPORT_PAGE["report_page.color_key"]
    assert key.endswith(" {link}") and 'rDocLink("colors", "report_page.link.colors")' in REPORT
    assert REPORT_PAGE["report_page.link.colors"] == "Documentation > Colors"
    for part in (
        "dark red, very low",
        "red, low",
        "green, in range",
        "light green, tight range",
        "yellow, high",
        "orange, very high",
        "amber, the edge of a note about the data",
    ):
        assert part in key, part
    assert "attention required" not in key  # a color key describes, it does not instruct
    assert 'a === "colors"' in REPORT
    assert "uses the time-in-ranges colors instead" in DOC_TEXT


def test_no_rearm_rule_left():
    """The re-arm rule of the removed glucose notifications is in no dashboard or Documentation text."""
    for text in (STRAT_TEXT, DOC_TEXT, STRAT):
        assert "can be sent again" not in text and "crossing" not in text and "rearm" not in text.lower()


def test_american_spelling():
    for name, text in (
        ("ui", UI),
        ("strategy", STRAT + STRAT_TEXT),
        ("docs", DOCS + DOC_TEXT),
        ("report", REPORT + json.dumps(REPORT_PAGE) + json.dumps(catalogue("report"))),
    ):
        low = text.lower().replace("aria-" + "label" + "ledby", "")  # the ARIA attribute's own name (W3C)
        # the unit texts a source may state, named as they are read (units.GLUCOSE_UNIT_TEXTS)
        low = low.replace("mg/decilitre", "").replace("mmol/litre", "")
        for word in ("label" + "led", "col" + "our", "centred", "decilitre", "litre"):
            assert word not in low, (name, word)
    md = repo_doc("DASHBOARD.md").lower()
    assert "col" + "our" not in md and "label" + "led" not in md and "centred" not in md
