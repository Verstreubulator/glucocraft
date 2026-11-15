# License: AGPL-3.0-only (LICENSE).
"""The texts of the dashboard pages and the Documentation as the user sees and hears them.

- Every text card of the dashboard (About This Page, the Maintenance and Settings Status texts) is GlucoCraft's own
  text card (custom:glucocraft-markdown, glucocraft-strategy.js), never Home Assistant's Markdown card: GlucoCraft's
  link color (4.5:1 in Home Assistant's default themes) and focus ring, no text below 14 px, the language of the
  catalogue (lang), and tables with a name, header cells with their scope and a scroll region that the keyboard
  reaches; a text with a Jinja template is rendered by Home Assistant (render_template), and the four tables of the
  pages are rendered from their templates here.
- The decimal comma never rewrites a cited locator ("Table 6.2", "Recommendation 6.3b") in the cited-context texts.
- The Documentation's tables: every header cell has its scope, also in a text of a language that leaves it out.
- A placeholder is filled only from the parameters' own properties ("{constructor}" stays as written).
- No dashboard file formats a date, weekday, month or clock time with the browser's locale (toLocale*,
  Intl.DateTimeFormat): they follow GlucoCraft's own format settings.
- The ARIA states and roles of the dashboard are documented in docs/ACCESSIBILITY.md.
"""

from __future__ import annotations

import json
import re
import textwrap
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import jinja2
import pytest

from js_i18n import WWW, catalogue
from node_runner import needs_node, node_json

REPO = Path(__file__).resolve().parents[1]
STRAT = (WWW / "glucocraft-strategy.js").read_text(encoding="utf-8")
I18N = (WWW / "glucocraft-i18n.js").read_text(encoding="utf-8")
FE = catalogue("frontend")
CFG = "sensor.glucocraft_configuration"
RANGES = {"very_low": 54, "low": 70, "tight_low": 70, "tight_high": 140, "high": 180, "very_high": 250}
ATTRS = {
    "unit": "mg/dL",
    "ranges": RANGES,
    "consensus_ranges": {f"range_{k}": v for k, v in RANGES.items()},
    "primary_source": {"name": "Libre <b>Link</b>", "in_use": True},
    "no_current_data_minutes": 15,
}
# the Last reading time sensor's attributes: the values that change with every reading (each source's newest
# measurement time, the reading count)
LAST_ATTRS = {"primary_last_measurement": "2026-11-03T08:00:00+00:00", "secondary_last_measurement": None,
              "reading_count": 1234, "last_reading_ts": 1_793_000_000}  # fmt: skip

# a browser for the strategy file in node: custom elements, a shadow root whose ha-card and text box keep their HTML,
# the history and events of a link that opens a page of the dashboard
DOM = textwrap.dedent("""
    globalThis.window = globalThis; globalThis.location = { pathname: "/glucocraft-home/start" };
    globalThis.history = { pushState: (s, t, url) => { globalThis.__pushed = url; } };
    globalThis.CustomEvent = class { constructor(type) { this.type = type; } };
    globalThis.dispatchEvent = (e) => { globalThis.__event = e.type; };
    const box = () => ({ innerHTML: "", attrs: {}, setAttribute(k, v) { this.attrs[k] = String(v); },
      contains: () => false, querySelectorAll: () => [] });
    globalThis.HTMLElement = class {
      attachShadow() {
        const card = box(), md = box();
        this.shadowRoot = { innerHTML: "", activeElement: null, card, md,
          querySelector(sel) { return sel === "ha-card" ? (this.innerHTML ? card : null) : sel === ".md" ? md : null; },
          querySelectorAll: () => [] };
        return this.shadowRoot;
      }
    };
    const reg = {};
    globalThis.customElements = { get: (n) => reg[n], define: (n, c) => { reg[n] = c; } };
""")
IMPORT = f"await import({json.dumps((WWW / 'glucocraft-strategy.js').as_uri())});\n"


def _dashboard(attrs: dict, devices: dict | None = None, entities: dict | None = None) -> dict:
    """The dashboard the strategy generates for the configuration attributes attrs (and these devices and entities)."""
    states = {CFG: {"state": "ok", "attributes": attrs}}
    return node_json(
        DOM
        + IMPORT
        + f"""const d = await reg["ll-strategy-dashboard-glucocraft"].generate({{}}, {{ states: {json.dumps(states)},
          devices: {json.dumps(devices or {})}, entities: {json.dumps(entities or {})} }});
        process.stdout.write("\\u0001" + JSON.stringify(d));"""
    )


