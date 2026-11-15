// License: AGPL-3.0-only (LICENSE).
/*
 * glucocraft-display-card: GlucoCraft's display card, the wall panels' pages on the Home Assistant dashboard.
 *
 * The same layout, data and rules as GlucoCraft's wall panels (the example panel configurations, glucocraft-panel.yaml;
 * docs/PANELS.md and docs/DISPLAY_CONTRACT.md):
 *   - data: GlucoCraft's entities, and its GLU2 v3 files from /api/glucocraft/binary (Home Assistant's login): the
 *     same files the panels get from the local-only webhook. Home Assistant's recorder is not needed.
 *   - glucose row 115 | 250 | 115 px: TREND = one arrow turned by the rate of change on the trend-arrow scale of the
 *     cited manufacturer documentation (+-2 mg/dL/min = straight up / down, 45 deg at 1; the rate is rounded to
 *     0.1 mg/dL/min first, so 4.5-deg steps, shown in whole degrees), or, by GlucoCraft's setting Trend shown as, its
 *     trend word (one of five, the broader category of the same rate; a longer word on two lines); 5 MIN DELTA =
 *     GlucoCraft's change of the smoothed glucose over 5 minutes; the source's own name; the zone word always under
 *     the value.
 *   - status bar: warm-up purple > expired red > no current data red (GlucoCraft's
 *     threshold) > expiring within 24 h amber > current green; purple = no data yet (GlucoCraft's status colors); "Custom
 *     ranges" when they are on; "Check Home Assistant" taking turns with them while GlucoCraft has a Repairs notice open
 *     (the configuration's attribute attention); "Sensor expiry passed" throbs until a new sensor is detected.
 *   - no current reading: never a number. During a warm-up the value's place says "Sensor warming up"; without
 *     current data (GlucoCraft's flag binary_sensor.glucocraft_no_current_data, or the card's own count of the reading's
 *     age past the same limit while Home Assistant cannot update it; never the status) "Sensor expired" after the
 *     stated expiry, else "No current data", at the zone word's large size; under the words, smaller and still, the last
 *     reading ("Last reading: 64 mg/dL", "Last reading: LO") and when it was taken ("Today at 17:39 (2 h 0 min ago)",
 *     on the square card split before the bracket), both left out without any reading or, for the time, while the
 *     reading's time is ahead of the clock; no zone word, "---" for the trend and the 5-minute delta; the status bar
 *     keeps the reading's age and the status. The words throb (a fade to about 30 % brightness and back every 3 seconds)
 *     for the first 30 minutes of each episode, then hold still, unless GlucoCraft's setting turns it off or the system
 *     asks for reduced motion. After the stated expiry, while readings arrive, the value keeps its zone color and the
 *     trend follows the data; the red status bar carries the expiry.
 *   - LO / HI: while the reading is beyond the sensor's range, the value's place shows, in the layout of the words
 *     without a current reading, the zone word in the zone color ("Very low"), "Below sensor range (LO)", since when and
 *     the last measured value before the stretch.
 *   - one size on every page and in every state, as the panels: the square card 480 x 480 and the rectangular card
 *     800 x 480 with the page buttons under them; the compact layout (narrow screens) in a frame of its own
 *     (_compactFrame).
 *   - weekly pages: the 14-day values at each week's end, in the range set GlucoCraft's setting selects (consensus ranges
 *     and targets, or custom ranges and goals; "Consensus target" / "Custom goal" where one exists); green / red = within / outside the cited
 *     target or the goal, neutral gray = none; no verdicts. Texts, targets and directions come from the file (one system).
 *   - every graph spaced by timestamp; the About page; the card's background and text color from GlucoCraft's
 *     settings (the Home Assistant theme or a color), the graphs always on black; with GlucoCraft's setting Outline
 *     text, a 1-pixel edge around the graph labels and every text in a zone color (gcEdge).
 *   - a reading outside the sensor's range has no value (never the stored limit): the graphs' line breaks there, a row
 *     of small marks runs along the graph's bottom (LO) or top (HI) edge for that time (gcLimitMarks), with no text
 *     on the graph (the screen in the value's place names a limit reading); lines are interrupted by the gap rule (more than
 *     max(15 min, 3 x reading interval) between readings, the reading interval judged per stretch from the readings
 *     around it, as the statistics and sensor active; gap_threshold_seconds only as the fallback).
 *   - a period below 70 % sensor active shows "---" for its statistics (GlucoCraft sends none for it).
 *
 * Card options (all optional):
 *   type: custom:glucocraft-display-card
 *   return_to_first_page: true (default) returns to the 6-hour page after 3 minutes without use; false turns it off
 *   current / trend / change / status / stale / source / last / expiry / warmup / ranges / stats_prefix / file_url:
 *   entity and file overrides (DEFAULTS below)
 * The background is a GlucoCraft setting (card_background), not a card option; a "background" card option is ignored.
 *
 * Pages: 6 h (live), 24 h, 7 d, 14 d, 30 d, 90 d, 6 months, the 9 weekly trend pages, and the About page (GlucoCraft's
 * icon, its disclaimer and the version line), a full screen without the clock header, the glucose information and the
 * status bar, as on the panels.
 * Navigation: the < > buttons under the display, swipe on a phone (left = next), tap the glucose block on any graph
 * page to open the weekly trend pages (once the history file has them) and the same area (the trend text) on a trend
 * page to return to 6 h; back to 6 h after 180 s without use, like the panel (the card option return_to_first_page:
 * false turns this off).
 */
const GCDC_VERSION = "0.0.1";
// The language catalogues (glucocraft-i18n.js, served next to this file): every text comes from them, in Home
// Assistant's language with English as the fallback. The card waits for gcI18nLoad before its first render.
const GC_I18N_AREAS = ["frontend"];
const GC_I18N = globalThis.glucocraftI18n ? Promise.resolve() : import(new URL("./glucocraft-i18n.js", import.meta.url).href);
const gcI18nLoad = (hass) => GC_I18N.then(() => globalThis.glucocraftI18n.load(hass, GC_I18N_AREAS));
const gcI18nReady = (hass) => !!globalThis.glucocraftI18n?.ready(hass, GC_I18N_AREAS);
const gcT = (key, params) => globalThis.glucocraftI18n.t(key, params);
// The rules the cards and pages share (glucocraft-shared.js, served next to this file and imported the same way): the
// settings in use and the number, glucose, time and date formats, the spoken text, the gap rule, the default colors and
// the GLU2 parser. The file waits for it before it defines its cards.
const GC_SHARED = globalThis.glucocraftShared ? Promise.resolve() : import(new URL("./glucocraft-shared.js", import.meta.url).href);
await GC_SHARED;
const { settings: GC, applyRegional, round, nf, dtx, shownAgainst, MGDL_PER_MMOLL, GLU_MAX, gU, gD, gD32, gF, gF32,
  deltaShown, gDelta, gmiMmolFromMean,
  gmiPctToMmol, gmiMmolToPct, setTimeZone, zoneParts, zoneHourStart, clockStr, hourLabel, weekdayName, dayMonth, numDate, esc,
  gcSpeakNumber, gcSpeak, gcSpeakA1c,
  gcSpeakAge, gcSpeakTrend, gcSpeakLimit, gcLimitSaid, gcSpeakText, GAP_WINDOW,
  gcGaps, gcGapIn, gcRunIn, first, gcRangeAreas, gcLimitMarks, ZONE_DEFAULT, BAND_DEFAULT, gcPalette, gcFile,
  gcFit, gcGridStep, gcGapThreshold, gcNewFailure, gcCap: cap, CONS_RANGES, css: GC_CSS, gcAxisText,
  // text on a colored area: black or white, whichever has the higher contrast with that color (WCAG 2.2), so that the
  // status bar's text stays readable for every status color
  gcContrast: contrast, gcOnColor: onColor } = globalThis.glucocraftShared;

const DEFAULTS = {
  current: "sensor.glucocraft_last_glucose_reading",   // zone, zone_color attributes (GlucoCraft colors the value)
  trend: "sensor.glucocraft_trend_arrow",               // description (the cited trend words), angle
  change: "sensor.glucocraft_5_minute_delta",           // 5 MIN DELTA (attribute value_mgdl; shown in the display unit)
  status: "sensor.glucocraft_status",                   // warm_up / expired / no_data / no_current_data / expiring_soon / current + color
  stale: "binary_sensor.glucocraft_no_current_data",   // threshold_minutes attribute
  source: "sensor.glucocraft_active_source",            // state = the source's own name; detected_interval
  last: "sensor.glucocraft_last_reading_time",          // last_reading_ts: the newest reading's own time (epoch s)
  expiry: "sensor.glucocraft_sensor_expiry",            // expires_at (UTC ISO or null)
  warmup: "binary_sensor.glucocraft_sensor_warm_up",    // started_at
  ranges: "binary_sensor.glucocraft_using_custom_ranges",   // on = custom; range_* attributes (mg/dL)
  stats_prefix: "sensor.glucocraft_6h_",   // + custom_ranges / consensus_ranges: the 6-hour summary sensors
  file_url: "/api/glucocraft/binary",
};
// The 6-hour statistics before the first file, from the attributes of a 6-hour summary sensor (glucose values in
// mg/dL), by statistics cell: [attribute of <period> consensus ranges, attribute of <period> custom ranges]; every cell of the page, as
// the panels fill it (glucocraft-panel.yaml update_stats_display)
const SUMMARY_6H = { tir: ["time_70_180", "time_in_range"], titr: ["time_70_140", "time_in_tight_range"],
  low: ["time_54_69", "time_low"], high: ["time_181_250", "time_high"], mean: ["mean", "mean"],
  sd: ["std_dev", "std_dev"], cv: ["cv", "cv"], clo: ["time_below_54", "time_very_low"],
  chi: ["time_above_250", "time_very_high"], min: ["min_value", "min_value"], max: ["max_value", "max_value"] };

const FONT = "Roboto, 'Noto Sans', 'Segoe UI', sans-serif";
// Layouts: the square card (480 x 480 panels) and the rectangular card (the 800 x 480 panel layout,
// make_glucocraft_landscape.py; also used for 1024 x 600 and the 3.5-inch, which are close). The shared graph code
// draws at the layout's graph and weekly-chart positions; every card draws with the layout in use (this.L).
// The square layout's own parts: stats, the two statistics columns beside the graph (colW x colH, from GY) and the row
// of four statistics below it (from rowY = GY + GH + 5, rowH = colH - GH - 5 high).
const LAYOUT_SQUARE = { W: 480, H: 480, NAV_H: 56, GX: 70, GY: 291, GW: 340, GH: 150, trend: { x: 0, y: 291, w: 480, h: 189 },
  stats: { colW: 60, colH: 185, rowY: 446, rowH: 30 } };
// the square glucose row, 115 | 250 | 115 px as on the panel
const LCOL = 115, RCOL = 115, MID_X = LCOL + 250 / 2;
// The rectangular layout's own parts: block, the glucose block on the left (x 0 to w, y 64-440; the value and the zone
// word around mid, the TREND and 5 MIN DELTA columns from colY, colH high); info, the weekly page's header on the right
// (from x, w wide, y 64-184).
const LAYOUT_WIDE = { W: 800, H: 480, NAV_H: 56, GX: 361, GY: 74, GW: 430, GH: 228, trend: { x: 361, y: 186, w: 430, h: 248 },
  block: { w: 352, mid: 252, colY: 290, colH: 150 }, info: { x: 362, w: 428 } };
// Geometry of the glucose value (_drawValue): its center and size, between the header and the zone word below it.
const SQ_VALUE = { x: 240, y: 141, size: 120 };
// The zone word under the value (one line, large, the card's text color; the graph shows the range's limits): its
// center and size per card, in one place, so that it can follow the panels.
const ZONE_SLOT = {
  square: { y: 216, size: 40 },
  wide: { y: 264, size: 40 },
  compact: { size: 40 },
};
// Without a current reading the value's place shows a stack (_noReading), as on the panel of the same layout
// (glucocraft-panel.yaml no_reading_box, make_glucocraft_landscape.py nr_*): the words at the zone word's size
// (ZONE_SLOT), the last reading at line px and when it was taken at time px, on one line or, with split, the age on a
// line of its own ("Today at 17:39" / "(2 h 0 min ago)"); cy: the stack's center, the middle of the room between the
// header and the TREND and 5 MIN DELTA columns (the square card's middle column y 96-256, the rectangular card's block
// y 78-290); room: its width.
const NO_READING = {
  square: { cy: 176, room: 248, line: 20, time: 16, split: true },
  wide: { cy: 184, room: 344, line: 26, time: 18, split: false },
  compact: { line: 20, time: 16, split: false },
};
// the words (s.words of _state) and the sentence of the text description that states them first (_describe)
const NO_READING_KEYS = {
  warming_up: ["card.no_reading.warming_up", "card.describe.warming_up"],
  sensor_expired: ["card.no_reading.sensor_expired", "card.describe.sensor_expired"],
  no_current_data: ["card.no_reading.no_current_data", "card.describe.no_current_data"],
};
// The trend word in the TREND column (GlucoCraft's setting Trend shown as: word), per card: its size and the column's
// room, as on the panel of the same layout (the square panel 26 px in 111 px, the 800 x 480 panel 36 px in 160 px:
// sizes at which the longest word of a line fits with a margin, tools/worst_case_fit.py); the compact layout uses the
// square size in the room it has.
const TREND_WORD = { square: { size: 26, room: 111 }, wide: { size: 36, room: 160 }, compact: { size: 26 } };
const WIDE_VALUE = { x: 176, y: 172, size: 160 };
// the statistics labels of the square layout: 12 px, the size of the square wall panel's labels (glucocraft-panel.yaml
// roboto_12; the accepted exception of ACCESSIBILITY.md rule 3); every other text of the cards is at least 14 px
const STAT_LABEL_PX = 12;
const IDLE_RESET_S = 180;
// The narrowest compact layout, one pixel per pixel: a 320 px screen leaves the card about 288 px after the dashboard's
// margins and the card's border, and at 240 px the page buttons and three statistics columns of 72 px still fit; only
// a narrower box scales the drawing
const COMPACT_MIN_W = 240;
// The compact layout's measures (px), in one place because two functions must agree on them: _compactFrame sizes every
// page by the tallest from these numbers, and _compactRender draws with them. From the top: the padding at the sides
// (and above the About page), the header with the clock, the glucose block (the value's room) and the row of the trend
// and the delta under it, the status bar (32 px) with the room under it. A graph page: the graph (170 px) with the room
// under it, one row of the statistics grid (cells at least cellW wide, at least three in a row) and the room under the
// grid. A weekly page: its title, each line of its description, its status line, and its chart (200 px) with the room
// under it. The About page: the icon and the room under it, the room under the disclaimer's first sentence and under
// the rest, the rule with the room around it, the room under the version line.
const COMPACT = Object.freeze({ pad: 12, header: 58, block: 188, trendRow: 116, bar: 40,
  graph: 178, cellRow: 52, cellW: 84, cellsEnd: 4,
  weekTitle: 34, weekLine: 18, weekStatus: 36, weekChart: 208,
  icon: 64, iconGap: 12, leadGap: 4, restGap: 12, ruleRoom: 9 + 4, versionGap: 4 });
// The page buttons ◀ / ▶ (px), on both layouts: w x h, inset from the drawing's sides (on the design layout also from
// the top of the navigation bar; the compact layout draws them at its padding). A tap on the bar counts for a button
// up to hit px from its side on the design layout, and up to hitCompact px on the compact one, where the page's name
// has the room between the two.
const NAV_BTN = Object.freeze({ w: 96, h: 40, inset: 8, hit: 110, hitCompact: 112 });
// the boxes [x, y, w, h] of the two page buttons of the design layout (a drawing of W x H with the bar under it)
const navBoxes = (W, H) => [[NAV_BTN.inset, H + NAV_BTN.inset, NAV_BTN.w, NAV_BTN.h],
  [W - NAV_BTN.inset - NAV_BTN.w, H + NAV_BTN.inset, NAV_BTN.w, NAV_BTN.h]];
const PERIOD_HOURS = [6, 24, 168, 336, 720, 2160, 4320];

// The pages' names (the catalogue keys): [the name under the display, the name within a sentence where it differs]
const PAGE_KEYS = [
  ["card.page.6_hours", "card.page_in_sentence.6_hours"], ["card.page.24_hours", "card.page_in_sentence.24_hours"],
  ["card.page.7_days", "card.page_in_sentence.7_days"], ["card.page.14_days", "card.page_in_sentence.14_days"],
  ["card.page.30_days", "card.page_in_sentence.30_days"], ["card.page.90_days", "card.page_in_sentence.90_days"],
  ["card.page.6_months", "card.page_in_sentence.6_months"],
  ["card.page.weekly_tir", "card.page_in_sentence.weekly_tir"], ["card.page.weekly_titr", "card.page_in_sentence.weekly_titr"],
  ["card.page.weekly_mean", "card.page_in_sentence.weekly_mean"], ["card.page.weekly_cv", "card.page_in_sentence.weekly_cv"],
  ["card.page.weekly_gmi", "card.page_in_sentence.weekly_gmi"], ["card.page.weekly_low", "card.page_in_sentence.weekly_low"],
  ["card.page.weekly_high", "card.page_in_sentence.weekly_high"], ["card.page.weekly_sd", "card.page_in_sentence.weekly_sd"],
  ["card.page.weekly_min_max", "card.page_in_sentence.weekly_min_max"],
  ["card.page.about"]];   // the About page is named by its title
const N_PAGES = PAGE_KEYS.length;
const pageName = (p) => gcT(PAGE_KEYS[p][0]);
const pageNameInSentence = (p) => gcT(PAGE_KEYS[p][1] || PAGE_KEYS[p][0]);
// The About page, as on the panels (DISPLAY_CONTRACT.md, Pages): the last page, a full screen with the icon, the
// disclaimer and the version line
const ABOUT_PAGE = 16;

// Weekly pages: the key of the title and the chart's y range while it has no values. The description, the target or
// goal and the range set come from the file (GCX1 extension; one system). The same titles as the wall panel.
const TREND_DEFS = [
  ["card.weekly.tir.title",     50, 100],
  ["card.weekly.titr.title",    30, 100],
  ["card.weekly.mean.title",    70, 200],
  ["card.weekly.cv.title",      20, 50],
  ["card.weekly.gmi.title",      5, 8],
  ["card.weekly.low.title",      0, 25],
  ["card.weekly.high.title",     0, 25],
  ["card.weekly.sd.title",      15, 55],
  ["card.weekly.min_max.title", 10, 350],
];
// the weekly measures as named in the text description ("Time in range of each 14-day period, ...")
const WEEKLY_SAID_KEYS = ["card.describe.weekly.tir", "card.describe.weekly.titr", "card.describe.weekly.mean",
  "card.describe.weekly.cv", "card.describe.weekly.gmi", "card.describe.weekly.low", "card.describe.weekly.high",
  "card.describe.weekly.sd", "card.describe.weekly.min_max"];
const TREND_MIN_SPREAD = [10, 10, 20, 10, 0.5, 4, 4, 10, 20];

