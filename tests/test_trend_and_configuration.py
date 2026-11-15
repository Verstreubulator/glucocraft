# License: AGPL-3.0-only (LICENSE).
"""coordinator.py: the trend arrow, the rate of change and the 5-minute change are cleared while the data are not
current; the configuration() contract (resolved values + *_setting, no Terms record, the integration version, no
glucose-level notification setting (a stored one is ignored))."""

from __future__ import annotations

import time
import types
from datetime import UTC, datetime, timedelta

import pytest
from glucocraft import sensor as se

from conftest import real_coordinator


def settings_coordinator(tmp_path, config=None, country="US", language="en"):
    """A coordinator with sensor.p in use, the settings `config` and Home Assistant's country and language."""
    return real_coordinator(
        tmp_path,
        country=country,
        language=language,
        _settings=dict(config or {}),
        entry_id="e1",
        active_source_entity="sensor.p",
        active_source="P",
    )


def rising(c, rate=1.5, minutes=60):
    """Readings every 5 minutes up to now, rising `rate` mg/dL per minute; the newest is processed."""
    now = int(time.time())
    for k in range(minutes // 5, -1, -1):
        t = now - k * 300
        c._process_reading(100 + rate * (minutes - k * 5), t, "sensor.p", "P", True)
    return now


def assert_shown(c):
    assert c.slope is not None and c.change_5min is not None
    assert c.trend_arrow == "↗" and c.trend_description == "Rising"
    assert c.trend_angle is not None and c.delta_5min is not None


def assert_cleared(c):
    assert c.slope is None and c.change_5min is None
    assert c.trend_arrow == "" and c.trend_description == "Unknown"
    assert c.trend_angle is None and c.delta_5min is None


def test_shown_while_current(tmp_path):
    c = settings_coordinator(tmp_path)
    rising(c)
    assert c.display_status == "current"
    assert_shown(c)


def test_cleared_when_no_current_data_and_back(tmp_path):
    c = settings_coordinator(tmp_path)
    rising(c)
    c.no_current_data = True  # the tick found no new reading
    assert c.display_status == "no_current_data"
    assert_cleared(c)
    c.no_current_data = False  # values were kept, shown again once current
    assert_shown(c)


def test_cleared_during_warm_up(tmp_path):
    c = settings_coordinator(tmp_path)
    rising(c)
    c.sensor_warming_up = True
    assert c.display_status == "warm_up"
    assert_cleared(c)


def test_cleared_without_data(tmp_path):
    c = settings_coordinator(tmp_path)
    c.slope, c.change_5min, c.trend_arrow, c.trend_description = 1.5, 7.0, "↗", "Rising"
    assert c.display_status == "no_data"
    assert_cleared(c)


@pytest.mark.parametrize(
    "hours,status,shown", [(-1, "expired", True), (2, "expiring_soon", True), (72, "current", True)]
)
def test_expiry(tmp_path, hours, status, shown):
    """The trend follows the data, not the status: after the stated expiry, while readings still arrive (no current
    data off), the slope, the 5-minute delta and the arrow are shown."""
    c = settings_coordinator(tmp_path)
    rising(c)
    c._expiry_entity = "sensor.expiry"
    exp = (datetime.fromtimestamp(time.time(), UTC) + timedelta(hours=hours)).replace(microsecond=0).isoformat()
    c.hass.states.d["sensor.expiry"] = types.SimpleNamespace(state=exp, attributes={})
    assert c.display_status == status
    (assert_shown if shown else assert_cleared)(c)


def test_the_trend_gate_is_the_flag_and_the_warm_up(tmp_path):
    """coordinator.trend_current = not no_current_data and not warm-up, whatever the status."""
    c = settings_coordinator(tmp_path)
    rising(c)
    for no_current, warming, shown in ((False, False, True), (True, False, False), (False, True, False)):
        c.no_current_data, c.sensor_warming_up = no_current, warming
        assert c.trend_current is shown
        (assert_shown if shown else assert_cleared)(c)


def test_sensor_entities_show_the_cleared_values(tmp_path):
    c = settings_coordinator(tmp_path)
    rising(c)

    def ent(cls):
        e = cls.__new__(cls)
        e.coordinator = c
        return e

    d5, arrow = ent(se.GlucoCraftDelta5Min), ent(se.GlucoCraftTrendArrow)
    assert d5.native_value is not None and arrow.native_value == "↗"
    assert arrow.extra_state_attributes["angle"] is not None and "rate_per_min" not in arrow.extra_state_attributes
    c.no_current_data = True
    assert d5.native_value is None and arrow.native_value is None
    attrs = arrow.extra_state_attributes
    assert attrs["angle"] is None and attrs["description"] == "Unknown"
    assert d5.extra_state_attributes["value_mgdl"] is None


def test_trend_word_from_the_stored_slope_after_a_restart(tmp_path):
    """_apply_adaptive works on the stored values even while the data are stale (no trend word lost)."""
    c = settings_coordinator(tmp_path)
    t = rising(c)
    c.no_current_data = True
    c._apply_adaptive(t)
    c.no_current_data = False
    assert_shown(c)


# ------------------------------------------------------------------------------------------------ configuration()
REGIONAL = ("time_format", "date_format", "first_weekday", "decimal_separator", "a1c_unit", "paper_size")


def test_configuration_contract_automatic(tmp_path):
    c = settings_coordinator(tmp_path, {"glucose_entity": "sensor.p"}, country="DE", language="de")
    cfg = c.configuration()
    assert {k: cfg[k] for k in REGIONAL} == {
        "time_format": "24",
        "date_format": "DMY",
        "first_weekday": 0,
        "decimal_separator": ",",
        "a1c_unit": "both",
        "paper_size": "a4",
    }
    assert {k: cfg[f"{k}_setting"] for k in REGIONAL} == {k: "auto" for k in REGIONAL}
    assert "terms" not in cfg  # no Terms of Use and no acceptance record
    assert cfg["integration_version"] is None
    assert "test_notifications" not in cfg and "direction_entity" not in cfg  # removed settings
    assert "glucose" not in cfg["notifications"]  # no glucose notifications


def test_configuration_contract_explicit(tmp_path):
    conf = {
        "time_format": "12",
        "date_format": "ymd",
        "first_weekday": "saturday",
        "decimal_separator": "point",
        "a1c_unit": "mmol_mol",
        "paper_size": "letter",
        "terms_version": "1.0",  # not a setting: ignored
        "direction_entity": "sensor.cgm_trend",  # not a setting: ignored
        "notify_low_enabled": True,  # a setting of the removed glucose notifications: ignored
    }
    c = settings_coordinator(tmp_path, conf, country="DE", language="de")
    c.integration_version = "0.0.2"
    cfg = c.configuration()
    assert {k: cfg[k] for k in REGIONAL} == {
        "time_format": "12",
        "date_format": "YMD",
        "first_weekday": 5,
        "decimal_separator": ".",
        "a1c_unit": "mmol_mol",
        "paper_size": "letter",
    }
    assert cfg["first_weekday_setting"] == "saturday" and cfg["decimal_separator_setting"] == "point"
    assert cfg["a1c_unit_setting"] == "mmol_mol" and cfg["paper_size_setting"] == "letter"
    assert "terms" not in cfg and "terms_version" not in cfg
    assert cfg["integration_version"] == "0.0.2"
    assert "test_notifications" not in cfg


def test_configuration_without_country_follows_the_unit(tmp_path):
    c = settings_coordinator(tmp_path, {"unit": "mmol/L"}, country=None, language=None)
    cfg = c.configuration()
    assert cfg["a1c_unit"] == "both" and cfg["paper_size"] == "letter" and cfg["first_weekday"] == 6
    assert cfg["decimal_separator"] == "."


def test_configuration_values_stay_unformatted(tmp_path):
    """Stored data, attributes and the API always use the point: the separator is only a setting here."""
    c = settings_coordinator(tmp_path, {"decimal_separator": "comma"}, country="US")
    cfg = c.configuration()
    assert cfg["decimal_separator"] == "," and isinstance(cfg["ranges"]["low"], (int, float))
    assert isinstance(cfg["no_current_data_minutes"], int)
