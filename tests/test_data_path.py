# License: AGPL-3.0-only (LICENSE).
"""The backend data path: sensor limits (LO / HI), the one gap rule, statistics windows, storage, retention, the report's
laboratory A1C, the CSV export and the Repairs notices.

Rules under test:
  * LO / HI (limits.py): detection in mg/dL and mmol/L (texts, translations, "Below X" / "Above Y", Dexcom 39 / 401,
    error codes, warm-up), storage at the source's limit with the limit flag, the per-source lower / upper limit
    (settings, Automatic from the source, the integration and the Nightscout device; a reading at a limit set by hand is
    LO / HI, with Automatic only a reading beyond it), the current-glucose attributes,
    the zone color, the counts per period (statistics, consensus sensors, report, PDF note);
  * one gap rule (gaps.py): gap = interval > max(15 min, 3 x the reading interval of the stretch), the reading interval
    from the readings around it; 1-, 5- and 15-minute data, mixed, outages of 10 / 16 / 60 minutes; statistics weights,
    sensor active, the report's minute grid, coverage and hypoglycemia events;
  * statistics performance: bisection windows and cached intervals give numbers identical to filtering the whole store;
  * storage version 3 (limit and source per reading);
  * retention 200 days, whole local days; 6-month report without a previous period;
    laboratory A1C without CGM comparison; CSV export; the Repairs notices (slow source, sensor cap);
    configuration attributes; strings.json = translations/en.json.
"""

from __future__ import annotations

import csv
import json
import math
import os
import random
import struct
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from glucocraft import change as ch
from glucocraft import const as C
from glucocraft import coordinator as co
from glucocraft import export as ex
from glucocraft import gaps
from glucocraft import limits as L
from glucocraft import report as R
from glucocraft import sensor as se
from glucocraft import statistics as st
from glucocraft.storage import GlucoCraftStorage, GlucoseReading
from glucocraft.units import UNIT_MMOLL

from conftest import AGP, LA, add_reading, real_coordinator

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "glucocraft"
MMOL = UNIT_MMOLL


