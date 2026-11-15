# License: AGPL-3.0-only (LICENSE).
"""The dashboard cards and the display cards: redraws, reloads, differences, parity with the panels, spoken signs.

- The history graphs: a graph longer than 24 hours keeps what it calculated from the readings until a new file
  arrives, and is not drawn again by the minute; an unchanged graph leaves the page alone. The merged readings are the
  ones the earlier sort-and-filter gave. Without its summary sensor a 6-month graph of 1-minute readings is described
  without an error.
- A GlucoCraft reload (the configuration sensor without attributes) keeps the display unit of the dashboard cards, and
  the display card keeps its ranges and the custom-ranges flag.
- Change Over Time: a difference that rounds to zero is shown without a sign, and its direction is "no change".
- The display card: a weekly page with a single week draws that week's dot, as the panels do.
- The display card's 6-hour page before the first file fills every cell from the 6-hour summary, as the panels do;
  its mmol/L rounding equals the panels' r1_mmol and GlucoCraft's, also at the exact halves of a file's float32.
- Texts: the weekday names come from the catalogue; a GMI goal in mmol/mol uses the GMI rule; a plus or minus sign
  before a number is spoken in words; the pages card's rows are named for where they lead and described by their lines;
  the legends of the marks have a spoken form; the Documentation's header cells name their column.
"""

from __future__ import annotations

import base64
import json
import random
import struct
import textwrap

import pytest
from glucocraft import units

from node_runner import needs_node, node_json
from test_accessibility import CARD, DOM
from test_accessibility_checks import GLU2, JS, UI_STATES, _eval

UI = JS["ui"]


def _ui(body: str) -> object:
    """Run body (a function body) after glucocraft-ui.js, in the test DOM of tests/test_accessibility_checks.py."""
    return _eval("ui", body)


# ------------------------------------------------------------------------------------------------ the history graphs
@needs_node
def test_a_long_graph_keeps_its_readings_and_is_not_drawn_by_the_minute():
    out = _ui(
        GLU2
        + textwrap.dedent(f"""
        const states = {json.dumps(UI_STATES)};
        const hass = {{ states, fetchWithAuth: async () => ({{ ok: true, arrayBuffer: async () => glu2() }}) }};
        const long = new (customElements.get("glucocraft-history"))(); long.setConfig({{ hours: 336 }});
        long._w = 700; long._hass = hass;
        await long.draw();
        const box = long.shadowRoot.querySelector(".g"), first = long._series, drawn = box.innerHTML;
        box.innerHTML = "unchanged";              // a second drawing of the same graph does not touch the page
        await long.draw();
        const same = long._series === first, untouched = box.innerHTML === "unchanged";
        const short = new (customElements.get("glucocraft-history"))(); short.setConfig({{ hours: 6 }});
        const banners = new (customElements.get("glucocraft-banners"))(); banners.setConfig({{}});
        const table = new (customElements.get("glucocraft-stats-table"))(); table.setConfig({{}});
        const daily = new (customElements.get("glucocraft-daily-range"))(); daily.setConfig({{}});
        return {{ same, untouched, svg: drawn.includes("<svg"), timed: [long.timed(), short.timed(), banners.timed(),
          table.timed(), daily.timed()] }};
    """)
    )
    assert out == {"same": True, "untouched": True, "svg": True, "timed": [False, True, True, False, False]}


