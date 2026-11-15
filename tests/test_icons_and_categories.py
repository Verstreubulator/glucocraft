# License: AGPL-3.0-only (LICENSE).
"""The icons (icons.json) and the entity categories of GlucoCraft's entities.

Rules under test (Home Assistant's quality-scale rules icon-translations and entity-category):
- Every entity and every action has its icon in icons.json, in Home Assistant's format: "entity" by platform and
  translation key (the keys of strings.json), with a "default" icon and, where the state names a different situation,
  one icon per state, each a state of that entity; "services" by action. No entity sets an icon in the code.
- Exactly the technical entities are diagnostic (EntityCategory.DIAGNOSTIC): Configuration, Active source, Data
  history, Sensor expiry, Sensor warm-up and, per period, the readings and sensor active sensors. No entity that shows glucose
  information has a category.
"""

from __future__ import annotations

import json
import re

import pytest
from glucocraft import binary_sensor as bs
from glucocraft import sensor as se

from conftest import REPO

PKG = REPO / "custom_components" / "glucocraft"
ICONS = json.loads((PKG / "icons.json").read_text(encoding="utf-8"))
STRINGS = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))
ICON = re.compile(r"mdi:[a-z0-9]+(?:-[a-z0-9]+)*")


def test_every_entity_and_action_has_an_icon():
    assert set(ICONS) == {"entity", "services"}
    for platform in ("sensor", "binary_sensor"):
        assert set(ICONS["entity"][platform]) == set(STRINGS["entity"][platform]), platform
    assert set(ICONS["services"]) == set(STRINGS["services"])


def _icons():
    for platform, entities in ICONS["entity"].items():
        for key, icon in entities.items():
            yield platform, key, icon


@pytest.mark.parametrize("platform, key, icon", list(_icons()), ids=[f"{p}.{k}" for p, k, _ in _icons()])
def test_each_entity_icon(platform, key, icon):
    assert set(icon) <= {"default", "state"} and ICON.fullmatch(icon["default"]), (platform, key)
    states = icon.get("state", {})
    known = set(STRINGS["entity"][platform][key].get("state", {})) or {"on", "off"}
    assert set(states) <= known, (platform, key, set(states) - known)
    for state, name in states.items():
        assert ICON.fullmatch(name) and name != icon["default"], (platform, key, state)


def test_each_action_icon():
    for action, icon in ICONS["services"].items():
        assert set(icon) == {"service"} and ICON.fullmatch(icon["service"]), action


def test_no_icon_in_the_code():
    for path in sorted(PKG.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        assert "_attr_icon" not in src and not re.search(r"def icon\(", src), path.name


# ------------------------------------------------------------------------------------------------ entity categories
DIAGNOSTIC = {
    se.GlucoCraftConfiguration,  # every setting
    se.GlucoCraftActiveSource,  # which entity delivers the readings
    se.GlucoCraftDataHistory,  # the size of GlucoCraft's own store
    se.GlucoCraftSensorExpiry,  # the state of the CGM sensor (the device)
    bs.GlucoCraftSensorWarmUp,  # the state of the CGM sensor (the device)
    se.GlucoCraftPeriodReadingCount,  # a count of stored readings
    se.GlucoCraftPeriodDataAvailability,  # how complete the period's data are
}


def _category_set(cls) -> bool:
    """True when a GlucoCraft class sets a category. Only GlucoCraft's own classes are read: the test stub for Home
    Assistant answers any unknown attribute with a placeholder, also EntityCategory.DIAGNOSTIC."""
    for klass in cls.__mro__:
        if klass.__module__.startswith("glucocraft") and "_attr_entity_category" in vars(klass):
            return vars(klass)["_attr_entity_category"] is not None
    return False


def _entity_classes():
    for module in (se, bs):
        for cls in vars(module).values():
            if isinstance(cls, type) and cls.__module__ == module.__name__ and "__init__" in vars(cls):
                yield cls


@pytest.mark.parametrize("cls", list(_entity_classes()), ids=lambda cls: cls.__name__)
def test_only_the_technical_entities_are_diagnostic(cls):
    assert _category_set(cls) is (cls in DIAGNOSTIC), cls.__name__


def test_the_category_is_diagnostic():
    """The category set is Home Assistant's diagnostic category, once per diagnostic class."""
    src = (PKG / "sensor.py").read_text(encoding="utf-8") + (PKG / "binary_sensor.py").read_text(encoding="utf-8")
    assert src.count("_attr_entity_category = EntityCategory.DIAGNOSTIC") == len(DIAGNOSTIC)
