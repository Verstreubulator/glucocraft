# License: AGPL-3.0-only (LICENSE).
"""Installation, the actions, unloading and removal (__init__.py), and the release checks of manifest.json.

Rules under test:
- One GlucoCraft installation per Home Assistant (manifest "single_config_entry"; the setup flow aborts a second one).
- The manifest passes hassfest's rules: every imported integration declared (lovelace), the HACS
  keys (issue_tracker, documentation, codeowners), integration_type, keys in hassfest's order.
- The workflows: HACS and hassfest validation, and the tests (ruff, the suite, the tests on Home Assistant itself on
  the oldest supported and the newest tested release).
- The actions are registered once in async_setup and find the loaded entry themselves; without one they fail with a
  clear message; every action changes data or can send data out of Home Assistant, so every one is for
  administrators (calls from automations and scripts, without a user, are allowed).
- Unloading shuts the coordinator down only when the platforms unloaded; a disabled entry removes the dashboard.
- Removal as the setting When GlucoCraft is deleted says. Delete everything leaves no trace: every readings file, CSV
  export, report PDF, store, Repairs issue and notification of GlucoCraft, the dashboard and the card resources are
  removed. Keep the readings for a reinstall (the default) removes the same, except the readings file, the laboratory
  results and the sensor events, which are kept in the folder glucocraft for a new installation (kept.py), and says so
  in a notification; a new installation adopts them, or deletes them when the setup chose to start fresh.
- The entry version check refuses an entry of a newer version; the current format (2.3) needs no migration.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import types
from pathlib import Path

import pytest
import yaml
from glucocraft import const as C
from glucocraft import coordinator as co

import ha_stub
from conftest import real_coordinator
from test_entity_ids import _load_init

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "custom_components" / "glucocraft"
MANIFEST = json.loads((PKG / "manifest.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------------------ the manifest
def test_manifest_single_entry_and_hacs_keys():
    assert MANIFEST["single_config_entry"] is True
    assert MANIFEST["integration_type"] == "service"
    for key in ("documentation", "issue_tracker", "codeowners", "version"):
        assert MANIFEST[key], key
    # The repository's address: an https address in a release; before it, the placeholder that the release fills
    address = MANIFEST["documentation"]
    assert address.startswith("https://") or re.fullmatch(r"\[repository address\]", address)
    assert MANIFEST["issue_tracker"] == f"{address}/issues"
    assert all(owner.startswith("@") for owner in MANIFEST["codeowners"])


def test_iot_class_is_calculated():
    """GlucoCraft reads other Home Assistant entities and calculates; it talks to no device or service itself."""
    assert MANIFEST["iot_class"] == "calculated"


def test_report_pdf_not_imported_in_the_event_loop():
    """report_pdf (a large module) is imported with report_service, which Home Assistant imports in its executor,
    never by an import statement inside an async function."""
    import ast

    tree = ast.parse((PKG / "report_service.py").read_text(encoding="utf-8"))
    top = [n for n in tree.body if isinstance(n, ast.ImportFrom) and any(a.name == "report_pdf" for a in n.names)]
    assert top
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            for inner in ast.walk(node):
                if isinstance(inner, (ast.Import, ast.ImportFrom)):
                    assert "report_pdf" not in ast.dump(inner), node.name


def test_manifest_key_order_is_hassfests():
    keys = list(MANIFEST)
    assert keys[:2] == ["domain", "name"] and keys[2:] == sorted(keys[2:])


def test_every_imported_integration_is_declared():
    """hassfest's dependencies rule: an integration imported anywhere in the code (also inside a function) must be
    in dependencies or after_dependencies, apart from the platforms and the integrations hassfest always allows."""
    import re

    declared = set(MANIFEST["dependencies"]) | set(MANIFEST["after_dependencies"])
    allowed = {"sensor", "binary_sensor", "persistent_notification", "repairs", "http", "frontend", "webhook"}
    # an integration this one has a platform file for (diagnostics.py, repairs.py) may be imported (hassfest rule)
    allowed |= {path.stem for path in PKG.glob("*.py")}
    used = set()
    for path in PKG.glob("*.py"):
        used |= set(re.findall(r"homeassistant\.components\.(\w+)", path.read_text(encoding="utf-8")))
        used |= {
            m for m in re.findall(r"from homeassistant\.components import (\w+)", path.read_text(encoding="utf-8"))
        }
    assert used - allowed <= declared, used - allowed - declared
    assert "lovelace" in declared


def test_hacs_and_hassfest_workflows():
    validate = (REPO / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")
    hassfest = (REPO / ".github" / "workflows" / "hassfest.yaml").read_text(encoding="utf-8")
    assert "hacs/action@main" in validate and 'category: "integration"' in validate and "ignore:" not in validate
    assert "home-assistant/actions/hassfest@master" in hassfest


def test_the_test_workflow_runs_ruff_the_suite_and_the_home_assistant_tests():
    """tests.yml: ruff, the suite (the PDF renders at night and on tags), and tests_ha on the oldest Home Assistant
    that hacs.json supports and on the newest tested one."""
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8"))
    steps = {job: [s.get("run", "") for s in spec["steps"]] for job, spec in workflow["jobs"].items()}
    assert {"ruff check .", "ruff format --check ."} <= set(steps["ruff"])
    assert {'python -m pytest -m "not slow"', "python -m pytest"} <= set(steps["tests"])
    assert "python -m pytest tests_ha" in steps["home-assistant"]
    matrix = workflow["jobs"]["home-assistant"]["strategy"]["matrix"]["requirements"]
    assert matrix == ["requirements_minimum.txt", "requirements.txt"]
    oldest = json.loads((REPO / "hacs.json").read_text(encoding="utf-8"))["homeassistant"]
    tested = re.findall(r"# Home Assistant (\d{4}\.\d+\.\d+)", (REPO / "tests_ha" / matrix[0]).read_text("utf-8"))
    assert tested == [oldest], (tested, oldest)  # hacs.json declares the release that is tested


def test_setup_flow_aborts_a_second_installation():
    from glucocraft import config_flow as cf

    flow = cf.GlucoCraftConfigFlow()
    flow._async_current_entries = lambda include_ignore=None: [object()]
    flow.async_abort = lambda **kw: ("abort", kw)
    assert asyncio.run(flow.async_step_user()) == ("abort", {"reason": "single_instance_allowed"})
    strings = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))
    assert "single_instance_allowed" in strings["config"]["abort"]


# ------------------------------------------------------------------------------------------------ a fake hass
class FakeServices:
    """Home Assistant's actions: the registered ones and the calls."""

    def __init__(self):
        self.registered: dict[str, dict] = {}
        self.calls: list = []

    def async_register(self, domain, name, handler, schema=None, supports_response=None):
        """Register an action."""
        self.registered[name] = {"handler": handler, "schema": schema, "response": supports_response}

    def has_service(self, domain, name):
        """Only the recorder's purge_entities exists."""
        return (domain, name) == ("recorder", "purge_entities")

    async def async_call(self, domain, service, data=None, **kw):
        """Record the action call (domain, action, data)."""
        self.calls.append((domain, service, dict(data or {})))


