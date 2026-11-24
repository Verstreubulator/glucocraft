# GlucoCraft developer guide

This guide is for anyone who wants to read, run, test or change GlucoCraft's code. It explains how the parts fit
together, where each rule is implemented, how to run the integration and the tests, and how to build a wall panel
from the example configurations. What GlucoCraft shows and how to use it is described in the [README](../README.md);
every entity, attribute and endpoint is listed in [ENTITIES_AND_API.md](ENTITIES_AND_API.md).

GlucoCraft's disclaimer is at the top of the [README](../README.md).

Works are cited in APA 7 author–date form. Full references: Documentation > References. The sources consulted for the
code (the integrations, libraries, issues and documentation behind its rules) are listed in
[SYSTEM_DESIGN.md](SYSTEM_DESIGN.md), section 14, *Sources consulted*.

## Contents

1. [The parts](#1-the-parts)
2. [Module map](#2-module-map)
3. [Data flow](#3-data-flow)
4. [Threading model](#4-threading-model)
5. [Where each rule lives](#5-where-each-rule-lives)
6. [File formats](#6-file-formats)
7. [Running GlucoCraft in a development Home Assistant](#7-running-glucocraft-in-a-development-home-assistant)
8. [Lint and tests](#8-lint-and-tests)
9. [Building a wall panel from the example configurations](#9-building-a-wall-panel-from-the-example-configurations)
10. [Glossary of code names](#10-glossary-of-code-names)
11. [Version policy](#11-version-policy)
12. [Synthetic data and the edge-case tests](#12-synthetic-data-and-the-edge-case-tests)

## 1. The parts

| Part | Where | Language |
|---|---|---|
| The integration: reading intake, storage, statistics, entities, notifications, report, HTTP API, panel feed | `custom_components/glucocraft/` | Python |
| The dashboard, its cards, the display card, the report page and the Documentation page | `custom_components/glucocraft/www/` | JavaScript (no build step) |
| The example wall-panel configurations and the C++ headers they include | `esphome/` | ESPHome YAML, C++ |
| The test suite | `tests/` (see [tests/README.md](../tests/README.md)) | Python (pytest), a few tests run JavaScript with Node |
| The tests on Home Assistant itself | `tests_ha/` (see [tests/README.md](../tests/README.md#tests-on-home-assistant-itself)) | Python (pytest with `pytest-homeassistant-custom-component`) |
| The developer tools: the synthetic test data (section 12), the test language `pseudo` (section 2), the PDF/UA-1 check of the CGM Report and the brand icon | `tools/` (`synth/`, `i18n/make_pseudo.py`, `check_pdf_ua.py`, `make_brand_icon.py`) | Python |

The integration has one runtime dependency, `reportlab` (the PDF), declared in `manifest.json`. The cards are plain
JavaScript files that the integration serves itself; there is no bundler and no `node_modules`.

## 2. Module map

One line per module of `custom_components/glucocraft/`: what it owns, and who uses it.

| Module | Owns | Used by |
|---|---|---|
| `__init__.py` | Setup and unload of the config entry, the actions (services), the entry version check, the removal (everything, or everything but the data kept for a reinstall: the setting *When GlucoCraft is deleted*) | Home Assistant |
| `kept.py` | The readings, laboratory results and sensor events kept for a reinstall: keep (at removal), `kept_summary` (the setup's question), adopt (the first start), discard (start fresh) | `__init__.py`, `config_flow.py` |
| `renamed_entities.py` | At every start and 2 seconds after a change of an id, the check of each entity's id against the id it was created with; the Repairs notice `entity_ids_changed` and, on the user's confirmation, the putting back of the original ids (a change is never undone by itself). | `__init__.py`, `repairs.py`, `stale_entities.py` |
| `source_rename.py` | The sources' settings follow a new entity id, from the entity registry's event: what is kept per source goes with it, and the entry reloads. | `__init__.py` |
| `stale_entities.py` | At every start, the removal of the entity-registry entries of the entry that the setup did not create (an optional feature that is off, a feature removed by an update; only when every platform reported its entities); their recorded history stays | `__init__.py`, `sensor.py`, `binary_sensor.py` |
| `config_flow.py` | The setup screens and the Configure menu of eight screens by topic, Glucose ranges and Custom goals only while the optional feature Custom ranges and goals is on (`options_menu`; every field has a short label and a `data_description` in `strings.json`; the collapsed sections are flattened before saving, so no stored key changes), range and goal checks | Home Assistant |
| `flow_schemas.py` | The forms of the setup and Configure screens and the field helpers they share: the collapsed sections (`_flatten`, `_section_errors`), the selectors, the color-picker conversions | `config_flow.py` |
| `flow_helpers.py` | The flows' helpers that reach outside the form: the entity-registry lookups (GlucoCraft's own entities, the sources' platforms), the printer address check and the phones' delivery test | `config_flow.py`, `flow_schemas.py` |
| `const.py` | Configuration keys, defaults, thresholds, colors, trend bands, statistics periods, the disclaimer and the license notices | every module |
| `settings.py` | The one reader of the stored settings: `setting(data, key)`, `ranges(data)` and `band_colors(data)` return each setting validated (a value of the wrong type or outside its span is the setting's default; a range set out of the settings screen's order is the consensus ranges; with the sensor's limits, `ranges(data, span)` keeps the very-low and the very-high threshold within them and `sensor_limit_conflicts` names a stored one that no longer fits); the spans and choices the settings screens offer. All settings are kept in the entry's data (one place for the setup and the Configure screens, read by one settings module); the entry's options stay empty | every module that reads a setting, `config_flow.py` |
| `diagnostics.py` | Config-entry diagnostics (Settings > Devices & services > GlucoCraft > Download diagnostics): the settings with the entity ids of the sources, the expiry entity and the phones, the printer address, the id of the wall-panel address, the custom range limits and goals (replaced by whether custom ranges and goals are set) and the value of any key GlucoCraft does not know redacted, the version, the stored readings' count and time span (never the readings), the detected interval, the source status, the open repair issues and the panel feed state | Home Assistant |
| `contrast.py` | WCAG 2.2 relative luminance and contrast ratio; the text color on a chosen background (`card_foreground`, `panel_foreground`); the contrast note of the Display card and wall panels screen (the zone colors and the text color on each background, the white graph labels on each graph range color at 60% over the graph's black area) | coordinator, `config_flow.py` |
| `spoken.py` | The spoken text (units, LO / HI, trends and comparison signs written out), the Python form of the cards' shared spoken-text functions; the Spoken summary sentence | sensors, notifications, the PDF |
| `coordinator.py` | `GlucoCraftCoordinator`: listens to the source entities, stores readings, smooths, computes the slope, the trend and the statistics, the no-current-data state, the second source, the clock check | entities, API, panel feed, notifications, report |
| `source_notices.py` | The sources' Repairs notices checked on the coordinator's timer (missing, fast and slow source, sensor cap and floor, readings at the automatic limit, the expiry entity's notice; a mixin of `GlucoCraftCoordinator`), and the issue ids of every source notice | coordinator |
| `storage.py` | `GlucoCraftStorage`: the binary readings file (GC02), load, save, merge, add (stored readings win), purge, window lookup | coordinator, history import, export, report |
| `history_import.py` | The first fill of the store from Home Assistant's recorder (skipped without a recorder), and the intake rules it shares with the live readings (`attribute_time`, `state_number`, `unitless_mmol`) | coordinator |
| `gaps.py` | The one gap rule and the reading interval per stretch; the time weight of each reading | statistics, report, coordinator |
| `limit_runs.py` | The stretches of readings at the sensor's limit (an unbroken run of LO or HI readings, ended by a measured reading, the other kind or a data gap): the stretches of a period for the graphs' edge marks, and the current stretch with the newest measured reading before it | coordinator, http_api, panel_feed |
| `limits.py` | Readings outside the sensor's measurement range (LO / HI), no-reading states, the source's lower and upper limit, `threshold_span` (the span of the very-low and very-high threshold), `below` / `above` (a LO reading below and a HI reading above every threshold) | coordinator, history import, statistics, binary sensors, notifications, report |
| `units.py` | mg/dL and mmol/L, the consensus value pairs, rounding halves away from zero | every module that shows a value |
| `unit_registry.py` | Writes the display unit into the entity registry of every glucose sensor, and reads the unit in use from it (`shown_unit`) | `__init__.py`, coordinator, `config_flow.py` |
| `statistics.py` | `PeriodStats` and `compute_period_stats`: time-weighted mean, SD, CV, GMI, time in ranges, GRI, sensor active; the GMI rule (`gmi_shown`) and the GRI period rule (`gri_shown`) | coordinator, report |
| `change.py` | Change Over Time: the 14-day and 7-day values against the consensus targets and the user's goals | coordinator (for the sensors and the cards) |
| `regional.py` | Time and date formats, first day of the week, decimal separator, A1C / GMI unit, paper size ("Automatic" from Home Assistant's country and language) | notifications, report, PDF, configuration attribute |
| `entity.py` | The base class of every entity: coordinator, unique id, entity id from the name, the one device | `sensor.py`, `binary_sensor.py` |
| `entity_ids.py` | The rule entity id = name | `entity.py` |
| `sensor.py`, `binary_sensor.py` | The entities and their attributes | Home Assistant |
| `notification.py` | The daily, weekly and monthly summaries, sent on a schedule; the no-data notice; the forwarding to the phones | `__init__.py` (schedule, actions and the no-data notice) |
| `expiry.py` | Reads an optional sensor-expiry entity in any of its usual forms | coordinator, sensors |
| `repairs.py` | The Repairs fix flow of the notice about readings later than the system clock | Home Assistant's Repairs |
| `report.py` | The CGM Report's calculations (the readings' seconds on local time as the statistics' weights, the minute grid of the display bins, daily profiles, events and the full report's events by period, weekly trend, laboratory A1C, the share of the second source) | report service, API |
| `report_pdf.py` | The CGM Report as a PDF (reportlab; every section a caption line, its data and its Notes, `Report.notes`), and the IPP printer client | report service |
| `pdf_tags.py` | The tagged PDF: TaggedCanvas and Tagger (structure tree, marked content, artifacts, XMP) | `report_pdf.py` |
| `report_service.py` | `GlucoCraftReports`: report data for the page, PDF files, printing, laboratory A1C entries | API, actions |
| `export.py` | The CSV export of readings and laboratory A1C entries, and its import (`read_readings_csv`, `read_lab_csv`) | actions, Maintenance page |
| `http_api.py` | The authenticated HTTP endpoints `/api/glucocraft/*` | the cards, the report page |
| `panel_feed.py` | The GLU2 v3 files (with the GCX1 extension, the GCR1 block, the GCM1 block and the GCL2 trailer) and the local-only GET webhook for the wall panels | wall panels, display card |
| `panel_notice.py` | The notifications when a wall panel is added, and when one is offline; a panel whose device is deleted is forgotten | `__init__.py` |
| `maintenance.py` | `MaintenanceIssues`: the Maintenance status (offline wall panels, open Repairs issues, a failed CSV export), published as the Configuration attribute `maintenance_issues` | coordinator, `panel_notice.py`, `__init__.py` (the CSV export), the dashboard |
| `frontend.py` | Serves `www/*.js` and the folder `i18n/` (`<base>/i18n/<language>/<area>.json`) under a content-hashed path (the hash covers both) and registers the Lovelace resources | `__init__.py` |
| `strategy_dashboard.py` | Registers the GlucoCraft dashboard (a Lovelace strategy) in the sidebar | `__init__.py` |
| `i18n.py` | The backend's language catalogues `i18n/<language>/backend.json` and `report.json`: loaded once in the executor at setup for Home Assistant's language, English per missing key; `t(key, **params)`, plurals with `tp(key, count)` and the plural-rule table `PLURAL_RULES` | every module that writes a text |

The backend's texts (see the header of `i18n.py`): every notification, the spoken summary, the panel notices, the
CSV headers, the regional words (month and weekday names, AM / PM) and every text of the CGM Report come from the
catalogues; Home Assistant's own texts (settings screens, entity names and states, Repairs, actions) are in
`strings.json` (identical to `translations/en.json`); the entity and action icons are in `icons.json` (Home
Assistant's icon translations, by platform and translation key, with an icon per state where the state names a
different situation; no entity sets an icon in the code, `tests/test_icons_and_categories.py`). The keys are flat and dotted by area (`notify.`, `spoken.`,
`panel_notice.`, `export.`, `import.`, `config_flow.`, `contrast.`, `panel_feed.`, `date.`, `time.`, `duration.`,
`entity.`, `status.`, `trend.`, `zone.`, `limit.`, `limits.`, `regional.`, `repairs.`, `api.`; `report.` in
`report.json`, which the Report page reads too), with the same placeholders and plural keys as the cards. A report text
that names a consensus threshold writes it as a placeholder: `{t54}` ... `{t250}`, the ranges `{t54_69}`, `{t70_140}`,
`{t70_180}`, `{t181_250}`, and `{unit}`; the PDF (`report_pdf._L`) and the Report page (`rL`, from the report data's
`thresholds`) fill them in the display unit from one table, `units.REPORT_THRESHOLDS`, and never scan a text for
numbers. A cited text keeps both units as published (`_ctx`, `rCtx`). The PDF's fonts are chosen per script (`report_pdf.FONTS`; a language in another alphabet adds a TrueType font with its glyphs).
The disclaimer: `const.py` keeps its English text in three forms (`DISCLAIMER`, the long form of the backend's setup
screen and PDF; `DISCLAIMER_SHORT`, the short form; `DISCLAIMER_LINE`, the one line of the PDF's page footer); the
report page and the Documentation show the catalogue's `disclaimer.text`, the display card's About page
`disclaimer.short`, and the panels `panel.about.title` and `panel.about.disclaimer` (the short form), which a
translation translates like every other text; `tests/test_disclaimer.py` checks that
the English copies are the same words. Developer override of
the language (never shown to users): the environment variable `GLUCOCRAFT_LANGUAGE` (for example `pseudo`).
`tests/test_i18n_backend.py` checks the keys and placeholders, the fallback and the files;
`tests/test_i18n_english_output.py` compares the English output of every surface with a snapshot.

The JavaScript files in `www/`:

| File | Content |
|---|---|
| `glucocraft-strategy.js` | The dashboard strategy: every view and its layout |
| `glucocraft-ui.js` | The dashboard's cards (statistics, history, Change Over Time, settings, status, ...); the header lists every custom element |
| `glucocraft-display-card.js` | The display card: the wall panel's pages in the browser (square and wide variants) |
| `glucocraft-report.js` | The CGM Report page |
| `glucocraft-docs.js` | The Documentation page: the order of the sections, the references (APA 7) and the rendering; the text is the document `i18n/<language>/documentation.json` |
| `glucocraft-shared.js` | The cards' shared module (`globalThis.glucocraftShared`): the rules every card and page uses, written once (section 5): the settings in use, the number, glucose, time and date formats (every time in Home Assistant's time zone), the two-decimal rule, the spoken text, content and focus across redraws, the gap rule, the graphs' minimum-to-maximum areas, the default colors, the consensus ranges and defaults (`CONS_RANGES`, `STALE_DEFAULT_MIN`, `RETENTION_DAYS`), GlucoCraft's link and button colors and the screen-reader CSS (`css.sr`), the GLU2 parser (version 3 and later) and the downloads, whose failures are logged once per reason (`gcNewFailure`). Every card file imports it and waits for it before it defines its elements |
| `glucocraft-i18n.js` | The one language module of the cards (`globalThis.glucocraftI18n`): loads the catalogues `i18n/<language>/frontend.json`, `report.json` and `documentation.json` for Home Assistant's frontend language with English as the fallback per key; `t(key, params)`. Every card file imports it and waits for it before its first render |

The cards' texts (frontend, see the header of `glucocraft-i18n.js`): flat dotted keys by area (`card.`, `ui.`,
`report_page.`, `strategy.`, `spoken.`, `disclaimer.`, `limit_time.` in `frontend.json`; `docs.` in
`documentation.json`), named placeholders `{name}`, plural forms as separate keys `<key>.one` / `<key>.other` chosen with `Intl.PluralRules`. A
language is added as a folder `i18n/<code>/` with the same files; a missing key falls back to English. The
Documentation's sections are lists of HTML parts with placeholders for the live settings and the tokens
`{cite:id}`, `{cite:id|part}`, `{ref:id}`, `{ref_year:id}` and `{sec:id|text}`, which the page turns into links.
Developer override of the language (never shown to users): `localStorage.glucocraft_language` (for example `pseudo`).
`tests/test_frontend_i18n.py` checks the keys, the placeholders, the module and the Documentation.

What is not translated, in one list: the entity ids, attribute keys, state keys, setting keys, action fields and the
keys of the HTTP API (fixed identifiers; the names shown for them are translated); the messages in Home Assistant's
log; the reference list of the Documentation and of the CGM Report (the works as published) and quotations from a
cited source, which keep the source's words; the names of products and organizations; and the README and the
developer documents in `docs/`, which are in English only. Everything a user sees on the dashboard, the cards, the
CGM Report and its PDF, the notifications, the settings screens and the wall panels comes from the language
catalogues, the disclaimer included. Languages written from right to left are not supported: the cards and the
panels set no text direction (`dir`) and lay every text out from left to right.

The same text under two keys: one concept has one key, which every reader uses (the weekday names of the cards, the
Settings Status page and the CGM Report page are `card.date.weekday.*` and `card.date.weekday_short.*`; the page
names are `strategy.page.*`). Two keys of one catalogue keep the same English text only where a translation may need
two texts. `tests/test_catalogue_duplicates.py` lists every such group with one of these reasons, and fails on a new
one until it is merged into one key or listed:

| Reason | Why the keys stay apart | Example |
|---|---|---|
| plural | the plural forms of one text (`.one`, `.other`), which differ in languages with other plural rules | `spoken.unit.percent.one`, `spoken.unit.percent.other` |
| short form | a full and a short form; May has no short form in English | `date.month.5`, `date.month_short.5` |
| zone by id | the code reads the word of each zone by its id; both in-range zones are "In range" in English | `zone.word.near_low`, `zone.word.near_high` |
| separator by role | a separator in a list, in a file name or in a spoken list; a language may write each one differently | `contrast.separator`, `notify.list_separator` |
| zone names by place | the contrast notice names each of the seven zones distinctly, the zone word stands under the value | `contrast.zone.low`, `zone.word.low` |
| display card | the display card's own set (`card.*`), which matches the wall panels' catalogue; the dashboard and the CGM Report page have their own sets | `card.describe.measured`, `ui.statistics.said.measured` |
| spoken | the spoken text for screen readers writes every word out; a shown text may be shortened in another language | `spoken.unit.day.one`, `ui.duration.day.one` |
| Settings Status tables | the words the strategy writes into the Settings Status page's tables: a complete set of goal and zone words of their own | `strategy.goal.time_low`, `ui.goal_name_in_text.time_low` |
| table labels | each table's column heads and row labels, and a figure's alternative text, which is read aloud: a narrow column of the PDF may need a shorter word | `report.blocks.col.cv`, `report.weekly.col.cv` |

The test language `pseudo`: `python tools/i18n/make_pseudo.py` writes it from English for every area
(`i18n/pseudo/*.json`) and for the panels (`<panels>/i18n/pseudo.yaml`, then `make_panel_texts.py pseudo`; the panel
folder is `--panels <folder>`, else `GLUCOCRAFT_PANELS`, else `esphome/` in the repository, and the panel texts are
left out without one of them or with `--no-panels`; `--out DIR` writes the catalogues into DIR only); every letter
accented, each text about 35% longer and between « and », placeholders and markup kept. A text that shows in
plain English under pseudo is not in a catalogue; a text that no longer fits shows the room a longer language needs.
It is chosen only by the developer overrides (`GLUCOCRAFT_LANGUAGE=pseudo`, `localStorage.glucocraft_language`, the
panel substitution `language: pseudo`). The generated files are not committed (`.gitignore`; they follow from English,
and what is not in the repository is never in a release); `make_pseudo.py --clean` removes them, for example before
copying the integration to a Home Assistant. `tests/test_pseudo_locale.py` makes the texts in memory;
`tests/test_no_text_in_code.py` fails on a user-visible literal in the Python or JavaScript code.

## 3. Data flow

```
source entity (a CGM integration's glucose sensor; optional second source)
   │  state_changed / state_reported events (event loop)
   ▼
coordinator: measurement time ─ unit (mmol/L → mg/dL) ─ LO / HI ─ merge of two sources
   │  executor, under the reading lock
   ▼
storage (GC02 file, 200 days) ──► smoothing, slope, trend ──► statistics (7 periods, every 5 minutes)
   │
   ├──► entities (sensor.glucocraft_*, binary_sensor.glucocraft_*) ──► dashboard and cards
   ├──► HTTP API (/api/glucocraft/data, /stats, /binary, /report, /report.pdf; Home Assistant login)
   ├──► panel webhook (GLU2 v3 files; GET only, local network only) ──► wall panels, display card
   ├──► notifications (summaries on a schedule) ──► Home Assistant, notify actions
   └──► CGM Report (report.py → report_pdf.py) ──► PDF file, printer (IPP), report page
```

1. **Intake.** `coordinator.py` subscribes to the state events of the source entities. Each reading gets its
   measurement time (the source's own time attribute when it has one, otherwise the state's time), is converted to
   mg/dL and checked for LO / HI. With two sources, the same measurement is stored once (the primary's copy wins); a
   second source is designed to supply readings that the primary source misses, up to 24 hours back, and to continue
   when it stops (the merge rules: `SYSTEM_DESIGN.md` section 3).
2. **Storage.** Readings are kept in memory, sorted by time; each stored reading is appended to the GC02 file at once
   (SYSTEM_DESIGN.md section 7, *Saved at once*). Readings older than the retention (200 days) are removed as whole
   local days after midnight.
3. **Derived values.** Each stored reading updates the slope of the smoothed glucose, the 5-minute delta and the
   trend arrow. The statistics of all periods (6 hours to 180 days, on the ranges in use and on the consensus
   ranges) and the weekly trend are computed every 5 minutes, and at once when the no-current-data state changes.
4. **Outputs.** The entities read the coordinator's attributes. The cards read the entities and the HTTP API; the
   wall panels and the display card read the GLU2 files. Notifications read the coordinator and the store under the
   reading lock; the statistics, the report and the CSV export copy the store under the reading lock and compute
   without it.

## 4. Threading model

* **Event loop.** Event handlers, entity updates, timers (`async_track_time_interval`, one tick per minute) and
  notifications run in Home Assistant's event loop and never block.
* **Executor.** Every file access and every computation over the store runs in Home Assistant's executor
  (`hass.async_add_executor_job`): loading and saving the store, processing a reading, the statistics, the GLU2 files,
  the summaries, the report, the PDF and printing.
* **The reading lock.** `GlucoCraftCoordinator._lock` (a `threading.Lock`) allows one pass over the readings at a
  time. Code in the executor that reads or changes the store runs through `coordinator.locked(fn, *args)` (or
  `_process_locked` for a reading), so no pass ever sees a half-merged store. The statistics pass, the CSV export and
  the report hold the lock only to copy the store (`storage.copy`, a few milliseconds) and compute from the copy
  without it, so that readings are stored meanwhile; one statistics pass runs at a time (`_stats_lock`, never held
  while waiting for the reading lock). Code in the event loop does not take the lock.
* **Back to the loop.** After a reading is processed in the executor, the coordinator hands the update to the loop
  with `hass.loop.call_soon_threadsafe` (`async_set_updated_data`, then the notification check).
* **Per-render settings.** `report.py` and `report_pdf.py` keep the regional and render settings in module state, set
  in one place per build or render and guarded by one lock each (`report._BUILD_LOCK`, `report_pdf.RENDER_LOCK`):
  two reports requested at the same time are built and rendered one after the other, each in its executor thread.

## 5. Where each rule lives

A rule is implemented once in Python. Where the cards need the same rule, they take it from one module,
`www/glucocraft-shared.js`; where the panels need it, they carry a copy (`gc_units.h`, `lvgl_compat.h`).

- **Loading.** Each card file is loaded on its own (a Lovelace resource, added by GlucoCraft or by hand, or an extra
  frontend module) and must work without the others. It therefore imports the shared module itself, as it imports the
  language module `glucocraft-i18n.js`: `import(new URL("./glucocraft-shared.js", import.meta.url))`, a path next to its
  own URL, so that it works below the versioned path `/glucocraft_v/<hash>/` and below the unversioned `/glucocraft/`.
  It waits for the module (`await GC_SHARED`) before it defines its elements, and takes the functions it uses by name
  (`const { ... } = globalThis.glucocraftShared`). The module defines `globalThis.glucocraftShared` once: a second load,
  from another card file or the other path, keeps the first. `frontend.py` serves every `www/*.js` below both paths, and
  the content hash covers the module, so a change of it reaches the browsers under a new versioned path.
- **Settings.** The display settings in use (decimal separator, A1C / GMI unit, time and date formats, display unit)
  are one object of the module (`settings`), written by each card from the configuration sensor (the report page from
  its report data) before it draws, and Home Assistant's time zone (`setTimeZone` with `hass.config.time_zone`), in
  which every date and time is shown (`zoneParts`; the browser's own zone only until it is set). This assumes one
  GlucoCraft installation (`single_config_entry`): every card on a page shows the same settings.
- **Tests.** `tests/test_card_shared_copies.py` compares the module with the Python rules (rounding, the two-decimal
  rule, units, the GLU2 parser, the loading of each card file alone), `tests/test_frontend_consistency.py` its gap rule
  with `gaps.py`, `tests/test_accessibility.py` its spoken text with `spoken.py`.

| Rule | Python | JavaScript | Wall panels | Test |
|---|---|---|---|---|
| Gap: an interval longer than max(15 min, 3 × the local reading interval) | `gaps.py` (`gap_threshold`, `local_intervals`, `weights`; constants `GAP_FLOOR_SECONDS`, `GAP_INTERVAL_FACTOR` in `const.py`) | `glucocraft-shared.js` (`gcGapThrAt`, `gcGaps`, `gcGapIn`) | `gc_units.h` | `test_data_path.py`, `test_frontend_consistency.py` |
| Time weight of a reading, sensor active | `gaps.weights`, `statistics.compute_period_stats` | - | - | `test_statistics.py` |
| Rounding: halves away from zero, once, where a value is shown (mean, SD, CV and percentages kept unrounded; no negative zero; two decimals at a rule, `units.shown_against`) | `units.round_half_up`, `units.fmt`, `units.shown_against` | `glucocraft-shared.js` (`round`, `nf`, `shownAgainst`, `nfAt`; `rh1` for a file's float32 glucose value, with the epsilon of `gcu::r1_mmol`; the GLU2 percentages without one, `panel_feed._pct32`) | `gc_units.h` (`gcu::num`, `gcu::pct_against`; `gcu::r1_mmol`) | `test_units.py`, `test_card_shared_copies.py`, `test_panel_configs.py`, `test_synth_scenarios.py` |
| Units: 1 mmol/L = 18.016 mg/dL; consensus pairs 54 / 70 / 140 / 180 / 250 = 3.0 / 3.9 / 7.8 / 10.0 / 13.9 | `units.py` | `glucocraft-shared.js` (`MGDL_PER_MMOLL`, `CONS_MMOLL`, `gD`, `gF`) | `gc_units.h` | `test_units.py`, `test_card_shared_copies.py` |
| Time and date formats: 12 / 24 hours, MDY / DMY / YMD, the decimal separator, the A1C / GMI unit | `regional.py` | `glucocraft-shared.js` (`settings`, `clock`, `hour`, `dayMonth`, `numDate`, `nf`, `dtx`) | `gc_units.h` | `test_settings_regional.py`, `test_dashboard_cards.py` |
| LO / HI: stored at the source's limit, flagged; LO below the lower limit, and also at a lower limit set by hand; HI above the upper limit, and also at an upper limit set by hand (compared at whole mg/dL or one decimal in mmol/L); a LO reading below every threshold and a HI reading above every threshold (always very low / very high) | `limits.py` (`classify`, `at_lower_limit`, `at_upper_limit`, `lower_set_by_hand`, `upper_set_by_hand`, `scale_position`, `below`, `above`) | shown as LO / HI, in GlucoCraft's zone color | shown as LO / HI, in GlucoCraft's zone color | `test_data_path.py`, `test_storage.py`, `test_settings_ranges_goals.py`, `test_range_sensor_limits.py` |
| The very-low threshold at or above the sensor's lower limit, the very-high threshold at or below its upper limit (the stricter of two sources); a stored threshold outside them used at the limit, with a Repairs notice | `limits.threshold_span`, `settings.ranges`, `coordinator._apply_ranges`, `config_flow._sensor_limit_errors` | - | - | `test_range_sensor_limits.py` |
| Stored settings: a wrong type or a value outside its span is the default | `settings.py` | - | - | `test_settings_invalid.py` |
| Smoothing (an adaptive 10-, 15- or 20-minute mean), slope, trend bands | `coordinator.ADAPTIVE`, `const.TREND_LIBRE_BANDS` | - | the arrow from the slope (`angle`) | `test_trend_and_configuration.py` |
| Statistics: time in ranges, mean, SD, CV, GMI, GRI | `statistics.py` | - | - | `test_statistics.py` |
| Hypoglycemia events (15 minutes below a limit; end after 15 minutes at or above 70 mg/dL; the readings' own times, to the second) | `report.py` (`report_events`, `_low_scan`) | - | - | `test_report.py`, `test_report_calculations.py` |
| Entity id = the entity's name | `entity_ids.py` | the ids the cards read | the ids the panels read | `test_entity_ids.py` |
| The disclaimer in its three forms | `const.py` (`DISCLAIMER`, `DISCLAIMER_SHORT`, `DISCLAIMER_LINE`) | the catalogue's `disclaimer.text` (the report page, the Documentation), `disclaimer.short` (the display card's About page) and `strategy.footer.disclaimer` (the page footer) | the About page (the short form) and the start-up screen (`panel.about.*`, `panel.startup.line`) | `test_disclaimer.py`, `test_frontend_consistency.py`, `test_panel_configs.py` |
| Display colors | `const.ZONE_COLORS` (sent as the `zone_colors` attribute; the current value's as `zone_color`) | read from the attribute; the same defaults in `glucocraft-shared.js` (`ZONE_DEFAULT`, `BAND_DEFAULT`, `gcPalette`) | the value's color from `zone_color` alone (no palette of their own) | `test_frontend_consistency.py`, `test_panel_configs.py` |
| Outline text: a 1-pixel edge around the graph labels and the text in a zone color, black around light text and white around dark text; off by default | `settings.py` (`text_outline`, published as the attribute `text_outline`) | `gcEdge`, `_ot` (display card) | `lvgl_compat.h` (`lvc_draw_text_edged`, `lvc_label_edge`) | `test_text_outline.py`, `test_panel_configs.py` |
| No current data, one fact: the flag (no reading, older than the limit of 5 to 30 minutes, or later than the clock) decides every display and the trend, never the status; without a current reading the words in the value's place, never a number ("Sensor warming up" during a warm-up, else "Sensor expired" while the status is expired, "No current data"), under them the last reading and when it was taken ("Last reading: 64 mg/dL", "Today at 17:39 (2 h 0 min ago)", split before the bracket on the square and 3.5-inch layouts), "---" for the trend and the 5-minute delta; the throb of all three words (about 0.33 Hz, to about 30%, setting `flash_no_current_data`); the status bar's age "Reading: 6 min ago" (hours from 100 minutes); after the stated expiry a current value in its zone color with its trend, and the status bar's "Sensor expiry passed" throbs | `coordinator.no_current_data`, `trend_current`, `settings.STALE_THRESHOLD_LIMITS`, `notification.NoDataNotice` | `_state` (`stale`, `old`, `words`, `gray`, `throb`), `NO_READING_KEYS`, `NO_READING`, `_noReading`, `_lastReading`, `_noTrend`, `_ageText`, the CSS `gc-throb` (display card); `ENT.nocur` (dashboard) | `update_glucose_display`, `no_reading_box`, `update_last_reading`, `gc_units.h` `no_reading_words`, `last_reading`, `reading_when`, `reading_ago` and `reading_age`, `lvgl_compat.h` `lvc_throb` | `test_no_current_data.py`, `test_panel_configs.py`, `test_settings_display.py` |
| Graph range colors (default: the CGM Report's colors, `report_pdf.AGP`), backgrounds and the text color on them | `const.BAND_COLORS`, `settings.band_colors`, `contrast.foreground` (sent as `band_colors`, `card_background`, `card_foreground`, `panel_background`, `panel_foreground`) | read from the attributes | read from the attributes | `test_settings_backgrounds.py` |
| Black or white text on a color: whichever has the higher WCAG 2.2 contrast ratio, white when both are equal | `contrast.foreground` | `glucocraft-shared.js` (`gcLum`, `gcContrast`, `gcOnColor`) | `gc_units.h` (`text_on`), `lvgl_compat.h` (`lvc_edge_color`) | `test_panel_contrast_rule.py`, `test_shared_rules_and_tidy.py` |
| Spoken text: units, LO / HI, trend words, plus and minus signs and comparison signs written out | `spoken.py` (`speak`, `speak_comparisons`) | `glucocraft-shared.js` (`gcSpeak`, `gcSpeakText`, ...) | - | `test_spoken_summary.py`, `test_accessibility.py` |
| Content and focus across redraws: unchanged content stays in place; after a change, the control that had the focus is focused again | - | `glucocraft-shared.js` `gcRender` (dashboard cards, CGM Report page); `gcFocusKey` / `gcRefocus` (the Documentation page) | - | `test_accessibility.py`, `test_frontend_edge_states.py` |
| The colors of GlucoCraft's own links and filled buttons (the theme's primary color mixed toward the text color or toward black, 4.5:1 in Home Assistant's default themes) and one focus ring (3 px, the text color) | - | `glucocraft-shared.js` `css` | - | `test_accessibility_checks.py` |
| GMI: % from the mean in mg/dL, mmol/mol from the mean in mmol/L (Bergenstal et al., 2018) | `units.gmi_percent`, `units.gmi_mmol_mol` | `glucocraft-shared.js` (`gmiMmolFromMean`, `gmiPctToMmol`) | - | `test_statistics_gmi.py`, `test_panel_configs.py` |

### Recipes: the files a change touches

A change reaches every layer that shows or states it: the code, the catalogues (`strings.json` and
`translations/en.json` identical, `i18n/en/*.json`, the panel catalogue), the documents and the tests. Every changed
`www/*.js` file and panel configuration takes one version step (section 11).

- **Support another CGM source.** Any sensor entity whose state is a glucose value in mg/dL or mmol/L already works.
  Its own measurement-time attribute: `const.SOURCE_TIME_ATTRIBUTES` (the live path and `history_import.py` read
  it). Its LO / HI texts, error codes or measurement limits: `limits.py` (`classify`, `auto_upper`, `is_dexcom_type`)
  and the same rule in the independent calculator `tools/synth/expected.py`. Document it in the Documentation
  (`documentation.json`, the sections *Supported Sources* and *Readings Outside the Sensor's Range*) and
  SYSTEM_DESIGN.md section 2; test it in `test_data_path.py` and with a scenario in `tools/synth/scenarios.py`
  (`test_synth_scenarios.py`).
- **Add a statistic.** The calculation in `statistics.py` (`PeriodStats`, `compute_period_stats`), with its value in
  `tools/synth/expected.py`; an entity in `sensor.py` (a period sensor class and its group in `entity_groups`) with its
  name in `strings.json` / `translations/en.json` and its icon in `icons.json`; the dashboard's table in
  `glucocraft-ui.js` and `frontend.json`; for the display card and the panels, a field of the GLU2 file
  (`panel_feed.py`, `glucocraft_panel_binary.h`, `parseGlu2` in `glucocraft-shared.js`, a new `GLU2_VERSION`);
  ENTITIES_AND_API.md, the Documentation's *Metrics and Sources*; tests in `test_statistics.py`, `test_synth_scenarios.py` and the
  entity counts (`test_optional_features.py`).
- **Add a wall-panel board.** A board entry in `esphome/tools/make_glucocraft_landscape.py` (its `BOARDS` and, for a
  new screen size, its layout), then the generated configuration; the fit check `esphome/tools/worst_case_fit.py`
  measures every layout; PANELS.md (*Panel types*); `tests/test_panel_configs.py` checks that the file is the
  generator's output (section 9).
- **Add a dashboard card.** The element in `glucocraft-ui.js` (its class, its line in `defs` and, for a card offered in
  the card picker, its line in `GC_PICKER`), its card-picker name and description (`ui.card_picker.*` in
  `frontend.json`), its place in `glucocraft-strategy.js`; DASHBOARD.md;
  tests in `test_dashboard_cards.py` and `test_accessibility_checks.py`.
- **Change the CGM Report.** The data in `report.py` (`build_report`), the PDF in `report_pdf.py` and the page in
  `glucocraft-report.js`, which show the same items with the same texts (`report.json`); the
  Documentation's *Report and PDF*; tests in `test_report.py`, `test_report_calculations.py`,
  `test_cgm_report_sections.py` and `test_report_pdf_text.py`; the English output snapshot
  (`test_i18n_english_output.py`) shows every changed text for review.

## 6. File formats

| Format | Where it is described | Version constant |
|---|---|---|
| GC02, the readings file (version 3: 16-byte header, 10 bytes per reading; the only version read) | the module docstring of `storage.py` | `STORAGE_VERSION` in `const.py` |
| GLU2, the panel and display-card file, with the GCX1 extension (range sets of the weekly pages), the GCR1 block (the stretches of readings at the sensor's limit), the GCM1 block (unrounded minimum and maximum) and the GCL2 trailer (the time at the sensor's limit) | the module docstring of `panel_feed.py`; the firmware structures in `glucocraft_panel_binary.h` | `GLU2_VERSION`, `EXT_VERSION` in `panel_feed.py` |
| The configuration attribute (`sensor.glucocraft_last_glucose_reading`) and the HTTP API's JSON | [ENTITIES_AND_API.md](ENTITIES_AND_API.md) | - |

`tests/test_panel_feed.py` checks the GLU2 layout field by field against a copy of the firmware structures, and
`tests/test_storage.py` checks the GC02 layout and that a file of another version is not read as readings.

## 7. Running GlucoCraft in a development Home Assistant

Use a Home Assistant that is not used for anything else.

1. Copy (or link) `custom_components/glucocraft` into the `custom_components` folder of the Home Assistant
   configuration folder, so that the result is `<config>/custom_components/glucocraft/manifest.json`.
2. Restart Home Assistant, then add GlucoCraft under Settings > Devices & services. A glucose sensor entity is needed
   as the source; for development, any sensor whose state is a number in mg/dL or mmol/L will do (for example a
   template sensor or an `input_number` shown through a template sensor).
3. After a change to a Python file, restart Home Assistant. After a change to a file in `www/`, restart Home Assistant
   as well: `frontend.py` serves the cards under `/glucocraft_v/<hash>/`, where the hash is computed from the file
   contents at start-up, so the browser and Home Assistant's service worker fetch the new files without a hard
   reload.
4. Each card file (`glucocraft-ui.js`, `glucocraft-strategy.js`, `glucocraft-display-card.js`, `glucocraft-report.js`
   and `glucocraft-docs.js`) logs its version to the browser console when it loads; the shared and language modules
   log theirs too, once (`GLUCOCRAFT-SHARED`, `GLUCOCRAFT-I18N`), and also give them as
   `globalThis.glucocraftShared.version` and `globalThis.glucocraftI18n.version`. Check them after a change.
5. Debug logging: add `custom_components.glucocraft: debug` under `logger: logs:` in `configuration.yaml`. A debug
   log names entities and folders of the installation; read it and remove names before sharing it. GlucoCraft's own
   lines name a source by its role and a notify action by its position among the chosen ones, never by name; Home
   Assistant's own log names a phone itself, at every log level, when its Mobile App integration cannot deliver a
   message to it (a failed cloud push names the device).

## 8. Lint and tests

From the repository root:

```
python -m pip install -r requirements_test.txt
python -m pytest                   # the whole suite
python -m pytest -m "not slow"     # without the PDF renders
ruff check .                       # lint, configured in pyproject.toml
ruff format --check .              # formatting (line length 120); `ruff format .` applies it
npx eslint@9 custom_components/glucocraft/www   # the dashboard files (configuration in eslint.config.js)
```

* Python 3.14, the version Home Assistant requires from 2026.3 and the one the CI uses (`.github/workflows/tests.yml`);
  ruff's `target-version` is Python 3.13 (`pyproject.toml`), so its upgrade rules suggest nothing that needs a newer
  Python. The tests do not need Home Assistant:
  `tests/ha_stub.py` stands in for it (see [tests/README.md](../tests/README.md)).
* Tests that run the cards' JavaScript need `node` on the path; without it they are skipped.
* `tests_ha/` runs GlucoCraft on Home Assistant itself, in a virtual environment of its own on Linux or macOS
  (`python -m pip install -r tests_ha/requirements.txt`, then `python -m pytest tests_ha`; see
  [tests/README.md](../tests/README.md#tests-on-home-assistant-itself)). The CI runs it on the oldest supported and
  the newest tested Home Assistant.
* The panel tests (`tests/test_panel_configs.py`, `test_panel_rounding.py` and the panel parts of
  `tests/test_entity_ids.py`, `test_optional_features.py`, `test_accessibility_checks.py`, `test_pseudo_locale.py`,
  `test_disclaimer.py`, `test_no_suggested_uses.py`, `test_panel_contrast_rule.py`, `test_card_shared_copies.py`,
  `test_limit_readings.py`, `test_published_texts.py` and `test_settings_and_standards.py`) read the example panel
  configurations from `esphome/` in the repository, or from the folder named by the environment variable
  `GLUCOCRAFT_PANELS`; without the folder they are skipped. They never read `secrets.yaml`. Some tests run a smaller
  check without the folder instead of a skip (in `test_panel_feed.py` and `test_recorder_load.py`, for example).
* Tests that read a document in `docs/` are skipped when that document is not in the copy of the repository.
* Optional sources, each skipped with the reason when its environment variable is not set (section 12):
  `GLUCOCRAFT_REAL_HISTORY` (a real history, kept outside the repository), `GLUCOCRAFT_SYNTH_PROFILE` (a profile
  fitted from a real history) and `GLUCOCRAFT_HA_PYTHON` (a Python with Home Assistant installed, for the real
  recorder). Without them the run uses artificial data only.
* Two developer tools need programs of their own: `python tools/check_pdf_ua.py` runs veraPDF's PDF/UA-1 profile on
  the CGM Report PDFs (veraPDF installed, which needs Java; without it the script exits with 2), and
  `python tools/make_brand_icon.py` writes `custom_components/glucocraft/brand/icon.png` and `icon@2x.png` (it needs
  Pillow, which is not in `requirements_test.txt`).
* The first line of every source file of the integration, the dashboard files, the tests, the published tools and
  the example panel configurations (their headers, the text catalogue, the generated texts, the panel tools and the
  starter file included; the generators write it) is the license line
  "License: AGPL-3.0-only (LICENSE)."; `tests/test_settings_and_standards.py` checks it.
* Ruff settings (`pyproject.toml`): line length 120, target Python 3.13, the default rules plus import order (I),
  pyupgrade (UP), bugbear (B), no lambda assigned to a name (E731), no ambiguous one-letter names (E741), no blind
  `except Exception` without a stated reason (BLE; each one carries `# noqa: BLE001 - <reason>`) and no `noqa`
  comment that suppresses nothing (RUF100). A change should not add findings.
* The calculation modules without Home Assistant: the package's `__init__.py` imports Home Assistant, so a script
  outside the tests cannot import `custom_components.glucocraft.statistics` (or any other module) without it. The
  tests call `ha_stub.install()` first (`tests/conftest.py`): it answers every import of Home Assistant with a
  stand-in and registers the integration as the package `glucocraft` without running its `__init__.py`, so that
  `from glucocraft import statistics` imports only that module. A script does the same: put `tests/` on `sys.path`,
  `import ha_stub`, call `ha_stub.install()`, then `from glucocraft import statistics`.
* Texts in published files: no file of the public repository names a decision or review id, quotes or refers to a
  remark of the maintainer, or carries a development date or the version in which something changed. Where the reason
  for a rule matters, the text states the reason in plain words; the history of the decisions stays in the
  maintainer's records, outside the public repository. A reference to the public repository's own pull requests and
  issues (`#123`) is allowed. The release scan checks this for every published file.
* Dependencies of the checks: `requirements_test.txt` pins every test and lint package to an exact version. The CI
  workflows (`.github/workflows/`) pin GitHub's own actions (`actions/checkout`, `actions/setup-python`,
  `actions/setup-node`) to the commit of a release, with its version in a comment, and `.github/dependabot.yml`
  proposes their updates every week; the validation actions follow the branches their documentation names
  (`home-assistant/actions/hassfest@master`, `hacs/action@main`), because they check against the current rules of
  Home Assistant and HACS. Every workflow sets its `permissions` (read-only or none).

## 9. Building a wall panel from the example configurations

The panel configurations are examples. They are not a finished or ready-to-use product. GlucoCraft does not
provide firmware: you build it yourself with ESPHome.

The build procedure is in [PANELS.md, Building a panel from the examples](PANELS.md#building-a-panel-from-the-examples),
with the boards in [Panel types](PANELS.md#panel-types); ESPHome 2026.10 or later is needed. In short: create the
first wall-panel address in GlucoCraft's settings (the build needs its id); copy the whole `esphome/` folder into the
ESPHome configuration folder, because a configuration needs more than its own file (the headers it lists under
`esphome: includes:`, the generated texts `i18n/generated/<language>/texts.yaml` and `gc_text.h` that the
configuration includes for its substitution `language`, and the images); fill in `secrets.yaml` from
`secrets.yaml.example`; set `ha_host` and `ha_port` (Home Assistant's HTTP port, 8123 unless it was changed), optionally `ntp_router` (a local time server; without it the Home Assistant address is used)
and, for the Waveshare P4 7B, `engineering_sample`, and the board in `glucocraft-starter.yaml`; then
`esphome run glucocraft-starter.yaml` (`esphome compile glucocraft-starter.yaml` builds without flashing), and add
the device in Home Assistant with its API key.

The four landscape configurations (`glucocraft-waveshare-lcd5.yaml`, `glucocraft-waveshare-p4-7b.yaml`,
`glucocraft-waveshare-lcd7.yaml`, `glucocraft-guition-35.yaml`) are generated from the square panel
`glucocraft-panel.yaml`. Never edit them by hand: change the square panel, then regenerate each board with

```
python esphome/tools/make_glucocraft_landscape.py <board>          # waveshare-lcd5, waveshare-p4-7b, ...
python esphome/tools/make_glucocraft_landscape.py <board> --out x.yaml   # write elsewhere, to compare
```

The texts are not in these files: every configuration takes them from the catalogue `esphome/i18n/<language>.yaml`
through the files that `python esphome/tools/make_panel_texts.py` generates in `esphome/i18n/generated/<language>/`.
Run it after a change to a catalogue,
`--check` reports a generated file that is out of date, and `python esphome/tools/worst_case_fit.py <language>`
checks that every text fits (PANELS.md, *Language of the panel texts* and *The fit check*).

The generator stops with an error on anything in the square panel it does not expect. `tests/test_panel_configs.py`
checks that every landscape file is exactly the generator's output.

## 10. Glossary of code names

Some identifiers in the code use other words than the user interface; the table maps them. Persisted keys (stored
settings, attribute keys, file formats) are part of the stored data and the published attributes, so a change of a
user-facing term does not rename them.

| In the code | In the user interface | Examples |
|---|---|---|
| `agp` | the consensus ranges (the ranges of the international consensus, Battelino et al., 2019) | `period_stats_agp`, `AGP_RANGES`, `on_agp` |
| `personal` | custom (the ranges and goals in the settings) | `personal_trend_weeks`, `WEEKLY_PAGES_PERSONAL` |
| `critical`, `crit`, `vlow` / `vhigh` | very low / very high (level 2) | `range_critical_low`, `time_critical_high`, `report_pdf.ZONES` |
| `ideal` | tight range (70-140 mg/dL) | `range_ideal_low`, `pct_time_in_ideal_range` |
| `stale` | no current data | `CONF_STALE_THRESHOLD`, `_stale_minutes` |
| `data_availability` | sensor active (data received by GlucoCraft) | `PeriodStats.data_availability` |
| `standard` | a range set of the weekly pages (consensus or custom) | GCX1 `standard_count`, `GCXStandard` (firmware structure) |
| `normal` | in range | `GLU2Ranges.normal_min`, `normal_max` (firmware structure) |
| `below_70`, `above_180` | the time below the low limit and above the high limit of the range set in use (70 and 180 mg/dL with the consensus ranges, the custom limits with the custom ranges) | the weekly values of `coordinator.trend_week_values`; `report.py` (always the consensus ranges) |
| `detected_interval`, `reading_interval`, `iv`, `fb` | the reading interval (the usual time between readings) | the `detected_interval` attribute, `gaps.local_intervals` |
| GC02 | the readings file | `storage.py` |
| GLU2, GCX1, GCR1, GCM1, GCL2 | the panel file, its range-set extension, its stretches at the sensor's limit, its unrounded minimum and maximum, its trailer of the time at the sensor's limit | `panel_feed.py` |
| `delta` (the file) | the recent-readings file: the last 2 hours and the statistics, fetched every 5 minutes (a published API and firmware name, kept); anywhere else "delta" means the 5-minute delta | `?file=delta` of `/api/glucocraft/binary` and the panel webhook, `panel_feed.py` |

The cards' JavaScript uses short helper names for the operations it repeats most. The shared ones are defined once in
`glucocraft-shared.js`; the others belong to the file named:

| Name | Where | What it does |
|---|---|---|
| `gcT` | every file | a text of the language catalogue, with its placeholders filled |
| `round` | shared | rounds half away from zero to d decimals (the backend's `round_half_up`) |
| `rh1` | shared | rounds a file's float32 mmol/L value to one decimal, as the panels do |
| `nf` | shared | a number rounded once to d decimals, with the decimal separator in use |
| `nfAt` | shared | `nf` with more decimals where d decimals would show a value on the wrong side of the rule beside it |
| `dtx` | shared | the decimal separator in use, in a text that already holds numbers (cited table numbers keep their point) |
| `gU` | shared | the display unit, "mg/dL" or "mmol/L" |
| `gD`, `gD32` | shared | a value in mg/dL as a number in the display unit (from a double; from a file's float32 value) |
| `gF`, `gF32` | shared | the same as text, "---" without a value |
| `p2` | shared | a number with two digits ("06") |
| `clock`, `hour` | shared | a clock time and a graph's hour label in the time format setting |
| `esc` | shared | text escaped for HTML |
| `n0`, `n1` | `glucocraft-ui.js` | a number with no or one decimal, "---" without one |
| `pc`, `pcAt` | `glucocraft-ui.js` | a percentage ("71%"), "---" without one; `pcAt` beside a rule, as `nfAt` |
| `gv`, `gs`, `gr` | `glucocraft-ui.js` | a glucose value in the display unit as a number, as text, and a range "70–180" |
| `p0`, `p1` | `glucocraft-display-card.js` | a percentage with no or one decimal ("100%" at 100.0), "---" without one |
| `rf`, `rG`, `rGu` | `glucocraft-report.js` | a report value with its unit ("%" by default), a glucose value, and a glucose value with its unit; "-" without a value |

The unique id of an entity is `<entry_id>_<key>` and never changes (`entity.py`). Where the key differs from the
entity's name (and so from its entity id), the two are:

| Unique-id key | Entity (entity id `<platform>.glucocraft_...`) |
|---|---|
| `current_glucose` | Last glucose reading (`last_glucose_reading`) |
| `delta_5min` | 5-minute delta (`5_minute_delta`) |
| `last_reading` | Last reading time (`last_reading_time`) |
| `data_maturity` | Data history (`data_history`) |
| `weekly_summary` | Weekly statistics (`weekly_statistics`) |
| `is_low`, `is_high` | Glucose below range, Glucose above range (`glucose_below_range`, `glucose_above_range`) |
| `is_critical_low`, `is_critical_high` | Glucose very low, Glucose very high (`glucose_very_low`, `glucose_very_high`) |
| `is_stale` | No current data (`no_current_data`) |
| `sensor_warmup` | Sensor warm-up (`sensor_warm_up`) |
| `in_range`, `in_tight_range` | Glucose in range, Glucose in tight range (`glucose_in_range`, `glucose_in_tight_range`) |
| `<period>_std_dev`, `<period>_min`, `<period>_max` | standard deviation, minimum, maximum (`<period>_standard_deviation`, `<period>_minimum`, `<period>_maximum`) |
| `<period>_data_availability` | sensor active (`<period>_sensor_active`) |
| `<period>_consensus`, `<period>_custom` | the consensus ranges and custom ranges summaries (`<period>_consensus_ranges`, `<period>_custom_ranges`) |
| `<period>_tir`, `<period>_titr`, `<period>_time_low`, `<period>_time_high`, `<period>_time_critical_low`, `<period>_time_critical_high` | custom time in range, in tight range, low, high, very low, very high (`<period>_custom_time_in_range`, ..., `<period>_custom_time_very_high`) |

## 11. Version policy

| Part | Where the version is set | When it changes |
|---|---|---|
| Integration | `version` in `custom_components/glucocraft/manifest.json` (also the device's software version) | every release |
| Each JavaScript file | its own constant at the top (`GC_STRATEGY_VERSION`, `GCUI_VERSION`, `GCDC_VERSION`, `GCREPORT_VERSION`, `GCDOCS_VERSION`, `GCI18N_VERSION`, `GCSHARED_VERSION`) | one patch step with every change to that file |
| Each panel configuration | the substitution `panel_version` | one patch step with every change; the landscape files take it from the square panel |
| Readings file | `STORAGE_VERSION` in `const.py` | only with a change of the file layout. Only the current version is read: a file of an older version is handled as a damaged header (its body is recovered where it parses as current records, Repairs `storage_recovered`, else the file is kept aside, Repairs `storage_damaged`), and a file of a newer version is neither read nor written (Repairs `storage_newer`); a layout change therefore needs its own conversion code |
| Panel file | `GLU2_VERSION`, `EXT_VERSION` in `panel_feed.py` | only together with the firmware structures in `glucocraft_panel_binary.h`. The display card and the panels read version 3 and later and ignore the parts they do not know, so a later version keeps version 3's header and blocks and adds its own (`gc_glu2_readable`, `parseGlu2`) |
| Configuration entry format | `ENTRY_VERSION`, `ENTRY_MINOR_VERSION` in `const.py` (2.3, the first format) | only with a change of the stored settings' layout, together with its migration step in `__init__.async_migrate_entry`; an entry of a newer format (written by a later GlucoCraft) is refused and not loaded |

All versions stay below 0.1.0 while GlucoCraft is a pre-release. The README's Versions table lists the current
values of the parts; the file and entry formats are listed only here.

## 12. Synthetic data and the edge-case tests

`tools/synth/` makes the test data for the suite and for a test Home Assistant. It does not import GlucoCraft, and
nothing in it is used by the integration. Every command runs from the repository root.

| Module | Does |
|---|---|
| `profile.py` | Fits a profile (characteristics only: time-of-day percentiles per 15 minutes, rate of change, autocorrelation, noise, lows and highs by day and night, reading intervals, gaps, LO / HI and no-reading states) from an entity's recorded history. GET requests only. |
| `example_profile.py` | The artificial example profile, the default everywhere. |
| `generate.py` | The realistic generator: a seeded series from a profile; 1, 5 or 15 minutes, mg/dL or mmol/L, hours to more than 200 days, a time-zone-aware start, modifiers (`stable`, `hypo_prone`, `dropouts`, `dexcom_codes`, ...). |
| `scenarios.py` | The named edge and extreme cases (each with a mg/dL and a mmol/L source), the recorder histories by length (`history`), measurement times off the whole minute (`sub_minute_times`), and `from_series` (any series as a scenario). |
| `sine.py` | Deterministic sine waves: three peaks and three valleys per day, inside each range or across several. |
| `real_history.py` | Reads a real history into a local file (optional; GET only; personal health information). |
| `expected.py` | An independent calculator written from the published definitions and the documented rules; `sine_statistics` gives a wave's closed-form values. |
| `recorder_write.py` | Writes a series into a recorder database with Home Assistant's own models. |
| `live_feed.py` | Sends a series to an entity over REST, one value every N seconds. |
| `common.py` | The pieces the other tools share: units and rounding, the LO / HI texts, the series format (`Series`, which never writes a real history inside the repository) and the connection file (`load_connection`). |

### 12.1 Three data sources

Every source runs through the same comparison: GlucoCraft's real code (history import, storage, statistics, CGM
Report, display units) against `expected.py`, for a mg/dL and a mmol/L source and both display units.

1. **The realistic generator** (`generate.py`). The body is a smooth random process (an AR(2) process whose
   autocorrelation matches the profile's from 5 minutes to 4 hours) on the profile's time-of-day percentiles. It stays
   between 58 and 246 mg/dL unless the profile's own percentiles lie beyond 54 / 250 mg/dL; the excursions below 70 /
   54 and above 180 / 250 mg/dL are injected at the profile's rates, durations and extremes, with short dips that make
   up the profile's remaining time below 54 mg/dL. What the body produces by itself is measured once per profile on
   fixed random streams, so every seed follows the profile (`tests/test_synth_realism.py` states the tolerances).
   The edge and extreme cases are scenarios: long very low and very high runs, LO / HI at both limits per sensor type
   in both units, rapid swings, a flat line, repeated values, gaps from 10 minutes to three days, clock jumps, both
   daylight-saving days, a single reading, no readings, an empty history.
2. **Sine waves** (`sine.py`): `offset + amplitude x sin(2 pi x 3 x t / day)`, every 1, 5 or 15 minutes, with the
   offset and the amplitude in the source unit (`sine.RANGES`: inside each range, and two waves across several). The
   deviation from the offset is what is rounded, so the readings pair up exactly around it: a mg/dL wave's mean is
   exactly its offset, and a wave inside one range spends exactly 100% there. `expected.sine_statistics` gives the
   closed-form time in each range (arcsine), mean and SD (amplitude / sqrt(2)); `tests/test_synth_sine.py` states the
   bounds of the sampling and the rounding.
3. **A real history (optional, local only).** Months of a real CGM entity, read with GET only, one day per request,
   one request at a time:

   ```
   python -m tools.synth.real_history --connection CONNECTION.json --url http://HOST --entity sensor.cgm_glucose \
       --days 200 --out OUTSIDE_THE_REPOSITORY.json
   ```

   Only the key `token` of `CONNECTION.json` is read (and `url` when `--url` is not given). The file is written only outside
   the repository, is marked `"artificial": false`, and no value is printed. The default run never reads it: set
   `GLUCOCRAFT_REAL_HISTORY` to the file to run `tests/test_synth_real_history.py` and the real part of
   `tests/test_history_lengths.py` (a failure names the values that differ, never shows them).

A profile fitted from a real person's history is personal health information as well: keep it outside the repository
(the tools refuse a path inside it); never commit or share it. `GLUCOCRAFT_SYNTH_PROFILE` names one for the optional
realism test against it.

```
python -m tools.synth.profile --connection CONNECTION.json --entity sensor.cgm_glucose --days 200 --out PROFILE.json
python -m tools.synth.generate --days 30 --interval 5 --unit mmol/L --seed 7 [--profile PROFILE.json] --out series.json
python -m tools.synth.live_feed --connection TEST_CONNECTION.json --series series.json --entity input_number.synthetic_glucose
```

#### Testing without a CGM

GlucoCraft's setup offers sensor entities only, so the simplest test source is a sensor whose state `live_feed` sets
through Home Assistant's REST API (`--state-api`): it writes the value with its unit and a measurement time, as a CGM
integration does. Use a test Home Assistant, never one that holds real readings or real devices.

1. In the test Home Assistant, create a long-lived access token (the user's profile, *Security*). Save it in a file
   outside the repository, `TEST_CONNECTION.json`:

   ```json
   {"url": "http://TEST_HOME_ASSISTANT:8123", "token": "THE_TEST_TOKEN"}
   ```

   The token gives full access to that Home Assistant: never use the token of another Home Assistant, and never commit
   the file. The tools never print it.
2. From the repository root, make one day of 5-minute readings and send one every 5 minutes:

   ```
   python -m tools.synth.generate --days 1 --interval 5 --seed 7 --out series.json
   python -m tools.synth.live_feed --connection TEST_CONNECTION.json --series series.json \
       --entity sensor.synthetic_glucose --state-api --every 300
   ```

   The first value creates `sensor.synthetic_glucose`. Leave the feed running.
3. Install GlucoCraft (README, *Installation*) and choose `sensor.synthetic_glucose` as the glucose source.

What to expect in the first hour: each value that arrives is a reading with its own measurement time; the reading
interval (5 minutes) is known after 3 readings; the trend arrow and the 5-minute delta appear after a few readings;
the 6-hour statistics show "---" until the readings cover 70% of the 6 hours (about 4.2 hours with no gaps), and the
dashboard says how much longer each period needs. A feed every 5 minutes or more often raises no `slow_source` notice;
one every 15 minutes or less often does (`--every 900`). A state set this way lasts until Home Assistant restarts; after
a restart, start the feed again. A source written without a measurement time (an `input_number` with the default
mode) that repeats one value for over an hour raises the `source_stuck` notice, as a stopped CGM source would.

`live_feed` sends LO / HI to an `input_number` as 39 / 501 mg/dL (2.2 / 27.9 mmol/L), which GlucoCraft reads as LO /
HI for every source (401 would be HI only for a source that uses the code 401 for HIGH; Documentation, *Readings
Outside the Sensor's Range*).

### 12.2 The recorder history length is a test parameter

Home Assistant keeps 10 days by default, but a recorder can keep any number of days. `scenarios.history(length,
variant, unit)` builds no history, 1 day, 10 days, 30 days, six months and more than six months, each from a midnight
without gaps, with gaps, and with a partial first day. `tests/test_history_lengths.py` checks that the import takes
what exists up to the 200-day retention (never 10 days by assumption), that the oldest stored day is complete, and
that every statistic and report value agrees with `expected.py`. `tests/test_history_lengths_recorder.py` writes every
history into one SQLite database with `recorder_write`, starts Home Assistant's recorder on it and reads each history
back through the import path. It needs the `homeassistant` package: set `GLUCOCRAFT_HA_PYTHON` to the Python of a
virtual environment with Home Assistant installed (on Windows in a short folder path: the package exceeds the
260-character path limit in a deep folder). It takes about seven minutes.

### 12.3 The installation-test Home Assistant

A throwaway Home Assistant OS virtual machine with the simulated source `sensor.synthetic_glucose` (for example a
template sensor on the `input_number` that `live_feed` sets) and no credential of another Home Assistant. Its
automatic updates are off (Supervisor and add-ons), so a rollback to its snapshot gives the same versions on any day;
the recorder's `purge_keep_days` is set per test (default 10). To write a history of N days on it, with core stopped:
generate the series with the default start (`generate --days N`: it ends at or before the moment it is generated,
never later), copy the `tools/synth` folder to `/share/synth` and the series to `/share/series.json`, then, on the
host console (or in the SSH add-on with protection mode off, whose `docker` commands act on the host):

```
set recorder: purge_keep_days to N or more in configuration.yaml
IMAGE=$(docker inspect --format '{{.Config.Image}}' homeassistant)   # the running Home Assistant's image
ha core stop
docker run --rm -v /mnt/data/supervisor/homeassistant:/config -v /mnt/data/supervisor/share:/share -w /share \
    --entrypoint python3 "$IMAGE" -m synth.recorder_write --series /share/series.json \
    --entity sensor.synthetic_glucose --db-url sqlite:////config/home-assistant_v2.db --replace --confirm-core-stopped
ha core start
```

`ha core stop` stops the `homeassistant` container, so the write runs in a new container of the same image (the same
Python and recorder models), with the configuration and the share folder mounted from the host.

`recorder_write` refuses what would corrupt the history: a database of another schema version or without the
recorder's tables, an entity that already has states in the series' span or after it, rows out of time order, a
malformed entity id. A source that already has states (it was fed until core stopped) needs `--replace`: it deletes
the entity's states from the series' first time on, so that the series becomes the entity's newest history; without
`--replace` the write is refused. Roll back to the clean snapshot after each run.

### 12.4 Known findings

A known disagreement is a strict expected failure with its reason, so the run fails once it is fixed: `FINDINGS` in
`test_synth_scenarios.py` and, for measurement times off the whole minute, `test_synth_sub_minute_times.py` (none
at present in either). A low event starts at its first reading below the limit and lasts the seconds its readings
represent, and the report's percentages, mean, SD and the other statistics weigh each reading by its seconds, as the
statistics sensors do (`statistics.compute_period_stats`); `test_synth_sub_minute_times.py` checks both.
