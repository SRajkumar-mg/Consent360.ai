"""R3-04 alert rules: bulk export, off-hours admin activity, repeated
failed logins (H-05: "alert rules (bulk export, off-hours admin, failed
logins)").

An alert is a structured CRITICAL log line (`"alert": true` in the JSON
payload - see app/core/utils.py::JsonFormatter - so any log-based
alerting/SIEM rule can match on it) plus, best-effort, an outbound webhook
POST when ALERT_WEBHOOK_URL is configured, and a Prometheus counter bump.
Alerts are deliberately NOT written to `audit_logs`: that table is the
compliance ledger of what an actor did; an alert is a detection-and-
response signal ABOUT that activity for a different audience (a security
on-call rotation, not a DPO's evidence trail) with a different retention
story, and mixing the two would make audit_logs' own review noisier.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import get_settings

_log = logging.getLogger("consent360.alerts")

IST = timezone(timedelta(hours=5, minutes=30))


def _fire(kind: str, **details: Any) -> None:
    payload = {"alert": True, "kind": kind, "at": datetime.now(timezone.utc).isoformat(), **details}
    _log.critical("SECURITY_ALERT: %s", kind, extra=payload)

    from app.core.metrics import ALERTS_TOTAL

    ALERTS_TOTAL.labels(kind=kind).inc()

    settings = get_settings()
    if not settings.ALERT_WEBHOOK_URL:
        return
    try:
        req = urllib.request.Request(
            settings.ALERT_WEBHOOK_URL,
            data=json.dumps(payload, default=str).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)  # noqa: S310 - operator-configured, not user input
    except Exception:
        _log.warning("Failed to deliver alert webhook for %s", kind)


def check_bulk_export(*, actor: str, row_count: int, path: str, source_app: str = "") -> bool:
    """Fires ALERT kind BULK_EXPORT when a single response returns at least
    ALERT_BULK_EXPORT_THRESHOLD rows of data. Returns whether it fired."""
    settings = get_settings()
    if row_count < settings.ALERT_BULK_EXPORT_THRESHOLD:
        return False
    _fire("BULK_EXPORT", actor=actor, source_app=source_app, row_count=row_count, path=path)
    return True


def is_off_hours(when: datetime | None = None) -> bool:
    """IST business hours are [ALERT_OFFHOURS_END_HOUR, ALERT_OFFHOURS_START_HOUR)
    (default 08:00-20:00 IST); anything outside that window counts as
    off-hours."""
    settings = get_settings()
    when = when or datetime.now(timezone.utc)
    hour = when.astimezone(IST).hour
    return not (settings.ALERT_OFFHOURS_END_HOUR <= hour < settings.ALERT_OFFHOURS_START_HOUR)


def check_offhours_admin(*, actor: str, event: str, when: datetime | None = None) -> bool:
    """Fires ALERT kind OFF_HOURS_ADMIN_ACTIVITY when a privileged/admin
    action happens outside IST business hours. Returns whether it fired."""
    if not is_off_hours(when):
        return False
    _fire("OFF_HOURS_ADMIN_ACTIVITY", actor=actor, event=event)
    return True


def check_failed_logins(db, *, username: str, window_minutes: int | None = None, threshold: int | None = None) -> bool:
    """Fires ALERT kind REPEATED_FAILED_LOGINS once `username` has at least
    `threshold` LOGIN_FAILED audit rows in the trailing `window_minutes`.
    Shares its counting logic with R3-02's account-lockout check - see
    app.core.security.recent_failed_login_count. Returns whether it fired.
    """
    from app.core.security import recent_failed_login_count

    settings = get_settings()
    window_minutes = window_minutes if window_minutes is not None else settings.ALERT_FAILED_LOGIN_WINDOW_MINUTES
    threshold = threshold if threshold is not None else settings.ALERT_FAILED_LOGIN_THRESHOLD
    count = recent_failed_login_count(db, username, window_minutes=window_minutes)
    if count < threshold:
        return False
    _fire("REPEATED_FAILED_LOGINS", username=username, count=count, window_minutes=window_minutes)
    return True
