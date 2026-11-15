# License: AGPL-3.0-only (LICENSE).
"""The known findings of tests/test_synth_scenarios.py hide exactly the disagreements listed here, no others.

Each finding there masks a pattern of comparison keys (for example every displayed mean and SD, in every scenario),
while its strict expected failure checks one case only. Without this list, a new disagreement under a masked key (a
regression in the display rounding of another scenario, a wrong LO / HI count in the store comparison of a masked
scenario) would pass unnoticed. When GlucoCraft or a scenario changes these values on purpose, update the list from the
output of the failing test (it prints the masked disagreements).
"""

from __future__ import annotations

import pytest

import test_synth_scenarios as T

# (finding, scenario, source unit, display unit, key, GlucoCraft's value, the expected value), as repr()
PINNED: set[tuple] = set()  # no known finding at present (test_synth_scenarios.FINDINGS is empty)


def masked(name: str, tmp_root) -> set[tuple]:
    out = set()
    for n, u, d in T.CASES:
        if n != name:
            continue
        for k, a, b in T.disagreements(n, u, d, tmp_root):
            f = T._known(n, u, k)
            if f:
                out.add((f.id, n, u, d, k, repr(a), repr(b)))
    return out


@pytest.mark.parametrize(
    "name", [pytest.param(n, marks=[pytest.mark.slow] if n in T.SLOW else [], id=n) for n in T.S.NAMES]
)
def test_known_findings_mask_only_the_pinned_disagreements(name, tmp_path_factory):
    got = masked(name, tmp_path_factory.mktemp("masks"))
    want = {p for p in PINNED if p[1] == name}
    assert got == want, (
        "masked now but not pinned:\n"
        + "\n".join(map(repr, sorted(got - want)))
        + "\npinned but no longer masked:\n"
        + "\n".join(map(repr, sorted(want - got)))
    )
