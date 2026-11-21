# License: AGPL-3.0-only (LICENSE).
"""A reading at the sensor's limit gives no value and still counts as a category.

Rules under test, with numbers worked out by hand:
- The mean, SD, CV, GMI, lowest and highest value come from the measured readings alone; a period with limit readings
  alone has none of them (None), and every surface shows its "no value" form.
- The time in each band, the GRI and sensor active keep every reading: LO is very low and HI very high.
- A period states the number of limit readings and the seconds they represent.
- The trend and the 5-minute change never use a limit reading, and are unknown when the newest reading is one.
- Last glucose reading has no state during a limit reading; its attributes mark it, and the zone word says "Sensor limit".
- The panel file leaves limit readings out and carries the GCL2 trailer; the chart data leave them out; the CSV leaves the
  number empty, and its own export can be read back.
- The warning line, the same rule in Python, on the display card and on the panels.
- No text says that such a reading is counted at the limit.
"""

from __future__ import annotations

import json
import re
import struct
import types
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import yaml
from glucocraft import export, http_api, notification, panel_feed, report
from glucocraft import limits as L
from glucocraft import statistics as S
from glucocraft.const import STAT_PERIODS
from glucocraft.storage import GlucoseReading

import ha_stub
from conftest import NO_PANELS, PANELS, REPO, real_coordinator
from node_runner import needs_node
from test_accessibility import _card
from test_data_path import source_coordinator

PKG = REPO / "custom_components" / "glucocraft"
R_I18N = PKG / "i18n" / "en"
PANEL_EN = PANELS / "i18n" / "en.yaml" if PANELS else REPO / "missing"
BASE = 1_790_000_000


def reading(minute: int, value: float, limit: str | None = None) -> GlucoseReading:
    return GlucoseReading(BASE + minute * 60, value, limit, 1)


# Four readings of 300 s each: 100 and 120 measured, LO stored at 40, HI stored at 500
MIXED = [reading(0, 100.0), reading(5, 120.0), reading(10, 40.0, "low"), reading(15, 500.0, "high")]
WEIGHTS = [300.0, 300.0, 300.0, 300.0]


def stats_of(readings, weights=None, period=1200):
    w = weights if weights is not None else [300.0] * len(readings)
    return S.compute_period_stats(readings, "t", period, 300, weights=w)


class TestStatistics:
    def test_the_values_come_from_the_measured_readings_alone(self):
        s = stats_of(MIXED)
        assert s.mean == pytest.approx(110.0)  # (100 + 120) / 2, not (100 + 120 + 40 + 500) / 4 = 190
        assert s.std_dev == pytest.approx(10.0)  # sqrt((10^2 + 10^2) / 2)
        assert s.cv == pytest.approx(100 * 10 / 110)
        assert s.gmi == 5.9  # 3.31 + 0.02392 x 110 = 5.9412
        assert s.gmi_mmol_mol == 41  # 12.71 + 4.70587 x 110 / 18.016 = 41.44
        assert (s.min_value, s.max_value) == (100.0, 120.0)

    def test_the_weights_of_the_measured_readings_decide_the_mean(self):
        s = stats_of(MIXED, [600.0, 200.0, 900.0, 100.0], 1800)
        assert s.mean == pytest.approx((100 * 600 + 120 * 200) / 800)  # 105

    def test_the_time_in_ranges_keeps_every_reading(self):
        s = stats_of(MIXED)
        assert s.time_critical_low == pytest.approx(25.0)  # LO: very low
        assert s.time_critical_high == pytest.approx(25.0)  # HI: very high
        assert s.pct_time_in_target_range == pytest.approx(50.0)
        assert s.time_below_low == pytest.approx(25.0) and s.time_above_high == pytest.approx(25.0)
        # the GRI: 3.0 x 25 + 1.6 x 25 = 115, capped at 100
        assert s.gri == 100.0

    def test_sensor_active_keeps_every_reading(self):
        s = stats_of(MIXED)
        assert s.covered_seconds == 1200.0 and s.data_availability == pytest.approx(100.0)
        assert s.reading_count == 4

    def test_the_period_states_the_number_and_the_time(self):
        s = stats_of(MIXED, [600.0, 200.0, 900.5, 100.0], 1800)
        assert s.limit_readings == {"low": 1, "high": 1}
        assert (s.limit_low_seconds, s.limit_high_seconds) == (900.5, 100.0)
        assert s.limit_seconds == {"low": 901, "high": 100}  # whole seconds, up

    def test_without_limit_readings_nothing_changes(self):
        s = stats_of([reading(0, 100.0), reading(5, 120.0)], period=600)
        assert s.mean == pytest.approx(110.0) and s.limit_seconds == {"low": 0, "high": 0}

    def test_a_single_measured_reading_has_no_spread(self):
        s = stats_of([reading(0, 100.0), reading(5, 40.0, "low")], period=600)
        assert s.mean == 100.0 and s.std_dev is None and s.cv is None

    @pytest.mark.parametrize(
        "flag, value, band", [("low", 40.0, "time_critical_low"), ("high", 500.0, "time_critical_high")]
    )
    def test_limit_readings_alone_have_no_value(self, flag, value, band):
        s = stats_of([reading(0, value, flag), reading(5, value, flag)], period=600)
        assert (s.mean, s.std_dev, s.cv, s.gmi, s.gmi_mmol_mol, s.min_value, s.max_value) == (None,) * 7
        assert getattr(s, band) == pytest.approx(100.0)
        assert s.limit_seconds[flag] == 600
        assert S.gmi_shown(s, 14 * 86400) is None


