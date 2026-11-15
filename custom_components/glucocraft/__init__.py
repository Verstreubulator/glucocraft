# License: AGPL-3.0-only (LICENSE).
"""The GlucoCraft integration: setup, unload, version check and removal of the config entry, and the actions.

GlucoCraft is a Home Assistant integration designed to calculate and display CGM data provided by other integrations;
its calculations are designed to follow the cited published consensus.

All data are stored internally as mg/dL. Display unit conversion happens at the presentation layer only.
"""

from __future__ import annotations

import errno
import logging
import re
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import persistent_notification, webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_CORE_CONFIG_UPDATE
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized, UnknownUser
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from . import coordinator as coordinator_module
from . import kept as kept_module
from . import notification as notification_module
from . import panel_feed as panel_feed_module
from . import panel_notice as panel_notice_module
from .const import (
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_TIME,
    CONF_NOTIFY_WEEKLY_ENABLED,
    CONF_ON_REMOVAL,
    CONF_SHOW_DASHBOARD,
    CONF_UNIT,
    DOMAIN,
    ENTRY_MINOR_VERSION,
    ENTRY_VERSION,
    EXPORT_DIR,
    ON_REMOVAL_KEEP,
    PLATFORMS,
    REPORT_OPTIONS,
)
from .coordinator import GlucoCraftConfigEntry, GlucoCraftCoordinator, loaded_coordinator
from .export import (
    EXPORT_FILE_NAME,
    NotAnExport,
    export_message,
    import_message,
    lab_file_of,
    newest_export,
    read_lab_csv,
    read_readings_file,
    read_text,
    write_export,
)
from .frontend import async_register_frontend, async_unregister_lovelace_resources
from .http_api import async_register_views
from .i18n import async_setup as async_setup_language
from .i18n import t
from .kept import DATA_FOLDER, KEPT_NOTICE_ID
from .kept import adopt as adopt_kept
from .kept import keep as keep_for_reinstall
from .notification import (
    async_send_daily_summary,
    async_send_monthly_summary,
    async_send_weekly_summary,
    async_setup_daily_notification,
    async_setup_no_data_notice,
)
from .panel_feed import async_register_panel_webhook
from .panel_notice import async_setup_panel_notice, async_setup_panel_offline, panel_device_ids
from .renamed_entities import async_setup_entity_id_watch
from .report_service import (
    LAB_STORE_KEY,
    REPORT_DIR,
    REPORT_PREFIX,
    SENSOR_EVENTS_STORE_KEY,
    GlucoCraftReports,
    period_refused,
    report_options,
)
from .settings import notify_hour_minute, setting
from .source_rename import async_setup_source_rename_watch
from .stale_entities import async_remove_stale_entities, start_setup
from .storage import GlucoCraftStorage
from .strategy_dashboard import async_remove_strategy_dashboard, async_setup_strategy_dashboard
from .unit_registry import keep_display_unit

_LOGGER = logging.getLogger(__name__)


# The integration is set up from its config entry only (no YAML configuration)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# The actions (services). They are registered once, in async_setup, so that they exist even while GlucoCraft is not
# loaded (Home Assistant's action-setup rule); each call looks up the loaded entry and fails with a clear message
# when there is none. Every action changes stored data or can send data out of Home Assistant (the summaries go to the
# phones chosen for them, the report to the printer), so every action is for administrators (_require_admin). A
# failure is raised as a translated error (strings.json "exceptions"). The actions and their handlers: _HANDLERS.

# The persistent notifications of the actions (each replaced by the next of its kind)
PRINT_NOTICE_ID = "glucocraft_report_print"
EXPORT_NOTICE_ID = "glucocraft_export"
IMPORT_NOTICE_ID = "glucocraft_import"

