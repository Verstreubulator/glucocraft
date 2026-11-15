# License: AGPL-3.0-only (LICENSE).
"""Units and rounding: units.py and the range entry of the setup screens.

Rules under test: mg/dL stored and calculated; 1 mmol/L = 18.016 mg/dL; mg/dL shown whole, mmol/L with one decimal;
halves rounded away from zero, once, from the stored value; the consensus thresholds 54 / 70 / 140 / 180 / 250 mg/dL =
3.0 / 3.9 / 7.8 / 10.0 / 13.9 mmol/L are used as published pairs in both directions.
"""

from __future__ import annotations

import pytest
from glucocraft import config_flow as cf
from glucocraft import const, units
from glucocraft.units import (
    MGDL_PER_MMOLL,
    UNIT_MGDL,
    UNIT_MMOLL,
    fmt,
    from_mgdl,
    gmi_mmol_mol,
    round_half_up,
    to_mgdl,
    to_mgdl_setting,
    unit_from_attribute,
)

CONSENSUS_PAIRS = [(54, 3.0), (70, 3.9), (140, 7.8), (180, 10.0), (250, 13.9)]


# ------------------------------------------------------------------------------------------------ round_half_up
class TestRoundHalfUp:
    @pytest.mark.parametrize(
        "value, decimals, expected",
        [
            (30.5, 0, 31),
            (-30.5, 0, -31),  # round() gives 30 / -30 (halves to even)
            (2.5, 0, 3),
            (3.5, 0, 4),
            (0.5, 0, 1),
            (-0.5, 0, -1),
            (1234.5, 0, 1235),
            (0.25, 1, 0.3),
            (-0.25, 1, -0.3),
            (0.05, 1, 0.1),
            (-0.05, 1, -0.1),
            (1.45, 1, 1.5),
            (2.675, 2, 2.68),  # binary floats just below the half
            (18.75, 1, 18.8),
            (337.8 / MGDL_PER_MMOLL, 1, 18.8),  # 18.75 mmol/L reached by division (float error at the half)
            (0.0, 0, 0),
            (0.0, 1, 0.0),
            (29.4999, 0, 29),
            (29.5001, 0, 30),
            (120.04, 1, 120.0),
            (120.06, 1, 120.1),
        ],
    )
    def test_values(self, value, decimals, expected):
        assert round_half_up(value, decimals) == pytest.approx(expected, abs=1e-12)

    def test_differs_from_builtin_round_at_halves(self):
        assert round(2.5) == 2 and round_half_up(2.5) == 3
        assert round(0.125, 2) == 0.12 and round_half_up(0.125, 2) == 0.13

    def test_sign_is_symmetric(self):
        for k in range(-500, 501):
            x = k / 4
            assert round_half_up(-x, 1) == -round_half_up(x, 1)
            assert round_half_up(-x) == -round_half_up(x)

    def test_returns_float(self):
        assert isinstance(round_half_up(3), float)


# ------------------------------------------------------------------------------------------------ unit detection
@pytest.mark.parametrize(
    "attribute, expected",
    [
        ("mg/dL", UNIT_MGDL),
        ("mg/dl", UNIT_MGDL),
        ("MG/DL", UNIT_MGDL),
        (" mg / dL ", UNIT_MGDL),
        ("mmol/L", UNIT_MMOLL),
        ("mmol/l", UNIT_MMOLL),
        ("MMOL/L", UNIT_MMOLL),
        (" mmol / L", UNIT_MMOLL),
        ("", None),
        (None, None),
        ("%", None),
        ("units", None),
    ],
)
def test_unit_from_attribute(attribute, expected):
    assert unit_from_attribute(attribute) == expected


def test_is_mmol():
    assert units.is_mmol(UNIT_MMOLL)
    assert not units.is_mmol(UNIT_MGDL) and not units.is_mmol(None) and not units.is_mmol("mmol/l")


