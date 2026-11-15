# License: AGPL-3.0-only (LICENSE).
r"""Generate the landscape GlucoCraft panel configurations from the square panel.

The square panel (glucocraft-panel.yaml, 480 x 480) is the source. A landscape panel keeps its firmware logic, data,
texts, colors and rules (one system with the display card) and changes only the layout: the glucose block always on
the left, the graph and weekly pages on the right, the status bar along the bottom, scaled per screen size; and each
board gets its own hardware block. Every replacement is counted, and the script stops with an error on any surprise,
so that a change in the square panel that this script does not expect never produces a wrong file.

usage: python tools/make_glucocraft_landscape.py <board> [--out PATH]
boards: waveshare-lcd5 (1024 x 600), waveshare-p4-7b (1024 x 600), waveshare-lcd7 (800 x 480), guition-35 (480 x 320)

Paths are relative to this script: the configurations are in its parent folder, the top layer of the landscape
layout is landscape_top_layer.yaml next to it (@key@ = a layout value). The texts are not in these files: like the
square panel, every board takes them from the text catalogue of its substitution language
(tools/make_panel_texts.py), so one generated file serves every language. After any change to glucocraft-panel.yaml,
regenerate every board; after a change to a layout, also the texts (make_panel_texts.py: the About page's lines
beside the icon); then check the text widths with tools/worst_case_fit.py.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
PANELS = TOOLS.parent  # the folder with the panel configurations
SRC = PANELS / "glucocraft-panel.yaml"  # the square panel, the source of every landscape panel
TOP_LAYER = TOOLS / "landscape_top_layer.yaml"
ABOUT_WIDE = TOOLS / "landscape_about.yaml"

# ------------------------------------------------------------------ screen layouts
# Pixel sizes and fonts per screen size. W x H = the screen; HDR = the clock header's height; LW = the glucose block's
# width (the graphs get W - LW); SB = the status bar's height; CW x CH = the history graph canvas, TCH = the weekly
# canvas's height; cell_* = the statistics cells; about_* / splash_* = the About page and the start-up screen.
LAYOUTS = {
    (1024, 600): {
        "W": 1024,
        "H": 600,
        "HDR": 80,
        "clock_font": "roboto_60",
        "date_font": "roboto_26",
        "edge": 20,
        "LW": 450,
        "SB": 52,
        "sb_font": "roboto_24",
        "sb_check_font": "roboto_24",  # "Check Home Assistant" in the status bar's middle (see below)
        "value_font": "roboto_200",
        "value_size": 200,
        "value_y": 20,
        "word_font": "roboto_40",
        "word_y": 222,
        "word_w": 440,
        "col_y": 276,
        "col_h": 192,
        "col_lbl_font": "roboto_20",
        "col_lbl_y": 8,
        "arrow": 96,
        "arrow_y": 4,
        "trend_word_font": "roboto_40",  # the trend word (see below)
        "trend_word_w": 205,
        "src_font": "roboto_18",
        "src_h": 22,  # one line of src_font (LVGL's line height), so that a long name is cut with "..."
        "src_y": -10,
        "delta_font": "roboto_64",
        "delta_y": 4,
        # without a current reading (see below): the last reading and its time on one line each
        "nr_line_font": "roboto_26",
        "nr_time_font": "roboto_20",
        "nr_split": False,
        "load_font": "roboto_32",
        "ti_h": 148,
        "ti_title_font": "roboto_32",
        "ti_title_y": 14,
        "ti_desc_font": "roboto_18",
        "ti_desc_y": 62,
        "ti_status_font": "roboto_24",
        "ti_status_y": 108,  # below two lines of the description (worst_case_fit.py)
        "tc_y": 230,
        "TCH": 305,
        "CW": 550,
        "cv_x": 12,
        "cv_y": 14,
        "CH": 285,
        "row1_y": 312,
        "row2_y": 390,
        "row_h": 70,
        "cell_w": 91,
        "cell_h": 66,
        "cell_lbl_font": "roboto_18",
        "cell_val_font": "roboto_26",
        "about_head_font": "roboto_40",
        "about_text_font": "roboto_32",
        "about_ver_font": "roboto_20",
        "about_gap": 12,
        "about_top": 41,
        "about_bottom": 16,
        "about_icon": 144,
        "about_icon_gap": 20,  # the icon (see below)
        "splash_head_font": "roboto_56",
        "splash_font": "roboto_40",
        "splash_ver_font": "roboto_26",
        "splash_w": "90%",
        "splash_gap": 24,
        "gmi_both_font": "roboto_18",  # see below
    },
    (800, 480): {
        "W": 800,
        "H": 480,
        "HDR": 64,
        "clock_font": "roboto_56",
        "date_font": "roboto_22",
        "edge": 16,
        "LW": 352,
        "SB": 40,
        "sb_font": "roboto_20",
        "sb_check_font": "roboto_20",
        "value_font": "roboto_160",
        "value_size": 160,
        "value_y": 14,
        "word_font": "roboto_40",
        "word_y": 176,
        "word_w": 344,
        "col_y": 226,
        "col_h": 150,
        "col_lbl_font": "roboto_16",
        "col_lbl_y": 6,
        "arrow": 80,
        "arrow_y": 2,
        "trend_word_font": "roboto_36",
        "trend_word_w": 160,
        "src_font": "roboto_16",
        "src_h": 19,  # one line of src_font (LVGL's line height), so that a long name is cut with "..."
        "src_y": -8,
        "delta_font": "roboto_64",
        "delta_y": 2,
        "nr_line_font": "roboto_26",
        "nr_time_font": "roboto_18",
        "nr_split": False,
        "load_font": "roboto_26",
        "ti_h": 120,
        "ti_title_font": "roboto_26",
        "ti_title_y": 10,
        "ti_desc_font": "roboto_16",
        "ti_desc_y": 50,
        "ti_status_font": "roboto_20",
        "ti_status_y": 90,  # below two lines of the description (worst_case_fit.py)
        "tc_y": 186,
        "TCH": 248,
        "CW": 430,
        "cv_x": 9,
        "cv_y": 10,
        "CH": 228,
        "row1_y": 246,
        "row2_y": 312,
        "row_h": 62,
        "cell_w": 71,
        "cell_h": 58,
        "cell_lbl_font": "roboto_14",
        "cell_val_font": "roboto_20",
        "about_head_font": "roboto_32",
        "about_text_font": "roboto_24",
        "about_ver_font": "roboto_18",
        "about_gap": 10,
        "about_top": 32,
        "about_bottom": 12,
        "about_icon": 112,
        "about_icon_gap": 16,
        "splash_head_font": "roboto_56",
        "splash_font": "roboto_32",
        "splash_ver_font": "roboto_26",
        "splash_w": "94%",
        "splash_gap": 24,
        "gmi_both_font": "roboto_14",
    },
    # 3.5" Guition JC3248W535: the square panel's font sizes, narrower graphs, statistics 3 x 4
    (480, 320): {
        "W": 480,
        "H": 320,
        "HDR": 40,
        "clock_font": "roboto_32",
        "date_font": "roboto_16",
        "edge": 8,
        "LW": 190,
        "SB": 30,
        "sb_font": "roboto_16",  # a narrower glucose block for wider graphs
        "sb_check_font": "roboto_14",  # one size below the bar's: the room between the widest texts beside it
        "value_font": "roboto_90",
        "value_size": 90,
        "value_y": 6,
        "word_font": "roboto_32",
        "word_y": 98,
        "word_w": 186,  # value 90 px
        "word_big": "roboto_32",
        "word_small": "roboto_22",  # the zone word under the value where 32 px is wider than its label
        "col_y": 140,
        "col_h": 110,
        "col_lbl_font": "roboto_12",
        "col_lbl_y": 2,
        "arrow": 40,
        "arrow_y": 2,
        "trend_word_font": "roboto_20",
        "trend_word_w": 87,
        "src_font": "roboto_12",
        "src_h": 15,  # one line of src_font (LVGL's line height), so that a long name is cut with "..."
        "src_y": -3,
        "delta_font": "roboto_40",
        "delta_y": 2,
        "delta_big": "roboto_40",
        "delta_small": "roboto_26",
        "nr_line_font": "roboto_14",  # 14 px, the smallest text size, and the age on a line of its own
        "nr_time_font": "roboto_14",
        "nr_split": True,
        "load_font": "roboto_22",
        "ti_h": 84,
        "ti_title_font": "roboto_18",
        "ti_title_y": 4,
        "ti_desc_font": "roboto_12",
        "ti_desc_y": 28,
        "ti_status_font": "roboto_14",
        "ti_status_y": 62,
        "tc_y": 126,
        "TCH": 160,
        "CW": 276,
        "cv_x": 5,
        "cv_y": 4,
        "CH": 126,
        "row_ys": (134, 172, 210),
        "row_h": 36,  # the last row ends 4 px above the status bar
        "cell_w": 68,
        "cell_h": 34,
        "stats_cols": 4,  # 9 px from the screen edge
        "cell_lbl_font": "roboto_12",
        "cell_val_font": "roboto_16",  # smaller numbers (the labels stay 12 px), a taller graph
        "about_head_font": "roboto_20",
        "about_text_font": "roboto_16",
        "about_ver_font": "roboto_14",
        "about_gap": 4,
        "about_top": 10,
        "about_bottom": 6,
        "about_icon": 60,
        "about_icon_gap": 8,  # every text at least 14 px, to stay legible
        "splash_head_font": "roboto_32",
        "splash_font": "roboto_20",
        "splash_ver_font": "roboto_18",
        "splash_w": "96%",
        "splash_gap": 16,
        "gmi_both_font": "roboto_12",
    },
}

# splash_head_font / splash_font / splash_ver_font / splash_w / splash_gap: the start-up screen (GlucoCraft's icon in
# the About page's size, the name, the panel version and the disclaimer line, centered), measured with Roboto
# (worst_case_fit.py; greedy word wrap; heights = Roboto's ascent + descent per line + the gaps).
# The About page (about_*): a full screen (no clock header, no glucose information, larger text), from
# tools/landscape_about.yaml: GlucoCraft's icon (about_icon px, images/glucocraft_icon_<px>.png) in a column of its own
# on the left, centered from top to bottom; about_top = the top margin and the side margins, about_bottom = the bottom
# margin, about_icon_gap px from the icon to the text column. In the text column (about_text_width()): the title
# (about_head_font) and GlucoCraft's disclaimer (about_text_font), about_gap apart, centered in the room above the
# footer; the footer at the bottom, centered in the same column: a thin line, the version line and the copyright line
# (about_ver_font). worst_case_fit.py measures every line and the page's height with Roboto, in each language.
# sb_check_font: "Check Home Assistant", which takes turns in the status bar's middle while a GlucoCraft notice is open
# in Repairs: the bar's size where it fits between the widest texts beside it, one size smaller on the 480 px wide
# layout (worst_case_fit.py), as on the square panel.
# gmi_both_font: the GMI cell in both A1C units ("12.9%/117" within the cell: 85 / 91, 68 / 71, 59 / 68 px).
# nr_line_font / nr_time_font / nr_split: without a current reading the value's place shows, stacked and centered
# between the value's top (value_y) and the TREND and 5 MIN DELTA columns (col_y), the words ("Sensor warming up",
# "Sensor expired", "No current data"; word_font, wrapped at word_w), the last reading in nr_line_font ("Last
# reading: 64 mg/dL") and when it was taken in nr_time_font ("Today at 17:39 (2 h 0 min ago)"); with nr_split the
# age goes on a line of its own ("Today at 17:39" / "(2 h 0 min ago)"), where one line does not fit (the
# square panel and the 3.5-inch). The sizes: the largest at which the widest forms fit word_w ("Last reading: 22.2
# mmol/L" 305 of 440 / 344 px at 26 px, 164 of 186 px at 14 px; "Yesterday at 12:59 PM (23 h 59 min ago)" 362 of
# 440 px at 20 px, 322 of 344 px at 18 px), measured with Roboto; worst_case_fit.py checks the widths and the
# stack's height in every language.
# trend_word_font / trend_word_w: the trend word in the TREND column (GlucoCraft's setting Trend shown as: word), wrapped
# at trend_word_w (a longer word on two lines), centered where the arrow is: a size at which the longest word of a
# line fits ("Changing": 168 of 205 px at 40 px, 152 of 160 px at 36 px, 84 of 87 px at 20 px; the square panel 109 of
# 111 px at 26 px), measured with Roboto. The 800 x 480 layout uses 36 px, one size below the largest that fits (38 px,
# 160 of 160 px with no room in height either), because the panel's whole-pixel glyph widths can differ from the
# measurement (worst_case_fit.py checks every language's words and their height between the column's label and the
# source's name).

# ------------------------------------------------------------------ boards
# Per board: its file (in PANELS), screen size, names, secret prefix, access-point name, flash size, the
# header's title and hardware note, and the hardware blocks that replace the square panel's.
BOARDS = {
    "waveshare-lcd5": {
        "file": "glucocraft-waveshare-lcd5.yaml",
        "size": (1024, 600),
        "name": "glucocraft-waveshare-lcd5",
        "friendly": "GlucoCraft Waveshare LCD-5",
        "project": "glucocraft.waveshare-lcd5-1024x600",
        "secret": "glucocraft_waveshare_lcd5",
        "ap": "GlucoCraft-WS-LCD5",
        "flash": "16MB",
        "title": "GlucoCraft Waveshare LCD-5 - Waveshare ESP32-S3-Touch-LCD-5 Rev 1.1 (1024 x 600)",
        "hw_note": """# Hardware: ESP32-S3, 16 MB flash, 8 MB
