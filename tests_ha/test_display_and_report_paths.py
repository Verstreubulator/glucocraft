# License: AGPL-3.0-only (LICENSE).
"""The display rules, the Maintenance status, the wall-panel notices, the dashboard and the CGM Report endpoints on
Home Assistant itself.

Rules under test:
- An expired sensor that still delivers: the status is Sensor expired, but the data are current (the no-current-data
  flag is off), so the value and the trend are shown and the daily summary gives the reading with "Sensor expired",
  never "No current data since". Once the reading is older than the limit, the flag decides: no current data.
- The no-current-data limit is 5 to 30 minutes; a stored 60 (an earlier span) is read as 30, everywhere.
- The Maintenance status (the Configuration sensor's maintenance_issues) counts exactly a wall panel offline for 10
  minutes, an open GlucoCraft Repairs issue (not an ignored one) and a failed CSV export; no current data and an
  expired sensor alone count nothing.
- A wall panel (a device with the manufacturer "glucocraft"): one "Panel Added" notice on its first appearance, an
  offline notice after 10 minutes off, removed when it is back; deleting its device removes the notice and forgets
  the panel.
- The GRI only from 14 days: no 6-hour, 24-hour or 7-day GRI entity, and no GRI value in those periods.
- The dashboard is kept through a reload, removed for a disabled entry and when turned off, back when turned on, and
  removed when GlucoCraft is deleted.
- The CGM Report endpoints: a login is required; days outside 14 / 30 / 90 / 180 give the 14-day report; the PDF's
  file name is built by the server and a copy is kept; the custom appendix answers 400 while Custom ranges and goals
  is off; 404 while GlucoCraft is not loaded.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from homeassistant.components import persistent_notification
from homeassistant.components.frontend import DATA_PANELS
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import InvalidData
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from conftest import (
    DOMAIN,
    LAST_READING,
    SOURCE,
    entity_ids,
    new_reading,
    open_screen,
    run_setup,
    save_screen,
    set_source,
)
from custom_components.glucocraft.coordinator import NOT_GLUCOSE_UNIT_ISSUE_PREFIX

INTEGRATION = Path(__file__).resolve().parent.parent / "custom_components" / DOMAIN
EXPIRY = "sensor.test_cgm_sensor_expiry"
STATUS = "sensor.glucocraft_status"
NO_CURRENT_DATA = "binary_sensor.glucocraft_no_current_data"
CONFIGURATION = "sensor.glucocraft_configuration"
TREND = "sensor.glucocraft_trend_arrow"
DASHBOARD = "glucocraft-home"


def text(key: str, **values: Any) -> str:
    """A text of GlucoCraft's English catalogue (i18n/en), its placeholders filled."""
    for name in ("backend", "report"):
        catalogue = json.loads((INTEGRATION / "i18n" / "en" / f"{name}.json").read_text(encoding="utf-8"))
        if key in catalogue:
            return catalogue[key].format(**values)
    raise KeyError(key)