def _cards(node) -> list[dict]:
    """Every card of a dashboard, a view or a section, nested cards included."""
    if isinstance(node, list):
        return [c for x in node for c in _cards(x)]
    if not isinstance(node, dict):
        return []
    own = [node] if "type" in node and "sections" not in node and "views" not in node else []
    return own + [c for k in ("views", "sections", "cards") for c in _cards(node.get(k, []))]


def _html(text: str, names: list[str] | None = None) -> str:
    """The text card's HTML of a Markdown text."""
    return node_json(
        DOM + IMPORT + f'process.stdout.write("\\u0001" + JSON.stringify(reg["glucocraft-markdown"].html('
        f"{json.dumps(text)}, {json.dumps(names)})));"
    )


class Tables(HTMLParser):
    """The tables of an HTML text: per table its caption, its header cells (scope, text), its rows and its scroll
    region (the attributes of the element around it)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.open, self.region, self.cell = [], [], None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div" and a.get("role") == "region":
            self.region = a
        elif tag == "table":
            self.tables.append({"region": self.region, "caption": None, "caption_id": None, "th": [], "rows": 0})
            self.region = None
        elif tag == "caption":
            self.tables[-1]["caption_id"] = a.get("id")
            self.cell = ["caption", ""]
        elif tag == "th":
            self.cell = ["th", "", a.get("scope")]
        elif tag == "tr":
            self.tables[-1]["rows"] += 1

    def handle_data(self, data):
        if self.cell:
            self.cell[1] += data

    def handle_endtag(self, tag):
        if tag == "caption" and self.cell:
            self.tables[-1]["caption"] = self.cell[1]
            self.cell = None
        elif tag == "th" and self.cell:
            self.tables[-1]["th"].append((self.cell[2], self.cell[1].strip()))
            self.cell = None


def _tables(html: str) -> list[dict]:
    p = Tables()
    p.feed(html)
    p.close()
    return p.tables


def _assert_accessible_table(table: dict, name: str) -> None:
    """A table named by its caption, in a focusable scroll region named by that caption; every header cell scoped:
    the header row's cells as columns, the first cell of each other row as its row."""
    assert table["caption"] == name and table["caption_id"]
    region = table["region"]
    assert region and region.get("tabindex") == "0" and region.get("aria-labelledby") == table["caption_id"]
    scopes = [scope for scope, _text in table["th"]]
    assert None not in scopes, table["th"]
    assert scopes.count("row") == table["rows"] - 1 and scopes.count("col") >= 2, table["th"]


@pytest.fixture(scope="module")
def dashboard() -> dict:
    return _dashboard(ATTRS)


# ------------------------------------------------------------------------------------------------ the text card
@needs_node
def test_every_text_card_of_the_dashboard_is_glucocrafts_with_its_tables_named(dashboard):
    cards = _cards(dashboard)
    assert not [c for c in cards if c["type"] == "markdown"]
    texts = [c for c in cards if c["type"] == "custom:glucocraft-markdown"]
    # About This Page on every page but the Documentation, the texts of Maintenance, Settings Status, Custom Ranges
    assert len(texts) >= 12 and all(isinstance(c["content"], str) and c["content"] for c in texts)
    named = 0
    for card in texts:
        count = len(re.findall(r"^\|-", card["content"], re.MULTILINE))
        assert len(card.get("tables", [])) == count, card["content"][:120]
        assert all(isinstance(n, str) and n.strip() for n in card.get("tables", []))
        named += count
    assert named == 3  # sources, Settings Status, requirements (the panel table only while a panel exists)
    assert "custom:glucocraft-markdown" not in json.dumps(dashboard["views"][-1])  # the Documentation: its own card


