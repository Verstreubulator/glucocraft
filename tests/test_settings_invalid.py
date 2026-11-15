# License: AGPL-3.0-only (LICENSE).
"""Invalid stored values (an old or hand-edited entry): unknown texts, wrong types, missing keys, None.

The rule under test: every setting falls back to its default without an error. Each case stores one invalid value in
an otherwise default entry and compares every output (harness.fingerprint: the Configuration attributes, every
entity, both panel files, the HTTP API, the three summaries, the CGM Report data with both appendices and the CSV
export) with the outputs of the default entry. The scheduling of the summaries and the dashboard registration are
checked separately below.
"""

from __future__ import annotations

import asyncio
import time
import types

import pytest
from glucocraft import const as C

from test_entity_ids import _load_init
from test_settings_harness import DEFAULT_ENTRY, build, entry, fingerprint, tz_patch  # noqa: F401 (the fixture)

pytestmark = pytest.mark.usefixtures("tz_patch")


@pytest.fixture(scope="module", autouse=True)
def now():
    """The time this module's tests start, frozen for all of them: the default entry's outputs (built once) and each
    case's are computed at the same instant, however long the module, or the suite before it, takes."""
    frozen = int(time.time())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(time, "time", lambda: float(frozen))
        yield frozen


@pytest.fixture(scope="module")
def reference(tmp_path_factory, now):
    """The outputs of the default entry (the same as an entry without the key: test_settings_inventory)."""
    from zoneinfo import ZoneInfo

    from homeassistant.util import dt as dt_util

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(dt_util, "get_time_zone", lambda n: ZoneInfo(n) if n else None, raising=False)
        return fingerprint(build(tmp_path_factory.mktemp("ref"), DEFAULT_ENTRY, now=now))


def _case(key, value, id_=None):
    return pytest.param(key, value, id=id_ or f"{key}={value!r}")


RANGE_KEYS = (
    C.CONF_RANGE_CRITICAL_LOW,
    C.CONF_RANGE_LOW,
    C.CONF_RANGE_TIGHT_LOW,
    C.CONF_RANGE_TIGHT_HIGH,
    C.CONF_RANGE_HIGH,
    C.CONF_RANGE_CRITICAL_HIGH,
)
GOAL_KEYS = (
    C.CONF_GOAL_TIME_IN_TARGET_RANGE,
    C.CONF_GOAL_TIME_IN_IDEAL_RANGE,
    C.CONF_GOAL_TBR,
    C.CONF_GOAL_TBR_CRITICAL,
    C.CONF_GOAL_TAR,
    C.CONF_GOAL_TAR_CRITICAL,
    C.CONF_GOAL_CV,
    C.CONF_GOAL_GMI,
)

