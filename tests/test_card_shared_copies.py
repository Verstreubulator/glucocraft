# License: AGPL-3.0-only (LICENSE).
"""The cards' shared module (custom_components/glucocraft/www/glucocraft-shared.js) against the Python rules.

The rules that several card files use are written once, in glucocraft-shared.js, which every card file imports itself
(docs/DEVELOPER_GUIDE.md section 5). These tests check:

- each rule is written once: in the module, in no card file;
- the module against the Python rules: the rounding (halves away from zero, once, no negative zero) and the two-decimal
  rule at a threshold (units.round_half_up, units.shown_against), the consensus thresholds in mmol/L (units.py), the
  mmol/L rounding of a file's float32 value (the panels' gc_units.h r1_mmol, by a Python model of it), and the
  percentages of a GLU2 file rounded as the backend rounds them (panel_feed._pct32);
- the GLU2 v3 parser reads what panel_feed.py writes: the statistics, the readings, the weekly values, the GCX1
  description lines in UTF-8 and the unrounded minimum and maximum of the GCM1 block;
- the loading: each card file loads on its own with only glucocraft-i18n.js and glucocraft-shared.js
  next to it, imports the module itself and defines its elements; a second load of the module keeps the first;
  frontend.py serves the module below both paths and its content hash covers it.

The gap rule is compared with gaps.py in tests/test_frontend_consistency.py, the spoken text in tests/test_accessibility.py.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import random
import re
import struct
import time
import types
from pathlib import Path

import pytest
from glucocraft import panel_feed as pf
from glucocraft import units
from glucocraft.const import STAT_PERIODS
from glucocraft.storage import GlucoseReading

from conftest import NO_PANELS, PANELS, stats_record
from node_runner import needs_node, node_json

WWW = Path(__file__).resolve().parents[1] / "custom_components" / "glucocraft" / "www"
CARD_NAMES = ("display-card", "ui", "docs", "strategy", "report")
FILES = {n: (WWW / f"glucocraft-{n}.js").read_text(encoding="utf-8") for n in (*CARD_NAMES, "shared")}
SHARED = FILES["shared"]
# the module's names in scope, as a card file takes them
USE = "const S = globalThis.glucocraftShared;\n"


def _node(src: str) -> object:
    """Run src after the language module and the shared module (js_i18n.i18n_prelude); return its JSON result."""
    return node_json(USE + src)


def _eval(expr: str) -> object:
    """Evaluate the function body expr with the shared module as S."""
    return _node("process.stdout.write('\\u0001' + JSON.stringify((() => { " + expr + " })()));\n")


# ------------------------------------------------------------------------------------------------ written once
SHARED_DEFINITIONS = (
    "const esc = ", "const rh1 = ", "const nf = ", "const dtx = ", "const CONS_MMOLL = ", "const MGDL_PER_MMOLL = ",
    "const gcRender = ", "const GC_FOCUSABLE = ", "const gcGaps = ", "const gcGapThrAt = ", "const gcGapIn = ",
    "const gcRangeAreas = ", "const gcSpeakText = ", "const parseGlu2 = ", "const shownAgainst = ",
    "const gmiMmolFromMean = ", "const gmiPctToMmol = ", "const ZONE_DEFAULT = ", "const BAND_DEFAULT = ",
    "const gcFile = ", "const gcLum = ", "const gcContrast = ", "const gcOnColor = ",
    "const unesc = ", "const gcFit = ", "const gcGridStep = ", "const gcGapThreshold = ", "const GMI = ",
    "const gmiMmolToPct = ", "const GC_CONS_GOAL = ", "const gcMeets = ", "const gcNavigate = ", "const gcPlainClick = ",
    "const gcFileFailed = ", "const gcNewFailure = ", "const CONS_RANGES = ", "const STALE_DEFAULT_MIN = ",
    "const gcCap = ", "const gcRefocus = ",
)  # fmt: skip


def test_each_shared_rule_is_written_once():
    for definition in SHARED_DEFINITIONS:
        assert SHARED.count(definition) == 1, definition
        for name in CARD_NAMES:
            assert definition not in FILES[name], (name, definition)
    # the copies under other names are gone too
    for name, retired in (("docs", "const escD = "), ("docs", "const dtxD = "),("report", "const rEsc = "), ("report", "const RCONS = "),
                          ("report", "const rN = "), ("ui", "const GLU2 = "), ("ui", "const gcSpeakUi = "),
                          ("display-card", "const lum = "), ("display-card", "const onColor = "),
                          ("report", "const rUnesc = "), ("report", "const rFit = "), ("ui", "const meets = ")):  # fmt: skip
        assert retired not in FILES[name], (name, retired)


def test_one_shared_module_bootstrap_in_every_card_file():
    """Every card file imports the module itself, next to its own URL (below the versioned and the unversioned path),
    and waits for it before it defines its elements."""
    lines = (
        (
            "const GC_SHARED = globalThis.glucocraftShared ? Promise.resolve() : "
            'import(new URL("./glucocraft-shared.js", import.meta.url).href);'
        ),
        "await GC_SHARED;",
    )
    for name in CARD_NAMES:
        js = FILES[name]
        for line in lines:
            assert js.count(line) == 1, (name, line)
        take = js.index("} = globalThis.glucocraftShared;")
        assert js.index("await GC_SHARED;") < take < js.index("customElements.define")
    # the module itself: one instance, defined once (a second load keeps the first)
    assert SHARED.count("if (!globalThis.glucocraftShared) {") == 1
    assert re.search(r'^const GCSHARED_VERSION = "\d+\.\d+\.\d+";$', SHARED, re.MULTILINE)


# ------------------------------------------------------------------------------------------------ the numbers
@needs_node
def test_the_rounding_is_units_round_half_up_once_and_never_negative_zero():
    rng = random.Random(20261029)
    values = [0.05, 0.15, 0.25, 1.005, 2.675, 69.45, 69.95, 69.96, 70.04, 112.6 / 18.016, -0.04, -0.05, -0.25, -2.5,
              -0.0, 0.0, 14.4, 99.95, 337.8 / 18.016]  # fmt: skip
    values += [round(rng.uniform(-20, 120), 4) for _ in range(400)] + [rng.uniform(-5, 105) for _ in range(400)]
    got = _eval(
        f"const v = {json.dumps(values)}; return v.map((x) => [S.round(x), S.round(x, 1), S.round(x, 2), S.nf(x, 0), "
        "S.nf(x, 1), S.nf(x, 2)]);"
    )
    for x, (r0, r1, r2, t0, t1, t2) in zip(values, got, strict=True):
        assert [r0, r1, r2] == [units.round_half_up(x, d) for d in (0, 1, 2)], x
        assert [t0, t1, t2] == [f"{units.round_half_up(x, d):.{d}f}" for d in (0, 1, 2)], x
        assert not any(t.startswith("-0") and float(t) == 0 for t in (t0, t1, t2)), x  # no "-0", "-0.0"
    # the decimal separator in use
    assert _eval('S.settings.dec = ","; const out = [S.nf(-0.04, 1), S.nf(69.45, 1), S.dtx("3.9 and 10.0")]; '
                 'S.settings.dec = "."; return out;') == ["0,0", "69,5", "3,9 and 10,0"]  # fmt: skip
    # a number that names something keeps its point: a cited locator, a guideline, license or software version, a
    # release; a value next to it still takes the comma
    named = ["Table 6.2", "Recommendation 6.3a", "WCAG 2.2", "License v3.0 (AGPL-3.0)", "Apache License 2.0",
             "Version 1.1", "Liberation Sans 2.1.5", "Home Assistant 2026.11", "2026.3.1 to 2026.10"]  # fmt: skip
    got = _eval(f'S.settings.dec = ","; const out = {json.dumps(named)}.map(S.dtx).concat(S.dtx("WCAG 2.2: 4.5:1")); '
                'S.settings.dec = "."; return out;')  # fmt: skip
    assert got == [*named, "WCAG 2.2: 4,5:1"]


@needs_node
def test_the_two_decimal_rule_at_a_threshold_is_units_shown_against():
    rng = random.Random(1029)
    cases = [(69.96, 70, 1), (70.04, 70, 1), (69.996, 70, 1), (70.004, 70, 1), (69.94, 70, 1), (70.0, 70, 1),
             (69.6, 70, 0), (70.4, 70, 0), (3.96, 4, 1), (36.04, 36, 1), (69.96, [70, 50], 1), (49.97, [70, 50], 1),
             (24.999, 25, 1), (0.96, 1, 1)]  # fmt: skip
    for rule in (1, 4, 5, 25, 36, 50, 70):
        cases += [(rule + rng.uniform(-0.06, 0.06), rule, d) for d in (0, 1) for _ in range(60)]
    got = _eval(f"return {json.dumps(cases)}.map(([v, r, d]) => [S.shownAgainst(v, r, d), S.nfAt(v, r, d)]);")
    for (v, r, d), ((shown, places), text) in zip(cases, got, strict=True):
        assert (shown, places) == units.shown_against(v, r, d), (v, r, d)
        assert text == f"{shown:.{places}f}", (v, r, d)
    # the shown number is never the rule's value on the wrong side of it
    assert _eval("return [S.nfAt(69.96, 70), S.nfAt(69.996, 70), S.nfAt(70.004, 70), S.nfAt(69.6, 70, 0)];") == [
        "69.96", "69.99", "70.01", "69.60"]  # fmt: skip


@needs_node
def test_the_percentages_of_a_glu2_file_round_as_the_backend_rounds_them():
    """panel_feed._pct32 writes each percentage as the float32 that rounds as the unrounded value; the cards round that
    float32 once, without an epsilon of their own, to the backend's whole number and one decimal."""
    rng = random.Random(32)
    values = [69.45, 69.5, 69.55, 70.05, 4.05, 0.45, 99.95, 36.45] + [rng.uniform(0, 100) for _ in range(3000)]
    values += [k / 20 for k in range(1, 2000, 2)]  # every half of a tenth: 0.05, 0.15, ... 99.95
    f32 = [struct.unpack("<f", struct.pack("<f", pf._pct32(v)))[0] for v in values]
    got = _eval(f"return {json.dumps(f32)}.map((x) => [S.nf(x, 0), S.nf(x, 1)]);")
    for v, (whole, tenth) in zip(values, got, strict=True):
        assert whole == f"{units.round_half_up(v):.0f}" and tenth == f"{units.round_half_up(v, 1):.1f}", v


