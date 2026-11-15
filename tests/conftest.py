# License: AGPL-3.0-only (LICENSE).
"""Shared set-up for GlucoCraft's test suite (see tests/README.md).

Importing this file installs the Home Assistant stand-ins (``ha_stub.install()``) before pytest collects any test
module, so every ``tests/test_*.py`` file can simply ``from glucocraft import <module>``. The fixtures below are
available to every test file without an import.

Files outside the integration are optional: the example panel configurations (``PANELS``, see ``panel_folder``) and
the documents in ``docs/`` (``repo_doc``). A test that needs one of them is skipped, with the reason, when this copy
of the repository does not contain it.
"""

from __future__ import annotations

import os
import sys
import types
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ha_stub

ha_stub.install()

from glucocraft.statistics import PeriodStats
from glucocraft.storage import GlucoCraftStorage, GlucoseReading
from glucocraft.units import gmi_mmol_mol

# Time zones used across the suite: a DST zone, a half-hour zone and a 45-minute zone.
LA = ZoneInfo("America/Los_Angeles")
KOLKATA = ZoneInfo("Asia/Kolkata")  # UTC+05:30, no DST
KATHMANDU = ZoneInfo("Asia/Kathmandu")  # UTC+05:45, no DST

# The consensus ranges in mg/dL (Battelino et al., 2019; tight range Battelino et al., 2023), as keyword arguments of
# statistics.compute_period_stats.
AGP = {
    "range_low": 70,
    "range_high": 180,
    "range_ideal_low": 70,
    "range_ideal_high": 140,
    "range_critical_low": 54,
    "range_critical_high": 250,
}

PERIOD_KEYS = ("6h", "24h", "7d", "14d", "30d", "90d", "180d")

REPO = Path(__file__).resolve().parents[1]


def panel_folder() -> Path | None:
    """Return the folder of the example panel configurations, or None when it is not available.

    The folder named by the environment variable GLUCOCRAFT_PANELS is used when it is set; otherwise the repository's
    ``esphome/`` folder. A folder counts only when it holds ``glucocraft-panel.yaml``. ``secrets.yaml`` in that
    folder is never read by any test.
    """
    configured = os.environ.get("GLUCOCRAFT_PANELS")
    folder = Path(configured) if configured else REPO / "esphome"
    return folder if (folder / "glucocraft-panel.yaml").is_file() else None


PANELS = panel_folder()
NO_PANELS = (
    "the example panel configurations are not available: set GLUCOCRAFT_PANELS to their folder, or place "
    "them in esphome/ in the repository"
)


def repo_doc(name: str) -> str:
    """Return the text of ``docs/<name>``; skip the calling test when this copy of the repository lacks it."""
    path = REPO / "docs" / name
    if not path.is_file():
        pytest.skip(f"docs/{name} is not in this copy of the repository")
    return path.read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _empty_store():
    """Every test starts with an empty in-memory Home Assistant Store, and on the clock of ha_stub.restart_clock (the
    real clock, or the instant of GLUCOCRAFT_TEST_CLOCK)."""
    ha_stub.restart_clock()
    ha_stub.reset_store()
    yield
    ha_stub.reset_store()


# The coroutines handed to a stand-in's hass.async_create_task that the test may run later (task_list)
_HANDED_TASKS: list[list] = []


def task_list() -> list:
    """Return a new list for the coroutines a stand-in Home Assistant is handed (hass.async_create_task); a test runs
    the ones it checks, and _close_unrun_tasks closes the rest when the test ends, so that none is left never
    awaited."""
    tasks: list = []
    _HANDED_TASKS.append(tasks)
    return tasks


@pytest.fixture(autouse=True)
def _close_unrun_tasks():
    """Close the coroutines of every task_list() that the test did not run (closing a finished one does nothing)."""
    yield
    while _HANDED_TASKS:
        for coro in _HANDED_TASKS.pop():
            coro.close()


@pytest.fixture
def agp_ranges() -> dict:
    return dict(AGP)


@pytest.fixture
def make_readings():
    """Factory: make_readings(values, start=1_700_000_000, step=300) -> [GlucoseReading].

    `values` is a list of mg/dL values or a callable f(i) with `count`."""

    def make(values, start: int = 1_700_000_000, step: int = 300, count: int | None = None):
        if callable(values):
            values = [values(i) for i in range(count or 0)]
        return [GlucoseReading(start + i * step, float(v)) for i, v in enumerate(values)]

    return make


