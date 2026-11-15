# License: AGPL-3.0-only (LICENSE).
"""The published documents and texts: other products named only where docs/LANGUAGE_STYLE.md rule 20 allows them,
the 12 px exceptions as one list (and the same as the panel configurations draw), the font license notice with its
Reserved Font Names, abbreviations spelled out at their first use, percentages without a space, the screen-reader
guide's quoted texts as GlucoCraft shows and reads them, and the places of the wall-panel address."""

from __future__ import annotations

import json
import re

import pytest

from conftest import NO_PANELS, PANELS, REPO
from js_i18n import catalogue

COMP = REPO / "custom_components" / "glucocraft"


def _flat(value) -> str:
    """Every string of a catalogue value, joined (nested objects and lists included)."""
    if isinstance(value, dict):
        return " ".join(_flat(v) for v in value.values())
    if isinstance(value, list):
        return "".join(_flat(v) for v in value)
    return value if isinstance(value, str) else ""


def _plain(text: str) -> str:
    """The words of a text: markup, Markdown emphasis and quotation marks removed, white space collapsed."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[*\"“”]", "", text)
    return " ".join(text.split())


# ------------------------------------------------------------------------------------------------ product names
# The names that rule 20 allows only in named places: the CGM systems and their makers, the CGM data projects, the
# phone health apps of the text "Sources not supported for live display", and the trademark signs. The
# platform names have places of their own (PLATFORMS below).
NAMES = re.compile(
    r"FreeStyle|Libre|Lingo|Abbott|Dexcom|Stelo|Medtronic|CareLink|Simplera|Guardian|Eversense|Senseonics|Medtrum|"
    r"SIBIONICS|Sibionics|xDrip|Nightscout|Apple Health|Health Connect|™|®"
)
# Abbott only as a cited source: the group author of the cited manufacturer documentation (rule 20)
CITED_ABBOTT = re.compile(r"Abbott Diabetes Care")
# The documents published with GlucoCraft whose text users read. docs/LANGUAGE_STYLE.md states the rule itself, and the
# code and the panel configurations' comments are not user-visible text (the code names the integrations it
# recognizes).
PUBLISHED_DOCS = (
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "tests/README.md",
    "docs/SYSTEM_DESIGN.md",
    "docs/PANELS.md",
    "docs/DASHBOARD.md",
    "docs/DEVELOPER_GUIDE.md",
    "docs/ENTITIES_AND_API.md",
    "docs/DISPLAY_CONTRACT.md",
    "docs/ACCESSIBILITY.md",
    "docs/SCREEN_READER_TEST_SCRIPT.md",
    "custom_components/glucocraft/THIRD_PARTY_NOTICES.md",
)
# Markdown sections that are allowed places: the reference lists, the README's copy of the text "Sources not supported
# for live display" and its Trademarks section
MD_ALLOWED = re.compile(
    r"^## (?:\d+\. )?(?:Clinical )?References\n.*?(?=^## |\Z)"
    r"|^## Sources not supported for live display\n.*?(?=^## |\Z)"
    r"|^## Trademarks\n.*?(?=^## |\Z)",
    re.MULTILINE | re.DOTALL,
)
# The Documentation's allowed places: the section Readings Outside the Sensor's Range and the References (whole
# sections), the text "Sources not supported for live display" of Supported Sources, the Trademarks
# paragraph of About and License, and the citations ({ref:...} and {cite:...})
DOC_ALLOWED_SECTIONS = ("docs.section.lohi.body", "docs.section.references.body")
DOC_ALLOWED = re.compile(
    r"<h3>Sources not supported for live display</h3>.*?(?=<h3>|\Z)"
    r"|<h3>Trademarks</h3><p>.*?</p>"
    r"|\{(?:ref|cite):[^}]*\}",
    re.DOTALL,
)


def _published_texts() -> dict[str, str]:
    """The published user-visible texts by where they are, with the places rule 20 allows taken out."""
    out: dict[str, str] = {}
    for name in PUBLISHED_DOCS:
        path = REPO / name
        if path.is_file():
            out[name] = MD_ALLOWED.sub("", path.read_text(encoding="utf-8"))
    github = REPO / ".github"
    for path in sorted(github.rglob("*")) if github.is_dir() else ():
        if path.suffix in (".md", ".yml", ".yaml"):
            out[path.relative_to(REPO).as_posix()] = path.read_text(encoding="utf-8")
    for path in sorted((COMP / "i18n" / "en").glob("*.json")):
        for key, value in json.loads(path.read_text(encoding="utf-8")).items():
            if key not in DOC_ALLOWED_SECTIONS:
                out[f"{path.name} {key}"] = DOC_ALLOWED.sub("", _flat(value))
    out["strings.json"] = _flat(json.loads((COMP / "strings.json").read_text(encoding="utf-8")))
    out["services.yaml"] = (COMP / "services.yaml").read_text(encoding="utf-8")
    if PANELS is not None and (PANELS / "i18n" / "en.yaml").is_file():
        out["panels i18n/en.yaml"] = (PANELS / "i18n" / "en.yaml").read_text(encoding="utf-8")
    return {k: CITED_ABBOTT.sub("", v) for k, v in out.items()}


def _names(text: str) -> list[str]:
    return NAMES.findall(text)


def test_other_products_are_named_only_where_rule_20_allows():
    found = {where: _names(text) for where, text in _published_texts().items()}
    assert not {where: names for where, names in found.items() if names}


def test_the_name_check_reads_every_published_document_and_catalogue():
    texts = _published_texts()
    for name in PUBLISHED_DOCS:
        if (REPO / name).is_file():
            assert name in texts, name
    assert any(k.startswith("documentation.json ") for k in texts) and "strings.json" in texts
    assert ".github/pull_request_template.md" in texts


@pytest.mark.parametrize(
    "probe",
    (
        "A source such as the Dexcom integration delivers readings every 5 minutes.",
        "The FreeStyle Libre 3 sends its history in 15-minute steps.",
        "Readings from Nightscout below 39 are error codes.",
        "Libre's warm-up marker is not stored.",
        "GlucoCraft™ shows the value.",
    ),
)
def test_the_name_check_catches_a_name_outside_the_allowed_places(probe):
    assert _names(CITED_ABBOTT.sub("", probe))


def test_the_allowed_places_hide_nothing_else():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    kept = MD_ALLOWED.sub("", readme)
    for heading in ("## Essentials", "## Features", "## Privacy", "## License"):
        assert heading in kept, heading
    for heading in ("## Trademarks", "## References", "## Sources not supported for live display"):
        assert heading in readme and heading not in kept, heading
    docs = catalogue("documentation")
    about = DOC_ALLOWED.sub("", _flat(docs["docs.section.about.body"]))
    assert "<h3>Trademarks</h3>" not in about and "<h3>" in about
    sources = DOC_ALLOWED.sub("", _flat(docs["docs.section.cgms.body"]))
    assert "Any glucose sensor entity" in sources and "These CGM systems cannot deliver that" not in sources
    # a citation of Abbott Diabetes Care is allowed; Abbott in another role is not
    assert not _names(CITED_ABBOTT.sub("", "(Abbott Diabetes Care, 2022)"))
    assert _names(CITED_ABBOTT.sub("", "an Abbott sensor"))


# ------------------------------------------------------------------------------------------------ 12 px exceptions
TWELVE_PX = _plain(
    "the statistics labels of the square 480 × 480 panel and the 3.5-inch 480 × 320 panel; on the 3.5-inch panel also "
    '"TREND", "5 MIN DELTA", the source name, the weekly description line and the GMI value in both A1C units; and the '
    "statistics labels of the square display card"
)


def test_the_12_px_exceptions_are_one_list_in_every_place():
    accessibility = _plain((REPO / "docs" / "ACCESSIBILITY.md").read_text(encoding="utf-8"))
    assert accessibility.count(TWELVE_PX) == 2  # rule 3 and Known limitations
    panels = REPO / "docs" / "PANELS.md"
    if panels.is_file():
        assert TWELVE_PX in _plain(panels.read_text(encoding="utf-8"))
    limits = _plain(_flat(catalogue("documentation")["docs.section.accessibility.body"]))
    assert TWELVE_PX[0].upper() + TWELVE_PX[1:] in limits or TWELVE_PX in limits


# The 12 px texts of each example panel configuration, by what they show: the statistics labels (the texts
# t_stat_*), the column labels TREND and 5 MIN DELTA, the source name, the weekly description line and the GMI value
# in both A1C units (set by a lambda)
TWELVE_PX_PANELS = {
    "glucocraft-panel.yaml": {"statistics labels"},
    "glucocraft-guition-35.yaml": {
        "statistics labels",
        "TREND",
        "5 MIN DELTA",
        "source name",
        "weekly description line",
        "GMI value in both A1C units",
    },
    "glucocraft-waveshare-lcd5.yaml": set(),
    "glucocraft-waveshare-lcd7.yaml": set(),
    "glucocraft-waveshare-p4-7b.yaml": set(),
}
WIDGET_MEANING = (
    (re.compile(r'text: "\$\{t_stat_'), "statistics labels"),
    (re.compile(r'text: "\$\{t_glucose_trend_label\}"'), "TREND"),
    (re.compile(r'text: "\$\{t_glucose_delta_label\}"'), "5 MIN DELTA"),
    (re.compile(r"id: data_source_label\b"), "source name"),
    (re.compile(r"id: trend_desc_label\b"), "weekly description line"),
)


def _twelve_px_texts(config: str) -> set[str]:
    """What a panel configuration draws in a 12 px font: each widget whose text_font is a 12 px font, by the meaning of
    its text or id, and the GMI value where a lambda chooses a 12 px font for both A1C units."""
    lines = config.splitlines()
    fonts = {
        m.group(1)
        for i, ln in enumerate(lines[:-1])
        if (m := re.match(r"\s*id:\s*(\w+)\s*$", ln)) and re.match(r"\s*size:\s*12\b", lines[i + 1])
    }
    found: set[str] = set()
    if not fonts:
        return found
    use = re.compile(r"\b(?:" + "|".join(sorted(fonts)) + r")\b")
    for i, ln in enumerate(lines):
        if "text_font:" in ln and use.search(ln):
            start = i
            while start > 0 and not re.match(r"\s*- \w+:\s*$", lines[start]):
                start -= 1
            indent = len(lines[start]) - len(lines[start].lstrip())
            end = start + 1
            while end < len(lines) and (not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) > indent):
                end += 1
            widget = "\n".join(lines[start:end])
            meanings = [name for pattern, name in WIDGET_MEANING if pattern.search(widget)]
            assert len(meanings) == 1, widget
            found.add(meanings[0])
        elif use.search(ln) and "a1c_mode() == 2" in ln:
            found.add("GMI value in both A1C units")
    return found


@pytest.mark.skipif(PANELS is None, reason=NO_PANELS)
@pytest.mark.parametrize("name", sorted(TWELVE_PX_PANELS))
def test_the_12_px_list_is_what_the_panels_draw(name):
    assert _twelve_px_texts((PANELS / name).read_text(encoding="utf-8")) == TWELVE_PX_PANELS[name]


def test_the_square_display_card_draws_only_its_statistics_labels_at_12_px():
    card = (COMP / "www" / "glucocraft-display-card.js").read_text(encoding="utf-8")
    assert "const STAT_LABEL_PX = 12;" in card
    assert card.count("STAT_LABEL_PX") >= 2


# ------------------------------------------------------------------------------------------------ font notice
RESERVED_FONT_NAMES = (
    "Digitized data copyright (c) 2010 Google Corporation with Reserved Font Arimo, Tinos and Cousine",
    "Red Hat, Inc. with Reserved Font Name Liberation.",
)


def test_the_liberation_sans_notices_carry_both_reserved_font_name_declarations():
    license_file = (COMP / "fonts" / "LICENSE-LiberationSans.txt").read_text(encoding="utf-8")
    assert "Copyright (c) 2012 Red Hat, Inc. with Reserved Font Name Liberation." in license_file
    notices = " ".join((COMP / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8").split())
    about = _flat(catalogue("documentation")["docs.section.about.body"])
    for text in (license_file, notices, about):
        for declaration in RESERVED_FONT_NAMES:
            assert declaration in text, declaration


# ------------------------------------------------------------------------------------------------ wording
# The first use of each abbreviation in a document is spelled out there, or stands in a legend ("Abbreviations: ...")
# close to it
FULL_NAMES = {
    "A1C": "glycated hemoglobin",
    "CSV": "comma-separated values",
    "CV": "coefficient of variation",
    "GMI": "glucose management indicator",
    "GRI": "Glycemia Risk Index",
    "IPP": "Internet Printing Protocol",
    "PDF": "Portable Document Format",
}
ABBREVIATED = {
    "README.md": ("A1C", "IPP", "CSV", "PDF"),
    "docs/DASHBOARD.md": ("GMI", "CV", "GRI", "A1C", "IPP", "CSV", "PDF"),
    "docs/ENTITIES_AND_API.md": ("GMI", "A1C", "CV", "CSV", "PDF", "GRI"),
    "docs/ACCESSIBILITY.md": ("GMI", "A1C", "CV", "PDF"),
}


@pytest.mark.parametrize("name", sorted(ABBREVIATED))
def test_abbreviations_are_spelled_out_at_their_first_use(name):
    path = REPO / name
    if not path.is_file():
        pytest.skip(f"{name} is not in this copy of the repository")
    text = " ".join(path.read_text(encoding="utf-8").split())
    for abbreviation in ABBREVIATED[name]:
        first = re.search(r"(?<![\w`/.-])" + abbreviation + r"(?![\w])", text)
        if first is None:
            continue
        spelled = re.search(re.escape(FULL_NAMES[abbreviation]), text, re.IGNORECASE)
        assert spelled and spelled.start() <= first.end() + 300, (name, abbreviation)


PUBLIC_DOCS = PUBLISHED_DOCS + ("docs/LANGUAGE_STYLE.md",)


def test_the_published_documents_write_percentages_without_a_space_and_american_spelling():
    for name in PUBLIC_DOCS:
        path = REPO / name
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert not re.findall(r"\d %", text), name
            assert not re.search(r"\bgrey\b", text, re.IGNORECASE), name


# ------------------------------------------------------------------------------------------------ the screen-reader guide
def test_the_screen_reader_guide_quotes_what_glucocraft_shows_and_reads():
    guide = REPO / "docs" / "SCREEN_READER_TEST_SCRIPT.md"
    if not guide.is_file():
        pytest.skip("docs/SCREEN_READER_TEST_SCRIPT.md is not in this copy of the repository")
    text = guide.read_text(encoding="utf-8")
    front = catalogue("frontend")
    backend = catalogue("backend")
    for key in (
        "ui.maintenance.said.none",
        "spoken.not_available",
        "card.describe.sensor_expired",
        "report_page.options.heading",
        "ui.banner.no_data.title",
    ):
        assert front[key] in text, key
    assert front["ui.maintenance.said.issues.one"].replace("{count}", "1") in text
    assert front["ui.link.new_tab"].strip("()") in text
    assert "meets the target" in front["ui.readout.name.meets"] and "meets the target" in text
    assert backend["notify.phone_test.title"] in text
    assert "Glucose Notification" not in text  # GlucoCraft sends no high or low glucose notifications
    assert "*Trend shown as*" in text and "page 17 of 17" in text
    assert "AGPL-3.0-only" in front["strategy.footer.license"] and "*AGPL-3.0-only*" in text
    # the feedback paragraph says what ACCESSIBILITY.md says
    assert "No response time is promised." in text
    assert "No response time is promised." in (REPO / "docs" / "ACCESSIBILITY.md").read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------------------ wall-panel address
def test_the_panel_guide_names_every_place_where_the_wall_panel_address_appears():
    panels = REPO / "docs" / "PANELS.md"
    if not panels.is_file():
        pytest.skip("docs/PANELS.md is not in this copy of the repository")
    text = " ".join(panels.read_text(encoding="utf-8").split())
    for place in ("Home Assistant's own log", "**Validate**", "`esphome config`", "build folder", "reverse proxy"):
        assert place in text, place


# ------------------------------------------------------------------------------------------------ platform names
# The phone and computer platforms and Google Fonts, each only in its places (LANGUAGE_STYLE.md rule 20): Android,
# iPhone and iPad in the screen-reader guide's set-up steps (its introduction and section 1), the Home Assistant iOS
# and Android apps and the iPhone in "Sources not supported for live display", the Trademarks sections;
# Google Fonts in the panel guide's build steps. Mac falls under the general sentence of the Trademarks.
PLATFORMS = re.compile(r"\b(?:Android|iPhone|iPad|Google Fonts)\b")


def _without_platform_places(name: str, text: str) -> str:
    """A published text without the places where rule 20 allows the platform names."""
    text = MD_ALLOWED.sub("", text) if name.endswith(".md") else text
    if name == "docs/SCREEN_READER_TEST_SCRIPT.md":
        text = text[text.index("\n## 2. ") :]
    if name == "docs/PANELS.md":
        start = text.index("\n## Building a panel from the examples\n")
        end = text.index("\n## ", start + 1)
        text = text[:start] + text[end:]
    return text


def test_the_platform_names_stand_only_in_their_places():
    found = {}
    for name in PUBLISHED_DOCS:
        path = REPO / name
        if path.is_file():
            rest = _without_platform_places(name, path.read_text(encoding="utf-8"))
            if hits := sorted(set(PLATFORMS.findall(rest))):
                found[name] = hits
    for key, value in catalogue("documentation").items():
        text = value if isinstance(value, str) else "".join(value)
        rest = DOC_ALLOWED.sub("", text)
        if hits := sorted(set(PLATFORMS.findall(rest))):
            found[key] = hits
    assert not found, found
    assert PLATFORMS.search((REPO / "docs" / "SCREEN_READER_TEST_SCRIPT.md").read_text(encoding="utf-8"))
