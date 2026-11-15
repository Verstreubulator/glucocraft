# License: AGPL-3.0-only (LICENSE).
"""Entity ids follow the display names, in the consensus terms. A renamed entity changes everywhere: its name, its id
and every file that refers to it.

- every entity's id is ``<platform>.glucocraft_<object id of its display name>`` (entity_id_for, the rule of
  entity_ids.object_id);
- names, ids and user-visible attribute keys use the consensus terms: very low, low, in range, tight range, high,
  very high, custom - not "critical", "ideal", "target range" or "personal";
- every entity id a consumer names (code, cards, dashboard, translations, the documentation, the example panel
  configurations) is the id of an entity GlucoCraft creates, and no consumer uses an id in the earlier terms.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import time
import types

import pytest
from glucocraft import binary_sensor as bs
from glucocraft import entity_ids as ids
from glucocraft import sensor as se
from glucocraft.change import ChangeOverTime

from conftest import PANELS, PERIOD_KEYS, REPO, stats_record
from test_trend_and_configuration import settings_coordinator

PKG = REPO / "custom_components" / "glucocraft"

OLD_TERMS = re.compile(r"critical|ideal|target[ _]range|personal", re.IGNORECASE)


def entity_id_for(platform: str, name: str) -> str:
    """The entity id the rule gives a GlucoCraft entity: ``<platform>.glucocraft_<object id of the name>``."""
    return f"{platform}.{ids.ID_PREFIX}{ids.object_id(name)}"


# ------------------------------------------------------------------------------------------------ the entities
@pytest.fixture
def coordinator(tmp_path):
    c = settings_coordinator(tmp_path)
    now = int(time.time())
    c.current_glucose, c.no_current_data, c.last_reading_ts = 50.0, False, now
    c.period_stats = {p: stats_record(period_label=p) for p in PERIOD_KEYS}
    c.period_stats_agp = dict(c.period_stats)
    c.weekly_trends = c.weekly_trends_agp = [stats_record()] * 3
    c.weekly_trends_at, c.trend_gmi14, c.trend_gri14, c.trend_directions = now, [6.9] * 3, [40.1] * 3, {"tir": "Stable"}
    block = ChangeOverTime(weeks14=[{"active": 90}], week_ends=[now])
    c.change, c.change_at = (
        {"consensus": block, "personal": ChangeOverTime(weeks14=[{"active": 90}], week_ends=[now])},
        now,
    )
    return c


def _entities(coordinator):
    """Every entity GlucoCraft can create, those of the two optional features included (which of them exist is
    checked in tests/test_optional_features.py)."""
    entry = types.SimpleNamespace(entry_id="e1", data={})
    out = [("sensor", e) for group in se.entity_groups(coordinator, entry).values() for e in group]
    out += [("binary_sensor", e) for e in bs.build_entities(coordinator, entry)]
    for _, e in out:
        e.coordinator = coordinator  # the stub CoordinatorEntity keeps nothing
    return out


STRINGS = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))


def english_name(platform: str, e) -> str:
    """Return an entity's English name: the name of its translation key in strings.json, placeholders filled."""
    text = STRINGS["entity"][platform][e.__dict__["_attr_translation_key"]]["name"]
    return text.format(**(e.__dict__.get("_attr_translation_placeholders") or {}))


def test_every_id_follows_its_display_name(coordinator):
    """The entity id is explicit and equal to the rule's id of the English name (the same id in every language)."""
    seen = set()
    for platform, e in _entities(coordinator):
        assert "_attr_name" not in e.__dict__, e.entity_id  # the name is translated, never set in the code
        name = english_name(platform, e)
        assert e.entity_id == entity_id_for(platform, name), (name, e.entity_id)
        assert e.entity_id not in seen, e.entity_id
        seen.add(e.entity_id)
    # core (the Spoken summary included), per period (the consensus ranges and the custom ranges summary included), GMI
    # and GRI (14d and longer), weekly + change, binary
    assert len(seen) == 10 + 7 * 15 + 4 + 4 + 2 + 9


def test_names_and_ids_use_the_consensus_terms(coordinator):
    for platform, e in _entities(coordinator):
        assert not OLD_TERMS.search(english_name(platform, e)), english_name(platform, e)
        assert not OLD_TERMS.search(e.entity_id), e.entity_id


# the words an entity name or state name keeps in capitals: abbreviations, the placeholder and the period labels
_CAPITALS = re.compile(r"\{period\}|CV|GMI|GRI|LO|HI|\d+[HD]")


