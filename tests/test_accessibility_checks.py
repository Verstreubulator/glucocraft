# License: AGPL-3.0-only (LICENSE).
"""The release checks of docs/ACCESSIBILITY.md, run as tests on the cards, the dashboard and the CGM Report page.

- The graph range colors: one default per file, equal to the build contract (the CGM Report's time-in-ranges colors).
- Contrast (WCAG 2.2, 1.4.3 and 1.4.11): every text and background pair that the display cards draw, on both default
  backgrounds (black and white, so both text colors) with the default palettes. Pairs below their minimum are reported
  and must be exactly the known ones that the Documentation lists (Accessibility, Known Limitations), so that a new one
  fails the test.
- Color vision (Machado et al., 2009, severity 1.0; CIEDE2000): both default palettes under protanopia, deuteranopia and
  tritanopia. Pairs closer than a difference of 10 are reported; a range-color pair must have different zone words (the
  zone word is shown in every zone). The graph-band pairs, solid and as drawn (60% over the graphs' black area), are
  the figures the Documentation publishes; neighboring bands as drawn come close, so the number scale carries the
  values.
- Text alternatives and keyboard (WCAG 1.1.1, 2.1.1, 4.1.2): every card and chart has a text alternative, every button
  and link has a name, nothing that can take the focus is inside an aria-hidden part, and each card root carries
  lang="en". Text sizes: nothing on the cards below 12 px, the statistics labels at the square panel's size.
"""

from __future__ import annotations

import html.parser
import itertools
import json
import math
import re
import textwrap
from pathlib import Path

import pytest
from glucocraft import const as C

from conftest import NO_PANELS, PANELS
from js_i18n import documentation_section, documentation_text
from js_i18n import text as i18n_text
from node_runner import NODE, needs_node, node_json

REPO = Path(__file__).resolve().parents[1]
WWW = REPO / "custom_components" / "glucocraft" / "www"
JS = {
    n: (WWW / f"glucocraft-{n}.js").read_text(encoding="utf-8")
    for n in ("display-card", "ui", "report", "docs", "strategy", "shared")
}

# the build contract: the graph range colors, the CGM Report's time-in-ranges colors (const.BAND_COLORS, report_pdf.py),
# written out once here; the other tests take them from const.BAND_COLORS, which
# test_one_default_of_the_graph_band_colors_equal_to_the_contract holds to this
BAND_CONTRACT = {
    "very_low": "#9B1C1F",
    "low": "#E5352B",
    "near_low": "#1F8F45",
    "tight_range": "#52B964",
    "near_high": "#1F8F45",
    "high": "#FCC21B",
    "very_high": "#F08A4B",
}
ZONE_ORDER = list(BAND_CONTRACT)  # from the bottom of a graph to its top
ZONE_WORDS = {"very_low": "Very low", "low": "Low", "near_low": "In range", "tight_range": "Tight range",
              "near_high": "In range", "high": "High", "very_high": "Very high"}  # fmt: skip
STATUS_COLORS = ["#43A047", "#F9A825", "#E53935", "#9E9E9E", "#7E57C2"]  # current, expiring, alert, gray, waiting
GRAPH = "#0A0A0A"  # the graphs' black, on every background


STUBS = textwrap.dedent("""
    const mk = () => ({ attrs: {}, style: {}, hidden: false, innerHTML: "", textContent: "", listeners: {}, dataset: {},
      setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; },
      addEventListener(k, f) { this.listeners[k] = f; }, querySelector() { return mk(); }, querySelectorAll() { return []; },
      getElementById() { return null; } });
    globalThis.window = globalThis;
    globalThis.document = { createElement: () => ({ getContext: () => ({ font: "14px", measureText(t) { return { width: String(t).length * 0.57 * (parseFloat(this.font) || 14) }; } }) }) };
    globalThis.HTMLElement = class {
      constructor() { this.style = {}; this.attrs = {}; }
      setAttribute(k, v) { this.attrs[k] = String(v); }
      getBoundingClientRect() { return { width: 600 }; }
      attachShadow() {
        const els = {}, root = mk();
        root.querySelector = (sel) => (els[sel] ||= mk());
        this.shadowRoot = root; return root;
      }
    };
    globalThis.ResizeObserver = class { observe() {} disconnect() {} };
    const __reg = {};
    globalThis.customElements = { get: (n) => __reg[n], define: (n, c) => { __reg[n] = c; }, whenDefined: async () => {} };
    globalThis.history = { pushState() {} }; globalThis.location = { pathname: "/glucocraft-home/start", hash: "" };
""")


# The CGM Report page draws into two parts of its card: the intro with the controls, and the report itself (the two
# message regions between them keep their elements)
REPORT_PARTS_JS = 'const REPORT_PARTS = [".top", ".main"];\n'


def _eval(name: str, expr: str) -> object:
    """Evaluate expr after the module source of one www file (its constants and functions in scope)."""
    return node_json(
        STUBS
        + JS[name]
        + "\nprocess.stdout.write('\\u0001' + JSON.stringify(await (async () => { "
        + expr
        + " })()));\n"
    )


