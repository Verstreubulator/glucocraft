# License: AGPL-3.0-only (LICENSE).
"""Sensor platform for GlucoCraft: the current values, the status, the configuration and the period statistics.

Three groups (entity_groups): the sensors every installation has (the current reading, the 5-minute delta, the trend,
the status, the configuration, the sources, the data history, the period summaries on the consensus ranges, the
weekly statistics, Change over time, the sensor expiry, the spoken summary); the custom-range sensors of each period
(optional feature Custom ranges and goals); and the statistic sensors of each period (optional feature Separate
statistics entities). The glucose sensors are in mg/dL with Home Assistant's blood glucose device class, so that Home
Assistant shows them in the display unit. A state is rounded once from the exact value; the attributes keep the exact
values that the cards, the panels and the report read, and the attributes that change with every reading are left
out of the recorder. Every entity's unique id, name and entity id: entity.py.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL, PERCENTAGE, EntityCategory, UnitOfBloodGlucoseConcentration
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .change import ChangeOverTime
from .const import (
    EXPIRING_SOON_MINUTES,
    GMI_MIN_PERIOD_SECONDS,
    PERIOD_SECONDS,
    STAT_PERIODS,
    STATUS_COLORS,
    sensor_active_label,
    sensor_active_note,
)
from .coordinator import GlucoCraftCoordinator
from .entity import GlucoCraftEntity
from .i18n import t, tp
from .spoken import reading_when, spoken_summary
from .stale_entities import record_created
from .statistics import PeriodStats, enough_data, gmi_shown, gri_shown
from .units import MGDL_PER_MMOLL, is_mmol, round_half_up  # halves away from zero, as every display rounds

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

_LOGGER = logging.getLogger(__name__)

# The entities are updated by the coordinator, never polled one by one: no limit on parallel updates
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up GlucoCraft sensors (the coordinator is the entry's runtime data).

    The sensors of an optional feature that is off are not created; the setup then removes their registry entries with
    those of every other entity not created (stale_entities.py), so that no entity is left behind as unavailable.
    """
    coordinator: GlucoCraftCoordinator = entry.runtime_data
    created, _switched_off = split_by_features(coordinator, entity_groups(coordinator, entry))
    async_add_entities(created)
    record_created(hass, entry, "sensor", created)


# The groups of sensors (entity_groups). GROUP_ALWAYS exists in every installation. GROUP_CUSTOM exists only while the
# optional feature Custom ranges and goals is on: per period the custom ranges summary and the six custom time-in-band
# sensors. GROUP_STATISTICS exists only while Separate statistics entities is on: per period the mean, standard
# deviation, CV, minimum, maximum, sensor active and readings, and the GMI and the GRI from 14 days.
GROUP_ALWAYS, GROUP_CUSTOM, GROUP_STATISTICS = "always", "custom", "statistics"


def entity_groups(coordinator: GlucoCraftCoordinator, entry: ConfigEntry) -> dict[str, list[SensorEntity]]:
    """Return every GlucoCraft sensor the entry can have, by group (GROUP_ALWAYS, GROUP_CUSTOM, GROUP_STATISTICS)."""
    always: list[SensorEntity] = [
        # Core sensors
        GlucoCraftCurrentGlucose(coordinator, entry),
        GlucoCraftDelta5Min(coordinator, entry),
        GlucoCraftTrendArrow(coordinator, entry),
        GlucoCraftLastReading(coordinator, entry),
        GlucoCraftStatus(coordinator, entry),
        GlucoCraftConfiguration(coordinator, entry),
        GlucoCraftActiveSource(coordinator, entry),
        GlucoCraftDataHistory(coordinator, entry),
        GlucoCraftSensorExpiry(coordinator, entry),
        GlucoCraftSpokenSummary(coordinator, entry),  # disabled by default
    ]

    custom: list[SensorEntity] = []
    statistics: list[SensorEntity] = []
    for label, _ in STAT_PERIODS:
        # every value of the period on the consensus (AGP) bands, whatever the ranges in use, and the same on the
        # custom ranges: what GlucoCraft's own pages, cards and panels read
        always.append(GlucoCraftPeriodConsensus(coordinator, entry, label))
        custom.append(GlucoCraftPeriodCustom(coordinator, entry, label))
        # the custom time-in-band values, one entity each
        custom.extend(
            cls(coordinator, entry, label)
            for cls in (
                GlucoCraftPeriodTIR,
                GlucoCraftPeriodTITR,
                GlucoCraftPeriodTimeLow,
                GlucoCraftPeriodTimeHigh,
                GlucoCraftPeriodTimeVeryLow,
                GlucoCraftPeriodTimeVeryHigh,
            )
        )
        # the range-independent statistics, one entity each; GMI only from 14 days (Battelino et al., 2019), unknown
        # below 70 % data; GRI (on the consensus bands) only from 14 days, as the GMI (statistics.gri_shown)
        statistics.extend(
            cls(coordinator, entry, label)
            for cls in (
                GlucoCraftPeriodMean,
                GlucoCraftPeriodStdDev,
                GlucoCraftPeriodCV,
                GlucoCraftPeriodMin,
                GlucoCraftPeriodMax,
                GlucoCraftPeriodDataAvailability,
                GlucoCraftPeriodReadingCount,
            )
        )
        if PERIOD_SECONDS[label] >= GMI_MIN_PERIOD_SECONDS:
            statistics.append(GlucoCraftPeriodGMI(coordinator, entry, label))
            statistics.append(GlucoCraftPeriodGRI(coordinator, entry, label))

    # Weekly statistics sensor: carries all 26 rolling 7-day weeks in its `weeks` attribute (the weekly summary
    # notification is a separate thing with its own title).
    always.append(GlucoCraftWeeklyStatistics(coordinator, entry))
    always.append(GlucoCraftChangeOverTime(coordinator, entry))
    return {GROUP_ALWAYS: always, GROUP_CUSTOM: custom, GROUP_STATISTICS: statistics}


def split_by_features(
    coordinator: GlucoCraftCoordinator, groups: dict[str, list[SensorEntity]]
) -> tuple[list[SensorEntity], list[SensorEntity]]:
    """Return (the sensors to create, the sensors of an optional feature that is off)."""
    on = {
        GROUP_ALWAYS: True,
        GROUP_CUSTOM: coordinator.custom_ranges_and_goals,
        GROUP_STATISTICS: coordinator.separate_statistics,
    }
    created = [e for group, entities in groups.items() if on[group] for e in entities]
    left_out = [e for group, entities in groups.items() if not on[group] for e in entities]
    return created, left_out


