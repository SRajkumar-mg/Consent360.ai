import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.database import Base, engine
from app.core.utils import CorrelationIdMiddleware, get_request_id
from app.models import entities  # noqa: F401  (ensure models registered)
from app.services.scheduler import start_scheduler, stop_scheduler

settings = get_settings()

logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    start_scheduler()

    # Seed English notification templates for R3-06 named triggers
    from app.core.database import get_db
    from app.models.entities import NotificationTemplate
    db = next(get_db())
    try:
        template_data = [
            # consent acknowledgement
            {"tenant_id": 1, "event_type": "consent_acknowledged", "channel": "EMAIL", "language": "en", "subject": "Your consent has been acknowledged", "body": "Dear {name}, your consent for purpose {purpose} has been acknowledged. Thank you for using Consent360."},
            # withdrawal confirmation
            {"tenant_id": 1, "event_type": "consent_withdrawn", "channel": "EMAIL", "language": "en", "subject": "Your consent has been withdrawn", "body": "Dear {name}, your consent for purpose {purpose} has been withdrawn. You can re-grant consent at any time."},
            # renewal reminder
            {"tenant_id": 1, "event_type": "consent_renewal_reminder", "channel": "EMAIL", "language": "en", "subject": "Consent renewal reminder", "body": "Dear {name}, your consent for purpose {purpose} is expiring soon. Please renew to continue."},
            # 48-hour erasure warning
            {"tenant_id": 1, "event_type": "erasure_warning", "channel": "EMAIL", "language": "en", "subject": "48-hour erasure warning", "body": "Dear {name}, your personal data will be erased in 48 hours unless you act."},
            # breach notice
            {"tenant_id": 1, "event_type": "breach_notification", "channel": "EMAIL", "language": "en", "subject": "Data breach notification", "body": "Dear {name}, we inform you of a data breach affecting your consents."},
            # request/grievance status change
            {"tenant_id": 1, "event_type": "grievance_status_change", "channel": "EMAIL", "language": "en", "subject": "Grievance status update", "body": "Dear {name}, your grievance status has been updated to {status}."},
            # legacy notice
            {"tenant_id": 1, "event_type": "legacy_notice", "channel": "EMAIL", "language": "en", "subject": "Legacy notice", "body": "Dear {name}, this is a legacy notice from Consent360."},
        ]
        for t in template_data:
            exists = db.query(NotificationTemplate).filter(
                NotificationTemplate.event_type == t["event_type"],
                NotificationTemplate.channel == t["channel"],
                NotificationTemplate.language == t["language"],
                NotificationTemplate.tenant_id == t["tenant_id"],
            ).first()
            if not exists:
                db.add(NotificationTemplate(**t))
        db.commit()
        logger.info("Seeded %d English notification templates", len(template_data))
    except Exception as exc:
        db.rollback()
        logger.warning("Notification template seed skipped: %s", exc)
    finally:
        db.close()

    yield
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
    allow_methods=settings.cors_allow_methods_list,
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(CorrelationIdMiddleware)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "request_id": get_request_id()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": get_request_id()},
    )


from app.api.routes import (
    admin, audit, auth, chatbot, consents, crm, crm_directory,
    customers, dashboard, data_categories, integration, notices,
    organizations, policies, portal, processing_activities,
    purposes, reports, rights, sharing_events, tenants,
)
from app.api.routes import (
    verification, notification_routes, processors, breaches,
    decisions, metrics, role_management, sdf_readiness,
)
from app.api.routes import legal_documents

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
    audit.router,
    dashboard.router,
    admin.router,
    organizations.router,
    portal.router,
    chatbot.router,
    tenants.router,
    notices.router,
    reports.router,
    reports.decisions_router,
    rights.router,
    sharing_events.router,
    verification.router,
    notification_routes.router,
    processors.router,
    breaches.router,
    decisions.router,
    metrics.router,
    role_management.router,
    sdf_readiness.router,
    legal_documents.router,
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
