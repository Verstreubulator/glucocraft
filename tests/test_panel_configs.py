# License: AGPL-3.0-only (LICENSE).
"""The wall panels' example configurations (ESPHome YAML and the C++ headers they include).

The panels are built with ESPHome, so their C++ cannot run here. These tests read the configuration files as text
and check the rules a change could break:

- every panel accepts firmware uploads only with its encryption key;
- the bounds checks of the panel files use 64-bit arithmetic, so a large offset cannot wrap around;
- the value's color, LO / HI included, is GlucoCraft's zone_color (the panels keep no zone palette of their own); the
  graph bands take GlucoCraft's band colors (band_colors), with the AGP colors as the fallback;
- Outline text: off by default and kept across a restart; the graph labels and the labels in a zone color take the
  1-pixel edge only while it is on (lvgl_compat.h), black around light text and white around dark text;
- the panels' background and text colors come from GlucoCraft (panel_background, panel_foreground) through one
  set of LVGL styles; the graph canvases stay black on every background;
- the press-and-hold guard, the About page's swipes and the page changes;
- the zone word under the value: GlucoCraft's display_word (none without it), white, two sizes, hidden while the
  value is not current; the TREND column's arrow or trend word (the setting Trend shown as, kept across a restart;
  the trend word wrapped at the column's width); without a current reading the words in the value's place ("Sensor
  warming up", "Sensor expired", "No current data"), never a number; the expired state ended only by GlucoCraft's
  status or expiry; LO / HI in the minimum and maximum;
- the About page (the icon, GlucoCraft's disclaimer, the version) and the start-up screen (the icon, the name, the
  version, the disclaimer line);
- the saved time zone, the decimal separator of the weekly descriptions, the consensus terms, the status bar text
  color and the source name's punctuation;
- the LVGL draw buffer in PSRAM, one draw layer per graph and no drawing pixel by pixel (set_px);
- one-decimal numbers rounded once, halves away from zero, as the display card; the percentages of the files rounded
  once without an epsilon, as GlucoCraft; beside a weekly page's target or goal two decimals where one would show the
  rule's value (units.shown_against); no negative zero; boolean attributes as Home Assistant sends them ("on" /
  "off"); a Wi-Fi outage reboots only by the panels' own 60-minute check; no glucose value and no reading time in a
  log line at the default level, which is INFO on every panel; the 6-hour fallback before the first file fills every
  cell;
- the display card's minimum-to-maximum look on the period graphs, the zone word in the square panel's weekly
  corner row, GLU2 version 3 and later read, and the project name of each panel type (its board and resolution);
- the configuration forms of ESPHome 2026.10 (light initial_state, the Waveshare enable pins);
- the four landscape files are exactly what the generator makes from the square panel;
- the texts: every text a panel shows is in the text catalogue i18n/<language>.yaml (no text in a label, a lambda or
  a header), the generated files are up to date for every catalogue (the English texts and their disclaimer also
  without ESPHome's fonts), each filled text gets exactly its placeholders,
  the fonts' glyph lists come from the catalogue, and every English text fits on every layout.

The folder comes from conftest.panel_folder (the environment variable GLUCOCRAFT_PANELS, or esphome/ in the
repository); without it the tests are skipped. secrets.yaml is never read.
"""

from __future__ import annotations

import colorsys
import json
import math
import random
import re
import struct
import subprocess
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
import yaml
from glucocraft import const as glucocraft_const
from glucocraft import panel_feed as pf
from glucocraft import units
from glucocraft.const import ZONE_COLORS

from conftest import NO_PANELS, REPO
from conftest import PANELS as _FOUND
from node_runner import needs_node, run_node

ZONE = ZONE_COLORS

# Without the folder the paths below are placeholders that no test opens: every test is skipped.
PANELS = _FOUND or REPO / "esphome"
pytestmark = pytest.mark.skipif(_FOUND is None, reason=NO_PANELS)

SQUARE = PANELS / "glucocraft-panel.yaml"
LANDSCAPE = {
    "waveshare-lcd5": PANELS / "glucocraft-waveshare-lcd5.yaml",
    "waveshare-p4-7b": PANELS / "glucocraft-waveshare-p4-7b.yaml",
    "waveshare-lcd7": PANELS / "glucocraft-waveshare-lcd7.yaml",
    "guition-35": PANELS / "glucocraft-guition-35.yaml",
}
LCD = [SQUARE, *LANDSCAPE.values()]  # the example panel configurations (each has a liquid-crystal display)
BINARY_H = PANELS / "glucocraft_panel_binary.h"
UNITS_H = PANELS / "gc_units.h"
COMPAT_H = PANELS / "lvgl_compat.h"
HEADERS = [UNITS_H, BINARY_H, COMPAT_H]
GENERATOR = PANELS / "tools" / "make_glucocraft_landscape.py"
TOP_LAYER = PANELS / "tools" / "landscape_top_layer.yaml"
ABOUT_WIDE = PANELS / "tools" / "landscape_about.yaml"  # the wide panels' About page
CATALOGUE = PANELS / "i18n" / "en.yaml"
GENERATED = PANELS / "i18n" / "generated" / "en"


def catalogue() -> dict[str, str]:
    """Return the English panel texts (i18n/en.yaml): key -> text."""
    return yaml.safe_load(CATALOGUE.read_text(encoding="utf-8"))


def text_of(key: str) -> str:
    """Return one English panel text."""
    return catalogue()[key]


def substitutions() -> dict[str, str]:
    """Return the generated package's substitutions for English (t_* texts, the About splits, glyphs_*)."""
    return yaml.safe_load((GENERATED / "texts.yaml").read_text(encoding="utf-8"))["substitutions"]


def _panel_version(path: Path) -> str:
    """Return the panel_version substitution of one panel configuration."""
    found = re.findall(r'^  panel_version: "([^"]*)"', path.read_text(encoding="utf-8"), re.MULTILINE)
    assert len(found) == 1, path.name
    return found[0]


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_a_lost_value_means_no_current_data(path):
    """After a renamed value sensor the panel must not show an old number with a fresh age: the newest reading's
    value (a second copy, on the time sensor) differing for over a minute counts as stale; a value that differs for a
    moment (the updates of one reading arrive in any order) is neither stored nor stale."""
    text = read(path)
    assert "attribute: last_reading_mgdl" in text
    assert "millis() - differs_since > 60000UL" in text
    assert "|| id(value_lost);" in text
    assert "fabs(have - want) > 0.05) return;" in text  # store_glucose_reading waits for the matching value


def test_panel_versions_are_well_formed_and_consistent():
    """Every panel_version is MAJOR.MINOR.PATCH; the panels share one version; the README lists it."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    listed = dict(re.findall(r"^\| ([^|]+?) \| (\d+\.\d+\.\d+) \|", readme, re.MULTILINE))
    lcd = {path.name: _panel_version(path) for path in LCD}
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", v) for v in lcd.values()), lcd
    assert len(set(lcd.values())) == 1, lcd
    assert listed["Wall panel configurations"] == lcd[SQUARE.name]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def block(text: str, start: str, end: str) -> str:
    a = text.index(start)
    return text[a : text.index(end, a + len(start))]


# ------------------------------------------------------------------------------------------------ touch
def _touchscreen(path: Path) -> str:
    """Return the touchscreen block of a panel configuration (up to the next top-level key)."""
    text = read(path)
    a = text.index("\ntouchscreen:\n")
    return text[a : re.search(r"\n[a-z_]+:\n", text[a + 1 :]).start() + a + 1]


def test_square_panel_touch_x_axis_is_mirrored_to_match_the_picture():
    """The square panel's mipi_rgb model mirrors the picture but not the touch controller: swipes and holds need the
    touch X axis mirrored (only X; the Y axis and the axes' order are unchanged)."""
    display = block(read(SQUARE), "\ndisplay:\n", "\nsensor:\n")
    assert "platform: mipi_rgb" in display and "model: GUITION-4848S040" in display
    assert "transform:" not in display  # the picture's orientation is the model's own
    touch = _touchscreen(SQUARE)
    assert "platform: gt911" in touch and "display: my_display" in touch
    assert re.search(r"\n    transform:\n      mirror_x: true\n", touch)
    assert "mirror_y" not in touch and "swap_xy" not in touch


@pytest.mark.parametrize("path", LANDSCAPE.values(), ids=lambda p: p.stem)
def test_landscape_panels_keep_their_own_touch_settings(path):
    """The generator replaces the square panel's hardware blocks, so its touch transform never reaches a landscape
    panel (those boards keep their drivers): each landscape touch block is the board's own, from the generator."""
    touch = _touchscreen(path)
    assert "GUITION-4848S040" not in touch
    assert touch.strip("\n") in read(GENERATOR)


# ------------------------------------------------------------------------------------------------ security
@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_ota_uploads_need_the_encryption_key(path):
    text = read(path)
    ota = re.search(r"(?m)^ota:\n((?:[ ].*\n)+)", text)
    assert ota, "no ota block"
    assert re.search(r"(?m)^\s+encryption:", ota.group(1)), "ota without encryption: anyone on the network can upload"
    assert re.search(r"(?m)^api:\n\s+encryption:\n\s+key: !secret \w+_api_key$", text), (
        "the api key comes from a secret"
    )


def test_panel_file_bounds_checks_are_64_bit():
    text = read(BINARY_H)
    fits = re.search(
        r"static inline bool gc_section_fits\(uint64_t offset, uint64_t count, uint64_t item_size, "
        r"uint64_t size\) \{\n\s+return offset <= size && count <= \(size - offset\) / item_size;",
        text,
    )
    assert fits, "gc_section_fits is missing or changed"
    # no 32-bit sum is left: every section of the full and the delta file is checked with it
    for old in (
        "uint32_t stats_end",
        "uint32_t readings_end",
        "uint32_t trends_end",
        "uint32_t delta_readings_end",
        "uint32_t xo =",
    ):
        assert old not in text
    parse = block(text, "bool parse_binary(", "\n}\n")
    for section in (
        "h.stats_offset, 7",
        "h.ranges_offset, 1",
        "h.readings_offset, h.reading_count",
        "backfill::header.trends_offset, tc",
        "gc_section_fits(xo, 1",
    ):
        assert f"gc_section_fits({section}" in parse or section in parse, section
    assert "gc_section_fits(delta_hdr.readings_offset, delta_hdr.reading_count" in text


def test_glucose_statistics_are_not_logged_at_info():
    text = read(BINARY_H)
    assert not re.search(r'ESP_LOGI\("backfill", "Stats', text)
    assert 'ESP_LOGD("backfill", "Stats 6h' in text


@pytest.mark.parametrize("path", [*LCD, *HEADERS], ids=lambda p: p.name)
def test_no_glucose_value_is_logged_at_the_default_level(path):
    """Glucose values (a reading, a minimum or maximum, a statistic) are health data: the INFO, WARN and ERROR lines,
    which appear in a log shared at the default level, carry counts, offsets, times and file names only."""
    text = read(path)
    formats = re.findall(r'ESP_LOG[IWE]\(\s*"[^"]*",\s*"([^"]*)"', text)
    for fmt in formats:
        assert not re.search(r"mg/dL|mmol|range %|mean|tir=|%[-+ 0#]*\d*(?:\.\d+)?f", fmt), fmt


# A reading's time or value in a log call's format or arguments: a timestamp (ts, cutoff, epoch), a date and time
# (struct tm, "%02d:%02d"), a glucose value
_TIME_OR_VALUE = re.compile(
    r"\bts\b|_ts\b|\bts=|cutoff|epoch|tm_(?:year|mon|mday|hour|min)|%02d:%02d|glucose_value|mgdl|\bvalue\b|\bmean\b"
)


@pytest.mark.parametrize("path", [*LCD, *HEADERS], ids=lambda p: p.name)
def test_no_reading_time_or_value_is_logged_at_the_default_level(path):
    """A reading's time and value are health data: the INFO, WARN and ERROR lines (a log shared at the panels' INFO
    level holds them) print neither, in their format or their arguments; the times are DEBUG lines. logger.log
    actions log a fixed text."""
    text = read(path)
    calls = re.findall(r"ESP_LOG[IWE]\((.*?)\);", text, re.DOTALL)
    assert calls or path in (UNITS_H, COMPAT_H)  # the search finds the log lines of the files that have them
    for call in calls:
        assert not _TIME_OR_VALUE.search(call), call
    for action in re.findall(r"logger\.log:(.*)", text):
        assert re.fullmatch(r' "[^"%]*"', action), action  # a fixed text, no format arguments


def test_the_reading_times_of_the_files_are_debug_lines():
    """The file's first and last reading time and the delta's cutoff are logged at DEBUG only."""
    text = read(BINARY_H)
    assert 'ESP_LOGD("backfill", "Readings from ts %u to %u",' in text
    assert 'ESP_LOGD("backfill", "Delta cutoff ts=%u", (unsigned) cutoff);' in text
    assert 'ESP_LOGI("backfill", "GLU2 v%u: %u readings",' in text


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_logger_level_of_every_panel_is_info(path):
    """Each example panel logs at INFO (not DEBUG or VERBOSE), so that the DEBUG lines (statistics, reading times)
    are not compiled in and a shared log holds no health data."""
    logger = block(read(path), "\nlogger:\n", "\n\n")
    assert re.findall(r"^  level: (\w+)$", logger, re.MULTILINE) == ["INFO"], logger
    assert "logs:" not in logger  # no component raised above INFO


# ------------------------------------------------------------------------------------------------ colors
def test_zone_keys_are_glucocrafts_zones_and_the_value_takes_glucocrafts_zone_color():
    """The value's color is GlucoCraft's zone_color, never a panel table; the panels keep GlucoCraft's range colors
    (zone_colors, with GlucoCraft's default palette until they arrive) only for the graphs' limit marks."""
    text = read(UNITS_H)
    keys = re.search(r"K\[Z_COUNT\] = \{([^}]*)\}", text).group(1)
    assert re.findall(r'"(\w+)"', keys) == list(ZONE_COLORS)
    assert "limit_color" not in text
    m = re.search(r"DEFAULT_ZONE_COLORS\[Z_COUNT\] = \{([^}]*)\}", text)
    panel = [int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{6})", m.group(1))]
    assert panel == [int(glucocraft_const.ZONE_COLORS[z][1:], 16) for z in ZONE_COLORS]
    assert "inline bool set_zone_colors(const char *s) { return read_zone_map(s, zone_table()); }" in text
    for path in LCD:
        config = read(path)
        assert "attribute: zone_colors" in config and "gcu::set_zone_colors(x.c_str())" in config, path.stem
        # the value: GlucoCraft's zone_color of the reading, never the table
        assert "gcu::zone_color(" not in config, path.stem


# The graph bands' default colors (the CGM Report's AGP colors): const.BAND_COLORS, which
# tests/test_accessibility_checks.py holds to the build contract
BAND_DEFAULTS = {zone: color.upper() for zone, color in glucocraft_const.BAND_COLORS.items()}


def test_default_band_colors_are_the_agp_colors():
    text = read(UNITS_H)
    m = re.search(r"DEFAULT_BAND_COLORS\[Z_COUNT\] = \{([^}]*)\}", text)
    panel = [int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{6})", m.group(1))]
    assert panel == [int(BAND_DEFAULTS[z][1:], 16) for z in ZONE_COLORS]


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_graph_bands_use_the_published_band_colors(path):
    text = read(path)
    assert "attribute: band_colors" in text and "gcu::set_band_colors(x.c_str())" in text
    # no band table with a fixed color, and none with the zone colors
    assert not re.search(r"\{[^{}\n]*,\s*0x(?:C62828|EF5350|FFC107|4CAF50|42A5F5|1565C0)\}", text)
    assert text.count("gcu::band_color(gcu::Z_VERY_LOW)") == 2
    assert "gcu::zone_color(gcu::" not in text


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_a_limit_reading_takes_glucocrafts_zone_color(path):
    """LO / HI in GlucoCraft's zone color like every value (a LO reading is always very low, a HI reading always very
    high): the panel keeps no color of its own for a limit reading; the range colors it reads (zone_colors) serve only
    the graphs' limit marks."""
    text = read(path)
    update = block(text, "  - id: update_glucose_display\n", "\n  - id: ")
    assert "id(global_glucose_color) = (int) strtol(zc.c_str() + 1, nullptr, 16);" in update
    assert "zone_color(" not in update and "limit_color" not in text
    colors = block(text, "    id: ha_zone_colors\n", "\n  - platform: ")
    assert "gcu::set_zone_colors(x.c_str())" in colors and "- script.execute: redraw_all" in colors


# ------------------------------------------------------------------------------------------------ Outline text
def test_outline_edge_is_one_pixel_in_the_opposite_lightness():
    """The canvas text and the labels take the same edge: one pixel left, right, up and down, in black around a light
    text and white around a dark one (the higher WCAG contrast), and only while the setting is on."""
    text = read(COMPAT_H)
    assert "constexpr int8_t LVC_EDGE[4][2] = {{-1, 0}, {1, 0}, {0, -1}, {0, 1}};" in text
    assert "1.05f / (l + 0.05f) >= (l + 0.05f) / 0.05f ? lv_color_hex(0xFFFFFF) : lv_color_hex(0x000000)" in text
    edged = block(text, "inline void lvc_draw_text_edged(", "\n}\n")
    assert "if (lvc_outline_text()) {" in edged and "dsc->color = lvc_edge_color(color);" in edged
    assert edged.rstrip().endswith("lvc_draw_text(canvas, x, y, w, dsc, text);")
    label = block(text, "inline void lvc_label_edge_cb(", "\n}\n")
    assert "e) {\n  if (!lvc_outline_text()) return;" in label
    assert "dsc.color = lvc_edge_color(dsc.color);" in label
    assert "LV_EVENT_DRAW_MAIN_BEGIN" in read(COMPAT_H) and "lvc_draw_text_outlined" not in text


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_outline_text_follows_glucocrafts_setting(path):
    text = read(path)
    sensor = block(text, "    attribute: text_outline\n", "  - platform:")
    # Home Assistant sends a boolean attribute to an ESPHome device as "on" / "off" (esphome/manager.py)
    assert 'if (x != "on" && x != "off") return false;' in sensor and 'const bool on = x == "on";' in sensor
    assert '"True"' not in text and '"False"' not in text
    custom = block(text, "    attribute: custom_ranges_and_goals\n", "  - platform:")
    assert 'if (x == "on" || x == "off") id(gc_custom_ranges_on) = x == "on";' in custom
    assert "lvc_outline_text() = on;" in sensor and "lv_obj_invalidate(lv_screen_active());" in sensor
    assert "script.execute: redraw_all" in sensor
    glob = block(text, "  - id: text_outline_on\n", "  - id: ")
    assert "type: bool" in glob and "restore_value: yes" in glob and "initial_value: 'false'" in glob
    boot = block(text, "    - priority: -10\n", "    - priority: -100\n")
    assert "lvc_outline_text() = id(text_outline_on);" in boot and "lvc_label_edge(id(glucose_value));" in boot
    # every graph label takes the edge (the loading text of a graph without a file too); none is drawn plain
    assert text.count("lvc_draw_text_edged(") == 17 and "lvc_draw_text(" not in text
    assert ("lvc_label_edge(id(weekly_mini_value));" in boot) is (path == SQUARE)


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_status_bar_text_color_follows_the_bar(path):
    text = read(path)
    for label in ("time_since_update", "warmup_countdown_label", "custom_ranges_label", "sensor_expiry_label"):
        upd = re.search(r"- lvgl\.label\.update:\n\s+id: " + label + r"\n(?:\s+text: .*\n)?\s+text_color: (.*)", text)
        assert upd and "gcu::text_on(id(global_time_since_color))" in upd.group(1), label


# ------------------------------------------------------------------------------------------------ panel colors
# the theme styles and the black design's color each one holds by default (style_definitions)
THEME_STYLES = {
    "theme_background": {"bg_color": "0x000000"},
    "theme_box": {"bg_color": "0x000000", "border_color": "0x808080"},
    "theme_text": {"text_color": "0xFFFFFF", "image_recolor": "0xFFFFFF"},
    "theme_text_secondary": {"text_color": "0xAAAAAA"},
    "theme_text_label": {"text_color": "0x888888"},
    "theme_text_progress": {"text_color": "0x4CAF50"},
}


def _style_definitions(text: str) -> dict[str, dict[str, str]]:
    """Return the style_definitions of the LVGL section: style id -> {property: value}."""
    defs = block(text, "  style_definitions:\n", "\n\n")
    styles: dict[str, dict[str, str]] = {}
    for m in re.finditer(r"    - id: (\w+).*\n((?:      \w+: \S+.*\n?)+)", defs):
        styles[m.group(1)] = dict(re.findall(r"      (\w+): (\S+)", m.group(2)))
    return styles


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_lcd_panels_read_the_panel_colors(path):
    """Each LCD panel reads panel_background and panel_foreground and applies them with one script at boot and on a
    change; the styles hold the black design by default, so black shows no change."""
    text = read(path)
    for attr, setter in (("panel_background", "set_panel_background"), ("panel_foreground", "set_panel_foreground")):
        sensor = block(text, f"    attribute: {attr}\n", "  - platform:")
        assert f"lambda: 'return gcu::{setter}(x.c_str());'" in sensor
        assert "script.execute: apply_panel_theme" in sensor
    assert "gcu::boot_theme();" in block(text, "  on_boot:\n", "\nesp32")
    assert "script.execute: apply_panel_theme" in block(text, "    - priority: -10\n", "    - priority: -100\n")
    assert _style_definitions(text) == THEME_STYLES
    apply = block(text, "  - id: apply_panel_theme\n", "\n  - id: ")
    for style, props in THEME_STYLES.items():
        for prop in props:
            assert f"lv_style_set_{prop}(id({style}), " in apply, (style, prop)
    assert "lv_obj_report_style_change(nullptr);" in apply


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_no_fixed_black_white_or_gray_outside_the_graphs_and_the_status_bar(path):
    """Every widget the black design shows in black, white or gray uses a theme style; only the style definitions,
    the status bar's labels (their color follows the bar) and the screen without Home Assistant (a fixed black with
    white text, whatever the panel colors, so that the change shows) keep a fixed value."""
    text = read(path)
    widgets = block(text, "\nlvgl:\n", "\ninterval:\n")
    grays = r"(?:text_color|bg_color|border_color|image_recolor): 0x(?i:FFFFFF|000000|888888|AAAAAA|808080)\b"
    fixed = len(re.findall(grays, widgets))
    in_defs = len(re.findall(grays, block(text, "  style_definitions:\n", "\n\n")))
    in_bar = len(re.findall(grays, block(text, "id: status_bar\n", "id: no_ha_screen\n")))
    no_ha = len(re.findall(grays, block(text, "id: no_ha_screen\n", "\n      # >>> About screen")))
    assert in_defs == 7 and in_bar == 5  # the status bar's five labels: gcu::text_on() of the bar's color
    assert no_ha == 5  # the black background and its four white lines
    assert fixed == in_defs + in_bar + no_ha


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_graph_canvases_stay_black_with_white_lines_and_labels(path):
    """The graphs keep their own black canvas (#0a0a0a), white line and white labels on every panel background."""
    text = read(path)
    canvases = re.findall(r"- canvas:\n((?:\s{4,}\w+: .*\n)+)", text)
    assert len(canvases) == 8  # the 6-hour graph, the six period graphs and the weekly graph
    assert all("styles:" not in c and "bg_color" not in c for c in canvases)
    six_hour = block(text, "  - id: draw_glucose_graph\n", "\n  - id: ")
    assert "id: glucose_canvas\n          color: 0x0a0a0a" in six_hour
    assert "lv_canvas_fill_bg(canvases[iteration], lv_color_hex(0x0a0a0a), LV_OPA_COVER);" in text
    assert "lv_canvas_fill_bg(id(trend_canvas), lv_color_hex(0x0a0a0a), LV_OPA_COVER);" in text
    graphs = six_hour + block(text, "  - id: draw_period_graphs\n", "\n  - id: ")
    assert "lv_color_t line_color = lv_color_hex(0xFFFFFF);" in graphs
    assert "label_dsc.color = lv_color_hex(0xFFFFFF);  // graph labels white" in graphs
    weekly = block(text, "  - id: draw_trend_page\n", "\n  - id: ")
    assert "gcu::theme_color(" not in graphs + weekly


def test_theme_grays_on_black_are_the_black_design():
    """The derived grays are mixes of the text color into the background whose levels are the black design's grays,
    so the default (white on black) gives exactly the style definitions' values; the minimums are WCAG 2.2's."""
    units = read(UNITS_H)
    grays = dict(re.findall(r"\{TH_(\w+), 0x([0-9A-F]{2}), [\d.]+f\}", units))
    minimums = dict(re.findall(r"\{TH_(\w+), 0x[0-9A-F]{2}, ([\d.]+)f\}", units))
    assert grays == {"TEXT_SECONDARY": "AA", "TEXT_LABEL": "88", "FRAME": "80"}
    assert minimums == {"TEXT_SECONDARY": "4.5", "TEXT_LABEL": "4.5", "FRAME": "3.0"}
    for role, style, prop in (
        ("TEXT_SECONDARY", "theme_text_secondary", "text_color"),
        ("TEXT_LABEL", "theme_text_label", "text_color"),
        ("FRAME", "theme_box", "border_color"),
    ):
        assert THEME_STYLES[style][prop] == "0x" + grays[role] * 3
    assert "DEFAULT_PANEL_BACKGROUND = 0x000000;" in units and "DEFAULT_PANEL_TEXT = 0xFFFFFF;" in units
    # no text color received yet: black or white, whichever contrasts more with the background
    assert "const int bg = r.background, fg = r.text >= 0 ? r.text : text_on(bg);" in units


# the panel's rule for a colored status text (gc_units.h shade_for), recomputed: the hue kept, the HSL lightness
# moved towards the text color's in steps of 1 % until 4.5:1
def _luminance(rgb: int) -> float:
    def ch(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * ch(rgb >> 16 & 255) + 0.7152 * ch(rgb >> 8 & 255) + 0.0722 * ch(rgb & 255)


def _contrast(a: int, b: int) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)))
    return (lb + 0.05) / (la + 0.05)


