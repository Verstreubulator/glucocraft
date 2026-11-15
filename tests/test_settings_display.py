# License: AGPL-3.0-only (LICENSE).
"""The display settings: the display unit, the display card, how the trend is shown, the seven zone colors
and their reset, the weekly pages' range set, and the no-current-data limit.

Each value is checked on every output that uses it: the entities (state, unit, attributes), the Configuration
attributes (the dashboard's and the cards' input), the panel feed (GLU2 with the GCX1 extension), the HTTP API, the
summaries, the CGM Report data and the CSV export.
"""

from __future__ import annotations

import re
import time
import types
from pathlib import Path

import pytest
from glucocraft import const as C
from glucocraft import coordinator as co
from glucocraft import panel_notice as pn
from glucocraft import unit_registry as ur
from glucocraft.units import from_mgdl

from conftest import FakeRegistry
from test_settings_harness import (
    ENTRY_ID,
    build,
    entry,
    fingerprint,
    series,
    state,
    tz_patch,  # noqa: F401 (the fixture)
)

pytestmark = pytest.mark.usefixtures("tz_patch")
# the ranges in the settings are in use only while the optional feature Custom ranges and goals is on
CUSTOM_ON = {C.CONF_CUSTOM_RANGES_AND_GOALS: True}

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "glucocraft"
GLUCOSE_SENSORS = (
    "current_glucose",
    "delta_5min",
    *(f"{p}_{k}" for p in ("6h", "14d", "180d") for k in ("mean", "std_dev", "min", "max")),
)


# ------------------------------------------------------------------------------------------------ the display unit
@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
def test_the_display_unit_on_every_output(tmp_path, unit):
    run = build(tmp_path, entry(unit=unit))
    c = run.c
    mm = unit == "mmol/L"
    # the entities: the glucose sensors report mg/dL and suggest no unit (Home Assistant converts to the unit of the
    # entity settings, which setup writes: unit_registry.keep_display_unit), so whole mg/dL is one decimal in mmol/L
    for key in GLUCOSE_SENSORS:
        assert run.sensor(key)["suggested_unit"] is None, key
    assert run.sensor("current_glucose")["attributes"]["unit"] == unit
    assert run.sensor("current_glucose")["attributes"]["value_mgdl"] == c.current_glucose
    # the slope stays inside the coordinator: the trend arrow publishes no rate as a number
    assert "rate_per_min" not in run.sensor("trend_arrow")["attributes"]
    assert run.sensor("delta_5min")["attributes"]["unit"] == unit
    for key, limit in (("is_low", 70), ("is_high", 180), ("is_critical_low", 54), ("is_critical_high", 250)):
        attrs = run.sensor(key)["attributes"]
        assert attrs["unit"] == unit and attrs["threshold"] == from_mgdl(limit, unit)
        assert "unit_of_measurement" not in attrs  # Home Assistant's own key for an entity's unit; On / Off has none
    # the dashboard's input
    assert run.config()["unit"] == unit
    # the HTTP API: values in the display unit, unrounded (mmol/L: mg/dL / 18.016, the ranges as well)
    api = run.api_data("24h")
    factor = 18.016 if mm else 1
    assert api["unit"] == unit and api["current"] == c.current_glucose / factor
    assert api["ranges"]["low"] == 70 / factor and api["ranges"]["very_high"] == 250 / factor
    assert api["stats"]["mean"] == c.period_stats["24h"].mean / factor
    assert run.api_stats()["unit"] == unit
    # the panel feed: values stay mg/dL; the weekly pages' lines name the display unit
    feed = run.feed()
    assert feed["ranges"] == (54.0, 250.0, 70.0, 180.0, 70.0, 140.0)
    desc = feed["ext"]["standards"][0]["desc"][0]
    assert desc.startswith("3.9-10.0 mmol/L." if mm else "70-180 mg/dL.")
    # the summaries name the unit in words
    words = "millimoles per liter" if mm else "milligrams per deciliter"
    assert f"(glucose in {words})" in run.daily()
    # the report and the CSV
    assert run.report(14)["unit"] == unit
    last = run.csv().strip().splitlines()[-1].split(",")
    assert last[4] == unit and last[2] == f"{c.current_glucose:.1f}"
    assert last[3] == (f"{from_mgdl(c.current_glucose, unit):.1f}" if mm else f"{round(c.current_glucose):.0f}")