CASES = [
    # ---- ranges: text, None, a list, out of order, out of the span
    *[_case(k, "70") for k in RANGE_KEYS],
    *[_case(k, None) for k in RANGE_KEYS],
    _case(C.CONF_RANGE_LOW, [70]),
    _case(C.CONF_RANGE_LOW, 200, id_="range_low above range_high"),
    _case(C.CONF_RANGE_CRITICAL_HIGH, 1000, id_="range_critical_high out of span"),
    _case(C.CONF_RANGE_CRITICAL_LOW, -5, id_="range_critical_low negative"),
    # the full order of the settings screen: very low < low <= tight from < tight to <= high < very high
    _case(C.CONF_RANGE_LOW, 80, id_="range_low above the tight range's start"),
    _case(C.CONF_RANGE_TIGHT_HIGH, 190, id_="tight range end above range_high"),
    _case(C.CONF_RANGE_HIGH, 130, id_="range_high below the tight range's end"),
    _case(C.CONF_RANGE_CRITICAL_LOW, 70, id_="range_critical_low equal to range_low"),
    # ---- goals: text and out of span (None is "no goal set": test_settings_ranges_goals)
    *[_case(k, "abc") for k in GOAL_KEYS],
    _case(C.CONF_GOAL_TBR, "4"),
    _case(C.CONF_GOAL_CV, 150, id_="goal_cv out of span"),
    _case(C.CONF_GOAL_TIME_IN_TARGET_RANGE, -10, id_="goal_time_in_target_range negative"),
    # ---- the no-current-data limit
    _case(C.CONF_STALE_THRESHOLD, "15"),
    _case(C.CONF_STALE_THRESHOLD, None),
    _case(C.CONF_STALE_THRESHOLD, "abc"),
    _case(C.CONF_STALE_THRESHOLD, 0),
    _case(C.CONF_STALE_THRESHOLD, -5),
    _case(C.CONF_STALE_THRESHOLD, 4),
    _case(C.CONF_STALE_THRESHOLD, 15.5),
    # ---- the throb and the no-data notice
    _case(C.CONF_FLASH_NO_CURRENT_DATA, "false"),
    _case(C.CONF_FLASH_NO_CURRENT_DATA, None),
    _case(C.CONF_NO_DATA_NOTICE, "true"),
    _case(C.CONF_NO_DATA_NOTICE_DELAY, 45),
    _case(C.CONF_NO_DATA_NOTICE_DELAY, True),
    _case(C.CONF_NO_DATA_NOTICE_DELAY, "abc"),
    # ---- the display unit and the display card
    _case(C.CONF_UNIT, "foo"),
    _case(C.CONF_UNIT, None),
    _case(C.CONF_UNIT, "mmol/l"),
    _case(C.CONF_UNIT, 5),
    _case(C.CONF_DISPLAY_CARD, "foo"),
    _case(C.CONF_DISPLAY_CARD, None),
    # ---- regional settings: unknown texts and wrong types fall back to Automatic
    _case(C.CONF_TIME_FORMAT, "13"),
    _case(C.CONF_TIME_FORMAT, 24),
    _case(C.CONF_TIME_FORMAT, None),
    _case(C.CONF_DATE_FORMAT, "DM"),
    _case(C.CONF_DATE_FORMAT, 5),
    _case(C.CONF_DATE_FORMAT, None),
    _case(C.CONF_FIRST_WEEKDAY, "funday"),
    _case(C.CONF_FIRST_WEEKDAY, 0),
    _case(C.CONF_FIRST_WEEKDAY, None),
    _case(C.CONF_FIRST_WEEKDAY, ["monday"]),
    _case(C.CONF_DECIMAL_SEPARATOR, ","),
    _case(C.CONF_DECIMAL_SEPARATOR, 1),
    _case(C.CONF_DECIMAL_SEPARATOR, ["comma"]),
    _case(C.CONF_A1C_UNIT, "percentage"),
    _case(C.CONF_A1C_UNIT, ["both"]),
    _case(C.CONF_PAPER_SIZE, "A4 "),
    _case(C.CONF_PAPER_SIZE, ["a4"]),
    # ---- trend shown as, colors, weekly pages
    _case(C.CONF_TREND_DISPLAY, "trend"),
    _case(C.CONF_TREND_DISPLAY, ["word"]),
    _case(C.CONF_TREND_DISPLAY, None),
    _case(C.CONF_ZONE_COLORS["low"], "red"),
    _case(C.CONF_ZONE_COLORS["high"], "#12345"),
    _case(C.CONF_ZONE_COLORS["very_high"], "#GGGGGG"),
    _case(C.CONF_ZONE_COLORS["tight_range"], 0x4CAF50),
    _case(C.CONF_ZONE_COLORS["near_low"], [255, 0, 0]),
    _case(C.CONF_BAND_COLORS["low"], "red"),
    _case(C.CONF_BAND_COLORS["high"], "#FCC21"),
    _case(C.CONF_BAND_COLORS["tight_range"], [82, 185, 100]),
    _case(C.CONF_BAND_COLORS["very_low"], None),
    _case(C.CONF_CARD_BACKGROUND, "Theme"),
    _case(C.CONF_CARD_BACKGROUND, "white"),
    _case(C.CONF_CARD_BACKGROUND, None),
    _case(C.CONF_CARD_BACKGROUND, 0),
    _case(C.CONF_PANEL_BACKGROUND, "theme"),
    _case(C.CONF_PANEL_BACKGROUND, "black"),
    _case(C.CONF_PANEL_BACKGROUND, [0, 0, 0]),
    _case(C.CONF_WEEKLY_PAGES, "custom"),
    _case(C.CONF_WEEKLY_PAGES, None),
    # ---- sensor limits
    _case(C.CONF_SENSOR_LOWER_LIMIT, "45"),
    _case(C.CONF_SENSOR_LOWER_LIMIT, [40]),
    _case(C.CONF_SENSOR_UPPER_LIMIT, "600"),
    _case(C.CONF_SENSOR_UPPER_LIMIT, None),
    _case(C.CONF_SECONDARY_SENSOR_UPPER_LIMIT, "HI"),
    # ---- sources and time attributes
    _case(C.CONF_SECONDARY_ENTITY, ""),
    _case(C.CONF_SECONDARY_ENTITY, "sensor.p", id_="secondary_glucose_entity=the primary"),
    _case(C.CONF_PRIMARY_TIME_ATTR, None),
    _case(C.CONF_PRIMARY_TIME_ATTR, "   "),
    _case(C.CONF_PRIMARY_TIME_ATTR, 5),
    # ---- phones, printer, switches
    _case(C.CONF_PHONE_TARGETS, None),
    _case(C.CONF_PHONE_TARGETS, "mobile_app_phone"),
    _case(C.CONF_PHONE_TYPES, None),
    _case(C.CONF_PHONE_TYPES, "low"),
    _case(C.CONF_PRINTER, None),
    _case(C.CONF_PRINTER, "   "),
    _case(C.CONF_PRINTER, 123),
    _case(C.CONF_ON_REMOVAL, "remove"),
    _case(C.CONF_ON_REMOVAL, True),
    # ---- the notification settings as published (scheduling: below)
    _case(C.CONF_NOTIFY_TIME, "ab:cd"),
    _case(C.CONF_NOTIFY_WEEKLY_DAY, "6"),
    _case(C.CONF_NOTIFY_WEEKLY_DAY, 7),
    # ---- a stored key that is no setting is ignored, whatever its value
    _case("retention_days", 0),
    _case("direction_entity", "sensor.direction"),
    _case("display_word", "trend"),
    _case("display_word_mode", "trend"),
    _case("projection_acknowledged", True),
    _case("custom_gri_formula", True),
    _case(C.CONF_DISPLAY_CARD, "epaper"),  # a display card layout that does not exist
    _case(C.CONF_PHONE_TYPES, ["critical_low", "critical_high"], id_="phone_notify_types=unknown types"),
    # ---- the settings of the removed glucose notifications, as an earlier version stored them: ignored
    _case("notify_low_enabled", True),
    _case("notify_very_high_enabled", True),
    _case(C.CONF_PHONE_TYPES, ["very_low", "low", "high", "very_high"], id_="phone_notify_types=earlier types"),
    _case(C.CONF_GOAL_TIME_IN_TARGET_RANGE, 100, id_="goal_time_in_target_range=100 (no earlier span)"),
]


