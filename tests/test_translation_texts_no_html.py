# License: AGPL-3.0-only (LICENSE).
"""The translation texts contain nothing that Home Assistant's translation check (hassfest) reads as HTML.

hassfest validates every text of strings.json with `cv.string_with_no_html`, which refuses any text matching
`<[a-z].*?>` (case-insensitive): a placeholder written in angle brackets such as a file name pattern fails it. The
same rule is applied here to strings.json and translations/en.json, and to the action field texts of services.yaml.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

CC = Path(__file__).resolve().parents[1] / "custom_components" / "glucocraft"
HTML = re.compile(r"<[a-z].*?>", re.IGNORECASE)  # the expression of Home Assistant's string_with_no_html


def _texts(node, path=""):
    """Every text of a catalogue with its key path."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _texts(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _texts(value, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


@pytest.mark.parametrize("name", ["strings.json", "translations/en.json"])
def test_no_translation_text_looks_like_html(name):
    texts = dict(_texts(json.loads((CC / name).read_text(encoding="utf-8"))))
    assert [key for key, text in texts.items() if HTML.search(text)] == []


def test_no_action_field_text_looks_like_html():
    lines = (CC / "services.yaml").read_text(encoding="utf-8").splitlines()
    assert [line.strip() for line in lines if HTML.search(line)] == []


def test_the_check_finds_a_placeholder_in_angle_brackets():
    assert HTML.search("glucocraft-readings_<date and time>.csv")
    assert not HTML.search("70 < 180 and 250 > 180")
