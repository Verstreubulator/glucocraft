# License: AGPL-3.0-only (LICENSE).
"""GlucoCraft's dashboard: a native, tab-free Home Assistant dashboard in the sidebar.

The dashboard's content comes from a frontend dashboard strategy (www/glucocraft-strategy.js, type
"custom:glucocraft"): one Start view plus sub-views with Home Assistant's own back arrows, built only from
GlucoCraft's own cards. This module registers the dashboard the way Home Assistant registers its own "Map" dashboard: a
read-only config entry in the Lovelace data that returns the strategy, plus a Lovelace sidebar panel.

RISK: uses Lovelace internals (LOVELACE_DATA, LovelaceConfig) that are not a public integration API; re-check after
Home Assistant updates. Mode "yaml" = read-only in the UI (users copy cards instead of editing GlucoCraft's dashboard).

A dashboard that cannot be registered opens the Repairs issue DASHBOARD_ISSUE_ID (not persistent: it does not outlast
a restart of Home Assistant), with the reason; a registration that succeeds, and the removal of the dashboard (the
setting turned off, the entry unloaded), close it.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import frontend
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.json import json_bytes, json_fragment

from .const import DASHBOARD_ISSUE_ID, DOMAIN
from .i18n import t

_LOGGER = logging.getLogger(__name__)

URL_PATH = "glucocraft-home"
TITLE = "GlucoCraft"
STRATEGY = {"strategy": {"type": "custom:glucocraft"}}


def _lovelace_bits():
    """Import the Lovelace internals lazily, so a missing / changed API only disables this dashboard."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA
    from homeassistant.components.lovelace.dashboard import LovelaceConfig

    return LOVELACE_DATA, LovelaceConfig


def _not_registered(hass: HomeAssistant, reason: str) -> bool:
    """Log a dashboard that could not be registered, open the Repairs issue DASHBOARD_ISSUE_ID with the reason, and
    return False."""
    _LOGGER.warning("GlucoCraft strategy dashboard not registered: %s", reason)
    ir.async_create_issue(
        hass,
        DOMAIN,
        DASHBOARD_ISSUE_ID,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=DASHBOARD_ISSUE_ID,
        translation_placeholders={"error": reason},
    )
    return False


async def async_setup_strategy_dashboard(hass: HomeAssistant) -> bool:
    """Register the strategy dashboard and return True. Return False if Home Assistant's internals do not allow it:
    the reason is logged as a warning and stated in the Repairs issue DASHBOARD_ISSUE_ID (_not_registered); a
    registration that succeeds closes the issue."""
    try:
        lovelace_data_key, lovelace_config_cls = _lovelace_bits()
        data = hass.data.get(lovelace_data_key)
        if data is None:
            return _not_registered(hass, t("repairs.dashboard.not_ready"))

        class _GlucoCraftStrategyConfig(lovelace_config_cls):
            """Read-only dashboard config: always the GlucoCraft strategy."""

            @property
            def mode(self) -> str:
                """Return "yaml": the dashboard cannot be edited in the user interface."""
                return "yaml"

            async def async_get_info(self) -> dict[str, Any]:
                """Return the dashboard's information: its mode, no stored views."""
                return {"mode": self.mode, "views": 0}

            async def async_load(self, force: bool) -> dict[str, Any]:
                """Return the dashboard's configuration: the GlucoCraft strategy."""
                return dict(STRATEGY)

            async def async_json(self, force: bool) -> json_fragment:
                """Return the dashboard's configuration as JSON."""
                return json_fragment(json_bytes(STRATEGY))

        data.dashboards[URL_PATH] = _GlucoCraftStrategyConfig(
            hass, URL_PATH, {"mode": "yaml", "title": TITLE, "icon": "mdi:diabetes", "url_path": URL_PATH}
        )
        frontend.async_register_built_in_panel(
            hass,
            "lovelace",
            sidebar_title=TITLE,
            sidebar_icon="mdi:diabetes",
            frontend_url_path=URL_PATH,
            config={"mode": "yaml"},
            require_admin=False,
            update=True,
        )
        _LOGGER.info("GlucoCraft strategy dashboard registered at /%s", URL_PATH)
    except Exception as err:  # noqa: BLE001 - a failed dashboard registration must never stop the integration
        return _not_registered(hass, str(err) or err.__class__.__name__)
    ir.async_delete_issue(hass, DOMAIN, DASHBOARD_ISSUE_ID)
    return True


async def async_remove_strategy_dashboard(hass: HomeAssistant) -> None:
    """Remove the strategy dashboard (the setting off, the entry unloaded or removed) and close its Repairs issue."""
    ir.async_delete_issue(hass, DOMAIN, DASHBOARD_ISSUE_ID)
    try:
        lovelace_data_key, _ = _lovelace_bits()
        data = hass.data.get(lovelace_data_key)
        if data is not None:
            data.dashboards.pop(URL_PATH, None)
        frontend.async_remove_panel(hass, URL_PATH, warn_if_unknown=False)  # no log warning when it is absent
    except Exception as err:  # noqa: BLE001 - removing an absent dashboard must not fail the unload
        _LOGGER.debug("GlucoCraft strategy dashboard removal: %s", err)
