# License: AGPL-3.0-only (LICENSE).
"""GlucoCraft CGM Report service: data for the dashboard, PDF files, printing and laboratory A1C entries.

* the report data: report.build_report over a copy of the coordinator's stored readings (the copy is taken under
  the reading lock, the report is built without it, in the executor), cached until the readings it depends on
  change (report.build_report's reads_until), the local date changes, or the laboratory results, the display unit or
  the regional settings change (with the custom appendix, also the ranges and goals in the settings);
* PDF: report_pdf.render, in the executor; it renders one at a time itself (report_pdf.RENDER_LOCK, its render
  settings are module state); each PDF is also kept in <config>/glucocraft_reports/ (the last 12; not web-reachable);
  a copy that cannot be written is logged as a warning and named in the print notice, and the PDF is still
  downloaded or printed;
* printing (optional): the printer address in GlucoCraft's settings, IPP Print-Job with the PDF;
* laboratory A1C: entered with the actions glucocraft.add_lab_a1c / remove_lab_a1c (in percent or in mmol/mol;
  a value entered in mmol/mol is kept as well, in whole mmol/mol), stored in .storage;
* the two optional appendices (off by default): include_custom_appendix (the ranges and goals in the settings) and
  include_research_appendix (measures defined for research);
* sensor changes and warm-ups for the data quality page: each warm-up GlucoCraft detects (coordinator
  warmup_started_at) is recorded with its end (the first valid reading, or 24 hours after its start when no reading
  measured after it came: coordinator.WARMUP_MAX_SECONDS) in .storage (glucocraft.sensor_events), and
  the history of the Sensor warm-up entity in Home Assistant's recorder fills in the warm-ups from before the record
  began.
"""

from __future__ import annotations

import asyncio
import bisect
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

# report_pdf is imported with this module, which Home Assistant loads in its executor: importing it inside an action
# would compile the large module in the event loop. reportlab itself is still imported only when a PDF is made.
from . import report as R
from . import report_pdf
from .const import (
    CONF_PRINTER,
    COPYRIGHT,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
    FALLBACK_INTERVAL_SECONDS,
    GNU_NOTICE,
    LICENSE_NAME,
    LICENSE_URL,
    REPORT_OPT_CUSTOM_APPENDIX,
    REPORT_OPT_RESEARCH_APPENDIX,
)
from .i18n import t, tp
from .regional import A1C_MMOL_PER_PCT, A1C_PCT_OFFSET, a1c_to_percent
from .settings import setting
from .units import report_threshold_texts, round_half_up

_LOGGER = logging.getLogger(__name__)

# The report periods (days); each one's name is the catalogue's report.period.<days> ("14 Days", ..., "6 Months")
REPORT_DAYS = (14, 30, 90, 180)


def period_label(days: int) -> str:
    """Return the name of a report period: "30 Days", "6 Months"."""
    return t(f"report.period.{days}")


class ReportPeriodError(ValueError):
    """A report period that the CGM Report does not have (not one of REPORT_DAYS); its text is the refusal."""


def period_refused(days) -> str | None:
    """Return the refusal of a report period that does not exist ("This report period is not available: 60 days. ..."),
    or None for one of REPORT_DAYS. A wrong period is refused, never replaced by another one without a word."""
    if isinstance(days, int) and not isinstance(days, bool) and days in REPORT_DAYS:
        return None
    return t("report.error.period", days=days)


def license_texts() -> dict[str, Any]:
    """Return the license notices of the report's last section, the texts the PDF prints (const.py), so that the Report
    page shows the same: the license's name, the copyright line, the GNU notice (its paragraphs) and the address of the
    license."""
    return {"name": LICENSE_NAME, "copyright": COPYRIGHT, "notice": list(GNU_NOTICE), "url": LICENSE_URL}


def print_message(ok: bool, reason: str, pages: int, name: str, not_kept: str | None = None) -> str:
    """Return the result of a print as the notification and the action's reply state it: printed or not, the
    printer's answer, the page count and the kept PDF, or why its copy could not be kept (not_kept: the reason)."""
    key = "report.print.printed" if ok else "report.print.not_printed"
    if not_kept is not None:
        return tp(f"{key}_not_kept", pages, reason=reason, folder=REPORT_DIR, error=not_kept)
    return tp(key, pages, reason=reason, file=name)


