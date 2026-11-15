# License: AGPL-3.0-only (LICENSE).
"""Constants of the GlucoCraft integration: setting keys and defaults, the display rules, the storage format, the
disclaimer and the license notices."""

from __future__ import annotations

import operator
import re
from typing import Any

from .i18n import t

DOMAIN = "glucocraft"
PLATFORMS = ["binary_sensor", "sensor"]
# The config entry's format (config_flow.GlucoCraftConfigFlow.VERSION / MINOR_VERSION): 2.3 is the first format of
# the published GlucoCraft; a later change of the stored settings raises it and adds a step to
# __init__.async_migrate_entry
ENTRY_VERSION = 2
ENTRY_MINOR_VERSION = 3
# Repairs notices of a failed start step (the texts are in strings.json "issues"); each closes at the next start at
# which the step succeeds and does not outlast a restart of Home Assistant (not persistent)
RESOURCES_ISSUE_ID = "lovelace_resources_failed"  # the cards could not be added as dashboard resources (frontend.py)
HISTORY_IMPORT_ISSUE_ID = "history_import_failed"  # the source's recorded history could not be read (history_import)
DASHBOARD_ISSUE_ID = "dashboard_failed"  # the GlucoCraft dashboard could not be registered (strategy_dashboard.py)

# Config flow keys
CONF_GLUCOSE_ENTITY = "glucose_entity"
# An optional second source, which fills only gaps in the primary's readings; an optional per-source attribute that
# holds the CGM's own measurement time (blank = auto-detect)
CONF_SECONDARY_ENTITY = "secondary_glucose_entity"
CONF_PRIMARY_TIME_ATTR = "primary_time_attribute"
CONF_SECONDARY_TIME_ATTR = "secondary_time_attribute"
# Attributes known to carry the measurement time: LibreLink/LibreView (measurement_timestamp), Nightscout (dateString)
SOURCE_TIME_ATTRIBUTES = ("measurement_timestamp", "dateString", "date", "timestamp", "last_reading", "reading_time")
CONF_STALE_THRESHOLD = "stale_threshold"
CONF_UNIT = "unit"
# time and date formats (regional.py): "auto" = from Home Assistant's country
CONF_TIME_FORMAT = "time_format"
# the display card on the dashboard: square (480 x 480 panels) or rectangular (800 x 480)
CONF_DISPLAY_CARD = "display_card"
DISPLAY_CARD_SQUARE = "square"
DISPLAY_CARD_RECTANGULAR = "rectangular"
CONF_DATE_FORMAT = "date_format"
# The Date format choices as stored and as translation keys (lower case, as Home Assistant requires for selector
# options) and the codes the cards, the panels and regional.py use ("MDY" / "DMY" / "YMD").
DATE_FORMAT_OPTIONS = ("auto", "mdy", "dmy", "ymd")


def date_format_code(stored: Any) -> str:
    """Return the Date format setting as the code regional.py and every display read: "MDY", "DMY", "YMD" or "auto"."""
    text = str(stored or "auto").strip().lower()
    return text.upper() if text in DATE_FORMAT_OPTIONS[1:] else "auto"


