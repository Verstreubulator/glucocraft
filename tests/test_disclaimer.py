# License: AGPL-3.0-only (LICENSE).
"""The disclaimer in three forms (const.py DISCLAIMER, the long form; DISCLAIMER_SHORT, the short form of the panels'
and the display card's About page; DISCLAIMER_LINE, the one line), each the same words in every place that shows it;
the setup's first screen shows the long form with no field and stores nothing; no Terms of Use, no acceptance."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
import yaml
from glucocraft import config_flow as cf
from glucocraft import const as C
from glucocraft import flow_schemas as fs
from glucocraft import repairs
from glucocraft.i18n import t

from conftest import NO_PANELS, PANELS
from js_i18n import catalogue
from js_i18n import text as i18n_text

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "custom_components" / "glucocraft"
STRINGS = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))

# The disclaimer's three forms, word for word (its first sentence in bold where the place has emphasis):
# the long form, the short form and the one line
TEXT = (
    "For development and testing only. GlucoCraft is free, open-source software, made available for development and "
    "testing. It is not intended for any medical purpose, including diagnosing, monitoring, treating or managing "
    "diabetes or any other medical condition, or for making treatment decisions. It has not been cleared, approved or "
    "certified as a medical device by the FDA or any other regulator. Its information and notifications can be late, "
    "missing or wrong. Its notifications are not alarms. GlucoCraft is licensed under the GNU Affero General Public "
    "License, version 3 only (AGPL-3.0-only), and is provided without warranty and, as far as the law allows, without "
    "liability (sections 15 and 16 of the license)."
)
SHORT = (
    "For development and testing only. GlucoCraft is free, open-source software, made available for development and "
    "testing. It is not intended for any medical purpose, such as diagnosing, monitoring or managing diabetes or making "
    "treatment decisions, and has not been cleared or approved by the FDA or any other regulator. Its information and "
    "notifications can be late, missing or wrong. Its notifications are not alarms. GlucoCraft is licensed under the "
    "AGPL-3.0-only, without warranty or, as far as the law allows, liability."
)
LINE = "For development and testing only. Not for medical use."


def _flat(text: str) -> str:
    """A text without HTML tags and Markdown emphasis or quote marks, whitespace collapsed."""
    text = re.sub(r"<[^>]+>", "", text).replace("**", "")
    return " ".join(re.sub(r"^> ?", "", text, flags=re.MULTILINE).split())


def _quoted(markdown: str) -> list[str]:
    """The quoted paragraphs ("> ...") of a Markdown file, each as one line."""
    out, cur = [], []
    for line in markdown.splitlines() + [""]:
        if line.startswith(">"):
            cur.append(line[1:].strip())
        elif cur:
            out.append(" ".join(cur))
            cur = []
    return out


# ------------------------------------------------------------------------------------------------ the one text
def test_the_source_is_the_disclaimer_text():
    assert C.DISCLAIMER_PLAIN == TEXT
    assert C.DISCLAIMER == "**For development and testing only.**" + TEXT.removeprefix(C.DISCLAIMER_LEAD)
    assert C.DISCLAIMER_SHORT_PLAIN == SHORT
    assert C.DISCLAIMER_SHORT == "**For development and testing only.**" + SHORT.removeprefix(C.DISCLAIMER_LEAD)
    assert C.DISCLAIMER_LINE == LINE
    assert C.DISCLAIMER_LEAD == "For development and testing only." and TEXT.startswith(C.DISCLAIMER_LEAD)
    # the forms state the intended purpose, not a legal status
    for form in (TEXT, SHORT, LINE):
        assert "not a medical device" not in form.lower(), form


@pytest.mark.parametrize("name", ["README.md", "CONTRIBUTING.md"])
def test_the_readme_and_contributing_carry_the_disclaimer_once_in_full(name):
    text = (REPO / name).read_text(encoding="utf-8")
    quoted = [q for q in _quoted(text) if "development and testing only" in q]
    assert [_flat(q) for q in quoted] == [TEXT], name
    assert quoted[0].startswith("**For development and testing only.**"), name
    assert _flat(text).count("Its notifications are not alarms.") == 1, name


def test_the_catalogue_copy_is_the_same_text():
    """The report page and the Documentation show the catalogue's disclaimer.text (the long form); the display card's
    About page shows disclaimer.short (the short form, as on the panels)."""
    assert _flat(i18n_text("disclaimer.text")) == TEXT
    assert i18n_text("disclaimer.text").startswith("<b>For development and testing only.</b> ")
    assert _flat(i18n_text("disclaimer.short")) == SHORT
    assert i18n_text("disclaimer.short").startswith("<b>For development and testing only.</b> ")
    docs = catalogue("documentation")
    assert docs["docs.section.disclaimer.title"] == "Disclaimer"
    # the text as written: no decimal-comma conversion of its version numbers (class nodec)
    assert docs["docs.section.disclaimer.body"] == ['<p class="nodec">{disclaimer}</p>']


def test_the_panels_show_the_same_text():
    if PANELS is None:
        pytest.skip(NO_PANELS)
    panel = yaml.safe_load((PANELS / "i18n" / "en.yaml").read_text(encoding="utf-8"))
    assert f"{panel['panel.about.title']} {panel['panel.about.disclaimer']}" == SHORT  # the short form
    assert panel["panel.startup.line"].replace("\n", " ") == LINE  # one sentence per line on the start-up screen
    assert not [k for k in panel if k.startswith("panel.terms.")]
    assert "panel.about.restriction" not in panel and "panel.about.license" not in panel
    # the generated English texts the panels are built with (tools/make_panel_texts.py), without the fonts
    generated = PANELS / "i18n" / "generated" / "en"
    subs = yaml.safe_load((generated / "texts.yaml").read_text(encoding="utf-8"))["substitutions"]
    assert f"{subs['t_about_title']} {subs['t_about_disclaimer']}" == C.DISCLAIMER_SHORT_PLAIN == SHORT
    assert subs["t_startup_line"].replace("\n", " ") == C.DISCLAIMER_LINE == LINE
    header = (generated / "gc_text.h").read_text(encoding="utf-8")
    assert (
        'inline constexpr const char *startup_line = "For development and testing only.\\nNot for medical use.";'
        in header
    )
    assert f'inline constexpr const char *about_title = "{C.DISCLAIMER_LEAD}";' in header
    assert f'inline constexpr const char *about_disclaimer = "{SHORT.removeprefix(C.DISCLAIMER_LEAD + " ")}";' in header


def test_the_pdf_shows_the_disclaimer_in_about_this_report_and_the_line_in_its_footer():
    from glucocraft import report_pdf as P

    assert C.DISCLAIMER_LINE == LINE
    assert P.footer_line(1, 3) == "GlucoCraft. For development and testing only. Not for medical use. · page 1 of 3"
    about = (PKG / "report_pdf.py").read_text(encoding="utf-8")
    assert "(DISCLAIMER_LEAD, DISCLAIMER_PLAIN.removeprefix(DISCLAIMER_LEAD).strip())" in about
    assert t("report.license.title") == "License"


# ------------------------------------------------------------------------------------------------ the short lines
def test_the_short_lines_are_exact():
    assert i18n_text("strategy.footer.disclaimer") == LINE + " [Disclaimer]({url})"
    assert t("notify.last_line") == "This is a notification, not an alarm."
    assert t("report.footer") == "GlucoCraft. {disclaimer} · page {page} of {total}"


# ------------------------------------------------------------------------------------------------ the setup
def _setup_flow(monkeypatch):
    from test_config_flow_options import _hass

    flow = cf.GlucoCraftConfigFlow()
    flow.hass = _hass()  # no readings kept by an earlier installation
    shown, reached = [], []
    flow._async_current_entries = lambda include_ignore=None: []  # no GlucoCraft entry yet
    flow.async_show_form = lambda **kw: (shown.append(kw), ("form", kw))[1]

    async def source(user_input=None):
        reached.append("source")
        return ("form", {"step_id": "source"})

    monkeypatch.setattr(flow, "async_step_source", source)
    flow.async_create_entry = lambda **kw: pytest.fail("no entry may be created on the first screen")
    return flow, shown, reached


def test_the_first_setup_screen_shows_the_disclaimer_with_no_field(monkeypatch):
    monkeypatch.setattr(fs.vol, "Schema", lambda fields: fields)  # the stub: the form's fields as given
    flow, _shown, reached = _setup_flow(monkeypatch)
    kind, kw = asyncio.run(flow.async_step_user())
    assert kind == "form" and kw["step_id"] == "user" and reached == []
    assert kw["data_schema"] == {}  # no checkbox: Submit continues
    assert kw["description_placeholders"] == {"disclaimer": C.DISCLAIMER}
    user = STRINGS["config"]["step"]["user"]
    assert user == {"title": "GlucoCraft Disclaimer", "description": "{disclaimer}"}


def test_submit_continues_and_stores_nothing(monkeypatch):
    flow, _shown, reached = _setup_flow(monkeypatch)
    asyncio.run(flow.async_step_user({}))
    assert reached == ["source"] and flow._data == {}


# ------------------------------------------------------------------------------------------------ no Terms
def test_no_terms_document_and_no_acceptance():
    # the disclaimer and the license are the only legal texts: no document titled terms of use
    title = re.compile(r"#\s+(?:\w+\s+)?Terms of Use\s*$", re.IGNORECASE)
    firsts = {p.name: p.read_text(encoding="utf-8").split("\n")[0] for p in (REPO / "docs").glob("*.md")}
    assert not [name for name, first in firsts.items() if title.match(first)]
    assert "terms_of_use" not in STRINGS["issues"] and "disclaimer_required" not in STRINGS["config"].get("error", {})
    for name in ("CONF_TERMS_VERSION", "CONF_DISCLAIMER_ACCEPTED", "TERMS_SHORT", "TERMS_IN_BRIEF", "PHONE_LAST_LINE"):
        assert not hasattr(C, name), name
    assert not hasattr(repairs, "async_check_terms")
    assert type(asyncio.run(repairs.async_create_fix_flow(None, "clock_behind", None))).__name__ == "ClockRepairFlow"


def test_strings_and_en_identical():
    assert (PKG / "strings.json").read_bytes() == (PKG / "translations" / "en.json").read_bytes()


def test_no_contractions_in_the_setup_texts():
    blob = json.dumps([STRINGS["selector"], STRINGS["config"], STRINGS["options"]], ensure_ascii=False)
    assert not re.search(r"\b(don't|can't|won't|isn't|aren't|it's|you're|doesn't)\b", blob, re.IGNORECASE)
    assert "You must" not in json.dumps(STRINGS)  # formal wording, no commands


# ------------------------------------------------------------------------------------------------ the notices
def test_operational_notices_add_nothing_after_their_text(monkeypatch):
    """Panel Added and Panel Offline are operational notices: their own text, no disclaimer, no last line."""
    from test_i18n_english_output import _panel_notices

    notices = _panel_notices(monkeypatch)
    assert {k.split("|")[0] for k in notices} == {"panel_added", "panel_offline"}
    for name, text in notices.items():
        for phrase in ("Do not rely", "not an alarm", "For development and testing only"):
            assert phrase not in text, (name, phrase)
