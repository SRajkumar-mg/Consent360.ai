"""R1-14 (F-01..F-06): the s.9 children-and-guardians service.

Everything in this module exists to answer two questions, and they are
deliberately answered by two separate functions that do not call each other:

  1. **May a consent be recorded for this principal at all?**
     s.9(1) / R.10: not for a child, unless there is a VERIFIED
     `guardian_consents` row. `assert_consent_permitted` is the gate, and it
     is called from `app/services/consent.py` - the single choke point every
     lifecycle transition already passes through - rather than from any route,
     because a rule enforced in a UI is a rule with a back door.

  2. **May this processing happen at all, consent or no consent?**
     s.9(3): tracking, behavioural monitoring and targeted advertising
     directed at children are *prohibited*, not consent-gated. A parent cannot
     authorise them. `child_prohibition_for` answers this, and it is called
     from `app/services/decision_engine.py` at a point that outranks a valid
     granted consent.

Keeping them apart matters. If the prohibition were expressed as "denied
unless a guardian consent exists", then the day someone recorded a parental
consent the platform would start serving targeted advertising to a child - the
most expensive single mistake available under this Act. `child_prohibition_for`
never reads `guardian_consents` at all, and the only thing that can lift it is
a Fourth Schedule exemption the tenant has explicitly configured and
authorised.

**On the Digital Locker route (R.10(2)(c)).** This build ships the interface
and no integration. `VIRTUAL_TOKEN_VERIFIERS` is an empty registry; a
deployment registers a real adapter for its authorised entity. With no adapter
registered, `verify_guardian_consent` refuses the record with HTTP 501 and the
row stays PENDING, which reads as "no verifiable parental consent" at every
enforcement point. Nothing here fabricates a verification, and the database
constraint `ck_guardian_consents_virtual_token_verified_has_moment` means
nothing downstream can either.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date, datetime, timezone
from typing import Callable, Optional, Protocol

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.guardian import (
    CHILD_AGE_THRESHOLD_YEARS,
    GuardianConsent,
    PrincipalAgeAssurance,
    TenantChildExemption,
)
from app.services.audit import log_audit

# --------------------------------------------------------------------------- #
#  Vocabulary shared with the enforcement points
# --------------------------------------------------------------------------- #

OBLIGATION_PARENTAL_CONSENT = "VERIFIABLE_PARENTAL_CONSENT"
OBLIGATION_TRACKING_AND_ADS = "TRACKING_AND_ADVERTISING_PROHIBITION"

#: The purposes s.9(3) reaches, expressed in this platform's seeded cookie
#: taxonomy (`seed.py`: strictly_necessary / functional / analytics /
#: advertising). "advertising" is targeted advertising; "analytics" is
#: behavioural monitoring. These are exactly the two the compliance register's
#: F-04 row names.
DEFAULT_CHILD_PROHIBITED_PURPOSE_CODES = frozenset({"advertising", "analytics"})

#: And the same prohibition expressed at the processing-activity level, so a
#: tenant that files behavioural monitoring under a differently-named purpose
#: is still caught. `personalize_offers` is the personalisation limb;
#: `analytics` is the tracking/behavioural-monitoring limb.
#:
#: Deliberately NOT including `send_marketing_email` / `send_sms` /
#: `third_party_sharing`: none of those is *by itself* tracking, behavioural
#: monitoring or targeted advertising (a school emailing a pupil, a payroll
#: processor), and a prohibition that over-reaches into ordinary service
#: communication would be routed around rather than obeyed. When they ARE
#: advertising, the purpose-level match above catches them, because the
#: purpose is what states the intent.
DEFAULT_CHILD_PROHIBITED_ACTIVITY_CODES = frozenset({"analytics", "personalize_offers"})

#: Per-tenant additions, read from `Organization.settings`. A tenant can only
#: ADD to the prohibited set - see `_prohibited_codes_for_tenant`. There is no
#: configuration anywhere in this module that removes a purpose from the
#: prohibition, because s.9(3) is not the tenant's to waive; the only lawful
#: relaxation is a Fourth Schedule exemption, which is a separate, authorised,
#: purpose-by-purpose record.
SETTINGS_KEY_PROHIBITED_PURPOSES = "child_prohibited_purpose_codes"
SETTINGS_KEY_PROHIBITED_ACTIVITIES = "child_prohibited_activity_codes"

_REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_REFERENCE_LENGTH = 12


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Same normalisation `app/services/rights_requests.py::as_utc` applies -
    a naive datetime is read as UTC, an aware one is converted - so a hash
    computed here is reproducible from a row Postgres hands back in a
    different session timezone."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
#  Age arithmetic
# --------------------------------------------------------------------------- #


def parse_iso_date(value: Optional[str]) -> Optional[date]:
    """Parse the ISO-8601 string an encrypted date column stores."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def age_on(dob: date, *, on: Optional[date] = None) -> int:
    """Completed years, the way s.2(f) counts them ("has not completed
    eighteen years of age"). Birthday-aware, not a 365.25 division: someone
    whose eighteenth birthday is tomorrow is still a child today, and rounding
    them up would be the exact failure this whole module exists to prevent."""
    today = on or utcnow().date()
    years = today.year - dob.year
    if (today.month, today.day) < (dob.month, dob.day):
        years -= 1
    return years