async def minutes(hass: HomeAssistant, freezer, count: int) -> None:
    """Let `count` minutes pass, one at a time, so that every per-minute timer runs as on a real system."""
    for _ in range(count):
        freezer.tick(timedelta(minutes=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
    await hass.async_block_till_done()


def maintenance(hass: HomeAssistant) -> list[dict[str, str]]:
    """The Maintenance status as every display reads it: the Configuration sensor's maintenance_issues."""
    return hass.states.get(CONFIGURATION).attributes["maintenance_issues"]


def notifications(hass: HomeAssistant) -> dict[str, dict]:
    """The persistent notifications now shown, by id."""
    return persistent_notification._async_get_or_create_notifications(hass)


async def installed(hass: HomeAssistant, freezer, values=("110", "115", "120", "125")):
    """GlucoCraft set up with a few readings, the newest just arrived."""
    set_source(hass, "100")
    entry = await run_setup(hass)
    for value in values:
        await new_reading(hass, freezer, value)
    return entry


# ---------------------------------------------------------------------------------------- the no-current-data flag


async def test_an_expired_sensor_that_still_delivers_shows_its_reading(hass: HomeAssistant, freezer):
    """The status says the sensor's stated expiry passed; the flag says the data are current, and the flag decides
    what the value, the trend and the daily summary show."""
    hass.states.async_set(EXPIRY, (dt_util.utcnow() - timedelta(hours=2)).isoformat())
    set_source(hass, "100")
    entry = await run_setup(hass)
    await save_screen(hass, entry, "sources", {"sensor_expiry_entity": EXPIRY})
    for value in ("110", "115", "120", "125"):
        await new_reading(hass, freezer, value)
    await minutes(hass, freezer, 1)  # the newest reading is 1 minute old

    assert hass.states.get(STATUS).state == "expired"
    flag = hass.states.get(NO_CURRENT_DATA)
    assert flag.state == "off" and flag.attributes["threshold_minutes"] > 1  # the age is the displays' own
    assert float(hass.states.get(LAST_READING).state) == 125
    assert hass.states.get(TREND).state not in ("unknown", "unavailable")  # the trend follows the data

    await hass.services.async_call(DOMAIN, "send_daily_summary", {}, blocking=True)
    await hass.async_block_till_done()
    summary = notifications(hass)["glucocraft_daily_summary"]["message"]
    assert text("status.expired") in summary
    assert "Last reading: 125 milligrams per deciliter" in summary
    assert "(1 minute before this summary)" in summary
    assert text("notify.no_current_data_since", time="").rstrip(".").strip() not in summary

    # older than the 15-minute limit: no current data, whatever the status
    await minutes(hass, freezer, 15)
    assert hass.states.get(STATUS).state == "expired"
    assert hass.states.get(NO_CURRENT_DATA).state == "on"
    assert hass.states.get(TREND).state == "unknown"
    await hass.services.async_call(DOMAIN, "send_daily_summary", {}, blocking=True)
    await hass.async_block_till_done()
    summary = notifications(hass)["glucocraft_daily_summary"]["message"]
    assert text("notify.no_current_data_since", time="").rstrip(".").strip() in summary
    assert text("status.expired") in summary


async def test_a_stored_no_current_data_limit_of_60_is_read_as_30(hass: HomeAssistant, freezer):
    """An entry that stored 60 minutes (an earlier span allowed up to 60) is read as 30: the flag, its attribute, the
    Configuration sensor and the Sources screen; the screen accepts only 5 to 30."""
    entry = await installed(hass, freezer)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "stale_threshold": 60})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await new_reading(hass, freezer, "130")

    assert hass.states.get(NO_CURRENT_DATA).attributes["threshold_minutes"] == 30
    assert hass.states.get(CONFIGURATION).attributes["no_current_data_minutes"] == 30
    await minutes(hass, freezer, 30)
    assert hass.states.get(NO_CURRENT_DATA).state == "off"  # exactly 30 minutes old: not older than the limit
    await minutes(hass, freezer, 1)
    assert hass.states.get(NO_CURRENT_DATA).state == "on"

    form = await open_screen(hass, entry, "sources")
    field = next(k for k in form["data_schema"].schema if str(k) == "stale_threshold")
    assert field.default() == 30
    hass.config_entries.options.async_abort(form["flow_id"])
    for refused in (4, 31, 60):
        with pytest.raises(InvalidData):
            await save_screen(hass, entry, "sources", {"stale_threshold": refused})
    for accepted in (5, 30):
        await save_screen(hass, entry, "sources", {"stale_threshold": accepted})
        assert entry.data["stale_threshold"] == accepted


# ---------------------------------------------------------------------------------------- the Maintenance status


async def test_no_current_data_and_an_expired_sensor_are_no_maintenance_issue(hass: HomeAssistant, freezer):
    hass.states.async_set(EXPIRY, (dt_util.utcnow() - timedelta(hours=2)).isoformat())
    entry = await installed(hass, freezer)
    await save_screen(hass, entry, "sources", {"sensor_expiry_entity": EXPIRY})
    await minutes(hass, freezer, 20)
    assert hass.states.get(STATUS).state == "expired"
    assert hass.states.get(NO_CURRENT_DATA).state == "on"
    assert maintenance(hass) == []


