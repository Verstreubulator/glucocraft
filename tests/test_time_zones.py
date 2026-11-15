# License: AGPL-3.0-only (LICENSE).
"""Whose clock each surface uses, with a browser in another time zone than Home Assistant's.

Rules under test (the Documentation's *Calendar days*, report.about.period.body, DISPLAY_CONTRACT.md Local time):
- The CGM Report's days and its night (00:00-05:59) follow Home Assistant's time zone, whatever the time zone of the
  computer that runs the code.
- Every time on the dashboard and the display cards is in Home Assistant's time zone (glucocraft-shared.js
  setTimeZone with hass.config.time_zone): the clock, the date, the hour on an axis and the start of an hour, also in
  a browser in another zone; before the zone is set, the browser's own zone.
- A calendar day of GlucoCraft ("2026-11-04") is shown as that day (zoneDate), whatever the browser's zone.
- The Documentation and the report state the rule.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime

from glucocraft import report as R
from glucocraft.regional import Regional

from conftest import LA, Rd
from js_i18n import catalogue, i18n_prelude
from node_runner import MARK, NODE, needs_node

# 2026-10-13 14:00 in Los Angeles (UTC-7) is 2026-10-14 06:00 in Tokyo (UTC+9)
READING = int(datetime(2026, 10, 13, 14, 0, tzinfo=LA).timestamp())


def test_the_report_counts_days_and_night_in_home_assistants_zone():
    # a 20-minute low at 14:00 Los Angeles time: a day-time event on Tue 10-13, never a night event
    now = int(datetime(2026, 10, 22, 9, 0, tzinfo=LA).timestamp())
    rd = [Rd(t, 60.0 if READING <= t < READING + 1200 else 120.0) for t in range(now - 16 * 86400, now, 60)]
    lows = R.build_report(rd, 60, LA, 14, False, [], now=now, regional=Regional("24", "YMD"))["lows"]
    assert lows["events"] == 1 and lows["events_night"] == 0 and lows["list"][0][0] == "Tue 10-13 14:00"


def _node(body: str, tz: str) -> list:
    """Run body after the language and shared modules in node with the computer's zone tz; return its printed JSON."""
    out = subprocess.run(
        [NODE, "--input-type=module", "-"],
        input=i18n_prelude() + body,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "TZ": tz},
        timeout=60,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.split(MARK, 1)[1])


@needs_node
def test_the_cards_show_a_reading_in_home_assistants_zone():
    # the browser in Tokyo, Home Assistant in Los Angeles: the reading of 14:00 in Los Angeles stands at 14:00 on the
    # 13th (in Tokyo it is 06:00 on the 14th, which the cards show only before the zone is set)
    body = f"""
const S = globalThis.glucocraftShared;
S.applyRegional({{ time_format: "24", date_format: "MDY" }});
const d = new Date({READING} * 1000);
const before = [S.zoneParts(d).d, S.clockStr(d)];
const changed = S.setTimeZone("America/Los_Angeles");
const unknown = S.setTimeZone("Not/A_Zone");
const hour = new Date(S.zoneHourStart(d.getTime() + 25 * 60000));
console.log("{MARK}" + JSON.stringify([before, changed, unknown, S.settings.tz, S.zoneParts(d).d, S.clockStr(d),
  S.numDate(d), S.hourLabel(d), S.clockStr(hour), S.numDate(S.zoneDate("2026-11-04")),
  S.sameZoneDay(d, new Date(d.getTime() + 9 * 3600e3))]));
"""
    out = _node(body, "Asia/Tokyo")
    assert out[0] == [14, "06:00"]  # before the zone is set: the browser's own zone
    assert out[1:4] == [True, False, "America/Los_Angeles"]  # an unknown zone keeps the zone in use
    assert out[4:9] == [13, "14:00", "10/13", "14:00", "14:00"]
    assert out[9] == "11/4" and out[10] is True  # a GlucoCraft day keeps its date; 23:00 is still the 13th


@needs_node
def test_a_half_hour_zone_and_a_far_browser_keep_the_whole_hours_and_the_days():
    # Home Assistant in Adelaide (UTC+10:30 in October), the browser at UTC-10: the start of an hour is a whole hour in
    # Adelaide, and a GlucoCraft day keeps its date at both ends of the world
    body = f"""
const S = globalThis.glucocraftShared;
S.applyRegional({{ time_format: "24", date_format: "YMD" }});
S.setTimeZone("Australia/Adelaide");
const h = S.zoneParts(S.zoneHourStart({READING} * 1000 + 50 * 60000));
const days = ["2026-01-01", "2026-04-05", "2026-10-04", "2026-12-31"].map((x) => S.numDate(S.zoneDate(x)));
console.log("{MARK}" + JSON.stringify([h.mi, h.s, days]));
"""
    assert _node(body, "Pacific/Honolulu") == [0, 0, ["01-01", "04-05", "10-04", "12-31"]]


def test_the_texts_name_the_one_clock():
    docs = " ".join(v if isinstance(v, str) else "".join(v) for v in catalogue("documentation").values())
    assert "follow Home Assistant's time zone, as does every time on the dashboard, the display cards" in docs
    assert "follow the clock of the device that shows them" not in docs
    period = catalogue("report")["report.about.period.body"]
    assert "the night and day periods follow the time zone of the system that created the report" in period
    assert "Home Assistant" not in period  # the report names Home Assistant only as a data source
