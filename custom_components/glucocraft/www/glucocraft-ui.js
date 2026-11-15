// License: AGPL-3.0-only (LICENSE).
/*
 * glucocraft-ui - GlucoCraft's dashboard cards. One system: every value, word, color and
 * direction comes from GlucoCraft's entities and files (docs/DISPLAY_CONTRACT.md); these cards only lay them out.
 *
 *   custom:glucocraft-banners        attention banners (no current data, reading times ahead of the clock, sensor
 *                                    expired / expiring, warm-up, 2nd source)
 *   custom:glucocraft-readout        a headline card (hero) or a report card that opens its page
 *   custom:glucocraft-stats-table    the statistics table for a set of periods, consensus ranges or custom ranges
 *   custom:glucocraft-history        a glucose graph for one period, by timestamp, on black with the range bands
 *   custom:glucocraft-change         Change Over Time: the 4-week comparison table (basis consensus / personal = Custom Ranges)
 *   custom:glucocraft-change-chart   Change Over Time: one measure over 26 weeks (14-day line, 7-day dots)
 *   custom:glucocraft-core           the core measures: last 14 days, ✓ / ✗, the change since 22–26 weeks ago (basis consensus / personal)
 *   custom:glucocraft-details        a collapsible section holding further cards (closed by default)
 *   custom:glucocraft-statistics     the statistics of the Consensus Ranges page (seven periods, targets and sources)
 *   custom:glucocraft-pages          navigation: a row per report page and three buttons; the size of the display card
 *   custom:glucocraft-start-pair     the display card and the pages card as one centered pair (stacked when both do not fit)
 *   custom:glucocraft-settings-button  Settings Status: the button that opens GlucoCraft's settings
 *   custom:glucocraft-export         Maintenance: the Save CSV export button (action glucocraft.export_csv) and its status
 *   custom:glucocraft-maintenance-issues  Maintenance: the issues of the Maintenance status (shown only while there are any)
 *   custom:glucocraft-custom-diff    Custom Ranges: where the custom ranges and goals differ from the consensus ranges
 *   custom:glucocraft-footer         the page footer: color key, disclaimer line, standard statement, license line
 *   custom:glucocraft-daily-range    Custom Ranges: each calendar day's mean, lowest and highest reading
 *
 * The options of each card: docs/DASHBOARD.md, "Cards on other dashboards" (the table's Options column).
 *
 * Look (without card_mod): status colors only where they mean something; headlines
 * with a 6-px edge and a gradient in the status color; banners with a 6-px edge and a 10 % tint.
 */
const GCUI_VERSION = "0.0.1";
// The language catalogues (glucocraft-i18n.js, served next to this file): every text comes from them, in Home
// Assistant's language with English as the fallback. The card waits for gcI18nLoad before its first render.
const GC_I18N_AREAS = ["frontend"];
const GC_I18N = globalThis.glucocraftI18n ? Promise.resolve() : import(new URL("./glucocraft-i18n.js", import.meta.url).href);
const gcI18nLoad = (hass) => GC_I18N.then(() => globalThis.glucocraftI18n.load(hass, GC_I18N_AREAS));
const gcI18nReady = (hass) => !!globalThis.glucocraftI18n?.ready(hass, GC_I18N_AREAS);
const gcT = (key, params) => globalThis.glucocraftI18n.t(key, params);
// The rules the cards and pages share (glucocraft-shared.js, served next to this file and imported the same way): the
// settings in use and the number, glucose, time and date formats, the spoken text, gcRender, the gap rule, the default
// colors and the GLU2 parser. The file waits for it before it defines its cards.
const GC_SHARED = globalThis.glucocraftShared ? Promise.resolve() : import(new URL("./glucocraft-shared.js", import.meta.url).href);
await GC_SHARED;
const { settings: GC, applyRegional, round, nf, dtx, shownAgainst, nfAt, MGDL_PER_MMOLL, GLU_MAX, isMmol, gU, gD, gF,
  gmiMmolFromMean, gmiPctToMmol, setTimeZone, zoneParts, zoneTime, zoneDate, sameZoneDay, clockStr, hourLabel, numDate,
  weekdayName, esc, gcSpeakNumber, gcSpeak, gcSpeakA1c,
  gcSpeakAge, gcSpeakTrend, gcSpeakLimit, gcLimitParts, gcLimitSaid, gcSpeakText, gcRender, GAP_WINDOW,
  gcGaps, gcGapIn, gcRunIn, first, gcRangeAreas,
  ZONE_DEFAULT, BAND_DEFAULT, gcPalette, gcOnColor, css: GC_CSS, gcFile, gDelta, gcAxisText, gcLimitMarks,
  ENOUGH_DATA_PCT, gcEnoughDataWait, GC_CONS_GOAL, gcMeets, gcGapThreshold, gcFit, gcGridStep, gcFileFailed,
  gcNavigate, gcPlainClick, gcNewFailure, gcCap, CONS_RANGES, RETENTION_DAYS } = globalThis.glucocraftShared;
// the language of the texts in use, for the lang attributes ("en" until a catalogue is loaded)
const gcLang = () => globalThis.glucocraftI18n?.htmlLang || "en";
// whole sentences, and the items of a list, joined as the language in use joins them (" " and "; " in English)
const gcSentences = (parts) => parts.filter(Boolean).join(gcT("ui.join.sentences"));
const gcList = (items) => items.join(gcT("ui.join.list"));

const STATUS = { ok: "#43A047", attention: "#F9A825", alert: "#E53935", unknown: "#9E9E9E", nothing: "#7E57C2" };
// A status color used for text or a mark (✓ / ✗) on the theme background: the same hue mixed with the theme's text
// color, darker on a light theme and lighter on a dark one, so that small text reaches 4.5:1 in both (ACCESSIBILITY.md
// rule 2). Larger areas (edges, chart backgrounds) keep the status color itself.
const stc = (col) => `color-mix(in srgb, ${col} 50%, var(--primary-text-color))`;
// A status color used for an icon and an edge on the pages card's buttons: 80% of the status color with the card's text
// color (--gp-fg), so that it reaches 3:1 (WCAG 1.4.11) on the button in every palette of the card and in Home
// Assistant's default themes (green on the light theme's #E5E5E5: 3.67:1; the status color itself: 2.62:1)
const statusEdge = (col) => `color-mix(in srgb, ${col} 80%, var(--gp-fg))`;
const NEUTRAL = "#616161";   // charts without a target or goal: neutral, no meaning
// The range colors (zone_colors: the LO / HI marks and everything else that names a zone by color) and the graph band
// colors (band_colors: the range bands of the graphs and their key), published on sensor.glucocraft_configuration and
// read in GCBase; the defaults of glucocraft-shared.js until they arrive
const gcZoneColors = (published) => gcPalette(ZONE_DEFAULT, published);
const gcBandColors = (published) => gcPalette(BAND_DEFAULT, published);
let ZONE = { ...ZONE_DEFAULT }, BAND = { ...BAND_DEFAULT };
// black or white text on a colored area "#RRGGBB", whichever has the higher contrast (glucocraft-shared.js gcOnColor)
const onColorHex = (hex) => (gcOnColor(parseInt(String(hex).slice(1), 16)) ? "#ffffff" : "#000000");
const AGP = CONS_RANGES;   // the consensus ranges in mg/dL (glucocraft-shared.js)
const ENT = {
  status: "sensor.glucocraft_status", source: "sensor.glucocraft_active_source", config: "sensor.glucocraft_configuration",
  current: "sensor.glucocraft_last_glucose_reading", last: "sensor.glucocraft_last_reading_time",
  expiry: "sensor.glucocraft_sensor_expiry", warmup: "binary_sensor.glucocraft_sensor_warm_up",
  ranges: "binary_sensor.glucocraft_using_custom_ranges",
  // computed_at: the time of the last statistics pass (it changes with every pass, as the weeks do)
  weekly: "sensor.glucocraft_weekly_statistics",
  // no current data: the one fact every readout tests, never the status (whose priority puts an expired sensor first)
  nocur: "binary_sensor.glucocraft_no_current_data",
};
// The period summary sensors: sensor.glucocraft_<period>_consensus_ranges (every value on the consensus ranges) and,
// while the optional feature Custom ranges and goals is on, sensor.glucocraft_<period>_custom_ranges (every value on
// the ranges in the settings). The cards read the period results only from these, never from the optional entities of
// Separate statistics entities; glucose values are mg/dL attributes.
const gcSummaryId = (p, custom) => `sensor.glucocraft_${p}_${custom ? "custom" : "consensus"}_ranges`;
// a statistic of the custom ranges summary by its statistics-table key (the attribute names of the other keys are the
// keys themselves)
const GC_CUSTOM_ATTR = { below_low: "time_below_low", above_high: "time_above_high", min: "min_value", max: "max_value" };
// the names of the periods in tables and warnings ("6 h", "7 days", "6 months")
const PERIOD_KEY = { "6h": "ui.period.6h", "24h": "ui.period.24h", "7d": "ui.period.7d", "14d": "ui.period.14d",
  "30d": "ui.period.30d", "90d": "ui.period.90d", "180d": "ui.period.180d" };
const periodName = (p) => gcT(PERIOD_KEY[p]);
const PERIOD_DAYS = { "6h": 0.25, "24h": 1, "7d": 7, "14d": 14, "30d": 30, "90d": 90, "180d": 180 };

// ------------------------------------------------------------------ formatting
// A visible text with unit symbols (a table cell, a label) as HTML for screen readers: shown as written and hidden from
// screen readers, which read its spoken form (gcSpeakText) instead; a text without symbols stays as it is.
// sr: a spoken form of the caller's own, where gcSpeakText cannot derive it (a time per day such as "21 h 10 min").
// "---", the mark of a value that does not exist, is read as "not available", with its unit if it has one ("--- mg/dL").
const GC_NO_VALUE = "---";
const gcNoValueSaid = (text) => String(text).replace(/(^|[^-])---(?: ?(?:mg\/dL|mmol\/L|mmol\/mol|%))?(?!-)/g,
  (m, before) => before + gcT("spoken.not_available"));
const gcSay = (text, sr = gcSpeakText(gcNoValueSaid(text))) =>
  (sr === String(text) ? esc(text) : `<span aria-hidden="true">${esc(text)}</span><span class="sr">${esc(sr)}</span>`);
// The same for a catalogue text that carries its own markup (<i>, <b>): shown as written, and read as its plain text
// made speakable
const gcSayHtml = (html) => {
  const plain = String(html).replace(/<[^>]*>/g, ""), sr = gcSpeakText(gcNoValueSaid(plain));
  return sr === plain ? html : `<span aria-hidden="true">${html}</span><span class="sr">${esc(sr)}</span>`;
};
const isNum = (v) => typeof v === "number" && isFinite(v);
// GMI in the A1C unit setting from its % value and its value in mmol/mol (published as gmi_mmol_mol, or from the mean);
// without mm (a GMI goal entered in %), from the % value by the GMI rule (gmiPctToMmol, the mean the GMI was calculated
// from), so that a goal and the values it is compared with use the same rule (the IFCC-NGSP master equation is for
// laboratory A1C results, which these cards do not show)
const gmiTxt = (pct, mm) => {
  if (!isNum(pct)) return "---";
  const m = isNum(mm) ? mm : round(gmiPctToMmol(pct));
  return GC.a1c === "mmol_mol" ? `${m} mmol/mol` : GC.a1c === "both" ? `${nf(pct, 1)}% (${m} mmol/mol)` : `${nf(pct, 1)}%`;
};
// the difference of two GMI values in mmol/mol: dm, the difference of their values in mmol/mol, or without it the
// difference d in percentage points converted by the same GMI rule
const gmiDiffMmol = (d, dm = NaN) => (isNum(dm) ? dm : round(gmiPctToMmol(d) - gmiPctToMmol(0)));
// a difference of two GMI values in the A1C unit setting: d in percentage points ("+0.2 percentage points"), dm as above
const a1cDiff = (d, dm = NaN) => {
  if (!isNum(d)) return "---";
  const mm = gmiDiffMmol(d, dm), mmol = `${mm > 0 ? "+" : ""}${mm} mmol/mol`;
  return GC.a1c === "mmol_mol" ? mmol : GC.a1c === "both" ? gcT("ui.difference.a1c_both", { points: gcPoints(d), mmol_mol: mmol })
    : gcPoints(d);
};
const n0 = (v) => (isNum(v) ? String(round(v)) : "---");
const n1 = (v) => (isNum(v) ? nf(v, 1) : "---");
const pc = (v, d = 0) => (isNum(v) ? `${nf(v, d)}%` : "---");
// a percentage next to a rule it is compared with: two decimals where d decimals would show the rule's value on the
// wrong side of it (nfAt, units.shown_against: 69.96% beside "more than 70%")
const pcAt = (v, rules, d = 0) => (isNum(v) ? `${nfAt(v, rules, d)}%` : "---");
// Glucose in the display unit (glucocraft-shared.js gD / gF; mg/dL whole numbers, mmol/L one decimal, the consensus
// thresholds as published): a value in mg/dL as a number (NaN without one) and as text
const gv = (mg) => (isNum(mg) ? gD(mg) : NaN);
const gs = (mg) => (isNum(mg) ? gF(mg) : "---");
const gr = (a, b) => `${gs(a)}–${gs(b)}`;
// a limit (mg/dL) as the number shown in the display unit: mg/dL whole (a limit entered in mmol/L is stored with one
// decimal in mg/dL, 8.9 mmol/L = 160.3 mg/dL, and shown as 160), mmol/L one decimal
const gShown = (mg) => (isMmol() ? gv(mg) : round(mg));
const gUpTo = (a, b) => (isMmol() ? `${gs(a)}–${nf(gv(b) - 0.1, 1)}` : `${gs(a)}–${gShown(b) - 1}`);   // from a to just below b
const gFrom = (a, b) => (isMmol() ? `${nf(gv(a) + 0.1, 1)}–${gs(b)}` : `${gShown(a) + 1}–${gs(b)}`);   // from just above a to b
// the same, null where the range holds no value as shown (its upper end below its lower end: two limits less than one
// display step apart), so that no key or table shows an impossible "76–75"
const gStepOf = () => (isMmol() ? 0.1 : 1);
const gcUpToShown = (a, b) => (round(gShown(b) - gStepOf(), 1) < gShown(a) ? null : gUpTo(a, b));
const gcFromShown = (a, b) => (round(gShown(a) + gStepOf(), 1) > gShown(b) ? null : gFrom(a, b));
const gCv = (v) => (isNum(v) && isMmol() ? v / MGDL_PER_MMOLL : v);                                // plotting value
const gR = (r) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, isMmol() ? gv(v) : v]));
// the weekday's short name, a word of the catalogue (LANGUAGE_STYLE rule 12, as the display card's), then the date in
// the date format of the settings (glucocraft-shared.js weekdayName, numDate)
const dayShort = (d) => `${weekdayName(d, true)} ${numDate(d)}`;
// the same day in Home Assistant's time zone (every time on the dashboard is in that zone)
const sameDay = (a, b) => sameZoneDay(a, b);
// A duration in minutes in full words, in the two largest units: "5 minutes", "2 hours 5 minutes", "3 days 4 hours"
// (an age in a banner, the time remaining until the sensor expires)
const GC_DURATION_KEY = { min: "ui.duration.minute", h: "ui.duration.hour", d: "ui.duration.day" };
const durUnit = (number, unit) => gcT(GC_DURATION_KEY[unit], { count: number, number });
// the duration of min minutes in words (rounded to whole minutes); "---" without a number
const dur = (min) => {
  if (!isNum(min)) return "---";
  const m = Math.max(0, round(min));
  const two = (larger, smaller) => (smaller ? gcT("ui.duration.two_units", { larger, smaller }) : larger);
  if (m < 60) return durUnit(m, "min");
  if (m < 1440) return two(durUnit(Math.floor(m / 60), "h"), m % 60 ? durUnit(m % 60, "min") : "");
  const h = Math.floor((m % 1440) / 60);
  return two(durUnit(Math.floor(m / 1440), "d"), h ? durUnit(h, "h") : "");
};
// The time per day of a percentage in the statistics tables, compact so that it fits a phone's narrow column ("21 h 10
// min"; the note under each table names the units): a 24-hour day, 1% = 14.4 minutes
const perDayTable = (pct) => {
  if (!isNum(pct)) return "";
  const m = round(pct / 100 * 1440);
  return m < 60 ? gcT("ui.per_day_table.minutes", { minutes: m })
    : gcT("ui.per_day_table.hours_minutes", { hours: Math.floor(m / 60), minutes: m % 60 });
};

// ------------------------------------------------------------------ LO / HI and the gap rule
// LO / HI: a reading outside the sensor's measurement range is stored at the sensor's limit and marked; every display
// shows its text ("LO" / "HI") instead of a number, in the very-low / very-high color. attributes of
// sensor.glucocraft_last_glucose_reading: limit ("low" | "high" | null), limit_text ("LO" | "HI" | null)
const gcLimit = (st) => {
  const a = st?.attributes || {};
  if (a.limit !== "low" && a.limit !== "high") return null;
  return { limit: a.limit, text: a.limit_text || (a.limit === "low" ? gcT("ui.limit.lo") : gcT("ui.limit.hi")),
    color: a.limit === "low" ? ZONE.very_low : ZONE.very_high };
};
// The gap rule (GlucoCraft rule; glucocraft-shared.js gcGaps): with fewer than 3 intervals around a stretch, the
// fallback threshold applies: the configuration's threshold for the source in use (gap_threshold_seconds = max(15 min,
// 3 x the detected interval)), else it is calculated here (gcGapThreshold).
const gcGap = (ca, interval) => {   // the fallback threshold (s)
  const g = +ca?.gap_threshold_seconds;
  if (g > 0) return g;
  const iv = +ca?.reading_interval_seconds > 0 ? +ca.reading_interval_seconds : +interval > 0 ? +interval : 60;
  return gcGapThreshold(iv);
};
// the number of limit readings of a period: {low, high} -> [total, "2 LO, 1 HI"]
const gcLimitCount = (lr) => {
  const lo = +(lr?.low) || 0, hi = +(lr?.high) || 0;
  const text = lo && hi ? gcT("ui.limit_count.low_high", { low: lo, high: hi })
    : lo ? gcT("ui.limit_count.low", { low: lo }) : hi ? gcT("ui.limit_count.high", { high: hi }) : "";
  return [lo + hi, text];
};
// The same in spoken words, LO, HI and the units written out: "2 readings below the sensor's lower limit for 2 hours 5
// minutes; 1 reading above the sensor's upper limit for 15 minutes"
const gcLimitCountSaid = (lr, ls) => gcList([["low", +(lr?.low) || 0, +ls?.low || 0],
  ["high", +(lr?.high) || 0, +ls?.high || 0]]
  .filter(([, n]) => n > 0)
  .map(([k, n, sec]) => gcT("ui.limit_count.said", { count: n,
    limit: sec > 0 ? gcLimitSaid(k === "low" ? [sec, 0] : [0, sec])[0] : gcSpeakLimit(k) })));
// the same with the time they represent (attribute limit_seconds {low, high}): "2 LO, 1 HI (LO 2 h 5 min · HI 15 min)"
const gcLimitCountTime = (lr, ls) => {
  const [n, text] = gcLimitCount(lr);
  const parts = gcLimitParts([+ls?.low || 0, +ls?.high || 0]);
  return n && parts.length ? gcT("ui.limit_count.with_time", { count: text, time: parts.join(gcT("limit_time.separator")) }) : text;
};

// Enough data for a target or goal: at least 70% sensor active (Battelino et al., 2019, Table 2), compared before
// rounding, as the GMI and the CGM Report compare it (69.96% is shown as 70.0% and is not enough). ok: GlucoCraft's
// attribute sensor_active_ok (Change over time: active_ok), that comparison
const gcActiveOk = (ok) => ok === true;
// The 70% rule of every period (glucocraft-shared.js gcEnoughDataWait): a period's statistics are shown only once the
// readings received cover at least 70% of it (sensor active, compared before rounding); below it GlucoCraft sends none
// (every value null, "---"). Each page states it once per period, in one note that covers all of the period's numbers
// and says, calculated when the page is drawn from the period's length and the time covered so far, about how long it
// still takes with no gaps: "Last 30 days: not enough data yet. GlucoCraft shows a period's statistics once the sensor
// has covered at least 70% of it. So far it has covered 23%. With no gaps, about 14 more days are needed." (hours for
// the periods of up to 24 hours and for less than a day). The current reading, the graphs and the time at the
// sensor's limit are shown as always.
const GC_ENOUGH_PERIOD = { "6h": "ui.enough_data.period.6h", "24h": "ui.enough_data.period.24h",
  "7d": "ui.enough_data.period.7d", "14d": "ui.enough_data.period.14d", "30d": "ui.enough_data.period.30d",
  "90d": "ui.enough_data.period.90d", "180d": "ui.enough_data.period.180d" };
/**
 * Return the 70% rule's state of period p from its summary sensor's attributes a: null while the period has enough
 * data (or nothing is known), else { p, percent (sensor active, unrounded), count, unit } (gcEnoughDataWait).
 *
 * Not enough: GlucoCraft's enough_data false, else sensor_active_ok false; the period's length and the time covered
 * from period_seconds and covered_seconds where published, else from the period's length and sensor active. A period
 * without any reading publishes has_data false alone (no sensor active): not enough, with nothing covered (0%).
 */
const gcEnough = (p, a) => {
  if (!a || !(p in GC_ENOUGH_PERIOD)) return null;
  const has = (k) => a[k] !== null && a[k] !== undefined && isFinite(+a[k]);
  const none = a.has_data === false && !has("sensor_active");
  const ok = typeof a.enough_data === "boolean" ? a.enough_data : none ? false : a.sensor_active_ok;
  if (ok !== false || (!none && !has("sensor_active"))) return null;
  const period = has("period_seconds") && +a.period_seconds > 0 ? +a.period_seconds : PERIOD_DAYS[p] * 86400;
  const covered = none ? 0 : has("covered_seconds") ? +a.covered_seconds : +a.sensor_active / 100 * period;
  return { p, ...gcEnoughDataWait(period, covered), percent: none ? 0 : +a.sensor_active };
};
// the note of one period below 70% (its text, one sentence after another)
const gcEnoughNote = (e) => gcSentences([gcT("ui.enough_data.not_yet", { period: gcT(GC_ENOUGH_PERIOD[e.p]) }),
  gcT("ui.enough_data.rule", { percent: `${ENOUGH_DATA_PCT}%` }),
  gcT("ui.enough_data.covered", { percent: pcAt(e.percent, ENOUGH_DATA_PCT) }),
  gcT(e.unit === "days" ? "ui.enough_data.more_days" : "ui.enough_data.more_hours", { count: e.count })]);
// The notes of the periods below 70%, in the warning box: [[period, the summary sensor's attributes], ...] -> HTML
// ("" when none). A page with two tables of the same periods names each period once (warn_periods).
const gcDataWarnings = (rows) => rows.map(([p, a]) => gcEnough(p, a)).filter(Boolean)
  .map((e) => `<div class="w">${gcSay(gcEnoughNote(e))}</div>`).join("");
// the warning box: primary text on the amber tint (secondary text does not reach 4.5:1 on the tint)
const GC_WARN_CSS = `.w { border-left: 6px solid ${STATUS.attention}; background: color-mix(in srgb, ${STATUS.attention} 10%, ` +
  "var(--ha-card-background, var(--card-background-color))); padding: 8px 12px; margin: 8px 0; font-size: 14px; " +
  "color: var(--primary-text-color); }";
// text read by screen readers only (ACCESSIBILITY.md rule 4; glucocraft-shared.js css.sr)
const GC_SR_CSS = GC_CSS.sr;
// the buttons of the cards: the hover and focus states of every button, and the filled button with its icon (Change
// Settings, Save CSV, Show All Statistics)
const GC_BUTTON_STATES_CSS = `button:hover { filter: brightness(1.1); } button:focus-visible { ${GC_CSS.focus} }`;
const GC_BUTTON_CSS = "button { display: inline-flex; align-items: center; gap: 8px; border: none; border-radius: 20px; " +
  "padding: 10px 20px; font: 500 15px Roboto, 'Noto Sans', sans-serif; letter-spacing: 0.02em; cursor: pointer; " +
  `background: ${GC_CSS.fill}; color: ${GC_CSS.onFill}; flex: none; } ` +
  `${GC_BUTTON_STATES_CSS} button ha-icon { --mdc-icon-size: 20px; margin: 0; }`;

