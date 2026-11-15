# License: AGPL-3.0-only (LICENSE).
"""APA 7 citations in the published Markdown documents of docs/ (LANGUAGE_STYLE.md rule 3): a document that cites works
either has its own reference list, in which every author-date citation of the document has an entry, or carries the
line "Full references: Documentation > References", and then every citation it makes is an entry of the
Documentation's References (glucocraft-docs.js REFS). A document with citations and neither fails.

A citation is found in its APA forms: "Battelino et al., 2019" / "Battelino et al. (2019)", "Vigersky and McMahon
(2019)" / "Vigersky & McMahon, 2019", and a group or single author named by an entry ("Abbott Diabetes Care, 2018",
"Mulley (2026)"); it is matched by its first author and its year (with the letter, 2026a, n.d.-b). The reference lists
are the sections headed References, Reference list or Sources consulted; each entry is a paragraph "Authors. (Year)."."""

from __future__ import annotations

import html
import re

import pytest

from conftest import REPO
from test_published_texts import PUBLISHED_DOCS
from test_references import REFS

FULL_REFERENCES = "Full references: Documentation > References"
DOCS = [name for name in PUBLISHED_DOCS if name.startswith("docs/") and (REPO / name).is_file()]

YEAR = r"(?:19|20)\d\d[a-z]?|n\.d\.(?:-[a-z])?"
END = r"(?!\d)(?!\.\d)"  # not "2026.11" (a version) or "20261"
LIST_HEADING = re.compile(r"^(#{2,4}) [^\n]*(?:References|Reference list|Sources consulted)[^\n]*$", re.MULTILINE)
ENTRY = re.compile(rf"^(?:- )?(?P<authors>.+?) \((?P<year>{YEAR})(?:, [A-Z][a-z]+(?: \d{{1,2}})?)?\)\. ")
PERSON = re.compile(r"^([^,]+), (?:[A-Z]\.(?:-[A-Z]\.)?)(?: [A-Z]\.(?:-[A-Z]\.)?)*(?:,|$| &)")
ET_AL = re.compile(rf"\b(?P<a>[A-Z][\w'’-]+) et al\.,? \(?(?P<y>{YEAR}){END}")
TWO = re.compile(rf"\b(?P<a>[A-Z][\w'’-]+) (?:&|and) [A-Z][\w'’-]+,? \(?(?P<y>{YEAR}){END}")


def _plain(text: str) -> str:
    text = html.unescape(re.sub(r"</?i>", "", text))
    return re.sub(r"\s+", " ", text.replace("*", ""))


def _first(authors: str) -> str:
    """The first author of an entry: a person's surname, or the whole group author."""
    m = PERSON.match(authors)
    if m:
        return m.group(1)
    # the period that ends the author element, except where it is the period of an abbreviation ("Dexcom, Inc.")
    return authors if re.search(r"\b(?:Inc|Ltd|Co|Corp)\.$", authors) else authors.removesuffix(".")


def _entry_key(entry: str) -> tuple[str, str] | None:
    m = ENTRY.match(_plain(entry).strip())
    return (_first(m.group("authors")), m.group("year")) if m else None


def _split(text: str) -> tuple[str, list[str]]:
    """The document without its reference lists, and the paragraphs of those lists."""
    body, entries, pos = [], [], 0
    for m in LIST_HEADING.finditer(text):
        if m.start() < pos:
            continue
        level = len(m.group(1))
        nxt = re.compile(rf"^#{{1,{level}}} ", re.MULTILINE).search(text, m.end())
        end = nxt.start() if nxt else len(text)
        body.append(text[pos : m.start()])
        entries += [p for p in re.split(r"\n\s*\n", text[m.end() : end]) if p.strip()]
        pos = end
    body.append(text[pos:])
    return "".join(body), entries


REFS_KEYS = {_entry_key(text) for _label, _full, text in REFS.values()}
assert None not in REFS_KEYS


def _citations(text: str, known: set[str]) -> set[tuple[str, str]]:
    text = _plain(text)
    found = {(m.group("a"), m.group("y")) for rx in (ET_AL, TWO) for m in rx.finditer(text)}
    for name in sorted(known, key=len, reverse=True):
        for m in re.finditer(rf"(?<![\w-]){re.escape(name)},? \(?({YEAR}){END}", text):
            found.add((name, m.group(1)))
    return found


def _doc(name: str) -> tuple[set, set, bool]:
    """A document's citations, its own entries' keys, and whether it carries the Full references line."""
    text = (REPO / name).read_text(encoding="utf-8")
    body, entries = _split(text)
    own = {k for k in map(_entry_key, entries) if k}
    known = {a for a, _ in own | REFS_KEYS}
    return _citations(body, known), own, FULL_REFERENCES in " ".join(text.split())


@pytest.mark.parametrize("name", DOCS)
def test_every_citation_of_a_published_document_has_an_entry(name):
    cited, own, full = _doc(name)
    allowed = own | (REFS_KEYS if full else set())
    if cited:
        assert own or full, (name, "cites works but has no reference list and no Full references line")
    assert cited <= allowed, (name, sorted(cited - allowed))


def test_the_documents_without_a_list_point_to_the_documentation():
    for name in ("docs/ENTITIES_AND_API.md", "docs/DISPLAY_CONTRACT.md", "docs/DEVELOPER_GUIDE.md"):
        cited, _own, full = _doc(name)
        assert cited and full, name


@pytest.mark.parametrize(
    "probe",
    (
        "as Battelino et al. (2018) state",  # a wrong year
        "(Smith et al., 2020)",  # no entry
        "Vigersky and McMahon (2019)",  # not in the list
        "(Abbott Diabetes Care, 2017)",  # a known group author, a wrong year
    ),
)
def test_the_check_catches_a_citation_without_an_entry(probe):
    known = {a for a, _ in REFS_KEYS}
    cited = _citations(probe, known)
    assert cited and not cited <= {("Battelino", "2019"), ("Abbott Diabetes Care", "2018")}