// Colors with a meaning (status, target met or not). Everything else follows the card's colors (gcCardPalette).
const C = {
  alert: 0xE53935, unknown: 0x9E9E9E,   // the card's own no-current-data state, and no status color published
  inside: 0x43A047, outside: 0xE53935,                                                        // cited target or goal
  neutral: 0x616161,                                                                          // no target or goal
};
// ONE SYSTEM (docs/DISPLAY_CONTRACT.md): GlucoCraft decides the trend words, the arrow angle, the 5-minute delta,
// the value's color, the status and the trend directions; this card only shows them. Its own work: the clock rules
// (age, expiry countdown, warm-up minutes), the no-current-data override when Home Assistant cannot update the flag,
// and the graph layout.
const hexNum = (h) => (typeof h === "string" && /^#[0-9a-f]{6}$/i.test(h)) ? parseInt(h.slice(1), 16) : NaN;

// The range colors (zone_colors: the glucose value, LO / HI and everything else that names a zone by color) and the
// graph range colors (band_colors: the range bands of the graphs, 60 % over the graph's black), published on
// sensor.glucocraft_configuration, as numbers (0xRRGGBB; the dashboard cards keep them as "#RRGGBB" texts); the
// defaults of glucocraft-shared.js until the attributes arrive (the moment of a reload) and for a zone whose published
// color is not a valid "#RRGGBB" value
const gcNumbers = (colors) => Object.fromEntries(Object.entries(colors).map(([zone, c]) => [zone, hexNum(c)]));
const gcZoneColorNumbers = (published) => gcNumbers(gcPalette(ZONE_DEFAULT, published));
const gcBandColorNumbers = (published) => gcNumbers(gcPalette(BAND_DEFAULT, published));
// the colors in use, refreshed from the configuration sensor on every update (one installation: see glucocraft-shared.js
// settings)
let ZC = gcZoneColorNumbers(), BC = gcBandColorNumbers();
// The zone word under the glucose value, in every setting (color is never the only cue): a word for every zone, from
// the attribute display_word of the Last glucose reading sensor, else zone_label (outside the range only), else these
// words. Each zone: [the word on screen, the word within a sentence of the text description].
const ZONE_KEYS = {
  very_low: ["card.zone.very_low", "card.zone_in_sentence.very_low"],
  low: ["card.zone.low", "card.zone_in_sentence.low"],
  near_low: ["card.zone.near_low", "card.zone_in_sentence.near_low"],
  tight_range: ["card.zone.tight_range", "card.zone_in_sentence.tight_range"],
  near_high: ["card.zone.near_high", "card.zone_in_sentence.near_high"],
  high: ["card.zone.high", "card.zone_in_sentence.high"],
  very_high: ["card.zone.very_high", "card.zone_in_sentence.very_high"],
};
// the labels of the statistics cells, by the cell's id
const STAT_KEYS = { tir: "card.stat.tir", titr: "card.stat.titr", min: "card.stat.min", max: "card.stat.max",
  low: "card.stat.low", high: "card.stat.high", very_low: "card.stat.very_low", very_high: "card.stat.very_high",
  mean: "card.stat.mean", sd: "card.stat.sd", cv: "card.stat.cv", gmi_14d: "card.stat.gmi_14d", gmi_30d: "card.stat.gmi_30d",
  gmi_90d: "card.stat.gmi_90d", gmi_180d: "card.stat.gmi_180d" };
// every GMI names its period: GMI14d up to 14 days (GMI needs at least 14 days), then GMI30d / 90d / 180d
const GMI_IDS = ["gmi_14d", "gmi_14d", "gmi_14d", "gmi_14d", "gmi_30d", "gmi_90d", "gmi_180d"];
const statLabel = (id) => gcT(STAT_KEYS[id]);

// The card's colors, from GlucoCraft's settings (sensor.glucocraft_configuration): the background card_background
// ("theme" = the Home Assistant theme, or "#RRGGBB"; black by default) and the text color card_foreground (black or
// white, whichever has the higher contrast with the background; none with the theme, which then gives the text color).
// The graphs stay on black whatever the background. The grays are the text color mixed into the background, gray
// level / 255 of the way from the background to the text color (on black: #888888 the labels and a value that is not
// current, #aaaaaa the secondary texts, #808080 the frames), moved on toward the text color, one level at a time, where
// needed to keep 4.5:1 for text and 3:1 for the frames against the background (WCAG 1.4.3, 1.4.11): the rule of the
// wall panels (gc_units.h derive_theme), so that both give the same color for every setting.
const CARD_BG_DEFAULT = "#000000";
let GC_CARD_BG = CARD_BG_DEFAULT, GC_CARD_FG = NaN;   // the settings in use; NaN = the text color from the background
// Outline text (GlucoCraft's setting text_outline, off by default): a 1-pixel edge around the graph labels, the
// glucose value, LO / HI and every other text in a zone color, as on the panels. The edge is
// the opposite of the text's own lightness: black around a light text, white around a dark one (onColor: whichever
// contrasts more with the text). An SVG stroke of 2 px painted under the fill (paint-order) shows 1 px outside the
// glyphs. Only a text with a color of its own (a number) gets an edge; the page text in the card's palette (a CSS
// variable) never does.
let GC_OUTLINE = false;
// Trend shown as (GlucoCraft's setting trend_display, "arrow" by default): true = the trend word in the TREND column
let GC_TREND_WORD = false;
// Fade the words when there is no current reading (GlucoCraft's setting flash_no_current_data, on by default): the
// words in the value's place ("Sensor warming up", "Sensor expired", "No current data") throb, a smooth fade from full
// to about 30 % brightness and back, one cycle every THROB_MS (about 0.33 Hz, far below the three flashes per second of WCAG
// 2.3.1; ACCESSIBILITY.md rule 6), for the first THROB_EPISODE_MIN minutes of each episode, then they hold still for as
// long as it lasts (a GlucoCraft rule: a signal that stays on for hours is no longer noticed). An episode without current
// data starts when the reading's age passes the no-current-data limit, so the count survives a reload; a warm-up starts
// at its own start; an episode whose start is not known (no reading, a reading time ahead of the clock, a warm-up
// without its start time) at the moment the card first showed it. The status bar's "Sensor expiry passed" throbs the same way for as long as the expired status lasts,
// until a new sensor is detected (small text, no time limit). Brightness only: the text and the text description stay
// the same. The setting turns both off, and the CSS animation (gc-throb) stops while the system asks for reduced motion.
let GC_FLASH = true;
const THROB_MS = 3000, THROB_EPISODE_MIN = 30;
// The status bar's middle slot: while GlucoCraft has a Repairs notice open (the configuration's attribute attention),
// "Check Home Assistant" takes turns with the warm-up or "Custom ranges", each shown for TURN_MS
const TURN_MS = 3000;
// Return to the first page (the 6-hour page) after IDLE_RESET_S without use: on unless the card option
// return_to_first_page is false (WCAG 2.2.1: a timer the user can turn off)
const gcIdleReturn = (cfg) => cfg?.return_to_first_page !== false;
const gcEdge = (color) => (typeof color === "number"
  ? `stroke="${hex(onColor(color))}" stroke-width="2" stroke-linejoin="round" paint-order="stroke"` : "");
// [gray level of the black design, minimum contrast with the background]: the labels and a value that is not
// current; the 5-minute delta and notes; the frames of the statistics
const GRAYS = { fg2: [0x88, 4.5], fg3: [0xaa, 4.5], box: [0x80, 3.0] };
const roundAway = (v) => Math.sign(v) * Math.round(Math.abs(v));   // halves away from zero, as C's lroundf
const mixColor = (a, b, t) => [16, 8, 0].reduce((n, s) =>   // a mixed into b by the share t of a, each channel rounded
  n | ((((b >> s) & 255) + roundAway((((a >> s) & 255) - ((b >> s) & 255)) * t)) << s), 0);
const gcShade = (fg, bg, [level, min]) => {   // level / 255 of fg into bg, raised one level at a time until it reaches min
  let c = mixColor(fg, bg, level / 255);
  while (level < 255 && contrast(c, bg) < min) c = mixColor(fg, bg, ++level / 255);
  return c;
};
/** Return the card's colors as CSS custom properties for a background ("theme" or "#RRGGBB") and a text color. */
const gcCardPalette = (bg, fg) => {
  if (bg === "theme") {
    return "--gc-bg: var(--ha-card-background, var(--card-background-color, #1c1c1c)); " +
      "--gc-fg: var(--primary-text-color, #fff); --gc-fg2: var(--secondary-text-color, #888); " +
      "--gc-fg3: var(--secondary-text-color, #aaa); --gc-old: var(--secondary-text-color, #888); " +
      "--gc-btn: var(--secondary-background-color, #2a2a2a); --gc-box: var(--divider-color, #808080);";
  }
  const b = hexNum(bg), f = isNaN(fg) ? onColor(b) : fg, fg2 = hex(gcShade(f, b, GRAYS.fg2));
  // a value that is not current takes the labels' gray (the panels' TH_TEXT_LABEL)
  return `--gc-bg: ${hex(b)}; --gc-fg: ${hex(f)}; --gc-fg2: ${fg2}; --gc-fg3: ${hex(gcShade(f, b, GRAYS.fg3))}; ` +
    `--gc-old: ${fg2}; --gc-btn: ${hex(mixColor(f, b, 0.11))}; --gc-box: ${hex(gcShade(f, b, GRAYS.box))};`;
};
// Material Design Icons arrow-right (24 x 24), the wall panel's arrow image
const ARROW_PATH = "M4,11V13H16L10.5,18.5L11.92,19.92L19.84,12L11.92,4.08L10.5,5.5L16,11H4Z";

const hex = (n) => "#" + n.toString(16).padStart(6, "0");

let measureCtx = null;
const textWidth = (str, size = 14) => {
  try {
    measureCtx ??= document.createElement("canvas").getContext("2d");
    measureCtx.font = `${size}px ${FONT}`;
    return measureCtx.measureText(str).width;
  } catch { return str.length * size * 0.57; }
};
const textWidth14 = (str) => textWidth(str, 14);
// The x of a centered middle text on a bar of width w with a left text (width lw, from pad) and a right text (width
// rw, to w - pad): the bar's center when the text clears both by a pad, else the center of the room between them;
// null when that room is narrower than the text plus a pad on each side. Never over a side text.
const midX = (w, pad, lw, rw, mw) => {
  const l = pad + lw + pad, r = w - pad - rw - pad;   // the free room, a pad from each side text
  if (r - l < mw) return null;
  return w / 2 - mw / 2 >= l && w / 2 + mw / 2 <= r ? w / 2 : (l + r) / 2;
};
const fitSize = (str, size, maxW, min = 12) => {   // the largest font size up to `size` at which str fits maxW
  const w = textWidth(str, size);
  return w <= maxW ? size : Math.max(min, Math.floor(size * maxW / w));
};
const wrapText = (str, size, maxW) => {   // words into lines no wider than maxW (a number, or per line: (i) => px)
  const out = [], room = typeof maxW === "function" ? maxW : () => maxW;
  let line = "";
  String(str).split(" ").forEach((w) => {
    const t = line ? `${line} ${w}` : w;
    if (line && textWidth(t, size) > room(out.length)) { out.push(line); line = w; } else line = t;
  });
  if (line) out.push(line);
  return out;
};
// The GMI in mmol/mol from its % value and the mean glucose (mg/dL) it was calculated from: from the mean, as
// GlucoCraft's backend (units.gmi_mmol_mol); without the mean, from the % value by the same rule (gmiPctToMmol)
const gmiMm = (pct, meanMg) => (isFinite(gmiMmolFromMean(meanMg)) ? gmiMmolFromMean(meanMg) : round(gmiPctToMmol(pct)));
// GMI in the A1C unit setting from its % value and the mean glucose (mg/dL) it was calculated from. Cells have little
// room: [main value, second line or ""]
const gmiParts = (pct, meanMg) => {
  const mm = gmiMm(pct, meanMg);
  return GC.a1c === "mmol_mol" ? [String(mm), "mmol/mol"]
    : GC.a1c === "both" ? [`${nf(pct, 1)}%`, `${mm} mmol/mol`] : [`${nf(pct, 1)}%`, ""];
};
// The GMI in the wall panels' compact form (gc_units.h fmt_gmi), for a cell with the panel's room: "6.5%", "48"
// (mmol/mol, no room for the unit) or "6.5%/48" (both units)
const gmiCompact = (pct, meanMg) => (GC.a1c === "mmol_mol" ? String(gmiMm(pct, meanMg))
  : GC.a1c === "both" ? `${nf(pct, 1)}%/${gmiMm(pct, meanMg)}` : `${nf(pct, 1)}%`);
const gmiText = (pct, meanMg) => { const [a, b] = gmiParts(pct, meanMg); return GC.a1c === "both" ? `${a} (${b})` : b ? `${a} ${b}` : a; };
// GlucoCraft's disclaimer in its short form, the catalogue's disclaimer.short (const.py DISCLAIMER_SHORT, its
// first sentence in bold; the long form is in the Documentation), as
// [the first sentence, the rest]: the About page shows the first sentence as its title, as the panels do
const disclaimerParts = () => {
  const text = gcT("disclaimer.short"), m = /^<b>([^<]*)<\/b>\s*([\s\S]*)$/.exec(text);
  return m ? [m[1], m[2]] : ["", text];
};
// the About page's version line (the name and version of the card), as on the panels
const versionLine = () => gcT("card.about.version_line", { version: GCDC_VERSION });
// the About page's copyright line under the version line (the copyright notice and where to read the license,
// AGPL-3.0-only section 0, "Appropriate Legal Notices"), as on the panels; its line break is the catalogue's
const copyrightLines = () => gcT("card.about.copyright").split("\n");
// GlucoCraft's icon on the About page (top-left corner, the text wrapped around it), as on the panels:
// brand/icon@2x.png at 192 px (LANCZOS), 128 colors (the square and rectangular cards)
const ICON_URI = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAMAAAADACAMAAABlApw1AAABgFBMVEWiHh4hl0rgX1nu3NT5j03qn5ima0wglUeiHCCjriuhHB8flUf0k2OjIyMGfyds5XVTvWb3eiX6j03XSzlYsGxwozT3x7i4XTxUwmj4j1G63MY1sz+EwZgilUzvgz4A//9VvWYAVVVVvWbzNzfaIxnzsSvpNiz/0Bwykjz/1xPpNS39xxr8xhvUf1X5w3QA/38AAABRuGPwiUqaGx77whrlNSsejkT+/v4jk0j/AAD//wCoKCRQwmj6vg/4xRnkNCnpLCQRiDqjHSD7kk3jJx2cHSCqAAD7xzEAqlUA/wAgj0X/qlVVqlX319P2pzH96rD+8tHJLCd/AABRqG35tyTaNSv75uX82G/8zUadICPRRjL/f3+UDA/xuLX+45cekkbK5NGr1LhyuYtV/1XzmztUwGf7kU3xl5JVwWf90VZ2l1V/f38Af3+hHSBOmjoglEdVwWj4jk33jU6IiE/93oYglEdVwmlnpVv/VVVVwmfQuSLra2PtdnChHCCazKv4lFCLIqeTAAAAgHRSTlMdIP//nv/+W1v+l5bv7AQJGwRn//D///9uGv8G/9f/AVgDqQ3/DLP//w1gYLMG/wIA/v7+/v7+//4BAf79/zAw/v/9/v/9A/8DAf4DA//+///+Av/////////9Av////n///8D/9JO/63//wIC0v/SjdEw//+qS/8DLv///6z/E+n0UTQAAAyvSURBVHja1Z2Je9NGGsYVmyQcOQhhWQql9IDdds+JfGRsoa0POcZJbAcDIYGEJEC5kl4QWtpw/Os7kg/NSCNpNPrGMdOnD6XNk7w/z/t9886ojDSkbty82f119bHCH6Kpk0/+HhsbO3t21mb45AAODlBj8s3Pebyw8MPz3xpof/aTAmjMIDR5BWNsAyzcuHH+N4QuX/10AIh7xt4Q9WTYADbC138g9PgTAbDln+nK7wMQhIVfziopBXCAma/QzcmdnnwXwJ6Fi6todX/EARrk45/8GQ/0UwAE4QdSCo8bowxAmZ8DQCbh+R/QPoIEOGigy2d2aPkeABuBlEJjdiQBHPdcwTv5MADbR1+SSbg6egB+9/ABCMLzs4AtVQNsnTs++VwAu6XOgpUCCMDBDLo8iTny+QA2wsWzQOlCA2udO3lxgF5LXW2MAkCA+SMA7FIASReaKvNHAgCVQjKAg/+isUkcLD8UwCmFWTS7f2wATmgOMr8IAEFwgnbjeABmQs0vBtBLF5ePAYAJzfIAvXQhXwqadGhuEPfk88kB7JZ6sSgdtDXZj58JzckAEgVtDbzzywCQfiQbtDWZ0Bza+aUA5IO2BpkbkgDIBm1NSe+RApBLF5pS+TEBZM4utFiHbTOT8eTHBXDOLuIFbU36vEENQOygralzjyRAzFLQQEIzLECsUhABOPhKwvxJAJygfVksXWiioVlOvyyAeNDWIHMDJEAvaD9OCCBbuwAATrqI3nOGAszcJOaPkRuAAZygfRBRClpEaL6S5ONPDCAQtDV17gEBsIN2aEvVQkLzZGL5AABOupgNDtoaSGhWCtBPF1fFAWDcAwcQli40pfLBAILThcbRPzMJJR8OwCmFMQ6BF6AB0DrVAPT2nI1wgAaaOQMoHxaAIPyy6iXwAOzPvIGUDw2wcOPrsf1GCMAqAtYPDUAIPHWgsfU7uZOPS7CM8XIeB/kOGmDhxpdsO6UBiuh69pQtJtZHnC+XyS/lcpxnZAm66cT9uUYwwD//nv3zL+SnLovrL+NHl3Z3dz/cLnPnDhjg9f1S7h/obQDAHPpXloxTP+WFfYTL59OL3bFHWBS30d//Wsrlcn9DBT5AEX2X7Y5TWBABlz9fHIw0jwBwIbs9kbP153Lf0AQaPQH/6wFkTTEfMfptgjxWFyUm7nbl53LfBwAUXYBs9s+fohFw+fYiM3bLy6rC3Mv7ffmCAAKlQPrVomd87jMRBjR/TACnFJZx2AR88AIs+kyEQdyTo/WLA4SXgs9ATisqY/At5ev7jPw4AKGlgMt7foDF22UMu6l/6ZUfDyC4pZIVgKPfNwU4qflzPv0xARwETkzgVQBnCjCk+SUBuOmC04K64wMcwOv7PPkSAL2WusxOwCPKN+eZRrQMcrj7cp4vXwrAXwrL+V3aNpfc3zwqY4Dj9fN898gDZLOMj5geultmf4eTP+BwcwMcANNSmR56vozLuwFljIFaJwQAlS6YErYzHN1TPyQD8OQGUIBBumAEOwGIBVqWf8y6EGJ+AIBeumAcZG8DmFWB9lDcB92v70bJTwjQLQX6A3eKlinqPVmAKPPDANg+oheBbtvEdFulPITjmV9Ef3IAgrDnTdAME+UhDGl+OIC6K/ZSVyymXbUXG+BGYG5QAmDe8y+8mF6NXQ/hpLlBDUDbvwcL8JDQ//T3u7B7gAD8Dgr0EE6aG1QAmC8YBw3SBc9DWDo0qwR46Cp1zy74HsJJc4MSC60NhLbdoM33EAZqnZAAtIPume7ZBddDOGluUAFAOahOBW2uh3DS3KDCQq6D1gZL87ceD32IAJAyPwyAueHKPDSZswtqW9PP1BjS/EAAhy7ABhu06cPqnodw0tygwEIcB/WD9hWfh3DS3AAPQC3DD01vSmU8xP+jiNzDtiEC0EFuwwuQPfR6CEOaH2YGAh1k09W9HsJJcwM4QJiDGLxFXw14zF+yrNLwAWgHvTBD/7PjIRx02GblUkdHR/OloUeJtHcZDpygPRrAE5qt+WdN54u2j4Yc5iIc5PHQch/Akxusd+uDrxrPDTNORzjIv9vEvNxgPaMP5Jtxl4VEM7AW7iBmina/dSzka52s/sXF9fnh7cgoeW0zcr9cJ0Hb3zqtce9DkebQZoDOQS/MSJM9JA93fLmhlPI/1kmXhjUDkQ5iJmkxm31i+LStcx5MPbOGAkDvxfg9yLPf+c8XupG5xRpom/toLTWUcyHa3xumwH6hqROATOUW3UD5zwbTQ5kByh1rYj6bsgEyGeNu/4fPr/MBFsct9QB0fR6aQl/W7AIMEBgDXWBm48hSPwMCJewt46npTG/YPrKOGNdcoxvquvIZoEu4bQo22+Z0rU9gl8I6W7fMhGxbqgHakYuAM+48maJkTWQGBLXpcV/npIneWUoB6O4SVsItXdcvUEYZAGxmJnyrL7uqub3U3itYlgUL0BYpYVu+rtNTkNra7H7+mc2mXyyTK5ruXuHZdnr72VHOggOgJyCwhO9U9e6gpU47c1CrbY1zuyb9pdsOVWmq/+/WuVlbEqAduQrfeaL3Bz0Fads9tc0tfsthTeQQXKAnJVWCAaCbe8AEtHRq0CK2bYItRii1D2PD9QV2SghqqgQzA2sRPbRVpfXrVSYnTG9lmAw9vkUHJEZxuhmZtWUA6ITGi0GUe7rjCzb0b7MiSVkbt8LydWjIiA9QZwzknwCzpftHOljSRGaTDkhBAW/QsJIC1JkO5K8Anny2jj0faa+xZoyAPWbEFMQGYPV7W9CdKle/10R0TTPpgr/LDNuvxQVg/ONdhH3mpwgucOU0N12AgY9K2yEA60ksZJobbea7MSnIbOlhg0fQNGj9A4TSODyAaZrZ+j1WPmugcPlcAp/+XtAOI5ADMDfuPWyveb/XmoD56eFVNb7p199rqVaqGQDQlAGot7nfqy5ifroOpuhuuj6R4env+aiUG0+DdaE6/7Nwl7CWLjimJ8Z7mT+d2tyq8fX3WqqVe8crm6P4AGY7XL+wfHIqsZUxJlKp1MR0ZisTKL/fUi3OqUtTIkrUQ/WLmN8FqJFVa4sMEkjD9PdLIRV9ZCQAsMFbTupmDPO7APZWYJP8FaG+Xwq+XOE/tJObgcP47ukDxBlGzpqKOnQUKWJv/2z3Pv5WVVcLQEqB7qdp3kZfpIhfsKvXhpR7pADIJFwjzcjuXOvpKe6mWGQG3Pyz1r7X+/TNlq4PAyBTM66VcqRxzZcs+VMJs37YbrcPX9RJopAzvzwAQSD9qJT0zw+YzoiTG+AA6A0byKmEjPmTAdAn2okBzJauDx8gGCEuQBL5iQAymVsAANLmhwDgl0IcgATmBwHg+igGQEvXjxvAPbuIDwAgHwKAeUgYAyCp+eEAvKUgBJDc/JAAbCl8H30pAIx7IAHoUgiege/4J82jAeCWQtC1DP2LMcDcAwzQL4XAizG6V5OYLV0fVYBuKQRfTVIsXM8Cy4cGsNPFN8HX86ygp9VRB6icREvBFyStoBPV0QaonGb1ewAKc9ehCWABKu+1TugdW0X0b2ACSIBK5bTGtCDONW1FhJ7+Wh1JgFpl+iTy6vdflFd4i+bOVaujB1CpnNQ8/g+4qnAFoY8n9OpoAVRqxD0c/dzLIgsE4RVUKRgw7gmQH3RdZ5EgnIMpBQiAyo/E/EuFWBemPiiij+cgfGTAmL/QiX1lreOj5AgGROvkuyfq0mC7FJ5OVY8VoNs6A9wTfW3zgxW0n7QUjITm76ClToKLs98W0GfJfGSoc4/Y1eVJW6ohL/99tHyRy+OLc4nShSFrfrt1dgog1/c/sNOFrI8MyYU30vyxXqBAfCRbCjIAlcxpEffEeoWFdCkYamo3/ktE7HQhUwqGlPmXCgj+NS4PHsiUgiGRGzodpOZFOjJB2wAJzVCvMpII2gZIaIZ7mVTxbcyAZICEZsjXedlBO4aPRAEq0/aGvYCG8UK1WKuCAZkboF5pV4ixYTMUtE6AlwqSdFE8J1QKhpD5C2K5AfK1jm8FW6oBu/BCvlhzzi6FakKArvk76FhebSoUtI3o1tkpoON6uSxJF59FpAtD+rxhOK/3jUwXBkhoVviC5ah0YYB3fvBXXIcHbQMyN6h6yTgphcBjPCNgxxgvNCt/zXtwKRiqzA8LEFwKhj83vIeTDwfQO9H2pwvDlzqBzA8O4ARtf0o1QELzcAC4J9qGgtapDqDbUhkfGSCheWgATtBeoVuqQZl/STo0DxHAu2EzQELzUAGclkqCdtUFUGF+hQCDoF11AGoVp3V2CujTAXDSRfHpiV9tgB/fkx1jp6PoB6kCcEoBfXz1StPsH7Gk7MeoAyClsNL9hyV18hH6P9vM1sZuofc8AAAAAElFTkSuQmCC";
const LH = 1.17;   // Roboto's line height (ascent + descent) per px of font size, as on the panels
const lhOf = (size) => Math.round(size * LH);
// Glucose in the display unit (glucocraft-shared.js gD, gF, gU; Home Assistant's unit pattern). Values are held in
// mg/dL (the files, the value_mgdl attributes); the unit shown is the Last glucose reading sensor's (attribute "unit").
// The 5-minute delta as shown: glucocraft-shared.js deltaShown / gDelta, the same rule on the dashboard and the panels.
const p0 = (v) => (v === null || v === undefined || isNaN(v)) ? "---" : `${nf(v, 0)}%`;
// the Very low and Very high cells: one decimal, a value that rounds to 100.0 as "100%" (gc_units.h pct1_cell: the
// square panel's narrow side column)
const p1 = (v) => (v === null || v === undefined || isNaN(v)) ? "---" : round(v, 1) >= 100 ? "100%" : `${nf(v, 1)}%`;
// a color is a hex number (fixed meaning) or a role of the card's colors ("fg", "fg2", "old", ...) -> CSS variable
const paint = (c) => typeof c === "number" ? `fill="${hex(c)}"` : `style="fill:var(--gc-${c})"`;

/** The square display card (custom:glucocraft-display-card), the base of the rectangular card. */
class GlucoCraftDisplayCard extends HTMLElement {
  get D() { return LAYOUT_SQUARE; }                 // the design layout
  get L() { return this._compactL || this.D; }     // the layout in use: the compact one below the design width
  /** Apply the card options and build the drawing, its keyboard buttons and pointer handlers. */
  setConfig(config) {
    // always the design layout, never the compact one (this.L): the stylesheet's max-width below is written only here,
    // so a compact width written into it would cap the card there and keep it compact after the screen grows wider.
    // The compact render sets its own viewBox, aspect ratio and keyboard boxes over the ones built here.
    const { W, H, NAV_H } = this.D;
    this._cfg = { ...DEFAULTS, ...(config || {}) };   // no "background" option: the background is GlucoCraft's setting
    this._page = 0;
    this._live = [];            // readings seen live since the last file load: [ts, mg/dL]
    this._lastUse = Date.now();
    this._bf = null; this._bfFetched = 0; this._delta = null; this._deltaFetched = 0;
    this._sig = null;           // a new configuration draws on the next hass
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._paletteCss = gcCardPalette(GC_CARD_BG, GC_CARD_FG);
    this.shadowRoot.innerHTML = `
      <style class="gc-palette">:host { ${this._paletteCss} }</style>
      <style>
        :host { display: block; }
        /* the drawing's box, inside the card's border, is at most the design width: drawn one unit per pixel */
        ha-card { background: var(--gc-bg); overflow: hidden; border-radius: var(--ha-card-border-radius, 12px);
                  width: 100%; max-width: calc(${W}px + 2 * var(--ha-card-border-width, 1px)); margin: 0 auto; }
        .disp { position: relative; width: 100%; aspect-ratio: ${W} / ${H + NAV_H}; touch-action: pan-y; user-select: none;
                -webkit-tap-highlight-color: transparent; }
        svg { display: block; width: 100%; height: 100%; }
        text { font-family: ${FONT}; }
        .btn { cursor: pointer; }
        .btn:hover rect { opacity: 0.8; }
        /* keyboard controls (docs/ACCESSIBILITY.md rule 5): real buttons over the drawn page buttons and the glucose
           block. They take no pointer events, so a click or a swipe reaches the drawing as before; the keyboard
           reaches them with Tab and uses them with Enter or Space, and the focused one is outlined. */
        .kb { position: absolute; padding: 0; border: none; border-radius: 10px; background: transparent;
              pointer-events: none; }
        /* the focus ring of every GlucoCraft card: 3 px in the text color (here the card's, on its own background) */
        .kb:focus-visible { outline: 3px solid var(--gc-fg); outline-offset: 2px; }
        /* the glucose block spans the card's width: its ring is drawn inside it, so that the card's edge does not cut
           it off, and scrolling to it keeps it clear of the dashboard's toolbar */
        .kb[data-kb="block"]:focus-visible { outline-offset: -4px; }
        .kb { scroll-margin-top: calc(var(--header-height, 56px) + 8px); }
        .kb[hidden] { display: none; }
        /* text read by screen readers only: not shown, but in the reading order */
        ${GC_CSS.sr}
        /* every text of the drawing keeps its spaces (with xml:space="preserve"), so that a text element needs no
           style attribute of its own besides its color */
        svg text { white-space: pre; }
        /* the throb of the words in the value's place and of the status bar's expiry text (GC_FLASH; the delay that
           keeps its phase across redraws: _phase) */
        @keyframes gc-throb { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
        .gc-throb { animation: gc-throb ${THROB_MS}ms ease-in-out infinite; }
        @media (prefers-reduced-motion: reduce) { .gc-throb { animation: none; } }
        /* two texts taking turns in the status bar's middle slot, each shown for TURN_MS (_phase keeps the turn) */
        @keyframes gc-turn { 0% { opacity: 1; } 50%, 100% { opacity: 0; } }
        .gc-turn0, .gc-turn1 { animation: gc-turn ${2 * TURN_MS}ms step-end infinite; }
      </style>
      <ha-card><div class="disp"><svg viewBox="0 0 ${W} ${H + NAV_H}" xmlns="http://www.w3.org/2000/svg"
        role="img"></svg><p class="sr" data-desc></p>
        <button type="button" class="kb" data-kb="block"></button><button type="button" class="kb" data-kb="prev"></button>
        <button type="button" class="kb" data-kb="next"></button><div class="sr" data-live aria-live="polite"></div></div>
        </ha-card>`;
    const disp = this.shadowRoot.querySelector(".disp");
    // the pointer over the card counts as use: the page does not return to the 6-hour page meanwhile (_inUse)
    disp.addEventListener("pointerenter", () => { this._hover = true; });
    disp.addEventListener("pointerleave", () => { this._hover = false; });
    disp.addEventListener("pointerdown", (e) => { this._down = [e.clientX, e.clientY]; });
    disp.addEventListener("pointerup", (e) => this._pointerUp(e));
    // each keyboard button covers its drawn counterpart, positioned in percent of the drawing
    const box = (el, [x, y, w, h]) => Object.assign(el.style, { left: `${x / W * 100}%`, top: `${y / (H + NAV_H) * 100}%`,
      width: `${w / W * 100}%`, height: `${h / (H + NAV_H) * 100}%` });
    const kb = (k) => this.shadowRoot.querySelector(`[data-kb="${k}"]`);
    const [prevBox, nextBox] = navBoxes(W, H);
    box(kb("prev"), prevBox);
    box(kb("next"), nextBox);
    box(kb("block"), this._blockArea());
    kb("prev").addEventListener("click", () => this._go(this._page - 1));
    kb("next").addEventListener("click", () => this._go(this._page + 1));
    kb("block").addEventListener("click", () => this._toggleWeekly());
  }

  // the area of the drawing that opens the weekly pages from a graph page and returns from them: [x, y, w, h]
  _blockArea() { return [0, 96, 480, 160]; }
  // what a tap on that area does (the same rule for the pointer and the keyboard)
  _canOpenWeekly() { return !!this._bf?.trends?.length; }
  _toggleWeekly() {
    if (this._page <= 6) { if (this._canOpenWeekly()) this._go(7); }
    else if (this._page < ABOUT_PAGE) this._go(0);
  }

  /** Take the new Home Assistant state: settings, live readings, file downloads, and redraw when needed. */
  set hass(hass) {
    this._hass = hass;
    // nothing is drawn before the language catalogues are loaded (once per language); then the latest state is drawn
    if (!gcI18nReady(hass)) {
      this._i18nLoading ||= gcI18nLoad(hass).then(() => {
        this._i18nLoading = null;
        this._sig = null;
        this.hass = this._hass;
      });
      return;
    }
    const cur = hass.states[this._cfg.current], lastS = hass.states[this._cfg.last];
    // the display unit and the regional settings in use (glucocraft-shared.js settings: one installation)
    if (cur?.attributes?.unit) GC.mmol = cur.attributes.unit === "mmol/L";
    const ca = hass.states["sensor.glucocraft_configuration"]?.attributes || {};
    applyRegional(ca);
    // every time in Home Assistant's time zone (the clock, the axes, the time of the last reading), as on the panels
    setTimeZone(hass.config?.time_zone);
    // the range colors, the graph range colors and the card's colors in use; kept while the configuration sensor has no
    // attributes (GlucoCraft reloading)
    if (ca.zone_colors) ZC = gcZoneColorNumbers(ca.zone_colors);
    if (ca.unit) {
      BC = gcBandColorNumbers(ca.band_colors);
      const bg = ca.card_background;
      GC_CARD_BG = bg === "theme" || !isNaN(hexNum(bg)) ? bg : CARD_BG_DEFAULT;
      GC_CARD_FG = hexNum(ca.card_foreground);
      GC_OUTLINE = ca.text_outline === true;
      GC_FLASH = ca.flash_no_current_data !== false;
      GC_TREND_WORD = ca.trend_display === "word";
    }
    this._applyPalette();
    const ts = parseInt(lastS?.attributes?.last_reading_ts, 10), v = this._mgOf(cur);
    // a reading at the sensor's limit (LO / HI) has no value: never a point on a graph (its marks: _limitRuns)
    if (!cur?.attributes?.limit && ts > 0 && v > 0 && v <= GLU_MAX && (!this._live.length || this._live[this._live.length - 1][0] < ts)) {
      this._live.push([ts, v]);
    }
    // a change of unit, A1C unit, ranges, goals or weekly range set downloads the files at once (the weekly
    // descriptions, the GMI goal line and the targets are in the file); not while the configuration sensor has no
    // attributes (reload)
    if (ca.unit) {
      const setKey = JSON.stringify([ca.unit, ca.a1c_unit, ca.ranges, ca.goals, ca.goals_custom, ca.weekly_pages?.shown]);
      if (this._setKey !== undefined && setKey !== this._setKey) { this._bfFetched = 0; this._deltaFetched = 0; this._force = { backfill: true, delta: true }; }
      this._setKey = setKey;
    }
    const bfAge = Date.now() - this._bfFetched;
    if ((!this._bf && bfAge > 60 * 1000) || bfAge > 60 * 60 * 1000) this._load("backfill");
    if (Date.now() - this._deltaFetched > 5 * 60 * 1000) this._load("delta");
    // redraw only when an entity this card reads has changed (every state change in Home Assistant sets hass)
    const c = this._cfg, ids = [c.current, c.trend, c.change, c.status, c.stale, c.source, c.last, c.expiry, c.warmup,
      c.ranges];
    if (!this._bf) ids.push(this._summary6h());   // the 6-hour statistics before the first file
    // a Repairs notice open or closed (the configuration's attention, kept while it has no attributes) redraws too
    if (ca.unit) this._attention = ca.attention === true;
    const sig = ids.map((e) => { const s = hass.states[e]; return s ? `${s.state}|${s.last_updated}` : "-"; }).join(";") +
      `;${GC.time};${GC.date};${GC.dec};${GC.a1c};${GC.mmol};${GC.tz};${!!this._bf};${Object.values(ZC).join(",")};${Object.values(BC).join(",")}` +
      `;${GC_OUTLINE};${GC_FLASH};${GC_TREND_WORD};${!!this._attention}`;
    if (sig === this._sig) return;
    this._sig = sig;
    this._redraw();
  }

  // No 1-second timer: the card redraws when its entities or files change, when it is used, on
  // the minute (clock, the reading's age, countdowns), and at the moment the data stop being current (the no-current-data
  // threshold) or the page returns to 6 h after IDLE_RESET_S.
  _redraw() {
    if (!gcI18nReady(this._hass)) return;   // set hass draws once the catalogues are loaded
    this._render();
    this._schedule();
  }
  // the card's colors (GlucoCraft's settings)
  _applyPalette() {
    const css = gcCardPalette(GC_CARD_BG, GC_CARD_FG);
    if (css === this._paletteCss) return;
    this._paletteCss = css;
    const el = this.shadowRoot?.querySelector("style.gc-palette");
    if (el) el.textContent = `:host { ${css} }`;
  }
  /** Schedule the next redraw: the next minute, the moment the data stop being current, or the idle reset. */
  _schedule() {
    clearTimeout(this._timer);
    if (!this.isConnected || !this._hass) return;
    const now = Date.now();
    let next = Math.floor(now / 60000) * 60000 + 60000;                     // the next whole minute
    const s = this._state();
    if (!isNaN(s.age)) {   // the moment the data stop being current (the no-current-data threshold, s.thr minutes)
      const at = now + (s.thr - s.age) * 60000;
      if (at > now) next = Math.min(next, at);
    }
    const idle = gcIdleReturn(this._cfg);
    if (idle && this._page !== 0) next = Math.min(next, this._lastUse + IDLE_RESET_S * 1000);
    this._timer = setTimeout(() => {
      if (this._inUse()) this._lastUse = Date.now();   // still being read: the idle time starts again
      if (idle && this._page !== 0 && Date.now() - this._lastUse > IDLE_RESET_S * 1000) this._page = 0;
      this._redraw();
    }, Math.max(0, next - now) + 50);                                       // just past the moment
  }
  // The card is in use while one of its buttons has the focus (a keyboard or screen-reader user is on it) or the
  // pointer is over it: the page shown then stays, also after IDLE_RESET_S (WCAG 2.2.1)
  _inUse() { return !!this.shadowRoot?.activeElement || !!this._hover; }

  // Below its design width the card does not shrink its drawing (text would fall below 14 px): it draws the compact
  // layout at the width it has, one pixel per pixel, with its parts stacked (_compactRender).
  connectedCallback() {
    this._ro ||= typeof ResizeObserver === "function" ? new ResizeObserver(() => this._resized()) : null;
    this._ro?.observe(this);
    this._redraw();
  }
  disconnectedCallback() { clearTimeout(this._timer); this._ro?.disconnect(); }
  // a new width: the compact layout below the design width (at least COMPACT_MIN_W px, below which the drawing is
  // scaled), else the design drawing. The width is that of the drawing's own box (.disp, inside the card's border), so
  // that the compact layout's view box is exactly as wide as the drawing is shown; before the box is laid out, the
  // card's width less its default 1 px border on each side
  _resized() {
    // a card given exactly its design width (a dashboard column of 480 px) has no room left for its 1-px border: it
    // drops the border, so that the drawing keeps its full width and the design layout (with the border its box was
    // 2 px short, and the card fell back to the compact layout at every width)
    const outer = Math.floor(this.getBoundingClientRect?.().width || 0), haCard = this.shadowRoot?.querySelector("ha-card");
    if (haCard && outer) haCard.style.borderWidth = outer >= this.D.W && outer < this.D.W + 2 ? "0px" : "";
    const box = this.shadowRoot?.querySelector(".disp")?.getBoundingClientRect?.().width;
    const w = Math.floor(box || Math.max(0, (this.getBoundingClientRect?.().width || 0) - 2));
    if (!w) return;
    const compact = w < this.D.W ? Math.max(COMPACT_MIN_W, w) : 0;
    if (compact === (this._compactW || 0)) return;
    this._compactW = compact;
    this._compactL = null;
    this._slotCache = null;
    if (!compact) this._restoreDesign();
    this._redraw();
  }
  // back to the design drawing: its view box, aspect ratio and keyboard buttons
  _restoreDesign() {
    const { W, H, NAV_H } = this.D, root = this.shadowRoot;
    if (!root) return;
    root.querySelector("svg")?.setAttribute("viewBox", `0 0 ${W} ${H + NAV_H}`);
    const disp = root.querySelector(".disp");
    if (disp) disp.style.aspectRatio = `${W} / ${H + NAV_H}`;
    this._placeKb(W, H + NAV_H, ...navBoxes(W, H), this._blockArea());
  }
  // the keyboard buttons over their drawn counterparts, in percent of a drawing of w x h
  _placeKb(w, h, prev, next, block) {
    const box = (k, [x, y, bw, bh]) => {
      const el = this.shadowRoot?.querySelector(`[data-kb="${k}"]`);
      if (el) Object.assign(el.style, { left: `${x / w * 100}%`, top: `${y / h * 100}%`, width: `${bw / w * 100}%`, height: `${bh / h * 100}%` });
    };
    box("prev", prev); box("next", next); box("block", block);
  }
  getCardSize() { return 9; }
  getGridOptions() { return { columns: "full", min_columns: 6 }; }
  static getStubConfig() { return {}; }
  // The card editor (Home Assistant's form): the one card option a user sets, Return to the first page after 3 minutes
  // (on by default); the entity overrides stay YAML-only
  static getConfigForm() {
    const text = (key) => (globalThis.glucocraftI18n?.t ? gcT(key) : undefined);   // the catalogue, once loaded
    return {
      schema: [{ name: "return_to_first_page", default: true, selector: { boolean: {} } }],
      computeLabel: (s) => (s.name === "return_to_first_page" ? text("card.editor.return_to_first_page") : undefined),
      computeHelper: (s) => (s.name === "return_to_first_page" ? text("card.editor.return_to_first_page_help")
        : undefined),
    };
  }

  // ------------------------------------------------------------------ navigation
  _go(p) {
    this._page = ((p % N_PAGES) + N_PAGES) % N_PAGES;
    this._lastUse = Date.now();
    this._redraw();
    this._announce();
  }
  // A page change, chosen by the user, is announced through a live region that exists, empty, from the start: the
  // page with its number, then the page's own sentence (a text page: its title). Focus stays on the button used.
  _announce() {
    const live = this.shadowRoot?.querySelector("[data-live]");
    if (!live || !gcI18nReady(this._hass)) return;
    const p = this._page;
    const what = p === ABOUT_PAGE ? "" : p >= 7 ? this._describeWeekly(p - 7) : this._describeGraph(p);
    const text = gcT("card.a11y.page_shown", { page: p + 1, pages: N_PAGES, name: pageName(p) }) +
      (what ? gcT("card.describe.separator") + what : "");
    // the same text again (a cycle back to the same page) is announced again: it differs by a no-break space
    live.textContent = text === live.textContent ? `${text}\u00a0` : text;
  }

  /** Handle a tap or swipe: the page buttons, a swipe to the next page, or the glucose block. */
  _pointerUp(e) {
    if (this._compact()) return this._compactPointerUp(e);
    const { W, H, NAV_H } = this.L;
    if (!this._down) return;
    const dx = e.clientX - this._down[0], dy = e.clientY - this._down[1];
    this._down = null;
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) { this._go(this._page + (dx < 0 ? 1 : -1)); return; }
    if (Math.abs(dx) > 10 || Math.abs(dy) > 10) return;
    const rect = this.shadowRoot.querySelector("svg").getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width * W, y = (e.clientY - rect.top) / rect.height * (H + NAV_H);
    if (y >= H) {
      if (x < NAV_BTN.hit) this._go(this._page - 1); else if (x > W - NAV_BTN.hit) this._go(this._page + 1);
      return;
    }
    if (this._blockHit(x, y)) this._toggleWeekly();
    this._lastUse = Date.now();
  }
  // a tap inside the glucose block (_blockArea), which opens the weekly pages and returns from them
  _blockHit(x, y) { const [bx, by, bw, bh] = this._blockArea(); return x >= bx && x < bx + bw && y >= by && y < by + bh; }

  // ------------------------------------------------------------------ data (GLU2 v3: panel_feed.py, glucocraft_panel_binary.h)
  /**
   * Get one GLU2 file (backfill or delta) from the page's file cache (gcFile, glucocraft-shared.js: one download for
   * every card on the page, which logs a failed download) and redraw; after a change of the settings the files carry,
   * downloaded at once. A file that arrives but cannot be used here is logged once per reason (gcNewFailure).
   */
  async _load(which) {
    const key = which === "delta" ? "_deltaLoading" : "_bfLoading";
    if (!this._hass || this[key]) return;
    this[key] = true;
    const force = !!this._force?.[which];
    if (force) this._force[which] = false;
    if (which === "delta") this._deltaFetched = Date.now(); else this._bfFetched = Date.now();
    try {
      const parsed = await gcFile(this._hass, which, { url: this._cfg.file_url, force });
      if (!parsed) throw new Error(which);
      if (which === "delta") this._delta = parsed;
      else { this._bf = parsed; this._bf.at = parsed.fetched; }
      const newest = Math.max(this._bf?.newest || 0, this._delta?.newest || 0);
      this._live = this._live.filter(([t]) => t > newest);      // the files now hold these
      gcNewFailure(`card|${which}`, null);
    } catch (e) {
      // no file (gcFile has logged why), or a failure in this card
      if (e?.message !== which && gcNewFailure(`card|${which}`, String(e?.message || e))) {
        console.warn(`GlucoCraft display card: file ${which} not used`, e);
      }
      // No recent-readings file (none during an outage of more than 2 hours, or Home Assistant unreachable): its old
      // 6 h / 24 h numbers go and the full file's show (refreshed hourly), as on the panels, never numbers frozen from
      // before the gap. A failed full file keeps the last good one and is tried again in a minute (the first one, and
      // a refresh while GlucoCraft reloads after a settings change), not in an hour.
      if (which === "delta") this._delta = null;
      else if (this._bf) this._bfFetched = Date.now() - 60 * 60 * 1000 + 60 * 1000;
    } finally { this[key] = false; this._redraw(); }
  }


  // readings in [start, end] from the full file, the delta and the live values, by timestamp, no duplicates;
  // before: also up to that many readings of each file before start (the gap rule's context)
  _readings(start, end, before = 0) {
    const out = [];
    const add = (f) => { if (!f) return; for (let i = Math.max(0, first(f.ts, start) - before); i < f.ts.length && f.ts[i] <= end; i++) {
      if (f.val[i] > 0 && f.val[i] <= GLU_MAX) out.push([f.ts[i], f.val[i]]); } };
    add(this._bf); add(this._delta);
    this._live.forEach(([t, v]) => { if ((before || t >= start) && t <= end) out.push([t, v]); });
    out.sort((a, b) => a[0] - b[0]);
    return out.filter((r, i) => i === 0 || r[0] !== out[i - 1][0]);
  }

  /**
   * Return the slots of a period graph (mean, minimum, maximum, first and last reading time) and its gaps.
   *
   * average + min/max per pixel slot, by timestamp; the window ends when the file was fetched. gaps: the gaps of the
   * window's readings by the gap rule (fb = its fallback threshold), with the 11 readings before the window as context
   */
  _periodSlots(p, fb) {
    const { GW } = this.L;
    const bf = this._bf;
    if (this._slotCache?.bf === bf && this._slotCache.fb === fb && this._slotCache.gw === GW && this._slotCache[p]) return this._slotCache[p];
    if (this._slotCache?.bf !== bf || this._slotCache.fb !== fb || this._slotCache.gw !== GW) this._slotCache = { bf, fb, gw: GW };
    const win = PERIOD_HOURS[p] * 3600, start = bf.at - win;
    const sum = new Float64Array(GW), cnt = new Uint32Array(GW), mn = new Uint16Array(GW).fill(999), mx = new Uint16Array(GW);
    const ft = new Uint32Array(GW), lt = new Uint32Array(GW);   // first and last reading time of each slot (gap rule)
    const tsAll = [];
    const i0 = first(bf.ts, start);
    for (let i = Math.max(0, i0 - GAP_WINDOW - 1); i < bf.ts.length; i++) {
      const t = bf.ts[i], v = bf.val[i];
      if (t > bf.at) break;
      if (v === 0 || v > GLU_MAX) continue;
      if (!tsAll.length || tsAll[tsAll.length - 1] !== t) tsAll.push(t);   // the same time twice: one interval of 0 s less
      if (i < i0) continue;                                          // context only
      let s = Math.floor((t - start) * GW / win); if (s < 0) s = 0; if (s >= GW) s = GW - 1;
      sum[s] += v; cnt[s]++; if (v < mn[s]) mn[s] = v; if (v > mx[s]) mx[s] = v;
      if (!ft[s]) ft[s] = t; lt[s] = t;
    }
    const avg = new Array(GW), lo = new Array(GW), hi = new Array(GW);
    // the slot's mean rounded to whole mg/dL, halves up (a truncated mean would draw the line up to 1 mg/dL low)
    for (let s = 0; s < GW; s++) {
      avg[s] = cnt[s] ? Math.floor(sum[s] / cnt[s] + 0.5) : 0; lo[s] = cnt[s] ? mn[s] : 0; hi[s] = cnt[s] ? mx[s] : 0;
    }
    return (this._slotCache[p] = { avg, lo, hi, ft, lt, gaps: gcGaps(tsAll, fb) });
  }

  // ------------------------------------------------------------------ helpers
  _st(id) { return this._hass?.states[id]; }
  _mgOf(st) {   // a glucose entity's value in mg/dL: its attribute value_mgdl (NaN without a value)
    const a = st?.attributes?.value_mgdl;
    return a !== undefined && a !== null ? parseFloat(a) : NaN;
  }
  _mg(id) {     // like _num: the last known value survives "unavailable" (a restart of Home Assistant)
    const v = this._mgOf(this._st(id));
    if (!isNaN(v)) { (this._lastMg ||= {})[id] = v; return v; }
    return this._lastMg?.[id] ?? NaN;
  }
  _num(id, keepUnknown = true) {   // the last known number survives "unavailable" (a restart), like the panel;
    const s = this._st(id); const v = s ? parseFloat(s.state) : NaN;   // keepUnknown=false: "unknown" = no value
    if (!isNaN(v)) { (this._lastNum ||= {})[id] = v; return v; }
    if (!keepUnknown && s?.state === "unknown") { if (this._lastNum) delete this._lastNum[id]; return NaN; }
    return this._lastNum?.[id] ?? NaN;
  }
  // The 6-hour summary sensor of the range set in use, as on the panels: <stats_prefix>custom_ranges while the optional
  // feature Custom ranges and goals is on (the configuration sensor's custom_ranges_and_goals), otherwise
  // <stats_prefix>consensus_ranges; the last known choice is kept while the configuration sensor has no attributes.
  _summary6h() {
    const on = this._st("sensor.glucocraft_configuration")?.attributes?.custom_ranges_and_goals;
    if (typeof on === "boolean") this._customOn = on;
    return `${this._cfg.stats_prefix}${this._customOn ? "custom" : "consensus"}_ranges`;
  }
  // One 6-hour statistic of that sensor (SUMMARY_6H key); the last known value survives "unavailable" (a restart of
  // Home Assistant)
  _summaryNum(key) {
    const id = this._summary6h(), v = parseFloat(this._st(id)?.attributes?.[SUMMARY_6H[key][this._customOn ? 1 : 0]]);
    const k = `${id}|${key}`;
    if (!isNaN(v)) { (this._lastNum ||= {})[k] = v; return v; }
    return this._lastNum?.[k] ?? NaN;
  }
  // the minimum or the maximum of that sensor (k "min" / "max"), a measured value, as the panels show it
  _summaryExtreme(k) {
    const v = this._summaryNum(k);
    return v > 0 ? gF(v) : "---";
  }
  // unavailable = keep the last value (a restart of Home Assistant); anything else = GlucoCraft's value (may be none)
  _keep(key, s, value) {
    this._kept ||= {};
    if (s?.state === "unavailable" || s === undefined) return this._kept[key] ?? null;
    return (this._kept[key] = (value === undefined ? null : value));
  }
  // GlucoCraft's ranges (custom when set), the consensus ranges as the fallback (the default, CONS_RANGES); the last
  // known ranges survive "unavailable" (GlucoCraft reloading, a restart of Home Assistant), as on the panels
  _ranges() {
    const st = this._st(this._cfg.ranges), a = st?.attributes || {}, C = CONS_RANGES;
    const n = (k) => { const v = parseFloat(a[`range_${k}`]); return isNaN(v) ? C[k] : v; };
    return this._keep("ranges", st, { cmin: n("very_low"), cmax: n("very_high"),
      imin: n("tight_low"), imax: n("tight_high"), nmin: n("low"), nmax: n("high") })
      || { cmin: C.very_low, cmax: C.very_high, imin: C.tight_low, imax: C.tight_high, nmin: C.low, nmax: C.high };
  }
  // cut to maxPx at 14 px with "...", like the panel's DOT label (111 px); by whole characters (code points), so that
  // a character outside the Basic Multilingual Plane is never cut in half
  _fit14(str, maxPx) {
    if (!str) return "";
    if (textWidth14(str) <= maxPx) return str;
    const chars = Array.from(str);
    while (chars.length > 1 && textWidth14(chars.join("") + "...") > maxPx) chars.pop();
    return chars.join("") + "...";
  }
  _t(x, y, str, size, color, anchor = "middle", baseline = "central", extra = "") {
    return `<text x="${x}" y="${y}" font-size="${size}" ${paint(color)} text-anchor="${anchor}" ` +
           `dominant-baseline="${baseline}" xml:space="preserve" ${extra}>${esc(str)}</text>`;
  }
  // Texts last (a grid line drawn after a label would cover part of it): the graph's texts (value and time labels,
  // title, legend) moved after all its shapes so far, in their order, at their positions; the same order as on the
  // panels (lvgl_compat.h lvc_end), so that no grid line, border or band covers a label
  _textsLast(svg) {
    const texts = [];
    const shapes = svg.replace(/<text\b[^>]*>[^<]*<\/text>/g, (t) => { texts.push(t); return ""; });
    return shapes + texts.join("");
  }
  // A text that takes the 1-pixel edge of the setting Outline text (gcEdge): every graph label and every text in a
  // zone color; plain while the setting is off
  _ot(x, y, str, size, color, anchor = "middle", baseline = "central", extra = "") {
    const edge = GC_OUTLINE ? gcEdge(color) : "";
    return this._t(x, y, str, size, color, anchor, baseline, edge && extra ? `${edge} ${extra}` : edge || extra);
  }

  /** Collect everything the card shows from GlucoCraft's entities, with the card's own clock rules applied. */
  _state() {
    const c = this._cfg, now = new Date(), r = this._ranges();
    const lv = this._mg(c.current);
    const src = this._st(c.source);
    // The reading's time, the stated expiry, the warm-up's start and the no-current-data limit are kept while
    // GlucoCraft's entities are unavailable (GlucoCraft not loaded, reloading or disabled), like the value: the card
    // keeps counting the age of the value it shows and shows "No current data" in its place past the limit.
    const { untrusted, age, ageMin } = this._readingAge(now);
    const ncA = this._st(c.stale), thrA = parseFloat(ncA?.attributes?.threshold_minutes);
    const thr = this._keep("thr", ncA, thrA > 0 ? thrA : null) ?? 15;                  // GlucoCraft's setting, default 15
    const exS = this._st(c.expiry), expAt = this._keep("expiresAt", exS, exS?.attributes?.expires_at || null);
    const remain = expAt ? Math.floor((Date.parse(expAt) - now) / 60000) : NaN;          // none / unknown -> NaN
    const wu = this._st(c.warmup), wuAt = this._keep("warmUpStart", wu, wu?.attributes?.started_at || null);
    const wuStart = wuAt ? Date.parse(wuAt) : NaN;
    const custom = !!this._keep("custom", this._st(c.ranges), this._st(c.ranges)?.state === "on");   // kept like the ranges
    const { noCurrent, status, statusColor } = this._statusOf(age, thr);
    const { word, angle, trendWord, change, zone, zoneColor, zoneWord, slotWord } = this._trendAndZone();
    // the source's name: kept while Home Assistant reports the entity unavailable (GlucoCraft restarting or reloading)
    // or it is missing, as on the panels. "unknown" clears it: the Active source sensor has no state (None, which Home
    // Assistant shows as "unknown") only while no source is known, for example after the source was changed in the
    // settings and before its first reading (GlucoCraft restores the saved name at a restart).
    const srcState = src?.state;
    if (srcState !== undefined && srcState !== "unavailable") {
      (this._kept ||= {}).source = srcState === "unknown" ? "" : srcState;
    }
    const source = this._kept?.source ?? "";
    const interval = parseInt(src?.attributes?.detected_interval, 10) || 60;
    const { lim, limitText, since, lastMeasured, lastLimit, lastMg } = this._limitReading();
    // the gap rule's fallback threshold (GlucoCraft rule): max(15 min, 3 x reading interval), used where a stretch has
    // fewer than 3 intervals around it (the graphs judge each stretch by its own readings: gcGaps)
    const ca = this._st("sensor.glucocraft_configuration")?.attributes || {};
    const gapA = parseFloat(ca.gap_threshold_seconds), ivA = parseFloat(ca.reading_interval_seconds);
    const gap = gapA > 0 ? gapA : gcGapThreshold(ivA > 0 ? ivA : interval);
    // old: no current data (also during a warm-up); stale: no current data outside a warm-up; words: no current reading,
    // the words in the value's place, never a number (NO_READING_KEYS: "warming_up" during a warm-up, without
    // current data "sensor_expired" after the stated expiry, else "no_current_data"; the panels' gc_units.h
    // no_reading_words). After the stated expiry, while readings still arrive, the value keeps its zone color: the red
    // status bar carries the expiry.
    const warmup = status === "warm_up", expired = status === "expired", stale = noCurrent && !warmup;
    const words = warmup ? "warming_up" : !noCurrent ? null : expired ? "sensor_expired" : "no_current_data";
    const throb = this._throbs(now, words, warmup, wuStart, age, thr);
    // the status bar's "Sensor expiry passed" throbs for as long as it shows (the setting turns it off as well)
    const expiryThrob = GC_FLASH && (expired || (status === "expiring_soon" && remain <= 0));
    return { now, r, lv, age, ageMin, thr, stale, old: noCurrent, words, throb, expiryThrob, untrusted,
             remain, expired, warmup, wuStart, custom, source, interval, gap, status, statusColor, word, angle,
             trendWord, change, zoneColor, zone, zoneWord, slotWord, attention: !!this._attention,
             limit: lim ? lim[0] : null, limitText, since: since > 0 ? since : NaN, lastMeasured, lastLimit, lastMg };
  }
  /**
   * Return the age of the newest reading at now: { untrusted, age (minutes, NaN when unknown), ageMin (whole minutes) }.
   *
   * From the reading's own time (kept while unavailable). A reading time more than 5 minutes ahead of the clock cannot
   * be trusted (GlucoCraft's attribute time_untrusted, or the card's own count): no age is shown then, "Reading: ---"
   * on the status bar and no time line under the words (the Maintenance page explains it while it lasts). Without a
   * reading time at all (the Last reading time sensor without its attribute during a reload, or that entity missing,
   * as with an entity override that names none), the time the value last changed; not while unavailable.
   */
  _readingAge(now) {
    const c = this._cfg, lastS = this._st(c.last);
    const last = parseInt(this._keep("lastTs", lastS, lastS?.attributes?.last_reading_ts ?? null), 10);
    const lag = last > 0 ? now.getTime() / 1000 - last : NaN;                          // seconds, from the reading's own time
    const untrusted = !!this._keep("untrusted", lastS, lastS?.attributes?.time_untrusted === true) || lag < -300;
    let age = !untrusted && lag >= -300 ? Math.max(0, lag) / 60 : NaN;
    const curNow = this._st(c.current);
    if (isNaN(age) && !untrusted && !isNaN(parseFloat(curNow?.state)) && curNow?.last_changed) {
      age = Math.max(0, (now - Date.parse(this._st(c.current).last_changed)) / 60000);
    }
    return { untrusted, age, ageMin: isNaN(age) ? NaN : Math.floor(age) };
  }
  /**
   * Return { noCurrent, status, statusColor } for a reading age minutes old and the no-current-data limit thr.
   *
   * No current data: GlucoCraft's flag, the one fact (kept while Home Assistant restarts), or the card's own count of
   * the reading's age past the same limit (Home Assistant cannot update the flag then). Never the status, whose
   * priority puts an expired sensor first: the status decides only the status bar. The status is GlucoCraft's (one
   * system); the card's own clock only adds "no current data" when Home Assistant cannot update it.
   */
  _statusOf(age, thr) {
    const c = this._cfg, ncS = this._st(c.stale);
    let noCurrent = !!this._keep("noCurrent", ncS, ncS ? ncS.state === "on" : null) || (!isNaN(age) && age > thr);
    const stS = this._st(c.status);
    let status = this._keep("status", stS, stS?.state) || "no_data";
    let statusColor = hexNum(this._keep("statusColor", stS, stS?.attributes?.color));
    if (status !== "warm_up" && status !== "expired" && !isNaN(age) && age > thr) { status = "no_current_data"; statusColor = C.alert; }
    // Never green without a value: GlucoCraft answers and calls the reading current, but the reading's entity is
    // missing or has no value (its id was changed, renamed_entities.py). The card then shows no current reading; a value
    // kept from earlier is only the last reading. Not while GlucoCraft's entities are unavailable (the value is kept).
    if ((status === "current" || status === "expiring_soon") && stS && stS.state !== "unavailable" && isNaN(this._mgOf(this._st(c.current)))) {
      noCurrent = true; status = "no_current_data"; statusColor = C.alert;
    }
    if (isNaN(statusColor)) statusColor = C.unknown;
    return { noCurrent, status, statusColor };
  }
  /**
   * Return the trend and the zone of the newest reading (each kept while unavailable): { word (the cited trend word),
   * angle, trendWord (the TREND column's word), change (the 5-minute delta, mg/dL), zone, zoneColor, zoneWord,
   * slotWord (the word under the value) }.
   */
  _trendAndZone() {
    const c = this._cfg, trS = this._st(c.trend), chS = this._st(c.change), cuS = this._st(c.current);
    const word = this._keep("word", trS, trS?.attributes?.description === "Unknown" ? null : trS?.attributes?.description);
    const angle = this._keep("angle", trS, word ? trS?.attributes?.angle : null);
    // the trend word as the TREND column shows it (GlucoCraft's attribute word, in the language in use)
    const tw = trS?.attributes?.word;
    const trendWord = this._keep("trendWord", trS, word && typeof tw === "string" ? tw || null : null);
    const chMg = this._mgOf(chS);
    const change = this._keep("change", chS, isNaN(chMg) ? null : chMg);
    // the zone and its color from GlucoCraft (one system); the published palette only when zone_color is missing
    const zone = this._keep("zoneName", cuS, cuS?.attributes?.zone);
    let zoneColor = hexNum(this._keep("zone", cuS, cuS?.attributes?.zone_color));
    if (isNaN(zoneColor) && ZC[zone] !== undefined) zoneColor = ZC[zone];
    // the zone word (zone_label outside the range, else the catalogue's word for the zone); slotWord below is what the
    // card shows
    const label = cuS?.attributes?.zone_label;
    const zoneWord = ZONE_KEYS[zone]   // (kept while the entity is unavailable, like the value)
      ? this._keep("zoneWord", cuS, typeof label === "string" && label ? label : gcT(ZONE_KEYS[zone][0])) : null;
    // The zone word under the value, in every setting: GlucoCraft publishes it as display_word (empty without current
    // data); without the attribute, the zone word above.
    const dw = cuS?.attributes?.display_word;
    const slotWord = this._keep("slotWord", cuS, typeof dw === "string" ? dw || null : zoneWord);
    return { word, angle, trendWord, change, zone, zoneColor, zoneWord, slotWord };
  }
  /**
   * Return the newest reading at the sensor's limit, and the last reading's own value (each kept while unavailable):
   * { lim: [limit, its text] or null, limitText, since, lastMeasured, lastLimit, lastMg }.
   *
   * LO / HI: a reading outside the sensor's range is shown as its text ("LO" / "HI"), never as the stored limit value
   * (attributes limit, limit_text). Its zone is GlucoCraft's like any other: a LO reading is always very low and a HI
   * reading always very high, because the very-low and the very-high limit stay within the sensor's limits.
   */
  _limitReading() {
    const c = this._cfg, cuS = this._st(c.current), lastS = this._st(c.last);
    const lim = this._keep("limit", cuS, cuS?.attributes?.limit === "low" || cuS?.attributes?.limit === "high"
      ? [cuS.attributes.limit, cuS.attributes.limit_text || gcT(cuS.attributes.limit === "low" ? "card.limit.low" : "card.limit.high")]
      : null);
    const limitText = lim ? lim[1] : null;
    // A stretch of limit readings (GlucoCraft's attributes, kept while unavailable): since when (limit_since, the first
    // reading of the unbroken stretch) and the last measured value before it (last_measured_mgdl at last_measured_ts)
    const ca0 = cuS?.attributes || {};
    const since = parseInt(this._keep("limitSince", cuS, lim ? ca0.limit_since ?? null : null), 10);
    const lastMeasured = this._keep("lastMeasured", cuS, lim && ca0.last_measured_mgdl != null
      ? [parseFloat(ca0.last_measured_mgdl), parseInt(ca0.last_measured_ts, 10)] : null);
    // The newest reading's own value for the line "Last reading" under the words: the Last reading time sensor's
    // last_reading_mgdl (and last_reading_limit, LO / HI), which belong to the reading whose time the line shows; else
    // the value kept from the Last glucose reading sensor
    const la = lastS?.attributes || {};
    const lastLimit = ["low", "high"].includes(la.last_reading_limit) ? la.last_reading_limit : null;
    const lastMgA = parseFloat(la.last_reading_mgdl);
    const lastMg = this._keep("lastMg", lastS, isFinite(lastMgA) ? lastMgA : null);
    return { lim, limitText, since, lastMeasured, lastLimit, lastMg };
  }
  /**
   * Return whether the words in the value's place throb at now (words: the words shown, or null).
   *
   * The words fade (GlucoCraft's setting) for the first THROB_EPISODE_MIN minutes of the episode: a warm-up from its
   * start (wuStart), no current data from the moment the reading's age (minutes) passed the limit thr (0 while the
   * flag is on within the limit); an episode whose start cannot be counted (no warm-up start, no reading or a reading
   * time ahead of the clock) from the moment the card first showed it, as the panels count it from their start.
   */
  _throbs(now, words, warmup, wuStart, age, thr) {
    const kind = !words ? null : warmup ? "warm_up" : "no_data";
    if (kind !== this._episode?.kind) this._episode = kind ? { kind, at: now.getTime() } : null;
    let episode = !words ? NaN : warmup ? (now - wuStart) / 60000 : isNaN(age) ? NaN : Math.max(0, age - thr);
    if (words && isNaN(episode)) episode = (now.getTime() - this._episode.at) / 60000;
    return !!words && GC_FLASH && episode < THROB_EPISODE_MIN;
  }

  // The glucose value in its range color on the card's background, as on the wall panels (the settings screen states
  // the contrast of each range color on the background chosen), also after the sensor's stated expiry (the red status
  // bar carries the expiry); without a current reading the words take its place (_noReading), at the sensor's limit
  // the LO / HI screen (_limitScreen). g: the layout's geometry.
  _drawValue(s, gv, g) {
    const color = gv === null || isNaN(s.zoneColor) ? "old" : s.zoneColor;
    return this._ot(g.x, g.y, gv === null ? "---" : String(gv), g.size, color);
  }
  // The zone word under the value (s.slotWord), in the card's text color, as on the panels: a text wider than the
  // room at its size (a long zone word in another language) at the smaller size (32 px for 40 px), smaller still only
  // if the room needs it (the compact layout); hidden without a value and without a current reading (no current data, a
  // warm-up), so that it never suggests a current zone. slot: ZONE_SLOT of the card; maxW: the room.
  _zoneSlot(s, gv, x, slot, maxW, color = "fg", baseline = "central") {
    if (!s.slotWord || gv === null || s.old || s.warmup) return "";
    const size = textWidth(s.slotWord, slot.size) > maxW ? Math.round(slot.size * 0.8) : slot.size;
    return this._t(x, slot.y, s.slotWord, fitSize(s.slotWord, size, maxW, 20), color, "middle", baseline);
  }
  // Without a current reading (s.words), in the value's place, never a number, stacked and centered at (x, cy), all in
  // the text color as on the panels: the words "Sensor warming up", "Sensor expired" or "No current data" at the zone
  // word's large size (size), wrapped at the room (two lines at most: worst_case_fit.py); under them, smaller, the last
  // reading at nr.line px and when it was taken at nr.time px, on one line or, with nr.split, the age on a line of its
  // own (NO_READING; _lastReading); a line not known is left out (without any reading only the words; while the
  // reading's time is ahead of the clock no time line). A line wider than the room alone is made smaller (the words to
  // 20 px at least, the other lines to 14 px). Only the words throb (s.throb: the first 30 minutes of the episode).
  _noReading(s, x, cy, size, room, nr) {
    const rows = wrapText(gcT(NO_READING_KEYS[s.words][0]), size, room).map((t) => [t, size, 20, "fg", s.throb]);
    const last = this._lastReading(s);
    if (last.value) rows.push([last.value, nr.line, 14, "fg"]);
    if (last.when) {
      rows.push(...(nr.split ? [last.when, gcT("card.no_reading.ago_line", { ago: last.ago })]
        : [gcT("card.no_reading.time_ago", { when: last.when, ago: last.ago })]).map((t) => [t, nr.time, 14, "fg"]));
    }
    return this._stack(x, cy, rows, room);
  }
  // A stack of lines centered at (x, cy), one under the other at Roboto's line height: rows [text, px, smallest px,
  // color, throb]; a line wider than the room alone is made smaller, down to its smallest size
  _stack(x, cy, rows, room) {
    let y = cy - rows.reduce((h, [, px]) => h + lhOf(px), 0) / 2;
    return rows.map(([t, px, min, color, throb]) => {
      const mid = y + lhOf(px) / 2;
      y += lhOf(px);
      return this._ot(x, mid, t, fitSize(t, px, room, min), color, "middle", "central", this._throbClass(throb));
    }).join("");
  }
  // The class of a text that throbs (a fade to about 30 % brightness and back every 3 seconds), "" for a still one;
  // the drawing is made again on every change, so _phase starts the new text at the cycle's current point and the
  // fade runs on without a jump
  _throbClass(on) { if (on) this._throbbing = true; return on ? 'class="gc-throb"' : ""; }
  // the delays that keep the throb's and the status bar's turns' phases across redraws: a style for the drawing, only
  // for what it draws
  _phase() {
    const now = Date.now(), t0 = now % (2 * TURN_MS), t1 = (now + TURN_MS) % (2 * TURN_MS);
    const throb = `<style>.gc-throb { animation-delay: -${now % THROB_MS}ms; }</style>`;
    const turns = `<style>.gc-turn0 { animation-delay: -${t0}ms; } .gc-turn1 { animation-delay: -${t1}ms; }</style>`;
    return (this._throbbing ? throb : "") + (this._turning ? turns : "");
  }
  /**
   * Draw a status bar: W wide at y, h tall, its texts at size px, pad from the edges, in black or white, whichever
   * reads better on GlucoCraft's status color; "Check Home Assistant" at checkSize px (the bar's size where the panel of
   * the same layout has room for it, one size smaller on the 480 px wide layouts and the compact card).
   *
   * Left the reading's age, right the sensor's remaining time ("Sensor expiry passed" throbs while s.expiryThrob); in
   * the middle the warm-up (warm: its text) or "Custom ranges", and while GlucoCraft has a Repairs notice open "Check
   * Home Assistant", the two taking turns every TURN_MS. A middle text is centered on the bar when it clears both side
   * texts by a pad, otherwise centered in the room between them, and left out only without that room (midX).
   */
  _bar(s, W, y, h, size, pad, warm, checkSize = size) {
    const col = s.statusColor, fg = onColor(col), ty = y + h / 2;
    const left = this._ageText(s), exp = this._expiryText(s);
    let out = `<rect x="0" y="${y}" width="${W}" height="${h}" fill="${hex(col)}"/>` +
      this._t(pad, ty, left, size, fg, "start") +
      this._t(W - pad, ty, exp, size, fg, "end", "central", this._throbClass(s.expiryThrob && !!exp));
    const mids = [[s.warmup ? warm : s.custom ? gcT("card.status_bar.custom_ranges") : "", size],
      [s.attention ? gcT("card.status_bar.check_home_assistant") : "", checkSize]].filter(([t]) => t)
      .map(([t, px]) => [t, px, midX(W, pad, textWidth(left, size), textWidth(exp, size), textWidth(t, px))])
      .filter(([, , x]) => x !== null);
    if (mids.length > 1) this._turning = true;
    mids.forEach(([t, px, x], i) => {
      out += this._t(x, ty, t, px, fg, "middle", "central", mids.length > 1 ? `class="gc-turn${i}"` : "");
    });
    return out;
  }
  /**
   * Draw the LO / HI screen in the value's place, in the layout of the words without a current reading: while the
   * reading is beyond the sensor's range there is no value to show, so the value's place says so, in four lines
   * centered at (x, cy): the zone word ("Very low" / "Very high") at the large size and in the zone's color; "Below
   * sensor range (LO)" / "Above sensor range (HI)" at nr.line px; since when ("Since 3:12 AM (2 h 5 min)") and the last
   * measured value before the stretch ("Last measured: 52 mg/dL at 3:10 AM") at nr.time px; the lines under the zone
   * word each wrapped at the room (two lines at most), as the panels wrap their labels; a line not known is left out,
   * and while the reading times cannot be trusted (ahead of the clock) the lines carry no time, as on the panels. All
   * still, the other lines in the text color. The sizes are those of the panel of the same layout.
   */
  _limitScreen(s, x, cy, size, room, nr) {
    const low = s.limit === "low", zoneWord = s.zoneWord || gcT(low ? "card.zone.very_low" : "card.zone.very_high");
    const color = isNaN(s.zoneColor) ? (ZC[low ? "very_low" : "very_high"] ?? "fg") : s.zoneColor;
    const rows = wrapText(zoneWord, size, room).map((t) => [t, size, 20, color]);
    const add = (text, px) => wrapText(text, px, room).slice(0, 2).forEach((t) => rows.push([t, px, 14, "fg"]));
    add(gcT(low ? "card.limit_screen.below" : "card.limit_screen.above", { limit: s.limitText }), nr.line);
    const since = this._sinceText(s);
    if (since) add(since, nr.time);
    const lm = this._lastMeasuredText(s);
    if (lm) add(lm, nr.time);
    return this._stack(x, cy, rows, room);
  }
  // when a time was, within a line of the LO / HI screen: [days before today, "3:12 AM" today, "yesterday at 3:12 AM",
  // "Oct 22 at 3:12 AM"], by the calendar day in Home Assistant's time zone; spoken: the same in the text description's
  // own words, the month in full ("October 22 at 3:12 AM")
  _dayTime(at, now, spoken = false) {
    const day = (d) => { const z = zoneParts(d); return Date.UTC(z.y, z.mo, z.d) / 86400000; };
    const days = day(now) - day(at), time = clockStr(at);
    return [days, days === 0 ? time
      : days === 1 ? gcT(spoken ? "card.describe.when_yesterday" : "card.limit_screen.when_yesterday", { time })
        : gcT(spoken ? "card.describe.when_date" : "card.no_reading.when_date", { date: dayMonth(at, spoken), time })];
  }
  // whether the LO / HI screen shows since when the stretch of limit readings lasts: a known start, not ahead of the
  // clock, while the reading times can be trusted (the text description follows the screen)
  _sinceShown(s) {
    return !isNaN(s.since) && !s.untrusted && s.since * 1000 <= s.now.getTime();
  }
  // "Since 3:12 AM (2 h 5 min)": the start of the stretch of limit readings and how long it has lasted ("" unknown)
  _sinceText(s) {
    if (!this._sinceShown(s)) return "";
    const at = new Date(s.since * 1000), m = Math.floor((s.now - at) / 60000);
    const duration = m < 60 ? gcT("limit_time.minutes", { minutes: m })
      : m < 1440 ? gcT("limit_time.hours", { hours: Math.floor(m / 60), minutes: m % 60 })
        : gcT("card.limit_screen.days", { days: Math.floor(m / 1440), hours: Math.floor((m % 1440) / 60) });
    return gcT("card.limit_screen.since", { time: this._dayTime(at, s.now)[1], duration });
  }
  // "Last measured: 52 mg/dL at 3:10 AM": the newest measured reading before the stretch ("" without one)
  _lastMeasuredText(s) {
    const [mg, ts] = s.lastMeasured || [];
    if (!(mg > 0)) return "";
    const value = gF(mg), unit = gU();
    if (!(ts > 0) || s.untrusted) return gcT("card.limit_screen.last_measured_value", { value, unit });
    const [days, when] = this._dayTime(new Date(ts * 1000), s.now);
    return gcT(days === 0 ? "card.limit_screen.last_measured" : "card.limit_screen.last_measured_day",
      { value, unit, when });
  }
  // The last reading and when it was taken, as the panels show them under the words (gc_units.h last_reading,
  // reading_when, reading_ago): value "Last reading: 64 mg/dL" in the display unit (a limit reading "Last reading:
  // LO"); when "Today at 17:39", "Yesterday at 17:39" or the date in the setting's order ("Oct 22 at 17:39"), by the
  // calendar day in Home Assistant's time zone; ago "45 min ago", "2 h 0 min ago", "1 d 3 h ago" (whole minutes,
  // rounded down); at, days: the reading's time and the days since its day. null where not known (never a reading).
  // The value is that of the reading whose time the line shows (_lastShown).
  _lastReading(s) {
    const [v, lim] = this._lastShown(s);
    const value = v === null ? null : lim ? gcT("card.no_reading.last_reading_limit", { value: v })
      : gcT("card.no_reading.last_reading", { value: v, unit: gU() });
    if (isNaN(s.age)) return { value, when: null, ago: null, at: null, days: NaN };
    const at = new Date(s.now.getTime() - s.age * 60000), time = clockStr(at);
    const day = (d) => { const z = zoneParts(d); return Date.UTC(z.y, z.mo, z.d) / 86400000; };
    const days = day(s.now) - day(at), m = s.ageMin;
    const when = days === 0 ? gcT("card.no_reading.when_today", { time })
      : days === 1 ? gcT("card.no_reading.when_yesterday", { time })
        : gcT("card.no_reading.when_date", { date: dayMonth(at), time });
    const ago = m < 60 ? gcT("card.no_reading.ago_minutes", { minutes: m })
      : m < 1440 ? gcT("card.no_reading.ago_hours", { hours: Math.floor(m / 60), minutes: m % 60 })
        : gcT("card.no_reading.ago_days", { days: Math.floor(m / 1440), hours: Math.floor((m % 1440) / 60) });
    return { value, when, ago, at, days };
  }
  // the TREND column or the 5-minute delta without a trend: "---" at the size of the 5-minute delta's "---"
  _noTrend(cx, cy, size) { return this._t(cx, cy, "---", size, "fg3"); }

  // The trend in the TREND column, centered at (cx, cy): GlucoCraft's arrow, arrowPx high, turned by its angle, or, with
  // the setting Trend shown as: word, the trend word in the card's text color, wrapped at the column's room (a longer
  // word on two lines, as on the panels; TREND_WORD). Neither without current data (also by the card's own count) or
  // while the trend is unknown (and "---" without a current reading: _noTrend); after the stated expiry it follows the
  // data, as the arrow.
  _trendCell(s, cx, cy, arrowPx, tw, room) {
    if (!s.word || s.old || s.limit || typeof s.angle !== "number") return "";   // blank at the sensor's limit too
    if (GC_TREND_WORD && s.trendWord) {
      const lines = wrapText(s.trendWord, tw.size, room), lh = lhOf(tw.size);
      return lines.map((line, i) => this._t(cx, cy + (i - (lines.length - 1) / 2) * lh, line,
        fitSize(line, tw.size, room, 14), "fg")).join("");
    }
    return `<g transform="translate(${cx} ${cy}) rotate(${-s.angle}) scale(${arrowPx / 24}) translate(-12 -12)">` +
      `<path d="${ARROW_PATH}" style="fill:var(--gc-fg)"/></g>`;
  }

  // the glucose value as shown: LO / HI for a limit reading, else the number in the display unit
  _valText(s) { return s.limitText ? s.limitText : isNaN(s.lv) ? null : gF(s.lv); }
  // The last reading as the line under the words shows it: [text, limit] of the newest reading, from the Last reading
  // time sensor (last_reading_limit, last_reading_mgdl: the reading of last_reading_ts), so that a value kept from an
  // earlier reading is never paired with a newer reading's time; without them, the value as shown (_valText)
  _lastShown(s) {
    if (s.lastLimit) return [gcT(s.lastLimit === "low" ? "card.limit.low" : "card.limit.high"), s.lastLimit];
    if (!s.limitText && s.lastMg > 0) return [gF(s.lastMg), null];
    return [this._valText(s), s.limit];
  }
  // the 5-minute delta as shown: "---" without a current reading or a trend
  _deltaText(s) {
    // GlucoCraft's 5-minute delta; none at the sensor's limit (no value to change from)
    const d5 = !s.words && !s.limit && s.word && !s.old && typeof s.change === "number" ? s.change : NaN;
    return isNaN(d5) ? "---" : gDelta(d5);
  }
  // the status bar's texts, the same on every card and panel: the age of the reading (whole minutes since its
  // measurement time, rounded down, "Reading: 6 min ago"; after 99 minutes whole hours, rounded down, "Reading: 2 h
  // ago"; gc_units.h reading_age), the sensor's remaining time, the warm-up
  _ageText(s) {
    if (isNaN(s.age)) return gcT("card.status_bar.reading_none");
    return s.ageMin <= 99 ? gcT("card.status_bar.reading_minutes", { minutes: s.ageMin })
      : gcT("card.status_bar.reading_hours", { hours: Math.floor(s.ageMin / 60) });
  }
  // the sensor's remaining time until its stated expiry: minutes, hours and minutes, or days and hours
  _expiryText(s) {
    const m = s.remain;
    if (isNaN(m)) return "";
    if (m <= 0) return gcT("card.status_bar.sensor_expired");
    if (m < 60) return gcT("card.status_bar.expiry_minutes", { minutes: m });
    if (m > 2880) return gcT("card.status_bar.expiry_days", { days: Math.floor(m / 1440), hours: Math.floor(m / 60) % 24 });
    return gcT("card.status_bar.expiry_hours", { hours: Math.floor(m / 60), minutes: m % 60 });
  }
  // the warm-up's minutes ("Warm-up: 12 min"); plain "Warm-up" without a start, or with a start ahead of the clock (no
  // count can be made then), as on the panels
  _warmUpText(s) {
    const mins = isNaN(s.wuStart) || s.now < s.wuStart ? NaN : Math.floor((s.now - s.wuStart) / 60000);
    return isNaN(mins) ? gcT("card.status_bar.warm_up") : gcT("card.status_bar.warm_up_minutes", { minutes: mins });
  }
  // the text while the history file is loading: before the first full file, also while its download is tried again
  // every minute: "Loading...", as on the panels
  _waitText() { return gcT("card.status.loading"); }

  // ------------------------------------------------------------------ render
  /** Draw the page shown. */
  _render() {
    if (!this._hass || !this.shadowRoot) return;
    const svg = this.shadowRoot.querySelector("svg");
    if (!svg) return;
    const s = this._state(), p = this._page;
    this._a11y(s, p);
    this._throbbing = false; this._turning = false;
    if (this._compact()) { this._compactRender(svg, s, p); return; }
    if (p === ABOUT_PAGE) { svg.innerHTML = this._aboutPage() + this._navBar(p); return; }   // a full screen
    const page = this._pageSvg(s, p, p >= 7) + this._navBar(p);
    svg.innerHTML = this._phase() + page;
  }
  // A graph page or a weekly page (trend) of the design layout, without the page buttons. The square card: a weekly
  // page's title and status in the glucose row's place, its chart in the graph's place.
  _pageSvg(s, p, trend) {
    return this._header(s) + (trend ? this._trendInfo(p - 7) : this._dataRow(s)) + this._statusBar(s) +
      (trend ? this._trendCanvas(p - 7) : this._graphArea(s, p));
  }

  // ------------------------------------------------------------------ compact layout (narrow screens)
  // Below the card's design width the parts are stacked at the width the card has, so that no text is drawn below
  // 14 px (the 12 px statistics labels of the square panel remain only in the design layout). The order is the panel's
  // header, the glucose value with its zone word, the trend arrow and the 5-minute delta, then on the square
  // card the status bar and the page (graph and statistics, the weekly chart, or the About text), on the rectangular
  // card the page and the status bar, and the page buttons. Like the panels, the card keeps
  // one size on every page and in every state: the glucose block has one height (the value's room holds the value and
  // its zone word, or the words without a current reading with the last reading under them), every page is drawn in
  // the room of the tallest (_compactFrame), and the page buttons stay at the bottom.
  _compact() { return !!this._compactW; }
  // The compact layout's frame at width w: top, the height of the header, the status bar and the glucose block; page,
  // the tallest page below them (a graph page with its statistics, or a weekly page with its description); about, the
  // About page (a full screen from the top, as drawn in _compactRender); own, whether any page's name needs a line of
  // its own above the page buttons. Kept until the width, the language or the file changes.
  _compactFrame(w, pad) {
    const key = `${w}|${globalThis.glucocraftI18n?.htmlLang}`;
    if (this._frame?.key === key && this._frame.bf === this._bf) return this._frame;
    const M = COMPACT, tw = w - 2 * pad, lines = (text, size) => wrapText(text, size, tw).length * lhOf(size);
    const cols = Math.max(3, Math.floor(tw / M.cellW));
    const graph = Math.max(...[0, 1, 2, 3, 4, 5, 6].map((p) => M.graph + Math.ceil(this._compactCells(p).length / cols) * M.cellRow + M.cellsEnd));
    const weekly = Math.max(...TREND_DEFS.map((_, pg) => M.weekTitle + wrapText(this._std(pg).desc, 14, tw).length * M.weekLine +
      M.weekStatus + M.weekChart));
    const [lead, rest] = disclaimerParts();
    const about = pad + M.icon + M.iconGap + lines(lead, 18) + M.leadGap + lines(rest, 16) + M.restGap + M.ruleRoom +
      lines(versionLine(), 14) + M.versionGap + copyrightLines().reduce((h, l) => h + lines(l, 14), 0);
    const own = PAGE_KEYS.some((_, p) => textWidth14(pageName(p)) > w - 2 * NAV_BTN.hitCompact);
    this._frame = { key, bf: this._bf, top: M.header + M.bar + M.block + M.trendRow, page: Math.max(graph, weekly), about, own };
    return this._frame;
  }
  // the statistics cells of a graph page in the compact layout, [id, value] in their order
  _compactCells(p) {
    const st = this._statsFor(p), gst = this._bf?.stats?.[p <= 2 ? 3 : p];
    const gmi = gst && gst.mean > 0 && gst.gmi > 0 ? gmiText(gst.gmi, gst.mean) : "---";
    return [...st.a.slice(0, 2), ...st.b, ...st.right, [GMI_IDS[p], gmi], ...st.a.slice(2)];
  }
  // whether the status bar comes last (under the page), as on the rectangular panel; on the square panel it is under
  // the glucose block
  _barLast() { return this.D === LAYOUT_WIDE; }
  // the compact status bar at y (32 px tall, 8 px room under it), its texts at 16 px; the warm-up as plain "Warm-up"
  _compactBar(s, w, pad, y) { return this._bar(s, w, y, 32, 16, pad, gcT("card.status_bar.warm_up"), 14); }
  /** Draw the compact layout: the parts stacked at the width the card has, in its frame (_compactFrame). */
  _compactRender(svg, s, p) {
    const w = this._compactW, pad = COMPACT.pad, frame = this._compactFrame(w, pad);
    let y = 0, o = `<rect x="0" y="0" width="${w}" height="4000" style="fill:var(--gc-bg)"/>`;
    let blockArea = [0, 0, 0, 0];   // the About page is a full screen: no glucose block
    if (p < ABOUT_PAGE) {
      const top = this._compactTop(s, w, pad);
      o += top.svg; y = top.y; blockArea = top.blockArea;
    }
    // the page: graph and statistics, the weekly chart, or the About text
    o += p === ABOUT_PAGE ? this._compactAbout(w, pad) : p >= 7 ? this._compactWeekly(p - 7, w, pad, y)
      : this._compactGraph(s, p, w, pad, y);
    const nav = this._compactNav(s, p, w, pad, frame), { navY, h } = nav;
    o += nav.svg;
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.innerHTML = this._phase() + o;
    const disp = this.shadowRoot.querySelector(".disp");
    if (disp) disp.style.aspectRatio = `${w} / ${h}`;
    this._cg = { w, h, navY, blockArea };
    this._placeKb(w, h, [pad, navY, NAV_BTN.w, NAV_BTN.h], [w - pad - NAV_BTN.w, navY, NAV_BTN.w, NAV_BTN.h], blockArea);
  }
  /**
   * Return the compact layout's top, above the page (not on the About page): { svg, y: where the page starts,
   * blockArea: the glucose block [x, y, w, h], which opens the weekly pages }.
   *
   * The header with the clock and the date; the glucose block; on the square card the status bar under it.
   */
  _compactTop(s, w, pad) {
    const fg = "fg", fg2 = "fg2", fg3 = "fg3";
    // header: the clock and the date
    const d = s.now, day = weekdayName(d);
    let o = this._t(pad, 30, clockStr(d), 40, "fg", "start") + this._t(w - pad, 18, day, 16, "fg2", "end") +
      this._t(w - pad, 40, dayMonth(d, true), 16, "fg2", "end");
    let y = COMPACT.header;
    // the glucose block, one height in every state: the value's room (188 px) holds the value (136 px) with its zone
    // word under it (52 px), or without a current reading the words with the last reading under them, or at the
    // sensor's limit the LO / HI screen; then the trend and the delta
    const blockTop = y, gv = this._valText(s);
    if (s.words) {
      o += this._noReading(s, w / 2, y + COMPACT.block / 2, ZONE_SLOT.compact.size, w - 2 * pad, NO_READING.compact);
    } else if (s.limit) {
      o += this._limitScreen(s, w / 2, y + COMPACT.block / 2, ZONE_SLOT.compact.size, w - 2 * pad, NO_READING.compact);
    } else {
      const txt = gv === null ? "---" : String(gv), size = fitSize(txt, 120, w - 2 * pad - 24, 60);
      const vcol = gv === null || isNaN(s.zoneColor) ? "old" : s.zoneColor;
      o += this._ot(w / 2, y + 66, txt, size, vcol);
      if (s.slotWord && gv !== null) {
        o += this._zoneSlot(s, gv, w / 2, { ...ZONE_SLOT.compact, y: y + 158 }, w - 2 * pad, fg);
      }
    }
    y += COMPACT.block;
    // the trend (with the source's name under it) and the 5-minute delta, side by side
    const half = (w - 2 * pad) / 2, ax = pad + half / 2, dx = pad + half * 1.5, dStr = this._deltaText(s);
    o += this._t(ax, y + 10, gcT("card.label.trend"), 16, fg2) + this._t(dx, y + 10, gcT("card.label.delta_5_min"), 16, fg2);
    o += s.words ? this._noTrend(ax, y + 56, fitSize("---", 48, half - 8, 24))
      : this._trendCell(s, ax, y + 56, 60, TREND_WORD.compact, half - 8);
    o += this._t(dx, y + 56, dStr, fitSize(dStr, 48, half - 8, 24), fg3) +
      this._t(ax, y + 102, this._fit14(s.source, half - 8), 14, fg2);
    y += COMPACT.trendRow;
    const blockArea = [0, blockTop, w, y - blockTop];
    // the status bar in the panel's order: on the square card under the glucose block, above the page (as on
    // the 480 x 480 panel); on the rectangular card at the bottom, under the page (as on the 800 x 480 panel)
    if (!this._barLast()) { o += this._compactBar(s, w, pad, y); y += COMPACT.bar; }
    return { svg: o, y, blockArea };
  }
  // The compact About page, as the square card's: the icon on top, the disclaimer (its first sentence in bold),
  // centered, then the footer: a thin line in the frame color, the version and copyright lines
  _compactAbout(w, pad) {
    const fg = "fg", fg2 = "fg2";
    const tw = w - 2 * pad, icon = COMPACT.icon, cx = w / 2, [lead, rest] = disclaimerParts();
    let o = "", y = 0;
    const para = (text, size, color, gap = 8, extra = "") => {
      wrapText(text, size, tw).forEach((l) => {
        o += this._t(cx, y + lhOf(size) / 2, l, size, color, "middle", "central", extra); y += lhOf(size);
      });
      y += gap;
    };
    o += `<image href="${ICON_URI}" x="${cx - icon / 2}" y="${pad}" width="${icon}" height="${icon}"/>`;
    y = pad + icon + COMPACT.iconGap;
    para(lead, 18, fg, COMPACT.leadGap, 'font-weight="700"');
    para(rest, 16, fg, COMPACT.restGap);
    const rule = Math.round(w * 0.4);
    o += `<rect x="${Math.round(cx - rule / 2)}" y="${y}" width="${rule}" height="1" style="fill:var(--gc-box)"/>`;
    y += COMPACT.ruleRoom;
    para(versionLine(), 14, fg2, COMPACT.versionGap);
    copyrightLines().forEach((l) => para(l, 14, fg2, 0));
    return o;
  }
  // a compact weekly page from y: its title, its description, its status line and its chart
  _compactWeekly(pg, w, pad, y) {
    const fg = "fg", fg2 = "fg2", fg3 = "fg3", title = gcT(TREND_DEFS[pg][0]);
    let o = this._t(w / 2, y + 14, title, fitSize(title, 22, w - 2 * pad, 16), fg); y += COMPACT.weekTitle;
    wrapText(this._std(pg).desc, 14, w - 2 * pad).forEach((l) => { o += this._t(w / 2, y + 8, l, 14, fg3); y += COMPACT.weekLine; });
    const st = this._bf ? this._trendStatus(pg) : this._waitText();
    o += this._t(w / 2, y + 14, st, fitSize(st, 18, w - 2 * pad, 14), this._bf ? fg : fg2); y += COMPACT.weekStatus;
    this._compactL = { ...this.D, W: w, H: 0, GX: pad, GY: y, GW: w - 2 * pad, GH: 200, trend: { x: pad, y, w: w - 2 * pad, h: 200 } };
    return o + this._trendCanvas(pg);
  }
  // a compact graph page from y: the graph, then its statistics as a grid of cells (label 14 px, value 20 px)
  _compactGraph(s, p, w, pad, y) {
    this._compactL = { ...this.D, W: w, H: 0, GX: pad, GY: y, GW: w - 2 * pad, GH: 170, trend: this.D.trend };
    let o = p === 0 ? this._graph6h(s) : this._graphPeriod(s, p);
    y += COMPACT.graph;
    const cells = this._compactCells(p);
    const cols = Math.max(3, Math.floor((w - 2 * pad) / COMPACT.cellW)), cw = (w - 2 * pad) / cols;
    cells.forEach(([id, val], i) => {
      const cx = pad + (i % cols) * cw + cw / 2, cy = y + Math.floor(i / cols) * COMPACT.cellRow;
      o += this._t(cx, cy + 12, statLabel(id), 14, "fg2") + this._t(cx, cy + 34, val, fitSize(String(val), 20, cw - 6, 14), "fg");
    });
    return o;
  }
  /**
   * Return the compact layout's bottom: { svg, navY: the top of the page buttons, h: the drawing's height }.
   *
   * The page buttons at the frame's bottom, the same place on every page: the page name between them and "Page n of
   * 17" under it. A name too wide for the room between the buttons even at 14 px (in a language with long page names,
   * on a narrow phone) is drawn on its own line above them, across the card's width (the frame keeps that line's room
   * on every page where any page needs it). On the rectangular card the status bar above them.
   */
  _compactNav(s, p, w, pad, frame) {
    const name = pageName(p), between = w - 2 * NAV_BTN.hitCompact, own = textWidth14(name) > between;
    const navY = Math.max(frame.top + frame.page, frame.about) + (frame.own ? 32 : 8), h = navY + 48;
    let o = "";
    // the rectangular card's status bar: under the page, in the same place on every page (the room of the tallest
    // page, as the frame keeps it), above the page buttons and the name line; not on the About page (a full screen)
    if (p < ABOUT_PAGE && this._barLast()) o += this._compactBar(s, w, pad, frame.top - COMPACT.bar + frame.page);
    const btn = (x, label) => `<g class="btn"><rect x="${x}" y="${navY}" width="${NAV_BTN.w}" height="${NAV_BTN.h}" rx="10" ` +
      `style="fill:var(--gc-btn)" stroke="#777"/>${this._t(x + NAV_BTN.w / 2, navY + 21, label, 22, "fg")}</g>`;
    o += btn(pad, "◀") + btn(w - pad - NAV_BTN.w, "▶") +
      (own ? this._t(w / 2, navY - 14, name, fitSize(name, 16, w - 2 * pad, 14), "fg")
        : this._t(w / 2, navY + 12, name, fitSize(name, 16, between, 14), "fg")) +
      this._t(w / 2, navY + 31, gcT("card.nav.page_of", { page: p + 1, pages: N_PAGES }), 14, "fg2");
    return { svg: o, navY, h };
  }
  // a tap or swipe on the compact layout: a swipe or the page buttons change the page, the glucose block opens or
  // leaves the weekly pages
  _compactPointerUp(e) {
    if (!this._down || !this._cg) return;
    const dx = e.clientX - this._down[0], dy = e.clientY - this._down[1];
    this._down = null;
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) { this._go(this._page + (dx < 0 ? 1 : -1)); return; }
    if (Math.abs(dx) > 10 || Math.abs(dy) > 10) return;
    const rect = this.shadowRoot.querySelector("svg").getBoundingClientRect(), g = this._cg;
    const x = (e.clientX - rect.left) / rect.width * g.w, y = (e.clientY - rect.top) / rect.height * g.h;
    if (y >= g.navY) {
      if (x < NAV_BTN.hitCompact) this._go(this._page - 1); else if (x > g.w - NAV_BTN.hitCompact) this._go(this._page + 1);
      return;
    }
    const [bx, by, bw, bh] = g.blockArea;
    if (this._page < ABOUT_PAGE && x >= bx && x < bx + bw && y >= by && y < by + bh) this._toggleWeekly();
    this._lastUse = Date.now();
  }

  // ------------------------------------------------------------------ screen readers and keyboard (ACCESSIBILITY.md)
  // The drawing's text description and the keyboard buttons' names, refreshed with every drawing.
  // The card's language (the catalogue in use) is set here too, as the texts are drawn.
  _a11y(s, p) {
    const root = this.shadowRoot;
    root.querySelector("ha-card")?.setAttribute("lang", globalThis.glucocraftI18n.htmlLang);
    // The About page holds several hundred characters: the drawing is named by the page's title, and the text follows
    // as text, which can be read word by word and translated by the browser. Every other page is named by its
    // description in the rule-4 order (data status first).
    const text = p === ABOUT_PAGE, desc = root.querySelector("[data-desc]");
    root.querySelector("svg")?.setAttribute("aria-label", text ? pageName(p) : this._describe(s, p));
    if (desc) desc.textContent = text ? this._aboutText().join(gcT("card.describe.separator")) : "";
    const name = (k) => pageNameInSentence(((k % N_PAGES) + N_PAGES) % N_PAGES);
    const kb = (k) => root.querySelector(`[data-kb="${k}"]`);
    if (!kb("prev")) return;
    kb("prev").setAttribute("aria-label", gcT("card.a11y.previous_page", { page: name(p - 1) }));
    kb("next").setAttribute("aria-label", gcT("card.a11y.next_page", { page: name(p + 1) }));
    const weekly = p >= 7 && p < ABOUT_PAGE, open = p <= 6 && this._canOpenWeekly();
    kb("block").hidden = !(weekly || open);
    kb("block").setAttribute("aria-label", gcT(weekly ? "card.a11y.back_to_6_hours" : "card.a11y.open_weekly"));
  }

  /**
   * Return the text description of the drawing for screen readers.
   *
   * The text description of what the card shows, in the order of ACCESSIBILITY.md rule 4: the data status (only when
   * there is a problem), the value with its unit and zone word, the time of the reading, the trend, then one sentence
   * on the page shown. Every unit is written out by the shared spoken-text functions.
   */
  _describe(s, p) {
    const out = [], dec = GC.dec;
    // whole sentences, each one text of the catalogue, joined by the language's sentence separator
    const sentences = (list) => list.join(gcT("card.describe.separator"));
    const title = gcT("card.describe.label", { text: pageName(p) });
    if (p === ABOUT_PAGE) return sentences([title, ...this._aboutText()]);
    // 1. the data status, only when there is a problem. Without a current reading the screen's order: its words first
    // ("Sensor warming up." / "Sensor expired." / "No current data."), then the last reading with when it was taken
    // and its age ("Last reading 64 milligrams per deciliter, today at 5:39 PM, 2 hours ago."), and no zone word or
    // trend; after the stated expiry, while readings arrive, "Sensor expired." before the value. The throb is
    // brightness only and changes nothing here. While GlucoCraft has a Repairs notice open, "Check Home Assistant."
    // before the page sentence, as the status bar shows it.
    const speak = (mg) => gcSpeak(gD(mg), gU(), { d: GC.mmol ? 1 : 0, dec });
    const value = s.limit ? gcSpeakLimit(s.limit) : isNaN(s.lv) ? "" : speak(s.lv);
    const attention = s.attention ? [gcT("card.describe.check_home_assistant")] : [];
    if (s.words) {
      out.push(gcT(NO_READING_KEYS[s.words][1]));
      const last = this._lastReading(s), [, lastLim] = this._lastShown(s);
      const lastValue = s.lastLimit ? gcSpeakLimit(s.lastLimit) : !lastLim && s.lastMg > 0 ? speak(s.lastMg) : value;
      if (lastValue && last.when) {
        const at = { time: clockStr(last.at) };
        const when = last.days === 0 ? gcT("card.describe.when_today", at)
          : last.days === 1 ? gcT("card.describe.when_yesterday", at)
            : gcT("card.describe.when_date", { ...at, date: dayMonth(last.at, true) });   // the month in full
        out.push(gcT("card.describe.last_reading", { value: lastValue, when, age: gcSpeakAge(s.age) }));
      } else if (lastValue) out.push(gcT("card.describe.last_reading_value", { value: lastValue }));
      else if (!isNaN(s.age)) out.push(gcT("card.describe.last_reading_ago", { age: gcSpeakAge(s.age) }));
      out.push(...attention, p >= 7 ? this._describeWeekly(p - 7) : this._describeGraph(p));
      return sentences(out);
    }
    if (s.expired) out.push(gcT("card.describe.sensor_expired"));
    // 2. the value, its unit and the zone word (after the stated expiry too); at the sensor's limit the LO / HI
    // screen's lines with the same times (none while the reading times cannot be trusted, no "since" for a start ahead
    // of the clock; the day as on the screen): since when, and the last measured value before the stretch
    const zone = s.zoneWord ? gcT(ZONE_KEYS[s.zone][1]) : "";
    if (!value) out.push(gcT("card.describe.no_reading"));
    else out.push(zone ? gcT("card.describe.glucose_zone", { value, zone }) : gcT("card.describe.glucose", { value }));
    if (s.limit && this._sinceShown(s)) {
      out.push(gcT("card.describe.limit_since", { time: this._dayTime(new Date(s.since * 1000), s.now, true)[1],
        duration: gcSpeakAge((s.now - s.since * 1000) / 60000) }));
    }
    const [lmMg, lmTs] = s.limit && s.lastMeasured ? s.lastMeasured : [];
    if (lmMg > 0) {
      if (lmTs > 0 && !s.untrusted) {
        const [days, when] = this._dayTime(new Date(lmTs * 1000), s.now, true);
        out.push(days === 0 ? gcT("card.describe.last_measured", { value: speak(lmMg), time: when })
          : gcT("card.describe.last_measured_day", { value: speak(lmMg), when }));
      } else out.push(gcT("card.describe.last_measured_value", { value: speak(lmMg) }));
    }
    // 3. the time of the reading (a reading less than a whole minute old: "Measured less than a minute ago.")
    if (!isNaN(s.age)) {
      out.push(s.age < 1 ? gcT("card.describe.measured_now") : gcT("card.describe.measured", { age: gcSpeakAge(s.age) }));
    }
    // 4. the trend: GlucoCraft's trend word and its 5-minute delta (after the stated expiry too: it follows the data);
    // none at the sensor's limit, as on the screen
    const trend = s.limit ? "" : gcSpeakTrend(s.word);
    if (trend) {
      const d5 = typeof s.change === "number" ? s.change : NaN;
      const delta = isNaN(d5) ? "" : gcSpeak(deltaShown(d5), gU(), { d: GC.mmol ? 1 : 0, dec, sign: true });
      out.push(delta ? gcT("card.describe.trend_delta", { trend: cap(trend), delta })
        : gcT("card.describe.trend", { trend: cap(trend) }));
    }
    // 5. one sentence on the page shown
    out.push(...attention, p >= 7 ? this._describeWeekly(p - 7) : this._describeGraph(p));
    return sentences(out);
  }
  // The sentences of the About page, without its title: the disclaimer (its first sentence and the rest, joined as the
  // language joins sentences), the version line and the copyright line
  _aboutText() {
    const label = (text) => gcT("card.describe.label", { text });
    const disclaimer = disclaimerParts().filter(Boolean).join(gcT("card.describe.separator")).trim();
    return [disclaimer, label(versionLine()), label(copyrightLines().join(" "))];
  }
  // "6-hour graph: lowest 58, highest 160." in the display unit; a LO / HI extreme in words
  _describeGraph(p) {
    const graph = gcT(["card.describe.graph.6h", "card.describe.graph.24h", "card.describe.graph.7d",
      "card.describe.graph.14d", "card.describe.graph.30d", "card.describe.graph.90d", "card.describe.graph.180d"][p]);
    const f = this._statsFile(p), st = f?.stats?.[p];
    // the history is loading: no file yet for a period graph, nor for the 6-hour graph (it may already draw the live
    // readings received since the page opened)
    if ((p >= 1 && !this._bf) || (p === 0 && !f)) return this._describeWaiting(graph);
    // the time at the sensor's limit, as the warning on the graph
    const said = gcLimitSaid(f?.limitSeconds?.[p]);
    const limit = said.length ? [gcT("card.describe.limit_time", { parts: said.join(gcT("ui.join.list")) })] : [];
    const join = (first) => [first, ...limit].join(gcT("card.describe.separator"));
    if (st && st.mean > 0) {
      const [low, high] = this._minMax(st.min, st.max, true, true);
      return join(gcT("card.describe.graph_range", { graph, low, high }));
    }
    // without a measured value: readings at the sensor's limit alone (their times in the bands), too little data for
    // the period's statistics (the 70 % rule: every statistic sent as none, the graph still drawn), or no readings
    if (st && ["tir", "low", "high", "clo", "chi"].some((k) => st[k] > 0)) {
      return join(gcT("card.describe.graph_limit_only", { graph }));
    }
    const end = p <= 1 && f === this._delta ? Math.floor(Date.now() / 1000) : this._bf?.at || 0;
    if (this._readings(end - PERIOD_HOURS[p] * 3600, end).length) {
      return join(gcT("card.describe.graph_not_enough_data", { graph }));
    }
    return join(gcT("card.describe.graph_empty", { graph }));
  }
  /**
   * Return the one-sentence description of a weekly page.
   *
   * "Time in range of each 14-day period, last 26 weeks: from 81.0 to 93.0 percent."
   */
  _describeWeekly(pg) {
    const what = gcT(WEEKLY_SAID_KEYS[pg]);
    if (!this._bf) return this._describeWaiting(what);   // the page shows the loading text
    const vals = this._bf.trends.map((t, w) => this._trendVal(w, pg)).filter((v) => isFinite(v));
    if (!vals.length) return gcT("card.describe.weekly_empty", { what });
    const count = this._bf.trends.length, lo = Math.min(...vals), hi = Math.max(...vals);
    // "from 81.0 to 93.0 percent": the unit once, after the second number (GMI: each value in the A1C unit setting, its
    // mmol/mol from the mean glucose of the same week, as the status line and the panels show it)
    // A percentage as the chart's page shows it beside its target or goal: two decimals where one would show the
    // target's value on the wrong side of it (_pctShown), so that the spoken value is the value shown
    const glucose = pg === 2 || pg === 7 || pg === 8, d = glucose ? (GC.mmol ? 1 : 0) : 1;
    const ends = (a, b) => {
      if (glucose) return [gcSpeakNumber(gD32(a), d, GC.dec), gcSpeak(gD32(b), gU(), { d, dec: GC.dec })];
      const [sa, da] = this._pctShown(a, pg), [sb, db] = this._pctShown(b, pg);
      return [gcSpeakNumber(sa, da, GC.dec), gcSpeak(sb, "%", { d: db, dec: GC.dec })];
    };
    const [from, to] = pg === 4 ? this._gmiEnds() : ends(lo, hi);
    if (pg === 8) {
      // the lowest and the highest of the weekly minimums and of the weekly maximums
      const side = (k) => {
        const v = this._bf.trends.filter((t) => t.mean > 0).map((t) => (k ? t.max : t.min));
        return ends(Math.min(...v), Math.max(...v));
      };
      const [lowFrom, lowTo] = side(0), [highFrom, highTo] = side(1);
      return [gcT("card.describe.weekly_min_max", { what, count, low_from: lowFrom, low_to: lowTo, high_from: highFrom, high_to: highTo }),
        ...this._describeWeeklyStatus(pg)].join(gcT("card.describe.separator"));
    }
    return [gcT("card.describe.weekly_range", { what, count, from, to }), ...this._describeWeeklyStatus(pg)]
      .join(gcT("card.describe.separator"));
  }
  // The lowest and the highest weekly GMI as spoken, each in the A1C unit setting with its mmol/mol from its own week's
  // mean glucose (gmiMm); of two weeks with the same GMI in %, the one with the lower (the higher) mean
  _gmiEnds() {
    const weeks = this._bf.trends.map((t, w) => [this._trendVal(w, 4), t.mean]).filter(([v]) => isFinite(v))
      .sort((x, y) => x[0] - y[0] || x[1] - y[1]);
    const said = ([v, mean]) => gcSpeakA1c(v, GC.a1c, GC.dec, gmiMm(v, mean));
    return [said(weeks[0]), said(weeks[weeks.length - 1])];
  }
  // What a weekly page shows besides its chart, as sentences: the description line under the title (the range or
  // definition and the target or goal with its source), the value of the last 14 days and the direction
  _describeWeeklyStatus(pg) {
    const out = [gcT("card.describe.label", { text: gcSpeakText(this._std(pg).desc) })];
    const t = this._bf.trends[0];
    if (t && pg === 8 && t.mean > 0) {
      const g = (v) => gcSpeak(gD32(v), gU(), { d: GC.mmol ? 1 : 0, dec: GC.dec });
      out.push(gcT("card.describe.weekly_last_min_max", { min: g(t.min), max: g(t.max) }));
    } else if (t) {
      const v = this._trendVal(0, pg);
      if (isFinite(v)) {
        const [pv, pd] = this._pctShown(v, pg);
        const value = pg === 2 || pg === 7 ? gcSpeak(gD32(v), gU(), { d: GC.mmol ? 1 : 0, dec: GC.dec })
          : pg === 4 ? gcSpeakA1c(v, GC.a1c, GC.dec, gmiMm(v, t.mean)) : gcSpeak(pv, "%", { d: pd, dec: GC.dec });
        out.push(gcT("card.describe.weekly_last", { value }));
      }
    }
    const dirKey = this._bf.dirs?.[pg];
    if (dirKey) out.push(gcT("card.describe.weekly_direction", { direction: gcT(dirKey) }));
    return out;
  }
  // a graph or weekly page while the history file is loading or not available: what the page shows instead
  _describeWaiting(what) {
    return gcT("card.describe.history_loading", { what });
  }

  // the page buttons under the display, the page's name between them and "Page n of 17" under it
  _navBar(p) {
    const { W, H, NAV_H } = this.L, CX = W / 2;
    const [[px, y, w, h], [nx]] = navBoxes(W, H);
    const btn = (x, label) => `<g class="btn"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10" style="fill:var(--gc-btn)" ` +
      `stroke="#777"/>${this._t(x + w / 2, y + h / 2 + 1, label, 22, "fg")}</g>`;
    return `<rect x="0" y="${H}" width="${W}" height="${NAV_H}" style="fill:var(--gc-bg)"/>` + btn(px, "◀") + btn(nx, "▶") +
      this._t(CX, y + 13, pageName(p), 16, "fg") +
      this._t(CX, y + 32, gcT("card.nav.page_of", { page: p + 1, pages: N_PAGES }), 14, "fg2");
  }

  _header(s) {
    const { W, H } = this.L, d = s.now, time = clockStr(d);
    const day = weekdayName(d);
    const md = dayMonth(d, true);   // "October 22" / "22 October"
    return `<rect x="0" y="0" width="${W}" height="${H}" style="fill:var(--gc-bg)"/>` + this._t(15, 48, time, 60, "fg", "start") +
      this._t(W - 15, 34, day, 26, "fg2", "end") + this._t(W - 15, 64, md, 26, "fg2", "end");
  }

  /** Draw the glucose row: TREND, the value with its zone word (or the words without a current reading), and the 5-minute delta. */
  _dataRow(s) {
    const { W } = this.L, mid = 176;
    const gv = this._valText(s);
    // TREND: GlucoCraft's arrow or trend word (one system), the source's own name under it
    const trend = s.words ? this._noTrend(LCOL / 2, mid, 64)
      : this._trendCell(s, LCOL / 2, mid, 80, TREND_WORD.square, TREND_WORD.square.room);
    const src = this._fit14(s.source, 111);
    // 5 MIN DELTA: GlucoCraft's change of the smoothed glucose over 5 minutes (whole mg/dL or 0.1 mmol/L); 4 characters drop to 40 px
    const dStr = this._deltaText(s);
    const nr = NO_READING.square;
    const value = s.words ? this._noReading(s, MID_X, nr.cy, ZONE_SLOT.square.size, nr.room, nr)
      : s.limit ? this._limitScreen(s, MID_X, nr.cy, ZONE_SLOT.square.size, nr.room, nr)
        : this._drawValue(s, gv, SQ_VALUE) + this._zoneSlot(s, gv, MID_X, ZONE_SLOT.square, 248);
    return this._t(LCOL / 2, mid - 50, gcT("card.label.trend"), 16, "fg2") + trend +
      this._t(LCOL / 2, mid + 55, src, 14, "fg2") + value +
      this._t(W - RCOL / 2, mid - 50, gcT("card.label.delta_5_min"), 16, "fg2") +
      this._t(W - RCOL / 2, mid, dStr, dStr.length > 3 ? 40 : 64, "fg3");
  }

  /** Draw the status bar (y 256-286, texts at 16 px, 10 px from the edges): GlucoCraft's status color (one system). */
  _statusBar(s) { return this._bar(s, this.L.W, 256, 30, 16, 10, this._warmUpText(s), 14); }

  _box(x, y, w, h) {
    return `<rect x="${x + 0.5}" y="${y + 0.5}" width="${w - 1}" height="${h - 1}" rx="4" style="fill:var(--gc-bg);stroke:var(--gc-box)" stroke-width="1"/>`;
  }

  /**
   * Return the statistics cells of a graph page.
   *
   * The statistics cells of a graph page, each [id, value] (the id names the label, STAT_KEYS): a / b the two side
   * columns, right the bottom row. "Very low" / "Very high" are the times below the very-low and above the very-high
   * limit (the consensus terms, in full words, as the panels).
   */
  _statsFor(p) {
    // the source: for 6 h / 24 h the 5-minute file's statistics, exactly what the panel shows; for 6 h before any file
    // the 6-hour summary sensor (every cell, as on the panel); else the full file's
    const f = this._statsFile(p), st = f?.stats?.[p], pre = p === 0 && !f;
    // An empty period = ---; a period with readings at the sensor's limit alone has its times in the bands only (they
    // have no value). A period below 70 % sensor active has every statistic sent as zero (mean 0, every band 0): ---
    // in every cell, with or without limit readings (the 70 % rule; the graph is still drawn).
    const bands = ["tir", "titr", "low", "high", "clo", "chi"];
    const limitOnly = !!st && !(st.mean > 0) && !!f?.limitSeconds?.[p]?.some((v) => v > 0)
      && bands.some((k) => st[k] > 0);
    const g = pre ? (k) => this._summaryNum(k) : (k) => (st && (st.mean > 0 || (limitOnly && bands.includes(k))) ? st[k] : NaN);
    const glucose = pre ? gF : gF32;   // the sensor's attributes are doubles, the file's values float32
    const [mn, mx] = pre ? [this._summaryExtreme("min"), this._summaryExtreme("max")]
      : this._minMax(g("min"), g("max"));
    return { a: [["tir", p0(g("tir"))], ["titr", p0(g("titr"))], ["min", mn], ["max", mx]],
             b: [["low", p0(g("low"))], ["high", p0(g("high"))], ["very_low", p1(g("clo"))], ["very_high", p1(g("chi"))]],
             right: [["mean", glucose(g("mean"))], ["sd", glucose(g("sd"))], ["cv", p0(g("cv"))]] };
  }
  // The statistics in the two rows of the rectangular panels: TIR TITR Low High Very low Very high /
  // Avg SD CV GMI Min Max, each [id, value]; the GMI's value is [main value, second line or "", size]: in both A1C units
  // the panels' compact form on one line at 14 px ("6.5%/48", gmiCompact: the 800 x 480 panel's GMI cell; the worst
  // case "12.9%/117" fits the 68 px cell), in mmol/mol the value with its unit under it (gmiParts)
  _statRows(p) {
    const st = this._statsFor(p), v = Object.fromEntries([...st.a, ...st.b, ...st.right]);
    const gst = this._bf?.stats?.[p <= 2 ? 3 : p], has = gst && gst.mean > 0 && gst.gmi > 0;
    const gmi = !has ? ["---", ""]
      : GC.a1c === "both" ? [gmiCompact(gst.gmi, gst.mean), "", 14] : gmiParts(gst.gmi, gst.mean);
    const cell = (id) => [id, v[id] ?? "---"];
    return [["tir", "titr", "low", "high", "very_low", "very_high"].map(cell),
      [cell("mean"), cell("sd"), cell("cv"), [GMI_IDS[p], gmi], cell("min"), cell("max")]];
  }

  // The file whose statistics a graph page shows for period p: the 5-minute file for 6 h / 24 h once it is loaded,
  // else the full file (as on the panels)
  _statsFile(p) { return p <= 1 && this._delta?.stats?.[p] ? this._delta : this._bf; }
  // The lowest and highest measured reading of a period as shown (a reading at the sensor's limit is no value);
  // spoken: the words of the text description. unit: in spoken words, the unit is said once, after the highest value
  _minMax(mn, mx, spoken = false, unit = false) {
    const ok = (v) => typeof v === "number" && isFinite(v) && v > 0;
    const num = (v, withUnit) => (!ok(v) ? (spoken ? gcT("spoken.not_available") : "---")
      : !spoken ? gF32(v) : withUnit ? gcSpeak(gD32(v), gU(), { d: GC.mmol ? 1 : 0, dec: GC.dec })
        : gcSpeakNumber(gD32(v), GC.mmol ? 1 : 0, GC.dec));
    return [num(mn, false), num(mx, unit)];
  }

  /** Draw the statistics boxes and the graph of a graph page. */
  _graphArea(s, p) {
    const { GX, GY, GW, stats: { colW, colH, rowY, rowH } } = this.L, st = this._statsFor(p);
    let out = "";
    [[5, st.a], [GX + GW + 5, st.b]].forEach(([bx, set]) => {
      out += this._box(bx, GY, colW, colH);
      set.forEach(([id, val], i) => {
        const y = GY + 5 + i * 45;
        out += this._t(bx + 4, y, statLabel(id), STAT_LABEL_PX, "fg2", "start", "hanging") +
          this._t(bx + 4, y + 13, val, 20, "fg", "start", "hanging");
      });
    });
    out += this._box(GX, rowY, GW, rowH);
    // GMI on the right of every page: 14 d and longer their own; 6 h, 24 h, 7 d the 14-day GMI,
    // labeled GMI14d (GMI_IDS). The square panel's four boxes and its GMI text (glucocraft-panel.yaml stats_row_*,
    // 68 / 62 / 72 / 134 px; gmiCompact, both units at 16 px). Measured worst cases (Roboto 12 + 20 px): "Avg 22.2"
    // 63, "SD 11.1" 57, "CV 100%" 68, "GMI180d 12.9%" 106, "GMI180d 12.9%/117" 130 px (16 px). The unit mmol/mol has no
    // room here, as on the panel (in mmol/mol "GMI180d 117 mmol/mol" would need 146 px).
    const gst = this._bf?.stats?.[p <= 2 ? 3 : p];
    const gmi = gst && gst.mean > 0 && gst.gmi > 0 ? gmiCompact(gst.gmi, gst.mean) : "---";
    const cells = [...st.right, [GMI_IDS[p], gmi]];
    const CW = [68, 62, 72, 134], cy = rowY + rowH / 2 + 1;
    let cx = GX + (GW - 336) / 2;
    cells.forEach(([id, val], i) => {
      const mid = cx + CW[i] / 2, size = i === 3 && GC.a1c === "both" ? 16 : 20; cx += CW[i];
      out += `<text x="${mid}" y="${cy}" text-anchor="middle" dominant-baseline="central">` +
        `<tspan font-size="${STAT_LABEL_PX}" style="fill:var(--gc-fg2)">${esc(statLabel(id))}</tspan><tspan font-size="${size}" style="fill:var(--gc-fg)" dx="4">${esc(val)}</tspan></text>`;
    });
    return out + (p === 0 ? this._graph6h(s) : this._graphPeriod(s, p));
  }

  // a grid line's label: one format per axis, as many decimals as the axis's step has ("7.0" / "6.5" / "6.0" at 0.5,
  // "4" / "6" / "8" at 2), never a decimal on some labels only, with the decimal separator in use (glucocraft-shared.js
  // gcAxisText: every graph)
  _axisText(value, step) {
    return gcAxisText(value, step);
  }
  // a glucose graph's scale for the values dmin to dmax (mg/dL; 70 to 180 without values): its ends and the y of a
  // value
  _scale(dmin, dmax) {
    const { GH } = this.L;
    if (dmin >= 999 || dmax <= 0) { dmin = 70; dmax = 180; }
    const [gmin, gmax] = gcFit(dmin, dmax, 20);
    const yOf = (g) => { const c = Math.min(Math.max(g, gmin), gmax); const y = GH - Math.floor((c - gmin) / (gmax - gmin) * GH);
                         return Math.min(Math.max(y, 0), GH - 1); };
    return { gmin, gmax, yOf };
  }

  /**
   * Draw a graph's background: the range bands, the grid and the title.
   *
   * Graphs are always on black, like the wall panels, whatever the card's background: the bands in the graph band
   * colors (band_colors) at 60 % over black, white line and labels.
   */
  _bgAndGrid(sc, r, title) {
    const { GX, GY, GW, GH } = this.L;
    const { gmin, gmax, yOf } = sc;
    let out = `<svg x="${GX}" y="${GY}" width="${GW}" height="${GH}" viewBox="0 0 ${GW} ${GH}" overflow="hidden">` +
              `<rect width="${GW}" height="${GH}" fill="#0a0a0a"/>`;
    // the seven range bands in the graph range colors (data-band names the zone)
    [[0, yOf(r.cmax), "very_high"], [yOf(r.cmax), yOf(r.nmax), "high"], [yOf(r.nmax), yOf(r.imax), "near_high"],
     [yOf(r.imax), yOf(r.imin), "tight_range"], [yOf(r.imin), yOf(r.nmin), "near_low"], [yOf(r.nmin), yOf(r.cmin), "low"],
     [yOf(r.cmin), GH, "very_low"]].forEach(([a, b, zone]) => {
      const ys = Math.max(a, 0), ye = Math.min(b, GH);
      if (ys < ye) out += `<rect x="0" y="${ys}" width="${GW}" height="${ye - ys}" fill="${hex(BC[zone])}" fill-opacity="0.6" data-band="${zone}"/>`;
    });
    const sf = GC.mmol ? MGDL_PER_MMOLL : 1;          // grid in the display unit, positions in mg/dL
    const step = GC.mmol ? ([0.5, 1, 2, 5, 10].find((x) => x >= (gmax - gmin) / sf / 5) || 10) : Math.max(10, gcGridStep(gmin, gmax, 5));
    for (let k = Math.ceil(gmin / sf / step - 1e-9); k * step * sf <= gmax; k++) {
      const gd = k * step, g = gd * sf;
      const gy = yOf(g); if (gy <= 2 || gy >= GH - 2) continue;
      const major = GC.mmol ? (k % 2 === 0 || step >= 5) : ((g % (step * 2) === 0) || step >= 50);
      out += `<rect x="0" y="${gy}" width="${GW}" height="1" fill="${major ? "#404040" : "#2a2a2a"}"/>`;
      // the value label, only clear of the time labels' row at the bottom (as on the panels): a line closer
      // than 28 px to the bottom keeps its line but not its label, which would be drawn over the first time label
      if (gy <= GH - 28) out += this._ot(5, gy - 7, this._axisText(gd, step), 14, 0xFFFFFF, "start", "hanging");
    }
    // no range-limit lines, as on the panels: the bands show the ranges
    return out + this._ot(GW - 10, 5, title, 16, 0xFFFFFF, "end", "hanging");
  }

  // The LO / HI marks of the graph (gcLimitMarks): the stretches of limit readings between t0 and t1 (the files'
  // GCR1 stretches, and the stretch going on now from limit_since), drawn after the data, so over the line and under
  // the graph's labels (_textsLast); no text.
  _limitMarks(s, t0, t1) {
    const { GW, GH } = this.L, runs = this._limitRuns(s);
    if (!runs.length) return "";
    return gcLimitMarks(runs, t0, t1, GW, GH, { low: hex(ZC.very_low), high: hex(ZC.very_high) });
  }
  // the stretches of limit readings known to the card: the full file's and the recent-readings file's (GCR1), and the
  // stretch going on now (the reading at the limit, since limit_since, up to now)
  _limitRuns(s) {
    const runs = [...(this._bf?.limitRuns || []), ...(this._delta?.limitRuns || [])];
    if (s.limit && !isNaN(s.since)) runs.push([s.since, Math.floor(s.now.getTime() / 1000), s.limit]);
    return runs;
  }

  /** Draw the 6-hour graph from the readings. */
  _graph6h(s) {         // one reading per 2-min clock slot (ts / 120, newest wins); no line across a gap (the gap rule)
    const { GW, GH } = this.L;
    const now = Math.floor(s.now.getTime() / 1000), win = 6 * 3600, start = now - win;
    const pts = [], firstKey = Math.floor(now / 120) - 179;    // the panel's 180 clock slots up to now
    this._readings(firstKey * 120, now).forEach((r) => {
      if (pts.length && Math.floor(pts[pts.length - 1][0] / 120) === Math.floor(r[0] / 120)) pts[pts.length - 1] = r;
      else pts.push(r);
    });
    // the gap rule on the drawn readings, one per slot, as the panels judge their 6-hour ring (gcu::is_gap), so that
    // both break the line at the same dropouts
    const gaps = gcGaps(pts.map((r) => r[0]), s.gap);
    let dmin = 999, dmax = 0;
    pts.forEach(([, v]) => { dmin = Math.min(dmin, v); dmax = Math.max(dmax, v); });
    const sc = this._scale(dmin, dmax), title = gcT("card.graph.title.6h");
    let out = this._bgAndGrid(sc, s.r, title);
    let prevLeft = Infinity;   // left edge of the label drawn before
    for (let h = 0; h <= 6; h++) {
      // whole hours in Home Assistant's time zone (as the clock), also in half-hour time zones
      const he = Math.floor(zoneHourStart((now - h * 3600) * 1000) / 1000);
      if (he < start || he > now) continue;
      const gx = Math.floor((he - start) * GW / win);
      if (gx >= 0 && gx < GW) out += `<rect x="${gx}" y="0" width="1" height="${GH}" fill="#2a2a2a"/>`;
      // the label centered 5 px right of its line, at least 4 px from the graph's edges and from its neighbour, as on
      // the panels
      const lbl = hourLabel(new Date(he * 1000)), half = textWidth14(lbl) / 2, lc = gx + 5;
      if (lc - half >= 4 && lc + half <= GW - 4 && lc + half <= prevLeft - 4) {
        out += this._ot(lc, GH - 14, lbl, 14, 0xFFFFFF, "middle", "hanging");
        prevLeft = lc - half;
      }
    }
    out += `<rect x="0" y="0" width="1" height="${GH}" fill="#2a2a2a"/><rect x="${GW - 1}" y="0" width="1" height="${GH}" fill="#404040"/>`;
    // the gap rule, per stretch (GlucoCraft rule): the line breaks where a gap lies between two drawn readings, and
    // across a stretch of limit readings (they have no value; their marks are at the edge), as on the panels
    const runs = this._limitRuns(s);
    let path = "", prev = null;
    pts.forEach(([ts, v]) => {
      const x = Math.min(Math.max(Math.floor((ts - start) * GW / win), 0), GW - 1), y = sc.yOf(v);
      path += (prev !== null && !gcGapIn(gaps, prev, ts) && !gcRunIn(runs, prev, ts)) ? `L${x} ${y}` : `M${x} ${y}`;
      prev = ts;
    });
    // the line in white over a black underlay, as the period graphs' mean line and the weekly lines
    if (path) out += `<path d="${path}" fill="none" stroke="#000000" stroke-width="4" stroke-linejoin="round" stroke-linecap="round" data-under="1"/>` +
      `<path d="${path}" fill="none" stroke="#fff" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    // nothing to draw yet and no file: the loading text, over the range bands in white like every graph label (a gray
    // text would not stand out on the light bands)
    else if (!this._statsFile(0)) out += this._ot(GW / 2, GH / 2, this._waitText(), 18, 0xFFFFFF);
    out += this._limitMarks(s, start, now);
    return this._textsLast(out) + "</svg>";   // the labels over the grid and the line (as on the panels)
  }

  /** Draw a period graph (24 hours to 6 months): range areas and the mean line per slot. */
  _graphPeriod(s, p) {
    const { GX, GY, GW, GH } = this.L;
    const titles = [null, "card.graph.title.24h", "card.graph.title.7d", "card.graph.title.14d", "card.graph.title.30d",
      "card.graph.title.90d", "card.graph.title.180d"];
    if (!this._bf) {
      return `<rect x="${GX}" y="${GY}" width="${GW}" height="${GH}" fill="#0a0a0a"/>` +
        this._t(GX + GW / 2, GY + GH / 2, this._waitText(), 18, 0x888888);
    }
    const { avg, lo, hi, ft, lt, gaps } = this._periodSlots(p, s.gap);
    let dmin = 999, dmax = 0;
    for (let i = 0; i < GW; i++) {
      const l = lo[i] > 0 ? lo[i] : avg[i], h = hi[i] > 0 ? hi[i] : avg[i];
      if (l > 0 && l <= GLU_MAX && l < dmin) dmin = l;
      if (h > 0 && h <= GLU_MAX && h > dmax) dmax = h;
    }
    const sc = this._scale(dmin, dmax), title = gcT(titles[p]);
    let out = this._bgAndGrid(sc, s.r, title);
    const div = [0, 6, 7, 7, 6, 6, 6][p];
    out += `<rect x="0" y="0" width="1" height="${GH}" fill="#2a2a2a"/><rect x="${GW - 1}" y="0" width="1" height="${GH}" fill="#404040"/>`;
    if (p !== 1) for (let i = 1; i < div; i++) out += `<rect x="${Math.floor(i * GW / div)}" y="0" width="1" height="${GH}" fill="#2a2a2a"/>`;
    out += this._ot(50, 5, gcT("card.graph.legend_range"), 14, 0xBDBDBD, "start", "hanging") +
      this._ot(50, 22, gcT("card.graph.legend_average"), 14, 0xFFFFFF, "start", "hanging");
    const hrs = PERIOD_HOURS[p], wStart = this._bf.at - hrs * 3600;   // same window as the data
    if (p === 1) {
      // lines and labels at whole hours in Home Assistant's time zone (every 4 hours: 12am, 4am, ...), as the clock;
      // each label centered 5 px right of its line and left out where it would come closer than 4 px to the graph's
      // edges or to the label before it, as on the panels
      let prevRight = -Infinity, prevT = 0;
      const t0 = zoneHourStart(wStart * 1000);
      for (let k = 0; k <= hrs + 1; k++) {
        const hd = new Date(zoneHourStart(t0 + k * 3600e3));
        const t = Math.floor(hd.getTime() / 1000);
        if (t === prevT || zoneParts(hd).h % 4 || t <= wStart || t >= this._bf.at) continue;
        prevT = t;
        const gx = Math.floor((t - wStart) * GW / (hrs * 3600)), lbl = hourLabel(hd), half = textWidth14(lbl) / 2;
        const lc = gx + 5;
        out += `<rect x="${gx}" y="0" width="1" height="${GH}" fill="#2a2a2a"/>`;
        if (lc - half < 4 || lc + half > GW - 4 || lc - half < prevRight + 4) continue;
        prevRight = lc + half;
        out += this._ot(lc, GH - 14, lbl, 14, 0xFFFFFF, "middle", "hanging");
      }
    }
    // the date labels centered 3 px right of their line, left out where closer than 4 px to the edges or to the label
    // before, as on the panels
    const n = p === 2 || p === 3 ? 7 : 6;
    let prevEnd = -Infinity;
    for (let i = 1; i < n && p !== 1; i++) {
      const lt = new Date((wStart + i * hrs * 3600 / n) * 1000), lx = Math.floor(i * GW / n);
      const lbl = p === 2 ? weekdayName(lt, true) : numDate(lt), half = textWidth14(lbl) / 2;
      if (lx + 3 - half < 4 || lx + 3 + half > GW - 4 || lx + 3 - half < prevEnd + 4) continue;
      out += this._ot(lx + 3, GH - 14, lbl, 14, 0xFFFFFF, "middle", "hanging");
      prevEnd = lx + 3 + half;
    }
    out += gcRangeAreas(lo, hi, sc.yOf);
    // the gap rule, per stretch: a slot is joined to the previous slot with data unless the interval between
    // the last reading of that slot and the first of this one is a gap (a gap inside one slot is below the resolution)
    // or a stretch of limit readings lies there, as on the panels
    const runs = this._limitRuns(s);
    let path = "", prev = -1;
    for (let i = 0; i < GW; i++) {
      if (!avg[i]) continue;
      path += `${prev >= 0 && !gcGapIn(gaps, lt[prev], ft[i]) && !gcRunIn(runs, lt[prev], ft[i]) ? "L" : "M"}${i} ${sc.yOf(avg[i])}`;
      prev = i;
    }
    // the mean line in white over a black underlay, so that it stays visible on the light range areas
    if (path) out += `<path d="${path}" fill="none" stroke="#000000" stroke-width="4" stroke-linecap="round" data-under="1"/>` +
      `<path d="${path}" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round"/>`;
    out += this._limitMarks(s, wStart, this._bf.at);
    return this._textsLast(out) + "</svg>";   // the labels over the grid and the data (as on the panels)
  }

  // ---- weekly pages (the 14-day values at each week's end)
  // the range set a weekly page shows: its description and target from the file (GCX1), as on the panels; empty
  // before the first file
  _std(pg) {
    const st = this._bf?.std;
    const r = st ? { desc: dtx(st.descs[pg]), target: st.targets[pg], custom: st.custom } : { desc: "", target: null, custom: false };
    // whose target colors the chart: no label on a page without a target or goal
    r.label = this._bf && r.target ? gcT(r.custom ? "card.weekly.custom_goal" : "card.weekly.consensus_target") : "";
    return r;
  }


  // a weekly percentage as shown beside the page's target or goal: [value, decimals], one decimal, two where one would
  // show the target's value on the wrong side of it (shownAgainst, units.shown_against)
  _pctShown(v, pg) { const tg = this._std(pg).target; return tg ? shownAgainst(v, tg[1], 1) : [round(v, 1), 1]; }

  _trendVal(w, pg) {   // NaN = no value that week (GMI 0 = too little data for GMI)
    const t = this._bf.trends[w];
    if (!(t.mean > 0)) return NaN;                     // a week without data (sent empty): gap
    const v = [t.tir, t.titr, t.mean, t.cv, t.gmi, t.low, t.high, t.sd, t.min][pg];
    return pg === 4 && !(v > 0) ? NaN : v;
  }

  // the title, the description (wrapped at 440 px into two lines at most, as on the square panel) and the status line
  _trendInfo(pg) {
    const { W } = this.L, CX = W / 2;
    let out = this._t(CX, 116, gcT(TREND_DEFS[pg][0]), 32, "fg", "middle", "hanging");
    wrapText(this._std(pg).desc, 18, 440).slice(0, 2).forEach((l, i) => {
      out += this._t(CX, 164 + i * lhOf(18), l, 18, "fg3", "middle", "hanging");
    });
    if (!this._bf) return out + this._t(CX, 214, this._waitText(), 24, "fg2", "middle", "hanging");
    const t = this._trendStatus(pg);
    return out + this._t(CX, 214, t, textWidth(t, 24) > 440 ? 18 : 24, "fg", "middle", "hanging");
  }

  /** Return the status line of a weekly page: the last 14-day value and the direction. */
  _trendStatus(pg) {   // "Last 14 days: 71%  Stable" (shared by both cards)
    const tc = this._bf.trends.length;
    const cur = tc > 0 ? this._trendVal(0, pg) : NaN;
    // direction: GlucoCraft's (last 4 weeks vs the 4 before, in plain words), from the file - one system
    const dirKey = this._bf.dirs?.[pg], direction = dirKey ? gcT(dirKey) : "---";
    if (pg === 8 && tc > 0) {
      const t = this._bf.trends[0], has = t.mean > 0;
      const min = !has ? "---" : gF32(t.min), max = !has ? "---" : gF32(t.max);
      return gcT("card.weekly.status_min_max", { min, max, direction });
    }
    if (tc === 0) return gcT("card.weekly.status_no_data");
    const value = isNaN(cur) ? "---" : pg === 2 || pg === 7 ? `${gF32(cur)} ${gU()}`
      : pg === 4 ? gmiText(cur, this._bf.trends[0].mean)   // GMI in the A1C unit
        : `${nf(...this._pctShown(cur, pg))}%`;
    return gcT("card.weekly.status_last_14_days", { value, direction });
  }

  /** Draw the chart of a weekly page: target background, the 26 weekly values and the grid. */
  _trendCanvas(pg) {
    const T = this.L.trend, cw = T.w, ch = T.h, top = T.y;
    const [ymin, ymax] = this._trendScale(pg);
    const yr = ymax - ymin, vy = (v) => ch - Math.floor((v - ymin) / yr * ch), zy = (v) => Math.min(Math.max(vy(v), 0), ch);
    const chart = { cw, ch, ymin, ymax, vy, zy };
    // on black with the colors at 60 %, like the glucose graphs
    let out = `<svg x="${T.x}" y="${top}" width="${cw}" height="${ch}" viewBox="0 0 ${cw} ${ch}" overflow="hidden">` +
              `<rect width="${cw}" height="${ch}" fill="#0a0a0a"/>`;
    const std = this._std(pg);
    out += this._trendBands(std.target, chart);
    const [columns, weekly] = this._trendWeeks(pg, chart);   // the weekly line(s) are drawn after the grid
    out += columns + this._trendGrid(pg, chart);
    out += this._ot(cw - 10, 5, std.label, 16, 0xFFFFFF, "end", "hanging");   // "Consensus target" / "Custom goal" where one exists
    // bands, grid, the weekly line(s), then the labels (as on the panels)
    return this._textsLast(out + weekly) + "</svg>";
  }
  // the weekly chart's scale: the page's fixed ends, or the weeks' values (and on the Lowest and highest page their
  // highest values too) fitted as on every graph (gcFit)
  _trendScale(pg) {
    const def = TREND_DEFS[pg];
    let ymin = def[1], ymax = def[2];
    if (this._bf && this._bf.trends.length) {
      let mn = Infinity, mx = -Infinity;
      this._bf.trends.forEach((t, w) => {
        [this._trendVal(w, pg), pg === 8 && t.mean > 0 ? t.max : NaN].forEach((v) => { if (isFinite(v)) { mn = Math.min(mn, v); mx = Math.max(mx, v); } });
      });
      if (isFinite(mn)) [ymin, ymax] = gcFit(mn, mx, TREND_MIN_SPREAD[pg]);
    }
    return [ymin, ymax];
  }
  // the weekly chart's background (SVG): green within the cited consensus target or the goal tg ([op, value]), red
  // outside it, split exactly at it, with a light gray line there; neutral without one. chart: _trendCanvas's scale.
  _trendBands(tg, { cw, ch, ymin, ymax, zy }) {
    if (!tg) {
      // neutral = no target or goal
      return `<rect width="${cw}" height="${ch}" fill="${hex(C.neutral)}" fill-opacity="0.6" data-band="neutral"/>`;
    }
    let out = "";
    const target = tg[1], up = tg[0] === ">" || tg[0] === ">=";
    const ty = target >= ymax ? 0 : target <= ymin ? ch : zy(target);
    const above = up ? "inside" : "outside", below = up ? "outside" : "inside";
    if (ty > 0) out += `<rect x="0" y="0" width="${cw}" height="${ty}" fill="${hex(C[above])}" fill-opacity="0.6" data-band="${above}"/>`;
    if (ty < ch) out += `<rect x="0" y="${ty}" width="${cw}" height="${ch - ty}" fill="${hex(C[below])}" fill-opacity="0.6" data-band="${below}"/>`;
    // the line at the weekly target or goal: solid and light gray, between the two colors
    if (ty > 0 && ty < ch) out += `<rect x="0" y="${ty}" width="${cw}" height="1" fill="#bbbbbb"/>`;
    return out;
  }
  /**
   * Return the weeks of a weekly chart (SVG): [the column lines and dates, the weekly line(s)], both "" without the
   * history file. chart: _trendCanvas's scale.
   */
  _trendWeeks(pg, { cw, ch, ymin, ymax, vy }) {
    if (!(this._bf && this._bf.trends.length > 0)) return ["", ""];
    let out = "";
    // a single week is drawn as its dot at the left edge, as on the panels (a line needs two weeks)
    // the column step as the panels compute it (float32), so that every week sits on the same pixel
    const tc = this._bf.trends.length, xs = tc > 1 ? Math.fround((cw - 20) / (tc - 1)) : 0, ml = 10;
    const xOf = (i) => ml + Math.floor(Math.fround(i * xs));
    for (let i = 0; i < tc; i += 4) out += `<rect x="${xOf(i)}" y="0" width="1" height="${ch - 16}" fill="#2a2a2a"/>`;   // solid, like the panel
    out += `<rect x="0" y="0" width="1" height="${ch}" fill="#2a2a2a"/><rect x="${cw - 1}" y="0" width="1" height="${ch}" fill="#404040"/>`;
    // the date under every 4th week: the record's start, the week's end minus 7 days, which is the middle of its
    // 14-day window ("Oct 12" / "12 Oct"); as on the panels, a label is left out where it would come closer than
    // 4 px to the one before it or to the chart's edges
    let prevEnd = -Infinity;
    for (let i = 0; i < tc; i += 4) {
      const t = this._bf.trends[tc - 1 - i]; if (!t.start) continue;
      const lbl = dayMonth(new Date(t.start * 1000)), lx = xOf(i), half = textWidth14(lbl) / 2;
      const cx = Math.max(lx + 7, 22);
      if (lx < 10 || cx - half < 4 || cx + half > cw - 4 || cx - half < prevEnd + 4) continue;
      out += this._ot(cx, ch - 15, lbl, 14, 0xFFFFFF, "middle", "hanging");
      prevEnd = cx + half;
    }
    // one line of weekly values (get: the value of week w, NaN for none), with a dot at each week: [the black
    // underlay (4 px, a 5 x 5 square under each dot), the white line (2 px) and its 3 x 3 dots], as on the panels
    const line = (get) => {
      let d = "", under = "", dots = "", pen = false;
      for (let i = 0; i < tc; i++) {
        const raw = get(tc - 1 - i);
        if (isNaN(raw)) { pen = false; continue; }       // a week without a value: gap in the line
        const v = Math.max(ymin, Math.min(ymax, raw)), x = xOf(i), y = vy(v);
        d += (pen ? "L" : "M") + `${x} ${y}`; pen = true;
        under += `<rect x="${x - 2}" y="${y - 2}" width="5" height="5" fill="#000000"/>`;
        dots += `<rect x="${x - 1}" y="${y - 1}" width="3" height="3" fill="#fff"/>`;
      }
      return [`<path d="${d}" fill="none" stroke="#000000" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" data-under="1"/>` + under,
        `<path d="${d}" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>` + dots];
    };
    const series = [line((w) => this._trendVal(w, pg))];
    if (pg === 8) series.push(line((w) => (this._bf.trends[w].mean > 0 ? this._bf.trends[w].max : NaN)));
    // every underlay first, then the white lines, as on the panels
    return [out, series.map((s) => s[0]).join("") + series.map((s) => s[1]).join("")];
  }
  // the weekly chart's grid lines with their value labels (SVG), in the display unit: on the GMI page in mmol/mol
  // when that is the A1C unit (the GMI rule; the positions stay in %), on the glucose pages in mmol/L
  _trendGrid(pg, { cw, ch, ymin, ymax, vy }) {
    let out = "";
    if (pg === 4 && GC.a1c === "mmol_mol") {   // GMI page: grid in mmol/mol (gmiPctToMmol), positions in % (gmiMmolToPct)
      const lo = gmiPctToMmol(ymin), hi = gmiPctToMmol(ymax), step = gcGridStep(lo, hi, 4);
      for (let k = Math.ceil(lo / step - 1e-9); k * step <= hi + 1e-9; k++) {
        const mm = k * step, gy = vy(gmiMmolToPct(mm)); if (gy <= 8 || gy >= ch - 24) continue;
        out += `<rect x="0" y="${gy}" width="${cw}" height="1" fill="#404040"/>` +
               this._ot(5, gy - 7, this._axisText(mm, step), 14, 0xFFFFFF, "start", "hanging");
      }
    } else {
      const sf = GC.mmol && (pg === 2 || pg === 7 || pg === 8) ? MGDL_PER_MMOLL : 1;   // glucose pages: grid in mmol/L
      const step = gcGridStep(ymin / sf, ymax / sf, 4);
      for (let k = Math.ceil(ymin / sf / step - 1e-9); k * step * sf <= ymax + 1e-9; k++) {
        const gd = k * step, g = gd * sf;
        const gy = vy(g); if (g < -1e-9 || gy <= 8 || gy >= ch - 24) continue;
        out += `<rect x="0" y="${gy}" width="${cw}" height="1" fill="#404040"/>` +
               this._ot(5, gy - 7, this._axisText(gd, step), 14, 0xFFFFFF, "start", "hanging");
      }
    }
    return out;
  }

  /**
   * Draw the About GlucoCraft page in the sizes o, as on the panels (glucocraft-panel.yaml about_screen and the wide
   * panels' tools/landscape_about.yaml; the page buttons stay below it): the square card stacks GlucoCraft's icon,
   * the disclaimer's first sentence as the title and the rest of it, centered in the room above the footer; the wide
   * card puts the icon in a column of its own on the left, centered from top to bottom, and the title, the disclaimer
   * and the footer centered in the column to its right. The footer at the bottom: a thin line in the frame color
   * (40 % of the footer's width), then the version line and the copyright line. In the panel's sizes (o); smaller only
   * if the browser's font needs more room than Roboto.
   */
  _aboutFlow(o) {
    const { W, H } = this.L;
    const tw = o.square ? Math.floor(W * 0.92) : W - 2 * o.top - o.icon - o.ig;   // the text column
    const cx = o.square ? W / 2 : o.top + o.icon + o.ig + tw / 2;
    const [lead, rest] = disclaimerParts();
    let f;
    for (let k = 0; ; k++) {
      const sz = (v) => Math.max(14, v - k);   // never below 14 px (ACCESSIBILITY.md rule 3)
      f = { head: sz(o.head), rest: sz(o.rest), ver: sz(o.ver) };
      f.leadL = wrapText(lead, f.head, tw);
      f.restL = wrapText(rest, f.rest, tw);
      f.verL = wrapText(versionLine(), f.ver, tw);
      f.copyL = copyrightLines().flatMap((l) => wrapText(l, f.ver, tw));
      f.text = f.leadL.length * lhOf(f.head) + o.gap + f.restL.length * lhOf(f.rest);
      f.foot = 9 + 4 + f.verL.length * lhOf(f.ver) + 4 + f.copyL.length * lhOf(f.ver);
      f.need = o.top + o.bottom + (o.square ? o.icon + o.gap + f.text + f.foot : Math.max(o.icon, f.text + f.foot));
      if (f.need <= H || (f.rest <= 14 && f.head <= 14)) break;
    }
    const footTop = H - o.bottom - f.foot, block = o.square ? o.icon + o.gap + f.text : f.text;
    let y = o.top + Math.max(0, (footTop - o.top - block) / 2);   // centered in the room above the footer
    let out = `<rect x="0" y="0" width="${W}" height="${H}" style="fill:var(--gc-bg)"/>`;
    const iy = o.square ? y : o.top + (H - o.top - o.bottom - o.icon) / 2;
    out += `<image href="${ICON_URI}" x="${o.square ? (W - o.icon) / 2 : o.top}" y="${iy}" width="${o.icon}" height="${o.icon}"/>`;
    if (o.square) y += o.icon + o.gap;
    const line = (str, size, color, extra = "") => {
      out += this._t(cx, y + lhOf(size) / 2, str, size, color, "middle", "central", extra); y += lhOf(size);
    };
    f.leadL.forEach((l) => line(l, f.head, "fg", 'font-weight="700"'));
    y += o.gap;
    f.restL.forEach((l) => line(l, f.rest, "fg"));
    const rule = Math.round((o.square ? W : tw) * 0.4);
    out += `<rect x="${Math.round(cx - rule / 2)}" y="${footTop}" width="${rule}" height="1" style="fill:var(--gc-box)"/>`;
    y = footTop + 9 + 4;
    f.verL.forEach((l) => line(l, f.ver, "fg2"));
    y += 4;
    f.copyL.forEach((l) => line(l, f.ver, "fg2"));
    return out;
  }
  // the square panel's sizes (480 x 480: margins 19 / 12 px, icon 96 px on top, 12 px apart; title 22 px, the
  // disclaimer 18 px, the footer 16 px)
  _aboutPage() {
    return this._aboutFlow({ square: true, top: 19, bottom: 12, icon: 96, ig: 8, head: 22, rest: 18, ver: 16, gap: 12 });
  }
}