# ================================================================================================ LO / HI detection
class TestClassify:
    @pytest.mark.parametrize("state", ["unavailable", "unknown", "???", "", "---", "?SN", "none", "abc", None, True])
    def test_no_reading_texts(self, state):
        assert L.classify(state, None).kind == "none"

    @pytest.mark.parametrize("state", ["LO", "lo", "Low", "LOW", "BAS", "niedrig", " lo "])
    def test_lo_words(self, state):
        c = L.classify(state, None, 40, 400)
        assert (c.kind, c.value, c.limit) == ("low", 40.0, "low")

    @pytest.mark.parametrize("state", ["HI", "High", "HIGH", "ÉLEVÉ", "eleve", "hoch"])
    def test_hi_words(self, state):
        c = L.classify(state, None, 40, 450)
        assert (c.kind, c.value) == ("high", 450.0)

    def test_below_and_above_texts_teach_the_limit(self):
        c = L.classify("Below 50 mg/dL", None)
        assert (c.kind, c.value, c.learned_lower) == ("low", 50.0, 50)
        c = L.classify("Above 400 mg/dL", None)
        assert (c.kind, c.value, c.learned_upper) == ("high", 400.0, 400)
        c = L.classify("Below 2.8 mmol/L", MMOL)
        assert c.learned_lower == 50
        c = L.classify("Above 22.2 mmol/L", MMOL)
        assert c.learned_upper == 400
        assert L.classify("<40", None).kind == "low" and L.classify(">500", None).learned_upper == 500

    @pytest.mark.parametrize("v", [0, -5, 1, 5, 9, 12, 12.5])
    def test_error_codes_are_no_reading(self, v):
        assert L.classify(v, None).kind == "none"
        assert L.classify(str(v), None).kind == "none"

    def test_xdrip_38_and_nightscout_below_39(self):
        assert L.classify(38, None).kind == "none"
        assert L.classify(37, None).kind == "low"  # not Nightscout: <= 39 is LO
        assert L.classify(37, None, nightscout=True).kind == "none"  # Nightscout: error codes below 39
        assert L.classify(39, None, nightscout=True).kind == "low"  # 39 = LOW (Dexcom Share)

    @pytest.mark.parametrize(
        "v, kind, value",
        [
            (39, "low", 40.0),
            (39.5, "low", 40.0),
            (40, "reading", 40.0),
            (400, "reading", 400.0),
            (401, "reading", 401.0),
            (500, "reading", 500.0),
            (500.1, "high", 500.0),
            (650, "high", 500.0),
        ],
    )
    def test_mgdl_default_limits(self, v, kind, value):
        c = L.classify(v, None)  # Automatic without information: 40 / 500
        assert (c.kind, c.value) == (kind, value)

    def test_dexcom_401_is_high(self):
        c = L.classify(401, None, 40, 500, dexcom_type=True)
        assert c.kind == "high" and c.learned_upper == 400
        assert L.classify(400, None, 40, 400, dexcom_type=True).kind == "reading"

    def test_upper_and_lower_settings(self):
        assert (L.classify(401, None, 40, 400).kind, L.classify(401, None, 40, 400).value) == ("high", 400.0)
        assert L.classify(450, None, 40, 450).kind == "reading" and L.classify(451, None, 40, 450).kind == "high"
        c = L.classify(45, None, 50, 400)
        assert (c.kind, c.value) == ("low", 50.0)
        assert L.classify(50, None, 50, 400).kind == "reading"

    @pytest.mark.parametrize(
        "v, upper, kind, value",
        [
            (2.1, 500, "low", 40.0),
            (2.2, 500, "low", 40.0),
            (2.3, 500, "reading", 41.4),
            (22.2, 400, "reading", 400.0),
            (22.3, 400, "high", 400.0),
            # compared in mg/dL: 25.0 mmol/L = 450.4 mg/dL and 27.8 = 500.8, above the upper limit (HI)
            (25.0, 450, "high", 450.0),
            (27.8, 500, "high", 500.0),
            (27.9, 500, "high", 500.0),
            (10.0, 500, "reading", 180.0),
        ],
    )
    def test_mmol(self, v, upper, kind, value):
        c = L.classify(v, MMOL, 40, upper)
        assert (c.kind, c.value) == (kind, value)

    def test_mmol_lower_limit_50(self):
        assert (L.classify(2.7, MMOL, 50, 400).kind, L.classify(2.7, MMOL, 50, 400).value) == ("low", 50.0)
        assert L.classify(2.8, MMOL, 50, 400).kind == "reading"

    # An upper limit set by hand: a reading at the limit is HI as well; Automatic: only above the limit. Compared at
    # the precision of the limits: whole mg/dL, one decimal in mmol/L (400 / 450 / 500 mg/dL = 22.2 / 25.0 / 27.8).
    @pytest.mark.parametrize(
        "v, upper, upper_set, kind, value",
        [
            (400, 400, True, "high", 400.0),
            (400, 400, False, "reading", 400.0),
            (399, 400, True, "reading", 399.0),
            (399.4, 400, True, "reading", 399.4),
            (399.5, 400, True, "high", 400.0),  # 400 at whole mg/dL
            (401, 400, True, "high", 400.0),
            (450, 450, True, "high", 450.0),
            (449, 450, True, "reading", 449.0),
            (450, 450, False, "reading", 450.0),
            (500, 500, True, "high", 500.0),
            (500, 500, False, "reading", 500.0),
        ],
    )
    def test_upper_limit_set_by_hand_mgdl(self, v, upper, upper_set, kind, value):
        c = L.classify(v, None, 40, upper, upper_set=upper_set)
        assert (c.kind, c.value) == (kind, value)

    @pytest.mark.parametrize(
        "v, upper, upper_set, kind, value",
        [
            (22.2, 400, True, "high", 400.0),  # 22.2 mmol/L = the 400 mg/dL limit as the sources show it
            (22.2, 400, False, "reading", 400.0),
            (22.1, 400, True, "reading", 398.2),
            (22.15, 400, True, "high", 400.0),  # 22.2 at one decimal
            (25.0, 450, True, "high", 450.0),
            (24.9, 450, True, "reading", 448.6),
            (27.8, 500, True, "high", 500.0),
            (27.7, 500, True, "reading", 499.0),
            (27.7, 500, False, "reading", 499.0),
        ],
    )
    def test_upper_limit_set_by_hand_mmol(self, v, upper, upper_set, kind, value):
        c = L.classify(v, MMOL, 40, upper, upper_set=upper_set)
        assert (c.kind, c.value) == (kind, value)

    # A lower limit set by hand: a reading at the limit is LO as well; Automatic: only below the limit. Compared at the
    # precision of the limits: whole mg/dL, one decimal in mmol/L (40 / 50 mg/dL = 2.2 / 2.8).
    @pytest.mark.parametrize(
        "v, lower, lower_set, kind, value",
        [
            (50, 50, True, "low", 50.0),
            (50, 50, False, "reading", 50.0),
            (51, 50, True, "reading", 51.0),
            (50.4, 50, True, "low", 50.0),  # 50 at whole mg/dL
            (50.5, 50, True, "reading", 50.5),
            (49, 50, True, "low", 50.0),
            (49, 50, False, "low", 50.0),
            (40, 40, True, "low", 40.0),
            (40, 40, False, "reading", 40.0),
            (41, 40, True, "reading", 41.0),
        ],
    )
    def test_lower_limit_set_by_hand_mgdl(self, v, lower, lower_set, kind, value):
        c = L.classify(v, None, lower, 500, lower_set=lower_set)
        assert (c.kind, c.value) == (kind, value)

    @pytest.mark.parametrize(
        "v, lower, lower_set, kind, value",
        [
            (2.8, 50, True, "low", 50.0),  # 2.8 mmol/L = the 50 mg/dL limit as the sources show it
            (2.8, 50, False, "reading", 50.4),
            (2.84, 50, True, "low", 50.0),  # 2.8 at one decimal
            (2.85, 50, True, "reading", 51.3),
            (2.9, 50, True, "reading", 52.2),
            (2.7, 50, True, "low", 50.0),
            (2.7, 50, False, "low", 50.0),
            (2.2, 40, True, "low", 40.0),
            (2.3, 40, True, "reading", 41.4),
            (2.3, 40, False, "reading", 41.4),
        ],
    )
    def test_lower_limit_set_by_hand_mmol(self, v, lower, lower_set, kind, value):
        c = L.classify(v, MMOL, lower, 500, lower_set=lower_set)
        assert (c.kind, c.value) == (kind, value)

    def test_a_limit_set_by_hand_leaves_the_other_limit(self):
        # an upper limit set by hand: the lower limit keeps the Automatic rule (only below the limit is LO)
        assert L.classify(40, None, 40, 400, upper_set=True).kind == "reading"
        assert L.classify(50, None, 50, 400, upper_set=True).kind == "reading"
        assert L.classify(2.8, MMOL, 50, 400, upper_set=True).kind == "reading"
        # a lower limit set by hand: the upper limit keeps the Automatic rule (only above the limit is HI)
        assert L.classify(400, None, 50, 400, lower_set=True).kind == "reading"
        assert L.classify(22.2, MMOL, 50, 400, lower_set=True).kind == "reading"
        # both set by hand
        both = {"lower_set": True, "upper_set": True}
        assert [L.classify(v, None, 50, 400, **both).kind for v in (50, 51, 399, 400)] == [
            "low",
            "reading",
            "reading",
            "high",
        ]
        assert [L.classify(v, MMOL, 50, 400, **both).kind for v in (2.8, 2.9, 22.1, 22.2)] == [
            "low",
            "reading",
            "reading",
            "high",
        ]

    def test_stored_limit_values(self):
        # a reading exactly at a limit, as stored: from a source in mg/dL, and at the limit's mmol/L value
        assert L.stored_limit_values(400) == (400.0,)  # 22.2 mmol/L = 400.0 mg/dL
        assert L.stored_limit_values(450) == (450.0, 450.4)  # 25.0 mmol/L = 450.4 mg/dL
        assert L.stored_limit_values(500) == (500.0, 500.8)  # 27.8 mmol/L = 500.8 mg/dL
        for limit in L.UPPER_CHOICES:
            assert max(L.stored_limit_values(limit)) == max(limit, L.to_mgdl(L.mmol_limit(limit), MMOL))

    def test_upper_set_by_hand_from_the_resolved_limits(self):
        assert L.upper_set_by_hand(L.resolve("auto", "400", None)) is True
        assert L.upper_set_by_hand(L.resolve("auto", "auto", "dexcom")) is False  # Automatic 400
        assert L.upper_set_by_hand({"upper": 500}) is False

    def test_lower_set_by_hand_from_the_resolved_limits(self):
        assert L.lower_set_by_hand(L.resolve("50", "auto", None)) is True
        assert L.lower_set_by_hand(L.resolve("auto", "auto", "carelink")) is False  # Automatic 50
        assert L.lower_set_by_hand(L.resolve("auto", "400", None)) is False
        assert L.lower_set_by_hand({"lower": 40}) is False

    def test_comma_decimal_and_warmup(self):
        assert L.classify("5,6", MMOL).value == pytest.approx(100.9)
        assert L.classify(6553.5, None).kind == "warmup" and L.classify("65535", None).kind == "warmup"


class TestResolve:
    @pytest.mark.parametrize(
        "domain, device, lower, upper",
        [
            ("dexcom", None, 40, 400),
            ("carelink", None, 50, 400),
            ("nightscout", "share2", 40, 400),
            ("nightscout", "xDrip-DexcomG6", 40, 400),
            ("nightscout", "nightscout-librelink-up", 40, 500),
            ("librelink", None, 40, 500),
            (None, None, 40, 500),
        ],
    )
    def test_automatic(self, domain, device, lower, upper):
        r = L.resolve("auto", "auto", domain, device)
        assert (r["lower"], r["upper"], r["lower_setting"], r["upper_setting"]) == (lower, upper, "auto", "auto")
        assert r["lower_reason"] and r["upper_reason"]

    def test_settings_win_and_learned_used(self):
        r = L.resolve("50", "450", "dexcom")
        assert (r["lower"], r["upper"], r["lower_setting"], r["upper_setting"]) == (50, 450, 50, 450)
        assert r["lower_reason"] is None and r["upper_reason"] is None
        r = L.resolve("auto", "auto", None, None, learned_lower=50, learned_upper=400)
        assert (r["lower"], r["upper"]) == (50, 400) and r["upper_reason"] == "shown by the source"
        assert L.resolve("123", "999", None)["upper"] == 500  # unknown values = Automatic


