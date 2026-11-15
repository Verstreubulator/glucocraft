# License: AGPL-3.0-only (LICENSE).
"""The harness of the settings test matrix (tests/test_settings_*.py; the inventory in test_settings_inventory.py).

Every test of the matrix starts from a stored config entry (the entry's ``data``, as Home Assistant hands it to the
integration) and reads the outputs the integration builds from it, never the settings screens:

- ``build(tmp_path, data, ...)`` runs GlucoCraft's own coordinator ``__init__`` with ``data`` and a stand-in Home
  Assistant (country, language, time zone, services), loads artificial readings into its storage, restores the newest
  reading the way ``async_setup`` does and computes the statistics. It returns a ``Run``.
- ``Run`` collects the outputs: every sensor and binary sensor (state, unit, attributes), the Configuration sensor's
  attributes (the dashboard's and the cards' input), the panel feed (GLU2 v3 with GCX1 and the GCL2 trailer, parsed
  as the firmware parses it), the HTTP API's JSON, the summaries (texts, recorded service calls), the CGM Report data and PDF, and the CSV export.
- ``fixed_clock`` (a fixture) runs the clock from ``NOON`` (noon in Los Angeles) for a test whose texts name the
  day of a reading; ``build`` and ``Run.reload`` take "now" from ``time.time()``, the clock the integration reads.
- ``pairwise(params)`` builds a pairwise covering array: every pair of values of every two settings appears in at
  least one row.

Nothing here changes the integration; the tests at the end check the harness itself.
"""

from __future__ import annotations

import asyncio
import itertools
import math
import re
import time
import types
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from glucocraft import binary_sensor as bs
from glucocraft import const as C
from glucocraft import coordinator as coordinator_module
from glucocraft import export as EX
from glucocraft import http_api as H
from glucocraft import notification as N
from glucocraft import panel_feed as PF
from glucocraft import report_service as RS
from glucocraft import sensor as se
from glucocraft.coordinator import GlucoCraftCoordinator
from glucocraft.settings import setting
from glucocraft.storage import GlucoseReading

import ha_stub
from conftest import LA, DictStates, task_list
from test_panel_feed import parse as parse_glu2

ENTRY_ID = "t"
PRIMARY = "sensor.p"
SECONDARY = "sensor.s"
STEP = 300  # seconds between the artificial readings

# The stored defaults of an entry: the test-matrix baseline. Every key is a stored setting; the values are the defaults,
# the two optional features off as the setup stores them (a new setup stores no ranges and goals; they are stored once
# Custom ranges and goals is turned on and they are set in Configure, and are listed here at their default values).
DEFAULT_ENTRY: dict[str, Any] = {
    C.CONF_GLUCOSE_ENTITY: PRIMARY,
    C.CONF_SENSOR_EXPIRY_ENTITY: None,
    C.CONF_SECONDARY_ENTITY: None,
    C.CONF_SENSOR_LOWER_LIMIT: "auto",
    C.CONF_SENSOR_UPPER_LIMIT: "auto",
    C.CONF_SECONDARY_SENSOR_LOWER_LIMIT: "auto",
    C.CONF_SECONDARY_SENSOR_UPPER_LIMIT: "auto",
    C.CONF_UNIT: "mg/dL",
    C.CONF_RANGE_CRITICAL_LOW: 54,
    C.CONF_RANGE_LOW: 70,
    C.CONF_RANGE_TIGHT_LOW: 70,
    C.CONF_RANGE_TIGHT_HIGH: 140,
    C.CONF_RANGE_HIGH: 180,
    C.CONF_RANGE_CRITICAL_HIGH: 250,
    C.CONF_GOAL_TIME_IN_TARGET_RANGE: 70,
    C.CONF_GOAL_TIME_IN_IDEAL_RANGE: None,
    C.CONF_GOAL_TBR: 4,
    C.CONF_GOAL_TBR_CRITICAL: 1,
    C.CONF_GOAL_TAR: 25,
    C.CONF_GOAL_TAR_CRITICAL: 5,
    C.CONF_GOAL_CV: 36,
    C.CONF_GOAL_GMI: None,
    C.CONF_STALE_THRESHOLD: 15,
    C.CONF_NOTIFY_ENABLED: True,
    C.CONF_NOTIFY_TIME: "08:00",
    C.CONF_SHOW_DASHBOARD: True,
    C.CONF_TIME_FORMAT: "auto",
    C.CONF_DATE_FORMAT: "auto",
    C.CONF_FIRST_WEEKDAY: "auto",
    C.CONF_DECIMAL_SEPARATOR: "auto",
    C.CONF_A1C_UNIT: "auto",
    C.CONF_PAPER_SIZE: "auto",
    C.CONF_TREND_DISPLAY: "arrow",
    C.CONF_PHONE_TARGETS: [],
    C.CONF_PHONE_TYPES: list(C.DEFAULT_PHONE_TYPES),
    C.CONF_CUSTOM_RANGES_AND_GOALS: False,
    C.CONF_SEPARATE_STATISTICS: False,
}
# both optional features on
BOTH_FEATURES = {C.CONF_CUSTOM_RANGES_AND_GOALS: True, C.CONF_SEPARATE_STATISTICS: True}

