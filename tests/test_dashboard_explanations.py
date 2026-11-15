# License: AGPL-3.0-only (LICENSE).
"""The dashboard's explanations that the code produces: what each card says follows what it shows.

Rules under test:
- The Consensus Ranges headline compares the time in range of the last 4 weeks with 22–26 weeks ago, or with 9–13
  weeks ago when no older data exist, as the core measures and the Change Over Time table do.
- The ‡ mark of Change Over Time stands beside the difference the direction uses.
- The differences of Change Over Time carry their unit: percentage points, or the glucose unit.
- On Custom Ranges, a measure for which no goal can be set says so, rather than "No goal set".
- A graph's spoken summary names the same mean, lowest and highest as the numbers shown above it (the period's
  summary sensor), and the daily chart names a reading at the sensor's limit in words, never as a number.
- Durations are written in full words; the time per day in the tables is compact, its units named in a note.
- The statistics table of Today shows the GRI's hypoglycemia and hyperglycemia components, named for screen readers,
  and --- for the periods shorter than 14 days.
- A headline card that opens Home Assistant's details says so on screen.
- Settings Status lists the graph range colors, how the trend is shown, both backgrounds and Outline text,
  the sensor-expiry entity and whether a report printer is set.
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

import jinja2

from node_runner import needs_node, node_json

WWW = Path(__file__).resolve().parents[1] / "custom_components" / "glucocraft" / "www"
UI = (WWW / "glucocraft-ui.js").read_text(encoding="utf-8")
STUBS = (
    "globalThis.window = globalThis; globalThis.HTMLElement = class { attachShadow() { this.shadowRoot = "
    "{ innerHTML: '' }; return this.shadowRoot; } }; const __reg = {};\n"
    "globalThis.customElements = { get: (n) => __reg[n], define: (n, c) => { __reg[n] = c; } };\n"
    "globalThis.ResizeObserver = class { observe() {} disconnect() {} };\n"
)


def _ui(expr: str) -> object:
    """Evaluate the function body expr after glucocraft-ui.js (its module-level helpers and cards in scope)."""
    return node_json(STUBS + UI + "\nprocess.stdout.write('\\u0001' + JSON.stringify((() => { " + expr + " })()));\n")


def _change_table(compare: dict, basis: str = "consensus") -> str:
    """Return the Change Over Time table drawn from one compare attribute."""
    attrs = {"consensus": {"compare": compare, "weeks14": [{}]}, "custom": {"compare": compare, "weeks14": [{}]}}
    return _ui(
        f'const t = new __reg["glucocraft-change"](); t.setConfig({{ basis: "{basis}" }}); '
        f't._hass = {{ states: {{ "sensor.glucocraft_change_over_time": {{ attributes: {json.dumps(attrs)} }} }} }}; '
        "t.draw(); return t.shadowRoot.innerHTML;"
    )


def _shown(html: str) -> str:
    """Return the text shown on screen: without the screen-reader texts and the markup."""
    return re.sub(r"<[^>]+>", "", re.sub(r'<span class="sr">.*?</span>', "", html)).strip()


def _row(table: str, label: str) -> list[str]:
    """Return the shown text of the cells of the table row whose header begins with label."""
    row = next(r for r in table.split("<tr>") if _shown(r.split("</th>")[0]).startswith(label))
    return [_shown(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row)]


# ------------------------------------------------------------------------------------------------ Change Over Time
@needs_node
def test_the_headline_change_falls_back_to_9_13_weeks_ago():
    out = _ui(
        "return [gcTirChange({ last_4_weeks: { tir: 80 }, weeks_9_13_ago: { tir: 77.5 } }), "
        "gcTirChange({ last_4_weeks: { tir: 80 }, weeks_9_13_ago: { tir: 77.5 }, weeks_22_26_ago: { tir: 81 } }), "
        "gcTirChange({ last_4_weeks: { tir: 80 }, weeks_22_26_ago: { tir: 80.02 } }), "
        "gcTirChange({ last_4_weeks: { tir: 80 } }), gcTirChange(undefined)];"
    )
    assert out == [
        "Time in range, last 4 weeks: +2.5 percentage points since 9–13 weeks ago",
        "Time in range, last 4 weeks: -1.0 percentage points since 22–26 weeks ago",
        "Time in range, last 4 weeks: no change since 22–26 weeks ago",
        "Change over time: not enough history yet",
        "Change over time: not enough history yet",
    ]


@needs_node
def test_the_dagger_stands_beside_the_difference_the_direction_uses():
    # without data from 22–26 weeks ago the direction uses 9–13 weeks ago: ‡ beside that difference
    cells = _row(_change_table({"last_4_weeks": {"tir": 80}, "weeks_9_13_ago": {"tir": 74}}), "Time in Range")
    assert cells[2].endswith("+6.0 percentage points ‡") and cells[4] == "---" and "‡" not in cells[4]
    assert cells[5] == "Higher than 9–13 weeks ago"
    # with older data the direction uses 22–26 weeks ago: ‡ there only
    cells = _row(
        _change_table({"last_4_weeks": {"tir": 80}, "weeks_9_13_ago": {"tir": 79}, "weeks_22_26_ago": {"tir": 70}}),
        "Time in Range",
    )
    assert "‡" not in cells[2] and cells[4].endswith("+10.0 percentage points ‡")


@needs_node
def test_the_differences_carry_their_unit():
    now = {"tir": 80, "mean": 130, "gmi": 6.5, "gmi_mmol_mol": 48, "active": 90}
    old = {"tir": 78, "mean": 125, "gmi": 6.3, "gmi_mmol_mol": 45, "active": 90}
    table = _change_table({"last_4_weeks": now, "weeks_9_13_ago": old})
    assert _row(table, "Mean Glucose")[2] == "+5 mg/dL"
    assert _row(table, "Glucose Management Indicator (GMI)")[2] == "+0.2 percentage points"
    assert _row(table, "Sensor active (data received by GlucoCraft)")[0] == "90%"


@needs_node
def test_a_measure_without_a_possible_goal_says_so_on_custom_ranges():
    table = _change_table({"last_4_weeks": {"mean": 130}}, basis="personal")
    for label in ("Mean Glucose", "Standard Deviation"):
        row = next(r for r in table.split("<tr>") if _shown(r.split("</th>")[0]).startswith(label))
        assert "No goal set" not in row, label
    assert "No goal can be set for this measure, and no consensus target exists" in table
    # the GRI is on the consensus ranges only: no row for it on Custom Ranges
    assert "GRI" not in table and "Glycemia Risk Index" not in table
    assert "Glycemia Risk Index (GRI)" in _change_table({"last_4_weeks": {"mean": 130}}, basis="consensus")


# ------------------------------------------------------------------------------------------------ spoken summaries
@needs_node
def test_the_graph_summary_speaks_the_numbers_shown_above_the_graph():
    # the summary sensor's time-weighted mean, lowest and highest (shown above the graph), not the drawn readings'
    out = _ui(
        "const h = new __reg['glucocraft-history'](); h._cfg = { hours: 336 }; h._hass = { states: { "
        "'sensor.glucocraft_14d_consensus_ranges': { attributes: { has_data: true, mean: 123.4, min_value: 61, "
        "max_value: 251 } } } }; return h.describe('Glucose — Last 14 Days', 336, [[1, 90], [2, 100]]);"
    )
    assert out == "Glucose — Last 14 Days: lowest 61, highest 251, mean 123 milligrams per deciliter."


@needs_node
def test_the_daily_chart_says_measured_numbers():
    day = "{ avg: 110, lo: 58, hi: 250, loMg: 58, hiMg: 250 }"
    expr = (
        "const d = new __reg['glucocraft-daily-range'](); d._hass = { states: { "
        "'sensor.glucocraft_14d_consensus_ranges': { attributes: { limit_readings: { low: 1, high: 0 }, "
        "limit_seconds: { low: 300, high: 0 } } } } }; "
        f"return d.describeDays('Daily', [{day}, {{ avg: NaN }}]);"
    )
    assert _ui(expr) == (
        "Daily: daily means from 110 to 110 milligrams per deciliter; lowest 58 milligrams per deciliter, highest 250 milligrams per deciliter."
    )


# ------------------------------------------------------------------------------------------------ words and tables
@needs_node
def test_durations_in_full_words_and_the_compact_time_per_day():
    out = _ui(
        "return [dur(1), dur(5), dur(60), dur(125), dur(1440), dur(1500), dur(NaN), "
        "perDayTable(88.2), perDayTable(2), perDayTable(NaN)];"
    )
    assert out == [
        "1 minute", "5 minutes", "1 hour", "2 hours 5 minutes", "1 day", "1 day 1 hour", "---",
        "21 h 10 min", "29 min", "",
    ]  # fmt: skip


@needs_node
def test_the_today_table_shows_the_gri_components_and_the_units():
    summary = {"has_data": True, "gri": 25, "gri_hypo": 2.1, "gri_hyper": 10.3, "mean": 130, "sensor_active": 90}
    # the GRI only from 14 days: the shorter periods' summaries carry none (sensor.GlucoCraftPeriodConsensus)
    short = {**summary, "gri": None, "gri_hypo": None, "gri_hyper": None}
    states = {f"sensor.glucocraft_{p}_consensus_ranges": {"attributes": short} for p in ("6h", "24h", "7d")}
    states["sensor.glucocraft_14d_consensus_ranges"] = {"attributes": summary}
    states["sensor.glucocraft_configuration"] = {"attributes": {"unit": "mg/dL"}}
    html = _ui(
        "const t = new __reg['glucocraft-stats-table'](); t.setConfig({ basis: 'consensus' }); "
        f"t._hass = {{ states: {json.dumps(states)} }}; t.draw(); return t.shadowRoot.innerHTML;"
    )
    assert (
        '<span aria-hidden="true">2.1 · 10.3</span><span class="sr">hypoglycemia component 2.1, hyperglycemia ' in html
    )
    assert "Under each value: the hypoglycemia component · the hyperglycemia component (Klonoff et al., 2023)." in html
    assert html.count('<span aria-hidden="true">2.1 · 10.3</span>') == 1  # the 14 days only
    row = html[html.index("Glycemia Risk Index (GRI)") :].split("</tr>")[0]
    assert row.count(">---<") == 3 and ">25<" in row  # 6 hours, 24 hours and 7 days: ---
    assert (
        "No consensus target. GRI is shown for periods of 14 days or more; shorter periods show ---. Unlike GMI, it is "
        "also shown with less than 70% sensor active. This method is a GlucoCraft rule. The CGM Report shows the GRI "
        "with its zone." in html
    )
    for label in ("Glucose Management Indicator (GMI)", "Mean Glucose (mg/dL)", "Time in Range (70–180 mg/dL)"):
        assert label in html, label
    assert (
        "Under each time percentage of a period of 24 hours or longer: its time per 24-hour day, in hours (h) and minutes (min)"
        in html
    )


@needs_node
def test_a_headline_card_that_opens_the_details_says_so():
    draw = (
        "const r = new __reg['glucocraft-readout'](); r.setConfig({ view: 'today', hero: true{path} }); "
        "r.shadowRoot.querySelector = () => ({}); r._hass = { states: {} }; r.draw(); return r.shadowRoot.innerHTML;"
    )
    hint = "Select to show the details in Home Assistant."
    # shown, and the description of the card's button (with its lines): read when the button is reached
    assert f'<div class="s" id="hint">{hint}</div>' in _ui(draw.replace("{path}", ""))
    assert hint not in _ui(draw.replace("{path}", ", path: '/x/today'"))


# ------------------------------------------------------------------------------------------------ Settings Status
def _settings_status(attrs: dict) -> str:
    """Return the Current Settings markdown of Settings Status rendered with the configuration attributes attrs."""
    script = textwrap.dedent(f"""
        globalThis.HTMLElement = class {{}}; const reg = {{}};
        globalThis.customElements = {{ get: (n) => reg[n], define: (n, c) => {{ reg[n] = c; }} }};
        globalThis.location = {{ pathname: "/glucocraft-home/start" }};
        await import({json.dumps((WWW / "glucocraft-strategy.js").as_uri())});
        const d = await reg["ll-strategy-dashboard-glucocraft"].generate({{}}, {{ states: {{}}, devices: {{}}, entities: {{}} }});
        const v = d.views.find((x) => x.path === "settings");
        process.stdout.write("\\u0001" + JSON.stringify(v.sections[1].cards[1].content));
    """)
    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    return env.from_string(node_json(script)).render(
        states={"sensor": {"glucocraft_configuration": {"attributes": {**_BASE_ATTRS, **attrs}}}},
        is_state=lambda entity, state: False,
    )


_RANGES = {"very_low": 54, "low": 70, "tight_low": 70, "tight_high": 140, "high": 180, "very_high": 250}
_BASE_ATTRS = {
    "unit": "mg/dL",
    "ranges": _RANGES,
    "consensus_ranges": {f"range_{k}": v for k, v in _RANGES.items()},
}


@needs_node
def test_settings_status_lists_the_display_settings():
    attrs = {
        "zone_colors": {"very_low": "#C62828"},
        "band_colors": {"very_low": "#9B1C1F", "high": "#FCC21B"},
        "trend_display": "word",
        "card_background": "theme",
        "panel_background": "#000000",
        "text_outline": True,
    }
    md = _settings_status(attrs)
    assert "**Graph range colors:** very low #9B1C1F" in md and "high #FCC21B" in md
    assert (
        "**Trend shown as:** the trend word · **display card background:** Home Assistant theme · "
        "**wall panel background:** #000000 · **outline text:** on"
    ) in md
    # a reading at a lower limit chosen by hand is LO and at an upper limit chosen by hand HI as well (limits.py)
    assert "with a limit chosen by hand (not *Automatic*), a reading at the limit as well" in md
    # the same rule in the requirements table of the same page
    table = "".join(
        json.loads((WWW.parent / "i18n" / "en" / "frontend.json").read_text(encoding="utf-8"))[
            "strategy.settings.requirements.table"
        ]
    )
    assert "With a limit chosen by hand, a reading at the limit is LO or HI as well." in table


@needs_node
def test_settings_status_lists_the_expiry_entity_and_the_report_printer():
    """Every setting is listed (coordinator.configuration()): the printer only as set or not, never its address."""
    md = _settings_status({"expiry_entity": "sensor.cgm_expiry", "printer": True})
    assert "**Sources:** " in md and " · **sensor-expiry entity:** sensor.cgm_expiry" in md
    assert "**report printer:** set (the address is shown only in the settings)" in md
    assert "trend direction" not in md and "Glucose notifications" not in md
    md = _settings_status({"expiry_entity": None, "printer": False})
    assert "**sensor-expiry entity:** none" in md and "**report printer:** none" in md


@needs_node
def test_settings_status_daily_summary_time_and_measurement_time():
    """The daily summary's time in the time format in use, and each source's measurement time in words: recognized
    automatically, or the attribute named in the settings (never the raw setting "auto")."""
    sources = {
        "primary_source": {"name": "CGM", "time_attribute": "auto"},
        "secondary_source": {"name": "Second", "time_attribute": "dateString"},
    }
    for time_format, stored, shown in (("12", "8:00", "8:00 AM"), ("12", "13:05:00", "1:05 PM"), ("12", "0:30", "12:30 AM"),
                                       ("24", "8:00:00", "08:00"), ("24", "21:45", "21:45")):  # fmt: skip
        md = _settings_status({"time_format": time_format, **sources,
                               "notifications": {"daily": True, "daily_time": stored, "weekly": False}})  # fmt: skip
        assert f"**Daily summary:** on, {shown} · **weekly summary:** off" in md, (time_format, stored)
    assert (
        "CGM (measurement time: recognized automatically); second source: Second (measurement time: attribute "
        "dateString)"
    ) in md and "auto)" not in md


# ------------------------------------------------------------------------------------------------ the 70% rule
def _statistics(active: float, ok) -> str:
    """The Consensus Ranges statistics card with a 14-day period at `active`% sensor active (attribute sensor_active,
    rounded to 0.1) and the comparison before rounding `ok` (attribute sensor_active_ok; None: not published)."""
    attrs = {"has_data": True, "time_70_180": 80.0, "time_below_70": 2.0, "sensor_active": active}
    if ok is not None:
        attrs["sensor_active_ok"] = ok
    # the card draws its two parts into the boxes .lr and .pt of its frame
    return _ui(
        'const c = new __reg["glucocraft-statistics"](); c.setConfig({}); '
        "const box = {}; c.shadowRoot = { innerHTML: '', querySelector: (s) => (box[s] ||= { innerHTML: '' }) }; "
        f'c._hass = {{ states: {{ "sensor.glucocraft_14d_consensus_ranges": {{ attributes: {json.dumps(attrs)} }} }} }}; '
        "c.draw(); return c.shadowRoot.innerHTML + box['.lr'].innerHTML + box['.pt'].innerHTML;"
    )


@needs_node
def test_the_seventy_percent_rule_compares_the_unrounded_sensor_active():
    """69.96% sensor active is shown as 70.0% but is less than 70%: no ✓ / ✗ and the note of the 70% rule, as the GMI
    and the CGM Report decide (sensor_active_ok); the cards never compare the shown percentage themselves."""
    warning = "Last 14 days: not enough data yet."
    mark = re.compile(r'<span aria-hidden="true">[✓✗]</span>')
    below = _statistics(70.0, False)
    assert not mark.search(below) and warning in below
    met = _statistics(70.0, True)
    assert mark.search(met) and warning not in met
    assert not mark.search(_statistics(99.0, None))  # no attribute, no comparison
    readout = _ui(
        'const r = new __reg["glucocraft-readout"](); r.setConfig({ view: "consensus" }); '
        'r._hass = { states: { "sensor.glucocraft_14d_consensus_ranges": { attributes: { has_data: true, '
        "time_70_180: 80, sensor_active: 70.0, sensor_active_ok: false } } } }; "
        "return [r.tirCheck('consensus'), gcGoalsMet({ cv: { op: '<=', value: 36 } }, { cv: 30 }, false)];"
    )
    assert readout == [None, [0, 0, 1]]
    change = _ui(
        'const t = new __reg["glucocraft-change"](); t.setConfig({ basis: "consensus" }); '
        "const cmp = { last_4_weeks: { tir: 80 } }; "
        't._hass = { states: { "sensor.glucocraft_change_over_time": { attributes: { consensus: { compare: cmp, '
        "weeks14: [{ tir: 80, active: 70.0, active_ok: false }] } } } } }; t.draw(); return t.shadowRoot.innerHTML;"
    )
    # no ✓ / ✗ in the Last 14 days column (the note under the table names the marks, in words for screen readers)
    assert not mark.search(change) and "does not meet</span>" not in change


@needs_node
def test_settings_status_names_the_custom_goals_only_while_custom_ranges_exist():
    # the goals are used on Custom Ranges, which exists only while the optional feature Custom ranges and goals is on
    assert "**Custom goals**" not in _settings_status({"custom_ranges_and_goals": False})
    assert "**Custom goals**" not in _settings_status({})
    assert "**Custom goals**" in _settings_status({"custom_ranges_and_goals": True})
