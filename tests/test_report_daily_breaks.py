# License: AGPL-3.0-only (LICENSE).
"""The report's daily profiles mark data gaps between 10-minute slots: a 15-19-minute gap that does
not empty a whole slot still breaks the line in the PDF; a short dropout within the gap rule does not."""

from datetime import UTC, datetime

from glucocraft import report


class _R:
    """A stored reading: time, value, no limit flag."""

    def __init__(self, ts, raw):
        self.timestamp, self.raw = ts, raw
        self.limit = None


def _day_report(present_minutes):
    tz = UTC
    day0 = datetime(2026, 10, 20, tzinfo=tz)
    start = int(day0.timestamp())
    readings = [_R(start + m * 60, 100.0) for m in present_minutes]
    g = report.minute_grid(readings, 60, start, start + 86400, tz)
    return report.period_report(g, day0, 1, daily=True)


def test_a_16_minute_gap_breaks_the_line_without_an_empty_slot():
    # 05:03-05:19 missing: slots 30 (05:00-05:09) and 31 (05:10-05:19) both keep some minutes
    rep = _day_report([m for m in range(1440) if not 303 <= m < 319])
    assert rep["daily"][0][30] is not None and rep["daily"][0][31] is not None
    assert 31 in rep["daily_breaks"][0]


def test_a_10_minute_dropout_is_bridged():
    rep = _day_report([m for m in range(1440) if not 125 <= m < 135])
    assert rep["daily_breaks"][0] == []


def test_complete_day_has_no_breaks():
    rep = _day_report(list(range(1440)))
    assert rep["daily_breaks"] == [[]]
