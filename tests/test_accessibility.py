# License: AGPL-3.0-only (LICENSE).
"""Accessibility of the dashboard and the display cards (docs/ACCESSIBILITY.md, rules 1, 4 and 5).

- Rule 4, spoken units: one shared set of functions (glucocraft-shared.js) turns a value and its unit into spoken
  text for every card and page, and every row of the rule-4 table is checked.
- Rule 4, reading order: the display card's text description reads the data status (only when there is a problem),
  the value with its unit written out and the zone word, the time of the reading, the trend, then one sentence on the
  page shown; every chart carries a one-sentence description; key results are real tables.
- Rule 4, the page footer: every page ends with the disclaimer line and its link; no copy of the disclaimer is hidden
  from screen readers.
- Rule 5, keyboard: the display card's page buttons and glucose block, the pages card, the readout cards and the
  Documentation's links and buttons can be reached and used with the keyboard, with a visible focus outline.
- Rule 4, comparison signs: "<", ">", "≤" and "≥" before a number are spoken in words in the cards, the table cells,
  the notifications, the settings error and the PDF (its /ActualText); the PDF's headings are its bookmarks.
- Rule 5, focus and messages (in a small DOM, FAKE_DOM): the focus stays on its control when a card or the CGM Report
  page is drawn again; the links have their addresses; the Report page's choice buttons carry aria-pressed; its
  messages, the banners' message and the display card's page change go to live regions that exist from the start; the
  Documentation's sections are headings with a disclosure button, and its links move the focus to their targets.
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

import pytest

from js_i18n import text as i18n_text
from node_runner import NODE, needs_node, node_json

REPO = Path(__file__).resolve().parents[1]
WWW = REPO / "custom_components" / "glucocraft" / "www"
CARD = (WWW / "glucocraft-display-card.js").read_text(encoding="utf-8")
UI = (WWW / "glucocraft-ui.js").read_text(encoding="utf-8")
DOCS = (WWW / "glucocraft-docs.js").read_text(encoding="utf-8")
STRAT = (WWW / "glucocraft-strategy.js").read_text(encoding="utf-8")
SHARED = (WWW / "glucocraft-shared.js").read_text(encoding="utf-8")
# The spoken-text functions of glucocraft-shared.js, as a card file takes them
SPEECH = (
    "const { gcSpeak, gcSpeakA1c, gcSpeakAge, gcSpeakNumber, gcSpeakTrend, gcSpeakLimit, gcSpeakText } = "
    "globalThis.glucocraftShared;\n"
)


def _speak(expr: str) -> object:
    """Evaluate expr with the shared spoken-text functions (glucocraft-shared.js) and the English texts."""
    return node_json(SPEECH + "\nprocess.stdout.write('\\u0001' + JSON.stringify((() => { " + expr + " })()));\n")


# ------------------------------------------------------------------------------------------------ rule 4: units
def test_one_shared_spoken_text_block():
    """The spoken text is written once, in glucocraft-shared.js; the cards and pages that speak take it from there."""
    assert SHARED.count("const GC_SPOKEN_UNIT_KEYS = {") == 1 and SHARED.count("const gcSpeakText = ") == 1
    report = (WWW / "glucocraft-report.js").read_text(encoding="utf-8")
    for text in (CARD, UI, report):
        assert "GC_SPOKEN_UNIT_KEYS" not in text and "const gcSpeakText" not in text
        assert "gcSpeakText" in text.split("} = globalThis.glucocraftShared;", 1)[0]


@needs_node
@pytest.mark.parametrize(
    "expr, spoken",
    [
        # mg/dL and mmol/L (both glucose units, both decimal separators)
        ('gcSpeak(62, "mg/dL")', "62 milligrams per deciliter"),
        ('gcSpeak(1, "mg/dL")', "1 milligram per deciliter"),
        ('gcSpeak(3.4, "mmol/L", { d: 1 })', "3.4 millimoles per liter"),
        ('gcSpeak(3.4, "mmol/L", { d: 1, dec: "," })', "3,4 millimoles per liter"),
        # the rates per minute
        ('gcSpeak(1.5, "mg/dL/min", { d: 1 })', "1.5 milligrams per deciliter per minute"),
        ('gcSpeak(0.08, "mmol/L/min", { d: 2, dec: "," })', "0,08 millimoles per liter per minute"),
        # percent (time in range, CV, sensor active)
        ('gcSpeak(71, "%")', "71 percent"),
        ('gcSpeak(70.5, "%", { d: 1, dec: "," })', "70,5 percent"),
        # A1C / GMI in % and in mmol/mol, in each A1C unit setting
        ('gcSpeakA1c(6.5, "percent")', "6.5 percent"),
        ('gcSpeakA1c(6.5, "mmol_mol")', "48 millimoles per mole"),
        ('gcSpeakA1c(6.5, "both", ",")', "6,5 percent, 48 millimoles per mole"),
        ('gcSpeakA1c(5.7, "mmol_mol", ".", 39)', "39 millimoles per mole"),  # a GMI's own value from the mean
        # minutes, hours and days
        ('gcSpeak(5, "min")', "5 minutes"),
        ('gcSpeak(1, "min")', "1 minute"),
        ('gcSpeak(2, "h")', "2 hours"),
        ('gcSpeak(3, "d")', "3 days"),
        ("gcSpeakAge(125)", "2 hours 5 minutes"),
        ("gcSpeakAge(1500)", "1 day 1 hour"),
        # period labels in a visible text
        ('gcSpeakText("14d")', "14 days"),
        ('gcSpeakText("90d")', "90 days"),
        ('gcSpeakText("6h")', "6 hours"),
        ('gcSpeakText("GMI14d")', "GMI 14 days"),
        # plus and minus changes
        ('gcSpeakNumber(-3, 0, ".", true)', "minus 3"),
        ('gcSpeakNumber(3, 0, ".", true)', "plus 3"),
        ('gcSpeakNumber(-0.2, 1, ",", true)', "minus 0,2"),
        ('gcSpeak(-3, "mg/dL", { sign: true })', "minus 3 milligrams per deciliter"),
        # a value that rounds to zero is never "minus 0" (rounded once, halves away from zero)
        ('gcSpeakNumber(-0.04, 1, ".", true)', "0.0"),
        ("gcSpeakNumber(-0.4, 0)", "0"),
        ("gcSpeakNumber(-2.5, 0)", "minus 3"),
        # a sign before a number in a visible text
        ('gcSpeakText("-0.6 percentage points")', "minus 0.6 percentage points"),
        ('gcSpeakText("Change: +2.5 points (22-26 weeks)")', "Change: plus 2.5 points (22-26 weeks)"),
        ('gcSpeakText("-3 mg/dL")', "minus 3 milligrams per deciliter"),
        # the trend arrows and words
        ('gcSpeakTrend("↑")', "rising quickly"),
        ('gcSpeakTrend("↗")', "rising"),
        ('gcSpeakTrend("→")', "changing slowly"),
        ('gcSpeakTrend("↘")', "falling"),
        ('gcSpeakTrend("↓")', "falling quickly"),
        ('gcSpeakTrend("Falling Quickly")', "falling quickly"),
        ('gcSpeakTrend("Unknown")', ""),
        # LO / HI
        ('gcSpeakLimit("low")', "below the sensor's lower limit"),
        ('gcSpeakLimit("high")', "above the sensor's upper limit"),
    ],
)
def test_every_unit_is_spoken(expr, spoken):
    assert _speak(f"return {expr};") == spoken


@needs_node
def test_visible_text_made_speakable():
    out = _speak(
        'return gcSpeakText("Mean 102 mg/dL · 70–180 88% (24 h) GMI14d 5,7% ✓ 48 mmol/mol → 3,9–10,0 mmol/L");'
    )
    assert out == (
        "Mean 102 milligrams per deciliter · 70 to 180 88 percent (24 hours) GMI 14 days 5,7 percent meets "
        "48 millimoles per mole changing slowly 3,9 to 10,0 millimoles per liter"
    )
    assert _speak('return gcSpeak(NaN, "mg/dL");') == "not available"


# ------------------------------------------------------------------------------------------------ the display card
# Minimal browser stand-ins: enough of the DOM for the card to draw into an object whose attributes can be read.
DOM = textwrap.dedent("""
    const mk = () => ({ attrs: {}, style: {}, hidden: false, innerHTML: "", listeners: {},
      setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; },
      addEventListener(k, f) { this.listeners[k] = f; }, querySelector() { return null; } });
    globalThis.window = globalThis;
    globalThis.document = { createElement: () => ({ getContext: () => ({ font: "14px", measureText(t) { return { width: String(t).length * 0.57 * (parseFloat(this.font) || 14) }; } }) }) };
    globalThis.HTMLElement = class {
      attachShadow() {
        const els = {}, root = mk();
        root.querySelector = (sel) => (els[sel] ||= mk());
        this.shadowRoot = root; return root;
      }
    };
    const reg = {};
    globalThis.customElements = { get: (n) => reg[n], define: (n, c) => { reg[n] = c; } };
