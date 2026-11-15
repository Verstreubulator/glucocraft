# License: AGPL-3.0-only (LICENSE).
"""Small robustness rules of the settings, the data view, the exports and the statistics.

Rules under test:
  * a digit that is not an ASCII digit ("²" is a digit to str.isdigit(), but int() cannot read it) is no number of
    days for /api/glucocraft/data?daily=N (nothing is added, no error) and no stored no-data notice delay (the
    default is used);
  * the Glucose ranges screen (Configure) opens on an entry whose stored range is of the wrong type (None, a list):
    the form shows the default for it, and saving the form unchanged stores valid numbers;
  * only a name the export gives its files counts as an export: a file the user copied into the folder under another
    name (glucocraft-readings_backup.csv) is not the newest export and is never pruned;
  * each reading has exactly one weight in the statistics: a list of weights of another length is an error, not a
    silent loss of readings.
"""

from __future__ import annotations

import asyncio

import pytest
from glucocraft import const as C
from glucocraft import export as EX
from glucocraft import http_api
from glucocraft import settings as S
from glucocraft import statistics as ST
from glucocraft.storage import GlucoseReading

from test_config_flow_options import (
    _defaults_input,
    _options_flow,
    _stored,
    schemas,  # noqa: F401 (the fixture)
)
from test_surface_fixes import _api_hass, _Request, _view, api_coordinator  # noqa: F401 (the fixture)

VLOW, LOW = C.CONF_RANGE_CRITICAL_LOW, C.CONF_RANGE_LOW


# ------------------------------------------------------------------------------------------------ non-ASCII digits
def test_a_non_ascii_digit_adds_no_daily_values_and_raises_nothing(api_coordinator):  # noqa: F811
    hass = _api_hass(api_coordinator, [])
    for bad in ("²", "¹⁴", "1²"):
        assert "²".isdigit()  # the trap: isdigit() alone would let it through to int()
        out = asyncio.run(_view(http_api.GlucoCraftDataView).get(_Request(hass, {"daily": bad})))
        assert "daily" not in out
    out = asyncio.run(_view(http_api.GlucoCraftDataView).get(_Request(hass, {"daily": "2"})))
    assert len(out["daily"]) == 2  # an ASCII number still works


def test_a_stored_notice_delay_with_a_non_ascii_digit_is_the_default():
    key = C.CONF_NO_DATA_NOTICE_DELAY
    assert S.setting({key: "²"}, key) == S.setting({}, key)
    assert S.setting({key: "60"}, key) == 60  # the selector's text is still read
    assert S.setting({key: 15}, key) == 15


# ------------------------------------------------------------------------------------------------ the ranges screen
@pytest.mark.usefixtures("schemas")
@pytest.mark.parametrize("bad", [None, [70], "70", {"x": 1}])
def test_the_ranges_screen_opens_with_a_stored_range_of_the_wrong_type(bad):
    flow, shown, saved = _options_flow({**_stored(), VLOW: bad, LOW: bad})
    asyncio.run(flow.async_step_ranges())
    form = shown[-1]
    assert form["step_id"] == "ranges" and not form["errors"]
    sent = _defaults_input(form["data_schema"])
    assert sent[VLOW] == S.RANGE_DEFAULTS[VLOW] and sent[LOW] == S.RANGE_DEFAULTS[LOW]
    asyncio.run(flow.async_step_ranges(sent))  # saved unchanged
    assert saved, shown[-1].get("errors")
    for key in S.RANGE_LIMITS:
        value = saved[-1][key]
        assert isinstance(value, (int, float)) and not isinstance(value, bool), (key, value)
    assert saved[-1][VLOW] == S.RANGE_DEFAULTS[VLOW] and saved[-1][LOW] == S.RANGE_DEFAULTS[LOW]


def test_stored_ranges_validates_each_limit_and_the_set():
    assert S.stored_ranges({VLOW: None, LOW: [70]}) == S.RANGE_DEFAULTS
    assert S.stored_ranges({VLOW: 50})[VLOW] == 50  # a valid stored limit is kept
    assert S.stored_ranges({VLOW: 80})[VLOW] == S.RANGE_DEFAULTS[VLOW]  # outside its span: its default
    assert S.stored_ranges({VLOW: 65, LOW: 60}) == S.RANGE_DEFAULTS  # each within its span, out of order: the defaults


# ------------------------------------------------------------------------------------------------ the export names
def _touch(folder, name):
    (folder / name).write_text("x", encoding="utf-8")


def test_a_file_under_another_name_is_not_the_newest_export(tmp_path):
    exact = f"{EX.READINGS_PREFIX}2026-10-01_080000.csv"
    _touch(tmp_path, exact)
    _touch(tmp_path, f"{EX.READINGS_PREFIX}backup.csv")  # sorts after every time stamp
    _touch(tmp_path, f"{EX.READINGS_PREFIX}2026-10-02_080000.csv.tmp")  # an unfinished write
    assert EX.newest_export(str(tmp_path)) == exact


def test_no_export_at_all_is_none(tmp_path):
    _touch(tmp_path, f"{EX.READINGS_PREFIX}backup.csv")
    assert EX.newest_export(str(tmp_path)) is None


def test_the_pruning_keeps_a_file_under_another_name(tmp_path):
    for k in range(C.EXPORT_KEEP + 2):
        _touch(tmp_path, f"{EX.READINGS_PREFIX}2026-10-01_0000{k:02d}.csv")
    _touch(tmp_path, f"{EX.READINGS_PREFIX}backup.csv")
    EX._prune(str(tmp_path), EX.READINGS_PREFIX)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert f"{EX.READINGS_PREFIX}backup.csv" in names  # never deleted, and takes no kept place
    exports = [n for n in names if EX.EXPORT_FILE_NAME.fullmatch(n)]
    assert len(exports) == C.EXPORT_KEEP
    assert exports[-1] == f"{EX.READINGS_PREFIX}2026-10-01_0000{C.EXPORT_KEEP + 1:02d}.csv"  # the newest stay


# ------------------------------------------------------------------------------------------------ the weights
def test_weights_of_another_length_are_an_error():
    readings = [GlucoseReading(1_700_000_000 + k * 300, 100.0 + k) for k in range(4)]
    assert ST.compute_period_stats(readings, "x", 1200, 300, weights=[300.0] * 4) is not None
    with pytest.raises(ValueError):
        ST.compute_period_stats(readings, "x", 1200, 300, weights=[300.0] * 3)
