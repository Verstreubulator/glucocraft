// License: AGPL-3.0-only (LICENSE).
/*
 * glucocraft-docs - the Documentation page of GlucoCraft's dashboard: what GlucoCraft is and is not, how it works,
 * where every target and research finding comes from (with links to the publications).
 *
 *   custom:glucocraft-documentation   the whole document, one collapsible section per topic
 *
 * The text is the language's document, i18n/<language>/documentation.json (glucocraft-i18n.js loads it, English as
 * the fallback for each text); this file holds the order of the sections, the references (APA 7, not translated), the
 * live settings and the rendering.
 *
 * Written for anyone: GlucoCraft only knows the entities it was given, never which CGM, cloud or integration is behind
 * them. Third-party names appear only where necessary: CGM systems only in Readings Outside the Sensor's
 * Range and in citations, the panel boards in the panel setup. Live values (thresholds, sources, history kept) come
 * from sensor.glucocraft_configuration.
 */
const GCDOCS_VERSION = "0.0.1";
// The language catalogues (glucocraft-i18n.js, served next to this file): every text comes from them, in Home
// Assistant's language with English as the fallback. The card waits for gcI18nLoad before its first render.
const GC_I18N_AREAS = ["frontend", "documentation"];
const GC_I18N = globalThis.glucocraftI18n ? Promise.resolve() : import(new URL("./glucocraft-i18n.js", import.meta.url).href);
const gcI18nLoad = (hass) => GC_I18N.then(() => globalThis.glucocraftI18n.load(hass, GC_I18N_AREAS));
const gcI18nReady = (hass) => !!globalThis.glucocraftI18n?.ready(hass, GC_I18N_AREAS);
const gcT = (key, params) => globalThis.glucocraftI18n.t(key, params);
// The rules the cards and pages share (glucocraft-shared.js, served next to this file and imported the same way): the
// rounding, the glucose units, the HTML escape, the gap threshold and the default colors. The file waits for it before
// it defines its page.
const GC_SHARED = globalThis.glucocraftShared ? Promise.resolve() : import(new URL("./glucocraft-shared.js", import.meta.url).href);
await GC_SHARED;
// STALE_DEFAULT_MIN, RETENTION_DAYS: the no-current-data limit (minutes) and the days of history kept, shown while the
// configuration sensor has no attributes (GlucoCraft reloading); CONS_RANGES: the consensus ranges (mg/dL)
const { round, MGDL_PER_MMOLL, CONS_MMOLL, esc, ZONE_DEFAULT, BAND_DEFAULT, gcPalette, applyRegional, dtx,
  gcSpeakText, css: GC_CSS, gcGapThreshold, CONS_RANGES, STALE_DEFAULT_MIN, RETENTION_DAYS, gcFocusKey,
  gcRefocus } = globalThis.glucocraftShared;

