# License: AGPL-3.0-only (LICENSE).
"""The wording of the published documents and the length of the settings texts.

- One conformance wording (LANGUAGE_STYLE.md rule 25): "designed to follow WCAG 2.2 level AA where it applies" with
  "No conformance claim is made." in the README, ACCESSIBILITY.md and the Documentation.
- The Documentation's list of Repair notices names every Repairs issue GlucoCraft can raise; Troubleshooting explains
  the notices about the sources, the readings file and the cards; the notification titles are named; the unit texts
  read as mg/dL and mmol/L are listed with the refusal of any other unit.
- About This Report states the GMI condition once (in the metrics' sources and the data sufficiency, not again).
- The Documentation's Motion and Timing names both things that move (the throb and the section links' scrolling).
- The published texts describe the system as it is (no earlier versions, migrations or replaced rules) and use the
  current terms; the entities are described as ordinary entities (no "for the user's own dashboards").
- The Glossary has an entry, with a link to the defining section, for each term the screens show.
- The history import is described as the code does it: at every start with fewer than 100 readings stored.
- The printer certificate caveat stands in the setting, the Documentation's printing text and Troubleshooting.
- The settings texts carry each fact once: the sensor-limit explanation in the screen description, each field its
  own fact with the Documentation pointer; the facts moved out are in the Documentation.
"""

from __future__ import annotations

import json
import re

import pytest

from conftest import REPO

PKG = REPO / "custom_components" / "glucocraft"
DOC = json.loads((PKG / "i18n" / "en" / "documentation.json").read_text(encoding="utf-8"))
DOC_TEXT = json.dumps(DOC, ensure_ascii=False)
STRINGS = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))
README = (REPO / "README.md").read_text(encoding="utf-8")
REPORT = json.loads((PKG / "i18n" / "en" / "report.json").read_text(encoding="utf-8"))
PUBLIC_DOCS = (
    "SYSTEM_DESIGN.md",
    "PANELS.md",
    "DASHBOARD.md",
    "DEVELOPER_GUIDE.md",
    "ENTITIES_AND_API.md",
    "DISPLAY_CONTRACT.md",
    "ACCESSIBILITY.md",
)


def _doc(key: str) -> str:
    value = DOC[key]
    return "".join(value) if isinstance(value, list) else value


def _published() -> dict[str, str]:
    """The published texts: the README, the public documents, the catalogues and strings.json."""
    texts = {"README.md": README, "documentation.json": DOC_TEXT, "strings.json": json.dumps(STRINGS)}
    for name in ("frontend.json", "backend.json", "report.json"):
        texts[name] = (PKG / "i18n" / "en" / name).read_text(encoding="utf-8")
    for name in PUBLIC_DOCS:
        path = REPO / "docs" / name
        if path.is_file():
            texts[name] = path.read_text(encoding="utf-8")
    return texts


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("*", "").replace("`", ""))


def _words(text: str) -> int:
    return len(re.findall(r"\S+", text))


# ------------------------------------------------------------------------------------------------------- conformance
CONFORMANCE = "designed to follow WCAG 2.2 level AA where it applies"


def test_one_conformance_wording():
    accessibility = (REPO / "docs" / "ACCESSIBILITY.md").read_text(encoding="utf-8")
    for name, text in (("README", README), ("ACCESSIBILITY.md", accessibility), ("Documentation", DOC_TEXT)):
        plain = _plain(text)
        assert CONFORMANCE in plain and "No conformance claim is made." in plain, name
        # every copy is the one sentence pair, with nothing inserted between its two sentences
        assert plain.count(CONFORMANCE) == plain.count(CONFORMANCE + ". No conformance claim is made."), name
    for name, text in _published().items():
        for retired in ("designed to meet WCAG", "The standard applied is", "the accessibility standard for PDF"):
            assert retired not in text, (name, retired)
    assert "PDF/UA-1 (ISO 14289-1), the specification for accessible PDF files" in DOC_TEXT
    assert "standard 14-day summary" not in (REPO / "docs" / "SYSTEM_DESIGN.md").read_text(encoding="utf-8")


