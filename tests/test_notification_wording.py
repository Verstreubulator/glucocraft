# License: AGPL-3.0-only (LICENSE).
"""Corrections to the notification texts (notification.py).

Rules under test:
- A summary never calls a stored value "current": the last reading is shown with its measurement time and its age,
  and while there is no current data the first line says so.
- No warning signs or colored symbols in the summaries (they are not alarms).
- The weekly summary does not call GlucoCraft's 14 days a "clinical assessment"; the 5-point comparison keeps the
  source's context (clinical trials, an individual participant) and is labeled a GlucoCraft rule.
- The text sent to phones has no Markdown marks (bold or italic).
- The log carries no glucose value and no reading time.
- The summaries are built under the coordinator's lock.
- In the published texts only the delivery check is named a test (its button, *Send a test notification now*).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time

import pytest
from glucocraft import const
from glucocraft import notification as N
from glucocraft.i18n import t

from conftest import PERIOD_KEYS, stats_record
from js_i18n import catalogue


@pytest.fixture
def coord(make_coordinator):
    def make(age_s: int = 60, status: str = "current", **kw):
        c = make_coordinator(
            current_glucose=112.0,
            period_stats={k: stats_record() for k in PERIOD_KEYS},
            period_stats_agp={k: stats_record() for k in PERIOD_KEYS},
            **kw,
        )
        c.last_reading_ts, c.display_status = int(time.time()) - age_s, status
        c.stale_minutes = 15
        # the no-current-data flag, as the coordinator sets it: the reading older than the limit (whatever the status)
        c.no_current_data = age_s > c.stale_minutes * 60
        return c

    return make


def test_no_stored_value_is_called_current(coord):
    for c in (coord(), coord(age_s=7 * 3600, status="no_current_data")):
        assert "Current:" not in N._build_daily_summary(c)


def test_stale_summary_says_no_current_data_first(coord):
    c = coord(age_s=7 * 3600 + 30, status="no_current_data", trend_arrow="→")
    lines = N.last_reading_lines(c)
    assert lines[0].startswith("No current data since ") and lines[0].endswith(".")
    assert (
        lines[1].startswith("Last reading: 112 milligrams per deciliter at ")
        and "(7 hours before this summary)." in lines[1]
    )
    assert "→" not in lines[1]  # no trend arrow without current data


def test_expired_sensor_that_still_delivers_is_not_no_current_data(coord):
    """After the stated expiry a fresh reading is a current reading: the summary gives it with its trend, never "No
    current data since"; the expiry line says "Sensor expired" (the status decides only that line)."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    c = coord(age_s=60, status="expired", trend_arrow="↘", trend_description="Falling")
    c.expires_at = dt_util.utcnow() - timedelta(hours=2)
    lines = N.last_reading_lines(c)
    assert len(lines) == 1 and lines[0].startswith("Last reading: 112 milligrams per deciliter (Falling) at ")
    summary = N._build_daily_summary(c)
    assert "No current data" not in summary and "\nSensor expired\n" in summary


def test_the_flag_decides_not_the_status(coord):
    """An old reading is no current data whatever the status, also after the stated expiry."""
    c = coord(age_s=3 * 3600, status="expired")
    assert N.last_reading_lines(c)[0].startswith("No current data since ")


def test_current_summary_has_time_and_trend_words(coord):
    lines = N.last_reading_lines(coord(age_s=120, trend_arrow="↗"))
    assert len(lines) == 1 and lines[0].startswith("Last reading: 112 milligrams per deciliter (Rising) at ")
    assert lines[0].endswith("(2 minutes before this summary).")


def test_limit_reading_shown_in_words(coord):
    c = coord()
    c.current_limit = "high"
    c.current_glucose = 400.0
    line = N.last_reading_lines(c)[0]
    assert line.startswith("Last reading: above the sensor's upper limit ") and "400" not in line


def test_reading_of_another_day_shows_the_date(coord):
    lines = N.last_reading_lines(coord(age_s=3 * 86400, status="no_current_data"))
    assert re.search(r"since \w{3} \d", lines[0]), lines[0]  # "Mon 10/20 ..." with the weekday and the date


