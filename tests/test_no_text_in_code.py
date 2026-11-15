# License: AGPL-3.0-only (LICENSE).
"""No user-visible text in the code: every text a person sees or hears comes from a language catalogue.

One check per kind of file, each with a small allow-list that says why an entry is not a text to translate:

- Python (custom_components/glucocraft/*.py): a string that reads as words (two words or more), or any string given
  as a title= or message= of a call. Not checked: docstrings and the messages of the log (_LOGGER.*), which are for
  developers; the canonical English constants of const.py (the disclaimer and its one line, the license notice, the
  cited trend category names that are attribute values, CANONICAL_CONSTANTS).
- JavaScript (custom_components/glucocraft/www/*.js): the text between HTML tags, the read-out attributes (aria-label,
  title, alt, ...), a string that reads as words, and a single word that starts with a capital letter (such as "AM"
  or "LO"; tests/js_literals.py reads the literals). Not checked: console messages (for developers) and the keys
  given to gcT().
- The panels (YAML labels and lambdas, C++ headers): tests/test_panel_configs.py, test_no_text_is_typed_in_a_label
  and test_no_text_is_typed_in_the_code, with their allow-list ALLOWED_LITERALS.

The pseudo-locale run (tests/test_pseudo_locale.py and the cards under localStorage.glucocraft_language = "pseudo")
covers what a static check cannot see, such as a lower-case word chosen at run time.
"""

from __future__ import annotations

import ast
import re

import pytest

from conftest import REPO
from js_literals import literals, visible_words

PKG = REPO / "custom_components" / "glucocraft"
WWW = PKG / "www"
WORDS = re.compile(r"[A-Za-z][a-z]+(?:['’][a-z]+)?")
LOG_CALLS = {"debug", "info", "warning", "error", "exception", "critical", "log"}
TEXT_KEYWORDS = {"title", "message"}

# const.py: assignments whose strings are canonical English in the backend (the translation contract): the disclaimer
# in its long and short forms and its one line (the cards and the panels show their catalogues' copies) and the license notice; the trend category
# names are the values of the trend attribute "description" (the cards and panels translate them through their keys,
# TREND_KEYS)
CANONICAL_CONSTANTS = {
    "DISCLAIMER_LEAD",
    "DISCLAIMER",
    "DISCLAIMER_SHORT",
    "DISCLAIMER_LINE",
    "LICENSE_NAME",
    "COPYRIGHT",
    "GNU_NOTICE",
    "TREND_LIBRE_BANDS",
}

# Python strings that read as words but are not texts to translate: (file, the string or its beginning) -> why
PY_ALLOWED = {
    ("config_flow.py", "GlucoCraft"): "the entry's title: the product name",
    ("http_api.py", 'attachment; filename="'): "an HTTP header",
    ("limits.py", "^(?:below|under|less than|lower than|<)"): "a pattern reading a CGM integration's own text",
    ("limits.py", "^(?:above|over|more than|greater than|higher than|>)"): "a pattern reading a CGM integration's text",
    ("notification.py", "Battelino et al., 2019"): "an author-date citation",
    ("panel_feed.py", "GlucoCraft panel feed"): "the webhook's name in Home Assistant's webhook registry",
    ("report_pdf.py", "American Diabetes Association Professional Practice Committee for Diabetes, 2026"): "citation",
    ("report_pdf.py", "GlucoCraft · "): "the product name before the canonical copyright line",
    ("report_pdf.py", r"(\d+(?:–\d+)?) mg/dL"): "a pattern of the unit pairs",
    ("report_pdf.py", r"\2 mmol/L (\1 mg/dL)"): "the unit pair reordered (unit symbols)",
    ("sensor.py", "Klonoff et al., 2023"): "an author-date citation",
    ("storage.py", "unreadable header ("): "the reason in a log message (_quarantine logs it)",
    ("storage.py", "damaged header, "): "the reason in a log message (_keep_copy logs it)",
    ("storage.py", " readings recovered"): "the reason in a log message (_keep_copy logs it)",
    ("storage.py", " records not readable, "): "the reason in a log message (_keep_copy logs it)",
    ("storage.py", "none of "): "the reason in a log message (_quarantine logs it)",
    ("storage.py", " records readable"): "the reason in a log message (_quarantine logs it)",
}
# report_pdf.REFERENCES: the APA 7 reference list, not translated (the translation contract); pdf_tags.py: the PDF
# operators of marked content, the XMP metadata packet (XML markup) and the messages of programming errors (for
# developers, like the log)
PY_ALLOWED_BLOCKS = {
    ("report_pdf.py", "REFERENCES"),
    ("pdf_tags.py", "_BDC_TAGGED"),
    ("pdf_tags.py", "_BDC_ARTIFACT"),
    ("pdf_tags.py", "_XMP_PACKET"),
    ("pdf_tags.py", "_MISUSE"),
}


