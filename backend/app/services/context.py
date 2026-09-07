"""Shared consent-context creation used by the integration API and the CRM portal.

Both entry points identify-or-create the consent platform's Customer reference
and mint a short-lived context token that lets the customer manage their own
consents in the customer portal without a staff account.
"""

import hashlib
import re
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.encryption import hmac_digest
from app.core.security import create_context_token
from app.models.entities import ConsentContext, Customer
from app.schemas.schemas import CustomerContextOut
from app.services.audit import log_audit
from app.services.tenancy import external_id_owned_by_another_tenant, resolve_customer, resolve_tenant_id

settings = get_settings()

# R1-12/B-04: matches the synthetic placeholder email a calling site
# fabricates for an anonymous visitor who gave no real identifier - e.g.
# `visitor-${Date.now()}@careerhub.local` in portal/job-portal/src/useConsent.ts.
# A fresh timestamp on every page load means accepting this would create one
# throwaway, never-reused Customer row per anonymous visit forever - exactly
# the "customer records for anonymous visitors" gap the workbook names.
_SYNTHETIC_VISITOR_EMAIL_RE = re.compile(r"^visitor-\d+@", re.IGNORECASE)


def _is_synthetic_visitor_email(email: str | None) -> bool:
    return bool(email) and bool(_SYNTHETIC_VISITOR_EMAIL_RE.match(email.strip()))


def assert_identifiable_principal(
    *,
    email: str | None,
    phone: str | None = None,
    customer_id: str | None = None,
) -> None:
    """R1-12/B-04: refuse to persist a data-principal record for an identity the
    calling site fabricated for an anonymous visitor.

    THE ONE COPY OF THIS RULE. It used to live inline in
    ``create_context_for_customer`` below, which meant it only ever fired for
    the two callers that go through that function -
    ``POST /consent/customer-context`` and ``crm.py``'s ``.../consent-context``.
    ``POST /crm/login`` (``routes/crm_directory.py::crm_login``) writes the
    ``CrmCustomer`` and platform ``Customer`` rows directly and never mints a
    context, so it sailed straight past the guard - and it is the ONE
    customer-creating endpoint that takes no credential at all. Reproduced:
    ``POST /crm/login {"email": "visitor-1788531480@careerhub.local"}`` returned
    200 and created CrmCustomer id 29 / Customer id 47, while the very same
    address on ``POST /consent/customer-context`` was refused 422.

    So the predicate AND the refusal both live here, and every path that first
    creates a customer record calls this one function. Restating the rule at
    each call site is what let the two copies diverge in the first place, and a
    drifted copy of a rule like this is always the permissive one.

    Why a record is refused rather than quietly created: a consent attributed
    to ``visitor-<timestamp>@...`` names nobody. It cannot be honoured,
    withdrawn, or produced as evidence that any identifiable person agreed
    (DPDP s.6(1)), and a fresh timestamp per page load means one throwaway,
    never-reused row per anonymous visit forever.

    An explicit ``customer_id`` or a ``phone`` exempts the caller: either is a
    real identifier the site is asserting, and the placeholder email is then
    only a display value alongside it.
    """
    if customer_id or phone is not None:
        return
    if not _is_synthetic_visitor_email(email):
        return
    raise HTTPException(
        status_code=422,
        detail=(
            "This looks like a synthesised anonymous-visitor identity, not a real one - "
            "a customer record is not created for an unidentified visitor (DPDP s.6(1) "
            "minimisation, gap B-04). Provide a real email or phone, an explicit "
            "customer_id, or skip context creation entirely for anonymous browsing."
        ),
    )


