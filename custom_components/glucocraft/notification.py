# License: AGPL-3.0-only (LICENSE).
"""GlucoCraft's notifications: the daily, weekly and monthly summaries and the optional no-data notice.

The summaries are sent on a schedule (a set time, a set day), never because of a glucose value, a range or a trend.
Each reports the statistics of a period that has passed and ends with the line that names it a notification
(notify.last_line), in Home Assistant and on the phones alike. GlucoCraft sends no notification about high or low
glucose.

The no-data notice (off by default) is a status notice about GlucoCraft itself: once no reading has arrived for the
chosen delay and there is no current data, one notice per outage says since when GlucoCraft has received no readings;
it is removed when a reading arrives. It never depends on a glucose value, a range or a trend.

They are Home Assistant persistent notifications, optionally also sent to the notify actions chosen in the options
(phones) as ordinary messages (a title and a text, nothing else); the no-data notice goes to each of the two only where
it is chosen.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import (
    AGP_RANGES,
    COMPARE,
    CONF_NO_DATA_NOTICE,
    CONF_NO_DATA_NOTICE_DELAY,
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_WEEKLY_DAY,
    CONF_NOTIFY_WEEKLY_ENABLED,
    CONF_PHONE_TARGETS,
    CONF_PHONE_TYPES,
    CONSENSUS_TARGET_VALUES,
    ENOUGH_DATA_PCT,
    GMI_MIN_PERIOD_SECONDS,
    GOAL_OPS,
    NO_DATA_NOTICE_ID,
    PERIOD_SECONDS,
)
from .coordinator import GlucoCraftCoordinator
from .i18n import t, tp
from .limits import LIMIT_FLAGS, limit_minutes
from .regional import Regional
from .settings import setting
from .spoken import SPOKEN_COMPARISONS, speak_limit, speak_trend
from .statistics import PeriodStats, enough_data, gmi_shown, gri_shown
from .units import UNIT_MGDL, UNIT_MMOLL, fmt, gmi_mmol_mol, is_mmol, round_half_up, shown_against

_LOGGER = logging.getLogger(__name__)

# Every persistent notification this module creates (the three summaries and the no-data notice): the removal deletes
# them (__init__.async_remove_entry)
SUMMARY_KINDS = ("daily", "weekly", "monthly")
NOTIFICATION_IDS = (
    *(f"glucocraft_{kind}_summary" for kind in SUMMARY_KINDS),
    NO_DATA_NOTICE_ID,
)


async def async_forward_to_phones(
    hass: HomeAssistant, config: dict[str, Any], kind: str, title: str, message: str
) -> None:
    """Also send a GlucoCraft notification to the notify actions chosen in the options (phones etc.), if `kind`
    (const.PHONE_TYPES: summaries, no_data_notice) is one the user picked. The same text as the Home Assistant
    notification, as plain text, sent as an ordinary message: only a title and a message, never a priority, a channel, a
    sound or another delivery option, so that the phone delivers it under its own notification settings. A missing or
    failing target is logged and skipped - the Home Assistant notification is always created first.

    One notify action at a time, each call waiting until the action has run (blocking): a failure then reaches the
    handler below. A call that does not wait runs in the background, and Home Assistant logs its failure itself, with
    the whole call (the message text) and the error's text (a phone's webhook id or device name). The waiting never
    delays a reading: the no-data notice is sent from a task of its own, the summaries at their time or by their
    action. It delays only the next notify action in the list (Home Assistant's Mobile App
    integration gives up on a cloud push after 10 seconds)."""
    if kind not in setting(config, CONF_PHONE_TYPES):
        return
    text = plain_text(message)
    targets = setting(config, CONF_PHONE_TARGETS)
    # the log names a notify action by its position in the settings, not by its name: the name usually names a
    # person's phone, which the diagnostics redact as well
    for pos, name in enumerate(targets, 1):
        if not hass.services.has_service("notify", name):
            _LOGGER.warning(
                "GlucoCraft: notify action %d of %d is not available - %s not sent there", pos, len(targets), title
            )
            continue
        try:
            await hass.services.async_call("notify", name, {"title": title, "message": text}, blocking=True)
        except Exception as err:  # noqa: BLE001 - one broken phone must not stop the others
            # the error's class only: the text of a Companion app error names the phone (its device name or its
            # webhook id)
            _LOGGER.warning(
                "GlucoCraft: sending %s to notify action %d of %d failed: %s",
                title,
                pos,
                len(targets),
                err.__class__.__name__,
            )


# Markdown emphasis as GlucoCraft writes it: **bold**, __bold__ and *italic* (an asterisk directly before and after
# the text, not inside a word); a phone shows plain text, where the marks would appear as characters
_BOLD = re.compile(r"(\*\*|__)(.+?)\1", re.DOTALL)
_ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")


def plain_text(message: str) -> str:
    """A notification's Markdown as plain text for the notify actions (phones): bold and italic marks removed."""
    return _ITALIC.sub(r"\1", _BOLD.sub(r"\2", message))


def short_statement() -> str:
    """Return the short standard statement: the line before the last line of every summary notification."""
    return t("notify.statement_short")