# ================================================================================================ the coordinator
def source_coordinator(tmp_path, config=None, secondary=None):
    """A coordinator with sensor.p in use, the settings `config` and, optionally, a second source."""
    return real_coordinator(
        tmp_path,
        _settings=dict(config or {}),
        entry_id="e1",
        active_source_entity="sensor.p",
        active_source="P",
        _secondary_entity=secondary,
        _time_attr={"sensor.p": "", **({secondary: ""} if secondary else {})},
    )


def state(value, unit="mg/dL", **attrs):
    return types.SimpleNamespace(state=value, attributes={"unit_of_measurement": unit, **attrs})


class TestCoordinatorLimits:
    def test_lo_hi_stored_at_the_limit_with_the_flag(self, tmp_path):
        c = source_coordinator(tmp_path)
        now = int(time.time())
        for k, s in enumerate(["120", "LO", "HI", "39", "130"]):
            v, lim = c._source_reading("sensor.p", state(s))
            assert c._process_reading(v, now - 300 * (5 - k), "sensor.p", "P", True, lim) == 2
        rd = c.storage.readings
        assert [r.raw for r in rd] == [120.0, 40.0, 500.0, 40.0, 130.0]
        assert [r.limit for r in rd] == [None, "low", "high", "low", None]
        assert {r.source for r in rd} == {C.SOURCE_PRIMARY}
        assert c.current_limit is None and c.current_glucose == 130.0

    def test_current_glucose_attributes_and_zone(self, tmp_path):
        c = source_coordinator(tmp_path)
        c.range_critical_low, c.range_critical_high = 35, 550  # custom ranges: the limits still color
        now = int(time.time())
        v, lim = c._source_reading("sensor.p", state("HI"))
        c._process_reading(v, now, "sensor.p", "P", True, lim)
        e = se.GlucoCraftCurrentGlucose.__new__(se.GlucoCraftCurrentGlucose)
        e.coordinator = c
        a = e.extra_state_attributes
        # no number during a limit reading; the stored limit stays for the panels, marked by "limit"
        assert e.native_value is None and a["value_mgdl"] == 500.0 and a["limit"] == "high" and a["limit_text"] == "HI"
        assert a["zone"] == "very_high" and a["zone_color"] == C.ZONE_COLORS["very_high"]
        v, lim = c._source_reading("sensor.p", state("LO"))
        c._process_reading(v, now + 60, "sensor.p", "P", True, lim)
        a = e.extra_state_attributes
        assert (a["limit"], a["limit_text"], a["zone"]) == ("low", "LO", "very_low")
        c._process_reading(120.0, now + 120, "sensor.p", "P", True, None)
        a = e.extra_state_attributes
        assert a["limit"] is None and a["limit_text"] is None

    def test_settings_and_learned_limits(self, tmp_path):
        c = source_coordinator(tmp_path, {"sensor_upper_limit": "400"})
        assert c._source_reading("sensor.p", state("401")) == (400.0, "high")
        assert c._source_reading("sensor.p", state("380")) == (380.0, None)
        c2 = source_coordinator(tmp_path / "b")
        assert c2._source_reading("sensor.p", state("Below 50 mg/dL")) == (50.0, "low")
        assert c2._source_reading("sensor.p", state("45")) == (50.0, "low")  # learned lower 50
        assert c2._state_data()["learned_limits"] == {"sensor.p": {"lower": 50}}
        assert c2.configuration()["sources"]["primary"]["lower_limit"] == 50

    def test_upper_limit_set_by_hand_at_the_limit(self, tmp_path):
        # set by hand: a reading at the limit is HI, in both source units; Automatic (Dexcom 400): only above it
        c = source_coordinator(tmp_path, {"sensor_upper_limit": "400"})
        assert c._source_reading("sensor.p", state("400")) == (400.0, "high")
        assert c._source_reading("sensor.p", state("399")) == (399.0, None)
        assert c._source_reading("sensor.p", state("22.2", unit="mmol/L")) == (400.0, "high")
        assert c._source_reading("sensor.p", state("22.1", unit="mmol/L")) == (398.2, None)
        c2 = source_coordinator(tmp_path / "b")
        c2._src_domain = {"sensor.p": "dexcom"}
        assert c2._source_reading("sensor.p", state("400")) == (400.0, None)
        assert c2._source_reading("sensor.p", state("22.2", unit="mmol/L")) == (400.0, None)

    def test_lower_limit_set_by_hand_at_the_limit(self, tmp_path):
        # set by hand: a reading at the limit is LO, in both source units; Automatic (CareLink 50): only below it
        c = source_coordinator(tmp_path, {"sensor_lower_limit": "50"})
        assert c._source_reading("sensor.p", state("50")) == (50.0, "low")
        assert c._source_reading("sensor.p", state("51")) == (51.0, None)
        assert c._source_reading("sensor.p", state("49")) == (50.0, "low")
        assert c._source_reading("sensor.p", state("2.8", unit="mmol/L")) == (50.0, "low")
        assert c._source_reading("sensor.p", state("2.9", unit="mmol/L")) == (52.2, None)
        c2 = source_coordinator(tmp_path / "b")
        c2._src_domain = {"sensor.p": "carelink"}
        assert c2._source_reading("sensor.p", state("50")) == (50.0, None)
        assert c2._source_reading("sensor.p", state("2.8", unit="mmol/L")) == (50.4, None)

    def test_limits_set_by_hand_per_source(self, tmp_path):
        # each source by its own settings: the second source's limits set by hand, the primary's Automatic
        c = source_coordinator(
            tmp_path,
            {"secondary_sensor_lower_limit": "50", "secondary_sensor_upper_limit": "400"},
            secondary="sensor.s",
        )
        assert c._source_reading("sensor.s", state("50")) == (50.0, "low")
        assert c._source_reading("sensor.s", state("51")) == (51.0, None)
        assert c._source_reading("sensor.s", state("2.8", unit="mmol/L")) == (50.0, "low")
        assert c._source_reading("sensor.s", state("400")) == (400.0, "high")
        assert c._source_reading("sensor.s", state("399")) == (399.0, None)
        assert c._source_reading("sensor.p", state("50")) == (50.0, None)  # Automatic 40 / 500
        assert c._source_reading("sensor.p", state("400")) == (400.0, None)
        c2 = source_coordinator(
            tmp_path / "b", {"sensor_lower_limit": "50", "sensor_upper_limit": "400"}, secondary="sensor.s"
        )
        assert c2._source_reading("sensor.p", state("50")) == (50.0, "low")
        assert c2._source_reading("sensor.p", state("400")) == (400.0, "high")
        assert c2._source_reading("sensor.s", state("50")) == (50.0, None)
        assert c2._source_reading("sensor.s", state("400")) == (400.0, None)

    def test_dexcom_domain_and_nightscout_device(self, tmp_path, monkeypatch):
        c = source_coordinator(tmp_path, secondary="sensor.ns")
        c._src_domain = {"sensor.p": "dexcom", "sensor.ns": "nightscout"}
        assert c._source_reading("sensor.p", state("401")) == (400.0, "high")
        assert c._source_reading("sensor.p", state("39")) == (40.0, "low")
        assert c._source_reading("sensor.ns", state("30")) is None  # Nightscout error code
        assert c._source_reading("sensor.ns", state("401")) == (401.0, None)  # device unknown: a measured 401
        assert c._source_reading("sensor.ns", state("401", device="share2")) == (400.0, "high")

    def test_unitless_low_values_not_used(self, tmp_path, monkeypatch):
        c = source_coordinator(tmp_path)
        created = []
        monkeypatch.setattr(co.ir, "async_create_issue", lambda *a, **k: created.append(a[2]), raising=False)
        for _ in range(10):
            assert c._source_reading("sensor.p", state("5.6", unit=None)) is None
        assert created == ["source_unit_sensor.p"]
        assert c._source_reading("sensor.p", state("35", unit=None)) == (40.0, "low")

    def test_mmol_source(self, tmp_path):
        c = source_coordinator(tmp_path)
        assert c._source_reading("sensor.p", state("2.2", unit="mmol/L")) == (40.0, "low")
        assert c._source_reading("sensor.p", state("27.9", unit="mmol/L")) == (500.0, "high")
        assert c._source_reading("sensor.p", state("6.1", unit="mmol/L")) == (109.9, None)

    def test_configuration_contract(self, tmp_path):
        c = source_coordinator(
            tmp_path, {"sensor_lower_limit": "50", "secondary_sensor_upper_limit": "450"}, secondary="sensor.s"
        )
        now = int(time.time())
        for k in range(12):
            c._process_reading(120.0, now - 60 * (12 - k), "sensor.p", "P", True)
        cfg = c.configuration()
        p, s = cfg["sources"]["primary"], cfg["sources"]["secondary"]
        assert cfg["primary_source"] == p and cfg["secondary_source"] == s
        assert (p["lower_limit"], p["lower_limit_setting"], p["upper_limit"], p["upper_limit_setting"]) == (
            50,
            50,
            500,
            "auto",
        )
        assert (s["upper_limit"], s["upper_limit_setting"], s["lower_limit_setting"]) == (450, 450, "auto")
        assert p["reading_interval_seconds"] == 60 and p["gap_threshold_seconds"] == 900
        assert cfg["reading_interval_seconds"] == 60 and cfg["gap_threshold_seconds"] == 900
        assert "test_notifications" not in cfg and "direction_entity" not in cfg
        json.dumps(cfg)  # attributes must serialize