""")

PALETTE = {
    "very_low": "#C62828",
    "low": "#EF5350",
    "near_low": "#FFC107",
    "tight_range": "#4CAF50",
    "near_high": "#FFC107",
    "high": "#42A5F5",
    "very_high": "#1565C0",
}


def _card(
    tag: str,
    *,
    value=250,
    zone="very_high",
    label="Very high",
    age_min=3,
    status="current",
    limit=None,
    unit="mg/dL",
    dec=".",
    page=0,
    zone_colors=None,
    compact=0,
    display_word=None,
    trend_word=None,
    config=None,
    no_current=None,
    reading=True,
    extra: str = "return null;",
) -> dict:
    """Draw a display card with the given state (config: more configuration attributes, for example text_outline;
    no_current: the state of binary_sensor.glucocraft_no_current_data, "on" / "off", None without the entity; reading
    False: no reading ever, the reading sensor unknown and no reading time);
    -> {label, desc, svg, kb: {name: [aria-label, hidden]}}: the drawing's name, the text that follows it (the text
    pages' text), the drawing and the keyboard buttons; extra: the body of a function run afterwards with the card
    in scope, its result as "extra"."""
    now = "Math.floor(Date.now() / 1000)"
    states = {
        "sensor.glucocraft_last_glucose_reading": {
            "state": "unknown" if limit else str(value),  # no number: a reading at the sensor's limit is no value
            "last_updated": "a",
            "attributes": {
                "value_mgdl": value,
                "zone": zone,
                "zone_color": PALETTE.get(zone),
                "zone_label": label,
                "unit": unit,
                "limit": limit,
                "limit_text": {"low": "LO", "high": "HI"}.get(limit),
            },
        },
        "sensor.glucocraft_status": {
            "state": status,
            "last_updated": "a",
            "attributes": {
                "color": {
                    "current": "#43A047",
                    "no_current_data": "#E53935",
                    "expired": "#E53935",
                    "warm_up": "#7E57C2",
                }[status]
            },
        },
        "sensor.glucocraft_trend_arrow": {
            "state": "↘",
            "last_updated": "a",
            "attributes": {"description": "Falling", "angle": -45},
        },
        "sensor.glucocraft_5_minute_delta": {"state": "-3", "last_updated": "a", "attributes": {"value_mgdl": -3}},
        "sensor.glucocraft_configuration": {
            "state": "ok",
            "last_updated": "a",
            "attributes": {
                "unit": unit,
                "decimal_separator": dec,
                "zone_colors": zone_colors or PALETTE,
                **(config or {}),
            },
        },
        "sensor.glucocraft_6h_consensus_ranges": {
            "state": "90",
            "last_updated": "a",
            "attributes": {"limit_readings": {"low": 0, "high": 0}, "limit_seconds": {"low": 0, "high": 0}},
        },
    }
    if no_current is not None:  # GlucoCraft's no-current-data flag
        states["binary_sensor.glucocraft_no_current_data"] = {
            "state": no_current,
            "last_updated": "a",
            "attributes": {},
        }
    if not reading:  # no reading ever: the reading sensor unknown, without a value
        states["sensor.glucocraft_last_glucose_reading"] = {"state": "unknown", "last_updated": "a", "attributes": {}}
    reading_ts = f"{now} - {age_min} * 60" if reading else "null"
    if display_word is not None:  # the zone word under the value, as GlucoCraft publishes it
        states["sensor.glucocraft_last_glucose_reading"]["attributes"]["display_word"] = display_word
    if trend_word is not None:  # the trend word of the TREND column, as GlucoCraft publishes it
        states["sensor.glucocraft_trend_arrow"]["attributes"]["word"] = trend_word
    script = (
        DOM
        + CARD
        + textwrap.dedent(f"""
        const states = {json.dumps(states, ensure_ascii=False)};
        states["sensor.glucocraft_active_source"] = {{ state: "Source", last_updated: "a",
          attributes: {{ detected_interval: 60 }} }};
        states["sensor.glucocraft_last_reading_time"] = {{ state: "t", last_updated: "a",
          attributes: {{ last_reading_ts: {reading_ts} }} }};
        const card = new reg[{json.dumps(tag)}]();
        card.setConfig({{}});
        card._delta = {{ stats: [{{ mean: 110, min: 40, max: 160 }}, {{ mean: 110, min: 58, max: 180 }}], newest: 0, ts: [], val: [] }};
        card._deltaFetched = Date.now(); card._bfFetched = Date.now();
        card._compactW = {compact};   // a card narrower than its design width: the compact layout
        card.hass = {{ states, fetchWithAuth: async () => ({{ ok: false }}) }};
        if ({page}) card._go({page});
        card._render();
        const root = card.shadowRoot, svg = root.querySelector("svg");
        const extra = (() => {{ {extra} }})();
        const kb = Object.fromEntries(["prev", "next", "block"].map((k) => {{
          const el = root.querySelector(`[data-kb="${{k}}"]`); return [k, [el.attrs["aria-label"], el.hidden]]; }}));
        process.stdout.write("\\u0001" + JSON.stringify({{ label: svg.attrs["aria-label"], svg: svg.innerHTML, kb,
          viewBox: svg.attrs.viewBox, desc: root.querySelector("[data-desc]").textContent || "", extra }}));
    """)
    )
    return node_json(script)


@needs_node
@pytest.mark.parametrize("tag", ["glucocraft-display-card", "glucocraft-display-card-wide"])
def test_card_description_in_rule_4_order(tag):
    out = _card(tag)
    text = out["label"]
    value = text.index("Glucose 250 milligrams per deciliter, very high.")
    measured, trend, view = (
        text.index("Measured 3 minutes ago."),
        text.index("Falling, minus 3 milligrams per deciliter in 5 minutes."),
        text.index("6-hour graph:"),
    )
    assert value < measured < trend < view
    assert not text.startswith("No current data") and "mg/dL" not in text
    # the unit is said once
    assert "6-hour graph: lowest 40, highest 160 milligrams per deciliter." in text


@needs_node
def test_card_description_status_first_when_there_is_a_problem():
    stale = _card(
        "glucocraft-display-card", age_min=125, status="no_current_data", value=112, zone="tight_range", label=None
    )["label"]
    # without a current reading the screen's order: its words first, then the last reading with when it was taken and
    # its age; no zone or trend (the screen shows none). The day as the clock has it (independent of the time of day)
    when = r"(today|yesterday) at [^,]+"
    assert re.match(
        r"No current data\. Last reading 112 milligrams per deciliter, "
        + when
        + r", 2 hours 5 minutes ago\. 6-hour graph:",
        stale,
    ), stale
    assert "Glucose" not in stale and "Measured" not in stale and "Falling" not in stale
    expired = _card("glucocraft-display-card", status="expired", age_min=125, no_current="on")["label"]
    assert re.match(r"Sensor expired\. Last reading .+, " + when + r", 2 hours 5 minutes ago\. 6-hour graph:", expired)
    # after the stated expiry while readings arrive: the status first, then the value
    assert _card("glucocraft-display-card", status="expired")["label"].startswith("Sensor expired. Glucose")
    assert _card("glucocraft-display-card", status="warm_up")["label"].startswith("Sensor warming up. Last reading")
    lo = _card("glucocraft-display-card", value=40, zone="very_low", label="Very low", limit="low")["label"]
    assert lo.startswith("Glucose below the sensor's lower limit, very low. Measured")


@needs_node
@pytest.mark.parametrize("tag", ["glucocraft-display-card", "glucocraft-display-card-wide"])
@pytest.mark.parametrize(
    "zone, word",
    [
        ("very_low", "Very low"),
        ("low", "Low"),
        ("near_low", "In range"),
        ("tight_range", "Tight range"),
        ("near_high", "In range"),
        ("high", "High"),
        ("very_high", "Very high"),
    ],
)
def test_zone_word_replaces_the_trend_word_on_screen(tag, zone, word):
    """Every zone has a word in the trend word's place on screen; the trend word stays in the description."""
    out = _card(tag, value=120, zone=zone, label=None)
    assert f">{word}</text>" in out["svg"] and ">Falling</text>" not in out["svg"]
    assert (
        f"Glucose 120 milligrams per deciliter, {word.lower()}." in out["label"] and "Falling, minus 3" in out["label"]
    )