# octal PSRAM; ESPHome mipi_rgb model WAVESHARE-5-1024X600; GT911 touch on I2C GPIO8/9, interrupt GPIO4; CH422G
# expander: touch reset pin 1, backlight pin 2 (on / off only), display reset pin 3.""",
        "hardware": """
# Waveshare ESP32-S3-Touch-LCD-5 Rev 1.1
i2c:
  - id: touch_i2c
    sda: GPIO08
    scl: GPIO09
    scan: false

ch422g:
  - id: ch422g_hub
    i2c_id: touch_i2c

output:
  - platform: gpio
    id: backlight_output
    pin:
      ch422g: ch422g_hub
      number: 2
      allow_other_uses: true   # also the display's enable pin (below)
      mode:
        output: true
      inverted: false

light:
  - platform: binary   # the CH422G switches the backlight on / off only (no dimming on this board)
    output: backlight_output
    name: "Display Backlight"
    id: backlight
    initial_state:   # on at every boot, never restored from flash
      state: true
    restore_state: none

touchscreen:
  - platform: gt911
    id: my_touchscreen
    # any touch starts the 3 minutes until the return to the first page again, as any use of the display card does
    on_touch:
      - lambda: 'id(page_idle_seconds) = 0;'
    i2c_id: touch_i2c
    interrupt_pin: GPIO4
    reset_pin:
      ch422g: ch422g_hub
      number: 1
      mode: OUTPUT
    display: my_display

display:
  - platform: mipi_rgb
    enable_pin:   # ESPHome 2026.10: the panel enable (CH422G pin 2, shared with the backlight output)
      - ch422g: ch422g_hub
        number: 2
        allow_other_uses: true
    model: WAVESHARE-5-1024X600
    id: my_display
    update_interval: never
    auto_clear_enabled: false
    color_order: RGB
    pclk_frequency: 16MHZ
    pclk_inverted: true
    dimensions:
      width: 1024
      height: 600
    de_pin:
      number: 5
    hsync_pin:
      number: 46
      ignore_strapping_warning: true
    vsync_pin:
      number: 3
      ignore_strapping_warning: true
    pclk_pin: 7
    reset_pin:
      ch422g: ch422g_hub
      number: 3
    data_pins:
      red: [1, 2, 42, 41, 40]
      blue: [14, 38, 18, 17, 10]
      green: [39, 0, 45, 48, 47, 21]
""",
    },
}

