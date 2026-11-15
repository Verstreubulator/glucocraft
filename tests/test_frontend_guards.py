# License: AGPL-3.0-only (LICENSE).
"""Small rules of the frontend (custom_components/glucocraft/www), each held by a test.

- The graph cards draw nothing before the language catalogues are loaded, also when their width changes first
  (the ResizeObserver keeps the width; set hass draws once the texts are there).
- The display card's stylesheet keeps the design width after setConfig runs again in the compact layout, so that the
  card can return to the design layout when the screen grows wider.
- The display card clears the source name when the Active source sensor is "unknown" (no source known) and keeps it
  while the sensor is unavailable (GlucoCraft restarting or reloading) or missing.
- The CSV export asks once for the action's response; an error goes to the status line, never into a second export.
- The GMI axis spread in mmol/mol uses the GMI rule's slope, not a bare constant.
- No www file holds an invisible private-use character (strategy.js writes its code-span sentinels as escapes), and
  the Markdown code spans still come back in place.
- The CGM Report page escapes the day, event and GRI zone values of the report data like every other value.
"""

from __future__ import annotations

import json
import re

from node_runner import needs_node
from test_accessibility_checks import JS, WWW, _eval

SOURCE = "sensor.glucocraft_active_source"


# ------------------------------------------------------------------------------------------------ graph cards
@needs_node
def test_a_graph_card_does_not_draw_before_its_texts_are_loaded():
    out = _eval(
        "ui",
        """
        let observed = null;
        globalThis.ResizeObserver = class { constructor(cb) { observed = cb; } observe() {} disconnect() {} };
        const card = new GlucoCraftHistory();
        card.setConfig({ hours: 6 });
        let draws = 0;
        card.draw = () => { draws += 1; };
        card._hass = { states: {} };
        card.connectedCallback();
        const ready = globalThis.glucocraftI18n.ready;
        globalThis.glucocraftI18n.ready = () => false;   // the catalogues still loading
        observed();
        const before = [draws, card._w];
        globalThis.glucocraftI18n.ready = ready;
        card.getBoundingClientRect = () => ({ width: 700 });
        observed();
        card.disconnectedCallback();
        return { before, after: [draws, card._w] };
        """,
    )
    # before the texts: no draw, but the width is kept for the draw that follows; after them: a new width draws
    assert out == {"before": [0, 600], "after": [1, 700]}


# ------------------------------------------------------------------------------------------------ display card
def _display(tag_class: str, body: str) -> object:
    return _eval("display-card", f"const card = new {tag_class}();\n" + body)


@needs_node
def test_set_config_in_the_compact_layout_keeps_the_design_width():
    for cls, width in (("GlucoCraftDisplayCard", 480), ("GlucoCraftDisplayCardWide", 800)):
        out = _display(
            cls,
            """
            card.setConfig({});
            // the layout a compact render leaves in use (a card narrower than its design width)
            card._compactL = { ...card.D, W: 360, H: 0 };
            card.setConfig({});
            return { compact: card.L.W, css: card.shadowRoot.innerHTML.match(/max-width: calc\\((\\d+)px/)[1] };
            """,
        )
        assert out == {"compact": 360, "css": str(width)}, cls


@needs_node
def test_the_source_name_is_cleared_when_no_source_is_known():
    states = {
        "sensor.glucocraft_last_glucose_reading": {"state": "120", "last_updated": "a", "attributes": {}},
        SOURCE: {"state": "Test CGM", "last_updated": "a", "attributes": {"detected_interval": 60}},
    }
    out = _display(
        "GlucoCraftDisplayCard",
        f"""
        card.setConfig({{}});
        const states = {json.dumps(states)};
        const seen = [];
        const at = (state) => {{
          if (state === null) delete states[{json.dumps(SOURCE)}];
          else states[{json.dumps(SOURCE)}] = {{ ...states[{json.dumps(SOURCE)}], state }};
          card._hass = {{ states }};
          card._state();
          seen.push(card._kept?.source ?? null);
        }};
        at("Test CGM"); at("unavailable"); at(null); at("unknown"); at("Other CGM");
        return seen;
        """,
    )
    # kept while unavailable (restarting) or missing; "unknown" (no source known) clears it; a new name shows
    assert out == ["Test CGM", "Test CGM", "Test CGM", "", "Other CGM"]


# ------------------------------------------------------------------------------------------------ CSV export
@needs_node
def test_the_csv_export_is_called_once_and_shows_an_error():
    out = _eval(
        "ui",
        """
        const calls = [];
        const card = new GlucoCraftExport();
        card.setConfig({});
        card.draw = () => {};
        card._hass = {
          states: {},
          callWS: async (msg) => { calls.push(["ws", msg.service, msg.return_response]); throw new Error("response failed"); },
          callService: async (...a) => { calls.push(["service", a[1]]); },
        };
        await card.run();
        return { calls, ok: !!card._ok, status: String(card._status || "") };
        """,
    )
    assert out["calls"] == [["ws", "export_csv", True]]
    assert not out["ok"] and out["status"]


# ------------------------------------------------------------------------------------------------ shared rules
@needs_node
def test_the_gmi_axis_spread_follows_the_gmi_rule():
    slope = _eval("ui", "return gmiPctToMmol(1) - gmiPctToMmol(0);")
    assert abs(slope - 10.92) < 0.005
    assert "* 10.92" not in JS["ui"]  # the number only in its comment


def test_no_www_file_holds_an_invisible_character():
    for path in sorted(WWW.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        assert not re.search("[" + chr(0xE000) + "-" + chr(0xF8FF) + "]", text), path.name  # the private-use area


@needs_node
def test_markdown_code_spans_keep_their_numbers():
    html = _eval("strategy", 'return mdInline("Set `limit 15` to 15 minutes");')
    assert html == "Set <code>limit 15</code> to 15 minutes"


# ------------------------------------------------------------------------------------------------ CGM Report page
def test_the_report_page_escapes_its_day_event_and_zone_values():
    src = JS["report"]
    for raw in ("? w[2] :", "${c[0]}", "${c[2]}", "(c) => c.events)", "(c) => c.level2)", "(c) => c.blocks[k])",
                "(c) => c.night)", "zone: s.gri_zone", "${p.gri_zone}", "${b.gri_zone}", "? b.events :"):  # fmt: skip
        assert raw not in src, raw