def _shade_for(color: int, bg: int, fg: int, minimum: float = 4.5) -> int:
    if _contrast(color, bg) >= minimum:
        return color
    h, lightness, s = colorsys.rgb_to_hls(*[(color >> k & 255) / 255 for k in (16, 8, 0)])
    target = colorsys.rgb_to_hls(*[(fg >> k & 255) / 255 for k in (16, 8, 0)])[1]
    step = 0.01 if target > lightness else -0.01
    c = color
    while _contrast(c, bg) < minimum and abs(target - lightness) > 0.005:
        lightness = target if abs(target - lightness) < 0.01 else lightness + step
        r, g, b = (round(v * 255) for v in colorsys.hls_to_rgb(h, lightness, s))
        c = r << 16 | g << 8 | b
    return c


@pytest.mark.parametrize("background", [0x000000, 0xFFFFFF, 0xF5F5DC, 0x003366, 0x1E1E1E, 0x777777, 0xC62828])
def test_the_loading_text_keeps_4_5_to_1_in_its_own_hue(background):
    """The loading text (green), the one colored text outside the graphs and the status bar, keeps 4.5:1 on any panel
    background, as a shade of its own hue; on black it is exactly the black design's color. No red status word is
    left (the words without a current reading are in the text color)."""
    units = read(UNITS_H)
    hues = dict(re.findall(r"\{TH_(\w+), 0x([0-9A-F]{6})\}", units))
    assert hues == {"PROGRESS": "4CAF50"} and "TH_ATTENTION" not in units
    assert "THEME_HUE_MIN_RATIO = 4.5f;" in units
    assert "t[hue.role] = shade_for(hue.color, bg, fg, THEME_HUE_MIN_RATIO);" in units
    fg = 0xFFFFFF if 1.05 / (_luminance(background) + 0.05) >= (_luminance(background) + 0.05) / 0.05 else 0x000000
    shade = _shade_for(0x4CAF50, background, fg)
    assert _contrast(shade, background) >= 4.5, hex(shade)
    if background == 0x000000:
        assert shade == 0x4CAF50


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_colored_status_texts_use_the_theme_shades(path):
    """No fixed red or green small text outside the graphs and the status bar: the loading text takes its style."""
    text = read(path)
    widgets = block(text, "\nlvgl:\n", "\ninterval:\n")
    assert "text_color: 0xE53935" not in widgets.replace(block(text, "  style_definitions:\n", "\n\n"), "")
    assert "theme_text_attention" not in text
    loading = re.search(r"id: loading_progress_label\n(?:\s+\w+: .*\n)+", text).group(0)
    assert "styles: theme_text_progress" in loading and "text_color" not in loading
    assert "col = 0xE53935" not in text


# ------------------------------------------------------------------------------------------------ touch and pages
def test_square_hold_changes_page_only_on_a_graph_page_with_weekly_data():
    text = read(SQUARE)
    hold = block(text, "      - obj:\n          id: glucose_data_container", "          widgets:")
    lam = hold.index("if (!backfill_has_trends() || id(current_page) > 6) return;")
    cond = hold.index("lambda: 'return id(current_page) == 7;'")
    show = hold.index("id: page_trend_tir")
    assert lam < cond < show, "the page change must sit inside the condition (a lambda's return ends only itself)"
    assert "script.execute: sync_page_mode" in hold[show:]


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_page_changes_update_the_top_layer_at_once(path):
    text = read(path)
    assert "  - id: sync_page_mode\n" in text
    for script in ("nav_next", "nav_prev"):
        assert "script.execute: sync_page_mode" in block(text, f"  - id: {script}\n", "\n  - id: ")
    weekly = block(text, "id: trend_info_container", "widgets:")
    assert "script.execute: sync_page_mode" in weekly
    about = block(text, "id: about_screen", "widgets:")
    assert "script.execute: nav_next" in about and "script.execute: nav_prev" in about
    assert "lvgl.widget.hide: about_screen\n            - lvgl.page.show" not in about


# ------------------------------------------------------------------------------------------------ the zone word and the trend
# Without a current reading, per panel (worst_case_fit.py measures them there): the stack's width and height, the
# words' font, the last reading's font, its time's font, and whether the age is on a line of its own
NO_READING_LAYOUT = {
    "glucocraft-panel": ("248", "160", "roboto_40", "roboto_20", "roboto_16", True),
    "glucocraft-waveshare-lcd7": ("344", "212", "roboto_40", "roboto_26", "roboto_18", False),
    "glucocraft-waveshare-lcd5": ("440", "256", "roboto_40", "roboto_26", "roboto_20", False),
    "glucocraft-waveshare-p4-7b": ("440", "256", "roboto_40", "roboto_26", "roboto_20", False),
    "glucocraft-guition-35": ("186", "134", "roboto_32", "roboto_14", "roboto_14", True),
}


def test_the_words_without_a_current_reading_are_the_display_cards():
    """gc_units.h no_reading_words: during a warm-up "Sensor warming up"; without current data "Sensor expired" after
    the stated expiry, else "No current data"; none while the reading is current. The same words as the card's."""
    units = read(UNITS_H)
    fn = block(units, "inline const char *no_reading_words(bool warm_up, bool no_current, bool expired) {", "\n}\n")
    assert "if (warm_up) return gct::no_reading_warming_up;" in fn
    assert "if (!no_current) return nullptr;" in fn
    assert "return expired ? gct::no_reading_sensor_expired : gct::no_reading_no_current_data;" in fn
    panel = yaml.safe_load((PANELS / "i18n" / "en.yaml").read_text(encoding="utf-8"))
    card = json.loads((REPO / "custom_components/glucocraft/i18n/en/frontend.json").read_text(encoding="utf-8"))
    for key in ("warming_up", "sensor_expired", "no_current_data"):
        assert panel[f"panel.no_reading.{key}"] == card[f"card.no_reading.{key}"]