@pytest.mark.parametrize("key,value", CASES)
def test_invalid_value_falls_back_to_the_default_everywhere(tmp_path, reference, now, key, value):
    got = fingerprint(build(tmp_path, entry(**{key: value}), now=now))
    if got["config"] != reference["config"]:
        diff = {k: (got["config"].get(k), reference["config"].get(k)) for k in reference["config"]}
        diff = {k: v for k, v in diff.items() if v[0] != v[1]}
        pytest.fail(f"configuration differs: {diff}")
    for part in reference:
        assert got[part] == reference[part], part


def test_empty_expiry_entity_is_no_expiry_entity(tmp_path, now):
    """An empty text is "none chosen" everywhere (the attribute echoes the empty text, which every reader treats as
    none)."""
    run = build(tmp_path, entry(**{C.CONF_SENSOR_EXPIRY_ENTITY: ""}), now=now)
    assert run.sensor("sensor_expiry")["state"] == "not_configured" and run.c.expires_at is None
    assert run.c.display_status == "current" and not run.config()["expiry_entity"]


def test_missing_glucose_source_is_refused_clearly(tmp_path):
    """The one setting without a default: an entry without a source cannot be set up (KeyError at once)."""
    with pytest.raises(KeyError):
        build(tmp_path, entry(**{C.CONF_GLUCOSE_ENTITY: ...}))


# ------------------------------------------------------------------------------------------------ scheduling
def _scheduled(tmp_path, data, monkeypatch, now):
    """(hour, minute, weekly_config) handed to the scheduler by __init__._setup_notifications, or None."""
    init = _load_init()
    got = []

    def fake(hass, coordinator, hour=8, minute=0, weekly_config=None):
        got.append((hour, minute, weekly_config))
        return lambda: None

    monkeypatch.setattr(init, "async_setup_daily_notification", fake)
    run = build(tmp_path, data, now=now)
    init._setup_notifications(run.hass, types.SimpleNamespace(data=data, async_on_unload=lambda f: None), run.c)
    return got[0] if got else None


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param("ab:cd", id="ab:cd"),
        pytest.param("", id="empty"),
        pytest.param(None, id="None"),
        pytest.param("25:00", id="25:00"),
        pytest.param("12:75", id="12:75"),
        pytest.param("-1:00", id="-1:00"),
        pytest.param(800, id="800"),
    ],
)
def test_invalid_notify_time_schedules_at_the_default(tmp_path, monkeypatch, now, stored):
    hour, minute, _ = _scheduled(tmp_path, entry(**{C.CONF_NOTIFY_TIME: stored}), monkeypatch, now)
    assert (hour, minute) == (8, 0)


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param(6, id="6 (valid)"),
        pytest.param("6", id="text 6"),
        pytest.param(7, id="7"),
        pytest.param(None, id="None"),
        pytest.param(-1, id="-1"),
    ],
)
def test_invalid_weekly_day_falls_back_to_sunday(tmp_path, monkeypatch, now, stored):
    """The weekly summary still goes out on the default day (Sunday) instead of never."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from glucocraft import notification as N

    sent = []
    callbacks = []
    monkeypatch.setattr(N, "async_track_time_change", lambda hass, cb, **kw: callbacks.append(cb) or (lambda: None))

    async def rec(hass, c, kind=None):
        sent.append(kind)

    monkeypatch.setattr(N, "async_send_daily_summary", lambda h, c: rec(h, c, "daily"))
    monkeypatch.setattr(N, "async_send_weekly_summary", lambda h, c: rec(h, c, "weekly"))
    monkeypatch.setattr(N, "async_send_monthly_summary", lambda h, c: rec(h, c, "monthly"))
    run = build(tmp_path, entry(**{C.CONF_NOTIFY_WEEKLY_ENABLED: True, C.CONF_NOTIFY_WEEKLY_DAY: stored}), now=now)
    run.hass.async_create_task = lambda coro: asyncio.run(coro)
    init = _load_init()
    init._setup_notifications(run.hass, types.SimpleNamespace(data=run.data, async_on_unload=lambda f: None), run.c)
    callbacks[0](datetime(2026, 10, 25, 8, 0, tzinfo=ZoneInfo("America/Los_Angeles")))  # a Sunday
    assert "weekly" in sent
