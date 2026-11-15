# License: AGPL-3.0-only (LICENSE).
"""One wording per topic (LANGUAGE_STYLE.md rule 25): each text below has one master and is the same in every place that
carries it: the statement *No high or low glucose notifications*, *Sources not supported for live display*,
the three essentials and the after-setup check list; and, each read from its home when the test runs, the standard
statement, the sensor-limit note, the sensor-active sentence, the custom-ranges note, the Other groups statement, the
5-minute sentence, the report's source-name sentences, the panel navigation, the GlucoCraft-rule sentence, the research
appendix and the accessibility limitations."""

from __future__ import annotations

import html
import json
import re

import pytest

from conftest import REPO

PKG = REPO / "custom_components" / "glucocraft"
DOC = json.loads((PKG / "i18n" / "en" / "documentation.json").read_text(encoding="utf-8"))
STRINGS = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))
FRONTEND = json.loads((PKG / "i18n" / "en" / "frontend.json").read_text(encoding="utf-8"))
README = (REPO / "README.md").read_text(encoding="utf-8")
SYSTEM_DESIGN = (REPO / "docs" / "SYSTEM_DESIGN.md").read_text(encoding="utf-8")


def _plain(text: str) -> str:
    """The words of a text: no HTML tags, Markdown list marks, emphasis or code marks, citation tokens or line breaks."""
    text = re.sub(r"\{cite:[^{}]*\}", "", text)
    text = re.sub(r"(?m)^\s*- ", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("**", "").replace("*", "").replace("`", "")
    text = re.sub(r"\s+", " ", text)
    return re.sub(r" ([.,;:)])", r"\1", text).strip()


def _doc(key: str) -> str:
    value = DOC[key]
    return _plain("".join(value) if isinstance(value, list) else value)


PLACES = {
    "Documentation, Notifications": _doc("docs.section.notifications.body"),
    "Documentation, Essentials": _doc("docs.section.essentials.body"),
    "Documentation, Setup": _doc("docs.section.setup.body"),
    "Documentation, Supported Sources": _doc("docs.section.cgms.body"),
    "README": _plain(README),
    "README, Notifications": _plain(README.split("\n## Notifications\n", 1)[1].split("\n## ", 1)[0]),
    "SYSTEM_DESIGN": _plain(SYSTEM_DESIGN),
}

# the statement that GlucoCraft sends no high or low glucose notifications, without its pointer (each place names the
# Documentation's Automations section in its own format)
NO_GLUCOSE_NOTIFICATIONS = [
    "No high or low glucose notifications. GlucoCraft sends no notifications about high or low glucose.",
]
CGM_SECTION = [
    (
        "GlucoCraft needs a new reading every 5 minutes or more often to show a current value. A source that delivers "
        "its readings less often, or hours late, is not supported for live display: its readings are stored and shown, "
        "but the trend, the 5-minute delta and No current data do not work as intended with it. These CGM systems "
        "cannot deliver that:"
    ),
    "FreeStyle Libre 14 day, FreeStyle Libre (first generation)",
    "A new reading only when the sensor is scanned; stored values every 15 minutes",
    "Dexcom Stelo",
    "No real-time sharing; readings reach other software about 3 hours late",
    "Abbott Lingo",
    "Readings shared only about 3 hours late, and only within its sharing range",
    (
        "GlucoCraft is built for readings from a CGM sensor. Readings from a blood glucose meter or entered by hand are "
        "stored and shown, but the statistics, the trend and the data-gap rules are not designed for them."
    ),
    "Readings passed on through a phone's health app are not supported for live display either:",
    "Apple Health (through the Home Assistant iOS app)",
    (
        "No measurement time on the readings; no new readings while the iPhone is locked; Dexcom readings arrive about "
        "3 hours late"
    ),
    "Health Connect (through the Home Assistant Android app)",
    "Readings every 15 minutes at the app's default update frequency; Dexcom readings arrive about 3 hours late",
    "With either, the features listed above do not work as described.",
    (
        "GlucoCraft is designed for every CGM system that delivers readings every 5 minutes or more often. If readings "
        "arrive every 15 minutes or less often for any reason, GlucoCraft shows a Repairs notice. Information as of "
        "October 2026."
    ),
]
ESSENTIALS = [
    (
        "The sensor limits must match the CGM sensor's measurement range. GlucoCraft sets them from the source where "
        "it can and shows a Repairs notice when the readings point to a different limit; otherwise they are set by "
        "hand (Documentation: Readings Outside the Sensor's Range)."
    ),
    (
        "GlucoCraft is designed for readings every 5 minutes or more often. The CGM systems and phone health apps that "
        "cannot deliver that are listed under"
    ),
    (
        "Deleting GlucoCraft deletes every CSV (comma-separated values) export, and also its stored readings when When "
        "GlucoCraft is deleted is set to Delete everything (by default they are kept for a reinstall). To keep the readings, export them and "
        "copy the export files out of the glucocraft_exports folder before deleting; they can be read back in after a "
        "reinstall."
    ),
]
# The check list is one text; only where it points for the measurement ranges differs: the README names the
# Documentation, the Documentation links its own section.
CHECK_LIST = (
    (
        "After the setup, Settings Status and Maintenance show: the unit of the source (mg/dL or mmol/L); the reading "
        "interval of the source (designed for 5 minutes or more often); the sensor lower and upper limits in use ("
    ),
    (
        "); the Maintenance status: No issues, or the issues to resolve, such as a source that updates too slowly or a "
        "sensor limit that does not match."
    ),
)
CHECK_LIST_POINTER = {
    "README": "the Documentation lists the measurement ranges of common CGM systems",
    "Documentation, Setup": "the measurement ranges of common CGM systems: see ",
}


@pytest.mark.parametrize("where", ["Documentation, Notifications", "README, Notifications", "SYSTEM_DESIGN"])
def test_no_high_or_low_glucose_notifications_is_the_same_text_everywhere(where):
    for sentence in NO_GLUCOSE_NOTIFICATIONS:
        assert sentence in PLACES[where], (where, sentence)


def test_the_paragraph_about_the_regulators_is_gone():
    """The statement gives no regulatory reasons: the former paragraph with its two regulator citations is in no place,
    and the Configure screen states only what it holds."""
    texts = {
        "strings.json": json.dumps(STRINGS, ensure_ascii=False),
        "documentation.json": json.dumps(DOC, ensure_ascii=False),
        "README.md": README,
        "SYSTEM_DESIGN.md": SYSTEM_DESIGN,
    }
    for name, text in texts.items():
        for phrase in ("Food and Drug Administration", "Medicines and Healthcare", "provides these alarms"):
            assert phrase not in text, (name, phrase)
    assert "docs.text.no_glucose_notifications" not in DOC
    assert STRINGS["options"]["step"]["notifications"]["description"] == (
        "The summaries, the no-data notice and the phones that receive GlucoCraft's notifications."
    )


@pytest.mark.parametrize("where", ["Documentation, Supported Sources", "README"])
def test_sources_not_supported_for_live_display_is_one_text(where):
    for text in CGM_SECTION:
        assert text in PLACES[where], (where, text)


def test_the_readme_opens_with_the_compatibility_section_and_the_essentials():
    sections = README.split("\n## ")
    assert sections[1].startswith("Sources not supported for live display\n")
    assert sections[2].startswith("Essentials\n")


@pytest.mark.parametrize("where", ["Documentation, Essentials", "README"])
def test_the_three_essentials_are_one_text(where):
    for line in ESSENTIALS:
        assert line in PLACES[where], (where, line)


@pytest.mark.parametrize("where", ["Documentation, Setup", "README"])
def test_the_after_setup_check_list_is_one_text(where):
    head, tail = CHECK_LIST
    text = PLACES[where]
    assert head + CHECK_LIST_POINTER[where] in text, where
    assert tail in text[text.index(head) :], where


def test_old_wordings_are_gone():
    """The earlier wordings of the same topics (rule 25)."""
    texts = {
        "strings.json": json.dumps(STRINGS, ensure_ascii=False),
        "documentation.json": json.dumps(DOC, ensure_ascii=False),
        "frontend.json": (PKG / "i18n" / "en" / "frontend.json").read_text(encoding="utf-8"),
        "README.md": README,
    }
    old = (
        "at least every 5 minutes",
        "need these set manually",
        "need them set manually",
        "own arrow can differ",
        "own arrow may differ",
        "Alarms for low and high glucose are a function",
        "works fully",  # the earlier CGM-systems sentence
        "work only partly",  # the earlier heading and health-app sentence of the unsupported sources
        "or more often for full function",  # the earlier first sentence of the unsupported sources
        "GlucoCraft is built for continuous glucose monitoring",  # the earlier monitoring sentence
    )
    for name, text in texts.items():
        for phrase in old:
            assert phrase not in text, (name, phrase)


# ------------------------------------------------------------------------------------------------ every copy and its home
# Each text below is read from its home (a catalogue key, a document's section or a style rule) when the test runs, and
# every copy is compared with it after the markup is removed (_norm): HTML tags, Markdown emphasis, links and quote
# marks, citation tokens and the citations they stand for, and no-break spaces. A change of the home changes what
# every copy must say; a change of one copy fails here.
REPORT = json.loads((PKG / "i18n" / "en" / "report.json").read_text(encoding="utf-8"))
STYLE = REPO / "docs" / "LANGUAGE_STYLE.md"
NBSP, NARROW_NBSP = chr(0xA0), chr(0x202F)  # the no-break spaces of the catalogues
DASH = chr(0x2014)  # the dash of the standard statement
QUOTES = "[" + chr(0x22) + chr(0x201C) + chr(0x201D) + "]"  # straight and curly double quotes
_CITATION = re.compile(r"\b[A-Z][\w'-]+(?: and [A-Z][\w'-]+)? et al\.,? \d{4}[a-z]?")


def _norm(text: str) -> str:
    """The words of a text with its markup and its citations removed, so that copies in different formats compare."""
    text = re.sub(r"(?m)^\s*> ?", " ", text)
    text = re.sub(r"\{(?:cite|ref):[^{}]*\}", "", text)
    text = re.sub(r"\{sec:[^{}|]*\|([^{}]*)\}", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = _plain(text.replace(NBSP, " ").replace(NARROW_NBSP, " "))
    text = re.sub(r"\(\s*\)", "", _CITATION.sub("", text))
    text = re.sub(r"\s+", " ", text)
    return re.sub(r" ([.,;:)])", r"\1", text).strip()


def _doc_section(path: str, heading: str) -> str:
    """The text of one Markdown section (from its heading to the next heading of the same or a higher level)."""
    text = (REPO / path).read_text(encoding="utf-8")
    level = len(heading) - len(heading.lstrip("#"))
    start = text.index(f"\n{heading}\n")
    end = re.search(rf"\n#{{1,{level}}} ", text[start + len(heading) + 2 :])
    return text[start : start + len(heading) + 2 + end.start()] if end else text[start:]


# the standard statement: the Documentation's (the home); the CGM Report's copy leaves out the manufacturer item and
# gives a report example of a rule (LANGUAGE_STYLE.md rule 25)
def test_the_reports_standard_statement_is_the_documentations():
    home = _norm(DOC["docs.text.standard_statement"])
    copy = _norm(REPORT["report.statement"])
    home_sentences = re.split(r"(?<=\.) (?=[A-Z])", home)
    assert len(home_sentences) == 5
    for sentence in home_sentences:
        if sentence.startswith("Every target and range"):
            sentence = re.sub(rf"; manufacturer: [^{DASH}]*? {DASH}", f" {DASH}", sentence)
            assert sentence in copy, sentence
        elif sentence.startswith("Where GlucoCraft applies a rule of its own"):
            before, after = sentence.split(" (for example ", 1)
            assert before + " (for example " in copy and after.split("), ", 1)[1] in copy, sentence
        else:
            assert sentence in copy, sentence


# the sensor-limit note: the Essentials' first item (the home)
SENSOR_LIMIT_COPIES = {
    "Documentation, the limit note": lambda: _doc("docs.text.limit_note"),
    "Documentation, Readings Outside the Sensor's Range": lambda: _doc("docs.section.lohi.body"),
    "Settings Status": lambda: _plain(FRONTEND["strategy.note.sensor_limits"]),
    "the setup's source screen": lambda: _plain(STRINGS["config"]["step"]["source"]["description"]),
    "Configure > Sources": lambda: _plain(STRINGS["options"]["step"]["sources"]["description"]),
    "README, Essentials": lambda: _plain(README.split("\n## Essentials\n", 1)[1].split("\n## ", 1)[0]),
}


@pytest.mark.parametrize("where", list(SENSOR_LIMIT_COPIES))
def test_the_sensor_limit_note_is_the_essentials_text(where):
    home = re.search(r"The sensor limits must match .*?set by hand", _doc("docs.section.essentials.body")).group(0)
    assert _norm(home) in _norm(SENSOR_LIMIT_COPIES[where]()), where


# the sensor-active sentence: report.sensor_active.note (the home, LANGUAGE_STYLE.md rule 21)
SENSOR_ACTIVE_COPIES = {
    "Consensus Ranges, cited context": lambda: FRONTEND["strategy.cx.active"],
    "Documentation": lambda: DOC["docs.text.sensor_active_note"],
    "README, Features": lambda: README.split("\n## Features\n", 1)[1].split("\n## ", 1)[0],
    "ENTITIES_AND_API.md": lambda: (REPO / "docs" / "ENTITIES_AND_API.md").read_text(encoding="utf-8"),
}


@pytest.mark.parametrize("where", list(SENSOR_ACTIVE_COPIES))
def test_the_sensor_active_sentence_is_the_reports_note(where):
    home = _norm(REPORT["report.sensor_active.note"])
    copy = _norm(SENSOR_ACTIVE_COPIES[where]())
    assert home in copy or (home[0].lower() + home[1:]) in copy, where


# the custom-ranges note: the Documentation's docs.text.custom_note (the home)
CUSTOM_NOTE_COPIES = {
    "Configure > Ranges": lambda: STRINGS["options"]["step"]["ranges"]["description"],
    "Configure > Goals": lambda: STRINGS["options"]["step"]["goals"]["description"],
    "Custom Ranges, About This Page": lambda: FRONTEND["strategy.note.custom_ranges"],
    "DASHBOARD.md": lambda: (REPO / "docs" / "DASHBOARD.md").read_text(encoding="utf-8"),
}


@pytest.mark.parametrize("where", list(CUSTOM_NOTE_COPIES))
def test_the_custom_ranges_note_is_the_documentations(where):
    assert _norm(DOC["docs.text.custom_note"]) in _norm(CUSTOM_NOTE_COPIES[where]()), where


# the Other groups statement: the Consensus Ranges page's strategy.cx.groups (the home)
def test_the_documentations_other_groups_statement_is_the_consensus_pages():
    home = _norm(FRONTEND["strategy.cx.groups"]).removeprefix("Other groups. ")
    metrics = "".join(DOC["docs.section.metrics.body"])
    paragraph = re.search(r'<p id="other-groups">(.*?)</p>', metrics).group(1)
    assert _norm(paragraph).removeprefix("Other groups. ") == home


# "GlucoCraft is designed for readings every 5 minutes or more often." (the Essentials' second item, the home)
FIVE_MINUTE_COPIES = {
    "the setup's source screen": lambda: STRINGS["config"]["step"]["source"]["description"],
    "Configure > Sources": lambda: STRINGS["options"]["step"]["sources"]["description"],
    "Settings Status": lambda: FRONTEND["strategy.note.reading_interval"],
    "Documentation, the interval note": lambda: DOC["docs.text.interval_note"],
    "README, Essentials": lambda: README.split("\n## Essentials\n", 1)[1],
}


@pytest.mark.parametrize("where", list(FIVE_MINUTE_COPIES))
def test_the_five_minute_sentence_is_the_essentials_text(where):
    home = re.search(r"GlucoCraft is designed for readings[^.]*\.", _doc("docs.section.essentials.body")).group(0)
    assert home in _norm(FIVE_MINUTE_COPIES[where]()), where


def test_the_five_minute_rule_in_const_py_is_the_essentials_clause():
    """const.py's comment on the reading-interval notice states the same clause."""
    home = re.search(r"GlucoCraft is designed for readings[^.]*\.", _doc("docs.section.essentials.body")).group(0)
    comments = " ".join(
        line.strip().removeprefix("#").strip()
        for line in (PKG / "const.py").read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("#")
    )
    assert home.removesuffix(".") in comments


def test_the_interval_note_of_settings_status_is_the_documentations():
    assert _norm(FRONTEND["strategy.note.reading_interval"]) == _norm(DOC["docs.text.interval_note"])


# the report's source-name sentences: the report setting (strings.json, the home; LANGUAGE_STYLE.md rule 23)
def test_the_report_source_name_sentences_are_the_report_settings():
    setting = STRINGS["options"]["step"]["report"]["description"]
    home = re.search(r"The report prints the names of the glucose sources\.[^.]*\.", setting).group(0)
    assert home in _doc("docs.section.pages.body")
    if STYLE.is_file():
        assert home in _norm(STYLE.read_text(encoding="utf-8"))


# the panel navigation: the Documentation's Pages and Controls (the home); PANELS.md's Navigation
def test_the_panel_navigation_is_the_documentations():
    body = _doc("docs.section.privacy.body")
    where, nxt, prev, cycle = re.search(
        r"changed by swiping: left (anywhere on the [\w ]+?) for (the next page), right for (the previous page); "
        r"(the pages form one cycle)",
        body,
    ).groups()
    nav = _norm(_doc_section("docs/PANELS.md", "## Navigation"))
    assert f"Swipe left {where}: {nxt}. Swipe right: {prev}. {cycle[0].upper()}{cycle[1:]}" in nav


# "This method is a GlucoCraft rule.": the sentence of LANGUAGE_STYLE.md rule 3 (the home), in every published text
def _published_texts() -> dict[str, str]:
    out = {}
    for path in sorted((PKG / "i18n" / "en").glob("*.json")):
        for key, value in json.loads(path.read_text(encoding="utf-8")).items():
            out[f"{path.name} {key}"] = "".join(value) if isinstance(value, list) else str(value)
    out["strings.json"] = json.dumps(STRINGS, ensure_ascii=False)
    for name in (
        "README.md",
        "docs/SYSTEM_DESIGN.md",
        "docs/DASHBOARD.md",
        "docs/PANELS.md",
        "docs/ENTITIES_AND_API.md",
    ):
        out[name] = (REPO / name).read_text(encoding="utf-8")
    return out


def test_the_rule_sentence_is_the_same_in_every_published_text():
    if not STYLE.is_file():
        pytest.skip("docs/LANGUAGE_STYLE.md is not in this copy of the repository")
    home = re.search(r'by the\s+sentence "([^"]+)"', STYLE.read_text(encoding="utf-8")).group(1)
    assert home == "This method is a GlucoCraft rule."  # the form the copies below are compared with
    stem = home.removesuffix(".")
    bad = {}
    for where, text in _published_texts().items():
        for m in re.finditer(r"This method is\b[^.;,(]*", re.sub(r"\s+", " ", text)):
            if m.group(0).strip() != stem:
                bad[where] = m.group(0)
    assert not bad, bad
    assert sum(_published_texts()[k].count(home) for k in _published_texts()) > 50


# the research appendix: the report catalogue's report.research.* (the home, LANGUAGE_STYLE.md rule 22); the quote in
# SYSTEM_DESIGN.md section 6.4
def test_the_research_appendix_quote_is_the_report_catalogues_text():
    t = REPORT
    parts = [
        t["report.research.intro_lead"],
        t["report.research.intro"],
        t["report.research.events_title"] + ".",
        t["report.research.definition_lead"],
        t["report.research.definition"],
        t["report.research.no_standard_lead"],
        t["report.research.no_standard"],
        t["report.research.method_lead"],
        t["report.research.method_note"],
    ]
    steps = sorted(k for k in t if re.fullmatch(r"report\.research\.method\.\d+", k))
    assert len(steps) >= 6
    for k in steps:
        parts += [t["report.research.step"].format(number=k.rsplit(".", 1)[1]), t[k]]
    parts += [t["report.research.limits_lead"], t["report.research.limits"]]
    expected = re.sub(r"\s+", " ", " ".join(parts).replace(NBSP, " "))
    # a placeholder ({ada_a}) is filled with a citation in the quote: any text without a full stop
    pattern = "[^.]+?".join(re.escape(piece) for piece in re.split(r"[{][a-z_]+[}]", expected))
    section = _doc_section("docs/SYSTEM_DESIGN.md", "### 6.4 CGM Report")
    quote = section[section.index("**The research appendix text.**") :]
    lines = []
    for line in quote.splitlines()[1:]:
        if line.startswith(">"):
            lines.append(line[1:].strip())
        elif lines and line.strip():
            break
    plain = re.sub(r"\s+", " ", " ".join(lines).replace("**", "").replace("*", "").replace(NBSP, " ")).strip()
    assert re.fullmatch(pattern, plain), plain


# the accessibility limitations: the Documentation's Known Limitations (the home); ACCESSIBILITY.md lists the same
# limitations (each with its rule)
LIMITATIONS = ("12 px text", "The automatic contrast", "A new reading", "Windows")


@pytest.mark.parametrize("first_words", LIMITATIONS)
def test_the_known_limitations_are_the_documentations(first_words):
    body = "".join(DOC["docs.section.accessibility.body"])
    table = body[body.index("Known Limitations") : body.index("How This Was Checked")]
    rows = [_norm(r) for r in re.findall(r"<tr><td>(.*?)</td>", table) if _norm(r).startswith(first_words)]
    assert len(rows) == 1, (first_words, rows)
    home = re.sub(QUOTES, "", rows[0].removesuffix("."))
    section = _doc_section("docs/ACCESSIBILITY.md", "## Known limitations")
    listed = re.sub(QUOTES, "", re.sub(r" \(rule \d+\)", "", _norm(section)))
    assert home in listed, home


# ------------------------------------------------------------------------------------------------ texts in their places
def test_the_gmi_mmol_mol_paragraph_of_metrics_and_sources():
    """The two equations of the GMI study and the one conversion factor, labeled as a GlucoCraft rule."""
    body = _doc("docs.section.metrics.body")
    assert (
        "GMI in mmol/mol. The GMI study gives two equations: GMI (%) = 3.31 + 0.02392 × mean glucose in mg/dL, and "
        "GMI (mmol/mol) = 12.71 + 4.70587 × mean glucose in mmol/L. GlucoCraft calculates the mean in mg/dL and "
        "converts it to mmol/L for the second equation with the factor 18.016, the factor it uses for every "
        "conversion. The study's two equations fit each other most closely with a factor of about 18.0. The "
        "difference changes the GMI in mmol/mol by 1 for about 1 in 20 mean values and never changes the GMI in "
        "percent. This method is a GlucoCraft rule."
    ) in body
    raw = "".join(DOC["docs.section.metrics.body"])
    assert "mean glucose in mmol/L {cite:bergenstal2018}. GlucoCraft calculates" in raw


def test_the_banners_say_when_they_end():
    for key in ("ui.banner.expired.text", "ui.banner.expired.text_today"):
        assert FRONTEND[key].endswith(" The banner ends when a later expiry time is reported."), key
    assert FRONTEND["ui.banner.no_data.text"].endswith(" The banner ends when a new reading arrives.")


def test_the_browser_title_is_a_known_limitation():
    """The dashboard's browser title: in ACCESSIBILITY.md's Known limitations and the Documentation's table."""
    title = "GlucoCraft " + chr(0x2013) + " Home Assistant"
    section = _norm(_doc_section("docs/ACCESSIBILITY.md", "## Known limitations"))
    assert (
        f"Every page of GlucoCraft's dashboard has the browser title that Home Assistant gives the dashboard, {title} "
        "(WCAG 2.2 success criterion 2.4.2, Page Titled); the page's own name is shown in Home Assistant's top bar."
    ) in re.sub(QUOTES, "", section)
    body = "".join(DOC["docs.section.accessibility.body"])
    assert (
        f'<tr><td>Every page of the dashboard has the same browser title, "{title}".</td><td>Home Assistant sets the '
        "browser title from the dashboard's name, not from the page.</td><td>The page's name is shown in Home "
        "Assistant's top bar at the top of each page.</td></tr>"
    ) in body


def test_the_six_month_period_is_named_6_months_in_the_notifications():
    """The 180-day period is "6 months" in the notifications and their setting and action texts."""
    backend = json.loads((PKG / "i18n" / "en" / "backend.json").read_text(encoding="utf-8"))
    assert backend["notify.period.180d.label"] == "6 months"
    assert backend["notify.period.180d.title"] == "Rolling 6 Months"
    assert backend["notify.not_enough.line"].startswith("Last {period}: not enough data yet.")
    texts = {
        "strings.json": json.dumps(STRINGS, ensure_ascii=False),
        "services.yaml": (PKG / "services.yaml").read_text(encoding="utf-8"),
        "documentation.json": json.dumps(DOC, ensure_ascii=False),
        "backend.json": json.dumps(backend, ensure_ascii=False),
    }
    for name, text in texts.items():
        assert "rolling 30, 90 and 180 days" not in text and "180 Days" not in text and "180-day summar" not in text, (
            name
        )
    assert "(rolling 30 and 90 days and 6 months)" in texts["services.yaml"]


def test_the_panel_guide_states_facts_without_saying_what_was_not_tested():
    text = (REPO / "docs" / "PANELS.md").read_text(encoding="utf-8")
    assert "The configuration was developed on a board with an\n  engineering sample.\n" in text
    assert "On the development board," in text
    for phrase in ("has not been tested", "have not been tested", "On the board tested"):
        assert phrase not in text, phrase


def test_the_displays_use_the_short_forms_min_max_and_avg():
    """The displays (the display card and the panels) use the short forms Min / Max / Avg for lack of room: the card's
    weekly page "Weekly: Min and Max", its title and the graph key one text with the panels'; the screen-reader texts
    keep the words ("weekly lowest and highest reading"); the dashboard and the CGM Report keep Lowest / Highest."""
    assert FRONTEND["card.page.weekly_min_max"] == "Weekly: Min and Max"
    assert FRONTEND["card.page_in_sentence.weekly_min_max"] == "weekly lowest and highest reading"
    assert FRONTEND["ui.measure.lowest_highest"] == "Lowest and Highest"  # the dashboard
    assert FRONTEND["card.weekly.min_max.title"] == "Min and Max - 14-Day Values"
    assert FRONTEND["card.graph.legend_range"] == "--- Min to max"
