# License: AGPL-3.0-only (LICENSE).
"""Rules written once and the small behaviors of the cards, the report and the statistics records.

- Black or white text on a color is one rule in glucocraft-shared.js (gcOnColor), used by the display card and the
  dashboard cards, and it gives the integration's contrast.foreground on every status color and around the tie.
- The headline card is drawn every minute only for the status view (it reads the source, which it does not watch);
  the other views and the pages card are drawn when an entity they watch changes.
- Maintenance, Sensor and Sources: the source in use carries its mark with its word ("✓ in use").
- The CGM Report: a difference of two values as shown keeps their decimals and has no sign at zero, in the PDF's
  _signed as before; the page fills only its own threshold placeholders.
- A PeriodStats record carries every value it is read for (covered seconds, combined bands, GMI in mmol/mol): the
  statistics and the CGM Report read them without a fallback.
"""

from __future__ import annotations

import json

import pytest
from glucocraft import contrast
from glucocraft import report_pdf as P
from glucocraft.const import STATUS_COLORS
from glucocraft.regional import Regional
from glucocraft.statistics import PeriodStats, compute_period_stats
from glucocraft.storage import GlucoseReading

from js_i18n import catalogue
from node_runner import needs_node
from test_accessibility_checks import _eval
from test_dashboard_text_card import ATTRS, _cards, _dashboard, _jinja
from test_report_outputs import _page

FE = catalogue("frontend")
GRAYS = ["#000000", "#FFFFFF", "#757575", "#767676"]  # black, white and the two grays beside the tie


# ------------------------------------------------------------------------------------------------ black or white
@needs_node
@pytest.mark.parametrize("card", ["display-card", "ui"])
def test_black_or_white_on_a_color_is_the_integrations_rule(card):
    colors = sorted(set(STATUS_COLORS.values())) + GRAYS
    expected = [contrast.foreground(c).lower() for c in colors]
    if card == "display-card":  # the status bar: numbers 0xRRGGBB
        out = _eval(card, f"return {json.dumps(colors)}.map((c) => hex(onColor(parseInt(c.slice(1), 16))));")
    else:  # the LO / HI chip of the Statistics card: "#RRGGBB"
        out = _eval(card, f"return {json.dumps(colors)}.map((c) => onColorHex(c));")
    assert out == expected


# ------------------------------------------------------------------------------------------------ the headline card
@needs_node
def test_no_headline_card_is_drawn_by_the_minute():
    out = _eval(
        "ui",
        "const views = ['status', 'today', 'consensus', 'personal', 'report'];"
        "const timed = views.map((view) => { const c = new (customElements.get('glucocraft-readout'))();"
        "  c.setConfig({ view, hero: true }); return c.timed(); });"
        "const pages = new (customElements.get('glucocraft-pages'))(); pages.setConfig({});"
        "return [...timed, pages.timed()];",
    )
    # every view reads only the entities it watches (the status view no longer reads the source, which the page's
    # sources table states once): none is drawn by the minute
    assert out == [False, False, False, False, False, False]


# ------------------------------------------------------------------------------------------------ Maintenance
@needs_node
def test_the_source_in_use_carries_its_mark_with_its_word():
    name = FE["strategy.maintenance.sources.table_name"]
    card = next(c for c in _cards(_dashboard(ATTRS)) if c.get("tables") == [name])
    in_use = FE["strategy.maintenance.sources.in_use"]
    assert in_use.startswith("✓ ") and len(in_use) > 2
    # both rows: the mark with its word for the source in use, an empty cell for the other
    for role in ("p", "s"):
        assert f"{{{{ '{in_use}' if {role}.in_use else '' }}}}" in card["content"], role
    text = _jinja().from_string(card["content"]).render()  # the primary source, in use (ATTRS)
    assert f"(primary) | Tue 11/3 8:00 AM | {in_use} |" in text and text.count("✓") == 1


# ------------------------------------------------------------------------------------------------ the CGM Report
@pytest.mark.parametrize("dec", [".", ","])
def test_a_difference_of_shown_values_keeps_their_decimals_and_no_sign_at_zero(monkeypatch, dec):
    monkeypatch.setitem(P._RG, "rg", Regional(decimal_separator=dec))
    for v in (0.0, 1.0, -1.0, 1.5, -1.5, 0.05, -0.05, 12.34, -12.34, 100.0, 2.5, 69.97 - 65.4):
        d = P.round_half_up(v, P._places(round(v, 2)))
        shown = (f"{d:+g}" if d else "0").replace(".", dec)  # the :g form with its sign, the separator in use
        assert P._signed(d, P._places(d)) == shown, (v, d)


@needs_node
def test_the_page_fills_only_its_own_threshold_placeholders():
    out = _page("RT_THR = { t70: '70', unit: 'mg/dL' }; return rL('{t70} {unit} {t999} {constructor} {toString}');")
    assert out == "70 mg/dL {t999} {constructor} {toString}"


# ------------------------------------------------------------------------------------------------ PeriodStats
def test_a_period_record_carries_every_value_it_is_read_for():
    readings = [GlucoseReading(1_000_000 + k * 300, 60.0 + k) for k in range(12)]
    s = compute_period_stats(readings, "1h", 3600, 300)
    assert s.covered_seconds == 3600 and s.sensor_active_exact == 100.0
    assert s.time_below_low == pytest.approx(s.time_low + s.time_critical_low)
    assert s.time_above_high == pytest.approx(s.time_high + s.time_critical_high)
    assert s.gmi_mmol_mol is not None
    # no field is filled in afterwards: a record without them cannot be made
    with pytest.raises(TypeError):
        PeriodStats(**{k: v for k, v in vars(s).items() if k != "covered_seconds"})