SCHEMA_LAB_ADD = vol.Schema(
    {
        vol.Required("date"): cv.date,
        vol.Required("a1c"): vol.All(vol.Coerce(float), vol.Range(min=3.0, max=195.0)),
        vol.Optional("unit", default="percent"): vol.In(["percent", "mmol_mol"]),
    }
)
SCHEMA_LAB_REMOVE = vol.Schema({vol.Required("date"): cv.date})
SCHEMA_PRINT = vol.Schema(
    {
        # any whole number: a period the CGM Report does not have is refused by the handler with its own text
        # (report_service.period_refused), never with a bare list of numbers
        vol.Optional("days", default=14): vol.Coerce(int),
        vol.Optional("report", default="summary"): vol.In(["summary", "full"]),
        vol.Optional("color", default=True): cv.boolean,
        vol.Optional("two_sided", default=True): cv.boolean,
        **{vol.Optional(k, default=False): cv.boolean for k in REPORT_OPTIONS},
    }
)


def _coordinator_for(hass: HomeAssistant) -> GlucoCraftCoordinator:
    """Return the loaded entry's coordinator; raise ServiceValidationError when GlucoCraft is not loaded."""
    coordinator = loaded_coordinator(hass)
    if coordinator is None:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    return coordinator


async def _require_admin(hass: HomeAssistant, call: ServiceCall) -> None:
    """Raise unless an administrator called the action.

    Calls from automations and scripts have no user and are allowed, as Home Assistant's own admin services do.
    """
    user_id = call.context.user_id
    if not user_id:
        return
    user = await hass.auth.async_get_user(user_id)
    if user is None:
        raise UnknownUser(context=call.context, user_id=user_id)
    if not user.is_admin:
        raise Unauthorized(context=call.context)


def _reason(err: Exception) -> str:
    """The reason of a failed action as its message shows it: the error's own text, or its class name without one."""
    return str(err) or err.__class__.__name__


def _export_reason(err: Exception) -> str:
    """The reason of a failed CSV export in plain words, for the action's message and the Maintenance status; the
    system's own error text (with its paths) goes only to the log."""
    if isinstance(err, OSError):
        if err.errno == errno.ENOSPC:
            return t("export.failed.no_space")
        if isinstance(err, PermissionError):
            return t("export.failed.no_permission")
        return t("export.failed.not_writable")
    return t("export.failed.other")


async def _translated(step: Awaitable[Any], key: str) -> Any:
    """Await an action's step; raise a failure as the translated error `key` (strings.json "exceptions", with the
    placeholder {reason}). An error that is already Home Assistant's (translated by the step itself) is raised as it
    is."""
    try:
        return await step
    except HomeAssistantError:
        raise
    except Exception as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key=key, translation_placeholders={"reason": _reason(err)}
        ) from err


async def _handle_send_daily(hass: HomeAssistant, call: ServiceCall) -> None:
    """Send the daily summary now."""
    await _translated(async_send_daily_summary(hass, _coordinator_for(hass)), "summary_failed")


async def _handle_send_weekly(hass: HomeAssistant, call: ServiceCall) -> None:
    """Send the weekly summary now."""
    await _translated(async_send_weekly_summary(hass, _coordinator_for(hass)), "summary_failed")


async def _handle_send_monthly(hass: HomeAssistant, call: ServiceCall) -> None:
    """Send the monthly summary now."""
    await _translated(async_send_monthly_summary(hass, _coordinator_for(hass)), "summary_failed")


async def _handle_add_lab(hass: HomeAssistant, call: ServiceCall) -> None:
    """Add a laboratory A1C result in percent or mmol/mol; the span is checked per unit (report_service). A value
    outside the span is a validation error (lab_invalid); any other failure (the store could not be written) is a
    translated error (lab_add_failed, _translated), as for every other action."""
    coordinator = _coordinator_for(hass)
    try:
        await coordinator.reports.async_add_lab(
            str(call.data["date"]), float(call.data["a1c"]), call.data.get("unit", "percent")
        )
    except ValueError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="lab_invalid", translation_placeholders={"error": str(err)}
        ) from err
    except HomeAssistantError:
        raise
    except Exception as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="lab_add_failed",
            translation_placeholders={"reason": _reason(err)},
        ) from err