class TestWarningTime:
    @pytest.mark.parametrize(
        "seconds, minutes, text",
        [
            (1, 1, "1 minute"),
            (60, 1, "1 minute"),
            (61, 2, "2 minutes"),
            (900, 15, "15 minutes"),
            (3540, 59, "59 minutes"),
            (3541, 60, "1 hour"),
            (7500, 125, "2 hours 5 minutes"),
            (3596400, 59940, "999 hours"),
        ],
    )
    def test_the_duration_is_whole_minutes_up_in_words(self, seconds, minutes, text):
        """Whole minutes, up (limits.limit_minutes); the notifications write the time in words (ACCESSIBILITY.md rule
        7), in hours and minutes as the cards and the panels count it."""
        assert L.limit_minutes(seconds) == minutes
        assert notification._limit_time(seconds) == text

    def test_no_time_is_zero_minutes(self):
        assert L.limit_minutes(0) == 0


class TestTrend:
    def coordinator(self, tmp_path, rows):
        c = real_coordinator(tmp_path, _secondary_entity=None, _time_attr={})
        now = 1_790_000_000
        for minute, value, flag in rows:
            c.storage.insert_reading(now + minute * 60, value, flag, 1)
        c._now = now
        return c, now

    def test_the_trend_is_unknown_at_a_limit_reading(self, tmp_path):
        c, now = self.coordinator(tmp_path, [(m, 120.0, None) for m in range(0, 40, 5)] + [(40, 40.0, "low")])
        assert c._adaptive(now + 40 * 60) is None
        c._apply_adaptive(now + 40 * 60)
        assert c.slope is None and c.change_5min is None and c.trend_description == "Unknown" and c.trend_arrow == ""

    def test_a_limit_reading_is_no_value_of_a_later_trend(self, tmp_path):
        steady = [(m, 120.0, None) for m in range(0, 40, 5)]
        with_lo, now = self.coordinator(tmp_path / "a", [*steady, (40, 500.0, "high"), (45, 120.0, None)])
        without, _ = self.coordinator(tmp_path / "b", [*steady, (45, 120.0, None)])
        assert with_lo._adaptive(now + 45 * 60) == without._adaptive(now + 45 * 60)
        assert with_lo._adaptive(now + 45 * 60)[1] == pytest.approx(
            0.0
        )  # steady: a stored 500 would make it a steep rise

    def test_the_word_still_shows_under_lo(self, tmp_path):
        c = source_coordinator(tmp_path)
        c.current_glucose, c.current_limit, c.last_reading_ts = 40.0, "low", int(datetime.now(UTC).timestamp())
        c.no_current_data = False
        assert c.measured_glucose is None and c.current_limit_text == "LO" and c.display_word == "Sensor limit"
        c.current_glucose, c.current_limit = 120.0, None
        assert c.measured_glucose == 120.0 and c.current_limit_text is None


def test_the_sensor_has_no_state_during_a_limit_reading():
    src = (PKG / "sensor.py").read_text(encoding="utf-8")
    body = src[src.index("class GlucoCraftCurrentGlucose") : src.index("class GlucoCraftStatus")]
    assert "return self.coordinator.measured_glucose" in body
    # the panels' own state of the reading stays, marked as no measurement by "limit"
    assert '"value_mgdl": c.current_glucose' in body and '"limit": c.current_limit' in body


class TestPanelFile:
    def stats(self):
        s = stats_of(MIXED)
        return {STAT_PERIODS[1][0]: s}

    def test_the_trailer_bytes(self):
        data = panel_feed.limit_time_trailer(self.stats())
        assert len(data) == panel_feed.GCL2_SIZE == 60 and data[-4:] == b"GCL2"
        values = struct.unpack("<14I", data[:56])
        assert values[2:4] == (300, 300)  # the second period: LO, HI
        assert sum(values) == 600

    def test_a_period_without_data_is_zero(self):
        assert set(struct.unpack("<14I", panel_feed.limit_time_trailer({})[:56])) == {0}

    def test_the_readings_leave_limit_readings_out(self, tmp_path):
        c = real_coordinator(tmp_path)
        for r in MIXED:
            c.storage.insert_reading(r.timestamp, r.raw, r.limit, 1)
        assert panel_feed._readings(c.storage.readings, 0, None) == [
            (MIXED[0].timestamp, 100),
            (MIXED[1].timestamp, 120),
        ]

    def test_a_record_with_no_measured_values_packs(self):
        s = stats_of([reading(0, 40.0, "low")], period=300)
        rec = panel_feed._period_record(s, 300)
        fields = panel_feed._STATS_FMT.unpack(rec)
        assert fields[0] == 0.0 and fields[-2:] == (0, 0)  # the file's "no value"
        assert fields[8] == pytest.approx(100.0)  # very low
        assert panel_feed.extremes_block({STAT_PERIODS[0][0]: s})[:8] == b"\0" * 8


