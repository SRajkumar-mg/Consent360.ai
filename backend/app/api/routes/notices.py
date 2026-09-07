"""R1-04: notice management (A-01, A-02, A-07, D-04, A-12).

Staff CRUD + publish for the Notice/NoticeVersion entity. The public,
unauthenticated render lives in app/api/routes/public.py alongside the other
`/public/{tenant_code}/...` endpoints, not here.
"""
import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import PERM_PURPOSE_MANAGE, PERM_PURPOSE_VIEW
from app.models.entities import (
    Consent,
    ConsentEvidence,
    Notice,
    NoticeVersion,
    Organization,
    Purpose,
    User,
)
from app.schemas.schemas import NoticeIn, NoticeOut, NoticeUpdate, NoticeVersionOut
from app.services import consent as consent_service
from app.services.audit import log_audit
from app.services.tenancy import platform_tenant_id

router = APIRouter(prefix="/notices", tags=["notices"])


def _notice_out(notice: Notice) -> NoticeOut:
    return NoticeOut(
        id=notice.id,
        purpose_id=notice.purpose_id,
        purpose_code=notice.purpose.code if notice.purpose else "",
        purpose_name=notice.purpose.name if notice.purpose else "",
        status=notice.status,
        current_version=notice.current_version,
        is_active=notice.is_active,
        created_at=notice.created_at,
        versions=[NoticeVersionOut.model_validate(v) for v in sorted(notice.versions, key=lambda v: v.version_number, reverse=True)],
    )


def _latest_version(notice: Notice) -> NoticeVersion:
    return max(notice.versions, key=lambda v: v.version_number)


def _content_hash(v: NoticeVersion) -> str:
    canonical = {
        "notice_id": v.notice_id,
        "version_number": v.version_number,
        "language_default": v.language_default,
        "title": v.title,
        "body": v.body,
        "translations": v.translations or {},
        "data_items": v.data_items or [],
        "purposes": v.purposes or [],
        "services_enabled": v.services_enabled or "",
        "retention_period_days": v.retention_period_days,
        "retention_note": v.retention_note or "",
        "child_restricted": v.child_restricted,
        "links": v.links or {},
        "contact_snapshot": v.contact_snapshot or {},
    }
    payload = json.dumps(canonical, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@router.get("", response_model=list[NoticeOut])
def list_notices(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_PURPOSE_VIEW))):
    return [_notice_out(n) for n in db.query(Notice).order_by(Notice.id).all()]


@router.get("/{notice_id}", response_model=NoticeOut)
def get_notice(notice_id: int, db: Session = Depends(get_db),
               _: User = Depends(require_permission(PERM_PURPOSE_VIEW))):
    notice = db.get(Notice, notice_id)
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    return _notice_out(notice)


@router.post("", response_model=NoticeOut, status_code=201)
def create_notice(payload: NoticeIn, db: Session = Depends(get_db),
                  current_user: User = Depends(require_permission(PERM_PURPOSE_MANAGE))):
    purpose = db.get(Purpose, payload.purpose_id)
    if not purpose:
        raise HTTPException(status_code=404, detail="Purpose not found")
    if db.query(Notice).filter(Notice.purpose_id == purpose.id).first():
        raise HTTPException(status_code=409, detail="A notice already exists for this purpose")

    pv = consent_service.get_current_purpose_version(purpose)
    data_items = [d.model_dump() for d in payload.data_items] or list(pv.data_items or [])
    services_enabled = payload.services_enabled or purpose.services_enabled or ""
    retention_days = payload.retention_period_days if payload.retention_period_days is not None else purpose.retention_period_days

    notice = Notice(
        tenant_id=platform_tenant_id(db), purpose_id=purpose.id,
        status="DRAFT", current_version=0, is_active=True,
    )
    db.add(notice)
    db.flush()
    nv = NoticeVersion(
        notice_id=notice.id, version_number=1,
        language_default=payload.language_default, title=payload.title, body=payload.body,
        translations={k: v.model_dump() for k, v in payload.translations.items()},
        data_items=data_items, services_enabled=services_enabled,
        retention_period_days=retention_days, retention_note=payload.retention_note,
        child_restricted=payload.child_restricted, is_current=False,
        checklist=payload.checklist.model_dump(mode="json") if payload.checklist is not None else None,
        created_by=current_user.username,
    )
    db.add(nv)
    db.flush()
    log_audit(db, "NOTICE_CREATED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id),
              source_app="UI", purpose_id=purpose.id, purpose_code=purpose.code,
              reason=f"Notice created (draft v1) for purpose {purpose.code}")
    db.commit()
    db.refresh(notice)
    return _notice_out(notice)