// ------------------------------------------------------------------ base
/** The base of the dashboard cards: settings in use, entity access, redraw when a watched entity changes. */
class GCBase extends HTMLElement {
  setConfig(config) { this._cfg = config || {}; if (!this.shadowRoot) this.attachShadow({ mode: "open" }); this._sig = null; }
  // the new state: the settings in use, then a redraw when a watched entity changed (or a timed card's minute passed)
  set hass(hass) {
    this._hass = hass;
    // the first render waits for the language catalogues (loaded once, for every card); then it draws in full
    if (!gcI18nReady(hass)) {
      this._i18n ||= gcI18nLoad(hass).then(() => { this._i18n = null; this._sig = null; if (this._hass) this.hass = this._hass; });
      return;
    }
    const ca = hass.states[ENT.config]?.attributes || {};
    // the display unit and the regional settings in use (kept while the sensor has no attributes, during a reload)
    if (ca.unit) GC.mmol = ca.unit === "mmol/L";
    applyRegional(ca);
    // every time and date in Home Assistant's time zone (the axes, the banners, the readouts), as on the panels, in
    // the CGM Report and in the notifications
    setTimeZone(hass.config?.time_zone);
    // the range colors and the graph range colors in use (kept during a reload; the defaults until they arrive)
    if (ca.zone_colors) ZONE = gcZoneColors(ca.zone_colors);
    if (ca.unit) BAND = gcBandColors(ca.band_colors);
    // drawn again when a watched entity changed, and every minute for a card whose content depends on the time
    const sig = this.watch().map((e) => hass.states[e]?.last_updated || "-").join("|") + `|${GC.tz}`;
    if (sig !== this._sig || (this.timed() && Date.now() - (this._drawn || 0) > 60e3)) {
      this._sig = sig; this._drawn = Date.now(); this.draw();
    }
  }
  connectedCallback() {
    this._tick = setInterval(() => { if (this._hass && this.timed()) { this._drawn = 0; this.hass = this._hass; } }, 60e3);
  }
  disconnectedCallback() { clearInterval(this._tick); }
  // the card's content: unchanged content stays in place, and the focus stays on its control (gcRender)
  set html(html) { gcRender(this.shadowRoot, html); }
  // A mistyped card option: [option, the value given, the values allowed], or null when the options are valid (each
  // card with such options checks its own).
  optionProblem() { return null; }
  /**
   * Return true, after drawing the message, when a card option is mistyped (optionProblem).
   *
   * The card then names the option, the value given and the values allowed, in place of an empty card or a table of
   * "---", and the browser console says the same once. Checked when the card draws, after the language catalogue is
   * loaded (setConfig runs before that). A card whose options are valid again is drawn afresh.
   */
  optionError() {
    const problem = this.optionProblem();
    if (!problem) {
      if (this._optionError) { this._optionError = null; this.shadowRoot.innerHTML = ""; this.shadowRoot.gcHtml = undefined; }
      return false;
    }
    const [option, value, allowed] = problem, shown = typeof value === "string" ? value : JSON.stringify(value);
    const text = gcT("ui.card.option_error", { option, value: shown, allowed });
    if (this._optionError !== text) {
      this._optionError = text;
      console.warn(`GlucoCraft: card option ${option} of ${this.localName}: ${shown} is not one of ${allowed}`);
    }
    this.html = this.css() + `<style>${GC_WARN_CSS}</style><ha-card lang="${gcLang()}"><div class="w">${gcSay(text)}</div></ha-card>`;
    return true;
  }
  watch() { return [ENT.status]; }
  // true for a card whose content changes with the time alone (an age, a countdown, a window that ends now): it is
  // drawn again every minute as well; the other cards only when an entity they watch changes
  timed() { return false; }
  st(e) { return this._hass?.states[e]; }
  attr(e, a) { return this.st(e)?.attributes?.[a]; }
  cfg() { return this.st(ENT.config)?.attributes || {}; }
  // the attributes of a period's summary sensor (custom: <period> custom ranges, otherwise <period> consensus ranges), null without data
  summary(p, custom = false) { const a = this.st(gcSummaryId(p, custom))?.attributes; return a && a.has_data ? a : null; }
  // the same attributes whether or not the period has data (the 70% rule reads sensor active of a period whose
  // statistics are not shown)
  rawSummary(p, custom = false) { return this.st(gcSummaryId(p, custom))?.attributes || null; }
  // one value of a period's custom ranges summary by its statistics-table key (GC_CUSTOM_ATTR); NaN without one
  custom(p, key) { const v = this.summary(p, true)?.[GC_CUSTOM_ATTR[key] || key]; return isNum(v) ? v : NaN; }
  // at least 70% sensor active in period p, compared before rounding (gcActiveOk); custom: the custom ranges summary
  activeOk(p, custom = false) { const a = this.summary(p, custom); return !!a && gcActiveOk(a.sensor_active_ok); }
  nav(path) { gcNavigate(path); }
  more(e) { this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: e }, bubbles: true, composed: true })); }
  ranges(basis) {
    if (basis !== "yours") return { ...AGP };
    const r = this.cfg().ranges; return r ? { ...r } : { ...AGP };
  }
  // The lowest and highest measured reading of a period as shown (mg/dL in; a reading at the sensor's limit is no
  // value). spoken: the words of a text description.
  minMaxText(mn, mx, spoken = false) {
    const num = (v) => (!isNum(v) ? (spoken ? gcT("spoken.not_available") : "---")
      : spoken ? gcSpeakNumber(gv(v), isMmol() ? 1 : 0, GC.dec) : gs(v));
    return [num(mn), num(mx)];
  }
  // The combined band of a period with the ranges in the settings: all time below the low limit (very low included) or
  // above the high limit (very high included), as GlucoCraft calculates it from the unrounded shares, rounded once (the
  // custom ranges summary's time_below_low / time_above_high).
  combined(p, side) { return this.custom(p, side === "below" ? "below_low" : "above_high"); }
  // the GMI of a period in mmol/mol: the published gmi_mmol_mol, else from the mean of the same period (the GMI rule);
  // custom: from the custom ranges summary, else from the consensus ranges summary (the same values)
  gmiMmol(p, custom) {
    const a = this.summary(p, custom) || {};
    return isNum(a.gmi_mmol_mol) ? a.gmi_mmol_mol : gmiMmolFromMean(a.mean);
  }
  getCardSize() { return 3; }
  getGridOptions() { return { columns: "full" }; }
  /** Return the shared style sheet of the cards. */
  css() {
    return `<style>
      :host { display: block; }
      ha-card { padding: 14px 16px; overflow: hidden; }
      .t { font-size: 16px; font-weight: 600; color: var(--primary-text-color); line-height: 1.35; }
      .s { font-size: 14px; color: var(--secondary-text-color); line-height: 1.45; white-space: pre-line; margin-top: 3px; }
      .row { display: flex; gap: 12px; align-items: flex-start; }
      ha-icon { --mdc-icon-size: 26px; flex: none; margin-top: 2px; }
      .tap { cursor: pointer; }
      .tap:focus-visible, button:focus-visible, a:focus-visible, .tw:focus-visible { ${GC_CSS.focus} }
      ${GC_SR_CSS}
    </style>`;
  }
}

// ------------------------------------------------------------------ banners
/** The attention banners (custom:glucocraft-banners). */
class GlucoCraftBanners extends GCBase {
  watch() { return [ENT.status, ENT.nocur, ENT.source, ENT.last, ENT.expiry, ENT.warmup, ENT.config]; }
  timed() { return true; }   // the ages and the time remaining
  // a pending announcement is dropped with the card, and made again when the card is drawn next
  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._announce) { clearTimeout(this._announce); this._announce = null; this._titles = null; this._sig = null; }
  }
  /** Draw one banner per condition: no current data, sensor expired or expiring, warm-up, second source. */
  draw() {
    const now = new Date(), out = [];
    const status = this.st(ENT.status)?.state;
    const src = this.st(ENT.source)?.state || gcT("ui.banner.the_source");
    const lastIso = this.st(ENT.last)?.state, last = lastIso && !isNaN(Date.parse(lastIso)) ? new Date(lastIso) : null;
    // No current data, as on the displays: GlucoCraft's flag, or this card's own count of the reading's age past the
    // same limit (the flag's threshold_minutes, else the configuration's no_current_data_minutes) while Home Assistant
    // cannot update the flag; not during a warm-up (no readings are expected then). After the stated expiry both
    // banners show.
    const thr = +(this.attr(ENT.nocur, "threshold_minutes") ?? this.cfg().no_current_data_minutes);
    const late = !!last && thr > 0 && (now - last) / 60000 > thr;
    const noCurrent = (this.st(ENT.nocur)?.state === "on" || late) && status !== "warm_up";
    const exp = this.attr(ENT.expiry, "expires_at") ? new Date(this.attr(ENT.expiry, "expires_at")) : null;
    // A reading time more than 5 minutes ahead of Home Assistant's clock cannot be trusted (GlucoCraft's attribute
    // time_untrusted): its age is not shown anywhere, and this banner points to the Maintenance page, whose first card
    // explains what to check; it replaces the no-current-data banner, whose age would be wrong
    const untrusted = this.attr(ENT.last, "time_untrusted") === true;
    if (untrusted) out.push([STATUS.alert, gcT("ui.banner.clock_ahead.title"), gcT("ui.banner.clock_ahead.text")]);
    if (noCurrent && last && !untrusted) {
      out.push([STATUS.alert, gcT("ui.banner.no_data.title"), gcT("ui.banner.no_data.text", { source: src,
        time: sameDay(last, now) ? clockStr(last) : `${dayShort(last)} ${clockStr(last)}`, age: dur((now - last) / 60000) })]);
    }
    if (status === "expired" && exp) {
      out.push([STATUS.alert, gcT("ui.banner.expired.title"), sameDay(exp, now)
        ? gcT("ui.banner.expired.text_today", { time: clockStr(exp) }) : gcT("ui.banner.expired.text", { date: dayShort(exp), time: clockStr(exp) })]);
    }
    if (status === "expiring_soon" && exp) {
      out.push([STATUS.attention, sameDay(exp, now) ? gcT("ui.banner.expiring.title_today", { time: clockStr(exp) })
        : gcT("ui.banner.expiring.title", { date: dayShort(exp), time: clockStr(exp) }),
      gcT("ui.banner.expiring.text", { remaining: dur((exp - now) / 60000) })]);
    }
    if (status === "warm_up") {
      const s = this.attr(ENT.warmup, "started_at"), st = s ? new Date(s) : null;
      out.push([STATUS.nothing, gcT("ui.banner.warm_up.title"), st
        ? gcT("ui.banner.warm_up.text_started", { time: clockStr(st), age: dur((now - st) / 60000) }) : gcT("ui.banner.warm_up.text")]);
    }
    if (this.attr(ENT.source, "second_source_active") && !noCurrent) {
      const primary = this.cfg().primary_source?.name || gcT("ui.banner.the_primary_source");
      out.push([STATUS.attention, gcT("ui.banner.second_source.title"), gcT("ui.banner.second_source.text", { source: src, primary })]);
    }
    // The banners are drawn again every minute (their ages change). Screen readers hear them through one status
    // message (WCAG 4.1.3) that exists from the first drawing and changes only when the conditions change: their
    // titles, without the ages. A live region that is inserted together with its text, or shown at the same moment,
    // is not reliably announced, so a region that has just been created or shown gets its text a moment later.
    const root = this.shadowRoot, fresh = !root.querySelector(".live") || this.style.display === "none";
    if (!root.querySelector(".live")) {
      root.innerHTML = this.css() + `<style>
        .b { margin-bottom: 8px; padding: 12px 16px; border-left: 6px solid var(--c);
             background: color-mix(in srgb, var(--c) 10%, var(--ha-card-background, var(--card-background-color))); }
        .b .s { color: var(--primary-text-color); }   /* secondary text does not reach 4.5:1 on the tint */
        .b:last-child { margin-bottom: 0; }</style><div class="list"></div><div class="sr live" role="status"></div>`;
    }
    this.style.display = out.length ? "" : "none";
    const live = root.querySelector(".live");
    live.setAttribute("lang", gcLang());
    gcRender(root, out.map(([c, t, s]) => `<ha-card lang="${gcLang()}" class="b" style="--c:${c}"><div class="t">${esc(t)}</div>` +
      `<div class="s">${esc(s)}</div></ha-card>`).join(""), root.querySelector(".list"));
    const titles = gcList(out.map(([, t]) => t));
    if (titles !== this._titles) {
      this._titles = titles;
      clearTimeout(this._announce);
      this._announce = setTimeout(() => { this._announce = null; live.textContent = titles; }, fresh ? 500 : 0);
    }
  }
  getCardSize() { return 2; }
}

// "N of M goals met": goals: {key: {op, value}}, val: the 14-day values, enough: at least 70% sensor active in the
// 14 days (gcActiveOk). -> [met, compared, set]; nothing is compared below 70% sensor active (the 14-day / 70% rule)
function gcGoalsMet(goals, val, enough) {
  const ks = Object.keys(goals || {});
  if (!enough) return [0, 0, ks.length];
  let met = 0, n = 0;
  ks.forEach((k) => {
    const { op, value } = goals[k], v = val[k];
    if (!isNum(v) || !isNum(+value)) return;
    n++;
    if (gcMeets(op, v, value)) met++;
  });
  return [met, n, ks.length];
}

// ------------------------------------------------------------------ Custom Ranges: where the custom settings make the
// results differ, so that Custom Ranges makes clear where a custom range changes a result.
// The consensus limits come from the configuration sensor (consensus_ranges, range_*); the
// consensus results from the <period>_consensus_ranges sensors and the "consensus" attribute of Change over time.
// Mean, standard deviation, CV and GMI do not depend on the ranges: no difference note.
const GC_LIMIT_KEYS = ["very_low", "low", "tight_low", "tight_high", "high", "very_high"];
const gcLimitsOf = (src, pre = "") => Object.fromEntries(GC_LIMIT_KEYS.map((k) => {
  const v = Number(src?.[pre + k]);
  return [k, isFinite(v) && v > 0 ? v : AGP[k]];
}));
const gcConsRanges = (ca) => gcLimitsOf(ca?.consensus_ranges, "range_");   // mg/dL
const gcCustRanges = (ca) => gcLimitsOf(ca?.ranges);                       // mg/dL
const gcLimDiff = (ca) => { const c = gcCustRanges(ca), s = gcConsRanges(ca); return GC_LIMIT_KEYS.filter((k) => Math.abs(c[k] - s[k]) > 0.05); };
// the range-dependent results (Change Over Time keys) and the limits each depends on
const GC_DEP = { tir: ["low", "high"], titr: ["tight_low", "tight_high"], below: ["low"], very_low: ["very_low"],
  above: ["high"], very_high: ["very_high"] };
const gcAffected = (ca, m) => { const d = gcLimDiff(ca); return (GC_DEP[m] || []).some((k) => d.includes(k)); };
// the same result on the consensus ranges: the attribute of sensor.glucocraft_<period>_consensus_ranges
const GC_CONS_ATTR = { tir: "time_70_180", titr: "time_70_140", below: "time_below_70", very_low: "time_below_54",
  above: "time_above_180", very_high: "time_above_250" };
// the statistics-table keys of Custom Ranges -> the Change Over Time keys
const GC_ROW_METRIC = { time_in_range: "tir", time_in_tight_range: "titr", below_low: "below", time_very_low: "very_low",
  above_high: "above", time_very_high: "very_high" };
// the band of a result on a range set L (mg/dL), in the display unit: "70–180 mg/dL", "below 70 mg/dL"
const gcConsBandText = (m, L) => {
  const below = (v) => gcT("ui.band.below", { value: `${gs(v)} ${gU()}` });
  const above = (v) => gcT("ui.band.above", { value: `${gs(v)} ${gU()}` });
  const band = { tir: () => `${gr(L.low, L.high)} ${gU()}`, titr: () => `${gr(L.tight_low, L.tight_high)} ${gU()}`,
    below: () => below(L.low), very_low: () => below(L.very_low), above: () => above(L.high),
    very_high: () => above(L.very_high) }[m];
  return band ? band() : "";
};
// "6.2% with the custom ranges · 4.1% with the consensus ranges" (the terms of LANGUAGE_STYLE rule 17)
const gcBoth = (own, cons) => gcT("ui.both", { own, consensus: cons });
// a comparison with a value: "more than 70%", "at most 36%" (an unknown operator is shown as written)
const GC_OP_KEY = { ">": "ui.compare.more_than", ">=": "ui.compare.at_least", "<": "ui.compare.less_than", "<=": "ui.compare.at_most" };
const gcCompare = (op, value) => (GC_OP_KEY[op] ? gcT(GC_OP_KEY[op], { value }) : `${op} ${value}`);
// the consensus target of a goal key as [op, value] (the statistics tables), from glucocraft-shared.js GC_CONS_GOAL,
// the one table of the consensus targets
const gcConsTarget = (k) => GC_CONS_GOAL[k].slice(0, 2);
const gcGoalVal = (k, v) => (k === "gmi" ? gmiTxt(+v) : `${dtx(v)}%`);   // a GMI goal by the GMI rule (gmiTxt)
const gcGoalEqual = (k, g) => { const t = GC_CONS_GOAL[k]; return !!(t && g && g.op === t[0] && Math.abs(+g.value - t[1]) < 1e-9); };
// the goal text of a row: the custom goal, with the consensus target where it differs, or the label of a goal without
// a published target (time in tight range: no consensus target for clinical care). g = {op, value} or undefined
const gcGoalText = (k, g) => {
  const t = GC_CONS_GOAL[k], target = t ? gcCompare(t[0], gcGoalVal(k, t[1])) : "";
  if (!g) return t ? gcT("ui.goal.none_with_target", { target, source: t[2] }) : gcT("ui.goal.none");
  const goal = gcCompare(g.op, gcGoalVal(k, g.value));
  if (!t) return k === "time_in_tight_range" ? gcT("ui.goal.custom_no_clinical_target", { goal }) : gcT("ui.goal.custom_no_published_target", { goal });
  return gcGoalEqual(k, g) ? gcT("ui.goal.custom_equal", { goal, source: t[2] })
    : gcT("ui.goal.custom_with_target", { goal, target, source: t[2] });
};
// The readings whose classification differs at one limit, in the display unit: mg/dL whole numbers, mmol/L on the
// 0.1 grid (the limits as displayed). -> ["Readings of 70–74 mg/dL count as low here and as in range on Consensus
// Ranges.", ...]; the classification is that of statistics.py (below very low, below low, low to high inclusive, up to
// very high inclusive, above very high; tight range inclusive at both ends).
const GC_BAND5 = ["ui.zone_in_sentence.very_low", "ui.zone_in_sentence.low", "ui.zone_in_sentence.in_range",
  "ui.zone_in_sentence.high", "ui.zone_in_sentence.very_high"];
const gcBand5 = (x, L) => (x < L.very_low ? 0 : x < L.low ? 1 : x <= L.high ? 2 : x <= L.very_high ? 3 : 4);
/** Return the sentences that name the readings classified differently at the range limit k. */
function gcLimitSegments(ca, k) {
  const sp = (L) => Object.fromEntries(Object.entries(L).map(([kk, v]) => [kk, isMmol() ? round(gv(v) * 10) : v]));
  const C = sp(gcCustRanges(ca)), N = sp(gcConsRanges(ca)), tight = k === "tight_low" || k === "tight_high";
  const cls = (x, L) => (tight ? (x >= L.tight_low && x <= L.tight_high ? 1 : 0) : gcBand5(x, L));
  const segs = [];
  for (let x = Math.floor(Math.min(C[k], N[k])) - 1; x <= Math.ceil(Math.max(C[k], N[k])) + 1; x++) {
    const a = cls(x, C), b = cls(x, N);
    if (a === b) continue;
    const last = segs[segs.length - 1];
    if (last && last[1] === x - 1 && last[2] === a && last[3] === b) last[1] = x; else segs.push([x, x, a, b]);
  }
  const f = (x) => (isMmol() ? nf(x / 10, 1) : String(x));
  return segs.map(([a, b, h, t]) => {
    const readings = `${a === b ? f(a) : `${f(a)}–${f(b)}`} ${gU()}`;
    if (tight) return h ? gcT("ui.segment.tight_in", { readings }) : gcT("ui.segment.tight_out", { readings });
    return gcT("ui.segment.band", { readings, here: gcT(GC_BAND5[h]), there: gcT(GC_BAND5[t]) });
  });
}

