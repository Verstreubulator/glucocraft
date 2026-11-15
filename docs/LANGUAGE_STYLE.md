# GlucoCraft language style

GlucoCraft's language is clear, concise, accurate and professional everywhere. These rules apply to every user-facing
text: the dashboard, the cards, the display cards, the wall panels, the setup screens, the notifications, entity names
and attributes, and the user documentation. They sit beside `ACCESSIBILITY.md`.

## Rules

1. **Register.** Formal and neutral, in the manner of the cited consensus statements and manufacturers' user manuals.
   No conversational phrases, no contractions, no slang.
2. **No evaluation.** Values are compared with published targets only: *meets / does not meet*. No *good, bad, better,
   worse, improving, favorable*. Changes are "numerical differences without clinical interpretation".
3. **Attribution.** Every target, range, term, formula and finding taken from a source is cited where it is used,
   and listed in Documentation > References. Rules defined by GlucoCraft are labeled as such: in prose, by the
   sentence "This method is a GlucoCraft rule." after the sentences it covers; "(GlucoCraft rule)" stands only in
   short labels (a table cell, a row, option or entity description that is not a sentence, a caption such as
   "Change (GlucoCraft rule): ...") and inside the cited-context texts (rule 15), which carry it in their
   closing sentence. It is made clear that GlucoCraft is not the one making decisions: GlucoCraft sets no targets or
   ranges and makes no assessments; every target shown is a cited consensus target or the user's own goal from the
   settings (wording: *Consensus target* / *Custom goal*; "your goal (Settings)" is not used). Targets and ranges come
   from "the cited published sources (consensus statements, studies and manufacturer documentation), each named as
   what it is" (rule 18).
   **Citation style: APA 7th edition, author–date.** In the text: "(Battelino et al., 2019)" for three or more
   authors; "(Smith & Jones, 2020)" in parentheses and "Smith and Jones (2020)" in a sentence for two; group authors
   spelled out ("(Abbott Diabetes Care, 2022)"); several sources in alphabetical order separated by semicolons
   ("(Battelino et al., 2019; Monnier et al., 2017)"); a part after the year ("(Battelino et al., 2023, Table 4)"); the
   year of the journal issue. The reference list is in APA 7 format, alphabetical by first author, with the DOI written
   as a URL, https://doi.org/..., and nothing else (no PubMed link in the reference text). Scope: the GlucoCraft
   integration and dashboard (pages, report, PDF, notifications, settings, Documentation); not the wall panels and not
   the display cards that mirror them.
4. **Population findings.** Research results are stated as findings in study populations ("Among 6,225 adults …"),
   never as statements about the reader.
5. **No instructions about treatment.** Texts do not advise any action affecting glucose: GlucoCraft's information can
   be late, missing or wrong, and acting on it can cause harm.
6. **Device neutrality.** No CGM, service or integration is assumed. Device names appear only where rule 20
   allows them; there are no passing examples.