# ------------------------------------------------------------------------------------------------ WCAG arithmetic
def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _rgb(h: str) -> list[float]:
    return [int(h[i : i + 2], 16) / 255 for i in (1, 3, 5)]


def _lum(rgb: list[float]) -> float:
    r, g, b = (_lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    """The WCAG contrast ratio of two colors ("#RRGGBB" or sRGB lists of 0..1)."""
    la, lb = (_lum(_rgb(x) if isinstance(x, str) else x) for x in (a, b))
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def over(color: str, bg: str = GRAPH, alpha: float = 0.6) -> list[float]:
    """A color drawn at alpha over bg, as the browser composites it (in sRGB)."""
    return [alpha * c + (1 - alpha) * b for c, b in zip(_rgb(color), _rgb(bg), strict=True)]


# ------------------------------------------------------------------------------------------------ the band colors
@needs_node
def test_one_default_of_the_graph_band_colors_equal_to_the_contract():
    """The default graph range colors are written once (glucocraft-shared.js BAND_DEFAULT); the display card uses them as
    numbers, the dashboard cards as they are, the Documentation names them."""
    assert JS["shared"].count("const BAND_DEFAULT = ") == 1
    assert not any("const BAND_DEFAULT" in JS[n] for n in ("display-card", "ui", "report", "docs", "strategy"))
    card = _eval(
        "display-card", "return Object.fromEntries(Object.entries(BC).map(([k, v]) => [k, hex(v).toUpperCase()]));"
    )
    assert card == BAND_CONTRACT
    assert _eval("ui", "return BAND_DEFAULT;") == BAND_CONTRACT
    assert _eval("ui", "return gcBandColors({ high: '#123456', low: 'red' });") == {**BAND_CONTRACT, "high": "#123456"}
    # the CGM Report page's time-in-ranges colors and the Documentation's table are the same colors
    rc = _eval("report", "return RC;")
    assert {rc[k] for k in ("vlow", "low", "target", "tight", "high", "vhigh")} == set(BAND_CONTRACT.values())
    assert _eval("docs", "return Object.keys(BAND_NAME);") == list(BAND_CONTRACT)
    assert {k: v.upper() for k, v in C.BAND_COLORS.items()} == BAND_CONTRACT  # the backend's constant


def test_the_bands_use_the_band_colors_and_everything_else_the_zone_colors():
    card, ui = JS["display-card"], JS["ui"]
    assert 'fill="${hex(BC[zone])}" fill-opacity="0.6" data-band="${zone}"' in card
    assert 'fill="${BAND[zone]}" fill-opacity="0.6"' in ui and "background:${BAND[zone]}" in ui
    assert "BC = gcBandColorNumbers(ca.band_colors)" in card and "BAND = gcBandColors(ca.band_colors)" in ui
    # the value, LO / HI and the weekly pages keep the range colors and the status colors
    assert "ZC[zone]" in card and "ZONE.very_low : ZONE.very_high" in ui and "hex(C[above])" in card


# ------------------------------------------------------------------------------------------------ contrast
def _card_palette(bg: str) -> dict[str, str]:
    css = _eval("display-card", f"return gcCardPalette({json.dumps(bg)}, NaN);")
    return {k: v.upper() for k, v in re.findall(r"--gc-(\w+): (#[0-9a-fA-F]{6})", css)}


# the known pairs below their minimum, listed in the Documentation (Accessibility, Known Limitations / Contrast)
KNOWN_ZONE_ON_WHITE = {"near_low", "tight_range", "near_high", "high"}  # amber 1.63, tight 2.78, high 2.65
KNOWN_WHITE_ON_BANDS = {"high"}  # 4.11:1 on the amber high band
KNOWN_KEY_ON_BANDS = {"near_low", "tight_range", "near_high", "high", "very_high"}  # the light-gray min-to-max key


@needs_node
@pytest.mark.parametrize("bg", ["#000000", "#FFFFFF"])
def test_contrast_of_every_text_the_cards_draw(bg):
    pal = _card_palette(bg)
    fg = pal["fg"]
    assert fg == ("#FFFFFF" if bg == "#000000" else "#000000") and pal["bg"] == bg
    report = {}
    # the page text: the text color and the secondary colors (labels, notes, a value that is not current)
    for role in ("fg", "fg2", "fg3", "old"):
        report[f"{role} on {bg}"] = contrast(pal[role], bg)
        assert report[f"{role} on {bg}"] >= 4.5, (role, pal[role])
    # the wall panels' grays (gc_units.h derive_theme: TH_TEXT_LABEL, TH_TEXT_SECONDARY, TH_FRAME); a value that is not
    # current takes the labels' gray on both
    panel = {"#000000": ("#888888", "#AAAAAA", "#808080"), "#FFFFFF": ("#767676", "#555555", "#7F7F7F")}[bg]
    assert (pal["fg2"], pal["fg3"], pal["box"]) == panel and pal["old"] == pal["fg2"]
    # the status bar's text: black or white, whichever has the higher contrast with the status color
    for col in STATUS_COLORS:
        on = _eval("display-card", f"return hex(onColor(0x{col[1:]})).toUpperCase();")
        assert contrast(on, col) >= 4.5, col
    # the glucose value (large text, 3:1) in the default range colors: below it only the colors the settings note names
    below = {z for z, c in C.ZONE_COLORS.items() if contrast(c, bg) < 3}
    assert below == (KNOWN_ZONE_ON_WHITE if bg == "#FFFFFF" else set()), below


# Home Assistant's default themes: its primary color (#009ac7, and the older default #03a9f4) and the text color
# and card background of the light and the dark theme
HA_PRIMARY = ("#009AC7", "#03A9F4")
HA_THEMES = {"light": ("#141414", "#FFFFFF"), "dark": ("#E1E1E1", "#1C1C1C")}  # (text color, card background)


def test_contrast_of_links_buttons_and_the_focus_ring_in_the_default_themes():
    """GlucoCraft's links and filled buttons reach 4.5:1 with Home Assistant's default primary colors in the light and
    the dark theme (the theme's primary color itself reaches 3.26:1 and 2.63:1 on white); the focus ring is one style,
    3 px in the text color, which reaches 3:1 on the background (WCAG 1.4.11)."""
    shared = JS["shared"]
    link = re.search(
        r'link: "color-mix\(in srgb, var\(--primary-color\) (\d+)%, var\(--primary-text-color\)\)"', shared
    )
    fill = re.search(
        r'fill: "color-mix\(in srgb, var\(--primary-color\) (\d+)%, #000\)", onFill: "(#[0-9a-f]{3,6})"', shared
    )
    assert link and fill and fill.group(2) == "#fff"
    for primary in HA_PRIMARY:
        assert contrast(primary, "#FFFFFF") < 4.5  # why the theme color is not used as it is
        for text, bg in HA_THEMES.values():
            assert contrast(over(primary, text, int(link.group(1)) / 100), bg) >= 4.5, (primary, bg)
            assert contrast("#FFFFFF", over(primary, "#000000", int(fill.group(1)) / 100)) >= 4.5, primary
            assert contrast(text, bg) >= 3  # the focus ring
    # every card and page: no text, link or fill in the theme's primary color as it is, and one focus ring
    for name in ("display-card", "ui", "report", "docs", "strategy"):
        js = JS[name]
        assert not re.search(r"(?<![\w-])(?:color|background): var\(--primary-color", js), name
        assert not re.search(r"outline: \d+px solid var\(--primary-color", js), name
        assert not re.search(r"outline: (?!3px)\d+px solid", js), name
    assert 'focus: "outline: 3px solid var(--primary-text-color); outline-offset: 2px;"' in shared


def test_contrast_inside_the_graphs_the_same_on_every_background():
    assert contrast("#FFFFFF", GRAPH) >= 4.5 and contrast("#888888", GRAPH) >= 4.5 and contrast("#BDBDBD", GRAPH) >= 4.5
    white = {z: contrast("#FFFFFF", over(c)) for z, c in BAND_CONTRACT.items()}
    key = {z: contrast("#BDBDBD", over(c)) for z, c in BAND_CONTRACT.items()}
    assert {z for z, v in white.items() if v < 4.5} == KNOWN_WHITE_ON_BANDS, white
    assert round(white["high"], 2) == 4.11  # the figure the Documentation and ACCESSIBILITY.md publish
    assert {z for z, v in key.items() if v < 4.5} == KNOWN_KEY_ON_BANDS, key
    docs = documentation_text()
    assert "<td>4.11:1</td>" in docs and "1.63:1, 2.78:1 and 2.65:1" in docs
    # the key's figures as published: every band below 4.5:1, recomputed (the two in-range bands share one color)
    assert key["near_low"] == key["near_high"]
    figures = (
        f"{key['high']:.2f}:1 on the high band, {key['very_high']:.2f}:1 on the very-high band, "
        f"{key['tight_range']:.2f}:1 on the tight-range band and {key['near_low']:.2f}:1 on the in-range bands"
    )
    assert f"below 4.5:1 on four default bands: {figures}." in docs, figures


# ------------------------------------------------------------------------------------------------ color vision
MACHADO = {  # Machado et al. (2009), severity 1.0, on linear RGB
    "protanopia": [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
    "deuteranopia": [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]],
    "tritanopia": [[1.255528, -0.076749, -0.178779], [-0.078411, 0.930809, 0.147602], [0.004733, 0.691367, 0.303900]],
}
CLOSE = 10.0  # CIEDE2000: pairs below this difference are reported


def _lab_f(t: float) -> float:
    return t ** (1 / 3) if t > (6 / 29) ** 3 else t / (3 * (6 / 29) ** 2) + 4 / 29


def _lab(lin_rgb):
    r, g, b = lin_rgb
    x, y, z = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b, 0.2126729 * r + 0.7151522 * g + 0.0721750 * b,
               0.0193339 * r + 0.1191920 * g + 0.9503041 * b)  # fmt: skip
    fx, fy, fz = _lab_f(x / 0.95047), _lab_f(y), _lab_f(z / 1.08883)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def ciede2000(l1, l2) -> float:
    L1, a1, b1 = l1
    L2, a2, b2 = l2
    cb = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    g = 0.5 * (1 - math.sqrt(cb**7 / (cb**7 + 25**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1, c2 = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1, h2 = math.degrees(math.atan2(b1, a1p)) % 360, math.degrees(math.atan2(b2, a2p)) % 360
    dh = 0 if c1 * c2 == 0 else (h2 - h1 if abs(h2 - h1) <= 180 else h2 - h1 - 360 if h2 > h1 else h2 - h1 + 360)
    dH = 2 * math.sqrt(c1 * c2) * math.sin(math.radians(dh / 2))
    lb, cbp = (L1 + L2) / 2, (c1 + c2) / 2
    if c1 * c2 == 0:
        hb = h1 + h2
    elif abs(h1 - h2) <= 180:
        hb = (h1 + h2) / 2
    else:
        hb = (h1 + h2 + 360) / 2 if h1 + h2 < 360 else (h1 + h2 - 360) / 2
    t = (1 - 0.17 * math.cos(math.radians(hb - 30)) + 0.24 * math.cos(math.radians(2 * hb))
         + 0.32 * math.cos(math.radians(3 * hb + 6)) - 0.20 * math.cos(math.radians(4 * hb - 63)))  # fmt: skip
    rt = -math.sin(math.radians(60 * math.exp(-(((hb - 275) / 25) ** 2)))) * 2 * math.sqrt(cbp**7 / (cbp**7 + 25**7))
    sl = 1 + 0.015 * (lb - 50) ** 2 / math.sqrt(20 + (lb - 50) ** 2)
    sc, sh = 1 + 0.045 * cbp, 1 + 0.015 * cbp * t
    return math.sqrt(((L2 - L1) / sl) ** 2 + ((c2 - c1) / sc) ** 2 + (dH / sh) ** 2 + rt * ((c2 - c1) / sc) * (dH / sh))


def _seen(color: str, kind: str | None):
    lin = [_lin(c) for c in _rgb(color)]
    if kind:
        m = MACHADO[kind]
        lin = [min(1.0, max(0.0, sum(m[i][j] * lin[j] for j in range(3)))) for i in range(3)]
    return _lab(lin)


def _close_pairs(palette: dict[str, str], kind: str) -> dict[tuple[str, str], float]:
    out = {}
    for a, b in itertools.combinations(palette, 2):
        if palette[a].upper() == palette[b].upper():  # the two in-range zones share one color and one meaning
            continue
        d = ciede2000(_seen(palette[a], kind), _seen(palette[b], kind))
        if d < CLOSE:
            out[(a, b)] = round(d, 1)
    return out


def test_the_simulation_reproduces_the_published_check():
    # the low / tight-range pair of the default range colors under deuteranopia (the Documentation's figure)
    assert round(ciede2000(_seen("#EF5350", "deuteranopia"), _seen("#4CAF50", "deuteranopia")), 1) == 1.9


@pytest.mark.parametrize("kind", list(MACHADO))
def test_color_vision_range_colors_are_covered_by_the_zone_word(kind):
    pairs = _close_pairs(C.ZONE_COLORS, kind)
    for a, b in pairs:
        assert ZONE_WORDS[a] != ZONE_WORDS[b], (kind, a, b)  # the zone word names every zone (the default)
    # the display card's zone words: one table of catalogue keys, whose English words are these
    from js_i18n import catalogue

    assert JS["display-card"].count("ZONE_KEYS = {") == 1
    assert {z: catalogue("frontend")[f"card.zone.{z}"] for z in ZONE_WORDS} == ZONE_WORDS
    if kind == "deuteranopia":
        assert pairs == {("low", "tight_range"): 1.9}


@pytest.mark.parametrize("kind", list(MACHADO))
def test_color_vision_graph_bands_close_pairs_are_never_neighbors(kind):
    pairs = _close_pairs(BAND_CONTRACT, kind)
    for a, b in pairs:
        assert abs(ZONE_ORDER.index(a) - ZONE_ORDER.index(b)) > 1, (kind, a, b, pairs)
    expected = {"protanopia": {("near_low", "very_high"): 7.4, ("tight_range", "very_high"): 6.2, ("near_high", "very_high"): 7.4},
                "deuteranopia": {("tight_range", "very_high"): 8.3}, "tritanopia": {}}[kind]  # fmt: skip
    assert pairs == expected  # the solid colors; the colors as drawn: test_report_and_card_accessibility.py


# ------------------------------------------------------------------------------------------------ ARIA and keyboard
FOCUSABLE = {"a", "button", "input", "select", "textarea", "summary"}
VOID = {"br", "img", "input", "col", "meta", "link", "hr", "image", "rect", "path", "line", "circle", "stop"}


class Aria(html.parser.HTMLParser):
    """Collect the accessibility problems of one rendered card: focusable elements inside aria-hidden, charts without
    a text alternative, buttons and links without a name."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.problems, self.charts = [], [], 0

    def _hidden(self):
        return any(e["hidden"] for e in self.stack)

    def handle_starttag(self, tag, attrs):
        """Open an element: its role, its hidden state and the label it carries."""
        a = dict(attrs)
        hidden = a.get("aria-hidden") == "true"
        role = a.get("role", "")
        img_parent = any(e["role"] == "img" for e in self.stack)
        focusable = (tag in FOCUSABLE and (tag != "a" or "href" in a)) or ("tabindex" in a and a["tabindex"] != "-1")
        if focusable and (self._hidden() or hidden):
            self.problems.append(f"focusable <{tag}> inside aria-hidden")
        if tag == "svg" and not img_parent and not self._hidden() and not hidden:
            self.charts += 1
            if role != "img" or not a.get("aria-label", "").strip():
                self.problems.append(f"chart without a text alternative: {str(attrs)[:80]}")
        if tag == "a" and "href" not in a:  # a link without an address: Tab does not reach it (WCAG 2.1.1)
            self.problems.append(f"link without an address: {str(attrs)[:80]}")
        named = tag in ("button", "a") or role in ("button", "link")
        el = {"tag": tag, "hidden": hidden, "role": role, "named": named, "label": a.get("aria-label", ""), "text": ""}
        if tag not in VOID and not tag.endswith("/"):
            self.stack.append(el)

    def handle_startendtag(self, tag, attrs):
        """A self-closing element: opened and, unless it is void, closed at once."""
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1]["tag"] == tag:
            self.handle_endtag(tag)

    def handle_data(self, data):
        """Add text to every open element."""
        for e in self.stack:
            e["text"] += data

    def handle_endtag(self, tag):
        """Close the element and check what it holds."""
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                e = self.stack.pop(i)
                del self.stack[i:]
                if e["named"] and not (e["label"].strip() or e["text"].strip()):
                    self.problems.append(f"<{tag} role={e['role']}> without a name")
                return


def _aria(markup: str) -> Aria:
    p = Aria()
    p.feed(markup)
    p.close()
    return p


# a 14-day GLU2 v3 file (backfill): five-minute readings and the period statistics, enough for the graph cards
GLU2 = textwrap.dedent("""
    const glu2 = () => {
      const now = Math.floor(Date.now() / 1000), n = 14 * 288, rdo = 40 + 7 * 44 + 24, buf = new ArrayBuffer(rdo + n * 6);
      const d = new DataView(buf);
      d.setUint32(0, 0x32554C47, true); d.setUint16(4, 3, true); d.setUint16(6, 0, true); d.setUint32(8, 40, true);
      d.setUint32(12, 40 + 7 * 44, true); d.setUint32(16, rdo, true); d.setUint32(20, n, true); d.setUint32(28, now, true);
      for (let p = 0; p < 7; p++) [120, 30, 25, 6.2, 80, 60, 3, 17, 0.5, 2].forEach((v, i) => d.setFloat32(40 + p * 44 + i * 4, v, true));
      for (let i = 0; i < n; i++) { d.setUint32(rdo + i * 6, now - (n - i) * 300, true); d.setUint16(rdo + i * 6 + 4, 100 + Math.round(60 * Math.sin(i / 20)), true); }
      return buf;
    };
""")
UI_STATES = {
    "sensor.glucocraft_configuration": {
        "state": "ok",
        "last_updated": "a",
        "attributes": {
            "unit": "mg/dL",
            "ranges": {"very_low": 54, "low": 70, "tight_low": 70, "tight_high": 140, "high": 180, "very_high": 250},
        },
    },
    "sensor.glucocraft_status": {
        "state": "current",
        "last_updated": "a",
        "attributes": {"label": "Current", "color": "#43A047"},
    },
    "sensor.glucocraft_last_glucose_reading": {
        "state": "112",
        "last_updated": "a",
        "attributes": {"zone": "tight_range", "unit": "mg/dL", "value_mgdl": 112},
    },
    "sensor.glucocraft_trend_arrow": {"state": "↘", "last_updated": "a", "attributes": {"description": "Falling"}},
    "sensor.glucocraft_14d_consensus_ranges": {
        "state": "80",
        "last_updated": "a",
        "attributes": {
            "has_data": True,
            "time_70_180": 80.2,
            "time_70_140": 60.1,
            "time_below_70": 3.0,
            "time_below_54": 0.5,
            "time_above_180": 16.8,
            "time_above_250": 2.0,
            "mean": 121,
            "std_dev": 30,
            "cv": 24.8,
            "gmi": 6.2,
            "sensor_active": 95,
            "sensor_active_ok": True,
            "min_value": 58,
            "max_value": 260,
        },
    },
}
UI_CARDS = [
    {"type": "glucocraft-readout", "view": "consensus", "hero": True, "path": "/glucocraft-home/consensus"},
    {"type": "glucocraft-pages"},
    {"type": "glucocraft-stats-table", "periods": ["6h", "24h", "7d", "14d"]},
    {"type": "glucocraft-statistics"},
    {"type": "glucocraft-history", "hours": 6},
    {"type": "glucocraft-history", "hours": 336},
    {"type": "glucocraft-daily-range", "days": 14},
    {
        "type": "glucocraft-footer",
        "disclaimer": "For development and testing only. Not for medical use. [Disclaimer](/x/documentation#disclaimer)",
        "license": "GlucoCraft · [AGPL-3.0-only](https://www.gnu.org/licenses/agpl-3.0.html)",
    },
]


@pytest.fixture(scope="module")
def ui_cards() -> dict[str, str]:
    if not NODE:
        pytest.skip("node not available")
    script = (
        STUBS
        + GLU2
        + JS["ui"]
        + textwrap.dedent(f"""
        const states = {json.dumps(UI_STATES, ensure_ascii=False)};
        // the history files (GLU2), and the daily chart's values (/api/glucocraft/data?daily=N: 14 days)
        const daily = Array.from({{ length: 14 }}, (_, i) => ({{ date: `2026-10-${{String(9 + i).padStart(2, "0")}}`,
          mean: 120 + i, min: 70 + i, max: 220 - i }}));
        const hass = {{ states, user: {{ id: "u" }}, fetchWithAuth: async (url) => (String(url).includes("/api/glucocraft/data")
          ? {{ ok: true, json: async () => ({{ unit: "mg/dL", daily }}) }} : {{ ok: true, arrayBuffer: async () => glu2() }}) }};
        globalThis.localStorage = {{ getItem: () => null, setItem() {{}} }};
        const out = {{}};
        for (const c of {json.dumps(UI_CARDS)}) {{
          const el = new (customElements.get(c.type))(); el.setConfig(c); el._w = 700; el._hass = hass; el.hass = hass;
          await el.draw?.();
          await new Promise((r) => setTimeout(r, 0));
          let h = el.shadowRoot.innerHTML;
          if (c.type.includes("history") || c.type.includes("daily")) h += el.shadowRoot.querySelector(".g").innerHTML;
          // the Statistics card draws its two parts into their boxes
          for (const b of c.type === "glucocraft-statistics" ? ["lr", "pt"] : [])
            h = h.replace(`<div class="${{b}}"></div>`, `<div class="${{b}}">${{el.shadowRoot.querySelector("." + b).innerHTML}}</div>`);
          out[c.type + (c.hours || "")] = h;
        }}
        process.stdout.write("\\u0001" + JSON.stringify(out));
    """)
    )
    return node_json(script)


def test_dashboard_cards_text_alternatives_names_and_no_focus_inside_hidden_parts(ui_cards):
    charts = 0
    for name, markup in ui_cards.items():
        p = _aria(markup)
        assert not p.problems, (name, p.problems)
        charts += p.charts
    assert charts >= 3  # the two history graphs and the daily chart, each described
    # the units of the visible tables are read in words
    assert '<span class="sr">80 percent</span>' in ui_cards["glucocraft-stats-table"]
    # the period headers are written in full words, so they need no spoken form of their own
    assert '<th scope="col">6 hours</th>' in ui_cards["glucocraft-statistics"]


class HiddenText(Aria):
    """The text of a rendered card that screen readers do not read (inside an aria-hidden part) and the text they read."""

    def __init__(self):
        super().__init__()
        self.hidden_text, self.read_text = "", ""

    def handle_data(self, data):
        """Collect the text that is hidden from screen readers apart from the rest."""
        super().handle_data(data)
        if self._hidden():
            self.hidden_text += data
        else:
            self.read_text += data


def _texts(markup: str) -> HiddenText:
    p = HiddenText()
    p.feed(markup)
    p.close()
    return p


def test_the_footer_shows_and_reads_the_disclaimer_line_with_its_link(ui_cards):
    footer = ui_cards["glucocraft-footer"]
    assert '<p>For development and testing only. Not for medical use. <a href="/x/documentation#disclaimer"' in footer
    assert "aria-hidden" not in footer
    assert not _aria(footer).problems


def test_no_card_hides_the_disclaimer_line_from_screen_readers(ui_cards):
    """The disclaimer line is read where it stands, in the footer."""
    for name, markup in ui_cards.items():
        assert "For development and testing only" not in _texts(markup).hidden_text, name
    assert "For development and testing only" in _texts(ui_cards["glucocraft-footer"]).read_text


def test_the_headline_card_name_has_the_units_in_words(ui_cards):
    """The headline card is a link to its page, named by its visible title with the units and the mark in words; its
    lines are the link's description, read in words as well."""
    markup = ui_cards["glucocraft-readout"]
    link = re.search(r'<a class="t ctl" href="/glucocraft-home/consensus" aria-describedby="lines">(.*?)</a>', markup)
    assert link, markup[:400]
    name = re.search(r'<span class="sr">([^<]*)</span>', link.group(1)).group(1)
    assert name.startswith("Consensus Ranges — 80 percent in range 70 to 180 milligrams per deciliter (14 days)")
    assert name.endswith(", meets the target")
    assert "%" not in name and "–" not in name and "mg/dL" not in name
    assert '<div class="s" id="lines">' in markup and 'role="button"' not in markup


def test_every_card_root_carries_the_page_language(ui_cards):
    for name, markup in ui_cards.items():
        root = re.search(r"<(?!style|/)(\w[\w-]*)([^>]*)>", re.sub(r"<style>.*?</style>", "", markup, flags=re.DOTALL))
        if root:
            assert 'lang="en"' in root.group(2), (name, root.group(0)[:80])
    for name in ("ui", "report", "docs", "strategy"):
        assert not re.search(r"<ha-card(?![^>]*lang=)", JS[name]), name
    # the display card sets its language as it draws (the catalogue in use; tests/test_accessibility.py renders it)
    assert 'querySelector("ha-card")?.setAttribute("lang", globalThis.glucocraftI18n.htmlLang)' in JS["display-card"]


@needs_node
@pytest.mark.parametrize("tag", ["glucocraft-display-card", "glucocraft-display-card-wide"])
@pytest.mark.parametrize("page", [0, 1, 7, 15, 16])
def test_display_cards_text_alternative_and_named_buttons(tag, page):
    from test_accessibility import _card

    out = _card(tag, page=page)
    # the drawing's description, in the rule-4 order (tests/test_accessibility.py); the About page: its title, and its
    # text after the drawing
    assert len(out["label"]) > 40 if page < 16 else (out["label"] and len(out["desc"]) > 40)
    assert all(lab for lab, _hidden in out["kb"].values())  # every keyboard button is named
    assert not re.search(r"<a\b|tabindex=|<button", out["svg"])  # the drawing itself has nothing to focus
    sizes = [float(x) for x in re.findall(r'font-size="([\d.]+)"', out["svg"])]
    assert min(sizes) >= 12, sorted(set(sizes))  # no text below 12 px


def test_the_statistics_labels_follow_the_square_panel():
    m = re.search(r"const STAT_LABEL_PX = (\d+);", JS["display-card"])
    assert m and int(m.group(1)) == 12
    assert 'font-size="11"' not in JS["display-card"] and ", 11, " not in JS["display-card"]
    # the panel part: the square panel's statistics labels (skipped, with the reason, without the panel configurations)
    if PANELS is None:
        pytest.skip(NO_PANELS)
    text = (PANELS / "glucocraft-panel.yaml").read_text(encoding="utf-8")
    size = re.search(r"id: roboto_12\s+size: (\d+)", text)
    assert size and int(size.group(1)) == int(m.group(1))
    # the label at that size is the panel's TIR text (the substitution of the panel catalogue), the card's word
    label = re.search(r"text_font: roboto_12\s+styles: theme_text_label\s+text: \"\$\{(t_stat_\w+)\}\"", text)
    assert label and label.group(1) == "t_stat_tir"
    texts = PANELS / "i18n" / "generated" / "en" / "texts.yaml"
    word = re.search(r"^\s*t_stat_tir: \"([^\"]*)\"", texts.read_text(encoding="utf-8"), re.MULTILINE)
    assert word and word.group(1) == i18n_text("card.stat.tir") == "TIR"


def test_no_css_text_below_14_px_on_the_dashboard():
    for name in ("ui", "report", "docs", "strategy"):
        sizes = [int(s) for s in re.findall(r"font-size:\s*(\d+)px", JS[name])]
        assert sizes and min(sizes) >= 14, (name, sorted(set(sizes)))


@needs_node
def test_report_page_headings_tables_and_chart_sentences():
    from test_report_outputs import _data

    data = _data(30, True)
    out = node_json(
        STUBS
        + REPORT_PARTS_JS
        + JS["report"]
        + textwrap.dedent(f"""
        const card = new (customElements.get("glucocraft-report"))(); card.setConfig({{}});
        card._hass = {{ states: {{}} }}; card._data = {json.dumps(data)}; card.draw();
        process.stdout.write("\\u0001" + JSON.stringify(REPORT_PARTS.map((s) => card.shadowRoot.querySelector(s).innerHTML).join("")));
    """)
    )
    p = _aria(out)
    assert not p.problems, p.problems
    assert (
        '<h2 class="h2">CGM Report: Continuous Glucose Monitoring</h2>' in out
        and '<h3 class="h3">Time in Ranges</h3>' in out
    )
    assert 'class="h2' not in re.sub(r'<h2 class="h2[^"]*"(?: id="[^"]*")?>', "", out) and "<th>" not in out
    assert '<th scope="row">' in out and '<th scope="col">' in out
    # one summary sentence per chart, with the data, not the title alone
    labels = re.findall(r'role="img" aria-label="([^"]*)"', out)
    assert any(lab.startswith("Time in ranges: very low ") for lab in labels)
    assert any(
        lab.startswith("Glucose Profile — 14 Days: median from ") and "milligrams per deciliter" in lab
        for lab in labels
    )
    assert any(lab.startswith("Hypoglycemia events by time of day: ") for lab in labels)
    assert all(re.search(r"\d", lab) or "no data" in lab or "insufficient" in lab for lab in labels), labels
    # the units of the tables in words; the column headers below / above with their threshold and unit
    assert '<span class="sr">Time below 70 milligrams per deciliter</span>' in out and "percent</span>" in out


# ------------------------------------------------------------------------------------------------ the Documentation
ACCESSIBILITY_HEADINGS = [
    "About This Section", "What Home Assistant Provides", "Screen Readers", "Keyboard and Focus",
    "Color Is Never the Only Cue", "Contrast", "Custom Colors, Graph Range Colors and Backgrounds", "Text Size and Zoom",
    "Notifications", "The CGM Report and Its PDF", "Voice", "Motion and Timing", "Wall Panels",
    "Plain Language", "Known Limitations", "How This Was Checked", "Feedback",
]  # fmt: skip


def _section(sid: str) -> str:
    """The English text of one Documentation section (i18n/en/documentation.json)."""
    return documentation_section(sid)


def test_documentation_accessibility_section():
    sec = _section("accessibility")
    assert re.findall(r"<h3>([^<]+)</h3>", sec) == ACCESSIBILITY_HEADINGS
    flat = sec
    assert ("GlucoCraft is designed to follow WCAG 2.2 level AA where it applies. No conformance claim is made. "
            "Known limitations are listed above.") in flat  # fmt: skip
    # the design and the review are described, and reports are invited; nothing says "untested"
    assert "{cite:apg}" in flat and "are invited to report" in flat
    assert "not yet tested" not in flat.lower() and "untested" not in flat.lower()
    assert "partially conformant" not in flat.lower() and "TO FILL" not in flat
    assert "<b>disabled by default</b>" in flat and "sensor.glucocraft_spoken_summary" in flat
    # the one voice example, marked as not part of GlucoCraft; "not an alarm" is said in the Notifications section only
    assert "These lines are not part of GlucoCraft" in flat and "HassGetState:" in flat and "{cite:haSentences}" in flat
    assert "GlucoCraft ships no voice intents" in flat and "never an alarm" not in flat and "not an alarm" not in flat
    assert "is tagged for screen readers" in flat and "4 full-screen black-and-white inversions" not in flat
    assert "passes the checks of PDF/UA-1" in flat and "names PDF/UA-1 in its metadata" in flat
    assert "not yet been validated" not in flat and "not tagged" not in flat
    assert "2.3.1" not in flat
    assert "by its color only" not in flat  # the zone word is under the value in every setting
    assert "as bug reports at" in flat and "with the area <i>Accessibility</i>" in flat
    assert "No response time is promised" in flat and "<i>accessibility</i>" not in flat
    for word in ("labeled lines", "labeled dashed", "Low limit", "11 px"):
        assert word not in flat, word


def test_documentation_describes_the_zone_attributes_as_published():
    sec = _section("ranges")
    assert "<code>zone_label</code> (the zone word)" not in sec
    assert "the zone word outside the range only" in sec and "with no value inside the range" in sec
    assert "<code>display_word</code>, the zone word the displays show under the value (in every zone" in sec
    assert "labeled dashed lines" not in sec and "Labeled lines" not in sec
    assert C.zone_label("tight_range") is None and C.display_zone_word("tight_range") == "Tight range"


def test_no_arrow_symbols_in_notification_examples_of_the_documentation():
    sec = _section("notifications") + _section("accessibility")
    assert not re.search(r"[↑↗↘↓]", sec)


# ------------------------------------------------------------------------------------------------ the PDF/UA check
def test_the_pdf_ua_check_fails_a_file_on_any_failed_rule():
    """tools/check_pdf_ua.py: a file passes only when no rule fails; clause 5 (the identification, which the CGM Report
    states since veraPDF passed it) is no exception any more."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_pdf_ua", REPO / "tools" / "check_pdf_ua.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    rule = '<rule clause="{c}" testNumber="{n}" status="failed" failedChecks="1"><description>{d}</description></rule>'
    job = '<job><item><name>{name}</name></item><validationReport isCompliant="false"><details>{rules}</details></validationReport></job>'
    mrr = (
        "<report><jobs>"
        + job.format(name="a.pdf", rules="")
        + job.format(name="c.pdf", rules=rule.format(c="5", n="1", d="identification"))
        + job.format(
            name="b.pdf",
            rules=rule.format(c="5", n="1", d="identification") + rule.format(c="7.1", n="3", d="artifact"),
        )
        + "</jobs></report>"
    )
    (a, a_ok, a_failed), (c, c_ok, c_failed), (b, b_ok, b_failed) = tool.results(mrr)
    assert (a, a_ok, a_failed) == ("a.pdf", True, [])
    assert (c, c_ok, c_failed) == ("c.pdf", False, ["5-1 (1 checks): identification"])
    assert (b, b_ok, len(b_failed)) == ("b.pdf", False, 2)
