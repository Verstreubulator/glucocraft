# License: AGPL-3.0-only (LICENSE).
"""Home Assistant, aiohttp and voluptuous stand-ins, so GlucoCraft's modules import and run without Home Assistant.

``install()`` (called once by ``conftest.py`` before any test module is collected) does three things:

1. Puts an import hook in front of ``sys.meta_path`` that answers every import of ``homeassistant.*``, ``aiohttp.*``
   and ``voluptuous`` with a permissive stub module: any attribute is a class of its own (so several stub base
   classes can be combined in one class definition), and any call returns a stub object.
2. Replaces the few pieces whose behavior the tests depend on with working versions: ``homeassistant.core.callback``
   (identity), ``homeassistant.const`` state strings, ``homeassistant.util.dt`` (real time-zone arithmetic in
   ``TZ``, on the clock of ``time.time()``: ``set_clock``, ``GLUCOCRAFT_TEST_CLOCK``),
   ``homeassistant.helpers.storage.Store`` (an in-memory store, ``STORE_DATA``), the entity and issue registries (``ENTITY_REGISTRY``, ``ISSUE_REGISTRY``, empty in every test) and the exceptions GlucoCraft raises
   (``homeassistant.exceptions``: real exception classes that keep their translation key and placeholders).
3. Registers the integration as the package ``glucocraft`` (path ``custom_components/glucocraft``) WITHOUT running
   its ``__init__.py``, so ``from glucocraft import units`` imports only what a test needs.

The stub is idempotent: calling ``install()`` again does nothing. Nothing here talks to a network or a real
Home Assistant instance.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import os
import sys
import time
import types
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_DIR = Path(__file__).resolve().parents[1]
PKG_DIR = REPO_DIR / "custom_components" / "glucocraft"
PKG_NAME = "glucocraft"

# The Home Assistant time zone the stub reports (dt_util.DEFAULT_TIME_ZONE); tests that need another zone pass it in.
TZ = ZoneInfo("America/Los_Angeles")

# The test clock. time.time() is the real clock unless a test sets one (set_clock: the clock then runs from that
# instant), or the environment variable GLUCOCRAFT_TEST_CLOCK names an instant: every test then starts at it
# (tests/README.md, "Running at a given time"). The stub's dt_util and node's clock (node_runner) follow time.time().
CLOCK_VARIABLE = "GLUCOCRAFT_TEST_CLOCK"
_real_time = time.time
_clock_offset = 0.0  # seconds added to the real clock


def simulated_clock(value: str | None = None) -> datetime | None:
    """The instant of GLUCOCRAFT_TEST_CLOCK (or of `value`), or None when it is not set.

    An ISO date and time with a UTC offset ("2026-11-03T00:00:30-08:00"), or a local time followed by a time-zone name
    in brackets ("2026-11-03T00:00:30[America/Los_Angeles]"; of a repeated hour, its first occurrence).
    """
    value = os.environ.get(CLOCK_VARIABLE, "") if value is None else value
    if not value:
        return None
    if value.endswith("]") and "[" in value:
        local, _, zone = value[:-1].partition("[")
        return datetime.fromisoformat(local).replace(tzinfo=ZoneInfo(zone))
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        raise ValueError(f"{CLOCK_VARIABLE} needs a UTC offset or a time-zone name: {value}")
    return instant


def _clock() -> float:
    """time.time() of a set clock: the real clock plus the offset."""
    return _real_time() + _clock_offset


def set_clock(instant: float | None) -> None:
    """Let time.time() run from `instant` (epoch seconds) on; None: the real clock."""
    global _clock_offset
    _clock_offset = 0.0 if instant is None else instant - _real_time()
    time.time = _real_time if instant is None else _clock


def restart_clock() -> None:
    """The clock a test starts with (conftest calls this before every test): GLUCOCRAFT_TEST_CLOCK's instant, or the
    real clock."""
    instant = simulated_clock()
    set_clock(None if instant is None else instant.timestamp())


def clock_offset() -> float:
    """The seconds the clock is ahead of the real one (0: the real clock); node_runner shifts node's clock by it."""
    return _clock_offset


STUBBED_ROOTS = ("homeassistant", "aiohttp", "voluptuous")

_installed = False


# ---------------------------------------------------------------------------------------------- permissive stubs
class _Meta(type):
    """Metaclass of the stub classes: any class attribute is a stub, and Generic[...] subscripting works."""

    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Stub()

    def __getitem__(cls, item):
        return cls


