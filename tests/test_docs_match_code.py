# License: AGPL-3.0-only (LICENSE).
"""The documents name what the code defines (doc-drift tests).

Documents fall behind the code easily (attribute names, error keys, defaults). These tests compare the documents with
the code's own lists, so that a new or renamed entity, attribute, Repairs issue, action field, HTTP endpoint or default
fails the suite until the document is brought up to date. Every failure names the missing or wrong item and the document
to fix.

Rules under test:
- every entity GlucoCraft can create (strings.json "entity", expanded to entity ids by the rule of
  docs/ENTITIES_AND_API.md "Naming" with the periods of const.STAT_PERIODS) is named in docs/ENTITIES_AND_API.md and in
  the Documentation's Entity Names section, as a full id, a "<period>" pattern or an id ending; the rule itself gives
  exactly the entities the code creates;
- every attribute key the sensors and binary sensors publish is named in docs/ENTITIES_AND_API.md;
- every Repairs translation key the code creates is in strings.json "issues", and every one there can be created by the
  code; every issue id (fixed, or "<prefix><entity_id>") and which issues are fixable are stated in
  docs/ENTITIES_AND_API.md "Repairs issues" (the translation keys there: test_developer_docs.py; the titles in the
  Documentation: test_docs_and_settings_texts.py);
- every action and action field of services.yaml, with its options and its default, is in docs/ENTITIES_AND_API.md
  "Services";
- every HTTP view of http_api.py and every query parameter the views read is in docs/ENTITIES_AND_API.md "HTTP API";
- every default the Documentation (and docs/ENTITIES_AND_API.md "Settings") states in words equals settings.py's
  default.
"""

from __future__ import annotations

import ast
import html
import json
import re
from dataclasses import dataclass

import pytest
import yaml
from glucocraft import const, settings

from conftest import REPO, repo_doc
from test_entity_ids import _entities
from test_trend_and_configuration import settings_coordinator

PKG = REPO / "custom_components" / "glucocraft"
STRINGS = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))
DOCUMENTATION = json.loads((PKG / "i18n" / "en" / "documentation.json").read_text(encoding="utf-8"))
API_DOC = "docs/ENTITIES_AND_API.md"
IN_APP = "the Documentation (custom_components/glucocraft/i18n/en/documentation.json)"

PERIODS = [label for label, _ in const.STAT_PERIODS]
LONG_PERIODS = [label for label, seconds in const.STAT_PERIODS if seconds >= const.GMI_MIN_PERIOD_SECONDS]
# the period statistics created only for the periods of 14 days and longer (sensor.entity_groups: the GMI and the GRI)
LONG_ONLY_KEYS = {"period_gmi", "period_gri"}


# ------------------------------------------------------------------------------------------------ reading the texts
def _md_sections(text: str) -> dict[str, str]:
    """The sections of a Markdown document by their "## " title (the text before the first title under "")."""
    out, title = {"": []}, ""
    for line in text.splitlines():
        if line.startswith("## "):
            title = line[3:].strip()
            out[title] = []
        else:
            out[title].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def _section(text: str, title: str) -> str:
    """One section of docs/ENTITIES_AND_API.md, found by the start of its title."""
    found = [body for name, body in _md_sections(text).items() if name.startswith(title)]
    assert found, f"{API_DOC} has no section '## {title}'"
    return found[0]


def _md_code(text: str) -> set[str]:
    """Everything written as code (`...`) in a Markdown text."""
    return {m.strip() for m in re.findall(r"`([^`\n]+)`", text)}


def _code_words(codes: set[str]) -> set[str]:
    """Every identifier inside the code spans (so that `{"low": n, "high": n}` names low and high)."""
    return {w for code in codes for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", code)}


def _doc_value(key: str) -> str:
    """One text of the Documentation as stored (HTML; a list is joined)."""
    value = DOCUMENTATION[key]
    return "".join(value) if isinstance(value, list) else value


def _html_code(text: str) -> set[str]:
    """Everything written as <code>...</code> in a Documentation text, tags removed and then entities decoded (so
    that `&lt;period&gt;` reads as `<period>`)."""
    return {
        html.unescape(re.sub(r"<[^>]+>", "", m)).strip() for m in re.findall(r"<code>(.*?)</code>", text, re.DOTALL)
    }


def _plain(text: str) -> str:
    """A text without markup (inline tags removed, the other tags a space, then entities decoded) and with single
    spaces."""
    text = re.sub(r"</?(?:i|b|em|strong|code|a|span)(?:\s[^>]*)?>", "", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text)).replace("*", "").replace("`", "")
    return re.sub(r"\s+", " ", text)


