# License: AGPL-3.0-only (LICENSE).
"""The dashboard cards in their edge states: GlucoCraft reloading, reading times ahead of the clock, failed downloads,
mistyped card options, a redraw of the Documentation, and the rules the card files share.

- While the configuration sensor has no attributes (GlucoCraft reloading), the Start and Maintenance pages show
  GlucoCraft's own no-current-data limit (15 minutes), never "None".
- A reading time ahead of the clock (time_untrusted): the Statistics card shows the value without that time and
  without an age ("(--- ago)"), and its red line says "No current data" without the time; the display card's text
  description at the sensor's limit speaks no time the screen leaves out, and names the day as the screen does.
- A download that fails, or a file that cannot be read, is written to the browser console once per reason (not at
  every retry), and again after a download that worked; the retry stays.
- A mistyped card option (an unknown metric or period, days outside 1 to 200) is named on the card with the values
  allowed, and once as a console warning; valid options draw the card as before.
- The Documentation page drawn again (a setting its text shows changed) focuses the control that had the focus.
- The shared module holds the consensus ranges, the defaults, the screen-reader CSS and the capital-letter rule once;
  the display card's and the CGM Report page's axis labels are gcAxisText, with the labels of their earlier rules; the
  shared and language modules report their version as a console line, as the card files do.
"""

from __future__ import annotations

import json
import re
import textwrap
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import ha_stub
from js_i18n import WWW, catalogue, i18n_prelude
from node_runner import needs_node, node_json
from test_accessibility import FAKE_DOM, _card
from test_accessibility_checks import JS, _eval
from test_dashboard_text_card import ATTRS, CFG, _cards, _dashboard, _jinja

FE = catalogue("frontend")


def _ui(body: str) -> object:
    return _eval("ui", body)


# ------------------------------------------------------------------------------------------------ GlucoCraft reloading
@needs_node
def test_the_no_current_data_limit_reads_15_minutes_while_glucocraft_reloads():
    """state_attr gives None for a missing attribute: default(15, true) replaces it (default(15) would not)."""
    attrs = {k: v for k, v in ATTRS.items() if k != "no_current_data_minutes"}
    texts = [c["content"] for c in _cards(_dashboard(attrs)) if c["type"] == "custom:glucocraft-markdown"]
    env = _jinja()
    env.globals["state_attr"] = lambda e, a: {CFG: attrs}.get(e, {}).get(a)
    shown = "\n".join(env.from_string(t).render() for t in texts if "no_current_data_minutes" in t)
    assert "(in use: 15 minutes)" in shown  # Start's About This Page
    assert "**no current data after** 15 minutes" in shown  # the Maintenance page's sources
    assert "None minutes" not in shown


# ------------------------------------------------------------------------------------------------ times ahead of the clock
_STATISTICS = """
  const box = {}, c = new (customElements.get("glucocraft-statistics"))(); c.setConfig({});
  c.shadowRoot = { innerHTML: "", querySelector: (s) => (box[s] ||= { innerHTML: "" }) };
  const at = new Date(Date.now() + %(ahead)d * 60e3).toISOString();
  c._hass = { states: {
    "sensor.glucocraft_configuration": { attributes: { unit: "mg/dL" } },
    "binary_sensor.glucocraft_no_current_data": { state: "on", attributes: {} },
    "sensor.glucocraft_last_glucose_reading": { state: "112", attributes: { zone: "tight_range" } },
    "sensor.glucocraft_last_reading_time": { state: at, attributes: { time_untrusted: %(untrusted)s } } } };
  GC.mmol = false; c.draw(); return box[".lr"].innerHTML;
"""


@needs_node
def test_the_statistics_card_shows_no_time_ahead_of_the_clock():
    out = _ui(_STATISTICS % {"ahead": 20, "untrusted": "true"})
    assert "<span>Last glucose reading</span><span>112 mg/dL</span></div>" in out  # the value alone
    assert f'<div class="nc" aria-hidden="true">{FE["ui.statistics.no_data"]}</div>' in out
    assert '<div class="sr">No current data. Glucose 112 milligrams per deciliter.</div>' in out
    # a trusted reading time: the time, its age and the red line with the time, as before
    out = _ui(_STATISTICS % {"ahead": -40, "untrusted": "false"})
    assert "measured at " in out and " (40 minutes ago)</span>" in out
    assert '<div class="nc" aria-hidden="true">No current data since ' in out


