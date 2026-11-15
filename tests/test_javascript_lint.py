# License: AGPL-3.0-only (LICENSE).
"""ESLint on the dashboard files (eslint.config.js).

Rules under test:
- The configuration covers every file in www/, as ES modules (Home Assistant loads them as module resources, and they
  use import.meta), with ESLint's recommended rules as errors, the browser globals the files use, the line length of
  the Python (120 columns) and no nested ternary, the last two as warnings.
- The recommended rules written in the configuration are those of the installed ESLint (its @eslint/js package: the
  test workflow installs ESLint alone, so the configuration cannot import that package).
- ESLint reports no error in any of the files (a file that does not parse is an error). Run only where the eslint
  command is installed (the test workflow installs it); skipped otherwise.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from node_runner import NODE

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "eslint.config.js"
ESLINT = shutil.which("eslint")


def _node(script: str) -> object:
    if not NODE:
        pytest.skip("node not available")
    res = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=False, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _config() -> list[dict]:
    """The configuration as ESLint reads it (node loads the file)."""
    return _node(f"process.stdout.write(JSON.stringify(require({json.dumps(str(CONFIG))})))")


def _eslint_package() -> Path | None:
    """The folder of the eslint package behind the eslint command: a link to its bin/eslint.js (Linux), or a command
    file beside node_modules (npm's .bin folder, or the global folder on Windows)."""
    exe = Path(ESLINT)
    for start in (exe.resolve(), exe.parent.parent / "eslint", exe.parent / "node_modules" / "eslint"):
        for folder in (start, *start.parents):
            package = folder / "package.json"
            if package.is_file() and json.loads(package.read_text(encoding="utf-8")).get("name") == "eslint":
                return folder
    return None


def test_the_configuration_lints_modules_with_the_recommended_rules():
    (block,) = _config()
    assert block["files"] == ["custom_components/glucocraft/www/*.js"]
    assert block["languageOptions"]["sourceType"] == "module"
    assert block["rules"]["max-len"] == ["warn", {"code": 120, "ignoreUrls": True}]
    assert block["rules"]["no-nested-ternary"] == "warn"
    errors = {rule for rule, level in block["rules"].items() if level == "error"}
    assert {"no-undef", "no-unused-vars", "no-unreachable", "no-control-regex"} <= errors and len(errors) >= 60
    assert set(block["languageOptions"]["globals"].values()) == {"readonly"}
    assert {"window", "document", "customElements", "HTMLElement"} <= set(block["languageOptions"]["globals"])


@pytest.mark.skipif(not ESLINT, reason="eslint not installed")
def test_the_recommended_rules_are_those_of_the_installed_eslint():
    package = _eslint_package()
    if package is None:
        pytest.skip("the eslint package behind the command was not found")
    script = (
        f"const r = require('module').createRequire({json.dumps(str(package / 'package.json'))}); "
        "process.stdout.write(JSON.stringify(Object.keys(r('@eslint/js').configs.recommended.rules)))"
    )
    recommended = set(_node(script))
    (block,) = _config()
    errors = {rule for rule, level in block["rules"].items() if level == "error"}
    assert errors == recommended


@pytest.mark.skipif(not ESLINT, reason="eslint not installed")
def test_eslint_finds_no_error_in_the_dashboard_files():
    res = subprocess.run(
        [ESLINT, "--format", "json", "custom_components/glucocraft/www"],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )
    report = json.loads(res.stdout or "[]")
    assert len(report) == len(list((REPO / "custom_components" / "glucocraft" / "www").glob("*.js")))
    errors = [
        (Path(f["filePath"]).name, m["line"], m["message"]) for f in report for m in f["messages"] if m["severity"] == 2
    ]
    assert not errors, errors