@needs_node
def test_the_text_card_markup():
    html = _html(
        "**Bold** and *italic* and `sensor.glucocraft_<period>_x` <script>alert(1)</script>\nsecond line\n\n"
        "- [Settings Status](/glucocraft-home/settings)\n- [the article](https://doi.org/10.2337/dci19-0028)\n"
        "- [no](javascript:alert) [no](//example.com)\n\n1. one\n2. two\n\n"
        "| Source | Time | In use |\n|---|---|---|\n| A | 8:00 | ✓ |\n| B |\n**Last reading:** 8:00",
        ["Glucose sources"],
    )
    assert "<p><b>Bold</b> and <i>italic</i> and <code>sensor.glucocraft_&lt;period&gt;_x</code> " in html
    assert "&lt;script&gt;" in html and "<script" not in html and "<br>second line" in html
    assert '<a href="/glucocraft-home/settings" data-nav="/glucocraft-home/settings">Settings Status</a>' in html
    assert (
        '<a href="https://doi.org/10.2337/dci19-0028" target="_blank" rel="noopener">the article'
        '<span class="sr"> (opens in a new tab)</span></a>'
    ) in html
    assert "javascript" not in html and "example.com" not in html and "<li>no no</li>" in html
    assert "<ol><li>one</li><li>two</li></ol>" in html
    (table,) = _tables(html)
    _assert_accessible_table(table, "Glucose sources")
    assert table["th"] == [("col", "Source"), ("col", "Time"), ("col", "In use"), ("row", "A"), ("row", "B")]
    assert "<td></td><td></td></tr>" in html  # a short row filled to the header's cells
    # the line under the table is a paragraph, not a row of it
    assert html.endswith("</table></div><p><b>Last reading:</b> 8:00</p>")


def _card_html(content: str, script: str = "", tables: list | None = None) -> dict:
    """Draw a text card: its root HTML (style, ha-card), its text box, the language and what the script recorded."""
    return node_json(
        DOM
        + IMPORT
        + f"""const el = new (customElements.get("glucocraft-markdown"))(); el.isConnected = true;
        const sent = [], stopped = [];
        {script}
        el.setConfig({{ content: {json.dumps(content)}, tables: {json.dumps(tables)} }});
        el.hass = globalThis.__hass || {{ states: {{}}, language: "en" }};
        await new Promise((r) => setTimeout(r, 0));
        const root = el.shadowRoot, first = root.md.innerHTML;
        globalThis.__after?.(el);
        await new Promise((r) => setTimeout(r, 0));
        process.stdout.write("\\u0001" + JSON.stringify({{ root: root.innerHTML, md: root.md.innerHTML, first,
          lang: root.card.attrs.lang, sent, stopped }}));"""
    )


@needs_node
def test_the_text_card_draws_the_link_color_the_focus_ring_its_language_and_no_text_below_14_px():
    out = _card_html("A [link](/glucocraft-home/documentation#colors).")
    assert out["lang"] == "en"
    assert out["md"] == (
        '<p>A <a href="/glucocraft-home/documentation#colors" data-nav="/glucocraft-home/documentation#colors">link</a>.</p>'
    )
    css = out["root"].split("</style>")[0]
    assert "a { color: color-mix(in srgb, var(--primary-color) 50%, var(--primary-text-color)); }" in css
    assert (
        "a:focus-visible, .tw:focus-visible { outline: 3px solid var(--primary-text-color); outline-offset: 2px; }"
        in css
    )
    sizes = [int(s) for s in re.findall(r"font-size:\s*(\d+)px", css)]
    assert sizes and min(sizes) >= 14 and "em;" not in css and "%;" not in css.replace("100%;", "")
    assert "var(--primary-color)" not in css.replace("color-mix(in srgb, var(--primary-color) 50%", "")


@needs_node
def test_the_contrast_of_the_text_cards_links_in_the_default_themes():
    """The links of the text card are drawn in the color the contrast check measures (4.5:1 or more on Home
    Assistant's default light and dark cards with its default primary colors, where the primary color itself reaches
    3.26:1 on white)."""
    from test_accessibility_checks import HA_PRIMARY, HA_THEMES, contrast, over

    css = _card_html("[x](/a)")["root"]
    link = re.search(
        r"a \{ color: color-mix\(in srgb, var\(--primary-color\) (\d+)%, var\(--primary-text-color\)\)", css
    )
    share = int(link[1])
    for primary in HA_PRIMARY:
        for text, bg in HA_THEMES.values():
            assert contrast(over(primary, text, share / 100), bg) >= 4.5, (primary, bg)


