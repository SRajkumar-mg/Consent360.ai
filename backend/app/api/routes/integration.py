from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.api.deps import (
    ResolvedApiKey,
    bearer_scheme,
    get_current_user,
    get_customer_from_context,
    require_permission,
    require_scope,
    verify_integration_key,
)
from app.api.routes.consents import _consent_out
from app.core.api_keys import SCOPE_FIDUCIARY_ASSERT, SCOPE_INTEGRATION_WRITE
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rbac import PERM_CONTEXT_USE
from app.core.utils import context_limiter
from app.models.entities import (
    ApiKey,
    AuditLog,
    Consent,
    ConsentContext,
    ConsentHistory,
    Customer,
    DataCategory,
    Organization,
    ProcessingActivity,
    Purpose,
)
from app.schemas.schemas import (
    AuditEventOut,
    ConsentHistoryOut,
    CustomerContextIn,
    CustomerContextOut,
    CustomerPortalOut,
    MessageOut,
)
from app.services import consent as consent_service
from app.services.audit import log_audit
from app.services.context import create_context_for_customer

settings = get_settings()
router = APIRouter(prefix="/consent", tags=["integration"])


def _resolve_fiduciary_assertion(resolved_key: ResolvedApiKey, requested: bool | None) -> bool:
    """Decide whether this context should be created already-verified
    (R3-05's "fiduciary-asserted" handoff) instead of requiring the
    principal to complete email-OTP verification in the self-service
    portal.

    Deliberately opt-in per tenant (the calling key must carry
    SCOPE_FIDUCIARY_ASSERT - see app/core/api_keys.py) and, within that,
    overridable per request:
      - ``requested is True``: the caller is explicitly asking for it. If
        the key isn't scoped for it this is a loud 403, never a silent
        fall-back to OTP - a caller that thinks it is asserting identity
        must not discover otherwise only by an auditor testing the gap.
      - ``requested is False``: an explicit opt-out, honoured even for a
        key that has the scope (e.g. an anonymous/guest call from a tenant
        that otherwise wants assertion).
      - ``requested is None`` (the field omitted, as every demo frontend's
        request body does today): defer entirely to the tenant's own
        setting - assert automatically when the key has the scope, so a
        site like the job portal (no OTP-verification UI of its own) works
        without needing a frontend change, and do nothing for every other
        tenant's key, which was never granted the scope.
    """
    has_scope = SCOPE_FIDUCIARY_ASSERT in (resolved_key.scopes or [])
    if requested is True:
        if not has_scope:
            raise HTTPException(
                status_code=403,
                detail=f"This API key is not scoped for '{SCOPE_FIDUCIARY_ASSERT}'",
            )
        return True
    if requested is False:
        return False
    return has_scope


def _reject_legacy_key_claiming_an_owned_tenant(db: Session, resolved_key: ResolvedApiKey, source_app: str | None) -> None:
    """The legacy, unbound INTEGRATION_API_KEY (``resolved_key.tenant_code is
    None``) carries no tenant identity of its own - the caller names
    whatever ``source_app`` it likes. `create_context_for_customer`'s own
    tenant-scoped lookup (see its docstring) then trusts that string as if
    it WERE an authenticated tenant, so a legacy caller who simply names a
    real tenant's own ``source_app`` matches (and, on the update path,
    overwrites) that tenant's actual Customer row - despite never having
    proven it owns that identity. Scoping every legacy call to one fixed
    bucket would break existing, intentional legacy behaviour (a legacy
    caller choosing its own fresh source_app label, e.g. `LEGACY_APP`, is
    fine and is exercised by test_api_keys.py::test_legacy_key_still_works_by_default),
    so instead this targets exactly the unsafe case: a ``source_app`` that
    ALREADY belongs to a tenant with its own real, active API key - i.e. a
    tenant that HAS proven ownership, the precise thing the legacy key
    cannot do. Once any tenant issues itself a real key (see
    seed_api_keys.py, the intended migration path off this flag), the
    shared legacy key is permanently locked out of that source_app,
    matching or creating, from then on.
    """
    if resolved_key.tenant_code is not None or not source_app:
        return
    org = db.query(Organization).filter(Organization.code == source_app).first()
    if not org:
        # R3 "squat-then-claim": about to auto-provision (via
        # resolve_tenant_id, inside create_context_for_customer) a brand-new
        # Organization for a name nobody has proven ownership of. Not
        # rejected outright - a legacy caller legitimately naming a fresh
        # tenant is the key's whole reason for existing (see this
        # function's own docstring and
        # test_legacy_key_still_works_for_a_source_app_nobody_owns) - but
        # logged loudly rather than left completely silent, so an admin
        # reviewing /audit can catch a squatted name BEFORE a real key is
        # later issued for it. See organizations.py::create_api_key's own
        # pre-existing-data check for the other half of this mitigation:
        # issuing a key for an org that already has customers attached
        # requires an explicit acknowledgement rather than silently
        # inheriting whatever was planted under it.
        from app.services.audit import log_audit

        log_audit(
            db, "TENANT_AUTO_PROVISIONED_VIA_LEGACY_KEY", actor_username="integration",
            actor_type="SYSTEM", source_app=source_app,
            reason=(
                f"Legacy integration key is about to create a customer under a brand-new, "
                f"unclaimed source_app '{source_app}' - no Organization or API key exists for it yet."
            ),
            commit=False,
        )
        return
    owned = db.query(ApiKey).filter(ApiKey.tenant_id == org.id, ApiKey.revoked_at.is_(None)).first()
    if owned:
        raise HTTPException(
            status_code=403,
            detail="source_app belongs to a tenant with its own API key; the shared legacy key cannot act as it",
        )


