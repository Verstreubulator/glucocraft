# License: AGPL-3.0-only (LICENSE).
"""The CGM Report PDF is a tagged PDF: its structure checked with pikepdf (custom_components/glucocraft/pdf_tags.py).

A subset of the machine checks of PDF/UA-1 (ISO 14289-1, the Matterhorn Protocol), for the reports of
test_i18n_english_output (14-day summary in mg/dL; 30-day full report in mmol/L with a decimal comma, both A1C units,
A4, black and white, both appendices; 90-day summary with A1C in mmol/mol). veraPDF runs the whole PDF/UA-1 profile
when it is installed (tools/check_pdf_ua.py); this test needs only pikepdf (requirements_test.txt).

Rules under test (docs/ACCESSIBILITY.md rule 4):
- the document: /Lang (the catalogue's language), the title shown instead of the file name, /MarkInfo, the XMP title
  and language, and no PDF/UA identification before a validator has passed the file;
- every page: /Tabs /S and /StructParents; every text, path, image and shading inside marked content, either tagged
  content (an MCID that the ParentTree maps to its structure element) or an artifact, never one inside the other;
- the structure: one H1 first, headings that never skip a level, in the order of the page (top to bottom, left to
  right); every Figure with an alternative text that states data and a bounding box; every table with header cells,
  each with its scope, and rows of equal width; "met" / "not met" as real text;
- the fonts: every font embedded, with a ToUnicode map; every character the report can write has a glyph in them.
"""

from __future__ import annotations

import itertools
import json
import re

import pytest

from conftest import REPO

pikepdf = pytest.importorskip("pikepdf")
pytest.importorskip("reportlab")

from glucocraft import const as C
from glucocraft import report_pdf as P
from glucocraft.i18n import language

from test_i18n_english_output import _report_cases

PKG = REPO / "custom_components" / "glucocraft"
Name = pikepdf.Name
TEXT_OPS = {"Tj", "TJ", "'", '"'}
PAINT_OPS = TEXT_OPS | {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "sh", "Do", "BI"}


def _unicode_map(font) -> dict[int, str]:
    """Return a simple font's codes as text, from its ToUnicode map (bfchar entries; other codes are themselves)."""
    cmap = font.ToUnicode.read_bytes().decode("latin-1")
    pairs = re.findall(r"<([0-9A-Fa-f]{2})>\s*<([0-9A-Fa-f]{4,})>", cmap.split("beginbfchar", 1)[-1])
    return {int(c, 16): bytes.fromhex(u).decode("utf-16-be") for c, u in pairs if int(u, 16)}


def _text(operands, op, fonts, font) -> str:
    """Return the text a text-showing operator draws."""
    strings = operands[0] if op == "TJ" else [operands[-1]]
    umap = fonts.get(font, {})
    return "".join(umap.get(b, chr(b)) for s in strings if isinstance(s, pikepdf.String) for b in bytes(s))