def _with_statement(lines: list[str]) -> str:
    """Return a summary's text, ending with the short standard statement and the last line of every summary
    (notify.last_line)."""
    return "\n".join([*lines, "", f"*{short_statement()}*", "", t("notify.last_line")])


# The in-text citations of the summaries (APA 7; citations are not translated)
CITE_CONSENSUS = "Battelino et al., 2019"
CITE_CV = "Battelino et al., 2019; Monnier et al., 2017"

# The consensus range limits the summaries name (mg/dL): very low, low, high and very high, from the one table of the
# consensus ranges (const.AGP_RANGES; Battelino et al., 2019), as the PDF and the panel files take them
VERY_LOW, LOW, HIGH, VERY_HIGH = (
    AGP_RANGES[k] for k in ("range_critical_low", "range_low", "range_high", "range_critical_high")
)


def _rg(coordinator: GlucoCraftCoordinator) -> Regional:
    """Return the coordinator's regional settings (decimal separator, A1C unit)."""
    return coordinator.regional


def _gl(coordinator: GlucoCraftCoordinator, mg: float | None, unit: str) -> str:
    """A glucose value in the display unit with the decimal separator in use (the number only)."""
    return _rg(coordinator).dec(fmt(mg, unit))


def _pct(rg: Regional, value: float | None, rules: tuple[float, ...] = ()) -> str:
    """A percentage (the number only) as the summaries write it: the unrounded value rounded once to one decimal, with
    the decimal separator in use; next to a rule it is compared with (`rules`), with two decimals when one decimal
    would show the rule's value although the value does not meet it or passes it (units.shown_against: 69.96%)."""
    if value is None:
        return "-"
    shown, decimals = shown_against(value, rules, 1)
    return rg.num(shown, decimals)


# Notification bodies are plain text first (ACCESSIBILITY.md rule 7): a glucose value carries its unit in words, in
# the display unit ("62 milligrams per deciliter", "3.4 millimoles per liter"), so that a screen reader or a phone's
# spoken notification reads it correctly. Within a block of lines that share the unit, it is named once, in the
# block's heading. Titles and every other text keep "mg/dL" / "mmol/L"; percent and A1C keep their symbols.
_UNIT_KEYS = {UNIT_MGDL: "mg_dl", UNIT_MMOLL: "mmol_l"}


def unit_words(unit: str) -> str:
    """The display unit in words: "milligrams per deciliter" or "millimoles per liter"."""
    return t(f"spoken.unit_name.{_UNIT_KEYS[UNIT_MMOLL if is_mmol(unit) else UNIT_MGDL]}")


def _glw(coordinator: GlucoCraftCoordinator, mg: float | None, unit: str) -> str:
    """A glucose value with its unit in words: "112 milligrams per deciliter"."""
    return t("notify.value_unit", value=_gl(coordinator, mg, unit), unit=unit_words(unit))


# Durations are written in words, as the spoken text writes them ("15 hours 36 minutes", "3 days 4 hours"), because a
# screen reader reads "15h 36min" or "3 d" letter by letter (ACCESSIBILITY.md rule 7); a part that is zero is left out
def _amount(unit: str, count: int) -> str:
    """A whole number of days, hours or minutes in words: "1 hour", "36 minutes" (spoken.unit.<unit>)."""
    return tp(f"spoken.unit.{unit}", count, number=count)


def _duration(larger_unit: str, larger: int, smaller_unit: str, smaller: int) -> str:
    """Two parts of a duration in words, "15 hours 36 minutes"; the smaller part is left out when it is zero."""
    if not smaller:
        return _amount(larger_unit, larger)
    return t("duration.pair", larger=_amount(larger_unit, larger), smaller=_amount(smaller_unit, smaller))


