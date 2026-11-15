# License: AGPL-3.0-only (LICENSE).
"""Every displayed number is rounded once, from the unrounded value, by the one rule (units.round_half_up).

Rules under test:
- The 5-minute delta is published unrounded (state and value_mgdl); each display rounds it once in its display unit,
  halves away from zero with the float allowance: a change of exactly 20.5 mg/dL is "+21", -17.38 mg/dL is "-1.0"
  mmol/L (never converted from a rounded -17).
- The cards round a double (an attribute, a value calculated in the browser) with the 1e-9 allowance of
  units.round_half_up, and only a GLU2 file's float32 value with the panels' float32 allowance (rh1).
- A difference shown on the dashboard is the difference of the values as shown beside it; the direction and the
  5-point mark follow it. The Change Over Time table, its headline line and the core tiles compare with one earlier
  window for every measure: 22-26 weeks ago when it has data, else 9-13 weeks ago; a measure without a value there
  has no direction ("---").
- A GMI in mmol/mol is the value from the mean glucose (gmi_mmol_mol), not converted from the rounded GMI in %; the
  conversion from % only for axes and goals.
- The dashboard's text pages print a stored limit as shown: whole mg/dL, halves away from zero.
- The banners decide "no current data" as the display card does: GlucoCraft's flag, or the reading's age past the
  limit when Home Assistant cannot update the flag.
- The defaults the dashboard files show while the configuration sensor has no attributes, the factor 18.016 and the
  500 mg/dL bound are GlucoCraft's own constants, each written once per file.
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

import jinja2
import pytest
from glucocraft import const as C
from glucocraft import coordinator as CO
from glucocraft import limits, units
from glucocraft import sensor as se

from conftest import real_coordinator
from node_runner import needs_node
from test_accessibility import _dom
from test_accessibility_checks import JS, _eval

WWW = Path(__file__).resolve().parents[1] / "custom_components" / "glucocraft" / "www"


# ------------------------------------------------------------------------------------------------ the 5-minute delta
def _ramp(monkeypatch, tmp_path, rate: float) -> CO.GlucoCraftCoordinator:
    """A 1-minute source rising `rate` mg/dL per minute for 45 minutes (values stored to 0.1 mg/dL, measured 23 s past
    the minute), processed by the real coordinator."""
    t0, clock = 1_790_000_000, {"now": 1_790_000_000}
    monkeypatch.setattr(CO.time, "time", lambda: clock["now"])
    c = real_coordinator(tmp_path, active_source_entity="sensor.p", active_source="P")
    base = 220 - rate * 30
    for k in range(46):
        t = t0 + 23 + k * 60
        clock["now"] = t + 20
        c._process_reading(round((base + rate * (t - t0) / 60) * 10) / 10, t, "sensor.p", "P", True)
    return c


@pytest.mark.parametrize("rate, shown", [(4.10, 21), (4.70, 24), (5.30, 27)])
def test_the_delta_is_published_unrounded_and_an_exact_half_shows_rounded_up(monkeypatch, tmp_path, rate, shown):
    c = _ramp(monkeypatch, tmp_path, rate)
    assert c.delta_5min == pytest.approx(5 * rate, abs=1e-9) and c.delta_5min == c.change_5min
    assert int(units.round_half_up(c.delta_5min)) == shown
    sensor = se.GlucoCraftDelta5Min.__new__(se.GlucoCraftDelta5Min)
    sensor.coordinator = c
    assert sensor.native_value == c.delta_5min and sensor.extra_state_attributes["value_mgdl"] == c.delta_5min


@needs_node
def test_the_cards_round_the_delta_once_in_the_display_unit():
    out = _eval(
        "display-card",
        "const mg = (x) => { GC.mmol = false; return gDelta(x); }, mm = (x) => { GC.mmol = true; return gDelta(x); };"
        "return [mg(20.5), mg(20.499999999999996), mg(23.499999999999996), mg(-0.4), mm(-17.38), mm(0.9008),"
        " mm(-0.9007)];",
    )
    # -17.38 / 18.016 = -0.9647: "-1.0", where a whole -17 first would give "-0.9"; 0.9008 mg/dL is 0.05 mmol/L;
    # a delta that rounds to zero has no sign (a sign says a direction, and zero has none)
    assert out == ["+21", "+21", "+24", "0", "-1.0", "+0.1", "0.0"]


@needs_node
def test_the_statistics_card_rounds_the_delta_once_from_its_value_in_mg_dl():
    out = _eval(
        "ui",
        textwrap.dedent("""
        const box = {}, c = new __reg["glucocraft-statistics"](); c.setConfig({});
        c.shadowRoot = { innerHTML: "", querySelector: (s) => (box[s] ||= { innerHTML: "" }) };
        c._hass = { states: {
          "sensor.glucocraft_configuration": { attributes: { unit: "mmol/L" } },
          "sensor.glucocraft_5_minute_delta": { state: "-0.9", attributes: { value_mgdl: -17.38, unit: "mmol/L" } } } };
        GC.mmol = true; c.draw(); return box[".lr"].innerHTML;
    """),
    )
    assert "-1.0 mmol/L" in out and "-0.9 mmol/L" not in out


# ------------------------------------------------------------------------------------------------ doubles and float32
@needs_node
def test_a_double_is_rounded_with_the_backend_allowance_and_a_file_value_with_the_float32_one():
    out = _eval(
        "ui",
        "const S = globalThis.glucocraftShared; S.settings.mmol = true;"
        "const r = [S.gF(145.02879309888178), S.gF32(145.02879309888178), S.gF(112.6), S.gF32(Math.fround(112.6)),"
        " S.gF(Math.fround(112.6))]; S.settings.mmol = false; return r;",
    )
    # the backend: 145.02879309888178 / 18.016 = 8.04999... -> 8.0; the float32 allowance would give 8.1
    assert units.fmt(145.02879309888178, units.UNIT_MMOLL) == "8.0"
    assert out == ["8.0", "8.1", "6.3", "6.3", "6.2"]
    for name in ("ui", "report", "display-card"):
        assert "rh1(" not in JS[name], name  # only the shared gD32 / gF32 use the float32 allowance


# ------------------------------------------------------------------------------------------------ differences as shown
def _change(compare: dict, unit: str = "mg/dL", a1c: str = "percent", weeks14: list | None = None) -> list[list[str]]:
    """The Change Over Time table's rows as cell texts."""
    html = _eval(
        "ui",
        textwrap.dedent(f"""
        const t = new __reg["glucocraft-change"](); t.setConfig({{ basis: "consensus" }});
        t._hass = {{ states: {{
          "sensor.glucocraft_configuration": {{ attributes: {{ unit: {json.dumps(unit)}, a1c_unit: {json.dumps(a1c)} }} }},
          "sensor.glucocraft_change_over_time": {{ attributes: {{ consensus: {{ compare: {json.dumps(compare)},
            weeks14: {json.dumps(weeks14 or [])} }} }} }} }} }};
        GC.mmol = {json.dumps(unit == "mmol/L")}; GC.a1c = {json.dumps(a1c)};
        t.draw(); return t.shadowRoot.innerHTML;
    """),
    )
    rows = []
    for row in html.split("<tr>")[2:]:
        cells = re.split(r"<t[hd][^>]*>", row)[1:]
        rows.append([re.sub(r"<span class=\"sr\">.*?</span>|<[^>]*>", "", c).strip() for c in cells])
    return rows


