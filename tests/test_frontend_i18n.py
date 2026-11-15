# License: AGPL-3.0-only (LICENSE).
"""The translation capability of the cards and pages (custom_components/glucocraft/www, i18n/<language>/).

- The catalogues: flat dotted keys, string (or list of strings) values, plural forms as <key>.one / <key>.other; every
  language other than English has only keys English has, with the same placeholders.
- The code: every catalogue key written in a card file exists in English, and every key of frontend.json is used;
  every literal call gcT("key", { ... }) passes exactly the placeholders of the English text (count / digits for the
  plural form); the language bootstrap is one copy in every card file.
- The module glucocraft-i18n.js: the English fallback per key, the lookup order (exact code, base language, English),
  plural forms by Intl.PluralRules, unknown placeholders left as written, a missing key shown as the key.
- The Documentation: one title and one body per section, every citation token naming a reference and every section link
  a section, and the rendered page without an unfilled placeholder.
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest
from glucocraft.units import REPORT_THRESHOLDS

from js_i18n import I18N, WWW, catalogue, i18n_prelude
from node_runner import NODE, needs_node, node_json

CARD_FILES = ["display-card", "ui", "report", "docs", "strategy"]
# the files that write catalogue keys: the card files and their shared module (glucocraft-shared.js)
KEY_FILES = [*CARD_FILES, "shared"]
JS = {n: (WWW / f"glucocraft-{n}.js").read_text(encoding="utf-8") for n in KEY_FILES}
AREAS = ("frontend", "report", "documentation")
EN = {area: catalogue(area) for area in AREAS}
ALL_EN = {k: v for area in AREAS for k, v in EN[area].items()}
KEY = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
PLURAL = ("zero", "one", "two", "few", "many", "other")
# the key prefixes of the frontend's catalogues (frontend.json: card, ui, report_page, strategy, spoken, disclaimer;
# documentation.json: docs; report.json: report)
PREFIX = re.compile(r'"((?:card|ui|report_page|strategy|spoken|disclaimer|docs|report)\.[a-z0-9_.]+)"')


def _text(value) -> str:
    return "".join(value) if isinstance(value, list) else value


def _placeholders(key: str) -> set[str]:
    """The placeholders of a key's English text, of all its plural forms together."""
    if key in ALL_EN:
        return set(PLACEHOLDER.findall(_text(ALL_EN[key])))
    return {
        p for form in PLURAL if f"{key}.{form}" in ALL_EN for p in PLACEHOLDER.findall(_text(ALL_EN[f"{key}.{form}"]))
    }


def _exists(key: str) -> bool:
    return key in ALL_EN or f"{key}.other" in ALL_EN


def _node(src: str) -> object:
    return node_json(src, prelude=False)


# ------------------------------------------------------------------------------------------------ the catalogues
@pytest.mark.parametrize("area", AREAS)
def test_catalogue_keys_and_values(area):
    cat = EN[area]
    if area == "report" and not cat:
        pytest.skip("report.json not written yet")
    assert cat, area
    for key, value in cat.items():
        assert KEY.match(key), key
        assert isinstance(value, str) or (isinstance(value, list) and all(isinstance(v, str) for v in value)), key
        if key.rsplit(".", 1)[-1] in PLURAL:
            base = key.rsplit(".", 1)[0]
            assert f"{base}.other" in cat, f"{key}: a plural form without .other"
            assert base not in cat, f"{base}: a text and plural forms"


def test_every_language_has_the_english_keys_and_placeholders():
    for folder in sorted(p for p in I18N.iterdir() if p.is_dir() and p.name != "en"):
        for area in AREAS:
            cat = catalogue(area, folder.name)
            for key, value in cat.items():
                base = key.rsplit(".", 1)[0] if key.rsplit(".", 1)[-1] in PLURAL else key
                assert key in EN[area] or f"{base}.other" in EN[area], (folder.name, key)
                assert set(PLACEHOLDER.findall(_text(value))) <= _placeholders(base) | {"count"}, (folder.name, key)
                # a placeholder of the English text is kept in the translation (a plural form may drop {count})
                kept = _placeholders(base) - {"count"}
                assert kept <= set(PLACEHOLDER.findall(_text(value))), (folder.name, key)