def test_the_last_reading_under_the_words_is_the_display_cards():
    """gc_units.h: the line under the words, "Last reading: 64 mg/dL" in the display unit (a limit reading "Last
    reading: LO"; nothing without a reading), and when it was taken, "Today at 17:39" / "Yesterday at 17:39" / the date,
    by the calendar day in Home Assistant's time zone, with its age in minutes, hours and minutes, or days and hours
    (whole minutes, rounded down). The same texts as the display card's (card.no_reading.*)."""
    units = read(UNITS_H)
    fn = block(units, "inline void last_reading(char *buf, size_t n, float mg, int lim) {", "\n}\n")
    assert "if (std::isnan(mg) || mg <= 0) { if (n) buf[0] = '\\0'; return; }" in fn
    assert 'fill(buf, n, gct::no_reading_last_reading_limit, {{"value", t}});' in fn
    assert "fmt(v, sizeof(v), mg);" in fn and '{{"value", v}, {"unit", unit()}}' in fn
    ago = block(units, "inline void reading_ago(char *buf, size_t n, unsigned long minutes) {", "\n}\n")
    for part in (
        'if (minutes < 60) fill(buf, n, gct::no_reading_ago_minutes, {{"minutes", minutes}});',
        'else if (minutes < 1440) fill(buf, n, gct::no_reading_ago_hours, {{"hours", minutes / 60}, {"minutes", minutes % 60}});',
        'else fill(buf, n, gct::no_reading_ago_days, {{"days", minutes / 1440}, {"hours", minutes % 1440 / 60}});',
    ):
        assert part in ago, part
    when = block(units, "inline bool reading_when(char *buf, size_t n, uint32_t reading, uint32_t now) {", "\n}\n")
    assert "civil_days(lt.tm_year + 1900L, lt.tm_mon + 1, lt.tm_mday) -" in when  # by the calendar, not 24 hours
    assert "if (days == 0) fill(buf, n, gct::no_reading_when_today" in when
    assert "else if (days == 1) fill(buf, n, gct::no_reading_when_yesterday" in when
    assert "day_month(date, sizeof(date), lr.tm_mon, lr.tm_mday, false);" in when
    panel = yaml.safe_load((PANELS / "i18n" / "en.yaml").read_text(encoding="utf-8"))
    card = json.loads((REPO / "custom_components/glucocraft/i18n/en/frontend.json").read_text(encoding="utf-8"))
    keys = (
        "last_reading",
        "last_reading_limit",
        "when_today",
        "when_yesterday",
        "when_date",
        "ago_minutes",
        "ago_hours",
        "ago_days",
        "time_ago",
        "ago_line",
    )
    for key in keys:
        assert panel[f"panel.no_reading.{key}"] == card[f"card.no_reading.{key}"], key


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_no_current_reading_shows_words_in_the_value_place(path):
    """Without a current reading (a warm-up, or no current data by GlucoCraft's flag or the panel's own count) the
    value's place shows the words, never a number, at the zone word's large size wrapped at its width, and under them
    the last reading and when it was taken, stacked and centered (a flex column, hidden lines take no room); the zone
    word is empty, the TREND column and the 5-minute delta show "---". After the sensor's stated expiry, while
    readings arrive, the value keeps its zone color and the word under it the text color: no gray (the red status bar
    carries the expiry). A current limit reading shows the limit screen in the value's place, with the trend blank and
    the delta "---". The change follows at once when a flag flips; the square panel's corner value on the weekly pages
    shows "---"."""
    text = read(path)
    width, height, word, line, time, split = NO_READING_LAYOUT[path.stem]
    box = block(text, "id: no_reading_box\n", "widgets:")
    for part in (f"width: {width}\n", f"height: {height}\n", "hidden: true", "flex_flow: COLUMN", "pad_row: 0"):
        assert part in box, part
    assert "flex_align_main: CENTER" in box and "clickable: false" in box
    for lid, font, mode, hidden in (
        ("no_reading_label", word, "WRAP", False),
        ("last_reading_label", line, "CLIP", True),
        ("reading_when_label", time, "CLIP", True),
        ("reading_ago_label", time, "CLIP", True),
    ):
        label = re.search(r"- label:\n\s+id: " + lid + r"\n(?:\s+\w+: .*\n)+", text).group(0)
        assert f"text_font: {font}\n" in label and f"width: {width}\n" in label and f"long_mode: {mode}" in label
        assert "styles: theme_text\n" in label and ("hidden: true" in label) == hidden, lid
    none = re.search(r"- label:\n\s+id: trend_none_label\n(?:\s+\w+: .*\n)+", text).group(0)
    assert 'text: "---"' in none and "styles: theme_text_secondary" in none
    upd = block(text, "  - id: update_glucose_display\n", "\n  - id: ")
    assert (
        "const char *words = gcu::no_reading_words(id(sensor_warmup_active), id(data_stale) || id(local_stale)," in upd
    )
    assert (
        "lv_label_set_text(id(no_reading_label), words);\n                  id(update_last_reading).execute();" in upd
    )
    for shown in (
        "no_reading_box), words != nullptr",
        "limit_box), limit_now",
        "glucose_value), words == nullptr && !limit_now",
        "trend_none_label), words != nullptr",
    ):
        assert f"show(id({shown});" in upd
    assert "const bool limit_now = words == nullptr && gcu::limit() != 0 && !isnan(id(glucose_current).state);" in upd
    assert "const bool known = id(global_trend_angle) != -999 && words == nullptr && !limit_now;" in upd
    assert 'const char *delta = (words != nullptr || limit_now) ? "---" : id(global_delta_str).c_str();' in upd
    # no gray after the stated expiry: the value in its zone color, the word in the text color
    assert "text_color: !lambda return lv_color_hex(id(global_glucose_color));" in upd
    assert "text_color: !lambda return lv_color_hex(gcu::theme_color(gcu::TH_TEXT));" in upd
    assert "TH_TEXT_LABEL" not in upd and "sensor_expired) ?" not in upd
    flip = block(text, "            if (ls != id(local_stale)) {", "\n          }\n")
    assert "id(update_glucose_display).execute();" in flip
    stale = block(text, "    id: ha_data_stale\n", "\nbutton:\n")
    assert "- script.execute: update_glucose_display" in stale
    warm = block(text, "    id: ha_sensor_warmup\n", "\n  - platform: ")
    assert "- script.execute: refresh_glucose_block" in warm and "- script.execute: update_glucose_display" in warm
    for gone in (
        "stale_data_container",
        "expired_sensor_container",
        "stale_last_reading",
        "global_stale",
        "t_stale_",
        "t_expired_",
    ):
        assert gone not in text, gone
    # the last reading and its time under the words (update_last_reading), the age split where the layout says so
    last = block(text, "  - id: update_last_reading\n", "\n  - id: ")
    for part in (
        f"constexpr bool SPLIT_AGE = {'true' if split else 'false'};",
        "gcu::last_reading(line[0], sizeof(line[0]),",
        "gcu::reading_when(when, sizeof(when), at, (uint32_t) now.timestamp)",
        "gcu::reading_ago(ago, sizeof(ago),",
        'gct::no_reading_ago_line, {{"ago", ago}}',
        'gct::no_reading_time_ago, {{"when", when}, {"ago", ago}}',
        "if (shown[i] == line[i]) continue;",
    ):
        assert part in last, part
    # no time line while the reading's time cannot be trusted (ahead of the clock)
    assert "if (!id(time_untrusted) && now.is_valid() && at > 0 &&" in last
    tick = text[text.index("  - interval: 1s\n") :]
    assert "id(last_reading_epoch) = have_age && !untrusted && (uint32_t) time.timestamp > age_s" in tick
    assert "if (!lv_obj_has_flag(id(no_reading_box), LV_OBJ_FLAG_HIDDEN)) id(update_last_reading).execute();" in tick
    assert "if (!lv_obj_has_flag(id(limit_box), LV_OBJ_FLAG_HIDDEN)) id(update_limit_screen).execute();" in tick
    if path == SQUARE:
        mini = block(text, "  - id: update_weekly_mini\n", "  # <<< weekly mini value (script)")
        assert (
            "const bool none = gcu::no_reading_words(id(sensor_warmup_active), id(data_stale) || id(local_stale),"
            in mini
        )
        assert 'snprintf(v, sizeof(v), "%s", none ? "---" : id(global_glucose_str).c_str());' in mini
        assert "const bool gray = none;" in mini and "if (gray) col = gcu::theme_color(gcu::TH_TEXT_LABEL);" in mini
        assert "if (!none) ang = id(global_trend_angle);" in mini  # no arrow without a current reading


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_flag_decides_the_words_and_the_status_which(path):
    """One no-current-data system: the words take the value's place while GlucoCraft's flag or the panel's own count
    says no current data, "Sensor expired" after the stated expiry, else "No current data"; after the stated expiry a
    current reading is shown in the glucose block (in its zone color), as on the display card. A reading never changes the
    expired state: only GlucoCraft's status (ha_status) or its expiry (cgm_sensor_expiry) does."""
    text = read(path)
    assert "sensor_expired_overlay_shown" not in text and "refresh_overlays" not in text
    reading = block(text, "    id: glucose_current\n", "\n  - platform: ")
    assert "id(sensor_expired) =" not in reading
    for start, end in (
        ("    id: ha_status\n", "\n  - platform: "),
        ("    id: cgm_sensor_expiry\n", "\nbinary_sensor:\n"),
    ):
        status = block(text, start, end)
        assert "id(refresh_glucose_block).execute();" in status and "id(update_glucose_display).execute();" in status
    refresh = block(text, "  - id: refresh_glucose_block\n", "\n  - id: ")
    assert (
        "const bool words = gcu::no_reading_words(id(sensor_warmup_active), id(data_stale) || id(local_stale),"
        in refresh
    )
    assert "lv_obj_clear_flag(id(glucose_data_container), LV_OBJ_FLAG_HIDDEN);" in refresh


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_all_three_words_throb(path):
    """The words in the value's place throb (lvgl_compat.h lvc_throb) whenever they show, all three of them ("Sensor
    warming up", "Sensor expired", "No current data": gc_units.h no_reading_words, the same rule as the words
    themselves), never on a hidden block, while GlucoCraft's setting Fade the words when there is no current reading
    is on (kept in flash, on by default), for the first 30 minutes of each episode, counted from the reading's age
    (gc_units.h throb_window), so a restart does not start them again. A change of the warm-up flag refreshes the
    throb. The status bar's "Sensor expiry passed" throbs until a new sensor (no 30-minute limit), stopped by the same
    setting. The square panel's corner value ("---") does not throb."""
    text = read(path)
    assert re.search(r"- id: flash_on\n\s+type: bool\n\s+restore_value: yes\n\s+initial_value: 'true'", text)
    setting = block(text, "    id: ha_flash_no_current_data\n", "\n  - platform: ")
    assert "attribute: flash_no_current_data" in setting and "id(flash_on) = on;" in setting
    assert "script.execute: refresh_glucose_block" in setting
    refresh = block(text, "  - id: refresh_glucose_block\n", "\n  - id: ")
    assert (
        "const bool words = gcu::no_reading_words(id(sensor_warmup_active), id(data_stale) || id(local_stale),\n"
        "                                                   id(sensor_expired)) != nullptr;" in refresh
    )
    assert (
        "lvc_throb(id(no_reading_label), words && !owned && id(flash_on) && id(words_throb) && !id(no_ha_screen_on));"
        in refresh
    )
    assert "!id(sensor_warmup_active)" not in refresh  # no exception for a warm-up
    # the 30 minutes, counted from the reading's age (or the warm-up's start) every second
    tick = text[text.index("  - interval: 1s\n") :]
    assert "const bool throb = gcu::throb_window(id(sensor_warmup_active), wmin, have_age && !untrusted, age_s," in tick
    assert "if (throb != id(words_throb)) {" in tick and "id(refresh_glucose_block).execute();" in tick
    # the expiry text: while the status is expired, or expiring past the stated time; the same setting stops it
    assert 'id(expiry_throb) = st == "expired" || (st == "expiring_soon" && remaining_minutes <= 0);' in tick
    assert "lvc_throb(id(sensor_expiry_label), id(expiry_throb) && id(flash_on));" in tick
    assert text.count("lvc_throb(") == 2
    warm = block(text, "    id: ha_sensor_warmup\n", "\n  - platform: ")
    assert "- script.execute: refresh_glucose_block" in warm
    if path == SQUARE:
        mini = block(text, "  - id: update_weekly_mini\n", "  # <<< weekly mini value (script)")
        assert "const bool gray = none;" in mini
    else:
        assert "weekly_mini" not in text


def test_the_throb_is_a_smooth_fade_at_half_a_hertz():
    """lvc_throb: the label's text opacity from full to about 30 % in 1.5 s, eased, and back in 1.5 s, repeated: about 0.33 Hz,
    never invisible; stopping restores full opacity. The same rhythm as the display card's gc-throb."""
    compat = (PANELS / "lvgl_compat.h").read_text(encoding="utf-8")
    fn = block(compat, "inline void lvc_throb(lv_obj_t *label, bool on) {", "\n}\n")
    assert "constexpr uint32_t LVC_THROB_HALF_MS = 1500;" in compat
    assert "constexpr lv_opa_t LVC_THROB_MIN_OPA = 77;" in compat and round(77 / 255, 2) == 0.30
    for call in (
        "lv_anim_set_values(&a, LV_OPA_COVER, LVC_THROB_MIN_OPA);",
        "lv_anim_set_duration(&a, LVC_THROB_HALF_MS);",
        "lv_anim_set_reverse_duration(&a, LVC_THROB_HALF_MS);",
        "lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);",
        "lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);",
        "lv_obj_set_style_text_opa(label, LV_OPA_COVER, LV_PART_MAIN);",
    ):
        assert call in fn, call


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_word_under_the_value_is_glucocrafts_display_word(path):
    text = read(path)
    assert "trend_text" not in text and "global_trend_str" not in text
    assert re.search(
        r"id: ha_display_word\n\s+entity_id: sensor\.glucocraft_last_glucose_reading\n\s+attribute: display_word", text
    )
    label = re.search(r"- label:\n\s+id: display_word_label\n(?:\s+\w+: .*\n)*?\s+long_mode: CLIP", text).group(0)
    assert "styles: theme_text" in label and "text_color" not in label  # the text color of the panel colors
    upd = block(text, "  - id: update_glucose_display\n", "\n  - id: ")
    # display_word alone: the zone word, never the trend word in its place
    assert 'if (id(ha_display_word).has_state() && dw != "unavailable" && dw != "unknown")' in upd
    assert 'id(global_display_word) = current ? word : std::string("");' in upd
    # not current (no current data, warm-up) and a limit reading (the limit screen takes the place): no word
    assert re.search(
        r"current = !isnan\(id\(glucose_current\)\.state\) && !id\(data_stale\) && !id\(local_stale\) &&"
        r"\s+!id\(sensor_warmup_active\) && gcu::limit\(\) == 0;",
        upd,
    )
    # two sizes: the smaller where the larger is wider than the label, measured as LVGL draws it
    assert "lv_obj_set_style_text_font(id(display_word_label), lvc_font_that_fits(id(display_word_label)," in upd


TREND_WORD_LAYOUT = {  # the trend word's font and width per panel (as worst_case_fit.py measures them)
    "glucocraft-panel": ("roboto_26", "111"),
    "glucocraft-waveshare-lcd7": ("roboto_36", "160"),
    "glucocraft-waveshare-lcd5": ("roboto_40", "205"),
    "glucocraft-waveshare-p4-7b": ("roboto_40", "205"),
    "glucocraft-guition-35": ("roboto_20", "87"),
}


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_trend_shown_as_arrow_or_word(path):
    """GlucoCraft's setting Trend shown as (trend_display, kept across a restart) chooses the arrow or the trend word
    (the Trend arrow attribute word) in the TREND column: a label in the text color, wrapped at the column's width, in
    the layout's size; neither while no trend is known; the arrow without the word. The square panel's weekly corner
    value keeps the arrow."""
    text = read(path)
    assert re.search(r"- id: trend_word_on\n\s+type: bool\n\s+restore_value: yes\n\s+initial_value: 'false'", text)
    setting = block(text, "    id: ha_trend_display\n", "\n  - platform: ")
    assert "entity_id: sensor.glucocraft_configuration\n    attribute: trend_display" in setting
    assert 'if (x != "arrow" && x != "word") return false;' in setting and "id(trend_word_on) = on;" in setting
    assert "script.execute: update_glucose_display" in setting
    assert re.search(r"id: ha_trend_word\n\s+entity_id: sensor\.glucocraft_trend_arrow\n\s+attribute: word\n", text)
    assert re.search(
        r"id: ha_trend_description\n\s+entity_id: sensor\.glucocraft_trend_arrow\n\s+attribute: description\n", text
    )
    label = re.search(r"- label:\n\s+id: trend_word_label\n(?:\s+\w+: .*\n)+", text).group(0)
    font, width = TREND_WORD_LAYOUT[path.stem]
    assert f"text_font: {font}\n" in label and f"width: {width}\n" in label
    assert "long_mode: WRAP" in label and "styles: theme_text\n" in label and "hidden: true" in label
    upd = block(text, "  - id: update_glucose_display\n", "\n  - id: ")
    assert "const bool as_word = known && id(trend_word_on) && !id(global_trend_word).empty();" in upd
    assert "if (!known || as_word) {\n                  lv_obj_add_flag(id(trend_arrow), LV_OBJ_FLAG_HIDDEN);" in upd
    assert "lv_label_set_text(id(trend_word_label), id(global_trend_word).c_str());" in upd
    assert 'id(global_trend_angle) = -999;\n              id(global_trend_word) = "";' in upd  # unknown: neither
    if path == SQUARE:  # the weekly pages' corner value: the arrow only
        mini = block(text, "  - id: update_weekly_mini\n", "  # <<< weekly mini value (script)")
        assert "global_trend_word" not in mini and "weekly_mini_arrow" in mini


def test_trend_words_are_glucocrafts():
    """The panel catalogue's trend words (measured by worst_case_fit.py, their letters in the fonts) are the five
    words GlucoCraft sends as the Trend arrow attribute word."""
    backend = json.loads((REPO / "custom_components/glucocraft/i18n/en/backend.json").read_text(encoding="utf-8"))
    panel = {
        k.removeprefix("panel.word.trend."): v for k, v in catalogue().items() if k.startswith("panel.word.trend.")
    }
    assert panel == {k.removeprefix("trend.word."): v for k, v in backend.items() if k.startswith("trend.word.")}


def test_zone_word_of_each_zone_is_glucocrafts():
    """The panel catalogue's zone words (measured by worst_case_fit.py, their letters in the fonts) are the words
    GlucoCraft sends for each zone; the two zones beside the tight range are both "In range"."""
    backend = json.loads((REPO / "custom_components/glucocraft/i18n/en/backend.json").read_text(encoding="utf-8"))
    key = {"near_low": "in_range", "near_high": "in_range"}
    assert {zone: text_of(f"panel.word.zone.{key.get(zone, zone)}") for zone in ZONE} == {
        zone: backend[f"zone.word.{zone}"] for zone in ZONE
    }
    # the word of a limit reading in the tables and the graph warning
    assert text_of("panel.word.zone.sensor_limit") == backend["limit.word"]
    assert "zone_word(" not in read(UNITS_H)  # the panels show GlucoCraft's display_word; no mapping of their own


# ------------------------------------------------------------------------------------------------ sensor warm-up
def test_warm_up_text_is_one_rule_for_every_panel():
    """The status bar's warm-up minutes come from GlucoCraft's started_at through one helper (gc_units.h)."""
    header = read(UNITS_H)
    assert "inline long warm_up_minutes(const std::string &started_at, uint32_t now) {" in header
    helper = block(header, "inline void warm_up_text(", "\n}\n")
    assert 'gct::status_warm_up_minutes, {{"minutes", mins}}' in helper and "gct::status_warm_up, {}" in helper
    for path in LCD:
        text = read(path)
        assert re.search(
            r"id: ha_warmup_started\n\s+entity_id: binary_sensor\.glucocraft_sensor_warm_up\n\s+attribute: started_at",
            text,
        ), path.name
        assert "gcu::warm_up_text(" in text, path.name
        assert "sscanf(id(ha_warmup_started)" not in text, path.name  # no parse of its own