def test_age_text():
    assert [N._age_text(s) for s in (0, 59, 61, 3600, 7 * 3600 + 59, 3 * 86400 + 4 * 3600)] == [
        "0 minutes",
        "0 minutes",
        "1 minute",
        "1 hour",
        "7 hours",
        "3 days 4 hours",
    ]
    assert N._age_text(7 * 3600 + 5 * 60) == "7 hours 5 minutes" and N._age_text(86400) == "1 day"


def test_no_alarm_symbols(coord):
    c = coord()
    c.current_glucose = 48.0
    for text in (N._build_daily_summary(c), N._build_weekly_summary(c), N._build_monthly_summary(c)):
        assert not re.search("[☀-➿\U0001f300-\U0001faff]", text)  # no emoji or symbol characters
    assert "Last reading: 48 milligrams per deciliter" in N._build_daily_summary(c)


def test_weekly_wording(coord):
    weekly = N._build_weekly_summary(coord())
    assert (
        "clinical assessment" not in weekly and "**Rolling 14 Days** (glucose in milligrams per deciliter):" in weekly
    )
    assert "(Battelino et al., 2019, Table 2)" in weekly


def test_five_point_comparison_keeps_the_context(coord):
    c = coord()
    c.period_stats_agp["14d"] = stats_record(pct_time_in_target_range=70.0)
    c.period_stats_agp["90d"] = stats_record(pct_time_in_target_range=60.0)
    text = " ".join(N._tir_comparison(c, "14d", "90d", always=True))
    assert "For clinical trials" in text and "individual participant" in text
    assert "This method is a GlucoCraft rule. The comparison is not an assessment." in text
    assert "last 14 days compared with the last 90 days: 10.0 percentage points higher." in text
    assert "meaningful for an individual (" not in text


def test_phone_text_has_no_markdown(fake_hass):
    hass = fake_hass(services=("notify.phone",))
    message = N._with_statement(["**GlucoCraft Daily Summary**", "", "*Insufficient data*", "2*3 stays", "a * b"])
    asyncio.run(
        N.async_forward_to_phones(
            hass,
            {const.CONF_PHONE_TARGETS: ["phone"], const.CONF_PHONE_TYPES: ["summaries"]},
            "summaries",
            "T",
            message,
        )
    )
    sent = hass.services.calls[0][2]["message"]
    assert "*" not in sent.replace("2*3", "").replace("a * b", "")
    assert sent.startswith("GlucoCraft Daily Summary") and N.short_statement() in sent
    assert "2*3 stays" in sent and "a * b" in sent  # an asterisk that is not a mark stays


def test_log_without_glucose_values(make_coordinator, fake_hass, caplog):
    hass = fake_hass()
    c = make_coordinator(current_glucose=62.0, hass=hass)
    c.last_reading_ts, c.display_status, c.stale_minutes = int(time.time()) - 30, "current", 15
    with caplog.at_level(logging.DEBUG):
        asyncio.run(N.async_send_daily_summary(hass, c))
    ours = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("glucocraft"))
    assert ours and "62" not in ours and "Glucose" not in ours


def test_summaries_built_under_the_lock():
    """Every summary is sent by _send_summary, which builds it under the coordinator's lock (executor)."""
    assert "async_add_executor_job(coordinator.locked, build, coordinator)" in inspect.getsource(N._send_summary)
    for fn, build in (
        (N.async_send_daily_summary, "_build_daily_summary"),
        (N.async_send_weekly_summary, "_build_weekly_summary"),
        (N.async_send_monthly_summary, "_build_monthly_summary"),
    ):
        assert "_send_summary(hass, coordinator, " in inspect.getsource(fn) and build in inspect.getsource(fn)


def test_settings_wording_is_neutral():
    assert "your own settings" not in N.short_statement() and "the user's settings" in N.short_statement()
    src = inspect.getsource(N) + json.dumps(catalogue("backend"))
    assert "Shown for information" not in src and "70% sensor data" not in src


