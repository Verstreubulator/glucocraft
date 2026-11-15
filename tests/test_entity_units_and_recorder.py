# License: AGPL-3.0-only (LICENSE).
"""The glucose sensors' display unit and precision, and what the recorder keeps of the entities' attributes.

Rules under test:
- The state of the per-statistic percentage entities (CV, sensor active, the custom time-in-band entities) is the exact
  value rounded once to one decimal, as is the summary sensors' state; the summary sensors' attributes stay exact.
- The glucose sensors suggest no unit (no suggested_unit_of_measurement): Home Assistant then scales the display
  precision 0 from the native mg/dL to the unit in use, which is one decimal in mmol/L. The unit comes from the
  entity settings, which setup writes after the platforms (unit_registry.keep_display_unit): the display unit setting
  for a sensor without one, an earlier suggestion where Home Assistant holds one, and never over a unit chosen in the
  entity settings (so no sensor changes its unit at a restart, and the history and long-term statistics stay as
  they are). Idempotent.
- Attributes that change with every reading or minute while the state stays are not recorded: all of the
  Configuration sensor's, and the churning ones of five more entities. Every excluded name is an attribute
  the entity publishes (no exclusion that matches nothing).
- A second source that is the primary source itself is refused; a failed print or export is a
  HomeAssistantError with a translated message; the delivery test logs no notify action's name; the
  notify actions are read from the notify domain only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest
from glucocraft import binary_sensor as bs
from glucocraft import config_flow as cf
from glucocraft import const as C
from glucocraft import flow_schemas as fs
from glucocraft import sensor as se
from glucocraft import unit_registry as ur

from conftest import FakeRegistry
from test_entity_ids import _load_init
from test_settings_harness import build, entry, state, tz_patch  # noqa: F401 (the fixture)

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "glucocraft"
STRINGS = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))
GLUCOSE_CLASSES = (
    se.GlucoCraftCurrentGlucose,
    se.GlucoCraftDelta5Min,
    se.GlucoCraftPeriodMean,
    se.GlucoCraftPeriodStdDev,
    se.GlucoCraftPeriodMin,
    se.GlucoCraftPeriodMax,
)


# ------------------------------------------------------------------------------------------------ rounded states
ROUNDED_STATES = {
    se.GlucoCraftPeriodCV: "cv",
    se.GlucoCraftPeriodDataAvailability: "data_availability",
    se.GlucoCraftPeriodTIR: "pct_time_in_target_range",
    se.GlucoCraftPeriodTITR: "pct_time_in_ideal_range",
    se.GlucoCraftPeriodTimeLow: "time_low",
    se.GlucoCraftPeriodTimeHigh: "time_high",
    se.GlucoCraftPeriodTimeVeryLow: "time_critical_low",
    se.GlucoCraftPeriodTimeVeryHigh: "time_critical_high",
}


@pytest.mark.parametrize("exact, shown", [(28.9063187487193, 28.9), (28.95, 29.0), (69.96, 70.0), (0.04, 0.0)])
def test_the_per_statistic_percentage_states_are_rounded_once(exact, shown):
    """The state of the per-statistic percentage entities is the exact value rounded once to one decimal (halves away
    from zero), so that the recorder writes a row only when the value shown changes; the summary sensors' attributes,
    which the cards, panels and report read, keep the exact value."""
    stats = types.SimpleNamespace(**dict.fromkeys(ROUNDED_STATES.values(), exact), sensor_active_ok=True)
    coordinator = types.SimpleNamespace(period_stats={"14d": stats}, period_stats_agp={"14d": stats})
    for cls in ROUNDED_STATES:
        entity = cls.__new__(cls)
        entity.coordinator, entity._period = coordinator, "14d"
        assert entity.native_value == shown, cls.__name__
    assert se._state_pct(None) is None
    # the summary sensors' time in range is rounded the same way (tests/test_recorder_load.py)


@pytest.mark.parametrize(
    "exact, unit, state, shown",
    [
        (129.46, "mg/dL", 129, "129"),  # not 129.5 first, then 130
        (129.5, "mg/dL", 130, "130"),
        (129.46, "mmol/L", 129.7152, "7.2"),  # 7.1858 mmol/L -> 7.2 -> 7.2 x 18.016 mg/dL
        (127.36, "mmol/L", 127.9136, "7.1"),  # 7.0693 -> 7.1, not 127 mg/dL first, then 7.0
        (18.5, "mmol/L", 18.016, "1.0"),  # a standard deviation of 1.03 mmol/L
    ],
)
def test_the_mean_and_sd_states_are_rounded_once_in_the_display_unit(exact, unit, state, shown):
    """The state of the 14 mean / standard deviation entities is rounded once to the step Home Assistant shows in the
    display unit: whole mg/dL, or the mg/dL value of the mean rounded to 0.1 mmol/L, which Home Assistant converts back
    with 18.016 to exactly that tenth. Two exact values shown the same give the same state, so
    the recorder writes no row for them."""
    stats = types.SimpleNamespace(mean=exact, std_dev=exact, sensor_active_ok=True)
    coordinator = types.SimpleNamespace(period_stats={"14d": stats}, display_unit=unit)
    for cls in (se.GlucoCraftPeriodMean, se.GlucoCraftPeriodStdDev):
        entity = cls.__new__(cls)
        entity.coordinator, entity._period = coordinator, "14d"
        assert entity.native_value == state, cls.__name__
        in_unit = entity.native_value / 18.016 if unit == "mmol/L" else entity.native_value
        assert f"{in_unit:.{1 if unit == 'mmol/L' else 0}f}" == shown
    near_stats = types.SimpleNamespace(mean=exact + 0.02, sensor_active_ok=True)
    near = types.SimpleNamespace(period_stats={"14d": near_stats}, display_unit=unit)
    entity = se.GlucoCraftPeriodMean.__new__(se.GlucoCraftPeriodMean)
    entity.coordinator, entity._period = near, "14d"
    assert entity.native_value == state  # shown the same: the same state
    assert se._state_glucose(None, unit) is None


# ------------------------------------------------------------------------------------------------ the precision
def test_the_glucose_sensors_suggest_no_unit():
    assert "suggested_unit_of_measurement" not in (PKG / "sensor.py").read_text(encoding="utf-8")
    for cls in GLUCOSE_CLASSES:
        own = {
            k: v for klass in cls.__mro__ if klass.__module__.startswith("glucocraft") for k, v in vars(klass).items()
        }
        assert "_attr_suggested_unit_of_measurement" not in own, cls.__name__
        assert own["_attr_suggested_display_precision"] == 0, cls.__name__  # whole mg/dL


@pytest.mark.usefixtures("tz_patch")
@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
def test_no_instance_suggests_a_unit(tmp_path, unit):
    run = build(tmp_path, entry(unit=unit), days=1)
    for e in run.entities():
        assert "_attr_suggested_unit_of_measurement" not in e.__dict__, e._attr_unique_id


def _precision(base_precision: int, default_unit: str, unit: str) -> int:
    """Home Assistant's display precision of a converted sensor (sensor/__init__.py, _get_adjusted_display_precision
    and _calculate_precision_from_ratio, with the BloodGlucoseConcentrationConverter ratios), as a reference."""
    ratio = {"mg/dL": 18.016, "mmol/L": 1.0}
    if default_unit == unit:
        return base_precision
    log = math.log10(ratio[default_unit] / ratio[unit])
    log = math.floor(log) if log > 0 else math.ceil(log)
    return max(0, base_precision + log) or base_precision


def test_the_precision_follows_the_unit_from_the_native_unit():
    # without a suggested unit the default unit is the native mg/dL: 120 mg/dL, and 6.4 mmol/L with one decimal
    assert _precision(0, "mg/dL", "mg/dL") == 0
    assert _precision(0, "mg/dL", "mmol/L") == 1
    # suggesting the display unit (the earlier code) made mmol/L the default unit, so the precision stayed 0 ("6")
    assert _precision(0, "mmol/L", "mmol/L") == 0


# ------------------------------------------------------------------------------------------------ the registry
def unit(reg: FakeRegistry, key: str) -> str | None:
    """The unit in the entity settings of the GlucoCraft sensor `key`."""
    return (reg.entities[f"sensor.glucocraft_{key}"].options.get("sensor") or {}).get("unit_of_measurement")


@pytest.fixture
def registry(monkeypatch):
    def make(options):
        """The sensors `key: options` of the installation "e" (unique ids e_<key>)."""
        reg = FakeRegistry(
            {f"sensor.glucocraft_{key}": {"unique_id": f"e_{key}", "options": o} for key, o in options.items()}
        )
        monkeypatch.setattr(ur, "er", types.SimpleNamespace(async_get=lambda hass: reg))
        return reg

    return make


def test_keep_display_unit_on_a_fresh_installation(registry):
    reg = registry({k: {} for k in ur.glucose_sensor_keys()})
    assert ur.keep_display_unit(None, "e", "mmol/L") == len(ur.glucose_sensor_keys())
    assert all(unit(reg, k) == "mmol/L" for k in ur.glucose_sensor_keys())
    # idempotent: a second setup writes nothing
    reg.updates.clear()
    assert ur.keep_display_unit(None, "e", "mmol/L") == 0 and reg.updates == []


def test_keep_display_unit_never_changes_a_unit_in_use(registry):
    reg = registry(
        {
            "current_glucose": {"sensor": {"unit_of_measurement": "mmol/L", "suggested_display_precision": 0}},
            "14d_mean": {"sensor": {"unit_of_measurement": "mg/dL"}},  # chosen in the entity settings
            "14d_max": {},  # a feature's sensor created again
        }
    )
    assert ur.keep_display_unit(None, "e", "mmol/L") == 1
    assert unit(reg, "current_glucose") == "mmol/L" and unit(reg, "14d_mean") == "mg/dL"
    assert unit(reg, "14d_max") == "mmol/L"
    # the other sensor options are kept
    assert reg.entities["sensor.glucocraft_current_glucose"].options["sensor"]["suggested_display_precision"] == 0


def test_apply_display_unit_still_changes_every_sensor(registry):
    reg = registry({"current_glucose": {"sensor": {"unit_of_measurement": "mg/dL"}}, "14d_min": {}})
    assert ur.apply_display_unit(None, "e", "mmol/L") == 2
    assert unit(reg, "current_glucose") == "mmol/L" and unit(reg, "14d_min") == "mmol/L"
    assert ur.apply_display_unit(None, "e", "mmol/L") == 0


def test_setup_writes_the_unit_after_the_platforms(monkeypatch):
    init = _load_init()
    order: list[str] = []

    async def step(name, value=None):
        order.append(name)
        return value

    coordinator = types.SimpleNamespace(async_track_unit_setting=lambda: order.append("track"))
    monkeypatch.setattr(init, "async_setup_language", lambda hass: step("language"))
    monkeypatch.setattr(init, "_async_adopt_kept", lambda hass, e: step("kept"))
    monkeypatch.setattr(init, "_async_create_coordinator", lambda hass, e: step("coordinator", coordinator))
    monkeypatch.setattr(init, "keep_display_unit", lambda hass, entry_id, unit: order.append(("unit", entry_id, unit)))
    monkeypatch.setattr(init, "_async_setup_frontend", lambda *a: step("frontend"))
    monkeypatch.setattr(init, "_setup_notifications", lambda *a: order.append("notifications"))
    monkeypatch.setattr(init, "_async_setup_notices", lambda *a: step("notices"))
    hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(async_forward_entry_setups=lambda e, p: step("platforms")), data={}
    )
    e = types.SimpleNamespace(entry_id="e1", data={C.CONF_UNIT: "mmol/L"})
    assert asyncio.run(init.async_setup_entry(hass, e)) is True
    assert order.index("platforms") < order.index(("unit", "e1", "mmol/L")) < order.index("frontend")
    assert order.index("track") < order.index(("unit", "e1", "mmol/L"))  # the listener refreshes the other states
    assert order.index("language") < order.index("kept") < order.index("coordinator")  # kept readings adopted first


# ------------------------------------------------------------------------------------------------ attributes the recorder does not keep
EXPECTED_UNRECORDED = {
    "sensor_expiry": {"remaining_hours", "remaining_days", "expiry_value"},
    "current_glucose": {"limit_since", "last_measured_mgdl", "last_measured_ts"},
    "last_reading": {
        "last_reading_ts",
        "last_reading_mgdl",
        "last_reading_limit",
        "reading_count",
        "primary_last_measurement",
        "secondary_last_measurement",
    },
    "trend_arrow": {"angle"},
    "weekly_summary": {"weeks", "computed_at"},
}


def _unrecorded(e) -> frozenset | None:
    for klass in type(e).__mro__:
        if klass.__module__.startswith("glucocraft") and "_unrecorded_attributes" in vars(klass):
            return vars(klass)["_unrecorded_attributes"]
    return None


def test_configuration_attributes_are_not_recorded():
    assert se.GlucoCraftConfiguration._unrecorded_attributes == frozenset({se.MATCH_ALL})


@pytest.mark.usefixtures("tz_patch")
def test_the_active_source_is_unknown_until_a_source_delivers(tmp_path):
    """No source name yet (after the setup, the imported history names none): Home Assistant's own unknown (None),
    never a text of GlucoCraft's; the name once a source delivers."""
    run = build(tmp_path)
    run.c.active_source = ""
    assert run.sensor("active_source")["state"] is None
    run.c.active_source = "Test CGM"
    assert run.sensor("active_source")["state"] == "Test CGM"