class _Stub(metaclass=_Meta):
    """Accepts any arguments, returns a stub for any attribute or call."""

    def __init__(self, *args, **kwargs):
        pass

    def __init_subclass__(cls, **kwargs):  # class X(ConfigFlow, domain=DOMAIN)
        pass

    def __call__(self, *args, **kwargs):
        return _Stub()

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Stub()


class StubModule(types.ModuleType):
    """A module whose missing attributes are distinct stub classes (one per name, cached)."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        cls = _Meta(name, (_Stub,), {})
        setattr(self, name, cls)
        return cls


class _StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Answers imports below STUBBED_ROOTS that are not already in sys.modules with a StubModule."""

    def find_spec(self, fullname, path, target=None):
        """A stub package for every module under a stubbed root, else None (the real import)."""
        if fullname.split(".")[0] in STUBBED_ROOTS:
            return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        """A new stub module that is also a package."""
        mod = StubModule(spec.name)
        mod.__path__ = []
        return mod

    def exec_module(self, module):
        """Nothing to run: every attribute is made on first use."""


def _module(name: str, **attrs) -> StubModule:
    """Create (or fetch) a stub module, set attributes on it and link it to its parent package."""
    mod = sys.modules.get(name)
    if not isinstance(mod, StubModule):
        mod = StubModule(name)
        mod.__path__ = []
        sys.modules[name] = mod
    mod.__dict__.update(attrs)
    parent, _, child = name.rpartition(".")
    if parent:
        setattr(_module(parent), child, mod)
    return mod


# ---------------------------------------------------------------------------------------------- working pieces
def _identity(fn):
    return fn


STORE_DATA: dict[str, object] = {}
"""The in-memory contents of every ``Store`` by key (``homeassistant.helpers.storage.Store``)."""
STORE_SAVES: list[str] = []
"""The keys saved, in order."""


class Store:
    """In-memory ``homeassistant.helpers.storage.Store``: async_load / async_save / async_remove on STORE_DATA."""

    def __init__(self, hass=None, version=1, key="", *args, **kwargs):
        self.hass = hass
        self.version = version
        self.key = key

    async def async_load(self):
        """Return the stored data of this key (a copy of a dict), or None."""
        value = STORE_DATA.get(self.key)
        return dict(value) if isinstance(value, dict) else value

    async def async_save(self, data):
        """Store the data under this key and record the save."""
        STORE_DATA[self.key] = dict(data) if isinstance(data, dict) else data
        STORE_SAVES.append(self.key)

    async def async_remove(self):
        """Remove the stored data of this key."""
        STORE_DATA.pop(self.key, None)


class EntityRegistry:
    """Home Assistant's entity registry, as GlucoCraft calls it (``homeassistant.helpers.entity_registry.async_get``):
    entries by entity id, each with its unique id, platform, config entry, device and entity options.

    EntityRegistry({"sensor.x": {"unique_id": "e_x", "platform": "glucocraft", "options": {...}}}); put() adds one
    entry, add(entities) registers created entities under their unique ids. `updates` lists the option updates
    written. ``ENTITY_REGISTRY`` is the registry every ``async_get(hass)`` returns (emptied before every test)."""

    def __init__(self, entries: dict[str, dict] | None = None) -> None:
        self.entities: dict[str, types.SimpleNamespace] = {}
        self.updates: list[tuple[str, str, dict]] = []
        for entity_id, entry in (entries or {}).items():
            self.put(entity_id, **entry)

    def put(
        self,
        entity_id: str,
        unique_id: str | None = None,
        platform: str = "glucocraft",
        options=None,
        config_entry_id: str | None = None,
        device_id: str | None = None,
    ) -> None:
        """Add a registry entry, as Home Assistant creates one for an entity."""
        self.entities[entity_id] = types.SimpleNamespace(
            entity_id=entity_id,
            domain=entity_id.partition(".")[0],
            unique_id=unique_id,
            platform=platform,
            config_entry_id=config_entry_id,
            device_id=device_id,
            options=dict(options or {}),
        )

    def add(self, entities, config_entry_id: str | None = None) -> None:
        """Register created entities (an entry already registered under its entity id keeps that id)."""
        for e in entities:
            if self.async_get_entity_id(e.entity_id.partition(".")[0], "glucocraft", e._attr_unique_id) is None:
                self.put(e.entity_id, e._attr_unique_id, config_entry_id=config_entry_id)

    @property
    def ids(self) -> dict[str, str]:
        """The entity id of each unique id."""
        return {e.unique_id: e.entity_id for e in self.entities.values() if e.unique_id is not None}

    def async_get_entity_id(self, domain: str, platform: str, unique_id: str) -> str | None:
        """Return the entity id of a unique id on a platform, or None."""
        return next(
            (
                e.entity_id
                for e in self.entities.values()
                if e.unique_id == unique_id and e.platform == platform and e.domain == domain
            ),
            None,
        )

    def async_get(self, entity_id: str):
        """Return the registry entry of an entity id, or None."""
        return self.entities.get(entity_id)

    def async_remove(self, entity_id: str) -> None:
        """Remove the registry entry of an entity id."""
        self.entities.pop(entity_id, None)

    def async_update_entity(self, entity_id: str, *, new_entity_id: str) -> None:
        """Change an entity id; an id another entity has is refused, as Home Assistant refuses it."""
        if new_entity_id in self.entities:
            raise ValueError("Entity with this ID is already registered")
        e = self.entities.pop(entity_id)
        e.entity_id = new_entity_id
        self.entities[new_entity_id] = e

    def async_update_entity_options(self, entity_id: str, domain: str, options: dict) -> None:
        """Set a domain's options of an entry and record the update."""
        e = self.entities[entity_id]
        e.options = {**e.options, domain: options}
        self.updates.append((entity_id, domain, options))


