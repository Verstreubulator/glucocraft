# License: AGPL-3.0-only (LICENSE).
"""One key per concept in each language catalogue (i18n/en/backend.json, frontend.json, report.json).

Two keys of one catalogue with the same English text are either one concept, which gets one key that every reader
uses, or two concepts that only happen to read the same in English, which a translation may write differently. The
second kind is listed below, group by group, with its reason; docs/DEVELOPER_GUIDE.md section 2 ("The same text under
two keys") explains each reason. A new key whose text equals another key's text, or a changed text that now equals
another one, fails here until it is merged into the existing key or listed with its reason; a listed group that is no
longer a duplicate fails as well, so that the list stays exact.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CATALOGUES = ROOT / "custom_components" / "glucocraft" / "i18n" / "en"
GUIDE = ROOT / "docs" / "DEVELOPER_GUIDE.md"

# The reasons, as named in the Developer Guide
REASONS = (
    "plural",
    "short form",
    "zone by id",
    "separator by role",
    "zone names by place",
    "display card",
    "spoken",
    "Settings Status tables",
    "table labels",
)

# Every group of keys with the same English text, per catalogue, and its reason
JUSTIFIED: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "backend": [
        ("plural", ("spoken.unit.percent.one", "spoken.unit.percent.other")),
        ("short form", ("date.month.5", "date.month_short.5")),
        ("zone by id", ("spoken.zone.near_high", "spoken.zone.near_low")),
        ("zone by id", ("zone.word.near_high", "zone.word.near_low")),
        ("separator by role", ("contrast.separator", "notify.list_separator")),
        ("separator by role", ("export.file_separator", "spoken.list_separator")),
        ("zone names by place", ("contrast.zone.high", "zone.word.high")),
        ("zone names by place", ("contrast.zone.low", "zone.word.low")),
        ("zone names by place", ("contrast.zone.tight_range", "zone.word.tight_range")),
        ("zone names by place", ("contrast.zone.very_high", "zone.word.very_high")),
        ("zone names by place", ("contrast.zone.very_low", "zone.word.very_low")),
    ],
    "frontend": [
        ("plural", ("spoken.unit.percent.one", "spoken.unit.percent.other")),
        ("short form", ("card.date.month.may", "card.date.month_short.may")),
        ("display card", ("card.describe.measured", "ui.statistics.said.measured")),
        ("display card", ("card.describe.measured_now", "ui.statistics.said.measured_now")),
        ("display card", ("card.describe.no_current_data", "ui.statistics.said.no_data")),
        ("display card", ("card.no_reading.no_current_data", "ui.statistics.no_data")),
        ("display card", ("card.describe.separator", "ui.join.sentences")),
        ("display card", ("card.describe.trend", "ui.statistics.said.trend")),
        ("display card", ("card.describe.weekly.cv", "ui.goal_name.cv")),
        ("display card", ("card.describe.weekly.tir", "ui.goal_name.time_in_range")),
        ("display card", ("card.describe.weekly.titr", "ui.goal_name.time_in_tight_range")),
        ("display card", ("card.limit.high", "ui.limit.hi")),
        ("display card", ("card.limit.low", "ui.limit.lo")),
        ("display card", ("card.page_in_sentence.14_days", "ui.period.14d")),
        ("display card", ("card.page_in_sentence.24_hours", "ui.period.24h")),
        ("display card", ("card.page_in_sentence.30_days", "ui.period.30d")),
        ("display card", ("card.page_in_sentence.6_hours", "ui.period.6h")),
        ("display card", ("card.page_in_sentence.6_months", "ui.period.180d")),
        ("display card", ("card.page_in_sentence.7_days", "ui.period.7d")),
        ("display card", ("card.page_in_sentence.90_days", "ui.period.90d")),
        ("display card", ("card.stat.high", "card.zone.high", "ui.zone.high")),
        ("display card", ("card.stat.low", "card.zone.low", "ui.zone.low")),
        ("display card", ("card.stat.sd", "report_page.col.sd")),
        ("display card", ("card.stat.very_high", "card.zone.very_high", "ui.zone.very_high")),
        ("display card", ("card.stat.very_low", "card.zone.very_low", "ui.zone.very_low")),
        ("display card", ("card.weekly.consensus_target", "ui.core.column.consensus_target")),
        ("display card", ("card.weekly.custom_goal", "ui.core.column.custom_goal")),
        ("display card", ("card.zone.near_high", "card.zone.near_low", "ui.zone.in_range")),
        ("display card", ("card.zone.tight_range", "ui.zone.tight_range")),
        ("display card", ("card.zone_in_sentence.high", "strategy.zone.high", "ui.zone_in_sentence.high")),
        ("display card", ("card.zone_in_sentence.low", "strategy.zone.low", "ui.zone_in_sentence.low")),
        (
            "display card",
            ("card.zone_in_sentence.near_high", "card.zone_in_sentence.near_low", "ui.zone_in_sentence.in_range"),
        ),
        (
            "display card",
            ("card.zone_in_sentence.tight_range", "strategy.zone.tight_range", "ui.zone_in_sentence.tight_range"),
        ),
        (
            "display card",
            ("card.zone_in_sentence.very_high", "strategy.zone.very_high", "ui.zone_in_sentence.very_high"),
        ),
        ("display card", ("card.zone_in_sentence.very_low", "strategy.zone.very_low", "ui.zone_in_sentence.very_low")),
        ("spoken", ("card.describe.when_date", "card.no_reading.when_date")),
        ("spoken", ("card.describe.when_yesterday", "card.limit_screen.when_yesterday")),
        ("spoken", ("spoken.compare.at_least", "ui.compare.at_least")),
        ("spoken", ("spoken.compare.at_most", "ui.compare.at_most")),
        ("spoken", ("spoken.compare.less_than", "ui.compare.less_than")),
        ("spoken", ("spoken.compare.more_than", "ui.compare.more_than")),
        ("spoken", ("spoken.does_not_meet", "ui.result.does_not_meet")),
        ("spoken", ("spoken.duration.two_units", "ui.duration.two_units")),
        ("spoken", ("spoken.meets", "ui.result.meets")),
        ("spoken", ("spoken.unit.day.one", "ui.duration.day.one")),
        ("spoken", ("spoken.unit.day.other", "ui.duration.day.other")),
        ("spoken", ("spoken.unit.hour.one", "ui.duration.hour.one")),
        ("spoken", ("spoken.unit.hour.other", "ui.duration.hour.other")),
        ("spoken", ("spoken.unit.minute.one", "ui.duration.minute.one")),
        ("spoken", ("spoken.unit.minute.other", "ui.duration.minute.other")),
        ("Settings Status tables", ("strategy.goal.gmi", "ui.goal_name.gmi")),
        ("Settings Status tables", ("strategy.goal.time_high", "ui.goal_name_in_text.time_high")),
        ("Settings Status tables", ("strategy.goal.time_in_range", "ui.goal_name_in_text.time_in_range")),
        ("Settings Status tables", ("strategy.goal.time_low", "ui.goal_name_in_text.time_low")),
        ("Settings Status tables", ("strategy.goal.time_very_high", "ui.goal_name_in_text.time_very_high")),
        ("Settings Status tables", ("strategy.goal.time_very_low", "ui.goal_name_in_text.time_very_low")),
        ("Settings Status tables", ("strategy.separator.long_list", "ui.join.list")),
        ("table labels", ("report_page.col.measure", "ui.column.measure")),
        ("table labels", ("ui.column.last_14_days", "ui.enough_data.period.14d")),
    ],
    "report": [
        ("table labels", ("report.alt.measure.cv", "report.change.row.cv", "report.goal.cv", "report.overview.row.cv")),
        ("table labels", ("report.alt.measure.mean", "report.change.row.mean", "report.overview.row.mean")),
        ("table labels", ("report.alt.measure.tir", "report.goal.time_in_range")),
        ("table labels", ("report.blocks.col.cv", "report.weekly.col.cv")),
        ("table labels", ("report.blocks.col.data", "report.days.col.data", "report.weekly.col.data")),
        ("table labels", ("report.blocks.col.events", "report.days.col.events")),
        (
            "table labels",
            ("report.blocks.col.gmi", "report.change.row.gmi", "report.goal.gmi", "report.overview.row.gmi"),
        ),
        ("table labels", ("report.blocks.col.gri", "report.overview.row.gri")),
        ("table labels", ("report.blocks.col.mean", "report.days.col.mean")),
        ("table labels", ("report.blocks.col.period", "report.events.period")),
        ("table labels", ("report.blocks.col.tir", "report.weekly.col.tir")),
        ("table labels", ("report.change.row.above_180", "report.overview.row.above_180", "report.row.above_180")),
        ("table labels", ("report.change.row.below_54", "report.overview.row.below_54", "report.row.below_54")),
        ("table labels", ("report.change.row.below_70", "report.overview.row.below_70", "report.row.below_70")),
        ("table labels", ("report.change.row.tir", "report.overview.row.tir")),
        ("table labels", ("report.custom.col.value", "report.overview.value")),
        ("table labels", ("report.events.row.events", "report.lows.level1")),
        ("table labels", ("report.overview.consensus_target", "report.table.consensus_target")),
        ("table labels", ("report.overview.row.above_250", "report.row.above_250")),
        ("table labels", ("report.overview.target.none", "report.quality.gaps_none")),
    ],
}


def duplicate_groups(catalogue: dict[str, object]) -> set[frozenset[str]]:
    """Return every group of two or more keys whose texts are the same (texts only; the Documentation's lists are
    not compared)."""
    by_text: dict[str, list[str]] = defaultdict(list)
    for key, value in catalogue.items():
        if isinstance(value, str):
            by_text[value].append(key)
    return {frozenset(keys) for keys in by_text.values() if len(keys) > 1}


@pytest.mark.parametrize("area", JUSTIFIED)
def test_every_repeated_text_is_one_listed_group(area):
    catalogue = json.loads((CATALOGUES / f"{area}.json").read_text(encoding="utf-8"))
    found = duplicate_groups(catalogue)
    listed = {frozenset(keys) for _, keys in JUSTIFIED[area]}
    new = sorted(sorted(group) for group in found - listed)
    gone = sorted(sorted(group) for group in listed - found)
    assert not new, f"one text under several keys of {area}.json: merge them into one key, or list the group: {new}"
    assert not gone, f"listed groups of {area}.json that are no longer one text: remove them from the list: {gone}"


def test_every_reason_is_explained_in_the_developer_guide():
    guide = GUIDE.read_text(encoding="utf-8")
    section = guide.split("The same text under two keys", 1)[1].split("\n## ", 1)[0]
    used = {reason for groups in JUSTIFIED.values() for reason, _ in groups}
    assert used == set(REASONS)
    for reason in REASONS:
        assert f"| {reason} |" in section, reason


def test_the_documentation_has_no_repeated_text():
    """The Documentation's texts are long, each written once."""
    catalogue = json.loads((CATALOGUES / "documentation.json").read_text(encoding="utf-8"))
    assert duplicate_groups(catalogue) == set()
