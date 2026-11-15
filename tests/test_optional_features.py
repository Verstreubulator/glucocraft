# License: AGPL-3.0-only (LICENSE).
"""The two optional features (Configure > Optional features): Custom ranges and goals, and
Separate statistics entities, each off in a new installation.

Rules under test:
- Every combination of the two switches: the entities created (a new installation 28, an installation with both on
  134), the ranges, goals and weekly pages in effect, the zone of the glucose value, the panel feed's ranges, the Configuration attributes, the dashboard (the Custom Ranges page only while
  Custom ranges and goals is on) and the CGM Report's custom appendix (refused while it is off).
- Turning a switch off removes its entities from the entity registry; turning it on again creates the same entities
  with the same unique ids and entity ids. The stored ranges and goals are kept while Custom ranges and goals is off.
- Nothing GlucoCraft shows depends on an optional entity: the cards, the dashboard and the wall panels read the period
  summary sensors (<period> Consensus Ranges, <period> Custom Ranges), whose attributes carry what they read; the
  Custom Ranges page shows its numbers from the summary sensors alone, in both units.
"""

from __future__ import annotations

import asyncio
import json
import re
import textwrap
import time
import types

import pytest
from glucocraft import binary_sensor as bs
from glucocraft import const as C
from glucocraft import http_api as H
from glucocraft import sensor as se
from glucocraft import settings as S
from glucocraft.units import round_half_up

from conftest import PANELS, PERIOD_KEYS, REPO, FakeRegistry
from node_runner import node_json
from test_entity_ids import _load_init
from test_settings_harness import ENTRY_ID, build, entry, tz_patch  # noqa: F401 (the fixture)

pytestmark = pytest.mark.usefixtures("tz_patch")

WWW = REPO / "custom_components" / "glucocraft" / "www"
CUSTOM, STATS = C.CONF_CUSTOM_RANGES_AND_GOALS, C.CONF_SEPARATE_STATISTICS
COMBINATIONS = [(False, False), (True, False), (False, True), (True, True)]
# custom values stored in every entry below: in use only while Custom ranges and goals is on
STORED = {C.CONF_RANGE_LOW: 80, C.CONF_RANGE_TIGHT_LOW: 80, C.CONF_RANGE_HIGH: 160, C.CONF_GOAL_TBR: 3}

# the entities of each optional feature, by entity id
CUSTOM_IDS = {
    f"sensor.glucocraft_{p}_{m}"
    for p in PERIOD_KEYS
    for m in (
        "custom_ranges",
        "custom_time_in_range",
        "custom_time_in_tight_range",
        "custom_time_low",
        "custom_time_high",
        "custom_time_very_low",
        "custom_time_very_high",
    )
}
STATS_IDS = {
    f"sensor.glucocraft_{p}_{m}"
    for p in PERIOD_KEYS
    for m in ("mean", "standard_deviation", "cv", "minimum", "maximum", "sensor_active", "readings")
} | {f"sensor.glucocraft_{p}_{m}" for p in ("14d", "30d", "90d", "180d") for m in ("gmi", "gri")}
ALWAYS = 28  # 19 sensors (10 core, 7 consensus ranges, Weekly statistics, Change over time) and 9 binary sensors


def _run(tmp_path, custom: bool, stats: bool, **changes):
    return build(tmp_path, entry(**{CUSTOM: custom, STATS: stats, **STORED, **changes}))


def _created(run) -> dict:
    """The entities the entry creates, by entity id."""
    ent = types.SimpleNamespace(entry_id=ENTRY_ID, data=run.data)
    out = list(se.split_by_features(run.c, se.entity_groups(run.c, ent))[0]) + list(bs.build_entities(run.c, ent))
    for e in out:
        e.coordinator = run.c  # the stub CoordinatorEntity keeps nothing
    return {e.entity_id: e for e in out}


# ------------------------------------------------------------------------------------------------ the entities
@pytest.mark.parametrize("custom, stats", COMBINATIONS)
def test_the_entities_of_each_combination(tmp_path, custom, stats):
    ids = set(_created(_run(tmp_path, custom, stats)))
    assert len(CUSTOM_IDS) == 49 and len(STATS_IDS) == 57
    assert (ids & CUSTOM_IDS) == (CUSTOM_IDS if custom else set())
    assert (ids & STATS_IDS) == (STATS_IDS if stats else set())
    always = ids - CUSTOM_IDS - STATS_IDS
    assert len(always) == ALWAYS
    assert {f"sensor.glucocraft_{p}_consensus_ranges" for p in PERIOD_KEYS} <= always
    assert len(ids) == ALWAYS + 49 * custom + 57 * stats  # a new installation 28, both on 134


