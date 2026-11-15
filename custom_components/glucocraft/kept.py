# License: AGPL-3.0-only (LICENSE).
"""The readings kept for a reinstall: the setting "When GlucoCraft is deleted" (const.CONF_ON_REMOVAL).

With "Keep the readings for a reinstall" (the default), deleting GlucoCraft keeps three things in the folder
`glucocraft` of the Home Assistant configuration, under names that belong to no installation, so that a new one can
adopt them:
- the readings file, renamed glucocraft_kept.bin (the format of storage.py);
- the laboratory A1C results, glucocraft_kept_lab_a1c.json ({"results": [...]}, as their store holds them);
- the sensor changes and warm-ups, glucocraft_kept_sensor_events.json ({"events": [...]}).
Everything else is removed as with "Delete everything" (__init__.async_remove_entry). Deleting the folder removes what
was kept.

A new setup finds kept readings (kept_summary) and asks whether to keep them or to delete them and start fresh
(config_flow). Kept, the new installation adopts all three at its first start (adopt): the readings file becomes the
entry's own and the two lists go back into their stores. Deleted (discard), nothing of them is left. A kept readings
file in which no reading can be read is not asked about; it is adopted all the same, and the first start reports it
as a damaged readings file (storage.py, Repairs). A kept list that cannot be read is renamed aside, with a warning in
the log, never deleted (adopt).

Every function here touches files and runs in the executor.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .storage import GlucoCraftStorage

_LOGGER = logging.getLogger(__name__)

DATA_FOLDER = "glucocraft"  # the folder of the readings file in the Home Assistant configuration
KEPT_ID = "kept"  # the readings file kept: glucocraft_kept.bin (GlucoCraftStorage with this id)
KEPT_LAB = "glucocraft_kept_lab_a1c.json"
KEPT_EVENTS = "glucocraft_kept_sensor_events.json"
KEPT_NOTICE_ID = "glucocraft_kept_readings"  # the persistent notification after a removal that kept the readings
NOTIFICATION_IDS = (KEPT_NOTICE_ID,)  # every persistent notification of this module (the removal deletes them)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write a JSON file whole: a temporary file, then a replace."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, key: str) -> list[dict] | None:
    """Return the list under `key` of a kept JSON file; [] when it is missing, None when it cannot be read (an
    operating-system error, or no JSON object with that list)."""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    items = data.get(key) if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    return [item for item in items if isinstance(item, dict)]


def _set_aside(path: Path) -> None:
    """Rename a kept list that could not be read to <name>.corrupt-<epoch> (discard removes it), with a warning."""
    aside = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
    try:
        path.replace(aside)
    except OSError as err:
        _LOGGER.warning("GlucoCraft: the kept file %s could not be read, nor renamed aside (%s)", path.name, err)
        return
    _LOGGER.warning("GlucoCraft: the kept file %s could not be read; it is kept as %s", path.name, aside.name)


def keep(folder: str, entry_id: str, lab: list[dict], events: list[dict]) -> bool:
    """Keep an entry's readings file, laboratory results and sensor events for a reinstall; return True when there
    was a readings file to keep (without one nothing is kept, and the removal removes everything)."""
    entry = GlucoCraftStorage(folder, entry_id)
    if not entry.path.is_file():
        return False
    entry.path.replace(GlucoCraftStorage(folder, KEPT_ID).path)
    base = Path(folder)
    _write_json(base / KEPT_LAB, {"results": lab})
    _write_json(base / KEPT_EVENTS, {"events": events})
    return True


def kept_summary(folder: str) -> tuple[int, int, int] | None:
    """Return the kept readings as (count, first time, last time; epoch s), or None when none are kept or none can
    be read. The file is only read (GlucoCraftStorage read_only): nothing in the folder changes."""
    storage = GlucoCraftStorage(folder, KEPT_ID, read_only=True)
    if not storage.path.is_file():
        return None
    readings = storage.readings
    if not readings:
        return None
    return len(readings), readings[0].timestamp, readings[-1].timestamp


def adopt(folder: str, entry_id: str) -> tuple[list[dict], list[dict]] | None:
    """Make the kept data an entry's own: the readings file becomes the entry's (an existing one is never replaced)
    and the laboratory results and sensor events are returned for their stores, their files removed; a list file that
    cannot be read is renamed aside instead (_set_aside), and its list is returned empty. None when nothing is
    kept."""
    base = Path(folder)
    kept = GlucoCraftStorage(folder, KEPT_ID)
    if not (kept.path.is_file() or (base / KEPT_LAB).is_file() or (base / KEPT_EVENTS).is_file()):
        return None
    entry = GlucoCraftStorage(folder, entry_id)
    if kept.path.is_file() and not entry.path.exists():
        kept.path.replace(entry.path)
    lists = []
    for name, key in ((KEPT_LAB, "results"), (KEPT_EVENTS, "events")):
        items = _read_json(base / name, key)
        if items is None:
            _set_aside(base / name)
            items = []
        else:
            (base / name).unlink(missing_ok=True)
        lists.append(items)
    _LOGGER.info("GlucoCraft: the readings kept from an earlier installation are in use again")
    return lists[0], lists[1]


def discard(folder: str) -> None:
    """Delete the kept data (the readings file with its temporary file and any copy of a damaged file, the two
    lists and any of them renamed aside) and the folder when it is then empty."""
    base = Path(folder)
    for path in GlucoCraftStorage(folder, KEPT_ID).stored_files():
        path.unlink(missing_ok=True)
    for name in (KEPT_LAB, KEPT_EVENTS):
        (base / name).unlink(missing_ok=True)
        for aside in base.glob(f"{name}.corrupt-*"):
            aside.unlink(missing_ok=True)
    try:
        base.rmdir()  # only an empty folder
    except OSError:
        pass