def add_reading(storage: GlucoCraftStorage, timestamp: int, raw: float, limit=None, source: int = 0) -> None:
    """Append a reading after the newest stored one (storage.insert_reading); a reading at or before the newest time is
    left out. The tests feed their readings in time order."""
    if storage.readings and storage.readings[-1].timestamp >= timestamp:
        return
    storage.insert_reading(timestamp, raw, limit, source)


@pytest.fixture
def storage(tmp_path) -> GlucoCraftStorage:
    """An empty, loaded storage in a temporary directory."""
    s = GlucoCraftStorage(str(tmp_path), "test")
    s.load()
    return s


def stats_record(**overrides) -> PeriodStats:
    """A complete PeriodStats with plain values; any field can be overridden."""
    base = {
        "period_label": "x",
        "period_seconds": 14 * 86400,
        "reading_count": 5000,
        "data_availability": 95.0,
        "mean": 150.4,
        "std_dev": 50.0,
        "cv": 33.2,
        "gmi": 6.9,
        "min_value": 62.0,
        "max_value": 280.5,
        "pct_time_in_target_range": 71.0,
        "pct_time_in_ideal_range": 50.0,
        "time_low": 2.5,
        "time_high": 20.1,
        "time_critical_low": 1.6,
        "time_critical_high": 5.3,
        "gri": 40.1,
        "gri_hypo": 3.6,
        "gri_hyper": 15.4,
    }
    base.update(overrides)
    # the values compute_period_stats derives, from the values above unless given: the combined bands, the GMI in
    # mmol/mol and the covered time (sensor active x the period)
    base.setdefault("time_below_low", base["time_low"] + base["time_critical_low"])
    base.setdefault("time_above_high", base["time_high"] + base["time_critical_high"])
    base.setdefault("gmi_mmol_mol", gmi_mmol_mol(base["mean"]))
    base.setdefault("covered_seconds", base["data_availability"] * base["period_seconds"] / 100)
    return PeriodStats(**base)


@pytest.fixture
def make_stats():
    """Factory for PeriodStats records: make_stats(mean=120, data_availability=60, ...)."""
    return stats_record


class _States:
    """The states of a stand-in Home Assistant: entity id -> state object."""

    def __init__(self, states: dict | None = None):
        self._states = states or {}

    def get(self, entity_id):
        """Return the state of an entity, or None."""
        return self._states.get(entity_id)


class _Services:
    """Records service calls; has_service answers from `available` ("notify.phone" style names)."""

    def __init__(self, available=()):
        self.calls: list[tuple[str, str, dict]] = []
        self.available = set(available)

    def has_service(self, domain, name):
        """True when the action was made available to this stand-in."""
        return f"{domain}.{name}" in self.available

    async def async_call(self, domain, service, data=None, **kwargs):
        """Record the action call (domain, action, data)."""
        self.calls.append((domain, service, dict(data or {})))


@pytest.fixture
def fake_hass():
    """Factory: fake_hass(states={entity_id: state}, services=("notify.phone",)) -> a minimal hass object."""

    def make(states: dict | None = None, services=()):
        async def run(fn, *args):
            return fn(*args)

        return types.SimpleNamespace(
            states=_States(states), services=_Services(services), async_add_executor_job=run, data={}
        )

    return make