# the stretch of limit readings: its start and the last measured reading (seconds before now), and time_untrusted;
# Home Assistant's time zone set, so that "today" and "yesterday" depend only on the test's clock (_at)
_LIMIT = """
  const cur = states["sensor.glucocraft_last_glucose_reading"].attributes, now = Math.floor(Date.now() / 1000);
  Object.assign(cur, {{ limit_since: now - {since}, last_measured_mgdl: 52, last_measured_ts: now - {measured} }});
  Object.assign(states["sensor.glucocraft_last_reading_time"].attributes, {{ time_untrusted: {untrusted} }});
  card.hass = {{ states, config: {{ time_zone: "America/Los_Angeles" }}, fetchWithAuth: async () => ({{ ok: false }}) }};
  card._render();
  return {{ svg: root.querySelector("svg").innerHTML, label: root.querySelector("svg").attrs["aria-label"] }};
"""


def _at(hour: int, minute: int) -> float:
    """Set the test's clock (and node's) to Wednesday 21 October 2026 at hour:minute in Los Angeles; return it."""
    instant = datetime(2026, 10, 21, hour, minute, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()
    ha_stub.set_clock(instant)
    return instant


def _limit_card(since: int, measured: int, untrusted: bool) -> dict:
    extra = _LIMIT.format(since=since, measured=measured, untrusted=json.dumps(untrusted))
    return _card("glucocraft-display-card", value=40, zone="very_low", label="Very low", limit="low", extra=extra)[
        "extra"
    ]


@needs_node
def test_the_spoken_lo_hi_lines_hide_what_the_screen_hides():
    # reading times that cannot be trusted: neither since when nor the time of the last measured value, on the screen
    # and in the text description
    out = _limit_card(125 * 60, 127 * 60, True)
    assert "Since " not in out["svg"] and "Since " not in out["label"]
    assert "Last measured: 52 mg/dL</text>" in out["svg"]
    assert "Last measured 52 milligrams per deciliter." in out["label"]
    # a start ahead of the clock: no "since" (never "for 0 minutes")
    out = _limit_card(-600, 60, False)
    assert "Since " not in out["svg"] and "Since " not in out["label"] and "0 minutes" not in out["label"]


@needs_node
def test_the_spoken_lo_hi_lines_name_the_day_as_the_screen_does():
    """The screen and the text description name the day of the start alike: the clock time alone today, "yesterday at"
    the day before, the date (the month in full when spoken) further back; the last measured value of another day is
    spoken with its day. On a fixed clock and time zone, so that the day does not depend on when the suite runs."""

    def screen(out: dict) -> str:  # the screen's lines as one text (a wrapped line joined again)
        return " ".join(re.findall(r"<text[^>]*>([^<]*)</text>", out["svg"]))

    _at(12, 0)  # noon
    out = _limit_card(125 * 60, 127 * 60, False)  # since 9:55 AM today
    assert "Since 9:55 AM (2 h 5 min)" in screen(out)
    assert "Since 9:55 AM, for 2 hours 5 minutes." in out["label"] and "yesterday" not in out["label"]
    out = _limit_card(26 * 3600, 3 * 86400, False)  # since 10:00 AM yesterday; last measured three days back
    assert "Since yesterday at 10:00 AM" in screen(out) and "Since yesterday at 10:00 AM, for " in out["label"]
    assert "Last measured 52 milligrams per deciliter, October 18 at 12:00 PM" in out["label"]
    _at(1, 6)  # shortly after midnight
    out = _limit_card(125 * 60, 127 * 60, False)  # the same 2 h 5 min: since 11:01 PM the day before
    assert "Since yesterday at 11:01 PM (2 h 5 min)" in screen(out)
    assert "Since yesterday at 11:01 PM, for 2 hours 5 minutes." in out["label"]
    out = _limit_card(26 * 3600, 3 * 86400, False)  # 26 hours: two calendar days back, its date
    assert "Since October 19 at 11:06 PM, for " in out["label"]


# ------------------------------------------------------------------------------------------------ failures logged once
@needs_node
def test_a_failed_download_is_logged_once_per_reason_and_still_retried():
    out = node_json(
        textwrap.dedent("""
        const S = globalThis.glucocraftShared, warned = [];
        console.warn = (...a) => warned.push(a.join(" "));
        let answer = { ok: false, status: 401 }, calls = 0;
        const hass = { fetchWithAuth: async () => { calls++; return { ...answer, arrayBuffer: async () => new ArrayBuffer(8) }; } };
        const files = globalThis.__glucocraftFiles;
        const again = async () => { files.at.backfill = 0; return S.gcFile(hass, "backfill"); };
        await again(); await again();                             // the same failure twice: logged once
        answer = { ok: false, status: 500 }; await again();       // a new reason: logged
        answer = { ok: true, status: 200 }; await again();        // the answer is no GLU2 file: logged
        answer = { ok: false, status: 503 }; await again();       // no readings yet: a state, not a failure
        answer = { ok: false, status: 401 }; await again();       // after that, the first failure again: logged
        process.stdout.write("\\u0001" + JSON.stringify({ warned, calls, failed: S.gcFileFailed("backfill") }));
    """)
    )
    assert out["calls"] == 6 and out["failed"] == "error"
    assert len(out["warned"]) == 4, out["warned"]
    assert "HTTP 401" in out["warned"][0] and "HTTP 500" in out["warned"][1] and "not GLU2 v3" in out["warned"][2]
    assert all("backfill" in w and "tried again" in w for w in out["warned"])


@needs_node
def test_the_daily_values_log_a_failed_download_once():
    out = _ui(
        textwrap.dedent("""
        const warned = []; console.warn = (...a) => warned.push(a.join(" "));
        const c = new (customElements.get("glucocraft-daily-range"))(); c.setConfig({});
        let status = 500;
        c._hass = { fetchWithAuth: async () => ({ ok: status === 200, status, json: async () => ({ unit: "mg/dL", daily: [] }) }) };
        const box = { innerHTML: "" };
        await c.dailyData(box, "t", 14); c._dailyFailed = 1; await c.dailyData(box, "t", 14);
        status = 200; c._dailyFailed = 1; await c.dailyData(box, "t", 14);
        status = 500; c._daily = null; c._dailyFailed = 1; await c.dailyData(box, "t", 14);
        return warned;
    """)
    )
    assert len(out) == 2 and all("HTTP 500" in w and "14 days" in w for w in out), out


# ------------------------------------------------------------------------------------------------ mistyped card options
_OPTION = """
  const warned = []; console.warn = (...a) => warned.push(a.join(" "));
  const c = new (customElements.get("{tag}"))(); c.setConfig({config});
  c._hass = {{ states: {{ "sensor.glucocraft_configuration": {{ attributes: {{ unit: "mg/dL" }} }} }},
    fetchWithAuth: async () => ({{ ok: false, status: 500 }}) }};
  await c.draw(); await c.draw();
  return {{ html: c.shadowRoot.innerHTML, warned }};
"""


@needs_node
@pytest.mark.parametrize(
    "tag,config,option,value,allowed",
    [
        ("glucocraft-change-chart", {"metric": "tirr"}, "metric", "tirr", "tir, titr, below"),
        ("glucocraft-stats-table", {"periods": ["7d", "1w"]}, "periods", "1w", "6h, 24h, 7d, 14d, 30d, 90d, 180d"),
        ("glucocraft-stats-table", {"periods": "7d"}, "periods", "7d", "6h, 24h"),
        ("glucocraft-stats-table", {"warn_periods": ["2d"]}, "warn_periods", "2d", "6h, 24h"),
        ("glucocraft-daily-range", {"days": 365}, "days", "365", "1–200"),
        ("glucocraft-daily-range", {"days": 0}, "days", "0", "1–200"),
    ],
)
def test_a_mistyped_card_option_is_named_on_the_card(tag, config, option, value, allowed):
    out = _ui(_OPTION.format(tag=tag, config=json.dumps(config)))
    text = FE["ui.card.option_error"].split("{")[0]
    assert text in out["html"] and f"{option}" in out["html"] and value in out["html"] and allowed in out["html"]
    assert len(out["warned"]) == 1 and option in out["warned"][0], out["warned"]


@needs_node
@pytest.mark.parametrize(
    "tag,config",
    [
        ("glucocraft-change-chart", {"metric": "gri"}),
        ("glucocraft-change-chart", {}),
        ("glucocraft-stats-table", {"periods": ["14d", "30d"], "warn_periods": ["30d"]}),
        ("glucocraft-daily-range", {"days": 200}),
        ("glucocraft-daily-range", {}),
    ],
)
def test_valid_card_options_draw_no_option_message(tag, config):
    out = _ui(_OPTION.format(tag=tag, config=json.dumps(config)))
    assert FE["ui.card.option_error"].split("{")[0] not in out["html"]
    assert not any("card option" in w for w in out["warned"])


# ------------------------------------------------------------------------------------------------ the Documentation's focus
@needs_node
def test_the_documentation_drawn_again_keeps_the_focus_on_its_control():
    states = {"sensor.glucocraft_configuration": {"state": "ok", "attributes": {"unit": "mg/dL", "ranges": None}}}
    script = (
        i18n_prelude(areas=("frontend", "documentation"))
        + FAKE_DOM
        + "globalThis.setTimeout = () => 0;\n"
        + (WWW / "glucocraft-docs.js").read_text(encoding="utf-8")
        + textwrap.dedent(f"""
        const el = new (customElements.get("glucocraft-documentation"))(); el.setConfig({{}});
        const states = {json.dumps(states)};
        el.hass = {{ states, language: "en" }};
        const root = el.shadowRoot, before = root.querySelector('.sh button[data-sec="references"]');
        before.focus();
        // a setting the text shows changes: the page is written anew
        states["sensor.glucocraft_configuration"].attributes = {{ unit: "mmol/L" }};
        el.hass = {{ states, language: "en" }};
        const after = root.activeElement;
        out([after !== before, after?.dataset?.sec, root.querySelector('.sh button[data-sec="references"]') === after]);
    """)
    )
    assert node_json(script, prelude=False) == [True, "references", True]


# ------------------------------------------------------------------------------------------------ shared rules
@needs_node
def test_the_axis_labels_are_the_earlier_rules_of_each_graph():
    """The display card's labels (toFixed of the step's decimals, then the decimal separator) and the CGM Report
    page's (mmol/L: one decimal below a step of 1, mg/dL: the whole number) are those of gcAxisText."""
    out = node_json(
        textwrap.dedent("""
        const S = globalThis.glucocraftShared, bad = [];
        for (const dec of [".", ","]) {
          S.settings.dec = dec;
          for (const step of [0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 20, 25, 50, 100])
            for (let k = 0; k <= 400; k++) {
              const v = k * step, card = S.dtx(v.toFixed(S.gcAxisDecimals(step)));
              if (S.gcAxisText(v, step) !== card) bad.push(["card", dec, step, v]);
            }
          for (const st of [0.5, 1, 2, 5, 10]) for (let k = 0; k <= 60; k++) {
            const v = k * st, report = S.nf(v, st < 1 ? 1 : 0);
            if (S.gcAxisText(v, st) !== report) bad.push(["report mmol", dec, st, v]);
          }
          for (const st of [5, 10, 20, 25, 50, 100]) for (let k = 0; k <= 60; k++) {
            const v = k * st;
            if (S.gcAxisText(v, st) !== S.dtx(v)) bad.push(["report mg", dec, st, v]);
          }
        }
        S.settings.dec = ".";
        process.stdout.write("\\u0001" + JSON.stringify(bad.slice(0, 5)));
    """)
    )
    assert out == []


def test_the_shared_values_are_taken_from_the_shared_module():
    shared = JS["shared"]
    for name in ("CONS_RANGES", "STALE_DEFAULT_MIN", "RETENTION_DAYS", "gcCap", "gcNewFailure", "gcRefocus"):
        assert f" {name}," in shared.split("globalThis.glucocraftShared = Object.freeze({", 1)[1], name
    assert 'sr: ".sr { position: absolute;' in shared
    for name in ("display-card", "ui", "report", "docs", "strategy"):
        assert ".sr { position: absolute;" not in JS[name], name  # the shared css.sr
    assert "const cap = " not in JS["display-card"] and "const gcCap = " not in JS["ui"]
    assert ".toFixed(gcAxisDecimals" not in JS["display-card"] and "nf(gd, st < 1 ? 1 : 0)" not in JS["report"]


@needs_node
def test_the_shared_and_language_modules_report_their_version():
    shown = node_json(
        "const info = [];\nconsole.info = (...a) => info.push(a[0]);\n"
        + (WWW / "glucocraft-i18n.js").read_text(encoding="utf-8")
        + "\nglobalThis.glucocraftI18n.add('en', 'frontend', {});\n"
        + (WWW / "glucocraft-shared.js").read_text(encoding="utf-8")
        + "\n"
        + (WWW / "glucocraft-shared.js").read_text(encoding="utf-8").replace("const GCSHARED_VERSION", "const V2")
        + "\nprocess.stdout.write('\\u0001' + JSON.stringify(info));",
        prelude=False,
    )
    assert [s.split(" v")[0] for s in shown] == [
        "%c GLUCOCRAFT-I18N %c",
        "%c GLUCOCRAFT-SHARED %c",
    ]  # a second load: none