def _all_documentation_text() -> str:
    """Every text of the Documentation as one plain text."""
    return _plain(" ".join(_doc_value(k) for k in DOCUMENTATION))


# ------------------------------------------------------------------------------------------------ 1. entities
@dataclass(frozen=True)
class Entity:
    """An entity GlucoCraft can create, as the naming rule gives it."""

    entity_id: str
    platform: str
    period: str | None  # the period of a period statistic ("14d"), else None
    ending: str  # the id after "glucocraft_" and the period ("mean", "glucose_very_low")
    key: str  # its translation key in strings.json


def _object_id(name: str) -> str:
    """The naming rule of docs/ENTITIES_AND_API.md: the name in lower case, every other character an underscore."""
    return re.sub(r"[^a-z0-9]", "_", name.lower())


def defined_entities() -> list[Entity]:
    """Every entity of strings.json "entity", each "{period}" name once per period (the GMI and the GRI only for the
    periods of 14 days and longer)."""
    out = []
    for platform in ("sensor", "binary_sensor"):
        for key, entry in STRINGS["entity"][platform].items():
            name = entry["name"]
            if "{period}" not in name:
                oid = _object_id(name)
                out.append(Entity(f"{platform}.glucocraft_{oid}", platform, None, oid, key))
                continue
            ending = _object_id(name.replace("{period}", "").strip())
            for period in LONG_PERIODS if key in LONG_ONLY_KEYS else PERIODS:
                oid = _object_id(name.format(period=period.upper()))
                out.append(Entity(f"{platform}.glucocraft_{oid}", platform, period, ending, key))
    return out


ENTITIES = defined_entities()


def test_the_naming_rule_gives_the_entities_the_code_creates(tmp_path):
    """The entity list these tests check (strings.json expanded by the documented naming rule and period rules) is
    exactly the set of entities the code creates, so that the rule in docs/ENTITIES_AND_API.md "Naming" and
    "Statistics per period" is the code's rule."""
    created = {e.entity_id for _, e in _entities(settings_coordinator(tmp_path))}
    derived = {e.entity_id for e in ENTITIES}
    assert derived == created, (
        f"the naming rule of {API_DOC} (Naming, Statistics per period) no longer gives the entities the code creates: "
        f"not created {sorted(derived - created)}, not given by the rule {sorted(created - derived)}"
    )


_PERIOD_TOKENS = ("<period>", "<p>", "<P>")


def _entity_documented(e: Entity, text: str, codes: set[str], period_codes: set[str]) -> bool:
    """Whether a document names an entity: its full id; for a period statistic a "<period>" pattern of it, or a generic
    "<platform>.glucocraft_<period>_..." pattern with the statistic's ending named as code (`mean` or `_mean`) in the
    period part (period_codes); otherwise its id ending written as code (`_glucose_very_low`)."""
    if re.search(rf"(?<![\w.]){re.escape(e.entity_id)}(?!\w)", text):
        return True
    if e.period is None:
        return f"_{e.ending}" in codes
    patterns = {f"{e.platform}.glucocraft_{tok}_{e.ending}" for tok in _PERIOD_TOKENS}
    if patterns & codes:
        return True
    generic = any(re.match(rf"{re.escape(e.platform)}\.glucocraft_(?:<period>|<p>|<P>)_", c) for c in codes)
    return generic and bool({e.ending, f"_{e.ending}"} & period_codes)


def test_every_entity_is_in_entities_and_api():
    """docs/ENTITIES_AND_API.md names every entity GlucoCraft can create (a period statistic by the
    `sensor.glucocraft_<period>_<metric>` pattern with its metric named in "Statistics per period")."""
    doc = repo_doc("ENTITIES_AND_API.md")
    codes = _md_code(doc)
    period_codes = _md_code(_section(doc, "Statistics per period"))
    missing = sorted(e.entity_id for e in ENTITIES if not _entity_documented(e, doc, codes, period_codes))
    assert not missing, f"{API_DOC} does not name these entities: {missing}"


def test_every_entity_is_in_the_documentations_entity_names():
    """The Documentation's Entity Names section (docs.section.entities.body) names every entity GlucoCraft can create,
    as a full id, a `<period>` pattern or an id ending such as `_standard_deviation`."""
    body = _doc_value("docs.section.entities.body")
    codes = _html_code(body)
    missing = sorted(e.entity_id for e in ENTITIES if not _entity_documented(e, body, codes, codes))
    assert not missing, f"{IN_APP}, docs.section.entities.body, does not name these entities: {missing}"


