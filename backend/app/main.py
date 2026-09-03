import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.database import Base, engine
from app.core.utils import CorrelationIdMiddleware, get_request_id
from app.models import entities  # noqa: F401  (ensure models registered)

settings = get_settings()

logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    yield
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
    allow_methods=["*"],
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


from app.api.routes import admin, audit, auth, chatbot, consents, crm, crm_directory, customers, dashboard, data_categories, integration, organizations, policies, portal, processing_activities, purposes  # noqa: E402

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
