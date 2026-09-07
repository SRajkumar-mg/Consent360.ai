"""Consent Management Platform - CRM consent APIs.

Serves the consent popups used by the CRM portal (port 8005) and any future
client website: cookie-category preference storage (per customer), mirroring
of those choices onto the platform's consent records, and consent-context
minting. The CRM's own customer directory lives in its separate backend
service (port 8001); consent data is only ever touched here.
"""

import uuid
from urllib.parse import quote
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import ResolvedApiKey, require_scope, verify_integration_key
from app.core.api_keys import SCOPE_CUSTOMER_PURGE
from app.core.config import get_settings
from app.core.database import get_db
from app.core.encryption import hmac_digest
from app.models.entities import (
    Consent,
    ConsentContext,
    CrmCustomer,
    Customer,
    DataCategory,
    ProcessingActivity,
    Purpose,
)
from app.schemas.schemas import ClientContext, CrmCustomerOut, CrmLoginOut
from app.services import consent as consent_service
from app.services.context import create_context_for_customer, derive_customer_id
from app.services.gpc import purpose_is_consent_based
from app.services.tenancy import ANY_TENANT, resolve_customer, resolve_tenant_id

settings = get_settings()
router = APIRouter(prefix="/crm", tags=["crm-consent"])

CRM_SOURCE_APP = "CRM_PORTAL"

COOKIE_CATEGORY_TO_PURPOSE = {
    "necessary": "strictly_necessary",
    "functional": "functional",
    "analytics": "analytics",
    "advertising": "advertising",
}

# This one router backs THREE different demo frontends (CRM, Codex,
# SkillLearn - see cms/{crm,codex,skilllearn}/src/api.ts, all of which call
# the very same `/crm/customers/{id}/...` paths with no field identifying
# which site is asking). Every route below used to hardcode
# `source_app=CRM_SOURCE_APP`, so every Codex/SkillLearn consent action was
# silently attributed to CRM_PORTAL's ledger instead - a live audit found 81
# CODEX-owned and 81 SKILLLEARN-owned consents mis-stamped this way (see
# scripts/backfill_source_app_misattribution.py, which corrects the
# historical rows this bug already wrote).
#
# The authoritative source_app for a given customer is decided exactly
# once: at /crm/login (crm_directory.py::_ensure_consent360_customer),
# which DOES receive the real source_app from the frontend
# (LoginPage.tsx sends it) and stamps it onto the linked Consent360
# `Customer.source_app` when that Customer row is first created. Every
# route here that already has a linked Customer must defer to that value
# rather than re-guessing - `crm_customers` itself has no source_app column
# (it is one shared directory row per email across all three sites), so it
# cannot answer the question itself.
#
# The one case with no established link at all - a brand-new CrmCustomer
# created here, not at login, because Consent360 was unreachable at login
# time - genuinely cannot be answered from the CrmCustomer directory row
# today. _infer_source_app_from_request's Origin/Referer heuristic is a
# best-effort fallback for that narrow case only, not a substitute for the
# authoritative value: a fully robust fix would add a `source_app` column
# to `crm_customers` itself, populated at /crm/login the same way
# Customer.source_app already is, so this fallback is never needed.
_ORIGIN_PORT_TO_SOURCE_APP = {
    "5174": "CODEX",
    "5175": "SKILLLEARN",
    "8008": "CRM_PORTAL",
}

# The closed, named set of source_apps allowed to share this one unauthenticated
# directory/login flow (see the module comment above and R3's "squat-then-claim"
# writeup in the security-fix report: /crm/login and the routes below take no
# credential at all, so the set of tenants they may act as must be a small,
# explicit allow-list - never an arbitrary caller-supplied string that could
# name an unrelated, real, independently-authenticated tenant).
CRM_FAMILY_SOURCE_APPS = frozenset(_ORIGIN_PORT_TO_SOURCE_APP.values())

# Where "Return to site" should land, per source_app - see _consent_portal_url.
_SOURCE_APP_RETURN_PATH = {
    "CRM_PORTAL": "/users",
    "CODEX": "/platform",
    "SKILLLEARN": "/learn",
}


def _infer_source_app_from_request(request: Request) -> str:
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    for port, source_app in _ORIGIN_PORT_TO_SOURCE_APP.items():
        if f":{port}" in origin:
            return source_app
    return CRM_SOURCE_APP