def _parents(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node


def _skipped(node: ast.AST, file: str) -> bool:
    """True for a docstring, a log message, a canonical constant or an allowed block."""
    if isinstance(node.parent, ast.Expr):
        return True  # a docstring (or another bare string)
    q = node
    while hasattr(q, "parent"):
        q = q.parent
        if isinstance(q, ast.Call) and isinstance(q.func, ast.Attribute) and q.func.attr in LOG_CALLS:
            return True
        if isinstance(q, (ast.Assign, ast.AnnAssign)):
            targets = q.targets if isinstance(q, ast.Assign) else [q.target]
            names = {t.id for t in targets if isinstance(t, ast.Name)}
            if (file == "const.py" and names & CANONICAL_CONSTANTS) or any(
                (file, n) in PY_ALLOWED_BLOCKS for n in names
            ):
                return True
    return False


def _python_texts():
    out = []
    for path in sorted(PKG.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        _parents(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or _skipped(node, path.name):
                continue
            s = node.value
            parent = node.parent.parent if isinstance(node.parent, ast.JoinedStr) else node.parent
            sink = isinstance(parent, ast.keyword) and parent.arg in TEXT_KEYWORDS and WORDS.search(s)
            if sink or (len(WORDS.findall(s)) >= 2 and " " in s.strip()):
                out.append((path.name, node.lineno, s))
    return out


PY_TEXTS = _python_texts()


def _allowed(file: str, text: str, table: dict) -> bool:
    return any(f == file and text.startswith(start) for f, start in table)


def test_the_python_scan_finds_the_strings():
    assert len(PY_TEXTS) >= len(PY_ALLOWED) - 3  # the allowed strings are found (the scan works)


@pytest.mark.parametrize("file, line, text", PY_TEXTS, ids=[f"{f}:{n}" for f, n, _ in PY_TEXTS])
def test_no_user_visible_text_in_python(file, line, text):
    assert _allowed(file, text, PY_ALLOWED), f"{file}:{line}: a text in the code, not in a catalogue: {text!r}"


def test_every_python_allowance_is_still_needed():
    found = {(f, s) for f, _, s in PY_TEXTS}
    unused = [key for key in PY_ALLOWED if not any(f == key[0] and s.startswith(key[1]) for f, s in found)]
    assert not unused, unused


# ------------------------------------------------------------------------------------------------ JavaScript
# JavaScript literals that read as words but are not texts to translate: (file, the literal or its beginning) -> why
JS_ALLOWED = {
    ("*", "Roboto, 'Noto Sans', 'Segoe UI', sans-serif"): "a CSS font list",
    ("*", "rising quickly"): "a key of GC_SPOKEN_TREND_KEYS: the attribute value it translates",
    ("*", "changing slowly"): "a key of GC_SPOKEN_TREND_KEYS: the attribute value it translates",
    ("*", "falling quickly"): "a key of GC_SPOKEN_TREND_KEYS: the attribute value it translates",
    ("*", "\x00 mmol/mol"): "a unit symbol after a number",
    ("*", "\x00\x00 mmol/mol"): "a unit symbol after a number",
    ("*", "not GLU2 v3"): "the error of a damaged panel file, for the console",
    ("*", "American Diabetes Association Professional Practice Committee"): "an author-date citation",
    ("*", "Battelino et al., 20"): "an author-date citation",
    ("*", "GlucoCraft"): "the product name",
    ("*", "DMY"): "a date-format setting code",
    ("*", "MDY"): "a date-format setting code",
    ("*", "YMD"): "a date-format setting code",
    ("*", "Unknown"): "compared with the trend attribute's value",
    ("glucocraft-display-card.js", 'dominant-baseline="'): "SVG attributes",
    ("glucocraft-strategy.js", " | {{ '—' if up < 0"): "a Jinja template (code)",
    ("glucocraft-strategy.js", ".attributes if states."): "a Jinja template (code)",
    ("glucocraft-strategy.js", "[v.op] if v.op in ["): "a Jinja template (code)",
    ("glucocraft-docs.js", ' <a href="https://doi.org/'): "a DOI link (an address)",
    ("glucocraft-docs.js", ".refs li.hl"): "a CSS selector",
    ("glucocraft-docs.js", "code, pre, .refs, .sr"): "a CSS selector",
    ("glucocraft-strategy.js", " if (t | timestamp_custom("): "a Jinja template (code)",
}
# glucocraft-docs.js REFS: the APA 7 reference list and its citation labels, not translated (the translation contract)
JS_REFERENCE_LISTS = {"glucocraft-docs.js": "const REFS = {"}


def _js_texts():
    out = []
    for path in sorted(WWW.glob("*.js")):
        src = path.read_text(encoding="utf-8")
        refs = JS_REFERENCE_LISTS.get(path.name)
        refs_at = (src.index(refs), src.index("\n};", src.index(refs))) if refs else None
        for lit in literals(src):
            if re.search(r"console\.\w+\(\s*$", lit.before) or re.search(r"\bgcT\(\s*$", lit.before):
                continue
            if refs_at and refs_at[0] <= len("\n".join(src.splitlines()[: lit.line - 1])) <= refs_at[1]:
                continue
            words = visible_words(lit)
            single = re.fullmatch(r"[A-Z][A-Za-z]+|[A-Z]{2,}", lit.text)
            if words or single:
                out.append((path.name, lit.line, lit.text))
    return out


JS_TEXTS = _js_texts()


def _js_allowed(file: str, text: str) -> bool:
    return any(f in ("*", file) and (text == start or text.startswith(start)) for f, start in JS_ALLOWED)


@pytest.mark.parametrize("file, line, text", JS_TEXTS, ids=[f"{f}:{n}" for f, n, _ in JS_TEXTS])
def test_no_user_visible_text_in_javascript(file, line, text):
    assert _js_allowed(file, text), f"{file}:{line}: a text in the code, not in a catalogue: {text[:120]!r}"


def test_every_javascript_allowance_is_still_needed():
    unused = [key for key in JS_ALLOWED if not any(_js_allowed(f, t) and t.startswith(key[1]) for f, _, t in JS_TEXTS)]
    assert not unused, unused


@pytest.mark.parametrize(
    "source, shown",
    [
        ("const a = `<td>Time in range</td>`;", "Time in range"),
        ('const a = `<button aria-label="Open the page">${x}</button>`;', "Open the page"),
        ('const a = "Time in range";', "Time in range"),
        ('const a = gcT("strategy.page.today");', ""),
        ('console.warn("GlucoCraft: no text");', ""),
        ('const a = "display: none; color: red";', ""),
        ('const r = /(\\d+) min/g; const b = "x";', ""),
    ],
)
def test_the_javascript_scan(source, shown):
    lits = [lit for lit in literals(source) if not re.search(r"(console\.\w+|gcT)\(\s*$", lit.before)]
    assert " ".join(w for w in (visible_words(lit) for lit in lits) if w) == shown


# ------------------------------------------------------------------------------------------------ key states by name
# The states of these sensors are keys (not_expired, 6_hours, custom_ranges, ...): a page shows them by their name in
# the language in use (Jinja state_translated(), the frontend's hass.formatEntityState), never the key itself.
KEY_STATE_SENSORS = ("sensor_expiry", "data_history", "configuration")


@pytest.mark.parametrize("name", sorted(p.name for p in WWW.glob("*.js")))
def test_key_states_are_shown_by_their_name(name):
    src = (WWW / name).read_text(encoding="utf-8")
    for sensor in KEY_STATE_SENSORS:
        entity = f"sensor.glucocraft_{sensor}"
        assert not re.search(rf"\bstates\(\s*['\"]{entity}['\"]\s*\)", src), (name, entity)  # Jinja: the raw key
        assert not re.search(rf"states\[\s*['\"]{entity}['\"]\s*\]\??\.state\b", src), (name, entity)
    if name == "glucocraft-strategy.js":
        assert "{{ state_translated('sensor.glucocraft_sensor_expiry') }}" in src
    if name == "glucocraft-ui.js":
        assert 'word("sensor.glucocraft_data_history")' in src and "word(ENT.expiry)" in src
        assert "this._hass.formatEntityState(s)" in src
        for raw in ('g("sensor.glucocraft_data_history")', "g(ENT.expiry)", "g(ENT.config)"):
            assert raw not in src, raw
