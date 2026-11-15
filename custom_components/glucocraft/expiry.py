# License: AGPL-3.0-only (LICENSE).
"""Read a CGM sensor-expiry entity, whatever form it reports; the expiry entity is optional, and the setting offers
the sensor entities of Home Assistant (config_flow, the sources step).

parse_expiry reads an ISO date and time, a date (its local midnight), epoch seconds
or milliseconds, or a time left in days, hours or minutes (by the entity's unit); anything else is not guessed. It
returns the expiry in UTC and a format code, which the Sensor expiry entity publishes so that a display can say what
was read.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

_TIME_LEFT_UNITS = {  # unit_of_measurement -> seconds per unit, for expiry entities that report time left
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
}
# the expiry_format code of a time left, by seconds per unit (codes, not texts: the attribute is read by code)
_TIME_LEFT_FORMATS = {86400: "time_left_days", 3600: "time_left_hours", 60: "time_left_minutes"}


def whole_days_left_expiry(days: int, now_local: datetime) -> datetime:
    """Return the expiry for N whole days left: the end of the local day N days from today, in UTC.

    The end of a day is the start of the day after. "0 d left" = the end of today; "1 d left" = the end of tomorrow.
    """
    day = now_local.date() + timedelta(days=days + 1)
    return dt_util.as_utc(datetime(day.year, day.month, day.day, tzinfo=now_local.tzinfo))


def parse_expiry(state) -> tuple[datetime | None, str]:
    """Read an expiry entity's state, whatever form it has. Returns (expiry as aware UTC datetime, format).

    Understood: an ISO date/time (naive = Home Assistant's local time), a date, epoch seconds or milliseconds, and a
    number of days / hours / minutes LEFT (from the entity's unit_of_measurement). Anything else is not guessed, nor
    is a number that gives no representable time (unrecognized, never an exception). The format is a code: missing,
    unavailable, datetime, date, time_left_days / _hours / _minutes, epoch_ms, epoch_s or unrecognized.
    """
    if state is None:
        return None, "missing"
    raw = str(state.state).strip()
    if raw in ("", "unknown", "unavailable", "None"):
        return None, "unavailable"
    # a date first: Home Assistant's parse_datetime (ciso8601) reads a date without a time as its midnight, so the
    # date form would never be seen; parse_date reads only "YYYY-MM-DD", never a text with a time
    day = dt_util.parse_date(raw)
    if day is not None:
        return dt_util.as_utc(dt_util.start_of_local_day(day)), "date"
    parsed = dt_util.parse_datetime(raw)
    if parsed is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        return dt_util.as_utc(parsed), "datetime"
    try:
        num = float(raw)
    except ValueError:
        return None, "unrecognized"
    unit = str(state.attributes.get("unit_of_measurement") or "").strip().lower()
    # A number that gives no representable time (an epoch in micro- or nanoseconds, "inf", "nan", a huge time left)
    # makes datetime / timedelta raise. It is no expiry GlucoCraft can read, so it is "unrecognized" like any other
    # unreadable state, and the callers (setup, the timer, the state listener, the displays) never see an exception.
    try:
        if unit in _TIME_LEFT_UNITS:
            seconds = _TIME_LEFT_UNITS[unit]
            if seconds == 86400 and float(num).is_integer() and num >= 0:
                # whole days left ("0 d" while hours remain) = to the end of that local day, not now
                return whole_days_left_expiry(int(num), dt_util.now()), _TIME_LEFT_FORMATS[seconds]
            return dt_util.utcnow() + timedelta(seconds=num * seconds), _TIME_LEFT_FORMATS[seconds]
        if num > 1e12:
            return dt_util.utc_from_timestamp(num / 1000), "epoch_ms"
        if num > 1e9:
            return dt_util.utc_from_timestamp(num), "epoch_s"
    except (ValueError, OverflowError, OSError):
        return None, "unrecognized"
    return None, "unrecognized"
