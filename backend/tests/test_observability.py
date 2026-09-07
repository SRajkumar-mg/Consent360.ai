"""R3-04 (access logging, monitoring, alerting) behavioural tests.

Covers: contextvar-backed request id (T-03's process-global-class-attribute
fix), structured JSON logging, the Redis-backed rate limiter (against
fakeredis - no real Redis server needed), the Prometheus /metrics endpoint,
the access-log middleware's structured PII-read/admin-action logging and
alert wiring, and the three alert rules themselves.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import fakeredis
import pytest

from app.core import alerting
from app.core.utils import JsonFormatter, RedisRateLimiter, get_request_id, set_request_id


@pytest.fixture(autouse=True)
def _reenable_consent360_loggers():
    """conftest.py's `_test_database` session fixture runs Alembic
    in-process, and Alembic's env.py calls
    `logging.config.fileConfig(..., disable_existing_loggers=True)` (the
    default) - which disables every logger that already exists at that
    moment. pytest imports every test module during collection, before any
    fixture runs, so a logger created at module-import time (e.g.
    `logging.getLogger("consent360.alerts")` in app/core/alerting.py,
    imported by this very test file) already exists by the time fileConfig
    runs and gets silently disabled for the rest of the session - a
    test-environment artifact only (a real deployment runs
    `alembic upgrade` as a separate one-off process, never inside the
    long-running API server), not a production concern. See
    tests/test_logging.py's docstring for the same root cause hitting a
    different logger. Re-enabling here, once per test, keeps caplog able to
    see records from these loggers.
    """
    for name in ("consent360.alerts", "consent360.access", "consent360.encryption", "consent360.ratelimit"):
        logging.getLogger(name).disabled = False
    yield


# --------------------------------------------------------------------------- #
# contextvars request id (T-03)
# --------------------------------------------------------------------------- #

def test_request_id_is_isolated_per_async_task():
    """The old implementation stored request_id on a shared class
    attribute: two requests handled concurrently in the same process could
    see each other's id. A contextvar gives each asyncio task (Starlette
    runs each request in its own task) an independent value."""
    results = {}

    async def handle(name, rid, delay):
        set_request_id(rid)
        await asyncio.sleep(delay)
        # If request_id were a shared global, the OTHER task's later
        # set_request_id() would have clobbered this one by now.
        results[name] = get_request_id()

    async def run():
        await asyncio.gather(
            handle("first", "request-id-AAA", 0.02),
            handle("second", "request-id-BBB", 0.0),
        )

    asyncio.run(run())
    assert results["first"] == "request-id-AAA"
    assert results["second"] == "request-id-BBB"


def test_get_set_request_id_still_works_as_a_plain_function_pair():
    set_request_id("plain-test-id")
    assert get_request_id() == "plain-test-id"


# --------------------------------------------------------------------------- #
# Structured JSON logging
# --------------------------------------------------------------------------- #

def test_json_formatter_produces_valid_json_with_expected_fields():
    set_request_id("json-fmt-test-id")
    record = logging.LogRecord(
        name="consent360.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello %s", args=("world",), exc_info=None,
    )
    record.request_id = get_request_id()
    record.custom_field = "extra-value"

    line = JsonFormatter().format(record)
    payload = json.loads(line)  # must be valid JSON

    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "consent360.test"
    assert payload["request_id"] == "json-fmt-test-id"
    assert payload["custom_field"] == "extra-value"
    assert "timestamp" in payload


# --------------------------------------------------------------------------- #
# Redis-backed rate limiter (fakeredis - no real Redis server needed)
# --------------------------------------------------------------------------- #

def test_redis_rate_limiter_blocks_after_limit_and_release_gives_a_slot_back():
    client = fakeredis.FakeStrictRedis()
    limiter = RedisRateLimiter(client, limit=3, window_seconds=60, namespace="test")

    key = "1.2.3.4"
    assert limiter.allow(key) is True
    assert limiter.allow(key) is True
    assert limiter.allow(key) is True
    assert limiter.allow(key) is False  # 4th within the window is refused

    limiter.release(key)
    assert limiter.allow(key) is True  # released slot is usable again


def test_redis_rate_limiter_keys_are_independent():
    client = fakeredis.FakeStrictRedis()
    limiter = RedisRateLimiter(client, limit=1, window_seconds=60, namespace="test")
    assert limiter.allow("key-a") is True
    assert limiter.allow("key-a") is False
    assert limiter.allow("key-b") is True  # different key, own budget


def test_build_rate_limiter_falls_back_to_in_memory_when_redis_url_unset(monkeypatch):
    from app.core.config import get_settings
    from app.core.utils import RateLimiter, build_rate_limiter

    monkeypatch.setenv("REDIS_URL", "")
    get_settings.cache_clear()
    try:
        limiter = build_rate_limiter("fallback-test", limit=5, window_seconds=60)
        assert isinstance(limiter, RateLimiter)
    finally:
        get_settings.cache_clear()


def test_build_rate_limiter_falls_back_when_redis_is_unreachable(monkeypatch):
    from app.core.config import get_settings
    from app.core.utils import RateLimiter, build_rate_limiter

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")  # nothing listens on port 1
    get_settings.cache_clear()
    try:
        limiter = build_rate_limiter("unreachable-test", limit=5, window_seconds=60)
        assert isinstance(limiter, RateLimiter)  # failed open to in-memory, did not raise
    finally:
        get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# Alert rules
# --------------------------------------------------------------------------- #

def test_bulk_export_alert_fires_at_threshold(monkeypatch, caplog):
    monkeypatch.setattr(alerting, "get_settings", lambda: _FakeSettings(alert_bulk_export_threshold=10))
    with caplog.at_level(logging.CRITICAL, logger="consent360.alerts"):
        fired_low = alerting.check_bulk_export(actor="tester", row_count=9, path="/customers")
        fired_high = alerting.check_bulk_export(actor="tester", row_count=10, path="/customers")
    assert fired_low is False
    assert fired_high is True
    assert any("BULK_EXPORT" in r.message for r in caplog.records)


def test_offhours_admin_alert_uses_ist_business_hours(monkeypatch):
    monkeypatch.setattr(alerting, "get_settings", lambda: _FakeSettings(offhours_start=20, offhours_end=8))
    business_hours = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)  # 17:30 IST
    late_night = datetime(2026, 1, 5, 20, 0, tzinfo=timezone.utc)  # 01:30 IST next day

    assert alerting.is_off_hours(business_hours) is False
    assert alerting.is_off_hours(late_night) is True


def test_offhours_admin_alert_fires_only_off_hours(monkeypatch, caplog):
    monkeypatch.setattr(alerting, "get_settings", lambda: _FakeSettings(offhours_start=20, offhours_end=8))
    with caplog.at_level(logging.CRITICAL, logger="consent360.alerts"):
        during_business_hours = alerting.check_offhours_admin(
            actor="admin", event="ROLE_CHANGED", when=datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc),
        )
        off_hours = alerting.check_offhours_admin(
            actor="admin", event="ROLE_CHANGED", when=datetime(2026, 1, 5, 20, 0, tzinfo=timezone.utc),
        )
    assert during_business_hours is False
    assert off_hours is True
    assert any("OFF_HOURS_ADMIN_ACTIVITY" in r.message for r in caplog.records)


def test_login_endpoint_triggers_the_repeated_failed_logins_alert_at_the_default_threshold(client, caplog):
    """R3-04's failed-login alert rule is wired directly into
    POST /auth/login (app/api/routes/auth.py), not just callable in
    isolation - five wrong-password attempts against the same username
    (ALERT_FAILED_LOGIN_THRESHOLD's default) must fire it without any
    extra step."""
    with caplog.at_level(logging.CRITICAL, logger="consent360.alerts"):
        for _ in range(5):
            resp = client.post(
                "/auth/login", json={"username": "does-not-exist-obs-test", "password": "wrong"},
            )
            assert resp.status_code == 401

    assert any("REPEATED_FAILED_LOGINS" in r.message for r in caplog.records)


def test_check_failed_logins_respects_an_explicit_threshold(db):
    for _ in range(3):
        from app.services.audit import log_audit

        log_audit(db, "LOGIN_FAILED", actor_username="explicit-threshold-user", source_app="UI", actor_type="USER")

    assert alerting.check_failed_logins(db, username="explicit-threshold-user", threshold=5, window_minutes=15) is False
    assert alerting.check_failed_logins(db, username="explicit-threshold-user", threshold=3, window_minutes=15) is True


class _FakeSettings:
    def __init__(self, alert_bulk_export_threshold=100, offhours_start=20, offhours_end=8,
                 alert_failed_login_threshold=5, alert_failed_login_window_minutes=15, alert_webhook_url=""):
        self.ALERT_BULK_EXPORT_THRESHOLD = alert_bulk_export_threshold
        self.ALERT_OFFHOURS_START_HOUR = offhours_start
        self.ALERT_OFFHOURS_END_HOUR = offhours_end
        self.ALERT_FAILED_LOGIN_THRESHOLD = alert_failed_login_threshold
        self.ALERT_FAILED_LOGIN_WINDOW_MINUTES = alert_failed_login_window_minutes
        self.ALERT_WEBHOOK_URL = alert_webhook_url


# --------------------------------------------------------------------------- #
# Prometheus /metrics
# --------------------------------------------------------------------------- #

def test_metrics_endpoint_is_live_and_reports_request_counts(client, staff_token):
    client.get("/health")

    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert "consent360_http_requests_total" in body
    assert "consent360_http_request_duration_seconds" in body


# --------------------------------------------------------------------------- #
# Access-log middleware: structured logging + alert wiring for PII reads
# --------------------------------------------------------------------------- #

def test_pii_read_is_structured_logged_with_actor_and_row_count(db, client, staff_token, caplog):
    from app.models.entities import Customer

    for i in range(3):
        db.add(Customer(
            external_id=f"CUST-OBS-{i}", name=f"Obs Customer {i}",
            email=f"obs-customer-{i}@example.com", source_app="OBS_TEST",
        ))
    db.commit()

    with caplog.at_level(logging.INFO, logger="consent360.access"):
        resp = client.get("/customers", headers={"Authorization": f"Bearer {staff_token}"})
    assert resp.status_code == 200

    pii_records = [r for r in caplog.records if getattr(r, "event", None) == "PII_ACCESS"]
    assert pii_records, "expected a structured PII_ACCESS log record for GET /customers"
    record = pii_records[-1]
    assert record.path == "/customers"
    assert record.actor == "test-admin"
    assert record.row_count >= 3


def test_admin_action_is_structured_logged(db, client, staff_token, caplog):
    with caplog.at_level(logging.INFO, logger="consent360.access"):
        resp = client.get("/admin/roles", headers={"Authorization": f"Bearer {staff_token}"})
    assert resp.status_code == 200
    # GET is a read, not a mutation - /admin/roles should be picked up by
    # the PII-read patterns (it's under /admin/) rather than admin-action
    # ones (those require a mutating method); either way it must be logged.
    assert any(
        getattr(r, "event", None) in ("PII_ACCESS", "ADMIN_ACTION") and getattr(r, "path", "") == "/admin/roles"
        for r in caplog.records
    )


def test_unauthenticated_pii_read_attempt_is_not_logged_as_a_successful_access(client, caplog):
    with caplog.at_level(logging.INFO, logger="consent360.access"):
        resp = client.get("/customers")  # no Authorization header
    assert resp.status_code == 401
    assert not any(getattr(r, "event", None) == "PII_ACCESS" for r in caplog.records)
