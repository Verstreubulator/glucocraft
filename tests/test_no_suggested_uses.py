# License: AGPL-3.0-only (LICENSE).
"""No published text suggests a use for GlucoCraft or its entities (docs/LANGUAGE_STYLE.md rule 24), and GlucoCraft's
notifications are always called notifications, never alarms or alerts.

GlucoCraft provides the tools and describes them; it never suggests that something should be done with them: not as an
alarm, alert, safety or warning system, not for another person to watch, not as a forecast, not as a basis for
treatment, never telling anyone to bring a report to a healthcare professional, and with no automation example.
Neutral facts are allowed (ALLOWED): that the dashboard opens on any device signed in to Home Assistant, that the CGM
Report and its PDF can be printed or shared, for example with a healthcare professional, that notifications can be
sent to more than one phone, and the one voice example of the Documentation's Accessibility section. Two kinds of search,
both case-insensitive and across line breaks, run over every published text: the language catalogues, strings.json,
services.yaml, the README, the community files and the GitHub templates, the published documents, tests/README.md, the
panel text catalogue, the example panel configurations and the testers' starter file.
- SUGGESTIONS: fixed phrases, each matched as written.
- PATTERNS: the constructions of a suggestion in any words (an action toward a healthcare contact or a healthcare
  contact using the data, another person watching, building an automation or alarm, a device switched by the entities,
  an alarm sound, being notified or woken, a prediction or a change announced as coming, a suggested group of users,
  relying on GlucoCraft, using it as an alarm or in place of one, a treatment or food action or a treatment said to be
  needed, a call to act now, a check before an activity such as driving). They run on the text after the known
  negations (NEGATIONS) and the allowed facts (ALLOWED) are removed, so "GlucoCraft provides no automation examples"
  passes.
- ALARM_NAMES: GlucoCraft's own notifications named as alarms or alerts.
PROBES and MUST_FAIL are suggestions written in ordinary words; each must be caught, so that the guard cannot weaken
unnoticed.
The test is a backstop: no list of words catches every suggestion, and the rule is kept by reviewing each new text.
LANGUAGE_STYLE.md is not searched: it quotes the forms in the rules themselves.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from conftest import PANELS, REPO

COMP = REPO / "custom_components" / "glucocraft"

# The forms of a suggested use (lower case; a space matches any run of white space, a line break included)
SUGGESTIONS = (
    # automations and other configurations built on GlucoCraft's entities
    "for the user's own automations",
    "for your own automations",
    "for use in automations",
    "use it in an automation",
    "example automation",
    # alarms, alerts and safety
    "use it as an alarm",
    "use them as alarms",
    "as a backup alarm",
    "as a safety net",
    "to alert you",
    "to warn you",
    "wake you",
    "keep you safe",
    "peace of mind",
    # watching or following another person
    "keep an eye on",
    "keep track of",
    "remote monitoring",
    "for caregivers",
    "for parents",
    "watch over",
    # forecasts and treatment
    "predicts",
    "will go low",
    "will go high",
    "decide how much",
    "dose your",
    # reports for a healthcare professional
    "share with your",
    "share it with",
    "bring to your",
    "bring it to",
    "show your doctor",
    "show it to your",
    "for your doctor",
    "for your appointment",
    "agp report",
)

# The constructions of a suggested use, in any words ([^.] keeps a match inside one sentence)
# people other than the user: relatives, people who look after someone, people nearby
_PERSON = (
    r"(caregivers?|partners?|parents?|family( members?)?|spouses?|loved ones?|household( members?)?|relatives?"
    r"|mums?|moms?|mothers?|dads?|fathers?|grand(ma|pa|mother|father|parent|son|daughter|child)s?|grandchildren"
    r"|babysitters?|nann(y|ies)|roommates?|flatmates?|housemates?|teachers?|coach(es)?|neighbou?rs?|friends?"
    r"|whoever|anyone else|someone else)"
)
_LOOK = r"(see|sees|watch\w*|follow\w*|monitor\w*|check\w*|view|read|glance|peek|look\w*|keep tabs|notice\w*|obvious)"
# people a text could suggest GlucoCraft for (caregiver monitoring, a target group)
_GROUP = r"(parents?|caregivers?|famil(y|ies)|children|kids|teachers?|nurses?|grandparents?|athletes?|seniors?)"
# healthcare contacts and the occasions of a visit
_CARE = (
    r"(doctors?|physicians?|clinicians?|clinic(s|'s)?|practices?|fax(es)?|healthcare (providers?|professionals?|team)"
    r"|diabetes team|care team|endocrinolog\w*|(diabetes )?educators?|nurses?|appointments?|visits?|check-?ups?"
    r"|consultations?|(patient )?portals?)"
)
# what a treatment or a food intake acts on
_DOSE = (
    r"(insulin|doses?|dosing|bolus|basal|medications?|carbs?|carbohydrates?|sugar|juice( box)?|snacks?"
    r"|glucose tablets?|dextrose|grams of|therapy|correction|regimens?|medicines?|meds|pump settings?"
    r"|ratios?)"
)
# devices a sound or light would come from (a light theme or light gray is a color, not a device)
_DEVICE = (
    r"(smart (plugs?|bulbs?|speakers?|lamps?|switch(es)?)|lamps?|bulbs?|lights?(?! (theme|mode|gr[ae]y|colou?r))"
    r"|speakers?)"
)
PATTERNS = {
    "an action toward a healthcare contact": r"\b(share|send|give|bring|show|take|hand|forward|e-?mail|mail|print|fax"
    rf"|upload|submit|post)(e?s)?\b[^.]{{0,80}}\b{_CARE}\b"
    rf"|\b{_CARE}\b[^.]{{0,60}}\b(bring|take|hand|show|share)\b"
    # a contact addressed to the reader ("call your healthcare team"); a manufacturer's instruction reported about
    # "their clinician" is a cited fact, not a suggestion
    r"|\b(call|phone|ring|contact|consult|tell|inform|ask|talk to|speak (to|with)|reach out to|get in touch with)"
    rf" (your|the|a|an)\b[^.]{{0,40}}\b{_CARE}\b",
    "a healthcare contact using the data": r"\b(doctors?|physicians?|clinicians?|endocrinolog\w*|educators?|nurses?"
    r"|care team|diabetes team|healthcare (providers?|professionals?|team))\b[^.]{0,60}\b(appreciate\w*|welcome\w*"
    r"|fine-tune\w*|adjust\w*|review\w*|discuss\w*|go (through|over)|look (at|over)|print-?outs?)\b",
    "another person watching": rf"\b{_PERSON}\b[^.]{{0,60}}\b{_LOOK}\b",
    "another person watching (the reverse order)": rf"\b{_LOOK}\b[^.]{{0,60}}\b{_PERSON}\b",
    "building an automation, alarm or alert": r"\b(build|create|set up|write|make|trigger)\b[^.]{0,60}"
    r"\b(automations?|alarms?|alerts?|sirens?|lights?)\b",
    "a device switched by GlucoCraft's entities": r"\b(hook|connect|link|pair|wire|tie|turn|switch|flash|blink"
    rf"|set)(s|ed)?\b[^.]{{0,60}}\b{_DEVICE}\b",
    "an automation in any words": r"\b(with|via|through|using|by) (an?|your|the|its) (own )?(automation|script"
    r"|blueprint)s?\b|\bwhenever\b[^.]{0,40}\b(reading|glucose|value|zone|sensor|level)\b",
    "an alarm sound": r"\bannounce (every|each|any|all|a|the)\b[^.]{0,30}\b(lows?|highs?|readings?|glucose|values?)\b"
    r"|\b(out loud|sirens?|buzzers?|beepers?|klaxons?|loud (tones?|sounds?|noises?|beeps?)"
    r"|sound(s|ing)? an? (alarm|alert|siren|buzzer|tone|chime|beep))\b",
    "an automation example": r"\bautomation examples?\b",
    "the user's automations": r"\byour (own )?automations\b",
    "being notified or woken": r"\b(notify|notifies|notifying|alerts?|alerting|warns?|warning|wakes?|waking)"
    r" (you|them|someone)\b|\bsleep(s|ing)? (soundly |safely )?through the night\b"
    r"|\bwak(e|es|ing) (up )?(anyone|everyone|the (household|house|family|parents?)|your (partner|parents?|family))\b",
    "being notified or woken (passive)": r"\bbe (notified|woken|alerted)\b",
    "a prediction": r"\b(predict\w*|forecast\w*|foresee\w*|anticipat\w*|extrapolat\w*|before it happens"
    r"|will (go|fall|rise|drop|land|look like)"
    r"|where\b[^.]{0,30}\bheading|where\b[^.]{0,30}\bwill be|going to (be|land|reach|look)|what\b[^.]{0,30}\bwill"
    r" (be|look))\b"
    # a change announced as coming
    r"|\b(drops?|lows?|highs?|rises?|falls?|crash(es)?|spikes?|dips?)\b[^.]{0,20}\b(is|are) (coming|on (its|their|the)"
    r" way|imminent|approaching|ahead)\b|\babout to (drop|fall|rise|go|crash|spike|dip)\b",
    "a suggested group of users": rf"\b{_GROUP}\b[^.]{{0,80}}"
    r"\b(find|finds|benefit|benefits|useful|helpful|ideal|enjoy)\b"
    rf"|\b(useful|helpful|ideal|perfect|designed|made) for\b[^.]{{0,40}}\b{_GROUP}\b",
    "relying on GlucoCraft": r"\b(rel(y|ies|ying) on|count(s|ing)? on|trust)\b[^.]{0,60}"
    r"\b(glucocraft|notifications?|summar(y|ies)|panels?|dashboard|display cards?|readings?|values?|it|them)\b",
    "a treatment action": r"\b(lower|raise|increase|decrease|reduce|adjust|change|skip|take|correct|eat|drink|have|give"
    rf"|bump|plan|time|inject|dose|fine-tune|tweak|cut)s?\b[^.]{{0,40}}\b{_DOSE}\b|(^|[.!?:] )(eat|drink|snack)\b"
    # a treatment said to be needed ("a correction is needed now")
    rf"|\b{_DOSE}\b[^.]{{0,20}}\b(is|are) (needed|required|due|recommended|advised|necessary|called for)\b"
    r"|\b(needs?|requires?|calls? for) (an? )?(correction|bolus|dose|snack|treatment)\b",
    "a call to act": r"\b(act|react|respond|intervene) (now|immediately|quickly|at once|right away|promptly|fast)\b"
    r"|\btake action\b",
    "a check before an activity": r"\b(check|look|glance|test|consult|verify)\w*\b[^.]{0,40}\b(before|prior to"
    r"|while)\b[^.]{0,20}\b(driv(e|es|ing)|exercis\w*|workouts?|sports?|swim\w*|bed(time)?|sleep\w*|meals?|eating"
    r"|cycling)\b|\b(before|while) (you )?(drive|driving|getting behind the wheel)\b",
    "an alarm use": r"\b(use|uses|using|treat|treats|set up|keep|works?|serves?|acts?)\b[^.]{0,40}\bas "
    r"(an? |your |the )?(\w+ ){0,2}(alarms?|alerts?|alarm system|warning system|safety net|safety backup|backup|safeguard|fallback)\b",
    "an alarm replacement": r"\b(stands? in for|stand-ins? for|replac(e|es|ing)|substitut(e|es|ing) for|in place of"
    r"|instead of|backup (to|for)|back(s|ing)? up)\b[^.]{0,40}\b(alarms?|receivers?)\b",
}

# The negations removed before PATTERNS run: each says what GlucoCraft is not or does not do
NEGATIONS = (
    r"provides no automation examples",
    r"a straight-line extrapolation is not a prediction",
    r"is not a prediction",
    r"no forecast",
    r"not alarms",
    # tests/README.md describing this test
    r"suggests a use for glucocraft or its entities \([^)]*\)",
)
# The pull-request requirement that rules alarms, predictions and treatment advice out (CONTRIBUTING.md): it names what
# GlucoCraft does not provide and why, so it is removed before every search (ALARM_NAMES included)
RULED_OUT = (
    r"\*\*No alarms, predictions or treatment advice\.\*\* .*? The project's notifications are ordinary messages and"
    r" are always called notifications\."
)
# The neutral facts that are allowed (LANGUAGE_STYLE.md rule 24), removed before PATTERNS run as well
ALLOWED = (
    r"can be opened on any device signed in to home assistant",
    r"can be sent to more than one phone",
    r"can be printed or shared, for example with a healthcare professional",
)

# Suggestions in ordinary words: every one must be caught by a phrase or a pattern
PROBES = (
    "You can build an automation that turns a light red when glucose is low.",
    "Family members can follow the readings on a wall panel at home.",
    "Share the PDF with your diabetes team before the visit.",
    "Bring the report to your next appointment.",
    "Rely on the low notification so that no low at night goes unnoticed.",
    "Lower the insulin dose when the trend arrow points down.",
    "Email the report to your doctor before the visit.",
    "Use the glucose notifications as your low alarm.",
    "Send the low notification to a parent's phone so that they can watch over their child at night.",
    "A partner can see the value on the panel at night.",
    "The trend arrow indicates where glucose is heading.",
    "Use the Glucose very low sensor to trigger your own alarm.",
    "An automation example: when the zone is very_low, flash the lights.",
    "Set an alert on the binary sensor to be woken at night.",
    "Give the printed report to your doctor.",
    "The panels let caregivers watch the glucose value from another room.",
    "The trend arrow tells you where you will be in half an hour.",
    "Send the PDF straight to your clinic's fax from the print action.",
    "Parents of children with diabetes may find the wall panel useful at night.",
    # the forms of a suggestion in other words: treatment and food, sounds and lights, other people watching, a
    # forecast, a healthcare contact, a stand-in for an alarm
    "Hand the printed CGM Report to your endocrinologist at the next visit.",
    "Let a parent keep an eye on a teenager's numbers from their own phone.",
    "Turn a smart bulb red with an automation whenever the reading drops below 70.",
    "GlucoCraft can sound a siren upstairs so that a low at night does not go unnoticed.",
    "The dashboard estimates where your glucose will be in twenty minutes.",
    "Lower your basal rate when the weekly time below range is above 4%.",
    "Email the 14-day summary to your diabetes educator before each appointment.",
    "Use the High glucose notification as your alarm for rising glucose overnight.",
    "Caregivers can monitor a loved one remotely with the wall panel.",
    "Eat 15 grams of fast-acting carbohydrate when the panel turns red.",
    "Forward the monthly PDF to the endocrinology office ahead of your check-up.",
    "Your diabetes educator will appreciate a printout of the CGM Report.",
    "Grandma can glance at the wall display to see whether her grandson is in range.",
    "Roommates can peek at the shared panel while you are asleep.",
    "Hook the very-high binary sensor to a smart plug that sounds a buzzer.",
    "Turn the bedside lamp red whenever the zone sensor reports low.",
    "The delta tells you roughly what the next reading is going to be.",
    "Extrapolate the trend to see what glucose will look like in 20 minutes.",
    "Bump your correction bolus when the weekly average climbs.",
    "Eat 15 grams of fast sugar when the panel turns red.",
    "Teachers at school can see the student's value on the classroom display.",
    "Let the babysitter know to check the panel every hour.",
    "Treat the phone notification as your safety backup overnight.",
    "The panel can stand in for your CGM receiver's alarm.",
    "Coaches can follow an athlete's numbers from the sideline.",
    "Hand the doctor the PDF at the next consultation.",
    "Night-time lows will be obvious to whoever sleeps next to you, because the panel glows red.",
    "A sleeping child's mum can glance at the dashboard on a tablet.",
    "Upload the CSV export to your clinic's patient portal.",
    "Plan your morning insulin around the daily summary.",
    # and more of them, in other words again
    "Give your kids a juice box as soon as the panel shows Low.",
    "Your spouse can check on you from work through the dashboard.",
    "With the daily summary in hand, your care team can fine-tune your therapy.",
    "The trend arrow shows where you are going to land in half an hour.",
    "Pair the low binary sensor with a smart speaker that plays a loud tone.",
    "Count on the wall panel to wake the household when glucose crashes overnight.",
    "Skip the bedtime snack if the evening summary shows time above range.",
    "Neighbours watching the kids after school can open the dashboard on their phones.",
    "The 14-day report is ready to send to the diabetes clinic.",
    "GlucoCraft works as a backup to your CGM's own low alarm.",
    "Before the endocrinologist appointment, export the readings and bring them along.",
    "Your nephew's coach can look at the wall panel during practice.",
    "Drink a glass of orange juice once the dashboard says very low.",
    "The 5-minute delta lets you foresee a low before it happens.",
    "Connect the high binary sensor to the porch light so it goes amber.",
    "GlucoCraft is a dependable stand-in for the alarm on the CGM app.",
    "Mail a copy of the weekly summary to the nurse at your GP surgery.",
    "Increase the morning dose by one unit if the 14-day mean is above target.",
    "Caregivers at the care home can keep watch over residents with the display card.",
    "Set the speaker in the child's room to announce every low reading out loud.",
)

# Suggestions that an earlier, weaker form of the guard let through: each must be caught
MUST_FAIL = (
    "A drop is coming in the next hour.",
    "Check before driving.",
    "As a caregiver, watch the panel at night.",
    "A correction is needed now.",
    "Call your healthcare team when the GRI climbs.",
    "Adjust your regimen when the mean rises.",
    "Eat something when the value is low.",
    "Act now when it falls.",
    "It sleeps through the night without waking you.",
    "The trend predicts where glucose will be in 30 minutes.",
)

# The published documents (the files included and the optional ones), without LANGUAGE_STYLE.md
PUBLISHED_DOCS = (
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    ".github/pull_request_template.md",
    "tests/README.md",
    "docs/SYSTEM_DESIGN.md",
    "docs/PANELS.md",
    "docs/DASHBOARD.md",
    "docs/DEVELOPER_GUIDE.md",
    "docs/ENTITIES_AND_API.md",
    "docs/DISPLAY_CONTRACT.md",
    "docs/ACCESSIBILITY.md",
    "docs/SCREEN_READER_TEST_SCRIPT.md",
)


def _flat(value) -> list[str]:
    """Every string in a catalogue value (nested objects and lists included)."""
    if isinstance(value, dict):
        return [s for v in value.values() for s in _flat(v)]
    if isinstance(value, list):
        return [s for v in value for s in _flat(v)]
    return [value] if isinstance(value, str) else []


def _texts() -> dict[str, str]:
    """The published texts by where they are: one entry per catalogue key or per file."""
    out: dict[str, str] = {}
    for path in sorted((COMP / "i18n" / "en").glob("*.json")):
        for key, value in json.loads(path.read_text(encoding="utf-8")).items():
            out[f"{path.name} {key}"] = " ".join(_flat(value))
    out["strings.json"] = " ".join(_flat(json.loads((COMP / "strings.json").read_text(encoding="utf-8"))))
    out["services.yaml"] = (COMP / "services.yaml").read_text(encoding="utf-8")
    for name in PUBLISHED_DOCS:
        path = REPO / name
        if path.is_file():
            out[name] = path.read_text(encoding="utf-8")
    for path in sorted((REPO / ".github").rglob("*.y*ml")) if (REPO / ".github").is_dir() else ():
        out[path.relative_to(REPO).as_posix()] = path.read_text(encoding="utf-8")
    if PANELS is not None and (PANELS / "i18n" / "en.yaml").is_file():
        out["panels i18n/en.yaml"] = (PANELS / "i18n" / "en.yaml").read_text(encoding="utf-8")
    # the example panel configurations (their comments are published with them) and the testers' starter file
    # (published beside them)
    for path in sorted(PANELS.glob("glucocraft-*.yaml")) if PANELS is not None else ():
        out[f"panels {path.name}"] = path.read_text(encoding="utf-8")
    return out


TEXTS = _texts()


def _pattern(phrase: str) -> re.Pattern:
    return re.compile(r"\s+".join(re.escape(w) for w in phrase.split(" ")), re.IGNORECASE)


_NEGATION = re.compile("|".join((*NEGATIONS, *ALLOWED, RULED_OUT)), re.IGNORECASE)


def _without_negations(text: str) -> str:
    """The text on one line, with the known negations and the allowed facts removed (each replaced by a full stop)."""
    return _NEGATION.sub(".", re.sub(r"\s+", " ", text))


def _caught(text: str) -> list[str]:
    """The phrases and the patterns that find a suggestion in the text."""
    found = [phrase for phrase in SUGGESTIONS if _pattern(phrase).search(text)]
    clean = _without_negations(text)
    return found + [name for name, rx in PATTERNS.items() if re.search(rx, clean, re.IGNORECASE)]


@pytest.mark.parametrize("phrase", SUGGESTIONS)
def test_no_published_text_suggests_a_use(phrase):
    rx = _pattern(phrase)
    hits = [where for where, text in TEXTS.items() if rx.search(text)]
    assert not hits, f"{phrase!r} in {hits}"


@pytest.mark.parametrize("name", list(PATTERNS))
def test_no_published_text_has_the_construction_of_a_suggestion(name):
    rx = re.compile(PATTERNS[name], re.IGNORECASE)
    hits = {where: m.group(0) for where, text in TEXTS.items() if (m := rx.search(_without_negations(text)))}
    assert not hits, f"{name}: {hits}"


@pytest.mark.parametrize("probe", PROBES)
def test_the_guard_catches_a_suggestion_in_ordinary_words(probe):
    assert _caught(probe), probe


@pytest.mark.parametrize("sentence", MUST_FAIL)
def test_the_guard_catches_a_suggestion_it_once_let_through(sentence):
    assert _caught(sentence), sentence


@pytest.mark.parametrize("phrase", SUGGESTIONS)
def test_the_guard_catches_each_phrase_in_a_sentence(phrase):
    assert phrase in _caught(f"The text says {phrase.upper()} here."), phrase


def test_the_negations_pass_and_hide_nothing_else():
    """A negation passes; the same sentence without the negation is caught."""
    assert not _caught("GlucoCraft provides no automation examples. A straight line is not a prediction.")
    assert _caught("GlucoCraft provides automation examples.")
    assert _caught("The straight line is a forecast.")


def test_the_search_covers_the_published_texts():
    """The search is not empty: the catalogues, strings.json and the README are always there."""
    assert any(k.startswith("documentation.json ") for k in TEXTS) and "strings.json" in TEXTS
    assert "README.md" in TEXTS and "tests/README.md" in TEXTS and len(TEXTS) > 1000
    if PANELS is not None:
        assert "panels glucocraft-panel.yaml" in TEXTS and "panels i18n/en.yaml" in TEXTS


def test_the_ruled_out_requirement_is_removed_once_and_hides_nothing_else():
    """The requirement is in CONTRIBUTING.md once; removing it leaves a suggestion beside it caught."""
    text = re.sub(r"\s+", " ", TEXTS["CONTRIBUTING.md"])
    assert len(re.findall(RULED_OUT, text)) == 1
    found = re.search(RULED_OUT, text).group(0)
    assert _caught(found.replace("**No alarms", "Bring it to your doctor. **No alarms"))
    assert _alarm_names(found + " GlucoCraft sends a low alert.")


def test_the_allowed_facts_pass_and_hide_nothing_else():
    """The neutral facts pass; a suggestion beside them is still caught."""
    assert not _caught("The dashboard can be opened on any device signed in to Home Assistant.")
    assert not _caught("Notifications can be sent to more than one phone.")
    # the neutral statement that GlucoCraft sends no high or low glucose notifications (LANGUAGE_STYLE.md rule 24)
    assert not _caught(
        "GlucoCraft sends no notifications about high or low glucose. Its entities, the range binary sensors among "
        "them, are available to other dashboards, automations and scripts."
    )
    assert _caught("Its entities are available to automations, so build your own low alarm from them.")
    assert _caught("The dashboard can be opened on any device signed in to Home Assistant, so caregivers can watch.")


# GlucoCraft's notifications named as alarms or alerts: "glucose alarm", "a low alert", "GlucoCraft's alerts", an
# "alert notification"; and a sentence that calls its notifications alarms or alerts without a negation
ALARM_NAMES = re.compile(
    r"\b(glucose|low|high|very low|very high|summary|no-data|glucocraft(?:'s)?) (alarms?|alerts?)\b"
    r"|\b(alarm|alert) notifications?\b"
    r"|\bnotifications? (is|are) (an? )?(alarms?|alerts?)\b",
    re.IGNORECASE,
)
ALARM_PROBES = (
    "GlucoCraft sends a low alert to the phone.",
    "The glucose alarms of GlucoCraft are off by default.",
    "Its notifications are alarms.",
    "An alert notification is sent at night.",
)


def _alarm_names(text: str) -> list[str]:
    """GlucoCraft's notifications called alarms or alerts, in the words of a text (HTML class names aside)."""
    words = re.sub(RULED_OUT, ".", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))
    return [m.group(0) for m in ALARM_NAMES.finditer(words)]


def test_glucocrafts_notifications_are_never_called_alarms_or_alerts():
    hits = {where: found for where, text in TEXTS.items() if (found := _alarm_names(text))}
    assert not hits, hits


@pytest.mark.parametrize("probe", ALARM_PROBES)
def test_the_alarm_names_check_catches_a_misnaming(probe):
    assert _alarm_names(probe), probe


def test_the_notifications_section_says_they_are_not_alarms():
    """The notifications are not alarms: said in the disclaimer, the Documentation's Notifications section and the last
    line of every summary."""
    assert "Notifications are not alarms." in TEXTS["documentation.json docs.section.notifications.body"]
    assert TEXTS["backend.json notify.last_line"] == "This is a notification, not an alarm."
    assert "Its notifications are not alarms." in TEXTS["frontend.json disclaimer.text"]


def test_the_rule_is_in_the_language_style():
    path = Path(REPO / "docs" / "LANGUAGE_STYLE.md")
    if not path.is_file():
        pytest.skip("docs/LANGUAGE_STYLE.md is not in this copy of the repository")
    text = path.read_text(encoding="utf-8")
    assert "**Rule 24: no suggested uses.**" in text and "tests/test_no_suggested_uses.py" in text