class IssueRegistry:
    """Home Assistant's issue registry (``homeassistant.helpers.issue_registry``): ``issues`` by (domain, issue id),
    each with the fields GlucoCraft reads. ``ISSUE_REGISTRY`` is the registry every ``async_get(hass)`` returns
    (emptied before every test); async_create_issue / async_delete_issue write it."""

    def __init__(self) -> None:
        self.issues: dict[tuple[str, str], types.SimpleNamespace] = {}

    def create(self, domain: str, issue_id: str, **kwargs) -> None:
        """Create or replace a Repairs issue (active)."""
        self.issues[(domain, issue_id)] = types.SimpleNamespace(
            domain=domain,
            issue_id=issue_id,
            active=True,
            dismissed_version=None,
            translation_key=kwargs.get("translation_key"),
            translation_placeholders=kwargs.get("translation_placeholders"),
            is_persistent=kwargs.get("is_persistent"),
            is_fixable=kwargs.get("is_fixable"),
            severity=kwargs.get("severity"),
            data=kwargs.get("data"),
        )


ENTITY_REGISTRY = EntityRegistry()
ISSUE_REGISTRY = IssueRegistry()


def reset_store() -> None:
    """Empty the in-memory Store and the two registries (conftest calls this before every test)."""
    STORE_DATA.clear()
    STORE_SAVES.clear()
    ENTITY_REGISTRY.entities.clear()
    ENTITY_REGISTRY.updates.clear()
    ISSUE_REGISTRY.issues.clear()


# ---------------------------------------------------------------------------------------------- exceptions
class HomeAssistantError(Exception):
    """``homeassistant.exceptions.HomeAssistantError``: an error with an optional translation (domain, key and
    placeholders, kept as attributes)."""

    def __init__(self, *args, translation_domain=None, translation_key=None, translation_placeholders=None):
        super().__init__(*args)
        self.translation_domain = translation_domain
        self.translation_key = translation_key
        self.translation_placeholders = translation_placeholders


class ServiceValidationError(HomeAssistantError):
    """``homeassistant.exceptions.ServiceValidationError``: an action called with values it refuses."""


class Unauthorized(HomeAssistantError):
    """``homeassistant.exceptions.Unauthorized``: the caller may not call the action."""

    def __init__(self, context=None, user_id=None, **kwargs):
        super().__init__(**kwargs)
        self.context = context
        self.user_id = user_id


class UnknownUser(Unauthorized):
    """``homeassistant.exceptions.UnknownUser``: the caller's user does not exist."""


