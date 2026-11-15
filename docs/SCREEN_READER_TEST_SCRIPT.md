# GlucoCraft screen-reader guide for testers

This guide is for people who use a screen reader and would like to check GlucoCraft. It lists, page by page, what a
screen reader is designed to read on GlucoCraft's dashboard, display cards, CGM Report and notifications, so that
anything read differently can be reported (section 4). Use any screen reader you have; NVDA on Windows, TalkBack on
Android and VoiceOver on Apple devices are described below.

The wall panels are visual only and are not described here ([ACCESSIBILITY.md](ACCESSIBILITY.md), rule 9).

## 1. Before you start

### 1.1 Test data
- Use a Home Assistant that is not used for anything else, with GlucoCraft set up and current readings.
- Note the current glucose value, the zone, the trend and the time of the last reading as shown on screen, so that
  what is heard can be compared with what is shown.

### 1.2 NVDA on Windows
1. NVDA is free from nvaccess.org. Default keys: NVDA key = Insert.
2. Open Chrome, sign in to Home Assistant and open the GlucoCraft dashboard.
3. Useful keys:
   - Tab / Shift+Tab: next / previous control;
   - H / Shift+H: next / previous heading;
   - T: next table;
   - G: next graphic;
   - Down arrow: read the next line (browse mode);
   - NVDA+Down arrow: read everything from here;
   - Control: stop speech.

### 1.3 TalkBack on Android
1. Settings > Accessibility > TalkBack > On.
2. Open the Home Assistant Companion app and the GlucoCraft dashboard.
3. Useful gestures:
   - swipe right / left: next / previous item;
   - double-tap: activate;
   - two-finger swipe: scroll;
   - swipe down then right: open the TalkBack menu (reading controls, for example headings).
4. To turn TalkBack off again: hold both volume keys for three seconds (if the shortcut is enabled), or use the
   Settings path with TalkBack gestures.

### 1.4 VoiceOver on an iPhone, iPad or Mac
1. iPhone and iPad: Settings > Accessibility > VoiceOver. Mac: Command+F5.
2. Open the Home Assistant Companion app (or Safari) and the GlucoCraft dashboard.
3. Useful gestures and keys:
   - iPhone and iPad: swipe right / left for the next / previous item, double-tap to activate, and the rotor (turn two
     fingers) for headings, links and tables;
   - Mac: VO (Control+Option) with the arrow keys to move, VO+Space to activate, VO+U for the rotor.

## 2. What GlucoCraft is designed to read

