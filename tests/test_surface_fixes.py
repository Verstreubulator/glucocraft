# License: AGPL-3.0-only (LICENSE).
"""The setup, the actions, the HTTP API, the CSV export and the removal: the rules of their edges.

- Every save of the entry's data keeps only the keys GlucoCraft knows (settings.known_entry_data): the Configure
  screens, the display unit taken from the entity settings and the wall-panel address.
- At every start the registry entries of entities the setup did not create are removed (stale_entities.py), only when
  both platforms reported their entities.
- /data and /stats publish unrounded glucose values and the last reading's time; /data reduces long periods to slots
  in the executor, not in the event loop.
- The CSV export writes a LO / HI reading as LO / HI in glucose_display; the import reads such a row back.
- The removal deletes only the file names GlucoCraft writes.
- A failed card registration and a failed history import open a Repairs notice; a failed action is a translated
  error; device registry updates that concern no panel are left alone.
- Every persistent notification id and store of the package is in the removal's lists.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import types
from pathlib import Path

import pytest
from glucocraft import const as C
from glucocraft import coordinator as co
from glucocraft import diagnostics, export, frontend, history_import, http_api, panel_notice, stale_entities
from glucocraft import panel_feed as pf
from glucocraft.limits import DEFAULT_UPPER
from glucocraft.settings import ENTRY_KEYS, known_entry_data
from glucocraft.storage import GlucoCraftStorage, GlucoseReading
from glucocraft.units import UNITLESS_MMOL_BELOW, looks_like_mmol

import ha_stub
from conftest import LA, FakeCoordinator, real_coordinator
from test_entity_ids import _load_init

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "glucocraft"
STRINGS = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))
REMOVED_KEYS = {"display_word_mode": "trend", "projection_acknowledged": True, "custom_gri_formula": True}


# ------------------------------------------------------------------------------------------------ known keys only
def test_known_entry_data_keeps_only_known_keys():
    data = {C.CONF_GLUCOSE_ENTITY: "sensor.p", C.CONF_UNIT: "mg/dL", C.CONF_PANEL_WEBHOOK_ID: "w", **REMOVED_KEYS}
    assert known_entry_data(data) == {
        C.CONF_GLUCOSE_ENTITY: "sensor.p",
        C.CONF_UNIT: "mg/dL",
        C.CONF_PANEL_WEBHOOK_ID: "w",
    }
    assert not set(REMOVED_KEYS) & ENTRY_KEYS


def test_a_configure_save_drops_unknown_keys():
    from glucocraft import config_flow as cf

    saved = []

    async def reload(entry_id):
        return True

    stored = {C.CONF_GLUCOSE_ENTITY: "sensor.p", C.CONF_STALE_THRESHOLD: 15, **REMOVED_KEYS}
    flow = cf.GlucoCraftOptionsFlow(types.SimpleNamespace(data=dict(stored), entry_id="t"))
    flow.hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(async_update_entry=lambda e, data: saved.append(data), async_reload=reload)
    )
    flow._updates = {C.CONF_PRINTER: "ipp://printer.local"}
    flow.async_create_entry = lambda **kw: ("entry", kw)
    asyncio.run(flow._async_finish())
    assert saved[-1] == {
        C.CONF_GLUCOSE_ENTITY: "sensor.p",
        C.CONF_STALE_THRESHOLD: 15,
        C.CONF_PRINTER: "ipp://printer.local",
    }


def test_the_unit_listener_drops_unknown_keys(tmp_path, monkeypatch):
    c = real_coordinator(tmp_path)
    ha_stub.ENTITY_REGISTRY.put(
        "sensor.glucocraft_last_glucose_reading",
        "t_current_glucose",
        options={"sensor": {"unit_of_measurement": "mmol/L"}},
    )
    monkeypatch.setattr(
        co, "current_glucose_entity_id", lambda hass, entry_id: "sensor.glucocraft_last_glucose_reading"
    )
    monkeypatch.setattr(co, "apply_display_unit", lambda *a: None)
    entry = types.SimpleNamespace(data={C.CONF_GLUCOSE_ENTITY: "sensor.p", C.CONF_UNIT: "mg/dL", **REMOVED_KEYS})
    saved = []
    c.hass.config_entries = types.SimpleNamespace(
        async_get_entry=lambda entry_id: entry, async_update_entry=lambda e, data: saved.append(data)
    )
    c._config_unit = "mg/dL"
    c._async_unit_setting_changed(types.SimpleNamespace(data={"action": "update", "changes": {"options": {}}}))
    assert saved == [{C.CONF_GLUCOSE_ENTITY: "sensor.p", C.CONF_UNIT: "mmol/L"}]


def test_a_new_panel_address_drops_unknown_keys(monkeypatch):
    saved = []
    monkeypatch.setattr(pf.persistent_notification, "async_create", lambda *a, **kw: None, raising=False)
    entry = types.SimpleNamespace(data={C.CONF_PANEL_FEED: True, **REMOVED_KEYS}, runtime_data=None)
    hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(async_update_entry=lambda e, data: saved.append(data))
    )
    webhook_id = pf.ensure_webhook_id(hass, entry)
    assert saved == [{C.CONF_PANEL_FEED: True, C.CONF_PANEL_WEBHOOK_ID: webhook_id}]


# ------------------------------------------------------------------------------------------------ stale entities
def _entity(entity_id: str, unique_id: str):
    return types.SimpleNamespace(entity_id=entity_id, unique_id=unique_id, expected_entity_id=entity_id)


def test_registry_entries_the_setup_does_not_create_are_removed(caplog):
    reg = ha_stub.ENTITY_REGISTRY
    hass = types.SimpleNamespace(data={})
    entry = types.SimpleNamespace(entry_id="e1")
    for entity_id, uid in (
        ("sensor.glucocraft_last_glucose_reading", "e1_current_glucose"),
        ("binary_sensor.glucocraft_low", "e1_low"),
        ("sensor.glucocraft_smoothed_glucose", "e1_smoothed_glucose"),  # an entity the code does not create
        ("sensor.glucocraft_14d_custom_gri_formula", "e1_14d_custom_gri_formula"),  # an entity the code does not create
        ("sensor.glucocraft_14d_custom_ranges", "e1_14d_custom_ranges"),  # an optional feature that is off
    ):
        reg.put(entity_id, uid, config_entry_id="e1")
    reg.put("sensor.other", "x", platform="other", config_entry_id="other_entry")  # another entry's entity
    stale_entities.start_setup(hass, entry)
    stale_entities.record_created(
        hass, entry, "sensor", [_entity("sensor.glucocraft_last_glucose_reading", "e1_current_glucose")]
    )
    stale_entities.record_created(hass, entry, "binary_sensor", [_entity("binary_sensor.glucocraft_low", "e1_low")])
    with caplog.at_level(logging.INFO):
        assert stale_entities.async_remove_stale_entities(hass, entry) == 3
    assert set(reg.entities) == {
        "sensor.glucocraft_last_glucose_reading",
        "binary_sensor.glucocraft_low",
        "sensor.other",
    }
    assert "removed 3 entities" in caplog.text
    assert entry.entry_id not in hass.data[stale_entities._DATA_CREATED]  # nothing kept for the next setup


def test_nothing_is_removed_unless_both_platforms_reported():
    reg = ha_stub.ENTITY_REGISTRY
    hass = types.SimpleNamespace(data={})
    entry = types.SimpleNamespace(entry_id="e1")
    reg.put("binary_sensor.glucocraft_low", "e1_low", config_entry_id="e1")
    reg.put("sensor.glucocraft_smoothed_glucose", "e1_smoothed_glucose", config_entry_id="e1")
    stale_entities.start_setup(hass, entry)
    stale_entities.record_created(hass, entry, "sensor", [])  # the binary sensor platform failed
    assert stale_entities.async_remove_stale_entities(hass, entry) == 0
    assert len(reg.entities) == 2
    assert stale_entities.async_remove_stale_entities(hass, entry) == 0  # without start_setup: nothing


def test_the_setup_runs_the_clean_up_after_the_platforms():
    import inspect

    src = inspect.getsource(_load_init().async_setup_entry)
    assert (
        src.index("start_setup(") < src.index("async_forward_entry_setups") < src.index("async_remove_stale_entities(")
    )
    sensor_src = (PKG / "sensor.py").read_text(encoding="utf-8") + (PKG / "binary_sensor.py").read_text(
        encoding="utf-8"
    )
    assert sensor_src.count("record_created(hass, entry,") == 2 and "remove_switched_off" not in sensor_src


# ------------------------------------------------------------------------------------------------ the HTTP API
class _Request:
    def __init__(self, hass, query=None):
        self.app = {http_api.KEY_HASS: hass}
        self.query = query or {}


def _api_hass(coordinator, jobs):
    async def run(fn, *args):
        jobs.append(fn)
        return fn(*args)

    entry = types.SimpleNamespace(runtime_data=coordinator)
    return types.SimpleNamespace(
        async_add_executor_job=run, config_entries=types.SimpleNamespace(async_entries=lambda domain: [entry])
    )


def _view(cls):
    view = cls()
    view.json = lambda data, **kw: data
    return view


@pytest.fixture
def api_coordinator(tmp_path, monkeypatch):
    """A loaded coordinator with 30 days of readings every 5 minutes up to now, the newest 129.46 mg/dL."""
    import time

    c = real_coordinator(tmp_path)
    monkeypatch.setattr(http_api, "loaded_coordinator", lambda hass: c)
    now = int(time.time())
    start = now - 30 * 86400
    for k, ts in enumerate(range(start, now, 300)):
        c.storage.insert_reading(ts, 100.0 + (k % 7) * 0.3, None, C.SOURCE_PRIMARY)
    c.current_glucose = 129.46
    c.last_reading_ts = now - 7 * 3600  # 7 hours old: not current
    return c


def test_data_publishes_unrounded_values_and_the_last_reading_time(api_coordinator):
    jobs = []
    hass = _api_hass(api_coordinator, jobs)
    out = asyncio.run(_view(http_api.GlucoCraftDataView).get(_Request(hass, {"period": "30d"})))
    assert out["current"] == 129.46 and out["last_reading_ts"] == api_coordinator.last_reading_ts
    assert len(out["readings"]) == 400 and all({"t", "r", "min", "max"} <= set(s) for s in out["readings"])
    means = [s["r"] for s in out["readings"]]
    assert any(round(m, 1) != m for m in means)  # unrounded slot means
    # the slots are made in the executor job, never in the event loop
    assert jobs == [http_api._chart_readings]
    stats = asyncio.run(_view(http_api.GlucoCraftStatsView).get(_Request(hass)))
    assert stats["current"] == 129.46 and stats["last_reading_ts"] == api_coordinator.last_reading_ts


def test_mmol_values_are_unrounded_and_compare_as_in_mgdl(api_coordinator, monkeypatch):
    monkeypatch.setattr(type(api_coordinator), "display_unit", property(lambda self: "mmol/L"))
    out = asyncio.run(
        _view(http_api.GlucoCraftDataView).get(_Request(_api_hass(api_coordinator, []), {"period": "6h"}))
    )
    assert out["current"] == 129.46 / 18.016
    assert out["ranges"]["low"] == 70 / 18.016  # the limits with the same factor: 70 mg/dL equals the low limit
    assert all("l" not in r for r in out["readings"])
    assert http_api._glucose(None, "mmol/L") is None and http_api._glucose(70, "mg/dL") == 70.0


def test_data_without_a_reading_has_no_reading_time(api_coordinator):
    api_coordinator.current_glucose, api_coordinator.last_reading_ts = None, 0
    out = asyncio.run(_view(http_api.GlucoCraftDataView).get(_Request(_api_hass(api_coordinator, []))))
    assert out["current"] is None and out["last_reading_ts"] is None


def test_the_report_views_share_their_parameters():
    c = FakeCoordinator()
    c.custom_ranges_and_goals = False
    # a period that does not exist is refused (400 with its text), never replaced by the 14-day report
    days, options, refused = http_api._report_query(types.SimpleNamespace(query={"days": "x"}), c)
    assert refused is not None
    days, options, refused = http_api._report_query(types.SimpleNamespace(query={}), c)
    assert days == 14 and options == (False, False) and refused is None
    days, options, refused = http_api._report_query(
        types.SimpleNamespace(query={"days": "90", "include_custom_appendix": "1"}), c
    )
    assert days == 90 and options == (True, False) and refused is not None


# ------------------------------------------------------------------------------------------------ CSV LO / HI
def test_the_export_writes_lo_and_hi_and_the_import_reads_them_back():
    readings = [
        GlucoseReading(1_790_000_000, 40.0, "low", C.SOURCE_PRIMARY),
        GlucoseReading(1_790_000_300, 101.4, None, C.SOURCE_PRIMARY),
        GlucoseReading(1_790_000_600, 400.0, "high", C.SOURCE_PRIMARY),
    ]
    for unit in ("mg/dL", "mmol/L"):
        text = export.readings_csv(readings, unit, LA)
        rows = [ln.split(",") for ln in text.strip().split("\n")]
        assert [r[3] for r in rows[1:]] == ["LO", "101" if unit == "mg/dL" else "5.6", "HI"]
        assert [r[2] for r in rows[1:]] == ["", "101.4", ""]  # no number for a limit reading
        back, left_out = export.read_readings_csv(text, (40, 400))  # stored again at the sources' limits, by the flag
        assert left_out == 0 and back == readings
        # without the stored value: the display value, and a LO / HI row at the sources' limits
        reduced = "\n".join(",".join(r[:2] + r[3:]) for r in rows)
        back, left_out = export.read_readings_csv(reduced, (50, 400))
        assert left_out == 0
        assert [(r.raw, r.limit) for r in back] == [
            (50.0, "low"),
            (101.0 if unit == "mg/dL" else 100.9, None),
            (400.0, "high"),
        ]


# ------------------------------------------------------------------------------------------------ one rule each
def test_span_days_rounds_halves_away_from_zero(tmp_path):
    s = GlucoCraftStorage(str(tmp_path), "t")
    s.load()
    s.insert_reading(1_790_000_000, 100.0, None, 1)
    s.insert_reading(1_790_000_000 + 21_600, 100.0, None, 1)  # 0.25 days
    assert diagnostics.stored_readings_summary(s)["span_days"] == 0.3


def test_one_stored_span():
    from glucocraft import storage

    assert C.STORED_MGDL[1] == DEFAULT_UPPER
    assert storage._plausible(1_790_000_000, 5000, 0, 1, None) and not storage._plausible(
        1_790_000_000, 5001, 0, 1, None
    )
    assert storage._plausible(1_790_000_000, 200, 0, 1, None) and not storage._plausible(1_790_000_000, 199, 0, 1, None)
    for name, literal in (
        ("panel_feed.py", "r.raw <= 500"),
        ("export.py", "20.0, 500.0"),
        ("storage.py", "200 <= raw_x10 <= 5000"),
    ):
        src = (PKG / name).read_text(encoding="utf-8")
        assert literal not in src and "STORED_MGDL" in src, name


def test_the_unitless_mmol_rule_is_one_function():
    assert looks_like_mmol(29.9) and not looks_like_mmol(UNITLESS_MMOL_BELOW) and not looks_like_mmol(0)
    # the live path and the history import share history_import.unitless_mmol, which reads units.looks_like_mmol
    for name, rule in (("coordinator.py", "unitless_mmol("), ("history_import.py", "looks_like_mmol(")):
        src = (PKG / name).read_text(encoding="utf-8")
        assert rule in src and "< 30" not in src, name
    src = (PKG / "coordinator.py").read_text(encoding="utf-8")
    assert src.count("0.75") == 1 and src.count("SAME_MEASUREMENT_SHARE *") == 2  # the constant and its two uses
    report_pdf = (PKG / "report_pdf.py").read_text(encoding="utf-8")
    assert "10.929 *" not in report_pdf and 'factor="10.929"' not in report_pdf


def test_the_sensor_expiry_uses_the_status_rule():
    src = (PKG / "sensor.py").read_text(encoding="utf-8")
    assert "hours <= 24" not in src and "EXPIRING_SOON_MINUTES * 60" in src


# ------------------------------------------------------------------------------------------------ the removal
def test_the_removal_deletes_only_the_names_glucocraft_writes(tmp_path):
    init = _load_init()
    exports, reports = tmp_path / C.EXPORT_DIR, tmp_path / "glucocraft_reports"
    exports.mkdir()
    reports.mkdir()
    own = [
        exports / "glucocraft-readings_2026-10-01_080000.csv",
        exports / "glucocraft-lab-a1c_2026-10-01_080000.csv",
        exports / "glucocraft-readings_2026-10-02_080000.csv.tmp",
        reports / "glucocraft-cgm-report_2026-10-01_0800_14d_summary_color.pdf",
        reports / "glucocraft-cgm-report_2026-10-01_0800_180d_full_grayscale.pdf.tmp",
    ]
    foreign = [exports / "my_old_export_copy.csv", reports / "doctor_letter.pdf", exports / "notes.txt"]
    for p in own + foreign:
        p.write_text("x")
    hass = types.SimpleNamespace(config=types.SimpleNamespace(path=lambda *p: str(Path(tmp_path, *p))))
    removed = init._remove_data_files(hass, "e1")
    assert not any(p.exists() for p in own) and all(p.exists() for p in foreign)
    assert len(removed) == len(own)


# ------------------------------------------------------------------------------------------------ Repairs notices
def _issue(issue_id):
    return ha_stub.ISSUE_REGISTRY.issues.get((C.DOMAIN, issue_id))


class _Resources:
    def __init__(self, fail=False):
        self.items, self.fail, self.loaded = [], fail, True

    def async_items(self):
        return list(self.items)

    async def async_create_item(self, data):
        if self.fail:
            raise RuntimeError("storage is read-only")
        self.items.append({"id": str(len(self.items)), **data})


def test_a_failed_card_registration_opens_a_notice_and_a_good_one_closes_it(caplog, monkeypatch):
    resources = _Resources(fail=True)
    hass = types.SimpleNamespace(data={"lovelace": types.SimpleNamespace(resources=resources)})
    assert asyncio.run(frontend._async_register_lovelace_resources(hass, "/glucocraft_v/abc")) is False
    issue = _issue(C.RESOURCES_ISSUE_ID)
    assert issue.translation_placeholders == {"error": "storage is read-only"} and issue.is_persistent is False
    resources.fail = False
    assert asyncio.run(frontend._async_register_lovelace_resources(hass, "/glucocraft_v/abc")) is True
    assert _issue(C.RESOURCES_ISSUE_ID) is None
    assert {i["res_type"] for i in resources.items} == {"module"} and len(resources.items) == len(frontend.CARD_FILES)


def test_the_frontend_line_only_when_the_cards_were_registered(caplog, monkeypatch):
    async def version(hass):
        return frontend.FrontendVersion("abc", "0.0.0+abc", ())

    async def failed(hass, base):
        return False

    monkeypatch.setattr(frontend, "async_frontend_version", version)
    monkeypatch.setattr(frontend, "_async_register_lovelace_resources", failed)

    async def static(paths):
        return None

    hass = types.SimpleNamespace(data={}, http=types.SimpleNamespace(async_register_static_paths=static))
    with caplog.at_level(logging.INFO):
        asyncio.run(frontend.async_register_frontend(hass))
    assert "frontend registered" not in caplog.text
    assert "isinstance(lovelace_data, dict)" not in (PKG / "frontend.py").read_text(encoding="utf-8")
    assert '"type": "module"' not in (PKG / "frontend.py").read_text(encoding="utf-8")


def test_a_failed_history_import_opens_a_notice(tmp_path, monkeypatch):
    async def broken(*a, **kw):
        raise RuntimeError("database is locked for sensor.jane_glucose")

    async def empty(*a, **kw):
        return []

    hass = types.SimpleNamespace(states=types.SimpleNamespace(get=lambda e: None))
    storage = GlucoCraftStorage(str(tmp_path), "t")
    monkeypatch.setattr(history_import, "async_rows_kept", broken)
    assert asyncio.run(history_import.async_import_history(hass, "sensor.jane_glucose", storage)) == 0
    issue = _issue(C.HISTORY_IMPORT_ISSUE_ID)
    assert issue.translation_placeholders == {"error": "RuntimeError"}  # never the error's text (an entity id)
    monkeypatch.setattr(history_import, "async_rows_kept", empty)
    asyncio.run(history_import.async_import_history(hass, "sensor.jane_glucose", storage))
    assert _issue(C.HISTORY_IMPORT_ISSUE_ID) is None


@pytest.mark.parametrize("issue_id", [C.RESOURCES_ISSUE_ID, C.HISTORY_IMPORT_ISSUE_ID])
def test_the_new_notices_have_texts(issue_id):
    text = STRINGS["issues"][issue_id]
    assert text["title"] and "{error}" in text["description"] and "closes" in text["description"]


# ------------------------------------------------------------------------------------------------ action errors
def test_action_error_texts_are_plain_text():
    for key, value in STRINGS["exceptions"].items():
        assert "*" not in value["message"] and "`" not in value["message"], key


@pytest.mark.parametrize(
    "handler, key",
    [
        ("_handle_send_daily", "summary_failed"),
        ("_handle_send_weekly", "summary_failed"),
        ("_handle_send_monthly", "summary_failed"),
        ("_handle_remove_lab", "lab_remove_failed"),
    ],
)
def test_a_failed_action_is_a_translated_error(monkeypatch, handler, key):
    init = _load_init()

    async def broken(*a, **kw):
        raise OSError(28, "No space left on device")

    for name in ("async_send_daily_summary", "async_send_weekly_summary", "async_send_monthly_summary"):
        monkeypatch.setattr(init, name, broken)
    coordinator = types.SimpleNamespace(reports=types.SimpleNamespace(async_remove_lab=broken))
    monkeypatch.setattr(init, "_coordinator_for", lambda hass: coordinator)
    with pytest.raises(ha_stub.HomeAssistantError) as err:
        asyncio.run(getattr(init, handler)(None, types.SimpleNamespace(data={"date": "2026-10-01"})))
    assert err.value.translation_key == key and err.value.translation_domain == C.DOMAIN
    assert err.value.translation_placeholders == {"reason": "[Errno 28] No space left on device"}
    assert "{reason}" in STRINGS["exceptions"][key]["message"]


def test_any_failed_export_is_a_translated_error(tmp_path, monkeypatch, caplog):
    from glucocraft.maintenance import MaintenanceIssues

    init = _load_init()

    def broken(*a, **kw):
        raise ValueError("bad value")

    monkeypatch.setattr(init, "write_export", broken)
    coordinator = types.SimpleNamespace(
        reports=types.SimpleNamespace(lab_results=[]),
        display_unit="mg/dL",
        storage=types.SimpleNamespace(readings=[]),
        locked=lambda fn, *a: fn(*a),
        maintenance=MaintenanceIssues(None, lambda: None),
    )
    monkeypatch.setattr(init, "_coordinator_for", lambda hass: coordinator)

    async def run(fn, *args):
        return fn(*args)

    hass = types.SimpleNamespace(
        config=types.SimpleNamespace(path=lambda *p: str(Path(tmp_path, *p)), time_zone="UTC"),
        async_add_executor_job=run,
    )
    with caplog.at_level("WARNING"), pytest.raises(ha_stub.HomeAssistantError) as err:
        asyncio.run(init._handle_export(hass, types.SimpleNamespace(data={})))
    assert err.value.translation_key == "export_failed" and isinstance(err.value.__cause__, ValueError)
    assert "The CSV export failed: bad value" in caplog.text  # the system's own text: in the log only
    assert err.value.translation_placeholders == {"reason": "an unexpected error; Home Assistant's log names it"}


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (OSError(28, "No space left on device"), "the disk is full"),
        (
            PermissionError(13, "Permission denied", "/config/glucocraft_exports"),
            "Home Assistant has no permission to write there",
        ),
        (
            FileExistsError(17, "File exists", "/config/glucocraft_exports"),
            "the folder or a file in it could not be written",
        ),
        (RuntimeError("boom"), "an unexpected error; Home Assistant's log names it"),
    ],
)
def test_a_failed_export_states_its_reason_in_plain_words(error, reason):
    """The Maintenance status and the action's message name the reason in plain words, never the system's error text
    with its paths (that text is logged: test_any_failed_export_is_a_translated_error)."""
    assert _load_init()._export_reason(error) == reason


# ------------------------------------------------------------------------------------------------ panel notices
def test_device_updates_that_concern_no_panel_are_left_alone():
    devices = {
        "panel": types.SimpleNamespace(manufacturer="GlucoCraft", identifiers={("esphome", "x")}),
        "own": types.SimpleNamespace(manufacturer="GlucoCraft", identifiers={(C.DOMAIN, "e1")}),
        "lamp": types.SimpleNamespace(manufacturer="IKEA", identifiers={("zha", "y")}),
    }
    registry = types.SimpleNamespace(async_get=devices.get)
    dr = panel_notice.dr
    original = dr.async_get
    dr.async_get = lambda hass: registry
    try:

        def concerns(action, device_id):
            return panel_notice._concerns_panels(
                None, types.SimpleNamespace(data={"action": action, "device_id": device_id})
            )

        assert concerns("update", "panel") and concerns("create", "panel")
        assert not concerns("update", "lamp") and not concerns("update", "own") and not concerns("create", "gone")
        assert concerns("remove", "lamp")  # a removed device may have been a panel
    finally:
        dr.async_get = original


# ------------------------------------------------------------------------------------------------ ids and stores
def _literal_pattern(node: ast.expr, module) -> re.Pattern | None:
    """A notification id expression as a pattern: a string, a module constant, or an f-string (its fields any text)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return re.compile(re.escape(node.value))
    if isinstance(node, ast.Name) and isinstance(getattr(module, node.id, None), str):
        return re.compile(re.escape(getattr(module, node.id)))
    if isinstance(node, ast.JoinedStr):
        parts = [re.escape(v.value) if isinstance(v, ast.Constant) else ".+" for v in node.values]
        return re.compile("".join(parts))
    return None