def _find_linked_customer(db: Session, crm_customer: CrmCustomer) -> Customer | None:
    """Look up (never create) the Consent360 Customer reference linked to a
    CRM directory customer, by email or by its derived external id.

    Scoped to CRM_FAMILY_SOURCE_APPS, not ANY_TENANT: this preserves the
    intentional shared identity across the three sibling sites (a customer
    who used CRM_PORTAL is still recognised on CODEX with the same email),
    while making it structurally impossible for this unauthenticated lookup
    to reach a customer belonging to any OTHER, unrelated tenant - see R3's
    write-up for the concrete attack this closes.
    """
    email = (crm_customer.email or "").strip().lower()
    if not email:
        return None
    target = resolve_customer(db, source_app=CRM_FAMILY_SOURCE_APPS, email=email)
    if not target:
        target = resolve_customer(db, source_app=CRM_FAMILY_SOURCE_APPS, external_id=derive_customer_id(email=email))
    return target


def _link_customer(db: Session, crm_customer: CrmCustomer, fallback_source_app: str = CRM_SOURCE_APP) -> Customer | None:
    """Find or create the Consent360 Customer reference for a CRM customer.

    If no Customer exists yet (e.g. Consent360 was unreachable at login
    time), this creates one using ``fallback_source_app`` - see the module
    note above for why that value is only a best-effort guess, never an
    authoritative one, for this specific fallback path.
    """
    email = (crm_customer.email or "").strip().lower()
    if not email:
        return None
    target = _find_linked_customer(db, crm_customer)
    if not target:
        external_id = derive_customer_id(email=email, phone=crm_customer.phone)
        target = Customer(
            external_id=external_id,
            external_id_search=hmac_digest(external_id),
            name=crm_customer.name,
            email=email,
            email_search=hmac_digest(email),
            phone=crm_customer.phone or "",
            status="ACTIVE",
            source_app=fallback_source_app,
            tenant_id=resolve_tenant_id(db, fallback_source_app),
        )
        db.add(target)
        db.flush()
    return target


def _read_gpc_signal(request: Request) -> bool | None:
    """Global Privacy Control (https://globalprivacycontrol.org/): the
    ``Sec-GPC`` request header, read server-side rather than trusted from the
    request body - the same "observed vs claimed" split already applied to
    ip_address/user_agent here, and the same helper
    app/api/routes/portal.py::_read_gpc_signal implements for the portal.

    It has to exist in both places: CRM, Codex and SkillLearn all record their
    cookie-banner choices through PUT /crm/customers/{id}/consent-preferences,
    never through /portal/grant, so without this three of the four demo sites
    left ConsentEvidence.gpc_signal permanently NULL and recorded only the
    client-claimed value - exactly the weaker signal that column separation
    exists to distinguish.

    Returns None when the header is absent, True when it is "1", False for any
    other value.
    """
    header = request.headers.get("sec-gpc")
    if header is None:
        return None
    return header.strip() == "1"


