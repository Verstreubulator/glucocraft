# License: AGPL-3.0-only (LICENSE).
"""Which GlucoCraft entities start enabled.

The binary sensors that no GlucoCraft page, card or panel reads start switched off (Home Assistant's
entity_registry_enabled_default) and each one can be enabled on its own. Every binary sensor that the dashboard, the cards or the panels read stays enabled. Existing installations keep the state
their entity registry already holds.
"""

from __future__ import annotations

from glucocraft import binary_sensor as bs

OFF_BY_DEFAULT = {
    bs.GlucoCraftHigh,  # Glucose above range
    bs.GlucoCraftVeryLow,  # Glucose very low
    bs.GlucoCraftVeryHigh,  # Glucose very high
    bs.GlucoCraftInRange,  # Glucose in range
    bs.GlucoCraftInTightRange,  # Glucose in tight range
}
READ_BY_GLUCOCRAFT = {
    bs.GlucoCraftLow,  # Glucose below range: the dashboard
    bs.GlucoCraftNoCurrentData,  # the cards and the panels
    bs.GlucoCraftSensorWarmUp,  # the cards and the panels
    bs.GlucoCraftUsingCustomRanges,  # the cards and the panels
}


def _enabled_default(cls) -> bool:
    """The value a GlucoCraft class sets, or Home Assistant's default (True). Only GlucoCraft's own classes are read:
    the test stub for Home Assistant answers any unknown attribute with a placeholder."""
    for klass in cls.__mro__:
        if klass.__module__.startswith("glucocraft") and "_attr_entity_registry_enabled_default" in vars(klass):
            return vars(klass)["_attr_entity_registry_enabled_default"]
    return True


def test_automation_only_binary_sensors_start_off():
    for cls in OFF_BY_DEFAULT:
        assert _enabled_default(cls) is False, cls.__name__


def test_binary_sensors_read_by_glucocraft_stay_enabled():
    for cls in READ_BY_GLUCOCRAFT:
        assert _enabled_default(cls) is True, cls.__name__


def test_every_binary_sensor_class_is_classified():
    classes = {
        obj
        for obj in vars(bs).values()
        if isinstance(obj, type)
        and issubclass(obj, bs.GlucoCraftBinaryBase)
        and obj is not bs.GlucoCraftBinaryBase
        and not obj.__name__.startswith("_")  # a base class (_RangeBinarySensor) is no entity
    }
    assert classes == OFF_BY_DEFAULT | READ_BY_GLUCOCRAFT
