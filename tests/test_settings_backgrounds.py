# License: AGPL-3.0-only (LICENSE).
"""The graph range colors, the two backgrounds, the text color on them and the contrast note of the settings screen.

Rules under test (docs/DISPLAY_CONTRACT.md, docs/ACCESSIBILITY.md rules 2 and 8):
- band_colors: seven colors of their own for the graphs' range bands, by default the CGM Report's colors, from one
  table (const.BAND_COLORS) that the PDF's colors are built from; the zone colors are not affected.
- card_background ("theme" or "#RRGGBB") and panel_background ("#RRGGBB"), black by default; card_foreground and
  panel_foreground are black or white, whichever contrasts more (None with the theme).
- Every stored value is read through settings.py: a value that is not valid is the default.
- The Display card and wall panels screen: the collapsed section Graph range colors, the card background as the theme
  or a color, the wall panel background, and the contrast note of the saved colors in one sentence: the zone colors
  and the text color on each background, and the white graph labels on each graph range color as drawn (60 % over the
  graph's black area, the same on every background, so each band is listed once).
"""

from __future__ import annotations

import asyncio
import time

import pytest
from glucocraft import config_flow as cf
from glucocraft import const as C
from glucocraft import contrast as K
from glucocraft import report_pdf as P
from glucocraft import settings as S

from test_config_flow_options import (
    OPTIONS,
    _defaults_input,
    _options_flow,
    _screen,
    _stored,
    schemas,  # noqa: F401 (the fixture)
)
from test_settings_harness import build, entry, tz_patch  # noqa: F401 (the fixture)

pytestmark = pytest.mark.usefixtures("tz_patch")

DEFAULT_ZONES = {z: c.upper() for z, c in C.ZONE_COLORS.items()}
# the default graph range colors with a darker amber high band (5.4:1 under the white labels), so that the zone tests
# below see only the zones; the default high band (4.11:1) is tested on its own
BANDS_MET = {**C.BAND_COLORS, "high": "#D9A31A"}


# ------------------------------------------------------------------------------------------------ the defaults
def test_the_defaults_are_published(tmp_path):
    cfg = build(tmp_path).config()
    assert cfg["band_colors"] == C.BAND_COLORS  # the build contract (tests/test_accessibility_checks.py)
    assert (cfg["card_background"], cfg["card_foreground"]) == ("#000000", "#FFFFFF")
    assert (cfg["panel_background"], cfg["panel_foreground"]) == ("#000000", "#FFFFFF")
    assert cfg["zone_colors"] == DEFAULT_ZONES


def test_the_report_and_the_graphs_share_one_table():
    assert P.AGP == {
        "vlow": C.BAND_COLORS["very_low"],
        "low": C.BAND_COLORS["low"],
        "target": C.BAND_COLORS["near_low"],
        "tight": C.BAND_COLORS["tight_range"],
        "high": C.BAND_COLORS["high"],
        "vhigh": C.BAND_COLORS["very_high"],
    }
    assert C.BAND_COLORS["near_low"] == C.BAND_COLORS["near_high"]  # in range outside the tight range: one color
    assert tuple(C.BAND_COLORS) == C.ZONES and tuple(C.CONF_BAND_COLORS) == C.ZONES


# ------------------------------------------------------------------------------------------------ band colors
@pytest.mark.parametrize("zone", C.ZONES)
def test_band_colors_one_zone(tmp_path, zone):
    cfg = build(tmp_path, entry(**{C.CONF_BAND_COLORS[zone]: "#12ab3c"})).config()
    assert cfg["band_colors"][zone] == "#12AB3C"  # upper case for every consumer
    assert all(cfg["band_colors"][z] == C.BAND_COLORS[z] for z in C.ZONES if z != zone)
    assert cfg["zone_colors"] == DEFAULT_ZONES  # the zone colors keep their own setting


@pytest.mark.parametrize("value", ["red", "#12345", "#GGGGGG", 0x52B964, [82, 185, 100], None, True])
def test_band_colors_invalid_value_is_the_default(value):
    assert S.band_colors({C.CONF_BAND_COLORS["tight_range"]: value}) == C.BAND_COLORS


def test_band_colors_never_change_the_data(tmp_path, monkeypatch):
    """Both builds on one fixed clock: the panel file's header carries times derived from the time of the build."""
    fixed = float(int(time.time()))
    monkeypatch.setattr(time, "time", lambda: fixed)
    a = build(tmp_path / "a")
    b = build(tmp_path / "b", entry(**{C.CONF_BAND_COLORS[z]: "#123456" for z in C.ZONES}))
    assert a.feed_bytes() == b.feed_bytes() and a.api_stats() == b.api_stats() and a.daily() == b.daily()