@needs_node
def test_the_merged_readings_are_those_of_sorting_and_removing_duplicates():
    rng = random.Random(7)
    bf_ts = sorted(rng.sample(range(0, 40000, 7), 3000))
    dl_ts = sorted(rng.sample(range(35000, 41000, 5), 600) + bf_ts[-50:])  # overlaps the full file
    bf = {"ts": bf_ts, "val": [rng.choice([0, 80, 120, 501, 200]) for _ in bf_ts]}
    dl = {"ts": dl_ts, "val": [rng.choice([90, 0, 140]) for _ in dl_ts]}
    out = _ui(
        textwrap.dedent(f"""
        const bf = {json.dumps(bf)}, dl = {json.dumps(dl)}, start = 30000, end = 40500;
        // the earlier rule: every valid reading of both files, sorted by time (stable), the first of each time kept
        const pts = [];
        const add = (f) => {{ for (let i = Math.max(0, first(f.ts, start) - GAP_WINDOW - 1); i < f.ts.length && f.ts[i] <= end; i++)
          if (f.val[i] > 0 && f.val[i] <= 500) pts.push([f.ts[i], f.val[i]]); }};
        add(bf); add(dl); pts.sort((a, b) => a[0] - b[0]);
        const all = pts.filter((p, i) => i === 0 || p[0] !== pts[i - 1][0]);
        const h = new (customElements.get("glucocraft-history"))(); h.setConfig({{ hours: 3 }});
        const s = h.series(bf, dl, start, end, 300, false, 900);
        return [JSON.stringify(s.uniq) === JSON.stringify(all.filter(([t]) => t >= start)),
          JSON.stringify(s.gaps) === JSON.stringify(gcGaps(all.map(([t]) => t), 900)), s.uniq.length];
    """)
    )
    assert out[0] and out[1] and out[2] > 500


@needs_node
def test_a_six_month_graph_without_its_summary_is_described_without_an_error():
    out = _ui(
        "const h = new (customElements.get('glucocraft-history'))(); h.setConfig({ hours: 4320 });"
        "h._hass = { states: {} };"
        "const pts = Array.from({ length: 259200 }, (_, i) => [i * 60, 100 + (i % 61)]);"
        "return h.describe('Glucose', 4320, pts);"
    )
    assert out == "Glucose: lowest 100, highest 160, mean 130 milligrams per deciliter."


# ------------------------------------------------------------------------------------------------ reloads
@needs_node
def test_the_display_unit_is_kept_while_glucocraft_reloads():
    out = _ui(
        "const c = new (customElements.get('glucocraft-custom-diff'))(); c.setConfig({}); c.draw = () => {};"
        "c.hass = { states: { 'sensor.glucocraft_configuration': { last_updated: 'a', attributes: { unit: 'mmol/L' } } } };"
        "const a = gU(); c.hass = { states: {} }; return [a, gU(), gs(70)];"
    )
    assert out == ["mmol/L", "mmol/L", "3.9"]


@needs_node
def test_the_display_card_keeps_its_ranges_and_the_custom_flag_while_glucocraft_reloads():
    script = (
        DOM
        + CARD
        + textwrap.dedent("""
        const card = new reg["glucocraft-display-card"](); card.setConfig({});
        const on = { state: "on", attributes: { range_very_low: 50, range_low: 65, range_tight_low: 72,
          range_tight_high: 150, range_high: 200, range_very_high: 260 } };
        card._hass = { states: { "binary_sensor.glucocraft_using_custom_ranges": on } };
        const live = [card._ranges(), card._state().custom];
        card._hass = { states: { "binary_sensor.glucocraft_using_custom_ranges": { state: "unavailable", attributes: {} } } };
        const reload = [card._ranges(), card._state().custom];
        card._hass = { states: {} };
        const removed = [card._ranges(), card._state().custom];
        process.stdout.write("\\u0001" + JSON.stringify([live, reload, removed]));
    """)
    )
    live, reload, removed = node_json(script)
    assert live == [{"cmin": 50, "cmax": 260, "imin": 72, "imax": 150, "nmin": 65, "nmax": 200}, True]
    assert reload == live and removed == live


