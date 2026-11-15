# License: AGPL-3.0-only (LICENSE).
"""The rendered Documentation page (glucocraft-docs.js with i18n/en/documentation.json).

- Every section link is written with the title of the section it opens.
- The decimal separator setting rewrites numbers in the text, never a citation locator ("Table 6.2", "Recommendation
  6.3a"): every locator lies inside a part the page leaves alone (the walker's skip list, read from the code).
- The Colors table: the in-range rows outside the tight range exist only where the tight range does not reach the
  in-range limit (no inverted "181–180"), and mg/dL values are whole numbers also for limits stored with a decimal
  (a limit entered in mmol/L).
- A change of a configuration value the text does not use (a source's live reading interval) does not draw the page
  again; a changed value of the text does.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

import pytest

from js_i18n import WWW, catalogue, i18n_prelude
from node_runner import needs_node, node_json

DOCS_JS = (WWW / "glucocraft-docs.js").read_text(encoding="utf-8")
DOC = catalogue("documentation")
FE = catalogue("frontend")
# a minimal browser for the card: custom elements, a shadow root that keeps its HTML, no tree walker (the decimal
# separator is applied below, by the rule the page applies)
RENDER = """
globalThis.window = globalThis; globalThis.location = { hash: "" };
const reg = {}; globalThis.customElements = { get: (n) => reg[n], define: (n, c) => { reg[n] = c; } };
globalThis.HTMLElement = class { attachShadow() { this.shadowRoot = { innerHTML: "", querySelectorAll: () => [],
  getElementById: () => ({}), querySelector: () => null }; return this.shadowRoot; } };
globalThis.document = { createTreeWalker: () => ({ nextNode: () => null }) }; globalThis.NodeFilter = { SHOW_TEXT: 4 };
"""
AGP = {"very_low": 54, "low": 70, "tight_low": 70, "tight_high": 140, "high": 180, "very_high": 250}
LOCATOR = re.compile(r"\b(?:Table|Recommendation) \d+\.\d")


def _text(value) -> str:
    return "".join(value) if isinstance(value, list) else value


def _render(attrs: dict, translation: dict | None = None) -> str:
    """The Documentation's HTML for the configuration attributes given; with `translation` (area -> {key: text}), in a
    language "xx" whose catalogues hold these texts, English for every other key."""
    states = {"sensor.glucocraft_configuration": {"state": "ok", "attributes": attrs}}
    areas = {"frontend": {}, "documentation": {}, **(translation or {})} if translation else {}
    adds = "".join(
        f"globalThis.glucocraftI18n.add('xx', {json.dumps(a)}, {json.dumps(x)});\n" for a, x in areas.items()
    )
    src = (
        i18n_prelude(areas=("frontend", "documentation"))
        + adds
        + RENDER
        + DOCS_JS
        + "\nconst el = new (customElements.get('glucocraft-documentation'))(); el.setConfig({});"
        + f"\nel.hass = {{ states: {json.dumps(states)}, language: '{'xx' if translation else 'en'}' }};"
        + "\nprocess.stdout.write('\\u0001' + JSON.stringify(el.shadowRoot.innerHTML));"
    )
    return node_json(src, prelude=False)


def _colors_table(html: str) -> str:
    """The Colors table of Ranges, Terminology and Colors (the one with the column Display color)."""
    head = re.escape(_text(DOC["docs.colors.head.color"]))
    return re.search(
        rf"<table[^>]*><tr><th scope=\"col\">[^<]*</th><th scope=\"col\">{head}</th>.*?</table>", html, re.DOTALL
    ).group(0)


class _Walker(HTMLParser):
    """The text nodes of the page, each with whether the decimal separator setting leaves it alone: the selector of
    the page's skip list (closest("...") in draw), read from glucocraft-docs.js."""

    VOID = frozenset({"br", "hr", "img", "input", "meta", "link", "wbr"})

    def __init__(self, selector: str) -> None:
        super().__init__(convert_charrefs=True)
        self.rules = [s.strip() for s in selector.split(",")]
        self.stack: list[bool] = []
        self.nodes: list[tuple[str, bool]] = []

    def _skips(self, tag: str, attrs: dict) -> bool:
        classes = (attrs.get("class") or "").split()
        for rule in self.rules:
            if rule.startswith(".") and rule[1:] in classes:
                return True
            if rule == "a[href]" and tag == "a" and "href" in attrs:
                return True
            if rule.isalpha() and rule == tag:
                return True
        return False

    def handle_starttag(self, tag, attrs):
        """Open an element; it is skipped when it or an enclosing element is."""
        if tag in self.VOID:
            return
        self.stack.append((bool(self.stack) and self.stack[-1]) or self._skips(tag, dict(attrs)))

    def handle_endtag(self, tag):
        """Close the element."""
        if tag not in self.VOID and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        """Record the text with whether it is skipped."""
        self.nodes.append((data, bool(self.stack) and self.stack[-1]))