BOARDS["waveshare-p4-7b"] = {
    "file": "glucocraft-waveshare-p4-7b.yaml",
    "size": (1024, 600),
    "name": "glucocraft-waveshare-p4-7b",
    "friendly": "GlucoCraft Waveshare P4 7B",
    "project": "glucocraft.waveshare-p4-7b-1024x600",
    "secret": "glucocraft_waveshare_p4_7b",
    "ap": "GlucoCraft-WS-P4-7B",
    "flash": "16MB",
    "lvgl_full_buffer": True,
    "title": 'GlucoCraft Waveshare P4 7B - Waveshare ESP32-P4-WIFI6-Touch-LCD-7B (7", 1024 x 600 MIPI-DSI)',
    "hw_note": """# Hardware: ESP32-P4 (its chip revision: the substitution engineering_sample), hex PSRAM 200 MHz, 16 MB flash,
# LDO channel 3 at 2.5 V for the display; Wi-Fi 6 through the on-board ESP32-C6 (esp32_hosted, SDIO); MIPI-DSI
# display, ESPHome model WAVESHARE-ESP32-P4-WIFI6-TOUCH-LCD-7B; GT911 touch on I2C GPIO7/8, reset GPIO23;
# backlight PWM on GPIO32 (inverted).""",
    "substitutions": """\
  # the chip revision of the board's ESP32-P4 (the ESPHome option esp32: engineering_sample), set for the board in use:
  # "false" for a production chip (revision v3.0 or later), "true" for an early engineering sample (below v3.0),
  # which restarts in a loop with "Illegal instruction" without it. The start-up log names the revision
  # ("chip revision: vX.Y").
  engineering_sample: "false"   # REPLACE for your board: "true" only for an ESP32-P4 below chip revision v3.0
""",
    "esp32_block": """esp32:
  variant: esp32p4
  engineering_sample: ${engineering_sample}   # the board's chip revision (the substitution above)
  flash_size: 16MB
  cpu_frequency: 360MHz
  framework:
    type: esp-idf
    sdkconfig_options:   # the 30 s task watchdog of the other panels, idle tasks of both cores included (ESP-IDF: 5 s)
      CONFIG_ESP_TASK_WDT: y
      CONFIG_ESP_TASK_WDT_TIMEOUT_S: "30"
      CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU0: y
      CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU1: y
    advanced:
      enable_idf_experimental_features: true
      include_builtin_idf_components:
        - esp_http_client
        - driver

psram:
  mode: hex
  speed: 200MHz

esp_ldo:
  - voltage: 2.5V
    channel: 3

# Wi-Fi 6 through the on-board ESP32-C6 (SDIO)
esp32_hosted:
  variant: ESP32C6
  reset_pin: GPIO54
  cmd_pin: GPIO19
  clk_pin: GPIO18
  d0_pin: GPIO14
  d1_pin: GPIO15
  d2_pin: GPIO16
  d3_pin: GPIO17
  active_high: true
  use_psram: true   # the Wi-Fi link's 48 KB buffer pool in PSRAM; without it: assert sdio_mempool_create (esphome#16574 / #16627)

""",
    # the esp_hosted interface, for the co-processor firmware sensor below
    "includes": ["<esp_hosted.h>", "<esp_hosted_host_fw_ver.h>"],
    # Every restart through ESPHome (an update over Wi-Fi, the Restart button, a settings change) first holds the ESP32-C6
    # in reset for 20 ms (its enable line, GPIO54, low: the line is high while the C6 runs, esp32_hosted active_high), so
    # that the C6 starts afresh with the ESP32-P4, as after removing the power. Without it the C6 could stay stuck after
    # an update over Wi-Fi (seen on the serial log: "sdmmc_send_cmd returned 0x107", "H_SDIO_DRV: failed to read
    # registers", a restart every 15 s until the power was removed); a plain restart did not trigger it.
    "on_shutdown": """  on_shutdown:
    - priority: -100   # after the other components' shutdown, just before the restart
      then:
        - lambda: |-
            // the ESP32-C6's enable line low: the C6 is held in reset and starts afresh with the ESP32-P4
            gpio_reset_pin(GPIO_NUM_54);
            gpio_set_direction(GPIO_NUM_54, GPIO_MODE_OUTPUT);
            gpio_set_level(GPIO_NUM_54, 0);
            delay(20);
""",
    "text_sensors": """  # The firmware of the Wi-Fi co-processor (the ESP32-C6 runs its own esp_hosted firmware, which should match the
  # host library that ESPHome builds into the panel; the start-up log shows both). Read only, from the ESP32-C6 itself
  # over the board's internal SDIO link (no network request), after Wi-Fi is up (a read before the link is up waits
  # for an answer and fails). Each unanswered request holds the main loop for about one second, so the panel asks at
  # most three times: esp_hosted firmware before 1.0.0 has no version request and never answers, and the sensor then
  # shows "Not reported". That old firmware made this board fail to start now and then; docs/PANELS.md, "Waveshare P4
  # 7B", describes how to update it. The panel has no update entity for it: an update entity would contact
  # esphome.github.io after each start and every 6 hours, and the panels contact nothing outside the home network.
  - platform: template
    id: coprocessor_firmware
    name: "Wi-Fi Co-processor Firmware"
    entity_category: diagnostic
    icon: mdi:chip
    update_interval: 60s
    lambda: |-
      static std::string version;
      static uint8_t attempts = 0;
      if (version.empty() && attempts < 3 && wifi::global_wifi_component->is_connected()) {
        attempts++;
        esp_hosted_coprocessor_fwver_t v;
        if (esp_hosted_get_coprocessor_fwversion(&v) == ESP_OK) {
          version = str_sprintf("%" PRIu32 ".%" PRIu32 ".%" PRIu32, v.major1, v.minor1, v.patch1);
        } else if (attempts == 3) {
          version = "Not reported";
        }
        if (!version.empty()) {
          ESP_LOGI("hosted", "Wi-Fi co-processor firmware %s, host library %d.%d.%d", version.c_str(),
                   ESP_HOSTED_VERSION_MAJOR_1, ESP_HOSTED_VERSION_MINOR_1, ESP_HOSTED_VERSION_PATCH_1);
        }
      }
      if (version.empty())
        return {};
      return version;
""",
    "hardware": """
# Waveshare ESP32-P4-WIFI6-Touch-LCD-7B
i2c:
  - id: touch_i2c
    sda: GPIO7
    scl: GPIO8
    frequency: 400kHz

output:
  - platform: ledc
    id: backlight_output
    pin: GPIO32
    inverted: true
    frequency: 1000Hz

light:
  - platform: monochromatic
    output: backlight_output
    name: "Display Backlight"
    id: backlight
    initial_state:   # on at every boot, never restored from flash
      state: true
    restore_state: none
    default_transition_length: 0s

touchscreen:
  - platform: gt911
    id: my_touchscreen
    # any touch starts the 3 minutes until the return to the first page again, as any use of the display card does
    on_touch:
      - lambda: 'id(page_idle_seconds) = 0;'
    i2c_id: touch_i2c
    reset_pin: GPIO23
    update_interval: 50ms
    display: my_display
    transform:   # the touch panel is mounted 180 deg to the display (measured: the right edge read x ~20, the top y ~580)
      mirror_x: true
      mirror_y: true

display:
  - platform: mipi_dsi
    id: my_display
    model: WAVESHARE-ESP32-P4-WIFI6-TOUCH-LCD-7B
    dimensions:
      width: 1024
      height: 600
    auto_clear_enabled: false
    update_interval: never
""",
}

