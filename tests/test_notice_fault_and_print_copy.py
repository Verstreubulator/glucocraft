# License: AGPL-3.0-only (LICENSE).
"""Two complaints of the backend that must say what happened, once.

Rules under test:
- A fault in the source-notice checks (source_notices._check_source_notices_guarded) is logged once per episode as a
  warning with its traceback, then at debug level while it lasts; a check that succeeds ends the episode, and the next
  fault is a warning again.
- A print whose PDF copy could not be kept is an error of the action that does not name a kept file
  (print_failed_not_kept); a print with a kept copy names it (print_failed).
"""

from __future__ import annotations

import asyncio
import logging
import types

import pytest
from glucocraft import source_notices as sn

from test_data_path import source_coordinator
from test_entity_ids import _load_init


def test_a_lasting_notice_fault_is_logged_once_with_its_traceback(tmp_path, monkeypatch, caplog):
    c = source_coordinator(tmp_path)
    faults = [True, True, True, False, True]

    def check():
        if faults.pop(0):
            raise AttributeError("'NoneType' object has no attribute 'state'")

    monkeypatch.setattr(c, "_check_source_notices", check)
    with caplog.at_level(logging.DEBUG, logger=sn._LOGGER.name):
        for _ in range(5):
            c._check_source_notices_guarded()
    lines = [(r.levelno, r.exc_info is not None) for r in caplog.records if "source notices not checked" in r.message]
    assert lines == [
        (logging.WARNING, True),
        (logging.DEBUG, False),
        (logging.DEBUG, False),
        (logging.WARNING, True),  # after a check that succeeded: a new episode
    ]


class _Failed(Exception):
    def __init__(self, **kw):
        self.kw = kw


@pytest.mark.parametrize(("kept", "key"), [(True, "print_failed"), (False, "print_failed_not_kept")])
def test_a_print_without_a_kept_copy_names_no_file(monkeypatch, kept, key):
    init = _load_init()
    monkeypatch.setattr(init, "HomeAssistantError", _Failed)

    async def refused(*args):
        return {
            "ok": False,
            "message": "Not printed: ...",
            "reason": "the printer refused",
            "file": "x.pdf",
            "kept": kept,
        }

    coordinator = types.SimpleNamespace(reports=types.SimpleNamespace(async_print=refused))
    monkeypatch.setattr(init, "_coordinator_for", lambda hass: coordinator)

    async def call(*a, **kw):
        return None

    hass = types.SimpleNamespace(services=types.SimpleNamespace(async_call=call))
    with pytest.raises(_Failed) as err:
        asyncio.run(init._handle_print(hass, types.SimpleNamespace(data={})))
    assert err.value.kw["translation_key"] == key
    expected = {"reason": "the printer refused", "file": "x.pdf"} if kept else {"reason": "the printer refused"}
    assert err.value.kw["translation_placeholders"] == expected