def _sentence_case(text: str) -> bool:
    """Home Assistant's naming rule: the first word starts with a capital letter (or a number), the rest is lower case,
    except abbreviations and the period labels."""
    words = text.split()
    first = words[0]
    if _CAPITALS.fullmatch(first):
        return all(_CAPITALS.fullmatch(w) or w == w.lower() for w in words[1:])
    return (
        (first[0].isdigit() or first[0].isupper())
        and first[1:] == first[1:].lower()
        and all(_CAPITALS.fullmatch(w) or w == w.lower() for w in words[1:])
    )


def test_names_and_state_names_are_in_sentence_case():
    """Every entity name and every name of a state or attribute state in strings.json, before and after the period is
    filled in ("14D custom time in range", "Glucose in range", "Not configured")."""
    checked = 0
    for platform in ("sensor", "binary_sensor"):
        for key, entry in STRINGS["entity"][platform].items():
            names = [
                entry["name"],
                *(entry["name"].format(period=p.upper()) for p in PERIOD_KEYS if "{" in entry["name"]),
            ]
            names += list(entry.get("state", {}).values())
            for attribute in entry.get("state_attributes", {}).values():
                names += list(attribute.get("state", {}).values())
            for name in names:
                assert _sentence_case(name), (key, name)
                checked += 1
    assert checked > 100
    assert not _sentence_case("Last Glucose Reading") and not _sentence_case("14D Custom Time In Range")
    assert _sentence_case("5-minute delta") and _sentence_case("14D GMI") and _sentence_case("Glucose in range")


def test_unique_ids_are_unchanged(coordinator):
    """History and entity settings follow the unique id: a rename never changes it."""
    uids = {e._attr_unique_id for _, e in _entities(coordinator)}
    for key in (
        "is_critical_low",
        "is_critical_high",
        "in_range",
        "in_tight_range",
        "delta_5min",
        *(f"{p}_{k}" for p in PERIOD_KEYS for k in ("tir", "titr", "time_critical_low", "time_critical_high")),
    ):
        assert f"e1_{key}" in uids, key
    # entities the code does not create (Smoothed Glucose, Rate of Change, Custom GRI Formula, the GRI below 14 days)
    for key in ("smoothed", "delta", *(f"{p}_risk_score" for p in PERIOD_KEYS), "6h_gri", "24h_gri", "7d_gri"):
        assert f"e1_{key}" not in uids, key


@pytest.mark.parametrize(
    "entity_id",
    [
        "binary_sensor.glucocraft_glucose_very_low",
        "binary_sensor.glucocraft_glucose_very_high",
        "binary_sensor.glucocraft_glucose_in_range",
        "binary_sensor.glucocraft_glucose_in_tight_range",
        "sensor.glucocraft_5_minute_delta",
        # names in the consensus terms
        "binary_sensor.glucocraft_no_current_data",
        "binary_sensor.glucocraft_sensor_warm_up",
        "sensor.glucocraft_weekly_statistics",
        "sensor.glucocraft_data_history",
        "binary_sensor.glucocraft_glucose_below_range",
        "binary_sensor.glucocraft_glucose_above_range",
        "sensor.glucocraft_last_glucose_reading",
        "sensor.glucocraft_last_reading_time",
        *(
            f"sensor.glucocraft_{p}_{m}"
            for p in PERIOD_KEYS
            for m in (
                "custom_time_in_range",
                "custom_time_in_tight_range",
                "custom_time_low",
                "custom_time_high",
                "custom_time_very_low",
                "custom_time_very_high",
                "consensus_ranges",
                "standard_deviation",
                "minimum",
                "maximum",
            )
        ),
    ],
)
def test_the_consensus_term_ids_exist(coordinator, entity_id):
    assert entity_id in {e.entity_id for _, e in _entities(coordinator)}


