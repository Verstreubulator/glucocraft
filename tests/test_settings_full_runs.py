# License: AGPL-3.0-only (LICENSE).
"""Full runs: every setting at its default, and every setting at a value other than its default at the same time, each
in both display units; every output is checked against the entry.

The non-default entry (harness.NON_DEFAULT_ENTRY) changes every stored setting at once, so that a setting that
silently overrides another, or an output that ignores one, shows up here even when each setting passes on its own.
"""

from __future__ import annotations

import asyncio
import io
import re
import time
import types
from datetime import UTC, datetime

import pytest
from glucocraft import const as C
from glucocraft import notification as N

from test_entity_ids import _load_init
from test_settings_harness import (
    DEFAULT_ENTRY,
    NON_DEFAULT_ENTRY,
    PRIMARY,
    SECONDARY,
    build,
    fixed_clock,  # noqa: F401 (a fixture)
    state,
    tz_patch,  # noqa: F401 (a fixture)
)

pytestmark = pytest.mark.usefixtures("tz_patch", "fixed_clock")

UNITS = ["mg/dL", "mmol/L"]
PUBLIC_RANGE = {
    "very_low": C.CONF_RANGE_CRITICAL_LOW,
    "low": C.CONF_RANGE_LOW,
    "tight_low": C.CONF_RANGE_TIGHT_LOW,
    "tight_high": C.CONF_RANGE_TIGHT_HIGH,
    "high": C.CONF_RANGE_HIGH,
    "very_high": C.CONF_RANGE_CRITICAL_HIGH,
}
PUBLIC_GOAL = {
    "time_in_range": C.CONF_GOAL_TIME_IN_TARGET_RANGE,
    "time_in_tight_range": C.CONF_GOAL_TIME_IN_IDEAL_RANGE,
    "time_low": C.CONF_GOAL_TBR,
    "time_very_low": C.CONF_GOAL_TBR_CRITICAL,
    "time_high": C.CONF_GOAL_TAR,
    "time_very_high": C.CONF_GOAL_TAR_CRITICAL,
    "cv": C.CONF_GOAL_CV,
    "gmi": C.CONF_GOAL_GMI,
}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).replace(microsecond=0).isoformat()


def _scheduled(run, monkeypatch) -> list:
    scheduled = []
    monkeypatch.setattr(N, "async_track_time_change", lambda hass, cb, **kw: scheduled.append(kw) or (lambda: None))
    _load_init()._setup_notifications(
        run.hass, types.SimpleNamespace(data=run.data, async_on_unload=lambda f: None), run.c
    )
    return scheduled


@pytest.mark.parametrize("unit", UNITS)
def test_every_setting_at_its_default(tmp_path, monkeypatch, unit):
    data = {**DEFAULT_ENTRY, C.CONF_UNIT: unit}
    run = build(tmp_path, data, country="US", language="en", days=20)
    cfg = run.config()
    assert cfg["unit"] == unit and cfg["no_current_data_minutes"] == 15
    assert {k: cfg[k] for k in ("time_format", "date_format", "first_weekday", "decimal_separator", "paper_size")} == {
        "time_format": "12",
        "date_format": "MDY",
        "first_weekday": 6,
        "decimal_separator": ".",
        "paper_size": "letter",
    }
    assert cfg["a1c_unit"] == "percent"  # Automatic from the country (US)
    assert cfg["ranges"] == {
        "very_low": 54,
        "low": 70,
        "tight_low": 70,
        "tight_high": 140,
        "high": 180,
        "very_high": 250,
    }
    assert cfg["goals"] == {
        "time_in_range": {"op": ">", "value": 70},
        "time_low": {"op": "<", "value": 4},
        "time_very_low": {"op": "<", "value": 1},
        "time_high": {"op": "<", "value": 25},
        "time_very_high": {"op": "<", "value": 5},
        "cv": {"op": "<=", "value": 36},
    }
    assert cfg["goals_custom"] is False and cfg["weekly_pages"] == {
        "setting": "consensus",
        "offered": False,
        "shown": "consensus",
    }
    assert cfg["zone_colors"] == {z: c.upper() for z, c in C.ZONE_COLORS.items()}
    assert cfg["trend_display"] == "arrow" and cfg["display_card"] == "square"
    assert cfg["notifications"] == {
        "daily": True,
        "daily_time": "08:00",
        "weekly": False,
        "weekly_day": 6,
        "no_data_notice": False,
        "no_data_notice_delay_minutes": 30,
    }
    assert cfg["flash_no_current_data"] is True
    assert "test_notifications" not in cfg and "direction_entity" not in cfg
    assert cfg["phone_notifications"] == {"targets": [], "types": []}
    assert cfg["dashboard"] is True and cfg["panel_feed"] is False and cfg["retention_days"] == 200
    p = cfg["primary_source"]
    assert (p["lower_limit"], p["upper_limit"], p["time_attribute"]) == (40, 500, "auto")
    assert cfg["secondary_source"] is None and cfg["expiry_entity"] is None
    assert [(s["hour"], s["minute"]) for s in _scheduled(run, monkeypatch)] == [(8, 0)]
    feed = run.feed()
    assert [s["kind"] for s in feed["ext"]["standards"]] == [0]
    assert run.api_stats()["ranges"]["is_default"] is True
    rep = run.report(14)
    assert rep["printer"] is False and rep["unit"] == unit and "custom_appendix" not in rep


