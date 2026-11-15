# License: AGPL-3.0-only (LICENSE).
"""quality_scale.yaml: GlucoCraft's record of Home Assistant's Integration Quality Scale rules.

Rules under test:
- Every rule of the four tiers is recorded, and no other key, in the format of hassfest's schema
  (script/hassfest/quality_scale.py): "done" or "todo", or a mapping with "status" and "comment"; "exempt" only with a
  comment.
- GlucoCraft's own rule on top of the schema: every "todo" carries a comment that says what is missing.
- The records that a file can confirm agree with the code: diagnostics, runtime data, a single entry, the device and
  the platforms' PARALLEL_UPDATES.
- codeowners is a list of GitHub accounts (@name); it names a real account, not the placeholder, when its quality-scale
  rule is recorded as done and whenever the documentation address is on github.com.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "glucocraft"
RULES = yaml.safe_load((PKG / "quality_scale.yaml").read_text(encoding="utf-8"))["rules"]

# The rules of Home Assistant's Integration Quality Scale, by tier (https://contributors.home-assistant.io/core/
# quality-scale/rules/; the same list as ALL_RULES in hassfest's quality_scale.py)
TIERS = {
    "bronze": (
        "action-setup appropriate-polling brands common-modules config-flow config-flow-test-coverage "
        "dependency-transparency docs-actions docs-conditions docs-high-level-description "
        "docs-installation-instructions docs-removal-instructions docs-triggers entity-event-setup entity-unique-id "
        "has-entity-name runtime-data test-before-configure test-before-setup unique-config-entry"
    ),
    "silver": (
        "action-exceptions config-entry-unloading docs-configuration-parameters docs-installation-parameters "
        "entity-unavailable integration-owner log-when-unavailable parallel-updates reauthentication-flow "
        "test-coverage"
    ),
    "gold": (
        "devices diagnostics discovery discovery-update-info docs-data-update docs-examples docs-known-limitations "
        "docs-supported-devices docs-supported-functions docs-troubleshooting docs-use-cases dynamic-devices "
        "entity-category entity-device-class entity-disabled-by-default entity-translations exception-translations "
        "icon-translations reconfiguration-flow repair-issues stale-devices"
    ),
    "platinum": "async-dependency inject-websession strict-typing",
}
ALL_RULES = [rule for names in TIERS.values() for rule in names.split()]


def _status(value) -> str:
    return value["status"] if isinstance(value, dict) else value


def test_every_rule_is_recorded_once_and_nothing_else():
    assert len(ALL_RULES) == 54 and len(set(ALL_RULES)) == 54
    assert set(RULES) == set(ALL_RULES), set(RULES) ^ set(ALL_RULES)


@pytest.mark.parametrize("rule", ALL_RULES)
def test_each_record_has_hassfests_format_and_todo_says_why(rule):
    value = RULES[rule]
    if isinstance(value, str):
        assert value == "done", f"{rule}: a todo or an exemption needs a comment"
        return
    assert set(value) == {"status", "comment"}, rule
    assert value["status"] in ("done", "todo", "exempt"), rule
    assert isinstance(value["comment"], str) and value["comment"].strip().endswith("."), rule


def test_the_records_agree_with_the_code():
    manifest = json.loads((PKG / "manifest.json").read_text(encoding="utf-8"))
    init = (PKG / "__init__.py").read_text(encoding="utf-8")
    if _status(RULES["diagnostics"]) == "done":
        assert "async def async_get_config_entry_diagnostics" in (PKG / "diagnostics.py").read_text(encoding="utf-8")
    if _status(RULES["runtime-data"]) == "done":
        assert "entry.runtime_data = coordinator" in init
    if _status(RULES["unique-config-entry"]) == "done":
        assert manifest["single_config_entry"] is True
    if _status(RULES["devices"]) == "done":
        assert "DeviceEntryType.SERVICE" in (PKG / "entity.py").read_text(encoding="utf-8")
    if _status(RULES["parallel-updates"]) == "done":
        for platform in ("sensor", "binary_sensor"):
            assert "\nPARALLEL_UPDATES = " in (PKG / f"{platform}.py").read_text(encoding="utf-8"), platform
    if _status(RULES["icon-translations"]) == "done":
        assert (PKG / "icons.json").is_file()
    codeowners = manifest["codeowners"]
    assert isinstance(codeowners, list) and codeowners
    assert all(re.fullmatch(r"@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", c) for c in codeowners), codeowners
    named = not any("placeholder" in c for c in codeowners)  # codeowners names a real account
    if _status(RULES["integration-owner"]) == "done" or manifest["documentation"].startswith("https://github.com/"):
        assert named, codeowners  # done, or a repository on GitHub, needs the maintainer's account
    assert "quality_scale" not in manifest  # no tier is declared while rules are open
