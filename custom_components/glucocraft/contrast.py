# License: AGPL-3.0-only (LICENSE).
"""The contrast of the display colors: the text color on a chosen background, and the contrast note of the settings.

GlucoCraft decides the colors, and the display card and the wall panels draw them (docs/DISPLAY_CONTRACT.md). For a
background chosen in the settings, GlucoCraft publishes the text color the displays use on it (foreground): black or
white, whichever has the higher contrast, so that the page text, the clock, the labels, the status words and the
navigation stay readable. One of the two always reaches at least 4.58:1, so this text meets the 4.5:1 minimum on every
background. The graphs keep their own black area and are not affected.

The settings screen Display card and wall panels shows the measured contrast of the saved colors in one sentence
(contrast_note): the glucose value in each zone color against the card's and the panels' background (large text, at
least 3:1), the text color against each background (text, at least 4.5:1), and the graphs' white labels against each
graph range color as the displays draw it, at 60 % over the graph's black area, whatever the background (text, at
least 4.5:1). The note changes no color; the chosen colors are always used.

Relative luminance and contrast ratio are the definitions of WCAG 2.2 (World Wide Web Consortium, 2024), the same as in
the cards (glucocraft-shared.js gcLum / gcContrast / gcOnColor), so that GlucoCraft and the cards agree on every color.
"""

from __future__ import annotations

import math

from .const import CARD_BACKGROUND_THEME, ZONES
from .i18n import t

BLACK = "#000000"
WHITE = "#FFFFFF"
LARGE_TEXT_MINIMUM = 3.0  # WCAG 2.2 success criterion 1.4.3: large text (the glucose value)
TEXT_MINIMUM = 4.5  # WCAG 2.2 success criterion 1.4.3: text
# The graphs as the display card and the LCD panels draw them: the range bands at 60 % over a black area, the labels
# white, on every background
GRAPH_AREA = "#0A0A0A"
GRAPH_BAND_OPACITY = 0.6
GRAPH_LABEL = WHITE

# The colors the texts name in words (contrast.color.black / .white); any other color is named by its code
_COLOR_KEYS = {BLACK: "black", WHITE: "white"}
SAME_COLOR_ON_PURPOSE = {"near_low", "near_high"}  # shared_colors_note: no finding when only these two share a color


def _channel(value: float) -> float:
    """One sRGB channel (0-255) as linear light (WCAG 2.2 relative luminance)."""
    c = value / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _rgb(color: str) -> tuple[int, int, int]:
    """The red, green and blue channels (0-255) of a "#RRGGBB" color."""
    return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))


def _luminance(rgb) -> float:
    """The relative luminance of three sRGB channels (0-255)."""
    r, g, b = rgb
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _ratio(la: float, lb: float) -> float:
    """The contrast ratio of two relative luminances."""
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def luminance(color: str) -> float:
    """The relative luminance of a "#RRGGBB" color (0 black to 1 white)."""
    return _luminance(_rgb(color))


def contrast_ratio(a: str, b: str) -> float:
    """The contrast ratio of two "#RRGGBB" colors, from 1 (equal) to 21 (black on white)."""
    return _ratio(luminance(a), luminance(b))


def band_label_contrast(band_color: str) -> float:
    """The contrast ratio of the white graph labels on a graph range color as the displays draw it: at 60 % over the
    graph's black area, channel by channel (the measure of tests/test_accessibility_checks.py, 4.11:1 for the default
    high band)."""
    drawn = [
        GRAPH_BAND_OPACITY * c + (1 - GRAPH_BAND_OPACITY) * a
        for c, a in zip(_rgb(band_color), _rgb(GRAPH_AREA), strict=True)
    ]
    return _ratio(luminance(GRAPH_LABEL), _luminance(drawn))


def foreground(background: str) -> str | None:
    """The text color on a background: "#FFFFFF" or "#000000", whichever has the higher contrast (white when both are
    equal, as the cards decide). None for the Home Assistant theme, whose own text color is then used."""
    if background == CARD_BACKGROUND_THEME:
        return None
    return WHITE if contrast_ratio(WHITE, background) >= contrast_ratio(BLACK, background) else BLACK


def _ratio_text(ratio: float) -> str:
    """A ratio as the note shows it, one decimal, rounded down, so that a ratio below a minimum never reads as that
    minimum (2.96 is "2.9:1", not "3.0:1")."""
    return f"{math.floor(ratio * 10 + 1e-9) / 10:.1f}:1"