def derive_customer_id(
    *,
    customer_id: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    source_app: str | None = None,
) -> str:
    """Resolve a stable external customer id when the calling app has no id to send.

    ``source_app`` is folded into the hash so that two different tenants
    deriving an id for the *same* email/phone land on two different,
    non-colliding external ids (``external_id`` / ``external_id_search`` are
    globally unique on ``customers`` - see create_context_for_customer's
    tenant-scoping note). It is optional and only affects the email/phone
    derivation path - an explicit ``customer_id`` is always returned as-is.
    Callers that deliberately want ONE shared identity across a family of
    source_apps (crm.py / crm_directory.py's CRM+Codex+SkillLearn shared
    directory - see the module comment in app/api/routes/crm.py) must keep
    omitting ``source_app`` so they keep deriving today's un-salted id.
    """
    if customer_id:
        return customer_id
    seed = None
    if email:
        seed = "email:" + email.strip().lower()
    elif phone:
        seed = "phone:" + phone.strip()
    if not seed:
        raise HTTPException(status_code=422, detail="customer_id, email or phone is required")
    if source_app:
        seed = f"tenant:{source_app}|{seed}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10].upper()
    return f"CUST-{digest}"


def create_context_for_customer(
    db: Session,
    *,
    name: str,
    email: str | None = None,
    phone: str | None = None,
    status: str | None = None,
    source_app: str = "EXTERNAL_APP",
    created_by: str = "integration",
    customer_id: str | None = None,
    request_id: str | None = None,
    fiduciary_asserted: bool = False,
) -> CustomerContextOut:
    """Identify (or create) the consent-platform Customer and mint a context token.

    Lookup order: explicit ``customer_id`` -> email match (case-insensitive) ->
    derived external id -> create. Matching by email first reuses the existing
    Customer reference instead of creating a duplicate for the same person.

    Every lookup below is scoped to ``source_app`` (the calling tenant) - see
    app/services/tenancy.py's "source_app is the tenant key" note and
    app/api/routes/customers.py::get_customer's identical
    scope-mismatch-is-a-404 idiom. A Customer belonging to a DIFFERENT tenant
    must be completely invisible here: matched by email or by a derived id,
    it is treated as "does not exist" and a brand-new Customer is created for
    the calling tenant, rather than either (a) being reused, which would
    hand the calling tenant somebody else's real identity and consent
    history (the R3-05/fiduciary cross-tenant breach this closes), or (b)
    the whole request being rejected, which would permanently block a
    tenant from ever onboarding a customer whose email happens to coincide
    with an unrelated customer elsewhere - overlapping personal emails
    across unrelated tenants is completely ordinary. To keep two such
    per-tenant Customer rows from colliding on ``customers.external_id``'s
    table-wide uniqueness constraint, the id derived from email/phone (used
    only when the caller supplies no explicit ``customer_id``) is itself
    salted with ``source_app`` - see derive_customer_id.

    An explicit ``customer_id`` is different: it is a literal identity the
    calling tenant is asserting, not something derived, so it cannot be
    silently re-scoped. If that literal id is already claimed by a
    DIFFERENT tenant's Customer it is a genuine naming collision - not a
    duplicate to work around and not something a second row can be created
    for (external_id stays globally unique) - so the caller gets a loud 409
    rather than either an IntegrityError or, worse, silently resolving to
    someone else's customer.

    R1-12/B-04: a caller with no explicit ``customer_id`` and no ``phone``
    whose ``email`` looks like a synthesised anonymous-visitor placeholder is
    refused outright rather than given a fresh Customer row - DPDP s.6(1)
    minimisation means a record is not persisted for someone who has not
    actually identified themselves. The rule itself lives in
    ``assert_identifiable_principal`` above, shared with ``crm_login``.
    """
    assert_identifiable_principal(email=email, phone=phone, customer_id=customer_id)
    if customer_id:
        customer = resolve_customer(db, source_app=source_app, external_id=customer_id)
        if not customer and external_id_owned_by_another_tenant(db, customer_id, source_app):
            raise HTTPException(
                status_code=409,
                detail="customer_id is already in use by a different tenant",
            )
        external_id = customer_id
    else:
        external_id = derive_customer_id(email=email, phone=phone, source_app=source_app)
        customer = None
        if email:
            customer = resolve_customer(db, source_app=source_app, email=email)
        if not customer:
            customer = resolve_customer(db, source_app=source_app, external_id=external_id)
    if not customer:
        customer = Customer(
            external_id=external_id,
            external_id_search=hmac_digest(external_id),
            name=name,
            email=email or "",
            email_search=hmac_digest(email) if email else None,
            phone=phone or "",
            status=status or "ACTIVE",
            source_app=source_app,
            tenant_id=resolve_tenant_id(db, source_app),
        )
        db.add(customer)
        db.flush()
        log_audit(db, "CUSTOMER_CREATED", actor_username=created_by, source_app=source_app,
                  customer_id=customer.id, customer_external_id=customer.external_id,
                  reason="Customer reference created from integration context")
    else:
        if name:
            customer.name = name
        if email:
            customer.email = email
            customer.email_search = hmac_digest(email)
        if phone:
            customer.phone = phone
        if status:
            customer.status = status
        log_audit(db, "CUSTOMER_UPDATED", actor_username=created_by, source_app=source_app,
                  customer_id=customer.id, customer_external_id=customer.external_id,
                  reason="Customer context refreshed from integration")

    # R1-06/G-02: a context is minted when this principal logs into a client
    # site or a fiduciary hands her off to the consent portal - i.e. she has
    # "approached the Data Fiduciary for the performance of the specified
    # purpose", which is where DPDP Rules 2025 R.8(1) read with the Third
    # Schedule starts its three-year clock. Recorded here rather than on the
    # `customers` row's own `updated_at`, which also moves for writes the
    # principal had nothing to do with.
    from app.services.erasure import touch_last_interaction

    touch_last_interaction(db, customer, channel="context")

    token = create_context_token(customer.id, source_app, request_id)
    now = datetime.now(timezone.utc)
    context = ConsentContext(
        customer_id=customer.id,
        token=token,
        source_app=source_app,
        request_id=request_id,
        created_by=created_by,
        expires_at=now + timedelta(minutes=settings.CONTEXT_TOKEN_EXPIRE_MINUTES),
    )
    if fiduciary_asserted:
        # Independent guard, deliberately NOT a consequence of the
        # tenant-scoped lookup above: a tenant may only assert identity for
        # a customer it actually owns. This must hold even if some future
        # change (or bug) in the lookup above ever handed back a customer
        # belonging to a different tenant - fiduciary assertion is the one
        # thing here that skips OTP verification entirely, so it gets its
        # own explicit check rather than trusting that upstream scoping was
        # done correctly. A loud, generic 404 (not a 403 naming the real
        # owner) - "not found" is exactly what a cross-tenant customer
        # already is everywhere else in this function.
        if customer.source_app != source_app:
            raise HTTPException(status_code=404, detail="Customer not found")
        # R3-05: a tenant whose API key carries SCOPE_FIDUCIARY_ASSERT (see
        # app/api/routes/integration.py::_resolve_fiduciary_assertion) has
        # already authenticated this principal itself before calling the
        # integration API, so the context is pre-verified rather than
        # waiting on email-OTP. Recorded under a distinct
        # verification_method (never "EMAIL_OTP") precisely so the two are
        # never conflated in evidence or audit - a downstream OTP-only check
        # (see app/services/consent.py::_create_evidence's affirmative_action
        # == "OTP" handling) must keep treating this as NOT an OTP
        # verification.
        context.verified_at = now
        context.verification_method = "FIDUCIARY_ASSERTED"
    db.add(context)
    db.flush()
    log_audit(db, "CONTEXT_CREATED", actor_username=created_by, source_app=source_app,
              customer_id=customer.id, customer_external_id=customer.external_id,
              reason="Secure consent context created for customer",
              request_id=request_id,
              metadata={"context_id": context.id, "source": created_by})
    if fiduciary_asserted:
        log_audit(db, "CONTEXT_FIDUCIARY_ASSERTED", actor_username=created_by, source_app=source_app,
                  customer_id=customer.id, customer_external_id=customer.external_id,
                  reason="Identity asserted by the calling tenant at handoff instead of email-OTP verification",
                  request_id=request_id,
                  metadata={"context_id": context.id})
    db.commit()
    db.refresh(context)

    return CustomerContextOut(
        context_token=token,
        context_id=context.id,
        customer_id=customer.external_id,
        name=customer.name,
        expires_in_minutes=settings.CONTEXT_TOKEN_EXPIRE_MINUTES,
        source_app=context.source_app,
        ui_url=f"/consent/context/{token}",
        request_id=request_id,
    )
