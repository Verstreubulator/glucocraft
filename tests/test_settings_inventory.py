# License: AGPL-3.0-only (LICENSE).
"""The settings inventory of the test matrix: every stored setting, its values, its default and the outputs it affects.

The test matrix is built from INVENTORY below. The tests here keep the inventory complete: every setting key of
const.py is in it (or named as not a setting, with the reason), every key the setup and options screens store is in
it, every reader named in it reads the key, and every configuration attribute the cards read is published.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from glucocraft import const as C

from test_settings_harness import DEFAULT_ENTRY, build, fingerprint, tz_patch  # noqa: F401 (the fixture)

pytestmark = pytest.mark.usefixtures("tz_patch")

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "glucocraft"
WWW = PKG / "www"

# key -> (allowed values, default, the modules that read it)
ZONE_KEYS = tuple(C.CONF_ZONE_COLORS.values())
BAND_KEYS = tuple(C.CONF_BAND_COLORS.values())
INVENTORY: dict[str, tuple[tuple, object, tuple[str, ...]]] = {
    # ---- sources
    C.CONF_GLUCOSE_ENTITY: (("<sensor entity>",), None, ("coordinator.py",)),
    C.CONF_SENSOR_EXPIRY_ENTITY: (("<sensor entity>", None), None, ("coordinator.py",)),  # the entity asks it
    C.CONF_SECONDARY_ENTITY: (("<sensor entity>", None), None, ("coordinator.py",)),
    C.CONF_PRIMARY_TIME_ATTR: (("", "<attribute name>"), "", ("coordinator.py",)),
    C.CONF_SECONDARY_TIME_ATTR: (("", "<attribute name>"), "", ("coordinator.py",)),
    C.CONF_SENSOR_LOWER_LIMIT: (tuple(C.SENSOR_LOWER_LIMIT_OPTIONS), "auto", ("coordinator.py",)),
    C.CONF_SENSOR_UPPER_LIMIT: (tuple(C.SENSOR_UPPER_LIMIT_OPTIONS), "auto", ("coordinator.py",)),
    C.CONF_SECONDARY_SENSOR_LOWER_LIMIT: (tuple(C.SENSOR_LOWER_LIMIT_OPTIONS), "auto", ("coordinator.py",)),
    C.CONF_SECONDARY_SENSOR_UPPER_LIMIT: (tuple(C.SENSOR_UPPER_LIMIT_OPTIONS), "auto", ("coordinator.py",)),
    # ---- data and display
    C.CONF_STALE_THRESHOLD: (tuple(range(5, 31)), C.DEFAULT_STALE_THRESHOLD, ("coordinator.py",)),
    C.CONF_UNIT: (("mg/dL", "mmol/L"), "mg/dL", ("coordinator.py",)),
    C.CONF_TIME_FORMAT: (("auto", "12", "24"), "auto", ("const.py", "regional.py")),
    C.CONF_DATE_FORMAT: (C.DATE_FORMAT_OPTIONS, "auto", ("const.py", "regional.py")),
    C.CONF_FIRST_WEEKDAY: (("auto", "monday", "sunday", "saturday"), "auto", ("const.py", "regional.py")),
    C.CONF_DECIMAL_SEPARATOR: (("auto", "point", "comma"), "auto", ("const.py", "regional.py")),
    C.CONF_A1C_UNIT: (("auto", "percent", "mmol_mol", "both"), "auto", ("const.py", "regional.py")),
    C.CONF_PAPER_SIZE: (("auto", "letter", "a4"), "auto", ("const.py", "regional.py")),
    C.CONF_DISPLAY_CARD: (("square", "rectangular"), "square", ("coordinator.py", "panel_notice.py")),
    C.CONF_TREND_DISPLAY: (C.TREND_DISPLAY_OPTIONS, "arrow", ("coordinator.py",)),
    **{k: (("#RRGGBB", None), None, ("const.py",)) for k in ZONE_KEYS},
    **{k: (("#RRGGBB", None), None, ("settings.py",)) for k in BAND_KEYS},
    C.CONF_CARD_BACKGROUND: (("#RRGGBB", "theme"), C.DEFAULT_CARD_BACKGROUND, ("settings.py", "coordinator.py")),
    C.CONF_PANEL_BACKGROUND: (("#RRGGBB",), C.DEFAULT_PANEL_BACKGROUND, ("settings.py", "coordinator.py")),
    C.CONF_TEXT_OUTLINE: ((False, True), False, ("coordinator.py",)),
    C.CONF_FLASH_NO_CURRENT_DATA: ((True, False), True, ("coordinator.py",)),
    C.CONF_WEEKLY_PAGES: (("consensus", "personal"), "consensus", ("coordinator.py",)),
    # ---- ranges (mg/dL) and goals (%)
    C.CONF_RANGE_CRITICAL_LOW: (tuple(range(30, 71)), 54, ("coordinator.py",)),
    C.CONF_RANGE_LOW: (tuple(range(50, 101)), 70, ("coordinator.py",)),
    C.CONF_RANGE_TIGHT_LOW: (tuple(range(50, 101)), 70, ("coordinator.py",)),
    C.CONF_RANGE_TIGHT_HIGH: (tuple(range(100, 201)), 140, ("coordinator.py",)),
    C.CONF_RANGE_HIGH: (tuple(range(120, 301)), 180, ("coordinator.py",)),
    C.CONF_RANGE_CRITICAL_HIGH: (tuple(range(180, 401)), 250, ("coordinator.py",)),
    C.CONF_GOAL_TIME_IN_TARGET_RANGE: (tuple(range(50, 101)), 70, ("coordinator.py",)),
    C.CONF_GOAL_TIME_IN_IDEAL_RANGE: ((*range(20, 101), None), None, ("coordinator.py",)),
    C.CONF_GOAL_TBR: (tuple(range(1, 21)), 4, ("coordinator.py",)),
    C.CONF_GOAL_TBR_CRITICAL: (tuple(range(11)), 1, ("coordinator.py",)),
    C.CONF_GOAL_TAR: (tuple(range(5, 51)), 25, ("coordinator.py",)),
    C.CONF_GOAL_TAR_CRITICAL: (tuple(range(1, 21)), 5, ("coordinator.py",)),
    C.CONF_GOAL_CV: (tuple(range(15, 51)), 36, ("coordinator.py",)),
    C.CONF_GOAL_GMI: (("4.0 .. 12.0 (0.1 steps)", None), None, ("coordinator.py",)),
    # ---- notifications
    C.CONF_NOTIFY_ENABLED: ((True, False), True, ("__init__.py", "coordinator.py")),
    C.CONF_NOTIFY_TIME: (("HH:MM[:SS]",), "08:00", ("__init__.py", "coordinator.py")),
    C.CONF_NOTIFY_WEEKLY_ENABLED: ((True, False), True, ("__init__.py", "coordinator.py")),
    C.CONF_NOTIFY_WEEKLY_DAY: (tuple(range(7)), 6, ("notification.py", "coordinator.py")),
    C.CONF_NO_DATA_NOTICE: ((True, False), False, ("notification.py", "coordinator.py")),
    C.CONF_NO_DATA_NOTICE_DELAY: (C.NO_DATA_NOTICE_DELAYS, 30, ("notification.py", "coordinator.py")),
    C.CONF_PHONE_TARGETS: (("[notify action names]",), [], ("notification.py", "coordinator.py")),
    C.CONF_PHONE_TYPES: ((*C.PHONE_TYPES,), list(C.DEFAULT_PHONE_TYPES), ("notification.py", "coordinator.py")),
    # ---- other
    C.CONF_PRINTER: (("", "<host or ipp:// address>"), "", ("report_service.py",)),
    C.CONF_SHOW_DASHBOARD: ((True, False), True, ("__init__.py", "coordinator.py")),
    C.CONF_ON_REMOVAL: (C.ON_REMOVAL_OPTIONS, "keep", ("__init__.py", "coordinator.py")),
    C.CONF_PANEL_FEED: ((True, False), False, ("panel_feed.py",)),
    C.CONF_PANEL_WEBHOOK_ID: (("glucocraft_panel_<32 hex>", None), None, ("panel_feed.py", "coordinator.py")),
    # ---- the optional features (off in a new installation)
    C.CONF_CUSTOM_RANGES_AND_GOALS: ((False, True), False, ("settings.py", "coordinator.py", "config_flow.py")),
    C.CONF_SEPARATE_STATISTICS: ((False, True), False, ("settings.py", "coordinator.py", "flow_schemas.py")),
}

# Keys of const.py that are not stored settings, with the reason
NOT_SETTINGS = {
    C.CONF_PHONE_TEST: "a one-shot action of the screens (send a test now); never stored",
    C.CONF_REGENERATE_PANEL_WEBHOOK: "a one-shot action of the screens: makes a new address and sets panel_feed",
}


def _const_setting_keys() -> dict[str, str]:
    """Every CONF_* text constant of const.py (name -> stored key)."""
    return {n: v for n, v in vars(C).items() if n.startswith("CONF_") and isinstance(v, str)}


def test_every_setting_key_of_const_is_in_the_inventory():
    for name, key in _const_setting_keys().items():
        assert key in INVENTORY or key in NOT_SETTINGS, f"{name} = {key!r}: add it to the matrix"
    for key in (*ZONE_KEYS, *BAND_KEYS):
        assert key in INVENTORY


def test_every_key_the_screens_store_is_in_the_inventory():
    """The screens are being reorganized; the keys they store do not change. Every CONF_* name used in
    config_flow.py and its forms (flow_schemas.py) is in the inventory or named as not a setting."""
    src = "\n".join((PKG / name).read_text(encoding="utf-8") for name in ("config_flow.py", "flow_schemas.py"))
    keys = _const_setting_keys()
    for name in sorted(set(re.findall(r"\bCONF_[A-Z_]+\b", src))):
        if name in keys:
            assert keys[name] in INVENTORY or keys[name] in NOT_SETTINGS, name


@pytest.mark.parametrize("key", sorted(INVENTORY))
def test_each_named_reader_reads_the_key(key):
    """The readers in the matrix are real: each named module refers to the key (by its constant or its text)."""
    const_names = [n for n, v in _const_setting_keys().items() if v == key]
    for module in INVENTORY[key][2]:
        src = (PKG / module).read_text(encoding="utf-8")
        if key in ZONE_KEYS or key in BAND_KEYS:
            assert ("CONF_ZONE_COLORS" if key in ZONE_KEYS else "CONF_BAND_COLORS") in src, (key, module)
            continue
        assert any(re.search(rf"\b{n}\b", src) for n in const_names) or f'"{key}"' in src, (key, module)


def test_every_default_matches_const():
    defaults = {
        C.CONF_STALE_THRESHOLD: C.DEFAULT_STALE_THRESHOLD,
        C.CONF_RANGE_CRITICAL_LOW: C.DEFAULT_RANGE_CRITICAL_LOW,
        C.CONF_RANGE_LOW: C.DEFAULT_RANGE_LOW,
        C.CONF_RANGE_TIGHT_LOW: C.DEFAULT_RANGE_TIGHT_LOW,
        C.CONF_RANGE_TIGHT_HIGH: C.DEFAULT_RANGE_TIGHT_HIGH,
        C.CONF_RANGE_HIGH: C.DEFAULT_RANGE_HIGH,
        C.CONF_RANGE_CRITICAL_HIGH: C.DEFAULT_RANGE_CRITICAL_HIGH,
        C.CONF_GOAL_TIME_IN_TARGET_RANGE: C.DEFAULT_GOAL_TIME_IN_TARGET_RANGE,
        C.CONF_GOAL_TIME_IN_IDEAL_RANGE: C.DEFAULT_GOAL_TIME_IN_IDEAL_RANGE,
        C.CONF_GOAL_TBR: C.DEFAULT_GOAL_TBR,
        C.CONF_GOAL_TBR_CRITICAL: C.DEFAULT_GOAL_TBR_CRITICAL,
        C.CONF_GOAL_TAR: C.DEFAULT_GOAL_TAR,
        C.CONF_GOAL_TAR_CRITICAL: C.DEFAULT_GOAL_TAR_CRITICAL,
        C.CONF_GOAL_CV: C.DEFAULT_GOAL_CV,
        C.CONF_GOAL_GMI: C.DEFAULT_GOAL_GMI,
        C.CONF_NOTIFY_WEEKLY_DAY: C.DEFAULT_NOTIFY_WEEKLY_DAY,
        C.CONF_PHONE_TYPES: C.DEFAULT_PHONE_TYPES,
        C.CONF_TREND_DISPLAY: C.TREND_DISPLAY_ARROW,
        C.CONF_WEEKLY_PAGES: C.WEEKLY_PAGES_CONSENSUS,
        C.CONF_DISPLAY_CARD: C.DISPLAY_CARD_SQUARE,
        C.CONF_CARD_BACKGROUND: C.DEFAULT_CARD_BACKGROUND,
        C.CONF_PANEL_BACKGROUND: C.DEFAULT_PANEL_BACKGROUND,
        C.CONF_TEXT_OUTLINE: False,
        C.CONF_FLASH_NO_CURRENT_DATA: True,
        C.CONF_NO_DATA_NOTICE: False,
        C.CONF_NO_DATA_NOTICE_DELAY: C.DEFAULT_NO_DATA_NOTICE_DELAY,
        C.CONF_CUSTOM_RANGES_AND_GOALS: False,
        C.CONF_SEPARATE_STATISTICS: False,
    }
    for key, default in defaults.items():
        assert INVENTORY[key][1] == default, key
        if INVENTORY[key][0] and not str(INVENTORY[key][0][0]).startswith(("<", "[", "#", "H", "4")):
            assert (
                all(d in INVENTORY[key][0] for d in default)
                if isinstance(default, list)
                else default in INVENTORY[key][0]
            ), key


def test_an_entry_with_only_a_source_runs_on_every_default(tmp_path):
    """Missing keys: an entry that holds only the glucose source gives exactly the outputs of the stored defaults, on
    every output."""
    now = int(time.time())
    bare = {C.CONF_GLUCOSE_ENTITY: DEFAULT_ENTRY[C.CONF_GLUCOSE_ENTITY]}
    assert fingerprint(build(tmp_path / "a", bare, now=now)) == fingerprint(
        build(tmp_path / "b", DEFAULT_ENTRY, now=now)
    )


def _card_reads() -> set[str]:
    """The Configuration sensor attributes the cards read (`ca.<key>`, also in their shared module; the report card's
    `ca` is its appendix)."""
    keys: set[str] = set()
    for name in ("glucocraft-display-card.js", "glucocraft-ui.js", "glucocraft-shared.js"):
        keys |= set(re.findall(r"\bca\??\.([a-z_0-9]+)", (WWW / name).read_text(encoding="utf-8")))
    return keys


def test_every_configuration_attribute_the_cards_read_is_published(tmp_path):
    cfg = build(tmp_path).config()
    missing = sorted(k for k in _card_reads() if k not in cfg)
    assert not missing, missing
    assert {"unit", "time_format", "date_format", "decimal_separator", "a1c_unit", "zone_colors"} <= _card_reads()


def test_the_entry_keys_are_the_inventory():
    """settings.ENTRY_KEYS (the keys GlucoCraft knows; the diagnostics redact the value of any other stored key) holds
    exactly the inventory's settings."""
    from glucocraft.settings import ENTRY_KEYS

    assert set(ENTRY_KEYS) == set(INVENTORY)
