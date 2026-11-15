# License: AGPL-3.0-only (LICENSE).
"""The source settings: the glucose source, the sensor expiry entity, the second source and the two time attributes,
and the sensor's lower and upper limit per source, with the limits in both units; a stored key that is no setting (a
trend direction entity) is ignored.

Each setting is stored in the entry; the tests read its effect on the coordinator, the entities, the Configuration
attributes (the dashboard's input), the panel feed, the HTTP API, the summaries, the CSV export and the Repairs notices.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pytest
from glucocraft import const as C
from glucocraft import coordinator as co

import ha_stub
from test_settings_harness import (
    PRIMARY,
    SECONDARY,
    build,
    entry,
    make_hass,
    series,
    state,
    tz_patch,  # noqa: F401 (the fixture)
)

pytestmark = pytest.mark.usefixtures("tz_patch")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------------------------------------ the glucose source
def test_the_glucose_source_is_the_only_primary(tmp_path):
    now = int(time.time())
    run = build(tmp_path, entry(**{C.CONF_GLUCOSE_ENTITY: PRIMARY}), readings=series(now, 1, age=600))
    n = run.c.storage.reading_count
    run.push("sensor.other", 123, at=now - 30)  # not a source: ignored
    assert run.c.storage.reading_count == n
    run.push(PRIMARY, 123, at=now - 30)
    assert run.c.storage.reading_count == n + 1 and run.c.current_glucose == 123.0
    cfg = run.config()
    assert cfg["primary_source"]["entity"] == PRIMARY and cfg["primary_source"]["in_use"] is True
    assert cfg["secondary_source"] is None and cfg["sources"]["primary"] == cfg["primary_source"]
    assert run.sensor("active_source")["attributes"]["source_entity"] == PRIMARY
    assert run.sensor("active_source")["attributes"]["second_source_active"] is False


def test_the_glucose_source_s_name_in_the_report(tmp_path):
    run = build(tmp_path)
    run.hass.states.d[PRIMARY] = state(120, friendly_name="Libre 3 (test)", unit_of_measurement="mg/dL")
    assert run.config()["primary_source"]["name"] == "Libre 3 (test)"
    assert run.report(14)["sources"] == ["Libre 3 (test)"]


def test_the_report_names_both_sources_and_whether_the_second_has_measurement_times(tmp_path):
    """The report's page header and About This Report name each source by its name, else its entity id, the primary
    first; a second source without measurement times is marked so (About This Report states when it is used)."""
    run = build(tmp_path, entry(**{C.CONF_SECONDARY_ENTITY: SECONDARY}))
    run.hass.states.d[PRIMARY] = state(120, friendly_name="Libre 3 (test)")
    run.hass.states.d[SECONDARY] = state(121)  # no name and no measurement time
    rep = run.report(14)
    assert rep["sources"] == ["Libre 3 (test)", SECONDARY] and rep["second_source_timed"] is False
    run.hass.states.d[SECONDARY] = state(121, reading_time=_iso(time.time() - 60))
    assert run.report(14)["second_source_timed"] is True
    run.hass.states.d[SECONDARY] = state("unavailable")  # nothing to tell: no clause
    assert run.report(14)["second_source_timed"] is None
    assert build(tmp_path / "one").report(14)["second_source_timed"] is None  # one source


def test_a_glucocraft_entity_as_the_source_is_never_used(tmp_path, monkeypatch):
    run = build(tmp_path, readings=[])
    monkeypatch.setattr(run.c, "_is_own_entity", lambda e: e == PRIMARY)
    flagged = []
    monkeypatch.setattr(run.c, "_flag_own_source", flagged.append)
    run.push(PRIMARY, 120, at=int(time.time()) - 30)
    assert run.c.storage.reading_count == 0 and flagged == [PRIMARY]


def _restart_after_a_source_change(tmp_path, monkeypatch):
    """Readings stored with sensor.old as the source; then the entry names sensor.new and GlucoCraft restarts."""
    first = build(tmp_path, entry(**{C.CONF_GLUCOSE_ENTITY: "sensor.old"}))
    first.c.storage.save()
    before = [(r.timestamp, r.raw) for r in first.c.storage.readings]
    ha_stub.STORE_DATA["glucocraft.t_state"] = {
        "active_source_entity": "sensor.old",
        "active_source": "Old CGM",
        "warmup_started_at": 0,
        "learned_limits": {"sensor.old": {"upper": 400}},
    }

    async def no_import(*args, **kwargs):
        return 0

    monkeypatch.setattr(co, "async_import_history", no_import)
    hass = make_hass(tmp_path)
    c = co.GlucoCraftCoordinator(hass, "t", entry(**{C.CONF_GLUCOSE_ENTITY: "sensor.new"}))
    c.hass = hass
    asyncio.run(c.async_setup())
    return c, before


def test_changing_the_source_keeps_the_history(tmp_path, monkeypatch):
    c, before = _restart_after_a_source_change(tmp_path, monkeypatch)
    assert [(r.timestamp, r.raw) for r in c.storage.readings] == before  # the readings belong to the entry
    assert c.period_stats["14d"] is not None and c.display_status == "current"
    # the limit the earlier source showed is not applied to the new one
    assert c.configuration()["primary_source"]["upper_limit"] == 500


def test_changing_the_source_shows_the_new_source_in_use(tmp_path, monkeypatch):
    c, _ = _restart_after_a_source_change(tmp_path, monkeypatch)
    assert c.second_source_active is False
    assert c.active_source_entity in ("", "sensor.new")
    assert c.configuration()["secondary_source"] is None


# ------------------------------------------------------------------------------------------------ the CGM's own trend
def test_a_stored_trend_direction_entity_is_ignored(tmp_path):
    """A stored trend direction entity (direction_entity) is no setting: its value is ignored (no listener, no
    attribute); GlucoCraft's own trend is used for every source."""
    run = build(tmp_path, entry(direction_entity="sensor.direction"))
    before = dict(run.sensor("trend_arrow")["attributes"])
    run.push("sensor.direction", "FortyFiveUp", unit=None, attr=None)
    attrs = run.sensor("trend_arrow")["attributes"]
    assert attrs == before and "source_direction" not in attrs
    assert attrs["description"] == run.c.trend_description