@needs_node
@pytest.mark.parametrize("tag", ["glucocraft-display-card", "glucocraft-display-card-wide"])
@pytest.mark.parametrize("word", ["In tight range", "Tight range", "Very high"])
@pytest.mark.parametrize("trend_display", ["arrow", "word"])
def test_the_zone_word_is_shown_and_fits(tag, word, trend_display):
    """display_word (the zone word, with either choice of Trend shown as) under the value; the description reads zone
    and trend."""
    out = _card(
        tag,
        value=120,
        zone="tight_range",
        label="Tight range",
        display_word=word,
        config={"trend_display": trend_display},
    )
    m = re.search(r'font-size="(\d+)"[^>]*>' + word + "</text>", out["svg"])
    assert m and int(m.group(1)) >= 20
    room = {"glucocraft-display-card": 248, "glucocraft-display-card-wide": 344}[tag]  # as on the panels
    assert len(word) * 0.57 * int(m.group(1)) <= room + 1  # the stand-in font: 0.57 em per character
    assert "Glucose 120 milligrams per deciliter, tight range." in out["label"] and "Falling, minus 3" in out["label"]
    empty = _card(tag, value=120, zone="tight_range", label="Tight range", display_word="")
    assert ">Tight range</text>" not in empty["svg"]  # empty: no word (no current data)


@needs_node
@pytest.mark.parametrize("tag", ["glucocraft-display-card", "glucocraft-display-card-wide"])
def test_during_a_warm_up_the_words_take_the_values_place(tag):
    # as the panels: during a warm-up "Sensor warming up" in the value's place (never the old sensor's number), no
    # word under it, and no current zone in the description
    out = _card(tag, age_min=60, status="warm_up", value=123, zone="tight_range", label="Tight range")
    assert ">Tight range</text>" not in out["svg"] and ">123</text>" not in out["svg"]
    assert re.search(r">Sensor( warming( up)?)?</text>", out["svg"])
    assert "tight range." not in out["label"] and "Glucose" not in out["label"]


@needs_node
def test_the_cards_mirror_the_panels_sizes():
    # a zone word wider than its room at 40 px (a translation's): 32 px at most, as on the panels
    long_lcd = _card("glucocraft-display-card", value=120, zone="tight_range", display_word="In tight range")["svg"]
    m = re.search(r'font-size="(\d+)"[^>]*>In tight range</text>', long_lcd)
    assert m and 20 <= int(m.group(1)) <= 32


TREND_ARROW = "M4,11V13H16L10.5,18.5"  # the arrow's path (Material Design Icons arrow-right)


@needs_node
@pytest.mark.parametrize("tag, size", [("glucocraft-display-card", 26), ("glucocraft-display-card-wide", 36)])
def test_trend_shown_as_arrow_or_word(tag, size):
    """The setting Trend shown as: the arrow (the default), or GlucoCraft's trend word in the TREND column, a longer
    word on two lines at the panel's size; the zone word stays under the value; the description is the same."""
    kw = {"value": 120, "zone": "tight_range", "label": "Tight range", "display_word": "Tight range"}
    kw["trend_word"] = "Falling"
    arrow = _card(tag, **kw)
    assert (
        TREND_ARROW in arrow["svg"] and ">Falling</text>" not in arrow["svg"] and ">Tight range</text>" in arrow["svg"]
    )
    word = _card(tag, **kw, config={"trend_display": "word"})
    assert TREND_ARROW not in word["svg"] and ">Tight range</text>" in word["svg"]
    m = re.search(r'font-size="(\d+)"[^>]*>Falling</text>', word["svg"])
    assert m and 14 <= int(m.group(1)) <= size
    assert word["label"] == arrow["label"] and "Falling, minus 3" in word["label"]
    # a longer word on two lines, each within the column
    two = _card(tag, **{**kw, "trend_word": "Changing slowly"}, config={"trend_display": "word"})["svg"]
    assert ">Changing</text>" in two and ">slowly</text>" in two and ">Changing slowly</text>" not in two
    # no word received yet: the arrow; no current data: neither
    assert TREND_ARROW in _card(tag, **{**kw, "trend_word": None}, config={"trend_display": "word"})["svg"]
    stale = _card(tag, **kw, age_min=60, status="no_current_data", config={"trend_display": "word"})["svg"]
    assert ">Falling</text>" not in stale and TREND_ARROW not in stale


@needs_node
def test_card_description_follows_unit_and_decimal_separator():
    text = _card("glucocraft-display-card", value=250, unit="mmol/L", dec=",")["label"]
    assert (
        "Glucose 13,9 millimoles per liter, very high." in text
        and "minus 0,2 millimoles per liter in 5 minutes" in text
    )


@needs_node
def test_card_keyboard_buttons_are_named_for_where_they_lead():
    kb = _card("glucocraft-display-card")["kb"]
    assert kb["prev"] == ["Previous page: About GlucoCraft", False]
    assert kb["next"] == ["Next page: 24 hours", False]
    assert kb["block"][0] == "Open the weekly pages" and kb["block"][1] is True  # no weekly data: not offered
    assert ".kb:focus-visible { outline: 3px solid" in CARD and "pointer-events: none" in CARD


@needs_node
@pytest.mark.parametrize("tag", ["glucocraft-display-card", "glucocraft-display-card-wide"])
def test_one_about_page_as_on_the_panels(tag):
    """Page 16 (17 of 17): the icon, the disclaimer, the version line and the copyright line. The drawing is named by
    its title, and its text follows as text (read word by word)."""
    from glucocraft import const as C

    about = _card(tag, page=16)
    text = about["desc"]
    assert about["label"] == "About GlucoCraft" and text.startswith(C.DISCLAIMER_SHORT_PLAIN)  # as on the panels
    assert "GlucoCraft Display Card v" in text and "Never for children" not in text
    assert ">17 of 17</text>" in about["svg"] and "For development and testing only." in about["svg"]


# ------------------------------------------------------------------------------------------------ the dashboard
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
            },
        }
    }
    script = DOM + textwrap.dedent(f"""
        globalThis.location = {{ pathname: "/glucocraft-home/start" }};
        await import({json.dumps((WWW / "glucocraft-strategy.js").as_uri())});
        const d = await reg["ll-strategy-dashboard-glucocraft"].generate({{}}, {{ states: {json.dumps(states)},
          devices: {{}}, entities: {{}} }});
        process.stdout.write("\\u0001" + JSON.stringify(d));
    """)
    return node_json(script)


def test_every_page_ends_with_the_disclaimer_line_in_its_footer(dashboard):
    for view in dashboard["views"]:
        cards = [c for s in view["sections"] for c in s.get("cards", [])]
        footers = [c for c in cards if c["type"] == "custom:glucocraft-footer"]
        assert len(footers) == 1, view["path"]
        assert footers[0]["disclaimer"].startswith("For development and testing only. Not for medical use. "), view
        assert "#disclaimer)" in footers[0]["disclaimer"]
        if view["path"] in ("documentation", "report"):
            # these pages carry the attribution in their own text
            assert set(footers[0]) == {"type", "disclaimer", "license", "grid_options"}
        assert "page-notice" not in json.dumps(view) and "terms-notice" not in json.dumps(view)
    assert "part(c.disclaimer)" in UI and "c.notice" not in UI and "hide = false" not in UI


def test_no_copy_of_the_disclaimer_is_hidden_from_screen_readers():
    """No element that holds the disclaimer (disclaimer.text, the long form, or disclaimer.short, the short form)
    carries aria-hidden, in any www file: the display cards, the CGM Report page and the Documentation."""
    files = sorted(WWW.glob("*.js"))
    assert len(files) >= 6
    uses = 0
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if '"disclaimer.text"' not in line and '"disclaimer.short"' not in line:
                continue
            uses += 1
            # the element that holds it opens on this line or the line before (a template split over two lines)
            near = "\n".join(lines[max(0, i - 1) : i + 1])
            assert "aria-hidden" not in near, f"{path.name}:{i + 1}"
    assert uses >= 3  # the display card, the Documentation, the CGM Report
    assert "notice: true" not in STRAT


