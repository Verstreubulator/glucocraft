# License: AGPL-3.0-only (LICENSE).
"""A key no catalogue has (i18n.t): the key itself is returned every time, and it is logged once per key."""

from __future__ import annotations

import logging

from glucocraft import i18n


def test_a_missing_key_is_logged_once(caplog):
    i18n.load("en")
    with caplog.at_level(logging.DEBUG, logger="glucocraft.i18n"):
        assert [i18n.t("notify.part_a2_no_such_text") for _ in range(3)] == ["notify.part_a2_no_such_text"] * 3
        i18n.t("notify.part_a2_other_missing_text")
    logged = [r.getMessage() for r in caplog.records if "no text for" in r.getMessage()]
    assert logged == [
        "GlucoCraft: no text for notify.part_a2_no_such_text",
        "GlucoCraft: no text for notify.part_a2_other_missing_text",
    ]
