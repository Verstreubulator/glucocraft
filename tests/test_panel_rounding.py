# License: AGPL-3.0-only (LICENSE).
"""The panels round each value once, as GlucoCraft rounds it, and keep the glucose bound in one place.

Rules under test (the example panel configurations, gc_units.h, glucocraft_panel_binary.h):
- The 6-hour statistics before the first file come from the summary sensor's attributes as text: the panel reads each
  as the double GlucoCraft calculated and rounds it once by units.round_half_up (a float32 sensor would show
  286.4999948 mg/dL as 287 where GlucoCraft shows 286). The 5-minute delta, published unrounded, is read the same way
  and rounded once in the display unit (an exact 20.5 mg/dL is "+21", -17.38 mg/dL is "-1.0" mmol/L).
- The "Last reading" line gets the reading in mg/dL as received, also after a reboot (saved unrounded), and rounds it
  once in the display unit (81.9 mg/dL is 4.5 mmol/L, never 82 -> 4.6).
- The largest glucose value is one named constant (gcu::GLU_MAX = limits.DEFAULT_UPPER).
- No comment speaks of an older GlucoCraft.
The C++ cannot run here: its arithmetic is transcribed from the header's own text.
"""

from __future__ import annotations

import math
import random
import re
import struct

import pytest
from glucocraft import limits, units

from conftest import NO_PANELS
from conftest import PANELS as _FOUND
from test_panel_configs import BINARY_H, LCD, SQUARE, UNITS_H, block, read

pytestmark = pytest.mark.skipif(_FOUND is None, reason=NO_PANELS)

ATTRIBUTE_VALUES = ("mean", "std_dev", "cv", "tir", "titr", "low", "high", "very_low", "very_high", "min", "max")


def _section(text: str, name: str) -> str:
    """One top-level section of a configuration (sensor:, text_sensor:, ...)."""
    start = text.index(f"\n{name}:\n")
    nxt = re.search(r"\n[a-z_]+:\n", text[start + 1 :])
    return text[start : start + 1 + (nxt.start() if nxt else len(text))]


def _f32(v: float) -> float:
    return struct.unpack("<f", struct.pack("<f", v))[0]


def _round_half_up():
    """gc_units.h round_half_up, transcribed: x rounded to dec decimals (0 or 1), in units of its last decimal."""
    body = re.search(r"inline long round_half_up\(double x, int dec = 0\) \{(.*?)\n\}", read(UNITS_H), re.DOTALL).group(
        1
    )
    assert "const double scale = dec == 1 ? 10.0 : 1.0;" in body
    assert "return (long) copysign(floor(fabs(x) * scale + 0.5 + 1e-9), x);" in body
    return lambda x, dec=0: int(math.copysign(math.floor(abs(x) * (10.0 if dec == 1 else 1.0) + 0.5 + 1e-9), x))


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_six_hour_attributes_are_read_as_text_and_rounded_as_doubles(path):
    text = read(path)
    sensors, texts = _section(text, "sensor"), _section(text, "text_sensor")
    for ranges in ("consensus", "custom"):
        for name in ATTRIBUTE_VALUES:
            assert f"id: stats_6h_{ranges}_{name}\n" in texts and f"id: stats_6h_{ranges}_{name}\n" not in sensors
    upd = text[text.index("  - id: update_stats_display\n") :]
    upd = upd[: upd.index("\n  - id: ", 10)]
    fallback = upd[upd.index("} else {") :]
    # each attribute's text, read as GlucoCraft's double; "None" (no value: no readings, or less than 70 % of the 6
    # hours) shows "---"
    assert "return custom ? c->state : s->state;" in fallback
    assert "const double v = gcu::attr_value(t);" in fallback and 'else if (t == "None") dst = "---";' in fallback
    for call in ("text(id(stats_6h_custom_tir), id(stats_6h_consensus_tir)), gcu::pct0_attr)",
                 "text(id(stats_6h_custom_mean), id(stats_6h_consensus_mean)), gcu::fmt_attr)",
                 "gcu::pct1_cell_attr(buf, sizeof(buf), vlow)", "gcu::fmt_attr(buf, sizeof(buf), mx)"):  # fmt: skip
        assert call in fallback, call
    assert "const float" not in fallback and "gcu::pct0(" not in fallback and "gcu::fmt(" not in fallback


