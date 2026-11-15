# License: AGPL-3.0-only (LICENSE).
"""Terminology: the CGM Report (never "AGP-style"), the range set of the weekly pages, the names of other products only where necessary,
the privacy, calculations and trademarks sections, the generic sensor-limit reasons, the user-visible keys and the PDF file names."""

from __future__ import annotations

import json
import os
import re
import types
from pathlib import Path

import yaml
from glucocraft import limits as L
from glucocraft import report_service as RS

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "custom_components" / "glucocraft"
STRINGS_RAW = (PKG / "strings.json").read_text(encoding="utf-8")
STRINGS = json.loads(STRINGS_RAW)
SERVICES = (PKG / "services.yaml").read_text(encoding="utf-8")

# the third-party names allowed only in the Documentation section "Readings Outside the Sensor's Range", in
# citations and in the Trademarks section: other products and companies are named only where a text needs the name
NAMES = re.compile(
    r"FreeStyle|Libre|Dexcom|Medtronic|CareLink|Simplera|Guardian|Eversense|Medtrum|SIBIONICS|Sibionics|"
    r"xDrip|Nightscout|Abbott|Guition|Waveshare|™|®"
)


def test_the_settings_texts_name_no_other_product():
    assert not NAMES.findall(STRINGS_RAW)
    assert not NAMES.findall(SERVICES)
    assert not re.search(r"\bHA\b", STRINGS_RAW)


def test_the_limit_options_are_plain_numbers():
    sel = STRINGS["selector"]
    assert sel["sensor_lower_limit"]["options"]["40"] == "40 mg/dL (2.2 mmol/L)"
    assert sel["sensor_lower_limit"]["options"]["50"] == "50 mg/dL (2.8 mmol/L)"
    assert sel["sensor_upper_limit"]["options"]["400"] == "400 mg/dL (22.2 mmol/L)"
    assert sel["sensor_upper_limit"]["options"]["450"] == "450 mg/dL (25.0 mmol/L)"
    assert sel["sensor_upper_limit"]["options"]["500"] == "500 mg/dL (27.8 mmol/L)"
    for step in (STRINGS["config"]["step"]["source"], STRINGS["options"]["step"]["sources"]):
        notes = {**step["data_description"], **step["sections"]["second_source"]["data_description"]}
        for k in (
            "sensor_lower_limit",
            "sensor_upper_limit",
            "secondary_sensor_lower_limit",
            "secondary_sensor_upper_limit",
        ):
            text = notes[k]
            assert "*Readings Outside the Sensor's Range*" in text, k
        # the responsibility sentence once per screen (in its description)
        assert "It is the user's responsibility to know their CGM system" in step["description"]


def test_cgm_report_and_range_set_in_the_settings():
    assert "AGP" not in STRINGS_RAW and "AGP" not in SERVICES
    assert STRINGS_RAW.count("*CGM Report*") == 1  # the report printer field
    assert "Creates the GlucoCraft CGM Report as a PDF" in STRINGS_RAW and "the GlucoCraft CGM Report" in SERVICES
    wp = STRINGS["options"]["step"]["displays"]  # Configure > Display card and wall panels
    assert wp["data"]["weekly_pages"] == "Range set of the weekly pages"
    note = wp["data_description"]["weekly_pages"]
    assert "They use one of two range sets: the consensus ranges or the custom ranges" in note
    assert "standard" not in note.lower()
    assert "your goal" not in STRINGS_RAW.lower()


# the README's Privacy section: one paragraph that points to the Documentation's full statement
README_PRIVACY = (
    "GlucoCraft runs inside the user's Home Assistant and sends no data to its authors or to any cloud service of its "
    "own; it has no telemetry. Data leave Home Assistant only where the user sends them: notifications to the phones "
    "and notify actions selected in the settings, CGM Reports to a printer address set in the settings, and files the "
    "user copies, along with whatever Home Assistant itself sends, such as backups, remote access and voice "
    "assistants, as set up by the user. How a notification reaches a phone is decided by Home Assistant's Companion app (see its "
    "documentation). The full statement, with what each route means, is in the Documentation, *Privacy and Wall "
    "Panels*."
)
TRADEMARKS = (
    "FreeStyle Libre, LibreLinkUp and Lingo are trademarks of Abbott; Dexcom and Stelo are trademarks of Dexcom, Inc.; "
    "Medtronic, CareLink, Simplera and Guardian are trademarks of Medtronic or its affiliates; Eversense is a "
    "trademark of Senseonics, Incorporated; Apple Health and iPhone are trademarks of Apple Inc., and iOS is a "
    "trademark of Cisco, used by Apple under license; Android and Health Connect are trademarks of Google "
    "LLC; Home Assistant and ESPHome are trademarks of the Open Home Foundation; ESP32 is a trademark of Espressif "
    "Systems; Medtrum, SIBIONICS, Guition and Waveshare are trademarks of their respective owners; Claude is a "
    "trademark of Anthropic, PBC; Nightscout and xDrip+ are names of open-source projects; other names are the trademarks of their owners. Each name is used "
    "only to identify the product it names. GlucoCraft is not affiliated with, endorsed by or sponsored by any of "
    "them."
)


