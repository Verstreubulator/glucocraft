# License: AGPL-3.0-only (LICENSE).
"""The sources' Repairs notices that the coordinator checks on its timer, and the issue ids of every source notice.

Every 5 minutes (and at setup) the coordinator checks, per glucose source (_check_source_notices):
- missing source: a configured source that does not exist in Home Assistant (once Home Assistant has started);
- fast source (the primary only): its new values have kept replacing its own last reading for 10 minutes, none added;
- slow source: it updates every 15 minutes or less often;
- sensor cap / sensor floor: its readings sit flat at a value inside its resolved limit (_scan_sensor_cap), so the
  "Sensor upper limit" or "Sensor lower limit" setting may need to be set;
- at upper limit / at lower limit: its readings sit flat at its automatic limit itself, sent as numbers;
and, once for the entry, the CGM sensor expiry entity's notice (_check_expiry_entity).

SourceNotices is a mixin of coordinator.GlucoCraftCoordinator: the checks read the coordinator's state (the sources, the
stored readings, each source's recent measurement times and resolved limits) and keep their own on it (the last
sensor-cap scan, the expiry entity's count of unrecognized values). They live in this module so that a notice can be
read and changed without the reading intake around it. The notices judged with each reading as it arrives (the unit
checks, the measurement-time attribute, a stuck source, numbers beyond a limit set by hand) and the clock, storage
and range notices stay in coordinator.py, next to the intake they judge; several of them open and close through
_source_issue here. Every notice of this module is a warning the user cannot fix from Repairs (the texts: strings.json
"issues"); the issue ids of all the source notices are defined here (SOURCE_ISSUE_PREFIXES).
"""

from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING, NamedTuple

from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SLOW_SOURCE_SECONDS, SOURCE_PRIMARY, SOURCE_SECONDARY, SOURCE_UNKNOWN
from .expiry import parse_expiry
from .gaps import median_interval
from .limits import lower_set_by_hand, mmol_limit, stored_limit_values, upper_set_by_hand
from .units import round_half_up

if TYPE_CHECKING:
    from .regional import Regional
    from .storage import GlucoseReading

# The notices log under the coordinator's logger, as the coordinator's own checks do: a user who sets the log level of
# custom_components.glucocraft.coordinator sees every notice check, wherever its code lives
_LOGGER = logging.getLogger(f"{__package__}.coordinator")

