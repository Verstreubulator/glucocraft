// License: AGPL-3.0-only (LICENSE).
/*
 * glucocraft-report - the CGM Report page: the same content and order as the PDF (report_pdf.py).
 * Data from /api/glucocraft/report (Home Assistant login), calculated by GlucoCraft (report.py); this card only lays
 * it out.
 *
 *   custom:glucocraft-report   period selector (14 / 30 / 90 days / 6 months), the 14-day CGM Report page, the
 *                              longer-term overview, hypoglycemia, comparison, laboratory A1C, PDF download, printing
 *
 * Consensus ranges only, in the time-in-ranges colors of the published report examples. Formal wording
 * (docs/LANGUAGE_STYLE.md). The page is called "CGM Report"; "AGP" appears only in citations and in the description
 * sentence. Besides the 14-day page it has the high-glucose section, the data quality section, the 26-week weekly trend
 * (90 days and 6 months), the full report's hypoglycemia events by period, the label "Sensor active (data received)",
 * the glucose sources by their names, and the two optional appendices (include_custom_appendix,
 * include_research_appendix; off by default), the same as the PDF (report_pdf.py). Every section has the PDF's layout:
 * a caption line with the rule and its source, the data, and Notes with the cited context that explains it.
 */
const GCREPORT_VERSION = "0.0.2";
// The language catalogues (glucocraft-i18n.js, served next to this file): every text comes from them, in Home
// Assistant's language with English as the fallback. The card waits for gcI18nLoad before its first render.
const GC_I18N_AREAS = ["frontend", "report"];
const GC_I18N = globalThis.glucocraftI18n ? Promise.resolve() : import(new URL("./glucocraft-i18n.js", import.meta.url).href);
const gcI18nLoad = (hass) => GC_I18N.then(() => globalThis.glucocraftI18n.load(hass, GC_I18N_AREAS));
const gcI18nReady = (hass) => !!globalThis.glucocraftI18n?.ready(hass, GC_I18N_AREAS);
const gcT = (key, params) => globalThis.glucocraftI18n.t(key, params);
// The rules the cards and pages share (glucocraft-shared.js, served next to this file and imported the same way): the
// settings in use and the number, glucose, time and date formats, the spoken text and gcRender. The file waits for it
// before it defines its page.
const GC_SHARED = globalThis.glucocraftShared ? Promise.resolve() : import(new URL("./glucocraft-shared.js", import.meta.url).href);
await GC_SHARED;
const { settings: GC, round, nf, dtx, gU, gD, gF, p2, clock, hour, esc, unesc, gcSpeakNumber, gcSpeak, gcSpeakLimit,
  gcSpeakText, gcRender, css: GC_CSS, MGDL_PER_MMOLL, BAND_DEFAULT, GC_CONS_GOAL, gcFit, gcNavigate,
  gcPlainClick, gcAxisText } = globalThis.glucocraftShared;
// The disclaimer (the catalogue's disclaimer.text, its first sentence in bold) stands once, in About This Report, as in
// the PDF; the last section, License, has the license notice.

// Every text of the page is in the catalogues: report_page.* (frontend.json) and the texts the page shares with the
// PDF (report.json). The cited context of the CGM Report (the sentences that explain a target, a limit or a rule from
// its published source, the same words as in the PDF) keeps its numbers in mg/dL and mmol/L as published, and rCtx
// below shows it with the display unit first; the other fixed texts name the thresholds by placeholders ({t70},
// {t70_180}, {unit}), which rL fills in the display unit; both with the decimal separator in use. In those texts a no-break space joins
// "Table" / "Recommendation" to its number (never wrapped, never given a decimal comma).
// a cited-context text as shown
const rCtxT = (key, params) => rCtx(gcT(key, params));
// the two sections of the ADA Standards of Care that the cited context names ({ada_a}, {ada_b}): author-date
// citations, not translated
const RADA = { ada_a: "American Diabetes Association Professional Practice Committee for Diabetes, 2026a",
  ada_b: "American Diabetes Association Professional Practice Committee for Diabetes, 2026b" };
// A link to a section of the Documentation page of GlucoCraft's dashboard: a real link with its address (Tab reaches
// it, and it can be opened in a new tab); the click handler of draw() opens it in the dashboard. The dashboard's path
// is fixed (strategy_dashboard.URL_PATH), so the link also works when this card is placed on another dashboard; the
// card's option `base` names another path, as on the pages card. While *Dashboard in the sidebar* is off, the
// GlucoCraft dashboard and its Documentation page do not exist. RT_BASE: the path of the card being drawn (each card
// sets its own before it draws, so that two cards with different paths each link to their own).
let RT_BASE = "/glucocraft-home";
const rDocUrl = (section) => `${RT_BASE}/documentation#${section}`;
const rDocLink = (act, key) => `<a class="lnk" href="${esc(rDocUrl(act))}" data-act="${act}">${gcT(key)}</a>`;
// the sensor-active label, wherever the percentage is shown alone (its explanation is shown once, under Data Quality)
const rSA = () => gcT("report.sensor_active.label");
// the report options (shared names, services.yaml): the option and the keys of its label and its description; the
// custom appendix is offered only while the optional feature Custom ranges and goals is on (offered())
const GCR_OPTS = [
  ["include_custom_appendix", "report_page.options.custom.label", "report_page.options.custom.description"],
  ["include_research_appendix", "report_page.options.research.label", "report_page.options.research.description"],
];
// a duration in whole minutes with the unit symbols, for the event summary table and the time per day beside a
// percentage: "45 min", "2 h 5 min", "3 d 4 h" (report.duration.*, the PDF's _dur; About This Report gives the key of
// the symbols, and the tables read them in words, rTables)
const rDur = (m) => {
  if (m === null || m === undefined) return "-";
  const h = Math.floor(m / 60);
  if (m < 60) return gcT("report.duration.min", { minutes: m });
  if (m < 1440) return m % 60 ? gcT("report.duration.h_min", { hours: h, minutes: m % 60 }) : gcT("report.duration.h", { hours: h });
  return gcT("report.duration.d_h", { days: Math.floor(m / 1440), hours: Math.floor((m % 1440) / 60) });
};
// the same duration in words, for running text: "45 minutes", "2 hours 5 minutes", "3 days 4 hours"
// (report.duration.words.*, the PDF's _dur_words)
const rDurW = (m) => {
  if (m === null || m === undefined) return "-";
  const words = (unit, n) => gcT(`report.duration.words.${unit}`, { count: n });
  const two = (larger, n, unit) => (n ? gcT("report.duration.join", { larger, smaller: words(unit, n) }) : larger);
  if (m < 60) return words("minute", m);
  if (m < 1440) return two(words("hour", Math.floor(m / 60)), m % 60, "minute");
  return two(words("day", Math.floor(m / 1440)), Math.floor((m % 1440) / 60), "hour");
};
// the time per day beside a percentage, " (1 h 12 min)" (report.per_day; 1% of time = 14.4 minutes per day, from the
// unrounded share, report.period_report per_day), or "" without a value; under: on a line of its own (a wide table).
// A whole day is "(24 h)": a time per day, not one day (the PDF's _per_day)
const rPerDay = (m, under = false) => (rHas(m)
  ? `${under ? "<br>" : " "}<span class="pd">${gcT("report.per_day", { duration: m >= 1440 ? gcT("report.duration.h", { hours: 24 }) : rDur(m) })}</span>` : "");
// Enough data: a period's values are shown only with at least 70% sensor active, compared before rounding (the report
// data's enough_data). The note of a period without enough data: "30 Days: not enough data yet. ... So far they cover
// 23%. With no gaps, about 14 more days are needed." (more_needed only for a period that ends with the report), or ""
// (the PDF's enough_note); the short line of the sections after it (the PDF's enough_short)
const rEnoughNote = (r, period) => {
  if (!r || r.enough_data !== false) return "";
  const more = r.more_needed;
  return gcT("report.enough.note", { period, active: rf(r.active) }) +
    (more ? ` ${gcT(`report.enough.more.${more[0]}`, { count: more[1] })}` : "");
};
const rEnoughShort = (active, days) => gcT("report.enough.short", { active: rf(active), count: days });
// a table's name for screen readers (ACCESSIBILITY.md rule 4): its section or chart title, as the PDF's /T
const rCaption = (text) => `<caption class="sr">${esc(text)}</caption>`;

// the time-in-ranges colors of the published report examples: the graph range colors (glucocraft-shared.js), in range
// as its two near bands
const RC = { vhigh: BAND_DEFAULT.very_high, high: BAND_DEFAULT.high, target: BAND_DEFAULT.near_low,
  tight: BAND_DEFAULT.tight_range, low: BAND_DEFAULT.low, vlow: BAND_DEFAULT.very_low };
// the color key: one small line at the bottom of the page, as on the other pages, with only the
// colors this page uses (the time-in-ranges colors, the gray in-range band and the amber edge of a data note) and a link to
// Documentation > Colors
const rColorKey = () => `<div class="sub" style="margin-top:14px">${gcT("report_page.color_key",
  { link: rDocLink("colors", "report_page.link.colors") })}</div>`;
// The consensus thresholds of the zones and the chart lines (mg/dL): very low below 54, low below 70, high above 180,
// very high above 250 (Battelino et al., 2019), from the backend's one table (const.AGP_RANGES, the PDF's too), sent
// with the report data as zones and kept here by draw (rSetZones)
let RZ = {};
const rSetZones = (z) => { RZ = { vlow: z.range_very_low, low: z.range_low, high: z.range_high, vhigh: z.range_very_high }; };
// the bands of the glucose profile, for clipping it into its zone colors (the boundaries are lines there)
const rZones = () => [[-1e9, RZ.vlow, "vlow"], [RZ.vlow, RZ.low, "low"], [RZ.low, RZ.high, "target"],
  [RZ.high, RZ.vhigh, "high"], [RZ.vhigh, 1e9, "vhigh"]];
// the zone of a value (mg/dL) as every statistic counts it (statistics.py; report_pdf.zone_key): in range 70 to 180
// inclusive, high above 180 to 250 inclusive (Battelino et al., 2019)
const rZone = (mg) => (mg < RZ.vlow ? "vlow" : mg < RZ.low ? "low" : mg <= RZ.high ? "target" : mg <= RZ.vhigh ? "high" : "vhigh");
// the legend of a table with short column heads: one item per column (report.legend.<key>), in the order of the
// columns, in the display unit (the same items as the PDF, report_pdf._legend)
const rLegend = (...keys) => `<div class="sub">${rSay(rL(gcT("report.legend.text", { items: keys.map((k) => gcT(`report.legend.${k}`,
  { unit: gU(), sensor_active: gcT("report.sensor_active.in_sentence") })).join(gcT("report.list_separator")) })))}</div>`;
const RDAYS = [[14, "report.period.14"], [30, "report.period.30"], [90, "report.period.90"],
  [180, "report.period.180"]];
// the weekday names, Monday first, as the column heads of a calendar that starts at the first day of the week setting
const RWEEKDAYS = ["card.date.weekday_short.monday", "card.date.weekday_short.tuesday", "card.date.weekday_short.wednesday",
  "card.date.weekday_short.thursday", "card.date.weekday_short.friday", "card.date.weekday_short.saturday",
  "card.date.weekday_short.sunday"];
const rWeekHead = () => RWEEKDAYS.map((_, i) => gcT(RWEEKDAYS[(i + RT_WEEK1) % 7]));
// a table that scrolls sideways on a narrow screen: a region the keyboard reaches (WCAG 2.1.1; Safari does not scroll an
// unfocusable box with the keys), named by the heading before it (id)
const rScroll = (id, table) => `<div class="scroll" tabindex="0" role="region" aria-labelledby="${id}">${table}</div>`;
// a visible text with unit symbols (a table cell, a label) as HTML: shown as written and hidden from screen readers,
// which read its spoken form instead; sr: a spoken form of the caller's own
const rSay = (text, sr = String(text).trim() === "-" ? gcT("spoken.not_available") : gcSpeakText(text)) => (sr === String(text) ? esc(text)
  : `<span aria-hidden="true">${esc(text)}</span><span class="sr">${esc(sr)}</span>`);
// The report's tables for screen readers (ACCESSIBILITY.md rule 4): the first cell of each row of a results table
// (class "t") becomes its row header, and every cell text with a unit symbol ("71%", "102 mg/dL", "14 d") is shown as
// written and read in words (rSay). Text already split into its shown and spoken forms is left as it is.
const rTables = (html) => html.replace(/<table[\s\S]*?<\/table>/g, (table) => {
  let t = table;
  if (/^<table class="t[ "]/.test(t)) t = t.replace(/<tr><td( class="[^"]*")?>([\s\S]*?)<\/td>/g, '<tr><th scope="row"$1>$2</th>');
  return t.replace(/(<[^>]+>)([^<]+)(?=<)/g, (m, tag, text) => (/aria-hidden|class="sr"|<(?:style|button)/.test(tag) || !text.trim()
    ? m : tag + rSay(unesc(text))));
});
// the headers of the time-below and time-above columns: shown as "<70" (the threshold in the display unit), read as
// "Time below 70 milligrams per deciliter"
const rBandHeads = () => [["<", RZ.low, "report_page.band_head.below"], ["<", RZ.vlow, "report_page.band_head.below"],
  [">", RZ.high, "report_page.band_head.above"], [">", RZ.vhigh, "report_page.band_head.above"]]
  .map(([op, mg, key]) => `<th scope="col">${rSay(`${op}${rG(mg)}`, gcT(key, { value: gcSpeakText(`${rG(mg)} ${gU()}`) }))}</th>`).join("");
