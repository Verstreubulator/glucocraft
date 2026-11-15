# License: AGPL-3.0-only (LICENSE).
"""Panel feed: GLU2 v3 files for the wall panels over a local-only GET webhook.

The layout of the GLU2 v3 file (the panel firmware's glucocraft_panel_binary.h reads it; tests/test_panel_feed.py
compares the two):

  HEADER    40 bytes  "GLU2", version 3, trend_count, stats/ranges/readings offsets,
                      reading_count, oldest_ts, newest_ts, trends_offset, directions (uint32: the direction of
                      each trend page, 2 bits per page in TREND_PAGES order, bit 31 = present; 0 in the delta file)
  STATS    308 bytes  7 periods (6h, 24h, 7d, 14d, 30d, 90d, 180d) x (10 float32 + 2 uint16); the mean, SD, CV and
                      percentages unrounded, the GMI in percent as GlucoCraft shows it (one decimal; the displays
                      work out the GMI in mmol/mol from the mean), each as the float32 that a display rounds as the
                      backend rounds the value (the mean and SD: _glucose32; the CV, GMI and percentages: _pct32), so
                      that a display rounds each once; an SD and a CV that do not exist (a single reading) are NaN, a
                      GMI that is not shown 0.0. A period without enough data (statistics.enough_data: sensor active
                      below 70%) is all zeros (mean 0), which every display shows as "---", as a period without
                      readings
  RANGES    24 bytes  6 float32: critical_min, critical_max, normal_min, normal_max, ideal_min, ideal_max
  READINGS   6 bytes  each: uint32 timestamp + uint16 mg/dL
  TRENDS    48 bytes  each (up to 26 weeks, week 0 = the 14 days ending now): the standard the weekly pages SHOW
  EXTENSION (full file only, right after the trend records: both standards and the goals):
            "GCX1", uint16 version 1, uint16 standard_count (1 or 2); then per standard, 656 bytes:
              uint8 kind (0 = consensus, 1 = custom), uint8 reserved, uint16 week_count, uint32 directions (as header)
              9 x target: uint8 op (0 none, 1 >, 2 >=, 3 <, 4 <=), 3 reserved, float32 value
              9 x description: 64 bytes, UTF-8, NUL-terminated (the line under each weekly page's title;
              at most 63 bytes, cut at a whole character)
            then the second standard's weekly records (its week_count x 48 bytes, as TRENDS).
            The first standard is the one in TRENDS (the one shown); old parsers ignore the extension.
  GCR1      6 + 9 n   (both files, right before the GCM1 block) the stretches of readings at the sensor's limit in the
                      file's span (limit_runs.py): uint16 count n, then n x (uint32 start, uint32 end, uint8 kind:
                      1 LO, 2 HI), then "GCR1"; little endian, no padding. The graphs mark these stretches at their
                      bottom (LO) or top (HI) edge; the readings themselves are left out of READINGS.
  GCM1      60 bytes  (both files, right before the GCL2 trailer) each period's minimum and maximum unrounded, so that
                      a display rounds them once, in its own unit: 7 x (float32 minimum, float32 maximum) mg/dL, then
                      "GCM1". STATS keeps its whole mg/dL minimum and maximum for firmware that does not know GCM1.
                      Zeros for a period without enough data.
  GCL2      60 bytes  the last bytes of both files: each period's seconds at the sensor's limit, LO and HI
                      (limit_time_trailer).

Two files: ``?file=backfill`` (all stored readings, up to the retention: 200 days, fixed) and ``?file=delta`` (the last
2 hours). All values are mg/dL (GlucoCraft's storage unit); only the weekly pages' description lines are in the display
unit (as Home Assistant's own unit handling: the stored value stays, the display converts; the panels convert what they
show). The times are uint32 seconds (Unix time, UTC), which end on 2106-02-07 read as unsigned, as every reader of this
format reads them; a reader that took them as signed would end on 2038-01-19.

A reader finds the blocks at the end of the file by their magic, from the end: GCL2 (the last 60 bytes), GCM1 before
it, and GCR1 before GCM1 (its size follows from its count: the uint16 at 6 + 9 n bytes before the end of GCR1 equals
n). A reader that does not know a block still finds the ones after it.

Auth: a Home Assistant webhook, GET only, ``local_only`` (refused from outside the local network, Home Assistant Cloud
included). The panel needs no Home Assistant token, only the id of the wall-panel address (Home Assistant's webhook id),
so no Home Assistant access token is stored on the panel. The id is the address's secret, so GlucoCraft keeps it out of
Home Assistant's log where it can (see async_register_panel_webhook).
"""

from __future__ import annotations

import logging
import math
import secrets
import struct
import time
from collections.abc import Callable

from aiohttp import web
from homeassistant.components import persistent_notification, webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    AGP_RANGES,
    CONF_PANEL_FEED,
    CONF_PANEL_WEBHOOK_ID,
    CONSENSUS_TARGET_VALUES,
    DOMAIN,
    GAP_FLOOR_SECONDS,
    GOAL_OPS,
    STAT_PERIODS,
    STORED_MGDL,
    TREND_PAGES,
)
from .coordinator import GlucoCraftCoordinator
from .i18n import t
from .limit_runs import limit_runs
from .regional import A1C_MMOL_MOL, A1C_PERCENT, Regional
from .settings import known_entry_data, setting
from .statistics import PeriodStats, enough_data, gmi_shown
from .storage import GlucoseReading
from .units import (
    MGDL_PER_MMOLL,
    UNIT_MGDL,
    UNIT_MMOLL,
    fmt,
    from_mgdl,
    gmi_goal_mmol_mol,
    gmi_mmol_mol,
    round_half_up,
)