def test_charts_tables_and_controls_in_the_ui_cards():
    # every chart is described in one sentence (role="img" with a label)
    assert UI.count('role="img" aria-label="${esc(said)}"') == 3  # history, daily range, Change Over Time
    assert 'svg.replace("<svg ", `<svg role="img" aria-label="${esc(said)}" `)' in UI  # Change Over Time
    # key results are real tables: header cells with their scope; the core tiles have a table for screen readers
    assert '<th scope="row" class="l">' in UI and '<th scope="col">' in UI
    assert '<table class="sr"><caption>${gcT("ui.core.caption")}</caption>' in UI
    assert i18n_text("ui.core.caption") == "Core measures, last 14 days"
    # links and buttons named for where they lead; a page row of the pages card is described by its two lines (their
    # units and mark in words), not named by them
    assert UI.count('aria-label="${esc(gcT("ui.open", { name }))}"') == 2
    assert 'aria-describedby="h-${p} d-${p}"' in UI and "gcLinkLabel" not in UI
    assert i18n_text("ui.open", name="Today") == "Open Today"
    # the headline card: a real link (with its page) or button, named by its title; the lines are its description
    assert '<a class="t ctl" href="${esc(path)}" aria-describedby="lines">' in UI
    assert '<button type="button" class="t ctl" aria-describedby="lines hint">' in UI
    assert 'role="button" tabindex="0"' not in UI and 'role="link"' not in UI
    assert (
        i18n_text("ui.settings.button_label") == "Change Settings: open GlucoCraft's page in Home Assistant's settings"
    )
    assert 'aria-label="${esc(gcT("ui.settings.button_label"))}"' in UI
    # one focus ring: 3 px in the text color, from glucocraft-shared.js
    assert ".tap:focus-visible, button:focus-visible, a:focus-visible, .tw:focus-visible { ${GC_CSS.focus} }" in UI
    assert 'focus: "outline: 3px solid var(--primary-text-color); outline-offset: 2px;"' in SHARED


def test_documentation_links_and_buttons_work_by_keyboard():
    assert '<a class="cite" href="#ref-${id}"' in DOCS and '<a class="sec" href="#${id}"' in DOCS
    assert '<button type="button" id="all">${gcT("docs.bar.open_all")}</button>' in DOCS
    assert i18n_text("docs.bar.open_all", area="documentation") == "Open all sections"
    # each section: a heading with a button that shows and hides it (the disclosure pattern)
    assert (
        '<h2 class="sh" id="${id}-title">' in DOCS
        and 'aria-expanded="${open.has(id)}" aria-controls="${id}-body"' in DOCS
    )
    assert "<details" not in DOCS and "<summary" not in DOCS
    assert '["accessibility", "mdi:human"]' in DOCS
    assert i18n_text("docs.section.accessibility.title", area="documentation") == "Accessibility"


@needs_node
def test_history_chart_description_in_the_display_unit():
    src = (
        "globalThis.window = globalThis; globalThis.HTMLElement = class {}; const __reg = {};\n"
        "globalThis.customElements = { get: (n) => __reg[n], define: (n, c) => { __reg[n] = c; } };\n"
        "globalThis.ResizeObserver = class { observe() {} disconnect() {} };\n"
        + UI
        + "\nconst h = new __reg['glucocraft-history'](); h._hass = { states: { 'sensor.glucocraft_6h_consensus_ranges': "
        "{ attributes: { limit_readings: { low: 0, high: 1 }, limit_seconds: { low: 0, high: 300 } } } } }; "
        "h._cfg = { hours: 6 };\n"
        "process.stdout.write('\\u0001' + JSON.stringify(h.describe('Glucose — Last 6 Hours', 6, [[1, 58], [2, 400], [3, 100]])));\n"
    )
    out = node_json(src)
    assert out == ("Glucose — Last 6 Hours: lowest 58, highest 400, mean 186 milligrams per deciliter.")


@needs_node
@pytest.mark.parametrize("tag", ["glucocraft-display-card", "glucocraft-display-card-wide"])
@pytest.mark.parametrize("width", [360, 412])
@pytest.mark.parametrize("page", [0, 7, 16, 17])
def test_narrow_cards_never_draw_text_below_14_px(tag, width, page):
    """Below the design width the card draws its compact layout at its own width (one pixel per pixel)."""
    out = _card(tag, compact=width, page=page)
    assert out["viewBox"].startswith(f"0 0 {width} ")
    sizes = [int(x) for x in re.findall(r'font-size="(\d+)"', out["svg"])]
    assert sizes and min(sizes) >= 14, sorted(set(sizes))
    if page == 0:
        # the panel's order, top to bottom (the glucose information first, the status bar never above it): value,
        # zone word, trend, then on the square card the status bar and the graph (as on the
        # 480 x 480 panel), on the rectangular card the graph and the status bar (its bottom strip on the 800 x 480
        # panel), then the page buttons
        svg = out["svg"]

        def top(text):
            m = re.search(r'<text\b[^>]*\by="(-?[0-9.]+)"[^>]*>' + re.escape(text) + "<", svg)
            assert m, text
            return float(m.group(1))

        value, word, trend = top("250"), top("Very high"), top("TREND")
        # the page: its statistics row (the graph's own texts are drawn in the graph's coordinates)
        status, graph, name = top("Reading: 3 min ago"), top("TIR"), top("6 Hours")
        assert "6-Hour History" in svg
        assert value < word < trend
        if tag == "glucocraft-display-card":
            assert trend < status < graph < name
        else:
            assert trend < graph < status < name


WEEK_S = 7 * 86400


@needs_node
@pytest.mark.parametrize("tag", ["glucocraft-display-card", "glucocraft-display-card-wide"])
@pytest.mark.parametrize("compact", [0, 360])
def test_weekly_dates_have_a_space_and_never_overlap(tag, compact):
    """The date under every 4th week is "Oct 12" (with a space, as on the panels): the record's start, the middle of its
    14-day window; a label that would come closer than 4 px to the one before it is left out."""
    script = (
        DOM
        + CARD
        + textwrap.dedent(f"""
        const card = new reg[{json.dumps(tag)}]();
        card.setConfig({{}});
        card._compactW = {compact};
        if ({compact}) card._compactL = {{ ...card.L, W: {compact}, trend: {{ x: 12, y: 0, w: {compact} - 24, h: 200 }} }};
        const end = Date.UTC(2026, 9, 22, 12) / 1000;
        card._bf = {{ at: end, dirs: [], stats: [], trends: Array.from({{ length: 26 }}, (_, w) => ({{
          start: end - w * {WEEK_S} - {WEEK_S}, mean: 120, tir: 70, titr: 50, cv: 30, gmi: 6.5, low: 2, high: 20, sd: 30,
          min: 50, max: 250 }})) }};
        process.stdout.write("\\u0001" + JSON.stringify(card._trendCanvas(0)));
    """)
    )
    svg = node_json(script)
    labels = [(float(x), t) for x, t in re.findall(r'<text x="([\d.]+)"[^>]*>([A-Z][a-z]{2} \d{1,2})</text>', svg)]
    assert len(labels) >= 3, svg[-600:]
    assert not re.search(r">[A-Z][a-z]{2}\d", svg)  # never "Oct12"
    xs = [x for x, _ in labels]
    assert xs == sorted(xs)
    # the approximate width of a 14 px label in the test DOM (0.57 x size per character): at least 4 px apart
    half = [len(t) * 14 * 0.57 / 2 for _, t in labels]
    assert all(xs[i] - half[i] >= xs[i - 1] + half[i - 1] + 4 for i in range(1, len(xs)))


@needs_node
def test_about_version_line_is_the_name_and_version():
    """The version line names the card and its version, within the text column."""
    out = _card("glucocraft-display-card", page=16)
    texts = re.findall(r">([^<>]+)</text>", out["svg"])
    line = next(t for t in texts if t.startswith("GlucoCraft Display Card v"))
    assert re.fullmatch(r"GlucoCraft Display Card v\d+\.\d+\.\d+", line), line
    assert len(line) * 16 * 0.57 <= 480 * 0.92  # within the column (test DOM)


@needs_node
@pytest.mark.parametrize("tag", ["glucocraft-display-card", "glucocraft-display-card-wide"])
def test_about_page_ends_with_the_copyright_line(tag):
    """Under the version line, the copyright line in its two lines (the catalogue's line break), as on the panels;
    the text that follows the drawing reads it as one sentence."""
    out = _card(tag, page=16)
    texts = re.findall(r">([^<>]+)</text>", out["svg"])
    v = next(i for i, t in enumerate(texts) if t.startswith("GlucoCraft Display Card v"))
    assert texts[v + 1 : v + 3] == ["Copyright (C) 2026 The GlucoCraft authors.", "License: gnu.org/licenses/agpl-3.0"]
    assert out["desc"].endswith(" Copyright (C) 2026 The GlucoCraft authors. License: gnu.org/licenses/agpl-3.0.")