# ------------------------------------------------------------------------------------------------ the code
@pytest.mark.parametrize("name", [*KEY_FILES, "i18n"])
def test_every_card_file_is_valid_javascript(name):
    if not NODE:
        pytest.skip("node not available")
    path = WWW / f"glucocraft-{name}.js"
    res = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, check=False, timeout=60)
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize("name", KEY_FILES)
def test_every_key_written_in_the_code_exists_in_english(name):
    missing = sorted({k for k in PREFIX.findall(JS[name]) if not _exists(k)})
    assert not missing, missing


def test_every_frontend_key_is_used():
    """Every key of frontend.json is read by a card file or the Python side: named literally, or built from a prefix
    the code writes (`a.b.${x}`, "a.b." + x, `a.${x}.c`). A text that nothing shows would still be translated."""
    python = [p.read_text(encoding="utf-8") for p in sorted(WWW.parent.glob("*.py"))]
    code = "\n".join([*JS.values(), *python])
    quoted = set(re.findall(r"[\"'`]([a-z0-9_]+(?:\.[a-z0-9_]+)+)[\"'`]", code))
    prefixes = set(re.findall(r"`([a-z0-9_]+(?:\.[a-z0-9_]+)*\.)\$\{", code))
    prefixes |= set(re.findall(r"[\"']([a-z0-9_]+(?:\.[a-z0-9_]+)*\.)[\"']\s*\+", code))
    inner = [
        re.compile("^" + re.escape(a) + r"[a-z0-9_]+" + re.escape(b) + "$")
        for a, b in re.findall(r"`([a-z0-9_]+(?:\.[a-z0-9_]+)*\.)\$\{[^}]+\}((?:\.[a-z0-9_]+)+)`", code)
    ]
    unused = []
    for key in EN["frontend"]:
        base = re.sub(r"\.(zero|one|two|few|many|other)$", "", key)
        if base in quoted or key in quoted or any(base.startswith(p) for p in prefixes):
            continue
        if not any(rx.match(base) for rx in inner):
            unused.append(key)
    assert not unused, unused


@pytest.mark.parametrize("name", KEY_FILES)
def test_literal_calls_pass_exactly_the_placeholders(name):
    bad = []
    for m in re.finditer(r'gcT\("([a-z0-9_.]+)",\s*\{([^{}]*)\}\)', JS[name]):
        key, body = m.group(1), m.group(2)
        if not _exists(key) or "..." in body:
            continue  # a missing key is reported by the test above; a spread object cannot be read here
        # the property names at the top level: string literals and the arguments of calls removed first
        body = re.sub(r'"(?:[^"\\]|\\.)*"|`[^`]*`|\'(?:[^\'\\]|\\.)*\'', '""', body)
        while re.search(r"\([^()]*\)", body):
            body = re.sub(r"\([^()]*\)", "", body)
        names = set(re.findall(r"(?:^|,)\s*([A-Za-z_]\w*)\s*(?=:|,|$)", body.strip()))
        wanted = _placeholders(key)
        # the thresholds of a report text (and its {unit} beside them) are filled by rL from the report data
        thresholds = wanted & set(REPORT_THRESHOLDS)
        if (
            not wanted - thresholds - ({"unit"} if thresholds else set()) <= names
            or not (names - {"count", "digits"}) <= wanted
        ):
            bad.append((key, sorted(wanted), sorted(names)))
    assert not bad, bad