# ------------------------------------------------------------------------------------------------ Change Over Time
@pytest.mark.parametrize(
    "unit, now, old, shown, direction",
    [
        ("mmol/L", 110.2, 110.0, "0.0 mmol/L", "No change"),  # 0.2 mg/dL: 0.01 mmol/L, never "+0.0" beside "higher"
        ("mmol/L", 110.0, 110.3, "0.0 mmol/L", "No change"),  # never "-0.0" beside "lower"
        ("mg/dL", 110.3, 110.0, "0 mg/dL", "No change"),  # never "+0" beside "higher"
        ("mmol/L", 112.0, 110.0, "+0.1 mmol/L", "Higher"),
        ("mg/dL", 108.0, 110.0, "-2 mg/dL", "Lower"),
    ],
)
@needs_node
def test_a_difference_that_rounds_to_zero_has_no_sign_and_no_direction(unit, now, old, shown, direction):
    out = _ui(
        textwrap.dedent(f"""
        const t = new (customElements.get("glucocraft-change"))(); t.setConfig({{ basis: "consensus" }});
        const cmp = {{ last_4_weeks: {{ mean: {now} }}, weeks_22_26_ago: {{ mean: {old} }} }};
        t.hass = {{ states: {{ "sensor.glucocraft_configuration": {{ last_updated: "a", attributes: {{ unit: {json.dumps(unit)} }} }},
          "sensor.glucocraft_change_over_time": {{ last_updated: "a", attributes: {{ consensus: {{ compare: cmp, weeks14: [] }} }} }} }} }};
        t.draw();
        const row = t.shadowRoot.innerHTML.split("<tr>").find((r) => r.includes("Mean"));
        return row.split("<td").map((c) => c.replace(/<[^>]*>/g, ""));
    """)
    )
    cells = [c.split(">", 1)[-1] for c in out[1:]]
    assert any(c.startswith(shown) for c in cells), cells
    assert any(c.startswith(direction) for c in cells), cells


# ------------------------------------------------------------------------------------------------ parity with the panels
def _one_week_file() -> str:
    """A GLU2 v3 file (base64) with a single week in its trends section."""
    from glucocraft import panel_feed as pf

    so = pf.HEADER_SIZE
    ro = so + pf.STATS_SIZE
    to = ro + pf.RANGES_SIZE
    trends = pf._TREND_FMT.pack(0, 0, 120, 30, 25, 6.5, 70, 40, 3, 20, 60.0, 250.0)
    data = (
        pf._HEADER_FMT.pack(b"GLU2", pf.GLU2_VERSION, 1, so, ro, to, 0, 0, 0, to, 0)
        + b"\0" * pf.STATS_SIZE
        + struct.pack("<6f", 54, 250, 70, 180, 70, 140)
        + trends
        + pf.extremes_block({})
        + pf.limit_time_trailer({})
    )
    return base64.b64encode(data).decode()


@needs_node
@pytest.mark.parametrize("tag", ["glucocraft-display-card", "glucocraft-display-card-wide"])
def test_a_weekly_page_with_one_week_draws_its_dot_as_the_panels_do(tag):
    script = (
        DOM
        + CARD
        + textwrap.dedent(f"""
        const card = new reg[{json.dumps(tag)}](); card.setConfig({{}});
        const b = Buffer.from({json.dumps(_one_week_file())}, "base64");
        card._bf = globalThis.glucocraftShared.parseGlu2(b.buffer.slice(b.byteOffset, b.byteOffset + b.length));
        process.stdout.write("\\u0001" + JSON.stringify(card._trendCanvas(0)));
    """)
    )
    svg = node_json(script)
    assert 'width="3" height="3" fill="#fff"' in svg  # the week's dot
    assert '<path d="M10 ' in svg  # at the left edge (x 10), as on the panel


@needs_node
def test_a_weekly_value_next_to_its_target_shows_its_side_of_the_target():
    """The weekly page's last 14-day value beside its target (">70%" for time in range): 69.96% is shown as 69.96%, not
    as 70.0% (the two-decimal rule, units.shown_against); a page without a target rounds once."""
    script = (
        DOM
        + CARD
        + textwrap.dedent("""
        const card = new reg["glucocraft-display-card"](); card.setConfig({});
        const week = { start: 1, mean: 120, sd: 30, cv: 25, gmi: 6.5, tir: 69.96, titr: 50.04, low: 3.96, high: 20, min: 60,
          max: 200 };
        card._bf = { trends: [week], dirs: [], std: { custom: false, descs: [], targets: [[">", 70], null, null, ["<=", 36],
          null, ["<", 4], ["<", 25], null, null] } };
        process.stdout.write("\\u0001" + JSON.stringify([card._trendStatus(0), card._trendStatus(1), card._trendStatus(5)]));
    """)
    )
    tir, titr, low = node_json(script)
    assert "69.96%" in tir and "50.0%" in titr and "3.96%" in low


