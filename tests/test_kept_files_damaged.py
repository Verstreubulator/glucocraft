# License: AGPL-3.0-only (LICENSE).
"""Damaged files among the data kept for a reinstall (kept.py).

Rules under test:
- Looking for kept readings (kept_summary, run by the setup) changes nothing in the folder: a kept readings file that
  cannot be parsed is neither moved aside nor copied, and one with a damaged header whose readings can be recovered is
  summarized without a copy being written. The first start adopts the file all the same and reports it as a damaged
  readings file.
- A kept laboratory or sensor-event list that cannot be read is never deleted: the adoption renames it aside with a
  warning in the log, and deleting the kept data removes that copy too.
"""

from __future__ import annotations

import json
import logging

from glucocraft import const as C
from glucocraft import kept
from glucocraft.storage import GlucoCraftStorage


def _kept_readings(folder, count=3, start=1_790_000_000):
    storage = GlucoCraftStorage(str(folder), kept.KEPT_ID)
    storage.load()
    for k in range(count):
        storage.insert_reading(start + 300 * k, 100.0 + k, None, C.SOURCE_PRIMARY)
    storage.save()
    return storage.path


def test_a_kept_file_that_cannot_be_parsed_is_left_as_it_is(tmp_path):
    folder = tmp_path / "glucocraft"
    folder.mkdir()
    path = GlucoCraftStorage(str(folder), kept.KEPT_ID).path
    path.write_bytes(b"not a readings file at all")
    assert kept.kept_summary(str(folder)) is None
    assert sorted(p.name for p in folder.iterdir()) == [path.name]
    assert path.read_bytes() == b"not a readings file at all"
    # adopted at the first start, where the entry's storage reports it (and keeps it aside) as a damaged file
    assert kept.adopt(str(folder), "e2") == ([], [])
    entry = GlucoCraftStorage(str(folder), "e2")
    entry.load()
    assert entry.problem == "damaged" and entry.kept_file and entry.kept_file.startswith("glucocraft_e2.bin.corrupt-")


def test_a_kept_file_with_a_damaged_header_is_summarized_without_a_copy(tmp_path):
    folder = tmp_path / "glucocraft"
    path = _kept_readings(folder)
    data = bytearray(path.read_bytes())
    data[:4] = b"XXXX"
    path.write_bytes(bytes(data))
    assert kept.kept_summary(str(folder)) == (3, 1_790_000_000, 1_790_000_600)
    assert sorted(p.name for p in folder.iterdir()) == [path.name] and path.read_bytes() == bytes(data)


def test_a_kept_list_that_cannot_be_read_is_renamed_aside(tmp_path, caplog):
    folder = tmp_path / "glucocraft"
    _kept_readings(folder)
    (folder / kept.KEPT_LAB).write_text("{not json", encoding="utf-8")
    events = {"events": [{"start": 1_790_000_000, "end": 1_790_007_200}]}
    (folder / kept.KEPT_EVENTS).write_text(json.dumps(events), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=kept.__name__):
        assert kept.adopt(str(folder), "e2") == ([], events["events"])
    names = sorted(p.name for p in folder.iterdir())
    assert names[0] == "glucocraft_e2.bin" and names[1].startswith(f"{kept.KEPT_LAB}.corrupt-") and len(names) == 2
    assert (folder / names[1]).read_text(encoding="utf-8") == "{not json"
    assert f"the kept file {kept.KEPT_LAB} could not be read; it is kept as {names[1]}" in caplog.text
    # deleting the kept data removes the copy as well
    kept.discard(str(folder))
    assert sorted(p.name for p in folder.iterdir()) == ["glucocraft_e2.bin"]


def test_a_list_without_its_key_is_not_read(tmp_path):
    folder = tmp_path / "glucocraft"
    folder.mkdir()
    (folder / kept.KEPT_EVENTS).write_text(json.dumps({"other": []}), encoding="utf-8")
    assert kept._read_json(folder / kept.KEPT_EVENTS, "events") is None
    assert kept._read_json(folder / kept.KEPT_LAB, "results") == []  # missing: nothing kept