def test_a_new_installation(tmp_path):
    """The setup stores both switches off: only the entities that every installation has; an entry without the
    switches (edited by hand) reads both as off."""
    new = build(tmp_path / "a", entry())
    assert len(_created(new)) == ALWAYS
    without = {k: v for k, v in entry(**STORED).items() if k not in (CUSTOM, STATS)}
    assert len(_created(build(tmp_path / "b", without))) == ALWAYS


def test_the_custom_summary_carries_every_custom_value(tmp_path):
    """<period> Custom Ranges: the custom time-in-band values (as the separate entities) and the range-independent
    values (as Consensus Ranges); no GRI (it is defined on the consensus ranges only)."""
    run = _run(tmp_path, True, True)
    ents = _created(run)
    for p in PERIOD_KEYS:
        a = ents[f"sensor.glucocraft_{p}_custom_ranges"].extra_state_attributes
        cons = ents[f"sensor.glucocraft_{p}_consensus_ranges"].extra_state_attributes
        # the state is the time in range rounded once to one decimal; the attribute keeps the exact value
        state = ents[f"sensor.glucocraft_{p}_custom_ranges"].native_value
        if not a["enough_data"]:  # the enough-data rule: no value, no state, on both summary sensors
            assert a["has_data"] is True and state is None and a["mean"] is None and cons["mean"] is None, p
            continue
        assert a["has_data"] is True and state == round_half_up(a["time_in_range"], 1)
        for key, ent in (
            ("time_in_range", "custom_time_in_range"),
            ("time_in_tight_range", "custom_time_in_tight_range"),
            ("time_low", "custom_time_low"),
            ("time_high", "custom_time_high"),
            ("time_very_low", "custom_time_very_low"),
            ("time_very_high", "custom_time_very_high"),
            ("mean", "mean"),
            ("std_dev", "standard_deviation"),
            ("cv", "cv"),
            ("min_value", "minimum"),
            ("max_value", "maximum"),
            ("sensor_active", "sensor_active"),
            ("readings", "readings"),
        ):
            # the percentage entities' state is the exact value rounded once to one decimal, the mean's and the
            # standard deviation's rounded once to the step shown in the display unit; the attribute exact
            exact, entity = a[key], ents[f"sensor.glucocraft_{p}_{ent}"]
            if key.startswith("time_") or key in ("cv", "sensor_active"):
                shown = round_half_up(exact, 1)
            elif key in ("mean", "std_dev"):
                shown = se._state_glucose(exact, entity.coordinator.display_unit)
            else:
                shown = exact
            assert shown == entity.native_value, (p, key)
        assert (
            a["time_below_low"]
            == ents[f"sensor.glucocraft_{p}_custom_time_low"].coordinator.period_stats[p].time_below_low
        )
        shared = ("mean", "std_dev", "cv", "gmi", "gmi_mmol_mol", "sensor_active", "sensor_active_ok", "readings")
        for key in (*shared, "limit_readings"):
            assert a[key] == cons[key], (p, key)
        if p in ("14d", "30d", "90d", "180d"):
            assert a["gmi"] == ents[f"sensor.glucocraft_{p}_gmi"].native_value
            assert cons["gri"] == ents[f"sensor.glucocraft_{p}_gri"].native_value
        else:  # the GRI only from 14 days
            assert cons["gri"] is None and cons["gri_zone"] is None and cons["gri_hypo"] is None
        assert not any("gri" in key for key in a)
        # the bands differ: custom low 80, consensus 70
        assert a["time_in_range"] != cons["time_70_180"] or p == "6h"