class Tagged:
    """A rendered PDF opened with pikepdf: its marked content per page and its structure tree in reading order."""

    def __init__(self, pdf_bytes: bytes):
        import io

        self.pdf = pikepdf.open(io.BytesIO(pdf_bytes))
        self.page_of = {p.objgen: i for i, p in enumerate(self.pdf.pages)}
        # per page: MCID -> {"text": ..., "pos": first text position}, artifact texts, and marking problems
        self.mcids: list[dict[int, dict]] = []
        self.artifact_text: list[str] = []
        self.problems: list[str] = []
        for i, page in enumerate(self.pdf.pages):
            fonts = {str(n): _unicode_map(f) for n, f in page.Resources.Font.items()}
            stack, font, pos, found = [], None, None, {}
            for operands, op in pikepdf.parse_content_stream(page):
                op = str(op)
                if op in ("BDC", "BMC"):
                    tag = str(operands[0])
                    props = operands[1] if op == "BDC" else None
                    mcid = int(props.MCID) if isinstance(props, pikepdf.Dictionary) and "/MCID" in props else None
                    kinds = {"artifact" if tag == "/Artifact" else "tagged"}
                    if stack and {s[0] for s in stack} != kinds:
                        self.problems.append(f"page {i + 1}: {tag} nested inside {stack[-1][0]} content")
                    if mcid is not None:
                        if mcid in found:
                            self.problems.append(f"page {i + 1}: MCID {mcid} used twice")
                        found[mcid] = {"tag": tag, "text": "", "pos": None}
                    stack.append(("artifact" if tag == "/Artifact" else "tagged", mcid))
                elif op == "EMC":
                    if not stack:
                        self.problems.append(f"page {i + 1}: EMC without BDC")
                    else:
                        stack.pop()
                elif op == "Tf":
                    font = str(operands[0])
                elif op == "Tm":
                    pos = (float(operands[4]), float(operands[5]))
                elif op in PAINT_OPS:
                    if not stack:
                        self.problems.append(f"page {i + 1}: {op} outside marked content")
                    elif op in TEXT_OPS:
                        text = _text(operands, op, fonts, font)
                        mcid = next((m for _, m in reversed(stack) if m is not None), None)
                        if stack[-1][0] == "artifact":
                            self.artifact_text.append(text)
                        elif mcid is not None:
                            found[mcid]["text"] += text
                            found[mcid]["pos"] = found[mcid]["pos"] or pos
            if stack:
                self.problems.append(f"page {i + 1}: marked content left open")
            self.mcids.append(found)
        root = self.pdf.Root.StructTreeRoot
        nums = list(root.ParentTree.Nums)
        self.parent_tree = {int(nums[k]): nums[k + 1] for k in range(0, len(nums), 2)}
        self.elements: list = []  # (element, depth) in reading order
        self.claimed: set[tuple[int, int]] = set()
        self._walk(root.K, 0)

    def _kids(self, elem) -> list:
        kids = elem.get("/K")
        return [] if kids is None else list(kids) if isinstance(kids, pikepdf.Array) else [kids]

    def _walk(self, elem, depth):
        self.elements.append((elem, depth))
        for kid in self._kids(elem):
            if isinstance(kid, int) or (isinstance(kid, pikepdf.Dictionary) and kid.get("/Type") == Name.MCR):
                page = self.page_of[(kid.Pg if not isinstance(kid, int) else elem.Pg).objgen]
                mcid = int(kid) if isinstance(kid, int) else int(kid.MCID)
                self.claimed.add((page, mcid))
                owner = self.parent_tree[int(self.pdf.pages[page].obj.StructParents)][mcid]
                if owner.objgen != elem.objgen:
                    self.problems.append(f"ParentTree: page {page + 1} MCID {mcid} points elsewhere")
            else:
                self._walk(kid, depth + 1)

    def text_of(self, elem) -> str:
        """Return the text of an element and its descendants, in reading order."""
        out = []
        for kid in self._kids(elem):
            if isinstance(kid, int) or kid.get("/Type") == Name.MCR:
                page = self.page_of[(kid.Pg if not isinstance(kid, int) else elem.Pg).objgen]
                out.append(self.mcids[page][int(kid) if isinstance(kid, int) else int(kid.MCID)]["text"])
            else:
                out.append(self.text_of(kid))
        return " ".join(s for s in out if s)

    def first_position(self, elem) -> tuple[int, float, float]:
        """Return (page, x, y) of an element's first text."""
        kid = self._kids(elem)[0]
        page = self.page_of[(kid.Pg if not isinstance(kid, int) else elem.Pg).objgen]
        x, y = self.mcids[page][int(kid) if isinstance(kid, int) else int(kid.MCID)]["pos"]
        return page, x, y

    def of_role(self, *roles) -> list:
        """The structure elements of the given roles."""
        return [e for e, _ in self.elements if str(e.S)[1:] in roles]


CASES = {name: (data, full, color) for name, data, full, color in _report_cases()}


@pytest.fixture(scope="module", params=list(CASES))
def report(request):
    data, full, color = CASES[request.param]
    pdf, pages = P.render(data, color, full)
    return request.param, Tagged(pdf), pages