# ================================================================================================ storage v3
class TestStorageV3:
    def test_round_trip_limit_and_source(self, storage):
        t0 = 1_760_000_000
        storage._readings = [
            GlucoseReading(t0, 40, "low", 1),
            GlucoseReading(t0 + 60, 120, None, 2),
            GlucoseReading(t0 + 120, 400, "high", 0),
        ]
        storage.save()
        data = storage._path.read_bytes()
        assert data[4] == 3 and len(data) == 16 + 3 * 10
        assert struct.unpack("<IHHBB", data[16:26]) == (t0, 400, 0, 1, 1)  # the reserved field 0
        back = GlucoCraftStorage(str(storage._dir), "test")
        back.load()
        assert [(r.raw, r.limit, r.source) for r in back.readings] == [
            (40.0, "low", 1),
            (120.0, None, 2),
            (400.0, "high", 0),
        ]

    def test_reading_intervals_cache(self, storage):
        rng = random.Random(3)
        t = 0
        for _ in range(300):
            t += rng.choice([60, 60, 60, 300, 900, 1200])
            add_reading(storage, t, 100)
        first = list(storage.reading_intervals(300))
        assert first == gaps.local_intervals([r.timestamp for r in storage.readings], 300)
        for _ in range(40):  # appended: only the new ones computed
            t += 60
            add_reading(storage, t, 100)
        assert storage.reading_intervals(300) == gaps.local_intervals([r.timestamp for r in storage.readings], 300)
        storage.insert_reading(150, 90)  # inserted in the middle: recomputed
        storage.purge_before(400)  # purged at the front: recomputed
        assert storage.reading_intervals(300) == gaps.local_intervals([r.timestamp for r in storage.readings], 300)


# ================================================================================================ the gap rule
def readings(times, value=100.0):
    return [GlucoseReading(t, value) for t in times]


