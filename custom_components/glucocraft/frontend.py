# License: AGPL-3.0-only (LICENSE).
"""Frontend registration for GlucoCraft Lovelace cards.

The card files are served below a path that contains a hash of their contents: /glucocraft_v/<hash>/glucocraft-ui.js.
Home Assistant's service worker caches by URL and ignores query strings, so a "?v=<hash>" would not make a browser
fetch a changed file; a new path does.

The hash covers the contents of the JS files (the card files and the modules they import: glucocraft-i18n.js and
glucocraft-shared.js) and of the language catalogues. It is computed once per Home Assistant
run, in the executor, when the frontend is first registered (FrontendVersion): if a file has changed since the last
run, every path changes and every browser fetches the new files without a hard reload.

The language catalogues (the folder i18n/, one folder per language, one file per area) are served as a directory
under the same versioned path, /glucocraft_v/<hash>/i18n/<lang>/<area>.json, so a card resolves them relative to its
own URL. Every file of every language folder is served (frontend.json, report.json, documentation, ...); a language
without a file is a 404, and the cards then use English.

An unversioned /glucocraft/... path is also served, for users who added the cards as resources to their own
dashboards by hand, so that those setups keep working.

RISK: the Lovelace resource registration (_lovelace_resources, _resource_items, _async_register_lovelace_resources)
uses Lovelace internals that are not a public integration API: the Lovelace data under the key "lovelace" (the value
of Home Assistant's LOVELACE_DATA), its resource collection and that collection's loaded flag and load method. The
hasattr checks let a collection without these (YAML mode, or a changed internal) fall back to loading the cards as
frontend modules instead of failing. Re-check after Home Assistant updates, together with strategy_dashboard.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, RESOURCES_ISSUE_ID
from .i18n import t

_LOGGER = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).parent
CARDS_DIR = PACKAGE_DIR / "www"
I18N_DIR = PACKAGE_DIR / "i18n"  # the language catalogues: i18n/<lang>/<area>.json

# Unversioned base path, for users who added /glucocraft/... resources to their own dashboards by hand.
UNVERSIONED_URL_BASE = "/glucocraft"

# Versioned path prefix; the content hash follows it: /glucocraft_v/<hash>/<filename>.js
VERSIONED_URL_PREFIX = "/glucocraft_v"

# hass.data keys: the run's FrontendVersion, the static paths registered, the cards loaded as extra modules
_DATA_VERSION = "glucocraft_frontend_version"
_DATA_STATIC = "glucocraft_static_registered"
_DATA_EXTRA_JS = "glucocraft_extra_js"


def _hashed_files() -> list[Path]:
    """Return the files the content hash covers: every www/*.js and every file of every language catalogue."""
    files = sorted(CARDS_DIR.glob("*.js")) if CARDS_DIR.exists() else []
    if I18N_DIR.exists():
        files += sorted(f for f in I18N_DIR.rglob("*") if f.is_file())
    return files


def _compute_content_hash() -> str:
    """Return a short hash of the contents of the JS files and the catalogues (blocking: reads every file).

    From the contents, not the modification times: the hash is the same on every installation with the same files,
    and changes only when a file changes. A file that cannot be read is logged as a warning (the cards may not load)
    and stands in the hash with its modification time, so that the hash still changes when the file does.
    """
    h = hashlib.md5()
    for f in _hashed_files():
        h.update(f.relative_to(PACKAGE_DIR).as_posix().encode())  # the name too: a renamed file changes the hash
        h.update(b":")
        try:
            h.update(f.read_bytes())
        except OSError as err:
            _LOGGER.warning("GlucoCraft: the card file %s cannot be read (%s); the cards may not load", f.name, err)
            h.update(str(f.stat().st_mtime).encode())
        h.update(b"\n")
    return h.hexdigest()[:10]


# Card files that load as Lovelace resources (not the dashboard panel).
CARD_FILES = [
    "glucocraft-display-card.js",  # the display card: the wall panel's screen, same look and data
    "glucocraft-strategy.js",  # GlucoCraft's tab-free dashboard (a dashboard strategy)
    "glucocraft-ui.js",  # the dashboard's cards: banners, readout, statistics table, history, weekly trend
    "glucocraft-docs.js",  # the dashboard's Documentation page (glucocraft-documentation)
    "glucocraft-report.js",  # the dashboard's CGM Report page (glucocraft-report)
]

# The catalogue folder, served below both base paths
I18N_URL = "i18n"


@dataclass(frozen=True, slots=True)
class FrontendVersion:
    """The card files' version for one Home Assistant run."""

    content_hash: str  # _compute_content_hash
    version: str  # "<manifest version>+<hash>", for the log
    # every file served as a static path: the card files and the modules they import, which are not Lovelace resources
    # of their own (www/glucocraft-i18n.js, the catalogue loader; www/glucocraft-shared.js, the rules the cards share).
    # Each card file imports them next to its own URL, so they are served below both base paths.
    static_files: tuple[str, ...]

    @property
    def base(self) -> str:
        """The versioned base path, /glucocraft_v/<hash>."""
        return f"{VERSIONED_URL_PREFIX}/{self.content_hash}"


def _read_frontend_version() -> FrontendVersion:
    """Read the manifest version, hash the files and list the served files (blocking: the executor)."""
    manifest = json.loads((PACKAGE_DIR / "manifest.json").read_text(encoding="utf-8"))
    content_hash = _compute_content_hash()
    files = sorted({*CARD_FILES, *(f.name for f in CARDS_DIR.glob("*.js"))})
    return FrontendVersion(content_hash, f"{manifest.get('version', '0.0.0')}+{content_hash}", tuple(files))


async def async_frontend_version(hass: HomeAssistant) -> FrontendVersion:
    """Return the run's FrontendVersion, computed in the executor the first time.

    Once per run, like the static paths (registered once: a reload of the entry runs setup again): a file changed on
    disk before a reload keeps the hash of the paths already registered, so the Lovelace resources never point to a
    path that is not served. The next start uses the new hash."""
    fv = hass.data.get(_DATA_VERSION)
    if fv is None:
        fv = hass.data[_DATA_VERSION] = await hass.async_add_executor_job(_read_frontend_version)
    return fv


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Register frontend static files and Lovelace resources.

    Registers each JS file at two paths:
    1. Versioned: /glucocraft_v/<hash>/filename.js, used by GlucoCraft's dashboard and the Lovelace resources it
       registers. The path changes with every change of a file, so browsers fetch the new file.
    2. Unversioned: /glucocraft/filename.js, for users who added resources to their own dashboards by hand. These
       work too, but a browser may keep a cached copy after a file has changed.
    The language catalogues are served below both paths as well (<base>/i18n/<lang>/<area>.json).
    """
    fv = await async_frontend_version(hass)
    # A versioned path never changes its content (a new file gets a new hash), so browsers may cache it; an
    # unversioned path keeps its name across changes, so it is served without cache headers
    static_paths = [
        # The language catalogues, as a directory: every area file of every language folder
        StaticPathConfig(f"{fv.base}/{I18N_URL}", str(I18N_DIR), cache_headers=True),
        StaticPathConfig(f"{UNVERSIONED_URL_BASE}/{I18N_URL}", str(I18N_DIR), cache_headers=False),
        # Versioned paths (primary: the Lovelace resources GlucoCraft registers use them)
        *(StaticPathConfig(f"{fv.base}/{card}", str(CARDS_DIR / card), cache_headers=True) for card in fv.static_files),
        # Unversioned paths (resources added by hand)
        *(
            StaticPathConfig(f"{UNVERSIONED_URL_BASE}/{card}", str(CARDS_DIR / card), cache_headers=False)
            for card in fv.static_files
        ),
    ]

    if not hass.data.get(_DATA_STATIC):  # once per run: a reload re-runs setup
        await hass.http.async_register_static_paths(static_paths)
        hass.data[_DATA_STATIC] = True

    # Auto-register Lovelace resources with VERSIONED URLs; the line below only when they were registered
    if await _async_register_lovelace_resources(hass, fv.base):
        _LOGGER.info(
            "GlucoCraft frontend registered: %d cards, version %s, path %s",
            len(CARD_FILES),
            fv.version,
            fv.base,
        )


def _load_as_extra_modules(hass: HomeAssistant, base: str) -> None:
    """Load every card file (below the versioned base path) as an extra JavaScript module of Home Assistant's frontend
    (once per run). The URLs are kept, so that the unload removes exactly these."""
    if hass.data.get(_DATA_EXTRA_JS):
        return
    from homeassistant.components.frontend import add_extra_js_url  # frontend is a dependency

    urls = [f"{base}/{card}" for card in CARD_FILES]
    for url in urls:
        add_extra_js_url(hass, url)
    hass.data[_DATA_EXTRA_JS] = urls
    _LOGGER.info(
        "GlucoCraft: Lovelace resources cannot be written (YAML mode); the cards are loaded as frontend modules"
    )


def _lovelace_resources(hass: HomeAssistant):
    """Return Home Assistant's Lovelace resource collection (the resources of its LovelaceData), or None (Lovelace not
    loaded yet, or no collection)."""
    return getattr(hass.data.get("lovelace"), "resources", None)


def _resource_items(resources) -> list:
    """Return the items of a Lovelace resource collection (the collection API, or its data mapping)."""
    if hasattr(resources, "async_items"):
        return list(resources.async_items())
    data = getattr(resources, "data", None)
    return list(data.values()) if hasattr(data, "values") else []


def _glucocraft_resources(items) -> list[tuple[str, str, str | None]]:
    """Return GlucoCraft's card resources among Lovelace resource items: (card file, url, resource id), in the order of
    the items. A card is matched by its file name below one of GlucoCraft's paths (unversioned, an earlier hash or the
    current one), so it is found whichever path it points to. The URL's path must begin with one of these paths
    (/glucocraft/ or /glucocraft_v/; an address with a host counts by its path): a copy below another path, such as
    /local/glucocraft/glucocraft-ui.js, is not GlucoCraft's and is never changed or deleted."""
    found: list[tuple[str, str, str | None]] = []
    for resource in items:
        url = resource.get("url", "") if isinstance(resource, dict) else getattr(resource, "url", "")
        res_id = resource.get("id", None) if isinstance(resource, dict) else getattr(resource, "id", None)
        path = urlparse(url or "").path
        if not path.startswith((f"{UNVERSIONED_URL_BASE}/", f"{VERSIONED_URL_PREFIX}/")):
            continue
        card = next((c for c in CARD_FILES if path.endswith(f"/{c}")), None)
        if card is not None:
            found.append((card, url, res_id))
    return found


async def _async_register_lovelace_resources(hass: HomeAssistant, base: str) -> bool:
    """Register card JS files as Lovelace resources with versioned URLs (below `base`, FrontendVersion.base); return
    True when the cards are registered (as resources, or as frontend modules in YAML mode).

    On every startup, updates each resource to point at the current versioned path. When the hash changes, the URL
    changes, and the service worker fetches the new resource automatically. A failure, also of a single resource's
    update (the resource keeps its old path, and an earlier hash's path is not served in this run), is logged as a
    warning and opens the Repairs issue
    RESOURCES_ISSUE_ID (the cards and GlucoCraft's dashboard may not load); the next registration that succeeds
    closes it, and it does not outlast a restart of Home Assistant (not persistent).
    """
    try:
        if hass.data.get("lovelace") is None:
            _LOGGER.debug("Lovelace data not available, skipping resource registration")
            return False
        resources = _lovelace_resources(hass)

        if resources is None or not hasattr(resources, "async_create_item"):
            # Lovelace resources in YAML mode cannot be written: the cards and the dashboard strategy are loaded as
            # extra modules of the frontend instead (they work in every Lovelace mode; each file defines its elements
            # only once, so a resource the user also added by hand does no harm)
            _load_as_extra_modules(hass, base)
            ir.async_delete_issue(hass, DOMAIN, RESOURCES_ISSUE_ID)
            return True

        # The collection is often not loaded yet this early in startup: an empty list would make every start add
        # each card again. Load it first.
        # Setting `loaded` by hand copies what Home Assistant's own Lovelace code does after this load (an internal;
        # see the RISK note in the module docstring): without it, Lovelace would load the collection a second time.
        if hasattr(resources, "loaded") and not resources.loaded and hasattr(resources, "async_load"):
            await resources.async_load()
            resources.loaded = True

        # GlucoCraft's resources by card file (the first one of each); extra copies of a card are deleted
        existing: dict[str, tuple[str, str | None]] = {}
        duplicates: list[str] = []
        for card, url, res_id in _glucocraft_resources(_resource_items(resources)):
            if card not in existing:
                existing[card] = (url, res_id)
            elif res_id:
                duplicates.append(res_id)

        if duplicates and hasattr(resources, "async_delete_item"):
            for res_id in duplicates:
                try:
                    await resources.async_delete_item(res_id)
                except Exception as err:  # noqa: BLE001 - only a cleanup
                    _LOGGER.debug("Could not delete duplicate resource %s: %s", res_id, err)
            _LOGGER.info("Removed %d duplicate GlucoCraft Lovelace resources", len(duplicates))

        # Register or update each card with the current versioned URL. A card whose resource cannot be updated is
        # collected and the others are still updated: the resource keeps its old path, and a path of an earlier hash
        # is not served in this run (a 404), so it is a failure of the registration (raised below: the warning and
        # the Repairs issue)
        not_updated: list[str] = []
        for card in CARD_FILES:
            target_url = f"{base}/{card}"

            if card in existing:
                old_url, res_id = existing[card]
                if old_url == target_url:
                    _LOGGER.debug("Lovelace resource up to date: %s", target_url)
                elif res_id and hasattr(resources, "async_update_item"):
                    # the hash changed, or the resource used the unversioned path: update the URL
                    try:
                        await resources.async_update_item(res_id, {"url": target_url})
                        _LOGGER.info("Updated Lovelace resource: %s -> %s", old_url, target_url)
                    except Exception as err:  # noqa: BLE001 - collected; raised after the loop
                        not_updated.append(f"{card} ({err or err.__class__.__name__})")
                else:  # no resource id, or a collection without updates (a changed internal)
                    not_updated.append(f"{card} ({t('repairs.resources.cannot_update')})")
            else:
                # New resource, registered with the versioned URL
                await resources.async_create_item({"res_type": "module", "url": target_url})
                _LOGGER.info("Registered Lovelace resource: %s", target_url)
        if not_updated:
            raise RuntimeError(t("repairs.resources.not_updated", resources="; ".join(not_updated)))

    except Exception as err:  # noqa: BLE001 - the cards can be added by hand; setup must not fail over it
        _LOGGER.warning(
            "Could not auto-register Lovelace resources: %s. They can be added by hand in Settings > Dashboards > "
            "Resources (with Advanced mode on in the user profile), each as a JavaScript module: %s",
            err,
            ", ".join(f"{UNVERSIONED_URL_BASE}/{card}" for card in CARD_FILES),
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            RESOURCES_ISSUE_ID,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=RESOURCES_ISSUE_ID,
            translation_placeholders={"error": str(err) or err.__class__.__name__},
        )
        return False
    ir.async_delete_issue(hass, DOMAIN, RESOURCES_ISSUE_ID)
    return True


async def async_unregister_lovelace_resources(hass: HomeAssistant) -> None:
    """Remove GlucoCraft's Lovelace resources when the integration is removed.

    Deletes every resource of the collection that is one of GlucoCraft's cards (_glucocraft_resources: the versioned
    path /glucocraft_v/... or the unversioned path /glucocraft/... ending with a card's file name). Cards loaded as
    extra frontend modules (YAML mode) are removed as well.
    """
    if urls := hass.data.pop(_DATA_EXTRA_JS, None):
        try:
            # imported where it is used, as add_extra_js_url: only an entry whose cards were loaded as frontend modules
            # (Lovelace in YAML mode) needs it
            from homeassistant.components.frontend import remove_extra_js_url

            for url in urls:
                remove_extra_js_url(hass, url)
        except Exception as err:  # noqa: BLE001 - only a cleanup
            _LOGGER.debug("GlucoCraft: extra frontend modules not removed: %s", err)
    try:
        resources = _lovelace_resources(hass)
        if resources is None or not hasattr(resources, "async_delete_item"):
            return
        # the ids first, so the collection is not changed while it is iterated
        to_delete = [res_id for _card, _url, res_id in _glucocraft_resources(_resource_items(resources)) if res_id]
        for res_id in to_delete:
            try:
                await resources.async_delete_item(res_id)
                _LOGGER.info("Removed Lovelace resource id=%s", res_id)
            except Exception as err:  # noqa: BLE001 - only a cleanup
                _LOGGER.debug("Could not delete resource %s: %s", res_id, err)

    except Exception as err:  # noqa: BLE001 - only a cleanup; removing the entry must not fail over it
        _LOGGER.warning("Could not unregister Lovelace resources: %s", err)