# The source notices, by prefix + entity id (removed when the source changes and with the integration). No prefix
# begins another one, so that each issue id has exactly one prefix (diagnostics.active_issue_ids).
OWN_SOURCE_ISSUE_PREFIX = "source_is_glucocraft_"  # a GlucoCraft entity chosen as a source
SLOW_SOURCE_ISSUE_PREFIX = "slow_source_"  # a source that updates every 15 minutes or less often
SENSOR_CAP_ISSUE_PREFIX = "sensor_cap_"  # readings flat at 400 or 450 mg/dL (the upper end)
SENSOR_FLOOR_ISSUE_PREFIX = "sensor_floor_"  # readings flat at 50 mg/dL (the lower end)
SOURCE_UNIT_ISSUE_PREFIX = "source_unit_"  # a source without a unit whose values look like mmol/L
NOT_GLUCOSE_UNIT_ISSUE_PREFIX = "unit_not_glucose_"  # a source that states a unit other than mg/dL or mmol/L
SOURCE_MISSING_ISSUE_PREFIX = "source_missing_"  # a configured source that does not exist in Home Assistant
MMOL_TOO_HIGH_ISSUE_PREFIX = "mmol_too_high_"  # a source stating mmol/L whose values look like another unit
MGDL_TOO_LOW_ISSUE_PREFIX = "mgdl_too_low_"  # a source stating mg/dL whose values look like mmol/L
TIME_ATTRIBUTE_ISSUE_PREFIX = "time_attribute_"  # the measurement-time attribute set for a source holds no time
FAST_SOURCE_ISSUE_PREFIX = "fast_source_"  # a source whose new values keep replacing the reading, none added
STUCK_SOURCE_ISSUE_PREFIX = "stuck_source_"  # a source without its own time that repeats one value for over an hour
HAND_LOW_ISSUE_PREFIX = "hand_limit_low_"  # numbers below a lower limit set by hand (they are shown as LO)
HAND_HIGH_ISSUE_PREFIX = "hand_limit_high_"  # numbers above an upper limit set by hand (they are shown as HI)
AT_UPPER_ISSUE_PREFIX = "at_upper_limit_"  # readings flat at the automatic upper limit, sent as a number
AT_LOWER_ISSUE_PREFIX = "at_lower_limit_"  # readings flat at the automatic lower limit, sent as a number
SOURCE_ISSUE_PREFIXES = (
    SLOW_SOURCE_ISSUE_PREFIX,
    SENSOR_CAP_ISSUE_PREFIX,
    SENSOR_FLOOR_ISSUE_PREFIX,
    SOURCE_UNIT_ISSUE_PREFIX,
    OWN_SOURCE_ISSUE_PREFIX,
    NOT_GLUCOSE_UNIT_ISSUE_PREFIX,
    SOURCE_MISSING_ISSUE_PREFIX,
    MMOL_TOO_HIGH_ISSUE_PREFIX,
    MGDL_TOO_LOW_ISSUE_PREFIX,
    TIME_ATTRIBUTE_ISSUE_PREFIX,
    FAST_SOURCE_ISSUE_PREFIX,
    STUCK_SOURCE_ISSUE_PREFIX,
    HAND_LOW_ISSUE_PREFIX,
    HAND_HIGH_ISSUE_PREFIX,
    AT_UPPER_ISSUE_PREFIX,
    AT_LOWER_ISSUE_PREFIX,
)
# The expiry entity's notice (_check_expiry_entity): one id; its text by what is wrong (expiry_entity_missing,
# expiry_entity_unrecognized, expiry_entity_past)
EXPIRY_ISSUE_ID = "expiry_entity"


def clock_stamp(rg: Regional, ts: int) -> str:
    """Return a stored time as the clock notice and its fix flow show it: the local date with the year and the time
    ("Wed 10/22/2026 7:46 AM"), in the date and time formats in use. The expiry entity's notice shows its expiry the
    same way."""
    return rg.stamp_year(dt_util.as_local(dt_util.utc_from_timestamp(ts)))


class ReplacingRun(NamedTuple):
    """The primary source's run of new values that each replace its own last reading, none added (kept by
    coordinator._process_reading; the fast-source notice): the measurement times of the run's first and newest
    value."""

    first: int
    last: int


