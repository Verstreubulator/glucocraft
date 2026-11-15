# License: AGPL-3.0-only (LICENSE).
"""Diagnostics (diagnostics.py): what the downloaded file contains, and what it never contains.

Rules under test:
- The file has the settings, the version, the counts and time span of the stored readings, the reading interval, the
  state of the sources, the active repair issues, the wall-panel feed and the language, and it is plain JSON.
- It never contains a glucose value (of a stored reading or of a source's state), an entity id of a source, a phone's
  notify action, the printer's address or the wall-panel address.
- A repair issue named after a source is listed with the source's role; issues of other integrations and issues that
  are no longer active are left out.
"""

from __future__ import annotations

import asyncio
import json
import types
from collections.abc import Iterable, Mapping

import pytest
from glucocraft import const as C
from glucocraft import diagnostics as D

from conftest import add_reading, real_coordinator

REDACTED = "**REDACTED**"

PRIMARY, SECONDARY = "sensor.cgm_primary_glucose", "sensor.cgm_backup_glucose"
EXPIRY = "sensor.cgm_sensor_expiry"
PHONE = "mobile_app_phone_of_the_household"
PRINTER = "ipp://192.168.77.41/ipp/print"
WEBHOOK = "glucocraft_panel_0123456789abcdef0123456789abcdef"

# The stored readings (mg/dL) and the sources' current states: values that are not any setting's default, so that a
# match in the output can only be a reading
READINGS = (123.4, 287.6, 61.3)
SOURCE_STATES = {PRIMARY: "131.9", SECONDARY: "7.3"}


def _redact(data, to_redact: Iterable):
    """Home Assistant's async_redact_data (homeassistant/components/diagnostics/util.py), for the stub."""
    if not isinstance(data, (Mapping, list)):
        return data
    if isinstance(data, list):
        return [_redact(v, to_redact) for v in data]
    out = {**data}
    for key, value in out.items():
        if value is None or (isinstance(value, str) and not value):
            continue
        if key in to_redact:
            out[key] = REDACTED
        elif isinstance(value, (Mapping, list)):
            out[key] = _redact(value, to_redact)
    return out


def _issue(active: bool = True):
    return types.SimpleNamespace(active=active)


@pytest.fixture
def diagnostics(tmp_path, monkeypatch):
    """Run async_get_config_entry_diagnostics on an entry with two sources, phones, a printer and a panel address."""
    data = {
        C.CONF_GLUCOSE_ENTITY: PRIMARY,
        C.CONF_SECONDARY_ENTITY: SECONDARY,
        C.CONF_SENSOR_EXPIRY_ENTITY: EXPIRY,
        C.CONF_PHONE_TARGETS: [PHONE],
        C.CONF_PRINTER: PRINTER,
        C.CONF_PANEL_FEED: True,
        C.CONF_PANEL_WEBHOOK_ID: WEBHOOK,
        C.CONF_UNIT: "mg/dL",
        # custom ranges and goals: redacted, only whether they are set is given
        C.CONF_CUSTOM_RANGES_AND_GOALS: True,
        C.CONF_RANGE_CRITICAL_LOW: 60,
        C.CONF_GOAL_TBR: 3,
    }
    c = real_coordinator(tmp_path, _settings=data, _glucose_entity=PRIMARY, _secondary_entity=SECONDARY)
    start = 1_790_000_000
    for i, raw in enumerate(READINGS):
        source = C.SOURCE_SECONDARY if i == 2 else C.SOURCE_PRIMARY
        add_reading(c.storage, start + i * 300, raw, "high" if i == 1 else None, source)
    c.active_source_entity = PRIMARY
    c.last_reading_ts = start + 600
    c._src_last_ts = {PRIMARY: start + 600}
    c._src_times = {PRIMARY: [start + i * 300 for i in range(11)]}
    c.integration_version = "0.0.5"
    for entity_id, state in SOURCE_STATES.items():
        c.hass.states.d[entity_id] = types.SimpleNamespace(
            state=state, attributes={"unit_of_measurement": "mg/dL", "friendly_name": "CGM of the household"}
        )

    async def run(fn, *args):
        return fn(*args)

    c.hass.async_add_executor_job = run
    issues = {
        (C.DOMAIN, f"slow_source_{PRIMARY}"): _issue(),
        (C.DOMAIN, f"sensor_cap_{SECONDARY}"): _issue(),
        (C.DOMAIN, "source_unit_sensor.removed_source"): _issue(),
        (C.DOMAIN, "storage_problem"): _issue(active=False),
        ("other_integration", f"slow_source_{PRIMARY}"): _issue(),
    }
    panel = types.SimpleNamespace(model="glucocraft.guition4848s040-480x480", sw_version="0.0.19", name="Panel A")
    monkeypatch.setattr(D, "async_redact_data", _redact)
    monkeypatch.setattr(D, "REDACTED", REDACTED)
    monkeypatch.setattr(D.ir, "async_get", lambda hass: types.SimpleNamespace(issues=issues))
    monkeypatch.setattr(D.dr, "async_get", lambda hass: types.SimpleNamespace(async_get=lambda device_id: panel))
    monkeypatch.setattr(D, "panel_device_ids", lambda hass: ["device_1"])
    entry = types.SimpleNamespace(entry_id="t", data=data, options={}, runtime_data=c)
    return asyncio.run(D.async_get_config_entry_diagnostics(c.hass, entry))


