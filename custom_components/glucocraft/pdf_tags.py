# License: AGPL-3.0-only (LICENSE).
"""A tagged PDF from ReportLab's canvas: the structure tree, marked content and artifacts.

A tagged PDF carries, besides what is drawn, a structure tree: headings, paragraphs, lists, tables with their header
cells, and figures with an alternative text, in reading order (ISO 32000-1:2008, sections 14.7 and 14.8). Screen
readers read the PDF by that tree, and decoration (rules, frames, backgrounds, the page header and footer) is marked
as an artifact, which they skip. PDF/UA-1 (ISO 14289-1) is the specification for accessible PDF files built on it.

The free edition of ReportLab writes no structure tree, so this module adds it with ReportLab's public canvas method
addLiteral() (it writes PDF operators into the page) and ReportLab's own PDF object classes:

- TaggedCanvas is a ReportLab Canvas whose drawing calls mark what they draw. Text belongs to the structure element
  that is open (a heading, a paragraph, a table cell). Lines, rectangles and other paths are artifacts, unless a
  Figure is open, which takes everything drawn inside it. A text drawn while no element that holds content is open
  raises StructureError, so that no text can be left out of the structure unnoticed.
- Tagger collects the structure elements in the order they are opened, which is the reading order, numbers the
  marked content of each page (its MCID), and, when the document is finished, writes the structure tree with its
  ParentTree, the catalog's /MarkInfo, each page's /StructParents and /Tabs, and the XMP metadata (title, language).

Use:
    c = TaggedCanvas(buffer, lang="en", title="...", pagesize=..., initialFontName=<an embedded font>)
    with c.tagger.tag("H1"):
        c.drawString(...)                       # the heading's text
    c.line(...)                                 # a rule: an artifact
    with c.tagger.tag("Table"), c.tagger.tag("TR"):
        with c.tagger.tag("TH", A=table_attributes(scope="Column")):
            c.drawString(...)
    with c.tagger.figure("Bar chart of ...", bbox=(x0, y0, x1, y1)):
        ...                                     # a chart, drawn as before
    with c.tagger.artifact("Pagination", "Footer"):
        c.drawString(...)                       # a page number
    c.showPage()
    c.save()                                    # writes the structure tree first

Every font of a tagged document must be embedded (PDF/UA-1, 7.21.4.1): the caller registers a TrueType font and
passes it as initialFontName, because ReportLab starts each page with its initial font selected.

The page keys /StructParents and /Tabs are not among the keys ReportLab writes for a page. Tagger wraps the
document's addPage so that each page gets both.

ReportLab internals: the PDF code relies on these undocumented names of ReportLab 4.2.5. The version is pinned in
manifest.json because of them; check every name here before changing that pin.
- here (Tagger): Canvas._doc (the canvas's PDFDocument), PDFPage.__NoDefault__ (the page keys written),
  PDFDocument.addPage, .thisPageName, .Reference, .idToObject (every object of the document) and .Catalog;
  tests/test_pdf_tagging.py fails if the structure is not written.
- report_pdf._creation_time (byte-identical renders): Canvas._doc, PDFDocument._timeStamp, the TimeStamp fields t, lt
  and YMDhms, and PDFDocument.signature with ReportLab's own seed b"a reportlab document" (the file identifier);
  tests/test_report_enough_data_and_limit_marks.py fails if two renders of the same data differ.
- report_pdf.Report.spoken_text: PDFDictionary.dict (the keys of a structure element), and an attribute
  spoken_parts that it sets on a table cell's structure element (_Elem).
"""

from __future__ import annotations

import functools
from collections.abc import Iterator
from contextlib import contextmanager
from xml.sax.saxutils import escape

from reportlab.pdfbase import pdfdoc
from reportlab.pdfbase.pdfdoc import PDFArray, PDFDictionary, PDFName, PDFObjectReference, PDFStream, PDFString
from reportlab.pdfgen.canvas import Canvas

# The structure types that hold content of their own (their text and paths are marked with an MCID); every other
# type (Document, Table, TR, L, LI) only groups elements. ISO 32000-1, section 14.8.4. A Span is a part of a text inside
# one of them, for example a text with an /ActualText that says how it is read.
CONTENT_ROLES = frozenset({"H1", "H2", "H3", "P", "Lbl", "LBody", "TH", "TD", "Figure", "Span"})