@dataclass
class FakeCoordinator:
    """The coordinator attributes that the notifications, the panel feed and the report read (plain values)."""

    display_unit: str = "mg/dL"
    current_glucose: float | None = 120.0
    current_limit: str | None = None  # the current reading's LO / HI flag
    last_reading_ts: int = 0
    display_status: str = "current"
    no_current_data: bool = False
    sensor_warming_up: bool = False
    warmup_started_at: int = 0  # the time the warm-up began (0: none)
    stale_minutes: int | None = None  # None: the age of the reading is not checked
    detected_interval_seconds: int | None = None
    expires_at: object = None
    trend_arrow: str = "→"
    trend_description: str = ""  # the trend word ("" while unknown: the arrow is spoken)
    range_critical_low: float = 54
    range_low: float = 70
    range_ideal_low: float = 70
    range_ideal_high: float = 140
    range_high: float = 180
    range_critical_high: float = 250
    custom_ranges: bool = False
    custom_goals: bool = False
    goal_time_in_target_range: float | None = 70
    goal_time_in_ideal_range: float | None = None
    goal_cv: float | None = 36
    goal_gmi: float | None = None
    goal_tbr: float | None = 4
    goal_tar: float | None = 25
    period_stats: dict = field(default_factory=dict)
    period_stats_agp: dict = field(default_factory=dict)
    trend_weeks: list = field(default_factory=list)
    personal_trend_weeks: list = field(default_factory=list)
    trend_directions: dict = field(default_factory=dict)
    personal_trend_directions: dict = field(default_factory=dict)
    trend_gmi14: list = field(default_factory=list)
    weekly_pages_custom: bool = False
    change_at: int | None = None
    storage: object = None
    hass: object = None
    entry_id: str = "test"
    _settings: dict = field(default_factory=dict)

    @property
    def settings(self) -> dict:
        """The entry's settings."""
        return self._settings

    def update_settings(self, data: dict) -> None:
        """Change settings, as a Configure save does."""
        self._settings.update(data)

    def uses_custom_ranges(self) -> bool:
        """True when the custom ranges are in use."""
        return self.custom_ranges

    def goals_custom(self) -> bool:
        """True when custom goals are set."""
        return self.custom_goals

    def locked(self, fn, *args):
        """Run fn as the coordinator runs it under the reading lock (here: directly)."""
        return fn(*args)

    def reading_interval(self) -> int | None:
        """The reading interval of the source in use (None: not known)."""
        return None

    def time_untrusted_at(self, now: int) -> bool:
        """True while the newest reading's time is more than 5 minutes ahead of `now` (the coordinator's rule)."""
        return bool(self.last_reading_ts) and self.last_reading_ts > now + 300

    @property
    def regional(self):
        """The regional settings (the defaults)."""
        from glucocraft.regional import Regional

        return Regional()


class DictStates:
    """A stand-in for hass.states: get(entity_id) returns the state object stored under that id in `d`."""

    def __init__(self):
        self.d = {}

    def get(self, entity_id):
        """Return the state of an entity, or None."""
        return self.d.get(entity_id)


def real_coordinator(tmp_path, *, country: str = "US", language: str = "en", **attributes):
    """Build a GlucoCraftCoordinator with its own __init__ and a stand-in Home Assistant.

    The coordinator starts with the default settings and the primary source sensor.p; its readings file is
    glucocraft_t.bin in tmp_path (loaded, empty). Keyword arguments then set attributes, for example `_settings` (the
    entry's settings), `_secondary_entity` or the ranges. hass.states is a DictStates; state saves are not scheduled.
    """
    from glucocraft.const import CONF_GLUCOSE_ENTITY
    from glucocraft.coordinator import GlucoCraftCoordinator

    hass = types.SimpleNamespace(
        states=DictStates(),
        config=types.SimpleNamespace(
            country=country, language=language, path=lambda *parts: str(tmp_path), components={"recorder"}
        ),
        data={},
    )
    c = GlucoCraftCoordinator(hass, "t", {CONF_GLUCOSE_ENTITY: "sensor.p"})
    c.hass = hass
    for name, value in attributes.items():
        setattr(c, name, value)
    c.storage.load()
    c._schedule_state_save = lambda: None
    return c


@pytest.fixture
def make_coordinator(fake_hass):
    """Factory: make_coordinator(**attributes) -> FakeCoordinator with a fake hass."""

    def make(**kw):
        kw.setdefault("hass", fake_hass())
        return FakeCoordinator(**kw)

    return make


def local_ts(tz, *args, fold: int = 0) -> int:
    """Epoch seconds of a local wall-clock time: local_ts(LA, 2026, 11, 1, 1, 30, fold=1)."""
    return int(datetime(*args, tzinfo=tz, fold=fold).timestamp())


@dataclass
class Rd:
    """A stored reading as the report reads it: time (epoch s), value (mg/dL), the LO / HI flag (limits.py; None
    for a measured value) and the source byte (const.SOURCE_*: 0 unknown, 1 primary, 2 secondary)."""

    timestamp: int
    raw: float
    limit: str | None = None
    source: int = 0


# Home Assistant's entity registry as GlucoCraft calls it (ha_stub.EntityRegistry); a test that needs its own
# registry builds one: FakeRegistry({"sensor.x": {"unique_id": "e_x", "options": {...}}})
FakeRegistry = ha_stub.EntityRegistry