def test_the_api_has_no_smoothed_value_and_no_slope(tmp_path):
    """The smoothed level and its slope stay inside the coordinator (no second glucose value, no rate as a number)."""
    now = int(time.time())
    run = build(tmp_path, entry(unit="mmol/L"), readings=series(now, 1, value=lambda i: 80 + i * 1.0))
    data = run.api_data("6h")
    assert "smoothed" not in data and "delta" not in data and data["trend"] == run.c.trend_description


def test_changing_the_unit_with_history_changes_only_what_is_shown(tmp_path):
    """The stored readings and every calculation stay mg/dL; switching back gives exactly the outputs before."""
    now = int(time.time())
    rows = series(now, 15)
    mg = build(tmp_path / "a", entry(unit="mg/dL"), readings=rows, now=now)
    mm = build(tmp_path / "b", entry(unit="mmol/L"), readings=rows, now=now)
    back = build(tmp_path / "c", entry(unit="mg/dL"), readings=rows, now=now)
    assert [(r.timestamp, r.raw) for r in mg.c.storage.readings] == [
        (r.timestamp, r.raw) for r in mm.c.storage.readings
    ]
    assert mg.c.period_stats == mm.c.period_stats and mg.c.trend_weeks == mm.c.trend_weeks
    fa, fb = mg.feed(), mm.feed()
    assert fa["readings"] == fb["readings"] and fa["stats"] == fb["stats"] and fa["trends"] == fb["trends"]
    assert [r.split(",")[:3] for r in mg.csv().splitlines()] == [r.split(",")[:3] for r in mm.csv().splitlines()]
    assert fingerprint(back) == fingerprint(mg)


LAST_GLUCOSE_READING = "sensor.glucocraft_last_glucose_reading"
RANGE_LIMITS = {"is_low": 70, "is_high": 180, "is_critical_low": 54, "is_critical_high": 250}  # the defaults, mg/dL


def _range_sensors(run) -> dict:
    """The state, threshold and unit of each range binary sensor, as they would be written now."""
    out = {}
    for key in RANGE_LIMITS:
        s = run.sensor(key)
        out[key] = (s["state"], s["attributes"]["threshold"], s["attributes"]["unit"])
    return out


def _range_sensors_at_60(unit: str) -> dict:
    """The range binary sensors for a current reading of 60 mg/dL: below range only."""
    return {k: (k == "is_low", from_mgdl(limit, unit), unit) for k, limit in RANGE_LIMITS.items()}


