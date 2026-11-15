# License: AGPL-3.0-only (LICENSE).
"""Accessibility and wording rules of the CGM Report page and PDF, the display card and the dashboard cards.

Rules under test:
  * every surface of the CGM Report that shows consensus targets (the PDF, summary and full, and the Report page)
    says for whom they are published: the Other groups sentence of the Consensus Ranges page, without its
    link, in the Notes of page 1;
  * the Report page draws its charts at the width of its content box, one unit per pixel, so that their 14 px labels
    stay 14 px at a 320 px wide screen; the display card's compact layout is as wide as the drawing's own box;
  * the published light-theme contrast figures are those of Home Assistant's current theme colors;
  * the graph range colors as drawn (60% over the graphs' black area) come close under color vision simulations,
    neighboring bands included, and the Documentation says so;
  * the Maintenance button's status icon and edge reach 3:1 on the button;
  * a "---" (no value) is read as "not available", and every check and cross mark carries its spoken word;
  * the zone word is in the text color, also after the stated sensor expiry: never gray;
  * the Change Over Time chart sentence and the display card's weekly sentence say "of each 14-day period", and the
    example sentence of ACCESSIBILITY.md is the one the display card produces;
  * a reading less than a minute old is "Measured less than a minute ago.".
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

import pytest
from glucocraft import i18n

from js_i18n import catalogue, documentation_text
from node_runner import NODE, needs_node, node_json
from test_accessibility import _card
from test_accessibility_checks import (
    BAND_CONTRACT,
    GLU2,
    HA_PRIMARY,
    HA_THEMES,
    JS,
    MACHADO,
    REPORT_PARTS_JS,
    STATUS_COLORS,
    STUBS,
    UI_STATES,
    ZONE_ORDER,
    _close_pairs,
    _eval,
    _texts,
    contrast,
    over,
)
from test_report_outputs import _data, _page_html

REPO = Path(__file__).resolve().parents[1]
ACCESSIBILITY = (REPO / "docs" / "ACCESSIBILITY.md").read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Text without whitespace, so that line breaks of the PDF and the page do not matter."""
    return re.sub(r"\s+", "", text)


# ------------------------------------------------------------------------------------------------ for whom the targets are
def _groups_sentence() -> str:
    """The Other groups sentence of the Consensus Ranges page, without its bold lead and without its link."""
    text = catalogue("frontend")["strategy.cx.groups"]
    text = text.replace("**Other groups.** ", "")
    return re.sub(r": \[the published article\]\([^)]*\)", "", text)


def test_the_report_carries_the_other_groups_sentence():
    body = i18n.t("report.page1.notes.groups.body")
    assert body == _groups_sentence()
    assert "adults in general" in body and "Home Assistant" not in body and "http" not in body
    assert i18n.t("report.page1.notes.groups.lead") == "Other groups:"


@pytest.mark.slow
@pytest.mark.parametrize("days, full", [(14, False), (30, True)])
def test_every_report_surface_with_consensus_targets_says_for_whom_they_are(days, full):
    from test_report_shown_values_and_texts import _pdf_pages

    body = _flat(i18n.t("report.page1.notes.groups.body"))
    d = _data(days, full)
    text = _flat(" ".join(_pdf_pages(d, full)))
    assert _flat(i18n.t("report.page1.notes.ranges.lead")) in text  # the PDF shows consensus targets
    assert body in text and _flat(i18n.t("report.page1.notes.groups.lead")) in text
    # the Report page, which shows the same targets, carries the same paragraph after the ranges and targets
    if NODE:
        page = _flat(re.sub(r"<[^>]+>", " ", _page_html(d)))
        assert body in page
        assert page.index(_flat(i18n.t("report.page1.notes.ranges.lead"))) < page.index(body)


# ------------------------------------------------------------------------------------------------ chart text size
def _report_at(card_width: int, days: int = 90) -> dict:
    """The Report page drawn in a card whose ha-card is card_width wide inside its border (clientWidth): the content
    box, the charts' view-box widths and the mini charts' width."""
    data = _data(days, False)
    return node_json(
        STUBS
        + REPORT_PARTS_JS
        + JS["report"]
        + textwrap.dedent(f"""
        const card = new (customElements.get("glucocraft-report"))(); card.setConfig({{}});
        card.shadowRoot.querySelector("ha-card").clientWidth = {card_width};
        card._hass = {{ states: {{}} }}; card._data = {json.dumps(data)}; card.draw();
        const html = REPORT_PARTS.map((s) => card.shadowRoot.querySelector(s).innerHTML).join("");
        const widths = [...html.matchAll(/<svg class="chart" viewBox="0 0 ([\\d.]+) /g)].map((m) => +m[1]);
        process.stdout.write("\\u0001" + JSON.stringify({{ cw: card._cw, widths, mini: card.miniW(), chart: card.chartW() }}));
    """)
    )