def _sync_consent_preferences(
    db: Session,
    crm_customer: CrmCustomer,
    categories: dict,
    client_context: ClientContext | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    fallback_source_app: str = CRM_SOURCE_APP,
    gpc_signal: bool | None = None,
) -> None:
    """Mirror the CRM cookie-category choices onto the consent platform's consent
    records so the admin portal reflects the customer's preferences (grant /
    deny / withdraw per purpose).

    Attributed to the linked Customer's own, already-authoritative
    source_app (see the module note above) - NOT hardcoded to CRM_PORTAL -
    so a Codex or SkillLearn visitor's cookie-banner choices land in their
    own tenant's ledger rather than CRM's.

    B-01/B-02 (s.6(10)) - A REFUSAL IS RECORDED, NOT JUST AN ABSENCE. This
    function used to handle a switched-off category with
    `elif consent.status in ("GRANTED", "ACTIVE", "RENEWED", "UPDATED")`, i.e.
    it only ever WITHDREW something already granted and did nothing whatsoever
    for a row that had never been granted. Since CRM, Codex and SkillLearn all
    record their banner choices through this one function - only CareerHub goes
    through `/portal/deny` - a first-time visitor pressing "Reject all" on
    three of the four demo sites got HTTP 200 and left every consent row
    NOT_REQUESTED, with zero evidence rows and only CONSENT_CREATED in the
    audit log. Reproduced live: a brand-new CRM_PORTAL visitor rejecting all
    four categories left all 33 of her consent rows NOT_REQUESTED. The refusal
    lived in the `consent_preferences` blob and that browser's localStorage and
    nowhere else - indistinguishable, in the ledger, from never having been
    asked, so s.6(10)'s burden of proving she refused could not be discharged.

    Now: deny what was never granted, withdraw what is live. Both verbs go
    through `services/consent.py` so the status change, the ConsentHistory row,
    the ConsentEvidence row (carrying this same ClientContext - language,
    session, screen/control, click depth - plus the observed IP, user agent and
    server-read `Sec-GPC`) and the audit row are written exactly as they are
    for a grant. Statuses that are already non-affirmative (WITHDRAWN, DENIED,
    EXPIRED) are left alone: re-denying a principal's own withdrawal would
    overwrite her act with a system one, and the banner re-sends every category
    on every save.

    Which statuses those are is read from `DENIABLE_STATUSES` /
    `WITHDRAWABLE_STATUSES` in the consent service, derived from
    CONSENT_TRANSITIONS - shared with `/portal/deny` rather than restated here,
    which is exactly the drift that produced this gap.
    """
    customer = _link_customer(db, crm_customer, fallback_source_app)
    if not customer:
        return
    source_app = customer.source_app or fallback_source_app
    purposes = db.query(Purpose).filter(Purpose.is_active.is_(True)).all()
    for purpose in purposes:
        category_key = next((k for k, code in COOKIE_CATEGORY_TO_PURPOSE.items() if code == purpose.code), None)
        if not category_key:
            continue
        enabled = bool(categories.get(category_key, False))
        try:
            purpose_version = consent_service.get_current_purpose_version(purpose)
        except Exception:
            continue
        for category_id in purpose_version.data_category_ids:
            data_category = db.get(DataCategory, category_id)
            for activity_id in purpose_version.processing_activity_ids:
                activity = db.get(ProcessingActivity, activity_id)
                consent, _ = consent_service.get_or_create_consent(
                    db, customer, purpose, data_category, activity,
                    actor_username="crm", source_app=source_app, collection_method="UI",
                )
                if enabled:
                    if consent.status not in ("GRANTED", "ACTIVE"):
                        consent_service.grant_consent(
                            db, consent, reason="Consent granted via CRM cookie preferences",
                            actor_username="crm", source_app=source_app, collection_method="UI",
                            client_context=client_context, ip_address=ip_address, user_agent=user_agent,
                            gpc_signal=gpc_signal,
                        )
                        consent_service.activate_consent(db, consent, reason="Consent granted via CRM cookie preferences",
                                                         actor_username="crm", source_app=source_app)
                elif consent.status in consent_service.DENIABLE_STATUSES and purpose_is_consent_based(purpose):
                    # Never granted, and the principal has now said no: that is
                    # a refusal, and it belongs in the ledger as one.
                    #
                    # `purpose_is_consent_based` is the SAME predicate GPC
                    # enforcement uses (app/services/gpc.py), for the same
                    # reason and with the same boundary: only processing that
                    # actually rests on s.6 consent can be refused by
                    # withholding consent. It is needed HERE and not in
                    # `/portal/deny` because this is the only refusal path that
                    # INFERS one - the banner sends a category map, and
                    # `categories.get(key, False)` reads an absent or false
                    # `necessary` as a refusal of the seeded
                    # `strictly_necessary` purpose (legal_basis S7_A,
                    # requires_consent False), which the CRM banner itself
                    # renders as a locked, always-on toggle. Recording DENIED
                    # there would make `evaluate_decision` answer DENY for
                    # login, session security and the record of the
                    # principal's own consent choices - it reads the consent
                    # status before it ever reaches `requires_consent=False`
                    # - i.e. a reject-all would switch off the service rather
                    # than protect anybody. `/portal/deny` names one purpose
                    # explicitly and `/portal/overview` only ever offers
                    # consent-based ones, so it infers nothing and needs no
                    # such guard.
                    consent_service.deny_consent(
                        db, consent, reason="Consent refused via CRM cookie preferences",
                        actor_username="crm", source_app=source_app, collection_method="UI",
                        client_context=client_context, ip_address=ip_address, user_agent=user_agent,
                        gpc_signal=gpc_signal,
                    )
                elif consent.status in consent_service.WITHDRAWABLE_STATUSES:
                    consent_service.withdraw_consent(
                        db, consent, reason="Consent withdrawn via CRM cookie preferences",
                        actor_username="crm", source_app=source_app,
                        client_context=client_context, ip_address=ip_address, user_agent=user_agent,
                        gpc_signal=gpc_signal,
                    )


