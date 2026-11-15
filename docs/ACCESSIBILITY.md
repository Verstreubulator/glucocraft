# GlucoCraft accessibility rules

These rules apply to every surface: the dashboard and the Documentation, the display cards, the CGM Report page and its
PDF (Portable Document Format), the notifications, the Spoken summary sensor and the wall panels. They sit beside
`LANGUAGE_STYLE.md` (wording). Every change is checked against them, and new work is not released until it follows them.
They describe the accessibility work that is done; they are not a claim that GlucoCraft is accessible or meets a
standard.
Abbreviations: A1C, glycated hemoglobin; CV, coefficient of variation; GMI, glucose management indicator.

## Conformance

GlucoCraft is designed to follow WCAG 2.2 level AA where it applies. No conformance claim is made. GlucoCraft does not
claim to be accessible or to meet WCAG or any other standard. The accessibility work is measured against the part of the Web Content Accessibility Guidelines (WCAG) 2.2, level AA, that
applies to GlucoCraft (World Wide Web Consortium [W3C], 2024), and its controls are built on the patterns of the
WAI-ARIA Authoring Practices Guide (World Wide Web Consortium, n.d.). The rules below say what is done. Where a surface
does not follow a rule, the limitation is stated openly in the Documentation (rule 9) and listed under Known
limitations.

## 1. Color is never the only cue

