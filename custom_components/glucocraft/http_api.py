# License: AGPL-3.0-only (LICENSE).
"""HTTP API of GlucoCraft. Every view requires a Home Assistant login; every answer is 404 while GlucoCraft is not
loaded. The endpoints (docs/ENTITIES_AND_API.md lists every key):

  GET /api/glucocraft/binary?file=backfill|delta   the GLU2 v3 files (panel_feed.build_glu2), mg/dL: the display
                                                   card's data; 503 without readings
  GET /api/glucocraft/data?period=6h|24h|...       JSON in the display unit: the last reading and its time, the
                                                   ranges, the period's statistics and readings
                                                   (_downsample_readings), and the stretches of readings at the
                                                   sensor's limit (limit_runs); &daily=N (1 to 200) adds the mean, the
                                                   lowest and the highest value of each of the last N local days
                                                   (report.daily_values, the Custom Ranges daily chart)
  GET /api/glucocraft/stats                        JSON: every period's statistics, the ranges and the goals
  GET /api/glucocraft/report?days=14|30|90|180     JSON: the CGM Report's data (report.py), &full=1 for the full
                                                   report; another period: 400 with the refusal
                                                   (report_service.period_refused); without days: 14
  GET /api/glucocraft/report.pdf?days=...          the CGM Report as a PDF, &full=0|1 &color=0|1; 503 without the PDF
                                                   library
full and color also take true / false, on / off, yes / no (report_service.REPORT_FLAG_ON / _OFF); any other value is
refused with 400 (_flags), never read as the default.
The two report endpoints take &include_custom_appendix=1 and &include_research_appendix=1 (400 for the custom
appendix while Custom ranges and goals is off). The wall panels get the same GLU2 files from the local-only webhook
(panel_feed.py), so the card and the panels show the same numbers.

The glucose values of /data and /stats are unrounded (_glucose), as the entities' attributes: a chart of the user's
own rounds each value once, as it shows it. A period without enough data (statistics.enough_data: sensor active below
70% of the period) carries enough_data false and every statistic value null (_period_json).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.http import KEY_HASS
from homeassistant.util import dt as dt_util

from .const import DEFAULT_RETENTION_DAYS, FALLBACK_INTERVAL_SECONDS, PERIOD_SECONDS, STAT_PERIODS
from .coordinator import GlucoCraftCoordinator, loaded_coordinator
from .gaps import gap_threshold
from .i18n import t
from .limit_runs import limit_runs
from .panel_feed import build_glu2_file
from .report import daily_values
from .report_service import REPORT_FLAG_OFF, REPORT_FLAG_ON, period_refused, report_options
from .statistics import PeriodStats, enough_data, gmi_shown
from .units import MGDL_PER_MMOLL, is_mmol

_LOGGER = logging.getLogger(__name__)

# From 7 days on, /data sends time slots instead of readings: a chart of a long period has fewer pixel columns than
# readings (180 days of 1-minute readings are about 260,000), so one slot per column shows the same picture
_DOWNSAMPLE_THRESHOLD = 7 * 86400


def _glucose(value: float | None, unit: str) -> float | None:
    """Return a glucose value in the display unit, unrounded: mg/dL as stored or calculated, mmol/L divided by the one
    factor (units.MGDL_PER_MMOLL). The ranges are converted the same way, so that a reading and a limit compare in the
    display unit exactly as in mg/dL. None stays None."""
    if value is None:
        return None
    return value / MGDL_PER_MMOLL if is_mmol(unit) else float(value)


def _downsample_readings(readings: list, period_seconds: int, unit: str, max_points: int = 400) -> list[dict]:
    """Return the readings of a chart in the display unit (unrounded, _glucose).

    Below 7 days, or with max_points readings or fewer: every reading. Otherwise max_points time slots of equal length,
    each with the time of its first reading, the mean, and the lowest and the highest reading, so that a chart draws
    the mean as a line and the range as a band; a slot without readings is left out. A reading at the sensor's limit
    (LO / HI) has no value, so it is left out: the chart breaks there and marks the stretch from limit_runs.
    """
    readings = [r for r in readings if r.limit is None]
    if not readings:
        return []

    if period_seconds < _DOWNSAMPLE_THRESHOLD or len(readings) <= max_points:
        return [{"t": r.timestamp, "r": _glucose(r.raw, unit)} for r in readings]

    start_ts = readings[0].timestamp
    end_ts = readings[-1].timestamp
    time_range = end_ts - start_ts
    if time_range <= 0:
        return []

    slot_size = time_range / max_points

    # min / max: None until a slot has a reading
    slots: list[dict[str, Any]] = [{"sum": 0, "count": 0, "min": None, "max": None, "ts": 0} for _ in range(max_points)]

    for r in readings:
        slot_idx = int((r.timestamp - start_ts) / slot_size)
        if slot_idx >= max_points:
            slot_idx = max_points - 1
        slot_idx = max(slot_idx, 0)

        s = slots[slot_idx]
        s["sum"] += r.raw
        s["count"] += 1
        s["min"] = r.raw if s["min"] is None else min(s["min"], r.raw)
        s["max"] = r.raw if s["max"] is None else max(s["max"], r.raw)
        if s["ts"] == 0:
            s["ts"] = r.timestamp

    return [
        {
            "t": s["ts"] if s["ts"] > 0 else int(start_ts + i * slot_size),
            "r": _glucose(s["sum"] / s["count"], unit),
            "min": _glucose(s["min"], unit),
            "max": _glucose(s["max"], unit),
        }
        for i, s in enumerate(slots)
        if s["count"] > 0
    ]


def _readings_since(coordinator: GlucoCraftCoordinator, since: int) -> list:
    """The stored readings with a time >= since, oldest first: a slice found by bisection (storage.window_indices),
    not a scan of the whole store. Readings newer than the clock are included as well (the end stays open)."""
    readings = coordinator.storage.readings  # loads the store if needed
    return readings[coordinator.storage.window_indices(since)[0] :]


def _readings_and_intervals(coordinator: GlucoCraftCoordinator, since: int) -> tuple[list, list[int]]:
    """The stored readings from the last one before `since` on, and their reading intervals (storage.reading_intervals),
    copied; under the coordinator's lock (the one reading before `since` lets a stretch at the sensor's limit that began
    before it continue into the period)."""
    store = coordinator.storage
    i = max(0, store.window_indices(since)[0] - 1)
    return store.readings[i:], store.reading_intervals(coordinator.detected_interval_seconds)[i:]


def _chart_readings(
    coordinator: GlucoCraftCoordinator, since: int, period_seconds: int, unit: str
) -> tuple[list[dict], list[list]]:
    """The readings of /data and the period's stretches of readings at the sensor's limit (executor): the slice copied
    under the coordinator's lock, then reduced to the chart's slots outside it (_downsample_readings), so that neither
    step runs in the event loop and the lock is held only for the copy. The stretches: [start, end, "low" | "high"]
    (limit_runs.limit_runs), cut at the period's start."""
    readings, intervals = coordinator.locked(_readings_and_intervals, coordinator, since)
    runs = [[start, end, kind] for start, end, kind in limit_runs(readings, intervals, since)]
    return _downsample_readings([r for r in readings if r.timestamp >= since], period_seconds, unit), runs