# The keys a page dictionary is written with: ReportLab's own and the two a tagged page needs.
_PAGE_KEYS = [*pdfdoc.PDFPage.__NoDefault__, "StructParents", "Tabs"]


# The marked-content operators (ISO 32000-1, section 14.6): the beginning of a tagged sequence with its MCID, the
# beginning of an artifact (with its type and an optional subtype), and the end of either
_BDC_TAGGED = "/{role} <</MCID {mcid}>> BDC"
_BDC_ARTIFACT = "/Artifact <</Type /{kind}{subtype}>> BDC"
_EMC = "EMC"

# The messages of StructureError: programming errors in the drawing code, for developers
_MISUSE = {
    "open": "{role} opened inside a figure or an artifact",
    "close": "close({role}) does not match the open {open}",
    "artifact": "an artifact inside a figure or an artifact",
    "text": "text drawn inside {role}, which holds no content of its own",
    "page": "a page ended inside a figure or an artifact",
    "left_open": "elements left open: {roles}",
}


class StructureError(RuntimeError):
    """A drawing call that does not fit the structure (a text outside any content element, an unbalanced close)."""

    def __init__(self, kind: str, **params) -> None:
        super().__init__(_MISUSE[kind].format(**params))


def table_attributes(scope: str | None = None, colspan: int = 1) -> PDFDictionary:
    """Return the Table attributes of a cell: the scope of a header cell ("Column" or "Row"), a column span."""
    attrs = {"O": PDFName("Table")}
    if scope:
        attrs["Scope"] = PDFName(scope)
    if colspan > 1:
        attrs["ColSpan"] = colspan
    return PDFDictionary(attrs)


class _Elem(PDFDictionary):
    """A structure element: a PDF dictionary with its children (elements and marked content) in reading order.

    Its other keys come from `extra`; a text value (/Alt, /ActualText) is written as a PDF text string.
    """

    def __init__(self, role: str, extra: dict) -> None:
        keys = {k: PDFString(v) if isinstance(v, str) else v for k, v in extra.items()}
        super().__init__({"Type": PDFName("StructElem"), "S": PDFName(role), **keys})
        self.role = role
        self.kids: list = []
        self.page: int | None = None  # the page of its first marked content (its /Pg)


