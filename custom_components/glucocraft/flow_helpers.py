# License: AGPL-3.0-only (LICENSE).
"""Helpers of the settings screens (config_flow.py) that neither build a form nor run a step.

- GlucoCraft's own entities and a source's integration, from the entity registry: the source fields never offer or
  accept one of GlucoCraft's own entities, and the sensor's limits depend on the integration that delivers a source.
- The report printer's address check (printer_address_valid).
- The delivery test message to the phones (_send_phone_test), which the screens start when *Send a test notification
  now* is ticked.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from typing import Any
from urllib.parse import urlsplit

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .i18n import t

# The delivery test logs under the name of the settings flow that starts it, so that a logger setting for
# config_flow still covers its lines
_LOGGER = logging.getLogger(f"{__package__}.config_flow")


# ---- the sources: never one of GlucoCraft's own entities. Its Last glucose reading is GlucoCraft's own last value;
# as a source it would be stored again as a new reading at every rewrite, so a stopped CGM would look current.
def glucocraft_entity_ids(hass: HomeAssistant) -> list[str]:
    """Every entity of GlucoCraft itself (entity registry platform "glucocraft"), sorted."""
    return sorted(e.entity_id for e in er.async_get(hass).entities.values() if e.platform == DOMAIN)


def is_glucocraft_entity(hass: HomeAssistant, entity_id: str | None) -> bool:
    """True for an entity of GlucoCraft itself."""
    if not entity_id:
        return False
    entry = er.async_get(hass).async_get(entity_id)
    return entry is not None and entry.platform == DOMAIN


def _source_platform(hass: HomeAssistant, entity_id: str) -> str | None:
    """The integration that delivers a source (its entity registry platform); None without a registry entry."""
    entry = er.async_get(hass).async_get(entity_id)
    return entry.platform if entry is not None else None


# ---- the report printer: a host or an IPP / HTTP address, never with a user name or password (they would appear in
# error messages, the log and notifications)
PRINTER_SCHEMES = ("ipp", "ipps", "http", "https")


def _printer_host_valid(host: str) -> bool:
    """True for a host that can exist: an IPv6 or IPv4 address, or a name of at most 253 characters whose labels have 1
    to 63 letters, digits, hyphens or underscores, without a hyphen at either end. All digits and dots is an IPv4
    address, so 999.1.1.1 is no host. Reachable or not is not decided here: the printer may be off when this is
    saved."""
    try:
        if ":" in host:
            ipaddress.IPv6Address(host.partition("%")[0])  # the address without a scope ("%eth0")
        elif re.fullmatch(r"[0-9.]+", host):
            ipaddress.IPv4Address(host)
        else:
            name = host.removesuffix(".")
            return len(name) <= 253 and all(re.fullmatch(r"(?!-)[\w-]{1,63}(?<!-)", label) for label in name.split("."))
    except ValueError:
        return False
    return True


def printer_address_valid(text: Any) -> bool:
    """True for an empty address (no printer) or a host name / IP address that can exist (_printer_host_valid),
    optionally with one of PRINTER_SCHEMES, a port and a path, and without user information."""
    address = str(text or "").strip()
    if not address:
        return True
    try:
        parts = urlsplit(address if "://" in address else f"ipp://{address}")
        port_ok = parts.port is None or 0 < parts.port < 65536  # .port raises ValueError for a malformed port
    except ValueError:
        return False
    return (
        parts.scheme.lower() in PRINTER_SCHEMES
        and bool(parts.hostname)
        and _printer_host_valid(parts.hostname)
        and port_ok
        and parts.username is None
        and parts.password is None
        and "@" not in parts.netloc
    )


# ---- the phones' delivery test
async def _send_phone_test(hass: HomeAssistant, targets: list[str]) -> None:
    """A delivery test message to each chosen notify action (errors are logged, never block saving). It contains no
    glucose value. The settings screens start it as a task of its own, so that saving does not wait for the phones.

    The log names a notify action by its position among the chosen ones, never by its name: a phone's action is named
    after the phone, often after its owner, and the diagnostics redact these names too. Each call waits until the
    action has run (blocking), so that a failure reaches the handler below; Home Assistant would otherwise log it
    itself, with the error's text."""
    targets = list(targets or [])
    for position, name in enumerate(targets, start=1):
        if not hass.services.has_service("notify", name):
            _LOGGER.warning("GlucoCraft delivery test: notify action %d of %d is not available", position, len(targets))
            continue
        try:
            await hass.services.async_call(
                "notify",
                name,
                {"title": t("notify.phone_test.title"), "message": t("notify.phone_test.message")},
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001 - a broken target must not stop the settings from saving
            # the error's class only: the text of a Companion app error names the phone (its device name or its
            # webhook id), which this log line leaves out on purpose
            _LOGGER.warning(
                "GlucoCraft delivery test to notify action %d of %d failed: %s",
                position,
                len(targets),
                err.__class__.__name__,
            )
