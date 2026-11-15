# License: AGPL-3.0-only (LICENSE).
"""Black or white on a color: one rule on the wall panels, the display card and the integration.

Text on a colored area (the panels' status bar, gc_units.h text_on; the cards' gcOnColor, glucocraft-shared.js) and
the Outline text edge (lvgl_compat.h lvc_edge_color; gcOnColor of the text color) are black or white, whichever has the higher
WCAG 2.2 contrast ratio with the color, and white when both are equal, the same as contrast.foreground. The panels'
C++ cannot run here: the tests read each rule from its source and evaluate it, on an exact tie and around it.
"""

from __future__ import annotations

import re

import pytest
from glucocraft import contrast
from glucocraft.const import STATUS_COLORS

from conftest import NO_PANELS, REPO
from conftest import PANELS as _FOUND

PANELS = _FOUND or REPO / "esphome"
UNITS_H = PANELS / "gc_units.h"
COMPAT_H = PANELS / "lvgl_compat.h"
SHARED_JS = REPO / "custom_components" / "glucocraft" / "www" / "glucocraft-shared.js"
needs_panels = pytest.mark.skipif(_FOUND is None, reason=NO_PANELS)

WHITE, BLACK = 0xFFFFFF, 0x000000
# a C / JavaScript ternary between black and white: <a> >= <b> ? <color> : <color>
TERNARY = r"(.+?) >= (.+?) \? (?:lv_color_hex\()?(0x[0-9A-Fa-f]{6})\)? : (?:lv_color_hex\()?(0x[0-9A-Fa-f]{6})\)?;"


def _rule(source: str, start: str) -> tuple[str, str, int, int]:
    """Return the comparison of the function that starts with `start`: (left, right, color if >=, color if <)."""
    body = source[source.index(start) :]
    line = next(ln for ln in body.splitlines() if " ? " in ln)
    m = re.search(TERNARY, re.sub(r"^.*?(?:return |=> \()", "", line))
    assert m, start
    left, right, yes, no = m.groups()
    return left, right, int(yes, 16), int(no, 16)


def _ratios(lum: float) -> dict[str, float]:
    """The contrast ratios of white and of black with a color of relative luminance lum, as each source names them."""
    white, black = 1.05 / (lum + 0.05), (lum + 0.05) / 0.05
    return {
        "with_white": white,
        "with_black": black,
        "1.05f / (l + 0.05f)": white,
        "(l + 0.05f) / 0.05f": black,
        "gcContrast(n, 0xFFFFFF)": white,
        "gcContrast(n, 0x000000)": black,
    }


def _choose(rule: tuple[str, str, int, int], ratios: dict[str, float]) -> int:
    left, right, yes, no = rule
    return yes if ratios[left] >= ratios[right] else no


def _sources() -> dict[str, tuple[str, str, int, int]]:
    units, compat = UNITS_H.read_text(encoding="utf-8"), COMPAT_H.read_text(encoding="utf-8")
    shared = SHARED_JS.read_text(encoding="utf-8")
    return {
        "gc_units.h text_on": _rule(units, "inline int text_on(int bg) {"),
        "lvgl_compat.h lvc_edge_color": _rule(compat, "inline lv_color_t lvc_edge_color(lv_color_t text) {"),
        "glucocraft-shared.js gcOnColor": _rule(shared, "const gcOnColor = (n) =>"),
    }


# the relative luminance at which white and black have the same contrast ratio: (l + 0.05)^2 = 1.05 x 0.05
TIE = (1.05 * 0.05) ** 0.5 - 0.05


@needs_panels
def test_every_source_chooses_white_on_an_exact_tie():
    ratios = {name: 4.58 for name in _ratios(TIE)}  # both ratios exactly equal
    for name, rule in _sources().items():
        assert _choose(rule, ratios) == WHITE, name


def test_the_integration_chooses_white_on_an_exact_tie(monkeypatch):
    monkeypatch.setattr(contrast, "contrast_ratio", lambda a, b: 4.58)
    assert contrast.foreground("#777777") == contrast.WHITE


@needs_panels
@pytest.mark.parametrize("delta", [-1e-6, 1e-6])
def test_every_source_takes_the_higher_contrast_beside_the_tie(delta):
    """Just darker than the tie white contrasts more; just lighter black does."""
    expected = WHITE if delta < 0 else BLACK
    for name, rule in _sources().items():
        assert _choose(rule, _ratios(TIE + delta)) == expected, name


@needs_panels
@pytest.mark.parametrize("color", sorted(set(STATUS_COLORS.values())) + ["#000000", "#FFFFFF", "#757575", "#767676"])
def test_panels_and_integration_agree_on_the_status_colors(color):
    """The status bar's text color on each of GlucoCraft's status colors, black, white and the two grays beside the
    tie (#757575 white, #767676 black)."""
    lum = contrast.luminance(color)
    expected = int(contrast.foreground(color)[1:], 16)
    for name, rule in _sources().items():
        assert _choose(rule, _ratios(lum)) == expected, (name, color)


@needs_panels
def test_the_panel_comments_name_wcag_2_2():
    units = UNITS_H.read_text(encoding="utf-8")
    assert "WCAG 2.1" not in units and "WCAG 2.1" not in COMPAT_H.read_text(encoding="utf-8")
    assert "(WCAG 2.2 relative luminance), white when both are equal" in units