# ------------------------------------------------------------------------------------------------ weekly LO / HI
def _weekly_file() -> str:
    """A GLU2 v3 file (base64) with two weeks in its trends section and the GCL2 trailer."""
    import base64
    import struct

    from glucocraft import panel_feed as pf

    so = pf.HEADER_SIZE
    ro = so + pf.STATS_SIZE
    to = ro + pf.RANGES_SIZE  # no readings: the trends section follows the ranges
    trends = b"".join(
        pf._TREND_FMT.pack(0, 0, 120, 30, 25, 6.5, 70, 40, 3, 20, lo, hi) for lo, hi in ((40.0, 400.0), (60.0, 250.0))
    )
    data = (
        pf._HEADER_FMT.pack(b"GLU2", pf.GLU2_VERSION, 2, so, ro, to, 0, 0, 0, to, 0)
        + b"\0" * pf.STATS_SIZE
        + struct.pack("<6f", 54, 250, 70, 180, 70, 140)
        + trends
        + pf.extremes_block({})
        + pf.limit_time_trailer({})
    )
    return base64.b64encode(data).decode()


def _weekly(tag: str) -> list:
    """[the status line, the text description] of the weekly Min and Max page, from the file of _weekly_file."""
    script = (
        DOM
        + CARD
        + textwrap.dedent(f"""
        const card = new reg[{json.dumps(tag)}](); card.setConfig({{}});
        const b = Buffer.from({json.dumps(_weekly_file())}, "base64");
        card._bf = globalThis.glucocraftShared.parseGlu2(b.buffer.slice(b.byteOffset, b.byteOffset + b.length));
        process.stdout.write("\u0001" + JSON.stringify([card._trendStatus(8), card._describeWeekly(8)]));
    """)
    )
    return node_json(script)


@needs_node
def test_weekly_min_max_are_measured_numbers():
    """A week's lowest and highest value come from the measured readings (a reading at the sensor's limit is no value):
    always numbers, on every card."""
    for tag in ("glucocraft-display-card", "glucocraft-display-card-wide"):
        status, said = _weekly(tag)
        assert status.startswith("Min: 40  Max: 400"), tag
        assert (
            "lowest from 40 to 60 milligrams per deciliter, highest from 250 to 400 milligrams per deciliter." in said
        )
        assert "Last 14 days: lowest 40 milligrams per deciliter, highest 400 milligrams per deciliter." in said
    assert "weekLimits" not in CARD and "_weekLimits" not in CARD


def _period_file(lo: int, hi: int) -> str:
    """A GLU2 v3 file (base64) whose GCL2 trailer gives the 7-day period lo and hi seconds at the sensor's limit, as
    panel_feed.limit_time_trailer writes it."""
    import base64
    import struct
    import types

    from glucocraft import panel_feed as pf

    so = pf.HEADER_SIZE
    ro = so + pf.STATS_SIZE
    times = {"7d": types.SimpleNamespace(limit_seconds={"low": lo, "high": hi})}
    data = (
        pf._HEADER_FMT.pack(b"GLU2", pf.GLU2_VERSION, 0, so, ro, ro + pf.RANGES_SIZE, 0, 0, 0, 0, 0)
        + b"\0" * pf.STATS_SIZE
        + struct.pack("<6f", 54, 250, 70, 180, 70, 140)
        + pf.extremes_block({})
        + pf.limit_time_trailer(times)
    )
    return base64.b64encode(data).decode()


def _period_min_max(tag: str, lo: int, hi: int, sensor: str) -> list:
    """[the 7-day Min and Max cells, the 7-day graph's sentence, its warning] of a card whose full file has a 7-day
    minimum of 58 and maximum of 300 and the given seconds at the limit; sensor: the state of the 7-day period sensor
    ("absent", "unavailable" or "disagrees": available, counting no limit reading)."""
    states = {}
    if sensor != "absent":
        attrs = {"limit_readings": {"low": 0, "high": 0}, "limit_seconds": {"low": 0, "high": 0}}
        states["sensor.glucocraft_7d_consensus_ranges"] = {
            "state": "unavailable" if sensor == "unavailable" else "90",
            "last_updated": "a",
            "attributes": {} if sensor == "unavailable" else attrs,
        }
    script = (
        DOM
        + CARD
        + textwrap.dedent(f"""
        const card = new reg[{json.dumps(tag)}](); card.setConfig({{}});
        card._hass = {{ states: {json.dumps(states)} }};
        const b = Buffer.from({json.dumps(_period_file(lo, hi))}, "base64");
        card._bf = globalThis.glucocraftShared.parseGlu2(b.buffer.slice(b.byteOffset, b.byteOffset + b.length));
        card._bf.stats[2] = {{ ...card._bf.stats[2], mean: 140, min: 58, max: 300 }};
        const cells = Object.fromEntries(card._statsFor(2).a);
        process.stdout.write("\u0001" + JSON.stringify([[cells.min, cells.max], card._describeGraph(2)]));
    """)
    )
    return node_json(script)


@needs_node
@pytest.mark.parametrize("tag", ["glucocraft-display-card", "glucocraft-display-card-wide"])
@pytest.mark.parametrize("sensor", ["absent", "unavailable", "disagrees"])
def test_the_time_at_the_limit_comes_from_the_file_and_the_extremes_stay_numbers(tag, sensor):
    """The screen-reader sentence on the time at the limit comes from the file that holds the graph (its GCL2
    trailer), whatever the period sensor says or whether it exists; the minimum and the maximum are measured numbers (a
    reading at the sensor's limit is no value)."""
    cells, said = _period_min_max(tag, 600, 60, sensor)
    assert cells == ["58", "300"] and "lowest 58, highest 300 milligrams per deciliter" in said
    assert (
        "below the sensor's lower limit for 10 minutes" in said
        and "above the sensor's upper limit for 1 minute" in said
    )
    # no time at the limit: no sentence about it
    cells, said = _period_min_max(tag, 0, 0, sensor)
    assert cells == ["58", "300"] and "sensor's" not in said


def _graph_lines(tag: str) -> list:
    """[a glucose graph's background, the weekly Time in range chart] of a card, as drawn."""
    r = {"cmin": 54, "nmin": 70, "imin": 70, "imax": 140, "nmax": 180, "cmax": 250}
    script = (
        DOM
        + CARD
        + textwrap.dedent(f"""
        const card = new reg[{json.dumps(tag)}](); card.setConfig({{}});
        // a full file without weeks whose range set (GCX1) gives Time in range its consensus target
        card._bf = {{ trends: [], weekLimits: [], std: {{ custom: false, targets: [[">", 70]], descs: [""] }} }};
        const graph = card._bgAndGrid(card._scale(40, 300), {json.dumps(r)}, "6 hours");
        process.stdout.write("\u0001" + JSON.stringify([graph, card._trendCanvas(0)]));
    """)
    )
    return node_json(script)


@needs_node
def test_the_cards_draw_no_limit_lines_and_a_solid_target_line():
    """The cards draw no lines at the range limits and the weekly target as a solid light-gray line, as the panels."""
    for tag in ("glucocraft-display-card", "glucocraft-display-card-wide"):
        graph, weekly = _graph_lines(tag)
        assert "<line" not in graph and "dasharray" not in weekly and 'height="1" fill="#bbbbbb"/>' in weekly


@needs_node
def test_card_description_waits_with_the_page_and_names_no_zone_during_a_warm_up():
    """A graph or weekly page without the history file says that the history is loading (or not available), as the
    page shows; during a warm-up no zone is spoken, as no word is shown under the value."""
    # no file yet and no live reading (the first draw): the 6-hour graph waits too, in words and in the drawing
    first_draw = (
        "card._delta = null; card._bf = null; card._live = []; card._render();"
        "const s = card.shadowRoot.querySelector('svg');"
        "return {label: s.attrs['aria-label'], loading: s.innerHTML.includes('Loading')};"
    )
    for tag in ("glucocraft-display-card", "glucocraft-display-card-wide"):
        six = _card(tag, page=0, extra=first_draw)["extra"]
        assert "6-hour graph: the history is loading." in six["label"] and "no readings" not in six["label"], tag
        assert six["loading"], tag
    assert "24-hour graph: the history is loading." in _card("glucocraft-display-card", page=1)["label"]
    assert "Time in range: the history is loading." in _card("glucocraft-display-card", page=7)["label"]
    warm = _card("glucocraft-display-card", status="warm_up")["label"]
    assert re.match(r"Sensor warming up\. Last reading 250 milligrams per deciliter, [^,]+, 3 minutes ago\.", warm)
    assert "very high" not in warm


def test_documentation_code_examples_wrap_instead_of_scrolling():
    """A code example on the Documentation page (the voice example) wraps at any width: a sideways scroll box would be
    reachable only by pointer (WCAG 2.2 reflow and keyboard)."""
    docs = (WWW / "glucocraft-docs.js").read_text(encoding="utf-8")
    rule = re.search(r"\n\s*pre \{([^}]*)\}", docs).group(1)
    assert "white-space: pre-wrap" in rule and "overflow-wrap: anywhere" in rule and "overflow-x" not in rule


