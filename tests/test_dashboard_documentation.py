# License: AGPL-3.0-only (LICENSE).
"""The dashboard and the Documentation: the cited-context texts on the dashboard pages
and in the Documentation, the section "What GlucoCraft Leaves Out, and Why", the color key at the bottom of each page
that uses colors (exactly the colors each page draws), the Custom Ranges headline counted with the 14-day / 70% rule,
its one About This Page, the data-quality warning on Today and Custom Ranges, Changing Settings naming every setting
of the Report and dashboard screen, the sensor-active label and DOI-only references."""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

import pytest

from js_i18n import catalogue, catalogue_text, documentation_section, documentation_text
from node_runner import NODE, run_node

REPO = Path(__file__).resolve().parents[1]
WWW = REPO / "custom_components" / "glucocraft" / "www"
DOCS = (WWW / "glucocraft-docs.js").read_text(encoding="utf-8")
DOC_TEXT = documentation_text()  # the Documentation's English text (i18n/en/documentation.json)
UI = (WWW / "glucocraft-ui.js").read_text(encoding="utf-8")
STRAT = (WWW / "glucocraft-strategy.js").read_text(encoding="utf-8")
STRAT_TEXT = catalogue_text("strategy.")  # the dashboard's English texts (i18n/en/frontend.json)
FE = catalogue("frontend")
SENTENCE = "Close to, but not the same as, the CGM system's own figure: only the readings received are counted."


def _flat(js: str) -> str:
    """The JavaScript string literals joined (\"a\" + \"b\" -> ab), for phrase checks across line breaks."""
    return re.sub(r'"\s*\+\s*\n?\s*"', "", js)


@pytest.fixture(scope="module")
def dashboard() -> dict:
    if not NODE:
        pytest.skip("node not available")
    states = {
        "sensor.glucocraft_configuration": {
            "state": "ok",
            "attributes": {
                "unit": "mg/dL",
                "ranges": {
                    "very_low": 54,
                    "low": 70,
                    "tight_low": 70,
                    "tight_high": 140,
                    "high": 180,
                    "very_high": 250,
                },
                "decimal_separator": ",",
            },
        }
    }
    script = textwrap.dedent(f"""
        globalThis.HTMLElement = class {{}}; const reg = {{}};
        globalThis.customElements = {{ get: (n) => reg[n], define: (n, c) => {{ reg[n] = c; }} }};
        globalThis.location = {{ pathname: "/glucocraft-home/start" }};
        await import({json.dumps((WWW / "glucocraft-strategy.js").as_uri())});
        const d = await reg["ll-strategy-dashboard-glucocraft"].generate({{}}, {{ states: {json.dumps(states)},
          devices: {{}}, entities: {{}} }});
        process.stdout.write(JSON.stringify(d));
    """)
    out = run_node(script)  # the strategy's console line, then the dashboard
    return json.loads(out[out.index('{"title"') :])


def _view(dash: dict, path: str) -> str:
    return json.dumps(next(v for v in dash["views"] if v["path"] == path), ensure_ascii=False)


# ------------------------------------------------------------------------------------------------ cited context
def test_cited_context_texts_on_the_consensus_page_and_in_the_documentation():
    s, d = STRAT_TEXT, DOC_TEXT
    # 1.3 Other groups: once in the Documentation and once on Consensus Ranges, with the DOI link
    assert s.count("The consensus also publishes different targets for some groups") == 1
    assert d.count("The consensus also publishes different targets for some groups") == 1
    assert "(https://doi.org/10.2337/dci19-0028)" in s and 'href="https://doi.org/10.2337/dci19-0028"' in d
    # the cited-context sentences, each on the dashboard and in the Documentation
    for phrase in (
        "which are less than 6 hours and less than 1 hour and 12 minutes per day",
        "as a secondary target range",
        "two conditions for valid CGM data",
        "a value it takes from",
        "key secondary glycemic variability measure",
        "so a CV of exactly 36% meets the 2019 target",
        "calculated value approximating A1C",
        "at least 10 and preferably 14 or more days of CGM data",
        "derived from 528 people in four randomized trials",
        "should be calculated and displayed separately",
        "not a substitute for looking at individual metrics",
        "threshold for adrenergic responses",
        "names the glucose ranges of the standardized report",
        "describes trend arrows that show the direction in which glucose is heading",
    ):
        assert phrase in s, phrase
        assert phrase in d, phrase
    assert "each incremental 5% increase in TIR is associated with" in s
    assert "clinically meaningful for an individual participant (Battelino et al., 2023, Panel 4)" in s
    assert "should remain available for individualized review" in s
    assert "{ada_b}, Recommendation 7.9)" in s and 'Professional Practice Committee for Diabetes, 2026b";' in STRAT