@needs_node
def test_the_glucose_units_match_units_py():
    expected = {str(k): v for k, v in units.CONSENSUS_MMOLL.items()}
    assert _eval("return S.CONS_MMOLL;") == expected and _eval("return S.MGDL_PER_MMOLL;") == units.MGDL_PER_MMOLL
    # the display unit: mg/dL whole numbers, mmol/L one decimal, the published thresholds as published
    shown = _eval(
        "const out = []; for (const mmol of [false, true]) { S.settings.mmol = mmol; "
        "out.push([70, 140, 140.5, 120.5, 112.6, 337.8, null].map((mg) => S.gF(mg))); } S.settings.mmol = false; return out;"
    )
    assert shown == [
        ["70", "140", "141", "121", "113", "338", "---"],
        ["3.9", "7.8", "7.8", "6.7", "6.3", "18.8", "---"],
    ]
    for mg in (140.5, 120.5, 112.6, 337.8):
        assert units.fmt(mg, units.UNIT_MMOLL) == shown[1][[70, 140, 140.5, 120.5, 112.6, 337.8].index(mg)]


def _r1_mmol(mg32: float, eps: float) -> float:
    """Python model of the panels' gc_units.h r1_mmol and of the module's rh1: the file's float32 value in double,
    mg/dL to mmol/L, one decimal, rounded once, halves away from zero (eps: R1_FLOAT_EPS, the float32 error of a file
    value)."""
    x = abs(mg32) / units.MGDL_PER_MMOLL * 10.0
    return math.copysign(math.floor(x + 0.5 + eps), mg32) / 10.0