def _color_name(color: str) -> str:
    """Black and white by name, any other color by its code."""
    return t(f"contrast.color.{_COLOR_KEYS[color]}") if color in _COLOR_KEYS else color


def _minimum_text(minimum: float) -> str:
    """Return a contrast ratio as written: "4.5:1"."""
    return f"{minimum:g}:1"


def _sharing(colors: dict[str, str], name: str) -> list[str]:
    """The sentences for the zones of one palette (`name`: "zone" or "band") that share a color, in the palette's order.
    Near low and near high alone are no finding: they are the same range on either side of the target, and share a
    color in the default palettes."""
    groups: dict[str, list[str]] = {}
    for zone in ZONES:
        groups.setdefault(colors[zone].upper(), []).append(zone)
    return [
        t(f"contrast.shared_{name}", zones=", ".join(t(f"contrast.{name}.{zone}") for zone in zones))
        for zones in groups.values()
        if len(zones) > 1 and set(zones) != SAME_COLOR_ON_PURPOSE
    ]


def shared_colors_note(zone_colors: dict[str, str], band_colors: dict[str, str]) -> str:
    """The sentences that follow the contrast note when zones share a color, each with a space before it ("" when none
    do). A note only: the chosen colors are always used, and the zone word names the zone in every setting."""
    return "".join(f" {text}" for text in _sharing(zone_colors, "zone") + _sharing(band_colors, "band"))


def contrast_note(
    zone_colors: dict[str, str], band_colors: dict[str, str], card_background: str, panel_background: str
) -> str:
    """The contrast note of the settings screen, one plain sentence.

    "All colors meet the minimum contrast on this background." or the list of the pairs below their minimum, for
    example "Very low on black: 2.6:1, below the 3:1 minimum for large text" or "White graph text on the high band:
    4.1:1, below the 4.5:1 minimum for text". A display card that follows the Home Assistant theme cannot be measured;
    the note then says that its contrast depends on the theme. zone_colors and band_colors: the colors in use
    (const.zone_colors, settings.band_colors); the backgrounds as settings.setting returns them.
    """
    theme = card_background == CARD_BACKGROUND_THEME
    # the backgrounds to measure, each with the displays it belongs to
    if theme:
        surfaces = [(panel_background, "panels")]
    elif card_background == panel_background:
        surfaces = [(card_background, "")]
    else:
        surfaces = [(card_background, "card"), (panel_background, "panels")]
    below = []
    for background, where in surfaces:
        color = _color_name(background)
        on = (
            t("contrast.on_where", color=color, where=t(f"contrast.where.{where}"))
            if where
            else t("contrast.on", color=color)
        )
        for zone in ZONES:
            ratio = contrast_ratio(zone_colors[zone], background)
            if ratio < LARGE_TEXT_MINIMUM:
                below.append(
                    t(
                        "contrast.zone_below",
                        zone=t(f"contrast.zone.{zone}"),  # the zone's name as the settings screen labels its color
                        on=on,
                        ratio=_ratio_text(ratio),
                        minimum=_minimum_text(LARGE_TEXT_MINIMUM),
                    )
                )
        text = foreground(background)
        ratio = contrast_ratio(text, background)
        if ratio < TEXT_MINIMUM:  # never with black or white text (at least 4.58:1); measured all the same
            below.append(
                t(
                    "contrast.text_below",
                    color=_color_name(text),
                    on=on,
                    ratio=_ratio_text(ratio),
                    minimum=_minimum_text(TEXT_MINIMUM),
                )
            )
    # the graph labels: the graph area is the same on every background, so each band is measured once
    for zone in ZONES:
        ratio = band_label_contrast(band_colors[zone])
        if ratio < TEXT_MINIMUM:
            below.append(
                t(
                    "contrast.band_below",
                    band=t(f"contrast.band.{zone}"),
                    ratio=_ratio_text(ratio),
                    minimum=_minimum_text(TEXT_MINIMUM),
                )
            )
    if below:
        note = t("contrast.below", items=t("contrast.separator").join(below))
    elif theme:
        note = t("contrast.all_met.panels")
    else:
        note = t("contrast.all_met.single" if len(surfaces) == 1 else "contrast.all_met.both")
    if theme:
        note += " " + t("contrast.theme")
    return note