def test_every_notification_id_of_the_package_is_removed():
    """Every notification_id the package writes (keyword or dictionary key) is one of the removal's ids, or a panel
    prefix followed by the device id."""
    import importlib

    init = _load_init()
    known = [*init.NOTIFICATION_IDS, *(f"{p}DEVICE" for p in init.PANEL_NOTIFICATION_PREFIXES)]
    found = 0
    for path in sorted(PKG.glob("*.py")):
        module = importlib.import_module(f"glucocraft.{path.stem}") if path.stem != "__init__" else init
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            values = []
            if isinstance(node, ast.keyword) and node.arg == "notification_id":
                values.append(node.value)
            if isinstance(node, ast.Dict):
                values += [
                    v
                    for k, v in zip(node.keys, node.values, strict=True)
                    if isinstance(k, ast.Constant) and k.value == "notification_id"
                ]
            for value in values:
                pattern = _literal_pattern(value, module)
                if pattern is None:  # a name of the function (the panel notices' _nid, a summary's kind): its source
                    continue
                found += 1
                assert any(pattern.fullmatch(k) for k in known), (path.name, ast.unparse(value))
    assert found >= 7
    assert len(set(init.NOTIFICATION_IDS)) == len(init.NOTIFICATION_IDS)
    assert {"glucocraft_daily_summary", "glucocraft_report_print", "glucocraft_panel_webhook"} <= set(
        init.NOTIFICATION_IDS
    )


def test_the_store_suffixes_come_from_their_modules():
    init = _load_init()
    assert set(init.ENTRY_STORE_SUFFIXES) == {"state", "panels"}
    assert not hasattr(init, "ADMIN_SERVICES") and "gmi_ok" not in vars(co.GlucoCraftCoordinator)


def test_no_test_seams_in_the_registry_calls():
    for path in sorted(PKG.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        assert "(tests)" not in src, path.name
        assert 'getattr(r, "limit", None)' not in src or path.name == "report.py", path.name