# ------------------------------------------------------------------------------------------------ expiry entity
@pytest.mark.parametrize(
    "hours,status,expiry_state,line",
    [
        (72, "current", "not_expired", "Sensor expires in 3 days"),
        (30, "current", "not_expired", "Sensor expires in 1 day 6 hours"),
        (12, "expiring_soon", "expiring_soon", "Sensor expires in 12 hours"),
        (-1, "expired", "expired", "Sensor expired"),
    ],
)
def test_the_sensor_expiry_entity(tmp_path, hours, status, expiry_state, line):
    expiry = _iso(time.time() + hours * 3600 + 120)
    run = build(
        tmp_path,
        entry(**{C.CONF_SENSOR_EXPIRY_ENTITY: "sensor.expiry"}),
        states={"sensor.expiry": state(expiry)},
    )
    assert run.c.display_status == status
    out = run.sensor("sensor_expiry")
    assert out["state"] == expiry_state and out["attributes"]["expires_at"] == expiry
    # only the expiry: the time since the last reading is on No current data, its time on Last reading time
    assert not {"no_current_data_hours", "last_reading"} & out["attributes"].keys()
    assert out["attributes"]["expiry_format"] == "datetime"
    assert run.sensor("status")["state"] == status
    assert run.config()["expiry_entity"] == "sensor.expiry"
    # the daily summary reads the coordinator's expiry (coordinator.expires_at), as the Sensor expiry entity does
    assert line in run.daily()
    # expired while readings still arrive: the trend and the word follow the data (not the status), and the daily
    # summary gives the reading, not "No current data"
    if status == "expired":
        assert run.c.no_current_data is False and run.c.trend_arrow != "" and run.c.display_word != ""
        assert "No current data" not in run.daily()


def test_without_an_expiry_entity(tmp_path):
    run = build(tmp_path, entry(**{C.CONF_SENSOR_EXPIRY_ENTITY: None}))
    assert run.sensor("sensor_expiry")["state"] == "not_configured"
    assert run.c.display_status == "current" and run.config()["expiry_entity"] is None
    assert "Sensor expire" not in run.daily()


def test_a_new_sensor_seen_on_the_expiry_entity_starts_a_warm_up(tmp_path):
    run = build(tmp_path, entry(**{C.CONF_SENSOR_EXPIRY_ENTITY: "sensor.expiry"}))
    run.push("sensor.expiry", _iso(time.time() + 2 * 86400), unit=None, attr=None)  # the baseline
    assert run.c.display_status == "current"
    run.push("sensor.expiry", _iso(time.time() + 14 * 86400), unit=None, attr=None)  # a new sensor
    assert run.c.display_status == "warm_up" and run.sensor("sensor_warmup")["state"] is True
    assert run.c.trend_arrow == "" and run.c.display_word == ""