_LOGGER = logging.getLogger(__name__)

GLU2_VERSION = 3
HEADER_SIZE = 40
STATS_SIZE = 7 * 44
RANGES_SIZE = 24
READING_SIZE = 6
TREND_SIZE = 48
MAX_TREND_WEEKS = 26

# The panels download into a 1 MB PSRAM buffer (glucocraft_panel_binary.h BACKFILL_BUFFER_SIZE). Fixed in GlucoCraft
# (not a setting): keep the file under this budget by thinning readings older than 24 h evenly (the 7 d+ graphs draw at
# most 340 points each, so they lose nothing visible). 180 days of 2-minute readings (~130k) fit without thinning.
BACKFILL_BUDGET_BYTES = 960 * 1024
DELTA_SECONDS = 2 * 3600
FULL_RES_SECONDS = 24 * 3600

# The header's last field carries each trend page's direction, 2 bits per page (TREND_PAGES order), bit 31 = present.
DIRECTION_CODES = {None: 0, "Increasing": 1, "Decreasing": 2, "Stable": 3}

_STATS_FMT = struct.Struct("<10fHH")
_RANGES_FMT = struct.Struct("<6f")
_READING_FMT = struct.Struct("<IH")
_TREND_FMT = struct.Struct("<II10f")
_HEADER_FMT = struct.Struct("<4sHHIIIIIIII")
_EXT_FMT = struct.Struct("<4sHH")
_STD_FMT = struct.Struct("<BBHI")
_TARGET_FMT = struct.Struct("<B3xf")
EXT_MAGIC = b"GCX1"
EXT_VERSION = 1
PANEL_PAGES = 9  # the nine weekly pages (TREND_PAGES without the dashboard-only GRI)
DESC_BYTES = 64
OP_CODES = {None: 0, ">": 1, ">=": 2, "<": 3, "<=": 4}
# The goal of each of the nine weekly pages (TREND_PAGES order; const.GOAL_OPS keys), None for a page without one
PAGE_GOALS = ("time_in_range", "time_in_tight_range", None, "cv", "gmi", "time_low", "time_high", None, None)
# Cited consensus targets of the nine weekly pages (const.CONSENSUS_TARGET_VALUES: Battelino 2019; CV: Monnier 2017,
# adopted by Battelino 2019); None for a page without one
CONSENSUS_TARGETS = tuple(
    (GOAL_OPS[key], CONSENSUS_TARGET_VALUES[key]) if key in CONSENSUS_TARGET_VALUES else None for key in PAGE_GOALS
)


def _or_zero(value: float | None) -> float:
    """Return a value for a GLU2 field, 0.0 for none: the file's "no value" (as for a GMI that is not shown)."""
    return 0.0 if value is None else float(value)


def _sd32(value: float | None) -> float:
    """Return a standard deviation for a GLU2 float32 field (_glucose32), NaN for none: a period with a single reading
    has no SD, and 0.0 is a measured SD (identical readings), so the displays show "---" for NaN only."""
    return math.nan if value is None else _glucose32(value)


def _cv32(value: float | None) -> float:
    """Return a CV for a GLU2 float32 field (_pct32), NaN for none, as _sd32."""
    return math.nan if value is None else _pct32(value)


_F32 = struct.Struct("<f")
_F32_BITS = struct.Struct("<I")


def _shown_digits(value: float) -> tuple[float, float, float]:
    """A percentage rounded once to a whole number, to one decimal and to two decimals (units.round_half_up), as the
    displays show it (two decimals: the two-decimal rule beside a target or goal, units.shown_against)."""
    return round_half_up(value), round_half_up(value, 1), round_half_up(value, 2)


def _on_grid(x: float) -> bool:
    """True when a float32 is exactly a number of at most two decimals (a whole number, .25, .5, .75): x * 100 is
    exact in double for a float32 below 2**29."""
    return x * 100.0 == int(x * 100.0)


def _pct32(value: float | None) -> float:
    """Return a percentage (also the CV and the GMI) for a GLU2 float32 field: the unrounded value
    (statistics.PeriodStats), as the float32 that the displays round to the same whole number, one decimal and two
    decimals as the unrounded value, and that is a whole number (a target's or goal's value) only where the value is
    exactly that number.

    float32 keeps about 7 digits (3.8e-6 below 100), so the nearest float32 of a value at a rounding half can fall on
    the other side of it: 69.45% is 69.44999695 in float32, which a display would show as 69.4%. Then the float32 one
    step (unit in the last place) nearer the value's side is written (69.45000458), which every display rounds as the
    backend does. In the same way a value just beside a whole number (70.000001%, whose nearest float32 is 70.0
    exactly) is written one step toward its own side (70.00000763), so that the two-decimal rule shows 70.01% as the
    backend does (units.shown_against), not 70.0%. A display therefore rounds the float32 as it is, without an
    epsilon of its own."""
    v = _or_zero(value)
    nearest = _F32.unpack(_F32.pack(v))[0]
    want = _shown_digits(v)

    def fits(x: float) -> bool:
        """Return True when x shows the same digits as the value and, unless it is the value itself, is not exactly a
        number of at most two decimals (_on_grid)."""
        return _shown_digits(x) == want and (x == v or not _on_grid(x))

    if v <= 0 or fits(nearest):
        return nearest
    return _nearest_fitting(v, nearest, fits, 2)


