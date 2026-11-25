# Contributing to GlucoCraft

GlucoCraft is free, open-source software, and contributions are welcome: test reports, bug reports, translations and
pull requests. This page describes how to report what you find and how to propose a change.

> **For development and testing only.** GlucoCraft is free, open-source software, made available for development and
> testing. It is not intended for any medical purpose, including diagnosing, monitoring, treating or managing diabetes
> or any other medical condition, or for making treatment decisions. It has not been cleared, approved or certified as a
> medical device by the FDA or any other regulator. Its information and notifications can be late, missing or wrong. Its
> notifications are not alarms. GlucoCraft is licensed under the GNU Affero General Public License, version 3 only
> (AGPL-3.0-only), and is provided without warranty and, as far as the law allows, without liability (sections 15 and 16
> of the license).

## Volunteers

GlucoCraft is in development and is not offered for everyday use. The project is looking for volunteers to help make
it a multilingual community project that meets the rules that apply to it:

- **Testers** test GlucoCraft in their own way and report what they find (*Testing and reporting*).
- **Developers** fix bugs, improve the code and the tests, and review pull requests (*Developer setup*, *Pull
  requests*).
- **Lawyers** review the texts and the project's position under health-software, privacy and open-source licensing
  law. Comments made in public issues and discussions are contributions to the project, not legal advice to anyone
  reading them.

Translators are welcome too (*Translations*). There is no sign-up: anyone can start with a test report, a bug report,
a pull request or a discussion. Replies come as volunteers' time allows.

## Testing and reporting

**What testers do.** Testers install GlucoCraft in a Home Assistant, follow the [README](README.md) and the
Documentation page, and compare what GlucoCraft shows with what the documentation says it is designed to do: the
statistics, the dashboard pages, the CGM Report and its PDF, the notifications (the summaries and the no-data
notice), the settings, the installation and removal, and the example wall-panel configurations. Testers choose how
they test and with which data. Real readings from the tester's own CGM are fine; the CGM system's own app, reader or
receiver stays the one to rely on for diabetes management. As an option, every test can be done with the synthetic
data of `tools/synth` ([developer guide](docs/DEVELOPER_GUIDE.md), section 12), without any CGM data; the developer
guide's *Testing without a CGM* gives the steps and the two commands that feed a test Home Assistant with
synthetic readings and says what to expect in the first hour.

**How to report.** One finding per issue, with the form that fits it:

- **Bug report:** something that does not work as documented. Its optional *Area* field marks the part concerned;
  an accessibility problem is reported with the area *Accessibility*.
- **Test report:** what was tested and what was found, including tests that found nothing.
- **Translation offer:** an offer to translate GlucoCraft's texts into a language.

The bug report and the test report ask for the Home Assistant version, the GlucoCraft version, the source integration
(or the synthetic data of `tools/synth`) and the display unit, and, optionally, the wall panel type and version. The
bug report can also take the diagnostics file (Settings > Devices & services > GlucoCraft > the menu (three dots) >
**Download diagnostics**). The diagnostics file contains no glucose values or readings and no custom range limits or
goals (it states only whether custom ranges and custom goals are set), and it redacts the entity ids of the sources,
the phones' notify actions, the printer address and the id of the wall-panel address; read it before attaching it.
The translation offer asks for the language, its language code and the parts offered.

**Issues are public.** Leave out glucose values, laboratory results, health information, names, addresses, tokens,
passwords, wall-panel addresses and screenshots that show any of these. A security vulnerability is reported
privately, as described in [SECURITY.md](SECURITY.md).

## Where to report

Reports, questions and proposals are made on GitHub: **Issues** for bug reports, test reports, translation offers and
displays to be listed, **Discussions** for questions and ideas, and private vulnerability reporting for security
issues.

## Developer setup

The [developer guide](docs/DEVELOPER_GUIDE.md) explains how the parts fit together, how to run GlucoCraft in a
development Home Assistant, how to run the tests and the linters ([tests/README.md](tests/README.md)), and how to
build a wall panel from the example configurations. Every file uses LF line endings and UTF-8 (`.gitattributes`,
`.editorconfig`).

## Translations

Translations are welcome. GlucoCraft is written in English, and every text it shows is kept in language catalogues so
that it can be translated:

- the integration and the dashboard: `custom_components/glucocraft/i18n/<language>/` (`backend.json`,
  `frontend.json`, `report.json`, `documentation.json`);
- Home Assistant's own screens (settings, entity names, Repairs, actions):
  `custom_components/glucocraft/translations/<language>.json`;
- the wall panels: `esphome/i18n/<language>.yaml`, from which `esphome/tools/make_panel_texts.py` generates the
  panel texts.

A missing key falls back to English. Translations are often longer than English, and the wall panels have fixed room
for each text. A new language passes the fit check, `esphome/tools/worst_case_fit.py <language>`, before any panel is
built. Where a text does not fit, the translator supplies an approved shorter term; a small panel can stay in English
for a language that cannot fit, with the display card as the translated equivalent. Texts are never built from
fragments. Right-to-left languages are not supported: the cards, the report and the panels have no right-to-left
layout.