# Every setting at a value other than its default (one valid choice each): the "non-default" full run
NON_DEFAULT_ENTRY: dict[str, Any] = {
    **DEFAULT_ENTRY,
    C.CONF_SENSOR_EXPIRY_ENTITY: "sensor.expiry",
    C.CONF_SECONDARY_ENTITY: SECONDARY,
    C.CONF_PRIMARY_TIME_ATTR: "measurement_timestamp",
    C.CONF_SECONDARY_TIME_ATTR: "dateString",
    C.CONF_SENSOR_LOWER_LIMIT: "50",
    C.CONF_SENSOR_UPPER_LIMIT: "400",
    C.CONF_SECONDARY_SENSOR_LOWER_LIMIT: "40",
    C.CONF_SECONDARY_SENSOR_UPPER_LIMIT: "450",
    C.CONF_RANGE_CRITICAL_LOW: 60,
    C.CONF_RANGE_LOW: 75,
    C.CONF_RANGE_TIGHT_LOW: 80,
    C.CONF_RANGE_TIGHT_HIGH: 130,
    C.CONF_RANGE_HIGH: 170,
    C.CONF_RANGE_CRITICAL_HIGH: 230,
    C.CONF_GOAL_TIME_IN_TARGET_RANGE: 75,
    C.CONF_GOAL_TIME_IN_IDEAL_RANGE: 50,
    C.CONF_GOAL_TBR: 3,
    C.CONF_GOAL_TBR_CRITICAL: 2,
    C.CONF_GOAL_TAR: 20,
    C.CONF_GOAL_TAR_CRITICAL: 3,
    C.CONF_GOAL_CV: 33,
    C.CONF_GOAL_GMI: 6.5,
    C.CONF_WEEKLY_PAGES: "personal",
    C.CONF_STALE_THRESHOLD: 30,
    C.CONF_NOTIFY_ENABLED: False,
    C.CONF_NOTIFY_TIME: "21:45",
    C.CONF_NOTIFY_WEEKLY_ENABLED: True,
    C.CONF_NOTIFY_WEEKLY_DAY: 0,
    C.CONF_NO_DATA_NOTICE: True,
    C.CONF_NO_DATA_NOTICE_DELAY: 60,
    C.CONF_FLASH_NO_CURRENT_DATA: False,
    C.CONF_SHOW_DASHBOARD: False,
    C.CONF_TIME_FORMAT: "24",
    C.CONF_DATE_FORMAT: "ymd",
    C.CONF_FIRST_WEEKDAY: "saturday",
    C.CONF_DECIMAL_SEPARATOR: "comma",
    C.CONF_A1C_UNIT: "both",
    C.CONF_PAPER_SIZE: "a4",
    C.CONF_DISPLAY_CARD: "rectangular",
    C.CONF_TREND_DISPLAY: "word",
    C.CONF_PHONE_TARGETS: ["mobile_app_phone", "mobile_app_tablet"],
    C.CONF_PHONE_TYPES: ["summaries", "no_data_notice"],
    C.CONF_PRINTER: "ipp://printer.local:631/ipp/print",
    C.CONF_PANEL_FEED: True,
    **BOTH_FEATURES,
    **{
        C.CONF_ZONE_COLORS[z]: c
        for z, c in zip(
            C.ZONES, ("#111111", "#222222", "#333333", "#444444", "#555555", "#666666", "#777777"), strict=True
        )
    },
}