@pytest.mark.slow
def test_document_language_title_and_marking(report):
    _name, tg, _pages = report
    root = tg.pdf.Root
    assert str(root.Lang) == language() == "en"
    assert root.ViewerPreferences.DisplayDocTitle is True
    assert str(tg.pdf.docinfo.Title).startswith("GlucoCraft CGM Report — ")
    assert root.MarkInfo.Marked is True
    meta = tg.pdf.open_metadata()
    assert meta.get("dc:title") == str(tg.pdf.docinfo.Title) and "en" in str(meta.get("dc:language"))
    assert meta.get("pdfuaid:part") is None  # no conformance claimed before veraPDF has passed the report
    assert tg.pdf.pdf_version == "1.7"


@pytest.mark.slow
def test_every_page_is_tagged_content_or_artifact(report):
    _name, tg, pages = report
    assert len(tg.pdf.pages) == pages
    for i, page in enumerate(tg.pdf.pages):
        assert page.obj.Tabs == Name.S and int(page.obj.StructParents) == i
    assert not tg.problems, tg.problems[:10]
    # every marked content with an MCID belongs to one structure element, and every element's MCID exists
    marked = {(p, m) for p, found in enumerate(tg.mcids) for m in found}
    assert marked == tg.claimed
    # the page header and footer are artifacts: the footer of every page, the header from page 2
    footers = [s for s in tg.artifact_text if s.startswith("GlucoCraft. For development and testing only. ")]
    assert len(footers) == pages


@pytest.mark.slow
def test_headings_start_at_h1_never_skip_and_follow_the_page(report):
    name, tg, _pages = report
    heads = tg.of_role("H1", "H2", "H3")
    levels = [int(str(h.S)[2]) for h in heads]
    assert levels[0] == 1 and levels.count(1) == 1
    assert all(b <= a + 1 for a, b in itertools.pairwise(levels)), levels
    # the reading order of the headings is the order on the page: by page, top to bottom, then left to right
    where = [tg.first_position(h) for h in heads]
    assert where == sorted(where, key=lambda w: (w[0], -w[2], w[1])), where
    texts = [tg.text_of(h) for h in heads]
    assert texts[:6] == [
        "CGM Report: Continuous Glucose Monitoring",
        "Time in Ranges",
        "Glucose Metrics",
        "Glucose Profile — 14 Days",
        "Notes",  # page 1's Notes, after its data (the 14-day profile)
        "Daily Glucose Profiles — 14 Days",
    ]
    assert texts[-2:] == ["References", "License"]
    if name.startswith("30 full"):
        assert "Extended high-glucose events" in texts and "Daily Log — 30 Days" in texts

    # the headings H1 and H2 are the bookmarks, one each, in their order (the page-1 profile among them)
    def flat(items):
        for item in items:
            yield item.title
            yield from flat(item.children)

    with tg.pdf.open_outline() as outline:
        bookmarks = list(flat(outline.root))
    assert bookmarks == [tg.text_of(h) for h in heads if int(str(h.S)[2]) <= 2]


@pytest.mark.slow
def test_every_section_reads_caption_data_notes(report):
    """The reading order of every section: its caption, its data, then its Notes (H3 "Notes") at its end. No table
    or figure of a section follows its Notes; every section with data has Notes, except About This Report, the
    references and the License (no data) and a comparison that is not available (its caption states why)."""
    _name, tg, _pages = report
    sections: list[dict] = []
    for elem, _depth in tg.elements:
        role = str(elem.S)[1:]
        if role in ("H1", "H2"):
            sections.append({"title": tg.text_of(elem), "data": False, "notes": False, "after": []})
        elif role == "H3" and tg.text_of(elem) == "Notes":
            sections[-1]["notes"] = True
        elif role in ("Table", "Figure") and sections[-1]["notes"]:
            sections[-1]["after"].append(role)
        elif role in ("Table", "Figure"):
            sections[-1]["data"] = True
    # page 1 is one section with its Notes after its last part, the 14-day glucose profile
    page1 = [s for s in sections if s["title"] in ("Time in Ranges", "Glucose Metrics")]
    assert all(not s["notes"] for s in page1) and sections[3]["title"].startswith("Glucose Profile")
    for s in sections:
        assert not s["after"], s["title"]  # nothing of the section's data after its Notes
        if s["data"] and s not in page1:
            assert s["notes"], s["title"]
    assert not any(s["notes"] for s in sections if s["title"] in ("About This Report", "References"))


