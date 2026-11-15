# License: AGPL-3.0-only (LICENSE).
"""The notification settings: the daily summary and its time, the weekly summary and its day (with the monthly
summary in every fourth calendar week), the phone targets and types and the phone test; times at 00:00 and 23:59 and
across daylight saving. No glucose value, range or trend sends a notification, whatever an entry of an earlier version
stores.

The outputs checked: what is scheduled (hour, minute) and what is sent on which day, the persistent notifications
(titles, texts) and the notify calls to the phones (plain text, only the chosen types, only available targets), and the
Configuration attributes.
"""

from __future__ import annotations

import asyncio
import time
import types
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from glucocraft import const as C
from glucocraft import notification as N
from glucocraft.i18n import t

from conftest import local_ts
from test_entity_ids import _load_init
from test_settings_harness import (
    build,
    entry,
    tz_patch,  # noqa: F401 (the fixture)
)

pytestmark = pytest.mark.usefixtures("tz_patch")

LA = ZoneInfo("America/Los_Angeles")
PHONE = "mobile_app_phone"
TABLET = "mobile_app_tablet"  # never available in these tests


class Scheduler:
    """Captures what __init__._setup_notifications schedules and what each trigger sends."""

    def __init__(self, monkeypatch, run):
        self.scheduled: list[dict] = []
        self.sent: list[str] = []
        self.run = run

        def track(hass, cb, hour=None, minute=None, second=None):
            self.scheduled.append({"hour": hour, "minute": minute, "second": second, "cb": cb})
            return lambda: None

        monkeypatch.setattr(N, "async_track_time_change", track)
        for kind in ("daily", "weekly", "monthly"):

            async def send(hass, c, kind=kind):
                self.sent.append(kind)

            monkeypatch.setattr(N, f"async_send_{kind}_summary", send)
        run.hass.async_create_task = lambda coro: asyncio.run(coro)
        init = _load_init()
        init._setup_notifications(run.hass, types.SimpleNamespace(data=run.data, async_on_unload=lambda f: None), run.c)

    def fire(self, when: datetime) -> list[str]:
        """Trigger the scheduled time at the local datetime `when`; return what was sent, in order."""
        self.sent.clear()
        for s in self.scheduled:
            s["cb"](when)
        return list(self.sent)


SUNDAY = datetime(2026, 10, 25, 8, 0, tzinfo=LA)  # ISO week 43
MONDAY = datetime(2026, 10, 26, 8, 0, tzinfo=LA)  # ISO week 44
SUNDAY_W44 = datetime(2026, 11, 1, 8, 0, tzinfo=LA)  # ISO week 44, the daylight-saving fall-back day


# ------------------------------------------------------------------------------------------------ the summaries
@pytest.mark.parametrize(
    "daily,weekly,expected_sunday",
    [
        pytest.param(True, True, ["weekly", "daily"], id="daily on, weekly on"),
        pytest.param(True, False, ["daily"], id="daily on, weekly off"),
        pytest.param(False, False, [], id="daily off, weekly off"),
        pytest.param(False, True, ["weekly"], id="daily off, weekly on"),
    ],
)
def test_daily_and_weekly_switches(tmp_path, monkeypatch, daily, weekly, expected_sunday):
    run = build(tmp_path, entry(notify_enabled=daily, notify_weekly_enabled=weekly, notify_weekly_day=6))
    s = Scheduler(monkeypatch, run)
    assert s.fire(SUNDAY) == expected_sunday
    assert s.fire(MONDAY) == (["daily"] if daily else [])
    n = run.config()["notifications"]
    assert (n["daily"], n["weekly"], n["weekly_day"]) == (daily, weekly, 6)


@pytest.mark.parametrize(
    "stored,hm",
    [
        ("08:00", (8, 0)),
        ("00:00", (0, 0)),
        ("23:59", (23, 59)),
        ("00:00:00", (0, 0)),
        ("23:59:00", (23, 59)),
        ("21:45", (21, 45)),
    ],
)
def test_the_notification_time(tmp_path, monkeypatch, stored, hm):
    run = build(tmp_path, entry(notify_time=stored))
    s = Scheduler(monkeypatch, run)
    assert [(x["hour"], x["minute"], x["second"]) for x in s.scheduled] == [(*hm, 0)]
    assert run.config()["notifications"]["daily_time"] == stored