@pytest.mark.parametrize(("old", "new"), [("mg/dL", "mmol/L"), ("mmol/L", "mg/dL")])
def test_the_range_sensors_follow_a_unit_change_at_once(tmp_path, monkeypatch, old, new):
    """A unit change reaches the range binary sensors' threshold and unit at once, from the stored current value,
    without waiting for a new reading: after an options save, whose reload sets up the binary sensors before Last
    Glucose Reading writes its state in the new unit, and after a unit chosen in Last glucose reading's own entity
    settings (no reload)."""
    reg = FakeRegistry(
        {
            LAST_GLUCOSE_READING: {
                "unique_id": f"{ENTRY_ID}_current_glucose",
                "options": {"sensor": {"unit_of_measurement": old}},
            }
        }
    )
    monkeypatch.setattr(ur, "er", types.SimpleNamespace(async_get=lambda hass: reg))
    monkeypatch.setattr(co, "er", types.SimpleNamespace(async_get=lambda hass: reg))
    now = int(time.time())
    run = build(
        tmp_path,
        entry(unit=old),
        readings=series(now, 1, value=lambda i: 60.0),
        states={LAST_GLUCOSE_READING: state(60, unit_of_measurement=old)},
    )
    assert _range_sensors(run) == _range_sensors_at_60(old)

    # Configure -> Units and formats: the entity settings get the new unit (config_flow._async_finish), then the reload;
    # Last glucose reading's state still carries the earlier unit, and no reading arrives
    ur.apply_display_unit(run.hass, ENTRY_ID, new)
    after = run.reload(entry(unit=new))
    assert after.hass.states.get(LAST_GLUCOSE_READING).attributes["unit_of_measurement"] == old
    assert after.c.storage.readings[-1].timestamp == run.c.storage.readings[-1].timestamp
    assert _range_sensors(after) == _range_sensors_at_60(new)

    # the earlier unit chosen in Last glucose reading's entity settings: the states are written again at once, in it
    written = []
    monkeypatch.setattr(after.c, "async_update_listeners", lambda: written.append(_range_sensors(after)))
    after.hass.config_entries = types.SimpleNamespace(async_get_entry=lambda entry_id: None)
    reg.async_update_entity_options(LAST_GLUCOSE_READING, "sensor", {"unit_of_measurement": old})
    after.c._async_unit_setting_changed(types.SimpleNamespace(data={"action": "update", "changes": {"options": {}}}))
    assert written == [_range_sensors_at_60(old)]


@pytest.mark.parametrize(
    "data, expected",
    [
        ({}, "consensus ranges"),
        ({C.CONF_RANGE_LOW: 65}, "consensus ranges"),  # stored, but not in use while the feature is off
        ({**CUSTOM_ON, C.CONF_RANGE_LOW: 65}, "custom ranges"),
        (CUSTOM_ON, "consensus ranges"),  # the feature on, the ranges equal to the consensus ranges
    ],
)
def test_the_limit_source_names_the_range_set_in_use(tmp_path, data, expected):
    """limit_source of the four limit conditions: the range set whose limit is the threshold, never stored values
    that are not in use."""
    run = build(tmp_path, entry(**data))
    for key in ("is_low", "is_high", "is_critical_low", "is_critical_high"):
        assert run.sensor(key)["attributes"]["limit_source"] == expected, key
    assert run.sensor("is_low")["attributes"]["threshold"] == (65 if expected == "custom ranges" else 70)


# ------------------------------------------------------------------------------------------------ the display card
# "unknown_layout": a stored value that is not one of the options reads as the default (square)
@pytest.mark.parametrize("card", [None, "square", "rectangular", "unknown_layout"])
@pytest.mark.parametrize("model", ["panel", "waveshare-lcd7-800x480", "waveshare-p4-7b-1024x600", "guition-35-480x320"])
def test_the_display_card(tmp_path, monkeypatch, card, model):
    data = entry(**({C.CONF_DISPLAY_CARD: card} if card else {}))
    shown = card if card in (C.DISPLAY_CARD_SQUARE, C.DISPLAY_CARD_RECTANGULAR) else "square"
    assert build(tmp_path, data).config()["display_card"] == shown
    # the notice for a new wall panel names the card the dashboard shows and whether it matches the panel
    notes = []
    monkeypatch.setattr(pn.persistent_notification, "async_create", lambda hass, msg, **kw: notes.append(msg))
    devices = {}
    monkeypatch.setattr(pn, "_panels", lambda hass: dict(devices))
    hass = types.SimpleNamespace(
        bus=types.SimpleNamespace(async_listen=lambda *a: lambda: None), async_create_task=lambda coro: coro.close()
    )
    ent = types.SimpleNamespace(entry_id="t", data=data, async_on_unload=lambda f: None)
    import asyncio

    asyncio.run(pn.async_setup_panel_notice(hass, ent))  # the first run records the panels already there
    devices["d1"] = types.SimpleNamespace(id="d1", model=model, name="Panel", name_by_user=None)
    ent_unload = []
    ent.async_on_unload = ent_unload.append
    asyncio.run(pn.async_setup_panel_notice(hass, ent))
    assert len(notes) == 1
    text = {"square": "square display card", "rectangular": "rectangular display card"}
    assert f"GlucoCraft's dashboard is set to show the {text[shown]}" in notes[0]
    panel_shape = "square" if model == "panel" else "rectangular"
    # the rectangular card has the 800 x 480 layout: close to the other rectangular sizes
    close = panel_shape == "rectangular" and "800x480" not in model
    assert ("which matches the shape" in notes[0]) is (panel_shape == shown and not close)
    assert ("which is close to the layout" in notes[0]) is (panel_shape == shown and close)
    assert ("which does not match the shape" in notes[0]) is (panel_shape != shown)