// ------------------------------------------------------------------ readout / headline
// The earlier window of a Change Over Time comparison, chosen once for every measure of a table, tile or line:
// 22–26 weeks ago, or 9–13 weeks ago when 22–26 weeks ago has less than 70% sensor active (GlucoCraft then sends no
// values for it, change.py). compare: the compare attribute of Change over time
const gcRefWindow = (compare) => (compare?.weeks_22_26_ago ? "weeks_22_26_ago" : "weeks_9_13_ago");
// the difference a - b of two percentages as they are shown (one decimal each), in points; NaN without both
const gcPointsShown = (a, b) => (isNum(a) && isNum(b) ? round(round(a, 1) - round(b, 1), 1) : NaN);
// The GMI of a Change Over Time window in mmol/mol: its published gmi_mmol_mol, else from its mean (the GMI rule)
const gcGmiMmol = (w) => (isNum(w?.gmi_mmol_mol) ? w.gmi_mmol_mol : gmiMmolFromMean(w?.mean));
// The headline's change of the time in range: the last 4 weeks against 22–26 weeks ago, or against 9–13 weeks ago when
// 22–26 weeks ago has less than 70% sensor active (the rule of the core measures and of the Change Over Time table);
// "no change" when the two values as shown (one decimal) are the same. compare: the compare attribute of Change over
// time (last_4_weeks, weeks_9_13_ago, weeks_22_26_ago)
const gcTirChange = (compare) => {
  const c = compare || {}, key = gcRefWindow(c), old = key === "weeks_22_26_ago";
  const d = gcPointsShown(c.last_4_weeks?.tir, c[key]?.tir);
  if (!isNum(d)) return gcT("ui.readout.consensus.no_history");
  if (Math.abs(d) < 0.05) return old ? gcT("ui.readout.consensus.no_change_22_26") : gcT("ui.readout.consensus.no_change_9_13");
  const p = { count: Math.abs(d), digits: 1, difference: `${d > 0 ? "+" : ""}${nf(d, 1)}` };
  return old ? gcT("ui.readout.consensus.change_22_26", p) : gcT("ui.readout.consensus.change_9_13", p);
};
/** A headline card or a page row (custom:glucocraft-readout). */
class GlucoCraftReadout extends GCBase {
  watch() { return [ENT.status, ENT.config, ENT.weekly, "sensor.glucocraft_change_over_time", "sensor.glucocraft_24h_consensus_ranges", "sensor.glucocraft_14d_consensus_ranges", "sensor.glucocraft_14d_custom_ranges"]; }
  // every view and the pages card read only the entities they watch: drawn again when one of them changes
  timed() { return false; }
  tirCheck(v) {   // the 14-day time in range against the consensus target or the goal; null = not compared (< 70 % data, no goal)
    let tir, enough, op, value;
    if (v === "consensus") {
      const a = this.st("sensor.glucocraft_14d_consensus_ranges")?.attributes || {};
      if (!a.has_data) return null;
      [tir, enough, op, value] = [a.time_70_180, this.activeOk("14d"), ...gcConsTarget("time_in_range")];
    } else {
      const g = (this.cfg().goals || {}).time_in_range;
      if (!g) return null;
      [tir, enough, op, value] = [this.custom("14d", "time_in_range"), this.activeOk("14d", true), g.op, g.value];
    }
    if (!isNum(tir) || !enough) return null;   // Battelino et al., 2019: >= 70 % data over 14 days
    return gcMeets(op, tir, value);
  }
  /**
   * Return { icon, name, head, lines } of the headline for a view (today, status, consensus, personal, report).
   *
   * name: the page (or GlucoCraft); head: what the card shows of it; lines: the lines of text under the title.
   * compact: the pages card's one-line row, where the note under the rows names the 14 days of the two range-set rows
   */
  content(v = this._cfg.view, compact = false) {
    // a target or goal of the last 14 days: met, not met, or not compared (less than 70% sensor active, no goal)
    const result = (ok, met, notMet, notCompared) => (ok === null ? notCompared : ok ? met : notMet);
    const head14 = (params) => (compact ? gcT("ui.pages.head_14d", params) : gcT("ui.readout.head_14d", params));
    // the 70% rule's note of the period the headline shows (not on the pages card's one-line rows)
    const enough = (p, custom) => {
      const e = compact ? null : gcEnough(p, this.rawSummary(p, custom));
      return e ? [gcEnoughNote(e)] : [];
    };
    if (v === "today") {
      const a = this.st("sensor.glucocraft_24h_consensus_ranges")?.attributes || {};
      return { icon: "mdi:calendar-today", name: gcT("strategy.page.today"),
        head: gcT("ui.readout.head_24h", { percent: pc(a.time_70_180), range: gr(70, 180), unit: gU() }),
        lines: [gcT("ui.readout.today.numbers", { mean: gs(a.mean), unit: gU(), tight_range: gr(70, 140),
          tight: pc(a.time_70_140), low: gs(70), below: pc(a.time_below_70, 1) }), ...enough("24h", false),
        gcT("ui.readout.today.periods")] };
    }
    if (v === "status") {
      // the status and when the statistics were calculated; the source in use is in the page's sources table (it is
      // stated once on the page)
      const lab = this.attr(ENT.status, "label") || "---";
      const at = this.attr(ENT.weekly, "computed_at"), time = at ? clockStr(new Date(at)) : "---";
      return { icon: "mdi:list-status", name: "GlucoCraft", head: lab,
        lines: [gcT("ui.readout.status.calculated", { time })] };
    }
    if (v === "consensus") {
      const a = this.st("sensor.glucocraft_14d_consensus_ranges")?.attributes || {};
      const ok = this.tirCheck("consensus");
      return { icon: "mdi:earth", name: gcT("strategy.page.consensus"),
        head: head14({ percent: pcAt(a.time_70_180, 70), range: gr(70, 180), unit: gU() }),
        lines: [gcTirChange(this.attr(ENT_CHANGE, "consensus")?.compare),
        result(ok, gcT("ui.readout.consensus.target_met"), gcT("ui.readout.consensus.target_not_met"),
          gcT("ui.readout.consensus.target_not_compared")), ...enough("14d", false),
        gcT("ui.readout.consensus.contents")] };
    }
    if (v === "personal") {
      const c = this.cfg(), r = c.ranges || AGP, g = c.goals || {}, v14 = (k) => this.custom("14d", k);
      const val = { time_in_range: v14("time_in_range"), time_in_tight_range: v14("time_in_tight_range"),
        time_low: this.combined("14d", "below"), time_very_low: v14("time_very_low"),
        time_high: this.combined("14d", "above"), time_very_high: v14("time_very_high"), cv: v14("cv"), gmi: v14("gmi") };
      // counted with the same rule as every other ✓ / ✗: the last 14 days, only with at least 70%
      // sensor active (Battelino et al., 2019); below 70% no goal is compared
      const [met, n, total] = gcGoalsMet(g, val, this.activeOk("14d", true));
      const gt0 = g.time_in_range, ok = this.tirCheck("personal");
      // where the in-range limits differ, the time in range with the consensus ranges; where the goal
      // differs, the consensus target
      const a14 = this.st("sensor.glucocraft_14d_consensus_ranges")?.attributes || {};
      const tirBoth = gcAffected(c, "tir") ? gcT("ui.readout.personal.tir_both", {
        both: gcBoth(pc(val.time_in_range), pc(a14.has_data ? a14.time_70_180 : NaN)), band: gcConsBandText("tir", gcConsRanges(c)) }) : null;
      const goals = !total ? gcT("ui.readout.personal.goals_none") : n ? gcT("ui.readout.personal.goals_met", { count: n, met })
        : gcT("ui.readout.personal.goals_not_compared");
      const goal = gt0 ? gcCompare(gt0.op, `${dtx(gt0.value)}%`) : "";
      const tir = !gt0 ? gcT("ui.readout.personal.tir_no_goal") : gcGoalEqual("time_in_range", gt0)
        ? result(ok, gcT("ui.readout.personal.tir_goal.met", { goal }), gcT("ui.readout.personal.tir_goal.not_met", { goal }),
          gcT("ui.readout.personal.tir_goal.not_compared", { goal }))
        : result(ok, gcT("ui.readout.personal.tir_goal_differs.met", { goal }), gcT("ui.readout.personal.tir_goal_differs.not_met", { goal }),
          gcT("ui.readout.personal.tir_goal_differs.not_compared", { goal }));
      return { icon: "mdi:account-cog", name: gcT("strategy.page.custom"),
        head: head14({ percent: gt0 ? pcAt(val.time_in_range, +gt0.value) : pc(val.time_in_range), range: gr(r.low, r.high),
          unit: gU() }),
        lines: [tirBoth, goals, tir, ...enough("14d", true), gcT("ui.readout.personal.contents")]
          .filter((line) => line !== null) };
    }
    if (v === "report") {
      return { icon: "mdi:file-chart-outline", name: gcT("strategy.page.report"), head: gcT("ui.readout.report.head"),
        lines: [gcT("ui.readout.report.periods"), gcT("ui.readout.report.contents")] };
    }
    return { icon: "mdi:information-outline", name: this._cfg.title || "", head: "", lines: String(this._cfg.text || "").split("\n") };
  }
  /** Draw the headline card with its status edge and mark, as a button that opens its page. */
  draw() {
    const { icon, name, head, lines } = this.content();
    const t = head ? gcT("ui.readout.title", { name, head }) : name, s = lines.join("\n");
    const hero = !!this._cfg.hero;
    // Top cards: Maintenance by GlucoCraft's status; Consensus / Custom Ranges by the 14-day time in
    // range (green check = meets, red cross = does not meet); Today neutral (no target comparison under 14 days)
    const v = this._cfg.view;
    let col = null, ok = null;
    if (hero && v === "status") col = this.attr(ENT.status, "color") || STATUS.unknown;
    else if (hero && (v === "consensus" || v === "personal")) {
      ok = this.tirCheck(v);
      if (ok !== null) col = ok ? STATUS.ok : STATUS.alert;
    }
    const mark = ok === null ? "" : ok ? " ✓" : " ✗";
    const style = col ? `border-left: 6px solid ${col}; background: linear-gradient(135deg, color-mix(in srgb, ${col} 32%, var(--ha-card-background, var(--card-background-color))) 0%, var(--ha-card-background, var(--card-background-color)) 75%);`
      : "border-left: 6px solid var(--divider-color);";
    // The control is the title: a link to the card's page (path), or a button that opens Home Assistant's details of
    // its entity. Its name is the title as shown, with the units and the mark in words (never color alone;
    // ACCESSIBILITY.md rule 4); the lines under it, which stay ordinary text, are its description. The whole card
    // remains a pointer target.
    const shown = `<span aria-hidden="true">${esc(t)}${col && mark ? `<span style="color:${stc(col)}">${mark}</span>` : ""}</span>`;
    const said = ok === null ? gcSpeakText(t) : gcT(ok ? "ui.readout.name.meets" : "ui.readout.name.does_not_meet", { shown: gcSpeakText(t) });
    const title = `${shown}<span class="sr">${esc(said)}</span>`;
    // a card that opens Home Assistant's details says so on screen, and its button is described by that line as well
    const path = this._cfg.path;
    const ctl = path ? `<a class="t ctl" href="${esc(path)}" aria-describedby="lines">${title}</a>`
      : `<button type="button" class="t ctl" aria-describedby="lines hint">${title}</button>`;
    const hint = path ? "" : `<div class="s" id="hint">${esc(gcT("ui.readout.show_details_hint"))}</div>`;
    this.html = this.css() + `<style>
        .ctl { display: block; padding: 0; border: 0; background: none; font: inherit; font-size: 16px; font-weight: 600;
               line-height: 1.35; color: var(--primary-text-color); text-align: left; text-decoration: none; cursor: pointer; }
        /* the lines over the status tint of a headline in the primary text color (the secondary one does not reach
           4.5:1 at the tint's start), as the banners and the warning boxes */
        ${col ? ".s { color: var(--primary-text-color); }" : ""}
      </style><ha-card lang="${gcLang()}" class="tap" style="${style}"><div class="row">` +
      `<ha-icon icon="${icon}" style="color:${col || "var(--secondary-text-color)"}" aria-hidden="true"></ha-icon>` +
      `<div>${ctl}<div class="s" id="lines">${gcSay(s)}</div>${hint}</div></div></ha-card>`;
    const card = this.shadowRoot.querySelector("ha-card");
    card.onclick = (e) => {
      if (path && !gcPlainClick(e)) return;   // the browser opens the link itself
      e.preventDefault();
      if (path) this.nav(path);
      else this.more(this._cfg.view === "today" ? "sensor.glucocraft_24h_consensus_ranges" : ENT.status);
    };
  }
}

// ------------------------------------------------------------------ Custom Ranges: differences from the consensus ranges
// The line at the top of the page: the differences are marked, or the settings are equal to the
// consensus ranges; a note at every range limit that differs (both limits and which readings are classified
// differently); every goal that differs from its consensus target, with that target; goals without a published target.
// the note of each range limit that differs: both limits, the readings classified differently, the results it changes
const GC_LIMIT_ITEM = { very_low: "ui.custom_diff.limit.very_low", low: "ui.custom_diff.limit.low",
  tight_low: "ui.custom_diff.limit.tight_low", tight_high: "ui.custom_diff.limit.tight_high",
  high: "ui.custom_diff.limit.high", very_high: "ui.custom_diff.limit.very_high" };
// the goals: their names as a label, and within a sentence (only the goals with a consensus target can be equal to it)
const GC_GOAL_NAME = { time_in_range: "ui.goal_name.time_in_range", time_in_tight_range: "ui.goal_name.time_in_tight_range",
  time_low: "ui.goal_name.time_low", time_very_low: "ui.goal_name.time_very_low", time_high: "ui.goal_name.time_high",
  time_very_high: "ui.goal_name.time_very_high", cv: "ui.goal_name.cv", gmi: "ui.goal_name.gmi" };
const GC_GOAL_NAME_IN_TEXT = { time_in_range: "ui.goal_name_in_text.time_in_range", time_low: "ui.goal_name_in_text.time_low",
  time_very_low: "ui.goal_name_in_text.time_very_low", time_high: "ui.goal_name_in_text.time_high",
  time_very_high: "ui.goal_name_in_text.time_very_high", cv: "ui.goal_name_in_text.cv" };
const GC_GOAL_BAND = { time_in_range: "tir", time_in_tight_range: "titr", time_low: "below", time_very_low: "very_low",
  time_high: "above", time_very_high: "very_high" };
/**
 * Return the HTML of the differences of the custom ranges and goals from the consensus ranges.
 *
 * ca: the configuration sensor's attributes -> HTML
 */
function gcCustomDiffHtml(ca) {
  if (!ca || !ca.ranges) return `<div class="p">${gcT("strategy.waiting")}</div>`;
  const C = gcCustRanges(ca), N = gcConsRanges(ca), lim = gcLimDiff(ca), goals = ca.goals || {};
  const limits = lim.map((k) => {
    const segs = gcLimitSegments(ca, k);
    return `<li>${gcT(GC_LIMIT_ITEM[k], { custom: `${gs(C[k])} ${gU()}`, consensus: `${gs(N[k])} ${gU()}`,
      source: k.startsWith("tight") ? "Battelino et al., 2023" : "Battelino et al., 2019",
      segments: segs.length ? gcSentences(segs) : gcT("ui.custom_diff.limit.within_resolution") })}</li>`;
  });
  const differ = [], noTarget = [], equal = [];
  Object.keys(GC_GOAL_NAME).forEach((k) => {
    const g = goals[k], name = gcT(GC_GOAL_NAME[k]);
    if (!GC_CONS_GOAL[k]) { if (g) noTarget.push(`<li>${gcT("ui.custom_diff.goal", { name, text: esc(gcGoalText(k, g)) })}</li>`); return; }
    const band = GC_GOAL_BAND[k], other = !!band && gcAffected(ca, band);
    if (gcGoalEqual(k, g) && !other) equal.push(gcT(GC_GOAL_NAME_IN_TEXT[k]));
    else differ.push(`<li>${other ? gcT("ui.custom_diff.goal_other_range", { name, text: esc(gcGoalText(k, g)),
      custom: esc(gcConsBandText(band, C)), consensus: esc(gcConsBandText(band, N)) })
      : gcT("ui.custom_diff.goal", { name, text: esc(gcGoalText(k, g)) })}</li>`);
  });
  // Every result is equal to Consensus Ranges only without any difference: a goal without a published target (time in
  // tight range, GMI) also adds a ✓ / ✗ that Consensus Ranges never shows
  const none = !lim.length && !differ.length && !noTarget.length;
  // the line at the top: equal, or which differences are marked (limits and goals, limits only, goals only, goals
  // without a consensus target only)
  const top = none ? gcT("ui.custom_diff.equal")
    : differ.length ? (lim.length ? gcT("ui.custom_diff.marked.limits_and_goals", { count: differ.length, limits: lim.length })
      : gcT("ui.custom_diff.marked.goals", { count: differ.length }))
      : lim.length ? (noTarget.length ? gcT("ui.custom_diff.marked.limits_no_target", { count: noTarget.length, limits: lim.length })
        : gcT("ui.custom_diff.marked.limits", { limits: lim.length }))
        : gcT("ui.custom_diff.marked.no_target", { count: noTarget.length });
  return `<div class="p">${top}</div>` +
    (limits.length ? `<div class="h">${gcT("ui.custom_diff.heading.limits")}</div><ul>${limits.join("")}</ul>` : "") +
    (differ.length ? `<div class="h">${gcT("ui.custom_diff.heading.goals")}</div><ul>${differ.join("")}</ul>` : "") +
    (noTarget.length ? `<div class="h">${gcT("ui.custom_diff.heading.no_target")}</div><ul>${noTarget.join("")}</ul>` : "") +
    (equal.length && !none ? `<div class="p">${gcT("ui.custom_diff.goals_equal", { list: gcList(equal) })}</div>` : "") +
    `<div class="p">${gcT("ui.custom_diff.independent")}</div>`;
}
/** The Differences from the Consensus Ranges card (custom:glucocraft-custom-diff). */
class GlucoCraftCustomDiff extends GCBase {
  watch() { return [ENT.config, ENT.ranges]; }
  draw() {
    this.html = this.css() + `<style>
        .p { font-size: 14px; line-height: 1.45; margin-top: 6px; color: var(--primary-text-color); }
        .h { font-size: 14px; font-weight: 600; margin-top: 10px; color: var(--secondary-text-color); }
        ul { margin: 4px 0 0; padding-left: 20px; } li { font-size: 14px; line-height: 1.45; margin: 4px 0; }
      </style><ha-card lang="${gcLang()}"><div class="t">${gcT("ui.custom_diff.title")}</div>${gcCustomDiffHtml(this.cfg())}</ha-card>`;
  }
  getCardSize() { return 3; }
}

// ------------------------------------------------------------------ Change Settings button (Settings Status)
// A real button (a full-width button card looks like a header). It opens GlucoCraft's
// page in Home Assistant's settings, where the gear icon (Configure) on the GlucoCraft row opens the settings.
// Administrators only: other users see why the button is not offered.
/** The Change Settings button of Settings Status (custom:glucocraft-settings-button). */
class GlucoCraftSettingsButton extends GCBase {
  watch() { return [ENT.config]; }
  /** Draw the button for administrators, or the note that only administrators can change settings. */
  draw() {
    if (!this._hass) return;
    const admin = !!this._hass.user?.is_admin;
    this.html = this.css() + `<style>
        .wrap { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
        ${GC_BUTTON_CSS}
        .x { flex: 1; min-width: 220px; font-size: 14px; color: var(--secondary-text-color); line-height: 1.45; }
        .path { margin-top: 6px; color: var(--primary-text-color); }
      </style><ha-card lang="${gcLang()}"><div class="wrap">` +
      (admin
        ? `<button type="button" aria-label="${esc(gcT("ui.settings.button_label"))}">` +
          `<ha-icon icon="mdi:cog-outline"></ha-icon>${gcT("ui.settings.button")}</button>` +
          `<div class="x">${gcT("ui.settings.admin_note")}<div class="path">${gcT("ui.settings.location")}</div></div>`
        : `<div class="x">${gcT("ui.settings.not_admin")}<div class="path">${gcT("ui.settings.location")}</div></div>`) +
      `</div></ha-card>`;
    const b = this.shadowRoot.querySelector("button");
    if (b) b.onclick = () => this.nav("/config/integrations/integration/glucocraft");
  }
  getCardSize() { return 2; }
}

// ------------------------------------------------------------------ the Maintenance status (maintenance.py)
// One source: the Configuration sensor's attribute maintenance_issues, one entry per issue ({kind: "panel_offline",
// name}, {kind: "repairs", title}, {kind: "csv_export", reason}); null while the sensor has no attributes (GlucoCraft
// reloading). The Start page's Maintenance button counts the entries; the Maintenance page's hero card lists them.
const gcMaintenanceIssues = (ca) => (Array.isArray(ca?.maintenance_issues) ? ca.maintenance_issues : null);
// the status line ("No issues" / "1 issue" / "2 issues") and its spoken form ("Maintenance: no issues")
const gcMaintenanceLine = (n) => (n ? gcT("ui.maintenance.line.issues", { count: n }) : gcT("ui.maintenance.line.none"));
const gcMaintenanceSaid = (n) => (n ? gcT("ui.maintenance.said.issues", { count: n }) : gcT("ui.maintenance.said.none"));
// The Repairs page of Home Assistant (Settings > System > Repairs)
const REPAIRS_PATH = "/config/repairs";

/**
 * The hero card of the Maintenance page (custom:glucocraft-maintenance-issues): shown only while there are issues.
 *
 * In red with an alert icon (never color alone: the title, the count and the list say it): the title Maintenance
 * issues, the count, and each issue with where it is resolved: a wall panel offline for 10 minutes (its power and
 * Wi-Fi; the GlucoCraft Panels table below), a GlucoCraft Repairs issue (Home Assistant's Repairs, linked), a failed CSV
 * export (CSV Export below, with the reason). No current data and the sensor expiry are not issues of this status: the
 * banners and the status headline show them.
 */
class GlucoCraftMaintenanceIssues extends GCBase {
  watch() { return [ENT.config, ENT.last]; }
  /**
   * Draw the list of issues, or nothing while there are none.
   *
   * While the newest reading's time is ahead of Home Assistant's clock (time_untrusted), the card also explains it,
   * under the count: what happened, what it means and what to check (shown while it lasts).
   */
  draw() {
    const issues = gcMaintenanceIssues(this.cfg()) || [];
    const clock = this.attr(ENT.last, "time_untrusted") === true;
    if (!issues.length && !clock) { this.style.display = "none"; this.html = ""; return; }
    this.style.display = "";
    const col = STATUS.alert;
    const item = (i) => {
      if (i.kind === "panel_offline") return gcT("ui.maintenance.item.panel_offline", { name: esc(i.name ?? "") });
      if (i.kind === "repairs") return gcT("ui.maintenance.item.repairs", { title: esc(i.title ?? ""), url: REPAIRS_PATH });
      if (i.kind === "csv_export") return gcT("ui.maintenance.item.csv_export", { reason: esc(i.reason ?? "") });
      return esc(String(i.kind ?? ""));
    };
    this.html = this.css() + `<style>
        ha-card { --gc-mi-bg: var(--ha-card-background, var(--card-background-color)); border-left: 6px solid ${col};
                  background: linear-gradient(135deg, color-mix(in srgb, ${col} 32%, var(--gc-mi-bg)) 0%, var(--gc-mi-bg) 75%); }
        .row { display: flex; gap: 12px; align-items: flex-start; }
        ul { margin: 6px 0 0; padding-left: 20px; } li { font-size: 14px; line-height: 1.45; margin: 4px 0; }
        a { color: var(--primary-text-color); }
        /* on the status tint the lines are in the primary text color: the secondary one does not reach 4.5:1 there */
        .s { color: var(--primary-text-color); }
        .p { font-size: 14px; line-height: 1.45; margin-top: 6px; color: var(--primary-text-color); }
      </style><ha-card lang="${gcLang()}" role="region" aria-label="${esc(gcT("ui.maintenance.title"))}"><div class="row">` +
      `<ha-icon icon="mdi:alert-circle-outline" style="color:${col}" aria-hidden="true"></ha-icon><div>` +
      `<div class="t">${esc(gcT("ui.maintenance.title"))}</div><div class="s">${esc(gcMaintenanceLine(issues.length))}</div>` +
      (clock ? `<div class="p">${gcT("ui.maintenance.clock_ahead")}</div>` : "") +
      (issues.length ? `<ul>${issues.map((i) => `<li>${item(i)}</li>`).join("")}</ul>` : "") + `</div></div></ha-card>`;
    // a plain click opens Home Assistant's Repairs in the same window; with a modifier key the browser opens the link
    this.shadowRoot.querySelectorAll(`a[href="${REPAIRS_PATH}"]`).forEach((el) => {
      el.onclick = (e) => {
        if (!gcPlainClick(e)) return;
        e.preventDefault();
        this.nav(REPAIRS_PATH);
      };
    });
  }
  getCardSize() { return 2; }
}

// ------------------------------------------------------------------ CSV export (Maintenance)
/**
 * The CSV export card of Maintenance (custom:glucocraft-export).
 *
 * One button: the action glucocraft.export_csv saves every stored reading to /config/glucocraft_exports/ (the last 12
 * exports are kept). The status line shows the result GlucoCraft returns, or the error.
 */
class GlucoCraftExport extends GCBase {
  watch() { return [ENT.config]; }
  /** Run the action glucocraft.export_csv and show its result. */
  async run() {
    if (this._busy || !this._hass) return;
    this._busy = true; this._status = gcT("ui.export.saving"); this.draw();
    try {
      // the action may return a response (it is registered with SupportsResponse.OPTIONAL), so asking for one is always
      // accepted; any error goes to the status line below
      const res = await this._hass.callWS({ type: "call_service", domain: "glucocraft", service: "export_csv", service_data: {}, return_response: true });
      // the action returns {folder, files, readings} (backend contract)
      const r = res?.response || {}, files = (Array.isArray(r.files) ? r.files : r.files ? [r.files] : [])
        .map((f) => String(f).split(/[\\/]/).pop());
      const n = isNum(+r.readings) ? +r.readings : NaN, folder = r.folder ? String(r.folder) : "/config/glucocraft_exports";
      // the file names as a list of the language in use ("a.csv and b.csv")
      const list = new Intl.ListFormat(gcLang(), { type: "conjunction" }).format(files);
      this._status = !files.length ? gcT("ui.export.saved_in_folder") : isNum(n)
        ? gcT("ui.export.saved_readings", { files: list, folder, count: n }) : gcT("ui.export.saved", { files: list, folder });
      this._ok = true;
    } catch (e) {
      this._status = gcT("ui.export.not_saved", { error: e?.message || e?.code || e });
      this._ok = false;
    }
    this._busy = false; this.draw();
  }
  /**
   * Draw the button, its description and the status line.
   *
   * The status line is a status message (WCAG 4.1.3) that exists, empty, from the first drawing, so that screen
   * readers announce each new result; while the export runs, the button stays focusable and ignores a further press.
   */
  draw() {
    if (!this._hass) return;
    const days = this.cfg().retention_days || RETENTION_DAYS, root = this.shadowRoot;
    if (!root.querySelector(".st")) {
      root.innerHTML = this.css() + `<style>
        .wrap { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
        ${GC_BUTTON_CSS}
        button[aria-disabled="true"] { opacity: .5; cursor: default; }
        .x { flex: 1; min-width: 220px; font-size: 14px; color: var(--secondary-text-color); line-height: 1.45; }
        .st { margin-top: 10px; font-size: 14px; padding: 6px 10px; border-radius: 6px; background: var(--secondary-background-color);
              border-left: 6px solid var(--c, var(--divider-color)); }
        .st:empty { margin: 0; padding: 0; border: 0; }
      </style><ha-card lang="${gcLang()}"><div class="wrap"></div><div class="st" role="status"></div></ha-card>`;
    }
    root.querySelector("ha-card").setAttribute("lang", gcLang());
    gcRender(root, `<button type="button"${this._busy ? ' aria-disabled="true"' : ""}><ha-icon icon="mdi:file-delimited-outline"></ha-icon>` +
      `${gcT("ui.export.button")}</button><div class="x">${gcT("ui.export.description", { days: esc(days) })}</div>`,
    root.querySelector(".wrap"));
    const st = root.querySelector(".st");
    st.setAttribute("style", `--c:${this._busy ? STATUS.attention : this._ok ? STATUS.ok : STATUS.alert}`);
    if (st.textContent !== (this._status || "")) st.textContent = this._status || "";
    root.querySelector("button").onclick = () => this.run();
  }
  getCardSize() { return 2; }
}