# ------------------------------------------------------------------------------------------------ the outputs
@pytest.mark.parametrize("custom, stats", COMBINATIONS)
def test_ranges_goals_zone_and_feed_follow_the_custom_feature(tmp_path, custom, stats):
    run = _run(tmp_path, custom, stats)
    c, cfg = run.c, run.config()
    assert cfg[CUSTOM] is custom and cfg[STATS] is stats
    low, tight_low, high = (80, 80, 160) if custom else (70, 70, 180)
    assert (cfg["ranges"]["low"], cfg["ranges"]["tight_low"], cfg["ranges"]["high"]) == (low, tight_low, high)
    assert c.uses_custom_ranges() is custom and c.goals_custom() is custom
    assert c.goal_tbr == (3 if custom else 4)
    assert cfg["weekly_pages"]["offered"] is custom
    # the zone of 75 mg/dL: low with the custom ranges, in the tight range with the consensus ranges
    c.current_glucose, c.current_limit = 75.0, None
    assert c.glucose_zone == ("low" if custom else "tight_range")
    # the wall panels' ranges (GLU2 RANGES: very low, very high, low, high, tight low, tight high)
    assert run.feed()["ranges"] == pytest.approx((54, 250, low, high, tight_low, 140))


def test_the_stored_ranges_and_goals_are_kept_while_off(tmp_path):
    data = entry(**{CUSTOM: False, **STORED})
    run = build(tmp_path, data)
    assert run.c.range_low == 70 and S.ranges(data)[C.CONF_RANGE_LOW] == 70
    assert all(run.data[k] == v for k, v in STORED.items())  # never removed
    on = {**data, CUSTOM: True}
    assert S.ranges(on)[C.CONF_RANGE_LOW] == 80 and S.setting(on, C.CONF_GOAL_TBR) == 3
    assert S.stored_custom_values(data) is True  # whatever the switch


@pytest.mark.parametrize("custom", [False, True])
def test_the_custom_appendix_is_refused_while_off(tmp_path, monkeypatch, custom):
    run = _run(tmp_path, custom, False)
    monkeypatch.setattr(H, "web", types.SimpleNamespace(Response=lambda **kw: kw))
    for view in (H.GlucoCraftReportView, H.GlucoCraftReportPdfView):
        if view is H.GlucoCraftReportPdfView and custom:
            continue  # the PDF itself is tested with the report
        out = run.api(view, {"days": "14", "include_custom_appendix": "1"})
        if custom:
            assert out["include_custom_appendix"] is True
        else:
            assert out["status"] == 400 and "Optional features" in out["text"], view
    # the action: refused with its own message while off
    init = _load_init()

    class Refused(Exception):
        def __init__(self, **kw):
            self.kw = kw

    monkeypatch.setattr(init, "ServiceValidationError", Refused)
    monkeypatch.setattr(init, "_coordinator_for", lambda hass: run.c)
    printed = []

    async def async_print(*args):
        printed.append(args)
        return {"ok": True, "message": "sent"}

    run.c.reports = types.SimpleNamespace(async_print=async_print)

    async def call(*a, **kw):
        return None

    hass = types.SimpleNamespace(services=types.SimpleNamespace(async_call=call))
    action = init._handle_print(hass, types.SimpleNamespace(data={"include_custom_appendix": True}))
    if custom:
        asyncio.run(action)
        assert printed and printed[-1][4] is True  # the custom appendix asked for
    else:
        with pytest.raises(Refused) as err:
            asyncio.run(action)
        assert err.value.kw["translation_key"] == "custom_appendix_off" and not printed
    strings = json.loads((REPO / "custom_components/glucocraft/strings.json").read_text(encoding="utf-8"))
    assert "Optional features" in strings["exceptions"]["custom_appendix_off"]["message"]


# ------------------------------------------------------------------------------------------------ off and on again
def _setup(run, registry, monkeypatch) -> list:
    """The platforms' setup for the run's entry, as __init__.async_setup_entry runs it: both platforms create their
    entities (registered in `registry`), then the registry entries the setup did not create, those of a feature that
    is off among them, are removed (stale_entities.py); returns the entities added (sensors and binary sensors)."""
    from glucocraft import stale_entities

    monkeypatch.setattr(stale_entities.er, "async_get", lambda hass: registry)
    run.hass.data = getattr(run.hass, "data", None) or {}
    ent = types.SimpleNamespace(entry_id=ENTRY_ID, data=run.data, runtime_data=run.c)
    stale_entities.start_setup(run.hass, ent)
    added: list = []
    asyncio.run(se.async_setup_entry(run.hass, ent, added.extend))
    binary: list = []
    asyncio.run(bs.async_setup_entry(run.hass, ent, binary.extend))
    registry.add([*added, *binary], config_entry_id=ENTRY_ID)
    stale_entities.async_remove_stale_entities(run.hass, ent)
    return [*added, *binary]