def test_every_display_card_has_a_card_on_the_dashboard():
    """The dashboard maps the attribute display_card to a card element (glucocraft-ui.js _tagFor)."""
    js = (PKG / "www" / "glucocraft-ui.js").read_text(encoding="utf-8")
    m = re.search(r"_tagFor\(kind\) \{\s*return \{([^}]*)\}\[kind\]", js)
    assert m
    keys = set(re.findall(r"(\w+):", m.group(1)))
    assert keys == {C.DISPLAY_CARD_SQUARE, C.DISPLAY_CARD_RECTANGULAR}


# ------------------------------------------------------------------------------------------------ the zone word and
# the trend shown as arrow or word
ZONE_CASES = [
    (48, "very_low", "Very low", "Very low"),
    (60, "low", "Low", "Low"),
    (75, "near_low", None, "In range"),
    (120, "tight_range", None, "Tight range"),
    (160, "near_high", None, "In range"),
    (200, "high", "High", "High"),
    (300, "very_high", "Very high", "Very high"),
]


def _current_at(run, mg, trend="Changing Slowly"):
    c = run.c
    c.current_glucose, c.current_limit = float(mg), None
    c.last_reading_ts, c.no_current_data = int(time.time()) - 60, False
    c._trend_description, c._trend_arrow = trend, "→"
    return run.sensor("current_glucose")["attributes"]


@pytest.mark.parametrize("shown", [None, "arrow", "word"])
@pytest.mark.parametrize("mg,zone,label,word", ZONE_CASES)
def test_the_zone_word_and_the_trend_word(tmp_path, shown, mg, zone, label, word):
    # the tight range from 80: a near-low zone exists (70-79); no stored choice = the default, the arrow
    run = build(
        tmp_path, entry(**CUSTOM_ON, **{C.CONF_RANGE_TIGHT_LOW: 80, C.CONF_TREND_DISPLAY: shown if shown else ...})
    )
    a = _current_at(run, mg, "Rising Quickly")
    assert (a["zone"], a["zone_label"]) == (zone, label)
    assert run.config()["trend_display"] == (shown or "arrow")
    # the zone word under the value with either choice; the trend word for the TREND column on the Trend arrow sensor
    assert a["display_word"] == word and "display_word_mode" not in a
    assert run.sensor("trend_arrow")["attributes"]["word"] == "Rising quickly"
    # the zone label (the word of a zone outside the range) is the same with either choice
    run.c.no_current_data = True
    a = run.sensor("current_glucose")["attributes"]
    assert a["display_word"] == "" and a["zone_label"] == label


def test_the_trend_word_is_empty_while_the_trend_is_unknown(tmp_path):
    run = build(tmp_path, entry(trend_display="word"))
    _current_at(run, 120, "Unknown")
    assert run.sensor("trend_arrow")["attributes"]["word"] == ""
    _current_at(run, 120, "Falling")
    assert run.sensor("trend_arrow")["attributes"]["word"] == "Falling"