def is_child_by_dob(dob: date, *, on: Optional[date] = None) -> bool:
    return age_on(dob, on=on) < CHILD_AGE_THRESHOLD_YEARS


# --------------------------------------------------------------------------- #
#  References and evidence hashes
# --------------------------------------------------------------------------- #


def _random_reference(prefix: str, now: datetime) -> str:
    body = "".join(secrets.choice(_REFERENCE_ALPHABET) for _ in range(_REFERENCE_LENGTH))
    return f"{prefix}-{now.year}-{body[:4]}-{body[4:8]}-{body[8:]}"


def generate_reference_no(db: Session, *, now: Optional[datetime] = None) -> str:
    """A unique, non-guessable handle for a guardian-consent record.

    Same three properties as `rights_requests.generate_reference_no`, and the
    middle one matters more here than anywhere else in the codebase: a
    sequential reference on this table would publish **how many children a
    tenant has on its books**, and would let anyone holding one reference walk
    to their neighbours'. `secrets`, never `random`.
    """
    when = now or utcnow()
    for _ in range(8):
        candidate = _random_reference("GRD", when)
        if (
            db.query(GuardianConsent.id)
            .filter(GuardianConsent.reference_no == candidate)
            .first()
            is None
        ):
            return candidate
    raise RuntimeError("Could not generate a unique GRD reference")


def _canonical_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_guardian_evidence_hash(record: GuardianConsent) -> str:
    """SHA-256 over the canonical JSON of *who consented, how they were
    verified, and when* - the three facts a challenged fiduciary has to be
    able to produce.

    The same value goes into the append-only, hash-chained
    GUARDIAN_CONSENT_VERIFIED audit row, so an after-the-fact edit to
    `guardian_consents` is detectable by recomputing this and comparing
    against the ledger.

    Encrypted narrative columns are deliberately excluded, for the reason
    `rights_requests.compute_closure_hash` documents: they are stored under a
    key that may be rotated, and a hash whose inputs can legitimately change
    ciphertext is not a checkable hash. The *identity* of the guardian is
    covered by hashing the HMAC search digest of their email plus the linked
    account id, both of which are stable.
    """
    payload = {
        "reference_no": record.reference_no,
        "customer_id": record.customer_id,
        "guardian_type": record.guardian_type,
        "verification_method": record.verification_method,
        "guardian_is_adult": bool(record.guardian_is_adult),
        "guardian_email_search": record.guardian_email_search,
        "guardian_customer_id": record.guardian_customer_id,
        "guardian_context_id": record.guardian_context_id,
        "virtual_token_issuer": record.virtual_token_issuer,
        "virtual_token_verified_at": (
            as_utc(record.virtual_token_verified_at).isoformat()
            if record.virtual_token_verified_at
            else None
        ),
        "appointment_authority": record.appointment_authority,
        "appointment_reference": record.appointment_reference,
        "appointment_date": (
            record.appointment_date.isoformat() if record.appointment_date else None
        ),
        "verified_at": (
            as_utc(record.verified_at).isoformat() if record.verified_at else None
        ),
        "verified_by": record.verified_by,
        "source_app": record.source_app,
    }
    return _canonical_hash(payload)


# --------------------------------------------------------------------------- #
#  R.10(2)(c): the virtual-token interface. A PLACEHOLDER, on purpose.
# --------------------------------------------------------------------------- #


class VirtualTokenVerifier(Protocol):
    """The adapter a deployment registers for its authorised entity.

    Called with the token the caller presented and must return True only if
    the issuing entity confirmed that the token maps to a **verified adult
    identity**. Raising is fine; anything other than a True return leaves the
    guardian-consent record PENDING.
    """

    def __call__(self, *, issuer: str, token: str) -> bool:  # pragma: no cover
        ...


#: Empty in this build, and that is the honest state of the integration.
#: DigiLocker (and any other entity "entrusted by law" or "authorised by a
#: Digital Locker service provider" under R.10(2)(c)) requires a real,
#: contracted API relationship that this platform does not have; a stub that
#: returned True would be a fabricated verification for a child, sitting
#: behind a database column that says the fiduciary did due diligence.
VIRTUAL_TOKEN_VERIFIERS: dict[str, VirtualTokenVerifier] = {}


def register_virtual_token_verifier(issuer: str, verifier: VirtualTokenVerifier) -> None:
    """Register the adapter for one authorised entity. Deployment wiring."""
    VIRTUAL_TOKEN_VERIFIERS[issuer.strip().upper()] = verifier


def _verify_virtual_token(issuer: str, token: Optional[str]) -> datetime:
    key = (issuer or "").strip().upper()
    verifier: Optional[Callable] = VIRTUAL_TOKEN_VERIFIERS.get(key)
    if verifier is None:
        raise HTTPException(
            status_code=501,
            detail=(
                f"No virtual-token verifier is registered for issuer '{issuer}'. "
                "R.10(2)(c) verification via a Digital Locker service provider is an "
                "interface in this build with no integration behind it: the guardian "
                "consent record stays PENDING and counts as no parental consent at "
                "all. Use EXISTING_VERIFIED_ACCOUNT or VOLUNTARILY_PROVIDED_DETAILS, "
                "or register a verifier for this issuer."
            ),
        )
    if not token:
        raise HTTPException(status_code=422, detail="A virtual token is required for this method")
    if not verifier(issuer=key, token=token):
        raise HTTPException(
            status_code=422,
            detail=f"Issuer '{issuer}' did not confirm this virtual token as a verified adult identity",
        )
    return utcnow()