// Regional settings (resolved values from the report data or sensor.glucocraft_configuration; regional.py): the
// decimal separator, the A1C / GMI unit, the time and date formats and the display unit are glucocraft-shared.js's
// settings (written by draw from the report data); the first day of the week (0 = Monday ... 6 = Sunday) is this
// page's own. Display only: never in the data, the API or parsing (the laboratory A1C is entered with its own unit).
let RT_WEEK1 = 0;
// The numbers of the report data are shown as GlucoCraft rounded them (report.py); nf (glucocraft-shared.js) rounds
// the page's own numbers once, and dtx (glucocraft-shared.js) applies the decimal separator to a value or text that
// already contains numbers (values from the report data, fixed texts)
// GMI and laboratory A1C in the A1C unit. mm: the value in mmol/mol from the report data, which GlucoCraft calculates
// for every GMI from the unrounded mean glucose (Bergenstal et al., 2018), for a laboratory result entered in percent
// by the IFCC-NGSP master equation (Hoelzel et al., 2004) and keeps for one entered in mmol/mol, and for the custom GMI
// goal by the GMI rule (report.custom_appendix goal_mmol_mol): this page converts nothing, so that it and the PDF show
// the same number. A value without it shows its percent alone, or "-" in mmol/mol alone.
const rHas = (v) => v !== null && v !== undefined && !isNaN(v);
const rA1c = (pct, mm) => {
  if (!rHas(pct)) return "-";
  const m = rHas(mm) ? `${round(+mm)} mmol/mol` : null;
  if (GC.a1c === "mmol_mol") return m || "-";
  return GC.a1c === "both" && m ? `${nf(pct, 1)}% (${m})` : `${nf(pct, 1)}%`;
};
// a difference of two A1C / GMI values: d in percentage points (shown with the words "percentage points"); dmm, the
// difference of the two values in mmol/mol from the report data (rMmDiff). cell: the laboratory table's cell, which
// with both units writes the points without their words, as the PDF's (the section's Notes name the unit)
const rA1cDiff = (d, dmm = null, cell = false) => {
  if (!rHas(d)) return "-";
  // rounded first, then signed: a difference shown as 0.0 has no sign (the PDF's _signed)
  const r1 = round(+d, 1), sg = (x) => (x > 0 ? "+" : "");
  const mm = rHas(dmm) ? `${sg(round(+dmm))}${round(+dmm)} mmol/mol` : null;
  const pts = cell && GC.a1c === "both" ? `${sg(r1)}${nf(r1, 1)}` : gcT("report_page.lab.percentage_points", { value: `${sg(r1)}${nf(r1, 1)}` });
  if (GC.a1c === "mmol_mol") return mm || "-";
  return GC.a1c === "both" && mm ? `${pts} (${mm})` : pts;
};
// the difference of two values in mmol/mol, or null when one is missing
const rMmDiff = (a, b) => (rHas(a) && rHas(b) ? round(+a) - round(+b) : null);
// Glucose in the display unit (data["unit"]; glucocraft-shared.js gF: mg/dL whole numbers, mmol/L one decimal, the
// consensus thresholds as published); "-" for no value
const rG = (mg) => gF(mg, "-");
const rGu = (mg) => (mg === null || mg === undefined || isNaN(mg) ? "-" : `${rG(mg)} ${gU()}`);
// a fixed text with its threshold placeholders ({t70}, {t70_180}, ...) and {unit} filled in the report's unit, from the
// one table the PDF uses too (units.REPORT_THRESHOLDS, sent as data.thresholds and kept in RT_THR by draw), with the
// decimal separator in use; every other number of the text is left as written
let RT_THR = {};
const rL = (t) => dtx(String(t).replace(/\{(t\d+(?:_\d+)?|unit)\}/g, (m, k) => (Object.hasOwn(RT_THR, k) ? RT_THR[k] : m)));
// a cited text as shown: in mmol/L the paired thresholds with the display unit first ("10.0 mmol/L (180 mg/dL)"), both
// units as published (LANGUAGE_STYLE rule 11); the decimal separator in use
const rCtx = (t) => dtx(GC.mmol ? String(t).replace(/(\d+(?:–\d+)?) mg\/dL \((\d+(?:\.\d)?(?:–\d+(?:\.\d)?)?) mmol\/L\)/g, "$2 mmol/L ($1 mg/dL)") : t);
const rf = (v, u = "%") => (v === null || v === undefined ? "-" : `${dtx(v)}${u}`);
// Time and date formats of this report (data time_format / date_format; regional.py): a clock time "06:00" / "6:00 AM"
// (glucocraft-shared.js clock), an hour label (hour), a date
const rDate = (s, short) => {   // "Wed 10/22/2026" / "Wed 22/10/2026" / "Wed 2026-10-22"; short: "10/22" / "22/10" / "10-22"
  if (!s) return "-";
  const d = new Date(`${s}T12:00:00`), m = d.getMonth() + 1, dd = d.getDate(), y = d.getFullYear();
  const num = GC.date === "DMY" ? (short ? `${dd}/${m}` : `${p2(dd)}/${p2(m)}/${y}`) : GC.date === "YMD" ? (short ? `${p2(m)}-${p2(dd)}` : `${y}-${p2(m)}-${p2(dd)}`)
    : (short ? `${m}/${dd}` : `${p2(m)}/${p2(dd)}/${y}`);
  // the weekday's short name from the catalogue (RWEEKDAYS is Monday first; getDay() is 0 on Sunday)
  return short ? num : `${gcT(RWEEKDAYS[(d.getDay() + 6) % 7])} ${num}`;
};
const rHours = (step, n) => Array.from({ length: n }, (_, i) => hour(i * step));
// the time-axis ticks of a chart w pixels wide, midnight to midnight: [[fraction of the width, label]], every 3 hours,
// or every 6 hours where 3-hour labels of 14 px would touch
const rTicks = (w) => { const step = w / 8 >= 46 ? 3 : 6, n = 24 / step + 1; return rHours(step, n).map((t, i) => [i / (n - 1), t]); };
// the smallest spread of a glucose chart's axis (mg/dL); the scale itself is every graph's (glucocraft-shared.js gcFit)
const R_MIN_SPREAD = 20;

// The readings at the sensor's limit that a chart marks (runs: [[first slot, slot after the last, "low" | "high"]],
// report.period_report profile_limits / daily_limits), as the sentence its description adds, or "" (the PDF's
// _alt_limits)
const rAltLimits = (runs) => {
  const kinds = ["low", "high"].filter((k) => (runs || []).some((run) => run[2] === k)).map((k) => gcSpeakLimit(k));
  return kinds.length ? gcT("report.alt.limits", { kinds: kinds.join(gcT("report.list_separator")) }) : "";
};
// The marks of those runs inside an SVG whose x runs from x0 over w for `slots` slots and whose plot area has its
// bottom at yb and its top at yt (the PDF's Report.limit_marks): a run of short marks along the bottom (LO) or top (HI)
// edge in the very-low or very-high color, at least 3 units long. Such a reading has no value: the line still breaks.
const rLimitMarks = (runs, x0, w, yb, yt, slots, sw = 3.2, nonScaling = false) => (runs || []).map(([a, b, kind]) => {
  let x1 = x0 + a / slots * w, x2 = x0 + b / slots * w;
  if (x2 - x1 < 3) { const m = (x1 + x2) / 2; x1 = Math.max(x0, m - 1.5); x2 = Math.min(x0 + w, m + 1.5); }
  const y = kind === "low" ? yb - sw / 2 : yt + sw / 2, color = kind === "low" ? RC.vlow : RC.vhigh;
  const scale = nonScaling ? "non-scaling-stroke" : "none";
  return `<line x1="${x1.toFixed(1)}" x2="${x2.toFixed(1)}" y1="${y}" y2="${y}" stroke="${color}" stroke-width="${sw}" stroke-dasharray="2 1.5" vector-effect="${scale}"/>`;
}).join("");
// the letters LO / HI once per run, where they do not touch the previous letters of the same edge: [[x, kind]], x as a
// share of the width (0 to 1); minGap: the share one label takes
const rLimitLabels = (runs, slots, minGap) => {
  const last = { low: -1, high: -1 }, out = [];
  (runs || []).forEach(([a, , kind]) => {
    const f = Math.min(a / slots, 1 - minGap);
    if (f >= last[kind] + minGap) { out.push([f, kind]); last[kind] = f; }
  });
  return out;
};

// The note on LO / HI readings, and the line drawing of the daily profiles. The data gaps of the profiles come from
// the report data (daily_breaks), calculated by GlucoCraft with the one gap rule (gaps.py).
// a line through 10-minute means; brk(a, b) = true where the line breaks between bins a < b; pred = which bins belong
// to the line (a bin with a value outside pred ends it)
const rLine = (vals, sx, sy, brk, pred = () => true) => {
  let d = "", last = -1;
  vals.forEach((v, i) => {
    if (v === null || v === undefined) return;
    if (!pred(v)) { last = -1; return; }
    d += `${last >= 0 && !brk(last, i) ? "L" : "M"}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`;
    last = i;
  });
  return d;
};
// The readings at the sensor's limit with their time: ["LO 1 (5 minutes)", ...], the PDF's text (report_pdf._limit_parts)
const rLimitParts = (lr, ls) => [["low", +(lr?.low) || 0, +(ls?.low) || 0], ["high", +(lr?.high) || 0, +(ls?.high) || 0]]
  .filter(([, n]) => n).map(([k, n, sec]) => gcT("report.limit_part", { limit: gcT(`report.limit.${k}`), count: n, duration: rDurW(Math.ceil(sec / 60)) }));
// "2 readings at the sensor's limit (LO 1 (5 minutes); HI 1 (5 minutes)), left out of ..." or "" (none), the PDF's text
// (report_pdf._limit_note)
const rLimitNote = (lr, ls) => {
  const parts = rLimitParts(lr, ls);
  return parts.length ? gcT("report.limit_note", { count: (+(lr?.low) || 0) + (+(lr?.high) || 0), parts: parts.join(gcT("report.list_separator")) }) : "";
};

// The layout of every section, the same as the PDF's (report_pdf.Report.notes): a caption line that names the rule and
// its source, the data, and a Notes block at the section's end with the full cited explanation. items: [lead, text]
// paragraphs (lead: a bold label or null; an empty text is left out); the texts are the catalogue's, with the values
// of the report data escaped where they are put in. An item that is a string is HTML of its own (a list).
const rCap = (text) => `<div class="sub">${text}</div>`;
const rNotes = (items) => `<div class="notes"><h3 class="h3">${gcT("report.notes.title")}</h3>${items
  .map((it) => (typeof it === "string" ? it : it[1] ? `<p>${it[0] ? `<b>${esc(it[0])}</b> ` : ""}${it[1]}</p>` : "")).join("")}</div>`;
// The glucose sources by their names, the primary first (the report data's sources; report_pdf.source_line): the line
// under the title, as in the PDF's page header, and About This Report's item (report_pdf.data_sources)
const rSourceLine = (d) => { const s = d.sources?.length ? d.sources : ["-"];
  return s.length > 1 ? gcT("report.header.data_two", { primary: esc(s[0]), second: esc(s[1]) })
    : gcT("report.header.data", { primary: esc(s[0]) }); };
const rDataSources = (d) => {
  const s = d.sources?.length ? d.sources : ["-"];
  if (s.length < 2) return [gcT("report.about.source.lead"), gcT("report.about.source.body", { name: esc(s[0]) })];
  // a second source without measurement times is used only after the primary has been silent (second_source_timed)
  const text = gcT(d.second_source_timed === false ? "report.about.sources.body_untimed" : "report.about.sources.body",
    { primary: esc(s[0]), second: esc(s[1]) });
  return [gcT("report.about.sources.lead"), rHas(d.second_source_pct)
    ? `${text} ${gcT("report.about.sources.share", { share: rf(d.second_source_pct) })}` : text];
};

// a column header
const th = (key, params) => `<th scope="col">${gcT(key, params)}</th>`;
// a consensus target of a percentage as written in the report's tables (such as <4%), from the one table of the targets
// (glucocraft-shared.js GC_CONS_GOAL, by goal key); the CV's target has a text of its own (report.overview.target.cv)
const rTarget = (k) => `${GC_CONS_GOAL[k][0]}${GC_CONS_GOAL[k][1]}%`;
// the consensus target of a time-in-ranges row ("Consensus target <5%")
const tgt = (k) => gcT("report.tir.target", { target: esc(rTarget(k)) });
// the consensus target of each column of the longer-term overview, in its order (TIR, TITR, <70, <54, >180, >250,
// mean, SD, CV, GMI, GRI, lowest, highest), as the PDF's overview lists them (report_pdf.Report.overview)
const OVERVIEW_TARGETS = () => {
  const none = gcT("report.overview.target.none");
  return [rTarget("time_in_range"), none, rTarget("time_low"), rTarget("time_very_low"), rTarget("time_high"),
    rTarget("time_very_high"), none, none, gcT("report.overview.target.cv"), none, none, none, none];
};
// the night and the day of the Hypoglycemia and Hyperglycemia sections, in the time format in use, and the columns of
// their tables (the first column: the unit of the thresholds in the row labels)
const rNightDay = () => [`${clock(0)}–${clock(5, 59)}`, `${clock(6)}–${clock(23, 59)}`];
const rNightDayHead = () => { const [night, day] = rNightDay();
  return `<tr>${th("report.table.thresholds", { unit: gU() })}${th("report.table.night", { time: night })}` +
    `${th("report.table.day", { time: day })}${th("report.table.whole_period")}${th("report.table.consensus_target")}</tr>`; };

