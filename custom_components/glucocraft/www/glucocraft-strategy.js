// License: AGPL-3.0-only (LICENSE).
/*
 * glucocraft-strategy - GlucoCraft's own dashboard, built by a Lovelace dashboard strategy: a Start page and sub-pages
 * that open with Home Assistant's back arrow, without a tab bar. Built only from GlucoCraft's cards (glucocraft-ui.js,
 * the display card, the report and Documentation cards, the text card below) and Home Assistant's grid, heading and
 * entities cards. Every page: banners -> headline -> content -> About This Page -> the footer with the disclaimer line.
 * The custom ranges and the consensus ranges are never shown on the same page.
 *
 *   custom:glucocraft-markdown   the text card of the pages (About This Page, the Maintenance and Settings Status
 *                                texts): Markdown, with Jinja templates rendered by Home Assistant
 */
const GC_STRATEGY_VERSION = "0.0.1";
// The language catalogues (glucocraft-i18n.js, served next to this file): every text comes from them, in Home
// Assistant's language with English as the fallback. The card waits for gcI18nLoad before its first render.
const GC_I18N_AREAS = ["frontend"];
const GC_I18N = globalThis.glucocraftI18n ? Promise.resolve() : import(new URL("./glucocraft-i18n.js", import.meta.url).href);
const gcI18nLoad = (hass) => GC_I18N.then(() => globalThis.glucocraftI18n.load(hass, GC_I18N_AREAS));
const gcI18nReady = (hass) => !!globalThis.glucocraftI18n?.ready(hass, GC_I18N_AREAS);
const gcT = (key, params) => globalThis.glucocraftI18n.t(key, params);
// The rules the cards and pages share (glucocraft-shared.js, served next to this file and imported the same way): here
// the decimal separator in use, the HTML escape, the redraw that keeps the focus, and the link color and focus ring.
// The file waits for it before it defines the strategy.
const GC_SHARED = globalThis.glucocraftShared ? Promise.resolve() : import(new URL("./glucocraft-shared.js", import.meta.url).href);
await GC_SHARED;
// dtx writes the decimal separator in fixed texts ("3.9" -> "3,9"); generate() sets the separator from the configuration
// sensor (applyRegional) when the dashboard is built. Not used in templates (their numbers are code): they use the
// macros in GJ.
// STALE_DEFAULT_MIN, RETENTION_DAYS: the no-current-data limit (minutes) and the days of history kept, shown while the
// configuration sensor has no attributes (GlucoCraft reloading). state_attr returns None for a missing attribute, so
// the templates use default(value, true), which replaces None too
const { settings: GC, applyRegional, MGDL_PER_MMOLL, esc, unesc, gcRender, css: GC_CSS, gcSpeakText,
  gcNavigate, gcPlainClick, STALE_DEFAULT_MIN, RETENTION_DAYS, dtx } = globalThis.glucocraftShared;

// Glucose in the display unit inside a Jinja template; the template must set `c` (the configuration attributes) first.
// gv(v): a value or limit stored in mg/dL (a limit entered in mmol/L is stored with one decimal, 8.9 mmol/L = 160.3
// mg/dL) as shown: whole mg/dL, halves away from zero (units.round_half_up), or mmol/L with one decimal (the
// consensus thresholds as published).
// The decimal separator (decimal_separator "." / ",") and the A1C / GMI unit (a1c_unit "percent" / "mmol_mol" / "both")
// apply to the displayed values only. a1(p): a GMI goal in the A1C / GMI unit; its mmol/mol value by the GMI rule, as
// the GMI values it is compared with (12.71 + 4.70587 x the mean glucose in mmol/L that gives p %; Bergenstal et al.,
// 2018). Its numbers are a copy of glucocraft-shared.js GMI: a template is code that Home Assistant runs, and cannot
// read them from there.
const GJ = "{%- set gmm = (c.unit | default('mg/dL')) == 'mmol/L' -%}" +
  "{%- set gds = ',' if c.decimal_separator == ',' else '.' -%}{%- set gau = c.a1c_unit | default('percent', true) -%}" +
  "{%- set gcm = {54: 3.0, 70: 3.9, 140: 7.8, 180: 10.0, 250: 13.9} -%}" +
  "{%- macro gv(v) -%}{%- if not gmm -%}{{ ((v | float(0)) + 0.5 + 1e-9) | round(0, 'floor') | int }}" +
  "{%- elif (v | float(0)) == (v | int(0)) and (v | int(0)) in gcm -%}{{ (gcm[v | int(0)] | string) | replace('.', gds) }}" +
  "{%- else -%}{{ ('%.1f' | format(((((v | float(0)) / " + MGDL_PER_MMOLL + ") * 10 + 0.5 + 1e-9)" +
  " | round(0, 'floor')) / 10))" +
  " | replace('.', gds) }}{%- endif -%}{%- endmacro -%}" +
  "{%- macro dn(v) -%}{{ (v | string) | replace('.', gds) }}{%- endmacro -%}" +
  "{%- macro a1(p) -%}{%- set pp = ('%.1f' | format((((p | float(0)) * 10 + 0.5 + 1e-9) | round(0, 'floor')) / 10))" +
  " | replace('.', gds) -%}" +
  "{%- set mm = ((12.71 + 4.70587 * (((p | float(0)) - 3.31) / 0.02392) / " + MGDL_PER_MMOLL + ") + 0.5 + 1e-9)" +
  " | round(0, 'floor') | int -%}" +
  "{{ (mm ~ ' mmol/mol') if gau == 'mmol_mol' else (pp ~ '% (' ~ mm ~ ' mmol/mol)') if gau == 'both' else (pp ~ '%') }}" +
  "{%- endmacro -%}";

// the address of the Documentation page (set by generate() for the dashboard's own path)
let DOCS_LINK = "documentation";
// The citation labels of the ADA Standards of Care, Section 6 = 2026a, Section 7 = 2026b (both in Documentation >
// References): author names, not translated; the cited-context texts take them as {ada_a} / {ada_b}.
const ADA_A = "American Diabetes Association Professional Practice Committee for Diabetes, 2026a";
const ADA_B = "American Diabetes Association Professional Practice Committee for Diabetes, 2026b";
const CFG = "sensor.glucocraft_configuration";
// the Last reading time sensor: the newest reading's time, with the reading count and each source's newest measurement
const LAST = "sensor.glucocraft_last_reading_time";

// ---------------------------------------------------------------- texts inside Jinja templates
// The Markdown cards are Jinja templates that Home Assistant renders. A text used inside a Jinja expression is written
// as a quoted Jinja string literal (jstr); a catalogue template with placeholders becomes a Jinja concatenation in
// which each placeholder is a Jinja expression (jexpr: "on, {value}" -> 'on, ' ~ <value>), so a translation keeps its
// own word order. jdict / jlist / jpairs write Jinja dictionaries and lists of such texts.
const jstr = (s) => `'${String(s).replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`;
const jexpr = (text, exprs) => text.split(/(\{\w+\})/).filter(Boolean)
  .map((p) => (/^\{\w+\}$/.test(p) && Object.hasOwn(exprs, p.slice(1, -1)) ? exprs[p.slice(1, -1)] : jstr(p)))
  .join(" ~ ") || "''";
const jif = (cond, yes, no) => `{{ ${jstr(yes)} if ${cond} else ${jstr(no)} }}`;
const jdict = (texts) => `{${Object.entries(texts).map(([k, v]) => `'${k}': ${jstr(v)}`).join(", ")}}`;
const jlist = (texts) => `[${texts.map(jstr).join(", ")}]`;
const jpairs = (texts) => `[${Object.entries(texts).map(([k, v]) => `['${k}', ${jstr(v)}]`).join(", ")}]`;

// ---------------------------------------------------------------- cited context: the sentences that explain a target,
// a limit or a rule from its published source (docs/LANGUAGE_STYLE.md rules 15 and 16), each at most once per page, in
// About This Page; the page texts take them as placeholders {cx_<name>}. The targets for other groups are stated once,
// in `groups` (on Consensus Ranges and in the Documentation), with the article's DOI.
const CX_KEY = {
  cx_below: "strategy.cx.below", cx_limits: "strategy.cx.limits", cx_tir: "strategy.cx.tir",
  cx_per_day: "strategy.cx.per_day", cx_above: "strategy.cx.above", cx_tight: "strategy.cx.tight",
  cx_enough: "strategy.cx.enough", cx_active: "strategy.cx.active", cx_cv: "strategy.cx.cv",
  cx_cv_primary: "strategy.cx.cv_primary", cx_cv_stable: "strategy.cx.cv_stable", cx_gmi: "strategy.cx.gmi",
  cx_gmi_days: "strategy.cx.gmi_days", cx_gmi_formula: "strategy.cx.gmi_formula", cx_gri: "strategy.cx.gri",
  cx_names: "strategy.cx.names", cx_trend: "strategy.cx.trend", cx_dagger: "strategy.cx.dagger",
  cx_groups: "strategy.cx.groups", cx_forms: "strategy.cx.forms",
};
const citedContext = () => Object.fromEntries(Object.entries(CX_KEY).map(([name, key]) => [name, gcT(key, { ada_a: ADA_A, ada_b: ADA_B })]));