def _purge_customer_data(db: Session, target: Customer) -> str:
    """Anonymise a customer's direct identifiers. Consents, evidence, history
    and audit rows are kept intact for the compliance ledger - only the
    Customer row's PII is blanked, and its contexts are deactivated."""
    anon_ref = f"ANON-{uuid.uuid4().hex[:12].upper()}"
    db.query(ConsentContext).filter(ConsentContext.customer_id == target.id).update(
        {ConsentContext.is_active: False}, synchronize_session=False
    )
    target.name = "[anonymised]"
    target.email = ""
    target.email_search = None
    target.phone = ""
    target.status = "ANONYMISED"
    target.anonymised_ref = anon_ref
    target.external_id = anon_ref
    target.external_id_search = hmac_digest(anon_ref)
    return anon_ref


class ConsentPreferencesIn(BaseModel):
    lang: str = "en"
    categories: dict[str, bool] = {}
    context: ClientContext = ClientContext()


def _return_base_for(source_app: str) -> str:
    return {
        "CRM_PORTAL": settings.CRM_PORTAL_URL,
        "CODEX": settings.CODEX_PORTAL_URL,
        "SKILLLEARN": settings.SKILLLEARN_PORTAL_URL,
    }.get(source_app, settings.CRM_PORTAL_URL)


def _consent_portal_url(context_token: str, source_app: str = CRM_SOURCE_APP) -> str:
    """Consent portal URL carrying the context token and the return address
    back to the ORIGINATING demo site.

    Previously this always pointed at CRM_PORTAL_URL regardless of
    source_app - and CRM_PORTAL_URL itself defaulted to the STAFF admin
    console's port (:8005), not any demo site - so "Return to site" never
    went back where the principal actually came from for Codex or
    SkillLearn, and even a CRM visitor landed on the admin console's
    /users rather than the CRM demo site's own /users page. See
    CRM_PORTAL_URL/CODEX_PORTAL_URL/SKILLLEARN_PORTAL_URL in
    app/core/config.py and _SOURCE_APP_RETURN_PATH above.
    """
    path = _SOURCE_APP_RETURN_PATH.get(source_app, "/users")
    return_url = quote(f"{_return_base_for(source_app)}{path}", safe="")
    return f"{settings.CONSENT_PORTAL_URL}/portal/consent?token={context_token}&return_url={return_url}"


def _issue_context(db: Session, customer: CrmCustomer, request: Request) -> CrmLoginOut:
    linked = _find_linked_customer(db, customer)
    source_app = linked.source_app if linked and linked.source_app else _infer_source_app_from_request(request)
    ctx = create_context_for_customer(
        db,
        name=customer.name,
        email=customer.email,
        phone=customer.phone or None,
        status="ACTIVE",
        source_app=source_app,
        created_by="crm",
    )
    return CrmLoginOut(
        customer=CrmCustomerOut.model_validate(customer),
        created=False,
        context_token=ctx.context_token,
        consent_portal_url=_consent_portal_url(ctx.context_token, source_app),
        expires_in_minutes=ctx.expires_in_minutes,
    )


@router.post("/customers/{customer_id}/consent-context", response_model=CrmLoginOut)
def crm_customer_consent_context(customer_id: int, request: Request, db: Session = Depends(get_db)):
    """Mint a consent context for an existing CRM customer (Manage Consent flow).

    Shared by all three demo frontends (CRM, Codex, SkillLearn) - see the
    module note above _ORIGIN_PORT_TO_SOURCE_APP for how the real caller is
    attributed."""
    customer = db.get(CrmCustomer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="CRM customer not found")
    return _issue_context(db, customer, request)


@router.get("/customers/{customer_id}/consent-preferences")
def get_crm_consent_preferences(customer_id: int, db: Session = Depends(get_db)):
    """Return the cookie preferences saved for a CRM customer (per-customer, not browser-wide)."""
    customer = db.get(CrmCustomer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="CRM customer not found")
    return {"preferences": customer.consent_preferences or {}}