class TestGapRule:
    def test_threshold(self):
        assert gaps.gap_threshold(60) == 900 and gaps.gap_threshold(300) == 900 and gaps.gap_threshold(900) == 2700
        assert gaps.gap_threshold(None) == 900

    @pytest.mark.parametrize("step", [60, 300, 900])
    def test_regular_data_has_no_gaps_and_full_coverage(self, step):
        n = 7 * 86400 // step
        rd = readings(range(0, n * step, step))
        s = st.compute_period_stats(rd, "x", n * step, step)
        assert s.data_availability == 100.0 and s.covered_seconds == n * step

    @pytest.mark.parametrize("outage_min, gap", [(10, False), (15, False), (16, True), (60, True)])
    def test_outages_in_one_minute_data(self, outage_min, gap):
        times = list(range(0, 3600, 60)) + list(range(3600 - 60 + outage_min * 60, 7200 + outage_min * 60, 60))
        iv = gaps.local_intervals(times, 60)
        k = 59  # the reading before the outage
        delta = times[k + 1] - times[k]
        assert iv[k] == 60 and (delta > gaps.gap_threshold(iv[k])) is gap
        w = gaps.weights(times, iv)
        assert w[k] == (60.0 if gap else float(delta))

    def test_15_minute_history_then_1_minute_live(self):
        """FreeStyle Libre: 15-minute history steps are no gaps; the 1-minute stretch after them is judged by its own
        interval (a 16-minute outage there is a gap)."""
        hist = list(range(0, 12 * 3600, 900))
        live = list(range(hist[-1] + 900, hist[-1] + 900 + 3600, 60))
        outage = live[-1] + 16 * 60
        tail = list(range(outage, outage + 1800, 60))
        times = hist + live + tail
        iv = gaps.local_intervals(times, 300)
        w = gaps.weights(times, iv)
        assert all(w[k] == 900 for k in range(1, len(hist) - 1))  # history: covered
        k = len(hist) + len(live) - 1
        assert iv[k] == 60 and w[k] == 60  # the live outage: a gap
        s = st.compute_period_stats(readings(times), "x", times[-1] - times[0] + 60, 300, intervals=iv)
        assert s.covered_seconds == pytest.approx(sum(w))

    def test_mixed_sources_and_batched_delivery(self):
        """A 5-minute secondary filling a stretch of a 1-minute primary: the stretch keeps 5-minute readings covered;
        late (batched) delivery does not matter, only the measurement times."""
        times = list(range(0, 1800, 60)) + list(range(1800, 5400, 300)) + list(range(5400, 7200, 60))
        iv = gaps.local_intervals(times, 60)
        w = gaps.weights(times, iv)
        assert sum(w) == pytest.approx(7200 - 60 + iv[-1])  # no gaps anywhere
        shuffled = sorted(times, key=lambda t: (t // 600, -t))  # delivery order is irrelevant: stored sorted
        assert gaps.local_intervals(sorted(shuffled), 60) == iv

    def test_weekly_trends_use_covered_time(self):
        now = 1_800_000_000
        rd = readings(range(now - 7 * 86400, now, 900))  # 15-minute data for a week
        w = st.compute_weekly_trends(rd, num_weeks=2, detected_interval=60, now=now)
        assert w[0].data_availability == 100.0 and w[0].covered_seconds == 7 * 86400


class TestReportGapRule:
    def test_minute_grid_and_coverage_agree(self):
        times = list(range(0, 3600, 60)) + list(range(3600 + 960, 7200, 60))  # 16-minute outage (+1 interval)
        rd = [types.SimpleNamespace(timestamp=t, raw=100.0, limit=None) for t in times]
        g = R.minute_grid(rd, 60, 0, 7200, LA)
        cov = R._period(g, 0, 7200)[1].sensor_active_exact
        assert cov == 100.0 * len(g["mins"]) * 60 / 7200
        assert 3540 in g["mins"] and 3600 not in g["mins"] and 4500 not in g["mins"] and 4560 in g["mins"]

    @pytest.mark.parametrize("outage_min, events", [(10, 1), (16, 2)])
    def test_hypoglycemia_event_ends_at_a_data_gap(self, outage_min, events):
        """GlucoCraft rule: a data gap by the one gap rule ends an event; an outage that is no gap (10 minutes of
        1-minute data) keeps it."""
        first = list(range(0, 20 * 60, 60))
        second = list(range(first[-1] + outage_min * 60, first[-1] + outage_min * 60 + 20 * 60, 60))
        rd = [types.SimpleNamespace(timestamp=t, raw=60.0, limit=None) for t in first + second]
        rd.append(types.SimpleNamespace(timestamp=second[-1] + 60, raw=100.0, limit=None))
        g = R.minute_grid(rd, 60, 0, second[-1] + 3600, LA)
        assert len(R.report_events(g)) == events


# ================================================================================================ performance
def _random_store(tmp_path, seed, days=60):
    rng = random.Random(seed)
    s = GlucoCraftStorage(str(tmp_path), f"perf{seed}")
    s.load()
    now = 1_800_000_000
    t = now - days * 86400
    while t < now:
        step = rng.choice([60] * 20 + [300] * 5 + [900, 1500, 3600])
        t += step
        lim = rng.choice([None] * 50 + ["low", "high"])
        v = 40.0 if lim == "low" else 400.0 if lim == "high" else round(rng.uniform(45, 380), 1)
        add_reading(s, t, v, lim, 1)
    return s, now


def _reference_period_stats(c, now):
    """Compute the reference statistics the plain way: filter the whole store per period (list comprehension), with
    the intervals sliced the same way."""
    rd = c.storage.readings
    iv = gaps.local_intervals([r.timestamp for r in rd], c.detected_interval_seconds)
    out = {}
    for label, seconds in C.STAT_PERIODS:
        idx = [k for k, r in enumerate(rd) if r.timestamp >= now - seconds]
        out[label] = st.compute_period_stats(
            [rd[k] for k in idx], label, seconds, c.detected_interval_seconds, **AGP, intervals=[iv[k] for k in idx]
        )
    return out, iv


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_bisection_windows_identical_to_filtering(tmp_path, seed):
    s, now = _random_store(tmp_path, seed)
    c = source_coordinator(tmp_path / "c")
    c.storage = s
    c._compute_all_stats(now)
    ref, iv = _reference_period_stats(c, now)
    assert c.period_stats == ref and c.period_stats_agp == ref
    rd = s.readings
    for w in range(26):
        end = now - w * 7 * 86400
        idx = [k for k, r in enumerate(rd) if end - 7 * 86400 <= r.timestamp < end]
        exp = (
            st.compute_period_stats(
                [rd[k] for k in idx], f"week_{w}", 7 * 86400, 300, **AGP, intervals=[iv[k] for k in idx]
            )
            if len(idx) >= 2
            else None
        )
        assert c.weekly_trends[w] == (exp if exp is not None and exp.sensor_active_ok else None)
    # Change Over Time: storage windows vs filtering
    agp = ch.compute_change_sets(s, 300, now, (AGP,))[0]
    for w, end in enumerate(agp.week_ends):
        idx = [k for k, r in enumerate(rd) if end - 14 * 86400 <= r.timestamp < end]
        exp = (
            st.compute_period_stats(
                [rd[k] for k in idx], f"w14_{w}", 14 * 86400, 300, **AGP, intervals=[iv[k] for k in idx]
            )
            if len(idx) >= 2
            else None
        )
        assert agp.stats14[w] == (exp if exp is not None and exp.sensor_active_exact >= C.ENOUGH_DATA_PCT else None)


def test_limit_readings_counted(tmp_path):
    s, now = _random_store(tmp_path, 9, days=3)
    c = source_coordinator(tmp_path / "c")
    c.storage = s
    c._compute_all_stats(now)
    st24 = c.period_stats["24h"]
    win = [r for r in s.readings if r.timestamp >= now - 86400]
    assert st24.limit_readings == {
        "low": sum(r.limit == "low" for r in win),
        "high": sum(r.limit == "high" for r in win),
    }
    assert (st24.limit_seconds["low"] > 0, st24.limit_seconds["high"] > 0) == (st24.limit_low > 0, st24.limit_high > 0)
    measured = [r.raw for r in win if r.limit is None]
    assert (st24.min_value, st24.max_value) == (
        pytest.approx(min(measured), abs=0.05),
        pytest.approx(max(measured), abs=0.05),
    )
    e = se.GlucoCraftPeriodConsensus.__new__(se.GlucoCraftPeriodConsensus)
    e.coordinator, e._period = c, "24h"
    assert e.extra_state_attributes["limit_readings"] == st24.limit_readings


# ================================================================================================ retention
class TestRetention:
    def test_retention_is_fixed(self):
        assert C.DEFAULT_RETENTION_DAYS == 200 and not hasattr(C, "CONF_RETENTION_DAYS")

    @pytest.mark.parametrize(
        "now_local", [datetime(2026, 10, 22, 9, 30), datetime(2026, 3, 9, 0, 5), datetime(2026, 11, 2, 23, 59)]
    )
    def test_whole_local_days(self, tmp_path, now_local):
        c = source_coordinator(tmp_path)
        now = int(now_local.replace(tzinfo=LA).timestamp())
        cut = c.retention_cutoff(now)
        local = datetime.fromtimestamp(cut, LA)
        assert (local.hour, local.minute, local.second) == (0, 0, 0)
        assert (now_local.date() - local.date()).days == 200
        for t in (cut - 1, cut, now - 60):
            add_reading(c.storage, t, 100)
        assert c._purge_whole_days(now) == 1 and c.storage.readings[0].timestamp == cut
        assert c._purge_day == now_local.date()


# ================================================================================================ the report
NOW_LOCAL = datetime(2026, 10, 22, 9, 0, tzinfo=LA)
NOW = NOW_LOCAL.timestamp()


@pytest.fixture(scope="module")
def report_readings():
    start = int(NOW) - 205 * 86400
    out = []
    for t in range(start, int(NOW) - 3600, 300):
        v = 120 + 80 * math.sin(t / 7000.0)
        lim = None
        if (t // 300) % 997 == 0:
            v, lim = 40.0, "low"
        elif (t // 300) % 1009 == 0:
            v, lim = 400.0, "high"
        out.append(types.SimpleNamespace(timestamp=t, raw=v, limit=lim, source=0))
    return out


class TestReport:
    def test_six_months_has_no_previous_period(self, report_readings):
        out = R.build_report(report_readings, 300, LA, 180, False, [], now=NOW)
        assert out["previous"] is None
        assert R.build_report(report_readings, 300, LA, 90, False, [], now=NOW)["previous"] is not None

    def test_limit_readings_per_period(self, report_readings):
        out = R.build_report(report_readings, 300, LA, 30, False, [], now=NOW)
        p = out["period"]
        s, e = (
            int((NOW_LOCAL.replace(hour=0) - timedelta(days=30)).timestamp()),
            int(NOW_LOCAL.replace(hour=0).timestamp()),
        )
        win = [r for r in report_readings if s <= r.timestamp < e]
        assert p["limit_readings"] == {
            "low": sum(r.limit == "low" for r in win),
            "high": sum(r.limit == "high" for r in win),
        }
        assert p["limit_readings"]["low"] > 0 and p["limit_seconds"]["low"] > 0 and p["limit_seconds"]["high"] > 0
        assert all("limit_readings" in b for b in out["blocks"])

    def test_lab_without_cgm_comparison(self, report_readings):
        lab = [{"date": "2026-07-01", "a1c": 6.8}, {"date": "2026-06-30", "a1c": 6.9}, {"date": "2026-09-01", "a1c": 7}]
        out = R.build_report(report_readings, 300, LA, 14, False, lab, now=NOW, retention_days=200)
        res = {r["date"]: r for r in out["lab"]["results"]}
        # kept from 2026-04-05 (200 days before 2026-10-22); 2026-07-01's 90 days begin 2026-04-03 -> no comparison
        assert res["2026-07-01"]["no_cgm_comparison"] is True and res["2026-07-01"]["gmi_90d"] is None
        assert res["2026-07-01"]["comparison_note"] == "no CGM comparison (data older than 200 days)"
        assert res["2026-09-01"]["no_cgm_comparison"] is False and res["2026-09-01"]["gmi_90d"] is not None
        assert out["lab"]["average_difference"] is not None and out["lab"]["retention_days"] == 200

    def test_pdf_limit_note_and_no_comparison_page(self, report_readings):
        pytest.importorskip("reportlab")
        pypdf = pytest.importorskip("pypdf")
        import io

        from glucocraft import report_pdf as P
        from glucocraft.regional import Regional

        rg = Regional()
        lab = [{"date": "2026-06-01", "a1c": 6.8}]
        data = R.build_report(report_readings, 300, LA, 180, False, lab, now=NOW, regional=rg)
        data.update(
            {
                "label": "6 Months",
                "sources": ["Test"],
                "created": "x",
                "unit": "mg/dL",
                **rg.as_dict(),
                "integration_version": "0",
            }
        )
        pdf, _pages = P.render(data, True, False)
        text = " ".join((p.extract_text() or "") for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
        text = " ".join(text.split())
        # the 6-month report has no comparison: the heading stays, with the note that says why (as the report page)
        assert (
            "Comparison with the Previous Period Not part of the 6-month report: a previous period of the same " in text
        )
        assert "require about 360 days of readings, and readings are kept for 200 days" in text
        assert "at the sensor's limit" in text and "no CGM comparison (data older than 200 days)" in text
        assert P._limit_note({"limit_readings": {"low": 0, "high": 0}}) == ""
        note = P._limit_note({"limit_readings": {"low": 1, "high": 1}, "limit_seconds": {"low": 300, "high": 7500}})
        assert note.startswith(
            "2 readings at the sensor's limit (LO 1 (5 minutes); HI 1 (2 hours 5 minutes)), left out of the values"
        )


# ================================================================================================ CSV export
class TestExport:
    def test_files_columns_and_keep(self, tmp_path):
        rd = [
            GlucoseReading(1_761_148_800, 120.4, None, 1),
            GlucoseReading(1_761_148_860, 40, "low", 2),
            GlucoseReading(1_761_148_920, 500, "high", 0),
        ]
        res = ex.write_export(
            str(tmp_path),
            rd,
            MMOL,
            LA,
            [{"date": "2026-09-01", "a1c": 7.1, "a1c_mmol_mol": 54}],
            now=datetime(2026, 10, 22, 9, 0, 1, tzinfo=LA),
        )
        assert res["files"] == ["glucocraft-readings_2026-10-22_090001.csv", "glucocraft-lab-a1c_2026-10-22_090001.csv"]
        rows = list(csv.reader((tmp_path / res["files"][0]).read_text(encoding="utf-8").splitlines()))
        assert tuple(rows[0]) == ex.READING_COLUMNS
        assert rows[1] == ["2025-10-22T09:00:00-07:00", "2025-10-22T16:00:00Z", "120.4", "6.7", "mmol/L", "primary", ""]
        assert rows[2][5:] == ["secondary", "low"] and rows[3][5:] == ["", "high"] and rows[3][2] == ""
        lab = list(csv.reader((tmp_path / res["files"][1]).read_text(encoding="utf-8").splitlines()))
        assert lab == [["date", "a1c_percent", "a1c_mmol_mol", "entered_unit"], ["2026-09-01", "7.1", "54", "mmol_mol"]]
        for k in range(14):
            ex.write_export(str(tmp_path), rd, "mg/dL", LA, None, now=datetime(2026, 10, 23, 9, 0, k, tzinfo=LA))
        names = os.listdir(tmp_path)
        assert sum(n.startswith(ex.READINGS_PREFIX) for n in names) == C.EXPORT_KEEP
        assert sum(n.startswith(ex.LAB_PREFIX) for n in names) == 1
        newest = max(n for n in names if n.startswith(ex.READINGS_PREFIX))
        mg = list(csv.reader((tmp_path / newest).read_text(encoding="utf-8").splitlines()))
        assert mg[1][3] == "120" and mg[1][4] == "mg/dL"

    def test_an_old_export_that_cannot_be_removed_is_logged(self, tmp_path, monkeypatch, caplog):
        """The export succeeds, the file stays, and the failed removal is logged (not swallowed)."""
        import logging

        for k in range(C.EXPORT_KEEP + 1):
            (tmp_path / f"{ex.READINGS_PREFIX}2026-10-01_0000{k:02d}.csv").write_text("x", encoding="utf-8")

        def refuse(path):
            raise PermissionError(13, "in use", path)

        monkeypatch.setattr(ex.os, "remove", refuse)
        with caplog.at_level(logging.DEBUG, logger=ex.__name__):
            ex._prune(str(tmp_path), ex.READINGS_PREFIX)
        assert len(os.listdir(tmp_path)) == C.EXPORT_KEEP + 1
        assert [r.levelno for r in caplog.records] == [logging.DEBUG]
        assert f"{ex.READINGS_PREFIX}2026-10-01_000000.csv" in caplog.records[0].getMessage()


# ================================================================================================ the HTTP API
class TestDataView:
    def test_readings_since_by_bisection_equal_the_filter(self, tmp_path, monkeypatch):
        """The data view's readings are a bisected slice of the store (storage.window_indices), the same
        readings as a filter over the whole store, readings newer than the clock included as before."""
        from glucocraft import http_api as H

        c = real_coordinator(tmp_path)
        now = int(time.time())
        times = sorted({now - random.Random(5).randrange(0, 10 * 86400) for _ in range(2000)} | {now + 300})
        for ts in times:
            c.storage.insert_reading(ts, 100.0, None, C.SOURCE_PRIMARY)
        calls = []
        real = c.storage.window_indices
        monkeypatch.setattr(c.storage, "window_indices", lambda *a: calls.append(a) or real(*a))
        for hours in (0, 6, 24, 24 * 7, 24 * 30):
            since = now - hours * 3600
            got = H._readings_since(c, since)
            assert [r.timestamp for r in got] == [ts for ts in times if ts >= since] and calls[-1] == (since,)
        assert H._readings_since(c, now + 301) == []

    def test_views_use_the_http_app_key_and_aiohttp_sets_the_length(self):
        """The views read Home Assistant from KEY_HASS (not the backwards-compatibility "hass" key) and
        leave Content-Length to aiohttp, as the panels' webhook does."""
        src = (Path(co.__file__).parent / "http_api.py").read_text(encoding="utf-8")
        assert "from homeassistant.helpers.http import KEY_HASS" in src
        assert 'request.app["hass"]' not in src and src.count("request.app[KEY_HASS]") == 1  # _coordinator_or_404
        assert src.count("_coordinator_or_404(request)") == 5  # every view
        assert "Content-Length" not in src.replace("aiohttp sets Content-Length", "")


# ================================================================================================ Repairs notices
class TestNotices:
    @pytest.fixture
    def issues(self, monkeypatch):
        live: dict[str, dict] = {}
        monkeypatch.setattr(co.ir, "async_create_issue", lambda h, d, i, **k: live.__setitem__(i, k), raising=False)
        monkeypatch.setattr(co.ir, "async_delete_issue", lambda h, d, i: live.pop(i, None), raising=False)
        return live

    def test_slow_source(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        now = int(time.time())
        c._src_times["sensor.p"] = [now - 900 * k for k in range(8, -1, -1)]
        c._check_source_notices()
        assert issues["slow_source_sensor.p"]["translation_placeholders"] == {"entity": "sensor.p", "minutes": "15"}
        c._src_times["sensor.p"] = [now - 300 * k for k in range(8, -1, -1)]
        c._check_source_notices()
        assert "slow_source_sensor.p" not in issues

    def test_sensor_cap(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        t = 0
        for _ in range(3):  # three runs of 4 readings flat at 400
            for v in (300, 380, 400, 400, 400, 400, 350, 200):
                t += 300
                add_reading(c.storage, t, v, None, 1)
        c._cap_findings = c._scan_sensor_cap()
        assert c._cap_findings[C.SOURCE_PRIMARY] == {"upper": 400.0, "lower": None}
        c._check_source_notices()
        assert issues["sensor_cap_sensor.p"]["translation_placeholders"]["value_mgdl"] == "400"
        c._settings["sensor_upper_limit"] = "400"  # the user set the limit: the notice closes
        c._check_source_notices()
        assert "sensor_cap_sensor.p" not in issues
        c._settings["sensor_upper_limit"] = "auto"
        add_reading(c.storage, t + 300, 420, None, 1)  # a reading above: no cap
        c._cap_findings = c._scan_sensor_cap()
        assert c._cap_findings[C.SOURCE_PRIMARY]["upper"] is None

    @pytest.mark.parametrize(
        "mmol,cap,stored", [(22.2, 400.0, 400.0), (25.0, 450.0, 450.4)], ids=["22.2 mmol/L", "25.0 mmol/L"]
    )
    def test_sensor_cap_from_a_source_in_mmol(self, tmp_path, issues, mmol, cap, stored):
        """A source in mmol/L that stops at 22.2 or 25.0 mmol/L is found at its mg/dL cap, although 25.0 mmol/L is
        stored as 450.4 mg/dL: the cap is compared at the precision of the limits (limits.stored_limit_values)."""
        c = source_coordinator(tmp_path)
        t = 0
        for _ in range(3):  # three runs of 4 readings flat at the limit's mmol/L value
            for v in (16.7, 21.1, mmol, mmol, mmol, mmol, 19.4, 11.1):
                t += 300
                k = L.classify(v, UNIT_MMOLL)  # Automatic limits (40 / 500): a measured reading, stored as it is
                assert k.kind == "reading"
                add_reading(c.storage, t, k.value, k.limit, 1)
        assert stored in {r.raw for r in c.storage.readings}
        c._cap_findings = c._scan_sensor_cap()
        assert c._cap_findings[C.SOURCE_PRIMARY]["upper"] == cap
        c._check_source_notices()
        ph = issues["sensor_cap_sensor.p"]["translation_placeholders"]
        assert ph["value_mgdl"] == str(int(cap)) and ph["value_mmol"] == f"{mmol:.1f}"
        k = L.classify(round(mmol + 0.1, 1), UNIT_MMOLL)  # one reading above the limit's mmol/L value: no cap
        add_reading(c.storage, t + 300, k.value, k.limit, 1)
        assert c._scan_sensor_cap()[C.SOURCE_PRIMARY] == {"upper": None, "lower": None}

    def test_no_sensor_cap_at_27_8_mmol(self, tmp_path):
        """27.8 mmol/L (500.8 mg/dL) is above the Automatic upper limit of 500 mg/dL, so it is stored as HI, a limit
        reading, which the cap scan never counts; 500 mg/dL is not a cap value."""
        c = source_coordinator(tmp_path)
        t = 0
        for _ in range(3):
            for v in (21.1, 27.8, 27.8, 27.8, 27.8, 11.1):
                t += 300
                k = L.classify(v, UNIT_MMOLL)
                add_reading(c.storage, t, k.value, k.limit, 1)
        assert L.classify(27.8, UNIT_MMOLL) == L.Classified(L.LIMIT_HIGH, 500.0)
        assert c._scan_sensor_cap()[C.SOURCE_PRIMARY] == {"upper": None, "lower": None}

    def test_two_runs_are_not_enough(self, tmp_path):
        c = source_coordinator(tmp_path)
        t = 0
        for _ in range(2):
            for v in (450, 450, 450, 300):
                t += 300
                add_reading(c.storage, t, v, None, 1)
        assert c._scan_sensor_cap()[C.SOURCE_PRIMARY] == {"upper": None, "lower": None}

    # the lower end: a source that sends its lower limit (50 mg/dL) instead of LO; the same run rules as above
    def test_sensor_cap_at_the_lower_end(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        t = 0
        for _ in range(3):  # three runs of 4 readings flat at 50
            for v in (80, 60, 50, 50, 50, 50, 70, 120):
                t += 300
                add_reading(c.storage, t, v, None, 1)
        c._cap_findings = c._scan_sensor_cap()
        assert c._cap_findings[C.SOURCE_PRIMARY] == {"upper": None, "lower": 50.0}
        c._check_source_notices()
        ph = issues["sensor_floor_sensor.p"]["translation_placeholders"]
        assert ph == {"entity": "sensor.p", "value_mgdl": "50", "value_mmol": "2.8"}
        assert issues["sensor_floor_sensor.p"]["translation_key"] == "sensor_floor"
        assert "sensor_cap_sensor.p" not in issues  # the upper end has its own notice
        c._settings["sensor_lower_limit"] = "50"  # the user set the limit: the notice closes
        c._check_source_notices()
        assert "sensor_floor_sensor.p" not in issues
        c._settings["sensor_lower_limit"] = "auto"
        add_reading(c.storage, t + 300, 45, None, 1)  # a reading below: no cap
        assert c._scan_sensor_cap()[C.SOURCE_PRIMARY]["lower"] is None

    def test_a_lo_reading_rules_out_the_lower_cap(self, tmp_path):
        """A LO reading (stored at the lower limit 40, marked low) shows that the source does send LO."""
        c = source_coordinator(tmp_path)
        t = 0
        for _ in range(3):
            for v in (50, 50, 50, 90):
                t += 300
                add_reading(c.storage, t, v, None, 1)
        assert c._scan_sensor_cap()[C.SOURCE_PRIMARY]["lower"] == 50.0
        add_reading(c.storage, t + 300, 40, "low", 1)
        assert c._scan_sensor_cap()[C.SOURCE_PRIMARY]["lower"] is None

    def test_a_lo_at_a_lower_limit_set_by_hand_keeps_the_lower_cap(self, tmp_path, issues):
        """With the lower limit set by hand to 50, the source's readings at 50 arrive as LO stored at 50: they never lie
        below the cap, so the cap is still found, and the notice stays closed while the setting matches."""
        c = source_coordinator(tmp_path)
        t = 0
        for _ in range(3):
            for v in (80, 50, 50, 50, 50, 90):
                t += 300
                add_reading(c.storage, t, v, None, 1)
        c._settings["sensor_lower_limit"] = "50"
        for v in ("50", "50", "50", "70"):
            t += 300
            value, limit = c._source_reading("sensor.p", state(v))
            add_reading(c.storage, t, value, limit, 1)
        assert [r.limit for r in c.storage.readings[-4:]] == ["low", "low", "low", None]
        c._cap_findings = c._scan_sensor_cap()
        assert c._cap_findings[C.SOURCE_PRIMARY]["lower"] == 50.0
        c._check_source_notices()
        assert "sensor_floor_sensor.p" not in issues

    def test_lower_cap_from_a_source_in_mmol(self, tmp_path, issues):
        """2.8 mmol/L is stored as 50.4 mg/dL and found at the 50 mg/dL cap (limits.stored_limit_values)."""
        c = source_coordinator(tmp_path)
        t = 0
        for _ in range(3):
            for v in (4.4, 3.3, 2.8, 2.8, 2.8, 5.0):
                t += 300
                k = L.classify(v, UNIT_MMOLL)
                assert k.kind == "reading"
                add_reading(c.storage, t, k.value, k.limit, 1)
        assert 50.4 in {r.raw for r in c.storage.readings}
        c._cap_findings = c._scan_sensor_cap()
        assert c._cap_findings[C.SOURCE_PRIMARY]["lower"] == 50.0
        c._check_source_notices()
        assert issues["sensor_floor_sensor.p"]["translation_placeholders"]["value_mmol"] == "2.8"

    def test_two_runs_are_not_enough_at_the_lower_end(self, tmp_path):
        c = source_coordinator(tmp_path)
        t = 0
        for _ in range(2):
            for v in (50, 50, 50, 50, 100):
                t += 300
                add_reading(c.storage, t, v, None, 1)
        assert c._scan_sensor_cap()[C.SOURCE_PRIMARY] == {"upper": None, "lower": None}

    def test_both_ends_at_once(self, tmp_path, issues):
        c = source_coordinator(tmp_path)
        t = 0
        for _ in range(3):
            for v in (50, 50, 50, 120, 400, 400, 400, 120):
                t += 300
                add_reading(c.storage, t, v, None, 1)
        c._cap_findings = c._scan_sensor_cap()
        assert c._cap_findings[C.SOURCE_PRIMARY] == {"upper": 400.0, "lower": 50.0}
        c._check_source_notices()
        assert {"sensor_cap_sensor.p", "sensor_floor_sensor.p"} <= set(issues)

    def test_no_source_notice_prefix_begins_another(self):
        prefixes = co.SOURCE_ISSUE_PREFIXES
        assert co.SENSOR_FLOOR_ISSUE_PREFIX in prefixes
        assert not [(a, b) for a in prefixes for b in prefixes if a != b and b.startswith(a)]


# ================================================================================================ settings and texts
STRINGS = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))


def test_strings_and_translation_identical():
    assert (PKG / "strings.json").read_text(encoding="utf-8") == (PKG / "translations" / "en.json").read_text(
        encoding="utf-8"
    )


def test_cooldown_settings_removed_everywhere():
    from glucocraft import config_flow as cf

    src = (PKG / "config_flow.py").read_text(encoding="utf-8")
    assert "COOLDOWN" not in src
    for flow in ("config", "options"):
        for step in STRINGS[flow]["step"].values():
            keys = [*step.get("data", {})] + [k for sec in step.get("sections", {}).values() for k in sec["data"]]
            assert not any("cooldown" in k for k in keys)
    assert set(cf.LIMIT_SETTINGS) == {
        C.CONF_SENSOR_LOWER_LIMIT,
        C.CONF_SENSOR_UPPER_LIMIT,
        C.CONF_SECONDARY_SENSOR_LOWER_LIMIT,
        C.CONF_SECONDARY_SENSOR_UPPER_LIMIT,
    }


@pytest.mark.parametrize("step", [("options", "sources"), ("config", "source")])
def test_limit_settings_have_labels_and_notes(step):
    s = STRINGS[step[0]]["step"][step[1]]
    for k in ("sensor_lower_limit", "sensor_upper_limit"):
        assert k in s["data"] and k in s["data_description"]
    second = s["sections"]["second_source"]  # the collapsed section Second glucose source
    for k in ("secondary_sensor_lower_limit", "secondary_sensor_upper_limit"):
        assert k in second["data"] and k in second["data_description"]
    # the responsibility note and the interval paragraph once per screen: in the description, not per field
    blob = json.dumps(s)
    assert blob.count("user's responsibility to know their CGM system") == 1
    assert "user's responsibility to know their CGM system" in s["description"]
    assert blob.count("every 5 minutes or more often") == 1 and "every 5 minutes or more often" in s["description"]


def test_limit_selector_options():
    assert set(STRINGS["selector"]["sensor_lower_limit"]["options"]) == set(C.SENSOR_LOWER_LIMIT_OPTIONS)
    assert set(STRINGS["selector"]["sensor_upper_limit"]["options"]) == set(C.SENSOR_UPPER_LIMIT_OPTIONS)
    assert all(v in L.LOWER_CHOICES for v in map(int, C.SENSOR_LOWER_LIMIT_OPTIONS[1:]))
    assert all(v in L.UPPER_CHOICES for v in map(int, C.SENSOR_UPPER_LIMIT_OPTIONS[1:]))


def test_issue_texts_and_service():
    iss = STRINGS["issues"]
    slow = json.dumps(iss["slow_source"])
    assert "{entity}" in slow and "{minutes}" in slow and "a reading every 5 minutes or more often" in slow
    for key in ("sensor_cap", "sensor_floor"):
        assert "{value_mgdl}" in iss[key]["description"] and "{value_mmol}" in iss[key]["description"]
    assert "*Sensor lower limit*" in iss["sensor_floor"]["description"] and "LO" in iss["sensor_floor"]["description"]
    # the two notices state the rule of a limit set by hand in parallel: a reading at the limit is LO / HI
    assert (
        "readings at or below it are shown as LO, left out of the calculated values"
        in iss["sensor_floor"]["description"]
    )
    assert (
        "readings at or above it are shown as HI, left out of the calculated values" in iss["sensor_cap"]["description"]
    )
    assert "measured value" not in iss["sensor_floor"]["description"]
    assert "export_csv" in STRINGS["services"]
    assert "export_csv:" in (PKG / "services.yaml").read_text(encoding="utf-8")
    assert "export_csv" in (PKG / "__init__.py").read_text(encoding="utf-8")