def test_the_repair_notices_item_names_every_repairs_issue():
    """The Documentation's list of Repair notices names the title of every Repairs issue GlucoCraft can raise, and its
    count of the readings-file notices matches the titles of those notices."""
    item = next(li for li in _doc("docs.section.notifications.body").split("<li>") if "Repair notices" in li)
    titles = {issue["title"] for issue in STRINGS["issues"].values()}
    for title in titles:
        # the sensor-cap titles carry their values; the item names them by their common start
        shown = title.split("{")[0].rstrip() if "{" in title else title
        assert shown in item, title
    file_titles = {t for t in titles if t.startswith("GlucoCraft's readings file")}
    words = {3: "three", 4: "four", 5: "five", 6: "six"}
    assert f"{words[len(file_titles)]} notices about the readings file" in item


def test_the_clock_allowance_is_stated_with_the_later_reading():
    """A reading counts as later than the system clock (no current data; the Repairs issue clock_behind) only beyond the
    coordinator's clock allowance; every description of that condition states the allowance in minutes."""
    code = (PKG / "coordinator.py").read_text(encoding="utf-8")
    minutes = int(re.search(r"CLOCK_MARGIN_SECONDS = (\d+)", code).group(1)) // 60
    stated = f"more than {minutes} minutes later than the"
    texts = {"documentation.json": DOC_TEXT}
    for name in ("SYSTEM_DESIGN.md", "ENTITIES_AND_API.md", "DISPLAY_CONTRACT.md"):
        texts[name] = _plain((REPO / "docs" / name).read_text(encoding="utf-8"))
    for name, text in texts.items():
        assert stated in text, name
        # (the Repairs issue's title, "Stored glucose readings are later than the system clock", names the issue)
        for bare in (
            "or a reading later than the",
            "newest reading is later than the",
            "While stored readings are later than the",
            "(fixable): stored readings are later than the",
        ):
            assert bare not in text, (name, bare)


def test_motion_names_both_movements():
    text = _doc("docs.section.accessibility.body")
    assert "with one exception" not in text
    assert "only two things move" in text and "fade to about 30% brightness" in text
    assert "section links scroll smoothly" in text and "without motion, while the system's reduced-motion" in text
    # the code: the section links scroll at once while reduced motion is asked for
    docs_js = (PKG / "www" / "glucocraft-docs.js").read_text(encoding="utf-8")
    assert '"(prefers-reduced-motion: reduce)")?.matches ? "auto" : "smooth"' in docs_js


# ----------------------------------------------------------------------------------------- the system as it is now
@pytest.mark.parametrize(
    "phrase",
    [
        "for the user's own dashboards",
        "stored by an earlier version",
        "written by an earlier version",
        "replaces three earlier rules",
        "entity-id migration",
        "id migration list",
        "former YAML option",
        "an older GlucoCraft",
        "earlier setting *Word under the glucose value*",
        "so that existing installations keep working",
        "the key is the older name",
        "retention_days` value left in an older",
        "trend direction and sensor-expiry entities",
        "70% sensor data",
        "limited sensor data",
        "(stale flag)",
        "percentile bands, lines and target range",
        "history import at first setup",
        "History at first setup",
    ],
)
def test_no_history_or_retired_wording(phrase):
    for name, text in _published().items():
        assert phrase not in text, (name, phrase)


def test_the_automatic_entity_clean_up_is_described():
    design = (REPO / "docs" / "SYSTEM_DESIGN.md").read_text(encoding="utf-8")
    guide = (REPO / "docs" / "DEVELOPER_GUIDE.md").read_text(encoding="utf-8")
    for text in (design, guide):
        assert "| `stale_entities.py` | At every start, the removal of the entity-registry entries" in text
    assert (PKG / "stale_entities.py").is_file()


def test_the_history_import_has_one_description():
    how = _doc("docs.section.how.body")
    assert "only the history import (at a start with fewer than 100 readings stored) and the CSV import" in how
    assert "<b>History import.</b> When GlucoCraft has fewer than 100 readings stored" in how
    code = (PKG / "coordinator.py").read_text(encoding="utf-8")
    assert "HISTORY_IMPORT_BELOW = 100" in code and "self.storage.reading_count < self.HISTORY_IMPORT_BELOW" in code