# ------------------------------------------------------------------------------------------------ units in words
@pytest.mark.parametrize("unit, words", [("mg/dL", "milligrams per deciliter"), ("mmol/L", "millimoles per liter")])
def test_glucose_unit_in_words_in_the_summaries(coord, unit, words):
    c = coord(display_unit=unit)
    for text in (N._build_daily_summary(c), N._build_weekly_summary(c), N._build_monthly_summary(c)):
        body = text.split("*Targets and ranges")[0]
        assert "mg/dL" not in body and "mmol/L" not in body
        assert words in body
    daily = N._build_daily_summary(c)
    assert f"Last reading: {'112' if unit == 'mg/dL' else '6.2'} {words}" in daily
    assert f"**Rolling 24 Hours** (glucose in {words}):" in daily


# ------------------------------------------------------------------------------------------------ the name
def test_only_the_delivery_check_is_called_a_test_notification():
    """In every published text the words "test notification" name only the delivery check, *Send a test notification
    now*. The texts searched are those of test_no_suggested_uses (the catalogues, strings.json, services.yaml, the
    published documents with tests/README.md, the GitHub templates and the panel texts)."""
    from test_no_suggested_uses import TEXTS

    assert "tests/README.md" in TEXTS and "README.md" in TEXTS
    for name, text in TEXTS.items():
        rest = re.sub(r"send a test notification now", "", text, flags=re.IGNORECASE)
        found = re.search(r".{0,40}\btest[ -]notifications?\b.{0,40}", rest, flags=re.IGNORECASE)
        assert found is None, (name, found and found.group(0))


# ------------------------------------------------------------------------------------------------ the clock behind
def test_a_reading_later_than_the_clock_has_no_age_and_no_time(coord):
    """While the newest reading's own time is more than 5 minutes ahead of the clock (time_untrusted_at), its age is not
    known: the summary gives the value with the reason, never "0 minutes before this summary" nor a "since" time later
    than the clock (as every display shows no time then)."""
    c = coord(age_s=-600)  # the reading 10 minutes later than the clock
    c.no_current_data = True  # the coordinator's flag for a reading later than the clock
    lines = N.last_reading_lines(c)
    assert lines == [
        t("notify.last_reading_untrusted", reading="112 milligrams per deciliter"),
    ]
    assert lines[0] == (
        "Last reading: 112 milligrams per deciliter. Its time is later than the system clock, so its age is not known"
        " (see Settings > System > Repairs)."
    )
    daily = N._build_daily_summary(c)
    assert "before this summary" not in daily and "No current data since" not in daily


def test_a_reading_within_the_clock_margin_keeps_its_age(coord):
    """Up to 5 minutes ahead (the clock margin) the reading is given as usual, its age at least 0 minutes."""
    lines = N.last_reading_lines(coord(age_s=-240))
    assert len(lines) == 1 and lines[0].endswith("(0 minutes before this summary).")


# ------------------------------------------------------------------------------------------------ the schedule
def test_one_failing_summary_does_not_stop_the_others(make_coordinator, monkeypatch, caplog):
    """On a monthly day the summaries go out in the order monthly, weekly, daily, each on its own: one that fails is
    logged with its error and the next ones are still sent."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    c = make_coordinator(
        _settings={
            const.CONF_NOTIFY_ENABLED: True,
            const.CONF_NOTIFY_WEEKLY_ENABLED: True,
            const.CONF_NOTIFY_WEEKLY_DAY: 6,
        }
    )
    c.hass.async_create_task = lambda coro: asyncio.run(coro)
    callbacks, sent = [], []
    monkeypatch.setattr(N, "async_track_time_change", lambda hass, cb, **kw: callbacks.append(cb) or (lambda: None))

    async def fails(hass, coordinator):
        raise RuntimeError("build failed")

    for kind in ("weekly", "daily"):

        async def send(hass, coordinator, kind=kind):
            sent.append(kind)

        monkeypatch.setattr(N, f"async_send_{kind}_summary", send)
    monkeypatch.setattr(N, "async_send_monthly_summary", fails)
    N.async_setup_daily_notification(c.hass, c, 8, 0)
    with caplog.at_level(logging.WARNING, logger=N.__name__):
        callbacks[0](datetime(2026, 11, 1, 8, 0, tzinfo=ZoneInfo("America/Los_Angeles")))  # a Sunday, ISO week 44
    assert sent == ["weekly", "daily"]
    failed = [r for r in caplog.records if "monthly summary could not be sent" in r.getMessage()]
    assert len(failed) == 1 and failed[0].levelno == logging.WARNING and failed[0].exc_info is not None