def test_the_default_notification_time(tmp_path, monkeypatch):
    run = build(tmp_path, entry(notify_time=...))
    s = Scheduler(monkeypatch, run)
    assert (s.scheduled[0]["hour"], s.scheduled[0]["minute"]) == (8, 0)
    assert run.config()["notifications"]["daily_time"] == "08:00"


@pytest.mark.parametrize("day", range(7))
def test_the_weekly_day(tmp_path, monkeypatch, day):
    """0 = Monday ... 6 = Sunday: the weekly summary on that day only, before the daily one."""
    run = build(tmp_path, entry(notify_weekly_enabled=True, notify_weekly_day=day))
    s = Scheduler(monkeypatch, run)
    for offset in range(7):
        when = MONDAY + timedelta(days=offset)  # Monday 26 Oct ... Sunday 1 Nov 2026: one calendar week
        got = s.fire(when)
        if when.weekday() == day:
            # every fourth calendar week (ISO week number a multiple of 4) also the monthly summary, first
            monthly = ["monthly"] if when.isocalendar()[1] % 4 == 0 else []
            assert got == [*monthly, "weekly", "daily"]
        else:
            assert got == ["daily"]
    assert run.config()["notifications"]["weekly_day"] == day


def test_the_weekly_summary_is_off_by_default(tmp_path, monkeypatch):
    """A new entry sends no weekly (nor monthly) summary until it is turned on under Notifications; the daily one is
    on."""
    run = build(tmp_path, entry())
    s = Scheduler(monkeypatch, run)
    for offset in range(7):
        assert s.fire(MONDAY + timedelta(days=offset)) == ["daily"]
    assert run.config()["notifications"]["weekly"] is False


@pytest.mark.parametrize("day", [0, 6])
@pytest.mark.parametrize("hm", [(0, 0), (23, 59)])
def test_weekday_0_and_6_at_midnight_and_one_minute_before(tmp_path, monkeypatch, day, hm):
    """The weekday is the local one of the trigger: 00:00 and 23:59 belong to the same day."""
    run = build(
        tmp_path, entry(notify_weekly_enabled=True, notify_weekly_day=day, notify_time=f"{hm[0]:02d}:{hm[1]:02d}")
    )
    s = Scheduler(monkeypatch, run)
    target = MONDAY if day == 0 else MONDAY + timedelta(days=6)
    when = target.replace(hour=hm[0], minute=hm[1])
    assert "weekly" in s.fire(when)
    assert "weekly" not in s.fire(when - timedelta(days=1))
    assert "weekly" not in s.fire(when + timedelta(days=1))


@pytest.mark.parametrize(
    "when,weekly",
    [
        # the fall-back Sunday (1 Nov 2026): 00:30 PDT, 01:30 in both offsets, 23:59 PST
        (datetime(2026, 11, 1, 0, 30, tzinfo=LA), True),
        (datetime(2026, 11, 1, 1, 30, tzinfo=LA, fold=0), True),
        (datetime(2026, 11, 1, 1, 30, tzinfo=LA, fold=1), True),
        (datetime(2026, 11, 1, 23, 59, tzinfo=LA), True),
        # the spring-forward Sunday (8 Mar 2026): before and after the missing hour, and the next midnight
        (datetime(2026, 3, 8, 1, 59, tzinfo=LA), True),
        (datetime(2026, 3, 8, 3, 0, tzinfo=LA), True),
        (datetime(2026, 3, 8, 23, 59, tzinfo=LA), True),
        (datetime(2026, 3, 9, 0, 0, tzinfo=LA), False),
    ],
)
def test_the_weekly_and_monthly_summaries_across_daylight_saving(tmp_path, monkeypatch, when, weekly):
    run = build(tmp_path, entry(notify_weekly_enabled=True, notify_weekly_day=6))
    s = Scheduler(monkeypatch, run)
    got = s.fire(when)
    monthly = weekly and when.isocalendar()[1] % 4 == 0
    assert ("weekly" in got) is weekly and ("monthly" in got) is monthly and got[-1] == "daily"