def fake_hass(tmp_path, entries=(), users=None):
    async def run(fn, *args):
        return fn(*args)

    async def get_user(user_id):
        return (users or {}).get(user_id)

    return types.SimpleNamespace(
        config=types.SimpleNamespace(path=lambda *p: str(Path(tmp_path, *p)), time_zone="UTC"),
        async_add_executor_job=run,
        services=FakeServices(),
        data={},
        config_entries=types.SimpleNamespace(async_entries=lambda domain=None: list(entries)),
        auth=types.SimpleNamespace(async_get_user=get_user),
    )


@pytest.fixture
def init():
    return _load_init()


# ------------------------------------------------------------------------------------------------ the actions
def test_actions_registered_once_in_async_setup(init, tmp_path):
    hass = fake_hass(tmp_path)
    assert asyncio.run(init.async_setup(hass, {})) is True
    assert set(hass.services.registered) == set(init.SERVICES)
    import inspect

    src = inspect.getsource(init)
    for name in ("print_report", "export_csv"):  # the two actions that return a response
        assert hass.services.registered[name]["response"] is init._HANDLERS[name][2]
        line = next(ln for ln in src.splitlines() if ln.strip().startswith(f'"{name}": (_handle_'))
        assert line.rstrip().endswith("SupportsResponse.OPTIONAL),"), line
    assert "services.async_register" not in inspect.getsource(init.async_setup_entry)  # not per entry
    assert init.CONFIG_SCHEMA is not None