def test_superseded_wording_is_gone():
    # no tight-range aim for clinical trials, and the ‡ rule is attributed to the 2019 consensus, its source
    assert "suggest an aim above 70% for clinical trials" not in UI
    assert "the size the consensus considers meaningful for an individual" not in UI + STRAT + STRAT_TEXT
    # the Documentation names no lower CV target for people who take insulin or sulfonylureas
    assert "CV below 33%" not in DOC_TEXT and "sulfonylureas " not in DOC_TEXT
    # the targets of other groups are not quoted
    assert "more than 50% time in range" not in DOC_TEXT and "63–140 mg/dL" not in DOC_TEXT
    # no medication in the GRI population sentence
    assert "insulin-treated" not in DOC_TEXT


def test_the_leaves_out_section():
    assert '["left-out", "mdi:filter-remove-outline"]' in DOCS
    assert DOC_TEXT.count("What GlucoCraft Leaves Out, and Why\n") == 1  # the section title
    body = documentation_section("left-out")
    for cat in (
        "Treatment recommendations.",
        "Targets for specific groups.",
        "Device and laboratory accuracy.",
        "Findings that cannot be stated without naming medications or substances.",
    ):
        assert cat in body, cat
    assert "their content is not restated here" in body
    assert not re.search(r"\d+(\.\d+)?%", body)  # no excluded target or outcome figure restated


# ------------------------------------------------------------------------------------------------ references
def test_references_are_doi_only_and_ada_2026_has_letters():
    assert "pubmed.ncbi.nlm.nih.gov" not in DOCS
    assert '"American Diabetes Association Professional Practice Committee for Diabetes, 2026a"' in DOCS
    assert '(2026b). 7. Diabetes technology: " +' in DOCS and '"10.2337/dc26-S007"' in DOCS
    assert "{cite:ada2026b}" in DOC_TEXT


# ------------------------------------------------------------------------------------------------ the pages
def test_color_key_only_on_pages_with_colors(dashboard):
    for path in ("start", "today", "custom", "consensus", "maintenance"):
        assert _view(dashboard, path).count("documentation#colors") == 1, path
    for path in ("settings", "documentation", "report"):
        assert "documentation#colors" not in _view(dashboard, path), path
    # the Documentation opens its Colors table for #colors
    assert 'id="colors"' in documentation_section("ranges") and 'colors: "ranges"' in DOCS
    assert "gray, no target" in STRAT_TEXT
    # Today draws no chart without a target (no neutral gray) and no Change Over Time chart (no green / red side)
    today = _view(dashboard, "today").split("*Colors:*")[1].split("](")[0]
    assert "no target" not in today and "chart backgrounds" not in today and "light gray" in today


def _key(dash: dict, path: str) -> str:
    """The items of a page's color key."""
    return _view(dash, path).split("*Colors:* ")[1].split(". [Documentation")[0]


# each page's color key: the colors its cards draw (the cards rendered in every status), no other
COLOR_KEYS = {
    "start": [
        "glucose",
        "lohi",
        "light_gray",
        "green_start",
        "red_start",
        "target_side",
        "amber_start",
        "purple_status",
        "gray_start",
    ],
    "today": ["bands", "lohi", "light_gray", "green", "red_status", "amber", "purple"],
    "custom": [
        "bands",
        "lohi",
        "light_gray",
        "green",
        "red_status",
        "target_side",
        "faint_dots",
        "amber",
        "purple",
        "gray",
    ],
    "consensus": ["lohi", "green", "red_status", "target_side", "faint_dots", "amber", "purple", "gray"],
    "maintenance": ["data_green", "data_red", "data_amber", "purple_status", "gray_status"],
}


