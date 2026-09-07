from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.rbac import PERM_PURPOSE_MANAGE, PERM_PURPOSE_VIEW
from app.models.entities import (
    DataCategory,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
    User,
)
from app.schemas.reconsent import ChangeClassificationOut
from app.schemas.schemas import (
    CoverageActivityOut,
    CoverageReportOut as _CoverageReportOut,
    DataCategoryOut,
    ProcessingActivityOut,
    PurposeIn,
    PurposeOut,
    PurposeTemplateOut,
    PurposeUpdate,
    PurposeVersionCreate,
    PurposeVersionOut,
    check_data_items_cover_categories,
)
from app.core.utils import get_request_id
from app.services import consent as consent_service
from app.services.audit import log_audit
from app.services.material_change import MaterialityError, publish_purpose_change
from app.services.tenancy import platform_tenant_id

router = APIRouter(prefix="/purposes", tags=["purposes"])


class CoverageReportOut(_CoverageReportOut):
    """K-10: widens `coverage_pct` to Optional. Zero active processing
    activities means the coverage ratio is undefined, not "100% covered".
    Overridden locally rather than in app/schemas/schemas.py, which this
    lane does not own - see coverage_report() below for the corresponding
    source-side fix."""

    coverage_pct: Optional[float] = None


def _purpose_out(purpose: Purpose, db: Session) -> PurposeOut:
    pv = None
    for v in purpose.versions:
        if v.is_current:
            pv = v
            break
    data_categories = []
    activities = []
    if pv:
        data_categories = db.query(DataCategory).filter(DataCategory.id.in_(pv.data_category_ids or [])).all()
        activities = (
            db.query(ProcessingActivity)
            .filter(ProcessingActivity.id.in_(pv.processing_activity_ids or []))
            .all()
        )
    return PurposeOut(
        id=purpose.id,
        name=purpose.name,
        code=purpose.code,
        description=purpose.description,
        legal_basis=purpose.legal_basis,
        requires_consent=purpose.requires_consent,
        retention_period_days=purpose.retention_period_days,
        services_enabled=purpose.services_enabled,
        child_restricted=purpose.child_restricted,
        retention_policy_id=purpose.retention_policy_id,
        status=purpose.status,
        current_version=purpose.current_version,
        is_active=purpose.is_active,
        created_at=purpose.created_at,
        versions=[PurposeVersionOut.model_validate(v) for v in sorted(purpose.versions, key=lambda x: x.version_number, reverse=True)],
        data_categories=[DataCategoryOut.model_validate(dc) for dc in data_categories],
        processing_activities=[ProcessingActivityOut.model_validate(pa) for pa in activities],
    )


def _apply_version_to_purpose(purpose: Purpose, pv: PurposeVersion) -> None:
    purpose.name = pv.name
    purpose.description = pv.description
    purpose.legal_basis = pv.legal_basis
    purpose.requires_consent = pv.requires_consent
    purpose.retention_period_days = pv.retention_period_days
    purpose.services_enabled = pv.services_enabled
    purpose.child_restricted = pv.child_restricted
    purpose.retention_policy_id = pv.retention_policy_id
    purpose.current_version = pv.version_number


# Ready-to-submit PurposeIn payloads for the two lawful-gateway families the
# workbook names explicitly (L-02 employment, L-03 State/benefit processing).
# Returned as scaffolding for the create-purpose form, not persisted here -
# an admin still POSTs the (possibly edited) payload to create a real Purpose.
_PURPOSE_TEMPLATES: list[dict] = [
    {
        "key": "employment",
        "label": "Employment purposes (s.7(i))",
        "guidance": (
            "Processing for employment, or to safeguard the employer from loss or liability "
            "(e.g. preventing corporate espionage, protecting trade secrets/IP/classified "
            "information, or providing a service or benefit the employee has sought). Does not "
            "require consent, but must stay within purpose limits: only employment-related data, "
            "not repurposed for e.g. marketing to employees."
        ),
        "purpose": {
            "name": "Employment records processing",
            "code": "employment_records",
            "description": "Processing of employee personal data for employment administration "
                            "and to safeguard the employer from loss or liability.",
            "legal_basis": "S7_I",
            "requires_consent": False,
            "retention_period_days": 2555,  # ~7 years, First Schedule Part B 4(c) order of magnitude
            "data_category_ids": [],
            "processing_activity_ids": [],
            "data_items": [],
            "services_enabled": "Employment administration, payroll, and safeguarding the "
                                 "employer's confidential/trade-secret information.",
            "child_restricted": False,
            "retention_policy_id": None,
            "consent_text": "",
        },
    },
    {
        "key": "state_benefit",
        "label": "State subsidy / benefit / service (s.7(b))",
        "guidance": (
            "Use only when the tenant IS the State or a State instrumentality providing a "
            "subsidy, benefit, service, certificate, licence or permit to the data principal "
            "(L-03). If a Second Schedule intimation to the principal is required, record it "
            "against this purpose's description until a dedicated tenant-type flag exists."
        ),
        "purpose": {
            "name": "State benefit eligibility processing",
            "code": "state_benefit_eligibility",
            "description": "Processing to determine eligibility for, and provide, a State "
                            "subsidy, benefit, service, certificate, licence or permit.",
            "legal_basis": "S7_B",
            "requires_consent": False,
            "retention_period_days": 2555,
            "data_category_ids": [],
            "processing_activity_ids": [],
            "data_items": [],
            "services_enabled": "Determining and administering eligibility for a State-provided "
                                 "subsidy, benefit, service, certificate, licence or permit.",
            "child_restricted": False,
            "retention_policy_id": None,
            "consent_text": "",
        },
    },
]