def _nearest_fitting(v: float, nearest: float, fits: Callable[[float], bool], max_steps: int) -> float:
    """The float32 nearest to v, at most max_steps steps (units in the last place) from its nearest float32, that
    fits; the nearest float32 itself when none does (two rounding boundaries closer together than the steps)."""
    bits = _F32_BITS.unpack(_F32.pack(nearest))[0]
    toward = 1 if v > nearest else -1  # a larger positive float32 has the next larger bit pattern
    for k in range(1, max_steps + 1):
        for step in (toward * k, -toward * k):
            candidate = _F32.unpack(_F32_BITS.pack(bits + step))[0]
            if fits(candidate):
                return candidate
    return nearest


# The displays' 0.1 mmol/L rounding of a file value (glucocraft-shared.js rh1, the panels' gc_units.h r1_mmol): the
# mmol/L value times 10 in double, plus R1_FLOAT_EPS (1e-5 of a tenth), so that a stored tenth at an exact half
# (112.6 mg/dL = 6.25 mmol/L) rounds up from its float32 as the backend rounds it from its double
DISPLAY_R1_EPS = 1e-5
# The steps _glucose32 may move a value: that epsilon is 1.8e-5 mg/dL, about 300 steps at the lowest mmol/L half
# (0.05 mmol/L = 0.90 mg/dL, a standard deviation), fewer than 5 at a mean of 40 mg/dL or more
_GLUCOSE32_MAX_STEPS = 1024


def _glucose_shown(mg: float, display: bool, mean: bool) -> tuple[float, ...]:
    """A mean or SD in mg/dL as shown: whole mg/dL, 0.1 mmol/L and, for a mean, its GMI in mmol/mol (the GMI rule:
    units.gmi_mmol_mol). display False: as GlucoCraft rounds its double (units.round_half_up, units.from_mgdl);
    display True: as the cards and the panels round a file's float32 (glucocraft-shared.js round, rh1 and
    gmiMmolFromMean; gc_units.h fmt, r1_mmol and gmi_mmol_mol), in the same order of operations. The published
    mmol/L values of the consensus thresholds (70 mg/dL = 3.9 mmol/L) are the same as this rounding, so they need no
    case of their own."""
    if display:
        mmol = math.floor(mg / MGDL_PER_MMOLL * 10.0 + 0.5 + DISPLAY_R1_EPS) / 10
        # Deliberately not units.gmi_mmol_mol: this is the GMI formula (Bergenstal et al., 2018) in the displays' own
        # order of operations, copied from glucocraft-shared.js gmiMmolFromMean and gc_units.h gmi_mmol_mol, because
        # the float results of another order can differ at a half. The 1e-9 is that of gmiMmolFromMean (and of
        # units.round_half_up): an allowance for binary float error at an exact half.
        gmi = math.floor(12.71 + 4.70587 * mg / MGDL_PER_MMOLL + 0.5 + 1e-9) if mean else 0.0
    else:
        mmol = from_mgdl(mg, UNIT_MMOLL)
        gmi = gmi_mmol_mol(mg) if mean else 0.0
    return round_half_up(mg), mmol, gmi


def _glucose32(value: float | None, *, mean: bool = False) -> float:
    """Return a mean or a standard deviation (mg/dL, unrounded) for a GLU2 float32 field, as the float32 that the cards
    and the panels show as GlucoCraft shows the unrounded value: the same whole mg/dL, the same 0.1 mmol/L and, for a
    mean, the same GMI in mmol/mol (the displays work it out from the file's mean).

    As for the percentages (_pct32): the nearest float32 of a value just below a rounding half can be the half itself
    (112.499998 mg/dL is 112.5 in float32, which a display would show as 113 where GlucoCraft shows 112), and the
    displays' 0.1 mmol/L epsilon (DISPLAY_R1_EPS) rounds up a value up to 1e-5 of a tenth below a half. Then the
    nearest float32 that every display rounds as the backend does is written instead (a few steps, units in the last
    place, away for a mean; up to about 300 for a standard deviation below 1 mg/dL); the value moves by less than
    1e-4 mg/dL. 0.0 stays the file's "no value"."""
    v = _or_zero(value)
    nearest = _F32.unpack(_F32.pack(v))[0]
    want = _glucose_shown(v, False, mean)

    def fits(x: float) -> bool:
        """Return True when a display rounds x (read as float32) to the value the backend shows."""
        return _glucose_shown(x, True, mean) == want

    if v <= 0 or fits(nearest):
        return nearest
    return _nearest_fitting(v, nearest, fits, _GLUCOSE32_MAX_STEPS)


