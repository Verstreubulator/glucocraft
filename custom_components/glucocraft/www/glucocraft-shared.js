// License: AGPL-3.0-only (LICENSE).
/*
 * glucocraft-shared - the rules that GlucoCraft's cards and pages share, written once (docs/DEVELOPER_GUIDE.md section
 * 5): the settings in use and the number, glucose, time and date formats; the GMI formula and the consensus targets;
 * the spoken text (docs/ACCESSIBILITY.md rule 4); links inside the dashboard; content and focus across redraws
 * (gcRender); the gap rule; the graphs' scale and their minimum-to-maximum areas; the default colors and black or white
 * text on a color; the GLU2 parser (version 3 and later) and the downloads of GlucoCraft's files.
 *
 * Use: each card file imports this module itself, as it imports glucocraft-i18n.js (the same pattern, a path next to
 * its own URL, so that it works below the versioned path of GlucoCraft's dashboard and below the unversioned path of a
 * resource added by hand), and waits for it before it defines its elements:
 *   const GC_SHARED = globalThis.glucocraftShared ? Promise.resolve() : import(new URL("./glucocraft-shared.js", import.meta.url).href);
 *   await GC_SHARED;
 *   const { esc, nf, ... } = globalThis.glucocraftShared;
 * The module defines globalThis.glucocraftShared once (a second load, from another card file or another path, keeps
 * the first), so every card file works on its own and any number of them work together. It reads its texts from the
 * language module (glucocraftI18n.t) only when a function is called, after the card has loaded the catalogues.
 *
 * Each rule with a Python counterpart is a copy of the rule named beside it (units.py, gaps.py, spoken.py,
 * contrast.py); tests/test_card_shared_copies.py, test_frontend_consistency.py (gap rule), test_accessibility.py
 * (spoken text) and test_panel_contrast_rule.py (contrast) compare them.
 */
const GCSHARED_VERSION = "0.0.1";