// ------------------------------------------------------------------ pages (navigation; the same size as the display card)
// One card with a row per report page (its headline from GlucoCraftReadout) and three buttons below. The frame matches
// glucocraft-display-card exactly: max-width 480 px, aspect ratio 480:536, the same background options; text scales with
// the card width (container units), so the two cards look alike side by side or stacked.
const PAGES_W = 480, PAGES_H = 536;
const u = (n) => `${(n / PAGES_W * 100).toFixed(3)}cqw`;   // design units (480 wide) -> container width
// the rows by view (each row's name and headline from GlucoCraftReadout); the page "Custom Ranges" has the view key
// "personal" (code name) and the page path "custom"
const PAGE_ROWS = ["today", "personal", "consensus"];
const PAGE_PATH = (view) => (view === "personal" ? "custom" : view);
const PAGE_BUTTONS = [["maintenance", "strategy.page.maintenance", "mdi:wrench"], ["settings", "strategy.page.settings", "mdi:cog"],
  ["documentation", "strategy.page.documentation", "mdi:book-open-variant"]];
// the source of the marks on the rows (little room: one footnote line in the card) is the text ui.pages.note
const PAGE_PALETTES = {
  theme: "--gp-bg: var(--ha-card-background, var(--card-background-color, #1c1c1c)); --gp-row: var(--secondary-background-color, rgba(127,127,127,0.08)); --gp-fg: var(--primary-text-color, #fff); --gp-fg2: var(--secondary-text-color, #888); --gp-edge: var(--divider-color, #555); --gp-fg2s: color-mix(in srgb, var(--secondary-text-color, #888) 45%, var(--primary-text-color, #fff));",
  black: "--gp-bg: #000; --gp-row: #141414; --gp-fg: #fff; --gp-fg2: #999; --gp-edge: #444;",
  white: "--gp-bg: #fff; --gp-row: #f2f2f2; --gp-fg: #111; --gp-fg2: #666; --gp-edge: #ccc;",
};
/** The pages card (custom:glucocraft-pages): a row per report page and three buttons. */
class GlucoCraftPages extends GlucoCraftReadout {
  // Custom Ranges row: live from the configuration sensor (the optional feature Custom ranges and goals on, and custom
  // ranges or custom goals set); while the sensor has no data (GlucoCraft reloading) the last answer is kept, so the
  // row does not disappear
  customInUse() {
    const c = this.cfg();
    if (c.ranges) {
      this._custom = c.custom_ranges_and_goals !== false
        && (Object.keys(AGP).some((k) => c.ranges[k] !== AGP[k]) || !!c.goals_custom);
    }
    return !!this._custom;
  }
  // the number of Maintenance issues (gcMaintenanceIssues); while the sensor has no data (GlucoCraft reloading) the
  // last number is kept; null before GlucoCraft has published one
  maintenanceCount() {
    const issues = gcMaintenanceIssues(this.cfg());
    if (issues) this._issues = issues.length;
    return this._issues ?? null;
  }
  // the card options with their defaults (the dashboard's path, the theme background, no report row)
  setConfig(config) {
    const c = { base: "/glucocraft-home", background: "theme", report: false, ...(config || {}) };
    // an unknown background (theme, black or white) falls back to the theme: setConfig runs before a language file
    // can be loaded, so an error shown on the card could not be in the user's language
    if (!PAGE_PALETTES[c.background]) {
      console.warn(`GlucoCraft: pages card background "${c.background}" is not theme, black or white; theme is used`);
      c.background = "theme";
    }
    super.setConfig(c);
  }
  getCardSize() { return 9; }
  getGridOptions() { return { columns: "full", min_columns: 6 }; }
  /** Draw the page rows with their headlines and marks, and the buttons. */
  draw() {
    const base = String(this._cfg.base || "/glucocraft-home").replace(/\/$/, "");
    const rows = PAGE_ROWS.filter((v) => v !== "personal" || this.customInUse())   // only with custom ranges / goals
      .concat(this._cfg.report ? ["report"] : []);
    const gap = 8, pad = 14, btnH = 92;
    const rowHtml = rows.map((view) => {
      const { icon, name, head, lines } = this.content(view, true), detail = lines[0];
      // Consensus / Custom Ranges rows: green check / red cross by the 14-day time in range, as their top cards;
      // Today and the Report neutral
      const ok = view === "consensus" || view === "personal" ? this.tirCheck(view) : null;
      const col = ok === null ? "" : ok ? STATUS.ok : STATUS.alert;
      // The link is named for where it leads ("Open Today"); its two lines are its description, each with the units
      // and the mark in words (ACCESSIBILITY.md rule 4), so that a screen reader can read them word by word.
      const mark = col ? `<span style="color:${stc(col)}"> <span aria-hidden="true">${ok ? "✓" : "✗"}</span>` +
        `<span class="sr">${ok ? gcT("ui.result.meets") : gcT("ui.result.does_not_meet")}</span></span>` : "";
      const p = PAGE_PATH(view);
      // a real link (its address can be opened in a new tab), which the click handler opens in the dashboard
      return `<a class="row" href="${esc(base)}/${p}" data-p="${p}"${col ? ` style="border-left-color:${col}"` : ""} ` +
        `aria-label="${esc(gcT("ui.open", { name }))}" aria-describedby="h-${p} d-${p}">` +
        `<ha-icon icon="${icon}"${col ? ` style="color:${col}"` : ""}></ha-icon><div class="txt"><div class="n">${esc(name)}</div>` +
        `<div class="h" id="h-${p}">${gcSay(head)}${mark}</div><div class="d" id="d-${p}">${gcSay(detail)}</div></div><div class="go">›</div></a>`;
    }).join("");
    // The Maintenance button carries the Maintenance status: green with the wrench and "No issues", or red with an
    // alert icon and "1 issue" / "2 issues" (never color alone: the icon and the line say it), named "Maintenance: no
    // issues" / "Maintenance: 2 issues"; neutral, as the other buttons, before GlucoCraft has published a status
    const issues = this.maintenanceCount();
    const btnHtml = PAGE_BUTTONS.map(([p, key, icon]) => {
      const name = gcT(key), status = p === "maintenance" && issues !== null;
      if (!status) {
        return `<a class="btn" href="${esc(base)}/${p}" data-p="${p}" aria-label="${esc(gcT("ui.open", { name }))}">` +
          `<ha-icon icon="${icon}"></ha-icon><div>${esc(name)}</div></a>`;
      }
      // the icon and the edge in the status color mixed with the card's text color (statusEdge): 3:1 on the button
      const col = statusEdge(issues ? STATUS.alert : STATUS.ok);
      return `<a class="btn st" href="${esc(base)}/${p}" data-p="${p}" style="border-left-color:${col}" ` +
        `aria-label="${esc(gcMaintenanceSaid(issues))}"><ha-icon icon="${issues ? "mdi:alert-circle-outline" : icon}" ` +
        `style="color:${col}"></ha-icon><div>${esc(name)}</div><div class="bl">${esc(gcMaintenanceLine(issues))}</div></a>`;
    }).join("");
    this.html = `<style>
        :host { display: block; ${PAGE_PALETTES[this._cfg.background]} }
        ha-card { background: var(--gp-bg); overflow: hidden; border-radius: var(--ha-card-border-radius, 12px);
                  width: 100%; max-width: ${PAGES_W}px; margin: 0 auto; container-type: inline-size; }
        /* The design size is a minimum: the box and its rows grow with their content (larger text, wider text
           spacing), so that nothing is cut off. Below the design width the lines wrap instead of being shortened. */
        .box { aspect-ratio: ${PAGES_W} / ${PAGES_H}; box-sizing: border-box; padding: ${u(pad)}; display: flex;
               flex-direction: column; gap: ${u(gap)}; font-family: Roboto, 'Noto Sans', 'Segoe UI', sans-serif; }
        .row { flex: 1 0 auto; display: flex; align-items: center; gap: ${u(12)}; box-sizing: border-box; padding: 0 ${u(12)} 0 ${u(14)};
               background: var(--gp-row); border-left: ${u(6)} solid var(--gp-edge); border-radius: ${u(8)}; cursor: pointer;
               text-decoration: none; }
        .row ha-icon { --mdc-icon-size: ${u(30)}; color: var(--gp-fg2); flex: none; }
        .txt { flex: 1; min-width: 0; }
        .n { font-size: max(14px, ${u(20)}); font-weight: 600; color: var(--gp-fg); line-height: 1.2; }
        /* the lines wrap at every width, never cut with an ellipsis (a line too long for its row would otherwise be
           hidden with no way to read it); at the design width the shipped lines fit on one line each */
        .h { font-size: max(14px, ${u(16)}); color: var(--gp-fg); line-height: 1.3; overflow-wrap: break-word; }
        .d { font-size: max(14px, ${u(14)}); color: var(--gp-fg2s, var(--gp-fg2)); line-height: 1.3;
             overflow-wrap: break-word; }
        @container (max-width: ${PAGES_W - 1}px) {
          .row { padding-top: 6px; padding-bottom: 6px; }
        }
        .go { font-size: ${u(30)}; color: var(--gp-fg2); flex: none; }
        .fn { flex: none; font-size: max(14px, ${u(12)}); color: var(--gp-fg2); line-height: 1.3; }
        .btns { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: ${u(gap)}; height: ${u(btnH)}; flex: none; }
        .btn { background: var(--gp-row); border-radius: ${u(8)}; display: flex; flex-direction: column; align-items: center;
               justify-content: center; gap: ${u(6)}; cursor: pointer; font-size: max(14px, ${u(15)}); color: var(--gp-fg); text-align: center;
               text-decoration: none; }
        .btn ha-icon { --mdc-icon-size: ${u(28)}; color: var(--gp-fg2); }
        .btn.st { border-left: ${u(6)} solid; gap: ${u(2)}; }
        .bl { font-size: max(14px, ${u(13)}); color: var(--gp-fg2s, var(--gp-fg2)); line-height: 1.2; }
        .row:hover, .btn:hover { filter: brightness(1.15); }
        .row:focus-visible, .btn:focus-visible { outline: 3px solid var(--gp-fg); outline-offset: 2px; }   /* the card's text color */
        ${GC_SR_CSS}
      </style><ha-card lang="${gcLang()}"><div class="box">${rowHtml}<div class="fn">${gcSay(gcT("ui.pages.note"))}</div><div class="btns">${btnHtml}</div></div></ha-card>`;
    // a plain click (or Enter) opens the page in the dashboard; with a modifier key the browser opens the link itself
    this.shadowRoot.querySelectorAll("a[data-p]").forEach((el) => {
      el.onclick = (e) => {
        if (!gcPlainClick(e)) return;
        e.preventDefault();
        this.nav(`${base}/${el.dataset.p}`);
      };
    });
  }
}

// ------------------------------------------------------------------ page footer
// A small subset of Markdown for the footer texts the dashboard strategy passes: [text](link), **bold**, *italic*.
// Internal links (beginning with "/") stay in the dashboard; others open in a new tab, and say so to screen readers.
const gcMiniMd = (md) => esc(md)
  .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, t, u) => (u.startsWith("/")
    ? `<a href="${u}" data-nav="${u}">${t}</a>`
    : `<a href="${u}" target="_blank" rel="noopener">${t}<span class="sr"> ${esc(gcT("ui.link.new_tab"))}</span></a>`))
  .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/\*([^*]+)\*/g, "<i>$1</i>");
/**
 * The page footer (custom:glucocraft-footer).
 *
 * The page footer: the color key of the page (key), the disclaimer line with its link to the full text (disclaimer),
 * the standard statement (attribution) and the license line (license). Each part is a Markdown string from the
 * strategy; parts left out are not shown. Every part is shown and read.
 */
class GlucoCraftFooter extends GCBase {
  watch() { return [ENT.config]; }
  // the footer's parts as paragraphs; a link inside the dashboard opens its page without reloading
  draw() {
    const c = this._cfg, part = (md) => (md ? `<p>${gcMiniMd(md)}</p>` : "");
    this.html = this.css() + `<style>
        ha-card { font-size: 14px; line-height: 1.5; color: var(--primary-text-color); }
        p { margin: 0 0 10px; } p:last-child { margin-bottom: 0; }
        a { color: ${GC_CSS.link}; }
      </style><ha-card lang="${gcLang()}" role="contentinfo">${part(c.key)}${part(c.disclaimer)}${part(c.attribution)}${part(c.license)}</ha-card>`;
    this.shadowRoot.querySelectorAll("a[data-nav]").forEach((a) => {
      a.onclick = (e) => {
        if (!gcPlainClick(e)) return;   // the browser opens the link itself
        e.preventDefault();
        this.nav(a.dataset.nav);
      };
    });
  }
  getCardSize() { return 2; }
}

// ------------------------------------------------------------------ start pair (the display card and the pages card as one pair)
/**
 * The display card and the pages card as one centered pair (custom:glucocraft-start-pair).
 *
 * Two cards at their full size, side by side and centered when both fit, otherwise stacked, with the
 * dashboard's own gap between them (two separate sections would each center their card and leave a wide gap).
 */
class GlucoCraftStartPair extends HTMLElement {
  /** Build the pair and mount the display card chosen in the settings and the pages card. */
  setConfig(config) {
    this._cfg = config || {};
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `<style>
        :host { display: block; }
        .pair { display: flex; flex-wrap: wrap; justify-content: center; gap: var(--ha-section-grid-row-gap, 8px)
                var(--ha-section-grid-column-gap, 8px); }
        /* each card at its full size, never shrunk to sit beside the other: the display card at its
           design width (square 480, rectangular 800 px: the panel's own layout), the pages card at 480 px, all
           536 px tall. Side by side when both fit, otherwise stacked, the display card first. Only a screen
           narrower than a card makes it narrower (max-width), and only then does the display card draw its
           compact layout. */
        .pair > * { flex: 0 0 480px; max-width: 100%; min-width: 0; }
        .pair > .wide { flex: 0 0 800px; }
      </style><div class="pair"></div>`;
    const pair = this._pair = this.shadowRoot.querySelector(".pair");
    this._kids = {};
    this._dispSlot = document.createElement("div");
    const pagesSlot = document.createElement("div");
    pair.append(this._dispSlot, pagesSlot);
    // the display card chosen in GlucoCraft's settings: square or rectangular; the card
    // option (set when the dashboard is built) until the configuration sensor names it
    this._tag = null;
    this._showDisplay(this._tagFor(this._cfg.card) || (this._cfg.wide ? "glucocraft-display-card-wide" : "glucocraft-display-card"));
    this._mount(pagesSlot, "glucocraft-pages", this._cfg.pages || {}, "pages");
  }
  _tagFor(kind) {
    return { square: "glucocraft-display-card", rectangular: "glucocraft-display-card-wide" }[kind];
  }
  // put a new card element of type tag (with its options c) in slot once the type is defined; key names it in _kids
  _mount(slot, tag, c, key) {
    customElements.whenDefined(tag).then(() => {
      if (key === "display" && tag !== this._tag) return;   // the setting changed again before the card was defined
      const el = document.createElement(tag);
      el.setConfig(c);
      if (this._hass) el.hass = this._hass;
      slot.replaceChildren(el);
      this._kids[key] = el;
    });
  }
  // show the display card of type tag (square or rectangular) in its slot, in place of the one shown
  _showDisplay(tag) {
    if (tag === this._tag) return;
    this._tag = tag;
    delete this._kids.display;
    this._dispSlot.replaceChildren();
    this._dispSlot.className = tag === "glucocraft-display-card-wide" ? "wide" : "";
    this._mount(this._dispSlot, tag, this._cfg.display || {}, "display");
  }
  // the new state: the pair's language, the display card of the settings, and the state passed to both cards
  set hass(hass) {
    this._hass = hass;
    // the pair's language, once the language catalogues are loaded (the cards in it draw their own texts)
    if (gcI18nReady(hass)) this._pair.setAttribute("lang", gcLang());
    else this._i18n ||= gcI18nLoad(hass).then(() => { this._i18n = null; this._pair.setAttribute("lang", gcLang()); });
    // the Display card setting, live (no page reload); kept while the sensor has no attributes
    const live = this._tagFor(hass.states[ENT.config]?.attributes?.display_card);
    if (live) this._showDisplay(live);
    Object.values(this._kids || {}).forEach((k) => { k.hass = hass; });
  }
  getCardSize() { return 9; }
  getGridOptions() { return { columns: "full" }; }
}

// ------------------------------------------------------------------ statistics table
// rows: [key, label, unit, target [op, value] or null, citation, decimals]; the labels in the display unit
const consensusRows = () => [
  ["time_70_180", gcT("ui.measure.time_in_range", { range: gr(70, 180), unit: gU() }), "%", gcConsTarget("time_in_range"), gcT("ui.note.target.time_70_180"), 0],
  ["time_70_140", gcT("ui.measure.time_in_tight_range", { range: gr(70, 140), unit: gU() }), "%", null, gcT("ui.note.tight_range"), 0],
  ["time_below_70", gcT("ui.measure.below", { value: gs(70), unit: gU() }), "%", gcConsTarget("time_low"), gcT("ui.note.target.time_below_70"), 1],
  ["time_below_54", gcT("ui.measure.below", { value: gs(54), unit: gU() }), "%", gcConsTarget("time_very_low"), gcT("ui.note.target.time_below_54"), 1],
  ["time_above_180", gcT("ui.measure.above", { value: gs(180), unit: gU() }), "%", gcConsTarget("time_high"), gcT("ui.note.target.time_above_180"), 1],
  ["time_above_250", gcT("ui.measure.above", { value: gs(250), unit: gU() }), "%", gcConsTarget("time_very_high"), gcT("ui.note.target.time_above_250"), 1],
  ["mean", gcT("ui.measure.mean_unit", { unit: gU() }), "G", null, gcT("ui.note.no_consensus_target"), 0],
  ["std_dev", gcT("ui.measure.standard_deviation_unit", { unit: gU() }), "G", null, gcT("ui.note.no_consensus_target"), 0],
  ["cv", gcT("ui.measure.cv"), "%", gcConsTarget("cv"), gcT("ui.note.target.cv"), 0],
  ["gmi", gcT("ui.measure.gmi"), "A1C", null, gcT("ui.note.gmi"), 1],
  // the GRI with its hypoglycemia and hyperglycemia components under each value (Klonoff et al., 2023)
  ["gri", gcT("ui.measure.gri"), "", null, gcSentences([gcT("ui.note.gri_zones"), gcT("ui.note.gri_components")]), 0],
  ["sensor_active", gcT("ui.label.sensor_active"), "%", [">=", 70], gcT("ui.note.target.sensor_active"), 0],
];
// the GRI components of a period under its value, "2.1 · 10.3", named in words for screen readers; "" without them
const gcGriParts = (hypo, hyper) => (isNum(hypo) && isNum(hyper)
  ? gcSay(gcT("ui.statistics.gri_components", { hypo: nf(hypo, 1), hyper: nf(hyper, 1) }),
    gcT("ui.statistics.gri_components_said", { hypo: nf(hypo, 1), hyper: nf(hyper, 1) })) : "");