# --------------------------------------------------------------------------- #
#  Reads
# --------------------------------------------------------------------------- #


def get_age_assurance(db: Session, customer_id: int) -> Optional[PrincipalAgeAssurance]:
    return (
        db.query(PrincipalAgeAssurance)
        .filter(PrincipalAgeAssurance.customer_id == customer_id)
        .first()
    )


def verified_guardian_consent(
    db: Session, customer_id: int, *, guardian_type: Optional[str] = None
) -> Optional[GuardianConsent]:
    """The VERIFIED guardian-consent record for this principal, if any.

    PENDING, REJECTED and REVOKED are all "no parental consent". That is what
    makes revocation immediate: nothing has to be cleaned up elsewhere,
    because every enforcement point asks this question fresh.
    """
    query = db.query(GuardianConsent).filter(
        GuardianConsent.customer_id == customer_id,
        GuardianConsent.status == "VERIFIED",
    )
    if guardian_type:
        query = query.filter(GuardianConsent.guardian_type == guardian_type)
    return query.order_by(GuardianConsent.verified_at.desc()).first()


def find_exemption(
    db: Session,
    *,
    tenant_id: Optional[int],
    purpose_code: str,
    obligation: str,
    now: Optional[datetime] = None,
) -> Optional[TenantChildExemption]:
    """An in-force R.12 / Fourth Schedule exemption for this tenant that
    relaxes `obligation` for `purpose_code` - or None.

    Scoped to one tenant by construction (`tenant_id` is NOT NULL on the
    table), and narrowed again by purpose and by obligation inside
    `TenantChildExemption.covers`. A tenant with no `tenant_id` resolved -
    which should not happen, but is representable - gets no exemption at all
    rather than everyone's.
    """
    if tenant_id is None:
        return None
    when = now or utcnow()
    rows = (
        db.query(TenantChildExemption)
        .filter(
            TenantChildExemption.tenant_id == tenant_id,
            TenantChildExemption.is_active.is_(True),
        )
        .all()
    )
    for row in rows:
        if row.covers(purpose_code, obligation, now=when):
            return row
    return None


def _prohibited_codes_for_tenant(db: Session, tenant_id: Optional[int]) -> tuple[frozenset, frozenset]:
    """The s.9(3) purpose/activity code sets for one tenant.

    The tenant's `Organization.settings` may ADD codes. It cannot remove any:
    the union is taken with the defaults, never a replacement. A tenant that
    could delete "advertising" from this set would have switched off a
    statutory prohibition with a settings edit.
    """
    purposes = set(DEFAULT_CHILD_PROHIBITED_PURPOSE_CODES)
    activities = set(DEFAULT_CHILD_PROHIBITED_ACTIVITY_CODES)
    if tenant_id is None:
        return frozenset(purposes), frozenset(activities)
    # Imported here rather than at module scope: this module is registered
    # into Base.metadata from app/models/__init__.py, which runs BEFORE
    # app/models/entities.py is imported.
    from app.models.entities import Organization

    org = db.query(Organization).filter(Organization.id == tenant_id).first()
    settings = (org.settings or {}) if org else {}
    extra_purposes = settings.get(SETTINGS_KEY_PROHIBITED_PURPOSES) or []
    extra_activities = settings.get(SETTINGS_KEY_PROHIBITED_ACTIVITIES) or []
    if isinstance(extra_purposes, (list, tuple, set)):
        purposes.update(str(c) for c in extra_purposes)
    if isinstance(extra_activities, (list, tuple, set)):
        activities.update(str(c) for c in extra_activities)
    return frozenset(purposes), frozenset(activities)


# --------------------------------------------------------------------------- #
#  s.9(3): the prohibition
# --------------------------------------------------------------------------- #


class ChildProhibition:
    """Why this processing is prohibited for this child, and under what."""

    def __init__(self, *, ground: str, reason: str, matched_on: str):
        self.ground = ground
        self.reason = reason
        self.matched_on = matched_on

    def as_details(self) -> dict:
        return {
            "child_prohibition": True,
            "ground": self.ground,
            "matched_on": self.matched_on,
        }