// ------------------------------------------------------------------ references (APA 7th edition)
// id: [in-text label (APA author-date, without parentheses), reference entry (APA 7, HTML), DOI, PMID, other link].
// Authors, titles, volumes, issues, pages and DOIs follow the PubMed record of each article (its PMID); APA 7
// lists up to 20 authors, and for 21 or more the first 19, an ellipsis and the last author. Year = the year of the
// issue. Hyphenated given names keep the hyphen (J.-O., H.-M., Y.-C.).
// Documents and web pages: the identifier in parentheses after the title, the description in brackets, no page
// numbers in the entry (locators go in the in-text citation, e.g. Abbott Diabetes Care, 2022, p. 11); a retrieval
// date only for pages that change ("Retrieved Month Day, Year, from" + the URL the list appends).
// The PDF report has its own copy of the entries it cites (report_pdf.REFERENCES; tests/test_references.py).
const REFS = {
  battelino2019: ["Battelino et al., 2019",
    "Battelino, T., Danne, T., Bergenstal, R. M., Amiel, S. A., Beck, R., Biester, T., Bosi, E., Buckingham, B. A., " +
    "Cefalu, W. T., Close, K. L., Cobelli, C., Dassau, E., DeVries, J. H., Donaghue, K. C., Dovc, K., Doyle, F. J., III, " +
    "Garg, S., Grunberger, G., Heller, S., . . . Phillip, M. (2019). Clinical targets for continuous glucose monitoring " +
    "data interpretation: Recommendations from the International Consensus on Time in Range. <i>Diabetes Care, 42</i>(8), " +
    "1593–1603.", "10.2337/dci19-0028", "31177185"],
  battelino2023: ["Battelino et al., 2023",
    "Battelino, T., Alexander, C. M., Amiel, S. A., Arreaza-Rubin, G., Beck, R. W., Bergenstal, R. M., Buckingham, B. A., " +
    "Carroll, J., Ceriello, A., Chow, E., Choudhary, P., Close, K., Danne, T., Dutta, S., Gabbay, R., Garg, S., Heverly, " +
    "J., Hirsch, I. B., Kader, T., . . . Phillip, M. (2023). Continuous glucose monitoring and metrics for clinical " +
    "trials: An international consensus statement. <i>The Lancet Diabetes &amp; Endocrinology, 11</i>(1), 42–57.",
    "10.1016/S2213-8587(22)00319-9", "36493795"],
  monnier2017: ["Monnier et al., 2017",
    "Monnier, L., Colette, C., Wojtusciszyn, A., Dejager, S., Renard, E., Molinari, N., &amp; Owens, D. R. (2017). " +
    "Toward defining the threshold between low and high glucose variability in diabetes. <i>Diabetes Care, 40</i>(7), " +
    "832–838.", "10.2337/dc16-1769", "28039172"],
  bergenstal2018: ["Bergenstal et al., 2018",
    "Bergenstal, R. M., Beck, R. W., Close, K. L., Grunberger, G., Sacks, D. B., Kowalski, A., Brown, A. S., Heinemann, " +
    "L., Aleppo, G., Ryan, D. B., Riddlesworth, T. D., &amp; Cefalu, W. T. (2018). Glucose management indicator (GMI): A " +
    "new term for estimating A1C from continuous glucose monitoring. <i>Diabetes Care, 41</i>(11), 2275–2280.",
    "10.2337/dc18-1581", "30224348"],
  klonoff2023: ["Klonoff et al., 2023",
    "Klonoff, D. C., Wang, J., Rodbard, D., Kohn, M. A., Li, C., Liepmann, D., Kerr, D., Ahn, D., Peters, A. L., " +
    "Umpierrez, G. E., Seley, J. J., Xu, N. Y., Nguyen, K. T., Simonson, G., Agus, M. S. D., Al-Sofiani, M. E., " +
    "Armaiz-Pena, G., Bailey, T. S., Basu, A., . . . Kovatchev, B. (2023). A Glycemia Risk Index (GRI) of hypoglycemia " +
    "and hyperglycemia for continuous glucose monitoring validated by clinician ratings. <i>Journal of Diabetes Science " +
    "and Technology, 17</i>(5), 1226–1242.",
    "10.1177/19322968221085273", "35348391"],
  // two 2025 sections of the same group author: the letters a and b (ordered by title, the section numbers in
  // numerical order); Section 7 of 2025 is listed in What GlucoCraft Leaves Out
  ada2025: ["American Diabetes Association Professional Practice Committee, 2025a",
    "American Diabetes Association Professional Practice Committee. (2025a). 6. Glycemic goals and hypoglycemia: " +
    "Standards of Care in Diabetes—2025. <i>Diabetes Care, 48</i>(Suppl. 1), S128–S145.", "10.2337/dc25-S006", "39651981"],
  ada2025b: ["American Diabetes Association Professional Practice Committee, 2025b",
    "American Diabetes Association Professional Practice Committee. (2025b). 7. Diabetes technology: Standards of " +
    "Care in Diabetes—2025. <i>Diabetes Care, 48</i>(Suppl. 1), S146–S166.", "10.2337/dc25-S007", "39651978"],
  // Section 7 is cited as well (the CSV export, Maintenance), so the 2026 sections of the same group author carry
  // letters (APA 7, ordered by title, the section numbers in numerical order: "6. Glycemic ..." (a), "7. Diabetes
  // ..." (b), and Sections 13, 14 and 15 (c, d, e), listed in What GlucoCraft Leaves Out)
  ada2026: ["American Diabetes Association Professional Practice Committee for Diabetes, 2026a",
    "American Diabetes Association Professional Practice Committee for Diabetes. (2026a). 6. Glycemic goals, " +
    "hypoglycemia, and hyperglycemic crises: Standards of Care in Diabetes—2026. <i>Diabetes Care, 49</i>(Suppl. 1), " +
    "S132–S149.", "10.2337/dc26-S006", "41358894"],
  ada2026b: ["American Diabetes Association Professional Practice Committee for Diabetes, 2026b",
    "American Diabetes Association Professional Practice Committee for Diabetes. (2026b). 7. Diabetes technology: " +
    "Standards of Care in Diabetes—2026. <i>Diabetes Care, 49</i>(Suppl. 1), S150–S165.", "10.2337/dc26-S007", "41358889"],
  ada2026c: ["American Diabetes Association Professional Practice Committee for Diabetes, 2026c",
    "American Diabetes Association Professional Practice Committee for Diabetes. (2026c). 13. Older adults: Standards " +
    "of Care in Diabetes—2026. <i>Diabetes Care, 49</i>(Suppl. 1), S277–S296.", "10.2337/dc26-S013", "41358888"],
  ada2026d: ["American Diabetes Association Professional Practice Committee for Diabetes, 2026d",
    "American Diabetes Association Professional Practice Committee for Diabetes. (2026d). 14. Children and " +
    "adolescents: Standards of Care in Diabetes—2026. <i>Diabetes Care, 49</i>(Suppl. 1), S297–S320.",
    "10.2337/dc26-S014", "41358890"],
  ada2026e: ["American Diabetes Association Professional Practice Committee for Diabetes, 2026e",
    "American Diabetes Association Professional Practice Committee for Diabetes. (2026e). 15. Management of diabetes " +
    "in pregnancy: Standards of Care in Diabetes—2026. <i>Diabetes Care, 49</i>(Suppl. 1), S321–S338.",
    "10.2337/dc26-S015", "41358885"],
  beck2019: ["Beck et al., 2019",
    "Beck, R. W., Bergenstal, R. M., Riddlesworth, T. D., Kollman, C., Li, Z., Brown, A. S., &amp; Close, K. L. (2019). " +
    "Validation of time in range as an outcome measure for diabetes clinical trials. <i>Diabetes Care, 42</i>(3), " +
    "400–405.", "10.2337/dc18-1444", "30352896"],
  bergenstal2013: ["Bergenstal et al., 2013",
    "Bergenstal, R. M., Ahmann, A. J., Bailey, T., Beck, R. W., Bissen, J., Buckingham, B., Deeb, L., Dolin, R. H., " +
    "Garg, S. K., Goland, R., Hirsch, I. B., Klonoff, D. C., Kruger, D. F., Matfin, G., Mazze, R. S., Olson, B. A., " +
    "Parkin, C., Peters, A., Powers, M. A., . . . Wesley, D. M. (2013). Recommendations for standardizing glucose " +
    "reporting and analysis to optimize clinical decision making in diabetes: The Ambulatory Glucose Profile (AGP). " +
    "<i>Diabetes Technology &amp; Therapeutics, 15</i>(3), 198–211.", "10.1089/dia.2013.0051", "23448694"],
  cichosz2023: ["Cichosz et al., 2023",
    "Cichosz, S. L., Jensen, M. H., &amp; Hejlesen, O. (2023). Optimal data collection period for continuous glucose " +
    "monitoring to assess long-term glycemic control: Revisited. <i>Journal of Diabetes Science and Technology, " +
    "17</i>(3), 690–695.", "10.1177/19322968211069177", "34986667"],
  riddlesworth2018: ["Riddlesworth et al., 2018",
    "Riddlesworth, T. D., Beck, R. W., Gal, R. L., Connor, C. G., Bergenstal, R. M., Lee, S., &amp; Willi, S. M. (2018). " +
    "Optimal sampling duration for continuous glucose monitoring to determine long-term glycemic control. " +
    "<i>Diabetes Technology &amp; Therapeutics, 20</i>(4), 314–316.", "10.1089/dia.2017.0455", "29565197"],
  danne2017: ["Danne et al., 2017",
    "Danne, T., Nimri, R., Battelino, T., Bergenstal, R. M., Close, K. L., DeVries, J. H., Garg, S., Heinemann, L., " +
    "Hirsch, I., Amiel, S. A., Beck, R., Bosi, E., Buckingham, B., Cobelli, C., Dassau, E., Doyle, F. J., III, Heller, " +
    "S., Hovorka, R., Jia, W., . . . Phillip, M. (2017). International consensus on use of continuous glucose " +
    "monitoring. <i>Diabetes Care, 40</i>(12), 1631–1640.", "10.2337/dc17-1600", "29162583"],
  gorst2015: ["Gorst et al., 2015",
    "Gorst, C., Kwok, C. S., Aslam, S., Buchan, I., Kontopantelis, E., Myint, P. K., Heatlie, G., Loke, Y., Rutter, " +
    "M. K., &amp; Mamas, M. A. (2015). Long-term glycemic variability and risk of adverse outcomes: A systematic review " +
    "and meta-analysis. <i>Diabetes Care, 38</i>(12), 2354–2369.", "10.2337/dc15-1188", "26604281"],
  holman2008: ["Holman et al., 2008",
    "Holman, R. R., Paul, S. K., Bethel, M. A., Matthews, D. R., &amp; Neil, H. A. W. (2008). 10-year follow-up of " +
    "intensive glucose control in type 2 diabetes. <i>The New England Journal of Medicine, 359</i>(15), 1577–1589.",
    "10.1056/NEJMoa0806470", "18784090"],
  ihsg2017: ["International Hypoglycaemia Study Group, 2017",
    "International Hypoglycaemia Study Group. (2017). Glucose concentrations of less than 3.0 mmol/L (54 mg/dL) should " +
    "be reported in clinical trials: A joint position statement of the American Diabetes Association and the European " +
    "Association for the Study of Diabetes. <i>Diabetes Care, 40</i>(1), 155–157.", "10.2337/dc16-2215", "27872155"],
  laiteerapong2017: ["Laiteerapong et al., 2017",
    "Laiteerapong, N., Karter, A. J., Moffet, H. H., Cooper, J. M., Gibbons, R. D., Liu, J. Y., Gao, Y., &amp; Huang, " +
    "E. S. (2017). Ten-year hemoglobin A1c trajectories and outcomes in type 2 diabetes mellitus: The Diabetes &amp; " +
    "Aging Study. <i>Journal of Diabetes and Its Complications, 31</i>(1), 94–100.",
    "10.1016/j.jdiacomp.2016.07.023", "27503405"],
  laiteerapong2019: ["Laiteerapong et al., 2019",
    "Laiteerapong, N., Ham, S. A., Gao, Y., Moffet, H. H., Liu, J. Y., Huang, E. S., &amp; Karter, A. J. (2019). The " +
    "legacy effect in type 2 diabetes: Impact of early glycemic control on future complications (The Diabetes &amp; " +
    "Aging Study). <i>Diabetes Care, 42</i>(3), 416–426.", "10.2337/dc17-1144", "30104301"],
  li2025: ["Li et al., 2025",
    "Li, Y.-C., Mo, K.-E., Zhang, L.-S., Zhao, Q., Deng, J., &amp; Li, L. (2025). Long-term glycemic variability and the " +
    "risk of heart failure: A meta-analysis. <i>PeerJ, 13</i>, Article e20401.", "10.7717/peerj.20401", "41328074"],
  lu2018: ["Lu et al., 2018",
    "Lu, J., Ma, X., Zhou, J., Zhang, L., Mo, Y., Ying, L., Lu, W., Zhu, W., Bao, Y., Vigersky, R. A., &amp; Jia, W. " +
    "(2018). Association of time in range, as assessed by continuous glucose monitoring, with diabetic retinopathy in " +
    "type 2 diabetes. <i>Diabetes Care, 41</i>(11), 2370–2376.", "10.2337/dc18-1131", "30201847"],
  lu2021: ["Lu et al., 2021",
    "Lu, J., Wang, C., Shen, Y., Chen, L., Zhang, L., Cai, J., Lu, W., Zhu, W., Hu, G., Xia, T., &amp; Zhou, J. (2021). " +
    "Time in range in relation to all-cause and cardiovascular mortality in patients with type 2 diabetes: A " +
    "prospective cohort study. <i>Diabetes Care, 44</i>(2), 549–555.", "10.2337/dc20-1862", "33097560"],
  nathan2014: ["Nathan &amp; DCCT/EDIC Research Group, 2014",
    "Nathan, D. M., &amp; DCCT/EDIC Research Group. (2014). The Diabetes Control and Complications Trial/Epidemiology " +
    "of Diabetes Interventions and Complications study at 30 years: Overview. <i>Diabetes Care, 37</i>(1), 9–16.",
    "10.2337/dc13-2112", "24356592"],
  riddle2021: ["Riddle et al., 2021",
    "Riddle, M. C., Cefalu, W. T., Evans, P. H., Gerstein, H. C., Nauck, M. A., Oh, W. K., Rothberg, A. E., le Roux, " +
    "C. W., Rubino, F., Schauer, P., Taylor, R., &amp; Twenefour, D. (2021). Consensus report: Definition and " +
    "interpretation of remission in type 2 diabetes. <i>Diabetes Care, 44</i>(10), 2438–2444.",
    "10.2337/dci21-0034", "34462270"],
  shah2024: ["Shah et al., 2024",
    "Shah, V. N., Kanapka, L. G., Akturk, H. K., Polsky, S., Forlenza, G. P., Kollman, C., Beck, R. W., &amp; " +
    "Snell-Bergeon, J. K. (2024). Time in range is associated with incident diabetic retinopathy in adults with type 1 " +
    "diabetes: A longitudinal study. <i>Diabetes Technology &amp; Therapeutics, 26</i>(4), 246–251.",
    "10.1089/dia.2023.0486", "38133643"],
  // readings outside the measurement range: low and high messages replaced with 39 and 401 mg/dL (Methods)
  spartano2025: ["Spartano et al., 2025",
    "Spartano, N. L., Sultana, N., Lin, H., Cheng, H., Lu, S., Fei, D., Murabito, J. M., Walker, M. E., Wolpert, H. A., &amp; " +
    "Steenkamp, D. W. (2025). Defining continuous glucose monitor time in range in a large, community-based cohort without " +
    "diabetes. <i>The Journal of Clinical Endocrinology &amp; Metabolism, 110</i>(4), 1128–1134.",
    "10.1210/clinem/dgae626",
    "39257191"],
  stratton2000: ["Stratton et al., 2000",
    "Stratton, I. M., Adler, A. I., Neil, H. A. W., Matthews, D. R., Manley, S. E., Cull, C. A., Hadden, D., Turner, R. C., " +
    "&amp; Holman, R. R. (2000). Association of glycaemia with macrovascular and microvascular complications of type 2 " +
    "diabetes (UKPDS 35): Prospective observational study. <i>BMJ, 321</i>(7258), 405–412.",
    "10.1136/bmj.321.7258.405", "10938048"],
  // research consulted and left out (What GlucoCraft Leaves Out, and Why); the supplement pages as PubMed gives them
  johnson2019: ["Johnson et al., 2019",
    "Johnson, M. L., Martens, T. W., Criego, A. B., Carlson, A. L., Simonson, G. D., &amp; Bergenstal, R. M. (2019). " +
    "Utilizing the ambulatory glucose profile to standardize and implement continuous glucose monitoring in clinical " +
    "practice. <i>Diabetes Technology &amp; Therapeutics, 21</i>(S2), S217–S225.", "10.1089/dia.2019.0034", "31169432"],
  sundberg2022: ["Sundberg et al., 2022",
    "Sundberg, F., deBeaufort, C., Krogvold, L., Patton, S., Piloya, T., Smart, C., Van Name, M., Weissberg-Benchell, " +
    "J., Silva, J., &amp; diMeglio, L. A. (2022). ISPAD Clinical Practice Consensus Guidelines 2022: Managing diabetes " +
    "in preschoolers. <i>Pediatric Diabetes, 23</i>(8), 1496–1511.", "10.1111/pedi.13427", "36537520"],
  sundberg2025: ["Sundberg et al., 2025",
    "Sundberg, F., Smart, C. E., Samuelsson, J., Åkesson, K., &amp; Krogvold, L. (2025). Using time in tight glucose " +
    "range as a health-promoting strategy in preschoolers with type 1 diabetes. <i>Diabetes Care, 48</i>(1), 6–14.",
    "10.2337/dci24-0058", "39546551"],
  tauschmann2024: ["Tauschmann et al., 2024",
    "Tauschmann, M., Cardona-Hernandez, R., DeSalvo, D. J., Hood, K., Laptev, D. N., Lindholm Olinder, A., Wheeler, " +
    "B. J., &amp; Smart, C. E. (2024). International Society for Pediatric and Adolescent Diabetes Clinical Practice " +
    "Consensus Guidelines 2024 diabetes technologies: Glucose monitoring. <i>Hormone Research in Paediatrics, " +
    "97</i>(6), 615–635.", "10.1159/000543156", "39884260"],
  vigersky2019: ["Vigersky &amp; McMahon, 2019",
    "Vigersky, R. A., &amp; McMahon, C. (2019). The relationship of hemoglobin A1C to time-in-range in patients with " +
    "diabetes. <i>Diabetes Technology &amp; Therapeutics, 21</i>(2), 81–85.", "10.1089/dia.2018.0310", "30575414"],
  // the codes of out-of-range readings in trial data of Dexcom CGM systems (39 and 401 mg/dL; Readings Outside the
  // Sensor's Range)
  mulley2026: ["Mulley, 2026",
    "Mulley, J. F. (2026). Prevalence, duration, and clinical implications of continuous glucose monitor measurement " +
    "limit capping in type 1 diabetes. <i>Diabetic Medicine, 43</i>(10), Article e70412.", "10.1111/dme.70412",
    "42394198"],
  abbott: ["Abbott Diabetes Care, 2022",
    "Abbott Diabetes Care. (2022). <i>Get started with the FreeStyle Libre 3 system</i> (ADC-55381 v1.0) [Getting " +
    "started guide, US, mg/dL].", "", "",
    "https://www.freestyleprovider.abbott/content/dam/adc/freestyleprovider/countries/us-en/documents/Brochure_FreeStyleLibre3_Getting_Started_Guide.pdf"],
  // the AGP report's range colors (very low dark red, low red, in range green, high yellow, very high orange) are cited
  // to the guide idc2024 below, whose time-in-ranges figure shows them
  hoelzel2004: ["Hoelzel et al., 2004",
    "Hoelzel, W., Weykamp, C., Jeppsson, J.-O., Miedema, K., Barr, J. R., Goodall, I., Hoshino, T., John, W. G., " +
    "Kobold, U., Little, R., Mosca, A., Mauri, P., Paroni, R., Susanto, F., Takei, I., Thienpont, L., Umemoto, M., " +
    "Wiedmeyer, H.-M., &amp; IFCC Working Group on HbA1c Standardization. (2004). IFCC reference system for measurement " +
    "of hemoglobin A1c in human blood and the national standardization schemes in the United States, Japan, and Sweden: " +
    "A method-comparison study. <i>Clinical Chemistry, 50</i>(1), 166–174.", "10.1373/clinchem.2003.024802", "14709644"],
  // measurement ranges, out-of-range readings, no-current-data limits and the CGM systems that work only partly:
  // manufacturer documents and FAQ pages as group-author works (APA 7: "n.d." and the letters a, b, c for works
  // without a date by one author, ordered by title; works without a date precede dated works). The web pages carry
  // their retrieval date in the entry; a document with a fixed address (a PDF) carries none.
  // the FreeStyle Libre software manual: 2018, the date the document prints ("Copyright © 2018 Abbott",
  // "ART39660-001 Rev. A 06/18"); the two undated works of Abbott Diabetes Care are ordered by title
  abbottSoftware: ["Abbott Diabetes Care, 2018",
    "Abbott Diabetes Care. (2018). <i>FreeStyle Libre software user's manual</i> (Software version 1.0; " +
    "ART39660-001 rev. A).", "", "", "https://freestyleserver.com/Payloads/IFU/2018/ART39660-001_rev-A-Web.pdf"],
  abbottLlu: ["Abbott Diabetes Care, n.d.-a",
    "Abbott Diabetes Care. (n.d.-a). <i>Glucose readings</i> [LibreLinkUp article].", "", "",
    "https://librelinkup.com/articles/glucose-readings/"],
  abbottHiLo: ["Abbott Diabetes Care, n.d.-b",
    "Abbott Diabetes Care. (n.d.-b). <i>Understanding HI and LO glucose results on FreeStyle Libre 3</i> [FAQ page]. " +
    "Retrieved November 9, 2026, from", "", "",
    "https://www.freestyle.abbott/en-us/support/faq/system/understanding-hi-and-lo-glucose-results-on-freestyle-libre-3.html"],
  // the FreeStyle Libre 14 day sensor stores a reading every 15 minutes (p. 6; "©2021 Abbott. ADC-10386 v3.0 07/21")
  abbott2021: ["Abbott Diabetes Care, 2021",
    "Abbott Diabetes Care. (2021). <i>Your FreeStyle Libre 14 day system</i> (ADC-10386 v3.0) [In-service guide, US].",
    "", "",
    "https://www.freestyleprovider.abbott/content/dam/adc/freestyleprovider/countries/us-en/4-2-page/documents/In-Service,%20FreeStyle%20Libre%2014%20Day%20System%20with%20App,%20HCP%20Sales%20New.pdf"],
  abbott2025: ["Abbott Diabetes Care, 2025",
    "Abbott Diabetes Care. (2025). <i>FreeStyle Libre 3 app user's manual: For use with the FreeStyle Libre 3 sensor and " +
    "FreeStyle Libre 3 Plus sensor</i> (ART48742-003 rev. B) [User's manual, US].", "", "",
    "https://freestyleserver.com/Payloads/IFU/2025/q4/ART48742-003_rev-B-pub.pdf"],
  // Dexcom: the three FAQ pages without a date (ordered by title), then the user guide, dated by its back page
  // ("AW00046-05 Rev 006 ... Rev Date 2024/06"; the measurement range on p. 37). The Stelo pages are published by
  // Dexcom, Inc.
  dexcomStelo: ["Dexcom, Inc., n.d.-a",
    "Dexcom, Inc. (n.d.-a). <i>How do I share my Stelo glucose data with health apps?</i> [FAQ]. Retrieved November 6, " +
    "2026, from", "", "",
    "https://www.stelo.com/en-us/faqs/using-stelo/how-do-i-share-my-glucose-data-with-health-apps"],
  dexcomSharing: ["Dexcom, Inc., n.d.-b",
    "Dexcom, Inc. (n.d.-b). <i>Sharing Dexcom CGM data: Clinics, apps, and followers</i> [FAQ]. Retrieved November 6, " +
    "2026, from", "", "", "https://www.dexcom.com/en-gb/faqs/data-sharing"],
  dexcomFaq: ["Dexcom, Inc., n.d.-c",
    "Dexcom, Inc. (n.d.-c). <i>Why am I getting Signal Loss when using Dexcom G7?</i> [FAQ]. Retrieved October 22, 2026, from",
    "", "", "https://www.dexcom.com/en-us/faqs/why-am-i-getting-signal-loss"],
  dexcomG7: ["Dexcom, Inc., 2024",
    "Dexcom, Inc. (2024). <i>Dexcom G7 user guide</i> (AW00046-05 Rev 006) [Instructions for use, outside the US, " +
    "mmol/L].", "", "", "https://s3.us-west-2.amazonaws.com/dexcompdf/G7/AW00046-05_UG_G7_OUS_en_MMOL.pdf"],
  // the two Medtronic user guides, dated by their last pages ("©2020 Medtronic. M995836A012_A"; "© 2024 Medtronic",
  // "M052531C001_3", 2024-09-27)
  medtronicGc: ["Medtronic, 2020",
    "Medtronic. (2020). <i>Guardian Connect system user guide</i> (M995836A012).", "", "",
    "https://www.medtronicdiabetes.com/sites/default/files/library/download-library/user-guides/Guardian-Connect-System-User-Guide.pdf"],
  medtronicSimplera: ["Medtronic, 2024",
    "Medtronic. (2024). <i>Simplera system user guide</i> (M052531C001).", "", "",
    "https://www.medtronicdiabetes.com/sites/default/files/library/download-library/user-guides/Simplera-system-user-guide.pdf"],
  // no address of the fact sheet published by Medtrum Technologies was found (2026-11-06)
  medtrum: ["Medtrum Technologies, n.d.",
    "Medtrum Technologies. (n.d.). <i>TouchCare Nano CGM</i> [Product fact sheet].", "", ""],
  // the code of Nightscout (the error codes, the values below 39 that are not readings, LOW at 39; the settings file),
  // and xDrip+, whose repository the Nightscout Foundation publishes: three works without a date by one author,
  // ordered by title and then by the description in brackets
  nightscoutCode: ["Nightscout Foundation, n.d.-a",
    "Nightscout Foundation. (n.d.-a). <i>cgm-remote-monitor</i> [Computer software; files lib/plugins/errorcodes.js, " +
    "lib/plugins/bgnow.js and lib/client/index.js]. GitHub. Retrieved November 6, 2026, from", "", "",
    "https://github.com/nightscout/cgm-remote-monitor/tree/master/lib"],
  nightscout: ["Nightscout Foundation, n.d.-b",
    "Nightscout Foundation. (n.d.-b). <i>cgm-remote-monitor</i> [Computer software; settings file lib/settings.js, " +
    "alarmTimeagoWarnMins]. GitHub. Retrieved October 22, 2026, from", "", "",
    "https://github.com/nightscout/cgm-remote-monitor/blob/master/lib/settings.js"],
  xdrip: ["Nightscout Foundation, n.d.-c",
    "Nightscout Foundation. (n.d.-c). <i>xDrip</i> [Computer software; file " +
    "app/src/main/java/com/eveningoutpost/dexdrip/models/BgReading.java, BG_READING_ERROR_VALUE]. GitHub. Retrieved " +
    "November 6, 2026, from", "", "",
    "https://github.com/NightscoutFoundation/xDrip/blob/master/app/src/main/java/com/eveningoutpost/dexdrip/models/BgReading.java"],
  // the library behind Home Assistant's Dexcom integration (the reading's value passed on as a number)
  pydexcom: ["gagebenne, n.d.",
    "gagebenne. (n.d.). <i>pydexcom</i> [Computer software; file pydexcom/glucose_reading.py]. GitHub. Retrieved " +
    "November 6, 2026, from", "", "", "https://github.com/gagebenne/pydexcom/blob/main/pydexcom/glucose_reading.py"],
  // the user guide as the U.S. Food and Drug Administration publishes it ("© Senseonics, Inc. 2023 PN:
  // LBL-6002-01-001 Rev 2 01/2023"; no copy published by the manufacturer was found, 2026-11-06)
  senseonics2023: ["Senseonics, Inc., 2023",
    "Senseonics, Inc. (2023). <i>Sensor / smart transmitter / mobile app user guide: A guide for using the Eversense E3 " +
    "continuous glucose monitoring system</i> (LBL-6002-01-001 rev. 2).", "", "",
    "https://www.accessdata.fda.gov/cdrh_docs/pdf16/P160048S021C.pdf"],
  // the author's name as the user guide itself writes it (in capitals); no address of the October 2023 edition
  // published by SIBIONICS was found (2026-11-06)
  sibionics2023: ["SIBIONICS, 2023",
    "SIBIONICS. (2023). <i>GS1 continuous glucose monitoring system app user guide</i> [English, mg/dL; revision date " +
    "October 2023].", "", ""],
  // Abbott Lingo: readings shared with a 3-hour delay, and only within its range
  stiehl2025: ["Stiehl &amp; Koenck, 2025",
    "Stiehl, C., &amp; Koenck, S. (2025, November 19). Guide: How to integrate Lingo into Apple Health app. " +
    "<i>Lingo</i>.", "", "", "https://www.hellolingo.com/blog/apple-health-integration-guide"],
  // HealthPartners Institute International Diabetes Center: four documents, each dated by its copyright line; the three
  // of 2024 carry the letters a, b, c, ordered by title ("CGM-guided ...", "Determine ...", "Guide ..."). The key
  // idc2024 is the guide (2024c).
  idc2024a: ["HealthPartners Institute International Diabetes Center, 2024a",
    "HealthPartners Institute International Diabetes Center. (2024a). <i>CGM-guided glucose management: Partnering for " +
    "personalized diabetes care</i> [Handout].", "", "",
    "https://www.healthpartners.com/institute/wp-content/uploads/2025/05/CGM-Guided-Glucose-Management.pdf"],
  idc2024b: ["HealthPartners Institute International Diabetes Center, 2024b",
    "HealthPartners Institute International Diabetes Center. (2024b). <i>Determine where to act</i> [Handout].", "", "",
    "https://www.healthpartners.com/institute/wp-content/uploads/2025/05/Determine-Where-to-Act.pdf"],
  idc2024: ["HealthPartners Institute International Diabetes Center, 2024c",
    "HealthPartners Institute International Diabetes Center. (2024c). <i>Guide to understanding the AGP report</i>.", "", "",
    "https://www.healthpartners.com/institute/wp-content/uploads/2025/05/Ambulatory-Glucose-Profile-Report-Overview.pdf"],
  idc2025: ["HealthPartners Institute International Diabetes Center, 2025",
    "HealthPartners Institute International Diabetes Center. (2025). <i>AGP report: Continuous glucose monitoring</i> " +
    "[Sample report with time in tight range].", "", "",
    "https://www.healthpartners.com/institute/wp-content/uploads/2025/09/AGP-Report-TITR.060125proof.pdf"],
  // accessibility: the ARIA authoring practices, WCAG 2.2 (the accessibility rules of docs/ACCESSIBILITY.md), the
  // color-vision simulation of the tests, and Home Assistant's documentation and code (works without a date by one
  // author: n.d.-a to n.d.-e, ordered by title: core, Exposing ..., intents, Sensors, Setting ...)
  // CGM values outside the reportable range left out of the analysis (p. 21); the date as the trial registry lists the
  // document (ClinicalTrials.gov, NCT03970278: Statistical Analysis Plan, 2024-08-26)
  ultragenyx2024: ["Ultragenyx Pharmaceutical Inc., 2024",
    "Ultragenyx Pharmaceutical Inc. (2024). <i>Statistical analysis plan: Study 401GSDIA02</i> (ClinicalTrials.gov " +
    "identifier NCT03970278).", "", "", "https://cdn.clinicaltrials.gov/large-docs/78/NCT03970278/SAP_001.pdf"],
  apg: ["World Wide Web Consortium, n.d.",
    "World Wide Web Consortium. (n.d.). <i>ARIA authoring practices guide</i>. Retrieved October 28, 2026, from", "", "",
    "https://www.w3.org/WAI/ARIA/apg/"],
  wcag22: ["World Wide Web Consortium, 2024",
    "World Wide Web Consortium. (2024). <i>Web content accessibility guidelines (WCAG) 2.2</i> (W3C Recommendation, " +
    "December 12, 2024).", "", "", "https://www.w3.org/TR/WCAG22/"],
  machado2009: ["Machado et al., 2009",
    "Machado, G. M., Oliveira, M. M., &amp; Fernandes, L. A. F. (2009). A physiologically-based model for simulation " +
    "of color vision deficiency. <i>IEEE Transactions on Visualization and Computer Graphics, 15</i>(6), 1291–1298.",
    "10.1109/TVCG.2009.113", ""],
  // the Dexcom and Nightscout integrations of Home Assistant (the value as a number; the device attribute of
  // Nightscout)
  haCore: ["Home Assistant, n.d.-a",
    "Home Assistant. (n.d.-a). <i>core</i> [Computer software; integration files homeassistant/components/dexcom/" +
    "sensor.py and homeassistant/components/nightscout/sensor.py]. GitHub. Retrieved November 6, 2026, from", "", "",
    "https://github.com/home-assistant/core/tree/dev/homeassistant/components"],
  haExpose: ["Home Assistant, n.d.-b",
    "Home Assistant. (n.d.-b). <i>Exposing entities to Assist</i> [Documentation]. Retrieved October 24, 2026, from",
    "", "", "https://www.home-assistant.io/voice_control/voice_remote_expose_devices/"],
  haIntents: ["Home Assistant, n.d.-c",
    "Home Assistant. (n.d.-c). <i>intents</i> [Computer software; response file responses/en/HassGetState.yaml]. " +
    "GitHub. Retrieved October 24, 2026, from", "", "",
    "https://github.com/home-assistant/intents/blob/main/responses/en/HassGetState.yaml"],
  // the Companion apps' sensors: the 15-minute update of the Android app, the Apple Health sensors while the device is
  // locked
  haSensors: ["Home Assistant, n.d.-d",
    "Home Assistant. (n.d.-d). <i>Sensors</i> [Home Assistant Companion documentation]. Retrieved November 6, 2026, " +
    "from", "", "", "https://companion.home-assistant.io/docs/core/sensors/"],
  haSentences: ["Home Assistant, n.d.-e",
    "Home Assistant. (n.d.-e). <i>Setting up custom sentences in configuration.yaml</i> [Documentation]. Retrieved " +
    "October 24, 2026, from", "", "", "https://www.home-assistant.io/voice_control/custom_sentences_yaml/"],
};

