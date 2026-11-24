# GlucoCraft System Design

This document describes how GlucoCraft is built: its data flow, the rules of its calculations and the reasons for
them, its storage and file formats, its entities, its configuration and its files. It summarizes these detailed
references: `ENTITIES_AND_API.md` (every entity, attribute and endpoint), `DISPLAY_CONTRACT.md` (what the display card
and the wall panels show and why), `PANELS.md` (the wall panels), `DASHBOARD.md` (the dashboard), `LANGUAGE_STYLE.md`
(wording rules) and `ACCESSIBILITY.md` (accessibility rules).

---

## 1. Purpose and Scope

GlucoCraft is a Home Assistant custom integration designed to calculate and display continuous glucose monitoring
(CGM) data provided by other integrations. It is designed to calculate trend information and statistics as defined in
the published international consensus (Battelino et al., 2019), and to provide a dashboard, a CGM Report (with PDF and
printing), support for ESP32 wall panels and summary notifications. GlucoCraft's disclaimer (`const.py` DISCLAIMER,
shown in full at the top of the README) applies to everything below. Rule for every text of the system
(`LANGUAGE_STYLE.md` rule 14): never describe GlucoCraft or its values as accurate, reliable, usable, validated or safe,
never recommend using it, and never present it as helping to manage diabetes; the disclaimer stands once per surface
(rule 16). The design rules below describe how values are calculated; they are not claims that the results are
accurate.

### Who Installs It
- It is designed for any CGM system whose readings reach Home Assistant as a sensor entity (section 2); no CGM,
  service or integration is assumed.
- One installation serves one person and one CGM system (section 16).

### What GlucoCraft Is Not
- Not intended for any medical purpose, including diagnosing, monitoring, treating or managing diabetes or any other
  medical condition, or for making treatment decisions.
- Not cleared, approved or certified as a medical device by the FDA or any other regulator.
- Made available for development and testing; designed to calculate and display data with cited targets, with no
  evaluation of the values (`LANGUAGE_STYLE.md` rule 2); it sends notifications, prints reports and saves PDF and CSV
  files only as chosen in its settings or requested by the user.

- Its notifications (the summaries and the notices) are ordinary Home Assistant notifications, optionally also sent to
  the phones chosen in its settings, and are always called notifications (`LANGUAGE_STYLE.md` rule 26).

**No high or low glucose notifications.** GlucoCraft sends no notifications about high or low glucose. This text is
the same in the Documentation (*Notifications (Not Alarms)*) and in the README.

---

## 2. Supported CGM Sources

GlucoCraft is designed for any sensor entity in Home Assistant that reports glucose values in mg/dL or mmol/L; the
source's `unit_of_measurement` is checked on every reading. One or two sources can be chosen. A second
source is designed to supply readings that the primary source misses, up to 24 hours back, and to continue when it
stops, merged by measurement time (section 3). The integration
detects the data rate from the readings themselves.

### Detected interval
- The detected interval is the median of the intervals between the last 50 stored readings that are longer than 30 s
  and shorter than 15 minutes (`storage.detect_interval`). Only when the readings have no such interval (a source that
  reads every 15 minutes or less often), the median of their intervals from 15 minutes to one hour is used, so that
  such a source is shown with its own interval (900 s for a 15-minute source). The first rule decides whenever a
  shorter interval exists, so the interval of a 1- to 5-minute source does not depend on outages or on 15-minute
  history. With fewer than 10 readings, or no interval in either range, the interval stays as it was.
- It is detected at setup and again every 50 readings, stored in the GC02 header (section 7) and kept on the
  coordinator (300 s until known). It is the fallback of GlucoCraft's gap rule when fewer than 3 intervals exist
  around a reading (section 4.5; the cards and the panels have fallbacks of their own there).

### Reading interval per source
- The reading interval of a source is the median of its last 10 live measurement intervals (`coordinator.
  reading_interval`, `gaps.median_interval`); it is unknown until 3 intervals exist, and for the source in use without
  live readings it is the interval of the newest stored readings. It is published as `reading_interval_seconds` and,
  as max(15 minutes, 3 × it), `gap_threshold_seconds` (per source and for the source in use; the cards' fallback of
  the gap rule, section 4.5), and the slow-source notice uses it once a source has delivered at least 6 live readings.