def test_the_diagnostics_text_names_only_entities_that_exist():
    text = _doc("docs.section.troubleshooting.body")
    assert "the entity ids of the sources (also the sensor-expiry entity)" in text
    assert "trend direction" not in text


# --------------------------------------------------------------------------------------------------------- glossary
GLOSSARY_TERMS = {
    "Automatic (setting value).": ("{sec:lohi|", "{sec:setup|"),
    "Delivery test.": ("{sec:notifications|",),
    "Increasing; decreasing; stable.": ("{sec:ranges|",),
    "Maintenance status.": ("{sec:pages|",),
    "No-data notice.": ("{sec:gaps|",),
}


@pytest.mark.parametrize("term", GLOSSARY_TERMS)
def test_the_glossary_has_the_terms_the_screens_show(term):
    entries = [e for e in DOC["docs.section.glossary.body"] if e.startswith(f"<li><b>{term}</b>")]
    assert len(entries) == 1, term
    for link in GLOSSARY_TERMS[term]:
        assert link in entries[0], (term, link)


def test_the_glossary_entries_are_in_alphabetical_order():
    body = DOC["docs.section.glossary.body"]
    end = body.index("<h3>Terms defined in other sections</h3>")
    terms = [re.match(r"<li><b>([^<]+)</b>", e).group(1) for e in body[:end] if e.startswith("<li><b>")]
    assert terms == sorted(terms, key=str.casefold)


# ------------------------------------------------------------------------------------------------- the printer
CERTIFICATE = "work only with a printer certificate that Home Assistant trusts; most printers' own certificates are not trusted, and the print then fails"


def test_the_certificate_caveat_stands_in_every_printing_text():
    setting = STRINGS["options"]["step"]["report"]["data_description"]["printer_address"]
    assert "ipps:// and https:// " + CERTIFICATE in setting
    caveat = "<code>ipps://</code> and <code>https://</code> " + CERTIFICATE
    assert caveat in _doc("docs.section.pages.body")
    assert caveat in _doc("docs.section.troubleshooting.body")
    assert REPORT["report.print.reason.certificate"] == "the printer's certificate is not trusted"


# ------------------------------------------------------------------------------------------------- settings texts
SOURCE_SCREENS = (STRINGS["config"]["step"]["source"], STRINGS["options"]["step"]["sources"])
LIMIT_FIELDS = (
    "sensor_lower_limit",
    "sensor_upper_limit",
    "secondary_sensor_lower_limit",
    "secondary_sensor_upper_limit",
)


@pytest.mark.parametrize("screen", SOURCE_SCREENS, ids=("setup", "configure"))
def test_the_sensor_limit_explanation_stands_once_per_screen(screen):
    blob = json.dumps(screen, ensure_ascii=False)
    assert (
        blob.count("**Sensor lower and upper limit**") == 1
        and "**Sensor lower and upper limit**" in screen["description"]
    )
    assert blob.count("with a limit set by hand, a reading at the limit as well") == 1
    notes = {**screen["data_description"], **screen["sections"]["second_source"]["data_description"]}
    for key in LIMIT_FIELDS:
        text = notes[key]
        assert "*Automatic*:" in text and "*Readings Outside the Sensor's Range*" in text, key
        assert _words(text) <= 50, (key, _words(text))
        assert "set by hand" not in text, key  # the rule stands in the screen's description