class Tagger:
    """Collect the structure tree while a TaggedCanvas draws, and write it into the document at the end."""

    def __init__(self, canvas: Canvas, lang: str, title: str) -> None:
        """Start the structure tree of the canvas's document (its root and the Document element, in the language
        `lang`, titled `title`), and have every page the document adds carry its /StructParents key and the structure
        tab order."""
        self.c, self.doc = canvas, canvas._doc
        self.lang, self.title = lang, title
        self.root = PDFDictionary({"Type": PDFName("StructTreeRoot")})
        self.document = _Elem("Document", {})
        self.document["P"] = self.doc.Reference(self.root)
        self.stack: list[_Elem] = [self.document]
        self.page_index = 0  # the page being drawn; also its /StructParents key
        self.page_refs: list[PDFObjectReference] = []
        self.parent_tree: dict[int, list] = {}  # page -> the element of each of its MCIDs, in MCID order
        self.enclosing: str | _Elem | None = None  # an open figure or artifact context: it takes everything drawn
        self.marked: str | _Elem | None = None  # the marked content open now: "artifact", an element, or None
        add_page = self.doc.addPage

        def add_tagged_page(page) -> None:
            """Give the page its /StructParents and /Tabs (structure order) before ReportLab adds it."""
            page.__NoDefault__ = _PAGE_KEYS  # an instance attribute: ReportLab then writes the two keys
            page.StructParents = self.page_index
            page.Tabs = PDFName("S")
            add_page(page)
            self.page_index += 1

        self.doc.addPage = add_tagged_page

    # ------------------------------------------------------------------ the structure
    def _element(self, role: str, attrs: dict) -> _Elem:
        """Create an element as the next child of the open element."""
        elem = _Elem(role, attrs)
        parent = self.stack[-1]
        elem["P"] = self.doc.Reference(parent)
        parent.kids.append(self.doc.Reference(elem))
        return elem

    def open(self, role: str, **attrs) -> _Elem:
        """Open an element (it stays open until close()); its attributes become keys of its dictionary (A: its
        attribute dictionary, Alt or ActualText: a text for screen readers)."""
        if self.enclosing is not None:
            raise StructureError("open", role=role)
        elem = self._element(role, attrs)
        self.stack.append(elem)
        return elem

    def close(self, role: str) -> None:
        """Close the innermost open element, which must be a `role`."""
        if len(self.stack) < 2 or self.stack[-1].role != role:
            raise StructureError("close", role=role, open=self.stack[-1].role)
        if self.marked is self.stack[-1]:
            self._switch(None)
        self.stack.pop()

    @contextmanager
    def tag(self, role: str, **attrs) -> Iterator[_Elem]:
        """Open an element for the drawing calls inside the block."""
        elem = self.open(role, **attrs)
        yield elem
        self.close(role)

    @contextmanager
    def figure(self, alt: str, bbox: tuple[float, float, float, float]) -> Iterator[None]:
        """Mark everything drawn inside the block as one Figure with an alternative text and its bounding box."""
        layout = PDFDictionary({"O": PDFName("Layout"), "BBox": PDFArray([round(v, 2) for v in bbox])})
        elem = self.open("Figure", Alt=alt, A=layout)
        self._switch(elem)
        self.enclosing = elem
        try:
            yield
        finally:
            self.enclosing = None
            self.close("Figure")

    @contextmanager
    def artifact(self, kind: str = "Layout", subtype: str | None = None) -> Iterator[None]:
        """Mark everything drawn inside the block, text included, as an artifact (Pagination: a header or footer)."""
        if self.enclosing is not None:
            raise StructureError("artifact")
        self._switch(None)
        self.c.addLiteral(_BDC_ARTIFACT.format(kind=kind, subtype=f" /Subtype /{subtype}" if subtype else ""))
        self.enclosing = self.marked = "artifact"
        try:
            yield
        finally:
            self.c.addLiteral(_EMC)
            self.enclosing = self.marked = None

    # ------------------------------------------------------------------ marked content
    def _switch(self, target: str | _Elem | None) -> None:
        """End the marked content that is open and begin `target`'s: "artifact", an element's, or none."""
        if target is self.marked:
            return
        if self.marked is not None:
            self.c.addLiteral(_EMC)
        self.marked = target
        if target == "artifact":
            self.c.addLiteral(_BDC_ARTIFACT.format(kind="Layout", subtype=""))
        elif target is not None:
            mcid = len(self.parent_tree.setdefault(self.page_index, []))
            self.parent_tree[self.page_index].append(self.doc.Reference(target))
            if target.page is None:
                target.page = self.page_index
                target["Pg"] = self._page_ref()
            if target.page == self.page_index:
                target.kids.append(mcid)
            else:  # content of an element that began on an earlier page: a marked-content reference
                target.kids.append(PDFDictionary({"Type": PDFName("MCR"), "Pg": self._page_ref(), "MCID": mcid}))
            self.c.addLiteral(_BDC_TAGGED.format(role=target.role, mcid=mcid))

    def _page_ref(self) -> PDFObjectReference:
        """Return the reference of the page being drawn."""
        while len(self.page_refs) <= self.page_index:
            self.page_refs.append(PDFObjectReference(self.doc.thisPageName()))
        return self.page_refs[self.page_index]

    def before_text(self) -> None:
        """Mark a text that is about to be drawn: the open figure or artifact, else the open content element."""
        if self.enclosing is not None:
            return
        elem = self.stack[-1]
        if elem.role not in CONTENT_ROLES:
            raise StructureError("text", role=elem.role)
        self._switch(elem)

    def before_paint(self) -> None:
        """Mark a path that is about to be drawn: the open figure or artifact, else a Layout artifact."""
        if self.enclosing is None:
            self._switch("artifact")

    def before_state(self) -> None:
        """End the marked content before q or Q, so that marked content and saved graphics states nest."""
        if self.enclosing is None:
            self._switch(None)

    def end_page(self) -> None:
        """End the page's marked content (a marked-content sequence never spans two pages)."""
        if self.enclosing is not None:
            raise StructureError("page")
        self._switch(None)

    # ------------------------------------------------------------------ the end
    def finish(self) -> None:
        """Write the structure tree, the ParentTree, /MarkInfo and the XMP metadata into the document."""
        if len(self.stack) != 1:
            raise StructureError("left_open", roles=[e.role for e in self.stack[1:]])
        for obj in list(self.doc.idToObject.values()):
            if isinstance(obj, _Elem) and obj.kids:
                obj["K"] = obj.kids[0] if len(obj.kids) == 1 else PDFArray(obj.kids)
        self.root["K"] = self.doc.Reference(self.document)
        nums: list = []
        for page in range(self.page_index):
            nums += [page, PDFArray(self.parent_tree.get(page, []))]
        self.root["ParentTree"] = PDFDictionary({"Nums": PDFArray(nums)})
        self.root["ParentTreeNextKey"] = self.page_index
        catalog = self.doc.Catalog
        catalog.StructTreeRoot = self.root
        catalog.MarkInfo = PDFDictionary({"Marked": "true"})
        catalog.Metadata = self._xmp()

    def _xmp(self) -> PDFStream:
        """Return the XMP metadata stream with the document's title and language (Dublin Core)."""
        xmp = _XMP_PACKET.format(title=escape(self.title), lang=escape(self.lang))
        header = PDFDictionary({"Type": PDFName("Metadata"), "Subtype": PDFName("XML")})
        return PDFStream(dictionary=header, content=xmp.encode("utf-8"), filters=[])