@pytest.mark.parametrize("switch, ids", [(CUSTOM, CUSTOM_IDS), (STATS, STATS_IDS)])
def test_off_and_on_again_restores_the_same_entities(tmp_path, monkeypatch, switch, ids):
    registry = FakeRegistry()
    now = int(time.time())
    both = {CUSTOM: True, STATS: True}
    before = {
        e._attr_unique_id: e.entity_id
        for e in _setup(build(tmp_path / "a", entry(**both), now=now), registry, monkeypatch)
    }
    assert ids <= set(registry.ids.values())
    # off: removed from the registry, the others kept
    _setup(build(tmp_path / "b", entry(**{**both, switch: False}), now=now), registry, monkeypatch)
    assert not ids & set(registry.ids.values())
    assert set(registry.ids.values()) == set(before.values()) - ids
    # on again: the same unique ids under the same entity ids
    again = _setup(build(tmp_path / "c", entry(**both), now=now), registry, monkeypatch)
    assert {e._attr_unique_id: e.entity_id for e in again} == before
    assert registry.ids == before


def test_turning_custom_off_also_removes_the_custom_summaries(tmp_path, monkeypatch):
    registry = FakeRegistry()
    _setup(_run(tmp_path / "a", True, False), registry, monkeypatch)
    assert {f"sensor.glucocraft_{p}_custom_ranges" for p in PERIOD_KEYS} <= set(registry.ids.values())
    _setup(_run(tmp_path / "b", False, False), registry, monkeypatch)
    sensors = [e for e in registry.ids.values() if e.startswith("sensor.")]
    assert not any("custom" in e for e in sensors) and len(registry.ids) == ALWAYS


# ------------------------------------------------------------------------------------------------ what is read
OPTIONAL_SUFFIX = re.compile(
    r"(mean|standard_deviation|cv|minimum|maximum|sensor_active|readings|gri|gmi|custom_time_\w+)\b"
)


def test_no_card_or_page_reads_an_optional_entity():
    """The frontend files build no entity id of an optional feature (the summary sensors only)."""
    for path in sorted(WWW.glob("*.js")):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"sensor\.glucocraft_(\$\{[^}]+\}|\d+[hd])_([a-z_]+)", src):
            assert not OPTIONAL_SUFFIX.fullmatch(m.group(2)), (path.name, m.group(0))
        assert "custom_time_" not in src, path.name


LCD_PANELS = (
    "glucocraft-panel.yaml",
    "glucocraft-waveshare-lcd5.yaml",
    "glucocraft-waveshare-p4-7b.yaml",
    "glucocraft-waveshare-lcd7.yaml",
    "glucocraft-guition-35.yaml",
)


def _summary_sensors(text: str) -> dict:
    """The panel's Home Assistant sensors of the 6-hour summaries: id -> (entity id, attribute)."""
    pat = re.compile(r"id: (stats_6h_\w+)\n    entity_id: (sensor\.glucocraft_6h_\w+)\n    attribute: (\w+)\n")
    return {m.group(1): (m.group(2), m.group(3)) for m in pat.finditer(text)}


def test_the_panels_read_the_summary_of_the_range_set_in_use(tmp_path):
    if PANELS is None:
        pytest.skip("the example panel configurations are not available")
    ents = _created(_run(tmp_path, True, False))
    attrs = {
        "consensus": set(ents["sensor.glucocraft_6h_consensus_ranges"].extra_state_attributes),
        "custom": set(ents["sensor.glucocraft_6h_custom_ranges"].extra_state_attributes),
    }
    square = None
    for name in LCD_PANELS:
        text = (PANELS / name).read_text(encoding="utf-8")
        sensors = _summary_sensors(text)
        assert len(sensors) == 22, name  # 11 statistics from each summary
        for sid, (entity_id, attribute) in sensors.items():
            kind = sid.split("_")[2]
            assert entity_id == f"sensor.glucocraft_6h_{kind}_ranges" and attribute in attrs[kind], (name, sid)
        for m in re.finditer(r"entity_id: sensor\.glucocraft_(\d+[hd])_([a-z_]+)", text):
            assert m.group(2) in ("consensus_ranges", "custom_ranges"), (name, m.group(0))
        # the range set from the configuration attribute; the values chosen by it
        assert "attribute: custom_ranges_and_goals" in text and 'id(gc_custom_ranges_on) = x == "on"' in text
        assert "const bool custom = id(gc_custom_ranges_on);" in text, name
        square = square or sensors
        assert sensors == square, name  # every LCD panel reads the same