class _Ctx:
    """The context of an action call: the user who called it."""

    def __init__(self, user_id=None):
        self.user_id = user_id


def test_action_without_a_loaded_entry_fails_clearly(init, tmp_path, monkeypatch):
    raised = []

    class SVE(Exception):
        def __init__(self, **kw):
            raised.append(kw)

    monkeypatch.setattr(init, "ServiceValidationError", SVE)
    hass = fake_hass(tmp_path)
    asyncio.run(init.async_setup(hass, {}))
    with pytest.raises(SVE):
        asyncio.run(
            hass.services.registered["send_daily_summary"]["handler"](types.SimpleNamespace(data={}, context=_Ctx()))
        )
    assert raised[0]["translation_key"] == "not_loaded"
    strings = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))
    assert "not_loaded" in strings["exceptions"] and "lab_invalid" in strings["exceptions"]


def test_data_actions_are_for_administrators(init, tmp_path, monkeypatch):
    class Denied(Exception):
        def __init__(self, **kw):
            pass

    monkeypatch.setattr(init, "Unauthorized", Denied)
    users = {"kid": types.SimpleNamespace(is_admin=False), "admin": types.SimpleNamespace(is_admin=True)}
    hass = fake_hass(tmp_path, users=users)
    asyncio.run(init.async_setup(hass, {}))
    # every action, the three summaries too: they go to the phones chosen for them
    assert set(hass.services.registered) == set(init.SERVICES)
    assert {"send_daily_summary", "send_weekly_summary", "send_monthly_summary"} <= set(init.SERVICES)
    for name in init.SERVICES:
        with pytest.raises(Denied):
            asyncio.run(
                hass.services.registered[name]["handler"](
                    types.SimpleNamespace(data={"date": "2026-10-01"}, context=_Ctx("kid"))
                )
            )
    asyncio.run(init._require_admin(hass, types.SimpleNamespace(context=_Ctx("admin"))))  # allowed
    asyncio.run(init._require_admin(hass, types.SimpleNamespace(context=_Ctx(None))))  # automations: allowed


def test_loaded_coordinator_is_the_runtime_data(tmp_path):
    c = real_coordinator(tmp_path)
    entry = types.SimpleNamespace(runtime_data=c)
    hass = types.SimpleNamespace(config_entries=types.SimpleNamespace(async_entries=lambda d: [entry]))
    assert co.loaded_coordinator(hass) is c
    c._shut_down = True
    assert co.loaded_coordinator(hass) is None
    assert (
        co.loaded_coordinator(
            types.SimpleNamespace(
                config_entries=types.SimpleNamespace(async_entries=lambda d: [types.SimpleNamespace()])
            )
        )
        is None
    )


# ------------------------------------------------------------------------------------------------ unloading
class _Coordinator:
    """A coordinator that counts its shutdowns."""

    def __init__(self):
        self.shut = 0

    async def async_shutdown(self):
        """Count the shutdown."""
        self.shut += 1


def _unload(init, tmp_path, unload_ok, disabled_by=None, monkeypatch=None):
    removed = []

    async def remove_dashboard(hass):
        removed.append("dashboard")

    monkeypatch.setattr(init, "async_remove_strategy_dashboard", remove_dashboard)
    coordinator = _Coordinator()
    entry = types.SimpleNamespace(entry_id="e1", runtime_data=coordinator, disabled_by=disabled_by)
    hass = fake_hass(tmp_path)

    async def unload_platforms(e, platforms):
        return unload_ok

    hass.config_entries.async_unload_platforms = unload_platforms
    result = asyncio.run(init.async_unload_entry(hass, entry))
    return result, coordinator.shut, removed


def test_coordinator_kept_when_platforms_fail_to_unload(init, tmp_path, monkeypatch):
    assert _unload(init, tmp_path, False, monkeypatch=monkeypatch) == (False, 0, [])


def test_unload_shuts_down_and_keeps_the_dashboard_for_a_reload(init, tmp_path, monkeypatch):
    assert _unload(init, tmp_path, True, monkeypatch=monkeypatch) == (True, 1, [])