// ------------------------------------------------------------------ helpers
// APA 7 in-text citations, each label a link to its entry in the reference list:
//   C(id, ...)        parenthetical, several sources in alphabetical order: "(Battelino et al., 2019; Monnier et al., 2017)"
//   CP(id, part)      with a specific part: "(Battelino et al., 2023, Table 4)"
//   L(id, text)       the link alone (for narrative and secondary citations)
// (a real link with an address, so that the keyboard reaches it; the click handler opens the entry instead)
const L = (id, text = REFS[id][0]) => `<a class="cite" href="#ref-${id}" data-ref="${id}">${text}</a>`;
// (works of the same author are combined, APA 7 section 8.12: "(Author, 2026a, 2026b)")
const C = (...ids) => {
  const sorted = [...ids].sort((a, b) => REFS[a][0].localeCompare(REFS[b][0])), out = [];
  const author = (id) => REFS[id][0].replace(/, [^,]+$/, ""), year = (id) => REFS[id][0].slice(author(id).length + 2);
  sorted.forEach((id, i) => out.push(i && author(id) === author(sorted[i - 1]) ? `, ${L(id, year(id))}` : `${i ? "; " : ""}${L(id)}`));
  return "(" + out.join("") + ")";
};
// (the part is a locator of the source, "Table 6.2" or "Recommendation 6.3a": marked nodec, so that the decimal
// separator setting never rewrites it, see draw)
const CP = (id, part) => "(" + L(id) + ", " + `<span class="nodec">${part}</span>` + ")";
// the year alone, for a second work of the same author in one citation: "(Author, 2026a, 2026b)"
const LY = (id) => L(id, REFS[id][0].replace(/^.*, /, ""));
// APA 7 reference-list order: word by word through the author list ("nothing precedes something"), then the year
const apaOrder = (a, b) => {
  const key = (t) => {
    const m = t.match(/ \((\d{4}|n\.d\.)(-?[a-z])?\)\./), i = m ? m.index : t.length;
    return [t.slice(0, i).split(/[\s,.;&]+|amp;/).filter(Boolean), m ? (m[1] === "n.d." ? "0000" : m[1]) + (m[2] || "") : ""];
  };
  const [wa, ya] = key(a), [wb, yb] = key(b);
  for (let i = 0; i < Math.min(wa.length, wb.length); i++) { const c = wa[i].localeCompare(wb[i]); if (c) return c; }
  return wa.length - wb.length || ya.localeCompare(yb);
};
const tbl = (head, rows) => "<table><tr>" + head.map((h) => `<th scope="col">${h}</th>`).join("") + "</tr>" +
  rows.map((r) => "<tr>" + r.map((c) => `<td>${c}</td>`).join("") + "</tr>").join("") + "</table>";
