"""R3-10 (CM-01, CM-02, CM-06, J-04): Consent Manager interoperability.

Three things live here, in dependency order:

1. **Authorisation** - `resolve_broker_context`, the single choke point that
   turns an authenticated API key into "which fiduciary may this caller act
   for". This is the security-critical part of the whole task; see its
   docstring for the cross-tenant reasoning.
2. **The artefact** - `build_artefact_payload` and the create / update /
   withdraw operations, which are *facades* over `app/services/consent.py`.
   No consent state is ever changed here directly.
3. **Metrics** - `measure`, which samples every CM-facing call for CM-06 /
   K-42 / K-43.

The data-blind design decision
------------------------------
DPDP Rules 2025 First Schedule Part B 2 requires a Consent Manager to be
**data-blind**: the personal data whose sharing it intermediates must not be
readable by it. That is an architectural commitment, so it is written down
here and in `docs/compliance/CONSENT_MANAGER_DATA_BLIND_DECISION.md` rather
than left to be inferred from whichever fields a serialiser happens to emit.

The decision, in short:

  Consent360 is a **fiduciary-side** consent platform. It is not itself a
  registered Consent Manager, and this API does not make it one. When a
  registered CM calls this API, Consent360 acts data-blind *towards that CM*:
  the CM is given consent metadata and nothing else.

What that means concretely, and is enforced by `build_artefact_payload` and
the `ArtefactOut` serialiser:

  - A CM caller never receives any personal-data **content**. Purposes and
    data *categories* are named (it must know what the consent covers); no
    value of any field in those categories is ever returned. The platform has
    no endpoint on this surface that returns a data value at all.
  - A CM caller never receives the principal's name, email, phone, IP or the
    fiduciary's own external id for them. It gets `principal_ref`, an
    HMAC-derived pseudonym that is stable for one (CM, principal) pair and
    *different* for every other CM, so two CMs cannot correlate their
    subject populations by comparing references, and a leaked reference is
    not an identifier anywhere else.
  - A fiduciary calling this API for its **own** tenant is not a CM and is
    not treated as one: it already holds its own customers' data, so
    redacting its own external id from its own response would be
    security theatre. `BrokerContext.data_blind` is exactly
    "the caller is a Consent Manager", never a configurable flag - a
    per-CM opt-out is not offered, because a Part B 2 obligation an operator
    can switch off is not an obligation.

What this decision does NOT claim: Consent360 still stores full personal data
on behalf of the fiduciaries that own it (that is what a fiduciary-side CMS
does). Data-blindness here is a property of the CM-facing boundary, not of
the platform as a whole. If Consent360 were ever to register as a Consent
Manager in its own right, CM-02 would additionally require that the *stored*
payloads be opaque to it (end-to-end encryption between fiduciaries with the
CM holding no key), which is a different system and a different task.

Retention
---------
`consent_artefacts` / `consent_artefact_events` are Consent Manager records
under First Schedule Part B 3 and 4(c): **7 years**. That floor belongs to
R1-10's retention register (`app/services/retention.py`), which already
defines a 7-year `consent_manager_records` overlay class; these two tables
belong under it. Nothing in this module deletes an artefact or an event, and
no competing retention mechanism is defined here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import ResolvedApiKey
from app.core.encryption import hmac_signature, hmac_signature_matches
from app.core.utils import build_rate_limiter, mask_identifier
from app.models.artefacts import (
    ConsentArtefact,
    ConsentArtefactEvent,
    ConsentArtefactLink,
    ConsentManager,
    ConsentManagerApiCall,
    ConsentManagerFiduciary,
)
from app.models.entities import (
    Consent,
    Customer,
    DataCategory,
    Organization,
    ProcessingActivity,
    Purpose,
)
from app.schemas.schemas import ClientContext
from app.services import consent as consent_service
from app.services.audit import log_audit
from app.services.tenancy import resolve_customer

logger = logging.getLogger("app.consent_manager")


# --------------------------------------------------------------------------- #
#  API-key scopes
#
#  These belong next to SCOPE_INTEGRATION_WRITE / SCOPE_CUSTOMER_PURGE /
#  SCOPE_FIDUCIARY_ASSERT in `app/core/api_keys.py`; they are defined here
#  only because that file's neighbourhood (`app/api/deps.py`) is owned by
#  another lane in this round. They are plain strings enforced by the
#  existing `app.api.deps.require_scope`, so nothing about the enforcement
#  path is new or duplicated: a key that was never granted one of these
#  cannot use the endpoint that requires it, and the legacy unbound
#  INTEGRATION_API_KEY implicitly carries only SCOPE_INTEGRATION_WRITE and
#  therefore none of these, ever.
# --------------------------------------------------------------------------- #

# Canonical definitions live in app/core/api_keys.py alongside the other
# scope constants, so there is one source of truth for what a key may hold.
from app.core.api_keys import (  # noqa: E402
    SCOPE_ARTEFACT_READ,
    SCOPE_ARTEFACT_WRITE,
    SCOPE_DECISION_EVALUATE,
)

CONSENT_MANAGER_SCOPES = (SCOPE_DECISION_EVALUATE, SCOPE_ARTEFACT_READ, SCOPE_ARTEFACT_WRITE)


# --------------------------------------------------------------------------- #
#  Artefact schema identity
# --------------------------------------------------------------------------- #

#: TS 27560 clause 6.3.3.2 makes `schema_version` a REQUIRED record-header
#: field, defined as "a unique reference for the implementation documentation
#: describing interpretation of the record structure and contents". It is
#: therefore a reference to OUR published schema documentation, not a bare
#: number - and clause 6.3.2.1 is a `shall`: an organization that creates its
#: own schema "shall publish or reference the schema(s) being used and
#: maintain documentation necessary for its correct technical implementation".
#: `GET /consent-manager/artefact-schema` (unauthenticated) plus
#: `docs/compliance/CONSENT_MANAGER_ARTEFACT_SCHEMA.md` is how that `shall` is
#: discharged; this URN is the stable identifier both of them describe. Bump it
#: whenever the payload shape changes.
ARTEFACT_SCHEMA_VERSION = "urn:consent360:artefact-schema:1.0"

#: Where a reader can fetch the documentation `schema_version` refers to.
ARTEFACT_SCHEMA_REFERENCE = "/consent-manager/artefact-schema"

ARTEFACT_SCHEMA_MODELLED_ON = (
    "ISO/IEC TS 27560:2023 (Privacy technologies - Consent record information structure), "
    "first edition 2023-08; DPDP Rules 2025 First Schedule Part B; MeitY BRD sec.4.1.1 "
    "consent artefact; DEPA / RBI Account Aggregator artefact semantics"
)

#: Read this before repeating any conformance claim anywhere. It is deliberately
#: blunt; see `docs/compliance/CONSENT_MANAGER_ARTEFACT_SCHEMA.md` for the
#: field-by-field provenance table behind it.
#:
#: What IS true, from the ISO text itself (the publicly available ISO preview
#: covers the front matter, Scope, definitions, full Contents and the normative
#: Tables 1-2):
#:   - TS 27560 defines an information model, NOT a JSON encoding. Every annex,
#:     including Annex A ("Examples of consent records and receipts") and
#:     Annex D ("Consent record encoding structure"), is marked *informative*;
#:     the only normative references are ISO/IEC 29100 and 29184. The
#:     Introduction states the document "does not specify an exchange protocol
#:     for consent records or consent receipts, nor structures for such
#:     exchanges", and 6.3.2.2 NOTE 2 says implementers may organize the fields
#:     "according to the implementers' operational needs".
#:   - Consequently there is no official JSON Schema and no validator: "passes
#:     the TS 27560 validator" is not a thing that can be true of anything.
#:   - The record-header and PII-processing field names used below are taken
#:     from the standard's own Tables 1 and 2.
#: What is NOT verified:
#:   - The field names for the party, event and PII-information sections are
#:     beyond the pages of the preview. They follow the W3C DPV community
#:     group's implementation guide, which matched the ISO text exactly on all
#:     12 processing fields that could be checked - good evidence, not proof.
#:   - No conformance assessment of any kind has been carried out.
ARTEFACT_CONFORMANCE_STATEMENT = (
    "MODELLED ON ISO/IEC TS 27560:2023, NOT CERTIFIED AGAINST IT. TS 27560 defines an "
    "information model, not a JSON encoding: its JSON examples are informative, there is "
    "no normative schema and no conformance validator, and clause 6.3.2.2 expressly lets "
    "an implementer organise the fields for its own needs. This payload therefore carries "
    "the standard's information elements under Consent360's own published schema, as "
    "clause 6.3.2.1 requires ('shall publish or reference the schema(s) being used') - see "
    "GET /consent-manager/artefact-schema. Record-header and PII-processing field names are "
    "taken from the standard's Tables 1 and 2; party, event and PII-information field names "
    "follow the W3C DPV community group's public implementation guide and have NOT been "
    "checked against the ISO text. No conformance assessment has been performed. Note also "
    "that TS 27560:2023 has been at ISO stage 90.92 ('to be revised') since April 2025 and "
    "is being replaced by ISO/IEC CD 27560.2, 'Structure of Personally Identifiable "
    "Information (PII) Processing Records'."
)

#: TS 27560 status, surfaced in the schema descriptor so an integrator building
#: a conformance claim on top of ours sees the shelf life of the reference.
ARTEFACT_STANDARD_STATUS = (
    "ISO/IEC TS 27560:2023, first edition 2023-08-08, ISO/IEC JTC 1/SC 27. Published but at "
    "stage 90.92 (standard to be revised) since 2025-04-03; successor ISO/IEC CD 27560.2 is "
    "in development as a full International Standard under a new title."
)

CONSENT_MANAGER_COLLECTION_METHOD = "CONSENT_MANAGER"


# --------------------------------------------------------------------------- #
#  Rate limiters
#
#  Same in-memory/Redis limiter every other credentialed surface uses (see
#  app/core/utils.py::build_rate_limiter). Keyed per API key prefix + IP, not
#  per IP alone: a shared NAT egress must not let one tenant exhaust another
#  tenant's budget, and a leaked key must not be able to hide behind rotating
#  source addresses.
# --------------------------------------------------------------------------- #

decision_limiter = build_rate_limiter("decision", limit=600, window_seconds=60)
artefact_limiter = build_rate_limiter("artefact", limit=120, window_seconds=60)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
#  Authorisation: which fiduciary may this caller act for?
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BrokerContext:
    """The resolved answer to "who is calling, and for whom".

    `fiduciary_source_app` / `fiduciary_tenant_id` are the ONLY values any
    downstream lookup may scope on. Nothing downstream re-derives a tenant
    from a caller-supplied string.
    """

    consent_manager: Optional[ConsentManager]
    onboarding: Optional[ConsentManagerFiduciary]
    fiduciary_source_app: str
    fiduciary_tenant_id: int
    actor: str

    @property
    def is_consent_manager(self) -> bool:
        return self.consent_manager is not None

    @property
    def data_blind(self) -> bool:
        """Whether the caller must be treated as data-blind. Identical to
        "the caller is a Consent Manager" by design - see this module's
        docstring; there is deliberately no configurable override."""
        return self.consent_manager is not None

    @property
    def consent_manager_ref(self) -> Optional[str]:
        return self.consent_manager.cm_ref if self.consent_manager else None


def resolve_broker_context(
    db: Session,
    resolved_key: ResolvedApiKey,
    *,
    requested_source_app: Optional[str],
) -> BrokerContext:
    """Turn an authenticated API key into the one fiduciary it may act for.

    Two callers exist, and they are separated here rather than anywhere else:

    **A fiduciary acting for itself.** Its key's tenant IS the fiduciary, so
    the rule is the same one `/consent/customer-context` already applies: a
    `source_app` that differs from the key's tenant is a 403, never a silent
    re-scope.

    **A registered Consent Manager acting for an onboarded fiduciary.** This
    is the one place in this codebase where the calling key's tenant is
    legitimately NOT the tenant whose data is touched, so it is also the one
    place a new cross-tenant hole could open. Four independent conditions must
    all hold, and each is checked here rather than at any call site:

      1. The key's tenant is a `consent_managers` row (a CM is a tenant of its
         own, never one of the fiduciaries it brokers for).
      2. That CM is `REGISTERED` and active. s.6(9)/R.4: an unregistered,
         suspended or deregistered CM has no standing to act at all.
      3. An `ACTIVE` `consent_manager_fiduciaries` row exists for
         (this CM, the named `source_app`). That row is the entire
         authorisation - there is no fallback, no implicit onboarding, and no
         "allow if the tenant exists".
      4. The Organization that row points at still exists, is active, and its
         `code` still equals the `source_app` string on the row. This is a
         second, independent agreement between the row's two representations
         of the same tenant, in the same spirit as
         `portal.py::_resolve_customer_and_context` re-checking the persisted
         context against the token claim: a row whose denormalised
         `source_app` ever drifted from its `tenant_id` must fail closed, not
         pick one.

    Everything downstream then scopes on the returned `fiduciary_source_app`
    via `app/services/tenancy.py::resolve_customer`, exactly like every other
    customer resolution in the codebase.
    """
    # The legacy, unbound INTEGRATION_API_KEY proves no tenant identity at
    # all - it names whatever source_app it likes. `require_scope` already
    # refuses it every scope other than integration.write (so it can never
    # hold SCOPE_ARTEFACT_*/SCOPE_DECISION_EVALUATE), but this surface is
    # the one where "the caller names another tenant" is a legitimate
    # request shape, so it is refused here explicitly as well rather than
    # relying on that one control.
    if resolved_key.tenant_code is None or resolved_key.tenant_id is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "This API requires a tenant-bound API key; the shared legacy integration key "
                "cannot identify a Consent Manager or a Data Fiduciary."
            ),
        )

    cm = (
        db.query(ConsentManager)
        .filter(ConsentManager.tenant_id == resolved_key.tenant_id)
        .first()
    )

    if cm is None:
        if requested_source_app and requested_source_app != resolved_key.tenant_code:
            raise HTTPException(
                status_code=403,
                detail="source_app does not match the tenant bound to this API key",
            )
        return BrokerContext(
            consent_manager=None,
            onboarding=None,
            fiduciary_source_app=resolved_key.tenant_code,
            fiduciary_tenant_id=resolved_key.tenant_id,
            actor=f"fiduciary:{resolved_key.tenant_code}",
        )

    if not cm.is_active or cm.registration_status != "REGISTERED":
        raise HTTPException(
            status_code=403,
            detail=(
                f"Consent Manager {cm.cm_ref} is not currently registered "
                f"(status {cm.registration_status}) and cannot broker consent."
            ),
        )
    if not requested_source_app:
        raise HTTPException(
            status_code=422,
            detail="source_app is required: a Consent Manager must name the Data Fiduciary it is acting for.",
        )
    if requested_source_app == resolved_key.tenant_code:
        raise HTTPException(
            status_code=403,
            detail="A Consent Manager cannot broker consent for itself.",
        )

    onboarding = (
        db.query(ConsentManagerFiduciary)
        .filter(
            ConsentManagerFiduciary.consent_manager_id == cm.id,
            ConsentManagerFiduciary.source_app == requested_source_app,
        )
        .first()
    )
    if onboarding is None or onboarding.status != "ACTIVE":
        # Deliberately the same message either way: whether a fiduciary
        # exists but is not onboarded, or does not exist at all, is not the
        # calling CM's business.
        raise HTTPException(
            status_code=403,
            detail=(
                f"Consent Manager {cm.cm_ref} is not onboarded to act for '{requested_source_app}'."
            ),
        )

    org = db.get(Organization, onboarding.tenant_id)
    if org is None or not org.is_active or org.code != requested_source_app:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Consent Manager {cm.cm_ref} is not onboarded to act for '{requested_source_app}'."
            ),
        )

    return BrokerContext(
        consent_manager=cm,
        onboarding=onboarding,
        fiduciary_source_app=org.code,
        fiduciary_tenant_id=org.id,
        actor=f"cm:{cm.cm_ref}",
    )


def assert_purpose_onboarded(ctx: BrokerContext, purpose_code: str) -> None:
    """Enforce the onboarding row's purpose bound, if it set one.

    An empty `allowed_purpose_codes` means "every active purpose" (the
    ordinary onboarding); a non-empty list is a fiduciary that only wants
    some of its purposes intermediated, and a CM asking outside it is a 403,
    not a silently-ignored request.
    """
    if ctx.onboarding is None:
        return
    allowed = list(ctx.onboarding.allowed_purpose_codes or [])
    if allowed and purpose_code not in allowed:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Purpose '{purpose_code}' is outside the purposes "
                f"'{ctx.fiduciary_source_app}' has onboarded to this Consent Manager."
            ),
        )


def principal_pseudonym(consent_manager_ref: Optional[str], customer_pk: int) -> str:
    """A stable, non-reversible principal reference.

    Keyed by the server's own HMAC secret (`app.core.encryption.hmac_signature`
    - the same primitive that signs consent evidence and receipts, no second
    scheme), and salted with the Consent Manager's own ref, so:

      - the same principal is the same reference across all of ONE CM's
        artefacts, which is what makes "review my consents" work for a CM
        without it ever holding a direct identifier;
      - the SAME principal is a DIFFERENT reference to a different CM, so two
        CMs comparing notes cannot correlate their subject populations;
      - the reference discloses nothing on its own and cannot be reversed
        without the server key.

    For a fiduciary acting on its own tenant the salt is the fixed string
    "fiduciary" - it already knows its own customers, so a per-caller
    reference would add nothing.
    """
    salt = consent_manager_ref or "fiduciary"
    return "pid_" + hmac_signature("principal-pseudonym", salt, str(customer_pk))[:32]


def resolve_principal(
    db: Session,
    ctx: BrokerContext,
    *,
    customer_id: Optional[str] = None,
    email: Optional[str] = None,
    principal_ref: Optional[str] = None,
) -> Customer:
    """Resolve the principal, always scoped to the fiduciary in `ctx`.

    Every branch goes through `app/services/tenancy.py::resolve_customer` with
    an explicit `source_app` - the codebase's required choke point (a guard
    test fails any code that reaches around it). A principal belonging to a
    different tenant is indistinguishable from one that does not exist, which
    is the entire point.

    This API deliberately **never creates** a principal. A Consent Manager
    brokers consent for people who already have a relationship with the
    fiduciary; letting it mint identities in a tenant it does not own would
    hand it a write primitive over that tenant's customer base, which no part
    of s.6(7) or First Schedule Part B contemplates.

    `principal_ref` is resolved by recomputing the pseudonym for candidate
    customers rather than by storing a reverse index: the artefact rows
    already record which customer each ref belongs to, so a ref is looked up
    through `consent_artefacts` (scoped to this fiduciary AND, for a CM, to
    that CM's own artefacts), never globally.
    """
    provided = [v for v in (customer_id, email, principal_ref) if v]
    if len(provided) != 1:
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one of customer_id, email or principal_ref to identify the principal.",
        )

    if principal_ref:
        q = db.query(ConsentArtefact).filter(
            ConsentArtefact.principal_ref == principal_ref,
            ConsentArtefact.source_app == ctx.fiduciary_source_app,
        )
        if ctx.is_consent_manager:
            q = q.filter(ConsentArtefact.consent_manager_id == ctx.consent_manager.id)
        else:
            q = q.filter(ConsentArtefact.consent_manager_id.is_(None))
        artefact = q.order_by(ConsentArtefact.id.desc()).first()
        customer = (
            resolve_customer(
                db, source_app=ctx.fiduciary_source_app, customer_pk=artefact.customer_id
            )
            if artefact
            else None
        )
    elif customer_id:
        customer = resolve_customer(
            db, source_app=ctx.fiduciary_source_app, external_id=customer_id
        )
    else:
        customer = resolve_customer(db, source_app=ctx.fiduciary_source_app, email=email)

    if not customer:
        raise HTTPException(
            status_code=404,
            detail=f"No such principal at '{ctx.fiduciary_source_app}'.",
        )
    return customer


# --------------------------------------------------------------------------- #
#  The artefact payload
# --------------------------------------------------------------------------- #

def _party_blocks(db: Session, artefact: ConsentArtefact, ctx: BrokerContext) -> list[dict]:
    """The party identification section: one entry per party to this record.

    TS 27560 Table 2 makes `pii_controllers` a structure of *references* into
    this section rather than inline controller objects, so the fiduciary
    appears here once and is referenced by `party_id` from every purpose.

    Field names (`party_id`, `party_name`, `party_role`, ...) follow the W3C
    DPV community group's implementation guide for TS 27560 clause 6.3.6; that
    part of the ISO text is beyond the publicly readable preview, so these
    names are high-confidence but unverified - see
    ARTEFACT_CONFORMANCE_STATEMENT.

    Contact details are the tenant's own DPO configuration
    (`organizations.dpo_*`): the business contact a principal is entitled to
    under s.8(9)/R.9, published deliberately - not personal data of the DPO
    harvested from somewhere else.
    """
    parties: list[dict] = []
    org = db.get(Organization, artefact.tenant_id)
    if org is not None:
        parties.append(
            {
                "party_id": org.code,
                "party_name": org.name,
                "party_type": "ORGANIZATION",
                "party_role": "PII_CONTROLLER",
                "party_contact": org.dpo_name or "",
                "party_email": org.dpo_email or "",
                "party_phone": org.dpo_phone or "",
                "party_url": org.rights_url or "",
                "party_address": "",
                # DPDP-specific attributes of the same party. Namespaced with
                # an x_ prefix because TS 27560 has no slot for any of them.
                "x_dpdp_role": "DATA_FIDUCIARY",
                "x_dpdp_withdrawal_url": org.withdraw_url or "",
                "x_dpdp_rights_url": org.rights_url or "",
                "x_dpdp_grievance_url": org.grievance_url or "",
                "x_dpdp_grievance_response_days": org.grievance_response_days,
                "x_dpdp_board_complaint_url": org.board_complaint_url or "",
            }
        )
    if ctx.consent_manager is not None:
        cm = ctx.consent_manager
        parties.append(
            {
                "party_id": cm.cm_ref,
                "party_name": cm.name,
                "party_type": "ORGANIZATION",
                # Not a TS 27560 role - the standard has no Consent Manager
                # concept at all - so it is prefixed to say so.
                "party_role": "x_dpdp_CONSENT_MANAGER",
                "party_contact": "",
                "party_email": cm.contact_email or "",
                "party_phone": "",
                "party_url": cm.website_url or "",
                "party_address": "",
                "x_dpdp_board_registration_number": cm.board_registration_number or "",
                "x_dpdp_registration_status": cm.registration_status,
                "x_dpdp_data_blind": True,
            }
        )
    return parties


def _purpose_entry(consent: Consent, *, controller_party_id: str) -> dict:
    """One entry of the `purposes` array in the PII processing section.

    Field names here are from TS 27560 Table 2, which the ISO preview covers:
    `purpose`, `purpose_type`, `lawful_basis`, `pii_information`,
    `pii_controllers`, `collection_method`, `processing_method`,
    `storage_locations`, `retention_period`. `pii_information` sub-field names
    (`pii_type`, `pii_attribute_id`, ...) are from the DPV guide and are NOT
    verified against the ISO text.

    Personal-data *categories* are named because a consent that does not say
    what it covers is not specific (s.6(1)). No personal-data *value* appears
    here - that is the data-blind boundary this module's docstring describes.
    """
    purpose = consent.purpose
    pv = consent.purpose_version
    notice_version = consent.notice_version
    return {
        "purpose": purpose.name,
        "purpose_type": purpose.code,
        # TS 27560 marks lawful_basis optional and assumes consent; DPDP does
        # not - s.4 requires a gateway to be named, so it always is here.
        "lawful_basis": purpose.legal_basis,
        "pii_information": [
            {
                "pii_type": consent.data_category.code,
                "pii_attribute_id": consent.data_category.code,
                "pii_optional": not purpose.requires_consent,
                "sensitive_pii_category": None,
                "special_pii_category": None,
                "x_dpdp_name": consent.data_category.name,
            }
        ],
        "pii_controllers": [controller_party_id],
        "collection_method": consent.collection_method,
        "processing_method": consent.processing_activity.code,
        # Data residency is configured per deployment, not per consent, and
        # this platform does not yet record a per-purpose storage location
        # (gap E-0x). Emitting a guess in a REQUIRED standard field would be
        # worse than emitting an explicit "not recorded".
        "storage_locations": [],
        "retention_period": {
            "x_days": purpose.retention_period_days,
            "x_expires_at": consent.expires_at.isoformat() if consent.expires_at else None,
        },
        # --- Consent360 extensions to the purpose entry ----------------- #
        "x_consent360": {
            "purpose_code": purpose.code,
            "purpose_description": purpose.description or "",
            "purpose_version": pv.version_number if pv else None,
            "requires_consent": purpose.requires_consent,
            "services_enabled": (pv.services_enabled if pv and pv.services_enabled else purpose.services_enabled) or "",
            "child_restricted": purpose.child_restricted,
            "processing_activity_name": consent.processing_activity.name,
            "consent_record_id": consent.id,
            "consent_version": consent.consent_version,
            "state": consent.status,
            "granted": consent.status in ("GRANTED", "ACTIVE", "RENEWED", "UPDATED"),
            "notice": (
                {
                    "notice_version_id": notice_version.id,
                    "version_number": notice_version.version_number,
                    "content_hash": notice_version.content_hash,
                    "title": notice_version.title,
                    "language": notice_version.language_default,
                }
                if notice_version is not None
                else None
            ),
        },
    }


def build_artefact_payload(
    db: Session,
    artefact: ConsentArtefact,
    consents: list[Consent],
    *,
    event_type: str,
    artefact_version: int,
    ctx: BrokerContext,
    occurred_at: datetime,
    customer: Customer,
) -> dict:
    """The interoperable consent record.

    Organised into the six sections TS 27560 clause 6.3.2.2 says a consent
    record *should* have - record header, PII processing, event, purposes, PII
    information and party identification - using the standard\'s own field
    names where they are verifiable, and `x_`-prefixed names everywhere else so
    a reader can always tell which parts are the standard\'s and which are
    ours. The standard fixes the *field* names, not the *container* names for
    those sections (6.3.2.2 NOTE 2 explicitly leaves the arrangement to the
    implementer), so `pii_processing`, `event` and `parties` are our containers,
    documented as such.

    Read `ARTEFACT_CONFORMANCE_STATEMENT` before repeating any conformance
    claim: TS 27560 is an information model with informative-only JSON
    examples, no normative schema and no validator, so this payload carries
    the standard\'s information elements under our own published schema (which
    clause 6.3.2.1 requires us to publish - see
    `GET /consent-manager/artefact-schema`), and it has not been assessed for
    conformance by anyone.

    The `pii_principal_id` header field is where the data-blind decision is
    enforced. Emitting a pseudonym there is also what TS 27560 clause 6.3.3.4
    itself recommends: "organizations should consider using measures to prevent
    identification of the PII principal through using mechanisms such as
    pseudonyms".
    """
    parties = _party_blocks(db, artefact, ctx)
    controller_party_id = parties[0]["party_id"] if parties else artefact.source_app

    payload: dict = {
        # === Record header (TS 27560 Table 1 - all three REQUIRED) ====== #
        "schema_version": ARTEFACT_SCHEMA_VERSION,
        "record_id": artefact.artefact_ref,
        "pii_principal_id": artefact.principal_ref,
        # === PII processing section (TS 27560 Table 2) ================== #
        "pii_processing": {
            "privacy_notice": _notice_reference(consents),
            "language": (consents[0].evidence[-1].language if consents and consents[0].evidence else "en"),
            "purposes": [
                _purpose_entry(c, controller_party_id=controller_party_id) for c in consents
            ],
        },
        # === Party identification section =============================== #
        "parties": parties,
        # === Event section ============================================== #
        "event": {
            "entity_id": ctx.actor,
            "event_type": event_type,
            "event_time": occurred_at.isoformat(),
            "event_state": artefact.status,
            "validity_duration": (
                artefact.expires_at.isoformat() if artefact.expires_at else None
            ),
        },
        # === Consent360 extensions ====================================== #
        # Everything below this line is ours, not the standard\'s. Kept in one
        # namespaced block so no reader can mistake an extension for a
        # standard field.
        "x_consent360": {
            "schema_reference": ARTEFACT_SCHEMA_REFERENCE,
            "modelled_on": ARTEFACT_SCHEMA_MODELLED_ON,
            "conformance": ARTEFACT_CONFORMANCE_STATEMENT,
            "record_version": artefact_version,
            "jurisdiction": "IN",
            "artefact_status": artefact.status,
            "source_app": artefact.source_app,
            "consent_manager_ref": ctx.consent_manager_ref,
            "data_blind": ctx.data_blind,
            "created_at": artefact.created_at.isoformat() if artefact.created_at else occurred_at.isoformat(),
            "expires_at": artefact.expires_at.isoformat() if artefact.expires_at else None,
            "withdrawn_at": artefact.withdrawn_at.isoformat() if artefact.withdrawn_at else None,
            "collection_method": CONSENT_MANAGER_COLLECTION_METHOD if ctx.is_consent_manager else "API",
        },
        "x_dpdp": {
            "act": "Digital Personal Data Protection Act, 2023",
            "consent_section": "s.6",
            "consent_manager_section": "s.6(7)",
            "rules": "Digital Personal Data Protection Rules, 2025",
            "record_retention_years": 7,
            "record_retention_basis": "First Schedule Part B 3 and 4(c)",
        },
        # Machine-to-machine sharing semantics from the MeitY BRD sec.4.1.1
        # and the DEPA / RBI Account Aggregator artefact. None of these exist
        # in TS 27560.
        "x_sharing": {
            "revocable": True,
            "data_life_days": max(
                [c.purpose.retention_period_days for c in consents if c.purpose] or [0]
            )
            or None,
            "frequency": "ON_DEMAND",
            "access_mode": "QUERY",
            "notification_url": None,
        },
    }
    if not ctx.data_blind:
        # The fiduciary reading its own tenant. It already holds this; see the
        # data-blind decision in this module\'s docstring for why it is present
        # here and absent for a Consent Manager.
        payload["x_consent360"]["pii_principal_external_id"] = customer.external_id
        payload["x_consent360"]["pii_principal_email_masked"] = mask_identifier(customer.email)
    return payload


def _notice_reference(consents: list[Consent]) -> Optional[dict]:
    """TS 27560 Table 2 makes `privacy_notice` a REQUIRED field of the PII
    processing section, and clause 6.2.2.6 requires a unique reference to the
    *specific version* of information expected to change over time, naming
    privacy notices explicitly. The notice version pinned on the consent rows
    is exactly that reference; `None` when no notice has been published for
    the purpose yet, which is a stated absence rather than a fabricated id."""
    for consent in consents:
        nv = consent.notice_version
        if nv is not None:
            return {
                "notice_version_id": nv.id,
                "version_number": nv.version_number,
                "content_hash": nv.content_hash,
                "language": nv.language_default,
                "title": nv.title,
            }
    return None


def canonical_payload_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def sign_event(payload: dict) -> tuple[str, str]:
    """Return (payload_hash, signature).

    Uses `app.core.encryption.hmac_signature` - the same keyed primitive that
    already signs `ConsentEvidence` and `ConsentReceipt`. Verification is
    `hmac_signature_matches`, which accepts any key in the search-digest key
    ring, so rotating the HMAC key does not turn every historical artefact
    into a false tamper alarm.
    """
    payload_hash = canonical_payload_hash(payload)
    return payload_hash, hmac_signature(payload_hash)


def verify_event(event: ConsentArtefactEvent) -> bool:
    """True only if the stored payload still hashes to `payload_hash` AND that
    hash still verifies under some key in the ring."""
    if canonical_payload_hash(event.payload) != event.payload_hash:
        return False
    return hmac_signature_matches(event.signature, event.payload_hash)


# --------------------------------------------------------------------------- #
#  Artefact operations - facades over app/services/consent.py
# --------------------------------------------------------------------------- #

def _client_context(ctx: BrokerContext, *, language: str, reference: Optional[str]) -> ClientContext:
    """The evidence context for a consent captured through this API.

    `affirmative_action` stays "CLICK": the principal really did click, in the
    Consent Manager's interface. What makes the evidence honest is
    `affirmative_reference`, which records WHOSE interface and WHICH
    interaction the CM is attesting to - the route requires a CM caller to
    supply one, because an intermediary asserting that somebody consented
    with no reference behind it is a claim, not evidence.
    """
    return ClientContext(
        language=language,
        affirmative_action="CLICK",
        affirmative_reference=(
            f"{ctx.actor}|{reference}" if reference else None
        ),
        interaction_step=1,
    )


def _consents_for_purpose(
    db: Session,
    customer: Customer,
    purpose: Purpose,
    ctx: BrokerContext,
) -> list[Consent]:
    """Materialise the consent rows for one purpose at this fiduciary.

    Consent rows are per customer x purpose x data category x processing
    activity x source_app, so one artefact purpose expands to the cross
    product declared by the purpose's *current* version - the same expansion
    `integration.py::_ensure_source_consent_matrix` performs, reusing
    `get_or_create_consent(exact_source=True)` so a row owned by a different
    real source is never adopted.
    """
    pv = consent_service.get_current_purpose_version(purpose)
    cat_ids = pv.data_category_ids or []
    act_ids = pv.processing_activity_ids or []
    categories = (
        db.query(DataCategory).filter(DataCategory.id.in_(cat_ids)).all() if cat_ids else []
    )
    activities = (
        db.query(ProcessingActivity).filter(ProcessingActivity.id.in_(act_ids)).all()
        if act_ids
        else []
    )
    if not categories or not activities:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Purpose '{purpose.code}' has no data categories or processing activities in its "
                f"current version, so there is nothing to consent to."
            ),
        )
    out: list[Consent] = []
    for dc in categories:
        for pa in activities:
            consent, _created = consent_service.get_or_create_consent(
                db,
                customer,
                purpose,
                dc,
                pa,
                actor_username=ctx.actor,
                source_app=ctx.fiduciary_source_app,
                collection_method=CONSENT_MANAGER_COLLECTION_METHOD,
                exact_source=True,
            )
            out.append(consent)
    return out


_ACTIVE_CONSENT_STATUSES = ("GRANTED", "ACTIVE", "RENEWED", "UPDATED")


def _grant_all(
    db: Session,
    consents: list[Consent],
    ctx: BrokerContext,
    *,
    expires_in_days: Optional[int],
    reason: str,
    language: str,
    reference: Optional[str],
    request_id: Optional[str],
) -> None:
    client_context = _client_context(ctx, language=language, reference=reference)
    for consent in consents:
        if consent.status in _ACTIVE_CONSENT_STATUSES:
            # Already granted through some other channel. Linking it is
            # correct (the artefact records the state that exists); calling
            # grant_consent again would be rejected by the lifecycle's own
            # transition validation, which is exactly the check that should
            # not be worked around.
            continue
        consent_service.grant_consent(
            db,
            consent,
            expires_in_days=expires_in_days,
            reason=reason or "Consent given through a Consent Manager",
            actor_username=ctx.actor,
            source_app=ctx.fiduciary_source_app,
            collection_method=CONSENT_MANAGER_COLLECTION_METHOD,
            request_id=request_id,
            client_context=client_context,
            actor_type="CONSENT_MANAGER" if ctx.is_consent_manager else "SYSTEM",
            actor_id=ctx.consent_manager_ref,
        )


def _withdraw_all(
    db: Session,
    consents: list[Consent],
    ctx: BrokerContext,
    *,
    reason: str,
    reference: Optional[str],
    request_id: Optional[str],
) -> None:
    client_context = _client_context(ctx, language="en", reference=reference)
    for consent in consents:
        if consent.status not in _ACTIVE_CONSENT_STATUSES:
            continue
        consent_service.withdraw_consent(
            db,
            consent,
            reason=reason or "Consent withdrawn through a Consent Manager",
            actor_username=ctx.actor,
            source_app=ctx.fiduciary_source_app,
            collection_method=CONSENT_MANAGER_COLLECTION_METHOD,
            request_id=request_id,
            client_context=client_context,
            actor_type="CONSENT_MANAGER" if ctx.is_consent_manager else "SYSTEM",
            actor_id=ctx.consent_manager_ref,
        )


def _record_event(
    db: Session,
    artefact: ConsentArtefact,
    consents: list[Consent],
    ctx: BrokerContext,
    customer: Customer,
    *,
    event_type: str,
    request_id: Optional[str],
) -> ConsentArtefactEvent:
    """Append one immutable, signed version of the artefact."""
    occurred_at = utcnow()
    payload = build_artefact_payload(
        db,
        artefact,
        consents,
        event_type=event_type,
        artefact_version=artefact.artefact_version,
        ctx=ctx,
        occurred_at=occurred_at,
        customer=customer,
    )
    payload_hash, signature = sign_event(payload)
    event = ConsentArtefactEvent(
        artefact_id=artefact.id,
        event_ref=f"CAE-{uuid.uuid4().hex[:20].upper()}",
        artefact_version=artefact.artefact_version,
        event_type=event_type,
        payload=payload,
        payload_hash=payload_hash,
        signature=signature,
        signature_alg="HMAC-SHA256",
        actor=ctx.actor,
        source_app=ctx.fiduciary_source_app,
        request_id=request_id,
        occurred_at=occurred_at,
    )
    db.add(event)
    db.flush()
    return event


def _resolve_purposes(db: Session, ctx: BrokerContext, purpose_codes: list[str]) -> list[Purpose]:
    resolved: list[Purpose] = []
    seen: set[str] = set()
    for code in purpose_codes:
        if code in seen:
            continue
        seen.add(code)
        assert_purpose_onboarded(ctx, code)
        purpose = (
            db.query(Purpose)
            .filter(Purpose.code == code, Purpose.is_active.is_(True))
            .first()
        )
        if not purpose:
            raise HTTPException(status_code=404, detail=f"Unknown or inactive purpose '{code}'.")
        resolved.append(purpose)
    return resolved


def _artefact_status(links: list[ConsentArtefactLink]) -> str:
    active = [link for link in links if link.status == "ACTIVE"]
    if not active:
        return "WITHDRAWN"
    if len(active) < len(links):
        return "PARTIAL"
    return "ACTIVE"


def create_artefact(
    db: Session,
    ctx: BrokerContext,
    customer: Customer,
    *,
    purpose_codes: list[str],
    expires_in_days: Optional[int],
    language: str,
    reference: Optional[str],
    reason: str,
    request_id: Optional[str],
) -> ConsentArtefact:
    """Give consent: grant every consent row the named purposes expand to, and
    record a signed version-1 artefact over them."""
    purposes = _resolve_purposes(db, ctx, purpose_codes)

    artefact = ConsentArtefact(
        artefact_ref=f"CA-{uuid.uuid4().hex[:20].upper()}",
        schema_version=ARTEFACT_SCHEMA_VERSION,
        tenant_id=ctx.fiduciary_tenant_id,
        source_app=ctx.fiduciary_source_app,
        consent_manager_id=ctx.consent_manager.id if ctx.consent_manager else None,
        customer_id=customer.id,
        principal_ref=principal_pseudonym(ctx.consent_manager_ref, customer.id),
        status="ACTIVE",
        artefact_version=1,
        created_by=ctx.actor,
        request_id=request_id,
    )
    db.add(artefact)
    db.flush()

    all_consents: list[Consent] = []
    for purpose in purposes:
        consents = _consents_for_purpose(db, customer, purpose, ctx)
        _grant_all(
            db,
            consents,
            ctx,
            expires_in_days=expires_in_days,
            reason=reason,
            language=language,
            reference=reference,
            request_id=request_id,
        )
        for consent in consents:
            db.add(
                ConsentArtefactLink(
                    artefact_id=artefact.id,
                    consent_id=consent.id,
                    purpose_id=purpose.id,
                    purpose_code=purpose.code,
                    status="ACTIVE",
                )
            )
        all_consents.extend(consents)

    db.flush()
    expiries = [c.expires_at for c in all_consents if c.expires_at]
    artefact.expires_at = min(expiries) if expiries else None

    _record_event(
        db, artefact, all_consents, ctx, customer, event_type="CREATED", request_id=request_id
    )
    log_audit(
        db,
        "ARTEFACT_CREATED",
        actor_username=ctx.actor,
        actor_type="CONSENT_MANAGER" if ctx.is_consent_manager else "SYSTEM",
        actor_id=ctx.consent_manager_ref,
        source_app=ctx.fiduciary_source_app,
        tenant_id=ctx.fiduciary_tenant_id,
        customer_id=customer.id,
        customer_external_id=customer.external_id,
        reason=f"Consent artefact {artefact.artefact_ref} created over {len(all_consents)} consent record(s)",
        request_id=request_id,
        metadata={
            "artefact_ref": artefact.artefact_ref,
            "consent_manager": ctx.consent_manager_ref,
            "purpose_codes": [p.code for p in purposes],
        },
        commit=False,
    )
    db.commit()
    db.refresh(artefact)
    return artefact


def update_artefact(
    db: Session,
    ctx: BrokerContext,
    artefact: ConsentArtefact,
    customer: Customer,
    *,
    purpose_codes: list[str],
    expires_in_days: Optional[int],
    reference: Optional[str],
    reason: str,
    request_id: Optional[str],
) -> ConsentArtefact:
    """Manage: revise the purpose set an artefact covers.

    `purpose_codes` is the desired end state. Purposes added are granted;
    purposes removed are withdrawn through `withdraw_consent`, never by
    deleting a link - the link stays, marked WITHDRAWN, because an artefact
    that silently forgot a purpose it once carried is not a record.
    """
    purposes = _resolve_purposes(db, ctx, purpose_codes)
    wanted = {p.code for p in purposes}
    existing_codes = {link.purpose_code for link in artefact.links if link.status == "ACTIVE"}

    artefact.artefact_version += 1
    now = utcnow()

    # Withdraw the purposes no longer wanted.
    dropped = existing_codes - wanted
    if dropped:
        dropped_links = [
            link for link in artefact.links if link.status == "ACTIVE" and link.purpose_code in dropped
        ]
        consents = [db.get(Consent, link.consent_id) for link in dropped_links]
        _withdraw_all(
            db,
            [c for c in consents if c is not None],
            ctx,
            reason=reason or "Purpose removed from the consent artefact",
            reference=reference,
            request_id=request_id,
        )
        for link in dropped_links:
            link.status = "WITHDRAWN"
            link.withdrawn_at = now

    # Grant the purposes newly added.
    for purpose in purposes:
        if purpose.code in existing_codes:
            continue
        consents = _consents_for_purpose(db, customer, purpose, ctx)
        _grant_all(
            db,
            consents,
            ctx,
            expires_in_days=expires_in_days,
            reason=reason or "Purpose added to the consent artefact",
            language="en",
            reference=reference,
            request_id=request_id,
        )
        for consent in consents:
            existing_link = next(
                (link for link in artefact.links if link.consent_id == consent.id), None
            )
            if existing_link is not None:
                existing_link.status = "ACTIVE"
                existing_link.withdrawn_at = None
            else:
                db.add(
                    ConsentArtefactLink(
                        artefact_id=artefact.id,
                        consent_id=consent.id,
                        purpose_id=purpose.id,
                        purpose_code=purpose.code,
                        status="ACTIVE",
                    )
                )
    db.flush()
    db.refresh(artefact)

    active_consents = _linked_consents(db, artefact, active_only=True)
    artefact.status = _artefact_status(list(artefact.links))
    expiries = [c.expires_at for c in active_consents if c.expires_at]
    artefact.expires_at = min(expiries) if expiries else None

    _record_event(
        db,
        artefact,
        _linked_consents(db, artefact),
        ctx,
        customer,
        event_type="UPDATED",
        request_id=request_id,
    )
    log_audit(
        db,
        "ARTEFACT_UPDATED",
        actor_username=ctx.actor,
        actor_type="CONSENT_MANAGER" if ctx.is_consent_manager else "SYSTEM",
        actor_id=ctx.consent_manager_ref,
        source_app=ctx.fiduciary_source_app,
        tenant_id=ctx.fiduciary_tenant_id,
        customer_id=customer.id,
        customer_external_id=customer.external_id,
        reason=f"Consent artefact {artefact.artefact_ref} revised to version {artefact.artefact_version}",
        request_id=request_id,
        metadata={
            "artefact_ref": artefact.artefact_ref,
            "purpose_codes": sorted(wanted),
            "withdrawn_purpose_codes": sorted(dropped),
        },
        commit=False,
    )
    db.commit()
    db.refresh(artefact)
    return artefact


def withdraw_artefact(
    db: Session,
    ctx: BrokerContext,
    artefact: ConsentArtefact,
    customer: Customer,
    *,
    purpose_codes: Optional[list[str]],
    reference: Optional[str],
    reason: str,
    request_id: Optional[str],
) -> ConsentArtefact:
    """Withdraw the artefact, wholly or for named purposes only."""
    targets = [link for link in artefact.links if link.status == "ACTIVE"]
    if purpose_codes:
        wanted = set(purpose_codes)
        unknown = wanted - {link.purpose_code for link in artefact.links}
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"Artefact {artefact.artefact_ref} does not cover purpose(s): {sorted(unknown)}",
            )
        targets = [link for link in targets if link.purpose_code in wanted]

    now = utcnow()
    consents = [db.get(Consent, link.consent_id) for link in targets]
    _withdraw_all(
        db,
        [c for c in consents if c is not None],
        ctx,
        reason=reason or "Consent withdrawn through a Consent Manager",
        reference=reference,
        request_id=request_id,
    )
    for link in targets:
        link.status = "WITHDRAWN"
        link.withdrawn_at = now

    artefact.artefact_version += 1
    artefact.status = _artefact_status(list(artefact.links))
    if artefact.status == "WITHDRAWN":
        artefact.withdrawn_at = now
    db.flush()

    _record_event(
        db,
        artefact,
        _linked_consents(db, artefact),
        ctx,
        customer,
        event_type="WITHDRAWN",
        request_id=request_id,
    )
    log_audit(
        db,
        "ARTEFACT_WITHDRAWN",
        actor_username=ctx.actor,
        actor_type="CONSENT_MANAGER" if ctx.is_consent_manager else "SYSTEM",
        actor_id=ctx.consent_manager_ref,
        source_app=ctx.fiduciary_source_app,
        tenant_id=ctx.fiduciary_tenant_id,
        customer_id=customer.id,
        customer_external_id=customer.external_id,
        reason=f"Consent artefact {artefact.artefact_ref} withdrawn ({artefact.status})",
        request_id=request_id,
        metadata={
            "artefact_ref": artefact.artefact_ref,
            "withdrawn_purpose_codes": sorted({link.purpose_code for link in targets}),
            "artefact_status": artefact.status,
        },
        commit=False,
    )
    db.commit()
    db.refresh(artefact)
    return artefact


def _linked_consents(
    db: Session, artefact: ConsentArtefact, *, active_only: bool = False
) -> list[Consent]:
    links = [
        link for link in artefact.links if not active_only or link.status == "ACTIVE"
    ]
    if not links:
        return []
    rows = (
        db.query(Consent)
        .filter(Consent.id.in_([link.consent_id for link in links]))
        .order_by(Consent.purpose_id.asc(), Consent.data_category_id.asc(), Consent.id.asc())
        .all()
    )
    # Belt-and-braces: a Consent row is only ever linked by an operation that
    # already scoped it to this artefact's fiduciary, but re-checking here
    # means a link that ever pointed outside its own tenant cannot leak
    # through a read path either.
    return [c for c in rows if c.source_app == artefact.source_app]


def artefact_query(db: Session, ctx: BrokerContext):
    """Every artefact read starts here, so the scope is applied once.

    Two independent filters for a Consent Manager caller: the artefact must
    belong to the fiduciary it is authorised for AND have been created by
    this CM. A fiduciary acting for itself sees the artefacts on its own
    tenant, including ones a CM brokered for it - it is the controller of
    that data.
    """
    q = db.query(ConsentArtefact).filter(
        ConsentArtefact.source_app == ctx.fiduciary_source_app,
        ConsentArtefact.tenant_id == ctx.fiduciary_tenant_id,
    )
    if ctx.is_consent_manager:
        q = q.filter(ConsentArtefact.consent_manager_id == ctx.consent_manager.id)
    return q


def get_artefact_or_404(db: Session, ctx: BrokerContext, artefact_ref: str) -> ConsentArtefact:
    artefact = artefact_query(db, ctx).filter(
        ConsentArtefact.artefact_ref == artefact_ref
    ).first()
    if not artefact:
        # An artefact belonging to another fiduciary, or brokered by another
        # Consent Manager, is indistinguishable from one that does not exist.
        raise HTTPException(status_code=404, detail="Consent artefact not found")
    return artefact


# --------------------------------------------------------------------------- #
#  CM-06 / K-42 / K-43 metrics
# --------------------------------------------------------------------------- #

@contextmanager
def measure(db: Session, *, endpoint: str, method: str, request_id: Optional[str] = None):
    """Sample one CM-facing call for the availability/latency KPIs.

    Yields a mutable dict the route fills in (`consent_manager_id`,
    `tenant_id`) once it knows them - they are unknown until the broker
    context is resolved, which is itself part of the call being measured.

    Recording a sample must never be able to fail the request it is measuring,
    and must never be the reason a failing request returns a different error:
    on the error path the session is rolled back first (the route is aborting
    anyway) and any failure to persist the sample is logged and swallowed.
    """
    started = time.perf_counter()
    holder: dict = {
        "status_code": 200,
        "consent_manager_id": None,
        "tenant_id": None,
        # Exposed so a route that wants to report its own latency back to the
        # caller measures the same interval this sample records, rather than
        # timing a second, slightly different one.
        "started": started,
    }
    try:
        yield holder
    except HTTPException as exc:
        holder["status_code"] = exc.status_code
        raise
    except Exception:
        holder["status_code"] = 500
        raise
    finally:
        duration_ms = int((time.perf_counter() - started) * 1000)
        status_code = int(holder.get("status_code") or 200)
        outcome = (
            "SUCCESS" if status_code < 400 else "CLIENT_ERROR" if status_code < 500 else "SERVER_ERROR"
        )
        try:
            if status_code >= 400:
                db.rollback()
            db.add(
                ConsentManagerApiCall(
                    consent_manager_id=holder.get("consent_manager_id"),
                    tenant_id=holder.get("tenant_id"),
                    endpoint=endpoint,
                    method=method,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    outcome=outcome,
                    request_id=request_id,
                )
            )
            db.commit()
        except Exception:  # pragma: no cover - a metrics write must never mask the real outcome
            logger.warning("Could not record a Consent Manager API sample for %s", endpoint, exc_info=True)
            try:
                db.rollback()
            except Exception:
                pass


def percentile(values: list[int], pct: float) -> Optional[int]:
    """Nearest-rank percentile. Small sample sizes are the norm here (an
    availability report over an hour of a low-traffic tenant), and
    interpolating between two samples would invent a latency nobody
    observed."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-len(ordered) * pct // 100))))
    return ordered[rank - 1]