# ------------------------------------------------------------------------------------------------ local only
OUTBOUND = (
    "http_request:",
    "platform: esp32_hosted",
    "pool.ntp.org",
    "https://",
    "esphome.github.io",
    "web_server:",
    "mqtt:",
    "dashboard_import:",
)


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_panels_contact_nothing_outside_the_home_network(path):
    """No update entity, no public time server and no internet address: a panel talks only to Home Assistant and the
    local time server (ntp_router)."""
    code = "\n".join(line for line in read(path).splitlines() if not line.lstrip().startswith("#"))
    for needle in OUTBOUND:
        assert needle not in code, f"{path.name}: {needle}"
    sntp = code[code.index("platform: sntp") :]
    sntp = sntp[: re.search(r"\n\S", sntp).start()]  # up to the next top-level key
    servers = re.findall(r"\$\{\w+\}|[\w-]+(?:\.[\w-]+)+", sntp.split("servers:", 1)[1])
    assert servers == ["${ntp_router}"], servers
    assert 'GC_HA_BASE=\\"http://${ha_host}:${ha_port}\\"' in code  # the panel files from Home Assistant only
    assert not re.search(r"^update:", code, re.MULTILINE), path.name  # no update entity
    assert set(re.findall(r'http://[^\s"\\]*', code)) == {"http://${ha_host}:${ha_port}"}  # Home Assistant only


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_first_value_of_the_warm_up_and_no_current_data_flags_after_a_boot_is_used(path):
    """ESPHome runs on_state for the first value after a boot only with trigger_on_initial_state: both flags whose
    on_state sets the panel's state have it, so a panel started during a warm-up or without current data shows it."""
    text = read(path)
    for entity in ("binary_sensor.glucocraft_sensor_warm_up", "binary_sensor.glucocraft_no_current_data"):
        block = text[text.index(f"entity_id: {entity}") :]
        block = block[: block.index("on_state:")]
        assert "trigger_on_initial_state: true" in block, f"{path.name}: {entity}"


def test_p4_chip_revision_is_a_substitution_for_the_board_in_use():
    """engineering_sample (an ESP32-P4 below chip revision v3.0) is set by the substitution of the same name, so a
    builder sets it for the board, in the starter file, without editing the board configuration."""
    text = read(LANDSCAPE["waveshare-p4-7b"])
    assert re.search(r'(?m)^  engineering_sample: "(true|false)"', text.split("\npackages:", 1)[0])
    esp32 = block(text, "\nesp32:\n", "\npsram:\n")
    assert "  engineering_sample: ${engineering_sample}" in esp32
    for path in LCD:
        if path != LANDSCAPE["waveshare-p4-7b"]:
            assert "engineering_sample" not in read(path), path.name


def test_clock_header_starts_without_a_made_up_time_or_date():
    """Until a clock source has set the time, the header shows the clock's own first text and no date, in every time
    and date format."""
    assert text_of("panel.header.clock_initial") == "--:--" and text_of("panel.header.date_initial") == ""
    assert "initial_value: '\"--:--\"'" in read(SQUARE)


def test_the_weekly_lines_and_targets_come_from_the_file_alone():
    """The line under a weekly page's title and its target or goal come from GlucoCraft's file (GCX1) alone: the
    catalogue holds no fallback descriptions, the panel no consensus targets of its own (a file without them shows the
    title alone and no target line); the text font still holds the file lines' symbols."""
    assert not [k for k in catalogue() if k.startswith("panel.weekly.") and k.endswith(".description")]
    assert "_description" not in (GENERATED / "gc_text.h").read_text(encoding="utf-8")
    for path in LCD:
        pages = block(read(path), "          TrendPageDef pages[9] = {", "};")
        assert pages.count('"", ') == 9 and pages.count("NAN, false}") == 9, path.name
    assert "≤" in substitutions()["glyphs_text"]  # "Consensus ≤36%" (panel_feed.py)


def test_p4_keeps_the_read_only_coprocessor_firmware_sensor():
    """The P4 7B reads its Wi-Fi co-processor's version over the board's own link and has no update entity."""
    text = read(LANDSCAPE["waveshare-p4-7b"])
    assert 'name: "Wi-Fi Co-processor Firmware"' in text
    assert "esp_hosted_get_coprocessor_fwversion(&v)" in text
    assert "Wi-Fi Co-processor Update" not in text and "\nupdate:\n" not in text
    assert "coprocessor_update" not in read(GENERATOR)


def test_the_about_page_has_no_note_and_no_license_line():
    """The About page shows the icon, the disclaimer and the version line; the note and the license line are gone (the
    disclaimer names the license and that there is no warranty)."""
    about = block(read(SQUARE), "id: about_screen", "# <<< About screen")
    for gone in ("t_about_restriction", "t_about_license", "theme_text_note"):
        assert gone not in about, gone
    assert "panel.about.restriction" not in CATALOGUE.read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------------------ the time at the limit
def test_limit_time_trailer_is_read_from_both_files():
    text = read(BINARY_H)
    assert 'memcmp(data + size - 4, "GCL2", 4)' in text
    assert "gc_read_limit_time(data, size, backfill::limit_lo_s, backfill::limit_hi_s, 7);" in text
    assert (
        "gc_read_limit_time(backfill::ds_buf, backfill::ds_size, backfill::limit_lo_s, backfill::limit_hi_s, 2);"
        in text
    )
    for fn in ("get_stat_limit_lo", "get_stat_limit_hi"):
        assert f"uint32_t {fn}(int " in text
    assert "is_limit" not in text and "GCL1" not in text


def test_unrounded_minimum_and_maximum_are_read_from_both_files():
    """The GCM1 block (panel_feed.extremes_block): the minimum and maximum are rounded once, when shown."""
    text = read(BINARY_H)
    assert 'memcmp(end - 4, "GCM1", 4)' in text
    assert "gc_read_extremes(data, size, backfill::stat_min, backfill::stat_max, 7);" in text
    assert "gc_read_extremes(backfill::ds_buf, backfill::ds_size, backfill::stat_min, backfill::stat_max, 2);" in text
    assert "float get_stat_min(int p)            { return backfill::stat_min[p]; }" in text
    assert "float get_stat_max(int p)            { return backfill::stat_max[p]; }" in text


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_minimum_and_maximum_are_measured_numbers(path):
    """A reading at the sensor's limit gives no value: gc_min / gc_max write a number, or --- without one."""
    text = read(path)
    assert "fmt_extreme" not in text and "is_limit" not in text
    assert "auto gc_min = [&](lv_obj_t* l, int p) { gc_glu(l, p, get_stat_min(p)); };" in text
    assert "auto gc_max = [&](lv_obj_t* l, int p) { gc_glu(l, p, get_stat_max(p)); };" in text
    assert "gcu::fmt(a, sizeof(a), wk_min);" in text and "gcu::fmt(b2, sizeof(b2), wk_max);" in text


# ------------------------------------------------------------------------------------------------ the About page, start-up
@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_one_about_page_in_a_17_page_cycle(path):
    text = read(path)
    nxt, prv = block(text, "  - id: nav_next\n", "\n  - id: "), block(text, "  - id: nav_prev\n", "\n  - id: ")
    assert "id(current_page) >= 16 ? 0 : id(current_page) + 1" in nxt and "script.execute: show_outer_page" in nxt
    assert "id(current_page) == 0 ? 16 : id(current_page) - 1" in prv and "script.execute: show_outer_page" in prv
    for script in (nxt, prv):  # no page change while the start-up screen is shown (the else branch)
        assert "script.execute: end_startup\n          else:" in script
    outer = block(text, "  - id: show_outer_page\n", "\n  - id: ")
    assert "if (id(current_page) == 16) lv_obj_remove_flag(id(about_screen), LV_OBJ_FLAG_HIDDEN);" in outer
    assert "id: page_about" in outer and "17" not in outer
    for gone in ("terms_splash", "page_disclaimer", "cp == 17"):
        assert gone not in text, gone


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_start_up_screen_shows_5_seconds_and_a_swipe_ends_it(path):
    text = read(path)
    boot = block(text, "    - priority: -100\n", "\nesp32:")
    assert "- delay: 15s\n        - script.execute: end_startup" in boot
    for gone in ("startup_key", "splash_continue", '"Continue"', "global_preferences->sync()", "delay: 20s"):
        assert gone not in text, gone
    assert "startup_key" not in read(UNITS_H)
    for script in ("nav_next", "nav_prev"):  # a swipe on the start-up screen ends it
        nav = block(text, f"  - id: {script}\n", "\n  - id: ")
        assert nav.index("lambda: 'return id(startup_active);'") < nav.index("script.execute: end_startup")
    end = block(text, "  - id: end_startup\n", "\n  # ====")
    assert "lambda: 'return id(startup_active);'" in end and "lvgl.widget.hide: startup_screen" in end  # only once


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_no_on_screen_buttons_every_page_by_swipe(path):
    text = read(path)
    assert "on_click" not in text and "Next >" not in text and "- button:" not in text
    for screen in ("id: startup_screen", "id: about_screen"):
        head = block(text, screen, "widgets:")
        assert "on_swipe_left:\n            - script.execute: nav_next" in head
        assert "on_swipe_right:\n            - script.execute: nav_prev" in head


# ------------------------------------------------------------------------------------------------ the disclaimer
@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_about_page_shows_the_disclaimer(path):
    """GlucoCraft's disclaimer in its short form (const.py DISCLAIMER_SHORT) in one block under its first sentence as
    the title, then the
    footer: a thin line in the frame color, the version line and the copyright line. The square panel: the icon
    centered on top and the texts centered in the room above the footer; a wide panel: the icon in a column of its own
    on the left, the title, the disclaimer and the footer centered in the column to its right
    (tools/landscape_about.yaml)."""
    from glucocraft import const as C

    text = read(path)
    assert text_of("panel.about.title") == C.DISCLAIMER_LEAD
    assert f"{C.DISCLAIMER_LEAD} {text_of('panel.about.disclaimer')}" == C.DISCLAIMER_SHORT_PLAIN
    assert "t_about_disclaimer_" not in text  # no part of it split around the icon
    about = block(text, "id: about_screen", "# <<< About screen")
    order = [
        about.index(x)
        for x in (
            "src: about_icon_img",
            'text: "${t_about_title}"',
            'text: "${t_about_disclaimer}"',
            "border_side: TOP",
            'text: "${t_about_version}"',
            'text: "${t_about_copyright}"',
        )
    ]
    assert order == sorted(order)
    assert "styles: theme_box" in about  # the line follows the panel colors (the frame color)
    assert "flex_grow: 1" in about and "flex_align_main: CENTER" in about  # centered in the room above the footer
    head = block(text, "id: about_screen", "widgets:")
    assert ("flex_flow: COLUMN" in head) is (path == SQUARE) and ("flex_flow: ROW" in head) is (path != SQUARE)


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_about_page_ends_with_the_copyright_line(path):
    """Under the version line: the copyright line (AGPLv3 section 0, "Appropriate Legal Notices"), in the version's
    font, the same words as on the display card, its two lines at the catalogue's line break."""
    about = block(read(path), "id: about_screen", "# <<< About screen")
    version, copyright_ = about.index('text: "${t_about_version}"'), about.index('text: "${t_about_copyright}"')
    assert version < copyright_ and about.rstrip().endswith('text: "${t_about_copyright}"')
    fonts = re.findall(r"text_font: (\w+)   # about version font", about)
    assert len(fonts) == 2 and fonts[0] == fonts[1]
    line = text_of("panel.about.copyright")
    assert line == "Copyright (C) 2026 The GlucoCraft authors.\nLicense: gnu.org/licenses/agpl-3.0"
    assert line.replace("\n", " ") == f"{glucocraft_const.COPYRIGHT}. License: gnu.org/licenses/agpl-3.0"
    frontend = json.loads((REPO / "custom_components/glucocraft/i18n/en/frontend.json").read_text(encoding="utf-8"))
    assert frontend["card.about.copyright"] == line


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_start_up_screen_shows_the_icon_name_version_and_line(path):
    from glucocraft import const as C

    splash = block(read(path), "# >>> start-up screen", "# <<< start-up screen")
    order = [
        splash.index(x)
        for x in ("src: about_icon_img", "${t_startup_name}", "${t_startup_version}", "${t_startup_line}")
    ]
    assert order == sorted(order)
    assert text_of("panel.startup.name") == "GlucoCraft"
    # the disclaimer line, one sentence per line, centered
    line = text_of("panel.startup.line")
    assert line.replace("\n", " ") == C.DISCLAIMER_LINE
    assert line.split("\n") == ["For development and testing only.", "Not for medical use."]
    assert "text_align: CENTER" in splash
    assert text_of("panel.startup.version") == "Panel version {panel_version}"
    for gone in ("t_terms_heading", "t_terms_short_form"):
        assert gone not in splash, gone


def test_no_retired_self_description_on_the_panels():
    for repeated in (
        "Built for its author's own personal use",
        "built for its author's own personal use",
        "personal project",
        "Only its author",
        "shared, not offered",
        "Never for children",
        "Do not rely",
    ):
        assert repeated not in read(SQUARE) and repeated not in CATALOGUE.read_text(encoding="utf-8"), repeated


# ------------------------------------------------------------------------------------------------ GMI in mmol/mol
def _panel_gmi_functions() -> dict:
    """Return gc_units.h's GMI functions as Python, transcribed from the header's own text (the C++ cannot run here):
    gmi_mmol_mol (from the mean), gmi_mmol_from_pct and gmi_pct_from_mmol (the weekly GMI page's axis)."""
    units = read(UNITS_H)
    factor = float(re.search(r"constexpr double MGDL_PER_MMOLL_D = ([0-9.]+);", units).group(1))
    env = {"floor": math.floor, "MGDL_PER_MMOLL_D": factor}
    for name in ("gmi_mmol_mol_exact", "gmi_mmol_mol", "gmi_mmol_from_pct", "gmi_pct_from_mmol"):
        arg, body = re.search(rf"inline \w+ {name}\((?:double|float) (\w+)\) {{ return (.+?); }}", units).groups()
        body = re.sub(r"\((?:long|double|float)\) ", "", body)
        exec(f"def {name}({arg}):\n    return {body}", env)  # noqa: S102 - the header's own arithmetic
    return env


def _f32(v: float) -> float:
    """The value as the files carry it (float32)."""
    return struct.unpack("<f", struct.pack("<f", v))[0]


def test_panels_gmi_in_mmol_mol_is_the_gmi_formula_from_the_mean():
    """The panels write GMI in mmol/mol as the backend and the display card do: 12.71 + 4.70587 x mean glucose in mmol/L
    (Bergenstal et al., 2018), whole numbers, from the mean of the same period or week; never converted from the
    percentage with the master equation for laboratory A1C (10.929 x (% - 2.15))."""
    from glucocraft.units import gmi_mmol_mol, round_half_up

    g = _panel_gmi_functions()
    for tenth in range(400, 4001):  # 40.0 ... 400.0 mg/dL
        mean = _f32(tenth / 10)
        assert g["gmi_mmol_mol"](mean) == round_half_up(gmi_mmol_mol(mean)), mean
    assert g["gmi_mmol_mol"](_f32(154.0)) == 53  # GMI 7.0 % (Bergenstal et al., 2018, Table 1)
    # the axis of the weekly GMI page: the mean behind the percentage, and back
    for pct in (5.0, 6.5, 7.0, 9.25, 12.9):
        mean = (pct - 3.31) / 0.02392
        assert g["gmi_mmol_from_pct"](pct) == pytest.approx(12.71 + 4.70587 * mean / 18.016, abs=1e-9)
        assert g["gmi_pct_from_mmol"](g["gmi_mmol_from_pct"](pct)) == pytest.approx(pct, abs=1e-9)
    units = read(UNITS_H)
    assert "10.929" not in units and "mean_mgdl > 0 ? gmi_mmol_mol(mean_mgdl)" in units
    for path in LCD:
        text = read(path)
        assert "10.929" not in text, path.name
        calls = re.findall(r"gcu::fmt_gmi\(([^;]*)\);", text)
        assert calls and all(len(c.split(", ")) >= 4 and "mean(" in c for c in calls), (path.name, calls)


@needs_node
def test_panels_and_the_display_card_show_the_same_gmi_in_mmol_mol():
    """The same mean (float32, as in the file) gives the same whole mmol/mol on the panels and the display card, from
    the mean and, without one, from the percentage (gmiMmolFromMean, gmiPctToMmol)."""
    g = _panel_gmi_functions()
    # the display card's GMI rule: glucocraft-shared.js, the cards' shared module
    shared = (REPO / "custom_components" / "glucocraft" / "www" / "glucocraft-shared.js").read_text(encoding="utf-8")
    js = shared + "\nconst { gmiMmolFromMean, gmiPctToMmol } = globalThis.glucocraftShared;"
    means = [_f32(t / 10) for t in range(400, 4001, 7)]
    pcts = [round(4 + k * 0.01, 2) for k in range(0, 900, 7)]
    script = (
        js
        + f"\nconst m = {means}, q = {pcts};\n"
        + "process.stdout.write(JSON.stringify([m.map(gmiMmolFromMean), q.map((x) => Math.round(gmiPctToMmol(x)))]));"
    )
    from_mean, from_pct = json.loads(run_node(script, prelude=False).splitlines()[-1])  # after the version line
    assert [g["gmi_mmol_mol"](m) for m in means] == from_mean
    assert [math.floor(g["gmi_mmol_from_pct"](x) + 0.5) for x in pcts] == from_pct


# ------------------------------------------------------------------------------------------------ time, text, labels
@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_saved_time_zone_is_applied_at_boot(path):
    text = read(path)
    assert "gcu::boot_tz();" in text and "gcu::keep_tz();" in text and "gcu::mark_ha_time_sync();" in text
    assert "gcu::set_utc();" not in text
    units = read(UNITS_H)
    assert "tz_pref().load(&s) && s.magic == SAVED_TZ_MAGIC" in units and "tz_pref().save(&saved);" in units


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_weekly_description_uses_the_decimal_separator(path):
    text = read(path)
    assert "gcu::sep_copy(desc, sizeof(desc)," in text
    assert "lv_label_set_text(id(trend_desc_label), def.desc);" not in text


@pytest.mark.parametrize("path", [*LCD, GENERATOR, CATALOGUE], ids=lambda p: p.stem)
def test_statistics_labels_use_the_consensus_terms(path):
    text = read(path)
    assert "Crit Lo" not in text and "Crit Hi" not in text
    assert "V. Low" not in text and "V. High" not in text  # full words, not abbreviations
    if path == CATALOGUE:
        assert text_of("panel.stat.very_low") == "Very low" and text_of("panel.stat.very_high") == "Very high"
    elif path == GENERATOR:
        assert '("very_low", "crit_low")' in text and '("very_high", "crit_high")' in text
    else:
        assert all(u in text for u in ("${t_stat_very_low}", "${t_stat_very_high}"))


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_source_name_punctuation_is_plain(path):
    assert "gcu::plain_punctuation(" in read(path)


