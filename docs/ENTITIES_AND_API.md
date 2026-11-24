# GlucoCraft entities and API

This document lists GlucoCraft's entities with their states and attributes, its settings as published, its actions,
notifications and Repairs issues, its storage file and its HTTP API. Abbreviations: A1C, glycated hemoglobin; CGM,
continuous glucose monitoring; CSV, comma-separated values; CV, coefficient of variation; GMI, glucose management
indicator; GRI, Glycemia Risk Index; PDF, Portable Document Format.

Works are cited in APA 7 author–date form. Full references: Documentation > References.

GlucoCraft's disclaimer is at the top of the [README](../README.md). GlucoCraft's entities are ordinary Home Assistant
entities; like any Home Assistant entity, they are available to other dashboards, automations and scripts, and the
HTTP API makes the same values available to GlucoCraft's own cards.

## Naming

- Every entity id starts with **`glucocraft_`**; display names start with "GlucoCraft". Search for "glucocraft" in
  Home Assistant to find them all.
- Entity names, entity ids and attribute keys use the consensus terms of `LANGUAGE_STYLE.md` (very low, low, in range,
  tight range, high, very high, custom).
- **Sentence case:** entity names and the names of their states follow Home Assistant's naming rule: the first word
  starts with a capital letter and the rest is lower case, except abbreviations and the period labels ("Last glucose
  reading", "14D custom time in range", "14D GMI"; states "Consensus ranges", "Not configured"). The dashboard's page
  and card titles keep their own title case (*Consensus Ranges*, *Change Over Time*).
- **The id follows the display name:** `<platform>.glucocraft_<name>` with the name in lower case and every other
  character an underscore ("14D custom time in tight range" = `sensor.glucocraft_14d_custom_time_in_tight_range`,
  "5-minute delta" = `sensor.glucocraft_5_minute_delta`). `tests/test_entity_ids.py` checks it for every entity.
- **Translated names:** every entity's name is translated (`translation_key`, `strings.json` `entity`); the entity ids
  stay the same in every language (the ids of the English names). The period statistics are named "{period} mean" and
  so on, with the period ("14D") as a placeholder.
- **Entities no longer created:** at every start GlucoCraft removes the entity-registry entries of its entry whose
  unique id the setup did not create (`stale_entities.py`): the entities of an optional feature that is off
  (*Optional features* below) and those of a feature an update removed, so that none is left behind as "no longer
  provided". Only when both platforms set up without an error; logged at info with the count. Their recorded history
  and long-term statistics stay in the recorder.
- **Keep GlucoCraft's entity ids.** The dashboard, the cards and the wall panels look for these ids (a panel's are in
  its firmware), and Home Assistant cannot lock an id. A change of an id, also through a new name for the device, is
  the user's choice and is left as it is: the fixable Repairs issue `entity_ids_changed` names it, 2 seconds
  after the last change, and puts the original ids back on the user's confirmation. Display names of the entities and
  the device are free.

## Icons and entity categories

- Every entity and action icon is in `icons.json` (by platform and translation key), with state icons for the status
  (`warm_up`, `expired`, `no_data`, `no_current_data`, `expiring_soon`), the sensor expiry (`not_configured`,
  `no_value`, `unknown_format`, `expiring_soon`, `expired`) and the off state of no current data and of the sensor
  warm-up. The trend arrow has one icon (its states are arrow characters, which Home Assistant's icon translations
  cannot key). `binary_sensor.glucocraft_glucose_in_range` has the icon `mdi:arrow-left-right`.
- Diagnostic entities: `sensor.glucocraft_configuration`, `sensor.glucocraft_active_source`,
  `sensor.glucocraft_data_history`, `sensor.glucocraft_sensor_expiry`, `binary_sensor.glucocraft_sensor_warm_up` and,
  per period, `sensor.glucocraft_<p>_readings` and `sensor.glucocraft_<p>_sensor_active` (these two only while
  *Separate statistics entities* is on). Home Assistant lists them
  under *Diagnostic* on the device page and leaves them out of its automatically generated dashboards.
- **Languages:** the texts in attributes (`label` of the status, `zone_label`, `display_word`, the trend arrow's
  `word`, `limit_text`, the sensor-active `label` and `note`, the `source` texts of the trend arrow and the
  consensus summaries) and the states "N weeks" (Weekly statistics) and "N of M" (Change over time) come from
  GlucoCraft's text catalogue, in Home Assistant's language. The Repairs titles in
  `maintenance_issues` are Home Assistant's translations of GlucoCraft's issues (`strings.json` `issues`), in Home
  Assistant's language, or the issue's translation key where there is no title. The GRI sensor's `source` is the
  fixed citation "Klonoff et al., 2023". The trend arrow's `description` stays the English category name, which
  the cards and panels read.

## Units

- GlucoCraft stores and calculates in mg/dL; every reading's unit is checked on arrival (a mmol/L source is
  converted, 1 mmol/L = 18.016 mg/dL; a source without a unit whose values look like mmol/L raises a repair notice).
- The glucose sensors (current, 5-minute delta, and each period's mean, standard deviation, minimum and maximum) have the blood glucose device class with native mg/dL; Home Assistant shows them in the display unit.
- One setting, **Display unit**, writes every glucose sensor's unit in the entity settings; everything (dashboard,
  cards, report, panels) follows the unit of `sensor.glucocraft_last_glucose_reading`. A unit chosen there in Home
  Assistant's entity settings is copied to the other glucose sensors.
- mg/dL as whole numbers, mmol/L with one decimal; halves are rounded away from zero. The consensus thresholds are
  used as published pairs: 54 / 70 / 140 / 180 / 250 mg/dL = 3.0 / 3.9 / 7.8 / 10.0 / 13.9 mmol/L.
- Attributes named `value_mgdl`, the `range_*` attributes and the glucose values inside the statistics attributes
  (`…_consensus_ranges`, `weekly_statistics`, `change_over_time`, `configuration` `ranges`) are always mg/dL; condition
  thresholds are in the display unit.
- After a unit change, Home Assistant's long-term statistics graphs of earlier data are a Home Assistant limitation;
  GlucoCraft's own store (200 days) is mg/dL and unaffected.

## Recorder rows

Home Assistant's recorder writes a row whenever an entity's state or any of its attributes changes, also when the
attribute is left out of the recorder (`_unrecorded_attributes` removes its bytes, not the row). The values that change
with every reading (the reading time as epoch seconds, the reading count, each source's newest measurement time) are
therefore attributes of Last reading time, whose state changes with every reading anyway: the Active source sensor
changes only with the source, its data state or the detected reading interval (detected again every 50 stored
readings), and the Configuration sensor with a setting, a source's reading interval (the median of its last 10
intervals, which moves with the source's timing), the source in use or the Maintenance status (`maintenance_issues`,
`attention`). The Trend arrow (its angle)
writes a row per reading, and the period
summary sensors and Weekly statistics one per statistics pass (every 5 minutes); those attributes are not stored with
them, except the summary sensors' values. No attribute follows the clock alone except the time left of Sensor
expiry (`remaining_hours`, `remaining_days`). Left out of the recorder (the row is still written): every attribute of
the Configuration sensor, whose state alone is kept in history; the Trend arrow's `angle`; Sensor expiry's
`remaining_hours`, `remaining_days` and `expiry_value`; and the attributes named as not recorded in the tables below.
Rows a day for GlucoCraft's entities, measured
(one simulated day on Home Assistant 2026.10.4's test harness, 200 days
of readings stored; "all": a second source, both optional features on and custom ranges, 134 entities; "defaults": one
source and the settings of a new installation, 28 entities; the entities disabled by default stay disabled):

| Reading interval | defaults | all |
|---|---|---|
| 1 minute | 7,586 | 13,692 |
| 5 minutes | 3,501 | 9,974 |

## How readings work

- One or two sources (options flow). Each reading carries the **CGM's own measurement time** (`measurement_timestamp`,
  `dateString`, … or a configured attribute; Home Assistant's arrival time only when the source has none).
- Two sources: a second source is designed to supply readings that the primary source misses, up to 24 hours back,
  and to continue when it stops, **merged by measurement time**: one reading per measurement, never stored twice; the
  primary's copy wins.
- GlucoCraft's own timer runs every minute (the no-current-data flag), statistics every 5 minutes.
- **Log:** one warning when the source in use becomes unavailable or sends no reading for longer than the
  no-current-data limit, and one info entry when it delivers current readings again (by role, never the entity id).