# The XMP metadata packet (ISO 16684-1) with the Dublin Core title and language and the PDF/UA identification
# (PDF/UA-1, clause 5: pdfuaid:part 1). The identification is written because veraPDF's PDF/UA-1 profile passes the
# reports (tools/check_pdf_ua.py, which reports any failed rule as a failure).
_XMP_PACKET = (
    '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
    '<dc:title><rdf:Alt><rdf:li xml:lang="x-default">{title}</rdf:li></rdf:Alt></dc:title>\n'
    "<dc:language><rdf:Bag><rdf:li>{lang}</rdf:li></rdf:Bag></dc:language>\n"
    "</rdf:Description>\n"
    '<rdf:Description rdf:about="" xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/">\n'
    "<pdfuaid:part>1</pdfuaid:part>\n"
    "</rdf:Description></rdf:RDF></x:xmpmeta>\n"
    '<?xpacket end="w"?>'
)


class TaggedCanvas(Canvas):
    """A ReportLab Canvas that marks everything it draws for its Tagger (self.tagger)."""

    def __init__(self, *args, lang: str, title: str, **kwargs) -> None:
        """A Canvas (its own arguments) for a document in the language `lang`, titled `title`, with its Tagger."""
        super().__init__(*args, lang=lang, pdfVersion=(1, 7), **kwargs)  # PDF 1.7, the base of PDF/UA-1
        self.setTitle(title)
        self.tagger = Tagger(self, lang, title)

    def drawText(self, aTextObject) -> None:
        """Draw a text object (drawString, drawRightString, drawCentredString and Paragraph all end here)."""
        self.tagger.before_text()
        super().drawText(aTextObject)

    def saveState(self) -> None:
        """Save the graphics state outside any marked content (Tagger.before_state)."""
        self.tagger.before_state()
        super().saveState()

    def restoreState(self) -> None:
        """Restore the graphics state outside any marked content (Tagger.before_state)."""
        self.tagger.before_state()
        super().restoreState()

    def showPage(self) -> None:
        """End the page: its marked content is closed first (Tagger.end_page)."""
        self.tagger.end_page()
        super().showPage()

    def save(self) -> None:
        """Write the structure tree into the document (Tagger.finish), then the document."""
        self.tagger.finish()
        super().save()


# The canvas methods that paint a path, an image or a form: each marks its painting first (Tagger.before_paint)
_PAINTING = (
    "line",
    "lines",
    "bezier",
    "arc",
    "rect",
    "ellipse",
    "wedge",
    "circle",
    "roundRect",
    "drawPath",
    "clipPath",
    "shade",
    "linearGradient",
    "radialGradient",
    "drawImage",
    "drawInlineImage",
    "doForm",
)


def _marks_painting(name: str):
    """Return the canvas method `name`, marking its painting first."""
    base = getattr(Canvas, name)

    @functools.wraps(base)
    def method(self, *args, **kwargs):
        self.tagger.before_paint()
        return base(self, *args, **kwargs)

    return method


for _name in _PAINTING:
    setattr(TaggedCanvas, _name, _marks_painting(_name))