@router.get("", response_model=list[PurposeOut])
def list_purposes(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_PURPOSE_VIEW))):
    purposes = db.query(Purpose).order_by(Purpose.code).all()
    return [_purpose_out(p, db) for p in purposes]


@router.get("/coverage-report", response_model=CoverageReportOut)
def coverage_report(db: Session = Depends(get_db), _: User = Depends(require_permission(PERM_PURPOSE_VIEW))):
    """K-10 lawful-gateway coverage (L-01): every ProcessingActivity checked
    against every active Purpose's *current* version - an activity is
    "covered" if at least one active purpose that lists it has a valid DPDP
    gateway (which the CHECK constraint already guarantees is true for any
    row that exists at all; this report is what makes that fact visible and
    catches an activity nobody has mapped to any purpose yet)."""
    activities = db.query(ProcessingActivity).filter(ProcessingActivity.is_active.is_(True)).order_by(ProcessingActivity.code).all()
    purposes = db.query(Purpose).filter(Purpose.is_active.is_(True)).all()

    current_versions = []
    for p in purposes:
        pv = next((v for v in p.versions if v.is_current), None)
        if pv:
            current_versions.append((p, pv))

    gateways_by_activity: dict[int, set[str]] = {}
    purposes_by_activity: dict[int, set[str]] = {}
    for p, pv in current_versions:
        for aid in (pv.processing_activity_ids or []):
            gateways_by_activity.setdefault(aid, set()).add(p.legal_basis)
            purposes_by_activity.setdefault(aid, set()).add(p.code)

    rows = []
    covered_count = 0
    for a in activities:
        gateways = sorted(gateways_by_activity.get(a.id, set()))
        covered = len(gateways) > 0
        if covered:
            covered_count += 1
        rows.append(CoverageActivityOut(
            id=a.id, code=a.code, name=a.name, covered=covered,
            gateways=gateways, purpose_codes=sorted(purposes_by_activity.get(a.id, set())),
        ))

    total = len(activities)
    # K-10: None, not 100.0, when there is no active processing activity to
    # check - an empty register is unmeasured coverage, not full coverage.
    pct = round((covered_count / total) * 100, 2) if total else None
    return CoverageReportOut(total_activities=total, covered_activities=covered_count,
                             coverage_pct=pct, activities=rows)


@router.get("/templates", response_model=list[PurposeTemplateOut])
def purpose_templates(_: User = Depends(require_permission(PERM_PURPOSE_VIEW))):
    """Employment (s.7(i), L-02) and State/benefit (s.7(b), L-03) purpose
    templates: ready-to-submit `PurposeIn` scaffolding, not persisted rows."""
    return [PurposeTemplateOut(**t) for t in _PURPOSE_TEMPLATES]


@router.get("/{purpose_id}", response_model=PurposeOut)
def get_purpose(purpose_id: int, db: Session = Depends(get_db),
                _: User = Depends(require_permission(PERM_PURPOSE_VIEW))):
    purpose = db.get(Purpose, purpose_id)
    if not purpose:
        raise HTTPException(status_code=404, detail="Purpose not found")
    return _purpose_out(purpose, db)