/** The statistics table for a set of periods (custom:glucocraft-stats-table). */
class GlucoCraftStatsTable extends GCBase {
  // the periods shown (option periods; default the first four)
  periods() { return this._cfg.periods || ["6h", "24h", "7d", "14d"]; }
  // the sensors of the periods shown (a mistyped period has none: optionProblem names it)
  watch() {
    const ps = Array.isArray(this.periods()) ? this.periods().filter((p) => SP.includes(p)) : [];
    return [ENT.config, ...ps.map((p) => gcSummaryId(p, false)), ...ps.map((p) => gcSummaryId(p, true))];
  }
  // periods and warn_periods: lists of GlucoCraft's seven periods (SP)
  optionProblem() {
    for (const [option, list] of [["periods", this.periods()], ["warn_periods", this._cfg.warn_periods]]) {
      if (list === undefined) continue;
      const bad = Array.isArray(list) ? list.filter((p) => !SP.includes(p)) : [list];
      if (bad.length) return [option, bad.map((p) => (typeof p === "string" ? p : JSON.stringify(p))).join(", "), SP.join(", ")];
    }
    return null;
  }
  /** Return the table rows: the consensus rows, or the rows of the custom ranges and goals. */
  rows() {
    if (this._cfg.basis !== "yours") return consensusRows();
    const r = this.cfg().ranges || AGP, g = this.cfg().goals || {};
    // "Custom goal" or "No goal set" (no default for time in tight range and GMI);
    // a goal that differs from the consensus target is shown with that target, a goal without one is labeled so
    const goal = (k) => (g[k] ? [g[k].op, g[k].value] : null), gtxt = (k) => gcGoalText(k, g[k]);
    return [
      ["time_in_range", gcT("ui.measure.in_range", { range: gr(r.low, r.high), unit: gU() }), "%", goal("time_in_range"), gtxt("time_in_range"), 0],
      ["time_in_tight_range", gcT("ui.measure.in_tight_range", { range: gr(r.tight_low, r.tight_high), unit: gU() }), "%",
        goal("time_in_tight_range"), gtxt("time_in_tight_range"), 0],
      ["below_low", gcT("ui.measure.below_including", { low: gs(r.low), very_low: gs(r.very_low), unit: gU() }), "%", goal("time_low"),
        gtxt("time_low"), 1],
      ["time_very_low", gcT("ui.measure.below", { value: gs(r.very_low), unit: gU() }), "%", goal("time_very_low"), gtxt("time_very_low"), 1],
      ["above_high", gcT("ui.measure.above_including", { high: gs(r.high), very_high: gs(r.very_high), unit: gU() }), "%", goal("time_high"),
        gtxt("time_high"), 1],
      ["time_very_high", gcT("ui.measure.above", { value: gs(r.very_high), unit: gU() }), "%", goal("time_very_high"), gtxt("time_very_high"), 1],
      ["mean", gcT("ui.measure.mean_unit", { unit: gU() }), "G", null, "", 0],
      ["std_dev", gcT("ui.measure.standard_deviation_unit", { unit: gU() }), "G", null, "", 0],
      ["cv", gcT("ui.measure.cv"), "%", goal("cv"), gtxt("cv"), 0],
      ["gmi", gcT("ui.measure.gmi"), "A1C", goal("gmi"), gcT("ui.note.gmi_goal", { goal: gtxt("gmi") }), 1],
      ["sensor_active", gcT("ui.label.sensor_active"), "%", null, "", 0],
    ];
  }
  // one statistic of period p by its table key: on the consensus ranges, or on the ranges in the settings
  value(p, key) {
    if (this._cfg.basis !== "yours") {
      const a = this.st(`sensor.glucocraft_${p}_consensus_ranges`)?.attributes;
      return a && a.has_data ? a[key] : NaN;
    }
    // the low / high goals cover all time below low / above high, as the consensus targets do (Battelino et al., 2019):
    // below_low / above_high are the combined bands
    return this.custom(p, key);
  }
  /** Draw the table with targets or goals, the data-quality warnings and the notes. */
  draw() {
    if (this.optionError()) return;
    const ps = this.periods();
    const yours = this._cfg.basis === "yours";
    const enough = Object.fromEntries(ps.map((p) => [p, this.activeOk(p, yours)]));   // >= 70%, before rounding
    // a real table for screen readers: the column headers name the periods, each row header the measure
    let h = `<tr><th scope="col"><span class="sr">${gcT("ui.column.measure")}</span></th>${ps.map((p) => `<th scope="col">${gcSay(periodName(p))}</th>`).join("")}</tr>`;
    const ca = this.cfg();
    for (const [key, label, unit, target, note, dec] of this.rows()) {
      // Custom Ranges: a row whose range differs from the consensus ranges says so, and every value
      // carries the result with the consensus ranges in smaller text
      const m = yours ? GC_ROW_METRIC[key] : null, aff = !!m && gcAffected(ca, m);
      const diff = aff ? `<div class="n dn">${gcSay(gcT("ui.range_differs", { band: gcConsBandText(m, gcConsRanges(ca)) }))}</div>` : "";
      h += `<tr><th scope="row" class="l">${gcSay(label)}${note ? `<div class="n">${gcSay(note)}</div>` : ""}${diff}</th>`;
      for (const p of ps) {
        const v = this.value(p, key);
        let cons = "";
        if (aff) {
          const a = this.st(`sensor.glucocraft_${p}_consensus_ranges`)?.attributes, cv0 = a && a.has_data ? a[GC_CONS_ATTR[m]] : NaN;
          cons = `<div class="cn">${gcSay(pc(cv0, dec))}</div>`;
        }
        // color only at a cited target (or the custom goal), with >= 14 days and >= 70 % data (Battelino et al., 2019); the
        // meaning is also carried by ✓ / ✗, and the text uses the shade of the status color that reaches 4.5:1
        let col = "", mark = "";
        if (target && isNum(v) && PERIOD_DAYS[p] >= 14 && enough[p]) {
          const ok = gcMeets(target[0], v, target[1]);
          col = ok ? STATUS.ok : STATUS.alert;
          mark = ok ? ` <span aria-hidden="true">✓</span><span class="sr">${gcT("ui.result.meets")}</span>`
            : ` <span aria-hidden="true">✗</span><span class="sr">${gcT("ui.result.does_not_meet")}</span>`;
        }
        // glucose: both summary sensors carry mg/dL, shown in the display unit
        const txt = unit === "A1C" ? gmiTxt(v, this.gmiMmol(p, yours)) : unit === "%" ? (target ? pcAt(v, target[1], dec) : pc(v, dec))
          : unit === "G" ? gs(v) : dec ? n1(v) : n0(v);
        const day = unit === "%" && key !== "sensor_active" && key !== "cv" && key !== "gmi" && PERIOD_DAYS[p] >= 1 && isNum(v)
          ? `<div class="n">${gcSay(perDayTable(v), gcT("ui.per_day", { duration: gcSpeakAge(round(v / 100 * 1440)) }))}</div>` : "";
        // the GRI's two components, displayed separately (Klonoff et al., 2023); the row's note names them
        const sum = key === "gri" && !yours ? this.summary(p) : null, parts = sum ? gcGriParts(sum.gri_hypo, sum.gri_hyper) : "";
        h += `<td class="v"><span style="${col ? `color:${stc(col)};font-weight:600` : ""}">${gcSay(txt)}${mark}</span>${day}` +
          `${parts ? `<div class="n">${parts}</div>` : ""}${cons}</td>`;
      }
      h += "</tr>";
    }
    const [lim, limSaid] = limitNoteFor(this, ps);
    // the note of each period below 70% sensor active (the 70% rule); warn_periods: a second table of the same page
    // names only the periods the first one does not
    const warn = gcDataWarnings((this._cfg.warn_periods || ps).map((p) => [p, this.rawSummary(p, yours)]));
    // the table's name (a page can hold two tables of this layout): its ranges and its periods, in words
    const caption = gcSpeakText(gcT(yours ? "ui.stats.caption.custom" : "ui.stats.caption.consensus",
      { periods: new Intl.ListFormat(gcLang(), { type: "conjunction" }).format(ps.map(periodName)) }));
    this.html = this.css() + `<style>
      ${GC_WARN_CSS}
      table { width: 100%; border-collapse: collapse; font-size: 15px; table-layout: fixed; }
      col.lc { width: 34%; }
      th { font-size: 14px; font-weight: 600; color: var(--secondary-text-color); text-align: right; padding: 4px 6px; }
      td, th.l { padding: 7px 6px; border-top: 1px solid var(--divider-color); vertical-align: top; }
      th.l { text-align: left; font-size: 15px; font-weight: 400; color: var(--primary-text-color); }
      td.v { text-align: right; color: var(--primary-text-color); overflow-wrap: anywhere; }
      .n { font-size: 14px; color: var(--secondary-text-color); margin-top: 2px; white-space: normal; }
      td.v .n { overflow-wrap: normal; }
      .cn { font-size: 14px; color: var(--secondary-text-color); margin-top: 2px; font-style: italic; }
      .dn { font-style: italic; }
      /* phones: tighter padding, never below 14 px (ACCESSIBILITY.md rule 3) */
      @media (max-width: 500px) { table { font-size: 14px; } td, th { padding: 6px 2px; } th.l { font-size: 14px; } }
    </style><ha-card lang="${gcLang()}">${warn}<table><caption class="sr">${esc(caption)}</caption><colgroup><col class="lc">${ps.map(() => "<col>").join("")}</colgroup>${h}</table>` +
      `<div class="s" style="margin-top:8px">${gcSay(gcSentences([yours ? gcT("ui.stats.footnote.custom") : gcT("ui.stats.footnote.consensus"),
        gcT("ui.stats.footnote.per_day", { minutes: nf(14.4, 1) })]))}</div>` +
      (lim ? `<div class="s" style="margin-top:4px">${gcSay(lim, limSaid)}</div>` : "") + `</ha-card>`;
  }
  getCardSize() { return 9; }
}
// the note under the statistics: readings at the sensor's limit per period, from the
// *_consensus_ranges sensors (attribute limit_readings {low, high}; the same count with any ranges): [shown, spoken]
// (the spoken form says LO, HI, h and min in words, as the display card does); ["", ""] when there are none
function limitNoteFor(card, periods) {
  const at = (p) => card.st(`sensor.glucocraft_${p}_consensus_ranges`)?.attributes || {};
  const per = periods.filter((p) => gcLimitCount(at(p).limit_readings)[0] > 0);
  if (!per.length) return ["", ""];
  const list = (readings) => gcList(per.map((p) => gcT("ui.limit_note.period", { period: periodName(p),
    readings: readings(at(p)) })));
  const note = (readings) => gcSentences([gcT("ui.limit_note.list", { list: list(readings) }), gcT("ui.limit_note.rule")]);
  return [note((a) => gcLimitCountTime(a.limit_readings, a.limit_seconds)),
    note((a) => gcLimitCountSaid(a.limit_readings, a.limit_seconds))];
}

// ------------------------------------------------------------------ canvas helpers (SVG at the real width, so text is real px)
/** The base of the graph cards: SVG drawn at the card's real width, so that text sizes are real pixels. */
class GCGraph extends GCBase {
  connectedCallback() {
    super.connectedCallback();
    // a new width redraws the graph; before the language catalogues are loaded only the width is kept: set hass
    // draws once they are, and a draw now would show raw text keys (and warn about each one as missing)
    this._ro = new ResizeObserver(() => {
      const w = Math.round(this.getBoundingClientRect().width);
      if (w && w !== this._w) { this._w = w; if (gcI18nReady(this._hass)) this.draw(); }
    });
    this._ro.observe(this);
  }
  disconnectedCallback() { super.disconnectedCallback(); this._ro?.disconnect(); }
  // the drawing's width: the card's less its padding, at least 240 px (a 320 px screen leaves the card about 288 px,
  // so its graphs are drawn at their real width there; only a narrower card clips them)
  width() { return Math.max(240, (this._w || 600) - 32); }
  // the scale of every graph (glucocraft-shared.js gcFit); without values, 70 to 180 mg/dL in the display unit
  fit(mn, mx, spread) {
    if (!(mx >= mn)) { mn = gv(70); mx = gv(180); }
    return gcFit(mn, mx, spread);
  }
  step(lo, hi, n) { return gcGridStep(lo, hi, n); }   // the grid's step (glucocraft-shared.js gcGridStep)
  // chart text, always on the black chart area and its color bands (white, as on the display card), at least 14 px
  t(x, y, s, size, fill = "#fff", anchor = "start") {
    return `<text x="${x}" y="${y}" font-size="${Math.max(14, size)}" fill="${fill}" text-anchor="${anchor}" ` +
      `dominant-baseline="hanging">${esc(s)}</text>`;
  }
  // a centered axis label kept 2 px inside the chart, so that the newest date is not cut off at the right edge
  tc(x, y, s, size, w, fill = "#fff") {
    const half = String(s).length * size * 0.29;   // Roboto digits and "/" about 0.55-0.58 em
    return this.t(Math.min(Math.max(x, half + 2), w - half - 2), y, s, size, fill, "middle");
  }
}

// ------------------------------------------------------------------ history graph
// the statistics period of a graph's length in hours (its summary sensor)
const GC_HOURS_PERIOD = { 6: "6h", 24: "24h", 168: "7d", 336: "14d", 720: "30d", 2160: "90d", 4320: "180d" };
// the line under a graph's title that names its bands: the ranges in the settings or the consensus ranges (cited)
const gcBands = (yours) => (yours ? gcT("ui.bands.custom", { unit: gU() })
  : gcT("ui.bands.consensus", { tight_range: gr(70, 140), unit: gU() }));
