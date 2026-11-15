# License: AGPL-3.0-only (LICENSE).
"""GLU2 v3 + GCX1 files for the wall panels and the display card (panel_feed.py).

The layout is checked against the panel firmware's own structures (glucocraft_panel_binary.h of the example panels,
"#pragma pack(1)", little-endian ESP32), copied below field by field, so a change on either side shows here:

  GLU2Header       40 bytes   uint32 magic 0x32554C47 ("GLU2"), uint16 version 3, uint16 trend_count, uint32
                              stats/ranges/readings offsets, reading_count, oldest_ts, newest_ts, trends_offset,
                              directions (2 bits per page, bit 31 = present)
  GLU2PeriodStats  44 bytes   x 7 (6h ... 180d): 10 float32 + uint16 min + uint16 max
  GLU2Ranges       24 bytes   6 float32: critical_min, critical_max, normal_min, normal_max, ideal_min, ideal_max
  GLU2Reading       6 bytes   uint32 timestamp + uint16 mg/dL
  GLU2TrendWeek    48 bytes   uint32 start, uint32 end, 10 float32
  GCX1 extension              "GCX1", uint16 version 1, uint16 standard_count; GCXStandard 656 bytes each
                              (uint8 kind, pad, uint16 week_count, uint32 directions, 9 x (uint8 op, 3 pad,
                              float32), 9 x char[64]); then the second standard's week records
  GCM1 block       60 bytes   right before the GCL2 trailer in both files (gc_read_extremes): 7 x (float32 minimum,
                              float32 maximum) mg/dL unrounded, then "GCM1"; firmware without it reads STATS' uint16
  GCL2 trailer     60 bytes   the last bytes of the full and the delta file (gc_read_limit_time): 7 x (uint32 LE seconds
                              of LO, uint32 LE seconds of HI; 6 h ... 180 d), then "GCL2"

The mean, SD, CV, GMI and percentages are written as the float32 that the cards and the panels round as GlucoCraft
rounds the unrounded value (TestShownAsTheBackend: the panels' arithmetic modeled from gc_units.h, the cards' own
functions run in node).
"""

from __future__ import annotations

import itertools
import json
import math
import random
import re
import struct
import time

import pytest
from glucocraft import panel_feed as pf
from glucocraft import units
from glucocraft.const import STAT_PERIODS, TREND_PAGES
from glucocraft.storage import GlucoseReading
from glucocraft.units import UNIT_MGDL, UNIT_MMOLL, fmt

from conftest import PANELS, PERIOD_KEYS, stats_record
from node_runner import needs_node, node_json

# ---- the firmware's structures (glucocraft_panel_binary.h)
FW_MAGIC_GLU2 = 0x32554C47
FW_HEADER = struct.Struct("<IHHIIIIIIII")
FW_STATS = struct.Struct("<10fHH")
FW_RANGES = struct.Struct("<6f")
FW_READING = struct.Struct("<IH")
FW_TREND = struct.Struct("<II10f")
FW_EXT = struct.Struct("<4sHH")
FW_STANDARD = struct.Struct("<BBHI" + "B3xf" * 9 + "64s" * 9)
FW_BACKFILL_BUFFER = 1024 * 1024
FW_DELTA_BUFFER = 8 * 1024
FW_EXTREMES = struct.Struct("<14f")
FW_GCM1_SIZE = FW_EXTREMES.size + 4


def test_struct_sizes_match_the_firmware():
    assert (FW_HEADER.size, FW_STATS.size, FW_RANGES.size, FW_READING.size, FW_TREND.size, FW_STANDARD.size) == (
        40,
        44,
        24,
        6,
        48,
        656,
    )
    assert pf.HEADER_SIZE == FW_HEADER.size == pf._HEADER_FMT.size
    assert pf.STATS_SIZE == 7 * FW_STATS.size and pf._STATS_FMT.size == FW_STATS.size
    assert pf.RANGES_SIZE == FW_RANGES.size == pf._RANGES_FMT.size
    assert pf.READING_SIZE == FW_READING.size == pf._READING_FMT.size
    assert pf.TREND_SIZE == FW_TREND.size == pf._TREND_FMT.size
    assert pf._STD_FMT.size + pf.PANEL_PAGES * (pf._TARGET_FMT.size + pf.DESC_BYTES) == FW_STANDARD.size
    assert pf._EXT_FMT.size == FW_EXT.size == 8
    assert len(STAT_PERIODS) == 7 and [k for k, _ in STAT_PERIODS] == list(PERIOD_KEYS)


def test_formats_are_little_endian_without_padding():
    for f in (
        pf._HEADER_FMT,
        pf._STATS_FMT,
        pf._RANGES_FMT,
        pf._READING_FMT,
        pf._TREND_FMT,
        pf._EXT_FMT,
        pf._STD_FMT,
        pf._TARGET_FMT,
    ):
        assert f.format.startswith("<"), f.format


def test_budget_fits_the_panel_buffer():
    assert pf.BACKFILL_BUDGET_BYTES < FW_BACKFILL_BUFFER


# ------------------------------------------------------------------------------------------------ helpers
def parse_trailer(data: bytes) -> dict | None:
    """The GCL2 trailer as the firmware reads it from the end of the file (gc_read_limit_time)."""
    if len(data) < 60 or data[-4:] != b"GCL2":
        return None
    v = struct.unpack_from("<14I", data, len(data) - 60)
    return {"lo": list(v[0::2]), "hi": list(v[1::2]), "size": 60}


