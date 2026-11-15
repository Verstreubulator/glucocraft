# License: AGPL-3.0-only (LICENSE).
"""Three guards of the core: an unreadable expiry number, a failing notice check and a missing recorder.

- An expiry entity's number that gives no representable time (an epoch in micro- or nanoseconds, "inf", "nan", a huge
  time left) is "unrecognized" like any other unreadable state; reading it never raises, also not at setup.
- A fault in a source-notice check never skips the timer's save of new readings and its entity refresh.
- Without Home Assistant's recorder (it is optional) the history import is skipped quietly: no error in the log and no
  Repairs notice, also not at the catch-up after the clock was behind.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import types

import pytest
from glucocraft import coordinator as co
from glucocraft import expiry as ex
from glucocraft import history_import as hi

from test_settings_harness import entry, make_hass, state


def _set_up(tmp_path, monkeypatch, **changes):
    """A coordinator set up as at a start, with an empty store and no history import."""

    async def no_import(*args, **kw):
        return 0

    monkeypatch.setattr(co, "async_import_history", no_import)
    hass = make_hass(tmp_path)
    for entity_id, value in changes.pop("states", {}).items():
        hass.states.d[entity_id] = value
    c = co.GlucoCraftCoordinator(hass, "t", entry(glucose_entity="sensor.p", **changes))
    c.hass = hass
    c._schedule_state_save = lambda: None
    asyncio.run(c.async_setup())
    return c


@pytest.mark.parametrize(
    ("value", "unit"),
    [
        ("1.7e15", None),  # an epoch in microseconds
        ("1.7e18", None),  # an epoch in nanoseconds
        ("inf", None),
        ("1e400", None),  # read as inf
        ("inf", "h"),
        ("nan", "min"),
        ("1e300", "h"),  # a time left far beyond any date
        ("1e12", "d"),  # whole days left far beyond any date
        ("-1e15", "min"),
    ],
)
def test_an_expiry_number_with_no_representable_time_is_unrecognized(value, unit):
    attributes = {"unit_of_measurement": unit} if unit else {}
    assert ex.parse_expiry(types.SimpleNamespace(state=value, attributes=attributes)) == (None, "unrecognized")


def test_an_unreadable_expiry_number_does_not_stop_setup(tmp_path, monkeypatch):
    c = _set_up(
        tmp_path,
        monkeypatch,
        sensor_expiry_entity="sensor.expiry",
        states={"sensor.expiry": state("1.7e15")},
    )
    assert c.expiry() == (None, "unrecognized")
    assert c.display_status  # the Status and Spoken summary sensors read it


def test_a_failing_notice_check_does_not_skip_the_timers_save_and_refresh(tmp_path, monkeypatch, caplog):
    c = _set_up(tmp_path, monkeypatch)

    def full(fd):
        raise OSError(28, "No space left on device")

    # a reading whose own save fails stays in memory: the timer's save is the next try
    with monkeypatch.context() as m:
        m.setattr(os, "fsync", full)
        assert c._process_locked("sensor.p", "P", 120.0, int(time.time()) - 60, True) == 2
    assert c.storage.dirty

    def broken():
        raise ValueError("a state of an unexpected form")

    refreshed = []
    monkeypatch.setattr(c, "_check_source_notices", broken)
    monkeypatch.setattr(c, "async_set_updated_data", refreshed.append)
    c._ticks = 4  # the next tick is a 5-minute one: statistics and source notices
    with caplog.at_level(logging.WARNING):
        asyncio.run(c._async_tick())
    assert "source notices not checked" in caplog.text
    assert not c.storage.dirty and c.storage.reading_count == 1  # the new reading was saved
    assert refreshed  # and the entities were refreshed


def test_without_a_recorder_the_history_import_is_skipped_quietly(tmp_path, monkeypatch, caplog):
    """The recorder is optional (manifest.json after_dependencies): without it neither the setup's history import nor
    the catch-up after the clock was behind runs, and nothing is logged as an error. The same import, run without a
    recorder, logs an error and opens a notice: that is what the check prevents."""
    issues = []
    monkeypatch.setattr(hi, "_history_issue", lambda hass, error: issues.append(error))
    hass = make_hass(tmp_path, components=())
    c = co.GlucoCraftCoordinator(hass, "t", entry(glucose_entity="sensor.p"))
    c.hass = hass
    c._schedule_state_save = lambda: None
    with caplog.at_level(logging.DEBUG):
        asyncio.run(c.async_setup())
        c._catch_up_after = 0  # readings were refused while the clock was behind
        assert asyncio.run(c.async_catch_up()) == 0
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert issues == [] and "history import" not in caplog.text
    caplog.clear()
    with caplog.at_level(logging.ERROR):
        assert asyncio.run(hi.async_import_history(hass, "sensor.p", c.storage)) == 0
    assert "Failed to retrieve history" in caplog.text and len(issues) == 1


@pytest.mark.parametrize(("components", "imports"), [((), 0), (("recorder",), 2)])
def test_the_history_import_runs_only_with_the_recorder(tmp_path, monkeypatch, components, imports):
    calls = []

    async def fake_import(*args, after=None, **kw):
        calls.append(after)
        return 0

    monkeypatch.setattr(co, "async_import_history", fake_import)
    hass = make_hass(tmp_path, components=components)
    c = co.GlucoCraftCoordinator(hass, "t", entry(glucose_entity="sensor.p"))
    c.hass = hass
    c._schedule_state_save = lambda: None
    asyncio.run(c.async_setup())  # an empty store: the history import
    c._catch_up_after = 0
    asyncio.run(c.async_catch_up())  # the readings after the newest stored one
    assert calls == [None, 0][:imports]