# regional settings (regional.py): each "auto" (from Home Assistant's country / language) or a choice;
# configuration() publishes the resolved value and the setting (*_setting)
CONF_FIRST_WEEKDAY = "first_weekday"  # auto / monday / sunday / saturday
CONF_DECIMAL_SEPARATOR = "decimal_separator"  # auto / point / comma
CONF_A1C_UNIT = "a1c_unit"  # auto / percent / mmol_mol / both
CONF_PAPER_SIZE = "paper_size"  # auto / letter / a4
REGIONAL_SETTINGS = (
    CONF_TIME_FORMAT,
    CONF_DATE_FORMAT,
    CONF_FIRST_WEEKDAY,
    CONF_DECIMAL_SEPARATOR,
    CONF_A1C_UNIT,
    CONF_PAPER_SIZE,
)
CONF_NOTIFY_ENABLED = "notify_enabled"
CONF_NOTIFY_TIME = "notify_time"
CONF_SHOW_DASHBOARD = "show_dashboard"
CONF_SENSOR_EXPIRY_ENTITY = "sensor_expiry_entity"
CONF_PANEL_WEBHOOK_ID = "panel_webhook_id"  # GET-only, local-only webhook serving the panels' GLU2 files
CONF_REGENERATE_PANEL_WEBHOOK = "regenerate_panel_webhook"  # options flow: make a new panel address
# True once the user created a wall-panel address in the settings: the address (and its notification) exists only
# when wall panels are used, never at the first setup. An entry that already has an address keeps it.
CONF_PANEL_FEED = "panel_feed"
# The two optional features (Configure > Optional features), each off in a new installation (settings.py):
# - Custom ranges and goals: the ranges and goals in the settings are used, and the Custom Ranges page, the CGM Report's
#   custom appendix and the custom entities exist; off, the consensus ranges and targets are used everywhere and the
#   stored ranges and goals are kept unused;
# - Separate statistics entities: one entity per range-independent statistic and period (mean, standard deviation,
#   CV, GMI, GRI, minimum, maximum, readings, sensor active); GlucoCraft's own pages and panels never need them.
CONF_CUSTOM_RANGES_AND_GOALS = "custom_ranges_and_goals"
CONF_SEPARATE_STATISTICS = "separate_statistics"

# Goal target config keys
CONF_GOAL_TIME_IN_TARGET_RANGE = "goal_time_in_target_range"
CONF_GOAL_TIME_IN_IDEAL_RANGE = "goal_time_in_ideal_range"
CONF_GOAL_TBR = "goal_tbr"
CONF_GOAL_TBR_CRITICAL = "goal_tbr_critical"
CONF_GOAL_TAR = "goal_tar"
CONF_GOAL_TAR_CRITICAL = "goal_tar_critical"
CONF_GOAL_CV = "goal_cv"
CONF_GOAL_GMI = "goal_gmi"

# Default goals = the consensus targets. GlucoCraft sets no targets of its own, so the goals without a published
# target start empty ("no goal set"): time in tight range and GMI (the ADA's A1C goal is not a GMI goal). Time in
# range: more than 70 %, as the consensus target says.
DEFAULT_GOAL_TIME_IN_TARGET_RANGE = 70  # >70% (Battelino et al., 2019)
DEFAULT_GOAL_TIME_IN_IDEAL_RANGE = None  # no published target: no goal set until the user enters one
DEFAULT_GOAL_TBR = 4  # <4% (Battelino et al., 2019)
DEFAULT_GOAL_TBR_CRITICAL = 1  # <1% (Battelino et al., 2019)
DEFAULT_GOAL_TAR = 25  # <25% (Battelino et al., 2019)
DEFAULT_GOAL_TAR_CRITICAL = 5  # <5% (Battelino et al., 2019)
DEFAULT_GOAL_CV = 36  # at most 36% (Battelino et al., 2019; Monnier et al., 2017)
DEFAULT_GOAL_GMI = None  # no published GMI target: no goal set until the user enters one

# Retention: 200 days, fixed (there is no setting), dropped as whole local days at midnight. It covers the 6-month
# report (180 days), the 90-day report with its previous period (180 days), the weekly trends (26 weeks of 14-day
# values: the oldest window starts 25 x 7 + 14 = 189 days back; change.py) and Change Over Time. A previous period for
# the 6-month report would need about 360 days (2 x 180), so that report has none.
DEFAULT_RETENTION_DAYS = 200

# The sensor's measurement range per source (limits.py): "auto" or a value in mg/dL. Stored as the
# select's text ("auto", "40", ...); missing = "auto".
CONF_SENSOR_LOWER_LIMIT = "sensor_lower_limit"  # primary source
CONF_SENSOR_UPPER_LIMIT = "sensor_upper_limit"
CONF_SECONDARY_SENSOR_LOWER_LIMIT = "secondary_sensor_lower_limit"  # secondary source
CONF_SECONDARY_SENSOR_UPPER_LIMIT = "secondary_sensor_upper_limit"
SENSOR_LOWER_LIMIT_OPTIONS = ["auto", "40", "50"]
SENSOR_UPPER_LIMIT_OPTIONS = ["auto", "400", "450", "500"]