def _numbers(value) -> list[float]:
    """Every number in a JSON-like structure."""
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [value]
    if isinstance(value, Mapping):
        return [n for v in value.values() for n in _numbers(v)]
    if isinstance(value, list):
        return [n for v in value for n in _numbers(v)]
    return []


def test_the_file_has_every_part_and_is_json(diagnostics):
    assert json.loads(json.dumps(diagnostics)) == diagnostics
    assert diagnostics["version"] == "0.0.5" and diagnostics["loaded"] is True
    assert diagnostics["language"] == {"home_assistant": "en", "in_use": "en"}
    stored = diagnostics["stored_readings"]
    assert stored["count"] == 3 and stored["by_source"] == {"primary": 2, "secondary": 1, "unknown": 0}
    assert stored["limit_readings"] == 1 and stored["span_days"] == 0.0
    assert stored["oldest"].startswith("2026-09-21T") and stored["newest"] > stored["oldest"]
    assert diagnostics["reading_interval"]["source_in_use_seconds"] == 300
    assert diagnostics["reading_interval"]["gap_threshold_seconds"] == 900
    sources = diagnostics["sources"]
    assert sources["primary"]["in_use"] is True and sources["primary"]["available"] is True
    assert sources["primary"]["unit"] == "mg/dL" and sources["secondary"]["in_use"] is False
    assert sources["sensor_expiry"] == {"exists": False, "available": False, "unit": None, "format": "missing"}
    assert diagnostics["custom"] == {"custom_ranges_in_use": True, "custom_goals_set": True}
    assert diagnostics["regional"]["country"] == "US" and diagnostics["regional"]["resolved"]["time_format"] == "12"
    assert set(sources) == {"primary", "secondary", "sensor_expiry"}
    assert diagnostics["status"]["display_status"] in ("current", "no_current_data")
    assert diagnostics["panel_feed"] == {
        "enabled": True,
        "address_created": True,
        "webhook_id": REDACTED,
        "panels": [{"model": "glucocraft.guition4848s040-480x480", "version": "0.0.19"}],
    }


def test_the_settings_are_kept_and_the_identifying_ones_redacted(diagnostics):
    settings = diagnostics["settings"]
    assert settings[C.CONF_UNIT] == "mg/dL" and settings[C.CONF_PANEL_FEED] is True
    for key in D.TO_REDACT & set(settings):
        assert settings[key] == REDACTED, key
    # the identifying settings, a range limit and a goal are among them
    assert {C.CONF_GLUCOSE_ENTITY, C.CONF_PRINTER, C.CONF_RANGE_CRITICAL_LOW, C.CONF_GOAL_TBR} <= set(settings)


def test_repair_issues_name_the_source_role_and_only_active_issues_of_glucocraft(diagnostics):
    assert diagnostics["repair_issues"] == [
        "sensor_cap_secondary_source",
        "slow_source_primary_source",
        f"source_unit_{REDACTED}",
    ]


