# License: AGPL-3.0-only (LICENSE).
"""Smaller backend rules: Change Over Time, the GRI zones, the display unit and the PDF's texts.

Rules under test:
- Change Over Time marks a lowest or highest weekly value that is a reading at the sensor's limit (LO / HI).
- The GRI zones are described without verdict words: the quintiles of the GRI, from the lowest (first to 20th
  percentile) to the highest (81st to 100th percentile) (Klonoff et al., 2023); taking the zone from the GRI value is
  labeled a GlucoCraft rule.
- The display unit changes only through a change of the entity's options, never through another registry update.
- No user-visible text of the PDF names the International Diabetes Center.
"""

from __future__ import annotations

import re
import types
from pathlib import Path

from glucocraft import change as ch
from glucocraft import coordinator as co
from glucocraft import statistics as st

from conftest import stats_record

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "glucocraft"


def test_change_over_time_values_are_measured_numbers():
    """A reading at the sensor's limit is no value: the week's values carry no limit marks."""
    s = stats_record(min_value=55.0, max_value=300.0, limit_low=1, limit_low_seconds=300.0, covered_seconds=14 * 86400)
    v = ch._values(s, 14, True)
    assert (v["min"], v["max"]) == (55.0, 300.0)
    assert "min_is_limit" not in v and "max_is_limit" not in v


def test_gri_zone_wording_follows_the_source():
    pdf = (PKG / "report_pdf.py").read_text(encoding="utf-8") + (PKG / "i18n" / "en" / "report.json").read_text(
        encoding="utf-8"
    )
    assert "A (0-20) to E (81-100)" not in pdf
    joined = " ".join(part.strip().strip('"') for part in pdf.split("\n"))
    # the quintiles of the GRI, as the cited source describes them, without the verdict words "best" and "worst"
    assert (
        "zones A to E are the quintiles of the GRI, from the lowest (first to 20th percentile) to the highest" in joined
    )
    assert "from the best" not in joined and "to the worst" not in joined
    # taking the zone from the GRI value is labeled a GlucoCraft rule
    assert re.search(r"its zone is taken from the GRI value: [^.]*\. This method is a GlucoCraft rule\.", joined)
    assert [st.gri_zone(v) for v in (0, 20.0, 20.1, 40, 60, 80, 80.1, 100)] == ["A", "A", "B", "B", "C", "D", "E", "E"]


def test_no_international_diabetes_center_in_the_pdf():
    assert (
        "International Diabetes Center"
        not in (PKG / "report_pdf.py").read_text(encoding="utf-8").split("REFERENCES")[0]
    )


class _Coordinator:
    """The attributes the unit listener reads."""

    def __init__(self, registry_unit):
        self.updated = []
        self.applied = []
        entry = types.SimpleNamespace(options={"sensor": {"unit_of_measurement": registry_unit}})
        reg = types.SimpleNamespace(async_get=lambda entity_id: entry)
        self.entry_id = "e1"
        self._config_unit = "mg/dL"
        self.hass = types.SimpleNamespace(
            loop=types.SimpleNamespace(call_later=lambda *a: None),
            config_entries=types.SimpleNamespace(
                async_get_entry=lambda i: types.SimpleNamespace(data={}),
                async_update_entry=lambda ce, data: self.updated.append(data),
            ),
        )
        self._reg = reg

    def async_update_listeners(self):
        """Nothing to update: the test reads the entities itself."""


def test_unit_changes_only_with_an_options_change(monkeypatch):
    c = _Coordinator("mmol/L")
    monkeypatch.setattr(
        co, "current_glucose_entity_id", lambda hass, entry_id: "sensor.glucocraft_last_glucose_reading"
    )
    monkeypatch.setattr(co, "er", types.SimpleNamespace(async_get=lambda hass: c._reg))
    monkeypatch.setattr(co, "apply_display_unit", lambda hass, entry_id, unit: c.applied.append(unit))
    listener = co.GlucoCraftCoordinator._async_unit_setting_changed
    listener(c, types.SimpleNamespace(data={"action": "update", "changes": {"name": "Old"}}))  # a rename
    assert c.updated == [] and c.applied == []
    listener(c, types.SimpleNamespace(data={"action": "update", "changes": {"options": {}}}))  # a unit chosen
    assert c.updated == [{"unit": "mmol/L"}] and c.applied == ["mmol/L"]


def test_the_70_percent_rule_is_compared_before_rounding():
    """69.96% sensor active is shown as 70.0% but does not meet the 70% rule (Battelino et al., 2019, Table 2): the
    windows carry active_ok, the summary sensors sensor_active_ok, and the Change Over Time state counts active_ok."""
    from glucocraft import sensor as se

    period = 14 * 86400
    short = stats_record(data_availability=70.0, covered_seconds=0.6996 * period)  # 69.96%, shown as 70.0%
    full = stats_record(data_availability=70.0, covered_seconds=0.70 * period)
    assert short.sensor_active_ok is False and full.sensor_active_ok is True
    assert ch._values(short, 14, True) is None  # not enough data: the window is a gap
    assert ch._values(full, 14, True)["active_ok"] is True

    weeks = [ch._values(short, 14, True), ch._values(full, 14, True), None]
    coordinator = types.SimpleNamespace(change={"consensus": ch.ChangeOverTime(weeks14=weeks)})
    cot = object.__new__(se.GlucoCraftChangeOverTime)
    cot.coordinator = coordinator
    assert cot.native_value == "1 of 3"

    for cls in (se.GlucoCraftPeriodConsensus, se.GlucoCraftPeriodCustom):
        ent = object.__new__(cls)
        ent._period = "14d"
        ent.coordinator = types.SimpleNamespace(period_stats={"14d": short}, period_stats_agp={"14d": short})
        attrs = ent.extra_state_attributes
        assert attrs["sensor_active"] == 70.0 and attrs["sensor_active_ok"] is False, cls.__name__