def test_the_periods_of_the_statistics_are_stated():
    """Both documents list the periods of const.STAT_PERIODS, and the periods of the GMI and the GRI (14 days and
    longer, const.GMI_MIN_PERIOD_SECONDS)."""
    statistics = _plain(_section(repo_doc("ENTITIES_AND_API.md"), "Statistics per period"))
    listed, long_days = ", ".join(PERIODS), const.GMI_MIN_PERIOD_SECONDS // 86400
    assert f"periods {listed}" in statistics, f"{API_DOC} Statistics per period: the periods are not '{listed}'"
    assert f"({long_days} days and longer) gmi and gri" in statistics, (
        f"{API_DOC} Statistics per period: the GMI and GRI periods are not '{long_days} days and longer'"
    )
    body = _plain(_doc_value("docs.section.entities.body"))
    long_listed = ", ".join(LONG_PERIODS[:-1]) + f" and {LONG_PERIODS[-1]}"
    assert f"({listed})" in body, f"{IN_APP}, docs.section.entities.body: the periods are not '{listed}'"
    assert f"only for {long_listed}, _gmi and _gri" in body, (
        f"{IN_APP}, docs.section.entities.body: the GMI and GRI periods are not '{long_listed}'"
    )


# ------------------------------------------------------------------------------------------------ 2. attributes
def _keys_in(node: ast.AST) -> set[str]:
    """The string keys of every dict literal in a function, and of every `name["key"] = ...` it writes."""
    keys = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Dict):
            keys |= {k.value for k in sub.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        elif (
            isinstance(sub, ast.Subscript)
            and isinstance(sub.ctx, ast.Store)
            and isinstance(sub.slice, ast.Constant)
            and isinstance(sub.slice.value, str)
        ):
            keys.add(sub.slice.value)
    return keys


def _published_attribute_keys() -> dict[str, set[str]]:
    """Every attribute key the code publishes, by the file it is in: the dict literals of each
    extra_state_attributes of sensor.py and binary_sensor.py and of the module functions they call (_coverage), of
    coordinator.configuration (the Configuration sensor's attributes) and the keys configuration() returns when run
    (the regional keys and their "_setting" forms, which it builds from other modules)."""
    out: dict[str, set[str]] = {}
    for name in ("sensor.py", "binary_sensor.py"):
        tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
        functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        keys: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "extra_state_attributes":
                keys |= _keys_in(node)
                for call in ast.walk(node):
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in functions:
                        keys |= _keys_in(functions[call.func.id])
        out[name] = keys
    tree = ast.parse((PKG / "coordinator.py").read_text(encoding="utf-8"))
    configuration = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "configuration")
    out["coordinator.py configuration()"] = _keys_in(configuration)
    return out


def _range_names(doc: str) -> set[str]:
    """The keys a document names as a range of periods (`stats_6h` … `stats_180d`)."""
    first, last = PERIODS[0], PERIODS[-1]
    found = re.findall(rf"`([a-z_]+)_{first}` … `\1_{last}`", doc)
    return {f"{prefix}_{p}" for prefix in found for p in PERIODS}


# Attribute keys deliberately not named in docs/ENTITIES_AND_API.md, each with its reason.
UNDOCUMENTED_ATTRIBUTES: dict[str, str] = {}


def test_every_attribute_key_is_in_entities_and_api(tmp_path):
    """docs/ENTITIES_AND_API.md names every attribute key GlucoCraft's sensors and binary sensors publish (written as
    code in the sections before "Services": the entity tables, Settings, Weekly trends, Change Over Time)."""
    doc = repo_doc("ENTITIES_AND_API.md")
    entity_part = doc.split("\n## Services", 1)[0]
    named = _code_words(_md_code(entity_part)) | _range_names(entity_part)
    published = _published_attribute_keys()
    published["coordinator.py configuration() (run)"] = set(settings_coordinator(tmp_path).configuration())
    missing = {source: sorted(keys - named - set(UNDOCUMENTED_ATTRIBUTES)) for source, keys in published.items()}
    missing = {source: keys for source, keys in missing.items() if keys}
    assert not missing, f"{API_DOC} does not name these attribute keys (by the file that publishes them): {missing}"