def parse_extremes(data: bytes, trailer: dict | None) -> dict | None:
    """The GCM1 block as the firmware finds it, right before the GCL2 trailer (gc_read_extremes)."""
    if trailer is None or trailer["size"] + FW_GCM1_SIZE > len(data):
        return None
    end = len(data) - trailer["size"]
    if data[end - 4 : end] != b"GCM1":
        return None
    v = FW_EXTREMES.unpack_from(data, end - FW_GCM1_SIZE)
    return {"min": list(v[0::2]), "max": list(v[1::2]), "size": FW_GCM1_SIZE}


def parse_runs(data: bytes, before: int) -> dict | None:
    """The GCR1 block (the stretches at the sensor's limit) right before the GCM1 block, which ends at `before`: its
    count n is the uint16 found 6 + 9 n bytes before that end."""
    if before < 6 or data[before - 4 : before] != b"GCR1":
        return None
    for n in range((before - 6) // 9 + 1):
        start = before - 6 - 9 * n
        if struct.unpack_from("<H", data, start)[0] == n:
            runs = [struct.unpack_from("<IIB", data, start + 2 + 9 * k) for k in range(n)]
            return {"runs": runs, "size": 6 + 9 * n}
    return None


def parse(data: bytes) -> dict:
    """Parse a file exactly as the firmware does (offsets from the header; the GCL2 trailer and the GCM1 block before it
    from the end)."""
    trailer = parse_trailer(data)
    extremes = parse_extremes(data, trailer)
    h = FW_HEADER.unpack_from(data, 0)
    hdr = dict(
        zip(
            (
                "magic",
                "version",
                "trend_count",
                "stats_offset",
                "ranges_offset",
                "readings_offset",
                "reading_count",
                "oldest_ts",
                "newest_ts",
                "trends_offset",
                "directions",
            ),
            h,
            strict=False,
        )
    )
    stats = [FW_STATS.unpack_from(data, hdr["stats_offset"] + i * FW_STATS.size) for i in range(7)]
    ranges = FW_RANGES.unpack_from(data, hdr["ranges_offset"])
    readings = [
        FW_READING.unpack_from(data, hdr["readings_offset"] + i * FW_READING.size) for i in range(hdr["reading_count"])
    ]
    trends = [FW_TREND.unpack_from(data, hdr["trends_offset"] + i * FW_TREND.size) for i in range(hdr["trend_count"])]
    out = {
        "header": hdr,
        "stats": stats,
        "ranges": ranges,
        "readings": readings,
        "trends": trends,
        "ext": None,
        "gcl2": trailer,
        "gcm1": extremes,
    }
    before = len(data) - (trailer["size"] if trailer else 0) - (extremes["size"] if extremes else 0)
    runs = parse_runs(data, before) if extremes else None
    out["gcr1"] = runs
    out["body"] = before - (runs["size"] if runs else 0)
    xo = hdr["trends_offset"] + hdr["trend_count"] * FW_TREND.size
    if xo + 8 <= len(data) and data[xo : xo + 4] == b"GCX1":
        _magic, version, count = FW_EXT.unpack_from(data, xo)
        stds, p = [], xo + 8
        for _ in range(count):
            raw = FW_STANDARD.unpack_from(data, p)
            p += FW_STANDARD.size
            stds.append(
                {
                    "kind": raw[0],
                    "week_count": raw[2],
                    "directions": raw[3],
                    "targets": [(raw[4 + 2 * i], raw[5 + 2 * i]) for i in range(9)],
                    "desc": [b.split(b"\0", 1)[0].decode("utf-8") for b in raw[22:31]],
                }
            )
        second = []
        if count > 1:
            second = [FW_TREND.unpack_from(data, p + i * FW_TREND.size) for i in range(stds[1]["week_count"])]
            p += len(second) * FW_TREND.size
        out["ext"] = {"version": version, "standards": stds, "second_weeks": second, "end": p}
    return out


@pytest.fixture
def feed(make_coordinator, storage):
    """Factory: feed(readings=..., **coordinator attributes) -> a coordinator whose storage holds the readings."""

    def make(readings=None, **kw):
        if readings is not None:
            storage._readings = list(readings)
        kw.setdefault("period_stats", {k: stats_record(period_seconds=s) for k, s in STAT_PERIODS})
        return make_coordinator(storage=storage, **kw)

    return make


def recent(n: int, step: int = 60, value=lambda i: 100 + i % 50, end: int | None = None):
    end = end or int(time.time()) - 30
    return [GlucoseReading(end - (n - 1 - i) * step, float(value(i))) for i in range(n)]


# ------------------------------------------------------------------------------------------------ the full file
class TestBackfill:
    def test_header_offsets_and_total_length(self, feed):
        weeks = [stats_record() for _ in range(26)]
        c = feed(recent(500), trend_weeks=weeks, trend_gmi14=[6.5] * 26)
        data = pf.build_glu2(c, delta=False)
        p = parse(data)
        h = p["header"]
        assert h["magic"] == FW_MAGIC_GLU2 and data[:4] == b"GLU2" and h["version"] == 3
        assert data[4:6] == b"\x03\x00"  # little-endian on the wire
        assert h["stats_offset"] == 40 and h["ranges_offset"] == 40 + 308 and h["readings_offset"] == 372
        assert h["reading_count"] == 500 and h["trends_offset"] == 372 + 500 * 6
        assert h["trend_count"] == 26
        assert h["oldest_ts"] == p["readings"][0][0] and h["newest_ts"] == p["readings"][-1][0]
        assert p["ext"] is not None and p["ext"]["end"] == p["body"]  # only GCR1, GCM1 and GCL2 after it
        assert p["gcr1"] == {"runs": [], "size": 6}  # no reading at the sensor's limit: an empty block
        assert len(p["ext"]["standards"]) == 1 and p["ext"]["standards"][0]["kind"] == 0

    def test_readings_are_whole_mgdl_half_up_and_filtered(self, feed):
        now = int(time.time())
        rd = [
            GlucoseReading(now - 300, 120.5),
            GlucoseReading(now - 240, 120.4),
            GlucoseReading(now - 180, 500.0),
            GlucoseReading(now - 120, 500.1),
            GlucoseReading(now - 60, 0.0),
            GlucoseReading(now - 30, 69.5),
        ]
        p = parse(pf.build_glu2(feed(rd), delta=False))
        assert [v for _, v in p["readings"]] == [121, 120, 500, 70]
        assert [t for t, _ in p["readings"]] == [now - 300, now - 240, now - 180, now - 30]

    def test_no_readings_no_file(self, feed):
        assert pf.build_glu2(feed([]), delta=False) is None
        assert pf.build_glu2(feed([]), delta=True) is None

    def test_ranges_in_firmware_order(self, feed):
        c = feed(
            recent(10),
            range_critical_low=55,
            range_critical_high=260,
            range_low=72,
            range_high=160.3,
            range_ideal_low=75,
            range_ideal_high=135,
        )
        r = parse(pf.build_glu2(c, delta=False))["ranges"]
        assert r == pytest.approx((55, 260, 72, 160.3, 75, 135), abs=1e-4)

    def test_period_stats_and_gmi_rule(self, feed):
        stats = {
            k: stats_record(
                period_seconds=s,
                gmi=6.8,
                min_value=62.5,
                max_value=280.49,
                data_availability=95.0 if k != "30d" else 60.0,
            )
            for k, s in STAT_PERIODS
        }
        p = parse(pf.build_glu2(feed(recent(10), period_stats=stats), delta=False))
        gmi = [s[3] for s in p["stats"]]
        # GMI only for 14 days or more with at least 70 % data (Battelino 2019): 6h 24h 7d none, 30d too little data
        assert gmi[:3] == [0.0, 0.0, 0.0] and gmi[4] == 0.0
        assert gmi[3] == pytest.approx(6.8) and gmi[5] == pytest.approx(6.8) and gmi[6] == pytest.approx(6.8)
        # min / max half away from zero; 30 days with 60% sensor active have not enough data: all zeros
        assert all(s[10] == 63 and s[11] == 280 for k, s in enumerate(p["stats"]) if k != 4)
        assert p["stats"][4] == (0.0,) * 10 + (0, 0)
        # unrounded in the GCM1 block, so that a display rounds them once, in its own unit
        assert p["gcm1"]["min"] == pytest.approx([62.5] * 4 + [0.0] + [62.5] * 2)
        assert p["gcm1"]["max"] == pytest.approx([280.49] * 4 + [0.0] + [280.49] * 2)

    def test_missing_period_is_zeroes(self, feed):
        p = parse(pf.build_glu2(feed(recent(10), period_stats={}), delta=False))
        assert all(s == (0.0,) * 10 + (0, 0) for s in p["stats"])
        assert p["gcm1"] == {"min": [0.0] * 7, "max": [0.0] * 7, "size": FW_GCM1_SIZE}

    def test_directions_field(self, feed):
        dirs = {"tir": "Increasing", "titr": "Decreasing", "mean": "Stable", "cv": None}
        c = feed(recent(10), trend_directions=dirs)
        d = parse(pf.build_glu2(c, delta=False))["header"]["directions"]
        assert d >> 31 == 1
        codes = [(d >> (2 * k)) & 3 for k in range(len(TREND_PAGES))]
        assert codes[:4] == [1, 2, 3, 0] and all(x == 0 for x in codes[4:])

    def test_trend_records(self, feed):
        now = int(time.time())
        wk = stats_record(mean=140.0, time_low=2.0, time_critical_low=0.5, time_high=20.0, time_critical_high=4.0)
        c = feed(recent(10), trend_weeks=[wk, None, wk], trend_gmi14=[6.6, None, None], change_at=now)
        t = parse(pf.build_glu2(c, delta=False))["trends"]
        assert len(t) == 3
        assert t[0][1] == now and t[0][1] - t[0][0] == 7 * 86400
        assert t[1][1] == now - 7 * 86400 and t[1][2:] == (0.0,) * 10  # an empty week: mean 0, kept
        assert t[0][2] == pytest.approx(140.0) and t[0][5] == pytest.approx(6.6) and t[2][5] == 0.0
        assert t[0][8] == pytest.approx(2.5) and t[0][9] == pytest.approx(24.0)  # level 1 + level 2

    def test_at_most_26_weeks(self, feed):
        c = feed(recent(10), trend_weeks=[stats_record()] * 40)
        assert parse(pf.build_glu2(c, delta=False))["header"]["trend_count"] == 26


# ------------------------------------------------------------------------------------------------ the GCX1 extension
class TestExtension:
    def test_consensus_only_when_nothing_custom(self, feed):
        ext = parse(pf.build_glu2(feed(recent(10)), delta=False))["ext"]
        assert ext["version"] == 1 and len(ext["standards"]) == 1
        std = ext["standards"][0]
        ops = [op for op, _ in std["targets"]]
        # the cited consensus targets of the nine pages: TIR > 70, CV <= 36, below 70 < 4, above 180 < 25
        assert ops == [1, 0, 0, 4, 0, 3, 3, 0, 0]
        assert [v for _, v in std["targets"]] == pytest.approx([70, 0, 0, 36, 0, 4, 25, 0, 0])
        assert std["desc"][0] == "70-180 mg/dL. Consensus >70% (Battelino 2019)"
        assert std["desc"][5] == "Below 70 mg/dL. Consensus <4% (Battelino 2019)"

    def test_mmol_descriptions(self, feed):
        ext = parse(pf.build_glu2(feed(recent(10), display_unit="mmol/L"), delta=False))["ext"]
        d = ext["standards"][0]["desc"]
        assert d[0] == "3.9-10.0 mmol/L. Consensus >70% (Battelino 2019)"
        assert d[1].startswith("3.9-7.8 mmol/L") and d[6].startswith("Above 10.0 mmol/L")

    def test_custom_offered_second(self, feed):
        c = feed(
            recent(10),
            custom_ranges=True,
            range_low=80,
            range_high=160,
            trend_weeks=[stats_record()] * 3,
            personal_trend_weeks=[stats_record(mean=111.0)] * 2,
        )
        data = pf.build_glu2(c, delta=False)
        p = parse(data)
        kinds = [s["kind"] for s in p["ext"]["standards"]]
        assert kinds == [0, 1]
        assert p["header"]["trend_count"] == 3 and len(p["ext"]["second_weeks"]) == 2
        assert p["ext"]["second_weeks"][0][2] == pytest.approx(111.0)
        assert p["ext"]["end"] == p["body"]
        custom = p["ext"]["standards"][1]
        assert custom["desc"][0] == "80-160 mg/dL. Custom goal >70%"
        assert custom["desc"][1].endswith("No goal in the settings")  # tight range: no goal
        assert custom["targets"][1] == (0, 0.0) and custom["targets"][4] == (0, 0.0)  # no tight-range or GMI goal

    def test_custom_shown_first(self, feed):
        c = feed(
            recent(10),
            custom_goals=True,
            weekly_pages_custom=True,
            goal_gmi=7.0,
            trend_weeks=[stats_record()] * 3,
            personal_trend_weeks=[stats_record(mean=99.0)] * 4,
            personal_trend_directions={"tir": "Stable"},
            trend_directions={"tir": "Increasing"},
        )
        p = parse(pf.build_glu2(c, delta=False))
        assert [s["kind"] for s in p["ext"]["standards"]] == [1, 0]
        assert p["header"]["trend_count"] == 4 and p["trends"][0][2] == pytest.approx(99.0)
        assert p["header"]["directions"] & 3 == 3  # the shown standard's
        assert p["ext"]["standards"][0]["desc"][4] == "Not a laboratory A1C (Bergenstal 2018). Goal <7.0%"
        assert len(p["ext"]["second_weeks"]) == 3

    @pytest.mark.parametrize(
        "a1c_unit, goal",
        [
            ("percent", "<7.0%"),
            ("mmol_mol", "<53 mmol/mol"),  # the GMI rule: 12.71 + 4.70587 x (7.0 - 3.31) / 0.02392 / 18.016 = 53.0
            ("both", "<7.0%"),  # both units do not fit the 63 bytes beside the citation: percent, the first unit
        ],
    )
    def test_custom_gmi_goal_in_the_a1c_unit(self, feed, monkeypatch, a1c_unit, goal):
        from glucocraft.regional import Regional

        c = feed(recent(10), custom_goals=True, weekly_pages_custom=True, goal_gmi=7.0)
        monkeypatch.setattr(type(c), "regional", property(lambda self: Regional(a1c_unit=a1c_unit)))
        desc = parse(pf.build_glu2(c, delta=False))["ext"]["standards"][0]["desc"]
        assert desc[4] == f"Not a laboratory A1C (Bergenstal 2018). Goal {goal}"
        # the widest goal the settings accept, in mmol/mol, fits the 63-byte field whole
        c.goal_gmi = 12.0
        line = parse(pf.build_glu2(c, delta=False))["ext"]["standards"][0]["desc"][4]
        assert line.endswith("<108 mmol/mol" if a1c_unit == "mmol_mol" else "<12.0%")

    def test_custom_gmi_goal_in_mmol_mol_follows_the_gmi_rule(self, feed, monkeypatch):
        """A GMI goal in mmol/mol is converted with the GMI rule (Bergenstal et al., 2018), as the GMI values and
        the weekly GMI page's axis, not with the IFCC-NGSP master equation of laboratory A1C results. Recomputed for
        every goal the settings accept (4.0-12.0 %, one decimal); 4.3 % and 9.7 % are where the two differ."""
        import math

        from glucocraft.regional import Regional

        c = feed(recent(10), custom_goals=True, weekly_pages_custom=True)
        monkeypatch.setattr(type(c), "regional", property(lambda self: Regional(a1c_unit="mmol_mol")))
        for tenth in range(40, 121):
            goal = tenth / 10
            mmol = math.floor(12.71 + 4.70587 * ((goal - 3.31) / 0.02392) / 18.016 + 0.5)  # positive: half up
            assert pf._gmi_goal(c, goal) == f"{mmol} mmol/mol", goal
        assert pf._gmi_goal(c, 9.7) == "82 mmol/mol"  # the master equation: 10.929 x (9.7 - 2.15) = 82.51 -> 83
        assert pf._gmi_goal(c, 4.3) == "24 mmol/mol"  # the master equation: 23.50 -> 23
        c.goal_gmi = 9.7
        line = parse(pf.build_glu2(c, delta=False))["ext"]["standards"][0]["desc"][4]
        assert line == "Not a laboratory A1C (Bergenstal 2018). Goal <82 mmol/mol"

    def test_custom_pages_without_a_goal_and_the_extremes_line(self, feed):
        desc = parse(pf.build_glu2(feed(recent(10), custom_goals=True), delta=False))["ext"]["standards"][1]["desc"]
        # mean and standard deviation have no custom goal at all; tight range and GMI have one only when set
        assert desc[2] == "Time-weighted mean glucose. No custom goal for this measure"
        assert desc[7] == "Spread around the mean. No custom goal for this measure"
        assert desc[1].endswith("No goal in the settings") and desc[4].endswith("No goal in the settings")
        assert desc[8] == "Lowest and highest reading of each 14-day period"

    def test_descriptions_utf8_nul_terminated_and_bounded(self, feed):
        c = feed(recent(10), custom_ranges=True)
        data = pf.build_glu2(c, delta=False)
        p = parse(data)
        for std in p["ext"]["standards"]:
            for line in std["desc"]:
                assert 0 < len(line.encode("utf-8")) <= pf.DESC_BYTES - 1

    @pytest.mark.parametrize(
        "text, field",
        [
            ("\u00e9" + "x" * 200, "\u00e9".encode() + b"x" * 61),  # 63 bytes: the 2-byte letter and 61 x
            ("x" * 62 + "\u00e9", b"x" * 62),  # the letter would end at byte 64: left out, never split
            (
                "Gl\u00fc\u00e7\u00f6\u0161\u00e9 \u00abtest\u00bb",
                "Gl\u00fc\u00e7\u00f6\u0161\u00e9 \u00abtest\u00bb".encode(),
            ),  # as written
        ],
    )
    def test_description_in_utf8_cut_at_a_whole_character(self, feed, monkeypatch, text, field):
        orig = pf._range_set

        def texts(c, custom):
            targets, lines = orig(c, custom)
            return targets, [text] + lines[1:]

        monkeypatch.setattr(pf, "_range_set", texts)
        block = pf._standard_block(feed(recent(10)), False, [], {})
        desc0 = block[8 + 9 * 8 : 8 + 9 * 8 + 64]
        assert len(block) == 656 and desc0 == field.ljust(64, b"\0")
        desc0.split(b"\0", 1)[0].decode("utf-8")  # valid UTF-8 for every reader


# ------------------------------------------------------------------------------------------------ the delta file
class TestDelta:
    def test_last_two_hours_no_trends_no_extension(self, feed):
        c = feed(recent(300, step=60), trend_weeks=[stats_record()] * 26, trend_directions={"tir": "Stable"})
        data = pf.build_glu2(c, delta=True)
        p = parse(data)
        h = p["header"]
        now = int(time.time())
        assert h["trend_count"] == 0 and h["directions"] == 0 and p["ext"] is None
        assert all(t >= now - 2 * 3600 - 5 for t, _ in p["readings"])
        assert 118 <= h["reading_count"] <= 121
        # + an empty GCR1 block (no reading at the sensor's limit: 6 bytes), GCM1 and the GCL2 trailer
        assert len(data) == 40 + 308 + 24 + 6 * h["reading_count"] + 6 + 60 + 60
        assert len(data) <= FW_DELTA_BUFFER

    def test_delta_readings_by_bisection_equal_the_filter(self, feed, monkeypatch):
        """The delta file's readings are a bisected slice of the store, the same readings (and the same order)
        as a filter over every reading, readings newer than the clock and values outside 1-500 included as before."""
        now = int(time.time())
        # three days of 5-minute readings, the last one 2 h 35 min ago, then the readings around the 2-hour edge
        rd = [GlucoseReading(now - 3 * 86400 + i * 300, float(40 + i % 400)) for i in range(3 * 288 - 30)]
        rd += [
            GlucoseReading(now - pf.DELTA_SECONDS - 1, 101.0),  # just outside the 2 hours
            GlucoseReading(now - pf.DELTA_SECONDS + 3, 102.5),  # just inside (the build reads the clock later)
            GlucoseReading(now - 10, 0.0),  # no value: left out
            GlucoseReading(now - 5, 500.4),
            GlucoseReading(now + 600, 130.0),  # later than the clock: kept, as the filter did
        ]
        rd.sort(key=lambda r: r.timestamp)
        c = feed(rd)
        calls = []
        real = c.storage.window_indices
        monkeypatch.setattr(c.storage, "window_indices", lambda *a: calls.append(a) or real(*a))

        def reference(since: int) -> list[tuple[int, int]]:
            return [
                (r.timestamp, int(pf.round_half_up(r.raw))) for r in rd if r.timestamp >= since and 0 < r.raw <= 500
            ]

        since = now - pf.DELTA_SECONDS
        copied, _intervals, any_reading = pf._snapshot(c, since)  # the copy made under the reading lock
        assert pf._readings(copied, since, None) == reference(since) and calls == [(since,)] and any_reading
        assert copied[0].timestamp == now - pf.DELTA_SECONDS - 1  # with the one reading before the 2 hours
        assert reference(since)[0] == (now - pf.DELTA_SECONDS + 3, 103) and reference(since)[-1] == (now + 600, 130)
        assert pf._readings(c.storage.readings, 0, None) == reference(0)  # the full file: every reading
        p = parse(pf.build_glu2(c, delta=True))
        assert p["readings"][0][0] >= now - pf.DELTA_SECONDS and p["readings"][-1] == (now + 600, 130)


# ------------------------------------------------------------------------------------------------ the buffer budget
class TestBudget:
    def test_small_history_not_thinned(self, feed):
        rd = recent(20_000, step=120)
        p = parse(pf.build_glu2(feed(rd), delta=False))
        assert p["header"]["reading_count"] == 20_000

    def test_180_days_of_one_minute_data_thinned_to_budget(self, feed):
        n = 180 * 1440
        rd = recent(n, step=60)
        data = pf.build_glu2(
            feed(rd, custom_ranges=True, trend_weeks=[stats_record()] * 26, personal_trend_weeks=[stats_record()] * 26),
            delta=False,
        )
        p = parse(data)
        assert len(data) <= pf.BACKFILL_BUDGET_BYTES
        ts = [t for t, _ in p["readings"]]
        assert ts == sorted(ts) and len(set(ts)) == len(ts)
        newest = ts[-1]
        last_day = [t for t in ts if t >= newest - 24 * 3600]
        assert len(last_day) == 1441  # the last 24 h complete, every reading
        assert ts[0] < newest - 170 * 86400  # still reaching back to the oldest data
        assert p["ext"]["end"] == p["body"]

    @pytest.mark.parametrize("dropout", [841, 880, 900])
    def test_thinning_makes_no_gap_beside_a_dropout_just_under_the_gap_floor(self, feed, dropout):
        """180 days of 1-minute readings with a dropout just under 15 minutes every 2 hours: GlucoCraft finds no gap,
        and the thinned panel file has no interval longer than the gap rule's floor either (the readings beside each
        dropout stay), so the 7-day to 6-month graphs break nowhere."""
        end, ts, t = int(time.time()) - 30, [], 0
        while t < 180 * 86400:
            ts.append(t)
            t += dropout if len(ts) % 120 == 0 else 60
        rd = [GlucoseReading(end - ts[-1] + x, 120.0) for x in ts]
        p = parse(pf.build_glu2(feed(rd), delta=False))
        out = [r[0] for r in p["readings"]]
        assert len(out) < len(rd)  # thinned
        assert max(b - a for a, b in itertools.pairwise(out)) <= pf.GAP_FLOOR_SECONDS


def test_a_period_with_one_reading_has_no_sd_or_cv_in_the_file(feed):
    """A single reading has no SD and no CV (statistics: None): the file carries NaN, which every display shows as
    "---"; 0.0 stays a measured SD and CV (identical readings)."""
    none = pf._STATS_FMT.unpack(pf._period_record(stats_record(reading_count=1, std_dev=None, cv=None), 6 * 3600))
    assert math.isnan(none[1]) and math.isnan(none[2]) and none[0] > 0
    flat = pf._STATS_FMT.unpack(pf._period_record(stats_record(std_dev=0.0, cv=0.0), 6 * 3600))
    assert flat[1] == 0.0 and flat[2] == 0.0
    week = parse(pf.build_glu2(feed(recent(500), trend_weeks=[stats_record(std_dev=None, cv=None)]), delta=False))
    assert math.isnan(week["trends"][0][3]) and math.isnan(week["trends"][0][4])  # start, end, mean, SD, CV


class TestLimitTrailer:
    """The GCL2 trailer: each period's seconds at the sensor's limit (LO, HI), for the warning on its graph."""

    def stats(self):
        stats = {k: stats_record(period_seconds=s) for k, s in STAT_PERIODS}
        stats["24h"] = stats_record(limit_low=2, limit_low_seconds=600.0)  # p = 1: LO
        stats["180d"] = stats_record(limit_high=1, limit_high_seconds=299.5)  # p = 6: HI, whole seconds up
        return stats

    @pytest.mark.parametrize("delta", [False, True])
    def test_both_files_carry_the_time(self, feed, delta):
        p = parse(pf.build_glu2(feed(recent(30), period_stats=self.stats()), delta=delta))
        assert p["gcl2"]["lo"] == [0, 600, 0, 0, 0, 0, 0]
        assert p["gcl2"]["hi"] == [0, 0, 0, 0, 0, 0, 300]
        assert p["gcm1"] is not None

    def test_layout_bytes(self):
        raw = pf.limit_time_trailer(
            {"14d": stats_record(limit_low=1, limit_low_seconds=70.0, limit_high=1, limit_high_seconds=5.0)}
        )
        values = [0] * 14
        values[6:8] = [70, 5]  # p = 3
        assert raw == struct.pack("<14I", *values) + b"GCL2"
        assert pf.GCL2_SIZE == len(raw) == 60

    def test_a_delta_file_with_limit_readings_alone_has_no_rows_and_its_statistics(self, feed):
        """More than 2 hours at the limit: the panels keep getting the statistics and the warning."""
        now = int(time.time())
        rows = [GlucoseReading(now - 60 * i, 40.0, "low", 1) for i in range(5, 0, -1)]
        stats = {k: stats_record(period_seconds=s) for k, s in STAT_PERIODS}
        stats["6h"] = stats_record(limit_low=5, limit_low_seconds=300.0, min_value=None, max_value=None, mean=None)
        p = parse(pf.build_glu2(feed(rows, period_stats=stats), delta=True))
        assert p["header"]["reading_count"] == 0 and p["gcl2"]["lo"][0] == 300
        assert p["stats"][0][0] == 0.0 and p["stats"][0][10:12] == (0, 0)

    def test_no_reading_at_all_gives_no_file(self, feed):
        assert pf.build_glu2(feed([]), delta=True) is None

    def test_a_week_with_limit_readings_alone_packs(self, feed):
        week = stats_record(
            mean=None, min_value=None, max_value=None, std_dev=None, cv=None, limit_low=5, limit_low_seconds=21600.0
        )
        c = feed(
            recent(120), period_stats={k: stats_record(period_seconds=s) for k, s in STAT_PERIODS}, trend_weeks=[week]
        )
        p = parse(pf.build_glu2(c, delta=False))
        assert p["trends"][0][2] == 0.0  # the mean: the file's "no value"


class TestExtremes:
    """The GCM1 block: each period's minimum and maximum unrounded, rounded once by the display."""

    def test_layout_bytes_and_order(self):
        stats = {"24h": stats_record(min_value=69.36, max_value=250.4), "180d": stats_record(min_value=40.0)}
        raw = pf.extremes_block(stats)
        assert len(raw) == pf.GCM1_SIZE == FW_GCM1_SIZE == 60 and raw[-4:] == b"GCM1"
        values = FW_EXTREMES.unpack(raw[:-4])
        assert values[2:4] == pytest.approx((69.36, 250.4)) and values[12] == pytest.approx(40.0)
        assert values[:2] == (0.0, 0.0)  # a period without statistics

    @pytest.mark.parametrize("delta", [False, True])
    def test_in_both_files_and_once_rounded_in_mmol(self, feed, delta):
        """69.4 mg/dL is 3.85 mmol/L, shown as 3.9; through whole mg/dL (69) it would be 3.8."""
        stats = {k: stats_record(period_seconds=s, min_value=69.4) for k, s in STAT_PERIODS}
        p = parse(pf.build_glu2(feed(recent(30), period_stats=stats), delta=delta))
        assert all(s[10] == 69 for s in p["stats"])  # STATS keeps whole mg/dL for older firmware
        assert p["gcm1"]["min"] == pytest.approx([69.4] * 7)
        assert fmt(p["gcm1"]["min"][0], UNIT_MMOLL) == "3.9" and fmt(p["stats"][0][10], UNIT_MMOLL) == "3.8"

    def test_older_readers_still_find_the_trailer(self, feed):
        stats = {k: stats_record(period_seconds=s) for k, s in STAT_PERIODS}
        p = parse(pf.build_glu2(feed(recent(30), period_stats=stats), delta=True))
        assert p["gcl2"] == {"lo": [0] * 7, "hi": [0] * 7, "size": 60} and p["gcm1"] is not None


# ------------------------------------------------------------------------------------------------ the mean, SD and GMI
def _f32(v: float) -> float:
    return struct.unpack("<f", struct.pack("<f", v))[0]


def _backend_texts(v: float, mean: bool) -> tuple:
    """A mean or SD as GlucoCraft shows its double: whole mg/dL, 0.1 mmol/L and, for a mean, the GMI in mmol/mol."""
    return fmt(v, UNIT_MGDL), fmt(v, UNIT_MMOLL), int(units.gmi_mmol_mol(v)) if mean else None


def _panel_constants() -> tuple[float, float]:
    """The panels' factor and 0.1 mmol/L epsilon (gc_units.h), with the source lines of the model below checked when
    the panel folder is at hand; the module's DISPLAY_R1_EPS otherwise (the same number as the cards' rh1:
    tests/test_card_shared_copies.py)."""
    if PANELS is None:
        return units.MGDL_PER_MMOLL, pf.DISPLAY_R1_EPS
    h = (PANELS / "gc_units.h").read_text(encoding="utf-8")
    assert "return (float) (copysign(floor(x + 0.5 + R1_FLOAT_EPS), (double) mg) / 10.0);" in h
    assert "const double x = fabs((double) mg) / MGDL_PER_MMOLL_D * 10.0;" in h
    assert "return 12.71 + 4.70587 * mean_mgdl / MGDL_PER_MMOLL_D; }" in h
    assert "return (long) floor(gmi_mmol_mol_exact(mean_mgdl) + 0.5 + 1e-9); }" in h
    assert 'else snprintf(buf, n, "%d", (int) lroundf(mg));' in h  # fmt: whole mg/dL
    factor = float(re.search(r"constexpr double MGDL_PER_MMOLL_D = ([0-9.]+);", h).group(1))
    eps = float(re.search(r"constexpr double R1_FLOAT_EPS = ([0-9.e+-]+);", h).group(1))
    assert eps == pf.DISPLAY_R1_EPS
    return factor, eps


def _panel_texts(x: float, mean: bool, factor: float, eps: float) -> tuple:
    """Python model of the panels' arithmetic on a file's float32 (gc_units.h): fmt (whole mg/dL: lroundf; mmol/L:
    disp, the published values of the consensus thresholds within 0.05 mg/dL, else r1_mmol, written by num) and
    gmi_mmol_mol from the mean."""
    whole = str(math.floor(abs(x) + 0.5))  # lroundf: x + 0.5 is exact in double for a float32
    r = math.floor(x + 0.5)
    if abs(_f32(x - r)) < _f32(0.05) and r in units.CONSENSUS_MMOLL:
        tenths = round(units.CONSENSUS_MMOLL[r] * 10)
    else:
        tenths = math.floor(abs(x) / factor * 10.0 + 0.5 + eps)
    gmi = math.floor(12.71 + 4.70587 * x / factor + 0.5 + 1e-9) if mean else None
    return whole, f"{tenths // 10}.{tenths % 10}", gmi


def _values(rng: random.Random, n: int, low: float, mean: bool) -> list[float]:
    """n means or SDs, 30 % placed within a few float32 steps of a rounding half: of a whole mg/dL, of a 0.1 mmol/L
    and (means) of a GMI in mmol/mol."""
    out = []
    for _ in range(n):
        v, r = rng.uniform(low, 400), rng.random()
        if r < 0.12:
            v = round(v) + 0.5 + rng.uniform(-3e-6, 3e-6)
        elif r < 0.24:
            v = (round(v / units.MGDL_PER_MMOLL, 1) + 0.05) * units.MGDL_PER_MMOLL + rng.uniform(-3e-5, 3e-5)
        elif r < 0.30 and mean:
            half = round(12.71 + 4.70587 * v / units.MGDL_PER_MMOLL) + 0.5
            v = (half - 12.71) / 4.70587 * units.MGDL_PER_MMOLL + rng.uniform(-3e-6, 3e-6)
        out.append(max(low, v))
    return out


class TestShownAsTheBackend:
    """The mean, SD and GMI of a file are each written as the float32 that the cards and the panels round as GlucoCraft
    rounds the unrounded value (panel_feed._glucose32, _pct32): the same whole mg/dL, 0.1 mmol/L and GMI on every
    display. The file layout is unchanged (the tests above), so no panel needs a new build for it."""

    @pytest.mark.parametrize(
        "value, mean, text",
        [
            (112.499998, True, ("112", "6.2", 42)),  # float32 112.5: 113 on a display
            (180.499998, True, ("180", "10.0", 60)),
            (69.499997, False, ("69", "3.9", None)),  # a standard deviation
            (112.599991, True, ("113", "6.2", 42)),  # 1e-5 of a tenth below 6.25 mmol/L: the displays' epsilon
            (137.0187947, True, ("137", "7.6", 48)),  # just below 48.5 mmol/mol
        ],
    )
    def test_recomputed_examples(self, value, mean, text):
        factor, eps = _panel_constants()
        assert _backend_texts(value, mean) == text
        assert _panel_texts(_f32(value), mean, factor, eps) != text  # the nearest float32 shows another number
        written = pf._glucose32(value, mean=mean)
        assert written == _f32(written) and abs(written - value) < 1e-4
        assert _panel_texts(written, mean, factor, eps) == text

    def test_the_gmi_is_written_as_a_percentage(self):
        """8.0499999 % is 8.0500002 in float32, which a display shows as 8.1 %; GlucoCraft shows 8.0 %."""
        assert units.round_half_up(8.0499999, 1) == 8.0 and math.floor(_f32(8.0499999) * 10 + 0.5) == 81
        assert math.floor(pf._pct32(8.0499999) * 10 + 0.5) == 80

    def test_zero_and_none_stay_the_files_no_value(self):
        assert pf._glucose32(None) == pf._glucose32(0.0) == 0.0 and pf._pct32(None) == 0.0

    def test_the_panels_round_every_value_as_the_backend(self):
        factor, eps = _panel_constants()
        rng = random.Random(44)
        for mean, low in ((True, 39.0), (False, 0.5)):
            for v in _values(rng, 20000, low, mean):
                assert _panel_texts(pf._glucose32(v, mean=mean), mean, factor, eps) == _backend_texts(v, mean), v

    @needs_node
    def test_the_card_rounds_every_value_as_the_backend(self):
        """The cards' own functions (glucocraft-shared.js gF32 and gmiMmolFromMean, nf for the GMI) run in node."""
        rng = random.Random(45)
        means = _values(rng, 6000, 39.0, True)
        sds = _values(rng, 6000, 0.5, False)
        gmis = [units.gmi_percent(v) for v in means[:3000]] + [k / 100 + 0.005 - 1e-7 for k in range(500, 1300)]
        file = {
            "means": [pf._glucose32(v, mean=True) for v in means],
            "sds": [pf._glucose32(v) for v in sds],
            "gmis": [pf._pct32(g) for g in gmis],
        }
        got = node_json(
            "const S = globalThis.glucocraftShared; const f = " + json.dumps(file) + ";\n"
            "const out = {};\n"
            "for (const k of ['means', 'sds']) { out[k] = [];\n"
            "  for (const mmol of [false, true]) { S.settings.mmol = mmol; out[k].push(f[k].map((x) => S.gF32(x))); } }\n"
            "S.settings.mmol = false;\n"
            "out.gmi_mm = f.means.map((x) => S.gmiMmolFromMean(x)); out.gmi = f.gmis.map((x) => S.nf(x, 1));\n"
            "process.stdout.write('\\u0001' + JSON.stringify(out));\n"
        )
        for i, v in enumerate(means):
            assert (got["means"][0][i], got["means"][1][i], got["gmi_mm"][i]) == _backend_texts(v, True), v
        for i, v in enumerate(sds):
            assert (got["sds"][0][i], got["sds"][1][i], None) == _backend_texts(v, False), v
        assert got["gmi"] == [f"{units.round_half_up(g, 1):.1f}" for g in gmis]

    def test_the_file_carries_the_written_values(self, feed):
        """STATS and TRENDS: the mean and SD through _glucose32, the CV, GMI and percentages through _pct32."""
        s = stats_record(mean=112.499998, std_dev=69.499997, gmi=8.0499999, data_availability=95.0)
        c = feed(recent(10), period_stats={k: s for k, _ in STAT_PERIODS}, trend_weeks=[s], trend_gmi14=[8.0499999])
        p = parse(pf.build_glu2(c, delta=False))
        want = (pf._glucose32(112.499998, mean=True), pf._glucose32(69.499997), pf._pct32(s.cv), pf._pct32(8.0499999))
        assert p["stats"][3][:4] == want and p["trends"][0][2:6] == want
        assert p["stats"][3][0] != _f32(112.499998) and p["stats"][3][3] != _f32(8.0499999)