BOARDS["guition-35"] = {
    "file": "glucocraft-guition-35.yaml",
    "size": (480, 320),
    "name": "glucocraft-guition-35",
    "friendly": "GlucoCraft Guition 3.5",
    "project": "glucocraft.guition35-480x320",
    "secret": "glucocraft_guition_35",
    "ap": "GlucoCraft-Guition35",
    "flash": "16MB",
    "lvgl_rotation": 90,
    "title": 'GlucoCraft Guition 3.5 - Guition JC3248W535 (3.5", 480 x 320 landscape)',
    "hw_note": """# Hardware: ESP32-S3 N16R8; 3.5" IPS 320 x 480 native,
# AXS15231 controller over QSPI -> mipi_spi model JC3248W535; landscape by LVGL rotation: 90 (the controller cannot
# swap axes; needs ESPHome 2026.5+); AXS15231 touch on I2C GPIO4/8 (0x3B); backlight PWM on GPIO1.""",
    "hardware": """
# Guition JC3248W535
spi:
  - id: quad_spi
    type: quad
    clk_pin: 47
    data_pins: [21, 48, 40, 39]

i2c:
  - id: touch_i2c
    sda: 4
    scl: 8
    scan: false
    frequency: 400kHz

output:
  - platform: ledc
    pin: 1
    id: backlight_output

light:
  - platform: monochromatic
    output: backlight_output
    name: "Display Backlight"
    id: backlight
    initial_state:   # on at every boot, never restored from flash
      state: true
    restore_state: none
    default_transition_length: 0s

touchscreen:
  - platform: axs15231
    id: my_touchscreen
    # any touch starts the 3 minutes until the return to the first page again, as any use of the display card does
    on_touch:
      - lambda: 'id(page_idle_seconds) = 0;'
    i2c_id: touch_i2c
    display: my_display

display:
  - platform: mipi_spi
    id: my_display
    model: JC3248W535
    spi_id: quad_spi
    data_rate: 40MHz
    update_interval: never
    auto_clear_enabled: false
""",
}