def _daily(coordinator: GlucoCraftCoordinator, days: int, now: int, unit: str) -> list[dict]:
    """The daily values of /data?daily=N (executor): the readings of the N days and the day before them copied under
    the coordinator's lock, the days calculated outside it (report.daily_values), in Home Assistant's time zone; the
    values in the display unit, unrounded (_glucose)."""
    tz = dt_util.get_default_time_zone()
    first = datetime.fromtimestamp(now, tz).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
    readings = coordinator.locked(_readings_since, coordinator, int(first.timestamp()))
    interval = coordinator.detected_interval_seconds or FALLBACK_INTERVAL_SECONDS
    return [
        {**d, **{k: _glucose(d[k], unit) for k in ("mean", "min", "max")}}
        for d in daily_values(readings, interval, tz, days, now)
    ]


def _ranges(coordinator: GlucoCraftCoordinator, unit: str, is_default: bool) -> dict[str, Any]:
    """Return the ranges in the display unit, keys in the consensus terms (as the Configuration sensor's ranges)."""
    c = coordinator
    return {
        "very_low": _glucose(c.range_critical_low, unit),
        "low": _glucose(c.range_low, unit),
        "tight_low": _glucose(c.range_ideal_low, unit),
        "tight_high": _glucose(c.range_ideal_high, unit),
        "high": _glucose(c.range_high, unit),
        "very_high": _glucose(c.range_critical_high, unit),
        "unit": unit,
        "is_default": is_default,
    }