def test_churning_attributes_are_not_recorded_and_every_exclusion_is_published(tmp_path):
    now = int(time.time())
    expiry = datetime.fromtimestamp(now + 3 * 86400, UTC).isoformat()
    run = build(
        tmp_path,
        entry(**{C.CONF_SENSOR_EXPIRY_ENTITY: "sensor.expiry"}),
        now=now,
        states={"sensor.expiry": state(expiry)},
    )
    key_of = {e: e._attr_unique_id.split("_", 1)[1] for e in run.entities()}
    for e, key in key_of.items():
        excluded = _unrecorded(e)
        if key in EXPECTED_UNRECORDED:
            assert excluded == frozenset(EXPECTED_UNRECORDED[key]), key
        if not excluded or se.MATCH_ALL in excluded:
            continue
        published = set(run.sensor(key)["attributes"])
        assert excluded <= published, (key, excluded - published)  # no exclusion that matches nothing
    # the period statistics have no exclusion of their own: their attributes are the data the cards read
    assert "_unrecorded_attributes" not in vars(se.GlucoCraftPeriodBase)
    assert "_unrecorded_attributes" not in vars(bs.GlucoCraftUsingCustomRanges)


@pytest.mark.usefixtures("tz_patch")
def test_using_custom_ranges_publishes_the_consensus_ranges_from_const(tmp_path):
    attrs = build(tmp_path, days=1).sensor("using_custom_ranges")["attributes"]
    consensus = {k: v for k, v in attrs.items() if k.startswith("consensus_")}
    assert consensus == {
        "consensus_low": 70,
        "consensus_high": 180,
        "consensus_very_low": 54,
        "consensus_very_high": 250,
        "consensus_tight_low": 70,
        "consensus_tight_high": 140,
    }
    assert 'consensus_low": 70' not in (PKG / "binary_sensor.py").read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------------------ the second source, the action errors and the notify actions