def test_no_on_screen_buttons():
    assert "Next >" not in read(SQUARE)  # no on-screen buttons (navigation by swipe)


# ------------------------------------------------------------------------------------------------ memory and drawing
@pytest.mark.parametrize(
    "path", [SQUARE, *[p for k, p in LANDSCAPE.items() if k != "waveshare-p4-7b"]], ids=lambda p: p.stem
)
def test_lvgl_draw_buffer_is_not_in_internal_ram(path):
    # ESPHome's LVGL tries internal RAM first for a buffer of a quarter of the screen or less
    m = re.search(r"(?m)^  buffer_size: (\d+)%$", read(path))
    assert m and int(m.group(1)) >= 50


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_graphs_draw_on_one_layer_without_per_pixel_invalidation(path):
    text = read(path)
    assert "lv_canvas_set_px" not in text
    # a batch ends with lvc_end(), or with lvc_end_shapes() and, after the data, lvc_texts() (the labels over the data)
    assert text.count("lvc_begin(") == text.count("lvc_end(") + text.count("lvc_end_shapes(") >= 8
    assert text.count("lvc_end_shapes(") == 7 and text.count("lvc_texts(") == 8  # 6 hours + the 6 periods
    period = block(text, "  - id: draw_period_graphs\n", "\n  - id: ")
    assert "count: 6" in period and "if (iteration == 5)" in period and "- delay: 50ms" in period
    hourly = block(text, "  - id: backfill_hourly\n", "\n  - id: ")
    assert hourly.count("backfill_load_period(") == 6 and hourly.count("- delay: 100ms") >= 6


def test_compat_helpers_queue_on_the_open_layer():
    text = read(COMPAT_H)
    for helper in ("lvc_fill_rect", "lvc_draw_line"):
        body = block(text, f"inline void {helper}(", "\n}\n")
        assert "lvc_layer_for(canvas, &own_layer, &own)" in body, helper
    assert "dsc->text_local = 1;" in text


def test_compat_draws_the_texts_after_the_shapes():
    """No grid line may cover an axis label. Inside a batch a text is only noted, and lvc_end()
    queues the noted texts after all the batch's shapes; the display card does the same (_textsLast)."""
    text = read(COMPAT_H)
    draw = block(text, "inline void lvc_draw_text(", "\n}\n")
    assert "b.texts.push_back(" in draw and draw.index("b.texts.push_back(") < draw.index("lv_canvas_init_layer")
    finish = block(text, "inline void lvc_finish_batch(", "\n}\n")
    assert finish.index("lvc_label_on(") < finish.index("lv_canvas_finish_layer(")
    assert "lvc_finish_batch(b)" in block(text, "inline void lvc_begin(", "\n}\n")
    assert "lvc_finish_batch(b)" in block(text, "inline void lvc_end(", "\n}\n")
    card = read(REPO / "custom_components" / "glucocraft" / "www" / "glucocraft-display-card.js")
    assert card.count("this._textsLast(") == 3  # the 6-hour graph, the period graphs, the weekly pages


def test_p4_resets_its_wifi_chip_before_every_restart():
    """The Waveshare P4 7B holds its ESP32-C6 in reset (GPIO54 low, the esp32_hosted reset
    pin, active high) for 20 ms before every restart, so that the C6 starts afresh with the ESP32-P4; only the P4."""
    p4 = read(LANDSCAPE["waveshare-p4-7b"])
    shutdown = block(p4, "  on_shutdown:\n", "  on_boot:\n")
    assert "gpio_set_level(GPIO_NUM_54, 0);" in shutdown and "delay(20);" in shutdown
    assert re.search(r"esp32_hosted:\n(?:  .*\n)*  reset_pin: GPIO54\n(?:  .*\n)*  active_high: true", p4)
    for other in (SQUARE, *[v for k, v in LANDSCAPE.items() if k != "waveshare-p4-7b"]):
        assert "on_shutdown:" not in read(other), other.name


def test_every_graph_line_has_the_black_underlay_and_the_labels_last():
    """Every graph is drawn the same way: bands, grid, data on a 4-px black underlay,
    then the labels, on every graph of the panels and the display card."""
    text = read(SQUARE)
    six = block(text, "--- Draw data line (direct pixels", "6-hour graph drawn")
    assert "lvc_vline(canvas_obj, x0 + k, y0 - 1, y0 + 2, black)" in six and six.rindex("lvc_texts(") > six.index(
        "pass == 0"
    )
    weekly = block(text, "The weekly line(s) after the grid", "lvc_end(id(trend_canvas));")
    assert "const int lw = underlay ? 4 : 2, dot = underlay ? 5 : 3;" in weekly
    assert text.index("The weekly line(s) after the grid") > text.index("Vertical grid lines and X-axis week labels")
    card = read(REPO / "custom_components" / "glucocraft" / "www" / "glucocraft-display-card.js")
    assert card.count('stroke="#000000" stroke-width="4"') == 3  # the 6-hour line, the mean line, the weekly lines
    assert "return this._textsLast(out + weekly)" in card


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_no_value_label_over_the_time_labels(path):
    """A value label must not stand on a time label ("0" over "12pm" on the 24-hour graph): on the 6-hour and period graphs a grid line closer
    than 28 px to the bottom keeps its line but not its value label, which would reach into the time labels' row
    (drawn at the graph's height less 19 px); the weekly chart already leaves out such lines (ch - 24). The same rule on
    the display card."""
    text = read(path)
    six = block(text, "for (float g = first_line; g <= glucose_max; g += step) {", "// --- Title ---")
    assert (
        six.index("lvc_fill_rect(canvas_obj, 0, gy, canvas_width, 1, gc);")
        < six.index("if (gy > canvas_height - 28) continue;")
        < six.index("gcu::axis_label(lbl, sizeof(lbl), g, step);")
    )
    period = block(text, "for (float g = first_line; g <= gmax; g += step) {", "// Helper: draw title")
    assert (
        period.index("lvc_fill_rect(cv, 0, gy, CW, 1, gc);")
        < period.index("if (gy > CH - 28) continue;")
        < period.index("gcu::axis_label(lbl, sizeof(lbl), g, step);")
    )
    # the time labels' rows sit 19 px above each graph's bottom, inside the 28 px kept free
    ch = int(re.search(r"const int CH = (\d+);", text).group(1))
    assert "canvas_height - 19, tsz.x + 4, &label_dsc, lbl);" in text
    assert f"lvc_draw_text_edged(cv, x0 - 2, {ch - 19}, tsz.x + 4, &ld, lbl);" in text
    card = read(REPO / "custom_components" / "glucocraft" / "www" / "glucocraft-display-card.js")
    assert "if (gy <= GH - 28) out += this._ot(5, gy - 7, this._axisText(gd, step)" in card
    assert 'out += this._ot(lc, GH - 14, lbl, 14, 0xFFFFFF, "middle", "hanging");' in card


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_esphome_2026_10_forms(path):
    text = read(path)
    assert "restore_mode:" not in text
    assert re.search(r"initial_state:.*\n\s+state: true\n\s+restore_state: none", text)


@pytest.mark.parametrize("key", ["waveshare-lcd5", "waveshare-lcd7"])
def test_waveshare_enable_pin_shares_pin_2(key):
    text = read(LANDSCAPE[key])
    assert re.search(r"enable_pin:.*\n\s+- ch422g: ch422g_hub\n\s+number: 2\n\s+allow_other_uses: true", text)
    assert re.search(r"number: 2\n\s+allow_other_uses: true\s+# also the display's enable pin", text)


def test_square_uses_the_mipi_rgb_model():
    text = read(SQUARE)
    assert "platform: st7701s" not in text
    assert re.search(r"- platform: mipi_rgb\n\s+model: GUITION-4848S040\n\s+id: my_display\n\s+spi_id: lcd_spi", text)


# ------------------------------------------------------------------------------------------------ rounding, Wi-Fi, state
def _f32(v: float) -> float:
    """Return v as the float32 a panel file holds."""
    return struct.unpack("<f", struct.pack("<f", v))[0]


def test_one_decimal_numbers_round_halves_away_from_zero_as_the_display_card():
    """num(), pct1() and fmt_gmi() round the file's float32 once, halves away from zero, as the display card's toFixed
    (printf "%.1f" rounds an exact half to even: 1.25 gave "1.2" on the panel and "1.3" on the card)."""
    text = read(UNITS_H)
    num = block(text, "inline void num(char *buf, size_t n, float v, int dec) {", "\n}\n")
    assert "lround((double) v * (double) scale)" in num and "%.*f" not in num
    pct1 = block(text, "inline void pct1(", "\n}\n")
    assert "num(d, sizeof(d), v, 1);" in pct1 and "%.1f" not in pct1
    assert "num(p, sizeof(p), pct, 1);" in block(text, "inline void fmt_gmi(", "\n}\n")
    # the model: a float32 times 10 is exact in double, so only an exact half rounds up
    for v in (1.25, 1.15, 12.35, 99.95, 0.05):
        assert _f32(v) * 10.0 == float(Decimal(_f32(v)) * 10)

    def model(v: float) -> int:
        return int((Decimal(_f32(v)) * 10).to_integral_value(rounding=ROUND_HALF_UP))

    assert model(1.25) == 13  # an exact half: up (printf: 12)
    assert model(1.15) == 11  # 1.1499999761...: down, as toFixed


# Python models of gc_units.h: C's lround on a double (halves away from zero; a float32 times 10 or 100 and plus 0.5 is
# exact in double), num_scaled (the text of a number in units of its last decimal) and pct_against.
def _lround(x: float) -> int:
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def _num_scaled(t: int, dec: int) -> str:
    a = abs(t)
    return f"{'-' if t < 0 else ''}{a // 10**dec}.{a % 10**dec:0{dec}d}"


def _pct_against(v32: float, rule32: float) -> tuple[float, int]:
    """gc_units.h pct_against on the file's float32 value and the file's float32 target or goal: (shown, decimals)."""
    t1 = _lround(v32 * 10.0)
    if t1 != _lround(rule32 * 10.0) or v32 == rule32:
        return t1 / 10, 1
    r2, t2 = _lround(rule32 * 100.0), _lround(v32 * 100.0)
    if t2 == r2:
        t2 = r2 - 1 if v32 < rule32 else r2 + 1
    return t2 / 100, 2


# the rules a weekly page shows a percentage beside: the consensus targets (TIR >70, CV <=36, below <4, above <25) and
# the percentage goals (whole numbers, settings.GOAL_LIMITS: 1 to 100)
_RULES = (70.0, 36.0, 4.0, 25.0, 1.0, 5.0, 20.0, 50.0, 99.0, 100.0)


def test_percentages_round_once_without_an_epsilon_as_the_backend():
    """GLU2 STATS and TRENDS hold each CV and percentage unrounded, as the float32 that rounds as the unrounded
    value (panel_feed._pct32). The panels round it once, halves away from zero, with no epsilon: the whole number
    (pct0) and the one decimal (pct1 / num) equal units.round_half_up of the unrounded value for a million random
    values and at every half."""
    text = read(UNITS_H)
    for name in ("inline void pct0(", "inline void pct1(", "inline void num(char", "inline void pct_against("):
        body = block(text, name, "\n}\n")
        assert "EPS" not in body and "1e-" not in body and "+ 0.5" not in body, name
    assert 'snprintf(buf, n, "%ld%%", lroundf(v));' in block(text, "inline void pct0(", "\n}\n")
    rng = random.Random(20261029)
    halves = [k / 20 for k in range(1, 2000, 2)] + [k / 2 for k in range(1, 200, 2)]  # x.x5 and x.5
    for v in [rng.uniform(0.0, 100.0) for _ in range(1_000_000)] + halves:
        w = pf._pct32(v)
        assert _lround(w) == units.round_half_up(v), v
        assert _lround(w * 10.0) / 10 == units.round_half_up(v, 1), v


def test_a_percentage_beside_a_target_keeps_its_side_as_the_backend():
    """The weekly pages' value beside the page's target or goal (gc_units.h pct_against, the status line): two decimals
    where one decimal would show the rule's value although the value is on the other side, the last digit moved to the
    value's side where two decimals still show it: units.shown_against, for a million values near the rules and at the
    edges, from the float32 that panel_feed._pct32 writes (it keeps the two decimals and the side of a rule)."""
    status = block(read(SQUARE), "          // Status text:", "lv_label_set_text(id(trend_status_label), status_buf);")
    assert "gcu::pct_against(a, sizeof(a), current_val, def.target);" in status and "gcu::pct1(" not in status
    for path in LANDSCAPE.values():
        assert "gcu::pct_against(a, sizeof(a), current_val, def.target);" in read(path), path.name
    body = block(read(UNITS_H), "inline void pct_against(", "\n}\n")
    assert "if (t1 != lround((double) rule * 10.0) || v == rule) {" in body
    assert "if (t2 == r2) t2 = v < rule ? r2 - 1 : r2 + 1;" in body and "num_scaled(d, sizeof(d), t2, 2);" in body
    # the cases of the rule, by hand (on float32 values that keep their digits)
    assert _pct_against(_f32(69.96), 70.0) == (69.96, 2)
    assert _pct_against(_f32(69.94), 70.0) == (69.9, 1)
    assert _pct_against(_f32(70.04), 70.0) == (70.04, 2)
    assert _pct_against(_f32(69.996), 70.0) == (69.99, 2)
    assert _pct_against(_f32(70.004), 70.0) == (70.01, 2)
    assert _pct_against(70.0, 70.0) == (70.0, 1)
    assert _pct_against(_f32(3.96), 4.0) == (3.96, 2)
    rng = random.Random(20261030)
    values = [(rule, rule + rng.uniform(-0.1, 0.1)) for rule in _RULES for _ in range(100_000)]
    values += [(rule, rule + k * 1e-6) for rule in (70.0, 36.0, 4.0, 25.0) for k in range(-60_000, 60_001, 7)]
    differ = [
        (rule, v)
        for rule, v in values
        if 0 <= v <= 100 and _pct_against(pf._pct32(v), _f32(rule)) != units.shown_against(v, rule, 1)
    ]
    assert not differ, differ[:5]


def test_the_file_keeps_the_two_decimals_and_the_side_of_a_rule():
    """For the full parity of the two-decimal rule, _pct32 also keeps the two decimals of the unrounded value and never
    writes a whole number (a rule's value) for a value that is not exactly it."""
    for rule in (70.0, 36.0, 4.0, 25.0):
        for v in (rule - 1e-6, rule + 1e-6, rule - 0.035, rule + 0.015, rule - 0.045 + 1e-7):
            assert _pct_against(pf._pct32(v), _f32(rule)) == units.shown_against(v, rule, 1), (rule, v)


@needs_node
def test_the_weekly_status_shows_a_percentage_as_on_the_display_card():
    """Card and panel parity of the two-decimal rule: the weekly pages' "Last 14 days" value beside a target or goal is
    the same on the panels (gc_units.h pct_against) and on the display card (glucocraft-shared.js shownAgainst, the
    display card's _pctShown), from the same float32 of the file, and both equal units.shown_against of the value."""
    rng = random.Random(20261031)
    cases = []
    for rule in _RULES:
        cases += [(rule, rule + rng.uniform(-0.06, 0.06)) for _ in range(1500)]
        cases += [(rule, rule + d) for d in (-0.045, -0.0049, -0.004, -1e-6, 0.0, 1e-6, 0.004, 0.0049, 0.005, 0.045)]
    cases = [(rule, v) for rule, v in cases if 0 <= v <= 100]
    file = [(_f32(rule), pf._pct32(v)) for rule, v in cases]
    shared = (REPO / "custom_components" / "glucocraft" / "www" / "glucocraft-shared.js").read_text(encoding="utf-8")
    script = (
        "globalThis.glucocraftI18n = { t: (k) => k };\n"
        + shared
        + f"\nconst cases = {json.dumps(file)};\n"
        + "process.stdout.write(JSON.stringify(cases.map(([r, v]) => globalThis.glucocraftShared.shownAgainst(v, r, 1))));"
    )
    card = json.loads(run_node(script, prelude=False).splitlines()[-1])  # after the module's version line
    for (rule, v), (rule32, v32), shown in zip(cases, file, card, strict=True):
        expected = units.shown_against(v, rule, 1)
        assert _pct_against(v32, rule32) == expected, (rule, v)
        assert (shown[0], shown[1]) == expected, (rule, v, shown)