def test_the_long_settings_texts_are_shorter():
    options = STRINGS["options"]["step"]
    lengths = {
        "setup sources": _words(SOURCE_SCREENS[0]["description"]),
        "Configure sources": _words(SOURCE_SCREENS[1]["description"]),
        "display card and wall panels": _words(options["displays"]["description"]),
        "phones": _words(options["notifications"]["data_description"]["phone_notify_targets"]),
        "A1C and GMI unit": _words(options["units"]["data_description"]["a1c_unit"]),
        "wall-panel address": _words(options["displays"]["data_description"]["regenerate_panel_webhook"]),
        "sensor floor notice": _words(STRINGS["issues"]["sensor_floor"]["description"]),
        "sensor cap notice": _words(STRINGS["issues"]["sensor_cap"]["description"]),
    }
    limits = {
        "setup sources": 220,
        "Configure sources": 215,
        "display card and wall panels": 175,
        "phones": 120,
        "A1C and GMI unit": 100,
        "wall-panel address": 110,
        "sensor floor notice": 135,
        "sensor cap notice": 135,
    }
    assert {k: v for k, v in lengths.items() if v > limits[k]} == {}
    # one text on both screens, but for where a phone's needs are listed: the setup's text says "after the setup",
    # since the dashboard and its Documentation exist only then
    setup_text = STRINGS["config"]["step"]["finish"]["data_description"]["phone_notify_targets"]
    configure_text = options["notifications"]["data_description"]["phone_notify_targets"]
    pointer = "What a phone needs"
    assert setup_text.split(pointer)[0] == configure_text.split(pointer)[0]
    assert "after the setup" in setup_text.split(pointer)[1]


@pytest.mark.parametrize(
    "fact",
    [
        "a value of 39 mg/dL (2.2 mmol/L) or less",
        "because a sensor may measure the limit value itself",
        '\\"At the limit\\" is compared at the precision of the limits: whole mg/dL, or one decimal in mmol/L',
        "The very-low limit of the ranges in the settings cannot be lower than the source's sensor lower limit",
        "A change of a limit applies to readings received after it; stored readings are not changed.",
        "marked as a limit reading",
        "12.71 + 4.70587 × mean glucose in mmol/L",
        "mmol/mol = 10.929 × (% − 2.15) {cite:hoelzel2004}",
        "the GMI goal (always entered in %) is converted in the same way",
        "<code>glucocraft_panel_webhook_id</code> in <code>secrets.yaml</code>",
        "battery optimization disabled for the app",
    ],
)
def test_the_facts_moved_out_of_the_settings_texts_are_in_the_documentation(fact):
    frontend = (PKG / "i18n" / "en" / "frontend.json").read_text(encoding="utf-8")
    assert fact in DOC_TEXT or fact in frontend, fact


# ------------------------------------------------------------------------------------------------ the sources and file
@pytest.mark.parametrize(
    "title",
    [
        "Glucose source not found",
        "Glucose source with a unit other than mg/dL or mmol/L",
        "Glucose source without a unit",
        "GlucoCraft's readings file could not be written",
        "GlucoCraft's cards could not be added to the dashboards",
    ],
)
def test_troubleshooting_explains_the_repair_notices_a_user_can_act_on(title):
    assert title in _doc("docs.section.troubleshooting.body"), title


def test_the_unit_texts_read_and_the_units_refused_are_documented():
    from glucocraft.units import GLUCOSE_UNIT_TEXTS

    unit = next(li for li in _doc("docs.section.how.body").split("<li>") if li.startswith("<b>Unit.</b>"))
    for text in GLUCOSE_UNIT_TEXTS:
        spelled = {"mg/dl": "mg/dL", "mmol/l": "mmol/L"}.get(text, text)
        assert spelled in unit, text
    assert "in any case" in unit and "cannot be chosen as a source" in unit
    assert STRINGS["issues"]["source_unit_not_glucose"]["title"] in unit


def test_the_notification_titles_are_named():
    backend = json.loads((PKG / "i18n" / "en" / "backend.json").read_text(encoding="utf-8"))
    text = _doc("docs.section.notifications.body")
    for key in ("notify.title.daily", "notify.title.weekly", "notify.title.monthly", "notify.no_data.title",
                "notify.import.title", "notify.kept.title", "notify.export.title", "notify.print.title"):  # fmt: skip
        assert backend[key] in text, key


def test_the_gmi_condition_is_not_repeated_in_about_this_report():
    assert "GMI is shown only" not in REPORT["report.about.data.body"]
    assert "GMI" in REPORT["report.metrics.sources"] or "GMI" in REPORT["report.context.sufficiency"]