REPORT_DIR = "glucocraft_reports"
REPORT_KEEP = 12
REPORT_PREFIX = "glucocraft-cgm-report_"  # the PDF file names: <prefix><creation time>_<days>d_<kind>_<color>.pdf


# The two stores of this module in Home Assistant's .storage folder (one GlucoCraft installation per Home Assistant):
# the laboratory A1C results and the sensor changes and warm-ups. Removing GlucoCraft deletes both.
LAB_STORE_KEY = "glucocraft.lab_a1c"
SENSOR_EVENTS_STORE_KEY = "glucocraft.sensor_events"

# the laboratory A1C a person can enter (percent: 3-20; mmol/mol: the same span, IFCC-NGSP master equation)
LAB_LIMITS = {"percent": (3.0, 20.0), "mmol_mol": (9.0, 195.0)}


def custom_settings(cfg: dict) -> dict:
    """Return the ranges and goals in the settings (coordinator.configuration()) as the custom appendix uses them."""
    return {"ranges": dict(cfg.get("ranges") or {}), "goals": dict(cfg.get("goals") or {})}


def lab_entry(date: str, value: float, unit: str = "percent") -> dict:
    """Return one laboratory result as stored; raise ValueError, with the refusal as its text, for a date, unit or
    value that is not valid.

    a1c in percent (one decimal; two when converted from mmol/mol, so that the value entered is reproduced), and
    a1c_mmol_mol when it was entered in mmol/mol. The date is a real calendar date written YYYY-MM-DD (a date that
    does not exist, such as February 30, is refused).
    """
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):  # the shape first: strptime alone accepts "2026-1-5"
        raise ValueError(t("report.lab.error.date"))
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise ValueError(t("report.lab.error.date")) from None
    if unit not in LAB_LIMITS:
        raise ValueError(t("report.lab.error.unit"))
    lo, hi = LAB_LIMITS[unit]
    v = float(value)
    if not lo <= v <= hi:
        raise ValueError(t("report.lab.error.range", low=f"{lo:g}", high=f"{hi:g}", unit=t(f"report.lab.unit.{unit}")))
    if unit == "mmol_mol":
        mm = int(round_half_up(v))
        return {"date": date, "a1c": round_half_up(a1c_to_percent(mm), 2), "a1c_mmol_mol": mm}
    return {"date": date, "a1c": round_half_up(v, 1)}