class TestChartData:
    def test_limit_readings_leave_the_chart_readings(self):
        rows = http_api._downsample_readings(MIXED, 3600, "mg/dL")
        assert [r["r"] for r in rows] == [100.0, 120.0] and all("l" not in r for r in rows)

    def test_a_period_gives_its_time(self):
        out = http_api._period_json(stats_of(MIXED), 1200, "mg/dL")
        assert out["min"] == 100.0 and out["max"] == 120.0 and out["mean"] == pytest.approx(110.0)
        assert out["limit_readings"] == {"low": 1, "high": 1} and out["limit_seconds"] == {"low": 300, "high": 300}
        assert "min_is_limit" not in out

    def test_a_period_with_limit_readings_alone(self):
        out = http_api._period_json(stats_of([reading(0, 40.0, "low")], period=300), 300, "mmol/L")
        assert out["mean"] is None and out["min"] is None and out["max"] is None and out["gmi"] is None


class TestExport:
    def test_the_csv_has_no_number_for_a_limit_reading(self):
        rows = export.readings_csv(MIXED, "mg/dL", UTC).strip().split("\n")
        assert rows[1].split(",")[2:4] == ["100.0", "100"]
        lo, hi = rows[3].split(","), rows[4].split(",")
        assert lo[2:4] == ["", "LO"] and lo[-1] == "low"
        assert hi[2:4] == ["", "HI"] and hi[-1] == "high"

    def test_the_csv_reads_back(self):
        rows, left_out = export.read_readings_csv(export.readings_csv(MIXED, "mg/dL", UTC), (40, 500))
        assert left_out == 0
        assert [(r.raw, r.limit) for r in rows] == [(100.0, None), (120.0, None), (40.0, "low"), (500.0, "high")]


class TestReport:
    def grid(self):
        tz = UTC
        start = datetime(2026, 10, 1, tzinfo=tz)
        ts0 = int(start.timestamp())
        # 00:00 to 01:00 measured at 100, 01:00 to 02:00 LO, 02:00 to 03:00 measured at 200, 03:00 to 04:00 HI
        rows = []
        for m in range(0, 240, 5):
            hour = m // 60
            value, flag = ((100.0, None), (40.0, "low"), (200.0, None), (500.0, "high"))[hour]
            rows.append(GlucoseReading(ts0 + m * 60, value, flag, 1))
        return report.minute_grid(rows, 300, ts0, ts0 + 4 * 3600, tz), start, ts0

    def test_the_grid_leaves_them_out_of_the_values(self):
        g, _, ts0 = self.grid()
        assert set(g["vals"]) == {100.0, 200.0}  # the AGP curve and the daily profiles
        assert len(g["vals"]) == len(g["mins"]) == len(g["tod"]) == len(g["lod"]) == 120
        assert g["mins"][60] == ts0 + 2 * 3600  # the gap: 01:00 to 02:00 has no minute

    def test_the_period_states_count_and_time(self):
        g, _, ts0 = self.grid()
        counts, seconds = report.limit_counts(g, ts0, ts0 + 4 * 3600)
        assert counts == {"low": 12, "high": 12} and seconds == {"low": 3600, "high": 3600}
        counts, seconds = report.limit_counts(g, ts0 + 3600 + 1800, ts0 + 2 * 3600)
        assert counts == {"low": 6, "high": 0} and seconds == {"low": 1800, "high": 0}

    def test_the_hourly_log_has_measured_numbers(self):
        g, start, _ = self.grid()
        days = report.day_pages(g, start, 1)
        mn, mx, lo, hi = days["log"][0]
        assert mn[:4] == [100.0, None, 200.0, None] and mx[:4] == [100.0, None, 200.0, None]
        assert lo[:4] == [False, True, False, False] and hi[:4] == [False, False, False, True]

    def test_the_low_events_still_see_lo(self):
        g, _, ts0 = self.grid()
        events = report.events_in(g, ts0, ts0 + 4 * 3600)
        assert len(events) == 1 and events[0][0] == ts0 + 3600  # the hour at LO is an event

    def test_the_daily_values_are_measured(self):
        now = datetime(2026, 10, 2, 12, tzinfo=UTC)
        ts0 = int((now - timedelta(days=1)).replace(hour=0).timestamp())
        rows = [
            GlucoseReading(ts0 + m * 60, 40.0 if m == 60 else 120.0, "low" if m == 60 else None, 1)
            for m in range(0, 1440, 5)
        ]
        days = report.daily_values(rows, 300, UTC, 2, now.timestamp())  # a whole day: enough data
        assert days[0]["min"] == 120.0 and days[0]["max"] == 120.0 and days[0]["mean"] == pytest.approx(120.0)
        assert set(days[0]) == {"date", "mean", "min", "max"}


