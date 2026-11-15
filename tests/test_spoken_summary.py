# License: AGPL-3.0-only (LICENSE).
"""sensor.glucocraft_spoken_summary and the shared spoken text (spoken.py).

Rules under test (docs/ACCESSIBILITY.md rules 4 and 9, docs/ENTITIES_AND_API.md):
- One sentence of at most 255 characters, in the order of rule 4: the data status when there is a problem, the value
  with its unit written out (a LO / HI reading in words) and the zone word, "measured at" the time of the reading, the
  trend words. Never an age, which would become wrong while the state stands; a reading of an earlier day is named
  with its day ("yesterday at 9:45 PM", "on October 22 at 9:45 PM"), and the sentence is made again at midnight.
- Both display units and the decimal separator in use; every status.
- The entity is disabled by default, has no unit and no state class (no long-term statistics), and makes its
  sentence again only when the reading or the status changes, never on the minute tick alone.
- The words are those of the cards' shared spoken-text functions.
"""

from __future__ import annotations

import itertools
import json
import re
import time
import types
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from glucocraft import const as C
from glucocraft import sensor as se
from glucocraft import spoken as sp
from glucocraft.limits import LIMIT_FLAGS
from glucocraft.regional import Regional

from js_i18n import catalogue
from test_settings_harness import build, entry, fixed_clock, series, tz_patch  # noqa: F401 (fixtures)

pytestmark = pytest.mark.usefixtures("tz_patch", "fixed_clock")

WWW = Path(__file__).resolve().parents[1] / "custom_components" / "glucocraft" / "www"
STATUSES = ("warm_up", "expired", "no_data", "no_current_data", "expiring_soon", "current")


# The longest state Home Assistant stores; the Spoken summary sentence must always fit
MAX_STATE_LENGTH = 255


def _say(
    status="current",
    current=True,
    mg=112.0,
    limit=None,
    zone="tight_range",
    trend="Changing Slowly",
    when="at 7:46 AM",
    unit="mg/dL",
    dec=".",
):
    return sp.spoken_summary(
        status=status,
        current=current,
        mg=mg,
        limit=limit,
        zone=zone,
        trend=trend,
        reading_when=when,
        unit=unit,
        dec=dec,
    )


# ------------------------------------------------------------------------------------------------ the sentence
@pytest.mark.parametrize(
    "unit, dec, mg, expected",
    [
        (
            "mg/dL",
            ".",
            112.0,
            "Glucose 112 milligrams per deciliter, tight range, measured at 7:46 AM, changing slowly.",
        ),
        (
            "mg/dL",
            ".",
            70.5,
            "Glucose 71 milligrams per deciliter, tight range, measured at 7:46 AM, changing slowly.",
        ),
        (
            "mmol/L",
            ".",
            112.0,
            "Glucose 6.2 millimoles per liter, tight range, measured at 7:46 AM, changing slowly.",
        ),
        (
            "mmol/L",
            ",",
            112.0,
            "Glucose 6,2 millimoles per liter, tight range, measured at 7:46 AM, changing slowly.",
        ),
        (
            "mmol/L",
            ".",
            70.0,
            "Glucose 3.9 millimoles per liter, tight range, measured at 7:46 AM, changing slowly.",
        ),
    ],
)
def test_the_sentence_in_both_units(unit, dec, mg, expected):
    assert _say(mg=mg, unit=unit, dec=dec) == expected


@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
@pytest.mark.parametrize("status", STATUSES)
def test_every_status(unit, status):
    current = status in ("current", "expiring_soon", "expired")
    said = _say(status=status, current=current, unit=unit, trend="Falling")
    words = "milligrams per deciliter" if unit == "mg/dL" else "millimoles per liter"
    value = "112" if unit == "mg/dL" else "6.2"
    expected = {
        "warm_up": "Sensor warming up.",
        "no_data": "No glucose reading yet.",
        "no_current_data": "No current data. Last reading at 7:46 AM.",
        "expired": f"Sensor expired. Glucose {value} {words}, tight range, measured at 7:46 AM, falling.",
        "expiring_soon": f"Glucose {value} {words}, tight range, measured at 7:46 AM, falling.",
        "current": f"Glucose {value} {words}, tight range, measured at 7:46 AM, falling.",
    }[status]
    assert said == expected


def test_no_current_data_says_no_value():
    """Without current data no value, zone or trend is said: the reading may no longer be the glucose now."""
    assert _say(status="no_current_data", current=False) == "No current data. Last reading at 7:46 AM."
    assert _say(status="expired", current=False) == "Sensor expired. No current data. Last reading at 7:46 AM."
    assert _say(status="current", current=False, when=None) == "No current data."
    assert _say(mg=None, status="current") == "No glucose reading yet."