BOARDS["waveshare-lcd7"] = {
    "file": "glucocraft-waveshare-lcd7.yaml",
    "size": (800, 480),
    "name": "glucocraft-waveshare-lcd7",
    "friendly": "GlucoCraft Waveshare LCD-7",
    "project": "glucocraft.waveshare-lcd7-800x480",
    "secret": "glucocraft_waveshare_lcd7",
    "ap": "GlucoCraft-WS-LCD7",
    "flash": "8MB",
    "title": 'GlucoCraft Waveshare LCD-7 - Waveshare ESP32-S3-Touch-LCD-7 Rev 1.2 (7", 800 x 480)',
    "hw_note": """# Hardware: ESP32-S3, 8 MB flash, octal PSRAM;
# ESPHome mipi_rgb model ESP32-S3-TOUCH-LCD-7-800X480, 13 MHz pixel clock; GT911 touch on I2C GPIO8/9, interrupt GPIO4;
# CH422G expander: touch reset pin 1, backlight pin 2 (on / off only), display reset pin 3; log on the native USB port.""",
    "hardware": """
# Waveshare ESP32-S3-Touch-LCD-7 Rev 1.2
i2c:
  - id: touch_i2c
    sda: GPIO08
    scl: GPIO09
    scan: false

ch422g:
  - id: ch422g_hub
    i2c_id: touch_i2c

output:
  - platform: gpio
    id: backlight_output
    pin:
      ch422g: ch422g_hub
      number: 2
      allow_other_uses: true   # also the display's enable pin (below)
      mode:
        output: true
      inverted: false

light:
  - platform: binary   # the CH422G switches the backlight on / off only (no dimming on this board)
    output: backlight_output
    name: "Display Backlight"
    id: backlight
    initial_state:   # on at every boot, never restored from flash
      state: true
    restore_state: none

touchscreen:
  - platform: gt911
    id: my_touchscreen
    # any touch starts the 3 minutes until the return to the first page again, as any use of the display card does
    on_touch:
      - lambda: 'id(page_idle_seconds) = 0;'
    i2c_id: touch_i2c
    interrupt_pin: GPIO4
    reset_pin:
      ch422g: ch422g_hub
      number: 1
      mode: OUTPUT
    display: my_display

display:
  - platform: mipi_rgb
    enable_pin:   # ESPHome 2026.10: the panel enable (CH422G pin 2, shared with the backlight output) and pin 6
      - ch422g: ch422g_hub
        number: 2
        allow_other_uses: true
      - ch422g: ch422g_hub
        number: 6
    model: ESP32-S3-TOUCH-LCD-7-800X480
    id: my_display
    update_interval: never
    auto_clear_enabled: false
    color_order: RGB
    pclk_frequency: 13MHZ
    pclk_inverted: true
    dimensions:
      width: 800
      height: 480
    de_pin:
      number: 5
    hsync_pin:
      number: 46
      ignore_strapping_warning: true
    vsync_pin:
      number: 3
      ignore_strapping_warning: true
    pclk_pin: 7
    reset_pin:
      ch422g: ch422g_hub
      number: 3
    hsync_back_porch: 8
    hsync_front_porch: 8
    hsync_pulse_width: 4
    vsync_back_porch: 16
    vsync_front_porch: 16
    vsync_pulse_width: 4
    data_pins:
      red: [1, 2, 42, 41, 40]
      blue: [14, 38, 18, 17, 10]
      green: [39, 0, 45, 48, 47, 21]
""",
}


# ------------------------------------------------------------------ the graph pages (right side)
# (page id, period key, graph container, canvas, GMI label: the catalogue's panel.stat.<label>)
PAGES = [
    ("page_6h", "6h", "graph_container", "glucose_canvas", "gmi_14d"),
    ("page_24h", "24h", "graph_container_24h", "glucose_canvas_24h", "gmi_14d"),
    ("page_7d", "7d", "graph_container_7d", "glucose_canvas_7d", "gmi_14d"),
    ("page_14d", "14d", "graph_container_14d", "glucose_canvas_14d", "gmi_14d"),
    ("page_30d", "30d", "graph_container_30d", "glucose_canvas_30d", "gmi_30d"),
    ("page_90d", "90d", "graph_container_90d", "glucose_canvas_90d", "gmi_90d"),
    ("page_180d", "180d", "graph_container_180d", "glucose_canvas_180d", "gmi_180d"),
]


class GeneratorError(Exception):
    """The square panel does not contain what a replacement expects."""


# The first line of every generated configuration: the license line that every GlucoCraft source file carries
LICENSE_LINE = "License: AGPL-3.0-only (LICENSE)."


def check(condition: bool, what: str) -> None:
    """Stop with an error when a check fails (unlike assert, also under python -O)."""
    if not condition:
        raise GeneratorError(what)


def one(s: str, old: str, new: str, count: int = 1) -> str:
    """Replace old with new in s, and stop when old does not occur exactly count times: a landscape edit that no
    longer matches the square source must stop the generator, never pass silently."""
    n = s.count(old)
    check(n == count, f"found {n} times, expected {count}: {old[:90]!r}")
    return s.replace(old, new)


def about_text_width(layout: dict) -> int:
    """Return the width of the About page's text column (px): the screen less its side margins, the icon and the gap."""
    return layout["W"] - 2 * layout["about_top"] - layout["about_icon"] - layout["about_icon_gap"]


def layout_values(layout: dict) -> dict:
    """Return the layout's values together with the values derived from them, for the @key@ placeholders."""
    rx, rw = layout["LW"], layout["W"] - layout["LW"]
    return dict(
        layout,
        RX=rx,
        RW=rw,
        BH=layout["H"] - layout["HDR"] - layout["SB"],  # block height (left block and right area)
        TI_W=rw - 20,
        COL_W=layout["LW"] // 2,
        SRC_W=layout["LW"] // 2 - 10,
        TC_X=rx + layout["cv_x"],
        SB_Y=layout["H"] - layout["SB"],
        MX=layout["edge"],
        NR_H=layout["col_y"] - layout["value_y"],  # the no-reading stack's height
        NO_HA_H=layout["H"] - layout["HDR"],  # the screen without Home Assistant: everything below the header
        NO_HA_W=layout["W"] - 40,  # its lines' width
    )


def fill(text: str, values: dict) -> str:
    """Replace every @key@ placeholder in text with the layout value."""
    return re.sub(r"@(\w+)@", lambda m: str(values[m.group(1)]), text)


def header(s: str, board: dict, values: dict) -> str:
    """Replace the square panel's header comment with the board's."""
    a = s.index("substitutions:")
    extra_subs = (
        ", language (the panel's texts) and engineering_sample (the chip revision)"
        if board.get("substitutions")
        else " and language (the panel's texts)"
    )
    return (
        fill(
            f"""# {LICENSE_LINE}
# ============================================================================
# {board["title"]}
# ============================================================================
# Panel type {board["project"]} (docs/PANELS.md, "Panel types"). Generated from glucocraft-panel.yaml
# (the 480 x 480 Guition panel) by tools/make_glucocraft_landscape.py; do not edit by hand, regenerate. The same
# logic, data, texts (the text catalogue i18n/<language>.yaml of the substitution language), colors and rules (one
# system with the display card), in the landscape layout:
#   - left (@LW@ x @BH@): the glucose block - value @value_size@ px, zone word, TREND arrow or trend word + source,
#     5 MIN DELTA -
#     always visible, also on the weekly pages (without a current reading the words in the value's place);
#   - right (@RW@ x @BH@): the graph pages (canvas @CW@ x @CH@, twelve statistics in rows) or the weekly pages
#     (title, description, "Last 14 days" line, canvas @CW@ x @TCH@);
#   - status bar along the bottom (@W@ x @SB@).
# Secrets (secrets.yaml): wifi_ssid, wifi_password, {board["secret"]}_api_key (also the OTA key),
# {board["secret"]}_ap_password, glucocraft_panel_webhook_id.
# Substitutions: ha_host / ha_port (the Home Assistant that runs GlucoCraft), ntp_router (an optional local time
# server){extra_subs}.
{board["hw_note"]}
# ============================================================================

""",
            values,
        )
        + s[a:]
    )