const sw = (hex) => `<span class="sw" style="background:${hex}"></span>`;

// ------------------------------------------------------------------ the document
// The content is the language's document, i18n/<language>/documentation.json (English as the fallback for each text).
// Its sections are HTML with placeholders for the live settings ({unit}, {stale}, ...) and for the shared texts below,
// and with tokens that this page turns into links:
//   {cite:id1,id2}    a parenthetical citation (C), {cite:id|part} with a specific part (CP)
//   {ref:id}          the citation label as a link (L), {ref:id|text} with its own text, {ref_year:id} the year alone (LY)
//   {sec:id|text}     a link to a section of this page (SEC)
// The citations themselves (REFS) are not translated.
const SEC = (id, text) => `<a class="sec" href="#${id}" data-sec="${id}">${text}</a>`;
const TOKEN = /\{(cite|ref|ref_year|sec):([^{}|]+)(?:\|([^{}]*))?\}/g;
// A token whose id names no reference (a typing error in a translation) is shown as its text and reported on the
// console, so that one wrong token never leaves the whole section empty.
const docsHtml = (html) => html.replace(TOKEN, (m, kind, id, text) => {
  if (kind === "sec") return SEC(id, text);
  const unknown = id.split(",").filter((k) => !REFS[k]);
  if (unknown.length) {
    console.warn(`GlucoCraft Documentation: unknown reference ${unknown.join(", ")} in ${m}`);
    return esc(text ?? m);
  }
  if (kind === "ref_year") return LY(id);
  if (kind === "ref") return text === undefined ? L(id) : L(id, text);
  return text === undefined ? C(...id.split(",")) : CP(id, text);
});
// a link that opens in a new tab says so to screen readers (WCAG technique G201)
const newTab = (html) => html.replace(/(<a\b[^>]*\btarget="_blank"[^>]*>)([\s\S]*?)<\/a>/g,
  (m, open, text) => `${open}${text}<span class="sr"> ${esc(gcT("ui.link.new_tab"))}</span></a>`);