// the page's style sheet (draw writes it once, with the frame of the page)
const GCR_STYLE = `<style>
        :host { display: block; --gcr-grid: var(--divider-color, rgba(127,127,127,.3)); --gcr-fg2: var(--secondary-text-color, #888);
                --gcr-band: rgba(127,127,127,.12); }
        ha-card { padding: 14px 16px; color: var(--primary-text-color); font-size: 14px; line-height: 1.45; }
        .h2 { font-size: 18px; font-weight: 600; margin: 4px 0 6px; } .h2.sep { margin-top: 22px; padding-top: 10px; border-top: 1px solid var(--divider-color); }
        .h3 { font-size: 15px; font-weight: 600; margin: 14px 0 2px; }
        .sub { font-size: 14px; color: var(--secondary-text-color); margin: 2px 0 6px; } .sub p { margin: 4px 0; }
        .warn { border-left: 6px solid #F9A825; background: rgba(249,168,37,.1); padding: 8px 12px; margin: 8px 0; font-size: 14px; }
        .status { font-size: 14px; margin: 6px 0; padding: 6px 10px; background: var(--secondary-background-color); border-radius: 6px; }
        .status.err { border-left: 6px solid #E53935; }
        /* an empty message takes no room, and stays in the page so that its next text is announced */
        .status:empty { margin: 0; padding: 0; border: 0; }
        .ctl { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 4px; }
        .seg { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 8px; overflow: hidden; }
        button { font: inherit; font-size: 14px; padding: 6px 10px; border: 0; background: transparent; color: var(--primary-text-color); cursor: pointer; }
        .seg button + button { border-left: 1px solid var(--divider-color); }
        button.on { background: ${GC_CSS.fill}; color: ${GC_CSS.onFill}; font-weight: 600; }
        /* the chosen button of a group: a check mark besides the color (its state is announced by aria-pressed, so the
           mark itself is not read) */
        button[aria-pressed="true"]::before { content: "✓\u00a0"; content: "✓\u00a0" / ""; }
        button.act { border: 1px solid var(--divider-color); border-radius: 8px; }
        button:disabled { opacity: .45; cursor: default; }
        .out { border: 1px solid var(--divider-color); border-radius: 10px; padding: 2px 12px 4px; margin: 8px 0; }
        .out .h3 { margin-top: 8px; margin-bottom: 6px; }
        button.sm { font-size: 14px; padding: 2px 8px; min-height: 24px; border: 1px solid var(--divider-color); border-radius: 6px; }
        .grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr)); gap: 16px; }
        .tir { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-start; }
        table.t { border-collapse: collapse; font-size: 14px; margin: 6px 0; } table.t td, table.t th { padding: 3px 8px 3px 0; text-align: left; vertical-align: top; }
        table.t th { color: var(--secondary-text-color); font-weight: 500; font-size: 14px; }
        table.t th[scope="row"] { color: inherit; font-weight: 400; }
        /* text read by screen readers only (ACCESSIBILITY.md rule 4): not shown, but in the reading order */
        ${GC_CSS.sr} table.wide td, table.wide th { padding-right: 12px; white-space: nowrap; }
        td.n { color: var(--secondary-text-color); font-size: 14px; } .pd { color: var(--secondary-text-color); white-space: nowrap; }
        .sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; vertical-align: -1px; }
        /* positioned, so that the screen-reader texts (.sr, absolutely positioned) of a scrolling table stay inside it */
        .scroll { overflow-x: auto; position: relative; }
        .scroll:focus-visible { ${GC_CSS.focus} }
        svg.chart { width: 100%; height: auto; display: block; } svg text { font-size: 14px; fill: var(--secondary-text-color); font-family: inherit; }
        /* the daily profiles: one calendar week per row, scrolling sideways where seven days do not fit */
        .days { display: grid; grid-template-columns: repeat(7, minmax(92px, 1fr)); gap: 6px; }
        .day .dw { position: relative; }
        .day svg { width: 100%; height: 60px; display: block; border: 1px solid var(--divider-color); }
        /* the letters LO / HI of a run of readings at the sensor's limit, at the chart's bottom or top edge */
        .lim { position: absolute; font-size: 14px; font-weight: 700; line-height: 1; color: var(--primary-text-color); }
        .lim.lo { bottom: 5px; } .lim.hi { top: 5px; }
        svg text.lim { fill: var(--primary-text-color); font-weight: 700; }
        .dl { font-size: 14px; font-weight: 600; color: var(--secondary-text-color); }
        .labform { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-top: 6px; font-size: 14px; }
        .labform input { font: inherit; padding: 4px 6px; background: var(--card-background-color); color: var(--primary-text-color); border: 1px solid var(--divider-color); border-radius: 6px; }
        .labform button { border: 1px solid var(--divider-color); border-radius: 6px; }
        .labform input[aria-invalid="true"] { border: 2px solid #E53935; }
        a.lnk { color: ${GC_CSS.link}; cursor: pointer; text-decoration: underline; }
        button:focus-visible, a:focus-visible, input:focus-visible { ${GC_CSS.focus} }
        .intro { font-size: 14px; margin: 0 0 10px; padding: 8px 12px; border-left: 6px solid var(--divider-color); background: var(--secondary-background-color); border-radius: 6px; }
        .log { border-collapse: separate; border-spacing: 1px; } .log td, .log th { min-width: 36px; text-align: center; font-size: 14px; padding: 2px 1px; line-height: 1.2; font-weight: 400; }
        .log .dl2 { width: auto; text-align: left; font-weight: 600; padding-right: 6px; white-space: nowrap; }
        .cal { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(260px, 100%), 1fr)); gap: 14px; }
        .cal table { width: 100%; table-layout: fixed; border-collapse: collapse; } .cal td { border: 1px solid var(--divider-color); height: 44px; vertical-align: top; font-size: 14px; padding: 2px; overflow-wrap: anywhere; }
        .cal td b { display: block; text-align: center; font-size: 15px; } .cal td i { float: right; font-style: normal; font-weight: 600; }
        table.pat { width: 100%; min-width: 600px; table-layout: fixed; border-collapse: separate; border-spacing: 2px; }
        table.pat th, table.pat td { text-align: center; border: 1px solid var(--divider-color); padding: 3px 0; font-size: 14px; font-weight: 400; }
        table.pat b { font-size: 16px; }
        .opts { display: grid; gap: 4px; margin: 4px 0 6px; font-size: 14px; } .opts label { cursor: pointer; } .opts .sub { display: block; margin: 0 0 0 24px; }
        .grid3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(200px, 100%), 1fr)); gap: 10px; } .h3.small { font-size: 14px; margin-top: 6px; }
        table.strip { width: 100%; table-layout: fixed; border-collapse: collapse; } table.strip th { font-size: 14px; color: var(--secondary-text-color); font-weight: 500; }
        table.strip td { border: 1px solid var(--divider-color); height: 40px; vertical-align: top; font-size: 14px; padding: 2px; overflow-wrap: anywhere; }
        table.strip td i { font-style: normal; color: var(--primary-text-color); } table.strip td b { display: block; text-align: right; font-size: 14px; }
        table.strip td.nod { background: var(--gcr-band); }
        p.plain, ol.plain { font-size: 14px; margin: 4px 0; } ol.plain li { margin: 2px 0; }
        /* the Notes at a section's end: set apart by a rule at the left and the secondary color (the page's text is
           14 px at the smallest, so they are not set smaller, as in the PDF) */
        .notes { margin: 10px 0 4px; padding: 0 0 0 10px; border-left: 3px solid var(--divider-color); font-size: 14px;
                 color: var(--secondary-text-color); } .notes p { margin: 4px 0; } .notes .h3 { font-size: 14px; margin: 4px 0 2px; }
      </style>`;

/** The CGM Report page (custom:glucocraft-report): loads the report data and draws every section of the report. */
class GlucoCraftReport extends HTMLElement {
  setConfig(config) {
    this._cfg = config || {};
    // the path of GlucoCraft's dashboard, whose Documentation page the links open (the option `base`), kept per card;
    // RT_BASE is set here so that a link works before the first drawing, and applySettings sets it from this card's
    // base again before each drawing
    this._base = String(this._cfg.base || "/glucocraft-home").replace(/\/$/, "");
    RT_BASE = this._base;
    this._days = Number(this._cfg.days) || 14;
    this._full = false;
    this._color = true;
    this._two = true;   // two-sided printing where the printer supports it (the print action's default)
    this._opts = { include_custom_appendix: false, include_research_appendix: false };   // both off by default
    this._data = null;
    // the messages of the page (WCAG 4.1.3): _status, read politely (progress and results), and _alert, read at once
    // (an error after the user's action); _lab, the laboratory entry with its invalid fields
    this._status = "";
    this._alert = "";
    this._lab = { date: "", a1c: "", unit: "", invalid: [] };
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }
  set hass(hass) {
    this._hass = hass;
    // the language catalogues first (also after a change of the language): nothing is drawn before they are loaded
    if (!gcI18nReady(hass)) {
      if (!this._i18nWait) this._i18nWait = gcI18nLoad(hass).then(() => { this._i18nWait = null; this.hass = this._hass; });
      return;
    }
    if (!this._started) { this._started = true; this._offer = this.offered().length; this.load(); return; }
    // the optional feature Custom ranges and goals turned on or off: the report is loaded again with the appendices
    // that are offered now
    if (this.offered().length !== this._offer) { this._offer = this.offered().length; this._data = null; this.load(); return; }
    const sig = this.intlSig();   // a changed setting or language: drawn again
    if (this._intlSig !== undefined && sig !== this._intlSig && this._data) this.draw();
    this._intlSig = sig;
  }
  // a setting the page is drawn with: the report data's first (the backend adds them), else the configuration sensor's
  pick(k) {
    const d = this._data || {};
    return d[k] !== undefined && d[k] !== null ? d[k] : this.cfgA()[k];
  }
  // the settings and the language the page is drawn with, as resolved: the Configuration sensor's attributes, which
  // are absent while GlucoCraft reloads, change it only when no report data are loaded
  intlSig() {
    return [this.pick("decimal_separator"), this.pick("a1c_unit"), this.pick("first_weekday"),
      globalThis.glucocraftI18n.language].join("|");
  }
  intl() {   // the resolved settings
    const ds = this.pick("decimal_separator"), au = this.pick("a1c_unit"), fw = this.pick("first_weekday");
    GC.dec = ds === "," ? "," : ".";
    GC.a1c = ["percent", "mmol_mol", "both"].includes(au) ? au : "percent";
    RT_WEEK1 = Number.isInteger(fw) && fw >= 0 && fw <= 6 ? fw : 0;   // Monday when absent
    this._intlSig = this.intlSig();
  }
  cfgA() { return this._hass?.states["sensor.glucocraft_configuration"]?.attributes || {}; }
  getCardSize() { return 20; }
  getGridOptions() { return { columns: "full" }; }

  // The charts are drawn at the width they are shown at (one SVG unit = one pixel), so that their 14 px labels stay
  // 14 px on a phone and on a wide screen. _cw is the width of the card's content box, measured at each draw; the page
  // is drawn again when that width changes by a whole pixel.
  connectedCallback() {
    if (!this._ro && window.ResizeObserver) {
      this._ro = new ResizeObserver(() => {
        if (this._data && Math.floor(this.contentWidth()) !== Math.floor(this._cw || 0)) this.draw();
      });
    }
    this._ro?.observe(this);
  }
  disconnectedCallback() { this._ro?.disconnect(); }
  // the content box of ha-card: its clientWidth (inside its border) less its 16 px padding on each side; before the
  // card is drawn, the element's width less the card's default 1 px border on each side
  contentWidth() {
    const inner = this.shadowRoot?.querySelector("ha-card")?.clientWidth || Math.max(0, (this.clientWidth || 0) - 2);
    return Math.max(0, inner - 32);
  }
  // the charts' width in whole pixels, never wider than the content box (a wider drawing would be scaled down with its
  // text); at least 240 px (a 320 px wide screen leaves a content box of about 270 px), 700 px before the first layout
  chartW() { return Math.max(240, Math.floor(this._cw || 700)); }
  // the width of one of the weekly trend's small charts: up to three side by side, each at least 200 px (.grid3)
  miniW() {
    const cw = this.chartW(), cols = Math.max(1, Math.min(3, Math.floor((cw + 10) / 210)));
    return Math.floor((cw - 10 * (cols - 1)) / cols);
  }

  /** Fetch the report data for the chosen period and options, then draw the page. */
  async load() {
    const seq = (this._seq = (this._seq || 0) + 1);   // only the latest request may set the page
    this._loading = true;
    this.draw();
    let data = null, error = "";
    try {
      const r = await this._hass.fetchWithAuth(`/api/glucocraft/report?days=${this._days}&full=${this._full ? 1 : 0}${this.optQuery()}`, { cache: "no-cache" });
      if (!r.ok) {
        // a refusal (400) carries its own text, shown as it is: a report period that does not exist, an appendix
        // that is not available; any other failure is named with its status
        const text = r.status === 400 ? await r.text().catch(() => "") : "";
        throw Object.assign(new Error(text || `HTTP ${r.status}`), { refused: !!text });
      }
      data = await r.json();
    } catch (e) {
      error = e.refused ? e.message : gcT("report_page.status.load_failed", { error: e.message });
    }
    if (seq !== this._seq) return;                    // a period chosen since: its own request draws the page
    if (data) this._data = data;
    this._error = error;
    this._alert = error;
    this._loading = false;
    this.draw();
  }

  // the appendices offered: the custom appendix only while Custom ranges and goals is on (configuration
  // custom_ranges_and_goals; GlucoCraft refuses it otherwise)
  offered() {
    const on = this.cfgA().custom_ranges_and_goals;
    if (typeof on === "boolean") this._customOn = on;   // kept while the configuration sensor is away (a reload)
    return GCR_OPTS.filter(([k]) => k !== "include_custom_appendix" || this._customOn !== false);
  }
  // the chosen appendices among those offered, by their shared names
  chosen() { return Object.fromEntries(this.offered().map(([k]) => [k, !!this._opts[k]])); }
  // the chosen appendices as query parameters (shared names; only those chosen)
  optQuery() { return Object.entries(this.chosen()).filter(([, v]) => v).map(([k]) => `&${k}=1`).join(""); }