# ------------------------------------------------------------------------------------------------ texts
@needs_node
def test_weekday_names_come_from_the_catalogue():
    assert "toLocaleDateString" not in UI
    assert _ui("GC.date = 'MDY'; return dayShort(new Date(2026, 9, 25, 12));") == "Sun 10/25"


@needs_node
def test_the_hour_labels_are_one_format_on_every_card():
    """The dashboard's graphs and the display card label the hours alike ("6am" / "06:00", DISPLAY_CONTRACT.md),
    from one catalogue key."""
    assert _ui("GC.time = '12'; return [hourLabel(new Date(2026, 9, 25, 6)), hourLabel(new Date(2026, 9, 25, 18))];") == [
        "6am", "6pm"]  # fmt: skip
    assert _ui("GC.time = '24'; const h = hourLabel(new Date(2026, 9, 25, 6)); GC.time = '12'; return h;") == "06:00"
    assert "ui.time.hour_12" not in UI


@needs_node
def test_values_next_to_a_rule_show_the_side_of_the_rule_they_are_on():
    """The two-decimal rule (units.shown_against): a percentage next to a target, a goal or the 70% of sensor active is
    shown with two decimals where its rounded value would be the rule's value on the wrong side of it; elsewhere it is
    rounded once."""
    out = _ui(
        "return [pcAt(69.96, 70), pcAt(69.6, 70), pc(69.6), pcAt(70.4, 70), pcAt(3.96, 4, 1), "
        "gcDataWarnings([['14d', { sensor_active: 69.96, sensor_active_ok: false }]])];"
    )
    assert out[:5] == ["69.96%", "69.60%", "70%", "70.40%", "3.96%"]
    assert "So far it has covered 69.96%." in out[5]


@needs_node
def test_a_gmi_goal_in_mmol_mol_uses_the_gmi_rule():
    # 9.7 %: 83 mmol/mol by the IFCC-NGSP master equation, 82 by the GMI rule that the GMI values use
    assert _ui("GC.a1c = 'mmol_mol'; return [gcGoalVal('gmi', 9.7), gmiTxt(9.7)];") == ["82 mmol/mol", "82 mmol/mol"]
    # no card converts a laboratory A1C: the report data carry its mmol/mol (regional.py), so no copy of 10.929 is left
    assert "a1cMmol" not in UI and "10.929" not in UI and JS["shared"].count("10.929") == 0
    strategy = JS["strategy"]
    assert '12.71 + 4.70587 * (((p | float(0)) - 3.31) / 0.02392) / " + MGDL_PER_MMOLL + "' in strategy
    assert "10.929" not in strategy and "18.016" not in strategy


@pytest.mark.parametrize(
    "shown, said",
    [
        (
            "Time in range, last 4 weeks: -0.6 percentage points since 22–26 weeks ago",
            "Time in range, last 4 weeks: minus 0.6 percentage points since 22 to 26 weeks ago",
        ),
        ("+5 mg/dL", "plus 5 milligrams per deciliter"),
        ("−0.1 mmol/L", "minus 0.1 millimoles per liter"),
        ("-1.2 mg/dL per minute", "minus 1.2 milligrams per deciliter per minute"),
        ("+2,5 percentage points (+3 mmol/mol)", "plus 2,5 percentage points (plus 3 millimoles per mole)"),
        ("22-26 weeks ago", "22-26 weeks ago"),  # a hyphen between two numbers is no sign
    ],
)
@needs_node
def test_a_sign_before_a_number_is_spoken_in_words(shown, said):
    assert _ui(f"return gcSpeakText({json.dumps(shown)});") == said


@needs_node
def test_the_pages_card_rows_are_named_for_where_they_lead_and_described_by_their_lines():
    out = _ui(
        textwrap.dedent(f"""
        const p = new (customElements.get("glucocraft-pages"))(); p.setConfig({{}});
        p._hass = {{ states: {json.dumps(UI_STATES)} }}; p.draw();
        return p.shadowRoot.innerHTML;
    """)
    )
    assert 'aria-label="Open Today" aria-describedby="h-today d-today"' in out
    assert 'aria-label="Open Consensus Ranges" aria-describedby="h-consensus d-consensus"' in out
    assert '<div class="h" id="h-today">' in out and '<div class="d" id="d-consensus">' in out
    assert "milligrams per deciliter" in out and '<span class="sr">meets</span>' in out
    # the legend of the marks, in words
    assert '<span class="sr">meets / does not meet: time in range, last 14 days' in out
    assert ".sr { position: absolute;" in out