def test_no_number_shows_a_negative_zero():
    """A value that rounds to zero is shown without a sign: "0.0", "0%", and "0" / "0.0" for a delta (num_scaled
    writes the sign only for a negative rounded value, fmt_delta "+" only for a positive one; lround of -0.0 and -0.04 x
    10 is 0), as units.round_half_up returns +0.0."""
    text = read(UNITS_H)
    scaled = block(text, "inline void num_scaled(", "\n}\n")
    assert 'snprintf(buf, n, "%s%ld.%0*ld", t < 0 ? "-" : "", a / scale, dec, a % scale);' in scaled
    assert "num_scaled(buf, n, t, dec);" in block(text, "inline void num(char", "\n}\n")
    delta = block(text, "inline void fmt_delta(", "\n}\n")
    assert "num_scaled(d, sizeof(d), t, 1);" in delta and '"%.1f"' not in delta and '"+%.1f"' not in delta
    assert 'snprintf(buf, n, t > 0 ? "+%s" : "%s", d);' in delta
    assert 'snprintf(buf, n, d > 0 ? "+%ld" : "%ld", d);' in delta
    # no printf float conversion outside the log lines: every shown number goes through num / num_scaled / lround
    for path in [*HEADERS, *LCD, TOP_LAYER, ABOUT_WIDE]:
        code = _strip_logs(read(path))
        code = "\n".join(line.split("//", 1)[0] for line in code.splitlines() if not line.lstrip().startswith("#"))
        assert not re.search(r'"[^"\n]*%[-+ 0#]*\d*(?:\.[\d*]+)?[fFeEgG][^"\n]*"', code), path.name
    for v in (-0.0, -0.04, -0.049, 0.0, 0.04):
        assert _num_scaled(_lround(_f32(v) * 10.0), 1) == "0.0", v
        assert f"{units.round_half_up(v, 1):.1f}" == "0.0", v
    assert _num_scaled(_lround(_f32(-0.05) * 10.0), 1) == "-0.1"
    assert _num_scaled(_lround(_f32(-1.25) * 10.0), 1) == "-1.3"


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_period_statistics_choose_the_decimals_not_a_format_string(path):
    stats = block(read(path), "  - id: populate_period_stats\n", "\nimage:")
    assert "auto gc_stat = [&](lv_obj_t* l, int p, float v, int decimals) {" in stats
    assert "gcu::pct0(buf, sizeof(buf), v);" in stats and "gcu::pct1_cell(buf, sizeof(buf), v);" in stats
    assert "strcmp(fmt" not in stats and "%.1f" not in stats and "%.0f" not in stats
    # TIR, TITR, Low, High of the six periods from 24 hours (the 6-hour ones: update_stats_display, the only writer)
    assert len(re.findall(r"gc_stat\(id\(\w+\), \d, \w+\(\d\), 0\);", stats)) == 24
    assert len(re.findall(r"gc_stat\(id\(stat_\w+_crit_(?:low|high)\), \d, \w+\(\d\), 1\);", stats)) == 14
    assert len(re.findall(r"gc_cv\(id\(stat_\w+_cv\), \d\);", stats)) == 6  # the CV needs a mean
    assert "gc_stat(id(stat_6h_low)" not in stats and "gc_stat(id(stat_6h_high)" not in stats


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_one_rule_for_every_periods_cells_and_the_70_percent_rule(path):
    """Every period's cells follow one rule (get_stat_shown): a period GlucoCraft shows no statistics for (no
    readings, or less than 70 % sensor active: the all-zero STATS block) shows "---" everywhere; readings at the
    sensor's limit alone show their times in the ranges and "---" for the mean, SD, CV, GMI, minimum and maximum. The
    6-hour cells have one writer each, so they never alternate."""
    text = read(path)
    stats = block(text, "  - id: populate_period_stats\n", "\nimage:")
    assert 'if (!get_stat_shown(p)) { lv_label_set_text(l, "---"); return; }' in stats
    assert "get_stat_shown(p) ? get_stat_gmi(p) : NAN" in stats
    assert (
        stats.count('if (!get_stat_shown(p) || !(get_stat_mean(p) > 0)) { lv_label_set_text(l, "---"); return; }') == 2
    )
    six = block(text, "  - id: update_stats_display\n", "\n  - id: ")
    assert "const bool shown = get_stat_shown(0), with_mean = shown && get_stat_mean(0) > 0;" in six
    assert 'else if (t == "None") dst = "---";' in six  # the 6-hour sensors without a value
    binary = read(BINARY_H)
    shown = block(binary, "bool get_stat_withheld(int p) {", "\n}\n")
    for field in ("mean", "cv", "gmi", "tir", "titr", "time_low", "time_high", "time_crit_low", "time_crit_high"):
        assert f"s.{field} == 0" in shown, field
    assert "!get_stat_withheld(p) && (backfill::stats[p].mean > 0 || get_stat_limit_lo(p) > 0" in binary


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_a_wifi_outage_reboots_only_by_the_panels_own_60_minute_check(path):
    """ESPHome's Wi-Fi default reboots after 15 minutes without a connection; the panels turn it off, so that their
    own check (12 failed 5-minute checks) is the only Wi-Fi reboot."""
    text = read(path)
    wifi = block(text, "\nwifi:\n", "\ncaptive_portal:")
    assert "\n  reboot_timeout: 0s\n" in wifi
    check = block(text, "  - interval: 5min\n", "\n  - interval:")
    assert "id(wifi_fail_count) >= 12" in check and "App.safe_reboot();" in check


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_reading_that_ends_a_no_current_data_period_is_stored(path):
    """The 6-hour ring skips a reading by its own age, not by Home Assistant's stale flag, which turns off only after
    the new reading has arrived."""
    store = block(read(path), "  - id: store_glucose_reading\n", "\n  - id: ")
    assert "if (id(data_stale)) {" not in store
    assert "(uint32_t) tn.timestamp - lr > (uint32_t) (thr * 60.0f)) return;" in store
    assert "if (!own_time && id(data_stale)) return;" in store


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_6_hour_fallback_fills_every_cell(path):
    """Before the first file, Very low, Very high, the minimum and the maximum come from the 6-hour summary too."""
    text = read(path)
    for ranges, names in (
        ("consensus", {"very_low": "time_below_54", "very_high": "time_above_250"}),
        ("custom", {"very_low": "time_very_low", "very_high": "time_very_high"}),
    ):
        for name, attr in {**names, "min": "min_value", "max": "max_value"}.items():
            assert re.search(
                rf"id: stats_6h_{ranges}_{name}\n\s+entity_id: sensor\.glucocraft_6h_{ranges}_ranges\n"
                rf"\s+attribute: {attr}\n",
                text,
            ), (ranges, name)
    upd = block(text, "  - id: update_stats_display\n", "\n  - id: ")
    for label in ("stat_6h_crit_low", "stat_6h_crit_high", "stat_6h_min", "stat_6h_max"):
        assert f"lv_label_set_text(id({label}), buf);" in upd, label
    assert "gcu::fmt_attr(buf, sizeof(buf), mn);" in upd and "_min_limit" not in text


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_no_dead_state_is_kept(path):
    text = read(path)
    for name in (
        "sensor_expiry_timestamp",
        "sensor_expiry_year",
        "sensor_expiry_month",
        "sensor_warmup_start_time",
        "reading_counter",
        "loading_readings_received",
        "id: api_connected",
        "glucose_6h_min",
        "glucose_24h_max",
        "glucose_180d_min",
        "bool own_stale",
        "UTC expiry",
        "PSRAM array wiped",
    ):
        assert name not in text, name
    assert text.count('ESP_LOGD("sensor", "CGM expiry') == 1  # a date and time: DEBUG only
    assert "if (id(sensor_expiry_epoch) > 0) {" in text
    binary = read(BINARY_H)
    for name in ("backfill_status", "binary_capacity", "backfill::status", "valid_idx", "(void)oldest_ts"):
        assert name not in binary, name
    period = block(binary, "int backfill_load_period(", "{")
    assert "min_val" not in period and "max_val" not in period
    assert "int backfill_load(int* val_array, uint32_t* ts_array, int max_count) {" in read(BINARY_H)


def test_header_comments_describe_the_current_code():
    binary = read(BINARY_H)
    assert "AppDaemon" not in binary and "within 960 KB (BACKFILL_BUDGET_BYTES)" in binary
    assert "(0 = gap marker)" not in binary and "Time in range 70-180" not in binary
    assert binary.index("static void gc_utf8_trim(") < binary.index("// The GCM1 block") < binary.index("GCM1_SIZE")
    compat = read(COMPAT_H)
    head = compat.split("#pragma once", 1)[0]
    assert "compatibility shim" not in head and "LVGL 8" not in head
    assert "lvc_draw_line (one line from points[0] to points[1])" in compat
    for board, path in LANDSCAPE.items():
        head = read(path).split("substitutions:", 1)[0]
        assert "# Substitutions: ha_host / ha_port" in head, board
        assert ("engineering_sample (the chip revision)" in head) is (board == "waveshare-p4-7b"), board


# ------------------------------------------------------------------------------------------------ the generator
@pytest.mark.parametrize("board", sorted(LANDSCAPE))
def test_landscape_files_are_the_generator_output(board, tmp_path):
    out = tmp_path / f"{board}.yaml"
    subprocess.run([sys.executable, str(GENERATOR), board, "--out", str(out)], check=True, capture_output=True)
    assert out.read_bytes() == LANDSCAPE[board].read_bytes(), f"{LANDSCAPE[board].name} differs from the generator"


# ------------------------------------------------------------------------------------------------ the texts
# Every text a panel shows is in the text catalogue i18n/<language>.yaml; tools/make_panel_texts.py generates from it
# the static label texts (substitutions t_*), the code's texts (gc_text.h, gct::*) and the fonts' glyph lists.
ROBOTO = PANELS / ".esphome" / "font" / "Roboto@400@False@v1.ttf"  # ESPHome's copy, after a compile
needs_roboto = pytest.mark.skipif(not ROBOTO.exists(), reason="Roboto is cached by ESPHome only after a compile")

# The string literals the code may hold, as they are not shown (or not words). Everything else with two letters or
# more must come from the catalogue.
ALLOWED_LITERALS = {
    # values Home Assistant or GlucoCraft send, compared in the code (states, attributes, settings); never shown
    *("unavailable", "unknown", "Unknown", "None", "True", "False", "true", "false", "on", "off"),
    *("arrow", "word"),  # the setting Trend shown as (trend_display)
    # a number format (gc_units.h num: the sign, the whole part, the decimals)
    "%s%ld.%0*ld",
    *("current", "expired", "no_current_data", "no_data", "warm_up", "expiring_soon"),
    *("low", "high", "very_low", "near_low", "tight_range", "near_high", "very_high", "LO", "HI"),
    *("DMY", "YMD", "both", "mmol_mol", "mmol"),
    # unit symbols (not words; the same in every language)
    *("mg/dL", "mmol/L", "%ld mmol/mol", "%s%% (%ld mmol/mol)"),
    # the panel files' block tags, and the C types of two globals
    *("GCL2", "GCM1", "GCX1", "GCR1", "int[180]", "uint32_t[180]"),
    # never shown: the download status kept for debugging, the PSRAM buffers' and the tasks' names, a compiler message
    *("Not started", "File too small", "Invalid file format", "Downloading...", "Downloaded", "Loaded"),
    *("File truncated (stats)", "File truncated (ranges)", "File truncated (readings)"),
    *("staging buffer", "delta buffer", "delta stats buffer", "bf_download", "bf_dstats"),
    "GCX1 standard block is 656 bytes",
    # ESPHome's font source
    "gfonts://Roboto",
    # a diagnostic state for Home Assistant only (the P4 panel's co-processor firmware sensor); never shown on a panel
    "Not reported",
}


def _strip_logs(text: str) -> str:
    """Return the text without its log calls (ESP_LOG*(...), over several lines too) and logger.log lines."""
    out, i = [], 0
    for m in re.finditer(r"ESP_LOG\w*\(", text):
        if m.start() < i:
            continue
        out.append(text[i : m.start()])
        j, depth, quoted = m.end(), 1, False
        while depth:
            c = text[j]
            if c == "\\" and quoted:
                j += 1
            elif c == '"':
                quoted = not quoted
            elif not quoted:
                depth += {"(": 1, ")": -1}.get(c, 0)
            j += 1
        i = j
    out.append(text[i:])
    return re.sub(r"(?m)^.*logger\.log:.*$", "", "".join(out))


def _code_literals(path: Path) -> list[str]:
    """Return the string literals of a file's code: the lambdas of a configuration (not its other values, the
    comments or the log calls) or the whole of a header (without comments, #include / #error and logs)."""
    text = _strip_logs(read(path))
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    found = []
    for line in text.splitlines():
        if path.suffix == ".yaml" and (
            line.lstrip().startswith("#")
            or (re.match(r"^\s*(?:- )?[a-z_]+: ", line) and not re.search(r"\b(lambda|initial_value)\b", line))
        ):
            continue  # a configuration value (a name, an id, a substitution), not code
        if re.match(r"^\s*#\s*(include|error|define|pragma)\b", line):
            continue
        code = re.sub(r'("(?:[^"\\\n]|\\.)*")|//.*', lambda m: m.group(1) or "", line)  # without a // comment
        found += re.findall(r'"((?:[^"\\\n]|\\.)*)"', code)
    return found


def _placeholder_names() -> set[str]:
    return {name for t in catalogue().values() for name in re.findall(r"\{(\w+)\}", t)}


@pytest.mark.parametrize("path", [*LCD, TOP_LAYER, ABOUT_WIDE], ids=lambda p: p.stem)
def test_no_text_is_typed_in_a_label(path):
    """Every static label text is a catalogue text (${t_*}); a label without text holds "" or "---" (no value)."""
    for value in re.findall(r"(?m)^\s+text: (?!!lambda)(.*)$", read(path)):
        value = value.split("   #")[0].strip()
        assert value in ('""', '"---"') or re.fullmatch(r'"\$\{t_\w+\}"', value), value


@pytest.mark.parametrize("path", [*LCD, *HEADERS], ids=lambda p: p.name)
def test_no_text_is_typed_in_the_code(path):
    """Every text the code shows comes from gc_text.h (gct::*). Allowed in the code: what has fewer than two letters
    (numbers, formats, "---"), the substitutions, the placeholder names given to gcu::fill(), and ALLOWED_LITERALS."""
    names = _placeholder_names()
    for literal in _code_literals(path):
        words = re.sub(r"%[-+0 #]*\d*(?:\.\d+)?(?:l|ll|h)?[sdufx%]|\$\{\w+\}", "", literal)
        if not re.search(r"[A-Za-z]{2,}", words) or literal in names or literal in ALLOWED_LITERALS:
            continue
        pytest.fail(f"{path.name}: a text in the code, not in the catalogue i18n/en.yaml: {literal!r}")


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_language_is_a_build_choice(path):
    """The substitution language selects the catalogue: its generated package (the label texts, the glyph lists)
    and its gc_text.h (the code's texts)."""
    text = read(path)
    assert re.search(r"(?m)^  language: en$", block(text, "substitutions:", "\nesphome:"))
    assert "packages:\n  texts: !include i18n/generated/${language}/texts.yaml\n" in text
    assert "    - i18n/generated/${language}/gc_text.h" in block(text, "  includes:\n", "\n  on_boot:")
    assert '#include "gc_text.h"' in read(UNITS_H)


@pytest.mark.parametrize("path", [*LCD, *HEADERS], ids=lambda p: p.name)
def test_every_text_used_is_generated(path):
    text, subs = read(path), substitutions()
    header = (GENERATED / "gc_text.h").read_text(encoding="utf-8")
    defined = set(re.findall(r"inline constexpr const char \*(\w+)", header))
    for name in re.findall(r"\$\{(t_\w+|glyphs_\w+)\}", text):
        assert name in subs, name
    for name in re.findall(r"\bgct::(\w+)", text):
        assert name in defined, name


@pytest.mark.parametrize("path", [*LCD, UNITS_H], ids=lambda p: p.name)
def test_each_filled_text_gets_exactly_its_placeholders(path):
    """gcu::fill() gives every text exactly the values its English text names (a translation keeps them)."""
    texts = {k.removeprefix("panel.").replace(".", "_"): v for k, v in catalogue().items()}
    calls = re.findall(r"\bfill\((.*?)\}\);", read(path))
    assert calls
    for call in calls:
        given = set(re.findall(r'\{"(\w+)", ', call))
        for name in re.findall(r"gct::(\w+)", call.split("{", 1)[0]):
            assert set(re.findall(r"\{(\w+)\}", texts[name])) == given, (name, given)


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_fonts_take_their_glyphs_from_the_catalogue(path):
    """Every font's glyph list is a generated one: the catalogue's characters plus the layout's digits and symbols."""
    fonts = block(read(path), "\nfont:\n", "\n\n")
    glyphs = re.findall(r"glyphs: (.*?)(?: \}|$)", fonts, re.MULTILINE)
    assert glyphs and all(re.fullmatch(r'"\$\{glyphs_\w+\}"', g) for g in glyphs), glyphs
    assert fonts.count("file: ") == len(glyphs)  # no font without a list (ESPHome's default set has no accents)


def test_english_glyph_lists_keep_the_fonts():
    """The English lists hold every character the panels showed before: the text fonts' accented Latin letters and
    symbols, and every letter of the catalogue in the larger fonts."""
    subs, used = substitutions(), "".join(re.sub(r"\{\w+\}", "", t).replace("\n", "") for t in catalogue().values())
    assert set("ÀÁÂÄÇÈÉÊÑÖØßàáâäçèéêñöøüŁłŒœŠšŽž0123456789%/:+.,°_") <= set(subs["glyphs_text"])
    for cls in ("glyphs_heading", "glyphs_text"):
        assert set(used) <= set(subs[cls]), cls
    assert {k for k in subs if k.startswith("glyphs_")} == {
        "glyphs_text",
        "glyphs_heading",
        "glyphs_value",
        "glyphs_delta",
        "glyphs_clock",
    }
    assert set(subs["glyphs_value"]) == set("0123456789-+.,HILO") and set(subs["glyphs_clock"]) == set(
        "0123456789:-+ APM"
    )