def test_the_allow_list_of_undocumented_attributes_is_current():
    """Every allow-listed attribute key is still published and still not documented (else the entry is removed)."""
    published = set().union(*_published_attribute_keys().values())
    named = _code_words(_md_code(repo_doc("ENTITIES_AND_API.md").split("\n## Services", 1)[0]))
    for key in UNDOCUMENTED_ATTRIBUTES:
        assert key in published and key not in named, f"remove {key!r} from UNDOCUMENTED_ATTRIBUTES"


# ------------------------------------------------------------------------------------------------ 3. Repairs issues
def _package_trees() -> dict[str, ast.Module]:
    """Every Python file of the integration, parsed."""
    return {p.name: ast.parse(p.read_text(encoding="utf-8")) for p in sorted(PKG.glob("*.py"))}


TREES = _package_trees()


def _module_constants() -> dict[str, object]:
    """Every module-level NAME = <literal> of the integration (strings, tuples and dicts of strings)."""
    out: dict[str, object] = {}
    for tree in TREES.values():
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                try:
                    out[node.targets[0].id] = ast.literal_eval(node.value)
                except ValueError:
                    continue
    return out


CONSTANTS = _module_constants()


ENTITY = "<entity_id>"  # how the documents write the entity id part of a per-source issue id


def _values(node: ast.AST | None, env: dict, assigns: dict, depth: int = 0) -> list[str]:
    """The strings an expression can be: a literal, a module constant, a loop variable of a literal tuple (env), a
    local variable by its assignments in the function, either branch of `a if c else b`, and an f-string with each
    part resolved (a part that cannot be resolved, such as the source's entity id, is written ENTITY)."""
    if node is None or depth > 4:
        return []
    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else []
    if isinstance(node, ast.IfExp):
        return _values(node.body, env, assigns, depth + 1) + _values(node.orelse, env, assigns, depth + 1)
    if isinstance(node, ast.Name):
        if node.id in env:
            return _values(env[node.id], {}, assigns, depth + 1)
        if isinstance(CONSTANTS.get(node.id), str):
            return [CONSTANTS[node.id]]
        return [v for expr in assigns.get(node.id, []) for v in _values(expr, env, assigns, depth + 1)]
    if isinstance(node, ast.JoinedStr):
        out = [""]
        for part in node.values:
            if isinstance(part, ast.Constant):
                out = [o + part.value for o in out]
            else:
                out = [o + v for o in out for v in (_values(part.value, env, assigns, depth + 1) or [ENTITY])]
        return out
    return []


@dataclass(frozen=True)
class CreatedIssue:
    """A Repairs issue a call of the code can create."""

    issue_id: str  # a fixed id, or "<prefix><entity_id>"
    key: str  # the translation key in strings.json "issues"
    fixable: bool


def _created_issues() -> list[CreatedIssue]:
    """Every issue an ir.async_create_issue(hass, DOMAIN, issue_id, ..., translation_key=...) call or a
    _source_issue(issue_id, show, key, placeholders) call (source_notices.py) can create, as far as its id and key can
    be resolved from the code (a key built from a value at run time, f"storage_{problem}", cannot)."""
    out = set()
    for tree in TREES.values():
        functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        home, loops = {}, {}  # call -> its innermost function / the bindings of its innermost literal loop
        for func in functions:  # ast.walk is outer before inner: an inner function or loop overwrites
            for node in ast.walk(func):
                home[id(node)] = func
            for loop in (n for n in ast.walk(func) if isinstance(n, ast.For)):
                names = [t.id for t in getattr(loop.target, "elts", []) if isinstance(t, ast.Name)]
                if names and isinstance(loop.iter, (ast.Tuple, ast.List)):
                    envs = [dict(zip(names, e.elts, strict=True)) for e in loop.iter.elts if isinstance(e, ast.Tuple)]
                    for node in ast.walk(loop):
                        loops[id(node)] = envs
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
            kwargs = {k.arg: k.value for k in call.keywords}
            if name == "async_create_issue" and len(call.args) >= 3:
                id_node, key_node = call.args[2], kwargs.get("translation_key")
                fixable = isinstance(kwargs.get("is_fixable"), ast.Constant) and kwargs["is_fixable"].value is True
            elif name == "_source_issue" and len(call.args) >= 3:
                id_node, key_node, fixable = call.args[0], call.args[2], False
            else:
                continue
            func = home.get(id(call))
            assigns: dict[str, list] = {}
            for node in ast.walk(func) if func else ():
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            assigns.setdefault(target.id, []).append(node.value)
            for env in loops.get(id(call), [{}]):
                for issue_id in _values(id_node, env, assigns):
                    for key in _values(key_node, env, assigns):
                        if ENTITY not in key:
                            out.add(CreatedIssue(issue_id, key, fixable))
    return sorted(out, key=lambda i: (i.key, i.issue_id))