@needs_node
def test_rh1_is_the_panels_r1_mmol():
    """The module's rh1 is the arithmetic of the panels' r1_mmol: the same epsilon, the same result for every
    stored tenth and for unrounded means."""
    eps = float(re.search(r"Math\.floor\(Math\.abs\(x\) \* 10 \+ 0\.5 \+ ([0-9.e-]+)\) / 10", SHARED).group(1))
    if PANELS is not None:
        h = (PANELS / "gc_units.h").read_text(encoding="utf-8")
        assert eps == float(re.search(r"constexpr double R1_FLOAT_EPS = ([0-9.e+-]+);", h).group(1))
    rng = random.Random(7)
    mg = [struct.unpack("<f", struct.pack("<f", v))[0] for v in [*(t / 10 for t in range(400, 5001, 7)), 112.6, 337.8]]
    mg += [struct.unpack("<f", struct.pack("<f", rng.uniform(5, 400)))[0] for _ in range(2000)]
    got = _eval(f"return {json.dumps(mg)}.map((v) => S.rh1(v / S.MGDL_PER_MMOLL));")
    assert got == [_r1_mmol(v, eps) for v in mg]
    assert _eval("return [S.rh1(Math.fround(112.6) / S.MGDL_PER_MMOLL), S.rh1(Math.fround(337.8) / S.MGDL_PER_MMOLL)];") == [
        6.3, 18.8]  # fmt: skip