# The gap rule (gaps.py): a gap is an interval longer than max(15 minutes, 3 x the reading interval of that
# stretch); the reading interval = the median of the 10 intervals around it
GAP_FLOOR_SECONDS = 900
GAP_INTERVAL_FACTOR = 3
INTERVAL_WINDOW = 10
# The reading interval a calculation uses when its caller passes none (0 or None): one minute, the shortest reading
# interval of current CGMs, so that a reading whose interval cannot be found from its neighbours stands for the least
# time a real reading can (gaps.local_intervals, gaps.weights), never more. Change Over Time, the /data API and the CGM
# Report's modules use it. It differs on purpose from storage.DEFAULT_INTERVAL_SECONDS (5 minutes), the interval a
# store starts with until one is detected: the coordinator always passes that one or the detected interval, so this value
# only guards a call without an interval.
FALLBACK_INTERVAL_SECONDS = 60
# Reading-interval notice: GlucoCraft is designed for readings every 5 minutes or more often; a Repairs notice
# appears when a source updates every 15 minutes or less often
SLOW_SOURCE_SECONDS = 900

# CSV export: <config>/glucocraft_exports/, the last 12 exports kept
EXPORT_DIR = "glucocraft_exports"
EXPORT_KEEP = 12

# Range setting keys (mg/dL). The keys "critical" and "tight" are the stored names; users see "very low / very high"
# and "tight range" (PUBLIC_KEYS)
CONF_RANGE_LOW = "range_low"
CONF_RANGE_HIGH = "range_high"
CONF_RANGE_CRITICAL_LOW = "range_critical_low"
CONF_RANGE_CRITICAL_HIGH = "range_critical_high"
CONF_RANGE_TIGHT_LOW = "range_tight_low"
CONF_RANGE_TIGHT_HIGH = "range_tight_high"


# Default range thresholds (mg/dL: every range is stored in mg/dL)
DEFAULT_RANGE_CRITICAL_LOW = 54
DEFAULT_RANGE_LOW = 70
DEFAULT_RANGE_HIGH = 180
DEFAULT_RANGE_CRITICAL_HIGH = 250
DEFAULT_RANGE_TIGHT_LOW = 70
DEFAULT_RANGE_TIGHT_HIGH = 140

# Default no-current-data limit (minutes): a reading older than this is not current data (the setting: 5 to 30
# minutes, settings.STALE_THRESHOLD_LIMITS)
DEFAULT_STALE_THRESHOLD = 15

# Trend: the five bands of the trend categories of Abbott Diabetes Care (2022, Get started with the FreeStyle Libre 3
# system, ADC-55381 v1.0, p. 11): "rising/falling quickly" > 2 mg/dL/min, "rising/falling" 1-2, "changing slowly" < 1.
# On the rate rounded to 0.1 (the delta sensor's value), so the entities, the card and the panels always say the same
# thing. The bounds sit on that 0.1 grid (a band starts where the rate >= its bound): a bound halfway between two steps
# (x.x5) leaves the step below it out of the band, a bound on a step takes that step in. So 2.05 makes "> 2" (2.0 is
# Rising, 2.1 Rising Quickly) and -0.95 puts -1.0 in Falling; do not "tidy" 2.05 to 2.0 or -0.95 to -1.0, which would
# change the categories.
TREND_LIBRE_BANDS = (  # (lower bound in mg/dL/min, word, arrow) - first match from the top
    (2.05, "Rising Quickly", "↑"),
    (1.0, "Rising", "↗"),
    (-0.95, "Changing Slowly", "→"),
    (-2.0, "Falling", "↘"),
    (float("-inf"), "Falling Quickly", "↓"),
)
# The key of each trend word (the catalogue's spoken.trend.<key> and trend.word.<key>); the words themselves stay the
# English category names of the source above, the value of the trend attribute "description" that cards and panels read
TREND_KEYS = {word: word.lower().replace(" ", "_") for _, word, _ in TREND_LIBRE_BANDS}