@needs_node
def test_the_legends_of_the_marks_have_a_spoken_form():
    out = _ui("return [gcSay(gcT('ui.statistics.group.consensus')), gcSayHtml('<i>Last 14 days</i>: ✓ / ✗, 70%')];")
    assert out[0].endswith('<span class="sr">Consensus Targets — meets Meets / does not meet Does Not Meet</span>')
    assert out[1] == (
        '<span aria-hidden="true"><i>Last 14 days</i>: ✓ / ✗, 70%</span>'
        '<span class="sr">Last 14 days: meets / does not meet, 70 percent</span>'
    )


def test_the_documentation_tables_name_their_columns_and_the_details_reflow():
    assert '`<th scope="col">${h}</th>`' in JS["docs"]
    assert "minmax(min(340px, 100%), 1fr)" in UI and "minmax(340px, 1fr)" not in UI


# ------------------------------------------------------------------------------------------------ parity, continued
@needs_node
@pytest.mark.parametrize("custom", [False, True])
def test_the_six_hour_page_before_the_first_file_fills_every_cell_as_the_panels_do(custom):
    """glucocraft-panel.yaml update_stats_display: Very low and Very high (one decimal), the minimum and the maximum
    (measured numbers: a reading at the sensor's limit is no value) of the 6-hour summary of the range set in use."""
    vl, vh = ("time_very_low", "time_very_high") if custom else ("time_below_54", "time_above_250")
    summary = {vl: 1.25, vh: 3.0, "min_value": 58, "max_value": 212}
    sid = f"sensor.glucocraft_6h_{'custom' if custom else 'consensus'}_ranges"
    script = (
        DOM
        + CARD
        + textwrap.dedent(f"""
        const card = new reg["glucocraft-display-card"](); card.setConfig({{}});
        card._hass = {{ states: {{ "sensor.glucocraft_configuration": {{ attributes: {{ custom_ranges_and_goals: {json.dumps(custom)} }} }},
          {json.dumps(sid)}: {{ state: "80", attributes: {json.dumps(summary)} }} }} }};
        const st = card._statsFor(0);
        process.stdout.write("\\u0001" + JSON.stringify(Object.fromEntries([...st.a, ...st.b])));
    """)
    )
    cells = node_json(script)
    assert [cells["very_low"], cells["very_high"], cells["min"], cells["max"]] == ["1.3%", "3.0%", "58", "212"]


@needs_node
def test_the_card_rounds_mmol_once_as_the_panels_and_glucocraft_do():
    """rh1 with the panels' float32 epsilon: the exact halves 112.6 and 337.8 mg/dL of a file's float32 show 6.3 and
    18.8 mmol/L (units.from_mgdl), and every stored tenth from 40.0 to 500.0 mg/dL rounds as GlucoCraft rounds it."""
    tenths = [t / 10 for t in range(400, 5001) if not ((t / 10).is_integer() and int(t / 10) in units.CONSENSUS_MMOLL)]
    script = (
        DOM
        + CARD
        + textwrap.dedent(f"""
        const mg = {json.dumps(tenths)};
        process.stdout.write("\\u0001" + JSON.stringify(mg.map((v) => globalThis.glucocraftShared.rh1(Math.fround(v) / MGDL_PER_MMOLL))));
    """)
    )
    out = node_json(script)
    expected = [units.from_mgdl(v, units.UNIT_MMOLL) for v in tenths]
    assert out == expected
    assert out[tenths.index(112.6)] == 6.3 and out[tenths.index(337.8)] == 18.8
    # one rh1, in glucocraft-shared.js, with the panels' float32 epsilon
    assert not any("const rh1 = " in JS[n] for n in ("display-card", "ui", "report"))
    assert "const rh1 = (x) => Math.sign(x) * Math.floor(Math.abs(x) * 10 + 0.5 + 1e-5) / 10 + 0;" in JS["shared"]