7. **Spelling.** American English, as in the principal references (ADA, *Diabetes Care*): color, hypoglycemia,
   recognize. Units with a space: 70 mg/dL, 15 minutes; percentages without a space: 70%. Data is plural (the data
   are), as in the cited clinical literature. Durations in running text in words ("2 hours 5 minutes"); the symbols h,
   min and d only in the CGM Report's event summary table (its columns have no room for the words) and in the time
   per day beside a percentage ("(1 h 12 min)"), with their key in About This Report ("Durations: d, days; h, hours;
   min, minutes."), and read in words by screen readers; the Maintenance page keeps its note on the panels' uptime.
   **Short forms on the displays.** The display cards and the wall panels have little room, so their labels use short
   forms, defined in `PANELS.md` (*Short labels*) and the Glossary. They use "Min", "Max" and "Avg" consistently (the
   statistics labels, the weekly page "Weekly: Min and Max"), and their status bar gives the age of the reading as
   "Reading: 3 min ago" / "Reading: 2 h ago" (min and h without a plural). The dashboard and the CGM Report keep
   "Lowest", "Highest" and "Mean", and screen-reader texts say the words.
8. **Terms.** Time in range (TIR), time below range, time above range, time in tight range (TITR), coefficient of
   variation (CV), glucose management indicator (GMI), Glycemia Risk Index (GRI), level 1 / level 2, sensor active
   (labeled as in rule 21), no current data, sensor warm-up, consensus ranges, ranges in the settings, custom ranges,
   consensus target, custom goal, range set, CGM Report. Every term is defined in the Documentation's Glossary; the
   README links to it.
9. **Second person.** Used sparingly and only for instructions ("Select **Configure**"); descriptive text is neutral.
   **Paths.** A menu path and the path to a Documentation section use ">" between the steps, as Home Assistant writes
   them: "Settings > Devices & services > GlucoCraft > Configure", "Documentation > References"; never "→".
10. **"Custom", not "Personal".** Wherever the user sees it: *Custom Ranges* (dashboard page `/custom`), *Custom
    Goals*, *Custom goal* (weekly pages). "Personal" and "Your goal" are not used in user-facing text; internal keys
    stay `personal`. Consensus values and custom values are never swapped or mixed: *Consensus target* for a cited
    target, *Custom goal* for a value from the settings.
    **Display colors and the zone word.** The default display palette shows lows in red and highs in blue (very low
    #C62828, low #EF5350, in range #FFC107, tight range #4CAF50, high #42A5F5, very high #1565C0); users can change each
    color (settings screen *Display card and wall panels*), and GlucoCraft publishes the colors in use (`zone_colors`).
    The range bands of the graphs have seven colors of their own, the *graph range colors* (the name of the settings
    section; user texts never say "graph band colors"), by default the colors of the CGM Report's glucose profile
    (`band_colors`). Color is never the only cue: every glucose value on the display cards and panels carries its
    zone word under the value, in every setting, in sentence case: "Very low", "Low", "In range", "Tight range", "High",
    "Very high". The CGM Report keeps the colors of the published report examples (red for lows).
11. **Numbers and units.** Glucose in the display unit: mg/dL as whole numbers, mmol/L with one decimal; halves are
    rounded away from zero. The consensus thresholds are written as published in both units: 54 / 70 / 140 / 180 /
    250 mg/dL = 3.0 / 3.9 / 7.8 / 10.0 / 13.9 mmol/L. **Exception, notification bodies:** the body of a notification
    spells out the glucose unit in the display unit, "milligrams per deciliter" or "millimoles per liter", because a
    phone's screen reader reads a notification as written (`ACCESSIBILITY.md` rule 7). Titles and all other visible
    text keep "mg/dL" / "mmol/L".
12. **Dates and times.** GlucoCraft's *Time format* and *Date format* settings (Automatic from Home Assistant's
    country, or chosen): 12-hour "7:46 AM" or 24-hour "07:46" (with a leading zero); dates "Oct 22" / "22 Oct",
    "10/22" / "22/10" / "10-22". Weekday and month names come from the language catalogue (`date.month.*`,
    `date.weekday.*`, `card.date.*` and the panels' generated texts), never from code.
13. *(Not in use.)*

**Rule 14: no claims.** Nothing may imply that GlucoCraft's results and information are accurate, usable or
reliable. No text anywhere (dashboard, cards, report, PDF, notifications, settings, Documentation, README, panels) may
describe GlucoCraft or its values as accurate, precise, reliable, trustworthy, validated, correct, dependable or safe,
recommend using it, or present it as helping to manage diabetes ("helps you", "lets you track", "monitor your glucose
with", "stay on top of", "keep you safe"). Say what it does in neutral terms ("calculates and displays"). Describing a
published source's own accuracy finding is allowed when cited and clearly about that source (for example a study's
finding about GMI), never about GlucoCraft.

**Rule 14a: no advertising, no recommendation.** GlucoCraft is made available, never advertised or recommended: the
authors may say that it exists and that they look for testers, developers and advisors, but never tell anyone to use
it. Features are described as what the code is designed to do ("designed to calculate and display ..."), not as
benefits, and no text makes a claim (rule 14).

**Rule 14b: public statements.** (1) GlucoCraft's published materials and the authors' announcements never describe the
authors' own use of GlucoCraft or its results. (2) They never quote, repost or link users' descriptions of their own
use: on the project's own channels these would read as the project's claims.
Not covered: what users do or say, what is already public (it is not repeated or linked), and the authors' personal
answers about their own choices. Posting and announcing the repository anywhere is allowed; an announcement describes
GlucoCraft: what it is and does, that it is free and open source and made available for development and testing, that
testers and contributors are welcome, and that it is not intended for any medical purpose, such as diagnosing,
monitoring or managing diabetes or making treatment decisions, and has not been cleared or approved by the FDA or any
other regulator (the disclaimer, rule 16).

**Rule 15: cited context.** Next to a number or rule GlucoCraft already calculates or shows, a short explanation may
describe what the cited source says about it: its definition, target and reasoning. It begins with a lead-in naming
the source ("The 2019 international consensus defines ...", "Battelino et al. (2019) set the target at ..."), uses the
source's own terms, carries the APA 7 citation, and states why it is shown (for example "This explains the target in
the Below 70 row."). It is descriptive and never addressed to the reader as an instruction, and it contains no
treatment, medication or dosing content of any kind, not even as a topic to discuss.
Wanted wherever possible: information, such as what a target is, what a level means, what a study found and how a
value is calculated. Excluded: anything that tells a person to do something about treatment, medication, food or
activity (for example to use insulin, to eat fewer carbohydrates or to go for a run).
**Guiding principle:** GlucoCraft states what the most current research says about the results it calculates; it does
not tell people what to do with these results. Where sources differ, GlucoCraft's calculations follow the treatment
standards (the international consensus for clinical use and the ADA Standards of Care); newer research (for example
the 2023 consensus on CGM metrics for clinical trials) is shown as context only, named as such.

**Rule 16: the disclaimer, once per surface.** The disclaimer is one text in three forms (`const.py`), each shown word
for word, once in each of its places. The long form (`const.py` DISCLAIMER) is shown in full at the top of the
README, on the first setup screen (as plain text; Submit continues), in the Documentation's *Disclaimer* section, in
*About This Report* on the CGM Report page and in its PDF, and in CONTRIBUTING.md. The short form (`const.py`
DISCLAIMER_SHORT), the same substance in fewer words, stands where the screen holds no more: the About page of the
display cards and the wall panels (with GlucoCraft's icon, the version line and the copyright line; page 17 of 17 on
the display card). Where there is no room for either, one line (`const.py` DISCLAIMER_LINE) stands for it: the
footer of every dashboard page ("For development and testing only. Not for medical use." with a link to the full
text, *Disclaimer*) and the wall panels' start-up screen (with the icon, the name and the version); the PDF's page
footer puts the name before it ("GlucoCraft. For development and testing only. Not for medical use."). Every summary
notification ends with the line "This is a notification, not an alarm."; the
operational notices (the wall-panel notices, the export, import and report notices, the wall-panel address, the
notice after a removal that kept the readings, the no-data notice and the delivery test) add nothing to their own
text. No other sentence on the same surface repeats the disclaimer in other words, and no text gives instructions
or safety advice in its place. `tests/test_disclaimer.py` compares the copies.

**Rule 17: custom goal and range set.** A value for a metric set in the settings is a *Custom goal* (never "your goal",
"Your goal" or "your goal (Settings)"); a value a cited consensus publishes is a *Consensus target*. The basis of the
weekly pages is the **range set**: "They use one of two range sets: the consensus ranges or the custom ranges." The
setting is "Range set of the weekly pages". "Standard" keeps only its other meanings: *treatment standards* (the
consensus statements for clinical care and the Standards of Care), *Standards of Care* and *standard deviation*.

**Rule 18: consensus.** "Consensus" names only the three consensus statements: Danne et al. (2017), Battelino et al.
(2019) and Battelino et al. (2023). A consensus statement is a published statement in which a panel of clinicians and
researchers, convened for that purpose, agrees on definitions, ranges and targets. Individual studies (for example
GMI, Bergenstal et al., 2018; CV, Monnier et al., 2017; GRI, Klonoff et al., 2023) and manufacturer documentation (for
example the trend categories, Abbott Diabetes Care, 2022) are named as what they are. The Terminology statement:
targets and ranges are "taken from the cited published sources (consensus statements, studies and manufacturer
documentation), each named as what it is". A value is labeled *Consensus target* only when a consensus statement
publishes it (the CV target of 36% or less: Battelino et al., 2019, with Monnier et al., 2017).

**Rule 19: the CGM Report.** The report page, its sidebar and page names, headings, links, notices, the PDF (title,
header, page-1 heading, file names `glucocraft-cgm-report_…`, print job) and the docs say **CGM Report**. "AGP"
appears only in citations and in the one description sentence, used where the report is described: "The CGM Report
shows the standardized metrics and glucose profile recommended for the Ambulatory Glucose Profile (Battelino et al.,
2019; Bergenstal et al., 2013)." Never "AGP-style", "AGP-like" or "similar to the AGP Report"; the International
Diabetes Center is not named. The graph is the *glucose profile*. Internal code names (`period_stats_agp`,
`AGP_RANGES`, ...) may stay; user-visible keys do not (`consensus_ranges`).

**Rule 20: other products only when necessary, written as their owners write them.** Allowed in user-visible text: Home
Assistant, HACS and ESPHome; the panel boards (Guition, Waveshare, ESP32) in the panel setup; ReportLab and Material
Design Icons in license notices only; FreeStyle Libre, Dexcom, Medtronic / CareLink / Simplera / Guardian, Eversense,
Medtrum, SIBIONICS, xDrip+ and Nightscout only in the Documentation section "Readings Outside the Sensor's Range" (the
Automatic rules and the cited table) and in citations and References; Abbott only as a cited source (the LO/HI method,
the trend categories); the CGM systems and phone health apps of *Sources not supported for live display*
(FreeStyle Libre 14 day, FreeStyle Libre (first generation), Dexcom Stelo, Abbott Lingo, Apple Health and Health
Connect, with the Home Assistant iOS and Android apps and the iPhone) only in that text (the Documentation's Supported
Sources and its copy in the README). Every other mention becomes a generic phrase ("a CGM integration", "a second system
that reads the same CGM sensor"); setting options are plain numbers ("40 mg/dL (2.2 mmol/L)") and their descriptions
point to the Documentation section. Forms: SIBIONICS, xDrip+, "Home Assistant" in full (never "HA" in user text), a
generic noun after a Dexcom, Medtronic or Abbott name ("Dexcom CGM system", "Simplera Sync sensor"), never possessive
("the code 401 of Dexcom CGM systems", "the report software of Abbott Diabetes Care"), no ™ or ®. The Trademarks section
(the Documentation's *About and License* and the README) names, with their owners, every CGM, health and phone-platform
product and company that GlucoCraft's texts mention (FreeStyle Libre, LibreLinkUp, Lingo and Abbott; Dexcom and Stelo;
Medtronic, CareLink, Simplera and Guardian; Eversense; Medtrum; SIBIONICS; Apple Health, iPhone and iOS; Android and
Health Connect), and also Home Assistant, ESPHome, ESP32, Guition, Waveshare, Nightscout and xDrip+. Every other name
(HACS, ReportLab, Material Design Icons, Liberation Sans, Windows, Linux, Mac) falls under its general sentence, "other
names are the trademarks of their owners". The platform names have their places as well: the phone and computer
platforms (Android, iPhone, iPad, Mac) in the screen-reader guide's set-up steps (`SCREEN_READER_TEST_SCRIPT.md`,
section 1 and its introduction); Google Fonts in the panel guide's build steps (`PANELS.md`, *Building a panel from the
examples*); besides these, the Home Assistant iOS and Android apps and the iPhone in *Sources not supported for live
display*, and the Trademarks sections. `tests/test_published_texts.py` checks these places.

