# License: AGPL-3.0-only (LICENSE).
"""One no-current-data system: the flag, what every display shows, the optional notice and the trend after the
stated expiry.

Rules under test:
- One fact, coordinator.no_current_data (published as binary_sensor.glucocraft_no_current_data), decides every display,
  never the status: the display card tests the flag (and its own count of the reading's age while Home Assistant
  cannot reach it).
- No current reading, never a number: during a warm-up the value's place says "Sensor warming up"; while the flag is on
  "Sensor expired" after the stated expiry, else "No current data"; no zone word, "---" for the trend and the
  5-minute delta; the status bar keeps the reading's age. All three words throb (a fade to about 30 % and back every
  2 seconds) for the first 30 minutes of each episode, then hold still, unless GlucoCraft's setting is off; the
  system's reduced-motion setting stops it too; the text description states the words first and does not change with
  the throb.
- After the stated expiry, while readings still arrive, the value and its zone word keep their colors (no gray), the
  trend follows the data, and the red status bar's "Sensor expiry passed" throbs until a new sensor is detected.
- The optional no-data notice: off by default; one notice per outage, once the last reading is as old as the delay and
  the flag is on; removed when a current reading arrives; the phones only where chosen, independently; never repeated,
  also not after a restart during the same outage; its text is a status of GlucoCraft, with no glucose value.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
import types

import pytest
from glucocraft import const as C
from glucocraft import notification as N

from conftest import task_list
from node_runner import needs_node
from test_accessibility import CARD, _card

TAGS = ["glucocraft-display-card", "glucocraft-display-card-wide"]
ARROW = "M4,11V13H16"  # the trend arrow's path


# ------------------------------------------------------------------------------------------------ the display card
@needs_node
@pytest.mark.parametrize("tag", TAGS)
def test_after_the_stated_expiry_a_current_reading_keeps_its_zone_color_and_the_trend_follows(tag):
    out = _card(tag, status="expired", age_min=3, no_current="off", value=120, zone="tight_range", label="Tight range")
    svg, label = out["svg"], out["label"]
    assert "SENSOR EXPIRED" not in svg and "NO CURRENT DATA" not in svg  # no overlay over a current reading
    assert re.search(r'fill="#4caf50"[^>]*>120</text>', svg)  # the zone color
    assert re.search(r'fill:var\(--gc-fg\)"[^>]*>Tight range</text>', svg) and "--gc-old" not in svg  # never gray
    assert ARROW in svg and ">-3</text>" in svg  # the arrow and the 5-minute delta
    assert label.startswith("Sensor expired. ") and "No current data" not in label
    assert "Glucose 120 milligrams per deciliter, tight range." in label and "Falling" in label


@needs_node
@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("compact", [0, 360])
@pytest.mark.parametrize(
    "status,no_current,words,said",
    [
        ("no_current_data", "on", "No current data", "No current data."),
        ("expired", "on", "Sensor expired", "Sensor expired."),
        ("warm_up", "on", "Sensor warming up", "Sensor warming up."),
        ("warm_up", "off", "Sensor warming up", "Sensor warming up."),  # a reading still within the limit
    ],
)
def test_no_current_reading_shows_words_never_a_number(tag, compact, status, no_current, words, said):
    """Without a current reading the value's place says what is the case, in the text color, never a number in the
    value's place; under the words the last reading and when it was taken; no zone word, "---" for the trend and the
    5-minute delta; the status bar keeps the reading's age; the text description states the same words first, then the
    last reading with its time and age, with no zone or trend."""
    out = _card(
        tag,
        status=status,
        age_min=65,
        no_current=no_current,
        value=123,
        zone="tight_range",
        label="Tight range",
        compact=compact,
    )
    svg, label = out["svg"], out["label"]
    texts = re.findall(r"<text[^>]*>([^<]*)</text>", svg)
    lines = re.findall(r"<text[^>]*fill:var\(--gc-fg\)[^>]*>([^<]*)</text>", svg)
    assert words in " ".join(lines), texts  # the words in the text color, on one or two lines
    assert ">123</text>" not in svg and "Tight range</text>" not in svg  # no number, no zone word
    assert ARROW not in svg and texts.count("---") >= 2  # the trend and the 5-minute delta
    assert "Reading: 65 min ago" in texts  # the status bar keeps the reading's age
    assert "Last reading: 123 mg/dL" in lines  # under the words, in the text color
    when = r"(Today|Yesterday) at \d{1,2}:\d\d( AM| PM)?"  # independent of the time of day
    if tag == TAGS[0] and not compact:  # the square card: split before the bracket, as the square panel
        assert any(re.fullmatch(when, t) for t in lines) and "(1 h 5 min ago)" in lines
    else:  # the rectangular card and the compact layout: one line
        assert any(re.fullmatch(when + r" \(1 h 5 min ago\)", t) for t in lines), lines
    assert re.match(
        re.escape(said) + r" Last reading 123 milligrams per deciliter, (today|yesterday) at [^,]+, 1 hour 5 minutes"
        r" ago\. ",
        label,
    ), label
    assert "Glucose" not in label and "tight range" not in label and "Falling" not in label
    assert "NO CURRENT DATA" not in svg and "SENSOR EXPIRED" not in svg


@needs_node
@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize(
    "status,no_current",
    [("no_current_data", "on"), ("expired", "on"), ("warm_up", "on"), ("warm_up", "off")],
)
def test_all_three_words_throb(tag, status, no_current):
    """Every text of the value's place without a current reading throbs: "No current data", "Sensor expired" and
    "Sensor warming up" (also while a reading is still within the limit during a warm-up); here 5 minutes into the
    episode (a reading 20 minutes old, the limit 15 minutes; a warm-up without a known start)."""
    out = _card(
        tag, status=status, age_min=20, no_current=no_current, value=120, zone="tight_range", label="Tight range"
    )
    word = "No current" if status == "no_current_data" else "Sensor"
    line = re.search(r"<text[^>]*>" + word + r"[^<]*</text>", out["svg"]).group(0)
    assert 'class="gc-throb"' in line
    assert re.search(r"<style>\.gc-throb \{ animation-delay: -\d+ms; \}</style>", out["svg"])  # the cycle runs on


@needs_node
@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("age_min,throbs", [(15, True), (44, True), (45, False), (65, False), (600, False)])
def test_the_words_throb_for_the_first_30_minutes_of_an_episode_then_hold_still(tag, age_min, throbs):
    """An episode without current data starts when the reading's age passes the limit (15 minutes here): the words
    throb for its first 30 minutes, then hold still for as long as it lasts, counted from the reading's age (so a
    reload keeps the count); the text and the text description are the same either way."""
    out = _card(tag, status="no_current_data", age_min=age_min, no_current="on")
    assert re.search(r">No current( data)?</text>", out["svg"])
    assert ('class="gc-throb"' in out["svg"]) is throbs
    assert out["label"].startswith("No current data. ")


@needs_node
@pytest.mark.parametrize("tag", TAGS)
def test_an_episode_without_a_known_start_throbs_for_30_minutes_from_when_the_card_first_showed_it(tag):
    """No reading at all (its age cannot be counted): the words throb for the first 30 minutes from the moment the card
    first showed them, then hold still, as the panels count from their start; a new episode starts a new count."""
    later = """
      const svg = () => root.querySelector("svg").innerHTML;
      card._episode.at -= 29 * 60000; card._render(); const at29 = svg();
      card._episode.at -= 2 * 60000; card._render(); const at31 = svg();
      card._episode = { kind: "warm_up", at: 0 }; card._render(); const again = svg();
      return [at29.includes('class="gc-throb"'), at31.includes('class="gc-throb"'), again.includes('class="gc-throb"')];
    """
    out = _card(tag, status="no_current_data", no_current="on", reading=False, extra=later)
    assert 'class="gc-throb"' in out["svg"]  # just shown
    assert out["extra"] == [True, False, True]


@needs_node
@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("minutes,throbs", [(10, True), (29, True), (31, False)])
def test_the_warm_up_words_throb_for_the_first_30_minutes_of_the_warm_up(tag, minutes, throbs):
    started = (
        "states['binary_sensor.glucocraft_sensor_warm_up'] = { state: 'on', last_updated: 'w', attributes: "
        f"{{ started_at: new Date(Date.now() - {minutes} * 60000).toISOString() }} }};"
        "card.hass = { states, fetchWithAuth: async () => ({ ok: false }) }; card._render();"
        "return root.querySelector('svg').innerHTML;"
    )
    out = _card(tag, status="warm_up", age_min=5, no_current="off", extra=started)
    assert re.search(r">Sensor( warming)?( up)?</text>", out["extra"])  # on one line or two
    assert ('class="gc-throb"' in out["extra"]) is throbs
    assert f"Warm-up: {minutes} min" in out["extra"]


@needs_node
@pytest.mark.parametrize("tag", TAGS)
def test_the_setting_turns_the_throb_off_and_the_text_stays_the_same(tag):
    on = _card(tag, status="no_current_data", age_min=20, no_current="on")
    off = _card(tag, status="no_current_data", age_min=20, no_current="on", config={"flash_no_current_data": False})
    assert 'class="gc-throb"' in on["svg"] and "gc-throb" not in off["svg"]
    assert ">No current data</text>" in off["svg"] or ">No current</text>" in off["svg"]  # the words stay, still
    assert on["label"] == off["label"]  # brightness only: the text description is the same


def test_the_throb_is_a_smooth_fade_at_half_a_hertz_stopped_by_reduced_motion():
    assert "const THROB_MS = 3000, THROB_EPISODE_MIN = 30;" in CARD
    assert "@keyframes gc-throb { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }" in CARD
    assert ".gc-throb { animation: gc-throb ${THROB_MS}ms ease-in-out infinite; }" in CARD
    assert "@media (prefers-reduced-motion: reduce) { .gc-throb { animation: none; } }" in CARD
    assert "GC_FLASH = ca.flash_no_current_data !== false;" in CARD  # on unless GlucoCraft's setting is off


@needs_node
@pytest.mark.parametrize("tag", TAGS)
def test_the_setting_also_keeps_the_warm_up_words_still(tag):
    """The setting off: "Sensor warming up" stays still too, and the throb is the only difference."""
    kw = {"status": "warm_up", "age_min": 65, "no_current": "on", "value": 120, "zone": "tight_range"}
    on = _card(tag, label="Tight range", **kw)
    off = _card(tag, label="Tight range", config={"flash_no_current_data": False}, **kw)
    assert 'class="gc-throb"' in on["svg"] and "gc-throb" not in off["svg"] and "No current data" not in on["svg"]
    assert on["label"] == off["label"] and on["label"].startswith("Sensor warming up.")


@needs_node
@pytest.mark.parametrize("tag", TAGS)
def test_a_current_reading_never_throbs(tag):
    # after the stated expiry, readings still arriving: the value in its zone color
    for status in ("current", "expired"):
        out = _card(tag, status=status, age_min=3, no_current="off", value=120, zone="tight_range", label="Tight range")
        assert "gc-throb" not in out["svg"], status


def _texts(svg: str) -> list[str]:
    return re.findall(r"<text[^>]*>([^<]*)</text>", svg)


@needs_node
@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize(
    "age_min,ago",
    [(0, "0 min ago"), (59, "59 min ago"), (60, "1 h 0 min ago"), (1439, "23 h 59 min ago"), (1500, "1 d 1 h ago")],
)
def test_the_last_readings_age_in_minutes_hours_and_days(tag, age_min, ago):
    """The age under the words: whole minutes under an hour, hours and minutes under a day, then days and hours (the
    panels' gc_units.h reading_ago); only the words throb, the lines under them are still."""
    out = _card(tag, status="no_current_data", age_min=age_min, no_current="on", value=64, zone="low", label="Low")
    texts = _texts(out["svg"])
    assert f"({ago})" in texts or any(t.endswith(f" ({ago})") for t in texts), texts
    assert "Last reading: 64 mg/dL" in texts
    lines = re.findall(r"<text[^>]*>(?:Last reading|Today|Yesterday|\()[^<]*</text>", out["svg"])
    assert lines and not any("gc-throb" in line for line in lines)


@needs_node
def test_an_older_reading_shows_its_date_in_the_settings_order():
    """Two days and more: the date (the short month, in the setting's order) instead of Today / Yesterday."""
    for fmt, pattern in (("MDY", r"[A-Z][a-z]{2} \d{1,2} at "), ("DMY", r"\d{1,2} [A-Z][a-z]{2} at ")):
        out = _card(
            TAGS[1], status="no_current_data", age_min=3 * 1440 + 5, no_current="on", config={"date_format": fmt}
        )
        assert any(re.match(pattern + r".+ \(3 d 0 h ago\)$", t) for t in _texts(out["svg"])), fmt


@needs_node
@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("limit,text,spoken", [("low", "LO", "below"), ("high", "HI", "above")])
def test_a_limit_reading_is_shown_as_lo_or_hi(tag, limit, text, spoken):
    out = _card(tag, status="no_current_data", age_min=65, no_current="on", value=40, limit=limit, zone="very_low")
    assert f"Last reading: {text}" in _texts(out["svg"])
    assert "Last reading: 40" not in out["svg"]
    assert out["label"].startswith("No current data. Last reading ") and spoken in out["label"].split(".")[1]


@needs_node
def test_the_last_reading_in_mmol_per_liter():
    out = _card(TAGS[0], status="no_current_data", age_min=65, no_current="on", value=123, unit="mmol/L")
    assert "Last reading: 6.8 mmol/L" in _texts(out["svg"])


@needs_node
@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("compact", [0, 360])
def test_without_any_reading_only_the_words(tag, compact):
    """No reading ever: the words alone, no last reading and no time."""
    out = _card(tag, status="no_current_data", no_current="on", reading=False, compact=compact)
    texts = _texts(out["svg"])
    assert any(t.startswith("No current") for t in texts)
    assert not any(t.startswith(("Last reading", "Today", "Yesterday", "(")) for t in texts), texts
    assert out["label"].startswith("No current data. ") and "Last reading" not in out["label"]


@needs_node
@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("compact", [0, 280, 360])
def test_every_page_and_state_has_the_same_size(tag, compact):
    """The card keeps one size on every page (the About page too) and in every state, as the panels do: the square
    card 480 x 536 and the rectangular card 800 x 536 (the panel's 480 px with the page buttons under it), the compact
    layout one frame for its width."""
    nodata = {"status": "no_current_data", "age_min": 65, "no_current": "on"}
    # the frame: the compact layout's view box, else the design drawing's fixed size (its view box is set once, in the
    # card's template), and the lowest text of the page, which must lie inside it
    frame = (
        "return card._compact() ? card.shadowRoot.querySelector('svg').attrs.viewBox"
        " : `0 0 ${card.D.W} ${card.D.H + card.D.NAV_H}`;"
    )
    runs = [("current", p, {"status": "current"}) for p in range(17)]
    runs += [("no current data", p, nodata) for p in (0, 7, 16)] + [("no reading", 0, {**nodata, "reading": False})]
    boxes = {}
    for state, page, kw in runs:
        out = _card(tag, page=page, compact=compact, extra=frame, **kw)
        boxes[state, page] = out["extra"]
        bottom = max(float(y) for y in re.findall(r'<text[^>]* y="([\d.]+)"', out["svg"]))
        assert bottom < float(out["extra"].split()[3]), (state, page, bottom)
    assert len(set(boxes.values())) == 1, boxes
    if not compact:
        assert set(boxes.values()) == {"0 0 480 536" if tag == TAGS[0] else "0 0 800 536"}
    else:
        assert next(iter(boxes.values())).startswith(f"0 0 {compact} ")


@needs_node
@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("compact", [0, 360])
def test_the_cards_own_count_when_home_assistant_cannot_update_the_flag(tag, compact):
    """The flag kept from before (or none) and a reading older than the limit by the card's own clock: no current
    data, in the design layout and in the compact one."""
    out = _card(tag, status="current", age_min=40, compact=compact, value=137)
    assert re.search(r">No current( data)?</text>", out["svg"]) and 'class="gc-throb"' in out["svg"]
    assert ">137</text>" not in out["svg"]


# Every GlucoCraft entity unavailable (GlucoCraft not loaded, reloading or disabled; the attributes are gone) or
# removed, then the card's clock moved on by the given minutes and the card drawn again.
_GONE = """
  const before = root.querySelector("svg").innerHTML;
  for (const id of Object.keys(states)) {{
    if ({remove}) delete states[id]; else states[id] = {{ state: "unavailable", last_updated: "z", attributes: {{}} }};
  }}
  const RealDate = Date, shift = {minutes} * 60000;
  globalThis.Date = class extends RealDate {{
    constructor(...a) {{ super(...(a.length ? a : [RealDate.now() + shift])); }}
    static now() {{ return RealDate.now() + shift; }}
  }};
  card.hass = {{ states, fetchWithAuth: async () => ({{ ok: false }}) }};
  card._render();
  const s = card._state();
  return {{ before, svg: root.querySelector("svg").innerHTML, old: s.old, thr: s.thr, status: s.status }};
"""


@needs_node
@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("remove", [False, True])
def test_the_card_keeps_counting_while_glucocraft_entities_are_unavailable(tag, remove):
    """A current value, then every GlucoCraft entity unavailable (or removed) for longer than the limit: the card
    keeps the reading's time and counts its age on, so "No current data" takes the value's place, as on the panels."""
    gone = _GONE.format(remove="true" if remove else "false", minutes=25)
    out = _card(
        tag,
        status="current",
        age_min=1,
        no_current="off",
        value=123,
        zone="tight_range",
        label="Tight range",
        extra=gone,
    )
    after = out["extra"]
    assert ">123</text>" in after["before"] and "No current" not in after["before"]  # current before
    assert after["old"] is True and after["status"] == "no_current_data"
    assert re.search(r">No current( data)?</text>", after["svg"]) and ">123</text>" not in after["svg"]
    assert ARROW not in after["svg"] and "Reading: 26 min ago" in after["svg"]


@needs_node
def test_the_no_current_data_limit_is_kept_while_the_flag_entity_is_unavailable():
    """GlucoCraft's limit (here 30 minutes) is kept while the flag entity is unavailable: a reading 20 minutes old
    stays current, never judged by the 15-minute default."""
    keep = """
      const flag = "binary_sensor.glucocraft_no_current_data";
      states[flag] = { state: "off", last_updated: "y", attributes: { threshold_minutes: 30 } };
      card.hass = { states, fetchWithAuth: async () => ({ ok: false }) };
      card._render();
      states[flag] = { state: "unavailable", last_updated: "z", attributes: {} };
      card.hass = { states, fetchWithAuth: async () => ({ ok: false }) };
      card._render();
      const s = card._state();
      return { old: s.old, thr: s.thr, svg: root.querySelector("svg").innerHTML };
    """
    out = _card(TAGS[0], status="current", age_min=20, no_current="off", extra=keep)
    assert out["extra"]["thr"] == 30 and out["extra"]["old"] is False
    assert "No current" not in out["extra"]["svg"]


@needs_node
def test_the_glucose_block_opens_the_weekly_pages_also_without_a_current_reading():
    """The glucose block opens the weekly pages in every state, as on the panels (the words are in the block)."""
    weekly = "card._bf = { trends: [{}] }; return card._canOpenWeekly();"
    expired = _card(TAGS[0], status="expired", age_min=3, no_current="off", extra=weekly)
    stale = _card(TAGS[0], status="expired", age_min=65, no_current="on", extra=weekly)
    assert expired["extra"] is True and stale["extra"] is True


# ------------------------------------------------------------------------------------------------ the no-data notice
class _Notices:
    """persistent_notification as the no-data notice calls it: the notices created and dismissed."""

    def __init__(self):
        self.created: list[tuple[str, str, str]] = []
        self.dismissed: list[str] = []

    def async_create(self, hass, message, title=None, notification_id=None):
        """Record a notification created."""
        self.created.append((notification_id, title, message))

    def async_dismiss(self, hass, notification_id):
        """Record a notification removed."""
        self.dismissed.append(notification_id)


@pytest.fixture
def notice(make_coordinator, fake_hass, monkeypatch):
    """Factory: notice(settings, age_min, flag, sent_for) -> t: t.n the NoDataNotice, t.c its coordinator, t.notices the
    Home Assistant notices, t.hass (its service calls: the phones), t.run() runs the phone messages sent."""
    notices = _Notices()
    monkeypatch.setattr(N, "persistent_notification", notices)

    def make(settings: dict, age_min: float = 45, flag: bool = True, sent_for: int = 0):
        hass = fake_hass(services=("notify.phone",))
        tasks = task_list()  # run by t.run(); the ones a test leaves are closed when it ends
        hass.async_create_task = tasks.append
        c = make_coordinator(hass=hass, current_glucose=112.0, _settings=dict(settings))
        c.last_reading_ts, c.no_current_data, c.no_data_notice_for = int(time.time() - age_min * 60), flag, sent_for
        c.sensor_warming_up = False
        c.saves = 0

        def save():
            c.saves += 1

        c.schedule_state_save = save

        def run_tasks():
            while tasks:
                asyncio.run(tasks.pop(0))

        return types.SimpleNamespace(n=N.NoDataNotice(hass, c), c=c, notices=notices, hass=hass, run=run_tasks)

    return make


HA_ON = {C.CONF_NO_DATA_NOTICE: True}
PHONE = {C.CONF_PHONE_TARGETS: ["phone"], C.CONF_PHONE_TYPES: ["no_data_notice"]}


def test_off_by_default(notice):
    t = notice({}, age_min=600)
    t.n.async_check()
    t.run()
    assert t.notices.created == [] and t.hass.services.calls == [] and t.c.no_data_notice_for == 0
    assert setting_default(C.CONF_NO_DATA_NOTICE) is False and setting_default(C.CONF_NO_DATA_NOTICE_DELAY) == 30


def setting_default(key):
    from glucocraft.settings import setting

    return setting({}, key)


@pytest.mark.parametrize("delay", C.NO_DATA_NOTICE_DELAYS)
def test_created_once_when_the_last_reading_is_as_old_as_the_delay(notice, delay):
    t = notice({**HA_ON, C.CONF_NO_DATA_NOTICE_DELAY: delay}, age_min=delay - 1)
    t.n.async_check()
    assert t.notices.created == []  # not yet
    t.c.last_reading_ts -= 60
    for _ in range(3):  # the coordinator updates every minute: no repeats
        t.n.async_check()
    assert len(t.notices.created) == 1 and t.c.no_data_notice_for == t.c.last_reading_ts and t.c.saves == 1
    nid, title, message = t.notices.created[0]
    assert nid == C.NO_DATA_NOTICE_ID == "glucocraft_no_data_notice" and title == "GlucoCraft No-Data Notice"
    # a notice about GlucoCraft's data: its one sentence, nothing added after it, no glucose value
    assert re.fullmatch(r"GlucoCraft has received no readings since [^\n]+\.", message) and "112" not in message


def test_never_before_there_is_no_current_data(notice):
    """A delay shorter than the no-current-data limit waits for the flag (never earlier than the limit)."""
    t = notice({**HA_ON, C.CONF_NO_DATA_NOTICE_DELAY: 15}, age_min=20, flag=False)
    t.n.async_check()
    assert t.notices.created == []
    t.c.no_current_data = True
    t.n.async_check()
    assert len(t.notices.created) == 1


def test_no_notice_during_a_sensor_warm_up(notice):
    """No readings are expected while a new sensor warms up: no notice, and none is owed for that time."""
    t = notice({**HA_ON, **PHONE}, age_min=45)
    t.c.sensor_warming_up = True
    t.n.async_check()
    t.run()
    assert t.notices.created == [] and t.hass.services.calls == [] and t.c.no_data_notice_for == 0
    t.c.sensor_warming_up = False  # the warm-up ended and readings still do not arrive
    t.n.async_check()
    assert len(t.notices.created) == 1


def test_removed_when_a_reading_arrives_and_a_new_outage_gets_a_new_notice(notice):
    t = notice(HA_ON, age_min=45)
    t.n.async_check()
    t.c.no_current_data, t.c.last_reading_ts = False, int(time.time())
    t.n.async_check()
    assert t.notices.dismissed == [C.NO_DATA_NOTICE_ID] and t.c.no_data_notice_for == 0
    t.n.async_check()
    assert t.notices.dismissed == [C.NO_DATA_NOTICE_ID] and len(t.notices.created) == 1  # no message on the return
    t.c.no_current_data, t.c.last_reading_ts = True, int(time.time() - 3600)
    t.n.async_check()
    assert len(t.notices.created) == 2


def test_the_phones_only_where_chosen_independently_of_the_home_assistant_notice(notice):
    t = notice(PHONE, age_min=45)
    t.n.async_check()
    t.n.async_check()
    t.run()
    assert t.notices.created == []  # the Home Assistant notice is off
    assert [(d, s) for d, s, _ in t.hass.services.calls] == [("notify", "phone")]
    data = t.hass.services.calls[0][2]
    assert data["title"] == "GlucoCraft No-Data Notice" and data["message"].startswith("GlucoCraft has received")
    t = notice({**HA_ON, C.CONF_PHONE_TARGETS: ["phone"]}, age_min=45)
    t.n.async_check()
    t.run()
    assert t.hass.services.calls == []  # the phone type not chosen


def test_a_restart_during_the_same_outage_repeats_nothing_on_the_phones(notice):
    """The outage whose notice was sent is kept (coordinator.no_data_notice_for, saved with the state): after a
    restart the Home Assistant notice, which a restart removes, is shown again; the phones get nothing again."""
    ts = int(time.time() - 45 * 60)
    t = notice({**HA_ON, **PHONE}, age_min=45, sent_for=ts)
    t.n.async_check()
    t.run()
    assert len(t.notices.created) == 1 and t.hass.services.calls == []


def test_turning_the_home_assistant_notice_off_removes_an_open_one(notice):
    t = notice({}, age_min=45)
    assert t.notices.dismissed == [C.NO_DATA_NOTICE_ID]


def test_the_notice_never_depends_on_a_glucose_value(notice):
    """The no-data notice is triggered by no glucose value, range or trend: it reads only the flag, the last reading's
    time and its settings."""
    src = inspect.getsource(N.no_data_notice_due) + inspect.getsource(N.NoDataNotice)
    for word in ("current_glucose", "glucose_zone", "range_", "trend", "current_limit"):
        assert word not in src, word
    for mg in (40.0, 112.0, 400.0):
        t = notice(HA_ON, age_min=45)
        t.c.current_glucose = mg
        t.n.async_check()
        assert len(t.notices.created) == 1
        t.notices.created.clear()


def test_the_coordinator_keeps_the_outage_and_the_entry_wires_the_notice(tmp_path):
    from conftest import REPO, real_coordinator

    c = real_coordinator(tmp_path)
    c.no_data_notice_for = 1_790_000_000
    assert c._state_data()["no_data_notice_for"] == 1_790_000_000
    init = (REPO / "custom_components" / "glucocraft" / "__init__.py").read_text(encoding="utf-8")
    assert "entry.async_on_unload(async_setup_no_data_notice(hass, coordinator))" in init
    assert "*notification_module.NOTIFICATION_IDS," in init  # the removal deletes the notice
    from glucocraft import const, notification

    assert const.NO_DATA_NOTICE_ID in notification.NOTIFICATION_IDS


def test_the_trend_gate_follows_the_flag(tmp_path):
    """coordinator.trend_current tests the flag and the warm-up, never the status (an expired sensor that still
    delivers keeps its trend)."""
    from glucocraft.coordinator import GlucoCraftCoordinator

    src = inspect.getsource(GlucoCraftCoordinator.trend_current.fget)
    assert "return not self.no_current_data and not self.sensor_warming_up" in src
    assert "display_status" not in src
    c = types.SimpleNamespace(no_current_data=False, sensor_warming_up=False, display_status="expired")
    assert GlucoCraftCoordinator.trend_current.fget(c) is True


# ------------------------------------------------------------------------------------------------ the status bar
# The stated expiry 2 hours ago (or in 5 hours), the card drawn again with it
_EXPIRY = """
  states["sensor.glucocraft_sensor_expiry"] = {{ state: "x", last_updated: "e",
    attributes: {{ expires_at: new Date(Date.now() + ({hours}) * 3600e3).toISOString() }} }};
  card.hass = {{ states, fetchWithAuth: async () => ({{ ok: false }}) }}; card._render();
  return root.querySelector("svg").innerHTML;
"""


@needs_node
@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("compact", [0, 360])
def test_the_expiry_text_throbs_until_a_new_sensor_and_the_setting_stops_it(tag, compact):
    """After the stated expiry the status bar's "Sensor expiry passed" throbs (the same fade, once every 3 seconds) for as long as the
    expired status lasts, also while readings arrive, with no time limit; GlucoCraft's setting turns it off; a stated
    expiry still ahead does not throb. The value keeps its zone color (no gray)."""
    line = r'<text[^>]*class="gc-throb"[^>]*>Sensor expiry passed</text>'
    on = _card(tag, status="expired", age_min=3, no_current="off", compact=compact, extra=_EXPIRY.format(hours=-50))
    assert re.search(line, on["extra"]) and "<style>.gc-throb { animation-delay:" in on["extra"]
    off = _card(
        tag,
        status="expired",
        age_min=3,
        no_current="off",
        compact=compact,
        config={"flash_no_current_data": False},
        extra=_EXPIRY.format(hours=-50),
    )
    assert ">Sensor expiry passed</text>" in off["extra"] and "gc-throb" not in off["extra"]
    ahead = _card(tag, status="current", age_min=3, no_current="off", compact=compact, extra=_EXPIRY.format(hours=5))
    assert "gc-throb" not in ahead["extra"] and "Sensor Exp: " in ahead["extra"]


@needs_node
@pytest.mark.parametrize("tag", TAGS)
# (400: a compact card with room for the text beside "Reading: 3 min ago" by the test's width stand-in, 0.57 x the
# size per character; with Roboto it has the room from 360 px on)
@pytest.mark.parametrize("compact", [0, 400])
def test_check_home_assistant_takes_turns_in_the_middle_slot_while_a_notice_is_open(tag, compact):
    """While GlucoCraft has a Repairs notice open (the configuration's attribute attention), the status bar's middle
    slot shows "Check Home Assistant", taking turns every 3 seconds with "Custom ranges" (or the warm-up); alone it
    stands still; the text description says it before the page's sentence; nothing else changes."""
    custom = """
      states["binary_sensor.glucocraft_using_custom_ranges"] = { state: "on", last_updated: "c", attributes: {} };
      card.hass = { states, fetchWithAuth: async () => ({ ok: false }) }; card._render();
      return root.querySelector("svg").innerHTML;
    """
    both = _card(tag, compact=compact, config={"attention": True}, extra=custom)["extra"]
    if not compact:  # (the compact layout shows each text only where it has room between the side texts)
        assert re.search(r'class="gc-turn0"[^>]*>Custom ranges</text>', both)
        assert re.search(r'class="gc-turn1"[^>]*>Check Home Assistant</text>', both)
        turns = r"<style>\.gc-turn0 \{ animation-delay: -\d+ms; \} \.gc-turn1 \{ animation-delay: -\d+ms; \}"
        assert re.search(turns, both)
    alone = _card(tag, compact=compact, config={"attention": True})
    assert ">Check Home Assistant</text>" in alone["svg"] and "gc-turn" not in alone["svg"]
    assert "Check Home Assistant. 6-hour graph" in alone["label"]
    none = _card(tag, compact=compact, config={"attention": False})
    assert "Check Home Assistant" not in none["svg"] and "Check Home Assistant" not in none["label"]


def test_the_turns_are_three_seconds_each():
    assert "const TURN_MS = 3000;" in CARD
    assert "@keyframes gc-turn { 0% { opacity: 1; } 50%, 100% { opacity: 0; } }" in CARD
    assert ".gc-turn0, .gc-turn1 { animation: gc-turn ${2 * TURN_MS}ms step-end infinite; }" in CARD


# a reading time 20 minutes ahead of the clock, with GlucoCraft's attribute time_untrusted
_AHEAD = """
  states["sensor.glucocraft_last_reading_time"].attributes = {{ last_reading_ts: Math.floor(Date.now() / 1000) + 1200,
    time_untrusted: {flag} }};
  card.hass = {{ states, fetchWithAuth: async () => ({{ ok: false }}) }}; card._render();
  return {{ svg: root.querySelector("svg").innerHTML, label: root.querySelector("svg").attrs["aria-label"] }};
"""


@needs_node
@pytest.mark.parametrize("tag", TAGS)
@pytest.mark.parametrize("flag", ["true", "false"])
def test_a_reading_time_ahead_of_the_clock_shows_reading_dashes_and_no_time_line(tag, flag):
    """A reading more than 5 minutes ahead of the clock (time_untrusted, or the card's own count): "Reading: ---" on the
    status bar and, under the words, the last reading without its time; never "0 min ago"."""
    out = _card(tag, status="no_current_data", no_current="on", value=112, extra=_AHEAD.format(flag=flag))["extra"]
    texts = re.findall(r"<text[^>]*>([^<]*)</text>", out["svg"])
    assert "Reading: ---" in texts and not any("min ago" in t for t in texts), texts
    assert "Last reading: 112 mg/dL" in texts
    assert not any(t.startswith(("Today", "Yesterday", "(")) for t in texts)
    assert "ago" not in out["label"] and "Last reading 112 milligrams per deciliter." in out["label"]


@needs_node
@pytest.mark.parametrize("option,returns", [(None, True), (True, True), (False, False)])
def test_the_return_to_the_first_page_is_a_card_option_on_by_default(option, returns):
    """After 3 minutes without use the card returns to its first page, unless the card option return_to_first_page
    is false; the card editor offers the option, on by default."""
    idle = f"""
      if ({json.dumps(option)} !== null) card._cfg.return_to_first_page = {json.dumps(option)};
      card._page = 9; card._lastUse = Date.now() - 200 * 1000; card.isConnected = true;
      let run = null; globalThis.setTimeout = (f) => {{ run = f; return 1; }};
      card._schedule(); run(); return card._page;
    """
    page = _card(TAGS[0], extra=idle)["extra"]
    assert (page == 0) is returns
    form = _card(TAGS[0], extra="return card.constructor.getConfigForm().schema;")["extra"]
    assert form == [{"name": "return_to_first_page", "default": True, "selector": {"boolean": {}}}]


@needs_node
def test_a_warm_up_start_ahead_of_the_clock_shows_plain_warm_up():
    future = """
      states["binary_sensor.glucocraft_sensor_warm_up"] = { state: "on", last_updated: "w",
        attributes: { started_at: new Date(Date.now() + 600e3).toISOString() } };
      card.hass = { states, fetchWithAuth: async () => ({ ok: false }) }; card._render();
      return root.querySelector("svg").innerHTML;
    """
    svg = _card(TAGS[0], status="warm_up", no_current="off", extra=future)["extra"]
    assert ">Warm-up</text>" in svg and "Warm-up: 0 min" not in svg


# ------------------------------------------------------------------------------------------------ smaller card rules
@needs_node
def test_the_last_reading_line_pairs_the_newest_value_with_its_time():
    """The value's entity has no value (its id was changed and not undone) while GlucoCraft calls the reading current:
    the line under the words takes the newest reading's own value (the Last reading time sensor's last_reading_mgdl),
    never a value kept from an earlier reading beside the newer time."""
    newer = """
      states["sensor.glucocraft_last_glucose_reading"] = { state: "unknown", last_updated: "n", attributes: {} };
      Object.assign(states["sensor.glucocraft_last_reading_time"].attributes, { last_reading_mgdl: 140.2 });
      card.hass = { states, fetchWithAuth: async () => ({ ok: false }) }; card._render();
      return root.querySelector("svg").innerHTML;
    """
    svg = _card(TAGS[0], status="current", no_current="off", value=98, age_min=2, extra=newer)["extra"]
    assert ">Last reading: 140 mg/dL</text>" in svg and "Last reading: 98" not in svg


@needs_node
def test_the_source_name_is_cut_by_whole_characters():
    out = _card(
        TAGS[0],
        extra=r"const t = card._fit14('Source ' + '\u{1F600}'.repeat(40), 111);"
        r" return [t.endsWith('...'), /[\uD800-\uDBFF](?![\uDC00-\uDFFF])/.test(t)];",
    )["extra"]
    assert out == [True, False]


@needs_node
def test_a_period_graph_slot_mean_is_rounded_not_truncated():
    slots = """
      const at = Math.floor(Date.now() / 1000);
      card._bf = { at, ts: [at - 3000, at - 2990], val: [100, 101], stats: [], trends: [] };
      const s = card._periodSlots(6, 900);
      return s.avg.filter((v) => v > 0);
    """
    assert _card(TAGS[0], extra=slots)["extra"] == [101]  # 100.5, halves up


@needs_node
def test_the_spoken_weekly_range_keeps_the_two_decimals_shown_beside_the_target():
    """A weekly time in range of 69.96% beside the consensus target of more than 70% is shown and spoken with two
    decimals (never "70.0 percent", which would read as the target's value)."""
    weekly = """
      const at = Math.floor(Date.now() / 1000), week = (tir) => ({ start: at - 7 * 86400, mean: 120, sd: 30, cv: 25,
        gmi: 6.2, tir, titr: 50, low: 2, high: 20, min: 60, max: 240 });
      card._bf = { at, ts: [], val: [], stats: [], trends: [week(69.96), week(80)], dirs: [],
        std: { custom: false, targets: [[">", 70], null, null, null, null, null, null, null, null],
          descs: ["70–180 mg/dL", "", "", "", "", "", "", "", ""] } };
      return card._describeWeekly(0);
    """
    said = _card(TAGS[0], extra=weekly)["extra"]
    assert "from 69.96 to 80.0 percent" in said, said