@router.put("/{notice_id}", response_model=NoticeOut)
def update_notice(notice_id: int, payload: NoticeUpdate, db: Session = Depends(get_db),
                  current_user: User = Depends(require_permission(PERM_PURPOSE_MANAGE))):
    notice = db.get(Notice, notice_id)
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    if payload.is_active is not None:
        notice.is_active = payload.is_active

    latest = _latest_version(notice)
    # Fields whose presence means the *content* of the notice is changing -
    # kept separate from `checklist` so a checklist-only PUT (attaching a
    # review to a draft whose text nobody touched this call) doesn't count as
    # a content change for the reset rule just below.
    other_content_given = any([
        payload.language_default is not None, payload.title is not None, payload.body is not None,
        payload.translations is not None, payload.data_items is not None,
        payload.services_enabled is not None, payload.retention_period_days is not None,
        payload.retention_note is not None, payload.child_restricted is not None,
    ])
    content_fields_given = other_content_given or payload.checklist is not None

    def _merged(field: str, value):
        return value if value is not None else getattr(latest, field)

    if content_fields_given:
        new_checklist = payload.checklist.model_dump(mode="json") if payload.checklist is not None else None
        if latest.is_current:
            # The live version cannot be silently rewritten (it may already
            # be pinned by consents/evidence) - editing spawns a new draft
            # on top of it, left unpublished until POST /publish.
            target = NoticeVersion(
                notice_id=notice.id, version_number=latest.version_number + 1,
                language_default=_merged("language_default", payload.language_default),
                title=_merged("title", payload.title),
                body=_merged("body", payload.body),
                translations=(
                    {k: v.model_dump() for k, v in payload.translations.items()}
                    if payload.translations is not None else dict(latest.translations or {})
                ),
                data_items=(
                    [d.model_dump() for d in payload.data_items]
                    if payload.data_items is not None else list(latest.data_items or [])
                ),
                services_enabled=_merged("services_enabled", payload.services_enabled),
                retention_period_days=_merged("retention_period_days", payload.retention_period_days),
                retention_note=_merged("retention_note", payload.retention_note),
                child_restricted=_merged("child_restricted", payload.child_restricted),
                # A-09: this is a new, as yet unreviewed, version - it never
                # inherits the live version's checklist (which reviewed
                # different, already-superseded content); only a checklist
                # submitted on this same call applies to it.
                checklist=new_checklist,
                is_current=False, created_by=current_user.username,
            )
            db.add(target)
            reason = f"Notice {notice.id} drafted new v{target.version_number} on top of the live version"
        else:
            # Still a pending draft that has never gone live - safe to edit in place.
            target = latest
            if payload.language_default is not None:
                target.language_default = payload.language_default
            if payload.title is not None:
                target.title = payload.title
            if payload.body is not None:
                target.body = payload.body
            if payload.translations is not None:
                target.translations = {k: v.model_dump() for k, v in payload.translations.items()}
            if payload.data_items is not None:
                target.data_items = [d.model_dump() for d in payload.data_items]
            if payload.services_enabled is not None:
                target.services_enabled = payload.services_enabled
            if payload.retention_period_days is not None:
                target.retention_period_days = payload.retention_period_days
            if payload.retention_note is not None:
                target.retention_note = payload.retention_note
            if payload.child_restricted is not None:
                target.child_restricted = payload.child_restricted
            if payload.checklist is not None:
                target.checklist = new_checklist
            elif other_content_given:
                # A-09: the draft's content just changed - any checklist
                # already recorded against it reviewed the old content, not
                # this content, so it no longer covers what's about to be
                # published. Same rule as update_purpose's content-changing
                # path (app/api/routes/purposes.py): never silently carried
                # forward, a fresh checklist must be submitted alongside (or
                # after) the change that needs it.
                target.checklist = None
            reason = f"Notice {notice.id} draft v{target.version_number} updated"
        log_audit(db, "NOTICE_UPDATED", actor_username=current_user.username,
                  actor_role=current_user.role.name if current_user.role else "",
                  actor_type="USER", actor_id=str(current_user.id),
                  source_app="UI", purpose_id=notice.purpose_id, reason=reason)
    db.commit()
    db.refresh(notice)
    return _notice_out(notice)