@pytest.mark.slow
def test_figures_have_an_alternative_text_with_the_data(report):
    _name, tg, _pages = report
    figures = tg.of_role("Figure")
    assert len(figures) >= 1 + 1 + 14  # the time-in-ranges bar, the glucose profile, the 14 daily profiles
    for fig in figures:
        alt = str(fig.Alt)
        assert alt and (re.search(r"\d", alt) or "no data" in alt), alt
        assert "mg/dL" not in alt and "mmol/L" not in alt  # units written out, as a screen reader reads them
        assert "minus" not in alt, alt  # every glucose value is a stored one (40 to 400 mg/dL, LO / HI beyond)
        assert len(fig.A.BBox) == 4 and fig.A.O == Name.Layout
    alts = [str(f.Alt) for f in figures]
    assert alts[0].startswith("Time in ranges: very low ") and " percent" in alts[0]
    assert alts[1].startswith("Glucose Profile — 14 Days: median from ")


@pytest.mark.slow
def test_tables_have_scoped_header_cells_and_even_rows(report):
    name, tg, _pages = report
    tables = tg.of_role("Table")
    assert len(tables) >= 8
    for table in tables:
        rows = [k for k in tg._kids(table) if str(k.S) == "/TR"]
        assert rows and all(str(c.S) in ("/TH", "/TD") for r in rows for c in tg._kids(r))
        cells = [c for r in rows for c in tg._kids(r)]
        headers = [c for c in cells if str(c.S) == "/TH"]
        assert headers and all(c.A.Scope in (Name.Column, Name.Row) for c in headers)
        widths = {sum(int(c.A.get("/ColSpan", 1)) if "/A" in c else 1 for c in tg._kids(r)) for r in rows}
        assert len(widths) == 1, (tg.text_of(table)[:80], widths)  # every row as wide as the others
    # the low-glucose summary of the full report: each period column has two column headers, the period and its
    # sensor active
    for table in tables:
        rows = [k for k in tg._kids(table) if str(k.S) == "/TR"]
        if tg.text_of(tg._kids(rows[0])[0]) != "Period" or len(rows) < 3:
            continue
        if not tg.text_of(tg._kids(rows[1])[0]).startswith("Sensor active"):
            continue
        assert all(str(c.S) == "/TH" and c.A.Scope == Name.Column for r in rows[:2] for c in tg._kids(r))
        assert tg.text_of(tg._kids(rows[2])[0]) and str(tg._kids(rows[2])[0].A.Scope) == "/Row"
    if name.startswith("30 full"):  # the custom appendix: its results as real text
        results = {tg.text_of(c) for c in tg.of_role("TD")}
        assert results & {"met", "not met"}


@pytest.mark.slow
def test_fonts_are_embedded_with_a_unicode_map(report):
    _name, tg, _pages = report
    for page in tg.pdf.pages:
        for font in page.Resources.Font.values():
            assert "/FontFile2" in font.FontDescriptor, font.BaseFont
            assert "/ToUnicode" in font
            assert "LiberationSans" in str(font.BaseFont)


def test_every_character_of_the_report_has_a_glyph():
    """The glyphs of the bundled fonts cover every character of the report's catalogue, the disclaimer, the
    license notices and the reference list (the references use the italic face too)."""
    from reportlab.pdfbase import pdfmetrics

    P._use_fonts("en")
    catalogue = json.loads((PKG / "i18n" / "en" / "report.json").read_text(encoding="utf-8"))
    texts = [*catalogue.values(), C.DISCLAIMER_PLAIN, C.DISCLAIMER_LINE, C.COPYRIGHT, *C.GNU_NOTICE, "0123456789"]
    references = [re.sub(r"<[^>]+>", "", text).replace("&amp;", "&") for _, text in P.REFERENCES]
    for face, strings in ((P.FONT, texts + references), (P.BOLD, texts), ("LiberationSans-Italic", references)):
        font = pdfmetrics.getFont(face).face
        chars = {ch for s in strings for ch in s if ch not in "\n"}
        missing = sorted(ch for ch in chars if ord(ch) not in font.charToGlyph)
        assert not missing, (face, missing)
