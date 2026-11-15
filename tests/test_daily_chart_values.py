# License: AGPL-3.0-only (LICENSE).
"""The Custom Ranges daily chart's values.

Rules under test:
  * each day's mean weighs every reading by the time it represents (the one gap rule), as every other mean, and the
    lowest and the highest value are the stored readings, unrounded (report.daily_values);
  * a day is shown only with enough data, the rule of every statistic (statistics.enough_data: at least 70% sensor
    active), today judged by the part of the day so far;
  * /api/glucocraft/data?daily=N adds the last N local days (today so far included) in Home Assistant's time zone, in
    the display unit, unrounded; any other value of daily adds nothing;
  * the chart draws those values (no whole-mg/dL file values, no plain mean) and its explanation no longer says "not
    weighted by time".
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from datetime import datetime, timedelta

from glucocraft import http_api
from glucocraft import report as R
from glucocraft.storage import GlucoseReading

from conftest import LA
from js_i18n import catalogue
from node_runner import needs_node
from test_accessibility_checks import UI_STATES, _eval
from test_surface_fixes import _api_hass, _Request, _view, api_coordinator  # noqa: F401 (the fixture)

NOW_LOCAL = datetime(2026, 10, 22, 18, 0, tzinfo=LA)
NOW = int(NOW_LOCAL.timestamp())


def _day_readings():
    """Yesterday: 12 hours of 5-minute readings at 100 mg/dL from 00:00, then 12 hours of 1-minute readings at 200 mg/dL
    (the whole day); today's readings start at 04:00 at 120.4 mg/dL every 5 minutes (14 of the 18 hours so far)."""
    y0 = int((NOW_LOCAL.replace(hour=0) - timedelta(days=1)).timestamp())
    out = [GlucoseReading(y0 + k * 300, 100.0) for k in range(144)]
    out += [GlucoseReading(y0 + 12 * 3600 + k * 60, 200.0) for k in range(720)]
    t0 = int(NOW_LOCAL.replace(hour=4).timestamp())
    out += [GlucoseReading(t, 120.4) for t in range(t0, NOW - 60, 300)]
    return out


def test_the_daily_mean_is_weighted_by_time():
    days = R.daily_values(_day_readings(), 300, LA, 3, NOW)
    assert [d["date"] for d in days] == ["2026-10-20", "2026-10-21", "2026-10-22"]
    empty, yesterday, today = days
    assert empty == {
        "date": "2026-10-20",
        "mean": None,
        "min": None,
        "max": None,
    }
    # 12 hours at 100 and 12 hours at 200 (each reading counts until the next one): 150, where the plain mean of the
    # 864 readings would be 183.3
    assert abs(yesterday["mean"] - 150.0) < 1e-9
    assert (yesterday["min"], yesterday["max"]) == (100.0, 200.0)
    assert today["mean"] == today["min"] == today["max"] == 120.4  # unrounded, as stored


def test_a_lo_reading_is_no_value_of_the_day():
    rd = _day_readings()
    rd[3] = GlucoseReading(rd[3].timestamp, 40.0, "low")
    yesterday = R.daily_values(rd, 300, LA, 2, NOW)[0]
    assert (yesterday["min"], yesterday["max"]) == (100.0, 200.0)
    assert set(yesterday) == {"date", "mean", "min", "max"}


def test_a_day_below_70_percent_sensor_active_is_left_empty():
    rd = [r for r in _day_readings() if not (NOW - 2 * 86400 < r.timestamp < NOW - 86400 - 9 * 3600)]
    yesterday = R.daily_values(rd, 300, LA, 2, NOW)[0]  # yesterday now holds readings for well under 70% of the day
    assert yesterday == {"date": "2026-10-21", "mean": None, "min": None, "max": None}


def test_today_is_judged_by_the_part_of_the_day_so_far():
    t6 = int(NOW_LOCAL.replace(hour=6).timestamp())
    late = [r for r in _day_readings() if r.timestamp < int(NOW_LOCAL.replace(hour=0).timestamp()) or r.timestamp >= t6]
    assert R.daily_values(_day_readings(), 300, LA, 1, NOW)[0]["mean"] == 120.4  # 14 of 18 hours: shown
    assert R.daily_values(late, 300, LA, 1, NOW)[0]["mean"] is None  # 12 of 18 hours (67%): left empty


def test_the_data_view_adds_the_days_unrounded_in_the_display_unit(api_coordinator, monkeypatch):  # noqa: F811
    hass = _api_hass(api_coordinator, [])
    out = asyncio.run(_view(http_api.GlucoCraftDataView).get(_Request(hass, {"daily": "14"})))
    assert len(out["daily"]) == 14 and all(d["mean"] is not None for d in out["daily"])
    assert any(round(d["mean"], 1) != d["mean"] for d in out["daily"])  # unrounded time-weighted means
    for bad in ("0", "x", "201", "-3", ""):
        assert "daily" not in asyncio.run(_view(http_api.GlucoCraftDataView).get(_Request(hass, {"daily": bad})))
    mg = out["daily"]
    monkeypatch.setattr(type(api_coordinator), "display_unit", property(lambda self: "mmol/L"))
    mm = asyncio.run(_view(http_api.GlucoCraftDataView).get(_Request(hass, {"daily": "14"})))["daily"]
    assert [round(d["mean"] * 18.016, 9) for d in mm] == [round(d["mean"], 9) for d in mg]
    assert [d["date"] for d in mm] == [d["date"] for d in mg]


@needs_node
def test_the_chart_draws_the_daily_values():
    daily = [
        {"date": "2026-10-21", "mean": 150.0, "min": 100.0, "max": 200.0},
        {"date": "2026-10-22", "mean": 120.4, "min": 120.4, "max": 120.4},
    ]
    out = _eval(
        "ui",
        textwrap.dedent(f"""
        const states = {json.dumps(UI_STATES)}, asked = [];
        const hass = {{ states, fetchWithAuth: async (url) => {{ asked.push(url);
          return {{ ok: true, json: async () => ({{ unit: "mg/dL", daily: {json.dumps(daily)} }}) }}; }} }};
        const card = new (customElements.get("glucocraft-daily-range"))(); card.setConfig({{ days: 2 }});
        card._w = 700; card._hass = hass;
        await card.draw();
        await card.draw();   // a second drawing within the hour downloads nothing
        const html = card.shadowRoot.querySelector(".g").innerHTML;
        return {{ asked, label: (html.match(/aria-label="([^"]*)"/) || [])[1] }};
    """),
    )
    assert out["asked"] == ["/api/glucocraft/data?daily=2"]
    # the means as drawn: 150 and 120 (120.4 rounded once), the lowest 100, the highest 200
    assert out["label"].startswith("Daily Mean, Lowest and Highest — Last 2 Days: daily means from 120 to 150")


def test_the_explanation_no_longer_says_not_weighted_by_time():
    how = catalogue("frontend")["ui.daily.how"]
    assert "not weighted by time" not in how and "daily mean, joined by a white line" in how