class GlucoCraftBaseEntity(GlucoCraftEntity, SensorEntity):
    """Base of every GlucoCraft sensor (entity.GlucoCraftEntity: unique id, entity id, device)."""

    _platform_domain = "sensor"


# =============================================================================
# Core sensors
# =============================================================================


class GlucoCraftCurrentGlucose(GlucoCraftBaseEntity):
    """Last glucose reading (raw, from the CGM).

    It stays the last reading while there is no current data, so the name says "Last". The unique id key is
    current_glucose; unit_registry finds the entity by it.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION  # Home Assistant converts mg/dL <-> mmol/L
    _attr_native_unit_of_measurement = UnitOfBloodGlucoseConcentration.MILLIGRAMS_PER_DECILITER
    _attr_suggested_display_precision = 0  # whole mg/dL; Home Assistant shows one decimal in mmol/L (unit_registry)
    # the stretch at the sensor's limit: read live by the display card and the panels, never from the history
    _unrecorded_attributes = frozenset({"limit_since", "last_measured_mgdl", "last_measured_ts"})

    def __init__(self, coordinator, entry):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "current_glucose", "last_glucose_reading")

    @property
    def native_value(self) -> float | None:
        """Return the last stored glucose reading (mg/dL; Home Assistant converts it to the display unit); unknown while
        it is a reading at the sensor's limit, so that no 40 or 500 enters the history or the statistics (a reading at
        the limit has no value)."""
        return self.coordinator.measured_glucose

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the zone, its color and word, the display word, mg/dL value, LO / HI flag and the current stretch at
        the sensor's limit."""
        # the zone on the ranges in use and its display color: the card and panels color the value with these
        # instead of working it out themselves, so every display agrees
        # LO / HI: a reading outside the sensor's measurement range has no value: the state is unknown, and every
        # display shows limit_text. value_mgdl then holds the limit it is stored at, only for the panels' own state of
        # the reading; limit marks it as no measurement
        # zone_label: the zone word of a zone outside the range ("Very low" ... "Very high", None in range), for the
        # users' own automations and LED setups (which can for example blink on it)
        c = self.coordinator
        # display_word: the zone word every display shows under the value, in every zone and in every setting;
        # empty without current data
        return {
            "zone": c.glucose_zone,
            "zone_color": c.glucose_zone_color,
            "zone_label": c.glucose_zone_label,
            "display_word": c.display_word,
            # mg/dL for the display card and panels (their histories are mg/dL); "unit" = what to show
            "value_mgdl": c.current_glucose,
            "unit": c.display_unit,
            "limit": c.current_limit if c.current_glucose is not None else None,
            "limit_text": c.current_limit_text,
            # the stretch of readings at the sensor's limit the newest reading belongs to (limit_runs.py): the time of
            # its first reading (epoch seconds; None while the newest reading is measured; a data gap by the gap rule
            # starts a new stretch), and the newest measured reading before it, unrounded mg/dL and its time (outside
            # a stretch: the newest measured reading; None when none is stored). Kept across a restart (the store).
            "limit_since": c.limit_since,
            "last_measured_mgdl": c.last_measured_mgdl,
            "last_measured_ts": c.last_measured_ts,
        }


class GlucoCraftStatus(GlucoCraftBaseEntity):
    """Status bar state, the same on every display.

    In priority order warm_up > expired > no_data > no_current_data > expiring_soon > current, with its label and
    color. It decides the status label and color only: whether the data are current is the No current data binary
    sensor (coordinator.no_current_data), which every display tests; the displays add their own clock (no current data
    when Home Assistant cannot reach them).
    """

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator, entry):
        """Initialize the sensor; its states are keys, which Home Assistant shows by their names in strings.json."""
        super().__init__(coordinator, entry, "status", "status")
        self._attr_options = list(STATUS_COLORS)

    @property
    def native_value(self) -> str:
        """Return the status shown in the status bar."""
        return self.coordinator.display_status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the status label and color.

        Both change only with the status, so the recorder writes a row only when the status changes. The time of the
        last statistics pass is the Weekly statistics sensor's computed_at, which changes with every pass anyway.
        """
        label, color = self.coordinator.display_status_info
        return {"label": label, "color": color}


class GlucoCraftConfiguration(GlucoCraftBaseEntity):
    """Every GlucoCraft setting as attributes, so users can build their own dashboards.

    Sources, ranges, goals and notifications. Change them in Settings > Devices & Services > GlucoCraft > Configure.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC  # every setting: technical
    _attr_device_class = SensorDeviceClass.ENUM
    # about 5 KB of attributes (the settings and the live reading intervals): read live by the dashboard and the cards,
    # never from the history, so the recorder keeps the state only
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(self, coordinator, entry):
        """Initialize the sensor; its state is the ranges in use, a key that Home Assistant shows by its name in
        strings.json ("consensus ranges")."""
        super().__init__(coordinator, entry, "configuration", "configuration")
        self._attr_options = ["consensus_ranges", "custom_ranges"]

    @property
    def native_value(self) -> str:
        """Return "custom_ranges" or "consensus_ranges"."""
        return "custom_ranges" if self.coordinator.uses_custom_ranges() else "consensus_ranges"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return every setting and resolved value (coordinator.configuration)."""
        return self.coordinator.configuration()


def _local_date(ts: float):
    """The local date (Home Assistant's time zone) of an epoch time: the coordinator's one helper."""
    return GlucoCraftCoordinator.local_date(ts)


def _spoken_inputs(c: GlucoCraftCoordinator, now: float) -> tuple:
    """Everything the Spoken summary says: the sentence is made again only when one of these changes. The local date
    is one of them, because the sentence names the day of a reading that was not measured today ("yesterday at")."""
    rg = c.regional
    return (
        _local_date(now),
        c.display_status,
        c.last_reading_ts,
        c.no_current_data,
        c.current_glucose,
        c.current_limit if c.current_glucose is not None else None,
        c.glucose_zone,
        c.trend_description,
        c.display_unit,
        rg.decimal,
        rg.time,
        rg.date,
    )


def spoken_summary_of(c: GlucoCraftCoordinator, now: float) -> str:
    """The Spoken summary sentence of the coordinator's current state at `now` (spoken.spoken_summary)."""
    rg = c.regional
    ts = c.last_reading_ts or 0
    when = reading_when(dt_util.as_local(dt_util.utc_from_timestamp(ts)), _local_date(now), rg) if ts else None
    return spoken_summary(
        status=c.display_status,
        current=bool(ts) and not c.no_current_data,
        mg=c.current_glucose,
        limit=c.current_limit if c.current_glucose is not None else None,
        zone=c.glucose_zone,
        trend=c.trend_description,
        reading_when=when,
        unit=c.display_unit,
        dec=rg.decimal,
    )


class GlucoCraftSpokenSummary(GlucoCraftBaseEntity):
    """Spoken summary: one ready-to-speak sentence about the last reading, for voice assistants and screen readers.

    For example "Glucose 112 milligrams per deciliter, tight range, measured at 7:46 AM, changing slowly." (the
    shared spoken text, spoken.py). Disabled by default: every new state is stored in Home Assistant's recorder, and the
    sentence changes with every reading. No unit and no state class, so it has no long-term statistics.

    The sentence is made again only when the reading, the status, the zone, the trend, a unit or format setting or the
    local date changes, never on the coordinator's minute tick alone, so that the recorder keeps one state per reading
    (and one more at midnight). For the same reason the sentence names the time of the reading, in the time format
    setting, and never its age: a time stays correct however long the state stands. A reading of an earlier day is
    named with its day ("Last reading yesterday at 9:45 PM", "on October 22 at 9:45 PM"; spoken.reading_when).
    """

    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, entry):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "spoken_summary", "spoken_summary")
        self._spoken_key: tuple | None = None
        self._spoken_text = ""

    @property
    def native_value(self) -> str:
        """Return the sentence (at most 255 characters)."""
        now = time.time()
        key = _spoken_inputs(self.coordinator, now)
        if key != self._spoken_key:
            self._spoken_key, self._spoken_text = key, spoken_summary_of(self.coordinator, now)
        return self._spoken_text