// ==================================================================================== rectangular card (800 x 480)
/**
 * The rectangular display card (custom:glucocraft-display-card-wide), in the 800 x 480 panel's layout.
 *
 * The same height as the square card, wider, the same functions; the 800 x 480 panel's layout:
 * header 64 px (clock, weekday and date), the glucose block on the left (352 px: value 160 px, zone word, TREND and
 * 5 MIN DELTA), the graph pages on the right (graph 430 x 228, twelve statistics in two rows of six) or the weekly
 * pages (title, description, "Last 14 days", chart 430 x 248), the status bar (40 px) at the bottom.
 */
class GlucoCraftDisplayCardWide extends GlucoCraftDisplayCard {
  get D() { return LAYOUT_WIDE; }

  _blockArea() { return [0, 64, this.D.block.w, 376]; }
  // like the panel: the glucose block opens the weekly pages from a graph page and returns from a weekly page; the
  // weekly header returns too
  _blockHit(x, y) { return super._blockHit(x, y) || (this._page >= 7 && x >= this.D.block.w && y >= 64 && y < 184); }
  // the glucose block stays on the left; a weekly page's title, status and chart are on the right
  _pageSvg(s, p, trend) {
    return this._header(s) + this._dataRow(s) + this._statusBar(s) +
      (trend ? this._trendInfo(p - 7) + this._trendCanvas(p - 7) : this._graphArea(s, p));
  }