@router.put("/customers/{customer_id}/consent-preferences")
def put_crm_consent_preferences(
    customer_id: int, payload: ConsentPreferencesIn, request: Request, db: Session = Depends(get_db)
):
    """Save cookie preferences for a CRM customer and mirror them onto the
    consent platform so the admin portal reflects the same choices."""
    customer = db.get(CrmCustomer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="CRM customer not found")
    prefs = {"lang": payload.lang, "categories": payload.categories, "at": datetime.now(timezone.utc).isoformat()}
    customer.consent_preferences = prefs
    db.commit()
    observed_ip = request.client.host if request.client else None
    observed_user_agent = request.headers.get("user-agent")
    _sync_consent_preferences(
        db, customer, payload.categories, payload.context,
        ip_address=observed_ip, user_agent=observed_user_agent,
        fallback_source_app=_infer_source_app_from_request(request),
        gpc_signal=_read_gpc_signal(request),
    )
    return {"saved": True, "preferences": customer.consent_preferences}


def purge_customer_by_email_core(
    db: Session,
    email: str,
    *,
    scope: str | frozenset,
    actor_username: str = "integration",
    actor_type: str = "INTEGRATION",
) -> dict:
    """Anonymise a customer's PII by email, scoped to ``scope`` — a single
    source_app, a frozenset of allowed source_apps (see
    ``CRM_FAMILY_SOURCE_APPS``), or the explicit ``ANY_TENANT`` sentinel for
    a caller that has actually proven it has no tenant identity to scope to.

    ``scope`` has NO default: every caller must say out loud what blast
    radius it is authorising. It used to default falsy values to
    ``ANY_TENANT``, and ``crm_directory.py``'s own DELETE
    ``/crm/customers/{id}`` — which takes NO credential at all — silently
    inherited that by never passing a scope, letting anyone anonymise ANY
    tenant's real customer merely by knowing (or asserting) their email.
    See the R3 fix report ("require a credential and a tenant to purge a
    customer") for the live exploit this closes.

    Shared by the API-key-gated ``DELETE /crm/customers/by-email/{email}``
    route below (external callers) AND, in-process, by crm_directory.py's
    own ``DELETE /crm/customers/{customer_id}`` (deleting a CRM directory
    record used to make a loopback HTTP call back into this very same
    backend process, authenticated with the legacy shared API key, purely
    to reach this logic - calling it directly removes that internal
    legacy-key dependency entirely). Consent, evidence, history and audit
    rows are never deleted.
    """
    from app.services.audit import log_audit

    # A wrong-tenant (or out-of-scope) match is indistinguishable from
    # "does not exist" (404), matching resolve_customer's idiom everywhere
    # else - not a 403 that would otherwise confirm the email belongs to
    # SOME customer.
    normalized = email.strip().lower()
    target = resolve_customer(db, source_app=scope, email=normalized)
    if not target:
        target = resolve_customer(db, source_app=scope, external_id=derive_customer_id(email=normalized))
    if not target:
        raise HTTPException(status_code=404, detail="Customer not found")
    original_external_id = target.external_id
    anon_ref = _purge_customer_data(db, target)
    log_audit(db, "CUSTOMER_PURGED", actor_username=actor_username, actor_type=actor_type,
              source_app=target.source_app, customer_id=target.id, customer_external_id=anon_ref,
              reason="Customer PII anonymised on purge request", commit=False)
    db.commit()
    return {"deleted": True, "anonymised": True, "external_id": original_external_id, "anonymised_ref": anon_ref}


@router.delete("/customers/by-email/{email}")
def purge_customer_by_email(
    email: str,
    db: Session = Depends(get_db),
    resolved_key: ResolvedApiKey = Depends(verify_integration_key),
):
    """Anonymise a customer's PII (called by any external client holding a
    tenant-bound - or, if explicitly enabled, the legacy - integration API
    key when the customer is deleted there)."""
    require_scope(resolved_key, SCOPE_CUSTOMER_PURGE)
    # A tenant-bound key is scoped to its own tenant; the legacy key (no
    # tenant_code) proves no tenant identity at all, so — in the one case
    # where it could even reach here — it keeps its pre-existing,
    # unrestricted-by-tenant reach. In practice `require_scope` above now
    # always rejects the legacy key first (it implicitly carries only
    # SCOPE_INTEGRATION_WRITE, never SCOPE_CUSTOMER_PURGE), so this branch
    # documents the intended blast radius rather than being reachable today
    # — defense in depth if that scope decision is ever revisited.
    return purge_customer_by_email_core(
        db, email, actor_username="integration", actor_type="INTEGRATION",
        scope=resolved_key.tenant_code if resolved_key.tenant_code else ANY_TENANT,
    )