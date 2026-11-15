# License: AGPL-3.0-only (LICENSE).
"""Render the CGM Report as a PDF, and send a PDF to a network printer over IPP.

Page 1 is always the 14-day summary; a longer period adds the longer-term overview after it. The report
carries no name or date of birth, names the glucose sources by their names (every page header and About This Report),
shows cited consensus targets only, and uses formal wording without evaluative words (docs/LANGUAGE_STYLE.md).
Every section opens with one caption line (its rule and citation), shows its data and ends with its Notes, the full
cited explanation (Report.notes); About This Report keeps the general method.

render(data, color=True, full=False) -> (pdf_bytes, page_count)
    data = report.build_report(...) plus "label" (e.g. "30 Days"), "sources" (the glucose sources' names, the
    primary first), "second_source_timed" (whether the second source carries measurement times) and "created" (text),
    and the regional settings (regional.Regional.as_dict: time and date formats, first day of the week, decimal
    separator, A1C / GMI unit, paper size "letter" / "a4").
ipp_print(target, pdf, job_name, color=True, two_sided=True, timeout=90) -> (ok, message)
    sends the PDF to a network printer over IPP (Print-Job, application/pdf).

The settings of the render in progress are module state, set in one place (_begin_render, which Report() calls): the
paper size (PAGE_W, PAGE_H, TOP), the display unit (_U), the regional settings (_RG), the fonts (FONT, BOLD) and the
document language (PDF_LANGUAGE); the helper functions below read them. render() holds RENDER_LOCK for the whole
render, so that renders run one at a time whoever calls them (they run in Home Assistant's executor threads).
report.py keeps the regional settings of a report being built the same way (report._BUILD_LOCK).

reportlab is imported inside the functions, so GlucoCraft still loads if the package is unavailable.

Every text of the report is in the language catalogue (i18n/<language>/report.json, keys report.*; the Report page
reads the same file). The cited-context texts are kept word for word there; citations and the reference
list (REFERENCES) are not translated, and the disclaimer is the canonical English text (const.py). The fonts are
chosen per script (FONTS below) and embedded.

The PDF is tagged (pdf_tags.py): it carries the document language and title, a structure tree in reading order
(headings, paragraphs, lists, tables with their header cells, figures with an alternative text) and marks its
decoration (rules, frames, backgrounds, the page header and footer) as artifacts. The alternative text of a chart
states its data in a sentence (report.alt.*, the words of the Report page's chart descriptions), with every unit
written out as a screen reader reads it (spoken.py). A text with a comparison sign before a number ("<4%") carries
its words as its /ActualText ("less than 4%"), and the headings H1 and H2 are the document's outline (bookmarks).
"""

from __future__ import annotations

import http.client as http_client
import re
import ssl
import struct
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager, nullcontext
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import NamedTuple

from .const import (
    AGP_RANGES,
    BAND_COLORS,
    CONSENSUS_TARGET_VALUES,
    COPYRIGHT,
    DISCLAIMER_LEAD,
    DISCLAIMER_LINE,
    DISCLAIMER_PLAIN,
    GNU_NOTICE,
    GOAL_OPS,
    LICENSE_NAME,
    LICENSE_URL,
    sensor_active_label,
    sensor_active_note,
)
from .i18n import language, t, tp
from .limits import limit_minutes
from .regional import (
    A1C_BOTH,
    A1C_MMOL_MOL,
    A1C_MMOL_PER_PCT,
    A1C_PCT_OFFSET,
    PAPER_A4,
    Regional,
    a1c_to_mmol_mol,
)
from .report import BLOCK_DAYS, MINUTES_PER_PERCENT, STANDARD_DAYS
from .report import GOAL_ROWS as R_GOAL_ROWS
from .spoken import SPOKEN_UNITS, speak, speak_comparisons, speak_glucose, speak_limit, speak_number
from .units import (
    MGDL_PER_MMOLL,
    REPORT_THRESHOLDS,
    UNIT_MGDL,
    UNIT_MMOLL,
    fmt,
    from_mgdl,
    report_threshold_texts,
    round_half_up,
)

# The time-in-ranges colors of the published report examples. They are the defaults of the graph range colors
# (const.BAND_COLORS, one table for both); the CGM Report always uses these defaults, whatever the settings choose for
# the displays' graphs.
AGP = {
    "vhigh": BAND_COLORS["very_high"],
    "high": BAND_COLORS["high"],
    "target": BAND_COLORS["near_low"],  # in range, outside the tight range
    "tight": BAND_COLORS["tight_range"],
    "low": BAND_COLORS["low"],
    "vlow": BAND_COLORS["very_low"],
}
GRAY = {"vhigh": 0.30, "high": 0.58, "target": 0.70, "tight": 0.84, "low": 0.42, "vlow": 0.12}
HATCHED = ("high", "vhigh")
# The consensus thresholds of the charts and the zones (mg/dL), from the one table of the consensus ranges
# (const.AGP_RANGES; Battelino et al., 2019): 54, 70, 180 and 250 mg/dL. The Report page receives the same table with
# the report data (report.build_report "zones").
VLOW, LOW, HIGH, VHIGH = (
    AGP_RANGES[k] for k in ("range_critical_low", "range_low", "range_high", "range_critical_high")
)
# The bands of the charts, for clipping the glucose profile into its zone colors (the boundaries are lines there)
ZONES = [(-1e9, VLOW, "vlow"), (VLOW, LOW, "low"), (LOW, HIGH, "target"), (HIGH, VHIGH, "high"), (VHIGH, 1e9, "vhigh")]


def zone_key(mg: float) -> str:
    """Return the zone of a value (mg/dL) as every statistic counts it (statistics.compute_period_stats): very low
    below 54, low 54 to below 70, in range 70 to 180 inclusive, high above 180 to 250 inclusive, very high above 250
    (Battelino et al., 2019). The Report page has the same rule (rZone)."""
    return (
        "vlow" if mg < VLOW else "low" if mg < LOW else "target" if mg <= HIGH else "high" if mg <= VHIGH else "vhigh"
    )


# Paper size: US Letter or A4, from the settings (Automatic: Letter in the Letter countries, A4
# elsewhere). The layout was drawn for the Letter content width (532 points); on A4 (515 points wide, 50 points taller)
# the horizontal positions scale with X() and the pages flow into the extra height. Set by _begin_render.
LETTER = (612.0, 792.0)
A4 = (595.2756, 841.8898)  # 210 x 297 mm
PAGE_W, PAGE_H = LETTER  # points
M = 40
TOP_MARGIN = 58  # from the top edge of the paper to the top of the content (below the page header)
TOP = PAGE_H - TOP_MARGIN
BOTTOM = 58  # above the footer rule and its one line
FOOTER_RULE_Y = 50
FOOTER_Y = 38
LAYOUT_W = LETTER[0] - 2 * M  # the content width the positions below were drawn for

# Page 1's layout (points; a y offset is measured down from the top of its block; an x position marked "X()" is drawn
# for Letter and passes through X())
P1_HEADER_H = 78  # the header block: the title, the period, sensor active and the days with data
# the note of a period without enough data under them: its first line below the block top, the line step, the width
P1_NOTE_TOP, P1_NOTE_LEAD, P1_NOTE_W = 76, 10, 330
KEY_W, KEY_COL_W, KEY_ROW_H = 190, 96, 10  # the range key at the top right: its width, column width and row step
TIR_NOTE_W = 360  # the width of the time-in-ranges note
TIR_BAR_FOOT = 220  # the foot of the time-in-ranges bar below the block top (lower by the note's extra lines)
TIR_BAR_W, TIR_BAR_H = 38, 180  # the bar's size
TIR_MIN_BAND = 3  # the smallest share (%) a band of the bar is drawn with, so that a thin band stays visible
# the four summary values beside the bar: their column (X()), the row step and the label's offset from the value
TIR_SUMMARY_X, TIR_SUMMARY_ROW_H, TIR_SUMMARY_LABEL_DX = 196, 44, 52
METRICS_X, METRICS_TOP = 372, 40  # the glucose metrics column: its left edge (X()) and its first row below the top


def _set_paper(paper: str) -> None:
    """Set the page size of the render in progress (Letter, or A4 for PAPER_A4)."""
    global PAGE_W, PAGE_H, TOP
    PAGE_W, PAGE_H = A4 if paper == PAPER_A4 else LETTER
    TOP = PAGE_H - TOP_MARGIN


def X(dx: float) -> float:
    """Return a horizontal position drawn for Letter, on the paper in use (A4: 3 % narrower)."""
    return dx * (PAGE_W - 2 * M) / LAYOUT_W


class FontSet(NamedTuple):
    """The TrueType files (in fonts/) of one script's regular, bold and italic faces. Each face is registered with
    ReportLab under its file name without the extension, and the three as one family, so that the italics of the
    reference list (<i> in a Paragraph) use the italic face."""

    regular: str
    bold: str
    italic: str


# The fonts, one entry per script. A tagged PDF embeds every font it uses (PDF/UA-1 forbids the standard fonts that a
# reader supplies), so each script has TrueType files of its own. The Latin script uses Liberation Sans 2.1.5 (SIL
# Open Font License 1.1, fonts/LICENSE-LiberationSans.txt): it has the widths of Arial, which are those of Helvetica,
# the font of the earlier PDF, for all but five of the Latin-1 characters (¯ ± µ · ÷), so the lines break where they
# did; and it has every letter of the European languages written in the Latin script, and the symbols the report
# uses. A language written in another script (Greek, Cyrillic, Arabic, Hebrew, Chinese, Japanese, Korean, ...) needs a
# font with its glyphs: put its .ttf files into fonts/ (with its license), add an entry such as
#     "cyrillic": FontSet("NotoSans-Regular.ttf", "NotoSans-Bold.ttf", "NotoSans-Italic.ttf"),
# and name the language's script in SCRIPTS ("ru": "cyrillic"). The files are registered the first time a report in
# that script is made, and the PDF embeds the subset of the glyphs it uses.
FONTS: dict[str, FontSet] = {
    "latin": FontSet("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf"),
}
SCRIPTS: dict[str, str] = {}  # language (base code) -> script; every language not listed here is "latin"
FONT_DIR = Path(__file__).parent / "fonts"
# the regular and the bold font of the render in progress (_begin_render sets them)
FONT, BOLD = (Path(f).stem for f in FONTS["latin"][:2])
PDF_LANGUAGE = "en"  # the document language of the render in progress (the catalog's /Lang)


def _use_fonts(lang: str) -> None:
    """Set the fonts of the render in progress for a language's script, registering its font files once."""
    global FONT, BOLD
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    fonts = FONTS[SCRIPTS.get(lang.split("-")[0], "latin")]
    names = [Path(file).stem for file in fonts]
    for name, file in zip(names, fonts, strict=True):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / file)))
    regular, bold, italic = names
    # the family: <b> and <i> in a Paragraph (the report sets no bold italic text; it maps to the bold face)
    for is_bold, is_italic, face in ((0, 0, regular), (1, 0, bold), (0, 1, italic), (1, 1, bold)):
        addMapping(regular, is_bold, is_italic, face)
    FONT, BOLD = regular, bold


# The smallest text size of the PDF (points), for every label, note, axis label and table cell
MIN_PT = 8.0
# How far a wrapped line may exceed its width (points). The widths of Liberation Sans are rounded to a 2048-unit
# design grid, those of Helvetica to a 1000-unit one, so the same text can measure a few hundredths of a point wider;
# without this allowance, a line that just fit in the earlier Helvetica PDF would break one word earlier. 0.05 pt is
# far below what the eye can see at the report's text sizes.
WRAP_TOLERANCE = 0.05


# The disclaimer stands once, in full, in About This Report; every page footer is one line (footer_line); the last
# section is "License" (the license notice).
def footer_line(page: int, total: int) -> str:
    """Return the one footer line of every page.

    For example "GlucoCraft. For development and testing only. Not for medical use. · page 1 of 3".
    """
    return t("report.footer", disclaimer=DISCLAIMER_LINE, page=page, total=total)


def source_line(d: dict) -> str:
    """Return the glucose sources as the page header names them: "Glucose data: <primary>." or "Glucose data:
    <primary>; second source: <second>." Each source by its name (the entity's name, else its entity id), the primary
    first (report_service)."""
    src = d.get("sources") or ["-"]  # every report of report_service names its sources
    if len(src) > 1:
        return t("report.header.data_two", primary=src[0], second=src[1])
    return t("report.header.data", primary=src[0])


def data_sources(d: dict) -> tuple[str, str]:
    """Return About This Report's item on the glucose sources as (lead, text), so that their names stand once in the
    report's text (the page headers are artifacts): "Data source." with the one source's name, or "Data sources." with
    both names, how the second source fills gaps (and, for a second source without measurement times, when it is
    used: d["second_source_timed"] False) and the share of the period's readings it delivered (second_source_pct,
    one decimal, rounded once)."""
    src = d.get("sources") or ["-"]
    if len(src) < 2:
        return t("report.about.source.lead"), t("report.about.source.body", name=src[0])
    untimed = d.get("second_source_timed") is False
    text = t(
        "report.about.sources.body_untimed" if untimed else "report.about.sources.body", primary=src[0], second=src[1]
    )
    share = d.get("second_source_pct")
    if share is not None:
        text += " " + t("report.about.sources.share", share=_f(share))
    return t("report.about.sources.lead"), text


def report_description() -> str:
    """Return the one description of the report (the PDF's subject; the term "AGP" appears only here and in
    citations)."""
    return t("report.description")


def report_scope() -> str:
    """Return About This Report's first item: the description, what this report is not, and where its values come
    from (not on page 1; the Report page shows the details after its own description)."""
    return t("report.scope", description=report_description(), details=t("report.about.report.body"))


# ---------------------------------------------------------------------- cited context
# The cited-context texts shown in the CGM Report, word for word (APA 7 with locators;
# docs/LANGUAGE_STYLE.md): report.context.<name>. Table and recommendation numbers are joined to their word with a
# no-break space (NB, written   in the catalogue), so that a line never breaks inside them and the decimal
# separator setting leaves them unchanged (_dx). In a report in mmol/L the paired thresholds are written with mmol/L
# first (_both); both units stay as published. The ADA citations are placeholders ({ada_a}, {ada_b}).
NB = " "
ADA_A = "American Diabetes Association Professional Practice Committee for Diabetes, 2026a"
ADA_B = "American Diabetes Association Professional Practice Committee for Diabetes, 2026b"


def ctx(name: str, **params) -> str:
    """Return a cited-context text (report.context.<name>) with its citations filled in, as written in the catalogue
    (_ctx sets it in the report's unit and decimal separator)."""
    return t(f"report.context.{name}", ada_a=ADA_A, ada_b=ADA_B, **params)


def ctx_night(night: str, day: str) -> str:
    """Return the cited context of the night and day periods, with GlucoCraft's two columns in the time format."""
    return ctx("night", night=night, day=day)


# The steps of the research appendix's method (report.research.method.<n>)
RESEARCH_METHOD_STEPS = 6
# The columns of the daily summary: (key of the header, x as a share of the column width, alignment)
DAY_TABLE_COLUMNS = (("day", 0, "left"), ("mean", 0.55, "right"), ("events", 0.75, "right"), ("data", 0.95, "right"))
# The reference list of the report (APA 7; every in-text citation has an entry): the works cited in the PDF, in APA
# order (by the author list, then the year), with the entry texts of Documentation > References
# (www/glucocraft-docs.js, REFS; tests/test_references.py keeps the two identical). hoelzel2004 is cited only when
# the A1C unit includes mmol/mol.
REFERENCES = (
    (
        "abbottSoftware",
        (
            "Abbott Diabetes Care. (2018). <i>FreeStyle Libre software user's manual</i> (Software version "
            "1.0; ART39660-001 rev. A). https://freestyleserver.com/Payloads/IFU/2018/ART39660-001_rev-A-Web.pdf"
        ),
    ),
    (
        "ada2026",
        (
            "American Diabetes Association Professional Practice Committee for Diabetes. (2026a). 6. Glycemic goals, "
            "hypoglycemia, and hyperglycemic crises: Standards of Care in Diabetes—2026. <i>Diabetes Care, 49</i>"
            "(Suppl. 1), S132–S149. https://doi.org/10.2337/dc26-S006"
        ),
    ),
    (
        "ada2026b",
        (
            "American Diabetes Association Professional Practice Committee for Diabetes. (2026b). 7. Diabetes "
            "technology: Standards of Care in Diabetes—2026. <i>Diabetes Care, 49</i>(Suppl. 1), S150–S165. "
            "https://doi.org/10.2337/dc26-S007"
        ),
    ),
    (
        "battelino2023",
        (
            "Battelino, T., Alexander, C. M., Amiel, S. A., Arreaza-Rubin, G., Beck, R. W., Bergenstal, R. M., "
            "Buckingham, B. A., Carroll, J., Ceriello, A., Chow, E., Choudhary, P., Close, K., Danne, T., "
            "Dutta, S., Gabbay, R., Garg, S., Heverly, J., Hirsch, I. B., Kader, T., . . . Phillip, M. (2023). "
            "Continuous glucose monitoring and metrics for clinical trials: An international consensus "
            "statement. <i>The Lancet Diabetes &amp; Endocrinology, 11</i>(1), 42–57. "
            "https://doi.org/10.1016/S2213-8587(22)00319-9"
        ),
    ),
    (
        "battelino2019",
        (
            "Battelino, T., Danne, T., Bergenstal, R. M., Amiel, S. A., Beck, R., Biester, T., Bosi, E., "
            "Buckingham, B. A., Cefalu, W. T., Close, K. L., Cobelli, C., Dassau, E., DeVries, J. H., Donaghue, "
            "K. C., Dovc, K., Doyle, F. J., III, Garg, S., Grunberger, G., Heller, S., . . . Phillip, M. (2019). "
            "Clinical targets for continuous glucose monitoring data interpretation: Recommendations from the "
            "International Consensus on Time in Range. <i>Diabetes Care, 42</i>(8), 1593–1603. "
            "https://doi.org/10.2337/dci19-0028"
        ),
    ),
    (
        "bergenstal2013",
        (
            "Bergenstal, R. M., Ahmann, A. J., Bailey, T., Beck, R. W., Bissen, J., Buckingham, B., Deeb, L., "
            "Dolin, R. H., Garg, S. K., Goland, R., Hirsch, I. B., Klonoff, D. C., Kruger, D. F., Matfin, G., "
            "Mazze, R. S., Olson, B. A., Parkin, C., Peters, A., Powers, M. A., . . . Wesley, D. M. (2013). "
            "Recommendations for standardizing glucose reporting and analysis to optimize clinical decision "
            "making in diabetes: The Ambulatory Glucose Profile (AGP). <i>Diabetes Technology &amp; "
            "Therapeutics, 15</i>(3), 198–211. https://doi.org/10.1089/dia.2013.0051"
        ),
    ),
    (
        "bergenstal2018",
        (
            "Bergenstal, R. M., Beck, R. W., Close, K. L., Grunberger, G., Sacks, D. B., Kowalski, A., Brown, "
            "A. S., Heinemann, L., Aleppo, G., Ryan, D. B., Riddlesworth, T. D., &amp; Cefalu, W. T. (2018). "
            "Glucose management indicator (GMI): A new term for estimating A1C from continuous glucose "
            "monitoring. <i>Diabetes Care, 41</i>(11), 2275–2280. https://doi.org/10.2337/dc18-1581"
        ),
    ),
    (
        "cichosz2023",
        (
            "Cichosz, S. L., Jensen, M. H., &amp; Hejlesen, O. (2023). Optimal data collection period for "
            "continuous glucose monitoring to assess long-term glycemic control: Revisited. <i>Journal of "
            "Diabetes Science and Technology, 17</i>(3), 690–695. https://doi.org/10.1177/19322968211069177"
        ),
    ),
    (
        "danne2017",
        (
            "Danne, T., Nimri, R., Battelino, T., Bergenstal, R. M., Close, K. L., DeVries, J. H., Garg, S., "
            "Heinemann, L., Hirsch, I., Amiel, S. A., Beck, R., Bosi, E., Buckingham, B., Cobelli, C., Dassau, E., "
            "Doyle, F. J., III, Heller, S., Hovorka, R., Jia, W., . . . Phillip, M. (2017). International consensus "
            "on use of continuous glucose monitoring. <i>Diabetes Care, 40</i>(12), 1631–1640. "
            "https://doi.org/10.2337/dc17-1600"
        ),
    ),
    (
        "hoelzel2004",
        (
            "Hoelzel, W., Weykamp, C., Jeppsson, J.-O., Miedema, K., Barr, J. R., Goodall, I., Hoshino, T., John, "
            "W. G., Kobold, U., Little, R., Mosca, A., Mauri, P., Paroni, R., Susanto, F., Takei, I., Thienpont, "
            "L., Umemoto, M., Wiedmeyer, H.-M., &amp; IFCC Working Group on HbA1c Standardization. (2004). IFCC "
            "reference system for measurement of hemoglobin A1c in human blood and the national standardization "
            "schemes in the United States, Japan, and Sweden: A method-comparison study. <i>Clinical Chemistry, "
            "50</i>(1), 166–174. https://doi.org/10.1373/clinchem.2003.024802"
        ),
    ),
    (
        "klonoff2023",
        (
            "Klonoff, D. C., Wang, J., Rodbard, D., Kohn, M. A., Li, C., Liepmann, D., Kerr, D., Ahn, D., Peters, "
            "A. L., Umpierrez, G. E., Seley, J. J., Xu, N. Y., Nguyen, K. T., Simonson, G., Agus, M. S. D., "
            "Al-Sofiani, M. E., Armaiz-Pena, G., Bailey, T. S., Basu, A., . . . Kovatchev, B. (2023). A Glycemia "
            "Risk Index (GRI) of hypoglycemia and hyperglycemia for continuous glucose monitoring validated by "
            "clinician ratings. <i>Journal of Diabetes Science and Technology, 17</i>(5), 1226–1242. "
            "https://doi.org/10.1177/19322968221085273"
        ),
    ),
    (
        "monnier2017",
        (
            "Monnier, L., Colette, C., Wojtusciszyn, A., Dejager, S., Renard, E., Molinari, N., &amp; Owens, "
            "D. R. (2017). Toward defining the threshold between low and high glucose variability in diabetes. "
            "<i>Diabetes Care, 40</i>(7), 832–838. https://doi.org/10.2337/dc16-1769"
        ),
    ),
    (
        "riddlesworth2018",
        (
            "Riddlesworth, T. D., Beck, R. W., Gal, R. L., Connor, C. G., Bergenstal, R. M., Lee, S., "
            "&amp; Willi, S. M. (2018). Optimal sampling duration for continuous glucose monitoring to "
            "determine long-term glycemic control. <i>Diabetes Technology &amp; Therapeutics, 20</i>(4), "
            "314–316. https://doi.org/10.1089/dia.2017.0455"
        ),
    ),
)


