# License: AGPL-3.0-only (LICENSE).
"""A start of GlucoCraft: values that come up as Home Assistant starts, and the recorder's readings after the newest
stored one.

Rules under test:
- Before Home Assistant has started, a source without its own measurement time that comes up (no state before, or
  unavailable / unknown) writes its old value: that is no reading, for the primary and for the second source alike,
  whatever the other source's last time. After the start, a new value that comes up is a reading; one that comes back
  unchanged is not.
- The history import at setup decides LO / HI with the device the source's current state shows, as a live reading
  is: a Nightscout source fed by a Dexcom uploader has the upper limit 400 and its HIGH code 401 is HI.
- A store with readings (kept from an earlier installation, or not loaded for a time while Home Assistant ran) gets the
  recorder's readings measured after its newest one at setup, read from shortly before that reading only, placed by
  their measurement times; no stored reading changes. A recorded state without its own measurement time is taken only
  when it cannot be a value written again as the entity came up.
- A first history import (an empty store) applies the same rule: the copy of the last value that Home Assistant
  writes as the source comes up at a start (same value and attributes, created afresh) is no reading; a value written
  again with force_update (its last_changed kept) is one, and the first state of the history is one.
"""

from __future__ import annotations

import asyncio
import time
import types
from datetime import UTC, datetime

from glucocraft import const as C
from glucocraft import coordinator as co
from glucocraft import history_import as hi

from conftest import add_reading
from test_data_path import source_coordinator, state
from test_settings_harness import entry, make_hass


# ------------------------------------------------------------------------------------------------ restored values
def _change(entity_id, new_state, old_state=None):
    return types.SimpleNamespace(data={"entity_id": entity_id, "new_state": new_state, "old_state": old_state})


def _two_sources(tmp_path):
    """Primary sensor.p (restored from the store, its last time long ago) and second source sensor.s, both without a
    measurement-time attribute; the jobs a state change starts are collected."""
    c = source_coordinator(tmp_path, secondary="sensor.s")
    c._src_last_ts["sensor.p"] = int(time.time()) - 3 * 3600  # Home Assistant was down for 3 hours
    c._src_last_value["sensor.p"] = 120.0
    jobs = []
    c.hass.async_add_executor_job = lambda *a: jobs.append(a)
    return c, jobs


def test_a_second_source_coming_up_before_the_start_gives_no_reading(tmp_path):
    c, jobs = _two_sources(tmp_path)
    assert not c._watch_source
    c._async_state_changed(_change("sensor.s", state("135")))  # an MQTT sensor's retained message
    c._async_state_changed(_change("sensor.s", state("136"), state("unavailable")))
    assert jobs == []


def test_the_primary_coming_up_before_the_start_gives_no_reading(tmp_path):
    """The mirror case: the second source was in use, and the primary has no own time."""
    c, jobs = _two_sources(tmp_path)
    c._src_last_value.clear()
    c.active_source_entity = "sensor.s"
    c._async_state_changed(_change("sensor.p", state("128")))
    assert jobs == []


def test_after_the_start_a_new_value_coming_up_is_a_reading(tmp_path):
    c, jobs = _two_sources(tmp_path)
    c._async_ha_started()
    c._async_state_changed(_change("sensor.p", state("120"), state("unavailable")))  # back unchanged: no reading
    assert jobs == []
    c._async_state_changed(_change("sensor.p", state("125"), state("unavailable")))
    c._async_state_changed(_change("sensor.s", state("135")))
    assert [job[1] for job in jobs] == ["sensor.p", "sensor.s"]


def test_a_value_with_its_own_time_is_taken_before_the_start(tmp_path):
    """Its measurement time decides (a reading made while Home Assistant was down)."""
    c, jobs = _two_sources(tmp_path)
    when = datetime.fromtimestamp(int(time.time()) - 120, UTC).isoformat()
    c._async_state_changed(_change("sensor.s", state("135", measurement_timestamp=when)))
    assert len(jobs) == 1


def test_a_state_change_from_a_value_is_a_reading_before_the_start(tmp_path):
    """Only a value that comes up is held back: a change from one value to another is a measurement."""
    c, jobs = _two_sources(tmp_path)
    c._async_state_changed(_change("sensor.s", state("136"), state("135")))
    assert len(jobs) == 1