@needs_node
@pytest.mark.parametrize("card_width", [302, 342, 394, 1100])  # screens of about 320, 360, 412 px and a desktop
def test_report_page_charts_are_drawn_one_unit_per_pixel(card_width):
    out = _report_at(card_width)
    cw = card_width - 32  # the content box: ha-card's 16 px padding on each side
    assert out["cw"] == cw
    # the weekly trend's small charts: as many columns as the grid (minmax(200px, 1fr), gap 10 px) lays out, three at most
    cols = min(3, max(1, (cw + 10) // 210))
    column = (cw - 10 * (cols - 1)) / cols
    assert out["chart"] <= cw and out["mini"] <= column
    assert out["widths"] and set(out["widths"]) <= {out["chart"], out["mini"]}, out
    # the rendered size of the 14 px chart text: 14 px times the screen scale of its chart, never below 14 px
    for w in out["widths"]:
        shown = cw if w == out["chart"] else column
        assert 14 * shown / w >= 14, (w, shown)
    assert "svg.chart { width: 100%; height: auto; display: block; } svg text { font-size: 14px;" in JS["report"]


@needs_node
@pytest.mark.parametrize("tag, design", [("glucocraft-display-card", 480), ("glucocraft-display-card-wide", 800)])
def test_display_card_compact_layout_is_as_wide_as_its_drawing_box(tag, design):
    extra = textwrap.dedent("""
        const disp = root.querySelector(".disp"), out = [];
        const haCard = root.querySelector("ha-card");
        for (const [host, box] of [[304, 302], [304, 0], [DESIGN + 2, DESIGN], [DESIGN, DESIGN]]) {
          card.getBoundingClientRect = () => ({ width: host });
          disp.getBoundingClientRect = () => ({ width: box });
          card._resized();
          out.push([card._compactW, card._compactW ? svg.attrs.viewBox : "", haCard.style.borderWidth]);
        }
        return out;
    """).replace("DESIGN", str(design))
    out = _card(tag, extra=extra)["extra"]
    assert out[0][0] == 302 and out[0][1].startswith("0 0 302 ")  # the drawing's box, inside the card's border
    assert out[0][2] == ""  # narrower than the design: the border stays
    assert out[1][0] == 302  # before the box is laid out: the card's width less its 1 px border on each side
    assert out[2][0] == 0 and out[2][2] == ""  # room for the design width and the border: the design drawing
    # exactly the design width (the dashboard's column): the border is dropped, the design drawing stays
    assert out[3][0] == 0 and out[3][2] == "0px"
    assert "max-width: calc(${W}px + 2 * var(--ha-card-border-width, 1px))" in JS["display-card"]


def test_start_page_pair_keeps_the_display_card_at_its_design_width():
    """On the start page the display card and the pages card each keep their full size and stack when both do not
    fit, so that the display card shows the panel's own layout on every screen at least as wide as its design width;
    they never shrink side by side (each 360 px in a 1058 px window), which would make the card fall back to its
    compact layout."""
    ui = JS["ui"]
    assert ".pair > * { flex: 0 0 480px; max-width: 100%; min-width: 0; }" in ui
    assert ".pair > .wide { flex: 0 0 800px; }" in ui
    assert "flex-wrap: wrap" in ui and "0.8955" not in ui and "1.4925" not in ui


@needs_node
@pytest.mark.parametrize("width", [280, 302, 360, 412, 479])
def test_compact_status_bar_middle_text_never_overlaps_the_reading_age(width):
    """ "Custom ranges" in the compact layout's status bar never runs over a long reading age ("Reading: 10 min ago"),
    for example at 360 px: it is centered when it clears both side texts by a pad, otherwise centered in the room
    between them, and left out only without that room."""
    extra = textwrap.dedent("""
        states["binary_sensor.glucocraft_using_custom_ranges"] = { state: "on", last_updated: "a", attributes: {} };
        card.hass = { states, fetchWithAuth: async () => ({ ok: false }) };
        card._render();
        const html = svg.innerHTML, pick = (t) => {
          const m = html.match(new RegExp('<text x="([0-9.]+)"[^>]*>' + t + "<"));
          return m ? parseFloat(m[1]) : null; };
        const age = "Reading: 10 min ago", mid = "Custom ranges";
        return { ageX: pick(age), midX: pick(mid), ageW: textWidth(age, 16), midW: textWidth(mid, 16),
          // the rule itself: centered when it fits there, between the side texts when not, null without room
          rule: [midX(480, 12, 100, 0, 100), midX(360, 12, 173, 0, 119), midX(300, 12, 173, 0, 119)] };
    """)
    out = _card("glucocraft-display-card", age_min=10, compact=width, extra=extra)["extra"]
    assert out["rule"] == [240, (12 + 173 + 12 + 360 - 12 - 12) / 2, None]
    assert out["ageX"] == 12
    if width >= 360:  # the room is there (the case seen at 360 px): drawn, beside the age instead of over it
        assert out["midX"] is not None, out
    if out["midX"] is not None:  # drawn: a pad clear of the reading age and inside the bar
        assert out["midX"] - out["midW"] / 2 >= 12 + out["ageW"] + 12 - 0.01, out
        assert out["midX"] + out["midW"] / 2 <= width - 12 + 0.01, out
    else:  # left out only when the room between the side texts is too small
        assert width - 12 - 12 - (12 + out["ageW"] + 12) < out["midW"], out


# ------------------------------------------------------------------------------------------------ contrast figures
def test_published_link_button_and_focus_ring_figures_are_the_current_themes():
    light_text, light_bg = HA_THEMES["light"]
    dark_text, dark_bg = HA_THEMES["dark"]
    assert (light_text, light_bg, dark_text, dark_bg) == ("#141414", "#FFFFFF", "#E1E1E1", "#1C1C1C")
    rule2 = " ".join(ACCESSIBILITY.split("## 2. Contrast", 1)[1].split("## 3.", 1)[0].split())
    # the theme colors are stated
    assert (
        "light theme's text color #141414 on white cards (#FFFFFF), the dark theme's text color #E1E1E1 on cards #1C1C1C"
        in rule2
    )

    def fig(x: float) -> str:
        return f"{x:.2f}:1"

    link = {name: [contrast(over(p, text, 0.5), bg) for p in HA_PRIMARY] for name, (text, bg) in HA_THEMES.items()}
    button = [contrast("#FFFFFF", over(p, "#000000", 0.65)) for p in HA_PRIMARY]
    ring = {name: contrast(text, bg) for name, (text, bg) in HA_THEMES.items()}
    assert (
        f"a link reaches {fig(link['light'][0])} and {fig(link['light'][1])} on the light theme's cards and "
        f"{fig(link['dark'][0])} and {fig(link['dark'][1])} on the dark theme's cards" in rule2
    )
    assert f"filled button reaches {fig(button[0])} and {fig(button[1])}" in rule2
    assert f"({fig(ring['light'])} on the light theme's cards and {fig(ring['dark'])} on the dark theme's" in rule2
    assert (fig(link["light"][0]), fig(link["light"][1]), fig(ring["light"])) == ("8.08:1", "7.08:1", "18.42:1")


# ------------------------------------------------------------------------------------------------ color vision
def _drawn(color: str) -> str:
    """A graph range color as drawn: 60% over the graphs' black area, composited to 8 bits per channel."""
    return "#" + "".join(f"{round(v * 255):02X}" for v in over(color))


DRAWN_PAIRS = {
    "protanopia": {("very_low", "low"): 8.5, ("low", "near_low"): 7.5, ("low", "near_high"): 7.5,
                   ("near_low", "tight_range"): 8.8, ("near_low", "very_high"): 4.7, ("tight_range", "near_high"): 8.8,
                   ("tight_range", "very_high"): 4.5, ("near_high", "very_high"): 4.7},
    "deuteranopia": {("very_low", "near_low"): 7.3, ("very_low", "near_high"): 7.3, ("low", "near_low"): 7.9,
                     ("low", "tight_range"): 8.8, ("low", "near_high"): 7.9, ("low", "very_high"): 8.8,
                     ("near_low", "tight_range"): 8.6, ("tight_range", "near_high"): 8.6,
                     ("tight_range", "very_high"): 7.0},
    "tritanopia": {("very_low", "low"): 9.5, ("near_low", "tight_range"): 8.5, ("tight_range", "near_high"): 8.5},
}  # fmt: skip


@pytest.mark.parametrize("kind", list(MACHADO))
def test_color_vision_graph_bands_as_drawn(kind):
    """As drawn, neighboring bands come close under every simulation: the graphs are read by their number scale."""
    pairs = _close_pairs({z: _drawn(c) for z, c in BAND_CONTRACT.items()}, kind)
    assert pairs == DRAWN_PAIRS[kind]
    assert any(abs(ZONE_ORDER.index(a) - ZONE_ORDER.index(b)) == 1 for a, b in pairs), pairs


def test_the_documentation_describes_the_bands_as_drawn():
    docs = " ".join(documentation_text().split())
    closest = min((v, k) for kind in MACHADO for k, v in DRAWN_PAIRS[kind].items())
    assert closest == (4.5, ("tight_range", "very_high"))
    assert (
        "In the graph range colors as drawn (at 60% over the graph's black area), several pairs come close under each "
        "simulation, among them bands that lie next to each other (the closest: the tight-range and the very-high band "
        "with protanopia, difference 4.5); a graph is therefore not read by its colors alone: its number scale gives the "
        "glucose value of each band, and the white glucose line is read against that scale." in docs
    )
    assert "never lie next to each other" not in docs


# ------------------------------------------------------------------------------------------------ the Maintenance status
@needs_node
def test_the_maintenance_status_icon_and_edge_reach_3_to_1_on_the_button():
    out = _eval(
        "ui",
        "return { ok: statusEdge(STATUS.ok), alert: statusEdge(STATUS.alert), palettes: PAGE_PALETTES, "
        "status: [STATUS.ok, STATUS.alert] };",
    )
    assert out["ok"] == "color-mix(in srgb, #43A047 80%, var(--gp-fg))"
    share = 0.8
    # (button background, text color): the card's black and white palettes and Home Assistant's default themes
    buttons = {
        name: (
            re.search(r"--gp-row: (#[0-9a-f]{3,6})", css).group(1),
            re.search(r"--gp-fg: (#[0-9a-f]{3,6})", css).group(1),
        )
        for name, css in out["palettes"].items()
        if name != "theme"
    }
    buttons["theme light"] = ("#E5E5E5", HA_THEMES["light"][0])
    buttons["theme dark"] = ("#282828", HA_THEMES["dark"][0])

    def six(h: str) -> str:
        return "#" + "".join(c * 2 for c in h[1:]) if len(h) == 4 else h

    for col in out["status"]:
        assert col in STATUS_COLORS
        for name, (bg, fg) in buttons.items():
            assert contrast(over(col, six(fg), share), six(bg)) >= 3, (col, name)
    assert contrast("#43A047", "#E5E5E5") < 3  # why the status color itself is not used
    # the drawn button: the icon and the edge in that shade
    html = _eval(
        "ui",
        "const el = new (customElements.get('glucocraft-pages'))(); el.setConfig({}); el._issues = 0; "
        f"el._hass = {{ states: {json.dumps(UI_STATES)} }}; el.draw(); return el.shadowRoot.innerHTML;",
    )
    button = re.search(r'<a class="btn st"[^>]*>.*?</a>', html, re.DOTALL).group(0)
    assert "border-left-color:color-mix(in srgb, #43A047 80%, var(--gp-fg))" in button
    assert 'style="color:color-mix(in srgb, #43A047 80%, var(--gp-fg))"' in button and '#43A047"' not in button


# ------------------------------------------------------------------------------------------------ "---" and the marks
def _states_with_marks() -> dict:
    """The dashboard states with a met and a not-met consensus target, the shorter periods without values, and the
    Change Over Time data of 26 weeks."""
    states = json.loads(json.dumps(UI_STATES))
    states["sensor.glucocraft_14d_consensus_ranges"]["attributes"]["time_below_70"] = 5.0  # not met: <4%
    week = {"tir": 80.2, "titr": 60.1, "below": 5.0, "very_low": 0.5, "above": 14.8, "mean": 121, "cv": 24.8,
            "gmi": 6.2, "sd": 30, "min": 58, "max": 260, "active": 95, "active_ok": True}  # fmt: skip
    states["sensor.glucocraft_change_over_time"] = {
        "state": "ok",
        "last_updated": "a",
        "attributes": {
            "consensus": {
                "weeks14": [week] * 26,
                "weeks7": [week] * 26,
                "week_ends": [None] * 26,
                "compare": {"last_4_weeks": week, "weeks_9_13_ago": week, "weeks_22_26_ago": week},
            }
        },
    }
    return states


MARK_CARDS = [
    {"type": "glucocraft-readout", "view": "consensus", "hero": True, "path": "/glucocraft-home/consensus"},
    {"type": "glucocraft-pages"},
    {"type": "glucocraft-stats-table", "periods": ["6h", "24h", "7d", "14d"]},
    {"type": "glucocraft-statistics"},
    {"type": "glucocraft-change", "basis": "consensus"},
    {"type": "glucocraft-core", "basis": "consensus"},
]


@pytest.fixture(scope="module")
def mark_cards() -> dict[str, str]:
    if not NODE:
        pytest.skip("node not available")
    return node_json(
        STUBS
        + GLU2
        + JS["ui"]
        + textwrap.dedent(f"""
        const states = {json.dumps(_states_with_marks(), ensure_ascii=False)};
        const hass = {{ states, user: {{ id: "u" }}, fetchWithAuth: async () => ({{ ok: true, arrayBuffer: async () => glu2() }}) }};
        globalThis.localStorage = {{ getItem: () => null, setItem() {{}} }};
        const out = {{}};
        for (const c of {json.dumps(MARK_CARDS)}) {{
          const el = new (customElements.get(c.type))(); el.setConfig(c); el._w = 700; el._hass = hass; el.hass = hass;
          await el.draw?.();
          await new Promise((r) => setTimeout(r, 0));
          let h = el.shadowRoot.innerHTML;
          for (const b of c.type === "glucocraft-statistics" ? ["lr", "pt"] : [])
            h = h.replace(`<div class="${{b}}"></div>`, `<div class="${{b}}">${{el.shadowRoot.querySelector("." + b).innerHTML}}</div>`);
          out[c.type] = h;
        }}
        process.stdout.write("\\u0001" + JSON.stringify(out));
    """)
    )


@needs_node
def test_every_no_value_mark_is_read_as_not_available(mark_cards):
    seen = 0
    for name, markup in mark_cards.items():
        texts = _texts(markup)
        assert "---" not in texts.read_text, (name, texts.read_text[:300])
        seen += markup.count('<span aria-hidden="true">---</span><span class="sr">not available</span>')
    assert seen >= 3  # the 6-hour, 24-hour and 7-day cells without values
    # the GRI row of the statistics table, where the shorter periods have no GRI
    table = mark_cards["glucocraft-stats-table"]
    gri = re.search(r'<tr><th scope="row" class="l">[^<]*GRI.*?</tr>', table, re.DOTALL)
    assert gri and '<span class="sr">not available</span>' in gri.group(0)


@needs_node
def test_every_check_and_cross_mark_carries_its_spoken_word(mark_cards):
    seen = {"✓": 0, "✗": 0}
    for name, markup in mark_cards.items():
        for m in re.finditer("[✓✗]", markup):
            rest = markup[m.end() :]
            stop = min((i for i in (rest.find(s) for s in ("</td>", "</tr>", "</a>", "</div>")) if i >= 0), default=400)
            words = re.sub(r"<[^>]+>", " ", rest[:stop])
            # (a legend names both marks: each must find its own word)
            said = re.search(r"(?<!not )meets" if m.group(0) == "✓" else r"does not meet", words)
            assert said, (name, m.group(0), words[:200])
            seen[m.group(0)] += 1
        # a mark that a screen reader would read as a symbol stands right before its word ("✓ meets")
        for m in re.finditer("[✓✗]", _texts(markup).read_text):
            assert _texts(markup).read_text[m.end() :].lstrip().startswith(("meets", "does not meet")), name
    assert seen["✓"] >= 3 and seen["✗"] >= 3, seen


# ------------------------------------------------------------------------------------------------ the zone word
@needs_node
@pytest.mark.parametrize("expired", [False, True])
def test_the_zone_word_keeps_the_text_color_after_the_stated_expiry(expired):
    """The zone word is drawn in the text color, also after the stated sensor expiry while readings still arrive: no
    gray anywhere (the red status bar carries the expiry), as on the panels."""
    out = _eval(
        "display-card",
        "const card = new (customElements.get('glucocraft-display-card'))(); card.setConfig({}); "
        f"return card._zoneSlot({{ slotWord: 'Low', expired: {json.dumps(expired)} }}, 62, 100, {{ y: 10, size: 40 }}, "
        "300);",
    )
    assert "Low" in out and "var(--gc-fg)" in out and "var(--gc-old)" not in out, out


def test_the_accessibility_page_says_the_zone_word_is_never_gray():
    text = " ".join(ACCESSIBILITY.split())
    assert "the zone word is drawn in the text color" in text
    assert "in gray, as the value" not in text
    glossary = " ".join(documentation_text().split())
    assert (
        "after the stated sensor expiry, while readings still arrive, the value and the zone word look as they did"
        " before it" in glossary
    )
    assert "shown in gray" not in glossary and "in gray, as the value" not in glossary


# ------------------------------------------------------------------------------------------------ the weekly sentences
@needs_node
def test_the_display_cards_weekly_sentence_is_the_published_example():
    example = "Time in range of each 14-day period, last 26 weeks: from 81.0 to 93.0 percent."
    assert f'"{example}"' in " ".join(ACCESSIBILITY.split())
    tir = [93.0 - 12.0 * i / 25 for i in range(26)]
    trends = [{"mean": 120, "tir": round(v, 1)} for v in tir]
    extra = f"card._bf = {{ trends: {json.dumps(trends)}, stats: [] }}; return card._describeWeekly(0);"
    said = _card("glucocraft-display-card", extra=extra)["extra"]
    assert said.startswith(example), said


@needs_node
def test_the_change_over_time_chart_sentence_says_each_14_day_period_and_the_7_day_dots():
    states = _states_with_marks()
    weeks = [
        dict(w, tir=round(93.0 - 12.0 * i / 25, 1))
        for i, w in enumerate(states["sensor.glucocraft_change_over_time"]["attributes"]["consensus"]["weeks14"])
    ]
    data = states["sensor.glucocraft_change_over_time"]["attributes"]["consensus"]
    data["weeks14"] = weeks
    script = (
        "const el = new (customElements.get('glucocraft-change-chart'))(); el.setConfig({ metric: 'tir', basis: 'consensus' }); "
        f"el._w = 700; el._hass = {{ states: {json.dumps(states)} }}; el.draw(); "
        "const with7 = el.shadowRoot.innerHTML; "
        f"const s = {json.dumps(states)}; s['sensor.glucocraft_change_over_time'].attributes.consensus.weeks7 = []; "
        "el._hass = { states: s }; el.draw(); return [with7, el.shadowRoot.innerHTML];"
    )
    with7, without7 = _eval("ui", script)
    label = re.search(r'role="img" aria-label="([^"]*)"', with7).group(1)
    assert label.endswith(
        "of each 14-day period, last 26 weeks: from 81.0 to 93.0 percent. Faint dots: the 7-day values of the same weeks."
    ), label
    assert "by week" not in label
    label = re.search(r'role="img" aria-label="([^"]*)"', without7).group(1)
    assert label.endswith("from 81.0 to 93.0 percent."), label
    for key in ("ui.chart.said.other", "ui.chart.said_min_max.other", "card.describe.weekly_range.other"):
        assert "of each 14-day period, last {count} weeks:" in catalogue("frontend")[key]
    assert not [
        k for k, v in catalogue("frontend").items() if "by week" in v and k.startswith(("ui.chart", "card.describe"))
    ]


# ------------------------------------------------------------------------------------------------ the age of a reading
@needs_node
@pytest.mark.parametrize("age, said", [(0, "Measured less than a minute ago."), (3, "Measured 3 minutes ago.")])
def test_a_reading_under_a_minute_old_on_the_display_card(age, said):
    text = _card("glucocraft-display-card", age_min=age)["label"]
    assert said in text and "Measured 0 minutes ago." not in text


@needs_node
@pytest.mark.parametrize("seconds, said", [(20, "Measured less than a minute ago."), (180, "Measured 3 minutes ago.")])
def test_a_reading_under_a_minute_old_on_the_statistics_card(seconds, said):
    states = json.loads(json.dumps(UI_STATES))
    script = textwrap.dedent(f"""
        const states = {json.dumps(states)};
        states["sensor.glucocraft_last_reading_time"] = {{ state: new Date(Date.now() - {seconds} * 1000).toISOString(), last_updated: "a", attributes: {{}} }};
        const el = new (customElements.get("glucocraft-statistics"))(); el.setConfig({{}}); el._w = 700;
        el._hass = {{ states }}; el.hass = el._hass; await el.draw?.();
        return el.shadowRoot.querySelector(".lr").innerHTML + el.shadowRoot.innerHTML;
    """)
    html = _eval("ui", script)
    assert said in html and "Measured 0 minutes ago." not in html