@needs_node
def test_a_template_is_rendered_by_home_assistant_and_shown_when_it_changes():
    content = "{{ states('sensor.x') }} and | {% if true %}y{% endif %}"
    script = """
        let push;
        globalThis.__hass = { states: {}, language: "en", connection: { subscribeMessage(cb, msg) {
          sent.push(msg); push = cb; cb({ result: "**One**" }); return Promise.resolve(() => stopped.push(1)); } } };
        globalThis.__after = (el) => { push({ result: "**Two**" }); el.disconnectedCallback(); };
    """
    out = _card_html(content, script)
    assert out["sent"] == [{"type": "render_template", "template": content, "strict": True}]
    assert out["first"] == "<p><b>One</b></p>" and out["md"] == "<p><b>Two</b></p>" and out["stopped"] == [1]
    # a template that Home Assistant refuses is shown as written, as its Markdown card shows it
    refused = """globalThis.__hass = { states: {}, language: "en", connection: {
      subscribeMessage(cb, msg) { sent.push(msg); return Promise.reject(new Error("no")); } } };"""
    out = _card_html("{{ x }}", refused)
    assert out["md"] == "<p>{{ x }}</p>" and len(out["sent"]) == 1
    # a text without a template is not sent
    out = _card_html("plain", refused)
    assert out["md"] == "<p>plain</p>" and out["sent"] == []


# ------------------------------------------------------------------------------------------------ the four tables
def _jinja() -> jinja2.Environment:
    """Jinja with the Home Assistant functions and filters the page templates use, on the configuration ATTRS."""
    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    attrs = {CFG: ATTRS, "sensor.glucocraft_sensor_expiry": {}, "sensor.glucocraft_last_reading_time": LAST_ATTRS}
    env.globals.update(
        state_attr=lambda e, a: attrs.get(e, {}).get(a),
        is_state=lambda e, s: e.endswith("_status") and s == "on",
        as_timestamp=lambda v, d=None: 1_793_000_000.0 if v else d,
        state_translated=lambda e: "Not expired",
    )
    env.globals["states"] = _States()
    env.filters["timestamp_custom"] = _timestamp_custom
    return env


def _timestamp_custom(ts, fmt, local=True, default=None) -> str:
    """Home Assistant's timestamp_custom for one fixed time, Tuesday 3 November 2026 8:00 (strftime's codes, "%-" for
    a number without its leading zero); the page writes the weekday and AM / PM from its catalogue, never %a or %p."""
    t = datetime(2026, 11, 3, 8, 0)
    out = fmt
    for code in ("%-d", "%-m", "%-I"):
        out = out.replace(code, str(int(t.strftime("%" + code[-1]))))
    return t.strftime(out)


class _States:
    """states.sensor.x (the configuration's attributes) and states('entity') (a panel's uptime in seconds)."""

    sensor = type("S", (), {"glucocraft_configuration": type("C", (), {"attributes": ATTRS})()})()

    def __call__(self, entity: str) -> str:
        return "3600"


@needs_node
def test_the_four_tables_of_the_pages_are_named_scoped_and_scroll_in_a_named_region():
    devices = {
        "d1": {"id": "d1", "manufacturer": "glucocraft", "name": "Panel A", "model": "panel", "sw_version": "1.0"}
    }
    entities = {
        "binary_sensor.panel_a_status": {"entity_id": "binary_sensor.panel_a_status", "device_id": "d1"},
        "sensor.panel_a_uptime": {"entity_id": "sensor.panel_a_uptime", "device_id": "d1"},
    }
    dash = _dashboard(ATTRS, devices, entities)
    env = _jinja()
    found = {}
    for card in _cards(dash):
        if card["type"] == "custom:glucocraft-markdown" and card.get("tables"):
            text = env.from_string(card["content"]).render()
            for table in _tables(_html(text, card["tables"])):
                found[table["caption"]] = table
    names = {
        FE["strategy.maintenance.sources.table_name"]: 1,
        FE["strategy.maintenance.panels.heading"]: 1,
        FE["strategy.settings.current.table_name"]: 5,
        FE["strategy.settings.requirements.heading"]: 13,
    }
    assert set(found) == set(names)
    for name, rows in names.items():
        _assert_accessible_table(found[name], name)
        assert found[name]["rows"] - 1 == rows, name
    # a source name is shown as text, never as markup
    name = FE["strategy.maintenance.sources.table_name"]
    card = next(c for c in _cards(dash) if c.get("tables") == [name])
    sources = _html(env.from_string(card["content"]).render(), [name])
    assert "Libre &lt;b&gt;Link&lt;/b&gt; (primary)" in sources