class GlucoCraftDelta5Min(GlucoCraftBaseEntity):
    """5-minute delta: the change of the smoothed glucose over 5 minutes, unrounded (each display rounds it once)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION  # Home Assistant converts mg/dL <-> mmol/L
    _attr_native_unit_of_measurement = UnitOfBloodGlucoseConcentration.MILLIGRAMS_PER_DECILITER
    _attr_suggested_display_precision = 0  # whole mg/dL; Home Assistant shows one decimal in mmol/L (unit_registry)

    def __init__(self, coordinator, entry):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "delta_5min", "5_minute_delta")  # sensor.glucocraft_5_minute_delta

    @property
    def native_value(self) -> float | None:
        """Return the 5-minute delta in mg/dL, unrounded, or None while it is unknown."""
        # Home Assistant converts it for mmol/L and shows it with the display precision (whole mg/dL, 0.1 mmol/L)
        return self.coordinator.delta_5min

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the delta in mg/dL (unrounded: the cards and panels round it once) and the display unit."""
        return {"value_mgdl": self.coordinator.delta_5min, "unit": self.coordinator.display_unit}


class GlucoCraftLastReading(GlucoCraftBaseEntity):
    """Newest measurement's own time (the CGM's), from which the displays count the reading's age.

    A reading is a value with its time; this entity holds only the time, so the name says "Time". Its state changes
    with every reading, so the values that change with every reading are its attributes (the epoch time the display
    card and the panels read, the stored reading count and each source's newest measurement time): no other sensor
    changes with every reading because of them, and Home Assistant's recorder writes no further rows for them.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    # read live by the dashboard, the display card and the panels, never from the history: not recorded
    _unrecorded_attributes = frozenset(
        {
            "last_reading_ts",
            "last_reading_mgdl",
            "last_reading_limit",
            "reading_count",
            "primary_last_measurement",
            "secondary_last_measurement",
        }
    )

    def __init__(self, coordinator, entry):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "last_reading", "last_reading_time")

    @property
    def native_value(self):
        """Return the newest reading's measurement time, or None without a reading."""
        ts = self.coordinator.last_reading_ts
        return dt_util.utc_from_timestamp(ts) if ts else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the measurement time as epoch seconds (None without a reading), the same reading's value in mg/dL and
        its LO / HI flag (the panels and the card pair a value with its time from one reading: they compare the value
        they received with it, so that "Last reading" never shows one reading's value with another's time), whether
        that time is ahead of the clock (time_untrusted), the stored reading count and the newest measurement time of
        each source (UTC ISO 8601; None without that source or before its first reading)."""
        c = self.coordinator
        last = c.last_measurements()
        return {
            "last_reading_ts": c.last_reading_ts or None,
            "last_reading_mgdl": c.current_glucose,
            "last_reading_limit": c.current_limit if c.current_glucose is not None else None,
            # true while the newest reading's own time is more than 5 minutes ahead of the clock: its age is not known,
            # and the displays show "Reading: ---" and no time line (the clock notice in Repairs says why)
            "time_untrusted": c.time_untrusted,
            "reading_count": self.coordinator.storage.reading_count,
            "primary_last_measurement": last["primary"],
            "secondary_last_measurement": last["secondary"],
        }


class GlucoCraftTrendArrow(GlucoCraftBaseEntity):
    """Trend arrow, from the five trend categories of Abbott Diabetes Care (2022), the same as the card and panels."""

    # the angle changes with every reading while the arrow stays: live only, not recorded
    _unrecorded_attributes = frozenset({"angle"})

    def __init__(self, coordinator, entry):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "trend_arrow", "trend_arrow")

    @property
    def native_value(self) -> str | None:
        """Return the trend arrow, or None while the trend is unknown."""
        return self.coordinator.trend_arrow or None  # unknown when no trend is known

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the trend category, the trend word and the panel angle."""
        return {
            # the category's English name ("Changing Slowly", "Unknown"), and the trend word as the displays show it in
            # the TREND column with the setting "Trend shown as: word" ("Changing slowly"; empty while unknown)
            "description": self.coordinator.trend_description,
            "word": self.coordinator.trend_word,
            "angle": self.coordinator.trend_angle,
            "source": t("entity.trend_arrow.source"),
        }


