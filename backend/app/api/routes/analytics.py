"""R2-07: banner-event ingest and the compliance KPI dashboard API.

Two routers, because the two halves have opposite threat models.

``public_router`` carries the write path. A cookie banner is painted before
anyone has logged in, so the beacon that records "a notice was shown" cannot
present a credential - the same constraint `app/api/routes/public.py` already
lives under for the notice, purposes and privacy-contact endpoints it serves
to those banners, and this router deliberately mirrors that file: same
``/public/{tenant_code}`` shape, same per-IP rate limit, same
``_get_tenant_or_404``. Three consequences are load-bearing:

* **It never creates a tenant.** `services/tenancy.py::resolve_tenant_id`
  provisions an Organization on first sight of an unknown ``source_app``,
  which is right for an authenticated integration and wrong here - it would
  let anyone with curl fill the ``organizations`` table by posting invented
  tenant codes. An unknown code is a flat 404.
* **It stores no personal data and reads none from the request.** No IP, no
  user agent, no identifier. See `app/models/analytics.py`.
* **The path it stores is normalised first.** `_normalize_path` collapses
  every identifier-looking segment, and the query string - the place a site
  would put a tracking id or an email - is dropped before that.

``router`` carries the read path: the KPI catalogue, one KPI's drill-down,
and its trend. All staff-authenticated behind ``dashboard.view``, and all
tenant-scoped by ``get_org_scope`` exactly like `routes/dashboard.py`.

The evidence pack is NOT re-exported here. `GET /retention/evidence-pack`
(R1-10) already produces it with a manifest hash a regulator must be able to
re-derive; a second export would produce a second hash over a second
serialisation of the same period, and the two would disagree. The dashboard's
download button calls that endpoint. This module only tells the UI where it
is and whether the caller may use it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission, role_has_permission
from app.core.access_log import _normalize_path
from app.core.database import get_db
from app.core.rbac import PERM_AUDIT_EXPORT, PERM_DASHBOARD
from app.core.utils import build_rate_limiter
from app.models.entities import Organization, User
from app.schemas.analytics import (
    BannerEventAck,
    BannerEventIn,
    KpiCatalogueOut,
    KpiOut,
    KpiTrendOut,
)
from app.services import banner_events, kpi_catalogue

# A banner beacon fires on a page view, not on a deliberate action, so the
# 60/min the /public read endpoints share would throttle a single busy tab.
# Still bounded: this is an open write path.
banner_limiter = build_rate_limiter("banner", limit=240, window_seconds=60)

public_router = APIRouter(prefix="/public", tags=["analytics"])
router = APIRouter(prefix="/analytics", tags=["analytics"])

MAX_PERIOD_DAYS = 400


# ---------------------------------------------------------------------------
#  Ingest (unauthenticated)
# ---------------------------------------------------------------------------
def _rate_limit(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    if not banner_limiter.allow(f"banner:{client_ip}"):
        raise HTTPException(status_code=429, detail="Too many requests")


def _tenant_or_404(tenant_code: str, db: Session) -> Organization:
    """Resolve an EXISTING tenant. Never provisions one - see the module
    docstring."""
    organization = (
        db.query(Organization).filter(Organization.code == (tenant_code or "").upper()).first()
    )
    if not organization or not organization.is_active:
        raise HTTPException(status_code=404, detail="Unknown tenant")
    return organization


@public_router.post(
    "/{tenant_code}/banner-events", response_model=BannerEventAck, status_code=202
)
def record_banner_event(
    tenant_code: str,
    payload: BannerEventIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """Record that a consent notice was shown, or that a decision was taken on
    one.

    202, not 201: the caller is a fire-and-forget beacon that must never block
    a banner from rendering, and there is no resource for it to go and read.
    """
    _rate_limit(request)
    organization = _tenant_or_404(tenant_code, db)

    page_ref = None
    if payload.page_path:
        # Drop the query string before normalising: `_normalize_path` redacts
        # identifier-looking *segments*, and a `?email=` never reaches a
        # segment boundary.
        path_only = payload.page_path.split("?", 1)[0].split("#", 1)[0]
        page_ref = _normalize_path(path_only)[:128]

    banner_events.record_event(
        db,
        tenant_id=organization.id,
        source_app=organization.code,
        event_type=payload.event_type,
        decision=payload.decision,
        surface=payload.surface,
        banner_version=payload.banner_version,
        language=payload.language,
        page_ref=page_ref,
        session_id=payload.session_id,
        purposes_offered=payload.purposes_offered,
        purposes_granted=payload.purposes_granted,
    )
    return BannerEventAck(recorded=True, event_type=payload.event_type)


# ---------------------------------------------------------------------------
#  KPI catalogue (staff)
# ---------------------------------------------------------------------------
def _period(period_days: int) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc)
    return end - timedelta(days=period_days), end


def _resolve_scope(
    db: Session, user: User, requested: Optional[str]
) -> tuple[Optional[str], Optional[int], bool]:
    """The viewer's own scope always wins.

    An org-scoped role (`jobhub_admin` and friends) cannot widen its view by
    asking for another tenant, and cannot widen it to "all" by asking for
    nothing - `get_org_scope` is the floor, and `scope_locked` tells the UI to
    stop offering a tenant picker it would only ever refuse.
    """
    locked_scope = get_org_scope(user)
    if locked_scope:
        scope = locked_scope
        locked = True
    else:
        scope = (requested or "").strip().upper() or None
        locked = False

    tenant_id = None
    if scope:
        organization = db.query(Organization).filter(Organization.code == scope).first()
        if not organization:
            raise HTTPException(status_code=404, detail="Unknown tenant code")
        tenant_id = organization.id
    return scope, tenant_id, locked


@router.get("/kpis", response_model=KpiCatalogueOut)
def kpi_catalogue_endpoint(
    period_days: int = Query(default=30, ge=1, le=MAX_PERIOD_DAYS),
    source_app: Optional[str] = Query(default=None),
    purpose_code: Optional[str] = Query(default=None, max_length=64),
    language: Optional[str] = Query(default=None, max_length=8),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_DASHBOARD)),
):
    """The whole KPI catalogue - every row of the register's K-series - with
    each one's current status.

    Read `counts` first. A KPI is only a number when its status is LIVE or
    PARTIAL; NO_DATA means the sample was empty and UNAVAILABLE means this
    build cannot answer at all. Neither is a finding of zero.
    """
    scope, tenant_id, locked = _resolve_scope(db, user, source_app)
    start, end = _period(period_days)
    return kpi_catalogue.build_catalogue(
        db, scope=scope, tenant_id=tenant_id, period_start=start, period_end=end,
        scope_locked=locked, purpose_code=purpose_code, language=language,
    )


@router.get("/kpis/{kpi_id}", response_model=KpiOut)
def kpi_detail(
    kpi_id: str,
    period_days: int = Query(default=30, ge=1, le=MAX_PERIOD_DAYS),
    source_app: Optional[str] = Query(default=None),
    purpose_code: Optional[str] = Query(default=None, max_length=64),
    language: Optional[str] = Query(default=None, max_length=8),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_DASHBOARD)),
):
    """One KPI with its full drill-down breakdown."""
    spec = kpi_catalogue.BY_ID.get(kpi_id.upper())
    if not spec:
        raise HTTPException(status_code=404, detail=f"Unknown KPI id '{kpi_id}'")
    scope, tenant_id, _ = _resolve_scope(db, user, source_app)
    start, end = _period(period_days)
    ctx = kpi_catalogue.KpiContext(
        db=db, scope=scope, tenant_id=tenant_id, period_start=start, period_end=end,
        purpose_code=purpose_code, language=language,
    )
    return kpi_catalogue.evaluate(spec, ctx)


@router.get("/kpis/{kpi_id}/trend", response_model=KpiTrendOut)
def kpi_trend(
    kpi_id: str,
    period_days: int = Query(default=30, ge=1, le=MAX_PERIOD_DAYS),
    group_by: str = Query(default="day", pattern="^(day|week|month)$"),
    source_app: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_DASHBOARD)),
):
    """A KPI's series over the period, where one exists.

    A KPI whose owning module reports point-in-time has no series, and says
    so rather than plotting a flat line of today's value repeated backwards.
    """
    spec = kpi_catalogue.BY_ID.get(kpi_id.upper())
    if not spec:
        raise HTTPException(status_code=404, detail=f"Unknown KPI id '{kpi_id}'")
    scope, tenant_id, _ = _resolve_scope(db, user, source_app)
    start, end = _period(period_days)
    return kpi_catalogue.build_trend(
        db, kpi_id=spec.id, scope=scope, tenant_id=tenant_id,
        period_start=start, period_end=end, group_by=group_by,
    )


@router.get("/evidence-pack-availability")
def evidence_pack_availability(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_DASHBOARD)),
):
    """Where the evidence pack lives and whether this caller may download it.

    The dashboard's download button targets `GET /retention/evidence-pack`
    (R1-10) directly. This endpoint exists so the button can be disabled with
    an accurate explanation rather than firing a request that will 403, and
    so the UI never has to hard-code which permission gates a route it does
    not own.
    """
    scope, _tenant_id, locked = _resolve_scope(db, user, None)
    allowed = bool(user.role) and role_has_permission(user.role, PERM_AUDIT_EXPORT)
    return {
        "endpoint": "/retention/evidence-pack",
        "method": "GET",
        "required_permission": PERM_AUDIT_EXPORT,
        "allowed": allowed,
        "scope": scope,
        "scope_locked": locked,
        "reason": None if allowed else (
            f"Downloading the regulator evidence pack requires the '{PERM_AUDIT_EXPORT}' "
            "permission, which this role does not hold."
        ),
        "note": (
            "The pack is produced by app/services/evidence_pack.py and carries its own "
            "SHA-256 manifest hash and HMAC signature. This dashboard links to that one "
            "export rather than serialising a second copy, so the hash a regulator "
            "re-derives is the hash the platform published."
        ),
    }