- **LO / HI.** A state outside the CGM sensor's measurement range (the texts LO / LOW / *Below ...*, HI / HIGH
  / *Above ...* and passed translations, a value of 39 mg/dL (2.2 mmol/L) or less, a value below the source's lower
  limit or above its upper limit (with a *Sensor lower limit* or *Sensor upper limit* chosen by hand, also a value at
  that limit, compared at the precision of the limits: whole mg/dL, or one decimal in mmol/L; with *Automatic* only
  below or above it, because a sensor may measure the limit value itself), or exactly 401 from a source that uses the code 401 for HIGH;
  mmol/L converted with 18.016 first) is stored
  at the source's limit (mg/dL) with the flag `low` / `high`. States that are no reading (unavailable, unknown, ???,
  empty, values of 0 or less, 1-12, 38, warm-up markers, anything below 39 from a source that uses those values as error codes; from a source without a
  unit, values below 30) are not stored. Why: a limit reading shows that glucose lies beyond the limit, and dropping it
  would remove it from time below or above range; it has no value, so it stays out of every number (`limits.py`; Documentation, *Readings Outside the Sensor's
  Range*).
- **Reading interval and gap rule.** A gap is an interval longer than max(15 min, 3 × the reading interval); for the
  statistics the reading interval at each reading is the median of the 10 intervals before it, filled with the
  intervals after it when fewer than 10 come before (`gaps.py`; the same per-stretch rule on the cards and panels). Each
  reading weighs the time to the next reading, or its own reading interval when that interval is a gap or it is the
  last reading; *sensor active* is that covered time divided by the period (`PeriodStats.covered_seconds`, internal).
  Why: `DISPLAY_CONTRACT.md`, Gap rule.
- **Retention:** 200 days, fixed (there is no setting); older readings are removed as whole local days, at setup and
  at the first tick after local midnight. It covers the 6-month report (180 days), the 90-day report with its
  previous period, the 26 weekly 14-day values (the oldest window starts 25 × 7 + 14 = 189 days back) and Change Over Time.

## Current reading