# ------------------------------------------------------------------------------------------------ backgrounds
@pytest.mark.parametrize(
    "background, foreground",
    [("#000000", "#FFFFFF"), ("#ffffff", "#000000"), ("#777777", "#000000"), ("#1565C0", "#FFFFFF")],
)
def test_card_and_panel_background_and_their_text_color(tmp_path, background, foreground):
    cfg = build(tmp_path, entry(card_background=background, panel_background=background)).config()
    assert (cfg["card_background"], cfg["card_foreground"]) == (background.upper(), foreground)
    assert (cfg["panel_background"], cfg["panel_foreground"]) == (background.upper(), foreground)


def test_card_background_theme_has_no_text_color_of_its_own(tmp_path):
    cfg = build(tmp_path, entry(card_background="theme")).config()
    assert (cfg["card_background"], cfg["card_foreground"]) == ("theme", None)
    assert cfg["panel_background"] == "#000000"


@pytest.mark.parametrize("value", ["Theme", "white", "#FFF", None, 0, ["theme"]])
def test_card_background_invalid_value_is_black(value):
    assert S.setting({C.CONF_CARD_BACKGROUND: value}, C.CONF_CARD_BACKGROUND) == "#000000"


@pytest.mark.parametrize("value", ["theme", "black", [0, 0, 0], None])
def test_panel_background_invalid_value_is_black(value):
    assert S.setting({C.CONF_PANEL_BACKGROUND: value}, C.CONF_PANEL_BACKGROUND) == "#000000"


def test_black_or_white_text_meets_4_5_to_1_on_every_background():
    """One of black and white always reaches at least 4.58:1 (ACCESSIBILITY.md rule 2): every gray, and a grid of
    colors."""
    colors = [f"#{v:02X}{v:02X}{v:02X}" for v in range(256)]
    colors += [
        f"#{r:02X}{g:02X}{b:02X}" for r in range(0, 256, 15) for g in range(0, 256, 15) for b in range(0, 256, 15)
    ]
    worst = min(K.contrast_ratio(K.foreground(c), c) for c in colors)
    assert worst >= 4.58


def test_contrast_follows_the_wcag_definition():
    assert K.contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)
    # the ratios as ACCESSIBILITY.md rule 2 states them (two decimals)
    assert round(K.contrast_ratio("#C62828", "#000000"), 2) == 3.74
    assert round(K.contrast_ratio("#1565C0", "#000000"), 2) == 3.65
    assert K.contrast_ratio("#123456", "#123456") == 1.0


# ------------------------------------------------------------------------------------------------ the contrast note
def _note(zones=None, card="#000000", panel="#000000", bands=None):
    return K.contrast_note({**DEFAULT_ZONES, **(zones or {})}, {**BANDS_MET, **(bands or {})}, card, panel)


def test_contrast_note_default_colors():
    # the default high band of the CGM Report's colors is listed; every other default color meets its minimum
    note = K.contrast_note(DEFAULT_ZONES, C.BAND_COLORS, "#000000", "#000000")
    assert note == "White graph text on the high band: 4.1:1, below the 4.5:1 minimum for text."
    assert _note() == "All colors meet the minimum contrast on this background."


def test_the_white_labels_on_each_band_as_drawn():
    """60 % of the band color over the graph's #0A0A0A: the measure of the accessibility checks and ACCESSIBILITY.md."""
    ratios = {zone: K.band_label_contrast(color) for zone, color in C.BAND_COLORS.items()}
    assert round(ratios["high"], 2) == 4.11
    assert min(r for zone, r in ratios.items() if zone != "high") >= 4.5
    assert round(K.band_label_contrast("#FFFFFF"), 2) == round(K.contrast_ratio("#FFFFFF", "#9D9D9D"), 2)  # 0.6*255+4


def test_each_band_is_listed_once_on_two_backgrounds():
    note = _note(card="#FFFFFF", panel="#000000", bands={"low": "#FFFF80", "near_high": "#FFFFFF"})
    assert note.count("White graph text on the low band: ") == 1
    assert "White graph text on the in-range band above the tight range: " in note
    assert "the in-range band below the tight range" not in note  # #1F8F45: 8.30:1
    assert note.endswith("below the 4.5:1 minimum for text.") and note.count(". ") == 0


def test_contrast_note_lists_each_color_below_the_minimum():
    ratio = K.contrast_ratio("#7F1D1D", "#000000")
    assert ratio < 3
    expected = f"Very low on black: {int(ratio * 10) / 10:.1f}:1, below the 3:1 minimum for large text."
    assert _note({"very_low": "#7F1D1D"}) == expected


def test_contrast_note_on_white():
    note = _note(card="#FFFFFF", panel="#FFFFFF")
    assert "In range, below the tight range on white: 1.6:1, below the 3:1 minimum for large text" in note
    assert "High on white" in note and "Tight range on white" in note  # #42A5F5 2.6:1, #4CAF50 2.8:1
    assert "Very low" not in note and "Very high" not in note  # #C62828 5.6:1, #1565C0 5.8:1
    assert note.endswith(".") and "\n" not in note and note.count(". ") == 0  # one sentence, the items joined by ";"


