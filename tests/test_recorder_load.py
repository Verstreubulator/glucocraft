# License: AGPL-3.0-only (LICENSE).
"""What makes Home Assistant's recorder write a row: the entities' states and attributes over time.

Home Assistant writes a state (one recorder row) whenever an entity's state or any of its attributes changes; an
attribute left out of the recorder (``_unrecorded_attributes``) saves its bytes, not the row. Rules under test:

- The state of the 14 period summary sensors (``<period> consensus ranges``, ``<period> custom ranges``) is their time
  in range rounded once to one decimal, halves away from zero; the attributes keep the exact values (the cards, the
  panels and the report read them).
- No attribute follows the clock: a minute without a new reading writes no new state for any entity.
- A new reading changes neither the Configuration nor the Active source sensor (the reading time, the reading count
  and each source's newest measurement time are attributes of the Last reading time sensor, whose state changes with
  every reading anyway); the display card, the panels and the Maintenance page read them there.
- A statistics pass without a new reading writes no new state for the Status, No current data, Data history,
  Configuration and Active source entities; the time of the pass is the Weekly statistics sensor's ``computed_at``
  (that sensor's 26 weeks end at the pass, so it changes with every pass anyway), and every display reads it there.
- No display reads an attribute that no longer exists (``statistics_at`` of the Status sensor, ``minutes_since_reading``
  of No current data, ``data_hours`` and ``reading_count`` of Data history).
"""

from __future__ import annotations

import re
import time
import types

import pytest
from glucocraft import const as C
from glucocraft import sensor as se
from glucocraft.units import round_half_up

from conftest import PANELS, REPO
from test_settings_harness import PRIMARY, build, entry, fixed_clock, tz_patch  # noqa: F401 (fixtures)

WWW = REPO / "custom_components" / "glucocraft" / "www"


def written(run) -> dict[str, tuple]:
    """Every entity's (state, attributes) as Home Assistant compares them before it writes a new state."""
    return {key: (out["state"], out["attributes"]) for key, out in run.all_sensors().items()}


def changed(before: dict[str, tuple], after: dict[str, tuple]) -> set[str]:
    """The entities whose state or attributes differ: each one would write a recorder row."""
    return {key for key in before if before[key] != after[key]}


def at(monkeypatch, run, now: int) -> None:
    """Move the clock to `now` and apply the coordinator's minute tick (the no-current-data flag), with no reading."""
    monkeypatch.setattr(time, "time", lambda: float(now))
    run.c.no_current_data = run.c._no_current_data_at(now)


# ------------------------------------------------------------------------------------------------ the summary states
@pytest.mark.parametrize("exact, shown", [(28.9063187487193, 28.9), (28.95, 29.0), (69.96, 70.0), (0.04, 0.0)])
def test_the_summary_states_are_the_time_in_range_rounded_once(exact, shown):
    stats = types.SimpleNamespace(pct_time_in_target_range=exact, sensor_active_ok=True)
    coordinator = types.SimpleNamespace(period_stats={"14d": stats}, period_stats_agp={"14d": stats})
    for cls in (se.GlucoCraftPeriodConsensus, se.GlucoCraftPeriodCustom):
        entity = cls.__new__(cls)
        entity.coordinator, entity._period = coordinator, "14d"
        assert entity.native_value == shown, cls.__name__
        assert entity._attr_suggested_display_precision == 1  # Home Assistant shows the state as it is


def test_a_summary_without_statistics_has_no_state():
    coordinator = types.SimpleNamespace(period_stats={}, period_stats_agp={})
    for cls in (se.GlucoCraftPeriodConsensus, se.GlucoCraftPeriodCustom):
        entity = cls.__new__(cls)
        entity.coordinator, entity._period = coordinator, "14d"
        assert entity.native_value is None


@pytest.mark.usefixtures("tz_patch")
def test_the_summary_attributes_stay_exact(tmp_path):
    run = build(tmp_path, entry(custom_ranges_and_goals=True))
    exact_seen = False
    for period in ("6h", "24h", "7d", "14d", "30d", "90d", "180d"):
        for key, attr in (("consensus", "time_70_180"), ("custom", "time_in_range")):
            out = run.sensor(f"{period}_{key}")
            value = out["attributes"][attr]
            if not out["attributes"]["enough_data"]:  # the enough-data rule: no value and no state
                assert value is None and out["state"] is None, (period, key)
                continue
            assert out["state"] == round_half_up(value, 1), (period, key)
            exact_seen |= value != out["state"]
            # the other exact values are the statistics' own, unrounded
            stats = run.c.period_stats_agp[period] if key == "consensus" else run.c.period_stats[period]
            assert value == stats.pct_time_in_target_range and out["attributes"]["mean"] == stats.mean
    assert exact_seen  # the artificial series gives values with more than one decimal