/** A glucose graph for one period (custom:glucocraft-history). */
class GlucoCraftHistory extends GCGraph {
  watch() { return [ENT.current, ENT.config]; }
  // A graph of up to 24 hours ends now, so it moves on every minute. A longer one ends when the history file was
  // downloaded (once an hour): its readings change only with a new file, and its numbers with a new reading.
  timed() { return (this._cfg.hours || 24) <= 24; }
  /**
   * Return the history file (gcFile) for the graph in box, null without one.
   *
   * Until the first download ends, box says that the graph is loading; without a file it says why: no readings are
   * stored yet (GlucoCraft answers 503), or the download failed (it is tried again in a minute). gcFile
   * (glucocraft-shared.js) downloads each file once for every card on the page, the display card included, and keeps
   * the reason of the last failed download (gcFileFailed).
   */
  async historyFile(box, title) {
    const line = (key) => gcRender(this.shadowRoot, `<div class="s">${esc(gcT(key, { title }))}</div>`, box);
    if (box.gcHtml === undefined) line("ui.chart.loading");
    const bf = await gcFile(this._hass, "backfill");
    if (!bf) line(gcFileFailed("backfill") === "none" ? "ui.chart.no_history" : "ui.chart.load_failed");
    return bf;
  }
  // the line under a graph drawn from an older history file because the latest download failed: when that file was
  // downloaded ("" otherwise)
  asOf(bf) {
    if (!gcFileFailed("backfill")) return "";
    const at = new Date(bf.fetched * 1000), time = sameDay(at, new Date()) ? clockStr(at) : `${dayShort(at)} ${clockStr(at)}`;
    return `<div class="s">${esc(gcT("ui.chart.as_of", { time }))}</div>`;
  }
  /**
   * Return the readings of a graph's window and what is drawn from them, kept until one of the inputs changes.
   *
   * The readings by timestamp, from the full file and the 5-minute file (dl, up to 24 hours), merged in time order
   * without duplicates (both files are in time order; at the same time the full file's reading is kept); with the 11
   * readings before the window, so that the gap rule judges its first stretch by the readings before it, as the
   * statistics do. -> { gaps, uniq: [[t, value in the display unit]] in the window, lo, hi, and for a graph longer than
   * 24 hours the slots: avg, mn, mx (display unit), cnt, fT / lT (the first and last reading time of each slot) }.
   * The result is the same for the same files, window, slots, display unit and gap threshold fb, so it is kept on the
   * card: a longer graph is calculated again only when a new file arrives.
   */
  series(bf, dl, start, end, slots, long, fb) {
    const key = [bf, dl, start, end, slots, long, fb, gU()], kept = this._series;
    if (kept && key.every((k, i) => k === kept.key[i])) return kept;
    const all = [], valid = (f, k) => f.val[k] > 0 && f.val[k] <= GLU_MAX;
    const nb = bf ? bf.ts.length : 0, nd = dl ? dl.ts.length : 0;
    let i = bf ? Math.max(0, first(bf.ts, start) - GAP_WINDOW - 1) : 0, j = dl ? Math.max(0, first(dl.ts, start) - GAP_WINDOW - 1) : 0;
    for (;;) {
      while (i < nb && bf.ts[i] <= end && !valid(bf, i)) i++;
      while (j < nd && dl.ts[j] <= end && !valid(dl, j)) j++;
      const inB = i < nb && bf.ts[i] <= end, inD = j < nd && dl.ts[j] <= end;
      if (!inB && !inD) break;
      const fromB = inB && (!inD || bf.ts[i] <= dl.ts[j]), t = fromB ? bf.ts[i] : dl.ts[j], v = fromB ? bf.val[i++] : dl.val[j++];
      if (!all.length || all[all.length - 1][0] !== t) all.push([t, v]);
    }
    // the gap rule, per stretch (GlucoCraft rule): the line is interrupted at every gap of the readings
    const gaps = gcGaps(all.map(([t]) => t), fb);
    const uniq = all.filter(([t]) => t >= start).map(([t, v]) => [t, gCv(v)]);
    let lo = 999, hi = 0, avg = [], mn = [], mx = [], fT = [], lT = [], cnt = [];
    if (long) {   // average + range per slot, with the first and last reading time of each slot (gap rule between slots)
      const sum = new Float64Array(slots); cnt = new Uint32Array(slots); mn = new Array(slots).fill(999); mx = new Array(slots).fill(0);
      fT = new Array(slots).fill(0); lT = new Array(slots).fill(0);
      uniq.forEach(([t, v]) => { const s = Math.min(slots - 1, Math.floor((t - start) * slots / (end - start))); sum[s] += v; cnt[s]++; mn[s] = Math.min(mn[s], v); mx[s] = Math.max(mx[s], v);
        if (!fT[s]) fT[s] = t; lT[s] = t; });
      avg = Array.from(sum, (v, k) => (cnt[k] ? v / cnt[k] : NaN));
      mn.forEach((v, k) => { if (cnt[k]) { lo = Math.min(lo, v); hi = Math.max(hi, mx[k]); } });
    } else uniq.forEach(([, v]) => { lo = Math.min(lo, v); hi = Math.max(hi, v); });
    return (this._series = { key, gaps, uniq, lo, hi, avg, mn, mx, cnt, fT, lT });
  }
  /** Draw the graph: range bands, grid, limit lines, the readings or slot means and ranges, the header. */
  async draw() {
    if (!this._hass) return;
    const hours = this._cfg.hours || 24, W = this.width(), H = this._cfg.height || 240;
    const title = this._cfg.title || (hours <= 24 ? gcT("ui.history.title_hours", { count: hours })
      : gcT("ui.history.title_days", { count: Math.round(hours / 24) }));
    if (!this.shadowRoot.innerHTML) this.shadowRoot.innerHTML = this.css() + `<ha-card lang="${gcLang()}"><div class="g"></div></ha-card>`;
    this.shadowRoot.querySelector("ha-card")?.setAttribute("lang", gcLang());   // the language in use, at every drawing
    const box = this.shadowRoot.querySelector(".g");
    const bf = await this.historyFile(box, title);
    if (!bf) return;
    const dl = hours <= 24 ? await gcFile(this._hass, "delta") : null;
    // the content: an unchanged graph stays in place (gcRender)
    const rm = this.ranges(this._cfg.basis), r = gR(rm);   // mg/dL for the key, display unit for drawing
    const end = hours <= 24 ? Math.floor(Date.now() / 1000) : bf.fetched, start = end - hours * 3600;
    const slots = Math.min(W, 600), long = hours > 24;
    const { gaps, uniq, lo, hi, avg, mn, mx, cnt, fT, lT } = this.series(bf, dl, start, end, slots, long,
      gcGap(this.cfg(), this.attr(ENT.source, "detected_interval")));
    const [gmin, gmax] = this.fit(lo, hi, gCv(20));
    const y = (g) => Math.round(H - (Math.min(Math.max(g, gmin), gmax) - gmin) / (gmax - gmin) * H);
    const x = (t) => ((t - start) / (end - start)) * W;
    // the chart's one-sentence description for screen readers (ACCESSIBILITY.md rule 4: charts are never silent)
    const said = this.describe(title, hours, uniq);
    let svg = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" style="display:block;font-family:Roboto,sans-serif" role="img" aria-label="${esc(said)}"><rect width="${W}" height="${H}" fill="#0a0a0a"/>`;
    svg += this.bands(r, y, W);
    // the value labels in one format per axis (gcAxisText: "7.0" / "6.5" / "6.0" in mmol/L at a step of 0.5)
    const st = Math.max(isMmol() ? 0.5 : 10, this.step(gmin, gmax, 5));
    for (let k = Math.ceil(gmin / st - 1e-9); k * st <= gmax + 1e-9; k++) {
      const g = k * st, gy = y(g);
      if (gy > 4 && gy < H - 4) {
        svg += `<rect x="0" y="${gy}" width="${W}" height="1" fill="#404040"/>` + this.t(4, gy - 16, gcAxisText(g, st), 14);
      }
    }
    // x lines on whole hours (<= 24 h) or midnights (longer) in Home Assistant's time zone, like the display card;
    // labels thinned so they never collide (about 70 px each). The ticks step through the zone's clock: every stepH
    // hours of the day from the first one at or after the window's start, or every stepH / 24 days at midnight.
    const stepH = hours <= 6 ? 1 : hours <= 24 ? 4 : 24 * (hours <= 168 ? 1 : hours <= 336 ? 2 : hours <= 720 ? 5 : hours <= 2160 ? 14 : 30);
    const z0 = zoneParts(start * 1000), h0 = stepH >= 24 ? 0 : Math.ceil(z0.h / stepH) * stepH;
    const ticks = [];
    for (let k = 0; ; k++) {
      const d = new Date(stepH >= 24 ? zoneTime(z0.y, z0.mo, z0.d + k * stepH / 24) : zoneTime(z0.y, z0.mo, z0.d, h0 + k * stepH));
      if (!(d.getTime() / 1000 < end)) break;   // also a time that cannot be calculated
      if (d.getTime() / 1000 > start) ticks.push(d);
    }
    const every = Math.max(1, Math.ceil(ticks.length * 70 / W));
    ticks.forEach((d, i) => {
      const lx = x(d.getTime() / 1000);
      svg += `<rect x="${Math.round(lx)}" y="0" width="1" height="${H}" fill="#2a2a2a"/>`;
      if (i % every === 0 && lx > 20 && lx < W - 20) {
        svg += this.tc(lx, H - 18, stepH < 24 ? hourLabel(d) : numDate(d), 14, W);
      }
    });
    // the stretches of limit readings: the line breaks across them, their marks at the edge (after the data)
    const runs = this.limitRuns(bf, dl, end);
    if (long) {
      svg += gcRangeAreas(mn.map((v, i) => (cnt[i] ? v : 0)), mx.map((v, i) => (cnt[i] ? v : 0)), y, W / slots);
      // slots are joined unless the interval between the last reading of one and the first of the next is a gap or
      // holds a stretch of limit readings (as on the panels and the display card)
      let d = "", prevI = -1;
      for (let i = 0; i < slots; i++) if (isNum(avg[i])) {
        d += `${prevI >= 0 && !gcGapIn(gaps, lT[prevI], fT[i]) && !gcRunIn(runs, lT[prevI], fT[i]) ? "L" : "M"}${((i + 0.5) * W / slots).toFixed(1)} ${y(avg[i])}`;
        prevI = i;
      }
      // the mean line in white over a black underlay, so that it stays visible on the light range areas
      svg += `<path d="${d}" fill="none" stroke="#000" stroke-width="4"/><path d="${d}" fill="none" stroke="#fff" stroke-width="2"/>`;
    } else {
      let d = "", prev = null;
      uniq.forEach(([t, v]) => { d += `${prev !== null && !gcGapIn(gaps, prev, t) && !gcRunIn(runs, prev, t) ? "L" : "M"}${x(t).toFixed(1)} ${y(v)}`; prev = t; });
      svg += `<path d="${d}" fill="none" stroke="#fff" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    }
    svg += runs.length ? gcLimitMarks(runs, start, end, W, H, { low: ZONE.very_low, high: ZONE.very_high }) : "";
    svg += "</svg>";
    // a graph longer than 24 hours ends when its file was downloaded: after a failed download it says when that was
    gcRender(this.shadowRoot, this.header(title, hours, long, slots, rm, long ? this.asOf(bf) : "") + svg, box);
  }
  // The stretches of readings at the sensor's limit in the files (GCR1) and the stretch going on now (limit_since of
  // the Last glucose reading sensor), for the marks along the bottom (LO) or top (HI) edge (glucocraft-shared.js
  // gcLimitMarks, as on the display card and the panels) and the line's breaks
  limitRuns(bf, dl, end) {
    const runs = [...(bf?.limitRuns || []), ...(dl?.limitRuns || [])], cur = this.st(ENT.current)?.attributes || {};
    if ((cur.limit === "low" || cur.limit === "high") && +cur.limit_since > 0) {
      runs.push([+cur.limit_since, end, cur.limit]);
    }
    return runs;
  }
  // the seven range bands in the graph range colors, 60 % over the chart's black. r: the ranges in the display unit;
  // y: display value -> y
  bands(r, y, W) {
    return [[Infinity, r.very_high, "very_high"], [r.very_high, r.high, "high"], [r.high, r.tight_high, "near_high"],
      [r.tight_high, r.tight_low, "tight_range"], [r.tight_low, r.low, "near_low"], [r.low, r.very_low, "low"],
      [r.very_low, -Infinity, "very_low"]].map(([a, b, zone]) => {
      const ya = y(a), yb = y(b);
      return yb > ya ? `<rect x="0" y="${ya}" width="${W}" height="${yb - ya}" fill="${BAND[zone]}" fill-opacity="0.6"/>` : "";
    }).join("");
  }
  // the key to the bands, under the graph's title: each band's color with its limits (mg/dL in r), in the numbers of
  // the display unit; the in-range bands outside the tight range only where the tight range does not reach the in-range
  // limit (no inverted "181–180"), as the Documentation's Colors table; a band narrower than the display step (two
  // limits less than 1 mg/dL or 0.1 mmol/L apart as shown) has no reading that it could show, and no key ("76–75")
  bandKey(r) {
    return [["very_low", gcT("ui.band_key.under", { value: gs(r.very_low) })], ["low", gcUpToShown(r.very_low, r.low)],
      ["near_low", gcUpToShown(r.low, r.tight_low)],
      ["tight_range", gr(r.tight_low, r.tight_high)],
      ["near_high", gcFromShown(r.tight_high, r.high)],
      ["high", gcFromShown(r.high, r.very_high)], ["very_high", gcT("ui.band_key.over", { value: gs(r.very_high) })]]
      .filter(([, t]) => t !== null)
      .map(([zone, t]) => `<span class="k"><i style="background:${BAND[zone]}"></i>${gcSay(t)}</span>`).join("");
  }
  // "Glucose — Last 6 Hours: lowest 58, highest 160, mean 110 milligrams per deciliter.": the same mean, lowest and
  // highest as the numbers shown above the graph (the period's summary sensor: the time-weighted mean), in the display
  // unit; without the summary (GlucoCraft reloading, or before the summary sensors exist), from the readings drawn
  // (their plain average, in one pass: a 6-month window can hold more readings than a function call takes arguments).
  describe(title, hours, pts) {
    if (!pts.length) return gcT("ui.chart.no_readings", { title });
    const p = GC_HOURS_PERIOD[hours], a = p ? this.summary(p, this._cfg.basis === "yours") : null;
    let mean, mn, mx;
    if (a && isNum(a.mean) && isNum(a.min_value) && isNum(a.max_value)) [mean, mn, mx] = [a.mean, a.min_value, a.max_value];
    else {
      let sum = 0; mn = Infinity; mx = -Infinity;
      for (const [, v] of pts) { const g = isMmol() ? v * MGDL_PER_MMOLL : v; sum += g; if (g < mn) mn = g; if (g > mx) mx = g; }
      mean = sum / pts.length;
    }
    const [lo, hi] = this.minMaxText(mn, mx, true);
    return gcT("ui.history.said", { title, lowest: lo, highest: hi,
      mean: gcSpeak(gv(mean), gU(), { d: isMmol() ? 1 : 0, dec: GC.dec }) });
  }
  /**
   * Return the graph's header: title, the period's numbers, how the line is drawn and the band key.
   *
   * What the graph shows, so that each graph says what it is for:
   * the period's own numbers from GlucoCraft, how the line is drawn, and a key to the bands with their limits;
   * asOf: the line of an older history file (asOf), or ""
   */
  header(title, hours, long, slots, r, asOf = "") {
    const p = GC_HOURS_PERIOD[hours];
    const yours = this._cfg.basis === "yours";
    let nums = "";
    if (p) {
      // the period's summary sensor: <period> custom ranges ("yours") or <period> consensus ranges; glucose values in mg/dL
      const a = this.summary(p, yours) || {}, get = (k) => (isNum(a[k]) ? a[k] : NaN);
      const [lo, hi] = this.minMaxText(get("min_value"), get("max_value"));
      nums = gcT("ui.history.numbers", { mean: gs(get("mean")), unit: gU(), lowest: lo, highest: hi,
        in_range: pc(get(yours ? "time_in_range" : "time_70_180")), sensor_active: pc(get("sensor_active")) });
    }
    // how the line is drawn, with the gap rule (GlucoCraft rule, the same as in the statistics and sensor active); the
    // slot length is rounded (to whole hours, or to 5 minutes), so the texts say "about". A graph longer than 24 hours
    // is drawn from the history file, which the cards download once an hour (gcFile); its numbers come from the
    // summary sensors, recalculated every 5 minutes
    const slotMin = (hours * 60) / slots;
    const how = !long ? gcT("ui.history.how_readings") : slotMin >= 90
      ? gcT("ui.history.how_hours", { hours: Math.round(slotMin / 60) }) : gcT("ui.history.how_minutes", { minutes: Math.round(slotMin / 5) * 5 });
    const key = this.bandKey(r);
    return `<style>
        .gh { margin-bottom: 8px; } .gh .t { margin-bottom: 2px; }
        .k { display: inline-flex; align-items: center; gap: 4px; margin: 4px 10px 0 0; font-size: 14px; color: var(--secondary-text-color); white-space: nowrap; }
        .k i { width: 12px; height: 12px; border-radius: 2px; opacity: 0.75; }
      </style><div class="gh"><div class="t">${esc(title)}</div>` +
      (nums ? `<div class="s">${gcSay(nums)}</div>` : "") + `<div class="s">${esc(how)}</div>` + asOf +
      `<div class="s">${gcSay(gcBands(yours))}</div><div>${key}</div></div>`;
  }
  getCardSize() { return 6; }
}

// ------------------------------------------------------------------ Change Over Time
// Data: sensor.glucocraft_change_over_time (change.py), for the consensus ranges ("consensus") or the ranges in the
// settings ("personal"). The line is the 14-day value at each week's end (14 days = the consensus period); the faint dots
// are the 7-day values of the same weeks, shown without colors or comparisons (7 days is too short for them).
const ENT_CHANGE = "sensor.glucocraft_change_over_time";
// metric: [label(basis, ranges), unit, chart scale lo, hi, minimum spread, consensus target [op, value, source] or null,
//          personal goal key or null, value keys]
const CHANGE_METRICS = {
  tir: [(b, r) => gcT("ui.measure.time_in_range", { range: b === "personal" ? gr(r.low, r.high) : gr(70, 180), unit: gU() }), "%", 50, 100, 10, GC_CONS_GOAL.time_in_range, "time_in_range", ["tir"]],
  titr: [(b, r) => gcT("ui.measure.time_in_tight_range", { range: b === "personal" ? gr(r.tight_low, r.tight_high) : gr(70, 140), unit: gU() }), "%", 30, 100, 10, null, "time_in_tight_range", ["titr"]],
  below: [(b, r) => gcT("ui.measure.time_below", { value: gs(b === "personal" ? r.low : 70), unit: gU() }), "%", 0, 25, 4, GC_CONS_GOAL.time_low, "time_low", ["below"]],
  very_low: [(b, r) => gcT("ui.measure.time_below", { value: gs(b === "personal" ? r.very_low : 54), unit: gU() }), "%", 0, 10, 2, GC_CONS_GOAL.time_very_low, "time_very_low", ["very_low"]],
  above: [(b, r) => gcT("ui.measure.time_above", { value: gs(b === "personal" ? r.high : 180), unit: gU() }), "%", 0, 25, 4, GC_CONS_GOAL.time_high, "time_high", ["above"]],
  very_high: [(b, r) => gcT("ui.measure.time_above", { value: gs(b === "personal" ? r.very_high : 250), unit: gU() }), "%", 0, 10, 2, GC_CONS_GOAL.time_very_high, "time_very_high", ["very_high"]],
  mean: [() => gcT("ui.measure.mean"), "G", 70, 200, 20, null, null, ["mean"]],
  sd: [() => gcT("ui.measure.standard_deviation"), "G", 15, 55, 10, null, null, ["sd"]],
  cv: [() => gcT("ui.measure.cv"), "%", 20, 50, 10, GC_CONS_GOAL.cv, "cv", ["cv"]],
  gmi: [() => gcT("ui.measure.gmi"), "%", 5, 8, 0.5, null, "gmi", ["gmi"]],
  gri: [() => gcT("ui.measure.gri"), "", 0, 100, 10, null, null, ["gri"]],
  min: [() => gcT("ui.measure.lowest_highest"), "G", 10, 350, 20, null, null, ["min", "max"]],
};
// a difference in percentage points: "+2.5 percentage points"
const gcPoints = (x) => gcT("ui.points", { count: Math.abs(x), digits: 1, difference: `${x > 0 ? "+" : ""}${nf(x, 1)}` });
// mm: the GMI's published value in mmol/mol (gmi_mmol_mol of the same period), for k === "gmi"; rules: the value of the
// target or goal a percentage is shown next to (pcAt)
const fmtv = (v, unit, k, mm, rules) => (!isNum(v) ? "---" : unit === "G" ? `${gs(v)} ${gU()}` : k === "gmi" ? gmiTxt(v, mm)
  : unit === "%" ? (rules === undefined ? pc(v, 1) : pcAt(v, rules, 1)) : `${round(v)}${unit}`);

/** The base of the Change Over Time cards: data, basis, targets or goals. */
class GCChangeBase extends GCGraph {
  watch() { return [ENT_CHANGE, ENT.config]; }
  basis() { return this._cfg.basis === "personal" ? "personal" : "consensus"; }
  // the attribute "custom" holds the ranges in the settings (the card's basis key stays "personal")
  data() { return this.attr(ENT_CHANGE, this.basis() === "personal" ? "custom" : "consensus") || null; }
  target(m) {   // [op, value, label] of the cited consensus target or of the user's goal, or null
    const def = CHANGE_METRICS[m];
    if (this.basis() === "consensus") {
      return def[5] ? [def[5][0], def[5][1], gcT("ui.target.consensus", { target: gcCompare(def[5][0], `${dtx(def[5][1])}${def[1]}`), source: def[5][2] })] : null;
    }
    const g = (this.cfg().goals || {})[def[6]];
    // with the consensus target where the goal differs, or labeled as having no published target
    return g ? [g.op, g.value, gcGoalText(def[6], g)] : null;
  }
  // Custom Ranges: true when this measure depends on a range limit that differs from the consensus
  affected(m) { return this.basis() === "personal" && gcAffected(this.cfg(), m); }
  consData() { return this.attr(ENT_CHANGE, "consensus") || null; }
}

/** The Change Over Time comparison table (custom:glucocraft-change). */
class GlucoCraftChange extends GCChangeBase {   // the 4-week comparison table
  /** Draw the table: the last 4 weeks against 9-13 and 22-26 weeks ago, the direction and the 14-day result. */
  draw() {
    if (!this._hass) return;
    const d = this.data(), b = this.basis(), r = this.ranges(b === "personal" ? "yours" : "consensus");
    if (!d) { this.html = this.css() + `<ha-card lang="${gcLang()}"><div class="s">${gcT("ui.change.not_calculated")}</div></ha-card>`; return; }
    const c = d.compare || {}, now = c.last_4_weeks || {}, m3 = c.weeks_9_13_ago || {}, m6 = c.weeks_22_26_ago || {};
    // the earlier window the directions and the ‡ mark use, one for the whole table (gcRefWindow)
    const use6 = gcRefWindow(c) === "weeks_22_26_ago", hasRef = !!(use6 ? c.weeks_22_26_ago : c.weeks_9_13_ago);
    const cur = (d.weeks14 || [])[0] || {};
    const cc = this.consData()?.compare || {}, ccs = [cc.last_4_weeks || {}, cc.weeks_9_13_ago || {}, cc.weeks_22_26_ago || {}];
    // the GRI only on the consensus ranges, the only ranges it is defined on (Klonoff et al., 2023)
    const rows = Object.keys(CHANGE_METRICS).filter((m) => m !== "min" && (m !== "gri" || b !== "personal")).map((m) => {
      const def = CHANGE_METRICS[m], unit = def[1], k = def[7][0], t = this.target(m);
      const a = now[k], p3 = m3[k], p6 = m6[k];
      // Custom Ranges: the results with the consensus ranges in smaller text where the range differs
      const aff = this.affected(m), cs = (i) => (aff ? `<div class="cn">${gcSay(fmtv(ccs[i][k], unit, k))}</div>` : "");
      // A difference is that of the two values as they are shown in the table (fmtv): glucose in whole mg/dL or 0.1
      // mmol/L, percentages and the GMI in % to one decimal (in points), the GRI whole; the GMI in mmol/mol from the
      // two values in mmol/mol (dm). The value, the direction and the ‡ mark are all taken from it, so that they agree
      // with the values beside them (never "+1 mg/dL" between "100" and "100", nor "-0.0 mmol/L" beside "lower").
      const places = (unit === "G" && isMmol()) || unit === "%" || k === "gmi" ? 1 : 0;
      const shownOf = (v) => (!isNum(v) ? NaN : unit === "G" ? gShown(v) : round(v, places));
      const dif = (x, y) => (isNum(shownOf(x)) && isNum(shownOf(y)) ? round(shownOf(x) - shownOf(y), places) : NaN);
      const d3 = dif(a, p3), d6 = dif(a, p6), ref = use6 ? d6 : d3;
      const dm = (w) => (isNum(gcGmiMmol(now)) && isNum(gcGmiMmol(w)) ? gcGmiMmol(now) - gcGmiMmol(w) : NaN);
      const refShown = k === "gmi" && GC.a1c === "mmol_mol" && isNum(ref) ? dm(use6 ? m6 : m3) : ref;
      // the direction of the difference in plain arithmetic: higher, lower or no change than the earlier period (the
      // target is shown separately; no direction relative to a target); "---" for a measure without a value in the
      // window the table compares with
      const direction = !hasRef ? "" : !isNum(refShown) ? "---" : Math.abs(refShown) < 0.05 ? gcT("ui.change.direction.none")
        : use6 ? (refShown > 0 ? gcT("ui.change.direction.higher_22_26") : gcT("ui.change.direction.lower_22_26"))
          : (refShown > 0 ? gcT("ui.change.direction.higher_9_13") : gcT("ui.change.direction.lower_9_13"));
      let result = "";
      if (t && isNum(cur[k]) && gcActiveOk(cur.active_ok)) {
        const ok = gcMeets(t[0], cur[k], t[1]);
        result = `<span style="color:${stc(ok ? STATUS.ok : STATUS.alert)}"><span aria-hidden="true">${ok ? "✓" : "✗"}</span> ` +
          `${ok ? gcT("ui.result.meets") : gcT("ui.result.does_not_meet")}</span>`;
      }
      // ‡ marks the difference the direction uses: 22–26 weeks ago, or 9–13 weeks ago when 22–26 weeks ago has less than 70% sensor active
      const big = b === "consensus" && m === "tir" && isNum(ref) && Math.abs(ref) >= 5 ? " ‡" : "";
      const big3 = use6 ? "" : big, big6 = use6 ? big : "";
      // a difference (as shown, dif) with its unit: percentage points for the percentages, the glucose unit for the
      // glucose values; a sign only before a shown number other than zero
      const sd = (x, w) => {
        if (!isNum(x)) return "---";
        if (k === "gmi") return a1cDiff(x, dm(w));
        if (unit === "%") return gcPoints(x);
        const num = `${x > 0 ? "+" : ""}${unit === "G" && isMmol() ? nf(x, 1) : String(x)}`;
        return unit === "G" ? `${num} ${gU()}` : num;
      };
      const cd = (i) => (aff ? `<div class="cn">${gcSay(sd(dif(ccs[0][k], ccs[i][k])))}</div>` : "");
      const dn = aff ? `<div class="nt"><i>${gcSay(gcT("ui.range_differs", { band: gcConsBandText(m, gcConsRanges(this.cfg())) }))}</i></div>` : "";
      // the note under the measure: the target or goal; on Custom Ranges, why a measure has none (no goal can be set
      // for the mean and the standard deviation)
      const note = t ? t[2] : b === "personal"
        ? (def[6] ? gcT("ui.goal.none") : gcT("ui.chart.note.no_goal"))
        : m === "titr" ? gcT("ui.note.tight_range") : gcT("ui.note.no_consensus_target");
      return `<tr><th scope="row" class="l">${gcSay(def[0](b, r))}<div class="nt">${gcSay(note)}</div>${dn}</th>` +
        `<td><b>${gcSay(fmtv(a, unit, k, gcGmiMmol(now)))}</b>${cs(0)}</td><td>${gcSay(fmtv(p3, unit, k, gcGmiMmol(m3)))}${cs(1)}</td><td>${gcSay(sd(d3, m3) + big3)}${cd(1)}</td><td>${gcSay(fmtv(p6, unit, k, gcGmiMmol(m6)))}${cs(2)}</td><td>${gcSay(sd(d6, m6) + big6)}${cd(2)}</td>` +
        `<td class="l">${gcSay(direction)}</td><td class="l">${result}</td></tr>`;
    }).join("");
    const act = (x) => pc(x?.active);
    this.html = this.css() + `<style>
        .tw { overflow-x: auto; } table { width: 100%; border-collapse: collapse; font-size: 14px; min-width: 800px; }
        th { padding: 6px 4px; font-size: 14px; border-bottom: 2px solid var(--divider-color); text-align: center; color: var(--secondary-text-color); font-weight: 500; }
        td, th[scope="row"] { text-align: center; padding: 5px 4px; border-bottom: 1px solid var(--divider-color); vertical-align: top; }
        th[scope="row"] { font-weight: 400; color: var(--primary-text-color); }
        td.l, th.l { text-align: left; } .nt { font-size: 14px; color: var(--secondary-text-color); }
        .cn { font-size: 14px; color: var(--secondary-text-color); font-style: italic; }
      </style><ha-card lang="${gcLang()}"><div class="tw" tabindex="0" role="region" aria-labelledby="cap"><table>` +
      `<caption class="sr" id="cap">${esc(gcSpeakText(gcT(b === "personal" ? "ui.change.caption.custom" : "ui.change.caption.consensus")))}</caption>` +
      `<tr><th scope="col" class="l">${gcT("ui.column.measure")}</th>` +
      `<th scope="col">${gcT("ui.change.column.last_4_weeks")}</th><th scope="col">${gcSay(gcT("ui.change.column.weeks_9_13"))}</th>` +
      `<th scope="col">${gcT("ui.change.column.difference")}</th><th scope="col">${gcSay(gcT("ui.change.column.weeks_22_26"))}</th>` +
      `<th scope="col">${gcT("ui.change.column.difference")}</th><th scope="col" class="l">${gcT("ui.change.column.direction")}</th>` +
      `<th scope="col" class="l">${gcT("ui.column.last_14_days")}</th></tr>${rows}` +
      `<tr><th scope="row" class="l">${gcT("ui.label.sensor_active")}</th><td>${gcSay(act(now))}</td><td>${gcSay(act(m3))}</td><td></td><td>${gcSay(act(m6))}</td><td></td><td></td><td></td></tr></table></div>` +
      `<div class="s" style="margin-top:8px">${gcSayHtml(b === "personal" ? gcT("ui.change.note.custom") : gcT("ui.change.note.consensus"))}</div></ha-card>`;
  }
  getCardSize() { return 8; }
}

/** One measure over 26 weeks (custom:glucocraft-change-chart). */
class GlucoCraftChangeChart extends GCChangeBase {   // one measure over 26 weeks
  // metric: one of CHANGE_METRICS (default tir)
  optionProblem() {
    const m = this._cfg.metric;
    return !m || Object.hasOwn(CHANGE_METRICS, m) ? null : ["metric", m, Object.keys(CHANGE_METRICS).join(", ")];
  }
  /** Draw the chart: target background, the 14-day line, the 7-day dots and the named target line. */
  draw() {
    if (!this._hass || this.optionError()) return;
    const m = this._cfg.metric || "tir", def = CHANGE_METRICS[m];
    const b = this.basis(), r = this.ranges(b === "personal" ? "yours" : "consensus"), d = this.data();
    const [label, unit, plo0, phi0, spread0] = def, keys = def[7], t = this.target(m);
    // glucose measures in the display unit; GMI in mmol/mol when that is the A1C unit (axis and target line)
    const glu = unit === "G", am = m === "gmi" && GC.a1c === "mmol_mol";
    const cv = (v) => (glu ? gCv(v) : am && isNum(v) ? gmiPctToMmol(v) : v);
    // the smallest spread of the axis: a GMI spread in % becomes mmol/mol by the GMI rule's slope (gmiPctToMmol is a
    // straight line, so a difference converts without its offset: about 10.92 mmol/mol per percentage point)
    const plo = cv(plo0), phi = cv(phi0), spread = am ? spread0 * (gmiPctToMmol(1) - gmiPctToMmol(0)) : cv(spread0);
    const w14raw = d?.weeks14 || [], w7raw = d?.weeks7 || [], ends = d?.week_ends || [];
    // the plotted values: glucose in the display unit; a GMI in mmol/mol as the week's own value in mmol/mol (from its
    // mean glucose, gcGmiMmol), not converted from its GMI in %
    const conv = (w) => (!w ? w : am ? { ...w, gmi: isNum(w.gmi) ? gcGmiMmol(w) : NaN }
      : glu ? Object.fromEntries(Object.entries(w).map(([kk, vv]) => [kk, keys.includes(kk) ? cv(vv) : vv])) : w);
    const w14 = w14raw.map(conv), w7 = w7raw.map(conv);
    const n = Math.max(w14.length, 1), W = this.width(), H = 170;
    let lo = Infinity, hi = -Infinity;
    [w14, w7].forEach((ws) => ws.forEach((w) => keys.forEach((k) => { const v = w?.[k]; if (isNum(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } })));
    const [ymin, ymax] = isFinite(lo) ? this.fit(lo, hi, spread) : [plo, phi];
    const y = (v) => Math.round(H - (Math.min(Math.max(v, ymin), ymax) - ymin) / (ymax - ymin) * H);
    const xs = n > 1 ? (W - 24) / (n - 1) : 0, x = (i) => 12 + (n - 1 - i) * xs;   // newest on the right
    const chart = { W, H, ymin, ymax, y, x, n };
    const [background, targetLine] = this.targetBackground(m, t, chart, am ? cv : null);
    let svg = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" style="display:block;font-family:Roboto,sans-serif"><rect width="${W}" height="${H}" fill="#0a0a0a"/>` +
      background + this.grid(chart, ends) + this.weeks(chart, keys, w14, w7) +
      targetLine + "</svg>";   // the target line and its name over the data
    const cur = w14raw[0] || {};
    const loTxt = (w) => fmtv(w?.min, "G");
    const hiTxt = (w) => fmtv(w?.max, "G");
    let nowTxt = m === "min" ? gcT("ui.chart.lowest_highest", { lowest: loTxt(cur), highest: hiTxt(cur) })
      : fmtv(cur[keys[0]], unit, keys[0], cur.gmi_mmol_mol, t ? +t[1] : undefined);
    // Custom Ranges: with the result on the consensus ranges where the range differs
    if (this.affected(m)) nowTxt = gcBoth(nowTxt, fmtv(((this.consData()?.weeks14 || [])[0] || {})[keys[0]], unit, keys[0]));
    const note = t ? t[2] : b === "personal"
      ? (def[6] ? gcT("ui.goal.none") : gcT("ui.chart.note.no_goal"))
      : m === "gri" ? gcT("ui.note.gri_zones") : m === "gmi" ? gcT("ui.note.gmi")
        : m === "titr" ? gcT("ui.note.tight_range") : gcT("ui.note.no_consensus_target");
    const said = this.description(m, label(b, r), w14raw, w7);
    svg = svg.replace("<svg ", `<svg role="img" aria-label="${esc(said)}" `);
    this.html = this.css() + `<ha-card lang="${gcLang()}"><div class="t">${esc(label(b, r))}</div>` +
      `<div class="s">${esc(gcT("ui.chart.last_14_days", { value: nowTxt }))}\n${esc(note)}</div><div style="margin-top:10px">${svg}</div>` +
      `<div class="s">${gcT("ui.chart.legend")}</div></ha-card>`;
  }
  /**
   * Return the chart's background and its named target line (SVG): green where a value meets the cited target (or
   * the custom goal), red where it does not, split exactly at it; without a target or goal the neutral gray, and no
   * line. -> [background, target line]; the line is drawn over the data.
   *
   * t: the target [op, value, label] (target()); chart: { W, H, ymin, ymax, y }; toAxis: the conversion of the
   * target's value to the axis (a GMI goal in % on an axis in mmol/mol), or null.
   */
  targetBackground(m, t, chart, toAxis) {
    const { W, H, ymin, ymax, y } = chart;
    if (!t) return [`<rect width="${W}" height="${H}" fill="${NEUTRAL}" fill-opacity="0.6"/>`, ""];
    let svg = "", line = "";
    const up = t[0] === ">" || t[0] === ">=", tv = toAxis ? toAxis(t[1]) : t[1], ty = tv >= ymax ? 0 : tv <= ymin ? H : y(tv);
    if (ty > 0) svg += `<rect width="${W}" height="${ty}" fill="${up ? STATUS.ok : STATUS.alert}" fill-opacity="0.6"/>`;
    if (ty < H) svg += `<rect y="${ty}" width="${W}" height="${H - ty}" fill="${up ? STATUS.alert : STATUS.ok}" fill-opacity="0.6"/>`;
    // the target line, named (color is never the only cue): "Consensus target: more than 70%"
    if (ty > 0 && ty < H) {
      const target = gcCompare(t[0], m === "gmi" ? gmiTxt(+t[1]) : `${dtx(t[1])}${CHANGE_METRICS[m][1]}`);
      const name = this.basis() === "personal" ? gcT("ui.chart.target_line.custom", { target }) : gcT("ui.chart.target_line.consensus", { target });
      line = `<line x1="0" y1="${ty + 0.5}" x2="${W}" y2="${ty + 0.5}" stroke="#ffffff" stroke-width="1.5" stroke-dasharray="6 4"/>` +
        this.t(W - 6, ty < 24 ? ty + 3 : ty - 19, name, 14, "#fff", "end");
    }
    return [svg, line];
  }
  // the grid (SVG): a line with its value label at each step of the axis, the labels in one format per axis
  // (gcAxisText); a line with the week's end date at every fourth week
  grid(chart, ends) {
    const { W, H, ymin, ymax, y, x, n } = chart, st = this.step(ymin, ymax, 4);
    let svg = "";
    for (let k = Math.ceil(ymin / st - 1e-9); k * st <= ymax + 1e-9; k++) {
      const g = k * st, gy = y(g);
      if (gy > 10 && gy < H - 22) {
        svg += `<rect y="${gy}" width="${W}" height="1" fill="#404040"/>` + this.t(4, gy - 16, gcAxisText(g, st), 14);
      }
    }
    for (let i = 0; i < n; i += 4) {
      const e = ends[i] ? new Date(ends[i]) : null;
      svg += `<rect x="${Math.round(x(i))}" y="0" width="1" height="${H - 18}" fill="#2a2a2a"/>` + (e ? this.tc(x(i), H - 16, numDate(e), 14, W) : "");
    }
    return svg;
  }
  // the weekly values (SVG), per value key: the faint 7-day dots, then the 14-day line with a point at each week (a
  // week without a value breaks the line)
  weeks(chart, keys, w14, w7) {
    const { y, x, n } = chart;
    let svg = "";
    keys.forEach((k) => {
      for (let i = 0; i < w7.length; i++) { const v = w7[i]?.[k]; if (isNum(v)) svg += `<circle cx="${x(i).toFixed(1)}" cy="${y(v)}" r="2.5" fill="#bbb" fill-opacity="0.45"/>`; }
      let path = "", dots = "", pen = false;
      for (let i = n - 1; i >= 0; i--) {
        const v = w14[i]?.[k];
        if (!isNum(v)) { pen = false; continue; }
        path += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(v)}`; pen = true;
        dots += `<rect x="${x(i) - 2}" y="${y(v) - 2}" width="4" height="4" fill="#fff"/>`;
      }
      svg += `<path d="${path}" fill="none" stroke="#fff" stroke-width="2" stroke-linejoin="round"/>` + dots;
    });
    return svg;
  }
  /**
   * Return the chart's description for screen readers (ACCESSIBILITY.md rule 4): "Time in Range 70–180 of each 14-day
   * period, last 26 weeks: from 81.0 to 93.0 percent.", from the 14-day values drawn (w14raw, as published), the unit
   * once after the second number (the display card's weekly pages say it in the same words); then the faint 7-day
   * dots, where drawn (w7, as plotted). label: the measure's name as shown.
   */
  description(m, label, w14raw, w7) {
    const unit = CHANGE_METRICS[m][1], keys = CHANGE_METRICS[m][7];
    const v14 = w14raw.map((w) => w?.[keys[0]]).filter(isNum);
    const digits = unit === "G" ? (isMmol() ? 1 : 0) : unit === "%" ? 1 : 0;
    const spk = (v) => (unit === "G" ? gcSpeak(gv(v), gU(), { d: digits, dec: GC.dec })
      : gcSpeak(v, unit === "%" ? "%" : "", { d: digits, dec: GC.dec }));
    const num = (v) => gcSpeakNumber(unit === "G" ? gv(v) : v, digits, GC.dec);
    // GMI: the lowest and the highest week, each in the A1C unit setting with its own value in mmol/mol (of two weeks
    // with the same GMI in %, the one with the lower or the higher mean)
    const gmiWeeks = w14raw.filter((w) => isNum(w?.gmi)).sort((x, y) => x.gmi - y.gmi || x.mean - y.mean);
    const spkGmi = (w) => gcSpeakA1c(w.gmi, GC.a1c, GC.dec, gcGmiMmol(w));
    const measure = gcSpeakText(label);
    // Lowest and Highest: both lines, the weekly minimums and the weekly maximums
    const range = (k) => {
      const vals = w14raw.map((w) => w?.[k]).filter(isNum);
      return [num(Math.min(...vals)), spk(Math.max(...vals))];
    };
    let said = gcT("ui.chart.said_empty", { measure });
    if (v14.length && m === "min") {
      const [lowFrom, lowTo] = range("min"), [highFrom, highTo] = range("max");
      said = gcT("ui.chart.said_min_max", { measure, count: w14raw.length, low_from: lowFrom, low_to: lowTo,
        high_from: highFrom, high_to: highTo });
    } else if (v14.length && m === "gmi") {
      said = gcT("ui.chart.said", { measure, count: w14raw.length, from: spkGmi(gmiWeeks[0]), to: spkGmi(gmiWeeks.at(-1)) });
    } else if (v14.length) {
      said = gcT("ui.chart.said", { measure, count: w14raw.length, from: num(Math.min(...v14)), to: spk(Math.max(...v14)) });
    }
    if (v14.length && w7.some((w) => keys.some((k) => isNum(w?.[k])))) said += ` ${gcT("ui.chart.said_dots")}`;
    return said;
  }
  getCardSize() { return 4; }
}

// ------------------------------------------------------------------ core measures: the core consensus
// measures first, in large figures, with ✓ / ✗ for the last 14 days and the change since 22–26 weeks ago
const CORE = { consensus: ["tir", "titr", "below", "very_low", "above"],   // Custom Ranges shows time in tight range first
  personal: ["titr", "tir", "below", "very_low", "above"] };
/** The core measures (custom:glucocraft-core). */
class GlucoCraftCore extends GCChangeBase {
  watch() { return [ENT_CHANGE, ENT.config, "sensor.glucocraft_14d_consensus_ranges", "sensor.glucocraft_14d_custom_ranges"]; }
  last14() {   // the live 14-day values (recalculated every 5 minutes), in the Change Over Time keys
    if (this.basis() === "consensus") {
      const a = this.st("sensor.glucocraft_14d_consensus_ranges")?.attributes || {};
      return a.has_data ? { tir: a.time_70_180, titr: a.time_70_140, below: a.time_below_70, very_low: a.time_below_54, above: a.time_above_180, active: a.sensor_active, enough: this.activeOk("14d") } : {};
    }
    const v14 = (k) => this.custom("14d", k);
    return { tir: v14("time_in_range"), titr: v14("time_in_tight_range"), below: this.combined("14d", "below"),
      very_low: v14("time_very_low"), above: this.combined("14d", "above"), active: v14("sensor_active"),
      enough: this.activeOk("14d", true) };
  }
  /** Draw one tile per core measure and the same results as a table for screen readers. */
  draw() {
    if (!this._hass) return;
    const b = this.basis(), r = this.ranges(b === "personal" ? "yours" : "consensus"), v = this.last14();
    const cmp = this.data()?.compare || {}, now4 = cmp.last_4_weeks || {}, old4 = cmp.weeks_22_26_ago || {}, mid4 = cmp.weeks_9_13_ago || {};
    const refKey = gcRefWindow(cmp);
    const enough = !!v.enough;   // at least 70% sensor active in the 14 days, compared before rounding
    const ca14 = this.st("sensor.glucocraft_14d_consensus_ranges")?.attributes || {};
    const rows = [];   // the same results as a table for screen readers (ACCESSIBILITY.md rule 4: real tables)
    const tiles = CORE[b].map((m) => {
      const def = CHANGE_METRICS[m], t = this.target(m), x = v[m];
      // the figure as shown: one decimal, two where one would show the target's or goal's value on the wrong side of it
      const [xs, xd] = isNum(x) ? (t ? shownAgainst(x, +t[1], 1) : [round(x, 1), 1]) : [NaN, 1];
      // Custom Ranges: the result with the consensus ranges in smaller text where the range differs
      const both = this.affected(m) ? `<div class="cr">${esc(gcBoth(pc(xs, xd), pc(ca14.has_data ? ca14[GC_CONS_ATTR[m]] : NaN, 1)))}</div>` : "";
      // the figure in the primary text color; met / not met as ✓ / ✗ with its word, in a shade of the status color
      // that reaches 4.5:1 on the tile, and as the tile's colored edge
      let ok = null, col = "";
      if (t && enough && isNum(x)) {
        ok = gcMeets(t[0], x, t[1]);
        col = ok ? STATUS.ok : STATUS.alert;
      }
      // since 22–26 weeks ago, or since 9–13 weeks ago when 22–26 weeks ago has less than 70% sensor active (one window
      // for every tile, gcRefWindow); the difference of the two values as shown, to one decimal (gcPointsShown)
      const old = refKey === "weeks_22_26_ago", ref = (old ? old4 : mid4)[m];
      let chg = gcT("ui.core.change.no_history");
      if (isNum(now4[m]) && isNum(ref)) {
        const d = gcPointsShown(now4[m], ref), difference = `${d > 0 ? "+" : ""}${nf(d, 1)}`;
        // the arithmetic difference only (no direction relative to a target)
        chg = Math.abs(d) < 0.05 ? (old ? gcT("ui.core.change.none_22_26") : gcT("ui.core.change.none_9_13"))
          : (old ? gcT("ui.core.change.points_22_26", { count: Math.abs(d), digits: 1, difference })
            : gcT("ui.core.change.points_9_13", { count: Math.abs(d), digits: 1, difference })) +
            (b === "consensus" && m === "tir" && Math.abs(d) >= 5 ? " ‡" : "");
        const cc = this.consData()?.compare || {}, cn = cc.last_4_weeks?.[m], co = cc[refKey]?.[m];
        if (this.affected(m) && isNum(cn) && isNum(co)) {
          const e = gcPointsShown(cn, co);
          chg = gcT("ui.core.change.with_consensus", { change: chg, count: Math.abs(e), digits: 1, difference: `${e > 0 ? "+" : ""}${nf(e, 1)}` });
        }
      }
      // the result: ✓ meets / ✗ does not meet, or why there is none
      const result = ok !== null ? (ok ? gcT("ui.result.meets") : gcT("ui.result.does_not_meet"))
        : t ? gcT("ui.core.not_compared") : b === "personal" ? gcT("ui.goal.none") : m === "titr" ? gcT("ui.core.no_clinical_target") : "";
      const mk = ok === null ? result : ok ? gcT("ui.core.mark.meets") : gcT("ui.core.mark.does_not_meet");
      const tg = t ? t[2] : m === "titr" && b === "consensus" ? gcT("ui.core.no_colors") : "";
      // a table for screen readers only: its text is the spoken form (the units and symbols in words)
      rows.push(`<tr><th scope="row">${esc(gcSpeakText(def[0](b, r)))}</th><td>${esc(isNum(x) ? gcSpeak(xs, "%", { d: xd, dec: GC.dec }) : gcT("spoken.not_available"))}</td>` +
        `<td>${esc(gcSpeakText(result))}</td><td>${esc(gcSpeakText(tg))}</td><td>${esc(gcSpeakText(chg))}</td></tr>`);
      return `<div class="tile" style="border-left-color:${col || "var(--divider-color)"}"><div class="lab">${esc(def[0](b, r))}</div>` +
        `<div class="big">${pc(xs, xd)}</div>${both}` +
        `<div class="mk"${col ? ` style="color:${stc(col)}"` : ""}>${esc(mk)}</div>` +
        `<div class="tg">${esc(tg)}</div><div class="ch">${esc(chg)}</div></div>`;
    }).join("");
    const intro = b === "personal" ? gcT("ui.core.intro.custom") : gcT("ui.core.intro.consensus");
    const table = `<table class="sr"><caption>${gcT("ui.core.caption")}</caption><tr><th scope="col">${gcT("ui.column.measure")}</th>` +
      `<th scope="col">${gcT("ui.column.last_14_days")}</th><th scope="col">${gcT("ui.core.column.result")}</th>` +
      `<th scope="col">${b === "personal" ? gcT("ui.core.column.custom_goal") : gcT("ui.core.column.consensus_target")}</th>` +
      `<th scope="col">${gcT("ui.core.column.change")}</th></tr>${rows.join("")}</table>`;
    const figures = isNum(v.active) ? gcT("ui.core.note.figures_active", { percent: pcAt(v.active, 70) }) : gcT("ui.core.note.figures");
    this.html = this.css() + `<style>
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; margin-top: 10px; }
        .tile { border-left: 6px solid; border-radius: 8px; background: var(--secondary-background-color); padding: 10px 12px; }
        .lab { font-size: 14px; font-weight: 600; } .big { font-size: 34px; font-weight: 600; line-height: 1.15; margin-top: 2px; }
        .mk { font-size: 14px; font-weight: 600; }
        /* secondary text on the tile's background: mixed toward the primary text color to reach 4.5:1 */
        .tg, .ch, .cr { font-size: 14px; color: color-mix(in srgb, var(--secondary-text-color) 45%, var(--primary-text-color)); margin-top: 3px; }
        .cr { font-style: italic; margin: 0 0 2px; }
      </style><ha-card lang="${gcLang()}"><div class="s" style="margin-top:0">${esc(intro)}</div>${table}<div class="grid" aria-hidden="true">${tiles}</div>` +
      `<div class="s" style="margin-top:8px">${gcSay(gcSentences([figures,
        b === "consensus" ? gcT("ui.core.note.change_consensus") : gcT("ui.core.note.change_custom")]))}</div></ha-card>`;
  }
  getCardSize() { return 5; }
}

// ------------------------------------------------------------------ collapsible section: further cards, one tap away
/**
 * A collapsible section holding further cards (custom:glucocraft-details).
 *
 * config: { title, cards: [card configs] } - GlucoCraft's own cards (custom:glucocraft-*) and Home Assistant's
 * built-in cards.
 * Closed by default; the open / closed state is remembered per page in this browser.
 */
class GlucoCraftDetails extends HTMLElement {
  /**
   * Build the section's button and its closed body; restore the remembered state.
   *
   * a real button at the bottom of the page ("Show All Statistics"), not a heading with a small arrow
   */
  setConfig(config) {
    this._cfg = config || {};
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    let open = false;
    try { open = localStorage.getItem(this.key()) === "1"; } catch { /* storage unavailable: closed */ }
    this.shadowRoot.innerHTML = `<style>
        :host { display: block; }
        .head { border-radius: var(--ha-card-border-radius, 12px); background: var(--ha-card-background, var(--card-background-color));
                padding: 16px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }   /* button left, text right */
        .sub { flex: 1 1 260px; font-size: 14px; color: var(--secondary-text-color); line-height: 1.45; }
        ${GC_BUTTON_CSS} button { padding: 10px 22px; }
        /* two columns where each can be 340 px wide; one column, as wide as the section, on a narrower screen (a
           320-px phone included), so that nothing scrolls sideways (WCAG 1.4.10) */
        .body { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(340px, 100%), 1fr)); gap: 8px; padding: 8px 0 0; }
        .body[hidden] { display: none; }
        .body > .wide { grid-column: 1 / -1; }
      </style><div class="head"><button type="button" aria-expanded="false" aria-controls="body"><ha-icon></ha-icon>` +
      // the section's title (written in the language in use by the dashboard) names the button until the catalogue's
      // "Show All Statistics" is loaded, so that it is never without a name
      `<span>${esc(this._cfg.button || this._cfg.title || "")}</span></button>` +
      `${this._cfg.subtitle ? `<div class="sub">${esc(this._cfg.subtitle)}</div>` : ""}</div>` +
      '<div class="body" id="body" hidden></div>';
    this._btn = this.shadowRoot.querySelector("button");
    this._body = this.shadowRoot.querySelector(".body");
    this._btn.addEventListener("click", () => this.toggle(this._body.hidden));
    this._kids = null;
    this.toggle(open, false);
  }
  // open or close the section; remember: keep the choice for the next visit (this browser)
  toggle(open, remember = true) {
    this._body.hidden = !open;
    this._btn.setAttribute("aria-expanded", open ? "true" : "false");
    this.label();
    this._btn.querySelector("ha-icon").setAttribute("icon", open ? "mdi:chevron-up" : "mdi:chevron-down");
    if (remember) { try { localStorage.setItem(this.key(), open ? "1" : "0"); } catch { /* storage unavailable: not kept */ } }
    if (open) this.build();
  }
  // the button's text ("Show All Statistics" / "Hide All Statistics", or the configured texts) and the section's
  // language, once the language catalogues are loaded
  label() {
    if (!this._hass || !gcI18nReady(this._hass)) return;
    const open = !this._body.hidden;
    this.shadowRoot.querySelector(".head").setAttribute("lang", gcLang());
    this._btn.querySelector("span").textContent = open ? this._cfg.button_hide || gcT("ui.details.hide")
      : this._cfg.button || gcT("ui.details.show");
  }
  key() { return `glucocraft-details:${location.pathname}:${this._cfg.title || ""}`; }
  /** Create the section's cards the first time it is opened. */
  async build() {   // the cards are made when the section is first opened (a closed section costs nothing)
    if (this._kids) return;
    this._kids = [];
    const body = this._body;
    const helpers = window.loadCardHelpers ? await window.loadCardHelpers() : null;
    for (const c of this._cfg.cards || []) {
      const slot = document.createElement("div");
      if (!String(c.type).includes("change-chart") && !c.half) slot.className = "wide";
      body.appendChild(slot);
      let el;
      if (String(c.type).startsWith("custom:glucocraft-")) {
        const tag = c.type.slice(7);
        await customElements.whenDefined(tag);
        el = document.createElement(tag);
        el.setConfig(c);
      } else if (helpers) el = helpers.createCardElement(c);
      if (!el) continue;
      if (this._hass) el.hass = this._hass;
      slot.appendChild(el);
      this._kids.push(el);
    }
  }
  set hass(hass) {
    this._hass = hass;
    if (gcI18nReady(hass)) this.label();
    else this._i18n ||= gcI18nLoad(hass).then(() => { this._i18n = null; this.label(); });
    (this._kids || []).forEach((k) => { k.hass = hass; });
  }
  getCardSize() { return 2; }
  getGridOptions() { return { columns: "full" }; }
}

// ------------------------------------------------------------------ daily mean, lowest and highest
/**
 * Each calendar day's mean, lowest and highest reading, on the bands of the ranges in use (custom:glucocraft-daily-range).
 * The days are calculated by GlucoCraft from the stored readings, unrounded, in Home Assistant's time zone
 * (/api/glucocraft/data?daily=N, report.daily_values): each mean weighs every reading by the time it represents (the
 * gap rule), as every other mean; the chart rounds each value once, as it shows it.
 */
class GlucoCraftDailyRange extends GlucoCraftHistory {
  // drawn again with each new reading, not every minute; the days are downloaded once an hour
  timed() { return false; }
  // days: a whole number from 1 to the days GlucoCraft keeps at most (RETENTION_DAYS; the data endpoint sends no more)
  optionProblem() {
    const d = this._cfg.days;
    const n = Number(d);
    return d == null || (Number.isInteger(n) && n >= 1 && n <= RETENTION_DAYS) ? null : ["days", d, `1–${RETENTION_DAYS}`];
  }
  /**
   * Return the chart's one-sentence description for screen readers.
   *
   * "...: daily means from 98 to 120 milligrams per deciliter; lowest 58 milligrams per deciliter, highest 212 milligrams
   * per deciliter." D: the days, {avg, lo, hi} as plotted (display unit).
   */
  describeDays(title, D) {
    const withData = D.filter((d) => isNum(d.avg));
    if (!withData.length) return gcT("ui.chart.no_readings", { title });
    const dd = isMmol() ? 1 : 0, shown = (v) => round(v, dd);
    const spoken = (v) => gcSpeak(shown(v), gU(), { d: dd, dec: GC.dec }), means = withData.map((d) => d.avg);
    // the lowest daily mean without its unit: "daily means from 98 to 120 milligrams per deciliter"
    return gcT("ui.daily.said", { title, from: gcSpeakNumber(shown(Math.min(...means)), dd, GC.dec), to: spoken(Math.max(...means)),
      lowest: spoken(Math.min(...withData.map((d) => d.lo))),
      highest: spoken(Math.max(...withData.map((d) => d.hi))) });
  }
  /**
   * Return the daily values of the last `days` days ({at, days, unit, daily}), null without them.
   *
   * Downloaded when there are none for these days and this display unit, or once they are an hour old; a failed
   * download is tried again a minute later, and the earlier values are drawn meanwhile (with the line "as of"); its
   * reason (the HTTP status, or why the answer could not be read) is logged once (gcNewFailure), not at every try.
   * Until the first download ends, box says that the chart is loading; without any values it says that the download
   * failed.
   */
  async dailyData(box, title, days) {
    const line = (key) => gcRender(this.shadowRoot, `<div class="s">${esc(gcT(key, { title }))}</div>`, box);
    const now = Date.now() / 1000, k = this._daily;
    const fresh = k && k.days === days && k.unit === gU() && now - k.at < 3600;
    if (!fresh && !this._dailyWait && !(this._dailyFailed && now - this._dailyFailed < 60)) {
      if (box.gcHtml === undefined) line("ui.chart.loading");
      this._dailyWait = (async () => {
        try {
          const r = await this._hass.fetchWithAuth(`/api/glucocraft/data?daily=${days}`, { cache: "no-cache" });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          const j = await r.json();
          this._daily = { at: now, days, unit: j.unit, daily: Array.isArray(j.daily) ? j.daily : [] };
          this._dailyFailed = 0;
          gcNewFailure(`daily|${days}`, null);
        } catch (e) {
          this._dailyFailed = now;
          const reason = String(e?.message || e);
          if (gcNewFailure(`daily|${days}`, reason)) {
            console.warn(`GlucoCraft: daily values of ${days} days not read (${reason}); tried again every minute`);
          }
        } finally {
          this._dailyWait = null;
        }
      })();
    }
    if (this._dailyWait) await this._dailyWait;
    const got = this._daily;
    if (!got || got.days !== days || got.unit !== gU()) { line("ui.chart.load_failed"); return null; }
    return got;
  }
  // the line under the chart drawn from earlier values because the latest download failed ("" otherwise)
  dailyAsOf(got) {
    if (!this._dailyFailed) return "";
    const at = new Date(got.at * 1000), time = sameDay(at, new Date()) ? clockStr(at) : `${dayShort(at)} ${clockStr(at)}`;
    return `<div class="s">${esc(gcT("ui.chart.as_of", { time }))}</div>`;
  }
  /** Draw the daily chart from GlucoCraft's daily values. */
  async draw() {
    if (!this._hass || this.optionError()) return;
    const days = this._cfg.days || 14, W = this.width(), H = this._cfg.height || 220;
    const title = this._cfg.title || gcT("ui.daily.title", { count: days });
    if (!this.shadowRoot.innerHTML) this.shadowRoot.innerHTML = this.css() + `<ha-card lang="${gcLang()}"><div class="g"></div></ha-card>`;
    this.shadowRoot.querySelector("ha-card")?.setAttribute("lang", gcLang());   // the language in use, at every drawing
    const box = this.shadowRoot.querySelector(".g");
    const got = await this.dailyData(box, title, days);
    if (!got) return;
    const r = this.ranges(this._cfg.basis), rd = gR(r);   // mg/dL for the key, display unit for drawing
    // the values in the display unit, unrounded
    const num = (v) => (isNum(v) ? v : NaN);
    // each day at its noon in Home Assistant's time zone (the zone of GlucoCraft's days), for its date label
    const D = got.daily.map((d) => ({ day: zoneDate(d.date), avg: num(d.mean), lo: num(d.min), hi: num(d.max) }));
    let mn = 999, mx = 0;
    D.forEach((d) => { if (isNum(d.avg)) { mn = Math.min(mn, d.lo); mx = Math.max(mx, d.hi); } });
    const [gmin, gmax] = this.fit(mn, mx, gCv(20));
    const y = (g) => Math.round(H - (Math.min(Math.max(g, gmin), gmax) - gmin) / (gmax - gmin) * H);
    const cw = W / days, cx = (i) => (i + 0.5) * cw;
    // the chart's one-sentence description for screen readers (ACCESSIBILITY.md rule 4)
    const said = this.describeDays(title, D);
    let svg = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" style="display:block;font-family:Roboto,sans-serif" role="img" aria-label="${esc(said)}"><rect width="${W}" height="${H}" fill="#0a0a0a"/>`;
    svg += this.bands(rd, y, W);
    const st = Math.max(isMmol() ? 0.5 : 10, this.step(gmin, gmax, 5));
    // the value labels in one format per axis (gcAxisText)
    for (let k = Math.ceil(gmin / st - 1e-9); k * st <= gmax + 1e-9; k++) {
      const g = k * st, gy = y(g);
      if (gy > 4 && gy < H - 22) {
        svg += `<rect y="${gy}" width="${W}" height="1" fill="#404040"/>` + this.t(4, gy - 16, gcAxisText(g, st), 14);
      }
    }
    const every = Math.max(1, Math.ceil(days * 48 / W));
    let path = "";
    D.forEach((d, i) => {
      if (i % every === 0) svg += this.tc(cx(i), H - 18, numDate(d.day), 14, W);
      if (!isNum(d.avg)) return;
      const bw = Math.max(3, Math.min(14, cw * 0.45));
      // light bar with a dark outline: readable on every color band
      svg += `<rect x="${cx(i) - bw / 2}" y="${y(d.hi)}" width="${bw}" height="${Math.max(2, y(d.lo) - y(d.hi))}" fill="#bdbdbd" stroke="#000000" stroke-width="1" rx="2"/>`;
      path += `${i && isNum(D[i - 1].avg) ? "L" : "M"}${cx(i).toFixed(1)} ${y(d.avg)}`;
    });
    svg += `<path d="${path}" fill="none" stroke="#000" stroke-width="4"/><path d="${path}" fill="none" stroke="#fff" stroke-width="2"/>` +
      D.map((d, i) => (isNum(d.avg) ? `<circle cx="${cx(i)}" cy="${y(d.avg)}" r="3.5" fill="#fff" stroke="#000" stroke-width="1"/>` : "")).join("") +
      "</svg>";
    const yours = this._cfg.basis === "yours";
    gcRender(this.shadowRoot, `<style>.gh{margin-bottom:8px}.k{display:inline-flex;align-items:center;gap:4px;margin:4px 10px 0 0;font-size:14px;color:var(--secondary-text-color);white-space:nowrap}.k i{width:12px;height:12px;border-radius:2px;opacity:.75}</style>` +
      `<div class="gh"><div class="t">${esc(title)}</div>` +
      `<div class="s">${gcT("ui.daily.how")}</div>` + this.dailyAsOf(got) +
      `<div class="s">${gcSay(gcBands(yours))}</div>` +
      `<div>${this.bandKey(r)}</div></div>` + svg, box);
  }
  getCardSize() { return 6; }
}