def _row(rows: list[list[str]], label: str) -> list[str]:
    return next(r for r in rows if r[0].startswith(label))


@needs_node
def test_a_difference_is_that_of_the_values_as_shown():
    # 100.46 and 100.0 mg/dL are both shown as 100: no difference, no direction (not "+1 mg/dL, higher")
    mean = _row(_change({"last_4_weeks": {"mean": 100.46}, "weeks_22_26_ago": {"mean": 100.0}}), "Mean")
    assert [mean[1], mean[4], mean[5], mean[6]] == ["100 mg/dL", "100 mg/dL", "0 mg/dL", "No change"]
    # 70.46% and 65.44% are shown as 70.5% and 65.4%: 5.1 points (not 5.0), and the mark with it
    tir = _row(_change({"last_4_weeks": {"tir": 70.46}, "weeks_22_26_ago": {"tir": 65.44}}), "Time in Range")
    assert tir[1] == "70.5%" and tir[4] == "65.4%" and tir[5].startswith("+5.1 percentage points ‡")
    # 4.96 points unrounded, 5.0 shown: the 5-point mark goes with the number shown
    near = _row(_change({"last_4_weeks": {"tir": 70.46}, "weeks_22_26_ago": {"tir": 65.50}}), "Time in Range")
    assert near[5].startswith("+5.0 percentage points ‡")


