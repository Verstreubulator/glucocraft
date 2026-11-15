# GlucoCraft test suite

Tests for the integration's Python code (`custom_components/glucocraft`), for the texts and helpers of the card files
(`custom_components/glucocraft/www`) and for the example wall-panel configurations. They run without Home Assistant:
`ha_stub.py` stands in for Home Assistant, aiohttp and voluptuous. A small second suite, `tests_ha/`, runs GlucoCraft
on Home Assistant itself (see [Tests on Home Assistant itself](#tests-on-home-assistant-itself)). How the code fits
together is described in [docs/DEVELOPER_GUIDE.md](../docs/DEVELOPER_GUIDE.md).

## Running

From the repository root, with Python 3.13 or later:

```
python -m pip install -r requirements_test.txt
python -m pytest                 # everything
python -m pytest -m "not slow"   # without the PDF renders
python -m pytest tests/test_units.py -k mmol
ruff check .                     # lint (configuration in pyproject.toml)
ruff format --check .            # formatting (line length 120)
npx eslint@9 custom_components/glucocraft/www   # the dashboard files (configuration in eslint.config.js)
```

The workflow `.github/workflows/tests.yml` runs the same on every push and pull request (Python 3.14, Node.js 24,
ESLint installed so that `test_javascript_lint.py` runs): ruff, the suite without the PDF renders
(`-m "not slow"`), and `tests_ha/` on the oldest supported and the newest tested Home Assistant; every night, on a
release tag and when started by hand, the whole suite.

`pytest.ini` (repository root) sets `testpaths = tests`, `pythonpath = tests`, `--strict-markers` and
`xfail_strict = true`, and makes a coroutine that is never awaited an error: a stand-in's
`hass.async_create_task` keeps its coroutines in a `conftest.task_list()`, the test runs the ones it checks, and
the rest are closed when the test ends.

Some tests need files or programs that are optional. Each such test is skipped, with the reason in the summary
(`-ra`), when the file or program is missing:

| Needs | Tests | How to provide it |
|---|---|---|
| `node` on the path | the tests that run the cards' JavaScript | install Node.js |
| `eslint` on the path | the ESLint run of `test_javascript_lint.py` | `npm install --global eslint@9` |
| `pypdf` | the PDF text tests | `requirements_test.txt` |
| `pikepdf` | `test_pdf_tagging.py` (the PDF's structure tree) and the tagged-PDF part of `test_report_layout_and_sources.py` | `requirements_test.txt` |
| `pymupdf` (PyMuPDF) | the 8-point minimum of the PDF text in `test_report_outputs.py`; the empty time-in-ranges bar, the page-1 "insufficient data" line below its caption and no y label over an hour label in `test_report_shown_values_and_texts.py` | `python -m pip install pymupdf` (not in `requirements_test.txt`, so the CI skips these tests, the nightly full run included) |
| The example panel configurations | `test_panel_configs.py`, `test_panel_rounding.py`, the panel parts of `test_entity_ids.py`, `test_optional_features.py`, `test_accessibility_checks.py`, `test_pseudo_locale.py`, `test_disclaimer.py`, `test_no_suggested_uses.py`, `test_panel_contrast_rule.py`, `test_card_shared_copies.py`, `test_limit_readings.py`, `test_published_texts.py` and `test_settings_and_standards.py`; some tests run a smaller check without the folder instead of a skip (in `test_panel_feed.py` and `test_recorder_load.py`, for example) | the folder `esphome/` in the repository, or the environment variable `GLUCOCRAFT_PANELS` set to the folder that holds `glucocraft-panel.yaml` |
| A document in `docs/` | the tests that compare a document with the code | the document in the repository |
| A real glucose history | `test_synth_real_history.py` (the whole history), the real part of `test_history_lengths.py` | `python -m tools.synth.real_history ... --out FILE` (outside the repository), then the environment variable `GLUCOCRAFT_REAL_HISTORY` set to FILE. Personal health information: never in the repository, no value printed, never read by the default run |
| A profile fitted from a real history | the local-profile check of `test_synth_realism.py` | `python -m tools.synth.profile ... --out FILE` (outside the repository), then `GLUCOCRAFT_SYNTH_PROFILE` set to FILE |
| The `homeassistant` package | `test_history_lengths_recorder.py`, the recorder writes of `test_synth_feed_and_recorder.py` and `test_verify_synth_tools.py` | a virtual environment with Home Assistant installed (a short folder path on Windows), and `GLUCOCRAFT_HA_PYTHON` set to its Python (the recorder writes of the other two files need it as the Python running pytest) |

The panel tests never read `secrets.yaml`.

### Running at a given time

The suite passes at any time of day and on any date. A test whose texts name the day of a reading runs on a fixed
clock (`fixed_clock` of `test_settings_harness.py`, noon in Los Angeles, or `ha_stub.set_clock(instant)`); a test
that needs "now" reads `time.time()`, the clock of the integration, never `datetime.now()` or `date.today()`. To check
the suite at a chosen instant, set `GLUCOCRAFT_TEST_CLOCK`: every test then starts at that instant (the clock runs on
during the test, node's `Date` included). The instant has a UTC offset or a time-zone name in brackets:

```
GLUCOCRAFT_TEST_CLOCK="2026-11-03T00:00:30[America/Los_Angeles]" python -m pytest
$env:GLUCOCRAFT_TEST_CLOCK = "2026-11-03T00:00:30-08:00"; python -m pytest   # PowerShell, the same instant
```

The instants worth a run: 30 seconds after and before local midnight, a Monday, the first day of a month and of a
year, the daylight-saving changes (in Los Angeles 2026-11-01 01:30 the second time, 2027-03-14 03:00), and midnight in
other time zones (Europe/Berlin, Asia/Tokyo, Australia/Adelaide). Node uses the computer's time zone (or `TZ`).

## Layout

| File | Covers |
|---|---|
| `ha_stub.py` | The stand-ins (see below) |
| `conftest.py` | Installs the stub; shared constants, fixtures and helpers (`panel_folder`, `repo_doc`, `real_coordinator`, a stored reading `Rd`, the entity registry stand-in `FakeRegistry`) |
| `test_units.py` | `units.py` (rounding halves away from zero, mg/dL and mmol/L in both directions, consensus pairs, formatting), the range entry of the setup screens (`config_flow`), GMI in mmol/mol |
| `test_statistics.py` | `statistics.py` (bands, time weighting, sensor active, GRI, rounding), `change.py`, zone / binary sensor / statistics agreement, the GMI rule |
| `test_statistics_gmi.py` | GMI against the published equations and Table 1 of Bergenstal et al. (2018): % from the mean in mg/dL, mmol/mol from the mean in mmol/L, each rounded once |
| `test_statistics_consumers.py` | One value per rule wherever the period statistics are used: combined bands rounded once, the 70% rule on the unrounded value, the GRI zone, LO / HI in the minimum and maximum |
| `test_statistics_pass_identical.py` | The statistics pass (several range sets at once, bands once per store) gives results equal to the earlier one-set-at-a-time calculation on six months of generated data |
| `test_storage.py` | `storage.py` (GC02 layout and byte order, load rules, damaged files, purge, merge, interval detection), `history_import.py`, reading times (daylight-saving fall-back), the second source, repeated states |
| `test_start_restored_values_and_gap_import.py` | A start of GlucoCraft: a source without its own measurement time that comes up before Home Assistant has started writes no reading (after the start a new value is one); the history import at setup decides LO / HI with the device the source's current state shows; a store with readings gets the recorder's readings measured after its newest one at setup, with no stored reading changed and no value written again as the entity came up; none while the clock is behind or without the recorder |
| `test_expiry_and_timer_guards.py` | Three guards of the core: an expiry entity's number that gives no representable time (an epoch in micro- or nanoseconds, `inf`, a huge time left) is unrecognized and never stops setup; a failing source-notice check never skips the timer's save and entity refresh; and without Home Assistant's recorder the history import is skipped quietly (no error, no Repairs notice) |
| `test_data_path.py` | The data path: LO / HI detection and limits (`limits.py`), the one gap rule (`gaps.py`: 1-, 5-, 15-minute and mixed data, outages), statistics windows identical to filtering, storage version 3, retention 200 days (fixed, whole days), the 6-month report, laboratory A1C without CGM comparison, CSV export (`export.py`), the Repairs notices (slow source, sensor cap), settings and texts |
| `test_input_guards.py` | Small robustness rules: a non-ASCII digit is no number of days for `/api/glucocraft/data?daily=N` and no stored notice delay, the Glucose ranges screen opens on a stored range of the wrong type, only the export's own file names count as exports, one weight per reading in the statistics |
| `test_clock_and_sources.py` | A wrong system clock never deletes readings; GlucoCraft's own entities are never a source; the history import's measurement times; the storage notices; the log of the source in use (one warning when it stops delivering, one info entry when it delivers again, never repeated) |
| `test_clock_repair_flow.py` | The fix flow of the Repairs notice about readings later than the system clock (`repairs.py`): nothing is discarded without the ticked box; a confirmed discard discards, closes the notice and starts the catch-up; while GlucoCraft is not loaded the flow says so (`not_loaded`), never reports a success, and the notice stays open; the abort reason has a text in `strings.json` and `translations/en.json` |
| `test_intake_edges.py` | The edges of the intake: impossible measurement times, the repeated fall-back hour in the history import, the live 24-hour rule against the clock and the newest stored reading, the log of refused readings (once per source and kind, the role only), the warm-up end, a readings file of a newer version, the report cache key, the reading lock of the statistics pass and the CSV export, the boundaries of the clock margin, the 5-minute change, the rounding rule and the reading interval; no log line names an entity id |
| `test_test_clock.py` | The suite's clock: `time.time()`, the stub's `dt` and node's `Date` show one instant, `ha_stub.set_clock` and `GLUCOCRAFT_TEST_CLOCK` (an offset or a time-zone name), the harness's fixed clock at noon; just after midnight the newest reading is of the day before (the daily summary gives its day, the spoken summary "yesterday") |
| `test_report.py` | `report.py` (the readings' seconds cut at each quarter hour and the minute grid of the display bins, in half-hour and 45-minute time zones, 23- and 25-hour days, monthly summary, hypoglycemia events, laboratory A1C), `report_pdf.py` (PDF labels, IPP), `regional.py` |
| `test_report_calculations.py` | The report's calculations, each with a case that tells the right rule from a plausible wrong one: events counted once where they start, the event level, daily profiles on the local clock, combined bands rounded once, one GMI and GRI rule, the 70% rule before rounding, weekly windows, LO / HI in the hourly log and the event list, the 4-week direction, the weekly statistics' 14-day GMI |
| `test_report_outputs.py` | The report outputs: no PDF text below 8 pt, LO / HI in the PDF's hourly log, the printer address without user name and password, the report page's 14 px minimum, its GMI in mmol/mol and its line breaks from `daily_breaks`, the wording of the color key, the date-format setting |
| `test_report_daily_breaks.py` | The report's daily profiles break the line at a data gap between 10-minute slots |
| `test_cgm_report_sections.py` | The CGM Report's sections: high glucose, data quality, the 26-week weekly trend, the optional appendices, the cited context, the report options |
| `test_report_layout_and_sources.py` | The CGM Report's glucose sources (the page header and About This Report, the share of the second source from the source byte, the clause for a second source without measurement times), the full report's hypoglycemia events by period, the lowest and highest value (LO / HI) of the Longer-Term Overview, and the section layout (caption, data, Notes; Notes continued on the next page; About This Report keeps the general method), in the PDF and on the report page |
| `test_report_time_per_day_and_durations.py` | The CGM Report's time per day beside the percentages (from the unrounded share, page 1 and the overview, page and PDF, read in words), durations in words in running text and the symbols only in the event summary table with their key, the comparison only when both periods have 50% sensor active, the conditional custom-appendix and laboratory sentences, the untimed second source, the monthly summary's heading |
| `test_daily_chart_values.py` | The Custom Ranges daily chart's values: time-weighted daily means with the gap rule and the stored lowest and highest readings (`report.daily_values`), `/api/glucocraft/data?daily=N` in the display unit, the chart drawn from them |
| `test_report_shown_values_and_texts.py` | The CGM Report's shown values and texts: the custom appendix's value beside its goal, the report page without the last 14 days, an empty time-in-ranges bar, the events heading kept with its table, the y labels clear of the hour labels, warm-ups under a minute, the laboratory A1C unit on the page, the overview's consensus targets, the printer's reasons, the print notification of a failed PDF, the cache key of the custom appendix, the 70% condition cited and labeled |
| `test_report_api_and_dashboard_complaints.py` | The CGM Report, its HTTP API and the registration of the cards and the dashboard: a day's sensor active shown beside the 70% rule (one decimal, two next to the rule), a warm-up ended by the 24-hour rule named as such, `full` and `color` read as 0 / 1 (also true / false, on / off, yes / no) and any other value refused with 400, a PDF copy that cannot be kept logged, a failed resource update a failed registration, only `/glucocraft/` and `/glucocraft_v/` resources GlucoCraft's, a dashboard that cannot be registered the Repairs notice `dashboard_failed` |
| `test_report_pdf_text.py` | The lowest and highest values and each event's lowest value passed as stored (rounded once, in the display unit); ✓ / ✗ written as words in the PDF; the PDF's title, author, subject and language, the structure tree and no PDF/UA claim |
| `test_pdf_tagging.py` | The tagged PDF (pikepdf): language, title and marking; every page's content tagged or an artifact; headings from H1 without skipped levels, in the order of the page; every figure with an alternative text that states data; every table with scoped header cells and even rows; every section read in the order caption, data, Notes; embedded fonts with a Unicode map and a glyph for every character of the report (veraPDF's whole PDF/UA-1 profile: `tools/check_pdf_ua.py`) |
| `test_pdf_layout_and_regional.py` | Letter and A4 layouts, decimal separator, A1C / GMI unit, first day of the week, the disclaimer in About This Report, the License as the last section, one footer line per page |
| `test_notification_text.py` | `notification.py` (the summaries, the forwarding to the phones chosen for them, the last line of every summary), `expiry.py` |
| `test_no_current_data.py` | The one no-current-data system: the display card follows GlucoCraft's flag (and its own count of the age), never the status; without a current reading the words in the value's place, never a number ("Sensor warming up" during a warm-up, else "Sensor expired" or "No current data") with the last reading and when it was taken under them (its age in minutes, hours or days, LO / HI, none without any reading), no zone word, "---" for the trend and the 5-minute delta, the reading's age in the status bar, and the throb of all three words (off with the setting, stopped by reduced motion; the text description states the words first and does not change with the throb); the card's one size on every page and in every state; after the stated expiry a current reading in its zone color with its trend; the trend gate; the optional no-data notice (off by default, once per outage, the delay, removed when a reading arrives, the phones independently, no repeat after a restart, no glucose value) |
| `test_phone_message_log.py` | A failed message to a phone (the summaries, the no-data notice, the delivery test) on a stand-in of Home Assistant's service registry: each notify action is waited for, the log names the phone by its position and the error's class only (never its webhook id, its device name or the message text), the other phones still get the message; saving the settings does not wait for the delivery test |
| `test_notification_wording.py` | Summaries never call a stored value "current", no warning signs in the summaries, no Markdown to phones, no glucose value in the log, only the delivery check named a test |
| `test_notification_format.py` | The last line of every summary, in Home Assistant and on the phones, the decimal separator and A1C / GMI unit in the summaries |
| `test_disclaimer.py` | The disclaimer: `const.py` `DISCLAIMER`, and the same words in every place that shows it in full (README, CONTRIBUTING, the catalogues, the panels, the PDF); the one-line texts; the setup's first screen with no field; no Terms of Use and no acceptance; operational notices with nothing added |
| `test_pdf_wrap.py` | `report_pdf.py` line wrapping: a word wider than its column gets no empty line before it, and a bold lead wider than its column stays bold |
| `test_contribution_terms.py` | The terms of a contribution: the License paragraph of CONTRIBUTING.md word for word, and no contributor agreement, sign-off or agreement checkbox in CONTRIBUTING.md or the pull-request template |
| `test_config_flow_options.py` | The setup screens and the Configure menu: the menu order, each screen saving only its own fields, the collapsed sections flattened (a cleared second source stored as None), the weekday stored as an int, the weekly pages offered only with custom values, the optional features stored as off at setup, no setting of the removed glucose notifications, the keys and values of a full setup unchanged, a label and an explanation for every field; the international settings, `services.yaml` |
| `test_settings_validation.py` | The report options of the print action, the range order and nested goals per field, the weekly statistics on the enough-data rule (a week counts only with sensor active at least 70% of it), the sensor-active label |
| `test_regional.py` | `regional.py`: first day of the week, decimal separator, A1C / GMI unit, paper size, number formatting, A1C conversions |
| `test_trend_and_configuration.py` | Trend values cleared while the data are not current; the `configuration()` attribute |
| `test_settings_and_standards.py` | The settings screens, the display colors, and the Home Assistant / HACS standards (translations, selectors, the panel webhook, the HTTP views, the device); the license line as the first line of every source file |
| `test_settings_backgrounds.py` | The graph range colors (one table with the CGM Report's colors), the card and panel backgrounds and their black or white text, invalid stored values, the contrast note, the Display card and wall panels screen's fields and saving |
| `test_settings_harness.py` (helper) | The harness of the settings test matrix: `build()` runs the real coordinator from a stored config entry and collects every output (`Run`: the entities, the Configuration attributes, the panel feed, the HTTP API, the summaries, the CGM Report, the CSV export; `Run.reload()` sets up a new coordinator on the stored data, as an options save does); `BUGS` lists known defects (none at present) |
| `test_settings_inventory.py` | The settings inventory of the matrix: every stored setting, its values, its default and the outputs it affects; every setting key of `const.py`, every key the screens store and every configuration attribute the cards read is covered |
| `test_settings_full_runs.py` | Every setting at its default, and every setting changed at the same time, each in both display units, checked on every output |
| `test_settings_invalid.py` | Invalid stored values (unknown texts, wrong types, missing keys, None): every setting falls back to its default without an error, on every output |
| `test_settings_display.py` | The display settings (display unit, display card, the zone word and *Trend shown as*, zone colors and their reset, the weekly pages' range set, the no-current-data limit) on every output; a unit change reaches the range binary sensors at once (after the reload of an options save and from the entity settings) |
| `test_settings_notifications.py` | The notification settings: the summaries and their times and days, the phone targets, types and test, no notification from a glucose value (also with the settings of an earlier version stored); times at 00:00 and 23:59 and across daylight saving |
| `test_settings_ranges_goals.py` | The six ranges and the eight goals at their bounds and defaults in both units, the edge cases, and pairwise combinations with the weekly pages, the display word and the unit (Custom ranges and goals on); a range, sensor-limit or optional-feature change reaches the binary sensors at the reload, without a new reading |
| `test_settings_regional.py` | The regional settings (time and date format, first day of the week, decimal separator, A1C / GMI unit, paper size), "auto" for ten countries, and combinations with the display unit |
| `test_settings_sources.py` | The source settings (glucose source, sensor expiry, second source, time attributes, sensor limits per source) on the coordinator, the entities and every output |
| `test_settings_other.py` | The report printer, the dashboard in the sidebar, the wall-panel address (created on request, kept, made anew) |
| `test_docs_and_settings_texts.py` | The published documents and the settings texts: one conformance wording, the Documentation's list of Repair notices (every Repairs issue named), the Documentation's Motion and Timing, no wording about earlier versions or retired terms, the Glossary's newer terms with their links, one description of the history import, the printer certificate caveat in every printing text, and each settings fact once (the sensor-limit explanation per screen, short field texts, the facts moved out present in the Documentation) |
| `test_range_sensor_limits.py` | The very-low and the very-high threshold within each source's sensor limits: the ranges screen's field errors in either unit, a stored threshold used at the limit with a Repairs notice, a LO reading always very low and a HI reading always very high |
| `test_sensor_limit_ranges.py` | A sensor limit never puts the default ranges in place of the user's ranges: when the source or its limit changes after the ranges were saved, only the very-low (or the very-high) limit moves to the sensor's limit and the other five stay as saved; when the very-low limit then equals the low limit, the low band is simply empty (no time low, never a negative time, a LO reading still very low) on the zone, the binary sensors, the statistics, the summary, the report, the panel file and the graphs' band key. Sensor first, then the ranges: in Configure, a sources change whose limits no longer fit the saved ranges opens the Glucose ranges screen with its field errors and is saved together with the ranges only once they fit. A saved set out of order (edited by hand) still uses the default ranges |
| `test_entity_defaults.py` | Which entities start enabled: the binary sensors that no page, card or panel reads start switched off; existing installations keep their state |
| `test_diagnostics.py` | `diagnostics.py`: what the downloaded file contains, and that it never contains a glucose value, a source's entity id, a notify action, the printer's address or the webhook id |
| `test_frontend_guards.py` | Small rules of the frontend: no graph drawn before its texts are loaded, the display card keeps its design width after `setConfig`, the source name cleared while no source is known, one CSV export call, the GMI axis spread from the GMI rule, no invisible characters in the www files, escaped report values |
| `test_quality_scale.py` | `quality_scale.yaml`: every rule of the four tiers recorded in hassfest's format, every exemption and to-do with a comment, and the records a file can confirm agree with the code |
| `test_text_outline.py` | The setting *Outline text* and the display card's 1-pixel text edge (off by default); the panels' side is in `test_panel_configs.py` |
| `test_accessibility_checks.py` | The release checks of `docs/ACCESSIBILITY.md` on the cards, the dashboard and the CGM Report page: the default graph range colors, contrast (WCAG 2.2) of every pair the display cards draw, the default palettes under the three color-vision deficiencies, text alternatives, names and keyboard access, the text sizes |
| `test_spoken_summary.py` | `spoken.py` and `sensor.glucocraft_spoken_summary`: the sentence in both units and for every status, in the rule-4 order, LO / HI and trends in words, the time of the reading and never an age, its length, disabled by default without statistics, made again only when one of its inputs changes, the same words as the cards |
| `test_display_word.py` | The zone word under the value in every setting, the setting *Trend shown as* (arrow or word) and their attributes |
| `test_maintenance_status.py` | The Maintenance status (`maintenance.py`): what is counted (an offline wall panel, an open Repairs issue, a failed CSV export) and what not, the one published source, the Start page's Maintenance button and the Maintenance page's hero card |
| `test_change_over_time_gri_and_unit.py` | Change Over Time at the sensor's limit, the GRI zone descriptions, the display unit, the PDF's texts |
| `test_entity_ids.py` | `entity_ids.py`, `sensor.py`, `binary_sensor.py`, `__init__.py`: every entity id follows its display name; names, ids and attribute keys in the consensus terms; no rename or clean-up pass for earlier ids; the Data history states and its GMI availability (70% rule); no consumer file (integration, cards, translations, README, the reference documents, the example panel configurations where available) refers to an earlier id |
| `test_entity_units_and_recorder.py` | The glucose sensors' display unit and precision (no suggested unit; `unit_registry.keep_display_unit` writes the entity settings at setup, never over a unit chosen there), the attributes the recorder leaves out, a second source equal to the primary refused, a failed print or export as a translated error, the notify actions read from the notify domain only, and a wall panel whose device is deleted forgotten with its offline notice removed; the per-statistic percentage states rounded once to one decimal |
| `test_icons_and_categories.py` | `icons.json` (every entity and action, the state icons, no icon in the code) and the entity categories (exactly the technical entities are diagnostic) |
| `test_optional_features.py` | The two optional features (Custom ranges and goals, Separate statistics entities) in every combination: the entities created (28 for a new installation, 134 with both on), the ranges, goals, zone and feed ranges in effect, the refused custom appendix, the dashboard's pages; off and on again with the same entity ids; the cards and panels reading only the period summary sensors, and the Custom Ranges page drawn from them alone in both units |
| `test_install_and_removal.py` | One installation per Home Assistant, the manifest, the HACS / hassfest workflows and the test workflow, the actions, unloading, removal without a trace (*Delete everything*) or with the readings kept for a reinstall (the default; `kept.py`: kept, the setup's question, adopted, deleted) |
| `test_kept_files_damaged.py` | Damaged files among the data kept for a reinstall (`kept.py`): looking for kept readings at the setup changes nothing in the folder; a kept readings file that cannot be read is adopted and reported at the first start as a damaged readings file; a kept laboratory or sensor-event list that cannot be read is renamed aside with a warning, never deleted, and deleting the kept data removes that copy too |
| `test_csv_import.py` | The CSV import (`glucocraft.import_csv`): the round trip export, delete, import; both units and both decimal separators; stored readings and laboratory results win; the retention; the file named in `glucocraft_exports` only; the laboratory export with both units and the entered unit; the readings file read one row at a time |
| `test_javascript_lint.py` | `eslint.config.js` (the files as ES modules, ESLint's recommended rules as errors, line length 120 and no nested ternary as warnings) and, where ESLint is installed, its recommended rules equal to the configuration's and no ESLint error in the dashboard files |
| `test_limit_readings.py` | Readings at the sensor's limit, with numbers worked out by hand: a reading at the sensor's limit gives no value and still counts as a category: the statistics, a period with limit readings alone, the trend, the current reading's state, the panel file's GCL2 trailer, the chart data, the CSV and its import, the report, the notifications, the warning line on the display card and on the panels, and no text that says such a reading is counted at the limit |
| `test_put_back_notice_persistent.py` | The notice that GlucoCraft put an entity id back: persistent (it stays, also across a restart, until it is dismissed), not closed at setup, and replaced by a later one that lists every id put back since the setup; the entities give their unique id and their original id through public properties |
| `test_enough_data_rule.py` | The enough-data rule, with numbers worked out by hand: a period's statistics only with sensor active at least 70% of the period, compared before rounding (69.995% does not meet it); the statistic sensors unknown and the summaries and the statistics API with `enough_data` false, `period_seconds`, `covered_seconds` and every value null; the panel file's zeros; the summaries' not-enough-data line; the weekly windows |
| `test_sources_limits_and_notices.py` | The sources, the limit stretches and the notices, with numbers worked out by hand: the stuck-source notice (12 identical readings over at least 60 minutes from a source without its own time), the warm-up's end after 24 hours and its no-data notice from 3 hours, the current stretch at the sensor's limit and the panel file's GCR1 block, the Configuration sensor's `attention`, a reading time more than 5 minutes ahead of the clock, a report period that does not exist refused with 400, the diagnostics' redaction of the custom ranges and goals, the expiry entity, the sensor limits set by hand and the phones without anything to send |
| `test_notice_fault_and_print_copy.py` | A fault in the source-notice checks is logged once per episode as a warning with its traceback, then at debug level; a print whose PDF copy could not be kept is an error of the action that names no kept file (`print_failed_not_kept`), one with a kept copy names it (`print_failed`) |
| `test_storage_clock_far_behind.py` | A clock more than a year behind the stored readings: readings later than a year after the clock that end the file in time order load as a clock that is behind, not as damage (no copy kept aside, no full write), while damage among the readings is still found |
| `test_wall_panel_notice_registry.py` | The wall panels in Home Assistant's device registry: read from the registry's values, a GlucoCraft panel is a device of the manufacturer "glucocraft" other than GlucoCraft's own, and the square panel's model is square |
| `test_i18n_missing_key_once.py` | A key that no catalogue has: the key itself is returned every time, and it is logged once per key |
| `test_report_enough_data_and_limit_marks.py` | The CGM Report, page and PDF alike: a period's values only with at least 70% sensor active, with one note per period and how much more data it needs; the LO / HI marks along the bottom or top edge of the daily profiles and the glucose profile; a report period that does not exist refused; the mmol/L axis labels in one format per axis, and the report's smaller rules |
| `test_panel_feed.py` | `panel_feed.py`: GLU2 v3, GCX1, GCR1, GCM1 and GCL2 checked against the panel firmware's structures (`glucocraft_panel_binary.h`), offsets, byte order, delta file, buffer budget |
| `test_panel_configs.py` | The example panel configurations and their headers, read as text: encryption, bounds checks, colors, the zone word and *Trend shown as* (arrow or trend word), the About pages (with the copyright line), the landscape files equal to the generator's output; the logger at INFO with no reading time or value at that level; the generated panel texts current and holding the disclaimer, without the fonts |
| `test_panel_rounding.py` | The panels round each value once as GlucoCraft does: the 5-minute delta and the 6-hour statistics before the first file read from the attributes' text as doubles, the last reading saved across a reboot (its time, its value and its LO / HI mark, shown under the words without a current reading, never in the value's place), one named glucose bound (`gcu::GLU_MAX`) |
| `test_card_shared_copies.py` | The cards' shared module `glucocraft-shared.js`: each rule written once, its rounding, two-decimal rule, units and mmol/L rounding against `units.py` and the panels, its GLU2 parser against `panel_feed.py`, and each card file loading on its own with only the language and shared modules |
| `test_dashboard_cards.py` | The dashboard cards and the display cards: the history graphs redrawn only when their data change, a reload that keeps the display unit and the ranges, Change Over Time without a sign at zero, parity of the display card's weekly and 6-hour pages and its mmol/L rounding with the panels, spoken signs and the names of the pages card's rows |
| `test_frontend_edge_states.py` | The dashboard cards in their edge states: GlucoCraft reloading (the 15-minute limit, never "None"), reading times ahead of the clock (no future time, no age), a failed download logged once per reason, a mistyped card option named on the card, the Documentation's focus after a redraw, and the rules the card files take from the shared module (consensus ranges, defaults, screen-reader CSS, axis labels, the version line) |
| `test_display_rounding.py` | Every displayed number rounded once from the unrounded value: the 5-minute delta published unrounded and rounded once on the cards (20.5 -> "+21", -17.38 mg/dL -> "-1.0" mmol/L), doubles and file float32 values each by their own allowance, differences of the values as shown with one earlier window per Change Over Time table, GMI in mmol/mol from the mean, the dashboard's stored limits as shown, the banners' own clock, the defaults and constants of the dashboard files |
| `test_dashboard_card_states.py` | The dashboard cards while a file loads or fails ("loading", a failed download, the time of an older file), the band key's spans, the Statistics card's minute redraw, one card-picker description per card and its options in the guide |
| `test_frontend_consistency.py` | The card files against the Python side: the disclaimer, the gap rule (compared with `gaps.py`), the shared texts and settings, retired wording; the dashboard cards' consensus targets in one table equal to `const.py` |
| `test_frontend_i18n.py` | The cards' translation capability: the catalogues' keys and placeholders, every key the card files use, the language module (English fallback, lookup order, plurals), the Documentation's sections and citation tokens; every key of `frontend.json` used |
| `js_i18n.py` (helper) | The language catalogues for the tests that run the cards in node (`i18n_prelude()`), and the English texts by key (`catalogue()`, `text()`, `documentation_text()`) |
| `node_runner.py` (helper) | Runs a test script in node after the language catalogues and the shared module (`run_node()`, `node_json()`), and skips a test when node is not installed (`NODE`, `needs_node`) |
| `test_pseudo_locale.py` | The test language `pseudo` (`tools/i18n/make_pseudo.py`): the same keys, markup and placeholders kept, every word accented, marked and longer; never used for a user; every backend surface and the PDF under pseudo without an escaped text |
| `test_i18n_backend.py` | The backend's language catalogues (`i18n.py`, `i18n/<language>/backend.json` and `report.json`) and `strings.json`: every key the code uses exists, with exactly its placeholders; flat dotted keys; English fallback key by key; the plural rule; `strings.json` equal to `translations/en.json`; every entity and enum state named; the catalogues served below the versioned path |
| `test_catalogue_duplicates.py` | One key per concept: every group of keys with the same English text in `backend.json`, `frontend.json` and `report.json` is listed with its reason (DEVELOPER_GUIDE section 2); a new one fails |
| `test_i18n_english_output.py` | The English output of every backend surface (notifications, the spoken summary, the CSV headers, the settings texts made in Python, the panel feed's texts, the entity names and states, the CGM Report data and PDF) compared with `snapshots/english_output.json`; after an intended change of an English text, write the snapshot again with `GLUCOCRAFT_WRITE_SNAPSHOT=1` and review its difference |
| `test_no_suggested_uses.py` | No published text suggests a use for GlucoCraft or its entities (alarms, watching another person, forecasts, treatment, reports for a doctor, automation examples, relying on it), the allowed neutral facts pass (and the pull-request requirement of CONTRIBUTING.md that names what GlucoCraft does not provide), and GlucoCraft's notifications are never called alarms or alerts: `LANGUAGE_STYLE.md` rules 24 and 26 |
| `test_no_text_in_code.py` | No user-visible text in the Python and JavaScript code (a documented allow-list per entry), and the key states shown by their names |
| `test_one_wording.py` | One wording per topic (`LANGUAGE_STYLE.md` rule 25): the statement *No high or low glucose notifications* is the same in the Documentation, the README and `SYSTEM_DESIGN.md`; *Sources not supported for live display*, the three essentials and the after-setup check list are the same in the Documentation and the README; the earlier wordings of these topics are gone (the paragraph about the regulators among them); every copy of a canonical sentence compared with its home, read at test time |
| `js_literals.py` (helper) | The string and template literals of a card file, and the words in them that a person would see or hear |
| `test_custom_ranges_differences.py` | The Custom Ranges page's differences box, the goals, the report's color key, no re-arm rule text left, American spelling |
| `test_documentation_rendering.py` | The rendered Documentation page: every section link written with the title of the section it opens; the decimal separator setting never rewriting a citation locator; the Colors table's in-range rows and whole mg/dL values |
| `test_dashboard_documentation.py` | The cited-context texts on the dashboard and in the Documentation, "What GlucoCraft Leaves Out, and Why", the color keys, the data-quality warning, DOI-only references |
| `test_dashboard_explanations.py` | The dashboard's explanations against what each card shows: the Consensus Ranges headline's comparison period, the ‡ mark and the units of Change Over Time, the measures without a goal on Custom Ranges, the graphs' spoken summaries, durations in words, the GRI components of Today, the headline cards that open Home Assistant's details, the settings listed on Settings Status |
| `test_translation_texts_no_html.py` | No text of `strings.json`, `translations/en.json` or the action fields of `services.yaml` matches the expression Home Assistant's translation check refuses as HTML (a placeholder in angle brackets) |
| `test_terminology.py` | The CGM Report's name, the range set of the weekly pages, other product names only where needed, the sensor-limit reasons, the user-visible keys, the PDF file names |
| `test_published_texts.py` | The published documents and catalogues: other products named only in the places of LANGUAGE_STYLE rule 20, the 12 px exceptions as one list and as the panel configurations draw them, the font license notice with its Reserved Font Names, abbreviations spelled out at their first use, percentages without a space, the screen-reader guide's quoted texts, the places of the wall-panel address |
| `test_references.py` | APA 7 citations and references: every in-text citation of the Documentation, the PDF and the README has an entry and every entry is cited; the PDF's `report_pdf.REFERENCES` and the README's References are the Documentation's `REFS` entries word for word, in APA order; quotation locators |
| `test_markdown_references.py` | APA 7 citations in the published Markdown documents of `docs/`: a document with its own reference list (References, Reference list, Sources consulted) has an entry for every author-date citation it makes; a document without one carries the line "Full references: Documentation > References" and cites only works of the Documentation's References; the check catches a wrong year and a work without an entry |
| `test_accessibility.py` | Spoken units and comparison signs, the reading order of the text descriptions, the page notice, keyboard access, the focus kept across redraws, links with addresses, the choice buttons' state, status messages, the Documentation's headings, the PDF's actual texts and bookmarks, the narrow card layouts |
| `test_synth_scenarios.py` | Every edge-case scenario of `tools/synth/scenarios.py` (range limits, LO / HI per sensor limit, the gap rule, 15-minute data, lows across midnight and the period start, both daylight-saving days, the 14-day and 70% rules, one and no readings, duplicates, out-of-order and unavailable states, a second-source takeover, retention beyond 200 days, the extended-high boundaries, long very low and very high runs, rapid swings, a flat line, repeated values, gaps of every length, clock jumps, an empty history) through GlucoCraft's real code, compared with the independent calculator `tools/synth/expected.py`, for mg/dL and mmol/L sources and both display units |
| `test_synth_expected.py` | The independent calculator against hand-computed tiny cases |
| `test_synth_generator.py` | The synthetic-data generator and the profile fit: seeded determinism, units and precision, intervals, lengths, LO / HI, modifiers; no fitted profile in the repository |
| `test_synth_feed_and_recorder.py` | The live feed (against a local HTTP server) and the recorder writer's rows; writing a SQLite recorder database only where the `homeassistant` package is installed |
| `test_synth_realism.py` | The realistic generator against the profile it is built from: excursion rates, durations and time beyond each limit by day and night, and the time above 180 per seed, within the stated tolerances (a profile fitted from generated data; optionally a real fitted profile); its parts (the AR(2) body, the excursion shape, the body's bounds, the seed-independent calibration) |
| `test_synth_sine.py` | The sine waves of `tools/synth/sine.py` (three peaks and three valleys per day, inside each range and across several, every 1, 5 and 15 minutes, both units): the calculator against the closed-form values within the stated bounds, and GlucoCraft against the calculator exactly |
| `test_synth_sub_minute_times.py` | Measurement times off the whole minute: the low events start at their first reading below the limit, and every value of the report and the statistics sensors agrees with the calculator, in both source and both display units (the report weighs each reading by its seconds) |
| `test_synth_real_history.py` | The real-history tool (GET only, one day per request, never inside the repository, no value printed, only the token key read) and, optionally, a real history through GlucoCraft against the calculator |
| `test_history_lengths.py` | The recorder history length as a test parameter: no history, 1, 10 and 30 days, six months and more, each without gaps, with gaps and with a partial first day, both units: the import takes what exists up to the 200-day retention, the oldest stored day is complete, and every value agrees with the calculator |
| `test_history_lengths_recorder.py` | The same histories written into a real Home Assistant recorder database and read back through the import path (only with `GLUCOCRAFT_HA_PYTHON`; slow) |
| `test_verify_hand_cases.py`, `test_verify_synth_tools.py`, `test_verify_scenario_masks.py` | The verification of the testing tools: hand-worked cases for every metric and rule, unreasonable inputs to every tool, and the disagreements a known finding may mask |
| `test_rounding_rules.py` | The rounding rules of every backend text: each value rounded once from the unrounded value (the percentages and the CV unrounded up to every display), no negative zero, and a value next to a rule it is compared with on its side of the rule (`units.shown_against`) |
| `test_rule_boundaries.py` | The published rules at their exact boundaries, on both sides: the CGM Report's 70% rule in the period, the custom appendix and the laboratory comparison (exactly 70.0%), a custom goal on the unrounded value, the laboratory window of 90 local days and the oldest day kept, the end of a level 2 event (69.9 mg/dL does not count), an interval of exactly the gap rule, Change Over Time's 6 hours, single-reading windows and hours of data; the code 401 (HIGH) in the independent calculator |
| `test_time_zones.py` | Whose clock each surface uses: the CGM Report's days and night in Home Assistant's time zone, the cards' clock times in Home Assistant's time zone as well, also in a browser in another zone (Tokyo; the browser's own zone only before it is set), and the texts that state the one clock |
| `test_developer_docs.py` | docs/ENTITIES_AND_API.md names every Repairs issue and every persistent notification id the integration creates |
| `test_docs_match_code.py` | The documents keep up with the code: every entity (the naming rule giving exactly the entities created) in docs/ENTITIES_AND_API.md and in the Documentation's Entity Names, every attribute key the sensors publish, every Repairs issue with its translation, its issue id and whether it can be fixed, every action field with its options and default, every HTTP endpoint and query parameter, and the defaults and the retention the documents state in words equal to the code's |
| `test_recorder_load.py` | What makes the recorder write a row: the 14 period summary sensors' state rounded once to one decimal (attributes exact), no attribute that follows the clock, no new state of the Status, No current data, Data history, Configuration and Active source entities from a statistics pass without a new reading, and no display reading an attribute that no longer exists |
| `test_report_and_summary_rules.py` | The CGM Report's numbers and the summaries' comparisons: one sensor-active formula on every page, band percentages exact at 4.00%, percentages beside a target on its side of it, differences of the values as shown, the summaries' 14 / 90-day comparison only with enough data, a custom GMI goal in mmol/mol, extended high-glucose events at the period start and at a gap, one table of goal directions and consensus zones |
| `test_dashboard_text_card.py` | The texts of the dashboard pages and the Documentation as the user sees and hears them: every text card is GlucoCraft's own text card (link color, focus ring, no text below 14 px, the language, tables with a name, header cells and a scroll region), and every ARIA role and state of the dashboard is named in `docs/ACCESSIBILITY.md` |
| `test_marketplace_rules.py` | The rules of the Home Assistant Marketplace and of the HACS action that the repository's own files decide (`hacs.json`, `README.md`, one integration folder named as its domain, the manifest) |
| `test_panel_contrast_rule.py` | Black or white on a color: one rule on the wall panels, the display card and the integration (the panels' status bar and Outline text edge, read from their source and evaluated) |
| `test_renamed_entities.py` | An entity whose id was changed: a change made while GlucoCraft runs is undone and the notice `entity_ids_put_back` says so, the Repairs notice `entity_ids_changed` for the rest, its fix flow and an original id another entity has, and the display card never green without a value. |
| `test_edge_case_decisions.py` | The rules for the edge cases of sources, settings and records: a source's new entity id followed, the notices for a source in the wrong unit, a bad measurement-time attribute and a source too fast to add readings, a learned limit forgotten with its device, the core configuration published at once, the minimum and maximum rounded, a badly formed time and an impossible printer address refused, a note for shared colors, and "keep" saying that the settings are not kept. |
| `test_report_and_card_accessibility.py` | Accessibility and wording rules of the CGM Report page and PDF, the display card and the dashboard cards: the Other groups sentence in the report's Notes, the charts drawn at the width of their content box, the light-theme contrast figures |
| `test_shared_rules_and_tidy.py` | Rules written once and the small behaviors of the cards, the report and the statistics records: black or white text on a color, when the headline card is drawn, the mark of the source in use, differences of values as shown |
| `test_wording_forms.py` | One form for recurring wording (`LANGUAGE_STYLE.md` rules 3, 9 and 25): ">" in menu and section paths, the GlucoCraft-rule sentence, several citations in one parenthesis in alphabetical order |
| `test_surface_fixes.py` | The edges of the setup, the actions, the HTTP API, the CSV export and the removal: only known keys saved, registry entries of entities not created removed at start, unrounded glucose values in /data and /stats, LO / HI in the CSV export and import, the removal deleting only GlucoCraft's own files, Repairs notices for a failed card registration and history import, every notification id and store in the removal's lists |
| `snapshots/` | `english_output.json`, the English output that `test_i18n_english_output.py` compares with (rewritten only with `GLUCOCRAFT_WRITE_SNAPSHOT=1`) |

## Tests on Home Assistant itself

`tests_ha/` runs GlucoCraft on a real Home Assistant with its test harness, `pytest-homeassistant-custom-component`
(Home Assistant's recorder on an in-memory database, its flow manager, entity registry, webhook component and
frontend). It covers what the stand-in cannot model: the setup flow (the disclaimer screen, a missing
source, the display unit from the source, a second installation aborted), every Configure screen saved as shown, the
entity set (28, and 134 with both optional features on), the glucose sensors' unit and Home Assistant's display
precision in both display units and after a unit switch, unload and reload, removal without a trace, the
wall-panel address (GET only, the GLU2 file, a new address replacing the earlier one), the no-current-data flag with
an expired sensor that still delivers, the stored no-current-data limit read within 5 to 30 minutes, the Maintenance
status, the wall-panel notices (added, offline, back, deleted), the GRI from 14 days only, the dashboard through a
reload, a disabled entry and its setting, the CGM Report endpoints, the setup's refusal of a second source equal
to the primary and of a source in a unit other than glucose, the entity categories and the entities disabled by
default, the binary sensors' "on" / "off" and their threshold after a unit switch, an action while GlucoCraft is
not loaded, the diagnostics' redaction, the login of the HTTP views, HEAD on the wall-panel address, three reloads
without a listener left behind and an entry of a newer version. A test fails when GlucoCraft logs an error.

It needs a virtual environment of its own, apart from `requirements_test.txt` (the stub suite must not see Home
Assistant), on Linux or macOS (Home Assistant does not run on Windows), with Python 3.14:

```
python -m pip install -r tests_ha/requirements.txt           # the newest tested Home Assistant
python -m pip install -r tests_ha/requirements_minimum.txt   # or the oldest supported one (hacs.json)
python -m pytest tests_ha
```

`tests_ha/pytest.ini` puts the repository root on the path, so that Home Assistant loads
`custom_components/glucocraft` from the repository.

| File | Covers |
|---|---|
| `tests_ha/conftest.py` | The recorder, the persistent notifications and a config folder of each test's own; the source entity, the setup flow, the Configure screens (`save_screen` sends every field with its shown value), a new reading 5 minutes later |
| `tests_ha/test_config_flow.py` | The setup flow on Home Assistant's flow manager |
| `tests_ha/test_setup_and_entities.py` | Setup, the 28 entities and the actions, the units and display precision in mg/dL and mmol/L, unload and reload, removal |
| `tests_ha/test_options.py` | The Configure menu and every screen, the optional features (134 entities), the display unit switch, a refused printer address |
| `tests_ha/test_webhook.py` | The wall-panel address on Home Assistant's webhook component |
| `tests_ha/test_display_and_report_paths.py` | The no-current-data flag with an expired sensor, the stored no-current-data limit, the Maintenance status, the wall-panel notices, the GRI from 14 days, the dashboard through reload / disable / its setting, the CGM Report endpoints |
| `tests_ha/test_host_behaviours.py` | The setup's refusals (a second source equal to the primary, a source in a unit other than glucose), the entity categories and the entities disabled by default, the binary sensors' states and threshold after a unit switch, an action while not loaded, the diagnostics' redaction, the views' login and HEAD on the wall-panel address, listeners after three reloads, an entry of a newer version |
| `tests_ha/test_intake_times.py` | Measurement times on Home Assistant's own time parser: impossible times are no measurement time; the history import keeps the repeated fall-back hour as the live path |
| `tests_ha/test_phone_messages.py` | A failed message to a phone on Home Assistant's own service registry: a call that does not wait is logged by Home Assistant with its data; GlucoCraft's messages and delivery test leave no message text, webhook id or action name in the log |

## How the stub works

`conftest.py` runs `ha_stub.install()` before pytest collects any test file. After that:

- every `homeassistant.*`, `aiohttp.*` and `voluptuous` import returns a permissive stub (any attribute is a class of
  its own, any call returns a stub);
- `homeassistant.core.callback` is the identity, `homeassistant.const` has the state strings,
  `homeassistant.util.dt` does real time-zone arithmetic in `ha_stub.TZ` (America/Los_Angeles;
  `dt.set_default_time_zone(tz)` changes it), and `homeassistant.helpers.storage.Store` is an in-memory store
  (`ha_stub.STORE_DATA`, emptied before every test);
- the integration is the package `glucocraft`, registered without running its `__init__.py`;
- there is one clock: `time.time()`, which the integration reads; the stub's `dt.utcnow`, `dt.now` and
  `dt.start_of_local_day` and node's `Date` (`node_runner.py`) follow it. Every test starts on the real clock (or at
  the instant of `GLUCOCRAFT_TEST_CLOCK`, see [Running at a given time](#running-at-a-given-time));
  `ha_stub.set_clock(instant)` lets the clock run from an instant for the rest of the test.

## Writing a new test file

Name it `tests/test_<topic>.py`. Nothing needs to be imported for the stub; import the code as

```python
from glucocraft import units, statistics
import ha_stub  # only when the in-memory Store or the stub time zone is needed
from conftest import LA, AGP, stats_record, local_ts  # shared constants and helpers
```

Fixtures available everywhere: `storage` (an empty `GlucoCraftStorage` in a temporary directory),
`make_readings`, `make_stats`, `agp_ranges`, `fake_hass` (states, recorded service calls, executor jobs run
inline), `make_coordinator` (a plain `FakeCoordinator` with the attributes the notifications, the panel feed and
the report read). Tests that need the real coordinator call `conftest.real_coordinator(tmp_path, **attributes)`: it
runs the coordinator's own `__init__` with a stand-in Home Assistant (`DictStates` for the states), loads an empty
readings file in `tmp_path` and then sets the attributes given (see `bare_coordinator` in `test_storage.py`).

A test that reads a file outside the integration uses the helpers of `conftest.py`, so that it is skipped with a
reason where the file is missing: `repo_doc("NAME.md")` for a document in `docs/`, and `PANELS` (None without the
panel configurations) with the skip reason `NO_PANELS`. No test uses a fixed path outside the repository.

Docstrings: the module docstring states what the file covers and its rules under test, in words (a rule, never the
id of a review finding); a test whose name does not say the whole rule has a one-line docstring with the rule; each
helper and test fake has a one-line docstring.

## Known open items (strict expected failures)

When a real defect is found and not fixed at once, mark its test `xfail` with the reason here; with `xfail_strict` a
fixed item fails the run until its marker is removed. The settings tests list such defects in `BUGS` of
`test_settings_harness.py` (marked with `bug(key)`), the synthetic-data tests in `FINDINGS` of
`test_synth_scenarios.py` (with `PINNED` in `test_verify_scenario_masks.py`; none at present) and in
`test_synth_sub_minute_times.py` (none at present).

## Synthetic data

`tools/synth/` (docs/DEVELOPER_GUIDE.md, section 12) provides three data sources, all compared through the same
harness (`GlucoCraftRun`, `ExpectedRun` and the `compare_*` functions of `test_synth_scenarios.py`):

1. the realistic generator (`generate.py`) and the named edge and extreme cases (`scenarios.py`);
2. deterministic sine waves (`sine.py`) with closed-form expected values (`expected.sine_statistics`);
3. optionally, a real history read into a local file (`real_history.py`, `GLUCOCRAFT_REAL_HISTORY`).

A new edge case is a function in `tools/synth/scenarios.py` added to `SCENARIOS`; `test_synth_scenarios.py` then runs
it in both source units and both display units. Any other series becomes a scenario with `scenarios.from_series`. The
recorder history length is a parameter of `scenarios.history` (`test_history_lengths.py`). A profile fitted from real
data, or a real history, never belongs in the repository (`test_synth_generator.py` and `test_synth_real_history.py`
check it).

The installation-test Home Assistant (docs/DEVELOPER_GUIDE.md, section 12.3) runs with automatic updates off and the recorder's keep days set per test; roll it back to its clean
snapshot after each run.