# ------------------------------------------------------------------------------------------------ the 70% rule
@needs_node
@pytest.mark.parametrize(
    "period,attrs,note",
    [
        # the note's wording, word for word: 23% of 30 days covered, 21 needed, about 14 more days
        (
            "30d",
            {"sensor_active": 23.0, "sensor_active_ok": False},
            (
                "Last 30 days: not enough data yet. GlucoCraft shows a period's statistics once the sensor has covered "
                "at least 70% of it. So far it has covered 23%. With no gaps, about 14 more days are needed."
            ),
        ),
        # a short period in hours: 20% of 6 hours = 1.2 h, 4.2 h needed
        ("6h", {"sensor_active": 20.0, "sensor_active_ok": False}, "With no gaps, about 3 more hours are needed."),
        # less than a day still needed of a long period: in hours (14 days at 67%: 0.42 days = 10 hours)
        ("14d", {"sensor_active": 67.0, "sensor_active_ok": False}, "about 10 more hours are needed."),
        # GlucoCraft's own figures where published (enough_data, period_seconds, covered_seconds); one day: singular
        (
            "7d",
            {"enough_data": False, "sensor_active": 55.71, "period_seconds": 604800, "covered_seconds": 336960},
            "So far it has covered 56%. With no gaps, about 1 more day is needed.",
        ),
        # just below 70%: two decimals, never "70%"
        ("14d", {"sensor_active": 69.96, "sensor_active_ok": False}, "So far it has covered 69.96%."),
        # a period without any reading (has_data false alone, no sensor active): nothing covered, the note all the same
        (
            "6h",
            {"has_data": False},
            (
                "Last 6 hours: not enough data yet. GlucoCraft shows a period's statistics once the sensor has covered "
                "at least 70% of it. So far it has covered 0%. With no gaps, about 4 more hours are needed."
            ),
        ),
    ],
)
def test_one_note_per_period_below_70_percent_with_the_time_still_needed(period, attrs, note):
    out = _ui(f"const e = gcEnough({json.dumps(period)}, {json.dumps(attrs)}); return e ? gcEnoughNote(e) : null;")
    assert out is not None and note in out, out


@needs_node
def test_no_note_for_a_period_with_enough_data_or_nothing_known():
    out = _ui(
        "return [gcEnough('14d', { sensor_active: 80, sensor_active_ok: true }), gcEnough('14d', {}),"
        " gcEnough('14d', null), gcEnough('14d', { enough_data: true, sensor_active: 20, sensor_active_ok: false })];"
    )
    assert out == [None, None, None, None]


@needs_node
def test_the_statistics_table_shows_the_note_once_and_dashes_for_the_period():
    """A period below 70%: GlucoCraft sends every value as null (---); the table shows one note for it, above the table,
    and none for a period with enough data."""
    enough = {"has_data": True, "time_70_180": 90.0, "mean": 120, "sensor_active": 99.0, "sensor_active_ok": True}
    below = {"has_data": True, "time_70_180": None, "mean": None, "sensor_active": 23.0, "sensor_active_ok": False}
    states = {
        "sensor.glucocraft_configuration": {"attributes": {"unit": "mg/dL"}},
        "sensor.glucocraft_6h_consensus_ranges": {"attributes": enough},
        "sensor.glucocraft_14d_consensus_ranges": {"attributes": below},
    }
    html = _ui(
        "const t = new (customElements.get('glucocraft-stats-table'))(); t.setConfig({ periods: ['6h', '14d'] });"
        f"t._hass = {{ states: {json.dumps(states)} }}; t.draw(); return t.shadowRoot.innerHTML;"
    )
    assert html.count('<div class="w">') == 1 and "Last 14 days: not enough data yet." in html
    assert "Last 6 hours" not in html


# ------------------------------------------------------------------------------------------------ other dashboard rules
@needs_node
def test_the_band_key_leaves_out_a_band_narrower_than_the_display_step():
    """Two limits less than 1 mg/dL apart as shown (75.6 and 76): no impossible "76–75" in the key."""
    out = _ui(
        "GC.mmol = false; const h = new (customElements.get('glucocraft-history'))(); h.setConfig({});"
        "return h.bandKey({ very_low: 75.6, low: 76, tight_low: 80, tight_high: 140, high: 180, very_high: 250 });"
    )
    assert "76–75" not in out and "76–79" in out


