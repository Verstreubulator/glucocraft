# License: AGPL-3.0-only (LICENSE).
"""The Maintenance status: what in GlucoCraft's setup needs checking, for the dashboard's Start and Maintenance pages.

Each of these is one issue:
  - a wall panel offline for 10 minutes: while its offline notice is shown (panel_notice.py);
  - an open GlucoCraft Repairs issue: active and not ignored in Home Assistant's Repairs;
  - a failed CSV export (the action glucocraft.export_csv): until an export succeeds or GlucoCraft restarts.
Not counted: no current data and the sensor expiry, which the status bar, the banners and the status headline show.

One source for every display of the status: the Configuration sensor's attribute maintenance_issues
(coordinator.configuration), a list with one entry per issue, in this order:
  {"kind": "panel_offline", "name": <the panel's name in Home Assistant>}
  {"kind": "repairs", "title": <the issue's title, in Home Assistant's language>}
  {"kind": "csv_export", "reason": <the error of the failed export>}
The Maintenance button on the Start page counts the entries; the Maintenance page's hero card lists each one with where
it is resolved. The list is shown on the dashboard only; the display card and the wall panels show only that a Repairs
issue is open (the Configuration sensor's attribute attention: "Check Home Assistant" in the status bar).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import translation

from .const import DOMAIN

KIND_PANEL_OFFLINE = "panel_offline"
KIND_REPAIRS = "repairs"
KIND_CSV_EXPORT = "csv_export"


def _fill(text: str, placeholders: dict[str, Any] | None) -> str:
    """Return a Repairs title with its placeholders ("{minutes}") filled, as Home Assistant shows it."""
    for key, value in (placeholders or {}).items():
        text = text.replace("{" + key + "}", str(value))
    return text


class MaintenanceIssues:
    """The issues the Maintenance status counts; on_change is called whenever the list changes."""

    def __init__(self, hass: HomeAssistant, on_change: Callable[[], None]) -> None:
        """Start with no issues; the Repairs issues are read by async_start."""
        self.hass = hass
        self._on_change = on_change
        self._panels: dict[str, str] = {}  # device id -> name of each panel offline for 10 minutes
        self._repairs: list[str] = []  # the titles of the open Repairs issues, sorted
        self._export_error: str | None = None  # the reason of the last export, while it failed

    def issues(self) -> list[dict[str, str]]:
        """Return the issues as published (the attribute maintenance_issues): the offline panels by name, the Repairs
        issues by title, then the failed export."""
        out = [{"kind": KIND_PANEL_OFFLINE, "name": name} for name in sorted(self._panels.values(), key=str.casefold)]
        out += [{"kind": KIND_REPAIRS, "title": title} for title in self._repairs]
        if self._export_error is not None:
            out.append({"kind": KIND_CSV_EXPORT, "reason": self._export_error})
        return out

    @property
    def attention(self) -> bool:
        """True while at least one GlucoCraft Repairs issue is open and not ignored by the user (async_refresh_repairs:
        active, without a dismissed version): the Configuration sensor's attribute attention, which makes the display
        card and the wall panels show "Check Home Assistant". The wall panels offline and the failed export do not
        count: they are not Repairs issues."""
        return bool(self._repairs)

    def _set(self, attr: str, value: Any) -> None:
        """Store a new value and report the change; an unchanged value reports nothing."""
        if getattr(self, attr) != value:
            setattr(self, attr, value)
            self._on_change()

    # ------------------------------------------------------------------ wall panels (panel_notice.py)
    def panel_offline(self, device_id: str, name: str) -> None:
        """Count a panel whose offline notice was created."""
        self._set("_panels", {**self._panels, device_id: name})

    def panel_back(self, device_id: str) -> None:
        """Stop counting a panel whose offline notice was removed (it is back, or its device was deleted)."""
        self._set("_panels", {k: v for k, v in self._panels.items() if k != device_id})

    # ------------------------------------------------------------------ the CSV export (__init__.py)
    def export_failed(self, reason: str) -> None:
        """Count a failed CSV export, with its reason."""
        self._set("_export_error", reason)

    def export_succeeded(self) -> None:
        """Stop counting a failed export: the last one succeeded."""
        self._set("_export_error", None)

    # ------------------------------------------------------------------ Repairs
    async def async_refresh_repairs(self) -> None:
        """Read GlucoCraft's open Repairs issues and their titles in Home Assistant's language.

        Open: active and not ignored (an ignored issue has a dismissed version). A title Home Assistant cannot
        translate is the issue's translation key.
        """
        issues = [
            issue
            for (domain, _issue_id), issue in ir.async_get(self.hass).issues.items()
            if domain == DOMAIN and issue.active and issue.dismissed_version is None
        ]
        titles: dict[str, str] = {}
        if issues:
            titles = await translation.async_get_translations(self.hass, self.hass.config.language, "issues", {DOMAIN})
        found = []
        for issue in issues:
            key = issue.translation_key or issue.issue_id
            title = titles.get(f"component.{DOMAIN}.issues.{key}.title") or key
            found.append(_fill(title, issue.translation_placeholders))
        self._set("_repairs", sorted(found, key=str.casefold))

    @callback
    def async_start(self) -> Callable[[], None]:
        """Read the Repairs issues now and again whenever one of GlucoCraft's changes; return the listener's stop."""

        @callback
        def _changed(event: Event) -> None:
            """Read the Repairs issues again after a change of one of GlucoCraft's."""
            if event.data.get("domain") == DOMAIN:
                self.hass.async_create_task(self.async_refresh_repairs())

        self.hass.async_create_task(self.async_refresh_repairs())
        return self.hass.bus.async_listen(ir.EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED, _changed)
