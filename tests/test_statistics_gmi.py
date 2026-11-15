# License: AGPL-3.0-only (LICENSE).
"""The glucose management indicator against the published equations and their example table.

Bergenstal et al. (2018), Diabetes Care 41(11):2275-2280, https://doi.org/10.2337/dc18-1581, Figure 1 and Table 1:
GMI (%) = 3.31 + 0.02392 x [mean glucose in mg/dL]; GMI (mmol/mol) = 12.71 + 4.70587 x [mean glucose in mmol/L].
Table 1 lists GMI in % for means in mg/dL and GMI in mmol/mol for means in mmol/L, each equation in its own unit.
GlucoCraft applies the mmol/mol equation to the mean in mmol/L as it shows it (the mean in mg/dL / 18.016).
"""

from __future__ import annotations

import pytest
from glucocraft import units as U
from glucocraft.statistics import compute_period_stats

from conftest import AGP

# Table 1 of the paper: mean glucose (mg/dL) -> GMI (%)
TABLE_PERCENT = [
    (100, 5.7),
    (125, 6.3),
    (150, 6.9),
    (175, 7.5),
    (200, 8.1),
    (225, 8.7),
    (250, 9.3),
    (275, 9.9),
    (300, 10.5),
    (350, 11.7),
]
# Table 1 of the paper: mean glucose (mmol/L) -> GMI (mmol/mol), one decimal as published
TABLE_MMOL_MOL = [
    (5, 36.2),
    (6, 40.9),
    (7, 45.7),
    (8, 50.4),
    (9, 55.1),
    (10, 59.8),
    (12, 69.2),
    (14, 78.6),
    (16, 88.0),
    (18, 97.4),
]


@pytest.mark.parametrize("mean_mgdl, gmi", TABLE_PERCENT)
def test_gmi_percent_matches_table_1(mean_mgdl, gmi):
    assert U.round_half_up(U.gmi_percent(mean_mgdl), 1) == gmi


@pytest.mark.parametrize("mean_mmoll, gmi", TABLE_MMOL_MOL)
def test_gmi_mmol_mol_matches_table_1(mean_mmoll, gmi):
    """The mmol/mol equation applied to the mean in mmol/L: a source in mmol/L whose mean is 7.0 mmol/L gets the
    paper's 45.7 mmol/mol, shown as 46."""
    mean_mgdl = mean_mmoll * U.MGDL_PER_MMOLL  # the mean as GlucoCraft stores it
    assert U.round_half_up(U.gmi_mmol_mol_exact(mean_mgdl), 1) == gmi
    assert U.gmi_mmol_mol(mean_mgdl) == U.round_half_up(gmi, 0)  # shown: whole numbers, rounded once


def test_each_equation_in_its_own_unit():
    # the equations as published, not one derived from the other
    assert U.gmi_percent(150) == pytest.approx(3.31 + 0.02392 * 150)
    assert U.gmi_mmol_mol_exact(150) == pytest.approx(12.71 + 4.70587 * (150 / 18.016))
    assert U.gmi_percent(None) is None and U.gmi_mmol_mol_exact(None) is None and U.gmi_mmol_mol(None) is None


def test_the_statistics_use_the_unrounded_mean(make_readings):
    """A period's GMI comes from the unrounded time-weighted mean, rounded once (one decimal in %, whole mmol/mol)."""
    s = compute_period_stats(make_readings([150.0, 151.0, 151.0]), "14d", 900, 300, **AGP)
    assert s.mean == pytest.approx(452 / 3)
    assert s.gmi == U.round_half_up(U.gmi_percent(s.mean), 1)
    assert s.gmi_mmol_mol == U.round_half_up(U.gmi_mmol_mol_exact(s.mean), 0)