@needs_roboto
def test_the_generated_texts_are_up_to_date():
    """i18n/generated/<language>/ is what make_panel_texts.py makes from every catalogue in i18n/."""
    tool = PANELS / "tools" / "make_panel_texts.py"
    run = subprocess.run(  # -X utf8: the tool writes UTF-8 whatever the console's code page
        [sys.executable, "-X", "utf8", str(tool), "--check"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert run.returncode == 0, run.stdout + run.stderr


def _panel_texts_tool():
    """tools/make_panel_texts.py of the panel folder, as a module (its texts need no font)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("make_panel_texts", PANELS / "tools" / "make_panel_texts.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def test_the_generated_panel_texts_hold_the_disclaimer_and_its_line_as_const():
    """The generated English texts (texts.yaml and gc_text.h) hold the disclaimer and its one line word for word as
    const.py DISCLAIMER_SHORT_PLAIN and DISCLAIMER_LINE: the About title with the short form, and the start-up line (its two
    sentences on a line each). No font is needed; no part of the disclaimer is split around the icon any more."""
    from glucocraft import const as C

    subs = substitutions()
    assert f"{subs['t_about_title']} {subs['t_about_disclaimer']}" == C.DISCLAIMER_SHORT_PLAIN
    assert subs["t_startup_line"].replace("\n", " ") == C.DISCLAIMER_LINE
    assert not [k for k in subs if k.startswith("t_about_disclaimer_")]
    header = (GENERATED / "gc_text.h").read_text(encoding="utf-8")
    tool = _panel_texts_tool()
    assert f"inline constexpr const char *about_disclaimer = {tool.c_string(subs['t_about_disclaimer'])};" in header
    assert f"inline constexpr const char *startup_line = {tool.c_string(subs['t_startup_line'])};" in header


def test_the_generated_panel_texts_are_current_without_the_fonts():
    """make_panel_texts.py's English output is the generated files, gc_text.h and texts.yaml, whole (no font needed)."""
    tool = _panel_texts_tool()
    made = tool.outputs("en")
    for path, content in made.items():
        assert path.read_text(encoding="utf-8") == content, path.name


def test_a_translation_translates_every_text(tmp_path, monkeypatch):
    """make_panel_texts.py takes every key of a translation from the translation, the disclaimer included, and a
    missing key from English."""
    import importlib.util

    if not (PANELS / "tools" / "make_panel_texts.py").is_file():
        pytest.skip(NO_PANELS)
    spec = importlib.util.spec_from_file_location("make_panel_texts", PANELS / "tools" / "make_panel_texts.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    source = yaml.safe_load((PANELS / "i18n" / "en.yaml").read_text(encoding="utf-8"))
    assert not hasattr(tool, "TERMS_KEYS")
    (tmp_path / "en.yaml").write_text((PANELS / "i18n" / "en.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    translated = {"panel.about.disclaimer": "Xx", "panel.startup.line": "Xx", "panel.no_reading.warming_up": "Xx"}
    (tmp_path / "xx.yaml").write_text(yaml.safe_dump(translated, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(tool, "CATALOGUES", tmp_path)
    texts = tool.load("xx")
    assert (
        texts["panel.no_reading.warming_up"] == texts["panel.about.disclaimer"] == texts["panel.startup.line"] == "Xx"
    )
    assert texts["panel.about.title"] == source["panel.about.title"]  # a missing key: English


def test_the_description_lines_are_read_in_utf8():
    """The weekly pages' description lines from the file are UTF-8 (panel_feed.py cuts them at a whole letter): the
    reader ends each at its 64th byte and removes a letter cut by any other sender (gc_utf8_trim, tested with g++)."""
    code = read(BINARY_H)
    assert "char desc[9][64];        // the line under each weekly page's title (UTF-8, NUL-terminated)" in code
    assert re.search(r"backfill::standard\.desc\[k\]\[63\] = 0;\s+gc_utf8_trim\(backfill::standard\.desc\[k\]\);", code)
    assert "static void gc_utf8_trim(char* s) {" in code


@needs_roboto
def test_every_english_text_fits_on_every_layout():
    tool = PANELS / "tools" / "worst_case_fit.py"
    run = subprocess.run(  # -X utf8: the report holds the texts' accents, whatever the console's code page
        [sys.executable, "-X", "utf8", str(tool), "en"], check=False, capture_output=True, text=True, encoding="utf-8"
    )
    assert run.returncode == 0 and run.stdout.rstrip().endswith("all fit"), run.stdout[-2000:] + run.stderr


def test_the_catalogue_follows_the_format():
    """Flat, dotted, lower-case panel.* keys, each given once; placeholders are {name}; no ESPHome "$"."""
    raw = CATALOGUE.read_text(encoding="utf-8")
    keys = re.findall(r"(?m)^([^#\s][^:]*):", raw)
    assert len(keys) == len(set(keys)) == len(catalogue())
    for key, text in catalogue().items():
        assert re.fullmatch(r"panel(\.[a-z0-9_]+)+", key), key
        rest = re.sub(r"\{\w+\}", "", text)
        assert "$" not in text and "{" not in rest and "}" not in rest, key


def test_every_catalogue_text_is_used():
    """No orphan text: each key is shown by a panel (${t_*}, gct::*, or through the generator's arrays), or is one of
    GlucoCraft's zone and trend words (panel.word.*: measured by worst_case_fit.py, their letters in the fonts)."""
    code = "".join(read(p) for p in [*LCD, *HEADERS])
    tools = (PANELS / "tools" / "make_panel_texts.py").read_text(encoding="utf-8")
    for key in catalogue():
        name = key.removeprefix("panel.").replace(".", "_")
        if key.startswith("panel.word."):
            continue
        arrays = re.search(r"panel\.date\.(weekday|weekday_short|month|month_short)\.", key)
        assert (
            f"${{t_{name}}}" in code
            or f"gct::{name}" in code
            or f'"{key}"' in tools
            or (arrays and f"gct::{arrays.group(1)}[" in code)
        ), key


# ------------------------------------------------------------------------------------------------ card and panel rules
CARD_JS = REPO / "custom_components" / "glucocraft" / "www" / "glucocraft-display-card.js"


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_every_label_cut_with_dots_has_a_one_line_height(path):
    """LVGL cuts a label with "..." only where its text is taller than the label: a label with long_mode DOT and no
    height (LV_SIZE_CONTENT) wraps instead. Every such label has a height."""
    lines = read(path).splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "long_mode: DOT":
            start = max(k for k in range(i) if lines[k].strip().startswith("- label:"))
            assert any(lines[k].strip().startswith("height:") for k in range(start, i)), f"{path.name}:{i + 1}"


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_panels_download_the_files_at_once_on_the_cards_settings(path):
    """The settings whose change makes the display card download the files at once (its refetch key) make every panel
    download them at once too (backfill_settings_changed): unit, A1C unit, ranges, goals, goals_custom, weekly pages."""
    card = read(CARD_JS)
    key = re.search(r"const setKey = JSON\.stringify\(\[([^\]]*)\]\)", card).group(1)
    assert [k.strip() for k in key.split(",")] == [
        "ca.unit",
        "ca.a1c_unit",
        "ca.ranges",
        "ca.goals",
        "ca.goals_custom",
        "ca.weekly_pages?.shown",
    ]
    text = read(path)
    for attribute in ("a1c_unit", "ranges", "goals", "goals_custom", "weekly_pages"):
        handler = block(text, f"    attribute: {attribute}\n", "  - platform:")
        assert "id(backfill_settings_changed).execute()" in handler, f"{path.name}: {attribute}"
    unit = block(text, "    id: ha_unit\n", "  - platform:")
    assert "id(backfill_settings_changed).execute()" in unit


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_swipe_that_ends_the_start_up_screen_changes_no_page(path):
    """end_startup clears startup_active at once, so the page change sits in the else branch of the start-up test."""
    text = read(path)
    for name in ("nav_next", "nav_prev"):
        script = block(text, f"  - id: {name}\n", "\n\n")
        start_up = script.index("lambda: 'return id(startup_active);'")
        ends, other = script.index("script.execute: end_startup", start_up), script.index("          else:", start_up)
        assert ends < other < script.index("lvgl.page.", other), f"{path.name}: {name}"
        assert "!id(startup_active)" not in script


def test_the_watchdog_options_are_the_same_on_every_panel():
    """The 30 s task watchdog with the idle tasks of both cores, on all five panels."""
    options = [
        "CONFIG_ESP_TASK_WDT: y",
        'CONFIG_ESP_TASK_WDT_TIMEOUT_S: "30"',
        "CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU0: y",
        "CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU1: y",
    ]
    for path in LCD:
        found = [line.strip() for line in read(path).splitlines() if "CONFIG_ESP_TASK_WDT" in line]
        assert found == options, path.name


def test_no_signed_32_bit_time_arithmetic():
    """long is 32 bits on these chips: an epoch in seconds computed in it passes its range in 2038."""
    for path in [*LCD, *HEADERS]:
        code = read(path)
        assert "86400L" not in code and "(long) time.timestamp" not in code and "(long) now" not in code, path.name


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_any_touch_restarts_the_idle_time(path):
    """The return to the first page counts 3 minutes from the last touch (as the display card from its last use), not
    from the last page change."""
    touch = block(read(path), "touchscreen:\n", "\n\n")
    assert "on_touch:\n      - lambda: 'id(page_idle_seconds) = 0;'" in touch


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_weekly_min_max_page_with_an_empty_newest_week_reads_as_on_the_card(path):
    """The Min and Max page is tested before the empty-week branch: "Min: ---  Max: ---" with the direction, as the
    display card shows it, never "Last 14 days: ---"."""
    status = block(read(path), "// Status text:", "lv_label_set_text(id(trend_status_label)")
    assert status.index("} else if (pg == 8) {") < status.index("} else if (isnan(current_val)")


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_value_is_never_gray(path):
    """The value keeps its zone color also after the stated expiry while readings arrive (the red status bar carries
    the expiry), so it always takes the Outline text edge; only the square panel's corner "---" without a current
    reading is gray (LV_OBJ_FLAG_USER_1: no edge, lvgl_compat.h), as on the display card."""
    text = read(path)
    upd = block(text, "  - id: update_glucose_display\n", "\n  - id: ")
    assert "const bool gray" not in upd and "LV_OBJ_FLAG_USER_1" not in upd
    assert "text_color: !lambda return lv_color_hex(id(global_glucose_color));" in upd
    assert "if (lv_obj_has_flag(label, LV_OBJ_FLAG_USER_1)) return;" in read(COMPAT_H)
    assert ("lv_obj_add_flag(id(weekly_mini_value), LV_OBJ_FLAG_USER_1)" in text) is (path == SQUARE)


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_without_a_recent_readings_file_the_full_files_6_h_and_24_h_numbers_show(path):
    """A failed recent-readings download (none during an outage of more than 2 hours) returns the 6 h and 24 h
    statistics to the full file's, as the display card does (it drops its old delta), never numbers frozen from
    before the gap."""
    interval = block(read(path), "lambda: 'return delta_stats_apply();'", "  # Once a minute")
    assert "lambda: 'return delta_stats_fallback();'" in interval
    fallback = block(read(BINARY_H), "bool delta_stats_fallback() {", "\n}\n")
    assert "backfill::ds_state != backfill::DL_FAILED" in fallback and "backfill::binary_buf" in fallback
    card = read(CARD_JS)
    assert 'if (which === "delta") this._delta = null;' in card


# ------------------------------------------------------------------------------------------------ the graphs' look
@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_period_graphs_have_the_display_cards_minimum_to_maximum_look(path):
    """Each time slot's lowest to highest reading as light-gray areas (#BDBDBD) with a 1-pixel black edge, the mean
    line in white over a 4-pixel black underlay, the key's minimum-to-maximum label in the same light gray: the display
    card's look (glucocraft-shared.js gcRangeAreas, the card's mean line)."""
    draw = block(read(path), "auto draw_data = [&]", "\n                };\n")
    assert "lv_color_hex(0xBDBDBD)" in draw and "lv_color_hex(0x000000)" in draw and "0x888888" not in draw
    assert "lvc_vline(cv, i, top[i], bot[i], area_color);" in draw
    assert "lvc_px(cv, i, top[i] - 1, black);" in draw and "lvc_px(cv, i, bot[i] + 1, black);" in draw
    assert "for (int k = -1; k <= 2; k++) lvc_vline(cv, x0 + k, y0 - 1, y0 + 2, black);" in draw
    assert draw.index("mean_line(true);") < draw.index("mean_line(false);")
    legend = block(read(path), "auto draw_legend = [&]", "};")
    assert "ld.color = lv_color_hex(0xBDBDBD);" in legend
    card, shared = read(CARD_JS), read(CARD_JS.with_name("glucocraft-shared.js"))
    assert 'fill="#bdbdbd" stroke="#000000"' in shared and 'stroke-width="1"' in shared
    assert 'stroke="#000000" stroke-width="4"' in card and "0xBDBDBD" in card


def test_the_square_panels_weekly_corner_row_shows_the_zone_word():
    """The square panel's corner value on the weekly pages shows the zone word (the glucose block's display_word,
    empty while the value is not current) in the text color (also after the stated expiry), hidden when empty."""
    text = read(SQUARE)
    row = block(
        text, "                id: weekly_mini\n", "            - label:\n                id: trend_title_label"
    )
    assert row.index("id: weekly_mini_value") < row.index("id: weekly_mini_word") < row.index("id: weekly_mini_arrow")
    assert "id: weekly_mini_word\n                      text_font: roboto_18\n" in row
    script = block(text, "  - id: update_weekly_mini\n", "  # <<< weekly mini value (script)")
    assert "const std::string &w = id(global_display_word);" in script
    assert "const int wcol = gcu::theme_color(gcu::TH_TEXT);" in script
    assert "if (w.empty()) lv_obj_add_flag(id(weekly_mini_word), LV_OBJ_FLAG_HIDDEN);" in script
    for path in LANDSCAPE.values():  # the landscape panels keep the glucose block: no corner row
        assert "weekly_mini" not in read(path), path.name


def test_the_panels_read_glu2_version_3_and_later():
    """Every reading of a GLU2 header checks the magic and version 3 or later (gc_glu2_readable), as the display card
    (parseGlu2): a later version is read by the parts the panel knows."""
    text = read(BINARY_H)
    assert "#define GLU2_MIN_VERSION      3" in text
    assert "return h.magic == BINARY_MAGIC_GLU2 && h.version >= GLU2_MIN_VERSION;" in text
    assert text.count("gc_glu2_readable(") == 5  # the definition and the four headers read
    assert "magic != BINARY_MAGIC_GLU2) return" not in text and "dh.magic == BINARY_MAGIC_GLU2" not in text
    assert "header.version >= 3" not in text
    assert "d.getUint16(4, true) < 3" in read(CARD_JS.with_name("glucocraft-shared.js"))


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_every_panel_type_names_its_board_and_resolution(path):
    """The project name (Home Assistant's model of the panel) is glucocraft.<board>-<width>x<height>."""
    name = re.search(r"\n  project:\n    name: (\S+)", read(path)).group(1)
    assert re.fullmatch(r"glucocraft\.[a-z0-9-]+-\d{3,4}x\d{3,4}", name), name
    if path == SQUARE:
        assert name == "glucocraft.guition4848s040-480x480"


def _weekly_axis_labels(lo: float, hi: float) -> list[str]:
    """The weekly page's labels as the panel draws them (draw_trend_page): the first step of the list at or above a
    quarter of the span, every multiple of it in [lo, hi], each with the step's decimals (gc_units.h step_decimals,
    weekly_axis_label: halves away from zero, as gcu::num)."""
    steps = (0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100)
    step = next((s for s in steps if s >= (hi - lo) / 4), 100)
    dec = 0 if abs(step - round(step)) < 1e-3 else 1 if abs(step * 10 - round(step * 10)) < 1e-3 else 2
    out, k = [], math.ceil(lo / step - 1e-4)
    while k * step <= hi:
        gd = k * step
        scaled = math.floor(abs(gd) * 10**dec + 0.5)
        out.append(str(scaled) if dec == 0 else f"{scaled // 10**dec}.{scaled % 10**dec:0{dec}d}")
        k += 1
    return out


@pytest.mark.parametrize(
    ("lo", "hi"), [(5.6, 6.4), (5.0, 7.0), (4.0, 10.0), (3.0, 15.0), (6.1, 6.9), (0.0, 25.0), (40.0, 52.0)]
)
def test_the_weekly_axis_has_one_label_format(lo, hi):
    """The weekly pages' axis in one format per axis, as every graph axis of GlucoCraft (in mmol/L with steps of
    0.25, 0.5, 1 and 2, and in % or mmol/mol): every label has the step's decimals ("6.5 / 7.0 / 7.5"), never a
    mixed "6.5 / 7 / 7.5"."""
    labels = _weekly_axis_labels(lo, hi)
    assert labels
    assert len({len(x.split(".")[1]) if "." in x else 0 for x in labels}) == 1, labels
    trend = block(read(SQUARE), "  - id: draw_trend_page\n", "\n  - id: ")
    assert "gcu::weekly_axis_label(y_buf, sizeof(y_buf), gd, step);" in trend
    assert "roundf(gd * 10)" not in trend  # never decided label by label
    h = UNITS_H.read_text(encoding="utf-8")
    assert "inline int step_decimals(float step) {" in h and "else num(buf, n, gd, dec);" in h


def test_a_mmol_axis_has_one_label_format():
    """The panel gives every label of an axis with a half one decimal, and whole numbers otherwise (one format per
    axis, as every graph of GlucoCraft)."""
    h = UNITS_H.read_text(encoding="utf-8")
    body = h[h.index("inline void axis_label(char *buf, size_t n, float g_mg, float step_mg)") :]
    body = body[: body.index("\n}")]
    assert "s = step_mg / MGDL_PER_MMOLL" in body and "fabsf(s - roundf(s)) < 0.05f" in body
    assert "fabsf(v - roundf(v))" not in body  # never decided label by label


# ------------------------------------------------------------------------------------------------ limit readings
def _tail(runs: list[tuple[int, int, int]] | None) -> bytes:
    """A file's end as GlucoCraft writes it: the GCR1 block (None: none, an old file), then GCM1 and GCL2."""
    out = b""
    if runs is not None:
        out += struct.pack("<H", len(runs)) + b"".join(struct.pack("<IIB", *r) for r in runs) + b"GCR1"
    out += struct.pack("<14f", *([60.0] * 14)) + b"GCM1"
    out += struct.pack("<14I", *range(14)) + b"GCL2"
    return out


def _find_limit_runs(data: bytes) -> list[tuple[int, int, int]] | None:
    """glucocraft_panel_binary.h gc_find_limit_runs, transcribed: the block's count is at its start, so it is found
    from the end, the first count c whose 2 bytes stand 9 x c bytes before the name and hold c, every stretch valid."""
    gcl2, gcm1 = 14 * 4 + 4, 14 * 4 + 4
    if len(data) < 40 + 6 + gcm1 + gcl2 or data[-4:] != b"GCL2" or data[-gcl2 - 4 : -gcl2] != b"GCM1":
        return None
    name = len(data) - gcl2 - gcm1 - 4
    if data[name : name + 4] != b"GCR1":
        return None
    room = name - 40
    c = 0
    while c <= 0xFFFF and c * 9 + 2 <= room:
        p = name - c * 9 - 2
        if struct.unpack_from("<H", data, p)[0] == c:
            runs = [struct.unpack_from("<IIB", data, p + 2 + i * 9) for i in range(c)]
            if all(k in (1, 2) and s <= e and s > 1600000000 for s, e, k in runs):
                return runs
        c += 1
    return None


def test_the_limit_stretches_are_read_from_the_end_and_an_old_file_still_loads():
    """The GCR1 block (the stretches of limit readings) stands directly before GCM1; its count is found from the end,
    for any count and any bytes before it; a file without the block (an older GlucoCraft) has no stretches and its
    GCM1 and GCL2 blocks are still found by their names from the end."""
    body = read(BINARY_H)
    fn = block(body, "static bool gc_find_limit_runs(", "\n}\n")
    for part in (
        'if (memcmp(name, "GCR1", 4) != 0) return false;',
        "const uint8_t* name = gcm_end - GCM1_SIZE - 4;",
        "for (size_t c = 0; c <= 0xFFFF && c * GCR1_RUN_SIZE + 2 <= room; c++) {",
        "ok = (r.kind == 1 || r.kind == 2) && r.start <= r.end && r.start > 1600000000u;",
    ):
        assert part in fn, part
    assert "gc_find_limit_runs(data, size, &backfill::runs_off, &backfill::runs_n);" in body
    # the GCM1 and GCL2 readers find their blocks from the end, whatever stands before them
    assert 'if (memcmp(end - 4, "GCM1", 4) != 0) return false;' in body
    rng = random.Random(233)
    for count in (0, 1, 2, 5, 31, 300):
        runs = []
        t = 1_760_000_000
        for _ in range(count):
            s = t + rng.randint(60, 86400)
            e = s + rng.randint(0, 7200)
            runs.append((s, e, rng.choice((1, 2))))
            t = e
        head = bytes(40) + bytes(rng.randrange(256) for _ in range(rng.randint(0, 600)))
        assert _find_limit_runs(head + _tail(runs)) == runs, count
        assert _find_limit_runs(head + _tail(None)) is None  # an old file: no stretches, nothing else changes


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_limit_stretches_are_marked_at_the_graphs_edge(path):
    """A stretch of limit readings has no value: the line breaks across it and it is marked at the bottom (LO) or top
    (HI) edge in the very-low / very-high range color, with no text, on the 6-hour graph (the
    files' stretches and the live one) and on the six period graphs (the full file's); after the line, before the
    labels."""
    text = read(path)
    six = block(text, "  - id: draw_glucose_graph\n", "\n  - id: ")
    assert "n_runs = gc_limit_runs(window_start, now_ts, runs, 48, true," in six
    assert "live.start = since > 1600000000.0 ? (uint32_t) since : own;" in six
    assert "if (gc_crosses_run(runs, n_runs, points[i].ts, points[i + 1].ts)) continue;" in six
    assert six.count("draw_marks();") == 2  # with and without a line
    assert six.index("draw_marks();   // the limit stretches' marks at the edge") < six.rindex("lvc_texts(canvas_obj);")
    period = block(text, "  - id: draw_period_graphs\n", "\n  - id: ")
    assert "n_runs = now_ts > 0 ? gc_limit_runs(win_start, now_ts, runs, 96, false, nullptr) : 0;" in period
    assert "if (gc_crosses_run(runs, n_runs, slot_t(i), slot_t(j + 1) - 1)) continue;" in period
    assert period.count("marks(cv);   // the limit stretches' marks at the edge") == 6
    marks = block(read(BINARY_H), "static void gc_draw_limit_marks(", "\n}\n")
    # in the very-low / very-high range color (the value's colors at LO / HI), as on the display card
    assert "gcu::zone_color(gcu::Z_VERY_LOW)" in marks and "gcu::zone_color(gcu::Z_VERY_HIGH)" in marks
    assert "band_color" not in marks
    assert "gct::" not in marks and "lvc_draw_text" not in marks  # no letters


def test_the_limit_marks_have_the_display_cards_sizes():
    """The panels' marks have the display card's sizes (glucocraft-shared.js LIMIT_MARK): an 8 px black strip at the
    edge, marks 2 x 6 px every 4 px, stretches of a kind closer than 3 px joined."""
    h = read(BINARY_H)
    shared = (REPO / "custom_components" / "glucocraft" / "www" / "glucocraft-shared.js").read_text(encoding="utf-8")
    js = re.search(r"const LIMIT_MARK = Object\.freeze\(\{([^}]*)\}\);", shared).group(1)
    card = {k: int(v) for k, v in re.findall(r"(\w+): (\d+)", js)}
    panel = {k: int(v) for k, v in re.findall(r"GC_MARK_(\w+) = (\d+)", h)}
    pairs = {
        "STRIP": "strip",
        "H": "mark",
        "W": "width",
        "STEP": "step",
        "MERGE": "merge",
    }
    assert {k: panel[k] for k in pairs} == {k: card[v] for k, v in pairs.items()}
    assert set(panel) == set(pairs) and set(card) == set(pairs.values())


LIMIT_SCREEN_LAYOUT = {  # the limit screen's width and fonts per panel: the zone, the sensor range, the two time lines
    "glucocraft-panel": ("248", "roboto_40", "roboto_20", "roboto_16"),
    "glucocraft-waveshare-lcd7": ("344", "roboto_40", "roboto_26", "roboto_18"),
    "glucocraft-waveshare-lcd5": ("440", "roboto_40", "roboto_26", "roboto_20"),
    "glucocraft-waveshare-p4-7b": ("440", "roboto_40", "roboto_26", "roboto_20"),
    "glucocraft-guition-35": ("186", "roboto_32", "roboto_14", "roboto_14"),
}


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_a_limit_reading_shows_the_limit_screen(path):
    """A current limit reading: in the value's place the four approved lines (the zone large in its zone color, the
    sensor range, since when, the last measured reading), each wrapped at the zone word's width, from GlucoCraft's
    limit_since, last_measured_mgdl and last_measured_ts; after the no-current-data limit the words without a current
    reading take the place ("Last reading: LO")."""
    text = read(path)
    width, title, line, time = LIMIT_SCREEN_LAYOUT[path.stem]
    for lid, font, hidden in (
        ("limit_title_label", title, False),
        ("limit_range_label", line, True),
        ("limit_since_label", time, True),
        ("limit_measured_label", time, True),
    ):
        label = re.search(r"- label:\n\s+id: " + lid + r"\n(?:\s+\w+: .*\n)+", text).group(0)
        assert f"text_font: {font}\n" in label and f"width: {width}\n" in label and "long_mode: WRAP" in label, lid
        assert ("hidden: true" in label) == hidden, lid
    for attr in ("limit_since", "last_measured_mgdl", "last_measured_ts"):
        assert re.search(r"entity_id: sensor\.glucocraft_last_glucose_reading\n\s+attribute: " + attr + "\n", text)
    script = block(text, "  - id: update_limit_screen\n", "\n  - id: ")
    assert "gcu::limit_screen(line, gcu::limit(), gcu::attr_value(id(ha_limit_since).state)," in script
    assert "lv_obj_set_style_text_color(id(limit_title_label), lv_color_hex(id(global_glucose_color)), 0);" in script
    assert "lvc_label_edge(id(limit_title_label));" in text
    cat = catalogue()
    assert cat["panel.limit_screen.low.title"] == "Very low" and cat["panel.limit_screen.high.title"] == "Very high"
    assert cat["panel.limit_screen.low.range"].replace("{limit}", cat["panel.value.lo"]) == "Below sensor range (LO)"
    assert cat["panel.limit_screen.high.range"].replace("{limit}", cat["panel.value.hi"]) == "Above sensor range (HI)"
    assert cat["panel.since.line"] == "Since {time} ({duration})"
    assert cat["panel.limit_screen.last_measured"] == "Last measured: {value} {unit} at {time}"
    assert cat["panel.duration.hours"] == "{hours} h {minutes} min"


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_without_home_assistant_for_an_hour_the_screen_below_the_clock_is_black(path):
    """One hour without the ESPHome connection to Home Assistant or without a successful download of GlucoCraft's
    files: everything below the clock and the date is black with the four approved lines ("Since" whichever started
    first, the last reading live while only the downloads fail); the page changes do nothing while it is up; it clears
    by itself. The one-hour limit is one constant."""
    text = read(path)
    tick = text[text.index("  - interval: 1s\n") :]
    assert "api::global_api_server != nullptr && api::global_api_server->is_connected();" in tick
    assert "const uint32_t dl = backfill_failing_since_ms();" in tick
    assert "lost_ms / 1000 >= gcu::NO_HA_AFTER_S;" in tick
    assert tick.index("lost_ms / 1000 >= gcu::NO_HA_AFTER_S;") < tick.index("if (!time.is_valid()) return;")
    assert read(UNITS_H).count("NO_HA_AFTER_S = 3600;") == 1 and "3600000" not in tick
    screen = block(text, "id: no_ha_screen\n", "\n      # >>> About screen")
    assert "hidden: true" in screen and "bg_color: 0x000000" in screen and "gesture_bubble: false" in screen
    assert "on_swipe" not in screen and "on_long_press" not in screen  # its touches go nowhere
    assert text.count("lambda: 'return !id(loading_mode_active) && !id(no_ha_screen_on);'") == 2  # nav_next, nav_prev
    assert "gc_note_download(ok);" in read(BINARY_H)
    cat = catalogue()
    assert cat["panel.no_connection.title"] == "Cannot reach Home Assistant or load its data"
    assert cat["panel.no_connection.last_reading"] == "Last reading: {value} {unit} at {time}"
    assert cat["panel.no_connection.check"] == "Check Home Assistant and the wall-panel address"


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_check_home_assistant_takes_turns_in_the_status_bar(path):
    """While a GlucoCraft notice is open in Repairs (the configuration attribute attention, "on" / "off"), "Check Home
    Assistant" takes turns with the warm-up text or "Custom ranges" in the status bar's middle, alone when neither
    shows; never the notice's text."""
    text = read(path)
    sensor = block(text, "    id: ha_attention\n", "\n  - platform: ")
    assert "entity_id: sensor.glucocraft_configuration\n    attribute: attention" in sensor
    assert 'if (x == "on" || x == "off") id(attention_on) = x == "on";' in sensor
    tick = text[text.index("  - interval: 1s\n") :]
    assert "const bool check = id(attention_on) && (base == 0 || (tick / 3) % 2 == 1);" in tick
    for shown in ("warmup_countdown_label), base == 1 && !check", "custom_ranges_label), base == 2 && !check",
                  "check_ha_label), check"):  # fmt: skip
        assert f"show(id({shown});" in tick
    assert '"${t_status_check_home_assistant}"' in text
    assert text_of("panel.status.check_home_assistant") == "Check Home Assistant"


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_a_reading_ahead_of_the_clock_has_no_age(path):
    """One tolerance for a reading time ahead of the clock, GlucoCraft's 5 minutes: up to it the reading counts as
    taken now and is stored at its own time; beyond it (or while GlucoCraft's time_untrusted is on) the status bar
    shows "Reading: ---" and no time line shows. The age counts in seconds with no upper cap."""
    text = read(path)
    assert re.search(r"entity_id: sensor\.glucocraft_last_reading_time\n\s+attribute: time_untrusted\n", text)
    tick = text[text.index("  - interval: 1s\n") :]
    assert "last_ts > (uint32_t) time.timestamp + gcu::FUTURE_TOLERANCE_S;" in tick
    assert "const bool untrusted = ahead || id(time_untrusted);" in tick
    assert "if (have_age && !untrusted) {" in tick
    assert "gcu::reading_age(time_since_buf, sizeof(time_since_buf), age_s / 60);" in tick
    assert "4000000" not in text and "elapsed_millis" not in text
    store = block(text, "  - id: store_glucose_reading\n", "\n  - id: ")
    assert "lr <= (uint32_t) tn.timestamp + gcu::FUTURE_TOLERANCE_S;" in store
    assert "+ 60;" not in store and "+ 300" not in tick
    assert "ts > now + gcu::FUTURE_TOLERANCE_S" in read(BINARY_H) and "now + 60" not in read(BINARY_H)
    assert "constexpr uint32_t FUTURE_TOLERANCE_S = 300;" in read(UNITS_H)


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_no_current_data_limit_is_kept_across_a_restart(path):
    """The panel's own count uses GlucoCraft's No current data after setting also after a restart without Home
    Assistant: kept in flash like the other settings."""
    text = read(path)
    assert re.search(
        r"- id: stale_threshold_min\n\s+type: float\n\s+restore_value: yes\n\s+initial_value: '15.0f'", text
    )
    assert "if (x > 0 && fabsf(x - id(stale_threshold_min)) > 0.01f) id(stale_threshold_min) = x;" in text
    assert "id(ha_stale_threshold).state" not in text
    assert text.count("id(stale_threshold_min) > 0 ? id(stale_threshold_min) : 15.0f") == 2


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_one_readings_updates_are_stored_together_whatever_their_order(path):
    """The value, the LO / HI mark and the reading's own time arrive one by one: the store waits for the last of them,
    a late mark takes only its own reading (by its time) out of the 6-hour ring, gives the saved copy its mark either
    way and runs the store again (a measured reading after a limit stretch is stored)."""
    text = read(path)
    store = block(text, "  - id: store_glucose_reading\n", "\n  - id: ")
    assert store.index("- delay: 300ms") < store.index("- lambda: |-") and "mode: restart" in store
    limit = block(text, "    id: ha_limit\n", "\n  - platform: ")
    assert "if (id(glucose_history_ts)[k] == own) {" in limit
    assert "if (own > 1600000000UL && id(saved_glucose_ts) == own) id(saved_glucose_limit) = gcu::limit();" in limit
    assert "- script.execute: store_glucose_reading" in limit
    assert "== (int) lroundf(have)" not in limit  # never by the value alone


def test_the_throb_window_is_30_minutes_from_the_episodes_start():
    """gc_units.h throb_window, transcribed: without current data the words throb while the reading's age is below
    the limit plus 30 minutes; in a warm-up for its first 30 minutes; without a known start for the first 30 minutes
    after the panel first showed the episode, as on the display card."""
    h = read(UNITS_H)
    fn = block(h, "inline bool throb_window(", "\n}\n")
    assert "if (warm_up && warm_up_min >= 0) return (uint32_t) warm_up_min * 60 < THROB_FOR_S;" in fn
    assert "if (!warm_up && have_age) return age_s < limit_s + THROB_FOR_S;" in fn
    assert "return shown_s < THROB_FOR_S;" in fn and "THROB_FOR_S = 30 * 60;" in h

    def window(warm, wmin, have, age, limit, shown):
        if warm and wmin >= 0:
            return wmin * 60 < 1800
        if not warm and have:
            return age < limit + 1800
        return shown < 1800

    assert window(False, -1, True, 15 * 60 + 1, 900, 0) and window(False, -1, True, 44 * 60 + 59, 900, 9999)
    assert not window(False, -1, True, 45 * 60, 900, 0)
    assert window(False, -1, False, 0, 900, 1799) and not window(False, -1, False, 0, 900, 1800)
    assert window(True, 29, False, 0, 900, 9999) and not window(True, 30, False, 0, 900, 0)
    assert window(True, -1, True, 0, 900, 0) and not window(True, -1, True, 0, 900, 1800)
    # the panel counts the moment it first showed the episode's words, again for a new episode (another kind)
    tick = read(SQUARE)
    for part in (
        "if (kind != shown_kind) {",
        "shown_at = kind != 0 ? (millis() | 1) : 0;",
        "const uint32_t shown_s = shown_at != 0 ? (millis() - shown_at) / 1000UL : 0;",
    ):
        assert part in tick, part


def test_the_very_low_cell_decides_100_percent_in_double():
    """pct1_cell decides "100%" as pct1 rounds, in double: the file's float32 99.949997 shows "99.9%", as the card."""
    body = block(read(UNITS_H), "inline void pct1_cell(char *buf, size_t n, float v) {", "\n}\n")
    assert "lround((double) v * 10.0) >= 1000" in body and "lroundf(v * 10.0f)" not in body
    v = struct.unpack("<f", struct.pack("<f", 99.949997))[0]
    assert round(v * 10.0) == 999  # in double: one decimal, "99.9%"
    assert round(struct.unpack("<f", struct.pack("<f", v * 10.0))[0]) == 1000  # the float32 product said "100%"


def test_the_period_slot_mean_is_rounded():
    """The period graphs' slot mean is rounded to the whole mg/dL (halves up), not truncated."""
    body = read(BINARY_H)
    assert "val_array[i] = (slot_sum[i] + slot_count[i] / 2) / slot_count[i];" in body
    assert "val_array[i] = slot_sum[i] / slot_count[i];" not in body