# ------------------------------------------------------------------------------------------------ to_mgdl (readings)
class TestToMgdl:
    def test_factor(self):
        assert MGDL_PER_MMOLL == 18.016  # Home Assistant's own factor (180.16 / 10)

    @pytest.mark.parametrize("mgdl, mmol", CONSENSUS_PAIRS)
    def test_consensus_reading_snaps_to_published_mgdl(self, mgdl, mmol):
        # 10.0 x 18.016 = 180.16 would count as high; the published pair keeps it in range
        assert to_mgdl(mmol, UNIT_MMOLL) == mgdl
        assert isinstance(to_mgdl(mmol, UNIT_MMOLL), float)

    @pytest.mark.parametrize(
        "mmol, expected",
        [
            (5.5, 99.1),
            (8.9, 160.3),
            (4.0, 72.1),
            (2.2, 39.6),
            (33.3, 599.9),
            (1.1, 19.8),
            (10.05, 181.1),  # not an exact one-decimal value: no snap (10.05 x 18.016 = 181.06)
            (9.95, 179.3),  # not an exact one-decimal value: no snap
            (3.95, 71.2),
        ],
    )
    def test_other_values_convert_to_one_decimal(self, mmol, expected):
        assert to_mgdl(mmol, UNIT_MMOLL) == pytest.approx(expected, abs=1e-9)

    @pytest.mark.parametrize(
        "value, expected",
        [(120.25, 120.3), (120.24, 120.2), (120.35, 120.4), (70, 70.0), (69.95, 70.0), (180.05, 180.1)],
    )
    def test_mgdl_reading_kept_to_one_decimal_half_up(self, value, expected):
        assert to_mgdl(value, UNIT_MGDL) == pytest.approx(expected, abs=1e-9)
        assert to_mgdl(value, None) == pytest.approx(expected, abs=1e-9)  # no unit = mg/dL

    def test_string_and_int_inputs(self):
        assert to_mgdl(5, UNIT_MMOLL) == pytest.approx(90.1)
        assert to_mgdl(100, UNIT_MGDL) == 100.0


# ------------------------------------------------------------------------------------------------ from_mgdl (display)
class TestFromMgdl:
    @pytest.mark.parametrize("mgdl, mmol", CONSENSUS_PAIRS)
    def test_consensus_threshold_shows_published_mmol(self, mgdl, mmol):
        assert from_mgdl(mgdl, UNIT_MMOLL) == mmol
        assert from_mgdl(float(mgdl), UNIT_MMOLL) == mmol

    def test_no_snap_with_explicit_decimals(self):
        assert from_mgdl(70, UNIT_MMOLL, 2) == 3.89  # rates of change pass decimals=2
        assert from_mgdl(180, UNIT_MMOLL, 2) == 9.99

    def test_mgdl_stays_mgdl(self):
        assert from_mgdl(120.25, UNIT_MGDL) == 120.3
        assert from_mgdl(120.25, UNIT_MGDL, 0) == 120

    def test_none(self):
        assert from_mgdl(None, UNIT_MMOLL) is None and from_mgdl(None, UNIT_MGDL) is None

    @pytest.mark.parametrize(
        "mgdl, mmol",
        [(100, 5.6), (160.3, 8.9), (99.1, 5.5), (337.8, 18.8), (181, 10.0), (179, 9.9), (40, 2.2), (400, 22.2)],
    )
    def test_values(self, mgdl, mmol):
        assert from_mgdl(mgdl, UNIT_MMOLL) == mmol

    def test_round_trip_every_one_decimal_mmol_value(self):
        """Any reading entered in mmol/L is shown back exactly as entered (1.1 ... 33.3 mmol/L)."""
        for i in range(11, 334):
            v = i / 10
            assert from_mgdl(to_mgdl(v, UNIT_MMOLL), UNIT_MMOLL) == v, v

    def test_round_trip_every_whole_mgdl_value(self):
        """mg/dL -> mmol/L -> mg/dL moves a value by at most half a mmol/L step (0.9 mg/dL), plus the published
        pair's offset where the mmol/L value is a consensus threshold (7.8 mmol/L = 140.5 mg/dL, published 140)."""
        for mg in range(20, 501):
            mmol = from_mgdl(mg, UNIT_MMOLL)
            back = to_mgdl(mmol, UNIT_MMOLL)
            snap = (
                abs(mmol * MGDL_PER_MMOLL - units.MGDL_FROM_CONSENSUS[mmol])
                if mmol in units.MGDL_FROM_CONSENSUS
                else 0.0
            )
            assert abs(back - mg) <= 0.9 + snap + 1e-9, (mg, back)


