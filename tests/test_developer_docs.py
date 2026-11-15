# License: AGPL-3.0-only (LICENSE).
"""The developer documents name what the integration creates.

Rules under test:
- docs/ENTITIES_AND_API.md names every Repairs issue (each translation key of strings.json "issues") and every
  persistent notification id the integration creates (the removal's list, __init__.NOTIFICATION_IDS, and the
  per-panel id forms "<prefix><device id>").
"""

from __future__ import annotations

import json
import re

from conftest import REPO, repo_doc
from test_entity_ids import _load_init as _init

PKG = REPO / "custom_components" / "glucocraft"

# __init__.py is loaded through the one shared loader (test_entity_ids._load_init), never a loader of its own: both
# cache the module under the same name in sys.modules, and a loader that leaves out submodule_search_locations=None
# makes __init__.py a package of its own, whose relative imports load a second copy of every GlucoCraft module. The
# tests that run after it and patch glucocraft.notification (test_settings_notifications, test_settings_invalid) then
# patch modules that copy never calls.


def _named(doc: str) -> set[str]:
    """Every name written as code (`...`) in a document."""
    return set(re.findall(r"`([^`\n]+)`", doc))


def test_every_repairs_issue_is_documented():
    issues = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))["issues"]
    missing = sorted(set(issues) - _named(repo_doc("ENTITIES_AND_API.md")))
    assert not missing, missing


def test_every_notification_id_is_documented():
    init = _init()
    named = _named(repo_doc("ENTITIES_AND_API.md"))
    missing = sorted(set(init.NOTIFICATION_IDS) - named)
    assert not missing, missing
    for prefix in init.PANEL_NOTIFICATION_PREFIXES:
        assert f"{prefix}<device id>" in named, prefix