class GlucoCraftReports:
    """The CGM Report of one GlucoCraft entry: its data (cached), the PDF, printing and the laboratory results."""

    def __init__(self, hass: HomeAssistant, coordinator) -> None:
        """Prepare the two stores; async_load reads them."""
        self.hass = hass
        self.coordinator = coordinator
        self._store: Store = Store(hass, 1, LAB_STORE_KEY)
        self._lab: list[dict] = []
        # slot (days, full, custom, research) -> ((key, readings_key), reads_until, report.build_report data):
        # key = (base, custom) from _key, readings_key from _readings_key; v[0][0][0] is an entry's base
        self._cache: dict[tuple, tuple[tuple, int | None, dict]] = {}
        self._build_lock = asyncio.Lock()  # one build at a time: a second request for the same report waits for it
        # sensor changes and warm-ups: [{"start": epoch s, "end": epoch s | None}]
        self._events_store: Store = Store(hass, 1, SENSOR_EVENTS_STORE_KEY)
        self._events: list[dict] = []

    async def async_load(self) -> None:
        """Load the laboratory results and the sensor events, and start recording warm-ups."""
        data = await self._store.async_load()
        self._lab = list((data or {}).get("results", []))
        ev = await self._events_store.async_load()
        self._events = [e for e in (ev or {}).get("events", []) if isinstance(e, dict) and e.get("start")]
        # every coordinator update: a warm-up that began or ended is recorded; the listener lives as long as the
        # coordinator, which is discarded when the entry unloads
        self.coordinator.async_add_listener(self.track_warmup)

    @property
    def lab_results(self) -> list[dict]:
        """The laboratory A1C results as stored (copies), oldest first: {"date", "a1c"[, "a1c_mmol_mol"]}."""
        return [dict(r) for r in self._lab]

    # ------------------------------------------------------------------ sensor changes and warm-ups
    def track_warmup(self) -> None:
        """Record a warm-up when the coordinator reports one, and its end when it is over.

        The start is warmup_started_at (epoch s), the end the first valid reading measured after it (last_reading_ts).
        Without such a reading the coordinator ended the warm-up by its 24-hour rule (_end_overlong_warmup): the end is
        then the start plus coordinator.WARMUP_MAX_SECONDS, and the event carries "limit": True (the data quality page
        names the cause). Events are kept for the retention plus a day.
        """
        started = int(self.coordinator.warmup_started_at or 0)
        changed = False
        if started and not any(e.get("start") == started for e in self._events):
            self._events.append({"start": started, "end": None})
            changed = True
        elif not started and self._events and self._events[-1].get("end") is None:
            event = self._events[-1]
            last = int(self.coordinator.last_reading_ts or 0)
            if last > event["start"]:
                event["end"] = last
            else:
                event["end"] = event["start"] + int(self.coordinator.WARMUP_MAX_SECONDS)
                event["limit"] = True
            changed = True
        if changed:
            keep = DEFAULT_RETENTION_DAYS + 1
            cutoff = time.time() - keep * 86400
            self._events = [e for e in self._events if e["start"] >= cutoff]
            self._cache.clear()
            self.hass.async_create_task(self._events_store.async_save({"events": list(self._events)}))

    async def _recorder_warmups(self, days: int) -> list[dict]:
        """Return the warm-ups in the history of the Sensor warm-up entity (Home Assistant's recorder).

        As far back as the recorder keeps history; [] when the recorder or the entity is not available, or when the
        query fails (logged at debug: the report then shows GlucoCraft's own record of warm-ups only).
        """
        try:
            # the recorder is optional (not a dependency of GlucoCraft)
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import state_changes_during_period
        except ImportError:
            return []
        try:
            entity_id = er.async_get(self.hass).async_get_entity_id(
                "binary_sensor", DOMAIN, f"{self.coordinator.entry_id}_sensor_warmup"
            )
            if not entity_id:
                return []
            end = dt_util.utcnow()
            start = end - timedelta(days=days + 1)
            hist = await get_instance(self.hass).async_add_executor_job(
                partial(state_changes_during_period, self.hass, start, end, entity_id, no_attributes=True)
            )
            out, on = [], None
            for st in (hist or {}).get(entity_id, []):
                changed = int(st.last_changed.timestamp())
                if st.state == "on" and on is None:
                    on = changed
                elif st.state == "off" and on is not None:
                    out.append({"start": on, "end": changed})
                    on = None
            if on is not None:
                out.append({"start": on, "end": None})
            return out
        except Exception as err:  # noqa: BLE001 - the recorder's warm-ups are optional for the report
            # a changed recorder API or a recorder not set up must not stop the report; the log says why they
            # are missing
            _LOGGER.debug("GlucoCraft report: no warm-ups from the recorder (%s: %s)", type(err).__name__, err)
            return []

    async def _sensor_events(self, days: int) -> list[dict]:
        """Return GlucoCraft's own record of warm-ups, completed with the recorder's warm-ups it does not contain.

        A recorder warm-up counts as new when its start is more than 10 minutes from every recorded start.
        """
        own = [dict(e) for e in self._events]
        for e in await self._recorder_warmups(days):
            if not any(abs(e["start"] - o["start"]) <= 600 for o in own):
                own.append(e)
        return sorted(own, key=lambda e: e["start"])

    # ------------------------------------------------------------------ data
    def _key(self, slot: tuple, cfg: dict) -> tuple:
        """Return the part of the cache key that does not depend on the readings: (base, custom). base: the local date
        (the report ends at the last midnight), the laboratory results, the display unit and the regional settings,
        the same for every slot. custom: for a slot with the custom appendix, the ranges and goals in the settings,
        which change without a reload (a sensor limit learned from the source changes the ranges in use); else None."""
        base = (
            dt_util.now().date().isoformat(),
            tuple(tuple(sorted(r.items())) for r in self._lab),
            self.coordinator.display_unit,
            tuple(self.coordinator.regional.as_dict().values()),
        )
        return base, json.dumps(custom_settings(cfg), sort_keys=True, default=str) if slot[2] else None

    @staticmethod
    def _readings_key(readings: list, until: int | None, edits: int) -> tuple:
        """Return the readings' part of the cache key: the readings up to `until` (report.build_report's reads_until),
        as their count and newest time, and the storage's count of edits (storage.edits: readings inserted before the
        newest, replaced or removed), so that a reading added, replaced or removed before `until` (a late fill by a
        second source, the primary's copy of a measurement replacing the second source's, the daily purge) changes
        the key and a new reading after it does not; with until None every reading counts."""
        if until is None:
            return len(readings), readings[-1].timestamp if readings else 0, edits
        n = bisect.bisect_right(readings, until, key=lambda r: r.timestamp)
        return n, readings[n - 1].timestamp if n else 0, edits

    def _cached(self, slot: tuple, cfg: dict) -> dict | None:
        """Return the cached report data of a slot while its key holds, else None (cfg: the configuration in use)."""
        hit = self._cache.get(slot)
        if not hit:
            return None
        key, until, data = hit
        try:  # read in the event loop while a reading may be stored: a list changed during the search is a miss
            storage = self.coordinator.storage
            now_key = (self._key(slot, cfg), self._readings_key(storage.readings, until, storage.edits))
        except (IndexError, AttributeError):
            return None
        return data if now_key == key else None

    async def async_data(
        self, days: int, full: bool = False, custom: bool = False, research: bool = False
    ) -> dict[str, Any]:
        """Return the report data (cached); custom / research: the optional appendices, off by default. The values
        that are not part of the report's calculation (the creation time, the source names, the printer, the settings
        the page and the PDF format with) are current on every call. A period that does not exist raises
        ReportPeriodError with its refusal (period_refused)."""
        refused = period_refused(days)
        if refused:
            raise ReportPeriodError(refused)
        slot = (days, bool(full), bool(custom), bool(research))
        c = self.coordinator
        rg = c.regional  # time and date formats, decimal separator, A1C unit, paper size
        cfg = c.configuration()
        data = self._cached(slot, cfg)
        if data is None:
            async with self._build_lock:
                data = self._cached(slot, cfg)  # built meanwhile by the request this one waited for
                if data is None:
                    data = await self._build(slot, rg, cfg)
        second = cfg.get("secondary_source")
        now = dt_util.now()
        return {
            **data,
            "label": period_label(days),
            # the glucose sources by their names (the entity's name, else its entity id), the primary first: the page
            # header and About This Report name them; whether the second source carries its own measurement times
            # (without them it is used only after the primary has been silent, which About This Report states)
            "sources": [s["name"] for s in (cfg.get("primary_source"), second) if s],
            "second_source_timed": c.has_measurement_time(second["entity"]) if second else None,
            "created": rg.stamp_year(now),
            # the creation time to the minute (epoch seconds, from the system clock as every other epoch time here):
            # the PDF's creation date, so that two renders of the same data in the same minute are the same file
            # (report_pdf.render)
            "created_ts": int(time.time()) // 60 * 60,
            "printer": bool(setting(c.settings, CONF_PRINTER)),
            # values stay mg/dL; the page and the PDF show them in this unit
            "unit": c.display_unit,
            # the thresholds the fixed texts name ({t70}, {t70_180}, {unit}) in this unit: the page fills them from
            # this one table, as the PDF does (units.REPORT_THRESHOLDS)
            "thresholds": report_threshold_texts(c.display_unit),
            # the page and the PDF format with these: time and date formats, first day of the week, decimal
            # separator, A1C / GMI unit, paper size
            **rg.as_dict(),
            "integration_version": c.integration_version or "",
            # the laboratory A1C the page's entry accepts, per unit: the limits of the action add_lab_a1c
            "lab_limits": {unit: list(limits) for unit, limits in LAB_LIMITS.items()},
            # the master equation's constants that About This Report names (regional.py; Hoelzel et al., 2004), so that
            # the page writes the numbers the PDF writes
            "ifcc": {"factor": A1C_MMOL_PER_PCT, "offset": A1C_PCT_OFFSET},
            # the license notices of the last section, as the PDF prints them (const.py)
            "license": license_texts(),
        }

    async def _build(self, slot: tuple, rg, cfg: dict) -> dict:
        """Build the report data of a slot (days, full, custom, research) and cache it.

        The readings and their reading intervals are copied under the coordinator's reading lock; the report is then
        built from the copy without it (report.build_report reads only the list it is given), so that new readings
        are stored meanwhile.
        """
        days, full, custom, research = slot
        key = self._key(slot, cfg)
        tz = dt_util.get_time_zone(self.hass.config.time_zone) or dt_util.DEFAULT_TIME_ZONE
        lab = [dict(r) for r in self._lab]
        # the ranges and goals in the settings, for the appendix "Custom ranges and goals" only
        settings = custom_settings(cfg) if custom else None
        events = await self._sensor_events(days)
        c = self.coordinator
        interval = int(c.detected_interval_seconds or FALLBACK_INTERVAL_SECONDS)

        def copy() -> tuple[list, list, int]:
            """Copy the readings and the gap rule's reading intervals (cached in the storage, aligned with them), with
            the storage's count of edits (the cache key)."""
            return list(c.storage.readings), list(c.storage.reading_intervals(interval)), c.storage.edits

        readings, iv, edits = await self.hass.async_add_executor_job(c.locked, copy)
        data = await self.hass.async_add_executor_job(
            partial(
                R.build_report,
                readings,
                interval,
                tz,
                days,
                full,
                lab,
                regional=rg,
                intervals=iv,
                retention_days=DEFAULT_RETENTION_DAYS,
                custom=settings,
                research=research,
                sensor_events=events,
            )
        )
        until = data.pop("reads_until", None)
        # keep the other slots of the same base (day, laboratory results, unit and regional settings: v[0][0][0])
        self._cache = {k: v for k, v in self._cache.items() if v[0][0][0] == key[0]}
        self._cache[slot] = ((key, self._readings_key(readings, until, edits)), until, data)
        return data

    # ------------------------------------------------------------------ PDF and printing
    async def async_pdf(
        self, days: int, full: bool, color: bool, custom: bool = False, research: bool = False
    ) -> tuple[bytes, int, str, str | None]:
        """Render the report as a PDF, keep a copy, and return (pdf, page count, file name, not_kept): not_kept is None
        when the copy was written, else the reason it could not be (_keep); the PDF is returned either way."""
        data = await self.async_data(days, full, custom, research)
        # one render at a time: report_pdf.render holds its RENDER_LOCK
        pdf, pages = await self.hass.async_add_executor_job(report_pdf.render, data, color, full)
        name = (
            f"{REPORT_PREFIX}{dt_util.now().strftime('%Y-%m-%d_%H%M')}_{data['ndays']}d_"
            f"{'full' if full else 'summary'}_{'color' if color else 'grayscale'}.pdf"
        )
        not_kept = await self.hass.async_add_executor_job(self._keep, name, pdf)
        return pdf, pages, name, not_kept

    def _keep(self, name: str, pdf: bytes) -> str | None:
        """Write a copy of the PDF to REPORT_DIR (executor) and keep only the newest REPORT_KEEP files. Return None, or
        the reason when the copy could not be written (a full disk, no permission: logged as a warning; the PDF itself
        is still downloaded or printed)."""
        folder = self.hass.config.path(REPORT_DIR)
        path = os.path.join(folder, name)
        try:
            os.makedirs(folder, exist_ok=True)
            with open(path + ".tmp", "wb") as fh:
                fh.write(pdf)
            os.replace(path + ".tmp", path)
        except OSError as err:
            reason = err.strerror or str(err) or type(err).__name__
            _LOGGER.warning("GlucoCraft could not keep a copy of the report %s in %s: %s", name, REPORT_DIR, reason)
            try:
                os.remove(path + ".tmp")
            except OSError:  # never written, or not removable either: the warning above names the cause
                pass
            return reason
        # the newest REPORT_KEEP PDFs, by the creation time in the name (it follows the prefix)
        old = sorted(f for f in os.listdir(folder) if f.startswith(REPORT_PREFIX) and f.endswith(".pdf"))
        for f in old[:-REPORT_KEEP]:
            try:
                os.remove(os.path.join(folder, f))
            except OSError as err:  # tried again with the next PDF
                _LOGGER.debug("GlucoCraft could not remove the old report %s: %s", f, err)
        return None

    async def async_print(
        self,
        days: int,
        full: bool,
        color: bool,
        two_sided: bool,
        include_custom_appendix: bool = False,
        include_research_appendix: bool = False,
    ) -> dict[str, Any]:
        """Print the report on the printer in the settings and return {ok, message, reason, pages, file, kept}: the
        result as the notification states it, the printer's answer, the page count, the PDF's file name and whether
        its copy was kept in REPORT_DIR (the message says why not). Without a printer address no PDF is made:
        {ok: False, message} only."""
        custom, research = bool(include_custom_appendix), bool(include_research_appendix)  # the shared option names
        target = setting(self.coordinator.settings, CONF_PRINTER)
        if not target:
            return {"ok": False, "message": t("report.print.no_printer")}
        pdf, pages, name, not_kept = await self.async_pdf(days, full, color, custom, research)
        job = t("report.print.job_name", period=period_label(days)).strip()  # async_pdf refused any other period
        ok, msg = await self.hass.async_add_executor_job(report_pdf.ipp_print, target, pdf, job, color, two_sided)
        _LOGGER.info("GlucoCraft report print: %s (%s, %d pages)", msg, name, pages)
        return {
            "ok": ok,
            "message": print_message(ok, msg, pages, name, not_kept),
            "reason": msg,
            "pages": pages,
            "file": name,
            "kept": not_kept is None,
        }

    # ------------------------------------------------------------------ laboratory A1C
    async def async_add_lab(self, date: str, a1c: float, unit: str = "percent") -> None:
        """Add or replace the laboratory A1C result of a date (percent or mmol/mol)."""
        entry = lab_entry(date, a1c, unit)
        self._lab = [r for r in self._lab if r.get("date") != date] + [entry]
        self._lab.sort(key=lambda r: r["date"])
        await self._store.async_save({"results": self._lab})
        self._cache.clear()

    async def async_import_lab(self, results: list[tuple[str, float, str]], not_read: int = 0) -> dict[str, int]:
        """Add imported laboratory results (date, value, unit) whose date has no stored result, and return the counts
        the import states: {"lab_imported", "lab_already_stored", "lab_not_read"}. A stored result is kept as it is
        (lab_already_stored); a result that lab_entry refuses (an impossible date, a value outside LAB_LIMITS) is left
        out and counted with the rows the file reader could not read (not_read: export.read_lab_csv) in
        lab_not_read."""
        dates = {r.get("date") for r in self._lab}
        added, already_stored, refused = [], 0, 0
        for date, value, unit in results:
            if date in dates:
                already_stored += 1
                continue
            try:
                added.append(lab_entry(date, value, unit))
            except ValueError:
                refused += 1
                continue
            dates.add(date)
        if added:
            self._lab = sorted([*self._lab, *added], key=lambda r: r["date"])
            await self._store.async_save({"results": self._lab})
            self._cache.clear()
        return {"lab_imported": len(added), "lab_already_stored": already_stored, "lab_not_read": not_read + refused}

    async def async_remove_lab(self, date: str) -> bool:
        """Remove the laboratory A1C result of a date; return True when one was removed."""
        before = len(self._lab)
        self._lab = [r for r in self._lab if r.get("date") != date]
        await self._store.async_save({"results": self._lab})
        self._cache.clear()
        return len(self._lab) != before


# The values that turn a report option on, and off (http_api reads full and color by these, and refuses any other)
REPORT_FLAG_ON = ("1", "true", "on", "yes")
REPORT_FLAG_OFF = ("0", "false", "off", "no")


def report_options(values) -> tuple[bool, bool]:
    """Return (include_custom_appendix, include_research_appendix) from service data or query parameters.

    "1", "true", "on", "yes" (REPORT_FLAG_ON) or True turn an appendix on; both are off unless chosen.
    """

    def on(v) -> bool:
        """Return True for an "on" value."""
        return v is True or str(v).strip().lower() in REPORT_FLAG_ON

    return on(values.get(REPORT_OPT_CUSTOM_APPENDIX, False)), on(values.get(REPORT_OPT_RESEARCH_APPENDIX, False))