def test_the_display_card_reads_the_same_summary(tmp_path):
    ents = _created(_run(tmp_path, True, False))
    src = (WWW / "glucocraft-display-card.js").read_text(encoding="utf-8")
    table = re.search(r"const SUMMARY_6H = \{(.*?)\};", src, re.DOTALL).group(1)
    pairs = re.findall(r'\w+: \["(\w+)", "(\w+)"\]', table)
    assert len(pairs) == 11
    cons = ents["sensor.glucocraft_6h_consensus_ranges"].extra_state_attributes
    cust = ents["sensor.glucocraft_6h_custom_ranges"].extra_state_attributes
    for c, u in pairs:
        assert c in cons and u in cust, (c, u)
    # a reading at the sensor's limit is no value: no limit marks beside min and max
    assert "_is_limit" not in src
    assert not {"min_is_limit", "max_is_limit"} & (set(cons) | set(cust))
    card = {("consensus", c) for c, _ in pairs} | {("custom", u) for _, u in pairs}
    # the same values as every LCD panel
    if PANELS:
        for name in LCD_PANELS:
            text = (PANELS / name).read_text(encoding="utf-8")
            assert {(e.split("_")[2], a) for e, a in _summary_sensors(text).values()} == card, name


# ------------------------------------------------------------------------------------------------ the dashboard
def _dashboard(config: dict) -> dict:
    states = {"sensor.glucocraft_configuration": {"state": "consensus_ranges", "attributes": config}}
    script = textwrap.dedent(f"""
        globalThis.HTMLElement = class {{}}; const reg = {{}};
        globalThis.customElements = {{ get: (n) => reg[n], define: (n, c) => {{ reg[n] = c; }} }};
        globalThis.location = {{ pathname: "/glucocraft-home/start" }};
        await import({json.dumps((WWW / "glucocraft-strategy.js").as_uri())});
        const d = await reg["ll-strategy-dashboard-glucocraft"].generate({{}}, {{ states: {json.dumps(states)},
          devices: {{}}, entities: {{}} }});
        process.stdout.write("\\u0001" + JSON.stringify(d));
    """)
    return node_json(script)


@pytest.mark.parametrize("custom, stats", [*COMBINATIONS, (None, None)])
def test_the_dashboard_of_each_combination(tmp_path, custom, stats):
    """The Custom Ranges page only while Custom ranges and goals is on (and while the configuration is not known yet,
    when its start-page row decides); Settings Status names both switches; no page names an optional entity."""
    config = _run(tmp_path, bool(custom), bool(stats)).config()
    if custom is None:
        config = {}  # GlucoCraft reloading: no attributes yet
    dash = _dashboard(json.loads(json.dumps(config, default=str)))
    paths = [v["path"] for v in dash["views"]]
    assert ("custom" in paths) is (custom is not False)
    assert paths[:2] == ["start", "today"] and "consensus" in paths
    text = json.dumps(dash, ensure_ascii=False)
    for entity_id in CUSTOM_IDS | STATS_IDS:
        assert entity_id not in text, entity_id
    settings = json.dumps(next(v for v in dash["views"] if v["path"] == "settings"), ensure_ascii=False)
    assert (
        "Optional features:" in settings
        and "c.custom_ranges_and_goals" in settings
        and "c.separate_statistics" in settings
    )


