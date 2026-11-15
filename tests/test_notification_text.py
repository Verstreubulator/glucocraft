# License: AGPL-3.0-only (LICENSE).
"""Summary texts and behavior (notification.py) and the sensor-expiry parsing behind the expiry line (expiry.py).

Rules under test: consensus targets judged on the consensus-range statistics, time below / above range = level 1 +
level 2, "met" / "not met" only for 14 days or more with at least 70 % data, no targets for mean, GMI or GRI, values
in the display unit's decimals, citations kept, the forwarding to the phones chosen for the summaries (plain text,
the log naming a notify action by its position), and every summary ending with the line "This is a notification, not
an alarm.".
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from glucocraft import const
from glucocraft import expiry as ex
from glucocraft import notification as N
from glucocraft.i18n import t

from conftest import LA, PERIOD_KEYS, stats_record


@pytest.fixture
def coordinator(make_coordinator):
    """Factory: statistics on the ranges in use differ from the consensus ones (time in range 71 vs 65)."""

    def make(unit="mg/dL", custom=False, avail=95.0, **kw):
        c = make_coordinator(
            display_unit=unit,
            current_glucose=62.0,
            custom_ranges=custom,
            period_stats={k: stats_record(data_availability=avail) for k in PERIOD_KEYS},
            period_stats_agp={
                k: stats_record(data_availability=avail, pct_time_in_target_range=65.0) for k in PERIOD_KEYS
            },
            **kw,
        )
        if custom:
            c.range_low, c.range_high = 80, 160
        c.period_stats_agp["90d"] = stats_record(pct_time_in_target_range=58.0)
        c.last_reading_ts, c.display_status = int(time.time()) - 60, "current"  # the data are current
        return c

    return make


def assert_statement_and_last_line(text: str) -> None:
    """A summary ends with the standard statement and, as its last line, "This is a notification, not an alarm."."""
    lines = text.rstrip().splitlines()
    assert lines[-1] == t("notify.last_line") == "This is a notification, not an alarm."
    assert f"*{N.short_statement()}*" in lines[-3:]


# ------------------------------------------------------------------------------------------------ summaries
@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
class TestSummaries:
    def test_daily_no_marks_for_24_hours(self, coordinator, unit):
        daily = N._build_daily_summary(coordinator(unit, custom=True))
        assert "): met" not in daily and "): not met" not in daily
        assert "Shown without met / not met" in daily and "Shown for information" not in daily

    def test_weekly_marks_only_on_14_days(self, coordinator, unit):
        weekly = N._build_weekly_summary(coordinator(unit, custom=True))
        wk7, wk14 = weekly.split("Rolling 14 Days")
        assert "): met" not in wk7 and "): not met" not in wk7
        assert "): met" in wk14

    def test_consensus_statistics_not_custom(self, coordinator, unit):
        daily = N._build_daily_summary(coordinator(unit, custom=True))
        assert ": 65.0%" in daily and "Time in range" in daily  # the consensus value, not 71.0

    def test_level_1_plus_level_2(self, coordinator, unit):
        wk14 = N._build_weekly_summary(coordinator(unit, custom=True)).split("Rolling 14 Days")[1]
        assert "4.1%" in wk14 and "25.4%" in wk14  # 2.5 + 1.6, 20.1 + 5.3
        # the target in words: a phone's screen reader reads the notification as written
        assert "consensus target less than 4%" in wk14 and "): not met" in wk14 and "<4%" not in wk14

    def test_no_targets_for_mean_gmi_gri(self, coordinator, unit):
        text = N._build_weekly_summary(coordinator(unit)) + N._build_monthly_summary(coordinator(unit))
        for forbidden in ("<154", "<7.0", "≤20", "ADA", "<8.6"):
            assert forbidden not in text
        assert "No consensus target: mean" in text

    def test_gmi_only_from_14_days(self, coordinator, unit):
        wk7, wk14 = N._build_weekly_summary(coordinator(unit)).split("Rolling 14 Days")
        # the first GMI of the summary is spelled out
        assert "GMI" not in wk7 and "glucose management indicator (GMI) 6.9%" in wk14

    def test_citations(self, coordinator, unit):
        weekly = N._build_weekly_summary(coordinator(unit))
        for cite in (
            "Battelino et al., 2019",
            "Monnier et al., 2017",
            "Klonoff et al., 2023",
            "Bergenstal et al., 2018",
        ):
            assert cite in weekly

    def test_comparison_on_consensus_statistics(self, coordinator, unit):
        daily = N._build_daily_summary(coordinator(unit))  # 65.0 - 58.0
        assert "last 14 days compared with the last 90 days: 7.0 percentage points higher." in daily

    def test_current_value_in_display_unit(self, coordinator, unit):
        daily = N._build_daily_summary(coordinator(unit))
        assert (
            "Last reading: 62 milligrams per deciliter" if unit == "mg/dL" else "Last reading: 3.4 millimoles per liter"
        ) in daily
        assert "62.0 mg/dL" not in daily

    def test_ends_with_statement_and_terms(self, coordinator, unit):
        for text in (
            N._build_daily_summary(coordinator(unit)),
            N._build_weekly_summary(coordinator(unit)),
            N._build_monthly_summary(coordinator(unit)),
        ):
            assert_statement_and_last_line(text)


def test_mmol_thresholds_as_published(coordinator):
    daily = N._build_daily_summary(coordinator("mmol/L"))
    assert (
        "(3.9 to 10.0)" in daily and "below 3.0" in daily and "above 13.9" in daily and "millimoles per liter" in daily
    )
    assert "3.89" not in daily and "180 mg/dL" not in daily


def test_little_data_shows_the_not_enough_lines_only(coordinator):
    """60% sensor active is not enough data (the enough-data rule, 70%): neither period of the weekly summary is
    shown, so no marks, no GMI and no values; each says how much the sensor has covered."""
    weekly = N._build_weekly_summary(coordinator(avail=60.0))
    assert "): met" not in weekly and "): not met" not in weekly and "GMI" not in weekly and "Mean:" not in weekly
    assert "Last 7 days: not enough data yet." in weekly and "So far it has covered 60%." in weekly


def test_the_enough_data_rule_on_the_unrounded_value(coordinator):
    """69.96% sensor active is shown as 70.0% with one decimal, but it is below the 70% rule: the 14-day section is its
    not-enough line, with the coverage on its side of the rule (69.96%); exactly 70% shows the section with its marks."""
    c = coordinator(avail=70.0)
    secs = 14 * 86400
    for key in ("14d",):
        for s in (c.period_stats[key], c.period_stats_agp[key]):
            s.covered_seconds, s.period_seconds = 0.6996 * secs, secs
    weekly = N._build_weekly_summary(c)
    assert "Last 14 days: not enough data yet." in weekly and "So far it has covered 69.96%." in weekly
    assert "Rolling 14 Days" not in weekly
    for s in (c.period_stats["14d"], c.period_stats_agp["14d"]):
        s.covered_seconds = 0.70 * secs  # exactly 70%: meets the rule
    weekly = N._build_weekly_summary(c)
    assert "Rolling 14 Days" in weekly and "Last 14 days: not enough data yet." not in weekly
    assert "met" in weekly.split("Rolling 14 Days")[1]


def test_custom_ranges_line(coordinator):
    text = N._build_daily_summary(coordinator(custom=True))
    assert "Custom ranges: in range 80 to 160:" in text and " per day); below 80: " in text
    assert "Custom ranges" not in N._build_daily_summary(coordinator(custom=False))


@pytest.mark.parametrize(
    "builder, key, needed",
    [
        (N._build_daily_summary, "24h", "Last 24 hours: not enough data yet."),
        (N._build_weekly_summary, "7d", "Last 7 days: not enough data yet."),
        (N._build_monthly_summary, "30d", "Last 30 days: not enough data yet."),
    ],
)
def test_not_enough_data(coordinator, builder, key, needed):
    """A summary needs enough data for its period (sensor active at least 70%, unrounded), whatever the number of
    readings."""
    c = coordinator()
    c.period_stats[key] = stats_record(data_availability=69.99, reading_count=50000)  # many readings do not count
    text = builder(c)
    assert needed in text and "GlucoCraft shows a period's statistics once the sensor has covered at least 70%" in text
    assert_statement_and_last_line(text)
    c.period_stats[key] = stats_record(data_availability=70.0, reading_count=10)  # 70% of the period: shown
    assert needed not in builder(c)


def test_monthly_sections_by_data(coordinator):
    # 90 days with 69.9%: 63 days needed, 62.91 covered, 0.09 more: at least 1 day; 6 months without readings: 0%,
    # 126 days needed
    c = coordinator()
    c.period_stats["90d"] = stats_record(data_availability=69.9, period_seconds=90 * 86400)
    c.period_stats["180d"] = None
    # the consensus-range statistics come from the same readings, so they lack the same data (a comparison is judged
    # on them: _tir_comparison)
    c.period_stats_agp["90d"] = stats_record(data_availability=69.9, period_seconds=90 * 86400)
    c.period_stats_agp["180d"] = None
    text = N._build_monthly_summary(c)
    assert "Rolling 30 Days" in text and "Last 90 days: not enough data yet." in text
    assert "So far it has covered 69.90%. With no gaps, about 1 more day is needed." in text
    assert "Last 6 months: not enough data yet." in text and "So far it has covered 0%." in text
    assert "about 126 more days are needed" in text and "Long-Term Comparison" not in text
    assert text.count("This method is a GlucoCraft rule.") == 1  # the intro


def test_weekly_14_day_section_by_data(coordinator):
    # 14 days with 40%: 9.8 days needed, 5.6 covered, 4.2 more: about 4 days
    c = coordinator()
    c.period_stats["14d"] = stats_record(data_availability=40.0)
    text = N._build_weekly_summary(c)
    assert "Last 14 days: not enough data yet." in text and "So far it has covered 40%." in text
    assert "With no gaps, about 4 more days are needed." in text


def _stats_of_source(interval: int, hours: float, period_seconds: int):
    """The statistics of one source that read every `interval` seconds for the last `hours` of a period."""
    from glucocraft.statistics import compute_period_stats
    from glucocraft.storage import GlucoseReading

    now = 1_800_000_000
    count = int(hours * 3600 / interval)
    readings = [GlucoseReading(now - (count - k) * interval, 120.0) for k in range(count)]
    return compute_period_stats(readings, "x", period_seconds, interval)


@pytest.mark.parametrize("interval", [60, 900], ids=["1-minute source", "15-minute source"])
@pytest.mark.parametrize(
    "builder, key, seconds",
    [
        (N._build_daily_summary, "24h", 86400),
        (N._build_weekly_summary, "7d", 7 * 86400),
        (N._build_monthly_summary, "30d", 30 * 86400),
    ],
)
def test_the_summary_rule_is_the_same_for_every_source_interval(coordinator, interval, builder, key, seconds):
    """The covered time decides, not the number of readings: a 1-minute and a 15-minute source with data for 70% of
    the period and 15 minutes more both get the section; with an hour less than 70%, neither does (the readings' count
    differs 15-fold)."""
    c = coordinator()
    hours = seconds / 3600 * 0.7
    c.period_stats[key] = _stats_of_source(interval, hours + 0.25, seconds)
    assert c.period_stats[key].sensor_active_exact >= 70.0
    assert "not enough data yet" not in builder(c)
    c.period_stats[key] = _stats_of_source(interval, hours - 1, seconds)
    assert c.period_stats[key].sensor_active_exact < 70.0
    assert "not enough data yet" in builder(c)


@pytest.mark.parametrize(
    "pct, text",
    [
        (0, "0 minutes"),
        (4.1, "59 minutes"),
        (65.0, "15 hours 36 minutes"),
        (100, "24 hours"),
        (25.4, "6 hours 6 minutes"),
        (100 / 1440, "1 minute"),
        (100 * 61 / 1440, "1 hour 1 minute"),
    ],
)
def test_pct_to_time(pct, text):
    assert N._pct_to_time(pct) == text


def test_pct_to_time_rounds_halves_away_from_zero():
    """The one rounding rule (units.round_half_up), not round() with halves to even: 0.5 and 2.5 minutes."""
    assert N._pct_to_time(100 * 0.5 / 1440) == "1 minute"
    assert N._pct_to_time(100 * 2.5 / 1440) == "3 minutes"


# ------------------------------------------------------------------------------------------------ expiry line
@pytest.mark.parametrize(
    "hours, text",
    [
        (71.6, "Sensor expires in 2 days 23 hours"),
        (72.0, "Sensor expires in 3 days"),
        (47.5, "Sensor expires in 1 day 23 hours"),
        (25.4, "Sensor expires in 1 day 1 hour"),
        (24.4, "Sensor expires in 24 hours"),
        (24.99, "Sensor expires in 24 hours"),
        (23.46, "Sensor expires in 23 hours"),
        (1.0, "Sensor expires in 1 hour"),
        (0.99, "Sensor expires in less than 1 hour"),
        (0.049, "Sensor expires in less than 1 hour"),
        (0, "Sensor expired"),
        (-3, "Sensor expired"),
        (70.5, "Sensor expires in 2 days 22 hours"),
    ],
)
def test_expiry_text(hours, text):
    """The exact time left in whole hours, rounded down once (as the display cards count it), so that the line never
    says more time than is left; "Sensor expired" only when none is left (2.9 minutes left is less than 1 hour). Whole
    hours first, so it never reads "2 days 24 hours"; in words and without a warning sign."""
    assert N._expiry_text(hours) == text


def test_expiry_line_in_daily_summary(coordinator):
    """From the coordinator's expiry (coordinator.expires_at), never from an entity id of its own: a renamed Sensor
    Expiry entity keeps the line."""
    c = coordinator()
    c.expires_at = datetime.fromtimestamp(time.time(), UTC) + timedelta(hours=25.4)
    assert "Sensor expires in 1 day 1 hour" in N._build_daily_summary(c)
    c.expires_at = datetime.fromtimestamp(time.time(), UTC) - timedelta(hours=1)
    assert "Sensor expired" in N._build_daily_summary(c)
    c.expires_at = None  # no expiry entity picked, or its value not known
    assert "Sensor expires" not in N._build_daily_summary(c) and "Sensor expired" not in N._build_daily_summary(c)


def test_expiry_line_rounds_once(coordinator):
    """The exact time left, rounded once (down, as the display cards count it): 2.96 h is 2 hours, never 3 from 3.0 h,
    and 2.9 minutes left is less than 1 hour, never "Sensor expired" from 0.0 h."""
    c = coordinator()
    c.expires_at = datetime.fromtimestamp(time.time(), UTC) + timedelta(hours=2.96)
    assert "Sensor expires in 2 hours" in N._build_daily_summary(c)
    c.expires_at = datetime.fromtimestamp(time.time(), UTC) + timedelta(hours=0.049)
    assert "Sensor expires in less than 1 hour" in N._build_daily_summary(c)


class TestParseExpiry:
    def test_whole_days_are_end_of_day(self):
        now = datetime(2026, 10, 22, 15, 0, tzinfo=LA)
        assert ex.whole_days_left_expiry(0, now) == datetime(2026, 10, 23, 0, 0, tzinfo=LA)
        assert ex.whole_days_left_expiry(1, now) == datetime(2026, 10, 24, 0, 0, tzinfo=LA)
        assert ex.whole_days_left_expiry(9, now).utcoffset() == timedelta(0)

    def test_end_of_day_across_fall_back(self):
        now = datetime(2026, 10, 31, 12, 0, tzinfo=LA)
        assert ex.whole_days_left_expiry(1, now) == datetime(2026, 11, 2, 0, 0, tzinfo=LA)  # midnight PST

    def test_zero_days_left_is_not_expired(self):
        exp, f = ex.parse_expiry(SimpleNamespace(state="0", attributes={"unit_of_measurement": "d"}))
        assert exp > datetime.fromtimestamp(time.time(), UTC) and f == "time_left_days"

    @pytest.mark.parametrize(
        "state, unit, seconds",
        [("1.5", "days", 1.5 * 86400), ("5", "h", 5 * 3600), ("90", "min", 5400), ("2", "hours", 7200)],
    )
    def test_time_left(self, state, unit, seconds):
        exp, _ = ex.parse_expiry(SimpleNamespace(state=state, attributes={"unit_of_measurement": unit}))
        assert abs((exp - datetime.fromtimestamp(time.time(), UTC)).total_seconds() - seconds) < 5

    def test_formats(self):
        e, f = ex.parse_expiry(SimpleNamespace(state="2026-10-25T08:00:00+00:00", attributes={}))
        assert f == "datetime" and e == datetime(2026, 10, 25, 8, 0, tzinfo=UTC)
        e, f = ex.parse_expiry(SimpleNamespace(state="2026-10-25T08:00:00", attributes={}))  # naive = local time
        assert f == "datetime" and e == datetime(2026, 10, 25, 15, 0, tzinfo=UTC)
        e, f = ex.parse_expiry(SimpleNamespace(state="2026-10-25", attributes={}))
        assert f == "date" and e == datetime(2026, 10, 25, 7, 0, tzinfo=UTC)
        e, f = ex.parse_expiry(SimpleNamespace(state="1761379200", attributes={}))
        assert f == "epoch_s" and e == datetime.fromtimestamp(1761379200, UTC)
        e, f = ex.parse_expiry(SimpleNamespace(state="1761379200000", attributes={}))
        assert f == "epoch_ms" and e == datetime.fromtimestamp(1761379200, UTC)

    def test_a_date_is_a_date_with_home_assistants_parser(self, monkeypatch):
        """Home Assistant's parse_datetime (ciso8601) reads a date without a time as its midnight (the stub does not);
        parse_expiry still reports the date form, and a text with a time stays a date and time."""
        stub_parse = ex.dt_util.parse_datetime

        def ha_parse_datetime(value):
            if len(value) == 10:  # "YYYY-MM-DD", as ciso8601 reads it: naive midnight
                return datetime.fromisoformat(value + "T00:00:00")
            return stub_parse(value)

        monkeypatch.setattr(ex.dt_util, "parse_datetime", ha_parse_datetime)
        e, f = ex.parse_expiry(SimpleNamespace(state="2026-10-25", attributes={}))
        assert f == "date" and e == datetime(2026, 10, 25, 7, 0, tzinfo=UTC)
        e, f = ex.parse_expiry(SimpleNamespace(state="2026-11-01", attributes={}))  # the fall-back day: midnight PDT
        assert f == "date" and e == datetime(2026, 11, 1, 7, 0, tzinfo=UTC)
        e, f = ex.parse_expiry(SimpleNamespace(state="2026-10-25T00:00:00", attributes={}))
        assert f == "datetime" and e == datetime(2026, 10, 25, 7, 0, tzinfo=UTC)

    @pytest.mark.parametrize(
        "state, fmt_",
        [
            (None, "missing"),
            ("unknown", "unavailable"),
            ("", "unavailable"),
            ("soon", "unrecognized"),
            ("12", "unrecognized"),
        ],
    )
    def test_not_guessed(self, state, fmt_):
        st = None if state is None else SimpleNamespace(state=state, attributes={})
        assert ex.parse_expiry(st) == (None, fmt_)


# ------------------------------------------------------------------------------------------------ phones
SUMMARY_TITLE = "GlucoCraft Daily Summary"
SUMMARY_TEXT = "**GlucoCraft Daily Summary**\n\n*No data.*"


def forward(hass, cfg: dict) -> None:
    """Forward one summary to the phones chosen in `cfg` (notification.async_forward_to_phones)."""
    asyncio.run(N.async_forward_to_phones(hass, cfg, "summaries", SUMMARY_TITLE, SUMMARY_TEXT))


def test_summaries_forwarded_to_chosen_phones_as_plain_text(fake_hass):
    hass = fake_hass(services=("notify.phone",))
    forward(hass, {const.CONF_PHONE_TARGETS: ["phone", "missing"], const.CONF_PHONE_TYPES: ["summaries"]})
    phone = [d for dom, srv, d in hass.services.calls if dom == "notify"]
    assert len(phone) == 1 and phone[0]["title"] == SUMMARY_TITLE and "**" not in phone[0]["message"]
    assert phone[0]["message"] == "GlucoCraft Daily Summary\n\nNo data."  # the text as given, as plain text


def test_the_log_names_a_notify_action_by_its_position(fake_hass, caplog):
    """A notify action's name usually names a person's phone (the diagnostics redact it): the log gives its position in
    the settings instead."""
    hass = fake_hass(services=("notify.mobile_app_phone_a", "notify.mobile_app_phone_b"))
    real_call = hass.services.async_call

    async def call(domain, service, data=None, **kwargs):
        if service == "mobile_app_phone_b":
            raise RuntimeError("push failed")
        return await real_call(domain, service, data, **kwargs)

    hass.services.async_call = call
    targets = ["mobile_app_phone_a", "mobile_app_phone_c", "mobile_app_phone_b"]
    with caplog.at_level("WARNING"):
        forward(hass, {const.CONF_PHONE_TARGETS: targets, const.CONF_PHONE_TYPES: ["summaries"]})
    logged = [r.getMessage() for r in caplog.records if r.name.startswith("glucocraft")]
    assert logged == [
        "GlucoCraft: notify action 2 of 3 is not available - GlucoCraft Daily Summary not sent there",
        # the error's class only: the text of a Companion app error names the phone
        "GlucoCraft: sending GlucoCraft Daily Summary to notify action 3 of 3 failed: RuntimeError",
    ]
    assert "mobile_app" not in caplog.text and "push failed" not in caplog.text


@pytest.mark.parametrize("types", [[], ["very_low"], ["low", "high"], ["no_data_notice"], ["critical_low"]])
def test_summaries_not_forwarded_unless_chosen(fake_hass, types):
    """The summaries go to the phones only when chosen (const.PHONE_TYPES: the summaries and the no-data notice, each
    on its own); a stored type of an earlier version is ignored."""
    hass = fake_hass(services=("notify.phone",))
    forward(hass, {const.CONF_PHONE_TARGETS: ["phone"], const.CONF_PHONE_TYPES: types})
    assert hass.services.calls == []
    assert const.PHONE_TYPES == ("summaries", "no_data_notice")
    assert const.DEFAULT_PHONE_TYPES == []


def test_the_summary_names_itself_a_notification_once(coordinator):
    """The last line names the summary a notification, not an alarm, once; no reliance or safety text follows it."""
    msg = N._build_daily_summary(coordinator())
    assert msg.rstrip().splitlines()[-1] == "This is a notification, not an alarm."
    for old in ("Do not rely", "reliable sources", "healthcare team", "Use at your own risk"):
        assert old not in msg, old


# ------------------------------------------------------------------------------------------------ plain text
def test_lowest_and_highest_are_measured_and_the_time_at_the_limit_is_stated(coordinator):
    """A reading at the sensor's limit is no value: lowest and highest are measured numbers; a line gives the
    time at the limit."""
    c = coordinator()
    c.period_stats["24h"] = stats_record(
        min_value=58.0, limit_low=2, limit_low_seconds=7500.0, max_value=300.0, limit_high=1, limit_high_seconds=900.0
    )
    daily = N._build_daily_summary(c)
    assert "lowest to highest: 58 to 300;" in daily
    # the time in words (ACCESSIBILITY.md rule 7), never "2 h 5 min"
    assert "Readings at the sensor's limit:" in daily and "(2 hours 5 minutes)" in daily and "(15 minutes)" in daily
    assert " h " not in daily and " min)" not in daily
    assert "left out of the values above" in daily


def test_terms_spelled_out_once_per_summary(coordinator):
    """CV, GMI and GRI are spelled out at their first use in each summary and abbreviated after it; durations and
    times per day are written in words, without "h", "min" or "/day"."""
    terms = ("Coefficient of variation (CV)", "glucose management indicator (GMI)", "Glycemia Risk Index (GRI)")
    weekly, monthly = N._build_weekly_summary(coordinator()), N._build_monthly_summary(coordinator())
    for text in (weekly, monthly):
        assert all(text.count(full) == 1 for full in terms)
    # the monthly summary's 90-day and 180-day sections use the abbreviations
    tail = monthly.split("**Rolling 90 Days**")[1]
    assert "CV: " in tail and "; GMI " in tail and "; GRI " in tail and not any(full in tail for full in terms)
    daily = N._build_daily_summary(coordinator())
    assert daily.count("Coefficient of variation (CV)") == 1 and "  CV: " in daily
    for text in (daily, N._build_weekly_summary(coordinator())):
        assert not re.search(r"\d+h\b|\d+min\b|/day| d \d| h\)", text)
        assert "⚠" not in text


def _short(c, key):
    """The period `key` with too little data (60% sensor active) in both range sets; the other periods unchanged."""
    c.period_stats[key] = stats_record(data_availability=60.0)
    c.period_stats_agp[key] = stats_record(data_availability=60.0, pct_time_in_target_range=65.0)
    return c


def test_a_short_first_period_does_not_hide_the_others(coordinator):
    """Each period of a summary is judged on its own: a first period with too little data gets its not-enough line,
    and the later periods with enough data and their comparisons are still shown."""
    weekly = N._build_weekly_summary(_short(coordinator(), "7d"))
    assert "Last 7 days: not enough data yet." in weekly and "Rolling 14 Days" in weekly
    assert "compared with the last 90 days" in weekly  # 65% against 58%: 7 points, marked
    monthly = N._build_monthly_summary(_short(coordinator(), "30d"))
    assert "Last 30 days: not enough data yet." in monthly
    assert "Rolling 90 Days" in monthly and "Rolling 6 Months" in monthly
    assert "last 30 days compared" not in monthly  # a comparison needs enough data in both of its periods
    daily = N._build_daily_summary(_short(coordinator(), "24h"))
    assert "Last 24 hours: not enough data yet." in daily and "last 14 days compared with the last 90 days" in daily
    for text in (weekly, monthly, daily):
        assert_statement_and_last_line(text)