  _header(s) {
    const { W, H } = this.L, d = s.now;
    return `<rect x="0" y="0" width="${W}" height="${H}" style="fill:var(--gc-bg)"/>` +
      this._t(16, 33, clockStr(d), 56, "fg", "start") +
      this._t(W - 16, 19, weekdayName(d), 22, "fg2", "end") + this._t(W - 16, 45, dayMonth(d, true), 22, "fg2", "end");
  }

  /** Draw the glucose block on the left of the rectangular layout. */
  _dataRow(s) {
    const { w: LW, colY, colH } = this.D.block, cx = LW / 2;
    const gv = this._valText(s);
    const colW = LW / 2, colMid = colY + colH / 2 + 2;
    const trend = s.words ? this._noTrend(colW / 2, colMid, 64)
      : this._trendCell(s, colW / 2, colMid, 80, TREND_WORD.wide, TREND_WORD.wide.room);
    const src = this._fit14(s.source, colW - 10);
    const dStr = this._deltaText(s);
    const nr = NO_READING.wide;
    const value = s.words ? this._noReading(s, cx, nr.cy, ZONE_SLOT.wide.size, nr.room, nr)
      : s.limit ? this._limitScreen(s, cx, nr.cy, ZONE_SLOT.wide.size, nr.room, nr)
        : this._drawValue(s, gv, WIDE_VALUE) + this._zoneSlot(s, gv, cx, ZONE_SLOT.wide, 344);
    return value +
      this._t(colW / 2, colY + 6 + 9, gcT("card.label.trend"), 16, "fg2") + trend +
      this._t(colW / 2, colY + colH - 8 - 9, src, 14, "fg2") +
      this._t(colW + colW / 2, colY + 6 + 9, gcT("card.label.delta_5_min"), 16, "fg2") +
      this._t(colW + colW / 2, colMid, dStr, dStr.length > 3 ? 40 : 64, "fg3");
  }