async def test_an_open_repairs_issue_is_a_maintenance_issue_until_it_closes_or_is_ignored(hass: HomeAssistant, freezer):
    """A source that states a unit other than mg/dL or mmol/L opens GlucoCraft's Repairs issue; the Maintenance status
    lists it by its title, as Repairs shows it, until it closes (a glucose unit again). An ignored issue is not
    counted."""
    await installed(hass, freezer)
    title = json.loads((INTEGRATION / "strings.json").read_text(encoding="utf-8"))["issues"]["source_unit_not_glucose"][
        "title"
    ]
    await new_reading(hass, freezer, "37.0", "°C")
    await hass.async_block_till_done()
    assert maintenance(hass) == [{"kind": "repairs", "title": title}]

    await new_reading(hass, freezer, "130")
    await hass.async_block_till_done()
    assert maintenance(hass) == []

    await new_reading(hass, freezer, "37.0", "°C")
    await hass.async_block_till_done()
    assert len(maintenance(hass)) == 1
    ir.async_ignore_issue(hass, DOMAIN, f"{NOT_GLUCOSE_UNIT_ISSUE_PREFIX}{SOURCE}", True)
    await hass.async_block_till_done()
    assert maintenance(hass) == []


async def test_a_failed_csv_export_is_a_maintenance_issue_until_an_export_succeeds(
    hass: HomeAssistant, freezer, own_config_folder
):
    await installed(hass, freezer)
    blocker = own_config_folder / "glucocraft_exports"
    blocker.write_text("a file where the export folder belongs", encoding="utf-8")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "export_csv", {}, blocking=True, return_response=True)
    await hass.async_block_till_done()
    issues = maintenance(hass)
    assert len(issues) == 1 and issues[0]["kind"] == "csv_export" and issues[0]["reason"], issues

    blocker.unlink()
    await hass.services.async_call(DOMAIN, "export_csv", {}, blocking=True, return_response=True)
    await hass.async_block_till_done()
    assert maintenance(hass) == []


# ---------------------------------------------------------------------------------------- wall panels


def add_panel(hass: HomeAssistant, name: str = "Panel A", model: str = "waveshare-lcd7-800x480"):
    """A GlucoCraft wall panel as ESPHome registers it: a device with the manufacturer "glucocraft" and its Status
    (connectivity) entity, connected. Returns (device, Status entity id)."""
    owner = MockConfigEntry(domain="esphome", title=name)
    owner.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id,
        identifiers={("esphome", name)},
        manufacturer="glucocraft",
        model=model,
        name=name,
    )
    status = (
        er.async_get(hass)
        .async_get_or_create(
            "binary_sensor",
            "esphome",
            f"{name}-status",
            suggested_object_id=f"{name.lower().replace(' ', '_')}_status",
            device_id=device.id,
            config_entry=owner,
        )
        .entity_id
    )
    hass.states.async_set(status, "on", {"device_class": "connectivity"})
    return device, status