def test_daylight_saving_weeks_have_a_monthly_summary_when_due(tmp_path, monkeypatch):
    """1 Nov 2026 is in ISO week 44 (a multiple of 4): the monthly summary is sent on that Sunday as well."""
    assert datetime(2026, 11, 1, tzinfo=LA).isocalendar()[1] == 44
    run = build(tmp_path, entry(notify_weekly_enabled=True, notify_weekly_day=6))
    assert Scheduler(monkeypatch, run).fire(datetime(2026, 11, 1, 8, 0, tzinfo=LA)) == ["monthly", "weekly", "daily"]


def test_the_last_reading_line_across_the_fall_back_hour(tmp_path, monkeypatch):
    """A reading in the first 01:30 (PDT) and a summary in the second 01:10 (PST): 40 minutes, the same date."""
    from homeassistant.util import dt as dt_util

    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", LA)
    at = local_ts(LA, 2026, 11, 1, 1, 30, fold=0)
    now = local_ts(LA, 2026, 11, 1, 1, 10, fold=1)
    for fmt, shown in (("12", "1:30 AM"), ("24", "01:30")):
        run = build(tmp_path / fmt, entry(time_format=fmt))
        run.c.last_reading_ts, run.c.current_glucose, run.c.no_current_data = at, 120.0, True
        lines = N.last_reading_lines(run.c, now=now)
        assert lines[-1].endswith(f"at {shown} (40 minutes before this summary).")


@pytest.mark.parametrize("fmt", ["12", "24"])
@pytest.mark.parametrize("date_format,shown", [("mdy", "Sat 10/31"), ("dmy", "Sat 31/10"), ("ymd", "Sat 10-31")])
def test_a_summary_at_00_00_names_yesterday_s_reading_with_its_date(tmp_path, monkeypatch, fmt, date_format, shown):
    from homeassistant.util import dt as dt_util

    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", LA)
    at = local_ts(LA, 2026, 10, 31, 23, 55)
    now = local_ts(LA, 2026, 11, 1, 0, 0)
    run = build(tmp_path, entry(time_format=fmt, date_format=date_format))
    run.c.last_reading_ts, run.c.current_glucose, run.c.no_current_data = at, 120.0, True
    lines = N.last_reading_lines(run.c, now=now)
    clock = "11:55 PM" if fmt == "12" else "23:55"
    assert lines[0] == f"No current data since {shown} {clock}."
    assert lines[1].endswith("(5 minutes before this summary).")


# ------------------------------------------------------------------------------------------------ no glucose notifications
# the switches of the glucose-level notifications of earlier versions (named alert_* and, later, notify_*_enabled):
# a stored value is ignored
EARLIER_SWITCHES = (
    "alert_critical_low_enabled",
    "alert_low_enabled",
    "alert_high_enabled",
    "alert_critical_high_enabled",
    "notify_very_low_enabled",
    "notify_low_enabled",
    "notify_high_enabled",
    "notify_very_high_enabled",
)
# the phone types of those notifications in earlier versions
EARLIER_TYPES = ["critical_low", "critical_high", "very_low", "low", "high", "very_high"]