- **Merging two sources** uses a separate interval (`coordinator._source_interval`): the median of a source's last 20
  live intervals that are from 20 seconds to 15 minutes long; until 3 such intervals exist, the detected interval for
  the primary (with 10 or more stored readings), otherwise 300 s. The same-measurement window is max(15 s, half the
  shorter of the two known intervals), and a second source without measurement times is used after the primary has
  been silent for max(5 minutes, 3 × the primary's interval).
- Smoothing adapts to the steadiness of the readings (section 5), not to a fixed number of readings.
- Statistics are time-weighted with the one gap rule (sections 4.2 and 4.5).

### Designed for data every 5 minutes or more often
With intervals of 15 minutes or longer, the trend arrow, the 5-minute delta, the no-current-data limit, gaps and
*sensor active* are not designed to function as described: such a source is not supported for live display, and the
displays can show "No current data" between its readings; readings are still stored and shown in the graphs, and
occasional dropouts of a faster source are not affected. A Repairs issue (`slow_source`) appears while a source's
reading interval is 15 minutes or longer (`SLOW_SOURCE_SECONDS = 900`): it says why, that a source that updates more
often corrects it, and that *No current data after* at 30 minutes reduces the "No current data" messages at the cost
of showing a real interruption up to 30 minutes late; it closes by itself when the readings arrive more often. The
same statement is in the Documentation (Known Limitations, Supported Sources), the README and next to the source
setting. The CGM systems and phone health apps that cannot deliver readings every 5
minutes or more often are listed under *Sources not supported for live display* (the Documentation's
Supported Sources, the master text; the README, a copy).

### A source that keeps sending its last value
A source without its own measurement time cannot tell GlucoCraft that it has stopped: each value is stored at the time
it arrives in Home Assistant, so a source that keeps writing its last value looks the same as a steady glucose level.
An unchanged value written again is no state change but Home Assistant's *state reported* event, which GlucoCraft
takes as a new reading at the time it was reported, at most one per max(20 s, 3/4 of the source's interval for
merging, `coordinator._source_interval`) and none before Home Assistant has started, when entities write old values
again (`coordinator._async_state_reported`); from a source with its own measurement time the same time is the same
measurement and is ignored.
After 12 readings in a row with exactly the same value, over at least 60 minutes from the first to the last, from such
a source, the Repairs issue `source_stuck` says so, explains why GlucoCraft cannot tell and what to check (the CGM
sensor and its app, an attribute with the time of each measurement to set as the measurement-time attribute, or a
source that provides one); the readings are kept unchanged, and the notice closes when the value changes or a reading
carries its own time (`coordinator.STUCK_READINGS`, `STUCK_SECONDS`; GlucoCraft rule).

---

## 3. Data Flow Architecture

```
CGM sensor (on the body)
        ↓
The CGM system's app, reader or receiver
        ↓
A cloud service or a local bridge (where the CGM system uses one)
        ↓
A Home Assistant integration that provides a sensor entity (optional second source)
        ↓
GlucoCraft coordinator (coordinator.py)
   ├── State listener (each reading with the CGM's own measurement time; two sources merged by measurement time)
   ├── Unit check (mmol/L readings converted to mg/dL on arrival; units.py)
   ├── Limit check (LO / HI and no-reading states; limits.py)
   ├── Storage (GC02 version 3 binary, persistent; 200 days)
   ├── Adaptive smoothing (mean over 20 / 15 / 10 minutes)
   ├── Slope, 5-minute delta, trend arrow (five trend categories)
   ├── Statistics engine (7 periods, time-weighted; ranges in use and consensus bands)
   └── Weekly statistics and Change Over Time (26 weeks; 14-day and 7-day windows; change.py)
        ↓
   ┌────┴─────────────────────────┬─────────────────────────────────┐
   ↓                              ↓                                 ↓
Entities (28 to 134)        HTTP API (Home Assistant login)    Panel webhook (GET, local only)
   ↓                        ├── /api/glucocraft/binary          └── GLU2 version 3 + GCX1 files
GlucoCraft dashboard        ├── /api/glucocraft/data                  ↓
(strategy, /glucocraft-home)├── /api/glucocraft/stats           Wall panels (5 types, ESPHome)
   ├── Display card         ├── /api/glucocraft/report          17 pages each
   ├── Pages card           └── /api/glucocraft/report.pdf
   ├── Statistics, history and Change Over Time cards
   ├── CGM Report page
   └── Documentation page
Notifications: persistent notifications (summaries, the optional no-data notice, panel added / offline, CSV export), optionally also sent to notify actions chosen in the settings; Repairs issues (section 16);
CSV export to <config>/glucocraft_exports/ (export.py)
```

**Merge by measurement time** (GlucoCraft rule; only the sources' own measurement times and intervals are used,
nothing specific to a source):
- The same measurement (within max(15 s, half the shorter of the two known intervals); 15 s while an interval is
  unknown) is never stored twice; the primary's copy wins.
- The second source fills only real gaps: no stored reading within 3/4 of the primary's interval. Fills are stored at
  their own measurement time, at most 24 hours back: a reading of either source measured more than 24 hours before
  both the system clock and the newest stored reading is not stored (`LIVE_LOOKBACK_SECONDS`; exactly 24 hours is
  stored), so that a clock far ahead never refuses the sources' correctly timed readings. Such a refusal, a time
  attribute without a usable time and a state that is no reading are logged once per source role and kind, again
  after that source's next good reading of the kind (INFO, never the entity id).
- After the primary's newest measurement, a reading of the second source is used only once the primary has been
  silent for one interval, so that a faster second source does not alternate with a primary that delivers.
- A second source without measurement times cannot be matched by time; it is used only after the primary has been
  silent for max(5 minutes, 3 × the primary's interval).

If either source stops, the other one continues at once.

---

## 4. Architecture Principles

These are fixed design rules: calculations follow the published methods, and the code is designed for any CGM. They
describe the method only; GlucoCraft makes no claim that its results are accurate.

### 4.1 Time Is the X-Axis, Always
Graphs plot readings by their timestamp, never by array index or buffer position. Gaps in data stay visible and time
is drawn to scale.

### 4.2 Time-Weighted Statistics
Each reading represents the time from itself until the next reading, not an equal weight. A reading that stands alone
for 10 minutes counts more than one in a burst of 1-minute readings. When the interval to the next reading is a gap
(section 4.5), or for the last reading of a window, the reading represents its own reading interval instead, so a gap
does not count as data (`gaps.weights`; one rule). *Sensor active* is the same covered time divided by the period
(`PeriodStats.covered_seconds`).

The CGM Report weighs every reading in the same way and with the same code: each reading's seconds (`gaps.weights`),
clipped to the report period of whole local days (the reading before the period counts with the seconds it reaches
into it), go to `statistics.compute_period_stats` as the weights. Every percentage, mean, standard deviation, CV, GMI,
GRI, the night and day shares, the 2-hour patterns and each day's mean and data percentage of the report come from
there. Only the display bins use whole minutes: the percentile curve of the glucose profile (15-minute bins of the
time of day, each pooled with its two neighboring bins: a 45-minute window, GlucoCraft rule; no percentile from fewer
than 3 minute values) and the daily profiles (10-minute slots of each day's clock) take one value per whole minute
that a
reading covers, to place the values for drawing (`report.minute_grid`).

**Sensor active label.** *Sensor active* measures data reception, not the time the CGM sensor was worn. Wherever the
percentage is shown it carries a label: "Sensor active (data received by GlucoCraft)" on the dashboard, in the
Documentation and in the README; "Sensor active (data received)" in the CGM Report, its PDF and the entity attributes
`label`. The sentence "Close to, but not the same as, the CGM system's own figure: only the readings received are
counted." stands once per surface (`LANGUAGE_STYLE.md` rule 21; `const.py` `sensor_active_label()` / `sensor_active_note()`, which
read the catalogue keys `report.sensor_active.*`, also the attributes `label` and `note` of the sensor active
sensors).

**Limit readings** (`limits.py`): a reading outside the sensor's measurement range (LO / HI) gives no value. It is flagged
`low` / `high`; each record of the readings file holds a number, so it holds the source's limit, which is never used as a
value. LO: a reading below the sensor's lower limit, or 39 mg/dL (2.2 mmol/L) and less; with
a *Sensor lower limit* chosen by hand (40 or 50 mg/dL), also a reading at the limit. HI: a reading above the upper
limit; with a *Sensor upper limit* chosen by hand (400, 450 or 500 mg/dL), also a reading at the limit. A limit set by
hand includes a reading at the limit because a source that never reports beyond a fixed value sends that value instead
of LO or HI; with *Automatic*, only a reading below or above the limit, because a sensor may measure the limit value
itself. "At the limit" is compared at the precision of the limits (whole mg/dL, or one decimal in mmol/L: 2.2 / 2.8 and
22.2 / 25.0 / 27.8 mmol/L; `limits.classify(..., lower_set=..., upper_set=...)`). A change of a limit applies to
readings received after it; stored readings are not changed. This method is a GlucoCraft rule. A limit reading is left
out of the mean, standard deviation, CV, GMI, the lowest and highest value, the trend, the 5-minute delta, the graphs and
the AGP curves, and counted as very low or very high in every time-in-range measure, the GRI and the events (a category
needs no number), with its time as sensor active. The count and the seconds per period are published
(`limit_readings`, `limit_seconds`) and stated on every period graph. A period with limit readings alone has no value for
the mean and the others (None; the displays show `---`). `sensor.glucocraft_last_glucose_reading` is unknown during a limit
reading, so that no limit value enters Home Assistant's history and statistics; `value_mgdl` and `last_reading_mgdl` then
hold the stored limit, for the panels' own state of the reading (the `limit` attribute marks it as no measurement). Why:
it tells only that glucose lies beyond what the sensor can measure. No consensus document addresses such readings, and
others differ: the manufacturer's report software shows sensor values below 40 mg/dL as 40 and above 500 mg/dL as 500
(Abbott Diabetes Care, 2018, p. 7), a community cohort study replaced them with 39 and 401 (Spartano et al., 2025), and a
trial's analysis plan leaves them out (Ultragenyx Pharmaceutical Inc., 2024, p. 21); so this is a GlucoCraft rule, and a
mean or GMI can differ from a manufacturer's. Leaving them out of the time in ranges as well would remove the lowest and
highest glucose from the time below and above range; storing a code that a CGM system sends for such a reading as a value
would present a number the CGM did not measure.

**No reading** (`limits.classify`; not stored, a gap): Home Assistant's unavailable and unknown, an empty text, ???,
---, any other text that is not a LO / HI text, a value of 12.99 mg/dL or less, also converted from mmol/L (error
codes 1 to 12), exactly 38 from a source not in mmol/L (an error marker), and anything below 39 mg/dL from a source
that uses those values as error codes. These are decided first; the LO rule "39 mg/dL (2.2 mmol/L) and less" applies
to the values left. A value of 6553.4 or more is the warm-up marker (section 4.7). A text such as "Below 50 mg/dL" or
"Above 400 mg/dL" is LO / HI, and a limit it names that is one of the settings' values (40 or 50, or 400, 450 or 500
mg/dL) becomes the source's limit under *Automatic*; exactly 401 from a source that uses the code 401 for HIGH is HI
and gives the upper limit 400 mg/dL (`limits.auto_lower`, `limits.auto_upper`).

**Each sensor's own limit:** the very-low threshold lies at or above the source's resolved sensor lower limit and the
very-high threshold at or below its resolved upper limit (`limits.resolve`: *Automatic* or the setting, per source;
with a second source the stricter of the two, `limits.threshold_span`). The ranges screen refuses a value outside
them, in either unit, compared at the precision it shows (2.2 mmol/L is accepted and stored as 40 mg/dL). Sensor first, then the ranges: a change on the
*Glucose sources* screen whose limits no longer fit the stored custom ranges leads to the *Glucose ranges* screen with
the field error, and the two are saved together once the ranges fit (`config_flow.async_step_sources`). A limit a
source shows only later (*Automatic*) changes that one threshold only: it is used at the sensor's limit
(`settings.ranges`), the other five limits stay as stored (a low limit at or below the very-low one then has no
readings: every reading below the sensor's lower limit is LO and very low; the same at the high end), never the default
ranges, with a Repairs notice (`range_below_sensor_limit` / `range_above_sensor_limit`) that closes by itself when the
setting fits again.

**One comparison rule for every output** (`limits.scale_position`, `limits.below` / `limits.above`): a LO reading lies
below every threshold and a HI reading above every threshold, so a LO reading is always very low and a HI reading
always very high (also at a threshold equal to the sensor's limit, and for a reading stored under another limit) in
the zone, the binary sensors, the statistics and the report's custom appendix; the displays take the zone as GlucoCraft gives it, with no case of their own. A state in mmol/L is converted to mg/dL first
(one decimal, as stored) and then compared with the limits: 27.8 mmol/L (500.8 mg/dL) is HI above an upper limit of
500, 25.0 mmol/L (450.4 mg/dL) HI above 450.

**A single reading** gives its value as the mean and no standard deviation and no CV (one value has no spread), on the
statistics sensors and in the report alike; the panel file carries 0 for them, its "no value".

**Performance:** the coordinator finds each period's window once by bisection (`storage.window_indices`) and passes
the stored reading intervals (`storage.reading_intervals`) instead of scanning the whole store per metric; the numbers
are identical to filtering the whole store (`tests/test_data_path.py`).

### 4.3 Store mg/dL Internally
All internal storage and computation uses mg/dL. Readings from a mmol/L source are converted when they arrive.
Conversion to mmol/L happens only at the display layer: the glucose sensors have the blood glucose device class with
native mg/dL, and Home Assistant converts their states to the display unit; the cards, panels, report and
notifications convert what they show. This prevents rounding errors from repeated conversions and keeps all internal
calculations in one unit.

**Conversion** (`units.py`): 1 mmol/L = 18.016 mg/dL. The published consensus thresholds are used as pairs in both
directions: 54 / 70 / 140 / 180 / 250 mg/dL = 3.0 / 3.9 / 7.8 / 10.0 / 13.9 mmol/L. mg/dL are shown as whole numbers,
mmol/L with one decimal; halves are rounded away from zero everywhere (Python `units.round_half_up`, JavaScript `rh1`,
C++ `gcu::r1_mmol`). A value is rounded once, where it is shown: the mean and the standard deviation of the statistics
(`PeriodStats`) and of the report data are kept unrounded, so that a mean of 129.46 mg/dL is shown as 129 (not 129.5,
then 130) and 127.36 mg/dL as 7.1 mmol/L (not 127, then 7.0). The percentages (the time in each band, the combined
bands, sensor active) and the CV are kept unrounded in the same way: in `PeriodStats`, every attribute, the HTTP API, Change Over Time and the panel feed (GLU2 STATS and TRENDS float32), so that an exact 69.45%
is shown as 69% (whole) or 69.5% (one decimal), never as 70% from a stored 69.5; the percentage sensors show one
decimal (Home Assistant's display precision). The state of the per-statistic percentage sensors (CV, sensor active, the
six custom time-in-band sensors) and of the 14 period summary sensors (their time in range) is that value rounded once
to one decimal, so that the state changes only when the value shown changes (a per-statistic sensor, whose attributes
do not change, then writes a recorder row only then; a summary sensor writes one whenever its unrounded attributes
change); no consumer reads those states. The state of the per-statistic mean, standard deviation, minimum and maximum sensors is
rounded once to the step shown in the display unit: whole mg/dL, or in mmol/L the mg/dL value of the value rounded to
0.1 mmol/L, which Home Assistant converts back with the same factor to exactly that tenth (a mean of 129.46 mg/dL is the
state 129 in mg/dL and 7.2 mmol/L in mmol/L, never 129.5 first); the state changes once at a change of the display
unit. Every rule compares the unrounded value. Where a value is compared with a
rule and one decimal would show the rule's value although the value is on the other side of it, the text shows two
decimals (`units.shown_against`: "Sensor active 69.96%" beside the 70% rule; 69.996% is shown as 69.99%). A rounded
zero is always "0" or "0.0", never "-0" or "-0.0" (`units.round_half_up` returns no negative zero; `Regional.dec`
turns a "-0.0" text into "0.0"). A difference of two values is the difference of the two values as shown: each is
rounded as it is shown, then subtracted (the summaries, the CGM Report's comparison with the previous period, Change
Over Time and the dashboard's change of the time in range, `gcPointsShown`), so that a difference always agrees with
the two numbers beside it.

**Display unit:** one GlucoCraft setting, *Display unit*, writes every glucose sensor's entity-registry unit;
everything follows the unit of `sensor.glucocraft_last_glucose_reading`, read from its entity-registry entry
(`unit_registry.shown_unit`), so every state written after a unit change, or after the reload of an options save,
carries the new unit at once. The glucose sensors suggest no unit, so Home Assistant scales their precision from
mg/dL (whole numbers) to one decimal in mmol/L; setup writes the display unit into the entity settings of each glucose
sensor that has none (`unit_registry.keep_display_unit`). Long-term statistics graphs of data recorded before a unit
change are a Home Assistant limitation; GlucoCraft's own store is in mg/dL and unaffected.

### 4.4 Adaptive to Data Rate
No fixed reading interval is assumed: the interval is detected from the data (section 2). A source that reads every
minute and a source that reads every 5 minutes are handled by the same code without configuration.

### 4.5 Gap Handling (the gap rule)
A **gap** is an interval between two readings longer than **max(15 minutes, 3 × the reading interval)**
(`const.GAP_FLOOR_SECONDS = 900`, `GAP_INTERVAL_FACTOR = 3`, `gaps.py`). The reading interval at a reading is the
median (upper middle) of the 10 intervals before it, filled with the intervals after it when fewer exist, never the
interval being judged; with fewer than 3 such intervals, the detected interval. One rule for the statistics weights
(section 4.2), *sensor active*, the report (hypoglycemia events: a gap ends an event and restarts a run below the
limit; the 15-minute start and end rule of Danne et al., 2017, Section 4, applies unchanged; section 6.4) and every
graph: the dashboard and the display card compute the same rule per stretch (`gcGapThrAt` / `gcGaps`), and so do
the wall panels on the readings they hold (`gc_units.h` `gcu::is_gap`; no file-format change). Only the fallback
with fewer than 3 intervals differs: GlucoCraft uses the detected interval, the cards `gap_threshold_seconds` of the
configuration (the source in use), the panels the 15-minute floor alone (`DISPLAY_CONTRACT.md`, Gap rule).
Dots are never connected across missing data. Percentages exclude intervals without data.

Why (GlucoCraft rule; no consensus document defines a data gap): 15 minutes, because some sources deliver history in
15-minute steps after a reconnection and cloud delivery has short synchronization dropouts, which would otherwise cut
graphs and remove time although readings exist on both sides; it is also the default of the no-current-data limit.
Three intervals, so that two missed readings of a slower source are not yet a gap. Per stretch, so that the rule
adapts when 1-minute readings follow 15-minute history or a second source has another interval. One rule, so that a
graph, the statistics and *sensor active* describe the same data.

### 4.6 Display Stores Every Reading
The wall panels keep the last 6 hours in a 180-slot ring: one reading per 2-minute clock slot (the newest wins). The
period graphs are drawn from the full file by timestamp (average and range per pixel slot). The full file
(`?file=backfill`) carries every stored reading; only when it would exceed the panels' buffer budget (960 KB) are
readings older than 24 hours thinned evenly (`panel_feed.py`).

### 4.7 No Current Data: One Fact
`coordinator.no_current_data` is the one fact about whether the data are current: no reading, a reading older than
the no-current-data limit (*No current data after*, 5 to 30 minutes, default 15; a stored value above 30 is read as
30, `settings.STALE_THRESHOLD_LIMITS`), or a reading more than 5 minutes later than the system clock. It is set at every reading and
every minute and published as `binary_sensor.glucocraft_no_current_data` (`threshold_minutes`). Everything that
depends on current data tests it, never the status: the trend values (`coordinator.trend_current` = not no current
data and not a warm-up, so after the sensor's stated expiry the trend follows the data), the zone word under the value,
the in-range sensors, the Spoken summary, the summaries' last-reading line, the dashboard's banners and Last
Reading, the display card and the wall panels (which also count the reading's age themselves while Home Assistant
cannot reach them), and the optional no-data notice. The status (`display_status`, priority warm-up > expired > no
data > no current data > expiring soon > current; expiring soon: the stated expiry 24 hours away or less,
GlucoCraft rule, `const.EXPIRING_SOON_MINUTES`) decides only the status label and color, and which words the displays
show without current data.

**A warm-up ends by itself after 24 hours** (`coordinator.WARMUP_MAX_SECONDS`; GlucoCraft rule): a warm-up begins when
the expiry moves more than a day later or the source sends a warm-up marker, and ends with the first reading measured
after it began, or 24 hours after it began, whichever comes first; then the normal no-current-data rule, its words and
its notice apply. A warm-up normally takes much less time than 24 hours, so a sensor that has not started is not
shown as warming up for long.

**A reading time ahead of the clock:** while the newest reading's own time is more than 5 minutes ahead of the clock
(`coordinator.time_untrusted`, published as the attribute `time_untrusted` of `sensor.glucocraft_last_reading_time`),
the reading's age is not known: the flag is on, the displays show "Reading: ---" in the status bar and no time line
under the words, the Repairs issue `clock_behind` is always open meanwhile, and the dashboard's Maintenance page
explains what happened and what to check (Home Assistant's clock and time zone, the source's clock, a source that
sends its times without a time zone, which is read in Home Assistant's time zone). A measurement time later than the
system clock is stored as the clock's time (`coordinator._reading_time`, and the history import, `history_import`), so
a reading time ahead of the clock comes from readings stored before the system clock was set back, for example a
clock that was ahead and has been corrected.

Without a current reading the displays show words in the value's place, never a number: "Sensor warming up" during a
warm-up; while the flag is on, "Sensor expired" after the stated expiry, else "No current data"; under them the last
reading ("Last reading: 64 mg/dL") and when it was taken ("Today at 17:39 (2 h 0 min ago)"), both left out
without any reading. The zone word is then
empty, the trend and the 5-minute delta show "---", and the status bar keeps the reading's age ("Reading: 2 h ago").
All three words throb (a fade to about 30% brightness and back, one cycle every
2 seconds) for the first 30 minutes of each episode and then hold still for as long as it lasts (counted from the
reading's age, so a panel reboot does not start the 30 minutes again), unless the setting *Fade the words when there is
no current reading* is off; the display cards also stop it for the system's reduced-motion setting (`ACCESSIBILITY.md`
rule 6, its stated exception). After the stated expiry, while readings still arrive, the value and its zone word keep
their zone color, the trend follows the data, and the red status bar carries the status: its "Sensor expiry passed"
throbs the same way for as long as the expired status lasts, until a new sensor is detected, without the 30-minute
limit (small text, and a new sensor can take a while to be put on); the same setting and the reduced-motion setting stop
it (`DISPLAY_CONTRACT.md`). While a GlucoCraft Repairs issue is open and not ignored (the attribute `attention` of
`sensor.glucocraft_configuration`), the status bar of the display card and the panels also shows "Check Home
Assistant", taking turns with "Custom ranges" and the warm-up text. The
gap rule (section 4.5) is separate and fixed: it asks whether a past stretch has data
and gives every installation the same statistics, *sensor active* and report.

**The no-data notice** (optional, both parts off by default; `notification.NoDataNotice`): a status notice about
GlucoCraft itself, never about glucose. Once the flag is on and the last reading is as old as *Notify after* (15,
30, 60 or 120 minutes, default 30; never earlier than the limit, because the flag must be on), one Home Assistant
notice (`glucocraft_no_data_notice`, setting *No-data notice*) and, independently, one message to the phones (the
phone type `no_data_notice`): "GlucoCraft has received no readings since 1:02 AM." During a warm-up the notice is
not sent for the first 3 hours (no readings are expected); from 3 hours into a warm-up it runs with its own text,
also without any reading: "The sensor warm-up began at {time} and no reading has arrived since. A warm-up usually takes
much less time than this. Check the CGM sensor and its app." (`notification.WARMUP_NOTICE_SECONDS`; GlucoCraft rule).
One per outage: the outage whose notice was sent is kept across restarts (`no_data_notice_for` in the state store),
so a restart re-creates only the Home Assistant notice, which a restart removes; the notice is removed when the flag
goes off, with no message when the readings return. It depends only on the flag, the time of the last reading and
its settings, never on a glucose value, a range or a trend.

---

## 5. Raw and Smoothed Data

**Two data streams exist. Both are stored. Each has specific, distinct uses.**

### Why This Distinction Matters
CGM systems apply their own filtering before they report values. What this document calls "raw" is the value the CGM
system reports, not an electrical signal. GlucoCraft calculates every statistic from these reported values without
additional smoothing (GlucoCraft rule); this is not a claim that its results are accurate or match any other report.

### Raw Data (CGM-reported values)
- What the CGM sensor entity reports to Home Assistant.
- **Used for:**
  - the current glucose value (the main number shown everywhere);
  - all graphs (6 hours through 6 months, on the dashboard, the display card and the wall panels);
  - all statistics: time in ranges, mean, standard deviation, CV, GMI, GRI, minimum and maximum;
  - the weekly values and Change Over Time (calculated from the raw readings per window);
  - the GLU2 version 3 files served to the display card and the wall panels.
- **Why raw for statistics:** smoothing before calculating statistics would reduce the variability (CV) and change the
  time in each range. GlucoCraft therefore calculates from the reported values; this is not a claim that its numbers
  are accurate or match any report.

### Smoothed Data (GlucoCraft adaptive mean)
Adaptive smoothing (GlucoCraft rule; `coordinator.ADAPTIVE`), on each reading's own measurement time:
1. the range (maximum − minimum) of the last 20 minutes: < 10 mg/dL, < 25 mg/dL, or more;
2. smoothed level = the mean over 20 / 15 / 10 minutes (the steadier, the longer);
3. slope (the rate of change the trend shows) = the change of that mean over the same window, per minute, capped at
   3 / 5 / 8 mg/dL/min (`coordinator.slope`);
4. 5-minute delta = the change of the smoothed level over 5 minutes (mg/dL, unrounded: each display rounds it once,
   whole mg/dL or 0.1 mmol/L).

Why adaptive: a fixed short window follows every short dip and would turn it into a quickly falling or rising trend
while the readings are steady.

- **Used for** (inside the coordinator only; no entity publishes the smoothed level or the slope as a value of its own):
  - the trend arrow and the trend word (the five trend categories on the slope rounded to 0.1, section 9), and the
    panels' arrow angle;
  - the 5-minute delta (`sensor.glucocraft_5_minute_delta`, display name 5-minute delta).
- **Never used for:** statistics, the current glucose value, graphs.
- **No forecast:** GlucoCraft shows no projection of future values; a straight-line extrapolation is not a prediction.
- **Why smoothed for the delta and the trend:** the noise of individual readings would make trend arrows change
  rapidly; the smoothed level gives a steadier direction.

### What Is Stored
- The raw readings are the basis of every statistic, and the only glucose values stored (GC02).
- The smoothed level is not stored: after a restart the slope and the trend are recalculated from the stored
  readings, and stay unknown while there are not enough earlier readings.

---

## 6. Clinical Standards and References

GlucoCraft's calculations follow the international consensus on CGM metrics (Battelino et al., 2019; Danne et al.,
2017) and the standardized glucose reporting of Bergenstal et al. (2013).
Where sources differ, the calculations follow the treatment standards; newer research is shown as context only, named
as such (`LANGUAGE_STYLE.md` rule 15).

### 6.1 Time in Range Targets
From the international consensus (Battelino et al., 2019; tight range: Battelino et al., 2023):

| Metric | Range (mg/dL) | Range (mmol/L) | Consensus target |
|--------|---------------|----------------|------------------|
| Time in range (TIR) | 70–180 | 3.9–10.0 | >70% |
| Time in tight range (TITR) | 70–140 | 3.9–7.8 | no consensus target |
| Time below range | <70 | <3.9 | <4% |
| Time very low (level 2) | <54 | <3.0 | <1% |
| Time above range | >180 | >10.0 | <25% |
| Time very high (level 2) | >250 | >13.9 | <5% |

**Consensus and custom values.** Users can set custom ranges and custom goals (section 13). The Today, Consensus
Ranges and CGM Report pages, the GRI and the per-period `…_consensus_ranges` sensors always use the consensus ranges
above; the ranges in use (the custom ranges while *Custom ranges and goals* is on) are used on the Custom Ranges page,
by the period sensors whose time-in-band names begin "<period> custom time …", by the display card and by the wall panels. The
two are never swapped or mixed. Default custom goals: time in range >70%, time below range <4%, time very low <1%,
time above range <25%, time very high <5% (Battelino et al., 2019) and CV ≤36% (Battelino et al., 2019; Monnier et al.,
2017); no default goal for the time in tight range and the GMI, because no consensus target exists for them.

### 6.2 Glucose Management Indicator (GMI)
Formula: **GMI (%) = 3.31 + 0.02392 × mean glucose (mg/dL)**; in mmol/mol: 12.71 + 4.70587 × mean glucose (mmol/L)
(Bergenstal et al., 2018).

- Calculated only for periods of 14 days or more with at least 70% sensor active (Battelino et al., 2019;
  `statistics.gmi_shown`); the 70% rule is applied to the unrounded *sensor active* value.
- The GMI estimates A1C from CGM data; it is not a laboratory A1C (Bergenstal et al., 2018).
- No consensus target; GlucoCraft sets no default goal for it.

### 6.3 Coefficient of Variation (CV)
- **CV = (standard deviation / mean) × 100%**.
- Consensus target: ≤36% (Battelino et al., 2019, adopting the threshold of Monnier et al., 2017).
- GlucoCraft shows the CV as a number compared with this target only (met / not met); it adds no bands or words of
  its own.

### 6.4 CGM Report
The CGM Report shows the standardized metrics and glucose profile recommended for the Ambulatory Glucose Profile
(Battelino et al., 2019; Bergenstal et al., 2013). It is GlucoCraft's report page (`glucocraft-report.js`,
`report.py`) and its PDF (`report_pdf.py`); the PDF files are named `glucocraft-cgm-report_…`.

**Scope and layout:**
- Consensus ranges only in the main report, never the custom ranges; full local days ending at the last midnight.
- Periods of 14, 30 and 90 days and 6 months; page 1 is always the 14-day summary, which stays about two
  pages.
- Glucose statistics (mean, GMI, CV, sensor active), time in ranges, the glucose profile (5th to 95th percentiles),
  daily profiles and daily patterns; each value is shown with its cited target. Night is 00:00 to 05:59 local time
  (Battelino et al., 2023, Panel 2).
- The number of readings at the sensor's limit under the statistics, with their time (for example "2 readings at the
  sensor's limit (LO 1 (5 minutes); HI 1 (5 minutes)), left out of the values and counted in the time in ranges, as
  described under About This Report."; GlucoCraft rule). They leave the glucose curves and the hourly log's numbers;
  in the log LO or HI takes the place of the hour's minimum or maximum.
- No previous-period section in the 6-month report; a laboratory A1C result older than about 110 days is listed with
  "no CGM comparison (data older than 200 days)" (section 7).
- **Data quality page:** sensor active per day, data gaps (count, total time, longest; the gap rule, section 4.5), LO /
  HI readings (counts), sensor changes and warm-ups (dates), with the sensor-active label (section 4.2).
- **High glucose:** the time above 180 and above 250 mg/dL at night and by day, in the same form as the hypoglycemia
  section and with the same night definition; consensus measures only, no high-glucose events in the main report.
- **26-week weekly trend** (90-day and 6-month reports): time in range, CV and mean per week, the weekly values of
  section 9.
- **Hypoglycemia events by period** (full report only, after the summary's sections; `report.low_event_summary`): one
  table, a column for each period of 14, 30, 90 and 180 days that lies inside the report's period, each ending at the
  last midnight and headed by its sensor active; rows: the level 1 events, those that reached level 2, the events per
  day (two decimals), the median and the longest duration, the event starts in eight 3-hour blocks of the local clock
  and at night (00:00-05:59; Battelino et al., 2023, Panel 2). No lowest value (an event's lowest value is not the
  period's minimum), no event list, no targets, colors or assessment.
- **Longer-Term Overview:** also the period's lowest and highest measured value.
- **Section layout** (the PDF and the page alike): every section opens with one caption line (its rule and citation),
  shows its data and ends with *Notes*, the full cited explanation, in the smallest text size of the PDF. Page 1 keeps
  its 14-day layout with its captions; its Notes start at the page foot. Notes that reach the next page
  continue at its top under "<section> (continued)", a running head that is an artifact of the tagged PDF. The reading
  order of every section is caption, data, Notes. *About This Report* holds the general method only: the data sources,
  the report period, the data and their gaps, the readings at the sensor's limit, the rounding.
- **Data sources:** every page header names the glucose sources, "Glucose data: <primary>; second source: <second>.",
  each by the entity's name, else its entity id (the one place where the report names something of Home Assistant).
  The headers after page 1 are artifacts, so *About This Report* names the sources in its text: "Data source." with
  one source; with a second source the way it fills gaps, when a second source without measurement times is used
  (section 2) and the share of the period's readings that came from it, from the source byte stored with each reading
  (section 7), rounded once to one decimal (never 0.0% or 100.0% unless exact; `units.shown_against`).
- References show the DOI as a URL (https://doi.org/...) only.

**Hypoglycemia events** (`report.report_events`): the rule of the clinical consensus, Danne et
al., 2017, Section 4. Neither the Standards of Care (American Diabetes Association Professional Practice Committee for
Diabetes, 2026a) nor Battelino et al. (2019) define a start or end rule for an event.
- **Level 1** starts after at least 15 consecutive minutes below 70 mg/dL (readings below 54 included); **level 2**
  starts after at least 15 consecutive minutes below 54 mg/dL.
- An event starts with its first reading below the limit, at that reading's measurement time (a reading at 10:30:40
  starts it at 10:30:40, shown as 10:30, not at the next whole minute of the minute grid), and its minutes are the time
  its readings represent, to the second, so that a first reading covering less than one whole grid minute still
  starts it.
- **Every event, level 2 included, ends after at least 15 consecutive minutes at or above 70 mg/dL**, so a level 2
  event continues while glucose is between 54 and 69 mg/dL (`report.EVENT_END = 70`). Level 2 events are counted
  separately and also counted as level 1.
- A data gap ends an event (GlucoCraft rule, section 4.5).
- Its duration ends at the first reading of the closing 15 minutes at or above 70 mg/dL. Each event is found once
  over all the report's readings, from one day before the report's earliest period, the previous period included
  (`report.EVENT_LOOKBACK_DAYS`), and counted once, in the period, 14-day block and day in which it starts, also when
  it continues past midnight (GlucoCraft rule; `report.events_in`).
- Context only (the Documentation, one sentence): for clinical trials, Battelino et al., 2023, Table 4 ends a level 2
  event after 15 minutes at or above 54 mg/dL.

**Optional appendices**, off by default (the options `include_custom_appendix` and `include_research_appendix` of the
report page, of the report endpoints and of `glucocraft.print_report`; `LANGUAGE_STYLE.md` rule 22):
- *Custom Ranges and Goals*: the statistics with the ranges and goals in the settings, labeled as the user's own
  settings; met / not met only from 14 days with at least 70% data. Offered only while *Custom ranges and goals* is on
  (section 13).
- *Measures Defined for Research*: extended high-glucose events, with exactly the text and method below
  (`report.extended_high_events`; the report catalogue's `report.research.*` texts, quoted word for word). No color, no ✓ / ✗,
  no target; only in this appendix, not on the dashboard or the panels.

**The research appendix text.** Its definition follows the endpoint panel of Battelino et al. (2023, p. 48), the one
version in that statement that gives both a start and an end; the appendix names the Table 4 variant openly.

> **Measures defined for research.** This appendix shows measures that research publications define but that the
> clinical-care standards do not. They are not part of the main report, they have no targets, and they are shown
> without color or comparison.
>
> **Extended high-glucose events.**
>
> *Definition.* An event is a period in which sensor glucose stays above 250 mg/dL (13.9 mmol/L) for at least 120
> minutes. It ends when glucose has returned to 180 mg/dL (10.0 mmol/L) or below for 15 minutes (Battelino et al.,
> 2023, p. 48). This definition was made for counting events in clinical trials: the 2023 international consensus
> statement lists the "extended hyperglycaemic event rate" as a secondary endpoint for clinical studies (Battelino et
> al., 2023, p. 48). The same statement also describes extended hyperglycemia as 90 cumulative minutes above 250 mg/dL
> (13.9 mmol/L) within a 120-minute period (Battelino et al., 2023, Table 4); this report uses the version that
> states both a start and an end.
>
> *No clinical-care standard.* The clinical-care consensus describes high glucose by the time above 180 mg/dL
> (10.0 mmol/L) and above 250 mg/dL (13.9 mmol/L) and sets targets for those times (Battelino et al., 2019); the ADA
> Standards of Care do the same (American Diabetes Association Professional Practice Committee for Diabetes, 2026a,
> Table 6.2). Neither defines
> high-glucose events or a target for their number. The 2023 definition exists only for research, so the count has no
> published target and no clinical interpretation.
>
> *Method* (GlucoCraft rules where the source does not specify):
> 1. The readings are the CGM values received for this report, in mg/dL; a HI reading counts at the sensor's upper
>    limit (always above 250 mg/dL). The limits in mmol/L are 13.9 and 10.0.
> 2. Start: a run of consecutive readings, each above 250 mg/dL, becomes an event once the time it covers reaches 120
>    minutes. The time a run covers is measured as in all statistics of this report: each reading counts until the
>    next reading, or for its own reading interval before a data gap. The event's start time is the time of the run's
>    first reading.
> 3. During the event, readings from 181 to 250 mg/dL do not end it.
> 4. End: the event ends at the first reading of the first run of readings at or below 180 mg/dL that covers 15
>    minutes. Its duration is the time from its start to that reading.
> 5. Data gaps: a data gap (an interval between two readings that is longer than 15 minutes or three reading
>    intervals, whichever is longer) ends a running event at the end of its last reading before the gap, which counts
>    for its own reading interval. A run above 250 mg/dL interrupted by a gap is an event only if it had already
>    covered 120 minutes before the gap.
> 6. Counting: each event is counted once, in the period and on the day in which it starts, also when it continues
>    past midnight. The appendix shows the number of events, their total duration and the longest event for the report
>    period.
>
> *Limits.* Only the readings received for this report are counted. An event during a gap in the data is not counted,
> or is counted shorter. The CGM system's own data can contain readings that were not received for this report.

**PDF, printing and laboratory results:** `/api/glucocraft/report.pdf` (`report_pdf.py`, ReportLab), tagged for
screen readers (`pdf_tags.py`; `ACCESSIBILITY.md` rule 4); copies kept in `<config>/glucocraft_reports/` (the last
12); printing to a network printer over IPP (`glucocraft.print_report`); laboratory A1C entries for comparison with
the GMI (Bergenstal et al., 2018) of the 90 days before each result (`glucocraft.add_lab_a1c` / `remove_lab_a1c`; the
90-day window is a GlucoCraft rule).

### 6.5 Glycemia Risk Index (GRI)
GRI = 3.0 × very low % + 2.4 × low % + 1.6 × very high % + 0.8 × high % (capped at 100), that is 3.0 × the
hypoglycemia component (very low + 0.8 × low) + 1.6 × the hyperglycemia component (very high + 0.5 × high). Zones A to
E are the quintiles of the GRI, from the lowest (first to 20th percentile) to the highest (81st to 100th percentile)
(Klonoff et al., 2023). GlucoCraft takes the zone from the GRI value as it is displayed, rounded to
one decimal: A up to 20, B up to 40, C up to 60, D up to 80, E above 80 (GlucoCraft rule; `statistics.gri_zone`), so
that the zone shown next to a number always belongs to that number (20.0 is zone A and 20.1 is zone B on every
surface). The GRI is always calculated on the consensus bands, the bands its weights were fitted to, and is shown only
for periods of 14 days or more with at least 70% sensor active (GlucoCraft rule, as the GMI; `statistics.gri_shown`):
the 6-hour, 24-hour and 7-day periods, and the 7-day values of Change Over Time, have no GRI. The weekly GRI is the
14-day GRI at each week's end (`coordinator.trend_gri14`, as `trend_gmi14`).

### 6.6 Key Clinical References
See section 14 for the full references.

---

## 7. Storage Format (GC02 version 3)

Why a store of its own: Home Assistant's recorder keeps 10 days of states by default and its long-term statistics
are hourly aggregates, so neither can give the glucose profile's percentiles, the hypoglycemia events or a 6-month
report. GlucoCraft therefore keeps every reading in its own file and reads the recorder only to fill a new store (the
history import below), so the recorder's retention does not need to be raised.

File: `<config>/glucocraft/glucocraft_<entry_id>.bin` (`storage.py`). **Retention: 200 days, fixed** (no setting; a
stored settings key that GlucoCraft does not know is never read and is not written back at the next settings save).
Old readings
are purged as **whole local days**, at setup and at the first tick after local midnight, so the oldest stored day is
complete; the Home Assistant recorder is never touched.

Why 200 days: they cover the 6-month report (180 days), the 90-day report with its previous period (180 days),
the weekly pages (26 weeks of 14-day values: the oldest window starts 25 × 7 + 14 = 189 days back) and Change Over Time. Not available: a previous period for the
6-month report (about 360 days, 2 × 180; no cited source asks for it) and a CGM comparison for laboratory A1C results older
than about 110 days ("no CGM comparison (data older than 200 days)"; the comparison uses the GMI (Bergenstal et al.,
2018) of the 90 days before the laboratory date; the 90-day window is a GlucoCraft rule). The **CSV export** (`glucocraft.export_csv`, `export.py`) keeps readings
available outside GlucoCraft.

### Binary File Structure
```
HEADER (16 bytes):
  Bytes 0-3:   Magic "GC02" (4 bytes)
  Byte 4:      Version (uint8, currently 3)
  Byte 5:      Flags (uint8, reserved)
  Bytes 6-7:   Detected interval in seconds (uint16, little-endian)
  Bytes 8-15:  Reserved (8 bytes)

READINGS (10 bytes each, repeating; version 3):
  Bytes 0-3:   Timestamp (uint32, Unix epoch, little-endian) - the CGM's own measurement time where the source gives it
  Bytes 4-5:   Raw glucose × 10 (uint16, mg/dL, for example 120.5 mg/dL = 1205; a LO / HI reading: the source's limit)
  Bytes 6-7:   Reserved (uint16, written as 0; its value is never used)
  Byte 8:      Limit (uint8: 0 none, 1 low = LO, 2 high = HI)
  Byte 9:      Source (uint8: 0 unknown, 1 primary, 2 secondary)
```
Only version 3 is read. A file with a newer version in its header (written by a newer GlucoCraft) is neither read nor
written (problem `newer`, Repairs `storage_newer`; as `async_migrate_entry` refuses a newer entry); a file with an older
version is handled as a damaged header (its records are recovered when they are plausible version 3 records;
otherwise the file is kept aside). The times are unsigned 32-bit seconds (Unix time, UTC), which end on 2106-02-07; a
reader that took the field as a signed 32-bit number would end in January 2038 (the module docstring of `storage.py`).

### Design Decisions
- **×10 encoding:** stores 0.1 mg/dL steps in a uint16 (range 0–6553.5 mg/dL, far beyond any CGM range).
- **Appends and atomic writes:** new readings after the newest saved one are appended and fsynced (a changed detected
  interval is written in place); saved readings are never written again, and a record cut short by a loss of power is
  left out at load. Every other change writes the whole file to a .tmp file, fsyncs it and replaces the old file; a
  .tmp file left behind is removed at the next load.
- **Damaged file kept:** a file that cannot be parsed is moved aside as `.corrupt-<epoch>` (never overwritten); when
  only the header is damaged and the body parses as plausible readings (at least 95% of the records with a time after
  2015 and a value of 20–500 mg/dL; GlucoCraft rule), the readings are recovered, an exact copy is kept as
  `.corrupt-<epoch>` and the next save writes a new header; a read error blocks saving until GlucoCraft starts again
  (a restart of Home Assistant, or a reload of GlucoCraft, which saving its settings also does: each start opens the
  file anew).
  Each case raises the Repairs issue `storage_problem`, with the translation key `storage_recovered`,
  `storage_damaged`, `storage_unreadable` or `storage_newer`, or `storage_recovered_not_kept` / `storage_damaged_not_kept` when no copy
  could be kept (saving is then blocked until GlucoCraft starts again), and is shown on the Data history sensor
  (`storage_problem`, `storage_kept_file`).
- **Sorted, no duplicates:** enforced on load; values outside 20–500 mg/dL are dropped.
- **A clock that is behind is not damage:** a plausible record's time lies from 2015 on and not more than a year after
  the clock; but records later than that which end the file in time order (at least 3 of them, or all the file's
  readings) are read as a clock that is more than a year behind, not as damage: they are kept, and the clock rule below
  applies (`storage._clock_behind_end`; GlucoCraft rule). A single record far in the future among the readings
  (overwritten bytes) breaks that order and remains damage. So no reading is dropped because of the clock alone.
- **A wrong clock never deletes readings:** readings later than the system clock are kept. While the clock is more
  than 5 minutes earlier than the newest stored reading, no reading is stored, nothing is purged, the history import
  does not run and the status is *no current data*; the fixable Repairs issue `clock_behind` explains it, closes by
  itself when the clock catches up, and lets the user discard the later readings (only on that confirmation). Once
  the clock is no longer behind, or after that discard, the primary's readings refused meanwhile are imported from
  the recorder: only those measured after the newest stored reading at the first refusal, or after the discard
  (`coordinator.async_catch_up`, `storage.fill_after`); a reading without its own measurement time from that time
  carries the clock's wrong time and is not imported. The
  retention cutoff is measured from the earlier of the clock and the newest stored reading, so a clock that jumps
  ahead cannot purge more than the retention allows. A clock more than 24 hours ahead does not refuse the readings
  that carry their own measurement time (the live 24-hour rule also compares with the newest stored reading);
  readings without one are stored at the clock's time, and once the clock is corrected they are later than it and
  the `clock_behind` issue lets the user discard them. While 3 consecutive live readings carry their own measurement
  time more than 1 hour before the clock, the Repairs warning `clock_ahead` says that the clock may be ahead (or the
  source late); it closes by itself with the first reading measured within that hour (GlucoCraft rule: the two
  thresholds keep a normally late source from raising it).
- **History import:** each time GlucoCraft loads with fewer than 100 stored readings (with more, only the readings
  after the newest stored one: see the end of this item), not while the clock is behind
  the stored readings, and for the primary source only (so a change of source with readings already stored imports
  nothing for the new source): every recorded state with its attributes (`get_significant_states`,
  `significant_changes_only=False`), so a repeated value is imported; each reading at its measurement time (the time
  attribute, else `last_updated`), one row per measurement time. The states are parsed in recording order, each with
  the newest measurement time before it (also across the one-day parts of the read), as the live readings are, so a
  naive local time in the repeated hour of a daylight-saving fall-back gets its second occurrence once the first has
  passed. Exactly the whole local days the retention keeps are
  read: from the retention's first local midnight, counted from the day of the newest recorded reading
  (`history_import.async_rows_kept`, `storage.retention_start`), so the oldest stored day is complete, also across a
  daylight-saving change. Never from a GlucoCraft entity. At every start, of each source's current state only a reading
  with its own measurement time is taken (without one it could be an old value restored at the start), and a state
  change before Home Assistant has started in which such a value comes up is no reading either. With 100 or more
  stored readings (readings kept from an earlier installation, or a time in which Home Assistant ran without
  GlucoCraft loaded: the entry disabled, or not loaded after an error), each start imports the recorder's readings of
  the primary measured after the newest stored one, under the same conditions (`_import_primary_history(newest,
  recent_only=True)`, placed by `storage.fill_after`, as the catch-up after a clock that was behind): the recorder is
  read only from an hour before the newest stored reading (`history_import._RECENT_CONTEXT_SECONDS`, at the earliest
  the retention's first day). In every import (the first one too) a recorded state without its own measurement time
  is skipped when it may be a value written again as the entity came up (`history_import._came_up_again`): when the
  state before it is not known (the recent-only import's first state; the first state of a whole history is taken),
  when it has the value of the state before it after an unavailable / unknown state, or when it has that state's
  value and attributes and was created afresh, its `last_changed` differing (the recorder writes no row for an
  unchanged write, so such a row is a restore at a start; a value written again with `force_update` keeps its
  `last_changed` and is a reading, as on the live path). While Home Assistant is not running, the recorder records
  nothing, so that time remains a gap.
- **Removal:** as the setting *When GlucoCraft is deleted* says (`__init__.async_remove_entry`). *Delete everything*:
  deleting the integration removes the readings file and its copies, the CSV exports, the report PDFs, the empty
  folders, every store, every Repairs issue and notification, the recorded states and long-term statistics of its
  entities (recorder `purge_entities` and clear statistics), the dashboard and the resources. *Keep the readings for a
  reinstall* (the default): the same, except the readings file, the laboratory results and the sensor events, which
  `kept.py` keeps in the folder `glucocraft` under names of no installation (`glucocraft_kept.bin` and two JSON
  files), with the notification *GlucoCraft deleted*. The next setup finds them (`kept.kept_summary`) and asks:
  kept, the new entry adopts them at its first start (`__init__._async_adopt_kept`); deleted, they go when the setup
  ends (`kept.discard`). The setup's check only reads (`GlucoCraftStorage(..., read_only=True)`): a kept readings file
  that cannot be read is adopted all the same, and the first start reports it as a damaged readings file. A kept
  laboratory or sensor-event list that cannot be read is never deleted: the adoption renames it
  `<name>.corrupt-<epoch>` with a warning in the log, and `kept.discard` removes such copies too.
- **Saved at once:** each stored reading is appended and fsynced when it is stored; the coordinator's minute tick
  saves anything still unsaved, and so does Home Assistant's stop; the whole file is rewritten only for purges and for
  inserted or replaced readings.
- **Reserved field in every record:** bytes 6-7 are written as 0 and their value is never used, so the record keeps
  its size and the format its version. A file with other values there loads
  unchanged and is written in full, with the field 0, at its next save. The slope and the trend are recalculated from
  the readings (section 5).
- **Detected interval in the header:** kept so that the reading rate is known immediately after a restart, without
  analyzing the readings again.

---

## 8. GLU2 Binary Format (display card and wall panels)

GlucoCraft serves **GLU2 version 3** with the GlucoCraft extension **GCX1** (`panel_feed.py`); the code defines the
format, and the panels read it with `esphome/glucocraft_panel_binary.h`. All values are mg/dL; only the weekly pages'
description lines are in the display unit. Little-endian. The display card and the panels read version 3 and later: a
later version is read by the parts they know (the header's offsets and the blocks found by their names) and the rest
is ignored; an earlier version is refused.

### Structure
```
HEADER (40 bytes):
  char[4]  "GLU2"; uint16 version (3); uint16 trend_count
  uint32   stats_offset, ranges_offset, readings_offset, reading_count, oldest_ts, newest_ts, trends_offset
  uint32   directions: 2 bits per weekly page (0 none, 1 Increasing, 2 Decreasing, 3 Stable), bit 31 = present

STATS (7 × 44 bytes; 6h, 24h, 7d, 14d, 30d, 90d, 180d):
  10 float32: mean, std_dev, cv, gmi (0 below 14 days or 70% data), tir, titr, time_low, time_high,
              time very low, time very high (on the ranges in use)
  uint16 min, uint16 max (whole mg/dL, for firmware that does not read GCM1; the unrounded values are in GCM1)

RANGES (24 bytes): 6 float32 very low, very high, low, high, tight low, tight high

READINGS (6 bytes each): uint32 timestamp, uint16 mg/dL

TRENDS (48 bytes each, up to 26, newest first; full file only):
  uint32 start, uint32 end; 10 float32: mean, std_dev, cv, gmi, tir, titr, below (combined), above (combined), min, max
  (the values of the 14 days ending at each week's end; an empty week = mean 0)

GCX1 EXTENSION (full file only, after the trends): "GCX1", uint16 version 1, uint16 standard_count (1 or 2);
  per range set 656 bytes: uint8 kind (0 consensus, 1 custom), uint8 reserved (0), uint16 week_count, uint32
  directions, 9 targets (8 bytes each: uint8 op, 3 padding bytes, float32 value), 9 description lines (64 bytes
  each: UTF-8, at most 63 bytes cut at a whole character, NUL-padded); then the
  second range set's weekly records

GCM1 BLOCK (60 bytes, both files, immediately before the GCL2 trailer): 7 x (float32 min, float32 max) mg/dL,
  unrounded, in the STATS period order (0 = no readings), then "GCM1". A reader finds it in front of the GCL2 trailer
  and uses STATS' whole mg/dL values when it is absent; with it, a minimum or maximum in mmol/L is rounded once, at
  display, like every other statistic (rounding whole mg/dL again to 0.1 mmol/L can change the last digit)

GCL2 TRAILER (60 bytes, the last bytes of the full file and of the delta file; read from the end, so a panel that does
  not know it ignores it): 7 x (uint32 LE seconds of LO, uint32 LE seconds of HI) in the STATS period order, then "GCL2".
  The readings section leaves limit readings out (a gap on the graphs); a delta file can then have no readings,
  for its statistics

GCR1 BLOCK (6 + 9 n bytes, both files, immediately before the GCM1 block): the stretches of readings at the sensor's
  limit in the file's span (`limit_runs.py`): uint16 count n, then n x (uint32 start, uint32 end, uint8 kind: 1 LO,
  2 HI), then "GCR1"; no padding. A stretch is an unbroken run of limit readings of the same kind (it ends at a measured
  reading, at a reading of the other kind or at a data gap) and runs to the end of the time its last reading
  represents. The graphs mark these stretches along their bottom (LO) or top (HI) edge. A reader finds the block in
  front of GCM1 (its size follows from its count); one that does not know it still finds GCM1 and GCL2
```
A period without enough data (sensor active below 70% of it, compared before rounding) has an all-zero STATS record
(mean 0, which every display shows as "---") and zeros in GCM1, as a period without readings. Every time in the file is
unsigned 32-bit seconds (Unix time, UTC), which end on 2106-02-07, as every reader of this format reads them; a reader
that took them as signed would end in January 2038 (the module docstring of `panel_feed.py`).

### Files and delivery
- `?file=backfill`: every stored reading (up to the retention of 200 days), the trends and the extension. Limit
  readings are left out (the GCR1 block gives their stretches); the panels take the mark of the current reading from the `limit` /
  `limit_text` attributes. Kept under 960 KB: when needed, readings older than 24 hours are
  thinned evenly.
- `?file=delta`: the last 2 hours, the statistics, no trends.
- The display card fetches them from `/api/glucocraft/binary` (Home Assistant login); the wall panels from the
  GET-only, local-only webhook (no Home Assistant token on the panel). The delta file is fetched every 5 minutes, the
  full file hourly.

---

## 9. Sensor Entities (28 to 134)

A new installation has **28 entities**: 19 sensors and 9 binary sensors. With both optional features on (section 13)
there are **134**: 125 sensors and 9 binary sensors. All are named `glucocraft_…` (full list with attributes:
`ENTITIES_AND_API.md`). Every glucose sensor has the blood glucose device class (native mg/dL, shown in the display
unit).

**Naming.** Display names use the consensus terms of `LANGUAGE_STYLE.md` (very low, low, in range, tight range, high,
very high, custom), and every entity id follows its display name: `<platform>.glucocraft_<name in lower case, other
characters as underscores>` (`entity_ids.py`; checked for every entity by `tests/test_entity_ids.py`). Unique ids
never change.

**The ids are kept.** The cards, the dashboard and the panels find the entities by those ids, a panel's are in its
firmware, and Home Assistant cannot lock an id. A change of an id is the user's choice and is left as it is:
`renamed_entities.py`
checks the ids at setup and 2 seconds after the last change of an id of GlucoCraft's entities (a device rename
changes them one by one), and the fixable issue `entity_ids_changed` names every changed id and gives the original
ids back on the user's confirmation; an original id another entity has is left alone. While such a change stands, the display card and the panels show no
current data rather than an old number (`DISPLAY_CONTRACT.md`).

### Core Sensors (10)
| Entity | Unit | Description |
|--------|------|-------------|
| `sensor.glucocraft_last_glucose_reading` | display unit | Last glucose reading: the newest CGM reading (raw; it stays the last reading while there is no current data); attributes `zone` (very_low, low, near_low, tight_range, near_high, high, very_high), `zone_color`, `value_mgdl`, `unit`, `limit` (low / high / null), `limit_text` (LO / HI / null; shown instead of the number; the state is then unknown), `limit_since`, `last_measured_mgdl` and `last_measured_ts` (the current stretch of limit readings and the newest measured reading before it, for the displays' LO / HI screen; not recorded), `display_word` (the zone word under the value, section 16) |
| `sensor.glucocraft_5_minute_delta` | display unit | 5-minute delta (unrounded; shown whole mg/dL or 0.1 mmol/L) |
| `sensor.glucocraft_trend_arrow` | — | ↑ ↗ → ↘ ↓, attributes `description` (the five trend categories), `word` (the trend word in sentence case, shown with the setting *Trend shown as* set to *Word*, section 16) and `angle` |
| `sensor.glucocraft_last_reading_time` | timestamp | Last reading time: the newest measurement's own time, with the values that change with every reading as attributes (`last_reading_ts`, epoch seconds; `last_reading_mgdl`, the same reading in mg/dL; `reading_count`; `primary_last_measurement`, `secondary_last_measurement`) |
| `sensor.glucocraft_status` | — | warm_up / expired / no_data / no_current_data / expiring_soon (the stated expiry 24 hours away or less) / current, with label and color |
| `sensor.glucocraft_configuration` | — | Every setting as attributes (diagnostic), with `sources` (per source the resolved sensor limits, their settings and the reason of *Automatic*, `reading_interval_seconds`, `gap_threshold_seconds`), the top-level `reading_interval_seconds` and `gap_threshold_seconds`, and `maintenance_issues` (the Maintenance status, section 10) |
| `sensor.glucocraft_active_source` | — | The name of the source in use, with attributes (`source_entity`, `second_source_active`, `detected_interval`, `no_current_data`) |
| `sensor.glucocraft_data_history` | — | Data history: the stored span as the longest period it reaches (No data, Less than 6 hours, 6 hours … 180 days), with feature availability |
| `sensor.glucocraft_sensor_expiry` | — | CGM sensor expiry from the optional expiry entity |
| `sensor.glucocraft_spoken_summary` | — | Spoken summary: one ready-to-speak sentence (`spoken.py`; disabled by default; `ACCESSIBILITY.md` rule 9) |

### Period Statistics (113 sensors with both optional features on: 7 periods × 15 + 4 GMI + 4 GRI)
For each period (6h, 24h, 7d, 14d, 30d, 90d, 180d), on the ranges in use unless noted. The consensus ranges summary always
exists (7). The custom ranges summary and the six custom time-in-band sensors exist only while *Custom ranges and goals* is on (49);
mean, standard deviation, CV, minimum, maximum, sensor active and readings, and GMI and GRI from 14 days, only while
*Separate statistics entities* is on (57).

| Metric | Entity Pattern | Unit |
|--------|---------------|------|
| Mean | `sensor.glucocraft_{period}_mean` | display unit |
| Standard deviation | `sensor.glucocraft_{period}_standard_deviation` | display unit |
| CV | `sensor.glucocraft_{period}_cv` | % |
| Custom time in range (TIR) | `sensor.glucocraft_{period}_custom_time_in_range` | % |
| Custom time in tight range (TITR) | `sensor.glucocraft_{period}_custom_time_in_tight_range` | % |
| Custom time low | `sensor.glucocraft_{period}_custom_time_low` | % |
| Custom time high | `sensor.glucocraft_{period}_custom_time_high` | % |
| Custom time very low | `sensor.glucocraft_{period}_custom_time_very_low` | % |
| Custom time very high | `sensor.glucocraft_{period}_custom_time_very_high` | % |
| Minimum | `sensor.glucocraft_{period}_minimum` | display unit |
| Maximum | `sensor.glucocraft_{period}_maximum` | display unit |
| Sensor active | `sensor.glucocraft_{period}_sensor_active` | % (attributes `label`, `note`: the sensor-active label, section 4.2) |
| Readings | `sensor.glucocraft_{period}_readings` | count |
| Consensus ranges | `sensor.glucocraft_{period}_consensus_ranges` | % (time 70–180; every consensus value as attributes, with `limit_readings` / `limit_seconds`) |
| Custom ranges | `sensor.glucocraft_{period}_custom_ranges` | % (time in range on the custom ranges; every value on them as attributes: `time_in_range`, `time_in_tight_range`, `time_very_low`, `time_low`, `time_high`, `time_very_high`, `time_below_low`, `time_above_high`, `mean`, `std_dev`, `cv`, `gmi`, `gmi_mmol_mol`, `min_value`, `max_value`, `sensor_active`, `readings`, `limit_readings`, `limit_seconds`; glucose in mg/dL) |
| GRI (14d and longer only) | `sensor.glucocraft_{period}_gri` | consensus bands; zone A–E |
| GMI (14d and longer only) | `sensor.glucocraft_{period}_gmi` | % |

GlucoCraft's own pages, cards and panels read only the two summaries (`{period}_consensus_ranges`, and
`{period}_custom_ranges` while custom is on), whose attributes carry every value.

### Weekly Statistics Sensors (2)
| Entity | Description |
|--------|-------------|
| `sensor.glucocraft_weekly_statistics` | All 26 weeks as the attribute `weeks` (7-day values, ranges in use and consensus), plus `directions` (each weekly page's direction from the mean of the 14-day consensus values of the last 4 weeks against the mean of the 4 weeks before: Increasing, Decreasing, or Stable while the difference is within 2% of the page's scale; null with fewer than 8 weeks or without values; GlucoCraft rule, `const.TREND_PAGES`); a week counts only with enough data (at least 70% sensor active) |
| `sensor.glucocraft_change_over_time` | Change over time: 14-day and 7-day values at each of 26 week ends and the three comparison windows (the last 4 weeks, weeks 9 to 13 ago and weeks 22 to 26 ago; `change.COMPARE`), both range sets; calculated hourly, and with it the weekly pages' 14-day values, GMI, GRI and directions (the period statistics every 5 minutes) |

**Week rule** (GlucoCraft rule): one rule for every weekly value: a week (or a 14-day window) counts only with enough
data, sensor active at least 70% of it, measured as covered time by the gap rule and compared before rounding
(`statistics.enough_data`). The weekly pages of the display card and the panels, Change Over Time (`panel_feed.py`,
`change.py`) and the weekly statistics apply it, so a week with less data is `None` for every consumer of the weekly
statistics (`has_data` false in the attribute `weeks`) and a gap on the weekly pages.

**Enough data** (`statistics.enough_data`; `const.ENOUGH_DATA_PCT` = 70): a period's statistics (the mean, SD, CV,
GMI, GRI, the time in ranges, the lowest and highest value) are shown only when sensor active is at least 70% of that
period, compared before rounding, for every period length and on every surface: the statistic sensors (state unknown
below it), the summary sensors (the same keys with null, and `enough_data`, `period_seconds`, `covered_seconds`), the
API, the panel files (an all-zero STATS record), the weekly pages, Change Over Time, the summaries and the CGM Report.
The current reading, the graphs' readings and the time at the sensor's limit are not affected. Battelino et al.
(2019, Table 2) give 70% for 14-day periods; applying it to every period length is a GlucoCraft rule. The panels and the
display card show "---" only; the dashboard pages and the CGM Report show one note per period, with about how many
more days (hours, for short periods) are needed with no gaps, worked out when the page opens from `period_seconds` and
`covered_seconds`. Why: a few readings would give a period values that describe only those hours.

### Binary Sensors (9)
`binary_sensor.glucocraft_glucose_below_range`, `_glucose_above_range`, `_glucose_very_low`, `_glucose_very_high`,
`_glucose_in_range`, `_glucose_in_tight_range` (unknown while there is no current data), `_no_current_data`, `_sensor_warm_up`,
`_using_custom_ranges` (attributes `range_very_low`, `range_low`, `range_tight_low`, `range_tight_high`, `range_high`,
`range_very_high`, and `consensus_*` with the same six suffixes). Glucose above range, Glucose very low, Glucose very
high, Glucose in range and Glucose in tight range are disabled in a new installation, because no GlucoCraft page, card or panel reads
them.

### Trend Arrow Bands
The five trend words of the manufacturer documentation (Abbott Diabetes Care, 2022, p. 11), applied to GlucoCraft's
slope rounded to 0.1 mg/dL/min, for every CGM (`const.TREND_LIBRE_BANDS`):

| Arrow | Word | Rate (mg/dL/min) |
|-------|------|------------------|
| ↑ | Rising Quickly | > 2.0 |
| ↗ | Rising | 1.0 to 2.0 |
| → | Changing Slowly | −0.9 to +0.9 |
| ↘ | Falling | −2.0 to −1.0 |
| ↓ | Falling Quickly | < −2.0 |

Unknown (no arrow) while there are not enough earlier readings. The panels' arrow angle = rate / 2 × 90°, clamped to
±90°, from the same rate rounded to 0.1 mg/dL/min (so in 4.5° steps, given as whole degrees: `coordinator.trend_angle`). With the setting *Trend shown as* set to *Word* the displays show the word instead of the arrow: the broader
category of the same rate (section 16).

---

## 10. Lovelace Cards and Dashboard

The frontend files (`custom_components/glucocraft/www/`) are registered as Lovelace resources at versioned paths
(`/glucocraft_v/<hash>/…`); in Lovelace YAML resource mode the cards are loaded with `add_extra_js_url`. The dashboard
user guide is `DASHBOARD.md`.

### glucocraft-strategy.js: the GlucoCraft dashboard
A native, read-only Home Assistant dashboard (strategy `custom:glucocraft`) in the sidebar at `/glucocraft-home`,
optional (setting, on by default). Views: **Start**, Today, Custom Ranges (`/custom`), Consensus Ranges, CGM Report,
Maintenance (wall panels found automatically), Settings Status and Documentation. The Custom Ranges page is built
while *Custom ranges and goals* is on (and while the configuration is not yet known); whether its start-page row is
shown is decided live. Start = the display card and the pages card side by side (stacked on narrow screens), the
display card exactly as tall as the pages card (at most 536 px).

**The Maintenance status** (`maintenance.py`; dashboard only): what in GlucoCraft's setup needs checking. Counted, one
issue each: a wall panel offline for 10 minutes (while its offline notice is shown, section 12), an open GlucoCraft
issue in Home Assistant's Repairs (active and not ignored; read at the start and after every change of one of
GlucoCraft's issues), and a failed CSV export (until an export succeeds or GlucoCraft restarts). Not counted: no
current data and the sensor expiry, which the banners, the status bar and the status headline show. One source: the
Configuration sensor's attribute `maintenance_issues`, a list with one entry per issue (`{"kind": "panel_offline",
"name"}`, `{"kind": "repairs", "title"}` with the issue's title in Home Assistant's language, `{"kind": "csv_export",
"reason"}`); a change writes the states again at once. The Start page's *Maintenance* button counts the entries: green
with the wrench and *No issues*, or red with an alert icon and *1 issue* / *N issues*, named for screen readers
"Maintenance: no issues" / "Maintenance: N issues" (never color alone). The Maintenance page opens with the hero card
*Maintenance issues*, shown only while there are issues, which lists each issue with where it is resolved (the panel's
power and Wi-Fi; Home Assistant's Repairs; the CSV export, saved again).

### glucocraft-display-card.js: the display card
The same layout, data and rules as the wall panels (`DISPLAY_CONTRACT.md`). Setting *Display card layout*, chosen
independently of the panels: Square (480 × 480 layout, card 480 × 536), Rectangular (800 × 480 layout, 800 × 536).
17 pages: 6 hours to 6 months, nine weekly pages and the About page (section 12).

### glucocraft-ui.js: the dashboard's cards
Banners, readout, statistics table (✓ meets / ✗ does not meet the cited consensus targets, from 14 days with at least
70% data), history graphs, Change Over Time, details, statistics, daily range, pages card (with the Maintenance
status), start pair, the Maintenance issues. Factual wording only (no evaluation; `LANGUAGE_STYLE.md`).

### glucocraft-report.js, glucocraft-docs.js and glucocraft-i18n.js
The CGM Report page (section 6.4), the Documentation page (the disclaimer, methods, references) and the text
language module shared by the cards and pages.

### glucocraft-shared.js: the cards' shared module
The rules that several card files use, written once (`DEVELOPER_GUIDE.md` section 5): the settings in use and the
number, glucose, time and date formats; the GMI formula and the consensus targets; the spoken text; links inside the
dashboard and content and focus across redraws (`gcRender`); the gap rule; the graphs' scale and minimum-to-maximum
areas; the default colors and black or white text on a color; the GLU2 parser and the downloads of GlucoCraft's
files. Every other frontend file except glucocraft-i18n.js imports it next to its own URL, as it imports
glucocraft-i18n.js, and waits for it before it defines its elements; the module defines `globalThis.glucocraftShared`
once (`GCSHARED_VERSION`), so a second load keeps the first. The rules with a Python counterpart are copies of
`units.py`, `gaps.py`, `spoken.py` and `contrast.py`, compared by the tests.

### Dashboard rules
- **Cited context** (`LANGUAGE_STYLE.md` rule 15): each text at the place whose number or rule it explains (Consensus
  Ranges, Today, Custom Ranges, Statistics, Change Over Time, the Documentation, the CGM Report and the PDF).
- **Documentation, What GlucoCraft Leaves Out, and Why:** the categories left out (treatment recommendations, targets
  for specific groups, device and laboratory accuracy, single-study outcome findings that cannot be stated without
  naming medications or substances), each with its reason and its sources by citation only; the excluded content is
  not restated.
- **Color key:** one small line at the bottom of each page that uses colors, listing only the colors that page uses
  (green met, red not met, amber attention, purple waiting, gray no target), with a link to Documentation, Colors;
  pages without colors have no line. Color is never the only cue (`ACCESSIBILITY.md` rule 1).
- **Custom Ranges:** the headline "N of M goals met" is counted with the same 14-day / 70% rule as every other ✓ / ✗;
  Today and Custom Ranges show the same data-quality warning as Consensus Ranges when less than 70% of the period has
  data.
- **Sensor active** is labeled as in section 4.2; the Documentation's References show the DOI as a URL only.

The disclaimer stands once per surface (`LANGUAGE_STYLE.md` rule 16): every dashboard page's footer carries the line
"For development and testing only. Not for medical use." with a link to the full text in Documentation >
Disclaimer, and the license line; Documentation > Disclaimer has the full text and Documentation > Essentials the three
essentials (the sensor limits, readings every 5 minutes or more often, what deleting GlucoCraft deletes); the notification rule is stated once (Notifications); the report page and the PDF carry
the disclaimer once, in *About This Report*, every PDF page footer is one line ("GlucoCraft. For development and testing
only. Not for medical use. · page n of m"), and the last section is *License* (the license notice).

---

## 11. HTTP API

All endpoints require Home Assistant authentication (Bearer token or Home Assistant session). Glucose values are in
the display unit unless noted. Details: `ENTITIES_AND_API.md`.

### GET /api/glucocraft/binary?file=backfill|delta
Returns a GLU2 version 3 file (section 8) for the display card.
- Content-Type: application/octet-stream
- No caching; 503 while there are no readings

### GET /api/glucocraft/data?period=6h
Returns JSON chart data (any period of section 9; default 6h).
```json
{
  "period": "6h",
  "unit": "mg/dL",
  "current": 105.0,
  "trend": "Changing Slowly",
  "trend_arrow": "→",
  "no_current_data": false,
  "detected_interval": 60,
  "limit": null, "limit_text": null,
  "reading_interval_seconds": 60, "gap_threshold_seconds": 900,
  "ranges": {"very_low": 54, "low": 70, "tight_low": 70, "tight_high": 140, "high": 180,
             "very_high": 250, "unit": "mg/dL", "is_default": true},
  "readings": [
    {"t": 1775900000, "r": 105.0},
    ...
  ],
  "stats": {
    "mean": 108.5, "std_dev": 15.2, "cv": 14.0, "gmi": 5.9,
    "time_in_range": 92.3, "time_in_tight_range": 78.5, "time_very_low": 0.0, ...
  }
}
```
Periods of 7 days and longer with more than 400 readings are downsampled to 400 slots (`r` = average, plus `min` /
`max`). Limit readings are left out (a gap on the graphs); `limit_runs` gives their stretches in the period,
`[[start, end, "low" | "high"], ...]`, which the graphs mark along their bottom (LO) or top (HI) edge. `stats` carries
`enough_data`, `sensor_active`, `period_seconds` and `covered_seconds`; without enough data every statistic value is
null. `&daily=N` (1 to 200) adds `daily`, the last N local days
(Home Assistant's time zone, today so far included), each with its time-weighted mean (the gap rule), its lowest and
highest measured reading, unrounded: the values of the Custom Ranges daily chart.

### GET /api/glucocraft/stats
Returns all period statistics, ranges, goals and the unit.
```json
{
  "current": 105.0,
  "detected_interval": 60,
  "reading_count": 127554,
  "unit": "mg/dL",
  "ranges": {
    "very_low": 54, "low": 70, "tight_low": 70, "tight_high": 140,
    "high": 180, "very_high": 250, "unit": "mg/dL", "is_default": true
  },
  "goals": {"time_in_range": 70, "time_in_tight_range": null, "time_low": 4, "time_very_low": 1,
            "time_high": 25, "time_very_high": 5, "cv": 36, "gmi": null},
  "periods": {
    "unit": "mg/dL",
    "6h": {"enough_data": true, "mean": 108.5, "time_in_range": 92.3, "sensor_active": 98.2,
           "period_seconds": 21600, "covered_seconds": 21211, ...},
    "24h": {...},
    ...
  }
}
```

### GET /api/glucocraft/report?days=14|30|90|180[&full=1] and /api/glucocraft/report.pdf?days=…&full=0|1&color=0|1
The CGM Report's data (JSON) and the report as a PDF (section 6.4). Both also take `&include_custom_appendix=1` and
`&include_research_appendix=1` (the optional appendices, off by default; the custom appendix only while *Custom ranges
and goals* is on, otherwise 400). A period the CGM Report does not have is refused with 400 and the text "This
report period is not available: 60 days. The CGM Report can be created for 14, 30, 90 or 180 days. Check the card's or
automation's settings." (`report.error.period`), never replaced by another period. `full` and `color` take 0 / 1, also
true / false, on / off, yes / no (an empty value is the default); any other value is refused with 400
(`api.flag_invalid`). Every period dict carries
`limit_readings` / `limit_seconds` and `enough_data` (below 70% sensor active every value is null), and a period that
ends at the last midnight `more_needed`; `previous` is null for the 6-month report; laboratory rows carry
`no_cgm_comparison` / `comparison_note`.

### Service glucocraft.print_report
Fields `days` (14 / 30 / 90 / 180), `report` (summary / full), `color`, `two_sided`, `include_custom_appendix` and
`include_research_appendix` (both off by default; section 6.4). The custom appendix is refused
(`custom_appendix_off`) while *Custom ranges and goals* is off.

### Service glucocraft.export_csv
Writes `glucocraft-readings_<stamp>.csv` (local time, UTC time, mg/dL, display-unit value, unit, source, limit flag)
and, with laboratory results, `glucocraft-lab-a1c_<stamp>.csv` to `<config>/glucocraft_exports/` (not reachable from
the web); the last 12 of each kind are kept; returns `{"folder", "files", "readings"}` (`ENTITIES_AND_API.md`). A failed export is
an issue of the Maintenance status until an export succeeds or GlucoCraft restarts (section 10).

### Service glucocraft.import_csv
Field `file` (a readings export in `<config>/glucocraft_exports/`; empty: the newest). Reads the export back in
(`export.read_readings_csv`, in either display unit and with either decimal separator) with the laboratory export of
the same time stamp; stored data win (`storage.add_readings`: a reading within 30 s of a stored one is not taken;
`report_service.async_import_lab`: a stored date is kept); only readings within the retention are taken
(`coordinator.import_readings`, which then saves and computes every statistic again). Returns the counts and creates
the notification *GlucoCraft import* (`ENTITIES_AND_API.md`).

### Panel webhook: GET /api/webhook/<id>?file=backfill|delta
The same GLU2 version 3 files for the wall panels; GET only, local network only (refused from outside the local
network), no Home Assistant token. GlucoCraft creates the id only when *Create a new wall-panel address* is ticked in
the settings (never at the first setup; an entry that has an address keeps it) and shows the address and the id only
on the settings screen *Display card and wall panels* (administrators); the notification that announces it contains
no id.

---

## 12. ESP32 Display System

GlucoCraft's wall panels are ESP32 boards running ESPHome, with example configurations in the repository's `esphome/`
folder. The user guide (setup, panel types, pages and controls) is `PANELS.md`; what each page shows and why is
`DISPLAY_CONTRACT.md`.

### Panel types
Five panel types, each identified by its ESPHome project name (Home Assistant shows the manufacturer "glucocraft" and
the type as the model):

| Type | Screen |
|---|---|
| Guition ESP32-S3-4848S040 | 4.0 in, 480 × 480, square |
| Guition JC3248W535 | 3.5 in, 480 × 320 |
| Waveshare ESP32-S3-Touch-LCD-5 | 5.0 in, 1024 × 600 |
| Waveshare ESP32-P4-WIFI6-Touch-LCD-7B | 7.0 in, 1024 × 600 |
| Waveshare ESP32-S3-Touch-LCD-7 | 7.0 in, 800 × 480 |

The panels draw with LVGL; every example panel connects to Home Assistant over Wi-Fi and is updated over Wi-Fi. The
landscape panels keep the glucose block on the left and the graph or weekly page on the right; on the square panel's
weekly pages a small current value stands in the top-right corner. At the sensor's limit the LO / HI screen stands in
the value's place, and the graphs mark the stretches of limit readings at their edge; every graph applies the gap rule
(section 4.5); every local time is in Home Assistant's time zone.

**Without Home Assistant:** after one hour without the ESPHome connection to Home Assistant or without a successful
download of GlucoCraft's files (`gc_units.h` `NO_HA_AFTER_S`, one constant), everything below the clock and the date is
black with "Cannot reach Home Assistant or load its data", since when (whichever problem started first), the last
reading with its time and "Check Home Assistant and the wall-panel address". It wins over every other screen, does not
throb, the page buttons do nothing while it is up, and it clears by itself when both work again. Before the hour the
panel shows No current data after the no-current-data limit, by its own count.

### Display pages (17, circular navigation)
The same pages on the display card and every panel (`DISPLAY_CONTRACT.md`, Pages):
- **0–6, glucose pages:** 6 hours (the current glucose value, trend arrow, 5-minute delta, source name, 6-hour graph
  and statistics), then 24 hours, 7, 14, 30 and 90 days and 6 months, each with its graph and statistics.
- **7–15, weekly pages:** time in range, time in tight range, mean, CV, GMI, time low, time high, standard deviation,
  minimum and maximum, as the 14-day values at each week's end, in the range set the setting *Range set of the weekly
  pages* selects (consensus ranges and targets, or the custom ranges and goals), with the direction (Increasing / Decreasing / Stable)
  from the file.
- **16, About GlucoCraft:** the icon, GlucoCraft's disclaimer (its first sentence as the title), the version line and the copyright line
  (`LANGUAGE_STYLE.md` rule 16).

At every start the panels show a start-up screen for 15 seconds, with no button: GlucoCraft's icon, its name, the panel
version and the line "For development and testing only. Not for medical use."
Navigation: touch; back to the 6-hour page after 3 minutes without use (the panels always; the display card unless its
option *Return to the first page after 3 minutes* is off).

### Statistics layout
- **Left:** time in range, time in tight range, minimum, maximum. **Right:** time low, time high, time very low, time
  very high.
- **Bottom row:** mean, standard deviation, CV, GMI (the 14-day GMI for the periods from 6 hours to 14 days, then the
  GMI of the 30-, 90- and 180-day period).

### Data pipeline
1. The panel downloads the GLU2 version 3 full file from GlucoCraft's local webhook (no Home Assistant token; the logs
   show only the file name, never the id), in its own task, into PSRAM (two buffers of 1024 KB: the live data and a
   staging buffer).
2. The file is validated before it replaces the data on screen.
3. The statistics, readings and weekly records are loaded into the display arrays.
4. Graphs are drawn from the timestamp-indexed data, with automatic scaling and the minimum-to-maximum areas.
5. The delta file is fetched every 5 minutes (6-hour and 24-hour statistics, gaps refilled), the full file hourly;
   live values come from GlucoCraft's entities through the ESPHome API.

### Panel notices
- **GlucoCraft Panel Added** (persistent notification): names the panel's shape and the display card the dashboard
  shows, and where to change it.
- **GlucoCraft Panel Offline:** when a panel's Status entity is off or unavailable for 10 minutes (the time since the
  entity changed counts, so a reload of GlucoCraft does not start the 10 minutes again); removed when the panel is back
  or its device is deleted. Notify-only. While it is shown, the panel is an issue of the Maintenance
  status (section 10).
- **A panel's device deleted** from Home Assistant: panels are the user's own devices, so GlucoCraft forgets the panel
  (its id leaves the store of panels already announced, `glucocraft.<entry>_panels`; a panel deleted while GlucoCraft
  was not running is forgotten at the next start) and removes its offline notice.

---

## 13. Configuration

### Config Flow Steps
1. **Disclaimer:** GlucoCraft's disclaimer as plain text, with no field; Submit continues. Nothing is stored.
1a. **Readings from an Earlier Installation** (step `kept`, only when a removal kept readings): keep them (the
   default) or delete them and start fresh; applied when the setup ends (`kept.py`).
2. **Glucose Sources:** the glucose sensor entity, an optional sensor expiry entity, the
   sensor lower and upper limit, the collapsed section *Second glucose source* (the second source and its limits), and
   *No current data after* (5 to 30 minutes, default 15); the display unit starts as the source's own unit, or as
   mg/dL when the source shows no value or no glucose unit, and the next screen then says that the unit could not be
   read.
3. **Units and Notifications** (step `finish`): display unit (mg/dL / mmol/L), daily notification toggle and time,
   dashboard on / off, phone notifications (notify targets, types, test). The two optional features (below) are
   stored explicitly as off; the setup has no ranges and goals screens. The
   screen says that every setting can be changed later under Configure, that the weekly summary (Sunday) and, with it,
   the monthly summary are off and are turned on under Configure > Notifications, and where the optional features
   are turned on.

### Optional features
Two switches on the Configure screen **Optional features** (`features`), each off in a new installation (an entry
without them reads both as off).
- **Custom ranges and goals** (`custom_ranges_and_goals`). On: the stored ranges and goals are in use (the zone and
  zone word of the glucose value, the graph bands, the weekly pages where chosen); the Glucose Ranges and Custom Goals screens are in the Configure menu (`config_flow.options_menu`); the
  dashboard has the Custom Ranges page; the CGM Report offers the custom appendix; per period the custom ranges
  summary (`sensor.glucocraft_{period}_custom_ranges`) and the six custom time-in-band sensors exist (49 entities). Off: `settings.ranges()` and `settings.setting()` return the defaults of the six limits, the
  eight goals and the weekly pages' range set, so every output uses the consensus ranges and targets; the stored values
  are kept for when it is turned on again; the page, the two screens and the entities are not there, and the custom
  appendix is refused (`print_report`: `custom_appendix_off`; the report endpoints: 400).
- **Separate statistics entities** (`separate_statistics`). On: one entity per range-independent statistic and period
  (mean, standard deviation, CV, minimum, maximum, sensor active, readings; GMI and GRI from 14 days; 57 entities). Off:
  not created. GlucoCraft's own pages, cards and panels never read them: they read the period summaries
  (`{period}_consensus_ranges`, and `{period}_custom_ranges` while custom is on), whose attributes carry every value.
- Turning a switch off removes its entities from the entity registry at the reload (`stale_entities.py`, which removes
  every registry entry of the entry that the setup did not create); the
  unique ids never change, so turning it on again creates the same entities under the same entity ids, and the
  recorder's history and long-term statistics, kept by entity id, continue. The Configuration sensor publishes both
  switches (`custom_ranges_and_goals`, `separate_statistics`): the dashboard strategy builds Custom Ranges from it, and
  the display card and the panels read the 6-hour statistics before the first file from the 6H custom ranges
  summary while custom is on and from the 6H consensus ranges summary otherwise.

### Options Flow (reconfigure after setup)
Settings > Devices & services > GlucoCraft > Configure: a menu of eight screens by topic (`config_flow.OPTIONS_MENU`;
Glucose Ranges and Custom Goals only while *Custom ranges and goals* is on, `config_flow.options_menu`), each saving
only its own fields; GlucoCraft reloads after each save. Every field has a short label and an explanation under it
(`strings.json` `data` and `data_description`); the collapsed sections (`homeassistant.data_entry_flow.section`) are
flattened before saving (`flow_schemas._flatten`), so every setting keeps its stored key, and a selector cleared inside
a section stores None as at the top level.
- **Glucose Sources** (`sources`): the primary source (a change keeps the stored readings; GlucoCraft's own entities
  are excluded and rejected: field error `source_is_glucocraft`), the CGM sensor expiry entity (a GlucoCraft entity
  is rejected there too: `expiry_is_glucocraft`), the **sensor lower and
  upper limit of each source** (Automatic / 40 / 50 and Automatic / 400 / 450 / 500 mg/dL; the screen's description
  states the rule once for both: the sensor limits must match the CGM sensor's measurement range, set from the source
  where GlucoCraft can, with a Repairs notice when the readings point to a different limit, otherwise by hand, and the
  user's responsibility; and the reading-interval statement), the primary measurement-time attribute, the collapsed
  section *Second glucose source* (the second source, its limits and its measurement-time attribute; an error in it is
  shown above the section, which then opens), and the no-current-data limit.
- **Units and Formats** (`units`): display unit, A1C and GMI unit, time format (Automatic from Home Assistant's
  country / 12-hour / 24-hour), date format (Automatic / mdy / dmy / ymd, stored in lower case and published as MDY /
  DMY / YMD), first day of the week, decimal separator.
- **Optional Features** (`features`): Custom ranges and goals, Separate statistics entities (see above).
- **Glucose Ranges**, **Custom Goals** (only while *Custom ranges and goals* is on).
- **Display Card and Wall Panels** (`displays`): display card (square / rectangular), *Trend shown as* (arrow or
  word), the seven zone colors and *Restore the default palette*, the collapsed section of the seven graph
  band colors, the card background and the panel background, *Outline text*, *Fade the words when there is no
  current reading* (on by default), the range set of the weekly pages (only
  while *Custom ranges and goals* is on and custom ranges or goals are set: consensus or custom), the wall-panel
  address in the screen's description and *Create a new wall-panel address*.
- **Notifications** (`notifications`): daily and weekly summaries, each switch on its own (the weekly summary, and
  every fourth week the monthly one, also while the daily summary is off; the weekday a selector of "0" to "6" with
  translated names, stored as an integer), the no-data notice (off by default) with *Notify after* (15 / 30 / 60 /
  120 minutes, stored as an integer, default 30; section 4.7), and phone notifications (the summaries and the
  no-data notice are sent there only when chosen, each on its own).
- **Report and Dashboard** (`report`): PDF paper size, report printer (IPP; `printer_invalid` for an address with a
  user name or password or of another form), dashboard on / off, *When GlucoCraft is deleted* (keep the readings for
  a reinstall, the default, or delete everything).

**Checks on the ranges and goals screens:**
- Range order: very low < low <= tight range from < tight range to <= high < very high (mg/dL, after the conversion
  from the display unit). "<=" where the consensus limits are equal (low = tight range from = 70 mg/dL) or may be (a
  tight range that ends at the high limit). Both fields of every pair out of order are marked, each with its own error
  text, plus the summary at the top (`config_flow._range_errors`); nothing is saved until the order is correct.
- Nested goals: the goal for the time below the very-low limit cannot be higher than the goal for the time below the
  low limit, and the goal for the time above the very-high limit cannot be higher than the goal for the time above the
  high limit, because each outer band includes the inner one (the consensus targets are nested the same way,
  Battelino et al., 2019) (`config_flow._goal_errors`). Every goal stays within the span of its number box.

**Stored settings are read through one validated reader** (`settings.py`: `setting`, `ranges`). A stored value of the
wrong type or outside its span ("70" instead of 70, None, a list, "false" for a switch, an unknown unit or display
card, a weekly day of 7, a notification time of "25:00") is used as missing, so the setting's default applies on every
output, and GlucoCraft never stops with an error. Nothing is guessed: the text "70" is not read as 70. The range limits
are validated as a set: each within its span, and in the screen's order, very low < low <= tight range from < tight
range to <= high < very high; otherwise the default (consensus) ranges apply. The very-low and the very-high threshold are then kept within the
sensors' limits (`settings.ranges` with `limits.threshold_span`): only that one threshold is used at the sensor's
limit, the other limits as stored, never the default ranges because of a sensor limit; a Repairs notice names it. The
stored entry is never changed.

### Custom Glucose Ranges
| Range | Default (mg/dL) | Default (mmol/L) |
|-------|----------------|-------------------|
| Very Low | 54 | 3.0 |
| Low | 70 | 3.9 |
| High | 180 | 10.0 |
| Very High | 250 | 13.9 |
| Tight Low | 70 | 3.9 |
| Tight High | 140 | 7.8 |

The defaults are the consensus ranges (section 6.1). Ranges are stored as whole mg/dL; entered in mmol/L, the
consensus values map to their mg/dL pairs. The period sensors, the display card and the panels use the ranges in use;
the consensus pages, the GRI and the report always use the consensus ranges (section 6.1).

---

## 14. Clinical References

These are the sources on which GlucoCraft's statistics, targets and report format are based. A change to a
calculation or a target is checked against them.

| Topic | Source | Used in GlucoCraft |
|---|---|---|
| Standardized glucose reporting | Bergenstal et al. (2013) | CGM Report (`report.py`, `glucocraft-report.js`, `report_pdf.py`) |
| CGM metrics; hypoglycemia events | Danne et al. (2017) | statistics engine, sensor entities; event start and end (section 6.4) |
| CV threshold of 36% | Monnier et al. (2017) | the CV consensus target (statistics table, weekly CV page) |
| GMI formula | Bergenstal et al. (2018) | `statistics.py`: 3.31 + 0.02392 × mean; the laboratory A1C comparison |
| Time in range targets | Battelino et al. (2019) | consensus ranges and targets, default ranges, the 14-day / 70% rule |
| Metrics for clinical trials; tight range | Battelino et al. (2023) | tight range 70–140 mg/dL; night definition; the research appendix; context only otherwise |
| Glycemia Risk Index | Klonoff et al. (2023) | `statistics.py`, `…_gri` |
| Trend categories | Abbott Diabetes Care (2022) | `const.py` `TREND_LIBRE_BANDS` |
| LO / HI left out of the values, counted in the time in ranges | Other methods: Abbott Diabetes Care (2018), Spartano et al. (2025), Ultragenyx Pharmaceutical Inc. (2024) | `limits.py`, `statistics.py`, `report.py` (GlucoCraft rule) |
| Standards of Care | American Diabetes Association Professional Practice Committee for Diabetes (2026a) | the research appendix (Table 6.2); context |

The Documentation's References list every source the dashboard cites, including the measurement ranges of other CGM
systems.

### Reference list (APA 7)

Abbott Diabetes Care. (2018). *FreeStyle Libre software user's manual* (Software version 1.0; ART39660-001 rev. A).
https://freestyleserver.com/Payloads/IFU/2018/ART39660-001_rev-A-Web.pdf

Abbott Diabetes Care. (2022). *Get started with the FreeStyle Libre 3 system* (ADC-55381 v1.0) [Getting started guide,
US, mg/dL]. https://www.freestyleprovider.abbott/content/dam/adc/freestyleprovider/countries/us-en/documents/Brochure_FreeStyleLibre3_Getting_Started_Guide.pdf

American Diabetes Association Professional Practice Committee for Diabetes. (2026a). 6. Glycemic goals,
hypoglycemia, and hyperglycemic crises: Standards of Care in Diabetes—2026. *Diabetes Care, 49*(Suppl. 1), S132–S149.
https://doi.org/10.2337/dc26-S006

Battelino, T., Alexander, C. M., Amiel, S. A., Arreaza-Rubin, G., Beck, R. W., Bergenstal, R. M., Buckingham, B. A.,
Carroll, J., Ceriello, A., Chow, E., Choudhary, P., Close, K., Danne, T., Dutta, S., Gabbay, R., Garg, S., Heverly,
J., Hirsch, I. B., Kader, T., . . . Phillip, M. (2023). Continuous glucose monitoring and metrics for clinical trials:
An international consensus statement. *The Lancet Diabetes & Endocrinology, 11*(1), 42–57.
https://doi.org/10.1016/S2213-8587(22)00319-9

Battelino, T., Danne, T., Bergenstal, R. M., Amiel, S. A., Beck, R., Biester, T., Bosi, E., Buckingham, B. A.,
Cefalu, W. T., Close, K. L., Cobelli, C., Dassau, E., DeVries, J. H., Donaghue, K. C., Dovc, K., Doyle, F. J., III,
Garg, S., Grunberger, G., Heller, S., . . . Phillip, M. (2019). Clinical targets for continuous glucose monitoring
data interpretation: Recommendations from the International Consensus on Time in Range. *Diabetes Care, 42*(8),
1593–1603. https://doi.org/10.2337/dci19-0028

Bergenstal, R. M., Ahmann, A. J., Bailey, T., Beck, R. W., Bissen, J., Buckingham, B., Deeb, L., Dolin, R. H., Garg,
S. K., Goland, R., Hirsch, I. B., Klonoff, D. C., Kruger, D. F., Matfin, G., Mazze, R. S., Olson, B. A., Parkin, C.,
Peters, A., Powers, M. A., . . . Wesley, D. M. (2013). Recommendations for standardizing glucose reporting and analysis
to optimize clinical decision making in diabetes: The Ambulatory Glucose Profile (AGP). *Diabetes Technology &
Therapeutics, 15*(3), 198–211. https://doi.org/10.1089/dia.2013.0051

Bergenstal, R. M., Beck, R. W., Close, K. L., Grunberger, G., Sacks, D. B., Kowalski, A., Brown, A. S., Heinemann, L.,
Aleppo, G., Ryan, D. B., Riddlesworth, T. D., & Cefalu, W. T. (2018). Glucose management indicator (GMI): A new term
for estimating A1C from continuous glucose monitoring. *Diabetes Care, 41*(11), 2275–2280.
https://doi.org/10.2337/dc18-1581

Danne, T., Nimri, R., Battelino, T., Bergenstal, R. M., Close, K. L., DeVries, J. H., Garg, S., Heinemann, L.,
Hirsch, I., Amiel, S. A., Beck, R., Bosi, E., Buckingham, B., Cobelli, C., Dassau, E., Doyle, F. J., III, Heller, S.,
Hovorka, R., Jia, W., . . . Phillip, M. (2017). International consensus on use of continuous glucose monitoring.
*Diabetes Care, 40*(12), 1631–1640. https://doi.org/10.2337/dc17-1600

Klonoff, D. C., Wang, J., Rodbard, D., Kohn, M. A., Li, C., Liepmann, D., Kerr, D., Ahn, D., Peters, A. L.,
Umpierrez, G. E., Seley, J. J., Xu, N. Y., Nguyen, K. T., Simonson, G., Agus, M. S. D., Al-Sofiani, M. E.,
Armaiz-Pena, G., Bailey, T. S., Basu, A., . . . Kovatchev, B. (2023). A Glycemia Risk Index (GRI) of hypoglycemia and
hyperglycemia for continuous glucose monitoring validated by clinician ratings. *Journal of Diabetes Science and
Technology, 17*(5), 1226–1242. https://doi.org/10.1177/19322968221085273

Monnier, L., Colette, C., Wojtusciszyn, A., Dejager, S., Renard, E., Molinari, N., & Owens, D. R. (2017). Toward
defining the threshold between low and high glucose variability in diabetes. *Diabetes Care, 40*(7), 832–838.
https://doi.org/10.2337/dc16-1769

### Sources consulted (APA 7)

The works consulted for the code and the panels, beside the clinical references above: the integrations and
libraries that deliver the readings and their out-of-range values (`limits.py`, `coordinator.py`), the data paths and
their reading intervals (section 2), the conversion factor of mmol/L (section 4.3), the panels' board, firmware and
libraries (section 12, `docs/PANELS.md`), the check of the report's PDF, and the reading research behind the panels'
start-up screen. The user texts cite the works of the Documentation's References only; the legal and trademark
research is not listed here. Retrieval dates are those of the last check.

Abbott Diabetes Care. (n.d.). *How often should I scan my FreeStyle Libre 14 day sensor with the FreeStyle LibreLink
app?* [FAQ]. Retrieved November 6, 2026, from
https://www.freestyle.abbott/en-us/support/faq/apps/how-often-should-i-scan-my-freestyle-libre-14-day-sensor-with-the-freestyle-librelink-app.html

Abbott Diabetes Care. (2021). *Your FreeStyle Libre 14 day system* (ADC-10386 v3.0) [In-service guide, US].
https://www.freestyleprovider.abbott/content/dam/adc/freestyleprovider/countries/us-en/4-2-page/documents/In-Service,%20FreeStyle%20Libre%2014%20Day%20System%20with%20App,%20HCP%20Sales%20New.pdf

Anderson, B. B., Vance, A., Kirwan, C. B., Jenkins, J. L., & Eargle, D. (2016). From warning to wallpaper: Why the
brain habituates to security warnings and what can be done about it. *Journal of Management Information Systems,
33*(3), 713–743. https://doi.org/10.1080/07421222.2016.1243947

Brysbaert, M. (2019). How many words do we read per minute? A review and meta-analysis of reading rate. *Journal of
Memory and Language, 109*, Article 104047. https://doi.org/10.1016/j.jml.2019.104047

Dexcom, Inc. (n.d.-a). *How do I share my Stelo glucose data with health apps?* [FAQ]. Retrieved November 6, 2026, from
https://www.stelo.com/en-us/faqs/using-stelo/how-do-i-share-my-glucose-data-with-health-apps

Dexcom, Inc. (n.d.-b). *Sharing Dexcom CGM data: Clinics, apps, and followers* [FAQ]. Retrieved November 6, 2026, from
https://www.dexcom.com/en-gb/faqs/data-sharing

dn32. (2026, August 31). *SDIO: After TRANSPORT_FAILURE and host restart, the C6 stays wedged — three CHIP_PU resets do
not recover it, only a power cycle does (EHM-271)* (Issue No. 240) [Issue]. GitHub.
https://github.com/espressif/esp-hosted-mcu/issues/240

dodog. (2026). *librelink* (Version 1.5.0) [Computer software]. GitHub. https://github.com/dodog/librelink

gagebenne. (2026). *pydexcom* (Version 0.5.1) [Computer software]. GitHub. https://github.com/gagebenne/pydexcom

gillesvs. (2024). *librelink* (Version 1.2.3) [Computer software]. GitHub. https://github.com/gillesvs/librelink

Home Assistant. (n.d.-a). *android* [Computer software; the Health Connect sensors,
HealthConnectSensorManager.kt]. GitHub. Retrieved October 30, 2026, from https://github.com/home-assistant/android

Home Assistant. (n.d.-b). *Home Assistant Contributors* [Developer documentation]. Retrieved November 6, 2026, from
https://contributors.home-assistant.io/

Home Assistant. (n.d.-c). *Sensors* [Home Assistant Companion documentation]. Retrieved November 6, 2026, from
https://companion.home-assistant.io/docs/core/sensors/

Home Assistant. (2026). *core* (Version 2026.11.0) [Computer software; homeassistant/components/dexcom,
homeassistant/components/nightscout and homeassistant/util/unit_conversion.py]. GitHub.
https://github.com/home-assistant/core

LVGL Kft. (n.d.). *LVGL 9.5 documentation* [Documentation]. Retrieved November 6, 2026, from
https://lvgl.io/docs/open/9.5/

Mulley, J. F. (2026). Prevalence, duration, and clinical implications of continuous glucose monitor measurement limit
capping in type 1 diabetes. *Diabetic Medicine, 43*(10), Article e70412. https://doi.org/10.1111/dme.70412

Nightscout Foundation. (n.d.-a). *cgm-remote-monitor* [Computer software; lib/client/index.js, lib/plugins/bgnow.js,
lib/plugins/bridge.js, lib/plugins/errorcodes.js and lib/settings.js]. GitHub. Retrieved November 6, 2026, from
https://github.com/nightscout/cgm-remote-monitor

Nightscout Foundation. (n.d.-b). *nightscout-connect* [Computer software; lib/sources/dexcomshare.js and
lib/sources/librelinkup.js]. GitHub. Retrieved October 30, 2026, from https://github.com/nightscout/nightscout-connect

Nightscout Foundation. (n.d.-c). *xDrip* [Computer software; app/src/main/java/com/eveningoutpost/dexdrip/models/
BgReading.java]. GitHub. Retrieved November 6, 2026, from https://github.com/NightscoutFoundation/xDrip

pachi81. (n.d.). *GlucoDataHandler* [Computer software]. GitHub. Retrieved October 22, 2026, from
https://github.com/pachi81/GlucoDataHandler

Swoboda, J. (2026, May 25). *[esp32_hosted] Bump esp_hosted to 2.12.8 and add use_psram option* (Pull request No.
16627) [Pull request]. GitHub. https://github.com/esphome/esphome/pull/16627

Spartano, N. L., Sultana, N., Lin, H., Cheng, H., Lu, S., Fei, D., Murabito, J. M., Walker, M. E., Wolpert, H. A., &
Steenkamp, D. W. (2025). Defining continuous glucose monitor time in range in a large, community-based cohort without
diabetes. *The Journal of Clinical Endocrinology & Metabolism, 110*(4), 1128–1134. https://doi.org/10.1210/clinem/dgae626

Tidepool. (n.d.). *Out of range values* [Documentation]. Retrieved November 6, 2026, from
https://tidepool.redocly.app/docs/device-data/oor-values

timoschlueter. (n.d.). *nightscout-librelink-up* [Computer software; src/index.ts and src/config.ts]. GitHub.
Retrieved October 30, 2026, from https://github.com/timoschlueter/nightscout-librelink-up

Ultragenyx Pharmaceutical Inc. (2024). *Statistical analysis plan: Study 401GSDIA02* (ClinicalTrials.gov identifier
NCT03970278). https://cdn.clinicaltrials.gov/large-docs/78/NCT03970278/SAP_001.pdf

Vance, A., Kirwan, B., Bjornn, D., Jenkins, J., & Anderson, B. B. (2017). What do we really know about how habituation
to warnings occurs over time? A longitudinal fMRI study of habituation and polymorphic warnings. In *Proceedings of the
2017 CHI Conference on Human Factors in Computing Systems* (pp. 2215–2227). Association for Computing Machinery.
https://doi.org/10.1145/3025453.3025896

veraPDF Consortium. (2026). *veraPDF* (Version 1.30.3) [Computer software]. https://github.com/veraPDF/veraPDF-library

Wampukas. (2026, May 22). *ESP32-P4 + ESP32-C6 SDIO: sdio_mempool_create assert at boot on 2026.5.0 (regression from
2026.4.5; confirmed not fixed by newer C6 firmware)* (Issue No. 16574) [Issue]. GitHub.
https://github.com/esphome/esphome/issues/16574

Waveshare. (n.d.-a). *ESP32-P4-WIFI6-Touch-LCD-7B* [Documentation]. Waveshare Wiki. Retrieved November 6, 2026, from
https://www.waveshare.com/wiki/ESP32-P4-WIFI6-Touch-LCD-7B

Waveshare. (n.d.-b). *ESP32-P4-WIFI6-Touch-LCD-7B* [Schematic].
https://files.waveshare.com/wiki/ESP32-P4-WIFI6-Touch-LCD-7B/ESP32-P4-WIFI6-Touch-LCD-7B.pdf

yo-han. (2026). *Home-Assistant-Carelink* (Version 2026.2.1) [Computer software]. GitHub.
https://github.com/yo-han/Home-Assistant-Carelink

---

## 15. File Inventory

### Integration (custom_components/glucocraft/)
| File | Purpose |
|------|---------|
| `__init__.py` | Setup, platform forwarding, HTTP / webhook / frontend / dashboard / notification registration, actions, the entry-version check, removal |
| `renamed_entities.py` | At every start and 2 seconds after a change of an id, the check of each entity's id against the id it was created with; the Repairs notice `entity_ids_changed` and, on the user's confirmation, the putting back of the original ids (a change is never undone by itself). |
| `source_rename.py` | The sources' settings follow a new entity id, from the entity registry's event: what is kept per source goes with it, and the entry reloads. |
| `stale_entities.py` | At every start, the removal of the entity-registry entries of the entry that the setup did not create (an optional feature that is off, a feature removed by an update); their recorded history stays |
| `config_flow.py` | Three-step config flow (with the step for readings kept from an earlier installation) and the options flow menu (eight screens) |
| `flow_schemas.py` | The screens' forms and shared field helpers (collapsed sections, selectors, color pickers) |
| `flow_helpers.py` | Entity-registry lookups, the printer address check and the phones' delivery test of the flows |
| `const.py` | Constants, thresholds, ranges, trend bands, zone and status colors, the disclaimer and the license notices |
| `settings.py` | The one validated reader of the stored settings (`setting`, `ranges`): wrong types and values outside their span fall back to the default |
| `units.py` | mg/dL ↔ mmol/L, consensus pairs, rounding (halves away from zero) |
| `unit_registry.py` | Display unit → every glucose sensor's entity settings |
| `regional.py` | Time and date formats (Automatic from Home Assistant's country, or chosen) |
| `i18n.py`, `i18n/` | The text catalogues and their lookup |
| `coordinator.py` | State listener, merge of two sources, adaptive smoothing, slope, trend, statistics, status |
| `source_notices.py` | The sources' Repairs notices checked on the timer (missing, fast, slow source; sensor cap and floor; at the automatic limit; the expiry entity), the ids of every source notice |
| `entity.py`, `entity_ids.py` | The service device of the entities; entity ids from the display names (the id rule) |
| `sensor.py` | The sensor entities (core, period, weekly, Change over time, Spoken summary) and the optional-feature groups |
| `binary_sensor.py` | The 9 binary sensors (conditions, no current data, warm-up, custom ranges) |
| `spoken.py` | The spoken sentence of the Spoken summary sensor |
| `storage.py` | GC02 version 3 binary persistence, window indices, whole-day purge |
| `statistics.py` | Time-weighted statistics (gap rule), limit-reading counts, GRI, weekly computation |
| `gaps.py` | The one gap rule: reading interval per stretch, gap threshold, weights |
| `limit_runs.py` | The stretches of readings at the sensor's limit: those of a period (the graphs' edge marks, the GCR1 block) and the current one (the LO / HI screen) |
| `limits.py` | LO / HI and no-reading detection, sensor limits per source, the comparison of a LO / HI reading with a limit |
| `export.py` | CSV export of the readings and laboratory A1C |
| `repairs.py` | The Repairs fix flows of the clock notice (`clock_behind`) and of changed entity ids (`entity_ids_changed`) |
| `change.py` | Change Over Time (14-day and 7-day windows, both range sets) |
| `expiry.py` | Parsing of the optional sensor expiry entity |
| `contrast.py` | Contrast measurement of the chosen colors for the settings note (`ACCESSIBILITY.md` rule 2) |
| `panel_feed.py` | GLU2 version 3 and GCX1 files, panel webhook |
| `panel_notice.py` | Panel Added / Panel Offline notifications; a deleted panel forgotten |
| `maintenance.py` | The Maintenance status: offline panels, open Repairs issues, a failed CSV export |
| `http_api.py` | REST endpoints (binary, data, stats, report, report.pdf) |
| `report.py`, `report_pdf.py`, `pdf_tags.py`, `report_service.py` | CGM Report: calculations, PDF, PDF tags for screen readers, storage, printing, laboratory A1C |
| `frontend.py` | Static file and Lovelace resource registration (versioned paths) |
| `strategy_dashboard.py` | The GlucoCraft dashboard (strategy) in the sidebar |
| `history_import.py` | Recorder history import with unit detection |
| `kept.py` | The readings, laboratory A1C results and sensor events kept for a reinstall (*Keep the readings for a reinstall*), found, adopted or deleted at the next setup |
| `notification.py` | Daily / weekly / monthly summaries, the optional no-data notice, phone forwarding |
| `diagnostics.py` | Home Assistant diagnostics download |
| `manifest.json`, `quality_scale.yaml`, `icons.json`, `brand/` | Integration manifest (requires ReportLab), integration quality scale, icons |
| `strings.json`, `translations/en.json`, `services.yaml` | User interface strings, action descriptions |
| `fonts/`, `THIRD_PARTY_NOTICES.md`, `LICENSE-Apache-2.0.txt` | The fonts of the PDF (Liberation Sans), the third-party license notices and the Apache License 2.0 text (the display card's Material Design Icons path) |

### Frontend (custom_components/glucocraft/www/)
| File | Purpose |
|------|---------|
| `glucocraft-strategy.js` | The GlucoCraft dashboard (views and layout) |
| `glucocraft-display-card.js` | Display card: square and rectangular variants |
| `glucocraft-ui.js` | The dashboard's cards |
| `glucocraft-report.js` | CGM Report page |
| `glucocraft-docs.js` | Documentation page |
| `glucocraft-i18n.js` | The language module of the cards and pages (the text catalogues in `i18n/`) |
| `glucocraft-shared.js` | The cards' shared module (`GCSHARED_VERSION`): the rules every card and page uses, written once (the number, glucose, time and date formats, the two-decimal rule, the spoken text, the gap rule, the default colors, GlucoCraft's link and button colors, the GLU2 parser) |

### Wall panels (esphome/)
The five example panel configurations (`glucocraft-*.yaml`), a starting configuration (`glucocraft-starter.yaml`),
`secrets.yaml.example` (placeholders only), the panel text catalogues and the C++ headers they include, among them
`glucocraft_panel_binary.h` (the GLU2 / GCX1 reader) and `gc_units.h` (units, rounding and the gap rule).

### Tests and tools
| Path | Purpose |
|------|---------|
| `tests/` | The test suite (`tests/README.md`) |
| `tests_ha/` | The tests on Home Assistant itself (its test harness; `tests/README.md`) |
| `tools/synth/` | Synthetic CGM data and independently calculated expected values for the tests |
| `tools/i18n/make_pseudo.py` | The generated test language for the text catalogues |
| `tools/check_pdf_ua.py` | PDF/UA check of the CGM Report PDF |
| `tools/make_brand_icon.py` | The integration icon |

### Documentation
| File | Purpose |
|------|---------|
| `README.md` | Overview, installation, features |
| `CONTRIBUTING.md`, `SECURITY.md` | Contributing and security reporting |
| `docs/SYSTEM_DESIGN.md` | This document |
| `docs/DEVELOPER_GUIDE.md` | Development setup, tests and conventions |
| `docs/ENTITIES_AND_API.md` | Every entity, attribute and endpoint |
| `docs/DISPLAY_CONTRACT.md` | What the display card and the panels show, and why |
| `docs/PANELS.md` | The wall panels: setup, panel types, pages and controls |
| `docs/DASHBOARD.md` | The GlucoCraft dashboard and its pages |
| `docs/LANGUAGE_STYLE.md` | Wording rules for all user-facing text (rule 14: no claims) |
| `docs/ACCESSIBILITY.md` | Accessibility rules |
| `docs/SCREEN_READER_TEST_SCRIPT.md` | A guide for testers who use a screen reader: what each page is designed to read |

### Repository
| File | Purpose |
|------|---------|
| `LICENSE` | AGPL-3.0-only |
| `hacs.json` | HACS / Marketplace manifest |
| `.github/` | Issue forms, pull-request template, workflows (tests, HACS validation, hassfest) |
| `pyproject.toml`, `pytest.ini`, `requirements_test.txt`, `eslint.config.js`, `.editorconfig`, `.gitattributes`, `.gitignore` | Tool settings |

---

## 16. Installation, Actions and Removal

- **One installation per Home Assistant** (`manifest.json` `"single_config_entry": true`; the setup flow also aborts a
  second one with `single_instance_allowed`). One installation serves one person and one CGM system; a second system
  that reads the same sensor is the second source.
- **Entry versions:** `VERSION` 2, `MINOR_VERSION` 3 (`const.ENTRY_VERSION` / `ENTRY_MINOR_VERSION`), the first format
  of the published GlucoCraft: there is nothing to migrate, and `async_migrate_entry` refuses an entry of a newer
  version (a later change of the stored settings adds its step there).
- **Runtime data:** the coordinator is `entry.runtime_data` (`coordinator.loaded_coordinator` finds it for the HTTP
  views, the actions and the Repairs flows). The coordinator is created with its config entry; its shutdown runs once.
- **Actions** are registered once in `async_setup` (`CONFIG_SCHEMA` = config entry only): `send_daily_summary`,
  `send_weekly_summary`, `send_monthly_summary`, `add_lab_a1c`, `remove_lab_a1c`, `print_report`, `export_csv` and
  `import_csv`; without a loaded entry they raise `ServiceValidationError` (`not_loaded`). All eight are for administrators (calls
  from automations have no user and are allowed); the summary actions too, because a summary is also sent to the
  phones chosen for the summaries. A print that fails raises `HomeAssistantError` (`print_failed`,
  `print_pdf_failed` when the PDF could not be made, or `print_no_printer` without a printer address); an export
  that fails raises `export_failed`, a summary that cannot be sent `summary_failed`, a laboratory result that cannot be
  stored `lab_add_failed`, one that cannot be removed `lab_remove_failed`, and an import of a file that cannot be read
  `import_failed`; refused with `ServiceValidationError`: a laboratory result outside its span or with a date or unit
  that cannot be read (`lab_invalid`), a report period the CGM Report does not have (`report_period`), the custom
  appendix while *Custom ranges and goals* is off (`custom_appendix_off`), and an import without such an export
  (`import_no_file`) or of a file that is not a readings export (`import_not_an_export`) (each a translated text,
  `strings.json` `exceptions`).
- **Repairs issues:** `source_unit_unknown` (a source without a unit whose values look like mmol/L),
  `source_unit_not_glucose` (a source that states another unit), `source_missing` (a source entity that does not
  exist), `source_is_glucocraft`, `slow_source`, `source_stuck` (a source without its own time that repeats one value
  for over an hour), `sensor_cap`, `sensor_floor`, `sensor_at_upper_limit` and `sensor_at_lower_limit` (readings flat
  at the automatic limit itself, sent as numbers), `hand_limit_low` and `hand_limit_high` (numbers beyond a sensor limit
  set by hand), `expiry_entity_missing`, `expiry_entity_unrecognized` and `expiry_entity_past` (the sensor expiry entity
  does not exist, holds no expiry, or gives one long past), `source_mmol_too_high`, `source_time_attribute`,
  `fast_source`, `clock_behind`, `entity_ids_changed` (an entity's id is not the one it was created with; `renamed_entities.py`), `source_mgdl_too_low` (a source stating mg/dL whose values look like mmol/L), `clock_ahead`, `storage_recovered`,
  `storage_damaged`, `storage_unreadable`, `storage_save_failed` (the readings file could not be written), `storage_newer`, `storage_recovered_not_kept`, `storage_damaged_not_kept` (the storage keys
  except `storage_save_failed`, which is its own issue id, share the issue id `storage_problem`),
  `range_below_sensor_limit` and `range_above_sensor_limit`,
  `lovelace_resources_failed` (the card files could not be added as dashboard resources), `dashboard_failed`
  (GlucoCraft's dashboard could not be added to the sidebar) and `history_import_failed`
  (the source's history could not be read from the recorder) (`ENTITIES_AND_API.md`, Repairs issues).
- **Unload:** the coordinator is shut down only when the platforms unloaded; a disabled entry removes the dashboard.
- **Entities:** one service device (`entity.py`, `DeviceEntryType.SERVICE`); `PARALLEL_UPDATES = 0` on both
  platforms.
- **Frontend:** in Lovelace YAML resource mode the cards are loaded with `add_extra_js_url`.
- **Notification text** (`ACCESSIBILITY.md` rule 7): in the bodies of the summaries a glucose value carries its unit
  in words in the display unit ("112 milligrams per deciliter", "6.2 millimoles per liter"; `notification.unit_words()`, catalogue keys `spoken.unit_name.*`); a summary block names it once in its heading ("**Rolling 24 Hours** (glucose in
  milligrams per deciliter):"); the trend is written in words beside the arrow. Titles, percent, A1C and every other
  text keep their symbols ("mg/dL", "%").
- **Outline text** (option `text_outline`, in *Display card and wall panels*; `false` by default, published as the
  configuration attribute `text_outline`): when on, a 1-pixel edge around the graph labels, the glucose value, LO / HI
  and every other text in a zone color, on the display card and every panel, black around a light text and white
  around a dark one (`DISPLAY_CONTRACT.md`).
- **The zone word under the value and Trend shown as** (option `trend_display`, in *Display card and wall panels*):
  every display shows the zone word under the glucose value in every setting (Very low, Low, In range, Tight range,
  High, Very high; the Last glucose reading sensor's `display_word`, empty while there is no current data and during
  a warm-up, section 4.7), so the zone never depends on color alone. The setting chooses how the TREND column shows
  the trend: `arrow` (default) or `word`, the trend word, the broader category of the same rate (the Trend arrow
  sensor's `word`, the category in sentence case, empty while the trend is unknown; a longer word on two lines);
  published as the configuration attribute `trend_display`. The square panel's corner value on the weekly pages keeps
  the arrow.
- **Validation:** `.github/workflows/validate.yml` (HACS action) and `hassfest.yaml`. The 15 rules of Home Assistant's
  integration quality scale that `quality_scale.yaml` marks exempt, with the reason: `test-before-setup` (setup must start without the
  source so that the stored data stay visible), `entity-unavailable` (the last reading stays with its own time; *No
  Current Data* and the status report the age), `reauthentication-flow` (no credentials), `appropriate-polling` (no
  polling: event-driven; a one-minute internal tick checks whether the data are current, and the statistics are
  recalculated every 5 minutes), `docs-examples` and `docs-use-cases` (the documentation describes what GlucoCraft
  and its entities do; GlucoCraft provides no automation examples), `docs-conditions` and `docs-triggers` (none
  provided), `discovery` and `discovery-update-info` (nothing to discover), `dynamic-devices` and `stale-devices`
  (one fixed device), `reconfiguration-flow` (Configure changes every setting), `async-dependency` and
  `inject-websession` (no device library; the printer request uses the standard library).

---

## 17. Known Limitations

- GlucoCraft is designed for sources that deliver a reading every 5 minutes or more often; a source that delivers
  every 15 minutes or less often is not supported for live display (section 2).
- A source without its own measurement time cannot tell GlucoCraft that it has stopped: if it keeps writing its last
  value, GlucoCraft shows that value as current until the notice of section 2 (*A source that keeps sending its last
  value*) appears after an hour.
- GlucoCraft sees only the readings it receives: *sensor active* measures data reception (section 4.2), and the CGM
  system's own data can contain readings that did not reach GlucoCraft.
- Weekday and month names come from the language catalogue, which is provided in English only; letters that are not
  in the panel fonts (for example of non-Latin scripts in the source name) are left out on the panels.
- The example wall-panel configurations are visual only (`ACCESSIBILITY.md`, Known limitations).
