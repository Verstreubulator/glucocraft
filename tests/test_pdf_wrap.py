# License: AGPL-3.0-only (LICENSE).
"""report_pdf.py: a word wider than its column (a long URL or entity id) gets a line of its own without an empty line
before it, and a bold lead that is wider than the column is still drawn in bold."""

from __future__ import annotations

from datetime import datetime

import pytest
from glucocraft import i18n
from glucocraft import report as R
from glucocraft import report_service as RS
from glucocraft.regional import Regional
from glucocraft.units import report_threshold_texts

from conftest import LA

reportlab = pytest.importorskip("reportlab")
from glucocraft import report_pdf as P

NOW_LOCAL = datetime(2026, 10, 22, 9, 0, tzinfo=LA)
NOW = int(NOW_LOCAL.timestamp())
LONG_WORD = "https://example.org/a/very/long/address/that/does/not/fit/in/one/column"
LONG_LEAD = "Supercalifragilisticexpialidocious:"


@pytest.fixture(scope="module")
def report() -> P.Report:
    """A Report of a 14-day period without readings, with the keys report_service adds for the PDF."""
    rg = Regional()
    d = R.build_report([], 300, LA, 14, False, [], now=NOW)
    d.update(
        {
            "label": i18n.t("report.period.14"),
            "sources": ["Test source"],
            "created": rg.stamp_year(NOW_LOCAL),
            "created_ts": NOW // 60 * 60,
            "unit": "mg/dL",
            "thresholds": report_threshold_texts("mg/dL"),
            **rg.as_dict(),
            "integration_version": "0.0.0",
            "ifcc": {"factor": 10.929, "offset": 2.15},
            "license": RS.license_texts(),
            "lab_limits": {"percent": [3.0, 20.0], "mmol_mol": [9.0, 195.0]},
        }
    )
    return P.Report(d, True)


def test_an_over_wide_first_word_gets_no_empty_line_before_it(report):
    width = 60
    assert report.sw(LONG_WORD, P.FONT, 8.5) > width
    assert report.wrap(LONG_WORD, 8.5, width) == [LONG_WORD]
    assert report.wrap(LONG_WORD + " and more", 8.5, width) == [LONG_WORD, "and more"]
    # a word that is not the first still starts a new line, as before
    assert report.wrap("a " + LONG_WORD, 8.5, width) == ["a", LONG_WORD]
    assert "" not in report.wrap(f"{LONG_WORD} {LONG_WORD}", 8.5, width)


def test_a_bold_lead_wider_than_the_column_stays_bold(report, monkeypatch):
    drawn: list[tuple[str, bool]] = []
    monkeypatch.setattr(P.Report, "text", lambda self, x, y, s, size=9, bold=False, *a, **k: drawn.append((s, bold)))
    # the structure calls are not under test here: run the recorded drawing functions without the tag tree
    monkeypatch.setattr(report.tg, "open", lambda *a, **k: None)
    monkeypatch.setattr(report.tg, "close", lambda *a, **k: None)
    report.new_page()
    report.para("the rest", width=60, bold_lead=LONG_LEAD)
    for fn in report.cur:
        fn()
    assert drawn[0] == (LONG_LEAD, True)
    assert ("", False) not in drawn and all(s for s, _ in drawn)
    assert ("the rest", False) in drawn