async def _handle_remove_lab(hass: HomeAssistant, call: ServiceCall) -> None:
    """Remove the laboratory A1C result of a date."""
    await _translated(_coordinator_for(hass).reports.async_remove_lab(str(call.data["date"])), "lab_remove_failed")


async def _print_notification(hass: HomeAssistant, message: str) -> None:
    """Show the result of a print as GlucoCraft's print notification (one notification, replaced by the next)."""
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {"title": t("notify.print.title"), "message": message, "notification_id": PRINT_NOTICE_ID},
    )


async def _handle_print(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Print the CGM Report as a PDF on the printer in the settings; the two appendices only when chosen.

    The custom appendix exists only while Custom ranges and goals is on; asked for while it is off, the action is
    refused with a message that says so (an appendix left out silently would look like a printed one).
    """
    coordinator = _coordinator_for(hass)
    days = int(call.data.get("days", 14))
    if period_refused(days) is not None:  # a period the CGM Report does not have: refused, never replaced
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="report_period", translation_placeholders={"days": str(days)}
        )
    custom, research = report_options(call.data)
    if custom and not coordinator.custom_ranges_and_goals:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="custom_appendix_off")
    try:
        result = await coordinator.reports.async_print(
            days,
            call.data.get("report", "summary") == "full",
            bool(call.data.get("color", True)),
            bool(call.data.get("two_sided", True)),
            custom,
            research,
        )
    except Exception as err:  # the PDF could not be made (reportlab missing, a render error): re-raised with the reason
        # the notification states it too, as for every other print result: an automation's print fails visibly
        await _print_notification(hass, t("report.print.pdf_failed", reason=_reason(err)))
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="print_pdf_failed",
            translation_placeholders={"reason": _reason(err)},
        ) from err
    await _print_notification(hass, result["message"])
    if not result.get("ok"):
        # a print that did not reach the printer is an error of the action (the notification above says why): with the
        # printer's answer and the PDF kept (or that no copy of it could be kept), or, without a printer address, that
        # none is set (no PDF is made then)
        if not result.get("file"):
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="print_no_printer")
        if result.get("kept") is False:  # its copy could not be written (the notification above says why)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="print_failed_not_kept",
                translation_placeholders={"reason": str(result.get("reason") or "")},
            )
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="print_failed",
            translation_placeholders={"reason": str(result.get("reason") or ""), "file": str(result["file"])},
        )
    return result


async def _handle_export(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Export every stored reading and the laboratory results as CSV (export.py).

    The files go to <config>/glucocraft_exports/; the last 12 exports are kept. A failed export is an issue of the
    Maintenance status until an export succeeds (maintenance.py).
    """
    coordinator = _coordinator_for(hass)
    tz = dt_util.get_time_zone(hass.config.time_zone) or dt_util.DEFAULT_TIME_ZONE
    lab = coordinator.reports.lab_results
    unit = coordinator.display_unit
    folder = hass.config.path(EXPORT_DIR)

    def copy() -> list:
        """Copy the stored readings (executor, under the coordinator lock: a reading may be stored meanwhile)."""
        return list(coordinator.storage.readings)

    try:
        # the export is built and written from the copy, without the lock, so that readings are stored meanwhile
        readings = await hass.async_add_executor_job(coordinator.locked, copy)
        result = await hass.async_add_executor_job(write_export, folder, readings, unit, tz, lab)
    except Exception as err:
        # the folder or a file could not be written (no space, no permission), or the export failed otherwise
        _LOGGER.warning("The CSV export failed: %s", _reason(err))
        reason = _export_reason(err)
        coordinator.maintenance.export_failed(reason)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="export_failed",
            translation_placeholders={"reason": reason},
        ) from err
    coordinator.maintenance.export_succeeded()
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "title": t("notify.export.title"),
            "message": export_message(result),
            "notification_id": EXPORT_NOTICE_ID,
        },
    )
    return {"folder": EXPORT_DIR, "files": result["files"], "readings": result["readings"]}


