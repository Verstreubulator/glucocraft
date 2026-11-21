# GlucoCraft

> **For development and testing only.** GlucoCraft is free, open-source software, made available for development and
> testing. It is not intended for any medical purpose, including diagnosing, monitoring, treating or managing diabetes
> or any other medical condition, or for making treatment decisions. It has not been cleared, approved or certified as a
> medical device by the FDA or any other regulator. Its information and notifications can be late, missing or wrong. Its
> notifications are not alarms. GlucoCraft is licensed under the GNU Affero General Public License, version 3 only
> (AGPL-3.0-only), and is provided without warranty and, as far as the law allows, without liability (sections 15 and 16
> of the license).

**Project status.** GlucoCraft is in development. The project is looking for volunteer testers, developers and
lawyers. It is not offered for everyday use. How to help is described in [CONTRIBUTING.md](CONTRIBUTING.md),
*Volunteers*.

GlucoCraft is a Home Assistant custom integration designed to calculate and display continuous glucose monitoring (CGM)
data provided by other integrations (statistics as defined in the published international consensus, a dashboard, a CGM
Report with PDF (Portable Document Format) and printing, summary notifications, and data files for ESP32 wall panels).

**Status:** pre-release (integration version 0.0.3). Not in the default catalog of Home Assistant's Marketplace
or of HACS: install it as a custom repository ([Installation](#installation)).

## Sources not supported for live display

GlucoCraft needs a new reading every 5 minutes or more often to show a current value. A source that delivers its
readings less often, or hours late, is not supported for live display: its readings are stored and shown, but the
trend, the 5-minute delta and *No current data* do not work as intended with it. These CGM systems cannot deliver that:

| CGM system | Why | What works | What does not work |
|---|---|---|---|
| FreeStyle Libre 14 day, FreeStyle Libre (first generation) | A new reading only when the sensor is scanned; stored values every 15 minutes | Readings stored and shown; graphs, statistics and the CGM Report | Trend, 5-minute delta, the no-current-data limit, data gaps, sensor active |
| Dexcom Stelo | No real-time sharing; readings reach other software about 3 hours late | Graphs, statistics and the CGM Report, once the readings arrive | Every live display shows No current data; no trend or 5-minute delta |
| Abbott Lingo | Readings shared only about 3 hours late, and only within its sharing range | Graphs, statistics and the CGM Report, once the readings arrive, without the readings outside that range | Every live display shows No current data; no trend or 5-minute delta |

Sources: the makers' published information (October 2026), cited in the Documentation, *Supported Sources*.

GlucoCraft is built for readings from a CGM sensor. Readings from a blood glucose meter or entered by hand are stored
and shown, but the statistics, the trend and the data-gap rules are not designed for them.

Readings passed on through a phone's health app are not supported for live display either:

| Health app | Why |
|---|---|
| Apple Health (through the Home Assistant iOS app) | No measurement time on the readings; no new readings while the iPhone is locked; Dexcom readings arrive about 3 hours late |
| Health Connect (through the Home Assistant Android app) | Readings every 15 minutes at the app's default update frequency; Dexcom readings arrive about 3 hours late |

With either, the features listed above do not work as described.

GlucoCraft is designed for every CGM system that delivers readings every 5 minutes or more often. If readings arrive
every 15 minutes or less often for any reason, GlucoCraft shows a Repairs notice. Information as of October 2026.

## Essentials

- The sensor limits must match the CGM sensor's measurement range. GlucoCraft sets them from the source where it can and
  shows a Repairs notice when the readings point to a different limit; otherwise they are set by hand (Documentation:
  *Readings Outside the Sensor's Range*).
- GlucoCraft is designed for readings every 5 minutes or more often. The CGM systems and phone health apps that cannot
  deliver that are listed under [*Sources not supported for live
  display*](#sources-not-supported-for-live-display).
- Deleting GlucoCraft deletes every CSV (comma-separated values) export, and also its stored readings when *When
  GlucoCraft is deleted* is set to *Delete everything* (by default they are kept for a reinstall). To keep the
  readings, export them and copy the export files out of the `glucocraft_exports` folder before deleting; they can be
  read back in after a reinstall. With either choice, deleting GlucoCraft also deletes every report PDF and the
  recorded history and long-term statistics of its entities in Home Assistant's recorder.

## Features

What the code is designed to do; the Documentation page of GlucoCraft's dashboard explains each feature in the
section named in italics.

- **Sources:** the glucose sensor entity of another integration (mg/dL or mmol/L) and an optional second source
  (*Supported Sources*).
- **LO / HI:** readings outside the CGM sensor's measurement range give no value: they are left out of the calculated
  values, counted as very low or very high in the time in ranges, and marked along the bottom or top edge of the
  graphs; the statistics tables, the notifications and the report state their time (*Readings Outside the Sensor's
  Range (LO / HI)*).
- **Statistics:** time-weighted, for seven periods from 6 hours to 6 months, with the cited consensus targets for
  adults in general or the custom goals of the settings (the targets the consensus publishes for older or high-risk
  adults and for pregnancy are not included); GlucoCraft sets no targets and makes no assessments (*Metrics and Sources*; *Gaps,
  Reading Interval and No Current Data*). *Sensor active (data received by GlucoCraft)*: close to, but not the same
  as, the CGM system's own figure: only the readings received are counted. A period's statistics are shown only when
  the readings received cover at least 70% of it (*How GlucoCraft Is Designed*, *Enough data*).
- **Trend:** a 5-minute delta and a trend arrow from an adaptive smoothed level (*How GlucoCraft Is Designed*).
- **Dashboard, display cards and CGM Report:** an optional read-only dashboard and two display cards; the zone word
  under every glucose value, so that color is never the only cue; a CGM Report page and PDF, which can be printed or shared, for example with a healthcare professional (*Pages*;
  [docs/DASHBOARD.md](docs/DASHBOARD.md)).
- **Notifications:** a daily summary (on by default), optional weekly and monthly summaries (off by default; the
  monthly summary comes with the weekly one) and an optional no-data notice, in Home Assistant and on the
  phones chosen in the settings; no notifications about high or low glucose (*Notifications (Not Alarms)*).
- **Data kept:** 200 days; GlucoCraft's own CSV export can be saved and read back in (*Data Kept and CSV Export*).
- **Accessibility:** designed to follow WCAG 2.2 level AA where it applies. No conformance claim is made. The rules:
  [docs/ACCESSIBILITY.md](docs/ACCESSIBILITY.md); problems are welcome as bug reports with the area *Accessibility*.
- **Local processing:** no data to its authors or to any cloud service of its own; no telemetry (see
  [Privacy](#privacy)). The update entity that announces a new version belongs to the Marketplace or HACS, not to
  GlucoCraft.
- **Language:** every text is prepared for translation, but only English is provided.

## Requirements

- Home Assistant **2026.3.1** or later (`hacs.json`): from that version Home Assistant shows GlucoCraft's own icon and
  sends its time zone to any wall panels.
- History: at the setup, GlucoCraft imports the source's history that Home Assistant's recorder still holds, up to
  200 days (the recorder keeps 10 days by default; its `purge_keep_days` setting).
- A glucose sensor entity provided by another integration; GlucoCraft connects to no CGM system, device or cloud
  service itself.
- `reportlab==4.2.5` (installed by Home Assistant from `manifest.json`) and the built-in integrations `http`,
  `frontend` and `webhook`, with `lovelace` and `recorder` where they are loaded.
- One installation per Home Assistant: it serves one person and one CGM system.
- Optional: a network printer that accepts the Internet Printing Protocol (IPP); ESP32 wall panels built with ESPHome.

## Installation

GlucoCraft is not in the default catalog of Home Assistant's Marketplace or of HACS. Once GlucoCraft is set up, the
Documentation page of its dashboard describes each step in full (*Installing, Updating and Removing GlucoCraft*).

1. **Install** it as a custom repository with the category **Integration**: on Home Assistant 2026.11 and later, add
   `https://github.com/Verstreubulator/glucocraft` in **Settings > Marketplace**; on 2026.3.1 to 2026.10, add `https://github.com/Verstreubulator/glucocraft` in HACS
   (the menu (three dots) > **Custom repositories**), then open GlucoCraft in HACS and select
   **Download**; or copy the folder `custom_components/glucocraft/` into the
   `custom_components/` folder of the configuration directory.

   On Home Assistant 2026.11 and later, this button opens GlucoCraft in the Marketplace of your own Home Assistant:

   [![Open your Home Assistant instance and open a repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=Verstreubulator&repository=glucocraft&category=integration)
2. **Set it up:** restart Home Assistant (not needed after an installation through the Marketplace), select
   **Settings > Devices & services > Add integration > GlucoCraft**, read
   the disclaimer on the first screen and follow the setup steps; the Documentation page of the GlucoCraft dashboard
   explains everything in detail.
3. **Update** it from the update entity of the Marketplace or HACS under **Settings > Updates** (by hand: copy the
   new folder over the old one), restart Home Assistant and reload any open dashboard page once; the settings and the
   stored readings are kept.
4. **Remove** it: first choose under **Configure > Report and dashboard > When GlucoCraft is deleted** whether the
   readings are kept for a reinstall (the default) or deleted; then **Settings > Devices & services > GlucoCraft** > the
   menu (three dots) > **Delete**, restart Home Assistant and remove GlucoCraft and its custom repository in the
   Marketplace or in HACS (by hand: delete the folder). A new installation starts with the default settings, and every
   wall panel needs its new wall-panel address.

## Configuration

**Settings > Devices & services > GlucoCraft > Configure** opens the settings, one screen per topic; each setting is
explained under its field and in the Documentation section *Setup*.

After the setup, *Settings Status* and *Maintenance* show:

- the unit of the source (mg/dL or mmol/L);
- the reading interval of the source (designed for 5 minutes or more often);
- the sensor lower and upper limits in use (the Documentation lists the measurement ranges of common CGM systems);
- the Maintenance status: *No issues*, or the issues to resolve, such as a source that updates too slowly or a sensor
  limit that does not match.

A wall panel needs a wall-panel address, created on the *Display card and wall panels* screen; a new address stops
the earlier one, and every panel is then built again ([docs/PANELS.md](docs/PANELS.md)).

## Notifications

GlucoCraft sends a daily summary and, when the weekly summary is turned on, the weekly and monthly summaries, an
optional no-data notice about its own data, and notices
about wall panels, exports and the report; the summaries and the no-data notice can also go to phones, with the same
text as in Home Assistant (Documentation: *Notifications (Not Alarms)*).

**No high or low glucose notifications.** GlucoCraft sends no notifications about high or low glucose.

## Entity names

Every entity id starts with `glucocraft_` and every display name with "GlucoCraft" (docs/ENTITIES_AND_API.md).
**GlucoCraft keeps its entity ids:** the dashboard, cards and wall panels look for these exact ids, so GlucoCraft gives
a changed id back within seconds and says so in a Repairs notice. Names can be changed freely: change the *display
name* of an entity or the device, and if Home Assistant offers to change the entity ids as well, leave them as they are.

## Wall panels

Optional. The panel configurations are examples. They are not a finished or ready-to-use product. The
examples in `esphome/` cover five boards (Guition ESP32-S3-4848S040 and JC3248W535; Waveshare
ESP32-S3-Touch-LCD-5, ESP32-S3-Touch-LCD-7 and ESP32-P4-WIFI6-Touch-LCD-7B); prebuilt firmware is not distributed. A panel's data download is plain HTTP over the
local network, so its data are only as private as the home network. Adding, building, removing and security:
[docs/PANELS.md](docs/PANELS.md).

## Troubleshooting and known limitations

The Documentation sections *Troubleshooting* and *Known Limitations* list the usual symptoms and checks and the known
limitations. **Download diagnostics** (Settings > Devices & services > GlucoCraft > the menu (three dots)) contains no
glucose values, no entity ids of the sources and no custom range limits or goals. For more detail in Home Assistant's
log, the same menu has **Enable debug logging**; **Disable debug logging** then downloads the log. A debug log names
entities and folders of the installation; read it and remove names before sharing it.

## Versions

| Part | Version | Where it is set |
|---|---|---|
| Integration | 0.0.3 | `custom_components/glucocraft/manifest.json` (also the device version) |
| Dashboard strategy | 0.0.1 | `www/glucocraft-strategy.js` |
| Dashboard cards | 0.0.1 | `www/glucocraft-ui.js` |
| Display card | 0.0.1 | `www/glucocraft-display-card.js` |
| Documentation page | 0.0.1 | `www/glucocraft-docs.js` |
| Report page | 0.0.2 | `www/glucocraft-report.js` |
| Language module | 0.0.1 | `www/glucocraft-i18n.js` |
| Shared module | 0.0.1 | `www/glucocraft-shared.js` |
| Wall panel configurations | 0.0.1 | `panel_version` in each example panel configuration |

Each frontend file reports its version in the browser console; each panel shows its version on its About page and in
Home Assistant's device information. Versions stay below 0.1.0 during the pre-release. The versions of the file
formats and of the stored settings are listed in [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md), *Version policy*.

## Development

[docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) explains the code, the tests ([tests/README.md](tests/README.md);
`python -m pytest`, `ruff check .`) and the panel build. Contributions are welcome: test reports, bug reports,
translations and pull requests ([CONTRIBUTING.md](CONTRIBUTING.md)); [SECURITY.md](SECURITY.md) describes how to
report a vulnerability.

**How GlucoCraft is made.** GlucoCraft is written with the help of an AI coding assistant, Claude (Anthropic). The
maintainer directs the work and decides every design question and every public text; the code is checked by the test
suite and on test installations of Home Assistant before each release.

## Privacy

GlucoCraft runs inside the user's Home Assistant and sends no data to its authors or to any cloud service of its own;
it has no telemetry. Data leave Home Assistant only where the user sends them: notifications to the phones and notify
actions selected in the settings, CGM Reports to a printer address set in the settings, and files the user copies,
along with whatever Home Assistant itself sends, such as backups, remote access and voice assistants, as set up by the
user. How a notification reaches a phone is decided by Home Assistant's Companion app (see its documentation). The
full statement, with what each route means, is in the Documentation, *Privacy and Wall Panels*.

The panels' connections are described in [Wall panels](#wall-panels).

## License

Copyright (C) 2026 The GlucoCraft authors. Licensed under the GNU Affero General Public License, version 3 only
(SPDX identifier `AGPL-3.0-only`; see [LICENSE](LICENSE)). Provided WITHOUT ANY WARRANTY.

Prebuilt wall-panel firmware is not distributed: users build it from the published example panel configurations; the
ESPHome software it contains has its own license.

## Third-party components

Material Design Icons (Apache License, Version 2.0; one icon path in the display card, and the same icon downloaded
by ESPHome for the panels, not bundled), ReportLab (BSD license; not bundled), Liberation Sans 2.1.5 (SIL Open Font
License, Version 1.1; bundled, with its license, in `custom_components/glucocraft/fonts/`), ESPHome (its own licenses), and for the panels Roboto (SIL Open Font License, Version 1.1), LVGL
(MIT License) and the ESP-Hosted co-processor firmware (Apache License, Version 2.0), none of them bundled: see
`custom_components/glucocraft/THIRD_PARTY_NOTICES.md`.

## Trademarks

FreeStyle Libre, LibreLinkUp and Lingo are trademarks of Abbott; Dexcom and Stelo are trademarks of Dexcom, Inc.;
Medtronic, CareLink, Simplera and Guardian are trademarks of Medtronic or its affiliates; Eversense is a trademark of
Senseonics, Incorporated; Apple Health and iPhone are trademarks of Apple Inc., and iOS is a trademark of Cisco, used by
Apple under license; Android and Health Connect are trademarks of Google LLC; Home Assistant and ESPHome are trademarks
of the Open Home Foundation; ESP32 is a trademark of Espressif Systems; Medtrum, SIBIONICS, Guition and Waveshare are
trademarks of their respective owners; Claude is a trademark of Anthropic, PBC; Nightscout and xDrip+ are names of
open-source projects; other names are the trademarks of their owners. Each name is used only to identify the product it
names. GlucoCraft is not affiliated with, endorsed by or sponsored by any of them. Research cited in GlucoCraft is
attributed to its authors; GlucoCraft is not affiliated with or endorsed by any cited researcher, institution or
organization.

## References

Every source cited by GlucoCraft is listed, with links, in the Documentation page's References section.