  /** Draw the status bar of the rectangular layout (y 440-480, texts at 20 px, 16 px from the edges). */
  _statusBar(s) { return this._bar(s, this.L.W, 440, 40, 20, 16, this._warmUpText(s)); }

  /** Draw the graph and the two rows of statistics of the rectangular layout. */
  _graphArea(s, p) {
    const { GX, GW } = this.L;
    const rows = this._statRows(p);   // rows of six 71 x 58 cells
    let out = "";
    [310, 376].forEach((ry, r) => {
      out += `<rect x="${GX + 0.5}" y="${ry + 0.5}" width="${GW - 1}" height="61" rx="4" style="fill:var(--gc-bg);stroke:var(--gc-box)" stroke-width="1"/>`;
      rows[r].forEach(([id, val], i) => {
        const cx = GX + 1 + i * 71 + 35.5;
        out += this._t(cx, ry + 18, statLabel(id), 14, "fg2");
        // GMI in mmol/mol: the value and its unit on two smaller lines (17 and 14 px); in both A1C units one line
        // at 14 px (_statRows)
        if (Array.isArray(val)) out += val[1] ? this._t(cx, ry + 36, val[0], 17, "fg") + this._t(cx, ry + 52, val[1], 14, "fg2")
          : this._t(cx, ry + 41, val[0], val[2] || 20, "fg");
        else out += this._t(cx, ry + 41, val, 20, "fg");
      });
    });
    return out + (p === 0 ? this._graph6h(s) : this._graphPeriod(s, p));
  }

