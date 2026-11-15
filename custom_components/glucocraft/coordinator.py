# License: AGPL-3.0-only (LICENSE).
"""GlucoCraft's coordinator: the intake of the readings, the values derived from them and the statistics schedule.

Listens to the configured glucose source entities (a primary and an optional secondary), merges their readings by
measurement time into the readings file (storage.py), and keeps the derived values: the trend (from the slope of the
smoothed level), the 5-minute delta, the status and the period statistics. Readings are processed in the executor
under one lock; the entities, the HTTP API, the panel feed and the report read the results.
"""

from __future__ import annotations

import bisect
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, tzinfo
from enum import IntEnum
from typing import TYPE_CHECKING, Any, NamedTuple, TypedDict, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import (
    async_track_entity_registry_updated_event,
    async_track_state_change_event,
    async_track_state_report_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .change import ChangeOverTime, compute_change_sets
from .const import (
    AGP_RANGES,
    CONF_CARD_BACKGROUND,
    CONF_CUSTOM_RANGES_AND_GOALS,
    CONF_DATE_FORMAT,
    CONF_DISPLAY_CARD,
    CONF_FLASH_NO_CURRENT_DATA,
    CONF_GLUCOSE_ENTITY,
    CONF_GOAL_CV,
    CONF_GOAL_GMI,
    CONF_GOAL_TAR,
    CONF_GOAL_TAR_CRITICAL,
    CONF_GOAL_TBR,
    CONF_GOAL_TBR_CRITICAL,
    CONF_GOAL_TIME_IN_IDEAL_RANGE,
    CONF_GOAL_TIME_IN_TARGET_RANGE,
    CONF_NO_DATA_NOTICE,
    CONF_NO_DATA_NOTICE_DELAY,
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_TIME,
    CONF_NOTIFY_WEEKLY_DAY,
    CONF_NOTIFY_WEEKLY_ENABLED,
    CONF_ON_REMOVAL,
    CONF_PANEL_BACKGROUND,
    CONF_PANEL_WEBHOOK_ID,
    CONF_PHONE_TARGETS,
    CONF_PHONE_TYPES,
    CONF_PRIMARY_TIME_ATTR,
    CONF_PRINTER,
    CONF_RANGE_CRITICAL_HIGH,
    CONF_RANGE_CRITICAL_LOW,
    CONF_RANGE_HIGH,
    CONF_RANGE_LOW,
    CONF_RANGE_TIGHT_HIGH,
    CONF_RANGE_TIGHT_LOW,
    CONF_SECONDARY_ENTITY,
    CONF_SECONDARY_SENSOR_LOWER_LIMIT,
    CONF_SECONDARY_SENSOR_UPPER_LIMIT,
    CONF_SECONDARY_TIME_ATTR,
    CONF_SENSOR_EXPIRY_ENTITY,
    CONF_SENSOR_LOWER_LIMIT,
    CONF_SENSOR_UPPER_LIMIT,
    CONF_SEPARATE_STATISTICS,
    CONF_SHOW_DASHBOARD,
    CONF_STALE_THRESHOLD,
    CONF_TEXT_OUTLINE,
    CONF_TREND_DISPLAY,
    CONF_UNIT,
    CONF_WEEKLY_PAGES,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
    EXPIRING_SOON_MINUTES,
    GMI_MIN_PERIOD_SECONDS,
    GOAL_OPS,
    REGIONAL_SETTINGS,
    SOURCE_PRIMARY,
    SOURCE_SECONDARY,
    STAT_PERIODS,
    STATUS_COLORS,
    TREND_DIRECTION_SHARE,
    TREND_KEYS,
    TREND_LIBRE_BANDS,
    TREND_PAGES,
    WEEKLY_PAGES_PERSONAL,
    date_format_code,
    display_zone_word,
    public_agp_ranges,
    public_key,
    status_label,
    zone_colors,
    zone_label,
)
from .contrast import foreground
from .expiry import parse_expiry
from .gaps import gap_threshold, median_interval, window_weights
from .gaps import weights as gap_weights
from .history_import import async_import_history, attribute_time, state_number, unitless_mmol
from .i18n import t
from .limit_runs import current_limit_run, last_measured_before
from .limits import (
    DEXCOM_HIGH_CODE,
    DEXCOM_LOW_CODE,
    ERROR_CODE_MAX,
    LIMIT_HIGH,
    LIMIT_LOW,
    LO_MMOL,
    Classified,
    ResolvedLimits,
    above,
    below,
    classify,
    is_dexcom_type,
    lower_set_by_hand,
    resolve,
    threshold_span,
    upper_set_by_hand,
)
from .maintenance import MaintenanceIssues
from .regional import Regional, regional_from_settings
from .settings import GOAL_DEFAULTS, band_colors, known_entry_data, ranges, sensor_limit_conflicts, setting

# The ids of the source notices are defined in source_notices.py. The names this module does not use itself are
# imported all the same (noqa F401): diagnostics.py and the tests read every one as coordinator.<name>.
from .source_notices import (
    AT_LOWER_ISSUE_PREFIX,  # noqa: F401
    AT_UPPER_ISSUE_PREFIX,  # noqa: F401
    EXPIRY_ISSUE_ID,  # noqa: F401
    FAST_SOURCE_ISSUE_PREFIX,  # noqa: F401
    HAND_HIGH_ISSUE_PREFIX,
    HAND_LOW_ISSUE_PREFIX,
    MGDL_TOO_LOW_ISSUE_PREFIX,
    MMOL_TOO_HIGH_ISSUE_PREFIX,
    NOT_GLUCOSE_UNIT_ISSUE_PREFIX,
    OWN_SOURCE_ISSUE_PREFIX,
    SENSOR_CAP_ISSUE_PREFIX,  # noqa: F401
    SENSOR_FLOOR_ISSUE_PREFIX,  # noqa: F401
    SOURCE_ISSUE_PREFIXES,  # noqa: F401
    SOURCE_MISSING_ISSUE_PREFIX,  # noqa: F401
    SOURCE_UNIT_ISSUE_PREFIX,
    STUCK_SOURCE_ISSUE_PREFIX,
    TIME_ATTRIBUTE_ISSUE_PREFIX,
    ReplacingRun,
    SourceNotices,
    clock_stamp,
)
from .statistics import (
    PeriodStats,
    band_flags,
    compute_period_stats_sets,
    compute_weekly_trends_sets,
    gmi_shown,
    gri_shown,
    window_flags,
)
from .storage import (
    DEFAULT_INTERVAL_SECONDS,
    DETECT_MIN_READINGS,
    DETECT_READINGS,
    GlucoCraftStorage,
    GlucoseReading,
    ReadingsCopy,
    retention_start,
)
from .unit_registry import apply_display_unit, current_glucose_entity_id, shown_unit
from .units import (
    MGDL_TOO_LOW,
    MGDL_TOO_LOW_RUN,
    MMOL_TOO_HIGH,
    MMOL_TOO_HIGH_RUN,
    UNIT_MGDL,
    UNIT_MMOLL,
    UNITLESS_MMOL_BELOW,
    UNITLESS_MMOL_RUN,
    epoch_seconds,
    fmt,
    non_glucose_unit,
    round_half_up,
    to_mgdl,
    unit_from_attribute,
)

if TYPE_CHECKING:
    from .report_service import GlucoCraftReports

_LOGGER = logging.getLogger(__name__)
T = TypeVar("T")

# Repairs notices of the coordinator (the ids; the texts are in strings.json "issues")
CLOCK_ISSUE_ID = "clock_behind"  # stored readings later than the system clock (fixable)
CLOCK_AHEAD_ISSUE_ID = "clock_ahead"  # live readings measured long before the system clock (a warning)
STORAGE_ISSUE_ID = "storage_problem"  # the readings file was damaged or could not be read
STORAGE_SAVE_ISSUE_ID = "storage_save_failed"  # the readings file cannot be written (storage.save_error)
# A very-low or very-high threshold in the settings outside the sensor's limits, replaced by the limit (_apply_ranges)
RANGE_ISSUE_IDS = {
    CONF_RANGE_CRITICAL_LOW: "range_below_sensor_limit",
    CONF_RANGE_CRITICAL_HIGH: "range_above_sensor_limit",
}
# The suffix of the store the coordinator keeps per entry (.storage/glucocraft.<entry id>_<suffix>: the state saved
# across restarts, _state_data); the removal deletes it (__init__.py)
STORE_SUFFIXES = ("state",)
# A second reading within this share of the source's interval after the last one is the same measurement again (the
# live path's repeated state) or, from the second source, a measurement the primary already has (no gap there)
SAME_MEASUREMENT_SHARE = 0.75


class ImportCounts(TypedDict):
    """What a CSV import did with the readings of the file (GlucoCraftCoordinator.import_readings)."""

    imported: int  # stored
    already_stored: int  # a stored reading within 30 s: the stored one kept
    not_kept: int  # older than the retention's first day, or later than the clock


def _local_naive_ts(dt: datetime, tz: tzinfo, after: int | None = None, now: int | None = None) -> int:
    """Convert a naive local time to epoch seconds.

    In the repeated hour of a daylight-saving fall-back the first occurrence (fold 0) is tried first; when it is older
    than the source's last measurement (`after`), the second occurrence (fold 1) is used if it is not older than
    `after` and not in the future. Without this, every reading of the repeated hour would look older than the one
    before and be rejected. A time equal to `after` in either occurrence is the same measurement again and keeps that
    time (a state recorded twice, or read again at the boundary of two parts of the history import).
    """
    ts = int(dt.replace(tzinfo=tz, fold=0).timestamp())
    if after is not None and ts < after:
        ts2 = int(dt.replace(tzinfo=tz, fold=1).timestamp())
        if ts2 >= after and (now is None or ts2 <= now + 60):
            return ts2
    return ts


DATE_ONLY_LENGTH = 10  # "YYYY-MM-DD": an ISO text this long or shorter has no time of day


def _mean(values: list[float]) -> float:
    """The arithmetic mean of a non-empty list."""
    return sum(values) / len(values)


class SourceLimits(ResolvedLimits):
    """A source's resolved limits (limits.resolve) and how its special values are read (limits.classify)."""

    dexcom_type: bool  # a Dexcom-type source (its 39 / 401 codes)
    nightscout: bool  # delivered by the Nightscout integration


class ReadingResult(IntEnum):
    """What _process_reading did with a reading."""

    UNCHANGED = 0  # nothing changed: the same or an older measurement, a reading not used, the clock behind
    SOURCE_CHANGED = 1  # the same measurement again, now from the source in use: no new reading
    STORED = 2  # a new reading was stored


class StuckRun(NamedTuple):
    """A source's run of identical values without its own measurement time (_check_stuck_source); kept per source
    from its first reading on."""

    key: tuple[float, str | None] | None  # (value, limit flag) of the run; None after a reading with its own time
    count: int  # the readings in the run
    first: int  # the time of the run's first reading
    last: int  # the time of its newest reading
    notice_open: bool  # the stuck-source notice is open


def _parse_reading_time(value: Any, after: int | None = None) -> int | None:
    """Return a measurement time from a source attribute: ISO text, or epoch seconds / milliseconds; None if not a time.

    A text without a time of day (a date only) is no measurement time: Home Assistant's parser reads it as midnight,
    which would give every reading of the day the same time. An infinite or NaN number is no time either, and neither
    is a well-formed text of a time that does not exist ("2026-02-30T10:00:00", a 61st second, an offset beyond 24
    hours), for which Home Assistant's parser raises ValueError.
    after: the source's last measurement time (decides the repeated hour of a daylight-saving fall-back).
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            text = str(value).strip()
            try:
                num = float(text)
            except ValueError:
                dt = dt_util.parse_datetime(text) if len(text) > DATE_ONLY_LENGTH else None
                if dt is None:
                    return None
            else:
                return epoch_seconds(num)
        if dt.tzinfo is None:
            return _local_naive_ts(dt, dt_util.get_default_time_zone(), after, int(time.time()))
        return int(dt.timestamp())
    except (ValueError, OverflowError):
        return None


class GlucoCraftCoordinator(SourceNotices, DataUpdateCoordinator):
    """Coordinator that receives glucose data and computes derived values.

    The sources' Repairs notices checked on the timer are in source_notices.py (SourceNotices)."""

    # A reading time more than this later than the system clock means the clock is behind (clock_behind)
    CLOCK_MARGIN_SECONDS = 300
    # The clock may be ahead (clock_ahead): this many consecutive live readings with their own measurement time, each
    # measured more than CLOCK_AHEAD_SECONDS before the clock; the first one within that time closes the notice. The
    # two thresholds keep a source whose readings arrive a few minutes late from raising it (GlucoCraft rule).
    CLOCK_AHEAD_SECONDS = 3600
    CLOCK_AHEAD_READINGS = 3
    # The reasons the source in use stopped delivering, for the log (_update_source_outage)
    SOURCE_UNAVAILABLE, SOURCE_NO_CURRENT_DATA = "unavailable", "no_current_data"
    # The outage last logged, (reason, time of the last reading), kept in hass.data across reloads of the entry (every
    # settings save reloads it), so that one outage is logged once and not again after each reload
    OUTAGE_LOGGED = f"{DOMAIN}_outage_logged"
    # Adaptive smoothing (_adaptive): (20-minute range below, window s, slope cap mg/dL/min)
    ADAPTIVE = ((10, 1200, 3.0), (25, 900, 5.0), (float("inf"), 600, 8.0))
    NO_TIME_RUN = 10  # readings in a row without a usable time under the name set: the notice (GlucoCraft rule)
    # The live rule for a reading measured long ago: it is stored only when it lies less than this far before the
    # clock or before the newest stored reading (_too_old). Comparing with the newest stored reading as well means
    # that a clock far ahead never refuses the source's correctly timed readings (GlucoCraft rule).
    LIVE_LOOKBACK_SECONDS = 86400
    # The kinds of log lines of readings that are not used, or used without their own time (_note_refusal). Each is
    # logged once per source and kind, again only after that source's next good reading of the kind; the log names the
    # source's role, never its entity id.
    REFUSED_TIME, REFUSED_OLD, REFUSED_NONE = "time", "old", "none"
    # A source without its own measurement time that delivers exactly the same value this many times in a row, over at
    # least this long, may have stopped: a Repairs notice says so (_check_stuck_source; GlucoCraft rule). Readings are
    # not changed: a very steady glucose level can do the same.
    STUCK_READINGS = 12
    STUCK_SECONDS = 3600
    # A warm-up ends by itself this long after it began (GlucoCraft rule; the warm-ups of CGM systems are far shorter):
    # the normal no-current-data rule, words and notice then apply (_end_overlong_warmup)
    WARMUP_MAX_SECONDS = 24 * 3600
    # Fewer stored readings than this at setup: the store is new or nearly empty, and the primary's recorded history
    # is imported (history_import.py); with more, only the readings recorded after the newest stored one (async_setup)
    HISTORY_IMPORT_BELOW = 100
    # The newest measurement times kept per source (20 intervals): the medians of its own interval read them
    # (_source_interval all of them, reading_interval the newest 10)
    SOURCE_TIMES_KEPT = 21
    # The interval is detected again every this many stored readings: detection reads the newest DETECT_READINGS
    # readings (storage.detect_interval), so each detection sees readings the one before had not
    REDETECT_EVERY_READINGS = DETECT_READINGS

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        config: dict[str, Any],
        config_entry: Any = None,
    ) -> None:
        """Initialize the coordinator from the entry's settings.

        `config_entry` is the entry it belongs to: Home Assistant then shuts the coordinator down when the entry
        unloads (async_shutdown).
        """
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
        )

        self.entry_id = entry_id
        # the entry's data as stored (settings, update_settings); every setting is read from it through
        # settings.setting / settings.ranges, which replace a value of the wrong type or outside its span with the
        # setting's default
        self._settings = config
        self._glucose_entity = config[CONF_GLUCOSE_ENTITY]
        # Second source: the primary is always preferred; the secondary fills only gaps in the primary's readings,
        # and GlucoCraft returns to the primary at its next new reading (_process_reading)
        self._secondary_entity = setting(config, CONF_SECONDARY_ENTITY)
        if self._secondary_entity == self._glucose_entity:
            self._secondary_entity = None
        self._time_attr = {self._glucose_entity: setting(config, CONF_PRIMARY_TIME_ATTR)}
        if self._secondary_entity:
            self._time_attr[self._secondary_entity] = setting(config, CONF_SECONDARY_TIME_ATTR)
        self._src_last_ts: dict[str, int] = {}  # newest measurement time seen per source
        self._src_times: dict[str, list[int]] = {}  # recent measurement times per source (its own interval)
        self._src_last_value: dict[str, float] = {}
        # the sources whose reading older than their last measurement was logged (once, until their next new reading)
        self._older_logged: set[str] = set()
        self._refusals_logged: set[tuple[str, str]] = set()  # (entity id, kind) logged by _note_refusal
        self._clock_issue_open = False  # the clock notice is open (created once per episode: _check_clock)
        # The clock-ahead notice (_check_clock_ahead): the consecutive live readings measured long before the clock,
        # the last measurement time counted per source (a measurement sent again is not counted twice), and whether
        # the notice is open (created once per episode; None: not known, as after a reload of the entry, so that the
        # first reading within the hour deletes a notice left open)
        self._clock_ahead_count = 0
        self._clock_ahead_last: dict[str, int] = {}
        self._clock_ahead_open: bool | None = None
        # The newest stored reading when a reading was first refused because the clock was behind, or the newest one
        # kept after the readings later than the clock were discarded: the recorded readings after it are imported
        # once the clock is right (async_catch_up). None while there is nothing to catch up.
        self._catch_up_after: int | None = None
        self._catching_up = False
        self._save_issue_open = False  # the "readings file cannot be written" notice is open (_check_save_error)
        self.second_source_active: bool = False
        self.active_source_entity: str = ""
        self._lock = threading.Lock()  # one change or copy of the stored readings at a time (executor threads)
        # one statistics pass at a time (_stats_pass); the copies are numbered, so that a pass from an older copy than
        # the last one computed is skipped
        self._stats_lock = threading.Lock()
        self._stats_copies = 0
        self._stats_computed = 0
        self._ticks = 0
        self._shut_down = False  # set once by async_shutdown; a second call does nothing
        self._unsub_timer = None
        # the state saved across restarts (_state_data)
        self._state_store: Store = Store(hass, 1, f"glucocraft.{entry_id}_state")
        self._stale_minutes = setting(config, CONF_STALE_THRESHOLD)
        # the unit at setup (the source's); afterwards the Last glucose reading sensor's unit rules (display_unit)
        self._config_unit: str = setting(config, CONF_UNIT)

        # The optional features (settings.py): while Custom ranges and goals is off, the ranges, goals and weekly pages'
        # range set read below are the defaults (the consensus ranges and targets), and its entities and page do not
        # exist; Separate statistics entities adds one entity per range-independent statistic and period
        self.custom_ranges_and_goals: bool = setting(config, CONF_CUSTOM_RANGES_AND_GOALS)
        self.separate_statistics: bool = setting(config, CONF_SEPARATE_STATISTICS)

        # Goal targets
        self.goal_time_in_target_range = setting(config, CONF_GOAL_TIME_IN_TARGET_RANGE)
        self.goal_time_in_ideal_range = setting(config, CONF_GOAL_TIME_IN_IDEAL_RANGE)
        self.goal_tbr = setting(config, CONF_GOAL_TBR)
        self.goal_tbr_critical = setting(config, CONF_GOAL_TBR_CRITICAL)
        self.goal_tar = setting(config, CONF_GOAL_TAR)
        self.goal_tar_critical = setting(config, CONF_GOAL_TAR_CRITICAL)
        self.goal_cv = setting(config, CONF_GOAL_CV)
        self.goal_gmi = setting(config, CONF_GOAL_GMI)
        self._weekly_pages = setting(config, CONF_WEEKLY_PAGES)  # the weekly pages' standard

        # Storage (loaded asynchronously in async_setup)
        storage_dir = hass.config.path("glucocraft")
        self.storage = GlucoCraftStorage(storage_dir, entry_id)

        # Current values
        self.current_glucose: float | None = None
        # The slope, the 5-minute change and the trend are cleared (unknown) while there is no current data and during
        # a sensor warm-up, as the fresh glucose value is; the properties below return them only while trend_current
        # (after the sensor's stated expiry too, while readings still arrive: the trend follows the data)
        self.slope: float | None = None  # of the smoothed level, mg/dL per minute (adaptive smoothing, ADAPTIVE)
        self.change_5min: float | None = None  # change of the smoothed level over 5 min (mg/dL)
        self.trend_arrow: str = ""  # empty until a trend is known (never a "Flat" default)
        self.trend_description: str = "Unknown"
        # The one no-current-data fact: no reading, a reading older than the no-current-data limit, or a reading later
        # than the system clock (_no_current_data_at). Published as binary_sensor.glucocraft_no_current_data; every
        # display, the trend values, the summaries and the no-data notice test it, never the status.
        self.no_current_data: bool = True
        # The no-data notice (notification.NoDataNotice): the time of the last reading before the outage whose notice
        # was sent (0 = none), kept across restarts so that an outage gets one notice only
        self.no_data_notice_for: int = 0
        # The log of the source in use (_update_source_outage): why it last stopped delivering (SOURCE_UNAVAILABLE or
        # SOURCE_NO_CURRENT_DATA), None while it delivers. Watched only once Home Assistant has started, because the
        # sources' integrations are still loading before that.
        self._source_outage: str | None = None
        self._watch_source = False
        self.sensor_warming_up: bool = False
        self.warmup_started_at: int = 0  # epoch s when GlucoCraft saw the warm-up start (0 = not warming up)
        # Optional expiry entity: a new sensor (the expiry jumps later) starts a warm-up, for any CGM
        self._expiry_entity = setting(config, CONF_SENSOR_EXPIRY_ENTITY)
        self._last_expiry = None  # first value after a start = baseline only
        self.last_reading_ts: int = 0
        self.active_source: str = ""
        # LO / HI (limits.py): the newest reading's limit flag ("low" / "high" / None), the limits a source showed
        # itself ("Below 50" / "Above 400" texts, the Dexcom code 401; kept across restarts), each source's Nightscout
        # device and delivering integration (domain)
        self.current_limit: str | None = None
        self._learned_limits: dict[str, dict[str, int]] = {}
        # the Nightscout device each source showed when its limit was learned: another device forgets the limit
        self._learned_device: dict[str, str] = {}
        self._src_device: dict[str, str] = {}
        self._src_domain: dict[str, str | None] = {}
        # The glucose ranges in use (mg/dL): the settings, with the very-low and the very-high threshold kept within
        # the sensors' limits (_apply_ranges; again once the learned limits are loaded and whenever one is learned)
        self._apply_ranges(notices=False)
        # the sources with an open "GlucoCraft entity chosen as a source", "source without a unit" or "unit that is not
        # a glucose unit" notice
        self._own_source_flagged: set[str] = set()
        self._unit_issue_open: set[str] = set()
        self._not_glucose_open: set[str] = set()
        # the state of the timer's source notices (source_notices.py): the last sensor-cap scan, the expiry count
        self._init_source_notices()
        self._store_interval: int | None = None  # the newest stored reading's interval (gap rule), per stats pass
        self._purge_day = None  # the local date of the last whole-day purge

        # Detected interval
        self.detected_interval_seconds: int = self.storage.detected_interval

        # Period statistics (computed periodically)
        self.period_stats: dict[str, PeriodStats | None] = {}
        self.weekly_trends: list[PeriodStats | None] = []
        # the same weeks on the AGP (consensus) bands, as the trend pages show them; computed with weekly_trends
        self.weekly_trends_agp: list[PeriodStats | None] = []
        self.weekly_trends_at: int = 0  # the "now" the weeks were computed for (week w ends at - w x 7 d)
        # the weeks exactly as the trend pages get them (None = empty week) and each page's direction (one system)
        self.trend_weeks: list[PeriodStats | None] = []
        self.trend_directions: dict[str, str | None] = {}
        self.trend_gmi14: list[float | None] = []  # per sent week: the 14-day GMI ending at that week's end
        self.trend_gri14: list[float | None] = []  # per sent week: the 14-day GRI ending at that week's end
        self._unitless_low: dict[str, int] = {}  # per source without a unit: readings under 30 in a row
        # per source: readings in a row above the highest plausible mmol/L value (units.MMOL_TOO_HIGH); missing until
        # the first reading, which also closes a notice left open before a reload
        self._mmol_high_run: dict[str, int] = {}
        # per source: readings in a row below the lowest plausible mg/dL value (units.MGDL_TOO_LOW), the same way
        self._mgdl_low_run: dict[str, int] = {}
        # per source with a measurement-time attribute set: readings in a row without a usable time under that name
        self._no_time_run: dict[str, int] = {}
        # the primary source's run of new values replacing its own last reading (the fast-source notice)
        self._replacing_run: ReplacingRun | None = None
        # per source without its own measurement time: the run of identical values (_check_stuck_source); missing
        # until the source's first reading
        self._stuck: dict[str, StuckRun] = {}
        # the sources whose numbers beyond a limit set by hand were noticed in this run (_check_hand_limits)
        self._hand_limit_open: set[str] = set()
        # The current stretch of readings at the sensor's limit (for the displays' below / above sensor range screen,
        # which states since when and the last measured value; limit_runs.py), kept up to date with every stored
        # reading and at the start (from the store, so it survives a restart): the first reading of the unbroken
        # run of limit readings of the same kind the newest reading belongs to (None while the newest is measured), and
        # the newest measured reading before it (unrounded mg/dL and its time; None when the store has none)
        self.limit_since: int | None = None
        self.last_measured_mgdl: float | None = None
        self.last_measured_ts: int | None = None
        # the same weeks on the ranges in the settings (the custom standard of the weekly pages)
        self.personal_trend_weeks: list[PeriodStats | None] = []
        self.personal_trend_directions: dict[str, str | None] = {}
        # Change Over Time: {"consensus": ..., "personal": ...} from change.compute_change_sets, hourly
        self.change: dict[str, ChangeOverTime] = {}
        self.change_at: int = 0
        # the period statistics on the consensus (AGP) bands, whatever the ranges in use (dashboard Today page)
        self.period_stats_agp: dict[str, PeriodStats | None] = {}

        # State listener
        self._unsub_state: list[Any] = []

        # Set by __init__.py after construction: the CGM Report and the manifest version
        self.reports: GlucoCraftReports | None = None
        self.integration_version: str = ""
        # the Maintenance status (maintenance.py): a change writes the entities' states again, so that the
        # Configuration sensor's maintenance_issues follows at once
        self.maintenance = MaintenanceIssues(hass, self.async_update_listeners)

    @property
    def settings(self) -> Mapping[str, Any]:
        """The entry's data as stored (read every setting with settings.setting / settings.ranges)."""
        return self._settings

    def update_settings(self, data: Mapping[str, Any]) -> None:
        """Take keys the entry stored without a reload (a new wall-panel address), so that configuration() shows them at
        once."""
        self._settings.update(data)

    # ---- trend values, cleared while the data are not current. Stored in _slope etc.; the coordinator's own
    # calculations set them, everything else reads the cleared view.
    @property
    def trend_current(self) -> bool:
        """True while the trend values may be shown: there is current data and the sensor is not warming up.

        The status is not tested: after the sensor's stated expiry the trend follows the data, as the value does.
        """
        return not self.no_current_data and not self.sensor_warming_up

    @property
    def slope(self) -> float | None:
        """Return the slope of the smoothed level (mg/dL per minute) while the data are current, else None: the rate
        the trend arrow and the trend word show."""
        return self._slope if self.trend_current else None

    @slope.setter
    def slope(self, value: float | None) -> None:
        """Store the computed slope."""
        self._slope = value

    @property
    def change_5min(self) -> float | None:
        """Return the smoothed level's change over 5 minutes (mg/dL) while the data are current, else None."""
        return self._change_5min if self.trend_current else None

    @change_5min.setter
    def change_5min(self, value: float | None) -> None:
        """Store the computed 5-minute change."""
        self._change_5min = value

    @property
    def trend_arrow(self) -> str:
        """Return the trend arrow while the data are current, else an empty string."""
        return self._trend_arrow if self.trend_current else ""

    @trend_arrow.setter
    def trend_arrow(self, value: str) -> None:
        """Store the computed trend arrow."""
        self._trend_arrow = value

    @property
    def trend_description(self) -> str:
        """Return the trend word while the data are current, else "Unknown"."""
        return self._trend_description if self.trend_current else "Unknown"

    @trend_description.setter
    def trend_description(self, value: str) -> None:
        """Store the computed trend word."""
        self._trend_description = value

    @property
    def regional(self) -> Regional:
        """Return the regional settings in use (regional.py).

        Each setting is the user's choice, or "auto": derived from Home Assistant's country (the decimal separator
        also from the language; the A1C unit without a country from the display unit).
        """
        cfg = self.hass.config
        return regional_from_settings(
            self._regional_settings(), cfg.country, cfg.language, setting(self._settings, CONF_UNIT)
        )

    def _regional_settings(self) -> dict[str, Any]:
        """Return the six regional settings as chosen, each "auto" or a choice.

        The date format is stored in lower case ("mdy") and passed on as the code that regional.py and every display
        use ("MDY").
        """
        chosen = {k: setting(self._settings, k) for k in REGIONAL_SETTINGS}
        chosen[CONF_DATE_FORMAT] = date_format_code(chosen[CONF_DATE_FORMAT])
        return chosen

    @property
    def display_unit(self) -> str:
        """Return the unit everything is shown in: the Last glucose reading sensor's unit.

        Home Assistant converts that sensor's state from mg/dL; the entity settings or GlucoCraft's display unit
        setting choose the unit. Read from the sensor's registry entry (unit_registry.shown_unit), so every state
        written after a change of the unit, or after the reload of an options save, already carries the new unit; the
        stored setting while the entry holds none (a fresh installation, before setup writes it).
        """
        return shown_unit(self.hass, self.entry_id) or self._config_unit

    @callback
    def _async_unit_setting_changed(self, event) -> None:
        """Apply a unit chosen for Last glucose reading in the entity settings to every glucose sensor (one unit).

        Only an update that changed the entity's options can change the display unit: any other registry update (a
        rename, an icon, an area) leaves the setting alone, so the unit never changes without a settings change.
        """
        data = event.data or {}
        if data.get("action") == "update" and "options" not in (data.get("changes") or {}):
            return
        entity_id = current_glucose_entity_id(self.hass, self.entry_id)
        entry = er.async_get(self.hass).async_get(entity_id) if entity_id else None
        unit = unit_from_attribute((entry.options.get("sensor") or {}).get("unit_of_measurement")) if entry else None
        if unit:
            apply_display_unit(self.hass, self.entry_id, unit)
            if unit != self._config_unit:
                self._config_unit = unit
                ce = self.hass.config_entries.async_get_entry(self.entry_id)
                if ce:
                    new_data = known_entry_data({**ce.data, CONF_UNIT: unit})  # without keys GlucoCraft no longer knows
                    self.hass.config_entries.async_update_entry(ce, data=new_data)
        # the states (the 5-minute delta, the thresholds, the configuration) follow at once: display_unit reads the
        # registry entry, which already holds the new unit
        self.async_update_listeners()

    @callback
    def async_track_unit_setting(self) -> None:
        """Start copying a unit chosen for Last glucose reading to the other glucose sensors.

        Called after the platforms are set up (__init__.py): on a fresh installation the entity is not in the registry
        before that, so a listener registered earlier would have nothing to listen to until a restart.
        """
        cg = current_glucose_entity_id(self.hass, self.entry_id)
        if cg:
            self._unsub_state.append(
                async_track_entity_registry_updated_event(self.hass, cg, self._async_unit_setting_changed)
            )

    def _track_state_reported(self) -> None:
        """Listen for "state reported" events of the sources.

        A source without a measurement-time attribute that writes an unchanged value fires no state change, only
        "state reported"; without this listener, steady glucose would look like no current data.
        """
        sources = [e for e in (self._glucose_entity, self._secondary_entity) if e]
        self._unsub_state.append(async_track_state_report_event(self.hass, sources, self._async_state_reported))

    @callback
    def _async_state_reported(self, event: Event) -> None:
        """Handle a source that wrote the same state again.

        Without a measurement-time attribute that is a new reading with an unchanged value (at the report time); with
        one it is the same measurement again (nothing to do). Deduplicated by time: a source that rewrites its state
        faster than it measures gives at most one reading per 3/4 of its interval.

        Not before Home Assistant has started (async_at_started): entities that come up then write their value again (a
        template sensor at the start event, on a restored helper), which is an old value and no measurement. A real new
        value comes as a state change, and a steady one is reported again at its next measurement.
        """
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        if (
            not self._watch_source
            or entity_id not in (self._glucose_entity, self._secondary_entity)
            or new_state is None
            or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
        ):
            return
        ts, from_source = self._reading_time(entity_id, new_state)
        if from_source:
            return
        reported = new_state.last_reported
        if isinstance(reported, datetime):
            ts = min(ts, int(reported.timestamp()))
        same_after = max(20, SAME_MEASUREMENT_SHARE * self._source_interval(entity_id))
        if ts - self._src_last_ts.get(entity_id, 0) < same_after:
            return
        got = self._source_reading(entity_id, new_state)
        if got is None:
            return
        self._check_time_attribute(entity_id, False)
        value, limit = got
        self._check_stuck_source(entity_id, value, limit, ts, False)
        name = new_state.attributes.get("friendly_name", entity_id)
        self.hass.async_add_executor_job(self._process_and_notify, entity_id, name, value, ts, False, limit)

    async def async_setup(self) -> None:
        """Load the stored data, import history when needed and start the listeners and the timer."""
        # Load storage in executor (avoid blocking event loop)
        await self.hass.async_add_executor_job(self.storage.load)

        saved = await self._state_store.async_load() or {}
        self._restore_newest(saved)
        self.warmup_started_at = int(saved.get("warmup_started_at") or 0)
        self.sensor_warming_up = self.warmup_started_at > 0
        self._end_overlong_warmup(int(time.time()))  # a warm-up that began 24 hours ago or more has ended
        notice = saved.get("no_data_notice_for")
        self.no_data_notice_for = notice if isinstance(notice, int) and not isinstance(notice, bool) else 0
        stored_expiry = dt_util.parse_datetime(saved["last_expiry"]) if saved.get("last_expiry") else None
        learned = saved.get("learned_limits")
        if isinstance(learned, dict):
            self._learned_limits = {
                str(e): {k: int(v) for k, v in (d or {}).items() if k in ("lower", "upper")}
                for e, d in learned.items()
                if isinstance(d, dict)
            }
        devices = saved.get("learned_device")
        if isinstance(devices, dict):
            self._learned_device = {str(e): str(d) for e, d in devices.items() if d}
        self._apply_ranges()  # with the learned limits; also opens or closes the Repairs notices

        # Purge old readings, whole local days (GlucoCraft's readings file only, never Home Assistant's recorder); in
        # the executor and under the lock, as on the timer
        if self.storage.reading_count > 0:
            purged = await self.hass.async_add_executor_job(self.locked, self._purge_whole_days, int(time.time()))
            if purged > 0:
                await self.hass.async_add_executor_job(self.locked, self.storage.save)

        # Detect interval from historical data. With fewer readings the default interval set at construction stays:
        # the interval in the header of a file this short is not used
        if self.storage.reading_count >= DETECT_MIN_READINGS:
            self.detected_interval_seconds = self.storage.detect_interval()
            _LOGGER.debug("Detected CGM interval: %ds", self.detected_interval_seconds)

        # A damaged or unreadable readings file is reported in Repairs (and on the Data history sensor), not only in
        # the log; the clock is checked before anything is stored or purged
        self._check_storage_problem()
        self._check_clock()
        self._clear_former_source_issues()
        # the notices about numbers beyond a sensor limit set by hand start again with every start of the entry (a
        # settings change restarts it): the next such number opens them again (_check_hand_limits)
        for entity in [e for e in (self._glucose_entity, self._secondary_entity) if e]:
            for prefix in (HAND_LOW_ISSUE_PREFIX, HAND_HIGH_ISSUE_PREFIX):
                ir.async_delete_issue(self.hass, DOMAIN, f"{prefix}{entity}")

        # Import from Home Assistant's history: the whole retention when the store is empty or very small, else the
        # readings recorded after the newest stored one. This runs before the source listeners start (a live reading
        # arriving during the import would make every older history row "older than the newest" and rejected), in time
        # order, stored and saved under the lock. Not while the clock is behind the stored readings, never from one of
        # GlucoCraft's own entities, and not without a recorder (_has_recorder).
        importable = not self.clock_behind() and not self._is_own_entity(self._glucose_entity) and self._has_recorder()
        newest = self.storage.newest_timestamp
        if importable and self.storage.reading_count < self.HISTORY_IMPORT_BELOW:
            _LOGGER.info(
                "Storage has %d readings, attempting history import",
                self.storage.reading_count,
            )
            imported = await self._import_primary_history()
            if imported > 0:
                self.detected_interval_seconds = self.storage.detected_interval
                _LOGGER.info(
                    "History import complete: %d readings, interval=%ds",
                    imported,
                    self.detected_interval_seconds,
                )
                # the newest reading is now an imported one: its value, time and status at once, not only at the next
                # live reading (which can be one reading interval away)
                self._restore_newest(saved)
        elif importable and newest and newest < int(time.time()):
            # Readings kept from an earlier installation, or a time in which the entry was not loaded while Home
            # Assistant ran (disabled, a failed reload): the recorder's readings measured after the newest stored one
            # fill that time, placed by their measurement times (storage.fill_after), as async_catch_up does
            if await self._import_primary_history(newest, recent_only=True):
                await self.hass.async_add_executor_job(self.locked, self._after_catch_up)

        # Listen for glucose entity changes
        entities = [self._glucose_entity]
        if self._secondary_entity:
            entities.append(self._secondary_entity)
        if self._expiry_entity:
            entities.append(self._expiry_entity)
            # baseline = the expiry saved before the restart, so a sensor changed while Home Assistant was down is seen
            self._last_expiry = stored_expiry
            current_expiry, _ = parse_expiry(self.hass.states.get(self._expiry_entity))
            if current_expiry is not None:
                self._check_new_sensor(current_expiry)

        self._unsub_state.append(async_track_state_change_event(self.hass, entities, self._async_state_changed))
        self._track_state_reported()
        # (the unit listener on Last glucose reading starts after the platforms: async_track_unit_setting)

        # Compute initial statistics (and the sensor-cap scan), then the Repairs notices
        await self.hass.async_add_executor_job(self._stats_pass)
        self._check_source_notices_guarded()

        # The sources' current states: only a reading with its own measurement time is taken here (a reading made
        # while Home Assistant was down); without one it could be an old value restored at start, which must never
        # appear as a fresh reading
        for entity_id in [e for e in (self._glucose_entity, self._secondary_entity) if e]:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                continue
            got = self._source_reading(entity_id, state)
            if got is None:
                continue
            value, limit = got
            ts, from_source = self._reading_time(entity_id, state)
            if from_source:
                name = state.attributes.get("friendly_name", entity_id)
                await self.hass.async_add_executor_job(self._process_locked, entity_id, name, value, ts, True, limit)

        # One timer: the no-current-data flag and the save of what is not saved yet every minute, the statistics every
        # 5 minutes, the daily purge
        self._unsub_timer = async_track_time_interval(self.hass, self._async_tick, timedelta(minutes=1))
        self._unsub_state.append(async_at_started(self.hass, self._async_ha_started))
        self.async_set_updated_data(self.data)

    @callback
    def async_core_config_updated(self, _event: Event) -> None:
        """Publish the states again after a change of Home Assistant's core configuration (__init__.py): the regional
        settings on Automatic follow its country and language, not only with the next reading, which a silent source may
        not send."""
        self.async_update_listeners()

    @callback
    def follow_source_renames(self, renames: Mapping[str, str]) -> None:
        """Carry what is kept per source across restarts over to its new entity id, before the entry reloads with it
        (source_rename.py; the unload saves the state). What is kept only while running starts again with the reload, as
        at a restart, and the notices of the old id go with it (_clear_former_source_issues)."""
        for old, new in renames.items():
            for kept in (self._learned_limits, self._learned_device):
                if old in kept:
                    kept[new] = kept.pop(old)
            if self.active_source_entity == old:
                self.active_source_entity = new

    def _restore_newest(self, saved: dict[str, Any]) -> None:
        """Restore the newest stored reading with its own time, however old, and the source in use (`saved`: the state
        store's data).

        After a long Home Assistant downtime the card and the panels then show "no current data" with the reading's
        real time, never the restart time. Called after the store is loaded, and again after a history import.
        """
        if not self.storage.readings:
            return
        last = self._current_from_store(int(time.time()))
        # the source in use as saved, while it is still one of the sources; after a source was changed in the settings
        # the saved entity is no longer one, and the primary is the source in use
        kept = saved.get("active_source_entity") in [e for e in (self._glucose_entity, self._secondary_entity) if e]
        src = saved["active_source_entity"] if kept else self._glucose_entity
        self._src_last_ts[src] = last.timestamp
        self._src_last_value[src] = last.raw
        self.active_source_entity = src
        self.active_source = saved.get("active_source", "") if kept else ""
        self.second_source_active = src != self._glucose_entity
        _LOGGER.debug("Restored the newest stored reading (the %s source in use)", self._role(src))

    def _current_from_store(self, now: int) -> GlucoseReading | None:
        """Set the current values from the newest stored reading and return it (None, and the values cleared, when
        the store is empty): its value, limit flag and time, the no-current-data flag at `now`, and the slope and
        trend while the reading is current."""
        last = self.storage.readings[-1] if self.storage.readings else None
        self.current_glucose = last.raw if last else None
        self.current_limit = last.limit if last else None
        self.last_reading_ts = last.timestamp if last else 0
        self._update_limit_run()
        self._set_stale(self._no_current_data_at(now))
        if last and not self.no_current_data:
            self._apply_adaptive(last.timestamp)
        return last

    def locked(self, fn: Callable[..., T], *args: Any) -> T:
        """Run fn under the coordinator's lock (executor): the one way to read or change the stored readings from
        outside the coordinator."""
        with self._lock:
            return fn(*args)

    # ---- the system clock. A reading time later than the clock is not treated as a wrong reading: when the clock
    # itself is wrong (a host without a battery-backed clock, started without network time), deleting such readings
    # would delete correct readings permanently. So nothing is ever deleted because of the clock alone.

    def clock_behind(self, now: int | None = None) -> bool:
        """Return True while the system clock is more than CLOCK_MARGIN_SECONDS earlier than the newest stored reading.

        Meanwhile no reading is stored, nothing is purged and the history import does not run; a Repairs notice
        explains it (_check_clock).
        """
        newest = self.storage.newest_timestamp
        now = int(time.time()) if now is None else int(now)
        return bool(newest) and now < newest - self.CLOCK_MARGIN_SECONDS

    @callback
    def _check_clock(self, now: int | None = None) -> None:
        """Create or delete the Repairs notice about stored readings later than the system clock (event loop).

        The notice is created once per episode (its "now" changes every minute, and every rewrite would update the
        issue registry and save it); it is deleted as soon as the clock is no longer behind.
        """
        now = int(time.time()) if now is None else int(now)
        # a newest reading more than CLOCK_MARGIN_SECONDS ahead of the clock (time_untrusted) always raises it: its age
        # is not known, and the displays show none
        if not self.clock_behind(now) and not self.time_untrusted_at(now):
            ir.async_delete_issue(self.hass, DOMAIN, CLOCK_ISSUE_ID)
            self._clock_issue_open = False
            return
        if self._clock_issue_open:
            return
        self._clock_issue_open = True
        rg = self.regional
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            CLOCK_ISSUE_ID,
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="clock_behind",
            translation_placeholders={
                "newest": clock_stamp(rg, max(self.storage.newest_timestamp or 0, self.last_reading_ts)),
                "now": clock_stamp(rg, now),
            },
            data={"entry_id": self.entry_id},
        )

    @callback
    def _check_clock_ahead(self, entity_id: str, measured: int, now: int | None = None) -> None:
        """Create or delete the Repairs warning that the system clock may be ahead of the readings (event loop).

        Called for each live reading that carries its own measurement time. Such a reading is stored at that time, so
        while the clock is ahead the newest reading is old and the status is *no current data*; a reading without its
        own time is stored at the clock's time. The warning opens after CLOCK_AHEAD_READINGS consecutive readings
        measured more than CLOCK_AHEAD_SECONDS before the clock, once per episode, and closes with the first reading
        within that time. It cannot be fixed from Repairs: the clock is corrected on the host.
        """
        now = int(time.time()) if now is None else int(now)
        if self._clock_ahead_last.get(entity_id) == measured:
            return  # the same measurement again
        self._clock_ahead_last[entity_id] = measured
        if now - measured <= self.CLOCK_AHEAD_SECONDS:
            if self._clock_ahead_count or self._clock_ahead_open is not False:
                ir.async_delete_issue(self.hass, DOMAIN, CLOCK_AHEAD_ISSUE_ID)
            self._clock_ahead_count, self._clock_ahead_open = 0, False
            return
        self._clock_ahead_count += 1
        if self._clock_ahead_count < self.CLOCK_AHEAD_READINGS or self._clock_ahead_open:
            return
        self._clock_ahead_open = True
        rg = self.regional
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            CLOCK_AHEAD_ISSUE_ID,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=CLOCK_AHEAD_ISSUE_ID,
            translation_placeholders={"newest": clock_stamp(rg, measured), "now": clock_stamp(rg, now)},
        )

    def discard_readings_after_clock(self) -> int:
        """Remove the stored readings later than the system clock and save; return the number removed.

        Runs in the executor, under the lock, and only on the user's confirmation in the Repairs notice. In the same
        step, each source's measurement times later than the clock go with the discarded readings (a last measurement
        time becomes the newest reading kept), so that the next live reading is not rejected as older than the last
        one; the current values are then those of the newest reading kept.
        """
        with self._lock:
            now = int(time.time())
            removed = self.storage.drop_after(now)
            if removed:
                self.storage.save()
            newest = self.storage.newest_timestamp or 0
            self._src_last_ts = {e: (ts if ts <= now else newest) for e, ts in self._src_last_ts.items()}
            self._src_times = {e: [when for when in times if when <= now] for e, times in self._src_times.items()}
            self._older_logged.clear()
            self._current_from_store(now)
            # the readings refused while the clock was behind the discarded ones are imported next (async_catch_up)
            self._catch_up_after = newest
        _LOGGER.warning("GlucoCraft: %d stored readings later than the system clock were discarded", removed)
        return removed

    async def async_catch_up(self) -> int:
        """Import the primary source's recorded readings that were refused while the clock was behind; return the
        number stored.

        Run once the clock is no longer behind the stored readings (_async_tick) and after the readings later than the
        clock were discarded (repairs.ClockRepairFlow). Only the recorder's readings measured after the newest stored
        reading of that moment (_catch_up_after) are stored, each placed among the stored readings by its measurement
        time (storage.fill_after): a reading already stored, also one stored live since, is kept as it is. A reading
        without its own measurement time that arrived while the clock was behind has the clock's wrong time and is not
        imported.
        """
        after = self._catch_up_after
        if after is None or self._catching_up or self._shut_down or self.clock_behind():
            return 0
        self._catching_up = True
        self._catch_up_after = None
        try:
            if self._is_own_entity(self._glucose_entity) or not self._has_recorder():
                return 0
            stored = await self._import_primary_history(after)
            if stored:
                await self.hass.async_add_executor_job(self.locked, self._after_catch_up)
                self.async_set_updated_data(self.data)
            return stored
        finally:
            self._catching_up = False

    def _has_recorder(self) -> bool:
        """Return True while Home Assistant's recorder is loaded. The recorder is optional (manifest.json lists it
        under after_dependencies only); without it there is no history to import, and the import is skipped quietly
        instead of failing with an error and a Repairs notice."""
        return "recorder" in self.hass.config.components

    async def _import_primary_history(self, after: int | None = None, recent_only: bool = False) -> int:
        """Import the primary source's recorded history (history_import.async_import_history), decided as its live
        readings are: the days the retention keeps at setup, or with `after` only the readings measured after that
        time (async_catch_up, which reads the whole retention; with `recent_only`, the setup's import into a store
        with readings, which reads the recorder from that time on). Return the number stored.

        The limits are resolved with the device the source's current state shows (_take_source_device), as for a live
        reading: the setup's import runs before the first live reading, and without the device a Nightscout source fed
        by a Dexcom uploader would have its HIGH code read as a number. The unit is taken from the same current state
        (history_import.async_import_history)."""
        state = self.hass.states.get(self._glucose_entity)
        device = (state.attributes or {}).get("device") if state is not None else None
        self._take_source_device(self._glucose_entity, device)
        if self._glucose_entity not in self._src_device and self._domain(self._glucose_entity) == "nightscout":
            _LOGGER.debug("History import: the primary source shows no device yet; its limits are those without one")
        return await async_import_history(
            self.hass,
            self._glucose_entity,
            self.storage,
            retention_days=DEFAULT_RETENTION_DAYS,
            lock=self._lock,
            limits=self._source_limits(self._glucose_entity),
            time_attribute=self._time_attr.get(self._glucose_entity, ""),
            parse_time=_parse_reading_time,
            after=after,
            recent_only=recent_only,
        )

    def _after_catch_up(self) -> None:
        """Take the readings of a catch-up import into the current values (executor, under the lock): the newest
        reading may now be an imported one, and the primary's last measurement time follows it."""
        newest = self._current_from_store(int(time.time()))
        if newest is not None and newest.timestamp > self._src_last_ts.get(self._glucose_entity, 0):
            self._src_last_ts[self._glucose_entity] = newest.timestamp
        self.detected_interval_seconds = self.storage.detect_interval()

    def import_readings(self, readings: list[GlucoseReading], now: int | None = None) -> ImportCounts:
        """Store the readings of a CSV import (the action glucocraft.import_csv; executor, under the lock: locked).

        Readings already stored win: a reading within 30 s of a stored one is not taken (storage.add_readings). Only
        the readings GlucoCraft keeps are taken: from the retention's first day (retention_cutoff) to the clock, so
        that an import never sets the clock rule (clock_behind) off. Afterwards the readings are saved, and the current
        values, the reading interval and every statistic, Change Over Time included, are computed again.
        """
        now = int(time.time()) if now is None else int(now)
        cutoff, latest = self.retention_cutoff(now), now + self.CLOCK_MARGIN_SECONDS
        kept = [r for r in readings if cutoff <= r.timestamp <= latest]
        added = self.storage.add_readings(kept)
        if added:
            self.storage.save()
            self._after_catch_up()
            # the caller holds the reading lock; no pass waits for it while holding the statistics lock (_stats_pass)
            with self._stats_lock:
                self.change = {}  # Change Over Time too, not only at its hourly pass
                self._compute_all_stats(now)
        return ImportCounts(imported=added, already_stored=len(kept) - added, not_kept=len(readings) - len(kept))

    def _state_data(self) -> dict[str, Any]:
        """Return the state saved across restarts: the warm-up start, the expiry baseline, the source in use and its
        name, the learned limits and the device they belong to, and the outage whose no-data notice was sent."""
        return {
            "warmup_started_at": self.warmup_started_at,
            "last_expiry": self._last_expiry.isoformat() if self._last_expiry else None,
            "active_source": self.active_source,
            "active_source_entity": self.active_source_entity,
            "learned_limits": self._learned_limits,
            "learned_device": self._learned_device,
            "no_data_notice_for": self.no_data_notice_for,
        }

    def schedule_state_save(self) -> None:
        """Save the state soon (the no-data notice records its outage through this)."""
        self._schedule_state_save()

    def _schedule_state_save(self) -> None:
        """Save warm-up / expiry / source state soon (safe from any thread)."""
        self.hass.loop.call_soon_threadsafe(self._state_store.async_delay_save, self._state_data, 2)

    def _check_new_sensor(self, expiry) -> None:
        """Start a warm-up when a new sensor is seen (the expiry moved more than a day later); save the expiry."""
        # (GlucoCraft rule: 1 day, well above the clock jitter of an expiry entity.)
        if (
            self._last_expiry is not None
            and expiry - self._last_expiry > timedelta(days=1)
            and not self.sensor_warming_up
        ):
            self.sensor_warming_up = True
            self.warmup_started_at = int(time.time())
            _LOGGER.info(
                "New CGM sensor (expiry %s -> %s): warm-up until the next reading",
                self._last_expiry.isoformat(),
                expiry.isoformat(),
            )
        if expiry != self._last_expiry:
            self._last_expiry = expiry
            self._schedule_state_save()

    def _reading_time(self, entity_id: str, state) -> tuple[int, bool]:
        """Return (time, from_source): the source's own measurement time, else Home Assistant's arrival time.

        A time attribute that holds no usable time (the attribute set in the settings missing or unusable, or a known
        one with a value that is no time) is logged once (_note_refusal) until the source's next usable time."""
        now = int(time.time())
        name = self._time_attr.get(entity_id, "")
        after = self._src_last_ts.get(entity_id) or None
        ts, unusable = attribute_time(state.attributes, name, _parse_reading_time, after)
        if ts is not None:
            self._clear_refusal(entity_id, self.REFUSED_TIME)
            return min(ts, now), True  # a source clock ahead of Home Assistant's counts as now
        if unusable:
            self._note_refusal(entity_id, self.REFUSED_TIME)
        return now, False

    @callback
    def _check_time_attribute(self, entity_id: str, from_source: bool) -> None:
        """Create or delete the Repairs notice for a measurement-time attribute set in the settings that holds no usable
        time (event loop; once per live reading): after NO_TIME_RUN readings in a row without one, until the next
        reading with one. The readings keep the time they arrive (_reading_time). A source without a name set is not
        concerned: GlucoCraft then looks for the known names, and a source may simply have none; clearing the name
        closes the notice."""
        name = self._time_attr.get(entity_id, "")
        issue_id = f"{TIME_ATTRIBUTE_ISSUE_PREFIX}{entity_id}"
        if from_source or not name:
            if self._no_time_run.get(entity_id) != 0:  # the first reading after a reload also closes an earlier notice
                self._no_time_run[entity_id] = 0
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        n = self._no_time_run.get(entity_id, 0) + 1
        self._no_time_run[entity_id] = n
        if n == self.NO_TIME_RUN:
            _LOGGER.warning(
                "GlucoCraft: the measurement-time attribute set for the %s source holds no usable time",
                self._role(entity_id),
            )
            self._source_issue(issue_id, True, "source_time_attribute", {"entity": entity_id, "attribute": name})

    def has_measurement_time(self, entity_id: str | None) -> bool | None:
        """Return whether a source's current state carries its own measurement time (_reading_time); None when the
        source has no state to tell. A second source without one is used only after the primary has been silent for
        failover_seconds (the CGM Report states it)."""
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return self._reading_time(entity_id, state)[1]

    async def _async_tick(self, _now=None) -> None:
        """Run the coordinator's timer step.

        Every minute: the no-current-data flag, and a save of what is not saved yet (a whole-day purge, a save that
        failed; a new reading is saved when it is stored, _process_reading); every 5 minutes (or when that flag
        changes): the statistics and the source notices; after local midnight: the whole-day purge.
        """
        self._ticks += 1
        now = int(time.time())
        self._end_overlong_warmup(now)
        stale = self._no_current_data_at(now)
        flipped = stale != self.no_current_data
        self._set_stale(stale)
        self._check_clock(now)
        if self._catch_up_after is not None and not self.clock_behind(now):
            await self.async_catch_up()  # the clock is right again: the readings refused meanwhile
        if self.local_date(now) != self._purge_day:
            await self.hass.async_add_executor_job(self.locked, self._purge_whole_days, now)
        if flipped or self._ticks % 5 == 0:
            await self.hass.async_add_executor_job(self._stats_pass)
            self._check_source_notices_guarded()
        if self.storage.dirty:
            await self.hass.async_add_executor_job(self.locked, self.storage.save)
        self._check_save_error()
        self.async_set_updated_data(self.data)

    # ---- retention: 200 days, dropped as whole local days at midnight
    @staticmethod
    def local_date(now: float) -> date:
        """Return the local date (Home Assistant's time zone) of an epoch time: the one helper of the coordinator and
        the sensors."""
        return datetime.fromtimestamp(now, dt_util.get_default_time_zone()).date()

    def retention_cutoff(self, now: int) -> int:
        """Return the first moment kept: local midnight `retention` days before today's.

        Whole days; a daylight-saving day keeps its real length. "Today" is the earlier of the clock and the newest
        stored reading, so a clock that jumps ahead cannot remove more than the retention allows relative to the
        stored data.
        """
        newest = self.storage.newest_timestamp
        ref = min(now, newest) if newest else now
        return retention_start(ref, dt_util.get_default_time_zone(), DEFAULT_RETENTION_DAYS)

    def _purge_whole_days(self, now: int) -> int:
        """Drop the readings before retention_cutoff (storage only, never the recorder); return the number dropped.

        Nothing is purged while the clock is behind the stored readings (clock_behind).
        """
        self._purge_day = self.local_date(now)
        if self.clock_behind(now):
            return 0
        purged = self.storage.purge_before(self.retention_cutoff(now))
        if purged:
            _LOGGER.info(
                "Purged %d readings older than %d whole days from GlucoCraft storage", purged, DEFAULT_RETENTION_DAYS
            )
        return purged

    async def async_save_all(self) -> None:
        """Save the readings (executor) and the state (when Home Assistant stops or the entry unloads)."""
        await self.hass.async_add_executor_job(self.locked, self.storage.save)
        await self._state_store.async_save(self._state_data())

    def time_untrusted_at(self, now: int) -> bool:
        """Return True while the newest reading's own time is more than CLOCK_MARGIN_SECONDS ahead of the clock at
        `now`: its age is not known (the displays then show "Reading: ---" and no time line), and the clock notice is
        raised (_check_clock)."""
        return bool(self.last_reading_ts) and self.last_reading_ts > now + self.CLOCK_MARGIN_SECONDS

    @property
    def time_untrusted(self) -> bool:
        """Return True while the newest reading's own time is more than 5 minutes ahead of the clock
        (time_untrusted_at), the Last reading time sensor's attribute time_untrusted."""
        return self.time_untrusted_at(int(time.time()))

    def _no_current_data_at(self, now: int) -> bool:
        """Return True for no current data at `now`.

        That is: no reading, a reading older than the no-current-data limit, or a reading later than the system clock
        (the clock is behind, so the reading's age is not known).
        """
        last = self.last_reading_ts
        return not last or now - last > self._stale_minutes * 60 or last > now + self.CLOCK_MARGIN_SECONDS

    def _set_stale(self, stale: bool) -> None:
        """Set the no-current-data flag; the log follows it (_update_source_outage)."""
        self.no_current_data = stale
        self._update_source_outage()

    # ---- the log of the source in use, as Home Assistant's log-when-unavailable rule asks: one warning when the
    # source stops delivering, one info line when it delivers again, nothing repeated while either lasts. A change of
    # the reason during one outage (the source returns but sends no reading yet) is not logged again, nor is an outage
    # after a reload of the entry that was logged before it (OUTAGE_LOGGED). The log names
    # the source's role, never its entity id, which often contains a person's name (logs are shared in issue reports).

    @callback
    def _async_ha_started(self, _hass: HomeAssistant | None = None) -> None:
        """Start watching the source in use once Home Assistant has started (async_at_started)."""
        self._watch_source = True
        self._update_source_outage()

    def _source_unavailable(self) -> bool:
        """Return True while the source in use has no state, or the state unavailable or unknown."""
        state = self.hass.states.get(self.active_source_entity or self._glucose_entity)
        return state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)

    def _update_source_outage(self) -> None:
        """Log a change between delivering and not delivering of the source in use, once per change."""
        if not self._watch_source:
            return
        if self._source_unavailable():
            reason = self.SOURCE_UNAVAILABLE
        elif self.no_current_data:
            reason = self.SOURCE_NO_CURRENT_DATA
        else:
            reason = None
        was, self._source_outage = self._source_outage, reason
        if (was is None) == (reason is None):
            return
        role = "second" if self.second_source_active else "primary"
        if reason is None:
            self.hass.data.pop(self.OUTAGE_LOGGED, None)
        elif self.hass.data.get(self.OUTAGE_LOGGED) == (reason, self.last_reading_ts):
            return  # this outage was logged before the entry was reloaded
        else:
            self.hass.data[self.OUTAGE_LOGGED] = (reason, self.last_reading_ts)
        if reason == self.SOURCE_UNAVAILABLE:
            _LOGGER.warning("GlucoCraft: the %s source, the source in use, is unavailable (no current data)", role)
        elif reason == self.SOURCE_NO_CURRENT_DATA:
            _LOGGER.warning(
                "GlucoCraft: no new reading from the %s source for more than %d minutes (no current data)",
                role,
                self._stale_minutes,
            )
        else:
            _LOGGER.info("GlucoCraft: the %s source delivers current readings again", role)

    @property
    def fresh_glucose(self) -> float | None:
        """Return the current value only while the data are current (the below / above / in-range sensors)."""
        return None if self.no_current_data else self.current_glucose

    @callback
    def _async_state_changed(self, event: Event) -> None:
        """Handle a state change of a source or of the sensor-expiry entity."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")

        if new_state is None or new_state.state in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            # logged at once, not only at the next minute; not for a state removed: the entity may only be getting a new
            # entity id (source_rename.py), and the next minute's check logs a source that is really gone
            if new_state is not None and entity_id == (self.active_source_entity or self._glucose_entity):
                self._update_source_outage()
            return

        if entity_id == self._expiry_entity:
            expiry, _ = parse_expiry(new_state)
            if expiry is None:
                return
            was = self.sensor_warming_up
            self._check_new_sensor(expiry)
            if self.sensor_warming_up != was:
                self._schedule_state_save()
                self.async_set_updated_data(self.data)
            return

        if entity_id in (self._glucose_entity, self._secondary_entity):
            got = self._source_reading(entity_id, new_state)
            if got is None:
                return
            value, limit = got
            ts, from_source = self._reading_time(entity_id, new_state)
            self._check_time_attribute(entity_id, from_source)
            if from_source:
                self._check_clock_ahead(entity_id, ts)  # its own measurement time, compared with the clock
            else:
                # Only Home Assistant's arrival time: a value that comes up (no state before, or unavailable / unknown)
                # is a restore, not a new measurement, when it comes back unchanged after a short outage, and whatever
                # it is before Home Assistant has started (async_at_started): an entity that comes up then (a retained
                # MQTT message, a trigger-based template sensor, a helper) writes its old value, as in "state reported"
                # and the setup loop (async_setup). A changed value is taken at its next state change, a steady one at
                # its next "state reported".
                old = event.data.get("old_state")
                if (old is None or old.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)) and (
                    not self._watch_source or self._src_last_value.get(entity_id) == value
                ):
                    return
            self._check_stuck_source(entity_id, value, limit, ts, from_source)
            name = new_state.attributes.get("friendly_name", entity_id)
            # Process in executor to avoid blocking
            self.hass.async_add_executor_job(self._process_and_notify, entity_id, name, value, ts, from_source, limit)

    # ---- the source's limits (limits.py)
    def _domain(self, entity_id: str | None) -> str | None:
        """Return the integration that delivers a source (entity registry platform, e.g. "dexcom"); cached."""
        if not entity_id:
            return None
        cache = self._src_domain
        if entity_id not in cache:
            # None for an entity without a registry entry (one without a unique id)
            entry = er.async_get(self.hass).async_get(entity_id)
            cache[entity_id] = entry.platform if entry is not None else None
        return cache[entity_id]

    def _is_own_entity(self, entity_id: str | None) -> bool:
        """Return True for an entity of GlucoCraft itself (entity registry platform "glucocraft").

        Such an entity is never a source: its value is GlucoCraft's own last reading, which would be stored again as a
        new reading every time it is rewritten, so a stopped CGM would look current.
        """
        return bool(entity_id) and self._domain(entity_id) == DOMAIN

    @callback
    def _flag_own_source(self, entity_id: str) -> None:
        """Create the Repairs notice for a GlucoCraft entity chosen as a source (event loop; once per entity)."""
        flagged = self._own_source_flagged
        if entity_id in flagged:
            return
        flagged.add(entity_id)
        _LOGGER.warning("GlucoCraft: a GlucoCraft entity was chosen as a glucose source; its values are not used")
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{OWN_SOURCE_ISSUE_PREFIX}{entity_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="source_is_glucocraft",
            translation_placeholders={"entity": entity_id},
        )

    @callback
    def _flag_unitless_source(self, entity_id: str) -> None:
        """Create the Repairs notice for a source without a unit whose values look like mmol/L (event loop)."""
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{SOURCE_UNIT_ISSUE_PREFIX}{entity_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="source_unit_unknown",
            translation_placeholders={"entity": entity_id},
        )
        self._unit_issue_open.add(entity_id)

    @callback
    def _clear_unit_issue(self, entity_id: str) -> None:
        """Delete the "source without a unit" notice of a source once its readings have a unit (or are mg/dL values)."""
        open_issues = self._unit_issue_open
        if entity_id in open_issues:
            open_issues.discard(entity_id)
            ir.async_delete_issue(self.hass, DOMAIN, f"{SOURCE_UNIT_ISSUE_PREFIX}{entity_id}")

    @callback
    def _check_not_glucose_unit(self, entity_id: str, unit: str | None) -> None:
        """Create or delete the Repairs notice for a source that states a unit other than mg/dL or mmol/L (event loop;
        `unit`: that unit, None when the source states a glucose unit or none). Created once while it lasts."""
        issue_id = f"{NOT_GLUCOSE_UNIT_ISSUE_PREFIX}{entity_id}"
        open_issues = self._not_glucose_open
        if unit is None:
            if entity_id in open_issues:
                open_issues.discard(entity_id)
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        if entity_id in open_issues:
            return
        open_issues.add(entity_id)
        _LOGGER.warning("GlucoCraft: the %s source states a unit that is not a glucose unit", self._role(entity_id))
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="source_unit_not_glucose",
            translation_placeholders={"entity": entity_id, "unit": unit},
        )

    @callback
    def _check_save_error(self) -> None:
        """Create or delete the Repairs notice for a readings file that cannot be written (storage.save_error; event
        loop, after each timer save). Created once per episode, deleted after the first save that succeeds."""
        error = self.storage.save_error
        if error is None:
            if self._save_issue_open:
                self._save_issue_open = False
                ir.async_delete_issue(self.hass, DOMAIN, STORAGE_SAVE_ISSUE_ID)
            return
        if self._save_issue_open:
            return
        self._save_issue_open = True
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            STORAGE_SAVE_ISSUE_ID,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=STORAGE_SAVE_ISSUE_ID,
            translation_placeholders={"folder": str(self.storage.path.parent), "error": error},
        )

    @callback
    def _check_storage_problem(self) -> None:
        """Create or delete the Repairs notice for a damaged or unreadable readings file (storage.problem).

        A damaged or recovered file of which no copy could be kept (storage.kept_file None; saving then stays blocked
        until GlucoCraft starts again: a restart of Home Assistant, or a reload of the entry, which saving its settings
        also does) has its own notice, storage_<problem>_not_kept, which names no kept file. The readings held in memory
        meanwhile are lost at that start; the recorder's readings come back by the history import at it."""
        problem = self.storage.problem
        if not problem:
            ir.async_delete_issue(self.hass, DOMAIN, STORAGE_ISSUE_ID)
            return
        kept = self.storage.kept_file
        key = (
            f"storage_{problem}_not_kept"
            if problem in ("damaged", "recovered") and kept is None
            else f"storage_{problem}"
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            STORAGE_ISSUE_ID,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=key,
            translation_placeholders={
                "file": kept or "-",
                "folder": str(self.storage.path.parent),
            },
        )

    def _source_limits(
        self, entity_id: str | None, settings: Mapping[str, Any] | None = None, secondary: bool | None = None
    ) -> SourceLimits:
        """Return the resolved limits of a source (limits.resolve) plus "dexcom_type" and "nightscout".

        settings / secondary: the limit settings to read and whether the source is the second one, for settings not yet
        in use (sensor_span with data); by default the settings in use and the source's role in them."""
        if secondary is None:
            secondary = bool(entity_id) and entity_id == self._secondary_entity
        settings = self._settings if settings is None else settings
        keys = (
            (CONF_SECONDARY_SENSOR_LOWER_LIMIT, CONF_SECONDARY_SENSOR_UPPER_LIMIT)
            if secondary
            else (CONF_SENSOR_LOWER_LIMIT, CONF_SENSOR_UPPER_LIMIT)
        )
        domain = self._domain(entity_id)
        device = self._src_device.get(entity_id or "")
        learned = self._learned_limits.get(entity_id or "", {})
        resolved = resolve(
            setting(settings, keys[0]),
            setting(settings, keys[1]),
            domain,
            device,
            learned.get("lower"),
            learned.get("upper"),
        )
        return SourceLimits(**resolved, dexcom_type=is_dexcom_type(domain, device), nightscout=domain == "nightscout")

    def source_summary(self, entity_id: str) -> dict[str, Any]:
        """Return what the diagnostics show of a source: its resolved limits (limits.resolve, with the setting and what
        Automatic used), its delivering integration and its newest measurement time (epoch s, None before the first)."""
        return {
            "limits": self._source_limits(entity_id),
            "integration": self._domain(entity_id),
            "last_measurement": self._src_last_ts.get(entity_id),
        }

    def last_measurements(self) -> dict[str, str | None]:
        """Return the newest measurement time of the primary and of the second source (UTC ISO 8601, whole seconds;
        None without that source or before its first reading), the Last reading time sensor's attributes."""

        def iso(entity_id: str | None) -> str | None:
            """Return the source's newest measurement time as UTC ISO 8601 (whole seconds), None without one."""
            ts = self._src_last_ts.get(entity_id) if entity_id else None
            return dt_util.utc_from_timestamp(ts).replace(microsecond=0).isoformat() if ts else None

        return {"primary": iso(self._glucose_entity), "secondary": iso(self._secondary_entity)}

    def sensor_span(self, data: Mapping[str, Any] | None = None) -> tuple[int, int]:
        """Return the span of the very-low and the very-high threshold from the sources' limits (limits.threshold_span:
        at or above the lower limit, at or below the upper limit; the stricter of two sources).

        data: settings not yet saved (Configure > Glucose sources), judged with what GlucoCraft knows of each source
        entity (its integration, its device and the limits it has shown itself, which are kept per entity id); None:
        the sources and settings in use."""
        if data is None:
            return threshold_span(
                *(self._source_limits(e) for e in (self._glucose_entity, self._secondary_entity) if e)
            )
        primary, second = data.get(CONF_GLUCOSE_ENTITY), setting(data, CONF_SECONDARY_ENTITY)
        sources = [(primary, False), (None if second == primary else second, True)]
        return threshold_span(*(self._source_limits(e, data, role) for e, role in sources if e))

    def _apply_ranges(self, notices: bool = True) -> None:
        """Set the glucose ranges in use from the settings (settings.ranges): the very-low and the very-high threshold
        kept within the sensors' limits, the other limits as stored (a band the forced threshold closes is empty; the
        default ranges are never used because of a sensor limit), and (notices=True, event loop) open or close one
        Repairs notice per threshold that the sensor's limit replaces (RANGE_ISSUE_IDS), so that the user sees why a
        threshold differs from the settings. The notice closes by itself once the setting fits again. Only these two
        limits can differ from the stored ones (settings.ranges); in practice only the very-low threshold: the very-high
        threshold (at most 400) never exceeds an upper limit (at least 400), so its notice is a guard
        (settings.sensor_limit_conflicts)."""
        span = self.sensor_span()
        in_use = ranges(self._settings, span)
        self.range_low = in_use[CONF_RANGE_LOW]
        self.range_high = in_use[CONF_RANGE_HIGH]
        self.range_critical_low = in_use[CONF_RANGE_CRITICAL_LOW]
        self.range_critical_high = in_use[CONF_RANGE_CRITICAL_HIGH]
        self.range_ideal_low = in_use[CONF_RANGE_TIGHT_LOW]
        self.range_ideal_high = in_use[CONF_RANGE_TIGHT_HIGH]
        if not notices:
            return
        conflicts = sensor_limit_conflicts(self._settings, span)
        unit = self.display_unit
        for key, issue_id in RANGE_ISSUE_IDS.items():
            if key not in conflicts:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                continue
            stored, used = conflicts[key]
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=issue_id,
                translation_placeholders={
                    "stored": fmt(stored, unit, with_unit=True),
                    "limit": fmt(span[0] if key == CONF_RANGE_CRITICAL_LOW else span[1], unit, with_unit=True),
                    "used": fmt(used, unit, with_unit=True),
                },
            )

    def _learn_limits(self, entity_id: str, c: Classified) -> None:
        """Keep a limit the source showed itself (kept across restarts)."""
        learned = self._learned_limits.setdefault(entity_id, {})
        changed = False
        for key, value in (("lower", c.learned_lower), ("upper", c.learned_upper)):
            if value is not None and learned.get(key) != value:
                learned[key] = int(value)
                changed = True
        if changed:
            if entity_id in self._src_device:  # the device it belongs to (_forget_limit_of_former_device)
                self._learned_device[entity_id] = self._src_device[entity_id]
            _LOGGER.debug(
                "GlucoCraft: the %s source shows its own %s",
                self._role(entity_id),
                ", ".join(f"{k} limit {v} mg/dL" for k, v in learned.items()),
            )
            self._schedule_state_save()
            self._apply_ranges()  # a new sensor limit can move the very-low or the very-high threshold

    @callback
    def _check_mmol_too_high(self, entity_id: str, unit: str | None, number: float | None) -> None:
        """Create or delete the Repairs notice for a source that states mmol/L and whose values look like another unit:
        after MMOL_TOO_HIGH_RUN readings in a row above MMOL_TOO_HIGH, until the next reading at or below it (event
        loop).

        Only a notice: such a reading is used as any other (a real reading is never dropped on a guess), so it is
        stored at the upper limit as HI."""
        issue_id = f"{MMOL_TOO_HIGH_ISSUE_PREFIX}{entity_id}"
        if unit == UNIT_MMOLL and number is not None and number > MMOL_TOO_HIGH:
            n = self._mmol_high_run.get(entity_id, 0) + 1
            self._mmol_high_run[entity_id] = n
            if n == MMOL_TOO_HIGH_RUN:
                _LOGGER.warning(
                    "GlucoCraft: the %s source states mmol/L with values that look like another unit",
                    self._role(entity_id),
                )
                self._source_issue(issue_id, True, "source_mmol_too_high", {"entity": entity_id})
        elif self._mmol_high_run.get(entity_id) != 0:  # the first reading after a reload also closes an earlier notice
            self._mmol_high_run[entity_id] = 0
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _check_mgdl_too_low(self, entity_id: str, unit: str | None, number: float | None, nightscout: bool) -> None:
        """Create or delete the Repairs notice for a source that states mg/dL and whose values look like mmol/L: after
        MGDL_TOO_LOW_RUN readings in a row below MGDL_TOO_LOW, until the next value at or above it (event loop).

        Only a notice: such a value is treated as mg/dL like any other (no reading, or stored at the lower limit as LO).
        Counted before limits.classify, which sorts these values two ways. A whole number a source uses as a code (up
        to limits.ERROR_CODE_MAX, from Nightscout anything below DEXCOM_LOW_CODE) neither counts nor closes the notice,
        and neither does a text such as LO: a failing sensor sending a code is not a wrong unit."""
        issue_id = f"{MGDL_TOO_LOW_ISSUE_PREFIX}{entity_id}"
        if unit == UNIT_MGDL:
            if number is None or not number > 0:
                return
            if number.is_integer() and (number <= ERROR_CODE_MAX or (nightscout and number < DEXCOM_LOW_CODE)):
                return
            if number < MGDL_TOO_LOW:
                n = self._mgdl_low_run.get(entity_id, 0) + 1
                self._mgdl_low_run[entity_id] = n
                if n == MGDL_TOO_LOW_RUN:
                    _LOGGER.warning(
                        "GlucoCraft: the %s source states mg/dL with values that look like mmol/L",
                        self._role(entity_id),
                    )
                    self._source_issue(issue_id, True, "source_mgdl_too_low", {"entity": entity_id})
                return
        if self._mgdl_low_run.get(entity_id) != 0:  # the first reading after a reload also closes an earlier notice
            self._mgdl_low_run[entity_id] = 0
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _forget_limit_of_former_device(self, entity_id: str, device: str) -> None:
        """Forget a limit learned from a Nightscout source when the source shows another device: the limit belonged to
        the former sensor, and the new one shows its own if it has one. The device is kept across restarts with the
        limit; a limit learned before the device was kept takes the device seen first."""
        learned = self._learned_limits.get(entity_id)
        if not learned or self._domain(entity_id) != "nightscout":
            return
        former = self._learned_device.get(entity_id)
        if former == device:
            return
        self._learned_device[entity_id] = device
        if former is not None:
            del self._learned_limits[entity_id]
            _LOGGER.info(
                "GlucoCraft: the %s source shows another device; the limit it showed before is forgotten",
                self._role(entity_id),
            )
            self._apply_ranges()
        self._schedule_state_save()

    def _take_source_device(self, entity_id: str, device: Any) -> None:
        """Take the device a source shows (its attribute device; nothing without one): a Nightscout source's Dexcom
        codes and automatic upper limit depend on it (limits.resolve), and a limit learned for another device is
        forgotten (_forget_limit_of_former_device)."""
        if device:
            self._forget_limit_of_former_device(entity_id, str(device))
            self._src_device[entity_id] = str(device)

    def _source_reading(self, entity_id: str, state) -> tuple[float, str | None] | None:
        """Return a source's state as (mg/dL, limit), or None for no reading (a gap).

        Decided by limits.classify: LO / HI texts and codes are stored at the source's limit with the limit flag
        "low" / "high"; error codes are no reading; mmol/L is converted with 18.016 when the entity says so
        (unit_of_measurement). A source that states a unit other than mg/dL or mmol/L (a temperature, a percentage)
        gives no reading, with a Repairs notice. A source without a unit counts as mg/dL; when its values look like
        mmol/L (under 30 for 10 readings in a row) a Repairs notice says so, and those values are not used. The
        warm-up value of a FreeStyle Libre (limits.classify: kind "warmup") is no reading; it starts the warm-up
        (_start_warmup).
        """
        if self._is_own_entity(entity_id):
            self._flag_own_source(entity_id)
            return None
        attrs = state.attributes or {}
        not_glucose = non_glucose_unit(attrs.get("unit_of_measurement"))
        self._check_not_glucose_unit(entity_id, not_glucose)
        if not_glucose is not None:
            return None
        unit = unit_from_attribute(attrs.get("unit_of_measurement"))
        number = state_number(state.state)
        if unitless_mmol(unit is not None, number):  # the history import leaves such a value out the same way
            n = self._unitless_low.get(entity_id, 0) + 1
            self._unitless_low[entity_id] = n
            if n == UNITLESS_MMOL_RUN:
                self._flag_unitless_source(entity_id)
            return None
        if unit is None and number is not None:
            self._unitless_low[entity_id] = 0
        if unit is not None or (number is not None and number >= UNITLESS_MMOL_BELOW):
            # a reading the source-unit notice does not apply to: the notice closes as soon as the source is fixed
            self._clear_unit_issue(entity_id)
        self._check_mmol_too_high(entity_id, unit, number)
        self._take_source_device(entity_id, attrs.get("device"))
        lim = self._source_limits(entity_id)
        self._check_mgdl_too_low(entity_id, unit, number, lim["nightscout"])
        c = classify(
            state.state,
            unit,
            lim["lower"],
            lim["upper"],
            dexcom_type=lim["dexcom_type"],
            nightscout=lim["nightscout"],
            lower_set=lower_set_by_hand(lim),
            upper_set=upper_set_by_hand(lim),
        )
        if c.kind == "warmup":
            self._start_warmup()
            return None
        if c.kind == "none":
            self._note_refusal(entity_id, self.REFUSED_NONE)
            return None
        self._clear_refusal(entity_id, self.REFUSED_NONE)
        self._check_hand_limits(entity_id, unit, number, c, lim)
        if c.learned_lower is not None or c.learned_upper is not None:
            self._learn_limits(entity_id, c)
            lim = self._source_limits(entity_id)
            if c.limit == LIMIT_LOW:
                c.value = float(lim["lower"])
            elif c.limit == LIMIT_HIGH:
                c.value = float(lim["upper"])
        return c.value, c.limit

    @callback
    def _start_warmup(self) -> None:
        """Start a warm-up when a source shows its warm-up value (event loop); a warm-up in progress goes on. It ends
        with the first valid reading measured after it started (_process_reading)."""
        if self.sensor_warming_up:
            return
        self.warmup_started_at = int(time.time())
        self.sensor_warming_up = True
        self._schedule_state_save()
        _LOGGER.info("Sensor warm-up detected")
        self.async_set_updated_data(self.data)

    def _process_locked(
        self,
        entity_id: str | None,
        name: str,
        value: float,
        timestamp: int,
        from_source: bool = True,
        limit: str | None = None,
    ) -> ReadingResult:
        """Run _process_reading under the lock (executor).

        A reading that gets the lock after async_shutdown has begun is not stored: the final save may already have
        run, and the coordinator is being discarded (a reading the listeners dispatched just before they stopped).
        """
        with self._lock:
            if self._shut_down:
                return ReadingResult.UNCHANGED
            return self._process_reading(value, timestamp, entity_id, name, from_source, limit)

    def _process_and_notify(
        self,
        entity_id: str,
        name: str,
        value: float,
        timestamp: int,
        from_source: bool = True,
        limit: str | None = None,
    ) -> None:
        """Process a reading, then update the listeners (executor).

        The state listeners do not wait for this job, so an error is logged here, with the source's role (an error in
        a job nobody waits for would otherwise appear only later, out of context). Nothing is done once the coordinator
        is shut down (no reading stored, no update of a coordinator that is being discarded).
        """
        if self._shut_down:
            return
        try:
            result = self._process_locked(entity_id, name, value, timestamp, from_source, limit)
        except Exception:  # logged with its traceback; the next reading is processed as usual
            _LOGGER.exception("GlucoCraft: a reading of the %s source was not processed", self._role(entity_id))
            return
        if result == ReadingResult.UNCHANGED or self._shut_down:
            return
        self.hass.loop.call_soon_threadsafe(self.async_set_updated_data, self.data)

    def _role(self, entity_id: str | None) -> str:
        """Return a source's role for the log, "primary" or "second": never its entity id, which often contains a
        person's name (logs are shared in issue reports)."""
        return "primary" if entity_id is None or entity_id == self._glucose_entity else "second"

    def _note_refusal(self, entity_id: str | None, kind: str) -> None:
        """Log a reading that is not used, or used without its own time (kind: REFUSED_*), once per source and kind
        until _clear_refusal (INFO, the source's role only)."""
        key = (entity_id or "", kind)
        if key in self._refusals_logged:
            return
        self._refusals_logged.add(key)
        role = self._role(entity_id)
        if kind == self.REFUSED_TIME:
            _LOGGER.info(
                "GlucoCraft: the measurement-time attribute of the %s source holds no usable time; its readings take "
                "the time they arrive",
                role,
            )
        elif kind == self.REFUSED_OLD:
            _LOGGER.info(
                "GlucoCraft: a reading of the %s source measured more than 24 hours before the system clock and before "
                "the newest stored reading is not used",
                role,
            )
        else:
            _LOGGER.info(
                "GlucoCraft: the %s source shows a state that is no reading (an error code or an unknown text); it is "
                "not used",
                role,
            )

    def _clear_refusal(self, entity_id: str | None, kind: str) -> None:
        """End the logged episode of a kind of refusal for a source (its next good reading of that kind)."""
        self._refusals_logged.discard((entity_id or "", kind))

    def _too_old(self, timestamp: int, now: int) -> bool:
        """Return True for a reading measured more than LIVE_LOOKBACK_SECONDS before both the clock and the newest
        stored reading (with nothing stored: before the clock). Such a reading is not used: readings fill gaps at most
        24 hours back."""
        newest = self.storage.newest_timestamp
        return timestamp < now - self.LIVE_LOOKBACK_SECONDS and (
            not newest or timestamp < newest - self.LIVE_LOOKBACK_SECONDS
        )

    def _source_interval(self, entity_id: str | None, known_only: bool = False) -> int | None:
        """Return a source's own measurement interval: the median of its recent intervals from 20 s to 15 minutes.

        Until it is known (3 live intervals): None with known_only (the caller then applies its strict rules: the
        stored history's interval can differ from the live one, a 5-minute history before a 1-minute source); else the
        stored history's interval for the primary (10+ readings, storage.DETECT_MIN_READINGS), or 300 s
        (storage.DEFAULT_INTERVAL_SECONDS).
        """
        iv = median_interval(self._src_times.get(entity_id or "", []), window=None, shortest=20, longest=900)
        if iv is not None:
            return iv
        if known_only:
            return None
        if (
            entity_id == self._glucose_entity
            and self.storage.reading_count >= DETECT_MIN_READINGS
            and self.detected_interval_seconds
        ):
            return int(self.detected_interval_seconds)
        return DEFAULT_INTERVAL_SECONDS

    def _process_reading(
        self,
        raw_value: float,
        timestamp: int,
        entity_id: str | None = None,
        name: str | None = None,
        from_source: bool = True,
        limit: str | None = None,
    ) -> ReadingResult:
        """Process a new glucose reading (runs in the executor, under the lock) and return what was done with it.

        raw_value: mg/dL as limits.classify gives it (_source_reading: within the sensor's limits; a LO / HI reading
        at the source's limit, with limit "low" / "high"). The sources' special values never get here: they are decided
        in limits.py (_source_reading), where error codes and texts such as "???" are no reading and the warm-up value
        starts the warm-up, and the states "unavailable" / "unknown" are dropped by the state listeners.

        A stored reading is saved at once (normally an append of one record, storage.save), so that a loss of power
        or a crash loses no stored reading; the statistics run on the coordinator's timer.
        """
        now = int(time.time())
        # The system clock is earlier than the newest stored reading: either the clock is behind or those readings were
        # stored while it was ahead. A new reading cannot be placed in time, so nothing is stored and nothing is
        # deleted; the Repairs notice (_check_clock) explains it and lets the user discard the later readings. The
        # readings refused meanwhile are imported from the recorder once the clock is right (async_catch_up).
        if self.clock_behind(now):
            if self._catch_up_after is None:
                self._catch_up_after = self.storage.newest_timestamp
            return ReadingResult.UNCHANGED
        primary = entity_id is None or entity_id == self._glucose_entity
        if entity_id:
            self._src_last_value[entity_id] = raw_value
            last_ts = self._src_last_ts.get(entity_id, 0)
            if timestamp <= last_ts:
                # the same measurement again (or older) from this source; an older one is logged once, until the
                # source's next new reading, so that a source stuck behind its last measurement shows in the log
                if timestamp < last_ts and entity_id not in self._older_logged:
                    self._older_logged.add(entity_id)
                    _LOGGER.info(
                        "GlucoCraft: a reading of the %s source is older than its last measurement and is not used",
                        self._role(entity_id),
                    )
                return ReadingResult.UNCHANGED
            self._older_logged.discard(entity_id)
            self._src_last_ts[entity_id] = timestamp
            self._src_times[entity_id] = (self._src_times.get(entity_id, []) + [timestamp])[-self.SOURCE_TIMES_KEPT :]

        # MERGE BY MEASUREMENT TIME: one source, or any two. Only the sources' own measurement times and intervals are
        # used, nothing source-specific.
        #  - same measurement = within half the shorter of the two live intervals (15 s for one source): never
        #    stored twice
        #  - the primary wins: its copy replaces the secondary's
        #  - the secondary fills only real gaps: no stored reading within 3/4 of the primary's interval
        #  - fills land at their own time, at most 24 h back (_too_old)
        # So if either source stops, the other one carries on at once.
        if self._too_old(timestamp, now):
            self._note_refusal(entity_id, self.REFUSED_OLD)
            return ReadingResult.UNCHANGED
        self._clear_refusal(entity_id, self.REFUSED_OLD)
        ip = self._source_interval(self._glucose_entity)
        # Same measurement: with two sources, half the shorter of their live intervals, and 15 s while either is still
        # unknown (never too wide: a stored history's slower interval would merge a faster source's new readings);
        # with one source, 15 s (a repeated measurement arrives with its own time, which is caught above)
        same = 15
        if self._secondary_entity:
            kp = self._source_interval(self._glucose_entity, known_only=True)
            ks = self._source_interval(self._secondary_entity, known_only=True)
            if kp and ks:
                same = max(15, min(kp, ks) // 2)
        # a secondary without measurement times cannot be matched: used only while the primary is silent
        if (
            not primary
            and not from_source
            and now - self._src_last_ts.get(self._glucose_entity, 0) <= self.failover_seconds
        ):
            return ReadingResult.UNCHANGED
        if not primary:
            # After the primary's newest measurement, the secondary is used only once the primary has been silent for
            # one interval; otherwise a faster secondary would be stored between the primary's readings while the
            # primary is healthy, and the current source would flip at every reading. Gaps further back: the 3/4 rule.
            p_last = self._src_last_ts.get(self._glucose_entity, 0)
            if p_last and timestamp > p_last and timestamp - p_last < ip + same:
                return ReadingResult.UNCHANGED
        idx, dist = self.storage.nearest(timestamp)
        replace = False
        if primary:
            replace = dist is not None and dist <= same
        elif dist is not None and dist < SAME_MEASUREMENT_SHARE * ip:
            return ReadingResult.UNCHANGED  # the primary has this measurement (or no gap here)
        newest_before = self.storage.readings[-1].timestamp if self.storage.readings else 0
        # the primary's copy identical to the stored one: nothing to write, but the primary becomes the current source
        identical = (
            replace
            and self.storage.readings[idx].raw == raw_value
            and self.storage.readings[idx].timestamp == timestamp
            and self.storage.readings[idx].limit == limit
        )
        role = SOURCE_PRIMARY if primary else SOURCE_SECONDARY
        if primary:
            # The primary's new value replacing its own last reading, not a second source's copy (the Repairs notice
            # fast_source, _check_source_notices); a reading added ends the run. What becomes a reading is not changed.
            own = replace and self.storage.readings[idx].source == role
            first = self._replacing_run.first if self._replacing_run else timestamp
            self._replacing_run = ReplacingRun(first, timestamp) if own and not identical else None

        # A valid new reading measured after the warm-up started ends it. The old sensor's last reading, which can
        # arrive together with the new expiry, must not end it (a re-sent old value never gets here)
        if self.sensor_warming_up and timestamp > self.warmup_started_at:
            self.sensor_warming_up = False
            self.warmup_started_at = 0
            self._schedule_state_save()
            _LOGGER.info("Sensor warm-up complete: the first reading after it arrived")  # no glucose value in the log

        # Store reading (in time order; the primary's copy of a measurement replaces the secondary's)
        if not identical:
            if replace:
                self.storage.replace_at(idx, timestamp, raw_value, limit, role)
            else:
                self.storage.insert_reading(timestamp, raw_value, limit, role)

        newest = self.storage.readings[-1]
        if newest.timestamp == timestamp or newest_before <= timestamp:
            # the current value = the newest measurement, whichever source it came from
            if entity_id and entity_id != self.active_source_entity:
                _LOGGER.debug("GlucoCraft: the current value is from the %s source", self._role(entity_id))
                self.active_source_entity = entity_id
                self.second_source_active = not primary
                self._schedule_state_save()
            if name:
                self.active_source = name
            self.current_glucose = newest.raw
            self.current_limit = newest.limit
            self.last_reading_ts = newest.timestamp
            self._set_stale(now - newest.timestamp > self._stale_minutes * 60)
            # slope, DELTA and trend at the newest measurement
            self._apply_adaptive(newest.timestamp)
        if identical:
            return ReadingResult.SOURCE_CHANGED  # not a new reading, only the source changed

        # Re-detect interval periodically
        if self.storage.reading_count % self.REDETECT_EVERY_READINGS == 0:
            self.detected_interval_seconds = self.storage.detect_interval()
        self._update_limit_run()  # also when an older reading was filled in: it can join or split a stretch

        # Saved at once (an append of the new record); the statistics run on the coordinator's timer (every 5 minutes)
        self.storage.save()
        return ReadingResult.STORED

    # ADAPTIVE SMOOTHING, SLOPE AND DELTA, on each reading's own measurement time. The smoothing window adapts to how
    # steady the readings are: a fixed short window follows every short dip and turns it into "Falling Quickly" and
    # "Rising Quickly" while the sensor's own trend says stable.
    #  1. how steady: the range (max - min) of the last 20 min: < 10 mg/dL = steady, < 25 = moving, else active
    #  2. smoothing: the mean over 20 / 15 / 10 min (steadier = longer)
    #  3. slope: the change of that mean over the same window, per minute, capped at 3 / 5 / 8 mg/dL/min
    #  4. DELTA: the change of the smoothed level over 5 minutes
    # (the windows and caps: ADAPTIVE)

    @staticmethod
    def _measured(readings: Sequence[GlucoseReading]) -> list[float]:
        """Return the values of the measured readings: a reading at the sensor's limit (LO / HI) has none."""
        return [r.raw for r in readings if r.limit is None]

    def _adaptive(self, ts: int) -> tuple[float, float | None, float | None, float, int] | None:
        """Return (smoothed, slope or None, change over 5 min or None, 20-min range, window s) at time ts, or None."""
        last_20 = self.storage.window(ts - 1200 + 1, ts + 1)
        if not last_20 or last_20[-1].limit is not None:
            return None  # a reading at the sensor's limit has no value, so no trend and no change start from it
        r20 = self._measured(last_20)
        rng = max(r20) - min(r20)
        win, cap = next((w, c) for range_below, w, c in self.ADAPTIVE if rng < range_below)
        now_w = self._measured(self.storage.window(ts - win + 1, ts + 1))
        smoothed = _mean(now_w)
        old_w = self._measured(self.storage.window(ts - 2 * win + 1, ts - win + 1))
        slope = None if not old_w else max(-cap, min(cap, (smoothed - _mean(old_w)) / (win / 60)))
        p_w = self._measured(self.storage.window(ts - 300 - win + 1, ts - 300 + 1))
        change = None if not p_w else smoothed - _mean(p_w)
        return smoothed, slope, change, rng, win

    def _apply_adaptive(self, ts: int) -> None:
        """Set the slope, DELTA and the trend word (the trend categories of Abbott Diabetes Care, 2022) at measurement
        time ts."""
        ad = self._adaptive(ts)
        slope = ad[1] if ad else None
        self.slope = slope
        self.change_5min = ad[2] if ad and ad[1] is not None else None
        if slope is None:
            # not enough earlier readings (after a gap, a restart, a new sensor): the trend is unknown
            self.trend_arrow = ""
            self.trend_description = "Unknown"
            return
        # the five trend words of Abbott Diabetes Care (2022) on the rounded slope, the same rule as the card and the
        # panels
        r = round_half_up(slope, 1)  # halves away from zero, as the card and the panels round
        for bound, word, arrow in TREND_LIBRE_BANDS:
            if r >= bound:
                self.trend_description, self.trend_arrow = word, arrow
                break

    @property
    def trend_angle(self) -> int | None:
        """Return the panels' arrow angle: rate / 2 mg/dL/min x 90 deg, clamped to +-90, from the slope rounded to 0.1
        mg/dL/min as the trend word is (4.5-degree steps, given as whole degrees)."""
        if self.slope is None:
            return None
        return int(round_half_up(max(-2.0, min(2.0, round_half_up(self.slope, 1))) / 2 * 90))

    @property
    def delta_5min(self) -> float | None:
        """Return the 5-minute delta: the change of the smoothed glucose over 5 minutes, mg/dL, unrounded.

        Every display rounds it once, in its display unit (whole mg/dL or 0.1 mmol/L, halves away from zero with the
        float allowance of units.round_half_up), so that a value in mmol/L is never converted from a rounded mg/dL
        value.
        """
        return self.change_5min

    @property
    def failover_seconds(self) -> int:
        """Return the primary's silence after which a secondary without measurement times is used.

        Such a secondary cannot be merged by time, so it is used only once the primary has been silent for
        max(5 min, 3 x the primary's interval).
        """
        return max(300, 3 * self._source_interval(self._glucose_entity))

    @property
    def stale_minutes(self) -> int:
        """Return the no-current-data limit in minutes (options flow setting)."""
        return self._stale_minutes

    def _stats_pass(self, now: int | None = None) -> None:
        """Run the statistics pass of the timer and the setup (executor).

        The readings and their reading intervals are copied under the reading lock (storage.copy, a few milliseconds);
        the statistics are computed from the copy without it, so that readings are stored meanwhile. One pass at a
        time (_stats_lock); a pass from an older copy than the last one computed is skipped.
        """
        copied = self.locked(self._stats_copy)
        with self._stats_lock:
            self._compute_all_stats(now, copied)

    def _stats_copy(self) -> tuple[int, ReadingsCopy]:
        """Return the next statistics copy (under the reading lock): its number and the copied store."""
        self._stats_copies += 1
        return self._stats_copies, self.storage.copy(self.detected_interval_seconds)

    def _compute_all_stats(self, now: int | None = None, copied: tuple[int, ReadingsCopy] | None = None) -> None:
        """Compute the statistics of every period, the weekly trends and Change Over Time (executor).

        From `copied` (_stats_copy, taken under the reading lock); without it, from a copy taken here, under the
        reading lock the caller holds. Results from an older copy than the newest one computed are not assigned.
        The reading intervals of the gap rule are computed once for the store (storage.reading_intervals, cached),
        and so are the readings' weights and their positions on the glucose scale; each period is found by bisection
        instead of filtering the whole store per period. With custom ranges, the consensus statistics are computed in
        the same step (statistics.compute_period_stats_sets: only the band sums depend on the ranges). The numbers are
        the same as computing each period and each range set on its own (the tests compare both).
        """
        number, store = copied or self._stats_copy()
        if number < self._stats_computed:
            return
        self._stats_computed = number
        now = int(time.time()) if now is None else int(now)
        readings = store.readings
        iv = store.intervals
        ts = [r.timestamp for r in readings]
        sp = gap_weights(ts, iv)  # each reading's weight in the store (a window: gaps.window_weights)
        self._store_interval = iv[-1] if iv else None
        windows = {label: bisect.bisect_left(ts, now - seconds) for label, seconds in STAT_PERIODS}
        personal = self.ranges_in_use
        # the ranges in use, and the consensus (AGP) ranges when they differ: on the consensus ranges both results are
        # the same objects (sets[-1] is then the ranges in use)
        on_agp = not self.uses_custom_ranges()
        sets = (personal,) if on_agp else (personal, AGP_RANGES)
        flags = [band_flags(readings, **ranges) for ranges in sets]  # each set's bands per reading (a window: a slice)

        # The periods on the ranges in use and on the consensus bands (dashboard Today page). Built in local
        # dictionaries and assigned once: a reader outside the lock (a summary, a sensor, the HTTP view) sees the
        # previous or the new statistics, never a dictionary filled in between
        period_stats: dict[str, PeriodStats | None] = {}
        agp_stats: dict[str, PeriodStats | None] = {}
        for label, seconds in STAT_PERIODS:
            i = windows[label]
            per_set = compute_period_stats_sets(
                readings[i:],
                label,
                seconds,
                self.detected_interval_seconds,
                sets,
                weights=window_weights(sp, iv, i, len(readings)),
                flags=window_flags(flags, i, len(readings)),
            )
            period_stats[label], agp_stats[label] = per_set[0], per_set[-1]
        self.period_stats = period_stats
        self.period_stats_agp = agp_stats

        # Weekly trends, and the consensus (AGP) weeks the trend pages use (the same list on the consensus ranges)
        self.weekly_trends_at = now
        weeks = compute_weekly_trends_sets(
            readings,
            sets,
            num_weeks=26,
            detected_interval=self.detected_interval_seconds,
            intervals=iv,
            now=now,
            spans=sp,
            flags=flags,
        )
        self.weekly_trends, self.weekly_trends_agp = weeks[0], weeks[-1]
        # Change Over Time for both standards (hourly; 26 x two 14-day windows each). The weekly values sent to the
        # display card and the panels are the consensus 14-day values at each week's end, so they agree with the
        # dashboard's Change Over Time (14 days is the consensus period, Battelino et al., 2019).
        if not self.change or now - self.change_at >= 3600:
            per_set = compute_change_sets(store, self.detected_interval_seconds, now, sets, iv, sp, flags)
            agp = per_set[-1]
            self.change = {"consensus": agp, "personal": per_set[0]}
            self.change_at = now
            self._cap_findings = self._scan_sensor_cap(readings)  # hourly, with Change Over Time
            stats14 = agp.stats14
            last = max((i for i, s in enumerate(stats14) if s is not None), default=-1)
            self.trend_weeks = stats14[: last + 1]  # a week with too few readings in between is sent empty
            self.trend_gmi14 = [gmi_shown(s, GMI_MIN_PERIOD_SECONDS) for s in self.trend_weeks]
            # the GRI on the consensus bands only (Klonoff et al., 2023): the weeks on the ranges in the settings have
            # none
            self.trend_gri14 = [gri_shown(s, GMI_MIN_PERIOD_SECONDS) for s in self.trend_weeks]
            self.trend_directions = self._directions(self.trend_weeks, self.trend_gri14)
            pst = self.change["personal"].stats14  # the same windows on the ranges in the settings
            self.personal_trend_weeks = pst[: last + 1]
            self.personal_trend_directions = self._directions(self.personal_trend_weeks, [])

    @callback
    def _check_stuck_source(self, entity_id: str, value: float, limit: str | None, ts: int, from_source: bool) -> None:
        """Create or delete the Repairs notice for a source without its own measurement time that may have stopped
        (event loop; once per live reading dispatched): after STUCK_READINGS readings in a row with exactly the same
        value over at least STUCK_SECONDS (first to last), until the value changes or a reading carries its own time.

        Such a source gives GlucoCraft only the time each value arrives, so a source that keeps sending its last value
        looks like a steady glucose level; the readings are not changed (a very steady level can do the same). A source
        with its own measurement time is never concerned: a repeated value with the same time is no new reading
        (_process_reading). The notice is kept across a reload of the entry while the value stays the same (the issue
        holds the value)."""
        issue_id = f"{STUCK_SOURCE_ISSUE_PREFIX}{entity_id}"
        run = self._stuck.get(entity_id)
        if from_source:
            if run is None or run.notice_open:  # not known after a reload, or open: the notice goes
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            self._stuck[entity_id] = StuckRun(None, 0, ts, ts, False)
            return
        key = (value, limit)
        if run is None:  # the first reading of this run of GlucoCraft (a start or a reload of the entry)
            issue = ir.async_get(self.hass).issues.get((DOMAIN, issue_id))
            data = (issue.data or {}) if issue is not None else {}
            still = issue is not None and data.get("value") == value and data.get("limit") == limit
            if issue is not None and not still:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            run = StuckRun(key, 0, ts, ts, still)
        if run.key != key:
            if run.notice_open:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            self._stuck[entity_id] = StuckRun(key, 1, ts, ts, False)
            return
        run = run._replace(count=run.count + 1, last=ts)
        self._stuck[entity_id] = run
        if run.notice_open or run.count < self.STUCK_READINGS or run.last - run.first < self.STUCK_SECONDS:
            return
        run = self._stuck[entity_id] = run._replace(notice_open=True)
        _LOGGER.warning(
            "GlucoCraft: the %s source has delivered the same value %d times in a row and gives no measurement time",
            self._role(entity_id),
            run.count,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="source_stuck",
            translation_placeholders={
                "entity": entity_id,
                "count": str(run.count),
                "minutes": str((run.last - run.first) // 60),
            },
            data={"value": value, "limit": limit},
        )

    @callback
    def _check_hand_limits(
        self, entity_id: str, unit: str | None, number: float | None, c: Classified, lim: SourceLimits
    ) -> None:
        """Create the Repairs notice for a source whose numbers lie beyond a sensor limit set by hand (event loop): a
        number below a lower limit set by hand is shown as LO, one above an upper limit set by hand as HI, so the number
        itself is never shown, although a source that sends it can measure there (the limit may be wrong). The CGM's
        own codes (39 mg/dL and 2.2 mmol/L, a Dexcom-type source's 401 mg/dL) and a reading at the limit itself are not
        counted. Created once per
        source and run; it closes with the next start of the entry (every settings change restarts it), and the next
        such number opens it again."""
        if number is None or c.limit not in (LIMIT_LOW, LIMIT_HIGH):
            return
        mmol = unit == UNIT_MMOLL
        mg = to_mgdl(number, UNIT_MMOLL if mmol else None)
        if c.limit == LIMIT_LOW:
            hit = (
                lower_set_by_hand(lim)
                and mg < lim["lower"]
                and (number > LO_MMOL if mmol else number > DEXCOM_LOW_CODE)
            )
            prefix, key, limit = HAND_LOW_ISSUE_PREFIX, "hand_limit_low", lim["lower"]
        else:
            dexcom_code = lim["dexcom_type"] and not mmol and number == DEXCOM_HIGH_CODE  # HIGH, not a number
            hit = upper_set_by_hand(lim) and mg > lim["upper"] and not dexcom_code
            prefix, key, limit = HAND_HIGH_ISSUE_PREFIX, "hand_limit_high", lim["upper"]
        if not hit or f"{prefix}{entity_id}" in self._hand_limit_open:
            return
        self._hand_limit_open.add(f"{prefix}{entity_id}")
        _LOGGER.warning(
            "GlucoCraft: the %s source sends numbers beyond a sensor limit set by hand", self._role(entity_id)
        )
        unit_shown = self.display_unit
        self._source_issue(
            f"{prefix}{entity_id}",
            True,
            key,
            {
                "entity": entity_id,
                "value": fmt(mg, unit_shown, with_unit=True),
                "limit": fmt(limit, unit_shown, with_unit=True),
            },
        )

    def _update_limit_run(self) -> None:
        """Set the current stretch of readings at the sensor's limit from the stored readings (under the reading lock,
        or before the listeners start): limit_since, last_measured_mgdl and last_measured_ts (limit_runs.py; a data gap
        by the gap rule starts a new stretch)."""
        readings = self.storage.readings
        if not readings:
            self.limit_since = self.last_measured_mgdl = self.last_measured_ts = None
            return
        first = None
        if readings[-1].limit in (LIMIT_LOW, LIMIT_HIGH):
            first = current_limit_run(readings, self.storage.reading_intervals(self.detected_interval_seconds))
        self.limit_since = readings[first].timestamp if first is not None else None
        measured = last_measured_before(readings, len(readings) - 1 if first is None else first)
        self.last_measured_mgdl = measured.raw if measured is not None else None
        self.last_measured_ts = measured.timestamp if measured is not None else None

    def _end_overlong_warmup(self, now: int) -> None:
        """End a warm-up that began WARMUP_MAX_SECONDS ago or more (GlucoCraft rule): then the normal no-current-data
        rule, words and notice apply. A warm-up otherwise ends with the first valid reading measured after it began
        (_process_reading)."""
        if not self.sensor_warming_up or not self.warmup_started_at:
            return
        if now - self.warmup_started_at < self.WARMUP_MAX_SECONDS:
            return
        self.sensor_warming_up = False
        self.warmup_started_at = 0
        self._schedule_state_save()
        _LOGGER.info("Sensor warm-up ended after 24 hours without a reading measured after it began")

    # ---- the gap rule's interval per source
    def reading_interval(self, entity_id: str | None = None) -> int | None:
        """Return a source's reading interval: the median of its last 10 measurement intervals (gaps.py).

        For the source in use without live readings yet: the interval of the newest stored stretch.
        """
        entity_id = entity_id or self.active_source_entity or self._glucose_entity
        iv = median_interval(self._src_times.get(entity_id or "", []))
        if iv is None and entity_id == (self.active_source_entity or self._glucose_entity):
            iv = self._store_interval or None
        return int(iv) if iv else None

    @staticmethod
    def trend_week_values(
        s: PeriodStats | None, gmi14: float | None = None, gri14: float | None = None
    ) -> dict[str, float | None]:
        """Return one week's values as the Change Over Time pages show them for the range set of `s`, with the 14-day
        GMI and the 14-day GRI (the GRI only for the consensus weeks). The keys below_70 / above_180 hold the time
        below the low limit and above the high limit of that range set: 70 and 180 mg/dL for the consensus weeks, the
        custom limits for the custom weeks."""
        if s is None:
            return {k: None for k, _, _ in TREND_PAGES}
        return {
            "tir": s.pct_time_in_target_range,
            "titr": s.pct_time_in_ideal_range,
            "mean": s.mean,
            "cv": s.cv,
            "gmi": gmi14,
            "below_70": s.time_below_low,
            "above_180": s.time_above_high,  # the combined bands, rounded once
            "std_dev": s.std_dev,
            "min": s.min_value,
            "gri": gri14,
        }

    def _directions(self, weeks: list, gri14: list[float | None]) -> dict[str, str | None]:
        """Return per trend page: Increasing / Decreasing / Stable (last 4 weeks vs the 4 before), or None. gri14: the
        14-day GRI of each week (empty for the weeks on the ranges in the settings, which have no GRI)."""
        out: dict[str, str | None] = {}
        vals = [
            self.trend_week_values(
                s,
                self.trend_gmi14[i] if i < len(self.trend_gmi14) else None,
                gri14[i] if i < len(gri14) else None,
            )
            for i, s in enumerate(weeks)
        ]
        for key, lo, hi in TREND_PAGES:
            out[key] = None
            if len(vals) < 8:
                continue
            recent = [v[key] for v in vals[0:4] if v[key] is not None]
            prior = [v[key] for v in vals[4:8] if v[key] is not None]
            if not recent or not prior:
                continue
            diff = sum(recent) / len(recent) - sum(prior) / len(prior)
            thr = (hi - lo) * TREND_DIRECTION_SHARE
            out[key] = "Increasing" if diff > thr else "Decreasing" if diff < -thr else "Stable"
        return out

    @property
    def ranges_in_use(self) -> dict[str, float]:
        """The six ranges in use (mg/dL) under the keys of const.AGP_RANGES (range_low, range_ideal_low, ...), which are
        the names of the coordinator's attributes."""
        return {key: getattr(self, key) for key in AGP_RANGES}

    def uses_custom_ranges(self) -> bool:
        """Return True when the ranges in use differ from the consensus (AGP) ranges."""
        return self.ranges_in_use != AGP_RANGES

    def goals_custom(self) -> bool:
        """Return True when any goal differs from its default (settings.GOAL_DEFAULTS; each goal's setting key is the
        name of the coordinator's attribute: goal_tbr, ...)."""
        return any(getattr(self, key) != default for key, default in GOAL_DEFAULTS.items())

    @property
    def weekly_pages_custom(self) -> bool:
        """Return True when the weekly pages of the display card and the panels show the custom standard.

        That is the setting, and only while custom ranges or goals are set.
        """
        return self._weekly_pages == WEEKLY_PAGES_PERSONAL and (self.uses_custom_ranges() or self.goals_custom())

    def configuration(self) -> dict[str, Any]:
        """Return every setting and resolved value as one dictionary (the Configuration sensor's attributes).

        Users can build their own dashboards from it; the dashboard's Settings page reads it.
        """
        c = self._settings

        def src(e, attr):
            """Return one source's settings and live values, or None without a source. Its newest measurement time is
            an attribute of the Last reading time sensor (last_measurements), whose state changes with every reading
            anyway, so that this sensor does not change with every reading."""
            if not e:
                return None
            lim = self._source_limits(e)
            iv = self.reading_interval(e)
            return {
                "entity": e,
                "name": (self.hass.states.get(e).attributes.get("friendly_name", e) if self.hass.states.get(e) else e),
                "time_attribute": attr or "auto",
                "in_use": e == self.active_source_entity,
                # the sensor's measurement range: resolved mg/dL, the setting ("auto" or the value) and what
                # Automatic used
                "lower_limit": lim["lower"],
                "upper_limit": lim["upper"],
                "lower_limit_setting": lim["lower_setting"],
                "upper_limit_setting": lim["upper_setting"],
                "lower_limit_auto_reason": lim["lower_reason"],
                "upper_limit_auto_reason": lim["upper_reason"],
                # the gap rule (gaps.py): this source's reading interval and its gap threshold
                "reading_interval_seconds": iv,
                "gap_threshold_seconds": gap_threshold(iv) if iv else None,
            }

        primary = src(self._glucose_entity, self._time_attr.get(self._glucose_entity))
        secondary = src(self._secondary_entity, self._time_attr.get(self._secondary_entity or ""))
        active_iv = self.reading_interval()
        # time in range: "more than", as the consensus target >70 % (Battelino et al., 2019); a goal with no value =
        # no goal set (tight range and GMI have none by default). The attribute keys use the consensus terms; the
        # stored setting keys (CONF_*) keep their names
        values = {
            "time_in_range": self.goal_time_in_target_range,
            "time_in_tight_range": self.goal_time_in_ideal_range,
            "time_low": self.goal_tbr,
            "time_very_low": self.goal_tbr_critical,
            "time_high": self.goal_tar,
            "time_very_high": self.goal_tar_critical,
            "cv": self.goal_cv,
            "gmi": self.goal_gmi,
        }
        goals = {key: (GOAL_OPS[key], value) for key, value in values.items()}  # the direction of each (GOAL_OPS)
        return {
            "primary_source": primary,
            "secondary_source": secondary,
            "sources": {"primary": primary, "secondary": secondary},
            # the gap rule for the source in use: a gap is an interval longer than max(15 min, 3 x the reading
            # interval); the panels compute the same rule from their readings
            "reading_interval_seconds": active_iv,
            "gap_threshold_seconds": gap_threshold(active_iv) if active_iv else gap_threshold(None),
            "no_current_data_minutes": self._stale_minutes,
            "unit": self.display_unit,
            # resolved for the cards and panels (time_format "12"/"24", date_format "MDY"/"DMY"/"YMD", first_weekday
            # 0 = Monday ... 6 = Sunday, decimal_separator "."/",", a1c_unit "percent"/"mmol_mol"/"both", paper_size
            # "letter"/"a4"), and each setting as chosen ("auto" or a choice) as <key>_setting
            **self.regional.as_dict(),
            **{f"{k}_setting": v for k, v in self._regional_settings().items()},
            # the display color of each glucose zone (the user's colors or the default palette): every card and panel
            # colors the glucose value and everything else that shows a zone by color with these, never with colors of
            # its own; the range bands of the graphs alone use band_colors (by default the CGM Report's colors)
            "zone_colors": self.zone_colors,
            "band_colors": band_colors(c),
            # the backgrounds ("theme" = the Home Assistant theme, or "#RRGGBB") and the text color on each (black or
            # white, whichever contrasts more; None with the theme, whose text color is used); the graphs keep their
            # black area on every background
            "card_background": setting(c, CONF_CARD_BACKGROUND),
            "card_foreground": foreground(setting(c, CONF_CARD_BACKGROUND)),
            "panel_background": setting(c, CONF_PANEL_BACKGROUND),
            "panel_foreground": foreground(setting(c, CONF_PANEL_BACKGROUND)),
            # the setting "Outline text" (off by default): a 1-pixel edge around the graph labels, the glucose value and
            # every other text in a zone color, on the display card and the wall panels
            "text_outline": setting(c, CONF_TEXT_OUTLINE),
            # the setting "Trend shown as" ("arrow" or "word"): the TREND column of the display cards and the wall
            # panels shows the arrow or the trend word (the zone word is always under the glucose value)
            "trend_display": setting(c, CONF_TREND_DISPLAY),
            # the setting "Fade the words when there is no current reading" (on by default): the cards and the panels
            # fade the words in the value's place while there is no current reading (all three words)
            "flash_no_current_data": setting(c, CONF_FLASH_NO_CURRENT_DATA),
            "display_card": setting(c, CONF_DISPLAY_CARD),
            # the two optional features: the dashboard builds the Custom Ranges page, the report page offers the custom
            # appendix and the panels read the 6-hour summary of the custom ranges only while custom_ranges_and_goals is
            # on; separate_statistics says whether the per-statistic period entities exist
            "custom_ranges_and_goals": self.custom_ranges_and_goals,
            "separate_statistics": self.separate_statistics,
            # the manifest version (shown in the dashboard's Documentation, About and License)
            "integration_version": self.integration_version or None,
            # the Maintenance status (maintenance.py): one entry per issue; the dashboard's Maintenance button counts
            # them and the Maintenance page lists them
            "maintenance_issues": self.maintenance.issues(),
            # true while at least one GlucoCraft Repairs notice is open and not ignored by the user (a notice the user
            # chose Ignore on does not count): the display card and the wall panels then show "Check Home Assistant"
            # in the status bar, never the notice's text
            "attention": self.maintenance.attention,
            "ranges": {
                "very_low": self.range_critical_low,
                "low": self.range_low,
                "tight_low": self.range_ideal_low,
                "tight_high": self.range_ideal_high,
                "high": self.range_high,
                "very_high": self.range_critical_high,
            },
            # the consensus ranges (the ranges of the CGM Report)
            "consensus_ranges": public_agp_ranges(),
            # a goal without a value is left out = "no goal set" (the dashboard lists the missing ones)
            "goals": {k: {"op": op, "value": v} for k, (op, v) in goals.items() if v is not None},
            "goals_custom": self.goals_custom(),
            # the weekly pages of the display card and the panels: the setting, and the standard shown
            # (values "custom" / "consensus"; the stored setting value "personal" is shown as "custom")
            "weekly_pages": {
                "setting": public_key(self._weekly_pages),
                "offered": self.uses_custom_ranges() or self.goals_custom(),
                "shown": "custom" if self.weekly_pages_custom else "consensus",
            },
            "expiry_entity": self._expiry_entity,
            "notifications": {
                "daily": setting(c, CONF_NOTIFY_ENABLED),
                "daily_time": setting(c, CONF_NOTIFY_TIME),
                "weekly": setting(c, CONF_NOTIFY_WEEKLY_ENABLED),
                "weekly_day": setting(c, CONF_NOTIFY_WEEKLY_DAY),
                # the no-data notice in Home Assistant and its delay (its phone message: phone_notifications types)
                "no_data_notice": setting(c, CONF_NO_DATA_NOTICE),
                "no_data_notice_delay_minutes": setting(c, CONF_NO_DATA_NOTICE_DELAY),
            },
            "phone_notifications": {
                "targets": setting(c, CONF_PHONE_TARGETS),
                "types": [public_key(kind) for kind in setting(c, CONF_PHONE_TYPES)],
            },
            "retention_days": DEFAULT_RETENTION_DAYS,
            "dashboard": setting(c, CONF_SHOW_DASHBOARD),
            # the setting When GlucoCraft is deleted: keep (the readings for a reinstall) or delete (everything)
            "on_removal": setting(c, CONF_ON_REMOVAL),
            # the wall-panel address exists (a record kept in the entry, not a setting)
            "panel_feed": bool(c.get(CONF_PANEL_WEBHOOK_ID)),
            # a report printer is set: only whether it is, never the address (every user can read these attributes)
            "printer": bool(setting(c, CONF_PRINTER)),
        }

    @property
    def glucose_zone(self) -> str | None:
        """Return the current value's zone on the ranges in use (the displays color the value with it).

        Decided on the stored value (one decimal), as the binary sensors and the statistics decide it, not on a value
        rounded to whole mg/dL first; otherwise a value at a limit could fall in different zones in different places.
        A LO reading is always very low and a HI reading always very high: the very-low and the very-high threshold
        stay within the sensor's limits (_apply_ranges), and limits.below / above place a LO / HI reading beyond a
        threshold equal to its stored value.
        """
        if self.current_glucose is None:
            return None
        limit = self.current_limit
        g = self.current_glucose
        if below(g, limit, self.range_critical_low):
            return "very_low"
        if below(g, limit, self.range_low):
            return "low"
        if below(g, limit, self.range_ideal_low):
            return "near_low"
        if not above(g, limit, self.range_ideal_high):
            return "tight_range"
        if not above(g, limit, self.range_high):
            return "near_high"
        if not above(g, limit, self.range_critical_high):
            return "high"
        return "very_high"

    @property
    def zone_colors(self) -> dict[str, str]:
        """Return the color of each glucose zone: the user's colors from the settings, otherwise the default palette."""
        return zone_colors(self._settings)

    @property
    def glucose_zone_color(self) -> str | None:
        """Return the color of the current value's zone (None without a reading)."""
        return self.zone_colors.get(self.glucose_zone or "")

    @property
    def glucose_zone_label(self) -> str | None:
        """Return the zone word of a zone outside the range ("Very low", "Low", "High", "Very high"; zone_label).

        None in range and without a reading; the displays show display_word, a word for every zone.
        """
        return zone_label(self.glucose_zone)

    @property
    def display_word(self) -> str:
        """Return the zone word every display shows under the glucose value, in every setting.

        The range the reading is in ("Very low", "Low", "In range", "Tight range", "High", "Very high"); a LO / HI
        reading lies outside what the sensor can measure, and its word says so ("Sensor limit"; its zone stays very low
        / very high). Empty while there is no current data (the value shown is then an old reading) and during a sensor
        warm-up (trend_current). After the sensor's stated expiry it follows the data.
        """
        if self.current_glucose is None or not self.trend_current:
            return ""
        if self.current_limit in (LIMIT_LOW, LIMIT_HIGH):
            return t("limit.word")
        return display_zone_word(self.glucose_zone)

    @property
    def trend_word(self) -> str:
        """Return the trend word in sentence case ("Changing slowly"), shown in the TREND column with the setting
        "Trend shown as: word"; empty while the trend is unknown (no current data, a warm-up, too few readings)."""
        key = TREND_KEYS.get(self.trend_description)  # None while the trend is unknown
        return t(f"trend.word.{key}") if key else ""

    @property
    def measured_glucose(self) -> float | None:
        """Return the newest reading's value, None while it is a reading at the sensor's limit (LO / HI): stored at the
        limit for the file format, it is no measurement, and no number leaves GlucoCraft as one."""
        return None if self.current_limit in (LIMIT_LOW, LIMIT_HIGH) else self.current_glucose

    @property
    def current_limit_text(self) -> str | None:
        """Return "LO" / "HI" while the current reading is a limit reading (shown instead of the number), else None."""
        if self.current_glucose is None or self.current_limit not in (LIMIT_LOW, LIMIT_HIGH):
            return None
        return t(f"limit.{self.current_limit}")

    @property
    def expiry_entity(self) -> str | None:
        """Return the optional expiry entity picked in the settings, or None."""
        return self._expiry_entity or None

    def expiry(self) -> tuple[datetime | None, str]:
        """Return the CGM sensor's expiry from the optional expiry entity and the form it was read from
        (expiry.parse_expiry): (None, "not_configured") when no expiry entity is picked."""
        if not self._expiry_entity:
            return None, "not_configured"
        return parse_expiry(self.hass.states.get(self._expiry_entity))

    @property
    def expires_at(self) -> datetime | None:
        """Return the CGM sensor's expiry (UTC datetime) from the optional expiry entity, or None."""
        return self.expiry()[0]

    @property
    def display_status(self) -> str:
        """Return what the status bar shows.

        In priority order: warm-up > expired > no data > no current data > expiring soon > current. It decides the
        status label and color only; whether the data are current is the no_current_data flag, which every display
        tests (so an expired sensor that still delivers shows its value in its zone color; without current data the
        displays say "Sensor expired"). The displays add their own clock: no current data when their own count of the
        reading's age passes the no-current-data limit (Home Assistant unreachable).
        """
        if self.sensor_warming_up:
            return "warm_up"
        exp = self.expires_at
        now = dt_util.utcnow()
        if exp is not None and exp <= now:
            return "expired"
        if not self.last_reading_ts:
            return "no_data"
        if self.no_current_data:
            return "no_current_data"
        if exp is not None and (exp - now).total_seconds() <= EXPIRING_SOON_MINUTES * 60:
            return "expiring_soon"
        return "current"

    @property
    def display_status_info(self) -> tuple[str, str]:
        """Return the status's label and color (const.status_label, const.STATUS_COLORS)."""
        return status_label(self.display_status), STATUS_COLORS[self.display_status]

    async def async_shutdown(self) -> None:
        """Stop the listeners and the timer, save everything and stop the base class's refresh machinery.

        Home Assistant also calls this when the entry unloads (the coordinator is created with its config entry), so
        a second call does nothing.
        """
        if self._shut_down:
            return
        self._shut_down = True
        for unsub in self._unsub_state:
            unsub()
        self._unsub_state.clear()
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        await self.async_save_all()
        await super().async_shutdown()


# The config entry of GlucoCraft carries its coordinator as runtime data (Home Assistant's runtime-data rule):
# entry.runtime_data is set in __init__.async_setup_entry and removed by Home Assistant when the entry unloads.
GlucoCraftConfigEntry = ConfigEntry[GlucoCraftCoordinator]


def loaded_coordinator(hass: HomeAssistant) -> GlucoCraftCoordinator | None:
    """Return the coordinator of the loaded GlucoCraft entry, or None when GlucoCraft is not loaded.

    There is at most one entry (single_config_entry in manifest.json).
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        if isinstance(coordinator, GlucoCraftCoordinator) and not coordinator._shut_down:
            return coordinator
    return None
