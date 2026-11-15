# License: AGPL-3.0-only (LICENSE).
"""notification.py: the last line of every summary, in Home Assistant and on the phones ("This is a notification,
not an alarm."); the decimal separator and the A1C / GMI unit."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

import pytest
from glucocraft import const as C
from glucocraft import notification as N
from glucocraft.i18n import t
from glucocraft.regional import Regional

from conftest import PERIOD_KEYS, FakeCoordinator, stats_record


@dataclass
class RegionalCoordinator(FakeCoordinator):
    """A FakeCoordinator with regional settings of the test's choice."""

    rg: Regional = None

    @property
    def regional(self):
        """The regional settings of the test, else the defaults."""
        return self.rg or Regional()


def _coord(fake_hass, rg=None, **kw):
    stats = {k: stats_record(period_label=k) for k in PERIOD_KEYS}
    agp = {k: stats_record(period_label=k, pct_time_in_target_range=65.0) for k in PERIOD_KEYS}
    agp["90d"] = stats_record(pct_time_in_target_range=58.0)
    kw.setdefault("hass", fake_hass())
    c = RegionalCoordinator(period_stats=stats, period_stats_agp=agp, rg=rg, **kw)
    c.last_reading_ts, c.display_status = int(time.time()) - 60, "current"  # the data are current
    return c


# ------------------------------------------------------------------------------------------------ the last line
def test_summary_ends_with_its_last_line_in_home_assistant_and_on_the_phone(fake_hass):
    """Home Assistant's notification and the same summary on a phone end with the same last line; the phone's text is
    the Home Assistant notification's text as plain text."""
    hass = fake_hass(services=("notify.phone",))
    c = _coord(fake_hass, hass=hass)
    c.update_settings({C.CONF_PHONE_TARGETS: ["phone"], C.CONF_PHONE_TYPES: ["summaries"]})
    asyncio.run(N.async_send_daily_summary(hass, c))
    ((_, _, ha),) = [x for x in hass.services.calls if x[0] == "persistent_notification"]
    ((_, _, phone),) = [x for x in hass.services.calls if x[0] == "notify"]
    assert ha["notification_id"] == "glucocraft_daily_summary"
    last = "This is a notification, not an alarm."
    assert t("notify.last_line") == last
    assert ha["message"].splitlines()[-1] == last and phone["message"].splitlines()[-1] == last
    assert "**" not in phone["message"] and phone["message"] == N.plain_text(ha["message"])


@pytest.mark.parametrize("build", [N._build_daily_summary, N._build_weekly_summary, N._build_monthly_summary])
def test_summaries_end_with_the_last_line(fake_hass, build):
    text = build(_coord(fake_hass))
    lines = text.splitlines()
    assert lines[-1] == t("notify.last_line") and lines[-3] == f"*{N.short_statement()}*"
    assert text.count("not an alarm") == 1  # said once, in the last line


def test_insufficient_data_summary_also_ends_with_it(fake_hass):
    c = _coord(fake_hass)
    c.period_stats = {}
    assert N._build_daily_summary(c).splitlines()[-1] == t("notify.last_line")


# ------------------------------------------------------------------------------------------------ decimals and A1C unit
NUM_POINT = re.compile(r"\d\.\d")


@pytest.mark.parametrize("build", [N._build_daily_summary, N._build_weekly_summary, N._build_monthly_summary])
@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
def test_decimal_comma_in_every_summary(fake_hass, build, unit):
    rg = Regional(decimal_separator=",", a1c_unit="both")
    text = build(_coord(fake_hass, rg=rg, display_unit=unit, custom_ranges=True, range_low=80, range_high=160))
    assert not NUM_POINT.search(text), NUM_POINT.findall(text)
    assert re.search(r"\d,\d", text)


def test_decimal_point_kept_by_default(fake_hass):
    text = N._build_weekly_summary(_coord(fake_hass, display_unit="mmol/L"))
    assert "CV: 33.2%" in text and "(3.9 to 10.0)" in text and "(glucose in millimoles per liter)" in text


def test_comma_values(fake_hass):
    rg = Regional(decimal_separator=",")
    weekly = N._build_weekly_summary(_coord(fake_hass, rg=rg, display_unit="mmol/L"))
    assert "CV: 33,2%" in weekly and "(3,9 to 10,0)" in weekly and "Mean: 8,3;" in weekly
    assert "Time in range (3,9 to 10,0): 65,0%" in weekly
    daily = N._build_daily_summary(_coord(fake_hass, rg=rg, display_unit="mmol/L", current_glucose=101.0))
    assert "Last reading: 5,6 millimoles per liter " in daily and " at " in daily
    assert "7,0 percentage points higher" in daily


@pytest.mark.parametrize(
    "unit,expected",
    [
        ("percent", "glucose management indicator (GMI) 6.9% (Bergenstal"),
        ("mmol_mol", "glucose management indicator (GMI) 52 mmol/mol (Bergenstal"),
        ("both", "glucose management indicator (GMI) 6.9% (52 mmol/mol) (Bergenstal"),
    ],
)
def test_gmi_in_the_a1c_unit(fake_hass, unit, expected):
    text = N._build_weekly_summary(_coord(fake_hass, rg=Regional(a1c_unit=unit)))
    assert expected in text


def test_no_trend_arrow_leaves_no_trailing_space(fake_hass):
    text = N._build_daily_summary(_coord(fake_hass, trend_arrow=""))
    assert "Last reading: 120 milligrams per deciliter at " in text and "  at" not in text


def test_the_regional_settings_of_the_coordinator(fake_hass):
    rg = Regional(decimal_separator=",")
    assert N._rg(_coord(fake_hass, rg=rg)) is rg