def test_the_attribute_rounding_is_units_round_half_up():
    rhu = _round_half_up()
    # a value just below a half: the float32 of 286.4999948 is 286.5 (a float32 sensor shows 287)
    assert _f32(286.4999948) == 286.5 and rhu(286.4999948) == 286 == units.round_half_up(286.4999948)
    rng = random.Random(9)
    values = [rng.uniform(0, 400) for _ in range(20000)] + [k / 2 + d for k in range(800) for d in (-1e-7, 0, 1e-7)]
    values += [k / 20 - 1e-12 for k in range(2000)]  # binary error just below an exact half
    for v in values:
        assert rhu(v) == units.round_half_up(v), v
        assert rhu(v, 1) / 10 == units.round_half_up(v, 1), v
        # mmol/L with one decimal, as units.from_mgdl
        assert rhu(v / units.MGDL_PER_MMOLL, 1) / 10 == units.from_mgdl(v, units.UNIT_MMOLL) or v in (
            54,
            70,
            140,
            180,
            250,
        )
    h = read(UNITS_H)
    fmt = re.search(r"inline void fmt_attr\(char \*buf, size_t n, double mg\) \{(.*?)\n\}", h, re.DOTALL).group(1)
    assert "num_scaled(buf, n, round_half_up(mg / MGDL_PER_MMOLL_D, 1), 1);" in fmt
    assert 'snprintf(buf, n, "%ld", round_half_up(mg));' in fmt
    assert "return (!s.empty() && end != s.c_str() && *end == '\\0') ? v : NAN;" in h  # "None", "unknown": no value


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_delta_is_read_as_text_and_rounded_once_in_the_display_unit(path):
    text = read(path)
    assert "id: ha_change5\n" in _section(text, "text_sensor") and "id: ha_change5\n" not in _section(text, "sensor")
    assert "const double d5 = gcu::attr_value(id(ha_change5).state);" in text
    assert "gcu::fmt_delta(buf, sizeof(buf), d5);" in text
    delta = re.search(r"inline void fmt_delta\(char \*buf, size_t n, double mg\) \{(.*?)\n\}", read(UNITS_H), re.DOTALL)
    assert "const long t = round_half_up(mg / MGDL_PER_MMOLL_D, 1);" in delta.group(1)
    assert "const long d = round_half_up(mg);" in delta.group(1)
    rhu = _round_half_up()
    # an exact 20.5 mg/dL (and its binary neighbor) is +21; -17.38 mg/dL is -1.0 mmol/L (-0.9 from a whole -17)
    assert [rhu(20.5), rhu(20.499999999999996), rhu(-17.38 / units.MGDL_PER_MMOLL, 1)] == [21, 21, -10]


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_the_last_reading_survives_a_reboot(path):
    """The last reading survives a reboot: its own time (the status bar's age, the
    weekly corner's time), its value in mg/dL as received and its LO / HI mark, saved together at most every 10
    minutes; without a current reading the line under the words shows it ("Last reading: 64 mg/dL"), the live value
    while Home Assistant has one, else the saved one, each with its own mark. The saved value is never shown in the
    value's place."""
    text = read(path)
    for gid, kind in (
        ("saved_glucose_ts", "uint32_t"),
        ("saved_glucose_mgdl", "float"),
        ("saved_glucose_limit", "int"),
    ):
        assert re.search(rf"  - id: {gid}( +#[^\n]*)?\n    type: {kind}\n    restore_value: yes\n", text), gid
    save = block(text, "              if (rts >= id(saved_glucose_ts) + 600", "              }\n")
    for line in (
        "id(saved_glucose_ts) = rts;",
        "id(saved_glucose_mgdl) = id(glucose_current).state;",
        "id(saved_glucose_limit) = gcu::limit();",
    ):
        assert line in save, line
    last = block(text, "  - id: update_last_reading\n", "\n  - id: ")
    assert "live ? id(glucose_current).state : id(saved_glucose_mgdl)" in last
    assert "live ? gcu::limit() : id(saved_glucose_limit)" in last
    # declared, saved, shown, and the mark that arrives just after the value kept with it
    assert text.count("saved_glucose_mgdl") == 4 and text.count("id(saved_glucose)") == 0


def test_the_glucose_bound_is_one_constant():
    h = read(UNITS_H)
    assert int(re.search(r"constexpr int GLU_MAX = (\d+);", h).group(1)) == limits.DEFAULT_UPPER
    for path in (BINARY_H, *LCD):
        code = re.sub(r"//[^\n]*", "", read(path))
        assert not re.search(r"[<>]=? ?500\b", code), path.name
    assert read(BINARY_H).count("gcu::GLU_MAX") == 5 and read(SQUARE).count("gcu::GLU_MAX") == 6


@pytest.mark.parametrize("path", LCD, ids=lambda p: p.stem)
def test_no_comment_speaks_of_an_older_glucocraft(path):
    assert "older GlucoCraft" not in read(path)