def _fit(mn, mx, min_spread=20.0):
    """Return a chart's value range around [mn, mx]: at least min_spread wide, the data in its middle 70 %."""
    if mx - mn < min_spread:
        c = (mn + mx) / 2.0
        mn, mx = c - min_spread / 2.0, c + min_spread / 2.0
    m = (mx - mn) * 0.15 / 0.70
    return mn - m, mx + m


def _step(lo, hi, n=5):
    """Return the grid step (mg/dL) that gives about n grid lines between lo and hi."""
    raw = (hi - lo) / n
    for s in (5, 10, 20, 25, 50, 100):
        if s >= raw:
            return s
    return 100


def _date(s, short=False):
    """Return a report date in the date format in use: "Wed 10/22/2026" / "Wed 22/10/2026"; short: "10/22"."""
    try:
        d = datetime.strptime(s, "%Y-%m-%d")
    except (TypeError, ValueError):
        return "-"
    return _RG["rg"].numeric(d) if short else _RG["rg"].weekday_numeric(d, True)


# the regional settings of the render in progress (time and date formats, decimal separator, A1C unit, paper)
_RG = {"rg": Regional()}


def _hours() -> list[str]:
    """Return the time axis labels: every 3 hours, midnight to midnight."""
    return [_RG["rg"].hour(h % 24) for h in range(0, 25, 3)]


def _night(day: bool = False) -> str:
    """Return the night (00:00–05:59) or the day (06:00–23:59) in the time format in use; one form everywhere."""
    rg = _RG["rg"]
    a, b = ((6, 0), (23, 59)) if day else ((0, 0), (5, 59))
    return t("report.time_range", start=rg.clock(datetime(2000, 1, 1, *a)), end=rg.clock(datetime(2000, 1, 1, *b)))


def _night_day_head() -> tuple[str, ...]:
    """Return the head row of the night-and-day tables: the unit of the thresholds in the row labels, the night, the
    day, the whole period, the consensus target."""
    return (
        t("report.table.thresholds", unit=_unit()),
        t("report.table.night", time=_night()),
        t("report.table.day", time=_night(True)),
        t("report.table.whole_period"),
        t("report.table.consensus_target"),
    )


def _f(v, unit="%"):
    """Return a value formatted as :g with the decimal separator in use, plus its unit; "-" for None."""
    return "-" if v is None else f"{_RG['rg'].g(v)}{unit}"


def _d(text: str) -> str:
    """Return a number written in a fixed text ("14.4") with the decimal separator in use."""
    return _RG["rg"].dec(text)


# The minutes per day of 1% of the time, as the time-in-ranges notes write it ("14.4"; report.MINUTES_PER_PERCENT)
MINUTES_PER_PERCENT_TEXT = f"{MINUTES_PER_PERCENT:g}"


def _target(key: str) -> str:
    """Return a consensus target as the tables write it, from the one table of the targets (const.GOAL_OPS and
    CONSENSUS_TARGET_VALUES; Battelino et al., 2019): "time_low" -> "<4%", "time_in_range" -> ">70%"."""
    return f"{GOAL_OPS[key]}{CONSENSUS_TARGET_VALUES[key]}%"


def _gmi(v, mm=None) -> str:
    """Return GMI / A1C (given in percent) in the A1C / GMI unit in use.

    mm: the value in mmol/mol when known (GMI from the mean glucose, Bergenstal et al., 2018).
    """
    return "-" if v is None else _RG["rg"].a1c(v, mm)


def _mm_diff(a, b):
    """Return the difference of two values in mmol/mol (whole numbers), or None when one is missing."""
    return None if a is None or b is None else round_half_up(a) - round_half_up(b)


def _gmi_main(v, mm=None) -> str:
    """Return GMI / A1C in the main unit only, with its unit: "7.0%" or "53 mmol/mol" (both units: percent)."""
    return "-" if v is None else _RG["rg"].a1c_parts(v, mm)[0]


def _gmi_short(v, mm=None) -> str:
    """Return GMI for a narrow table cell: the number of the main unit only, "6.2%" or "44" (both units: percent);
    the table's note names the unit (report.blocks.gmi_mmol_mol / gmi_both)."""
    if v is None:
        return "-"
    rg = _RG["rg"]
    if rg.a1c_unit == A1C_MMOL_MOL:
        return rg.num(a1c_to_mmol_mol(v) if mm is None else mm, 0)  # as a1c_parts writes it, without the unit
    return rg.a1c_parts(v, mm)[0]


def _signed(v: float, decimals: int) -> str:
    """Return a difference with its sign and `decimals` decimals ("+1.5", "-3"); zero has no sign ("0.0", "0"), as on
    the Report page."""
    r = round_half_up(v, decimals)
    return _RG["rg"].num(r, decimals, sign=r != 0)


def _places(v: float) -> int:
    """Return the decimals of a value of the report data as it is shown (:g): 65.4 -> 1, 69.97 -> 2, 70.0 -> 0."""
    return len(f"{float(v):g}".partition(".")[2])


def _a1c_diff(diff_pct, diff_mmol_mol=None) -> str:
    """Return a difference of two A1C / GMI values in the A1C / GMI unit in use, with no sign for zero: "+0.3
    percentage points", "0 mmol/mol", "-0.5 percentage points (-5 mmol/mol)".

    diff_mmol_mol: the difference of the two values as shown in mmol/mol, when both are known; otherwise the master
    equation's 10.929 mmol/mol per percentage point (its offset cancels in a difference)."""
    if diff_pct is None:
        return "-"
    rg = _RG["rg"]
    p_txt = t("regional.percentage_points", value=_signed(diff_pct, 1))
    m_txt = f"{_signed(A1C_MMOL_PER_PCT * diff_pct if diff_mmol_mol is None else diff_mmol_mol, 0)} mmol/mol"
    if rg.a1c_unit == A1C_MMOL_MOL:
        return m_txt
    return f"{p_txt} ({m_txt})" if rg.a1c_unit == A1C_BOTH else p_txt


# Glucose in the display unit (data["unit"]): the data stay mg/dL; mg/dL whole numbers, mmol/L one decimal, the
# consensus thresholds as published (units.py)
_U = {"mmol": False}


def _mm() -> bool:
    """Return True when the report is in mmol/L."""
    return _U["mmol"]


def _unit() -> str:
    """Return the display unit of the report."""
    return UNIT_MMOLL if _U["mmol"] else UNIT_MGDL


# One render at a time: the settings above are module state (the module docstring), so render() holds this lock while
# it sets and reads them. A plain lock: render() never renders again from inside a render.
RENDER_LOCK = threading.Lock()


def _begin_render(data: dict) -> None:
    """Set the settings of the render in progress from the report data: the display unit, the regional settings
    (formats, week start, decimal separator, A1C unit, paper size), the paper size, the document language (the
    catalogue's language, i18n.py) and the fonts of its script. The one place where they are set."""
    global PDF_LANGUAGE
    _U["mmol"] = data.get("unit") == UNIT_MMOLL
    _RG["rg"] = Regional.from_dict(data)
    _set_paper(_RG["rg"].paper)
    PDF_LANGUAGE = language()
    _use_fonts(PDF_LANGUAGE)


def _g(mg) -> str:
    """Return a glucose value (mg/dL) in the display unit, without the unit."""
    return "-" if mg is None else _d(fmt(mg, _unit()))


def _gu(mg) -> str:
    """Return a glucose value (mg/dL) in the display unit, with the unit."""
    return "-" if mg is None else f"{_d(fmt(mg, _unit()))} {_unit()}"


def _limit_parts(n: dict, seconds: dict) -> list[str]:
    """The readings at the sensor's limit with their time: ["LO 1 (5 minutes)", ...], those with a count only."""
    return [
        t(
            "report.limit_part",
            limit=t(f"report.limit.{k}"),
            count=int(n.get(k) or 0),
            duration=_dur_words(limit_minutes(float(seconds.get(k) or 0))),
        )
        for k in ("low", "high")
        if n.get(k)
    ]


def _limit_note(r: dict) -> str:
    """Return the note under the statistics when the period has readings at the sensor's limit, else "".

    For example "2 readings at the sensor's limit (LO 1 (5 minutes); HI 1 (5 minutes)), left out of ...".
    """
    n = r.get("limit_readings") or {}
    parts = _limit_parts(n, r.get("limit_seconds") or {})
    if not parts:
        return ""
    return tp(
        "report.limit_note",
        int(n.get("low") or 0) + int(n.get("high") or 0),
        parts=t("report.list_separator").join(parts),
    )


def _legend(*keys: str) -> str:
    """Return the legend of a table with short column heads: one item per column (report.legend.<key>), in the order
    of the columns, in the display unit. The Report page builds the same legends from the same items."""
    params = {"unit": _unit(), "sensor_active": t("report.sensor_active.in_sentence")}
    items = t("report.list_separator").join(t(f"report.legend.{key}", **params) for key in keys)
    return _L(t("report.legend.text", items=items))


# a threshold placeholder of the report's fixed texts ({t70}, {t70_180}; units.REPORT_THRESHOLDS) or {unit}
THRESHOLD_PLACEHOLDER = re.compile(r"\{(" + "|".join([*REPORT_THRESHOLDS, "unit"]) + r")\}")


def _L(text: str) -> str:
    """Return a fixed text with its threshold placeholders ({t70}, {t70_180}, ...) and {unit} filled in the display
    unit, from the one table of the report page and the PDF (units.report_threshold_texts), the numbers with the
    decimal separator in use. Every other number of the text is left as written."""
    values = report_threshold_texts(_unit())
    return THRESHOLD_PLACEHOLDER.sub(lambda m: _d(values[m[1]]) if m[1] in values else m[0], text)


_PAIRED = re.compile(r"(\d+(?:–\d+)?) mg/dL \((\d+(?:\.\d)?(?:–\d+(?:\.\d)?)?) mmol/L\)")
_DEC = re.compile(r"(\d)\.(\d)")


def _dx(text: str) -> str:
    """Return a sentence with numbers in it, with the decimal separator in use: "13.9" -> "13,9".

    A number joined to its word by a no-break space ("Table 6.2", "Recommendation 7.9", in any language) and "et al."
    are left unchanged.
    """
    if _RG["rg"].decimal != ",":
        return text
    keep = re.sub(rf"(\w+{NB}\d+)\.(\d)", "\\1\x01\\2", text)
    return _DEC.sub(r"\1,\2", keep).replace("\x01", ".")


def _both(text: str) -> str:
    """Return a cited text with paired thresholds ("180 mg/dL (10.0 mmol/L)") with the display unit first.

    In a report in mmol/L that is "10.0 mmol/L (180 mg/dL)"; both units stay as published (docs/LANGUAGE_STYLE.md).
    """
    return _PAIRED.sub(r"\2 mmol/L (\1 mg/dL)", text) if _U["mmol"] else text


def _ctx(text: str) -> str:
    """Return a cited-context text as shown in this report: the display unit first, the decimal separator in use."""
    return _dx(_both(text))


def _scatter_caption() -> str:
    """Return the explanation of the hypoglycemia events chart, in the Notes of the Hypoglycemia section (the same text
    as on the report page).

    A marker's color is the event's level, not its lowest value: a single value below 54 mg/dL does not make a level
    2 event.
    """
    return _L(t("report.lows.scatter_caption"))