def _walk(value, path=""):
    """Every (path, key) of nested attribute dicts and lists."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield f"{path}.{k}", k
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")


def test_attribute_keys_use_the_consensus_terms(coordinator):
    checked = 0
    for _, e in _entities(coordinator):
        attrs = e.extra_state_attributes  # a stub object for the entities without attributes of their own
        if not isinstance(attrs, dict):
            continue
        checked += 1
        for path, key in _walk(attrs):
            assert not OLD_TERMS.search(str(key)), f"{e.entity_id} attribute {path}"
    assert checked >= 38  # every entity with attributes (configuration, weekly statistics, change over time, ...)


def test_shown_values_use_the_consensus_terms(coordinator):
    """The zone, the weekly pages' standard and the notification types as shown (the stored values may stay)."""
    coordinator._weekly_pages = "personal"
    coordinator.range_low = 75  # custom ranges, so the custom standard is shown
    coordinator.update_settings({"phone_notify_types": ["summaries", "critical_low"]})
    cfg = coordinator.configuration()
    assert cfg["weekly_pages"] == {"setting": "custom", "offered": True, "shown": "custom"}
    assert cfg["phone_notifications"]["types"] == ["summaries"]  # a type GlucoCraft does not offer is left out
    assert "test_notifications" not in cfg
    assert set(cfg["ranges"]) == {"very_low", "low", "tight_low", "tight_high", "high", "very_high"}
    assert "agp_ranges" not in cfg  # the earlier name of consensus_ranges
    assert cfg["consensus_ranges"] == {
        "range_very_low": 54,
        "range_low": 70,
        "range_tight_low": 70,
        "range_tight_high": 140,
        "range_high": 180,
        "range_very_high": 250,
    }
    assert set(cfg["goals"]) <= {
        "time_in_range",
        "time_in_tight_range",
        "time_low",
        "time_very_low",
        "time_high",
        "time_very_high",
        "cv",
        "gmi",
    }
    for g, zone in ((50, "very_low"), (100, "tight_range"), (300, "very_high")):
        coordinator.current_glucose = g
        assert coordinator.glucose_zone == zone


@pytest.mark.parametrize(
    "hours, state",
    [
        (None, "No data"),
        (0, "Less than 6 hours"),
        (5.9, "Less than 6 hours"),
        (6, "6 hours"),
        (23.9, "6 hours"),
        (24, "24 hours"),
        (24 * 7, "7 days"),
        (24 * 14, "14 days"),
        (24 * 30, "30 days"),
        (24 * 90, "90 days"),
        (24 * 179.9, "90 days"),
        (24 * 180, "180 days"),
        (24 * 200, "180 days"),
    ],
)
def test_data_history_states(coordinator, hours, state):
    """The state is the longest statistics period the stored span reaches; no "Ready", "Full" or "unlocked"."""
    readings = (
        [] if hours is None else [types.SimpleNamespace(timestamp=0), types.SimpleNamespace(timestamp=hours * 3600)]
    )
    coordinator.storage = types.SimpleNamespace(
        readings=readings, reading_count=len(readings), problem=None, kept_file=None, save_error=None
    )
    e = se.GlucoCraftDataHistory(coordinator, types.SimpleNamespace(entry_id="e1", data={}))
    e.coordinator = coordinator
    assert STRINGS["entity"]["sensor"]["data_maturity"]["state"][e.native_value] == state  # the key's English name
    attrs = e.extra_state_attributes
    assert "features_unlocked" not in attrs
    assert attrs["features_available_count"] == sum(attrs["features_available"].values())
    # the keys name the entities by their names (Last glucose reading, Weekly statistics)
    assert {"last_glucose_reading", "weekly_statistics"} <= attrs["features_available"].keys()
    assert not {"current_glucose", "weekly_trends"} & attrs["features_available"].keys()


def test_data_history_gmi_follows_the_70_percent_rule(coordinator):
    """features_available.gmi is true exactly when a GMI entity has a value (>= 14 days with >= 70 % data)."""
    coordinator.storage = types.SimpleNamespace(
        readings=[types.SimpleNamespace(timestamp=0), types.SimpleNamespace(timestamp=20 * 86400)],
        reading_count=2,
        problem=None,
        kept_file=None,
        save_error=None,
    )
    e = se.GlucoCraftDataHistory(coordinator, types.SimpleNamespace(entry_id="e1", data={}))
    e.coordinator = coordinator
    coordinator.period_stats = {p: stats_record(period_label=p, data_availability=60.0) for p in PERIOD_KEYS}
    assert e.extra_state_attributes["features_available"]["gmi"] is False  # 20-day span, 60 % data: GMI unknown
    coordinator.period_stats["30d"] = stats_record(period_label="30d", data_availability=75.0)
    assert e.extra_state_attributes["features_available"]["gmi"] is True


# ------------------------------------------------------------------------------------------------ __init__.py
def _load_init():
    """custom_components/glucocraft/__init__.py as a module of the stub package (ha_stub does not run it)."""
    name = "glucocraft._init_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PKG / "__init__.py", submodule_search_locations=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "glucocraft"
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_setup_has_no_rename_or_clean_up_pass():
    """GlucoCraft's first published entry format has no earlier entity ids to rename and no hand-kept list of earlier
    entities: setup goes from the language straight to the coordinator and the platforms (the one general clean-up of
    entities no longer created: stale_entities.py)."""
    init = _load_init()
    assert not hasattr(init, "_migrate_entity_ids") and not hasattr(init, "_cleanup_orphan_entities")
    assert not hasattr(ids, "ENTITY_ID_RENAMES")


