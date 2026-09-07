"""R3-04: structured access logging, request metrics, and the bulk-export /
off-hours-admin alert triggers, all applied at one ASGI middleware rather
than by instrumenting each route handler individually.

Why a middleware instead of per-route calls: the routes that most need
this (customer lists, audit-log queries, dashboard exports) live in files
outside this lane's scope (routes/customers.py, routes/audit.py,
routes/dashboard.py, ...) and are being edited concurrently by other lanes.
A path-pattern middleware covers every current AND future route under
those prefixes with no per-route wiring, and cannot be silently skipped by
a handler that forgets to call it.

"Access log" here means the structured JSON log stream (see
app/core/utils.py::JsonFormatter) that a log shipper/SIEM ingests, not a
new `audit_logs` row: `audit_logs` already gets precise, richly-detailed
entries from the specific consent/customer/admin actions that write it
(CONSENT_VIEWED, USER_UPDATED, ROLE_CHANGED, ...); duplicating every GET
into that append-only compliance ledger as well would bloat it and blur
the line between "what an actor did" (audit) and "what was accessed, for
detection/monitoring" (access log) - see H-05's own phrasing, "structured
access logs for every PII read".
"""
from __future__ import annotations

import json
import logging
import re
import time

_access_log = logging.getLogger("consent360.access")

# Endpoints that return personal data - or a privileged listing (staff
# users, roles) - in a list/detail/export shape. Reads (GET) of any of
# these are structured-logged and checked against the bulk-export alert;
# see _ADMIN_ACTION_PATTERNS below for the separate off-hours check on
# MUTATIONS to the admin/user-management ones.
_PII_READ_PATTERNS = [
    re.compile(r"^/customers(/|$)"),
    re.compile(r"^/consents(/|$)"),
    re.compile(r"^/audit(/|$)"),
    re.compile(r"^/dashboard(/|$)"),
    re.compile(r"^/organizations(/[^/]+/(customers|dashboard)|/portal/dashboard)"),
    re.compile(r"^/admin(/|$)"),
    re.compile(r"^/auth/users(/|$)"),
]