  /** Create the PDF on the server and download it. */
  async pdf() {
    this._status = gcT("report_page.status.pdf_creating");
    this._alert = "";
    this.draw();
    try {
      const r = await this._hass.fetchWithAuth(`/api/glucocraft/report.pdf?days=${this._days}&full=${this._full ? 1 : 0}&color=${this._color ? 1 : 0}${this.optQuery()}`);
      if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
      const blob = await r.blob();
      const name = (r.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || "glucocraft-report.pdf";
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = name;
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
      this._status = gcT("report_page.status.pdf_created", { name });
    } catch (e) {
      this._status = "";
      this._alert = gcT("report_page.status.pdf_failed", { error: e.message });
    }
    this.draw();
  }

  /** Send the report to the configured network printer (service glucocraft.print_report). */
  async print() {
    this._status = gcT("report_page.status.printing");
    this._alert = "";
    this.draw();
    try {
      const res = await this._hass.callWS({ type: "call_service", domain: "glucocraft", service: "print_report",
        service_data: { days: this._days, report: this._full ? "full" : "summary", color: this._color, two_sided: this._two,
          ...this.chosen() }, return_response: true });
      this._status = res?.response?.message || gcT("report_page.status.sent");
    } catch (e) {
      this._status = "";
      this._alert = gcT("report_page.status.print_failed", { error: e.message || e.code || e });
    }
    this.draw();
  }

  /** Add or remove a laboratory A1C result (unit: percent or mmol_mol, services.yaml), then reload the report. */
  async lab(action, date, a1c, unit) {
    this._alert = "";
    try {
      await this._hass.callService("glucocraft", action, action === "add_lab_a1c" ? { date, a1c, unit } : { date });
      if (action === "add_lab_a1c") this._lab = { date: "", a1c: "", unit, invalid: [] };   // saved: the entry is cleared
      await this.load();
    } catch (e) {
      // a result the action refuses (lab_invalid): its reason only, which Home Assistant passes as the error's
      // translation placeholder; any other failure: Home Assistant's message
      const invalid = e && e.translation_key === "lab_invalid" && e.translation_placeholders
        && e.translation_placeholders.error;
      this._alert = gcT("report_page.status.lab_failed", { error: invalid || (e && e.message) || e });
      this.draw();
    }
  }

  // ------------------------------------------------------------------ pieces
  /** Return the stacked time-in-ranges bar as SVG. */
  tirBar(r) {
    const v = [["vlow", r.very_low], ["low", r.low], ["tight", r.titr], ["target", (r.tir || 0) - (r.titr || 0)], ["high", r.high], ["vhigh", r.very_high]];
    // each band at least 3% high, so that it stays visible; without any value (no data) the bar is its frame only
    const empty = [r.very_low, r.low, r.titr, r.tir, r.high, r.very_high].every((x) => !rHas(x));
    const disp = v.map(([, x]) => (empty ? 0 : Math.max(Math.max(x || 0, 0), 3))), tot = disp.reduce((a, b) => a + b, 0) || 1;
    let y = 200, out = empty ? `<rect x="0.5" y="0.5" width="43" height="199" fill="none" stroke="var(--gcr-fg2)"/>` : "";
    v.forEach(([k], i) => { if (!disp[i]) return; const h = disp[i] / tot * 200; y -= h; out += `<rect x="0" y="${y}" width="44" height="${h}" fill="${RC[k]}" stroke="#fff" stroke-width="1"/>`; });
    // the chart's one-sentence description (ACCESSIBILITY.md rule 4: charts are never silent), from the values drawn
    const pct = (x) => (rHas(x) ? gcSpeak(+x, "%", { dec: GC.dec }) : gcT("spoken.not_available"));
    const said = gcT("report.alt.tir", { very_low: pct(r.very_low), low: pct(r.low), in_range: pct(r.tir),
      tight: pct(r.titr), high: pct(r.high), very_high: pct(r.very_high) });
    return `<svg viewBox="0 0 44 200" width="44" height="200" role="img" aria-label="${esc(said)}">${out}</svg>`;
  }
  // a glucose value of the report data (mg/dL) as spoken, in the display unit: "62 milligrams per deciliter"
  rSpk(mg) { return rHas(mg) ? gcSpeakText(`${rG(mg)} ${gU()}`) : gcT("spoken.not_available"); }

  /**
   * Return the glucose profile chart (median, 25th-75th and 5th-95th percentile bands) as HTML. caption: true for
   * the 14-day profile's caption (the lines and bands drawn, as in the PDF); the period's profile has none (its
   * section's Notes describe it).
   */
  agp(r, title, h = 240, caption = false) {
    const p = r.profile || {};
    const all = ["p5", "p25", "p50", "p75", "p95"].flatMap((k) => (p[k] || []).filter((x) => x !== null));
    if (!all.length) return `<div class="sub">${gcT("report_page.chart.insufficient", { title: esc(title) })}</div>`;
    const [lo, hi] = gcFit(Math.min(...all), Math.max(...all), R_MIN_SPREAD);
    const W = this.chartW(), X = 40, w = W - X - 44;
    const sy = (v) => 10 + (hi - v) / (hi - lo) * h, sx = (i) => X + (i / 4 + 0.125) / 24 * w;
    let s = `<rect x="${X}" y="${sy(Math.min(RZ.high, hi))}" width="${w}" height="${Math.max(0, sy(Math.max(RZ.low, lo)) - sy(Math.min(RZ.high, hi)))}" fill="var(--gcr-band)"/>`;
    const band = (a, b) => {
      const up = [], dn = [];
      for (let i = 0; i < 96; i++) { if (p[b]?.[i] !== null && p[b]?.[i] !== undefined) up.push(`${sx(i)},${sy(p[b][i])}`); }
      for (let i = 95; i >= 0; i--) { if (p[a]?.[i] !== null && p[a]?.[i] !== undefined) dn.push(`${sx(i)},${sy(p[a][i])}`); }
      return up.length > 1 && dn.length > 1 ? `M${up.join("L")}L${dn.join("L")}Z` : "";
    };
    const clip = (id, z0, z1) => `<clipPath id="${id}"><rect x="${X}" y="${sy(Math.min(z1, hi))}" width="${w}" height="${Math.max(0, sy(Math.max(z0, lo)) - sy(Math.min(z1, hi)))}"/></clipPath>`;
    let defs = "";
    const uid = Math.random().toString(36).slice(2, 8);
    rZones().forEach(([z0, z1, k]) => { defs += clip(`c${uid}${k}`, z0, z1); });
    const outer = band("p5", "p95"), inner = band("p25", "p75");
    let med = "";
    { let d = "", on = false; for (let i = 0; i < 96; i++) { const v = p.p50?.[i]; if (v === null || v === undefined) { on = false; continue; } d += `${on ? "L" : "M"}${sx(i)},${sy(v)}`; on = true; } med = d; }
    rZones().forEach(([, , k]) => {
      s += `<g clip-path="url(#c${uid}${k})"><path d="${outer}" fill="${RC[k]}" fill-opacity="0.32"/><path d="${inner}" fill="${RC[k]}" fill-opacity="0.7"/>` +
        `<path d="${med}" fill="none" stroke="${RC[k]}" stroke-width="3"/></g>`;
    });
    [[RZ.vhigh, "4 3", 1], [RZ.high, "", 1.6], [RZ.low, "", 1.6], [RZ.vlow, "4 3", 1]].forEach(([v, dash, wd]) => {
      if (v > lo && v < hi) s += `<line x1="${X}" x2="${X + w}" y1="${sy(v)}" y2="${sy(v)}" stroke="${dash ? "var(--gcr-fg2)" : RC.target}" stroke-width="${wd}" ${dash ? `stroke-dasharray="${dash}"` : ""}/>`;
    });
    let grid = "";
    const sf = GC.mmol ? MGDL_PER_MMOLL : 1;   // grid in the display unit, positions in mg/dL
    const st = (GC.mmol ? [0.5, 1, 2, 5, 10] : [5, 10, 20, 25, 50, 100]).find((q) => q >= (hi - lo) / sf / 5) || (GC.mmol ? 10 : 100);
    // the labels in one format per axis (gcAxisText): in mmol/L one decimal on a step with a half (7.0 / 6.5 / 6.0),
    // whole numbers on a whole step (the PDF's axes_box)
    for (let k = Math.ceil(lo / sf / st - 1e-9); k * st * sf < hi; k++) {
      const gd = k * st, g = gd * sf;
      grid += `<line x1="${X}" x2="${X + w}" y1="${sy(g)}" y2="${sy(g)}" stroke="var(--gcr-grid)"/><text x="${X - 4}" y="${sy(g) + 4}" text-anchor="end">${gcAxisText(gd, st)}</text>`;
    }
    rTicks(w).forEach(([f, t]) => {
      const x = X + f * w;
      grid += `<line x1="${x}" x2="${x}" y1="10" y2="${10 + h}" stroke="var(--gcr-grid)"/><text x="${x}" y="${h + 28}" text-anchor="middle">${t}</text>`;
    });
    const lastv = (k) => [...(p[k] || [])].reverse().find((x) => x !== null && x !== undefined);
    let labels = "";
    if (["p5", "p25", "p50", "p75", "p95"].every((k) => lastv(k) !== undefined)) {
      const ys = { p50: sy(lastv("p50")) };
      ys.p75 = Math.min(sy(lastv("p75")), ys.p50 - 15); ys.p95 = Math.min(sy(lastv("p95")), ys.p75 - 15);
      ys.p25 = Math.max(sy(lastv("p25")), ys.p50 + 15); ys.p5 = Math.max(sy(lastv("p5")), ys.p25 + 15);
      [["p95", "95%"], ["p75", "75%"], ["p50", "50%"], ["p25", "25%"], ["p5", "5%"]].forEach(([k, t]) => { labels += `<text x="${X + w + 4}" y="${ys[k] + 5}">${t}</text>`; });
    }
    // the times of day with readings at the sensor's limit: marks at the bottom (LO) and top (HI) edges, the letters once
    // per run (profile_limits)
    const runs = r.profile_limits || [];
    labels += rLimitMarks(runs, X, w, 10 + h, 10, 96) + rLimitLabels(runs, 96, 24 / w).map(([f, kind]) =>
      `<text class="lim" x="${(X + 1 + f * w).toFixed(1)}" y="${kind === "low" ? 10 + h - 6 : 10 + 3.2 + 14}">${gcT(kind === "low" ? "report.limit.low" : "report.limit.high")}</text>`).join("");
    return `<h3 class="h3">${esc(title)}</h3>${caption ? rCap(rL(gcT("report.profile.caption", { lines: gcT("report.profile.lines") }))) : ""}` +
      `<svg class="chart" viewBox="0 0 ${W} ${h + 36}" role="img" aria-label="${esc([this.agpSaid(p, title), rAltLimits(runs)].filter(Boolean).join(" "))}"><defs>${defs}</defs>${grid}${s}` +
      `<rect x="${X}" y="10" width="${w}" height="${h}" fill="none" stroke="var(--gcr-fg2)"/>${labels}</svg>`;
  }

  // "Glucose Profile — 14 Days: median from 98 to 165, 5 to 95 percent range from 62 to 240 milligrams per deciliter."
  agpSaid(p, title) {
    const v = (k) => (p[k] || []).filter((x) => x !== null && x !== undefined);
    const med = v("p50"), lo = v("p5"), hi = v("p95");
    if (!med.length) return gcT("report_page.chart.insufficient", { title });
    const num = (mg) => gcSpeakNumber(gD(mg), GC.mmol ? 1 : 0, GC.dec);
    const mn = num(Math.min(...med)), mx = this.rSpk(Math.max(...med));
    return lo.length && hi.length
      ? gcT("report.alt.profile", { title, median_min: mn, median_max: mx, range_min: num(Math.min(...lo)), range_max: this.rSpk(Math.max(...hi)) })
      : gcT("report.alt.profile_median", { title, median_min: mn, median_max: mx });
  }

  /**
   * Return the daily glucose profiles, one small chart per day, one calendar week per row from the first day of the
   * week in use (as in the PDF; the row scrolls sideways on a narrow screen), as HTML; id: the heading's id; notes: the
   * texts of the section's Notes.
   */
  dailyProfiles(r, title, notes, id) {
    const rows = r.daily || [], labels = r.daily_labels || [];
    if (!rows.length) return "";
    const wds = r.daily_weekdays || [], lead = wds.length ? ((wds[0] - RT_WEEK1) % 7 + 7) % 7 : 0;
    const limits = r.daily_limits || [];
    const all = rows.flat().filter((v) => v !== null);
    const [lo, hi] = all.length ? gcFit(Math.min(...all), Math.max(...all), R_MIN_SPREAD) : [40, 300];
    // The 144 bins of a day are 10-minute periods of the local clock, calculated by GlucoCraft (report.py
    // period_report). The line breaks between two bins where a data gap lies (the gap rule, gaps.py): at an empty bin,
    // and before each bin listed in daily_breaks (a gap between two bins that both have data).
    const breaks = r.daily_breaks || [];
    const said = r.daily_said || [];   // each day in words ("Wednesday, October 14"), for its description
    const cell = (vals, lab, k) => {
      const before = new Set(breaks[k] || []);
      const brk = (a, b) => b - a > 1 || before.has(b);
      const W = 100, H = 60, sy = (v) => (hi - Math.min(Math.max(v, lo), hi)) / (hi - lo) * H, sx = (i) => (i + 0.5) / 144 * W;
      const path = rLine(vals, sx, sy, brk), hiP = rLine(vals, sx, sy, brk, (v) => v > RZ.high), loP = rLine(vals, sx, sy, brk, (v) => v < RZ.low);
      // each day's chart described in one sentence: the lowest and highest 10-minute mean of the day
      const got = vals.filter((v) => v !== null && v !== undefined);
      const runs = limits[k] || [];
      const day = said[k] || lab, desc = (got.length ? gcT("report.alt.day", { label: day,
        min: gcSpeakNumber(gD(Math.min(...got)), GC.mmol ? 1 : 0, GC.dec),
        max: this.rSpk(Math.max(...got)) }) : gcT("report.alt.day_no_data", { label: day })) + (runs.length ? ` ${rAltLimits(runs)}` : "");
      // the readings at the sensor's limit: marks at the bottom (LO) and top (HI) edges, the letters once per run (text
      // over the stretched drawing, so that it keeps its size; the chart's description says it)
      const letters = rLimitLabels(runs, 144, 0.25).map(([f, kind]) => `<span class="lim ${kind === "low" ? "lo" : "hi"}" ` +
        `style="left:${(f * 100).toFixed(1)}%">${gcT(kind === "low" ? "report.limit.low" : "report.limit.high")}</span>`).join("");
      return `<div class="day"><div class="dl" aria-hidden="true">${esc(lab)}</div><div class="dw"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(desc)}">` +
        `<rect x="0" y="${sy(Math.min(RZ.high, hi))}" width="${W}" height="${Math.max(0, sy(Math.max(RZ.low, lo)) - sy(Math.min(RZ.high, hi)))}" fill="var(--gcr-band)"/>` +
        `<path d="${path}" fill="none" stroke="${RC.target}" stroke-width="1" vector-effect="non-scaling-stroke"/>` +
        `<path d="${hiP}" fill="none" stroke="${RC.high}" stroke-width="2.2" vector-effect="non-scaling-stroke"/>` +
        `<path d="${loP}" fill="none" stroke="${RC.low}" stroke-width="2.2" vector-effect="non-scaling-stroke"/>` +
        `${rLimitMarks(runs, 0, W, H, 0, 144, 3.2, true)}</svg>${letters ? `<div aria-hidden="true">${letters}</div>` : ""}</div></div>`;
    };
    const cells = Array(lead).fill("<div></div>").concat(rows.map((v, i) => cell(v, labels[i] || "", i)));
    return `<h2 class="h2 sep" id="${id}">${esc(title)}</h2>${rCap(rL(gcT("report.daily.caption")))}` +
      rScroll(id, `<div class="days">${cells.join("")}</div>`) + rNotes(notes.map((n) => [null, n]));
  }

  /** Return the chart of the hypoglycemia events by time of day and lowest value as SVG. */
  eventsChart(ev) {
    if (!ev.length) return "";
    // the scale holds both lines, 70 and 54 mg/dL, so that the Notes' description of the chart holds for every chart
    const vals = ev.map((e) => e[2]).concat([RZ.low, RZ.vlow]);
    const [lo, hi] = gcFit(Math.min(...vals), Math.max(...vals), R_MIN_SPREAD);
    const W = this.chartW(), X = 40, w = W - X - 12, H = 130, sy = (v) => 10 + (hi - v) / (hi - lo) * H;
    let s = "";
    rTicks(w).forEach(([f, t]) => { const x = X + f * w; s += `<line x1="${x}" x2="${x}" y1="10" y2="${10 + H}" stroke="var(--gcr-grid)"/><text x="${x}" y="${H + 28}" text-anchor="middle">${t}</text>`; });
    [[RZ.low, ""], [RZ.vlow, "4 3"]].forEach(([v, d]) => { if (v > lo && v < hi) s += `<line x1="${X}" x2="${X + w}" y1="${sy(v)}" y2="${sy(v)}" stroke="${d ? "var(--gcr-fg2)" : RC.target}" ${d ? `stroke-dasharray="${d}"` : ""} stroke-width="1.4"/><text x="${X - 4}" y="${sy(v) + 5}" text-anchor="end">${rG(v)}</text>`; });
    // one row per level 1 event: [start, minutes, lowest value, hour of day, level, lowest value is LO]; the marker's
    // color is the event's level (2 when a level 2 event lies inside it), not its lowest value
    ev.forEach((e) => { s += `<circle cx="${X + e[3] / 24 * w}" cy="${sy(e[2])}" r="${2.5 + Math.min(e[1], 120) / 25}" fill="${e[4] === 2 ? RC.vlow : RC.low}" fill-opacity="0.85"><title>${gcT("report_page.events.marker", { start: esc(e[0]), duration: rDurW(e[1]), level: e[4] === 2 ? 2 : 1, lowest: e[5] ? gcT("ui.limit.lo") : rGu(e[2]) })}</title></circle>`; });
    // "Hypoglycemia events by time of day: 6 level 1 events, 2 of them with a level 2 event; lowest value 48 milligrams
    // per deciliter." (a lowest value at the sensor's limit in words)
    const n2 = ev.filter((e) => e[4] === 2).length, low = ev.reduce((a, e) => (e[2] < a[2] ? e : a), ev[0]);
    const said = gcT("report.alt.events", { count: ev.length, level_2: n2,
      lowest: low[5] ? gcSpeakLimit("low") : this.rSpk(low[2]) });
    return `<h3 class="h3">${gcT("report.lows.chart_title")}</h3>` +
      `<svg class="chart" viewBox="0 0 ${W} ${H + 36}" role="img" aria-label="${esc(said)}">${s}<rect x="${X}" y="10" width="${w}" height="${H}" fill="none" stroke="var(--gcr-fg2)"/></svg>`;
  }

  // ------------------------------------------------------------------ full report (the same sections as the full PDF)
  /**
   * Return the sections of the full report: the hypoglycemia events by period, the daily profiles of the period, daily
   * patterns, daily summary, daily log, monthly summary.
   */
  fullSections(d) {
    const p = d.period || {}, days = d.days || {}, out = [this.eventSummary(d)];
    if (d.ndays > 14 && (p.daily || []).length) {
      out.push(this.dailyProfiles(p, gcT("report.daily.title", { period: d.label }), [gcT("report.daily.notes_period")], "r-daily-period"));
    }
    const pat = days.pattern_2h || [], hrs = rHours(2, 12);
    const zone = (v) => (v === null || v === undefined ? null : rZone(v));
    const patTitle = gcT("report.patterns.title", { period: d.label });
    // the twelve 2-hour cells: a table of one row, the hours as its column headers (read as their full time) and each
    // mean with its unit in words (the PDF's patterns); without enough data in the period, the line that says so
    out.push(`<h2 class="h2 sep" id="r-patterns">${esc(patTitle)}</h2>` + rCap(rL(gcT("report.patterns.caption", { unit: gU() }))) +
      (days.enough_data === false ? `<p class="plain">${rEnoughShort(p.active, d.ndays)}</p>`
        : `<p class="plain">${gcT("report.patterns.mean", { mean: rGu(days.mean) })}</p>` +
        rScroll("r-patterns", `<table class="pat">${rCaption(patTitle)}<tr>${hrs.map((h, i) => `<th scope="col">${rSay(h, clock(2 * i))}</th>`).join("")}</tr>` +
        `<tr>${hrs.map((h, i) => { const v = pat[i], z = zone(v); return `<td style="${z && z !== "target" ? `background:${RC[z]}33` : ""}"><b>${rSay(rG(v), this.rSpk(v))}</b></td>`; }).join("")}</tr></table>`)) +
      rNotes([[null, gcT("report.patterns.notes")]]));
    const week = days.week || [];
    const daysTitle = gcT("report.days.title", { period: d.label });
    out.push(`<h2 class="h2 sep" id="r-days">${esc(daysTitle)}</h2>` +
      rCap(gcT("report.days.caption")) +
      rScroll("r-days", `<table class="t">${rCaption(daysTitle)}<tr><th scope="col">${gcT("report.days.col.day")}</th><th scope="col">${gcT("report.days.col.mean")}</th>` +
      `<th scope="col">${gcT("report.days.col.events")}</th><th scope="col">${gcT("report.blocks.col.data")}</th></tr>${week.map((w) => `<tr><td>${esc(w[0])}</td><td><b>${rGu(w[1])}</b></td><td>${rHas(w[2]) ? esc(w[2]) : "-"}</td><td>${rf(w[3])}</td></tr>`).join("")}</table>`) +
      rNotes([[null, rL(gcT("report.days.notes"))]]));
    const log = days.log || [];
    // one cell per hour: maximum above, minimum below, measured values; LO / HI (lo / hi from the report data) are the
    // hour's category and take the place of its minimum or maximum, never a number (report_pdf.log_cell). The colored
    // cells are opaque with fixed text colors, so that the numbers keep their contrast in the light and the dark
    // theme: white on the dark red, black on the others.
    const cell = (mn, mx, lo, hi) => {
      const has = (v) => v !== null && v !== undefined;
      if (!has(mn) && !lo && !hi) return `<td></td>`;
      const k = lo || (has(mn) && mn < RZ.vlow) ? "vlow" : has(mn) && mn < RZ.low ? "low"
        : hi || (has(mx) && mx > RZ.vhigh) ? "vhigh" : has(mx) && mx > RZ.high ? "high" : null;
      const style = k ? `background:${RC[k]};color:${k === "vlow" ? "#fff" : "#000"}` : "background:var(--gcr-band)";
      const top = hi ? gcT("ui.limit.hi") : has(mx) ? rG(mx) : "", bottom = lo ? gcT("ui.limit.lo") : has(mn) ? rG(mn) : "";
      return `<td style="${style}">${top}<br>${bottom}</td>`;
    };
    // the hour labels, as in the PDF: 0 to 23, or 12a, 1 ... 5, 6a, 7 ... 11, 12p, 1 ... 5, 6p, 7 ... 11 (the half of
    // the day at 12 and 6), with the key of the shortened labels in the 12-hour format
    const half = (h) => gcT(h < 12 ? "report.log.hour_am" : "report.log.hour_pm", { hour: h % 12 || 12 });
    const hl = Array.from({ length: 24 }, (_, h) => (GC.time === "24" ? String(h) : h % 6 === 0 ? half(h) : String(h % 12)));
    const hoursKey = GC.time === "24" ? "" : ` ${gcT("report.log.hours_12", { h0: hl[0], t0: clock(0), h6: hl[6], t6: clock(6),
      h12: hl[12], t12: clock(12), h18: hl[18], t18: clock(18) })}`;
    const logTitle = gcT("report.log.title", { period: d.label });
    out.push(`<h2 class="h2 sep" id="r-log">${esc(logTitle)}</h2>` +
      rCap(rL(gcT("report.log.caption"))) +
      // the hour of each column and the day of each row are the headers; each hour is read as its full time
      rScroll("r-log", `<table class="log">${rCaption(logTitle)}<tr><th scope="col" class="dl2">${gcT("report.days.col.day")}</th>${hl.map((lab, h) =>
        `<th scope="col">${rSay(lab, clock(h))}</th>`).join("")}</tr>` +
      log.map((row, i) => `<tr><th scope="row" class="dl2">${esc(week[i]?.[0] || "")}</th>${row[0].map((mn, h) => cell(mn, row[1][h], row[2]?.[h], row[3]?.[h])).join("")}</tr>`).join("") + `</table>`) +
      rNotes([[null, rL(gcT("report.log.notes_color")) + esc(hoursKey)]]));
    const months = d.monthly || [];
    const head = rWeekHead();
    out.push(`<h2 class="h2 sep">${gcT("report.monthly.title", { period: esc(d.label) })}</h2>` +
      rCap(gcT("report.monthly.caption")) + `<div class="cal">` +
      months.map((m) => {
        // m.first_weekday: the weekday of the month's first day shown (0 = Monday); the columns start at the first day
        // of the week setting
        const lead = ((m.first_weekday - RT_WEEK1) % 7 + 7) % 7, cells = Array(lead).fill(null).concat(m.days);
        let rows = "";
        for (let i = 0; i < cells.length; i += 7) rows += `<tr>${cells.slice(i, i + 7).map((c) => (c ? `<td>${esc(c[0])}${c[2] ? `<i>${esc(c[2])}</i>` : ""}<b>${rG(c[1])}</b></td>` : "<td></td>")).join("")}</tr>`;
        return `<div><h3 class="h3">${esc(m.month)}</h3><table>${rCaption(m.month)}<tr>${head.map((w) => `<th scope="col">${w}</th>`).join("")}</tr>${rows}</table></div>`;
      }).join("") + `</div>` + rNotes([[null, rL(gcT("report.monthly.notes"))]]));
    return out.join("");
  }

  /**
   * Return the full report's summary of the hypoglycemia events (report.low_event_summary): one table, a column per
   * period inside the report's period with its sensor active, rows of counts and durations; no lowest value, no list,
   * no targets, no colors (the PDF's Report.event_summary).
   */
  eventSummary(d) {
    const cols = d.low_events || [];
    if (!cols.length) return "";
    const [night] = rNightDay();
    const dur = (m) => (m === null || m === undefined ? "-" : rDur(m));
    // a column whose period does not have enough data: its sensor active and a dash for each count (the PDF's)
    const val = (c, fn) => (c.enough_data === false ? "-" : fn(c));
    const row = (label, fn) => `<tr><td>${label}</td>${cols.map((c) => `<td><b>${val(c, fn)}</b></td>`).join("")}</tr>`;
    const block = (k) => gcT("report.events.row.block", { time: gcT("report.time_range", { start: clock(3 * k), end: clock(3 * k + 2, 59) }) });
    const rows = [
      row(rL(gcT("report.events.row.events")), (c) => esc(c.events)),
      row(rL(gcT("report.events.row.level2")), (c) => esc(c.level2)),
      row(gcT("report.events.row.per_day"), (c) => dtx(c.per_day)),
      row(gcT("report.events.row.median"), (c) => dur(c.median_min)),
      row(gcT("report.events.row.longest"), (c) => dur(c.longest_min)),
      ...[0, 1, 2, 3, 4, 5, 6, 7].map((k) => row(block(k), (c) => esc(c.blocks[k]))),
      row(gcT("report.events.row.night", { time: night }), (c) => esc(c.night)),
    ];
    return `<h2 class="h2 sep" id="r-events">${gcT("report.events.title")}</h2>${rCap(gcT("report.events.caption"))}` +
      rScroll("r-events", `<table class="t wide">${rCaption(gcT("report.events.title"))}<tr>${th("report.events.period")}${cols.map((c) => th(`report.period.${c.days}`)).join("")}</tr>` +
        // the second head row: each period's sensor active, a column header of its period's column
        `<tr><th scope="col">${rSA()}</th>${cols.map((c) => `<th scope="col">${rf(c.active)}</th>`).join("")}</tr>${rows.join("")}</table>`) +
      rNotes([[null, rL(gcT("report.events.notes", { night }))]]);
  }

  // ------------------------------------------------------------------ draw
  /** Render the whole page from the loaded report data and wire up its controls. */
  draw() {
    this.applySettings();
    const d = this._data;
    const intro = `<div class="intro">${gcT("report_page.intro")}</div>`;
    // The page: the introduction and the controls (top), the two message regions, and the report (main). The regions
    // exist from the first drawing and only their text changes, so that screen readers announce each message: the
    // status politely, an error at once. Top and main are drawn with gcRender, which keeps the focus on the control
    // that was used.
    const root = this.shadowRoot;
    if (!root.querySelector(".main")) {
      root.innerHTML = GCR_STYLE + `<ha-card lang="${globalThis.glucocraftI18n.htmlLang}"><div class="top"></div><div class="status" role="status"></div>` +
        `<div class="status err" role="alert"></div><div class="main"></div></ha-card>`;
    }
    root.querySelector("ha-card").setAttribute("lang", globalThis.glucocraftI18n.htmlLang);
    gcRender(root, intro + this.controlsHtml(d), root.querySelector(".top"));
    gcRender(root, this.bodyHtml(d), root.querySelector(".main"));
    const say = (sel, text) => { const el = root.querySelector(sel); if (el.textContent !== text) el.textContent = text; };
    say(".status[role=status]", this._status);
    say(".status[role=alert]", this._alert);
    this.wireControls(root);
  }
  // the settings the page is drawn with: the resolved regional settings (intl), the width, and from the report data
  // the display unit, the thresholds of the fixed texts, the zones and the time and date formats (glucocraft-shared.js
  // settings: one installation, the same as the cards'), and this card's own Documentation path (RT_BASE)
  applySettings() {
    if (this._base) RT_BASE = this._base;   // (set by setConfig)
    this.intl();
    this._cw = this.contentWidth();
    if (this._data?.unit) GC.mmol = this._data.unit === "mmol/L";
    if (this._data?.thresholds) RT_THR = this._data.thresholds;   // the thresholds of the fixed texts (rL)
    if (this._data?.zones) rSetZones(this._data.zones);   // the consensus ranges of the zones and the chart lines
    if (this._data?.time_format) GC.time = this._data.time_format;
    if (this._data?.date_format) GC.date = this._data.date_format;
  }
  /**
   * Return the controls of the page: the period and the form, the appendices offered, and the output (colors, sides,
   * the PDF and printing). d: the report data (null while loading).
   */
  controlsHtml(d) {
    // an action button, and a choice of a group (period, form, colors): a toggle button whose state is announced
    // (aria-pressed) and shown by a check mark besides its color (WCAG 1.4.1, 4.1.2)
    const btn = (label, act, cls = "", off = false) =>
      `<button type="button" class="${cls}" data-act="${act}"${off ? " disabled" : ""}>${esc(label)}</button>`;
    const pick = (on, label, act) =>
      `<button type="button" class="${on ? "on" : ""}" data-act="${act}" aria-pressed="${on}">${esc(label)}</button>`;
    const group = (key, buttons) => `<div class="seg" role="group" aria-label="${esc(gcT(key))}">${buttons}</div>`;
    return `<h2 class="sr">${gcT("report_page.options.heading")}</h2><div class="ctl">
        ${group("report_page.control.group.period", RDAYS.map(([n, l]) => pick(this._days === n, gcT(l), `days:${n}`)).join(""))}
        ${group("report_page.control.group.form", pick(!this._full, gcT("report_page.control.summary"), "full:0") + pick(this._full, gcT("report_page.control.full"), "full:1"))}
      </div><div class="sub">${gcT("report_page.control.note", { forms: gcT("report.about.forms.body"), context: rCtxT("report.context.forms", RADA) })}</div>
      <div class="opts">${this.offered().map(([k, l, dsc]) => `<label><input type="checkbox" data-opt="${k}"${this._opts[k] ? " checked" : ""}> <b>${esc(gcT(l))}</b><span class="sub">${gcT("report_page.options.note", { description: esc(gcT(dsc)) })}</span></label>`).join("")}</div>
      <div class="out"><h3 class="h3">${gcT("report_page.output.title")}</h3><div class="ctl">
        ${group("report_page.control.group.colors", pick(this._color, gcT("report_page.control.color"), "color:1") + pick(!this._color, gcT("report_page.control.grayscale"), "color:0"))}
        ${group("report.print.sides_group", pick(!this._two, gcT("report.print.one_sided"), "two:0") + pick(this._two, gcT("report.print.two_sided"), "two:1"))}
        ${btn(gcT("report_page.control.download"), "pdf", "act")}${btn(gcT("report_page.control.print"), "print", "act", !!d && !d.printer)}
      </div><div class="sub">${gcT(d && !d.printer ? "report_page.output.note_no_printer" : "report_page.output.note")}</div></div>`;
  }
  /**
   * Return the report itself: a loading or no-data line, or the 14-day page and the sections of the PDF. d: the report
   * data (null while loading).
   *
   * The body depends on the data (which carry the version and the retention it names), the width, the laboratory entry
   * and the settings, not on the status messages or the color choice: it is kept and built again only when one of
   * those changed.
   */
  bodyHtml(d) {
    const s = d?.standard || {};
    const bodyKey = JSON.stringify([this._loading, this._error, this._cw, this._lab, this._intlSig]);
    // a section without data: no readings and no time in range
    const empty = (r) => !r || (r.tir === null && !(r.days_with_data));
    if (d && this._bodyData === d && this._bodyKey === bodyKey) return this._body;
    if (this._loading && !d) return `<div class="sub">${gcT("report_page.status.loading")}</div>`;
    if (this._error) return "";   // the error is the page's alert message (below the controls)
    if (d && empty(s) && empty(d.period)) return `<div class="warn">${gcT("report_page.no_data")}</div>`;
    if (!d) return "";
    // the 14-day page (its Notes after its glucose profile), then the sections of the PDF in its order, each with
    // its caption, its data and its Notes. When only the last 14 days have no data, the note above the 14-day
    // page says so and every other section is drawn, as in the PDF.
    const p14 = gcT("report.period.14");
    let body = (empty(s) ? `<div class="warn">${gcT("report_page.no_data_14_days")}</div>` : "") +
      this.page1(d) + this.agp(s, gcT("report.profile.title", { period: p14 }), 240, true) + this.page1Notes(d) +
      this.dailyProfiles(s, gcT("report.daily.title", { period: p14 }), [rL(gcT("report.daily.notes")), rCtxT("report.context.patterns")], "r-daily-14") +
      this.overview(d) + this.weekly(d) + this.lowsSection(d) + this.highsSection(d) +
      this.comparison(d) + this.quality(d) + (d.full ? this.fullSections(d) : "") + this.labSection(d) +
      this.aboutSection(d) + this.customAppendix(d) + this.researchAppendix(d) + this.license() + rColorKey();
    body = rTables(body);
    [this._bodyData, this._bodyKey, this._body] = [d, bodyKey, body];
    return body;
  }
  /** Wire up the controls drawn in root: the appendices, the buttons and links, and the laboratory entry's unit. */
  wireControls(root) {
    root.querySelectorAll("[data-opt]").forEach((el) => {   // the appendices: reload with the choice
      el.onchange = () => { this._opts[el.dataset.opt] = !!el.checked; this._data = null; this.load(); };
    });
    root.querySelectorAll("[data-act]").forEach((el) => {
      el.onclick = (e) => {
        const [a, v] = el.dataset.act.split(":");
        if (a === "days") { this._days = Number(v); this._data = null; this.load(); }
        else if (a === "full") { this._full = v === "1"; this._data = null; this.load(); }
        else if (a === "color") { this._color = v === "1"; this.draw(); }
        else if (a === "two") { this._two = v === "1"; this.draw(); }
        else if (a === "pdf") this.pdf();
        else if (a === "print") this.print();
        else if (a === "references" || a === "colors") {   // the Documentation page of this dashboard, at that section
          if (!gcPlainClick(e)) return;   // the browser opens the link itself
          e.preventDefault();
          gcNavigate(el.getAttribute("href"));   // the link's own address: this card's Documentation path
        }
        else if (a === "labdel") { if (confirm(gcT("report_page.status.lab_remove_confirm", { date: v }))) this.lab("remove_lab_a1c", v); }
        else if (a === "labadd") {
          const date = root.getElementById("labdate").value, entered = root.getElementById("laba1c").value;
          const unit = root.getElementById("labunit").value, [lo, hi] = this.labLimits(unit);
          const a1c = parseFloat(entered);
          const invalid = [...(date ? [] : ["date"]), ...(a1c >= lo && a1c <= hi ? [] : ["a1c"])];
          this._lab = { date, a1c: entered, unit, invalid };
          if (invalid.length) {   // the fields marked, the message under them, the focus on the first invalid field
            this.draw();
            root.getElementById(invalid[0] === "date" ? "labdate" : "laba1c")?.focus();
            return;
          }
          this.lab("add_lab_a1c", date, a1c, unit);
        }
      };
    });
    // the unit of the laboratory A1C: the entry is drawn again with that unit's limits, keeping what was typed
    const unitSel = root.getElementById("labunit");
    if (unitSel) {
      unitSel.onchange = () => {
        this._lab = { date: root.getElementById("labdate").value, a1c: root.getElementById("laba1c").value,
          unit: unitSel.value, invalid: [] };
        this.draw();
      };
    }
  }
  // the lowest and highest laboratory A1C a person can enter in a unit, from the report data (report_service
  // LAB_LIMITS, the limits of the action add_lab_a1c)
  labLimits(unit) { return this._data?.lab_limits?.[unit] || [NaN, NaN]; }
  // the unit the laboratory entry offers first: the one last chosen, else mmol/mol when A1C is shown in mmol/mol only
  labUnit() { return this._lab.unit || (GC.a1c === "mmol_mol" ? "mmol_mol" : "percent"); }
  // ------------------------------------------------------------------ the sections of the 14-day page and the period
  // the days of history GlucoCraft keeps (the laboratory comparison and the About texts name them), from the report
  // data, which always carry it
  retention(d) { return +d.retention_days; }

  /** Return the head of the 14-day page, its time in ranges and its glucose metrics. */
  page1(d) {
    const s = d.standard || {};
    // the title, the period, sensor active and the days with data, and the line of the PDF's page header: the ranges'
    // sources and the glucose sources by their names
    const head = `<h2 class="h2">${gcT("report.page1.title")}</h2>
      <div><b>${gcT("report.page1.period", { start: rDate(s.period_start), end: rDate(s.period_end) })}</b></div>
      <div>${gcT("report_page.head.active", { active: rf(s.active), days: s.days_with_data || 0, total: 14 })}</div>` +
      // the note of the 14 days when they do not have enough data (the PDF's under the days with data)
      (s.enough_data === false ? `<div class="warn">${rEnoughNote(s, gcT("report.period.14"))}</div>` : "") +
      rCap(gcT("report.header.sources", { data: rSourceLine(d) }));
    // the time per day beside each percentage (rPerDay)
    const pd = Object.fromEntries(["very_high", "high", "tir", "titr", "low", "very_low", "above_180", "below_70"]
      .map((k) => [k, rPerDay(s.per_day?.[k])]));
    const tir = `<div class="grid2"><div><h3 class="h3">${gcT("report.tir.title")}</h3><div class="sub">${gcT("report.tir.note", { unit: gU(), minutes: nf(14.4, 1) })}</div>
        <div class="tir">${this.tirBar(s)}<table class="t">${rCaption(gcT("report.tir.title"))}
          <tr><td><span class="sw" style="background:${RC.vhigh}"></span>${rL(gcT("report.key.vhigh"))}</td><td><b>${rf(s.very_high)}</b>${pd.very_high}</td><td class="n">${tgt("time_very_high")}</td></tr>
          <tr><td><span class="sw" style="background:${RC.high}"></span>${rL(gcT("report.key.high"))}</td><td><b>${rf(s.high)}</b>${pd.high}</td><td class="n"></td></tr>
          <tr><td><span class="sw" style="background:${RC.target}"></span>${rL(gcT("report.key.target"))}</td><td><b>${rf(s.tir)}</b>${pd.tir}</td><td class="n">${tgt("time_in_range")}</td></tr>
          <tr><td><span class="sw" style="background:${RC.tight}"></span>${rL(gcT("report.key.tight"))}</td><td><b>${rf(s.titr)}</b>${pd.titr}</td><td class="n">${gcT("report.tir.no_target")}</td></tr>
          <tr><td><span class="sw" style="background:${RC.low}"></span>${rL(gcT("report.key.low"))}</td><td><b>${rf(s.low)}</b>${pd.low}</td><td class="n"></td></tr>
          <tr><td><span class="sw" style="background:${RC.vlow}"></span>${rL(gcT("report.key.vlow"))}</td><td><b>${rf(s.very_low)}</b>${pd.very_low}</td><td class="n">${tgt("time_very_low")}</td></tr>
          <tr><td>${rL(gcT("report_page.tir.above"))}</td><td><b>${rf(s.above_180)}</b>${pd.above_180}</td><td class="n">${tgt("time_high")}</td></tr>
          <tr><td>${rL(gcT("report_page.tir.below"))}</td><td><b>${rf(s.below_70)}</b>${pd.below_70}</td><td class="n">${tgt("time_low")}</td></tr></table></div></div>
      <div><h3 class="h3">${gcT("report.metrics.title")}</h3><table class="t">${rCaption(gcT("report.metrics.title"))}
        <tr><td>${gcT("report.metrics.mean")}</td><td><b>${rGu(s.mean)}</b></td><td class="n"></td></tr>
        <tr><td>${gcT("report.metrics.gmi")}</td><td><b>${rA1c(s.gmi, s.gmi_mmol_mol)}</b></td><td class="n">${gcT("report.metrics.gmi_note")}</td></tr>
        <tr><td>${gcT("report.metrics.cv")}</td><td><b>${rf(s.cv)}</b></td><td class="n">${gcT("report.metrics.cv_note")}</td></tr>
        <tr><td>${gcT("report.metrics.gri")}</td><td><b>${s.gri === undefined || s.gri === null ? "-" : gcT("report.metrics.gri_value", { value: esc(dtx(s.gri)), zone: esc(s.gri_zone) })}</b></td><td class="n">${s.gri === undefined || s.gri === null ? "" : [gcT("report.metrics.gri_hypo", { value: dtx(s.gri_hypo) }), gcT("report.metrics.gri_hyper", { value: dtx(s.gri_hyper) })].join(gcT("report.list_separator"))}</td></tr>
        <tr><td>${gcT("report.metrics.sd")}</td><td><b>${rGu(s.sd)}</b></td><td class="n"></td></tr></table></div></div>`;
    return head + tir;
  }

  /**
   * Return the Notes of the 14-day page (after its glucose profile, as in the PDF): the sources and rules of the
   * metrics with the readings at the sensor's limit, the ranges and targets, the groups whose published targets are not
   * used, the names of the ranges, the CV target, the formulas of the metrics and the glucose profile, each with its
   * citation (report_pdf.Report.page1_notes).
   */
  page1Notes(d) {
    const s = d.standard || {}, limit = rLimitNote(s.limit_readings, s.limit_seconds);
    const lead = (k) => gcT(`report.page1.notes.${k}.lead`);
    return rNotes([
      [null, gcT("report.metrics.sources") + (limit ? ` ${esc(limit)}` : "")],
      [lead("ranges"), rL(gcT("report.page1.notes.ranges.body"))],
      [lead("groups"), gcT("report.page1.notes.groups.body")],
      [lead("names"), rCtxT("report.context.names")],
      [lead("cv"), rCtxT("report.context.cv", RADA)],
      [lead("metrics"), dtx(gcT("report.page1.notes.metrics.body"))],
      [lead("profile"), `${rCtxT("report.context.profile")} ${gcT("report.page1.notes.profile.body")}`],
    ]);
  }

  /** Return the overview of a period longer than 14 days: its numbers, its profile and its 14-day blocks. */
  overview(d) {
    const p = d.period || {};
    const limP = rLimitNote(p.limit_readings, p.limit_seconds);
    let over = "";
    if (d.ndays > 14) {
      const bl = d.blocks || [];
      const title = gcT("report.overview.title", { period: d.label });
      // the period's note when it does not have enough data, after the period line (the PDF's, its first sentence in
      // bold)
      const note = rEnoughNote(p, d.label), cut = note.indexOf(". ");
      over = `<h2 class="h2 sep" id="r-overview">${esc(title)}</h2>
        ${rCap(gcT("report.overview.caption"))}
        <p class="plain">${gcT("report.overview.period", { start: rDate(p.period_start), end: rDate(p.period_end), sensor_active: rSA(), active: rf(p.active), count: p.days_with_data || 0, total: d.ndays })}</p>
        ${note ? `<div class="warn"><b>${note.slice(0, cut + 1)}</b> ${note.slice(cut + 2)}</div>` : ""}
        ${rScroll("r-overview", `<table class="t wide">${rCaption(title)}<tr><th scope="col"><span class="sr">${gcT("report_page.col.measure")}</span></th>${th("report.blocks.col.tir")}${th("report.blocks.col.titr")}${rBandHeads()}${th("report.weekly.col.mean", { unit: gU() })}${th("report_page.col.sd")}${th("report.blocks.col.cv")}${th("report.blocks.col.gmi")}${th("report.blocks.col.gri")}${th("report.overview.row.min")}${th("report.overview.row.max")}</tr>
        <tr><td>${gcT("report.overview.value")}</td>${["tir", "titr", "below_70", "very_low", "above_180", "very_high"].map((k) => `<td><b>${rf(p[k])}</b>${rPerDay(p.per_day?.[k], true)}</td>`).join("")}<td><b>${rG(p.mean)}</b></td><td><b>${rG(p.sd)}</b></td><td><b>${rf(p.cv)}</b></td><td><b>${rA1c(p.gmi, p.gmi_mmol_mol)}</b></td><td><b>${p.gri === undefined || p.gri === null ? "-" : `${esc(dtx(p.gri))} ${esc(p.gri_zone)}`}</b></td><td><b>${rGu(p.min)}</b></td><td><b>${rGu(p.max)}</b></td></tr>
        <tr><td>${gcT("report.overview.consensus_target")}</td>${OVERVIEW_TARGETS().map((x) => `<td class="n">${esc(x)}</td>`).join("")}</tr></table>`)}
        ${this.agp(p, gcT("report.profile.title", { period: d.label }), 190)}
        <h3 class="h3" id="r-blocks">${gcT("report.blocks.title")}</h3>
        ${rScroll("r-blocks", `<table class="t wide">${rCaption(gcT("report.blocks.title"))}<tr>${th("report.blocks.col.period")}${th("report.blocks.col.tir")}${th("report.blocks.col.titr")}${rBandHeads()}${th("report.weekly.col.mean", { unit: gU() })}${th("report.blocks.col.cv")}${th("report.blocks.col.gmi")}${th("report.blocks.col.gri")}${th("report.blocks.col.events")}${th("report.blocks.col.data")}</tr>
        ${bl.map((b) => { const span = gcT("report.date_range", { start: rDate(b.period_start, 1), end: rDate(b.period_end, 1) });
          // a shorter first period is marked with its length
          return `<tr><td>${b.days < 14 ? gcT("report.blocks.short", { period: span, days: b.days, count: b.days }) : span}</td><td>${rf(b.tir)}</td><td>${rf(b.titr)}</td><td>${rf(b.below_70)}</td><td>${rf(b.very_low)}</td><td>${rf(b.above_180)}</td><td>${rf(b.very_high)}</td><td>${rG(b.mean)}</td><td>${rf(b.cv)}</td><td>${rA1c(b.gmi, b.gmi_mmol_mol)}</td><td>${b.gri === null || b.gri === undefined ? "-" : `${esc(dtx(b.gri))} ${esc(b.gri_zone)}`}</td><td>${rHas(b.events) ? esc(b.events) : "-"}</td><td>${rf(b.active)}</td></tr>`; }).join("")}</table>`)}` +
        // the legend of the short column heads of both tables of the overview
        rLegend("tir", "titr", "bands", "mean", "sd", "cv", "gmi", "gri", "events", "data_period") +
        rNotes([[null, gcT("report.overview.notes") + (limP ? ` ${esc(limP)}` : "")],
          [null, gcT("report.overview.per_day_note", { minutes: nf(14.4, 1) })], [null, gcT("report.overview.profile_notes")],
          [null, bl.length ? gcT("report.blocks.notes") : ""]]);
    }
    return over;
  }

  /** Return the hypoglycemia section: night and day, the events and their chart. */
  lowsSection(d) {
    const p = d.period || {};
    const lw = d.lows || {};
    const [NIGHT, DAY] = rNightDay(), nightDayHead = rNightDayHead(), ev = lw.list || [];
    const title = gcT("report.lows.title", { period: d.label });
    // the caption: the consensus event rule; the Notes: the cited context of time and events, the event method, the
    // night and day periods and the chart (the PDF's Report.lows)
    const notes = rNotes([[null, rL(gcT("report.lows.notes"))], [null, rCtxT("report.context.both")], [null, rCtxT("report.context.event")],
      [gcT("report.lows.event.lead"), rL(gcT("report.lows.event.body"))],
      [gcT("report.lows.night.lead"), rCtxT("report.context.night", { night: NIGHT, day: DAY })],
      [gcT("report.lows.chart.lead"), ev.length ? rL(gcT("report.lows.scatter_caption")) : ""]]);
    // without enough data in the period: the line that says so takes the tables' and the chart's place (the PDF's)
    if (p.enough_data === false) {
      return `<h2 class="h2 sep" id="r-lows">${esc(title)}</h2>${rCap(rL(gcT("report.lows.caption")))}` +
        `<p class="plain">${rEnoughShort(p.active, d.ndays)}</p>${notes}`;
    }
    return `<h2 class="h2 sep" id="r-lows">${esc(title)}</h2>${rCap(rL(gcT("report.lows.caption")))}
      ${rScroll("r-lows", `<table class="t">${rCaption(title)}${nightDayHead}
      <tr><td>${rL(gcT("report.change.row.below_70"))}</td><td>${rf(lw.night_below_70)}</td><td>${rf(lw.day_below_70)}</td><td><b>${rf(p.below_70)}</b></td><td class="n">${esc(rTarget("time_low"))}</td></tr>
      <tr><td>${rL(gcT("report.change.row.below_54"))}</td><td>${rf(lw.night_below_54)}</td><td>${rf(lw.day_below_54)}</td><td><b>${rf(p.very_low)}</b></td><td class="n">${esc(rTarget("time_very_low"))}</td></tr></table>`)}
      <table class="t">${rCaption(title)}<tr><td>${rL(gcT("report.lows.level1"))}</td><td>${gcT("report.lows.level1_value", { count: `<b>${lw.events ?? "-"}</b>`, per_week: rf(lw.events_per_week, ""), night: lw.events_night ?? "-" })}</td></tr>
      <tr><td>${rL(gcT("report.lows.level2"))}</td><td>${gcT("report.lows.level2_value", { count: `<b>${lw.events_54 ?? "-"}</b>`, per_week: rf(lw.events_54_per_week, "") })}</td></tr>
      <tr><td>${gcT("report.lows.duration")}</td><td>${gcT("report.lows.duration_value", {
        mean: rHas(lw.mean_duration_min) ? rDurW(lw.mean_duration_min) : "-",
        longest: lw.longest_min ? gcT("report.lows.longest", { duration: rDurW(lw.longest_min), when: esc(lw.longest_when) }) : "-" })}</td></tr></table>
      ${this.eventsChart(ev)}${notes}`;
  }

  /** Return the high-glucose section, mirroring the hypoglycemia section; no events. */
  highsSection(d) {
    const p = d.period || {};
    const nightDayHead = rNightDayHead();
    const hg = d.highs || {};
    const title = gcT("report.highs.title", { period: d.label });
    const notes = rNotes([[null, rCtxT("report.context.tar", RADA)], [null, gcT("report.highs.notes")]]);
    // without enough data in the period: the line that says so takes the table's place (the PDF's)
    if (p.enough_data === false) {
      return `<h2 class="h2 sep" id="r-highs">${esc(title)}</h2>${rCap(rL(gcT("report.highs.caption")))}` +
        `<p class="plain">${rEnoughShort(p.active, d.ndays)}</p>${notes}`;
    }
    return `<h2 class="h2 sep" id="r-highs">${esc(title)}</h2>${rCap(rL(gcT("report.highs.caption")))}
      ${rScroll("r-highs", `<table class="t">${rCaption(title)}${nightDayHead}
      <tr><td>${rL(gcT("report.change.row.above_180"))}</td><td>${rf(hg.night_above_180)}</td><td>${rf(hg.day_above_180)}</td><td><b>${rf(p.above_180)}</b></td><td class="n">${esc(rTarget("time_high"))}</td></tr>
      <tr><td>${rL(gcT("report.row.above_250"))}</td><td>${rf(hg.night_above_250)}</td><td>${rf(hg.day_above_250)}</td><td><b>${rf(p.very_high)}</b></td><td class="n">${esc(rTarget("time_very_high"))}</td></tr></table>`)}
      ${notes}`;
  }

  /** Return the comparison with the previous period of the same length. */
  comparison(d) {
    const p = d.period || {};
    const pv = d.previous || {}, retention = this.retention(d);
    // the periods are compared only when both have enough data, at least 70% sensor active compared before rounding
    // (enough_data; the PDF's Report.change): first this period, then the previous one
    const thisOk = p.enough_data === true;
    const have = thisOk && pv.tir !== null && pv.tir !== undefined && pv.enough_data === true;
    const rows = [["report.change.row.tir", "tir", "%"], ["report.change.row.below_70", "below_70", "%"], ["report.change.row.below_54", "very_low", "%"],
      ["report.change.row.above_180", "above_180", "%"], ["report.change.row.mean", "mean", ""], ["report.blocks.col.gmi", "gmi", "%"],
      ["report.change.row.cv", "cv", "%"], ["report.change.row.gri", "gri", ""], ["report.sensor_active.label", "active", "%"]];
    // the 6-month report has no previous period (with it, the report would need twice its length: 360 days of
    // readings; GlucoCraft keeps 200 days)
    const six = d.ndays >= 180;   // previous is null for the 6-month report (backend contract)
    return `<h2 class="h2 sep" id="r-change">${gcT("report.change.title")}</h2>` + (six
      ? `<div class="sub">${gcT("report.change.six_months", { days: nf(2 * d.ndays, 0), retention })}</div>`
      : have
      ? `<div class="sub">${gcT("report.change.subtitle", { count: d.ndays, start: rDate(p.period_start, 1), end: rDate(p.period_end, 1), previous_start: rDate(pv.period_start, 1), previous_end: rDate(pv.period_end, 1) })}</div>
        ${rScroll("r-change", `<table class="t">${rCaption(gcT("report.change.title"))}<tr>${th("report.table.thresholds", { unit: gU() })}${th("report.change.this_period")}${th("report.change.previous")}${th("report.change.difference")}</tr>${rows.map(([lk, k, u]) => {
          // every difference is that of the two values as shown (the row adds up; nothing is rounded twice), with the
          // decimals they are shown with (a percentage beside a rule can have two) and no sign for zero
          // (report_pdf.Report.change)
          const places = (v) => (String(v).split(".")[1] || "").length;
          const l = gcT(lk), a = p[k], b = pv[k], df = a !== null && a !== undefined && b !== null && b !== undefined
            ? round(a - b, Math.max(places(a), places(b))) : null;
          if (k === "mean") {   // glucose: in the display unit, mmol/L one decimal, mg/dL whole numbers
            const dg = df === null ? null : GC.mmol ? round(gD(a) - gD(b), 1) : round(a) - round(b);
            return `<tr><td>${l}</td><td><b>${rGu(a)}</b></td><td>${rGu(b)}</td><td>${dg === null ? "-" : `${dg > 0 ? "+" : ""}${GC.mmol ? nf(dg, 1) : dg} ${gU()}`}</td></tr>`;
          }
          if (k === "gmi") return `<tr><td>${l}</td><td><b>${rA1c(a, p.gmi_mmol_mol)}</b></td><td>${rA1c(b, pv.gmi_mmol_mol)}</td><td>${df === null ? "-" : rA1cDiff(df, rMmDiff(p.gmi_mmol_mol, pv.gmi_mmol_mol))}</td></tr>`;
          const dv = df === null ? "" : `${df > 0 ? "+" : ""}${dtx(df)}`;   // a percentage: the difference in points
          return `<tr><td>${rL(l)}</td><td><b>${rf(a, u)}</b></td><td>${rf(b, u)}</td><td>${df === null ? "-" : u === "%" ? gcT("report.change.points", { value: dv, count: Math.abs(df), digits: Number.isInteger(df) ? 0 : 1 }) : dv + u}</td></tr>`;
        }).join("")}</table>`)}${rNotes([[null, gcT("report.change.notes")]])}`
      : !thisOk ? `<div class="sub">${gcT("report.change.not_available_this", { active: rf(p.active) })}</div>`
      : `<div class="sub">${gcT("report.change.not_available", { previous_start: rDate(pv.period_start, 1), previous_end: rDate(pv.period_end, 1), active: rf(pv.active) })}</div>`);
  }

  /** Return the laboratory A1C section: the results, their comparison with GMI, and the entry form. */
  labSection(d) {
    const retention = this.retention(d);
    // an invalid field is marked and described by the message under the form (WCAG 3.3.1)
    const bad = (f) => (this._lab.invalid.includes(f) ? ' aria-invalid="true" aria-describedby="laberr"' : "");
    const lab = d.lab || {}, lr = lab.results || [];
    // the entry: a laboratory A1C in percent or in mmol/mol, within that unit's limits
    const unit = this.labUnit(), [lo, hi] = this.labLimits(unit);
    const labMean = rA1cDiff(lab.average_difference, lab.average_difference_mmol_mol);   // "-" when not known
    // the Notes: the cited context of GMI versus laboratory A1C and the method of the comparison (the PDF's Report.lab)
    // the laboratory sentence names the results in this section only when there are any
    const notes = rNotes([[null, gcT("report.lab.intro")], [null, rCtxT(lr.length ? "report.context.lab_difference" : "report.context.lab_difference_none")],
      [null, rCtxT("report.context.lab_ada", RADA)],
      [null, gcT("report.lab.method", { days: retention }) + (GC.a1c === "both" ? ` ${gcT("report.lab.difference_both")}` : "")]]);
    // the results table scrolls inside its own region at a narrow width, as every wide table of the page (rScroll)
    return `<h2 class="h2 sep" id="r-lab">${gcT("report.lab.title")}</h2>${rCap(gcT("report.lab.caption"))}` +
      (lr.length ? rScroll("r-lab", `<table class="t">${rCaption(gcT("report.lab.title"))}<tr>${th("report.lab.col.date")}${th("report.lab.col.a1c")}${th("report.lab.col.gmi")}${th("report.lab.col.difference")}${th("report.lab.col.active")}<th scope="col"><span class="sr">${gcT("report_page.lab.remove")}</span></th></tr>` +
        [...lr].reverse().map((x) => {
          // the comparison uses the 90 days before the laboratory date; they are stored only while the date is less than
          // (retention - 90) days ago, about 110 days: the report data mark an older result (no_cgm_comparison, with
          // its comparison_note)
          const old = x.no_cgm_comparison === true;
          const note = old ? String(x.comparison_note) : "";
          const g = old ? esc(note.charAt(0).toUpperCase() + note.slice(1)) : x.gmi_90d === null || x.gmi_90d === undefined ? gcT("report.lab.gmi_low_data") : rA1c(x.gmi_90d, x.gmi_90d_mmol_mol);
          return `<tr><td>${rDate(x.date)}</td><td><b>${rA1c(x.a1c, x.a1c_mmol_mol)}</b></td><td>${g}</td><td>${x.gmi_90d === null || x.gmi_90d === undefined ? "-" : rA1cDiff(x.a1c - x.gmi_90d, rMmDiff(x.a1c_mmol_mol, x.gmi_90d_mmol_mol), true)}</td><td>${old ? "-" : rf(x.cgm_active_90d)}</td><td><button type="button" class="sm" data-act="labdel:${esc(x.date)}" aria-label="${esc(gcT("report_page.lab.remove_label", { date: rDate(x.date) }))}">${gcT("report_page.lab.remove")}</button></td></tr>`;
        }).join("") + `</table>`) +
        (lab.average_difference !== null && lab.average_difference !== undefined ? `<div class="sub">${gcT("report_page.lab.mean_difference", { value: labMean })}</div>` : "")
        : `<div class="sub">${gcT("report_page.lab.none")}</div>`) +
      `<div class="labform"><label>${gcT("report_page.lab.date")} <input type="date" id="labdate" value="${esc(this._lab.date)}"${bad("date")}></label>` +
      `<label>${gcT("report.lab.col.a1c")} <input type="number" id="laba1c"${rHas(lo) ? ` min="${lo}" max="${hi}"` : ""} step="${unit === "mmol_mol" ? 1 : 0.1}" value="${esc(this._lab.a1c)}"${bad("a1c")}></label>` +
      `<label>${gcT("report.lab.unit.label")} <select id="labunit">${["percent", "mmol_mol"].map((u) =>
        `<option value="${u}"${u === unit ? " selected" : ""}>${gcT(`report.lab.unit.${u}`)}</option>`).join("")}</select></label>` +
      `<button type="button" data-act="labadd">${gcT("report_page.lab.save")}</button></div>` +
      // the message of each invalid field: the texts of the action add_lab_a1c (report_service.lab_entry)
      (this._lab.invalid.length ? `<div class="warn" id="laberr">${[
        ...(this._lab.invalid.includes("date") ? [gcT("report.lab.error.no_date")] : []),
        ...(this._lab.invalid.includes("a1c") ? [gcT("report.lab.error.range", { low: dtx(lo), high: dtx(hi), unit: gcT(`report.lab.unit.${unit}`) })] : []),
      ].join(" ")}</div>` : "") + notes;
  }

  /**
   * Return About This Report: the disclaimer, the scope and the general method, in the order of the PDF's items
   * (report_pdf.Report.about); each section's own explanation is in its Notes. The summary and full report item is
   * under the page's controls; the page's own items (the data kept, the link to the references) where the PDF has
   * their facts.
   */
  aboutSection(d) {
    const retention = this.retention(d);
    const para = (key, params) => `<p>${gcT(key, params)}</p>`;
    const item = (lead, text) => `<p><b>${lead}</b> ${text}</p>`;
    const about = (key, text = gcT(`report.about.${key}.body`)) => item(gcT(`report.about.${key}.lead`), text);
    const [sourcesLead, sources] = rDataSources(d);
    return `<h2 class="h2 sep">${gcT("report.about.title")}</h2><div class="sub">
      ${para("disclaimer.text")}
      ${about("report", esc(gcT("report.about.report.body")))}
      ${about("statement", esc(gcT("report.statement")))}
      ${item(sourcesLead, sources)}
      ${about("period")}
      ${about("length")}
      ${about("data")}
      ${about("limit")}
      ${about("enough")}
      ${about("rounding")}
      ${about("durations")}
      ${para("report_page.about.kept", { retention, age: retention - 90 })}
      ${about("not_included")}
      ${GC.a1c === "percent" ? "" : about("mmol_mol", dtx(gcT("report.about.mmol_mol.body", { factor: String(d.ifcc?.factor ?? ""), offset: String(d.ifcc?.offset ?? "") })))}
      ${para("report_page.about.references", { link: rDocLink("references", "report_page.link.references") })}</div>`;
  }

  // ------------------------------------------------------------------ further sections (the same sections as the PDF)
  /** Return the 26-week weekly trend (90 days and 6 months): GlucoCraft's weekly statistics on the consensus ranges. */
  weekly(d) {
    const wk = d.weekly || [];
    if (!wk.length) return "";
    const n = wk.length;
    // title: the chart's heading; name: the measure as spoken (the heading without its unit)
    const mini = (title, name, key, conv) => {
      const vv = wk.map((w) => (w[key] === null || w[key] === undefined ? null : conv(w[key]))), got = vv.filter((v) => v !== null);
      if (!got.length) return "";
      let [lo, hi] = key === "tir" ? [0, 100] : gcFit(Math.min(...got), Math.max(...got), GC.mmol && key === "mean" ? 4 : 10);
      if (key !== "tir") lo = Math.max(0, lo);
      const W = this.miniW(), H = 70, X = 36, sy = (v) => 8 + (hi - v) / (hi - lo) * H, bw = Math.max(1.5, (W - X) / n - 2);
      const bars = vv.map((v, i) => (v === null ? "" : `<rect x="${X + (i + 0.5) * (W - X) / n - bw / 2}" y="${sy(v)}" width="${bw}" height="${Math.max(0.5, sy(lo) - sy(v))}" fill="var(--gcr-fg2)"/>`)).join("");
      // the axis labels in one format: the mean in mmol/L with one decimal on every label, whole numbers otherwise
      const digits = GC.mmol && key === "mean" ? 1 : 0;
      const ax = [lo, (lo + hi) / 2, hi].map((t) => `<text x="${X - 4}" y="${sy(t) + 5}" text-anchor="end">${nf(round(t, digits), digits)}</text>`).join("");
      // "Time in range by week, 26 weeks with values: from 61.2 to 82 percent." in the chart's unit, each number with
      // the decimals the weekly table shows it with (a percentage as in the report data, the mean in the display unit)
      const places = (v) => (key === "mean" ? digits : (String(v).split(".")[1] || "").length);
      const mn = Math.min(...got), mx = Math.max(...got);
      const said = gcT("report.alt.weekly", { name, count: got.length, min: gcSpeakNumber(round(mn, places(mn)), places(mn), GC.dec),
        max: gcSpeak(round(mx, places(mx)), key === "mean" ? gU() : "%", { d: places(mx), dec: GC.dec }) });
      return `<div><h3 class="h3 small">${esc(title)}</h3><svg class="chart" viewBox="0 0 ${W} ${H + 28}" role="img" aria-label="${esc(said)}">${ax}<rect x="${X}" y="8" width="${W - X}" height="${H}" fill="none" stroke="var(--gcr-grid)"/>${bars}` +
        `<text x="${X}" y="${H + 26}">${rDate(wk[0].week_end, 1)}</text><text x="${W}" y="${H + 26}" text-anchor="end">${rDate(wk[n - 1].week_end, 1)}</text></svg></div>`;
    };
    const title = gcT("report.weekly.title", { count: n });
    return `<h2 class="h2 sep" id="r-weekly">${esc(title)}</h2>${rCap(gcT("report.weekly.caption"))}` +
      `<div class="grid3">${mini(gcT("report.weekly.chart.tir"), gcT("report.alt.measure.tir"), "tir", (v) => v)}` +
      `${mini(gcT("report.weekly.chart.cv"), gcT("report.alt.measure.cv"), "cv", (v) => v)}` +
      `${mini(gcT("report.weekly.chart.mean", { unit: gU() }), gcT("report.alt.measure.mean"), "mean", (v) => (GC.mmol ? v / MGDL_PER_MMOLL : v))}</div>` +
      // a week without enough data: its sensor active and a dash for each value; a week without data: one cell
      rScroll("r-weekly", `<table class="t wide">${rCaption(title)}<tr><th scope="col">${gcT("report.weekly.col.week")}</th><th scope="col">${gcT("report.blocks.col.tir")}</th>` +
      `<th scope="col">${gcT("report.blocks.col.cv")}</th><th scope="col">${gcT("report.weekly.col.mean", { unit: gU() })}</th><th scope="col">${gcT("report.blocks.col.data")}</th></tr>${wk.map((w) => (w.active === null || w.active === undefined
        ? `<tr><td>${rDate(w.week_end, 1)}</td><td colspan="4">${gcT("report.weekly.no_data")}</td></tr>`
        : `<tr><td>${rDate(w.week_end, 1)}</td><td>${rf(w.tir)}</td><td>${rf(w.cv)}</td><td>${rG(w.mean)}</td><td>${rf(w.active)}</td></tr>`)).join("")}</table>`) +
      rLegend("tir", "cv", "mean", "data_week") + rNotes([[null, gcT("report.weekly.notes", { count: n })]]);
  }

  /** Return the data quality section: sensor active per day, gaps, LO / HI readings and sensor changes. */
  quality(d) {
    const q = d.quality;
    if (!q) return "";
    const limParts = rLimitParts(q.limit_readings, q.limit_seconds);
    const gaps = q.gaps || 0, ev = q.sensor_events || [], head = rWeekHead();
    // the lead from the report data uses the backend's first day of the week; recomputed for this page's setting
    const lead = (q.per_day || []).length ? ((q.per_day[0].weekday - RT_WEEK1) % 7 + 7) % 7 : 0;
    const strip = Array(lead).fill(null).concat(q.per_day || []);
    let rows = "";
    for (let i = 0; i < strip.length; i += 7) rows += `<tr>${strip.slice(i, i + 7).map((c) => (c ? `<td class="${c.active ? "" : "nod"}"><i>${esc(c.label)}</i><b>${rf(c.active)}</b></td>` : "<td></td>")).join("")}</tr>`;
    const warmUps = ev.length ? ev.map((e) => (e.warmup === false ? gcT("report.quality.sensor_change", { start: esc(e.start) })
      : e.limit ? gcT("report.quality.warmup_limit", { start: esc(e.start) })
      : e.end ? gcT("report.quality.warmup", { start: esc(e.start), end: esc(e.end), duration: rDurW(e.minutes) })
      : gcT("report.quality.warmup_open", { start: esc(e.start) }))).join(gcT("report.list_separator"))
      : gcT("report.quality.no_events");
    const title = gcT("report.quality.title", { period: d.label }), stripTitle = gcT("report.quality.per_day", { label: rSA() });
    return `<h2 class="h2 sep">${esc(title)}</h2>${rCap(gcT("report.quality.caption"))}
      <table class="t">${rCaption(title)}<tr><td>${rSA()}</td><td><b>${rf(q.active)}</b></td></tr>
      <tr><td>${gcT("report.quality.days_with_data")}</td><td><b>${gcT("report.count_of", { count: d.period?.days_with_data || 0, total: d.ndays })}</b></td></tr>
      <tr><td>${gcT("report.quality.no_data_time")}</td><td><b>${rDurW(q.no_data_minutes)}</b></td></tr>
      <tr><td>${gcT("report.quality.gaps")}</td><td><b>${gaps ? gcT("report.quality.gaps_value", { count: gaps, duration: rDurW(q.longest_gap_minutes), from: esc(q.longest_gap_from), to: esc(q.longest_gap_to) }) : gcT("report.quality.gaps_none")}</b></td></tr>
      <tr><td>${gcT("report.quality.limit_readings")}</td><td><b>${limParts.length ? limParts.join(gcT("report.list_separator")) : gcT("report.quality.limit_none")}</b></td></tr></table>
      <p class="plain">${gcT("report.quality.sensor_events", { events: warmUps })}</p>
      <h3 class="h3">${esc(stripTitle)}</h3><table class="strip">${rCaption(stripTitle)}<tr>${head.map((w) => `<th scope="col">${w}</th>`).join("")}</tr>${rows}</table>
      ${rNotes([[null, gcT("report.quality.strip_note")],
        // the clause on the custom appendix only when the appendix is part of the report
        [null, `${gcT("report.sensor_active.note")} ${rCtxT(d.include_custom_appendix ? "report.context.sufficiency_custom" : "report.context.sufficiency", RADA)}`],
        [null, gcT("report.quality.sensor_events_method")], [null, gcT("report.quality.gap_rule")]])}`;
  }

  /** Return the appendix "Custom Ranges and Goals" (include_custom_appendix); met / not met by the 14-day / 70% rule. */
  customAppendix(d) {
    const ca = d.custom_appendix;
    if (!d.include_custom_appendix || !ca) return "";
    const rg = ca.ranges || {}, st = ca.stats || {}, cons = d.period || {};
    const below = (v) => gcT("report.custom.band.below", { value: rG(v) }), above = (v) => gcT("report.custom.band.above", { value: rG(v) });
    const band = { tir: `${rG(rg.low)}–${rG(rg.high)}`, titr: `${rG(rg.tight_low)}–${rG(rg.tight_high)}`, below_low: below(rg.low),
      very_low: below(rg.very_low), above_high: above(rg.high), very_high: above(rg.very_high) };
    const vkey = { time_in_range: "tir", time_in_tight_range: "titr", time_low: "below_low", time_very_low: "very_low", time_high: "above_high",
      time_very_high: "very_high", cv: "cv", gmi: "gmi" };
    const ckey = { tir: "tir", titr: "titr", below_low: "below_70", very_low: "very_low", above_high: "above_180", very_high: "very_high", cv: "cv", gmi: "gmi" };
    // the comparison of a custom goal (goal_op from the report data) as words: "more than 70%", "36% or less"
    const GOAL_KEY = { ">": "report.custom.goal.gt", ">=": "report.custom.goal.ge",
      "<": "report.custom.goal.lt", "<=": "report.custom.goal.le" };
    const val = (k, src) => (k === "gmi" ? rA1c(src[k], src.gmi_mmol_mol) : rf(src[k]));
    // the row's value: a percentage as GlucoCraft rounded it on its side of the goal (69.97% beside a goal of at least
    // 70%, so that the number agrees with "not met"; report.custom_appendix); the GMI by its own rule
    const own = (r, k) => (k === "gmi" ? val(k, st) : rf(r.value));
    const goal = (r) => {
      if (r.goal === null || r.goal === undefined) return gcT("report.custom.no_goal");
      const value = r.key === "gmi" ? rA1c(r.goal, r.goal_mmol_mol) : rf(r.goal);
      return GOAL_KEY[r.goal_op] ? gcT(GOAL_KEY[r.goal_op], { value }) : value;
    };
    const rows = (ca.rows || []).map((r) => {
      const k = vkey[r.key], res = r.met === true ? gcT("report.mark.met") : r.met === false ? gcT("report.mark.not_met")
        : r.goal === null || r.goal === undefined ? "-" : gcT("report.custom.not_compared");
      return `<tr><td>${esc(r.label)}${band[k] ? ` (${band[k]})` : ""}</td><td><b>${own(r, k)}</b></td><td>${goal(r)}</td><td>${res}</td><td class="n">${val(ckey[k], cons)}</td></tr>`;
    }).join("");
    const ranges = { very_low: rG(rg.very_low), low: rG(rg.low), high: rG(rg.high), tight_low: rG(rg.tight_low), tight_high: rG(rg.tight_high),
      very_high: rG(rg.very_high), unit: gU(), active: rf(st.active) };
    const compared = gcT(ca.comparable ? "report.custom.compared_note" : "report.custom.not_compared_note");
    const title = gcT("report.custom.title", { period: d.label });
    // without enough data: the line that says so; the goals (the settings) are still listed, a dash for each value
    return `<h2 class="h2 sep" id="r-custom">${esc(title)}</h2>${rCap(gcT("report.custom.caption"))}
      <p class="plain">${gcT(ca.ranges_differ ? "report_page.custom.ranges.differ" : "report_page.custom.ranges.equal", ranges)}</p>
      ${st.enough_data === false ? `<p class="plain">${rEnoughShort(st.active, d.ndays)}</p>` : ""}
      ${rScroll("r-custom", `<table class="t wide">${rCaption(title)}<tr><th scope="col">${gcT("report.custom.col.metric")}</th><th scope="col">${gcT("report.custom.col.value")}</th>` +
      `<th scope="col">${gcT("report.custom.col.goal")}</th><th scope="col">${gcT("report.custom.col.result")}</th><th scope="col">${gcT("report.custom.col.consensus")}</th></tr>${rows}</table>`)}
      ${rNotes([[null, gcT("report.custom.notes")], [null, `${compared} ${gcT("report.custom.note")}`]])}`;
  }

  /** Return the appendix "Measures Defined for Research" (include_research_appendix); no color, no target. */
  researchAppendix(d) {
    const ra = d.research_appendix;
    if (!d.include_research_appendix || !ra) return "";
    // GlucoCraft's method, one list item per rule (the six steps of the PDF), in the Notes after the intro, the
    // definition and the absent clinical-care standard (the PDF's Report.research_appendix)
    const method = [1, 2, 3, 4, 5, 6].map((n) => `<li>${dtx(gcT(`report.research.method.${n}`))}</li>`).join("");
    const lead = (k) => gcT(`report.research.${k}_lead`);
    // without enough data in the period: the line that says so, and a dash for each value (the PDF's)
    return `<h2 class="h2 sep">${gcT("report.research.title", { period: esc(d.label) })}</h2>${rCap(rCtxT("report.research.caption"))}
      ${rHas(ra.events) ? "" : `<p class="plain">${rEnoughShort(d.period?.active, d.ndays)}</p>`}
      <h3 class="h3">${gcT("report.research.events_title")}</h3>
      <table class="t">${rCaption(gcT("report.research.events_title"))}<tr><td>${gcT("report.research.events")}</td><td><b>${rHas(ra.events) ? ra.events : "-"}</b></td></tr><tr><td>${gcT("report.research.total")}</td><td><b>${ra.events ? rDurW(ra.total_minutes) : "-"}</b></td></tr>
      <tr><td>${gcT("report.research.longest")}</td><td><b>${ra.longest_minutes ? gcT("report.research.longest_value", { duration: rDurW(ra.longest_minutes), when: esc(ra.longest_when) }) : "-"}</b></td></tr></table>
      ${rNotes([[lead("intro"), rCtxT("report.research.intro")], [lead("definition"), rCtxT("report.research.definition")],
        [lead("no_standard"), rCtxT("report.research.no_standard", RADA)], [lead("method"), gcT("report.research.method_note")],
        `<ol class="plain">${method}</ol>`, [lead("limits"), gcT("report.research.limits")]])}`;
  }

  /**
   * Return the AGPL-3.0-only notices at the end of the report: the texts the PDF prints, from the report data
   * (report_service.license_texts: the license's name, the copyright line, the GNU notice and the license's address),
   * in the PDF's order (report_pdf.Report.license_section). The license link opens in a new tab and says so to screen
   * readers.
   */
  license() {
    const d = this._data || {}, L = d.license || {}, v = d.integration_version;   // the version that made the data
    const link = `<a class="lnk" href="${esc(L.url || "")}" target="_blank" rel="noopener">${esc(L.url || "")}` +
      `<span class="sr"> ${esc(gcT("ui.link.new_tab"))}</span></a>`;
    return `<h2 class="h2 sep">${gcT("report.license.title")}</h2><div class="sub">
        <p>${gcT("report.license.licensed", { license: esc(L.name || "") })}</p>
        <p>${esc(v ? `GlucoCraft ${v} · ${L.copyright || ""}` : `GlucoCraft · ${L.copyright || ""}`)}</p>
        ${(L.notice || []).map((n) => `<p>${esc(n)}</p>`).join("")}
        ${L.url ? `<p>${gcT("report.license.url", { url: link })}</p>` : ""}</div>`;
  }
}

if (!customElements.get("glucocraft-report")) customElements.define("glucocraft-report", GlucoCraftReport);
// The card picker's entry (the file is loaded twice: added once), with its texts in the frontend's language: from the
// hass object of Home Assistant's <home-assistant> element, an undocumented property, read only here because the entry
// is made before any card receives hass; without it, the language of the page (<html lang>)
gcI18nLoad(globalThis.document?.querySelector?.("home-assistant")?.hass).then(() => {
  window.customCards = window.customCards || [];
  if (!window.customCards.some((c) => c.type === "glucocraft-report")) {
    window.customCards.push({ type: "glucocraft-report", name: gcT("report_page.picker.name"),
      description: gcT("report_page.picker.description") });
  }
});
console.info(`%c GLUCOCRAFT-REPORT %c v${GCREPORT_VERSION} `, "background:#7E57C2;color:#fff", "background:#000;color:#fff");