async def _handle_import(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Read one of GlucoCraft's own CSV exports back in (export.py): the readings, and the laboratory results of the
    laboratory export with the same time stamp when there is one.

    The file is named by its name in <config>/glucocraft_exports/ (no folder; without a name, the newest readings
    export). Stored data win: a reading already stored and a laboratory result of a date already stored are kept as
    they are (coordinator.import_readings, report_service.async_import_lab). Laboratory rows that cannot be read
    (export.read_lab_csv) or are refused (report_service.lab_entry) are left out and counted. The counts are the
    action's response and the notification GlucoCraft import.
    """
    coordinator = _coordinator_for(hass)
    folder = hass.config.path(EXPORT_DIR)
    name = str(call.data.get("file") or "").strip() or await hass.async_add_executor_job(newest_export, folder)
    if not name or Path(name).name != name or not name.lower().endswith(".csv"):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="import_no_file", translation_placeholders={"folder": EXPORT_DIR}
        )
    lab_name = lab_file_of(name)
    span = coordinator.sensor_span()  # the limits a LO / HI row without its stored value is stored at

    def read() -> tuple[list, int, list, int]:
        """Read the readings file and its laboratory file (executor); each with the number of rows not read."""
        readings, left_out = read_readings_file(str(Path(folder, name)), span)  # one row at a time
        lab_path = Path(folder, lab_name) if lab_name else None
        lab, lab_left_out = read_lab_csv(read_text(str(lab_path))) if lab_path and lab_path.is_file() else ([], 0)
        return readings, left_out, lab, lab_left_out

    try:
        readings, left_out, lab, lab_left_out = await hass.async_add_executor_job(read)
    except FileNotFoundError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="import_no_file", translation_placeholders={"folder": EXPORT_DIR}
        ) from err
    except NotAnExport as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="import_not_an_export", translation_placeholders={"file": name}
        ) from err
    except (OSError, UnicodeDecodeError) as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="import_failed",
            translation_placeholders={"reason": _reason(err)},
        ) from err
    counts = await hass.async_add_executor_job(coordinator.locked, coordinator.import_readings, readings)
    lab_counts = await coordinator.reports.async_import_lab(lab, lab_left_out)
    coordinator.async_set_updated_data(coordinator.data)
    result = {"file": name, **counts, "not_read": left_out, **lab_counts}
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "title": t("notify.import.title"),
            "message": import_message(result),
            "notification_id": IMPORT_NOTICE_ID,
        },
    )
    return result


_HANDLERS = {
    "send_daily_summary": (_handle_send_daily, None, SupportsResponse.NONE),
    "send_weekly_summary": (_handle_send_weekly, None, SupportsResponse.NONE),
    "send_monthly_summary": (_handle_send_monthly, None, SupportsResponse.NONE),
    "add_lab_a1c": (_handle_add_lab, SCHEMA_LAB_ADD, SupportsResponse.NONE),
    "remove_lab_a1c": (_handle_remove_lab, SCHEMA_LAB_REMOVE, SupportsResponse.NONE),
    "print_report": (_handle_print, SCHEMA_PRINT, SupportsResponse.OPTIONAL),
    "export_csv": (_handle_export, vol.Schema({}), SupportsResponse.OPTIONAL),
    "import_csv": (_handle_import, vol.Schema({vol.Optional("file"): cv.string}), SupportsResponse.OPTIONAL),
}
# The actions' names (services.yaml and strings.json "services" describe each)
SERVICES = tuple(_HANDLERS)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register the actions once per Home Assistant run."""
    for name, (handler, schema, response) in _HANDLERS.items():

        async def _call(call: ServiceCall, handler=handler):
            """Check that an administrator (or an automation) called the action, then run it."""
            await _require_admin(hass, call)
            return await handler(hass, call)

        hass.services.async_register(DOMAIN, name, _call, schema=schema, supports_response=response)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GlucoCraftConfigEntry) -> bool:
    """Set up GlucoCraft from a config entry.

    The steps run in this order: the language, the readings an earlier installation kept, the coordinator, the
    platforms (then the removal of the registry entries of entities no longer created), the display unit, the
    frontend, the summary notifications and the notices. When a step after the coordinator's start fails, the
    coordinator is shut down
    before the error is raised: Home Assistant does not unload an entry whose setup failed, so its listeners and timer
    would otherwise keep running, and a retry would start a second set.
    """
    # the language catalogues of Home Assistant's language, read once in the executor (i18n.py)
    await async_setup_language(hass)
    # the readings an earlier installation kept, when the setup chose to keep them (kept.py)
    await _async_adopt_kept(hass, entry)

    coordinator = await _async_create_coordinator(hass, entry)
    try:
        entry.runtime_data = coordinator
        start_setup(hass, entry)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        # the registry entries of entities this version does not create (stale_entities.py)
        async_remove_stale_entities(hass, entry)
        # the unit listener needs the Last glucose reading entity in the registry (a fresh installation has it only
        # now)
        coordinator.async_track_unit_setting()
        # the display unit into the entity settings of every glucose sensor without a unit there (a fresh
        # installation, the sensors of an optional feature turned on): the sensors suggest no unit (unit_registry).
        # When Last glucose reading gets its unit here, the listener just started brings the other states up to date.
        keep_display_unit(hass, entry.entry_id, setting(entry.data, CONF_UNIT))

        await _async_setup_frontend(hass, entry)
        _setup_notifications(hass, entry, coordinator)
        await _async_setup_notices(hass, entry, coordinator)
    except Exception:
        await coordinator.async_shutdown()
        raise

    _LOGGER.info("GlucoCraft integration loaded successfully")
    return True