def child_prohibition_for(
    db: Session,
    *,
    customer_id: int,
    tenant_id: Optional[int],
    purpose_code: str,
    purpose_name: str,
    processing_activity_code: str,
    purpose_child_restricted: bool = False,
    now: Optional[datetime] = None,
) -> Optional[ChildProhibition]:
    """s.9(3): tracking, behavioural monitoring and targeted advertising
    directed at a child are prohibited. Returns the prohibition, or None.

    **This function never looks at `guardian_consents`, and that is the
    point.** s.9(3) is not an obligation consent discharges - a parent has no
    power to authorise targeted advertising to their child, because the Act
    does not make it authorisable. Wiring a guardian-consent lookup in here
    would mean that the moment a parental consent was recorded the platform
    began serving ads to a child.

    The only thing that lifts it is a Fourth Schedule exemption the tenant has
    configured, authorised and scoped to this exact purpose - R.12 - which is
    checked last so that the audit trail shows the prohibition applied and was
    then displaced by a named, cited exemption.

    A principal with no age-assurance record, or one assessed as an adult, is
    not a child, so this returns None and nothing about existing behaviour
    changes.
    """
    assurance = get_age_assurance(db, customer_id)
    if assurance is None or not assurance.is_child:
        return None

    prohibited_purposes, prohibited_activities = _prohibited_codes_for_tenant(db, tenant_id)

    matched_on = None
    if purpose_child_restricted:
        matched_on = f"purpose.child_restricted ({purpose_code})"
    elif purpose_code in prohibited_purposes:
        matched_on = f"purpose_code={purpose_code}"
    elif processing_activity_code in prohibited_activities:
        matched_on = f"processing_activity_code={processing_activity_code}"
    if matched_on is None:
        return None

    exemption = find_exemption(
        db,
        tenant_id=tenant_id,
        purpose_code=purpose_code,
        obligation=OBLIGATION_TRACKING_AND_ADS,
        now=now,
    )
    if exemption is not None:
        return None

    return ChildProhibition(
        ground="DPDP Act s.9(3)",
        matched_on=matched_on,
        reason=(
            f"{purpose_name} is tracking, behavioural monitoring or targeted advertising "
            f"directed at a child ({matched_on}). DPDP Act s.9(3) prohibits it outright: "
            "it is not something consent - including a verified parental consent - can "
            "authorise, and no Fourth Schedule (R.12) exemption covering this purpose is "
            "in force for this tenant."
        ),
    )


# --------------------------------------------------------------------------- #
#  s.9(1): the gate on recording consent
# --------------------------------------------------------------------------- #


def assert_consent_permitted(
    db: Session,
    consent,
    *,
    action: str,
    actor_username: str = "system",
    source_app: str = "",
    request_id: Optional[str] = None,
) -> Optional[dict]:
    """Refuse to record consent for a child that s.9 does not permit.

    Called from `app/services/consent.py` at the head of every transition that
    *reaches an affirmative state* - grant, renew, activate. Not from a route:
    the routes are many and growing (staff console, portal, CRM banner,
    integration handoff, re-consent campaigns) and a rule enforced in even
    four of five of them is a rule with a back door. `services/consent.py` is
    the one place all of them already pass through.

    Deliberately NOT called from `deny_consent` / `withdraw_consent`: refusing
    or withdrawing consent for a child is always permitted and is the
    protective direction. A guard that blocked withdrawal would trap a child
    in a consent nobody could get them out of.

    Two independent refusals, in this order:

      1. **s.9(3) prohibition** - checked first because it applies even when
         the parental consent exists and is perfect. Reporting "you need
         parental consent" for a purpose no parent can authorise would send
         the operator to collect a consent that would then be refused anyway.
      2. **s.9(1) verifiable parental consent** - no VERIFIED
         `guardian_consents` row, no consent recorded. R.11 routes a person
         with disability to the LAWFUL_GUARDIAN variant of the same record.

    Returns the dict of guardian/exemption facts to stamp onto the consent's
    `ConsentEvidence` row (so a consent can always be traced to the parental
    consent that permitted it), or None when s.9 does not apply to this
    principal at all - which is the case for every customer with no
    age-assurance record, so existing behaviour is untouched.
    """
    assurance = get_age_assurance(db, consent.customer_id)
    if assurance is None or not assurance.requires_guardian_consent:
        return None

    purpose = consent.purpose
    activity = consent.processing_activity
    tenant_id = consent.tenant_id

    if assurance.is_child:
        prohibition = child_prohibition_for(
            db,
            customer_id=consent.customer_id,
            tenant_id=tenant_id,
            purpose_code=purpose.code,
            purpose_name=purpose.name,
            processing_activity_code=activity.code if activity else "",
            purpose_child_restricted=bool(getattr(purpose, "child_restricted", False)),
        )
        if prohibition is not None:
            log_audit(
                db,
                "CHILD_PROHIBITED_PROCESSING_BLOCKED",
                actor_username=actor_username,
                actor_type="SYSTEM",
                source_app=source_app or consent.source_app or "",
                tenant_id=tenant_id,
                customer_id=consent.customer_id,
                consent_id=consent.id,
                purpose_id=consent.purpose_id,
                purpose_code=purpose.code,
                decision="DENY",
                reason=prohibition.reason,
                request_id=request_id,
                metadata={"action": action, **prohibition.as_details()},
                commit=True,
            )
            raise HTTPException(status_code=403, detail=prohibition.reason)

    exemption = find_exemption(
        db,
        tenant_id=tenant_id,
        purpose_code=purpose.code,
        obligation=OBLIGATION_PARENTAL_CONSENT,
    )
    if exemption is not None:
        log_audit(
            db,
            "CHILD_EXEMPTION_APPLIED",
            actor_username=actor_username,
            actor_type="SYSTEM",
            source_app=source_app or consent.source_app or "",
            tenant_id=tenant_id,
            customer_id=consent.customer_id,
            consent_id=consent.id,
            purpose_id=consent.purpose_id,
            purpose_code=purpose.code,
            reason=(
                f"R.12 Fourth Schedule exemption {exemption.schedule_reference or exemption.id} "
                f"({exemption.exemption_class}) relaxes verifiable parental consent for "
                f"purpose {purpose.code}."
            ),
            request_id=request_id,
            metadata={
                "action": action,
                "exemption_id": exemption.id,
                "exemption_class": exemption.exemption_class,
                "schedule_reference": exemption.schedule_reference,
            },
            commit=False,
        )
        return {
            "child_principal": True,
            "s9_1_satisfied_by": "FOURTH_SCHEDULE_EXEMPTION",
            "exemption_id": exemption.id,
            "exemption_class": exemption.exemption_class,
            "schedule_reference": exemption.schedule_reference,
        }

    required_type = "LAWFUL_GUARDIAN" if (assurance.is_person_with_disability and not assurance.is_child) else None
    record = verified_guardian_consent(db, consent.customer_id, guardian_type=required_type)
    if record is None:
        reason = (
            f"This data principal is recorded as "
            f"{'a child' if assurance.is_child else 'a person with disability with a lawful guardian'} "
            f"(age assurance #{assurance.id}, method {assurance.assurance_method}). "
            "DPDP Act s.9(1) with Rules 2025 R.10 requires verifiable consent of the parent "
            + ("or lawful guardian (R.11) " if assurance.is_person_with_disability else "")
            + "before their personal data may be processed, and no VERIFIED guardian-consent "
            f"record exists for them. Consent cannot be recorded for purpose {purpose.code} "
            f"(action {action})."
        )
        log_audit(
            db,
            "CHILD_CONSENT_BLOCKED",
            actor_username=actor_username,
            actor_type="SYSTEM",
            source_app=source_app or consent.source_app or "",
            tenant_id=tenant_id,
            customer_id=consent.customer_id,
            consent_id=consent.id,
            purpose_id=consent.purpose_id,
            purpose_code=purpose.code,
            decision="DENY",
            reason=reason,
            request_id=request_id,
            metadata={
                "action": action,
                "age_assurance_id": assurance.id,
                "is_child": bool(assurance.is_child),
                "is_person_with_disability": bool(assurance.is_person_with_disability),
            },
            commit=True,
        )
        raise HTTPException(status_code=403, detail=reason)

    return {
        "child_principal": bool(assurance.is_child),
        "person_with_disability": bool(assurance.is_person_with_disability),
        "s9_1_satisfied_by": "GUARDIAN_CONSENT",
        # Who consented, how they were verified, when - the three facts a
        # challenged fiduciary has to produce, carried onto the consent's own
        # evidence row so the two never have to be joined by hand.
        "guardian_consent_ref": record.reference_no,
        "guardian_type": record.guardian_type,
        "guardian_verification_method": record.verification_method,
        "guardian_verified_at": (
            as_utc(record.verified_at).isoformat() if record.verified_at else None
        ),
        "guardian_evidence_ref": record.evidence_ref,
        "guardian_evidence_hash": record.evidence_hash,
    }