// each table of a section is named by the heading above it, its subheading or else the section's title (WCAG 1.3.1);
// a subheading without an id gets one
// Each table stands in a scroll region that the keyboard reaches and that is named by the same heading (WCAG 1.4.10,
// 2.1.1): a table wider than a narrow screen scrolls sideways in it, and its words break only between words.
const nameTables = (id, html) => {
  let n = 0, last = `${id}-title`;
  return html.replace(/<h3( id="([^"]+)")?>|<table>|<\/table>/g, (tag, attr, own) => {
    if (tag === "<table>") {
      return `<div class="tw" tabindex="0" role="region" aria-labelledby="${last}"><table aria-labelledby="${last}">`;
    }
    if (tag === "</table>") return "</table></div>";
    last = own || `${id}-h${++n}`;
    return own ? tag : `<h3 id="${last}">`;
  });
};
// the header cells of each table get their scope here, so that no text of a language can leave it out (WCAG 1.3.1):
// the cells of a first row that has header cells only are column headers, and a header cell that starts another row
// is that row's header
const scopeTables = (html) => html.replace(/<table\b[^>]*>[\s\S]*?<\/table>/g, (table) => {
  let first = true;
  return table.replace(/<tr\b[^>]*>[\s\S]*?<\/tr>/g, (row) => {
    const head = first && !/<td\b/.test(row);
    first = false;
    if (head) return row.replace(/<th(?=[\s>])(?![^>]*\bscope=)/g, '<th scope="col"');
    return row.replace(/^(<tr\b[^>]*>\s*)<th(?=[\s>])(?![^>]*\bscope=)/, '$1<th scope="row"');
  });
});