async def _async_adopt_kept(hass: HomeAssistant, entry: GlucoCraftConfigEntry) -> None:
    """Adopt the readings, laboratory results and sensor events that an earlier installation kept when it was deleted
    (kept.adopt): the readings file becomes this entry's, and the two lists go into their stores where these hold
    none. The setup asked first and deleted them when that was chosen (config_flow.async_step_kept)."""
    adopted = await hass.async_add_executor_job(adopt_kept, hass.config.path(DATA_FOLDER), entry.entry_id)
    if adopted is None:
        return
    for key, name, items in zip((LAB_STORE_KEY, SENSOR_EVENTS_STORE_KEY), ("results", "events"), adopted, strict=True):
        store: Store = Store(hass, 1, key)
        if items and not ((await store.async_load()) or {}).get(name):
            await store.async_save({name: items})
    persistent_notification.async_dismiss(hass, KEPT_NOTICE_ID)


async def _async_create_coordinator(hass: HomeAssistant, entry: GlucoCraftConfigEntry) -> GlucoCraftCoordinator:
    """Create the coordinator, load its data, and attach the integration version and the report service.

    A failure after the coordinator has started (its listeners and timer) shuts it down before the error is raised,
    as in async_setup_entry."""
    coordinator = GlucoCraftCoordinator(hass, entry.entry_id, dict(entry.data), config_entry=entry)
    try:
        await coordinator.async_setup()
        # the manifest version, for configuration(), the device's software version and the report (About and
        # License); set before the platforms create the entities
        try:
            coordinator.integration_version = str((await async_get_integration(hass, DOMAIN)).version or "")
        except Exception:  # noqa: BLE001 - only a label
            coordinator.integration_version = ""

        # The CGM Report: report data, PDF, printing, laboratory A1C entries
        coordinator.reports = GlucoCraftReports(hass, coordinator)
        await coordinator.reports.async_load()
    except Exception:
        await coordinator.async_shutdown()
        raise
    return coordinator


