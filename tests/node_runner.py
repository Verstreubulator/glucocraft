# License: AGPL-3.0-only (LICENSE).
"""Run the dashboard files' JavaScript in node for the tests.

A test script is run as an ES module from standard input, by default after js_i18n.i18n_prelude() (the language
module, the English catalogues and the cards' shared module), so that a card file evaluated in it finds its texts and
shared rules loaded. A script that returns a value writes it as JSON after the mark "\\u0001" (anything the script
printed before the mark is ignored): node_json() reads it back. A test that needs node is skipped when node is not
installed (the needs_node marker, or the skip inside run_node). Node's Date shows the tests' clock (ha_stub.set_clock,
GLUCOCRAFT_TEST_CLOCK).
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

import ha_stub
from js_i18n import i18n_prelude

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="node not available")
MARK = "\u0001"


def _clock_prelude() -> str:
    """Node's clock set to the tests' clock (ha_stub.set_clock, GLUCOCRAFT_TEST_CLOCK), or nothing on the real one."""
    shift = round(ha_stub.clock_offset() * 1000)
    if not shift:
        return ""
    return (
        f"{{ const RealDate = Date, shift = {shift};\n"
        "  globalThis.Date = class extends RealDate {\n"
        "    constructor(...a) { super(...(a.length ? a : [RealDate.now() + shift])); }\n"
        "    static now() { return RealDate.now() + shift; }\n"
        "  }; }\n"
    )


def run_node(script: str, *, prelude: bool = True, timeout: int = 60) -> str:
    """Run script in node (after i18n_prelude() unless prelude is False) and return what it printed; a script that
    fails fails the test with node's error output."""
    if not NODE:
        pytest.skip("node not available")
    res = subprocess.run(
        [NODE, "--input-type=module", "-"],
        input=_clock_prelude() + (i18n_prelude() if prelude else "") + script,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    assert res.returncode == 0, res.stderr
    return res.stdout


def node_json(script: str, *, prelude: bool = True, timeout: int = 60) -> object:
    """Run script as run_node does and return the JSON value it wrote after MARK."""
    return json.loads(run_node(script, prelude=prelude, timeout=timeout).split(MARK, 1)[1])
