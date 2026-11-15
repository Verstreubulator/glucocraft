# License: AGPL-3.0-only (LICENSE).
"""Import glucose history from Home Assistant's recorder database.

When the readings file holds fewer than 100 readings at setup, the primary source's recorded states are read from the
recorder for the days the retention keeps and stored as readings, decided as live readings are (limits.classify, the
same measurement times and the same unit rule). With more readings stored, the readings recorded after the newest
stored one are read at setup (readings kept from an earlier installation, or a time the entry was not loaded) and
stored after it (recent_only). After the system clock was behind the stored readings, the readings refused meanwhile
are read the same way and stored after the newest stored reading (coordinator.async_catch_up). The recorder is read
one day at a time, so that a long history is never held in memory as Home Assistant states at once.
Uses Home Assistant's history API, so it works with every recorder database (SQLite, MariaDB, PostgreSQL).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, tzinfo
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.history import get_significant_states
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import DEFAULT_RETENTION_DAYS, DOMAIN, HISTORY_IMPORT_ISSUE_ID, SOURCE_TIME_ATTRIBUTES
from .limits import DEFAULT_LOWER, DEFAULT_UPPER, classify, lower_set_by_hand, upper_set_by_hand
from .storage import GlucoCraftStorage, retention_start
from .units import UNIT_MGDL, UNIT_MMOLL, epoch_seconds, looks_like_mmol, non_glucose_unit, unit_from_attribute

_LOGGER = logging.getLogger(__name__)

# The recorder is read in parts of one day (_rows_between): the states of a 1-minute source for 200 days would
# otherwise be held as Home Assistant states (1 to 2 KB each) all at once.
_FETCH_SECONDS = 86400
# The recent-only import (async_import_history) reads from this long before the newest stored reading: the states
# recorded before the first new one, the one of that reading among them, are then known (rows_from_states, before)
_RECENT_CONTEXT_SECONDS = 3600

Row = tuple[int, float, str | None]  # a reading from the recorder: measurement time, mg/dL, limit flag
# A parser of a time attribute's value into epoch seconds (coordinator._parse_reading_time): the value and the
# source's newest measurement time before it (which decides the repeated hour of a daylight-saving fall-back)
TimeParser = Callable[[Any, int | None], int | None]
# The recorded states as rows (rows_from_states), given the newest measurement time of the rows before them
RowsOf = Callable[[list[Any], int | None], list[Row]]


async def async_import_history(
    hass: HomeAssistant,
    entity_id: str,
    storage: GlucoCraftStorage,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    lock: threading.Lock | None = None,
    limits: dict | None = None,
    time_attribute: str = "",
    parse_time: TimeParser | None = None,
    after: int | None = None,
    recent_only: bool = False,
) -> int:
    """Import the recorded history of the glucose source `entity_id` into the storage; return the number of readings
    stored.

    The recorder is read for exactly the local days the retention (`retention_days`, whole local days) keeps
    (async_rows_kept). With `after` (the catch-up after the clock was behind), only the rows measured after that time
    are stored, each placed among the stored readings by its time (storage.fill_after); the whole retention is read all
    the same, because Home Assistant recorded the states of that time at the clock's wrong time. With `after` and
    `recent_only` (the setup's import of the readings after the newest stored one, coordinator.async_setup), the
    recorder is read only from shortly before `after` (_RECENT_CONTEXT_SECONDS; at the earliest from the retention's
    first day) to now: those states were recorded at the right time. A state without its own measurement time is then
    taken only when it cannot be a value written again as the entity came up (rows_from_states, `before`). The rows are
    stored and saved under the coordinator's `lock`. Each state is decided as a live one: `limits` are the source's
    resolved limits (coordinator._source_limits: lower, upper, lower_setting, upper_setting, dexcom_type, nightscout;
    limits.classify gives LO / HI at the limit, a reading at a lower limit set by hand LO and at an upper limit set by
    hand HI as well, error codes left out), `time_attribute` is the source's measurement-time attribute from the
    settings ("" = the known attribute names) and `parse_time` the coordinator's parser of an attribute value into
    epoch seconds (None reads only epoch numbers). The states are parsed in recording order, each with the newest
    measurement time before it, as the live readings are, so that both occurrences of the repeated hour of a
    daylight-saving fall-back keep their times. States whose time attribute holds no usable time take the time Home
    Assistant recorded them; their number is logged once.
    """
    now = int(time.time())

    # The source's unit from its current state, by the live path's rule (coordinator._source_reading): a unit other
    # than mg/dL or mmol/L is no glucose source, so nothing is imported; without a unit, values under 30 are left out
    entity_state = hass.states.get(entity_id)
    stated = entity_state.attributes.get("unit_of_measurement") if entity_state else None
    if non_glucose_unit(stated):
        _LOGGER.info("No history import: the glucose source states a unit that is not a glucose unit")
        return 0
    unit = unit_from_attribute(stated)
    has_unit = unit is not None
    source_unit = UNIT_MMOLL if unit == UNIT_MMOLL else UNIT_MGDL

    recent = recent_only and after is not None
    # the import at every setup with readings stored logs at debug level, except what it stored
    level = logging.DEBUG if recent else logging.INFO
    if after is None:
        _LOGGER.info("Starting history import (the last %d local days, unit=%s)", retention_days, source_unit)
    else:
        _LOGGER.log(level, "Starting history import of the readings after the newest stored one (unit=%s)", source_unit)

    async def fetch(start: int, end: int) -> list[Any]:
        """The source's recorded states from start to end (epoch seconds; the recorder's executor)."""

        def utc(ts):
            """An epoch time as a UTC datetime."""
            return datetime.fromtimestamp(ts, UTC)

        return await get_instance(hass).async_add_executor_job(_get_history, hass, entity_id, utc(start), utc(end))

    no_time = [0]  # the states whose time attribute held no usable time (rows_from_states)
    # the recent-only import: the usable state recorded before the part being parsed, carried from part to part
    before: list[Any] | None = [None, False] if recent else None

    def to_rows(states: list[Any], newest: int | None) -> list[Row]:
        """The recorded states as rows, decided as live readings (rows_from_states)."""
        return rows_from_states(
            states,
            source_unit == UNIT_MMOLL,
            has_unit,
            limits or {},
            time_attribute,
            parse_time,
            now,
            newest,
            no_time,
            before,
        )

    tz = dt_util.get_default_time_zone()
    try:
        if recent:
            start = max(int(after) - _RECENT_CONTEXT_SECONDS, retention_start(now, tz, retention_days))
            by_time = await _rows_between(fetch, to_rows, start, now) if start < now else {}
            rows = [by_time[t] for t in sorted(by_time)]
        else:
            rows = await async_rows_kept(fetch, to_rows, now, tz, retention_days)
    except Exception as err:  # noqa: BLE001 - any recorder or source failure only skips the import
        # the error's class only: its text can name the source's entity id
        _LOGGER.error("Failed to retrieve history: %s", err.__class__.__name__)
        _history_issue(hass, err.__class__.__name__)
        return 0
    _history_issue(hass, None)  # the recorder was read: a notice of an earlier failure closes
    if no_time[0]:
        _LOGGER.log(
            level,
            "History import: %d recorded states of the primary source had no usable measurement time; the time they "
            "were recorded was used",
            no_time[0],
        )

    if not rows:
        _LOGGER.log(level, "No history found for the glucose source")
        return 0

    # stored in time order (rows outside the stored span), detected and saved under the coordinator's lock; only the
    # rows actually accepted are counted
    def _store() -> int:
        """Store the rows (executor), under the lock when there is one."""
        if lock is None:
            return _store_rows(storage, rows, after)
        with lock:
            return _store_rows(storage, rows, after)

    imported = await hass.async_add_executor_job(_store)

    if imported > 0:
        _LOGGER.info(
            "Imported %d readings (interval=%ds)",
            imported,
            storage.detected_interval,
        )
    else:
        _LOGGER.log(level, "No valid glucose readings found in history")

    return imported


def _history_issue(hass: HomeAssistant, error: str | None) -> None:
    """Open the Repairs notice HISTORY_IMPORT_ISSUE_ID for a recorder that could not be read (error: the error's class,
    never its text, which can name the source's entity id), or close it (error None). Not persistent: it does not
    outlast a restart of Home Assistant."""
    if error is None:
        ir.async_delete_issue(hass, DOMAIN, HISTORY_IMPORT_ISSUE_ID)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        HISTORY_IMPORT_ISSUE_ID,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=HISTORY_IMPORT_ISSUE_ID,
        translation_placeholders={"error": error},
    )


async def _rows_between(
    fetch: Callable[[int, int], Awaitable[list[Any]]],
    to_rows: RowsOf,
    start: int,
    end: int,
) -> dict[int, Row]:
    """Return the rows of the states recorded from `start` to `end` by measurement time, read one day at a time.

    The parts follow each other in recording order, and a later part's row replaces an earlier one with the same
    measurement time, so the result is the same as reading the whole span at once (rows_from_states keeps the last
    state recorded for a measurement time). Each part is parsed with the newest measurement time of the parts before
    it, as one span would be. Each part after the first begins one second before the end of the one before it, so that
    a state recorded exactly at the boundary is never left out (read twice, it keeps its time).
    """
    by_time: dict[int, Row] = {}
    newest: int | None = None
    part_start = start
    while True:
        part_end = min(part_start + _FETCH_SECONDS, end)
        rows = to_rows(await fetch(part_start if part_start == start else part_start - 1, part_end), newest)
        for row in rows:
            by_time[row[0]] = row
        if rows:
            newest = max(rows[-1][0], newest or rows[-1][0])
        if part_end >= end:
            return by_time
        part_start = part_end


async def async_rows_kept(
    fetch: Callable[[int, int], Awaitable[list[Any]]],
    to_rows: RowsOf,
    now: int,
    tz: tzinfo,
    retention_days: int,
) -> list[Row]:
    """Return the rows of the recorded history that the retention keeps.

    The retention keeps whole local days from its first local midnight (storage.retention_start), counted back from
    the day of the newest reading, or of `now` when that is earlier (coordinator.retention_cutoff). The recorder is
    read from the first midnight counted from today; when the newest recorded reading is from an earlier day, the
    retention starts earlier as well, and those days are read too. So the oldest stored day is always complete, also
    when the days contain a daylight-saving change (a 23- or 25-hour day). fetch(start, end): the recorded states
    between two epoch times, read one day at a time (_rows_between); to_rows(states, newest): the states as rows
    (rows_from_states), parsed after the newest measurement time `newest` of the rows before them.
    """
    start = retention_start(now, tz, retention_days)
    by_time = await _rows_between(fetch, to_rows, start, now)
    if not by_time:
        return []
    first = retention_start(min(now, max(by_time)), tz, retention_days)
    if first < start:
        by_time = {**(await _rows_between(fetch, to_rows, first, start)), **by_time}
    return [by_time[t] for t in sorted(by_time) if t >= first]


def _epoch(value: Any, _after: int | None = None) -> int | None:
    """Epoch seconds from a number (seconds or milliseconds, units.epoch_seconds); None otherwise (a TimeParser)."""
    if isinstance(value, bool):
        return None
    try:
        return epoch_seconds(float(value))
    except (TypeError, ValueError):
        return None


# ---- the intake rules shared by the live readings (coordinator._reading_time, coordinator._source_reading) and the
# history import, so that a recorded state is decided exactly as the same state arriving live. What each path does
# when a state has no usable time stays its own: the live path takes the arrival time, the import the recording time.


def attribute_time(
    attrs: Mapping[str, Any], time_attribute: str, parse: TimeParser, after: int | None
) -> tuple[int | None, bool]:
    """Return (measurement time, no usable time) from a source state's attributes; the time is None when none holds
    a usable time.

    The measurement time: the time attribute set in the settings (`time_attribute`), else the first known one
    (SOURCE_TIME_ATTRIBUTES) that holds a time. No usable time: the attribute set in the settings is missing or
    unusable, or a known one holds a value that is no time. parse: the parser of an attribute value into epoch seconds;
    after: the source's newest measurement time before this state (parse decides the repeated hour of a daylight-saving
    fall-back with it)."""
    unusable = bool(time_attribute)
    for name in [time_attribute] if time_attribute else SOURCE_TIME_ATTRIBUTES:
        value = attrs.get(name)
        ts = parse(value, after)
        if ts is not None:
            return int(ts), False
        unusable = unusable or value is not None
    return None, unusable


def state_number(state: Any) -> float | None:
    """Return a source state as a number, None for a text that is no number (LO, an error text)."""
    try:
        return float(state)
    except (TypeError, ValueError):
        return None


def unitless_mmol(has_unit: bool, number: float | None) -> bool:
    """True for a value of a source without a unit that looks like mmol/L (units.looks_like_mmol): it is not used."""
    return not has_unit and number is not None and looks_like_mmol(number)


def _measured(state: Any, time_attribute: str, parse_time: TimeParser | None, after: int | None) -> tuple[int, bool]:
    """Return (measurement time, no usable time) of a recorded state, as for a live reading (attribute_time).

    The measurement time: the time attribute's (attribute_time), else the time Home Assistant updated the state
    (last_updated: a reading with an unchanged value has a new last_updated but not a new last_changed). after: the
    newest measurement time before this state."""
    ts, unusable = attribute_time(state.attributes, time_attribute, parse_time or _epoch, after)
    if ts is not None:
        return ts, False
    return int(state.last_updated.timestamp()), unusable


def _came_up_again(
    state: Any, previous: Any, went_away: bool, time_attribute: str, parse_time: TimeParser | None, after: int | None
) -> bool:
    """Return True for a recorded state without its own measurement time that may be a value written again as the
    entity came up, not a measurement (rows_from_states, `before`); a state with its own time is decided by it."""
    if attribute_time(state.attributes, time_attribute, parse_time or _epoch, after)[0] is not None:
        return False
    if previous is None:
        return True
    return state.state == previous.state and (went_away or dict(state.attributes) == dict(previous.attributes))


def rows_from_states(
    states: list[Any],
    is_mmol: bool,
    has_unit: bool,
    limits: dict,
    time_attribute: str = "",
    parse_time: TimeParser | None = None,
    now: int | None = None,
    after: int | None = None,
    no_time: list[int] | None = None,
    before: list[Any] | None = None,
) -> list[Row]:
    """The recorded states as (measurement time, mg/dL, limit) rows, decided as live readings (limits.classify: mmol/L
    converted, LO / HI at the limit, error codes and texts left out). One row per measurement time: when the recorder
    holds several states with the same measurement time (an attribute that changed without a new measurement), the
    last one recorded is kept. A measurement time later than `now` counts as now, as for a live reading.

    The states are parsed in recording order, each with the newest measurement time before it (`after` for the
    first), as the live path parses each reading with the source's last measurement time: a naive local time in the
    repeated hour of a daylight-saving fall-back then gets its second occurrence once the first has passed.
    no_time: a one-element counter of the states whose time attribute held no usable time (their recording time is
    used).
    before: for the recent-only import (async_import_history), [the usable state recorded before these states (None:
    not known), whether an unavailable or unknown state came after it], carried from one call to the next. A state
    without its own measurement time is then left out where the live path leaves it out as a value that came up
    again, not a measurement (coordinator._async_state_changed): when the state before it is not known, when it has
    the value of the state before it after an unavailable or unknown state, and when it has that state's value and
    attributes (Home Assistant records no row for a value written again unchanged, so such a row is one written as the
    entity came up at a start of Home Assistant)."""
    by_time: dict[int, Row] = {}
    for state in states:
        if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN, ""):
            if before is not None:
                before[1] = True
            continue
        if before is not None:
            previous, went_away = before
            before[0], before[1] = state, False
            if _came_up_again(state, previous, went_away, time_attribute, parse_time, after):
                continue
        if unitless_mmol(has_unit, state_number(state.state)):  # no unit: values under 30 look like mmol/L
            continue
        c = classify(
            state.state,
            UNIT_MMOLL if is_mmol else None,
            limits.get("lower", DEFAULT_LOWER),
            limits.get("upper", DEFAULT_UPPER),
            dexcom_type=bool(limits.get("dexcom_type")),
            nightscout=bool(limits.get("nightscout")),
            lower_set=lower_set_by_hand(limits),
            upper_set=upper_set_by_hand(limits),
        )
        if c.kind not in ("reading", "low", "high"):
            continue
        ts, unusable = _measured(state, time_attribute, parse_time, after)
        if unusable and no_time is not None:
            no_time[0] += 1
        if now is not None:
            ts = min(ts, now)
        after = ts if after is None else max(after, ts)
        by_time[ts] = (ts, c.value, c.limit)
    return [by_time[t] for t in sorted(by_time)]


def _store_rows(storage: GlucoCraftStorage, rows: list[Row], after: int | None = None) -> int:
    """Merge the rows into the storage (merge_history; with `after`, fill_after), detect the interval and save
    (executor, under the lock)."""
    accepted = storage.merge_history(rows) if after is None else storage.fill_after(rows, after)
    if accepted > 0:
        storage.detect_interval()
        storage.save()
    return accepted


def _get_history(
    hass: HomeAssistant,
    entity_id: str,
    start_time: datetime,
    end_time: datetime,
) -> list[Any]:
    """Every recorded state of the source with its attributes (runs in the recorder's executor).

    get_significant_states with significant_changes_only=False returns every recorded row, also the rows in which
    only the attributes changed: a new reading with the same value as the previous one is recorded that way (its
    measurement-time attribute changed), and state_changes_during_period would leave it out. A reading written with
    an unchanged value AND unchanged attributes is not recorded as a row by Home Assistant (only its last_reported
    time) and cannot be imported."""
    history_dict = get_significant_states(
        hass,
        start_time,
        end_time,
        [entity_id],
        include_start_time_state=False,
        significant_changes_only=False,
        minimal_response=False,
        no_attributes=False,
    )
    states = history_dict.get(entity_id, [])
    _LOGGER.debug("Retrieved %d history states", len(states))
    return states