# ------------------------------------------------------------------------------------------------ the second source
def _gap_run(tmp_path, data):
    """The primary silent for 30 minutes: its newest reading 1800 s old."""
    now = int(time.time())
    return build(tmp_path, data, readings=series(now, 1, age=1800)), now


@pytest.mark.parametrize("secondary", [None, SECONDARY])
def test_second_source_on_and_off(tmp_path, secondary):
    run, now = _gap_run(tmp_path, entry(**{C.CONF_SECONDARY_ENTITY: secondary}))
    assert run.c.display_status == "no_current_data"
    n = run.c.storage.reading_count
    run.push(SECONDARY, 140, at=now - 30)
    cfg = run.config()
    if secondary is None:
        assert run.c.storage.reading_count == n and cfg["secondary_source"] is None
        assert run.c.display_status == "no_current_data"
        return
    assert run.c.storage.reading_count == n + 1 and run.c.current_glucose == 140.0
    assert run.c.storage.readings[-1].source == C.SOURCE_SECONDARY
    assert run.c.display_status == "current" and run.c.second_source_active is True
    assert run.sensor("active_source")["attributes"]["second_source_active"] is True
    assert cfg["secondary_source"]["entity"] == SECONDARY and cfg["secondary_source"]["in_use"] is True
    assert cfg["primary_source"]["in_use"] is False
    assert run.csv().strip().splitlines()[-1].split(",")[5] == "secondary"
    # the primary's next reading takes over again
    run.push(PRIMARY, 150, at=now - 10)
    assert run.c.current_glucose == 150.0 and run.c.second_source_active is False


def test_a_second_source_equal_to_the_primary_is_none(tmp_path):
    run = build(tmp_path, entry(**{C.CONF_SECONDARY_ENTITY: PRIMARY}))
    assert run.c._secondary_entity is None and run.config()["secondary_source"] is None


def test_the_secondary_never_fills_while_the_primary_is_healthy(tmp_path):
    now = int(time.time())
    run = build(tmp_path, entry(**{C.CONF_SECONDARY_ENTITY: SECONDARY}), readings=series(now, 1, age=60))
    n = run.c.storage.reading_count
    run.push(SECONDARY, 200, at=now - 30)
    assert run.c.storage.reading_count == n and run.c.current_glucose != 200.0


# ------------------------------------------------------------------------------------------------ time attributes
@pytest.mark.parametrize(
    "setting,attr,from_source",
    [
        ("", "measurement_timestamp", True),  # automatic: a known attribute
        ("", "dateString", True),
        ("", "my_time", False),  # automatic: an unknown attribute is not looked at
        ("my_time", "my_time", True),  # the attribute named in the setting
        ("my_time", "measurement_timestamp", False),  # named: only that one
        ("  my_time  ", "my_time", True),  # stored with spaces
    ],
)
def test_the_primary_time_attribute(tmp_path, setting, attr, from_source):
    run = build(tmp_path, entry(**{C.CONF_PRIMARY_TIME_ATTR: setting}))
    at = int(time.time()) - 120
    ts, src = run.c._reading_time(PRIMARY, state(100, **{attr: _iso(at)}))
    assert src is from_source and (ts == at) is from_source
    assert run.config()["primary_source"]["time_attribute"] == (setting.strip() or "auto")


@pytest.mark.parametrize("secondary", [None, SECONDARY])
@pytest.mark.parametrize("sec_attr", ["", "reading_time", "other"])
def test_the_secondary_time_attribute(tmp_path, secondary, sec_attr):
    data = entry(**{C.CONF_SECONDARY_ENTITY: secondary, C.CONF_SECONDARY_TIME_ATTR: sec_attr})
    run = build(tmp_path, data)
    at = int(time.time()) - 120
    _ts, src = run.c._reading_time(SECONDARY, state(100, reading_time=_iso(at)))
    if secondary is None:
        assert run.config()["secondary_source"] is None
        return
    assert src is (sec_attr in ("", "reading_time"))
    assert run.config()["secondary_source"]["time_attribute"] == (sec_attr or "auto")
    # the primary's setting is its own
    assert run.config()["primary_source"]["time_attribute"] == "auto"


