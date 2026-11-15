# License: AGPL-3.0-only (LICENSE).
"""The wall-panel address on Home Assistant's webhook component.

Rules under test:
- A new installation has no wall-panel address; Create a new wall-panel address makes the first one.
- A GET returns the GLU2 file (backfill and delta); any other method is refused (405); an unknown file is 404.
- A new address replaces the earlier one, which then answers nothing.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from conftest import new_reading, run_setup, save_screen, set_source


async def _with_address(hass: HomeAssistant, freezer):
    set_source(hass, "100")
    entry = await run_setup(hass)
    assert not entry.data.get("panel_webhook_id")
    await save_screen(hass, entry, "displays", {"regenerate_panel_webhook": True})
    assert entry.data["panel_webhook_id"] and entry.data["panel_feed"] is True
    await new_reading(hass, freezer, "110")
    return entry


async def test_a_get_returns_the_panel_file_and_other_methods_are_refused(
    hass: HomeAssistant, hass_client_no_auth, freezer
):
    entry = await _with_address(hass, freezer)
    client = await hass_client_no_auth()
    url = f"/api/webhook/{entry.data['panel_webhook_id']}"
    for which in ("backfill", "delta"):
        response = await client.get(url, params={"file": which})
        assert response.status == 200, which
        body = await response.read()
        assert body[:4] == b"GLU2", which
        assert response.headers["Content-Type"] == "application/octet-stream"
    response = await client.get(url, params={"file": "other"})
    assert response.status == 404
    for method in ("post", "put"):
        response = await getattr(client, method)(url, data=b"x")
        assert response.status == 405, method
        assert response.headers.get("Allow") == "GET"


async def test_a_new_address_replaces_the_earlier_one(hass: HomeAssistant, hass_client_no_auth, freezer):
    entry = await _with_address(hass, freezer)
    old = entry.data["panel_webhook_id"]
    await save_screen(hass, entry, "displays", {"regenerate_panel_webhook": True})
    new = entry.data["panel_webhook_id"]
    assert new != old
    client = await hass_client_no_auth()
    assert (await client.get(f"/api/webhook/{new}")).status == 200
    response = await client.get(f"/api/webhook/{old}")
    assert response.status == 200 and not await response.read()  # Home Assistant answers an unknown id with nothing