class SourceNotices:
    """The timer's source notices of GlucoCraftCoordinator (a mixin: see the module docstring).

    Reads from the coordinator: hass, _glucose_entity, _secondary_entity, _expiry_entity, _watch_source (Home
    Assistant has started), _replacing_run, _src_times (the recent measurement times per source), storage,
    no_current_data, regional and _source_limits(entity id). Keeps on it: _cap_findings, _expiry_unrecognized and
    _notice_fault_logged (_init_source_notices).
    """

    # The sensor-cap notices (_scan_sensor_cap): readings flat at one of these values at the upper end, or at the
    # lower end, for CAP_MIN_RUN consecutive readings, at least CAP_MIN_RUNS times, and never a reading beyond it
    # (GlucoCraft rule). 500 and 40 are the automatic limits: readings flat at one of them, sent as numbers, have
    # their own notice (at_upper_limit / at_lower_limit, _check_source_notices).
    CAP_VALUES = (400.0, 450.0, 500.0)
    FLOOR_VALUES = (40.0, 50.0)
    CAP_MIN_RUN = 3
    CAP_MIN_RUNS = 3
    # The fast-source notice: the primary's new values keep replacing its reading for this long, none added
    # (GlucoCraft rule) ...
    REPLACING_NOTICE_SECONDS = 600
    REPLACING_ENDED_SECONDS = 300  # ... until none has replaced it for this long
    # The slow-source notice is judged only once a source has this many recent measurement times (5 intervals), so
    # that its median interval is not decided by the first few intervals after a start
    SLOW_SOURCE_MIN_TIMES = 6
    # The expiry entity's notice: an unrecognized value at this many checks in a row (every 5 minutes); a stated expiry
    # more than this long ago while readings still arrive (GlucoCraft rule)
    EXPIRY_UNRECOGNIZED_CHECKS = 10
    EXPIRY_PAST_SECONDS = 86400

    def _init_source_notices(self) -> None:
        """Set up the state these notices keep (from the coordinator's __init__)."""
        # the last sensor-cap scan per source role (upper and lower; _scan_sensor_cap, hourly with Change Over Time)
        self._cap_findings: dict[int, dict[str, float | None]] = {}
        # the expiry entity's checks in a row with a value that is no expiry (_check_expiry_entity)
        self._expiry_unrecognized = 0
        # a fault in the notice checks was logged as a warning; until a check succeeds again it is logged at debug
        # level (_check_source_notices_guarded)
        self._notice_fault_logged = False

    @callback
    def _source_issue(self, issue_id: str, show: bool, key: str, placeholders: dict[str, str]) -> None:
        """Create (show) or delete one of the sources' Repairs notices: a warning the user cannot fix from Repairs,
        with the translation key `key` and its placeholders."""
        if not show:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=key,
            translation_placeholders=placeholders,
        )

    @callback
    def _clear_former_source_issues(self) -> None:
        """Delete the source notices of entities that are no longer sources (a source changed in the settings)."""
        current = {e for e in (self._glucose_entity, self._secondary_entity) if e}
        # the keys first (domain, issue id): deleting an issue changes the registry while it is iterated
        for domain, issue_id in list(ir.async_get(self.hass).issues):
            if domain != DOMAIN:
                continue
            for prefix in SOURCE_ISSUE_PREFIXES:
                if issue_id.startswith(prefix) and issue_id[len(prefix) :] not in current:
                    ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _scan_sensor_cap(self, readings: list[GlucoseReading] | None = None) -> dict[int, dict[str, float | None]]:
        """Return, per source role, the values at which its readings sit flat at the ends of the measurement range:
        {"upper": 400, 450 or 500 mg/dL or None, "lower": 40 or 50 mg/dL or None}.

        A cap is a value at which the readings repeatedly sit flat and never go beyond: never above an upper cap
        (CAP_VALUES), never below a lower one (FLOOR_VALUES; a source that sends its lower limit instead of LO). The
        same run rules apply at both ends. A reading is at a cap when it is stored at one of the cap's stored values
        (stored_limit_values: the cap from a source in mg/dL, or its mmol/L value, 22.2, 25.0, 27.8, 2.2 or 2.8 mmol/L,
        from a source in mmol/L, stored as 400.0, 450.4, 500.8, 39.6 or 50.4); a reading stored as LO / HI is never at a
        cap and counts by its stored value: a LO stored at 40 lies below the cap of 50, a LO stored at a lower limit set
        by hand to 50 (a reading at that limit) does not, as a HI stored at an upper limit set by hand does not lie
        above it. The primary also counts the readings stored without a source (const.SOURCE_UNKNOWN). Runs in the
        executor, on `readings` (the statistics pass's copy; None: the stored readings). _check_source_notices shows the
        sensor-cap or sensor-floor notice only while the source's resolved limit at that end lies beyond the cap (the
        "Sensor upper limit" or "Sensor lower limit" setting may need to be set; the decision stays the user's); a cap
        at the source's limit itself, while that limit is automatic (such as 500 or 40), gives the at-upper-limit or
        at-lower-limit notice instead.
        """
        ends = {"upper": self.CAP_VALUES, "lower": self.FLOOR_VALUES}
        at_cap = {stored: cap for caps in ends.values() for cap in caps for stored in stored_limit_values(cap)}
        out: dict[int, dict[str, float | None]] = {}
        roles = {SOURCE_PRIMARY: self._glucose_entity}
        if self._secondary_entity:
            roles[SOURCE_SECONDARY] = self._secondary_entity
        for role in roles:
            accept = (SOURCE_PRIMARY, SOURCE_UNKNOWN) if role == SOURCE_PRIMARY else (SOURCE_SECONDARY,)
            top, bottom = 0.0, math.inf
            runs = dict.fromkeys(at_cap.values(), 0)
            cur, n = None, 0
            for r in self.storage.readings if readings is None else readings:
                if r.source not in accept:
                    continue
                top, bottom = max(top, r.raw), min(bottom, r.raw)
                v = at_cap.get(round_half_up(r.raw, 1)) if r.limit is None else None
                if v is not None and v == cur:
                    n += 1
                else:
                    if cur is not None and n >= self.CAP_MIN_RUN:
                        runs[cur] += 1
                    cur, n = v, 1 if v is not None else 0
            if cur is not None and n >= self.CAP_MIN_RUN:
                runs[cur] += 1
            out[role] = {
                "upper": next(
                    (
                        v
                        for v in sorted(self.CAP_VALUES, reverse=True)
                        if runs[v] >= self.CAP_MIN_RUNS and top <= max(stored_limit_values(v))
                    ),
                    None,
                ),
                "lower": next(
                    (
                        v
                        for v in sorted(self.FLOOR_VALUES)
                        if runs[v] >= self.CAP_MIN_RUNS and bottom >= min(stored_limit_values(v))
                    ),
                    None,
                ),
            }
        return out

    @callback
    def _check_source_notices_guarded(self) -> None:
        """_check_source_notices for setup and the timer (event loop). A fault in a notice check, for example a source
        or expiry entity whose state has a form no check expects, is logged and never breaks setup, nor skips the
        timer's save and entity refresh that follow: the readings matter more than a notice. Logged once per episode,
        as a warning with the traceback; while it lasts (the checks run every 5 minutes), at debug level, until a check
        succeeds again."""
        try:
            self._check_source_notices()
        except Exception as err:  # a notice must never break setup or the timer (logged once with its traceback)
            if self._notice_fault_logged:
                _LOGGER.debug("GlucoCraft: source notices not checked: %s", err)
            else:
                self._notice_fault_logged = True
                _LOGGER.warning("GlucoCraft: source notices not checked: %s", err, exc_info=True)
        else:
            self._notice_fault_logged = False

    @callback
    def _check_source_notices(self) -> None:
        """Create / remove the sources' Repairs issues (event loop; every 5 minutes): the expiry entity's notice
        (_check_expiry_entity), and per source the missing-source, fast-source (the primary only), slow-source,
        sensor-cap, sensor-floor, at-upper-limit and at-lower-limit notices.

        A source that does not exist in Home Assistant (its integration removed, or its entity id changed while
        GlucoCraft was not running; while it runs, it follows a new id: source_rename.py) is named only once Home
        Assistant has started: before that, the sources' integrations are still loading.
        """
        self._check_expiry_entity()
        sources = {SOURCE_PRIMARY: self._glucose_entity}
        if self._secondary_entity:
            sources[SOURCE_SECONDARY] = self._secondary_entity
        for role, entity in sources.items():
            if self._watch_source:
                self._source_issue(
                    f"{SOURCE_MISSING_ISSUE_PREFIX}{entity}",
                    self.hass.states.get(entity) is None,
                    "source_missing",
                    {"entity": entity},
                )
            if role == SOURCE_PRIMARY:
                run = self._replacing_run
                self._source_issue(
                    f"{FAST_SOURCE_ISSUE_PREFIX}{entity}",
                    run is not None
                    and run.last - run.first >= self.REPLACING_NOTICE_SECONDS
                    and int(time.time()) - run.last < self.REPLACING_ENDED_SECONDS,
                    "fast_source",
                    {"entity": entity},
                )
            times = self._src_times.get(entity, [])
            iv = median_interval(times)
            if iv is not None and len(times) >= self.SLOW_SOURCE_MIN_TIMES:
                self._source_issue(
                    f"{SLOW_SOURCE_ISSUE_PREFIX}{entity}",
                    iv >= SLOW_SOURCE_SECONDS,
                    "slow_source",
                    {"entity": entity, "minutes": str(int(round_half_up(iv / 60)))},
                )
            if role in self._cap_findings:
                found, limits = self._cap_findings[role], self._source_limits(entity)
                # not when the source's limit at that end already is the cap
                for end, prefix, key, beyond in (
                    ("upper", SENSOR_CAP_ISSUE_PREFIX, "sensor_cap", lambda cap, limit: cap < limit),
                    ("lower", SENSOR_FLOOR_ISSUE_PREFIX, "sensor_floor", lambda cap, limit: cap > limit),
                ):
                    cap = found.get(end)
                    show = bool(cap) and beyond(cap, limits[end])
                    self._source_issue(
                        f"{prefix}{entity}",
                        show,
                        key,
                        {"entity": entity, "value_mgdl": str(int(cap)), "value_mmol": f"{mmol_limit(cap):.1f}"}
                        if show
                        else {},
                    )
                # readings flat at the automatic limit itself, sent as numbers: such a source can be set by hand to
                # that limit, so that they are shown as LO / HI
                for end, prefix, key, by_hand in (
                    ("upper", AT_UPPER_ISSUE_PREFIX, "sensor_at_upper_limit", upper_set_by_hand(limits)),
                    ("lower", AT_LOWER_ISSUE_PREFIX, "sensor_at_lower_limit", lower_set_by_hand(limits)),
                ):
                    cap = found.get(end)
                    show = bool(cap) and cap == limits[end] and not by_hand
                    self._source_issue(
                        f"{prefix}{entity}",
                        show,
                        key,
                        {"entity": entity, "value_mgdl": str(int(cap)), "value_mmol": f"{mmol_limit(cap):.1f}"}
                        if show
                        else {},
                    )

    @callback
    def _check_expiry_entity(self) -> None:
        """Create, change or delete the Repairs notice about the CGM sensor expiry entity (event loop; with the source
        notices, every 5 minutes): it does not exist (once Home Assistant has started), its value has been no expiry at
        EXPIRY_UNRECOGNIZED_CHECKS checks in a row, or its expiry lies more than EXPIRY_PAST_SECONDS in the past while
        readings still arrive (a start time or a last-reading time picked by mistake, or an entity that stopped). A
        notice, not a refusal: the choice may be deliberate."""
        entity = self._expiry_entity
        key = None
        if entity:
            state = self.hass.states.get(entity)
            expiry, form = parse_expiry(state) if state is not None else (None, "missing")
            self._expiry_unrecognized = self._expiry_unrecognized + 1 if form == "unrecognized" else 0
            if state is None:
                key = "expiry_entity_missing" if self._watch_source else None
            elif self._expiry_unrecognized >= self.EXPIRY_UNRECOGNIZED_CHECKS:
                key = "expiry_entity_unrecognized"
            elif (
                expiry is not None
                and (dt_util.utcnow() - expiry).total_seconds() > self.EXPIRY_PAST_SECONDS
                and not self.no_current_data
            ):
                key = "expiry_entity_past"
        if key is None:
            ir.async_delete_issue(self.hass, DOMAIN, EXPIRY_ISSUE_ID)
            return
        placeholders = {"entity": entity}
        if key == "expiry_entity_past":
            placeholders["expiry"] = clock_stamp(self.regional, int(expiry.timestamp()))
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            EXPIRY_ISSUE_ID,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=key,
            translation_placeholders=placeholders,
        )