# --------------------------------------------------------------------------- #
#  Writes
# --------------------------------------------------------------------------- #


def record_age_assurance(
    db: Session,
    *,
    customer,
    assurance_method: str,
    date_of_birth: Optional[date] = None,
    declared_is_child: Optional[bool] = None,
    is_person_with_disability: bool = False,
    assurance_reference: str = "",
    token_issuer: Optional[str] = None,
    details: Optional[dict] = None,
    actor_username: str = "system",
    source_app: str = "",
    request_id: Optional[str] = None,
) -> PrincipalAgeAssurance:
    """F-01: record (or re-record) the age-assurance finding for a principal.

    `is_child` is DERIVED from the date of birth whenever one is given -
    never taken from the caller - because a caller-supplied "is_child: false"
    alongside a 2015 date of birth is precisely the shape of the bug this
    module exists to prevent. `declared_is_child` is honoured only on the
    SELF_DECLARED route, where by definition there is no date to derive from.

    `is_verified` is likewise derived: SELF_DECLARED is never verification
    (R.10(1) requires due diligence), and the database refuses to store it as
    such anyway.
    """
    method = (assurance_method or "").strip().upper()
    if method == "SELF_DECLARED":
        if declared_is_child is None:
            raise HTTPException(
                status_code=422,
                detail="declared_is_child is required for the SELF_DECLARED method",
            )
        is_child = bool(declared_is_child)
        is_verified = False
    else:
        if date_of_birth is None:
            raise HTTPException(
                status_code=422,
                detail=f"date_of_birth is required for the {method} age-assurance method",
            )
        is_child = is_child_by_dob(date_of_birth)
        is_verified = True

    if method == "VIRTUAL_TOKEN" and not (token_issuer or "").strip():
        raise HTTPException(
            status_code=422,
            detail="token_issuer is required for the VIRTUAL_TOKEN age-assurance method",
        )

    now = utcnow()
    record = get_age_assurance(db, customer.id)
    created = record is None
    if record is None:
        record = PrincipalAgeAssurance(customer_id=customer.id)
        db.add(record)

    record.tenant_id = customer.tenant_id
    record.is_child = is_child
    record.is_person_with_disability = bool(is_person_with_disability)
    record.assurance_method = method
    record.is_verified = is_verified
    record.date_of_birth = date_of_birth.isoformat() if date_of_birth else None
    record.assurance_reference = assurance_reference or ""
    record.token_issuer = (token_issuer or "").strip() or None
    record.details = details or {}
    record.assured_at = now if is_verified else None
    record.assured_by = actor_username if is_verified else None
    record.source_app = source_app or customer.source_app or ""
    record.request_id = request_id
    db.flush()

    log_audit(
        db,
        "AGE_ASSURANCE_RECORDED",
        actor_username=actor_username,
        actor_type="USER",
        source_app=record.source_app,
        tenant_id=record.tenant_id,
        customer_id=customer.id,
        reason=(
            f"Age assurance {'created' if created else 'updated'} by method {method}: "
            f"is_child={is_child}, verified={is_verified}."
        ),
        request_id=request_id,
        metadata={
            "age_assurance_id": record.id,
            "assurance_method": method,
            "is_child": is_child,
            "is_verified": is_verified,
            "is_person_with_disability": bool(is_person_with_disability),
            # Deliberately no date of birth, no age: the audit ledger is
            # exported to regulators and read by staff, and it does not need
            # a child's date of birth to evidence that the check happened.
        },
        commit=False,
    )
    db.commit()
    db.refresh(record)
    return record