async def test_a_wall_panel_added_offline_back_and_deleted(hass: HomeAssistant, freezer, hass_storage):
    entry = await installed(hass, freezer)
    device, status = add_panel(hass)
    await hass.async_block_till_done()
    added = f"glucocraft_panel_added_{device.id}"
    offline = f"glucocraft_panel_offline_{device.id}"
    shown = notifications(hass)
    assert added in shown and shown[added]["title"] == text("panel_notice.added.title")
    connected = text("panel_notice.added.connected", name="Panel A", size="800 × 480", shape="rectangular")
    assert connected in shown[added]["message"]
    assert sum(n.startswith("glucocraft_panel_added_") for n in shown) == 1
    persistent_notification.async_dismiss(hass, added)
    dr.async_get(hass).async_update_device(device.id, sw_version="2")  # not a new panel: no second notice
    await hass.async_block_till_done()
    assert added not in notifications(hass)

    # off for 10 minutes: one offline notice, one Maintenance issue named after the panel
    hass.states.async_set(status, "off", {"device_class": "connectivity"})
    await minutes(hass, freezer, 9)
    assert offline not in notifications(hass) and maintenance(hass) == []
    await minutes(hass, freezer, 2)
    assert notifications(hass)[offline]["title"] == text("panel_notice.offline.title")
    assert maintenance(hass) == [{"kind": "panel_offline", "name": "Panel A"}]

    # renamed while offline: the notice and the issue take the new name at once
    dr.async_get(hass).async_update_device(device.id, name_by_user="Hall Panel")
    await hass.async_block_till_done()
    assert "Hall Panel" in notifications(hass)[offline]["message"]
    assert maintenance(hass) == [{"kind": "panel_offline", "name": "Hall Panel"}]

    # back: the notice is removed and the issue with it
    hass.states.async_set(status, "on", {"device_class": "connectivity"})
    await hass.async_block_till_done()
    assert offline not in notifications(hass) and maintenance(hass) == []

    # offline again, then the device deleted: the notice goes and the panel is forgotten
    hass.states.async_set(status, "unavailable")
    await minutes(hass, freezer, 11)
    assert offline in notifications(hass)
    dr.async_get(hass).async_remove_device(device.id)
    await hass.async_block_till_done()
    assert offline not in notifications(hass) and maintenance(hass) == []
    assert device.id not in hass_storage[f"glucocraft.{entry.entry_id}_panels"]["data"]["known"]


async def test_every_maintenance_issue_together(hass: HomeAssistant, freezer, own_config_folder):
    """A panel offline, an open Repairs issue and a failed export: three issues, in that order."""
    await installed(hass, freezer)
    _device, status = add_panel(hass)
    await hass.async_block_till_done()
    hass.states.async_set(status, "off", {"device_class": "connectivity"})
    await new_reading(hass, freezer, "37.0", "°C")
    (own_config_folder / "glucocraft_exports").write_text("x", encoding="utf-8")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "export_csv", {}, blocking=True, return_response=True)
    await minutes(hass, freezer, 6)
    assert [i["kind"] for i in maintenance(hass)] == ["panel_offline", "repairs", "csv_export"]


# ---------------------------------------------------------------------------------------- the GRI from 14 days


async def test_no_gri_for_periods_shorter_than_14_days(hass: HomeAssistant, freezer):
    set_source(hass, "100")
    entry = await run_setup(hass)
    await save_screen(hass, entry, "features", {"custom_ranges_and_goals": False, "separate_statistics": True})
    for value in ("110", "180", "260", "60", "120", "140"):
        await new_reading(hass, freezer, value)
    await minutes(hass, freezer, 5)  # the statistics pass (every 5 minutes)
    gri = sorted(i for i in entity_ids(hass, entry) if i.endswith("_gri"))
    assert gri == [f"sensor.glucocraft_{p}_gri" for p in ("14d", "180d", "30d", "90d")]
    for period in ("6h", "24h", "7d"):
        attrs = hass.states.get(f"sensor.glucocraft_{period}_consensus_ranges").attributes
        assert attrs["has_data"] is True, period
        assert attrs["gri"] is None and attrs["gri_zone"] is None, period
    # 14 days and longer have a GRI; like every value it waits for 70 % sensor active (six readings here: not yet)
    a14 = hass.states.get("sensor.glucocraft_14d_consensus_ranges").attributes
    assert a14["enough_data"] is False and a14["gri"] is None


# ---------------------------------------------------------------------------------------- the dashboard


