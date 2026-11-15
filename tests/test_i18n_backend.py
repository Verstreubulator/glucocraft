# License: AGPL-3.0-only (LICENSE).
"""The backend's language catalogues (custom_components/glucocraft/i18n.py, i18n/<language>/backend.json and
report.json) and Home Assistant's own texts (strings.json).

Rules under test (docs/DEVELOPER_GUIDE.md section 2, "The backend's texts"):
- every key the code uses exists in English, and the placeholders of each call are exactly those of its text;
- the catalogues are flat objects of lower-case dotted keys, each a text; no key is also the prefix of another; a
  plural has its "other" form;
- a language falls back to English key by key; a regional code falls back to its base language; the development
  override GLUCOCRAFT_LANGUAGE chooses the language; a missing key shows as the key, never as an empty text;
- the plural rule: English "one" only for exactly 1 without decimals;
- strings.json and translations/en.json are identical; every entity has a translated name, every enum sensor a name
  for each state;
- frontend.py serves every catalogue below the versioned path, and the content hash changes with a catalogue;
- the PDF's fonts come from the registry per script.
English output itself: tests/test_i18n_english_output.py.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import types

import pytest
from glucocraft import i18n

from conftest import REPO

PKG = REPO / "custom_components" / "glucocraft"
EN = {area: json.loads((PKG / "i18n" / "en" / f"{area}.json").read_text(encoding="utf-8")) for area in i18n.AREAS}
ALL = {**EN["backend"], **EN["report"]}
KEY = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
WILDCARD = "[a-z0-9_.]+"  # a {...} part of a key written as an f-string


def filled_later(wanted: set[str]) -> set[str]:
    """The placeholders of a report text that report_pdf._L (and the page's rL) fills, not its caller: the thresholds
    (units.REPORT_THRESHOLDS) and, in a text that names a threshold, {unit}."""
    from glucocraft.units import REPORT_THRESHOLDS

    thresholds = wanted & set(REPORT_THRESHOLDS)
    return thresholds | ({"unit"} if thresholds else set())


# ------------------------------------------------------------------------------------------------ the calls in the code
def _calls():
    """Every t() / tp() call of the backend: (file, line, key or key pattern, keyword names or None for **kwargs, plural).

    A key written as an f-string is a pattern: each {...} part matches one or more key segments."""
    out = []
    for path in sorted(PKG.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("t", "tp")):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                key = re.escape(first.value)
            elif isinstance(first, ast.JoinedStr):
                key = "".join(re.escape(v.value) if isinstance(v, ast.Constant) else WILDCARD for v in first.values)
            elif isinstance(first, ast.IfExp) and all(
                isinstance(b, ast.Constant) for b in (first.body, first.orelse)
            ):  # t("a" if x else "b")
                for branch in (first.body, first.orelse):
                    out.append(_call(path, node, re.escape(branch.value)))
                continue
            else:
                continue
            out.append(_call(path, node, key))
    return out


def _call(path, node, key):
    names = None if any(k.arg is None for k in node.keywords) else {k.arg for k in node.keywords}
    return path.name, node.lineno, key, names, node.func.id == "tp"


CALLS = _calls()


def _matches(pattern: str, plural: bool) -> list[str]:
    rx = re.compile(pattern + (r"\.[a-z]+" if plural else "") + "$")
    return [k for k in ALL if rx.match(k)]


def test_the_code_uses_catalogue_keys():
    assert len(CALLS) > 300  # the scan finds the calls (notifications, spoken text, report, ...)


@pytest.mark.parametrize("where, line, key, names, plural", CALLS, ids=[f"{c[0]}:{c[1]}" for c in CALLS])
def test_every_key_exists_in_english_with_its_placeholders(where, line, key, names, plural):
    found = _matches(key, plural)
    assert found, f"{where}:{line}: no English text for {key}"
    if plural:
        assert any(k.endswith(".other") for k in found), f"{where}:{line}: {key} has no .other form"
    if names is None:
        return
    given = names | ({"count"} if plural else set())
    if WILDCARD in key:  # a key pattern (an f-string): one of its texts takes these values
        assert any(i18n.placeholders(ALL[k]) - filled_later(i18n.placeholders(ALL[k])) <= given for k in found), (
            f"{where}:{line}: {key}"
        )
        return
    for k in found:
        wanted = i18n.placeholders(ALL[k])
        assert wanted - filled_later(wanted) <= given, f"{where}:{line}: {k} needs {sorted(wanted - given)}"
        if not plural:  # a plural form may leave the count out ("{count}" is given anyway)
            assert given <= wanted, f"{where}:{line}: {k} does not use {sorted(given - wanted)}"


def test_no_key_is_missing_while_every_surface_is_made(monkeypatch, make_coordinator, fake_hass):
    """Make every English output of tests/test_i18n_english_output.py and record each key that has no text."""
    import test_i18n_english_output as out

    missing: set[str] = set()

    class Recording(dict):
        def get(self, key, default=None):
            if key not in self:
                missing.add(key)
            return super().get(key, default)

    i18n.load("en")
    monkeypatch.setitem(i18n._state, "texts", Recording(i18n._state["texts"]))
    out._summaries(make_coordinator, fake_hass)
    out._spoken()
    out._small(make_coordinator)
    out._panel_notices(monkeypatch)
    out._phone_test(monkeypatch)
    out._ipp_messages(monkeypatch)
    out._report(monkeypatch)  # skipped without reportlab
    assert not missing, sorted(missing)


# ------------------------------------------------------------------------------------------------ the files
@pytest.mark.parametrize("area", i18n.AREAS)
def test_catalogue_files_are_flat_texts_with_dotted_keys(area):
    data = EN[area]
    assert data and all(isinstance(v, str) and v for v in data.values())
    bad = [k for k in data if not KEY.match(k)]
    assert not bad, bad
    assert list(data) == sorted(data), "keys in alphabetical order"
    prefixes = [k for k in data if any(o.startswith(k + ".") for o in data)]
    assert not prefixes, prefixes  # a key is never also the start of another (the frontend nests them)
    singles = {k.rsplit(".", 1)[0] for k in data if k.endswith((".one", ".two", ".few", ".many", ".zero"))}
    assert all(f"{base}.other" in data for base in singles), singles
    if area == "report":
        assert all(k.startswith("report.") for k in data)


def test_backend_and_report_keys_do_not_overlap():
    assert not set(EN["backend"]) & set(EN["report"])


def test_no_contractions_and_no_empty_placeholders():
    blob = json.dumps(ALL, ensure_ascii=False)
    assert not re.search(r"\b(don't|can't|won't|isn't|aren't|it's|you're|doesn't|didn't)\b", blob, re.IGNORECASE)
    for k, v in ALL.items():
        assert "{}" not in v, k
        i18n.placeholders(v)  # a malformed brace raises


# ------------------------------------------------------------------------------------------------ loading and fallback
@pytest.fixture
def languages(tmp_path, monkeypatch):
    """A catalogue folder with English and a test language "xx" that has one backend text and one plural form."""
    for area in i18n.AREAS:
        (tmp_path / "en").mkdir(exist_ok=True)
        (tmp_path / "en" / f"{area}.json").write_text(json.dumps(EN[area]), encoding="utf-8")
    (tmp_path / "xx").mkdir()
    (tmp_path / "xx" / "backend.json").write_text(
        json.dumps({"notify.title.daily": "Tageszusammenfassung", "export.done.other": "{count} Werte"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(i18n, "I18N_DIR", tmp_path)
    yield tmp_path
    i18n.load("en")


def test_a_language_falls_back_to_english_key_by_key(languages):
    assert i18n.load("xx") == "xx"
    assert i18n.t("notify.title.daily") == "Tageszusammenfassung"
    assert i18n.t("notify.title.weekly") == "GlucoCraft Weekly Summary"  # not in xx: English
    assert i18n.t("report.period.30") == "30 Days"  # no report.json in xx: English
    assert i18n.tp("export.done", 5, folder="f", files="a", keep=1) == "5 Werte"
    assert i18n.tp("export.done", 1, folder="f", files="a", keep=1).startswith("1 reading exported")  # xx lacks .one


def test_language_codes_resolve_to_a_folder(languages):
    assert i18n.resolve("xx-YY") == "xx" and i18n.resolve("XX") == "xx"
    assert i18n.resolve("de") == "en" and i18n.resolve(None) == "en" and i18n.resolve("") == "en"


def test_a_missing_key_shows_as_the_key():
    i18n.load("en")
    assert i18n.t("notify.no_such_text") == "notify.no_such_text"


def test_placeholders_without_a_value_stay_visible():
    i18n.load("en")
    assert i18n.t("notify.period.heading") == "**{title}** (glucose in {unit}):"
    assert i18n.t("notify.period.heading", title="Rolling 24 Hours") == "**Rolling 24 Hours** (glucose in {unit}):"


def test_home_assistants_language_and_the_development_override(languages, monkeypatch):
    async def run(fn, *args):
        return fn(*args)

    hass = types.SimpleNamespace(config=types.SimpleNamespace(language="xx-YY"), async_add_executor_job=run)
    monkeypatch.delenv(i18n.OVERRIDE_ENV, raising=False)
    assert asyncio.run(i18n.async_setup(hass)) == "xx" and i18n.language() == "xx"
    monkeypatch.setenv(i18n.OVERRIDE_ENV, "en")  # e.g. GLUCOCRAFT_LANGUAGE=pseudo for the test language
    assert asyncio.run(i18n.async_setup(hass)) == "en"
    assert i18n.configured_language(types.SimpleNamespace()) == "en"


@pytest.mark.parametrize(
    "count, decimals, category", [(1, 0, "one"), (1, 1, "other"), (0, 0, "other"), (2, 0, "other"), (1.5, 1, "other")]
)
def test_the_english_plural_rule(count, decimals, category):
    assert i18n.plural_category(count, decimals, "en") == category
    assert i18n.plural_category(count, decimals, "xx") == category  # a language without a rule: the English rule


# ------------------------------------------------------------------------------------------------ Home Assistant's texts
STRINGS = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))


def test_strings_and_the_english_translation_are_identical():
    assert (PKG / "strings.json").read_bytes() == (PKG / "translations" / "en.json").read_bytes()


def test_entity_translation_keys_follow_hassfest():
    for platform, items in STRINGS["entity"].items():
        for key, item in items.items():
            assert re.fullmatch(r"[a-z0-9]+(?:[_-][a-z0-9]+)*", key), (platform, key)
            assert item["name"], (platform, key)
            for state in item.get("state", {}):
                assert re.fullmatch(r"[a-z0-9]+(?:[_-][a-z0-9]+)*", state), (key, state)
            for attr, spec in item.get("state_attributes", {}).items():
                for state in spec.get("state", {}):
                    assert re.fullmatch(r"[a-z0-9]+(?:[_-][a-z0-9]+)*", state), (key, attr, state)


def test_every_enum_sensor_names_each_of_its_states(tmp_path):
    from test_entity_ids import _entities
    from test_trend_and_configuration import settings_coordinator

    enums = []
    for platform, e in _entities(settings_coordinator(tmp_path)):
        options = e.__dict__.get("_attr_options")
        if platform != "sensor" or not isinstance(options, list):
            continue
        enums.append(e.entity_id)
        names = STRINGS["entity"]["sensor"][e.__dict__["_attr_translation_key"]].get("state", {})
        assert set(options) == set(names), e.entity_id
        assert e.native_value in options, e.entity_id
    assert sorted(enums) == [  # the enum sensors (device class ENUM)
        "sensor.glucocraft_configuration",
        "sensor.glucocraft_data_history",
        "sensor.glucocraft_sensor_expiry",
        "sensor.glucocraft_status",
    ]


# ------------------------------------------------------------------------------------------------ serving and fonts
def test_frontend_serves_the_catalogues_under_the_versioned_path(monkeypatch):
    from glucocraft import frontend as fe

    files = fe._hashed_files()
    assert any(f.parent.name == "en" and f.name == "report.json" for f in files)
    assert any(f.suffix == ".js" for f in files)
    registered = []

    async def register(paths):
        registered.extend(paths)

    async def lovelace(hass, base):
        return None

    in_executor = []

    async def executor(fn, *args):
        in_executor.append(fn)
        return fn(*args)

    monkeypatch.setattr(fe, "_async_register_lovelace_resources", lovelace)
    monkeypatch.setattr(fe, "StaticPathConfig", lambda url, path, cache_headers: (url, path, cache_headers))
    hass = types.SimpleNamespace(
        data={}, http=types.SimpleNamespace(async_register_static_paths=register), async_add_executor_job=executor
    )
    asyncio.run(fe.async_register_frontend(hass))
    fv = hass.data["glucocraft_frontend_version"]
    urls = {url: (path, cache) for url, path, cache in registered}
    assert urls[f"{fv.base}/i18n"] == (str(fe.I18N_DIR), True)
    assert urls[f"{fe.UNVERSIONED_URL_BASE}/i18n"] == (str(fe.I18N_DIR), False)
    # the hash and the file list are read in the executor (not at import), once per run
    assert in_executor == [fe._read_frontend_version]
    assert fv.base == f"/glucocraft_v/{fe._compute_content_hash()}" and "glucocraft-i18n.js" in fv.static_files
    assert all(f"{fv.base}/{card}" in urls for card in fe.CARD_FILES)
    asyncio.run(fe.async_register_frontend(hass))  # a reload: the same version, no second read
    assert in_executor == [fe._read_frontend_version] and hass.data["glucocraft_frontend_version"] is fv


def test_frontend_reads_no_files_at_import():
    """Importing frontend.py does no file I/O (the hash, the manifest and the file list are read in the
    executor when the frontend is registered)."""
    tree = ast.parse((PKG / "frontend.py").read_text(encoding="utf-8"))
    top_calls = [
        n
        for stmt in tree.body
        if isinstance(stmt, ast.Assign | ast.AnnAssign | ast.Expr)
        for n in ast.walk(stmt)
        if isinstance(n, ast.Call)
    ]
    names = {getattr(c.func, "attr", getattr(c.func, "id", "")) for c in top_calls}
    assert not names & {"read_text", "read_bytes", "glob", "rglob", "_compute_content_hash", "loads", "open"}, names


def test_the_content_hash_changes_with_a_catalogue(tmp_path, monkeypatch):
    from glucocraft import frontend as fe

    (tmp_path / "www").mkdir()
    (tmp_path / "www" / "a.js").write_text("x", encoding="utf-8")
    (tmp_path / "i18n" / "en").mkdir(parents=True)
    cat = tmp_path / "i18n" / "en" / "frontend.json"
    cat.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(fe, "PACKAGE_DIR", tmp_path)
    monkeypatch.setattr(fe, "CARDS_DIR", tmp_path / "www")
    monkeypatch.setattr(fe, "I18N_DIR", tmp_path / "i18n")
    before = fe._compute_content_hash()
    cat.write_text('{"a.b": "c"}', encoding="utf-8")
    assert fe._compute_content_hash() != before


def test_the_pdf_fonts_come_from_the_registry_per_script():
    pytest.importorskip("reportlab")
    from glucocraft import report_pdf as P

    assert P.FONTS["latin"] == ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf")
    assert all((P.FONT_DIR / file).is_file() for file in P.FONTS["latin"])  # bundled, embedded in every PDF
    P._use_fonts("en")
    assert (P.FONT, P.BOLD) == ("LiberationSans-Regular", "LiberationSans-Bold")
    P._use_fonts("pseudo")  # a language with no script of its own: Latin
    assert P.FONT == "LiberationSans-Regular"