# ONE SYSTEM: GlucoCraft decides what the displays show; card, panels and users' own dashboards read these from the
# entities / files instead of working them out themselves (docs/DISPLAY_CONTRACT.md).
# Glucose zones on the ranges in use, with the displays' colors: seven zones, named in the consensus terms (very low,
# low, in range below the tight range, tight range, in range above the tight range, high, very high)
# The default display palette: lows in red, highs in blue, the in-range zones amber and green. Users can choose their
# own color for each zone (the options CONF_ZONE_COLORS below); the colors in use are published as the configuration
# sensor's attribute "zone_colors" and as "zone_color" on the Last glucose reading sensor, which every card and panel
# reads instead of a color of its own.
ZONE_COLORS = {
    "very_low": "#C62828",
    "low": "#EF5350",
    "near_low": "#FFC107",
    "tight_range": "#4CAF50",
    "near_high": "#FFC107",
    "high": "#42A5F5",
    "very_high": "#1565C0",
}
ZONES = tuple(ZONE_COLORS)  # the seven zones, from very low to very high
# The option key of each zone's color ("color_very_low" ... "color_very_high"), stored as "#RRGGBB" text
CONF_ZONE_COLORS = {zone: f"color_{zone}" for zone in ZONES}
# The graph range colors: a second set of seven colors, used only for the range bands of the graphs (drawn at 60 % over
# the graphs' black area). The default is the time-in-ranges palette of the CGM Report (report_pdf.AGP is built from
# this table, so that the report and the graphs share one source); both in-range zones outside the tight range share
# one color, as in the report. Users can choose their own (the options CONF_BAND_COLORS, "band_color_very_low" ...,
# "#RRGGBB"; absent = the default); the colors in use are published as the configuration sensor's attribute
# "band_colors" (settings.band_colors validates them).
BAND_COLORS = {
    "very_low": "#9B1C1F",
    "low": "#E5352B",
    "near_low": "#1F8F45",
    "tight_range": "#52B964",
    "near_high": "#1F8F45",
    "high": "#FCC21B",
    "very_high": "#F08A4B",
}
CONF_BAND_COLORS = {zone: f"band_color_{zone}" for zone in ZONES}
# The backgrounds: the display card's ("theme" = the Home Assistant theme, or "#RRGGBB") and the LCD wall panels'
# ("#RRGGBB"); both black by default. The text on them is black or white, whichever contrasts more (contrast.py,
# published as card_foreground / panel_foreground); the graphs keep their black area on every background.
CONF_CARD_BACKGROUND = "card_background"
CONF_PANEL_BACKGROUND = "panel_background"
CARD_BACKGROUND_THEME = "theme"
DEFAULT_CARD_BACKGROUND = "#000000"
DEFAULT_PANEL_BACKGROUND = "#000000"
# Outline text (display card and LCD wall panels; off by default): a 1-pixel edge around the graph labels, the glucose
# value, LO / HI and every other text drawn in a zone color, in black for a light text and in white for a dark one
# (whichever contrasts more with the text). It helps where a light text meets a light band or background. Published
# as the configuration attribute "text_outline".
CONF_TEXT_OUTLINE = "text_outline"
# Fade the words when there is no current reading (display card and LCD wall panels; on by default): while there is no
# current reading, the words in the value's place ("Sensor warming up", "Sensor expired", "No current data") fade
# smoothly from full to about 30% brightness and back, one cycle every 3 seconds (about 0.33 Hz), for the first 30 minutes of
# each episode, then hold still; they never disappear. The display card's status bar "Sensor expiry passed" fades the
# same way for as long as it is shown. Off: both stay still. The display cards also stop it while the system asks for
# reduced motion. Published as the configuration attribute "flash_no_current_data".
CONF_FLASH_NO_CURRENT_DATA = "flash_no_current_data"
# zone_label: the zone word of the four zones outside the range (the catalogue's zone.word.<zone>: "Very low", "Low",
# "High", "Very high"; None in range), published by the Last glucose reading sensor for the users' own automations and
# LED setups. Color is never the only cue: the displays show display_word (below), a word for every zone.
ZONES_WITH_LABEL = ("very_low", "low", "high", "very_high")


