"""Pluggable SMS sender. R3-06 is explicitly interim here: no real SMS
provider credentials exist anywhere in this codebase (and the task
constraints forbid inventing any), so the only sender wired up today is
``MockSmsSender`` - it never calls out to a real carrier, just logs the
message and manufactures a fake provider reference, exactly like
``ConsoleEmailSender`` does for email in app/integrations/notifications/email.py.

``SmsSender`` is a real interface (a ``Protocol``, matching ``EmailSender``'s
own shape) so a future task can add a real adapter (Twilio, MSG91, an
Indian DLT-registered aggregator, ...) by implementing ``send`` and wiring
it into ``get_sms_sender`` - reading whatever provider-specific env vars it
needs directly (there is no SMS_* block in app/core/config.py yet; that is
a deliberate choice of this task since app/core/config.py is out of this
lane's file scope, not an oversight).
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Protocol

logger = logging.getLogger("app.notifications.sms")


class SmsSender(Protocol):
    def send(self, to: str, message: str) -> str:
        """Send `message` to `to` and return a provider reference string."""
        ...


class MockSmsSender:
    """Interim adapter: logs the message and returns a fake provider
    reference. Never used to actually reach a phone - this is what every
    SMS-channel Notification is sent through until a real provider is
    plugged into get_sms_sender below."""

    def send(self, to: str, message: str) -> str:
        provider_ref = f"mock-sms-{uuid.uuid4().hex[:16]}"
        logger.info("SMS to=%s provider_ref=%s body=%s", to, provider_ref, message)
        return provider_ref


def get_sms_sender() -> SmsSender:
    """Returns the configured SMS sender. ``SMS_PROVIDER`` (read directly
    from the environment rather than app/core/config.py's Settings class -
    see this module's docstring) selects a real adapter once one exists;
    today only "mock" (the default) is implemented, so any other value
    fails loudly rather than silently sending nothing."""
    provider = os.environ.get("SMS_PROVIDER", "mock").strip().lower()
    if provider in ("", "mock"):
        return MockSmsSender()
    raise RuntimeError(
        f"SMS_PROVIDER={provider!r} has no real adapter implemented yet - only 'mock' exists. "
        "Add a real SmsSender implementation and wire it in here before setting SMS_PROVIDER "
        "to anything else."
    )