// the color key: one small line at the bottom of each page that uses colors, with only the colors that page draws and
// a link to Documentation > Colors; pages without colors have none. glucose: the glucose values (display colors, with
// the zone word) and the graph bands (the display card on Start); bands: the graph bands alone; lohi: a reading at the
// sensor's limit (the LO / HI chip of the Statistics card, the LO / HI screen of the display card and the marks on the
// graphs); faint_dots: the 7-day dots of the Change Over Time charts; green, red_status, amber, purple, gray: met / not
// met, the banners (no current data, sensor expired, expiring, second source, warm-up), the notes of the 70% rule and
// the neutral charts; *_start: the Start page (also the display card's status bar, and the Maintenance button);
// purple_status: the status bar and the Maintenance headline, also while no data has been received yet; gray_status:
// the Maintenance headline before GlucoCraft has a status; data_*: the Maintenance page (data received, the CSV export
// and the maintenance issues)
const COLOR_KEY = {
  green: "strategy.colors.green", red_status: "strategy.colors.red_status", amber: "strategy.colors.amber",
  amber_start: "strategy.colors.amber_start", green_start: "strategy.colors.green_start", red_start: "strategy.colors.red_start",
  purple: "strategy.colors.purple", purple_status: "strategy.colors.purple_status",
  gray: "strategy.colors.gray", gray_start: "strategy.colors.gray_start",
  gray_status: "strategy.colors.gray_status", glucose: "strategy.colors.glucose", bands: "strategy.colors.bands",
  lohi: "strategy.colors.lohi", light_gray: "strategy.colors.light_gray", target_side: "strategy.colors.target_side",
  faint_dots: "strategy.colors.faint_dots",
  data_green: "strategy.colors.data_green", data_red: "strategy.colors.data_red", data_amber: "strategy.colors.data_amber",
};
const colorKey = (items) => gcT("strategy.colors.key", {
  items: items.map((c) => gcT(COLOR_KEY[c])).join(gcT("strategy.separator.long_list")), url: `${DOCS_LINK}#colors` });

const full = (card) => ({ ...card, grid_options: { columns: "full" } });
const section = (cards, span = 4) => ({ type: "grid", column_span: span, cards: cards.map(full) });
const heading = (text, icon) => ({ type: "heading", heading: text, heading_style: "title", icon });
// a text card (custom:glucocraft-markdown, below); names: the name of each of its tables, in order
const md = (content, names) => ({ type: "custom:glucocraft-markdown", content, ...(names ? { tables: names } : {}) });
// About This Page: the page's text, then further list items (raw: not given the decimal separator, e.g. a DOI) and the
// References item, with its link to Documentation > References
const how = (text, ...raw) => section([heading(gcT("strategy.about.title"), "mdi:information-outline"),
  md(dtx(text) + [...raw, gcT("strategy.about.references", { url: `${DOCS_LINK}#references` })].map((item) => `\n- ${item}`).join(""))]);
// the disclaimer line of every page footer, with its link to the full disclaimer in the Documentation
const disclaimerLine = () => gcT("strategy.footer.disclaimer", { url: `${DOCS_LINK}#disclaimer` });
// the AGPL-3.0-only legal notice in every footer (the "appropriate legal notices" of AGPLv3 section 5(d)), linking to
// Documentation > About and License
const licenseLine = () => gcT("strategy.footer.license", { url: `${DOCS_LINK}#about` });
// The page footer (glucocraft-footer): key, the color key of the page (colorKey), as its first line; the disclaimer
// line; the short standard statement and the license line.
const footer = (key = "") => section([{ type: "custom:glucocraft-footer", key, disclaimer: disclaimerLine(),
  attribution: gcT("strategy.footer.attribution"), license: licenseLine() }]);
// The Documentation and the CGM Report carry the standard statement in their own text, so their footer is the
// disclaimer line and the license line.
const licenseFooter = () =>
  section([{ type: "custom:glucocraft-footer", disclaimer: disclaimerLine(), license: licenseLine() }]);

// the change-over-time items of About This Page, per range set
const CHANGE_KEY = { consensus: "strategy.change.about.consensus", personal: "strategy.change.about.personal" };
// the words of the Settings Status page's Jinja tables: zones, weekdays (Monday first, as first_weekday 0-6), goals
const ZONE_KEY = {
  very_low: "strategy.zone.very_low", low: "strategy.zone.low", near_low: "strategy.zone.near_low",
  tight_range: "strategy.zone.tight_range", near_high: "strategy.zone.near_high", high: "strategy.zone.high",
  very_high: "strategy.zone.very_high",
};
const DAY_KEYS = ["card.date.weekday.monday", "card.date.weekday.tuesday", "card.date.weekday.wednesday",
  "card.date.weekday.thursday", "card.date.weekday.friday", "card.date.weekday.saturday", "card.date.weekday.sunday"];
const GOAL_KEY = {
  time_in_range: "strategy.goal.time_in_range", time_in_tight_range: "strategy.goal.time_in_tight_range",
  time_low: "strategy.goal.time_low", time_very_low: "strategy.goal.time_very_low", time_high: "strategy.goal.time_high",
  time_very_high: "strategy.goal.time_very_high", cv: "strategy.goal.cv", gmi: "strategy.goal.gmi",
};
const texts = (keys) => Object.fromEntries(Object.entries(keys).map(([k, key]) => [k, gcT(key)]));