@pytest.mark.parametrize("mg", [45, 60, 200, 300])
def test_no_glucose_value_sends_a_notification(tmp_path, mg):
    """Nothing is triggered by a glucose value, a range or a trend, also with the switches of the earlier versions
    stored as on and a phone chosen for every earlier type."""
    data = entry(
        **dict.fromkeys(EARLIER_SWITCHES, True),
        phone_notify_targets=[PHONE],
        phone_notify_types=[*EARLIER_TYPES, "summaries"],
    )
    run = build(tmp_path, data, services=(f"notify.{PHONE}",), readings=[])
    now = int(time.time())
    run.push("sensor.p", "120", at=now - 120)
    run.push("sensor.p", str(mg), at=now - 60, old="120")
    assert run.c.current_glucose == mg and not run.c.no_current_data  # the reading was stored and is current
    assert run.hass.services.notifications() == [] and run.hass.services.phone() == []
    cfg = run.config()
    assert "test_notifications" not in cfg and "direction_entity" not in cfg and "glucose" not in cfg["notifications"]
    assert cfg["phone_notifications"]["types"] == ["summaries"]  # the earlier types are left out


# ------------------------------------------------------------------------------------------------ phones
TYPE_SETS = {
    "default": list(C.DEFAULT_PHONE_TYPES),
    "summaries": ["summaries"],
    "earlier_types_only": ["critical_low", "very_low", "high"],  # stored by an earlier version: ignored
    "none": [],
}
TARGET_SETS = {"none": [], "phone": [PHONE], "phone+tablet": [PHONE, TABLET]}


@pytest.mark.parametrize("targets", list(TARGET_SETS))
@pytest.mark.parametrize("types_", list(TYPE_SETS))
def test_phone_targets_and_types(tmp_path, targets, types_):
    data = entry(phone_notify_targets=TARGET_SETS[targets], phone_notify_types=TYPE_SETS[types_])
    run = build(tmp_path, data, services=(f"notify.{PHONE}",))
    # the summaries go to the phones only when chosen
    asyncio.run(N.async_send_daily_summary(run.hass, run.c))
    asyncio.run(N.async_send_weekly_summary(run.hass, run.c))
    phone = run.hass.services.phone()
    to_phone = PHONE in TARGET_SETS[targets] and "summaries" in TYPE_SETS[types_]
    assert [d["title"] for _, d in phone] == (
        ["GlucoCraft Daily Summary", "GlucoCraft Weekly Summary"] if to_phone else []
    )
    assert all(target == PHONE for target, _ in phone)  # the unavailable tablet is skipped
    for _, d in phone:
        # plain text, with the summaries' last line, as in Home Assistant's notification
        assert "**" not in d["message"] and d["message"].endswith("\n\nThis is a notification, not an alarm.")
    assert [n["title"] for n in run.hass.services.notifications()] == [
        "GlucoCraft Daily Summary",
        "GlucoCraft Weekly Summary",
    ]
    # the dashboard's input
    pn = run.config()["phone_notifications"]
    types = [t for t in TYPE_SETS[types_] if t in C.PHONE_TYPES]
    assert pn == {"targets": TARGET_SETS[targets], "types": [C.public_key(t) for t in types]}


def test_the_phone_types_default(tmp_path):
    """Nothing is chosen for the phones by default: the summaries go there only when chosen."""
    run = build(tmp_path, entry(phone_notify_types=..., phone_notify_targets=[PHONE]), services=(f"notify.{PHONE}",))
    asyncio.run(N.async_send_daily_summary(run.hass, run.c))
    assert run.hass.services.phone() == []
    assert run.config()["phone_notifications"]["types"] == []


def test_the_phone_test(tmp_path):
    """The phone test (a one-shot action of the screens, never stored): one message per available target, plain text
    with the phones' last line; an unavailable target is skipped."""
    from glucocraft import config_flow as cf

    send = getattr(cf, "_send_phone_test", None)
    if send is None:
        pytest.skip("the phone test moved with the settings-screen rework")
    run = build(tmp_path, services=(f"notify.{PHONE}",))
    asyncio.run(send(run.hass, [PHONE, TABLET]))
    calls = run.hass.services.phone()
    assert [t for t, _ in calls] == [PHONE]
    # no glucose value: titled as the delivery test
    assert calls[0][1]["title"] == "GlucoCraft delivery test"
    # an operational message: its text alone, as plain text
    assert calls[0][1]["message"] == t("notify.phone_test.message") and "**" not in calls[0][1]["message"]
    assert C.CONF_PHONE_TEST not in entry()