| Entity | State | Attributes |
|---|---|---|
| `sensor.glucocraft_last_glucose_reading` | newest measurement, blood glucose device class (native mg/dL; Home Assistant shows the display unit); it stays the last reading while there is no current data | `zone` (`very_low`, `low`, `near_low`, `tight_range`, `near_high`, `high`, `very_high`) / `zone_color` (the value's zone on the ranges in use and its display color: the colors in the settings or the default palette, very low #C62828, low #EF5350, in range below and above the tight range #FFC107, tight range #4CAF50, high #42A5F5, very high #1565C0), `zone_label` (the zone word outside the range: "Very low", "Low", "High", "Very high"; None in range), `display_word` (the zone word every display shows under the value, in every setting: "Very low" / "Low" / "In range" / "Tight range" / "High" / "Very high"; for a LO / HI reading "Sensor limit" (`zone` stays `very_low` / `very_high`); empty while there is no current data and during a warm-up; after the stated expiry it follows the data), `value_mgdl` (mg/dL, for the card and panels), `unit` (the display unit everything follows), `limit` (`low` / `high` / null: a reading outside the sensor's measurement range; the state is then unknown, so that no limit value enters Home Assistant's history and statistics; `value_mgdl` and `last_reading_mgdl` then hold the stored limit, for the panels' own state of the reading, and are no measurement; `zone` / `zone_color` are `very_low` / `very_high`) and `limit_text` (`LO` / `HI` / null: what every display shows instead of the number); for the screen the displays show during a stretch of readings at the sensor's limit: `limit_since` (epoch seconds: the first reading of the current unbroken stretch of limit readings of the same kind; a data gap by the gap rule starts a new stretch; null while the newest reading is measured), `last_measured_mgdl` and `last_measured_ts` (the newest measured reading before the stretch, unrounded mg/dL and epoch seconds; outside a stretch the newest measured reading; null when none is stored); the three are worked out from the stored readings, so they survive a restart, and they are not recorded (read live). The attributes `zone` and `limit` have state names in `strings.json` |
| `sensor.glucocraft_last_reading_time` | its measurement time (timestamp) | `last_reading_mgdl` (the same reading in mg/dL; the panels compare it with the value they received, so that a value is never paired with another reading's time; not recorded) and the values that change with every reading, so that no other sensor changes with every reading because of them (not recorded: read live): `last_reading_ts` (the same time, epoch seconds, null without a reading; the display card and the panels count the reading's age from it), `last_reading_limit` (`low` / `high` / null: the same reading's limit flag, so that "Last reading" shows LO / HI with its own time), `reading_count` (the stored readings), `primary_last_measurement` / `secondary_last_measurement` (each source's newest measurement time, UTC ISO 8601; null without that source or before its first reading); and `time_untrusted` (recorded in history: true while that time is more than 5 minutes ahead of the system clock: the reading's age is not known, so the displays show "Reading: ---" in the status bar and no time line under the words; the Repairs issue `clock_behind` is open meanwhile, and the dashboard's Maintenance page explains what to check) |
| `sensor.glucocraft_5_minute_delta` | display name **5-minute delta**: the change over 5 minutes of the smoothed glucose (the mean over 20 / 15 / 10 min when the last 20 min's range is < 10 / < 25 / more mg/dL; GlucoCraft rule; `coordinator.ADAPTIVE`), unrounded (every display rounds it once: whole mg/dL or 0.1 mmol/L), blood glucose device class; 5 MIN DELTA on the card and panels | `value_mgdl`, `unit` |
| `sensor.glucocraft_trend_arrow` | ↑ ↗ → ↘ ↓ (unknown when no trend is known) | `description` = the trend category, Rising Quickly > 2, Rising 1–2, Changing Slowly < 1 mg/dL/min, Falling, Falling Quickly (Abbott Diabetes Care, 2022), used for every CGM; "Unknown" while no trend is known; `word` (the trend word as the displays show it in the TREND column with the setting *Trend shown as* set to *Word*: the category in sentence case in Home Assistant's language, "Changing slowly"; empty while no trend is known); `angle` (the panels' arrow: rate / 2 mg/dL/min × 90°, clamped to ±90°, from the rate rounded to 0.1 mg/dL/min as the trend word is, so in 4.5° steps, given as whole degrees) and `source` (the citation of the trend categories) |
| `sensor.glucocraft_active_source` | the current value's source name; unknown (Home Assistant's state) until a reading has arrived from a source since the setup | `source_entity`, `second_source_active` (the current value came from the second source), `detected_interval` (s: the median interval between the last 50 stored readings, from 30 seconds to 15 minutes, or from 15 minutes to 1 hour for a source that reads every 15 minutes or less often; the statistics' default interval, not the gap rule's interval, which is `reading_interval_seconds` of the Configuration sensor), `no_current_data` |
| `sensor.glucocraft_spoken_summary` | display name **Spoken summary**, **disabled by default** (every new state is stored in the recorder; enable it under the entity's settings). One ready-to-speak sentence of at most 255 characters (Home Assistant's limit for a state), from the shared spoken text (`spoken.py`, the same words as the cards' screen-reader text, without its 5-minute delta and page sentence): in the order of `ACCESSIBILITY.md` rule 4: the data status when there is a problem ("Sensor expired."; during a warm-up "Sensor warming up." alone), the value with its unit written out (a LO / HI reading as "below the sensor's lower limit" / "above the sensor's upper limit") and the zone word, the time of the reading in the time format setting, and the trend words (after the stated expiry too, while readings still arrive: "Sensor expired." comes first), for example "Glucose 112 milligrams per deciliter, tight range, measured at 7:46 AM, changing slowly."; while there is no current data "No current data. Last reading at 7:46 AM.", without a value; a reading of an earlier day is named with its day ("Last reading yesterday at 9:45 PM.", or its date in the date format setting, "on October 22 at 9:45 PM"). Made again with each reading and on a change of the status, the no-current-data flag, the zone, the trend, the limit flag, the display unit, the decimal separator or the time or date format, and at midnight; never on the minute tick alone; for this reason it names the time of the reading and never an age, which would no longer be correct while the state stands. No unit, no state class (no long-term statistics). | — |

## Status

The enum sensors below publish keys as their states; Home Assistant shows them by name. Templates show the names with
`state_translated(...)`; `states(...)` returns the key.

| Entity | Meaning | Attributes |
|---|---|---|
| `sensor.glucocraft_status` | what the status bar shows, in priority order: `warm_up` > `expired` > `no_data` > `no_current_data` > `expiring_soon` > `current`, shown by Home Assistant as Sensor warming up / Sensor expired / No data yet / No current data / Sensor expiring soon / Current data | `label` (the same names), `color` ("#RRGGBB": current #43A047, expiring soon #F9A825, expired and no current data #E53935, warm-up and no data yet #7E57C2; `const.STATUS_COLORS`); both change only with the status (the time of the last statistics pass is `computed_at` of `sensor.glucocraft_weekly_statistics`) |
| `binary_sensor.glucocraft_no_current_data` | on = no current data: no reading, no new measurement for longer than the no-current-data limit (*No current data after*, 5 to 30 minutes), or a reading more than 5 minutes later than the system clock. The one fact that every display, the trend values, the summaries and the no-data notice follow (the status decides only the status label and color) | `threshold_minutes` (the limit in use); no age attribute, which would change every minute: the reading's time is `sensor.glucocraft_last_reading_time`, and every display counts the age itself |
| `binary_sensor.glucocraft_sensor_warm_up` | on = a new CGM sensor is warming up (expiry moved > 1 day later, or the warm-up marker some CGM integrations send; Documentation, *Readings Outside the Sensor's Range*); off with the first reading measured after the warm-up began, and at the latest 24 hours after it began (GlucoCraft rule: then the normal no-current-data rule applies) | `started_at` |
| `sensor.glucocraft_sensor_expiry` | an enum sensor: `not_expired` / `expiring_soon` (≤ 24 h) / `expired` / `not_configured` / `no_value` / `unknown_format`, shown by Home Assistant as Not expired / Expiring soon / Expired / Not configured / No value (the expiry entity has no value; not Home Assistant's own state "unavailable") / Unknown format | `expires_at` (UTC ISO), `remaining_hours`, `remaining_days` (both only while the expiry is known), `expiry_entity` (null while no expiry entity is picked), `expiry_format` (a code: `not_configured`, `missing`, `unavailable`, `datetime`, `date`, `time_left_days`, `time_left_hours`, `time_left_minutes`, `epoch_ms`, `epoch_s`, `unrecognized`) and `expiry_value` (the expiry entity's raw state; absent when that entity does not exist); the reading's time is `sensor.glucocraft_last_reading_time` |
| `binary_sensor.glucocraft_using_custom_ranges` | on = custom ranges are in use (the consensus ranges, Battelino et al., 2019, are the default); always off while the optional feature *Custom ranges and goals* is off | `range_very_low`, `range_low`, `range_tight_low`, `range_tight_high`, `range_high`, `range_very_high` (in use, mg/dL), `consensus_very_low`, `consensus_low`, `consensus_tight_low`, `consensus_tight_high`, `consensus_high`, `consensus_very_high` (the consensus ranges, mg/dL) |
| `sensor.glucocraft_data_history` | an enum sensor: the span from the first to the last stored reading, as the longest statistics period it reaches: `no_data`, `less_than_6_hours`, `6_hours`, `24_hours`, `7_days`, `14_days`, `30_days`, `90_days`, `180_days`, shown by Home Assistant as No data, Less than 6 hours, 6 hours, ... 180 days. The span is not the share of time with data (that is *sensor active*) | `features_available` (per key, whether the stored span reaches it; it says nothing about the share of time with data: `last_glucose_reading` a reading, `trend_arrow` and `delta` 5 readings, `stats_6h` … `stats_180d` a span of 6 hours … 180 days, `weekly_statistics` a span of 7 days, `gmi` true when the GMI rule, 14 days or more with ≥70% data, is met for a period of 14 days or longer, whether or not the GMI entities exist), `features_available_count`, `features_total` (12), `storage_problem` (`save_failed` while the readings file cannot be written (the readings since the last good save are in memory only; Repairs `storage_save_failed`); `recovered`, `damaged`, `unreadable` or `newer` when the readings file was damaged, could not be read or is in a newer version's format, also a Repairs issue; null when it is read and written normally) and `storage_kept_file` (the name of the copy kept aside, or null) |

## Settings

`sensor.glucocraft_configuration` (diagnostic; an enum sensor, state `consensus_ranges` or `custom_ranges`, shown as
"Consensus ranges" / "Custom ranges") carries every setting as attributes: `primary_source` / `secondary_source`
(`entity`, `name`, `time_attribute`, `in_use`, `lower_limit` / `upper_limit` (the resolved sensor
limits, mg/dL, integer), `lower_limit_setting` / `upper_limit_setting` ("auto" or the value),
`lower_limit_auto_reason` / `upper_limit_auto_reason` (what *Automatic* used, a short generic text such as "shown by
the source" or "the delivering integration", without product names; null when set), `reading_interval_seconds` (the
median of the source's last 10 live intervals; null until 3 exist; for the source in use without live readings, the
newest stored reading's interval) and `gap_threshold_seconds` (max(900, 3 × that); null without an interval)), the
same objects under `sources` (`primary`, `secondary` or null), top-level `reading_interval_seconds` and
`gap_threshold_seconds` (the source in use; the threshold 900 without an interval),
`no_current_data_minutes`, `unit` (the display unit), `time_format` "12"/"24" and `date_format` "MDY"/"DMY"/"YMD" (as
resolved: Automatic follows Home Assistant's country), `time_format_setting` / `date_format_setting` ("auto" or the
format chosen; the date format as MDY / DMY / YMD, although the setting is stored in lower case), `first_weekday`
(0 = Monday … 6 = Sunday), `decimal_separator` ("." or ","), `a1c_unit` ("percent", "mmol_mol" or "both") and
`paper_size` ("letter" or "a4"), each as resolved, with `first_weekday_setting`, `decimal_separator_setting`,
`a1c_unit_setting` and `paper_size_setting` ("auto" or the choice), `integration_version` (the version of the
integration), `display_card`
(square or rectangular: the display card on the dashboard's start page), `trend_display` (the setting *Trend shown
as*: `arrow`, the default, or `word`: how the TREND column of the display cards and the wall panels shows the trend;
the zone word is under the value with either), `maintenance_issues` (the Maintenance status, `maintenance.py`: one
entry per issue, `{"kind": "panel_offline", "name"}` for a wall panel offline for 10 minutes, `{"kind": "repairs",
"title"}` for an open GlucoCraft Repairs issue with its title, `{"kind": "csv_export", "reason"}` for a failed CSV export
until an export succeeds or GlucoCraft restarts; an empty list when there are none; no current data and the sensor
expiry are not counted;
the dashboard's Maintenance button counts the entries and the Maintenance page lists them), `zone_colors` and `band_colors` (zone → "#RRGGBB" for the seven zones
`very_low` … `very_high`: the display colors (setting *Display colors*) of the glucose value and everything else that shows a zone by color, and the
colors of the graphs' range bands, by default the CGM Report's colors), `card_background` ("theme" or "#RRGGBB",
default "#000000"), `card_foreground` ("#000000" or "#FFFFFF", whichever contrasts more with the card background; null
with "theme", whose text color is used), `panel_background` ("#RRGGBB", default "#000000") and `panel_foreground` (the
same rule for the wall panels), `text_outline` (the setting *Outline text*, `false` by default: `true` = a 1-pixel
edge around the graph labels and every text in a zone color on the display card and the panels),
`flash_no_current_data` (the setting *Fade the words when there is no current reading*, `true` by default: while there
is no current reading, the words "Sensor warming up", "Sensor expired" or "No current data" in the value's place throb
for the first 30 minutes of each episode, and the status bar's "Sensor expiry passed" throbs for as long as the stated
expiry has passed, on the display card and the panels), `attention` (true while at least one GlucoCraft Repairs issue
is open and not ignored by the user (an issue the user chose *Ignore* on does not count), else false: the display card
and the panels then show "Check Home Assistant" in the middle of the status bar), `ranges` (mg/dL, the ranges in use: the very-low threshold at or above the sensor's lower limit
and the very-high threshold at or below its upper limit, so a stored threshold outside them is given at the limit;
`very_low`, `low`, `tight_low`, `tight_high`, `high`, `very_high`), `consensus_ranges` (the consensus values:
`range_very_low`, `range_low`, `range_tight_low`, `range_tight_high`, `range_high`, `range_very_high`), `goals`
(`op`, `value`; keys `time_in_range`, `time_in_tight_range`, `time_low`, `time_very_low`, `time_high`,
`time_very_high`, `cv`, `gmi`; a goal without a value is left out), `goals_custom` (true when any goal differs from
its default), `expiry_entity`, `notifications` (`daily`, `daily_time`, `weekly`, `weekly_day` 0 = Monday,
`no_data_notice` (the Home Assistant no-data notice, `false` by default), `no_data_notice_delay_minutes` (15, 30,
60 or 120, default 30)), `phone_notifications` (`targets`: the notify actions chosen at setup or in the options,
`types`: which notifications go there, `summaries` and `no_data_notice`, each only when chosen), `retention_days`
(always 200), `dashboard` (the setting *Dashboard in the sidebar*), `on_removal` (the setting *When GlucoCraft is
deleted*: `keep`, the default, or `delete`), `panel_feed` (true once a wall-panel address
exists), `printer` (true when a report printer is set; the address itself is not an attribute), `weekly_pages` (`setting` consensus / custom, `offered` = custom ranges or
goals are set, `shown` = the range set the display card's and panels' weekly pages show, consensus / custom), and the
two optional features `custom_ranges_and_goals` and `separate_statistics` (true / false; below).
While `custom_ranges_and_goals` is false, `ranges`, `goals` and `weekly_pages` are the defaults in use (the consensus
ranges and targets), whatever is stored. The stored setting keys of the configuration entry keep their internal names
(for example `range_critical_low`, `goal_tbr_critical`, the weekly-pages value
`personal`); only what is shown uses the consensus terms. The display colors are stored as `color_<zone>` and
`band_color_<zone>` ("#RRGGBB"; absent = the default), the backgrounds as `card_background` and `panel_background`,
Outline text as `text_outline` (true / false); a stored value that is not valid is its default. A stored key that is
not a setting of this version is never read.

**Checks.** The ranges screen refuses limits out of order (very low < low <= tight range from < tight range to <= high
< very high, in mg/dL) and marks each field out of order with its own error text (error keys `<field>_order`, for
example `range_high_order`, and the summary `range_order`). A stored set out of this order (an entry edited by hand)
is not used: GlucoCraft then uses the consensus ranges as a whole. The ranges screen also refuses a very-low
limit below the glucose sources' lower limit and a very-high limit above their upper limit (with a second source the
stricter of the two; `limits.threshold_span`), error keys `range_critical_low_below_sensor_limit` and
`range_critical_high_above_sensor_limit`; it compares at the precision shown and stores a limit equal to the sensor's
limit as shown as the limit itself (2.2 mmol/L as 40 mg/dL). When it opens after the Glucose sources screen (the
ranges no longer fit the limits entered there), it shows these errors at once, and the sources' and the ranges'
changes are saved together. The very-high check is a guard only: the screen's very-high box ends at 400 mg/dL, the
lowest upper limit. The goals screen refuses a goal for the time below
the very-low limit above the goal for the time below the low limit, and the same for the time above the very-high and
the high limit (`goal_tbr_critical_order`, `goal_tar_critical_order`).

**Sensor-limit settings.** Configuration-entry keys `sensor_lower_limit` ("auto" | "40" | "50") and
`sensor_upper_limit` ("auto" | "400" | "450" | "500") for the primary source, `secondary_sensor_lower_limit` /
`secondary_sensor_upper_limit` for the second (strings, Home Assistant select values; missing = "auto"). *Automatic*,
upper limit: a limit the source shows itself (an *Above Y* text, or the code 401 for HIGH; a plain HI / HIGH text
shows no limit), the delivering integration or the uploader its device names (`limits.auto_upper`); otherwise
500 mg/dL, so that a measured value is never turned into HI. *Automatic*, lower limit: 40 mg/dL, or 50 when the source
shows a *Below 50* text or its integration has that lower limit (`limits.auto_lower`). The Documentation's *Readings
Outside the Sensor's Range* names the CGM systems and integrations of these rules. The screen's description states the rule once for both settings: "The sensor limits must match the CGM sensor's
measurement range. GlucoCraft sets them from the source where it can and shows a Repairs notice when the readings point
to a different limit; otherwise they are set by hand", and that it is the user's responsibility to know their CGM
system and to adjust these settings where necessary.

**Optional features.** Configuration-entry keys `custom_ranges_and_goals` and `separate_statistics` (booleans;
Configure > *Optional features*), stored as false by the setup; an entry without them reads both as off. *Custom ranges and goals* off: the ranges, goals and weekly pages' range set in use
are the defaults (the stored values are kept), the screens *Glucose ranges* and *Custom goals*, the dashboard page
*Custom Ranges* and the report's custom appendix are not offered, and per period the entities `custom_ranges`,
`custom_time_in_range`, `custom_time_in_tight_range`, `custom_time_low`, `custom_time_high`, `custom_time_very_low`,
`custom_time_very_high` do not exist (49). *Separate statistics entities* off: per period
`mean`, `standard_deviation`, `cv`, `minimum`, `maximum`, `sensor_active`, `readings` and (14 days and longer) `gmi` and
`gri` do not exist (57). Turning a feature off removes its entities from the entity registry; the recorded history and
long-term statistics stay, and turning it on again creates the same entities under the same entity ids, so their
history continues. A new installation has 28 entities; one with both features on has 134.

## Conditions (unknown while there is no current data)

`binary_sensor.glucocraft_glucose_below_range` (below the low limit, very low included), `_glucose_above_range`
(above the high limit, very high included), `_glucose_very_low`, `_glucose_very_high`, `_glucose_in_range`,
`_glucose_in_tight_range`. All on the ranges in use. Glucose below range, Glucose above range, Glucose very low and
Glucose very high have the attributes `threshold` (the limit, in the display unit), `unit` (the display unit) and
`limit_source` (the range set in use: "consensus ranges", or "custom ranges" while custom ranges are in use).
Glucose above range, Glucose very low, Glucose very high, Glucose in range and Glucose in tight range start switched
off in a new installation, because no GlucoCraft page, card or panel reads them. Each one is enabled on its own under
Settings > Devices & services > Entities. Glucose below range stays enabled (the dashboard reads it).

## Statistics per period

`sensor.glucocraft_<period>_<metric>`, periods **6h, 24h, 7d, 14d, 30d, 90d, 180d**, on the ranges in use (custom
when set; the time-in-band metrics say so in their names, "14D custom time in range"). Recomputed every 5 minutes; a
period without readings is unknown. `consensus_ranges` always exists; `custom_ranges` and the six `custom_time_*`
metrics only while *Custom ranges and goals* is on; `mean`, `standard_deviation`, `minimum`, `maximum`, `cv`,
`sensor_active`, `readings` and (14 days and longer) `gmi` and `gri` only while *Separate statistics entities* is on
(see Settings). GlucoCraft's dashboard, display card and wall panels read only the two summaries, `consensus_ranges` and
`custom_ranges`.

**Enough data.** A period's statistics are given only when sensor active is at least 70% of the period, compared
before rounding (`statistics.enough_data`; Battelino et al., 2019, Table 2, give 70% for 14-day periods; applying it to
every period length is a GlucoCraft rule). Below it the state of every statistic sensor of the period (`mean`,
`standard_deviation`, `cv`, `minimum`, `maximum`, `gmi`, `gri`, the `custom_time_*` sensors and the two summaries) is
unknown, and the summaries' statistic attributes are null (the same keys, so that a reader finds every key);
`sensor_active` and `readings` keep their states. The summaries also carry `enough_data` (false below the rule),
`period_seconds` (the period's length) and `covered_seconds` (the seconds the readings cover by the gap rule, unrounded;
sensor active = covered / period), from which the dashboard works out about how much more data the period needs. The
time at the sensor's limit (`limit_readings`, `limit_seconds`) is a fact, not a statistic, and is stated either way.

| Metric | Meaning |
|---|---|
| `mean`, `standard_deviation`, `minimum`, `maximum` | blood glucose device class (display unit); the state of each rounded once to the step shown in the display unit (whole mg/dL; in mmol/L the mg/dL value of the value rounded to 0.1 mmol/L, which Home Assistant converts back to exactly that tenth), so that it changes only when the shown value does; a period with a single reading has its value as the mean and no standard deviation (unknown); `minimum` / `maximum` are the lowest and highest measured reading, without attributes (a reading at the sensor's limit has no value, so a period of limit readings alone has none: unknown) |
| `cv` | %; unknown for a single reading. The state of the per-statistic percentage sensors (`cv`, `sensor_active` and the six `custom_time_*` sensors) is the exact value rounded once to one decimal (halves away from zero), so that Home Assistant's recorder writes a row only when the value shown changes; the summary sensors' states (their time in range) are rounded the same way (a summary sensor still writes a row whenever its unrounded attributes change); every attribute is unrounded and shown with one decimal (Home Assistant's display precision): every display rounds them once |
| `custom_time_in_range`, `custom_time_in_tight_range` | % of time in range (low limit to high limit) / in the tight range, on the ranges in the settings |
| `custom_time_low`, `custom_time_high` | % of time in the level-1 bands (between the very-low and the low limit / the high and the very-high limit) |
| `custom_time_very_low`, `custom_time_very_high` | % of time below the very-low limit / above the very-high limit |
| `consensus_ranges` | the period on the consensus bands, whatever the ranges in use: state = % of time 70–180 mg/dL, rounded once to one decimal; attributes `has_data`, `time_70_180`, `time_70_140`, `time_below_70`, `time_below_54`, `time_54_69`, `time_181_250`, `time_above_180`, `time_above_250` (`time_below_70` and `time_above_180`, the combined bands; every percentage and the CV unrounded, rounded once where shown), `time_below_low` and `time_above_high` (the same combined bands), `mean`, `std_dev`, `cv`, `gmi`, `gmi_mmol_mol`, `gri`, `gri_zone` (A to E, the quintiles of the GRI; Klonoff et al., 2023), `gri_hypo`, `gri_hyper` (the four GRI values null for the periods shorter than 14 days), `min_value`, `max_value`, `sensor_active`, `sensor_active_ok` (true at 70% sensor active or more, compared before rounding: 69.96% is shown as 70.0% but does not meet the rule; the dashboard's 70% rules read it; Battelino et al., 2019), `readings`, `limit_readings` (`{"low": n, "high": n}`, the readings at the sensor's limit, left out of the mean, SD, CV, GMI and the lowest and highest value and counted in the time in each band) and `limit_seconds` (`{"low": s, "high": s}`, the whole seconds they represent, up), `enough_data`, `period_seconds` and `covered_seconds` (*Enough data* above), `days` (the length of the period in days: 0.25 for 6 hours), `source` |
| `custom_ranges` | the period on the ranges in the settings, only while *Custom ranges and goals* is on: state = % of time in range, rounded once to one decimal; attributes `has_data`, `time_in_range`, `time_in_tight_range`, `time_very_low`, `time_low`, `time_high`, `time_very_high`, `time_below_low` and `time_above_high` (all time below the low / above the high limit; the percentages and the CV unrounded), `mean`, `std_dev` (mg/dL), `cv`, `gmi`, `gmi_mmol_mol`, `min_value`, `max_value`, `sensor_active`, `sensor_active_ok` (as on `consensus_ranges`), `readings`, `limit_readings`, `limit_seconds`, `enough_data`, `period_seconds`, `covered_seconds`, `days`; the range-independent values equal those of `consensus_ranges`. The Custom Ranges page and, while the feature is on, the 6-hour statistics of the display card and the panels read it |
| `gri` | Glycemia Risk Index (Klonoff et al., 2023), **always on the consensus bands**, 14 days and longer only, with at least 70% sensor active (as the GMI; GlucoCraft rule); attributes `gri_zone` (A to E, the quintiles of the GRI), `gri_hypo`, `gri_hyper` (as on `consensus_ranges`), `source` |
| `sensor_active` | % of the period with CGM data: the time the readings represent under the gap rule, divided by the period. It measures data reception, not sensor wear; the attributes `label` ("Sensor active (data received)", the CGM Report's form) and `note` ("Close to, but not the same as, the CGM system's own figure: only the readings received are counted."), not recorded in history (`LANGUAGE_STYLE.md` rule 21) |
| `readings` | number of readings |
| `gmi` | %, whatever the A1C and GMI unit setting (the attributes of `consensus_ranges` and `custom_ranges` carry `gmi_mmol_mol` beside `gmi`); 14 days and longer, only with ≥70% data (Battelino et al., 2019) |

## Weekly trends

`sensor.glucocraft_weekly_statistics` (state "N weeks"; the attributes `weeks` and `computed_at` are not recorded in
history, `directions` is). Attribute `computed_at`: the time of the statistics pass that calculated the weeks (UTC ISO,
every 5 minutes; every period statistic is calculated in the same pass, so the dashboard shows it as the time the
statistics were calculated; null before the first pass). Attribute `weeks`: 26
entries, newest first, each with `start`, `end` (UTC ISO) and `has_data` (the week has enough data); the
values are the 7 days of each week. **Week rule:** the same as the weekly pages of the display card and the panels and
Change Over Time: a week counts only with enough data, sensor active at least 70% of the week, measured as covered time
by the gap rule and compared before rounding (`statistics.enough_data`); a week with less data has no values anywhere
(`has_data` false), and the state counts only the weeks that meet it. With data: the values on the
ranges in use (`mean`, `std_dev`, `cv`, `gmi` (the 14-day GMI at the week's end, only with ≥70% data),
`time_in_range`, `time_in_tight_range`, `time_low`, `time_high`, `time_below_low` and `time_above_high` (all time
below the low / above the high limit, level 1 and level 2 together; the percentages and the CV unrounded),
`min_value`, `max_value`, `reading_count`, `gri` (the 14-day GRI at the week's end, on the consensus bands whatever the
ranges in use)) and **`consensus`**: the same week on the consensus bands
(`time_70_180`, `time_70_140`, `time_below_70`, `time_below_54`, `time_above_180`, `time_above_250`,
`time_below_low` and `time_above_high` (the same combined bands as `time_below_70` and `time_above_180`), `mean`,
`std_dev`, `cv`, `gmi` (the 14-day GMI at the week's end, only with ≥70% data), `min_value`, `max_value`,
`sensor_active`, `gri` (the 14-day GRI at the week's end)), or null when the week has no values on the consensus bands. Attribute `directions`: each
weekly page's direction on the consensus ranges, with the keys `tir`, `titr`, `mean`, `cv`, `gmi`, `below_70`,
`above_180`, `std_dev`, `min` and `gri` (`const.TREND_PAGES`): Increasing, Decreasing or Stable, from the mean of the
14-day consensus values of the last 4 weeks against the mean of the 4 weeks before; Stable while the difference is
within 2% of the page's scale; null with fewer than 8 weeks or without values. This method is a GlucoCraft rule.

The weekly pages of the display card and the panels show the **14-day values** at each week's end (Change Over Time
below), not these 7-day values.

## Change Over Time

`sensor.glucocraft_change_over_time` (state "N of 26": 14-day windows with at least 70% sensor active; attributes not
recorded in history; calculated hourly). Attributes `computed_at`, `consensus` (the consensus ranges) and `custom`
(the ranges in use), each with `week_ends` (26, newest first), `weeks14` (the 14 days ending at each week
end), `weeks7` (the 7 days ending there) and `compare` (`last_4_weeks`, `weeks_9_13_ago`, `weeks_22_26_ago`); each
value set has `tir`, `titr`, `below`, `very_low`, `above`, `very_high`, `mean`, `sd`, `cv`, `gmi` (14 days or more with
≥70% data) with `gmi_mmol_mol`, `gri` (the consensus ranges only, 14 days or more: `weeks14` and `compare`; null in
`weeks7`, and in `custom` unless `custom_is_consensus` is true), `min`, `max` (measured values), `active` (sensor active, unrounded), `active_ok`
(at least 70% sensor active, compared before rounding; the 70% rules read it, never `active` as shown,
rounded), `hours`; a
window without enough data (less than 70% sensor active) is `null`. `custom_is_consensus` is true while the ranges in use are
the consensus ones (the default, with *Custom ranges and goals* off); `custom` is then the same data as `consensus`.

## Services

The actions are registered once per Home Assistant run (`async_setup`). Every action (the three summary actions,
`add_lab_a1c`, `remove_lab_a1c`, `print_report`, `export_csv` and `import_csv`) is for administrators only when a user calls it;
calls without a user (automations and scripts) are allowed, as for Home Assistant's own administrator actions. The
summary actions are among them because a summary is also sent to the phones chosen for the summaries. Every action fails with `not_loaded` while no GlucoCraft entry is loaded. The select options of
`print_report` and `add_lab_a1c` are translated.

| Service | Does |
|---|---|
| `glucocraft.send_daily_summary`, `send_weekly_summary`, `send_monthly_summary` | create the summary notification now, also while the scheduled summary is off; it replaces the previous summary of its kind and also goes to the notify actions chosen for summaries; a summary that cannot be sent fails with `summary_failed` |
| `glucocraft.add_lab_a1c` (`date`, `a1c` 3–20% or 9–195 mmol/mol, `unit` `percent` / `mmol_mol`, default `percent`; a result in mmol/mol is kept in whole mmol/mol; a result with the same date replaces the earlier one), `glucocraft.remove_lab_a1c` (`date`; nothing changes when no result exists for it) | laboratory A1C results for the Report (compared with the GMI of the 90 days ending on the result's date); the action's field check refuses a date (year-month-day) or unit that cannot be read and a number outside 3–195 (Home Assistant's own validation message); `add_lab_a1c` fails with `lab_invalid` for a value outside the span of its unit (above 20% or below 9 mmol/mol) and for a date later than today (Home Assistant's time zone), and with `lab_add_failed` when the result could not be stored; `remove_lab_a1c` fails with `lab_remove_failed` when the result could not be removed |
| `glucocraft.print_report` (`days` 14 / 30 / 90 / 180, default 14, `report` summary / full, default summary, `color` and `two_sided`, both on by default, `include_custom_appendix` and `include_research_appendix`, both off by default) | the CGM Report as a PDF to the printer in the settings (IPP, the Internet Printing Protocol); returns `{ok, message, reason, pages, file}` (the result as the notification states it, the printer's answer, the page count, the PDF kept) when the report was sent, so `ok` is then always true; a print that fails raises an error instead. Without a printer address it creates no PDF and fails (`print_no_printer`); a print that does not reach the printer fails with `print_failed` (the PDF is kept); when the PDF could not be made, it fails with `print_pdf_failed` (nothing is sent to the printer; the error states why). After every attempt, a failed one included, the notification *GlucoCraft CGM Report* states the result; a refused period or appendix creates none. `include_custom_appendix` adds the appendix *Custom ranges and goals* (the statistics with the ranges and goals in the settings, labeled as the user's own settings; the main report always uses the consensus ranges); `include_research_appendix` adds the appendix *Measures defined for research* (extended high-glucose events as defined for clinical trials, Battelino et al., 2023; no targets). `include_custom_appendix` is refused (`custom_appendix_off`) while *Custom ranges and goals* is off, and a period other than 14, 30, 90 or 180 days with `report_period` |
| `glucocraft.export_csv` (no fields) | saves every stored reading as `glucocraft-readings_<YYYY-MM-DD_HHMMSS>.csv` in `<config>/glucocraft_exports/` (columns `local_time` (ISO 8601 with offset, Home Assistant's time zone), `utc_time`, `glucose_mg_dl` (one decimal; empty for a limit reading, which has no value), `glucose_display` (as the displays show it: a limit
reading as `LO` / `HI`), `display_unit`, `source` (`primary` / `secondary` / empty for a reading stored without its source), `limit` (`low` / `high` / empty)) and, when laboratory results exist, `glucocraft-lab-a1c_<same stamp>.csv` (`date`, `a1c_percent`, `a1c_mmol_mol` (a result entered in percent converted with the IFCC-NGSP master equation, whole mmol/mol), `entered_unit` (`percent` / `mmol_mol`, the unit the import reads)); decimal point always, UTF-8; the last 12 of each kind kept. Returns `{"folder", "files", "readings"}` and creates a persistent notification; when the files could not be written (no space, no permission, or any other failure), it fails with `export_failed` (a failed export is an issue of the Maintenance status until an export succeeds or GlucoCraft restarts). The Maintenance page's *Save CSV export* button calls it. Why: readings older than the retention are removed, and the export keeps them available outside GlucoCraft |
| `glucocraft.import_csv` (`file`: the name of a readings export in `<config>/glucocraft_exports/`, without a folder; empty: the newest export there) | reads one of GlucoCraft's own readings exports back in, one row at a time (`export.read_readings_file`: the columns by their names in the language in use or in English; `glucose_mg_dl`, else `glucose_display` in `display_unit`, and for a `LO` / `HI` row (`limit`) the lower / upper
limit of the glucose sources in use; `utc_time`, else `local_time`; the decimal point or the decimal comma, comma-, semicolon- or tab-separated) and the laboratory results of the export with the same time stamp. Stored data win: a reading within 30 s of a stored reading is not imported (`storage.add_readings`), a laboratory result of a date already stored is kept (and counted); a laboratory row that cannot be read, or whose date does not exist or is later than today, or whose value is outside 3–20% / 9–195 mmol/mol, is left out and counted. Only readings from the retention's first day to the clock are taken. Returns `{"file", "imported", "already_stored", "not_kept", "not_read", "lab_imported", "lab_already_stored", "lab_not_read"}` and creates the persistent notification *GlucoCraft import*; fails with `import_no_file` (no such file, a name with a folder or a name that does not end in `.csv`), `import_not_an_export` (the header row has no time or glucose column) or `import_failed` (the file could not be read) |

## Notifications

All are Home Assistant persistent notifications: the summaries (they can also be sent to the notify actions chosen
in the settings; they compare consensus targets only on the consensus ranges, mark met / not met only for 14 days or
more with ≥70% data, and show mean, GMI and GRI as values without a target; notification ids `glucocraft_daily_summary`,
`glucocraft_weekly_summary`, `glucocraft_monthly_summary`), the optional no-data notice (`glucocraft_no_data_notice`; during a sensor warm-up only from 3 hours after it began,
with its own text; `SYSTEM_DESIGN.md` section 4.7), **GlucoCraft CGM Report** (`glucocraft_report_print`,
after each printing attempt: printed or not, the reason and the PDF kept), **GlucoCraft Wall-Panel Address**
(`glucocraft_panel_webhook`: a new wall-panel address was created; it names the settings screen where administrators
find the address and the id, and does not contain them), **GlucoCraft Panel Added**
(`glucocraft_panel_added_<device id>`: the panel's shape and the dashboard's display card), **GlucoCraft Panel
Offline** (`glucocraft_panel_offline_<device id>`: a panel's Status entity off or unavailable for 10 minutes; removed
when it is back), the CSV export's confirmation, **GlucoCraft export** (`glucocraft_export`), the CSV import's result,
**GlucoCraft import** (`glucocraft_import`), and, after a removal that kept the readings for a reinstall,
**GlucoCraft deleted** (`glucocraft_kept_readings`: where the kept readings are and how they are deleted).

Each kind of GlucoCraft notification has one place in Home Assistant's notifications (a fixed notification id): a new
daily summary, for example, replaces the previous one. The Panel Added and Panel Offline notices have one place per
panel. Every summary ends with one line that names it a notification (`notify.last_line`), in Home Assistant and on
the phones; the other notifications end with their own text. GlucoCraft sends no notifications about high or low
glucose.

The phone delivery test (*Send a test notification now* in the settings) is sent only to the notify actions chosen
for phones, not to Home Assistant's notifications: titled **GlucoCraft delivery test**, it contains no glucose value. A
delivery that fails is recorded as a warning in the Home Assistant log.

## Repairs issues

Home Assistant *Repairs* (translation keys in `strings.json` `issues`):

- `source_unit_unknown` (issue id `source_unit_<entity_id>`): after 10 values below 30 in a row from a source without
  a unit (they look like mmol/L; such values are not used); closes when a reading with a unit, or a value of 30 or
  more, arrives.
- `slow_source` (issue id `slow_source_<entity_id>`): a source's reading interval, the median of its last 10 live
  intervals, is 15 minutes or longer; such a source is not supported for live display (its readings are still stored
  and graphed); the notice says why, what to correct and that *No current data after* at 30 minutes reduces the "No
  current data" messages; it closes by itself when the source updates more often.
- `source_stuck` (issue id `stuck_source_<entity_id>`): a source without its own measurement time has delivered
  exactly the same value for 12 readings in a row, over at least 60 minutes from the first to the last (a source that
  keeps writing its last value looks the same as a steady glucose level); the readings are kept unchanged; it states
  why GlucoCraft cannot tell and what to check (the measurement-time attribute); closes when the value changes or a
  reading carries its own time.
- `hand_limit_low` and `hand_limit_high` (issue ids `hand_limit_low_<entity_id>`, `hand_limit_high_<entity_id>`): a
  source sent a number below a *Sensor lower limit* or above a *Sensor upper limit* set by hand (such a number is shown
  as LO / HI, so the number itself is never shown, although a source that sends it can measure there); the CGM's own
  codes (39 mg/dL, 2.2 mmol/L, the code 401 for HIGH) and a reading at the limit itself do not count; created once per
  source and run; closes with the next start of the entry (every settings change restarts it), and the next such number
  opens it again.
- `sensor_at_upper_limit` and `sensor_at_lower_limit` (issue ids `at_upper_limit_<entity_id>`,
  `at_lower_limit_<entity_id>`): the flat-run rule of `sensor_cap` at the automatic limit itself: the readings
  repeatedly stay at exactly the upper (lower) limit in use with *Automatic*, as a number, not as HI (LO); they are shown
  as measured readings; it states that such a source can be set by hand to that limit; checked hourly; closes when the
  limit is set by hand.
- `expiry_entity_missing`, `expiry_entity_unrecognized` and `expiry_entity_past` (one issue id, `expiry_entity`, its
  text by what is wrong; checked every 5 minutes): the *CGM sensor expiry entity* does not exist (once Home Assistant
  has started), has held a value that is no expiry at its last 10 checks, or gives an expiry more than a day in the
  past while readings still arrive; a notice, not a refusal (the choice may be deliberate); closes by itself when the
  cause goes or the setting is changed.
- `sensor_cap` (issue id `sensor_cap_<entity_id>`): at least 3 runs of 3 or more consecutive readings exactly at 400 or
  450 mg/dL, never above, while the resolved upper limit is higher; it suggests checking *Sensor upper limit*, the
  decision stays the user's; checked hourly; closes when the setting matches or a higher reading arrives.
- `sensor_floor` (issue id `sensor_floor_<entity_id>`): the same rule at the lower end: at least 3 runs of 3 or more
  consecutive readings exactly at 50 mg/dL, never below (a LO reading stored below 50 mg/dL counts as below), while the resolved lower limit
  is lower (a source that sends its lower limit instead of LO); it suggests checking *Sensor lower limit*; checked
  hourly; closes when the setting matches or a lower reading arrives.
- `range_below_sensor_limit` and `range_above_sensor_limit`: the very-low limit in the settings is below the sources'
  sensor lower limit, or the very-high limit above their upper limit, after a source or limit change; it names the
  stored value, the sensor's limit and the value in use, which is the limit; closes by itself when the setting fits
  again.
- `clock_behind` (fixable): stored readings, or the newest reading, are more than 5 minutes later than the system
  clock (also while `time_untrusted` is true); the fix removes those readings only on the user's confirmation; while
  GlucoCraft is not loaded, the fix ends with `not_loaded` and the issue stays. A
  readings file whose records later than a year after the clock end the file in time order (at least 3 of them, or all
  its readings) is read as a clock that is behind, not as damage: the readings are kept and this issue applies.
- `entity_ids_put_back`: no longer raised. In earlier versions it said that GlucoCraft had undone a change of an
  entity id; it was persistent, so every setup deletes one that is left.
- `entity_ids_changed` (fixable): an entity of GlucoCraft has another id than the one it was created with (found at
  setup, or 2 seconds after the last change while GlucoCraft runs; never undone by itself); it lists the changed ids, 5 and then how many more; the fix gives the
  original ids back on the user's confirmation (an original id another entity has is left alone and the issue stays
  open); closes by itself when every id is the original one.
- `source_mmol_too_high` (issue id `mmol_too_high_<entity_id>`): 10 readings in a row above 33.3 mmol/L from a source that
  states mmol/L (the values may be in another unit); they are still used, stored at the upper limit as HI; closes with the
  next value at or below 33.3 mmol/L.
- `source_mgdl_too_low` (issue id `mgdl_too_low_<entity_id>`): 10 readings in a row below 30 mg/dL from a source that states
  mg/dL (the values may be mmol/L); they are still treated as mg/dL, as no reading or stored at the lower limit as LO; a
  source's codes and texts neither count nor close it; closes with the next value of 30 mg/dL or more.
- `source_time_attribute` (issue id `time_attribute_<entity_id>`): 10 readings in a row without a usable time under the
  measurement-time attribute set in the settings; the readings take the time they arrive; closes with the next usable
  time or when the name is cleared.
- `fast_source` (issue id `fast_source_<entity_id>`): the primary's new values have kept replacing its own last reading for
  at least 10 minutes with none added (checked every 5 minutes); closes when a reading is added or the values have
  stopped for 5 minutes.
- `clock_ahead`: 3 consecutive live readings carry their own measurement time more than 1 hour before the system clock
  (the clock may be ahead, or the source delivers late; the status is *no current data* meanwhile); it names the newest
  measurement time and the clock; closes by itself with the first reading measured within that hour.
- `source_is_glucocraft` (issue id `source_is_glucocraft_<entity_id>`): a GlucoCraft entity was chosen as a glucose
  source; its values are not used; closes when another source is chosen. The Glucose sources screen refuses such an
  entity (field error `source_is_glucocraft`; `expiry_is_glucocraft` for the CGM sensor expiry entity).
- `source_unit_not_glucose` (issue id `unit_not_glucose_<entity_id>`): a source states a unit other than mg/dL or
  mmol/L; its values are not used as glucose readings; closes when the source reports mg/dL or mmol/L, or another
  source is chosen.
- `source_missing` (issue id `source_missing_<entity_id>`): a configured source does not exist in Home Assistant (after
  Home Assistant has started); closes when the entity exists again or another source is chosen. A new entity id of a
  source is followed while GlucoCraft runs, without a notice.
- `storage_save_failed` (issue id `storage_save_failed`): the readings file could not be written (the folder and the
  error are named); new readings are kept in memory until the next successful write; closes when the file is written
  again.
- `storage_recovered`, `storage_damaged`, `storage_unreadable` (issue id `storage_problem`): the readings file was
  damaged and its readings recovered, was damaged and kept aside, or could not be read (see `storage_problem` and
  `storage_kept_file` of Data history). `storage_recovered_not_kept` and `storage_damaged_not_kept`: the same two
  cases when no copy of the damaged file could be kept or it could not be moved aside. While the file could not be
  read, or a damaged file could not be kept aside, GlucoCraft does not write to it until GlucoCraft starts again (a
  restart of Home Assistant, or a reload of GlucoCraft, which saving its settings also does), and the readings in
  memory are lost then, except those Home Assistant's recorder still holds, which that start imports (the history
  import, `SYSTEM_DESIGN.md` section 7). `storage_newer` (issue id `storage_problem`): the file is in a
  format version newer than this version reads (an older version installed over a newer one); it is neither read nor
  written, and the readings in memory are lost when GlucoCraft starts again.
- `lovelace_resources_failed`: the card files could not be added as dashboard resources at the start (the error is
  named); GlucoCraft's dashboard and cards may not load; closes at the next start at which they are added.
- `dashboard_failed`: GlucoCraft's dashboard could not be added to the sidebar at the start (the reason is named and
  logged as a warning; the card files are registered all the same); closes when the dashboard is added at a later
  start or reload, when the setting *Dashboard in the sidebar* is off or the entry is disabled or removed, and at the
  next restart of Home Assistant.
- `history_import_failed`: the glucose source's history could not be read from the recorder (the error's class is
  named, never its text); closes when the history is read, and at the next restart of Home Assistant.

## Storage

`<config>/glucocraft/glucocraft_<entry_id>.bin`, magic "GC02", **version 3**: 10 bytes per reading (uint32 time,
uint16 raw × 10, uint16 reserved (written as 0, never used), uint8 limit 0 none / 1 low / 2 high, uint8 source 0 unknown / 1 primary / 2
secondary; `SYSTEM_DESIGN.md` section 7). New readings are appended; any other change is written through a temporary `.tmp` file; a damaged file is
kept aside as
`glucocraft_<entry_id>.bin.corrupt-<epoch>` (Repairs, `storage_problem`).

GlucoCraft also keeps:

- Home Assistant stores (`<config>/.storage/`): `glucocraft.<entry_id>_state` (the state kept across restarts),
  `glucocraft.<entry_id>_panels` (the wall panels already announced), `glucocraft.lab_a1c` (the laboratory A1C
  results) and `glucocraft.sensor_events` (the sensor events of the report);
- the folders `<config>/glucocraft_exports/` (the CSV exports, the last 12) and `<config>/glucocraft_reports/` (the
  report PDFs, the last 12).

**Removing the integration** with the setting *When GlucoCraft is deleted* (`on_removal`) at *Delete everything*
(`delete`) deletes all of these (the readings file with its temporary and damaged copies, every CSV export and report
PDF, the folders when they are then empty, and every store), GlucoCraft's Repairs issues and notifications, its
dashboard and card resources, and the recorded history and long-term statistics of every GlucoCraft entity in Home
Assistant's recorder (`__init__.py` `async_remove_entry`). At *Keep the readings for a reinstall* (`keep`, the
default) it deletes the same, except the readings file, the laboratory A1C results and the sensor events, which stay
in `<config>/glucocraft/` as `glucocraft_kept.bin`, `glucocraft_kept_lab_a1c.json` and
`glucocraft_kept_sensor_events.json` (`kept.py`), and creates the notification `glucocraft_kept_readings`; the next
setup asks whether to keep them (the new entry adopts them at its first start) or to delete them. This differs from switching an
optional feature off, which keeps the recorded history.

## Diagnostics

Home Assistant offers GlucoCraft's diagnostics file on the integration's page (the menu, *Download diagnostics*;
`diagnostics.py`). Its keys: `version`, `loaded`, `settings` and `options` (the configuration entry, redacted),
`language` (Home Assistant's and the one in use), `stored_readings` (counts by source, the number of limit
readings, the oldest and newest time, the span and the storage problem), `reading_interval` (the detected interval,
the interval of the source in use and its gap threshold), `sources` (by role, the primary and the second source: in use,
the entity's availability and unit, the integration, the sensor limits, the reading interval and the last
measurement; the expiry entity: availability and unit only), `status` (the display status
and the facts it is made from, no value),
`repair_issues` (the active issue ids), `panel_feed` (whether the feature is on and an address exists, and each
panel's model and firmware version), `custom` (`custom_ranges_in_use` and `custom_goals_set`, true / false, in place of
the redacted ranges and goals) and `regional` (Home Assistant's `country` and the regional formats it resolves to,
because the formats and some defaults follow the country); the expiry entity also carries the `format` GlucoCraft read
its value in (a code, never the value). It contains no glucose value, no reading and no laboratory result. Redacted:
the source and expiry entity ids, the phones' notify actions, the printer address, the id of the wall-panel address,
and the custom range limits and goals (ranges set by a clinician describe what a person's care team considers low and
high), and the value of every stored key that this version does not know (it may name an entity or a person); an
issue id that ends with a source's entity id ends with the source's role instead (redacted when that entity is not a
configured source); the panels' names are left out.

## HTTP API (Home Assistant login)

| Endpoint | Returns |
|---|---|
| `GET /api/glucocraft/data?period=6h` (any period above; another value gives 6h) | JSON in the display unit: `period` (the period used), `unit` (the display unit), `current` (the last reading, current or not: `no_current_data` says which; null during a limit reading, `limit` and `limit_text` say which) with `last_reading_ts` (its measurement time, epoch seconds; null without a reading), `ranges`, `stats` (the period's statistics; absent for a period without readings) and the period's **readings with their measurement times**; `trend` (the trend category, as the Trend arrow's `description`), `trend_arrow`, `no_current_data`, `limit`, `limit_text`, `detected_interval`, `reading_interval_seconds`, `gap_threshold_seconds`. `readings`: for periods shorter than 7 days, or with 400 readings or fewer, every measured reading as `{t, r}` (limit readings are left out, a gap on the graphs); otherwise at most 400 time slots `{t, r, min, max}` of equal length between the period's first and last measured reading (a slot without readings is left out; the time of the slot's first reading, the mean, the lowest and the highest reading of the slot), from the measured readings. `limit_runs`: the stretches of readings at the sensor's limit in the period, `[[start, end, "low" \| "high"], ...]` (epoch seconds, oldest first; a stretch ends at a measured reading, at a reading of the other kind or at a data gap, and runs to the end of the time its last reading represents; one that began before the period is cut at its start), which the graphs mark along their bottom (LO) or top (HI) edge. With `&daily=N` (1 to 200; another value adds nothing) also `daily`: the last N local days in Home Assistant's time zone, today so far included, oldest first, each `{date, mean, min, max}` (from the measured readings: the mean weighted by time with the gap rule, the lowest and the highest reading; unrounded; null for a day without data; `report.daily_values`), the Custom Ranges daily chart's values |
| `GET /api/glucocraft/stats` | JSON: `current` (the last reading, current or not), `last_reading_ts` (its measurement time, epoch seconds; null without a reading), `detected_interval`, `reading_count`, `unit`, `ranges`, `goals` (every goal key, null when no goal is set; the Configuration sensor leaves such a goal out) and `periods` (the statistics of every period with readings, keyed by period, plus the key `unit`, the display unit; glucose values in the display unit; GMI per the 14-day / 70% rule; each period with `enough_data`, `sensor_active` (unrounded %), `period_seconds` and `covered_seconds`: without enough data every statistic value is null) |
| `GET /api/glucocraft/binary?file=backfill` / `?file=delta` | the GLU2 v3 files the card and panels use (full history with the GCX1 extension / the recent-readings file, last 2 h). The full file keeps every reading of the last 24 hours and thins the older readings evenly when the file would otherwise exceed 960 KiB (the panels' download buffer; for example 200 days of 1-minute readings). All values are mg/dL, except the weekly pages' description lines, which are in the display unit. A period without enough data has an all-zero STATS record (mean 0, which every display shows as "---"). Both files end with three blocks a reader finds from the end by their magic: the GCR1 block (the stretches of readings at the sensor's limit in the file's span: uint16 count n, then n x (uint32 start, uint32 end, uint8 kind 1 LO, 2 HI), then "GCR1"; the graphs mark them, and the limit readings themselves are not in the readings), the GCM1 block (each period's unrounded minimum and maximum) and the GCL2 trailer (each period's seconds at the limit); a reader that does not know a block still finds the ones after it (`SYSTEM_DESIGN.md` section 8) |
| `GET /api/glucocraft/report?days=14\|30\|90\|180&full=1` | the Report's data (JSON; means and standard deviations in mg/dL unrounded, rounded once by the page and the PDF; `sd` and `cv` null for a single reading); every period dict (`standard`, `period`, `previous`, blocks) has `active` (sensor active, one decimal, or two where one decimal would show the 70% rule's value: 69.96), `enough_data` (at least 70% sensor active, compared before rounding: below it every value of the period is null, and a previous period is compared only when both have enough data; GlucoCraft rule), `limit_readings` and `limit_seconds`; a period that ends at the last midnight also has `more_needed` (`["days", n]` or `["hours", n]`: about how much more data, with no gaps, it needs to reach the rule; null when it has enough); `previous` is null for the 180-day (6-month) report; lab rows have `no_cgm_comparison` (bool) and `comparison_note` ("no CGM comparison (data older than 200 days)" or null), and `lab.retention_days`; `standard`, `period` and `previous` have `min` / `max` (measured values, mg/dL as stored); `sources` (the glucose sources' names, the primary first), `thresholds` (the values of the threshold placeholders of the report's fixed texts, `{t70}`, `{t70_180}`, `{unit}` and the others of `units.REPORT_THRESHOLDS`, in the display unit), `second_source_timed` (whether the second source carries measurement times; null without one or without its state) and `second_source_pct` (the share of the period's readings from the second source, one decimal, rounded once; null without readings); the other top-level keys: `ndays`, `full`, `label`, `lows`, `highs`, `quality`, `lab`, `retention_days`, `zones`, `include_custom_appendix`, `include_research_appendix`, `created`, `created_ts`, `printer`, `unit`, the regional keys of the Configuration sensor (`time_format`, `date_format`, `first_weekday`, `decimal_separator`, `a1c_unit`, `paper_size`), `integration_version`, `lab_limits`, `ifcc` and `license`, and, where they apply, `weekly` (the 90-day and 6-month reports), `blocks` (the 14-day blocks of a period longer than 14 days), `custom_appendix` and `research_appendix` (when asked for); the full report adds `low_events` (one entry per period of 14 / 30 / 90 / 180 days inside the report period: `days`, `active`, `events`, `level2`, `per_day`, `median_min`, `longest_min` (both null without events), `blocks` (8 counts by 3-hour block of the local clock), `night`, and `enough_data`; an entry with `enough_data` false has only `days` and `active`), `days` (the 2-hour patterns, the per-day table and the hourly log) and `monthly` (each calendar month the period touches, with each day's mean, low events and data %); a day's data % (`days.week[][3]`, `monthly[].days[][3]`) is a number with one decimal, or two where one decimal would show the 70% rule's value, as `active` |
| `GET /api/glucocraft/report.pdf?days=…&full=0\|1&color=0\|1` | the Report as a PDF (a copy is kept in `<config>/glucocraft_reports/`, the last 12; a copy that cannot be written is logged as a warning, and the PDF is returned all the same); `&include_custom_appendix=1` and `&include_research_appendix=1` add the optional appendices (off by default), on this endpoint and on `/report`; the custom appendix only while *Custom ranges and goals* is on (otherwise 400) |

Every glucose value of `/data` and `/stats` (`current`, the readings and slots, the ranges, each period's mean,
standard deviation, minimum and maximum) is unrounded, as the attributes: mg/dL as stored or calculated, mmol/L the
mg/dL value divided by 18.016, the ranges included, so that a reading and a limit compare in mmol/L exactly as in
mg/dL; a chart rounds each value once, as it shows it. Keys of `/data` and `/stats` (in the consensus terms): `ranges` = `very_low`, `low`, `tight_low`, `tight_high`,
`high`, `very_high`, `unit`, `is_default`; each period's statistics = `mean`, `std_dev`, `cv`, `gmi`, `min`, `max`,
`time_in_range`, `time_in_tight_range`, `time_low`, `time_high`, `time_very_low`, `time_very_high`,
`time_below_low` and `time_above_high` (all time below the low / above the high limit, level 1 and level 2 together;
the percentages, the CV and `data_availability` unrounded, rounded once by the cards), `reading_count`,
`data_availability`, `limit_readings`, `limit_seconds`; `goals` (`/stats`) = the keys of the configuration sensor's goals, values only.

Answers other than the data: every endpoint answers 404 with "GlucoCraft is not loaded." while no GlucoCraft entry is
loaded; `/binary` answers 404 for a `file` value other than `backfill` and `delta`, and 503 "No readings yet" without
readings; `/report.pdf` answers 503 when the PDF library is not available; `/report` and `/report.pdf` answer 400 for
the custom appendix while *Custom ranges and goals* is off, and 400 for a period the CGM Report does not have, with the
text from the language tables ("This report period is not available: 60 days. The CGM Report can be created for 14,
30, 90 or 180 days. Check the card's or automation's settings."), never another period without a word; the action
`print_report` refuses such a period with the same text. Defaults: `days` 14 (also for an empty value), `full=0` (the
summary), `color=1` (color). `full` and `color` also take true / false, on / off, yes / no; an empty value is the
default, and any other value is refused with 400 ("{name} must be 0 or 1 ...", `api.flag_invalid`).

The wall panels get the same files from a GET-only, local-only webhook (no Home Assistant token on the panel):
`GET /api/webhook/glucocraft_panel_<32 hexadecimal characters>?file=backfill|delta` (default `backfill`; 404 for
another value or while GlucoCraft is not loaded, 503 before any reading, 405 with `Allow: GET` for POST and PUT, 500
when the file could not be built, logged without the address id), over plain HTTP, which is not encrypted (the panels' ESPHome API connection to Home Assistant is encrypted with each
panel's API key). GlucoCraft shows the address and its id on the settings screen *Display card and wall
panels* (administrators; never in a notification). No address exists after the setup: *Create a new wall-panel
address* in the options creates the first one, which is needed before the first wall panel is built; ticked again,
it replaces the address, and the old address stops working at once, so every wall panel needs to be built and
installed again with the new id. The panels are described in `PANELS.md`; the binary format of the files is defined by the code
(`custom_components/glucocraft/panel_feed.py` and the panel header `esphome/glucocraft_panel_binary.h`).