def _dur(minutes) -> str:
    """Return a duration in whole minutes with the unit symbols, for the event summary table and the time per day
    beside a percentage: "45 min", "2 h 5 min", "3 d 4 h" (About This Report gives the key of the symbols; _dur_words
    is its spoken form)."""
    if minutes is None:
        return "-"
    m = int(minutes)
    if m < 60:
        return t("report.duration.min", minutes=m)
    if m < 24 * 60:
        h, rest = divmod(m, 60)
        return t("report.duration.h_min", hours=h, minutes=rest) if rest else t("report.duration.h", hours=h)
    return t("report.duration.d_h", days=m // 1440, hours=(m % 1440) // 60)


def _dur_words(minutes) -> str:
    """Return a duration in whole minutes in words, for running text and as the spoken form of _dur: "45 minutes",
    "2 hours 5 minutes", "3 days 4 hours" (the same parts as _dur)."""
    if minutes is None:
        return "-"
    m = int(minutes)

    def words(unit: str, n: int) -> str:
        """One part: "1 hour", "5 minutes"."""
        return tp(f"report.duration.words.{unit}", n)

    def two(larger: str, n: int, unit: str) -> str:
        """The larger part, and the smaller one when it is not zero."""
        return t("report.duration.join", larger=larger, smaller=words(unit, n)) if n else larger

    if m < 60:
        return words("minute", m)
    if m < 24 * 60:
        return two(words("hour", m // 60), m % 60, "minute")
    return two(words("day", m // 1440), (m % 1440) // 60, "hour")


def _per_day(minutes) -> tuple[str, str]:
    """Return the time per day beside a percentage as shown, "(1 h 12 min)", and as read, "(1 hour 12 minutes)"; ("",
    "") without a value. A whole day is "(24 h)", "(24 hours)": a time per day, not one day."""
    if minutes is None:
        return "", ""
    if int(minutes) >= 24 * 60:
        return t("report.per_day", duration=t("report.duration.h", hours=24)), t(
            "report.per_day", duration=tp("report.duration.words.hour", 24)
        )
    return t("report.per_day", duration=_dur(minutes)), t("report.per_day", duration=_dur_words(minutes))


# ---------------------------------------------------------------------- enough data
# A period's values are shown only with enough data: sensor active at least 70 %, compared before rounding
# (report.ENOUGH_DATA_PCT; the report data carry enough_data per period). The period's note names the period, its
# sensor active and, for a period that ends with the report, about how much more data it would need with no gaps
# (more_needed); the sections after it that show the same period carry the short line.
def enough_note(r: dict, period: str) -> str:
    """Return the note of a period without enough data: "30 Days: not enough data yet. ... So far they cover 23%. With
    no gaps, about 14 more days are needed."; "" for a period with enough data."""
    if r.get("enough_data", True):
        return ""
    text = t("report.enough.note", period=period, active=_f(r.get("active")))
    more = r.get("more_needed")
    if more:
        text += " " + tp(f"report.enough.more.{more[0]}", int(more[1]))
    return text


def enough_short(active, days: int) -> str:
    """Return the short line of a period without enough data: "Not enough data: sensor active (data received) 23% of
    30 days; the values of a period are shown from 70%."."""
    return tp("report.enough.short", int(days), active=_f(active))


# ---------------------------------------------------------------------- alternative texts
# The alternative text of each chart (its Figure's /Alt): one sentence with the data the chart shows, in the words
# of the Report page's chart descriptions (report.alt.*). The values are rounded as the report shows them and written
# as a screen reader reads them, with every unit written out (docs/ACCESSIBILITY.md rule 4; spoken.py).
def _spoken_pct(v) -> str:
    """Return a percentage as read aloud, rounded as shown: "72 percent"; "not available" for None."""
    if v is None:
        return t("spoken.not_available")
    rg = _RG["rg"]
    return speak(float(v), "%", len(rg.g(v).partition(rg.decimal)[2]), rg.decimal)


def _spoken_glucose(mg, with_unit=True) -> str:
    """Return a glucose value (mg/dL) as read aloud in the display unit: "62 milligrams per deciliter"; without the
    unit, the number alone."""
    if with_unit:
        return speak_glucose(mg, _unit(), dec=_RG["rg"].decimal)
    return speak_number(float(fmt(mg, _unit())), 1 if _mm() else 0, _RG["rg"].decimal)


def _alt_tir(r: dict) -> str:
    """Return the alternative text of the time-in-ranges bar: the percentage of every range."""
    return t(
        "report.alt.tir",
        very_low=_spoken_pct(r.get("very_low")),
        low=_spoken_pct(r.get("low")),
        in_range=_spoken_pct(r.get("tir")),
        tight=_spoken_pct(r.get("titr")),
        high=_spoken_pct(r.get("high")),
        very_high=_spoken_pct(r.get("very_high")),
    )


def _alt_profile(prof: dict, title: str) -> str:
    """Return the alternative text of a glucose profile: the lowest and highest median, and the 5 to 95 % range."""
    got = {k: [v for v in prof.get(k, []) if v is not None] for k in ("p5", "p50", "p95")}
    if not got["p50"]:
        return t("report.profile.insufficient")
    median = {"median_min": _spoken_glucose(min(got["p50"]), False), "median_max": _spoken_glucose(max(got["p50"]))}
    if not (got["p5"] and got["p95"]):
        return t("report.alt.profile_median", title=title, **median)
    return t(
        "report.alt.profile",
        title=title,
        range_min=_spoken_glucose(min(got["p5"]), False),
        range_max=_spoken_glucose(max(got["p95"])),
        **median,
    )


def _alt_limits(runs: list) -> str:
    """Return the sentence on the readings at the sensor's limit that a chart marks (runs: [[first, after last, kind]],
    report.period_report), in words: "Readings at the sensor's limit, which have no value: below the sensor's lower
    limit."; "" without any."""
    kinds = [speak_limit(k) for k in ("low", "high") if any(run[2] == k for run in runs or [])]
    return t("report.alt.limits", kinds=t("report.list_separator").join(kinds)) if kinds else ""


def _alt_day(label: str, values: list, runs: list | None = None) -> str:
    """Return the alternative text of one day's profile: its lowest and highest 10-minute mean, and the readings at
    the sensor's limit it marks."""
    got = [v for v in values if v is not None]
    if not got:
        text = t("report.alt.day_no_data", label=label)
    else:
        text = t("report.alt.day", label=label, min=_spoken_glucose(min(got), False), max=_spoken_glucose(max(got)))
    limits = _alt_limits(runs or [])
    return f"{text} {limits}" if limits else text


def _alt_events(events: list) -> str:
    """Return the alternative text of the hypoglycemia events chart: the events, the level 2 ones, the lowest value."""
    lowest = min(events, key=lambda e: e[2])  # [start, minutes, lowest value, hour, level, lowest is LO]
    return tp(
        "report.alt.events",
        len(events),
        level_2=sum(1 for e in events if e[4] == 2),
        lowest=speak_limit("low") if lowest[5] else _spoken_glucose(lowest[2]),
    )


def _alt_weekly(key: str, values: list) -> str:
    """Return the alternative text of a weekly trend chart: the weeks with values, the lowest and highest value, each
    with the decimals the weekly table shows it with (a percentage as rounded in the report data, the mean in the
    display unit), so that the words and the table give the same numbers."""
    dec = _RG["rg"].decimal
    if key == "mean":
        digits = 1 if _mm() else 0
        lo, hi = (float(fmt(v, _unit())) for v in (min(values), max(values)))
        lo_d = hi_d = digits
        unit = _unit()
    else:  # a percentage of the report data, shown as it is (:g, _f)
        lo, hi = min(values), max(values)
        lo_d, hi_d = _places(lo), _places(hi)
        unit = "%"
    return tp(
        "report.alt.weekly",
        len(values),
        name=t(f"report.alt.measure.{key}"),
        min=speak_number(lo, lo_d, dec),
        max=speak(hi, unit, hi_d, dec),
    )


# A number with a unit symbol in a table cell ("102 mg/dL", "7.0%", "53 mmol/mol") and a duration with its symbol ("2 h
# 5 min"): read in words (_speak_units), as the Report page reads every table cell (gcSpeakText)
_UNIT_NUMBER = re.compile(r"(\d+(?:[.,]\d+)?)\s?(mg/dL/min|mmol/L/min|mmol/mol|mg/dL|mmol/L|%)")
_DURATION = re.compile(r"(?<![\w.,])(\d+)\s?(min|h|d)(?!\w)")


def _speak_units(text: str) -> str:
    """Return a text with every number and its unit symbol in words ("102 mg/dL" -> "102 milligrams per deciliter",
    "7.0%" -> "7.0 percent", "2 h 5 min" -> "2 hours 5 minutes"), the number as written; the rest as it is. The
    unit steps of the Report page's gcSpeakText (glucocraft-shared.js), with the same catalogue words
    (spoken.unit.*)."""

    def unit(m: re.Match[str]) -> str:
        """One number with its unit symbol in words, the number as written."""
        number, symbol = m.group(1), m.group(2)
        digits = len(re.split(r"[.,]", number)[1]) if re.search(r"[.,]", number) else 0
        return tp(f"spoken.unit.{SPOKEN_UNITS[symbol]}", abs(float(number.replace(",", "."))), digits, number=number)

    text = _UNIT_NUMBER.sub(unit, text)
    return _DURATION.sub(
        lambda m: tp(f"spoken.unit.{SPOKEN_UNITS[m.group(2)]}", int(m.group(1)), 0, number=m.group(1)), text
    )


class Report:
    """Build the CGM Report page by page.

    Each section method lays out its parts with need() and block(): a block reserves its height on the current page
    (or starts a new page when it does not fit) and records a drawing function for that page. finish() then draws
    every page: the header and the footer (which need the page count) and the recorded functions. Positions are in
    points from the bottom left; self.y is the top of the free space on the current page.

    The PDF is tagged (pdf_tags.py): the drawing functions open the structure elements (headings, paragraphs, lists,
    tables, figures) around what they draw, in the order of the page, which is the reading order. Whatever is drawn
    outside an element (rules, frames, backgrounds, the page header and footer) is an artifact. An element that spans
    several blocks or pages, such as a table, is opened and closed by recorded functions too (begin() and end()).
    """

    def __init__(self, data, color):
        """Prepare the canvas and set the settings of the render from `data` (_begin_render)."""
        from reportlab.lib.colors import Color, HexColor
        from reportlab.pdfbase.pdfdoc import PDFString
        from reportlab.pdfbase.pdfmetrics import stringWidth

        from .pdf_tags import CONTENT_ROLES, TaggedCanvas, table_attributes

        def sw(s, font, size):
            """Return the width of a text as drawn."""
            return stringWidth(s, font, size)

        self.HexColor, self.Color, self.sw, self.table_attributes = HexColor, Color, sw, table_attributes
        self.PDFString = PDFString
        self.content_roles = CONTENT_ROLES
        self.d, self.color = data, color
        _begin_render(data)
        self.buf = BytesIO()
        # The document's language (the catalog's /Lang) and title, which PDF readers and screen readers use; the
        # embedded fonts start every page (initialFontName)
        self.c = TaggedCanvas(
            self.buf,
            pagesize=(PAGE_W, PAGE_H),
            lang=PDF_LANGUAGE,
            title=t("report.pdf.title", period=data["label"]),
            initialFontName=FONT,
        )
        self.tg = self.c.tagger
        self.c.setAuthor("GlucoCraft")
        self.c.setSubject(report_description())
        self.c.setCreator("GlucoCraft")
        self.c.setViewerPreference("DisplayDocTitle", "true")  # a reader's window shows the title, not the file name
        self.c.showOutline()  # a reader opens the bookmarks beside the pages (WCAG technique PDF2)
        self.bookmarks = 0
        self.pages = []
        self.cur = None
        self.y = TOP
        # the title of the section whose Notes are being laid out: a page they reach starts with "<title> (continued)"
        self.continued: str | None = None

    # ------------------------------------------------------------------ colors
    def zone_fill(self, key, alpha=1.0):
        """Return the fill color of a glucose zone (AGP colors, or gray levels in black and white)."""
        if self.color:
            col = self.HexColor(AGP[key])
            return self.Color(col.red, col.green, col.blue, alpha=alpha)
        g = GRAY[key]
        g = 1 - (1 - g) * alpha if alpha < 1 else g
        return self.Color(g, g, g)

    def ink(self, g=0.0):
        """Return a gray level (0 black, 1 white)."""
        return self.Color(g, g, g)

    def hatch(self, x, y, w, h, key):
        """Hatch a high or very high zone in black and white, so the two high zones differ from the low ones."""
        if self.color or key not in HATCHED or w <= 0 or h <= 0:
            return
        c = self.c
        c.saveState()
        p = c.beginPath()
        p.rect(x, y, w, h)
        c.clipPath(p, stroke=0, fill=0)
        c.setStrokeColor(self.ink(1.0 if key == "vhigh" else 0.25))
        c.setLineWidth(0.6)
        k = -h
        while k < w:
            c.line(x + k, y, x + k + h, y + h)
            k += 4.5
        c.restoreState()

    def zone_rect(self, x, y, w, h, key, alpha=1.0):
        """Fill a rectangle in a zone's color (hatched where needed)."""
        self.c.setFillColor(self.zone_fill(key, alpha))
        self.c.rect(x, y, w, h, fill=1, stroke=0)
        if alpha >= 0.99:
            self.hatch(x, y, w, h, key)

    # ------------------------------------------------------------------ text
    def text(self, x, y, s, size=9, bold=False, g=0.0, align="left", said=None):
        """Draw one line of text (left, right or center aligned); a comparison sign is read in words (spoken_text), and
        `said`, when given, is read instead of the text (a duration with unit symbols in words). The report writes met /
        not met as words (report.mark.*), never as the marks ✓ / ✗, which need a symbol font and are read back
        differently by PDF readers."""
        c = self.c
        c.setFont(BOLD if bold else FONT, size)
        c.setFillColor(self.ink(g))
        with self.spoken_text(s, said):
            if align == "right":
                c.drawRightString(x, y, s)
            elif align == "center":
                c.drawCentredString(x, y, s)
            else:
                c.drawString(x, y, s)

    def spoken_text(self, s, said=None):
        """Return the context of a text with a comparison sign before a number ("Consensus target <4%"), or of a text
        with its own spoken form `said` ("2 h 5 min" read as "2 hours 5 minutes"): a Span whose /ActualText writes it
        in words ("less than 4%"), which screen readers read instead of the text; for any other text, and inside a
        figure or an artifact (which are not read as text), no element. A dash that stands for a missing value is
        read "not available".

        In a table cell every number with a unit symbol is read in words too ("102 mg/dL", "7.0%"; _speak_units), as
        on the Report page, and the words go into the cell's own /ActualText: the spoken forms of the texts drawn in
        it, in their order (a Span for every cell would add about 0.1 MB to the 6-month full report). A header cell
        opened with its own /ActualText (the hour of the Daily Log) keeps it."""
        tg = self.tg
        top = tg.stack[-1]
        if tg.enclosing is not None or top.role not in self.content_roles:
            return nullcontext()
        if said is None and s.strip() == "-":
            said = t("spoken.not_available")
        said = speak_comparisons(said or s)
        if top.role in ("TD", "TH"):
            parts = getattr(top, "spoken_parts", None)
            if parts is None:
                if "ActualText" in top.dict:
                    return nullcontext()
                parts = top.spoken_parts = []
            parts.append((s, _speak_units(said)))
            if any(shown != spoken for shown, spoken in parts):
                top["ActualText"] = self.PDFString(" ".join(spoken for _, spoken in parts))
            return nullcontext()
        if said == s:
            return nullcontext()
        return tg.tag("Span", ActualText=said)

    def outline(self, title, level, y):
        """Add a heading to the document outline at its place on the current page: H1 at the first level, H2 under
        it (WCAG technique PDF2: bookmarks for a long document)."""
        key = f"h{self.bookmarks}"
        self.bookmarks += 1
        self.c.bookmarkHorizontalAbsolute(key, y)
        self.c.addOutlineEntry(title, key, level=level - 1)

    def wrap(self, s, size, width, bold=False):
        """Return the lines of a text wrapped to a width; a no-break space (NB) keeps "Table 6.2" on one line. A line
        may exceed the width by WRAP_TOLERANCE."""
        words, lines, cur = [w.replace("\x00", NB) for w in s.replace(NB, "\x00").split()], [], ""
        for w in words:
            joined = (cur + " " + w).strip()
            if self.sw(joined, BOLD if bold else FONT, size) <= width + WRAP_TOLERANCE:
                cur = joined
            else:
                # a word wider than the width (a long URL or entity id) stands on a line of its own: no empty line
                # before it when it is the first word
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    def para(self, s, size=8.5, width=None, lead=None, g=0.15, bold_lead=None, item=False, split=False):
        """Add a paragraph at the current position, with an optional lead set in bold.

        item: the paragraph is an item of a list (an L that the caller opened): its bold lead is the item's label
        (Lbl) and the rest its body (LBody). Otherwise the whole paragraph is one P.
        split: the paragraph may continue on the next page line by line (the Notes, note()); otherwise it moves to the
        next page as a whole when it does not fit.
        """
        width = width or PAGE_W - 2 * M  # the paper in use
        lead = lead or size * 1.32
        full = (bold_lead + " " + s) if bold_lead else s
        # the lead is set in bold: leave room for its extra width on the first line
        extra = self.sw(bold_lead, BOLD, size) - self.sw(bold_lead, FONT, size) if bold_lead else 0
        lines = self.wrap(full, size, width - extra)
        self.need((1 if split else len(lines)) * lead + 2)
        led = bool(bold_lead) and bool(lines) and lines[0].startswith(bold_lead)
        body = "LBody" if item else "P"
        if item:
            self.begin("LI")
        if not (item and led):  # a labeled item opens its body after the label
            self.begin(body)
        for i, ln in enumerate(lines):
            if split and i:
                self.need(lead)
            y = self.y - size
            if i == 0 and led:
                rest = ln[len(bold_lead) :].strip()

                def first(y=y, rest=rest):
                    """Draw the first line: the lead in bold (an item's label), then the rest of the line.

                    In a paragraph the lead is drawn with the space after it, so that the text read from the PDF
                    has the space between the lead and the rest.
                    """
                    if item:
                        with self.tg.tag("Lbl"):
                            self.text(M, y, bold_lead, size, True, g)
                        self.tg.open(body)
                    else:
                        self.text(M, y, bold_lead + " " if rest else bold_lead, size, True, g)
                    if rest:
                        self.text(M + self.sw(bold_lead + " ", BOLD, size), y, rest, size, False, g)

                self.draw(first)
            else:
                self.draw(lambda y=y, ln=ln: self.text(M, y, ln, size, False, g))
            self.y -= lead
        self.end(body)
        if item:
            self.end("LI")
        self.y -= 3

    # ------------------------------------------------------------------ structure (pdf_tags.py)
    def tag(self, role, **attrs):
        """Return the context of a structure element around drawing calls (inside a drawing function)."""
        return self.tg.tag(role, **attrs)

    def th(self, scope="Column", **attrs):
        """Return the context of a header cell: of its column ("Column") or of its row ("Row")."""
        return self.tg.tag("TH", A=self.table_attributes(scope), **attrs)

    def td(self, colspan=1):
        """Return the context of a data cell, spanning `colspan` columns."""
        return self.tg.tag("TD", A=self.table_attributes(colspan=colspan)) if colspan > 1 else self.tg.tag("TD")

    def begin(self, role, **attrs):
        """Record the opening of a structure element at this point of the page flow; it stays open across blocks and
        pages until end() records its closing."""
        self.draw(lambda: self.tg.open(role, **attrs))

    def end(self, role):
        """Record the closing of the element that begin() opened."""
        self.draw(lambda: self.tg.close(role))

    # ------------------------------------------------------------------ page flow
    def new_page(self):
        """Start a new page."""
        self.pages.append([])
        self.cur = self.pages[-1]
        self.y = TOP

    def draw(self, fn):
        """Record a drawing function for the current page."""
        self.cur.append(fn)

    def need(self, h):
        """Start a new page unless `h` points still fit on the current one. While a section's Notes are laid out
        (notes()), the new page starts with "<section> (continued)"."""
        if self.cur is None or self.y - h < BOTTOM:
            self.new_page()
            if self.continued:
                y, title = self.y, t("report.notes.continued", section=self.continued)

                def head():
                    """Draw the section's title with "(continued)": a running head, an artifact, as the repeated header
                    rows of a table are."""
                    with self.tg.artifact():
                        self.text(M, y - 10, title, 10, True)

                self.draw(head)
                self.y -= 16

    @contextmanager
    def notes(self, section: str):
        """Lay out the Notes block that ends a section, with the paragraphs that note() adds inside the block: the
        title "Notes" (H3) with at least one line after it, then the full cited explanation in the smallest text size.
        The Notes stay with their section: where they reach the next page, they continue at its top under
        "<section> (continued)". The reading order of a section is its caption, its data, its Notes."""
        self.continued = section
        self.need(13 + MIN_PT * 1.32 + 2)
        y = self.y

        def title():
            """Draw the title of the Notes."""
            with self.tag("H3"):
                self.text(M, y - 9, t("report.notes.title"), 8.5, True, 0.2)

        self.draw(title)
        self.y -= 13
        try:
            yield
        finally:
            self.continued = None

    def note(self, text: str, lead: str | None = None, item: bool = False):
        """Add a paragraph to the Notes block that is being laid out (notes()), with an optional lead in bold; item: a
        list item (of an L the caller opened)."""
        if text:
            self.para(text, size=MIN_PT, g=0.3, bold_lead=lead, item=item, split=True)

    def heading(self, s, sub=None, keep=70, level=2):
        """Add a heading (H2, or H3 inside a section) with a rule and an optional subtitle; `keep` points of the
        section stay with it."""
        sub_lines = self.wrap(sub, 8.5, PAGE_W - 2 * M) if sub else []
        sub_h = 11 * len(sub_lines) + 3 if sub else 0
        self.need(24 + sub_h + keep)
        y = self.y

        def title():
            """Draw the heading and its rule."""
            with self.tag(f"H{level}"):
                self.text(M, y - 13, s, 12.5, True)
            if level <= 2:
                self.outline(s, level, y)
            self.c.setStrokeColor(self.ink(0.0))
            self.c.setLineWidth(0.8)
            self.c.line(M, y - 18, PAGE_W - M, y - 18)

        self.draw(title)
        self.y -= 24
        if sub:
            yy = self.y
            self.begin("P")
            for ln in sub_lines:
                self.draw(lambda yy=yy, ln=ln: self.text(M, yy - 8.5, ln, 8.5, False, 0.3))
                yy -= 11
            self.end("P")
            self.y = yy - 3

    def block(self, h, fn):
        """Reserve `h` points and record fn(top y) to draw them."""
        self.need(h)
        y = self.y
        self.draw(lambda: fn(y))
        self.y -= h

    def header_note(self) -> str:
        """Return the second line of every page header: the ranges' sources and the glucose sources (source_line).

        When the line is too long for the page at the smallest text size, the citations are left out (page 1 carries
        them), and a source line that is still too long is shortened with an ellipsis. About This Report names the
        sources in full: the header of the pages after page 1 is an artifact, which screen readers skip.
        """
        width = PAGE_W - 2 * M
        data = source_line(self.d)
        full = t("report.header.sources", data=data)
        if self.sw(full, FONT, MIN_PT) <= width:
            return full
        short, text = data, data
        while self.sw(short, FONT, MIN_PT) > width and len(text) > 1:
            text = text[:-1]
            short = t("report.header.cut", text=text.rstrip())
        return short

    def header_texts(self) -> tuple[str, str, str]:
        """Return the texts of the page header: the title, the period with the creation time, and the sources."""
        created = t("report.header.created", period=self.d["label"], created=self.d["created"])
        return t("report.header.title"), created, self.header_note()

    def first_page_header(self):
        """Draw page 1's creation line and sources as text of the report, after its first lines.

        Every other page repeats them in its header, which is an artifact (finish()).
        """
        _title, created, note = self.header_texts()
        with self.tag("P"):
            self.text(PAGE_W - M, PAGE_H - 34, created, 9.5, False, 0.2, "right")
        with self.tag("P"):
            self.text(M, PAGE_H - 50, note, MIN_PT, False, 0.35)

    def finish(self):
        """Draw every page (header, footer, recorded content) and return (pdf_bytes, page_count)."""
        total = len(self.pages)
        title, created, note = self.header_texts()
        c, tg = self.c, self.tg
        for i, calls in enumerate(self.pages):
            # the header and the footer repeat on every page: artifacts (page 1's own lines: first_page_header)
            with tg.artifact("Pagination", "Header"):
                self.text(M, PAGE_H - 34, title, 9.5, True)
                if i:
                    self.text(PAGE_W - M, PAGE_H - 34, created, 9.5, False, 0.2, "right")
                c.setStrokeColor(self.ink(0.6))
                c.setLineWidth(0.5)
                c.line(M, PAGE_H - 40, PAGE_W - M, PAGE_H - 40)
                if i:
                    self.text(M, PAGE_H - 50, note, MIN_PT, False, 0.35)
            # the footer: one line on every page; the disclaimer in full is in About This Report
            with tg.artifact("Pagination", "Footer"):
                c.line(M, FOOTER_RULE_Y, PAGE_W - M, FOOTER_RULE_Y)
                self.text(M, FOOTER_Y, footer_line(i + 1, total), MIN_PT, False, 0.2)
            for fn in calls:
                fn()
            c.showPage()
        c.save()
        return self.buf.getvalue(), total

    # ------------------------------------------------------------------ chart pieces
    def axes_box(self, x, y, w, h, lo, hi, ylabels=True, xlabels=True, size=MIN_PT):
        """Draw a chart frame with the grid in the display unit and the time axis; return the y function (mg/dL)."""
        c = self.c

        def sy(v):
            """The y position of a value on the chart."""
            return y + (v - lo) / (hi - lo) * h

        sf = MGDL_PER_MMOLL if _mm() else 1.0  # grid in the display unit, positions in mg/dL
        raw = (hi - lo) / sf / 5
        st = next((q for q in (0.5, 1, 2, 5, 10) if q >= raw), 10) if _mm() else _step(lo, hi, 5)
        k = int((lo / sf) // st) + 1
        c.setLineWidth(0.4)
        while k * st * sf < hi:
            gd = k * st
            g = gd * sf
            if g > 0:
                c.setStrokeColor(self.ink(0.85))
                c.line(x, sy(g), x + w, sy(g))
                # a label within 8 points of the bottom edge would overprint the first hour label: its line stays.
                # In mmol/L one format per axis: one decimal on a step with a half (7.0 / 6.5 / 6.0), whole numbers
                # on a whole step (4 / 6 / 8)
                if ylabels and not (xlabels and sy(g) - y < 8):
                    label = _RG["rg"].num(gd, 1 if st < 1 else 0) if _mm() else _RG["rg"].g(gd)
                    self.text(x - 3, sy(g) - 2.5, label, size, False, 0.3, "right")
            k += 1
        if xlabels:
            for i, hour in enumerate(_hours()):
                xx = x + i * w / 8
                c.setStrokeColor(self.ink(0.88))
                c.line(xx, y, xx, y + h)
                self.text(xx, y - 10, hour, size, False, 0.3, "center")
        c.setStrokeColor(self.ink(0.45))
        c.setLineWidth(0.6)
        c.rect(x, y, w, h, fill=0, stroke=1)
        return sy

    def range_lines(self, x, w, sy, lo, hi):
        """Draw the consensus thresholds 54, 70, 180 and 250 mg/dL across a chart."""
        c = self.c
        for v, key, wd, dash in (
            (VHIGH, None, 0.6, (1.5, 2)),
            (HIGH, "target", 1.2, None),
            (LOW, "target", 1.2, None),
            (VLOW, None, 0.6, (1.5, 2)),
        ):
            if lo < v < hi:
                c.setStrokeColor(self.zone_fill(key) if (key and self.color) else self.ink(0.25))
                c.setLineWidth(wd)
                c.setDash(*dash) if dash else c.setDash()
                c.line(x, sy(v), x + w, sy(v))
        c.setDash()

    def limit_marks(self, x, w, y_bottom, y_top, runs, slots, labels=True):
        """Mark the runs of slots that hold readings at the sensor's limit (runs: [[first, after last, "low" | "high"]],
        report.period_report) along a chart's bottom edge (LO) or top edge (HI): a run of short marks in the very-low or
        very-high color across the run's slots (at least 3 points long, so that one slot is seen), and the letters LO
        or HI once per run (not where they would touch the previous letters on the same edge). Such a reading has no
        value: the line still breaks there, and the marks say that it is not a gap in the data. slots: the slots across
        the width w."""
        c = self.c
        last_end = {"low": -1e9, "high": -1e9}
        for first, after, kind in runs or []:
            x0, x1 = x + first / slots * w, x + after / slots * w
            if x1 - x0 < 3:
                mid = (x0 + x1) / 2
                x0, x1 = mid - 1.5, mid + 1.5
            x0, x1 = max(x, x0), min(x + w, x1)
            yy = y_bottom + 1.6 if kind == "low" else y_top - 1.6
            c.setStrokeColor(self.zone_fill("vlow" if kind == "low" else "vhigh"))
            c.setLineWidth(3.2)
            c.setDash(1.6, 1.2)
            c.line(x0, yy, x1, yy)
            c.setDash()
            text = t(f"report.limit.{kind}")
            tw = self.sw(text, BOLD, MIN_PT)
            tx = min(max(x0, x + 1), x + w - tw - 1)
            if labels and tx >= last_end[kind] + 2:
                ty = y_bottom + 4.5 if kind == "low" else y_top - 4.5 - MIN_PT * 0.75
                self.text(tx, ty, text, MIN_PT, True, 0.0)  # black: the marks carry the color
                last_end[kind] = tx + tw

    def clip_zone(self, x, y, w, h, sy, zlo, zhi):
        """Clip the drawing to one zone's band of a chart; return False when the band is outside the chart."""
        a, b = max(sy(zlo), y), min(sy(zhi), y + h)
        if b <= a:
            return False
        p = self.c.beginPath()
        p.rect(x, a, w, b - a)
        self.c.clipPath(p, stroke=0, fill=0)
        return True

    def band_path(self, xs, lower, upper):
        """Return the closed path between a lower and an upper line (None where too few points exist)."""
        p = self.c.beginPath()
        pts_u = [(x, v) for x, v in zip(xs, upper, strict=False) if v is not None]
        pts_l = [(x, v) for x, v in zip(xs, lower, strict=False) if v is not None]
        if len(pts_u) < 2 or len(pts_l) < 2:
            return None
        p.moveTo(*pts_u[0])
        for q in pts_u[1:]:
            p.lineTo(*q)
        for q in reversed(pts_l):
            p.lineTo(*q)
        p.close()
        return p

    # ------------------------------------------------------------------ the standard page (14 days)
    def standard_page(self):
        """Lay out page 1: the 14-day header (_page1_header), the time in ranges with the glucose metrics
        (_page1_ranges) and the glucose profile with the start of the Notes (_page1_profile)."""
        r = self.d["standard"]
        self.new_page()
        self._page1_header(r)
        self._page1_ranges(r)
        self._page1_profile(r)

    def _page1_header(self, r):
        """Add page 1's header block: the title, the period, sensor active, the days with data, the creation line and
        the sources, and the key of the range colors."""
        # the note of a period without enough data (less than 70% sensor active, compared before rounding:
        # report.period_report enough_data), under the days with data: the header grows by its lines
        note = enough_note(r, t("report.period.14"))
        low_data = bool(note)
        low_lines = self.wrap(note, 8, P1_NOTE_W, True) if low_data else []

        def header(y):
            """Draw the title, the period, sensor active, the days with data, the creation line and the range key."""
            with self.tag("H1"):
                self.text(M, y - 18, t("report.page1.title"), 16, True)
            self.outline(t("report.page1.title"), 1, y)
            start, end = _date(r.get("period_start")), _date(r.get("period_end"))
            with self.tag("P"):
                self.text(M, y - 36, t("report.page1.period", start=start, end=end), 10, True)
            # the sensor-active label wherever the percentage is shown; its explanation is on the Data Quality page
            # (once per surface)
            with self.tag("P"):
                self.text(
                    M, y - 50, t("report.label_value", label=sensor_active_label(), value=_f(r.get("active"))), 10
                )
            with self.tag("P"):
                days = t("report.page1.days_with_data", count=r.get("days_with_data", 0), total=STANDARD_DAYS)
                self.text(M, y - 63, days, 10)
            if low_data:
                with self.tag("P"):
                    for k, ln in enumerate(low_lines):
                        self.text(M, y - P1_NOTE_TOP - k * P1_NOTE_LEAD, ln, 8, True, 0.25)
            self.first_page_header()
            self._range_key(y)

        # with the note: down to 4 points below its last line
        if low_lines:
            height = max(P1_HEADER_H, P1_NOTE_TOP + P1_NOTE_LEAD * (len(low_lines) - 1) + 4)
        else:
            height = P1_HEADER_H
        self.block(height, header)

    def _range_key(self, y):
        """Draw the key of the range colors at the top right of page 1 (y: the top of the header block)."""
        kx, ky = PAGE_W - M - KEY_W, y - 34
        # the title names the unit of the thresholds; the key has a swatch for each band of the bar, the tight
        # range included (the lighter part of in range)
        kt = _L(t("report.key.title" if self.color else "report.key.title_hatched", unit=_unit()))
        with self.tag("P"):
            self.text(min(kx, PAGE_W - M - self.sw(kt, BOLD, MIN_PT)), ky, kt, MIN_PT, True, 0.25)
        with self.tag("L"):
            for i, key in enumerate(("vhigh", "high", "target", "tight", "low", "vlow")):
                lab = _L(t(f"report.key.{key}"))
                xx, yy = kx + (i % 2) * KEY_COL_W, ky - 11 - (i // 2) * KEY_ROW_H
                with self.tag("LI"), self.tag("LBody"):
                    self.zone_rect(xx, yy - 1, 9, 7, key)
                    self.text(xx + 12, yy, lab, MIN_PT, False, 0.2)

    def _page1_ranges(self, r):
        """Add page 1's time-in-ranges block: the bar, its band labels and the four summary values, with the glucose
        metrics column at the right. The block is as tall as the taller of the two."""
        # Glucose Metrics column: laid out first, so that the block is as tall as its text (mmol/L labels wrap more)
        mx = M + X(METRICS_X)
        # GMI in the A1C / GMI unit in use: the second unit on the note line
        gmi, gmi2 = (
            _RG["rg"].a1c_parts(r.get("gmi"), r.get("gmi_mmol_mol")) if r.get("gmi") is not None else ("-", None)
        )
        # (label, value, note lines under the value)
        has_gri = r.get("gri") is not None
        mt = [
            (t("report.metrics.mean"), _gu(r.get("mean")), ()),
            (t("report.metrics.gmi"), gmi, ((gmi2,) if gmi2 else ()) + (t("report.metrics.gmi_note"),)),
            (t("report.metrics.cv"), _f(r.get("cv")), (t("report.metrics.cv_note"),)),
            (
                t("report.metrics.gri"),
                t("report.metrics.gri_value", value=_f(r["gri"], ""), zone=r["gri_zone"]) if has_gri else "-",
                # the two components, one line each
                (
                    t("report.metrics.gri_hypo", value=_f(r["gri_hypo"], "")),
                    t("report.metrics.gri_hyper", value=_f(r["gri_hyper"], "")),
                )
                if has_gri
                else (),
            ),
            (t("report.metrics.sd"), _gu(r.get("sd")), ()),
        ]
        mlines = {lab: self.wrap(lab, 8.5, min(120, PAGE_W - M - mx - self.sw(v, BOLD, 11) - 6)) for lab, v, _ in mt}
        # the time-in-ranges note: from its third line on, the bar and its labels move down by a line each
        tir_note = self.wrap(
            t("report.tir.note", unit=_unit(), minutes=_d(MINUTES_PER_PERCENT_TEXT)), MIN_PT, TIR_NOTE_W
        )
        down = 9 * max(0, len(tir_note) - 2)
        tir_h = max(
            TIR_BAR_FOOT + 10 + down,  # the bar and the note of its lowest label
            METRICS_TOP + sum(len(mlines[lab]) * 10 + 7 + 9 * max(1, len(notes)) for lab, _, notes in mt) + 4,
        )

        def ranges(y):
            """Draw the time-in-ranges bar, its labels, the four summary values and the glucose metrics column."""
            with self.tag("H2"):
                self.text(M, y - 12, t("report.tir.title"), 11.5, True)
            self.outline(t("report.tir.title"), 2, y)
            with self.tag("P"):
                for k, ln in enumerate(tir_note):
                    self.text(M, y - 24 - k * 9, ln, MIN_PT, False, 0.35)
            bx, by = M + 4, y - TIR_BAR_FOOT - down
            self._tir_bar(r, bx, by)
            self._tir_band_labels(r, bx + TIR_BAR_W + 8, by)
            self._tir_summary(r, by)
            self._metrics_column(mt, mlines, mx, y)

        self.block(tir_h, ranges)

    def _tir_bar(self, r, bx, by):
        """Draw the time-in-ranges bar with its lower left corner at (bx, by), one band per range, as the figure that
        carries the bar's description."""
        c = self.c
        bw, bh = TIR_BAR_W, TIR_BAR_H
        vals = [
            ("vlow", r.get("very_low")),
            ("low", r.get("low")),
            ("tight", r.get("titr")),
            ("target", (r.get("tir") or 0) - (r.get("titr") or 0)),
            ("high", r.get("high")),
            ("vhigh", r.get("very_high")),
        ]
        # thin bands remain visible; without any value (no data) the bar is its frame only
        empty = all(r.get(k) is None for k in ("very_low", "low", "titr", "tir", "high", "very_high"))
        disp = [0 if empty else max(max(v or 0, 0), TIR_MIN_BAND) for _, v in vals]
        tot = sum(disp) or 1
        with self.tg.figure(_alt_tir(r), (bx, by, bx + bw, by + bh)):
            yy = by
            for (key, _), dh in zip(vals, disp, strict=False):
                if not dh:
                    continue
                hh = dh / tot * bh
                self.zone_rect(bx, yy, bw, hh, key)
                c.setStrokeColor(self.ink(1.0))
                c.setLineWidth(0.8)
                c.line(bx, yy, bx + bw, yy)
                yy += hh
            c.setStrokeColor(self.ink(0.3))
            c.setLineWidth(0.5)
            c.rect(bx, by, bw, bh, fill=0, stroke=1)

    def _tir_band_labels(self, r, lx, by):
        """Draw the labels of the bar's bands from lx (the bar's foot at by): each band's percentage with its time per
        day, and its consensus target under it."""
        bh = TIR_BAR_H
        bands = (("vhigh", "very_high"), ("high", "high"), ("target", "tir"), ("low", "low"), ("vlow", "very_low"))
        per_day = r.get("per_day") or {}
        with self.tag("L"):
            for (key, value), ty in zip(
                bands, [by + bh - 8, by + bh - 48, by + bh / 2 + 4, by + 46, by + 8], strict=False
            ):
                label = t(f"report.tir.band.{key}")
                with self.tag("LI"), self.tag("LBody"):
                    band = t("report.tir.band_value", label=label, value=_f(r.get(value)))
                    self.text(lx, ty, band, 9, True)
                    # the time per day beside the percentage: "Very high 4.8% (1 h 9 min)"
                    shown, said = _per_day(per_day.get(value))
                    if shown:
                        self.text(lx + self.sw(band + " ", BOLD, 9), ty, shown, 9, False, 0.2, said=said)
                    # one line: "Consensus target <5% · above 250"
                    self.text(lx, ty - 10, _L(t(f"report.tir.band_note.{key}")), MIN_PT, False, 0.35)

    def _tir_summary(self, r, by):
        """Draw the four summary values beside the bar (the bar's foot at by), one table row each: its label (the row
        header), the value with its time per day, the consensus target and the range."""
        # (key, value, consensus target; None: no consensus target)
        summary = (
            ("tar", "above_180", _target("time_high")),
            ("tir", "tir", _target("time_in_range")),
            ("titr", "titr", None),
            ("tbr", "below_70", _target("time_low")),
        )
        per_day = r.get("per_day") or {}
        sx = M + X(TIR_SUMMARY_X)
        with self.tag("Table", T=t("report.tir.title")):
            for i, (key, value, target) in enumerate(summary):
                ty = by + TIR_BAR_H - 10 - i * TIR_SUMMARY_ROW_H
                with self.tag("TR"):
                    with self.th("Row"):
                        self.text(sx + TIR_SUMMARY_LABEL_DX, ty, t(f"report.tir.summary.{key}.label"), 9, i == 1)
                    with self.td():
                        self.text(sx, ty, _f(r.get(value)), 15 if i == 1 else 12, True)
                        # the time per day, on a line under the value
                        shown, said = _per_day(per_day.get(value))
                        if shown:
                            self.text(sx, ty - 11, shown, MIN_PT, False, 0.35, said=said)
                    # two lines: the target, then the range
                    tline = t("report.tir.target", target=target) if target else t("report.tir.no_target")
                    for k, ln in enumerate((tline, t(f"report.tir.summary.{key}.range"))):
                        with self.td():
                            self.text(sx + TIR_SUMMARY_LABEL_DX, ty - 11 - k * 9, _L(ln), MIN_PT, False, 0.35)

    def _metrics_column(self, mt, mlines, mx, y):
        """Draw the Glucose Metrics column from mx (y: the top of the block): its heading and one table row per metric
        (mt: (label, value, note lines); mlines: each label's wrapped lines)."""
        with self.tag("H2"):
            self.text(mx, y - 12, t("report.metrics.title"), 11.5, True)
        self.outline(t("report.metrics.title"), 2, y)
        ty = y - METRICS_TOP
        with self.tag("Table", T=t("report.metrics.title")):
            for lab, v, notes in mt:
                with self.tag("TR"):
                    with self.th("Row"):
                        for ln in mlines[lab]:
                            self.text(mx, ty, ln, 8.5)
                            ty -= 10
                    with self.td():
                        self.text(PAGE_W - M, ty + 10, v, 11, True, 0, "right")
                        for k, ln in enumerate(notes):
                            self.text(PAGE_W - M, ty - k * 9, ln, MIN_PT, False, 0.35, "right")
                ty -= 7 + 9 * max(1, len(notes))

    def _page1_profile(self, r):
        """Add page 1's glucose profile and the start of its Notes."""
        # The glucose profile fills page 1 below the time in ranges (96 to 168 points: the
        # block is the profile's height plus 62 points and its caption's extra lines), leaving room at the page foot
        # for the compact start of the Notes: their title and the sources of the metrics (page1_notes). The further
        # Notes continue at the top of page 2.
        notes = self.page1_notes(r)
        caption = self.profile_caption()
        lead = MIN_PT * 1.32
        compact = 13 + len(self.wrap(notes[0][1], MIN_PT, PAGE_W - 2 * M)) * lead + 3
        room = self.y - BOTTOM - 62 - 9.6 * (len(caption) - 1) - compact
        title = t("report.profile.title", period=t("report.period.14"))
        self.agp_curve(r, title, h=max(96, min(168, int(room) - 1)), caption=caption)
        with self.notes(t("report.page1.title")):
            for lead_text, text in notes:
                self.note(text, lead_text)

    def page1_notes(self, r) -> list[tuple[str | None, str]]:
        """Return the Notes of page 1 as (lead, text) paragraphs: the sources and rules of the metrics with the
        readings at the sensor's limit (LO / HI), the ranges and targets, the groups whose published targets are not
        used, the names of the ranges, the CV target, the formulas of the metrics and the glucose profile, each with its
        citation."""
        limit = _limit_note(r)

        def lead(key):
            """The lead of a paragraph of the Notes of page 1."""
            return t(f"report.page1.notes.{key}.lead")

        return [
            (None, t("report.metrics.sources") + (f" {limit}" if limit else "")),
            (lead("ranges"), _L(t("report.page1.notes.ranges.body"))),
            (lead("groups"), t("report.page1.notes.groups.body")),
            (lead("names"), _ctx(ctx("names"))),
            (lead("cv"), _ctx(ctx("cv"))),
            (lead("metrics"), _dx(t("report.page1.notes.metrics.body"))),  # the decimal separator in use
            (lead("profile"), " ".join((_ctx(ctx("profile")), t("report.page1.notes.profile.body")))),
        ]

    def profile_caption(self) -> list[str]:
        """Return the caption lines of page 1's glucose profile: the lines and bands drawn (report.profile.lines, which
        the Report page shows too); its cited context is in the Notes of page 1. The thresholds are in the display
        unit."""
        caption = t("report.profile.caption", lines=t("report.profile.lines"))
        return self.wrap(_dx(_L(caption)), MIN_PT, PAGE_W - 2 * M)

    def agp_curve(self, r, title, h=240, level=2, caption=()):
        """Add a glucose profile: median, 25-75 % and 5-95 % bands by time of day, colored by zone.

        level: the level of its title (H2 on page 1, H3 inside a section); caption: the lines of its caption (page 1:
        profile_caption; the period's profile has none, its section's Notes describe it).
        """
        extra = 9.6 * (len(caption) - 1)

        def curve(y):
            """Draw the profile chart with its caption and the percentile labels at the right edge."""
            c = self.c
            with self.tag(f"H{level}"):
                self.text(M, y - 12, title, 11.5, True)
            if level <= 2:  # the headings H1 and H2 are the document's bookmarks (heading())
                self.outline(title, level, y)
            with self.tag("P"):
                for k, ln in enumerate(caption):
                    self.text(M, y - 24 - k * 9.6, ln, MIN_PT, False, 0.35)
            prof = r.get("profile") or {}
            allv = [v for k in ("p5", "p25", "p50", "p75", "p95") for v in prof.get(k, []) if v is not None]
            if not allv:
                with self.tag("P"):
                    self.text(M, y - 60 - max(0.0, extra), t("report.profile.insufficient"), 9)
                return
            lo, hi = _fit(min(allv), max(allv))
            x, w = M + 30, PAGE_W - 2 * M - 64
            yb = y - 40 - extra - h
            alt = _alt_profile(prof, title)
            limits = _alt_limits(r.get("profile_limits") or [])
            with self.tg.figure(f"{alt} {limits}" if limits else alt, (x - 30, yb - 14, x + w + 24, yb + h + 14)):
                sy = self.axes_box(x, yb, w, h, lo, hi)
                self.text(x - 26, yb + h + 6, _unit(), MIN_PT, False, 0.3)
                if lo < HIGH and hi > LOW:
                    a, b = sy(max(LOW, lo)), sy(min(HIGH, hi))
                    c.setFillColor(self.ink(0.95))
                    c.rect(x, a, w, b - a, fill=1, stroke=0)
                xs = [x + (i / 4 + 0.125) / 24 * w for i in range(96)]

                def P(k):
                    """The y positions of one percentile curve (None where it has no value)."""
                    return [sy(v) if v is not None else None for v in prof.get(k, [None] * 96)]

                for lo_k, hi_k, alpha in (("p5", "p95", 0.32), ("p25", "p75", 0.70)):
                    for zlo, zhi, key in ZONES:
                        c.saveState()
                        if self.clip_zone(x, yb, w, h, sy, zlo, zhi):
                            path = self.band_path(xs, P(lo_k), P(hi_k))
                            if path is not None:
                                c.setFillColor(self.zone_fill(key, alpha))
                                c.drawPath(path, fill=1, stroke=0)
                        c.restoreState()
                med = P("p50")
                for zlo, zhi, key in ZONES:
                    c.saveState()
                    if self.clip_zone(x, yb, w, h, sy, zlo, zhi):
                        c.setStrokeColor(self.zone_fill(key) if self.color else self.ink(0.0))
                        c.setLineWidth(2.6)
                        p = c.beginPath()
                        started = False
                        for xx, yy in zip(xs, med, strict=False):
                            if yy is None:
                                started = False
                                continue
                            p.lineTo(xx, yy) if started else p.moveTo(xx, yy)
                            started = True
                        c.drawPath(p, fill=0, stroke=1)
                    c.restoreState()
                self.range_lines(x, w, sy, lo, hi)
                # the times of day with readings at the sensor's limit, at the chart's bottom (LO) and top (HI) edges
                self.limit_marks(x, w, yb, yb + h, r.get("profile_limits"), 96)
                last = {
                    k: next((v for v in reversed(prof.get(k, [])) if v is not None), None)
                    for k in ("p5", "p25", "p50", "p75", "p95")
                }
                if all(v is not None for v in last.values()):
                    ys = {"p50": sy(last["p50"])}
                    ys["p75"] = max(sy(last["p75"]), ys["p50"] + 9)
                    ys["p95"] = max(sy(last["p95"]), ys["p75"] + 9)
                    ys["p25"] = min(sy(last["p25"]), ys["p50"] - 9)
                    ys["p5"] = min(sy(last["p5"]), ys["p25"] - 9)
                    for k, pct in (("p95", "95%"), ("p75", "75%"), ("p50", "50%"), ("p25", "25%"), ("p5", "5%")):
                        self.text(x + w + 4, ys[k] - 2.5, pct, MIN_PT, k == "p50", 0.2)

        self.block(h + 62 + extra, curve)

    def daily_profiles(self, r, title, notes):
        """Add the daily glucose profiles: one small chart per day, one calendar week per row; notes: the texts of the
        section's Notes."""
        rows, labels = r.get("daily") or [], r.get("daily_labels") or []
        said = r.get("daily_said") or []  # each day in words, for the figures' alternative texts
        if not rows:
            return
        allv = [v for day in rows for v in day if v is not None]
        lo, hi = _fit(min(allv), max(allv)) if allv else (40, 300)
        per_row, cw, chh, gap = 7, (PAGE_W - 2 * M - 22) / 7, 58, 18
        self.heading(title, _L(t("report.daily.caption")), keep=chh + gap + 6)
        # rows from the first day of the week in use: empty cells before the period's first day
        wds = r.get("daily_weekdays") or []
        lead = _RG["rg"].lead_days(wds[0]) if wds else 0
        cells = [None] * lead + list(range(len(rows)))
        for start in range(0, len(cells), per_row):
            chunk = cells[start : start + per_row]

            def row(y, chunk=chunk):
                """Draw one calendar week of daily profiles, each day a figure."""
                c = self.c
                for j, k in enumerate(chunk):
                    x = M + 22 + j * cw
                    w, yb = cw - 5, y - 12 - chh

                    def sy(v, yb=yb):
                        """The y position of a value on the chart."""
                        return yb + (v - lo) / (hi - lo) * chh

                    if j == 0:  # the scale at the left of the row: the figures' alternative texts give the values
                        with self.tg.artifact():
                            for v in (LOW, HIGH):
                                if lo < v < hi:
                                    self.text(x - 3, sy(v) - 2.5, _g(v), MIN_PT, False, 0.35, "right")  # display unit
                    if k is None:
                        continue
                    label = labels[k] if k < len(labels) else ""
                    vals = rows[k]
                    dl = r.get("daily_limits") or []
                    runs = dl[k] if k < len(dl) else []
                    with self.tg.figure(
                        _alt_day(said[k] if k < len(said) else label, vals, runs), (x, yb - 12, x + w, y)
                    ):
                        if lo < HIGH and hi > LOW:
                            c.setFillColor(self.ink(0.93))
                            c.rect(x, sy(max(LOW, lo)), w, sy(min(HIGH, hi)) - sy(max(LOW, lo)), fill=1, stroke=0)
                        c.setStrokeColor(self.ink(0.55))
                        c.setLineWidth(0.4)
                        c.rect(x, yb, w, chh, fill=0, stroke=1)
                        self.text(x + 2, y - 9, label, MIN_PT, True, 0.15)
                        xs = [x + (i / 6 + 1 / 12) / 24 * w for i in range(144)]
                        db = r.get("daily_breaks") or []
                        brk = set(db[k]) if k < len(db) else set()

                        def seg(pred, colkey, width, xs=xs, vals=vals, brk=brk, sy=sy):
                            """Stroke the parts of the day's line whose values meet pred, broken at data gaps."""
                            p, on, any_ = c.beginPath(), False, False
                            for i, (xx, v) in enumerate(zip(xs, vals, strict=False)):
                                if i in brk:  # a data gap before this slot (report.daily_breaks)
                                    on = False
                                if v is None or not pred(v):
                                    on = False
                                    continue
                                yy = sy(min(max(v, lo), hi))
                                p.lineTo(xx, yy) if on else p.moveTo(xx, yy)
                                on, any_ = True, True
                            if any_:
                                if colkey:
                                    c.setStrokeColor(
                                        self.zone_fill(colkey)
                                        if self.color
                                        else self.ink(0.0 if colkey == "low" else 0.45)
                                    )
                                else:
                                    c.setStrokeColor(self.zone_fill("target") if self.color else self.ink(0.25))
                                c.setLineWidth(width)
                                if not self.color and colkey == "high":
                                    c.setDash(2, 1.2)
                                c.drawPath(p, fill=0, stroke=1)
                                c.setDash()

                        seg(lambda v: True, None, 0.8)
                        seg(lambda v: v > HIGH, "high", 1.8)
                        seg(lambda v: v < LOW, "low", 1.8)
                        # the readings at the sensor's limit: marks at the bottom (LO) and top (HI) edges
                        self.limit_marks(x, w, yb, yb + chh, runs, 144)
                        self.text(x + w / 2, yb - 9, _RG["rg"].hour(12), MIN_PT, False, 0.45, "center")  # time format

            self.block(chh + gap + 6, row)
        with self.notes(title):
            for text in notes:
                self.note(text)

    # ------------------------------------------------------------------ the longer-term overview
    def overview(self):
        """Add the longer-term overview: the summary table, the period's profile and the 14-day periods."""
        d, r = self.d, self.d["period"]
        section = t("report.overview.title", period=d["label"])
        period = t(
            "report.overview.period",
            start=_date(r.get("period_start")),
            end=_date(r.get("period_end")),
            sensor_active=sensor_active_label(),
            active=_f(r.get("active")),
            count=r.get("days_with_data", 0),
            total=d["ndays"],
        )
        # the heading, its caption, the period line and the summary table stay together: never a heading alone at a
        # page foot (the period line as para() sets it, 8.5 pt lines of 1.32 leading, then 3 points)
        summary_h = 22 + 7 * 12 + 8
        period_h = len(self.wrap(period, 8.5, PAGE_W - 2 * M)) * 8.5 * 1.32 + 3
        # the period's note when it does not have enough data, after the period line: its first sentence in bold
        note = enough_note(r, d["label"])
        lead, _, rest = note.partition(". ")
        note_h = len(self.wrap(note, 8.5, PAGE_W - 2 * M - 10)) * 8.5 * 1.32 + 3 if note else 0
        self.heading(section, t("report.overview.caption"), keep=period_h + note_h + summary_h)
        self.para(period)
        if note:
            self.para(rest, bold_lead=lead + ".")

        def summary(y):
            """Draw the metrics of the period in two columns (two tables), with the consensus targets."""

            def row(key):
                """The label of a table row."""
                return _L(t(f"report.overview.row.{key}"))

            gri = (
                t("report.metrics.gri_value", value=_f(r["gri"], ""), zone=r["gri_zone"])
                if r.get("gri") is not None
                else "-"
            )
            none = t("report.overview.target.none")  # no consensus target ("-" is a value not available)
            per_day = r.get("per_day") or {}
            # (label, value, the key of its time per day (None: not a share of time), consensus target)
            rows = [
                (row("tir"), _f(r.get("tir")), "tir", _target("time_in_range")),
                (row("titr"), _f(r.get("titr")), "titr", none),
                (row("below_70"), _f(r.get("below_70")), "below_70", _target("time_low")),
                (row("below_54"), _f(r.get("very_low")), "very_low", _target("time_very_low")),
                (row("above_180"), _f(r.get("above_180")), "above_180", _target("time_high")),
                (row("above_250"), _f(r.get("very_high")), "very_high", _target("time_very_high")),
                (row("mean"), _gu(r.get("mean")), None, none),
                (row("sd"), _gu(r.get("sd")), None, none),
                (row("cv"), _f(r.get("cv")), None, t("report.overview.target.cv")),
                (row("gmi"), _gmi(r.get("gmi"), r.get("gmi_mmol_mol")), None, none),  # the A1C / GMI unit in use
                (row("gri"), gri, None, none),
                (row("min"), _gu(r.get("min")), None, none),
                (row("max"), _gu(r.get("max")), None, none),
            ]
            half = (len(rows) + 1) // 2
            for col, chunk in enumerate((rows[:half], rows[half:])):
                x = M + col * (PAGE_W - 2 * M) / 2
                # the right edges of the values and of the consensus targets, with room for a percentage and its time
                # per day ("100.0% (24 h)") between the longest label and the targets' header, also on A4
                vx, tx = x + X(189), x + X(262)
                with self.tag("Table", T=section):
                    with self.tag("TR"):
                        with self.th():
                            self.text(x, y - 9, t("report.overview.metric", unit=_unit()), MIN_PT, True, 0.3)
                        with self.th():
                            self.text(vx, y - 9, t("report.overview.value"), MIN_PT, True, 0.3, "right")
                        with self.th():
                            self.text(tx, y - 9, t("report.overview.consensus_target"), MIN_PT, True, 0.3, "right")
                    for i, (lab, v, key, target) in enumerate(chunk):
                        ty = y - 22 - i * 12
                        with self.tag("TR"):
                            with self.th("Row"):
                                self.text(x, ty, lab, 8.5)
                            with self.td():
                                # the value, and after it the time per day of a share of time
                                shown, said = _per_day(per_day.get(key)) if key else ("", "")
                                time_w = self.sw(" " + shown, FONT, 8) if shown else 0
                                self.text(vx - time_w, ty, v, 8.5, True, 0, "right")
                                if shown:
                                    self.text(vx, ty, shown, 8, False, 0.35, "right", said=said)
                            with self.td():
                                self.text(tx, ty, target, 8, False, 0.35, "right")

        self.block(summary_h, summary)
        self.agp_curve(r, t("report.profile.title", period=d["label"]), h=190, level=3)
        self.block_table()
        limit = _limit_note(r)
        with self.notes(section):
            self.note(t("report.overview.notes") + (f" {limit}" if limit else ""))
            self.note(t("report.overview.per_day_note", minutes=_d(MINUTES_PER_PERCENT_TEXT)))
            self.note(t("report.overview.profile_notes"))
            if self.d.get("blocks"):
                self.note(t("report.blocks.notes"))

    def block_table(self):
        """Add the table of consecutive 14-day periods, oldest first (its Notes: the overview's)."""
        bl = self.d.get("blocks") or []
        if not bl:
            return
        self.heading(t("report.blocks.title"), keep=40, level=3)
        cols = [
            (_L(t(f"report.blocks.col.{key}")), dx, al)
            for key, dx, al in (
                ("period", 0, "left"),
                ("tir", 128, "right"),
                ("titr", 166, "right"),
                ("below_70", 200, "right"),
                ("below_54", 232, "right"),
                ("above_180", 268, "right"),
                ("above_250", 302, "right"),
                ("mean", 342, "right"),
                ("cv", 374, "right"),
                ("gmi", 406, "right"),
                ("gri", 444, "right"),
                ("events", 480, "right"),
                ("data", 532, "right"),
            )
        ]

        def head(y):
            """Draw the head row of the 14-day periods: the column titles."""
            with self.tag("TR"):
                for title, dx, al in cols:
                    with self.th():
                        self.text(M + X(dx), y - 9, title, MIN_PT, True, 0.3, al)

        self.begin("Table", T=t("report.blocks.title"))
        self.block(13, head)
        for b in bl:

            def line(y, b=b):
                """Draw one 14-day block's row: its dates (a shorter first block with its length), its values and its
                sensor active."""
                lab = t(
                    "report.date_range",
                    start=_date(b["period_start"], short=True),
                    end=_date(b["period_end"], short=True),
                )
                if b["days"] < BLOCK_DAYS:
                    lab = tp("report.blocks.short", b["days"], period=lab, days=b["days"])
                vals = [
                    lab,
                    _f(b.get("tir")),
                    _f(b.get("titr")),
                    _f(b.get("below_70")),
                    _f(b.get("very_low")),
                    _f(b.get("above_180")),
                    _f(b.get("very_high")),
                    _g(b.get("mean")),
                    _f(b.get("cv")),
                    _gmi_short(b.get("gmi"), b.get("gmi_mmol_mol")),
                    "-" if b.get("gri") is None else f"{_f(b['gri'], '')} {b['gri_zone']}",
                    "-" if b.get("events") is None else str(b["events"]),  # none without enough data
                    _f(b.get("active")),
                ]
                with self.tag("TR"):
                    for (_t, dx, al), v in zip(cols, vals, strict=False):
                        with self.th("Row") if al == "left" else self.td():
                            self.text(M + X(dx), y - 9, v, 8, al == "left", 0 if al == "left" else 0.1, al)

            self.block(12, line)
        self.end("Table")
        unit_note = {A1C_MMOL_MOL: "report.blocks.gmi_mmol_mol", A1C_BOTH: "report.blocks.gmi_both"}.get(
            _RG["rg"].a1c_unit
        )
        # the short column heads, defined in the order of the columns
        legend = _legend("tir", "titr", "bands", "mean", "cv", "gmi", "gri", "events", "data_period")
        self.para(legend + (f" {t(unit_note)}" if unit_note else ""), size=MIN_PT, g=0.35)

    # ------------------------------------------------------------------ hypoglycemia, change, lab, definitions
    def lows(self):
        """Add the hypoglycemia section: time below range by night and day, the event counts and the events chart."""
        d = self.d
        lw, r = d.get("lows") or {}, d["period"]
        section = t("report.lows.title", period=d["label"])
        # the caption: the consensus event rule; the Notes: the cited context of time and events, the event method,
        # the night and day periods and the chart
        self.heading(section, _L(t("report.lows.caption")), keep=84)
        enough = r.get("enough_data", True)
        if not enough:  # the period's values need enough data: the line that says so takes the tables' place
            self.para(enough_short(r.get("active"), d["ndays"]))

        def table(y):
            """Draw the time-below-range table and the event counts."""
            rows = [
                _night_day_head(),
                (
                    _L(t("report.row.below_70")),
                    _f(lw.get("night_below_70")),
                    _f(lw.get("day_below_70")),
                    _f(r.get("below_70")),
                    _target("time_low"),
                ),
                (
                    _L(t("report.row.below_54")),
                    _f(lw.get("night_below_54")),
                    _f(lw.get("day_below_54")),
                    _f(r.get("very_low")),
                    _target("time_very_low"),
                ),
            ]
            self._night_day_rows(y, rows, section)
            ty = y - 10 - 3 * 14 - 6
            mean = lw.get("mean_duration_min")
            # the durations in words: "23 minutes / 1 hour 5 minutes (Wed 10/14 3:10 AM)"
            longest = (
                t("report.lows.longest", duration=_dur_words(lw["longest_min"]), when=lw["longest_when"])
                if lw.get("longest_min")
                else "-"
            )
            items = [
                (
                    _L(t("report.lows.level1")),
                    t(
                        "report.lows.level1_value",
                        count=lw.get("events", "-"),
                        per_week=_f(lw.get("events_per_week"), ""),
                        night=lw.get("events_night", "-"),
                    ),
                ),
                (
                    _L(t("report.lows.level2")),
                    t(
                        "report.lows.level2_value",
                        count=lw.get("events_54", "-"),
                        per_week=_f(lw.get("events_54_per_week"), ""),
                    ),
                ),
                (
                    t("report.lows.duration"),
                    t(
                        "report.lows.duration_value",
                        mean=_dur_words(mean),
                        longest=longest,
                    ),
                ),
            ]
            with self.tag("Table", T=section):
                for i, (a, b) in enumerate(items):
                    with self.tag("TR"):
                        with self.th("Row"):
                            self.text(M, ty - i * 13, a, 9)
                        with self.td():
                            self.text(M + X(170), ty - i * 13, b, 9, True)

        if enough:
            self.block(100, table)
        ev = lw.get("list") or []

        def scatter(y):
            """Draw the events by time of day: one marker per event, sized by duration."""
            c = self.c
            title = t("report.lows.chart_title")
            with self.tag("H3"):
                self.text(M, y - 10, title, 10, True)
            x, w, h = M + 30, PAGE_W - 2 * M - 40, 110
            yb = y - 34 - h
            # the scale holds both lines, 70 and 54 mg/dL, so that the Notes' description of the chart holds for every
            # chart
            vals = [e[2] for e in ev] + [LOW, VLOW]
            lo, hi = _fit(min(vals), max(vals))
            with self.tg.figure(_alt_events(ev), (x - 30, yb - 14, x + w, yb + h + 12)):
                sy = self.axes_box(x, yb, w, h, lo, hi)
                self.text(x - 26, yb + h + 4, _unit(), MIN_PT, False, 0.3)
                self.range_lines(x, w, sy, lo, hi)
                for e in ev:
                    rr = 2 + min(e[1], 120) / 30
                    # the marker's color is the event's level (e[4]: 2 when a level 2 event lies inside it)
                    c.setFillColor(self.zone_fill("vlow" if e[4] == 2 else "low", 0.85 if self.color else 1))
                    c.circle(x + e[3] / 24 * w, sy(e[2]), rr, fill=1, stroke=0)

        if ev:
            self.block(166, scatter)
        with self.notes(section):
            self.note(_L(t("report.lows.notes")))
            self.note(_ctx(ctx("both")))
            self.note(_ctx(ctx("event")))
            self.note(_L(t("report.lows.event.body")), t("report.lows.event.lead"))
            self.note(_ctx(ctx_night(_night(), _night(True))), t("report.lows.night.lead"))
            if ev:
                self.note(_scatter_caption(), t("report.lows.chart.lead"))

    def highs(self):
        """Add the hyperglycemia section, mirroring the hypoglycemia section, without events.

        Time above 180 and above 250 mg/dL by night and day (the same night), for the whole period, and the consensus
        targets, with the cited context of time above range.
        """
        d = self.d
        hi, r = d.get("highs") or {}, d["period"]
        section = t("report.highs.title", period=d["label"])
        self.heading(section, _L(t("report.highs.caption")), keep=50)
        enough = r.get("enough_data", True)
        if not enough:  # the period's values need enough data: the line that says so takes the table's place
            self.para(enough_short(r.get("active"), d["ndays"]))

        def table(y):
            """Draw the time-above-range table by night and day."""
            rows = [
                _night_day_head(),
                (
                    _L(t("report.row.above_180")),
                    _f(hi.get("night_above_180")),
                    _f(hi.get("day_above_180")),
                    _f(r.get("above_180")),
                    _target("time_high"),
                ),
                (
                    _L(t("report.row.above_250")),
                    _f(hi.get("night_above_250")),
                    _f(hi.get("day_above_250")),
                    _f(r.get("very_high")),
                    _target("time_very_high"),
                ),
            ]
            self._night_day_rows(y, rows, section)

        if enough:
            self.block(50, table)
        with self.notes(section):
            self.note(_ctx(ctx("tar")))
            self.note(t("report.highs.notes"))

    def _night_day_rows(self, y, rows, title):
        """Draw a table of the night, the day, the whole period and the consensus target (the first row: the head);
        title: the table's name (its section)."""
        xs = (M, M + X(230), M + X(330), M + X(430), PAGE_W - M)
        with self.tag("Table", T=title):
            for i, row in enumerate(rows):
                ty = y - 10 - i * 14
                with self.tag("TR"):
                    for k, (x, cell) in enumerate(zip(xs, row, strict=False)):
                        with self.th() if not i else self.th("Row") if k == 0 else self.td():
                            if cell:
                                self.text(
                                    x,
                                    ty,
                                    cell,
                                    9 if i else MIN_PT,
                                    bool(i) and k in (1, 2, 3),
                                    0 if i else 0.3,
                                    "left" if k == 0 else "right",
                                )

    # ------------------------------------------------------------------ data quality
    def quality(self):
        """Add the data quality section: sensor active, gaps, limit readings, sensor changes and the day strip."""
        d = self.d
        q, r = d.get("quality") or {}, d["period"]
        if not q:
            return
        section = t("report.quality.title", period=d["label"])
        self.heading(section, t("report.quality.caption"), keep=120)
        lim = q.get("limit_readings") or {}
        lim_parts = _limit_parts(lim, q.get("limit_seconds") or {})
        gaps = q.get("gaps") or 0
        gap_txt = (
            t("report.quality.gaps_none")
            if not gaps
            else t(
                "report.quality.gaps_value",
                count=gaps,
                duration=_dur_words(q.get("longest_gap_minutes")),
                **{"from": q.get("longest_gap_from"), "to": q.get("longest_gap_to")},
            )
        )
        rows = [
            (sensor_active_label(), _f(q.get("active"))),
            (
                t("report.quality.days_with_data"),
                t("report.count_of", count=r.get("days_with_data", 0), total=d["ndays"]),
            ),
            (t("report.quality.no_data_time"), _dur_words(q.get("no_data_minutes"))),
            (t("report.quality.gaps"), gap_txt),
            (
                t("report.quality.limit_readings"),
                t("report.list_separator").join(lim_parts) if lim_parts else t("report.quality.limit_none"),
            ),
        ]
        lx = M + X(205)
        lines = [(lab, self.wrap(v, 9, PAGE_W - M - lx)) for lab, v in rows]

        def body(y):
            """Draw the label and value rows."""
            ty = y - 10
            with self.tag("Table", T=section):
                for lab, vl in lines:
                    with self.tag("TR"):
                        with self.th("Row"):
                            self.text(M, ty, lab, 9)
                        with self.td():
                            for k, ln in enumerate(vl):
                                self.text(lx, ty - k * 11, ln, 9, True)
                    ty -= 11 * len(vl) + 3

        self.block(sum(11 * len(vl) + 3 for _, vl in lines) + 6, body)
        ev = q.get("sensor_events") or []
        events = [
            t("report.quality.sensor_change", start=e["start"])
            if e.get("warmup") is False
            else t("report.quality.warmup_limit", start=e["start"])
            if e.get("limit")
            else t("report.quality.warmup", start=e["start"], end=e["end"], duration=_dur_words(e.get("minutes")))
            if e.get("end")
            else t("report.quality.warmup_open", start=e["start"])
            for e in ev
        ]
        listed = t("report.list_separator").join(events) if events else t("report.quality.no_events")
        self.para(t("report.quality.sensor_events", events=listed), size=8.5)
        self.day_strip(q)
        with self.notes(section):
            self.note(t("report.quality.strip_note"))
            # the clause on the custom appendix only when the appendix is part of the report
            sufficiency = "sufficiency_custom" if d.get("include_custom_appendix") else "sufficiency"
            self.note(sensor_active_note() + " " + _ctx(ctx(sufficiency)))
            self.note(t("report.quality.sensor_events_method"))
            self.note(t("report.quality.gap_rule"))

    def day_strip(self, q):
        """Sensor active per day: one cell per day, in calendar rows from the first day of the week in use."""
        days = q.get("per_day") or []
        if not days:
            return
        cells = [None] * int(q.get("lead") or 0) + days
        dw = (PAGE_W - 2 * M) / 7
        rh = 22 if len(cells) <= 35 else 19
        rows = [cells[i : i + 7] for i in range(0, len(cells), 7)]

        def head(y, repeated=False):
            """Draw the title and the weekday names; at the top of a further page again, as an artifact."""
            title = t("report.quality.per_day", label=sensor_active_label())
            names = _RG["rg"].weekday_names()
            if repeated:
                with self.tg.artifact():
                    self.text(M, y - 10, title, 10, True)
                    for k, wd in enumerate(names):
                        self.text(M + k * dw + dw / 2, y - 23, wd, MIN_PT, True, 0.35, "center")
                return
            with self.tag("H3"):
                self.text(M, y - 10, title, 10, True)
            self.tg.open("Table", T=title)
            with self.tag("TR"):
                for k, wd in enumerate(names):
                    with self.th():
                        self.text(M + k * dw + dw / 2, y - 23, wd, MIN_PT, True, 0.35, "center")

        self.block(27, head)
        for row in rows:
            if self.y - rh < BOTTOM:  # the weekday names again at the top of the next page
                self.new_page()
                self.block(27, lambda y: head(y, repeated=True))

            def line(y, row=row):
                """Draw one calendar row of day cells (empty cells before the first day and after the last)."""
                c = self.c
                with self.tag("TR"):
                    for k in range(7):
                        cell = row[k] if k < len(row) else None
                        x = M + k * dw
                        with self.td():
                            if cell is None:
                                continue
                            c.setStrokeColor(self.ink(0.8))
                            c.setLineWidth(0.4)
                            a = cell.get("active")
                            if not a:  # no data that day: light gray (no assessment)
                                c.setFillColor(self.ink(0.93))
                                c.rect(x, y - rh, dw, rh, fill=1, stroke=1)
                            else:
                                c.rect(x, y - rh, dw, rh, fill=0, stroke=1)
                            # ink 0.40: at least 4.5:1 on the gray of a day without data
                            self.text(x + 3, y - 8.5, cell.get("label", ""), MIN_PT, False, 0.40)
                            self.text(x + dw - 4, y - rh + 5, _f(a), 9 if rh > 18 else 8, True, 0, "right")

            self.block(rh, line)
        self.end("Table")
        self.y -= 6

    # ------------------------------------------------------------------ the weekly trend
    def weekly_trend(self):
        """Add the weekly trend: three small bar charts (TIR, CV, mean) and the table of the weeks."""
        wk = self.d.get("weekly") or []
        if not wk:
            return
        n = len(wk)
        section = tp("report.weekly.title", n)
        self.heading(section, t("report.weekly.caption"), keep=150)
        charts = [
            (t("report.weekly.chart.tir"), "tir", lambda v: v),
            (t("report.weekly.chart.cv"), "cv", lambda v: v),
            (t("report.weekly.chart.mean", unit=_unit()), "mean", lambda v: v / MGDL_PER_MMOLL if _mm() else v),
        ]
        cw = (PAGE_W - 2 * M - 2 * 14) / 3
        ch = 70

        def mini(y):
            """Draw the three bar charts side by side, each with its title."""
            c = self.c
            for i, (title, key, conv) in enumerate(charts):
                x0 = M + i * (cw + 14)
                with self.tag("H3"):
                    self.text(x0, y - 9, title, 8, True, 0.2)
                xs = [x0 + 18 + (k + 0.5) * (cw - 18) / n for k in range(n)]
                vv = [conv(w[key]) if w.get(key) is not None else None for w in wk]
                got = [v for v in vv if v is not None]
                if not got:
                    continue
                lo, hi = (
                    (0.0, 100.0) if key == "tir" else _fit(min(got), max(got), 4 if _mm() and key == "mean" else 10)
                )
                if key != "tir":
                    lo = max(0.0, lo)
                yb = y - 16 - ch

                def sy(v, yb=yb, lo=lo, hi=hi):
                    """The y position of a value on the chart."""
                    return yb + (v - lo) / (hi - lo) * ch

                with self.tg.figure(_alt_weekly(key, got), (x0, yb - 12, x0 + cw, yb + ch)):
                    c.setStrokeColor(self.ink(0.6))
                    c.setLineWidth(0.5)
                    c.rect(x0 + 18, yb, cw - 18, ch, fill=0, stroke=1)
                    # one format per axis: the mean in mmol/L with one decimal on every label, whole numbers otherwise
                    digits = 1 if _mm() and key == "mean" else 0
                    for tick in (lo, (lo + hi) / 2, hi):
                        self.text(
                            x0 + 16,
                            sy(tick) - 2.5,
                            _RG["rg"].num(round_half_up(tick, digits), digits),
                            MIN_PT,
                            False,
                            0.4,
                            "right",
                        )
                    bw = max(1.5, (cw - 18) / n - 2)
                    c.setFillColor(self.ink(0.45))
                    for xx, v in zip(xs, vv, strict=False):
                        if v is not None:
                            c.rect(xx - bw / 2, sy(lo), bw, max(0.5, sy(v) - sy(lo)), fill=1, stroke=0)
                    self.text(x0 + 18, yb - 9, _date(wk[0]["week_end"], short=True), MIN_PT, False, 0.4)
                    self.text(x0 + cw, yb - 9, _date(wk[-1]["week_end"], short=True), MIN_PT, False, 0.4, "right")

        self.block(16 + ch + 16, mini)
        half = (n + 1) // 2
        colw = (PAGE_W - 2 * M) / 2
        cols = (
            (t("report.weekly.col.week"), 0, "left"),
            (t("report.weekly.col.tir"), 0.40, "right"),
            (t("report.weekly.col.cv"), 0.55, "right"),
            (t("report.weekly.col.mean", unit=_unit()), 0.80, "right"),
            (t("report.weekly.col.data"), 0.95, "right"),
        )
        no_data = t("report.weekly.no_data")

        def table(y):
            """Draw the weeks in two columns (two tables). A week without enough data shows its sensor active and a
            dash for each value; a week without data, one cell across the four value columns."""
            for ci in range(2):
                x = M + ci * colw
                with self.tag("Table", T=section):
                    with self.tag("TR"):
                        for title, f, al in cols:
                            with self.th():
                                self.text(x + f * colw, y - 9, title, MIN_PT, True, 0.3, al)
                    for k, w in enumerate(wk[ci * half : (ci + 1) * half]):
                        ty = y - 9 - (k + 1) * 11
                        with self.tag("TR"):
                            with self.th("Row"):
                                self.text(x, ty, _date(w["week_end"], short=True), 8, False, 0)
                            if w.get("active") is None:  # no data: one cell across the four value columns
                                with self.td(colspan=4):
                                    self.text(x + cols[1][1] * colw, ty, no_data, 8, False, 0.1, "right")
                                continue
                            vals = [_f(w.get("tir")), _f(w.get("cv")), _g(w.get("mean")), _f(w.get("active"))]
                            for (_title, f, al), v in zip(cols[1:], vals, strict=True):
                                with self.td():
                                    self.text(x + f * colw, ty, v, 8, False, 0.1, al)

        self.block(9 + (half + 1) * 11 + 4, table)
        self.para(_legend("tir", "cv", "mean", "data_week"), size=MIN_PT, g=0.35)
        with self.notes(section):
            self.note(tp("report.weekly.notes", n))

    def change(self):
        """Add the comparison with the previous period of the same length (when both periods have enough data).

        The 6-month report has no previous period (report.build_report): the section states why, as on the Report
        page; the two periods together would be twice the report's length. The periods are compared only when both
        have enough data, at least 70 % sensor active compared before rounding (enough_data, the rule of every period
        of the report); otherwise the section names the period that has less, with its sensor active.
        """
        d = self.d
        r, p = d["period"], d.get("previous")
        if p is None:
            retention = int(d["retention_days"])  # report.build_report always sets the retention in use
            self.heading(
                t("report.change.title"),
                t("report.change.six_months", days=2 * d["ndays"], retention=retention),
                keep=40,
            )
            return
        nd = d["ndays"]
        # both periods need enough data, at least 70% sensor active compared before rounding (enough_data; the rule of
        # every period): first this period, then the previous one
        this_ok = bool(r.get("enough_data"))
        have = this_ok and p.get("tir") is not None and bool(p.get("enough_data"))

        def short(d_):
            """A report date in the short numeric form."""
            return _date(d_, short=True)

        if have:
            subtitle = tp(
                "report.change.subtitle",
                nd,
                start=short(r.get("period_start")),
                end=short(r.get("period_end")),
                previous_start=short(p.get("period_start")),
                previous_end=short(p.get("period_end")),
            )
        elif not this_ok:
            subtitle = t("report.change.not_available_this", active=_f(r.get("active")))
        else:
            subtitle = t(
                "report.change.not_available",
                previous_start=short(p.get("period_start")),
                previous_end=short(p.get("period_end")),
                active=_f(p.get("active")),
            )
        self.heading(t("report.change.title"), subtitle, keep=40 if not have else 130)
        if not have:
            return

        def row(key):
            """The label of a table row."""
            return t(f"report.change.row.{key}")

        rows = [
            (row("tir"), "tir", "%"),
            (row("below_70"), "below_70", "%"),
            (row("below_54"), "very_low", "%"),
            (row("above_180"), "above_180", "%"),
            (row("mean"), "mean", ""),  # in the display unit (its own branch below)
            (row("gmi"), "gmi", "%"),
            (row("cv"), "cv", "%"),
            (row("gri"), "gri", ""),
            (sensor_active_label(), "active", "%"),
        ]

        def body(y):
            """Draw the rows: this period, the previous one and the difference."""
            # right edges of the value columns (room for GMI in both A1C units)
            xs = (M, M + X(250), M + X(370), PAGE_W - M)
            heads = (
                t("report.table.thresholds", unit=_unit()),  # the unit of the thresholds in the row labels
                t("report.change.this_period"),
                t("report.change.previous"),
                t("report.change.difference"),
            )
            self.tg.open("Table", T=t("report.change.title"))
            with self.tag("TR"):
                for k, title in enumerate(heads):
                    with self.th():
                        self.text(xs[k], y - 9, title, MIN_PT, True, 0.3, "left" if k == 0 else "right")
            for i, (lab, key, unit) in enumerate(rows):
                ty = y - 9 - (i + 1) * 13
                a, b = r.get(key), p.get(key)
                # every difference is that of the two values as shown, so that the row adds up and nothing is rounded
                # twice (a percentage beside a rule can be shown with two decimals: the difference keeps them)
                if key == "mean":  # glucose: the display unit, mmol/L one decimal, mg/dL whole numbers
                    cells = (_gu(a), _gu(b))
                    if a is None or b is None:
                        diff = None
                    elif _mm():
                        diff = f"{_signed(from_mgdl(a, UNIT_MMOLL) - from_mgdl(b, UNIT_MMOLL), 1)} {_unit()}"
                    else:
                        diff = f"{_signed(round_half_up(a) - round_half_up(b), 0)} {_unit()}"
                elif key == "gmi":  # the A1C / GMI unit in use
                    cells = (_gmi(a, r.get("gmi_mmol_mol")), _gmi(b, p.get("gmi_mmol_mol")))
                    diff = (
                        None
                        if a is None or b is None
                        else _a1c_diff(round_half_up(a - b, 1), _mm_diff(r.get("gmi_mmol_mol"), p.get("gmi_mmol_mol")))
                    )
                else:
                    cells = (_f(a, unit), _f(b, unit))
                    dn = None if a is None or b is None else round_half_up(a - b, max(_places(a), _places(b)))
                    dv = None if dn is None else _signed(dn, _places(dn))
                    diff = None if dv is None else dv + unit
                    if (
                        dv is not None and unit == "%"
                    ):  # in percentage points ("+1 percentage point", "+1.5 ... points")
                        diff = tp("report.change.points", abs(dn), 0 if float(dn).is_integer() else 1, value=dv)
                with self.tag("TR"):
                    with self.th("Row"):
                        self.text(M, ty, _L(lab), 9)
                    with self.td():
                        self.text(xs[1], ty, cells[0], 9, True, 0, "right")
                    with self.td():
                        self.text(xs[2], ty, cells[1], 9, False, 0.25, "right")
                    with self.td():
                        if diff is not None:
                            self.text(xs[3], ty, diff, 9, False, 0, "right")
            self.tg.close("Table")

        self.block(13 * (len(rows) + 1) + 10, body)
        with self.notes(t("report.change.title")):
            self.note(t("report.change.notes"))

    def lab(self):
        """Add the laboratory A1C results, each compared with the GMI of the 90 days before it."""
        lab = self.d.get("lab") or {}
        rows = lab.get("results") or []
        section = t("report.lab.title")
        self.heading(section, t("report.lab.caption"))
        if rows:
            self.lab_table(lab, rows)
        else:
            self.para(t("report.lab.none"))
        # the cited context of GMI versus laboratory A1C and the method of the comparison
        method = t("report.lab.method", days=int(self.d["retention_days"]))
        if _RG["rg"].a1c_unit == A1C_BOTH:  # the table cells have no room for the unit words (diff_cell)
            method += " " + t("report.lab.difference_both")
        with self.notes(section):
            self.note(t("report.lab.intro"))
            # the laboratory sentence names the results in this section only when there are any
            self.note(_ctx(ctx("lab_difference" if rows else "lab_difference_none")))
            self.note(_ctx(ctx("lab_ada")))
            self.note(method)

    def lab_table(self, lab: dict, rows: list[dict]):
        """Add the laboratory results, newest first, and their mean difference from the GMI."""

        rg = _RG["rg"]
        # columns (room for both A1C units); the A1C / GMI unit in use
        cx = (M, M + X(200), M + X(320), M + X(450), PAGE_W - M)

        def diff_cell(dp, dmm=None):
            """Return a difference for a table cell: "+0.3 percentage points" / "+3 mmol/mol" / "+0.3 (+3 mmol/mol)".

            dmm: the difference of the two values in mmol/mol when both are known. With both units the cell has no
            room for the words; the method text above the table names the unit (percentage points).
            """
            if rg.a1c_unit != A1C_BOTH:
                return _a1c_diff(dp, dmm)
            m_txt = f"{_signed(A1C_MMOL_PER_PCT * dp if dmm is None else dmm, 0)} mmol/mol"
            return f"{_signed(dp, 1)} ({m_txt})"

        def head(y):
            """Draw the head row of the laboratory results: the column titles."""
            with self.tag("TR"):
                for x, key, al in (
                    (cx[0], "date", "left"),
                    (cx[1], "a1c", "right"),
                    (cx[2], "gmi", "right"),
                    (cx[3], "difference", "right"),
                    (cx[4], "active", "right"),
                ):
                    with self.th():
                        self.text(x, y - 9, t(f"report.lab.col.{key}"), MIN_PT, True, 0.3, al)

        self.begin("Table", T=t("report.lab.title"))
        self.block(12, head)
        for rr in reversed(rows):

            def line(y, rr=rr):
                """Draw one laboratory result row."""
                g = rr.get("gmi_90d")
                with self.tag("TR"):
                    with self.th("Row"):
                        self.text(cx[0], y - 9, _date(rr.get("date")), 8.5)
                    with self.td():
                        self.text(cx[1], y - 9, _gmi(rr.get("a1c"), rr.get("a1c_mmol_mol")), 8.5, True, 0, "right")
                    if rr.get("no_cgm_comparison"):  # its 90 days are older than the retention
                        note = rr.get("comparison_note") or t("report.lab.no_comparison_short")
                        with self.td(colspan=3):
                            self.text(cx[1] + X(16), y - 9, note, 8.5, False, 0.25)
                        return
                    with self.td():
                        self.text(
                            cx[2],
                            y - 9,
                            _gmi(g, rr.get("gmi_90d_mmol_mol")) if g is not None else t("report.lab.gmi_low_data"),
                            8.5,
                            False,
                            0,
                            "right",
                        )
                    with self.td():
                        self.text(
                            cx[3],
                            y - 9,
                            diff_cell(
                                round_half_up(rr["a1c"] - g, 1),
                                _mm_diff(rr.get("a1c_mmol_mol"), rr.get("gmi_90d_mmol_mol")),
                            )
                            if g is not None
                            else "-",
                            8.5,
                            True,
                            0,
                            "right",
                        )
                    with self.td():
                        self.text(cx[4], y - 9, _f(rr.get("cgm_active_90d")), 8.5, False, 0.3, "right")

            self.block(12, line)
        self.end("Table")
        if lab.get("average_difference") is not None:
            self.y -= 4
            diff = _a1c_diff(lab["average_difference"], lab.get("average_difference_mmol_mol"))
            self.para(t("report.lab.mean_difference", value=diff), bold_lead=t("report.lab.mean_difference_lead"))

    def about(self):
        """Add About This Report: the scope and the general method (the data sources, the report period, the data and
        their gaps, the readings at the sensor's limit, the rounding). Each section's own explanation is in its
        Notes, not repeated here.

        Each item is its lead and its text (report.about.<item>), which the Report page shows with the same words.
        """
        self.heading(t("report.about.title"), keep=60)

        def body(key):
            """The body text of an About section."""
            return t(f"report.about.{key}.body")

        items = [
            # the disclaimer, in full and once in the report (const.DISCLAIMER), its first sentence in bold
            (DISCLAIMER_LEAD, DISCLAIMER_PLAIN.removeprefix(DISCLAIMER_LEAD).strip()),
            (t("report.about.report.lead"), report_scope()),
            (t("report.about.forms.lead"), _ctx(ctx("forms")) + " " + body("forms")),
            # the standard statement, long form (not on page 1), in-text citations in APA 7th edition author-date style
            (t("report.about.statement.lead"), t("report.statement")),
            data_sources(self.d),
            (t("report.about.period.lead"), body("period")),
            (t("report.about.length.lead"), body("length")),
            (t("report.about.data.lead"), body("data")),
            (t("report.about.limit.lead"), body("limit")),
            (t("report.about.enough.lead"), body("enough")),
            (t("report.about.rounding.lead"), body("rounding")),
            # the key of the unit symbols of the durations in the event summary table and beside the percentages
            (t("report.about.durations.lead"), body("durations")),
            (t("report.about.not_included.lead"), body("not_included")),
        ]
        if _RG["rg"].a1c_unit in (A1C_MMOL_MOL, A1C_BOTH):  # only when mmol/mol is shown
            items.append(
                (
                    t("report.about.mmol_mol.lead"),
                    _dx(t("report.about.mmol_mol.body", factor=f"{A1C_MMOL_PER_PCT}", offset=f"{A1C_PCT_OFFSET}")),
                )
            )
        for lead, text in items:
            self.para(text, bold_lead=lead, size=8)

    # ------------------------------------------------------------------ the optional appendices
    def custom_appendix(self):
        """Add the appendix "Custom Ranges and Goals" when chosen (include_custom_appendix).

        The statistics with the ranges and goals in the settings, labeled as the user's own settings; met / not met
        only under the 14-day / 70 % rule.
        """
        d = self.d
        ca = d.get("custom_appendix")
        if not d.get("include_custom_appendix") or not ca:
            return
        rg, st, cons = ca.get("ranges") or {}, ca.get("stats") or {}, d["period"]
        section = t("report.custom.title", period=d["label"])
        self.heading(section, t("report.custom.caption"), keep=120)
        ranges_txt = t(
            "report.custom.ranges",
            unit=_unit(),
            **{k: _g(rg.get(k)) for k in ("very_low", "low", "tight_low", "tight_high", "high", "very_high")},
        )
        self.para(
            t(
                "report.custom.ranges_text" if ca.get("ranges_differ") else "report.custom.ranges_text_equal",
                ranges=ranges_txt,
                sensor_active=sensor_active_label(),
                active=_f(st.get("active")),
            ),
            bold_lead=t("report.custom.ranges_lead"),
            size=8.5,
        )
        enough = st.get("enough_data", True)
        if not enough:  # the period's values need enough data: the line that says so takes the table's place
            self.para(enough_short(st.get("active"), d["ndays"]))

        def below(k):
            """The band below a limit."""
            return t("report.custom.band.below", value=_g(rg.get(k)))

        def above(k):
            """The band above a limit."""
            return t("report.custom.band.above", value=_g(rg.get(k)))

        def span(a, b):
            """The band between two limits."""
            return t("report.custom.band.between", low=_g(rg.get(a)), high=_g(rg.get(b)))

        band = {
            "tir": span("low", "high"),
            "titr": span("tight_low", "tight_high"),
            "below_low": below("low"),
            "very_low": below("very_low"),
            "above_high": above("high"),
            "very_high": above("very_high"),
        }
        cons_key = {
            "tir": "tir",
            "titr": "titr",
            "below_low": "below_70",
            "very_low": "very_low",
            "above_high": "above_180",
            "very_high": "very_high",
            "cv": "cv",
            "gmi": "gmi",
        }
        ops = {">": "gt", ">=": "ge", "<": "lt", "<=": "le"}

        def goal_txt(row):
            """Return a goal as text ("more than 70%", "36% or less", "no goal set")."""
            if row.get("goal") is None:
                return t("report.custom.no_goal")
            # narrow column: the main unit; the GMI goal in mmol/mol by the GMI rule (report.custom_appendix)
            v = _gmi_main(row["goal"], row.get("goal_mmol_mol")) if row["key"] == "gmi" else _f(row["goal"])
            op = ops.get(row["goal_op"])
            return t(f"report.custom.goal.{op}", value=v) if op else v

        def val(key, src):
            """Return a statistic of `src` as text (GMI in the A1C unit in use)."""
            v = src.get(key)
            return _gmi(v, src.get("gmi_mmol_mol")) if key == "gmi" else _f(v)

        def own(row, key):
            """Return the row's value as text: a percentage as report.custom_appendix rounded it on its side of the
            goal (69.97% beside a goal of at least 70%, so that the number agrees with "not met"); the GMI by its own
            rule."""
            return val(key, st) if key == "gmi" else _f(row.get("value"))

        cols = (
            (M, t("report.custom.col.metric"), "left"),
            (M + X(250), t("report.custom.col.value"), "right"),
            (M + X(360), t("report.custom.col.goal"), "right"),
            (M + X(445), t("report.custom.col.result"), "right"),
            (PAGE_W - M, t("report.custom.col.consensus"), "right"),
        )

        def head(y):
            """Draw the head row of the custom goals: the column titles."""
            with self.tag("TR"):
                for x, title, al in cols:
                    with self.th():
                        self.text(x, y - 9, title, MIN_PT, True, 0.3, al)

        self.begin("Table", T=section)
        self.block(13, head)
        # the goals are the settings: listed also without enough data, with a dash for each value of the period
        for row in ca.get("rows") or []:
            k = next(vk for gk, vk in R_GOAL_ROWS if gk == row["key"])

            def line(y, row=row, k=k):
                """Draw one goal's row: its label, the value, the goal, met / not met and the consensus value."""
                lab = t("report.custom.row_label", label=row["label"], band=band[k]) if k in band else row["label"]
                res = (
                    t("report.mark.met" if row["met"] else "report.mark.not_met")
                    if row["met"] is not None
                    else ("-" if row.get("goal") is None else t("report.custom.not_compared"))
                )
                vals = (lab, own(row, k), goal_txt(row), res, val(cons_key[k], cons))
                with self.tag("TR"):
                    for (x, _, al), v in zip(cols, vals, strict=False):
                        with self.th("Row") if x == cols[0][0] else self.td():
                            self.text(
                                x, y - 9, v, 8.5, al == "right" and x == cols[1][0], 0 if x != cols[4][0] else 0.3, al
                            )

            self.block(12, line)
        self.end("Table")
        compared = t("report.custom.compared_note" if ca.get("comparable") else "report.custom.not_compared_note")
        with self.notes(section):
            self.note(t("report.custom.notes"))
            self.note(compared + " " + t("report.custom.note"))

    def research_appendix(self):
        """Add the appendix "Measures Defined for Research" when chosen (include_research_appendix).

        The text and method of docs/SYSTEM_DESIGN.md section 6.4, and the count, total duration and longest extended
        high-glucose event; no color, no target.
        """
        d = self.d
        ra = d.get("research_appendix")
        if not d.get("include_research_appendix") or ra is None:
            return
        section = t("report.research.title", period=d["label"])
        self.heading(section, _ctx(t("report.research.caption")), keep=120)
        if ra.get("events") is None:  # the period's values need enough data
            self.para(enough_short(d["period"].get("active"), d["ndays"]))
        events_title = t("report.research.events_title")
        self.need(9.5 * 1.32 + 2)
        y0 = self.y

        def events_heading():
            """Draw the title of the extended high-glucose events (H3)."""
            with self.tag("H3"):
                self.text(M, y0 - 9.5, events_title, 9.5, True, 0.15)

        self.draw(events_heading)
        self.y -= 9.5 * 1.32 + 3
        longest = ra.get("longest_minutes")
        rows = [
            (t("report.research.events"), "-" if ra.get("events") is None else str(ra["events"])),
            (t("report.research.total"), _dur_words(ra.get("total_minutes")) if ra.get("events") else "-"),
            (
                t("report.research.longest"),
                t("report.research.longest_value", duration=_dur_words(longest), when=ra.get("longest_when"))
                if longest
                else "-",
            ),
        ]

        def table(y):
            """Draw the counts of the extended high-glucose events."""
            with self.tag("Table", T=events_title):
                for i, (a, b) in enumerate(rows):
                    with self.tag("TR"):
                        with self.th("Row"):
                            self.text(M, y - 10 - i * 13, a, 9)
                        with self.td():
                            self.text(M + X(170), y - 10 - i * 13, b, 9, True)

        self.block(13 * len(rows) + 6, table)
        with self.notes(section):
            self.note(_ctx(t("report.research.intro")), t("report.research.intro_lead"))
            self.note(_ctx(t("report.research.definition")), t("report.research.definition_lead"))
            self.note(_ctx(t("report.research.no_standard", ada_a=ADA_A)), t("report.research.no_standard_lead"))
            self.note(t("report.research.method_note"), t("report.research.method_lead"))
            self.begin("L")
            for i in range(1, RESEARCH_METHOD_STEPS + 1):
                self.note(_dx(t(f"report.research.method.{i}")), t("report.research.step", number=i), item=True)
            self.end("L")
            self.note(t("report.research.limits"), t("report.research.limits_lead"))

    def references(self):
        """Add the reference list (APA 7): the works cited in this report, before the last section.

        Hanging indent; journal and volume in italics. The list is a list (L), one item per work.
        """
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import Paragraph

        mmol = _RG["rg"].a1c_unit in (A1C_MMOL_MOL, A1C_BOTH)  # the only place hoelzel2004 is cited (about)
        style = ParagraphStyle(
            "gc_ref",
            fontName=FONT,
            fontSize=MIN_PT,
            leading=10.2,
            leftIndent=18,
            firstLineIndent=-18,
            textColor=self.ink(0.15),
        )
        width = PAGE_W - 2 * M
        self.heading(t("report.references.title"), t("report.references.subtitle"), keep=40)
        self.begin("L")
        for key, text in REFERENCES:
            if key == "hoelzel2004" and not mmol:
                continue
            p = Paragraph(text, style)
            _, h = p.wrap(width, 10000)
            self.need(h + 3)
            y = self.y

            def item(p=p, y=y, h=h):
                """Draw one reference as a list item."""
                with self.tag("LI"), self.tag("LBody"):
                    p.drawOn(self.c, M, y - h)

            self.draw(item)
            self.y -= h + 3
        self.end("L")

    def license_section(self):
        """Add the last section of every report: the license and its notices (the copyright and the GNU AGPL
        notice)."""
        version = (self.d.get("integration_version") or "").strip()
        # the copyright and the GNU AGPL notice are legal texts and stay in English (const.py)
        paras = [
            t("report.license.licensed", license=LICENSE_NAME),
            f"GlucoCraft {version} · {COPYRIGHT}" if version else f"GlucoCraft · {COPYRIGHT}",
            *GNU_NOTICE,
            t("report.license.url", url=LICENSE_URL),
        ]
        # the whole section stays on one page (the last page): its height as para() sets it (no paragraph has a lead)
        lead = 8.5 * 1.32
        height = sum(len(self.wrap(text, 8.5, PAGE_W - 2 * M)) * lead + 3 for text in paras)
        self.heading(t("report.license.title"), keep=int(height) + 2)
        for text in paras:
            self.para(text, size=8.5)

    # ------------------------------------------------------------------ full report pages
    def event_summary(self):
        """Add the full report's summary of the hypoglycemia events: one table, a column per period (14 / 30 / 90 / 180
        days inside the report's period, report.low_event_summary) headed by its sensor active, and rows of counts and
        durations; no lowest value, no event list, no targets, no colors."""
        cols = self.d.get("low_events") or []
        if not cols:
            return
        rg = _RG["rg"]
        section = t("report.events.title")
        xs = [M + X(300 + 77 * k) for k in range(len(cols))]  # the right edges of the period columns

        def block_label(k):
            """The 3-hour block k of the local clock in the time format in use: "12:00 AM–2:59 AM"."""
            start, end = rg.clock(datetime(2000, 1, 1, 3 * k)), rg.clock(datetime(2000, 1, 1, 3 * k + 2, 59))
            return t("report.events.row.block", time=t("report.time_range", start=start, end=end))

        def dur(v):
            """A duration in whole minutes with the unit symbols and its spoken form in words, or "-" without events
            (the table's columns have no room for the words; About This Report gives the key of the symbols)."""
            return "-" if v is None else (_dur(v), _dur_words(v))

        def count(c, key, k=None):
            """A count of a column, or "-" for a period without enough data."""
            if not c.get("enough_data", True):
                return "-"
            return str(c[key] if k is None else c[key][k])

        def value(c, key, fn):
            """A value of a column through fn, or "-" for a period without enough data."""
            return fn(c[key]) if c.get("enough_data", True) else "-"

        rows = [
            (_L(t("report.events.row.events")), [count(c, "events") for c in cols]),
            (_L(t("report.events.row.level2")), [count(c, "level2") for c in cols]),
            (t("report.events.row.per_day"), [value(c, "per_day", lambda v: _f(v, "")) for c in cols]),
            (t("report.events.row.median"), [value(c, "median_min", dur) for c in cols]),
            (t("report.events.row.longest"), [value(c, "longest_min", dur) for c in cols]),
            *((block_label(k), [count(c, "blocks", k) for c in cols]) for k in range(8)),
            (t("report.events.row.night", time=_night()), [count(c, "night") for c in cols]),
        ]
        lead = 12
        height = (len(rows) + 2) * lead + 10
        # the table is one block: the heading and its caption stay with all of it, never alone at a page foot
        self.heading(section, t("report.events.caption"), keep=height)

        def table(y):
            """Draw the table: the periods and their sensor active (the head), then one row per count."""
            c = self.c
            with self.tag("Table", T=section):
                with self.tag("TR"):
                    with self.th():
                        self.text(M, y - 9, t("report.events.period"), MIN_PT, True, 0.3)
                    for x, col in zip(xs, cols, strict=True):
                        with self.th():
                            self.text(x, y - 9, t(f"report.period.{col['days']}"), MIN_PT, True, 0.3, "right")
                # the second head row: each period's sensor active, a column header of its period's column
                with self.tag("TR"):
                    with self.th():
                        self.text(M, y - 9 - lead, sensor_active_label(), 8.5, False, 0.3)
                    for x, col in zip(xs, cols, strict=True):
                        with self.th():
                            self.text(x, y - 9 - lead, _f(col["active"]), 8.5, False, 0.3, "right")
                with self.tg.artifact():  # the rule under the head: decoration
                    c.setStrokeColor(self.ink(0.6))
                    c.setLineWidth(0.5)
                    c.line(M, y - 2 * lead - 3, PAGE_W - M, y - 2 * lead - 3)
                for i, (label, values) in enumerate(rows):
                    ty = y - 9 - (i + 2) * lead - 4
                    with self.tag("TR"):
                        with self.th("Row"):
                            self.text(M, ty, label, 8.5)
                        for x, v in zip(xs, values, strict=True):
                            shown, said = v if isinstance(v, tuple) else (v, None)
                            with self.td():
                                self.text(x, ty, shown, 8.5, True, 0, "right", said=said)

        self.block(height, table)
        with self.notes(section):
            self.note(_L(t("report.events.notes", night=_night())))

    def patterns(self):
        """Add the daily patterns: the mean glucose of each two hours of the day (a table of one row), each cell shaded
        by the zone of its mean (zone_key: the inclusive ranges of every statistic, so a mean of 180 mg/dL is in
        range)."""
        days = self.d.get("days") or {}
        pat = days.get("pattern_2h") or []
        hrs = [_RG["rg"].hour(h) for h in range(0, 24, 2)]  # "12am" / "00:00", the time format in use
        section = t("report.patterns.title", period=self.d["label"])
        self.heading(section, _L(t("report.patterns.caption", unit=_unit())))
        enough = days.get("enough_data", True)
        if enough:
            self.para(t("report.patterns.mean", mean=_gu(days.get("mean"))))
        else:  # the period's values need enough data: the line that says so takes the table's place
            self.para(enough_short(self.d["period"].get("active"), self.d["ndays"]))
        # a header cell is read as its full time ("12:00 AM" for "12am")
        spoken = [_RG["rg"].clock(datetime(2000, 1, 1, h)) for h in range(0, 24, 2)]

        def body(y):
            """Draw the twelve two-hour cells: the hours as the header row, the means below them."""
            c = self.c
            cw = (PAGE_W - 2 * M) / 12
            with self.tag("Table", T=section):
                with self.tag("TR"):
                    for i, hname in enumerate(hrs):
                        x = M + i * cw
                        v = pat[i] if i < len(pat) else None
                        key = None if v is None else zone_key(v)
                        if key and key != "target":
                            self.zone_rect(x + 1, y - 34, cw - 2, 16, key, 0.35 if self.color else 1)
                        c.setStrokeColor(self.ink(0.7))
                        c.setLineWidth(0.4)
                        c.rect(x + 1, y - 34, cw - 2, 30, fill=0, stroke=1)
                        with self.th(ActualText=spoken[i]):
                            self.text(x + cw / 2, y - 13, hname, MIN_PT, False, 0.3, "center")
                with self.tag("TR"):
                    for i in range(len(hrs)):
                        with self.td():
                            self.text(
                                M + i * cw + cw / 2, y - 29, _g(pat[i] if i < len(pat) else None), 10, True, 0, "center"
                            )

        if enough:
            self.block(44, body)
        with self.notes(section):
            self.note(t("report.patterns.notes"))

    def day_table(self):
        """Add the daily summary: one row per day (mean, events, sensor active), in up to three columns."""
        week = (self.d.get("days") or {}).get("week") or []
        if not week:
            return
        section = t("report.days.title", period=self.d["label"])
        self.heading(section, t("report.days.caption"))
        cols = 1 if len(week) <= 7 else 2 if len(week) <= 31 else 3
        colw = (PAGE_W - 2 * M) / cols
        lead = 12
        i = 0
        while i < len(week):
            self.need(lead * 3)
            fit = int((self.y - BOTTOM - 6) // lead) - 1
            n = min(len(week) - i, fit * cols)
            per = -(-n // cols)
            chunk = week[i : i + n]

            def body(y, chunk=chunk, per=per):
                """Draw the day rows that fit on this page: one table per column."""
                for ci in range(cols):
                    x = M + ci * colw
                    rows = chunk[ci * per : (ci + 1) * per]
                    with self.tag("Table", T=section):
                        with self.tag("TR"):
                            for key, f, al in DAY_TABLE_COLUMNS:
                                with self.th():
                                    self.text(x + colw * f, y - 9, t(f"report.days.col.{key}"), MIN_PT, True, 0.3, al)
                        for k, w in enumerate(rows):
                            ty = y - 9 - (k + 1) * lead
                            with self.tag("TR"):
                                with self.th("Row"):
                                    self.text(x, ty, w[0], 8)
                                with self.td():
                                    self.text(x + colw * 0.55, ty, _gu(w[1]), 8, True, 0, "right")
                                with self.td():  # the day's events; a dash without enough data
                                    events = "-" if w[2] is None else str(w[2])
                                    self.text(x + colw * 0.75, ty, events, 8, bool(w[2]), 0, "right")
                                with self.td():
                                    self.text(x + colw * 0.95, ty, _f(w[3]), 8, False, 0.3, "right")

            self.block((per + 1) * lead + 6, body)
            i += n
        with self.notes(section):
            self.note(_L(t("report.days.notes")))

    def daily_log(self):
        """Add the daily log: one row per day, one cell per hour with the hour's maximum and minimum."""
        days = self.d.get("days") or {}
        log, week = days.get("log") or [], days.get("week") or []
        if not log:
            return
        rg = _RG["rg"]
        notes = _L(t("report.log.notes_color" if self.color else "report.log.notes_gray"))
        if rg.time == "24":  # 0-23
            hl = [str(h) for h in range(24)]
        else:  # 12a, 1 ... 5, 6a, 7 ... 11, 12p, 1 ... 5, 6p, 7 ... 11: the half of the day at 12 and 6

            def half(h):
                """An hour label at 12 and 6 with its half of the day."""
                return t("report.log.hour_am" if h < 12 else "report.log.hour_pm", hour=h % 12 or 12)

            hl = [half(h) if h % 6 == 0 else str(h % 12) for h in range(24)]
            # the key of the shortened hour labels, with each one's time in the time format in use
            clock = {f"t{h}": rg.clock(datetime(2000, 1, 1, h)) for h in (0, 6, 12, 18)}
            notes += " " + t("report.log.hours_12", **{f"h{h}": hl[h] for h in (0, 6, 12, 18)}, **clock)
        section = t("report.log.title", period=self.d["label"])
        self.heading(section, _L(t("report.log.caption")))
        lx, cw, rh = M + 52, (PAGE_W - 2 * M - 52) / 24, 21
        # a header cell is read as its full time ("1:00 AM" for "1")
        spoken_hours = [rg.clock(datetime(2000, 1, 1, h)) for h in range(24)]
        i = 0
        self.begin("Table", T=section)
        while i < len(log):
            self.need(rh * 3)
            n = min(len(log) - i, int((self.y - BOTTOM - 12) // rh) - 1)
            idx = list(range(i, i + n))

            def body(y, idx=idx, first=not i):
                """Draw the hour labels (the header row; repeated on a further page as an artifact) and the day rows
                that fit on this page."""
                if first:
                    with self.tag("TR"):
                        with self.th():  # the column of the days
                            self.text(M, y - 8, t("report.days.col.day"), MIN_PT, True, 0.35)
                        for hcol, hour in enumerate(hl):
                            with self.th(ActualText=spoken_hours[hcol]):
                                self.text(lx + hcol * cw + cw / 2, y - 8, hour, MIN_PT, False, 0.35, "center")
                else:
                    with self.tg.artifact():
                        for hcol, hour in enumerate(hl):
                            self.text(lx + hcol * cw + cw / 2, y - 8, hour, MIN_PT, False, 0.35, "center")
                for k, di in enumerate(idx):
                    ty = y - 12 - (k + 1) * rh
                    with self.tag("TR"):
                        with self.th("Row"):
                            self.text(M, ty + 7, week[di][0] if di < len(week) else "", MIN_PT, True, 0.15)
                        # [minimum, maximum, a LO reading, a HI reading] per hour (report.day_pages)
                        mn_row, mx_row, lo_row, hi_row = log[di]
                        for h in range(24):
                            with self.td():
                                self.log_cell(lx + h * cw, ty, cw, rh, mn_row[h], mx_row[h], lo_row[h], hi_row[h])

            self.block(12 + (n + 1) * rh, body)
            i += n
        self.end("Table")
        with self.notes(section):
            self.note(notes)

    def log_cell(self, x, ty, cw, rh, mn, mx, lo, hi):
        """Draw one hour of the daily log: its zone's background, the maximum above and the minimum below."""
        c = self.c
        if mn is None and not lo and not hi:
            return
        # the measured values, and LO / HI as a category: the hour's minimum or maximum in words, never a number
        key = (
            "vlow"
            if lo or (mn is not None and mn < VLOW)
            else "low"
            if mn is not None and mn < LOW
            else "vhigh"
            if hi or (mx is not None and mx > VHIGH)
            else "high"
            if mx is not None and mx > HIGH
            else None
        )
        if key:
            self.zone_rect(x + 0.4, ty, cw - 0.8, rh - 1, key, 0.9 if self.color else 1)
        else:
            c.setFillColor(self.ink(0.9))
            c.rect(x + 0.4, ty, cw - 0.8, rh - 1, fill=1, stroke=0)
        dark = key == "vlow" or (not self.color and key in ("low", "vhigh"))
        g = 1.0 if dark else 0.0
        if not self.color and key in HATCHED:
            c.setFillColor(self.ink(1.0))
            c.rect(x + 2, ty + 1.5, cw - 4, rh - 4, fill=1, stroke=0)
            g = 0.0
        # never the stored limit as a number, and never a limit reading hidden behind a measured value
        top = t("report.limit.high") if hi else _g(mx) if mx is not None else ""
        bottom = t("report.limit.low") if lo else _g(mn) if mn is not None else ""
        self.text(x + cw / 2, ty + rh - 9, top, MIN_PT, False, g, "center")
        self.text(x + cw / 2, ty + 2.5, bottom, MIN_PT, False, g, "center")

    def monthly(self):
        """Add the monthly summary: a calendar per month with the mean glucose and the events of each day."""
        months = self.d.get("monthly") or []
        if not months:
            return
        section = t("report.monthly.title", period=self.d["label"])
        self.heading(section, t("report.monthly.caption"), keep=27 + 6 * 24 + 12)
        cw2 = (PAGE_W - 2 * M - 16) / 2
        dh = 24
        for pair in range(0, len(months), 2):

            def body(y, pair=pair):
                """Draw two months side by side, each a heading and a calendar table."""
                for j, m in enumerate(months[pair : pair + 2]):
                    x0 = M + j * (cw2 + 16)
                    with self.tag("H3"):
                        self.text(x0, y - 11, m["month"], 10, True)
                    dw = cw2 / 7
                    cells = [None] * _RG["rg"].lead_days(m["first_weekday"]) + m["days"]
                    # the cells with a frame: the empty ones before the first day shown and the days
                    framed = len(cells)
                    cells += [None] * (-len(cells) % 7)  # whole weeks: every row has seven cells
                    with self.tag("Table", T=m["month"]):
                        # the week starts on the first day of the week in use
                        with self.tag("TR"):
                            for k, wd in enumerate(_RG["rg"].weekday_names()):
                                with self.th():
                                    self.text(x0 + k * dw + dw / 2, y - 23, wd, MIN_PT, True, 0.35, "center")
                        for start in range(0, len(cells), 7):
                            with self.tag("TR"):
                                for n in range(start, start + 7):
                                    self.month_cell(x0, y, dw, dh, n, cells[n], framed=n < framed)

            self.block(27 + 6 * dh + 12, body)
        with self.notes(section):
            self.note(_L(t("report.monthly.notes")))

    def month_cell(self, x0, y, dw, dh, n, cell, framed=True):
        """Draw cell n of a month calendar (a table cell): its frame, the day, the mean and the events."""
        c = self.c
        col, rw = n % 7, n // 7
        x, yy = x0 + col * dw, y - 27 - (rw + 1) * dh
        with self.td():
            if not framed:  # after the last day shown
                return
            c.setStrokeColor(self.ink(0.8))
            c.setLineWidth(0.4)
            c.rect(x, yy, dw, dh, fill=0, stroke=1)
            if not cell:
                return
            self.text(x + 2, yy + dh - 8.5, str(cell[0]), MIN_PT, False, 0.45)
            self.text(x + dw / 2, yy + 5, _g(cell[1]), 8.5, True, 0, "center")
            if cell[2]:  # the number of events, at the upper right (the subtitle names it; no room for a word)
                self.text(x + dw - 2, yy + dh - 8.5, str(cell[2]), MIN_PT, True, 0.15, "right")


def _creation_time(canvas, epoch: int) -> None:
    """Give the document the report's creation time (whole minutes, UTC) as its creation date, and derive its file
    identifier from it, so that two renders of the same report data are the same bytes (reportlab otherwise stamps the
    clock time of the render to the second). It sets ReportLab internals (listed in pdf_tags.py's docstring)."""
    from hashlib import md5

    from reportlab.pdfbase.pdfdoc import TimeStamp

    doc = canvas._doc
    stamp = TimeStamp(invariant=True)  # UTC, no zone offset; its time is replaced below
    stamp.t = float(epoch)
    stamp.lt = time.gmtime(epoch)
    stamp.YMDhms = tuple(stamp.lt)[:6]
    doc._timeStamp = stamp
    doc.signature = md5()
    doc.signature.update(b"a reportlab document")
    doc.signature.update(ascii(stamp.t).encode())


def render(data, color=True, full=False):
    """Render the CGM Report and return (pdf_bytes, page_count); full adds the daily pages. With data["created_ts"]
    (report_service) the PDF's creation date is the report's creation time, and the same data render to the same
    bytes. One render at a time (RENDER_LOCK)."""
    with RENDER_LOCK:
        return _render(data, color, full)


def _render(data, color, full):
    """Render the report (render(), under RENDER_LOCK)."""
    rp = Report(data, color)
    if data.get("created_ts"):
        _creation_time(rp.c, int(data["created_ts"]))
    rp.standard_page()
    # the daily glucose profiles of the 14 days; their Notes describe the lines and the band, and why the profiles are
    # shown with the glucose profile
    rp.daily_profiles(
        data["standard"],
        t("report.daily.title", period=t("report.period.14")),
        (_L(t("report.daily.notes")), _ctx(ctx("patterns"))),
    )
    if data["ndays"] > STANDARD_DAYS:
        rp.overview()
    if data.get("weekly"):  # the 26-week weekly trend: 90 days and 6 months
        rp.weekly_trend()
    rp.lows()
    rp.highs()
    rp.change()  # the 6-month report has no previous period: the section says why
    rp.quality()
    if full:
        rp.event_summary()  # the full report's pages follow the summary's
        if data["ndays"] > STANDARD_DAYS:
            rp.daily_profiles(
                data["period"], t("report.daily.title", period=data["label"]), (t("report.daily.notes_period"),)
            )
        rp.patterns()
        rp.day_table()
        rp.daily_log()
        rp.monthly()
    rp.lab()
    rp.about()
    rp.custom_appendix()  # the optional appendices, only when chosen
    rp.research_appendix()
    rp.references()
    rp.license_section()  # always the last section
    return rp.finish()


# ---------------------------------------------------------------------- IPP
# The numbers of the IPP encoding (RFC 8010) and of the Print-Job operation (RFC 8011) that the request and the reply
# use. A request starts with the version, the operation and the request id; its attributes follow in groups, each
# opened by a delimiter tag, and every attribute starts with the value tag of its syntax.
IPP_VERSION = (2, 0)  # the request's version: IPP 2.0 (major, minor)
IPP_REPLY_MAJOR_VERSIONS = (1, 2)  # the first byte of a reply that is IPP: major version 1 or 2
IPP_PRINT_JOB = 0x0002  # operation-id Print-Job
IPP_REQUEST_ID = 1  # the one request of the connection
# delimiter tags: the operation attributes, the job attributes, the end of the attributes; below 0x10 every tag is one
IPP_TAG_OPERATION, IPP_TAG_JOB, IPP_TAG_END = 0x01, 0x02, 0x03
IPP_DELIMITER_BELOW = 0x10
# value tags
IPP_TAG_INTEGER = 0x21
IPP_TAG_NAME = 0x42  # nameWithoutLanguage
IPP_TAG_KEYWORD = 0x44
IPP_TAG_URI = 0x45
IPP_TAG_CHARSET = 0x47
IPP_TAG_LANGUAGE = 0x48  # naturalLanguage
IPP_TAG_MIME_TYPE = 0x49  # mimeMediaType
IPP_SUCCESSFUL_BELOW = 0x0100  # the status codes 0x0000 to 0x00FF are "successful" (RFC 8011's status codes)


def _attr(tag, name, val):
    """Return one encoded IPP attribute (value tag, name, value)."""
    n = name.encode()
    v = val if isinstance(val, bytes) else val.encode()
    return bytes([tag]) + struct.pack(">H", len(n)) + n + struct.pack(">H", len(v)) + v


def _with_port(hostport: str) -> str:
    """Return host:port with the IPP default port 631 added when none is given.

    IPv6 addresses are written in brackets ("[fe80::1]:631"); a bare IPv6 address gets brackets and the port.
    """
    if hostport.startswith("["):
        end = hostport.find("]")
        if end < 0:
            return hostport
        return hostport if hostport[end + 1 :].startswith(":") else hostport[: end + 1] + ":631"
    if hostport.count(":") > 1:  # a bare IPv6 address (it cannot carry a port without brackets)
        return f"[{hostport}]:631"
    return hostport if ":" in hostport else hostport + ":631"


def printer_url(target: str) -> tuple[str, str]:
    """Return (printer-uri, HTTP URL) for a printer address.

    Accepted: 'host', 'host:port', '[IPv6]:port', 'ipp://host[:port]/path' or 'http(s)://...'. A user name and
    password written into the address ('ipp://user:password@host/...') are removed: the print request does not send
    them, and removing them keeps them out of the log, the action's reply and the notification.
    """
    address = (target or "").strip()
    if "://" not in address:
        address = f"ipp://{address}"
    scheme, rest = address.split("://", 1)
    scheme = scheme.strip().lower()  # "IPPS://" is ipps://
    hostport, _, path = rest.partition("/")
    hostport = hostport.rpartition("@")[2]  # the part after the last "@": the host (and port) only
    path = "/" + (path or "ipp/print")
    hostport = _with_port(hostport)
    http = ("https" if scheme in ("ipps", "https") else "http") + f"://{hostport}{path}"
    return f"{'ipps' if scheme in ('ipps', 'https') else 'ipp'}://{hostport}{path}", http


def parse_ipp_reply(resp: bytes) -> tuple[int | None, int | None]:
    """Return (status code, job-id) of an IPP reply; None for what the reply does not contain.

    A short or malformed reply never raises.
    """
    if len(resp) < 8:
        return None, None
    status = struct.unpack(">H", resp[2:4])[0]
    job_id = None
    i, n = 8, len(resp)
    while i < n:
        tag = resp[i]
        i += 1
        if tag < IPP_DELIMITER_BELOW:  # a delimiter tag
            if tag == IPP_TAG_END:  # end-of-attributes
                break
            continue
        if i + 2 > n:
            break
        nl = struct.unpack(">H", resp[i : i + 2])[0]
        name = resp[i + 2 : i + 2 + nl].decode("ascii", "replace")
        i += 2 + nl
        if i + 2 > n:
            break
        vl = struct.unpack(">H", resp[i : i + 2])[0]
        val = resp[i + 2 : i + 2 + vl]
        i += 2 + vl
        if i > n:
            break
        if name == "job-id" and vl == 4 and len(val) == 4:
            job_id = struct.unpack(">i", val)[0]
    return status, job_id


def _plain(text) -> str:
    """Return a reason from the printer or the network as plain words, at most 60 characters, cut at a word: letters,
    digits, spaces and , ' - only. A printer's reply text is free text, and the message is shown in a notification
    that renders Markdown (links, images, and an address that starts with "www." as a link: no period is kept)."""
    words = " ".join(re.sub(r"[^A-Za-z0-9 ,'-]", " ", str(text or "")).split())
    if len(words) <= 60:
        return words
    return words[:61].rsplit(" ", 1)[0].rstrip(" ,'-") if " " in words[:61] else words[:60]


def _network_reason(reason, timeout) -> str:
    """Return the message of a print request that got no valid reply from the printer: a time-out, a secure connection
    that failed (an untrusted printer certificate, or a printer without ipps), a connection the printer closed without
    a valid reply, else the system's reason in plain words (never the name of a program error class)."""
    if isinstance(reason, ssl.SSLCertVerificationError):
        return t("report.print.reason.certificate")
    if isinstance(reason, ssl.SSLError):
        return t("report.print.reason.secure")
    if isinstance(reason, TimeoutError):  # socket.timeout is TimeoutError
        return t("report.print.reason.timeout", seconds=int(timeout))
    if isinstance(reason, http_client.HTTPException):  # the connection closed, or a reply that is not HTTP
        return t("report.print.reason.no_valid_reply")
    text = _plain(reason.strerror if isinstance(reason, OSError) else reason)
    return t("report.print.reason.unreachable", reason=text) if text else t("report.print.reason.unreachable_no_detail")


def ipp_print(target, pdf, job_name, color=True, two_sided=True, timeout=90):
    """Send the PDF as an IPP Print-Job and return (ok, message); the message never contains the printer address."""
    uri, http = printer_url(target)
    req = struct.pack(">BBHI", *IPP_VERSION, IPP_PRINT_JOB, IPP_REQUEST_ID) + bytes([IPP_TAG_OPERATION])
    req += _attr(IPP_TAG_CHARSET, "attributes-charset", "utf-8")
    req += _attr(IPP_TAG_LANGUAGE, "attributes-natural-language", "en")
    req += _attr(IPP_TAG_URI, "printer-uri", uri) + _attr(IPP_TAG_NAME, "requesting-user-name", "glucocraft")
    req += _attr(IPP_TAG_NAME, "job-name", job_name[:60])
    req += _attr(IPP_TAG_MIME_TYPE, "document-format", "application/pdf")
    req += bytes([IPP_TAG_JOB])
    req += _attr(IPP_TAG_KEYWORD, "sides", "two-sided-long-edge" if two_sided else "one-sided")
    req += _attr(IPP_TAG_KEYWORD, "print-color-mode", "color" if color else "monochrome")
    req += _attr(IPP_TAG_INTEGER, "copies", struct.pack(">i", 1))
    req += bytes([IPP_TAG_END]) + pdf
    r = urllib.request.Request(http, data=req, headers={"Content-Type": "application/ipp"})
    try:
        reply = urllib.request.urlopen(r, timeout=timeout)
        try:
            resp = reply.read()
            headers = getattr(reply, "headers", None)
            content_type = headers.get("Content-Type") if headers is not None else None
        finally:
            getattr(reply, "close", lambda: None)()
    except urllib.error.HTTPError as e:  # the printer answered, with an HTTP error
        return False, t("report.print.reason.http_error", code=e.code, reason=_plain(e.reason))
    except urllib.error.URLError as e:  # no connection: the reason only (for example "Connection refused")
        return False, _network_reason(e.reason, timeout)
    except OSError as e:  # a time-out or a secure-connection error outside the connection itself
        return False, _network_reason(e, timeout)
    except (ValueError, http_client.InvalidURL):  # the address cannot be parsed; its text is not repeated
        return False, t("report.print.reason.invalid_address")
    except http_client.HTTPException as e:  # a reply cut short (IncompleteRead) or not HTTP while it was read
        return False, _network_reason(e, timeout)
    except Exception:  # noqa: BLE001 - any other network problem: reported without a program error's name
        return False, t("report.print.reason.unreachable_no_detail")
    # an IPP reply: the Content-Type application/ipp (when given) and the major version 1 or 2 first
    ipp_type = content_type is None or content_type.split(";")[0].strip().lower() == "application/ipp"
    if not ipp_type or (resp and resp[0] not in IPP_REPLY_MAJOR_VERSIONS):
        return False, t("report.print.reason.not_ipp")
    status, job_id = parse_ipp_reply(resp)
    if status is None:
        return False, tp("report.print.reason.incomplete", len(resp))
    if status < IPP_SUCCESSFUL_BELOW:
        return True, t("report.print.reason.sent_job", job=job_id) if job_id else t("report.print.reason.sent")
    return False, t("report.print.reason.rejected", status=f"{status:04x}")