def test_one_language_bootstrap_in_every_card_file():
    lines = (
        "const GC_I18N = globalThis.glucocraftI18n ? Promise.resolve() : "
        + 'import(new URL("./glucocraft-i18n.js", import.meta.url).href);',
        "const gcI18nLoad = (hass) => GC_I18N.then(() => globalThis.glucocraftI18n.load(hass, GC_I18N_AREAS));",
        "const gcI18nReady = (hass) => !!globalThis.glucocraftI18n?.ready(hass, GC_I18N_AREAS);",
        "const gcT = (key, params) => globalThis.glucocraftI18n.t(key, params);",
    )
    areas = {
        "display-card": '["frontend"]',
        "ui": '["frontend"]',
        "strategy": '["frontend"]',
        "report": '["frontend", "report"]',
        "docs": '["frontend", "documentation"]',
    }
    for name in CARD_FILES:
        js = JS[name]
        for line in lines:
            assert js.count(line) == 1, (name, line)
        assert f"const GC_I18N_AREAS = {areas[name]};" in js, name
        # the language in use (htmlLang), never a fixed one
        fixed = re.findall(r'(?<![\w-])lang="en"', js)
        assert not fixed, name


# ------------------------------------------------------------------------------------------------ the module
MODULE_TEST = """
const i = globalThis.glucocraftI18n;
i.add("en", "frontend", { "a.hello": "Hello {name}", "a.only_en": "English", "a.n.one": "{count} reading",
  "a.n.other": "{count} readings", "a.list": ["one ", "text"], "a.jinja": "{{ x }} {kept}" });
i.add("pt", "frontend", { "a.hello": "Olá {name}" });
i.add("pt-BR", "frontend", { "a.only_en": "Inglês (BR)" });
const out = [];
out.push(i.t("a.hello", { name: "Ana" }), i.t("a.only_en"), i.t("a.n", { count: 1, digits: 1 }), i.t("a.list"),
  i.t("a.jinja", { other: 1 }), i.t("a.missing"), i.language, i.htmlLang);
i.add("en", "frontend", { "a.hello": "Hello {name}", "a.only_en": "English", "a.n.one": "{count} reading",
  "a.n.other": "{count} readings" });
out.push(i.t("a.hello", { name: "Ana" }), i.t("a.n", { count: 1 }), i.t("a.n", { count: 1, digits: 1 }),
  i.t("a.n", { count: 2 }), i.ready({ language: "en" }), i.ready({ language: "de" }),
  i.ready({ locale: { language: "en" }, language: "de" }));
process.stdout.write("\\u0001" + JSON.stringify(out));
"""


@needs_node
def test_module_fallback_lookup_order_and_plurals():
    out = _node(i18n_prelude(areas=()) + MODULE_TEST)
    assert out == [
        "Olá Ana",  # pt-BR has no text: the base language pt
        "Inglês (BR)",  # the exact code first
        "1 reading",  # Portuguese plural rules: "1.0" is singular (the English text: no Portuguese one)
        "one text",  # a list value, joined
        "{{ x }} {kept}",  # braces without a parameter stay as written
        "a.missing",  # a missing key is visible
        "pt-BR",
        "pt-BR",
        "Hello Ana",
        "1 reading",
        "1 readings",  # English plural rules: "1.0" is not singular
        "2 readings",
        True,
        False,
        True,  # the frontend's language (hass.locale.language) before hass.language
    ]


# ------------------------------------------------------------------------------------------------ the Documentation
def _doc_sections() -> list[str]:
    block = JS["docs"][JS["docs"].index("const SECTIONS = [") :]
    return re.findall(r'^  \["([a-z-]+)", "mdi:[a-z-]+"\],$', block[: block.index("\n];")], re.MULTILINE)