# ------------------------------------------------------------------------------------------------ the import's device
def _recorder(monkeypatch, states):
    """A recorder holding `states`; returns the (start, end) of each read."""
    calls = []

    async def run(fn, *a):
        return fn(*a)

    def get_history(hass, entity_id, start, end):
        calls.append((start.timestamp(), end.timestamp()))
        return [s for s in states if start <= s.last_updated <= end]

    monkeypatch.setattr(hi, "get_instance", lambda hass: types.SimpleNamespace(async_add_executor_job=run))
    monkeypatch.setattr(hi, "_get_history", get_history)
    monkeypatch.setattr(co, "async_import_history", hi.async_import_history)
    return calls


def _recorded(value, recorded, measured=None, **attrs):
    attributes = {"unit_of_measurement": "mg/dL", **attrs}
    if measured is not None:
        attributes["timestamp"] = measured
    when = datetime.fromtimestamp(recorded, UTC)
    return types.SimpleNamespace(state=str(value), attributes=attributes, last_updated=when, last_changed=when)


def _coordinator(tmp_path, current, domain=None, **changes):
    hass = make_hass(tmp_path)
    hass.states.d["sensor.p"] = current
    c = co.GlucoCraftCoordinator(hass, "t", entry(glucose_entity="sensor.p", **changes))
    c.hass = hass
    c._schedule_state_save = lambda: None
    if domain:
        c._src_domain = {"sensor.p": domain}
    return c


def test_the_setup_import_knows_the_device_of_a_nightscout_source(tmp_path, monkeypatch):
    now = int(time.time())
    device = "share2-bridge"
    states = [_recorded(v, now - 3600 + 300 * k, device=device) for k, v in enumerate(["150", "401", "160"])]
    _recorder(monkeypatch, states)
    c = _coordinator(tmp_path, state("160", device=device), domain="nightscout")
    asyncio.run(c.async_setup())
    assert [(r.raw, r.limit) for r in c.storage.readings] == [(150.0, None), (400.0, "high"), (160.0, None)]
    assert c._source_limits("sensor.p")["upper"] == 400 and c._source_limits("sensor.p")["dexcom_type"]


def test_without_a_device_a_nightscout_source_keeps_its_limits_without_one(tmp_path, monkeypatch):
    now = int(time.time())
    _recorder(monkeypatch, [_recorded(v, now - 3600 + 300 * k) for k, v in enumerate(["150", "401"])])
    c = _coordinator(tmp_path, state("401"), domain="nightscout")
    asyncio.run(c.async_setup())
    assert [(r.raw, r.limit) for r in c.storage.readings] == [(150.0, None), (401.0, None)]


# ------------------------------------------------------------------------------------------------ the gap import
def _stored(c, newest, count=120):
    for k in range(count):
        add_reading(c.storage, newest - 300 * (count - 1 - k), 110.0)
    c.storage.save()
    return [(r.timestamp, r.raw, r.limit, r.source) for r in c.storage.readings]


