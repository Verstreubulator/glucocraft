# License: AGPL-3.0-only (LICENSE).
"""GlucoCraft entity ids.

Rule: an entity's id is ``<platform>.glucocraft_<object id of its display name>`` (lower case, every run of other
characters one underscore: "14D custom time in tight range" -> ``sensor.glucocraft_14d_custom_time_in_tight_range``).
An entity and everything that refers to it use the same id, so a rename changes the name, the id and every consumer
together. The entities pass their object ids as literals (sensor.py, binary_sensor.py); tests/test_entity_ids.py
checks that each one is the object id of the English name by this rule, so a rename must change the literal too. The
unique ids never change. Pure Python (no Home Assistant import), so the tests use the same rule.
"""

from __future__ import annotations

import re

ID_PREFIX = "glucocraft_"


def object_id(name: str) -> str:
    """Return the object id of a display name.

    Lower case, every run of characters other than a-z and 0-9 one underscore (Home Assistant's slugify gives the
    same result for these ASCII names).
    """
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