@router.post("/customer-context", response_model=CustomerContextOut)
def create_customer_context(
    payload: CustomerContextIn,
    request: Request,
    db: Session = Depends(get_db),
    resolved_key: ResolvedApiKey = Depends(verify_integration_key),
):
    client_ip = request.client.host if request.client else "unknown"
    if not context_limiter.allow(f"ctx:{client_ip}"):
        raise HTTPException(status_code=429, detail="Too many context requests")
    if resolved_key.tenant_code and payload.source_app and payload.source_app != resolved_key.tenant_code:
        raise HTTPException(status_code=403, detail="source_app does not match the tenant bound to this API key")
    require_scope(resolved_key, SCOPE_INTEGRATION_WRITE)
    fiduciary_asserted = _resolve_fiduciary_assertion(resolved_key, payload.fiduciary_asserted)
    effective_source_app = resolved_key.tenant_code or payload.source_app or "EXTERNAL_APP"
    # Checked against the fully-resolved value (not just payload.source_app)
    # so an omitted source_app defaulting to "EXTERNAL_APP" is covered too,
    # not just an explicit one.
    _reject_legacy_key_claiming_an_owned_tenant(db, resolved_key, effective_source_app)
    request_id = request.headers.get("X-Request-ID")
    return create_context_for_customer(
        db,
        customer_id=payload.customer_id,
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        status=payload.status,
        source_app=effective_source_app,
        created_by="integration",
        request_id=request_id,
        fiduciary_asserted=fiduciary_asserted,
    )


def _ensure_source_consent_matrix(db: Session, customer: Customer, source_app: str) -> None:
    """Materialize the consent matrix for a customer, owned by the website/app source.

    - A row already recorded through this source is reused.
    - A placeholder row ("" / "SYSTEM" / "UI" - created by admin tooling, not by a real
      website) is adopted into this source so a fresh registration always sees its matrix.
    - A row owned by a DIFFERENT real source is left untouched and a NEW independent row
      is created for this source (per-website consent records).
    """
    purposes = db.query(Purpose).filter(Purpose.is_active.is_(True)).all()
    for purpose in purposes:
        pv = consent_service.get_current_purpose_version(purpose)
        cat_ids = pv.data_category_ids or []
        act_ids = pv.processing_activity_ids or []
        categories = db.query(DataCategory).filter(DataCategory.id.in_(cat_ids)).all() if cat_ids else []
        activities = db.query(ProcessingActivity).filter(ProcessingActivity.id.in_(act_ids)).all() if act_ids else []
        for dc in categories:
            for pa in activities:
                same_source = (
                    db.query(Consent)
                    .filter(
                        Consent.customer_id == customer.id,
                        Consent.purpose_id == purpose.id,
                        Consent.data_category_id == dc.id,
                        Consent.processing_activity_id == pa.id,
                        Consent.source_app == source_app,
                    )
                    .first()
                )
                if same_source:
                    continue
                existing = (
                    db.query(Consent)
                    .filter(
                        Consent.customer_id == customer.id,
                        Consent.purpose_id == purpose.id,
                        Consent.data_category_id == dc.id,
                        Consent.processing_activity_id == pa.id,
                    )
                    .first()
                )
                if existing and existing.source_app in ("", "SYSTEM", "UI"):
                    existing.source_app = source_app
                    continue
                consent_service.get_or_create_consent(
                    db, customer, purpose, dc, pa,
                    actor_username="integration", source_app=source_app, exact_source=True,
                )