# ------------------------------------------------------------------------------------------------ fmt (text)
class TestFmt:
    @pytest.mark.parametrize(
        "value, unit, text",
        [
            (70.45, UNIT_MGDL, "70"),  # rounded once: not "71" via 70.5
            (70.5, UNIT_MGDL, "71"),
            (69.5, UNIT_MGDL, "70"),
            (70.49, UNIT_MGDL, "70"),
            (62.0, UNIT_MGDL, "62"),  # never "62.0"
            (180, UNIT_MMOLL, "10.0"),
            (70, UNIT_MMOLL, "3.9"),
            (54, UNIT_MMOLL, "3.0"),
            (250, UNIT_MMOLL, "13.9"),
            (160.3, UNIT_MMOLL, "8.9"),
            (300, UNIT_MMOLL, "16.7"),
            (62, UNIT_MMOLL, "3.4"),
            (None, UNIT_MGDL, "---"),
            (None, UNIT_MMOLL, "---"),
        ],
    )
    def test_text(self, value, unit, text):
        assert fmt(value, unit) == text

    def test_with_unit(self):
        assert fmt(120.5, UNIT_MGDL, True) == "121 mg/dL"
        assert fmt(180, UNIT_MMOLL, True) == "10.0 mmol/L"
        assert fmt(120, None, True) == "120 mg/dL"

    def test_mmol_always_one_decimal(self):
        for mg in range(20, 501):
            text = fmt(mg, UNIT_MMOLL)
            assert "." in text and len(text.split(".")[1]) == 1, text

    def test_mgdl_never_has_a_decimal(self):
        for tenth in range(200, 5001, 7):
            assert "." not in fmt(tenth / 10, UNIT_MGDL)


# ------------------------------------------------------------------------------------------------ range entry
class TestToMgdlSetting:
    def test_limit_equals_reading_in_mmol(self):
        """A limit entered as 8.9 mmol/L is stored as the same mg/dL value a reading of 8.9 mmol/L is."""
        for i in range(17, 223):  # 1.7 ... 22.2 mmol/L
            v = i / 10
            limit, reading = to_mgdl_setting(v, UNIT_MMOLL), to_mgdl(v, UNIT_MMOLL)
            assert abs(limit - reading) < 1e-9, (v, limit, reading)
            assert from_mgdl(limit, UNIT_MMOLL) == v, (v, limit)

    @pytest.mark.parametrize("mgdl, mmol", CONSENSUS_PAIRS)
    def test_consensus_values_snap_and_are_whole(self, mgdl, mmol):
        out = to_mgdl_setting(mmol, UNIT_MMOLL)
        assert out == mgdl and isinstance(out, int)

    def test_one_decimal_from_mmol(self):
        assert to_mgdl_setting(8.9, UNIT_MMOLL) == 160.3
        assert to_mgdl_setting(5.6, UNIT_MMOLL) == 100.9

    @pytest.mark.parametrize("value, expected", [(100, 100), (100.5, 101), (100.4, 100), (99.5, 100), (180, 180)])
    def test_whole_from_mgdl(self, value, expected):
        out = to_mgdl_setting(value, UNIT_MGDL)
        assert out == expected and isinstance(out, int)

    def test_clamped_to_span(self):
        assert to_mgdl_setting(16.7, UNIT_MMOLL, 120, 300) == 300  # 300.9 above the maximum
        assert isinstance(to_mgdl_setting(16.7, UNIT_MMOLL, 120, 300), int)
        assert to_mgdl_setting(1.0, UNIT_MMOLL, 30, 70) == 30
        assert to_mgdl_setting(500, UNIT_MGDL, 180, 400) == 400
        assert to_mgdl_setting(250, UNIT_MGDL, 180, 400) == 250

    def test_every_box_limit_converts_inside_its_span(self):
        for k, (lo, hi) in cf.RANGE_LIMITS.items():
            top = round_half_up(hi / MGDL_PER_MMOLL, 1)
            bottom = round_half_up(lo / MGDL_PER_MMOLL, 1)
            assert lo <= to_mgdl_setting(top, UNIT_MMOLL, lo, hi) <= hi, k
            assert lo <= to_mgdl_setting(bottom, UNIT_MMOLL, lo, hi) <= hi, k