def test_disabled_entry_removes_the_dashboard(init, tmp_path, monkeypatch):
    assert _unload(init, tmp_path, True, disabled_by="user", monkeypatch=monkeypatch) == (True, 1, ["dashboard"])


# ------------------------------------------------------------------------------------------------ removal
def test_removal_with_delete_everything_leaves_no_trace(init, tmp_path, monkeypatch):
    # files GlucoCraft wrote in the configuration directory
    readings = tmp_path / "glucocraft"
    readings.mkdir()
    for name in ("glucocraft_e1.bin", "glucocraft_e1.bin.corrupt-1700000000", "glucocraft_e1.tmp"):
        (readings / name).write_bytes(b"x")
    exports = tmp_path / C.EXPORT_DIR
    exports.mkdir()
    for name in (
        "glucocraft-readings_2026-10-01_080000.csv",
        "glucocraft-lab-a1c_2026-10-01_080000.csv",
        "glucocraft-readings_2026-10-02_080000.csv.tmp",
    ):
        (exports / name).write_text("x")
    reports = tmp_path / init.REPORT_DIR
    reports.mkdir()
    (reports / "glucocraft-cgm-report_2026-10-01_0800_14d_summary_color.pdf").write_bytes(b"%PDF")
    # stores, issues, notifications
    keys = [
        "glucocraft.e1_state",
        "glucocraft.e1_panels",
        "glucocraft.lab_a1c",
        "glucocraft.sensor_events",
    ]
    for key in keys:
        ha_stub.STORE_DATA[key] = {"x": 1}
    issues = {
        ("glucocraft", "terms_of_use_e1"): 1,
        ("glucocraft", "clock_behind"): 1,
        ("glucocraft", "slow_source_sensor.x"): 1,
        ("other", "keep"): 1,
    }
    ir = sys.modules["homeassistant.helpers.issue_registry"]
    monkeypatch.setattr(ir, "async_get", lambda hass: types.SimpleNamespace(issues=dict(issues)), raising=False)
    monkeypatch.setattr(ir, "async_delete_issue", lambda hass, d, i: issues.pop((d, i), None), raising=False)
    dismissed = []
    pn = sys.modules["homeassistant.components.persistent_notification"]
    monkeypatch.setattr(pn, "async_dismiss", lambda hass, nid: dismissed.append(nid), raising=False)
    from glucocraft import panel_notice

    monkeypatch.setattr(init, "panel_device_ids", lambda hass: ["dev1"])
    assert init.panel_device_ids is not panel_notice.panel_device_ids  # the name __init__.py calls
    done = []

    async def remove_dashboard(hass):
        done.append("dashboard")

    async def remove_resources(hass):
        done.append("resources")

    monkeypatch.setattr(init, "async_remove_strategy_dashboard", remove_dashboard)
    monkeypatch.setattr(init, "async_unregister_lovelace_resources", remove_resources)
    cleared = _fake_recorder(
        init, monkeypatch, ["sensor.glucocraft_last_glucose_reading", "binary_sensor.glucocraft_no_current_data"]
    )

    hass = fake_hass(tmp_path)
    asyncio.run(init.async_remove_entry(hass, types.SimpleNamespace(entry_id="e1", data={C.CONF_ON_REMOVAL: "delete"})))
    ids = ["binary_sensor.glucocraft_no_current_data", "sensor.glucocraft_last_glucose_reading"]
    assert ("recorder", "purge_entities", {"entity_id": ids, "keep_days": 0}) in hass.services.calls
    assert cleared == [ids]  # the long-term statistics as well

    assert not readings.exists() and not exports.exists() and not reports.exists()
    assert not any(k in ha_stub.STORE_DATA for k in keys)
    assert issues == {("other", "keep"): 1}
    for nid in (
        "glucocraft_daily_summary",
        "glucocraft_weekly_summary",
        "glucocraft_monthly_summary",
        "glucocraft_export",
        "glucocraft_report_print",
        "glucocraft_panel_webhook",
        "glucocraft_panel_added_dev1",
        "glucocraft_panel_offline_dev1",
    ):
        assert nid in dismissed, nid
    assert done == ["dashboard", "resources"]


