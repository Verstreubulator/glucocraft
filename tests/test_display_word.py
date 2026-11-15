# License: AGPL-3.0-only (LICENSE).
"""The zone word under the glucose value and the setting "Trend shown as" (trend_display), with their attributes.

Rules under test:
- Every display shows the zone word under the glucose value, in every zone and in every setting: the Last Glucose
  Reading sensor publishes it as display_word; zone_label (outside the range only) stays as it is.
- The setting "Trend shown as" is "arrow" (default, also for existing entries) or "word"; it is offered next to the
  display colors, with texts for both choices, and published as the Configuration attribute trend_display. A stored
  display_word key is no setting: its value is read by nothing.
- The trend word is the trend category in sentence case, published as the Trend arrow sensor's attribute word.
- Without current data display_word is empty; while the trend is unknown the trend word is empty.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
from glucocraft import const as C
from glucocraft import flow_schemas as fs
from glucocraft import sensor as se
from glucocraft.settings import ENTRY_KEYS, setting

from test_trend_and_configuration import settings_coordinator

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "glucocraft"
STRINGS = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))


def _current(c, value, status="current", trend="Changing Slowly"):
    c.current_glucose, c.current_limit = value, None
    c.last_reading_ts, c.no_current_data = int(time.time()) - 60, status != "current"
    c._trend_description = trend
    return c


def _attributes(entity_class, c):
    e = entity_class.__new__(entity_class)
    e.coordinator = c
    return e.extra_state_attributes


def test_default_and_stored_values():
    assert setting({}, C.CONF_TREND_DISPLAY) == "arrow"
    assert setting({"trend_display": "word"}, C.CONF_TREND_DISPLAY) == "word"
    assert setting({"trend_display": "trend"}, C.CONF_TREND_DISPLAY) == "arrow"
    assert "self._data.setdefault(CONF_TREND_DISPLAY, TREND_DISPLAY_ARROW)" in (PKG / "config_flow.py").read_text(
        encoding="utf-8"
    )
    # display_word is no setting: a stored value is read by nothing
    assert "display_word" not in ENTRY_KEYS and not hasattr(C, "CONF_DISPLAY_WORD")


@pytest.mark.parametrize(
    "value, word",
    [
        (48, "Very low"),
        (60, "Low"),
        (69, "Low"),
        (70, "Tight range"),
        (140, "Tight range"),
        (160, "In range"),
        (181, "High"),
        (260, "Very high"),
    ],
)
@pytest.mark.parametrize("stored", [{}, {"trend_display": "word"}, {"display_word": "trend"}])
def test_zone_word_in_every_setting(tmp_path, value, word, stored):
    c = _current(settings_coordinator(tmp_path, stored), value)
    assert c.display_word == word


@pytest.mark.parametrize("limit, value, zone", [("low", 40, "very_low"), ("high", 400, "very_high")])
def test_limit_reading_says_sensor_limit(tmp_path, limit, value, zone):
    """LO / HI: the word comes from the catalogue; the zone stays what automations and statistics use."""
    c = _current(settings_coordinator(tmp_path), value)
    c.current_limit = limit
    assert c.display_word == "Sensor limit" and c.glucose_zone == zone


def test_near_low_is_in_range(tmp_path):
    c = _current(settings_coordinator(tmp_path), 75)
    c.range_ideal_low = 80
    assert c.glucose_zone == "near_low" and c.display_word == "In range"


@pytest.mark.parametrize(
    "trend, word",
    [
        ("Rising Quickly", "Rising quickly"),
        ("Rising", "Rising"),
        ("Changing Slowly", "Changing slowly"),
        ("Falling", "Falling"),
        ("Falling Quickly", "Falling quickly"),
        ("Unknown", ""),
    ],
)
def test_trend_word(tmp_path, trend, word):
    c = _current(settings_coordinator(tmp_path, {"trend_display": "word"}), 120, trend=trend)
    assert c.trend_word == word
    a = _attributes(se.GlucoCraftTrendArrow, c)
    assert (a["description"], a["word"]) == (trend, word)


def test_empty_without_current_data(tmp_path):
    c = _current(settings_coordinator(tmp_path), 48, status="no_current_data")
    assert c.display_word == "" and c.glucose_zone_label == "Very low"  # zone_label stays as it is
    assert c.trend_word == ""  # the trend is unknown without current data
    c.current_glucose = None
    assert c.display_word == ""


def test_published_on_the_last_reading_and_the_configuration(tmp_path):
    c = _current(settings_coordinator(tmp_path), 200)
    a = _attributes(se.GlucoCraftCurrentGlucose, c)
    assert (a["display_word"], a["zone_label"]) == ("High", "High") and "display_word_mode" not in a
    assert c.configuration()["trend_display"] == "arrow" and "display_word_mode" not in c.configuration()
    c = _current(settings_coordinator(tmp_path, {"trend_display": "word"}), 200)
    assert c.configuration()["trend_display"] == "word"


def test_offered_next_to_the_colors_with_texts():
    schema_src = (PKG / "flow_schemas.py").read_text(encoding="utf-8")  # the forms
    start = schema_src.index("def _displays_schema(")
    colors = schema_src[start : schema_src.index("\ndef ", start + 1)]
    assert "CONF_TREND_DISPLAY" in colors and "translation_key=CONF_TREND_DISPLAY" in colors
    step = STRINGS["options"]["step"]["displays"]  # Configure > Display card and wall panels
    assert step["data"]["trend_display"] == "Trend shown as" and "display_word" not in step["data"]
    note = step["data_description"]["trend_display"]
    # the arrow or the trend word, the broader category of the same rate; the zone word stays under the value
    assert "the broader category of the same rate" in note and "The zone word is shown under the glucose value" in note
    assert "shows the trend as the arrow with either choice" in note and "Default: Arrow." in note
    options = STRINGS["selector"]["trend_display"]["options"]
    assert set(options) == set(C.TREND_DISPLAY_OPTIONS) and "display_word" not in STRINGS["selector"]
    assert options["arrow"].startswith("Arrow (default)") and options["word"].startswith("Word:")
    assert "the broader category of the same rate" in options["word"]
    for text in (*options.values(), step["description"], note):
        assert not re.search(r"\b\w+n't\b", text)  # no contractions
        assert "by color only" not in text  # the zone word is shown in every setting
    assert set(C.TREND_DISPLAY_OPTIONS) == set(fs.TREND_DISPLAY_OPTIONS)