WCAG 2.2 success criterion 1.4.1, Use of Color (https://www.w3.org/TR/WCAG22/#use-of-color).

Every meaning carried by color is also carried by text, a symbol or a number:
- the zone word under the glucose value ("Very low", "Low", "In range", "Tight range", "High", "Very high"), on every
  display card and wall panel, in every setting (the setting *Trend shown as* chooses only whether the TREND column
  shows the arrow or the trend word); at the sensor's limit the value's place shows words instead ("Very low" with
  "Below sensor range (LO)", "Very high" with "Above sensor range (HI)");
- the status words: in the value's place without a current reading "No current data", "Sensor expired" or "Sensor
  warming up" (never a number then); on the status bar "Warm-up", "Sensor expiry passed", "Check Home Assistant" and
  "Reading: ---";
- the place of the marks where readings lay beyond the sensor's range: along a graph's bottom edge for below the range,
  along its top edge for above it, besides their color;
- ✓ / ✗ for met and not met on the dashboard, read as "meets" / "does not meet"; the CGM Report page and its PDF write
  the words "met" / "not met";
- the Maintenance status on the Start page's *Maintenance* button: besides green or red, the wrench or an alert icon
  and the line *No issues* / *1 issue* / *N issues*, read as "Maintenance: no issues" / "Maintenance: N issues";
- the number scale of every graph, which gives the glucose value of each range band.

Color vision is checked by simulation of protanopia, deuteranopia and tritanopia (Machado et al., 2009) for the default
palettes (rule 8). Under deuteranopia the default low color (#EF5350) and tight-range color (#4CAF50) are nearly
identical; the zone word carries the difference. Colors that users choose themselves cannot be simulated in advance;
the cues above remain with every palette, so a custom palette never removes information.

## 2. Contrast

WCAG 2.2 success criteria 1.4.3, Contrast (Minimum) (https://www.w3.org/TR/WCAG22/#contrast-minimum), and 1.4.11,
Non-text Contrast (https://www.w3.org/TR/WCAG22/#non-text-contrast). Contrast ratios are calculated with the WCAG 2.2
definitions of relative luminance and contrast ratio (W3C, 2024).

- Small text: at least 4.5:1 against its background.
- Large text (the glucose value, headings; at least 24 px, or 18.66 px in bold) and graphical elements: at least 3:1.
- Light and dark themes are checked separately.
- Where a range color is too faint for small text on the dashboard, a darker or lighter shade of the same hue is used
  for that text only. The Maintenance button's status icon and edge use the status color mixed with the card's text
  color (80 parts to 20), so that they reach 3:1 on the button in the default themes and the card's palettes.
- Contrast comes from the colors and the backgrounds the user chooses (rule 8). No plates are drawn behind text, and
  by default no outlines either. The optional setting *Outline text* (rule 8) adds a thin 1-pixel edge around the
  graph labels, the glucose value, LO / HI and every other text drawn in a zone color, on the display card and every
  panel: black around a light text and white around a dark one, whichever contrasts more with the text. The
  contrast values below are measured without the edge.
- On a chosen background color, the page text, the clock and the navigation of the display card and the panels
  are drawn in black or white, whichever contrasts more with the background. One of the two always reaches at least
  4.58:1, so this text meets 4.5:1 on every background. The labels use a secondary shade mixed from that text color
  toward the background and kept at 4.5:1 or more. The words in the value's place without a current reading ("No
  current data", "Sensor expired", "Sensor warming up") are drawn in the text color as well, so no status word is
  drawn in red. With the Home Assistant theme as the card background, the theme's own text colors are used.
- The settings show the measured contrast of the saved colors in the description at the top of the screen *Display
  card and wall panels*: "All colors meet the minimum contrast on this background." (or "on both backgrounds" or "on
  the wall panel background", by the backgrounds measured), or a list of the colors below the minimum (for example
  "Very low on black: 2.6:1, below the 3:1 minimum for large text"). With the Home Assistant theme as the card
  background it adds "The display card follows the Home Assistant theme, so its contrast depends on the theme.", and
  where zones or graph bands share a color it names them ("These zones have the same color: ..."). The note also
  measures the white graph labels on each graph range color as
  drawn, at 60% over the graph's black area, against 4.5:1; with the default graph range colors it lists "White graph
  text on the high band: 4.1:1, below the 4.5:1 minimum for text" (the one decimal is rounded down). The note does not
  change any color; the chosen colors are always used.
- The graphs keep a black area (#0A0A0A) on every background, with a white glucose line and white labels, so that the
  range bands and the line remain visible on a light page.
- GlucoCraft's own links and filled buttons do not use the theme's primary color as it is (Home Assistant's default
  #009AC7 reaches 3.26:1 on white). A link is the primary color mixed half and half with the theme's text color; a
  filled button is the primary color mixed with 35% black, with white text. Home Assistant's default themes: the light
  theme's text color #141414 on white cards (#FFFFFF), the dark theme's text color #E1E1E1 on cards #1C1C1C. With
  Home Assistant's default primary colors (#009AC7, and the older #03A9F4) a link reaches 8.08:1 and 7.08:1 on the light
  theme's cards and 8.09:1 and 8.84:1 on the dark theme's cards; the white text of a filled button reaches 6.68:1 and
  5.64:1. Every control that can receive focus shows the same focus ring: 3 px in the text color (18.42:1 on the light
  theme's cards and 13.03:1 on the dark theme's; rule 5).

Measured values of the default palettes:

| Pair | Contrast | Minimum |
|---|---|---|
| Glucose value, very low #C62828 on the default black background | 3.74:1 | 3:1 (large text) |
| Glucose value, very high #1565C0 on black | 3.65:1 | 3:1 (large text) |
| Glucose value, the other display colors (#EF5350, #FFC107, #4CAF50, #42A5F5) on black | 6.02:1 to 12.88:1 | 3:1 (large text) |
| White graph text on the very-low band (#9B1C1F at 60% over #0A0A0A) | 12.91:1 | 4.5:1 |
| White graph text on the low band (#E5352B) | 8.72:1 | 4.5:1 |
| White graph text on the in-range bands outside the tight range (#1F8F45) | 8.30:1 | 4.5:1 |
| White graph text on the tight-range band (#52B964) | 5.71:1 | 4.5:1 |
| White graph text on the high band (#FCC21B, yellow) | 4.11:1 | 4.5:1: below, a known limitation |
| White graph text on the very-high band (#F08A4B) | 5.74:1 | 4.5:1 |

The graph range colors are the colors of the CGM Report's glucose profile; every band is drawn at 60% over the graph's
black area.

## 3. Text size

WCAG 2.2 success criteria 1.4.4, Resize Text (https://www.w3.org/TR/WCAG22/#resize-text), and 1.4.10, Reflow
(https://www.w3.org/TR/WCAG22/#reflow).

- Text is at least 14 px on every surface shown on a screen, and at least 8 pt in the CGM Report PDF. One exception:
  these texts remain at 12 px: the statistics labels of the
  square 480 × 480 panel and the 3.5-inch 480 × 320 panel; on the 3.5-inch panel also "TREND", "5 MIN DELTA", the
  source name, the weekly description line and the GMI value in both A1C units; and the statistics labels of the
  square display card (as on the 480 × 480 panel). Below its design width a display card draws a compact layout in
  which no text is below 14 px. A display card keeps one size on every page and in every state, as the panels do. A larger panel shows larger text. New elements, including the zone word, follow the 14 px rule.
- Full words; no abbreviations unless they are necessary.
- The physical size depends on the screen. The Documentation states, for each panel type, how large the smallest text
  is, and that a larger panel shows larger text.
- The dashboard follows the browser's zoom. The layouts are designed to reflow down to a width of 320 CSS pixels
  (WCAG 2.2 success criterion 1.4.10): below its design width a display card draws its compact layout, the pages card
  on Start lets its lines wrap, and its rows grow with their text instead of cutting it off (1.4.12, Text Spacing).
  The CGM Report page was measured at a 320 CSS-pixel width: its blocks stack in one column, and the page does not
  scroll sideways. Its charts are drawn at the width of the page's content box, one unit per pixel (never scaled down
  by a view box), so that their 14 px labels stay at 14 px.

## 4. Screen readers: what is read first

WCAG 2.2 success criteria 1.1.1, Non-text Content (https://www.w3.org/TR/WCAG22/#non-text-content), 1.3.1, Info and
Relationships (https://www.w3.org/TR/WCAG22/#info-and-relationships), and 3.1.1, Language of Page
(https://www.w3.org/TR/WCAG22/#language-of-page).

A person who is blind hears first whatever would change how they read the number.

For the display card and every place that shows the current glucose, the text description is read in this order:
1. **Data status, only when there is a problem:** "No current data. Last reading 64 milligrams per deciliter, today
   at 5:39 PM, 2 hours ago." / "Sensor expired." / "Sensor warming up."
2. **Value, unit and zone:** "Glucose 62 milligrams per deciliter, low." Units are spelled out, because screen
   readers mispronounce "mg/dL" and "mmol/L".
3. **Time of the reading:** "Measured 5 minutes ago."
4. **Trend:** "Changing slowly, minus 3 milligrams per deciliter in 5 minutes."
5. **What the page or view shows, in one sentence:** "6-hour graph: lowest 58, highest 160 milligrams per deciliter."
   / "Time in range of each 14-day period, last 26 weeks: from 81.0 to 93.0 percent." (the measures that are not
   glucose values are spoken with one decimal).

Without a current reading (no current data, or a sensor warm-up) the display card shows words in the value's place,
never a number, with the last reading and when it was taken under them, and its text description follows the
screen: the same words first, as a sentence ("Sensor warming up." / "Sensor expired." / "No current data."),
then the last reading with when it was taken and its age ("Last reading 64 milligrams per deciliter, today at
5:39 PM, 2 hours ago."; what is not known is left out), then the page sentence (5); no zone word or trend is read. After the stated sensor expiry, while readings
still arrive, "Sensor expired." comes first and the value, the time, the trend and the page sentence follow as usual.
At the sensor's limit the value is read as "below the sensor's lower limit" or "above the sensor's upper limit" with
its zone, followed by since when and the last measured value, and no trend, as on the screen. While GlucoCraft has a
Repairs notice open, "Check Home Assistant." is read before the page sentence.

The *Last Reading* block of the statistics card (Consensus Ranges, *Show All Statistics*) reads a shorter sentence: the
no-current-data status when there is no current data, the value with its unit and zone, the time of the reading and
the trend. It does not read the sensor-expired or warm-up status or the 5-minute delta; the status is in the page's
banner.

**Every unit is spelled out in spoken text**, in both glucose units and every other unit GlucoCraft shows. One rule
produces the spoken text, so that no surface differs: it is implemented once for the cards, in their shared module
`glucocraft-shared.js` (used by the display card, the dashboard cards and the CGM Report page), and once in
`spoken.py` (the Spoken summary sensor and the notifications), and the tests compare the two implementations:

| Shown | Spoken |
|---|---|
| mg/dL | milligrams per deciliter |
| mmol/L | millimoles per liter |
| mg/dL/min, mmol/L/min | milligrams per deciliter per minute, millimoles per liter per minute |
| % (time in range, CV, sensor active) | percent |
| A1C / GMI % | percent |
| A1C / GMI mmol/mol | millimoles per mole |
| min, h, d | minutes, hours, days |
| 14d, 90d (period labels) | 14 days, 90 days |
| −3 / +3 (change) | minus 3 / plus 3 |
| °, arrows (↑ ↗ → ↘ ↓) | the trend words ("rising quickly", "rising", "changing slowly", "falling", "falling quickly") |
| LO / HI | "below the sensor's lower limit" / "above the sensor's upper limit" |
| <, >, ≤, ≥ before a number ("<4%", "≤36%") | "less than", "more than", "at most", "at least" ("less than 4 percent", "at most 36 percent") |

Decimal numbers follow the user's decimal-separator setting; the screen reader reads them in the page language. Every
card declares the language of its texts (`lang`, the language of the catalogue in use).

Dashboard pages:
- the page title first;
- the status / value / time / trend sentence where the page shows the current reading;
- key results as real tables, with table headers, in the CGM Report's order (time in ranges first);
- one summary sentence per chart (charts are never silent): the chart is an image (`role="img"`) named by that sentence (`aria-label`);
- buttons and links with names that say where they lead ("Next page: 24 hours", "Open CGM Report");
- the page footer at the end of every page, with the line "For development and testing only. Not for medical use."
  and its link to the full disclaimer (Documentation, *Disclaimer*); the CGM Report reads the disclaimer once, in
  *About This Report*. No copy of the disclaimer is hidden from screen readers.

Roles and names on the dashboard:
- the banners (no current data, sensor expired, sensor expiry, warm-up, second source, reading times ahead of the
  clock) are announced through one
  status message (`role="status"`) that exists from the first drawing and changes only when the conditions change (their
  titles; the minute-by-minute ages are not announced); the page footer is the page's content information
  (`role="contentinfo"`);
- the headline card at the top of a page is a link to its page, or a button that opens Home Assistant's details of its
  entity, named by its title with the units and the mark in words; its lines are its description (`aria-describedby`);
- the rows and buttons of the pages card on Start are links with their addresses (`<a href>`), reached with Tab and
  opened with Enter, and they can be opened in a new tab; the *Maintenance* button is named by its status
  ("Maintenance: no issues", "Maintenance: 2 issues"), and the Maintenance page's *Maintenance issues* card, shown only
  while there are issues, is a named region (`role="region"`) with the issues as a list;
- in tables, a text with unit symbols or comparison signs is shown as written and hidden from screen readers, which
  read its spoken form instead; result tables have row headers, and every table has a name (a caption or the heading
  above it, `aria-labelledby`); a table wider than the screen scrolls in a region that the keyboard reaches;
- the texts of the pages (About This Page, the Maintenance and Settings Status texts) are shown in GlucoCraft's own
  text card, not in Home Assistant's Markdown card, so that these rules reach them: GlucoCraft's link color and focus
  ring (rules 2 and 5), no text below 14 px, the language of the texts (`lang`), and tables with a name, header cells
  with their scope and a scroll region that the keyboard reaches;
- *Show All Statistics* (Consensus Ranges, and Custom Ranges while it exists) is a button that says whether its section is expanded
  (`aria-expanded`);
- while a CSV export runs, the *Save CSV export* button stays focusable, is marked as unavailable (`aria-disabled`) and
  ignores a further press; the result is announced in its status line (`role="status"`);
- the *Last Reading* and *Period Statistics* titles of the statistics card are headings;
- the display card's glucose block is a button named "Open the weekly pages", and on the weekly pages "Back to the
  6-hour graph"; a page change is announced through a polite live region (`aria-live="polite"`) ("Page 2 of 17: 24 Hours", then the page's sentence); on the About page
  (*About GlucoCraft*) the drawing is named by the page's title and the text follows as text, which can be read word by
  word;
- each section of the Documentation is a heading with a button that shows and hides the section and says whether it is
  expanded (`aria-expanded`; the disclosure pattern of the WAI-ARIA Authoring Practices Guide) and names the section it controls (`aria-controls`); a section link or a citation moves the
  focus to its target, and scrolls without motion where the system's reduced-motion setting is on;
- a link that opens a new tab says so ("opens in a new tab").

Exception to short names: the display card's drawing is named by its whole description, so that a screen reader
reads the status, value, time, trend and view first (guide step 3).

The CGM Report page has real headings, table headers and one summary sentence per chart. It is the alternative to
the PDF. Its choice buttons (period, form, colors) are toggle buttons whose state is announced
(`aria-pressed`) and shown by a check mark besides the color, each choice a group named by what it chooses
(`role="group"`); its messages are announced (*Creating the PDF…*, *PDF
created*: `role="status"`; an error: `role="alert"`); an invalid laboratory entry marks its fields (`aria-invalid`), names the
error under the form (`aria-describedby`) and moves the focus to the first invalid field; its links to the Documentation are real links; each
day of the daily profiles is named with its weekday and date in words. The PDF is tagged for screen readers (`pdf_tags.py`): a structure tree in the order of the page with
headings, paragraphs, lists, tables with header cells, figures whose alternative text is the chart's summary sentence,
and artifacts for the rules, frames, backgrounds, the repeated page header and footer and the running head of Notes
that continue on the next page ("<section> (continued)"); every section reads in the order caption, data, Notes, and
the glucose sources that the page header names (an artifact) stand once in the text of About This Report; a text with a comparison sign
carries its words as its actual text ("less than 4%"); its headings H1 and H2 are its bookmarks; it states its title
and language (/Lang) and embeds its fonts. The CGM Report page and its PDF write "met" and "not met" as words (the
dashboard's ✓ and ✗ are read as "meets" and "does not meet"). The PDF has not yet been
validated against PDF/UA-1 (ISO 14289-1) and claims no PDF/UA conformance until a validator has passed it
(`tools/check_pdf_ua.py`); the CGM Report page remains the alternative to the PDF.

## 5. Keyboard and focus

WCAG 2.2 success criteria 2.1.1, Keyboard (https://www.w3.org/TR/WCAG22/#keyboard), 2.4.7, Focus Visible
(https://www.w3.org/TR/WCAG22/#focus-visible), 2.5.1, Pointer Gestures (https://www.w3.org/TR/WCAG22/#pointer-gestures),
and 4.1.2, Name, Role, Value (https://www.w3.org/TR/WCAG22/#name-role-value).

- Every control on the dashboard can be reached and used with the keyboard, in reading order. Links are real links
  (with an address); buttons are real buttons.
- Focus is visible, and it stays on the control that was used when a card or page is drawn again (a new reading, the
  minute's update, a choice on the CGM Report page): unchanged content is left in place, and changed content focuses
  the same control again. The Documentation page is drawn again only when the language or a value its text shows
  changes (a setting, a source's name or limits, the reading interval), not on a new reading alone; the open sections
  stay open, but the page is drawn anew, so the focus does not stay on the control that had it.
- A link to another place on the same page moves the focus there.
- Nothing is reachable only by hover or by swipe. The panels' swipes and 1-second holds have button equivalents on the
  display card (on GlucoCraft's dashboard, or on any dashboard it is added to).
- Nothing that can receive focus is inside a part that is hidden from screen readers (`aria-hidden`).

## 6. Motion and timing

WCAG 2.2 success criteria 2.3.1, Three Flashes or Below Threshold
(https://www.w3.org/TR/WCAG22/#three-flashes-or-below-threshold), 2.2.2, Pause, Stop, Hide
(https://www.w3.org/TR/WCAG22/#pause-stop-hide), and 2.2.1, Timing Adjustable
(https://www.w3.org/TR/WCAG22/#timing-adjustable).

- No flashing on the dashboard, the display cards and the panels, with one stated exception, a throb of two texts:
  - while there is no current reading, the words in the value's place ("Sensor warming up", "Sensor expired" or "No
    current data") throb for the first 30 minutes of each episode, then hold still and stay on screen for as long as
    the episode lasts; a new episode starts a new 30 minutes. The 30 minutes count from the age of the reading (or the
    start of the warm-up), so a reboot of a panel does not start them again. A signal that stays on all night teaches
    the eye to skip it, and the words remain on screen after the throb ends;
  - after the stated sensor expiry, the status bar's text "Sensor expiry passed" throbs for as long as the expired
    status lasts, until a new sensor is detected, without the 30-minute limit: it is small text in the status bar, and
    putting on a new sensor can take a while, so the limit of the large words is not needed for it.

  Both fade smoothly from full brightness to about 30% and back, one cycle every 3 seconds (about 0.33 Hz, far below the
  three flashes per second of 2.3.1), and never disappear; only their brightness changes, so a screen reader reads the
  same text throughout and nothing is announced. The setting *Fade the words when there is no current reading* (on by
  default) turns both off on the display cards and every panel (2.2.2); the display cards also stop them while the
  system's reduced-motion setting is on (`prefers-reduced-motion`), so a viewer without access to the settings can stop
  them as well. At full brightness the words, in the text color, meet the contrast of rule 2. The panels' screen
  without Home Assistant does not throb.
- The status bar's middle slot shows two texts in turn, each for 3 seconds, while a GlucoCraft notice is open in
  Repairs and the slot also has a text of its own: "Check Home Assistant" and "Custom ranges" or the warm-up text
  (`DISPLAY_CONTRACT.md`). This is a change of text, not a flash, and it does not stop under the reduced-motion
  setting; the card's text description does not change with the turns: it states "Check Home Assistant." and the
  warm-up.
- No information disappears on a timer without another way to reach it. The display card returns to its 6-hour page
  after 3 minutes without use, but not while one of its buttons has the focus or the pointer is over it. A screen
  reader that reads a page without moving the focus does not hold it, so the card has the option *Return to the first
  page after 3 minutes* (on by default), which turns the timer off (2.2.1 asks for a way to turn such a timer off).
  The wall panels always return after 3 minutes. The panels'
  start-up screen shows GlucoCraft's icon, name, version and the disclaimer line for 15 seconds at every start, with
  no button (a swipe ends it earlier). The disclaimer's short form is always on the About page *About GlucoCraft* (page
  17 of 17 on the display card), reachable at any time, on the panels and on the display cards, and its long
  form in the Documentation, so the start-up screen's
  brief display does not limit access to its text.

## 7. Notifications

- Plain text first: the title names the notification ("GlucoCraft Daily Summary"), and the body states the value
  with its unit spelled out ("milligrams per deciliter", "millimoles per liter") and its time. Glucose values are
  written with their unit in words; percentages keep "%", the A1C and GMI values keep their unit symbol, and the titles
  keep "mg/dL" / "mmol/L".
- LO / HI are written as words ("below the sensor's lower limit" / "above the sensor's upper limit").
- The trend is written as trend words ("Changing slowly"); no arrow symbols.
- Comparisons are written in words ("consensus target more than 70%"), and the values of a summary line are separated
  by semicolons.
- No information is carried only by emoji or color.

## 8. Customization

Users can change:
- the seven display colors (the glucose value and everything else that shows a zone by color; the zone word is drawn in
  the text color);
- the seven graph range colors (by default the colors of the CGM Report's glucose profile);
- the card background ("Home Assistant theme" or a color);
- the wall panel background (a color);
- *Outline text* (off by default): the 1-pixel edge of rule 2, identical on the display card and every panel,
  intended for light backgrounds and light graph bands;
- *Fade the words when there is no current reading* (on by default): the throb of rule 6, off on the display card and
  every panel;
- *Return to the first page after 3 minutes* (an option of each display card, on by default): the card's return to its
  first page (rule 6);
- *Trend shown as* (the arrow by default, or the trend word): the TREND column of the display card and every panel;
  the zone word stays under the value with either choice.

Both backgrounds are black by default. On a chosen background the page text switches automatically to black or white,
whichever contrasts more (rule 2); the graphs keep their black area. The status colors (green, amber, red, purple and gray) are fixed.

The rules above still apply with every choice: the zone word, the status words, ✓ / ✗ and the number scale remain, so
a custom palette never removes information.

## 9. Voice and honest limits

- GlucoCraft's values are ordinary Home Assistant entities, so voice assistants can read them. GlucoCraft ships no
  voice intents. The Documentation says this neutrally and shows one example, marked as not part of GlucoCraft: the
  lines a user adds to Home Assistant's `configuration.yaml` for the sentence "read the GlucoCraft Spoken summary".
- The optional *Spoken summary* sensor (`sensor.glucocraft_spoken_summary`) holds one ready-to-speak sentence, a shorter
  form of the cards' screen-reader text made by the same rule (rule 4) and in its order, without the 5-minute delta
  and the page sentence (for example "Glucose 112 milligrams per
  deciliter, tight range, measured at 7:46 AM, changing slowly."; without current data "No current data. Last reading at
  7:46 AM."; after the stated sensor expiry "Sensor expired." before the reading, with the trend while readings still
  arrive; during a sensor warm-up "Sensor warming up." alone; before any reading "No glucose reading yet."). Its
  state changes only with a reading, a change of status, no current data, zone, trend or LO / HI, a change of the
  display unit, the decimal separator or the time or date format, and with the date, so it names the time of the reading
  in the time format setting, never an age that would no longer be correct; a reading of an earlier day is named with
  its day ("Last reading yesterday at 9:45 PM.", or its date in the date format setting). It is disabled by default,
  because every new state is stored in the recorder. The Documentation explains how to enable it.
- The wall panels are visual only: a screen reader cannot read them, and a person who is blind cannot use them. The
  same pages are available on the display card, which has screen-reader text. The Documentation states this plainly,
  without advice.
- The screen-reader rules (rule 4) apply to the dashboard, the display cards, the CGM Report page and the
  notifications, not to the wall panels.

## Checks

The release process runs automatic tests, and findings are fixed before release:
- contrast: every text / background pair of the default palettes, against the minimums in rule 2;
- color vision: a simulation of protanopia, deuteranopia and tritanopia for the default palettes (rule 1);
- keyboard and ARIA: every control reachable and named, every link with an address, every chart and card with a
  text description in the rule-4 order, nothing focusable inside a hidden part (rules 4 and 5);
- focus and messages: the focus stays on its control when a card or the CGM Report page is drawn again; the state of
  the choice buttons; the status messages and their regions (rules 4 and 5);
- spoken text: the spoken forms of units, trend arrows and comparison signs, the same in the cards' shared module and in Python
  (rule 4).

## How accessibility is checked

GlucoCraft is designed to follow WCAG 2.2 level AA where it applies. No conformance claim is made. GlucoCraft does not
claim to be accessible. Each surface has been reviewed against
WCAG 2.2 level AA and the WAI-ARIA Authoring Practices Guide (World Wide Web Consortium, n.d.), and the automatic
checks under *Checks* run with GlucoCraft's tests; the review and the checks describe the work done, not a result.
[SCREEN_READER_TEST_SCRIPT.md](SCREEN_READER_TEST_SCRIPT.md) describes, page by page, what a screen reader is designed to
read; people who use a screen reader can follow it and report where GlucoCraft differs (*Feedback*).

## Known limitations

The same limitations, with their reasons and alternatives, are listed in the Documentation (Accessibility, *Known
Limitations*).

- The CGM Report PDF is tagged but has not yet been validated against PDF/UA-1; the CGM Report page is the
  alternative to the PDF (rule 4).
- The wall panels are visual only (rule 9); the display card shows the same pages with screen-reader text.
- 12 px text (rule 3): the statistics labels of the square 480 × 480 panel and the 3.5-inch 480 × 320 panel; on the
  3.5-inch panel also "TREND", "5 MIN DELTA", the source name, the weekly description line and the GMI value in both
  A1C units; and the statistics labels of the square display card.
- White graph text on the default high band (#FCC21B, drawn at 60% over the graph's black area) reaches 4.11:1, below
  4.5:1 (rule 2). The light-gray key "--- Min to max" above the period graphs is below 4.5:1 on four default bands:
  2.19:1 on the high band, 3.05:1 on the very-high band, 3.04:1 on the tight-range band and 4.42:1 on the in-range
  bands. The same values are in the statistics beside each graph and in the text description.
- The display card returns to its first page after 3 minutes without use, also while a screen reader reads a page without moving the focus. The card setting Return to the first page turns this off.
- The automatic contrast checks cover the default colors only; the contrast note of the settings measures the saved
  colors on the saved background (rule 2).
- A new reading is not announced by a screen reader; the card is read again to hear it (rule 4).
- Windows high-contrast mode does not change the colors of the cards' drawings; the text descriptions of the cards and
  charts carry the same information.
- Every page of GlucoCraft's dashboard has the browser title that Home Assistant gives the dashboard, "GlucoCraft –
  Home Assistant" (WCAG 2.2 success criterion 2.4.2, Page Titled); the page's own name is shown in Home Assistant's
  top bar.

## Feedback

Accessibility problems are welcome as bug reports at <https://github.com/Verstreubulator/glucocraft> with the area *Accessibility*. It helps to
name the screen reader or browser and its version, the page, and what was heard or seen. No response time is promised.

## References

Machado, G. M., Oliveira, M. M., & Fernandes, L. A. F. (2009). A physiologically-based model for simulation of color
vision deficiency. *IEEE Transactions on Visualization and Computer Graphics, 15*(6), 1291–1298.
https://doi.org/10.1109/TVCG.2009.113

World Wide Web Consortium. (n.d.). *ARIA authoring practices guide*. Retrieved October 28, 2026, from
https://www.w3.org/WAI/ARIA/apg/

World Wide Web Consortium. (2024). *Web content accessibility guidelines (WCAG) 2.2* (W3C Recommendation, December 12,
2024). https://www.w3.org/TR/WCAG22/