@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
def test_lo_and_hi_in_words(unit):
    assert _say(mg=40.0, limit="low", zone="very_low", trend="Falling", unit=unit) == (
        "Glucose below the sensor's lower limit, very low, measured at 7:46 AM, falling."
    )
    assert _say(mg=400.0, limit="high", zone="very_high", trend="Rising Quickly", unit=unit) == (
        "Glucose above the sensor's upper limit, very high, measured at 7:46 AM, rising quickly."
    )


@pytest.mark.parametrize("zone, word", [(z, C.display_zone_word(z).lower()) for z in C.ZONES] + [(None, None)])
def test_every_zone_word(zone, word):
    said = _say(zone=zone)
    assert (f", {word}, " in said) if word else said.startswith("Glucose 112 milligrams per deciliter, measured")


@pytest.mark.parametrize("when", ["at 7:46 AM", "at 07:46", "yesterday at 11:58 PM"])
def test_the_time_of_the_reading_and_never_an_age(when):
    """The time in the time format setting (a reading of another day with its day); no age, which would go stale."""
    said = _say(when=when)
    assert f", measured {when}, changing slowly." in said and " ago" not in said
    assert _say(when=None) == "Glucose 112 milligrams per deciliter, tight range, changing slowly."


TODAY = date(2026, 10, 24)


@pytest.mark.parametrize(
    "measured, time_format, date_format, expected",
    [
        (datetime(2026, 10, 24, 7, 46), "12", "MDY", "at 7:46 AM"),
        (datetime(2026, 10, 24, 7, 46), "24", "DMY", "at 07:46"),
        (datetime(2026, 10, 23, 21, 45), "12", "MDY", "yesterday at 9:45 PM"),
        (datetime(2026, 10, 23, 21, 45), "24", "DMY", "yesterday at 21:45"),
        (datetime(2026, 10, 22, 21, 45), "12", "MDY", "on October 22 at 9:45 PM"),
        (datetime(2026, 10, 22, 21, 45), "24", "DMY", "on 22 October at 21:45"),
        (datetime(2026, 10, 22, 21, 45), "24", "YMD", "on October 22 at 21:45"),
        (datetime(2025, 12, 31, 23, 59), "12", "MDY", "on December 31, 2025 at 11:59 PM"),
        (datetime(2025, 12, 31, 23, 59), "24", "DMY", "on 31 December 2025 at 23:59"),
    ],
)
def test_a_reading_of_an_earlier_day_is_named_with_its_day(measured, time_format, date_format, expected):
    """ "Last reading at 9:45 PM" alone would sound like today: yesterday's reading says so, an older one its date in
    the date format setting, with the year when it is not the current year."""
    rg = Regional(time_format, date_format)
    assert sp.reading_when(measured, TODAY, rg) == expected
    assert _say(status="no_current_data", current=False, when=expected) == f"No current data. Last reading {expected}."


def test_the_trend_words_and_no_arrow():
    for _, word, arrow in C.TREND_LIBRE_BANDS:
        assert _say(trend=word).endswith(f", {word.lower()}.")
        assert sp.speak_trend(arrow) == word.lower()
    assert ", unknown" not in _say(trend="Unknown") and sp.speak_trend("") == ""
    assert not re.search("[↑↗→↘↓]", _say())


def test_the_longest_sentence_fits_a_state():
    longest = max(
        (
            _say(
                status=s,
                current=cur,
                mg=mg,
                limit=lim,
                zone=z,
                trend=t,
                unit=u,
                dec=",",
                when="on September 30, 2025 at 11:59 PM",
            )
            for s, cur, (mg, lim), z, t, u in itertools.product(
                STATUSES,
                (True, False),
                ((399.9, None), (40.0, "low"), (500.0, "high")),
                C.ZONES,
                [w for _, w, _ in C.TREND_LIBRE_BANDS],
                ("mg/dL", "mmol/L"),
            )
        ),
        key=len,
    )
    assert len(longest) <= MAX_STATE_LENGTH and len(longest) < 140, longest