class GlucoCraftActiveSource(GlucoCraftBaseEntity):
    """Source in use (primary or second source) and its data state."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC  # which entity delivers the readings: technical
    # no attribute follows the readings (the reading time and the reading count are attributes of the Last reading
    # time sensor), so the recorder writes a row only when the source, its data state or the detected reading interval
    # changes (the interval is detected again every 50 stored readings, coordinator.REDETECT_EVERY_READINGS)

    def __init__(self, coordinator, entry):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "active_source", "active_source")

    @property
    def native_value(self) -> str | None:
        """Return the name of the source in use (the source entity's own name), or None (Home Assistant's unknown)
        until a reading has arrived from a source since the setup (the imported history names no source in use)."""
        return self.coordinator.active_source or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the source entity, the data state and the detected reading interval."""
        return {
            "no_current_data": self.coordinator.no_current_data,
            "source_entity": self.coordinator.active_source_entity or None,
            "second_source_active": self.coordinator.second_source_active,
            "detected_interval": self.coordinator.detected_interval_seconds,
        }


# Data history states: the longest statistics period the stored span (first to last reading) reaches, as keys that
# Home Assistant shows by their names in strings.json ("180 days" ... "6 hours", "Less than 6 hours", "No data"). The
# names use no "Ready" / "Full" / "unlocked" wording, which would read as a claim that the data are usable. The span
# says nothing about the share of time with data (that is sensor active).
DATA_HISTORY_STAGES: tuple[tuple[float, str], ...] = (
    (24 * 180, "180_days"),
    (24 * 90, "90_days"),
    (24 * 30, "30_days"),
    (24 * 14, "14_days"),
    (24 * 7, "7_days"),
    (24, "24_hours"),
    (6, "6_hours"),
)
DATA_HISTORY_NONE, DATA_HISTORY_SHORT = "no_data", "less_than_6_hours"
# The stored readings from which the Data history sensor lists the trend arrow and the 5-minute delta as available: a
# trend and a change need readings before the newest one (GlucoCraft rule; the values themselves: coordinator._adaptive)
TREND_FEATURE_READINGS = 5


class GlucoCraftDataHistory(GlucoCraftBaseEntity):
    """Data history: how long a span of readings is stored (unique id key data_maturity)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC  # the size of GlucoCraft's own store: technical
    _attr_device_class = SensorDeviceClass.ENUM
    # no attribute follows the readings (the span in hours and the reading count would change with every reading while
    # the stage stays, and Home Assistant's recorder writes a row for every change), so the recorder writes a row only
    # when the stage, an available feature or a storage problem changes

    def __init__(self, coordinator, entry):
        """Initialize the sensor (its states: DATA_HISTORY_STAGES and the two below the shortest stage)."""
        super().__init__(coordinator, entry, "data_maturity", "data_history")
        self._attr_options = [DATA_HISTORY_NONE, DATA_HISTORY_SHORT, *(key for _, key in reversed(DATA_HISTORY_STAGES))]

    @property
    def native_value(self) -> str:
        """Return the longest statistics period the stored span of readings reaches."""
        if self.coordinator.storage.reading_count == 0:
            return DATA_HISTORY_NONE
        hours = self._data_hours()
        return next((key for limit, key in DATA_HISTORY_STAGES if hours >= limit), DATA_HISTORY_SHORT)

    def _data_hours(self) -> float:
        """Return the stored span (first to last reading) in hours."""
        readings = self.coordinator.storage.readings
        if len(readings) < 2:
            return 0.0
        span = readings[-1].timestamp - readings[0].timestamp
        return span / 3600.0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the available features and storage problems."""
        hours = self._data_hours()
        storage = self.coordinator.storage
        readings = storage.reading_count
        features = {
            "last_glucose_reading": readings > 0,
            "trend_arrow": readings >= TREND_FEATURE_READINGS,
            "delta": readings >= TREND_FEATURE_READINGS,
            "stats_6h": hours >= 6,
            "stats_24h": hours >= 24,
            "stats_7d": hours >= 24 * 7,
            "stats_14d": hours >= 24 * 14,
            "stats_30d": hours >= 24 * 30,
            "stats_90d": hours >= 24 * 90,
            "stats_180d": hours >= 24 * 180,
            "weekly_statistics": hours >= 24 * 7,
            # the GMI rule itself (>= 14 days with >= 70 % data, statistics.gmi_shown), so this is true when the GMI
            # rule is met for a period of 14 days or longer; a 14-day span alone is not enough
            "gmi": any(
                gmi_shown(self.coordinator.period_stats.get(label), seconds) is not None
                for label, seconds in STAT_PERIODS
                if seconds >= GMI_MIN_PERIOD_SECONDS
            ),
        }
        return {
            "features_available": features,
            "features_available_count": sum(1 for v in features.values() if v),
            "features_total": len(features),
            # a readings file that cannot be written now ("save_failed": the readings since the last good save are kept
            # in memory only), or that was damaged, could not be read or is in a newer version's format at the start
            # ("recovered", "damaged", "unreadable" or "newer"), each also a Repairs notice, and the name of the copy
            # kept aside; None when the file was read and is written normally
            "storage_problem": "save_failed" if storage.save_error else storage.problem,
            "storage_kept_file": storage.kept_file,
        }


# =============================================================================
# Period statistics sensors
# =============================================================================


def _state_pct(value: float | None) -> float | None:
    """Return the state of a percentage entity (CV, sensor active, the custom time-in-band entities and the time in
    range of the period summary sensors): the exact value rounded once to one decimal, halves away from zero, as Home
    Assistant shows it.

    The state changes only when the shown value does, so the state history follows the value shown, and an entity
    whose attributes do not change (the per-statistic entities) writes a recorder row only then; the attributes, the
    files, the cards, the panels and the report keep the exact values (the summary sensors' attributes, which change
    with every statistics pass)."""
    return None if value is None else round_half_up(value, 1)


def _state_glucose(value: float | None, unit: str | None) -> float | None:
    """Return the state of a period's mean, standard deviation, minimum or maximum (mg/dL, the native unit): the exact
    value rounded once to the step Home Assistant shows in the display unit, whole mg/dL, or in mmol/L the mg/dL value
    of the value rounded to 0.1 mmol/L (Home Assistant converts it back with the same factor, 18.016, and shows exactly
    that tenth), halves away from zero.

    The state then changes only when the shown value does, so the recorder writes a row only then, and nothing is
    rounded twice (SYSTEM_DESIGN section 4.3); the summary sensors' attributes, the files, the cards, the panels and
    the report keep the exact values."""
    if value is None:
        return None
    if is_mmol(unit):
        return round(round_half_up(value / MGDL_PER_MMOLL, 1) * MGDL_PER_MMOLL, 4)  # 4 decimals: no float noise
    return round_half_up(value)