// ------------------------------------------------------------------ the Statistics page (Consensus Ranges, All Statistics)
// The last reading, the seven-period table grouped Consensus / Supplementary / Observational with target, reference and
// note per row and the time per day, and the data-sufficiency warnings; always on the consensus ranges. ✓ / ✗ only at a
// cited consensus target, from 14 days with at least 70% sensor active (Battelino et al., 2019); measures without a
// consensus target (mean glucose, GMI, standard deviation, GRI) are Supplementary; GMI from 14 days. No verdicts
// relative to a target, no clinical-context notes and no projection.
const SP = ["6h", "24h", "7d", "14d", "30d", "90d", "180d"];
// [section, key in *_consensus_ranges, label, unit, target [op, v] | null, reference, note, decimals]; the labels in the
// display unit
const statRows = () => [
  ["consensus", "sensor_active", gcT("ui.label.sensor_active"), "%", [">=", 70], gcT("ui.note.target.sensor_active"),
    gcT("ui.statistics.note.sensor_active"), 0],
  ["consensus", "time_70_180", gcT("ui.measure.time_in_range", { range: gr(70, 180), unit: gU() }), "%", gcConsTarget("time_in_range"),
    gcT("ui.note.target.time_70_180"), "", 1],
  ["consensus", "time_below_70", gcT("ui.statistics.row.time_below", { value: gs(70), unit: gU() }), "%", gcConsTarget("time_low"),
    gcT("ui.note.target.time_below_70"), gcT("ui.statistics.note.levels_1_2"), 1],
  ["consensus", "time_below_54", gcT("ui.statistics.row.time_below", { value: gs(54), unit: gU() }), "%", gcConsTarget("time_very_low"),
    gcT("ui.note.target.time_below_54"), gcT("ui.statistics.note.level_2"), 1],
  ["consensus", "time_above_180", gcT("ui.statistics.row.time_above", { value: gs(180), unit: gU() }), "%", gcConsTarget("time_high"),
    gcT("ui.note.target.time_above_180"), gcT("ui.statistics.note.levels_1_2"), 1],
  ["consensus", "time_above_250", gcT("ui.statistics.row.time_above", { value: gs(250), unit: gU() }), "%", gcConsTarget("time_very_high"),
    gcT("ui.note.target.time_above_250"), gcT("ui.statistics.note.level_2"), 1],
  ["consensus", "cv", gcT("ui.measure.cv"), "%", gcConsTarget("cv"), gcT("ui.note.target.cv"), gcT("ui.statistics.note.cv"), 1],
  ["supplementary", "time_70_140", gcT("ui.measure.time_in_tight_range", { range: gr(70, 140), unit: gU() }), "%", null,
    gcT("ui.statistics.reference.tight_range"), gcT("ui.statistics.note.tight_range"), 1],
  ["supplementary", "mean", gcT("ui.measure.mean_unit", { unit: gU() }), "G", null, "", gcT("ui.statistics.note.mean"), 0],
  ["supplementary", "gmi", gcT("ui.measure.gmi"), "A1C", null, gcT("ui.statistics.reference.gmi"), gcT("ui.statistics.note.gmi"), 1],
  ["supplementary", "std_dev", gcT("ui.measure.standard_deviation_unit", { unit: gU() }), "G", null, "", gcT("ui.statistics.note.std_dev"), 0],
  ["supplementary", "gri", gcT("ui.measure.gri"), "", null, gcT("ui.statistics.reference.gri"), gcT("ui.statistics.note.gri"), 0],
  ["observational", "min_value", gcT("ui.statistics.row.min", { unit: gU() }), "G", null, "", "", 0],
  ["observational", "max_value", gcT("ui.statistics.row.max", { unit: gU() }), "G", null, "", "", 0],
  ["observational", "readings", gcT("ui.statistics.row.readings"), "", null, "", "", 0],
  ["observational", "limit_readings", gcT("ui.statistics.row.limit_readings"), "", null, "", gcT("ui.statistics.note.limit_readings"), 0],
];
// the zone words of the last reading (as the display cards), where the reading carries no zone_label of its own
const GC_ZONE_WORD = { very_low: "ui.zone.very_low", low: "ui.zone.low", near_low: "ui.zone.in_range", tight_range: "ui.zone.tight_range",
  near_high: "ui.zone.in_range", high: "ui.zone.high", very_high: "ui.zone.very_high" };