@router.post("", response_model=PurposeOut, status_code=201)
def create_purpose(payload: PurposeIn, db: Session = Depends(get_db),
                   current_user: User = Depends(require_permission(PERM_PURPOSE_MANAGE))):
    if db.query(Purpose).filter(Purpose.code == payload.code).first():
        raise HTTPException(status_code=409, detail="Purpose code already exists")
    purpose = Purpose(
        name=payload.name,
        code=payload.code,
        description=payload.description,
        legal_basis=payload.legal_basis,
        requires_consent=payload.requires_consent,
        retention_period_days=payload.retention_period_days,
        services_enabled=payload.services_enabled,
        child_restricted=payload.child_restricted,
        retention_policy_id=payload.retention_policy_id,
        status="ACTIVE",
        current_version=1,
        is_active=True,
        tenant_id=platform_tenant_id(db),
    )
    db.add(purpose)
    db.flush()
    pv = PurposeVersion(
        purpose_id=purpose.id,
        version_number=1,
        name=payload.name,
        description=payload.description,
        legal_basis=payload.legal_basis,
        requires_consent=payload.requires_consent,
        retention_period_days=payload.retention_period_days,
        data_category_ids=payload.data_category_ids,
        processing_activity_ids=payload.processing_activity_ids,
        data_items=[d.model_dump() for d in payload.data_items],
        services_enabled=payload.services_enabled,
        child_restricted=payload.child_restricted,
        retention_policy_id=payload.retention_policy_id,
        consent_text=payload.consent_text,
        checklist=payload.checklist.model_dump(mode="json") if payload.checklist is not None else None,
        is_current=True,
        created_by=current_user.username,
    )
    db.add(pv)
    db.flush()
    log_audit(db, "PURPOSE_CREATED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id),
              source_app="UI", purpose_id=purpose.id, purpose_code=purpose.code,
              reason=f"Purpose {purpose.code} created with version 1")
    db.commit()
    db.refresh(purpose)
    return _purpose_out(purpose, db)


@router.put("/{purpose_id}", response_model=PurposeOut)
def update_purpose(purpose_id: int, payload: PurposeUpdate, db: Session = Depends(get_db),
                   current_user: User = Depends(require_permission(PERM_PURPOSE_MANAGE))):
    purpose = db.get(Purpose, purpose_id)
    if not purpose:
        raise HTTPException(status_code=404, detail="Purpose not found")
    pv = consent_service.get_current_purpose_version(purpose)
    change_result = None
    versioned_fields_changed = any([
        payload.name is not None and payload.name != pv.name,
        payload.description is not None and payload.description != pv.description,
        payload.legal_basis is not None and payload.legal_basis != pv.legal_basis,
        payload.requires_consent is not None and payload.requires_consent != pv.requires_consent,
        payload.retention_period_days is not None and payload.retention_period_days != pv.retention_period_days,
        payload.data_category_ids is not None and payload.data_category_ids != pv.data_category_ids,
        payload.processing_activity_ids is not None and payload.processing_activity_ids != pv.processing_activity_ids,
        payload.data_items is not None and [d.model_dump() for d in payload.data_items] != (pv.data_items or []),
        payload.services_enabled is not None and payload.services_enabled != pv.services_enabled,
        payload.child_restricted is not None and payload.child_restricted != pv.child_restricted,
        payload.retention_policy_id is not None and payload.retention_policy_id != pv.retention_policy_id,
        payload.consent_text is not None and payload.consent_text != pv.consent_text,
    ])
    if versioned_fields_changed:
        merged_category_ids = payload.data_category_ids if payload.data_category_ids is not None else pv.data_category_ids
        merged_data_items = (
            [d.model_dump() for d in payload.data_items] if payload.data_items is not None else (pv.data_items or [])
        )
        try:
            check_data_items_cover_categories(merged_category_ids, merged_data_items)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        pv.is_current = False
        pv.effective_to = datetime.now(timezone.utc)
        new_version = pv.version_number + 1
        new_pv = PurposeVersion(
            purpose_id=purpose.id,
            version_number=new_version,
            name=payload.name if payload.name is not None else pv.name,
            description=payload.description if payload.description is not None else pv.description,
            legal_basis=payload.legal_basis if payload.legal_basis is not None else pv.legal_basis,
            requires_consent=payload.requires_consent if payload.requires_consent is not None else pv.requires_consent,
            retention_period_days=payload.retention_period_days if payload.retention_period_days is not None else pv.retention_period_days,
            data_category_ids=merged_category_ids,
            processing_activity_ids=payload.processing_activity_ids if payload.processing_activity_ids is not None else pv.processing_activity_ids,
            data_items=merged_data_items,
            services_enabled=payload.services_enabled if payload.services_enabled is not None else pv.services_enabled,
            child_restricted=payload.child_restricted if payload.child_restricted is not None else pv.child_restricted,
            retention_policy_id=payload.retention_policy_id if payload.retention_policy_id is not None else pv.retention_policy_id,
            consent_text=payload.consent_text if payload.consent_text is not None else pv.consent_text,
            # Content changed, so any prior review no longer covers this
            # version's actual content - never silently carried forward; a
            # fresh checklist must be submitted alongside the change that
            # needs it (or completed afterwards via another PUT) exactly
            # like the frontend's own re-gate-on-publish behaviour.
            checklist=payload.checklist.model_dump(mode="json") if payload.checklist is not None else None,
            is_current=True,
            created_by=current_user.username,
        )
        db.add(new_pv)
        db.flush()
        _apply_version_to_purpose(purpose, new_pv)
        log_audit(db, "PURPOSE_UPDATED", actor_username=current_user.username,
                  actor_role=current_user.role.name if current_user.role else "",
                  actor_type="USER", actor_id=str(current_user.id),
                  source_app="UI", purpose_id=purpose.id, purpose_code=purpose.code,
                  reason=f"Purpose versioned to v{new_version}",
                  metadata={"new_version": new_version, "old_version": pv.version_number})
        db.commit()
        # R1-09/P-01: classify the change, log it whatever it turns out to be,
        # and - if it is material - flag every affected consent and tell those
        # principals. Publishing a new version used to leave existing consents
        # untouched and silent, which is BRD 4.1.3's "consent cannot be
        # assumed" failure exactly. Deliberately NOT best-effort: if the
        # campaign cannot be started, the purpose change must not stand
        # either, or the platform would be processing under a description
        # nobody agreed to with nothing recording that it is doing so.
        try:
            change_result = publish_purpose_change(
                db, purpose, pv, new_pv, actor_username=current_user.username,
                source_app="UI", request_id=get_request_id(),
                cosmetic_overrides=payload.cosmetic_overrides or None,
            )
        except MaterialityError as exc:
            db.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        if payload.is_active is not None:
            purpose.is_active = payload.is_active
        if payload.status is not None:
            purpose.status = payload.status
        log_audit(db, "PURPOSE_UPDATED", actor_username=current_user.username,
                  actor_role=current_user.role.name if current_user.role else "",
                  actor_type="USER", actor_id=str(current_user.id),
                  source_app="UI", purpose_id=purpose.id, purpose_code=purpose.code,
                  reason="Purpose metadata updated")
        db.commit()
    db.refresh(purpose)
    out = _purpose_out(purpose, db)
    # Echoed back on the publisher's own response so they see, at the moment
    # they make the edit, that they have just required N principals to consent
    # again - rather than discovering it later from a metrics page.
    out.change = ChangeClassificationOut(**change_result) if change_result else None
    return out