def _period_record(stats: PeriodStats | None, period_seconds: int) -> bytes:
    """One period of the STATS section. The mean, SD, CV and the six percentages are the unrounded values, the GMI in
    percent as GlucoCraft shows it (one decimal; the displays work out the GMI in mmol/mol from the mean), each as the
    float32 that every display rounds as GlucoCraft rounds the value (the mean and SD: _glucose32; the CV, GMI and
    percentages: _pct32), so that every display rounds them once; the GMI follows the GMI rule (statistics.gmi_shown,
    0.0 when not shown). A period without readings or without enough data (statistics.enough_data) is all zeros."""
    if not enough_data(stats):
        return _STATS_FMT.pack(*([0.0] * 10), 0, 0)
    gmi = gmi_shown(stats, period_seconds)
    return _STATS_FMT.pack(
        _glucose32(stats.mean, mean=True),
        _sd32(stats.std_dev),
        _cv32(stats.cv),
        _pct32(gmi),
        _pct32(stats.pct_time_in_target_range),
        _pct32(stats.pct_time_in_ideal_range),
        _pct32(stats.time_low),
        _pct32(stats.time_high),
        _pct32(stats.time_critical_low),
        _pct32(stats.time_critical_high),
        # whole mg/dL, halves away from zero, for panel firmware that does not read the GCM1 block (extremes_block)
        max(0, min(65535, int(round_half_up(_or_zero(stats.min_value))))),
        max(0, min(65535, int(round_half_up(_or_zero(stats.max_value))))),
    )


def _directions_field(directions: dict[str, str | None]) -> int:
    """Each trend page's direction (one system: the displays show this, they do not work it out)."""
    v = 1 << 31
    for k, (key, _, _) in enumerate(TREND_PAGES):
        v |= DIRECTION_CODES[directions.get(key)] << (2 * k)
    return v


def _percent(v: float) -> str:
    """A goal in percent as the page text shows it: "70%", whole numbers without a decimal, others with one."""
    v = float(v)
    return f"{int(v) if v.is_integer() else f'{v:.1f}'}%"


# The weekly page whose goal is a GMI (TREND_PAGES): written in the A1C / GMI unit (_gmi_goal), the others in percent
_GMI_PAGE = next(i for i, (key, _, _) in enumerate(TREND_PAGES) if key == "gmi")


def _gmi_goal(c: GlucoCraftCoordinator, value: float) -> str:
    """The custom GMI goal (stored in percent) in the A1C / GMI unit in use, as the weekly GMI page's line writes it:
    "7.0%" or "53 mmol/mol". The goal in mmol/mol follows the GMI rule, as the GMI values and the page's axis do
    (units.gmi_goal_mmol_mol, as the CGM Report converts the goal), not the IFCC-NGSP master equation of laboratory
    A1C results; the two can differ by one in the last digit (9.7 %: 82, not 83 mmol/mol).

    With both units chosen the goal is written in percent, the first of the two: the line has 63 bytes, and the goal
    in both units does not fit beside the citation. The number has the decimal point; the displays write the decimal
    separator in use (gc_units.h sep)."""
    unit = c.regional.a1c_unit
    return Regional(a1c_unit=A1C_MMOL_MOL if unit == A1C_MMOL_MOL else A1C_PERCENT).a1c(value, gmi_goal_mmol_mol(value))


def _range_set(c: GlucoCraftCoordinator, custom: bool) -> tuple[list[tuple[str, float] | None], list[str]]:
    """Return the nine weekly pages' targets and description lines for one range set.

    Consensus: the cited targets (CONSENSUS_TARGETS). Custom: the ranges and goals in the settings; pages without a
    goal (mean, standard deviation, lowest and highest) show none. The lines are the catalogue's panel_feed.*; the
    file carries them in UTF-8, as the panels' and the cards' readers show them (_desc_bytes)."""
    if not custom:
        r = AGP_RANGES
        u = c.display_unit  # the thresholds in the display unit (the values in the file stay mg/dL)
        lo, hi, ilo, ihi = (fmt(r[k], u) for k in ("range_low", "range_high", "range_ideal_low", "range_ideal_high"))
        un = UNIT_MMOLL if u == UNIT_MMOLL else UNIT_MGDL
        return list(CONSENSUS_TARGETS), [
            t("panel_feed.consensus.tir", low=lo, high=hi, unit=un),
            t("panel_feed.consensus.titr", low=ilo, high=ihi, unit=un),
            t("panel_feed.consensus.mean"),
            t("panel_feed.consensus.cv"),
            t("panel_feed.consensus.gmi"),
            t("panel_feed.consensus.below", low=lo, unit=un),
            t("panel_feed.consensus.above", high=hi, unit=un),
            t("panel_feed.consensus.sd"),
            t("panel_feed.min_max"),
        ]
    u = c.display_unit
    lo, hi, ilo, ihi = (fmt(v, u) for v in (c.range_low, c.range_high, c.range_ideal_low, c.range_ideal_high))
    un = UNIT_MMOLL if u == UNIT_MMOLL else UNIT_MGDL
    goals = {
        "time_in_range": c.goal_time_in_target_range,
        "time_in_tight_range": c.goal_time_in_ideal_range,
        "cv": c.goal_cv,
        "gmi": c.goal_gmi,
        "time_low": c.goal_tbr,
        "time_high": c.goal_tar,
    }
    # a goal with no value = no goal set (tight range and GMI have no default goal): no line
    targets = [(GOAL_OPS[k], goals[k]) if k and goals[k] is not None else None for k in PAGE_GOALS]

    def g(i: int) -> str:
        """The goal of page i ("Custom goal >70%"), or that the settings hold none. The GMI page writes its goal in the
        A1C / GMI unit and in the short form ("Goal <108 mmol/mol"): beside "Not a laboratory A1C" and its citation,
        the widest goal in mmol/mol fits the 63 bytes only so; the page's corner label already says "Custom goal"."""
        if not targets[i]:
            return t("panel_feed.custom.no_goal")
        op, v = targets[i]
        if i == _GMI_PAGE:
            return t("panel_feed.custom.goal_short", goal=f"{op}{_gmi_goal(c, v)}")
        return t("panel_feed.custom.goal", goal=f"{op}{_percent(v)}")

    return targets, [
        t("panel_feed.custom.range", low=lo, high=hi, unit=un, goal=g(0)),
        t("panel_feed.custom.range", low=ilo, high=ihi, unit=un, goal=g(1)),
        t("panel_feed.custom.mean"),
        t("panel_feed.custom.cv", goal=g(3)),
        t("panel_feed.custom.gmi", goal=g(4)),
        t("panel_feed.custom.below", low=lo, unit=un, goal=g(5)),
        t("panel_feed.custom.above", high=hi, unit=un, goal=g(6)),
        t("panel_feed.custom.sd"),
        t("panel_feed.min_max"),
    ]