def test_a_reading_without_a_measurement_time_restored_after_a_restart_is_not_new(tmp_path):
    now = int(time.time())
    run = build(tmp_path, readings=series(now, 1, age=600))
    n = run.c.storage.reading_count
    last = run.c.current_glucose
    run.push(PRIMARY, last, at=None, attr=None, old="unavailable")  # the same value back from unavailable
    assert run.c.storage.reading_count == n


# ------------------------------------------------------------------------------------------------ sensor limits
LOWER = {"auto": 40, "40": 40, "50": 50}
UPPER = {"auto": 500, "400": 400, "450": 450, "500": 500}
MMOL_LIMIT = {40: 2.2, 50: 2.8, 400: 22.2, 450: 25.0, 500: 27.8}


@pytest.mark.parametrize("lower", list(LOWER))
@pytest.mark.parametrize("upper", list(UPPER))
@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
def test_the_primary_sensor_limits(tmp_path, lower, upper, unit):
    run = build(
        tmp_path, entry(**{C.CONF_SENSOR_LOWER_LIMIT: lower, C.CONF_SENSOR_UPPER_LIMIT: upper, C.CONF_UNIT: unit})
    )
    lo, hi = LOWER[lower], UPPER[upper]
    p = run.config()["primary_source"]
    assert (p["lower_limit"], p["upper_limit"]) == (lo, hi)
    assert p["lower_limit_setting"] == (lo if lower != "auto" else "auto")
    assert p["upper_limit_setting"] == (hi if upper != "auto" else "auto")
    assert (p["lower_limit_auto_reason"] is None) is (lower != "auto")
    assert (p["upper_limit_auto_reason"] is None) is (upper != "auto")
    # the source's LO / HI texts are stored at the limit, marked as limit readings
    c = run.c
    assert c._source_reading(PRIMARY, state("LO", unit_of_measurement=unit)) == (float(lo), "low")
    assert c._source_reading(PRIMARY, state("HI", unit_of_measurement=unit)) == (float(hi), "high")
    # a number beyond the limit in the source's unit: the limit reading. At the upper limit: HI when the limit was set
    # by hand, a measured reading when it is Automatic (limits.classify); just below it: a measured reading
    at_upper = "high" if upper != "auto" else None
    if unit == "mg/dL":
        assert c._source_reading(PRIMARY, state(hi + 1, unit_of_measurement=unit)) == (float(hi), "high")
        assert c._source_reading(PRIMARY, state(hi, unit_of_measurement=unit))[1] == at_upper
        assert c._source_reading(PRIMARY, state(hi - 1, unit_of_measurement=unit))[1] is None
        assert c._source_reading(PRIMARY, state(lo - 1, unit_of_measurement=unit)) == (float(lo), "low")
    else:
        above = round(MMOL_LIMIT[hi] + 0.1, 1)
        assert c._source_reading(PRIMARY, state(above, unit_of_measurement=unit)) == (float(hi), "high")
        if upper != "auto":  # Automatic 500: 27.8 mmol/L is 500.8 mg/dL, above the limit in any case
            assert c._source_reading(PRIMARY, state(MMOL_LIMIT[hi], unit_of_measurement=unit))[1] == "high"
        just_below = round(MMOL_LIMIT[hi] - 0.1, 1)
        assert c._source_reading(PRIMARY, state(just_below, unit_of_measurement=unit))[1] is None
        below = round(MMOL_LIMIT[lo] - 0.1, 1)
        assert c._source_reading(PRIMARY, state(below, unit_of_measurement=unit)) == (float(lo), "low")


@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
@pytest.mark.parametrize("kind", ["low", "high"])
def test_a_limit_reading_on_every_output(tmp_path, unit, kind):
    """A LO / HI reading is shown as the text everywhere and counted at the limit."""
    data = entry(**{C.CONF_SENSOR_LOWER_LIMIT: "50", C.CONF_SENSOR_UPPER_LIMIT: "400", C.CONF_UNIT: unit})
    now = int(time.time())
    rows = series(now, 1)
    limit_value = 50.0 if kind == "low" else 400.0
    last = rows[-1]
    rows[-1] = type(last)(last.timestamp, limit_value, kind, last.source)
    run = build(tmp_path, data, readings=rows)
    text = "LO" if kind == "low" else "HI"
    a = run.sensor("current_glucose")["attributes"]
    assert (a["limit"], a["limit_text"]) == (kind, text)
    assert a["zone"] == ("very_low" if kind == "low" else "very_high")
    api = run.api_data("6h")
    # no number during a limit reading, and no limit reading among the chart's readings
    assert api["limit_text"] == text and api["current"] is None and all("l" not in r for r in api["readings"])
    s6 = run.c.period_stats["6h"]
    assert s6.limit_seconds[kind] > 0
    trailer = run.feed()["gcl2"]
    assert trailer["lo" if kind == "low" else "hi"][0] == s6.limit_seconds[kind]  # the 6 h period's time
    assert run.csv().strip().splitlines()[-1].split(",")[6] == kind
    # a notification body writes LO / HI in words, for a phone's screen reader
    words = "below the sensor's lower limit" if kind == "low" else "above the sensor's upper limit"
    assert f"Last reading: {words}" in run.daily() and f"Last reading: {text}" not in run.daily()