@pytest.mark.skipif(PANELS is None, reason=NO_PANELS)
def test_the_panels_mmol_rounding_matches_units_py():
    """A mean or SD in mmol/L on a panel equals GlucoCraft's units.from_mgdl: rounded once, not first to 0.1 mg/dL
    (that gave a different last digit for about 1.3 % of the means); the exact halves 112.6 and 337.8 mg/dL (6.25 and
    18.75 mmol/L) round up from the file's float32, as the backend rounds them from its double."""
    h = (PANELS / "gc_units.h").read_text(encoding="utf-8")
    a = h.index("inline float r1_mmol(float mg) {")
    body = h[a : h.index("\n}\n", a)]
    assert "const double x = fabs((double) mg) / MGDL_PER_MMOLL_D * 10.0;" in body
    assert "floor(x + 0.5 + R1_FLOAT_EPS)" in body and "mg10" not in body
    assert "constexpr double MGDL_PER_MMOLL_D = 18.016;" in h and units.MGDL_PER_MMOLL == 18.016
    eps = float(re.search(r"constexpr double R1_FLOAT_EPS = ([0-9.e+-]+);", h).group(1))

    def f32(v: float) -> float:
        return struct.unpack("<f", struct.pack("<f", v))[0]

    for mg in (112.6, 337.8):
        assert _r1_mmol(f32(mg), eps) == units.from_mgdl(mg, units.UNIT_MMOLL), mg
    for tenth in range(400, 5001):  # every stored reading, minimum and maximum: tenths of a mg/dL, 40.0 to 500.0
        mg = tenth / 10
        if mg.is_integer() and int(mg) in units.CONSENSUS_MMOLL:
            continue  # the published thresholds (disp() maps them first)
        assert _r1_mmol(f32(mg), eps) == units.from_mgdl(mg, units.UNIT_MMOLL), mg
    rng = random.Random(20261029)
    differ = 0
    for _ in range(200_000):  # unrounded means and SDs
        mg = rng.uniform(5.0, 400.0)
        if _r1_mmol(f32(mg), eps) != units.from_mgdl(mg, units.UNIT_MMOLL):
            differ += 1
            # only within the float32 error of a half
            assert abs((mg / units.MGDL_PER_MMOLL * 10.0) % 1.0 - 0.5) < 2 * eps, mg
    assert differ <= 5, differ