def identity(s: str, board: dict) -> str:
    """Set the board's names, keys, flash size, chip blocks and includes."""
    s = one(
        s,
        "  name: glucocraft-panel\n  friendly_name: GlucoCraft Panel\n",
        f"  name: {board['name']}\n  friendly_name: {board['friendly']}\n",
    )
    # the panel type (the project name in ESPHome, the model in Home Assistant): the board and its resolution
    s = one(s, "    name: glucocraft.guition4848s040-480x480   #", f"    name: {board['project']}   #")
    s = one(s, "    key: !secret glucocraft_panel_api_key\n", f"    key: !secret {board['secret']}_api_key\n")
    s = one(
        s,
        '    ssid: "GlucoCraft-Panel"\n    password: !secret glucocraft_panel_ap_password\n',
        f'    ssid: "{board["ap"]}"\n    password: !secret {board["secret"]}_ap_password\n',
    )
    if board.get("esp32_block"):
        # another chip (ESP32-P4): its own esp32 / psram blocks replace the S3 ones (S3 sdkconfig options do not apply)
        a = s.index("\nesp32:\n") + 1
        b = s.index("\nlogger:\n", a) + 1
        s = s[:a] + board["esp32_block"] + s[b:]
    else:
        s = one(s, "  flash_size: 16MB\n", f"  flash_size: {board['flash']}\n")
    if board.get("lvgl_full_buffer"):
        # MIPI-DSI on the P4: a full-screen LVGL buffer, little-endian (as in the board's working configuration)
        s = one(s, "  buffer_size: 50%\n", "  buffer_size: 100%\n  byte_order: little_endian\n")
    if board.get("includes"):
        s = one(s, "  includes:\n", "  includes:\n" + "".join(f"    - {i}\n" for i in board["includes"]))
    if board.get("on_shutdown"):
        s = one(s, "  on_boot:\n", board["on_shutdown"] + "  on_boot:\n")
    if board.get("substitutions"):
        # the board's own substitutions, after the square panel's
        webhook = "  gc_webhook_id: !secret glucocraft_panel_webhook_id\n"
        s = one(s, webhook, webhook + board["substitutions"])
    if board.get("text_sensors"):
        s = one(s, "\ntext_sensor:\n", "\ntext_sensor:\n" + board["text_sensors"])
    return s


def hardware(s: str, board: dict) -> str:
    """Replace the square panel's hardware blocks (spi: up to sensor:) with the board's."""
    a = s.index("\nspi:\n")
    b = s.index("\nsensor:\n", a)
    return s[:a] + board["hardware"] + s[b:]


def canvas_sizes(s: str, layout: dict) -> str:
    """Resize the graph canvases and move the graph titles to the layout's graph width."""
    s = re.sub(r"\b340\b", str(layout["CW"]), s)
    s = re.sub(r"\b339\b", str(layout["CW"] - 1), s)
    a = s.index("  - id: draw_period_graphs\n")
    b = s.index("\n  - id: ", a + 10)
    part = s[a:b]
    for old, new in (
        (r"\b150\b", str(layout["CH"])),
        (r"\b149\b", str(layout["CH"] - 1)),
        (r"\b131\b", str(layout["CH"] - 19)),
    ):  # period labels 19 px above the bottom
        part = re.sub(old, new, part)
    s = s[:a] + part + s[b:]
    s = one(s, "          const int canvas_height = 150;\n", f"          const int canvas_height = {layout['CH']};\n")
    # graph titles: right-aligned, 16 px from each graph's right edge (the square panel's fixed x 195 suits only its
    # 340 px graph)
    s = one(
        s,
        "lvc_draw_text_edged(cv, 195, 5, 135, &ld, title);",
        f"lvc_draw_text_edged(cv, {layout['CW'] - 151}, 5, 135, &ld, title);",
    )
    s = one(
        s,
        "lvc_draw_text_edged(canvas_obj, 195, 5, 135, &label_dsc, gct::graph_title_6h);",
        f"lvc_draw_text_edged(canvas_obj, {layout['CW'] - 151}, 5, 135, &label_dsc, gct::graph_title_6h);",
    )
    s = one(
        s,
        "lvc_draw_text_edged(id(trend_canvas), cw - 180, 5, 170,",
        "lvc_draw_text_edged(id(trend_canvas), cw - 186, 5, 170,",
    )  # weekly label: the same 16 px margin
    return one(
        s,
        "          int cw = 480;\n          int ch = 189;\n",
        f"          int cw = {layout['CW']};\n          int ch = {layout['TCH']};\n",
    )


def weekly_pages_keep_glucose_block(s: str) -> str:
    """Keep the glucose block (left) on the weekly pages."""
    s = one(
        s,
        "          const bool owned = (id(current_page) >= 7 && id(current_page) <= 15) || id(loading_mode_active);"
        "  // weekly pages / loading own the screen",
        "          const bool owned = id(loading_mode_active);"
        "  // loading owns the screen (the glucose block stays on the weekly pages: landscape layout)",
    )
    return one(
        s,
        """      # Show trend UI, hide glucose UI (keep status bar visible)
      - lvgl.widget.hide: glucose_data_container
      - script.execute: refresh_glucose_block   # the hidden block's throb stops
      - lvgl.widget.show: trend_info_container""",
        """      # Show the weekly page on the right; the glucose block on the left stays (landscape layout)
      - lvgl.widget.show: trend_info_container""",
    )


def remove_weekly_mini_value(s: str) -> str:
    """Remove the square panel's small current value of the weekly pages.

    The square panel hides the glucose block on the weekly pages and shows a small current value there instead
    (update_weekly_mini, its arrow image and its calls, each between "# >>> weekly mini value (...)" and
    "# <<< weekly mini value (...)"); the landscape layouts keep the glucose block on the weekly pages, so these go.
    """
    n_mini = 0
    while True:
        m = re.search(r"\n[ ]*# >>> weekly mini value \((\w+)\)\n", s)
        if not m:
            break
        end_mark = f"# <<< weekly mini value ({m.group(1)})\n"
        e = s.index(end_mark, m.end()) + len(end_mark)
        s = s[: m.start() + 1] + s[e:]
        n_mini += 1
    # the script, the image, two calls and its edge of Outline text (the widgets go with the square top layer, see
    # top_layer)
    check(n_mini == 5, f"{n_mini} weekly mini value blocks, expected 5")
    return s


