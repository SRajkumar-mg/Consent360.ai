"""Pluggable email sender. SmtpEmailSender drives real SMTP settings;
ConsoleEmailSender (used when SMTP_HOST is unset, e.g. in tests and local
dev) logs the message at INFO so the OTP is visible without a mail server.
get_email_sender() refuses to select ConsoleEmailSender when
ENVIRONMENT=production and SMTP_HOST is unset, so a misconfigured
production deploy fails loudly instead of quietly logging live codes. This
is the minimal sender R3-06 (S3) generalises into a full notification
service with SMS and in-app channels.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.mime.text import MIMEText
from typing import Protocol

logger = logging.getLogger("app.notifications.email")


class EmailSender(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


def _echo_body_enabled() -> bool:
    """Whether the console sender may write a message BODY to the log.

    Default: no. This is read from the environment rather than from
    `Settings` only because app/core/config.py is owned by another workstream
    right now; it belongs in Settings and should move there.

    The flag is refused outright when ENVIRONMENT=production, which is belt
    and braces - `get_email_sender` below already refuses to select this
    sender at all in production.
    """
    from app.core.config import get_settings

    if get_settings().ENVIRONMENT.strip().lower() == "production":
        return False
    return os.getenv("EMAIL_CONSOLE_ECHO_BODY", "").strip().lower() in ("1", "true", "yes", "on")


class ConsoleEmailSender:
    """Logs that a message was sent, not what was in it.

    An email body from this platform routinely contains a verification code,
    and `to` is a data principal's email address - both are exactly the class
    of value that must not reach a log file, an aggregator or a screen share
    (the same PII-in-logs class as app/core/access_log.py's redaction). So the
    default line carries a masked recipient and the subject, and nothing else.

    The body can still be echoed for a local demo that has no SMTP server and
    genuinely needs to read the code, but only as a deliberate opt-in:
    EMAIL_CONSOLE_ECHO_BODY=true, at DEBUG level, never in production. If that
    escape hatch did not exist there would be no way at all to complete an OTP
    flow on a laptop - `otp_challenges` stores only a hash of the code.
    """

    def send(self, to: str, subject: str, body: str) -> None:
        from app.core.utils import mask_identifier

        logger.info("EMAIL to=%s subject=%s", mask_identifier(to), subject)
        if _echo_body_enabled():
            logger.debug("EMAIL body (EMAIL_CONSOLE_ECHO_BODY is on) to=%s: %s",
                         mask_identifier(to), body)


class SmtpEmailSender:
    def __init__(self, host: str, port: int, username: str, password: str, sender: str):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender

    def send(self, to: str, subject: str, body: str) -> None:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = to
        with smtplib.SMTP(self.host, self.port, timeout=10) as server:
            server.starttls()
            if self.username:
                server.login(self.username, self.password)
            server.sendmail(self.sender, [to], msg.as_string())


def get_email_sender() -> EmailSender:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.SMTP_HOST:
        return SmtpEmailSender(settings.SMTP_HOST, settings.SMTP_PORT, settings.SMTP_USER, settings.SMTP_PASSWORD, settings.SMTP_FROM)
    if settings.ENVIRONMENT.strip().lower() == "production":
        # Never let a production deploy silently fall back to logging live
        # verification codes just because SMTP was left unconfigured - fail
        # loudly instead so this is caught at startup/first-use, not by
        # someone reading the logs and finding a customer's OTP in them.
        raise RuntimeError(
            "SMTP_HOST is not configured but ENVIRONMENT=production - refusing to fall back "
            "to ConsoleEmailSender, which logs verification codes in plain text."
        )
    return ConsoleEmailSender()