class TestSetupRanges:
    def test_unchanged_fields_keep_their_values(self):
        """Saving the form unchanged in mmol/L must not move the stored limits (100 must not come back as 101)."""
        stored = {const.CONF_RANGE_TIGHT_HIGH: 100, const.CONF_RANGE_HIGH: 160.3}
        shown = {k: cf._range_shown(stored, k, UNIT_MMOLL) for k in cf.RANGE_ORDER}
        assert shown[const.CONF_RANGE_TIGHT_HIGH] == 5.6 and shown[const.CONF_RANGE_HIGH] == 8.9
        out = cf._ranges_to_mgdl(dict(shown), UNIT_MMOLL, stored)
        assert out[const.CONF_RANGE_TIGHT_HIGH] == 100 and out[const.CONF_RANGE_HIGH] == 160.3
        assert out[const.CONF_RANGE_LOW] == 70 and out[const.CONF_RANGE_CRITICAL_HIGH] == 250

    def test_changed_fields_convert_and_clamp(self):
        stored = {const.CONF_RANGE_TIGHT_HIGH: 100, const.CONF_RANGE_HIGH: 160.3}
        shown = {k: cf._range_shown(stored, k, UNIT_MMOLL) for k in cf.RANGE_ORDER}
        out = cf._ranges_to_mgdl(
            {**shown, const.CONF_RANGE_HIGH: 16.7, const.CONF_RANGE_TIGHT_HIGH: 5.7}, UNIT_MMOLL, stored
        )
        assert out[const.CONF_RANGE_HIGH] == 300 and out[const.CONF_RANGE_TIGHT_HIGH] == 102.7

    def test_mgdl_form(self):
        stored = {const.CONF_RANGE_HIGH: 160.3}
        shown = {k: cf._range_shown(stored, k, UNIT_MGDL) for k in cf.RANGE_ORDER}
        assert shown[const.CONF_RANGE_HIGH] == 160  # a one-decimal limit shown whole ...
        assert cf._ranges_to_mgdl(shown, UNIT_MGDL, stored)[const.CONF_RANGE_HIGH] == 160.3  # ... kept when unchanged
        assert (
            cf._ranges_to_mgdl({**shown, const.CONF_RANGE_HIGH: 165}, UNIT_MGDL, stored)[const.CONF_RANGE_HIGH] == 165
        )

    @pytest.mark.parametrize("unit", [UNIT_MGDL, UNIT_MMOLL])
    def test_first_setup_gives_exact_consensus_defaults(self, unit):
        shown = {k: cf._range_shown(None, k, unit) for k in cf.RANGE_ORDER}
        assert cf._ranges_to_mgdl(shown, unit, None) == {
            const.CONF_RANGE_CRITICAL_LOW: 54,
            const.CONF_RANGE_LOW: 70,
            const.CONF_RANGE_TIGHT_LOW: 70,
            const.CONF_RANGE_TIGHT_HIGH: 140,
            const.CONF_RANGE_HIGH: 180,
            const.CONF_RANGE_CRITICAL_HIGH: 250,
        }

    @pytest.mark.parametrize(
        "change, ordered",
        [
            ({}, True),  # consensus defaults
            ({const.CONF_RANGE_HIGH: 260}, False),  # high above very high
            ({const.CONF_RANGE_LOW: 75}, False),  # low above tight low
            ({const.CONF_RANGE_LOW: 70, const.CONF_RANGE_TIGHT_LOW: 70}, True),  # low = tight low is allowed
            ({const.CONF_RANGE_TIGHT_HIGH: 180}, True),  # tight high = high is allowed
            ({const.CONF_RANGE_TIGHT_LOW: 140}, False),  # tight low = tight high
            ({const.CONF_RANGE_CRITICAL_LOW: 70}, False),  # very low = low
            ({const.CONF_RANGE_CRITICAL_HIGH: 180}, False),  # very high = high
            ({const.CONF_RANGE_CRITICAL_LOW: 69.9}, True),
        ],
    )
    def test_range_order(self, change, ordered):
        assert (not cf._range_errors(change, {})) is ordered


# ------------------------------------------------------------------------------------------------ GMI and const
class TestGmiMmolMol:
    def test_none(self):
        assert gmi_mmol_mol(None) is None

    @pytest.mark.parametrize("mean", [80, 100, 126, 154, 183, 212, 240])
    def test_formula_and_ifcc_consistency(self, mean):
        out = gmi_mmol_mol(mean)
        assert out == round_half_up(12.71 + 4.70587 * (mean / MGDL_PER_MMOLL))
        assert float(out).is_integer()
        gmi_pct = 3.31 + 0.02392 * mean  # Bergenstal et al. (2018)
        assert abs(out - (gmi_pct - 2.15) * 10.929) < 1.0  # the IFCC / NGSP master equation

    def test_known_value(self):
        assert gmi_mmol_mol(154) == 53  # GMI 7.0 % = 53 mmol/mol


def test_the_unit_aliases_are_gone():
    """One home for the conversion (units.from_mgdl); const.py has no aliases of it."""
    assert from_mgdl(180, UNIT_MMOLL) == 10.0 and from_mgdl(None, UNIT_MMOLL) is None
    assert not hasattr(const, "convert_glucose") and not hasattr(const, "display_unit")


def test_consensus_tables_are_inverse():
    assert units.CONSENSUS_MMOLL == {54: 3.0, 70: 3.9, 140: 7.8, 180: 10.0, 250: 13.9}
    assert {v: k for k, v in units.CONSENSUS_MMOLL.items()} == units.MGDL_FROM_CONSENSUS
    for mg, mm in units.CONSENSUS_MMOLL.items():
        assert round_half_up(mg / MGDL_PER_MMOLL, 1) == mm  # the pairs are also what plain conversion gives