# ------------------------------------------------------------------------------------------------ zone colors
@pytest.mark.parametrize("zone", C.ZONES)
def test_one_zone_color(tmp_path, zone):
    run = build(tmp_path, entry(**CUSTOM_ON, **{C.CONF_ZONE_COLORS[zone]: "#12ab3c", C.CONF_RANGE_TIGHT_LOW: 80}))
    colors = run.config()["zone_colors"]
    assert colors[zone] == "#12AB3C"  # upper case for every consumer
    assert all(colors[z] == C.ZONE_COLORS[z].upper() for z in C.ZONES if z != zone)
    mg = next(m for m, z, _, _ in ZONE_CASES if z == zone)
    assert _current_at(run, mg)["zone_color"] == "#12AB3C"


def test_the_reset_restores_the_default_palette(tmp_path):
    """The reset stores no color (the keys are removed); an entry without colors uses the default palette."""
    custom = entry(**{C.CONF_ZONE_COLORS[z]: "#000000" for z in C.ZONES})
    reset = {k: v for k, v in custom.items() if k not in C.CONF_ZONE_COLORS.values()}
    assert build(tmp_path / "a", custom).config()["zone_colors"] == dict.fromkeys(C.ZONES, "#000000")
    assert build(tmp_path / "b", reset).config()["zone_colors"] == {z: c.upper() for z, c in C.ZONE_COLORS.items()}
    # a color stored as None (a zone without a color of its own) is the default too
    none = entry(**{C.CONF_ZONE_COLORS["low"]: None})
    assert build(tmp_path / "c", none).config()["zone_colors"]["low"] == C.ZONE_COLORS["low"].upper()


@pytest.mark.parametrize("color", ["#FFFFFF", "#000000", "#C62828"])
def test_colors_equal_to_each_other_and_to_the_background(tmp_path, color):
    """Every zone the same color (white or black = the background of the light and dark themes): the colors are the
    user's choice and are published as chosen; the zone word stays, so that color is never the only cue."""
    colors = {C.CONF_ZONE_COLORS[z]: color for z in C.ZONES}
    run = build(tmp_path, entry(**CUSTOM_ON, **colors, **{C.CONF_RANGE_TIGHT_LOW: 80}))
    assert set(run.config()["zone_colors"].values()) == {color}
    words = set()
    for mg, _zone, label, word in ZONE_CASES:
        a = _current_at(run, mg)
        assert a["zone_color"] == color and a["zone_label"] == label and a["display_word"] == word
        words.add(a["display_word"])
    assert len(words) == 6  # six different words for seven zones (both in-range sides: "In range")


def test_colors_never_change_the_data(tmp_path):
    now = int(time.time())
    a = build(tmp_path / "a", now=now)
    b = build(tmp_path / "b", entry(**{C.CONF_ZONE_COLORS[z]: "#123456" for z in C.ZONES}), now=now)
    assert a.feed_bytes() == b.feed_bytes() and a.api_stats() == b.api_stats() and a.daily() == b.daily()


# ------------------------------------------------------------------------------------------------ weekly pages
# a set the settings screen accepts: the tight range starts at the low limit or above it
CUSTOM_RANGES = {C.CONF_RANGE_LOW: 80, C.CONF_RANGE_TIGHT_LOW: 80, C.CONF_RANGE_HIGH: 160}
CUSTOM_GOALS = {C.CONF_GOAL_TIME_IN_TARGET_RANGE: 80, C.CONF_GOAL_GMI: 6.8}