if (!globalThis.glucocraftShared) {
  const gcT = (key, params) => globalThis.glucocraftI18n.t(key, params);

  // ---------------------------------------------------------------- the settings in use
  // The display settings, from GlucoCraft's configuration sensor (sensor.glucocraft_configuration, resolved values;
  // regional.py): the decimal separator ("." or ","), the A1C / GMI unit ("percent", "mmol_mol", "both"), the time and
  // date formats ("12" / "24", "MDY" / "DMY" / "YMD") and the display unit (mmol: true for mmol/L). Display only: never
  // applied to stored data, attributes, the API or parsing.
  // One object for every card and page of the browser tab: GlucoCraft allows one installation (single_config_entry),
  // so every card reads the same settings; each card writes the values it reads before it draws, and a card given
  // entity overrides with another unit would share this unit with the others.
  const settings = { dec: ".", a1c: "percent", time: "12", date: "MDY", mmol: false };
  // the regional settings from the configuration attributes (or the report data); a value that is missing (GlucoCraft
  // reloading) keeps the one in use
  const applyRegional = (ca) => {
    if (ca?.decimal_separator === "," || ca?.decimal_separator === ".") settings.dec = ca.decimal_separator;
    if (["percent", "mmol_mol", "both"].includes(ca?.a1c_unit)) settings.a1c = ca.a1c_unit;
    if (ca?.time_format) settings.time = ca.time_format;
    if (ca?.date_format) settings.date = ca.date_format;
  };

  // ---------------------------------------------------------------- numbers (units.py)
  // Rounding: halves away from zero, once, where a value is shown (units.round_half_up; Math.round rounds -2.5 to -2
  // and toFixed rounds the binary value, 1.005 to 1.00). The 1e-9 is that of units.round_half_up, for the binary error
  // of a decimal half; it changes no float32 value of a GLU2 file (panel_feed._pct32 writes each percentage as the
  // float32 that rounds as its unrounded value), so those are rounded as they are, without an epsilon of their own.
  // A value that rounds to zero is +0: no text shows "-0" or "-0.0".
  const round = (x, d = 0) => { const m = 10 ** d; return Math.sign(x) * Math.floor(Math.abs(x) * m + 0.5 + 1e-9) / m + 0; };
  // A glucose value in mmol/L to one decimal from a file's float32 mg/dL value only (a double is rounded by round):
  // 1e-5 absorbs the float32 error (at most 8.5e-6 of a tenth up to 500 mg/dL), so that the exact halves 112.6 and
  // 337.8 mg/dL (6.25 and 18.75 mmol/L) round up as the backend rounds them from its double: the same arithmetic as the
  // panels' gc_units.h r1_mmol (R1_FLOAT_EPS)
  const rh1 = (x) => Math.sign(x) * Math.floor(Math.abs(x) * 10 + 0.5 + 1e-5) / 10 + 0;
  // THE number formatter: v rounded once to d decimals, with the decimal separator in use
  const nf = (v, d = 1) => { const s = round(+v, d).toFixed(d); return settings.dec === "," ? s.replace(".", ",") : s; };
  // the same separator in a text that already holds numbers (axis labels, entity states, fixed texts). A number that
  // is a name, not a value, keeps its point: the locator of a cited "Table" or "Recommendation" (after a space or a
  // no-break space, "Table 6.2", "Recommendation 7.12"), a guideline or license version ("WCAG 2.2", "v3.0",
  // "AGPL-3.0", "License 2.0", "Version 1.1"), a release ("2026.10") and a version of three parts ("2.1.5")
  // (word boundaries in Unicode: a letter of any language next to a name counts as part of its word)
  const WB = String.raw`(?<![\p{L}\p{N}_])`, WE = String.raw`(?![\p{L}\p{N}_])`;
  const DTX_KEEP = new RegExp(String.raw`((?:Table|Recommendation)[ \u00a0]\d+\.\d|${WB}WCAG\)?[ \u00a0]\d+\.\d|` +
    String.raw`${WB}v\d+\.\d|${WB}[A-Z]+-\d+\.\d|${WB}(?:License|Version)[ \u00a0]\d+\.\d|${WB}\d+\.\d+\.\d+|` +
    String.raw`${WB}20\d\d\.\d{1,2}${WE})|(\d)\.(\d)`, "gu");
  const dtx = (s) => (settings.dec === "," ? String(s).replace(DTX_KEEP, (m, kept, a, b) => kept || `${a},${b}`)
    : String(s));
  // a text with its first letter in upper case (a whole character, also outside the BMP)
  const gcCap = (t) => { const [c = "", ...rest] = Array.from(String(t)); return c.toUpperCase() + rest.join(""); };
  const EDGE_DECIMALS = 2;   // the decimals of a value that one decimal would show on the wrong side of its rule
  // The decimals of a value shown next to a rule it is compared with (a target, a goal, the 70% of sensor active):
  // [shown, decimals]. Rounded once to d decimals; where that shows the rule's value although the value is on the other
  // side of it (69.96% beside "at least 70%"), two decimals (69.96%), and where two decimals still show the rule's
  // value, the last decimal moved to the value's side (69.996% -> 69.99%, 70.004% -> 70.01%). The same as
  // units.shown_against. GlucoCraft rule. rules: one rule value or several.
  const shownAgainst = (value, rules, d = 1) => {
    const list = (Array.isArray(rules) ? rules : [rules]).map(Number).filter((r) => isFinite(r));
    let shown = round(value, d);
    if (!list.some((r) => shown === r && r !== value)) return [shown, d];
    const places = Math.max(EDGE_DECIMALS, d + 1), step = 10 ** -places;
    shown = round(value, places);
    for (const r of list) if (shown === r && r !== value) shown = round(value < r ? r - step : r + step, places);
    return [shown, places];
  };
  // the text of a value next to a rule (shownAgainst), with the decimal separator in use
  const nfAt = (value, rules, d = 1) => { const [shown, places] = shownAgainst(value, rules, d); return nf(shown, places); };

  // ---------------------------------------------------------------- glucose (units.py)
  // GlucoCraft calculates in mg/dL; values are shown in the display unit: mg/dL whole numbers, mmol/L one decimal,
  // 1 mmol/L = 18.016 mg/dL, the consensus thresholds as published (Battelino et al., 2019; tight range: Battelino et
  // al., 2023)
  const MGDL_PER_MMOLL = 18.016;
  const CONS_MMOLL = { 54: 3.0, 70: 3.9, 140: 7.8, 180: 10.0, 250: 13.9 };
  // The largest glucose value GlucoCraft stores (mg/dL; limits.DEFAULT_UPPER, the widest upper limit of a CGM system):
  // a reading of a file above it, or of 0, is no reading
  const GLU_MAX = 500;
  const isMmol = () => settings.mmol;
  const gU = () => (settings.mmol ? "mmol/L" : "mg/dL");
  // A value (mg/dL) in the display unit, as a number: mmol/L rounded to one decimal (the thresholds as published),
  // mg/dL unchanged (rounded where it is written). gD rounds a double (an attribute, the report data, a value
  // calculated here) as GlucoCraft does (round); gD32 a GLU2 file's float32 value (rh1, as the panels).
  const inUnit = (r1) => (mg) => (!settings.mmol ? mg : Number.isInteger(+mg) && CONS_MMOLL[+mg] !== undefined
    ? CONS_MMOLL[+mg] : r1(mg / MGDL_PER_MMOLL));
  const gD = inUnit((x) => round(x, 1)), gD32 = inUnit(rh1);
  // a value (mg/dL) as text in the display unit; none: the text for no value. gF for a double, gF32 for a file's value.
  const asText = (disp) => (mg, none = "---") => (mg === null || mg === undefined || isNaN(mg) ? none
    : settings.mmol ? nf(disp(+mg), 1) : String(round(+mg)));
  const gF = asText(gD), gF32 = asText(gD32);
  // A graph axis label: one format per axis, as many decimals as the axis's step has ("7.0" / "6.5" / "6.0" at 0.5,
  // "4" / "6" / "8" at 2, "6.25" at 0.25), never a decimal on some labels of an axis only; with the decimal separator
  // in use. The same rule on every graph: the display card, the dashboard and the CGM Report page use gcAxisText, the
  // panels their copy (gc_units.h axis_label).
  const gcAxisDecimals = (step) => (Math.abs(step - Math.round(step)) < 1e-9 ? 0
    : Math.abs(step * 10 - Math.round(step * 10)) < 1e-9 ? 1 : 2);
  const gcAxisText = (value, step) => nf(value, gcAxisDecimals(step));
  // The 5-minute delta (GlucoCraft's unrounded change, mg/dL) as shown: rounded once in the display unit (whole mg/dL
  // or 0.1 mmol/L, halves away from zero), its value and its sign from the same number; a change that rounds to zero
  // without a sign, "0" / "0.0" (a sign says a direction, and zero has none; never "-0" or "-0.0"), as on the panels
  // (gc_units.h fmt_delta). deltaShown: the number shown; gDelta: its text ("+5", "-0.3", "0"). A change, not a glucose
  // value: the consensus thresholds of gD do not apply to it.
  const deltaShown = (mg) => (settings.mmol ? round(mg / MGDL_PER_MMOLL, 1) : round(mg));
  const gDelta = (mg) => {
    const d = deltaShown(mg), sign = d > 0 ? "+" : d < 0 ? "-" : "";
    return sign + (settings.mmol ? nf(Math.abs(d), 1) : String(Math.abs(d)));
  };
  // The GMI formula (Bergenstal et al., 2018) in its two forms, as units.py has them: GMI (%) = 3.31 + 0.02392 x mean
  // glucose in mg/dL, and GMI (mmol/mol) = 12.71 + 4.70587 x mean glucose in mmol/L. The one copy in the cards; the
  // strategy's Jinja macro a1 writes the same numbers, because a template cannot read them from here.
  const GMI = Object.freeze({ pctOffset: 3.31, pctSlope: 0.02392, mmolOffset: 12.71, mmolSlope: 4.70587 });
  // GMI in mmol/mol, as GlucoCraft's backend (units.gmi_mmol_mol): from the mean glucose with the mmol/mol form of the
  // formula, whole numbers, halves up; where only the GMI in % is at hand, the mean it was calculated from is used
  // (gmiPctToMmol)
  const gmiMmolFromMean = (mg) => (typeof mg === "number" && isFinite(mg) && mg > 0
    ? Math.floor(GMI.mmolOffset + GMI.mmolSlope * mg / MGDL_PER_MMOLL + 0.5 + 1e-9) : NaN);
  const gmiPctToMmol = (pct) => GMI.mmolOffset + GMI.mmolSlope * ((pct - GMI.pctOffset) / GMI.pctSlope) / MGDL_PER_MMOLL;
  // the inverse of gmiPctToMmol: the GMI in % of a value in mmol/mol (a grid in mmol/mol drawn on an axis in %)
  const gmiMmolToPct = (mm) => GMI.pctOffset + GMI.pctSlope * ((mm - GMI.mmolOffset) / GMI.mmolSlope * MGDL_PER_MMOLL);

  // ---------------------------------------------------------------- the consensus ranges and GlucoCraft's defaults
  // The consensus ranges in mg/dL (Battelino et al., 2019; tight range: Battelino et al., 2023), GlucoCraft's default
  // ranges (const.py AGP_RANGES): the ranges drawn until the configuration sensor's attributes arrive, and the
  // ranges a custom range is compared with
  const CONS_RANGES = Object.freeze({ very_low: 54, low: 70, tight_low: 70, tight_high: 140, high: 180, very_high: 250 });
  // GlucoCraft's own values, shown while the configuration sensor has no attributes (GlucoCraft reloading): the
  // no-current-data limit in minutes (const.DEFAULT_STALE_THRESHOLD) and the days of history kept, also the most days of
  // daily values the data endpoint sends (const.DEFAULT_RETENTION_DAYS)
  const STALE_DEFAULT_MIN = 15, RETENTION_DAYS = 200;

  // ---------------------------------------------------------------- the consensus targets
  // The consensus targets (Battelino et al., 2019, Tables 2 and 3) by goal key, [op, value, source]: the one table of
  // the cards and the CGM Report page (the same values as const.py CONSENSUS_TARGET_VALUES with GOAL_OPS); the goals
  // without a published target are not in it
  const GC_CONS_GOAL = Object.freeze({ time_in_range: [">", 70, "Battelino et al., 2019"],
    time_low: ["<", 4, "Battelino et al., 2019"], time_very_low: ["<", 1, "Battelino et al., 2019"],
    time_high: ["<", 25, "Battelino et al., 2019"], time_very_high: ["<", 5, "Battelino et al., 2019"],
    cv: ["<=", 36, "Battelino et al., 2019, Table 2"] });
  // True when the value v meets the target or goal "op t". The operators are those of const.GOAL_OPS (">", ">=",
  // "<", "<="); any other is never met.
  const gcMeets = (op, v, t) => (op === ">" ? v > t : op === ">=" ? v >= t : op === "<" ? v < t : op === "<=" ? v <= t
    : false);

  // ---------------------------------------------------------------- Home Assistant's time zone
  // Every time and date the cards and pages show is in Home Assistant's time zone (hass.config.time_zone), as on the
  // wall panels, in the CGM Report and in the notifications: the clock, the graph axes, the banners and the readouts.
  // The cards set it from their hass object (setTimeZone); until then, and for a zone the browser does not know, the
  // browser's own zone. A time is an instant (a Date or epoch milliseconds); zoneParts gives its date and clock time in
  // that zone.
  settings.tz = null;     // the zone in use (an IANA name), null = the browser's own zone
  let tzFormat = null;    // the formatter of that zone
  const ZONE_WEEKDAY = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
  /** Use the time zone tz (Home Assistant's hass.config.time_zone) for every time shown; true when it changed. */
  const setTimeZone = (tz) => {
    if (!tz || tz === settings.tz) return false;
    try {
      tzFormat = new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", weekday: "short", year: "numeric",
        month: "numeric", day: "numeric", hour: "numeric", minute: "numeric", second: "numeric" });
    } catch { return false; }   // a zone the browser does not know: the zone in use stays
    settings.tz = tz;
    return true;
  };
  /**
   * Return the date and clock time of an instant in the time zone in use: { y, mo (0-11), d, h (0-23), mi, s, wd
   * (0 = Sunday) }, the order of JavaScript's Date (getMonth, getDay).
   */
  const zoneParts = (t) => {
    const ms = +t;
    if (!tzFormat) {
      const x = new Date(ms);
      return { y: x.getFullYear(), mo: x.getMonth(), d: x.getDate(), h: x.getHours(), mi: x.getMinutes(),
        s: x.getSeconds(), wd: x.getDay() };
    }
    const p = {};
    for (const { type, value } of tzFormat.formatToParts(ms)) p[type] = value;
    return { y: +p.year, mo: +p.month - 1, d: +p.day, h: +p.hour % 24, mi: +p.minute, s: +p.second, wd: ZONE_WEEKDAY[p.weekday] };
  };
  // the start of the hour of an instant in that zone (epoch ms): its minutes, seconds and milliseconds taken off, so
  // that the zone's whole hours are found in a half-hour zone too
  const zoneHourStart = (t) => { const p = zoneParts(t); return +t - (p.mi * 60 + p.s) * 1000 - (((+t % 1000) + 1000) % 1000); };
  // the zone's offset from UTC at an instant (ms)
  const zoneOffset = (ms) => {
    const p = zoneParts(ms);
    return Date.UTC(p.y, p.mo, p.d, p.h, p.mi, p.s) - (ms - (((ms % 1000) + 1000) % 1000));
  };
  /**
   * Return the instant (epoch ms) of a date and clock time in that zone; the values may run over (hour 24 = the next
   * day's midnight, day 32 = a day of the next month), as with Date.UTC. A clock time that the zone skips at a
   * daylight-saving change gives an instant next to it.
   */
  const zoneTime = (y, mo, d, h = 0, mi = 0) => {
    const wall = Date.UTC(y, mo, d, h, mi), guess = wall - zoneOffset(wall), off = zoneOffset(guess);
    return wall - off;
  };
  // a calendar date ("YYYY-MM-DD", Home Assistant's days) as its noon in that zone: a Date that every date format
  // below shows as that day
  const zoneDate = (iso) => { const [y, m, d] = String(iso).split("-").map(Number); return new Date(zoneTime(y, m - 1, d, 12)); };
  // the same day in that zone
  const sameZoneDay = (a, b) => { const x = zoneParts(a), z = zoneParts(b); return x.y === z.y && x.mo === z.mo && x.d === z.d; };

  // ---------------------------------------------------------------- times and dates (regional.py)
  // the month and weekday names and the 12-hour clock's AM / PM are words of the catalogue; the order of the parts is
  // the format setting; every date and time in Home Assistant's time zone (zoneParts)
  const p2 = (n) => String(n).padStart(2, "0");
  // a clock time: "06:05" / "6:05 AM" (h 0-24; 24 is the midnight at a day's end)
  const clock = (h, m = 0) => (settings.time === "24" ? `${p2(h)}:${p2(m)}`
    : gcT(h % 24 < 12 ? "card.time.clock_12.am" : "card.time.clock_12.pm", { time: `${h % 12 || 12}:${p2(m)}` }));
  // an hour label of a graph axis: "06:00" / "6am" (DISPLAY_CONTRACT.md, as on the panels)
  const hour = (h) => (settings.time === "24" ? `${p2(h % 24)}:00`
    : gcT(h % 24 < 12 ? "card.time.hour_12.am" : "card.time.hour_12.pm", { hour: h % 12 || 12 }));
  const clockStr = (d) => { const p = zoneParts(d); return clock(p.h, p.mi); };
  const hourLabel = (d) => hour(zoneParts(d).h);
  // the months (Date.getMonth order) and the weekdays (Date.getDay order, Sunday first): [name, short name]
  const MONTH_KEYS = [
    ["card.date.month.january", "card.date.month_short.january"], ["card.date.month.february", "card.date.month_short.february"],
    ["card.date.month.march", "card.date.month_short.march"], ["card.date.month.april", "card.date.month_short.april"],
    ["card.date.month.may", "card.date.month_short.may"], ["card.date.month.june", "card.date.month_short.june"],
    ["card.date.month.july", "card.date.month_short.july"], ["card.date.month.august", "card.date.month_short.august"],
    ["card.date.month.september", "card.date.month_short.september"],
    ["card.date.month.october", "card.date.month_short.october"],
    ["card.date.month.november", "card.date.month_short.november"],
    ["card.date.month.december", "card.date.month_short.december"]];
  const WEEKDAY_KEYS = [
    ["card.date.weekday.sunday", "card.date.weekday_short.sunday"], ["card.date.weekday.monday", "card.date.weekday_short.monday"],
    ["card.date.weekday.tuesday", "card.date.weekday_short.tuesday"],
    ["card.date.weekday.wednesday", "card.date.weekday_short.wednesday"],
    ["card.date.weekday.thursday", "card.date.weekday_short.thursday"],
    ["card.date.weekday.friday", "card.date.weekday_short.friday"],
    ["card.date.weekday.saturday", "card.date.weekday_short.saturday"]];
  const monthName = (d, short = false) => gcT(MONTH_KEYS[zoneParts(d).mo][short ? 1 : 0]);
  const weekdayName = (d, short = false) => gcT(WEEKDAY_KEYS[zoneParts(d).wd][short ? 1 : 0]);
  // "October 22" / "22 October" (long: the full month name)
  const dayMonth = (d, long) => { const m = monthName(d, !long), day = zoneParts(d).d;
    return settings.date === "DMY" ? `${day} ${m}` : `${m} ${day}`; };
  // the day and month in numbers: "10/22" / "22/10" / "10-22"
  const numDate = (d) => { const { mo, d: day } = zoneParts(d);
    return settings.date === "DMY" ? `${day}/${mo + 1}` : settings.date === "YMD" ? `${p2(mo + 1)}-${p2(day)}` : `${mo + 1}/${day}`; };

  // ---------------------------------------------------------------- HTML
  // HTML / SVG text escape, also the double quote, so that it is safe in attributes too
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  // the text of an escaped HTML text run again (the inverse of esc), to give it its spoken form (gcSpeakText); "&amp;"
  // last, so that a text that itself holds "&lt;" (escaped "&amp;lt;") keeps it
  const unesc = (s) => s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&amp;/g, "&");

  // ---------------------------------------------------------------- links inside the dashboard
  // a plain click: no modifier key held. With ctrl, meta, shift or alt the browser opens the link itself (a new tab or
  // window), so the card leaves the click to it.
  const gcPlainClick = (e) => !(e.ctrlKey || e.metaKey || e.shiftKey || e.altKey);
  // Open a path of the dashboard without reloading the page: Home Assistant's frontend follows the new address once it
  // hears "location-changed" (the usual way of custom cards; Home Assistant offers no public call for it)
  const gcNavigate = (path) => {
    history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
  };

  // ---------------------------------------------------------------- spoken text (docs/ACCESSIBILITY.md rule 4; spoken.py)
  // Every surface speaks a value the same way. Screen readers mispronounce unit symbols, so every unit is written out;
  // the words are the catalogue's spoken.* texts. The functions take the decimal separator in use (dec, "." or ",") and
  // never convert units: the caller passes the value as it is displayed.
  // the unit symbols: the key of a value with its unit (plural forms, "{number} milligrams per deciliter") and of the
  // unit's name alone ("milligrams per deciliter")
  const GC_SPOKEN_UNIT_KEYS = {
    "mg/dL": ["spoken.unit.mg_dl", "spoken.unit_name.mg_dl"],
    "mmol/L": ["spoken.unit.mmol_l", "spoken.unit_name.mmol_l"],
    "mg/dL/min": ["spoken.unit.mg_dl_min", "spoken.unit_name.mg_dl_min"],
    "mmol/L/min": ["spoken.unit.mmol_l_min", "spoken.unit_name.mmol_l_min"],
    "%": ["spoken.unit.percent", "spoken.unit_name.percent"],
    "mmol/mol": ["spoken.unit.mmol_mol", "spoken.unit_name.mmol_mol"],
    min: ["spoken.unit.minute", "spoken.unit_name.minute"],
    h: ["spoken.unit.hour", "spoken.unit_name.hour"],
    d: ["spoken.unit.day", "spoken.unit_name.day"],
  };
  // the trend arrow's symbols and GlucoCraft's trend words (sensor.glucocraft_trend_arrow) as spoken words
  const GC_SPOKEN_TREND_KEYS = { "↑": "spoken.trend.rising_quickly", "↗": "spoken.trend.rising",
    "→": "spoken.trend.changing_slowly", "↘": "spoken.trend.falling", "↓": "spoken.trend.falling_quickly",
    "rising quickly": "spoken.trend.rising_quickly", rising: "spoken.trend.rising",
    "changing slowly": "spoken.trend.changing_slowly", falling: "spoken.trend.falling",
    "falling quickly": "spoken.trend.falling_quickly" };
  // a comparison sign before a number ("<4%", "≥ 70%") as words: screen readers skip "<" and ">" at their usual
  // punctuation level, and "<4%" would then be heard as "4 percent"
  const GC_SPOKEN_COMPARE_KEYS = { "<": "spoken.compare.less_than", ">": "spoken.compare.more_than",
    "<=": "spoken.compare.at_most", "≤": "spoken.compare.at_most", ">=": "spoken.compare.at_least", "≥": "spoken.compare.at_least" };
  // a number as spoken: d decimals (undefined: as given; rounded once, halves away from zero), the decimal separator in
  // use, "minus" / "plus" with sign. A value that rounds to zero is "0" or "0.0", never "minus 0.0" (spoken.speak_number).
  const gcSpeakNumber = (v, d, dec = ".", sign = false) => {
    const r = d === undefined ? v + 0 : round(v, d), a = Math.abs(r);
    const number = (d === undefined ? String(a) : a.toFixed(d)).replace(".", dec === "," ? "," : ".");
    return r < 0 ? gcT("spoken.number.negative", { number }) : sign && r > 0 ? gcT("spoken.number.positive", { number }) : number;
  };
  // a number written as displayed ("3,9") with its unit symbol, written out; the plural form follows the number as
  // written (in English "1 minute", "1.0 millimoles per liter")
  const gcSpeakUnit = (number, unit) => {
    const digits = (String(number).split(/[.,]/)[1] || "").length;
    return gcT(GC_SPOKEN_UNIT_KEYS[unit][0], { count: Math.abs(parseFloat(String(number).replace(",", "."))), digits, number });
  };
  // a value with its unit, written out: gcSpeak(62, "mg/dL") = "62 milligrams per deciliter"; opt: {d, dec, sign}.
  // A unit not in the table is spoken as it is written.
  const gcSpeak = (v, unit = "", opt = {}) => {
    if (typeof v !== "number" || !isFinite(v)) return gcT("spoken.not_available");
    const number = gcSpeakNumber(v, opt.d, opt.dec, opt.sign);
    if (!unit) return number;
    if (!GC_SPOKEN_UNIT_KEYS[unit]) return gcT("spoken.number_unit", { number, unit });
    return gcT(GC_SPOKEN_UNIT_KEYS[unit][0], { count: Math.abs(v), digits: opt.d || undefined, number });
  };
  // A GMI in the A1C / GMI unit setting ("percent", "mmol_mol", "both"): mmol, its value in mmol/mol as displayed (from
  // the mean glucose); without it, from the % value by the GMI rule (gmiPctToMmol: the mean that gives that %)
  const gcSpeakA1c = (pct, a1cUnit = "percent", dec = ".", mmol = NaN) => {
    if (typeof pct !== "number" || !isFinite(pct)) return gcT("spoken.not_available");
    const mm = isFinite(mmol) ? mmol : round(gmiPctToMmol(pct));
    const p = gcSpeak(pct, "%", { d: 1, dec }), m = gcSpeak(mm, "mmol/mol");
    return a1cUnit === "mmol_mol" ? m : a1cUnit === "both" ? gcT("spoken.a1c.both", { percent: p, mmol_mol: m }) : p;
  };
  // an age or duration in minutes: "5 minutes", "2 hours 5 minutes", "3 days 4 hours"
  const gcSpeakAge = (min) => {
    if (typeof min !== "number" || !isFinite(min)) return gcT("spoken.not_available");
    const m = Math.max(0, Math.floor(min));
    const two = (larger, smaller) => (smaller ? gcT("spoken.duration.two_units", { larger, smaller }) : larger);
    if (m < 60) return gcSpeak(m, "min");
    if (m < 1440) return two(gcSpeak(Math.floor(m / 60), "h"), m % 60 ? gcSpeak(m % 60, "min") : "");
    const h = Math.floor((m % 1440) / 60);
    return two(gcSpeak(Math.floor(m / 1440), "d"), h ? gcSpeak(h, "h") : "");
  };
  // a trend arrow symbol or trend word as spoken words ("Falling Quickly" -> "falling quickly"); "" when unknown
  const gcSpeakTrend = (tr) => {
    const key = GC_SPOKEN_TREND_KEYS[String(tr ?? "").trim().toLowerCase()] || GC_SPOKEN_TREND_KEYS[String(tr ?? "").trim()];
    return key ? gcT(key) : "";
  };
  // a LO / HI reading: the CGM system measured no value, so the words say where the reading lies
  const gcSpeakLimit = (limit) => (limit === "low" ? gcT("spoken.limit.low") : limit === "high" ? gcT("spoken.limit.high") : "");
  // The time of a period's readings at the sensor's limit (they have no value: left out of every value, counted as
  // time): whole minutes, up (limits.limit_minutes; any time above zero is at least 1 minute): "15 min", "2 h 5 min"
  const gcLimitMinutes = (seconds) => (seconds > 0 ? Math.ceil(seconds / 60) : 0);
  const gcLimitDuration = (seconds) => {
    const m = gcLimitMinutes(seconds);
    return m < 60 ? gcT("limit_time.minutes", { minutes: m }) : gcT("limit_time.hours", { hours: Math.floor(m / 60), minutes: m % 60 });
  };
  // [seconds of LO, seconds of HI] -> ["LO 2 h 5 min", "HI 15 min"], only those above zero
  const gcLimitParts = (seconds) => [["card.limit.low", seconds?.[0]], ["card.limit.high", seconds?.[1]]]
    .filter(([, sec]) => sec > 0).map(([key, sec]) => gcT("limit_time.part", { limit: gcT(key), time: gcLimitDuration(sec) }));
  // the same in spoken words: "below the sensor's lower limit for 2 hours 5 minutes"
  const gcLimitSaid = (seconds) => [["low", seconds?.[0]], ["high", seconds?.[1]]].filter(([, sec]) => sec > 0)
    .map(([k, sec]) => gcT("spoken.limit_time", { limit: gcSpeakLimit(k), time: gcSpeakAge(gcLimitMinutes(sec)) }));
  // A visible text made speakable: a plus or minus sign before a number, the comparison signs before a number, the unit
  // symbols, the period labels, ✓ / ✗, the mark ‡ and the en dash between two numbers in words, the numbers themselves
  // unchanged
  // (they already carry the decimal separator in use). For labels built from the visible text of a card, such as "Mean
  // 102 mg/dL · 14d" -> "Mean 102 milligrams per deciliter · 14 days", "Consensus target <4%" -> "Consensus target less
  // than 4 percent", or "-0.6 percentage points" -> "minus 0.6 percentage points". Screen readers skip "+" and read "-"
  // as "dash" or not at all, so a difference would be heard without its direction; a sign counts only at the start of
  // the text or after a space, "(" or ":", so that a hyphen between two numbers ("22-26") or inside a word stays.
  const gcSpeakText = (text) => String(text)
    .replace(/(^|[\s(:])([+\-−])(\d+(?:[.,]\d+)?)/g, (m, before, sign, number) =>
      before + gcT(sign === "+" ? "spoken.number.positive" : "spoken.number.negative", { number }))
    .replace(/(<=|>=|[<>≤≥])\s?(\d+(?:[.,]\d+)?(?:\s?(?:%|mg\/dL\/min|mmol\/L\/min|mmol\/mol|mg\/dL|mmol\/L))?)/g,
      (m, sign, value) => gcT(GC_SPOKEN_COMPARE_KEYS[sign], { value }))
    .replace(/(\d+(?:[.,]\d+)?)\s?(mg\/dL\/min|mmol\/L\/min|mmol\/mol|mg\/dL|mmol\/L)/g, (m, n, u) => gcSpeakUnit(n, u))
    .replace(/(?<![\p{L}\p{N}_])(mg\/dL\/min|mmol\/L\/min|mmol\/mol|mg\/dL|mmol\/L)/gu,
      (m, u) => gcT(GC_SPOKEN_UNIT_KEYS[u][1]))
    .replace(/(\d+(?:[.,]\d+)?)\s?%/g, (m, n) => gcSpeakUnit(n, "%"))
    .replace(/([A-Za-z]?)(\d+)\s?(min|h|d)(?![\p{L}\p{N}_])/gu, (m, l, n, u) => (l ? `${l} ` : "") + gcSpeakUnit(n, u))
    .replace(/(\d)\s?[–−]\s?(\d)/g, (m, from, to) => gcT("spoken.range", { from, to }))
    .replace(/✓/g, () => gcT("spoken.meets")).replace(/✗/g, () => gcT("spoken.does_not_meet"))
    // the mark ‡ of a difference in time in range of 5 percentage points or more (its note explains it)
    .replace(/\s?‡/g, () => gcT("spoken.mark.large_change"))
    .replace(/[↑↗→↘↓]/g, (a) => gcT(GC_SPOKEN_TREND_KEYS[a]));

  // ---------------------------------------------------------------- content and focus across redraws (WCAG 2.4.3)
  // A card draws its content again with every new reading and every minute; replacing the content removes the control
  // that has the focus, and the focus would fall back to the page. gcRender leaves content that has not changed in
  // place, so that nothing is replaced (the focus and a screen reader's reading position stay where they are). Changed
  // content replaces the old, and the control that had the focus is focused again: the new control with the same data
  // attributes, link address or id (else the same class), or else the control in the same place among the card's
  // controls.
  const GC_FOCUSABLE = 'a[href], button, input, select, textarea, summary, [tabindex]:not([tabindex="-1"])';
  // the focused control inside box (in the card's shadow root): a selector that finds its counterpart, and its place
  const gcFocusKey = (root, box) => {
    const el = root.activeElement;
    if (!el || !box.contains?.(el) || !el.matches?.(GC_FOCUSABLE)) return null;
    const quote = (v) => `"${String(v).replace(/["\\]/g, "\\$&")}"`;
    const named = [...el.attributes].filter((a) => a.name.startsWith("data-") || a.name === "href" || a.name === "id");
    const by = named.length ? named.map((a) => `[${a.name}=${quote(a.value)}]`).join("")
      : el.hasAttribute("class") ? `[class=${quote(el.getAttribute("class"))}]` : "";
    return { sel: el.localName + by, index: [...box.querySelectorAll(GC_FOCUSABLE)].indexOf(el) };
  };
  // after box was written anew: focus the control that key (gcFocusKey, taken before) names, its counterpart or else
  // the control in its place; nothing without a key
  const gcRefocus = (box, key) => {
    if (key) (box.querySelector(key.sel) || box.querySelectorAll(GC_FOCUSABLE)[key.index])?.focus({ preventScroll: true });
  };
  // set the content of box (default: the shadow root itself) to html; true when it changed
  const gcRender = (root, html, box = root) => {
    if (box.gcHtml === html) return false;
    const key = gcFocusKey(root, box);
    box.innerHTML = html;
    box.gcHtml = html;
    gcRefocus(box, key);
    return true;
  };

  // ---------------------------------------------------------------- the gap rule (gaps.py)
  // The gap rule (GlucoCraft rule), judged per stretch exactly as GlucoCraft's statistics and "sensor active"
  // (gaps.py local_intervals / gap_threshold) and the panels (gc_units.h is_gap): the interval between two consecutive
  // readings is a gap when it is longer than max(15 min, 3 x the reading interval); the reading interval is the median
  // (upper middle) of the 10 intervals before the earlier reading, filled with the intervals after the judged one when
  // fewer exist, never the judged interval itself; intervals of 0 s do not count. With fewer than 3 intervals the
  // fallback threshold applies (fb: the configuration's gap_threshold_seconds = max(15 min, 3 x the detected interval)).
  const GAP_FLOOR_S = 900, GAP_FACTOR = 3, GAP_WINDOW = 10, GAP_MIN_IV = 3;
  // the gap threshold (s) of a reading interval iv (s): max(15 min, 3 x iv) (gaps.gap_threshold); also the fallback
  // threshold a card calculates while the configuration's gap_threshold_seconds is missing
  const gcGapThreshold = (iv) => Math.max(GAP_FLOOR_S, GAP_FACTOR * iv);
  // the threshold (s) of the interval ts[i] -> ts[i + 1] (ts sorted); fb = the fallback threshold
  const gcGapThrAt = (ts, i, fb) => {
    const n = ts.length, c = [];
    let taken = 0;
    for (let k = Math.max(0, i - GAP_WINDOW); k < i; k++, taken++) { const d = ts[k + 1] - ts[k]; if (d > 0) c.push(d); }
    for (let k = i + 1; k + 1 < n && taken < GAP_WINDOW; k++, taken++) { const d = ts[k + 1] - ts[k]; if (d > 0) c.push(d); }
    if (c.length < GAP_MIN_IV) return fb;
    c.sort((a, b) => a - b);
    return gcGapThreshold(c[c.length >> 1]);
  };
  // the gaps of the sorted reading times ts: [[t1, t2], ...], the readings on either side of each gap (oldest first).
  // Up to 15 minutes is never a gap, so the median is only needed for the longer intervals.
  const gcGaps = (ts, fb) => {
    const out = [];
    for (let i = 0; i + 1 < ts.length; i++) {
      const d = ts[i + 1] - ts[i];
      if (d > GAP_FLOOR_S && d > gcGapThrAt(ts, i, fb)) out.push([ts[i], ts[i + 1]]);
    }
    return out;
  };
  // true when one of the gaps lies between the times a < b (a gap [t1, t2] with t1 < b and t2 > a); binary search
  const gcGapIn = (gaps, a, b) => {
    let lo = 0, hi = gaps.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (gaps[m][1] <= a) lo = m + 1; else hi = m; }
    return lo < gaps.length && gaps[lo][0] < b;
  };
  // true when a stretch of limit readings (runs: [[start, end, kind], ...]) lies between the times a and b: a graph's
  // line between two readings breaks there, as across a gap (the panels' gc_crosses_run), also when the stretch is
  // shorter than a gap
  const gcRunIn = (runs, a, b) => (runs || []).some((r) => r[0] <= b && r[1] >= a);
  // the first index with ts[i] >= x (ts sorted by time); binary search
  const first = (ts, x) => { let lo = 0, hi = ts.length; while (lo < hi) { const m = (lo + hi) >> 1; if (ts[m] < x) lo = m + 1; else hi = m; } return lo; };

  // ---------------------------------------------------------------- graphs
  // Every graph's vertical scale (the display card, the dashboard and the CGM Report): the values mn to mx, widened
  // around their middle to at least spread, fill the middle 70 % of the height, with a 15 % margin above and below.
  // -> [the value at the bottom, the value at the top]
  const GRAPH_MARGIN = 0.15, GRAPH_DATA = 0.70;
  const gcFit = (mn, mx, spread) => {
    if (mx - mn < spread) { const c = (mn + mx) / 2; mn = c - spread / 2; mx = c + spread / 2; }
    const m = (mx - mn) * GRAPH_MARGIN / GRAPH_DATA;
    return [mn - m, mx + m];
  };
  // the step of a graph's grid lines from lo to hi: the smallest step of the ladder that divides lo to hi into at most
  // n parts (100 for a wider axis)
  const GRID_STEPS = [0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100];
  const gcGridStep = (lo, hi, n) => { const raw = (hi - lo) / n; return GRID_STEPS.find((s) => s >= raw) || 100; };
  /**
   * Return the SVG paths of the minimum-to-maximum range of each time slot.
   *
   * The minimum-to-maximum range of each time slot, drawn as light-gray areas with a dark outline (one area per run of
   * slots with data), so that it stands out on every color band. lo / hi: the values per slot (0 = no data); slot i
   * covers x = i * w to (i + 1) * w; yOf: value -> y.
   */
  const gcRangeAreas = (lo, hi, yOf, w = 1) => {
    let out = "", top = [], bottom = [];
    const flush = () => {
      if (top.length) out += `<path d="M${top.join("L")}L${bottom.reverse().join("L")}Z" fill="#bdbdbd" stroke="#000000" ` +
        'stroke-width="1" stroke-linejoin="round"/>';
      top = []; bottom = [];
    };
    for (let i = 0; i < lo.length; i++) {
      if (!(lo[i] > 0) || !(hi[i] > 0)) { flush(); continue; }
      const x0 = +(i * w).toFixed(1), x1 = +((i + 1) * w).toFixed(1), yt = yOf(hi[i]), yb = yOf(lo[i]) + 1;
      top.push(`${x0} ${yt}`, `${x1} ${yt}`);
      bottom.push(`${x0} ${yb}`, `${x1} ${yb}`);
    }
    flush();
    return out;
  };
  /**
   * Return the marks of the readings at the sensor's limit (LO / HI) on a graph: { shapes, labels }.
   *
   * A reading at the sensor's limit has no value, so the graph's line breaks there; instead, a row of small marks runs
   * along the very bottom of the graph area for LO and along the very top for HI, in the very-low / very-high color
   * (colors: { low, high } as "#RRGGBB"), each mark 2 px wide and 6 px tall every 4 px, over a black strip 8 px tall
   * (1 px beyond the marks on every side) so that they stand out on every range band. No text, no number and no
   * value: the marks say only that the readings were beyond the sensor's range for that time, and the edge (bottom:
   * below, top: above) and the color tell the side; the screen in the value's place names a limit reading while it
   * lasts. runs: [[start, end, "low" | "high"], ...] (epoch seconds); t0, t1: the graph's time axis; w, h: the graph
   * area. Runs of one kind closer than 3 px are one stretch. Returns the SVG shapes, which the caller draws after the
   * data and before the graph's labels. The panels draw the same marks at the same sizes (glucocraft_panel_binary.h
   * gc_draw_limit_marks).
   */
  const LIMIT_MARK = Object.freeze({ strip: 8, mark: 6, width: 2, step: 4, merge: 3 });
  const gcLimitMarks = (runs, t0, t1, w, h, colors) => {
    const M = LIMIT_MARK, xOf = (t) => Math.floor((t - t0) * w / (t1 - t0));
    const stretches = [];
    (Array.isArray(runs) ? runs : [])
      .filter((r) => r && r[1] >= t0 && r[0] <= t1 && (r[2] === "low" || r[2] === "high"))
      .map(([a, b, kind]) => [Math.max(0, Math.min(w - M.width, xOf(Math.max(a, t0)))),
        Math.max(0, Math.min(w, xOf(Math.min(b, t1)))), kind])
      .sort((p, q) => p[0] - q[0])
      .forEach(([x0, x1, kind]) => {
        const last = stretches.filter((s) => s[2] === kind).pop();
        if (last && x0 - last[1] < M.merge) last[1] = Math.max(last[1], x1);
        else stretches.push([x0, Math.max(x1, x0 + M.width), kind]);
      });
    let shapes = "";
    for (const [x0, x1, kind] of stretches) {
      const low = kind === "low", sy = low ? h - M.strip : 0, my = low ? h - 1 - M.mark : 1;
      const color = /^#[0-9a-f]{6}$/i.test(colors?.[kind] || "") ? colors[kind] : ZONE_DEFAULT[low ? "very_low" : "very_high"];
      shapes += `<rect x="${Math.max(0, x0 - 1)}" y="${sy}" width="${Math.min(w, x1 + 1) - Math.max(0, x0 - 1)}" ` +
        `height="${M.strip}" fill="#000000" data-limit="${kind}"/>`;
      for (let x = x0; x + M.width <= Math.max(x1, x0 + M.width); x += M.step) {
        shapes += `<rect x="${x}" y="${my}" width="${M.width}" height="${M.mark}" fill="${color}"/>`;
      }
    }
    return shapes;
  };
  // The 70% rule of the statistics: a period's statistics are shown only once the readings received cover at least 70%
  // of it (sensor active, compared before rounding). Battelino et al. (2019) set 70% for 14-day periods; applying it to
  // every period length is a GlucoCraft rule. For a period below it, the time still needed with no gaps:
  // gcEnoughDataWait(periodSeconds, coveredSeconds) -> { percent: the covered share (unrounded), seconds: the time
  // still needed, count and unit ("hours" for a period of up to 24 hours and for less than a day still needed, else
  // "days"), rounded to the nearest whole unit, at least 1 }. An estimate: it assumes that no reading falls out of the
  // period meanwhile.
  const ENOUGH_DATA_PCT = 70;
  const gcEnoughDataWait = (periodSeconds, coveredSeconds) => {
    const period = +periodSeconds, covered = Math.max(0, +coveredSeconds || 0);
    const seconds = Math.max(0, ENOUGH_DATA_PCT / 100 * period - covered);
    const hours = period <= 86400 || seconds < 86400;
    return { percent: period > 0 ? Math.min(100, 100 * covered / period) : 0, seconds,
      unit: hours ? "hours" : "days", count: Math.max(1, Math.round(seconds / (hours ? 3600 : 86400))) };
  };

  // Two sets of seven colors by zone name, each published by GlucoCraft on sensor.glucocraft_configuration (the user's
  // own colors or the default palette): zone_colors, the range colors (the glucose value, LO / HI and everything else
  // that names a zone by color), and band_colors, the graph range colors (the range bands of the graphs, 60 % over the
  // graph's black; their default is the time-in-ranges colors of the CGM Report, the AGP report colors, HealthPartners
  // Institute International Diabetes Center, 2024). The defaults (const.ZONE_COLORS, const.BAND_COLORS) are used only
  // until the attributes arrive, and for any zone whose published color is not a valid "#RRGGBB" value.
  const ZONE_DEFAULT = { very_low: "#C62828", low: "#EF5350", near_low: "#FFC107", tight_range: "#4CAF50",
    near_high: "#FFC107", high: "#42A5F5", very_high: "#1565C0" };
  const BAND_DEFAULT = { very_low: "#9B1C1F", low: "#E5352B", near_low: "#1F8F45", tight_range: "#52B964",
    near_high: "#1F8F45", high: "#FCC21B", very_high: "#F08A4B" };
  // a set of zone colors from a published { zone: "#RRGGBB" } attribute, the defaults for a missing or invalid color
  const gcPalette = (defaults, published) => {
    const out = { ...defaults };
    if (published && typeof published === "object") {
      for (const zone of Object.keys(defaults)) {
        if (typeof published[zone] === "string" && /^#[0-9a-f]{6}$/i.test(published[zone])) out[zone] = published[zone];
      }
    }
    return out;
  };
  // Text on a colored area is black or white, whichever has the higher contrast ratio with that color, white when both
  // are equal (WCAG 2.2 relative luminance; colors as numbers 0xRRGGBB): the rule of the integration
  // (contrast.foreground) and of the wall panels (gc_units.h text_on)
  const gcLum = (n) => {
    const lin = (v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
    return 0.2126 * lin(((n >> 16) & 255) / 255) + 0.7152 * lin(((n >> 8) & 255) / 255) + 0.0722 * lin((n & 255) / 255);
  };
  const gcContrast = (a, b) => { const x = gcLum(a), y = gcLum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const gcOnColor = (n) => (gcContrast(n, 0xFFFFFF) >= gcContrast(n, 0x000000) ? 0xFFFFFF : 0x000000);

  // ---------------------------------------------------------------- GlucoCraft's own controls (WCAG 1.4.3, 1.4.11)
  // Home Assistant's primary color is too light for small text: its default #009ac7 (the older default #03a9f4) reaches
  // 3.26:1 (2.63:1) on white and under white text, below the 4.5:1 of ACCESSIBILITY.md rule 2. GlucoCraft's links and
  // text buttons therefore use the primary color mixed with the theme's text color (darker on a light theme, lighter on a
  // dark one), and its filled buttons the primary color mixed toward black under white text: the hue stays, and Home
  // Assistant's default themes reach 4.5:1 (tests/test_accessibility_checks.py). One focus ring on every card and page:
  // 3 px in the text color, which contrasts with the background it is drawn on.
  const css = Object.freeze({
    link: "color-mix(in srgb, var(--primary-color) 50%, var(--primary-text-color))",
    fill: "color-mix(in srgb, var(--primary-color) 65%, #000)", onFill: "#fff",
    focus: "outline: 3px solid var(--primary-text-color); outline-offset: 2px;",
    // text read by screen readers only (ACCESSIBILITY.md rule 4): not shown, but in the reading order (class "sr")
    sr: ".sr { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; " +
      "clip: rect(0 0 0 0); white-space: nowrap; border: 0; }",
  });

  // ---------------------------------------------------------------- GlucoCraft's files (GLU2 v3: panel_feed.py)
  const OPS = [null, ">", ">=", "<", "<="];   // the file's target operator codes
  // the file's direction codes (1-3) as the keys of their words
  const DIRECTION_KEYS = [null, "card.direction.increasing", "card.direction.decreasing", "card.direction.stable"];
  /**
   * Parse a GLU2 file of version 3 or later (panel_feed.py, glucocraft_panel_binary.h), as the panels read it.
   *
   * A later version is read by the parts this parser knows (the header's offsets and the blocks found by their
   * names) and the rest is ignored, as on the panels; a version before 3 is refused.
   *
   * -> { stats (the seven periods; the CV and the percentages unrounded, panel_feed._pct32), ranges, ts, val (the
   * readings), newest, trends (the weekly records), dirs (the key of each weekly page's direction word), std (the GCX1
   * extension: the range set the weekly pages show, its targets and description lines, or null), limitSeconds (the GCL2
   * trailer: for each of the seven periods [seconds of LO readings, seconds of HI readings]; zeros in a file without
   * the trailer), limitRuns (the GCR1 block: the stretches of readings at the sensor's limit, [[start, end, "low" |
   * "high"], ...]; none in a file without the block) }
   */
  const parseGlu2 = (buf) => {
    const d = new DataView(buf);
    if (d.byteLength < 40 || d.getUint32(0, true) !== 0x32554C47 || d.getUint16(4, true) < 3) throw new Error("not GLU2 v3");
    const tc = d.getUint16(6, true), so = d.getUint32(8, true), ro = d.getUint32(12, true), rdo = d.getUint32(16, true);
    const n = d.getUint32(20, true), newest = d.getUint32(28, true), to = d.getUint32(32, true);
    if (rdo + n * 6 > d.byteLength) throw new Error("truncated");
    const stats = [];
    for (let p = 0; p < 7; p++) {
      const b = so + p * 44, f = (i) => d.getFloat32(b + i * 4, true);
      stats.push({ mean: f(0), sd: f(1), cv: f(2), gmi: f(3), tir: f(4), titr: f(5), low: f(6), high: f(7),
                   clo: f(8), chi: f(9), min: d.getUint16(b + 40, true), max: d.getUint16(b + 42, true) });
    }
    // The GCL2 trailer holds each period's seconds at the sensor's limit (LO, HI), for the warning on its graph. The GCM1
    // block in front of it holds each period's minimum and maximum unrounded (STATS has them as whole mg/dL), so that a
    // value in mmol/L is rounded once, when it is shown. A file without them keeps STATS' values, with no limit time.
    const L = d.byteLength, limitSeconds = stats.map(() => [0, 0]);
    let gcm1 = -1;                                                            // where the GCM1 block starts
    if (L >= 60 && d.getUint32(L - 4, true) === 0x324C4347) {                 // "GCL2": 7 x (uint32 LO, uint32 HI)
      const end = L - 60;                                                     // where the GCL2 trailer starts
      for (let p = 0; p < 7; p++) limitSeconds[p] = [d.getUint32(end + p * 8, true), d.getUint32(end + 4 + p * 8, true)];
      if (end >= 60 && d.getUint32(end - 4, true) === 0x314D4347) {           // "GCM1": 7 x (float32 min, float32 max)
        gcm1 = end - 60;
        for (let p = 0; p < 7; p++) {
          stats[p].min = d.getFloat32(end - 60 + p * 8, true);
          stats[p].max = d.getFloat32(end - 56 + p * 8, true);
        }
      }
    }
    const ranges = [0, 1, 2, 3, 4, 5].map((i) => d.getFloat32(ro + i * 4, true));
    const ts = new Uint32Array(n), val = new Uint16Array(n);
    for (let i = 0; i < n; i++) { ts[i] = d.getUint32(rdo + i * 6, true); val[i] = d.getUint16(rdo + i * 6 + 4, true); }
    const dirField = d.getUint32(36, true);   // the direction of each weekly page: the key of its word, or null
    const dirs = (dirField >>> 31) ? [0, 1, 2, 3, 4, 5, 6, 7, 8].map((k) => DIRECTION_KEYS[(dirField >>> (2 * k)) & 3]) : [];
    const trends = [];
    for (let w = 0; w < Math.min(tc, 26) && to && to + (w + 1) * 48 <= d.byteLength; w++) {
      const b = to + w * 48, f = (i) => d.getFloat32(b + 8 + i * 4, true);
      trends.push({ start: d.getUint32(b, true), mean: f(0), sd: f(1), cv: f(2), gmi: f(3), tir: f(4), titr: f(5),
                    low: f(6), high: f(7), min: f(8), max: f(9) });
    }
    // GCX1 extension: the range set the weekly pages show (custom or consensus), its targets and description lines
    let std = null;
    const xo = to + tc * 48;
    if (to && xo + 8 + 656 <= d.byteLength && d.getUint32(xo, true) === 0x31584347) {
      const b = xo + 8, targets = [], descs = [];
      for (let k = 0; k < 9; k++) {
        const op = OPS[d.getUint8(b + 8 + k * 8)];
        targets.push(op ? [op, d.getFloat32(b + 8 + k * 8 + 4, true)] : null);
        // the line in UTF-8 up to its NUL byte (at most 63 bytes, cut at a whole character by GlucoCraft)
        const line = new Uint8Array(d.buffer, d.byteOffset + b + 80 + k * 64, 63), end = line.indexOf(0);
        descs.push(new TextDecoder("utf-8").decode(end < 0 ? line : line.subarray(0, end)));
      }
      std = { custom: d.getUint8(b) === 1, targets, descs };
    }
    // The GCR1 block, directly in front of GCM1: the stretches of readings at the sensor's limit, for the LO / HI marks
    // of the graphs (the readings themselves are not in the file: they have no value). uint16 count, then count x
    // (uint32 start, uint32 end, uint8 kind: 1 LO, 2 HI), then "GCR1". Its start is the end of the parts before it (the
    // readings, the weekly records and the GCX1 extension); where that does not match, the count is found from the end.
    // A file without it has no stretches.
    const limitRuns = [];
    const r1 = gcm1 - 4;
    if (r1 >= 2 && d.getUint32(r1, true) === 0x31524347) {                    // "GCR1"
      let known = Math.max(rdo + n * 6, to ? to + tc * 48 : 0);
      if (std) {
        const sc = d.getUint16(xo + 6, true);
        known = Math.max(known, xo + 8 + sc * 656 + (sc >= 2 ? d.getUint16(xo + 8 + 656 + 2, true) * 48 : 0));
      }
      const valid = (start, c) => start >= 0 && d.getUint16(start, true) === c && start + 2 + 9 * c === r1 &&
        [...Array(c).keys()].every((i) => {
          const b = start + 2 + 9 * i, k = d.getUint8(b + 8);
          return (k === 1 || k === 2) && d.getUint32(b, true) <= d.getUint32(b + 4, true);
        });
      let count = -1;
      if (known < r1 && (r1 - known - 2) % 9 === 0 && valid(known, (r1 - known - 2) / 9)) count = (r1 - known - 2) / 9;
      for (let c = 0; count < 0 && 2 + 9 * c <= r1; c++) if (valid(r1 - 2 - 9 * c, c)) count = c;
      for (let i = 0, b = r1 - 9 * count; i < count; i++, b += 9) {
        limitRuns.push([d.getUint32(b, true), d.getUint32(b + 4, true), d.getUint8(b + 8) === 1 ? "low" : "high"]);
      }
    }
    return { stats, ranges, ts, val, newest, trends, dirs, std, limitSeconds, limitRuns };
  };

  // ------------------------------------------------------------------ failures logged once
  // A failure that a card shows in its own words and tries again (a download, a file that cannot be read) is written
  // to the browser console once with its reason, not at every try: gcNewFailure(key, reason) is true when reason is
  // not the one last logged for key (the caller then logs it); reason null records a try that worked, so that a later
  // failure is logged again. Kept for the page (globalThis.__glucocraftFailures), shared by the card files.
  const FAILURES = (globalThis.__glucocraftFailures ||= {});
  const gcNewFailure = (key, reason) => {
    if (reason === null) { delete FAILURES[key]; return false; }
    if (FAILURES[key] === reason) return false;
    FAILURES[key] = reason;
    return true;
  };

  // ------------------------------------------------------------------ GlucoCraft's files, shared by every card on the page
  // globalThis.__glucocraftFiles, per file (the key: "backfill" / "delta", or "<url>|<file>" for a card with its own
  // file_url): file, at: the file and when it was downloaded (or when the download failed); failed: why the last
  // download failed, "none" (GlucoCraft has no readings yet, HTTP 503) or "error" (any other failure, logged once per
  // file and reason with gcNewFailure: the HTTP status, or why the file could not be read), null after a download that
  // worked; loading: the download under way.
  const FILE_URL = "/api/glucocraft/binary";
  const FILES = (globalThis.__glucocraftFiles ||= { file: {}, at: {}, loading: {}, failed: {} });
  const fileKey = (url, which) => (url === FILE_URL ? which : `${url}|${which}`);
  /**
   * Return a GLU2 file (which: backfill or delta), downloaded once for every card on the page and kept: the full file
   * for an hour, the recent-readings file for 5 minutes, a failed download for a minute (then tried again). A failed
   * recent-readings file is null (never an old one: its 6 h / 24 h numbers would be frozen from before the gap); a
   * failed full file keeps the last good one. force: download now (a change of the settings the file carries); a
   * download already under way may have started before that change, so a forced call waits for it and downloads again.
   * The file gets `fetched`, the time of its download (epoch seconds).
   */
  const gcFile = (hass, which, { url = FILE_URL, force = false } = {}) => {
    const S = FILES, k = fileKey(url, which), maxAge = which === "delta" ? 5 * 60e3 : 60 * 60e3;
    if (!force && S.at[k] && Date.now() - S.at[k] < maxAge) return Promise.resolve(S.file[k] ?? null);
    const download = () => {
      S.loading[k] = (async () => {
        try {
          const r = await hass.fetchWithAuth(`${url}?file=${which}`, { cache: "no-cache" });
          if (!r.ok) throw new Error(r.status === 503 ? "none" : `HTTP ${r.status}`);
          const f = parseGlu2(await r.arrayBuffer());
          f.fetched = Math.floor(Date.now() / 1000);
          S.file[k] = f; S.at[k] = Date.now(); S.failed[k] = null;
          gcNewFailure(`file|${k}`, null);
        } catch (e) {
          if (which === "delta") S.file[k] = null;
          S.failed[k] = e?.message === "none" ? "none" : "error";
          S.at[k] = Date.now() - maxAge + 60e3;
          // GlucoCraft without readings yet is a state the cards name, not a failure
          const reason = S.failed[k] === "none" ? null : String(e?.message || e);
          if (gcNewFailure(`file|${k}`, reason)) {
            console.warn(`GlucoCraft: file ${which} from ${url} not read (${reason}); tried again every minute`);
          }
        } finally { S.loading[k] = null; }
        return S.file[k] ?? null;
      })();
      return S.loading[k];
    };
    if (!S.loading[k]) return download();
    if (!force) return S.loading[k];
    // a forced call must not get a download that started before its change: it gets one download after the running
    // one, shared by every forced call that arrives meanwhile, so N cards on a page download the file once more, not
    // N times
    S.reload ||= {};
    return (S.reload[k] ||= S.loading[k].then(() => {
      S.reload[k] = null;
      return download();
    }));
  };
  // why the last download of a file failed (gcFile): "none" (GlucoCraft has no readings yet), "error", or null after
  // a download that worked or before the first one ended
  const gcFileFailed = (which, { url = FILE_URL } = {}) => FILES.failed[fileKey(url, which)] ?? null;

  globalThis.glucocraftShared = Object.freeze({
    version: GCSHARED_VERSION, settings, applyRegional,
    round, rh1, nf, dtx, gcCap, shownAgainst, nfAt, MGDL_PER_MMOLL, CONS_MMOLL, GLU_MAX, isMmol, gU, gD, gD32, gF, gF32,
    deltaShown, gDelta, gcAxisDecimals, gcAxisText, GMI, gmiMmolFromMean, gmiPctToMmol, gmiMmolToPct,
    CONS_RANGES, STALE_DEFAULT_MIN, RETENTION_DAYS, GC_CONS_GOAL, gcMeets, ENOUGH_DATA_PCT, gcEnoughDataWait,
    setTimeZone, zoneParts, zoneHourStart, zoneTime, zoneDate, sameZoneDay,
    p2, clock, hour, clockStr, hourLabel, monthName, weekdayName, dayMonth, numDate, esc, unesc, gcPlainClick, gcNavigate,
    gcSpeakNumber, gcSpeakUnit, gcSpeak, gcSpeakA1c, gcSpeakAge, gcSpeakTrend, gcSpeakLimit, gcLimitDuration, gcLimitParts, gcLimitSaid, gcSpeakText, gcRender, gcFocusKey, gcRefocus,
    GAP_WINDOW, gcGapThreshold, gcGapThrAt, gcGaps, gcGapIn, gcRunIn, first, gcFit, gcGridStep, gcRangeAreas, LIMIT_MARK, gcLimitMarks, ZONE_DEFAULT, BAND_DEFAULT,
    gcPalette, gcContrast,
    gcOnColor, css, parseGlu2, gcFile, gcFileFailed, gcNewFailure,
  });
  // its version in the browser console, as each card file reports its own (once: a second load keeps the first module)
  console.info(`%c GLUCOCRAFT-SHARED %c v${GCSHARED_VERSION} `, "background:#7E57C2;color:#fff", "background:#000;color:#fff");
}
