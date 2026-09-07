"""R3-10 (CM-01, CM-02, CM-06, J-04): the Consent Manager interoperability API.

DPDP Act s.6(7) gives a Data Principal the right to give, manage, review and
withdraw consent **through a Consent Manager**. This router is that surface,
in four groups:

  * `/consent-manager/registrations/*` - staff-managed. The registry of
    Board-registered Consent Managers and the fiduciary **onboarding** model
    (First Schedule Part B 1). Onboarding is what authorises a CM to act for a
    tenant; it is a deliberate, dated, revocable staff action, never a
    side effect of an API call.
  * `/consent-manager/artefacts/*` - the versioned consent-artefact API, called
    with a tenant-bound API key. Create (give), read and list (review), update
    (manage) and withdraw. Every artefact version is an immutable, HMAC-signed
    event.
  * `/consent-manager/disclosures` and `/consent-manager/artefact-schema` -
    unauthenticated. The Part B 11 transparency disclosures (gap J-04) and the
    machine-readable descriptor of the artefact payload; both exist to be read
    by people and systems that have no credential with us, which is the point
    of publishing them.
  * `/consent-manager/metrics` - staff-read. CM-06 / K-42 / K-43.

Every artefact operation resolves its authorisation through exactly one
function, `app/services/consent_manager.py::resolve_broker_context`. Nothing in
this file re-derives a tenant from a caller-supplied string, and every
principal lookup goes through `resolve_customer`. Read that function's
docstring before changing anything here: this is the one API in the codebase
where the calling key's tenant is legitimately not the tenant whose data is
touched.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import (
    ResolvedApiKey,
    require_permission,
    require_scope,
    verify_integration_key,
)
from app.core.database import get_db
from app.core.rbac import PERM_AUDIT_VIEW, PERM_POLICY_MANAGE
from app.core.utils import get_request_id, public_limiter
from app.models.artefacts import (
    ConsentArtefact,
    ConsentArtefactEvent,
    ConsentManager,
    ConsentManagerApiCall,
    ConsentManagerFiduciary,
)
from app.models.entities import Organization, User
from app.schemas.artefacts import (
    ArtefactCreateIn,
    ArtefactEventOut,
    ArtefactListOut,
    ArtefactOut,
    ArtefactPurposeOut,
    ArtefactSchemaOut,
    ArtefactUpdateIn,
    ArtefactWithdrawIn,
    ConsentManagerDisclosureOut,
    ConsentManagerIn,
    ConsentManagerMetricsOut,
    ConsentManagerOut,
    ConsentManagerUpdate,
    FiduciaryOnboardingIn,
    FiduciaryOnboardingOut,
    FiduciaryOnboardingUpdate,
)
from app.services import consent_manager as cm_service
from app.services.audit import log_audit
from app.services.tenancy import resolve_customer

router = APIRouter(prefix="/consent-manager", tags=["consent-manager"])


# --------------------------------------------------------------------------- #
#  Serialisation helpers
# --------------------------------------------------------------------------- #

def _cm_out(db: Session, cm: ConsentManager) -> ConsentManagerOut:
    org = db.get(Organization, cm.tenant_id)
    out = ConsentManagerOut.model_validate(cm)
    out.tenant_code = org.code if org else ""
    out.onboarded_fiduciary_count = sum(1 for f in cm.fiduciaries if f.status == "ACTIVE")
    return out


def _artefact_out(
    db: Session, artefact: ConsentArtefact, ctx: cm_service.BrokerContext
) -> ArtefactOut:
    """Serialise one artefact for the caller in `ctx`.

    This is where the data-blind decision is applied on the read path: the
    fiduciary's own external id for the principal is included only when the
    caller IS that fiduciary. For a Consent Manager the principal is the
    pseudonymous `principal_ref` and nothing else - see
    `app/services/consent_manager.py`'s module docstring.

    Note the deliberate asymmetry between the response envelope and `payload`.
    The envelope is rendered per reader, so a fiduciary reading an artefact a
    Consent Manager brokered for it does get `principal_external_id`. The
    `payload` is NOT re-rendered: it is the exact bytes that were hashed and
    signed when that version was written, so an artefact created by a
    data-blind Consent Manager keeps `data_blind: true` and no direct
    identifier forever, whoever later reads it. Re-rendering the payload per
    caller would invalidate every signature and destroy the only thing that
    makes a version verifiable - do not "fix" the asymmetry that way.
    """
    events = sorted(artefact.events, key=lambda e: e.artefact_version)
    latest = events[-1] if events else None

    by_purpose: dict[str, ArtefactPurposeOut] = {}
    for link in artefact.links:
        entry = by_purpose.get(link.purpose_code)
        if entry is None:
            entry = ArtefactPurposeOut(
                purpose_code=link.purpose_code, purpose_name="", status=link.status
            )
            by_purpose[link.purpose_code] = entry
        entry.consent_ids.append(link.consent_id)
        if link.status == "ACTIVE":
            entry.status = "ACTIVE"

    # Purpose names come off the stored payload rather than a fresh Purpose
    # query: the payload is the snapshot that was actually signed, so a name
    # that has since been edited cannot make a historical artefact appear to
    # say something it never said.
    if latest:
        for entry in (latest.payload.get("pii_processing") or {}).get("purposes", []) or []:
            code = (entry.get("x_consent360") or {}).get("purpose_code")
            target = by_purpose.get(code)
            if target is not None and not target.purpose_name:
                target.purpose_name = entry.get("purpose", "")

    principal_external_id = None
    if not ctx.data_blind:
        customer = resolve_customer(
            db, source_app=artefact.source_app, customer_pk=artefact.customer_id
        )
        principal_external_id = customer.external_id if customer else None

    cm_ref = None
    if artefact.consent_manager_id:
        owner = db.get(ConsentManager, artefact.consent_manager_id)
        cm_ref = owner.cm_ref if owner else None

    return ArtefactOut(
        artefact_ref=artefact.artefact_ref,
        schema_version=artefact.schema_version,
        status=artefact.status,
        artefact_version=artefact.artefact_version,
        source_app=artefact.source_app,
        consent_manager_ref=cm_ref,
        principal_ref=artefact.principal_ref,
        principal_external_id=principal_external_id,
        purposes=list(by_purpose.values()),
        expires_at=artefact.expires_at,
        withdrawn_at=artefact.withdrawn_at,
        created_at=artefact.created_at,
        updated_at=artefact.updated_at,
        payload=latest.payload if latest else {},
        events=[_event_out(e) for e in events],
    )


def _event_out(event: ConsentArtefactEvent) -> ArtefactEventOut:
    out = ArtefactEventOut.model_validate(event)
    out.signature_valid = cm_service.verify_event(event)
    return out


# --------------------------------------------------------------------------- #
#  Registry and onboarding (staff)
#
#  Gated on PERM_POLICY_MANAGE (admin and DPO) rather than a consent-operations
#  permission: onboarding a Consent Manager decides which outside party may act
#  on this platform's principals, which is a governance decision of the same
#  kind as approving a policy, not a day-to-day consent operation.
# --------------------------------------------------------------------------- #

@router.post("/registrations", response_model=ConsentManagerOut)
def register_consent_manager(
    payload: ConsentManagerIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    org = db.query(Organization).filter(Organization.code == payload.tenant_code).first()
    if not org:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No organization with code '{payload.tenant_code}'. A Consent Manager must have "
                f"its own tenant (and its own API key) before it can be registered."
            ),
        )
    if db.query(ConsentManager).filter(ConsentManager.tenant_id == org.id).first():
        raise HTTPException(
            status_code=409, detail=f"'{org.code}' is already registered as a Consent Manager"
        )

    import uuid

    now = datetime.now(timezone.utc)
    cm = ConsentManager(
        cm_ref=f"CM-{uuid.uuid4().hex[:12].upper()}",
        name=payload.name,
        tenant_id=org.id,
        board_registration_number=payload.board_registration_number,
        registration_status=payload.registration_status,
        registered_at=now if payload.registration_status == "REGISTERED" else None,
        contact_email=payload.contact_email,
        website_url=payload.website_url,
        disclosures=payload.disclosures,
        is_active=True,
    )
    db.add(cm)
    db.flush()
    log_audit(
        db,
        "CONSENT_MANAGER_REGISTERED",
        actor_username=user.username,
        actor_type="USER",
        actor_id=str(user.id),
        actor_role=user.role.name if user.role else "",
        source_app=org.code,
        tenant_id=org.id,
        reason=f"Consent Manager {cm.cm_ref} registered ({cm.registration_status})",
        metadata={
            "cm_ref": cm.cm_ref,
            "board_registration_number": cm.board_registration_number,
            "registration_status": cm.registration_status,
        },
        commit=False,
    )
    db.commit()
    db.refresh(cm)
    return _cm_out(db, cm)


@router.get("/registrations", response_model=list[ConsentManagerOut])
def list_consent_managers(
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    return [_cm_out(db, cm) for cm in db.query(ConsentManager).order_by(ConsentManager.id).all()]


def _get_cm_or_404(db: Session, cm_ref: str) -> ConsentManager:
    cm = db.query(ConsentManager).filter(ConsentManager.cm_ref == cm_ref).first()
    if not cm:
        raise HTTPException(status_code=404, detail="Consent Manager not found")
    return cm


@router.patch("/registrations/{cm_ref}", response_model=ConsentManagerOut)
def update_consent_manager(
    cm_ref: str,
    payload: ConsentManagerUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    cm = _get_cm_or_404(db, cm_ref)
    changed = payload.model_dump(exclude_unset=True)
    for field, value in changed.items():
        setattr(cm, field, value)
    if changed.get("registration_status") == "REGISTERED" and cm.registered_at is None:
        cm.registered_at = datetime.now(timezone.utc)
    log_audit(
        db,
        "CONSENT_MANAGER_UPDATED",
        actor_username=user.username,
        actor_type="USER",
        actor_id=str(user.id),
        actor_role=user.role.name if user.role else "",
        tenant_id=cm.tenant_id,
        reason=f"Consent Manager {cm.cm_ref} updated",
        metadata={"cm_ref": cm.cm_ref, "changed": sorted(changed)},
        commit=False,
    )
    db.commit()
    db.refresh(cm)
    return _cm_out(db, cm)


@router.post("/registrations/{cm_ref}/fiduciaries", response_model=FiduciaryOnboardingOut)
def onboard_fiduciary(
    cm_ref: str,
    payload: FiduciaryOnboardingIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    """Onboard a Data Fiduciary onto a Consent Manager.

    This row is the entire authorisation for that CM to act on that tenant -
    see `resolve_broker_context`. It is created here, by an authenticated
    staff user with policy-management authority, and never by anything the CM
    itself can call.
    """
    cm = _get_cm_or_404(db, cm_ref)
    org = db.query(Organization).filter(Organization.code == payload.source_app).first()
    if not org:
        raise HTTPException(status_code=404, detail=f"No organization with code '{payload.source_app}'")
    if org.id == cm.tenant_id:
        raise HTTPException(
            status_code=422,
            detail="A Consent Manager cannot be onboarded to itself.",
        )
    existing = (
        db.query(ConsentManagerFiduciary)
        .filter(
            ConsentManagerFiduciary.consent_manager_id == cm.id,
            ConsentManagerFiduciary.tenant_id == org.id,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"'{org.code}' is already onboarded to {cm.cm_ref}; use PATCH to change its status.",
        )

    now = datetime.now(timezone.utc)
    row = ConsentManagerFiduciary(
        consent_manager_id=cm.id,
        tenant_id=org.id,
        source_app=org.code,
        status=payload.status,
        allowed_purpose_codes=list(payload.allowed_purpose_codes),
        onboarded_at=now if payload.status == "ACTIVE" else None,
        onboarded_by=user.username,
        notes=payload.notes,
    )
    db.add(row)
    db.flush()
    log_audit(
        db,
        "FIDUCIARY_ONBOARDED",
        actor_username=user.username,
        actor_type="USER",
        actor_id=str(user.id),
        actor_role=user.role.name if user.role else "",
        source_app=org.code,
        tenant_id=org.id,
        reason=f"'{org.code}' onboarded to Consent Manager {cm.cm_ref} (status {row.status})",
        metadata={
            "cm_ref": cm.cm_ref,
            "source_app": org.code,
            "status": row.status,
            "allowed_purpose_codes": row.allowed_purpose_codes,
        },
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return FiduciaryOnboardingOut.model_validate(row)


@router.get("/registrations/{cm_ref}/fiduciaries", response_model=list[FiduciaryOnboardingOut])
def list_onboarded_fiduciaries(
    cm_ref: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    cm = _get_cm_or_404(db, cm_ref)
    return [FiduciaryOnboardingOut.model_validate(f) for f in cm.fiduciaries]


@router.patch(
    "/registrations/{cm_ref}/fiduciaries/{source_app}", response_model=FiduciaryOnboardingOut
)
def update_onboarding(
    cm_ref: str,
    source_app: str,
    payload: FiduciaryOnboardingUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(PERM_POLICY_MANAGE)),
):
    cm = _get_cm_or_404(db, cm_ref)
    row = (
        db.query(ConsentManagerFiduciary)
        .filter(
            ConsentManagerFiduciary.consent_manager_id == cm.id,
            ConsentManagerFiduciary.source_app == source_app,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"'{source_app}' is not onboarded to {cm.cm_ref}")
    changed = payload.model_dump(exclude_unset=True)
    for field, value in changed.items():
        setattr(row, field, value)
    now = datetime.now(timezone.utc)
    if changed.get("status") == "ACTIVE" and row.onboarded_at is None:
        row.onboarded_at = now
    if changed.get("status") == "TERMINATED":
        # Kept, not deleted: the artefacts created while onboarded remain
        # valid records an auditor must be able to attribute.
        row.terminated_at = now
    log_audit(
        db,
        "FIDUCIARY_ONBOARDING_UPDATED",
        actor_username=user.username,
        actor_type="USER",
        actor_id=str(user.id),
        actor_role=user.role.name if user.role else "",
        source_app=row.source_app,
        tenant_id=row.tenant_id,
        reason=f"Onboarding of '{row.source_app}' to {cm.cm_ref} updated (status {row.status})",
        metadata={"cm_ref": cm.cm_ref, "source_app": row.source_app, "changed": sorted(changed)},
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return FiduciaryOnboardingOut.model_validate(row)


# --------------------------------------------------------------------------- #
#  Public: transparency disclosures and the artefact schema descriptor
# --------------------------------------------------------------------------- #

@router.get("/disclosures", response_model=list[ConsentManagerDisclosureOut])
def disclosures(request: Request, db: Session = Depends(get_db)):
    """DPDP Rules 2025 First Schedule Part B 11 (gap J-04): a Consent Manager
    must publish its promoters, directors, key managerial personnel and every
    person holding more than 2% shareholding.

    Unauthenticated by design - a transparency disclosure nobody can read
    without a credential is not a disclosure. Only REGISTERED, active Consent
    Managers appear: publishing an applicant as though it were registered
    would itself be misleading. Rate-limited with the same shared per-IP
    limiter as the other public endpoints.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not public_limiter.allow(f"public:{client_ip}"):
        raise HTTPException(status_code=429, detail="Too many requests")

    now = datetime.now(timezone.utc)
    rows = (
        db.query(ConsentManager)
        .filter(ConsentManager.registration_status == "REGISTERED", ConsentManager.is_active.is_(True))
        .order_by(ConsentManager.id)
        .all()
    )
    out: list[ConsentManagerDisclosureOut] = []
    for cm in rows:
        d = cm.disclosures or {}
        out.append(
            ConsentManagerDisclosureOut(
                cm_ref=cm.cm_ref,
                name=cm.name,
                board_registration_number=cm.board_registration_number,
                registration_status=cm.registration_status,
                registered_at=cm.registered_at,
                website_url=cm.website_url,
                contact_email=cm.contact_email,
                promoters=d.get("promoters", []) or [],
                directors=d.get("directors", []) or [],
                key_managerial_personnel=d.get("key_managerial_personnel", []) or [],
                shareholders_above_two_percent=d.get("shareholders_above_two_percent", []) or [],
                data_blind=True,
                onboarded_fiduciaries=[f.source_app for f in cm.fiduciaries if f.status == "ACTIVE"],
                published_at=now,
            )
        )
    return out