def _desc_bytes(text: str) -> bytes:
    """Return a description line as its 64-byte field: UTF-8, at most 63 bytes cut at a whole character (a letter
    is never split, so every reader gets valid UTF-8), padded with NUL bytes. The panels draw it with the glyphs of
    their catalogue (make_panel_texts.py); firmware older than UTF-8 lines passes the bytes on to the same drawing,
    where a letter outside its fonts stays blank."""
    raw = text.encode("utf-8")[: DESC_BYTES - 1].decode("utf-8", "ignore").encode("utf-8")
    return raw.ljust(DESC_BYTES, b"\0")


def _standard_block(c: GlucoCraftCoordinator, custom: bool, weeks: list, directions: dict) -> bytes:
    """Return one standard's block of the extension: kind, week count, directions, targets and descriptions."""
    targets, texts = _range_set(c, custom)
    out = [_STD_FMT.pack(1 if custom else 0, 0, len(weeks), _directions_field(directions))]
    for target in targets[:PANEL_PAGES]:
        out.append(_TARGET_FMT.pack(OP_CODES[target[0] if target else None], float(target[1]) if target else 0.0))
    for text in texts[:PANEL_PAGES]:
        out.append(_desc_bytes(text))
    return b"".join(out)


def _extension(c: GlucoCraftCoordinator, now: int, custom_shown: bool) -> bytes:
    """Both standards (the shown one first), so card and panels name and color the weekly pages from the file."""
    cons = (False, list(c.trend_weeks[:MAX_TREND_WEEKS]), c.trend_directions)
    pers = (True, list(c.personal_trend_weeks[:MAX_TREND_WEEKS]), c.personal_trend_directions)
    offered = c.uses_custom_ranges() or c.goals_custom()
    stds = [pers, cons] if custom_shown else ([cons, pers] if offered else [cons])
    out = [_EXT_FMT.pack(EXT_MAGIC, EXT_VERSION, len(stds))]
    out += [_standard_block(c, custom, weeks, dirs) for custom, weeks, dirs in stds]
    if len(stds) > 1:
        out += _trend_records(c, now, stds[1][1])
    return b"".join(out)


def _trend_records(coordinator: GlucoCraftCoordinator, now: int, weeks: list) -> list[bytes]:
    """Return the weekly records, newest first, up to the oldest week with enough data.

    A week without enough data in between is sent empty (mean 0), so the trend pages show the gap and keep the older
    weeks. Each record's values are the 14 days ending at the week's end (as Change Over Time); the start field stays
    the week's start (end - 7 days), which the displays use only to label the axis.

    `weeks`: the consensus weeks or the weeks on the ranges in the settings (the custom standard), as the coordinator
    built them with the statistics; a week counts only with enough data (statistics.enough_data).
    time_low / time_high are the combined bands (level 1 + level 2), not GlucoCraft's level-1-only time_low /
    time_high.
    """
    c = coordinator
    now = c.change_at or now  # the 14-day windows' "now" (computed hourly with Change Over Time)
    out: list[bytes] = []
    for week, s in enumerate(weeks):
        end_ts = now - week * 7 * 86400
        if s is None:
            out.append(_TREND_FMT.pack(end_ts - 7 * 86400, end_ts, *([0.0] * 10)))
            continue
        gmi = c.trend_gmi14[week] if week < len(c.trend_gmi14) else None  # of the 14-day window (>= 70 % data)
        out.append(
            _TREND_FMT.pack(
                end_ts - 7 * 86400,
                end_ts,
                _glucose32(s.mean, mean=True),  # rounded on the displays as the backend rounds it (_glucose32)
                _sd32(s.std_dev),
                _cv32(s.cv),
                _pct32(gmi),  # 0.0: none
                _pct32(s.pct_time_in_target_range),
                _pct32(s.pct_time_in_ideal_range),
                _pct32(s.time_below_low),
                _pct32(s.time_above_high),  # the combined bands, unrounded (statistics.PeriodStats)
                _or_zero(s.min_value),
                _or_zero(s.max_value),
            )
        )
    return out