  // the panel's weekly header (x 362-790, y 64-184): the title, the description wrapped at 428 px into two lines at
  // most and the status line below them, as on the 800 x 480 panel
  _trendInfo(pg) {
    const { x, w } = this.D.info, cx = x + w / 2;
    let out = this._t(cx, 64 + 10 + 15, gcT(TREND_DEFS[pg][0]), 26, "fg");
    wrapText(this._std(pg).desc, 16, w).slice(0, 2).forEach((l, i) => {
      out += this._t(cx, 64 + 50 + i * lhOf(16), l, 16, "fg3", "middle", "hanging");
    });
    const t = this._bf ? this._trendStatus(pg) : this._waitText();
    return out + this._t(cx, 64 + 90 + 12, t, textWidth(t, 20) > w ? 16 : 20, this._bf ? "fg" : "fg2");
  }

  // the 800 x 480 panel's sizes (tools/make_glucocraft_landscape.py LAYOUTS: margins 32 / 12 px, the icon column
  // 112 px + 16 px; title 32 px, the disclaimer 24 px, 10 px apart; the footer 18 px)
  _aboutPage() {
    return this._aboutFlow({ square: false, top: 32, bottom: 12, icon: 112, ig: 16, head: 32, rest: 24, ver: 18, gap: 10 });
  }
}