def _producible(key: str) -> bool:
    """Whether the code can produce a translation key: a string literal of it, or an f-string that starts with a
    literal part and matches it (f"storage_{problem}")."""
    for tree in TREES.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == key:
                return True
            if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
                pattern = "".join(
                    re.escape(v.value) if isinstance(v, ast.Constant) else r"[a-z0-9_]+" for v in node.values
                )
                if re.fullmatch(pattern, key):
                    return True
    return False


def test_every_issue_the_code_creates_has_its_translation():
    """Every Repairs issue the code creates has its texts in strings.json "issues" (Home Assistant shows the key
    alone without them), and every issue there can still be created by the code (no text, and no document entry, of an
    issue that no longer exists)."""
    issues = set(STRINGS["issues"])
    created = {i.key for i in _created_issues()}
    assert len(created) >= 20  # the extraction still finds the code's calls
    assert not created - issues, f"strings.json 'issues' has no texts for {sorted(created - issues)}"
    dead = sorted(k for k in issues if not _producible(k))
    assert not dead, f"strings.json 'issues' (and {API_DOC}) list issues the code never creates: {dead}"


def _issue_ids() -> tuple[set[str], set[str]]:
    """The fixed issue ids (the *_ISSUE_ID constants and the values of *_ISSUE_IDS) and the issue-id prefixes of the
    per-source issues (the *_ISSUE_PREFIX constants)."""
    fixed, prefixes = set(), set()
    for name, value in CONSTANTS.items():
        if name.endswith("_ISSUE_ID") and isinstance(value, str):
            fixed.add(value)
        elif name.endswith("_ISSUE_IDS") and isinstance(value, dict):
            fixed |= {v for v in value.values() if isinstance(v, str)}
        elif name.endswith("_ISSUE_PREFIX") and isinstance(value, str):
            prefixes.add(value)
    return fixed, prefixes


def test_every_issue_id_is_in_entities_and_api():
    """docs/ENTITIES_AND_API.md "Repairs issues" states every issue id the code uses: a fixed id as `<id>`, a
    per-source id as `<prefix><entity_id>` (the diagnostics and the Maintenance status name the ids)."""
    codes = _md_code(_section(repo_doc("ENTITIES_AND_API.md"), "Repairs issues"))
    fixed, prefixes = _issue_ids()
    assert len(fixed) >= 8 and len(prefixes) >= 10  # the constants are still found
    missing = sorted(i for i in fixed if i not in codes) + sorted(
        f"{p}<entity_id>" for p in prefixes if f"{p}<entity_id>" not in codes
    )
    assert not missing, f"{API_DOC} Repairs issues does not state these issue ids: {missing}"


def _bullets(section: str) -> list[str]:
    """The list items of a Markdown section, each with its continuation lines."""
    items: list[str] = []
    for line in section.splitlines():
        if line.startswith("- "):
            items.append(line)
        elif items and line.startswith("  "):
            items[-1] += " " + line.strip()
    return items


def test_each_issue_is_documented_with_its_own_issue_id():
    """In docs/ENTITIES_AND_API.md "Repairs issues", the entry of each translation key states the issue id the code
    creates it under (`sensor_cap` with `sensor_cap_<entity_id>`, `storage_newer` with `storage_problem`); an issue
    whose id is its key needs no separate id."""
    items = _bullets(_section(repo_doc("ENTITIES_AND_API.md"), "Repairs issues"))
    created = _created_issues()
    assert len({i.issue_id for i in created}) >= 20  # the ids are still resolved
    problems = []
    for issue in created:
        # the entry that leads with the key (before its first parenthesis or colon), else any entry that names it
        heads = [item for item in items if issue.key in _md_code(re.split(r" \(|:", item, maxsplit=1)[0])]
        entries = heads or [item for item in items if issue.key in _md_code(item)]
        if not entries:
            problems.append(f"no entry for {issue.key}")
        elif issue.issue_id != issue.key and not any(issue.issue_id in _md_code(e) for e in entries):
            problems.append(f"{issue.key}: issue id {issue.issue_id}")
    assert not problems, f"{API_DOC} Repairs issues does not state: {sorted(set(problems))}"