def _hass(states=("sensor.cgm", "sensor.cgm2")):
    st = types.SimpleNamespace(attributes={"unit_of_measurement": "mg/dL"})
    return types.SimpleNamespace(states=types.SimpleNamespace(get=lambda e: st if e in states else None))


def test_second_source_cannot_be_the_primary_source(monkeypatch):
    monkeypatch.setattr(cf, "is_glucocraft_entity", lambda hass, e: False)
    same = {C.CONF_GLUCOSE_ENTITY: "sensor.cgm", C.CONF_SECONDARY_ENTITY: "sensor.cgm"}
    errors = cf._source_errors(_hass(), same)
    assert errors == {C.CONF_SECONDARY_ENTITY: "secondary_same_as_primary"}
    # shown above the collapsed section, which the step then opens
    assert cf._section_errors(errors) == {cf.SECTION_SECOND_SOURCE: "secondary_same_as_primary"}
    assert cf._source_errors(_hass(), {**same, C.CONF_SECONDARY_ENTITY: "sensor.cgm2"}) == {}
    assert cf._source_errors(_hass(), {**same, C.CONF_SECONDARY_ENTITY: None}) == {}
    for step in ("config", "options"):
        assert "primary glucose source itself" in STRINGS[step]["error"]["secondary_same_as_primary"]