@router.post("/{purpose_id}/versions", response_model=PurposeVersionOut)
def create_purpose_version(purpose_id: int, payload: PurposeVersionCreate,
                           db: Session = Depends(get_db),
                           current_user: User = Depends(require_permission(PERM_PURPOSE_MANAGE))):
    purpose = db.get(Purpose, purpose_id)
    if not purpose:
        raise HTTPException(status_code=404, detail="Purpose not found")
    pv = consent_service.get_current_purpose_version(purpose)
    pv.is_current = False
    pv.effective_to = datetime.now(timezone.utc)
    new_pv = PurposeVersion(
        purpose_id=purpose.id,
        version_number=pv.version_number + 1,
        name=pv.name,
        description=pv.description,
        legal_basis=pv.legal_basis,
        requires_consent=pv.requires_consent,
        retention_period_days=pv.retention_period_days,
        data_category_ids=pv.data_category_ids,
        processing_activity_ids=pv.processing_activity_ids,
        data_items=pv.data_items,
        services_enabled=pv.services_enabled,
        child_restricted=pv.child_restricted,
        retention_policy_id=pv.retention_policy_id,
        consent_text=pv.consent_text,
        # This endpoint copies content verbatim (no field actually changes),
        # so - unlike update_purpose's content-changing path - the prior
        # review still covers what's live; carry it forward unless the
        # caller explicitly submits a fresh one.
        checklist=payload.checklist.model_dump(mode="json") if payload.checklist is not None else pv.checklist,
        is_current=True,
        created_by=current_user.username,
    )
    db.add(new_pv)
    db.flush()
    _apply_version_to_purpose(purpose, new_pv)
    log_audit(db, "PURPOSE_UPDATED", actor_username=current_user.username,
              actor_role=current_user.role.name if current_user.role else "",
              actor_type="USER", actor_id=str(current_user.id),
              source_app="UI", purpose_id=purpose.id, purpose_code=purpose.code,
              reason=f"Purpose versioned to v{new_pv.version_number} manually: {payload.reason}",
              metadata={"new_version": new_pv.version_number})
    db.commit()
    db.refresh(new_pv)
    return new_pv
