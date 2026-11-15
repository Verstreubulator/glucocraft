# License: AGPL-3.0-only (LICENSE).
"""report_pdf.py and report.py: Letter and A4 layouts that fit, the decimal separator, the A1C / GMI unit, calendars
from the first day of the week, and the disclaimer once, in About This Report (its first sentence in bold); every page
footer is one line, and the final "License" section carries the license notice."""

from __future__ import annotations

import io
import math
import re
from datetime import datetime

import pytest
from glucocraft import const as C
from glucocraft import report as R
from glucocraft.limits import DEFAULT_LOWER, LIMIT_HIGH, LIMIT_LOW, UPPER_CHOICES
from glucocraft.regional import Regional
from glucocraft.units import report_threshold_texts

from conftest import LA, Rd

reportlab = pytest.importorskip("reportlab")
import itertools

from glucocraft import report_pdf as P

pypdf = pytest.importorskip("pypdf")

NOW_LOCAL = datetime(2026, 10, 22, 9, 0, tzinfo=LA)
NOW = NOW_LOCAL.timestamp()


# The measurement range of the synthetic CGM system, mg/dL: a value below or above it is stored as LO / HI at the limit
# (limits.classify), never as a number outside the range
LOWER, UPPER = DEFAULT_LOWER, UPPER_CHOICES[0]


def _reading(t: int, mg: float) -> Rd:
    """A stored reading of the value mg, LO / HI beyond the measurement range."""
    if mg < LOWER:
        return Rd(t, float(LOWER), LIMIT_LOW)
    if mg > UPPER:
        return Rd(t, float(UPPER), LIMIT_HIGH)
    return Rd(t, mg)