def _period_json(stats: PeriodStats, seconds: int, unit: str) -> dict[str, Any]:
    """Return one period's statistics, keys in the consensus terms.

    The keys follow the period sensors' names: time in range, time in tight range, time low / very low, time high /
    very high. The glucose values, the CV and the percentages are unrounded (statistics.PeriodStats, _glucose): a card
    rounds each once, as shown. Without enough data (statistics.enough_data: sensor active below 70% of the period,
    compared before rounding) every statistic value is null and enough_data false; sensor_active (unrounded %),
    period_seconds and covered_seconds let a page work out how much more data the period needs. The reading count and
    the readings at the sensor's limit are stated either way.
    """
    values = {
        "mean": _glucose(stats.mean, unit),
        "std_dev": _glucose(stats.std_dev, unit),
        "cv": stats.cv,
        "gmi": gmi_shown(stats, seconds),  # >= 14 days, >= 70 % data, as every display
        "min": _glucose(stats.min_value, unit),
        "max": _glucose(stats.max_value, unit),
        "time_in_range": stats.pct_time_in_target_range,
        "time_in_tight_range": stats.pct_time_in_ideal_range,
        "time_low": stats.time_low,
        "time_high": stats.time_high,
        "time_very_low": stats.time_critical_low,
        "time_very_high": stats.time_critical_high,
        # all time below the low limit / above the high limit (level 1 and level 2 together, unrounded)
        "time_below_low": stats.time_below_low,
        "time_above_high": stats.time_above_high,
    }
    enough = enough_data(stats)
    return {
        "enough_data": enough,
        **(values if enough else dict.fromkeys(values)),
        "sensor_active": stats.data_availability,
        "period_seconds": seconds,
        "covered_seconds": stats.covered_seconds,
        "reading_count": stats.reading_count,
        "data_availability": stats.data_availability,
        # readings at the sensor's limit (LO / HI): they have no value, so they are left out of every value above but
        # the times in the bands; stated also without enough data
        "limit_readings": stats.limit_readings,
        "limit_seconds": stats.limit_seconds,
    }


def _coordinator_or_404(request: web.Request) -> tuple[HomeAssistant, GlucoCraftCoordinator | None, web.Response]:
    """Return (hass, the loaded entry's coordinator, the 404 answer to give without one). There is one GlucoCraft
    entry (single_config_entry); the coordinator is None while it is not loaded."""
    hass: HomeAssistant = request.app[KEY_HASS]
    return hass, loaded_coordinator(hass), web.Response(status=404, text=t("api.no_instance"))


class GlucoCraftBinaryView(HomeAssistantView):
    """GLU2 v3 files for the dashboard's display card (Home Assistant login): ?file=backfill (default) or ?file=delta.

    The same files the wall panels get from the local-only webhook (panel_feed.py), so card and panels show the same
    numbers.
    """

    url = "/api/glucocraft/binary"
    name = "api:glucocraft:binary"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """Answer with the requested GLU2 file, built under the coordinator's lock."""
        hass, coordinator, missing = _coordinator_or_404(request)
        if coordinator is None:
            return missing

        which = request.query.get("file", "backfill")
        if which not in ("backfill", "delta"):
            return web.Response(status=404)
        # from a copy of the readings made under the coordinator's lock (panel_feed.build_glu2_file), so a reading
        # arriving meanwhile cannot change the data mid-build, and readings are stored while the file is built
        binary_data = await hass.async_add_executor_job(build_glu2_file, coordinator, which == "delta")
        if binary_data is None:
            return web.Response(status=503, text=t("api.no_readings"))

        # aiohttp sets Content-Length from the body (as the panels' webhook, panel_feed.py)
        return web.Response(
            body=binary_data, content_type="application/octet-stream", headers={"Cache-Control": "no-cache"}
        )