// The shared texts of the document, used in the sections as placeholders of the same name, each said in one place:
//   configure       the menu of GlucoCraft's settings (strings.json options.step.init.menu_options), the path users are
//                   given; screen_*: the screens of Configure (config_flow.OPTIONS_MENU), named as the menu names them
// Each may use the ones before it.
const DOC_TEXTS = ["configure", "screen_sources", "screen_units", "screen_displays", "screen_notifications",
  "screen_report", "standard_statement", "limit_responsibility", "limit_note", "interval_note",
  "sensor_active_note", "gap_rule", "custom_note", "gmi_mmol_rule", "no_default_goals"];

// The sections in their order: [id (the address anchor), icon]; the title and the body are the document's
// docs.section.<id>.title and .body (the id with "_" for "-"). Each note says what the section holds.
const SECTIONS = [
  // the disclaimer, in full (the catalogue's disclaimer.text)
  ["disclaimer", "mdi:alert-circle-outline"],
  // the three essentials, the data source and the versions
  ["essentials", "mdi:clipboard-check-outline"],
  // how GlucoCraft is designed
  ["how", "mdi:cogs"],
  // LO / HI readings and the sensor-limit settings
  ["lohi", "mdi:arrow-collapse-vertical"],
  // the gap rule, the reading interval, the no-current-data limit
  ["gaps", "mdi:timer-sand"],
  // the data kept and the CSV export
  ["retention", "mdi:database-clock-outline"],
  // the dashboard's pages
  ["pages", "mdi:view-dashboard-outline"],
  // the ranges, their names and the colors
  ["ranges", "mdi:palette"],
  // the metrics and their sources, each cited context once
  ["metrics", "mdi:book-education-outline"],
  // the published findings; a finding that cannot be stated without naming a medication, substance or treatment
  // (LANGUAGE_STYLE rule 15) is cited only in What GlucoCraft Leaves Out, and Why
  ["studies", "mdi:flask-outline"],
  // what GlucoCraft leaves out on purpose, and why
  ["left-out", "mdi:filter-remove-outline"],
  // the notifications (not alarms)
  ["notifications", "mdi:bell-outline"],
  // automations: GlucoCraft's entities are ordinary entities; no examples
  ["automations", "mdi:robot-outline"],
  // installing (Marketplace, HACS, by hand), updating and removing GlucoCraft, and what Home Assistant keeps
  ["install", "mdi:download-outline"],
  // the setup
  ["setup", "mdi:tune-variant"],
  // the sources GlucoCraft reads
  ["cgms", "mdi:access-point"],
  // the entity names
  ["entities", "mdi:tag-outline"],
  // the terms not defined elsewhere, in alphabetical order
  ["glossary", "mdi:book-alphabet-variant"],
  // privacy and the wall panels
  ["privacy", "mdi:shield-outline"],
  // the accessibility statement (rules: docs/ACCESSIBILITY.md)
  ["accessibility", "mdi:human"],
  // symptom, cause and what to check
  ["troubleshooting", "mdi:wrench"],
  // the known limitations
  ["limits", "mdi:alert-outline"],
  // the references, in APA 7 order
  ["references", "mdi:format-list-bulleted"],
  // the version, the license and the AGPL notices, the trademarks
  ["about", "mdi:license"],
];

// the color names of the Colors table: the names of the default palettes' colors (glucocraft-shared.js ZONE_DEFAULT, the
// range colors, and BAND_DEFAULT, the graph range colors: the CGM Report's time-in-ranges colors) by zone; a color
// chosen in the settings is shown as its hex value
const ZONE_NAME = { very_low: "docs.color_name.dark_red", low: "docs.color_name.red", near_low: "docs.color_name.amber",
  tight_range: "docs.color_name.green", near_high: "docs.color_name.amber", high: "docs.color_name.blue",
  very_high: "docs.color_name.dark_blue" };
const BAND_NAME = { very_low: "docs.color_name.dark_red", low: "docs.color_name.red", near_low: "docs.color_name.green",
  tight_range: "docs.color_name.light_green", near_high: "docs.color_name.green", high: "docs.color_name.yellow",
  very_high: "docs.color_name.orange" };
const RANGE_NAME = { very_low: "docs.colors.range.very_low", low: "docs.colors.range.low",
  near_low: "docs.colors.range.near_low", tight_range: "docs.colors.range.tight_range",
  near_high: "docs.colors.range.near_high", high: "docs.colors.range.high", very_high: "docs.colors.range.very_high" };
// anchors inside a section that a link can open: anchor id -> section id
const ANCHORS = { colors: "ranges", "color-cues": "ranges", "other-groups": "metrics" };

// the Colors table: each range of the settings with its value span, its color, its graph band and its name. The two
// in-range rows outside the tight range exist only where the tight range does not reach the in-range limit (the
// settings allow tight_low = low and tight_high = high); otherwise their span would be inverted ("181–180").
const colorsTable = (x) => {
  // a span whose upper end is below its lower end as shown (two limits less than one display step apart: no value
  // shown can lie in it) has no row, as the band key under the dashboard's graphs
  const v = (k) => x.g(x.r[k]), span = (from, to) => (parseFloat(from) > parseFloat(to) ? null
    : gcT("docs.colors.value_span", { from, to }));
  const rows = [
    ["very_low", gcT("docs.colors.value_below", { value: v("very_low") })],
    ["low", span(v("very_low"), x.below(x.r.low))],
    ...(x.r.tight_low > x.r.low ? [["near_low", span(v("low"), x.below(x.r.tight_low))]] : []),
    ["tight_range", span(v("tight_low"), v("tight_high"))],
    ...(x.r.high > x.r.tight_high ? [["near_high", span(x.above(x.r.tight_high), v("high"))]] : []),
    ["high", span(x.above(x.r.high), v("very_high"))],
    ["very_high", gcT("docs.colors.value_above", { value: v("very_high") })],
  ].filter(([, value]) => value !== null);
  return tbl([gcT("docs.colors.head.value", { unit: x.u }), gcT("docs.colors.head.color"), gcT("docs.colors.head.band"),
    gcT("docs.colors.head.range")], rows.map(([z, value]) => [value, sw(x.zc[z]) + x.zn(z), sw(x.bc[z]) + x.bn(z),
    gcT(RANGE_NAME[z])]));
};
// the reference list, in APA order: by the author list as printed (the first author, then the next authors), not by
// the in-text label. APA 7: the DOI as https://doi.org/... only; no PubMed link in the reference text (the PMIDs in REFS
// are kept for checking the entries)
const referenceList = () => "<ul class=\"refs\">" + Object.entries(REFS).sort((a, b) => apaOrder(a[1][1], b[1][1]))
  .map(([id, [, text, doi, , url]]) => `<li id="ref-${id}">${text}` +
    (url ? ` <a href="${url}" target="_blank" rel="noopener">${url}</a>` : "") +
    (doi ? ` <a href="https://doi.org/${doi}" target="_blank" rel="noopener">https://doi.org/${doi}</a>` : "") +
    "</li>").join("") + "</ul>";

// ------------------------------------------------------------------ the card
// The fields of a source that the text shows: its sensor limits, their settings and why an automatic limit has its value
const DOC_SOURCE_FIELDS = ["lower_limit", "upper_limit", "lower_limit_setting", "upper_limit_setting",
  "lower_limit_auto_reason", "upper_limit_auto_reason"];
/**
 * Return the configuration attributes the text uses (a: sensor.glucocraft_configuration), with the language, as one
 * string. The configuration sensor also changes for values the text does not use (each source's live reading interval,
 * for example), and drawing the page again replaces it under the reader's position (keyboard focus, screen reader): the
 * page is drawn again only when this string changes, so a source enters with the fields of DOC_SOURCE_FIELDS only.
 */