def fonts_and_images(s: str, board: dict, layout: dict, values: dict) -> str:
    """Add the layout's glucose value and trend word fonts and set the image, delta, word and GMI sizes."""
    if layout["value_font"] != "roboto_120":  # a layout using the square panel's roboto_120 keeps it (none does)
        s = one(
            s,
            """  - file: "gfonts://Roboto"
    id: roboto_120
    size: 120
    bpp: 4
    glyphs: "${glyphs_value}"
""",
            fill(
                """  - file: "gfonts://Roboto"
    id: roboto_120
    size: 120
    bpp: 4
    glyphs: "${glyphs_value}"
  # the glucose value on the landscape panel (@value_size@ px; "888" fits the @LW@ px block; the decimal comma; a limit
  # reading shows LO / HI)
  - file: "gfonts://Roboto"
    id: @value_font@
    size: @value_size@
    bpp: 4
    glyphs: "${glyphs_value}"
""",
                values,
            ),
        )
    # 56 px (the landscape layouts' clock and start-up heading; the square panel has no use for it)
    s = one(
        s,
        "  # Large number-only fonts (clock, glucose value)\n",
        """  - file: "gfonts://Roboto"
    id: roboto_56
    size: 56
    bpp: 4
    glyphs: "${glyphs_heading}"
  # Large number-only fonts (clock, glucose value)
""",
    )
    if f"    id: {layout['trend_word_font']}\n" not in s:
        # the trend word's size, where the square panel has no font of it (the 800 x 480 layout's 36 px)
        size = layout["trend_word_font"].split("_")[1]
        s = one(
            s,
            """  - file: "gfonts://Roboto"
    id: roboto_56
""",
            f"""  # the trend word in the TREND column ({size} px; trend_word_font)
  - file: "gfonts://Roboto"
    id: {layout["trend_word_font"]}
    size: {size}
    bpp: 4
    glyphs: "${{glyphs_heading}}"
  - file: "gfonts://Roboto"
    id: roboto_56
""",
        )
    s = one(
        s,
        "    id: trend_arrow_img\n    resize: 80x80\n",
        fill("    id: trend_arrow_img\n    resize: @arrow@x@arrow@\n", values),
    )
    # the About page's icon in the layout's size
    s = one(
        s,
        "    file: images/glucocraft_icon_96.png\n",
        fill("    file: images/glucocraft_icon_@about_icon@.png\n", values),
    )
    s = one(
        s,
        "(images/glucocraft_icon_<px>.png; 96 px here, 2 bytes a pixel = 18.4 kB)",
        f"(images/glucocraft_icon_<px>.png; {layout['about_icon']} px here,"
        f" 2 bytes a pixel = {layout['about_icon'] ** 2 * 2 / 1000:.1f} kB)",
    )
    if layout.get("delta_big"):
        # the 5-minute delta's two sizes (3 characters / 4 characters) for this layout
        s = one(
            s,
            "? id(roboto_40)->get_lv_font() : id(roboto_64)->get_lv_font(), 0);",
            f"? id({layout['delta_small']})->get_lv_font() : id({layout['delta_big']})->get_lv_font(), 0);",
        )
    if layout.get("word_big"):
        # the zone word under the value: its two sizes (the smaller where the larger is too wide) for this layout
        s = one(
            s,
            "id(roboto_32)->get_lv_font(), id(roboto_40)->get_lv_font()), 0);",
            f"id({layout['word_small']})->get_lv_font(), id({layout['word_big']})->get_lv_font()), 0);",
        )
    # the GMI cells: the layout's statistics font, and the smaller one for both A1C units
    s = one(
        s,
        "gcu::a1c_mode() == 2 ? id(roboto_16)->get_lv_font() : id(roboto_20)->get_lv_font()",
        f"gcu::a1c_mode() == 2 ? id({layout['gmi_both_font']})->get_lv_font()"
        f" : id({layout['cell_val_font']})->get_lv_font()",
    )
    if board.get("lvgl_rotation"):
        s = one(
            s,
            "lvgl:\n  displays:\n",
            f"lvgl:\n  rotation: {board['lvgl_rotation']}"
            "   # landscape in software (the controller cannot swap axes)\n  displays:\n",
        )
    return s


def top_layer(s: str, layout: dict, values: dict) -> str:
    """Replace the square top layer with the landscape one, followed by the About page and the start-up screen."""
    a = s.index("  top_layer:\n")
    b = s.index("\n  pages:\n", a)
    top = TOP_LAYER.read_text(encoding="utf-8")
    # the start-up screen (the icon, the name, the version and the disclaimer line): the square panel's block, kept as
    # the top layer's last child (above everything), with the layout's fonts
    sa = s.index("      # >>> start-up screen", a)
    sb = s.index("      # <<< start-up screen\n", sa) + len("      # <<< start-up screen\n")
    check(sb <= b + 1, "the start-up screen block does not end at the line before '  pages:'")
    splash = s[sa:sb]
    for old, new, n in (
        (
            "text_font: roboto_32   # splash heading font",
            f"text_font: {layout['splash_head_font']}   # splash heading font",
            1,
        ),
        ("text_font: roboto_24   # splash text font", f"text_font: {layout['splash_font']}   # splash text font", 1),
        (
            "text_font: roboto_22   # splash version font",
            f"text_font: {layout['splash_ver_font']}   # splash version font",
            1,
        ),
        ("width: 90%   # splash width", f"width: {layout['splash_w']}   # splash width", 1),
        ("pad_row: 24   # splash gap", f"pad_row: {layout['splash_gap']}   # splash gap", 1),
    ):
        splash = one(splash, old, new, n)
    # the full-screen About page: the square panel's about_screen block, just below the start-up screen, replaced by
    # the wide layout (tools/landscape_about.yaml) with the layout's sizes
    aa = s.index("      # >>> About screen", a)
    ab = s.index("      # <<< About screen\n", aa) + len("      # <<< About screen\n")
    check(ab <= sa, "the About screen block does not come before the start-up screen")
    check('text: "${t_about_disclaimer}"' in s[aa:ab], "the square About screen does not show ${t_about_disclaimer}")
    about = fill(ABOUT_WIDE.read_text(encoding="utf-8"), values)
    return s[:a] + fill(top, values).rstrip("\n") + "\n" + about.rstrip("\n") + "\n\n" + splash.rstrip("\n") + s[b:]


def no_reading_split(s: str, layout: dict) -> str:
    """Put the last reading's time and age on one line where the layout has the room (nr_split False)."""
    if layout["nr_split"]:
        return s
    return one(s, "constexpr bool SPLIT_AGE = true;", "constexpr bool SPLIT_AGE = false;")


def stat_id(period: str, key: str) -> str:
    """Return the id of a statistics label, for example stat_24h_tir."""
    return f"stat_{period}_{key}"


def cell(label: str, sid: str, values: dict) -> str:
    """Return one statistics cell: the label (a text of the catalogue, ${t_stat_<label>}) above, the value below."""
    return fill(
        f"""                    - obj:
                        width: @cell_w@
                        height: @cell_h@
                        clickable: false
                        scrollable: false
                        bg_opa: TRANSP
                        border_width: 0
                        pad_all: 0
                        layout:
                          type: flex
                          pad_row: 2
                          flex_flow: COLUMN
                          flex_align_main: CENTER
                          flex_align_cross: CENTER
                          flex_align_track: CENTER
                        widgets:
                          - label:
                              text_font: @cell_lbl_font@
                              styles: theme_text_label
                              text: "${{t_stat_{label}}}"
                          - label:
                              id: {sid}
                              text_font: @cell_val_font@
                              styles: theme_text
                              text: "---"
""",
        values,
    )