class GlucoCraftDataView(HomeAssistantView):
    """One period's current values, ranges, statistics and readings as JSON, for a chart of the user's own (the
    GlucoCraft cards read the GLU2 files)."""

    url = "/api/glucocraft/data"
    name = "api:glucocraft:data"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """Answer for ?period= (6h when absent or unknown, so that a chart always gets data)."""
        hass, coordinator, missing = _coordinator_or_404(request)
        if coordinator is None:
            return missing

        period = request.query.get("period", "6h")
        if period not in PERIOD_SECONDS:
            period = "6h"
        period_seconds = PERIOD_SECONDS[period]

        now = int(time.time())
        since = now - period_seconds
        unit = coordinator.display_unit
        readings, runs = await hass.async_add_executor_job(_chart_readings, coordinator, since, period_seconds, unit)

        # Decided on the stored mg/dL values before any unit conversion (coordinator.uses_custom_ranges); the cards
        # use this flag instead of comparing display-unit values with mg/dL constants
        is_default_ranges = not coordinator.uses_custom_ranges()

        iv = coordinator.reading_interval()
        data: dict[str, Any] = {  # glucose values in the display unit
            "period": period,
            "unit": unit,
            # the last reading and its measurement time (epoch seconds), current or not (no_current_data)
            "current": _glucose(coordinator.measured_glucose, unit),
            "last_reading_ts": coordinator.last_reading_ts or None,
            "trend": coordinator.trend_description,
            "trend_arrow": coordinator.trend_arrow,
            "no_current_data": coordinator.no_current_data,
            "detected_interval": coordinator.detected_interval_seconds,
            # LO / HI and the gap rule (as the Last glucose reading and Configuration sensors)
            "limit": coordinator.current_limit if coordinator.current_glucose is not None else None,
            "limit_text": coordinator.current_limit_text,
            "reading_interval_seconds": iv,
            "gap_threshold_seconds": gap_threshold(iv),
            "ranges": _ranges(coordinator, unit, is_default_ranges),
            "readings": readings,
            # the stretches of readings at the sensor's limit in the period, [start, end, "low" | "high"] (epoch
            # seconds): the chart marks them at its bottom (LO) or top (HI) edge; they have no value
            "limit_runs": runs,
        }

        stats = coordinator.period_stats.get(period)  # none: a period without readings has no "stats" key
        if stats:
            data["stats"] = _period_json(stats, period_seconds, unit)
        # the daily values of the last N local days (1 to the retention); another value adds nothing. isascii() first:
        # isdigit() is also True for digits such as "²" that int() cannot read
        days = request.query.get("daily", "")
        if days.isascii() and days.isdigit() and 1 <= int(days) <= DEFAULT_RETENTION_DAYS:
            data["daily"] = await hass.async_add_executor_job(_daily, coordinator, int(days), now, unit)

        return self.json(data)


class GlucoCraftStatsView(HomeAssistantView):
    """Every period's statistics, the ranges and the goals as JSON, keyed as the Configuration sensor's attributes."""

    url = "/api/glucocraft/stats"
    name = "api:glucocraft:stats"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """Answer with the periods that have readings (a period without any is left out)."""
        _hass, coordinator, missing = _coordinator_or_404(request)
        if coordinator is None:
            return missing
        unit = coordinator.display_unit  # glucose values in the display unit

        all_stats: dict[str, Any] = {"unit": unit}
        for label, seconds in STAT_PERIODS:
            stats = coordinator.period_stats.get(label)
            if stats:
                all_stats[label] = _period_json(stats, seconds, unit)

        is_default_ranges = not coordinator.uses_custom_ranges()

        return self.json(
            {
                # the last reading in the display unit, as the statistics, and its measurement time (epoch seconds)
                "current": _glucose(coordinator.measured_glucose, unit),
                "last_reading_ts": coordinator.last_reading_ts or None,
                "detected_interval": coordinator.detected_interval_seconds,
                "reading_count": coordinator.storage.reading_count,
                "unit": unit,
                "ranges": _ranges(coordinator, unit, is_default_ranges),
                # the keys of the Configuration sensor's goals (one set of names everywhere)
                "goals": {
                    "time_in_range": coordinator.goal_time_in_target_range,
                    "time_in_tight_range": coordinator.goal_time_in_ideal_range,
                    "time_low": coordinator.goal_tbr,
                    "time_very_low": coordinator.goal_tbr_critical,
                    "time_high": coordinator.goal_tar,
                    "time_very_high": coordinator.goal_tar_critical,
                    "cv": coordinator.goal_cv,
                    "gmi": coordinator.goal_gmi,
                },
                "periods": all_stats,
            }
        )