class TestNotification:
    def overview(self, stats):
        c = types.SimpleNamespace(
            display_unit="mg/dL", uses_custom_ranges=lambda: False, _settings={}, regional=None, hass=None, config=None
        )
        return c, stats

    def test_the_overview_gives_measured_values_and_the_time(self, monkeypatch):
        c, s = self.overview(stats_of(MIXED, [300.0, 300.0, 7500.0, 900.0], 9000))
        monkeypatch.setattr(notification, "_rg", lambda _c: types.SimpleNamespace(num=lambda v, d=1: f"{v:.{d}f}"))
        monkeypatch.setattr(notification, "_gl", lambda _c, mg, _u: f"{mg:.0f}")
        lines = notification._overview_lines(c, s, set())
        text = "\n".join(lines)
        assert "lowest to highest: 100 to 120" in text
        # the number of limit readings and their time: one LO reading weighing 7500 s, one HI reading of 900 s
        assert "below the sensor's lower limit: 1 reading (2 hours 5 minutes)" in text
        assert "above the sensor's upper limit: 1 reading (15 minutes)" in text
        assert "left out of the values above" in text

    def test_a_period_with_limit_readings_alone(self, monkeypatch):
        c, s = self.overview(stats_of([reading(0, 40.0, "low")], period=300))
        monkeypatch.setattr(notification, "_rg", lambda _c: types.SimpleNamespace(num=lambda v, d=1: f"{v:.{d}f}"))
        monkeypatch.setattr(notification, "_gl", lambda _c, mg, _u: f"{mg:.0f}")
        text = "\n".join(notification._overview_lines(c, s, set()))
        assert "Mean: not available" in text and "lowest to highest: not available to not available" in text


# ------------------------------------------------------------------------------------------------ the display card
def _file_with_limit(lo: int, hi: int, p: int = 0) -> dict:
    """The file part the card reads: the period's mean and its time at the limit."""
    return {
        "stats": [{"mean": 120, "min": 80, "max": 180}] * 7,
        "limitSeconds": [[lo, hi] if k == p else [0, 0] for k in range(7)],
    }


TAGS = ["glucocraft-display-card", "glucocraft-display-card-wide"]


@needs_node
@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("page", [0, 1])
def test_the_cards_graphs_state_no_time_at_the_limit(tag, page):
    """No "Sensor limit" line on any graph: the LO / HI screen in the value's place names a limit
    reading, the marks at the edge show the stretches; the time at the limit is in the screen-reader text."""
    svg = _card(tag, extra=_RUNS.format(kind="low", page=page))["extra"]  # an hour at LO in every period
    assert "data-limit=" in svg and "Sensor limit" not in svg and "1 h 0 min" not in svg


@needs_node
def test_the_card_says_the_warning():
    extra = f"card._delta = null; card._bf = {json.dumps(_file_with_limit(7500, 900))}; return card._describeGraph(0);"
    said = _card("glucocraft-display-card", value=120, zone="in_range", label="", extra=extra)["extra"]
    assert "below the sensor's lower limit for 2 hours 5 minutes" in said
    assert "above the sensor's upper limit for 15 minutes" in said


@needs_node
def test_the_card_shows_the_lo_screen_and_never_no_current_data():
    """The state is unknown during a limit reading: the card reads value_mgdl and limit together, and shows the LO /
    HI screen in the value's place (never the stored limit, never "No current data")."""
    out = _card(
        "glucocraft-display-card", value=40, zone="very_low", label="Very low", limit="low", display_word="Sensor limit"
    )
    texts = " ".join(re.findall(r"<text[^>]*>([^<]*)</text>", out["svg"]))  # (the range line may wrap)
    assert "Below sensor range (LO)" in texts and "No current" not in out["svg"]
    assert ">LO</text>" not in out["svg"] and 'fill="#c62828"' in out["svg"]