# Mutating admin/privileged-action endpoints (role & user management).
_ADMIN_ACTION_PATTERNS = [
    re.compile(r"^/admin(/|$)"),
    re.compile(r"^/auth/users(/|$)"),
]
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Collapses a path with an id/email segment (`/customers/CUST-123`,
# `/crm/customers/by-email/x@y.com`) to a stable metric/log label
# (`/customers/{id}`) so Prometheus doesn't get one time series per
# distinct identifier ever seen — and, just as importantly, so no
# caller-supplied identifier reaches a log line or a metric label verbatim.
#
# A segment is treated as an identifier when it contains a digit, contains
# an "@", or is implausibly long for a route literal. That deliberately
# covers `leaky.person@example.com` (the integration API places no format
# constraint on `customer_id`, so an external_id IS routinely an email
# address), `CUST-123`, a numeric primary key, and a JWT in
# `/consent/context/consume/{token}`. Route literals in this codebase
# (`customers`, `by-email`, `roles`, `access-review`, `verify-login`, ...)
# contain no digits and stay readable.
_ID_SEGMENT = re.compile(r"^(?:[^/]*[\d@][^/]*|[^/]{41,})$")


def _normalize_path(path: str) -> str:
    """Redact every identifier-looking segment, not just a trailing one.

    Redacting only the LAST segment was the shape of the R3-04 leak: it was
    applied to the Prometheus labels but not to the access-log line, and it
    would in any case have missed a mid-path identifier
    (`/organizations/{org_id}/customers`).
    """
    segments = path.split("/")
    collapsed = "/".join("{id}" if seg and _ID_SEGMENT.match(seg) else seg for seg in segments)
    return collapsed if len(collapsed) <= 100 else collapsed[:100]


def _extract_actor(headers: dict[bytes, bytes]) -> str:
    """Best-effort actor label for the access log.

    Applies the SAME claim checks as app/api/deps.py::get_current_user
    (`ctx`/`type`/`sub_type`/`aud`, on top of the signature and `iss` that
    decode_token already verifies) rather than trusting `sub_type` alone.
    Nothing is authorised here - the worst case is a mislabelled log line -
    but a log line that attributes an access to a named staff member on
    weaker evidence than the request itself was authorised with is a log
    line an incident responder would be wrong to trust.
    """
    auth = headers.get(b"authorization", b"").decode("latin-1", errors="ignore")
    if auth.startswith("Bearer "):
        try:
            from app.core.config import get_settings
            from app.core.security import AUTH_CONTEXT, STAFF_SUBJECT_TYPE, decode_token

            payload = decode_token(auth[7:])
            if (
                payload
                and payload.get("ctx") == AUTH_CONTEXT
                and payload.get("type") == "access"
                and payload.get("sub_type") == STAFF_SUBJECT_TYPE
                and payload.get("aud") == get_settings().JWT_AUDIENCE
            ):
                return payload.get("username") or "unknown-staff"
        except Exception:
            pass
        return "unknown-bearer"
    if headers.get(b"x-api-key"):
        return "api-key-caller"
    return "anonymous"


def _estimate_row_count(body: bytes) -> int:
    if not body:
        return 0
    try:
        data = json.loads(body)
    except Exception:
        return 0
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("items", "results", "customers", "rows", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return len(value)
        total = data.get("total")
        if isinstance(total, int):
            return total
    return 1


class AccessLogMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET")
        headers = dict(scope.get("headers") or [])
        is_pii_read = method == "GET" and any(p.search(path) for p in _PII_READ_PATTERNS)
        is_admin_action = method in _MUTATING_METHODS and any(p.search(path) for p in _ADMIN_ACTION_PATTERNS)
        capture_body = is_pii_read

        start = time.monotonic()
        state = {"status": 0, "body": bytearray()}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
            elif message["type"] == "http.response.body" and capture_body:
                state["body"].extend(message.get("body") or b"")
            await send(message)

        await self.app(scope, receive, send_wrapper)

        duration = time.monotonic() - start
        status = state["status"]
        norm_path = _normalize_path(path)

        try:
            from app.core.metrics import HTTP_REQUEST_DURATION_SECONDS, HTTP_REQUESTS_TOTAL

            HTTP_REQUESTS_TOTAL.labels(method=method, path=norm_path, status=str(status)).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(method=method, path=norm_path).observe(duration)
        except Exception:
            _access_log.exception("Failed to record request metrics for %s %s", method, norm_path)

        if status >= 400 or not (is_pii_read or is_admin_action):
            return

        actor = _extract_actor(headers)

        if is_pii_read:
            row_count = _estimate_row_count(bytes(state["body"]))
            _access_log.info(
                "PII_ACCESS",
                extra={
                    # norm_path, NEVER the raw ASGI path: the integration API
                    # places no format constraint on `customer_id`, so a customer
                    # whose external_id is an email address produced the log line
                    # `"path": "/customers/leaky.person@example.com"` - personal
                    # data written verbatim into the log stream a SIEM ingests and
                    # retains for a year (LOG_RETENTION_DAYS). The redaction this
                    # file already applied to the Prometheus labels a few lines
                    # above simply was not applied here.
                    "event": "PII_ACCESS", "method": method, "path": norm_path, "status": status,
                    "actor": actor, "duration_ms": round(duration * 1000, 2), "row_count": row_count,
                },
            )
            try:
                from app.core.metrics import PII_ACCESS_TOTAL

                PII_ACCESS_TOTAL.labels(path=norm_path).inc()
            except Exception:
                pass
            try:
                from app.core.alerting import check_bulk_export

                # Redacted here too: check_bulk_export puts `path` straight into
                # the alert payload, which is CRITICAL-logged and POSTed to
                # ALERT_WEBHOOK_URL (app/core/alerting.py::_fire) - a second,
                # outbound copy of the same identifier.
                check_bulk_export(actor=actor, row_count=row_count, path=norm_path)
            except Exception:
                _access_log.exception("Bulk-export alert check failed for %s %s", method, norm_path)

        if is_admin_action:
            _access_log.info(
                "ADMIN_ACTION",
                extra={
                    "event": "ADMIN_ACTION", "method": method, "path": norm_path, "status": status,
                    "actor": actor, "duration_ms": round(duration * 1000, 2),
                },
            )
            try:
                from app.core.alerting import check_offhours_admin

                check_offhours_admin(actor=actor, event=f"{method} {norm_path}")
            except Exception:
                _access_log.exception("Off-hours alert check failed for %s %s", method, norm_path)