Every place that shows the current glucose reads in this order (ACCESSIBILITY.md rule 4):
1. the data status, only when there is a problem ("No current data. Last reading 64 milligrams per deciliter, today
   at 5:39 PM, 2 hours ago.");
2. the value with the unit spelled out and the zone ("Glucose 112 milligrams per deciliter, tight range.");
3. the time of the reading;
4. the trend in words ("Changing slowly, minus 3 milligrams per deciliter in 5 minutes.");
5. one sentence about the page or view ("6-hour graph: lowest 58, highest 160 milligrams per deciliter.").

On the display card without a current reading (no current data, or a sensor warm-up) the screen shows words instead of
the value, and the card reads the same: the data status ("No current data." / "Sensor expired." / "Sensor warming
up."), then the last reading with when it was taken and its age ("Last reading 64 milligrams per deciliter, today at
5:39 PM, 2 hours ago."; what is not known is left out), then the page sentence (5), without 2 to 4.

At the sensor's limit (LO / HI on the screen) the value is read as "below the sensor's lower limit" or "above the
sensor's upper limit" with its zone, then since when ("Since 5:10 PM, for 25 minutes."), the last measured value
before it ("Last measured 45 milligrams per deciliter at 5:05 PM.", when it is known) and the time of the reading; no
trend is read. While GlucoCraft has a Repairs notice open ("Check Home Assistant" in the status bar), "Check Home
Assistant." is read before the page sentence (5), in every state.

Units are spoken in full ("milligrams per deciliter", "millimoles per liter", "percent"). Comparison signs are spoken
in words ("less than 4 percent" for "<4%", "at most 36 percent" for "≤36%"). No arrow or symbol is read as a symbol
name (for example "south east arrow").

## 3. Steps

Use the steps that apply to your screen reader. Each step names what GlucoCraft is designed to read or do.

| # | Where | Do | Designed to read or do |
|---|---|---|---|
| 1 | Dashboard: Start, Today, Consensus Ranges, Custom Ranges (only while the optional feature *Custom ranges and goals* is on), Maintenance and Settings Status | Move to the start of each page | The page title first (on Start, "GlucoCraft"), then the banners (where any are shown) and the page's content |
| 2 | Dashboard | Headings list (NVDA: H; TalkBack: headings; VoiceOver: rotor, headings) | Headings in reading order; on Start the headings begin at About This Page (the banners and the display card have no heading). On Consensus Ranges, under *Show All Statistics*, *Last Reading* and *Period Statistics* are headings |
| 3 | Display card, page 1 of 17 (6 hours) | Move onto the card | The rule-4 sentence: value, unit in words, zone, time, trend, then the graph summary |
| 4 | Display card | Next page button, through all 17 pages | Each button names where it leads ("Next page: 24 hours"). After each press, the new page is announced: its number and name, then its sentence ("Page 2 of 17: 24 Hours. 24-hour graph: ..."); the focus stays on the button |
| 5 | Display card, page 17 of 17 (*About GlucoCraft*) | Read the page | The drawing is named *About GlucoCraft*; then the disclaimer's short form, as text, the version line and the copyright line |
| 6 | Today / Consensus Ranges / Custom Ranges (only while the optional feature *Custom ranges and goals* is on) | Tables (NVDA: T) | Each table has a name and is read with its column and row headers; values with units in words; a target with a sign in words ("Consensus target less than 4 percent"); on Consensus Ranges ✓ / ✗ read as "meets" / "does not meet" |
| 7 | Charts on every page | Graphics (NVDA: G) | Every chart has a sentence that states its data, not only its title; on *Change Over Time*, *Lowest and Highest* names both the lowest and the highest values |
| 8 | CGM Report page | Move to the start of the page; then headings, the choice buttons and tables | The introduction first; real headings, beginning with *Report Options*; the period, form and color buttons are announced as toggle buttons, pressed or not pressed, and after a choice the focus stays on the button; *Creating the PDF…* and *PDF created* are announced after *Download PDF*, and an error at once; the links to the Documentation are announced as links; results are written and read as the words "met" / "not met"; each day of the daily profiles is named with its weekday and date in words |
| 9 | Documentation page | Headings list, then a section link and a citation | *Disclaimer* is read first, then *Essentials*; each section title is a heading with a button that says whether its section is expanded or collapsed; following a section link or a citation moves to its target, which is read next; every table is named by the heading above it |
| 10 | Footer of a page | Move to the footer | The color key (where the page has one), the line "For development and testing only. Not for medical use.", the attribution sentence and the license line are read, and their links are announced as links: the color key's link to the Documentation's *Colors* section, *Disclaimer*, *AGPL-3.0-only* and *About and License*. *AGPL-3.0-only* opens a new tab and says so ("opens in a new tab"). On the CGM Report and the Documentation the footer is the disclaimer line and the license line |
| 11 | Settings (Configure) | Open each screen, Tab through it; on *Glucose ranges* (only while the optional feature *Custom ranges and goals* is on) enter a value out of order and submit | Every field is announced with its label; the explanations are reachable; after the submit, the fields out of order are marked and the error states the order in words ("very low less than low, low at most tight range from, ...") |
| 12 | A notification with a glucose value: the daily summary sent to the phone (Configure > Notifications: the phone selected under *Also send notifications to*, and *Daily, weekly and monthly summaries* selected under *Notifications to send there*; then the action `glucocraft.send_daily_summary` in Developer tools > Actions). (*Send a test notification now* sends *GlucoCraft delivery test*, which has no glucose value.) | Let the phone read it | The title names the notification; the value with the unit in words; the trend in words, no arrow names; targets in words ("consensus target more than 70%") |
| 13 | Spoken summary sensor (enable it under Settings > Devices & services > Entities first) | Read its state in the entity's dialog | One of these forms: "Glucose ... milligrams per deciliter, in range, measured at ..., changing slowly." (value with unit in words, zone, time, trend); "No current data. Last reading at ..." when not current; after the stated sensor expiry "Sensor expired." before the reading, with the trend while readings still arrive; "Sensor warming up." alone during a sensor warm-up; "No glucose reading yet." before any reading |
| 14 | Keyboard only (NVDA, no mouse) | Tab through a whole dashboard page; then rest on a link of the pages card on Start for two minutes | Every control can be reached in reading order; focus is always visible; the focus stays on its control when the card is drawn again; nothing needs a swipe or hover |
| 15 | Both glucose units | Switch the display unit (Configure > Units and formats) and repeat steps 3, 6 and 12 | Millimoles per liter spoken in full; decimals read in the page language |
| 16 | A banner, when one appears (for example *No Current Data*) | Stay on the page while it appears | Its title is announced once; its minute-by-minute age is not announced again |
| 17 | Display card without a current reading (the words "No current data" or "Sensor expired" in the value's place, or during a sensor warm-up "Sensor warming up", throbbing) | Move onto the card, and stay on it for a few seconds | The same words first, as a sentence ("No current data." / "Sensor expired." / "Sensor warming up."), then the last reading with when it was taken and its age ("Last reading 64 milligrams per deciliter, today at 5:39 PM, 2 hours ago."; what is not known is left out), then the page's sentence; no zone or trend; the throb changes only the brightness: the text stays the same and nothing is announced while it lasts |
| 18 | Start page: the *Maintenance* button of the pages card; then the headline card at the top of Today, Consensus Ranges, Custom Ranges (only while the optional feature *Custom ranges and goals* is on) and Maintenance | Move onto each | The *Maintenance* button is a link named by its status: "Maintenance: no issues", or "Maintenance: 1 issue" / "Maintenance: 2 issues"; while there are issues, the Maintenance page's *Maintenance issues* card is a named region with the issues as a list. Each headline card is a link to its page, or a button that opens Home Assistant's details of its entity, named by its title with the units in words and, where it shows ✓ or ✗, "meets the target" or "does not meet the target"; its lines are read as its description |
| 19 | Display card with *Trend shown as* set to *Word* (Configure > Display card and wall panels) | Move onto the card | The same sentence as in step 3: the trend is read in words with either choice, and the zone word stays; nothing is read twice |
| 20 | Display card after the stated sensor expiry while readings still arrive (the value in its zone color, with its trend; "Sensor expiry passed" in the status bar) | Move onto the card | "Sensor expired." first, then the value with its unit in words and the zone, the time of the reading and the trend, as in step 3 |
| 21 | Consensus Ranges, *Show All Statistics*: a statistics cell that shows "---" (for example the GRI of a period shorter than 14 days) | Read the table row by row (NVDA: T, then Control+Alt+arrow keys) | The cell is read as "not available", never as dashes or "minus" |

## 4. Reporting a problem

If something is read differently from this guide, cannot be reached or is hard to see, please open a bug report at
<https://github.com/Verstreubulator/glucocraft> with the area *Accessibility*. It helps to give the step number, the screen reader and the
browser or app with their versions, the page, and what was heard or seen. No response time is promised.