def test_report_reads_the_disclaimer_in_about_this_report():
    """The CGM Report page reads the disclaimer once, in About This Report: shown and read, never hidden."""
    report = (WWW / "glucocraft-report.js").read_text(encoding="utf-8")
    assert report.count('para("disclaimer.text")') == 1
    assert (
        "aria-hidden"
        not in report[report.index('para("disclaimer.text")') - 200 : report.index('para("disclaimer.text")')]
    )


# ------------------------------------------------------------------------------------------------ comparison signs
@needs_node
@pytest.mark.parametrize(
    "shown, said",
    [
        ("Consensus target <4%", "Consensus target less than 4 percent"),
        ("Consensus target >70%", "Consensus target more than 70 percent"),
        ("Consensus ≤36% (Battelino 2019)", "Consensus at most 36 percent (Battelino 2019)"),
        ("≥ 70%", "at least 70 percent"),
        (">=70%", "at least 70 percent"),
        ("Time Below Range (<70 mg/dL)", "Time Below Range (less than 70 milligrams per deciliter)"),
        ("<3,9 mmol/L", "less than 3,9 millimoles per liter"),
        ("<54", "less than 54"),
        ("Consensus target <5% · above 250", "Consensus target less than 5 percent · above 250"),
    ],
)
def test_comparison_signs_are_spoken_in_words(shown, said):
    """Screen readers skip "<" and ">" at their usual punctuation level: every spoken text says the sign in words, in
    the cards (gcSpeakText, the hidden spoken form of a table cell) and in Python (the PDF's /ActualText)."""
    from glucocraft.spoken import speak_comparisons

    assert _speak(f"return gcSpeakText({json.dumps(shown)});") == said
    # Python writes the signs in words and leaves the units as written (the PDF reads them as printed)
    words = speak_comparisons(shown)
    assert not re.search(r"[<>≤≥]", words)
    for sign, word in (("<", "less than"), (">", "more than"), ("≤", "at most"), ("≥", "at least")):
        if sign in shown and "=" not in shown:
            assert word in words


@needs_node
@pytest.mark.parametrize(
    "shown, said",
    [
        ("Change: -0.6 points", "Change: minus 0.6 points"),
        ("+2.5 points (22-26 weeks ago)", "plus 2.5 points (22-26 weeks ago)"),
        ("difference (−0,2)", "difference (minus 0,2)"),
        ("week 22-26, mean 3-4", "week 22-26, mean 3-4"),
    ],
)
def test_a_sign_before_a_number_is_spoken_in_words_by_the_cards_and_python(shown, said):
    """A plus or minus sign before a number is said in words by the cards (gcSpeakText) and by Python
    (spoken.speak_comparisons, the PDF's /ActualText), in the same words; a hyphen between numbers stays."""
    from glucocraft.spoken import speak_comparisons

    assert _speak(f"return gcSpeakText({json.dumps(shown)});") == said
    assert speak_comparisons(shown) == said


@needs_node
def test_a_table_cell_with_a_sign_has_its_spoken_form():
    """The visible sign stays, hidden from screen readers; its spoken form is read instead (like the unit symbols)."""
    out = node_json(
        "globalThis.window = globalThis; globalThis.HTMLElement = class {}; const __reg = {};\n"
        "globalThis.customElements = { get: (n) => __reg[n], define: (n, c) => { __reg[n] = c; } };\n"
        + UI
        + "\nprocess.stdout.write('\\u0001' + JSON.stringify(gcSay('Consensus target <4% (Battelino et al., 2019)')));\n"
    )
    assert out == (
        '<span aria-hidden="true">Consensus target &lt;4% (Battelino et al., 2019)</span>'
        '<span class="sr">Consensus target less than 4 percent (Battelino et al., 2019)</span>'
    )


def test_notifications_and_the_settings_error_write_the_comparisons_in_words():
    from glucocraft import i18n as I
    from glucocraft import notification as N
    from glucocraft.regional import Regional

    line = N._assess_target(80.2, 70, ">", "Time in range", "Battelino et al., 2019", Regional())
    assert "consensus target more than 70%" in line and ">" not in line
    assert "at most 36%" in N._assess_target(30.0, 36, "<=", "CV", "Battelino et al., 2019", Regional())
    assert "less than 4%" in N._assess_target(3.0, 4, "<", "Below", "Battelino et al., 2019", Regional())
    # the summary lines are separated by semicolons, which screen readers read as a pause ("·" is a "middle dot")
    assert "·" not in I.t("notify.overview.values") and "·" not in I.t("notify.overview.custom")
    strings = json.loads((REPO / "custom_components" / "glucocraft" / "strings.json").read_text(encoding="utf-8"))
    # the order in words: the settings error, and the Documentation's Setup (the screen's description points to it)
    doc_file = REPO / "custom_components" / "glucocraft" / "i18n" / "en" / "documentation.json"
    doc = json.loads(doc_file.read_text(encoding="utf-8"))
    for text in (
        strings["options"]["error"]["range_order"],
        re.sub(r"<[^>]+>", "", "".join(doc["docs.section.setup.body"])),
    ):
        # a menu path's separator ("Settings > Devices & services") is not a comparison sign: it stands between words
        text = re.sub(r" > (?=[A-Za-z]|\{(?:configure|screen_\w+)\})", " / ", text)
        assert not re.search(r"[<>≤≥]", text) and "very low less than low, low at most tight range from" in text


# ------------------------------------------------------------------------------------------------ a small DOM
# Enough of a browser for the focus rules: the elements of the HTML a card writes, as a tree (start and end tags,
# attributes; no text nodes), selectors of tags, classes, ids, attributes, :not() and descendants, the shadow root's
# activeElement and focus().
FAKE_DOM = textwrap.dedent(r"""
    const VOID = new Set(["input", "br", "img", "col", "rect", "path", "line", "circle", "meta", "link", "hr", "stop"]);
    const compound = (el, sel) => {
      const m = sel.match(/^([a-zA-Z][\w-]*)?((?:\.[\w-]+|#[\w-]+|\[[^\]]+\]|:not\([^)]*\))*)$/);
      if (!m) throw new Error(`selector ${sel}`);
      if (m[1] && el.localName !== m[1].toLowerCase()) return false;
      for (const part of m[2].match(/\.[\w-]+|#[\w-]+|\[[^\]]+\]|:not\([^)]*\)/g) || []) {
        if (part[0] === ".") { if (!(el.getAttribute("class") || "").split(/\s+/).includes(part.slice(1))) return false; }
        else if (part[0] === "#") { if (el.getAttribute("id") !== part.slice(1)) return false; }
        else if (part.startsWith(":not(")) { if (compound(el, part.slice(5, -1))) return false; }
        else {
          const a = part.slice(1, -1).match(/^([\w-]+)(?:([$^]?=)"?((?:[^"\\]|\\.)*)"?)?$/);
          const v = el.getAttribute(a[1]), want = a[3] === undefined ? undefined : a[3].replace(/\\(.)/g, "$1");
          if (v === null) return false;
          if (a[2] === "=" && v !== want) return false;
          if (a[2] === "$=" && !v.endsWith(want)) return false;
        }
      }
      return true;
    };
    const matches = (el, sel) => sel.split(",").some((one) => {
      const parts = one.trim().split(/\s+/);
      if (!compound(el, parts.pop())) return false;
      let up = el.parentNode;
      while (parts.length && up && up.localName) { if (compound(up, parts[parts.length - 1])) parts.pop(); up = up.parentNode; }
      return !parts.length;
    });
    class Node0 {
      constructor() { this.children = []; this._html = ""; }
      get innerHTML() { return this._html; }
      set innerHTML(html) {
        this._html = html; this.children = [];
        const stack = [this];
        for (const m of String(html).replace(/<style>[\s\S]*?<\/style>/g, "").matchAll(/<(\/?)([a-zA-Z][\w-]*)((?:[^>"]|"[^"]*")*)>/g)) {
          const [, close, tag, rest] = m;
          if (close) { while (stack.length > 1 && stack.pop().localName !== tag.toLowerCase()); continue; }
          const attrs = {};
          for (const a of rest.matchAll(/([\w:-]+)(?:="([^"]*)")?/g)) attrs[a[1]] = (a[2] ?? "").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
          const el = new El(this.root || this, tag.toLowerCase(), attrs, stack[stack.length - 1]);
          stack[stack.length - 1].children.push(el);
          if (!VOID.has(el.localName) && !rest.trim().endsWith("/")) stack.push(el);
        }
      }
      *all() { for (const c of this.children) { yield c; yield* c.all(); } }
      querySelectorAll(sel) { return [...this.all()].filter((e) => matches(e, sel)); }
      querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
      getElementById(id) { return [...this.all()].find((e) => e.getAttribute("id") === id) || null; }
      contains(el) { for (let e = el; e; e = e.parentNode) if (e === this) return true; return false; }
    }
    class El extends Node0 {
      constructor(root, tag, attrs, parent) {
        super(); this.root = root; this.localName = tag; this._a = attrs; this.parentNode = parent; this.style = {};
        this.textContent = ""; this.listeners = {};
      }
      get attributes() { return Object.entries(this._a).map(([name, value]) => ({ name, value })); }
      get dataset() { return Object.fromEntries(Object.entries(this._a).filter(([k]) => k.startsWith("data-"))
        .map(([k, v]) => [k.slice(5).replace(/-(\w)/g, (x, c) => c.toUpperCase()), v])); }
      get value() { return this._a.value ?? ""; }
      get classList() { const el = this; return { add() {}, remove() {}, contains: (c) => matches(el, `.${c}`) }; }
      getAttribute(n) { return n in this._a ? this._a[n] : null; }
      hasAttribute(n) { return n in this._a; }
      setAttribute(n, v) { this._a[n] = String(v); }
      removeAttribute(n) { delete this._a[n]; }
      matches(sel) { return matches(this, sel); }
      addEventListener(k, f) { this.listeners[k] = f; }
      focus() { this.root.activeElement = this; }
      scrollIntoView() {}
    }
    class Root extends Node0 { constructor() { super(); this.activeElement = null; } }
    globalThis.window = globalThis;
    globalThis.HTMLElement = class {
      constructor() { this.style = {}; this._attrs = {}; }
      attachShadow() { this.shadowRoot = new Root(); return this.shadowRoot; }
      setAttribute(k, v) { this._attrs[k] = v; }
      getBoundingClientRect() { return { width: 600 }; }
      dispatchEvent() {}
    };
    globalThis.ResizeObserver = class { observe() {} disconnect() {} };
    const __reg = {};
    globalThis.customElements = { get: (n) => __reg[n], define: (n, c) => { __reg[n] = c; }, whenDefined: async () => {} };
    globalThis.history = { pushState() {} }; globalThis.location = { pathname: "/glucocraft-home/report", hash: "" };
    globalThis.CustomEvent = class { constructor(type, init) { this.type = type; Object.assign(this, init); } };
    globalThis.dispatchEvent = () => {};
    const out = (v) => process.stdout.write("\u0001" + JSON.stringify(v));
""")