def _rhs(js: str, name: str) -> str:
    """Return the right-hand side of the one-line constant name (exactly one must exist)."""
    lines = [ln for ln in js.splitlines() if ln.startswith(f"const {name} = ")]
    assert len(lines) == 1, (name, len(lines))
    return lines[0].split(" = ", 1)[1]


def test_citation_labels_are_one_copy():
    # the ADA Standards' author-date labels: the report page's RADA and the strategy's ADA_A / ADA_B
    rada = re.search(r"const RADA = \{ ada_a: (\"[^\"]*\"),\s+ada_b: (\"[^\"]*\") \};", FILES["report"])
    assert rada
    assert rada.group(1) + ";" == _rhs(FILES["strategy"], "ADA_A")
    assert rada.group(2) + ";" == _rhs(FILES["strategy"], "ADA_B")


# ------------------------------------------------------------------------------------------------ the GLU2 parser
@pytest.fixture
def glu2_file(make_coordinator, storage) -> bytes:
    """Return a full GLU2 v3 file (with weekly values) as panel_feed.py builds it for the panels and the cards."""
    end = int(time.time()) - 30
    storage._readings = [GlucoseReading(end - (199 - i) * 60, float(80 + i % 90)) for i in range(200)]
    stats = {k: stats_record(period_seconds=s, mean=120.0 + i, gmi=6.4) for i, (k, s) in enumerate(STAT_PERIODS)}
    c = make_coordinator(storage=storage, period_stats=stats, trend_weeks=[stats_record()] * 5, trend_gmi14=[6.5] * 5)
    data = pf.build_glu2(c, delta=False)
    assert data is not None
    return data


def _parse(raw: bytes, result: str) -> object:
    """Parse a GLU2 file with the module's parser (as f) and return the expression result."""
    b64 = base64.b64encode(raw).decode("ascii")
    return _eval(
        f"const buf = Uint8Array.from(atob('{b64}'), (ch) => ch.charCodeAt(0)).buffer; "
        f"const f = S.parseGlu2(buf); return {result};"
    )


@needs_node
def test_one_download_of_each_file_for_every_card_on_the_page(glu2_file):
    """gcFile: the display card and the dashboard cards share each download; force downloads again; a failed
    recent-readings file is null, a failed full file keeps the last good one."""
    b64 = base64.b64encode(glu2_file).decode("ascii")
    got = _node(
        f"""
        const body = Uint8Array.from(atob('{b64}'), (ch) => ch.charCodeAt(0)).buffer;
        let calls = 0, ok = true;
        const hass = {{ fetchWithAuth: async () => {{ calls++; return ok ? {{ ok: true, arrayBuffer: async () => body }}
                                                                       : {{ ok: false, status: 500 }}; }} }};
        (async () => {{
          const [a, b] = await Promise.all([S.gcFile(hass, "backfill"), S.gcFile(hass, "backfill")]);
          const again = await S.gcFile(hass, "backfill");
          const one = calls;
          ok = false;
          const kept = await S.gcFile(hass, "backfill", {{ force: true }});
          const delta = await S.gcFile(hass, "delta");
          process.stdout.write("\u0001" + JSON.stringify([one, a === b && b === again, calls, kept === a, delta,
                                                               globalThis.__glucocraftFiles.failed.delta]));
        }})();
    """
    )
    assert got == [1, True, 3, True, None, "error"]