def test_notify_targets_read_the_notify_domain_only(monkeypatch):
    monkeypatch.setattr(fs.selector, "SelectOptionDict", dict)
    asked = []

    def for_domain(domain):
        asked.append(domain)
        return {"mobile_app_phone": None, "persistent_notification": None, "send_message": None, "notify": None}

    hass = types.SimpleNamespace(services=types.SimpleNamespace(async_services_for_domain=for_domain))
    values = [o["value"] for o in fs._notify_targets(hass, ["mobile_app_old"])]
    # the bare notify.notify is not offered (it duplicates the phones listed one by one) ...
    assert values == ["mobile_app_old", "mobile_app_phone"] and asked == ["notify"]
    # ... but a saved choice of it is kept
    assert "notify" in [o["value"] for o in fs._notify_targets(hass, ["notify"])]


def test_the_delivery_test_logs_no_notify_action_name(caplog):
    async def call(domain, name, data, **kwargs):
        raise RuntimeError("no push channel")

    hass = types.SimpleNamespace(
        services=types.SimpleNamespace(has_service=lambda d, n: n == "mobile_app_annas_phone", async_call=call)
    )
    with caplog.at_level(logging.WARNING):
        asyncio.run(cf._send_phone_test(hass, ["mobile_app_annas_phone", "mobile_app_bens_phone"]))
    text = caplog.text
    assert "annas" not in text and "bens" not in text
    assert "notify action 1 of 2 failed" in text and "notify action 2 of 2 is not available" in text