def create_guardian_consent(
    db: Session,
    *,
    customer,
    guardian_type: str,
    verification_method: str,
    guardian_name: str,
    guardian_email: str = "",
    guardian_phone: str = "",
    guardian_customer_id: Optional[int] = None,
    guardian_context_id: Optional[int] = None,
    guardian_identity_reference: Optional[str] = None,
    guardian_date_of_birth: Optional[date] = None,
    virtual_token_issuer: Optional[str] = None,
    virtual_token_reference: Optional[str] = None,
    appointment_authority: Optional[str] = None,
    appointment_reference: str = "",
    appointment_date: Optional[date] = None,
    actor_username: str = "system",
    source_app: str = "",
    request_id: Optional[str] = None,
) -> GuardianConsent:
    """F-02/F-03: open a parental or lawful-guardian consent record.

    Always starts PENDING. Creating the record is the fiduciary saying "here
    is who claims to be the parent"; `verify_guardian_consent` is the separate
    act of saying "and here is the due diligence R.10(1) requires". Collapsing
    the two would mean the verification could be asserted in the same breath
    as the claim, by the same caller, with nothing in between.
    """
    from app.core.encryption import hmac_digest

    record = GuardianConsent(
        tenant_id=customer.tenant_id,
        reference_no=generate_reference_no(db),
        customer_id=customer.id,
        guardian_type=(guardian_type or "PARENT").strip().upper(),
        verification_method=(verification_method or "").strip().upper(),
        status="PENDING",
        guardian_name=guardian_name,
        guardian_email=guardian_email or "",
        # Nothing fills a *_search column automatically - see docs/ARCHITECTURE.md.
        guardian_email_search=hmac_digest(guardian_email) if guardian_email else None,
        guardian_phone=guardian_phone or "",
        guardian_is_adult=False,
        guardian_customer_id=guardian_customer_id,
        guardian_context_id=guardian_context_id,
        guardian_identity_reference=guardian_identity_reference,
        guardian_date_of_birth=(
            guardian_date_of_birth.isoformat() if guardian_date_of_birth else None
        ),
        virtual_token_issuer=(virtual_token_issuer or "").strip() or None,
        virtual_token_reference=virtual_token_reference,
        appointment_authority=(appointment_authority or "").strip().upper() or None,
        appointment_reference=appointment_reference or "",
        appointment_date=appointment_date,
        source_app=source_app or customer.source_app or "",
        created_by=actor_username,
        request_id=request_id,
    )
    db.add(record)
    db.flush()

    log_audit(
        db,
        "GUARDIAN_CONSENT_CREATED",
        actor_username=actor_username,
        actor_type="USER",
        source_app=record.source_app,
        tenant_id=record.tenant_id,
        customer_id=customer.id,
        reason=(
            f"{record.guardian_type} consent record {record.reference_no} opened for "
            f"verification by method {record.verification_method}."
        ),
        request_id=request_id,
        metadata={
            "guardian_consent_ref": record.reference_no,
            "guardian_type": record.guardian_type,
            "verification_method": record.verification_method,
        },
        commit=False,
    )
    db.commit()
    db.refresh(record)
    return record