async def _async_setup_frontend(hass: HomeAssistant, entry: GlucoCraftConfigEntry) -> None:
    """Register the HTTP API, the wall-panel webhook, the cards and GlucoCraft's dashboard."""
    # HTTP API views (binary for the panels, JSON for the cards): once per Home Assistant run (a reload re-runs setup)
    if not hass.data.get(f"{DOMAIN}_views_registered"):
        async_register_views(hass)
        hass.data[f"{DOMAIN}_views_registered"] = True

    # Wall panels: GLU2 v3 files over a GET-only, local-only webhook (no Home Assistant token in the panel); only once a
    # wall-panel address was created in the settings (or an entry already has one)
    panel_webhook_id = async_register_panel_webhook(hass, entry)
    if panel_webhook_id:
        entry.async_on_unload(lambda: webhook.async_unregister(hass, panel_webhook_id))

    # Register frontend cards (always: users can use the cards on their own dashboards)
    await async_register_frontend(hass)

    # GlucoCraft's dashboard (strategy) in the sidebar - optional, on by default. Never breaks setup.
    if setting(entry.data, CONF_SHOW_DASHBOARD):
        await async_setup_strategy_dashboard(hass)
    else:
        await async_remove_strategy_dashboard(hass)


def _setup_notifications(hass: HomeAssistant, entry: GlucoCraftConfigEntry, coordinator: GlucoCraftCoordinator) -> None:
    """Schedule the summary notifications and start the optional no-data notice."""
    # The summaries go out at the notification time: the daily summary while it is on, and on the weekly day the
    # weekly summary (and every fourth week the monthly one) while that is on; each switch works without the other
    data = entry.data
    if setting(data, CONF_NOTIFY_ENABLED) or setting(data, CONF_NOTIFY_WEEKLY_ENABLED):
        hour, minute = notify_hour_minute(setting(data, CONF_NOTIFY_TIME))
        unsub_notify = async_setup_daily_notification(hass, coordinator, hour=hour, minute=minute)
        if unsub_notify is not None:
            entry.async_on_unload(unsub_notify)
    # the no-data notice: checked on every coordinator update; it does nothing while neither of its two settings is on
    entry.async_on_unload(async_setup_no_data_notice(hass, coordinator))