// the same words inside a spoken sentence (their own texts: a language may write them otherwise there)
const GC_ZONE_SAID = { very_low: "ui.zone_in_sentence.very_low", low: "ui.zone_in_sentence.low",
  near_low: "ui.zone_in_sentence.in_range", tight_range: "ui.zone_in_sentence.tight_range",
  near_high: "ui.zone_in_sentence.in_range", high: "ui.zone_in_sentence.high", very_high: "ui.zone_in_sentence.very_high" };
// the styles of the Statistics card, after the cards' shared styles (GCBase.css)
const GC_STATISTICS_STYLE = `<style>
      .cur div { display: flex; justify-content: space-between; gap: 12px; padding: 4px 0; border-bottom: 1px solid var(--divider-color); font-size: 14px; }
      .cur span:last-child { font-weight: 600; text-align: right; }
      .nc { border-left: 6px solid ${STATUS.alert}; padding: 6px 10px; margin: 4px 0 6px; font-size: 14px; font-weight: 600;
            background: color-mix(in srgb, ${STATUS.alert} 10%, var(--ha-card-background, var(--card-background-color))); }
      .chip { display: inline-block; padding: 0 6px; border-radius: 4px; font-weight: 700; }
      .h { font-size: 15px; font-weight: 600; margin: 16px 0 4px; color: var(--primary-text-color); }
      .x { font-size: 14px; color: var(--secondary-text-color); line-height: 1.45; margin-bottom: 8px; }
      .tw { overflow-x: auto; }
      table { width: 100%; border-collapse: collapse; font-size: 14px; min-width: 700px; }
      th { padding: 6px 4px; font-size: 14px; border-bottom: 2px solid var(--divider-color); text-align: center; }
      th:first-child { text-align: left; min-width: 200px; }
      td, th[scope="row"] { text-align: center; padding: 5px 4px; border-bottom: 1px solid var(--divider-color); vertical-align: top; color: var(--primary-text-color); }
      th[scope="row"] { font-weight: 400; }
      .l { text-align: left !important; } .o, .r { color: var(--secondary-text-color); } .r, .nt, .d { font-size: 14px; }
      .nt { color: var(--secondary-text-color); font-style: italic; line-height: 1.3; } .d { color: var(--secondary-text-color); }
      .sec { text-align: left; font-weight: 600; background: var(--secondary-background-color); padding: 8px 4px 4px; }
      /* secondary text on the secondary background: mixed toward the primary text color to reach 4.5:1 */
      .sec div { font-weight: 400; font-size: 14px; color: color-mix(in srgb, var(--secondary-text-color) 45%, var(--primary-text-color)); }
      ${GC_WARN_CSS}
    </style>`;
// the row header of a statistics row (statRows): the measure, its reference and its note
const gcStatRowHead = (row) => {
  const [, , lab, , , ref, note] = row;
  return `<th scope="row" class="l">${gcSay(lab)}` +
    `${ref ? `<div class="r">${gcSay(ref)}</div>` : ""}${note ? `<div class="nt">${gcSay(dtx(note))}</div>` : ""}</th>`;
};
/** The statistics of the Consensus Ranges page (custom:glucocraft-statistics). */
class GlucoCraftStatistics extends GCBase {
  watch() { return [ENT.config, ENT.current, ENT.source, ENT.nocur, ...SP.map((p) => `sensor.glucocraft_${p}_consensus_ranges`)]; }
  timed() { return true; }   // the age of the last reading and the time until the sensor expires
  v(p, k) { const a = this.st(`sensor.glucocraft_${p}_consensus_ranges`)?.attributes; return a && a.has_data && isNum(a[k]) ? a[k] : NaN; }
  // a period whose values are compared with the targets: 14 days or more, with >= 70% sensor active, compared before
  // rounding
  ok14(p) { return PERIOD_DAYS[p] >= 14 && this.activeOk(p); }
  /**
   * Return the last reading: { current: its rows [label, value, color, value is HTML], said: the sentences of its
   * spoken form, stale: no current data, at: its measurement time (or null), untrusted: that time is ahead of the
   * clock, when: a time as the rows show it }.
   *
   * The last reading, with its measurement time and age; while there is no current data the block says so first (a
   * stored value is never presented as current). A measurement time ahead of the clock (time_untrusted) is not shown,
   * nor an age: the value alone, as on the display cards. A reading outside the sensor's range: LO / HI on a chip in the
   * very-low / very-high color (white or black text, whichever reads better), never the stored limit value.
   */
  lastReading() {
    const g = (e) => { const s = this.st(e); return !s || ["unknown", "unavailable"].includes(s.state) ? "---" : s.state; };
    // an enum sensor's state in words: Home Assistant's own state name in its language (formatEntityState)
    const word = (e) => { const s = this.st(e); return g(e) === "---" || !this._hass.formatEntityState ? g(e) : this._hass.formatEntityState(s); };
    // glucose states are converted by Home Assistant and not rounded: mg/dL whole numbers, mmol/L one decimal
    const gq = (e) => { const v = parseFloat(this.st(e)?.state); return isNaN(v) ? "---" : isMmol() ? nf(v, 1) : String(round(v)); };
    const exp = this.attr(ENT.expiry, "expires_at") ? new Date(this.attr(ENT.expiry, "expires_at")) : null;
    const lim = gcLimit(this.st(ENT.current)), stale = this.st(ENT.nocur)?.state === "on";   // GlucoCraft's flag
    const lastIso = this.st(ENT.last)?.state, at = lastIso && !isNaN(Date.parse(lastIso)) ? new Date(lastIso) : null;
    // a reading time ahead of the clock (time_untrusted): neither that time nor an age, as on the displays
    const untrusted = this.attr(ENT.last, "time_untrusted") === true, shownAt = untrusted ? null : at;
    const ageMin = shownAt ? (Date.now() - shownAt) / 60000 : NaN;
    const when = (d) => (sameDay(d, new Date()) ? clockStr(d) : `${dayShort(d)} ${clockStr(d)}`);
    const cur = this.st(ENT.current)?.attributes || {}, zone = cur.zone;
    // the zone word of every zone (as the display cards), only while the data are current
    const zoneWord = GC_ZONE_WORD[zone] && !stale
      ? (typeof cur.zone_label === "string" && cur.zone_label ? cur.zone_label : gcT(GC_ZONE_WORD[zone])) : "";
    const chip = lim ? `<span class="chip" style="background:${lim.color};color:${onColorHex(lim.color)}">${esc(lim.text)}</span>` : "";
    // the value (or LO / HI), its zone word and its measurement time
    const value = !lim ? `${esc(gq(ENT.current))} ${gU()}` : lim.limit === "low"
      ? gcT("ui.statistics.limit_below", { chip }) : gcT("ui.statistics.limit_above", { chip });
    const zw = esc(zoneWord), time = shownAt ? esc(when(shownAt)) : "", age = shownAt ? esc(dur(ageMin)) : "";
    const reading = shownAt ? (zoneWord ? gcT("ui.statistics.reading.zone_measured", { value, zone: zw, time, age })
      : gcT("ui.statistics.reading.measured", { value, time, age })) : zoneWord ? gcT("ui.statistics.reading.zone", { value, zone: zw }) : value;
    const d5 = this.attr("sensor.glucocraft_5_minute_delta", "value_mgdl");
    const current = [
      [gcT("ui.statistics.last_glucose_reading"), reading, null, true],
      // the 5-minute delta from GlucoCraft's unrounded value in mg/dL, rounded once in the display unit with its sign
      // ("+5", "-0.3", "0"), as the display card and the panels show it (glucocraft-shared.js gDelta)
      [gcT("ui.statistics.delta_5_min"), `${isNum(d5) ? gDelta(d5) : GC_NO_VALUE} ${gU()}`],
      // the arrow is shown; screen readers read the trend word beside it
      [gcT("ui.statistics.trend_arrow"), `<span aria-hidden="true">${esc(g("sensor.glucocraft_trend_arrow"))}</span> ` +
        esc(this.attr("sensor.glucocraft_trend_arrow", "description") || ""), null, true],
      [gcT("ui.statistics.source"), g(ENT.source)], [gcT("ui.statistics.data_history"), dtx(word("sensor.glucocraft_data_history"))],
      [gcT("ui.statistics.sensor_expiry"), !this.cfg().expiry_entity ? gcT("ui.statistics.expiry_not_set") : exp
        ? gcT("ui.statistics.expiry", { date: dayShort(exp), time: clockStr(exp), remaining: dur((exp - new Date()) / 60000) }) : word(ENT.expiry)],
    ];
    // the rule-4 sentence for screen readers: the data status (only when there is a problem), the value with its unit
    // and zone word, the time of the reading, the trend (ACCESSIBILITY.md rule 4)
    const said = [];
    if (stale) {
      said.push(shownAt && isNum(ageMin) ? gcT("ui.statistics.said.no_data_since", { age: gcSpeakAge(ageMin) })
        : gcT("ui.statistics.said.no_data"));
    }
    const valNum = parseFloat(this.st(ENT.current)?.state), zoneSaid = zoneWord ? gcT(GC_ZONE_SAID[zone]) : "";
    const glucose = lim ? gcSpeakLimit(lim.limit) : isNum(valNum)
      ? gcSpeak(valNum, gU(), { d: isMmol() ? 1 : 0, dec: GC.dec }) : "";
    if (glucose) said.push(zoneSaid ? gcT("ui.statistics.said.glucose_zone", { glucose, zone: zoneSaid }) : gcT("ui.statistics.said.glucose", { glucose }));
    if (!stale && shownAt && isNum(ageMin)) {   // a reading less than a whole minute old: "Measured less than a minute ago."
      said.push(ageMin < 1 ? gcT("ui.statistics.said.measured_now") : gcT("ui.statistics.said.measured", { age: gcSpeakAge(ageMin) }));
    }
    const trendWord = gcSpeakTrend(this.attr("sensor.glucocraft_trend_arrow", "description"));
    if (!stale && trendWord) said.push(gcT("ui.statistics.said.trend", { trend: gcCap(trendWord) }));
    return { current, said, stale, at, untrusted, when };
  }
  /** Return one table cell: the value, its mark, the time per day and the GRI components. */
  cell(row, p) {
    const [, key, , unit, target, , , dec] = row;
    if (key === "limit_readings") {   // a count and a time, not a number attribute: "0" or "2 LO, 1 HI (LO 10 min · HI 5 min)"
      const a = this.st(`sensor.glucocraft_${p}_consensus_ranges`)?.attributes;
      if (!a || !a.has_data || !a.limit_readings) return `<td>${gcSay(GC_NO_VALUE)}</td>`;
      const [n] = gcLimitCount(a.limit_readings);
      const [lr, ls] = [a.limit_readings, a.limit_seconds];
      return `<td><b>${n ? gcSay(gcLimitCountTime(lr, ls), gcLimitCountSaid(lr, ls)) : "0"}</b></td>`;
    }
    const val = this.v(p, key);
    if (!isNum(val)) return `<td>${gcSay(GC_NO_VALUE)}</td>`;
    let mark = "", col = "";
    if (target && this.ok14(p)) {
      const m = gcMeets(target[0], val, target[1]); col = m ? STATUS.ok : STATUS.alert;
      mark = m ? ` <span aria-hidden="true">✓</span><span class="sr">${gcT("ui.result.meets")}</span>`
        : ` <span aria-hidden="true">✗</span><span class="sr">${gcT("ui.result.does_not_meet")}</span>`;
    }
    // GMI in the A1C unit setting (percent, mmol/mol or both)
    const txt = (unit === "A1C" ? gmiTxt(val, this.gmiMmol(p, false)) : unit === "%" ? (target ? pcAt(val, target[1], dec) : pc(val, dec))
      : unit === "G" ? gs(val) : dec ? nf(val, dec) : n0(val));
    const day = unit === "%" && key.startsWith("time_") && PERIOD_DAYS[p] >= 1
      ? `<div class="d">${gcSay(perDayTable(val), gcT("ui.per_day", { duration: gcSpeakAge(round(val / 100 * 1440)) }))}</div>` : "";
    // the GRI with its two components, displayed separately (Klonoff et al., 2023); the row's note names them
    const parts = key === "gri" ? gcGriParts(this.v(p, "gri_hypo"), this.v(p, "gri_hyper")) : "";
    const comp = parts ? `<div class="d">${parts}</div>` : "";
    return `<td style="${col ? `color:${stc(col)}` : ""}"><b>${gcSay(txt)}</b>${mark}${day}${comp}</td>`;
  }
  /** Draw the last reading and the seven-period table. */
  draw() {
    const { current, said, stale, at, untrusted, when } = this.lastReading();
    const rows = statRows();
    const group = (name, title, sub) => `<tr><th scope="colgroup" colspan="${SP.length + 1}" class="sec">${gcSay(title)}<div>${esc(sub)}</div></th></tr>` +
      rows.filter((r) => r[0] === name).map((r) => `<tr>${gcStatRowHead(r)}${SP.map((p) => this.cell(r, p)).join("")}</tr>`).join("");
    // the 70% rule, one note per period
    const warnings = gcDataWarnings(SP.map((p) => [p, this.rawSummary(p, false)]));
    const custom = this.cfg().ranges && Object.keys(AGP).some((k) => this.cfg().ranges[k] !== AGP[k]);
    const [limitShown, limitSaid] = limitNoteFor(this, SP);
    const limitNote = limitShown || gcT("ui.statistics.limit_note");
    // Two parts, each drawn on its own (gcRender): the last reading, whose age changes every minute, and the period
    // statistics, which change only with new values; so the minute leaves the table in place (its sideways scroll
    // position on a phone, a screen reader's position in it). The frame holds the styles and the two boxes; it is
    // written again only when it changes (the language of the texts).
    const root = this.shadowRoot;
    const frame = this.css() + GC_STATISTICS_STYLE + `<ha-card lang="${gcLang()}"><div class="lr"></div><div class="pt"></div></ha-card>`;
    if (root.gcFrame !== frame) { root.innerHTML = frame; root.gcFrame = frame; }
    gcRender(root, `<h3 class="h" style="margin-top:0">${gcT("ui.statistics.last_reading")}</h3>
      <div class="sr">${esc(gcSentences(said))}</div>
      ${stale && at ? `<div class="nc" aria-hidden="true">${untrusted ? gcT("ui.statistics.no_data")
        : gcT("ui.statistics.no_data_since", { time: esc(when(at)) })}</div>` : ""}
      <div class="x">${gcT("ui.statistics.last_reading_note")}</div>
      <div class="cur">${current.map(([a, b, c, html], i) => `<div${i === 0 && said.length ? ' aria-hidden="true"' : ""}><span>${esc(a)}</span><span${c ? ` style="color:${c}"` : ""}>${html ? b : gcSay(b)}</span></div>`).join("")}</div>`,
    root.querySelector(".lr"));
    gcRender(root, `<h3 class="h" id="ps">${gcT("ui.statistics.period_statistics")}</h3>
      <div class="x">${gcSay(custom ? gcT("ui.statistics.periods_note.custom", { minutes: nf(14.4, 1) })
        : gcT("ui.statistics.periods_note.consensus", { minutes: nf(14.4, 1) }))}</div>
      ${warnings}
      <div class="tw" tabindex="0" role="region" aria-labelledby="ps"><table aria-labelledby="ps"><thead><tr><th scope="col">${gcT("ui.statistics.metric")}</th>${SP.map((p) => `<th scope="col">${gcSay(periodName(p))}</th>`).join("")}</tr></thead><tbody>
        ${group("consensus", gcT("ui.statistics.group.consensus"), gcT("ui.statistics.group.consensus_note"))}
        ${group("supplementary", gcT("ui.statistics.group.supplementary"), gcT("ui.statistics.group.supplementary_note"))}
        ${group("observational", gcT("ui.statistics.group.observational"), gcT("ui.statistics.group.observational_note"))}
      </tbody></table></div>
      <div class="x" style="margin-top:10px">${gcSay(gcSentences([limitNote, gcT("ui.statistics.gap_note")]),
        gcSentences([limitSaid || limitNote, gcT("ui.statistics.gap_note")]))}</div>
      <div class="x" style="margin-top:14px;font-style:italic">${gcT("ui.statistics.attribution")}</div>`,
    root.querySelector(".pt"));
  }
  getCardSize() { return 14; }
}

const defs = [["glucocraft-banners", GlucoCraftBanners], ["glucocraft-readout", GlucoCraftReadout],
  ["glucocraft-stats-table", GlucoCraftStatsTable], ["glucocraft-history", GlucoCraftHistory],
  ["glucocraft-change", GlucoCraftChange], ["glucocraft-change-chart", GlucoCraftChangeChart], ["glucocraft-core", GlucoCraftCore], ["glucocraft-details", GlucoCraftDetails], ["glucocraft-settings-button", GlucoCraftSettingsButton], ["glucocraft-export", GlucoCraftExport], ["glucocraft-maintenance-issues", GlucoCraftMaintenanceIssues], ["glucocraft-statistics", GlucoCraftStatistics], ["glucocraft-daily-range", GlucoCraftDailyRange],
  ["glucocraft-pages", GlucoCraftPages], ["glucocraft-start-pair", GlucoCraftStartPair], ["glucocraft-custom-diff", GlucoCraftCustomDiff], ["glucocraft-footer", GlucoCraftFooter]];
for (const [name, cls] of defs) if (!customElements.get(name)) customElements.define(name, cls);   // loaded twice: no error
// The card picker's entries (the file is loaded twice: added once), with their texts in the frontend's language: each
// card by its name (ui.card_picker.<id>) and what it shows (ui.card_picker.description.<id>); the options are in
// docs/DASHBOARD.md. The language comes from the hass object of Home Assistant's <home-assistant> element, an
// undocumented property, read only here because the entries are made before any card receives hass; without it, the
// language of the page (<html lang>).
const GC_PICKER = [["glucocraft-banners", "banners"], ["glucocraft-readout", "readout"],
  ["glucocraft-stats-table", "stats_table"], ["glucocraft-history", "history"], ["glucocraft-change", "change"],
  ["glucocraft-change-chart", "change_chart"], ["glucocraft-core", "core"], ["glucocraft-details", "details"],
  ["glucocraft-settings-button", "settings_button"], ["glucocraft-export", "export"],
  ["glucocraft-statistics", "statistics"], ["glucocraft-daily-range", "daily_range"], ["glucocraft-pages", "pages"],
  ["glucocraft-start-pair", "start_pair"]];
gcI18nLoad(globalThis.document?.querySelector?.("home-assistant")?.hass).then(() => {
  window.customCards = window.customCards || [];
  for (const [type, id] of GC_PICKER) {   // loaded twice: registered once
    if (!window.customCards.some((c) => c.type === type)) {
      window.customCards.push({ type, name: gcT(`ui.card_picker.${id}`), description: gcT(`ui.card_picker.description.${id}`) });
    }
  }
});
console.info(`%c GLUCOCRAFT-UI %c v${GCUI_VERSION} `, "background:#7E57C2;color:#fff", "background:#000;color:#fff");
