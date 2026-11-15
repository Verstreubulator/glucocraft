# GlucoCraft dashboard

GlucoCraft's disclaimer is at the top of the [README](../README.md).

GlucoCraft registers its own read-only dashboard, **GlucoCraft** in the sidebar (address `/glucocraft-home`), while
the setting *Dashboard in the sidebar* is on (Configure > Report and dashboard). It is built by a dashboard strategy
(`www/glucocraft-strategy.js`) from GlucoCraft's own cards and Home Assistant's built-in cards; no other card is
needed. It has a Start page and sub-pages that open with Home Assistant's back arrow, without a tab bar. The dashboard
cannot be edited; its cards can be added to the user's own dashboards. The entities, attributes and endpoints the
pages read are listed in [ENTITIES_AND_API.md](ENTITIES_AND_API.md).

## Contents

- [The parts of a page](#the-parts-of-a-page)
- [Pages](#pages)
- [Optional features](#optional-features)
- [Display cards](#display-cards)
- [Cards on other dashboards](#cards-on-other-dashboards)
- [The CGM Report](#the-cgm-report)
- [The Documentation page](#the-documentation-page)
- [Colors](#colors)
- [References](#references)

## The parts of a page

- **Banners** (Start, Today, Custom Ranges, Consensus Ranges and Maintenance), only while something needs
  attention: no current data, sensor expired, sensor expiry within a day,
  sensor warm-up, second source in use, reading times ahead of the clock (shown in place of the no-current-data
  banner).
- **Headline** (Today, Custom Ranges, Consensus Ranges and Maintenance): the page's main result in the status
  color.
- **Content**, then **About This Page** (every page but
  Documentation): what the page shows, how it is calculated and the cited context of its
  targets and limits, with the citations.
- **Footer:** on the pages that use colors, a color key that lists only the colors used on that page, with a link to
  the Documentation's Colors table; the line "For development and testing only. Not for medical use." with a link to
  the full disclaimer (Documentation, *Disclaimer*); the attribution sentence (targets and ranges come from cited
  sources or the user's settings; GlucoCraft sets none); and the license line. The CGM Report and the Documentation
  carry the attribution in their own text, so their footer is the disclaimer line and the license line.

The consensus ranges and the custom ranges are never shown on the same page.

Every time on the dashboard and the display cards is in Home Assistant's time zone (the clock, the graph axes, the
banners and the readouts), as on the wall panels, in the CGM Report, in the notifications and in the statistics.

## Pages

| Page | Address | Ranges | Content |
|---|---|---|---|
| Start | `/glucocraft-home/start` | — | The display card and the pages card |
| Today | `/glucocraft-home/today` | Consensus | Statistics and graphs for 6 hours to 14 days |
| Custom Ranges | `/glucocraft-home/custom` | Settings | Statistics and Change Over Time with the custom ranges and goals (optional) |
| Consensus Ranges | `/glucocraft-home/consensus` | Consensus | Statistics and Change Over Time with the consensus ranges and targets |
| CGM Report | `/glucocraft-home/report` | Consensus | The CGM Report, its PDF (Portable Document Format) and printing |
| Maintenance | `/glucocraft-home/maintenance` | — | Maintenance issues (only while there are any, or while the newest reading's time is ahead of the clock), sources, sensor, CSV (comma-separated values) export, trend arrow calculation, wall panels |
| Settings Status | `/glucocraft-home/settings` | — | Every setting in use and the requirements of each setting |
| Documentation | `/glucocraft-home/documentation` | — | The Documentation card |

The consensus ranges are those of Battelino et al. (2019): in range 70–180 mg/dL (3.9–10.0 mmol/L), below 70 and 54
mg/dL (3.9 and 3.0 mmol/L), above 180 and 250 mg/dL (10.0 and 13.9 mmol/L); the tight range is 70–140 mg/dL (3.9–7.8
mmol/L) (Battelino et al., 2023). They apply on Today, Consensus Ranges and the CGM Report whatever the settings.
Values are shown in the display unit.

**Start.** The display card (see [Display cards](#display-cards)) and, beside it, the pages card: a row for each report
page with its headline (Today; Custom Ranges, shown only while custom ranges or goals are set; Consensus Ranges; CGM
Report) and buttons for Maintenance, Settings Status and Documentation. The Maintenance button shows the Maintenance
status: green with a wrench and *No issues*, or red with an alert icon and *1 issue* / *N issues*; a screen reader
hears "Maintenance: no issues" / "Maintenance: N issues". Both cards have the same height, side by side on wide
screens and stacked on a phone. About This Page describes the sources, the ranges, the display colors, the
gap rule and LO / HI readings.

**Today.** The headline gives the last 24 hours. *Statistics — Consensus Ranges* is a table for 6 hours, 24 hours, 7
days and 14 days; *Graphs — Consensus Ranges* shows the graphs of the same four periods, each with its numbers, how it
is drawn and a key to the range bands. A value is green where it meets its cited consensus target and red where it
does not, an arithmetic comparison and not a clinical assessment, and only for periods of 14 days or more with at
least 70% sensor active; values without a consensus target, and shorter periods, have no color. The consensus targets
are time in range more than 70%, below 70 mg/dL (3.9 mmol/L) less than 4%, below 54 mg/dL (3.0 mmol/L) less than 1%,
above 180 mg/dL (10.0 mmol/L) less than 25%, above 250 mg/dL (13.9 mmol/L) less than 5%, and the coefficient of
variation (CV) 36% or less (Battelino et al., 2019, Tables 2 and 3). A period's statistics are shown only when the
sensor has covered at least 70% of it (sensor active); otherwise its values show --- and one note above the table
covers all of them, for example "Last 30 days: not enough data yet. GlucoCraft shows a period's statistics once the
sensor has covered at least 70% of it. So far it has covered 23%. With no gaps, about 14 more days are needed." (short
periods in hours; worked out when the page opens). The 70% comes from Battelino et al. (2019) for 14-day periods;
using it for every period length is a GlucoCraft rule. The Glycemia Risk Index (GRI) and the glucose management
indicator (GMI) are shown for periods of 14 days or more; the shorter periods show ---. This method is a GlucoCraft
rule. On the graphs, readings at the sensor's limit (LO / HI) have no value: the line breaks, and a run of small marks
along the bottom (LO) or top (HI) edge shows their time.

**Consensus Ranges.** The headline gives the 14-day time in range with ✓ / ✗ at the consensus target. *Core Measures —
Consensus Ranges* shows time in range, time in tight range, below 70, below 54 and above 180 mg/dL (3.9, 3.0 and 10.0 mmol/L): the last 14 days with ✓ /
✗ and the change since 22–26 weeks ago (or since 9–13 weeks ago when 22–26 weeks ago has not enough data, less than 70%
sensor active). *Change Over Time — Core Measures* shows each of the five over the last 26
weeks: the 14-day value at each week's end as the line and the 7-day values as faint dots, green or red at the cited
target and neutral gray without one. The button **Show All Statistics** opens the statistics for seven periods (6 hours
to 180 days) with their consensus targets and sources, the comparison table (the last 4 weeks compared with 9–13 and
22–26 weeks ago: the differences and the direction, higher, lower or no change) and seven further charts over time
(very high, mean, standard deviation, CV, GMI, GRI, and lowest and highest together). The GRI is shown for periods of
14 days or more: the 6-hour, 24-hour and 7-day statistics and the 7-day dots of its chart show none. The section is
closed until it is opened; the browser remembers the choice for each page.

**Custom Ranges.** The same structure, calculated with the ranges and goals in the settings; it exists only while the
optional feature *Custom ranges and goals* is on. The headline gives the 14-day time in range with ✓ / ✗ at the
time-in-range goal and "N of M goals met"; a goal is compared only for 14 days or more with at least 70% sensor
active, and below 70% the headline states that the goals are not compared. The box *Differences from the Consensus
Ranges* lists every range limit that differs from the consensus ranges, the readings it classifies differently, the
results it changes, and every custom goal that differs from its consensus target or has no published target; under
it, *Ranges and Goals in Use* lists the ranges and goals the page uses. Where a
result depends on a limit that differs, the result with the consensus ranges is shown with it in smaller text. The
core measures are time in tight range, time in range, below the low limit, below the very-low limit and above the
high limit, compared with the custom goals. *Show All Statistics* holds two statistics tables (6 hours to 14 days, 14
days to 6 months), the comparison table, the six further charts, the graphs of 14 days to 6 months and a chart of
each day's mean, minimum and maximum. The GRI is shown on *Consensus Ranges* only; its weights were fitted to the
consensus ranges (Klonoff et al., 2023). Mean, standard deviation, CV and GMI do not depend on the ranges. The page states: "The defaults are the international consensus ranges and targets
(Battelino et al., 2019). Ranges and goals that differ from them are custom settings."

**Maintenance.** *Maintenance issues*, at the top, shown while there are issues or while the
newest reading's time is ahead of the clock: each issue with where it is
resolved. Counted, one issue each: a wall panel offline for 10 minutes or more (its power and Wi-Fi; *GlucoCraft
Panels* below), an open GlucoCraft issue in Home Assistant's Repairs (its title, with a link to Repairs), and a failed
CSV export (its reason; *CSV Export* below), until an export succeeds or GlucoCraft restarts. Not counted: no current
data and the sensor expiry, which the banners and the status headline show. While the newest reading's time is more
than 5 minutes ahead of Home Assistant's clock, the first card explains it in plain words: what happened, that the
reading's age is therefore not shown ("Reading: ---" on the displays), and what to check (Home Assistant's clock and
time zone; neither the clock of the glucose source nor a source that sends its times without a time zone causes it,
because a reading time later than Home Assistant's clock is stored as the time the reading arrives); the banner
*Reading Times Ahead of the Clock* on Start, Today, Custom Ranges, Consensus Ranges and
Maintenance points to it. While a GlucoCraft notice is
open in Repairs, the display cards and the wall panels show "Check Home Assistant" in their status bar. The button on
Start and this card read one source, the attribute `maintenance_issues` of `sensor.glucocraft_configuration`
([ENTITIES_AND_API.md](ENTITIES_AND_API.md)). *The status headline*: GlucoCraft's status and when the statistics were
last calculated. *Sensor and Sources*: the sensor expiry, each source's last measurement and which source is in use,
the last reading, the no-current-data interval, the readings stored, the reading interval,
the gap threshold and the history kept (200 days). *CSV Export*: the button *Save CSV export*, which saves every stored
reading to the folder `glucocraft_exports` of the Home Assistant configuration (the last 12 exports are kept).
*Trend Arrow Calculation*: the last reading, the 5-minute delta and the trend arrow. *GlucoCraft Panels*: one row per wall panel built from the example configurations, found automatically, with its
name, type, status, uptime and firmware version, offline panels first ([PANELS.md](PANELS.md)).

**Settings Status.** *Change Settings* opens GlucoCraft in Settings > Devices & services, where *Configure* opens the
settings (administrators only; others see where it is). *Current Settings* lists every setting in use with the consensus
defaults; *Changing Settings* describes the screens of the configuration menu; *Requirements of Each Setting* states
what each setting needs. The page is read-only.

## Optional features

Two switches on the configuration screen *Optional features*, each off in a new installation (the setup does not ask
for them). Turning a feature off removes its entities from Home Assistant and keeps their recorded history; turning it
on again restores the same entities, and their history continues. After *Custom ranges and goals* is turned on or off,
the page *Custom Ranges* appears or disappears on the dashboard once the browser page is reloaded; a newly
connected wall panel appears in *GlucoCraft Panels* on *Maintenance* after a reload as well.

- **Custom ranges and goals.** On, the ranges and goals in the settings are used for the zone of the glucose value on
  the display card and the wall panels (its color and its zone word), the graph bands and, where selected, the
  weekly pages. It adds the page *Custom Ranges*, the CGM Report's appendix *Custom ranges and goals*, the
  configuration screens *Glucose ranges* and *Custom goals*, and 49 entities (per period the custom ranges sensor and
  six custom time-in-band sensors). Off, the consensus ranges and targets
  are used everywhere (Battelino et al., 2019; tight range, Battelino et al., 2023), and the stored ranges and goals are
  kept.
- **Separate statistics entities.** On, one entity per statistic and period for the mean, standard deviation, CV,
  lowest reading, highest reading, readings and sensor active, and for the GMI and the GRI of the periods of 14 days
  and longer (57 entities), available to other dashboards, automations and scripts like any Home Assistant entity.
  GlucoCraft's dashboard, display cards and wall panels do not use them: the same values are attributes of each
  period's consensus ranges sensor.

## Display cards

The display card shows the screens of a wall panel on the dashboard: the same pages, layout, data and rules as the
panels ([PANELS.md](PANELS.md); [DISPLAY_CONTRACT.md](DISPLAY_CONTRACT.md)). It reads GlucoCraft's entities and the same
data files the panels read, through Home Assistant's login, so it needs no recorder history. The setting *Display card
layout* (Configure > Display card and wall panels) chooses it, independently of the panels:

| Setting | Card | Size | Mirrors |
|---|---|---|---|
| Square (the default) | `glucocraft-display-card` | 480 × 536 | the square 480 × 480 panel |
| Rectangular | `glucocraft-display-card-wide` | 800 × 536 | the 800 × 480 panel; close to the 1024 × 600 and 480 × 320 panels |

Both cards show the 17 pages of the panels: seven graph pages (6 hours to 6 months), nine weekly pages and *About
GlucoCraft* (page 17 of 17: GlucoCraft's icon, the disclaimer, the version and the copyright line). The display cards have no start-up
screen. The page
buttons under the drawing change the page, a swipe on a phone does the same, the glucose block opens the weekly pages
and the weekly information leads back to the 6-hour page; after 180 seconds without use the card returns to the 6-hour
page. Every control is a real button that can be reached with the keyboard. The drawing carries a text description for
screen readers, read in the order of [ACCESSIBILITY.md](ACCESSIBILITY.md), rule 4. Below its design width a card draws a
compact, stacked layout in which no text is smaller than 14 px.

The background of the display cards is the setting *Display card background* (the Home Assistant theme
or a color, black by default); the text is black or white, whichever contrasts more, and the graphs stay on black. The
zone colors, the graph range colors and *Outline text* are those of the wall panels ([PANELS.md](PANELS.md), Background
and colors). Without a current reading the cards show words in the value's place, never a number, as the panels do:
"Sensor warming up" during a sensor warm-up; while there is no current data (GlucoCraft's flag, or the card's own count
of the reading's age), "Sensor expired" after the stated expiry, else "No current data", with the last reading and
when it was taken under them ("Last reading: 64 mg/dL", "Today at 17:39 (2 h 0 min ago)"). While the current reading
lies outside the sensor's range, the value's place shows "Very low" or "Very high", "Below sensor range (LO)" or
"Above sensor range (HI)", since when, and the last measured reading before that stretch. After the stated expiry,
while readings still arrive, the value keeps its zone color and the red status bar shows "Sensor expiry passed". Each
card keeps one size on every page and in every state, as the panels do. The status bar keeps the age of the reading
("Reading: 2 h ago"; "Reading: ---" while the reading's time is ahead of the clock); all three words throb for the
first 30 minutes of each episode and then hold still, and "Sensor expiry passed" throbs until a new sensor is recognized,
unless *Fade the words when there is no current reading* is off or the system asks for reduced motion
([DISPLAY_CONTRACT.md](DISPLAY_CONTRACT.md)). The card returns to its first page after 3 minutes
without use (not while one of its buttons has the focus or the pointer is over it); the card
option *Return to the first page after 3 minutes* (on by default, in the card editor) turns this off. For lack of
room the display cards, as the panels, use the short forms Min, Max and Avg (the weekly page "Weekly: Min and Max");
the dashboard pages and the CGM Report keep Lowest and Highest.

## Cards on other dashboards

GlucoCraft's cards can be added to the user's own dashboards from the card picker (search for "GlucoCraft"), each
with its own description, or in YAML with the type and options below. Every option is optional; a value a card does not accept (for example an unknown `metric`, or `days` outside 1 to 200) is named on the card with the values it accepts. `basis` selects the
range set: the consensus ranges and targets (the default), or the custom ranges and goals in the settings (`yours` for
the statistics tables and graphs, `personal` for Change Over Time), which exist only while the optional feature *Custom
ranges and goals* is on.

| Card | What it shows | Options |
|---|---|---|
| `custom:glucocraft-banners` | The banners, each only while it applies: no current data, sensor expired, sensor expiry within a day, sensor warm-up, second source in use, reading times ahead of the clock (shown in place of the no-current-data banner) | none |
| `custom:glucocraft-readout` | The headline of a page | `view`: `today`, `consensus`, `personal` (Custom Ranges), `report` or `status` (Maintenance); `hero`: `true` for the headline with the status edge and ✓ / ✗; `path`: the address the card opens (without it, the card opens Home Assistant's details of its entity); without a `view`, `title` and `text`: a title and lines of its own (one line per line of the text) |
| `custom:glucocraft-stats-table` | The statistics table for a set of periods | `periods`: a list of `6h`, `24h`, `7d`, `14d`, `30d`, `90d`, `180d` (default the first four); `basis`: `consensus` or `yours`; `warn_periods`: the periods whose not-enough-data note the table shows (default `periods`) |
| `custom:glucocraft-history` | A glucose graph for one period, with its numbers and a key to the range bands | `hours`: the period in hours, for example `6`, `24`, `168`, `336`, `720`, `2160` or `4320` (default `24`); `basis`: `consensus` or `yours`; `title`; `height` in pixels (default `240`) |
| `custom:glucocraft-daily-range` | Each calendar day's mean, lowest and highest reading | `days`: 1 to 200 (default `14`); `basis`: `consensus` or `yours`; `title`; `height` in pixels (default `220`) |
| `custom:glucocraft-change` | Change Over Time: the last 4 weeks compared with 9–13 and 22–26 weeks ago | `basis`: `consensus` or `personal` |
| `custom:glucocraft-change-chart` | Change Over Time: one measure over 26 weeks | `metric`: `tir` (the default), `titr`, `below`, `very_low`, `above`, `very_high`, `mean`, `sd`, `cv`, `gmi`, `gri` or `min` (lowest and highest); `basis`: `consensus` or `personal` |
| `custom:glucocraft-core` | The core measures: the last 14 days with ✓ / ✗ and the change since 22–26 weeks ago | `basis`: `consensus` or `personal` |
| `custom:glucocraft-details` | A section, closed by default, that holds further cards | `title`; `subtitle`; `button` and `button_hide`: the texts of its button (default *Show All Statistics* and *Hide All Statistics*); `cards`: the cards inside (each the full width; a Change Over Time chart, or a card with `half: true`, half of it) |
| `custom:glucocraft-statistics` | The last reading and the statistics for seven periods with their consensus targets | none |
| `custom:glucocraft-pages` | A row for each report page with its headline, and the Maintenance, Settings Status and Documentation buttons | `base`: the address of the dashboard the rows open (default `/glucocraft-home`); `report`: `true` adds the CGM Report's row; `background`: `theme` (the default), `black` or `white` |
| `custom:glucocraft-start-pair` | The display card and the pages card side by side (stacked on a phone) | `card`: `square` or `rectangular`, the display card shown until the setting *Display card layout* is read; `display` and `pages`: the options of the two cards; `wide`: `true` for the rectangular display card (the older form of `card: rectangular`) |
| `custom:glucocraft-settings-button` | The Change Settings button, which opens GlucoCraft's settings | none |
| `custom:glucocraft-export` | The Save CSV export button and the result of the last export | none |
| `custom:glucocraft-report` | The CGM Report page: the 14-day summary, the longer-term overview, the PDF, printing and laboratory A1C (glycated hemoglobin) results | `days`: the period shown first, `14` (the default), `30`, `90` or `180`; `base`: the address of the GlucoCraft dashboard whose Documentation its links open (default `/glucocraft-home`) |

GlucoCraft's own dashboard also uses `custom:glucocraft-maintenance-issues` (the issues of the Maintenance status),
`custom:glucocraft-custom-diff` (the differences from the consensus ranges), `custom:glucocraft-footer` (the page
footer, whose texts the dashboard provides), `custom:glucocraft-markdown` (GlucoCraft's text card for the texts of
the pages: About This Page, the Maintenance and Settings Status texts) and `custom:glucocraft-documentation` (the
Documentation page); they are not offered in the card picker. The display cards are described
under [Display cards](#display-cards).

## The CGM Report

The CGM Report shows the standardized metrics and glucose profile recommended for the Ambulatory Glucose Profile
(Battelino et al., 2019; Bergenstal et al., 2013). It covers 14 days, 30 days, 90 days or 6 months of full days ending
at the last midnight; the first section always shows the most recent 14 days, and a longer period adds an overview in
consecutive 14-day periods. It contains hypoglycemia events, the comparison with the previous period (for 14, 30 and
90 days), and laboratory A1C (glycated hemoglobin) results compared with the GMI (Bergenstal et al., 2018). It always
uses the consensus ranges. A period with less than 70% sensor active gives its sensor active and no values, with one
note on how much more data it needs; the daily glucose profiles mark readings at the sensor's limit along their bottom
(LO) or top (HI) edge. A period other than 14, 30, 90 or 180 days (for example from a card option or an automation)
is refused with a message that names the periods offered. *Download PDF* creates the summary or the full report (with daily pages), in color or
grayscale; each PDF is also kept in the folder `glucocraft_reports` (the last 12). *Print Report* sends it to a network
printer that accepts the Internet Printing Protocol (IPP), where a printer address is set. Two appendices are added only when chosen: *Custom ranges and goals* and
*Measures defined for research* (Battelino et al., 2023). The report keeps the time-in-ranges colors of the published
report examples.

## The Documentation page

The Documentation page explains every page, metric, range and setting in collapsible sections: Disclaimer,
Essentials, How GlucoCraft Is Designed, Readings Outside the Sensor's Range (LO / HI), Gaps, Reading Interval and No Current Data, Data
Kept and CSV Export, Pages, Ranges, Terminology and Colors, Metrics and Sources, Published Findings, What GlucoCraft
Leaves Out, and Why, Notifications (Not Alarms), Automations, Installing, Updating and Removing GlucoCraft, Setup,
Supported Sources, Entity Names, Glossary, Privacy
and Wall Panels, Accessibility, Troubleshooting, Known Limitations, References, and About and License. Its live values come from GlucoCraft's configuration entity, and every citation links to the reference
list.

## Colors

The status colors of the pages: green, data current, a target or goal met, a CSV export saved or no maintenance
issues, and the side of a chart within its target or goal (not a result); red, no current data, sensor expired, a
target or goal not met, a CSV export not saved or maintenance issues, and the side of a chart outside its target or
goal (not a result); amber, sensor expiry within 24 hours, the second source in use, a period without enough data
yet (less than 70% sensor active) or a CSV export in progress; purple, waiting (sensor warm-up, no data yet); neutral
gray, a chart without a target or goal (and #9E9E9E, the display card's status bar and the Maintenance headline while
the status has no color); light gray, the lowest to the highest reading in the graphs. Green and red are an arithmetic comparison, not an
assessment. The glucose values, and the LO / HI mark of a reading at the sensor's limit, use the display colors of the
settings (by default lows in red and highs in blue), as do the marks along a graph's bottom or top edge for readings
at the sensor's limit, and the range bands of the graphs the graph range colors (by
default the time-in-ranges colors of the CGM Report). The color key of each page lists only the colors that page
draws. ✓ / ✗, the zone word under the glucose
value (in every setting), the status words, the icon and the line of the Maintenance button and the numbered graph
axes carry the same information as the colors ([ACCESSIBILITY.md](ACCESSIBILITY.md), rule 1).

## References

Battelino, T., Alexander, C. M., Amiel, S. A., Arreaza-Rubin, G., Beck, R. W., Bergenstal, R. M., Buckingham, B. A.,
Carroll, J., Ceriello, A., Chow, E., Choudhary, P., Close, K., Danne, T., Dutta, S., Gabbay, R., Garg, S., Heverly, J.,
Hirsch, I. B., Kader, T., . . . Phillip, M. (2023). Continuous glucose monitoring and metrics for clinical trials: An
international consensus statement. *The Lancet Diabetes & Endocrinology, 11*(1), 42–57.
https://doi.org/10.1016/S2213-8587(22)00319-9

Battelino, T., Danne, T., Bergenstal, R. M., Amiel, S. A., Beck, R., Biester, T., Bosi, E., Buckingham, B. A., Cefalu,
W. T., Close, K. L., Cobelli, C., Dassau, E., DeVries, J. H., Donaghue, K. C., Dovc, K., Doyle, F. J., III, Garg, S.,
Grunberger, G., Heller, S., . . . Phillip, M. (2019). Clinical targets for continuous glucose monitoring data
interpretation: Recommendations from the International Consensus on Time in Range. *Diabetes Care, 42*(8), 1593–1603.
https://doi.org/10.2337/dci19-0028

Bergenstal, R. M., Ahmann, A. J., Bailey, T., Beck, R. W., Bissen, J., Buckingham, B., Deeb, L., Dolin, R. H., Garg,
S. K., Goland, R., Hirsch, I. B., Klonoff, D. C., Kruger, D. F., Matfin, G., Mazze, R. S., Olson, B. A., Parkin, C.,
Peters, A., Powers, M. A., . . . Wesley, D. M. (2013). Recommendations for standardizing glucose reporting and analysis
to optimize clinical decision making in diabetes: The Ambulatory Glucose Profile (AGP). *Diabetes Technology &
Therapeutics, 15*(3), 198–211. https://doi.org/10.1089/dia.2013.0051

Bergenstal, R. M., Beck, R. W., Close, K. L., Grunberger, G., Sacks, D. B., Kowalski, A., Brown, A. S., Heinemann, L.,
Aleppo, G., Ryan, D. B., Riddlesworth, T. D., & Cefalu, W. T. (2018). Glucose management indicator (GMI): A new term
for estimating A1C from continuous glucose monitoring. *Diabetes Care, 41*(11), 2275–2280.
https://doi.org/10.2337/dc18-1581

Klonoff, D. C., Wang, J., Rodbard, D., Kohn, M. A., Li, C., Liepmann, D., Kerr, D., Ahn, D., Peters, A. L., Umpierrez,
G. E., Seley, J. J., Xu, N. Y., Nguyen, K. T., Simonson, G., Agus, M. S. D., Al-Sofiani, M. E., Armaiz-Pena, G.,
Bailey, T. S., Basu, A., . . . Kovatchev, B. (2023). A Glycemia Risk Index (GRI) of hypoglycemia and hyperglycemia for
continuous glucose monitoring validated by clinician ratings. *Journal of Diabetes Science and Technology, 17*(5),
1226–1242. https://doi.org/10.1177/19322968221085273