def test_section_links_carry_the_section_titles():
    for key, value in DOC.items():
        for sid, label in re.findall(r"\{sec:([^{}|]+)\|([^{}]*)\}", _text(value)):
            title = DOC.get(f"docs.section.{sid.replace('-', '_')}.title")
            if title is not None:  # an anchor inside a section (colors, other-groups) has its own words
                assert label == title, (key, sid, label)


@needs_node
def test_a_decimal_comma_never_rewrites_a_citation_locator():
    html = _render({"decimal_separator": ",", "ranges": AGP, "unit": "mg/dL"})
    selector = re.search(r'closest\("([^"]+)"\)', DOCS_JS).group(1)
    walker = _Walker(selector)
    walker.feed(html)
    rewritten = [text for text, skipped in walker.nodes if not skipped]
    assert any(re.search(r"\d\.\d", t) for t in rewritten)  # the setting does reach the text
    assert not [t for t in rewritten if LOCATOR.search(t)]
    assert '(<a class="cite" href="#ref-ada2026"' in html and '<span class="nodec">Table 6.2</span>)' in html


@needs_node
def test_a_decimal_comma_keeps_versions_and_the_disclaimer_word_for_word():
    """The page applies the shared rule (glucocraft-shared.js dtx) to the text it rewrites: a version, a license or a
    release keeps its point, and the disclaimer is marked nodec, so its "v3.0" and "AGPL-3.0" stay as written."""
    assert "dtx(n.nodeValue)" in DOCS_JS and "dtxD" not in DOCS_JS
    html = _render({"decimal_separator": ",", "ranges": AGP, "unit": "mg/dL"})
    selector = re.search(r'closest\("([^"]+)"\)', DOCS_JS).group(1)
    walker = _Walker(selector)
    walker.feed(html)
    rewritten = [text for text, skipped in walker.nodes if not skipped]
    shown = node_json(
        "const S = globalThis.glucocraftShared; S.settings.dec = ',';\n"
        f"process.stdout.write('\\u0001' + JSON.stringify({json.dumps(rewritten)}.map(S.dtx)));"
    )
    text = " ".join(shown)
    for name in ("WCAG 2.2", "Home Assistant 2026.", "Apache License 2.0"):
        assert name in text, name
    assert not re.search(r"WCAG\)? 2,2|License 2,0|AGPL-3,0|v3,0|\b20\d\d,\d", text)
    assert re.search(r"\d,\d", text)  # the values still take the comma
    disclaimer = re.search(r'<p class="nodec">([^<]*<b>[^<]*</b>[^<]*)</p>', html)
    assert disclaimer and "AGPL-3.0" in disclaimer.group(1)


@needs_node
@pytest.mark.parametrize(
    ("ranges", "absent"),
    [
        ({**AGP, "tight_high": 180}, "181–180"),  # the tight range reaches the high limit
        ({**AGP, "tight_low": 70, "low": 70}, "70–69"),  # and the low limit (the consensus default)
    ],
)
def test_no_inverted_row_when_the_tight_range_reaches_a_limit(ranges, absent):
    table = _colors_table(_render({"unit": "mg/dL", "ranges": ranges}))
    assert absent not in table
    spans = re.findall(r"<td>(\d+)–(\d+)</td>", table)
    assert spans and all(int(a) <= int(b) for a, b in spans), spans
    if ranges["tight_high"] == ranges["high"]:
        assert _text(DOC["docs.colors.range.near_high"]) not in table
        assert "<td>70–180</td>" in table and "<td>181–250</td>" in table


@needs_node
def test_mg_dl_values_are_whole_numbers_for_limits_stored_with_a_decimal():
    # limits entered in mmol/L are stored with one decimal in mg/dL (3.8 mmol/L = 68.5, 8.9 mmol/L = 160.3 mg/dL)
    ranges = {**AGP, "low": 68.5, "tight_high": 160.3}
    table = _colors_table(_render({"unit": "mg/dL", "ranges": ranges}))
    assert not re.search(r"\d\.\d", re.sub(r"<[^>]+>", " ", table)), table
    for span in ("54–68", "69–69", "70–160", "161–180", "181–250"):
        assert f"<td>{span}</td>" in table, span


@needs_node
def test_the_repository_address_is_a_link_in_install_entities_accessibility_and_about():
    """The page names the repository by its address, as a link with the address as its text: the manifest's
    documentation address (before a release, the placeholder that the release fills). It opens in a new tab and
    says so to screen readers."""
    address = json.loads((WWW.parent / "manifest.json").read_text(encoding="utf-8"))["documentation"]
    link = (
        f'<a href="{address}" target="_blank" rel="noopener">{address}<span class="sr"> (opens in a new tab)</span></a>'
    )
    html = _render({"unit": "mg/dL", "ranges": AGP})
    assert html.count(link) == 4
    for section in ("install", "entities", "accessibility", "about"):
        body = re.search(rf'<section class="doc" id="{section}">.*?</section>', html, re.DOTALL).group(0)
        assert link in body, section


