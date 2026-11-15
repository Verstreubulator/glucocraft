# License: AGPL-3.0-only (LICENSE).
"""The terms of a contribution: CONTRIBUTING.md states them in one paragraph, and nothing else asks for an agreement.

Contributions are made under the project's license (inbound = outbound): no contributor license agreement, no
sign-off and no agreement checkbox in the pull-request template. The paragraph is pinned word for word, so that a
change of the terms is a deliberate edit of this test as well.
"""

from __future__ import annotations

import re

import pytest

from conftest import REPO

LICENSE_PARAGRAPH = (
    "Contributions are made under the project's license, the GNU Affero General Public License, version 3 only "
    "(AGPL-3.0-only). By opening a pull request, you confirm that you have the right to submit your contribution under that license."
)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _read(rel: str) -> str:
    path = REPO / rel
    if not path.is_file():
        pytest.skip(f"{rel} is not in this copy of the repository")
    return path.read_text(encoding="utf-8")


def test_contributing_states_the_terms_in_its_license_section():
    text = _read("CONTRIBUTING.md")
    section = re.search(r"^### License\n(.*?)(?=^#|\Z)", text, re.DOTALL | re.MULTILINE)
    assert section, "CONTRIBUTING.md has no License section"
    assert _flat(section.group(1)) == LICENSE_PARAGRAPH


def test_no_text_asks_for_a_contributor_agreement():
    """No contributor license agreement, no sign-off and no agreement checkbox, in CONTRIBUTING.md or the template."""
    for rel in ("CONTRIBUTING.md", ".github/pull_request_template.md"):
        flat = _flat(_read(rel)).lower()
        for phrase in ("license agreement", "signed-off-by", "sign-off", "developer certificate of origin", "i agree"):
            assert phrase not in flat, (rel, phrase)
    template = _read(".github/pull_request_template.md").lower()
    assert not re.search(r"- \[ \][^\n]*\b(license|agree|terms)\b", template)