def zone_label(zone: str | None) -> str | None:
    """Return the zone word of a zone outside the range (zone_label); None in range and without a zone."""
    return t(f"zone.word.{zone}") if zone in ZONES_WITH_LABEL else None


# Every display (the display cards and the wall panels) shows the zone word under the glucose value, in every zone, so
# that the range never depends on color alone; it is published as "display_word" on the Last glucose reading sensor
# (empty without current data). The TREND column shows the trend as the arrow (the default) or as the trend word, the
# broader category of the same rate: the setting "Trend shown as", published as "trend_display" on the Configuration
# sensor; the trend word itself is the attribute "word" of the Trend arrow sensor.
CONF_TREND_DISPLAY = "trend_display"
TREND_DISPLAY_ARROW = "arrow"
TREND_DISPLAY_WORD = "word"
TREND_DISPLAY_OPTIONS = (TREND_DISPLAY_ARROW, TREND_DISPLAY_WORD)


def display_zone_word(zone: str | None) -> str:
    """Return the zone word under the glucose value, a word for every zone, in range included ("In range",
    "Tight range"); "" without a zone (the catalogue's zone.word.<zone>)."""
    return t(f"zone.word.{zone}") if zone in ZONE_COLORS else ""


_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def valid_hex_color(value: Any) -> bool:
    """Return True for a color written as "#RRGGBB" (six hexadecimal digits)."""
    return isinstance(value, str) and bool(_HEX_COLOR.match(value))


def zone_colors(settings: dict[str, Any] | None) -> dict[str, str]:
    """Return the colors in use per zone: the user's color where one is set and valid, otherwise the default palette.

    Colors are returned in upper case ("#C62828") so that every consumer compares them the same way.
    """
    s = settings or {}
    out = {}
    for zone, default in ZONE_COLORS.items():
        chosen = s.get(CONF_ZONE_COLORS[zone])
        out[zone] = (chosen if valid_hex_color(chosen) else default).upper()
    return out


# Status, in priority order, with its color: green = receiving data, amber = attention, red = no data or expired,
# purple = nothing to assess yet. The label is the catalogue's status.<status> ("Sensor warming up", "Sensor expired",
# "No data yet", "No current data", "Sensor expiring soon", "Current data"), the same as the Status sensor's state names
# in strings.json. "Current data" is the counterpart of "No current data": the newest reading is within the No current
# data limit (5 to 30 minutes). The label describes the data, not the glucose value now (never "Current" alone); the
# age is shown beside it.
STATUS_COLORS = {
    "warm_up": "#7E57C2",
    "expired": "#E53935",
    "no_data": "#7E57C2",  # purple = waiting, as warm-up
    "no_current_data": "#E53935",
    "expiring_soon": "#F9A825",
    "current": "#43A047",
}
# The status decides the status label and color only. Whether the data are current is the no-current-data flag
# (coordinator.no_current_data), which every display, the trend values and the summaries test
# (coordinator.trend_current).


def status_label(status: str) -> str:
    """Return the label of a status ("Current data")."""
    return t(f"status.{status}")


