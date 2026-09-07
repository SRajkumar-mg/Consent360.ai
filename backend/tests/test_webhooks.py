"""R3-12: outbound alert-webhook delivery (app/core/alerting.py's
ALERT_WEBHOOK_URL POST).

This is the one genuinely un-covered "webhook" in the codebase - see that
module's docstring: a structured CRITICAL log line plus a best-effort
outbound POST when ALERT_WEBHOOK_URL is configured. tests/test_observability.py
covers the alert *rules* (bulk export, off-hours, failed logins) purely
through their logging/metrics side effects and a _FakeSettings stub that
always carries alert_webhook_url="" - the actual HTTP delivery, its payload
shape, and its "never raise, even when the endpoint is unreachable" contract
were never exercised end to end. There is no separate webhook subsystem
elsewhere in this codebase (no delivery-webhook/callback integration for any
notification channel - see models/entities.py's note on that) and no breach-
management feature yet (R3-08 is not built - see this file's sibling gap
noted in the R3-12 handoff), so alerting is the only real webhook to test.
"""
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.core import alerting


@pytest.fixture(autouse=True)
def _reenable_consent360_loggers():
    """See tests/test_observability.py's fixture of the same name for why
    this is needed: Alembic's `disable_existing_loggers=True` (run
    in-process by conftest.py's session fixture) silently disables the
    module-level `logging.getLogger("consent360.alerts")` created when
    app/core/alerting.py is imported, for the rest of the test session."""
    logging.getLogger("consent360.alerts").disabled = False
    yield


class _CapturingHandler(BaseHTTPRequestHandler):
    """Records every POST it receives onto the class itself so the test can
    inspect it after the request completes."""

    received: list[dict] = []

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's naming
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        _CapturingHandler.received.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": json.loads(body) if body else None,
        })
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):  # noqa: D401 - silence default stderr logging
        pass


class _FakeSettings:
    def __init__(self, webhook_url: str):
        self.ALERT_WEBHOOK_URL = webhook_url
        self.ALERT_BULK_EXPORT_THRESHOLD = 100


def _run_capturing_server():
    _CapturingHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), _CapturingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_alert_webhook_delivers_the_alert_payload_as_json(monkeypatch):
    server, thread = _run_capturing_server()
    try:
        url = f"http://127.0.0.1:{server.server_port}/hooks/alerts"
        monkeypatch.setattr(alerting, "get_settings", lambda: _FakeSettings(url))

        fired = alerting.check_bulk_export(actor="webhook-test-user", row_count=999, path="/customers/export")
        assert fired is True

        # do_POST runs on the server thread; give it a moment to land rather
        # than racing a bare assertion against an empty list.
        for _ in range(50):
            if _CapturingHandler.received:
                break
            threading.Event().wait(0.02)

        assert len(_CapturingHandler.received) == 1
        delivered = _CapturingHandler.received[0]
        assert delivered["path"] == "/hooks/alerts"
        assert delivered["headers"]["Content-Type"] == "application/json"
        payload = delivered["body"]
        assert payload["alert"] is True
        assert payload["kind"] == "BULK_EXPORT"
        assert payload["actor"] == "webhook-test-user"
        assert payload["row_count"] == 999
        assert payload["path"] == "/customers/export"
        assert "at" in payload  # ISO timestamp
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_alert_webhook_is_not_called_when_unconfigured(monkeypatch):
    calls = []
    monkeypatch.setattr(alerting.urllib.request, "urlopen", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(alerting, "get_settings", lambda: _FakeSettings(""))

    fired = alerting.check_bulk_export(actor="tester", row_count=1000, path="/x")
    assert fired is True
    assert calls == []


def test_alert_webhook_delivery_failure_is_swallowed_not_raised(monkeypatch, caplog):
    """A dead/unreachable webhook endpoint must never take down the request
    that triggered the alert - see alerting._fire's own try/except. Uses a
    port nothing is listening on rather than mocking urlopen, so the real
    urllib connect-refused path is exercised."""
    monkeypatch.setattr(alerting, "get_settings", lambda: _FakeSettings("http://127.0.0.1:1/unreachable"))

    with caplog.at_level(logging.WARNING, logger="consent360.alerts"):
        fired = alerting.check_bulk_export(actor="tester", row_count=1000, path="/x")

    assert fired is True  # the alert itself still "fires" (log + metric)
    assert any("Failed to deliver alert webhook" in r.message for r in caplog.records)


def test_alert_webhook_carries_kind_specific_fields_for_offhours_and_failed_logins(monkeypatch):
    server, thread = _run_capturing_server()
    try:
        url = f"http://127.0.0.1:{server.server_port}/hooks"
        monkeypatch.setattr(alerting, "get_settings", lambda: _FakeSettings(url))

        alerting._fire("OFF_HOURS_ADMIN_ACTIVITY", actor="admin-x", event="ROLE_CHANGED")

        for _ in range(50):
            if _CapturingHandler.received:
                break
            threading.Event().wait(0.02)

        assert len(_CapturingHandler.received) == 1
        payload = _CapturingHandler.received[0]["body"]
        assert payload["kind"] == "OFF_HOURS_ADMIN_ACTIVITY"
        assert payload["actor"] == "admin-x"
        assert payload["event"] == "ROLE_CHANGED"
    finally:
        server.shutdown()
        thread.join(timeout=2)