def test_the_delivery_test_logs_the_error_class_not_its_text(caplog):
    """The Companion app's errors name the phone (its device name, its webhook id): only the class is logged."""

    class HomeAssistantError(Exception):
        pass

    async def call(domain, name, data, **kwargs):
        raise HomeAssistantError("Device Phone of Jane Doe is not connected to local push notifications (webhook 1a2b)")

    hass = types.SimpleNamespace(services=types.SimpleNamespace(has_service=lambda d, n: True, async_call=call))
    with caplog.at_level(logging.WARNING):
        asyncio.run(cf._send_phone_test(hass, ["mobile_app_phone"]))
    assert "Jane" not in caplog.text and "1a2b" not in caplog.text
    assert "notify action 1 of 1 failed: HomeAssistantError" in caplog.text


def test_a_failed_setup_shuts_the_coordinator_down(monkeypatch):
    """A step after the coordinator's start fails: the coordinator is shut down (its listeners and timer stop) and the
    error is raised, so that Home Assistant marks the setup as failed and a retry starts one coordinator only."""
    init = _load_init()
    shut = []

    async def shutdown():
        shut.append(True)

    async def step(value=None):
        return value

    async def broken(*a):
        raise RuntimeError("Handler is already defined!")

    coordinator = types.SimpleNamespace(async_track_unit_setting=lambda: None, async_shutdown=shutdown)
    monkeypatch.setattr(init, "async_setup_language", lambda hass: step())
    monkeypatch.setattr(init, "_async_adopt_kept", lambda hass, e: step())
    monkeypatch.setattr(init, "_async_create_coordinator", lambda hass, e: step(coordinator))
    monkeypatch.setattr(init, "keep_display_unit", lambda *a: 0)
    monkeypatch.setattr(init, "_async_setup_frontend", broken)
    hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(async_forward_entry_setups=lambda e, p: step()), data={}
    )
    e = types.SimpleNamespace(entry_id="e1", data={C.CONF_UNIT: "mg/dL"})
    with pytest.raises(RuntimeError):
        asyncio.run(init.async_setup_entry(hass, e))
    assert shut == [True]


def test_a_failed_coordinator_start_shuts_it_down(monkeypatch):
    """The same inside the coordinator's own start: a failure after its listeners started stops them again."""
    init = _load_init()
    shut = []

    class Coordinator:
        def __init__(self, *a, **kw):
            pass

        async def async_setup(self):
            pass

        async def async_shutdown(self):
            shut.append(True)

    class Reports:
        def __init__(self, *a):
            pass

        async def async_load(self):
            raise OSError("the report store could not be read")

    async def integration(hass, domain):
        return types.SimpleNamespace(version="0.0.6")

    monkeypatch.setattr(init, "GlucoCraftCoordinator", Coordinator)
    monkeypatch.setattr(init, "GlucoCraftReports", Reports)
    monkeypatch.setattr(init, "async_get_integration", integration)
    with pytest.raises(OSError):
        asyncio.run(init._async_create_coordinator(None, types.SimpleNamespace(entry_id="e1", data={})))
    assert shut == [True]