def test_contrast_note_names_the_display_when_the_backgrounds_differ():
    note = _note(card="#FFFFFF", panel="#000000")
    assert "on white (display card)" in note and "(wall panels)" not in note
    assert _note(card="#000000", panel="#123456").startswith("Very low on #123456 (wall panels): ")
    assert _note(card="#000000", panel="#101010") == "All colors meet the minimum contrast on both backgrounds."


def test_contrast_note_with_the_theme():
    theme = " The display card follows the Home Assistant theme, so its contrast depends on the theme."
    assert _note(card="theme") == "All colors meet the minimum contrast on the wall panel background." + theme
    note = _note(card="theme", panel="#FFFFFF")
    assert note.startswith("In range, below the tight range on white (wall panels): 1.6:1") and note.endswith(theme)


def test_contrast_note_rounds_down():
    """A ratio below a minimum never reads as that minimum."""
    assert K._ratio_text(2.96) == "2.9:1" and K._ratio_text(4.499) == "4.4:1" and K._ratio_text(3.0) == "3.0:1"


# ------------------------------------------------------------------------------------------------ the settings screen
@pytest.mark.usefixtures("schemas")
def test_the_screen_shows_the_note_of_the_saved_colors():
    stored = {**_stored(), C.CONF_CARD_BACKGROUND: "#FFFFFF", C.CONF_PANEL_BACKGROUND: "#FFFFFF"}
    flow, shown, _ = _options_flow(stored)
    _screen(flow, shown, "displays")
    assert shown[-1]["description_placeholders"]["contrast_note"] == K.contrast_note(
        DEFAULT_ZONES, C.BAND_COLORS, "#FFFFFF", "#FFFFFF"
    )
    assert "{contrast_note}" in OPTIONS["displays"]["description"]


def _save(changes: dict, stored: dict | None = None):
    """Save the Display card and wall panels screen with its defaults and `changes` (the section's under
    "graph_colors"); return the updates saved."""
    flow, shown, saved = _options_flow(stored or _stored())
    user_input = _defaults_input(_screen(flow, shown, "displays"))
    user_input["graph_colors"].update(changes.pop("graph_colors", {}))
    user_input.update(changes)
    assert asyncio.run(flow.async_step_displays(user_input))[0] == "done"
    return saved[-1]


@pytest.mark.usefixtures("schemas")
def test_saving_unchanged_stores_the_defaults():
    updates = _save({})
    assert all(updates[k] is None for k in C.CONF_BAND_COLORS.values())  # removed on saving: the default
    assert updates[C.CONF_CARD_BACKGROUND] == "#000000" and updates[C.CONF_PANEL_BACKGROUND] == "#000000"
    assert "graph_colors" not in updates and cf.FIELD_CARD_BACKGROUND_COLOR not in updates


@pytest.mark.usefixtures("schemas")
def test_saving_a_graph_color_and_the_reset():
    updates = _save({"graph_colors": {C.CONF_BAND_COLORS["high"]: [18, 52, 86]}})
    assert updates[C.CONF_BAND_COLORS["high"]] == "#123456"
    assert updates[C.CONF_BAND_COLORS["low"]] is None
    stored = {**_stored(), C.CONF_BAND_COLORS["high"]: "#123456"}
    flow, shown, _ = _options_flow(stored)
    shown_default = _screen(flow, shown, "displays")
    section = next(v for k, v in shown_default.items() if k.key == "graph_colors")
    assert next(k.default for k in section.schema if k.key == C.CONF_BAND_COLORS["high"]) == [18, 52, 86]
    reset = _save({"graph_colors": {"band_colors_reset": True}}, stored)
    assert all(reset[k] is None for k in C.CONF_BAND_COLORS.values())


@pytest.mark.usefixtures("schemas")
def test_saving_the_card_background():
    assert _save({C.CONF_CARD_BACKGROUND: "theme"})[C.CONF_CARD_BACKGROUND] == "theme"
    colored = _save({C.CONF_CARD_BACKGROUND: "color", cf.FIELD_CARD_BACKGROUND_COLOR: [255, 255, 255]})
    assert colored[C.CONF_CARD_BACKGROUND] == "#FFFFFF"
    # a stored theme shows "Home Assistant theme" and the black default in the color picker
    flow, shown, _ = _options_flow({**_stored(), C.CONF_CARD_BACKGROUND: "theme"})
    fields = {k.key: k.default for k in _screen(flow, shown, "displays") if hasattr(k, "default")}
    assert fields[C.CONF_CARD_BACKGROUND] == "theme" and fields[cf.FIELD_CARD_BACKGROUND_COLOR] == [0, 0, 0]


@pytest.mark.usefixtures("schemas")
def test_saving_the_panel_background():
    assert _save({C.CONF_PANEL_BACKGROUND: [240, 240, 240]})[C.CONF_PANEL_BACKGROUND] == "#F0F0F0"