def _dom(src: str, file: str = "ui") -> object:
    """Run src after one www file, in the small DOM (FAKE_DOM); src writes its result with out(...)."""
    code = {"ui": UI, "report": (WWW / "glucocraft-report.js").read_text(encoding="utf-8"), "docs": DOCS}[file]
    return node_json(FAKE_DOM + code + "\n{\n" + textwrap.dedent(src) + "\n}\n")  # in a block: its names stay its own


PAGES_STATES = {
    "sensor.glucocraft_configuration": {"state": "ok", "last_updated": "a", "attributes": {"unit": "mg/dL"}},
    "sensor.glucocraft_status": {"state": "current", "last_updated": "a", "attributes": {}},
    "sensor.glucocraft_14d_consensus_ranges": {
        "state": "80",
        "last_updated": "a",
        "attributes": {"has_data": True, "time_70_180": 80.2, "sensor_active": 95, "sensor_active_ok": True},
    },
}


# ------------------------------------------------------------------------------------------------ focus and links
@needs_node
def test_focus_stays_on_its_control_when_a_dashboard_card_redraws():
    """A card draws again with every reading and every minute: unchanged content stays in place (the same element
    keeps the focus), and changed content focuses the new element of the same control (WCAG 2.4.3)."""
    res = _dom(f"""
        const states = {json.dumps(PAGES_STATES)};
        const card = new (customElements.get("glucocraft-pages"))(); card.setConfig({{}});
        card.hass = {{ states }};
        const root = card.shadowRoot, row = () => root.querySelector('a[data-p="consensus"]');
        const first = row(); first.focus();
        card.draw();                                   // the minute's redraw, nothing changed
        const kept = root.activeElement === first && row() === first;
        states["sensor.glucocraft_14d_consensus_ranges"] = {{ ...states["sensor.glucocraft_14d_consensus_ranges"],
          last_updated: "b", attributes: {{ ...states["sensor.glucocraft_14d_consensus_ranges"].attributes, time_70_180: 64.0 }} }};
        card.hass = {{ states }};                      // a new reading changes the row
        const now = root.activeElement;
        out({{ kept, replaced: now !== first, focused: now === row(), p: now.getAttribute("data-p"),
          hrefs: root.querySelectorAll("a").map((a) => a.getAttribute("href")),
          roles: root.querySelectorAll("[role]").map((a) => a.getAttribute("role")) }});
    """)
    assert res["kept"] and res["replaced"] and res["focused"] and res["p"] == "consensus"
    # the rows and buttons are real links with their addresses (they can be opened in a new tab)
    assert res["hrefs"] == [
        "/glucocraft-home/today",
        "/glucocraft-home/consensus",
        "/glucocraft-home/maintenance",
        "/glucocraft-home/settings",
        "/glucocraft-home/documentation",
    ]
    assert "link" not in res["roles"]


@needs_node
def test_report_page_keeps_the_focus_announces_messages_and_shows_the_choice():
    """The CGM Report page: the pressed button keeps the focus while the page is drawn again; each group's choice is a
    toggle button with aria-pressed and a check mark; the messages go to two regions that exist from the start; an
    invalid laboratory entry marks its fields and moves the focus to the first."""
    from test_report_outputs import _data

    res = _dom(
        f"""
        const data = {json.dumps(_data(14, False))};
        let fail = false;
        const card = new (customElements.get("glucocraft-report"))(); card.setConfig({{}});
        card.hass = {{ states: {{}}, fetchWithAuth: async (url) => (fail || url.includes("report.pdf")
          ? {{ ok: false, status: 500, text: async () => "no printer" }} : {{ ok: true, json: async () => data }}) }};
        await new Promise((r) => setTimeout(r, 0));
        const root = card.shadowRoot, st = root.querySelector(".status[role=status]"), al = root.querySelector(".status[role=alert]");
        const empty = [st.textContent, al.textContent];
        const days = (n) => root.querySelector(`button[data-act="days:${{n}}"]`);
        days(30).focus();
        days(30).onclick({{}});
        await new Promise((r) => setTimeout(r, 0));
        const focus = root.activeElement.getAttribute("data-act");
        const pressed = root.querySelectorAll("button[aria-pressed]").map((b) => [b.getAttribute("data-act"), b.getAttribute("aria-pressed")]);
        const groups = root.querySelectorAll("[role=group]").map((g) => [g.getAttribute("aria-label"), g.querySelectorAll('button[aria-pressed="true"]').length]);
        await card.pdf();
        const after = [root.querySelector(".status[role=status]") === st, root.querySelector(".status[role=alert]") === al,
          st.textContent, al.textContent];
        root.querySelector('button[data-act="labadd"]').onclick({{}});
        const lab = [root.activeElement.getAttribute("id"), root.getElementById("labdate").getAttribute("aria-invalid"),
          root.getElementById("labdate").getAttribute("aria-describedby"), !!root.getElementById("laberr")];
        const links = root.querySelectorAll("a").map((a) => [a.getAttribute("href"), a.getAttribute("data-act")]);
        const remove = root.querySelectorAll("button.sm").map((b) => b.getAttribute("aria-label"));
        out({{ empty, focus, pressed, groups, after, lab, links, remove, heading: !!root.querySelector("h2.sr") }});
    """,
        "report",
    )
    assert res["empty"] == ["", ""]
    assert res["focus"] == "days:30"
    assert ["days:30", "true"] in res["pressed"] and ["days:14", "false"] in res["pressed"]
    assert res["groups"] == [
        ["Report period", 1],
        ["Report form", 1],
        ["Colors of the PDF and printout", 1],
        ["Printing", 1],  # one-sided or two-sided, sent with the print action (two_sided)
    ]
    same_status, same_alert, status, alert = res["after"]
    assert same_status and same_alert and status == "" and alert.startswith("The PDF could not be created")
    assert res["lab"] == ["labdate", "true", "laberr", True]
    # the links to the Documentation are real links with their addresses
    assert ["/glucocraft-home/documentation#references", "references"] in res["links"]
    assert ["/glucocraft-home/documentation#colors", "colors"] in res["links"]
    assert all(href for href, _ in res["links"])
    assert res["heading"]
    report = (WWW / "glucocraft-report.js").read_text(encoding="utf-8")
    # the chosen button is marked by a check mark besides its color; the mark itself is not read
    assert 'button[aria-pressed="true"]::before { content: "✓\\u00a0"; content: "✓\\u00a0" / ""; }' in report