@needs_node
def test_the_glu2_parser_reads_what_the_file_holds(glu2_file):
    keys = "['mean', 'sd', 'cv', 'gmi', 'tir', 'titr', 'low', 'high', 'clo', 'chi']"
    got = _parse(glu2_file, f"{{ stats: f.stats.map((s) => {keys}.map((k) => s[k])), ts: Array.from(f.ts), "
                            "val: Array.from(f.val), trends: f.trends.length }")  # fmt: skip
    # the header offsets as the firmware reads them
    _, _, trend_count, stats_off, _, readings_off, n = struct.unpack_from("<IHHIIII", glu2_file, 0)
    readings = [struct.unpack_from("<IH", glu2_file, readings_off + i * 6) for i in range(n)]
    assert got["ts"] == [t for t, _ in readings] and got["val"] == [v for _, v in readings]
    assert got["trends"] == trend_count == 5
    for p in range(7):
        floats = struct.unpack_from("<10f", glu2_file, stats_off + p * 44)
        assert got["stats"][p] == pytest.approx(list(floats), rel=1e-6)


@needs_node
def test_the_parser_reads_the_description_lines_in_utf8(make_coordinator, storage, monkeypatch):
    """The weekly pages' description lines are UTF-8 (a translated text, the markers of the test language)."""
    line = "Glüçöšé «70–180 mg/dL» ≤36%"
    orig = pf._range_set
    monkeypatch.setattr(pf, "_range_set", lambda c, custom: (orig(c, custom)[0], [line] + orig(c, custom)[1][1:]))
    end = int(time.time()) - 30
    storage._readings = [GlucoseReading(end - (199 - i) * 60, 100.0) for i in range(200)]
    c = make_coordinator(storage=storage, trend_weeks=[stats_record()] * 5, trend_gmi14=[6.5] * 5)
    descs = _parse(pf.build_glu2(c, delta=False), "f.std.descs")
    assert descs[0] == line and len(descs) == 9


@needs_node
def test_the_parser_reads_the_unrounded_minimum_and_maximum(make_coordinator, storage):
    end = int(time.time()) - 30
    storage._readings = [GlucoseReading(end - (29 - i) * 60, 100.0) for i in range(30)]
    stats = {k: stats_record(period_seconds=s, min_value=69.4, max_value=250.45) for k, s in STAT_PERIODS}
    data = pf.build_glu2(make_coordinator(storage=storage, period_stats=stats), delta=True)
    trailer = 8 + struct.unpack_from("<H", data, len(data) - 6)[0]
    without = data[: len(data) - trailer - pf.GCM1_SIZE] + data[len(data) - trailer :]  # a file from before GCM1
    extremes = "f.stats.map((s) => [s.min, s.max])"
    assert [v for pair in _parse(data, extremes) for v in pair] == pytest.approx([69.4, 250.45] * 7, abs=1e-4)
    assert _parse(without, extremes) == [[69, 250]] * 7


# ------------------------------------------------------------------------------------------------ loading
LOADER_STUBS = """
globalThis.window = globalThis;
globalThis.HTMLElement = class { attachShadow() { this.shadowRoot = { innerHTML: "", querySelector: () => null,
  querySelectorAll: () => [] }; return this.shadowRoot; } };
const defined = [];
globalThis.customElements = { get: (n) => defined.includes(n), define: (n) => { defined.push(n); },
  whenDefined: () => Promise.resolve() };
globalThis.ResizeObserver = class { observe() {} disconnect() {} };
globalThis.document = { createElement: () => ({ getContext: () => ({ measureText: (t) => ({ width: t.length * 8 }) }) }),
  querySelector: () => null, documentElement: { lang: "en" } };
globalThis.fetch = async () => ({ ok: false, json: async () => ({}) });   // no catalogue in node: English keys only
"""
ELEMENTS = {
    "display-card": ["glucocraft-display-card", "glucocraft-display-card-wide"],
    "ui": ["glucocraft-banners", "glucocraft-statistics", "glucocraft-pages", "glucocraft-footer"],
    "docs": ["glucocraft-documentation"],
    "strategy": ["ll-strategy-dashboard-glucocraft"],
    "report": ["glucocraft-report"],
}


