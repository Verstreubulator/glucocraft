# Third-party components

GlucoCraft is licensed under the GNU Affero General Public License, version 3 only (SPDX identifier `AGPL-3.0-only`;
see `LICENSE` in the repository). It contains or uses the following third-party components.

## Material Design Icons (bundled: one icon path)

`www/glucocraft-display-card.js` contains the path data of the icon *arrow-right* from Material Design Icons by
Pictogrammers (https://pictogrammers.com/library/mdi/).

Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with
the License. You may obtain a copy of the License at https://www.apache.org/licenses/LICENSE-2.0. Unless required by
applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language
governing permissions and limitations under the License.

The full text of the Apache License, Version 2.0 is in `LICENSE-Apache-2.0.txt`, in this folder.

The example panel configurations use the same icon, `mdi:arrow-right`, for the trend arrow (the `image:` entries
`trend_arrow_img` and `trend_arrow_mini_img`). ESPHome downloads it when a panel is built; it is not bundled. The icons
of Material Design Icons are licensed under the Apache License, Version 2.0 (Pictogrammers Free License,
https://github.com/Templarian/MaterialDesign).

## ReportLab (not bundled)

The PDF of the CGM Report is made with ReportLab (https://www.reportlab.com/), which Home Assistant installs from PyPI
as a requirement of the integration (`manifest.json`). ReportLab is licensed under the BSD license of ReportLab Europe
Ltd.; it is not part of this folder.

## Liberation Sans (bundled: three font files)

`fonts/LiberationSans-Regular.ttf`, `fonts/LiberationSans-Bold.ttf` and `fonts/LiberationSans-Italic.ttf` are
unmodified copies of the Liberation Sans fonts, version 2.1.5, of the Liberation Fonts project
(https://github.com/liberationfonts/liberation-fonts). The PDF of the CGM Report embeds subsets of them.

Digitized data copyright (c) 2010 Google Corporation with Reserved Font Arimo, Tinos and Cousine. Copyright (c) 2012
Red Hat, Inc. with Reserved Font Name Liberation. Licensed under the SIL Open Font License, Version 1.1; the license is
in `fonts/LICENSE-LiberationSans.txt` and at http://scripts.sil.org/OFL.

## ESPHome (not in this folder)

The example panel configurations are built with ESPHome, which has its own licenses (GPL-3.0 for its C++ code, MIT
for its Python code). No prebuilt firmware is distributed.

## Roboto (not bundled)

The example panel configurations draw their text in Roboto (`gfonts://Roboto`), which ESPHome downloads when a panel
is built; no font file of it is part of this repository. Copyright 2011 The Roboto Project Authors
(https://github.com/googlefonts/roboto-classic). The Roboto files that ESPHome downloads are licensed under the SIL Open
Font License, Version 1.1 (https://openfontlicense.org).

## LVGL (not bundled)

The panels' screens are drawn with LVGL (https://lvgl.io), which ESPHome builds into the panel firmware; it is not
part of this repository. LVGL is licensed under the MIT License (copyright LVGL Kft).

## ESP-Hosted (not bundled)

The Wi-Fi co-processor (ESP32-C6) of the Waveshare ESP32-P4-WIFI6-Touch-LCD-7B runs Espressif's ESP-Hosted firmware
(https://github.com/espressif/esp-hosted-mcu). The update described in `docs/PANELS.md` (*Updating the ESP32-C6*) is
installed by the user from ESPHome's firmware list; the firmware is not part of this repository. ESP-Hosted is
licensed under the Apache License, Version 2.0 (most of its source; each file's SPDX header states its license).