@needs_node
def test_the_headline_and_the_core_tiles_take_the_difference_as_shown():
    out = _eval(
        "ui",
        "return [gcTirChange({ last_4_weeks: { tir: 70.46 }, weeks_22_26_ago: { tir: 70.04 } }),"
        " gcTirChange({ last_4_weeks: { tir: 70.46 }, weeks_9_13_ago: { tir: 70.54 } }),"
        " gcPointsShown(70.46, 65.44)];",
    )
    # 70.5% and 70.0% as shown: +0.5 points (0.42 unrounded); 70.5% and 70.5%: no change (-0.08 unrounded)
    assert "+0.5" in out[0] and "22–26" in out[0]
    assert "+" not in out[1] and "-0" not in out[1] and "9–13" in out[1]
    assert out[2] == 5.1


@needs_node
def test_one_earlier_window_for_the_whole_table():
    """22-26 weeks ago has data but no GMI (below 70% sensor active): the GMI row has no direction, it does not
    compare with 9-13 weeks ago."""
    cmp = {
        "last_4_weeks": {"tir": 80, "mean": 120, "gmi": 6.2, "gmi_mmol_mol": 44},
        "weeks_9_13_ago": {"tir": 78, "mean": 125, "gmi": 6.3, "gmi_mmol_mol": 45},
        "weeks_22_26_ago": {"tir": 75, "mean": 130, "gmi": None, "gmi_mmol_mol": None},
    }
    rows = _change(cmp)
    gmi = _row(rows, "Glucose Management Indicator")
    assert gmi[3] == "-0.1 percentage points" and gmi[5] == "---" and gmi[6] == "---"
    assert _row(rows, "Time in Range")[6] == "Higher than 22–26 weeks ago"
    # without data 22-26 weeks ago, every measure compares with 9-13 weeks ago
    rows = _change({k: v for k, v in cmp.items() if k != "weeks_22_26_ago"})
    assert (
        _row(rows, "Glucose Management Indicator")[6] == "Lower than 9–13 weeks ago"
        and _row(rows, "Mean")[6] == "Lower than 9–13 weeks ago"
    )


# ------------------------------------------------------------------------------------------------ GMI in mmol/mol
@needs_node
def test_a_weekly_gmi_in_mmol_mol_comes_from_the_mean():
    """A mean of 72.0 mg/dL: GMI 5.0% and 32 mmol/mol (12.71 + 4.70587 x 72.0 / 18.016 = 31.52); converted from the
    rounded 5.0% it would be 31."""
    assert units.gmi_mmol_mol(72.0) == 32
    weeks = [{"gmi": 5.0, "gmi_mmol_mol": 32, "mean": 72.0}, {"gmi": 5.4, "gmi_mmol_mol": 35, "mean": 87.0}]
    out = _eval(
        "ui",
        textwrap.dedent(f"""
        const c = new __reg["glucocraft-change-chart"](); c.setConfig({{ metric: "gmi", basis: "consensus" }});
        c._w = 600; GC.a1c = "mmol_mol";
        c._hass = {{ states: {{ "sensor.glucocraft_configuration": {{ attributes: {{ a1c_unit: "mmol_mol" }} }},
          "sensor.glucocraft_change_over_time": {{ attributes: {{ consensus: {{ compare: {{}},
            weeks14: {json.dumps(weeks)}, weeks7: [], week_ends: [] }} }} }} }} }};
        c.draw(); return c.shadowRoot.innerHTML;
    """),
    )
    said = re.search(r'aria-label="([^"]*)"', out).group(1)
    assert "from 32 millimoles per mole to 35 millimoles per mole" in said, said
    # the display card's weekly GMI page: the same value from the week's mean
    card = _eval(
        "display-card",
        "const c = new __reg['glucocraft-display-card'](); c.setConfig({}); GC.a1c = 'mmol_mol';"
        "c._bf = { trends: [{ mean: Math.fround(72.0), gmi: Math.fround(5.0) }, { mean: Math.fround(87.0), gmi: Math.fround(5.4) }],"
        " dirs: [], std: null };"
        "return c._gmiEnds();",
    )
    assert card == ["32 millimoles per mole", "35 millimoles per mole"]