EXPIRING_SOON_MINUTES = 24 * 60  # GlucoCraft's "Expiring soon"
# Weekly trend pages: (key, page range low, high) - direction = last 4 weeks vs the 4 before, "Stable" within 2 % of
# the page range (a GlucoCraft rule); weeks without a value left out
TREND_PAGES = (
    ("tir", 50, 100),
    ("titr", 30, 100),
    ("mean", 70, 200),
    ("cv", 20, 50),
    ("gmi", 5, 8),
    ("below_70", 0, 25),
    ("above_180", 0, 25),
    ("std_dev", 15, 55),
    ("min", 10, 350),
    ("gri", 0, 100),
)  # GRI last, dashboard only: the panels show the nine pages before it, whose directions fill bits 0-17 of the panel
# feed's header (2 bits per page, panel_feed.py), so GRI must stay after them
TREND_DIRECTION_SHARE = 0.02
# The enough-data rule: a period's statistics (the mean, SD, CV, GMI, GRI, the time in ranges, the lowest and highest
# value; every period, week and comparison window) are shown only when sensor active is at least 70% of that period,
# compared before rounding (statistics.enough_data). Battelino et al. (2019, Table 2) give 70% for 14-day periods;
# using it for every period length is a GlucoCraft rule. Covered time, not a count of readings, so the rule is the same
# for a 1-minute and a 15-minute source.
ENOUGH_DATA_PCT = 70.0
GMI_MIN_DATA_PCT = ENOUGH_DATA_PCT  # the GMI's own 70% (Battelino et al., 2019): the same rule
GMI_MIN_PERIOD_SECONDS = 14 * 86400  # the GMI and the GRI over at least 14 days (Battelino et al., 2019)
AGP_RANGES = {
    "range_low": 70,
    "range_high": 180,
    "range_ideal_low": 70,
    "range_ideal_high": 140,
    "range_critical_low": 54,
    "range_critical_high": 250,
}  # the consensus (AGP) bands

# The direction of each goal's comparison, by the goal keys of the settings and the configuration attribute: time in
# range "more than" (the consensus target >70%), time in the tight range "at least", the times below and above range
# and the GMI "less than", the CV "at most" (the consensus target <=36%) (Battelino et al., 2019). The custom goals
# use the consensus directions. The one Python table: the configuration attribute (coordinator), the panel files
# (panel_feed), the summaries (notification) and the CGM Report's custom appendix; the frontend keeps one copy in
# glucocraft-shared.js (GC_CONS_GOAL).
GOAL_OPS = {
    "time_in_range": ">",
    "time_in_tight_range": ">=",
    "time_low": "<",
    "time_very_low": "<",
    "time_high": "<",
    "time_very_high": "<",
    "cv": "<=",
    "gmi": "<",
}
# The consensus targets of the goals that have one, in percent (Battelino et al., 2019): time in range, time below
# range (level 1 and level 2) and level 2, time above range (level 1 and level 2) and level 2, and the CV
CONSENSUS_TARGET_VALUES = {
    "time_in_range": 70,
    "time_low": 4,
    "time_very_low": 1,
    "time_high": 25,
    "time_very_high": 5,
    "cv": 36,
}
# The comparisons of GOAL_OPS as functions
COMPARE = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}

# User-visible keys (entity attributes, the JSON API) in the consensus terms: very low, tight range, custom, so that
# every surface uses the same words. The stored setting keys and values (CONF_*, "personal") and the internal Python
# names keep their names; these map them where they are shown.
PUBLIC_KEYS = {
    "critical_low": "very_low",
    "critical_high": "very_high",
    "ideal_low": "tight_low",
    "ideal_high": "tight_high",
    "personal": "custom",
}


def public_key(key: Any) -> Any:
    """Return a stored / internal key or value as shown to users ("critical_low" -> "very_low", "personal" -> ...)."""
    return PUBLIC_KEYS.get(key, key) if isinstance(key, str) else key


def public_agp_ranges() -> dict[str, float]:
    """Return the consensus (AGP) bands with the user-visible keys: range_very_low, range_low, range_tight_low, ..."""
    return {f"range_{public_key(k[len('range_') :])}": v for k, v in AGP_RANGES.items()}


# Statistics periods (label, seconds)
STAT_PERIODS = [
    ("6h", 6 * 3600),
    ("24h", 24 * 3600),
    ("7d", 7 * 86400),
    ("14d", 14 * 86400),
    ("30d", 30 * 86400),
    ("90d", 90 * 86400),
    ("180d", 180 * 86400),
]
PERIOD_SECONDS = dict(STAT_PERIODS)  # label -> seconds ("14d" -> 1209600)