def test_each_color_key_lists_the_colors_its_page_draws(dashboard):
    for path, items in COLOR_KEYS.items():
        texts = [FE[f"strategy.colors.{item}"] for item in items]
        assert _key(dashboard, path) == "; ".join(texts), path
    # the red banners (no current data, sensor expired) are drawn on every page with a key
    for path in COLOR_KEYS:
        assert "no current data" in _key(dashboard, path), path
    # the zone colors only where a glucose value is drawn in them (the display card on Start, LO / HI on Consensus
    # Ranges, the LO / HI marks on the graphs of Start, Today and Custom Ranges); Today and Custom Ranges draw the graph
    # bands, no glucose value
    assert "zone word" in _key(dashboard, "start") and "zone word" not in _key(dashboard, "today")
    for path in ("start", "today", "custom", "consensus"):
        assert "(LO / HI" in _key(dashboard, path), path
    # the faint 7-day dots of the Change Over Time charts are named where those charts are drawn
    for path in ("custom", "consensus"):
        assert "7-day value" in _key(dashboard, path), path
    # green and red as chart backgrounds are named as such, not as a result, wherever such a chart is drawn
    for path in ("start", "custom", "consensus"):
        assert "(not a result)" in _key(dashboard, path), path


def test_custom_ranges_has_one_about_this_page(dashboard):
    view = _view(dashboard, "custom")
    assert view.count('"heading": "About This Page"') == 1
    assert '"heading": "Ranges and Goals in Use"' in view


def test_changing_settings_names_every_setting_of_the_report_screen(dashboard):
    steps = catalogue_text("strategy.settings.changing.steps")
    strings = json.loads((REPO / "custom_components" / "glucocraft" / "strings.json").read_text(encoding="utf-8"))
    for label in strings["options"]["step"]["report"]["data"].values():
        assert label.lower() in steps.lower(), label
    assert "When GlucoCraft is deleted" in _view(dashboard, "settings")


def test_sensor_active_label_and_sentence_once_per_page(dashboard):
    for path in ("today", "custom", "consensus"):
        v = _view(dashboard, path)
        assert v.count("Sensor active (data received by GlucoCraft)") == 1, path
        assert v.count(SENTENCE) == 1, path
    assert catalogue_text("ui.label.sensor_active") == "Sensor active (data received by GlucoCraft)"
    assert 'gcT("ui.label.sensor_active")' in UI
    assert DOC_TEXT.count(SENTENCE) == 1 and DOC_TEXT.count("{sensor_active_note}") == 1


def test_no_undefined_and_dois_keep_their_point_with_a_decimal_comma(dashboard):
    blob = json.dumps(dashboard, ensure_ascii=False)
    assert "undefined" not in blob and "NaN" not in blob
    assert "10.2337/dci19-0028" in blob and "10,2337" not in blob


def test_data_warning_on_today_and_custom_ranges():
    assert "const gcDataWarnings = " in UI
    assert UI.count("gcDataWarnings(") == 2  # the statistics table and the Statistics card
    assert 'warn_periods: ["30d", "90d", "180d"]' in STRAT


def test_goals_met_counted_with_the_14_day_70_percent_rule():
    if not NODE:
        pytest.skip("node not available")
    fn = re.search(r"function gcGoalsMet\(.*?\n}\n", UI, re.DOTALL).group(0)
    script = (
        "const isNum = (v) => typeof v === 'number' && isFinite(v);\n"
        "const { gcMeets } = globalThis.glucocraftShared;\n"  # the comparison, from glucocraft-shared.js
        + fn
        + "const g = {time_in_range: {op: '>', value: 70}, cv: {op: '<=', value: 36}, gmi: {op: '<', value: 7}};\n"
        "const v = {time_in_range: 80, cv: 40, gmi: NaN};\n"
        # enough: at least 70% sensor active in the 14 days, compared before rounding (gcActiveOk)
        "process.stdout.write(JSON.stringify([gcGoalsMet(g, v, true), gcGoalsMet(g, v, false), gcGoalsMet({}, v, true)]));"
    )
    # (the last line: the modules' version lines come first)
    assert json.loads(run_node(script).splitlines()[-1]) == [[1, 2, 3], [0, 0, 3], [0, 0, 0]]
    # the headline: "N of M goals met", or not compared below 70% sensor active
    assert 'n ? gcT("ui.readout.personal.goals_met", { count: n, met })' in UI
    assert catalogue_text("ui.readout.personal.goals_met.other") == "{met} of {count} goals met"
    assert (
        catalogue_text("ui.readout.personal.goals_not_compared")
        == "Custom goals: not compared (less than 70% sensor active)"
    )