class GlucoCraftPeriodBase(GlucoCraftBaseEntity):
    """Base of the period statistic sensors (one set per period of STAT_PERIODS)."""

    def __init__(self, coordinator, entry, period: str, key: str, object_id: str):
        """Initialize a period statistic sensor: unique id key <period>_<key>, translation key period_<key> (the name
        "{period} <statistic>" with the period in capitals, "14D mean"), entity id glucocraft_<period>_<object_id>."""
        super().__init__(
            coordinator, entry, f"{period}_{key}", f"{period}_{object_id}", f"period_{key}", {"period": period.upper()}
        )
        self._period = period

    @property
    def _seconds(self) -> int:
        """The period's length in seconds."""
        return PERIOD_SECONDS[self._period]

    def _get_stats(self):
        """Return the period's statistics on the ranges in use, or None."""
        return self.coordinator.period_stats.get(self._period)

    def _shown(self):
        """Return the period's statistics on the ranges in use while they may be shown (statistics.enough_data: sensor
        active at least 70% of the period, compared before rounding), else None: every statistic's state is then
        unknown. Sensor active and the reading count are not statistics and stay."""
        s = self._get_stats()
        return s if enough_data(s) else None


def _shown_values(s: PeriodStats, values: dict[str, Any]) -> dict[str, Any]:
    """Return a period summary sensor's statistic values, or the same keys with None while the period has not enough
    data (statistics.enough_data), so that a reader finds every key."""
    return values if enough_data(s) else dict.fromkeys(values)


def _coverage(s: PeriodStats, seconds: int) -> dict[str, Any]:
    """Return what a period summary sensor states besides its statistics, with or without enough data: sensor active
    and the enough-data rule's facts (the dashboard works out from period_seconds and covered_seconds how much more data
    the period needs), the readings, and the readings at the sensor's limit with the seconds they represent (a fact, not
    a statistic: they are left out of the mean, SD, CV, GMI, lowest and highest value, counted in the time in each band,
    and stated also without enough data)."""
    return {
        "sensor_active": s.data_availability,
        # at least 70% sensor active, compared before rounding (Battelino et al., 2019, Table 2): 69.96% does not meet
        # it, although it is 70.0% with one decimal
        "sensor_active_ok": s.sensor_active_ok,
        # the enough-data rule (statistics.enough_data): False = every statistic value is None
        "enough_data": enough_data(s),
        "period_seconds": seconds,
        "covered_seconds": s.covered_seconds,  # unrounded; sensor active = covered / period
        "readings": s.reading_count,
        "limit_readings": s.limit_readings,
        "limit_seconds": s.limit_seconds,
        "days": seconds / 86400,
    }


class PeriodStatistic(NamedTuple):
    """A statistic of the period published as its own entity (GlucoCraftPeriodStatistic)."""

    key: str  # the unique id key <period>_<key> and the translation key period_<key>
    object_id: str  # the entity id glucocraft_<period>_<object_id>
    attribute: str  # the PeriodStats value it shows, on the ranges in use


class GlucoCraftPeriodStatistic(GlucoCraftPeriodBase):
    """One statistic of the period on the ranges in use as its own entity, described by its class's `statistic`
    (PeriodStatistic). Its state is unknown without enough data (_shown), and rounded once to the step shown
    (_rounded). The statistics entities below differ only in their PeriodStatistic and in the kind of value: a glucose
    value (_PeriodGlucoseStatistic) or a percentage (_PeriodPercentStatistic)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    statistic: ClassVar[PeriodStatistic]

    def __init__(self, coordinator, entry, period):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, period, self.statistic.key, self.statistic.object_id)

    def _rounded(self, value: float | None) -> float | None:
        """Return the state of the exact value (the kinds of value below)."""
        raise NotImplementedError

    @property
    def native_value(self):
        """Return the period's statistic, rounded once to the step shown."""
        s = self._shown()
        return self._rounded(getattr(s, self.statistic.attribute)) if s else None


class _PeriodGlucoseStatistic(GlucoCraftPeriodStatistic):
    """A glucose statistic of the period (mean, standard deviation, lowest and highest reading) in mg/dL."""

    _attr_device_class = SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION  # Home Assistant converts mg/dL <-> mmol/L
    _attr_native_unit_of_measurement = UnitOfBloodGlucoseConcentration.MILLIGRAMS_PER_DECILITER
    _attr_suggested_display_precision = 0  # whole mg/dL; Home Assistant shows one decimal in mmol/L (unit_registry)

    def _rounded(self, value: float | None) -> float | None:
        """Return the value rounded once to the step shown in the display unit (_state_glucose)."""
        return _state_glucose(value, self.coordinator.display_unit)


class _PeriodPercentStatistic(GlucoCraftPeriodStatistic):
    """A percentage statistic of the period (the CV and the custom time-in-band values)."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1  # the state is rounded once to one decimal (_state_pct)

    def _rounded(self, value: float | None) -> float | None:
        """Return the value rounded once to one decimal (_state_pct)."""
        return _state_pct(value)


class GlucoCraftPeriodMean(_PeriodGlucoseStatistic):
    """Mean glucose of the period, on the ranges in use."""

    statistic = PeriodStatistic("mean", "mean", "mean")


class GlucoCraftPeriodStdDev(_PeriodGlucoseStatistic):
    """Standard deviation of the glucose readings of the period."""

    statistic = PeriodStatistic("std_dev", "standard_deviation", "std_dev")


class GlucoCraftPeriodCV(_PeriodPercentStatistic):
    """Coefficient of variation of the period (%)."""

    statistic = PeriodStatistic("cv", "cv", "cv")


class GlucoCraftPeriodGMI(GlucoCraftPeriodBase):
    """GMI of the period, shown only under the 14-day / 70 % rule (statistics.gmi_shown)."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1  # one decimal: statistics.py rounds the GMI once (PeriodStats.gmi)
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, period):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, period, "gmi", "gmi")

    @property
    def native_value(self):
        """Return the period's GMI, or None when the period does not meet the rule."""
        seconds = self._seconds
        return gmi_shown(self._get_stats(), seconds)


