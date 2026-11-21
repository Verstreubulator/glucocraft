# GlucoCraft wall panels

GlucoCraft's disclaimer is at the top of the [README](../README.md).

GlucoCraft wall panels are optional ESP32 displays built with ESPHome from GlucoCraft's example panel configurations
(the folder `esphome/` of the repository). The configurations are examples. They are not a finished or
ready-to-use product. GlucoCraft provides no firmware: the user's own ESPHome builds the firmware (the program on
the panel) from them. A panel shows the same pages as the display card of the dashboard ([DASHBOARD.md](DASHBOARD.md)); GlucoCraft decides
every value, word, color and direction, and the panel only shows them ([DISPLAY_CONTRACT.md](DISPLAY_CONTRACT.md)).

The user provides the panel. This guide describes the panel types of the example configurations; what it says about
a type applies to a panel of that type, where one is built. A user can build any of them, several or none, adapt a
configuration, or build a display of their own from the published data format
([DISPLAY_CONTRACT.md](DISPLAY_CONTRACT.md)).

## Contents

- [Panel types](#panel-types)
- [What a panel shows](#what-a-panel-shows)
- [Short labels](#short-labels)
- [Navigation](#navigation)
- [Adding a panel](#adding-a-panel)
- [Building a panel from the examples](#building-a-panel-from-the-examples)
- [Removing a panel](#removing-a-panel)
- [Changing the wall-panel address](#changing-the-wall-panel-address)
- [Troubleshooting](#troubleshooting)
- [Waveshare P4 7B: start-up and updates](#waveshare-p4-7b-start-up-and-updates)
- [Language of the panel texts](#language-of-the-panel-texts)
- [The fit check](#the-fit-check)
- [Background and colors](#background-and-colors)
- [Known limitations](#known-limitations)
- [References](#references)

## Panel types

The example configurations cover five panel types, the boards they were developed and tested on; another board
needs a configuration of its own (a starting point is the example of the closest screen size and orientation;
[DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md), section 9, describes how the examples are made and checked, and
[DISPLAY_CONTRACT.md](DISPLAY_CONTRACT.md) what every display shows). Such a board needs what the examples use: an
ESP32-S3 or ESP32-P4 with PSRAM (the panel keeps two 1 MB buffers there for the data file: the one shown and the next
download), 8 MB of flash or more (the smallest of the examples), a display that LVGL in ESPHome can drive, and a
touchscreen. Each type is identified by its ESPHome project name; Home Assistant shows
"glucocraft" as the device's manufacturer, the panel type as its model and the configuration's version as its
firmware version.

| Panel | Board | Screen | Configuration | Project name |
|---|---|---|---|---|
| Square 480 × 480 panel | Guition ESP32-S3-4848S040 | 4.0 in, 480 × 480, LCD, touch | `glucocraft-panel.yaml` | `glucocraft.guition4848s040-480x480` |
| 3.5-inch panel | Guition JC3248W535 | 3.5 in, 480 × 320, LCD, touch | `glucocraft-guition-35.yaml` | `glucocraft.guition35-480x320` |
| Waveshare 5-inch LCD | Waveshare ESP32-S3-Touch-LCD-5 (Rev 1.1) | 5.0 in, 1024 × 600, LCD, touch | `glucocraft-waveshare-lcd5.yaml` | `glucocraft.waveshare-lcd5-1024x600` |
| Waveshare P4 7B | Waveshare ESP32-P4-WIFI6-Touch-LCD-7B | 7.0 in, 1024 × 600, LCD, touch | `glucocraft-waveshare-p4-7b.yaml` | `glucocraft.waveshare-p4-7b-1024x600` |
| Waveshare 7-inch LCD | Waveshare ESP32-S3-Touch-LCD-7 (Rev 1.2) | 7.0 in, 800 × 480, LCD, touch | `glucocraft-waveshare-lcd7.yaml` | `glucocraft.waveshare-lcd7-800x480` |

The four landscape configurations are generated from the square panel's configuration by
`esphome/tools/make_glucocraft_landscape.py` and are not edited by hand ([DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md),
section 9).

**Smallest text.** The panels use much smaller text than the dashboard, and the text is physically smaller on the
smaller panels; a larger panel shows larger text. The physical height follows from the screen's pixel density:

| Panel | Pixel density | Smallest text | Its height |
|---|---|---|---|
| Square 480 × 480 panel | 170 ppi | 12 px (statistics labels) | 1.8 mm |
| 3.5-inch panel | 165 ppi | 12 px (statistics labels; "TREND", "5 MIN DELTA", the source name, the weekly description line and the GMI value in both A1C units) | 1.8 mm |
| Waveshare 5-inch LCD | 237 ppi | 14 px (graph axis labels) | 1.5 mm |
| Waveshare P4 7B | 170 ppi | 14 px (graph axis labels) | 2.1 mm |
| Waveshare 7-inch LCD | 133 ppi | 14 px (graph axis labels, statistics labels) | 2.7 mm |

**Entities of a panel.** Every panel has the entities Status, Uptime, Free Memory, Free Memory Minimum, Free Memory
Largest Block, CPU Temperature, Display Backlight, Restart and Request Backfill (a new download of the full data
file). The Waveshare P4 7B also has *Wi-Fi Co-processor Firmware*, the firmware version of its Wi-Fi chip (see
[Waveshare P4 7B: start-up and updates](#waveshare-p4-7b-start-up-and-updates)). The configurations also contain
the Safe Mode button of ESPHome, marked internal, so Home Assistant does not show it.

**Board notes** (from the configurations and the boards):

- **Square 480 × 480 panel:** its USB socket reaches the chip through a USB-to-serial chip (CH340), while ESPHome
  writes the panel's log to the ESP32-S3's own USB, which this board does not connect to the socket: over the cable,
  the panel shows only the start-up lines of its chip. Flashing and restarting over the cable work; the panel's log is
  read over Wi-Fi (*Logs* in the ESPHome Device Builder, or `esphome logs`).
- **Waveshare 5-inch LCD and Waveshare 7-inch LCD:** the backlight can only be switched on and off (no dimming).
- **3.5-inch panel:** the landscape picture is made by rotating the drawing in software (an ESPHome feature since
  2026.5, within the minimum version below).
- **Waveshare P4 7B:** see [Waveshare P4 7B: start-up and updates](#waveshare-p4-7b-start-up-and-updates).

## What a panel shows

### Screen layout

- **Header:** the clock, the weekday and the date, in GlucoCraft's *Time format* and *Date format*.
- **Glucose block:** the glucose value in the display unit (mg/dL as whole numbers, mmol/L with one decimal), in its
  zone color; under it, in every setting, the zone word ("Very low", "Low", "In range", "Tight range", "High" or "Very
  high"); left of it **TREND**, one arrow turned by the rate of change or, with the setting *Trend shown as* set
  to *Word*, the trend word, the broader category of the same rate (a longer word on two lines, "Changing /
  slowly"), with the source's own name under it; right of it **5 MIN DELTA**, the change of the smoothed glucose over 5 minutes. The trend
  follows the five trend categories of the cited manufacturer documentation (Abbott Diabetes Care, 2022); GlucoCraft
  calculates the angle, the category and the word, and the panel draws them. While GlucoCraft has too few
  earlier readings for a trend (after a gap or with a new sensor), TREND stays empty and 5 MIN DELTA
  shows "---". The square panel's small value in the
  corner of its weekly pages shows the trend as the arrow with either setting, while a trend
  is known. While the current reading is outside the sensor's measurement
  range, the value's place shows words instead of a number (see *Readings outside the sensor's range* below). Without
  a current reading (no current data, a sensor warm-up) the value's place shows words instead of a number, the zone
  word is empty and the trend and the 5-minute delta show "---" (see *No current reading* below); after the stated
  sensor expiry, while readings still arrive, the value keeps its zone color and the zone word and the trend follow the
  data; the red status bar shows that the stated expiry has passed.
- **Page:** a graph page with its statistics, a weekly page, or an About page.
- **Status bar:** along the bottom, described below.

On the square panel the glucose block sits above the page; on the weekly pages it is replaced by the weekly
information, and a small current value (the value in its zone color, its zone word, the trend arrow while a trend is
known and the time of the reading; at the sensor's limit LO / HI in its zone color with the time of the reading
only; without a current reading "---" in gray with the time of the reading only) stays in the top-right
corner. The square display card does not show this small value: it shows the current
value on the graph pages only, and on its weekly pages the status bar alone. The landscape panels keep the
glucose block on the left of every page, the page on the right and the status bar along the bottom.

### The pages

Every panel has 17 pages, the same as the display card, in this order (the panels show no page number; the display
card numbers them 1 to 17):

| Pages | Content |
|---|---|
| The seven graph pages | 6 hours, 24 hours, 7 days, 14 days, 30 days, 90 days and 180 days |
| The nine weekly pages | Time in range, time in tight range, mean glucose, CV, GMI, time low, time high, standard deviation, minimum and maximum |
| The About page (*About GlucoCraft*) | GlucoCraft's icon, its disclaimer, the version line and the copyright line |

**Graph pages.** Each graph is plotted by measurement time over the range bands of the ranges in use (the ranges in
the settings while the optional feature *Custom ranges and goals* is on, otherwise the consensus ranges), in the graph
range colors at 60% over the graph's near-black area (#0A0A0A), with a white glucose line and white labels on a
numbered axis. The 6-hour graph shows the readings themselves, one per 2-minute slot. The graphs of 24 hours to 6
months show, for each time slot, the lowest to the highest reading (the light-gray area with a black edge, key *Min to
max*) and the mean of the readings (the white line over a black underlay, key *Mean*), as on the display card; a time slot is one pixel column of the graph (on the square panel and the
square display card about 4 minutes at 24 hours and about 13 hours at 6 months). These graphs come from the full
history file, which the panel and the display card download every hour, so each ends at that download and can be up
to an hour behind the current reading; the 24-hour numbers under the 24-hour graph come from the recent-readings file
(every 5 minutes). No line is drawn across a gap: an interval between two readings that is longer than 15 minutes or
three reading intervals, whichever is longer, the reading interval being the median of the 10 intervals before the
reading. This method is a GlucoCraft rule ([DISPLAY_CONTRACT.md](DISPLAY_CONTRACT.md), Gap rule). Under
each graph twelve statistics of the period: time in range (TIR), time in tight range (TITR), low, high, very low, very
high, mean, standard deviation, CV, GMI, minimum and maximum. GMI is shown only for 14 days or more (the 6-hour to
7-day pages show the 14-day GMI, labeled GMI14d); GMI is not a laboratory A1C (Bergenstal et al., 2018). A minimum or
maximum shows measured readings only; a reading at the sensor's limit has no value. For the time of such
readings, a run of small marks is drawn along the bottom (LO) or top (HI) edge of the graph, in the very-low or very-high
color, with no text: the screen in the value's place names a limit reading while it lasts. A statistic is shown only when the sensor has covered at least 70% of the period (sensor active, data
received); otherwise it shows "---". The Documentation explains both (*Readings Outside the Sensor's Range* and
*How GlucoCraft Is Designed*).

**Weekly pages.** Each page shows the 14-day value at the end of each of the last 26 weeks, titled "<measure> - 14-Day
Values", with a description line and the line "Last 14 days: <value> <direction>" (on the Min and Max page "Min:
<min>  Max: <max>  <direction>"). They use one of two range sets, the consensus ranges or the custom ranges (the
setting *Range set of the weekly pages*, for the display card and every panel together). The setting is offered only
while the optional feature *Custom ranges and goals* is on and custom ranges or goals are set; otherwise the weekly
pages use the consensus ranges. Where a consensus target or a custom goal exists, the chart is green on the side within
it and red
on the side outside it, split at the target, and labeled *Consensus target* or *Custom goal*; pages without one are
neutral gray. The consensus targets shown are those of Battelino et al. (2019): time in range more than 70%, time below
70 mg/dL (3.9 mmol/L) less than 4%, time above 180 mg/dL (10.0 mmol/L) less than 25% and CV 36% or less. The direction is "Increasing",
"Decreasing" or "Stable": the mean of the last 4 weeks compared with the 4 weeks before, with a difference of more than
2% of the page's scale counted as a change. This method is a GlucoCraft rule. The direction is a numerical difference
without clinical interpretation. A week with less than 70% sensor active is shown as a week without data. The weekly values are calculated once an hour
and come from the full data file, so the newest can be up to about two hours old. The date under
every fourth
point ("Oct 12" or "12 Oct", in the *Date format*) marks the middle of that point's 14-day window: the week's end minus
7 days.

**Status bar.** Its color is GlucoCraft's status color (`const.py`, `STATUS_COLORS`): purple, a sensor warm-up or no
data yet (waiting); red, sensor expired or no current data; amber, the stated sensor expiry is less than 24 hours
away; green, current data. Gray appears only until GlucoCraft's status color has arrived, for example in the first
seconds after a panel starts. The texts are described under [Short labels](#short-labels): the age of the newest
reading on the left, "Warm-up" or "Custom ranges" in the middle (taking turns with "Check Home Assistant" while
GlucoCraft has a Repairs notice open), the time until the stated sensor expiry on the right where an expiry entity is
set. The bar's text is black or white, whichever contrasts more with the bar color.

**No current reading.** Without a current reading the value's place shows words, never a number: "Sensor warming
up" during a sensor warm-up, when no readings are expected (also with a reading still within the *No current data
after* interval); after that interval (GlucoCraft's flag, or the panel's own count of the reading's age while Home
Assistant cannot reach it) "Sensor expired" once the expiry time stated by the sensor-expiry entity has passed, else
"No current data". A panel also treats the value as no current data when the value it holds has not been the newest
reading's for over a minute, which happens while the id of Last glucose reading is changed and not undone:
it waits for a new reading that differs, then 60 seconds, and never shows an old number as current. The words are in the text color at the zone word's large size (40 px; the 3.5-inch panel 32 px),
on two lines where needed ("No current / data"). Under them, smaller and still, the last reading ("Last reading:
64 mg/dL", in mmol/L with one decimal, "Last reading: LO" / "Last reading: HI" for a reading outside the sensor's
range) and when it was taken with its age ("Today at 17:39 (2 h 0 min ago)"; "Yesterday at ...", or the date for
an older reading; the age in minutes, hours and minutes, or days and hours), in Home Assistant's time zone and the
time and date formats of GlucoCraft's settings. On the square and 3.5-inch panels the age has a line of its own
("Today at 17:39" / "(2 h 0 min ago)"). Without any reading only the words show. A panel keeps the last reading
across a restart, also without Home Assistant. The zone
word is empty, and TREND and 5 MIN DELTA show "---". The status bar keeps the age of the reading ("Reading: 2 h ago")
and the status, with "Warm-up: 12 min" in the middle during a warm-up; the graphs keep the history. All three words
throb for the first 30 minutes: they fade to about 30% brightness and back once every 3 seconds, and never disappear;
then they hold still for as long as there is no current reading. The 30 minutes count from the age of the reading (during a
warm-up, from its start), so a restart of the panel does not start them again; where neither is known, from when the
panel first shows the words. While readings still arrive after the stated expiry, the value
keeps its zone color, with its zone word, the trend (arrow or word) and the 5-minute delta, and the status bar shows
"Sensor expiry passed" in red, until a later expiry time is received; that text throbs in the same way until a new sensor
is recognized, without the 30-minute limit. The setting *Fade the words when there is no current reading* (on by
default) turns both off. A sensor warm-up ends by itself after 24 hours at the latest; then *No current data* applies
as usual.

**Readings outside the sensor's range.** While the current reading lies below or above the sensor's measurement range
(LO / HI), the value's place shows four lines instead of a number, in the layout of *No current reading*: "Very low"
or "Very high", large, in the very-low or very-high color; "Below sensor range (LO)" or "Above sensor range (HI)";
since when, "Since 3:12 AM (2 h 5 min)"; and the last measured reading before that stretch, "Last measured: 52 mg/dL
at 3:10 AM". The TREND column is blank, and 5 MIN DELTA shows
"---". A data gap starts a new stretch; the start and the last measured reading are
kept across a restart. Once the reading is older than the *No current data after* interval with no new reading, the
*No current reading* screen applies, with "Last reading: LO" or "Last reading: HI".

**Reading time ahead of the clock.** While the newest reading's time is more than 5 minutes ahead of Home Assistant's
clock, its age cannot be known: the status bar shows "Reading: ---", and no time line is shown under the words. The
dashboard's Maintenance page explains what to check.

**Check Home Assistant.** While GlucoCraft has a notice open in Home Assistant's Repairs, the middle of the status bar
also shows "Check Home Assistant", taking turns every few seconds with "Custom ranges" or the warm-up text. The
notice's own text is not shown, nothing else changes and there is no sound; the text goes away when the notice closes,
or when *Ignore* is chosen on it in Repairs.

**Without Home Assistant.** After one hour without the connection to Home Assistant or without a successful download of
GlucoCraft's data files, everything below the clock and the date turns black with "Cannot reach Home Assistant or load
its data", since when ("Since 9:41 PM (1 h 12 min)"), the last reading with its time ("Last reading: 112 mg/dL at
9:40 PM") and "Check Home Assistant and the wall-panel address". The clock and the date stay (after a restart without
Home Assistant the clock shows "--:--" until a time source has set it: Home Assistant, or the local time server
`ntp_router` where one is set); swipes and the press and hold change no page while it is shown, and it does not
throb. It
clears by itself when both work again. What to check: that Home Assistant is running and the panel is connected
(Settings > Devices & services > ESPHome: the panel's device online), and that the panel's `secrets.yaml` holds the
current wall-panel address id and `ha_host` / `ha_port` (the id is shown under Settings > Devices & services >
GlucoCraft > Configure > *Display card and wall panels*; see [Changing the wall-panel
address](#changing-the-wall-panel-address)). Before the hour, the panel shows *No current data* as usual. GlucoCraft does not observe the sensor itself, only the stated
expiry. Apart from this screen, pressing and holding the glucose block opens the weekly pages in every state of the
value (when weekly data exist).

**Start-up screen and About page.** At every start, the panel shows GlucoCraft's icon, its name, the panel's
firmware version ("Panel version ...") and "For development and testing only." and "Not for medical use.", each on a
line of its own, for 15 seconds, then continues by itself; a swipe ends it early. The About page shows GlucoCraft's
icon, its disclaimer (short form), and at the bottom, below a thin line, the version line ("GlucoCraft Panel v...") and the
copyright line ("Copyright (C) 2026 The GlucoCraft authors. License: gnu.org/licenses/agpl-3.0"). On the square panel
the icon is centered at the top; on the wide panels it stands in a column of its own on the left, with the texts
centered in the column beside it.

**Restarts and outages.** A panel does not restart when Home Assistant is unavailable. Without Wi-Fi, a panel
restarts only after 60 minutes without a connection (checked every 5 minutes); a shorter Wi-Fi outage does not
restart it. A panel also restarts by itself when its free internal memory is below 20 KB at three checks in a row,
2 minutes apart. It keeps the last value, arrow
and word while Home Assistant restarts, and shows "No current data" from the age of the reading, by its own clock
(except during a sensor warm-up). A
panel keeps the last reading (its time, its value and its LO / HI mark) across a restart of the panel: the status
bar counts the age of that reading, and once it passes the limit "No current data" takes the value's place with that
reading under it ("Last reading: 64 mg/dL" and when it was taken), never in the value's place. Until then, a panel
that restarted without Home Assistant shows "---" in gray in the value's place. The reading is saved
at most every 10 minutes, so the reading kept can be up to about 10 minutes older than the last reading shown. Every panel keeps
the last time zone received from Home Assistant, its background and text colors, and the settings *Outline text*,
*Fade the words when there is no current reading* and *Trend shown as*; the graph range colors are the defaults after a
restart until Home Assistant sends them. The full data file is downloaded at start, every hour and about 15 seconds after a change of the display unit, the
A1C and GMI unit, the ranges, the goals or the weekly pages' range set; the recent-readings file every 5 minutes; a gap
that the panel missed is filled once GlucoCraft has the readings.

## Short labels

The panels and the display cards have little room, so some labels are abbreviated. They are the same on the display
card and every panel, and are defined here and in the Documentation's Glossary. For lack of room the displays
consistently use the short forms *Min*, *Max* and *Avg* (also in the weekly page name "Weekly: Min and Max"); the
dashboard and the CGM Report keep *Lowest* and *Highest* (and *Mean*), and the screen-reader texts keep the words.

- **Statistics under each graph** (the period of the page, on the ranges in use): *TIR*, time in range; *TITR*, time
  in tight range; *Low* and *High*, time in level 1 below and above the range (between the very-low and the low limit,
  and between the high and the very-high limit); *Very low* and *Very high*, time in level 2 (below the very-low and
  above the very-high limit); *Avg*, the time-weighted mean glucose; *SD*, standard deviation; *CV*, coefficient of
  variation; *Min* and *Max*, the lowest and the highest measured reading.
- **GMI14d, GMI30d, GMI90d, GMI180d:** the glucose management indicator (GMI) of the last 14, 30, 90 or 180 days. The
  6-hour, 24-hour and 7-day pages show GMI14d, because GMI is calculated only for 14 days or more (Bergenstal et al.,
  2018). The value is in the A1C / GMI unit; in mmol/mol it is calculated from the period's mean glucose with the
  mmol/mol form of the GMI formula, as on the dashboard and in the report. A panel's cell has no room for "mmol/mol", so
  it shows "48" in mmol/mol and "6.5%/48" with both units; so does the statistics row under the graphs of the square
  display card, which has the square panel's room, and the rectangular display card with both units. The display
  card's other GMI values show the unit ("6.5%", "48 mmol/mol").
- **---** in place of a value: no value (no readings in the period, less than 70% sensor active in the period, or,
  for GMI, less than 14 days).
- **TREND:** the trend arrow, turned by GlucoCraft's rate of change; the name under it is the name of the source in
  use. **5 MIN DELTA:** the change of the smoothed glucose over 5 minutes, in the display unit. **LO / HI:** a reading
  below the sensor's lower limit or above its upper limit (no number was measured). The values, the 5-minute delta and
  the axis labels are in the display unit, which is not repeated beside each number; in mmol/L each axis has one format
  (7.0 / 6.5 / 6.0, or 4 / 6 / 8), on every graph.
- **Status bar:** *Reading: 3 min ago*, the whole minutes since the measurement time of the newest reading, rounded
  down, up to *Reading: 99 min ago*, and from 100 minutes the whole hours, rounded down (*Reading: 1 h ago*,
  *Reading: 2 h ago*); it is the age of the reading, not the time the screen or a file was updated ("Reading: ---"
  before any reading); *Warm-up: 12 min*,
  the minutes since GlucoCraft recognized a sensor warm-up, not the time remaining ("Warm-up" without a start time);
  *Custom ranges*, while custom ranges that differ from the consensus ranges are in use; *Sensor Exp: 3d 4h*, *Sensor
  Exp: 5h 12m* or *Sensor Exp: 42m*, the time remaining until the expiry stated by the sensor-expiry entity ("Exp",
  expiry; "Sensor Exp: ---" on a panel until the countdown is calculated; nothing without an expiry entity); *Sensor
  expiry passed*, once that time has passed. *d*, *h* and *m* are days, hours and minutes; *min* and *h* in
  "Reading: 3 min ago" are minutes and hours and have no plural form.
- **Graph key** of the graphs of 24 hours to 6 months: *Min to max*, the light-gray area from the lowest to the highest
  reading of each time slot; *Mean*, the white line of the mean of the readings in each slot. The "---" before each
  word is the drawn sample of the area and of the line.
- **Weekly pages:** *14-Day Values*, each point the value of the 14 days ending at a week's end; *TIR %* and *TITR %*,
  time in range and time in tight range; *Time Low* and *Time High*, the time below and above the range (level 1 and
  level 2 together); *SD*, standard deviation; *Min and Max*, the lowest and the highest reading of each 14-day period.
  *Increasing*, *Decreasing* and *Stable* after the value are the direction of the last 4 weeks against the 4 weeks
  before (see [The pages](#the-pages)); this *Stable* is not the trend category of the arrow. *Consensus target* and
  *Custom goal* name whose target colors the chart.
- **Citations** on the panels and the display cards are short (author and year, for example "(Battelino 2019)"); the
  full references are in the Documentation's References. The weekly CV page, for example, reads "SD / mean.
  Consensus ≤36% (Battelino 2019; Monnier 2017)", the target of 36% or less of Battelino et al. (2019), with Monnier et
  al. (2017).
- **Other abbreviations in this guide:** *CGM*, continuous glucose monitoring (the CGM system: the sensor with its
  app, reader or receiver); *A1C*, the laboratory hemoglobin A1C test, whose units GMI uses (% and mmol/mol, millimoles
  per mole); *mg/dL* and *mmol/L*, milligrams per deciliter and millimoles per liter; *ppi*, pixels per inch; *px*,
  pixels; *LCD*, liquid-crystal display; *UTC*, Coordinated Universal Time; *NTP*, Network Time Protocol, the
  protocol of a time server; *HTTP* and *HTTPS*, the Hypertext Transfer Protocol, without and with encryption.

## Navigation

The panels have no on-screen buttons; the display card (on GlucoCraft's dashboard, or on any
dashboard it is added to) offers the same pages with buttons.

- **Swipe left** anywhere on the screen: the next page. **Swipe right:** the previous page. The pages form one cycle,
  from the About page back to the 6-hour page.
- **Press and hold the glucose block for 1 second** on a graph page: the first weekly page (when weekly data exist).
- **Press and hold the weekly information for 1 second** on a weekly page: back to the 6-hour page. On the landscape
  panels, which keep the glucose block on the weekly pages, pressing and holding the glucose block on a weekly page
  also returns to the 6-hour page.
- After 3 minutes without a touch, the panel returns to the 6-hour page.

The 1-second hold is used instead of a tap, so that a swipe that starts on these areas is never read as a tap. Page
changes are instant, without animation.

## Adding a panel

A panel is the user's own device: GlucoCraft provides the example configurations, this guide and the wall-panel
address; the user builds, installs, uses and removes the panel.

1. Create the wall-panel address, once, before the first panel (step 1 of
   [Building a panel from the examples](#building-a-panel-from-the-examples)).
2. Build the panel from the examples and install it (steps 2 to 6).
3. Add the device in Home Assistant (step 7).

When a panel connects for the first time, the notification *GlucoCraft Panel Added* names its shape, the display card
in use and whether the two match, and where the display card is chosen. Panels that are already in Home Assistant the
first time GlucoCraft checks for panels get no notice. The panel appears on the dashboard's *Maintenance* page. When a
panel's Status entity has been off or unavailable for 10 minutes, the notification *GlucoCraft Panel Offline* names
it; it is removed when the panel is connected again or its device is deleted from Home Assistant, and while it is
shown the panel counts as an issue of the dashboard's Maintenance status ([DASHBOARD.md](DASHBOARD.md)).

## Building a panel from the examples

**What is needed:** ESPHome 2026.10 or later (the ESPHome Device Builder, or `pip install esphome`; the configurations
use its forms and were last built and installed with 2026.10.1), a board of one of the types above (another board needs a
configuration of its own), a USB cable for the first flash, a 2.4 GHz Wi-Fi network through which the panel reaches
Home Assistant (the example configurations connect over Wi-Fi), and GlucoCraft in Home Assistant.

1. **Create the wall-panel address.** In Home Assistant, select **Settings > Devices & services > GlucoCraft > Configure >
   Display card and wall panels**, tick **Create a new wall-panel address** and select **Submit**. Open the same
   screen again: it shows the address of the panels' data and its id (to administrators only, never in a
   notification). No address exists until one is created. Create the address before building the first panel: the
   build needs its id. After that, a new address is created only if the address may have been exposed, which stops
   every wall panel until each one is built and installed again with the new id (see
   [Changing the wall-panel address](#changing-the-wall-panel-address)).
2. **Copy the folder.** Copy the contents of the repository's `esphome/` folder into the ESPHome configuration folder
   (for the ESPHome Device Builder app of Home Assistant, the folder `esphome` in Home Assistant's configuration
   folder; for `pip install esphome`, the folder the `esphome` commands are run in). The configurations need the
   headers, the generated texts in `i18n/` and the images beside them.
3. **Fill in the secrets.** Copy `secrets.yaml.example` to `secrets.yaml` (or add its entries to an existing
   `secrets.yaml`) and replace every value: `wifi_ssid` and `wifi_password`; `glucocraft_panel_webhook_id`, the id from
   step 1; and, for each panel, its own API key (a new 32-byte base64 key, which the ESPHome Device Builder can
   suggest; it is also the key for updates over Wi-Fi) and the password of its fallback access point (8 characters or
   more): when the panel cannot join the Wi-Fi, it opens a network of its own, which nearby devices can see, with a
   page for setting the Wi-Fi again; this password protects it. The names of each panel's secrets are in the header
   of its configuration. `secrets.yaml` is never published or shared.
4. **Set Home Assistant's address and port.** Open `esphome/glucocraft-starter.yaml` and set `ha_host`, the IP
   address of the Home Assistant that runs GlucoCraft, not a `.local` name such as `homeassistant.local`, which the
   panel may not resolve (the placeholder is a documentation address that reaches nothing), and `ha_port`, its HTTP
   port (8123, Home Assistant's default, unless it was changed). The panel reads its files over plain HTTP, so a Home
   Assistant that answers only over HTTPS needs a plain-HTTP address the panel can reach.
   - *Optional:* `ntp_router`, the IP address of a time server on the local network (for example a router that
     serves NTP). The panel takes its time from Home Assistant; the time server is used only by a panel that starts
     while Home Assistant is not connected. Without it, the configuration uses the Home Assistant address: the panel takes its time from Home
     Assistant only (unless a time server answers there), and a panel that starts while Home Assistant is not
     connected has no time until Home Assistant connects.
   - *Waveshare P4 7B only:* `engineering_sample`, the chip revision of the board (see
     [Waveshare P4 7B: start-up and updates](#waveshare-p4-7b-start-up-and-updates)).

   Each line of the starter file replaces the board configuration's own value. A panel connects only to Home
   Assistant (its encrypted ESPHome connection, and the panel files from GlucoCraft's wall-panel address) and, where
   one is set, to the time server. The configurations contain no connection to the internet; building the firmware
   downloads the components of ESPHome, the Roboto font (from Google Fonts) and the `mdi:arrow-right` icon of the trend
   arrow (from the Material Design Icons repository on raw.githubusercontent.com) on the computer that builds it, not
   on the panel.
5. **Choose the board.** Under `packages:` in the starter file, the line `panel: !include glucocraft-panel.yaml`
   names one board configuration; `glucocraft-panel.yaml` is the default. Replace it with the configuration of the
   board in use.
6. **Build and flash.** Build and install the starter file, never a board configuration on its own (a board
   configuration is included by the starter file and keeps the placeholder address of step 4):
   - *Command line:* run `esphome run glucocraft-starter.yaml` (over USB the first time, then over Wi-Fi).
     `esphome compile glucocraft-starter.yaml` builds without flashing.
   - *ESPHome Device Builder:* the folder of step 2 shows a card for the starter file and one for each board
     configuration. Select **Install** on the card of `glucocraft-starter.yaml`. The first installation needs the USB
     cable: connect the board to the computer and choose the Device Builder's option for a board connected to this
     computer, or download the firmware and flash it with the web flasher of ESPHome; after that, **Install** updates the
     panel over Wi-Fi.
7. **Add the device in Home Assistant.** Select **Settings > Devices & services > ESPHome** and add the new device
   with its API key from step 3. When the ESPHome Device Builder app runs in the same Home Assistant, Home Assistant
   takes the key from the Device Builder and does not ask for it.

**Panels of different types.** Each board configuration has its own device name and secrets. Copy the starter file
once for each board (for example `glucocraft-starter-waveshare-lcd7.yaml`), set the package line of each copy to its
board's configuration (step 5), and build and add each copy as in steps 6 and 7; `ha_host` and `ha_port` are the same
in every copy, and `engineering_sample` is set in the copy for the Waveshare P4 7B only.

**A second panel of the same type.** Each panel needs its own device name, API key and access-point password. In
every board configuration, the device name and the friendly name are in the `esphome:` block, the access-point name is
in the `wifi:` block under `ap:`, and the header lists the names of the secrets. Copy the starter file to a new file
(for example `glucocraft-starter-2.yaml`), add these lines at its end (each replaces the board configuration's own
value; shown for the default board), add the two new secrets to `secrets.yaml` with new values (as in step 3), and
build and add the new file as in steps 6 and 7:

```yaml
esphome:
  name: glucocraft-panel-2              # the device name: different for every panel
  friendly_name: GlucoCraft Panel 2
api:
  encryption:
    key: !secret glucocraft_panel_2_api_key
wifi:
  ap:
    ssid: "GlucoCraft-Panel-2"
    password: !secret glucocraft_panel_2_ap_password
```

**Connection.** A panel has two connections:

- **The ESPHome API connection** between Home Assistant and the panel (GlucoCraft's entities, and firmware updates
  over the network) is encrypted, with the panel's own API key from its `secrets.yaml` (`api: encryption:` in every
  configuration; updates over Wi-Fi use the same key).
- **The data download** (the panel's GLU2 files from GlucoCraft's wall-panel address,
  `http://<ha_host>:<ha_port>/api/webhook/<id>?file=...`) is not encrypted: it is plain HTTP over the local network.
  The address is read-only (GET only), answers only on the local network (Home Assistant's local-only check), and is
  protected by its secret id; the panel holds no Home Assistant token. Devices on the same home network could read the
  downloaded data or, by posing as Home Assistant, send false data to a panel. The panels' downloaded data are
  therefore only as private as the home network.

Home Assistant's local-only check needs Home Assistant to see the real address of the client (with Docker bridge
networking or a reverse proxy on the same host it may not). The panel's log names the file requested, never the id,
and GlucoCraft's log never names the id. Home Assistant's own log names it when a request from outside the local
network reaches the address (a warning), and while debug logging is on for Home Assistant's webhook component; a
shared Home Assistant log that contains such a line has exposed the address. The id also appears in the output of
the **Validate** step of ESPHome and of `esphome config`, in the panel's build folder (`.esphome/build/<name>/`) and in the
access log of a reverse proxy (it is part of the address); never share these.
Creating a new wall-panel address stops the earlier one (see
[Changing the wall-panel address](#changing-the-wall-panel-address)).

**Updating a panel.** Updating GlucoCraft does not change a panel's firmware. The panel shows its version on the
start-up screen, on the About page and in Home Assistant's device information; building the updated example configuration
again and installing it brings the panel to that version.

## Removing a panel

1. **Unplug the panel.**
2. **Delete its device in Home Assistant:** **Settings > Devices & services > ESPHome**, the panel's entry, the menu
   (three dots) > **Delete**. GlucoCraft then removes the panel's *GlucoCraft Panel Offline* notice, if one is shown,
   and forgets the panel: it leaves the *Maintenance* page and no longer counts in the Maintenance status. A
   *GlucoCraft Panel Added* notice stays until it is dismissed.
3. **Delete its ESPHome entry and its secrets:** the panel's entry in the ESPHome Device Builder (its menu > **Delete**)
   or its starter file, the panel's own secrets (its API key and access-point password) in `secrets.yaml`, and its
   build folder `.esphome/build/<device name>/` in the ESPHome configuration folder, which holds a copy of the
   panel's configuration with its secrets filled in.
4. **Erase the board if it leaves the home.** The board's flash memory holds the Wi-Fi name and password, the panel's
   API key and the wall-panel address id. Before the board is given away, sold or discarded, erase its whole flash
   memory over USB (for example with the erase-flash command of esptool).
5. **A new wall-panel address, if wanted.** A removed panel still holds the wall-panel address id. Where it could be
   used outside the home (for example a board that left without being erased), a new address can be created
   ([Changing the wall-panel address](#changing-the-wall-panel-address)); every remaining panel is then built and
   installed again with the new id.

## Changing the wall-panel address

No wall-panel address exists after GlucoCraft is set up. Ticking **Create a new wall-panel address** creates the
first one, before the first wall panel is built, because the build needs its id ([Building a panel from the
examples](#building-a-panel-from-the-examples), step 1). After that, a new address is created only if the address may
have been exposed, for example in a shared log, backup or screenshot; it is not a routine step. The address id is
built into each panel's firmware (in the example configurations, from `glucocraft_panel_webhook_id` in its
`secrets.yaml`). The moment a new address is
created, the earlier one stops working: every wall panel stops receiving data and shows that no current data arrives,
until each panel has been built and installed again with the new id.

1. Select **Settings > Devices & services > GlucoCraft > Configure > Display card and wall panels**, tick **Create a
   new wall-panel address** and select **Submit**.
2. Open the same screen again and copy the new id shown at the top.
3. Replace the value of `glucocraft_panel_webhook_id` in the panels' `secrets.yaml`.
4. Build and install every panel again (`esphome run`, or **Install** in the ESPHome Device Builder).
5. Check that each panel shows current data again.

## Troubleshooting

- **Panels show no current data after a new wall-panel address was created.** *Cause:* the earlier address stopped
  working when the new one was created, and the panels still request their data with the earlier id. *Check:* follow
  the steps in [Changing the wall-panel address](#changing-the-wall-panel-address) from step 2: copy the new id,
  replace `glucocraft_panel_webhook_id` in `secrets.yaml`, build and install every panel, and check that each panel
  shows current data again.
- **A panel is offline.** *Symptom:* the notification *GlucoCraft Panel Offline*, when the panel's Status entity
  has been off or unavailable for 10 minutes. *Cause:* the panel has no power or no network connection, or it cannot reach Home Assistant. *Check:* the
  panel's power and its network connection (Wi-Fi in the example configurations), and the panel in **Settings >
  Devices & services > ESPHome**.
- **A panel is connected but receives no data.** *Cause:* its id is not the one shown on the *Display card and wall
  panels* screen, or Home Assistant does not see its request as coming from the local network (see *Connection*
  above). *Check:* `glucocraft_panel_webhook_id` in `secrets.yaml` against the id on that screen, and `ha_host` and
  `ha_port` in the starter file (`ha_port` is Home Assistant's HTTP port, 8123 unless it was changed). To test the
  address and the port directly, open `http://<ha_host>:<ha_port>/` in a browser on a computer in the same network as
  the panel: Home Assistant's sign-in page (or its dashboard) means that the address and the port are right.
- **A panel shows an old version.** *Cause:* updating GlucoCraft does not change a panel's firmware. *Check:* the
  version on the About page; build and install the updated example configuration.
- **A board connects and disconnects over and over before its first flash.** *Symptom:* connected to the computer by
  USB, the board appears and disappears every few seconds (on Windows, the device sound repeats). *Cause:* its flash
  memory is empty (for example after `esptool erase-flash`), so its chip restarts every few seconds; on the boards
  whose USB socket is the chip's own (the 3.5-inch panel and the Waveshare 5-inch LCD), each restart also restarts the
  USB connection. Nothing is wrong. *Check:* flash the board (step 6); the repetition ends with the first flash. If
  the flashing tool cannot open the port between two restarts, hold the board's BOOT button while connecting it: the
  board then waits in download mode until it is flashed.
- **History not available.** *Symptom:* the graphs and weekly pages of a wall panel show *Loading...*, as the display card's do,
  until a download works; at its start the panel may show *History not available* (with *Showing live data...*)
  for a second, often hidden by the start-up screen.
  *Cause:* the history file could not be downloaded (no readings stored yet, Home Assistant restarting, or, for a
  panel, an address that is no longer valid). *Check:* reload the dashboard page; for a panel, the steps under *A panel is offline* and *A panel is
  connected but receives no data*. A panel shows live data meanwhile and tries the download again by itself, at
  first after 15 seconds and then less often, up to every 5 minutes.

## Waveshare P4 7B: start-up and updates

The Waveshare P4 7B differs from the other boards: its Wi-Fi runs on a second chip, an ESP32-C6, with its own
firmware. The board can fail to start after a firmware update over Wi-Fi, more often while the C6 still carries its
factory firmware. This section describes the fault, what to do, how to recognize it, the fix in the example
configuration, and how to update the C6 once.

- **Chip revision.** An ESP32-P4 below chip revision v3.0 is an early engineering sample: it needs the ESPHome setting
  `engineering_sample: true`, and without it restarts in a loop with "Illegal instruction". The configuration takes
  the setting from its substitution `engineering_sample`: `"false"`, the default, for a production chip (revision
  v3.0 or later), or `"true"` for an engineering sample, set in the starter file like the other values. The panel's
  start-up log names the revision ("chip revision: vX.Y"). The configuration was developed on a board with an
  engineering sample.
- **Wi-Fi through a second chip.** Wi-Fi runs on the board's own ESP32-C6, which is linked to the ESP32-P4 inside the
  board (`esp32_hosted` in ESPHome, with `use_psram: true`, without which the panel does not start). The ESP32-C6 runs
  its own firmware (esp_hosted), which should be the same version as the host library that ESPHome builds into the
  panel (esp_hosted 2.12.13, the host library of ESPHome 2026.10.0 and 2026.10.1; the panel's start-up log names both versions).

### The fault

- **Symptoms.** Right after a firmware update over Wi-Fi (and, with the ESP32-C6's factory firmware, sometimes after a
  plain restart) the screen stays white, dark or frozen, the panel does not come back on the network, and it restarts
  in a loop about every 15 seconds. The ESP32-C6 has stopped answering the ESP32-P4. If an update was being installed,
  the board goes back to its previous firmware (a rollback) once it starts again, because the new firmware never
  finished a start. The panel can recover by itself, but this can take many minutes (in the tests up to 45 minutes).
- **What to do.** Remove the panel's power for a few seconds and connect it again. The panel then starts normally;
  a restart from Home Assistant or the Restart button does not help, because the ESP32-C6 keeps its power. After the
  start, check the panel's version in Home Assistant (the device page, or the About page on the panel): if it still
  shows the previous version, the update was rolled back; install it again.
- **How to recognize it.** On the panel's log (USB serial, or `esphome logs` once it is back) the fault shows as
  `sdmmc_send_cmd returned 0x107` and `H_SDIO_DRV: failed to read registers`, followed by a restart, about every 15
  seconds. The diagnostic entity *Wi-Fi Co-processor Firmware* shows the ESP32-C6's firmware version: "Not reported"
  means firmware older than esp_hosted 1.0.0, which cannot report its version, and with which the fault also occurs
  after plain restarts. The entity reads the version from the ESP32-C6 itself over the board's internal link; it
  contacts no server.
- **The cause, as far as known.** When the ESP32-P4 restarts straight after the large transfer of an update, the
  ESP32-C6 can be left stuck, and the reset that the ESP32-P4 gives it at start-up does not always recover it. An old
  ESP32-C6 firmware makes this more likely, but it also occurs with an up-to-date one. On the development board, with
  esp_hosted 2.12.13 on the ESP32-C6 (the same version as the panel's host library), 2 of about 8 updates over Wi-Fi
  hung in one test series, while 10 plain restarts in a row were clean.
- **The fix in the example configuration.** Before every restart through ESPHome (an update, the
  Restart button, a settings change), the panel holds the ESP32-C6 in reset for 20 ms (`esphome: on_shutdown`, its
  enable line GPIO54 low), so that the ESP32-C6 starts afresh with the ESP32-P4, as after removing the power. With it,
  16 updates over Wi-Fi in a row started cleanly on the development board. Keep the ESP32-C6's firmware up to date as
  well (below). Should the fault still occur, remove the power as above; a report with the panel's log helps.
- **The first update from a firmware without this fix** still restarts through that earlier firmware: if that one
  update hangs, remove the power once; every later update uses the fix.

### Updating the ESP32-C6

The update is done once, with an update entity that is added only for it. The example configurations contain no
update entity, because it would make the panel contact the internet (see *Local only* below).

1. **Optional: back up the ESP32-C6 first** (see *Recovery and backup* below). The update replaces the factory
   firmware as the running one; a backup is the only copy of the factory firmware outside the board.
2. **Add the update entity.** Add these lines at the top level of `glucocraft-starter.yaml` (beside `packages:`):

   ```yaml
   http_request:

   update:
     - platform: esp32_hosted
       name: "Wi-Fi Co-processor Update"
       type: http
       source: https://esphome.github.io/esp-hosted-firmware/manifest/esp32c6.json
   ```

   The source is the official ESPHome list of ESP32-C6 firmware. The entity offers the highest version that is not
   newer than the panel's host library.
3. **Build and flash** the panel as usual (`esphome run glucocraft-starter.yaml`). With the factory firmware the new
   entity shows "unknown" as the installed version.
4. **Install.** Select **Install** on *Wi-Fi Co-processor Update* in Home Assistant. The firmware is downloaded and
   streamed to the ESP32-C6; the screen freezes for about 25 seconds and the panel restarts. With the factory firmware
   the install ends with the message "Failed to activate OTA" although it worked: that firmware restarts the
   ESP32-C6 by itself instead of answering.
5. **Confirm the version.** After the restart, *Wi-Fi Co-processor Firmware* shows the version of the host library
   that the panel's ESPHome release builds in, which its start-up log names (2.12.13 with ESPHome 2026.10.0 and 2026.10.1).
6. **Remove the entity again.** Delete the lines of step 2 from `glucocraft-starter.yaml`, build and flash once more.
   Home Assistant may keep the removed entity as unavailable; it can be deleted in its settings.

After a later ESPHome release that changes the host library, the panel's start-up log
shows a newer host library than the firmware; the same steps install the matching ESP32-C6 firmware.

### Risk, recovery and backup

- **Risk.** The firmware is checked before it is used (its checksum against the ESPHome list, then by the ESP32-C6
  itself before it switches to it), so a failed update is unlikely. If it fails, the panel has no Wi-Fi until the
  ESP32-C6 is flashed over its own serial port.
- **Recovery.** That port is not a USB socket: it is the small 4-pin header H4 on the board (1.0 mm pitch; pin 1 the
  ESP32-C6's boot pin, pin 2 GND, pin 3 the ESP32-C6's receive line, pin 4 its transmit line; the Waveshare schematic of
  the ESP32-P4-WIFI6-Touch-LCD-7B). It needs a 3.3 V USB-to-serial adapter (never 5 V): the adapter's transmit line to
  pin 3, its receive line to pin 4, GND to pin 2, and pin 1 connected to GND while the board is switched on, which
  starts the ESP32-C6's download mode. esptool (`--chip esp32c6`) can then write the firmware, for example the
  `network_adapter_esp32c6.bin` of the release in the ESPHome list. Release pin 1 before the next start. The board's own
  USB port is connected to the ESP32-P4, not to the ESP32-C6.
- **Backup (optional).** With the same connection, before the update, `esptool --chip esp32c6 -p <port> read-flash 0
  0x400000 c6_backup.bin` reads the whole 4 MB flash of the ESP32-C6. The update writes the ESP32-C6's other firmware
  slot, so the factory firmware also stays on the chip until a second update.

### Local only

GlucoCraft's panels are kept local by default: they connect only to Home Assistant and, where one is set, the
local time server. An
update entity makes the panel ask esphome.github.io for the list of firmware versions after each start and every
6 hours (no data from the panel is sent, but the panel then depends on, and is seen by, a server on the internet),
and it needs the `http_request` component of ESPHome. It is therefore added only for the update and removed
afterwards. A panel that keeps it works the same; it then also reaches the internet.

### Other settings of this board

The display buffer is the full screen (`buffer_size: 100%`), and the touch screen is mirrored in both directions
(`mirror_x`, `mirror_y`), because it is mounted turned by 180° to the display.

## Language of the panel texts

Every text a panel shows is in a text catalogue, `esphome/i18n/<language>.yaml`; the English catalogue, `en.yaml`, is
the source language and the fallback for every missing key. A panel's language is a build choice: the substitution
`language` of the board configuration (`en` by default) selects the catalogue. Only English is provided.

A new language is added in three steps, from the `esphome/` folder:

1. Copy `i18n/en.yaml` to `i18n/<code>.yaml` (the Home Assistant language code, for example `de` or `pt-BR`),
   translate the values and keep the keys and the placeholders such as `{minutes}`.
2. Run `python tools/make_panel_texts.py`. It checks every catalogue and writes `i18n/generated/<code>/`: the label
   texts for the configurations, the texts of the firmware (`gc_text.h`) and the glyph lists of the fonts, with the
   fixed line breaks made for each layout. `python tools/make_panel_texts.py --check` reports a generated file that is
   not up to date.
3. Run the fit check for the new language (below), then set `language` to the new code and build.

Numbers, dates and times keep GlucoCraft's regional settings (decimal separator, 12- or 24-hour clock, date order);
the catalogue holds the words only. The About page's disclaimer and the start-up screen's line are GlucoCraft's
disclaimer and its one line (`panel.about.title`, `panel.about.disclaimer` and `panel.startup.line`); a translation
translates them like every other text, and the English copies are word for word as in `const.py` (the start-up
line with a line break between its two sentences, which a translation keeps).

## The fit check

`tools/worst_case_fit.py` checks that every text of a language fits on every panel layout: the landscape layouts and the
square panel. It measures each text with the panels' font (Roboto) at its size against the room
it has on the screen, a text with values at its widest values (for example "Reading: 99 min ago" and "Reading: 9999 h
ago"), the words in the value's place without a current reading ("Sensor warming up", "Sensor expired", "No current
data", wrapped at the zone word's width in at most two lines), and checks the line counts of the start-up screen and
the About page. It prints one line per text and ends with "all fit" or the number
of texts that are too wide; the exit code is 1 when a text does not fit.

```
python tools/worst_case_fit.py          # English
python tools/worst_case_fit.py de       # another catalogue
```

Both tools need Python with PyYAML and Pillow, and the Roboto font that ESPHome stores in `.esphome/font/` after a
compile: compile any panel once in the folder before running them. The graph axis labels are not measured, because
they are placed only where they fit.

## Background and colors

The colors are set in GlucoCraft, on the settings screen **Display card and wall panels** (Settings > Devices &
services > GlucoCraft > Configure), and apply to every panel at once; the panel keeps the background and text colors
received last across a restart.

- **Display colors:** one color for each of the seven zones (very low, low, in range below the tight range, tight range,
  in range above the tight range, high, very high), used for the glucose value and LO / HI. The default palette shows
  lows in red and highs in blue (very low #C62828, low #EF5350, in range #FFC107, tight range #4CAF50, high #42A5F5,
  very high #1565C0); *Restore the default palette* returns to it.
- **Graph range colors:** seven colors for the range bands of the graphs, by default the colors of the CGM Report's
  glucose profile, drawn at 60% over the graph's near-black area (#0A0A0A); *Restore the default graph colors*
  returns to them.
- **Wall panel background color:** the background of every panel, black by default. The page text, the clock, the
  labels and the status words are black or white, whichever contrasts more with the background; one of the two always
  reaches at least 4.58:1. The graphs keep their black area, white line and white labels on every background, and the
  status bar keeps its own colors.
- **Outline text** (off by default): a thin 1-pixel edge around the graph labels, the glucose value, LO / HI and every
  other text in a zone color, black around light text and white around dark text, intended for light backgrounds and
  light graph bands.
- **Contrast note:** the screen measures the saved colors on the chosen background and states either "All colors meet
  the minimum contrast on this background." (or "on both backgrounds", "on the wall panel background", by the
  backgrounds measured) or the colors below the minimum (3:1 for the large glucose value, 4.5:1
  for text). It also measures the white graph labels on each band color; with the default band colors it lists the
  amber high band (4.1:1; 4.11:1 unrounded, the note rounds down). The note changes no color; the chosen colors are
  always used.

Color is never the only cue: the zone word under the value names the zone in every setting, the status bar
names its status, and the graphs show the glucose values on a numbered axis ([ACCESSIBILITY.md](ACCESSIBILITY.md),
rules 1, 2 and 8).

## Known limitations

- **The panels are visual only.** They have no audio and no screen-reader output: a screen reader cannot read them,
  and a person who is blind cannot use them. The same pages are available on the display card, which has
  screen-reader text.
- **12 px labels on the two small panel types.** 12 px text: the statistics labels of the square 480 × 480 panel and
  the 3.5-inch 480 × 320 panel; on the 3.5-inch panel also "TREND", "5 MIN DELTA", the source name, the weekly
  description line and the GMI value in both A1C units; and the statistics labels of the square display card. Every
  other text is 14 px or more ([ACCESSIBILITY.md](ACCESSIBILITY.md), rule 3).
- **White graph text on the default high band** reaches 4.11:1, below the 4.5:1 minimum for text; the same values are
  in the statistics beside each graph.
- **Out-of-date values.** A panel that has lost power, its network connection or its connection to Home Assistant can
  continue to show out-of-date values; the notice *GlucoCraft Panel Offline* appears only when the panel's Status
  entity has been off or unavailable for 10 minutes.
- **Time zone.** The panels take Home Assistant's time zone with every time synchronization (Home Assistant 2026.3 and
  later send it); before the first synchronization after the first start, and with an older Home Assistant, local
  times are in UTC.
- **Letters.** The panel fonts contain the Latin letters, including accented letters; letters of other scripts in the
  source name are left out.
- **Differences from the display card:** a panel has a start-up and download screen, its own swipe areas, and its own
  fonts and line drawing. On the weekly Min and Max page a panel shows the measured minimum and maximum, as the display
  cards do.
- **English only.** Only the English text catalogue is provided.

## References

Abbott Diabetes Care. (2022). *Get started with the FreeStyle Libre 3 system* (ADC-55381 v1.0) [Getting started
guide, US, mg/dL].
https://www.freestyleprovider.abbott/content/dam/adc/freestyleprovider/countries/us-en/documents/Brochure_FreeStyleLibre3_Getting_Started_Guide.pdf

Battelino, T., Danne, T., Bergenstal, R. M., Amiel, S. A., Beck, R., Biester, T., Bosi, E., Buckingham, B. A., Cefalu,
W. T., Close, K. L., Cobelli, C., Dassau, E., DeVries, J. H., Donaghue, K. C., Dovc, K., Doyle, F. J., III, Garg, S.,
Grunberger, G., Heller, S., . . . Phillip, M. (2019). Clinical targets for continuous glucose monitoring data
interpretation: Recommendations from the International Consensus on Time in Range. *Diabetes Care, 42*(8), 1593–1603.
https://doi.org/10.2337/dci19-0028

Bergenstal, R. M., Beck, R. W., Close, K. L., Grunberger, G., Sacks, D. B., Kowalski, A., Brown, A. S., Heinemann, L.,
Aleppo, G., Ryan, D. B., Riddlesworth, T. D., & Cefalu, W. T. (2018). Glucose management indicator (GMI): A new term
for estimating A1C from continuous glucose monitoring. *Diabetes Care, 41*(11), 2275–2280.
https://doi.org/10.2337/dc18-1581

Monnier, L., Colette, C., Wojtusciszyn, A., Dejager, S., Renard, E., Molinari, N., & Owens, D. R. (2017). Toward
defining the threshold between low and high glucose variability in diabetes. *Diabetes Care, 40*(7), 832–838.
https://doi.org/10.2337/dc16-1769