def test_the_same_words_as_the_cards():
    """The unit names, the trend words and the limit words are those of the cards' shared block (catalogue spoken.*)."""
    cards, backend = catalogue("frontend"), catalogue("backend")
    keys = [f"spoken.unit.{uid}.{form}" for uid in sp.SPOKEN_UNITS.values() for form in ("one", "other")]
    keys += [f"spoken.limit.{flag}" for flag in LIMIT_FLAGS]
    keys += [f"spoken.trend.{key}" for key in C.TREND_KEYS.values()]
    for key in keys:
        assert key in backend and backend[key] == cards.get(key), key
    for _, word, _ in C.TREND_LIBRE_BANDS:
        assert sp.speak_trend(word) == word.lower()
    # every spoken text the two catalogues share is the same
    assert not [k for k in backend if k.startswith("spoken.") and k in cards and backend[k] != cards[k]]


# ------------------------------------------------------------------------------------------------ the entity
def _entity(run):
    ent = next(
        e
        for e in se.split_by_features(run.c, se.entity_groups(run.c, types.SimpleNamespace(entry_id="e1", data={})))[0]
        if e.entity_id == "sensor.glucocraft_spoken_summary"
    )
    ent.coordinator = run.c
    return ent


def test_the_entity_is_disabled_by_default_and_has_no_statistics(tmp_path):
    ent = _entity(build(tmp_path))
    assert ent._attr_entity_registry_enabled_default is False
    # set nowhere in GlucoCraft's classes (the test stub of SensorEntity answers every attribute)
    own = [vars(k) for k in type(ent).__mro__ if k.__module__.startswith("glucocraft")]
    for attr in ("_attr_state_class", "_attr_native_unit_of_measurement", "_attr_device_class"):
        assert not any(attr in v for v in own), attr
    assert "_attr_name" not in vars(ent) and ent._attr_translation_key == "spoken_summary"
    strings = json.loads((WWW.parent / "strings.json").read_text(encoding="utf-8"))
    assert strings["entity"]["sensor"]["spoken_summary"]["name"] == "Spoken summary"


@pytest.mark.parametrize("unit", ["mg/dL", "mmol/L"])
def test_the_entity_speaks_the_current_reading(tmp_path, unit):
    now = int(time.time())
    run = build(tmp_path, entry(unit=unit), readings=series(now, 1, age=120), now=now)
    said = _entity(run).native_value
    words = "milligrams per deciliter" if unit == "mg/dL" else "millimoles per liter"
    assert said.startswith("Glucose ") and words in said and ", measured at " in said and " ago" not in said
    assert len(said) <= MAX_STATE_LENGTH


def test_the_entity_changes_only_with_a_reading_or_a_status(tmp_path, monkeypatch):
    now = int(time.time())
    run = build(tmp_path, readings=series(now, 1, age=120), now=now)
    ent = _entity(run)
    first = ent.native_value
    # the minute tick: time passes, nothing else changes -> the same state (no new recorder row)
    monkeypatch.setattr(se.time, "time", lambda: now + 5 * 60)
    assert ent.native_value == first
    # a new reading -> a new sentence with its time
    run.c.last_reading_ts, run.c.current_glucose = now + 4 * 60, 150.0
    assert ent.native_value != first and ", measured at " in ent.native_value
    # the status changes to no current data -> the sentence names the reading's time
    run.c.no_current_data = True
    assert ent.native_value.startswith("No current data. Last reading at ")


def test_the_entity_names_the_day_after_midnight(tmp_path, monkeypatch):
    """The sentence is made again when the local date changes: after midnight the last reading is yesterday's."""
    from homeassistant.util import dt as dt_util

    now = int(time.time())
    run = build(tmp_path, readings=series(now, 1, age=120), now=now)
    ent = _entity(run)
    run.c.no_current_data = True
    local = dt_util.as_local(dt_util.utc_from_timestamp(now)).replace(hour=12, minute=0, second=0, microsecond=0)
    run.c.last_reading_ts = int(local.timestamp())  # noon, so that the test never runs across a midnight itself
    monkeypatch.setattr(se.time, "time", lambda: local.timestamp() + 3600)
    today = ent.native_value
    clock = run.c.regional.clock(local)
    assert today == f"No current data. Last reading at {clock}."
    next_day = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1, minutes=5)
    monkeypatch.setattr(se.time, "time", lambda: next_day.timestamp())
    assert ent.native_value == f"No current data. Last reading yesterday at {clock}."


def test_the_entity_is_documented():
    doc = Path(__file__).resolve().parents[1] / "docs" / "ENTITIES_AND_API.md"
    if not doc.exists():
        pytest.skip("docs/ENTITIES_AND_API.md is not in this copy of the repository")
    text = doc.read_text(encoding="utf-8")
    assert "sensor.glucocraft_spoken_summary" in text and "disabled by default" in text.lower()