def _pct_to_time(pct: float) -> str:
    """Convert a percentage of a day to hours and minutes in words ("15 hours 36 minutes"); whole minutes, halves
    rounded away from zero (units.round_half_up, the one rounding rule)."""
    mins = int(round_half_up(pct / 100 * 1440))
    if mins < 60:
        return _amount("minute", mins)
    return _duration("hour", mins // 60, "minute", mins % 60)


def _limit_time(seconds: float) -> str:
    """The time of readings at the sensor's limit in words, "15 minutes", "2 hours 5 minutes": whole minutes, rounded
    up, so any time above zero is at least 1 minute (limits.limit_minutes, as the CGM Report counts it)."""
    mins = limit_minutes(seconds)
    if mins < 60:
        return _amount("minute", mins)
    return _duration("hour", mins // 60, "minute", mins % 60)


# The terms of the summaries: spelled out at their first use in each summary and abbreviated after it
# (notify.term.<term>.full / .short), so that the plain-text notification explains every abbreviation once
def _term(name: str, seen: set[str]) -> str:
    """A term as a summary writes it: "Coefficient of variation (CV)" the first time, "CV" after it. `seen` holds the
    terms already spelled out in the summary being written."""
    if name in seen:
        return t(f"notify.term.{name}.short")
    seen.add(name)
    return t(f"notify.term.{name}.full")


def _assess_target(
    value: float | None,
    target: float,
    direction: str,
    label: str,
    source: str,
    rg: Regional,
    time_equiv: bool = False,
    marked: bool = False,
) -> str | None:
    """A percentage with its published consensus target and citation. "met" / "not met" (an arithmetic comparison of
    the unrounded value, no symbols) only when `marked` (14 days or more with at least 70% sensor active, Battelino et
    al., 2019); otherwise the value and the target only. The value is shown rounded once, with two decimals where one
    decimal would show the target's value (_pct). The comparison is written in words (spoken.SPOKEN_COMPARISONS), as
    a screen reader reads a notification as written and skips "<" and ">"; `direction` is one of the goal signs
    (const.GOAL_OPS, compared by const.COMPARE)."""
    if value is None or direction not in COMPARE:
        return None
    met = COMPARE[direction](value, target)
    shown = f"{_pct(rg, value, (target,))}%"
    if time_equiv:
        shown = t("notify.target.value_per_day", value=shown, time=_pct_to_time(value))
    key = ("notify.target.met" if met else "notify.target.not_met") if marked else "notify.target.line"
    target_text = t(f"spoken.compare.{SPOKEN_COMPARISONS[direction]}", value=f"{rg.g(target)}%")
    return "  " + t(key, label=label, value=shown, target=target_text, source=source)


def _marked(stats: PeriodStats | None, period_seconds: int) -> bool:
    """Met / not met only for 14 days or more with at least 70% sensor active, compared before rounding
    (PeriodStats.sensor_active_ok; Battelino et al., 2019)."""
    return stats is not None and period_seconds >= GMI_MIN_PERIOD_SECONDS and stats.sensor_active_ok


def _sufficient(stats: PeriodStats | None) -> bool:
    """A summary section is shown only with enough data: sensor active at least 70% of its period, compared before
    rounding (statistics.enough_data), the same rule as on every other surface."""
    return enough_data(stats)


# The periods whose missing data the not-enough-data line counts in hours (the others in days)
_HOUR_PERIODS = ("6h", "24h")


def not_enough_line(coordinator: GlucoCraftCoordinator, key: str) -> str:
    """The line that stands for a period without enough data (statistics.enough_data): "Last 30 days: not enough data
    yet. GlucoCraft shows a period's statistics once the sensor has covered at least 70% of it. So far it has covered
    23%. With no gaps, about 14 more days are needed." The coverage is shown in whole percent, on its side of the 70%
    rule (units.shown_against: 69.6% is 69.60%, never 70%); the time still needed is 70% of the period less the time
    covered, rounded half up to whole hours (the 6- and 24-hour periods) or days, at least 1. Without readings in the
    period the coverage is 0%."""
    seconds = PERIOD_SECONDS[key]
    stats = coordinator.period_stats.get(key)
    covered = stats.covered_seconds if stats is not None else 0.0
    active = stats.sensor_active_exact if stats is not None else 0.0
    rg = _rg(coordinator)
    step = 3600 if key in _HOUR_PERIODS else 86400
    needed = max(1, int(round_half_up((ENOUGH_DATA_PCT * seconds / 100 - covered) / step)))
    more = tp("notify.not_enough.hours" if step == 3600 else "notify.not_enough.days", needed)
    return t(
        "notify.not_enough.line",
        period=t(f"notify.period.{key}.label"),
        active=rg.num(*shown_against(active, (ENOUGH_DATA_PCT,), 0)),
        more=more,
    )


def _consensus_lines(
    coordinator: GlucoCraftCoordinator, key: str, period_seconds: int, label: str, seen: set[str]
) -> list[str]:
    """The consensus targets of one period, on the consensus-range statistics (period_stats_agp), never on the ranges
    in the settings. Time below / above range = level 1 + level 2, as the consensus defines them. Mean, GMI and GRI
    have no consensus target: shown as values. The block's heading names the glucose unit once, in words; CV, GMI and
    GRI are spelled out at their first use in the summary (`seen`, _term)."""
    agp = coordinator.period_stats_agp.get(key)
    if agp is None:
        return []
    unit = coordinator.display_unit
    rg = _rg(coordinator)

    def g(mg: float | None) -> str:
        """A consensus threshold, or the period's mean (none without a measured reading), in the display unit."""
        return t("spoken.not_available") if mg is None else _gl(coordinator, mg, unit)

    marked = _marked(agp, period_seconds)
    tbr, tar = agp.time_below_low, agp.time_above_high  # the combined bands, unrounded
    src = CITE_CONSENSUS
    out = ["", "  " + t("notify.consensus.heading", period=label, unit=unit_words(unit), source=src)]
    if not marked:
        out.append("  " + t("notify.consensus.unmarked", source=src))
    # (value, goal key of its consensus target and direction, label): the time bands with their time per day
    bands = (
        (agp.pct_time_in_target_range, "time_in_range", t("notify.consensus.tir", low=g(LOW), high=g(HIGH))),
        (tbr, "time_low", t("notify.consensus.tbr", low=g(LOW))),
        (agp.time_critical_low, "time_very_low", t("notify.consensus.tbr2", low=g(VERY_LOW))),
        (tar, "time_high", t("notify.consensus.tar", high=g(HIGH))),
        (agp.time_critical_high, "time_very_high", t("notify.consensus.tar2", high=g(VERY_HIGH))),
    )

    def target(value: float | None, key: str, label: str, source: str, time_equiv: bool) -> str | None:
        """The line of one consensus target (const.CONSENSUS_TARGET_VALUES, GOAL_OPS)."""
        tg, op = CONSENSUS_TARGET_VALUES[key], GOAL_OPS[key]
        return _assess_target(value, tg, op, label, source, rg, time_equiv=time_equiv, marked=marked)

    targets = [
        *(target(v, key, lab, src, True) for v, key, lab in bands),
        target(agp.cv, "cv", _term("cv", seen), CITE_CV, False),
    ]
    out.extend(line for line in targets if line)
    values = [t("notify.consensus.mean", value=g(agp.mean))]
    gmi = gmi_shown(agp, period_seconds)  # one GMI rule: 14 days or more with at least 70%
    if gmi is not None:
        # the A1C / GMI unit in use; mmol/mol from the mean (Bergenstal et al., 2018)
        values.append(t("notify.consensus.gmi", name=_term("gmi", seen), value=rg.a1c(gmi, gmi_mmol_mol(agp.mean))))
    gri = gri_shown(agp, period_seconds)  # 14 days or more with enough data, as the GMI
    if gri is not None:
        values.append(t("notify.consensus.gri", name=_term("gri", seen), value=rg.num(gri, 1)))
    out.append("  " + t("notify.consensus.no_target", values=t("notify.list_separator").join(values)))
    return out


def _overview_lines(coordinator: GlucoCraftCoordinator, stats: PeriodStats, seen: set[str]) -> list[str]:
    """The period's values (mean, CV, lowest to highest, sensor active); with custom ranges also the time in, below
    and above the custom ranges (level 1 and level 2 together). The glucose unit is named in the period's heading
    (_period_section). The values are measured ones; readings at the sensor's limit (LO / HI) are left out of them, with
    a line that gives their number and time. The percentages are rounded once from the unrounded values."""
    c = coordinator
    unit = c.display_unit
    rg = _rg(c)

    def gl(mg: float | None) -> str:
        """A glucose value in the display unit; none without a measured reading."""
        return t("spoken.not_available") if mg is None else _gl(c, mg, unit)

    # sensor active is compared with the 70% rule (a section is shown only with enough data): shown on its side of it
    active = _pct(rg, stats.sensor_active_exact, (ENOUGH_DATA_PCT,))
    out = [
        "  "
        + t(
            "notify.overview.values",
            mean=gl(stats.mean),
            cv_name=_term("cv", seen),
            cv=_pct(rg, stats.cv),
            low=gl(stats.min_value),
            high=gl(stats.max_value),
            active=active,
        )
    ]
    if c.uses_custom_ranges():
        below, above = stats.time_below_low, stats.time_above_high  # the combined bands, unrounded
        tir = stats.pct_time_in_target_range
        out.append(
            "  "
            + t(
                "notify.overview.custom",
                low=gl(c.range_low),
                high=gl(c.range_high),
                tir=_pct(rg, tir),
                tir_time=_pct_to_time(tir),
                below=_pct(rg, below),
                below_time=_pct_to_time(below),
                above=_pct(rg, above),
                above_time=_pct_to_time(above),
            )
        )
    # the readings at the sensor's limit: their number and the time they represent, per kind
    counts = stats.limit_readings
    limit_parts = [
        tp("notify.overview.limit_part", counts[flag], limit=speak_limit(flag), time=_limit_time(seconds))
        for flag, seconds in stats.limit_seconds.items()
        if seconds and counts.get(flag)
    ]
    if limit_parts:
        out.append("  " + t("notify.overview.limit_time", parts=t("notify.list_separator").join(limit_parts)))
    return out


def _period_section(coordinator: GlucoCraftCoordinator, key: str, seen: set[str]) -> list[str]:
    """One period of a summary: its values and its consensus targets, under a heading that names the glucose unit
    (the heading, its note and the period's name in the text: notify.period.<key>.*); without enough data
    (statistics.enough_data) the not-enough-data line instead (not_enough_line). `seen`: the terms already spelled out
    in the summary (_term)."""
    stats = coordinator.period_stats.get(key)
    if not _sufficient(stats):
        return ["", f"*{not_enough_line(coordinator, key)}*"]
    seconds = PERIOD_SECONDS[key]
    unit = unit_words(coordinator.display_unit)
    title = t(f"notify.period.{key}.title")
    if key in _PERIOD_NOTES:
        heading = t("notify.period.heading_note", title=title, note=t(f"notify.period.{key}.note"), unit=unit)
    else:
        heading = t("notify.period.heading", title=title, unit=unit)
    out = ["", heading]
    out += _overview_lines(coordinator, stats, seen)
    out += _consensus_lines(coordinator, key, seconds, t(f"notify.period.{key}.label"), seen)
    return out


# The periods whose heading carries a note ("Rolling 90 Days (quarterly view)")
_PERIOD_NOTES = ("90d",)


# The difference in time in range that a summary marks with its cited context: 5 percentage points, the change the 2023
# consensus statement considers clinically meaningful for an individual participant in clinical trials (Battelino et
# al., 2023). The weekly and monthly summaries list a comparison only from this difference (GlucoCraft rule); the
# daily summary lists its comparison whenever both of its periods have enough data (_tir_comparison).
COMPARE_MARK_POINTS = 5


def _tir_comparison(
    coordinator: GlucoCraftCoordinator, a: str, b: str, always: bool, seen: set[str] | None = None
) -> list[str]:
    """Time in range (consensus range) of period a against period b, in percentage points, in words: "Time in range
    (70 to 180 milligrams per deciliter), last 14 days compared with the last 90 days: 6.2 percentage points higher."
    From a difference of COMPARE_MARK_POINTS the cited context follows, once per summary (`seen`, as _term). On the
    consensus-range statistics, and only when both periods have enough data (_sufficient): a difference of two periods
    with too few data is not listed. The difference is that of the two values as shown (one
    decimal each: 70.5% and 65.4% differ by 5.1 points), and the 5-point mark is taken from it."""
    sa, sb = coordinator.period_stats_agp.get(a), coordinator.period_stats_agp.get(b)
    if not _sufficient(sa) or not _sufficient(sb):
        return []
    shown_a, shown_b = (round_half_up(s.pct_time_in_target_range, 1) for s in (sa, sb))
    diff = round_half_up(shown_a - shown_b, 1)  # the rounding removes the float error of the subtraction only
    marked = abs(diff) >= COMPARE_MARK_POINTS
    if not marked and not always:
        return []
    unit = coordinator.display_unit
    rng = t("notify.compare.range", low=_gl(coordinator, LOW, unit), high=_glw(coordinator, HIGH, unit))
    if diff == 0:
        difference = t("notify.compare.equal")
    else:
        value = _rg(coordinator).num(abs(diff), 1)  # the decimal separator in use
        difference = tp("notify.compare.higher" if diff > 0 else "notify.compare.lower", abs(diff), 1, value=value)
    line = t(
        "notify.compare.line",
        range=rng,
        a=t(f"notify.period.{a}.label"),
        b=t(f"notify.period.{b}.label"),
        difference=difference,
    )
    seen = set() if seen is None else seen
    if marked and "compare" not in seen:
        seen.add("compare")
        line = t("notify.compare.marked", comparison=line)
    return ["", "  " + line]


def _expiry_text(remain_hours: float) -> str:
    """The sensor expiry line, in words and without a symbol: "Sensor expires in 2 days 5 hours". remain_hours: the
    exact time left. Whole hours, rounded down as the display cards count the time left, so that the line never says
    more time than is left; up to 24 hours, then days and hours, so it never reads "2 days 24 hours". "Sensor
    expired" only when no time is left. A warning sign is not used: these are notifications."""
    if remain_hours <= 0:
        return t("status.expired")  # the status label of an expired sensor
    total = math.floor(remain_hours)
    if total < 1:
        return t("notify.expiry.under_hour")
    if total <= 24:
        return t("notify.expiry.in", time=_amount("hour", total))
    days, hours = divmod(total, 24)
    return t("notify.expiry.in", time=_duration("day", days, "hour", hours))


def _age_text(seconds: float) -> str:
    """An age in words, "2 minutes", "7 hours 5 minutes" or "3 days 4 hours" (whole minutes, rounded down)."""
    minutes = max(0, int(seconds // 60))
    if minutes < 60:
        return _amount("minute", minutes)
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return _duration("hour", hours, "minute", minutes)
    days, hours = divmod(hours, 24)
    return _duration("day", days, "hour", hours)


def _data_current(c: GlucoCraftCoordinator) -> bool:
    """True only while there is current data: a reading, and the no-current-data flag off (the one fact every display
    tests). The status is not tested: an expired sensor that still delivers has current data, and the summary's expiry
    line says that the sensor has expired."""
    return (c.last_reading_ts or 0) > 0 and c.current_glucose is not None and not c.no_current_data


def _trend_words(c: GlucoCraftCoordinator) -> str:
    """The trend while it is known, in words only, in sentence case ("Changing slowly"); no arrow symbol, because a
    screen reader reads an arrow as its symbol name (ACCESSIBILITY.md rule 7). Empty when there is no trend."""
    arrow = c.trend_arrow  # empty while the trend is not shown (no current data, a warm-up)
    words = speak_trend(c.trend_description or arrow) if arrow else ""
    return words[:1].upper() + words[1:]


def _value_words(c: GlucoCraftCoordinator, mg: float, unit: str, limit: Any) -> str:
    """A glucose value as a notification body states it: "62 milligrams per deciliter", or a LO / HI reading in words
    ("below the sensor's lower limit"), never the stored limit value (spoken.speak_limit)."""
    return speak_limit(limit) if limit in LIMIT_FLAGS else _glw(c, mg, unit)


def _reading_time(c: GlucoCraftCoordinator, ts: int, now: float) -> str:
    """A reading's local time as the notifications give it: "1:02 AM" for a reading of today (at `now`), otherwise
    with its date, in the time and date formats in use."""
    rg = _rg(c)
    when = dt_util.as_local(dt_util.utc_from_timestamp(ts))
    today = dt_util.as_local(dt_util.utc_from_timestamp(now)).date()
    return rg.clock(when) if when.date() == today else rg.stamp(when)


def last_reading_lines(c: GlucoCraftCoordinator, now: float | None = None) -> list[str]:
    """The last reading as a summary shows it: "Last reading: 112 milligrams per deciliter (Changing slowly) at
    1:02 AM (2 minutes before this summary)." The trend while the data are current, a LO / HI reading in words, the
    date when the reading is not from today; while there is no current data (the flag, _data_current) a first line "No
    current data since 1:02 AM." After the sensor's stated expiry the reading is given as usual, and the summary's
    expiry line says "Sensor expired". While the reading's own time is ahead of the clock (time_untrusted_at), its age
    is not known, as on every display: the value alone, with a line that says why (notify.last_reading_untrusted), and
    no time. Empty without a reading."""
    ts = c.last_reading_ts or 0
    mg = c.current_glucose
    if ts <= 0 or mg is None:
        return []
    now = time.time() if now is None else now
    unit = c.display_unit
    limit = c.current_limit
    value = _value_words(c, mg, unit, limit)
    if c.time_untrusted_at(int(now)):
        return [t("notify.last_reading_untrusted", reading=value)]
    when = _reading_time(c, ts, now)
    current = _data_current(c)
    trend = _trend_words(c) if current else ""
    out = [] if current else [t("notify.no_current_data_since", time=when)]
    reading = t("notify.reading_with_trend", value=value, trend=trend) if trend else value
    out.append(t("notify.last_reading", reading=reading, time=when, age=_age_text(now - ts)))
    return out


def _build_daily_summary(coordinator: GlucoCraftCoordinator) -> str:
    """Build the daily summary: the last reading, the sensor expiry, the rolling 24 hours with the published targets,
    and the time in range of 14 days against 90 days. No GMI or GRI: neither is shown for a 24-hour period
    (statistics.gmi_shown, gri_shown).

    Sources:
    - Battelino et al. (2019) for the Time In Range / Time Below Range / Time Above Range targets and the 14-day,
      70 % data rule
    - Monnier et al. (2017) for the CV threshold (adopted by Battelino et al., 2019)
    - Battelino et al. (2023) for the 5-percentage-point difference in time in range
    """
    lines = [f"**{t('notify.title.daily')}**", ""]
    # The last reading with its own measurement time and age, never as "current": while there is no current data
    # the first line says so (the reading can be hours old)
    lines += last_reading_lines(coordinator)

    # --- Sensor expiry ---
    # From the coordinator's expiry (None while no expiry entity is picked or its value is not known), as the Sensor
    # Expiry entity computes it; the exact time left, rounded once (_expiry_text)
    expires_at = coordinator.expires_at
    if expires_at is not None:
        lines.append(_expiry_text((expires_at - dt_util.utcnow()).total_seconds() / 3600))

    # --- Rolling 24 Hours: its values with enough data, else its not-enough-data line (_period_section) ---
    lines += _period_section(coordinator, "24h", set())

    # --- 14d vs 90d trend comparison (brief, included in daily): each period is judged on its own, so a short last
    # 24 hours does not hide it; it needs enough data in both of its periods (_tir_comparison) ---
    lines += _tir_comparison(coordinator, "14d", "90d", always=True)

    return _with_statement(lines)


def _build_weekly_summary(coordinator: GlucoCraftCoordinator) -> str:
    """Build the weekly summary: rolling 7-day and 14-day statistics with the published targets. Sent once per week
    on the configured day, before the daily summary."""
    lines = [f"**{t('notify.title.weekly')}**", "", t("notify.weekly.intro")]

    # Each period on its own: its values with enough data, else its not-enough-data line (_period_section), so a
    # short last 7 days never hides a 14-day period that has enough data
    seen: set[str] = set()  # the terms spelled out once per summary (_term)
    lines += _period_section(coordinator, "7d", seen)
    lines += _period_section(coordinator, "14d", seen)

    # --- 14d vs 90d trend comparison ---
    lines += _tir_comparison(coordinator, "14d", "90d", always=False)

    return _with_statement(lines)


async def _send_summary(hass: HomeAssistant, coordinator: GlucoCraftCoordinator, kind: str, build) -> None:
    """Build a summary under the coordinator's lock (executor), create it as the persistent notification
    glucocraft_<kind>_summary (replacing the previous one) and forward it to the phones chosen for the summaries.
    kind: "daily", "weekly" or "monthly" (its title is notify.title.<kind>)."""
    summary = await hass.async_add_executor_job(coordinator.locked, build, coordinator)
    title = t(f"notify.title.{kind}")
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {"title": title, "message": summary, "notification_id": f"glucocraft_{kind}_summary"},
    )
    await async_forward_to_phones(hass, coordinator.settings, "summaries", title, summary)
    _LOGGER.info("GlucoCraft %s summary notification sent", kind)


async def async_send_daily_summary(hass: HomeAssistant, coordinator: GlucoCraftCoordinator) -> None:
    """Send the daily summary as a persistent notification."""
    await _send_summary(hass, coordinator, "daily", _build_daily_summary)


def _build_monthly_summary(coordinator: GlucoCraftCoordinator) -> str:
    """Build the monthly summary: the statistics of the rolling 30 and 90 days and 6 months (180 days).

    Sent on the configured weekly day in the ISO weeks whose number is a multiple of 4 (weeks 4, 8, ... 52: 4 weeks
    apart, 5 across the end of a year with 53 weeks). Each period is judged on its own: its values only with enough
    data (_sufficient), otherwise its not-enough-data line. Compares mid-term (30d), quarterly (90d) and long-term
    (180d) trajectory, each comparison only when both of its periods have enough data.
    """
    lines = [f"**{t('notify.title.monthly')}**", "", t("notify.monthly.intro")]

    # Each period on its own: its values with enough data, else its not-enough-data line (_period_section), so a
    # short last 30 days never hides a 90-day or 6-month period that has enough data
    seen: set[str] = set()  # the terms spelled out once per summary (_term)
    lines += _period_section(coordinator, "30d", seen)
    lines += _period_section(coordinator, "90d", seen)
    lines += _period_section(coordinator, "180d", seen)

    # Trajectory comparison: 30d vs 90d vs 180d (consensus range; a difference of 5 percentage points or more, and
    # enough data in both periods: _tir_comparison)
    comparisons = [
        *_tir_comparison(coordinator, "30d", "90d", always=False, seen=seen)[1:],
        *_tir_comparison(coordinator, "30d", "180d", always=False, seen=seen)[1:],
    ]

    if comparisons:
        lines += ["", f"**{t('notify.monthly.comparison')}**", *comparisons]

    return _with_statement(lines)


async def async_send_monthly_summary(hass: HomeAssistant, coordinator: GlucoCraftCoordinator) -> None:
    """Send the monthly summary as a persistent notification (on the weekly day in ISO weeks 4, 8, ... 52, before the
    weekly and the daily summary)."""
    await _send_summary(hass, coordinator, "monthly", _build_monthly_summary)


async def async_send_weekly_summary(hass: HomeAssistant, coordinator: GlucoCraftCoordinator) -> None:
    """Send the weekly summary as a persistent notification (on the weekly day, before the daily summary, so that the
    notification list shows the weekly summary first)."""
    await _send_summary(hass, coordinator, "weekly", _build_weekly_summary)


def async_setup_daily_notification(hass: HomeAssistant, coordinator: GlucoCraftCoordinator, hour: int, minute: int):
    """Schedule the summaries at the notification time (hour, minute; local time).

    Each day at that time: the daily summary while it is on; on the weekly day the weekly summary while it is on, and
    in every fourth calendar week the monthly summary with it. The two switches and the weekly day are the entry's
    settings (settings.setting); either switch works without the other.

    Returns the unsubscribe callable from async_track_time_change so the caller can register it for cleanup on
    config entry unload.
    """
    config = coordinator.settings
    daily_enabled = setting(config, CONF_NOTIFY_ENABLED)
    weekly_enabled = setting(config, CONF_NOTIFY_WEEKLY_ENABLED)
    weekly_day = setting(config, CONF_NOTIFY_WEEKLY_DAY)

    @callback
    def _send_notification(now: Any) -> None:
        """Send the summaries that are due.

        Order on the weekly day: monthly (ISO weeks 4, 8, ... 52), weekly, daily, so that the notification list shows
        the most comprehensive (monthly) at the top and the most recent (daily) below. Each is sent on its own: one
        that fails is logged with its error, and the next ones are still sent.
        """

        async def _fire_summaries():
            is_weekly_day = weekly_enabled and now.weekday() == weekly_day
            # The monthly summary goes out on the weekly day when the ISO week number is a multiple of 4
            # (weeks 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52: about 13 a year)
            is_monthly_day = is_weekly_day and (now.isocalendar()[1] % 4 == 0)

            due = (
                ("monthly", is_monthly_day, async_send_monthly_summary),
                ("weekly", is_weekly_day, async_send_weekly_summary),
                ("daily", daily_enabled, async_send_daily_summary),
            )
            for kind, is_due, send in due:
                if not is_due:
                    continue
                try:
                    await send(hass, coordinator)
                except Exception:  # one failing summary must not stop the others; logged with its error
                    _LOGGER.warning("GlucoCraft: the %s summary could not be sent", kind, exc_info=True)

        hass.async_create_task(_fire_summaries())

    unsub = async_track_time_change(
        hass,
        _send_notification,
        hour=hour,
        minute=minute,
        second=0,
    )

    _LOGGER.info(
        "Summaries scheduled for %02d:%02d (daily %s; weekly %s, on day %d)",
        hour,
        minute,
        "on" if daily_enabled else "off",
        "on" if weekly_enabled else "off",
        weekly_day,
    )

    return unsub


# ---- the no-data notice (Configure > Notifications, off by default). A status notice about GlucoCraft itself: it
# depends only on the time since the last reading and the no-current-data flag, never on a glucose value, a range or a
# trend. One notice per outage (an outage lasts while the flag is on): created once the last reading is as old as the
# delay chosen, "Notify after", and never before the flag is on, so never earlier than the no-current-data limit;
# removed when the flag goes off (a current reading arrived). No repeats and no message when the readings return.


# The no-data notice also runs during a warm-up, from this long after the warm-up began (GlucoCraft rule): a warm-up
# usually takes far less, so a sensor that has not started is not hidden behind "Sensor warming up"
WARMUP_NOTICE_SECONDS = 3 * 3600


def long_warmup(c: GlucoCraftCoordinator, now: float) -> bool:
    """True while a warm-up has lasted WARMUP_NOTICE_SECONDS or more (the coordinator ends it after 24 hours)."""
    return c.sensor_warming_up and c.warmup_started_at > 0 and now - c.warmup_started_at >= WARMUP_NOTICE_SECONDS


def no_data_notice_due(c: GlucoCraftCoordinator, now: float) -> bool:
    """True while the no-data notice is due: there is no current data, there is a last reading, and it is at least
    the notice delay old (a reading later than the clock never is). During a warm-up only from WARMUP_NOTICE_SECONDS
    after it began (long_warmup), then also without any reading."""
    ts = c.last_reading_ts or 0
    delay = setting(c.settings, CONF_NO_DATA_NOTICE_DELAY)
    if c.sensor_warming_up:
        return c.no_current_data and long_warmup(c, now) and (ts <= 0 or now - ts >= delay * 60)
    return c.no_current_data and ts > 0 and now - ts >= delay * 60


def no_data_notice_message(c: GlucoCraftCoordinator, now: float) -> str:
    """The notice's text: "GlucoCraft has received no readings since 1:02 AM." (a notice about GlucoCraft's data, so
    nothing is added after it); during a long warm-up: "The sensor warm-up began at 1:02 AM and no reading has arrived
    since. ..." (notify.no_data.warm_up)."""
    if c.sensor_warming_up:
        return t("notify.no_data.warm_up", time=_reading_time(c, c.warmup_started_at, now))
    return t("notify.no_data.message", time=_reading_time(c, c.last_reading_ts, now))


def _outage_key(c: GlucoCraftCoordinator) -> int:
    """The time that names the outage whose notice was sent (no_data_notice_for): the last reading's time, or during
    a warm-up the time the warm-up began (the warm-up's notice is a notice of its own)."""
    return c.warmup_started_at if c.sensor_warming_up else c.last_reading_ts


class NoDataNotice:
    """The optional no-data notice of one entry: the Home Assistant notice (setting no_data_notice) and, independently,
    the message to the phones (the phone type no_data_notice), each only where it is chosen.

    Checked whenever the coordinator updates (every minute and with every reading). The outage whose notice was sent is
    the coordinator's no_data_notice_for (the time of its last reading), saved across restarts: after a restart during
    the same outage the phones get nothing again, and the Home Assistant notice, which a restart removes, is shown
    again.
    """

    def __init__(self, hass: HomeAssistant, coordinator: GlucoCraftCoordinator) -> None:
        """Read the entry's settings (a change reloads the entry)."""
        self._hass = hass
        self._c = coordinator
        self._ha = setting(coordinator.settings, CONF_NO_DATA_NOTICE)
        self._phone = "no_data_notice" in setting(coordinator.settings, CONF_PHONE_TYPES)
        self._shown = False  # the Home Assistant notice exists in this run
        if not self._ha:
            persistent_notification.async_dismiss(hass, NO_DATA_NOTICE_ID)  # turned off: an open notice goes

    @callback
    def async_check(self, now: float | None = None) -> None:
        """Create the notice once per outage when it is due; remove it when the flag goes off."""
        c = self._c
        now = time.time() if now is None else now
        if not c.no_current_data:  # a current reading arrived: the outage, if any, is over
            if c.no_data_notice_for or self._shown:
                c.no_data_notice_for = 0
                c.schedule_state_save()
                persistent_notification.async_dismiss(self._hass, NO_DATA_NOTICE_ID)
                self._shown = False
            return
        # during a warm-up only from 3 hours after it began (no_data_notice_due): before that, no readings are expected
        if not (self._ha or self._phone) or not no_data_notice_due(c, now):
            return
        key = _outage_key(c)
        new_outage = not c.no_data_notice_for or (c.sensor_warming_up and c.no_data_notice_for != key)
        if not new_outage and (self._shown or not self._ha):
            return  # this outage's notice exists (or went to the phones only): nothing is repeated
        title = t("notify.no_data.title")
        message = no_data_notice_message(c, now)
        if self._ha:
            persistent_notification.async_create(self._hass, message, title=title, notification_id=NO_DATA_NOTICE_ID)
            self._shown = True
        if new_outage:
            c.no_data_notice_for = key
            c.schedule_state_save()
            if self._phone:
                self._hass.async_create_task(
                    async_forward_to_phones(self._hass, c.settings, "no_data_notice", title, message)
                )
            _LOGGER.info("GlucoCraft no-data notice created")


def async_setup_no_data_notice(hass: HomeAssistant, coordinator: GlucoCraftCoordinator) -> Callable[[], None]:
    """Start the no-data notice: checked on every coordinator update. Returns the function that stops it."""
    notice = NoDataNotice(hass, coordinator)
    return coordinator.async_add_listener(notice.async_check)
