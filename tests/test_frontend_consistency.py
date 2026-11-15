# License: AGPL-3.0-only (LICENSE).
"""The dashboard, the Documentation and the display cards (www/*.js), checked against the Python side:

- the disclaimer: the catalogue's long form (disclaimer.text, const.py DISCLAIMER), shown by the report and the
  Documentation, its short form (disclaimer.short, const.py DISCLAIMER_SHORT) on the display card's About page, and the footer's disclaimer line (const.py DISCLAIMER_LINE); no Terms of Use anywhere;
- the consensus targets of the dashboard cards: one table in glucocraft-ui.js, equal to const.py's;
- the gap rule and the shared helpers are written once, in glucocraft-shared.js, and taken from there by the card
  files that draw graphs; the JavaScript gap rule gives the gaps of gaps.py on the same readings;
- the shared texts and settings: the display colors read from zone_colors with the default palette of const.py, the
  zone word, the custom-ranges statement, the removal sentence, AGPL-3.0-only;
- the wording and citations of the cited texts, LO / HI in the minimum and maximum, and the arithmetic direction of
  a change instead of "toward" / "away".
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from glucocraft import const as C
from glucocraft import gaps

from js_i18n import catalogue_text, documentation_text
from js_i18n import text as i18n_text
from node_runner import needs_node, node_json

REPO = Path(__file__).resolve().parents[1]
WWW = REPO / "custom_components" / "glucocraft" / "www"
FILES = {
    n: (WWW / f"glucocraft-{n}.js").read_text(encoding="utf-8").replace("\r\n", "\n")
    for n in ("display-card", "ui", "docs", "strategy", "report")
}
CARD, UI, DOCS, STRAT = FILES["display-card"], FILES["ui"], FILES["docs"], FILES["strategy"]
SHARED = (WWW / "glucocraft-shared.js").read_text(encoding="utf-8").replace("\r\n", "\n")
STRAT_TEXT = catalogue_text("strategy.")  # the dashboard's English texts (i18n/en/frontend.json)
DOC_TEXT = documentation_text()  # the Documentation's English text (i18n/en/documentation.json)
# each file with the English texts it shows (its catalogue keys), for the wording checks
MINE = {"display-card": CARD + "\n" + catalogue_text("card."), "ui": UI + "\n" + catalogue_text("ui."),
        "docs": DOCS + "\n" + DOC_TEXT, "strategy": STRAT + "\n" + catalogue_text("strategy.")}  # fmt: skip
STUBS = (
    "globalThis.window = globalThis; globalThis.HTMLElement = class { attachShadow() { this.shadowRoot = "
    "{ innerHTML: '' }; return this.shadowRoot; } }; const __reg = {};\n"
    "globalThis.customElements = { get: (n) => __reg[n], define: (n, c) => { __reg[n] = c; } };\n"
    "globalThis.ResizeObserver = class { observe() {} disconnect() {} };\n"
    "globalThis.document = { createElement: () => ({ getContext: () => ({ measureText: (t) => ({ width: t.length * 8 }) }) }) };\n"
    # the shadow root of the Statistics card, which draws into two boxes: html() is the frame with the boxes filled in
    "globalThis.statisticsRoot = () => { const box = {}; return { innerHTML: '', querySelector: (s) => (box[s] ||= "
    "{ innerHTML: '' }), html() { return this.innerHTML.replace(/<div class=\"(lr|pt)\"><\\/div>/g, "
    "(m, b) => `<div class=\"${b}\">${box['.' + b].innerHTML}</div>`); } }; };\n"
)


def _eval(js: str, expr: str) -> object:
    """Evaluate expr after the module source js (its top-level constants and functions in scope)."""
    src = STUBS + js + "\nprocess.stdout.write('\\u0001' + JSON.stringify((() => { " + expr + " })()));\n"
    return node_json(src)


def _flat(js: str) -> str:
    return re.sub(r'"\s*\+\s*\n?\s*"', "", js)


# ------------------------------------------------------------------------------------------------ the disclaimer
def test_the_disclaimer_is_one_catalogue_text_shown_by_the_cards():
    """The disclaimer's long form is the catalogue's disclaimer.text (const.py DISCLAIMER, its lead in bold), read by the
    report page and the Documentation; its short form, disclaimer.short (const.py DISCLAIMER_SHORT), by the display
    card's About page, as on the panels; the footer of every page carries the disclaimer line with its link."""
    assert re.sub(r"</?b>", "", i18n_text("disclaimer.text")) == C.DISCLAIMER_PLAIN
    assert re.sub(r"</?b>", "", i18n_text("disclaimer.short")) == C.DISCLAIMER_SHORT_PLAIN
    for name in ("report", "docs"):
        assert 'gcT("disclaimer.text")' in FILES[name] or '"disclaimer.text"' in FILES[name], name
    assert 'gcT("disclaimer.short")' in FILES["display-card"] and '"disclaimer.text"' not in FILES["display-card"]
    assert i18n_text("strategy.footer.disclaimer") == C.DISCLAIMER_LINE + " [Disclaimer]({url})"
    assert 'gcT("strategy.footer.disclaimer", { url: `${DOCS_LINK}#disclaimer` })' in STRAT
    for name, js in FILES.items():
        body = js.split("\n", 1)[1]  # after the license line
        assert not re.search(r"TERMS_|gcTerms|terms\.(in_brief|short_form)|page-notice|terms-notice", body), name
    assert "translated(" not in SHARED and "gcTermsCourtesy" not in SHARED