# Storage (storage.py), version 3: each reading keeps its value, its limit flag (LO / HI) and its source; 2 bytes of
# each record are reserved (written as 0, never read).
STORAGE_VERSION = 3
STORAGE_MAGIC = b"GC02"
STORAGE_HEADER_SIZE = 16
STORAGE_READING_SIZE = 10  # 4 bytes timestamp + 2 bytes raw + 2 bytes reserved + 1 byte limit + 1 byte source
SOURCE_UNKNOWN, SOURCE_PRIMARY, SOURCE_SECONDARY = 0, 1, 2  # the source byte
SOURCE_NAMES = {SOURCE_UNKNOWN: "", SOURCE_PRIMARY: "primary", SOURCE_SECONDARY: "secondary"}
# The span of a stored reading, mg/dL: the readings file keeps no value outside it (storage._plausible), the CSV import
# reads none (export.py) and the panel files send none (panel_feed._readings). The upper end is the highest upper limit
# a CGM system has (limits.DEFAULT_UPPER), so no storable reading lies above it. GlucoCraft rule.
STORED_MGDL = (20.0, 500.0)


# Weekly summary notification setting keys
CONF_NOTIFY_WEEKLY_ENABLED = "notify_weekly_enabled"
CONF_NOTIFY_WEEKLY_DAY = "notify_weekly_day"

# Day of week: 0=Monday ... 6=Sunday (Python weekday convention)
DEFAULT_NOTIFY_WEEKLY_DAY = 6  # Sunday

# The no-data notice (Configure > Notifications; off by default): a status notice about GlucoCraft itself, never about
# glucose. Once the newest reading is older than the notice delay and there is no current data, one Home Assistant
# notice (CONF_NO_DATA_NOTICE) and, independently, one message to the phones (PHONE_TYPES "no_data_notice") per outage;
# the notice is removed when a reading arrives. The delay: one of NO_DATA_NOTICE_DELAYS minutes, never earlier than the
# no-current-data limit.
CONF_NO_DATA_NOTICE = "no_data_notice"
CONF_NO_DATA_NOTICE_DELAY = "no_data_notice_delay"
NO_DATA_NOTICE_DELAYS = (15, 30, 60, 120)
DEFAULT_NO_DATA_NOTICE_DELAY = 30
NO_DATA_NOTICE_ID = "glucocraft_no_data_notice"  # the persistent notification's id

# Phone notifications: GlucoCraft's own notifications, also sent to notify actions the user picks
# at setup or in the options (e.g. notify.mobile_app_<phone> from the Companion app). Off until a target is chosen.
CONF_PHONE_TARGETS = "phone_notify_targets"  # notify action names, without "notify."
CONF_PHONE_TYPES = "phone_notify_types"  # which notifications go there (PHONE_TYPES)
CONF_PHONE_TEST = "phone_notify_test"  # send a test now (not stored)
CONF_PRINTER = "printer_address"  # optional network printer for the Report (IPP host or ipp:// URL)
# When GlucoCraft is deleted: keep the readings, the laboratory results and the sensor events for a reinstall (the
# default), or delete everything (kept.py; __init__.async_remove_entry)
CONF_ON_REMOVAL = "on_removal"
ON_REMOVAL_KEEP, ON_REMOVAL_DELETE = "keep", "delete"
ON_REMOVAL_OPTIONS = (ON_REMOVAL_KEEP, ON_REMOVAL_DELETE)
# The CGM Report's two optional appendices: options of the print action (services.yaml) and of the
# report page, both off by default, so that the report is the consensus report unless an appendix is chosen.
REPORT_OPT_CUSTOM_APPENDIX = "include_custom_appendix"  # "Custom ranges and goals" (the user's own settings)
REPORT_OPT_RESEARCH_APPENDIX = "include_research_appendix"  # "Measures defined for research" (no targets)
REPORT_OPTIONS = (REPORT_OPT_CUSTOM_APPENDIX, REPORT_OPT_RESEARCH_APPENDIX)
# Sensor active as shown next to the percentage: GlucoCraft measures data reception, not sensor wear; the label and
# the sentence appear once per surface (docs/LANGUAGE_STYLE.md).


def sensor_active_label() -> str:
    """Return the CGM Report's sensor-active label, "Sensor active (data received)" (report.sensor_active.label; also
    the label attribute of the <period> sensor active entities). The dashboard and the Documentation use "Sensor active
    (data received by GlucoCraft)" (docs/LANGUAGE_STYLE.md)."""
    return t("report.sensor_active.label")