def _fake_recorder(init, monkeypatch, entity_ids):
    """The entity registry with this entry's entities and a recorder that records clear-statistics calls."""
    entries = [types.SimpleNamespace(entity_id=e) for e in entity_ids]
    monkeypatch.setattr(
        init,
        "er",
        types.SimpleNamespace(
            async_get=lambda hass: object(), async_entries_for_config_entry=lambda reg, entry_id: list(entries)
        ),
    )
    cleared = []
    recorder = sys.modules["homeassistant.components.recorder"]
    monkeypatch.setattr(
        recorder,
        "get_instance",
        lambda hass: types.SimpleNamespace(async_clear_statistics=cleared.append),
        raising=False,
    )
    return cleared


def test_recorder_failure_does_not_stop_the_removal(init, tmp_path, monkeypatch, caplog):
    _fake_recorder(init, monkeypatch, ["sensor.glucocraft_status"])
    hass = fake_hass(tmp_path)

    async def broken(*a, **kw):
        raise RuntimeError("recorder busy")

    hass.services.async_call = broken
    asyncio.run(init._async_clear_recorder(hass, ["sensor.glucocraft_status"]))
    assert "could not be removed" in caplog.text


def test_removal_keeps_files_it_did_not_write(init, tmp_path, monkeypatch):
    folder = tmp_path / C.EXPORT_DIR
    folder.mkdir()
    (folder / "notes.txt").write_text("the user's own file")
    init._remove_data_files(fake_hass(tmp_path), "e1")
    assert (folder / "notes.txt").exists()


def test_every_store_of_the_package_is_removed(init):
    """Every Store key the code creates is in the removal lists (a new store cannot be forgotten)."""
    import re

    found = set()
    for path in PKG.glob("*.py"):
        found |= set(re.findall(r'Store\(\s*\w+,\s*1,\s*f?"(glucocraft\.[^"]+)"', path.read_text(encoding="utf-8")))
    removed = {f"glucocraft.{{entry_id}}_{s}" for s in init.ENTRY_STORE_SUFFIXES} | set(init.REPORT_STORE_KEYS)
    normalized = {
        k.replace("{coordinator.entry_id}", "{entry_id}").replace("{entry.entry_id}", "{entry_id}") for k in found
    }
    assert normalized <= removed, normalized - removed


# ------------------------------------------------------------------------------------------------ keep for a reinstall
def _readings_file(folder: Path, entry_id: str, count: int = 3, start: int = 1_790_000_000) -> None:
    """A readings file of `count` readings every 5 minutes from `start` (storage.py's format)."""
    from glucocraft.storage import GlucoCraftStorage

    storage = GlucoCraftStorage(str(folder), entry_id)
    storage.load()
    for k in range(count):
        storage.insert_reading(start + 300 * k, 100.0 + k, None, C.SOURCE_PRIMARY)
    storage.save()


LAB = {"results": [{"date": "2026-09-01", "a1c": 7.1}]}
EVENTS = {"events": [{"start": 1_790_000_000, "end": 1_790_007_200}]}