def test_the_fixable_issues_are_marked_in_entities_and_api():
    """docs/ENTITIES_AND_API.md marks exactly the issues the code creates with is_fixable=True as "(fixable)"."""
    section = _section(repo_doc("ENTITIES_AND_API.md"), "Repairs issues")
    marked = set(re.findall(r"`([a-z_]+)`(?: \([^)]*\))? \(fixable\)", section))
    fixable = {i.key for i in _created_issues() if i.fixable}
    assert fixable, "no fixable issue found in the code"
    assert marked == fixable, (
        f"{API_DOC} Repairs issues: mark as (fixable) {sorted(fixable - marked)}, "
        f"remove (fixable) from {sorted(marked - fixable)}"
    )


# ------------------------------------------------------------------------------------------------ 4. actions
SERVICES = yaml.safe_load((PKG / "services.yaml").read_text(encoding="utf-8"))


def _service_rows(section: str, service: str) -> list[str]:
    """The table rows of the Services section that name an action (`glucocraft.<name>` or `<name>`)."""
    return [
        row
        for row in section.splitlines()
        if row.startswith("|") and ({f"glucocraft.{service}", service} & _md_code(row.split("|")[1]))
    ]


def test_every_action_and_field_is_in_entities_and_api():
    """docs/ENTITIES_AND_API.md "Services" has a row for every action of services.yaml, and names each of its fields
    in that row."""
    section = _section(repo_doc("ENTITIES_AND_API.md"), "Services")
    problems = []
    for service, spec in SERVICES.items():
        rows = _service_rows(section, service)
        if not rows:
            problems.append(f"no row for glucocraft.{service}")
            continue
        named = _code_words(_md_code(" ".join(rows)))
        problems += [f"glucocraft.{service}: field {f}" for f in (spec.get("fields") or {}) if f not in named]
    assert not problems, f"{API_DOC} Services is missing: {problems}"


def _stated_default(row: str, field: str) -> str | None:
    """The first default stated after a field's first mention in its row ("default 14", "both on by default")."""
    plain = row.replace("`", "")
    mention = re.search(rf"(?<![\w.]){re.escape(field)}(?!\w)", plain)
    m = mention and re.compile(r"default (\S+?)[,;)]|(on|off) by default").search(plain, mention.end())
    if not m:
        return None
    return m.group(1) or m.group(2)


def test_the_action_fields_options_and_defaults_are_stated():
    """For every action field with options or a default in services.yaml, the action's row in docs/ENTITIES_AND_API.md
    names each option and states the default ("default 14"; a switch "on by default" / "off by default")."""
    section = _section(repo_doc("ENTITIES_AND_API.md"), "Services")
    problems = []
    for service, spec in SERVICES.items():
        row = " ".join(_service_rows(section, service))
        for field, fspec in (spec.get("fields") or {}).items():
            options = ((fspec.get("selector") or {}).get("select") or {}).get("options") or []
            plain = row.replace("`", "")
            problems += [f"{service}.{field}: option {o}" for o in options if not re.search(rf"\b{o}\b", plain)]
            if "default" in fspec:
                default = fspec["default"]
                expected = ("on" if default else "off") if isinstance(default, bool) else str(default)
                stated = _stated_default(row, field)
                if stated != expected:
                    problems.append(f"{service}.{field}: default {expected!r} (stated: {stated!r})")
    assert not problems, f"{API_DOC} Services states these options or defaults wrongly or not at all: {problems}"