def _readings(
    readings: list[GlucoseReading], since: int, budget_bytes: int | None, runs_bytes: int = 0
) -> list[tuple[int, int]]:
    """Return the (time, whole mg/dL) readings since `since` (`readings`: the snapshot's readings, _snapshot), older
    ones thinned to fit `budget_bytes` if given, with `runs_bytes` taken by the GCR1 block.

    A reading at the sensor's limit (LO / HI) has no value and is left out (the graphs mark its stretch from GCR1)."""
    rows = [
        (r.timestamp, int(round_half_up(r.raw)))  # halves away from zero
        for r in readings
        if r.timestamp >= since and r.limit is None and 0 < r.raw <= STORED_MGDL[1]
    ]
    if budget_bytes is None:
        return rows
    fixed = runs_bytes + (
        HEADER_SIZE
        + STATS_SIZE
        + RANGES_SIZE
        + 2 * MAX_TREND_WEEKS * TREND_SIZE  # both standards' weeks
        + _EXT_FMT.size
        + 2 * (_STD_FMT.size + PANEL_PAGES * (_TARGET_FMT.size + DESC_BYTES))  # + extension
        + GCM1_SIZE
        + GCL2_SIZE
    )  # + the GCM1 block and the GCL2 trailer
    max_rows = max(1, (budget_bytes - fixed) // READING_SIZE)
    if len(rows) <= max_rows:
        return rows
    cut = rows[-1][0] - FULL_RES_SECONDS
    recent = [r for r in rows if r[0] >= cut]
    older = [r for r in rows if r[0] < cut]
    room = max(0, max_rows - len(recent))
    if room == 0:
        return recent[-max_rows:]
    thinned = _thin(older, room)
    _LOGGER.debug("Panel feed: %d older readings thinned to %d to fit the panel buffer", len(older), room)
    return thinned + recent


def _thin(rows: list[tuple[int, int]], room: int) -> list[tuple[int, int]]:
    """Return `room` of the readings `rows`, evenly spread, keeping both readings beside every long interval.

    Leaving out a reading joins its two intervals into one. Beside an interval that is no gap but close to the gap
    rule's floor (GAP_FLOOR_SECONDS), that would turn it into a gap on the displays where GlucoCraft finds none. So
    both readings beside every interval longer than the floor less twice the mean interval after thinning are kept,
    and the rest are spread evenly over the remaining room; where those readings alone would not fit, all are spread
    evenly."""
    span = (rows[-1][0] - rows[0][0]) / room  # the mean interval after thinning
    limit = GAP_FLOOR_SECONDS - 2 * span
    keep: set[int] = set()
    for k in range(1, len(rows)):
        if rows[k][0] - rows[k - 1][0] > limit:
            keep.update((k - 1, k))
    if len(keep) >= room:
        keep = set()
    rest = [i for i in range(len(rows)) if i not in keep]
    free = room - len(keep)
    step = len(rest) / free
    picked = keep.union(rest[int(i * step)] for i in range(free))
    return [rows[i] for i in sorted(picked)]


# The GCL2 trailer (the last bytes of the full file and of the delta file; glucocraft_panel_binary.h
# gc_read_limit_time): the time of each period's readings at the sensor's limit (LO / HI), for the warning on the period
# graphs (a reading at the limit has no value, so the graphs draw no point for it). Read from the end of the file, so a
# panel that does not know it ignores it:
#   7 x (uint32 LE seconds of LO, uint32 LE seconds of HI)   in the order of STAT_PERIODS: 6 h, 24 h, 7 d, 14 d, 30 d,
#                                                            90 d, 180 d (0 for a period without such readings)
#   "GCL2"                                                   the last 4 bytes
GCL2_MAGIC = b"GCL2"
_LIMIT_TIME_FMT = struct.Struct("<14I")
GCL2_SIZE = _LIMIT_TIME_FMT.size + len(GCL2_MAGIC)

# The GCM1 block (both files, immediately before the GCL2 trailer; glucocraft_panel_binary.h gc_read_extremes): each
# period's minimum and maximum without rounding. STATS carries them as whole mg/dL (uint16), and a display in mmol/L
# would then round them twice (to whole mg/dL here, to 0.1 mmol/L on the display), which can change the last digit;
# every other statistic is a float32 and is rounded once, at display. A reader finds the block in front of the GCL2
# trailer and uses the whole mg/dL values of STATS when it is absent, so older firmware and older files keep working.
#   7 x (float32 minimum, float32 maximum)   mg/dL, in the order of STAT_PERIODS; 0.0 for a period without readings
#   "GCM1"                                   the block's last 4 bytes
GCM1_MAGIC = b"GCM1"
_EXTREMES_FMT = struct.Struct("<14f")
GCM1_SIZE = _EXTREMES_FMT.size + len(GCM1_MAGIC)


def limit_time_trailer(period_stats: dict) -> bytes:
    """The GCL2 trailer for the period statistics (label -> PeriodStats): each period's seconds of LO and of HI."""
    values: list[int] = []
    for label, _ in STAT_PERIODS:
        s = period_stats.get(label)
        seconds = s.limit_seconds if s is not None else {"low": 0, "high": 0}
        values += (min(seconds["low"], 0xFFFFFFFF), min(seconds["high"], 0xFFFFFFFF))
    return _LIMIT_TIME_FMT.pack(*values) + GCL2_MAGIC


def extremes_block(period_stats: dict) -> bytes:
    """The GCM1 block: each period's minimum and maximum (label -> PeriodStats), unrounded, in mg/dL; zeros for a
    period without readings or without enough data (statistics.enough_data)."""
    values: list[float] = []
    for label, _ in STAT_PERIODS:
        s = period_stats.get(label)
        values += (_or_zero(s.min_value), _or_zero(s.max_value)) if enough_data(s) else (0.0, 0.0)
    return _EXTREMES_FMT.pack(*values) + GCM1_MAGIC


# The GCR1 block (both files, immediately before the GCM1 block): the stretches of readings at the sensor's limit
# (limit_runs.limit_runs) in the file's span, for the marks at the graphs' bottom (LO) and top (HI) edge.
#   uint16 n                                      the number of stretches (at most 65535, the newest kept)
#   n x (uint32 start, uint32 end, uint8 kind)    epoch seconds; kind 1 = LO, 2 = HI; oldest first
#   "GCR1"                                        the block's last 4 bytes
GCR1_MAGIC = b"GCR1"
_RUN_FMT = struct.Struct("<IIB")
_RUN_COUNT_FMT = struct.Struct("<H")
RUN_KINDS = {"low": 1, "high": 2}


def limit_runs_block(runs: list[tuple[int, int, str]]) -> bytes:
    """The GCR1 block for the limit runs (start, end, "low" | "high"), oldest first."""
    runs = runs[-0xFFFF:]
    body = b"".join(_RUN_FMT.pack(start, end, RUN_KINDS[kind]) for start, end, kind in runs)
    return _RUN_COUNT_FMT.pack(len(runs)) + body + GCR1_MAGIC


def _snapshot(coordinator: GlucoCraftCoordinator, since: int) -> tuple[list[GlucoseReading], list[int], bool]:
    """Copy what a file needs of the stored readings (under the coordinator's reading lock: coordinator.locked): the
    readings from the last one before `since` on, their reading intervals (storage.reading_intervals; the one reading
    before `since` lets a stretch at the sensor's limit that began before it continue into the file) and whether any
    reading lies at or after `since`. The file is then built from the copy outside the lock."""
    store = coordinator.storage
    readings = store.readings  # loads the store if needed
    i = store.window_indices(since)[0]
    j = max(0, i - 1)
    iv = store.reading_intervals(coordinator.detected_interval_seconds)
    return readings[j:], iv[j:], i < len(readings)


def build_glu2_file(coordinator: GlucoCraftCoordinator, delta: bool) -> bytes | None:
    """Build a file for the views and the wall panels' webhook (executor): the readings copied under the coordinator's
    reading lock, the file built outside it, so that readings are stored meanwhile (build_glu2)."""
    now = int(time.time())
    snapshot = coordinator.locked(_snapshot, coordinator, now - DELTA_SECONDS if delta else 0)
    return build_glu2(coordinator, delta, snapshot, now)


def build_glu2(
    coordinator: GlucoCraftCoordinator,
    delta: bool,
    snapshot: tuple[list[GlucoseReading], list[int], bool] | None = None,
    now: int | None = None,
) -> bytes | None:
    """Build the backfill (delta=False) or the 2-hour delta (delta=True) file. None if there are no readings.

    snapshot: the copied readings (_snapshot, build_glu2_file); None: copied here, by a caller that holds the reading
    lock."""
    now = int(time.time()) if now is None else int(now)
    since = now - DELTA_SECONDS if delta else 0
    readings, intervals, any_reading = snapshot if snapshot is not None else _snapshot(coordinator, since)
    runs = limit_runs(readings, intervals, since)
    runs_block = limit_runs_block(runs)
    rows = _readings(readings, since, None if delta else BACKFILL_BUDGET_BYTES, len(runs_block))
    # readings at the sensor's limit alone (LO / HI, left out of the rows): a file with no rows, for its statistics
    if not rows and not any_reading:
        return None
    # the panels read the weekly pages from the full file only; the standard they show comes first (TRENDS)
    custom = not delta and coordinator.weekly_pages_custom
    shown = coordinator.personal_trend_weeks if custom else coordinator.trend_weeks
    trends = [] if delta else _trend_records(coordinator, now, list(shown[:MAX_TREND_WEEKS]))
    directions = coordinator.personal_trend_directions if custom else coordinator.trend_directions

    stats_offset = HEADER_SIZE
    ranges_offset = stats_offset + STATS_SIZE
    readings_offset = ranges_offset + RANGES_SIZE
    trends_offset = readings_offset + len(rows) * READING_SIZE

    parts = [
        _HEADER_FMT.pack(
            b"GLU2",
            GLU2_VERSION,
            len(trends),
            stats_offset,
            ranges_offset,
            readings_offset,
            len(rows),
            rows[0][0] if rows else now,
            rows[-1][0] if rows else now,
            trends_offset,
            0 if delta else _directions_field(directions),
        )
    ]
    for label, seconds in STAT_PERIODS:
        parts.append(_period_record(coordinator.period_stats.get(label), seconds))
    c = coordinator
    parts.append(
        _RANGES_FMT.pack(
            c.range_critical_low,
            c.range_critical_high,
            c.range_low,
            c.range_high,
            c.range_ideal_low,
            c.range_ideal_high,
        )
    )
    parts.append(b"".join(_READING_FMT.pack(ts, v) for ts, v in rows))
    parts.extend(trends)
    if not delta:
        parts.append(_extension(coordinator, now, custom))
    parts.append(runs_block)
    parts.append(extremes_block(coordinator.period_stats))
    parts.append(limit_time_trailer(coordinator.period_stats))
    return b"".join(parts)


# ---------------------------------------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------------------------------------


# The notification of a new wall-panel address (new_webhook_id); the removal deletes it (__init__.py)
WEBHOOK_NOTICE_ID = "glucocraft_panel_webhook"
NOTIFICATION_IDS = (WEBHOOK_NOTICE_ID,)


def new_webhook_id(hass: HomeAssistant, *, replaced: bool) -> str:
    """A new id of the wall-panel address (Home Assistant's webhook id), announced by a notification: the first address,
    or one that replaces an earlier address (which then stops working). The notification does not contain the id,
    because every Home Assistant user can read notifications and the webhook serves the stored history without a login
    on the home network; the id is shown to administrators on the settings screen (config_flow._panel_address_text)."""
    webhook_id = f"glucocraft_panel_{secrets.token_hex(16)}"
    message = t("panel_feed.address.replaced" if replaced else "panel_feed.address.first")
    persistent_notification.async_create(
        hass,
        message,
        title=t("panel_feed.address.title"),
        notification_id=WEBHOOK_NOTICE_ID,
    )
    return webhook_id


def ensure_webhook_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """The id of the entry's wall-panel address (its webhook id), or None when no wall-panel address has been
    created.

    The address exists only when wall panels are used: *Create a new wall-panel address* in the settings makes it
    (config_flow._async_finish, with new_webhook_id), never the first setup; an entry that already has an address
    keeps it. An entry with wall panels on (CONF_PANEL_FEED) but without an address gets its first address here."""
    webhook_id = entry.data.get(CONF_PANEL_WEBHOOK_ID)
    if webhook_id:
        return webhook_id
    if not setting(entry.data, CONF_PANEL_FEED):
        return None
    webhook_id = new_webhook_id(hass, replaced=False)
    new_data = known_entry_data({**entry.data, CONF_PANEL_WEBHOOK_ID: webhook_id})  # only the keys GlucoCraft knows
    hass.config_entries.async_update_entry(entry, data=new_data)
    # the running coordinator holds a copy of the entry's data made before the address existed; it gets the address
    # at once, so that its configuration ("panel_feed") does not wait for the next reload
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is not None:
        coordinator.update_settings({CONF_PANEL_WEBHOOK_ID: webhook_id})
    return webhook_id


# The methods Home Assistant passes to the handler. Home Assistant's webhook component writes the webhook id into its
# log (a warning) for every request whose method the webhook does not accept, and for an error raised by the
# handler. The handler therefore accepts every method the component supports except HEAD (which the component itself
# answers with 200, without a log line) and answers 405 to all but GET itself: the address stays read-only, and its
# secret id stays out of the log.
_HANDLED_METHODS = ("GET", "POST", "PUT")


def async_register_panel_webhook(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Register the read-only (GET), local-only wall-panel address (a Home Assistant webhook) for this entry. Returns
    its id, or None when no wall-panel address has been created (nothing is registered then)."""
    webhook_id = ensure_webhook_id(hass, entry)
    if webhook_id is None:
        return None

    async def _handle(hass: HomeAssistant, wh_id: str, request: web.Request) -> web.Response:
        """Serve the backfill or the delta file to a GET; any other method is refused (405)."""
        if request.method != "GET":
            return web.Response(status=405, headers={"Allow": "GET"})
        coordinator: GlucoCraftCoordinator | None = getattr(entry, "runtime_data", None)
        which = request.query.get("file", "backfill")
        if coordinator is None or which not in ("backfill", "delta"):
            return web.Response(status=404)
        try:
            # from a copy of the readings made under the coordinator's lock, so a reading arriving meanwhile cannot
            # change the data mid-build, and readings are stored while the file is built
            data = await hass.async_add_executor_job(build_glu2_file, coordinator, which == "delta")
        except Exception:  # logged here, without the id: Home Assistant's own log line would name it
            _LOGGER.exception("GlucoCraft could not build the wall-panel file %s", which)
            return web.Response(status=500)
        if data is None:
            return web.Response(status=503, text=t("api.no_readings"))
        return web.Response(body=data, content_type="application/octet-stream", headers={"Cache-Control": "no-cache"})

    webhook.async_register(
        hass, DOMAIN, "GlucoCraft panel feed", webhook_id, _handle, local_only=True, allowed_methods=_HANDLED_METHODS
    )
    return webhook_id