if (!customElements.get("glucocraft-display-card")) customElements.define("glucocraft-display-card", GlucoCraftDisplayCard);
if (!customElements.get("glucocraft-display-card-wide")) customElements.define("glucocraft-display-card-wide", GlucoCraftDisplayCardWide);   // loaded twice (resource + panel): no error
// The card picker's entries (the file is loaded twice: added once), with their texts in the frontend's language: from
// the hass object of Home Assistant's <home-assistant> element, an undocumented property, read only here because the
// entries are made before any card receives hass; without it, the language of the page (<html lang>)
window.customCards = window.customCards || [];
const GC_PICKER = [
  ["glucocraft-display-card", "card.picker.square.name", "card.picker.square.description"],
  ["glucocraft-display-card-wide", "card.picker.rectangular.name", "card.picker.rectangular.description"]];
gcI18nLoad(globalThis.document?.querySelector?.("home-assistant")?.hass).then(() => {
  for (const [type, name, description] of GC_PICKER) {
    if (!window.customCards.some((c) => c.type === type)) window.customCards.push({ type, name: gcT(name), description: gcT(description) });
  }
});
console.info(`%c GLUCOCRAFT-DISPLAY-CARD %c v${GCDC_VERSION} `, "background:#43A047;color:#000", "background:#000;color:#fff");
