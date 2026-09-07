"""R1-14 (F-01..F-06): the children and guardian-consent API.

Three surfaces, one register:

* **Age assurance** (`/children/age-assurance`) - the F-01 onboarding step
  the demo sites' pre-ticked "I am 18+" checkbox stands in for today. Gated on
  `consent.manage`, because recording that a principal is or is not a child is
  an operational consent act performed by the same staff who work consents.
* **Guardian consents** (`/children/guardian-consents*`) - the R.10 / R.11
  register, created then separately verified, also on `consent.manage`.
* **Fourth Schedule exemptions** (`/children/exemptions*`) - gated on
  `policy.manage` instead, which in `rbac.py` is held only by `admin` and
  `dpo`. Deciding that a tenant falls inside a Fourth Schedule class is a
  legal judgement about the fiduciary itself, not an operational act on one
  principal's record, and it disapplies part of the highest-penalty head in
  the Act. The people who can grant one consent should not thereby be able to
  disapply s.9(1) for a whole tenant.

No new permission strings are introduced. That is a deliberate choice worth
stating: `rbac.py` is a shared file that a concurrent lane is editing, and
every act here maps cleanly onto an existing bar - the operational acts onto
`consent.manage`/`consent.view` (which already carry the power to grant and
withdraw a principal's consent) and the tenant-level legal judgement onto
`policy.manage`. If the platform later wants a separate `children.manage`
pair, adding it is additive and changes no call site's meaning.

Every list and lookup is org-scoped through `get_org_scope` exactly like the
customers/consents/audit lists, and every customer is resolved through
`services/tenancy.py::resolve_customer` - the single choke point - so a
jobhub_admin cannot record an age assurance against a CODEX child.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_org_scope, require_permission
from app.core.database import get_db
from app.core.rbac import PERM_CONSENT_MANAGE, PERM_CONSENT_VIEW, PERM_POLICY_MANAGE, PERM_POLICY_VIEW
from app.core.utils import mask_identifier
from app.models.entities import User
from app.models.guardian import GuardianConsent, TenantChildExemption
from app.schemas.guardian import (
    AgeAssuranceIn,
    AgeAssuranceOut,
    ChildExemptionActivateIn,
    ChildExemptionIn,
    ChildExemptionOut,
    ChildrenMetricsOut,
    GuardianConsentIn,
    GuardianConsentOut,
    GuardianConsentRejectIn,
    GuardianConsentRevokeIn,
    GuardianConsentVerifyIn,
)
from app.services import guardian as guardian_service
from app.services.audit import log_audit
from app.services.tenancy import ANY_TENANT, resolve_customer, resolve_tenant_id

router = APIRouter(prefix="/children", tags=["children-and-guardians"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_principal(db: Session, external_id: str, scope: str | None):
    """The one way a customer enters this module.

    `scope or ANY_TENANT` is the same staff-admin-fallback pattern
    customers.py, consents.py, receipts.py and objections.py already use, and
    it is behind require_permission on every route here: `scope` is None only
    for a genuinely unscoped platform admin, while every org-scoped role
    (jobhub_admin, codex_admin, skilllearn_admin) always gets its own real
    scope string. See tests/test_customer_resolution_guard.py.
    """
    customer = resolve_customer(db, source_app=scope or ANY_TENANT, external_id=external_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


def _guardian_out(record: GuardianConsent) -> GuardianConsentOut:
    out = GuardianConsentOut.model_validate(record)
    # A guardian's name and address are the guardian's own personal data. The
    # queue needs to tell two records apart, not to read a parent's inbox -
    # same treatment `crm_directory` and the audit views already give a
    # principal's identifiers.
    out.guardian_email = mask_identifier(record.guardian_email or "")
    return out


def _exemption_out(row: TenantChildExemption) -> ChildExemptionOut:
    return ChildExemptionOut.model_validate(row)


# --------------------------------------------------------------------------- #
#  F-01: age assurance
# --------------------------------------------------------------------------- #


@router.post("/age-assurance", response_model=AgeAssuranceOut)
def record_age_assurance(
    payload: AgeAssuranceIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    """F-01: record the age-assurance finding for a principal (s.2(f), R.10).

    `is_child` is DERIVED from `date_of_birth`; the caller cannot assert it.
    The one exception is `SELF_DECLARED`, which stores the principal's own
    unverified claim and can never be marked verified - see
    `ck_age_assurances_self_declared_is_not_verified`.
    """
    scope = get_org_scope(user)
    customer = _resolve_principal(db, payload.customer_external_id, scope)
    record = guardian_service.record_age_assurance(
        db,
        customer=customer,
        assurance_method=payload.assurance_method,
        date_of_birth=payload.date_of_birth,
        declared_is_child=payload.declared_is_child,
        is_person_with_disability=payload.is_person_with_disability,
        assurance_reference=payload.assurance_reference,
        token_issuer=payload.token_issuer,
        details=payload.details,
        actor_username=user.username,
        source_app=customer.source_app or "",
        request_id=request.headers.get("X-Request-ID"),
    )
    return AgeAssuranceOut.model_validate(record)


@router.get("/age-assurance/{customer_external_id}", response_model=AgeAssuranceOut)
def get_age_assurance(
    customer_external_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_VIEW)),
):
    scope = get_org_scope(user)
    customer = _resolve_principal(db, customer_external_id, scope)
    record = guardian_service.get_age_assurance(db, customer.id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="No age assurance has been recorded for this data principal",
        )
    return AgeAssuranceOut.model_validate(record)


# --------------------------------------------------------------------------- #
#  F-02 / F-03: guardian consents
# --------------------------------------------------------------------------- #


@router.post("/guardian-consents", response_model=GuardianConsentOut, status_code=201)
def create_guardian_consent(
    payload: GuardianConsentIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    """F-02/F-03: open a parental (s.9(1)) or lawful-guardian (R.11) consent
    record. Always PENDING - verification is a separate, deliberate act."""
    scope = get_org_scope(user)
    customer = _resolve_principal(db, payload.customer_external_id, scope)

    guardian_customer_id = None
    if payload.guardian_customer_external_id:
        guardian_account = _resolve_principal(db, payload.guardian_customer_external_id, scope)
        guardian_customer_id = guardian_account.id

    record = guardian_service.create_guardian_consent(
        db,
        customer=customer,
        guardian_type=payload.guardian_type,
        verification_method=payload.verification_method,
        guardian_name=payload.guardian_name,
        guardian_email=payload.guardian_email,
        guardian_phone=payload.guardian_phone,
        guardian_customer_id=guardian_customer_id,
        guardian_identity_reference=payload.guardian_identity_reference,
        guardian_date_of_birth=payload.guardian_date_of_birth,
        virtual_token_issuer=payload.virtual_token_issuer,
        virtual_token_reference=payload.virtual_token_reference,
        appointment_authority=payload.appointment_authority,
        appointment_reference=payload.appointment_reference,
        appointment_date=payload.appointment_date,
        actor_username=user.username,
        source_app=customer.source_app or "",
        request_id=request.headers.get("X-Request-ID"),
    )
    return _guardian_out(record)


def _get_guardian_record(db: Session, reference_no: str, scope: str | None) -> GuardianConsent:
    record = (
        db.query(GuardianConsent)
        .filter(GuardianConsent.reference_no == reference_no)
        .first()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Guardian consent not found")
    # Org scoping on the record's own source_app, the same filter the
    # customers/consents/audit lists apply. A record outside the caller's
    # scope is indistinguishable from one that does not exist.
    if scope is not None and record.source_app != scope:
        raise HTTPException(status_code=404, detail="Guardian consent not found")
    return record


@router.get("/guardian-consents", response_model=list[GuardianConsentOut])
def list_guardian_consents(
    customer_external_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_VIEW)),
):
    scope = get_org_scope(user)
    query = db.query(GuardianConsent)
    if scope is not None:
        query = query.filter(GuardianConsent.source_app == scope)
    if customer_external_id:
        customer = _resolve_principal(db, customer_external_id, scope)
        query = query.filter(GuardianConsent.customer_id == customer.id)
    if status:
        query = query.filter(GuardianConsent.status == status.upper())
    rows = query.order_by(GuardianConsent.created_at.desc()).limit(200).all()
    return [_guardian_out(r) for r in rows]


@router.get("/guardian-consents/{reference_no}", response_model=GuardianConsentOut)
def get_guardian_consent(
    reference_no: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_VIEW)),
):
    return _guardian_out(_get_guardian_record(db, reference_no, get_org_scope(user)))


@router.post("/guardian-consents/{reference_no}/verify", response_model=GuardianConsentOut)
def verify_guardian_consent(
    reference_no: str,
    payload: GuardianConsentVerifyIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    """R.10(1): perform the due diligence, and mark VERIFIED only if it passes.

    The VIRTUAL_TOKEN route (R.10(2)(c)) returns **501** in this build: the
    Digital Locker interface exists and no integration sits behind it, and a
    stub that returned success would be a fabricated verification for a child.
    The record stays PENDING and continues to count as no parental consent.
    """
    record = _get_guardian_record(db, reference_no, get_org_scope(user))
    return _guardian_out(
        guardian_service.verify_guardian_consent(
            db,
            record,
            verification_note=payload.verification_note,
            actor_username=user.username,
            request_id=request.headers.get("X-Request-ID"),
        )
    )


@router.post("/guardian-consents/{reference_no}/reject", response_model=GuardianConsentOut)
def reject_guardian_consent(
    reference_no: str,
    payload: GuardianConsentRejectIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    record = _get_guardian_record(db, reference_no, get_org_scope(user))
    return _guardian_out(
        guardian_service.reject_guardian_consent(
            db,
            record,
            rejection_reason=payload.rejection_reason,
            actor_username=user.username,
            request_id=request.headers.get("X-Request-ID"),
        )
    )


@router.post("/guardian-consents/{reference_no}/revoke", response_model=GuardianConsentOut)
def revoke_guardian_consent(
    reference_no: str,
    payload: GuardianConsentRevokeIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_MANAGE)),
):
    """Revocation takes effect on the next consent action with no cascade -
    every enforcement point asks for a VERIFIED record fresh."""
    record = _get_guardian_record(db, reference_no, get_org_scope(user))
    return _guardian_out(
        guardian_service.revoke_guardian_consent(
            db,
            record,
            reason=payload.reason,
            actor_username=user.username,
            request_id=request.headers.get("X-Request-ID"),
        )
    )


# --------------------------------------------------------------------------- #
#  F-05: Fourth Schedule exemptions
# --------------------------------------------------------------------------- #


@router.get("/exemptions", response_model=list[ChildExemptionOut])
def list_exemptions(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_VIEW)),
):
    scope = get_org_scope(user)
    query = db.query(TenantChildExemption)
    if scope is not None:
        query = query.filter(TenantChildExemption.tenant_id == resolve_tenant_id(db, scope))
    return [_exemption_out(r) for r in query.order_by(TenantChildExemption.id.desc()).all()]


@router.post("/exemptions", response_model=ChildExemptionOut, status_code=201)
def create_exemption(
    payload: ChildExemptionIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    """F-05 / R.12: register a Fourth Schedule exemption for this tenant.

    Created INACTIVE. Nothing is exempt from anything until someone with
    `policy.manage` separately activates it with a written justification -
    the same two-step shape verification uses, and for the same reason: the
    act that disapplies part of s.9 should not be a side effect of filling in
    a form.
    """
    scope = get_org_scope(user)
    tenant_id = resolve_tenant_id(db, scope) if scope else resolve_tenant_id(db, None)
    row = TenantChildExemption(
        tenant_id=tenant_id,
        exemption_class=payload.exemption_class,
        schedule_reference=payload.schedule_reference,
        purpose_codes=list(payload.purpose_codes),
        exempted_obligations=list(payload.exempted_obligations),
        conditions=payload.conditions,
        is_active=False,
        effective_from=payload.effective_from or _utcnow(),
        effective_to=payload.effective_to,
        created_by=user.username,
    )
    db.add(row)
    db.flush()
    log_audit(
        db,
        "CHILD_EXEMPTION_CONFIGURED",
        actor_username=user.username,
        actor_type="USER",
        tenant_id=tenant_id,
        source_app=scope or "",
        reason=(
            f"Fourth Schedule exemption registered (inactive): {payload.exemption_class}, "
            f"{payload.schedule_reference}, purposes {sorted(payload.purpose_codes)}, "
            f"relaxing {sorted(payload.exempted_obligations)}."
        ),
        request_id=request.headers.get("X-Request-ID"),
        metadata={
            "exemption_id": row.id,
            "exemption_class": payload.exemption_class,
            "schedule_reference": payload.schedule_reference,
            "purpose_codes": sorted(payload.purpose_codes),
            "exempted_obligations": sorted(payload.exempted_obligations),
        },
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return _exemption_out(row)


@router.post("/exemptions/{exemption_id}/activate", response_model=ChildExemptionOut)
def activate_exemption(
    exemption_id: int,
    payload: ChildExemptionActivateIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    row = _get_exemption(db, exemption_id, get_org_scope(user))
    row.is_active = True
    row.authorised_by = user.username
    row.authorised_at = _utcnow()
    log_audit(
        db,
        "CHILD_EXEMPTION_ACTIVATED",
        actor_username=user.username,
        actor_type="USER",
        tenant_id=row.tenant_id,
        reason=(
            f"Fourth Schedule exemption {row.id} ({row.exemption_class}, "
            f"{row.schedule_reference}) activated. Justification: {payload.justification}"
        ),
        request_id=request.headers.get("X-Request-ID"),
        metadata={
            "exemption_id": row.id,
            "purpose_codes": sorted(row.purpose_codes or []),
            "exempted_obligations": sorted(row.exempted_obligations or []),
        },
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return _exemption_out(row)


@router.post("/exemptions/{exemption_id}/deactivate", response_model=ChildExemptionOut)
def deactivate_exemption(
    exemption_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    row = _get_exemption(db, exemption_id, get_org_scope(user))
    row.is_active = False
    log_audit(
        db,
        "CHILD_EXEMPTION_DEACTIVATED",
        actor_username=user.username,
        actor_type="USER",
        tenant_id=row.tenant_id,
        reason=f"Fourth Schedule exemption {row.id} deactivated.",
        request_id=request.headers.get("X-Request-ID"),
        metadata={"exemption_id": row.id},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return _exemption_out(row)


def _get_exemption(db: Session, exemption_id: int, scope: str | None) -> TenantChildExemption:
    row = db.query(TenantChildExemption).filter(TenantChildExemption.id == exemption_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Exemption not found")
    if scope is not None and row.tenant_id != resolve_tenant_id(db, scope):
        raise HTTPException(status_code=404, detail="Exemption not found")
    return row


# --------------------------------------------------------------------------- #
#  F-06: K-24 / K-25
# --------------------------------------------------------------------------- #


@router.get("/metrics", response_model=ChildrenMetricsOut)
def children_metrics(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_CONSENT_VIEW)),
):
    """F-06: K-24 (age-assurance coverage) and K-25 (parental-consent
    completion rate, and the count of child-purpose denials)."""
    scope = get_org_scope(user)
    tenant_id = resolve_tenant_id(db, scope) if scope else None
    return ChildrenMetricsOut(
        tenant_id=tenant_id,
        metrics=guardian_service.children_metrics(db, tenant_id=tenant_id),
    )