# The clock of a test that names the day of a reading (the fixture fixed_clock): Wednesday 21 October 2026 at noon
# in Los Angeles. The newest reading of the artificial series and the texts made from it are then on the same local
# day, and the 15 days of the series hold no daylight-saving change.
NOON = int(datetime(2026, 10, 21, 12, 0, tzinfo=LA).timestamp())


@pytest.fixture
def fixed_clock() -> int:
    """The clock (time.time() and with it the stub's dt_util and node) runs from NOON in this test; returns NOON."""
    ha_stub.set_clock(NOON)
    return NOON


def entry(**changes: Any) -> dict[str, Any]:
    """Return the default entry with `changes` (a value of ...: the key is removed)."""
    out = dict(DEFAULT_ENTRY)
    for k, v in changes.items():
        if v is ...:
            out.pop(k, None)
        else:
            out[k] = v
    return out


# ------------------------------------------------------------------------------------------------ artificial data
def value_at(i: int) -> float:
    """The artificial series (mg/dL): a 6-hour wave from 60 to 240 with a daily dip to 45 and a daily peak to 300.

    Every glucose zone of the default ranges occurs each day, every value has one decimal as stored.
    """
    day_pos = i % 288  # 288 readings of 5 minutes = one day
    v = 150 + 90 * math.sin(2 * math.pi * i / 72)
    if 36 <= day_pos < 42:  # 03:00-03:30: a dip
        v = 45.0 + (day_pos - 36)
    if 216 <= day_pos < 228:  # 18:00-19:00: a peak
        v = 300.0 - 2 * (day_pos - 216)
    return round(v, 1)