@router.post("/{notice_id}/publish", response_model=NoticeOut)
def publish_notice(notice_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(require_permission(PERM_PURPOSE_MANAGE))):
    notice = db.get(Notice, notice_id)
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    latest = _latest_version(notice)
    if latest.is_current:
        raise HTTPException(status_code=409, detail="The latest version is already published")
    if not latest.title or not latest.body:
        raise HTTPException(status_code=422, detail="A notice needs a title and body before it can be published")

    # A-09 (R2-09): a plain-language and dark-pattern review checklist must be
    # completed and stored against this exact draft before it can reach any
    # Data Principal - reuses ReviewChecklistIn (schemas.py), the same shape
    # PurposeVersion.checklist/PolicyVersion.checklist already store, but
    # those two never actually check it here; this is the real gate they are
    # still missing. Attach a checklist to this draft via
    # PUT /notices/{notice_id} (its `checklist` field) before publishing.
    # See NoticeVersion.checklist's docstring for why a version published
    # before this gate existed is grandfathered rather than un-published.
    checklist = latest.checklist
    missing: list[str] = []
    if not checklist:
        missing.append(
            f"a completed plain-language and dark-pattern review checklist - submit one via "
            f"PUT /notices/{notice.id} with a `checklist` field before publishing"
        )
    else:
        if not checklist.get("reviewer"):
            missing.append("a named reviewer on the checklist")
        if not checklist.get("items"):
            missing.append("at least one confirmed checklist item")
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Notice {notice.id} v{latest.version_number} cannot be published: missing " + "; ".join(missing),
        )

    purpose = db.get(Purpose, notice.purpose_id)
    pv = consent_service.get_current_purpose_version(purpose)
    tenant = db.get(Organization, notice.tenant_id)
    now = datetime.now(timezone.utc)

    previous_current = next((v for v in notice.versions if v.is_current), None)
    if previous_current:
        previous_current.is_current = False
        previous_current.effective_to = now

    latest.purposes = [{
        "code": purpose.code, "name": purpose.name, "description": pv.description,
        "legal_basis": purpose.legal_basis, "requires_consent": purpose.requires_consent,
        "retention_period_days": purpose.retention_period_days,
    }]
    latest.links = {
        "withdraw_url": tenant.withdraw_url if tenant else "",
        "rights_url": tenant.rights_url if tenant else "",
        "grievance_url": tenant.grievance_url if tenant else "",
        "board_complaint_url": tenant.board_complaint_url if tenant else "",
    }
    latest.contact_snapshot = {
        "dpo_name": tenant.dpo_name if tenant else "",
        "dpo_email": tenant.dpo_email if tenant else "",
        "dpo_phone": tenant.dpo_phone if tenant else "",
    }
    latest.content_hash = _content_hash(latest)
    latest.is_current = True
    latest.effective_from = now
    latest.published_by = current_user.username
    latest.published_at = now

    notice.status = "ACTIVE"
    notice.current_version = latest.version_number

    log_audit(db, "NOTICE_PUBLISHED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id),
              source_app="UI", purpose_id=notice.purpose_id, purpose_code=purpose.code,
              reason=f"Notice {notice.id} v{latest.version_number} published",
              metadata={"notice_version_id": latest.id, "content_hash": latest.content_hash})
    db.commit()
    db.refresh(notice)
    return _notice_out(notice)


@router.delete("/{notice_id}")
def delete_notice(notice_id: int, db: Session = Depends(get_db),
                  current_user: User = Depends(require_permission(PERM_PURPOSE_MANAGE))):
    notice = db.get(Notice, notice_id)
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    version_ids = [v.id for v in notice.versions]
    in_use = False
    if version_ids:
        in_use = (
            db.query(ConsentEvidence).filter(ConsentEvidence.notice_version_id.in_(version_ids)).first() is not None
            or db.query(Consent).filter(Consent.notice_version_id.in_(version_ids)).first() is not None
        )
    if in_use:
        notice.is_active = False
        notice.status = "RETIRED"
        log_audit(db, "NOTICE_RETIRED", actor_username=current_user.username,
                  actor_role=current_user.role.name if current_user.role else "",
                  actor_type="USER", actor_id=str(current_user.id),
                  source_app="UI", purpose_id=notice.purpose_id,
                  reason="Notice retired because a consent/evidence record references it")
        db.commit()
        return {"deleted": False, "retired": True, "notice_id": notice_id}

    log_audit(db, "NOTICE_DELETED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id),
              source_app="UI", purpose_id=notice.purpose_id,
              reason=f"Notice {notice.id} deleted")
    db.query(NoticeVersion).filter(NoticeVersion.notice_id == notice.id).delete(synchronize_session=False)
    db.delete(notice)
    db.commit()
    return {"deleted": True, "notice_id": notice_id}