def test_readings_after_the_newest_kept_one_are_imported(tmp_path, monkeypatch):
    """Readings kept from an earlier installation, deleted two days ago: the recorder's readings since are imported,
    read from an hour before the newest kept reading only."""
    now = (int(time.time()) // 300) * 300
    newest = now - 2 * 86400
    c = _coordinator(tmp_path, state("140"))
    before = _stored(c, newest)
    recorded = [_recorded(130 + k % 20, newest + 300 * k + 20, measured=newest + 300 * k) for k in range(1, 576)]
    recorded.insert(0, _recorded(110, newest + 20, measured=newest))  # the newest kept reading itself
    calls = _recorder(monkeypatch, recorded)
    asyncio.run(c.async_setup())
    stored = [(r.timestamp, r.raw, r.limit, r.source) for r in c.storage.readings]
    assert stored[: len(before)] == before  # no stored reading changed
    assert [r[0] for r in stored[len(before) :]] == [newest + 300 * k for k in range(1, 576)]
    assert {r[3] for r in stored[len(before) :]} == {C.SOURCE_PRIMARY}
    assert min(start for start, _ in calls) == newest - 3600
    assert c.last_reading_ts == newest + 300 * 575 and c.current_glucose == stored[-1][1]
    assert c._src_last_ts["sensor.p"] == c.last_reading_ts


def test_a_value_written_again_at_a_start_is_not_imported(tmp_path, monkeypatch):
    """A source without measurement times: the value the entity writes again as Home Assistant starts (the same value
    and attributes as the state before it, or the same value after unavailable) is no reading; a new value is one,
    and so is a value whose attributes changed."""
    now = (int(time.time()) // 300) * 300
    newest = now - 6 * 3600
    c = _coordinator(tmp_path, state("150"))
    before = _stored(c, newest)
    recorded = [
        _recorded(110, newest),  # the newest stored reading's own state
        _recorded(110, newest + 2 * 3600),  # written again as Home Assistant started: no reading
        _recorded(120, newest + 2 * 3600 + 300),
        _recorded(120, newest + 2 * 3600 + 600, trend="flat"),  # its attributes changed: a reading
        _recorded("unavailable", newest + 2 * 3600 + 900),
        _recorded(120, newest + 2 * 3600 + 1200, trend="flat"),  # back unchanged after unavailable: no reading
        _recorded(125, newest + 2 * 3600 + 1500, trend="flat"),
    ]
    _recorder(monkeypatch, recorded)
    asyncio.run(c.async_setup())
    added = [(r.timestamp, r.raw) for r in c.storage.readings[len(before) :]]
    assert added == [
        (newest + 2 * 3600 + 300, 120.0),
        (newest + 2 * 3600 + 600, 120.0),
        (newest + 2 * 3600 + 1500, 125.0),
    ]


def test_the_first_state_read_without_its_own_time_is_not_imported(tmp_path, monkeypatch):
    """What came before it is not known, so it may be a value written again at a start."""
    now = (int(time.time()) // 300) * 300
    newest = now - 6 * 3600
    c = _coordinator(tmp_path, state("150"))
    before = _stored(c, newest)
    _recorder(monkeypatch, [_recorded(140, newest + 600), _recorded(145, newest + 900)])
    asyncio.run(c.async_setup())
    assert [(r.timestamp, r.raw) for r in c.storage.readings[len(before) :]] == [(newest + 900, 145.0)]


def test_a_first_import_leaves_out_the_copy_written_at_a_start(tmp_path, monkeypatch):
    """An empty store: the whole history is imported, without the value written again as Home Assistant started."""
    now = (int(time.time()) // 300) * 300
    t0 = now - 4 * 3600
    c = _coordinator(tmp_path, state("140"))
    original = _recorded(131, t0 + 600)
    forced = _recorded(131, t0 + 900)
    forced.last_changed = original.last_changed  # written again with force_update: the state did not change
    recorded = [
        _recorded(100, t0),  # the first state of the history
        _recorded(110, t0 + 300),
        original,
        forced,
        _recorded(131, t0 + 3600),  # written again as Home Assistant started: created afresh, no reading
        _recorded(140, t0 + 3900),
    ]
    _recorder(monkeypatch, recorded)
    asyncio.run(c.async_setup())
    assert [(r.timestamp, r.raw) for r in c.storage.readings] == [
        (t0, 100.0),
        (t0 + 300, 110.0),
        (t0 + 600, 131.0),
        (t0 + 900, 131.0),
        (t0 + 3900, 140.0),
    ]


def test_no_gap_import_while_the_clock_is_behind_or_without_a_recorder(tmp_path, monkeypatch):
    calls = []

    async def fake_import(*args, after=None, recent_only=False, **kw):
        calls.append((after, recent_only))
        return 0

    monkeypatch.setattr(co, "async_import_history", fake_import)
    now = int(time.time())
    for name, newest, components in (
        ("behind", now + 3600, ("recorder",)),
        ("norec", now - 3600, ()),
        ("gap", now - 3600, ("recorder",)),
    ):
        (tmp_path / name).mkdir()
        hass = make_hass(tmp_path / name, components=components)
        hass.states.d["sensor.p"] = state("120")
        c = co.GlucoCraftCoordinator(hass, "t", entry(glucose_entity="sensor.p"))
        c.hass = hass
        c._schedule_state_save = lambda: None
        _stored(c, newest)
        asyncio.run(c.async_setup())
    assert calls == [(now - 3600, True)]