def _readings():
    """200 days every 5 minutes: a sine from 40 to 200 mg/dL, 55 mg/dL lower in the hour from 03:00 UTC (LO where
    that falls below 40 mg/dL)."""
    start = int(NOW) - 200 * 86400
    return [
        _reading(t, 120 + 80 * math.sin(t / 7000.0) - (55 if (t // 3600) % 24 == 3 else 0))
        for t in range(start, int(NOW) - 3600, 300)
    ]


READINGS = _readings()


def test_the_test_readings_are_stored_readings():
    """Every test reading lies within the measurement range; the values beyond it are LO readings."""
    assert all(LOWER <= r.raw <= UPPER for r in READINGS)
    assert all((r.limit == LIMIT_LOW) == (r.raw == LOWER) for r in READINGS)
    assert any(r.limit == LIMIT_LOW for r in READINGS) and not any(r.limit == LIMIT_HIGH for r in READINGS)


LAB = [{"date": "2026-09-01", "a1c": 7.1}, {"date": "2026-06-10", "a1c": 6.53, "a1c_mmol_mol": 48}]


# the ranges and goals in the settings for the custom appendix, with a GMI goal (its cell is the widest)
CUSTOM = {
    "ranges": {"very_low": 54, "low": 80, "tight_low": 80, "tight_high": 140, "high": 160, "very_high": 250},
    "goals": {
        "time_in_range": {"op": ">", "value": 75},
        "cv": {"op": "<=", "value": 33},
        "gmi": {"op": "<", "value": 7.0},
    },
}


def _data(days=30, full=True, unit="mg/dL", rg=None, custom=None):
    rg = rg or Regional()
    # the 90 days before the 2026-06-10 result begin before the 200 days kept -> no CGM comparison
    kept_from = NOW_LOCAL.replace(hour=0) - R.timedelta(days=200)
    lab = R.lab_payload(LAB, READINGS, 300, LA, 200, kept_from)
    data = R.build_report(
        READINGS, 300, LA, days, full, LAB, now=NOW, regional=rg, custom=custom, research=bool(custom)
    )
    assert data["lab"] == lab
    assert [r["no_cgm_comparison"] for r in lab["results"]] == [True, False]
    data.update(
        {
            "label": f"{days} Days",
            "sources": ["Test source"],
            "created": rg.stamp_year(NOW_LOCAL),
            "unit": unit,
            "thresholds": report_threshold_texts(unit),  # as report_service.async_data sends them
            **rg.as_dict(),
            "integration_version": "0.0.2",
        }
    )
    return data


class Recorder:
    """Every text drawn: (page, x left, x right, y, text)."""

    def __init__(self, monkeypatch):
        self.items: list[tuple[int, float, float, float, str]] = []
        self.page = 0
        rec = self
        orig_text = P.Report.text

        def text(self_, x, y, s, size=9, bold=False, g=0.0, align="left", said=None):
            w = self_.sw(s, P.BOLD if bold else P.FONT, size)
            left = x - w if align == "right" else x - w / 2 if align == "center" else x
            rec.items.append((rec.page, left, left + w, y, s))
            return orig_text(self_, x, y, s, size, bold, g, align, said)

        from reportlab.pdfgen import canvas as cv

        orig_show = cv.Canvas.showPage

        def show(self_):
            rec.page += 1
            return orig_show(self_)

        monkeypatch.setattr(P.Report, "text", text)
        monkeypatch.setattr(cv.Canvas, "showPage", show)


def _render(monkeypatch, data, color=True, full=True):
    rec = Recorder(monkeypatch)
    pdf, pages = P.render(data, color, full)
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == pages
    return pdf, pages, reader, rec


def _text(reader, page=None):
    pages = reader.pages if page is None else [reader.pages[page]]
    return " ".join(" ".join((p.extract_text() or "").split()) for p in pages)


# ------------------------------------------------------------------------------------------------ the layouts
CASES = [
    (paper, unit, full, a1c, dec)
    for paper in ("letter", "a4")
    for unit in ("mg/dL", "mmol/L")
    for full in (False, True)
    for a1c, dec in (("percent", "."), ("both", ","), ("mmol_mol", "."))
]


@pytest.mark.slow
@pytest.mark.parametrize("paper,unit,full,a1c,dec", CASES)
def test_every_text_inside_the_margins_and_no_overlap(monkeypatch, paper, unit, full, a1c, dec):
    rg = Regional("24" if unit == "mmol/L" else "12", "DMY", 0, dec, a1c, paper)
    _pdf, _pages, reader, rec = _render(monkeypatch, _data(30, full, unit, rg), True, full)
    _assert_fits(reader, rec, paper, unit)


@pytest.mark.slow
@pytest.mark.parametrize("paper", ["letter", "a4"])
@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
@pytest.mark.parametrize("a1c", ["mmol_mol", "both"])
def test_the_widest_cells_fit(monkeypatch, paper, unit, a1c):
    """The 90-day report (the 14-day periods with GMI in mmol/mol, the weekly trend's "Mean (mmol/L)") and both
    appendices, with a GMI goal in the A1C unit "both": no text overprints another."""
    rg = Regional("24", "DMY", 0, ",", a1c, paper)
    _pdf, _pages, reader, rec = _render(monkeypatch, _data(90, False, unit, rg, CUSTOM), True, False)
    _assert_fits(reader, rec, paper, unit)


def _assert_fits(reader, rec, paper, unit):
    """Every text inside the margins, and no two texts on the same line of a page overlap."""
    w, h = P.A4 if paper == "a4" else P.LETTER
    box = reader.pages[0].mediabox
    assert (round(float(box.width), 1), round(float(box.height), 1)) == (round(w, 1), round(h, 1))
    for page, left, right, y, s in rec.items:
        assert left >= P.M - 0.6 and right <= w - P.M + 0.6, (paper, page, s, left, right)
        assert 12 <= y <= h - 20, (page, s, y)
    # no two texts on the same line of a page overlap (tables, labels, the footer)
    by_line: dict = {}
    for page, left, right, y, s in rec.items:
        if s.strip():
            by_line.setdefault((page, round(y, 1)), []).append((left, right, s))
    for key, items in by_line.items():
        items.sort()
        for (_l1, r1, s1), (l2, _r2, s2) in itertools.pairwise(items):
            assert l2 >= r1 - 0.5, (paper, unit, key, s1, s2, r1, l2)


@pytest.mark.slow
@pytest.mark.parametrize("paper", ["letter", "a4"])
@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("color", [True, False])
def test_the_disclaimer_once_the_footer_line_and_the_license_section(monkeypatch, paper, full, color):
    rg = Regional(paper_size=paper)
    _, pages, reader, _rec = _render(monkeypatch, _data(14, full, "mg/dL", rg), color, full)
    everything = _text(reader)
    first = _text(reader, 0)
    # the disclaimer: once, in About This Report, never on page 1
    assert everything.count(C.DISCLAIMER_PLAIN) == 1 and C.DISCLAIMER_PLAIN not in first
    assert everything.index("About This Report") < everything.index(C.DISCLAIMER_PLAIN)
    for old in ("Do not rely", "In brief.", "Terms of Use", "reliable sources"):
        assert old not in everything, old
    # the last section: License (the license notice)
    last = _text(reader, pages - 1)
    assert (
        "License" in last
        and "Licensed under the GNU Affero General Public License, version 3 only (AGPL-3.0-only)." in last
    )
    assert "**" not in everything
    assert "GlucoCraft 0.0.2 · Copyright (C) 2026 The GlucoCraft authors" in last
    for notice in C.GNU_NOTICE:
        assert notice in last
    assert "License: https://www.gnu.org/licenses/agpl-3.0.html" in last
    # the one footer line on every page, with the page number
    for i in range(pages):
        t = _text(reader, i)
        assert P.footer_line(i + 1, pages) in t, i
        assert (
            P.footer_line(i + 1, pages)
            == f"GlucoCraft. For development and testing only. Not for medical use. · page {i + 1} of {pages}"
        )
    # About This Report: the method, not the texts of page 1
    for old in (
        "For information only",
        "Information only",
        "own risk.",
        "Disclaimer:",
        "Not for diagnosis, treatment or any medical decision",
        "No warranty · Not for treatment decisions",
        "a personal open-source project",
    ):
        assert old not in everything, old
    # the CGM Report: its one description sentence; never "AGP-style", and the IDC is not named
    assert (
        "This report: The CGM Report shows the standardized metrics and glucose profile recommended for the"
        in everything
    )
    assert "CGM Report: Continuous Glucose Monitoring" in first
    assert "GlucoCraft CGM Report — Continuous Glucose Monitoring" in first
    for old in ("AGP-style", "AGP-Style", "AGP-like", "International Diabetes Center", "AGP curve", "Abbott's"):
        assert old not in everything, old


@pytest.mark.slow
@pytest.mark.parametrize("paper", ["letter", "a4"])
@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("low_data", [False, True])
def test_footer_is_one_line_and_page_one_fits(monkeypatch, paper, unit, full, low_data):
    """One footer line on every page, below the footer rule and clear of the paper's edge; page 1
    (the 14-day summary with its AGP) still on one page, also with the note on less than 70% sensor active; the
    disclaimer's first sentence in bold, drawn on its own."""
    rg = Regional("24" if unit == "mmol/L" else "12", "DMY", 0, ",", "both", paper)
    agp = []
    orig = P.Report.agp_curve

    def spy(self, r, title, h=240, level=2, caption=()):
        out = orig(self, r, title, h, level, caption)
        agp.append((len(self.pages), h, title))  # the page the profile was drawn on
        return out

    monkeypatch.setattr(P.Report, "agp_curve", spy)
    data = _data(30, full, unit, rg)
    if low_data:
        data["standard"]["active_ok"] = False
    _, pages, _reader, rec = _render(monkeypatch, data, True, full)
    for i in range(pages):
        foot = [(y, s) for page, _, _, y, s in rec.items if page == i and y < P.FOOTER_RULE_Y]
        assert foot == [(P.FOOTER_Y, P.footer_line(i + 1, pages))], (i, foot)
        assert P.FOOTER_Y >= 20  # clear of the paper's edge (printers' unprintable margin)
    drawn = [s.strip() for _, _, _, _, s in rec.items]
    assert C.DISCLAIMER_LEAD in drawn  # the bold lead of About This Report's first item, drawn on its own
    first_agp = next(a for a in agp if "14 Days" in a[2])
    assert first_agp[0] == 1 and first_agp[1] >= 96  # the 14-day AGP on page 1, at least its minimum height


@pytest.mark.slow
def test_without_an_integration_version(monkeypatch):
    d = _data(14, False)
    d["integration_version"] = ""
    _, pages, reader, _ = _render(monkeypatch, d, True, False)
    assert "GlucoCraft · Copyright (C) 2026 The GlucoCraft authors" in _text(reader, pages - 1)


# ------------------------------------------------------------------------------------------------ decimals and units
@pytest.mark.slow
def test_decimal_comma_everywhere_in_the_pdf(monkeypatch):
    rg = Regional("24", "DMY", 0, ",", "both", "a4")
    _, _, _reader, rec = _render(monkeypatch, _data(30, True, "mmol/L", rg), True, True)
    drawn = " ".join(s for *_, s in rec.items)
    drawn = drawn.replace("GlucoCraft 0.0.2", "").replace("AGPL-3.0", "").replace("License v3.0", "")
    drawn = re.sub(r"\bversion \d+\.\d+", "", drawn)  # the version of a cited document
    drawn = re.sub(r"https?://\S+", "", drawn)
    # table and recommendation numbers of the cited sources ("Table 6.2", joined by a no-break space) are not decimals
    drawn = re.sub(r"(?:Table|Recommendation)\u00a0\d+\.\d+", "", drawn)
    assert not re.search(r"\d\.\d", drawn), re.findall(r".{20}\d\.\d.{10}", drawn)[:5]
    assert re.search(r"\d,\d", drawn)
    assert "3,9-10,0" in drawn or "3,9" in drawn
    assert "mmol/mol" in drawn


@pytest.mark.slow
def test_decimal_point_and_percent_only(monkeypatch):
    rg = Regional("12", "MDY", 6, ".", "percent", "letter")
    _, _, _reader, rec = _render(monkeypatch, _data(30, True, "mmol/L", rg), True, True)
    drawn = " ".join(s for *_, s in rec.items)
    assert re.search(r"\d\.\d", drawn) and "mmol/mol" not in drawn


@pytest.mark.slow
def test_a1c_mmol_mol_in_the_lab_table_and_metrics(monkeypatch):
    rg = Regional(a1c_unit="mmol_mol")
    _, _, _reader, rec = _render(monkeypatch, _data(30, False, "mg/dL", rg), True, False)
    drawn = [s.strip() for *_, s in rec.items]  # a bold lead is drawn with the space after it
    assert "54 mmol/mol" in drawn  # 7.1 % -> 10.929 x 4.95 = 54.1
    assert "48 mmol/mol" in drawn  # entered in mmol/mol: kept as entered
    assert any(s.endswith("mmol/mol") and s[0] in "+-" for s in drawn)  # the difference
    assert "A1C and GMI in mmol/mol:" in drawn


# ------------------------------------------------------------------------------------------------ weeks
@pytest.mark.slow
@pytest.mark.parametrize("first", [0, 6, 5])
def test_month_calendar_starts_on_the_first_day_of_the_week(monkeypatch, first):
    rg = Regional(first_weekday=first)
    _, _, _, rec = _render(monkeypatch, _data(30, True, "mg/dL", rg), True, True)
    names = set(rg.weekday_names())
    rows: dict = {}
    for page, left, _right, y, s in rec.items:
        if s in names:
            rows.setdefault((page, round(y, 1)), []).append((left, s))
    assert rows
    # each line holds whole weeks: two month calendars side by side, or the data quality page's day strip (one week)
    for items in rows.values():
        line = [s for _, s in sorted(items)]
        assert len(line) % 7 == 0, line
        for k in range(0, len(line), 7):
            assert line[k : k + 7] == rg.weekday_names()


@pytest.mark.parametrize("first,lead", [(0, 3), (6, 4), (5, 5)])
def test_report_data_keeps_the_weekday_of_the_first_day_and_the_calendar_leads_from_it(first, lead):
    data = R.build_report(READINGS, 300, LA, 14, True, [], now=NOW, regional=Regional(first_weekday=first))
    # the 14-day period's days of October, labeled with their first and last date
    (october,) = data["monthly"]
    assert october["month"] == "October 8 – October 21, 2026" and october["days"][0][0] == 8
    assert october["first_weekday"] == 3  # Thursday, 0 = Monday (the contract: not shifted)
    # the page and the PDF place the first day after the empty cells of the first day of the week in use
    assert Regional(first_weekday=first).lead_days(october["first_weekday"]) == lead and "lead_days" not in october


def test_daily_profiles_weekdays_and_lead():
    data = R.build_report(READINGS, 300, LA, 14, False, [], now=NOW, regional=Regional(first_weekday=0))
    st = data["standard"]
    assert (
        len(st["daily_weekdays"]) == 14
        and st["daily_weekdays"][0] == datetime.fromisoformat(st["period_start"]).weekday()
    )
    assert "daily_lead" not in st  # the page and the PDF lead from daily_weekdays and the first day of the week


# ------------------------------------------------------------------------------------------------ GMI / A1C data
def test_gmi_mmol_mol_in_the_report_data():
    data = R.build_report(READINGS, 300, LA, 30, False, LAB, now=NOW)
    for r in (data["standard"], data["period"]):
        assert r["gmi"] is not None and r["gmi_mmol_mol"] is not None
        assert abs(r["gmi_mmol_mol"] - 10.929 * (r["gmi"] - 2.15)) <= 1.1
    assert all("gmi_mmol_mol" in b for b in data["blocks"])
    labs = {x["date"]: x for x in data["lab"]["results"]}
    assert labs["2026-09-01"]["a1c_mmol_mol"] == 54
    assert labs["2026-06-10"]["a1c_mmol_mol"] == 48
    assert labs["2026-09-01"]["gmi_90d_mmol_mol"] is not None


def test_lab_entry_in_both_units():
    from glucocraft.report_service import lab_entry

    assert lab_entry("2026-10-01", 7.04) == {"date": "2026-10-01", "a1c": 7.0}
    assert lab_entry("2026-10-01", 7.05) == {"date": "2026-10-01", "a1c": 7.1}  # halves away from zero
    e = lab_entry("2026-10-01", 53, "mmol_mol")
    assert e["a1c_mmol_mol"] == 53 and e["a1c"] == 7.0  # 53 / 10.929 + 2.15 = 6.9995%, kept with two decimals
    assert lab_entry("2026-10-01", 52.5, "mmol_mol")["a1c_mmol_mol"] == 53
    for bad in (
        ("2026-10-01", 2.9, "percent"),
        ("2026-10-01", 20.1, "percent"),
        ("2026-10-01", 8.9, "mmol_mol"),
        ("2026-10-01", 196, "mmol_mol"),
        ("2026-10-01", 7, "pct"),
        ("1.10.2026", 7, "percent"),
    ):
        with pytest.raises(ValueError):
            lab_entry(*bad)
    # the range error names the unit in words, from the catalogue
    with pytest.raises(ValueError, match="^The A1C must be between 3 and 20 percent.$"):
        lab_entry("2026-10-01", 2.9, "percent")
    with pytest.raises(ValueError, match="^The A1C must be between 9 and 195 mmol/mol.$"):
        lab_entry("2026-10-01", 196, "mmol_mol")


def test_page_constants_restored_between_reports(monkeypatch):
    """A4 after Letter after A4: the module-level page size follows each report."""
    for paper, size in (("a4", P.A4), ("letter", P.LETTER), ("a4", P.A4)):
        P.Report(_data(14, False, rg=Regional(paper_size=paper)), True)
        assert (P.PAGE_W, P.PAGE_H) == size and P.TOP == size[1] - 58
        assert P.X(P.LAYOUT_W) == pytest.approx(size[0] - 2 * P.M)
