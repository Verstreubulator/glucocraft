# License: AGPL-3.0-only (LICENSE).
"""Named edge-case scenarios, each available with a mg/dL and with a mmol/L source. All data are artificial.

Each scenario is the recorded state history of a glucose source entity (common.SourceState: recording time,
measurement time, state text) plus how to evaluate it: the evaluation time `now` (normally the local midnight after
the data, so that the CGM Report's period and the statistics periods end at the same moment), the report length, the
source's resolved sensor limits, and for one scenario a second source.

Values are given in mg/dL and written in the source unit at the source precision (mg/dL whole numbers, mmol/L one
decimal). Where the boundary matters in both units a pair (mg/dL value, mmol/L value) is given, so that each unit gets
the value that lies exactly on, or one display step beyond, a limit. Texts (LO, HI, "unavailable", ...) are written
as they are.

Usage:
  from tools.synth import scenarios
  sc = scenarios.build("range_limits", "mmol/L")
  for name in scenarios.NAMES: ...
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .common import DEFAULT_TZ, UNIT_MGDL, UNIT_MMOLL, UNITS, SourceState, mgdl_text, unit_name, value_text
from .generate import generate

TZ = ZoneInfo(DEFAULT_TZ)
NOW = datetime(2026, 6, 15, tzinfo=TZ)  # evaluation time of most scenarios: a Monday midnight, no DST nearby
PERIOD_START = NOW - timedelta(days=14)  # the 14-day report period: June 1-14
DATA_START = PERIOD_START - timedelta(days=1)  # one day before it, for events running into the period
DELAY = 30  # seconds between a measurement and its recording

# A gentle 4-hour cycle inside the target range (each value held 30 minutes): the background of most scenarios
BASE = (100, 115, 130, 145, 160, 145, 130, 115)
DEFAULT_LIMITS = {"lower": 40, "upper": 500, "dexcom_type": False, "nightscout": False}

Value = float | int | str | tuple


@dataclass
class Scenario:
    """One edge case: the source states and how to evaluate them."""

    name: str
    unit: str
    interval: int
    description: str
    states: list[SourceState]
    now: int
    tz: str = DEFAULT_TZ
    report_days: int = 14
    limits: dict = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    secondary: list[SourceState] | None = None
    secondary_interval: int | None = None
    tags: tuple[str, ...] = ()


class Track:
    """Builds one source's readings in time order, at a fixed reading interval."""

    def __init__(self, start: datetime, interval: int, unit: str, delay: int = DELAY):
        """Start a source's states at `start`, every `interval` seconds, in `unit`."""
        self.t = int(start.timestamp())
        self.interval = interval
        self.unit = unit_name(unit)
        self.delay = delay
        self.items: list[SourceState] = []

    def text(self, v: Value) -> str:
        """The state text of a value: a mg/dL number, a (mg/dL, mmol/L) pair, a {unit: text} dict or a text."""
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            return v[self.unit]
        if isinstance(v, tuple):
            return value_text(v[1], self.unit) if self.unit == UNIT_MMOLL else value_text(v[0], self.unit)
        return mgdl_text(float(v), self.unit)

    def reading(self, v: Value) -> Track:
        """One reading at the current time; the next one follows one interval later."""
        self.items.append(SourceState(recorded=self.t + self.delay, state=self.text(v), measured=self.t))
        self.t += self.interval
        return self

    def hold(self, v: Value, minutes: float) -> Track:
        """Readings of one value for `minutes` minutes (minutes x 60 / interval readings)."""
        if not minutes >= 0:
            raise ValueError("hold needs a duration of 0 minutes or more")
        for _ in range(int(minutes * 60 // self.interval)):
            self.reading(v)
        return self

    def fill(self, until: datetime | int, pattern=BASE, minutes_each: int = 30) -> Track:
        """Readings of the background pattern (by local clock time, so it is the same every day) up to `until`."""
        end = until if isinstance(until, int) else int(until.timestamp())
        while self.t < end:
            d = datetime.fromtimestamp(self.t, TZ)
            k = ((d.hour * 60 + d.minute) // minutes_each) % len(pattern)
            self.reading(pattern[k])
        return self

    def skip(self, seconds: int) -> Track:
        """No readings: the next reading comes `seconds` after the previous one (instead of one interval)."""
        if not seconds > 0:
            raise ValueError("skip needs a positive number of seconds (time never goes back)")
        self.t += seconds - self.interval
        return self

    def at(self, when: datetime | int) -> Track:
        """Move to a later time (no readings in between: a gap if long enough)."""
        t = when if isinstance(when, int) else int(when.timestamp())
        if t < self.t:
            raise ValueError("Track.at cannot go back in time")
        self.t = t
        return self

    def state(self, text: str, measured: int | None = None, recorded: int | None = None) -> Track:
        """An explicit state (for example "unavailable" without a measurement time), not advancing the time."""
        rec = recorded if recorded is not None else (measured if measured is not None else self.t) + self.delay
        self.items.append(SourceState(recorded=rec, state=text, measured=measured))
        return self

    def states(self) -> list[SourceState]:
        """The states in recording order."""
        return sorted(self.items, key=lambda s: s.recorded)


def _day(k: int, hour: int = 0, minute: int = 0, base: datetime = PERIOD_START, fold: int = 0) -> datetime:
    """Local time on day k of the period (day 0 = its first day)."""
    d = (base + timedelta(days=k)).date()
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=TZ, fold=fold)


def _scenario(name: str, unit: str, track: Track, description: str, **kw) -> Scenario:
    now = kw.pop("now", NOW)
    return Scenario(
        name=name,
        unit=unit_name(unit),
        interval=track.interval,
        description=description,
        states=track.states(),
        now=int(now.timestamp()),
        **kw,
    )


# ------------------------------------------------------------------------------------------------- the scenarios
def range_limits(unit: str) -> Scenario:
    """Values exactly on each consensus limit and one step beyond it, each held 60 minutes."""
    tr = Track(DATA_START, 300, unit).fill(_day(13, 8))
    for v in (
        (53, 2.9),
        (54, 3.0),
        (69, 3.8),
        (70, 3.9),
        (140, 7.8),
        (141, 7.9),
        (180, 10.0),
        (181, 10.1),
        (250, 13.9),
        (251, 14.0),
    ):
        tr.hold(v, 60)
    tr.fill(NOW)
    return _scenario(
        "range_limits",
        unit,
        tr,
        "values on every range limit (54 / 70 / 140 / 180 / 250 mg/dL = "
        "3.0 / 3.9 / 7.8 / 10.0 / 13.9 mmol/L) and one display step beyond",
        tags=("ranges",),
    )


def lo_hi_libre(unit: str) -> Scenario:
    """FreeStyle Libre outside the US: 40-500 mg/dL, LO / HI texts; values at and beyond both limits."""
    tr = Track(DATA_START, 60, unit).fill(_day(13, 6))
    tr.hold("LO", 20).hold((39, 2.1), 10).hold((40, 2.2), 10).hold((41, 2.3), 10).hold(120, 60)
    tr.hold((500, 27.8), 10).hold((501, 27.9), 10).hold("HI", 20).fill(NOW)
    return _scenario("lo_hi_libre", unit, tr, "LO / HI texts and values at the 40 / 500 mg/dL limits", tags=("limits",))


def lo_hi_dexcom(unit: str) -> Scenario:
    """Dexcom (40-400 mg/dL): the Share codes 39 / 401, LOW / HIGH texts, values at both limits."""
    tr = Track(DATA_START, 300, unit).fill(_day(13, 6))
    tr.hold((39, 2.2), 15).hold((40, 2.3), 15).hold(120, 60).hold((400, 22.2), 15).hold((401, 22.3), 15)
    tr.hold("LOW", 15).hold(120, 30).hold("HIGH", 15).fill(NOW)
    return _scenario(
        "lo_hi_dexcom",
        unit,
        tr,
        "Dexcom codes 39 / 401 and LOW / HIGH at the 40 / 400 mg/dL limits",
        limits={"lower": 40, "upper": 400, "dexcom_type": True, "nightscout": False},
        tags=("limits",),
    )


def lo_hi_medtronic(unit: str) -> Scenario:
    """Medtronic (50-400 mg/dL): "Below 50 mg/dL" / "Above 400 mg/dL" texts and values at both limits."""
    below = {UNIT_MGDL: "Below 50 mg/dL", UNIT_MMOLL: "Below 2.8 mmol/L"}
    above = {UNIT_MGDL: "Above 400 mg/dL", UNIT_MMOLL: "Above 22.2 mmol/L"}
    tr = Track(DATA_START, 300, unit).fill(_day(13, 6))
    tr.hold(below, 15).hold((49, 2.7), 15).hold((50, 2.8), 15).hold(120, 60).hold((400, 22.2), 15)
    tr.hold((401, 22.3), 15).hold(above, 15).fill(NOW)
    return _scenario(
        "lo_hi_medtronic",
        unit,
        tr,
        "Below / Above texts and values at the 50 / 400 mg/dL limits",
        limits={"lower": 50, "upper": 400, "dexcom_type": False, "nightscout": False},
        tags=("limits",),
    )


def lo_hi_upper_450(unit: str) -> Scenario:
    """A 40-450 mg/dL sensor: values at and beyond the 450 mg/dL (25.0 mmol/L) upper limit."""
    tr = Track(DATA_START, 300, unit).fill(_day(13, 6))
    tr.hold((450, 25.0), 15).hold((451, 25.1), 15).hold("HI", 15).fill(NOW)
    return _scenario(
        "lo_hi_upper_450",
        unit,
        tr,
        "values at and beyond a 450 mg/dL upper limit",
        limits={"lower": 40, "upper": 450, "dexcom_type": False, "nightscout": False},
        tags=("limits",),
    )


def gap_rule_1min(unit: str) -> Scenario:
    """1-minute data: intervals of exactly 15 minutes (no gap) and 16 minutes (a gap)."""
    tr = Track(DATA_START, 60, unit).fill(_day(13, 10))
    tr.reading(120)
    tr.skip(900).reading(125)
    tr.fill(_day(13, 12)).reading(120)
    tr.skip(960).reading(125)
    tr.fill(NOW)
    return _scenario(
        "gap_rule_1min", unit, tr, "1-minute source: 15 minutes is no gap, 16 minutes is a gap", tags=("gaps",)
    )


def gap_rule_5min(unit: str) -> Scenario:
    """5-minute data: 15 minutes (3 x interval, no gap), 16 and 20 minutes (gaps)."""
    tr = Track(DATA_START, 300, unit).fill(_day(13, 10))
    tr.reading(120).skip(900).reading(125)
    tr.fill(_day(13, 12)).reading(120).skip(960).reading(125)
    tr.fill(_day(13, 14)).reading(120).skip(1200).reading(125)
    tr.fill(NOW)
    return _scenario(
        "gap_rule_5min", unit, tr, "5-minute source: 15 minutes is no gap, 16 and 20 minutes are gaps", tags=("gaps",)
    )


def fifteen_minute_source(unit: str) -> Scenario:
    """A 15-minute source: the gap rule's threshold is 45 minutes; 45 minutes is no gap, 46 minutes is a gap."""
    tr = Track(DATA_START, 900, unit).fill(_day(13, 10))
    tr.reading(120).skip(2700).reading(125)
    tr.fill(_day(13, 16)).reading(120).skip(2760).reading(125)
    tr.fill(NOW)
    return _scenario(
        "fifteen_minute_source", unit, tr, "15-minute source (gap threshold 45 minutes)", tags=("gaps", "slow")
    )


def libre_history_then_live(unit: str) -> Scenario:
    """15-minute history (as FreeStyle Libre delivers after a reconnection) followed by 1-minute live readings."""
    tr = Track(DATA_START, 900, unit).fill(_day(10))
    live = Track(_day(10), 60, unit)
    live.fill(NOW)
    tr.items.extend(live.items)
    return _scenario(
        "libre_history_then_live", unit, tr, "15-minute history, then 1-minute live readings", tags=("gaps",)
    )


def low_midnight_period_start(unit: str) -> Scenario:
    """A low that crosses midnight at the report period's start, and one that crosses midnight inside the period."""
    tr = Track(DATA_START, 300, unit).fill(_day(-1, 23, 40)).hold((55, 3.1), 60)
    tr.fill(_day(6, 23, 50)).hold((60, 3.3), 40).fill(NOW)
    return _scenario(
        "low_midnight_period_start", unit, tr, "lows across midnight at and after the period start", tags=("events",)
    )


def dst_spring(unit: str) -> Scenario:
    """The spring-forward day (23 hours), with a low across the skipped hour."""
    now = datetime(2026, 3, 15, tzinfo=TZ)
    start = now - timedelta(days=14)
    tr = Track(start - timedelta(days=1), 300, unit).fill(datetime(2026, 3, 8, 1, 40, tzinfo=TZ))
    tr.hold((60, 3.3), 40)  # 01:40 to 03:20 local: 40 real minutes
    tr.fill(now)
    return _scenario(
        "dst_spring", unit, tr, "daylight saving begins (a 23-hour day) with a low across 02:00", now=now, tags=("dst",)
    )


def dst_fall(unit: str) -> Scenario:
    """The fall-back day (25 hours), with a low across the repeated hour."""
    now = datetime(2026, 11, 8, tzinfo=TZ)
    start = now - timedelta(days=14)
    tr = Track(start - timedelta(days=1), 300, unit).fill(datetime(2026, 11, 1, 1, 10, tzinfo=TZ, fold=0))
    tr.hold((60, 3.3), 70)  # 01:10 (first) to 01:20 (second): 70 real minutes
    tr.fill(now)
    return _scenario(
        "dst_fall",
        unit,
        tr,
        "daylight saving ends (a 25-hour day) with a low in the repeated hour",
        now=now,
        tags=("dst",),
    )


def short_history(unit: str) -> Scenario:
    """Less than 14 days of data: the last 10 days of the 14-day period."""
    tr = Track(_day(4), 300, unit).fill(NOW)
    return _scenario("short_history", unit, tr, "10 days of data in a 14-day period", tags=("rules",))


def _active(name: str, unit: str, minutes: int) -> Scenario:
    tr = Track(NOW - timedelta(minutes=minutes), 60, unit).fill(NOW)
    return _scenario(
        name, unit, tr, f"{minutes} minutes of data in 14 days ({100 * minutes / 20160:.3f}%)", tags=("rules",)
    )


def active_just_under_70(unit: str) -> Scenario:
    """14111 of 20160 minutes (69.995%, shown as 70.0%): below the 70% rule."""
    return _active("active_just_under_70", unit, 14111)


def active_just_over_70(unit: str) -> Scenario:
    """14113 of 20160 minutes (70.005%): meets the 70% rule."""
    return _active("active_just_over_70", unit, 14113)


def single_reading(unit: str) -> Scenario:
    """One reading in the whole history."""
    tr = Track(_day(13, 12), 300, unit).reading(120)
    return _scenario("single_reading", unit, tr, "a single reading", tags=("rules",))


def no_readings(unit: str) -> Scenario:
    """Only no-reading states."""
    tr = Track(_day(13, 12), 300, unit)
    tr.state("unavailable", recorded=int(_day(13, 12).timestamp())).state(
        "unknown", recorded=int(_day(13, 13).timestamp())
    )
    return _scenario("no_readings", unit, tr, "no readings (only unavailable / unknown)", tags=("rules",))


def duplicates(unit: str) -> Scenario:
    """The same measurement time recorded twice: an exact repeat, and a changed value (the last recorded counts)."""
    tr = Track(DATA_START, 300, unit).fill(NOW)
    t1 = int(_day(13, 9).timestamp())
    t2 = int(_day(13, 15).timestamp())
    first = next(s for s in tr.items if s.measured == t1)
    tr.items.append(SourceState(recorded=first.recorded + 10, state=first.state, measured=t1))
    tr.items.append(SourceState(recorded=t2 + DELAY + 20, state=tr.text((60, 3.3)), measured=t2))
    return _scenario(
        "duplicates", unit, tr, "repeated measurement times (exact, and with a changed value)", tags=("data",)
    )


def out_of_order(unit: str) -> Scenario:
    """Readings recorded out of measurement order: a 30-minute stretch arrives an hour late."""
    tr = Track(DATA_START, 300, unit).fill(NOW)
    a, b = int(_day(13, 10).timestamp()), int(_day(13, 10, 30).timestamp())
    for s in tr.items:
        if a <= (s.measured or 0) < b:
            s.recorded += 3600
            s.state = tr.text((65, 3.6))
    return _scenario("out_of_order", unit, tr, "a stretch of readings recorded one hour late", tags=("data",))


def unavailable_unknown(unit: str) -> Scenario:
    """ "unavailable" / "unknown" states in place of readings: a gap of 30 minutes, none of 10 and 15 minutes."""
    tr = Track(DATA_START, 300, unit).fill(_day(13, 9))
    tr.state("unavailable", recorded=int(_day(13, 9).timestamp()) + 10)  # between two readings: no effect
    tr.fill(_day(13, 10))
    for k in range(5):  # 10:00-10:20: no readings
        tr.state("unknown", recorded=tr.t + DELAY + 300 * k)
    tr.at(_day(13, 10, 25)).fill(_day(13, 12, 5))
    tr.state("unavailable", recorded=tr.t + DELAY)  # one reading missing: 10 minutes
    tr.at(_day(13, 12, 10)).fill(_day(13, 12, 35))
    tr.state("unavailable", recorded=tr.t + DELAY).state("unknown", recorded=tr.t + DELAY + 300)
    tr.at(_day(13, 12, 45)).fill(NOW)  # two missing: exactly 15 minutes
    return _scenario(
        "unavailable_unknown",
        unit,
        tr,
        "unavailable / unknown states: one gap (30 min), no gap at 10 and 15 minutes",
        tags=("data", "gaps"),
    )


def second_source_takeover(unit: str) -> Scenario:
    """A 1-minute primary that stops for two hours; a 5-minute secondary carries on, then the primary resumes."""
    start, stop, resume = _day(13, 4), _day(13, 10), _day(13, 12)
    pri = Track(start, 60, unit).fill(stop)
    pri.at(resume).fill(NOW)
    sec = Track(start + timedelta(seconds=40), 300, unit)
    while sec.t < int(NOW.timestamp()):
        d = datetime.fromtimestamp(sec.t, TZ)
        low = _day(13, 10, 30) <= d < _day(13, 11)
        sec.reading((60, 3.3) if low else BASE[((d.hour * 60 + d.minute) // 30) % len(BASE)])
    sc = _scenario(
        "second_source_takeover", unit, pri, "a second source fills a two-hour outage of the primary", tags=("sources",)
    )
    sc.secondary, sc.secondary_interval = sec.states(), 300
    return sc


def retention_over_200_days(unit: str) -> Scenario:
    """210 days of 5-minute data: the 200-day retention, the 6-month report and the 26-week trend.

    The evaluation time is in February, so that the 200 days contain the fall-back day (a 25-hour local day) and no
    spring-forward day: 200 local days are then 200 x 24 hours plus one hour.
    """
    now = datetime(2026, 2, 16, tzinfo=TZ)
    tr = Track(now - timedelta(days=210), 300, unit).fill(now)
    return _scenario(
        "retention_over_200_days",
        unit,
        tr,
        "210 days of data (retention 200 days)",
        report_days=180,
        now=now,
        tags=("retention", "slow"),
    )


def extended_high_boundaries(unit: str) -> Scenario:
    """Extended high-glucose events at the 120-minute start and the 15-minute end boundaries."""
    above, high, mid, ok = (251, 14.0), (260, 14.4), (200, 11.1), (170, 9.4)
    tr = Track(DATA_START, 60, unit)
    tr.fill(_day(1, 10)).hold(above, 120).hold(150, 30)  # exactly 120 minutes: an event
    tr.fill(_day(2, 10)).hold(above, 119).hold(150, 30)  # 119 minutes: no event
    tr.fill(_day(3, 10)).hold(high, 130).hold(ok, 14).hold(mid, 10).hold(ok, 15)  # ends after 154 minutes
    tr.fill(_day(4, 10)).hold(high, 125)  # cut by a gap after 125 minutes
    tr.skip(1800 + 60).hold(high, 60)
    tr.fill(_day(5, 10)).hold((300, 16.7), 60).hold("HI", 60).hold(150, 30)  # HI counts above 250
    tr.fill(_day(6, 23)).hold(high, 150).hold(150, 30)  # across midnight, counted where it starts
    tr.fill(NOW)
    return _scenario(
        "extended_high_boundaries", unit, tr, "extended high events at the 120-minute boundary", tags=("events",)
    )


def hypo_event_boundaries(unit: str) -> Scenario:
    """Hypoglycemia events (Danne et al., 2017) at the 15-minute start and end boundaries."""
    tr = Track(DATA_START, 60, unit)
    tr.fill(_day(1, 10)).hold((60, 3.3), 15).hold(100, 30)  # 15 minutes: an event
    tr.fill(_day(2, 10)).hold((60, 3.3), 14).hold(100, 30)  # 14 minutes: none
    tr.fill(_day(3, 10)).hold((50, 2.8), 20).hold((65, 3.6), 30).hold((70, 3.9), 14).hold((60, 3.3), 5)
    tr.hold((70, 3.9), 15).hold(100, 30)  # level 2 inside level 1, 69 min
    tr.fill(_day(4, 10)).hold((60, 3.3), 30).skip(1800 + 60).hold((60, 3.3), 10).hold(100, 30)  # a gap ends it
    tr.fill(_day(5, 3)).hold((55, 3.1), 40)  # at night
    tr.fill(NOW)
    return _scenario(
        "hypo_event_boundaries", unit, tr, "hypoglycemia events at the 15-minute boundaries", tags=("events",)
    )


def display_rounding(unit: str) -> Scenario:
    """A mean that rounds differently in mmol/L from the unrounded and from the whole-number mg/dL mean."""
    pattern = (
        (127, 7.1),
        (127, 7.1),
        (127, 7.1),
        (127, 7.1),
        (127, 7.1),
        (127, 7.1),
        (127, 7.1),
        (128, 7.0),
        (128, 7.0),
        (128, 7.0),
    )
    tr = Track(DATA_START, 60, unit).fill(NOW, pattern=pattern, minutes_each=6)
    return _scenario(
        "display_rounding",
        unit,
        tr,
        "mean 127.3 mg/dL (127.36 from mmol/L): mmol/L 7.1 from the exact mean, 7.0 from the whole-number mean",
        tags=("units",),
    )


def long_very_low_run(unit: str) -> Scenario:
    """A very low run of six hours across midnight, partly LO, and one of 30 minutes at the lowest value above LO."""
    tr = Track(DATA_START, 300, unit).fill(_day(5, 21))
    tr.hold((45, 2.5), 120).hold("LO", 90).hold((45, 2.5), 150).hold(100, 30)  # 21:00 to 03:00
    tr.fill(_day(9, 14)).hold((41, 2.3), 30).hold(100, 30)
    tr.fill(NOW)
    return _scenario(
        "long_very_low_run",
        unit,
        tr,
        "a six-hour very low run across midnight (LO inside it) and a run at the lowest value above LO",
        tags=("events", "extreme"),
    )


def long_very_high_run(unit: str) -> Scenario:
    """A very high run of 30 hours across two midnights, partly HI, and three hours just below the upper limit."""
    tr = Track(DATA_START, 300, unit).fill(_day(4, 20))
    tr.hold((320, 17.8), 600).hold("HI", 240).hold((320, 17.8), 960).hold(150, 30)  # 20:00 day 4 to 02:00 day 6
    tr.fill(_day(10, 12)).hold((499, 27.7), 180).hold(150, 30)
    tr.fill(NOW)
    return _scenario(
        "long_very_high_run",
        unit,
        tr,
        "a 30-hour very high run (HI inside it) and three hours just below the upper limit",
        tags=("events", "extreme"),
    )


def rapid_swings(unit: str) -> Scenario:
    """1-minute readings swinging between very low and very high: 45 and 300 mg/dL, alternating every 2 to 10 minutes
    (each band gets time; no event lasts 15 minutes)."""
    tr = Track(DATA_START, 60, unit).fill(_day(12, 8))
    for minutes in (2, 3, 5, 7, 10, 10, 7, 5, 3, 2):
        tr.hold((45, 2.5), minutes).hold((300, 16.7), minutes)
    tr.hold(100, 30).fill(NOW)
    return _scenario(
        "rapid_swings",
        unit,
        tr,
        "readings swinging between 45 and 300 mg/dL within minutes",
        tags=("events", "extreme"),
    )


def flat_line(unit: str) -> Scenario:
    """The same value for the whole history: no spread (SD and CV 0), every reading in one band."""
    tr = Track(DATA_START, 300, unit)
    while tr.t < int(NOW.timestamp()):
        tr.reading((100, 5.6))
    return _scenario("flat_line", unit, tr, "one value for 15 days (SD 0)", tags=("data", "extreme"))


def repeated_values(unit: str) -> Scenario:
    """1-minute readings that repeat each value for an hour: Home Assistant records a row for each (only the
    measurement-time attribute changes), and each counts as a reading."""
    tr = Track(DATA_START, 60, unit)
    pattern = ((110, 6.1), (110, 6.1), (180, 10.0), (69, 3.8), (250, 13.9), (54, 3.0))
    tr.fill(NOW, pattern=pattern, minutes_each=60)
    return _scenario(
        "repeated_values", unit, tr, "each value repeated for an hour (limit values among them)", tags=("data",)
    )


# Gaps from one missed reading to three days, in a 5-minute source (the gap rule: more than 15 minutes is a gap)
GAP_LENGTHS_MIN = (10, 14, 15, 16, 20, 30, 60, 120, 360, 720, 1440, 4320)


def gaps_of_every_length(unit: str) -> Scenario:
    """5-minute data with one gap of each length in GAP_LENGTHS_MIN (the time between two readings), one per day."""
    tr = Track(DATA_START, 300, unit)
    for k, minutes in enumerate(GAP_LENGTHS_MIN):  # one per day from the day before the period
        tr.fill(max(tr.t, int(_day(k - 1, 10).timestamp()))).reading(120)
        tr.skip(minutes * 60)
    tr.reading(125).fill(NOW)
    return _scenario("gaps_of_every_length", unit, tr, "gaps from 10 minutes to three days", tags=("gaps",))


def clock_jumps(unit: str) -> Scenario:
    """The CGM's clock jumps: measurement times go back 20 minutes (the readings then overlap earlier ones, one every
    2.5 minutes), and later forward by two hours (a gap without missing data)."""
    tr = Track(DATA_START, 300, unit).fill(_day(12, 9))
    back = Track(_day(12, 9) - timedelta(minutes=20) + timedelta(seconds=150), 300, unit)
    back.delay = int(timedelta(minutes=20).total_seconds()) + DELAY  # recorded after the readings before it
    back.hold((150, 8.3), 40)
    tr.items.extend(back.items)
    tr.at(_day(12, 9, 40)).fill(_day(12, 14)).at(_day(12, 16)).fill(NOW)
    return _scenario(
        "clock_jumps", unit, tr, "measurement times jump back 20 minutes and forward 2 hours", tags=("data", "gaps")
    )


def empty_history(unit: str) -> Scenario:
    """No recorded state at all."""
    return _scenario("empty_history", unit, Track(_day(13, 12), 300, unit), "no recorded state", tags=("rules",))


# Second offsets of 1-minute readings, as a real CGM's measurement times show them (never on the whole minute, a
# 59- or 61-second interval now and then)
SUB_MINUTE_OFFSETS = (1, 0, 59, 2, 58, 30, 31, 1, 0, 45)


def sub_minute_times(unit: str) -> Scenario:
    """1-minute readings at measurement times off the whole minute (SUB_MINUTE_OFFSETS), with lows of 14, 15, 16 and
    40 minutes, each starting with a reading at hh:mm:01 whose interval ends exactly at the next whole minute.

    Not in SCENARIOS: tests/test_synth_sub_minute_times.py tests it, with its known findings."""
    tr = Track(DATA_START, 60, unit)
    starts = {
        int(_day(11, 9).timestamp()): 14,
        int(_day(11, 15).timestamp()): 15,
        int(_day(12, 9).timestamp()): 16,
        int(_day(12, 22).timestamp()): 40,
    }
    low_until = 0
    for k, t in enumerate(range(tr.t, int(NOW.timestamp()), 60)):
        if t == int(NOW.timestamp()) - 60:
            break  # the last reading must be recorded before NOW
        if t in starts:
            low_until = t + starts[t] * 60
        offset = 1 if t in starts else 0 if t - 60 in starts else SUB_MINUTE_OFFSETS[k % len(SUB_MINUTE_OFFSETS)]
        d = datetime.fromtimestamp(t, TZ)
        value = (60, 3.3) if t < low_until else BASE[((d.hour * 60 + d.minute) // 30) % len(BASE)]
        tr.items.append(SourceState(recorded=t + offset + DELAY, state=tr.text(value), measured=t + offset))
    return _scenario(
        "sub_minute_times",
        unit,
        tr,
        "1-minute readings off the whole minute, lows of 14 to 40 minutes",
        tags=("events", "data"),
    )


SCENARIOS: dict[str, Callable[[str], Scenario]] = {
    f.__name__: f
    for f in (
        range_limits,
        lo_hi_libre,
        lo_hi_dexcom,
        lo_hi_medtronic,
        lo_hi_upper_450,
        gap_rule_1min,
        gap_rule_5min,
        fifteen_minute_source,
        libre_history_then_live,
        low_midnight_period_start,
        dst_spring,
        dst_fall,
        short_history,
        active_just_under_70,
        active_just_over_70,
        single_reading,
        no_readings,
        duplicates,
        out_of_order,
        unavailable_unknown,
        second_source_takeover,
        retention_over_200_days,
        extended_high_boundaries,
        hypo_event_boundaries,
        display_rounding,
        long_very_low_run,
        long_very_high_run,
        rapid_swings,
        flat_line,
        repeated_values,
        gaps_of_every_length,
        clock_jumps,
        empty_history,
    )
}
NAMES = tuple(SCENARIOS)


# ------------------------------------------------------------------------------- recorder histories by length
# The recorder history GlucoCraft finds at its setup is a test parameter: Home Assistant keeps 10 days by default, but
# any length is possible, from none to more than GlucoCraft's 200-day retention.
HISTORY_LENGTHS = {"none": 0, "1_day": 1, "10_days": 10, "30_days": 30, "six_months": 182, "over_six_months": 240}
HISTORY_VARIANTS = ("whole_days", "with_gaps", "partial_first_day")
PARTIAL_START = timedelta(hours=13, minutes=20)  # the first day of a partial_first_day history starts at 13:20


def history(length: str, variant: str, unit: str) -> Scenario:
    """A recorder history of HISTORY_LENGTHS[length] days ending at NOW: generated 5-minute data (generate.py, the
    example profile, readings aligned to the clock so that a day's first reading is at its midnight).

    whole_days: no gaps, from a local midnight; with_gaps: the generator's dropouts plus a gap of two days in the
    middle; partial_first_day: no gaps, from 13:20 on the first day. Six months and more are evaluated with the
    6-month CGM Report (180 days); shorter histories with the 14-day report.
    """
    if length not in HISTORY_LENGTHS or variant not in HISTORY_VARIANTS:
        raise ValueError(
            f"unknown history {length!r} / {variant!r}; known: {', '.join(HISTORY_LENGTHS)} / "
            f"{', '.join(HISTORY_VARIANTS)}"
        )
    unit = unit_name(unit)
    days = HISTORY_LENGTHS[length]
    name = f"history_{length}_{variant}"
    report_days = 180 if days >= 182 else 14
    tags = ("history",) + (("slow",) if days >= 182 else ())
    if not days:
        return _scenario(name, unit, Track(NOW, 300, unit), "no recorder history", report_days=report_days, tags=tags)
    start = NOW - timedelta(days=days)
    start = datetime(start.year, start.month, start.day, tzinfo=TZ)  # the local midnight, also across DST
    if variant == "partial_first_day":
        start += PARTIAL_START
    hours = (NOW.timestamp() - start.timestamp()) / 3600  # elapsed (a 23- or 25-hour day included)
    mods = ["dropouts"] if variant == "with_gaps" else ["no_gaps"]
    series = generate(None, start=start, hours=hours, interval=300, unit=unit, seed=days, modifiers=mods, aligned=True)
    states = series.states
    if variant == "with_gaps" and days >= 3:
        middle = int((start + (NOW - start) / 2).timestamp())
        states = [s for s in states if not middle <= (s.measured or s.recorded) < middle + 2 * 86400]
    tr = Track(start, 300, unit)
    tr.items = list(states)
    return _scenario(
        name,
        unit,
        tr,
        f"a {days}-day recorder history ({variant.replace('_', ' ')})",
        report_days=report_days,
        tags=tags,
    )


def from_series(series, name: str, days: float | None = None, report_days: int = 14) -> Scenario:
    """Any series (generated, a sine wave, a real history read by real_history.py) as a scenario, evaluated at the
    local midnight after its last recorded state; days: only the last `days` days before that midnight (a recorder
    that keeps that many days)."""
    zone = ZoneInfo(series.tz)
    last = max((s.recorded for s in series.states), default=int(NOW.timestamp()))
    d = datetime.fromtimestamp(last, zone).date() + timedelta(days=1)
    now = datetime(d.year, d.month, d.day, tzinfo=zone)
    states = series.states
    if days is not None:
        first = int((now - timedelta(days=days)).timestamp())
        states = [s for s in states if s.recorded >= first]
    return Scenario(
        name=name,
        unit=unit_name(series.unit),
        interval=series.interval,
        description=series.description,
        states=sorted(states, key=lambda s: s.recorded),
        now=int(now.timestamp()),
        tz=series.tz,
        report_days=report_days,
        tags=("series",),
    )


def histories() -> list[tuple[str, str]]:
    """Every (length, variant) of the history tests: no history once, each other length in every variant."""
    return [("none", "whole_days")] + [(n, v) for n in HISTORY_LENGTHS if n != "none" for v in HISTORY_VARIANTS]


def build(name: str, unit: str) -> Scenario:
    """Build scenario `name` with a source in `unit` (mg/dL or mmol/L)."""
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}; known: {', '.join(NAMES)}")
    return SCENARIOS[name](unit_name(unit))


def all_scenarios() -> list[Scenario]:
    """Every scenario in both source units."""
    return [build(n, u) for n in NAMES for u in UNITS]
