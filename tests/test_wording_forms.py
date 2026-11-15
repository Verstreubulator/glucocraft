# License: AGPL-3.0-only (LICENSE).
"""One form for recurring wording (LANGUAGE_STYLE.md rules 3, 9 and 25), in every published text.

- Paths: a menu path and the path to a Documentation section use ">" (Home Assistant's own form), never "→"; "→" stays
  only the trend arrow and the arrows of technical notation (a unit conversion, a data flow, a mapping).
- The GlucoCraft-rule label: in prose the sentence "This method is a GlucoCraft rule."; the parenthesis only in short
  labels and in the cited-context texts (rule 15). Both copies of the standard statement label a rule the same way.
- Citations: several works in one parenthesis are in alphabetical order (APA 7); the 14-day / 70% GMI condition is
  labeled a GlucoCraft rule where the dashboard states it.
- The gap rule, the second source and the panel offline notice: the master's sentences, no older variant.
"""

from __future__ import annotations

import json
import re

import pytest
import yaml

from conftest import REPO

PKG = REPO / "custom_components" / "glucocraft"
EN = PKG / "i18n" / "en"
MARKDOWN = (
    ["README.md", "CONTRIBUTING.md"]
    + [
        f"docs/{n}.md"
        for n in (
            "SYSTEM_DESIGN",
            "PANELS",
            "DASHBOARD",
            "DEVELOPER_GUIDE",
            "ENTITIES_AND_API",
            "DISPLAY_CONTRACT",
            "LANGUAGE_STYLE",
            "ACCESSIBILITY",
            "SCREEN_READER_TEST_SCRIPT",
        )
    ]
    + sorted(
        str(p.relative_to(REPO)).replace("\\", "/")
        for p in (REPO / ".github").rglob("*")
        if p.suffix in (".md", ".yml")
    )
)