# ------------------------------------------------------------------------------------------------ the decimal comma
@needs_node
def test_the_decimal_comma_keeps_the_point_of_every_cited_locator():
    cited = {k: (" ".join(v) if isinstance(v, list) else v) for k, v in FE.items() if k.startswith("strategy.cx.")}
    locators = [m for v in cited.values() for m in re.findall(r"(?:Table|Recommendation)[  ]\d+\.\d+\w?", v)]
    assert len(locators) >= 8
    out = node_json(
        "const S = globalThis.glucocraftShared; S.settings.dec = ',';"
        f"const out = Object.values({json.dumps(cited)}).map(S.dtx); S.settings.dec = '.';"
        "process.stdout.write('\\u0001' + JSON.stringify(out));"
    )
    text = "\n".join(out)
    for locator in locators:
        assert locator in text, locator
    assert not re.search(r"(?:Table|Recommendation)[  ]\d+,\d", text)
    assert "3,9 mmol/L" in text  # the other numbers take the comma
    # on the pages: About This Page with the decimal comma
    about = json.dumps(_dashboard({**ATTRS, "decimal_separator": ","}), ensure_ascii=False)
    assert "Table 6.2" in about and not re.search(r"(?:Table|Recommendation) \d+,\d", about)


# ------------------------------------------------------------------------------------------------ the Documentation
@needs_node
def test_every_header_cell_of_the_documentation_has_its_scope():
    from test_documentation_rendering import AGP, _render

    tables = _tables(_render({"ranges": AGP}))
    assert len(tables) >= 12
    for table in tables:
        assert table["th"] and all(scope == "col" for scope, _ in table["th"]), table["th"]
    # a text of a language without scope: the page adds it
    body = "<table><tr><th>Page</th><th>Content</th></tr><tr><th>Start</th><td>x</td></tr><tr><td>y</td><td>z</td></tr></table>"
    html = _render({"ranges": AGP}, {"documentation": {"docs.section.pages.body": body}})
    assert (
        '<tr><th scope="col">Page</th><th scope="col">Content</th></tr><tr><th scope="row">Start</th><td>x</td></tr>'
    ) in html


# ------------------------------------------------------------------------------------------------ placeholders
@needs_node
def test_a_placeholder_is_filled_only_from_the_parameters_own_properties():
    out = node_json(
        "const I = globalThis.glucocraftI18n; I.add('en', 'test', { 'test.k': '{constructor} {toString} {a}' });"
        "process.stdout.write('\\u0001' + JSON.stringify(I.t('test.k', { a: 1 })));"
    )
    assert out == "{constructor} {toString} 1"
    assert "name in params" not in I18N and "Object.hasOwn(params, name)" in I18N
    assert "in exprs" not in STRAT and "Object.hasOwn(exprs, " in STRAT


# ------------------------------------------------------------------------------------------------ dates and times
ALLOWED_INTL = {"PluralRules", "ListFormat", "getCanonicalLocales"}


def test_no_dashboard_file_formats_dates_or_times_with_the_browsers_locale():
    """Dates, weekdays, months and clock times follow GlucoCraft's time and date format settings
    (glucocraft-shared.js): never the browser's locale. Intl is used only for the plural category of a number
    (PluralRules), a list of names ("a and b", ListFormat), the language tag (getCanonicalLocales) and, once, for the
    date and clock time of an instant in Home Assistant's time zone (DateTimeFormat in glucocraft-shared.js: a fixed
    locale and numeric parts only, read with formatToParts, never shown as formatted)."""
    files = sorted(WWW.glob("*.js"))
    assert len(files) >= 7
    for path in files:
        js = path.read_text(encoding="utf-8")
        assert not re.search(r"\.toLocale\w*\(", js), path.name
        assert not re.search(r".*\.to(?:Date|Time|UTC)String\(.*", js), path.name
        assert not re.search(r"\bIntl\.(?:RelativeTimeFormat|NumberFormat|DurationFormat)\b", js), path.name
        zone = path.name == "glucocraft-shared.js"
        assert set(re.findall(r"\bIntl\.(\w+)", js)) <= ALLOWED_INTL | ({"DateTimeFormat"} if zone else set()), (
            path.name
        )
        if zone:  # the one DateTimeFormat: the parts of an instant in the time zone, in a fixed locale
            assert js.count("Intl.DateTimeFormat") == 1
            assert 'new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", weekday: "short"' in js
            assert js.count("tzFormat.format") == 1 and "tzFormat.formatToParts(ms)" in js
        # a list format only joins names
        for use in re.findall(r"new Intl\.ListFormat\([^)]*\)\.format\(([^;]*)\)", js):
            assert "Date" not in use and "clock" not in use, (path.name, use)