class GlucoCraftPeriodConsensus(GlucoCraftPeriodBase):
    """Period on the consensus ranges: state = time 70-180 %, attributes = every consensus value.

    The same as the other period sensors when the ranges in use are the consensus ranges. The name says "consensus
    ranges" because "consensus" alone refers to the consensus statements; the unique id key is <period>_consensus.
    """

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1  # the state is rounded once to one decimal (_state_pct)
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, period):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, period, "consensus", "consensus_ranges")

    def _agp(self):
        """Return the period's statistics on the consensus ranges, or None."""
        return self.coordinator.period_stats_agp.get(self._period)

    @property
    def native_value(self):
        """Return the period's time in range 70-180 mg/dL, rounded once to one decimal (the attributes stay exact)."""
        s = self._agp()
        return _state_pct(s.pct_time_in_target_range) if enough_data(s) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return every value of the period on the consensus ranges; without enough data (statistics.enough_data) the
        same keys with None, and enough_data False. has_data: the period has readings."""
        s = self._agp()
        if s is None:
            return {"has_data": False}
        seconds = self._seconds
        gmi = gmi_shown(s, seconds)
        gri = gri_shown(s, seconds)  # 14 days or more, with enough data, as the GMI
        values = {
            "time_70_180": s.pct_time_in_target_range,
            "time_70_140": s.pct_time_in_ideal_range,
            # the combined bands (level 1 and level 2 together), as the consensus targets apply to them
            "time_below_70": s.time_below_low,
            "time_below_54": s.time_critical_low,
            "time_54_69": s.time_low,
            "time_181_250": s.time_high,
            "time_above_180": s.time_above_high,
            "time_above_250": s.time_critical_high,
            # the same combined bands under the names of PeriodStats (all time below the low limit / above the high
            # limit, level 1 and level 2 together; here the consensus limits 70 and 180 mg/dL)
            "time_below_low": s.time_below_low,
            "time_above_high": s.time_above_high,
            "mean": s.mean,
            "std_dev": s.std_dev,
            "cv": s.cv,
            "gmi": gmi,
            # GMI in mmol/mol from the unrounded mean (Bergenstal et al., 2018), only where the GMI is shown
            "gmi_mmol_mol": s.gmi_mmol_mol if gmi is not None else None,
            "gri": gri,
            "gri_zone": s.gri_zone if gri is not None else None,
            "gri_hypo": s.gri_hypo if gri is not None else None,
            "gri_hyper": s.gri_hyper if gri is not None else None,
            "min_value": s.min_value,
            "max_value": s.max_value,
        }
        return {
            "has_data": True,
            **_shown_values(s, values),
            **_coverage(s, seconds),
            "source": t("entity.consensus_ranges.source"),
        }


class GlucoCraftPeriodCustom(GlucoCraftPeriodBase):
    """Period on the custom ranges: state = time in range on the ranges in the settings, attributes = every value of
    the period on them (only while Custom ranges and goals is on).

    The counterpart of <period> consensus ranges, with the band values under the names of the ranges in the settings
    (time_in_range, time_low, ...): GlucoCraft's Custom Ranges page and the wall panels read the custom results here,
    so that they never depend on the entities of Separate statistics entities. The range-independent values (mean,
    standard deviation, CV, GMI, lowest and highest reading, sensor active, readings) equal those of the consensus
    sensor; the GRI is on the consensus sensor only (it is defined on the consensus bands). The unique id key is
    <period>_custom.
    """

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1  # the state is rounded once to one decimal (_state_pct)
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, period):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, period, "custom", "custom_ranges")

    @property
    def native_value(self):
        """Return the period's time in range on the custom ranges, rounded once to one decimal (the attributes stay
        exact)."""
        s = self._shown()
        return _state_pct(s.pct_time_in_target_range) if s else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return every value of the period on the custom ranges (glucose values in mg/dL); without enough data
        (statistics.enough_data) the same keys with None, and enough_data False. has_data: the period has readings."""
        s = self._get_stats()
        if s is None:
            return {"has_data": False}
        seconds = self._seconds
        gmi = gmi_shown(s, seconds)
        values = {
            "time_in_range": s.pct_time_in_target_range,
            "time_in_tight_range": s.pct_time_in_ideal_range,
            "time_very_low": s.time_critical_low,
            "time_low": s.time_low,
            "time_high": s.time_high,
            "time_very_high": s.time_critical_high,
            # all time below the low limit / above the high limit (level 1 and level 2 together), as the low and high
            # goals apply to them
            "time_below_low": s.time_below_low,
            "time_above_high": s.time_above_high,
            "mean": s.mean,
            "std_dev": s.std_dev,
            "cv": s.cv,
            "gmi": gmi,
            # GMI in mmol/mol from the unrounded mean (Bergenstal et al., 2018), only where the GMI is shown
            "gmi_mmol_mol": s.gmi_mmol_mol if gmi is not None else None,
            "min_value": s.min_value,
            "max_value": s.max_value,
        }
        return {"has_data": True, **_shown_values(s, values), **_coverage(s, seconds)}


class GlucoCraftPeriodTIR(_PeriodPercentStatistic):
    """Time in range of the period, on the custom ranges.

    The six time-in-band sensors use the ranges in the settings (the custom ranges), so their names say "Custom"; the
    consensus values are on the <period> consensus ranges sensors. They exist only while Custom ranges and goals is on,
    as does the <period> custom ranges sensor, which carries the same values.
    """

    statistic = PeriodStatistic("tir", "custom_time_in_range", "pct_time_in_target_range")


class GlucoCraftPeriodTITR(_PeriodPercentStatistic):
    """Time in tight range of the period, on the custom ranges."""

    statistic = PeriodStatistic("titr", "custom_time_in_tight_range", "pct_time_in_ideal_range")


class GlucoCraftPeriodTimeLow(_PeriodPercentStatistic):
    """Time low (level 1) of the period, on the custom ranges."""

    statistic = PeriodStatistic("time_low", "custom_time_low", "time_low")


class GlucoCraftPeriodTimeHigh(_PeriodPercentStatistic):
    """Time high (level 1) of the period, on the custom ranges."""

    statistic = PeriodStatistic("time_high", "custom_time_high", "time_high")


class GlucoCraftPeriodTimeVeryLow(_PeriodPercentStatistic):
    """Time very low (level 2) of the period, on the custom ranges."""

    statistic = PeriodStatistic("time_critical_low", "custom_time_very_low", "time_critical_low")