@pytest.mark.parametrize("sec_lower", list(LOWER))
@pytest.mark.parametrize("sec_upper", list(UPPER))
def test_the_secondary_sensor_limits_are_its_own(tmp_path, sec_lower, sec_upper):
    data = entry(
        **{
            C.CONF_SECONDARY_ENTITY: SECONDARY,
            C.CONF_SENSOR_LOWER_LIMIT: "50",
            C.CONF_SENSOR_UPPER_LIMIT: "400",
            C.CONF_SECONDARY_SENSOR_LOWER_LIMIT: sec_lower,
            C.CONF_SECONDARY_SENSOR_UPPER_LIMIT: sec_upper,
        }
    )
    run = build(tmp_path, data)
    s, p = run.config()["secondary_source"], run.config()["primary_source"]
    assert (p["lower_limit"], p["upper_limit"]) == (50, 400)
    assert (s["lower_limit"], s["upper_limit"]) == (LOWER[sec_lower], UPPER[sec_upper])
    assert run.c._source_reading(SECONDARY, state("HI", unit_of_measurement="mg/dL")) == (
        float(UPPER[sec_upper]),
        "high",
    )
    assert run.c._source_reading(PRIMARY, state("HI", unit_of_measurement="mg/dL")) == (400.0, "high")


def test_the_secondary_limits_are_ignored_without_a_second_source(tmp_path):
    run = build(tmp_path, entry(**{C.CONF_SECONDARY_SENSOR_UPPER_LIMIT: "400"}))
    assert run.config()["secondary_source"] is None
    assert run.config()["primary_source"]["upper_limit"] == 500


@pytest.mark.parametrize(
    "upper,cap,notice",
    [("auto", 400.0, True), ("400", 400.0, False), ("450", 400.0, True), ("auto", 450.0, True), ("450", 450.0, False)],
)
@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
def test_the_sensor_cap_notice_follows_the_upper_limit(tmp_path, monkeypatch, upper, cap, notice, unit):
    """The Repairs notice for readings that sit flat at 400 / 450 mg/dL: only while the upper limit is higher; its
    texts give both units (mmol/L one decimal, as the sources show them)."""
    now = int(time.time())
    rows = series(now, 1, value=lambda k: cap if k % 20 < 4 else 150.0)
    created, deleted = [], []
    monkeypatch.setattr(co.ir, "async_create_issue", lambda hass, dom, iid, **kw: created.append((iid, kw)))
    monkeypatch.setattr(co.ir, "async_delete_issue", lambda hass, dom, iid: deleted.append(iid))
    run = build(tmp_path, entry(**{C.CONF_SENSOR_UPPER_LIMIT: upper, C.CONF_UNIT: unit}), readings=rows)
    run.c._check_source_notices()
    caps = [kw for iid, kw in created if iid == f"sensor_cap_{PRIMARY}"]
    assert bool(caps) is notice
    if notice:
        ph = caps[0]["translation_placeholders"]
        assert ph["value_mgdl"] == str(int(cap)) and ph["value_mmol"] == f"{MMOL_LIMIT[int(cap)]:.1f}"
    else:
        assert f"sensor_cap_{PRIMARY}" in deleted


def test_auto_limits_from_the_delivering_integration(tmp_path, monkeypatch):
    """Automatic: Dexcom 400 (and 401 = HI), CareLink 50 / 400, otherwise 40 / 500 (limits.auto_*)."""
    run = build(tmp_path)
    for domain, lo, hi in (("dexcom", 40, 400), ("carelink", 50, 400), (None, 40, 500)):
        run.c._src_domain[PRIMARY] = domain
        p = run.config()["primary_source"]
        assert (p["lower_limit"], p["upper_limit"]) == (lo, hi), domain
    run.c._src_domain[PRIMARY] = "dexcom"
    assert run.c._source_reading(PRIMARY, state(401, unit_of_measurement="mg/dL")) == (400.0, "high")
    # a setting wins over Automatic
    run2 = build(tmp_path / "b", entry(**{C.CONF_SENSOR_UPPER_LIMIT: "500"}))
    run2.c._src_domain[PRIMARY] = "dexcom"
    assert run2.config()["primary_source"]["upper_limit"] == 500