def verify_guardian_consent(
    db: Session,
    record: GuardianConsent,
    *,
    verification_note: str = "",
    actor_username: str = "system",
    request_id: Optional[str] = None,
) -> GuardianConsent:
    """R.10(1): perform the due diligence and, if it passes, mark VERIFIED.

    Each of the three R.10(2) routes has its own check, and each has to
    actually establish that the consenting person is an **identifiable
    adult** - the phrase in the Rule, and the reason `guardian_is_adult` is
    a column with a CHECK behind it rather than an assumption:

      (a) EXISTING_VERIFIED_ACCOUNT - the linked account must have a
          ConsentContext that was genuinely verified (OTP or fiduciary
          assertion), and that account must itself be assured as an adult.
          "We have an account for them" is not "we verified them".
      (b) VOLUNTARILY_PROVIDED_DETAILS - both an identity reference and a
          date of birth, and the date of birth must actually be an adult's.
      (c) VIRTUAL_TOKEN - a registered verifier must confirm it. There is
          none in this build, so this route returns 501 and the record stays
          PENDING. See the module docstring.
    """
    if record.status == "VERIFIED":
        return record
    if record.status in ("REJECTED", "REVOKED"):
        raise HTTPException(
            status_code=409,
            detail=f"Guardian consent {record.reference_no} is {record.status} and cannot be verified",
        )

    method = record.verification_method
    if method == "EXISTING_VERIFIED_ACCOUNT":
        _verify_existing_account(db, record)
    elif method == "VOLUNTARILY_PROVIDED_DETAILS":
        _verify_voluntary_details(record)
    elif method == "VIRTUAL_TOKEN":
        record.virtual_token_verified_at = _verify_virtual_token(
            record.virtual_token_issuer or "", record.virtual_token_reference
        )
    else:  # pragma: no cover - the column CHECK already bars anything else
        raise HTTPException(status_code=422, detail=f"Unknown verification method {method}")

    if record.guardian_type == "LAWFUL_GUARDIAN" and not (
        record.appointment_authority and (record.appointment_reference or "").strip()
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "A lawful guardian under Rules 2025 R.11 must name the appointing authority "
                "(court, designated authority or local level committee) and the appointment "
                "reference before the guardian consent can be verified."
            ),
        )

    now = utcnow()
    record.guardian_is_adult = True
    record.verified_at = now
    record.verified_by = actor_username
    record.verification_note = verification_note or ""
    record.status = "VERIFIED"
    record.evidence_ref = f"GEV-{secrets.token_hex(8).upper()}"
    record.evidence_hash = compute_guardian_evidence_hash(record)

    log_audit(
        db,
        "GUARDIAN_CONSENT_VERIFIED",
        actor_username=actor_username,
        actor_type="USER",
        source_app=record.source_app,
        tenant_id=record.tenant_id,
        customer_id=record.customer_id,
        reason=(
            f"Verifiable {record.guardian_type.lower().replace('_', ' ')} consent "
            f"{record.reference_no} established under DPDP Rules 2025 R.10(2) via "
            f"{record.verification_method}."
        ),
        request_id=request_id,
        metadata={
            "guardian_consent_ref": record.reference_no,
            "guardian_type": record.guardian_type,
            "verification_method": record.verification_method,
            "evidence_ref": record.evidence_ref,
            # The hash also lands in the immutable ledger, which is what makes
            # a later edit to guardian_consents detectable.
            "evidence_hash": record.evidence_hash,
        },
        commit=False,
    )
    db.commit()
    db.refresh(record)
    return record


def _verify_existing_account(db: Session, record: GuardianConsent) -> None:
    """R.10(2)(a): rely on identity/age details the fiduciary already holds."""
    from app.models.entities import ConsentContext

    if record.guardian_customer_id is None:
        raise HTTPException(
            status_code=422,
            detail="EXISTING_VERIFIED_ACCOUNT requires the adult's own customer account to be linked",
        )
    if record.guardian_customer_id == record.customer_id:
        raise HTTPException(
            status_code=422,
            detail="A principal cannot be their own parent or lawful guardian",
        )

    context = None
    if record.guardian_context_id is not None:
        context = (
            db.query(ConsentContext)
            .filter(
                ConsentContext.id == record.guardian_context_id,
                ConsentContext.customer_id == record.guardian_customer_id,
            )
            .first()
        )
    else:
        context = (
            db.query(ConsentContext)
            .filter(
                ConsentContext.customer_id == record.guardian_customer_id,
                ConsentContext.verified_at.isnot(None),
            )
            .order_by(ConsentContext.verified_at.desc())
            .first()
        )
    if context is None or context.verified_at is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "The linked adult account has never completed identity verification "
                "(no verified consent context). R.10(2)(a) allows reliance on details "
                "already held only where those details are reliable; an unverified "
                "account is not."
            ),
        )
    record.guardian_context_id = context.id

    guardian_assurance = get_age_assurance(db, record.guardian_customer_id)
    if guardian_assurance is None or not guardian_assurance.is_verified:
        raise HTTPException(
            status_code=422,
            detail=(
                "The linked account has no verified age assurance, so this fiduciary does "
                "not in fact hold reliable details of the consenting person's age "
                "(R.10(2)(a)). Record an age assurance for that account first."
            ),
        )
    if guardian_assurance.is_child:
        raise HTTPException(
            status_code=422,
            detail="The linked account is recorded as a child and cannot give parental consent",
        )


def _verify_voluntary_details(record: GuardianConsent) -> None:
    """R.10(2)(b): identity AND age details voluntarily provided."""
    if not record.guardian_identity_reference:
        raise HTTPException(
            status_code=422,
            detail="VOLUNTARILY_PROVIDED_DETAILS requires an identity reference for the adult",
        )
    dob = parse_iso_date(record.guardian_date_of_birth)
    if dob is None:
        raise HTTPException(
            status_code=422,
            detail="VOLUNTARILY_PROVIDED_DETAILS requires the adult's date of birth (R.10(2)(b) asks for age details, not only identity)",
        )
    if is_child_by_dob(dob):
        raise HTTPException(
            status_code=422,
            detail=(
                "The details provided describe someone under 18. R.10(1) requires due "
                "diligence that the consenting person is an identifiable ADULT."
            ),
        )


