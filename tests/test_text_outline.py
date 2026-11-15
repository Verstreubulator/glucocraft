# License: AGPL-3.0-only (LICENSE).
"""Outline text: GlucoCraft's setting text_outline and the 1-pixel text edge of the display card.

Rules under test (docs/ACCESSIBILITY.md rules 2 and 8, docs/DISPLAY_CONTRACT.md):
- The setting is off by default, read through settings.py (a stored value that is not True or False is off) and
  published as the configuration attribute text_outline; it is a field of Configure > Display card and wall panels.
- Off: the display card draws plain text. On: the graph labels, the glucose value, LO / HI and every other text in a
  zone color get a 1-pixel edge (an SVG stroke of 2 px painted under the fill), black around a light text and white
  around a dark one; the page text in the card's own colors never does. A change redraws the card.
The LCD panels' side is in tests/test_panel_configs.py (Outline text).
"""

from __future__ import annotations

import asyncio
import re

import pytest
from glucocraft import const as C
from glucocraft import contrast
from glucocraft.settings import setting

from node_runner import needs_node
from test_accessibility import PALETTE, _card
from test_config_flow_options import (
    _defaults_input,
    _options_flow,
    _screen,
    _stored,
    schemas,  # noqa: F401 (the fixture)
)
from test_settings_harness import build, entry, tz_patch  # noqa: F401 (the fixture)

LCD_CARDS = ["glucocraft-display-card", "glucocraft-display-card-wide"]
TEXT = re.compile(r"<text ([^>]*)>([^<]*)</text>")


# ------------------------------------------------------------------------------------------------ the setting
@pytest.mark.parametrize("stored, read", [(None, False), (True, True), (False, False), ("true", False), (1, False)])
def test_text_outline_setting_off_by_default(stored, read):
    data = {} if stored is None else {C.CONF_TEXT_OUTLINE: stored}
    assert setting(data, C.CONF_TEXT_OUTLINE) is read


@pytest.mark.usefixtures("tz_patch")
@pytest.mark.parametrize("on", [False, True])
def test_text_outline_is_published(tmp_path, on):
    run = build(tmp_path, entry(**({C.CONF_TEXT_OUTLINE: True} if on else {})))
    assert run.config()["text_outline"] is on


@pytest.mark.usefixtures("schemas")
def test_text_outline_on_the_display_screen():
    flow, shown, saved = _options_flow()
    schema = _screen(flow, shown, "displays")
    fields = {k.key: k for k in schema}
    assert fields[C.CONF_TEXT_OUTLINE].default is False
    asyncio.run(flow.async_step_displays({**_defaults_input(schema), C.CONF_TEXT_OUTLINE: True}))
    assert saved[-1][C.CONF_TEXT_OUTLINE] is True
    flow, shown, saved = _options_flow({**_stored(), C.CONF_TEXT_OUTLINE: True})
    schema = _screen(flow, shown, "displays")
    assert {k.key: k for k in schema}[C.CONF_TEXT_OUTLINE].default is True
    asyncio.run(flow.async_step_displays(_defaults_input(schema)))
    assert saved[-1][C.CONF_TEXT_OUTLINE] is True


# ------------------------------------------------------------------------------------------------ the display card
def _texts(svg: str) -> list[tuple[dict[str, str], str]]:
    """Every text of a drawing: (its attributes, its text)."""
    return [(dict(re.findall(r'([\w:-]+)="([^"]*)"', attrs)), body) for attrs, body in TEXT.findall(svg)]


def _edge(fill: str) -> str:
    """The edge color of a text color: black or white, whichever contrasts more with it."""
    return contrast.foreground(fill.upper()).lower()


@needs_node
@pytest.mark.parametrize("tag", LCD_CARDS)
def test_text_outline_off_draws_plain_text(tag):
    for config in ({}, {"text_outline": False}):
        svg = _card(tag, value=250, zone="very_high", config=config)["svg"]
        assert "paint-order" not in svg and not any("stroke" in a for a, _ in _texts(svg))


@needs_node
@pytest.mark.parametrize("tag", LCD_CARDS)
@pytest.mark.parametrize("zone, value", [("very_high", 260), ("near_low", 65), ("tight_range", 110), ("very_low", 50)])
def test_text_outline_on_edges_the_value_in_the_opposite_lightness(tag, zone, value):
    svg = _card(tag, value=value, zone=zone, config={"text_outline": True})["svg"]
    texts = _texts(svg)
    fill = PALETTE[zone].lower()
    shown = [a for a, body in texts if body == str(value) and a.get("fill") == fill]
    assert shown, f"the value {value} is not drawn"
    for attrs in shown:
        assert attrs["fill"] == fill and attrs["stroke"] == _edge(fill), attrs
        assert attrs["stroke-width"] == "2" and attrs["paint-order"] == "stroke"
    # black around a light text, white around a dark one
    assert _edge("#FFC107") == "#000000" and _edge("#1565C0") == "#ffffff"


@needs_node
@pytest.mark.parametrize("tag", LCD_CARDS)
def test_text_outline_on_edges_lo_hi(tag):
    """At the sensor's limit the value's place shows the zone word in the zone color (the LO / HI screen): it takes the
    edge as every text in a zone color."""
    for limit, zone, text in (("low", "very_low", "Very low"), ("high", "very_high", "Very high")):
        svg = _card(
            tag, value=40 if limit == "low" else 400, zone=zone, label=text, limit=limit, config={"text_outline": True}
        )
        attrs = [a for a, body in _texts(svg["svg"]) if body == text and a.get("fill") == PALETTE[zone].lower()]
        assert attrs and all(a["stroke"] == _edge(PALETTE[zone]) for a in attrs), text


@needs_node
@pytest.mark.parametrize("tag", LCD_CARDS)
def test_text_outline_on_edges_every_graph_label_and_no_page_text(tag):
    """The 6-hour graph (its scale, hour labels and title); every graph draws its labels with the same text call."""
    svg = _card(tag, config={"text_outline": True})["svg"]
    labels = [a for a, _ in _texts(svg) if a.get("fill") in ("#ffffff", "#bdbdbd")]
    assert labels, "no graph label drawn"
    assert all(a.get("stroke") == "#000000" and a.get("stroke-width") == "2" for a in labels)
    page_text = [a for a, _ in _texts(svg) if "fill" not in a]  # the card's own colors (CSS variables)
    assert page_text and not any("stroke" in a for a in page_text)


@needs_node
def test_text_outline_changes_redraw_the_card():
    """The setting is part of what the card compares before it redraws (the redraw signature), as are the settings
    Fade the words when there is no current reading and Trend shown as."""
    from test_accessibility import CARD

    assert "GC_OUTLINE = ca.text_outline === true;" in CARD
    assert "GC_FLASH = ca.flash_no_current_data !== false;" in CARD
    assert re.search(r"`;\$\{GC_OUTLINE\};\$\{GC_FLASH\};\$\{GC_TREND_WORD\};\$\{!!this\._attention\}`;", CARD)