# ------------------------------------------------------------------------------------------------ ARIA
def test_every_aria_state_and_role_of_the_dashboard_is_documented():
    rules = (REPO / "docs" / "ACCESSIBILITY.md").read_text(encoding="utf-8")
    code = "\n".join(p.read_text(encoding="utf-8") for p in sorted(WWW.glob("*.js")))
    # every ARIA attribute and every role the card files write, found in the code itself
    attrs = set(re.findall(r"\baria-[a-z]+", code))
    roles = set(re.findall(r'\brole="[a-z]+"', code))
    assert {
        "aria-expanded",
        "aria-disabled",
        "aria-pressed",
        "aria-live",
        'role="img"',
        'role="group"',
    } <= attrs | roles
    for attr in sorted(attrs):
        assert f"`{attr}" in rules, attr  # `aria-live="polite"` names aria-live with its value
    for role in sorted(roles):
        assert f"`{role}`" in rules, role


# ------------------------------------------------------------------------------------------------ rounding and time stamps
def _maintenance_text(attrs: dict) -> str:
    """The Maintenance page's sources text, rendered by Jinja for the configuration attributes attrs."""
    card = next(
        c for c in _cards(_dashboard(attrs)) if c.get("tables") == [FE["strategy.maintenance.sources.table_name"]]
    )
    env = _jinja()
    env.globals["state_attr"] = lambda e, a: {CFG: attrs, "sensor.glucocraft_last_reading_time": LAST_ATTRS}.get(
        e, {}
    ).get(a)
    return env.from_string(card["content"]).render()


@needs_node
def test_the_page_templates_round_halves_away_from_zero_as_everywhere():
    """Jinja's round rounds a half to even (17.25 minutes would show "17.2"); the page templates round once, halves
    away from zero, as the rest of GlucoCraft: a gap threshold of 1035 s is "17.3" minutes."""
    text = _maintenance_text({**ATTRS, "gap_threshold_seconds": 1035})
    assert "**gap after:** 17.3 minutes" in text
    js = (WWW / "glucocraft-strategy.js").read_text(encoding="utf-8")
    gj = js[js.index("const GJ = ") : js.index('"{%- endmacro -%}";', js.index("const GJ = "))]
    assert "| round(1)" not in gj and "| round(0) |" not in gj and "round(0, 'floor')" in gj


@needs_node
def test_time_stamps_take_the_weekday_and_am_pm_from_the_catalogue():
    """The Maintenance page's time stamps: the weekday's short name and AM / PM are the catalogue's words (never the
    server's locale through %a or %p), the numbers in the date and time formats of the settings."""
    js = (WWW / "glucocraft-strategy.js").read_text(encoding="utf-8")
    assert not re.search(r"['\s]%a|%p'", js.replace("strftime's %a and %p", ""))  # no strftime weekday or AM / PM
    assert "| Tue 11/3 8:00 AM |" in _maintenance_text(ATTRS)
    assert "| Tue 3/11 08:00 |" in _maintenance_text({**ATTRS, "date_format": "DMY", "time_format": "24"})


@needs_node
def test_the_text_card_speaks_unit_symbols_and_comparison_signs_in_words():
    """The text card's prose passes the spoken rule: a text with a unit symbol is shown as written and read in words;
    a code element keeps its text."""
    html = _html("The tight range is 70–140 mg/dL, `mg/dL` in code.")
    assert '<span aria-hidden="true">The tight range is 70–140 mg/dL, </span>' in html
    assert '<span class="sr">The tight range is 70 to 140 milligrams per deciliter, </span>' in html
    assert "<code>mg/dL</code>" in html
