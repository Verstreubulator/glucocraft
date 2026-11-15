# License: AGPL-3.0-only (LICENSE).
"""The consumers of the period statistics use one value per rule (sensor.py, coordinator.py, notification.py,
panel_feed.py, __init__.py).

Rules under test:
- The combined bands (all time below the low limit, all time above the high limit) are the values rounded once
  (PeriodStats.time_below_low / time_above_high), never the sum of two rounded parts.
- The 70% rule is applied to the unrounded sensor active (PeriodStats.sensor_active_ok).
- The GRI zone is the zone of the displayed GRI; GMI in mmol/mol comes from the unrounded mean.
- A minimum or maximum at the sensor's limit is marked as LO / HI.
- A failed print is an error of the print action.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import types

import pytest
from glucocraft import coordinator as co
from glucocraft import notification as N
from glucocraft import panel_feed as pf
from glucocraft import sensor as se
from glucocraft.statistics import gmi_shown

from conftest import REPO, stats_record
from test_entity_ids import _load_init

# parts whose rounded sum (2.5 + 1.6 = 4.1) differs from the value rounded once (4.0): the case that changed the
# comparison with the consensus target of less than 4%
PARTS = {
    "time_low": 2.45,
    "time_critical_low": 1.55,
    "time_below_low": 4.0,
    "time_high": 20.05,
    "time_critical_high": 4.95,
    "time_above_high": 25.0,
}


def test_consensus_sensor_uses_the_combined_bands_and_zone():
    s = stats_record(**PARTS, gri=20.04)
    e = se.GlucoCraftPeriodConsensus.__new__(se.GlucoCraftPeriodConsensus)
    e.coordinator = types.SimpleNamespace(period_stats_agp={"14d": s})
    e._period = "14d"
    a = e.extra_state_attributes
    assert (a["time_below_70"], a["time_above_180"]) == (4.0, 25.0)
    assert (a["time_below_low"], a["time_above_high"]) == (4.0, 25.0)  # also under the PeriodStats names
    assert a["gri_zone"] == s.gri_zone and a["gmi_mmol_mol"] == s.gmi_mmol_mol
    assert "min_is_limit" not in a and "max_is_limit" not in a and a["limit_seconds"] == {"low": 0, "high": 0}


def test_gri_sensor_uses_the_keys_of_the_consensus_sensor():
    """The GRI sensor's attributes carry the keys of <period> consensus ranges: gri_hypo, gri_hyper, gri_zone."""
    s = stats_record(**PARTS, gri=20.04)
    e = se.GlucoCraftPeriodGRI.__new__(se.GlucoCraftPeriodGRI)
    e.coordinator = types.SimpleNamespace(period_stats_agp={"14d": s})
    e._period = "14d"
    a = e.extra_state_attributes
    assert (a["gri_hypo"], a["gri_hyper"], a["gri_zone"]) == (s.gri_hypo, s.gri_hyper, s.gri_zone)
    assert not {"hypo_component", "hyper_component", "zone"} & a.keys()


def test_trend_week_values_and_panel_trends_use_the_combined_bands():
    s = stats_record(**PARTS)
    v = co.GlucoCraftCoordinator.trend_week_values(s)
    assert (v["below_70"], v["above_180"]) == (4.0, 25.0)
    src = " ".join(inspect.getsource(pf._trend_records).split())
    assert "_pct32(s.time_below_low), _pct32(s.time_above_high)" in src


def test_notification_uses_the_combined_bands(make_coordinator):
    s = stats_record(**PARTS, data_availability=95.0)
    c = make_coordinator(period_stats={"14d": s}, period_stats_agp={"14d": s})
    text = "\n".join(N._consensus_lines(c, "14d", 14 * 86400, "14 days", set()))
    assert "level 1 and level 2): 4.0%" in text and "): 25.0%" in text


def test_seventy_percent_rule_on_the_unrounded_value():
    s = stats_record(data_availability=70.0, covered_seconds=0.6996 * 14 * 86400, period_seconds=14 * 86400)
    assert s.sensor_active_ok is False
    assert gmi_shown(s, 14 * 86400) is None
    assert N._marked(s, 14 * 86400) is False
    ok = stats_record(data_availability=70.0, covered_seconds=0.7001 * 14 * 86400, period_seconds=14 * 86400)
    assert gmi_shown(ok, 14 * 86400) == ok.gmi and N._marked(ok, 14 * 86400)


@pytest.mark.parametrize("cls", [se.GlucoCraftPeriodMin, se.GlucoCraftPeriodMax])
def test_minimum_and_maximum_have_no_limit_form(cls):
    """A reading at the sensor's limit is no value: a period with limit readings alone has no state, and the
    sensors carry no limit attributes."""
    s = stats_record(min_value=None, max_value=None, mean=None, limit_low=1, limit_low_seconds=300.0)
    e = cls.__new__(cls)
    e.coordinator = types.SimpleNamespace(period_stats={"14d": s}, display_unit="mg/dL")
    e._period = "14d"
    assert e.native_value is None
    assert "extra_state_attributes" not in vars(cls)


def test_failed_print_is_an_error(monkeypatch, tmp_path):
    init = _load_init()

    class Failed(Exception):
        def __init__(self, **kw):
            self.kw = kw

    monkeypatch.setattr(init, "HomeAssistantError", Failed)

    async def no_printer(*args):
        return {"ok": False, "message": "No printer address is set in GlucoCraft's settings."}

    async def refused(*args):
        return {"ok": False, "message": "Not printed: ...", "reason": "the printer refused", "file": "x.pdf"}

    coordinator = types.SimpleNamespace(reports=types.SimpleNamespace(async_print=no_printer))
    monkeypatch.setattr(init, "_coordinator_for", lambda hass: coordinator)
    calls = []

    async def call(*a, **kw):
        calls.append(a)

    hass = types.SimpleNamespace(services=types.SimpleNamespace(async_call=call))
    with pytest.raises(Failed) as err:
        asyncio.run(init._handle_print(hass, types.SimpleNamespace(data={})))
    # without a printer address no PDF is made: its own message
    assert err.value.kw["translation_key"] == "print_no_printer" and calls  # the notification is still created
    # a print the printer did not take: the printer's answer and the PDF kept, never the result text twice
    coordinator.reports.async_print = refused
    with pytest.raises(Failed) as err:
        asyncio.run(init._handle_print(hass, types.SimpleNamespace(data={})))
    assert err.value.kw["translation_key"] == "print_failed"
    assert err.value.kw["translation_placeholders"] == {"reason": "the printer refused", "file": "x.pdf"}
    strings = json.loads((REPO / "custom_components/glucocraft/strings.json").read_text(encoding="utf-8"))
    assert "{reason}" in strings["exceptions"]["print_failed"]["message"]
    assert "Report printer" in strings["exceptions"]["print_no_printer"]["message"]
    assert init.REPORT_STORE_KEYS == ("glucocraft.lab_a1c", "glucocraft.sensor_events")


def test_http_period_json_publishes_the_combined_bands():
    from glucocraft import http_api

    d = http_api._period_json(stats_record(**PARTS), 14 * 86400, "mg/dL")
    assert (d["time_below_low"], d["time_above_high"]) == (4.0, 25.0)
    assert "min_is_limit" not in d and d["limit_seconds"] == {"low": 0, "high": 0}