@pytest.mark.parametrize("unit", UNITS)
def test_every_setting_at_a_non_default_value(tmp_path, monkeypatch, unit):
    data = {**NON_DEFAULT_ENTRY, C.CONF_UNIT: unit}
    now = int(time.time())
    expiry = _iso(now + 3 * 86400)
    run = build(
        tmp_path,
        data,
        country="DE",
        language="de",
        days=20,
        now=now,
        services=("notify.mobile_app_phone",),
        states={"sensor.expiry": state(expiry)},
    )
    c = run.c
    mm = unit == "mmol/L"
    cfg = run.config()
    # ---- sources
    assert cfg["primary_source"]["time_attribute"] == "measurement_timestamp"
    assert cfg["secondary_source"]["entity"] == SECONDARY and cfg["secondary_source"]["time_attribute"] == "dateString"
    p, s = cfg["primary_source"], cfg["secondary_source"]
    assert (p["lower_limit"], p["upper_limit"], s["lower_limit"], s["upper_limit"]) == (50, 400, 40, 450)
    assert c._source_reading(PRIMARY, state("HI", unit_of_measurement=unit)) == (400.0, "high")
    assert c._source_reading(SECONDARY, state("HI", unit_of_measurement=unit)) == (450.0, "high")
    assert cfg["expiry_entity"] == "sensor.expiry" and run.sensor("sensor_expiry")["state"] == "not_expired"
    assert "source_direction" not in run.sensor("trend_arrow")["attributes"]
    # ---- data and display
    assert cfg["no_current_data_minutes"] == 30 and run.sensor("is_stale")["attributes"]["threshold_minutes"] == 30
    assert cfg["unit"] == unit and run.sensor("current_glucose")["suggested_unit"] is None
    assert {
        k: cfg[k]
        for k in ("time_format", "date_format", "first_weekday", "decimal_separator", "a1c_unit", "paper_size")
    } == {
        "time_format": "24",
        "date_format": "YMD",
        "first_weekday": 5,
        "decimal_separator": ",",
        "a1c_unit": "both",
        "paper_size": "a4",
    }
    assert cfg["display_card"] == "rectangular" and cfg["trend_display"] == "word"
    assert cfg["zone_colors"] == {z: data[C.CONF_ZONE_COLORS[z]] for z in C.ZONES}
    a = run.sensor("current_glucose")["attributes"]
    assert a["zone_color"] == data[C.CONF_ZONE_COLORS[a["zone"]]]
    # the zone word under the value in every setting; the trend word for the TREND column on the Trend arrow sensor
    assert a["display_word"] == {"tight_range": "Tight range"}.get(a["zone"], a["display_word"]) and a["display_word"]
    tw = run.sensor("trend_arrow")["attributes"]["word"]
    assert tw == (c.trend_description[:1] + c.trend_description[1:].lower() if c.trend_description != "Unknown" else "")
    # ---- ranges and goals on every output
    assert cfg["ranges"] == {k: data[v] for k, v in PUBLIC_RANGE.items()}
    assert {k: v["value"] for k, v in cfg["goals"].items()} == {k: data[v] for k, v in PUBLIC_GOAL.items()}
    assert run.api_stats()["goals"] == {k: data[v] for k, v in PUBLIC_GOAL.items()}
    api = run.api_data("24h")["ranges"]
    factor = 18.016 if unit == "mmol/L" else 1  # the API's values are unrounded
    assert {k: api[k] for k in PUBLIC_RANGE} == {k: data[v] / factor for k, v in PUBLIC_RANGE.items()}
    assert api["is_default"] is False
    feed = run.feed()
    assert feed["ranges"] == tuple(
        float(data[k])
        for k in (
            C.CONF_RANGE_CRITICAL_LOW,
            C.CONF_RANGE_CRITICAL_HIGH,
            C.CONF_RANGE_LOW,
            C.CONF_RANGE_HIGH,
            C.CONF_RANGE_TIGHT_LOW,
            C.CONF_RANGE_TIGHT_HIGH,
        )
    )
    stds = feed["ext"]["standards"]
    assert [x["kind"] for x in stds] == [1, 0] and cfg["weekly_pages"]["shown"] == "custom"
    assert stds[0]["targets"][0] == (1, 75.0) and stds[0]["targets"][4] == (3, 6.5)
    assert ("mmol/L" if mm else "mg/dL") in stds[0]["desc"][0]
    # ---- notifications: the daily summary off, the weekly one on (at the daily summary time); the summaries to the
    # phones
    assert [(s["hour"], s["minute"]) for s in _scheduled(run, monkeypatch)] == [(21, 45)]
    assert cfg["notifications"] == {
        "daily": False,
        "daily_time": "21:45",
        "weekly": True,
        "weekly_day": 0,
        "no_data_notice": True,
        "no_data_notice_delay_minutes": 60,
    }
    assert cfg["flash_no_current_data"] is False
    assert "test_notifications" not in cfg and "direction_entity" not in cfg
    assert cfg["phone_notifications"] == {
        "targets": ["mobile_app_phone", "mobile_app_tablet"],
        "types": ["summaries", "no_data_notice"],
    }
    asyncio.run(N.async_send_daily_summary(run.hass, run.c))
    phone = [d["title"] for _, d in run.hass.services.phone()]
    assert phone == ["GlucoCraft Daily Summary"]  # the chosen type, only the available phone
    text = run.hass.services.notifications()[0]["message"]
    assert re.search(r" at \d\d:\d\d \(", text)  # 24-hour clock
    assert ("millimoles per liter" if mm else "milligrams per deciliter") in text
    if mm:
        assert re.search(r"Last reading: \d+,\d millimoles per liter", text)  # decimal comma
    # ---- the summaries: comma, both A1C units, the ranges in the settings
    weekly = run.weekly()
    assert re.search(r"\(GMI\) \d+,\d% \(\d+ mmol/mol\)", weekly)  # spelled out at its first use
    assert "Custom ranges:" in weekly and not re.search(r"\d\.\d", weekly.split("*Targets and ranges")[0])
    # ---- the report: regional values, the printer, the custom appendix
    rep = run.report(14, False, True, True)
    assert {
        k: rep[k]
        for k in ("time_format", "date_format", "first_weekday", "decimal_separator", "a1c_unit", "paper_size")
    } == {
        "time_format": "24",
        "date_format": "YMD",
        "first_weekday": 5,
        "decimal_separator": ",",
        "a1c_unit": "both",
        "paper_size": "a4",
    }
    assert rep["unit"] == unit and rep["printer"] is True
    assert rep["custom_appendix"]["ranges"] == cfg["ranges"]
    assert {r["key"]: r["goal"] for r in rep["custom_appendix"]["rows"]} == {k: data[v] for k, v in PUBLIC_GOAL.items()}
    assert re.fullmatch(r"\w{3} \d{4}-\d\d-\d\d \d\d:\d\d", rep["created"])
    # ---- the CSV: data, never the regional formats
    last = run.csv().strip().splitlines()[-1].split(",")
    assert last[4] == unit and "," not in last[2]


@pytest.mark.slow
@pytest.mark.parametrize("unit", UNITS)
def test_the_non_default_pdf(tmp_path, unit):
    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")
    run = build(tmp_path, {**NON_DEFAULT_ENTRY, C.CONF_UNIT: unit}, country="DE", language="de", days=20)
    (pdf, pages), data = run.pdf(14, True, True, True, True)
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    box = reader.pages[0].mediabox
    assert (round(float(box.width), 1), round(float(box.height), 1)) == (595.3, 841.9)  # A4
    text = " ".join(" ".join((p.extract_text() or "").split()) for p in reader.pages)
    assert "Custom Ranges and Goals" in text and "mmol/mol" in text and unit in text
    assert data["created"] in text
    if unit == "mmol/L":
        assert "very low below 3,3; low below 4,2" in text  # 60 / 75 mg/dL in mmol/L, decimal comma
    else:
        assert "very low below 60; low below 75" in text
    assert pages == len(reader.pages)
