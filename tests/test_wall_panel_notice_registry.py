# License: AGPL-3.0-only (LICENSE).
"""The wall panels in the device registry (panel_notice._panels).

Rules under test: the panels are the devices of the ESPHome config entries (dr.async_entries_for_config_entry; the
registry's devices mapping is deprecated in Home Assistant); a GlucoCraft panel is a device with the manufacturer
"glucocraft" that is not GlucoCraft's own device; the square panel's model "guition4848s040-480x480" is square,
480 x 480.
"""

from __future__ import annotations

import types

from glucocraft import panel_notice as pn
from glucocraft.const import DISPLAY_CARD_RECTANGULAR, DISPLAY_CARD_SQUARE, DOMAIN


def _device(device_id: str, manufacturer: str | None, model: str, identifiers=frozenset()):
    return types.SimpleNamespace(
        id=device_id, manufacturer=manufacturer, model=model, identifiers=identifiers, name=device_id, name_by_user=None
    )


def test_the_panels_are_the_esphome_entries_devices(monkeypatch):
    by_entry = {
        "esp1": [_device("sq", "glucocraft", "guition4848s040-480x480"), _device("other", "Espressif", "esp32")],
        "esp2": [_device("wide", "GlucoCraft", "waveshare-lcd7-800x480"), _device("none", None, "x")],
        "gc": [_device("own", "GlucoCraft", "Service", frozenset({(DOMAIN, "e1")}))],  # not an ESPHome entry
    }
    asked = []

    def async_entries(domain):
        asked.append(domain)
        return [types.SimpleNamespace(entry_id=e) for e in ("esp1", "esp2")] if domain == "esphome" else []

    hass = types.SimpleNamespace(config_entries=types.SimpleNamespace(async_entries=async_entries))
    registry = types.SimpleNamespace()  # no devices mapping: reading it would fail
    monkeypatch.setattr(pn.dr, "async_get", lambda h: registry, raising=False)
    monkeypatch.setattr(
        pn.dr, "async_entries_for_config_entry", lambda reg, entry_id: by_entry[entry_id], raising=False
    )
    assert sorted(pn._panels(hass)) == ["sq", "wide"]
    assert pn.panel_device_ids(hass) == ["sq", "wide"]
    assert set(asked) == {"esphome"}


def test_the_square_panel_model_is_square():
    assert pn.panel_shape("guition4848s040-480x480") == (DISPLAY_CARD_SQUARE, "480 × 480")
    assert pn.panel_shape("waveshare-p4-7b-1024x600") == (DISPLAY_CARD_RECTANGULAR, "1024 × 600")