**Rule 21: sensor active.** GlucoCraft measures data reception, not the time the CGM sensor was worn (`statistics.py`,
the gap rule). Wherever the sensor-active percentage is shown, it carries a label and the sentence "Close
to, but not the same as, the CGM system's own figure: only the readings received are counted." Both appear once per
surface: on the CGM Report and the PDF, on each dashboard page that shows the percentage, in the README. The
label is "Sensor active (data received)" in the CGM Report and its PDF, which describe the data and name GlucoCraft
only where needed (rule 23), and "Sensor active (data received by GlucoCraft)" on the dashboard, in the Documentation and
in the README. The entity attributes `label` and `note` of the sensor active sensors share the report's catalogue keys
(`const.py` `sensor_active_label()` and `sensor_active_note()`, from `report.sensor_active.label` and
`report.sensor_active.note`), so they carry the report's form. The full explanation (each reading represents the time
until the next one; a short dropout counts in full; across a gap only the reading's own reading interval counts; why
the CGM system's own figure can differ) is given once in the CGM Report (About This Report, Data), once in the
Documentation (How GlucoCraft Is Designed); the README carries the label and the sentence (Features). The entities are
named "<period> sensor active" ("14D sensor active"; the ids follow the names). **Entity names and state names** are in
sentence case, Home Assistant's rule: the first word starts with a capital letter and the rest is lower case, except
abbreviations and the period labels ("Last glucose reading", "14D custom time in range", "14D GMI", "Glucose in
range"; the states "Consensus ranges", "Not configured", "Less than 6 hours"); every text that names an entity or a
state writes it so. A dashboard row label that is an entity's name follows that name ("Last glucose reading", "Data
history", "Sensor expiry", "Trend arrow", "5-minute delta"; the sensor-active rows "Sensor active (data received by
GlucoCraft)"). The dashboard's page, card and section titles, the Documentation's section titles and the report's
headings keep their title case ("Consensus Ranges", "Trend Arrow Calculation", "Change Over Time", "Gaps, Reading
Interval and No Current Data").

**Rule 22: the CGM Report's appendices.** The main report always uses the consensus ranges and targets. Two appendices
are added only when chosen (the options `include_custom_appendix` and `include_research_appendix`, off by default;
labels "Include the custom ranges and goals appendix" and "Include the research measures appendix"):
- *Custom Ranges and Goals*: the statistics with the ranges and goals in the settings, labeled as the user's own
  settings; *Custom goal* (rule 17), met / not met only from 14 days with at least 70% data.
- *Measures Defined for Research*: the text and method of the catalogue keys `report.research.*` (`report.json`),
  their home; `SYSTEM_DESIGN.md` section 6.4 quotes them word for word (`tests/test_one_wording.py` compares the
  two); named as defined for clinical trials (Battelino et al., 2023), with the statement that no clinical-care
  definition or target exists; no color, no ✓ / ✗, no target; only in this appendix, not on the dashboard or the
  panels.

**Rule 23: the CGM Report describes the data.** The report's explanatory texts (the report page and the PDF) describe
the data and their method, not the product: "the readings received for this report", "readings are kept for 200 days",
"Method", never "GlucoCraft received" or "GlucoCraft keeps". GlucoCraft is named only where a sentence would become
unclear or untrue without it. Home Assistant is never named in the report's explanations. Unchanged: the title, the
page footer, the disclaimer, the not-affiliated statement, the standard statement,
"This method is a GlucoCraft rule." and the print messages shown in Home Assistant. One exception, for the clarity of
the data: the report names its glucose sources by their names in Home Assistant (the entity's name, else its entity
id), in every page header ("Glucose data: <primary>; second source: <second>.") and in About This Report; the
Documentation and the report setting text say: "The report prints the names of the glucose sources. To keep a personal
name off the report, rename the source in Home Assistant." In the report, cited context (rule 15) stands in each
section's Notes: one caption line names the rule and its citation, the data follow, and the Notes at the section's end
hold the full cited explanation.

**Rule 24: no suggested uses.** GlucoCraft provides the tools; it never suggests or says that something should be done
with them. Texts say what GlucoCraft and its entities contain and that they exist. They never suggest a use as a
forecast or prediction or as a basis for treatment or dosing, never tell anyone to share a report with a healthcare
professional (and no text says not to share it), and they give no automation example. GlucoCraft provides no alarms:
its notifications (in Home Assistant and on phones) may be described and offered, and they are always notifications
(rule 26). Neutral facts are allowed: the dashboard can be opened on any device signed in to Home Assistant;
the CGM Report and its PDF can be printed or shared, for example with a healthcare professional (the
FDA's 2022 software guidance lists recording glucose data to share with a health care professional among its
low-risk functions);
notifications can be sent to more than one phone; GlucoCraft's entities are ordinary Home Assistant entities, available
to other dashboards, automations and scripts; the Documentation's Accessibility section shows one voice example, marked
as not part of GlucoCraft. No text markets GlucoCraft for watching another person's glucose, and no text warns about
what users choose to do on their own. `tests/test_no_suggested_uses.py` fails on the usual forms of a suggestion ("keep
an eye on", "share with your", "bring to your", "AGP report" and others) and on their constructions in any words (an
action toward a healthcare contact, another person watching, building an automation or alarm, a device switched by
the entities, an alarm sound, being notified or woken, a prediction, a treatment or food action, a stand-in for an
alarm), after the known negations and the allowed facts are removed, in every published text; suggestions written in
ordinary words check that the test catches them. The test is a backstop: no list of words catches every suggestion,
so the rule is kept by reviewing each new text against it.

**Rule 25: one wording per topic.** Where texts talk about the same or a similar subject, they use the same text
everywhere: one master text per topic, its own sentences reused (or its first sentences with a pointer to the
master), with the same facts and the same terms in every place. A change of such a text changes every copy and
every place that explains the same topic at once, and the old wording and its near-variants are searched for afterwards.
This extends rule 16 from sentences to topics.
- *Sources not supported for live display*: the master in the Documentation's Supported Sources; the README carries a
  copy.
- *No high or low glucose notifications* (GlucoCraft sends none): the master in the Documentation's *Notifications
  (Not Alarms)*; the same sentence in the README's Notifications and in `SYSTEM_DESIGN.md` section 1. It states the
  fact and points to no other use of GlucoCraft's entities (rule 24).
- The three essentials (the sensor limits must match the CGM sensor's measurement range; readings every 5 minutes or
  more often; what deleting GlucoCraft deletes): the Documentation's Essentials and the README. The setting texts, the Settings Status notes and the Documentation use their sentences for the
  same topics ("GlucoCraft is designed for readings every 5 minutes or more often.", "The sensor limits must match the
  CGM sensor's measurement range. ...").
- The after-setup check list: the README's Configuration and the Documentation's Setup.
- The disclaimer: `const.py` DISCLAIMER, every full copy word for word, its short form and its one line (rule 16).
- The gap rule: "an interval between two readings that is longer than 15 minutes or three reading intervals, whichever
  is longer", with "the reading interval being the median of the 10 intervals before the reading" where the interval
  is explained; the master in the Documentation's *Gaps, Reading Interval and No Current Data*. The developer documents
  may write it as the formula max(15 minutes, 3 × the reading interval).
- The second source: the master in the Documentation's *How GlucoCraft Is Designed* (*Two sources*); its first
  sentence, "A second source is designed to supply readings that the primary source misses, up to 24 hours back, and
  to continue when it stops, merged by measurement time.", everywhere else. The CGM Report keeps its own data-sources
  text (rule 23).
- The panel offline notice: "when a panel's Status entity has been off or unavailable for 10 minutes"; the
  Maintenance lists keep their short item "a wall panel offline for 10 minutes or more".
- The status words of a sensor warm-up: "Sensor warming up" in the status, Maintenance, speech and the value's place
  on the display card and the wall panels, and the banner "Sensor Warming Up"; the status bar of the display card and
  the wall panels says "Warm-up", for its length.
- The standard statement: one text; the CGM Report's copy leaves out the manufacturer item (the report shows no trend
  categories) and gives a report example of a rule.

**Rule 26: notifications, never alarms or alerts.** GlucoCraft's notifications are always called notifications (the
summaries, the notices), never alarms or alerts, in every text and every name a user sees; the code's keys and ids
follow (`notify_enabled`, `glucocraft_daily_summary`). GlucoCraft sends no notifications about high or low glucose
(rule 25). That its notifications are not alarms is said in three places only: the disclaimer ("Its notifications are
not alarms."), the Documentation's *Notifications (Not Alarms)* section, and the last line of every summary ("This is
a notification, not an alarm.").
`tests/test_no_suggested_uses.py` fails on a text that calls GlucoCraft's notifications alarms or alerts.