class GlucoCraftPeriodTimeVeryHigh(_PeriodPercentStatistic):
    """Time very high (level 2) of the period, on the custom ranges."""

    statistic = PeriodStatistic("time_critical_high", "custom_time_very_high", "time_critical_high")


class GlucoCraftPeriodMin(_PeriodGlucoseStatistic):
    """Lowest measured reading of the period (a reading at the sensor's limit has no value)."""

    statistic = PeriodStatistic("min", "minimum", "min_value")


class GlucoCraftPeriodMax(_PeriodGlucoseStatistic):
    """Highest measured reading of the period (a reading at the sensor's limit has no value)."""

    statistic = PeriodStatistic("max", "maximum", "max_value")


class GlucoCraftPeriodDataAvailability(GlucoCraftPeriodBase):
    """Sensor active: the share of the period with data received by GlucoCraft (the gap rule).

    It is not the time the CGM sensor was worn. The attributes carry the label and the sentence shown wherever the
    percentage is shown.
    """

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1  # the state is rounded once to one decimal (_state_pct)
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC  # how complete the period's data are, not a glucose value
    _unrecorded_attributes = frozenset({"label", "note"})  # fixed texts: not recorded

    def __init__(self, coordinator, entry, period):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, period, "data_availability", "sensor_active")

    @property
    def native_value(self):
        """Return the period's sensor active percentage."""
        s = self._get_stats()
        return _state_pct(s.data_availability) if s else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the sensor-active label and the sentence that explains it."""
        return {"label": sensor_active_label(), "note": sensor_active_note()}


class GlucoCraftPeriodReadingCount(GlucoCraftPeriodBase):
    """Number of readings in the period."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC  # a count of stored readings, not a glucose value

    def __init__(self, coordinator, entry, period):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, period, "readings", "readings")

    @property
    def native_value(self):
        """Return the number of readings in the period."""
        s = self._get_stats()
        return s.reading_count if s else None


class GlucoCraftPeriodGRI(GlucoCraftPeriodBase):
    """Glycemia Risk Index (Klonoff et al., 2023) of the period, on the consensus bands.

    A composite score (0-100) of the low and high components, with zones A to E, the quintiles of the GRI
    (statistics.gri_zone). Always on the consensus bands (54 / 70 / 180 / 250 mg/dL), whatever the ranges
    in use, because its weights were fitted to them. Only for the periods of 14 days or more with enough data, as the
    GMI (statistics.gri_shown).
    """

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, period):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, period, "gri", "gri")

    def _get_stats(self):
        """Return the period's statistics on the consensus ranges, or None."""
        return self.coordinator.period_stats_agp.get(self._period)

    @property
    def native_value(self):
        """Return the period's GRI on the consensus ranges (statistics.gri_shown: 14 days or more, enough data)."""
        return gri_shown(self._get_stats(), self._seconds)

    @property
    def extra_state_attributes(self):
        """Return the GRI components, the zone and the source; nothing while the GRI is not shown (gri_shown)."""
        s = self._get_stats()
        if gri_shown(s, self._seconds) is None:
            return {}
        return {
            "gri_hypo": s.gri_hypo,
            "gri_hyper": s.gri_hyper,
            "gri_zone": s.gri_zone,  # the zone of the displayed (rounded) GRI, as on every other surface
            "source": "Klonoff et al., 2023",
        }


# =============================================================================
# Weekly statistics and Change Over Time
# =============================================================================