// ---------------------------------------------------------------- the text card (custom:glucocraft-markdown)
// The texts of the pages in GlucoCraft's own card instead of Home Assistant's Markdown card, so that GlucoCraft's rules
// reach them (docs/ACCESSIBILITY.md rules 2 to 5): the link color and the focus ring of every card (glucocraft-shared.js
// css), no text below 14 px, the language of the catalogue in use (lang), and tables with a name, header cells with
// their scope and a scroll region that the keyboard reaches and that is named by the table.
// The Markdown of the page texts: paragraphs (an empty line between them; a single line break is kept), lists ("- ",
// "1. "), tables ("| a | b |" with the line "|---|---|" under the header row), **bold**, *italic*, `code` and
// [text](address). Anything else is text: the whole text is escaped first, so that a name from the configuration (a
// source, a panel) is never read as markup.
const MD_TEMPLATE = /\{\{|\{%|\{#/;   // a Jinja template: rendered by Home Assistant
const MD_ROW = /^\s*\|/, MD_RULE = /^\s*\|?\s*:?-{3,}/, MD_ITEM = /^\s*(?:([-*])|\d+\.)\s+(.*)$/;
// a link: an address inside the dashboard ("/...") opens its page without reloading (data-nav), an https address opens
// in a new tab and says so to screen readers (WCAG technique G201), any other address is shown as text only
const mdLink = (text, url) => (/^\/(?!\/)/.test(url) ? `<a href="${url}" data-nav="${url}">${text}</a>`
  : /^https?:\/\//.test(url)
    ? `<a href="${url}" target="_blank" rel="noopener">${text}<span class="sr"> ${esc(gcT("ui.link.new_tab"))}</span></a>`
    : text);
// the inline marks of one escaped line; code first, so that nothing inside it is read as a mark: each code span is
// set aside and its place held by its number between the sentinels \uE000 and \uE001 (Unicode private-use
// characters, which no text contains), so that the last step puts back only those numbers, not every number
const mdInline = (line) => {
  const codes = [];
  return esc(line).replace(/`([^`]+)`/g, (m, code) => `\uE000${codes.push(code) - 1}\uE001`)
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, text, url) => mdLink(text, url))
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/\*([^*\s][^*]*)\*/g, "<i>$1</i>")
    .replace(/\uE000(\d+)\uE001/g, (m, i) => `<code>${codes[+i]}</code>`);
};
// a table in a scroll region that the keyboard reaches (tabindex), named by the table's caption (shown to screen
// readers only: the heading or text above the table names it on the screen); the header row's cells are column
// headers, the first cell of each row its row header
const mdTable = (head, rows, name, id) => `<div class="tw" tabindex="0" role="region" aria-labelledby="${id}"><table>` +
  `<caption id="${id}" class="sr">${esc(name)}</caption><thead><tr>` +
  head.map((cell) => `<th scope="col">${mdInline(cell)}</th>`).join("") + "</tr></thead><tbody>" +
  rows.map((row) => "<tr>" + head.map((h, j) => (j ? `<td>${mdInline(row[j] ?? "")}</td>`
    : `<th scope="row">${mdInline(row[0] ?? "")}</th>`)).join("") + "</tr>").join("") + "</tbody></table></div>";
// The spoken rule (ACCESSIBILITY.md rule 4) on the text of an HTML fragment: every run of text between its tags that
// holds a unit symbol, a comparison sign before a number or another mark with a spoken form (gcSpeakText) is shown as
// written and hidden from screen readers, which read its spoken form; the tags, a code element's text and everything
// else stay as they are
const mdSpoken = (html) => {
  let code = 0;
  return html.split(/(<[^>]+>)/).map((part) => {
    if (part.startsWith("<")) { code += /^<code\b/.test(part) ? 1 : /^<\/code>/.test(part) ? -1 : 0; return part; }
    if (!part || code > 0) return part;
    const shown = unesc(part), said = gcSpeakText(shown);
    return said === shown ? part : `<span aria-hidden="true">${part}</span><span class="sr">${esc(said)}</span>`;
  }).join("");
};
/** Return the HTML of a Markdown text; names: the name of each table, in order (else its header row). */
const mdHtml = (text, names = []) => {
  const lines = String(text).replace(/\r\n?/g, "\n").split("\n"), cells = (row) => row.trim().replace(/^\|/, "")
    .replace(/\|$/, "").split("|").map((c) => c.trim());
  let out = "", para = [], list = null, items = [], tables = 0;
  const endPara = () => { if (para.length) out += `<p>${para.map(mdInline).join("<br>")}</p>`; para = []; };
  const endList = () => {
    const li = (item) => `<li>${item.map(mdInline).join("<br>")}</li>`;
    if (list) out += `<${list}>${items.map(li).join("")}</${list}>`;
    list = null; items = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i], item = line.match(MD_ITEM);
    if (!line.trim()) { endPara(); endList(); continue; }
    if (MD_ROW.test(line) && MD_RULE.test(lines[i + 1] || "")) {
      endPara(); endList();
      const head = cells(line), rows = [];
      for (i += 2; i < lines.length && MD_ROW.test(lines[i]); i++) rows.push(cells(lines[i]));
      i--;
      tables++;
      out += mdTable(head, rows, names[tables - 1] || head.join(gcT("strategy.separator.list")), `t${tables}`);
    } else if (item) {
      const kind = item[1] ? "ul" : "ol";
      endPara();
      if (list !== kind) { endList(); list = kind; }
      items.push([item[2]]);
    } else if (list) items[items.length - 1].push(line.trim());   // a line under a list item continues the item
    else para.push(line.trim());
  }
  endPara(); endList();
  return mdSpoken(out);
};

/**
 * A text of a page (custom:glucocraft-markdown): content, its Markdown; tables, the names of its tables.
 *
 * A text with a Jinja template is rendered by Home Assistant as its Markdown card renders one (the websocket command
 * render_template, strict, sent again whenever an entity it reads changes); a text without one is shown as it is. A
 * template that Home Assistant refuses is shown as written, as the Markdown card does. Each new result replaces only
 * what changed, and the focus stays on its control (gcRender).
 */
class GlucoCraftMarkdown extends HTMLElement {
  /** The HTML of a Markdown text (for the tests). */
  static html(text, names) { return mdHtml(text, names); }
  setConfig(config) {
    if (config?.content !== this._cfg?.content) { this.unsubscribe(); this._text = null; this._failed = false; }
    this._cfg = config || {};
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.subscribe();
    this.draw();
  }
  set hass(hass) {
    this._hass = hass;
    if (!gcI18nReady(hass)) this._i18n ||= gcI18nLoad(hass).then(() => { this._i18n = null; this.draw(); });
    this.subscribe();
    this.draw();
  }
  connectedCallback() { this.subscribe(); }
  disconnectedCallback() { this.unsubscribe(); }
  // the text to show: the content itself, or the result of its template while the card is on the page
  subscribe() {
    const content = String(this._cfg?.content ?? "");
    if (!MD_TEMPLATE.test(content) || this._failed) { this._text = content; return; }
    if (this._unsub || !this._hass?.connection || !this.isConnected) return;
    const sub = this._unsub = this._hass.connection.subscribeMessage((msg) => {
      if (typeof msg?.result === "string") { this._text = msg.result; this.draw(); }
    }, { type: "render_template", template: content, strict: true });
    sub.catch(() => {
      if (this._unsub !== sub) return;   // unsubscribed meanwhile
      this._unsub = null; this._failed = true; this._text = content; this.draw();
    });
  }
  unsubscribe() {
    const sub = this._unsub;
    this._unsub = null;
    sub?.then((stop) => stop()).catch(() => { /* never subscribed */ });
  }
  /**
   * Draw the text, once the language catalogues are loaded and the text (or the template's first result) is known;
   * again only when the text or the language changes (Home Assistant passes every state change of every entity).
   */
  draw() {
    const root = this.shadowRoot;
    if (!root || typeof this._text !== "string" || !this._hass || !gcI18nReady(this._hass)) return;
    const drawn = JSON.stringify([globalThis.glucocraftI18n.language, this._cfg.tables || null, this._text]);
    if (drawn === this._drawn) return;
    this._drawn = drawn;
    if (!root.querySelector("ha-card")) {
      root.innerHTML = `<style>
          :host { display: block; }
          ha-card { padding: 16px; color: var(--primary-text-color); font-size: 14px; line-height: 1.5; }
          .md { overflow-wrap: break-word; }
          .md > :first-child { margin-top: 0; } .md > :last-child { margin-bottom: 0; }
          p { margin: 0 0 10px; } ul, ol { margin: 0 0 10px; padding-left: 20px; } li { margin: 4px 0; }
          a { color: ${GC_CSS.link}; }
          a:focus-visible, .tw:focus-visible { ${GC_CSS.focus} }
          code { font-family: var(--ha-font-family-code, monospace); font-size: 14px; overflow-wrap: anywhere; }
          .tw { overflow-x: auto; margin: 0 0 10px; }
          table { border-collapse: collapse; width: 100%; font-size: 14px; }
          th, td { text-align: left; vertical-align: top; padding: 5px 8px; overflow-wrap: normal;
                   border-bottom: 1px solid var(--divider-color); }
          thead th { font-weight: 600; } tbody th { font-weight: inherit; }
          ${GC_CSS.sr}
        </style><ha-card lang="${globalThis.glucocraftI18n.htmlLang}"><div class="md"></div></ha-card>`;
    }
    root.querySelector("ha-card").setAttribute("lang", globalThis.glucocraftI18n.htmlLang);
    if (!gcRender(root, mdHtml(this._text, this._cfg.tables), root.querySelector(".md"))) return;
    root.querySelectorAll("a[data-nav]").forEach((a) => {
      a.onclick = (e) => {
        if (!gcPlainClick(e)) return;   // the browser opens the link itself
        e.preventDefault();
        gcNavigate(a.dataset.nav);
      };
    });
  }
  getCardSize() { return 3; }
}

// ---------------------------------------------------------------- the pages of the dashboard
// Each page is built by its own function from the context of one generate() call (pageContext). The functions run
// after the catalogues are loaded and the regional settings are applied, so their texts are in the language and the
// decimal separator in use.

/**
 * Return what the page builders share for the dashboard at the path base, with the configuration attributes cfg:
 * { base, cfg, go(page): the address of one of its pages, docsAt(id): of a Documentation section (its anchor,
 * glucocraft-docs.js SECTIONS), cx: the cited context, notes: the notes several pages state, changeHow(basis): the
 * change-over-time item of About This Page, sub(...): a sub-page }.
 */
const pageContext = (base, cfg) => {
  const go = (p) => `${base}/${p}`;
  const docsAt = (id) => `${DOCS_LINK}#${id}`;
  const cx = citedContext();
  return {
    base, cfg, go, docsAt, cx,
    notes: {
      custom_statement: gcT("strategy.note.custom_ranges"),
      limit_note: gcT("strategy.note.sensor_limits", { lohi_url: docsAt("lohi") }),
      interval_note: gcT("strategy.note.reading_interval"),
    },
    changeHow: (basis) => gcT(CHANGE_KEY[basis], cx),
    // a sub-page: it opens from Start, and Home Assistant's back arrow returns there
    sub: (title, path, icon, sections, maxColumns = 4) => ({
      title, path, icon, type: "sections", max_columns: maxColumns, subview: true, back_path: go("start"), sections,
    }),
  };
};
// The gap threshold in use in minutes ("15", "17.5", "17,5"), with the decimal separator in use, as a Jinja expression;
// one decimal, halves away from zero as everywhere in GlucoCraft (Jinja's round rounds a half to even). Without the
// attribute, 900 s: the gap rule's 15-minute floor (glucocraft-shared.js gcGapThreshold).
const gapMinutes = () => "(((((state_attr('" + CFG + "', 'gap_threshold_seconds') | float(900)) / 60) * 10 + 0.5 + 1e-9)" +
  " | round(0, 'floor')) / 10) | string | replace('.0', '') | replace('.', " + jstr(GC.dec) + ")";

// ---------------------------------------------------------------- Start
// the sources of the readings, as a template: the primary source by name (or the selected entity), with the second
// source if set
const readingSources = () => {
  const primaryName = "{{ state_attr('" + CFG + "', 'primary_source').name if state_attr('" + CFG + "', 'primary_source') else " +
    jstr(gcT("strategy.start.readings.selected_entity")) + " }}";
  return "{% if state_attr('" + CFG + "', 'secondary_source') %}" +
    gcT("strategy.start.readings.with_second_source", { primary: primaryName,
      secondary: "{{ state_attr('" + CFG + "', 'secondary_source').name }}" }) +
    "{% else %}" + gcT("strategy.start.readings.one_source", { primary: primaryName }) + "{% endif %}";
};
/** Return the Start page: the display card and the pages card. */
const startPage = ({ base, cfg, go, docsAt }) => ({
  title: "GlucoCraft", path: "start", icon: "mdi:diabetes", type: "sections", max_columns: 4,
  sections: [
    section([{ type: "custom:glucocraft-banners" }]),
    // the display card and the pages card: each at its full size, side by side when both fit, otherwise stacked.
    // The card type follows the Display card setting live; this value is only the first one shown
    section([{ type: "custom:glucocraft-start-pair", card: cfg.display_card,
      pages: { base, report: true } }]),
    how(gcT("strategy.start.about", { docs_url: go("documentation"), readings: readingSources(), settings_url: go("settings"),
      lohi_url: docsAt("lohi"), ranges_url: docsAt("ranges"), gap_minutes: "{{ " + gapMinutes() + " }}",
      no_data_minutes: "{{ state_attr('" + CFG + "', 'no_current_data_minutes') | default(" + STALE_DEFAULT_MIN + ", true) }}" })),
    // the display card (glucose values, graph bands, status bar, the gray charts of the weekly pages), the pages
    // card (✓ / ✗ and the Maintenance status) and the banners
    footer(colorKey(["glucose", "lohi", "light_gray", "green_start", "red_start", "target_side", "amber_start",
      "purple_status", "gray_start"])),
  ],
});

// ---------------------------------------------------------------- Today (consensus ranges)
/** Return the Today page: the last 24 hours, the statistics table of the short periods and the four graphs. */
const todayPage = ({ sub, cx }) => sub(gcT("strategy.page.today"), "today", "mdi:calendar-today", [
  section([{ type: "custom:glucocraft-banners" }]),
  section([{ type: "custom:glucocraft-readout", view: "today", hero: true }]),
  section([heading(gcT("strategy.today.statistics"), "mdi:table"),
    { type: "custom:glucocraft-stats-table", periods: ["6h", "24h", "7d", "14d"], basis: "consensus" }]),
  section([heading(gcT("strategy.today.graphs"), "mdi:chart-line"),
    { type: "custom:glucocraft-history", hours: 6, title: gcT("strategy.graph.last_6_hours") },
    { type: "custom:glucocraft-history", hours: 24, title: gcT("strategy.graph.last_24_hours") },
    { type: "custom:glucocraft-history", hours: 168, title: gcT("strategy.graph.last_7_days") },
    { type: "custom:glucocraft-history", hours: 336, title: gcT("strategy.graph.last_14_days") }]),
  how(gcT("strategy.today.about", cx)),
  // the graph bands, ✓ / ✗ and the data-quality warnings of the table, the banners
  footer(colorKey(["bands", "lohi", "light_gray", "green", "red_status", "amber", "purple"])),
]);

// ---------------------------------------------------------------- the two range sets: one page per
// range set, each with Statistics and Change Over Time. Never both range sets on one page.
// shown first: the core consensus measures and time in tight range (the first measure on Custom Ranges)
const CORE_FOR = { consensus: ["tir", "titr", "below", "very_low", "above"], personal: ["titr", "tir", "below", "very_low", "above"] };
const MORE = ["very_high", "mean", "sd", "cv", "gmi", "gri", "min"];   // behind the Show All Statistics button
// one Change Over Time chart of a range set, in a column of its own
const chart = (basis) => (metric) => ({ type: "grid", column_span: 1,
  cards: [full({ type: "custom:glucocraft-change-chart", metric, basis })] });

// ---------------------------------------------------------------- Consensus Ranges
/** Return the Consensus Ranges page: the core measures, their Change Over Time charts and all statistics. */
const consensusPage = ({ sub, cx, changeHow }) => sub(gcT("strategy.page.consensus"), "consensus", "mdi:earth", [
  section([{ type: "custom:glucocraft-banners" }], 2),
  section([{ type: "custom:glucocraft-readout", view: "consensus", hero: true }], 2),
  section([heading(gcT("strategy.consensus.core"), "mdi:bullseye-arrow"), { type: "custom:glucocraft-core", basis: "consensus" }], 2),
  section([heading(gcT("strategy.change.heading"), "mdi:chart-timeline-variant")], 2),
  ...CORE_FOR.consensus.map(chart("consensus")),
  section([{ type: "custom:glucocraft-details", title: gcT("strategy.consensus.details.title"),
    subtitle: gcT("strategy.consensus.details.subtitle"),
    cards: [{ type: "custom:glucocraft-statistics" }, { type: "custom:glucocraft-change", basis: "consensus" },
      ...MORE.map((metric) => ({ type: "custom:glucocraft-change-chart", metric, basis: "consensus" }))] }], 2),
  { ...how(gcT("strategy.consensus.about", { ...cx, change: changeHow("consensus") }), cx.cx_groups), column_span: 2 },
  // the Statistics card's LO / HI mark, ✓ / ✗, the warnings, the banners and the neutral charts
  { ...footer(colorKey(["lohi", "green", "red_status", "target_side", "faint_dots", "amber", "purple", "gray"])),
    column_span: 2 },
], 2);

// ---------------------------------------------------------------- Custom Ranges (code name: personal)
// the ranges and goals in use, as a template: the six limits in the display unit and whether the goals are custom
const rangesInUseText = ({ go, notes }) =>
  "{%- set c = states." + CFG + ".attributes if states." + CFG + " is not none else {} -%}" +
  "{%- set r = c.ranges | default({}) -%}" + GJ + "{% if not r.very_low is defined %}" + gcT("strategy.waiting") +
  "{% else %}" + gcT("strategy.custom.intro", { settings_url: go("settings"), custom_statement: notes.custom_statement,
    ranges: jif("is_state('binary_sensor.glucocraft_using_custom_ranges', 'on')", gcT("strategy.custom.ranges_custom"),
      gcT("strategy.ranges.consensus_cited")),
    very_low: "{{ gv(r.very_low) }}", low: "{{ gv(r.low) }}", tight_low: "{{ gv(r.tight_low) }}",
    tight_high: "{{ gv(r.tight_high) }}", high: "{{ gv(r.high) }}", very_high: "{{ gv(r.very_high) }}",
    unit: "{{ c.unit }}",
    goals: jif("c.goals_custom", gcT("strategy.custom.goals_custom"), gcT("strategy.custom.goals_default")) }) +
  "{% endif %}";
/**
 * Return the Custom Ranges page: where the custom ranges and goals make the results differ, the ranges in use, the
 * core measures, their Change Over Time charts and all statistics on the ranges in the settings.
 */
const customRangesPage = ({ sub, go, cx, notes, changeHow }) => sub(gcT("strategy.page.custom"), "custom", "mdi:account-cog", [
  section([{ type: "custom:glucocraft-banners" }], 2),
  section([{ type: "custom:glucocraft-readout", view: "personal", hero: true }], 2),
  // where the custom ranges and goals make the results differ from the consensus ranges
  section([{ type: "custom:glucocraft-custom-diff" }], 2),
  section([heading(gcT("strategy.custom.in_use_heading"), "mdi:tune-variant"), md(rangesInUseText({ go, notes }))], 2),
  section([heading(gcT("strategy.custom.core"), "mdi:bullseye-arrow"), { type: "custom:glucocraft-core", basis: "personal" }], 2),
  section([heading(gcT("strategy.change.heading"), "mdi:chart-timeline-variant")], 2),
  ...CORE_FOR.personal.map(chart("personal")),
  section([{ type: "custom:glucocraft-details", title: gcT("strategy.custom.details.title"),
    subtitle: gcT("strategy.custom.details.subtitle"),
    cards: [{ type: "custom:glucocraft-stats-table", periods: ["6h", "24h", "7d", "14d"], basis: "yours" },
      { type: "custom:glucocraft-stats-table", periods: ["14d", "30d", "90d", "180d"], basis: "yours", warn_periods: ["30d", "90d", "180d"] },
      { type: "custom:glucocraft-change", basis: "personal" },
      // the GRI only on the consensus ranges, the only ranges it is defined on (Klonoff et al., 2023)
      ...MORE.filter((metric) => metric !== "gri").map((metric) => ({ type: "custom:glucocraft-change-chart", metric, basis: "personal" })),
      { type: "custom:glucocraft-history", hours: 336, basis: "yours", title: gcT("strategy.graph.last_14_days") },
      { type: "custom:glucocraft-history", hours: 720, basis: "yours", title: gcT("strategy.graph.last_30_days") },
      { type: "custom:glucocraft-history", hours: 2160, basis: "yours", title: gcT("strategy.graph.last_90_days") },
      { type: "custom:glucocraft-history", hours: 4320, basis: "yours", title: gcT("strategy.graph.last_6_months") },
      { type: "custom:glucocraft-daily-range", days: 14, basis: "yours" }] }], 2),
  { ...how(gcT("strategy.custom.about", { ...cx, settings_url: go("settings"), change: changeHow("personal") })), column_span: 2 },
  // the graph bands, ✓ / ✗, the warnings, the banners and the neutral charts
  { ...footer(colorKey(["bands", "lohi", "light_gray", "green", "red_status", "target_side", "faint_dots", "amber", "purple",
    "gray"])), column_span: 2 },
], 2);

// ---------------------------------------------------------------- Report (consensus ranges; full days; PDF)
/** Return the Report page: the CGM Report card. */
const reportPage = ({ sub }) => sub(gcT("strategy.page.report"), "report", "mdi:file-chart-outline", [
  section([{ type: "custom:glucocraft-report" }]),
  how(gcT("strategy.report.about")),
  licenseFooter(),
]);

// ---------------------------------------------------------------- Documentation
/** Return the Documentation page: the Documentation card. */
const documentationPage = ({ sub }) => sub(gcT("strategy.page.documentation"), "documentation", "mdi:book-open-variant", [
  section([{ type: "custom:glucocraft-documentation" }]),
  licenseFooter(),
]);

// ---------------------------------------------------------------- Maintenance
// GlucoCraft panels found automatically: every device of an ESPHome project "glucocraft.<type>" (manufacturer
// "glucocraft", model = the panel type, e.g. "panel"; other panel types use their own type name). One table row per
// panel (any number and type of panels), by name: [name, status entity, uptime entity, firmware, type]
const panelRowsOf = (hass) => Object.values(hass.devices || {}).filter((d) => d.manufacturer === "glucocraft").map((d) => {
  const ents = Object.values(hass.entities || {}).filter((e) => e.device_id === d.id).map((e) => e.entity_id);
  return [d.name_by_user || d.name || gcT("strategy.maintenance.panels.default_name"),
    ents.find((e) => /^binary_sensor\..*_status$/.test(e)) || "",
    ents.find((e) => /^sensor\..*_uptime$/.test(e)) || "", d.sw_version || "", d.model || ""];
}).sort((a, b) => a[0].localeCompare(b[0]));
// the panels' table as a template: the offline panels first, each with its type, status, uptime and firmware
const panelTable = (panelRows) => {
  // the uptime of a panel ("2 d 3 h 15 min") from its uptime in seconds (`up`)
  const uptime = jexpr(gcT("strategy.maintenance.panels.uptime"), { days: "(up // 86400) | int",
    hours: "((up % 86400) // 3600) | int", minutes: "((up % 3600) // 60) | int" });
  return "{% set rows = " + JSON.stringify(panelRows) + " %}" +
    "{% set ns = namespace(off=[], on=[]) %}{% for r in rows %}" +
    "{% if is_state(r[1], 'on') %}{% set ns.on = ns.on + [r] %}{% else %}{% set ns.off = ns.off + [r] %}{% endif %}{% endfor %}" +
    gcT("strategy.maintenance.panels.summary", { count: panelRows.length, online: "{{ ns.on | length }}",
      offline: "{{ ns.off | length }}" }) + "\n\n" + gcT("strategy.maintenance.panels.table") +
    "{% for r in ns.off + ns.on %}{% set up = states(r[2]) | float(-1) %}" +
    "| {{ r[0] }} | {{ r[4] or '—' }} | " +
    jif("is_state(r[1], 'on')", gcT("strategy.maintenance.panels.online"), gcT("strategy.maintenance.panels.offline")) +
    " | {{ '—' if up < 0 or not is_state(r[1], 'on') else (" + uptime + ") }} | " +
    "{{ r[3] or '—' }} |\n{% endfor %}\n" + gcT("strategy.maintenance.panels.uptime_note");
};
// A time stamp in GlucoCraft's time and date formats, in Home Assistant's time zone: "Wed 10/22 7:46 AM" / "Wed
// 22/10 07:46". The weekday's short name and AM / PM are words of the catalogue (never the server's locale, which
// strftime's %a and %p would use); the numbers come from timestamp_custom. gts(iso): the macro, defined once at
// the start of the text that uses it (timeStampMacro); "---" for a time that is missing or cannot be read.
const timeStampMacro = () => {
  const wdShort = jlist(["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
    .map((d) => gcT(`card.date.weekday_short.${d}`)));
  const h12 = "(((t | timestamp_custom('%H', true) | int) % 12) or 12) ~ ':' ~ (t | timestamp_custom('%M', true))";
  return "{%- macro gts(iso) -%}{%- set t = as_timestamp(iso, none) -%}{%- if t is none -%}---{%- else -%}" +
    "{%- set df = state_attr('" + CFG + "', 'date_format') -%}" +
    "{{ " + wdShort + "[t | timestamp_custom('%w', true) | int] ~ ' ' ~ " +
    "(t | timestamp_custom('%-d/%-m' if df == 'DMY' " +
    "else '%m-%d' if df == 'YMD' else '%-m/%-d', true)) ~ ' ' ~ ((t | timestamp_custom('%H:%M', true)) if " +
    "state_attr('" + CFG + "', 'time_format') == '24' else (" + jexpr(gcT("card.time.clock_12.am"), { time: h12 }) +
    " if (t | timestamp_custom('%H', true) | int) < 12 else " + jexpr(gcT("card.time.clock_12.pm"), { time: h12 }) +
    ")) }}{%- endif -%}{%- endmacro -%}";
};
// a time stamp of a Jinja expression (gts), with defaults, so that "unknown" / "unavailable" show "---" instead of a
// template error
const timeStamp = (iso) => "{{ gts(" + iso + ") if " + iso + " else '---' }}";
// The sources text of Maintenance, as a template: the sensor expiry, then the table of the sources with each one's
// newest measurement time (the source in use marked), the last reading, and the statistics' basis (readings, the
// reading interval, the gap threshold and the days kept)
const sourcesText = () => {
  // the sensor-expiry state is a key (not_expired, expiring_soon, ...): Home Assistant shows its name in its language
  const expiryState = "{{ state_translated('sensor.glucocraft_sensor_expiry') }}";
  // the source in use: the mark with its word ("✓ in use"), so that a screen reader says what the mark means
  const inUse = gcT("strategy.maintenance.sources.in_use");
  return timeStampMacro() +
    "{%- set e = state_attr('sensor.glucocraft_sensor_expiry', 'expires_at') -%}" +
    "{% if state_attr('" + CFG + "', 'expiry_entity') %}{% if e %}" +
    gcT("strategy.maintenance.sensor.expiry_with_date", { state: expiryState, time: timeStamp("e") }) + "{% else %}" +
    gcT("strategy.maintenance.sensor.expiry", { state: expiryState }) + "{% endif %}" +
    "{% else %}" + gcT("strategy.maintenance.sensor.not_set_up") + "{% endif %}\n\n" +
    "{% set p = state_attr('" + CFG + "', 'primary_source') %}{% set s = state_attr('" + CFG + "', 'secondary_source') %}" +
    // each source's newest measurement time and the stored reading count: attributes of the Last reading time
    // sensor, whose state changes with every reading anyway
    "{% set pt = state_attr('" + LAST + "', 'primary_last_measurement') %}" +
    "{% set st = state_attr('" + LAST + "', 'secondary_last_measurement') %}" +
    "\n\n" + gcT("strategy.maintenance.sources.table") +
    "{% if p %}" + gcT("strategy.maintenance.sources.primary", { name: "{{ p.name }}", time: timeStamp("pt"),
      in_use: jif("p.in_use", inUse, "") }) + "{% endif %}" +
    "{% if s %}" + gcT("strategy.maintenance.sources.second", { name: "{{ s.name }}", time: timeStamp("st"),
      in_use: jif("s.in_use", inUse, "") }) + "{% endif %}" +
    "\n" + gcT("strategy.maintenance.sources.last_reading", { time: timeStamp("states('" + LAST + "')"),
      minutes: "{{ state_attr('" + CFG + "', 'no_current_data_minutes') | default(" + STALE_DEFAULT_MIN + ", true) }}" }) +
    // (when the statistics were calculated is in the status headline above: stated once on the page)
    "\n\n" + gcT("strategy.maintenance.sources.statistics", {
      readings: "{{ state_attr('" + LAST + "', 'reading_count') }}",
      seconds: "{{ state_attr('" + CFG + "', 'reading_interval_seconds') or state_attr('sensor.glucocraft_active_source', 'detected_interval') }}",
      minutes: "{{ " + gapMinutes() + " }}",
      days: "{{ state_attr('" + CFG + "', 'retention_days') | default(" + RETENTION_DAYS + ", true) }}" });
};
/**
 * Return the Maintenance page: the maintenance issues, the status, the sources, the CSV export, the newest reading's
 * entities and the panels found (hass.devices).
 */
const maintenancePage = ({ sub, cx, notes, docsAt }, hass) => {
  const panelRows = panelRowsOf(hass);
  return sub(gcT("strategy.page.maintenance"), "maintenance", "mdi:wrench", [
    section([{ type: "custom:glucocraft-banners" }]),
    // the issues of the Maintenance status, each with where it is resolved (shown only while there are any)
    section([{ type: "custom:glucocraft-maintenance-issues" }]),
    section([{ type: "custom:glucocraft-readout", view: "status", hero: true }]),
    section([heading(gcT("strategy.maintenance.sources.heading"), "mdi:access-point"),
      md(sourcesText(), [gcT("strategy.maintenance.sources.table_name")])]),
    section([heading(gcT("strategy.maintenance.export.heading"), "mdi:file-delimited-outline"), { type: "custom:glucocraft-export" }]),
    section([heading(gcT("strategy.maintenance.trend.heading"), "mdi:chart-bell-curve"), {
      type: "entities", entities: ["sensor.glucocraft_last_glucose_reading", "sensor.glucocraft_5_minute_delta",
        "sensor.glucocraft_trend_arrow", LAST] }]),
    section([heading(gcT("strategy.maintenance.panels.heading"), "mdi:tablet-dashboard"), panelRows.length
      ? md(panelTable(panelRows), [gcT("strategy.maintenance.panels.heading")])
      : md(gcT("strategy.maintenance.panels.none"))]),
    how(gcT("strategy.maintenance.about", { ...cx, interval_note: notes.interval_note, how_url: docsAt("how") })),
    // banners, the maintenance issues, the status headline and the CSV export status line
    footer(colorKey(["data_green", "data_red", "data_amber", "purple_status", "gray_status"])),
  ]);
};

// ---------------------------------------------------------------- Settings Status (read-only; changed in Configure)
// The Current Settings text: one Jinja template over the configuration attributes (`c`), built from the parts below,
// one paragraph each, in this order: the ranges in use with the consensus ranges, the data, the sources, the sensor
// limits, the formats, the regional settings, the summaries, the two sets of colors, the display, the sidebar, the
// optional features, the weekly pages, the phones and the custom goals. Until the attributes are there it says that
// it is waiting.
const settingOn = () => gcT("strategy.settings.current.on");
const settingOff = () => gcT("strategy.settings.current.off");
const settingAuto = () => gcT("strategy.settings.current.automatic");
// "automatic (in use: <value>)" for a Jinja expression of the value in use
const autoInUse = (value) => jexpr(gcT("strategy.settings.current.automatic_in_use"), { value });
// the list separator (", ") as a Jinja string literal
const listSep = () => jstr(gcT("strategy.separator.list"));
// the ranges in use, the table of the six limits beside the consensus ranges, and its note
const settingsRangesPart = () =>
  gcT("strategy.settings.current.ranges_in_use", { ranges: jif("is_state('binary_sensor.glucocraft_using_custom_ranges', 'on')",
    gcT("strategy.settings.current.ranges_custom"), gcT("strategy.ranges.consensus_cited")) }) + "\n\n" +
  gcT("strategy.settings.current.table", { unit: "{{ c.unit }}",
    very_low: "{{ gv(r.very_low) }}", very_low_default: "{{ gv(a.range_very_low) }}",
    low: "{{ gv(r.low) }}", low_default: "{{ gv(a.range_low) }}",
    tight_low: "{{ gv(r.tight_low) }}", tight_high: "{{ gv(r.tight_high) }}",
    tight_low_default: "{{ gv(a.range_tight_low) }}", tight_high_default: "{{ gv(a.range_tight_high) }}",
    high: "{{ gv(r.high) }}", high_default: "{{ gv(a.range_high) }}",
    very_high: "{{ gv(r.very_high) }}", very_high_default: "{{ gv(a.range_very_high) }}" }) + "\n\n" +
  gcT("strategy.settings.current.default_note");
// the no-current-data limit, the unit, the days kept and what happens to the data when GlucoCraft is deleted
const settingsDataPart = () =>
  gcT("strategy.settings.current.data", { minutes: "{{ c.no_current_data_minutes | default(" + STALE_DEFAULT_MIN + ") }}", unit: "{{ c.unit }}",
    days: "{{ c.retention_days | default(" + RETENTION_DAYS + ", true) }}",
    // the setting When GlucoCraft is deleted (attribute on_removal: keep, the default, or delete)
    on_removal: jif("c.on_removal == 'delete'", gcT("strategy.settings.current.on_removal_delete"),
      gcT("strategy.settings.current.on_removal_keep")) });
// the sources with their measurement times, and the expiry entity
const settingsSourcesPart = () => {
  // a source's measurement time: recognized automatically (attribute time_attribute "auto"), or the attribute named
  // in the settings
  const timeOf = (src) => "{{ " + jstr(gcT("strategy.settings.current.time_auto")) + " if " + src + ".time_attribute == 'auto' else " +
    jexpr(gcT("strategy.settings.current.time_attribute"), { name: src + ".time_attribute" }) + " }}";
  const sourceP = { primary: "{{ c.primary_source.name }}", primary_time: timeOf("c.primary_source") };
  const sourceS = { secondary: "{{ c.secondary_source.name }}", secondary_time: timeOf("c.secondary_source") };
  return gcT("strategy.settings.current.sources", {
    sources: "{% if c.primary_source and c.secondary_source %}" +
      gcT("strategy.settings.current.sources_both", { ...sourceP, ...sourceS }) +
      "{% elif c.primary_source %}" + gcT("strategy.settings.current.sources_primary_only", sourceP) +
      "{% elif c.secondary_source %}" + gcT("strategy.settings.current.sources_second_only", { primary: "---", ...sourceS }) +
      "{% else %}" + gcT("strategy.settings.current.sources_neither", { primary: "---" }) + "{% endif %}",
    expiry_entity: "{{ c.expiry_entity or " + jstr(gcT("strategy.settings.current.none")) + " }}" });
};
// the sensor limits of each source: the setting (automatic or a value) and, when automatic, the limit in use and why
const settingsLimitsPart = (notes) =>
  "{% macro lim(s, k) -%}{%- set v = s[k] if s[k] is defined else none -%}" +
  "{%- set st = s[k ~ '_setting'] if s[k ~ '_setting'] is defined and s[k ~ '_setting'] is not none else 'auto' -%}" +
  "{%- set why = s[k ~ '_auto_reason'] if s[k ~ '_auto_reason'] is defined else none -%}" +
  "{%- if not (st | string | lower).startswith('auto') -%}{{ gv(st) ~ ' ' ~ c.unit }}" +
  "{%- elif v is none -%}{{ " + jstr(settingAuto()) + " }}" +
  "{%- elif why -%}{{ " + jexpr(gcT("strategy.settings.current.automatic_in_use_reason"),
    { value: "gv(v) ~ ' ' ~ c.unit", reason: "why" }) + " }}" +
  "{%- else -%}{{ " + autoInUse("gv(v) ~ ' ' ~ c.unit") + " }}{%- endif -%}" +
  "{%- endmacro -%}" +
  "{% set so = c.sources | default({}) %}" +
  "{% for key, nm in [['primary', " + jstr(gcT("strategy.settings.current.primary_source")) + "], ['secondary', " +
  jstr(gcT("strategy.settings.current.second_source")) + "]] if so[key] is defined and so[key] %}" +
  gcT("strategy.settings.current.limits", { source: "{{ nm }}", lower: "{{ lim(so[key], 'lower_limit') }}",
    upper: "{{ lim(so[key], 'upper_limit') }}" }) + "\n\n" +
  "{% endfor %}" +
  gcT("strategy.settings.current.limits_note", { limit_note: notes.limit_note });
// the time and date formats (the setting, or automatic with the format in use) and the display card
const settingsFormatsPart = () => {
  const automatic = settingAuto();
  // "12-hour" / "24-hour" (the setting) and the format in use (attribute time_format, "12" / "24")
  const hourNames = jdict({ 12: gcT("strategy.settings.current.hour_format", { hours: "12" }),
    24: gcT("strategy.settings.current.hour_format", { hours: "24" }) });
  const hourInUse = jexpr(gcT("strategy.settings.current.hour_format"), { hours: "c.time_format" });
  const dateNames = jdict({ MDY: gcT("strategy.settings.current.date_mdy"), DMY: gcT("strategy.settings.current.date_dmy"),
    YMD: gcT("strategy.settings.current.date_ymd") });
  const cardNames = jdict({ rectangular: gcT("strategy.settings.current.card_rectangular", { size: "800 × 480" }) });
  return "{% set dfn = " + dateNames + " %}" +
    gcT("strategy.settings.current.formats", {
      time_format: "{{ " + hourNames + ".get(c.time_format_setting, " + jstr(automatic) + ") " +
        "if c.time_format_setting in ['12', '24'] or not c.time_format else (" + autoInUse(hourInUse) + ") }}",
      date_format: "{{ dfn.get(c.date_format_setting, " + jstr(automatic) + ") if c.date_format_setting in ['MDY', 'DMY', 'YMD'] " +
        "or not c.date_format else (" + autoInUse("dfn.get(c.date_format, c.date_format)") + ") }}",
      display_card: "{{ " + cardNames + ".get(c.display_card, " +
        jstr(gcT("strategy.settings.current.card_square", { size: "480 × 480" })) + ") }}" });
};
// the regional settings: Automatic (from Home Assistant's country, language and unit system) or a choice; the setting
// (raw) and the value in use (resolved)
const settingsRegionalPart = () =>
  "{% macro rs(raw, val, names) -%}{%- set n = names.get(val | string, val) if val is not none else none -%}" +
  "{%- if (raw | default('auto', true) | string | lower).startswith('auto') -%}{{ (" + autoInUse("n") + ") if n is not none else " +
  jstr(settingAuto()) + " }}" +
  "{%- else -%}{{ n if n is not none else raw }}{%- endif -%}{%- endmacro -%}" +
  gcT("strategy.settings.current.regional", {
    first_weekday: "{{ rs(c.first_weekday_setting, c.first_weekday | default(none), " +
      jdict(Object.fromEntries(DAY_KEYS.map((key, i) => [i, gcT(key)]))) + ") }}",
    decimal_separator: "{{ rs(c.decimal_separator_setting, c.decimal_separator | default(none), " +
      jdict({ ".": gcT("strategy.settings.current.point"), ",": gcT("strategy.settings.current.comma") }) + ") }}",
    a1c_unit: "{{ rs(c.a1c_unit_setting, c.a1c_unit | default(none), " + jdict({ percent: "%", mmol_mol: "mmol/mol",
      both: gcT("strategy.settings.current.a1c_both", { percent: "%", mmol_mol: "mmol/mol" }) }) + ") }}",
    paper_size: "{{ rs(c.paper_size_setting, c.paper_size | default(none), " +
      jdict({ letter: gcT("strategy.settings.current.paper_letter"), a4: gcT("strategy.settings.current.paper_a4") }) + ") }}",
    // whether a report printer is set (attribute printer); the address itself is not published
    printer: jif("c.printer", gcT("strategy.settings.current.printer_set"), gcT("strategy.settings.current.none")) });
// the daily and weekly summaries and the no-data notice, each on (with when) or off
const settingsSummariesPart = () => {
  const off = settingOff();
  return "{% set days = " + jlist(DAY_KEYS.map((key) => gcT(key))) + " %}" +
    // the daily summary's time ("HH:MM" or "H:MM", seconds possible) in the time format in use: "8:00 AM" / "08:00"
    "{% set nt = (c.notifications.daily_time if c.notifications else '') | string %}" +
    "{% set nh = nt.split(':')[0] | int(0) %}{% set nm = ('0' ~ (nt.split(':')[1] if ':' in nt else '0'))[-2:] %}" +
    gcT("strategy.settings.current.summaries", {
      daily: "{{ (" + jexpr(gcT("strategy.settings.current.on_at"), { value: "(" + "(('0' ~ nh)[-2:] ~ ':' ~ nm) if c.time_format == '24' else (" +
        jexpr(gcT("card.time.clock_12.am"), { time: "((nh % 12) or 12) ~ ':' ~ nm" }) + " if nh in range(12) else " +
        jexpr(gcT("card.time.clock_12.pm"), { time: "((nh % 12) or 12) ~ ':' ~ nm" }) + "))" }) +
        ") if c.notifications and c.notifications.daily else " + jstr(off) + " }}",
      weekly: "{{ (" + jexpr(gcT("strategy.settings.current.on_at"), { value: "days[c.notifications.weekly_day | int(6)]" }) +
        ") if c.notifications and c.notifications.weekly else " + jstr(off) + " }}",
      // the no-data notice in Home Assistant and its delay (its phone message: the phone types below)
      notice: "{{ (" + jexpr(gcT("strategy.settings.current.on_at"), {
        value: jexpr(gcT("strategy.settings.current.notice_after"),
          { minutes: "c.notifications.no_data_notice_delay_minutes | default(30)" }) }) +
        ") if c.notifications and c.notifications.no_data_notice else " + jstr(off) + " }}" });
};
// the display colors in use (attribute zone_colors: the user's colors or the default palette), then the graph range
// colors in use (attribute band_colors: the user's colors or the CGM Report's time-in-ranges colors), by the same zone
// names; two paragraphs
const settingsColorsPart = () =>
  "{% set zc = c.zone_colors | default({}) %}{% set zn = " + jpairs(texts(ZONE_KEY)) + " %}" +
  gcT("strategy.settings.current.colors", { colors: "{% if zc %}{% for k, n in zn %}" +
    gcT("strategy.settings.current.zone_color", { zone: "{{ n }}", color: "{{ zc[k] }}" }) +
    "{{ ' · ' if not loop.last else '' }}{% endfor %}{% else %}" + gcT("strategy.settings.current.default_palette") +
    "{% endif %}" }) + "\n\n" +
  "{% set bc = c.band_colors | default({}) %}" +
  gcT("strategy.settings.current.band_colors", { colors: "{% if bc %}{% for k, n in zn %}" +
    gcT("strategy.settings.current.zone_color", { zone: "{{ n }}", color: "{{ bc[k] }}" }) +
    "{{ ' · ' if not loop.last else '' }}{% endfor %}{% else %}" + gcT("strategy.settings.current.default_band_colors") +
    "{% endif %}" });
// How the TREND column shows the trend (attribute trend_display, "arrow" / "word"), the two backgrounds ("theme" = the
// Home Assistant theme, or "#RRGGBB"), Outline text and Fade the words when there is no current reading; then the
// sidebar and the optional features; three paragraphs
const settingsDisplayPart = () => {
  const on = settingOn(), off = settingOff();
  return gcT("strategy.settings.current.display", {
    trend: jif("c.trend_display == 'word'", gcT("strategy.settings.current.trend_word"),
      gcT("strategy.settings.current.trend_arrow")),
    card_background: "{{ " + jstr(gcT("strategy.settings.current.background_theme")) +
      " if (c.card_background | default('theme', true)) == 'theme' else c.card_background }}",
    panel_background: "{{ c.panel_background | default('---', true) }}",
    outline: jif("c.text_outline", on, off),
    flash: jif("c.flash_no_current_data", on, off) }) + "\n\n" +
    gcT("strategy.settings.current.sidebar", { dashboard: jif("c.dashboard", on, off), panel_feed: jif("c.panel_feed", on, off) }) +
    "\n\n" +
    gcT("strategy.settings.current.features", { custom: jif("c.custom_ranges_and_goals", on, off),
      statistics: jif("c.separate_statistics", on, off) });
};
// the range set of the weekly pages (attribute weekly_pages, set in the template as `wp`), and whether they are offered
const settingsWeeklyPart = () => {
  const weeklyShown = "(" + jstr(gcT("strategy.settings.current.weekly_custom")) + " if wp.shown == 'custom' else " +
    jstr(gcT("strategy.settings.current.weekly_consensus")) + ")";
  return "{% set wp = c.weekly_pages | default({}) %}" +
    gcT("strategy.settings.current.weekly", { weekly: "{{ " + weeklyShown + " if wp.offered else (" +
      jexpr(gcT("strategy.settings.current.weekly_not_offered"), { value: weeklyShown }) + ") }}" });
};
// the phones that get GlucoCraft's messages, and which messages
const settingsPhonesPart = () =>
  "{% set ph = c.phone_notifications | default({}) %}" +
  "{% set tn = " + jdict({ summaries: gcT("strategy.settings.current.type_summaries"),
    no_data_notice: gcT("strategy.settings.current.type_no_data_notice") }) + " %}" +
  gcT("strategy.settings.current.phones", { recipients: "{% if ph.targets %}" +
    gcT("strategy.settings.current.phones_with_types", {
      // a phone's notify service "mobile_app_<phone>" by the phone's name
      phones: "{% for x in ph.targets %}{{ (" + jexpr(gcT("strategy.settings.current.phone"), { name: "x[11:]" }) +
        ") if x.startswith('mobile_app_') else x }}{{ " + listSep() + " if not loop.last else '' }}{% endfor %}",
      types: "{% for t in ph.types %}{{ tn[t] if t in tn else t }}{{ " + listSep() + " if not loop.last else '' }}{% endfor %}" }) +
    "{% else %}" + gcT("strategy.settings.current.no_phone") + "{% endif %}" });
// The custom goals, then the measures without a goal: only while the optional feature Custom ranges and goals is on
// (the goals are used on Custom Ranges, which exists only then)
const settingsGoalsPart = () => {
  // the custom goals, "time in range more than 70%; ..." (the goal value in the A1C / GMI unit for GMI)
  const goalList = "{% for k, v in (c.goals or {}).items() %}" + gcT("strategy.settings.current.goal", {
    name: "{{ gn[k] if k in gn else k }}",
    op: "{{ " + jdict({ ">=": gcT("strategy.settings.current.op_at_least"), ">": gcT("strategy.settings.current.op_more_than"),
      "<=": gcT("strategy.settings.current.op_at_most"), "<": gcT("strategy.settings.current.op_less_than") }) +
      "[v.op] if v.op in ['>=', '>', '<=', '<'] else v.op }}",
    value: "{{ a1(v.value) if k == 'gmi' else (dn(v.value) ~ '%') }}" }) +
    "{{ " + jstr(gcT("strategy.separator.long_list")) + " if not loop.last else '' }}{% endfor %}";
  const goalSource = jif("c.goals_custom", gcT("strategy.settings.current.goals_custom"), gcT("strategy.settings.current.goals_default"));
  return "{% if c.custom_ranges_and_goals %}{% set gn = " + jdict(texts(GOAL_KEY)) + " %}" +
    "{% set ng = namespace(n=[]) %}{% for k, l in gn.items() if k not in (c.goals or {}) %}{% set ng.n = ng.n + [l] %}{% endfor %}" +
    "{% if ng.n %}" + gcT("strategy.settings.current.goals_with_unset", { source: goalSource, goals: goalList,
      names: "{{ ng.n | join(" + listSep() + ") }}" }) +
    "{% else %}" + gcT("strategy.settings.current.goals", { source: goalSource, goals: goalList }) + "{% endif %}" +
    "{% endif %}";
};
// the whole Current Settings template: the attributes (c), the ranges (r) and the consensus ranges (a) with the
// macros of GJ, then the parts, an empty line between them
const settingsStatusText = (notes) =>
  "{%- set c = states." + CFG + ".attributes if states." + CFG + " is not none else {} -%}" +
  "{%- set r = c.ranges | default({}) -%}{%- set a = c.consensus_ranges | default({}) -%}" + GJ +
  "{% if not r.very_low is defined or not a.range_very_low is defined %}" + gcT("strategy.waiting") + "{% else %}" +
  [settingsRangesPart(), settingsDataPart(), settingsSourcesPart(), settingsLimitsPart(notes), settingsFormatsPart(),
    settingsRegionalPart(), settingsSummariesPart(), settingsColorsPart(), settingsDisplayPart(), settingsWeeklyPart(),
    settingsPhonesPart(), settingsGoalsPart()].join("\n\n") +
  "{% endif %}";
/** Return the Settings Status page: the button to the settings, the current settings and the requirements. */
const settingsPage = ({ sub, notes, docsAt }) => sub(gcT("strategy.page.settings"), "settings", "mdi:cog", [
  section([{ type: "custom:glucocraft-settings-button" }]),
  section([heading(gcT("strategy.settings.current.heading"), "mdi:cog-outline"),
    md(settingsStatusText(notes), [gcT("strategy.settings.current.table_name")])]),
  section([heading(gcT("strategy.settings.changing.heading"), "mdi:pencil-outline"), md(gcT("strategy.settings.changing.steps"))]),
  section([heading(gcT("strategy.settings.requirements.heading"), "mdi:check-decagram-outline"), md(dtx(
    gcT("strategy.settings.requirements.table", { ...notes, privacy_url: docsAt("privacy") })),
    [gcT("strategy.settings.requirements.heading")])]),
  how(gcT("strategy.settings.about")),
  footer(),
]);

/** Build GlucoCraft's dashboard (strategy `custom:glucocraft`): the Start page and its sub-pages. */
class GlucoCraftDashboardStrategy extends HTMLElement {
  /** Return the dashboard configuration (title and views) for the current Home Assistant state. */
  static async generate(config, hass) {
    await gcI18nLoad(hass);
    const base = "/" + (location.pathname.split("/")[1] || "glucocraft-home");
    const cfg = hass.states[CFG]?.attributes || {};
    applyRegional(cfg);
    DOCS_LINK = `${base}/documentation`;   // before the pages: their texts link to it
    const ctx = pageContext(base, cfg);
    // Custom Ranges exists only while the optional feature Custom ranges and goals is on (the configuration attribute
    // custom_ranges_and_goals): its entities exist only then. The page list is made once, when the dashboard loads;
    // while the configuration sensor is missing (GlucoCraft reloading) the page is built, and its start-page row
    // appears only while custom ranges or goals are set, decided live by the pages card.
    const customPage = cfg.custom_ranges_and_goals !== false;
    return { title: "GlucoCraft",
      views: [startPage(ctx), todayPage(ctx), ...(customPage ? [customRangesPage(ctx)] : []), consensusPage(ctx),
        reportPage(ctx), maintenancePage(ctx, hass), settingsPage(ctx), documentationPage(ctx)] };
  }
}

if (!customElements.get("ll-strategy-dashboard-glucocraft")) customElements.define("ll-strategy-dashboard-glucocraft", GlucoCraftDashboardStrategy);   // loaded twice (resource + panel): no error
if (!customElements.get("glucocraft-markdown")) customElements.define("glucocraft-markdown", GlucoCraftMarkdown);
console.info(`%c GLUCOCRAFT-STRATEGY %c v${GC_STRATEGY_VERSION} `, "background:#7E57C2;color:#fff", "background:#000;color:#fff");