def dashboard_shown(hass: HomeAssistant) -> bool:
    """True while GlucoCraft's dashboard is registered: its Lovelace configuration and its sidebar panel."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    in_lovelace = DASHBOARD in hass.data[LOVELACE_DATA].dashboards
    in_sidebar = DASHBOARD in hass.data.get(DATA_PANELS, {})
    assert in_lovelace == in_sidebar
    return in_lovelace


async def test_the_dashboard_through_reload_disable_and_its_setting(hass: HomeAssistant):
    assert await async_setup_component(hass, "lovelace", {})
    set_source(hass, "100")
    entry = await run_setup(hass)
    assert not dashboard_shown(hass)  # set up with the dashboard off
    await save_screen(hass, entry, "report", {"show_dashboard": True})
    assert dashboard_shown(hass)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert dashboard_shown(hass)  # kept through a reload

    assert await hass.config_entries.async_set_disabled_by(entry.entry_id, ConfigEntryDisabler.USER)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not dashboard_shown(hass)  # a disabled entry: no dashboard without data

    assert await hass.config_entries.async_set_disabled_by(entry.entry_id, None)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED and dashboard_shown(hass)

    await save_screen(hass, entry, "report", {"show_dashboard": False})
    assert not dashboard_shown(hass)
    await save_screen(hass, entry, "report", {"show_dashboard": True})
    assert dashboard_shown(hass)

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert not dashboard_shown(hass)


# ---------------------------------------------------------------------------------------- the CGM Report endpoints

PDF_NAME = re.compile(r"glucocraft-cgm-report_\d{4}-\d\d-\d\d_\d{4}_(\d+)d_(full|summary)_(color|grayscale)\.pdf")


async def test_the_report_endpoints(hass: HomeAssistant, freezer, hass_client, hass_client_no_auth, own_config_folder):
    entry = await installed(hass, freezer)
    client = await hass_client()

    assert (await (await hass_client_no_auth()).get("/api/glucocraft/report")).status == 401

    for days, expected in (("30", 30), ("90", 90), ("", 14)):
        response = await client.get("/api/glucocraft/report", params={"days": days})
        assert response.status == 200, days
        data = await response.json()
        assert data["ndays"] == expected and data["label"] == text(f"report.period.{expected}"), days
        assert "custom_appendix" not in data
    assert response.headers["Cache-Control"] == "no-cache"
    # a period the CGM Report does not have is refused with its text, never replaced by the 14-day report
    for days in ("7", "1000", "abc"):
        for path in ("/api/glucocraft/report", "/api/glucocraft/report.pdf"):
            response = await client.get(path, params={"days": days})
            assert response.status == 400, (path, days)
            assert await response.text() == text("report.error.period", days=days), (path, days)

    # the PDF: the file name built by the server from the period and the options; a copy kept
    response = await client.get("/api/glucocraft/report.pdf", params={"full": "1", "color": "0"})
    assert response.status == 200 and response.content_type == "application/pdf"
    pdf = await response.read()
    assert pdf.startswith(b"%PDF-")
    assert len(re.findall(rb"/Type /Page\b(?!s)", pdf)) >= 1
    name = re.fullmatch(r'attachment; filename="(.+)"', response.headers["Content-Disposition"]).group(1)
    match = PDF_NAME.fullmatch(name)
    assert match and match.groups() == ("14", "full", "grayscale"), name
    kept = own_config_folder / "glucocraft_reports" / name
    assert kept.read_bytes() == pdf

    # the custom appendix: refused while Custom ranges and goals is off, given while it is on
    for url in ("/api/glucocraft/report", "/api/glucocraft/report.pdf"):
        response = await client.get(url, params={"include_custom_appendix": "1"})
        assert response.status == 400, url
        assert await response.text() == text("api.custom_appendix_off")
    await save_screen(hass, entry, "features", {"custom_ranges_and_goals": True, "separate_statistics": False})
    response = await client.get("/api/glucocraft/report", params={"include_custom_appendix": "1"})
    assert response.status == 200 and "custom_appendix" in await response.json()

    # not loaded: 404 with the reason
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    for url in ("/api/glucocraft/report", "/api/glucocraft/report.pdf"):
        response = await client.get(url)
        assert response.status == 404 and await response.text() == text("api.no_instance"), url