class GlucoCraftWeeklyStatistics(GlucoCraftBaseEntity):
    """Weekly statistics: all 26 weeks as attributes for charting (unique id key weekly_summary)."""

    # the 26 weeks end at the time of the statistics pass, so they and computed_at change with every pass: live only,
    # not recorded
    _unrecorded_attributes = frozenset({"weeks", "computed_at"})

    def __init__(self, coordinator, entry):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "weekly_summary", "weekly_statistics")

    def _has_data(self, week) -> bool:
        """Return True when a week has enough data (statistics.enough_data: sensor active at least 70% of the week, as
        the trend pages and change.py; the covered time by the gap rule, gaps.py)."""
        return enough_data(week)

    @property
    def native_value(self) -> str:
        """Return the number of weeks with data, as "<n> weeks"."""
        trends = self.coordinator.weekly_trends
        valid = sum(1 for week in trends if self._has_data(week))
        return tp("entity.weekly_statistics.state", valid)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the 26 weeks, newest first, each trend page's direction and the time the weeks were calculated.

        Each week has its dates, the values on GlucoCraft's ranges, and under "consensus" the AGP-band values the trend
        pages show (combined below 70 / above 180). GMI: the 14-day GMI at the week's end, only with >= 70 % data
        (Battelino et al., 2019; never a 7-day GMI). GRI: the 14-day GRI at the week's end, on the consensus bands
        (never a 7-day GRI). A week without enough data has has_data false (the trend pages show a gap).
        """
        c = self.coordinator
        trends = c.weekly_trends
        agp = c.weekly_trends_agp
        at = c.weekly_trends_at
        weeks = []
        for i, week in enumerate(trends):
            dates = {}
            if at:
                dates = {
                    "start": dt_util.utc_from_timestamp(at - (i + 1) * 7 * 86400).isoformat(),
                    "end": dt_util.utc_from_timestamp(at - i * 7 * 86400).isoformat(),
                }
            if not self._has_data(week):
                weeks.append({**dates, "has_data": False})
                continue
            gmi14 = c.trend_gmi14[i] if i < len(c.trend_gmi14) else None  # the 14-day / 70 % rule
            gri14 = c.trend_gri14[i] if i < len(c.trend_gri14) else None  # the 14-day GRI (consensus bands)
            a = agp[i] if i < len(agp) else None
            consensus = (
                None
                if a is None
                else {
                    "time_70_180": a.pct_time_in_target_range,
                    "time_70_140": a.pct_time_in_ideal_range,
                    "time_below_70": a.time_below_low,
                    "time_below_54": a.time_critical_low,
                    "time_above_180": a.time_above_high,
                    "time_below_low": a.time_below_low,
                    "time_above_high": a.time_above_high,
                    "time_above_250": a.time_critical_high,
                    "mean": a.mean,
                    "std_dev": a.std_dev,
                    "cv": a.cv,
                    "gmi": gmi14,  # the 14-day GMI at the week's end
                    "min_value": a.min_value,
                    "max_value": a.max_value,
                    "sensor_active": a.data_availability,
                    "gri": gri14,  # the 14-day GRI at the week's end
                }
            )
            weeks.append(
                {
                    **dates,
                    "has_data": True,
                    "consensus": consensus,
                    # on GlucoCraft's ranges (custom when set):
                    "mean": week.mean,
                    "std_dev": week.std_dev,
                    "cv": week.cv,
                    "time_in_range": week.pct_time_in_target_range,
                    "time_in_tight_range": week.pct_time_in_ideal_range,
                    "gmi": gmi14,  # the 14-day GMI at the week's end (GMI needs 14 days with >= 70 % data)
                    "time_low": week.time_low,
                    "time_high": week.time_high,
                    # all time below the low limit / above the high limit of the settings (level 1 and 2 together)
                    "time_below_low": week.time_below_low,
                    "time_above_high": week.time_above_high,
                    "min_value": week.min_value,
                    "max_value": week.max_value,
                    "reading_count": week.reading_count,
                    "gri": gri14,  # the 14-day GRI at the week's end, on the consensus bands whatever the ranges
                }
            )
        return {
            "weeks": weeks,
            # each trend page's direction as the trend pages show it (Increasing / Decreasing / Stable; None: few weeks)
            "directions": dict(c.trend_directions),
            # the time of the statistics pass that calculated the weeks; every period statistic is calculated in the
            # same pass, so the dashboard shows it as the time the statistics were calculated (None before the first)
            "computed_at": dt_util.utc_from_timestamp(at).replace(microsecond=0).isoformat() if at else None,
        }


class GlucoCraftChangeOverTime(GlucoCraftBaseEntity):
    """Change over time, for the consensus ranges and for the ranges in the settings (change.py).

    The 14 days ending at each of the last 26 week ends, the 7-day values of the same weeks, and the last 4 weeks /
    9-13 / 22-26 weeks ago. Calculated hourly. State: the number of 14-day windows with at least 70 % sensor active.
    """

    _unrecorded_attributes = frozenset({"consensus", "custom", "computed_at", "custom_is_consensus"})

    def __init__(self, coordinator, entry):
        """Initialize the sensor."""
        super().__init__(coordinator, entry, "change_over_time", "change_over_time")

    @property
    def native_value(self) -> str | None:
        """Return "<n> of 26": the 14-day windows with at least 70 % sensor active, compared before rounding."""
        consensus = self.coordinator.change.get("consensus")
        weeks = consensus.weeks14 if consensus else []
        if not weeks:
            return None
        active = sum(1 for w in weeks if w and w.get("active_ok"))
        return t("entity.change_over_time.state", count=active, total=len(weeks))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the windows, the weekly values and the comparisons for both range sets."""
        c = self.coordinator
        if not c.change:
            return {}
        out: dict[str, Any] = {"computed_at": dt_util.utc_from_timestamp(c.change_at).isoformat()}
        # attribute "custom" = the ranges in the settings (the user-facing term); the internal key of
        # coordinator.change is "personal"
        for key, internal in (("consensus", "consensus"), ("custom", "personal")):
            d = c.change.get(internal) or ChangeOverTime()
            out[key] = {
                "week_ends": [dt_util.utc_from_timestamp(end).isoformat() for end in d.week_ends],
                "weeks14": d.weeks14,
                "weeks7": d.weeks7,
                "compare": d.compare,
            }
        out["custom_is_consensus"] = c.change.get("personal") is c.change.get("consensus")
        return out


class GlucoCraftSensorExpiry(GlucoCraftBaseEntity):
    """CGM sensor expiry, from the optional expiry entity the user picks in setup or options.

    The entity may report any of the forms parse_expiry() understands, or nothing (none picked). It is never
    guessed from data gaps or a sensor model.

    States (keys; Home Assistant shows them by their names in strings.json):
    - not_configured (Not configured): no expiry entity picked
    - no_value (No value): the entity has no value right now
    - unknown_format (Unknown format): the entity reports something parse_expiry() does not understand (raw value in
      expiry_value)
    - not_expired (Not expired, more than 24 h left) / expiring_soon (Expiring soon, 24 h or less, a GlucoCraft rule:
      const.EXPIRING_SOON_MINUTES, as the status) / expired (Expired). "Not expired" states only the expiry, not that
      the CGM sensor is working (there can be no current data at the same time).
    Attribute expires_at = the expiry in UTC ISO form (None when unknown): what the panels and cards read; attribute
    expiry_entity = the entity picked (None when none is), expiry_format = the code of its form (expiry.parse_expiry,
    not_configured when none is picked).
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC  # the state of the CGM sensor (the device), not a glucose value
    _attr_device_class = SensorDeviceClass.ENUM
    # the times left and the expiry entity's own value (often a countdown) change with every minute tick or countdown
    # step while the state stays: live only, not recorded
    _unrecorded_attributes = frozenset({"remaining_hours", "remaining_days", "expiry_value"})

    def __init__(self, coordinator, entry):
        """Initialize the sensor (its states: see above)."""
        super().__init__(coordinator, entry, "sensor_expiry", "sensor_expiry")
        self._attr_options = ["not_configured", "no_value", "unknown_format", "not_expired", "expiring_soon", "expired"]

    @property
    def native_value(self) -> str:
        """Return the expiry state (not_configured, no_value, unknown_format, not_expired, ...)."""
        if not self.coordinator.expiry_entity:
            return "not_configured"
        expiry, fmt = self.coordinator.expiry()
        if expiry is None:
            return "unknown_format" if fmt == "unrecognized" else "no_value"
        seconds = (expiry - dt_util.utcnow()).total_seconds()
        if seconds <= 0:
            return "expired"
        if seconds <= EXPIRING_SOON_MINUTES * 60:
            return "expiring_soon"
        return "not_expired"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the expiry entity, its format, the expiry time and the time left."""
        entity = self.coordinator.expiry_entity
        expiry, fmt = self.coordinator.expiry()
        attrs: dict[str, Any] = {
            "expiry_entity": entity,  # None: no expiry entity picked
            "expiry_format": fmt,
            "expires_at": expiry.replace(microsecond=0).isoformat() if expiry else None,
        }
        if entity:
            st = self.coordinator.hass.states.get(entity)
            if st is not None:
                attrs["expiry_value"] = st.state
        if expiry is not None:
            left = (expiry - dt_util.utcnow()).total_seconds()
            attrs["remaining_hours"] = round_half_up(left / 3600, 1)
            attrs["remaining_days"] = round_half_up(left / 86400, 1)
        return attrs