def test_expiry_entity_with_the_second_source(tmp_path):
    """Every source setting at once: none of them changes another's effect."""
    expiry = _iso(time.time() + 5 * 86400)
    data = entry(
        **{
            C.CONF_SECONDARY_ENTITY: SECONDARY,
            C.CONF_SENSOR_EXPIRY_ENTITY: "sensor.expiry",
            C.CONF_PRIMARY_TIME_ATTR: "measurement_timestamp",
            C.CONF_SECONDARY_TIME_ATTR: "dateString",
        }
    )
    run = build(tmp_path, data, states={"sensor.expiry": state(expiry)})
    cfg = run.config()
    assert cfg["expiry_entity"] == "sensor.expiry" and run.sensor("sensor_expiry")["state"] == "not_expired"
    assert cfg["primary_source"]["time_attribute"] == "measurement_timestamp"
    assert cfg["secondary_source"]["time_attribute"] == "dateString"
    assert run.c.display_status == "current"


def test_unit_chosen_in_the_entity_settings_is_stored_and_applied(tmp_path, monkeypatch):
    """The display unit can also be chosen in Home Assistant's entity settings of Last glucose reading: GlucoCraft
    applies it to every glucose sensor and stores it in the entry."""
    import types

    run = build(tmp_path)
    updates, applied = [], []
    reg_entry = types.SimpleNamespace(options={"sensor": {"unit_of_measurement": "mmol/L"}})
    registry = types.SimpleNamespace(async_get=lambda eid: reg_entry)
    monkeypatch.setattr(
        co, "current_glucose_entity_id", lambda hass, entry_id: "sensor.glucocraft_last_glucose_reading"
    )
    monkeypatch.setattr(co.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(co, "apply_display_unit", lambda hass, entry_id, unit: applied.append(unit))
    ce = types.SimpleNamespace(data=dict(run.data))
    run.hass.config_entries = types.SimpleNamespace(
        async_get_entry=lambda eid: ce, async_update_entry=lambda e, data: updates.append(data)
    )
    run.c._async_unit_setting_changed(types.SimpleNamespace(data={"action": "update", "changes": {"options": {}}}))
    assert applied == ["mmol/L"] and updates and updates[0][C.CONF_UNIT] == "mmol/L"
    # a rename (no options change) never changes the unit
    applied.clear()
    run.c._async_unit_setting_changed(types.SimpleNamespace(data={"action": "update", "changes": {"name": "x"}}))
    assert applied == []


def test_history_is_imported_from_the_configured_source_only(tmp_path, monkeypatch):
    seen = {}

    async def fake_import(hass, entity_id, storage, **kw):
        seen["entity"] = entity_id
        seen["time_attribute"] = kw.get("time_attribute")
        seen["limits"] = (kw["limits"]["lower"], kw["limits"]["upper"])
        return 0

    monkeypatch.setattr(co, "async_import_history", fake_import)
    hass = make_hass(tmp_path)
    data = entry(
        **{
            C.CONF_GLUCOSE_ENTITY: "sensor.cgm",
            C.CONF_PRIMARY_TIME_ATTR: "reading_time",
            C.CONF_SENSOR_LOWER_LIMIT: "50",
            C.CONF_SENSOR_UPPER_LIMIT: "450",
        }
    )
    c = co.GlucoCraftCoordinator(hass, "t", data)
    c.hass = hass
    asyncio.run(c.async_setup())
    assert seen == {"entity": "sensor.cgm", "time_attribute": "reading_time", "limits": (50, 450)}


def test_restored_reading_age_follows_the_stale_limit(tmp_path):
    """The no-current-data limit decides the restored status after a restart (with the source settings)."""
    now = int(time.time())
    for minutes, status in ((5, "no_current_data"), (30, "current")):
        run = build(
            tmp_path / str(minutes), entry(**{C.CONF_STALE_THRESHOLD: minutes}), readings=series(now, 1, age=600)
        )
        assert run.c.display_status == status, minutes
        assert run.sensor("is_stale")["state"] is (status != "current")
        assert run.sensor("is_stale")["attributes"]["threshold_minutes"] == minutes
