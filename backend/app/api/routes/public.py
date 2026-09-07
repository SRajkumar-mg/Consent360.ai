from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.utils import public_limiter
from app.models.entities import DataCategory, Notice, Organization, ProcessingActivity, Purpose, PurposeVersion
from app.schemas.schemas import PublicNoticeOut, PublicPrivacyContactOut, PublicPurposeOut, PublicRightsOut

router = APIRouter(prefix="/public", tags=["public"])


def _rate_limit(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    if not public_limiter.allow(f"public:{client_ip}"):
        raise HTTPException(status_code=429, detail="Too many requests")


def _get_tenant_or_404(tenant_code: str, db: Session) -> Organization:
    tenant = db.query(Organization).filter(Organization.code == tenant_code.upper()).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Unknown tenant")
    return tenant


@router.get("/{tenant_code}/privacy-contact", response_model=PublicPrivacyContactOut)
def privacy_contact(tenant_code: str, request: Request, db: Session = Depends(get_db)):
    _rate_limit(request)
    tenant = _get_tenant_or_404(tenant_code, db)
    return PublicPrivacyContactOut(
        tenant_code=tenant.code, tenant_name=tenant.name,
        dpo_name=tenant.dpo_name, dpo_email=tenant.dpo_email, dpo_phone=tenant.dpo_phone,
    )


@router.get("/{tenant_code}/rights", response_model=PublicRightsOut)
def rights(tenant_code: str, request: Request, db: Session = Depends(get_db)):
    _rate_limit(request)
    tenant = _get_tenant_or_404(tenant_code, db)
    return PublicRightsOut(
        tenant_code=tenant.code, tenant_name=tenant.name,
        rights_url=tenant.rights_url, withdraw_url=tenant.withdraw_url,
        grievance_url=tenant.grievance_url, board_complaint_url=tenant.board_complaint_url,
        grievance_response_days=tenant.grievance_response_days,
    )


@router.get("/{tenant_code}/purposes", response_model=list[PublicPurposeOut])
def public_purposes(tenant_code: str, request: Request, db: Session = Depends(get_db)):
    """Itemised notice data for every active purpose, for an unauthenticated
    caller (a cookie banner, an SDK). Previously this issued one query for
    the current PurposeVersion per purpose (a lazy load of `Purpose.versions`
    inside consent_service.get_current_purpose_version) plus one query for
    its data categories and one for its processing activities - roughly
    1 + 3N queries for N purposes, on a route with no rate limiter at all,
    unlike login/context elsewhere. Batched into a fixed 4 queries
    regardless of N (tenant lookup, purposes, all current versions in one
    IN-query, then one query each for the union of every referenced
    DataCategory/ProcessingActivity id) plus a shared per-IP limiter.
    """
    _rate_limit(request)
    _get_tenant_or_404(tenant_code, db)

    purposes = db.query(Purpose).filter(Purpose.is_active.is_(True)).order_by(Purpose.name).all()
    if not purposes:
        return []

    purpose_ids = [p.id for p in purposes]
    versions = (
        db.query(PurposeVersion)
        .filter(PurposeVersion.purpose_id.in_(purpose_ids), PurposeVersion.is_current.is_(True))
        .all()
    )
    version_by_purpose_id = {v.purpose_id: v for v in versions}

    all_category_ids: set[int] = set()
    all_activity_ids: set[int] = set()
    for v in versions:
        all_category_ids.update(v.data_category_ids or [])
        all_activity_ids.update(v.processing_activity_ids or [])

    category_by_id = (
        {c.id: c for c in db.query(DataCategory).filter(DataCategory.id.in_(all_category_ids)).all()}
        if all_category_ids else {}
    )
    activity_by_id = (
        {a.id: a for a in db.query(ProcessingActivity).filter(ProcessingActivity.id.in_(all_activity_ids)).all()}
        if all_activity_ids else {}
    )

    out: list[PublicPurposeOut] = []
    for p in purposes:
        pv = version_by_purpose_id.get(p.id)
        if pv is None:
            raise HTTPException(status_code=500, detail=f"Purpose {p.code} has no current version")
        cats = [category_by_id[cid] for cid in (pv.data_category_ids or []) if cid in category_by_id]
        acts = [activity_by_id[aid] for aid in (pv.processing_activity_ids or []) if aid in activity_by_id]
        out.append(PublicPurposeOut(
            purpose_version_id=pv.id, code=p.code, name=p.name,
            description=pv.description or p.description, legal_basis=p.legal_basis,
            requires_consent=p.requires_consent, retention_period_days=p.retention_period_days,
            consent_text=pv.consent_text, data_categories=[c.name for c in cats],
            processing_activities=[a.name for a in acts], translations=pv.translations or {},
        ))
    return out


@router.get("/{tenant_code}/notices/{purpose_code}", response_model=PublicNoticeOut)
def public_notice(tenant_code: str, purpose_code: str, request: Request,
                  lang: str | None = None, db: Session = Depends(get_db)):
    """R1-04 public render endpoint: the exact notice content (in the
    requested language, falling back to the version's own default language -
    never a 404 for an unsupported language, matching the frontend i18n
    fallback convention) a caller must show before/with a consent request,
    and the payload a consent event should be recorded against (A-07)."""
    _rate_limit(request)
    tenant = _get_tenant_or_404(tenant_code, db)

    purpose = db.query(Purpose).filter(Purpose.code == purpose_code, Purpose.is_active.is_(True)).first()
    if not purpose:
        raise HTTPException(status_code=404, detail="Unknown purpose")

    notice = db.query(Notice).filter(Notice.purpose_id == purpose.id, Notice.is_active.is_(True)).first()
    current = next((v for v in notice.versions if v.is_current), None) if notice else None
    if not current:
        raise HTTPException(status_code=404, detail="No published notice for this purpose")

    requested = (lang or current.language_default or "en")
    lang_key = requested.lower()
    lang_content = (current.translations or {}).get(lang_key)
    if lang_content and (lang_content.get("title") or lang_content.get("body")):
        served = lang_key
        title = lang_content.get("title") or current.title
        body = lang_content.get("body") or current.body
    else:
        served = current.language_default
        title = current.title
        body = current.body

    return PublicNoticeOut(
        tenant_code=tenant.code, purpose_code=purpose.code, purpose_name=purpose.name,
        notice_version_id=current.id, version_number=current.version_number,
        language_requested=requested, language_served=served,
        title=title, body=body, data_items=current.data_items or [],
        services_enabled=current.services_enabled, legal_basis=purpose.legal_basis,
        requires_consent=purpose.requires_consent,
        retention_period_days=current.retention_period_days, retention_note=current.retention_note,
        child_restricted=current.child_restricted, links=current.links or {},
        contact=current.contact_snapshot or {}, content_hash=current.content_hash,
        effective_from=current.effective_from,
    )