def _flat(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _flat(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        if all(isinstance(v, str) for v in node):
            yield path, "".join(node)
        else:
            for i, value in enumerate(node):
                yield from _flat(value, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def _catalogues() -> dict[str, str]:
    """Every user text of the integration's catalogues, as "<file>#<key>" -> text."""
    out = {}
    for name in ("documentation", "frontend", "report", "backend"):
        for key, text in _flat(json.loads((EN / f"{name}.json").read_text(encoding="utf-8"))):
            out[f"{name}.json#{key}"] = text
    for key, text in _flat(json.loads((PKG / "strings.json").read_text(encoding="utf-8"))):
        out[f"strings.json#{key}"] = text
    for key, text in _flat(yaml.safe_load((PKG / "services.yaml").read_text(encoding="utf-8"))):
        out[f"services.yaml#{key}"] = text
    return out


CAT = _catalogues()
DOCS = {name: (REPO / name).read_text(encoding="utf-8") for name in MARKDOWN if (REPO / name).exists()}
ALL = {**CAT, **DOCS}

# ---------------------------------------------------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------------------------------------------------
# A step of a menu path or of a Documentation path, before or after an arrow (with emphasis marks or tags around it)
STEP = (
    r"Settings|Configure|Documentation|Developer tools|GlucoCraft|ESPHome|Devices (?:&amp;|&) services|Entities|"
    r"System|Logs|Repairs|Dashboards|Resources|Actions|Statistics|States|Voice assistants|Expose|Delete|Download|"
    r"Custom|Report and dashboard|the menu|three dots\)|the entity|Name|Add integration|References|Colors|Disclaimer|"
    r"Essentials|<i>|\{configure\}|\{screen_\w+\}"
)
PATH_ARROW = re.compile(rf"(?:(?:{STEP})(?:\*\*|</?[ib]>|\s)*→|→(?:\*\*|</?[ib]>|\s)*(?:{STEP}))")


def test_no_path_uses_an_arrow():
    found = [
        f"{name}: ...{text[max(0, m.start() - 40) : m.end() + 30]}..."
        for name, text in ALL.items()
        for m in PATH_ARROW.finditer(text)
    ]
    assert found == []


def test_the_arrow_check_finds_the_old_forms_and_keeps_the_trend_arrows():
    for old in (
        "Settings → Devices &amp; services → GlucoCraft → <i>Configure</i>",
        "[Documentation → References](u)",
        "select **GlucoCraft** →\n**Download**",
        "{screen_report} → <i>When GlucoCraft is deleted</i>",
        "GlucoCraft → the menu (three dots) →",
    ):
        assert PATH_ARROW.search(old), old
    for kept in (
        "↑ ↗ → ↘ ↓",
        "unit (mmol/L → mg/dL)",
        "report.py → report_pdf.py",
        "| → | Changing Slowly |",
        '`zone_colors` (zone → "#RRGGBB")',
    ):
        assert not PATH_ARROW.search(kept), kept


def test_the_configure_path_is_home_assistants_form():
    assert (
        CAT["documentation.json#docs.text.configure"]
        == "Settings > Devices &amp; services > GlucoCraft > <i>Configure</i>"
    )
    assert "Settings > Devices & services > GlucoCraft > Configure" in DOCS["README.md"]
    assert any(
        "Settings > Devices & services > GlucoCraft > Configure" in t for k, t in CAT.items() if k.startswith("strings")
    )


# ---------------------------------------------------------------------------------------------------------------------
# The GlucoCraft-rule label
# ---------------------------------------------------------------------------------------------------------------------
LABEL = re.compile(r"\((?:[^()]*[;,] )?(?:a )?GlucoCraft rule(?:[;,][^()]*)?\)")
# Where the parenthesis stays: the cited-context texts and their Documentation copies (their closing sentence
# carries it), and short labels that are not sentences
LABEL_ALLOWED = {
    "frontend.json#strategy.cx.": "the cited-context texts",
    "documentation.json#docs.section.how.body": "the Documentation copy of the trend-category text",
    "documentation.json#docs.section.metrics.body": "the Documentation copies of the data-sufficiency and the stable-glucose texts",
    "frontend.json#ui.core.note.change_": 'a caption, "Change (GlucoCraft rule): ..."',
    "frontend.json#ui.statistics.note.": "row notes of the statistics table",
    "strings.json#selector.a1c_unit.options.auto": "an option label",
}


def test_the_rule_label_in_prose_is_the_sentence():
    found = [k for k, t in CAT.items() if LABEL.search(t) and not any(k.startswith(p) for p in LABEL_ALLOWED)]
    assert found == [], 'write the label as the sentence "This method is a GlucoCraft rule." after what it covers'


def test_the_label_check_finds_the_parenthesis():
    assert LABEL.search("no consensus document addresses such readings (GlucoCraft rule).")
    assert LABEL.search("apart (the gap rule, a GlucoCraft rule);")
    assert LABEL.search("(the condition of Battelino et al., 2019; a GlucoCraft rule)")
    assert not LABEL.search("This method is a GlucoCraft rule.")


def test_both_standard_statements_label_a_rule_alike():
    for key in ("documentation.json#docs.text.standard_statement", "report.json#report.statement"):
        assert "it is labeled as a GlucoCraft rule." in CAT[key], key
    assert 'labeled "GlucoCraft rule"' not in CAT["documentation.json#docs.text.standard_statement"]


# ---------------------------------------------------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------------------------------------------------
GROUP = re.compile(r"\(([^()]*\d{4}[a-z]?(?:, [^;()]*)?(?:; [^;()]*\d{4}[a-z]?(?:, [^;()]*)?)+)\)")
AUTHOR = re.compile(r"^(?:the condition of |the study by )?([A-Z][A-Za-z\-]+)(?: et al\.| &| and|,| \(| Professional)")


def _out_of_order(text: str) -> list[str]:
    bad = []
    for m in GROUP.finditer(text):
        names = [AUTHOR.match(item.strip()) for item in m.group(1).split("; ")]
        if all(names) and [n.group(1) for n in names] != sorted(n.group(1) for n in names):
            bad.append(m.group(0))
    return bad


def test_several_works_in_one_parenthesis_are_in_alphabetical_order():
    found = {k: _out_of_order(t) for k, t in ALL.items() if _out_of_order(t)}
    assert found == {}


def test_the_order_check_finds_a_wrong_order():
    assert _out_of_order("(Bergenstal et al., 2018; Battelino et al., 2019)")
    assert not _out_of_order("(Battelino et al., 2019; Bergenstal et al., 2018)")
    assert not _out_of_order("(Battelino et al., 2019; Monnier et al., 2017)")


@pytest.mark.parametrize(
    "key",
    [
        "strategy.change.about.consensus",
        "strategy.change.about.personal",
        "ui.change.note.consensus",
        "ui.change.note.custom",
        "ui.stats.footnote.consensus",
        "ui.stats.footnote.custom",
    ],
)
def test_the_gmi_condition_is_labeled_a_glucocraft_rule(key):
    text = CAT[f"frontend.json#{key}"]
    gmi = text.index("GMI is shown")
    assert "(Battelino et al., 2019; Bergenstal et al., 2018)." in text[gmi:]
    assert "This method is a GlucoCraft rule." in text[gmi:]


# ---------------------------------------------------------------------------------------------------------------------
# The gap rule, the second source, the panel offline notice
# ---------------------------------------------------------------------------------------------------------------------
GAP = "longer than 15 minutes or three reading intervals, whichever is longer"
OLD = (
    "taken from the readings around each stretch",
    "three times the reading interval",
    "the reading interval taken from",
    "more than 15 minutes or three reading intervals (whichever is longer) apart",
    "the gap threshold is 15 minutes",
    "supplies readings missing from the first",
    "continues alone if the first stops",
    "fills readings the first missed",
    "fills only readings the first missed",
    "after 10 minutes without a connection",
    "appears only after 10 minutes.",
    "Declared after {stale} minutes without a new measurement",
)
SECOND = (
    "A second source is designed to supply readings that the primary source misses, up to 24 hours back, and to "
    "continue when it stops, merged by measurement time"
)


def test_no_older_wording_remains():
    found = [(k, o) for k, t in ALL.items() for o in OLD if o in re.sub(r"\s+", " ", t)]
    assert found == []


def test_every_gap_definition_is_the_masters():
    for key, text in CAT.items():
        for m in re.finditer(r"15 minutes or (?:three|3)\b", text):
            assert text[m.start() :].startswith("15 minutes or three reading intervals, whichever is longer"), key
    for key in (
        "frontend.json#ui.statistics.gap_note",
        "frontend.json#ui.history.how_readings",
        "frontend.json#ui.history.how_hours",
        "report.json#report.about.data.body",
    ):
        assert GAP in CAT[key] and "the median of the 10 intervals before the reading" in CAT[key], key


@pytest.mark.parametrize(
    "key",
    [
        "documentation.json#docs.section.how.body",
        "documentation.json#docs.section.setup.body",
        "documentation.json#docs.section.glossary.body",
        "strings.json#config.step.source.sections.second_source.description",
        "strings.json#options.step.sources.sections.second_source.description",
    ],
)
def test_the_second_source_is_the_masters_first_sentence(key):
    assert SECOND in CAT[key]
