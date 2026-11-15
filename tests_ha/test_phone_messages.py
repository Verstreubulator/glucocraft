# License: AGPL-3.0-only (LICENSE).
"""A failed message to a phone on Home Assistant's own service registry: what reaches the log.

Rules under test:
- Home Assistant logs a notify call that does not wait and fails with the whole call (the message text) and the
  error's text; the Companion app's error text names the phone (its webhook id).
- GlucoCraft's messages to the phones (the summaries, the no-data notice) and the delivery test wait for each action,
  so that the log names the failed phone by its position and the error's class only; the other phones still get the
  message.

The log is read at INFO and above: at DEBUG, Home Assistant's recorder logs every event it stores, an action call
with its data included.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from custom_components.glucocraft import config_flow, notification
from custom_components.glucocraft.const import CONF_PHONE_TARGETS, CONF_PHONE_TYPES

WEBHOOK_ID = "3f9c0a7e" * 8  # 64 hexadecimal characters, as a mobile_app webhook id
TARGETS = ["mobile_app_anna_phone", "mobile_app_jane_doe_pixel", "mobile_app_ben_phone"]
FAILING = "mobile_app_jane_doe_pixel"
TITLE = "GlucoCraft Daily Summary"
MESSAGE = "Mean glucose 154 mg/dL. Last reading 6:05 AM."


def _register_phones(hass: HomeAssistant) -> list[tuple[str, dict]]:
    """Three notify actions; the second raises the Companion app's error of a phone with local push only that is not
    connected. Returns the list of messages received."""
    received: list[tuple[str, dict]] = []

    async def handler(call: ServiceCall) -> None:
        if call.service == FAILING:
            raise HomeAssistantError(
                f"Device(s) with webhook id(s) {WEBHOOK_ID} not connected to local push notifications"
            )
        received.append((call.service, dict(call.data)))

    for name in TARGETS:
        hass.services.async_register("notify", name, handler)
    return received


def _private_in_log(caplog) -> list[str]:
    """The private texts found in the log (each record with its traceback): the webhook id, the phone's action name,
    the message text. The test database's own statement log (sqlalchemy, on in the test harness only) is left out."""
    form = logging.Formatter()
    text = " ".join(form.format(r) for r in caplog.records if not r.name.startswith("sqlalchemy")).lower()
    return [p for p in (WEBHOOK_ID, "jane_doe", "mean glucose", "6:05") if p.lower() in text]


async def test_home_assistant_logs_a_failed_call_that_does_not_wait(hass: HomeAssistant, caplog) -> None:
    """The reason GlucoCraft waits: a call that does not wait is logged by Home Assistant with its data and error."""
    _register_phones(hass)
    with caplog.at_level(logging.ERROR):
        await hass.services.async_call("notify", FAILING, {"title": TITLE, "message": MESSAGE})
        await hass.async_block_till_done()
    assert _private_in_log(caplog) == [WEBHOOK_ID, "jane_doe", "mean glucose", "6:05"]


async def test_a_failed_phone_leaves_no_message_and_no_id_in_the_log(hass: HomeAssistant, caplog) -> None:
    received = _register_phones(hass)
    config = {CONF_PHONE_TARGETS: TARGETS, CONF_PHONE_TYPES: ["summaries"]}
    with caplog.at_level(logging.INFO):
        await notification.async_forward_to_phones(hass, config, "summaries", TITLE, MESSAGE)
        await hass.async_block_till_done()
    assert _private_in_log(caplog) == []
    assert f"sending {TITLE} to notify action 2 of 3 failed: HomeAssistantError" in caplog.text
    assert [name for name, _ in received] == ["mobile_app_anna_phone", "mobile_app_ben_phone"]
    assert all(data == {"title": TITLE, "message": MESSAGE} for _, data in received)


async def test_a_failed_delivery_test_leaves_no_id_in_the_log(hass: HomeAssistant, caplog) -> None:
    received = _register_phones(hass)
    with caplog.at_level(logging.INFO):
        await config_flow._send_phone_test(hass, TARGETS)
        await hass.async_block_till_done()
    assert _private_in_log(caplog) == []
    assert "delivery test to notify action 2 of 3 failed: HomeAssistantError" in caplog.text
    assert [name for name, _ in received] == ["mobile_app_anna_phone", "mobile_app_ben_phone"]
