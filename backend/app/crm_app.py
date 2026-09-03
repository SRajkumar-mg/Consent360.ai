"""CRM Portal Backend - standalone service for the CRM directory (port 8001).

Runs independently from the Consent Management Platform backend (port 8000).
It owns the CRM's customer directory only; all consent-related operations are
delegated to the consent platform's APIs (cookie preferences, contexts, purge).
"""

import logging

from fastapi import FastAPI

from app.api.routes.crm_directory import router as crm_directory_router
from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger("crm_app")


app = FastAPI(
    title="CRM Portal Backend",
    version=settings.APP_VERSION,
    description=(
        "Standalone CRM directory service. Customer directory only - consent "
        "management is delegated to the Consent Management Platform APIs."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.include_router(crm_directory_router)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "app": "CRM Portal Backend", "version": settings.APP_VERSION}