def row(rid: str, y: int, cells: list, values: dict) -> str:
    """Return one framed row of statistics cells at height y."""
    return fill(
        f"""              - obj:
                  id: {rid}
                  x: @cv_x@
                  y: {y}
                  width: @CW@
                  height: @row_h@
                  clickable: false
                  scrollable: false
                  styles: theme_box
                  border_width: 1
                  radius: 4
                  pad_all: 1
                  layout:
                    type: flex
                    pad_column: 0   # {len(cells)} cells of @cell_w@ px fill the row
                    flex_flow: ROW
                    flex_align_main: START
                    flex_align_cross: CENTER
                    flex_align_track: CENTER
                  widgets:
""",
        values,
    ) + "".join(cell(label, sid, values) for label, sid in cells)


def stats_rows(period: str, gmi: str, layout: dict, values: dict) -> str:
    """Return the twelve statistics in rows of stats_cols (6 = two rows, 4 = three rows), the same order everywhere."""
    cells = [
        ("tir", "tir"),
        ("titr", "titr"),
        ("low", "low"),
        ("high", "high"),
        ("very_low", "crit_low"),
        ("very_high", "crit_high"),
        ("mean", "mean"),
        ("sd", "std_dev"),
        ("cv", "cv"),
        (gmi, "gmi"),
        ("min", "min"),
        ("max", "max"),
    ]
    cells = [(label, stat_id(period, key)) for label, key in cells]
    cols = layout.get("stats_cols", 6)
    ys = layout.get("row_ys") or (layout["row1_y"], layout["row2_y"])
    ids = (
        [f"stats_left_a_{period}", f"stats_row_{period}"]
        if cols == 6
        else [f"stats_left_a_{period}", f"stats_left_b_{period}", f"stats_row_{period}"]
    )
    return "".join(row(ids[i], ys[i], cells[i * cols : (i + 1) * cols], values) for i in range(len(ids)))


def graph_pages(s: str, layout: dict, values: dict) -> str:
    """Rebuild each graph page's widgets: the canvas and the statistics on the right side."""
    for pid, period, gid, cid, gmi in PAGES:
        a = s.index(f"    - id: {pid}\n")
        w = s.index("      widgets:\n", a)
        end = s.index("\n    - id: ", w)
        body = fill(
            f"""      widgets:
        - obj:
            id: {gid}
            hidden: {"true" if pid == "page_6h" else "false"}
            x: @RX@
            y: @HDR@
            width: @RW@
            height: @BH@
            clickable: false
            styles: theme_background
            border_width: 0
            pad_all: 0
            radius: 0
            widgets:
              - canvas:
                  id: {cid}
                  clickable: false
                  x: @cv_x@
                  y: @cv_y@
                  width: @CW@
                  height: @CH@
""",
            values,
        ) + stats_rows(period, gmi, layout, values)
        s = s[:w] + body.rstrip("\n") + s[end:]
    check(
        s.count("id: graph_container\n            hidden: true") == 1, "the 6-hour graph container is not hidden once"
    )
    return s


def instant_page_changes(s: str) -> str:
    """Switch pages without an animation.

    The boards cannot redraw 800 x 480 or 1024 x 600 fast enough during an animation, so a slide looks ragged; the
    display card also switches pages instantly. The square panel already does; this stays as a guard.
    """
    s = re.sub(r"animation: (?:MOVE_LEFT|MOVE_RIGHT|FADE_IN)(\n\s+)time: \d+ms", r"animation: NONE\1time: 0ms", s)
    check(not re.search(r"animation: (MOVE_|FADE)", s), "a page animation is left")
    return s


def drop_unused_fonts(s: str) -> str:
    """Leave out the fonts this layout never uses.

    The square panel's fonts that a layout does not use (for example its 120 px glucose value font) would still be
    built into the firmware. A font block is left out when its id appears nowhere else in the file.
    """
    fa = s.index("\nfont:\n") + 1
    fb = s.index("\nlvgl:\n", fa) + 1
    blocks = re.split(r"(?m)^(?=  - file: )", s[fa:fb])
    kept, dropped = [blocks[0]], []
    rest = s[:fa] + s[fb:]
    for blk in blocks[1:]:
        m = re.search(r"^    id: (\w+)$", blk, re.MULTILINE)
        fid = m.group(1) if m else None
        if fid and not re.search(r"\b" + fid + r"\b", rest):
            dropped.append(fid)
            continue
        kept.append(blk)
    print("fonts left out (not used by this layout):", ", ".join(dropped) or "none")
    return s[:fa] + "".join(kept) + s[fb:]


def check_leftovers(s: str, layout: dict) -> None:
    """Stop when anything of the square layout is left: its sizes, its display platform, its weekly mini value."""
    leftovers = ["width: 340", "y: 286", "y: 291", "st7701s", "weekly_mini", "trend_arrow_mini_img"]
    if layout["W"] != 480:
        leftovers.append("width: 480")
    for bad in leftovers:
        check(bad not in s, f"left from the square layout: {bad!r}")
    # an unfilled @key@ placeholder in the LVGL section (up to the first "interval:")
    check("@" not in s.split("lvgl:")[1].split("interval:")[0], "an unfilled @key@ placeholder is left")


def generate(board: dict) -> str:
    """Return the board's configuration, generated from the square panel."""
    layout = LAYOUTS[board["size"]]
    values = layout_values(layout)
    s = SRC.read_text(encoding="utf-8")
    s = header(s, board, values)
    s = identity(s, board)
    s = hardware(s, board)
    s = canvas_sizes(s, layout)
    s = weekly_pages_keep_glucose_block(s)
    s = remove_weekly_mini_value(s)
    s = fonts_and_images(s, board, layout, values)
    s = top_layer(s, layout, values)
    s = no_reading_split(s, layout)
    s = graph_pages(s, layout, values)
    s = instant_page_changes(s)
    s = drop_unused_fonts(s)
    check_leftovers(s, layout)
    return s


def main(argv: list[str] | None = None) -> None:
    """Generate one board's configuration (into its file, or into --out)."""
    parser = argparse.ArgumentParser(description="Generate a landscape GlucoCraft panel from glucocraft-panel.yaml.")
    parser.add_argument("board", choices=sorted(BOARDS))
    parser.add_argument("--out", type=Path, help="write here instead of the board's file")
    args = parser.parse_args(argv)
    board = BOARDS[args.board]
    dst = args.out or PANELS / board["file"]
    s = generate(board)
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)
    print("written", dst, len(s.splitlines()), "lines")


if __name__ == "__main__":
    main()