# ------------------------------------------------------------------------------------------------ 5. HTTP API
def _http_views() -> tuple[set[str], set[str]]:
    """The url of every view class of http_api.py and every query parameter the file reads (request.query.get("x"))."""
    tree = TREES["http_api.py"]
    urls, params = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and [getattr(t, "id", None) for t in stmt.targets] == ["url"]
                    and isinstance(stmt.value, ast.Constant)
                ):
                    urls.add(stmt.value.value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "query"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            params.add(node.args[0].value)
    return urls, params


def test_every_http_endpoint_and_parameter_is_in_entities_and_api():
    """docs/ENTITIES_AND_API.md "HTTP API" has a row for every view url of http_api.py, and names every query
    parameter the views read, the report options of the two report views included (const.REPORT_OPT_*)."""
    section = _section(repo_doc("ENTITIES_AND_API.md"), "HTTP API")
    urls, params = _http_views()
    assert len(urls) >= 5 and params  # the views are still found
    params |= {const.REPORT_OPT_CUSTOM_APPENDIX, const.REPORT_OPT_RESEARCH_APPENDIX}
    rows = [row for row in section.splitlines() if row.startswith("| `GET ")]
    endpoints = {re.match(r"\| `GET (/[^?` ]+)", row).group(1) for row in rows}
    missing = sorted(urls - endpoints) + sorted(f"?{p}=" for p in params if not re.search(rf"[?&]{p}=", section))
    assert not missing, f"{API_DOC} HTTP API does not document: {missing}"
    assert not endpoints - urls, f"{API_DOC} HTTP API documents endpoints http_api.py has not: {endpoints - urls}"


# ------------------------------------------------------------------------------------------------ 6. defaults
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _switch(word: str) -> bool:
    """ "on" / "off" as a switch's value."""
    return word.lower() in ("on", "true")


# (the words that state a default, with the value as the group "v"; the settings it is the default of; the value
# those words mean)
DOCUMENTATION_DEFAULTS = [
    (r"Daily summary \((?P<v>on|off) by default\)", [const.CONF_NOTIFY_ENABLED], _switch),
    (r"Daily summary time \(default (?P<v>\d\d:\d\d)\)", [const.CONF_NOTIFY_TIME], str),
    (r"Weekly summary \((?P<v>on|off) by default\)", [const.CONF_NOTIFY_WEEKLY_ENABLED], _switch),
    (r"Weekly summary day \(default (?P<v>[A-Z][a-z]+day)\)", [const.CONF_NOTIFY_WEEKLY_DAY], _WEEKDAYS.index),
    (r"No-data notice \((?P<v>on|off) by default\)", [const.CONF_NO_DATA_NOTICE], _switch),
    (r"No-data notice \(optional, (?P<v>on|off) by default", [const.CONF_NO_DATA_NOTICE], _switch),
    (r"Notify after \(default (?P<v>\d+) minutes\)", [const.CONF_NO_DATA_NOTICE_DELAY], int),
    (r"Why (?P<v>\d+) minutes by default", [const.CONF_STALE_THRESHOLD], int),
    (r"Outline text \(\{screen_displays\}; (?P<v>on|off) by default\)", [const.CONF_TEXT_OUTLINE], _switch),
    (r"setting Outline text, (?P<v>on|off) by default", [const.CONF_TEXT_OUTLINE], _switch),
    (
        r"Fade the words when there is no current reading \(\{screen_displays\}; (?P<v>on|off) by default\)",
        [const.CONF_FLASH_NO_CURRENT_DATA],
        _switch,
    ),
    (
        r"Trend shown as \(\{screen_displays\}\)\. (?P<v>Arrow|Word) \(the default\)",
        [const.CONF_TREND_DISPLAY],
        str.lower,
    ),
    (r"(?P<v>Square|Rectangular) \([^()]*; the default\)", [const.CONF_DISPLAY_CARD], str.lower),
    (
        (
            r"Range set of the weekly pages \(\{screen_displays\}\)\. The (?P<v>consensus|custom) ranges with the "
            r"\w+ \w+ \(the default\)"
        ),
        [const.CONF_WEEKLY_PAGES],
        str,
    ),
    (
        r"in Home Assistant's sidebar; (?P<v>on|off) by default",
        [const.CONF_SHOW_DASHBOARD],
        _switch,
    ),
    (
        (
            r"When GlucoCraft is deleted \(\{screen_report\}\)\. (?P<v>Keep the readings for a reinstall|Delete "
            r"everything) \(the default\)"
        ),
        [const.CONF_ON_REMOVAL],
        lambda v: "keep" if v.startswith("Keep") else "delete",
    ),
    (
        r"are set on the same screen, (?P<v>black|white) by default",
        [const.CONF_CARD_BACKGROUND, const.CONF_PANEL_BACKGROUND],
        lambda v: {"black": "#000000", "white": "#FFFFFF"}[v],
    ),
    (
        r"Sensor lower limit: (?P<v>Automatic) \(the default\)",
        [const.CONF_SENSOR_LOWER_LIMIT, const.CONF_SECONDARY_SENSOR_LOWER_LIMIT],
        lambda v: "auto",
    ),
    (
        r"Sensor upper limit: (?P<v>Automatic) \(the default\)",
        [const.CONF_SENSOR_UPPER_LIMIT, const.CONF_SECONDARY_SENSOR_UPPER_LIMIT],
        lambda v: "auto",
    ),
    (
        r"\. (?P<v>Automatic) \(the default\) follows Home Assistant's country",
        [const.CONF_TIME_FORMAT, const.CONF_DATE_FORMAT],
        lambda v: "auto",
    ),
    (
        r"Each is (?P<v>Automatic) \(the default\) or a choice",
        list(settings.REGIONAL_OPTIONS),
        lambda v: "auto",
    ),
    (
        r"both are (?P<v>off|on) in a new installation",
        [const.CONF_CUSTOM_RANGES_AND_GOALS, const.CONF_SEPARATE_STATISTICS],
        _switch,
    ),
]

# the same in docs/ENTITIES_AND_API.md "Settings" (the Configuration sensor's attributes)
API_DOC_DEFAULTS = [
    (r"card_background \(\"theme\" or \"#RRGGBB\", default \"(?P<v>#[0-9A-F]{6})\"", [const.CONF_CARD_BACKGROUND], str),
    (r"panel_background \(\"#RRGGBB\", default \"(?P<v>#[0-9A-F]{6})\"", [const.CONF_PANEL_BACKGROUND], str),
    (r"text_outline \(the setting Outline text, (?P<v>true|false) by default", [const.CONF_TEXT_OUTLINE], _switch),
    (
        r"flash_no_current_data \(the setting [^,]*, (?P<v>true|false) by default",
        [const.CONF_FLASH_NO_CURRENT_DATA],
        _switch,
    ),
    (r"trend_display \(the setting Trend shown as: (?P<v>\w+), the default", [const.CONF_TREND_DISPLAY], str),
    (
        r"no_data_notice \(the Home Assistant no-data notice, (?P<v>true|false) by default",
        [const.CONF_NO_DATA_NOTICE],
        _switch,
    ),
    (r"no_data_notice_delay_minutes \([^)]*, default (?P<v>\d+)\)", [const.CONF_NO_DATA_NOTICE_DELAY], int),
    (r"on_removal \(the setting When GlucoCraft is deleted: (?P<v>\w+), the default", [const.CONF_ON_REMOVAL], str),
]


def _check_defaults(text: str, table: list, where: str) -> list[str]:
    """Every stated default of a table compared with settings.setting({}, key), the default of settings.py's table."""
    problems = []
    for pattern, keys, meaning in table:
        matches = list(re.finditer(pattern, text))
        if not matches:
            problems.append(f"{where} no longer states this default (pattern {pattern!r}); update the test's table")
        for m in matches:
            for key in keys:
                stated, default = meaning(m.group("v")), settings.setting({}, key)
                if stated != default:
                    problems.append(f"{where}: {m.group(0)!r} states {stated!r}; the default of {key} is {default!r}")
    return problems


def test_the_defaults_the_documentation_states_are_the_code_defaults():
    """The defaults the Documentation states in words ("Daily summary (on by default)", "Notify after (default 30
    minutes)") equal the defaults of settings.py; a setting's default changed in the code fails until the text says so."""
    problems = _check_defaults(_all_documentation_text(), DOCUMENTATION_DEFAULTS, IN_APP)
    assert not problems, problems


def test_the_defaults_entities_and_api_states_are_the_code_defaults():
    """The defaults docs/ENTITIES_AND_API.md "Settings" states for the Configuration sensor's attributes equal the
    defaults of settings.py."""
    text = _plain(_section(repo_doc("ENTITIES_AND_API.md"), "Settings"))
    problems = _check_defaults(text, API_DOC_DEFAULTS, f"{API_DOC} Settings")
    assert not problems, problems


@pytest.mark.parametrize(
    "where, pattern",
    [
        (IN_APP, r"Why (\d+) days"),
        (IN_APP, r"older than (\d+) days"),
        (API_DOC, r"Retention:\s*(\d+) days"),
        (API_DOC, r"retention_days \(always (\d+)\)"),
        (API_DOC, r"GlucoCraft's own store \((\d+) days\)"),
    ],
)
def test_the_retention_stated_is_the_code_retention(where, pattern):
    """Every statement of the retention in days equals const.DEFAULT_RETENTION_DAYS."""
    text = _all_documentation_text() if where == IN_APP else _plain(repo_doc("ENTITIES_AND_API.md"))
    stated = {int(n) for n in re.findall(pattern, text)}
    assert stated, f"{where} no longer states the retention as {pattern!r}; update the test"
    assert stated == {const.DEFAULT_RETENTION_DAYS}, (
        f"{where}: the retention is stated as {sorted(stated)} days, the code keeps {const.DEFAULT_RETENTION_DAYS}"
    )