def _clock_at(hour: int, minute: int) -> None:
    """Set the test's clock (and node's) to Wednesday 21 October 2026 at hour:minute in Los Angeles."""
    ha_stub.set_clock(datetime(2026, 10, 21, hour, minute, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp())


# The attributes of a stretch of limit readings (limit_since, last_measured_mgdl, last_measured_ts), the stretch begun
# 2 h 5 min ago, the last measured reading 2 minutes before it; Home Assistant's time zone set, so that "today" and
# "yesterday" depend only on the test's clock (_clock_at)
_STRETCH = """
  const cur = states["sensor.glucocraft_last_glucose_reading"].attributes, now = Math.floor(Date.now() / 1000);
  Object.assign(cur, {{ limit_since: now - 125 * 60, last_measured_mgdl: {mg}, last_measured_ts: now - 127 * 60 }});
  card.hass = {{ states, config: {{ time_zone: "America/Los_Angeles" }}, fetchWithAuth: async () => ({{ ok: false }}) }};
  card._render();
  return {{ svg: root.querySelector("svg").innerHTML, label: root.querySelector("svg").attrs["aria-label"] }};
"""


@needs_node
@pytest.mark.parametrize("tag", ["glucocraft-display-card", "glucocraft-display-card-wide"])
@pytest.mark.parametrize("compact", [0, 360])
@pytest.mark.parametrize(
    "limit,zone,word,line,mg,unit,shown",
    [
        ("low", "very_low", "Very low", "Below sensor range (LO)", 52, "mg/dL", "52 mg/dL"),
        ("high", "very_high", "Very high", "Above sensor range (HI)", 387, "mg/dL", "387 mg/dL"),
        ("low", "very_low", "Very low", "Below sensor range (LO)", 52, "mmol/L", "2.9 mmol/L"),
    ],
)
def test_the_lo_hi_screen_four_lines(tag, compact, limit, zone, word, line, mg, unit, shown):
    """The value's place at the sensor's limit, modeled on the words without a current reading: the zone word, large
    and in the zone's color; "Below sensor range (LO)" / "Above sensor range (HI)"; since when, with how long; the last
    measured value before the stretch with its time. The trend stays blank, the 5-minute delta "---". At noon, so the
    stretch began today whenever the suite runs."""
    _clock_at(12, 0)
    out = _card(
        tag,
        value=40 if limit == "low" else 400,
        zone=zone,
        label=word,
        limit=limit,
        unit=unit,
        compact=compact,
        extra=_STRETCH.format(mg=mg),
    )["extra"]
    texts = re.findall(r"<text[^>]*>([^<]*)</text>", out["svg"])
    joined = " ".join(texts)
    color = {"very_low": "#c62828", "very_high": "#1565c0"}[zone]
    assert re.search(rf'font-size="\d+" fill="{color}"[^>]*>{word}</text>', out["svg"])  # the zone word, zone color
    # (the range line wraps at the room like the panels' label: the test's width stand-in is wider than Roboto)
    assert line in joined and not re.search(
        r'font-size="(120|160|\d{2,3})" fill="#[0-9a-f]{6}"[^>]*>40{1,2}</text>', out["svg"]
    )
    assert "Since 9:55 AM (2 h 5 min)" in joined, texts
    assert re.search(rf"Last measured: {re.escape(shown)}( at|,) ", joined) or f"Last measured: {shown}" in joined
    assert "M4,11V13H16" not in out["svg"]  # no trend arrow
    said = out["label"]
    assert said.startswith(f"Glucose {'below' if limit == 'low' else 'above'} the sensor's")
    assert ", for 2 hours 5 minutes." in said and "Last measured " in said


@needs_node
@pytest.mark.parametrize("tag", ["glucocraft-display-card", "glucocraft-display-card-wide"])
def test_the_lo_hi_screen_names_yesterday_after_midnight(tag):
    """The same stretch of 2 h 5 min seen at 1:06 AM began the day before: "yesterday at" its clock time, on the
    screen and in the text description (a line too long for the room wraps; joined again here)."""
    _clock_at(1, 6)
    out = _card(
        tag, value=40, zone="very_low", label="Very low", limit="low", unit="mg/dL", extra=_STRETCH.format(mg=52)
    )["extra"]
    joined = " ".join(re.findall(r"<text[^>]*>([^<]*)</text>", out["svg"]))
    assert "Since yesterday at 11:01 PM (2 h 5 min)" in joined, joined
    assert "yesterday at 10:59 PM" in joined.split("Last measured", 1)[1], joined
    assert "Since yesterday at 11:01 PM, for 2 hours 5 minutes." in out["label"]


@needs_node
def test_the_lo_hi_screens_times_on_earlier_days_and_untrusted_times():
    """The LO / HI screen's times as on the panels (gc_units.h day_time_at, duration_text): a time on the day before
    with "yesterday at", an earlier one with its date, a stretch of a day or more in days and hours; no time while the
    reading times cannot be trusted, and no "Since" for a start ahead of the clock."""
    extra = """
      const now = new Date(), n = Math.floor(now.getTime() / 1000);
      const since = (t, untrusted = false) => card._sinceText({ since: t, now, untrusted });
      const lm = (ts, untrusted = false) => card._lastMeasuredText({ lastMeasured: [52, ts], now, untrusted });
      return [since(n - 26 * 3600), since(n - 3 * 86400 - 600), since(n - 600, true), since(n + 600),
        lm(n - 3 * 86400), lm(n - 60, true)];
    """
    out = _card("glucocraft-display-card", value=40, zone="very_low", label="Very low", limit="low", extra=extra)
    one_day, three_days, untrusted, ahead, measured, measured_untrusted = out["extra"]
    clock = r"\d{1,2}:\d\d( AM| PM)?"
    assert re.fullmatch(rf"Since (yesterday at|[A-Z][a-z]{{2}} \d{{1,2}} at) {clock} \(1 d 2 h\)", one_day), one_day
    assert re.fullmatch(rf"Since [A-Z][a-z]{{2}} \d{{1,2}} at {clock} \(3 d 0 h\)", three_days), three_days
    assert untrusted == "" and ahead == ""
    assert re.fullmatch(rf"Last measured: 52 mg/dL, [A-Z][a-z]{{2}} \d{{1,2}} at {clock}", measured), measured
    assert measured_untrusted == "Last measured: 52 mg/dL"


def test_the_lo_hi_screens_texts_are_the_panels():
    """The display card and the panels show the LO / HI screen with the same texts (their catalogues)."""
    front = json.loads((R_I18N / "frontend.json").read_text(encoding="utf-8"))
    panel = yaml.safe_load(PANEL_EN.read_text(encoding="utf-8")) if PANEL_EN.exists() else None
    if panel is None:  # the panel configurations are not at hand
        pytest.skip("no panel catalogue")
    pairs = {
        "card.limit_screen.since": "panel.since.line",
        "card.limit_screen.days": "panel.duration.days",
        "limit_time.hours": "panel.duration.hours",
        "limit_time.minutes": "panel.duration.minutes",
        "card.limit_screen.when_yesterday": "panel.limit_screen.when_yesterday",
        "card.no_reading.when_date": "panel.no_reading.when_date",
        "card.limit_screen.last_measured_day": "panel.limit_screen.last_measured_day",
        "card.limit_screen.last_measured_value": "panel.limit_screen.last_measured_value",
    }
    for card_key, panel_key in pairs.items():
        assert front[card_key] == panel[panel_key], (card_key, panel_key)
    assert (
        front["card.limit_screen.last_measured"].replace("{when}", "{time}")
        == panel["panel.limit_screen.last_measured"]
    )


@needs_node
def test_the_lo_hi_screen_without_the_stretch_attributes_shows_two_lines():
    """An older GlucoCraft without limit_since and the last measured value: the zone word and the range line only."""
    out = _card("glucocraft-display-card", value=40, zone="very_low", label="Very low", limit="low")
    texts = re.findall(r"<text[^>]*>([^<]*)</text>", out["svg"])
    assert "Very low" in texts and "Below sensor range (LO)" in " ".join(texts)
    assert not any(t.startswith(("Since", "Last measured")) for t in texts)


@needs_node
def test_after_the_no_current_data_limit_the_no_current_data_screen_with_last_reading_lo():
    out = _card(
        "glucocraft-display-card",
        value=40,
        zone="very_low",
        label="Very low",
        limit="low",
        age_min=40,
        status="no_current_data",
        no_current="on",
    )
    texts = re.findall(r"<text[^>]*>([^<]*)</text>", out["svg"])
    assert "Last reading: LO" in texts and "Below sensor range (LO)" not in texts


_RUNS = """
  const now = Math.floor(Date.now() / 1000);
  card._delta = null;
  card._bf = {{ at: now, ts: [], val: [], newest: now, trends: [], dirs: [], std: null,
    stats: [0, 1, 2, 3, 4, 5, 6].map(() => ({{ mean: 110, sd: 10, cv: 9, gmi: 6, tir: 90, titr: 60, low: 5, high: 5,
      clo: 0, chi: 0, min: 60, max: 200 }})),
    limitSeconds: [0, 1, 2, 3, 4, 5, 6].map(() => [3600, 0]),
    limitRuns: [[now - 3 * 3600, now - 2 * 3600, "{kind}"]] }};
  card._go({page}); card._render();
  return root.querySelector("svg").innerHTML;
"""


@needs_node
@pytest.mark.parametrize("tag", ["glucocraft-display-card", "glucocraft-display-card-wide"])
@pytest.mark.parametrize("page", [0, 1])
@pytest.mark.parametrize("kind,letters,color", [("low", "LO", "#c62828"), ("high", "HI", "#1565c0")])
def test_every_graph_marks_a_stretch_of_limit_readings_at_its_edge(tag, page, kind, letters, color):
    """A stretch of limit readings (the files' GCR1 block) is a row of small marks along the graph's bottom (LO) or top
    (HI) edge in the very-low / very-high color over a black strip; no letters and no value."""
    svg = _card(tag, extra=_RUNS.format(kind=kind, page=page))["extra"]
    strip = re.findall(rf'<rect x="(\d+)" y="(\d+)" width="(\d+)" height="8" fill="#000000" data-limit="{kind}"/>', svg)
    assert len(strip) == 1
    marks = re.findall(rf'<rect x="\d+" y="(\d+)" width="2" height="6" fill="{color}"/>', svg)
    assert len(marks) >= 2 and len(set(marks)) == 1
    graph_h = 150 if tag == "glucocraft-display-card" else 228
    assert int(strip[0][1]) == (graph_h - 8 if kind == "low" else 0)
    assert f">{letters}</text>" not in svg
    # the marks over the line and under the graph's labels: in the graph's drawing, its texts come after the strip
    assert "<text" in svg[svg.rindex("data-limit=") :].split("</svg>")[0]


@needs_node
def test_a_period_with_limit_readings_alone_has_its_times_on_the_card():
    """The bands stay; the values show ---."""
    extra = (
        "card._delta = null; card._bf = { stats: [0, 1, 2, 3, 4, 5, 6].map(() => ({ mean: 0, sd: NaN, cv: NaN, gmi: 0, tir: 0, titr: 0, low: 0, high: 0,"
        " clo: 100, chi: 0, min: 0, max: 0 })), limitSeconds: [[600, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0]] };"
        " const s = card._statsFor(0); return [s.a, s.b, s.right];"
    )
    a, b, right = _card("glucocraft-display-card", value=40, zone="very_low", label="", limit="low", extra=extra)[
        "extra"
    ]
    assert dict(a)["min"] == "---" and dict(a)["max"] == "---" and dict(a)["tir"] == "0%"
    assert dict(b)["very_low"].startswith("100") and dict(right)["mean"] == "---"


# ---------------------------------------------------------------------------------------------------------- the panels
# the example panel configurations are optional (conftest.panel_folder): without them these tests are skipped
needs_panels = pytest.mark.skipif(PANELS is None, reason=NO_PANELS)


def _panel(name: str) -> str:
    return (PANELS / name).read_text(encoding="utf-8")


@needs_panels
def test_the_panels_draw_no_limit_text_on_any_graph():
    """No "Sensor limit" line and no letters on the panels' graphs, as on the display card."""
    for name in (
        "glucocraft-panel.yaml",
        "glucocraft-guition-35.yaml",
        "glucocraft-waveshare-lcd5.yaml",
        "glucocraft-waveshare-lcd7.yaml",
        "glucocraft-waveshare-p4-7b.yaml",
    ):
        text = _panel(name)
        assert "limit_lines" not in text and "lim_a" not in text and "n_avoid" not in text
        for period in ("24h", "7d", "14d", "30d", "90d", "180d"):
            assert f"draw_title(cv, gct::graph_title_{period});" in text, (name, period)
        assert "fmt_extreme" not in text and "is_limit" not in text


@needs_panels
def test_the_panels_keep_a_limit_reading_out_of_the_six_hour_ring():
    text = _panel("glucocraft-panel.yaml")
    assert "const bool measured = gcu::limit() == 0;" in text
    assert "&& same_slot && measured)" in text and "if (measured) {" in text
    # the mark can arrive just after the value: the reading just stored is taken out again, and the saved reading marked
    block = text[text.index("id: ha_limit\n") : text.index("id: ha_limit_text")]
    assert "id(glucose_history_count)--;" in block and "id(saved_glucose_limit) = gcu::limit();" in block


@needs_panels
def test_the_panels_keep_no_texts_of_the_removed_warning_line():
    h = (PANELS / "gc_units.h").read_text(encoding="utf-8")
    assert "limit_lines" not in h and "limit_duration" not in h and "graph_limit_" not in h
    catalogue = (PANELS / "i18n" / "en.yaml").read_text(encoding="utf-8")
    assert "panel.graph.limit_" not in catalogue


@needs_panels
def test_the_panel_reads_the_trailer():
    h = (PANELS / "glucocraft_panel_binary.h").read_text(encoding="utf-8")
    assert 'memcmp(data + size - 4, "GCL2", 4)' in h and "GCL1" not in h
    assert "gc_read_limit_time(data, size, backfill::limit_lo_s, backfill::limit_hi_s, 7);" in h
    assert (
        "gc_read_limit_time(backfill::ds_buf, backfill::ds_size, backfill::limit_lo_s, backfill::limit_hi_s, 2);" in h
    )


# ------------------------------------------------------------------------------------------------------------ the texts
def _texts() -> str:
    parts = [
        (PKG / "strings.json").read_text(encoding="utf-8"),
        (PKG / "translations" / "en.json").read_text(encoding="utf-8"),
        *(p.read_text(encoding="utf-8") for p in (PKG / "i18n" / "en").glob("*.json")),
    ]
    return "\n".join(parts)


def test_no_text_says_counted_at_the_limit():
    text = _texts()
    assert not re.search(r"counted at (the|\{values\}|its|40)", text)
    assert "at the limit in every" not in text
    assert (
        "plot it at" not in text
        and "plotted at the sensor's limit" not in text
        and "drawn at the sensor's limit" not in text
    )


def test_the_documentation_gives_the_reasons_and_the_examples():
    docs = (PKG / "i18n" / "en" / "documentation.json").read_text(encoding="utf-8")
    assert "Why limit readings are left out of the calculations and still counted in the time in ranges" in docs
    for cite in ("{cite:abbottSoftware}", "{cite:spartano2025}", "{cite:ultragenyx2024}", "{cite:abbottHiLo}"):
        assert cite in docs
    www = (PKG / "www" / "glucocraft-docs.js").read_text(encoding="utf-8")
    for ref in ("spartano2025:", "ultragenyx2024:", "abbottHiLo:", "10.1210/clinem/dgae626", "NCT03970278"):
        assert ref in www


# the 6-hour period with limit seconds of both kinds and the statistics given, the card drawn on the 6-hour page
_LIMIT_BOTH = """
  card._delta = null;
  card._bf = {{ at: Math.floor(Date.now() / 1000), ts: [], val: [], newest: 0, trends: [], dirs: [], std: null,
    stats: [0, 1, 2, 3, 4, 5, 6].map(() => ({stats})), limitSeconds: [[7500, 900], [0, 0], [0, 0], [0, 0], [0, 0],
    [0, 0], [0, 0]], limitRuns: [] }};
  card._render();
  return {{ svg: root.querySelector("svg").innerHTML, label: root.querySelector("svg").attrs["aria-label"],
    cells: card._statsFor(0) }};
"""
_MEASURED = (
    "{ mean: 110, sd: 10, cv: 9, gmi: 6, tir: 90, titr: 60, low: 5, high: 5, clo: 0, chi: 0, min: 60, max: 200 }"
)
_ZERO = "{ mean: 0, sd: 0, cv: 0, gmi: 0, tir: 0, titr: 0, low: 0, high: 0, clo: 0, chi: 0, min: 0, max: 0 }"


@needs_node
@pytest.mark.parametrize("compact", [0, 360])
def test_no_limit_warning_on_the_graphs_in_any_layout(compact):
    """With LO and HI both present the card's graphs carry no "Sensor limit" line, in the square and the compact
    layout."""
    out = _card("glucocraft-display-card", compact=compact, extra=_LIMIT_BOTH.format(stats=_MEASURED))["extra"]
    assert "Sensor limit" not in out["svg"]


_SHORT_RUN = """
  const now = Math.floor(Date.now() / 1000), run = [now - 1860, now - 1500, "{kind}"];
  const ts = [];
  for (let t = now - 3600; t <= now - 60; t += 120) if (t < run[0] || t > run[1]) ts.push(t);
  card._delta = null;
  card._bf = {{ at: now, ts, val: ts.map(() => 120), newest: ts[ts.length - 1], trends: [], dirs: [], std: null,
    stats: [0, 1, 2, 3, 4, 5, 6].map(() => ({{ mean: 110, sd: 10, cv: 9, gmi: 6, tir: 90, titr: 60, low: 5, high: 5,
      clo: 0, chi: 0, min: 60, max: 200 }})),
    limitSeconds: [0, 1, 2, 3, 4, 5, 6].map(() => [0, 0]), limitRuns: {runs} }};
  card._go(0); card._render();
  return root.querySelector("svg").innerHTML;
"""


@needs_node
@pytest.mark.parametrize("kind", ["low", "high"])
def test_the_cards_line_breaks_across_a_stretch_shorter_than_a_gap(kind):
    """A 6-minute stretch of limit readings between readings every 2 minutes is no gap by the gap rule, and still the
    line breaks there, as on the panels (gc_crosses_run): a line across it would look like measured values."""

    def moves(runs):
        svg = _card("glucocraft-display-card", extra=_SHORT_RUN.format(kind=kind, runs=runs))["extra"]
        d = re.search(r'<path d="([^"]*)" fill="none" stroke="#000000" stroke-width="4"', svg).group(1)
        return d.count("M")

    assert moves("[]") == 1
    assert moves("[run]") == 2


@needs_node
def test_a_period_below_70_percent_shows_dashes_in_every_cell_with_or_without_limit_readings():
    """The 70% rule: GlucoCraft sends every statistic of such a period as zero (mean 0, every band 0); the card shows
    "---" in every cell, also when the period has limit readings (unlike a period of limit readings alone, whose times
    are in the bands), and its text description says that the period has not enough data for statistics."""
    out = _card("glucocraft-display-card", extra=_LIMIT_BOTH.format(stats=_ZERO))["extra"]
    cells = dict(out["cells"]["a"] + out["cells"]["b"] + out["cells"]["right"])
    assert set(cells.values()) == {"---"}, cells
    assert "6-hour graph: not enough data for statistics." in out["label"]


@needs_node
def test_a_period_of_limit_readings_alone_is_described_as_such():
    stats = "{ mean: 0, sd: NaN, cv: NaN, gmi: 0, tir: 0, titr: 0, low: 0, high: 0, clo: 100, chi: 0, min: 0, max: 0 }"
    out = _card("glucocraft-display-card", extra=_LIMIT_BOTH.format(stats=stats))["extra"]
    assert "6-hour graph: readings beyond the sensor's range only, no measured value." in out["label"]
    assert "no readings in this period" not in out["label"]