def sensor_active_note() -> str:
    """Return the sentence that explains sensor active, once per surface."""
    return t("report.sensor_active.note")


# The standard of the nine weekly pages on the display card and every wall panel: one setting, in
# Configure only, offered while custom ranges or goals are set; without them the weekly pages use the consensus ranges.
CONF_WEEKLY_PAGES = "weekly_pages"
WEEKLY_PAGES_CONSENSUS = "consensus"
WEEKLY_PAGES_PERSONAL = "personal"
# the kinds of notification the phones can receive, each chosen on its own (labels: strings.json selector): the
# summaries, and the no-data notice (independent of its Home Assistant notice). Nothing is chosen by default; a stored
# kind that is not one of these (an entry edited by hand) is left out (settings._phone_types).
PHONE_TYPES = ("summaries", "no_data_notice")
DEFAULT_PHONE_TYPES: list[str] = []


# ---------------------------------------------------------------------------------------------------------------------
# The disclaimer, in three forms with the same substance; every copy is its form word for word
# (tests/test_disclaimer.py compares them). It states the intended purpose (what GlucoCraft is not intended for), not
# a legal status. Markdown: the lead sentence is bold; the *_PLAIN forms without the asterisks, for plain-text places.
# - DISCLAIMER, the long form: the README, the first setup screen, the Documentation, the CGM Report and its PDF,
#   CONTRIBUTING.md;
# - DISCLAIMER_SHORT, the short form, where the screen holds no more: the About page of the wall panels and of the
#   display card (the panels' fit check measures it);
# - DISCLAIMER_LINE, the one line: the footer of every dashboard page (with a link to the long form), the CGM Report's
#   page footers (report.footer puts the name before it) and the wall panels' start-up screen (one sentence per line).
DISCLAIMER_LEAD = "For development and testing only."
DISCLAIMER = (
    "**For development and testing only.** GlucoCraft is free, open-source software, made available for development "
    "and testing. It is not intended for any medical purpose, including diagnosing, monitoring, treating or managing "
    "diabetes or any other medical condition, or for making treatment decisions. It has not been cleared, approved or "
    "certified as a medical device by the FDA or any other regulator. Its information and notifications can be late, "
    "missing or wrong. Its notifications are not alarms. GlucoCraft is licensed under the GNU Affero General Public "
    "License, version 3 only (AGPL-3.0-only), and is provided without warranty and, as far as the law allows, without "
    "liability (sections 15 and 16 of the license)."
)
DISCLAIMER_PLAIN = DISCLAIMER.replace("**", "")
DISCLAIMER_SHORT = (
    "**For development and testing only.** GlucoCraft is free, open-source software, made available for development "
    "and testing. It is not intended for any medical purpose, such as diagnosing, monitoring or managing diabetes or "
    "making treatment decisions, and has not been cleared or approved by the FDA or any other regulator. Its "
    "information and notifications can be late, missing or wrong. Its notifications are not alarms. GlucoCraft is "
    "licensed under the AGPL-3.0-only, without warranty or, as far as the law allows, liability."
)
DISCLAIMER_SHORT_PLAIN = DISCLAIMER_SHORT.replace("**", "")
DISCLAIMER_LINE = "For development and testing only. Not for medical use."


# The license: the GNU Affero General Public License, version 3 only (SPDX identifier AGPL-3.0-only; no "or any later
# version" grant).
LICENSE_NAME = "GNU Affero General Public License, version 3 only (AGPL-3.0-only)"
LICENSE_URL = "https://www.gnu.org/licenses/agpl-3.0.html"
COPYRIGHT = "Copyright (C) 2026 The GlucoCraft authors"
GNU_NOTICE = (
    (
        "This program is free software: you can redistribute it and/or modify it under the terms of the GNU Affero "
        "General Public License as published by the Free Software Foundation, version 3 of the License."
    ),
    (
        "This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the "
        "implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public "
        "License for more details."
    ),
)
