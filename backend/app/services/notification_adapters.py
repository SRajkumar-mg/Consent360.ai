"""R3-06: Channel adapters for notification delivery."""
import logging
import smtplib
from email.mime.text import MIMEText
from typing import Optional

from app.core.config import get_settings

logger = logging.getLogger("notification.adapters")
settings = get_settings()


class EmailAdapter:
    """SMTP-based email adapter."""

    def send(self, recipient: str, subject: str, body: str) -> Optional[str]:
        if not settings.NOTIFICATION_EMAIL_HOST:
            logger.warning("Email adapter: no SMTP host configured, skipping delivery")
            return None
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"] = settings.NOTIFICATION_EMAIL_USER or "noreply@consent360.io"
            msg["To"] = recipient

            with smtplib.SMTP(settings.NOTIFICATION_EMAIL_HOST, settings.NOTIFICATION_EMAIL_PORT) as server:
                if settings.NOTIFICATION_EMAIL_PORT == 587:
                    server.starttls()
                if settings.NOTIFICATION_EMAIL_USER and settings.NOTIFICATION_EMAIL_PASSWORD:
                    server.login(settings.NOTIFICATION_EMAIL_USER, settings.NOTIFICATION_EMAIL_PASSWORD)
                server.send_message(msg)
            return f"smtp-{recipient}"
        except Exception as e:
            logger.error("Email delivery failed to %s: %s", recipient, e)
            raise


class SMSAdapter:
    """HTTP API-based SMS adapter (generic)."""

    def send(self, recipient: str, subject: str, body: str) -> Optional[str]:
        if not settings.NOTIFICATION_SMS_API_URL:
            logger.warning("SMS adapter: no API URL configured, skipping delivery")
            return None
        try:
            import httpx
            response = httpx.post(
                settings.NOTIFICATION_SMS_API_URL,
                json={"to": recipient, "message": body},
                headers={"Authorization": f"Bearer {settings.NOTIFICATION_SMS_API_KEY}"},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("message_id", f"sms-{recipient}")
        except Exception as e:
            logger.error("SMS delivery failed to %s: %s", recipient, e)
            raise
