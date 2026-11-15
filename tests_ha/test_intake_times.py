# License: AGPL-3.0-only (LICENSE).
"""Measurement times on Home Assistant's own time parser (homeassistant.util.dt), which the stub suite replaces.

Rules under test:
- A well-formed text of a time that does not exist (a 61st second, February 30, month 13, an offset beyond 24 hours)
  is no measurement time: Home Assistant's parser raises ValueError for it, and GlucoCraft's parser returns None, on
  the live path and on the history import (the recording time is used).
- Naive local measurement times across a daylight-saving fall-back: the history import keeps all 60 five-minute
  readings at their true times, as the live path does.
"""

from __future__ import annotations

import types
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.glucocraft import coordinator as co
from custom_components.glucocraft import history_import as hi

IMPOSSIBLE = ("2026-11-01T23:59:60", "2026-02-30T10:00:00", "2026-13-01T10:00:00", "2026-11-01T10:00:00+25:00")
FALL_BACK = {
    "America/Los_Angeles": (2025, 11, 2),
    "Europe/Berlin": (2025, 10, 26),
    "Australia/Adelaide": (2026, 4, 5),
}


def _recorded(text: str, recorded: int, name: str = "measurement_timestamp"):
    return types.SimpleNamespace(
        state="120",
        attributes={name: text},
        last_updated=datetime.fromtimestamp(recorded, UTC),
        last_changed=datetime.fromtimestamp(recorded, UTC),
    )


@pytest.mark.parametrize("text", IMPOSSIBLE)
async def test_an_impossible_time_is_no_measurement_time(hass, text) -> None:
    with pytest.raises(ValueError):
        dt_util.parse_datetime(text)
    assert co._parse_reading_time(text) is None
    assert co._parse_reading_time(text, after=1_790_000_000) is None
    recorded = 1_793_000_000
    no_time = [0]
    rows = hi.rows_from_states(
        [_recorded(text, recorded, "when")], False, True, {}, "when", co._parse_reading_time, no_time=no_time
    )
    assert rows == [(recorded, 120.0, None)] and no_time == [1]


@pytest.mark.parametrize("zone", list(FALL_BACK))
async def test_the_import_keeps_the_repeated_hour_as_the_live_path(hass, zone) -> None:
    tz = ZoneInfo(zone)
    start = int(datetime(*FALL_BACK[zone], 0, 0, tzinfo=tz).timestamp())
    true_ts = [start + 37 + 300 * k for k in range(60)]
    naive = [datetime.fromtimestamp(t, tz).replace(tzinfo=None).isoformat() for t in true_ts]
    previous = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(tz)
    try:
        live, after = [], None
        for text in naive:
            ts = co._parse_reading_time(text, after)
            live.append(ts)
            after = ts if after is None else max(after, ts)
        states = [_recorded(s, t + 20) for s, t in zip(naive, true_ts, strict=True)]
        rows = hi.rows_from_states(states, False, True, {}, "", co._parse_reading_time, true_ts[-1] + 120)
    finally:
        dt_util.set_default_time_zone(previous)
    assert live == true_ts
    assert [r[0] for r in rows] == true_ts