def _registries() -> None:
    """Install the working entity and issue registries."""
    _module(
        "homeassistant.helpers.entity_registry",
        async_get=lambda hass: ENTITY_REGISTRY,
        async_entries_for_config_entry=lambda reg, entry_id: [
            e for e in reg.entities.values() if e.config_entry_id == entry_id
        ],
        async_entries_for_device=lambda reg, device_id, **kw: [
            e for e in reg.entities.values() if e.device_id == device_id
        ],
        EVENT_ENTITY_REGISTRY_UPDATED="entity_registry_updated",
    )
    _module(
        "homeassistant.helpers.issue_registry",
        async_get=lambda hass: ISSUE_REGISTRY,
        async_create_issue=lambda hass, domain, issue_id, **kw: ISSUE_REGISTRY.create(domain, issue_id, **kw),
        async_delete_issue=lambda hass, domain, issue_id: ISSUE_REGISTRY.issues.pop((domain, issue_id), None),
        IssueSeverity=types.SimpleNamespace(CRITICAL="critical", ERROR="error", WARNING="warning"),
        EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED="repairs_issue_registry_updated",
    )


def _dt_module() -> StubModule:
    d = _module("homeassistant.util.dt")

    def set_default_time_zone(tz) -> None:
        d.DEFAULT_TIME_ZONE = tz

    def parse_datetime(value: str):
        """An ISO date and time; None for a text of 10 characters or fewer (a date without a time).

        Home Assistant's own parser (ciso8601) reads such a date as its midnight. The coordinator therefore never
        passes it a text without a time part (coordinator.DATE_ONLY_LENGTH), so its tests do not depend on this
        difference.
        """
        try:
            if len(value) <= 10:
                return None
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    def parse_date(value: str):
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    d.DEFAULT_TIME_ZONE = TZ
    d.UTC = UTC
    d.set_default_time_zone = set_default_time_zone
    d.get_default_time_zone = lambda: d.DEFAULT_TIME_ZONE
    # one clock with time.time(), which the integration reads too (and which a test may set or simulate)
    d.utcnow = lambda: datetime.fromtimestamp(time.time(), UTC)
    d.now = lambda tz=None: datetime.fromtimestamp(time.time(), tz or d.DEFAULT_TIME_ZONE)
    d.as_utc = lambda x: x.astimezone(UTC)
    d.as_local = lambda x: x.astimezone(d.DEFAULT_TIME_ZONE)
    d.as_timestamp = lambda x: x.timestamp()
    d.utc_from_timestamp = lambda t: datetime.fromtimestamp(t, UTC)
    d.parse_datetime = parse_datetime
    d.parse_date = parse_date
    d.start_of_local_day = lambda day=None: datetime(
        *(day or d.now().date()).timetuple()[:3], tzinfo=d.DEFAULT_TIME_ZONE
    )
    return d


def install() -> None:
    """Install the stubs and the ``glucocraft`` package (idempotent)."""
    global _installed
    if _installed:
        return
    _installed = True
    restart_clock()
    sys.meta_path.insert(0, _StubFinder())

    _module(
        "homeassistant.const",
        STATE_UNAVAILABLE="unavailable",
        STATE_UNKNOWN="unknown",
        PERCENTAGE="%",
        EVENT_STATE_REPORTED="state_reported",
        EVENT_STATE_CHANGED="state_changed",
    )
    _module("homeassistant.core", callback=_identity)
    _module("homeassistant.helpers.storage", Store=Store)
    _module(
        "homeassistant.exceptions",
        HomeAssistantError=HomeAssistantError,
        ServiceValidationError=ServiceValidationError,
        Unauthorized=Unauthorized,
        UnknownUser=UnknownUser,
    )
    _dt_module()
    _registries()
    # submodules that the code imports with "from homeassistant.helpers import x" must be the modules themselves
    for sub in (
        "entity_registry",
        "issue_registry",
        "device_registry",
        "selector",
        "config_validation",
        "event",
        "update_coordinator",
        "entity",
        "entity_platform",
        "json",
    ):
        _module(f"homeassistant.helpers.{sub}")
    for sub in (
        "recorder",
        "recorder.history",
        "persistent_notification",
        "webhook",
        "frontend",
        "http",
        "sensor",
        "binary_sensor",
        "lovelace",
        "lovelace.const",
        "lovelace.dashboard",
    ):
        _module(f"homeassistant.components.{sub}")
    # the same for "from homeassistant import x": without the module in place, the import would find the stub's
    # made-up attribute x instead of the module, whatever the import order
    for sub in ("config_entries", "data_entry_flow"):
        _module(f"homeassistant.{sub}")
    _module("aiohttp.web")
    _module("voluptuous")

    if PKG_NAME not in sys.modules:
        pkg = types.ModuleType(PKG_NAME)
        pkg.__path__ = [str(PKG_DIR)]
        pkg.__file__ = str(PKG_DIR / "__init__.py")
        sys.modules[PKG_NAME] = pkg