@needs_node
@pytest.mark.parametrize("name", CARD_NAMES)
def test_each_card_file_loads_on_its_own_with_the_language_and_shared_modules(name, tmp_path):
    """A card added alone as a resource: the folder holds only it, glucocraft-i18n.js and glucocraft-shared.js; the card
    file imports both by their paths next to its own URL and defines its elements. Loading the shared module a second
    time (another card file, another path) keeps the first instance."""
    for f in (f"glucocraft-{name}.js", "glucocraft-i18n.js", "glucocraft-shared.js"):
        (tmp_path / f).write_text((WWW / f).read_text(encoding="utf-8"), encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    (other / "glucocraft-shared.js").write_text(SHARED, encoding="utf-8")
    card, again = (tmp_path / f"glucocraft-{name}.js").as_uri(), (other / "glucocraft-shared.js").as_uri()
    src = (
        LOADER_STUBS
        + f"await import({json.dumps(card)});\n"
        + "const first = globalThis.glucocraftShared;\n"
        + f"await import({json.dumps(again)});\n"
        + "process.stdout.write('\\u0001' + JSON.stringify({ defined, same: globalThis.glucocraftShared === first, "
        + "i18n: !!globalThis.glucocraftI18n, frozen: Object.isFrozen(first) }));\n"
    )
    got = node_json(src, prelude=False)
    assert got["same"] and got["i18n"] and got["frozen"]
    assert set(ELEMENTS[name]) <= set(got["defined"]), got["defined"]


def test_frontend_serves_the_shared_module_below_both_paths(monkeypatch):
    """frontend.py: the shared module is served next to the card files below the versioned and the unversioned path
    (the cards import it relative to their own URL), it is no Lovelace resource of its own, and the content hash covers
    it, so that a change of it is fetched under a new versioned path."""
    from glucocraft import frontend as fe

    registered: list = []

    async def register(paths):
        registered.extend(paths)

    async def lovelace(hass, base):
        return None

    async def executor(fn, *args):
        return fn(*args)

    monkeypatch.setattr(fe, "_async_register_lovelace_resources", lovelace)
    monkeypatch.setattr(fe, "StaticPathConfig", lambda url, path, cache_headers: (url, path, cache_headers))
    hass = types.SimpleNamespace(
        data={}, http=types.SimpleNamespace(async_register_static_paths=register), async_add_executor_job=executor
    )
    asyncio.run(fe.async_register_frontend(hass))
    fv = hass.data["glucocraft_frontend_version"]
    urls = {url: (path, cache) for url, path, cache in registered}
    shared = str(fe.CARDS_DIR / "glucocraft-shared.js")
    assert urls[f"{fv.base}/glucocraft-shared.js"] == (shared, True)
    assert urls[f"{fe.UNVERSIONED_URL_BASE}/glucocraft-shared.js"] == (shared, False)
    assert "glucocraft-shared.js" not in fe.CARD_FILES  # loaded by the card files, not as a resource
    assert WWW / "glucocraft-shared.js" in fe._hashed_files()


# ------------------------------------------------------------------------------------------------ no Terms helpers
def test_no_courtesy_translation_helpers_remain():
    """The disclaimer is an ordinary catalogue text: no courtesy-translation label, link or English-text lookup."""
    for name in ("gcTermsCourtesy", "gcTermsTranslated", "terms.courtesy_translation", "terms-english"):
        assert name not in SHARED, name
        for card, js in FILES.items():
            assert name not in js, (card, name)


def test_the_shared_module_version_is_listed():
    version = re.search(r'^const GCSHARED_VERSION = "(\d+\.\d+\.\d+)";$', SHARED, re.MULTILINE).group(1)
    readme = (WWW.parents[2] / "README.md").read_text(encoding="utf-8")
    assert f"| Shared module | {version} | `www/glucocraft-shared.js` |" in readme
    assert "version: GCSHARED_VERSION," in SHARED  # globalThis.glucocraftShared.version


@needs_node
def test_the_parser_reads_glu2_version_3_and_later_and_refuses_an_earlier_one(glu2_file):
    """parseGlu2 reads a file of version 3 or later by the parts it knows and ignores the rest; an earlier version is
    refused (the panels' gc_glu2_readable, the same rule)."""
    later = glu2_file[:4] + (4).to_bytes(2, "little") + glu2_file[6:]
    assert _parse(later, "[f.stats[3].mean, f.ts.length, f.trends.length]") == _parse(
        glu2_file, "[f.stats[3].mean, f.ts.length, f.trends.length]"
    )
    earlier = glu2_file[:4] + (2).to_bytes(2, "little") + glu2_file[6:]
    b64 = base64.b64encode(earlier).decode("ascii")
    refused = _eval(
        f"const buf = Uint8Array.from(atob('{b64}'), (ch) => ch.charCodeAt(0)).buffer; "
        "try { S.parseGlu2(buf); return 'read'; } catch (e) { return e.message; }"
    )
    assert refused == "not GLU2 v3"


# ------------------------------------------------------------------------------------------------ the GCR1 block
def _glu2_with_runs(runs: list[tuple[int, int, int]], *, with_gcr1: bool = True, readings: int = 3) -> bytes:
    """A small GLU2 v3 file (no weekly records): header, STATS, RANGES, readings, then the GCR1 block of the stretches
    of limit readings (uint16 count, count x (uint32 start, uint32 end, uint8 kind 1 LO / 2 HI), "GCR1") directly in
    front of the GCM1 block and the GCL2 trailer, as the contract between the integration and the displays has it."""
    stats_off, ranges_off = 40, 40 + 308
    readings_off = ranges_off + 24
    t0 = 1_790_000_000
    rows = b"".join(struct.pack("<IH", t0 + 300 * i, 100 + i) for i in range(readings))
    header = b"GLU2" + struct.pack(
        "<HHIIIIIIII", 3, 0, stats_off, ranges_off, readings_off, readings, t0, t0 + 300 * (readings - 1), 0, 0
    )
    stats = b"".join(struct.pack("<10f2H", *([0.0] * 10), 0, 0) for _ in range(7))
    ranges = struct.pack("<6f", 54, 250, 70, 180, 70, 140)
    gcr1 = struct.pack("<H", len(runs)) + b"".join(struct.pack("<IIB", *r) for r in runs) + b"GCR1"
    gcm1 = struct.pack("<14f", *([0.0] * 14)) + b"GCM1"
    gcl2 = struct.pack("<14I", *([0] * 14)) + b"GCL2"
    return header + stats + ranges + rows + (gcr1 if with_gcr1 else b"") + gcm1 + gcl2


@needs_node
@pytest.mark.parametrize("count", [0, 1, 3, 400])
def test_the_parser_reads_the_stretches_of_limit_readings(count):
    """parseGlu2 finds the GCR1 block in front of GCM1 (its start is the end of the readings here) and returns the
    stretches as [start, end, "low" | "high"]; a file without it has none, and the other blocks read as before."""
    runs = [(1_790_000_000 + 600 * i, 1_790_000_000 + 600 * i + 300, 1 + i % 2) for i in range(count)]
    got = _parse(_glu2_with_runs(runs), "[f.limitRuns, f.ts.length, f.limitSeconds.length]")
    assert got[0] == [[a, b, "low" if k == 1 else "high"] for a, b, k in runs]
    assert got[1:] == [3, 7]
    assert _parse(_glu2_with_runs([], with_gcr1=False), "f.limitRuns") == []