@needs_node
def test_axis_labels_one_format_per_axis_and_the_spoken_limit_counts():
    out = _ui(
        "GC.mmol = true; return [gcAxisText(7, 0.5), gcAxisText(6.5, 0.5), gcAxisText(4, 2), gcAxisText(6.25, 0.25),"
        " gcLimitCountSaid({ low: 2, high: 1 }, { low: 7500, high: 900 }), gcSpeakText('+6.2 percentage points ‡')];"
    )
    assert out[:4] == ["7.0", "6.5", "4", "6.25"]
    assert out[4] == (
        "2 readings below the sensor's lower limit for 2 hours 5 minutes; "
        "1 reading above the sensor's upper limit for 15 minutes"
    )
    assert out[5] == "plus 6.2 percentage points (a difference of 5 percentage points or more)"


@needs_node
def test_the_statistics_card_shows_the_delta_with_its_sign():
    out = _ui(
        "const box = {}, c = new (customElements.get('glucocraft-statistics'))(); c.setConfig({});"
        "c.shadowRoot = { innerHTML: '', querySelector: (s) => (box[s] ||= { innerHTML: '' }) };"
        "c._hass = { states: { 'sensor.glucocraft_configuration': { attributes: { unit: 'mg/dL' } },"
        " 'sensor.glucocraft_5_minute_delta': { state: '5', attributes: { value_mgdl: 4.6 } } } };"
        "GC.mmol = false; c.draw(); return box['.lr'].innerHTML;"
    )
    assert "+5 mg/dL" in out


# a reading time ahead of the clock (time_untrusted) on the dashboard
_UNTRUSTED = {
    "sensor.glucocraft_status": {"state": "no_current_data", "last_updated": "a", "attributes": {}},
    "binary_sensor.glucocraft_no_current_data": {"state": "on", "last_updated": "a", "attributes": {}},
    "sensor.glucocraft_last_reading_time": {
        "state": "2099-01-01T00:00:00+00:00",
        "last_updated": "a",
        "attributes": {"time_untrusted": True},
    },
    "sensor.glucocraft_configuration": {
        "state": "ok",
        "last_updated": "a",
        "attributes": {"unit": "mg/dL", "maintenance_issues": []},
    },
}


@needs_node
def test_a_reading_time_ahead_of_the_clock_is_explained_on_maintenance_and_pointed_to_by_the_banner():
    """The banner names it and points to the Maintenance page (no age, no no-current-data banner with a wrong age);
    the Maintenance page's first card explains it while it lasts: what happened, what it means, what to check."""
    out = _ui(
        "const b = new (customElements.get('glucocraft-banners'))(); b.setConfig({});"
        f"const states = {json.dumps(_UNTRUSTED)}; b._hass = {{ states }}; b.draw();"
        "const m = new (customElements.get('glucocraft-maintenance-issues'))(); m.setConfig({});"
        "m._hass = { states }; m.draw();"
        "return [b.shadowRoot.querySelector('.list').innerHTML, m.style.display, m.shadowRoot.innerHTML];"
    )
    banners, display, hero = out
    assert "Reading Times Ahead of the Clock" in banners and "Maintenance page explains" in banners
    assert "No Current Data" not in banners
    assert display == "" and "Reading times ahead of the clock:" in hero
    for what in ("Home Assistant's clock and time zone", "the clock of the glucose source", "without a time zone"):
        assert what in hero, what


@needs_node
def test_the_details_button_names_the_section_it_controls_and_is_never_without_a_name():
    out = _ui(
        "const d = new (customElements.get('glucocraft-details'))();"
        "d.setConfig({ title: 'Show All Statistics', cards: [] });"
        "return d.shadowRoot.innerHTML;"
    )
    assert 'aria-expanded="false" aria-controls="body"' in out and 'id="body"' in out
    assert "<span>Show All Statistics</span>" in out