def test_a_deleted_panel_loses_its_offline_notice_and_is_forgotten(monkeypatch):
    """Panels are the user's own devices: when a panel's device is deleted from Home Assistant, its offline notice
    is removed (and its timer stopped), it no longer counts in the Maintenance status, and the store of panels already
    announced forgets it, also when it was deleted while GlucoCraft was not running."""
    from glucocraft import panel_notice as pn
    from glucocraft.maintenance import MaintenanceIssues

    from ha_stub import STORE_DATA

    dev = types.SimpleNamespace(id="d1", model="panel", name="Hall Panel", name_by_user=None)
    found = {"binary_sensor.hall_status": dev}
    created, dismissed, cancelled, listeners = [], [], [], {}
    monkeypatch.setattr(pn, "_panel_status_entities", lambda hass: dict(found))
    monkeypatch.setattr(pn, "async_track_state_change_event", lambda *a: lambda: None)
    monkeypatch.setattr(pn, "async_call_later", lambda hass, delay, fn: fn(None) or (lambda: cancelled.append(1)))
    monkeypatch.setattr(pn.persistent_notification, "async_create", lambda hass, msg, **kw: created.append(kw))
    monkeypatch.setattr(pn.persistent_notification, "async_dismiss", lambda hass, nid: dismissed.append(nid))
    hass = types.SimpleNamespace(
        bus=types.SimpleNamespace(async_listen=lambda kind, fn: listeners.setdefault(kind, []).append(fn)),
        states=types.SimpleNamespace(get=lambda e: types.SimpleNamespace(state="off")),
        async_create_task=lambda coro: asyncio.run(coro),
    )
    entry = types.SimpleNamespace(entry_id="e1", data={}, async_on_unload=lambda f: None)
    maintenance = MaintenanceIssues(hass, lambda: None)
    asyncio.run(pn.async_setup_panel_offline(hass, entry, maintenance))
    assert created and created[-1]["notification_id"] == "glucocraft_panel_offline_d1"
    assert maintenance.issues() == [{"kind": "panel_offline", "name": "Hall Panel"}]
    # the device deleted: its Status entity leaves the registry with it
    found.clear()
    for fn in listeners[pn.dr.EVENT_DEVICE_REGISTRY_UPDATED]:
        fn(types.SimpleNamespace(data={"action": "remove", "device_id": "d1"}))
    assert dismissed == ["glucocraft_panel_offline_d1"] and maintenance.issues() == []

    # the store of panels already announced forgets the deleted panel
    devices = {"d1": dev, "d2": types.SimpleNamespace(id="d2", model="panel", name="Panel B", name_by_user=None)}
    monkeypatch.setattr(pn, "_panels", lambda hass: dict(devices))
    listeners.clear()
    key = "glucocraft.e1_panels"
    STORE_DATA[key] = {"known": ["d0", "d1", "d2"]}  # d0 deleted while GlucoCraft was not running
    asyncio.run(pn.async_setup_panel_notice(hass, entry))
    assert STORE_DATA[key] == {"known": ["d1", "d2"]}
    devices.pop("d1")
    for fn in listeners[pn.dr.EVENT_DEVICE_REGISTRY_UPDATED]:
        fn(types.SimpleNamespace(data={"action": "remove", "device_id": "d1"}))
    assert STORE_DATA[key] == {"known": ["d2"]}
    assert len(created) == 1  # no notice for the deletion