@router.get("/artefact-schema", response_model=ArtefactSchemaOut)
def artefact_schema(request: Request):
    """The machine-readable descriptor of the artefact payload this API emits.

    `conformance_statement` is deliberately blunt about what has and has not
    been verified against ISO/IEC TS 27560:2023. An integrator is entitled to
    know that before wiring a conformance claim of their own on top of ours.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not public_limiter.allow(f"public:{client_ip}"):
        raise HTTPException(status_code=429, detail="Too many requests")
    return ArtefactSchemaOut(
        schema_version=cm_service.ARTEFACT_SCHEMA_VERSION,
        schema_reference=cm_service.ARTEFACT_SCHEMA_REFERENCE,
        modelled_on=cm_service.ARTEFACT_SCHEMA_MODELLED_ON,
        standard_status=cm_service.ARTEFACT_STANDARD_STATUS,
        conformance_statement=cm_service.ARTEFACT_CONFORMANCE_STATEMENT,
        normative_json_encoding=False,
        signature_alg="HMAC-SHA256",
        top_level_fields=[
            "schema_version",
            "record_id",
            "pii_principal_id",
            "pii_processing",
            "parties",
            "event",
            "x_consent360",
            "x_dpdp",
            "x_sharing",
        ],
        field_provenance={
            "schema_version": "ISO/IEC TS 27560:2023 Table 1 (6.3.3.2), REQUIRED - verified against the ISO text",
            "record_id": "ISO/IEC TS 27560:2023 Table 1 (6.3.3.3), REQUIRED - verified against the ISO text",
            "pii_principal_id": "ISO/IEC TS 27560:2023 Table 1 (6.3.3.4), REQUIRED - verified against the ISO text",
            "pii_processing.privacy_notice": "TS 27560 Table 2 (6.3.4.2), REQUIRED - verified",
            "pii_processing.language": "TS 27560 Table 2 (6.3.4.3), REQUIRED - verified",
            "pii_processing.purposes[]": "TS 27560 Table 2 (6.3.4.4-6.3.4.13) - field names verified; "
            "purpose, purpose_type, lawful_basis, pii_information, pii_controllers, collection_method, "
            "processing_method, storage_locations, retention_period",
            "pii_processing.purposes[].pii_information[]": "W3C DPV implementation guide for TS 27560 clause 6.3.5 - NOT verified against the ISO text",
            "parties[]": "W3C DPV implementation guide for TS 27560 clause 6.3.6 - NOT verified against the ISO text",
            "event": "W3C DPV implementation guide for TS 27560 clause 6.3.7 - NOT verified against the ISO text",
            "pii_processing / parties / event (container names)": "Consent360. TS 27560 6.3.2.2 names the six "
            "sections but expressly leaves their arrangement to the implementer (NOTE 2).",
            "x_consent360 / x_dpdp / x_sharing and every x_-prefixed key": "Consent360 extensions. No TS 27560 "
            "equivalent exists for DPDP's Consent Manager concept, Board-complaint links, grievance response "
            "period, or the DEPA/Account-Aggregator sharing semantics.",
        },
        field_notes={
            "pii_principal_id": (
                "A pseudonym, not a direct identifier - which is also what TS 27560 6.3.3.4 recommends "
                "(\"organizations should consider using measures to prevent identification of the PII "
                "principal through using mechanisms such as pseudonyms\"). It is stable for one "
                "(Consent Manager, principal) pair and different for every other Consent Manager. A "
                "fiduciary reading its own tenant additionally gets its own external id and a masked "
                "email under x_consent360; a Consent Manager never does. See the data-blind decision "
                "record in docs/compliance/CONSENT_MANAGER_DATA_BLIND_DECISION.md."
            ),
            "pii_processing.purposes[].storage_locations": (
                "REQUIRED by the standard but emitted empty: this platform does not yet record a "
                "per-purpose storage location, and emitting a guess in a required field would be worse "
                "than an explicit absence."
            ),
            "pii_processing.purposes[].lawful_basis": (
                "Optional in TS 27560, which assumes consent. Always populated here: DPDP s.4 requires a "
                "named gateway, so the value is CONSENT (s.6) or one of S7_A..S7_I."
            ),
            "signature": (
                "TS 27560 has no signature field. Clause 6.2.2.1 requires records to be kept so their "
                "integrity is assured over time and 6.2.3.6 requires receipts to be verifiable, but "
                "integrity controls are a `may` (6.2.3.3). Each artefact version is separately signed "
                "here with HMAC-SHA256 over the canonical payload hash - see "
                "GET /consent-manager/artefacts/{artefact_ref} -> events[].signature - which satisfies "
                "those controls rather than implementing a named standard field."
            ),
            "versioning": (
                "Two independent version numbers. schema_version identifies the SHAPE of the payload; "
                "x_consent360.record_version (and the artefact_version on each event) identifies the "
                "revision of this particular record. Every revision is retained and separately signed."
            ),
        },
    )


# --------------------------------------------------------------------------- #
#  The artefact API (tenant-bound API key)
# --------------------------------------------------------------------------- #

def _broker(
    db: Session,
    request: Request,
    resolved_key: ResolvedApiKey,
    source_app: Optional[str],
    sample: dict,
) -> cm_service.BrokerContext:
    """Rate-limit, then resolve the one fiduciary this caller may act for."""
    client_ip = request.client.host if request.client else "unknown"
    if not cm_service.artefact_limiter.allow(
        f"artefact:{resolved_key.tenant_code or 'legacy'}:{client_ip}"
    ):
        raise HTTPException(status_code=429, detail="Too many consent-artefact requests")
    ctx = cm_service.resolve_broker_context(db, resolved_key, requested_source_app=source_app)
    sample["tenant_id"] = ctx.fiduciary_tenant_id
    sample["consent_manager_id"] = ctx.consent_manager.id if ctx.consent_manager else None
    return ctx


@router.post("/artefacts", response_model=ArtefactOut)
def create_artefact(
    payload: ArtefactCreateIn,
    request: Request,
    db: Session = Depends(get_db),
    resolved_key: ResolvedApiKey = Depends(verify_integration_key),
):
    """Give consent: create a new consent artefact over one or more purposes."""
    request_id = request.headers.get("X-Request-ID") or get_request_id()
    with cm_service.measure(
        db, endpoint="POST /consent-manager/artefacts", method="POST", request_id=request_id
    ) as sample:
        require_scope(resolved_key, cm_service.SCOPE_ARTEFACT_WRITE)
        ctx = _broker(db, request, resolved_key, payload.source_app, sample)
        if ctx.is_consent_manager and not payload.principal_action_reference:
            raise HTTPException(
                status_code=422,
                detail=(
                    "principal_action_reference is required for a Consent Manager: it records the "
                    "affirmative action you are attesting the principal took in your interface, and "
                    "is written onto the consent evidence for every record this call creates."
                ),
            )
        customer = cm_service.resolve_principal(
            db, ctx, customer_id=payload.customer_id, email=payload.email
        )
        artefact = cm_service.create_artefact(
            db,
            ctx,
            customer,
            purpose_codes=payload.purpose_codes,
            expires_in_days=payload.expires_in_days,
            language=payload.language,
            reference=payload.principal_action_reference,
            reason=payload.reason,
            request_id=request_id,
        )
        return _artefact_out(db, artefact, ctx)


@router.get("/artefacts", response_model=ArtefactListOut)
def list_artefacts(
    request: Request,
    source_app: Optional[str] = Query(default=None),
    principal_ref: Optional[str] = Query(default=None),
    customer_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None, pattern=r"^(ACTIVE|PARTIAL|WITHDRAWN|EXPIRED)$"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    resolved_key: ResolvedApiKey = Depends(verify_integration_key),
):
    """Review: list the artefacts this caller is authorised to see.

    A Consent Manager sees only the artefacts it brokered, for the one
    fiduciary it named. `customer_id` is accepted only from a fiduciary
    reading its own tenant - a Consent Manager filters by `principal_ref`,
    the pseudonym it already holds.
    """
    request_id = request.headers.get("X-Request-ID") or get_request_id()
    with cm_service.measure(
        db, endpoint="GET /consent-manager/artefacts", method="GET", request_id=request_id
    ) as sample:
        require_scope(resolved_key, cm_service.SCOPE_ARTEFACT_READ)
        ctx = _broker(db, request, resolved_key, source_app, sample)

        q = cm_service.artefact_query(db, ctx)
        if principal_ref:
            q = q.filter(ConsentArtefact.principal_ref == principal_ref)
        if customer_id:
            if ctx.data_blind:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "A Consent Manager filters by principal_ref, not by the fiduciary's own "
                        "customer id (see the data-blind design decision)."
                    ),
                )
            customer = resolve_customer(
                db, source_app=ctx.fiduciary_source_app, external_id=customer_id
            )
            if not customer:
                return ArtefactListOut(total=0, artefacts=[])
            q = q.filter(ConsentArtefact.customer_id == customer.id)
        if status:
            q = q.filter(ConsentArtefact.status == status)

        total = q.count()
        rows = q.order_by(ConsentArtefact.id.desc()).limit(limit).all()
        return ArtefactListOut(
            total=total, artefacts=[_artefact_out(db, a, ctx) for a in rows]
        )


@router.get("/artefacts/{artefact_ref}", response_model=ArtefactOut)
def get_artefact(
    artefact_ref: str,
    request: Request,
    source_app: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    resolved_key: ResolvedApiKey = Depends(verify_integration_key),
):
    """Review one artefact, with every signed version it has ever had."""
    request_id = request.headers.get("X-Request-ID") or get_request_id()
    with cm_service.measure(
        db,
        endpoint="GET /consent-manager/artefacts/{artefact_ref}",
        method="GET",
        request_id=request_id,
    ) as sample:
        require_scope(resolved_key, cm_service.SCOPE_ARTEFACT_READ)
        ctx = _broker(db, request, resolved_key, source_app, sample)
        artefact = cm_service.get_artefact_or_404(db, ctx, artefact_ref)
        return _artefact_out(db, artefact, ctx)


@router.post("/artefacts/{artefact_ref}/update", response_model=ArtefactOut)
def manage_artefact(
    artefact_ref: str,
    payload: ArtefactUpdateIn,
    request: Request,
    source_app: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    resolved_key: ResolvedApiKey = Depends(verify_integration_key),
):
    """Manage: revise the purposes an artefact covers.

    A POST rather than a PATCH because it is not a partial edit of a stored
    representation - it grants and withdraws real consents and appends a new
    signed artefact version.
    """
    request_id = request.headers.get("X-Request-ID") or get_request_id()
    with cm_service.measure(
        db,
        endpoint="POST /consent-manager/artefacts/{artefact_ref}/update",
        method="POST",
        request_id=request_id,
    ) as sample:
        require_scope(resolved_key, cm_service.SCOPE_ARTEFACT_WRITE)
        ctx = _broker(db, request, resolved_key, source_app, sample)
        if ctx.is_consent_manager and not payload.principal_action_reference:
            raise HTTPException(
                status_code=422,
                detail="principal_action_reference is required for a Consent Manager.",
            )
        artefact = cm_service.get_artefact_or_404(db, ctx, artefact_ref)
        customer = cm_service.resolve_principal(db, ctx, principal_ref=artefact.principal_ref)
        artefact = cm_service.update_artefact(
            db,
            ctx,
            artefact,
            customer,
            purpose_codes=payload.purpose_codes,
            expires_in_days=payload.expires_in_days,
            reference=payload.principal_action_reference,
            reason=payload.reason,
            request_id=request_id,
        )
        return _artefact_out(db, artefact, ctx)


@router.post("/artefacts/{artefact_ref}/withdraw", response_model=ArtefactOut)
def withdraw_artefact(
    artefact_ref: str,
    payload: ArtefactWithdrawIn,
    request: Request,
    source_app: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    resolved_key: ResolvedApiKey = Depends(verify_integration_key),
):
    """Withdraw an artefact, wholly or for named purposes only.

    Withdrawal runs through `app/services/consent.py::withdraw_consent` for
    every linked consent, so the s.6(6) processor cease-processing propagation,
    the withdrawal-confirmation notification, the evidence row and the audit
    entry all happen exactly as they do for a withdrawal made anywhere else.
    """
    request_id = request.headers.get("X-Request-ID") or get_request_id()
    with cm_service.measure(
        db,
        endpoint="POST /consent-manager/artefacts/{artefact_ref}/withdraw",
        method="POST",
        request_id=request_id,
    ) as sample:
        require_scope(resolved_key, cm_service.SCOPE_ARTEFACT_WRITE)
        ctx = _broker(db, request, resolved_key, source_app, sample)
        artefact = cm_service.get_artefact_or_404(db, ctx, artefact_ref)
        customer = cm_service.resolve_principal(db, ctx, principal_ref=artefact.principal_ref)
        artefact = cm_service.withdraw_artefact(
            db,
            ctx,
            artefact,
            customer,
            purpose_codes=payload.purpose_codes,
            reference=payload.principal_action_reference,
            reason=payload.reason,
            request_id=request_id,
        )
        return _artefact_out(db, artefact, ctx)


@router.get("/fiduciaries", response_model=list[FiduciaryOnboardingOut])
def my_onboarded_fiduciaries(
    request: Request,
    db: Session = Depends(get_db),
    resolved_key: ResolvedApiKey = Depends(verify_integration_key),
):
    """The fiduciaries the calling Consent Manager is onboarded to act for -
    the list it needs before it can name a `source_app` on any other call."""
    request_id = request.headers.get("X-Request-ID") or get_request_id()
    with cm_service.measure(
        db, endpoint="GET /consent-manager/fiduciaries", method="GET", request_id=request_id
    ) as sample:
        require_scope(resolved_key, cm_service.SCOPE_ARTEFACT_READ)
        if resolved_key.tenant_id is None:
            raise HTTPException(status_code=403, detail="This endpoint requires a tenant-bound API key")
        sample["tenant_id"] = resolved_key.tenant_id
        cm = (
            db.query(ConsentManager)
            .filter(ConsentManager.tenant_id == resolved_key.tenant_id)
            .first()
        )
        if not cm:
            raise HTTPException(
                status_code=403, detail="This API key does not belong to a registered Consent Manager"
            )
        sample["consent_manager_id"] = cm.id
        return [
            FiduciaryOnboardingOut.model_validate(f)
            for f in cm.fiduciaries
            if f.status == "ACTIVE"
        ]


# --------------------------------------------------------------------------- #
#  CM-06 / K-42 / K-43
# --------------------------------------------------------------------------- #

_RETRIEVAL_ENDPOINTS = (
    "GET /consent-manager/artefacts",
    "GET /consent-manager/artefacts/{artefact_ref}",
)


@router.get("/metrics", response_model=ConsentManagerMetricsOut)
def metrics(
    window_hours: int = Query(default=24, ge=1, le=720),
    db: Session = Depends(get_db),
    _user: User = Depends(require_permission(PERM_AUDIT_VIEW)),
):
    """R.4 / CM-06: availability and latency of the Consent Manager API
    (K-42) and record-retrieval latency (K-43).

    Computed from `consent_manager_api_calls`, one row per call to this
    router's artefact endpoints and to `POST /decisions/evaluate`. Availability
    counts a *server* failure against us and a client error (a 4xx: an
    unauthorised CM, an unknown principal, a rate limit) as a successful
    service response, which is the standard reading of an availability SLO -
    the API answered correctly, the caller asked wrongly. Both numbers are
    reported so the distinction is visible rather than buried.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    rows = (
        db.query(ConsentManagerApiCall)
        .filter(ConsentManagerApiCall.occurred_at >= since)
        .all()
    )
    total = len(rows)
    server_errors = sum(1 for r in rows if r.outcome == "SERVER_ERROR")
    client_errors = sum(1 for r in rows if r.outcome == "CLIENT_ERROR")
    durations = [r.duration_ms for r in rows]
    retrieval = [r.duration_ms for r in rows if r.endpoint in _RETRIEVAL_ENDPOINTS]

    per_endpoint: list[dict] = []
    endpoints = sorted({r.endpoint for r in rows})
    for endpoint in endpoints:
        subset = [r for r in rows if r.endpoint == endpoint]
        per_endpoint.append(
            {
                "endpoint": endpoint,
                "calls": len(subset),
                "server_errors": sum(1 for r in subset if r.outcome == "SERVER_ERROR"),
                "client_errors": sum(1 for r in subset if r.outcome == "CLIENT_ERROR"),
                "p95_ms": cm_service.percentile([r.duration_ms for r in subset], 95),
            }
        )

    artefact_counts = dict(
        db.query(ConsentArtefact.status, func.count(ConsentArtefact.id))
        .group_by(ConsentArtefact.status)
        .all()
    )

    return ConsentManagerMetricsOut(
        window_hours=window_hours,
        total_calls=total,
        # K-42: None, not 100.0/0.0, when no call has been sampled in the
        # window. Zero calls is not "100% available" any more than it is
        # "0% error rate" - both are the same false-assurance pattern,
        # merely inverted for error_rate_pct, and both are undefined here.
        availability_pct=round(((total - server_errors) / total) * 100, 3) if total else None,
        error_rate_pct=round(((server_errors + client_errors) / total) * 100, 3) if total else None,
        latency_ms_p50=cm_service.percentile(durations, 50),
        latency_ms_p95=cm_service.percentile(durations, 95),
        latency_ms_max=max(durations) if durations else None,
        record_retrieval_ms_p95=cm_service.percentile(retrieval, 95),
        onboarded_fiduciary_count=(
            db.query(func.count(ConsentManagerFiduciary.id))
            .filter(ConsentManagerFiduciary.status == "ACTIVE")
            .scalar()
            or 0
        ),
        registered_consent_manager_count=(
            db.query(func.count(ConsentManager.id))
            .filter(
                ConsentManager.registration_status == "REGISTERED",
                ConsentManager.is_active.is_(True),
            )
            .scalar()
            or 0
        ),
        artefacts_total=sum(artefact_counts.values()),
        artefacts_active=artefact_counts.get("ACTIVE", 0) + artefact_counts.get("PARTIAL", 0),
        artefacts_withdrawn=artefact_counts.get("WITHDRAWN", 0),
        per_endpoint=per_endpoint,
    )