const docsSignature = (language, a) => {
  const source = (q) => (q ? DOC_SOURCE_FIELDS.map((k) => q[k] ?? null) : null);
  return JSON.stringify([language, a.ranges || null, a.unit || null, a.no_current_data_minutes ?? null,
    a.retention_days ?? null, a.primary_source?.name || null, a.secondary_source?.name || null,
    a.decimal_separator || null, a.gap_threshold_seconds ?? null, a.reading_interval_seconds ?? null,
    source(a.sources?.primary), source(a.sources?.secondary), a.zone_colors || null, a.band_colors || null,
    a.integration_version || null]);
};
// the page's style sheet (draw writes it with the page)
const GCDOCS_STYLE = `<style>
        :host { display: block; }
        ha-card { padding: 8px 16px 14px; color: var(--primary-text-color); font-size: 15px; line-height: 1.5; }
        .bar { display: flex; justify-content: flex-end; gap: 14px; font-size: 14px; }
        /* links are underlined: told apart from the text by more than their color (WCAG 1.4.1) */
        a { color: ${GC_CSS.link}; cursor: pointer; text-decoration: underline; text-underline-offset: 2px; }
        .bar button { border: none; background: none; padding: 4px 0; font: inherit; color: ${GC_CSS.link}; cursor: pointer; }
        a:focus-visible, button:focus-visible, [tabindex="-1"]:focus-visible { ${GC_CSS.focus} }
        .doc { border-bottom: 1px solid var(--divider-color); }
        h2.sh { margin: 0; font-size: 17px; font-weight: 600; }
        .sh button { width: 100%; border: none; background: none; color: inherit; font: inherit; text-align: left; cursor: pointer;
                     padding: 12px 0; display: flex; align-items: center; gap: 10px; }
        /* the arrow is drawn, not read: the button's state is announced (aria-expanded) */
        .sh button::after { content: "▸"; content: "▸" / ""; margin-left: auto; color: var(--secondary-text-color); }
        .sh button[aria-expanded="true"]::after { content: "▾"; content: "▾" / ""; }
        ha-icon { --mdc-icon-size: 22px; color: var(--secondary-text-color); }
        .body { padding: 0 0 12px; overflow-wrap: anywhere; }
        h3 { font-size: 15px; margin: 14px 0 4px; }
        p { margin: 6px 0; }
        ul { margin: 4px 0; padding-left: 20px; } li { margin: 4px 0; }
        .tw { overflow-x: auto; margin: 8px 0; }
        .tw:focus-visible { ${GC_CSS.focus} }
        table { border-collapse: collapse; width: 100%; margin: 0; font-size: 14px; }
        td, th { overflow-wrap: normal; }
        th, td { text-align: left; vertical-align: top; padding: 5px 6px; border-bottom: 1px solid var(--divider-color); }
        th { color: var(--secondary-text-color); font-weight: 500; }
        code { font-size: 14px; overflow-wrap: anywhere; }
        /* a code example wraps at any width (reflow to 320 px; nothing reachable only by scrolling); a copy keeps its
           lines */
        pre { background: var(--secondary-background-color); padding: 10px; border-radius: 8px; font-size: 14px;
          white-space: pre-wrap; overflow-wrap: anywhere; }
        .box { border-left: 6px solid; border-radius: 6px; padding: 8px 12px; margin: 8px 0; }
        .box.alert { border-color: #E53935; background: rgba(229,57,53,.10); }
        .box.note { border-color: #F9A825; background: rgba(249,168,37,.10); }
        .sw { display: inline-block; width: 12px; height: 12px; border-radius: 3px; vertical-align: -1px; margin-right: 4px; }
        .s { color: var(--secondary-text-color); font-size: 14px; }
        /* text read by screen readers only: not shown, but in the reading order */
        ${GC_CSS.sr}
        .refs { list-style: none; padding-left: 0; } .refs li { margin: 6px 0; padding-left: 2em; text-indent: -2em; }
        .refs li.hl { background: rgba(126,87,194,.18); border-radius: 4px; }
      </style>`;
/**
 * Return the HTML of one section ([id, icon] of SECTIONS): its heading with the button that shows and hides it, and
 * its text, hidden unless the section is open (open: the ids of the open sections; p: the placeholders, params()).
 */
