// License: AGPL-3.0-only (LICENSE).
/*
 * glucocraft-i18n - the one language module of GlucoCraft's cards and pages.
 *
 * Every text a user sees or hears on the cards lives in a language catalogue, never in the code:
 *   i18n/<language>/frontend.json       the dashboard pages and cards, the display cards, the Report page's own texts
 *   i18n/<language>/report.json         the CGM Report (the Report page and the PDF share it)
 *   i18n/<language>/documentation.json  the Documentation page
 * The catalogues are served next to this file (frontend.py: <VERSIONED_URL_BASE>/i18n/<language>/<area>.json), so a
 * language is added by adding its folder, without a change to the code.
 *
 * Use: each card file imports this module once (it defines globalThis.glucocraftI18n, one instance for every card)
 * and waits for load(hass, areas) before its first render; after that, t(key, params) is synchronous.
 *
 * Language: Home Assistant's frontend language (hass.locale.language, else hass.language). The catalogue files are
 * looked up as the exact code (pt-BR), the part before the first "-" (pt), and en; English is the fallback for every
 * key a language lacks. Developer override, never shown to users: localStorage "glucocraft_language" (for example
 * "pseudo", the generated test language), read once when the module loads (a change applies after a page reload).
 *
 * Catalogue format: a JSON object of flat dotted keys ("card.status.no_current_data"); nested objects are read as
 * dotted keys as well. A value is a string, or an array of strings joined without a separator (a long text, one
 * paragraph per item). A placeholder {name} is replaced by the parameter of that name, an own property of the
 * parameters (so "{constructor}" is never filled with an inherited function); a placeholder without a parameter stays
 * as written, so other braces (Jinja templates, {{ }}) are never touched. Values may hold the HTML markup of the text
 * (<b>, <i>, links); parameters are inserted as given, so the caller escapes what it passes.
 * Plurals: one key per CLDR plural category, <key>.one and <key>.other (.zero, .two, .few, .many where a language
 * needs them). t(key, { count }) picks the category with Intl.PluralRules for the language, and .other when the
 * language has no text for that category; { count, digits } for a number shown with that many decimals ("1.0" is not
 * singular in English). {count} is an ordinary placeholder.
 * Numbers, dates, times and units are not in the catalogues: the cards format them with the regional settings and pass
 * them as parameters.
 * A missing key shows the key itself (reported once on the console), so that a gap is visible, never silent.
 */
const GCI18N_VERSION = "0.0.1";