@needs_node
def test_the_disclaimer_section_comes_first_open_with_the_disclaimer_once():
    """The first section is the Disclaimer, open at the first drawing, with the catalogue's disclaimer.text; the full
    text stands nowhere else on the page. The Essentials follow, open as well."""
    html = _render({"unit": "mg/dL", "ranges": AGP})
    sections = re.findall(r'<section class="doc" id="([a-z-]+)">', html)
    assert sections[:2] == ["disclaimer", "essentials"] and "terms" not in sections and "read-first" not in sections
    body = re.search(r'<section class="doc" id="disclaimer">.*?</section>', html, re.DOTALL).group(0)
    assert f'<p class="nodec">{FE["disclaimer.text"]}</p>' in body and html.count(FE["disclaimer.text"]) == 1
    for sid in ("disclaimer", "essentials"):
        assert f'data-sec="{sid}" aria-expanded="true"' in html, sid


@needs_node
def test_about_and_license_ends_with_the_trademarks():
    html = _render({"unit": "mg/dL", "ranges": AGP})
    body = re.search(r'<section class="doc" id="about">.*?</section>', html, re.DOTALL).group(0)
    assert body.index("WITHOUT ANY WARRANTY") < body.index("License text:") < body.index(">Trademarks</h3>")
    assert "GlucoCraft is not affiliated with, endorsed by or sponsored by any of them." in body


@needs_node
def test_a_value_the_text_does_not_use_does_not_draw_the_page_again():
    """The configuration sensor also changes for values the text does not use (a source's live reading interval); the
    page is drawn again only when a value its text uses changes (a sensor limit, a setting), so the reader's position
    is kept."""
    source = {"name": "Libre", "reading_interval_seconds": 60, "in_use": True, "lower_limit": 40,
              "upper_limit": 400, "lower_limit_setting": "auto", "upper_limit_setting": "auto"}  # fmt: skip
    attrs = {"unit": "mg/dL", "ranges": AGP, "sources": {"primary": source}}
    later = {**attrs, "sources": {"primary": {**source, "reading_interval_seconds": 61}}}
    limit = {**attrs, "sources": {"primary": {**source, "lower_limit": 39}}}

    def states(a: dict) -> dict:
        return {"sensor.glucocraft_configuration": {"state": "ok", "attributes": a}}

    src = (
        i18n_prelude(areas=("frontend", "documentation"))
        + RENDER
        + DOCS_JS
        + "\nconst el = new (customElements.get('glucocraft-documentation'))(); el.setConfig({});"
        + "\nlet draws = 0; const draw = el.draw.bind(el); el.draw = () => { draws++; draw(); };"
        + "\nconst counts = [];"
        + "".join(
            f"\nel.hass = {{ states: {json.dumps(states(a))}, language: 'en' }}; counts.push(draws);"
            for a in (attrs, attrs, later, limit)
        )
        + "\nprocess.stdout.write('\\u0001' + JSON.stringify(counts));"
    )
    assert node_json(src, prelude=False) == [1, 1, 1, 2]


@needs_node
def test_the_voice_example_is_marked_and_cited():
    """The one voice example (Accessibility, Voice): marked as not part of GlucoCraft, the configuration lines as code,
    with the citation of Home Assistant's custom sentences."""
    html = _render({"unit": "mg/dL", "ranges": AGP})
    body = re.search(r'<section class="doc" id="accessibility">.*?</section>', html, re.DOTALL).group(0)
    assert "These lines are not part of GlucoCraft" in body
    assert "(Home Assistant, n.d.-e)" in re.sub(r"<[^>]+>", "", body)
    assert "<pre><code># Example: added by the user; GlucoCraft ships no voice intents.\nconversation:" in body
    assert 'HassGetState:\n      - "read [the] {name}"</code></pre>' in body


# ------------------------------------------------------------------------------------------------ robustness and reading
def test_a_wrong_reference_token_shows_as_text_and_the_page_still_renders():
    """A {cite:} or {ref:} token whose id names no reference (a typing error in a translation) is shown as its text;
    the section and every other section are drawn."""
    body = "<p>Before {cite:nosuchref} and {ref:alsonot|the named text} after.</p>"
    html = _render({"unit": "mg/dL"}, {"documentation": {"docs.section.essentials.body": body}})
    assert "Before {cite:nosuchref} and the named text after." in html
    assert 'id="references-body"' in html and 'id="disclaimer"' in html


def test_links_are_underlined_and_wide_tables_scroll_in_a_region_the_keyboard_reaches():
    html = _render({"unit": "mg/dL"})
    assert "text-decoration: underline;" in html and "text-decoration: none" not in html
    region = r'<div class="tw" tabindex="0" role="region" aria-labelledby="([^"]+)"><table aria-labelledby="\1">'
    regions = re.findall(region, html)
    assert regions and len(regions) == html.count("<table ")


def test_a_link_elsewhere_on_the_page_opens_its_section():
    """The footer's Disclaimer and About and License links change only the address: the page listens for it."""
    js = DOCS_JS
    assert 'window.addEventListener("location-changed", this._onHash);' in js
    assert 'window.addEventListener("hashchange", this._onHash);' in js
    assert "this._openHash = (h) => {" in js
