# License: AGPL-3.0-only (LICENSE).
"""The CGM Report: values rounded once, and the PDF's text and document information.

Rules under test (docs/ACCESSIBILITY.md rule 4, docs/DEVELOPER_GUIDE.md "Where each rule lives"):
- The lowest and highest value of a period and the lowest value of each hypoglycemia event are passed as stored, so
  that a display in mmol/L rounds them once (72.5 mg/dL is 4.0 mmol/L; rounded to 73 mg/dL first, it would be 4.1).
- In the PDF, ✓ and ✗ are real text: the words "met" and "not met".
- The PDF sets its title, author, subject and language (/Lang) and is tagged (tests/test_pdf_tagging.py checks
  its structure); it states the PDF/UA-1 identification, which veraPDF passed (tools/check_pdf_ua.py).
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from glucocraft import report as R
from glucocraft.regional import Regional
from glucocraft.units import fmt

from test_pdf_layout_and_regional import LA, NOW, NOW_LOCAL

reportlab = pytest.importorskip("reportlab")
pypdf = pytest.importorskip("pypdf")

from glucocraft import report_pdf as P

from conftest import Rd


def _readings():
    """20 days of 5-minute readings at 120 mg/dL, with one hypoglycemia event 3 days ago (20 minutes below 70, lowest
    60.5) and one reading of 72.5 the day before."""
    start = int(NOW) - 20 * 86400
    out = [Rd(t, 120.0) for t in range(start, int(NOW) - 3600, 300)]
    k = len(out) - 3 * 288
    for i, v in enumerate((65.0, 60.5, 62.0, 66.0, 68.0)):
        out[k + i].raw = v
    out[k - 288].raw = 72.5
    return out


def _report(days=14, full=False):
    rg = Regional()
    data = R.build_report(_readings(), 300, LA, days, full, [], now=NOW, regional=rg)
    data.update(
        {
            "label": f"{days} Days",
            "sources": ["Test source"],
            "created": rg.stamp_year(NOW_LOCAL),
            "unit": "mmol/L",
            **rg.as_dict(),
            "integration_version": "0",
        }
    )
    return data


# ------------------------------------------------------------------------------------------------ rounded once
def test_lowest_and_highest_are_passed_as_stored():
    d = _report()
    assert d["standard"]["min"] == 60.5 and d["standard"]["max"] == 120.0
    lowest = [row[2] for row in d["lows"]["list"]]
    assert lowest == [60.5]
    # one rounding, in the display unit: 60.5 mg/dL = 3.36 mmol/L -> 3.4; 72.5 mg/dL = 4.02 -> 4.0 (not 73 -> 4.1)
    assert fmt(60.5, "mmol/L") == "3.4" and fmt(72.5, "mmol/L") == "4.0" and fmt(73, "mmol/L") == "4.1"


def test_daily_log_and_daily_profiles_are_rounded_once_in_mmol():
    """81.9 mg/dL is 4.546 mmol/L, shown 4.5; rounded to 82 mg/dL first it would be 4.6 (twice rounded)."""
    start = int(NOW) - 20 * 86400
    readings = [Rd(t, 81.9) for t in range(start, int(NOW) - 3600, 300)]
    rg = Regional()
    data = R.build_report(readings, 300, LA, 14, True, [], now=NOW, regional=rg)
    log, daily = data["days"]["log"], data["standard"]["daily"]
    assert {v for day in log for v in day[0] + day[1]} == {81.9}  # each hour's minimum and maximum as stored
    assert {round(v, 9) for day in daily for v in day if v is not None} == {81.9}  # the 10-minute means unrounded
    assert {round(v, 9) for v in data["standard"]["profile"]["p50"] if v is not None} == {81.9}
    assert fmt(81.9, "mmol/L") == "4.5" and fmt(82, "mmol/L") == "4.6"
    data.update({"label": "14 Days", "sources": ["Test source"], "created": "x", "unit": "mmol/L", **rg.as_dict()})
    out, _pages = P.render(data, True, True)
    text = " ".join(" ".join((p.extract_text() or "").split()) for p in pypdf.PdfReader(io.BytesIO(out)).pages)
    assert "4.5" in text and "4.6" not in text
    assert "4.5 millimoles per liter" in P._alt_day("Monday", daily[0])  # the profile's description, once


# ------------------------------------------------------------------------------------------------ the PDF
@pytest.fixture(scope="module")
def pdf():
    out, _pages = P.render(_report(30, True), True, True)  # the full report: its Data Quality page
    return pypdf.PdfReader(io.BytesIO(out))


def test_marks_are_written_as_words(pdf):
    text = " ".join(" ".join((p.extract_text() or "").split()) for p in pdf.pages).replace(P.NB, " ")
    # the data-sufficiency context names what the 14-day / 70% rule governs; the PDF shows no marks for it
    assert "For this reason, the values of a period are shown only with at least 70% sensor active" in text
    assert "✓" not in text and "✗" not in text
    # met / not met are words in the catalogue (report.mark.*); no text of the report carries the marks themselves
    catalogue = json.loads((Path(P.__file__).parent / "i18n" / "en" / "report.json").read_text(encoding="utf-8"))
    assert not [k for k, v in catalogue.items() if "✓" in v or "✗" in v]


def test_the_document_information_and_language(pdf):
    info = pdf.metadata
    assert info.title == "GlucoCraft CGM Report — 30 Days"
    assert info.author == "GlucoCraft" and info.subject == P.report_description()
    root = pdf.trailer["/Root"]
    assert root["/Lang"] == "en"
    assert root["/ViewerPreferences"]["/DisplayDocTitle"] in (True, "true")
    # tagged: a structure tree and the "marked" flag; the PDF/UA-1 identification in the metadata
    assert "/StructTreeRoot" in root and root["/MarkInfo"]["/Marked"] in (True, "true")
    assert b"<pdfuaid:part>1</pdfuaid:part>" in root["/Metadata"].get_data()
