# License: AGPL-3.0-only (LICENSE).
"""APA 7 citations and references: every in-text citation has a reference entry and every entry is cited, in the Documentation, the PDF
and the README; the PDF and README entries are the Documentation's entries, word for word."""

from __future__ import annotations

import html
import io
import re
from pathlib import Path

import pytest
from glucocraft import report_pdf as P

from js_i18n import catalogue, documentation_text

REPO = Path(__file__).resolve().parents[1]
WWW = REPO / "custom_components" / "glucocraft" / "www"
DOCS = (WWW / "glucocraft-docs.js").read_text(encoding="utf-8")
DOC_TEXT = documentation_text()  # the Documentation's English text, with its citation tokens
README = (REPO / "README.md").read_text(encoding="utf-8")


def _refs() -> dict[str, tuple[str, str, str]]:
    """REFS of glucocraft-docs.js: id -> (in-text label, entry with the appended URL / DOI, entry text)."""
    body = DOCS[DOCS.index("const REFS = {") + len("const REFS = {") : DOCS.index("\n};", DOCS.index("const REFS = {"))]
    body = re.sub(r"^\s*//.*$", "", body, flags=re.MULTILINE)  # comment lines
    out = {}
    # tokens: string literals, keys, "[", "]", ",", "+"
    tok = re.compile(r'"((?:[^"\\]|\\.)*)"|(\w+):|(\[)|(\])|(,)|(\+)|\s+')
    key, vals, cur, depth = None, [], None, 0
    for m in tok.finditer(body):
        s, k, ob, cb, comma, plus = m.groups()
        if k and depth == 0:
            key, vals, cur = k, [], None
        elif ob:
            depth += 1
        elif s is not None and depth:
            cur = (cur or "") + s.replace('\\"', '"')
        elif plus:
            continue
        elif comma and depth and cur is not None:
            vals.append(cur)
            cur = None
        elif cb:
            depth -= 1
            if cur is not None:
                vals.append(cur)
                cur = None
            vals += [""] * (5 - len(vals))
            out[key] = vals[:5]
    for key, vals in list(out.items()):
        label, text, doi, _pmid, url = vals
        full = text + (f" {url}" if url else "") + (f" https://doi.org/{doi}" if doi else "")
        out[key] = (html.unescape(label), full, text)
    return out


REFS = _refs()
LABEL = {v[0]: k for k, v in REFS.items()}
CITE = re.compile(
    "|".join(
        sorted(
            (re.escape(lab).replace(r"\ ", r"\s+").replace(r"\&", "(?:&|&amp;)") for lab in LABEL),
            key=len,
            reverse=True,
        )
    )
)


def _cited(text: str) -> set[str]:
    text = html.unescape(re.sub(r"\s+", " ", text))
    # narrative citations, "Danne et al. (2017)", in the parenthetical form of the label
    text = re.sub(r" \(((?:19|20)\d\d[a-z]?|n\.d\.(?:-[a-z])?)\b", r", \1", text)
    return {LABEL[re.sub(r"\s+", " ", m.group(0)).replace("&amp;", "&")] for m in CITE.finditer(text)}


def test_every_documentation_citation_has_an_entry_and_every_entry_is_cited():
    assert len(REFS) >= 39
    # the document's citation tokens: {cite:id1,id2}, {cite:id|part}, {ref:id}, {ref:id|text}, {ref_year:id}
    used = set()
    for m in re.finditer(r"\{(?:cite|ref|ref_year):([^{}|]+)(?:\|[^{}]*)?\}", DOC_TEXT):
        ids = m.group(1).split(",")
        assert all(i in REFS for i in ids), m.group(0)
        used |= set(ids)
    assert used == set(REFS), set(REFS) - used


def test_the_pdf_lists_exactly_the_works_it_cites_with_the_documentation_entries():
    src = Path(P.__file__).read_text(encoding="utf-8")
    src = src[: src.index("REFERENCES = (")] + src[src.index("\n)\n", src.index("REFERENCES = (")) :]
    # the report's texts: the catalogue (with its citation placeholders filled) and the code
    texts = [P.ctx(k.split(".")[-1]) if k.startswith("report.context.") else v for k, v in catalogue("report").items()]
    texts = [v.replace("{ada_a}", P.ADA_A).replace("{ada_b}", P.ADA_B) for v in texts]
    strings = " ".join([*re.findall(r'"((?:[^"\\\n]|\\.)*)"', src), *texts])
    keys = [k for k, _ in P.REFERENCES]
    assert set(keys) == _cited(strings), set(keys) ^ _cited(strings)
    for key, text in P.REFERENCES:
        assert text == REFS[key][1], key
    # APA order: as the Documentation sorts them (the author list word by word, then the year)
    assert keys == sorted(
        keys, key=lambda k: (re.split(r"[\s,.;&]+", REFS[k][2].split(" (")[0].replace("&amp;", "")), REFS[k][2])
    )


def test_the_readme_lists_exactly_the_works_it_cites():
    head, refs = README.split("\n## References\n", 1)
    entries = [ln[2:] for ln in refs.splitlines() if ln.startswith("- ")]
    md = {k: html.unescape(re.sub(r"</?i>", "*", v[1])) for k, v in REFS.items()}
    listed = set()
    for e in entries:
        hit = [k for k, v in md.items() if v == e]
        assert hit, e[:80]
        listed.add(hit[0])
    assert listed == _cited(head), listed ^ _cited(head)


def test_the_quotations_carry_locators_and_the_exact_words():
    # the ADA Standards 2026, Section 6, Glycemic Assessment by A1C, para. 1 (the online text, unpaginated): "A1C
    # reflects average glycemia over approximately 2–3 months." (the 2025 edition has it on p. S129)
    assert 'approximately 2–3 months" {cite:ada2026|Glycemic Assessment by A1C section, para. 1}' in DOC_TEXT
    assert "2-3 months" not in DOC_TEXT
    assert "{cite:ada2025|p. S130}" in DOC_TEXT and "{cite:battelino2023|p. 42}" in DOC_TEXT
    assert "{cite:nathan2014|EDIC/Metabolic Memory section, para. 1}" in DOC_TEXT
    assert "{cite:laiteerapong2019|Abstract, para. 1}" in DOC_TEXT
    # no page number inside a reference entry (the locator belongs in the citation)
    assert not re.search(r", p\. \d+\]", DOCS)


@pytest.mark.slow
@pytest.mark.parametrize("a1c", ["percent", "both"])
@pytest.mark.parametrize("paper", ["letter", "a4"])
def test_the_pdf_ends_with_its_references_and_then_the_license(monkeypatch, a1c, paper):
    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")
    from glucocraft.regional import Regional

    from test_pdf_layout_and_regional import _data

    rg = Regional(a1c_unit=a1c, paper_size=paper)
    pdf, _pages = P.render(_data(30, False, "mg/dL", rg), True, False)
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    text = " ".join(" ".join((p.extract_text() or "").split()) for p in reader.pages)
    refs_at = text.index(
        "References The works cited in this report, in the style of the American Psychological Association (APA), "
        "7th edition."
    )
    assert refs_at < text.index("License", refs_at)  # the last section
    for key, entry in P.REFERENCES:
        lead = html.unescape(re.sub(r"</?i>", "", entry))[:28]
        assert (lead in text[refs_at:]) == (key != "hoelzel2004" or a1c == "both"), key