if (!globalThis.glucocraftI18n) {
  const FALLBACK = "en";
  const files = {};       // "language/area" -> Promise of the file's texts, one fetch per file
  const texts = {};       // "language/area" -> { key: value } once read (an empty object for a missing file)
  const areasOf = {};     // language -> Set of the areas whose files are loaded
  const rules = {};       // "language|decimals" -> Intl.PluralRules
  const warned = new Set();
  let table = {};         // the texts in use: English, overlaid by the base language, overlaid by the exact language

  // the developer override, read once (requested runs on every update of every card)
  let override = null;
  try { override = globalThis.localStorage?.getItem("glucocraft_language"); } catch { /* storage blocked */ }
  // the language a Home Assistant state asks for: the developer override, else the frontend's language (without a
  // state, e.g. for the card picker at load time: the language Home Assistant wrote on the page, <html lang>)
  const requested = (hass) => String(override || hass?.locale?.language || hass?.language
    || globalThis.document?.documentElement?.lang || FALLBACK);
  // the catalogue languages of a code, most general first: en, pt, pt-BR
  const chain = (code) => [...new Set([FALLBACK, code.split("-")[0], code])];

  // { a: { b: "x" } } -> { "a.b": "x" }; arrays are values (a text in parts)
  const flatten = (obj, prefix = "", out = {}) => {
    for (const [k, v] of Object.entries(obj || {})) {
      const key = prefix ? `${prefix}.${k}` : k;
      if (v && typeof v === "object" && !Array.isArray(v)) flatten(v, key, out);
      else out[key] = v;
    }
    return out;
  };

  // one catalogue file, fetched once; a missing file (another language without this area) is an empty catalogue
  const fetchFile = (code, area) => {
    const id = `${code}/${area}`;
    files[id] ||= fetch(new URL(`./i18n/${code}/${area}.json`, import.meta.url).href)
      .then((r) => (r.ok ? r.json() : {}))
      .catch((err) => {
        console.warn(`GlucoCraft: language file ${id}.json not read`, err);
        return {};
      })
      .then((data) => { texts[id] = flatten(data); });
    return files[id];
  };

  // rebuild the table for a language from every area loaded for it
  const build = (code) => {
    const areas = [...(areasOf[code] || [])];
    const next = {};
    for (const lang of chain(code)) for (const area of areas) Object.assign(next, texts[`${lang}/${area}`] || {});
    table = next;
  };
  // the text of a key in a table with its placeholders filled; with a numeric params.count, the plural form
  const lookup = (tbl, key, params) => {
    let v = tbl[key];
    if (v === undefined && typeof params?.count === "number") {
      v = tbl[`${key}.${api.plural(params.count, params.digits)}`] ?? tbl[`${key}.other`];
    }
    if (v === undefined) {
      if (!warned.has(key)) { warned.add(key); console.warn(`GlucoCraft: no text for "${key}"`); }
      return key;
    }
    if (Array.isArray(v)) v = v.join("");
    return params ? String(v).replace(/\{(\w+)\}/g, (m, name) => (Object.hasOwn(params, name) ? String(params[name]) : m))
      : String(v);
  };

  const api = {
    version: GCI18N_VERSION,
    /** The language in use (a Home Assistant language code; "en" until a catalogue is loaded). */
    language: FALLBACK,
    /** The language for an HTML lang attribute: the language in use where it is a valid language tag, else "en". */
    get htmlLang() {
      try { return Intl.getCanonicalLocales(this.language)[0] || FALLBACK; } catch { return FALLBACK; }
    },
    /** True when the catalogues of these areas are loaded for the language this Home Assistant state asks for. */
    ready(hass, areas = ["frontend"]) {
      const code = requested(hass);
      return code === api.language && areas.every((a) => areasOf[code]?.has(a));
    },
    /** Load the catalogues of these areas for the language of this Home Assistant state, with the English fallback. */
    async load(hass, areas = ["frontend"]) {
      const code = requested(hass);
      await Promise.all(chain(code).flatMap((lang) => areas.map((area) => fetchFile(lang, area))));
      (areasOf[code] ||= new Set());
      areas.forEach((a) => areasOf[code].add(a));
      api.language = code;
      build(code);
    },
    /** Register a catalogue without fetching it (tests, and a page that bundles its texts). */
    add(code, area, entries) {
      texts[`${code}/${area}`] = flatten(entries);
      files[`${code}/${area}`] = Promise.resolve();
      (areasOf[code] ||= new Set()).add(area);
      api.language = code;
      build(code);
    },
    /** The text of a key with its placeholders filled; with a numeric params.count, the plural form for that count. */
    t(key, params) { return lookup(table, key, params); },
    /** The CLDR plural category of a number (shown with `digits` decimals, if given) in the language in use. */
    plural(n, digits) {
      const id = `${api.language}|${digits ?? ""}`;
      const opt = digits === undefined ? {} : { minimumFractionDigits: digits, maximumFractionDigits: digits };
      if (!rules[id]) {
        try { rules[id] = new Intl.PluralRules(api.language, opt); } catch { rules[id] = new Intl.PluralRules(FALLBACK, opt); }
      }
      return rules[id].select(n);
    },
  };
  globalThis.glucocraftI18n = api;
  // its version in the browser console, as each card file reports its own (once: a second load keeps the first module)
  console.info(`%c GLUCOCRAFT-I18N %c v${GCI18N_VERSION} `, "background:#7E57C2;color:#fff", "background:#000;color:#fff");
}