def test_removal_keeps_the_readings_for_a_reinstall_by_default(init, tmp_path, monkeypatch):
    readings = tmp_path / "glucocraft"
    _readings_file(readings, "e1")
    (readings / "glucocraft_e1.bin.corrupt-1700000000").write_bytes(b"x")
    exports = tmp_path / C.EXPORT_DIR
    exports.mkdir()
    (exports / "glucocraft-readings_2026-10-01_080000.csv").write_text("x")
    for key, value in (("glucocraft.lab_a1c", LAB), ("glucocraft.sensor_events", EVENTS), ("glucocraft.e1_state", {})):
        ha_stub.STORE_DATA[key] = value
    ir = sys.modules["homeassistant.helpers.issue_registry"]
    monkeypatch.setattr(ir, "async_get", lambda hass: types.SimpleNamespace(issues={}), raising=False)
    pn = sys.modules["homeassistant.components.persistent_notification"]
    created, dismissed = [], []
    monkeypatch.setattr(pn, "async_dismiss", lambda hass, nid: dismissed.append(nid), raising=False)
    monkeypatch.setattr(pn, "async_create", lambda hass, msg, title=None, nid=None: created.append((msg, title, nid)))
    monkeypatch.setattr(init, "panel_device_ids", lambda hass: [])

    async def nothing(hass):
        return None

    monkeypatch.setattr(init, "async_remove_strategy_dashboard", nothing)
    monkeypatch.setattr(init, "async_unregister_lovelace_resources", nothing)
    _fake_recorder(init, monkeypatch, ["sensor.glucocraft_last_glucose_reading"])

    hass = fake_hass(tmp_path)
    asyncio.run(init.async_remove_entry(hass, types.SimpleNamespace(entry_id="e1", data={})))
    # kept: the readings file under its kept name and the two lists; everything else removed
    assert sorted(p.name for p in readings.iterdir()) == [
        "glucocraft_kept.bin",
        "glucocraft_kept_lab_a1c.json",
        "glucocraft_kept_sensor_events.json",
    ]
    assert not exports.exists() and not ha_stub.STORE_DATA
    assert (
        "recorder",
        "purge_entities",
        {"entity_id": ["sensor.glucocraft_last_glucose_reading"], "keep_days": 0},
    ) in (hass.services.calls)
    ((message, title, nid),) = created
    assert nid == "glucocraft_kept_readings" and title == "GlucoCraft deleted"
    assert message == (
        "GlucoCraft was deleted. Its readings were kept for a reinstall, in the `glucocraft` folder; its settings were "
        "not, so a reinstall starts with the default settings. To delete them, "
        "reinstall GlucoCraft and choose *Delete them and start fresh*, or delete that folder."
    )
    # a new installation finds them and adopts them at its first start
    from glucocraft import kept

    assert kept.kept_summary(str(readings)) == (3, 1_790_000_000, 1_790_000_600)
    asyncio.run(init._async_adopt_kept(hass, types.SimpleNamespace(entry_id="e2")))
    assert sorted(p.name for p in readings.iterdir()) == ["glucocraft_e2.bin"]
    assert ha_stub.STORE_DATA["glucocraft.lab_a1c"] == LAB and ha_stub.STORE_DATA["glucocraft.sensor_events"] == EVENTS
    assert "glucocraft_kept_readings" in dismissed
    assert kept.kept_summary(str(readings)) is None


def test_nothing_is_kept_without_a_readings_file(init, tmp_path, monkeypatch):
    from glucocraft import kept

    assert kept.keep(str(tmp_path / "glucocraft"), "e1", [], []) is False
    assert not (tmp_path / "glucocraft").exists()


def test_start_fresh_deletes_what_was_kept(tmp_path):
    from glucocraft import kept

    folder = tmp_path / "glucocraft"
    _readings_file(folder, "e1")
    assert kept.keep(str(folder), "e1", LAB["results"], EVENTS["events"]) is True
    kept.discard(str(folder))
    assert not folder.exists()
    assert kept.adopt(str(folder), "e2") is None


def test_adoption_never_replaces_an_entrys_own_readings(tmp_path):
    from glucocraft import kept

    folder = tmp_path / "glucocraft"
    _readings_file(folder, "e1", count=3)
    kept.keep(str(folder), "e1", [], [])
    _readings_file(folder, "e2", count=5)
    assert kept.adopt(str(folder), "e2") == ([], [])
    assert kept.kept_summary(str(folder))[0] == 3  # the kept file stays; the entry's own is untouched


def _setup_flow_with_kept(tmp_path, monkeypatch):
    from glucocraft import config_flow as cf

    from test_config_flow_options import _hass

    flow = cf.GlucoCraftConfigFlow()
    flow.hass = _hass(config_dir=tmp_path)
    flow._async_current_entries = lambda include_ignore=None: []
    shown, created = [], []
    flow.async_show_form = lambda **kw: (shown.append(kw), ("form", kw))[1]
    flow.async_create_entry = lambda **kw: (created.append(kw["data"]), ("entry", kw))[1]
    dismissed = []
    pn = sys.modules["homeassistant.components.persistent_notification"]
    monkeypatch.setattr(pn, "async_dismiss", lambda hass, nid: dismissed.append(nid), raising=False)
    return flow, shown, created, dismissed


