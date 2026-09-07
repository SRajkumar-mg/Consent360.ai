import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from urllib.parse import unquote

from app.core.access_log import AccessLogMiddleware
from app.core.config import get_settings
from app.core.database import Base, engine
from app.core.metrics import metrics_app
from app.core.access_log import _normalize_path
from app.core.utils import CorrelationIdMiddleware, SecurityHeadersMiddleware, get_request_id
from app.models import entities  # noqa: F401  (ensure models registered)

settings = get_settings()

logger = logging.getLogger("app")

class _RedactUvicornAccessPath(logging.Filter):
    """Redact identifier segments from uvicorn's own access log.

    The application's AccessLogMiddleware already redacts, but uvicorn logs
    every request independently through `uvicorn.access` and writes the raw
    path. The integration API places no format constraint on customer_id, so
    a tenant keying on email puts the address straight into the request line -
    verified: `GET /customers/leaky.person%40example.com` was found in a
    running server's log through this logger, after the application's own
    leak had been fixed.

    uvicorn formats the record as (client_addr, method, full_path,
    http_version, status_code); full_path is args[2] and may carry a query
    string, which is dropped rather than redacted because nothing in this
    application needs to log one.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            raw = args[2]
            path = raw.split("?", 1)[0]
            safe = _normalize_path(unquote(path))
            if safe != raw:
                record.args = args[:2] + (safe,) + args[3:]
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactUvicornAccessPath())



@asynccontextmanager
async def lifespan(app: FastAPI):
    # R3-03/H-09: refuse to start in production with an insecure default —
    # most importantly, an unset FIELD_ENCRYPTION_KEY, which would otherwise
    # silently store personal data (and one-time codes) in plaintext. See
    # Settings.production_issues(). Non-production environments are
    # unaffected (this always returns [] there), so local dev / tests / the
    # verification server keep working unchanged.
    issues = settings.production_issues()
    if issues:
        raise RuntimeError(
            "Refusing to start with ENVIRONMENT=production and insecure configuration:\n- "
            + "\n- ".join(issues)
        )
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    if settings.ALLOW_LEGACY_INTEGRATION_KEY:
        logger.warning(
            "ALLOW_LEGACY_INTEGRATION_KEY is enabled: the shared, unbound "
            "INTEGRATION_API_KEY can authenticate as ANY source_app and "
            "silently auto-provision a tenant for it. Issue tenant-bound "
            "keys instead (see seed_api_keys.py) and disable this."
        )
    # R3-02/R-01/N-03: provision (or re-sync) the DPO/Auditor/Operator roles
    # - and every other role in rbac.ROLE_PERMISSIONS - on every startup, so
    # they exist without requiring a re-run of seed.py. See
    # app/core/rbac.py::sync_roles.
    from app.core.database import SessionLocal
    from app.core.rbac import sync_roles

    _startup_db = SessionLocal()
    try:
        sync_roles(_startup_db)
    finally:
        _startup_db.close()
    if settings.SCHEDULER_ENABLED:
        from app.jobs.scheduler import start_scheduler

        start_scheduler()
    yield
    if settings.SCHEDULER_ENABLED:
        from app.jobs.scheduler import stop_scheduler

        stop_scheduler()
    logger.info("Shutting down")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Consent & Privacy Management Platform - central consent management, "
        "consent lifecycle, decision engine, evidence and audit for the organization."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    # R3-03/H-09: "CORS allows all methods with credentials" — this API never
    # needs HEAD/TRACE/CONNECT from a browser, so enumerate exactly what it
    # does serve rather than reflecting anything a caller asks for.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Context-Token", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
# R3-04: structured access logs + Prometheus request metrics + bulk-export /
# off-hours-admin alert triggers for every request, regardless of which
# router file defines the route - see app/core/access_log.py's docstring.
app.add_middleware(AccessLogMiddleware)

# R3-04: Prometheus /metrics endpoint. Mounted (not a plain route) so
# prometheus_client's own ASGI app handles content negotiation/exposition
# format; it is intentionally unauthenticated (a metrics scraper has no
# staff session) and exposes only aggregate counters/histograms, never PII.
app.mount("/metrics", metrics_app)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "request_id": get_request_id()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Redact identifier segments before logging: the raw path can carry a
    # customer's email or other direct identifier (the integration API places
    # no format constraint on customer_id), and this handler fires on the
    # error paths most likely to be shipped to an aggregator.
    logger.exception(
        "Unhandled error on %s %s", request.method, _normalize_path(request.url.path)
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": get_request_id()},
    )


from app.api.routes import admin, analytics, audit, auth, breaches, guardian, chatbot, consents, crm, crm_directory, customers, dashboard, consent_manager, data_categories, decision_validation, decisions, erasure, grievances, integration, notices, notifications, reconsent, objections, organizations, policies, portal, processing_activities, processors, public, purposes, receipts, retention, rights, sharing_events  # noqa: E402

for r in (
    auth.router,
    customers.router,
    integration.router,
    crm.router,
    crm_directory.router,
    consents.router,
    purposes.router,
    data_categories.router,
    processing_activities.router,
    policies.router,
    notices.router,
    receipts.router,
    sharing_events.router,
    objections.router,
    grievances.router,
    breaches.router,
    erasure.router,
    reconsent.router,
    rights.router,
    guardian.router,
    retention.router,
    processors.router,
    analytics.router,
    analytics.public_router,
    consent_manager.router,
    decision_validation.router,
    decisions.router,
    notifications.router,
    audit.router,
    dashboard.router,
    admin.router,
    organizations.router,
    portal.router,
    public.router,
    chatbot.router,
):
    app.include_router(r)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}


@app.get("/api/meta")
def meta():
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "request_id": get_request_id(),
    }