# ------------------------------------------------------------------------------------------------ the clock
@pytest.mark.usefixtures("tz_patch", "fixed_clock")
def test_a_minute_without_a_reading_writes_no_new_state(tmp_path, monkeypatch):
    run = build(tmp_path)
    at(monkeypatch, run, run.now)
    first = written(run)
    at(monkeypatch, run, run.now + 60)
    second = written(run)
    at(monkeypatch, run, run.now + 120)
    assert changed(first, second) == set()
    assert changed(second, written(run)) == set()


@pytest.mark.usefixtures("tz_patch")
def test_no_current_data_publishes_no_age(tmp_path):
    run = build(tmp_path)
    assert run.sensor("is_stale")["attributes"] == {"threshold_minutes": run.c.stale_minutes}
    empty = build(tmp_path / "empty", readings=[])  # no reading ever: no age either
    assert empty.sensor("is_stale")["attributes"] == {"threshold_minutes": empty.c.stale_minutes}


# ------------------------------------------------------------------------------------------------ the statistics pass
@pytest.mark.usefixtures("tz_patch")
def test_a_statistics_pass_changes_only_the_statistics(tmp_path, monkeypatch):
    run = build(tmp_path)
    at(monkeypatch, run, run.now)
    before = written(run)
    at(monkeypatch, run, run.now + 300)
    run.c._compute_all_stats(run.now + 300)
    after = written(run)
    for key in ("status", "is_stale", "data_maturity", "configuration", "active_source", "last_reading"):
        assert before[key] == after[key], key
    # the Weekly statistics sensor carries the time of the pass, in the attributes left out of the recorder
    weekly_before, weekly_after = before["weekly_summary"], after["weekly_summary"]
    assert weekly_before[0] == weekly_after[0]
    assert weekly_after[1]["computed_at"] != weekly_before[1]["computed_at"]
    assert {"weeks", "computed_at"} <= se.GlucoCraftWeeklyStatistics._unrecorded_attributes


@pytest.mark.usefixtures("tz_patch")
def test_the_time_of_the_pass_is_on_the_weekly_statistics_only(tmp_path):
    run = build(tmp_path)
    assert set(run.sensor("status")["attributes"]) == {"label", "color"}
    computed = run.sensor("weekly_summary")["attributes"]["computed_at"]
    assert computed == se.dt_util.utc_from_timestamp(run.now).replace(microsecond=0).isoformat()
    assert build(tmp_path / "none", compute=False).sensor("weekly_summary")["attributes"]["computed_at"] is None


@pytest.mark.usefixtures("tz_patch")
def test_data_history_publishes_no_attribute_that_follows_the_readings(tmp_path):
    attrs = build(tmp_path).sensor("data_maturity")["attributes"]
    assert set(attrs) == {
        "features_available",
        "features_available_count",
        "features_total",
        "storage_problem",
        "storage_kept_file",
    }
    assert "_unrecorded_attributes" not in vars(se.GlucoCraftDataHistory)


@pytest.mark.usefixtures("tz_patch")
def test_a_new_reading_changes_the_last_reading_time_and_not_the_configuration_or_the_source(tmp_path, monkeypatch):
    run = build(tmp_path, entry(**{C.CONF_GLUCOSE_ENTITY: PRIMARY}))
    at(monkeypatch, run, run.now)
    run.push(PRIMARY, "128", at=run.now - 30)  # a first live reading: the source in use is named by its entity
    before = written(run)
    last = run.sensor("last_reading")["attributes"]
    assert last["last_reading_ts"] == run.c.last_reading_ts and last["reading_count"] == run.c.storage.reading_count
    assert last["primary_last_measurement"] == se.dt_util.utc_from_timestamp(run.c.last_reading_ts).isoformat()
    assert last["last_reading_mgdl"] == run.c.current_glucose  # the panels compare it with the value they got
    assert last["secondary_last_measurement"] is None  # no second source
    at(monkeypatch, run, run.now + 60)
    run.push(PRIMARY, "131", at=run.now + 30, old="128")
    after = written(run)
    assert run.c.last_reading_ts == run.now + 30
    assert {"last_reading", "configuration", "active_source"} & changed(before, after) == {"last_reading"}
    assert after["last_reading"][1]["last_reading_ts"] == run.now + 30
    assert after["last_reading"][1]["reading_count"] == before["last_reading"][1]["reading_count"] + 1
    assert set(after["active_source"][1]) == {
        "no_current_data",
        "source_entity",
        "second_source_active",
        "detected_interval",
    }
    assert "last_measurement" not in str(after["configuration"][1])