@pytest.mark.parametrize("choice", ["keep", "delete"])
def test_the_setup_asks_about_kept_readings(tmp_path, monkeypatch, choice):
    """After the disclaimer: "Readings from an earlier installation were found: <N> readings from <date> to <date>."; the
    choice takes effect when the setup ends (kept: adopted at the first start; deleted: now)."""
    from glucocraft import kept

    folder = tmp_path / "glucocraft"
    # four readings from 1_790_000_000 s, 2026-09-21 (UTC)
    _readings_file(folder, "e1", count=4, start=1_790_000_000)
    kept.keep(str(folder), "e1", [], [])
    flow, _shown, created, dismissed = _setup_flow_with_kept(tmp_path, monkeypatch)
    kind, kw = asyncio.run(flow.async_step_user({}))
    assert kind == "form" and kw["step_id"] == "kept"
    assert kw["description_placeholders"]["count"] == "4"
    assert kw["description_placeholders"]["first"].endswith("2026") and kw["description_placeholders"]["last"]
    strings = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))
    assert strings["config"]["step"]["kept"]["description"] == (
        "Readings from an earlier installation were found: {count} readings from {first} to {last}."
    )
    assert strings["selector"]["kept_readings"]["options"] == {
        "keep": "Keep them",
        "delete": "Delete them and start fresh",
    }
    kind, kw = asyncio.run(flow.async_step_kept({"kept_readings": choice}))
    assert kw["step_id"] == "source" and (folder / "glucocraft_kept.bin").exists()  # nothing changed yet
    flow.hass.states = types.SimpleNamespace(get=lambda e: types.SimpleNamespace(attributes={}))
    asyncio.run(flow.async_step_source({C.CONF_GLUCOSE_ENTITY: "sensor.cgm", "second_source": {}}))
    asyncio.run(flow.async_step_finish({}))
    assert created and "glucocraft_kept_readings" in dismissed
    assert (folder / "glucocraft_kept.bin").exists() is (choice == "keep")
    assert C.CONF_ON_REMOVAL not in created[0]  # the default (keep) is not stored by the setup


def test_the_setting_when_glucocraft_is_deleted():
    from glucocraft.settings import setting

    assert setting({}, C.CONF_ON_REMOVAL) == "keep" and setting({C.CONF_ON_REMOVAL: "x"}, C.CONF_ON_REMOVAL) == "keep"
    assert setting({C.CONF_ON_REMOVAL: "delete"}, C.CONF_ON_REMOVAL) == "delete"
    strings = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))
    report = strings["options"]["step"]["report"]
    assert report["data"]["on_removal"] == "When GlucoCraft is deleted"
    assert strings["selector"]["on_removal"]["options"] == {
        "keep": "Keep the readings for a reinstall",
        "delete": "Delete everything",
    }
    assert (
        "Reload restarts GlucoCraft without deleting anything; Disable turns it off"
        in (report["data_description"]["on_removal"])
    )


# ------------------------------------------------------------------------------------------------ version check
def _migrate(init, version, minor, data):
    updates = []

    def update(entry, data=None, version=None, minor_version=None):
        updates.append((version, minor_version))
        if data is not None:
            entry.data = data
        if version is not None:
            entry.version = version
        if minor_version is not None:
            entry.minor_version = minor_version

    entry = types.SimpleNamespace(version=version, minor_version=minor, data=dict(data))
    hass = types.SimpleNamespace(config_entries=types.SimpleNamespace(async_update_entry=update))
    return asyncio.run(init.async_migrate_entry(hass, entry)), entry, updates


def test_migration_refuses_a_newer_entry(init):
    ok, _, updates = _migrate(init, 3, 1, {})
    assert ok is False and updates == []


def test_the_current_entry_format_needs_no_migration(init):
    """2.3 is the first entry format: an entry of it is accepted as it is; a newer one is refused (above)."""
    ok, entry, updates = _migrate(init, 2, 3, {"glucose_entity": "sensor.p"})
    assert ok is True and updates == [] and entry.data == {"glucose_entity": "sensor.p"}
    ok, _, updates = _migrate(init, 2, 4, {})
    assert ok is False and updates == []
    assert init.ENTRY_VERSION == 2 and init.ENTRY_MINOR_VERSION == 3