# ------------------------------------------------------------------------------------------------ the strategy's gv
def _gv(value, unit: str = "mg/dL") -> str:
    gj = re.search(r'const GJ = ((?:"[^"\n]*"(?: \+ [A-Z_]+ \+ )?(?: \+\n\s*)?)+);', JS["strategy"]).group(1)
    template = "".join(
        json.loads(p) if p.startswith('"') else "18.016" for p in re.findall(r'"[^"\n]*"|[A-Z_]{4,}', gj)
    )
    return (
        jinja2.Environment()
        .from_string("{%- set c = cfg -%}" + template + "{{ gv(v) }}")
        .render(cfg={"unit": unit}, v=value)
    )


def test_the_dashboard_text_pages_print_a_stored_limit_as_shown():
    # a limit entered as 8.9 mmol/L is stored as 160.3 mg/dL: "160" in mg/dL, "8.9" in mmol/L
    assert [_gv(160.3), _gv(160.5), _gv(70), _gv(61.3)] == ["160", "161", "70", "61"]
    assert [_gv(160.3, "mmol/L"), _gv(70, "mmol/L")] == ["8.9", "3.9"]


# ------------------------------------------------------------------------------------------------ the banners' clock
@needs_node
@pytest.mark.parametrize("flag, minutes, shown", [("off", 40, True), ("off", 10, False), ("on", 10, True)])
def test_the_banners_count_the_age_of_the_reading_as_the_display_card(flag, minutes, shown):
    res = _dom(f"""
        const states = {{ "sensor.glucocraft_status": {{ state: "current", last_updated: "a", attributes: {{}} }},
          "binary_sensor.glucocraft_no_current_data": {{ state: "{flag}", last_updated: "a",
            attributes: {{ threshold_minutes: 15 }} }},
          "sensor.glucocraft_last_reading_time": {{ state: new Date(Date.now() - {minutes} * 60e3).toISOString(),
            last_updated: "a" }},
          "sensor.glucocraft_configuration": {{ state: "ok", last_updated: "a", attributes: {{ unit: "mg/dL" }} }} }};
        const card = new (customElements.get("glucocraft-banners"))(); card.setConfig({{}});
        card.hass = {{ states }};
        out(card.shadowRoot.querySelector(".list").innerHTML);
    """)
    assert ("No Current Data" in res) == shown


# ------------------------------------------------------------------------------------------------ one place each
def test_defaults_factor_and_bound_are_the_backends_constants():
    docs, strategy, card, shared, ui = JS["docs"], JS["strategy"], JS["display-card"], JS["shared"], JS["ui"]
    # the defaults shown while the configuration sensor has no attributes: once, in the shared module
    assert re.search(r"const STALE_DEFAULT_MIN = (\d+), RETENTION_DAYS = (\d+);", shared).groups() == (
        str(C.DEFAULT_STALE_THRESHOLD),
        str(C.DEFAULT_RETENTION_DAYS),
    )
    ranges = re.search(r"const CONS_RANGES = Object\.freeze\((\{[^}]*\})\);", shared).group(1)
    assert json.loads(re.sub(r"(\w+):", r'"\1":', ranges)) == {
        k.removeprefix("range_"): v for k, v in C.public_agp_ranges().items()
    }
    for js in (docs, strategy, ui):
        assert "STALE_DEFAULT_MIN = " not in js and "RETENTION_DAYS = " not in js
        assert "?? 15" not in js and "?? 200" not in js and "default(15)" not in js and "default(200" not in js
        assert "|| 200" not in js and "very_low: 54" not in js
    assert re.search(r"const GLU_MAX = (\d+);", shared).group(1) == str(limits.DEFAULT_UPPER)
    code = re.sub(r"//[^\n]*|/\*.*?\*/", "", card, flags=re.DOTALL)
    assert "18.016" not in code and not re.search(r"[<>]=? ?500\b", code)
    assert "18.016" not in re.sub(r"//[^\n]*|/\*.*?\*/", "", strategy, flags=re.DOTALL)


def test_the_delta_attribute_is_not_rounded_in_the_backend():
    src = (Path(CO.__file__)).read_text(encoding="utf-8")
    body = src[src.index("def delta_5min") : src.index("@property", src.index("def delta_5min"))]
    assert "int(" not in body and "round" not in body.split('"""')[-1]