# ------------------------------------------------------------------------------------------------ Custom Ranges
def _render(states: dict, cards: list[dict]) -> list[str]:
    """The text of each card (its HTML without tags) drawn from `states` alone, with minimal browser stand-ins."""
    script = textwrap.dedent(f"""
        globalThis.window = globalThis; globalThis.addEventListener = () => {{}};
        globalThis.HTMLElement = class {{
          constructor() {{ this.style = {{}}; }}
          attachShadow() {{ this.shadowRoot = {{ innerHTML: "", querySelector: () => ({{}}), querySelectorAll: () => [] }};
            return this.shadowRoot; }}
          getBoundingClientRect() {{ return {{ width: 600 }}; }}
          dispatchEvent() {{}}
        }};
        const reg = {{}};
        globalThis.customElements = {{ get: (n) => reg[n], define: (n, c) => {{ reg[n] = c; }} }};
        globalThis.ResizeObserver = class {{ observe() {{}} disconnect() {{}} }};
        await import({json.dumps((WWW / "glucocraft-ui.js").as_uri())});
        const hass = {{ states: {json.dumps(states)}, language: "en" }};
        const out = [];
        for (const cfg of {json.dumps(cards)}) {{
          const el = new (reg["glucocraft-" + cfg.type])();
          el.setConfig(cfg);
          // a graph's header (the graph itself needs the readings files): the period's numbers and the band key
          if (cfg.header) {{ el._hass = hass; out.push(el.header(cfg.title, cfg.hours, true, 168, el.ranges(cfg.basis))); continue; }}
          el.hass = hass;
          out.push(el.shadowRoot.innerHTML);
        }}
        process.stdout.write("\\u0001" + JSON.stringify(out));
    """)
    html = node_json(script)
    return [re.sub(r"\s+", " ", re.sub(r"<style>.*?</style>|<[^>]+>", " ", h, flags=re.DOTALL)) for h in html]


@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
def test_the_custom_ranges_page_reads_the_summary_sensors_alone(tmp_path, unit):
    """With Separate statistics entities off (no per-statistic entity exists), the readout, the core measures, the two
    statistics tables and the graph headers of Custom Ranges show the custom values of the summary sensors."""
    run = _run(tmp_path, True, False, unit=unit)
    ents = _created(run)
    states = {
        eid: {"state": str(e.native_value), "attributes": json.loads(json.dumps(e.extra_state_attributes, default=str))}
        for eid, e in ents.items()
        if eid.endswith(("_custom_ranges", "_consensus_ranges"))
    }
    states["sensor.glucocraft_configuration"] = {"state": "custom_ranges", "attributes": run.config()}
    states = json.loads(json.dumps(states, default=str))
    assert not set(states) & (STATS_IDS | (CUSTOM_IDS - {f"sensor.glucocraft_{p}_custom_ranges" for p in PERIOD_KEYS}))
    cards = [
        {"type": "readout", "view": "personal", "hero": True},
        {"type": "stats-table", "periods": ["6h", "24h", "7d", "14d"], "basis": "yours"},
        {"type": "stats-table", "periods": ["14d", "30d", "90d", "180d"], "basis": "yours"},
        {"type": "history", "hours": 336, "basis": "yours", "title": "T", "header": True},
    ]
    readout, table, table_long, header = _render(states, cards)
    a14 = ents["sensor.glucocraft_14d_custom_ranges"].extra_state_attributes
    mm = unit == "mmol/L"

    def pct(v, d=0):
        return f"{v:.{d}f}%"

    def glucose(mg):
        return f"{mg / 18.016:.1f}" if mm else str(round(mg))

    assert pct(a14["time_in_range"]) in readout and ("80–160" if not mm else "4.4–8.9") in readout
    for p, text in (("6h", table), ("14d", table), ("180d", table_long)):
        a = ents[f"sensor.glucocraft_{p}_custom_ranges"].extra_state_attributes
        if not a["enough_data"]:  # the enough-data rule: no value is published for this period
            assert a["mean"] is None and a["time_in_range"] is None and a["sensor_active_ok"] is False, p
            continue
        for key, dec in (("time_in_range", 0), ("time_below_low", 1), ("time_very_low", 1), ("cv", 0)):
            assert pct(a[key], dec) in text, (p, key)
        assert glucose(a["mean"]) in text and glucose(a["std_dev"]) in text, p
    assert "---" not in table.split("GMI")[0]  # every value up to the GMI, which needs 14 days, is shown
    assert f"{glucose(a14['mean'])} {unit}" in header and pct(a14["time_in_range"]) in header