def series(now: int, days: float = 15, value=value_at, step: int = STEP, age: int = 60) -> list[GlucoseReading]:
    """Readings every `step` seconds for `days` days, the newest `age` seconds before `now`."""
    n = int(days * 86400 // step)
    last = now - age
    return [GlucoseReading(last - (n - 1 - k) * step, float(value(k)), None, C.SOURCE_PRIMARY) for k in range(n)]


# ------------------------------------------------------------------------------------------------ the stand-in hass
class Services:
    """hass.services: records every call; has_service answers from `available` ("notify.mobile_app_phone")."""

    def __init__(self, available=()):
        self.calls: list[tuple[str, str, dict]] = []
        self.available = set(available)

    def has_service(self, domain, name):
        """True when the action was made available to this stand-in."""
        return f"{domain}.{name}" in self.available

    async def async_call(self, domain, service, data=None, **kwargs):
        """Record the action call (domain, action, data)."""
        self.calls.append((domain, service, dict(data or {})))

    def notifications(self) -> list[dict]:
        """The persistent notifications created, in order."""
        return [d for dom, svc, d in self.calls if dom == "persistent_notification" and svc == "create"]

    def phone(self) -> list[tuple[str, dict]]:
        """The notify calls (target, data), in order."""
        return [(svc, d) for dom, svc, d in self.calls if dom == "notify"]


def make_hass(
    tmp_path, country="US", language="en", time_zone="America/Los_Angeles", services=(), components=("recorder",)
):
    """A stand-in Home Assistant: states, config (country, language, time zone, path, the loaded integrations: the
    recorder, as in almost every installation), services, executor jobs."""

    async def run(fn, *args):
        return fn(*args)

    tasks = task_list()  # run by the tests that check them; the rest are closed when the test ends
    hass = types.SimpleNamespace(
        states=DictStates(),
        config=types.SimpleNamespace(
            country=country,
            language=language,
            time_zone=time_zone,
            path=lambda *parts: str(tmp_path.joinpath(*parts)),
            components=set(components),
        ),
        services=Services(services),
        async_add_executor_job=run,
        async_create_task=lambda coro: tasks.append(coro),
        data={},
        loop=types.SimpleNamespace(call_soon_threadsafe=lambda fn, *a: fn(*a), call_later=lambda *a: None),
    )
    hass.tasks = tasks
    return hass


def state(value, **attributes):
    """A source state object."""
    return types.SimpleNamespace(state=str(value), attributes=attributes, last_reported=None)


# ------------------------------------------------------------------------------------------------ one run
def _defines(obj, name: str) -> bool:
    """True when a GlucoCraft class of obj defines `name` (the stub base classes answer every name)."""
    return any(name in k.__dict__ for k in type(obj).__mro__ if k.__module__.startswith("glucocraft"))


def _defined_value(obj, name: str):
    """The value of `name` from the first GlucoCraft class that defines it, else None."""
    for k in type(obj).__mro__:
        if k.__module__.startswith("glucocraft") and name in k.__dict__:
            return k.__dict__[name]
    return None


@dataclass
class Run:
    """One config entry through the integration, and every output built from it."""

    c: GlucoCraftCoordinator
    hass: Any
    data: dict
    now: int
    _entities: list = field(default_factory=list)

    # ---- entities
    def entities(self) -> list:
        """Every sensor and binary sensor the entry can have, those of an optional feature that is off included: the
        matrix checks each entity's output; which entities exist is checked in tests/test_optional_features.py."""
        if not self._entities:
            ent = types.SimpleNamespace(entry_id=ENTRY_ID, data=self.data)
            sensors = [e for group in se.entity_groups(self.c, ent).values() for e in group]
            out = sensors + list(bs.build_entities(self.c, ent))
            for e in out:
                e.coordinator = self.c  # the stub CoordinatorEntity keeps nothing
            self._entities = out
        return self._entities

    def entity(self, key: str):
        """The entity whose unique id is f"{entry_id}_{key}"."""
        uid = f"{ENTRY_ID}_{key}"
        return next(e for e in self.entities() if e._attr_unique_id == uid)

    def sensor(self, key: str) -> dict[str, Any]:
        """{"state", "unit", "suggested_unit", "attributes"} of one entity, as Home Assistant reads them."""
        e = self.entity(key)
        value = e.is_on if _defines(e, "is_on") else e.native_value
        if _defines(e, "native_unit_of_measurement"):
            unit = e.native_unit_of_measurement
        else:
            unit = _defined_value(e, "_attr_native_unit_of_measurement")
        return {
            "state": value,
            "unit": unit if isinstance(unit, str) else None,
            "suggested_unit": e.__dict__.get("_attr_suggested_unit_of_measurement"),
            "attributes": e.extra_state_attributes if _defines(e, "extra_state_attributes") else {},
        }

    def all_sensors(self) -> dict[str, dict[str, Any]]:
        """Every entity's output by unique id key."""
        return {
            e._attr_unique_id[len(ENTRY_ID) + 1 :]: self.sensor(e._attr_unique_id[len(ENTRY_ID) + 1 :])
            for e in self.entities()
        }

    # ---- the dashboard's and the cards' input
    def config(self) -> dict[str, Any]:
        """The Configuration sensor's attributes."""
        return self.c.configuration()

    # ---- the panel feed
    def feed_bytes(self, delta: bool = False) -> bytes | None:
        """The panel file (GLU2) as served: the full file, or the recent-readings file."""
        return PF.build_glu2(self.c, delta)

    def feed(self, delta: bool = False) -> dict:
        """The panel file, parsed."""
        return parse_glu2(self.feed_bytes(delta))

    # ---- the HTTP API
    def _loaded(self):
        self.hass.config_entries = types.SimpleNamespace(
            async_entries=lambda domain: [types.SimpleNamespace(runtime_data=self.c)]
        )

    def api(self, view_cls, query: dict | None = None) -> Any:
        """The JSON (as a dict) or the response of one view."""
        self._loaded()
        view = view_cls()
        view.json = lambda data, **kw: data
        request = types.SimpleNamespace(app={H.KEY_HASS: self.hass}, query=dict(query or {}))
        return asyncio.run(view.get(request))

    def api_data(self, period: str = "6h") -> dict:
        """/api/glucocraft/data for a period."""
        return self.api(H.GlucoCraftDataView, {"period": period})

    def api_stats(self) -> dict:
        """/api/glucocraft/stats."""
        return self.api(H.GlucoCraftStatsView)

    # ---- notifications
    def daily(self) -> str:
        """The daily summary's text."""
        return N._build_daily_summary(self.c)

    def weekly(self) -> str:
        """The weekly summary's text."""
        return N._build_weekly_summary(self.c)

    def monthly(self) -> str:
        """The monthly summary's text."""
        return N._build_monthly_summary(self.c)

    # ---- a source writes a new state (the coordinator's own state listener)
    def push(
        self,
        entity_id: str,
        value,
        *,
        at: int | None = None,
        unit: str | None = "mg/dL",
        attr: str | None = "measurement_timestamp",
        old: str | None = "100",
        **attributes,
    ) -> None:
        """A state change of `entity_id` to `value`; `at` = its measurement time in the attribute `attr`."""
        from datetime import UTC, datetime

        attrs = dict(attributes)
        if unit:
            attrs["unit_of_measurement"] = unit
        if at is not None and attr:
            attrs[attr] = datetime.fromtimestamp(at, UTC).isoformat()
        new = state(value, **attrs)
        self.hass.states.d[entity_id] = new
        event = types.SimpleNamespace(
            data={"entity_id": entity_id, "new_state": new, "old_state": state(old) if old else None}
        )
        executor = self.hass.async_add_executor_job
        self.hass.async_add_executor_job = lambda fn, *a: fn(*a)  # processed at once, as the executor would
        try:
            self.c._async_state_changed(event)
        finally:
            self.hass.async_add_executor_job = executor

    # ---- the CGM Report
    def report(self, days: int = 14, full: bool = False, custom: bool = False, research: bool = False) -> dict:
        """The CGM Report's data."""
        return asyncio.run(self.c.reports.async_data(days, full, custom, research))

    def pdf(self, days: int = 14, full: bool = False, color: bool = True, custom: bool = False, research=False):
        """The CGM Report as a PDF, and its data."""
        from glucocraft import report_pdf

        data = self.report(days, full, custom, research)
        return report_pdf.render(data, color, full), data

    # ---- the CSV export
    def csv(self) -> str:
        """The CSV export of the readings."""
        return EX.readings_csv(list(self.c.storage.readings), self.c.display_unit, ZoneInfo(self.hass.config.time_zone))

    # ---- a reload (every options save ends with one)
    def reload(self, data: dict[str, Any]) -> Run:
        """Save the readings, then set up a new coordinator with the entry `data` by its own async_setup, on the same
        stand-in Home Assistant (its states and files): what Home Assistant does before it writes the new entities'
        states. No new reading arrives: the store holds 100 readings or more, so only the recorder's readings after
        the newest stored one are imported, and the stand-in recorder holds none."""
        self.c.storage.save()
        c = GlucoCraftCoordinator(self.hass, ENTRY_ID, dict(data))
        c.hass = self.hass

        async def no_newer_readings(*_args, **_kw):
            return 0

        importer, coordinator_module.async_import_history = coordinator_module.async_import_history, no_newer_readings
        try:
            asyncio.run(c.async_setup())
        finally:
            coordinator_module.async_import_history = importer
        c.reports = RS.GlucoCraftReports(self.hass, c)
        return Run(c, self.hass, dict(data), int(time.time()))


def build(
    tmp_path,
    data: dict[str, Any] | None = None,
    *,
    country: str | None = "US",
    language: str | None = "en",
    time_zone: str = "America/Los_Angeles",
    readings: list | None = None,
    days: float = 15,
    now: int | None = None,
    services=(),
    states: dict | None = None,
    compute: bool = True,
) -> Run:
    """Run the coordinator's own __init__ with the stored entry `data` and restore `readings` (default: `days` of the
    artificial series ending now) as async_setup does; the statistics are computed unless compute=False."""
    data = dict(DEFAULT_ENTRY if data is None else data)
    now = int(time.time()) if now is None else now
    hass = make_hass(tmp_path, country, language, time_zone, services)
    for k, v in (states or {}).items():
        hass.states.d[k] = v
    c = GlucoCraftCoordinator(hass, ENTRY_ID, data)
    c.hass = hass
    c.storage.load()
    c._schedule_state_save = lambda: None
    rows = series(now, days) if readings is None else list(readings)
    c.storage._readings = [GlucoseReading(r.timestamp, r.raw, r.limit, r.source) for r in rows]
    if rows:
        # as async_setup restores the newest stored reading
        last = c.storage.readings[-1]
        c.current_glucose, c.current_limit = last.raw, last.limit
        c.last_reading_ts = last.timestamp
        c.no_current_data = c._no_current_data_at(now)
        if not c.no_current_data:
            c._apply_adaptive(last.timestamp)
        c._src_last_ts[PRIMARY] = last.timestamp
        c._src_last_value[PRIMARY] = last.raw
        c.active_source_entity, c.active_source = PRIMARY, "Primary CGM"
        c.detected_interval_seconds = c.storage.detect_interval() if len(rows) >= 10 else 300
    if compute:
        c._compute_all_stats(now)
    c.reports = RS.GlucoCraftReports(hass, c)
    return Run(c, hass, data, now)


_AGE = re.compile(r"\(\d+ (?:min|h \d+ min|d \d+ h) before this summary\)")


def fingerprint(run: Run, report: bool = True) -> dict[str, Any]:
    """Every output of a run, for comparing two entries: the Configuration attributes (without the echo of each
    choice as stored, *_setting and weekly_pages.setting), every entity, the panel feed (both files), the HTTP API,
    the three summaries, the CGM Report data (without its creation time) and the CSV export."""

    def strip(cfg: dict) -> dict:
        cfg = {k: v for k, v in cfg.items() if not k.endswith("_setting")}
        cfg["weekly_pages"] = {k: v for k, v in cfg["weekly_pages"].items() if k != "setting"}
        return cfg

    cfg = strip(run.config())
    sensors = {}
    for key, out in run.all_sensors().items():
        attrs = strip(out["attributes"]) if key == "configuration" else out["attributes"]
        sensors[key] = (out["state"], out["unit"], out["suggested_unit"], attrs)
    out = {
        "config": cfg,
        "sensors": sensors,
        "feed": run.feed_bytes(),
        "delta": run.feed_bytes(True),
        "api": run.api_data("24h"),
        "stats": run.api_stats(),
        "daily": _AGE.sub("", run.daily()),
        "weekly": run.weekly(),
        "monthly": run.monthly(),
        "csv": run.csv(),
    }
    if report:
        rep = dict(run.report(14, False, True, True))
        # the creation time differs between two runs a minute apart, so it is no output to compare
        for k in ("created", "created_ts", "generated"):
            rep.pop(k, None)
        out["report"] = rep
    return out


@pytest.fixture
def tz_patch(monkeypatch):
    """dt_util.get_time_zone for the report service (the stub has none): a ZoneInfo by name."""
    from homeassistant.util import dt as dt_util

    monkeypatch.setattr(dt_util, "get_time_zone", lambda name: ZoneInfo(name) if name else None, raising=False)
    return dt_util


def set_tz(monkeypatch, name: str) -> ZoneInfo:
    """Home Assistant's time zone for the whole test (dt_util default)."""
    from homeassistant.util import dt as dt_util

    tz = ZoneInfo(name)
    monkeypatch.setattr(dt_util, "DEFAULT_TIME_ZONE", tz)
    return tz


# ------------------------------------------------------------------------------------------------ the bugs found
# A defect the tests find and that is not fixed at once is listed here, one line each, and its tests are marked with
# bug(key), a strict expected failure (pytest.mark.xfail(strict=True)): when the defect is fixed, its tests pass and
# the run fails until the marker and the entry are removed. None is open at present.
BUGS: dict[str, str] = {}


def bug(key: str):
    """The strict expected failure of a defect in BUGS."""
    return pytest.mark.xfail(strict=True, reason=f"BUG {key}: {BUGS[key]}")


# ------------------------------------------------------------------------------------------------ pairwise
def pairwise(params: dict[str, list]) -> list[dict[str, Any]]:
    """A pairwise covering array of `params` (greedy, deterministic): every value pair of every two parameters
    appears in at least one row."""
    names = list(params)
    uncovered = {(a, va, b, vb) for a, b in itertools.combinations(names, 2) for va in params[a] for vb in params[b]}
    rows: list[dict[str, Any]] = []
    while uncovered:
        best, best_cover = None, -1
        # seed each candidate with one uncovered pair, then fill the other parameters greedily
        for seed in sorted(uncovered, key=repr)[:40]:
            a, va, b, vb = seed
            row = {a: va, b: vb}
            for n in names:
                if n in row:
                    continue
                row[n] = max(
                    params[n],
                    key=lambda v, n=n: sum(
                        1 for m, w in row.items() if (m, w, n, v) in uncovered or (n, v, m, w) in uncovered
                    ),
                )
            cover = sum(1 for x, y in itertools.combinations(names, 2) if (x, row[x], y, row[y]) in uncovered)
            if cover > best_cover:
                best, best_cover = row, cover
        rows.append(best)
        uncovered -= {(x, best[x], y, best[y]) for x, y in itertools.combinations(names, 2)}
    return rows


def assert_pairs_covered(params: dict[str, list], rows: list[dict]) -> None:
    for a, b in itertools.combinations(list(params), 2):
        for va in params[a]:
            for vb in params[b]:
                assert any(r[a] == va and r[b] == vb for r in rows), (a, va, b, vb)


# ------------------------------------------------------------------------------------------------ the harness's own tests
def test_pairwise_covers_every_pair():
    params = {"a": [1, 2, 3], "b": ["x", "y"], "c": [True, False], "d": ["p", "q", "r", "s"]}
    rows = pairwise(params)
    assert_pairs_covered(params, rows)
    assert len(rows) < 3 * 2 * 2 * 4  # fewer rows than every combination


def test_default_entry_is_what_the_setup_stores():
    """The baseline entry: every key the setup stores, with the default values of const.py."""
    assert DEFAULT_ENTRY[C.CONF_STALE_THRESHOLD] == C.DEFAULT_STALE_THRESHOLD
    assert DEFAULT_ENTRY[C.CONF_GOAL_TIME_IN_TARGET_RANGE] == C.DEFAULT_GOAL_TIME_IN_TARGET_RANGE
    assert DEFAULT_ENTRY[C.CONF_GOAL_GMI] is C.DEFAULT_GOAL_GMI is None
    assert DEFAULT_ENTRY[C.CONF_PHONE_TYPES] == C.DEFAULT_PHONE_TYPES
    assert all(setting(DEFAULT_ENTRY, k) == DEFAULT_ENTRY[k] for k in C.REGIONAL_SETTINGS)


def test_the_non_default_entry_changes_every_setting():
    # the unit: the full runs use this entry in both units
    skip = {C.CONF_GLUCOSE_ENTITY, C.CONF_UNIT}
    for k, v in DEFAULT_ENTRY.items():
        if k not in skip:
            assert NON_DEFAULT_ENTRY[k] != v, k


@pytest.mark.usefixtures("tz_patch")
def test_build_produces_every_output(tmp_path):
    run = build(tmp_path)
    assert run.c.display_status == "current"
    assert run.sensor("current_glucose")["state"] == run.c.current_glucose
    assert run.config()["unit"] == "mg/dL"
    assert run.feed_bytes()[:4] == b"GLU2" and run.feed()["ext"]["standards"]
    assert run.api_data()["unit"] == "mg/dL" and run.api_stats()["periods"]["14d"]["gmi"] is not None
    assert "GlucoCraft Daily Summary" in run.daily()
    assert run.report(14)["ndays"] == 14
    assert run.csv().startswith("local_time,")