def test_no_glucose_value_entity_id_webhook_id_or_address_appears(diagnostics):
    text = json.dumps(diagnostics)
    for secret in (PRIMARY, SECONDARY, EXPIRY, PHONE, PRINTER, WEBHOOK, "192.168.77.41", "removed_source"):
        assert secret not in text, secret
    assert "CGM of the household" not in text and "Panel A" not in text  # the names chosen by the household
    values = {*READINGS, *(float(v) for v in SOURCE_STATES.values())}
    assert not values & set(_numbers(diagnostics))
    for value in values:
        assert str(value) not in text, value


def test_an_entry_that_is_not_loaded_still_gives_its_redacted_settings(monkeypatch):
    monkeypatch.setattr(D, "async_redact_data", _redact)
    monkeypatch.setattr(D, "REDACTED", REDACTED)
    monkeypatch.setattr(D.ir, "async_get", lambda hass: types.SimpleNamespace(issues={}))
    monkeypatch.setattr(D.dr, "async_get", lambda hass: types.SimpleNamespace(async_get=lambda device_id: None))
    monkeypatch.setattr(D, "panel_device_ids", lambda hass: [])
    hass = types.SimpleNamespace(config=types.SimpleNamespace(language="en", country="US"))
    entry = types.SimpleNamespace(entry_id="t", data={C.CONF_GLUCOSE_ENTITY: PRIMARY}, options={}, runtime_data=None)
    out = asyncio.run(D.async_get_config_entry_diagnostics(hass, entry))
    assert out["loaded"] is False and out["version"] is None and "stored_readings" not in out
    assert out["settings"][C.CONF_GLUCOSE_ENTITY] == REDACTED and PRIMARY not in json.dumps(out)
    assert out["panel_feed"]["address_created"] is False and out["panel_feed"]["webhook_id"] is None


def test_a_stored_key_glucocraft_does_not_know_is_redacted(monkeypatch):
    """A stored key that nothing reads (for example direction_entity, which names an entity) is listed with its
    value redacted, in the settings and in the options; the keys GlucoCraft knows keep their
    values."""
    monkeypatch.setattr(D, "async_redact_data", _redact)
    monkeypatch.setattr(D, "REDACTED", REDACTED)
    monkeypatch.setattr(D.ir, "async_get", lambda hass: types.SimpleNamespace(issues={}))
    monkeypatch.setattr(D.dr, "async_get", lambda hass: types.SimpleNamespace(async_get=lambda device_id: None))
    monkeypatch.setattr(D, "panel_device_ids", lambda hass: [])
    hass = types.SimpleNamespace(config=types.SimpleNamespace(language="en", country="US"))
    old = {"direction_entity": "sensor.jane_doe_trend", "notify_low_enabled": True, "some_future_key": ["x"]}
    data = {C.CONF_GLUCOSE_ENTITY: PRIMARY, C.CONF_STALE_THRESHOLD: 20, C.CONF_NO_DATA_NOTICE: True, **old}
    entry = types.SimpleNamespace(entry_id="t", data=data, options=dict(old), runtime_data=None)
    out = asyncio.run(D.async_get_config_entry_diagnostics(hass, entry))
    for part in ("settings", "options"):
        for key in old:
            assert out[part][key] == REDACTED, (part, key)
    assert out["settings"][C.CONF_STALE_THRESHOLD] == 20 and out["settings"][C.CONF_NO_DATA_NOTICE] is True
    assert "jane_doe" not in json.dumps(out)
    # every key the entry can hold is known: a fresh entry's settings are never redacted for being unknown
    from glucocraft.settings import ENTRY_KEYS

    assert {C.CONF_FLASH_NO_CURRENT_DATA, C.CONF_NO_DATA_NOTICE_DELAY} <= ENTRY_KEYS
    assert not {"terms_version", "terms_accepted_at", "disclaimer_accepted"} & ENTRY_KEYS  # no acceptance record
    assert {*C.CONF_ZONE_COLORS.values(), *C.CONF_BAND_COLORS.values(), C.CONF_RANGE_LOW} <= ENTRY_KEYS
