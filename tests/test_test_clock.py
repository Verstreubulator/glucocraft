# License: AGPL-3.0-only (LICENSE).
"""The clock of the test suite (ha_stub: set_clock, GLUCOCRAFT_TEST_CLOCK) and the texts of a reading of the day before.

Rules under test:
- One clock: time.time(), which the integration reads, the stub's dt_util (utcnow, now, start_of_local_day) and node's
  Date (node_runner) show the same instant; a set clock runs on from its instant.
- GLUCOCRAFT_TEST_CLOCK names the instant every test starts at (with a UTC offset or a time-zone name); without it the
  clock is the real one.
- The harness's fixed clock (fixed_clock) is noon in Los Angeles, away from midnight.
- Just after midnight the newest reading is of the day before: the daily summary gives its day and the spoken summary
  says "yesterday".
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest
from homeassistant.util import dt as dt_util

import ha_stub
from conftest import LA, local_ts
from node_runner import MARK, needs_node, node_json
from test_settings_harness import NOON, build, entry, fixed_clock, tz_patch  # noqa: F401 (fixtures)
from test_spoken_summary import _entity


def test_a_set_clock_is_the_clock_of_the_code_and_the_stub():
    at = local_ts(LA, 2027, 1, 1, 0, 0, 30)
    ha_stub.set_clock(at)
    assert at <= time.time() < at + 5
    assert abs(dt_util.utcnow().timestamp() - time.time()) < 1 and dt_util.utcnow().tzinfo is UTC
    assert dt_util.now().date().isoformat() == "2027-01-01"
    assert dt_util.start_of_local_day() == datetime(2027, 1, 1, tzinfo=dt_util.DEFAULT_TIME_ZONE)
    first = time.time()
    while time.time() == first:  # the clock runs on
        pass


def test_the_real_clock_without_a_set_clock(monkeypatch):
    monkeypatch.delenv(ha_stub.CLOCK_VARIABLE, raising=False)
    ha_stub.set_clock(0)
    ha_stub.restart_clock()
    assert ha_stub.clock_offset() == 0 and abs(time.time() - datetime.now(UTC).timestamp()) < 1


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-11-03T00:00:30-08:00", local_ts(LA, 2026, 11, 3, 0, 0, 30)),
        ("2026-11-03T00:00:30[America/Los_Angeles]", local_ts(LA, 2026, 11, 3, 0, 0, 30)),
        ("2026-11-01T01:30:00[America/Los_Angeles]", local_ts(LA, 2026, 11, 1, 1, 30)),  # the first 1:30
        ("2026-11-03T00:00:30[Asia/Tokyo]", local_ts(LA, 2026, 11, 2, 7, 0, 30)),
    ],
)
def test_the_simulated_clock_names_an_instant(monkeypatch, value, expected):
    assert ha_stub.simulated_clock(value).timestamp() == expected
    monkeypatch.setenv(ha_stub.CLOCK_VARIABLE, value)
    ha_stub.restart_clock()  # as before every test
    assert expected <= time.time() < expected + 5


def test_the_simulated_clock_needs_an_offset_or_a_zone():
    with pytest.raises(ValueError, match="UTC offset or a time-zone name"):
        ha_stub.simulated_clock("2026-11-03T00:00:30")
    assert ha_stub.simulated_clock("") is None


@needs_node
def test_node_shows_the_same_clock():
    at = local_ts(LA, 2027, 3, 14, 3, 0, 30)
    ha_stub.set_clock(at)
    shown = node_json(f'console.log("{MARK}" + JSON.stringify([Date.now(), new Date().getTime()]));', prelude=False)
    assert all(at * 1000 <= ms < (at + 30) * 1000 for ms in shown)


@pytest.mark.usefixtures("fixed_clock")
def test_the_fixed_clock_is_noon_in_los_angeles():
    assert datetime.fromtimestamp(NOON, LA).hour == 12
    assert NOON <= time.time() < NOON + 5


@pytest.mark.usefixtures("tz_patch")
@pytest.mark.parametrize(
    "time_format, daily, spoken", [("12", "Wed 10/21 11:59 PM", "11:59 PM"), ("24", "Wed 10/21 23:59", "23:59")]
)
def test_just_after_midnight_the_last_reading_is_of_the_day_before(tmp_path, time_format, daily, spoken):
    """The newest reading 60 seconds before 00:00:30: the daily summary gives its day, the spoken summary "yesterday"."""
    ha_stub.set_clock(local_ts(LA, 2026, 10, 22, 0, 0, 30))
    run = build(tmp_path, entry(time_format=time_format, date_format="mdy"))
    assert datetime.fromtimestamp(run.c.last_reading_ts, LA).date().isoformat() == "2026-10-21"
    assert f" at {daily} (" in run.daily()
    assert f", measured yesterday at {spoken}" in _entity(run).native_value
