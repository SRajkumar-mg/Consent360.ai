"""Prometheus metrics (R3-04: "Prometheus /metrics").

A dedicated CollectorRegistry (rather than prometheus_client's process-wide
default) is used so importing this module twice in the same process (e.g.
pytest re-importing across test files) never raises prometheus_client's
"Duplicated timeseries" error.
"""
from prometheus_client import CollectorRegistry, Counter, Histogram, make_asgi_app

REGISTRY = CollectorRegistry()

HTTP_REQUESTS_TOTAL = Counter(
    "consent360_http_requests_total",
    "Total HTTP requests handled",
    ["method", "path", "status"],
    registry=REGISTRY,
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "consent360_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    registry=REGISTRY,
)

PII_ACCESS_TOTAL = Counter(
    "consent360_pii_access_total",
    "Reads of endpoints that return personal data",
    ["path"],
    registry=REGISTRY,
)

LOGIN_FAILURES_TOTAL = Counter(
    "consent360_login_failures_total",
    "Failed staff login attempts",
    registry=REGISTRY,
)

ALERTS_TOTAL = Counter(
    "consent360_security_alerts_total",
    "Security alerts fired (bulk export, off-hours admin activity, repeated failed logins)",
    ["kind"],
    registry=REGISTRY,
)

MFA_CHALLENGES_TOTAL = Counter(
    "consent360_mfa_challenges_total",
    "MFA (TOTP) verification attempts",
    ["result"],
    registry=REGISTRY,
)

# Mounted at /metrics in app/main.py.
metrics_app = make_asgi_app(registry=REGISTRY)