def revoke_guardian_consent(
    db: Session,
    record: GuardianConsent,
    *,
    reason: str = "",
    actor_username: str = "system",
    request_id: Optional[str] = None,
) -> GuardianConsent:
    """Withdraw a parental/guardian consent.

    Takes effect immediately and needs no cascade: every enforcement point
    asks `verified_guardian_consent` fresh, and a REVOKED row is not VERIFIED.
    The child's existing consents are left standing rather than force-
    withdrawn here - deciding what happens to them is a s.6(4)/s.8(7) question
    with its own machinery (services/consent.py, services/erasure.py), and
    silently mass-withdrawing from inside a revocation would bypass all of it.
    """
    if record.status == "REVOKED":
        return record
    record.status = "REVOKED"
    record.revoked_at = utcnow()
    record.revoked_by = actor_username
    record.revocation_reason = reason or ""
    log_audit(
        db,
        "GUARDIAN_CONSENT_REVOKED",
        actor_username=actor_username,
        actor_type="USER",
        source_app=record.source_app,
        tenant_id=record.tenant_id,
        customer_id=record.customer_id,
        reason=f"Guardian consent {record.reference_no} revoked.",
        request_id=request_id,
        metadata={"guardian_consent_ref": record.reference_no},
        commit=False,
    )
    db.commit()
    db.refresh(record)
    return record


def reject_guardian_consent(
    db: Session,
    record: GuardianConsent,
    *,
    rejection_reason: str,
    actor_username: str = "system",
    request_id: Optional[str] = None,
) -> GuardianConsent:
    """Record that the due diligence failed. A refusal with no recorded ground
    is the shape of an unlawful refusal, so `rejection_reason` is required by
    the column CHECK as well as by this signature."""
    record.status = "REJECTED"
    record.rejection_reason = rejection_reason
    record.rejected_at = utcnow()
    record.rejected_by = actor_username
    log_audit(
        db,
        "GUARDIAN_CONSENT_REJECTED",
        actor_username=actor_username,
        actor_type="USER",
        source_app=record.source_app,
        tenant_id=record.tenant_id,
        customer_id=record.customer_id,
        reason=f"Guardian consent {record.reference_no} rejected: {rejection_reason}.",
        request_id=request_id,
        metadata={
            "guardian_consent_ref": record.reference_no,
            "rejection_reason": rejection_reason,
        },
        commit=False,
    )
    db.commit()
    db.refresh(record)
    return record


# --------------------------------------------------------------------------- #
#  F-06: K-24 / K-25
# --------------------------------------------------------------------------- #


def children_metrics(db: Session, *, tenant_id: Optional[int] = None) -> dict:
    """K-24 (age-assurance coverage) and K-25 (parental-consent completion and
    child-purpose blocks).

    Every denominator is reported alongside its numerator and no ratio is
    invented when the denominator is zero - `None`, not 0% and not 100%. A
    tenant with no child accounts has not achieved 100% parental-consent
    completion; it has nothing to report, and saying otherwise would turn an
    empty set into a compliance claim.
    """
    from app.models.entities import AuditLog, Customer

    customers_q = db.query(Customer.id)
    assurance_q = db.query(PrincipalAgeAssurance)
    audit_q = db.query(AuditLog.id)
    if tenant_id is not None:
        customers_q = customers_q.filter(Customer.tenant_id == tenant_id)
        assurance_q = assurance_q.filter(PrincipalAgeAssurance.tenant_id == tenant_id)
        audit_q = audit_q.filter(AuditLog.tenant_id == tenant_id)

    total_customers = customers_q.count()
    assured = assurance_q.filter(PrincipalAgeAssurance.is_verified.is_(True)).count()
    child_ids = [
        row.customer_id
        for row in assurance_q.filter(PrincipalAgeAssurance.is_child.is_(True)).all()
    ]
    children = len(child_ids)

    with_parental = 0
    if child_ids:
        with_parental = (
            db.query(GuardianConsent.customer_id)
            .filter(
                GuardianConsent.customer_id.in_(child_ids),
                GuardianConsent.status == "VERIFIED",
            )
            .distinct()
            .count()
        )

    blocked_no_consent = audit_q.filter(AuditLog.event == "CHILD_CONSENT_BLOCKED").count()
    blocked_prohibited = audit_q.filter(
        AuditLog.event == "CHILD_PROHIBITED_PROCESSING_BLOCKED"
    ).count()
    denied_decisions = audit_q.filter(
        AuditLog.event == "CHILD_PROHIBITED_DECISION_DENIED"
    ).count()

    return {
        "K-24": {
            "name": "Age-assurance coverage",
            "accounts": total_customers,
            "accounts_with_verified_age_assurance": assured,
            "coverage_pct": (
                round(assured * 100.0 / total_customers, 2) if total_customers else None
            ),
        },
        "K-25": {
            "name": "Parental-consent completion & child-purpose blocks",
            "child_accounts": children,
            "child_accounts_with_verified_parental_consent": with_parental,
            "completion_pct": (
                round(with_parental * 100.0 / children, 2) if children else None
            ),
            "child_consent_blocked_no_parental_record": blocked_no_consent,
            "child_prohibited_processing_blocked_at_recording": blocked_prohibited,
            "child_prohibited_processing_denied_at_decision": denied_decisions,
        },
    }