@needs_node
def test_banners_announce_a_change_of_their_conditions_once():
    """The banners' status message is one region that exists from the first drawing; its text is the conditions'
    titles, set a moment after it was created or shown, and not changed by the minute's redraw (the ages)."""
    res = _dom("""
        const states = { "sensor.glucocraft_status": { state: "no_current_data", last_updated: "a", attributes: {} },
          "binary_sensor.glucocraft_no_current_data": { state: "on", last_updated: "a", attributes: {} },
          "sensor.glucocraft_last_reading_time": { state: new Date(Date.now() - 3600e3).toISOString(), last_updated: "a" },
          "sensor.glucocraft_configuration": { state: "ok", last_updated: "a", attributes: { unit: "mg/dL" } } };
        const card = new (customElements.get("glucocraft-banners"))(); card.setConfig({});
        card.hass = { states };
        const root = card.shadowRoot, live = root.querySelector(".live");
        const before = live.textContent;
        await new Promise((r) => setTimeout(r, 600));
        const said = live.textContent;
        card.draw();
        out({ before, said, same: root.querySelector(".live") === live, role: live.getAttribute("role"),
          cards: root.querySelectorAll("ha-card").map((c) => c.getAttribute("role")) });
    """)
    assert res["before"] == "" and res["said"] == "No Current Data" and res["same"] and res["role"] == "status"
    assert res["cards"] == [None]  # the visible banner is not a second live region


@needs_node
def test_documentation_sections_are_headings_and_links_move_the_focus():
    """Each Documentation section is a heading with a button (the disclosure pattern); a section link opens its
    section and moves the focus to it; every table is named by the heading above it."""
    from js_i18n import i18n_prelude as prelude

    states = {"sensor.glucocraft_configuration": {"state": "ok", "attributes": {"unit": "mg/dL"}}}
    src = (
        prelude(areas=("frontend", "documentation"))
        + FAKE_DOM
        + DOCS
        + textwrap.dedent(f"""
        globalThis.matchMedia = () => ({{ matches: true }});
        const el = new (customElements.get("glucocraft-documentation"))(); el.setConfig({{}});
        el.hass = {{ states: {json.dumps(states)}, language: "en" }};
        const root = el.shadowRoot, heads = root.querySelectorAll("h2.sh");
        const buttons = heads.map((h) => h.querySelector("button"));
        const controls = buttons.every((b) => root.getElementById(b.getAttribute("aria-controls")));
        const link = root.querySelector('a.sec[data-sec="glossary"]');
        const closed = root.getElementById("glossary-body").hasAttribute("hidden");
        link.onclick({{ preventDefault() {{}} }});
        const tables = root.querySelectorAll("table").map((t) => t.getAttribute("aria-labelledby"));
        out({{ heads: heads.length, expanded: buttons.filter((b) => b.getAttribute("aria-expanded") === "true").map((b) => b.getAttribute("data-sec")),
          controls, closed, open: !root.getElementById("glossary-body").hasAttribute("hidden"),
          focus: root.activeElement?.getAttribute("data-sec"), pressed: root.activeElement?.getAttribute("aria-expanded"),
          tables, named: tables.every((id) => id && root.getElementById(id)) }});
    """)
    )
    out = node_json(src, prelude=False)
    # the Disclaimer and the Essentials are open at the start, and the Glossary after its link was followed
    assert out["heads"] >= 20 and out["expanded"] == ["disclaimer", "essentials", "glossary"] and out["controls"]
    assert out["closed"] and out["open"] and out["focus"] == "glossary" and out["pressed"] == "true"
    assert out["tables"] and out["named"]


@needs_node
def test_display_card_announces_a_page_change_and_stays_while_in_use():
    """After Next page, a live region that exists from the start names the page and says what it shows; the card does
    not return to the 6-hour page while one of its buttons has the focus or the pointer is over it."""
    out = _card(
        "glucocraft-display-card",
        extra="""
        const live = card.shadowRoot.querySelector("[data-live]");
        const before = live.textContent || "";
        card._go(1);
        const said = live.textContent;
        card._go(16);
        const text = live.textContent;
        const idle = card._inUse();
        card.shadowRoot.activeElement = {};
        return { before, said, text, idle, used: card._inUse() };
        """,
    )["extra"]
    assert out["before"] == ""
    assert out["said"].startswith("Page 2 of 17: 24 Hours. 24-hour graph: the history is loading.")
    assert out["text"] == "Page 17 of 17: About GlucoCraft."
    assert out["idle"] is False and out["used"] is True
    # the glucose block's button comes first, as it is drawn above the page buttons; its ring is drawn inside it
    assert CARD.index('data-kb="block"') < CARD.index('data-kb="prev"')
    assert '.kb[data-kb="block"]:focus-visible { outline-offset: -4px; }' in CARD


def test_the_pdf_reads_comparison_signs_in_words_and_has_bookmarks():
    """In the PDF, a text with a comparison sign carries its words as /ActualText, and the headings H1 and H2 are the
    document's outline (bookmarks)."""
    pytest.importorskip("reportlab")
    from glucocraft import report_pdf as P

    from test_report_outputs import _data

    pdf, _ = P.render(_data(14, False), True, False)
    assert b"/S /Span" in pdf and b"/ActualText" in pdf
    # in a table cell the unit is read in words as well, as on the Report page (the cell's /ActualText)
    assert b"less than 4 percent" in pdf
    assert b"/Outlines" in pdf and b"/PageMode /UseOutlines" in pdf


# ------------------------------------------------------------------------------------------------ the GMI cell
@needs_node
def test_the_rectangular_cards_gmi_in_both_units_is_one_line_at_14_px():
    """The rectangular card's GMI cell: in both A1C units the panels' compact form on one line at 14 px ("6.7%/49", as
    the 800 x 480 panel), in mmol/mol the value over its unit (17 and 14 px), in percent the value at 20 px; no text
    of the cell below 14 px (ACCESSIBILITY.md rule 3)."""
    extra = """
      const S = globalThis.glucocraftShared;
      const st = (m) => ({ mean: m, gmi: 3.31 + 0.02392 * m, sd: 30, cv: 25, tir: 70, titr: 50, low: 2, high: 20,
        clo: 0.5, chi: 5, min: 50, max: 300 });
      card._bf = { stats: [0, 1, 2, 3, 4, 5, 6].map(() => st(140)), trends: [], ts: [], val: [],
        at: Math.floor(Date.now() / 1000) };
      const cell = (a1c) => { S.settings.a1c = a1c; return [card._statRows(4)[1][3][1], card._graphArea(card._state(), 4)]; };
      return [cell("both"), cell("mmol_mol"), cell("percent")];
    """
    both, mmol, pct = _card("glucocraft-display-card-wide", config={"a1c_unit": "both"}, extra=extra)["extra"]
    # mean 140 mg/dL: GMI 6.7%, 49 mmol/mol (from the mean)
    assert both[0] == ["6.7%/49", "", 14] and re.search(r'font-size="14"[^>]*>6\.7%/49</text>', both[1])
    assert mmol[0] == ["49", "mmol/mol"]
    assert re.search(r'font-size="17"[^>]*>49</text>', mmol[1]) and re.search(r'font-size="14"[^>]*>mmol/mol<', mmol[1])
    assert pct[0] == ["6.7%", ""] and re.search(r'font-size="20"[^>]*>6\.7%</text>', pct[1])
    for _cells, svg in (both, mmol, pct):
        assert 'font-size="12"' not in svg


@needs_node
@pytest.mark.parametrize(
    "value, step, text",
    [
        (7, 0.5, "7.0"),
        (6.5, 0.5, "6.5"),
        (6, 0.5, "6.0"),
        (4, 2, "4"),
        (8, 2, "8"),
        (10, 1, "10"),
        (6.25, 0.25, "6.25"),
        (6, 0.25, "6.00"),
        (6.2, 0.2, "6.2"),
    ],
)
def test_a_mmol_axis_has_one_label_format(value, step, text):
    """One format per mmol/L axis: as many decimals as the axis's step has, on every label of the axis."""
    assert (
        _card(
            "glucocraft-display-card",
            value=127,
            zone="in_range",
            label="",
            extra=f"return card._axisText({value}, {step});",
        )["extra"]
        == text
    )