def test_a_renamed_panel_shows_its_new_name_in_the_offline_notice_and_maintenance(monkeypatch):
    """A panel renamed in Home Assistant while its offline notice is shown: the notice and the Maintenance status take
    the new name at once (the device registry update), not only after GlucoCraft reloads."""
    from glucocraft import panel_notice as pn
    from glucocraft.maintenance import MaintenanceIssues

    found = {"binary_sensor.hall_status": types.SimpleNamespace(id="d1", model="panel", name="Panel A",
                                                                 name_by_user=None)}  # fmt: skip
    created, listeners, timers = [], {}, []
    monkeypatch.setattr(pn, "_panel_status_entities", lambda hass: dict(found))
    monkeypatch.setattr(pn, "async_track_state_change_event", lambda *a: lambda: None)
    monkeypatch.setattr(pn, "async_call_later", lambda hass, delay, fn: timers.append(fn) or (lambda: None))
    monkeypatch.setattr(pn.persistent_notification, "async_create", lambda hass, msg, **kw: created.append((msg, kw)))
    monkeypatch.setattr(pn.persistent_notification, "async_dismiss", lambda hass, nid: None)
    # the device registry knows d1 as a GlucoCraft panel (a device update of another device is left alone)
    panel = types.SimpleNamespace(manufacturer="glucocraft", identifiers={("esphome", "d1")})
    devices = types.SimpleNamespace(async_get=lambda device_id: panel if device_id == "d1" else None)
    monkeypatch.setattr(pn.dr, "async_get", lambda hass: devices, raising=False)
    hass = types.SimpleNamespace(
        bus=types.SimpleNamespace(async_listen=lambda kind, fn: listeners.setdefault(kind, []).append(fn)),
        states=types.SimpleNamespace(get=lambda e: types.SimpleNamespace(state="off")),
    )
    entry = types.SimpleNamespace(entry_id="e1", data={}, async_on_unload=lambda f: None)
    maintenance = MaintenanceIssues(hass, lambda: None)
    asyncio.run(pn.async_setup_panel_offline(hass, entry, maintenance))
    timers.pop()(None)  # the 10 minutes have passed
    assert "Panel A" in created[-1][0] and maintenance.issues()[0]["name"] == "Panel A"
    found["binary_sensor.hall_status"] = types.SimpleNamespace(
        id="d1", model="panel", name="Panel A", name_by_user="Hall Panel"
    )
    for fn in listeners[pn.dr.EVENT_DEVICE_REGISTRY_UPDATED]:
        fn(types.SimpleNamespace(data={"action": "update", "device_id": "d1"}))
    assert "Hall Panel" in created[-1][0] and created[-1][1]["notification_id"] == "glucocraft_panel_offline_d1"
    assert maintenance.issues() == [{"kind": "panel_offline", "name": "Hall Panel"}]
    count = len(created)
    for fn in listeners[pn.dr.EVENT_DEVICE_REGISTRY_UPDATED]:  # an update without a new name: nothing again
        fn(types.SimpleNamespace(data={"action": "update", "device_id": "d1"}))
    assert len(created) == count


@pytest.mark.parametrize("offline_min, delay", [(12, 0), (3, 420), (None, 600)])
def test_the_time_already_offline_counts_after_a_reload(monkeypatch, offline_min, delay):
    """A reload of GlucoCraft (every saved setting) does not start the 10 minutes again: the time since the panel's
    Status entity changed counts, so the notice and the Maintenance status follow at once for a panel long offline."""
    from datetime import timedelta

    from glucocraft import panel_notice as pn

    dev = types.SimpleNamespace(id="d1", model="panel", name="Hall Panel", name_by_user=None)
    delays = []
    monkeypatch.setattr(pn, "_panel_status_entities", lambda hass: {"binary_sensor.hall_status": dev})
    monkeypatch.setattr(pn, "async_track_state_change_event", lambda *a: lambda: None)
    monkeypatch.setattr(pn, "async_call_later", lambda hass, d, fn: delays.append(round(d)) or (lambda: None))
    since = (
        datetime.fromtimestamp(time.time(), UTC) - timedelta(minutes=offline_min) if offline_min is not None else None
    )
    state = types.SimpleNamespace(state="unavailable", **({"last_changed": since} if since else {}))
    hass = types.SimpleNamespace(
        bus=types.SimpleNamespace(async_listen=lambda kind, fn: lambda: None),
        states=types.SimpleNamespace(get=lambda e: state),
    )
    entry = types.SimpleNamespace(entry_id="e1", data={}, async_on_unload=lambda f: None)
    asyncio.run(pn.async_setup_panel_offline(hass, entry))
    assert delays == [delay]