async def _async_setup_notices(
    hass: HomeAssistant, entry: GlucoCraftConfigEntry, coordinator: GlucoCraftCoordinator
) -> None:
    """Save the data when Home Assistant stops, and start the wall-panel notices and the Maintenance status."""

    # Save storage on Home Assistant shutdown (file I/O in the executor, not the event loop)
    async def _async_save_on_stop(_event) -> None:
        """Save the readings and the state."""
        await coordinator.async_save_all()

    entry.async_on_unload(hass.bus.async_listen_once("homeassistant_stop", _async_save_on_stop))
    # Home Assistant's country, language or time zone changed: the formats derived from them are published at once
    entry.async_on_unload(hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, coordinator.async_core_config_updated))

    # a notice when a wall panel is added: which display card the dashboard shows and where to change it
    await async_setup_panel_notice(hass, entry)
    # a notice when a panel has been offline for 10 minutes (its screen can show old data); such a panel is an issue
    # of the Maintenance status, as are GlucoCraft's open Repairs issues (read now and after every change)
    await async_setup_panel_offline(hass, entry, coordinator.maintenance)
    # a Repairs notice while an entity's id is not the one it was created with (renamed_entities.py)
    async_setup_entity_id_watch(hass, entry)
    # the sources follow a change of their entity ids (source_rename.py)
    async_setup_source_rename_watch(hass, entry, coordinator)
    entry.async_on_unload(coordinator.maintenance.async_start())


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Refuse an entry written by a newer GlucoCraft (a downgrade), so that no data are read in a format this version
    does not know.

    The current entry format (const.ENTRY_VERSION / ENTRY_MINOR_VERSION, 2.3) is the first one: there is no earlier
    format to migrate, and a later change of the format adds its step here.
    """
    if (entry.version, entry.minor_version) > (ENTRY_VERSION, ENTRY_MINOR_VERSION):
        _LOGGER.error(
            "GlucoCraft entry version %s.%s is newer than this version of GlucoCraft supports",
            entry.version,
            entry.minor_version,
        )
        return False
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a GlucoCraft config entry.

    The platforms unload first; only when they did is the coordinator shut down (it saves the readings and stops its
    listeners and timer), so entities that could not be unloaded keep a working coordinator. The timers, listeners and
    the panel webhook registered with entry.async_on_unload are released by Home Assistant. The actions stay
    registered (async_setup) and report that GlucoCraft is not loaded.

    The dashboard and the card resources stay through a reload or an options save (a wall tablet on the GlucoCraft
    dashboard keeps working); a disabled entry removes the dashboard from the sidebar, because it would show no data.
    Everything is removed when the integration is deleted (async_remove_entry).
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is not None:
        await coordinator.async_shutdown()
    if entry.disabled_by:
        await async_remove_strategy_dashboard(hass)
    return True


# Every persistent notification GlucoCraft creates, by id, from the module that creates it; the panel notices add the
# panel's device id to their prefix (tests/test_surface_fixes.py finds every id of the package in these lists)
NOTIFICATION_IDS = (
    *notification_module.NOTIFICATION_IDS,
    PRINT_NOTICE_ID,
    EXPORT_NOTICE_ID,
    IMPORT_NOTICE_ID,
    *panel_feed_module.NOTIFICATION_IDS,
    *kept_module.NOTIFICATION_IDS,
)
PANEL_NOTIFICATION_PREFIXES = panel_notice_module.NOTIFICATION_PREFIXES
# Every Home Assistant store GlucoCraft writes (.storage/<key>): per entry (glucocraft.<entry id>_<suffix>), and the
# report's two stores
ENTRY_STORE_SUFFIXES = (
    *coordinator_module.STORE_SUFFIXES,
    *panel_notice_module.STORE_SUFFIXES,
)
REPORT_STORE_KEYS = (LAB_STORE_KEY, SENSOR_EVENTS_STORE_KEY)
# The folders of the configuration directory that only GlucoCraft writes (readings, CSV exports, report PDFs)
DATA_FOLDERS = (DATA_FOLDER, EXPORT_DIR, REPORT_DIR)
# The names of the report PDFs (report_service.GlucoCraftReports.async_pdf: prefix, creation time, days, kind, color)
# and their temporary files; the export names: export.EXPORT_FILE_NAME
REPORT_FILE_NAME = re.compile(
    rf"{re.escape(REPORT_PREFIX)}[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}_[0-9]{{4}}_[0-9]+d_(full|summary)_(color|grayscale)"
    r"[.]pdf([.]tmp)?"
)


def _remove_data_files(hass: HomeAssistant, entry_id: str) -> list[str]:
    """Delete GlucoCraft's files in the configuration directory (executor); return what was removed (for the log).

    That is the readings file of the entry with its temporary file and the copies of damaged files, every CSV export
    and every report PDF (only the names GlucoCraft gives them: a file of another name, such as a copy a user put in
    the export folder, stays), and then each of DATA_FOLDERS that is empty.
    """
    removed: list[str] = []
    folders = {name: Path(hass.config.path(name)) for name in DATA_FOLDERS}
    for path in GlucoCraftStorage(str(folders[DATA_FOLDER]), entry_id).stored_files():
        path.unlink(missing_ok=True)
        removed.append(path.name)
    for name, own in ((EXPORT_DIR, EXPORT_FILE_NAME), (REPORT_DIR, REPORT_FILE_NAME)):
        folder = folders[name]
        if folder.is_dir():
            for path in [p for p in folder.iterdir() if p.is_file() and own.fullmatch(p.name)]:
                path.unlink(missing_ok=True)
                removed.append(f"{name}/{path.name}")
    for folder in folders.values():
        try:
            folder.rmdir()  # only an empty folder: files someone else put there are never deleted
            removed.append(f"{folder.name}/")
        except OSError:
            pass
    return removed


async def _async_clear_recorder(hass: HomeAssistant, entity_ids: list[str]) -> None:
    """Remove the recorded states and the long-term statistics of the given entities.

    The states with recorder.purge_entities, the statistics with the recorder's clear-statistics task (a sensor's
    statistic id is its entity id). Without a recorder there is nothing to remove; a failure is logged and never stops
    the removal of the rest.
    """
    if not entity_ids or not hass.services.has_service("recorder", "purge_entities"):
        return
    try:
        await hass.services.async_call(
            "recorder", "purge_entities", {"entity_id": entity_ids, "keep_days": 0}, blocking=True
        )
        # the recorder is no dependency (manifest.json after_dependencies only): has_service above makes sure it runs
        from homeassistant.components.recorder import get_instance

        get_instance(hass).async_clear_statistics(entity_ids)
    except Exception as err:  # noqa: BLE001 - the files, stores and notices are removed in any case
        _LOGGER.warning("GlucoCraft: the recorder history of its entities could not be removed: %s", err)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove what GlucoCraft stored or created when the integration is deleted, as the setting "When GlucoCraft is
    deleted" (CONF_ON_REMOVAL) says.

    With "Delete everything", nothing of GlucoCraft's own is left behind:
    - the readings file (with its temporary file and any copy of a damaged file), every CSV export and every report
      PDF, and the folders GlucoCraft created for them when they are empty;
    - every Home Assistant store: the entry's state, the panels already announced, the laboratory A1C results and
      the sensor events;
    - every Repairs issue and persistent notification of GlucoCraft;
    - the GlucoCraft dashboard and the card resources;
    - the recorded history (states) and the long-term statistics of every GlucoCraft entity in Home Assistant's
      recorder (_async_clear_recorder).
    With "Keep the readings for a reinstall" (the default), the same, except that the readings file, the laboratory
    A1C results and the sensor events are kept in the folder glucocraft for a new installation (kept.keep), and a
    notification says so. Without a readings file there is nothing to keep, and everything is removed.
    Home Assistant itself removes the entities, the device and the entry afterwards, so the entities are still in the
    registry here. There is one entry (single_config_entry).
    """
    entity_ids = sorted(e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id))
    await _async_clear_recorder(hass, entity_ids)
    kept = False
    if setting(entry.data, CONF_ON_REMOVAL) == ON_REMOVAL_KEEP:
        lab = (await Store(hass, 1, LAB_STORE_KEY).async_load() or {}).get("results") or []
        events = (await Store(hass, 1, SENSOR_EVENTS_STORE_KEY).async_load() or {}).get("events") or []
        kept = await hass.async_add_executor_job(
            keep_for_reinstall, hass.config.path(DATA_FOLDER), entry.entry_id, list(lab), list(events)
        )
    removed = await hass.async_add_executor_job(_remove_data_files, hass, entry.entry_id)
    keys = [f"glucocraft.{entry.entry_id}_{suffix}" for suffix in ENTRY_STORE_SUFFIXES] + list(REPORT_STORE_KEYS)
    for key in keys:
        await Store(hass, 1, key).async_remove()

    registry = ir.async_get(hass)
    for domain, issue_id in list(registry.issues):
        if domain == DOMAIN:
            ir.async_delete_issue(hass, DOMAIN, issue_id)

    notification_ids = list(NOTIFICATION_IDS) + [
        f"{prefix}{device_id}" for device_id in panel_device_ids(hass) for prefix in PANEL_NOTIFICATION_PREFIXES
    ]
    for notification_id in notification_ids:
        persistent_notification.async_dismiss(hass, notification_id)
    if kept:  # the one notice left: where the kept readings are and how they are deleted
        persistent_notification.async_create(hass, t("notify.kept.message"), t("notify.kept.title"), KEPT_NOTICE_ID)

    await async_remove_strategy_dashboard(hass)
    await async_unregister_lovelace_resources(hass)
    _LOGGER.info(
        "GlucoCraft removed: %d files and folders, %d stores, the recorder history of %d entities, its notices, "
        "dashboard and card resources%s",
        len(removed),
        len(keys),
        len(entity_ids),
        "; the readings, laboratory results and sensor events kept for a reinstall" if kept else "",
    )
