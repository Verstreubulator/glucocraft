# License: AGPL-3.0-only (LICENSE).
"""The test language "pseudo" (tools/i18n/make_pseudo.py) and what it shows about the backend.

pseudo is English with every letter accented ("Glüçöšé"), each text about 35 % longer and between « and ». A text
in pseudo output that is plain English therefore did not come from a catalogue: it escaped.

- The generator: the same keys as English in every area (backend, report, frontend, documentation, the panels'
  catalogue); placeholders, plural keys, link tokens, Jinja, HTML tags, Markdown link targets and code kept exactly;
  every text with a letter accented, between the markers and longer; a text without a letter unchanged.
- pseudo is never used for a user: its files are not in the repository (.gitignore), and only the development
  override GLUCOCRAFT_LANGUAGE chooses it in the backend (the cards: localStorage.glucocraft_language; the panels:
  the substitution "language: pseudo", tests/test_panel_configs.py checks that every configuration builds "en").
- The backend under pseudo: every surface of tests/test_i18n_english_output.py (the notifications, the spoken
  summary, the panel notices, the CSV headers, the regional words, the panel feed's page texts, the report data and
  the PDF) is made without an error and without an escaped text. Allowed in plain English (ALLOWED, CANONICAL): the
  canonical English disclaimer and license notice (const.py, not translated in the backend), the reference
  list and author-date citations, proper names, unit symbols, entity ids, file names and the test's own data (entity
  names, source names, a printer's error text).
- The PDF under pseudo draws the markers and the accented letters with its fonts (report_pdf.FONTS).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re

import pytest
from glucocraft import const as C
from glucocraft import i18n

import test_i18n_english_output as out
from conftest import REPO, panel_folder
from test_i18n_english_output import fixed_clock  # noqa: F401 - the fixture

PKG = REPO / "custom_components" / "glucocraft"
TOOL = REPO / "tools" / "i18n" / "make_pseudo.py"
_spec = importlib.util.spec_from_file_location("make_pseudo", TOOL)
MP = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MP)

AREAS = sorted(p.stem for p in (PKG / "i18n" / "en").glob("*.json"))
EN = {area: json.loads((PKG / "i18n" / "en" / f"{area}.json").read_text(encoding="utf-8")) for area in AREAS}
PSEUDO = {area: MP.pseudo_catalogue(cat) for area, cat in EN.items()}

PLACEHOLDER = re.compile(r"\{[a-z_][a-z0-9_]*\}")
TOKEN = re.compile(r"\{(?:cite|ref|ref_year|sec):[^{}]*\}|\{\{.*?\}\}|\{%.*?%\}")
TAG = re.compile(r"</?[A-Za-z][\w-]*(?:\s[^<>]*)?/?>")
LINK_TARGET = re.compile(r"\]\([^()\s]*\)")
CODE = re.compile(r"<(code|pre)\b[^>]*>.*?</\1>|`[^`\n]*`", re.DOTALL)


def _texts(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [t for v in value for t in _texts(v)]
    if isinstance(value, dict):
        return [t for v in value.values() for t in _texts(v)]
    return []


def _pairs():
    for area in AREAS:
        for key, value in EN[area].items():
            for en, ps in zip(_texts(value), _texts(PSEUDO[area][key]), strict=True):
                yield f"{area}:{key}", en, ps
    panels = panel_folder()
    if panels is not None and (panels / "i18n" / "en.yaml").is_file():
        yaml = pytest.importorskip("yaml")
        source = yaml.safe_load((panels / "i18n" / "en.yaml").read_text(encoding="utf-8"))
        for key, en in source.items():
            yield f"panels:{key}", en, MP.pseudo_text(en)


PAIRS = list(_pairs())


# ------------------------------------------------------------------------------------------------ the generator
def test_the_same_keys_in_every_area():
    for area in AREAS:
        assert list(PSEUDO[area]) == list(EN[area]), area
    assert any(k.startswith("panels:") for k, _, _ in PAIRS) or panel_folder() is None


@pytest.mark.parametrize("where, en, ps", PAIRS, ids=[p[0] for p in PAIRS])
def test_markup_is_kept_and_every_word_is_marked(where, en, ps):
    for rx in (PLACEHOLDER, TOKEN, TAG, LINK_TARGET, CODE):
        assert [m.group(0) for m in rx.finditer(ps)] == [m.group(0) for m in rx.finditer(en)], (where, rx.pattern)
    words = MP.KEEP.sub("", en)
    if not re.search(r"[^\W\d_]", words):
        assert ps == en, where  # nothing to translate: a separator, a lone placeholder
        return
    assert ps.count(MP.OPEN) == ps.count(MP.CLOSE) >= 1, where
    assert not re.search(r"[aceinosuyzACEINOSUYZ]", MP.KEEP.sub("", ps)), where  # every one of these letters accented
    if len(words) >= 20:  # longer (a table row's pipes and spaces do not grow, so 15 % at the least)
        assert len(MP.KEEP.sub("", ps)) >= 1.15 * len(words), where


def test_examples():
    assert MP.pseudo_text("Glucose") == "«Glüçöšé»"
    assert MP.pseudo_text("{count} panels") == "«{count} páñélš»"
    assert MP.pseudo_text("; ") == "; " and MP.pseudo_text("{weekday}\n{date}") == "{weekday}\n{date}"
    assert MP.pseudo_text("| Source | In use |\n|---|---|\n") == "| «Šööürçé» | «ÍÍñ üšé» |\n|---|---|\n"
    assert MP.pseudo_text("- **Layout:** the core") == "- «**Lááýýööüt:** théé çööré»"
    assert MP.pseudo_text("Press KEY_A") == "«Préšš KEY_A»"


# ------------------------------------------------------------------------------------------------ never for a user
def test_the_generated_files_are_never_committed():
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "custom_components/glucocraft/i18n/pseudo/" in ignored.splitlines()
    panels = panel_folder()
    if panels is not None and (panels / ".gitignore").is_file():
        lines = (panels / ".gitignore").read_text(encoding="utf-8").splitlines()
        assert "/i18n/pseudo.yaml" in lines and "/i18n/generated/pseudo/" in lines


def test_the_out_folder_writes_nothing_into_the_repository_or_the_panels(tmp_path):
    """--out writes every catalogue into the given folder alone (a fit check of the pseudo texts that changes neither
    the repository nor the panels' folder)."""
    panels = panel_folder()
    pseudo_dirs = [REPO / "custom_components" / "glucocraft" / "i18n" / "pseudo"]
    if panels is not None:
        pseudo_dirs += [panels / "i18n" / "pseudo.yaml", panels / "i18n" / "generated" / "pseudo"]
    before = [p.exists() for p in pseudo_dirs]
    args = ["--out", str(tmp_path)] + (["--panels", str(panels)] if panels is not None else ["--no-panels"])
    assert MP.main(args) == 0
    assert [p.exists() for p in pseudo_dirs] == before
    assert {p.name for p in tmp_path.glob("*.json")} == {p.name for p in (MP.I18N / "en").glob("*.json")}
    assert (tmp_path / "panels" / "pseudo.yaml").is_file() == (panels is not None)


def test_only_the_development_override_chooses_pseudo(monkeypatch):
    hass = type("H", (), {"config": type("Cfg", (), {"language": "de"})()})()
    monkeypatch.delenv(i18n.OVERRIDE_ENV, raising=False)
    assert i18n.configured_language(hass) == "de"
    monkeypatch.setenv(i18n.OVERRIDE_ENV, "pseudo")
    assert i18n.configured_language(hass) == "pseudo"
    module = (PKG / "www" / "glucocraft-i18n.js").read_text(encoding="utf-8")
    assert 'localStorage?.getItem("glucocraft_language")' in module


# ------------------------------------------------------------------------------------------------ the backend
@pytest.fixture
def pseudo(tmp_path, monkeypatch):
    """The backend's catalogues in English and pseudo, pseudo loaded."""
    for lang, cats in (("en", EN), ("pseudo", PSEUDO)):
        (tmp_path / lang).mkdir()
        for area in i18n.AREAS:
            (tmp_path / lang / f"{area}.json").write_text(json.dumps(cats[area], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(i18n, "I18N_DIR", tmp_path)
    assert i18n.load("pseudo") == "pseudo"
    yield
    monkeypatch.undo()
    i18n.load("en")


def _canonical() -> list[str]:
    """The texts that are English in every language: the disclaimer, the license notice, the references."""
    from glucocraft import report_pdf as P

    texts = [
        C.DISCLAIMER,
        C.COPYRIGHT,
        C.LICENSE_NAME,
        *_texts(list(C.GNU_NOTICE)),
    ]
    texts += _texts([list(ref) for ref in P.REFERENCES])
    return [_norm(t) for t in texts]


def _phrases() -> list[str]:
    """The short canonical texts that catalogue texts quote (the PDF footer's disclaimer line, the About item's lead,
    the license)."""
    return [_norm(t) for t in (C.DISCLAIMER_LINE, C.DISCLAIMER_LEAD, C.COPYRIGHT, C.LICENSE_NAME)]


def _norm(text: str) -> str:
    """A text without markup (tags, Markdown emphasis) and with single spaces, for the comparison with CANONICAL."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>|\*\*|\*|(?<!\w)_|_(?!\w)", "", text)).strip()


# What may be plain English in pseudo output, with the reason. Not translated (the translation contract): web
# addresses, file names, entity ids and other identifiers (the license's SPDX identifier AGPL-3.0-only), Markdown
# code spans (an address template), unit symbols, author-date citations and the error class of an exception. Proper
# names stay as they are. TEST_DATA: the test's own input (entity and device names, notify services, a printer's HTTP
# reason), which in use comes from Home Assistant.
NOT_TEXT_PARTS = (
    r"`[^`]*`",
    r"https?://\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:org|com|net|io|html|pdf|csv|local)\b\S*",
    r"\b[a-z_]+\.[a-z0-9_]+\b|\b[a-z0-9]+(?:_[a-z0-9]+)+\b",
    r"\bmg/dL(?:/min)?|\bmmol/(?:L|mol)(?:/min)?",
    # an author-date citation: "Battelino et al., 2019", "Danne et al. (2017)", a group author ("American
    # Diabetes Association Professional Practice Committee for Diabetes, 2026a")
    (
        r"\b(?:[A-Z][A-Za-z'-]+\s(?:(?:for|of|and|&)\s)?)*[A-Z][A-Za-z'-]+(?:\s(?:et\sal\.|and|&)\s[A-Z][A-Za-z'-]+)*"
        r"(?:\set\sal\.)?,?\s\(?(?:19|20)\d\d[a-z]?\)?"
    ),
    r"\b[A-Z][A-Za-z]+Error\b",
    r"\bAGPL-3\.0-only\b",
)
NOT_TEXT = re.compile("|".join(NOT_TEXT_PARTS))


def _authors() -> re.Pattern:
    """The authors' names of the reference list (a citation can break over two lines of the PDF): a run of them,
    with the "for", "of" and "and" of a group author ("Professional Practice Committee for Diabetes")."""
    from glucocraft import report_pdf as P

    names = {
        w
        for ref in P.REFERENCES
        for w in re.findall(r"\b[A-Z][A-Za-z'-]+", re.split(r"\((?:19|20)\d\d", _texts(list(ref))[1])[0])
    }
    alternatives = "|".join(sorted(names, key=len, reverse=True))
    return re.compile(rf"(?:\b(?:{alternatives})\b(?:\s(?:for|of|and)\b)?\s*)+")


AUTHORS = _authors()
PROPER_NAMES = {"GlucoCraft", "Home", "Assistant", "AGPL", "GNU", "Affero"}
TEST_DATA = {
    "Panel",
    "Hall",
    "Test",
    "source",
    "old",
    "phone",
    "Upgrade",
    "Required",
    "Connection",
    "refused",
}


def escaped(text: str, canonical: list[str]) -> list[str]:
    """Return the plain-English words of an output text that came from no catalogue: a word of ASCII letters with an
    unaccented vowel (pseudo accents every vowel), outside NOT_TEXT, CANONICAL and the allowed names."""
    found = []
    for line in text.splitlines():
        norm = _norm(line)
        if not norm or any(norm in c for c in canonical):
            continue
        for phrase in _phrases():
            norm = norm.replace(phrase, " ").replace(phrase.rstrip("."), " ")
        for word in re.findall(r"[^\W\d_]+(?:['’][^\W\d_]+)*", AUTHORS.sub(" ", NOT_TEXT.sub(" ", norm))):
            plain = word.isascii() and len(word) > 1 and re.search(r"[aeiouAEIOU]", word)
            if plain and word not in PROPER_NAMES and word not in TEST_DATA:
                found.append(f"{word!r} in {line.strip()[:120]!r}")
    return found


def _surfaces(monkeypatch, make_coordinator, fake_hass) -> dict[str, str]:
    """Every backend output of tests/test_i18n_english_output.py, with its test input made in the language in use."""
    from glucocraft import spoken as sp
    from glucocraft.regional import Regional

    when = sp.reading_when(out.NOW_LOCAL.replace(hour=7, minute=46, tzinfo=None), out.NOW_LOCAL.date(), Regional())
    summary = sp.spoken_summary
    monkeypatch.setattr(
        sp, "spoken_summary", lambda **kw: summary(**{**kw, "reading_when": when if kw["reading_when"] else None})
    )
    got = {}
    got.update(out._summaries(make_coordinator, fake_hass))
    got.update(out._spoken())
    # the CSV files: the header line (the data rows hold codes: primary / secondary, low / high)
    got.update({k: v.split("\n", 1)[0] if k.startswith("csv|") else v for k, v in out._small(make_coordinator).items()})
    got.update(out._panel_notices(monkeypatch))
    got.update({k: v for k, v in out._phone_test(monkeypatch).items() if k != "plain_text"})  # plain_text: test input
    got.update({k: ast.literal_eval(v)[1] for k, v in out._ipp_messages(monkeypatch).items()})  # (ok, message)
    return got


@pytest.mark.usefixtures("fixed_clock")
def test_every_backend_surface_under_pseudo(pseudo, monkeypatch, make_coordinator, fake_hass):
    canonical = _canonical()
    got = _surfaces(monkeypatch, make_coordinator, fake_hass)
    assert len(got) > 500
    problems = [f"{k}: {w}" for k, v in got.items() for w in escaped(v, canonical)]
    assert not problems, "\n".join(dict.fromkeys(problems))
    assert all(MP.OPEN in v for k, v in got.items() if k.startswith(("daily|", "weekly|", "monthly|")))


def _pseudo_report_cases():
    """The report cases of tests/test_i18n_english_output.py, the period's name from the catalogue in use."""
    from glucocraft.report_service import period_label

    for name, data, full, color in ORIGINAL_CASES():
        data["label"] = period_label(int(name.split()[0]))
        yield name, data, full, color


ORIGINAL_CASES = out._report_cases


@pytest.mark.slow
@pytest.mark.usefixtures("fixed_clock")
def test_the_report_and_its_pdf_under_pseudo(pseudo, monkeypatch):
    """The report data and every text the PDF draws come from the catalogue; the PDF's fonts draw the markers and
    the accented letters (the text read back from the PDF holds them)."""
    monkeypatch.setattr(out, "_report_cases", _pseudo_report_cases)
    got = out._report(monkeypatch)
    canonical = _canonical()
    problems = []
    for k, v in got.items():
        kind = k.split("|", 1)[0]
        if kind == "report_data":  # "path=value": the texts (codes such as "MDY" or "low" are single words)
            v = "\n".join(value for line in v.splitlines() if " " in (value := line.split("=", 1)[1]))
        elif kind == "report_drawn":  # "page x y size align text": the text
            v = "\n".join(line.split(" ", 5)[5] for line in v.splitlines())
        elif kind != "report_meta":
            continue  # report_pdf_text: the same texts as drawn, read back (checked below)
        problems += [f"{k}: {w}" for w in escaped(v, canonical)]
    assert not problems, "\n".join(dict.fromkeys(problems))
    for k, v in got.items():
        if k.startswith("report_pdf_text|"):
            assert MP.OPEN in v and MP.CLOSE in v and "é" in v and "š" in v, k