def test_privacy_calculations_and_trademarks_in_the_documentation_and_the_readme():
    """The privacy facts, the calculations statement and the trademarks: ordinary sections of the Documentation (Privacy
    and Wall Panels; How GlucoCraft Is Designed; About and License) and of the README. The README's Privacy is the
    short paragraph that points to the Documentation's full statement; the trademarks are the same words in both."""
    from js_i18n import catalogue

    docs = catalogue("documentation")
    flat = lambda v: " ".join((v if isinstance(v, str) else "".join(v)).split())  # noqa: E731
    readme = " ".join((REPO / "README.md").read_text(encoding="utf-8").split())
    privacy = flat(docs["docs.section.privacy.body"])
    for words in (
        "It sends no data to its authors or to any cloud service of its own, and its authors do not receive",
        "Data leave Home Assistant only when the user chooses to send them:",
        (
            "Any cloud service used to bring CGM data into Home Assistant (for example the CGM manufacturer's "
            "service) is governed by that service's own terms."
        ),
        "How a notification reaches a phone is decided by Home Assistant's Companion app (see its documentation).",
    ):
        assert words in privacy, words
    assert README_PRIVACY in readme
    assert "Firebase" not in privacy + readme
    assert all(w in privacy for w in ("Companion app", "printer address", "downloads, exports or shares"))
    assert "<h3>Calculations</h3>" in flat(docs["docs.section.how.body"])
    about = flat(docs["docs.section.about.body"])
    assert "<h3>Trademarks</h3>" in about and TRADEMARKS in about and TRADEMARKS in readme
    assert "LibreView" not in about + privacy


def test_the_automatic_limit_reasons_name_no_product():
    for domain, device in (("dexcom", None), ("carelink", None), ("nightscout", "share2"), (None, None)):
        r = L.resolve("auto", "auto", domain, device)
        for why in (r["lower_reason"], r["upper_reason"]):
            assert why and not NAMES.search(why), (domain, why)


def test_the_pdf_file_names_and_the_cleanup(tmp_path):
    hass = types.SimpleNamespace(config=types.SimpleNamespace(path=lambda *p: str(tmp_path.joinpath(*p))))
    reports = RS.GlucoCraftReports.__new__(RS.GlucoCraftReports)
    reports.hass = hass
    folder = tmp_path / RS.REPORT_DIR
    folder.mkdir()
    for i in range(5):  # files with another name: not GlucoCraft's PDFs, never removed
        (folder / f"glucocraft-agp-style-report_2026-10-0{i + 1}_0800_14d_summary_color.pdf").write_bytes(b"%PDF")
    (folder / "other.pdf").write_bytes(b"%PDF")
    for i in range(RS.REPORT_KEEP):
        reports._keep(f"{RS.REPORT_PREFIX}2026-10-{10 + i}_0800_14d_summary_color.pdf", b"%PDF")
    names = sorted(os.listdir(folder))
    assert RS.REPORT_PREFIX == "glucocraft-cgm-report_"
    assert len([n for n in names if n.startswith("glucocraft-agp-style-report_")]) == 5  # one prefix only
    assert len([n for n in names if n.startswith(RS.REPORT_PREFIX)]) == RS.REPORT_KEEP and "other.pdf" in names


def test_american_spelling_in_the_report_option_and_const():
    texts = STRINGS["services"]["print_report"]["fields"]
    fields = yaml.safe_load(SERVICES)["print_report"]["fields"]
    custom = texts["include_custom_appendix"]["description"]
    assert custom == fields["include_custom_appendix"]["description"]
    assert (
        "shows the statistics calculated with the ranges and goals in the settings, labeled as the user's own "
        "settings; the main report always uses the consensus ranges"
    ) in custom
    for name in ("strings.json", "services.yaml", "const.py", "notification.py"):
        low = (PKG / name).read_text(encoding="utf-8").lower()
        assert "label" + "led" not in low and "col" + "our" not in low, name


# How GlucoCraft is made: the same words in the README (Development) and the Documentation (About and License)
HOW_MADE = (
    "GlucoCraft is written with the help of an AI coding assistant, Claude (Anthropic). The maintainer directs the work "
    "and decides every design question and every public text; the code is checked by the test suite and on test "
    "installations of Home Assistant before each release."
)


def test_how_glucocraft_is_made_is_one_text_in_the_readme_and_the_documentation():
    """The statement that GlucoCraft is written with an AI coding assistant stands word for word in the README's
    Development section and in the Documentation's About and License section."""
    readme = " ".join((REPO / "README.md").read_text(encoding="utf-8").split())
    assert "**How GlucoCraft is made.** " + HOW_MADE in readme
    doc = json.loads((REPO / "custom_components/glucocraft/i18n/en/documentation.json").read_text(encoding="utf-8"))
    assert "<h3>How GlucoCraft is made</h3><p>" + HOW_MADE + "</p>" in doc["docs.section.about.body"]
