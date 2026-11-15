# License: AGPL-3.0-only (LICENSE).
"""What a failed message to a phone leaves in the log: notification.async_forward_to_phones (the summaries and the
no-data notice) and the delivery test of the settings screens (config_flow._send_phone_test).

The stand-in service registry below runs a notify action as Home Assistant's registry does: a call that waits
(blocking) raises the action's error to the caller; a call that does not wait runs in the background, and a failure
there is logged by Home Assistant itself with the whole call (its data, so the message text) and the error's text
("Error executing service"). The Companion app's errors name the phone: its webhook id or its device name. Neither the
id, the name, the notify action's name nor the message text may reach the log, and the other phones still get the
message. tests_ha/test_phone_messages.py runs the same on Home Assistant's own registry.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import types

import pytest
from glucocraft import config_flow as cf
from glucocraft import const as C
from glucocraft import notification as N
from glucocraft.i18n import t
from homeassistant.exceptions import HomeAssistantError

from conftest import task_list

WEBHOOK_ID = "3f9c0a7e" * 8  # 64 hexadecimal characters, as a mobile_app webhook id
DEVICE = "Jane Doe Pixel"
TARGETS = ["mobile_app_anna_phone", "mobile_app_jane_doe_pixel", "mobile_app_ben_phone"]
FAILING = "mobile_app_jane_doe_pixel"
TITLE = "GlucoCraft Daily Summary"
MESSAGE = "**GlucoCraft Daily Summary**\n\nMean glucose 154 mg/dL. Last reading 6:05 AM."

# The two kinds of error text of the Companion app's notify action: the webhook id (a phone with local push only that
# is not connected) and the device name (a cloud push that failed)
ERRORS = {
    "webhook id": f"Device(s) with webhook id(s) {WEBHOOK_ID} not connected to local push notifications",
    "device name": f"Error sending notification to {DEVICE}: rate limit reached",
}


class HomeAssistantServices:
    """Home Assistant's service registry as far as a notify action goes (core.ServiceRegistry.async_call)."""

    def __init__(self, error_text: str, available=TARGETS) -> None:
        self.error_text = error_text
        self.available = set(available)
        self.sent: list[tuple[str, dict]] = []
        self.background: list[asyncio.Task] = []

    def has_service(self, domain: str, name: str) -> bool:
        """True for the notify actions made available to this stand-in."""
        return domain == "notify" and name in self.available

    async def _run(self, name: str, data: dict) -> None:
        """The notify action itself: the failing phone raises the Companion app's error, the others receive."""
        await asyncio.sleep(0)
        if name == FAILING:
            raise HomeAssistantError(self.error_text)
        self.sent.append((name, dict(data)))

    async def async_call(self, domain, service, data=None, blocking=False, **kwargs):
        """Wait for the action (blocking), or run it in the background and log its failure with the whole call, as
        Home Assistant does."""
        data = dict(data or {})
        if blocking:
            await self._run(service, data)
            return

        async def background() -> None:
            try:
                await self._run(service, data)
            except Exception:
                logging.getLogger("homeassistant.core").exception(
                    "Error executing service: %s", f"<ServiceCall {domain}.{service}: {data!r}>"
                )

        self.background.append(asyncio.ensure_future(background()))


def _run(services: HomeAssistantServices, coro) -> None:
    """Run `coro`, then every action call it left running in the background."""

    async def main() -> None:
        await coro
        await asyncio.gather(*services.background)

    asyncio.run(main())


def _assert_nothing_private(caplog) -> None:
    """Neither the phone's webhook id, its device name, a notify action's name nor the message text is in the log."""
    logged = caplog.text + " ".join(str(r.exc_info) for r in caplog.records if r.exc_info)
    for private in (
        WEBHOOK_ID,
        DEVICE,
        "jane",
        "anna",
        "ben_phone",
        "Mean glucose",
        "6:05",
        t("notify.phone_test.message"),
    ):
        assert private.lower() not in logged.lower(), private


@pytest.mark.parametrize("error", ERRORS)
def test_a_failed_phone_logs_only_its_position_and_the_error_class(error, caplog):
    services = HomeAssistantServices(ERRORS[error])
    hass = types.SimpleNamespace(services=services)
    config = {C.CONF_PHONE_TARGETS: TARGETS, C.CONF_PHONE_TYPES: ["summaries"]}
    with caplog.at_level(logging.DEBUG):
        _run(services, N.async_forward_to_phones(hass, config, "summaries", TITLE, MESSAGE))
    _assert_nothing_private(caplog)
    assert [r.getMessage() for r in caplog.records if r.name != "asyncio"] == [
        f"GlucoCraft: sending {TITLE} to notify action 2 of 3 failed: HomeAssistantError"
    ]
    # one broken phone does not stop the others: the ones before and after it get the message, as plain text
    assert [name for name, _ in services.sent] == ["mobile_app_anna_phone", "mobile_app_ben_phone"]
    assert all(data["message"].startswith("GlucoCraft Daily Summary\n\nMean glucose") for _, data in services.sent)


@pytest.mark.parametrize("error", ERRORS)
def test_a_failed_delivery_test_logs_only_its_position_and_the_error_class(error, caplog):
    services = HomeAssistantServices(ERRORS[error])
    hass = types.SimpleNamespace(services=services)
    with caplog.at_level(logging.DEBUG):
        _run(services, cf._send_phone_test(hass, TARGETS))
    _assert_nothing_private(caplog)
    assert [r.getMessage() for r in caplog.records if r.name != "asyncio"] == [
        "GlucoCraft delivery test to notify action 2 of 3 failed: HomeAssistantError"
    ]
    assert [name for name, _ in services.sent] == ["mobile_app_anna_phone", "mobile_app_ben_phone"]


def test_every_notify_call_waits_for_the_action():
    """Both places that call a notify action wait for it (blocking), so that its failure reaches their handler."""
    for fn in (N.async_forward_to_phones, cf._send_phone_test):
        src = inspect.getsource(fn)
        assert src.count("async_call(") == 1 and "blocking=True" in src, fn.__name__


def test_saving_the_settings_does_not_wait_for_the_delivery_test():
    """Configure saves at once: the delivery test runs as a task of its own (a phone can take up to the Companion
    app's push timeout)."""
    services = HomeAssistantServices("unused", available=["mobile_app_anna_phone"])
    tasks = task_list()
    updated: list[dict] = []

    async def reload(entry_id: str) -> None:
        return None

    flow = cf.GlucoCraftOptionsFlow(types.SimpleNamespace(data={}, entry_id="e"))
    flow.hass = types.SimpleNamespace(
        services=services,
        async_create_task=tasks.append,
        config_entries=types.SimpleNamespace(
            async_update_entry=lambda entry, data: updated.append(data), async_reload=reload
        ),
    )
    flow.async_create_entry = lambda **kw: ("done", kw)
    flow._updates = {C.CONF_PHONE_TEST: True, C.CONF_PHONE_TARGETS: ["mobile_app_anna_phone"]}
    assert asyncio.run(flow._async_finish()) == ("done", {"title": "", "data": {}})
    assert services.sent == [] and len(tasks) == 1  # saved; the test message not yet sent
    assert C.CONF_PHONE_TEST not in updated[0]
    asyncio.run(tasks.pop())
    assert [name for name, _ in services.sent] == ["mobile_app_anna_phone"]
    assert services.sent[0][1]["title"] == t("notify.phone_test.title")
    # the first setup starts it the same way
    src = inspect.getsource(cf.GlucoCraftConfigFlow.async_step_finish)
    assert "self.hass.async_create_task(_send_phone_test(" in src and "await _send_phone_test(" not in src