@pytest.mark.parametrize("pages", [None, "consensus", "personal"])
@pytest.mark.parametrize("custom", ["none", "ranges", "goals", "both"])
@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
def test_the_weekly_pages_range_set(tmp_path, pages, custom, unit):
    extra = {
        "none": {},
        "ranges": CUSTOM_RANGES,
        "goals": CUSTOM_GOALS,
        "both": {**CUSTOM_RANGES, **CUSTOM_GOALS},
    }[custom]
    data = entry(unit=unit, **CUSTOM_ON, **extra, **({C.CONF_WEEKLY_PAGES: pages} if pages else {}))
    run = build(tmp_path, data, days=40)
    offered = custom != "none"
    shown_custom = pages == "personal" and offered
    wp = run.config()["weekly_pages"]
    assert wp == {
        "setting": {"personal": "custom", None: "consensus"}.get(pages, pages),
        "offered": offered,
        "shown": "custom" if shown_custom else "consensus",
    }
    assert run.c.weekly_pages_custom is shown_custom
    feed = run.feed()
    stds = feed["ext"]["standards"]
    assert [s["kind"] for s in stds] == ([1, 0] if shown_custom else ([0, 1] if offered else [0]))
    # the weeks in TRENDS are those of the standard shown
    weeks = run.c.personal_trend_weeks if shown_custom else run.c.trend_weeks
    # (record: start, end, mean, SD, CV, GMI, time in range, ...)
    sent = [round(t[6], 3) for t in feed["trends"]]
    assert sent == [round(s.pct_time_in_target_range, 3) if s else 0.0 for s in weeks[: len(feed["trends"])]]
    assert len(feed["gcl2"]["lo"]) == len(feed["gcl2"]["hi"]) == 7  # the time at the limit of every period
    # the custom standard's targets are the goals in the settings; the consensus one's the cited targets
    for s in stds:
        tir_op, tir_value = s["targets"][0]
        if s["kind"] == 1:
            assert tir_value == data[C.CONF_GOAL_TIME_IN_TARGET_RANGE]
            assert s["desc"][0].endswith(f"Custom goal >{int(data[C.CONF_GOAL_TIME_IN_TARGET_RANGE])}%")
        else:
            assert (tir_op, tir_value) == (1, 70.0) and "Consensus >70%" in s["desc"][0]
        for d in s["desc"]:
            assert len(d.encode("utf-8")) < 64, d  # never cut: the panel's 64-byte field (UTF-8, "≤" is 3 bytes)
    # the delta file carries no weekly pages
    assert run.feed(True)["ext"] is None


# ------------------------------------------------------------------------------------------------ no current data
@pytest.mark.parametrize("minutes", [5, 15, 30])
@pytest.mark.parametrize("offset", [-30, 30])
def test_the_no_current_data_limit(tmp_path, minutes, offset):
    """A reading `minutes` x 60 + offset seconds old: current just inside the limit, no current data just after."""
    now = int(time.time())
    age = minutes * 60 + offset
    data = entry(stale_threshold=minutes)
    run = build(tmp_path, data, readings=series(now, 1, age=age), now=now)
    current = offset < 0
    assert run.c.display_status == ("current" if current else "no_current_data")
    assert run.sensor("is_stale")["state"] is (not current)
    assert run.sensor("is_stale")["attributes"]["threshold_minutes"] == minutes
    assert run.config()["no_current_data_minutes"] == minutes
    assert (run.c.trend_arrow != "") is current and (
        run.sensor("current_glucose")["attributes"]["display_word"] != ""
    ) is current
    assert (run.sensor("in_range")["state"] is None) is (not current)
    assert ("No current data since" in run.daily()) is (not current)
    # the tick decides the same
    assert run.c._no_current_data_at(now) is (not current)


@pytest.mark.parametrize("stored", [31, 45, 60])
def test_a_stored_limit_above_30_minutes_is_read_as_30(tmp_path, stored):
    """The limit's span is 5 to 30 minutes; a stored value above 30 is read as 30."""
    now = int(time.time())
    run = build(tmp_path, entry(stale_threshold=stored), readings=series(now, 1, age=31 * 60), now=now)
    assert run.config()["no_current_data_minutes"] == 30
    assert run.sensor("is_stale")["attributes"]["threshold_minutes"] == 30
    assert run.c.no_current_data is True and run.sensor("is_stale")["state"] is True