def test_the_panel_offline_watcher_rescans_only_for_status_entities(monkeypatch):
    """An entity registry update rescans the devices only when it can concern a panel's Status entity."""
    from glucocraft import panel_notice as pn

    scans = []
    listeners = {}
    monkeypatch.setattr(pn, "_panel_status_entities", lambda hass: scans.append(1) or {})
    monkeypatch.setattr(pn, "async_track_state_change_event", lambda *a: lambda: None)
    hass = types.SimpleNamespace(
        bus=types.SimpleNamespace(async_listen=lambda kind, fn: listeners.setdefault(kind, fn) and (lambda: None))
    )
    entry = types.SimpleNamespace(entry_id="e1", data={}, async_on_unload=lambda f: None)
    asyncio.run(pn.async_setup_panel_offline(hass, entry))
    on_entity = listeners[pn.er.EVENT_ENTITY_REGISTRY_UPDATED]
    before = len(scans)
    for entity_id in ("sensor.room_temperature", "light.hall", "sensor.glucocraft_14d_mean"):
        on_entity(types.SimpleNamespace(data={"action": "update", "entity_id": entity_id}))
    assert len(scans) == before  # no device scan for any other entity
    on_entity(types.SimpleNamespace(data={"action": "create", "entity_id": "binary_sensor.hall_panel_status"}))
    on_entity(
        types.SimpleNamespace(
            data={"action": "update", "entity_id": "binary_sensor.hall", "old_entity_id": "binary_sensor.hall_status"}
        )
    )
    assert len(scans) == before + 2


# ------------------------------------------------------------------------------------------------ a failed print or export
class Failed(Exception):
    """A stand-in for HomeAssistantError that keeps its translation key and placeholders."""

    def __init__(self, **kw):
        super().__init__(kw.get("translation_key"))
        self.kw = kw


def _action_hass(tmp_path):
    async def run(fn, *args):
        return fn(*args)

    async def call(*a, **kw):
        pass

    return types.SimpleNamespace(
        config=types.SimpleNamespace(path=lambda *p: str(Path(tmp_path, *p)), time_zone="UTC"),
        async_add_executor_job=run,
        services=types.SimpleNamespace(async_call=call),
    )


def test_a_print_whose_pdf_cannot_be_made_is_an_error(monkeypatch, tmp_path):
    init = _load_init()
    monkeypatch.setattr(init, "HomeAssistantError", Failed)

    async def no_reportlab(*args):
        raise ImportError("No module named 'reportlab'")

    coordinator = types.SimpleNamespace(
        custom_ranges_and_goals=False, reports=types.SimpleNamespace(async_print=no_reportlab)
    )
    monkeypatch.setattr(init, "_coordinator_for", lambda hass: coordinator)
    with pytest.raises(Failed) as err:
        asyncio.run(init._handle_print(_action_hass(tmp_path), types.SimpleNamespace(data={})))
    assert err.value.kw["translation_key"] == "print_pdf_failed"
    assert err.value.kw["translation_placeholders"] == {"reason": "No module named 'reportlab'"}
    assert isinstance(err.value.__cause__, ImportError)
    assert "{reason}" in STRINGS["exceptions"]["print_pdf_failed"]["message"]


def test_an_export_that_cannot_be_written_is_an_error(monkeypatch, tmp_path):
    init = _load_init()
    monkeypatch.setattr(init, "HomeAssistantError", Failed)

    def locked(fn):
        raise OSError(28, "No space left on device")

    from glucocraft.maintenance import MaintenanceIssues

    coordinator = types.SimpleNamespace(
        reports=types.SimpleNamespace(lab_results=[]),
        display_unit="mg/dL",
        storage=types.SimpleNamespace(readings=[]),
        locked=locked,
        maintenance=MaintenanceIssues(None, lambda: None),
    )
    monkeypatch.setattr(init, "_coordinator_for", lambda hass: coordinator)
    with pytest.raises(Failed) as err:
        asyncio.run(init._handle_export(_action_hass(tmp_path), types.SimpleNamespace(data={})))
    assert err.value.kw["translation_key"] == "export_failed"
    # the reason in plain words: the system's error text (with its paths) goes only to the log
    assert coordinator.maintenance.issues() == [{"kind": "csv_export", "reason": "the disk is full"}]
    assert err.value.kw["translation_placeholders"] == {"reason": "the disk is full"}
    assert "{reason}" in STRINGS["exceptions"]["export_failed"]["message"]
    assert init._reason(OSError()) == "OSError"  # an error without a text: its class name