def test_the_displays_read_the_reading_time_from_the_last_reading_time_sensor():
    files = _sources()
    card = files["glucocraft-display-card.js"]
    assert 'last: "sensor.glucocraft_last_reading_time",' in card
    assert card.count("?.attributes?.last_reading_ts") == 2  # the live point and the age, both from that sensor
    assert "state_attr('\" + LAST + \"', 'reading_count')" in files["glucocraft-strategy.js"]
    if PANELS is not None:
        for name, text in files.items():
            if name.endswith(".yaml"):
                assert (
                    "    id: ha_last_reading_ts\n    entity_id: sensor.glucocraft_last_reading_time\n"
                    "    attribute: last_reading_ts\n"
                ) in text, name


# ------------------------------------------------------------------------------------------------ every reader
_PANEL_TOP_LEVEL = re.compile(r"^(?:esphome|lvgl):", re.MULTILINE)


def _panel_configs(folder) -> dict[str, str]:
    """The panel configurations among the folder's glucocraft-*.yaml files: a file with an ``esphome:`` or ``lvgl:``
    top level. Another file there, such as the testers' starter file (a ``packages:`` file that includes a panel
    configuration), is not a panel configuration and is left out."""
    texts = {p.name: p.read_text(encoding="utf-8") for p in sorted(folder.glob("glucocraft-*.yaml")) if p.is_file()}
    return {name: text for name, text in texts.items() if _PANEL_TOP_LEVEL.search(text)}


def _sources() -> dict[str, str]:
    """The display card, the dashboard and the example panel configurations (when available)."""
    files = {p.name: p.read_text(encoding="utf-8") for p in sorted(WWW.glob("*.js"))}
    if PANELS is not None:
        files |= _panel_configs(PANELS)
    return files


def test_a_file_that_is_not_a_panel_configuration_is_left_out(tmp_path):
    """The starter file beside the panel configurations includes one of them; it is not read as one."""
    (tmp_path / "glucocraft-starter.yaml").write_text(
        'substitutions:\n  ha_host: "192.0.2.10"\npackages:\n  panel: !include glucocraft-panel.yaml\n',
        encoding="utf-8",
    )
    (tmp_path / "glucocraft-panel.yaml").write_text("substitutions:\n  x: y\nesphome:\n  name: p\n", encoding="utf-8")
    (tmp_path / "glucocraft-other.yaml").write_text("# esphome: in a comment only\nlvgl_like: 1\n", encoding="utf-8")
    assert list(_panel_configs(tmp_path)) == ["glucocraft-panel.yaml"]


def test_no_display_reads_a_removed_attribute():
    for name, text in _sources().items():
        assert "statistics_at" not in text, name
        assert "minutes_since_reading" not in text, name
        assert "data_hours" not in text, name
        # the reading count and the reading time are read from the Last reading time sensor only
        assert not re.search(r"data_history['\"]\s*,\s*['\"]reading_count", text), name
        assert not re.search(r"active_source['\"]\s*,\s*['\"](?:reading_count|last_reading_ts)", text), name
        assert "last_measurement" not in text.replace("_last_measurement", ""), name


def test_the_displays_read_the_time_of_the_statistics_from_the_weekly_statistics():
    ui = (WWW / "glucocraft-ui.js").read_text(encoding="utf-8")
    strategy = (WWW / "glucocraft-strategy.js").read_text(encoding="utf-8")
    assert 'weekly: "sensor.glucocraft_weekly_statistics"' in ui
    assert 'this.attr(ENT.weekly, "computed_at")' in ui
    # the Maintenance page states it once, in its status headline (glucocraft-ui.js), not again in its text
    assert "state_attr('sensor.glucocraft_weekly_statistics', 'computed_at')" not in strategy