class GlucoCraftReportView(HomeAssistantView):
    """Serve the CGM Report's data as JSON.

    ?days=14|30|90|180, &full=1 for the full report, &include_custom_appendix=1 and &include_research_appendix=1 for
    the optional appendices (off by default). The custom appendix exists only while Custom ranges and goals is on
    (otherwise 400, _custom_appendix_refused).
    """

    url = "/api/glucocraft/report"
    name = "api:glucocraft:report"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """Answer with the report's data; a period that does not exist: 400 with the refusal (_report_query)."""
        _hass, coordinator, missing = _coordinator_or_404(request)
        if coordinator is None:
            return missing
        days, options, refused = _report_query(request, coordinator)
        if refused is not None:
            return refused
        flags, refused = _flags(request, {"full": False})
        if refused is not None:
            return refused
        data = await coordinator.reports.async_data(days, flags["full"], *options)
        return self.json(data, headers={"Cache-Control": "no-cache"})


class GlucoCraftReportPdfView(HomeAssistantView):
    """Serve the CGM Report as a PDF.

    ?days=14|30|90|180&full=0|1&color=0|1, &include_custom_appendix=1 and &include_research_appendix=1 for the
    optional appendices (the custom appendix only while Custom ranges and goals is on). A copy is kept in
    <config>/glucocraft_reports/ (the last 12).
    """

    url = "/api/glucocraft/report.pdf"
    name = "api:glucocraft:report_pdf"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """Answer with the PDF as an attachment; 503 when the PDF library cannot be loaded."""
        _hass, coordinator, missing = _coordinator_or_404(request)
        if coordinator is None:
            return missing
        days, options, refused = _report_query(request, coordinator)
        if refused is not None:
            return refused
        flags, refused = _flags(request, {"full": False, "color": True})
        if refused is not None:
            return refused
        try:
            # a copy that could not be kept is logged by async_pdf; the download does not depend on it
            pdf, _pages, name, _not_kept = await coordinator.reports.async_pdf(
                days, flags["full"], flags["color"], *options
            )
        except ImportError as err:
            return web.Response(status=503, text=t("api.pdf_unavailable", error=err))
        return web.Response(
            body=pdf,
            content_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-cache"},
        )


def _report_query(
    request: web.Request, coordinator: GlucoCraftCoordinator
) -> tuple[int, tuple[bool, bool], web.Response | None]:
    """Read the parameters both report endpoints share: (days, the appendix options, the 400 answer to give instead or
    None). Without days (or empty): 14, the report page's default. A period the CGM Report does not have (another
    number, or no number) is refused with 400 and the refusal's text (report_service.period_refused), never replaced by
    another period without a word."""
    raw = (request.query.get("days") or "14").strip()  # an empty value is no value: 14
    try:
        days = int(raw)
    except ValueError:
        days = None
    refused = period_refused(days if days is not None else raw)
    if refused is not None:
        return 14, (False, False), web.Response(status=400, text=refused)
    options = report_options(request.query)
    return days, options, _custom_appendix_refused(coordinator, options)


def _flags(request: web.Request, defaults: dict[str, bool]) -> tuple[dict[str, bool], web.Response | None]:
    """Read the on / off parameters of a report endpoint (full, color): (their values, the 400 answer to give instead
    or None). Absent or empty: the default given. On: REPORT_FLAG_ON ("1", "true", "on", "yes"); off: REPORT_FLAG_OFF
    ("0", "false", "off", "no"); any other value is refused with 400 (api.flag_invalid), as a period that does not
    exist, never read as the default without a word."""
    values: dict[str, bool] = {}
    for name, default in defaults.items():
        raw = (request.query.get(name) or "").strip().lower()
        if not raw:
            values[name] = default
        elif raw in REPORT_FLAG_ON:
            values[name] = True
        elif raw in REPORT_FLAG_OFF:
            values[name] = False
        else:
            return values, web.Response(status=400, text=t("api.flag_invalid", name=name))
    return values, None


def _custom_appendix_refused(coordinator: GlucoCraftCoordinator, options: tuple[bool, bool]) -> web.Response | None:
    """A 400 answer when the custom appendix is asked for while Custom ranges and goals is off, else None. The report
    page offers the appendix only while the feature is on; an appendix left out silently would look like a report
    that has it."""
    custom, _research = options
    if custom and not coordinator.custom_ranges_and_goals:
        return web.Response(status=400, text=t("api.custom_appendix_off"))
    return None


def async_register_views(hass: HomeAssistant) -> None:
    """Register the five views (the endpoints of the module docstring)."""
    hass.http.register_view(GlucoCraftBinaryView())
    hass.http.register_view(GlucoCraftDataView())
    hass.http.register_view(GlucoCraftStatsView())
    hass.http.register_view(GlucoCraftReportView())
    hass.http.register_view(GlucoCraftReportPdfView())