# The version constant of each card file and the README's Versions row that lists it.
FILE_VERSIONS = {
    "strategy": ("GC_STRATEGY_VERSION", "Dashboard strategy"),
    "ui": ("GCUI_VERSION", "Dashboard cards"),
    "display-card": ("GCDC_VERSION", "Display card"),
    "docs": ("GCDOCS_VERSION", "Documentation page"),
    "report": ("GCREPORT_VERSION", "Report page"),
}


def readme_versions() -> dict[str, str]:
    """Return the README's Versions table as {part: version}."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    return dict(re.findall(r"^\| ([^|]+?) \| (\d+\.\d+(?:\.\d+)?) \|", readme, re.MULTILINE))


def test_frontend_versions_are_well_formed_and_consistent():
    """Each card file declares its version once as MAJOR.MINOR.PATCH and logs that constant; the README agrees."""
    listed = readme_versions()
    manifest = json.loads((REPO / "custom_components" / "glucocraft" / "manifest.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert listed["Integration"] == manifest["version"]
    for name, (key, row) in FILE_VERSIONS.items():
        found = re.findall(rf'\bconst {key} = "([^"]*)"', FILES[name])
        assert len(found) == 1, (name, key)
        assert re.fullmatch(r"\d+\.\d+\.\d+", found[0]), (name, found[0])
        assert f"v${{{key}}}" in FILES[name], (name, "the console line logs the constant")
        assert listed[row] == found[0], (name, row)
    assert "Terms of Use" not in listed


@needs_node
def test_the_consensus_targets_of_the_dashboard_cards_are_one_table_equal_to_const():
    """glucocraft-shared.js writes the consensus targets once (GC_CONS_GOAL, [op, value, source] by goal key); the
    statistics tables, Change Over Time and the 14-day check of glucocraft-ui.js and the CGM Report page read that
    table. The table equals const.py CONSENSUS_TARGET_VALUES with the operators of GOAL_OPS."""
    table = _eval(UI, "return GC_CONS_GOAL;")
    assert {k: (op, value) for k, (op, value, _source) in table.items()} == {
        k: (C.GOAL_OPS[k], v) for k, v in C.CONSENSUS_TARGET_VALUES.items()
    }
    assert all(source.startswith("Battelino et al., 2019") for _op, _value, source in table.values())
    assert SHARED.count("const GC_CONS_GOAL = Object.freeze({") == 1 and "const GC_CONS_GOAL" not in UI
    # no target written a second time: an [op, value] literal of a consensus target outside the table
    rest = UI
    for k, v in C.CONSENSUS_TARGET_VALUES.items():
        assert not re.search(rf'\["{re.escape(C.GOAL_OPS[k])}", {v}\b', rest), (k, v)
    assert rest.count('gcConsTarget("') + rest.count("GC_CONS_GOAL.") >= 3 * len(C.CONSENSUS_TARGET_VALUES)
    # the CGM Report page writes its targets from the same table ("<4%"), never as literals of its own
    assert not re.search(r'["`>](?:&lt;|&gt;|<|>)\d+%', FILES["report"]) and "rTarget(" in FILES["report"]


# ------------------------------------------------------------------------------------------------ the shared helpers
def test_gap_rule_and_helpers_are_one_implementation():
    """The gap rule and the helpers are written once, in glucocraft-shared.js (tests/test_card_shared_copies.py checks
    every shared rule); the card files that draw graphs take them from there."""
    assert SHARED.count("const GAP_FLOOR_S = 900, GAP_FACTOR = 3, GAP_WINDOW = 10, GAP_MIN_IV = 3;") == 1
    for name, js in FILES.items():
        assert "GAP_FLOOR_S" not in js, name  # the report page uses the backend's daily_breaks
    for js in (CARD, UI):
        assert "gcGaps, gcGapIn, gcRunIn, first, gcRangeAreas" in js.split("} = globalThis.glucocraftShared;", 1)[0]
        assert "const gcRunIn" not in js  # the line's break across a stretch of limit readings: the shared one
    assert SHARED.count("const gcRunIn = ") == 1
    # one name for the decimal-separator setting: the shared settings' dec
    assert "GC_DS" not in STRAT and "GC_DEC" not in STRAT and "dtx } = globalThis.glucocraftShared;" in STRAT


@needs_node
@pytest.mark.parametrize(
    "ts, fallback",
    [
        ([0, 60, 120, 180, 1200, 1260, 1320, 1380], 60),  # 17 min between 1-minute readings
        ([0, 900, 1800, 2700, 3600, 4500, 9000, 9900], 900),  # 15-minute history, one 75-min hole
        ([0, 300, 600, 900, 1200, 2400, 2700, 3000, 3300, 3600], 300),  # 20 min after 5-minute readings
        ([0, 60, 2000], 60),  # too few intervals: the fallback
    ],
)
def test_javascript_gap_rule_gives_the_gaps_of_gaps_py(ts, fallback):
    iv = gaps.local_intervals(ts, fallback)
    expected = [[ts[i], ts[i + 1]] for i in range(len(ts) - 1) if ts[i + 1] - ts[i] > gaps.gap_threshold(iv[i])]
    for js in (CARD, UI):
        assert _eval(js, f"return gcGaps({json.dumps(ts)}, {gaps.gap_threshold(fallback)});") == expected


# ------------------------------------------------------------------------------------------------ display colors
@needs_node
def test_display_colors_come_from_zone_colors_with_the_default_palette():
    default = {k: v.upper() for k, v in C.ZONE_COLORS.items()}
    assert _eval(UI, "return gcZoneColors(undefined);") == default
    assert _eval(UI, "return gcZoneColors({ low: '#123456', high: 'blue' });") == {**default, "low": "#123456"}
    card = _eval(
        CARD,
        "return Object.fromEntries(Object.entries(gcZoneColorNumbers({ high: '#00ff00' })).map(([k, v]) => [k, hex(v).toUpperCase()]));",
    )
    assert card == {**default, "high": "#00FF00"}
    # no other palette is drawn: the bands use the colors in use (data-band names each band's zone)
    for js in (CARD, UI):
        assert '#EF5350", high' not in js and 'high: "#EF5350"' not in js
    assert 'data-band="${zone}"' in CARD
    assert "blue denotes low glucose only" not in MINE["docs"]


@needs_node
def test_status_bar_text_reads_on_every_status_color():
    on = _eval(CARD, "return [0xF9A825, 0x43A047, 0xE53935, 0x9E9E9E, 0x7E57C2].map((c) => onColor(c));")
    assert on == [0, 0, 0, 0, 0xFFFFFF]  # black on amber, green, red, gray; white on purple


def test_zone_word_on_the_cards():
    # a word for every zone, in the trend word's place and size, from zone_label
    words = {"very_low": "Very low", "low": "Low", "near_low": "In range", "tight_range": "Tight range",
             "near_high": "In range", "high": "High", "very_high": "Very high"}  # fmt: skip
    for zone, word in words.items():
        assert f'{zone}: ["card.zone.{zone}", "card.zone_in_sentence.{zone}"]' in CARD
        assert i18n_text(f"card.zone.{zone}") == word and i18n_text(f"card.zone_in_sentence.{zone}") == word.lower()
    assert "cuS?.attributes?.zone_label" in CARD
    # the trend word's place and size: square and rectangular 40 px
    assert "square: { y: 216, size: 40 }" in CARD and "wide: { y: 264, size: 40 }" in CARD
    assert (
        "this._zoneSlot(s, gv, MID_X, ZONE_SLOT.square" in CARD and "this._zoneSlot(s, gv, cx, ZONE_SLOT.wide" in CARD
    )
    assert "VERY LOW" not in CARD and "TIGHT RANGE" not in CARD  # sentence case, as the panels


# ------------------------------------------------------------------------------------------------ shared wording
def test_custom_ranges_statement_everywhere():
    statement = "Ranges and goals that differ from them are custom settings."
    for name, js in MINE.items():
        assert "Consult your healthcare team" not in js, name
    assert statement in STRAT_TEXT and statement in DOC_TEXT
    assert "The defaults are the international consensus ranges and targets (Battelino et al., 2019)." in STRAT_TEXT


def test_no_display_comparison_in_the_frontend():
    """No text compares displays or praises one: the two cards (square and rectangular) describe what they show."""
    for name, js in MINE.items():
        flat = _flat(js)
        assert "well suited" not in flat and "Backlit LCD" not in flat and "backlit LCD" not in flat, name
    assert set(re.findall(r'customElements\.define\("([\w-]+)"', CARD)) == {
        "glucocraft-display-card",
        "glucocraft-display-card-wide",
    }


def test_removal_license_and_third_party_notices_in_the_documentation():
    d = DOC_TEXT
    assert (
        "<b>Removing GlucoCraft</b>" in d
        and "With <i>Delete everything</i>, deleting GlucoCraft removes every file GlucoCraft stored" in d
        and "<i>Keep the readings for a reinstall</i> (the default)" in d
        and "<code>glucocraft.import_csv</code>" in d
        and "recorded history and long-term statistics of the entities it has at that moment" in d
        and "leaves no trace" not in d  # the history of an optional feature turned off earlier is not removed
    )
    assert (
        "or (at your option) any later version" not in d and "Free Software Foundation, version 3 of the License." in d
    )
    assert "Material Design Icons" in d and "ReportLab" in d and "<b>Liberation Sans</b> 2.1.5" in d
    assert "AGPL-3.0-only" in STRAT_TEXT


# ------------------------------------------------------------------------------------------------ retired wording
def test_retired_and_misleading_wording_is_gone():
    gone = {  # why each phrase is wrong: the phrases
        "an instruction in a description": ["attention required", "needs attention"],
        "toward / away instead of the arithmetic direction": [
            "Toward the consensus target",
            "Away from the consensus target",
            "toward or away from",
        ],
        "an abbreviated band name": ["Crit Lo", "Crit Hi"],
        "a GlucoCraft-made score named as a risk assessment": ["Custom Risk Score", "risk score"],
        "a medical icon, and a setting that does not exist": ["mdi:heart-pulse", "Stale data threshold"],
        "an organization named without need": ["International Diabetes Center's guide"],
        "firmware instead of example panel configurations": [
            "GlucoCraft panel firmware",
            "with GlucoCraft panel firmware",
        ],
        "a mmol/L value attributed to a source that gives mg/dL only": ["(0.11 mmol/L) per minute"],
        "a product named without need": ["SiBionics"],
        "another word for the Glossary term sensor active": ["70% sensor data", "70% data"],
        "a platform named without need": ["(Android)"],
    }
    for finding, phrases in gone.items():
        for name, js in MINE.items():
            for phrase in phrases:
                assert phrase not in _flat(js), (finding, name, phrase)


def test_cited_wording_of_the_findings():
    s, d = STRAT_TEXT, DOC_TEXT
    assert "is appropriate for many nonpregnant adults using CGM" in s
    assert "a published guide to the standardized report rounds each 1% to about 15 minutes" in s
    # the 70% rule of every period: one note per period, with the time still needed (a GlucoCraft rule; the
    # Documentation cites Battelino et al., 2019, for its 14-day origin)
    assert "once the sensor has covered at least {percent} of it." in i18n_text("ui.enough_data.rule")
    assert "GlucoCraft is designed to return to" in i18n_text("ui.banner.second_source.text")
    assert "provides these alarms" not in d  # no paragraph about the regulators
    assert "clinically meaningful for an individual trial participant" in d
    assert "without severe hypoglycemia or frequent hypoglycemia affecting health or quality of life" in d
    assert "{cite:battelino2023|p. 49}" in d
    assert DOCS.count("Neil, H. A. W.") == 2  # two reference entries
    assert d.count("show the words <i>No current data</i> in place of the glucose value, never a number") == 1
    assert "is designed to supply readings that the primary source misses, up to 24 hours back" in d


@needs_node
def test_two_works_of_one_author_in_one_citation():
    out = _eval(DOCS, 'return C("ada2026", "ada2026b");')
    assert (
        re.sub(r"<[^>]+>", "", out)
        == "(American Diabetes Association Professional Practice Committee for Diabetes, 2026a, 2026b)"
    )


@needs_node
def test_change_over_time_shows_the_arithmetic_direction_only():
    js = (
        UI
        + """
    const t = new __reg["glucocraft-change"](); t.setConfig({ basis: "consensus" });
    t.attachShadow = () => {}; t.shadowRoot = { innerHTML: "" };
    const cmp = { last_4_weeks: { tir: 80, active: 90 }, weeks_9_13_ago: { tir: 78 }, weeks_22_26_ago: { tir: 75 } };
    t._hass = { states: { "sensor.glucocraft_change_over_time": { attributes: { consensus: { compare: cmp, weeks14: [{ tir: 80, active: 90, active_ok: true }] } } } } };
    t.draw();
    """
    )
    out = _eval(js, "return t.shadowRoot.innerHTML;")
    assert "Higher than 22–26 weeks ago" in out and "Toward" not in out and "Away" not in out
    # the mark is shown and its word read (the symbol hidden from screen readers); a difference in percentage points
    assert ">Direction</th>" in out and '<span aria-hidden="true">✓</span> meets' in out
    assert "+5.0 percentage points" in out


@needs_node
def test_statistics_last_reading_block_while_there_is_no_current_data():
    js = (
        UI
        + """
    const c = new __reg["glucocraft-statistics"](); c.setConfig({}); c.shadowRoot = statisticsRoot();
    const at = new Date(Date.now() - 7 * 3600e3).toISOString();
    c._hass = { states: {
      "sensor.glucocraft_status": { state: "no_current_data", attributes: {} },
      "binary_sensor.glucocraft_no_current_data": { state: "on", attributes: {} },
      "sensor.glucocraft_last_reading_time": { state: at, attributes: {} },
      "sensor.glucocraft_last_glucose_reading": { state: "112", attributes: { zone: "tight_range" } },
      "sensor.glucocraft_7d_consensus_ranges": { attributes: { has_data: true, min_value: 58, max_value: 250,
        limit_readings: { low: 2, high: 0 }, limit_seconds: { low: 600, high: 0 } } } } };
    c.draw();
    """
    )
    out = _eval(js, "return c.shadowRoot.html();")
    # the block's title is a heading (WCAG 1.3.1)
    assert '<h3 class="h" style="margin-top:0">Last Reading</h3>' in out
    assert ">Current</div>" not in out and "Current values" not in out
    assert "No current data since" in out and "measured at" in out
    assert "Smoothed Glucose" not in out and "Rate of Change" not in out  # values GlucoCraft does not publish
    assert "No current data. Last reading 7 hours ago. Glucose 112 milligrams per deciliter." in out
    assert '<td style=""><b>58</b></td>' in out and '<td style=""><b>250</b></td>' in out


@needs_node
def test_the_dashboard_readouts_test_the_flag_not_the_status():
    """An expired sensor that still delivers: the status is "expired", the no-current-data flag is off, so the Last
    Reading block and the banners show no *No current data*; an old reading after the stated expiry shows both
    banners."""
    js = (
        UI
        + """
    const at = (h) => new Date(Date.now() - h * 3600e3).toISOString();
    const states = (flag, age) => ({
      "sensor.glucocraft_status": { state: "expired", attributes: {} },
      "binary_sensor.glucocraft_no_current_data": { state: flag, attributes: {} },
      "sensor.glucocraft_last_reading_time": { state: at(age), attributes: {} },
      "sensor.glucocraft_sensor_expiry": { state: "expired", attributes: { expires_at: at(3) } },
      "sensor.glucocraft_last_glucose_reading": { state: "112", attributes: { zone: "tight_range" } } });
    const s = new __reg["glucocraft-statistics"](); s.setConfig({}); s.shadowRoot = statisticsRoot();
    s._hass = { states: states("off", 0.02) }; s.draw();
    const fresh = s.shadowRoot.html();
    s._hass = { states: states("on", 2) }; s.draw();
    const old = s.shadowRoot.html();
    """
    )
    out = _eval(js, "return { fresh, old };")
    assert "No current data" not in out["fresh"] and "No current data since" in out["old"]
    assert "Glucose 112 milligrams per deciliter, tight range." in out["fresh"]


@needs_node
def test_change_over_time_lowest_and_highest_are_measured_numbers():
    js = (
        UI
        + """
    const c = new __reg["glucocraft-change-chart"](); c.setConfig({ metric: "min", basis: "consensus" });
    const w = (lo, hi) => ({ min: lo, max: hi });
    c._hass = { states: { "sensor.glucocraft_change_over_time": { attributes: { consensus: {
      weeks14: [w(52, 300), w(60, 280)], weeks7: [], week_ends: [] } } } } };
    c.draw();
    """
    )
    out = _eval(js, "return c.shadowRoot.innerHTML;")
    assert "lowest 52 mg/dL · highest 300 mg/dL" in out and "LO" not in out.replace("LOW", "")


def test_gri_zones_are_quintiles_and_the_value_mapping_a_glucocraft_rule():
    docs = _flat(MINE["docs"])
    assert "zones A (0–20) to E (81–100)" not in docs
    assert "quintiles" in docs and "(GlucoCraft rule)" in docs
    # the dashboard cards show no GRI zone: they name the CGM Report, which shows it
    ui = _flat(MINE["ui"])
    assert "zones A (0–20) to E (81–100)" not in ui and "quintiles" not in ui
    assert "GRI is shown for periods of 14 days or more; shorter periods show ---." in ui
    assert "The CGM Report shows the GRI with its zone." in ui


@needs_node
def test_the_display_card_shows_a_periods_extremes_as_numbers():
    """A reading at the sensor's limit is no value: a measured number, or --- for a period without one, whatever a
    period sensor says."""
    js = (
        CARD
        + """
    const c = new __reg["glucocraft-display-card"]();
    c._hass = { states: { "sensor.glucocraft_24h_consensus_ranges": { attributes: { limit_readings: { low: 2, high: 0 },
      limit_seconds: { low: 600, high: 0 } } } } };
    """
    )
    assert _eval(js, "return c._minMax(58, 300);") == ["58", "300"]
    assert _eval(js, "return c._minMax(0, 0);") == ["---", "---"]
    assert _eval(js, "return c._minMax(52, 380, [false, false]);") == ["52", "380"]
    assert _eval(js, "return c._minMax(40, 300);") == ["40", "300"]  # a file without the trailer: no limit reading