const docsSection = ([id, icon], open, p) => {
  const key = `docs.section.${id.replace(/-/g, "_")}`;
  return `<section class="doc" id="${id}"><h2 class="sh" id="${id}-title">` +
    `<button type="button" data-sec="${id}" aria-expanded="${open.has(id)}" aria-controls="${id}-body">` +
    `<ha-icon icon="${icon}" aria-hidden="true"></ha-icon><span>${gcT(`${key}.title`)}</span></button></h2>` +
    `<div class="body" id="${id}-body"${open.has(id) ? "" : ' hidden="until-found"'}>` +
    `${nameTables(id, scopeTables(newTab(docsHtml(gcT(`${key}.body`, p)))))}</div></section>`;
};
// The decimal comma on every number of the drawn page (root), by the shared rule (dtx, which keeps the point of a
// version, a release or a cited locator); not in code, the references, links or texts marked "nodec" (the disclaimer,
// version numbers, the separator examples)
const docsDecimalComma = (root) => {
  applyRegional({ decimal_separator: "," });
  const skip = (n) => n.parentElement?.closest("code, pre, .refs, .nodec, a[href]");
  const walk = document.createTreeWalker(root.querySelector("ha-card"), NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) if (!skip(n) && /\d\.\d/.test(n.nodeValue)) n.nodeValue = dtx(n.nodeValue);
};
// The spoken rule on the drawn page (root; ACCESSIBILITY.md rule 4): a text with a unit symbol, a comparison sign
// before a number or a range of two numbers is shown as written and hidden from screen readers, which read its spoken
// form (gcSpeakText); not in code, the references or the buttons
const docsSpoken = (root) => {
  if (typeof globalThis.document?.createTreeWalker !== "function") return;
  const said = [], walk = document.createTreeWalker(root.querySelector("ha-card"), NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    if (n.parentElement?.closest("code, pre, .refs, .sr, button, style")) continue;
    const sp = gcSpeakText(n.nodeValue);
    if (sp !== n.nodeValue) said.push([n, sp]);
  }
  for (const [n, sp] of said) {
    const shown = document.createElement("span"), sr = document.createElement("span");
    shown.setAttribute("aria-hidden", "true"); shown.textContent = n.nodeValue;
    sr.className = "sr"; sr.textContent = sp;
    n.replaceWith(shown, sr);
  }
};
/** The Documentation page (custom:glucocraft-documentation): every section, with the live settings filled in. */
class GlucoCraftDocumentation extends HTMLElement {
  setConfig(config) { this._cfg = config || {}; if (!this.shadowRoot) this.attachShadow({ mode: "open" }); }
  // the new state: drawn again only when a setting the text uses changes
  set hass(hass) {
    this._hass = hass;
    // the first render waits for the language's document
    if (!gcI18nReady(hass)) {
      if (!this._loading) this._loading = gcI18nLoad(hass).then(() => { this._loading = null; this._sig = null; this.hass = this._hass; });
      return;
    }
    // redraw only when a setting the text uses changes (docsSignature)
    const sig = docsSignature(globalThis.glucocraftI18n.language,
      hass.states["sensor.glucocraft_configuration"]?.attributes || {});
    if (sig === this._sig) return;
    this._sig = sig;
    this.draw();
  }
  // A link to a section of this page from elsewhere on the page (the footer's "Disclaimer" and "About and License")
  // changes only the address: the page follows it, as it follows the address it opens with (_openHash, set by draw)
  connectedCallback() {
    this._onHash ||= () => this._openHash?.(location.hash.slice(1));
    window.addEventListener("location-changed", this._onHash);
    window.addEventListener("hashchange", this._onHash);
  }
  disconnectedCallback() {
    window.removeEventListener("location-changed", this._onHash);
    window.removeEventListener("hashchange", this._onHash);
  }
  getCardSize() { return 12; }
  getGridOptions() { return { columns: "full" }; }
  /** Return the live settings the sections use (ranges, unit, sources, limits, colors), formatted for display. */
  ctx() {
    const a = this._hass?.states["sensor.glucocraft_configuration"]?.attributes || {};
    const agp = CONS_RANGES, r = { ...agp, ...(a.ranges || {}) };
    // the display unit: consensus thresholds as published, other values / 18.016, one decimal; mg/dL as whole numbers
    // (a limit entered in mmol/L is stored with one decimal in mg/dL, 8.9 mmol/L = 160.3 mg/dL, and shown as 160)
    const u = a.unit === "mmol/L" ? "mmol/L" : "mg/dL", mm = u === "mmol/L";
    const gv = (mg) => CONS_MMOLL[mg] ?? round(mg / MGDL_PER_MMOLL, 1);   // halves away from zero (= units.py)
    const gw = (mg) => round(mg);   // whole mg/dL (= units.fmt)
    const g = (mg) => (mm ? gv(mg).toFixed(1) : String(gw(mg)));
    // the display colors in use (attribute zone_colors; the default palette until it arrives) and the graph band
    // colors, and how the Colors section names them: the default palette's color name, otherwise the hex value chosen
    const inUse = (defaults, published) => Object.fromEntries(Object.entries(gcPalette(defaults, published))
      .map(([z, hex]) => [z, hex.toUpperCase()]));
    const zc = inUse(ZONE_DEFAULT, a.zone_colors), bc = inUse(BAND_DEFAULT, a.band_colors);
    const named = (used, defaults, names) => (z) => (used[z] === defaults[z] ? ` ${gcT(names[z])}`
      : ` <span class="nodec">${used[z]}</span>`);
    const iv = +a.reading_interval_seconds;
    return {
      zc, zn: named(zc, ZONE_DEFAULT, ZONE_NAME), bc, bn: named(bc, BAND_DEFAULT, BAND_NAME),
      r, custom: Object.keys(agp).some((k) => r[k] !== agp[k]), u, g,
      // the last value below a limit and the first value above it, in the step of the display unit
      below: (mg) => (mm ? (gv(mg) - 0.1).toFixed(1) : String(gw(mg) - 1)),
      above: (mg) => (mm ? (gv(mg) + 0.1).toFixed(1) : String(gw(mg) + 1)),
      stale: a.no_current_data_minutes ?? STALE_DEFAULT_MIN, retention: a.retention_days ?? RETENTION_DAYS,
      // the gap threshold and reading interval of the source in use, and the sensor limits of each source
      gapMin: (() => { const ivs = iv > 0 ? iv : 60;
        const gs = +a.gap_threshold_seconds > 0 ? +a.gap_threshold_seconds : gcGapThreshold(ivs);
        return String(round(gs / 60, 1)); })(),
      interval: iv > 0 ? gcT("docs.ctx.interval_seconds", { count: round(iv) }) : gcT("docs.ctx.interval_unknown"),
      limitsText: (() => {
        const so = a.sources || {}, out = [];
        [["primary", "docs.ctx.limits.primary"], ["secondary", "docs.ctx.limits.secondary"]].forEach(([k, name]) => {
          const q = so[k]; if (!q) return;
          const one = (v, st, why) => {
            const value = v > 0 ? `${g(+v)} ${u}` : "---";
            if (st && !String(st).toLowerCase().startsWith("auto")) return value;
            return why ? gcT("docs.ctx.limits.automatic_reason", { value, reason: esc(why) })
              : gcT("docs.ctx.limits.automatic", { value });
          };
          out.push(gcT("docs.ctx.limits.source", { source: gcT(name),
            lower: one(q.lower_limit, q.lower_limit_setting, q.lower_limit_auto_reason),
            upper: one(q.upper_limit, q.upper_limit_setting, q.upper_limit_auto_reason) }));
        });
        return out.length ? out.join(gcT("docs.ctx.list_separator")) : gcT("docs.ctx.limits.none");
      })(),
      primary: a.primary_source?.name || gcT("docs.ctx.primary_default"), secondary: a.secondary_source?.name || "",
      dec: a.decimal_separator === "," ? "," : ".",
      version: a.integration_version || "",
    };
  }
  /** The values of the document's placeholders: the live settings, the shared texts and the generated parts. */
  params(x) {
    const p = {
      unit: x.u, stale: x.stale, retention: x.retention, interval: x.interval, gap_minutes: x.gapMin,
      sensor_limits: x.limitsText, primary_source: esc(x.primary),
      value_40: x.g(40), value_54: x.g(54), value_250: x.g(250), value_400: x.g(400), value_450: x.g(450), value_50: x.g(50),
      disclaimer: gcT("disclaimer.text"),
    };
    // each shared text with the placeholders filled, in order (a text may use the ones before it)
    for (const name of DOC_TEXTS) p[name] = gcT(`docs.text.${name}`, p);
    p.sources_in_use = x.secondary
      ? gcT("docs.how.sources_two", { primary_source: p.primary_source, second_source: esc(x.secondary) })
      : gcT("docs.how.sources_one", { primary_source: p.primary_source });
    p.ranges_in_use = gcT(x.custom ? "docs.ranges.in_use_custom" : "docs.ranges.in_use_consensus");
    p.colors_table = colorsTable(x);
    p.colors_caption = gcT(x.custom ? "docs.colors.caption_custom" : "docs.colors.caption_consensus");
    p.product = x.version ? gcT("docs.about.product_version", { version: esc(x.version) }) : gcT("docs.about.product");
    p.reference_list = referenceList();
    return p;
  }
  /**
   * Render all sections, keep the open ones open, and open the section named in the address.
   *
   * The page is written anew (only when a text it shows changes, docsSignature); the control that had the focus is
   * focused again afterwards (its counterpart in the new page, as gcRender does: ACCESSIBILITY.md rule 5).
   *
   * Each section is a heading with a button that shows and hides its text (the WAI-ARIA disclosure pattern): the
   * headings can be listed and followed by screen readers, the button says whether its section is expanded, and a
   * hidden text is still found by the browser's search, which opens its section ("until-found").
   */
  draw() {
    const root = this.shadowRoot;
    const open = new Set([...(root.querySelectorAll('.sh button[aria-expanded="true"]') || [])].map((b) => b.dataset.sec));
    if (!this._drawnOnce) ["disclaimer", "essentials"].forEach((id) => open.add(id));
    // a section named in the address opens: #disclaimer and #about (page footers), #references (report);
    // #colors (the color key at the bottom of each page) opens Ranges, Terminology and Colors at its Colors table
    const hash = location.hash.slice(1), inner = ANCHORS[hash] ? hash : "";
    const toSec = this._drawnOnce ? "" : ANCHORS[hash] || (SECTIONS.some(([id]) => id === hash) ? hash : "");
    if (toSec) open.add(toSec);
    const x = this.ctx(), p = this.params(x), focus = gcFocusKey(root, root);
    root.innerHTML = GCDOCS_STYLE + `
      <ha-card lang="${globalThis.glucocraftI18n.htmlLang}">
        <div class="bar"><button type="button" id="all">${gcT("docs.bar.open_all")}</button><button type="button" id="none">${gcT("docs.bar.close_all")}</button></div>
        ${SECTIONS.map((s) => docsSection(s, open, p)).join("")}
        <p class="s nodec">${gcT("docs.footer.version", { version: GCDOCS_VERSION })}</p>
      </ha-card>`;
    this._drawnOnce = true;
    if (x.dec === ",") docsDecimalComma(root);
    docsSpoken(root);
    this.wireSections(root, toSec, inner);
    gcRefocus(root, focus);
  }
  /**
   * Wire up the sections drawn in root: the buttons that show and hide them, the browser's search, the links to a
   * section, an anchor or a reference, and the section to open after the first drawing (toSec, at the anchor inner).
   */
  wireSections(root, toSec, inner) {
    // a section shown or hidden: its button's state and its text
    const show = (id, on) => {
      const btn = root.querySelector(`.sh button[data-sec="${id}"]`), body = root.getElementById(`${id}-body`);
      if (!btn || !body?.setAttribute) return;
      btn.setAttribute("aria-expanded", String(on));
      if (on) body.removeAttribute("hidden"); else body.setAttribute("hidden", "until-found");
    };
    root.querySelectorAll("button[data-sec]").forEach((b) => (b.onclick = () => show(b.dataset.sec, b.getAttribute("aria-expanded") !== "true")));
    // the browser's search found a text in a hidden section: the section is open now
    SECTIONS.forEach(([id]) => root.getElementById(`${id}-body`)?.addEventListener?.("beforematch", () => show(id, true)));
    // A link moves to its target: the section opens and scrolls into view, smoothly or at once where the system's
    // reduced-motion setting is on, and the focus moves to the target (WCAG 2.4.3), so that the keyboard and the
    // screen reader continue from there: a section's heading button, an anchor or a reference entry.
    const behavior = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ? "auto" : "smooth";
    const arrive = (el, block = "start") => {
      if (!el?.scrollIntoView) return;
      if (!el.matches("button, a[href]")) el.setAttribute("tabindex", "-1");
      el.scrollIntoView({ behavior, block });
      el.focus({ preventScroll: true });
    };
    const goSec = (id) => { show(id, true); arrive(root.querySelector(`.sh button[data-sec="${id}"]`)); };
    root.querySelectorAll("a.sec").forEach((a) => (a.onclick = (e) => { e.preventDefault(); goSec(a.dataset.sec); }));
    // a later address with a section or an anchor (a link elsewhere on the page) opens it the same way
    this._openHash = (h) => {
      const sec = ANCHORS[h] || (SECTIONS.some(([id]) => id === h) ? h : "");
      if (!sec) return;
      show(sec, true);
      if (ANCHORS[h] && root.getElementById(h)) arrive(root.getElementById(h)); else goSec(sec);
    };
    // (an anchor that is not on the page opens its section). After a short wait, so that the browser has laid out the
    // sections just drawn and the dashboard around the page before the scroll; a scroll at once can stop short.
    if (toSec) setTimeout(() => { show(toSec, true); if (inner && root.getElementById(inner)) arrive(root.getElementById(inner)); else goSec(toSec); }, 50);
    root.getElementById("all").onclick = () => SECTIONS.forEach(([id]) => show(id, true));
    root.getElementById("none").onclick = () => SECTIONS.forEach(([id]) => show(id, false));
    root.querySelectorAll("a.cite").forEach((a) => (a.onclick = (e) => {
      e.preventDefault();
      show("references", true);
      const li = root.getElementById(`ref-${a.dataset.ref}`);
      root.querySelectorAll(".refs li.hl").forEach((l) => l.classList.remove("hl"));
      li?.classList.add("hl");
      arrive(li, "center");
    }));
  }
}

if (!customElements.get("glucocraft-documentation")) customElements.define("glucocraft-documentation", GlucoCraftDocumentation);
console.info(`%c GLUCOCRAFT-DOCS %c v${GCDOCS_VERSION} `, "background:#7E57C2;color:#fff", "background:#000;color:#fff");