def test_documentation_sections():
    doc = EN["documentation"]
    ids = _doc_sections()
    assert len(ids) == 24 and ids[:2] == ["disclaimer", "essentials"] and ids[-1] == "about"
    assert "terms" not in ids and "read-first" not in ids
    for sid in ids:
        key = sid.replace("-", "_")
        assert doc[f"docs.section.{key}.title"] and doc[f"docs.section.{key}.body"], sid
    assert {k.split(".")[2] for k in doc if k.startswith("docs.section.")} == {s.replace("-", "_") for s in ids}
    assert not [k for k in doc if k.startswith("docs.terms.")]
    shared = re.search(r"const DOC_TEXTS = \[([^\]]*)\]", JS["docs"]).group(1)
    assert {k[len("docs.text.") :] for k in doc if k.startswith("docs.text.")} == set(re.findall(r'"(\w+)"', shared))


def test_documentation_tokens_name_a_reference_or_a_section():
    refs = set(re.findall(r"^  (\w+): \[", JS["docs"][JS["docs"].index("const REFS = {") :], re.MULTILINE))
    inner = re.search(r"const ANCHORS = \{[^}]*\}", JS["docs"]).group(0)  # anchors inside a section
    anchors = set(_doc_sections()) | set(re.findall(r'"?([a-z-]+)"?: "[a-z-]+"', inner))
    for key, value in EN["documentation"].items():
        for kind, ids, _ in re.findall(r"\{(cite|ref|ref_year|sec):([^{}|]+)(?:\|([^{}]*))?\}", _text(value)):
            if kind == "sec":
                assert ids in anchors, (key, ids)
            else:
                assert set(ids.split(",")) <= refs, (key, ids)


DOCS_RENDER = """
globalThis.window = globalThis; globalThis.location = { hash: "" };
const reg = {}; globalThis.customElements = { get: (n) => reg[n], define: (n, c) => { reg[n] = c; } };
globalThis.HTMLElement = class { attachShadow() { this.shadowRoot = { innerHTML: "", querySelectorAll: () => [],
  getElementById: () => ({}), querySelector: () => null }; return this.shadowRoot; } };
globalThis.document = { createTreeWalker: () => ({ nextNode: () => null }) }; globalThis.NodeFilter = { SHOW_TEXT: 4 };
"""


@needs_node
@pytest.mark.parametrize(
    "attrs",
    [
        {},
        {"unit": "mmol/L", "secondary_source": {"name": "Second"}, "integration_version": "0.0.5",
         "ranges": {"low": 65, "tight_low": 75}, "reading_interval_seconds": 60,
         "sources": {"primary": {"lower_limit": 40, "lower_limit_setting": "auto", "upper_limit": 400}}},
    ],
)  # fmt: skip
def test_the_rendered_documentation_has_no_unfilled_placeholder(attrs):
    states = {"sensor.glucocraft_configuration": {"state": "ok", "attributes": attrs}}
    html = _node(
        i18n_prelude(areas=("frontend", "documentation"))
        + DOCS_RENDER
        + JS["docs"]
        + "\nconst el = new (customElements.get('glucocraft-documentation'))(); el.setConfig({});"
        + f"\nel.hass = {{ states: {json.dumps(states)}, language: 'en' }};"
        + "\nprocess.stdout.write('\\u0001' + JSON.stringify(el.shadowRoot.innerHTML));"
    )
    left = set(re.findall(r"\{[a-z_0-9]+(?::[^{}]*)?\}", html))
    assert left == {"{name}"}, left  # {name}: the slot of the voice example, shown as written
    # no key (docs.section...) shown in place of a text; a file name such as glucocraft-docs.js is not one
    assert not re.search(r"(?<![-\w])docs\.[a-z_]", re.sub(r"<[^>]+>", "", html))


def test_the_language_module_version_is_listed():
    src = (WWW / "glucocraft-i18n.js").read_text(encoding="utf-8")
    version = re.search(r'const GCI18N_VERSION = "(\d+\.\d+\.\d+)";', src).group(1)
    readme = (WWW.parents[2] / "README.md").read_text(encoding="utf-8")
    assert f"| Language module | {version} | `www/glucocraft-i18n.js` |" in readme