# ------------------------------------------------------------------------------------------------ the consumers
# The example panel configurations, their headers and the landscape generator's parts, when available
# (conftest.panel_folder): a panel that reads an earlier id fails the suite. Only these files are read, never
# secrets.yaml.
PANEL_CONFIGS = (
    sorted(PANELS.glob("glucocraft-*.yaml"))
    + sorted(PANELS.glob("glucocraft_*.h"))
    + [PANELS / "gc_units.h", PANELS / "lvgl_compat.h"]
    + [PANELS / "tools" / "landscape_top_layer.yaml", PANELS / "tools" / "landscape_about.yaml"]
    + [PANELS / "tools" / "make_glucocraft_landscape.py"]
    if PANELS
    else []
)
# The documents that name entity ids; one that is not in this copy of the repository is skipped.
DOCUMENTS = (
    "ENTITIES_AND_API.md",
    "SYSTEM_DESIGN.md",
    "DISPLAY_CONTRACT.md",
    "PANELS.md",
    "DASHBOARD.md",
)

CONSUMERS = sorted(
    [
        p
        for p in PKG.rglob("*")
        if p.suffix in (".py", ".js", ".json", ".yaml") and "__pycache__" not in p.parts and p.name != "entity_ids.py"
    ]
    + [REPO / "README.md"]
    + [REPO / "docs" / f for f in DOCUMENTS]
    + PANEL_CONFIGS
)

# an id in the earlier terms, also written with a period placeholder (${p}, {period}, <period>, &lt;period&gt;)
_PERIOD = r"(?:\d+[hd]|\$\{p\}|\{period\}|\{p\}|<period>|<p>|<P>|&lt;period&gt;|&lt;p&gt;)"
_OLD_ID = re.compile(
    r"glucocraft_[A-Za-z0-9_${}<>&;]*?_(time_in_target_range|time_in_ideal_range|time_critical_low|"
    r"time_critical_high|data_availability)(?![a-z0-9_])"
    # the earlier ids of the period metrics, also written with a placeholder
    + r"|glucocraft_"
    + _PERIOD
    + r"_(consensus|time_in_range|time_in_tight_range|time_low|time_high|time_very_low|"
    r"time_very_high|std_dev|min|max)(?![a-z0-9_])"
    # the two in-range binary sensors before they were named "Glucose in range" / "Glucose in tight range"
     + r"|binary_sensor\.glucocraft_in_(?:tight_)?range(?![a-z0-9_])"
)
# in the cards and the dashboard: an earlier entity suffix or attribute key as a string or a property
_OLD_JS_KEY = re.compile(
    r"""["'`](time_in_target_range|time_in_ideal_range|time_critical_low|time_critical_high|range_critical_low|"""
    r"""range_critical_high|range_ideal_low|range_ideal_high|critical_low|critical_high|ideal_low|ideal_high|"""
    r"""personal_is_consensus|tbr_critical|tar_critical)["'`]|\.(critical_low|critical_high|ideal_low|ideal_high)\b"""
)


@pytest.mark.parametrize(
    "path", CONSUMERS, ids=lambda p: p.relative_to(REPO).as_posix() if p.is_relative_to(REPO) else f"esphome/{p.name}"
)
def test_no_consumer_refers_to_an_earlier_id(path):
    if not path.is_file():
        pytest.skip(f"{path.name} is not in this copy of the repository")
    text = path.read_text(encoding="utf-8")
    hits = sorted({m.group(0) for m in _OLD_ID.finditer(text)})
    if path.suffix == ".js":
        hits += sorted({m.group(0) for m in _OLD_JS_KEY.finditer(text)})
    assert not hits, f"{path.name}: {hits}"


# a whole GlucoCraft entity id (an id built with a placeholder, "sensor.glucocraft_${p}_mean", is not matched)
_ANY_ID = re.compile(r"(?<![A-Za-z0-9_.])(?:sensor|binary_sensor)\.glucocraft_[a-z0-9_]*[a-z0-9](?![A-Za-z0-9_${}<&])")


@pytest.mark.parametrize(
    "path", CONSUMERS, ids=lambda p: p.relative_to(REPO).as_posix() if p.is_relative_to(REPO) else f"esphome/{p.name}"
)
def test_every_entity_id_a_consumer_names_exists(coordinator, path):
    """An id a consumer names is the id of an entity GlucoCraft creates (an optional feature's entities included)."""
    if not path.is_file():
        pytest.skip(f"{path.name} is not in this copy of the repository")
    current = {e.entity_id for _, e in _entities(coordinator)}
    unknown = sorted({m.group(0) for m in _ANY_ID.finditer(path.read_text(encoding="utf-8"))} - current)
    assert not unknown, f"{path.name}: {unknown}"