To offer a translation, open a **Translation offer** issue; the translation itself then comes as a pull request.

## Sharing a display

A display that a tester has built for GlucoCraft's data can be listed in
[docs/DISPLAY_EXAMPLES.md](docs/DISPLAY_EXAMPLES.md). Submissions are about testing, developing and displaying
GlucoCraft's data; diagnosing, treating or managing diabetes, treatment decisions and personal health information are
outside this project's topics, and submissions about them are closed.

To submit a display, publish it in its own public repository under an open-source license and open a **Share a
display** issue. The GlucoCraft authors write the short description in the list. The display remains its author's
work and is maintained by its author.

A display can use any open-source license; a display that contains code taken from GlucoCraft (for example from the
example panel configurations) is published under the GNU AGPL-3.0-only, GlucoCraft's license.

## Pull requests

Pull requests are welcome. For anything larger than a small fix, open an issue first so that the direction can be
agreed before you write the code.

### Requirements for every pull request

- **One topic per pull request.** Separate changes go in separate pull requests.
  *Why:* a pull request with one topic can be reviewed, tested and, if needed, reverted on its own.
- **Tests.** `python -m pytest` passes, and new behavior has tests ([tests/README.md](tests/README.md)).
  *Why:* the project calculates statistics from glucose data. A wrong number looks as believable as a right one, so
  every calculation is pinned down by tests that would catch a change in its result.
- **Code style.** `ruff check .` and `ruff format --check .` pass.
  *Why:* one consistent style keeps the code readable for everyone, and the review can focus on what the change does,
  not on how it is formatted.
- **Texts.** Every user-visible text is in the English language catalogues, never in the code, and follows the
  [language style](docs/LANGUAGE_STYLE.md) and the [accessibility rules](docs/ACCESSIBILITY.md).
  *Why:* texts in the catalogues can be translated, checked for fit on the wall panels, and kept consistent. One topic
  is worded the same way everywhere. The accessibility rules make sure that nothing depends on color alone and that
  every screen can be read aloud.
- **Displays in step.** A change to what the display card shows is made on every example wall panel too, and the
  reverse.
  *Why:* the card and the panels show the same data. A user who sees one value on the wall and another on the
  dashboard cannot know which to believe.
- **Cited methods.** Every target, range and formula comes from a cited published source; a rule of the project's own
  is labeled as one.
  *Why:* the project sets no targets of its own. Citing the source lets anyone check where a number comes from, and
  the label shows where the project made a choice of its own.
- **No alarms, predictions or treatment advice.** The project provides notifications, never alarms. Pull requests that
  add alarms, predictions of future glucose, or treatment or dosing advice are not accepted.
  *Why:* these functions are outside the project's scope. An alarm is a safety function. People rely on it to warn
  them, so it must work every time, and this project's information can be late, missing or wrong. Predictions and
  treatment or dosing advice invite treatment decisions and can cause harm when they are wrong. The project's
  notifications are ordinary messages and are always called notifications.
- **No history in the files.** Code, comments, texts, tests and documents carry no internal decision or review
  numbers, no remarks about who decided what, and no development dates. Where the reason for a rule matters, the
  file states the reason in plain words; the history of a change belongs in its pull request, its issue and the
  commit message. A reference to an issue or a pull request of this repository (#123) may stand where it helps.
  *Why:* a file is read long after the discussion that shaped it. A reason in plain words stays true and readable;
  a bare number or a date sends the reader to records they may not have, and a release check rejects them.
- **Data.** No personal data, real glucose data, tokens, passwords or network addresses. Use the synthetic data of
  `tools/synth`.
  *Why:* pull requests are public and stay in the repository's history for good. Health data and secrets cannot be
  taken back once published.
- **Generated code.** If you use AI tools, you understand and have reviewed every line you submit.
  *Why:* the maintainers review what you submit, but you are the one who knows what it is meant to do. Code that
  nobody understands cannot be maintained or trusted with health data.
- **Versions.** Leave version numbers to the maintainers.
  *Why:* the integration, the dashboard files and the wall panels each have their own version, set together at
  release, so that users can tell which parts belong together.

### Commit messages

A short summary in the imperative: "Fix …", "Add …", "Remove …", "Change …". Pull requests are squash-merged, and the
maintainers may edit the final message.
*Why:* each pull request becomes one clear line in the history, which is what the release notes are written from.

### Review

The maintainers review every pull request before it is merged. The tests run automatically. For a first-time
contributor, they run after a maintainer approves them.
*Why:* review keeps every change within the rules above. Approving a first-time contributor's tests stops unknown code
from running in the project's automation.

### License

Contributions are made under the project's license, the GNU Affero General Public License, version 3 only
(AGPL-3.0-only). By opening a pull request, you confirm that you have the right to submit your contribution under that
license.