@router.get("/context/consume/{context_token}", response_model=CustomerPortalOut)
def consume_context(context_token: str, db: Session = Depends(get_db)):
    """Data-subject portal: the context token itself is the credential (no platform auth).

    Returns ONLY the data belonging to this customer AND recorded through this source
    (consents, their history, and related audit events). Everything else is hidden.
    """
    from app.api.deps import verify_context_token

    context = db.query(ConsentContext).filter(ConsentContext.token == context_token).first()
    if not context:
        raise HTTPException(status_code=401, detail="Invalid consent context token")
    # verify_context_token, not a bare decode_token: this call site checked
    # only the signature and `iss`, never `ctx`/`type`/`aud`. It was not
    # exploitable because the exact `consent_contexts.token` row match above
    # is a second, independent control - but that is a coincidence of the
    # storage design, not intent, and D-10 (hashing stored context tokens)
    # changes that lookup. A staff access token or an MFA-pending token
    # must fail HERE on its own claims, not because it happens not to
    # appear in a table.
    payload = verify_context_token(context_token)
    if not context.is_active:
        raise HTTPException(status_code=401, detail="Consent context has already been consumed")
    if context.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Consent context has expired")
    # This is the THIRD consumer of a raw context token/row (alongside
    # app/api/deps.py::get_customer_from_context and
    # app/api/routes/portal.py::_resolve_customer_and_context) and was
    # missing both of the independent tenant checks those apply. Reusing
    # get_customer_from_context itself (not re-deriving the same logic)
    # checks the token's own signed `source_app` claim against the
    # resolved customer; the second, independent check below additionally
    # verifies the PERSISTED ConsentContext.source_app row agrees, exactly
    # mirroring portal.py's second layer.
    customer = get_customer_from_context(db, payload)
    if customer.source_app != context.source_app:
        raise HTTPException(status_code=404, detail="Customer not found")

    source_app = context.source_app
    _ensure_source_consent_matrix(db, customer, source_app)

    consents = (
        db.query(Consent)
        .filter(Consent.customer_id == customer.id, Consent.source_app == source_app)
        .order_by(Consent.purpose_id.asc(), Consent.data_category_id.asc(), Consent.processing_activity_id.asc())
        .all()
    )
    consent_ids = [c.id for c in consents]
    history = (
        db.query(ConsentHistory)
        .filter(ConsentHistory.consent_id.in_(consent_ids))
        .order_by(ConsentHistory.created_at.desc())
        .all()
        if consent_ids
        else []
    )
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.customer_id == customer.id, AuditLog.source_app == source_app)
        .order_by(AuditLog.created_at.desc())
        .limit(100)
        .all()
    )

    status_counts: dict[str, int] = {}
    for c in consents:
        status_counts[c.status] = status_counts.get(c.status, 0) + 1

    context.is_active = False
    context.consumed_at = datetime.now(timezone.utc)
    log_audit(db, "CONTEXT_CONSUMED", actor_username="integration", source_app=source_app,
              customer_id=customer.id, customer_external_id=customer.external_id,
              reason="Consent context consumed - opening customer portal",
              request_id=context.request_id,
              metadata={"context_id": context.id})
    db.commit()

    return CustomerPortalOut(
        customer=customer,
        source_app=source_app,
        status_counts=status_counts,
        consents=[_consent_out(c) for c in consents],
        history=[ConsentHistoryOut.model_validate(h) for h in history],
        audit=[AuditEventOut.model_validate(e) for e in audit],
    )


@router.get("/context/status/{context_token}", response_model=MessageOut)
def context_status(
    context_token: str,
    db: Session = Depends(get_db),
    x_api_key: str | None = Header(default=None),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    # R3-11: this is documented (see the docstring below and the SDK's
    # client.py) as part of the "X-API-Key protected" integration surface,
    # but it used to accept ONLY a staff bearer token — no third-party
    # integration caller (the SDK's only audience for this method) holds
    # one of those, so every SDK call to it 401'd. It now accepts EITHER
    # credential: a tenant-bound (or legacy) integration API key, scoped
    # exactly like every other integration.write call, OR the pre-existing
    # staff bearer token + `context.use` permission, kept for the admin
    # console and unchanged for tests/test_jobs.py's coverage of the
    # token-hashing interaction below.
    resolved_key: ResolvedApiKey | None = None
    if x_api_key:
        resolved_key = verify_integration_key(x_api_key=x_api_key, db=db)
        require_scope(resolved_key, SCOPE_INTEGRATION_WRITE)
    elif credentials is not None:
        user = get_current_user(credentials=credentials, db=db)
        require_permission(PERM_CONTEXT_USE)(user=user)
    else:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # `context_token_cleanup_job` hashes the stored token of any consumed or
    # expired context (see app/jobs/context_token_cleanup_job.py), so a
    # caller presenting the original raw token -- this is part of the
    # published SDK surface, callers only ever know the raw token -- must be
    # matched against either form. `hash_token` is the exact same digest the
    # cleanup job computes, so the two always agree.
    from app.jobs.context_token_cleanup_job import hash_token

    context = (
        db.query(ConsentContext)
        .filter(ConsentContext.token.in_([context_token, hash_token(context_token)]))
        .first()
    )
    if not context:
        raise HTTPException(status_code=404, detail="Context not found")
    # A tenant-bound API key only ever answers for contexts it (or the
    # tenant it belongs to) created — matches the "wrong tenant is
    # indistinguishable from not found" idiom used by
    # crm.py::purge_customer_by_email, rather than confirming a token exists
    # for some OTHER tenant. The legacy, unbound key (tenant_code is None)
    # proves no tenant identity, so it keeps its existing unrestricted reach,
    # same as elsewhere in this file.
    if resolved_key is not None and resolved_key.tenant_code is not None and context.source_app != resolved_key.tenant_code:
        raise HTTPException(status_code=404, detail="Context not found")
    if context.consumed_at:
        return MessageOut(message="CONSUMED")
    if context.expires_at < datetime.now(timezone.utc):
        return MessageOut(message="EXPIRED")
    return MessageOut(message="VALID")
