"""R1-14 (F-01..F-06): age assurance, verifiable parental/guardian consent,
and the Fourth Schedule exemption register.

DPDP Act s.9 and DPDP Rules 2025 R.10-R.12 with the Fourth Schedule. This is
the highest-penalty head in the Act (up to Rs. 200 crore) and it contains the
only two *prohibitions* in the consent regime, as opposed to obligations that
consent can satisfy:

  s.9(1)  A Data Fiduciary shall, before processing any personal data of a
          child, obtain **verifiable consent** of the parent (or of the
          lawful guardian, s.9(1) read with R.11 for a person with
          disability). R.10(1)-(2) tells you what "verifiable" means: due
          diligence that the consenting adult is identifiable, by one of
          three named routes and no others.
  s.9(2)  No processing likely to cause any **detrimental effect** on the
          well-being of a child.
  s.9(3)  No **tracking, behavioural monitoring, or targeted advertising**
          directed at children.

s.9(3) is the one most easily got wrong in a consent platform, because every
other requirement in this codebase is satisfied by obtaining consent. This one
is not: a parent cannot consent to targeted advertising directed at their
child, because the Act does not make it consentable. It is prohibited. That is
why the enforcement for it lives in `app/services/decision_engine.py` as a
refusal that outranks a valid, granted, unexpired consent - see
`app/services/guardian.py::child_prohibition_for` and the block comment at the
head of `evaluate_decision`.

Three tables, and the reason each is a table rather than a column:

**`principal_age_assurances`** - a 1:1 side table on `customers`, not a column
on it. Age and child status are among the most sensitive attributes a
platform can hold about a person, and keeping them in their own table means
the date of birth can be encrypted (`EncryptedString`) and the whole record
can be read, written and audited as one unit without widening the hot
`customers` row that every list endpoint selects. The absence of a row means
"never assessed", which is deliberately distinct from "assessed as an adult":
only the second is a finding.

**`guardian_consents`** - the R.10 record. The thing a fiduciary must be able
to produce when challenged is not "we had consent" but *who* consented, *how
we satisfied ourselves they were an identifiable adult*, and *when*. All three
are columns here, and `ck_guardian_consents_verified_has_evidence` makes a
VERIFIED row that is missing any of them unwritable - the same discipline
`rights_requests` applies to `ck_rights_requests_fulfilled_is_verified` and
`erasure_jobs` to `ck_erasure_jobs_executed_has_evidence`.

**`tenant_child_exemptions`** - R.12 with the Fourth Schedule. Deliberately
NOT a boolean on `organizations`. A Fourth Schedule exemption is narrow in
three independent directions at once (a named class of fiduciary, a named set
of purposes, and only the specific obligations the Rules relax), and a boolean
switch would collapse all three into "children's rules off for this tenant" -
which is the single most damaging thing this module could get wrong. Every one
of the three narrowings is a NOT NULL column with a non-empty CHECK.

Defined in its own module, not `entities.py`, so this could be built alongside
other lanes; every ForeignKey target is named by STRING and nothing here
imports `entities.py`. It is registered into `Base.metadata` from
`app/models/__init__.py` (see that file's docstring) rather than from
`entities.py`'s sibling-import block, because none of these classes needs a
class defined in `entities.py` at import time.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.encryption import EncryptedJSON, EncryptedString, EncryptedText


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
#  Vocabulary
# --------------------------------------------------------------------------- #

#: s.2(f): a child is an individual who has not completed eighteen years of
#: age. There is no other threshold in the Act, and this constant exists so
#: that fact is stated once rather than as a literal 18 scattered through the
#: service layer.
CHILD_AGE_THRESHOLD_YEARS = 18

#: How the fiduciary came to its conclusion about the principal's age.
#:
#: SELF_DECLARED is listed first and is the one the demo sites do today (a
#: pre-ticked "I am 18+" box). It is recorded honestly as what it is - an
#: unverified claim - and `ck_age_assurances_self_declared_is_not_verified`
#: makes it impossible to store a SELF_DECLARED row that also claims to be
#: verified. R.10(1) requires *due diligence*; a tick-box is not due
#: diligence, and the platform must not be able to represent it as such.
AGE_ASSURANCE_METHODS = [
    "SELF_DECLARED",            # a tick-box or an unchecked claim - never verification
    "DECLARED_DATE_OF_BIRTH",   # principal supplied a date of birth at onboarding
    "FIDUCIARY_HELD_RECORD",    # age reliably available with the fiduciary already (R.10(2)(a) shape)
    "IDENTITY_DOCUMENT",        # an identity document actually checked by the fiduciary
    "VIRTUAL_TOKEN",            # R.10(2)(c) virtual token from an authorised entity - PLACEHOLDER
]

#: R.10(2): the three - and only three - routes by which a Data Fiduciary may
#: satisfy itself that the person consenting on a child's behalf is an
#: identifiable adult. Naming them as an enumerated, CHECK-constrained column
#: rather than free text is the point: a fourth route invented by a future
#: integration would have to change this list and the migration, in review,
#: rather than arriving as a new string value nobody noticed.
GUARDIAN_VERIFICATION_METHODS = [
    #: (a) reliable details of identity and age already available with the
    #: Data Fiduciary - in this platform, an existing Customer account whose
    #: own identity has been verified (a verified ConsentContext).
    "EXISTING_VERIFIED_ACCOUNT",
    #: (b) identity and age details voluntarily provided by the adult.
    "VOLUNTARILY_PROVIDED_DETAILS",
    #: (c) a virtual token mapped to a verified identity, issued by an entity
    #: entrusted by law or by an authorised Digital Locker service provider.
    #: This platform ships the INTERFACE for it and no integration - see
    #: `app/services/guardian.py::VIRTUAL_TOKEN_VERIFIERS`. A record on this
    #: route cannot reach VERIFIED without a registered verifier having
    #: actually confirmed the token, which
    #: `ck_guardian_consents_virtual_token_verified_has_moment` enforces at
    #: the database.
    "VIRTUAL_TOKEN",
]

#: s.9(1) covers the parent of a child; R.11 covers the **lawful guardian** of
#: a person with disability who has a guardian appointed by a court, a
#: designated authority, or a local level committee. The two are the same act
#: with different due diligence, so they are one table with a discriminator
#: rather than two near-identical registers that would drift apart.
GUARDIAN_TYPES = ["PARENT", "LAWFUL_GUARDIAN"]

#: R.11: who appointed the lawful guardian. Required for LAWFUL_GUARDIAN and
#: meaningless for PARENT.
GUARDIAN_APPOINTMENT_AUTHORITIES = [
    "COURT",
    "DESIGNATED_AUTHORITY",
    "LOCAL_LEVEL_COMMITTEE",
]

GUARDIAN_CONSENT_STATUSES = ["PENDING", "VERIFIED", "REJECTED", "REVOKED"]

#: The classes of Data Fiduciary the Fourth Schedule names, as summarised in
#: the compliance register's F-05 row ("healthcare, education, crèche,
#: transport; specified purposes"). `schedule_reference` on each row carries
#: the exact Fourth Schedule entry the tenant is relying on, in the tenant's
#: own words, because this list is a routing key - not a substitute for
#: citing the Gazette.
FOURTH_SCHEDULE_CLASSES = [
    "HEALTHCARE_PROVIDER",
    "ALLIED_HEALTHCARE_PROVIDER",
    "EDUCATIONAL_INSTITUTION",
    "CHILD_CARE_OR_CRECHE",
    "CHILD_TRANSPORT",
    #: The Fourth Schedule's "specified purposes" limb, available to any
    #: fiduciary for the narrow purposes it lists.
    "SPECIFIED_PURPOSE",
]

#: The obligations a Fourth Schedule exemption may relax, and no others.
#:
#: s.9(2) - no processing likely to have a detrimental effect on the
#: well-being of a child - is DELIBERATELY ABSENT and is not configurable
#: anywhere in this module. Nothing in R.12 or the Fourth Schedule offers a
#: route to processing that harms a child, so the platform does not offer a
#: switch for it. If a future reading of the Gazette shows this list should be
#: narrower still, narrowing it is a one-line change; there is no code path
#: that widens it.
EXEMPTIBLE_OBLIGATIONS = [
    "VERIFIABLE_PARENTAL_CONSENT",           # s.9(1)
    "TRACKING_AND_ADVERTISING_PROHIBITION",  # s.9(3)
]


# --------------------------------------------------------------------------- #
#  F-01: age assurance
# --------------------------------------------------------------------------- #


class PrincipalAgeAssurance(Base):
    """One age-assurance finding per Customer (F-01, s.2(f), s.9(1), R.10).

    One row per customer, enforced by a UNIQUE on `customer_id`: a principal
    has one age, and two rows disagreeing about whether someone is a child is
    not a state this platform should be able to reach. Re-assessment updates
    the row in place and is recorded in the append-only audit ledger, which is
    where the history of the finding lives.

    **No row means "never assessed", not "adult".** Every read path in
    `app/services/guardian.py` treats a missing row as "not known to be a
    child" and leaves existing behaviour untouched - a platform that started
    refusing consent for every pre-existing customer the moment this shipped
    would have been switched off within the hour, and the Act's requirement is
    that a child's data is protected, not that unassessed adults are locked
    out. The gap that leaves is visible and countable rather than hidden: it
    is exactly K-24, age-assurance coverage.
    """

    __tablename__ = "principal_age_assurances"
    __table_args__ = (
        UniqueConstraint("customer_id", name="uq_principal_age_assurances_customer"),
        CheckConstraint(
            "assurance_method IN ('SELF_DECLARED','DECLARED_DATE_OF_BIRTH',"
            "'FIDUCIARY_HELD_RECORD','IDENTITY_DOCUMENT','VIRTUAL_TOKEN')",
            name="ck_age_assurances_method",
        ),
        # R.10(1) requires due diligence. A pre-ticked "I am 18+" box is a
        # claim by the person whose claim is in question, and the whole reason
        # F-01 is open. It can be recorded; it can never be stored as
        # verified.
        CheckConstraint(
            "is_verified = false OR assurance_method <> 'SELF_DECLARED'",
            name="ck_age_assurances_self_declared_is_not_verified",
        ),
        # A verification claim naming neither a moment nor an actor is not
        # evidence of anything - same shape as
        # ck_rights_requests_verified_has_evidence.
        CheckConstraint(
            "is_verified = false OR (assured_at IS NOT NULL AND assured_by IS NOT NULL)",
            name="ck_age_assurances_verified_has_evidence",
        ),
        # R.10(2)(c): a virtual token is only meaningful if the issuing entity
        # is named. An unattributed token is an opaque string, not evidence.
        CheckConstraint(
            "assurance_method <> 'VIRTUAL_TOKEN' OR "
            "(token_issuer IS NOT NULL AND length(trim(token_issuer)) > 0)",
            name="ck_age_assurances_virtual_token_has_issuer",
        ),
        Index("ix_age_assurances_tenant_is_child", "tenant_id", "is_child"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id"), nullable=False, index=True
    )

    #: The finding. s.2(f): under eighteen. This is the flag every enforcement
    #: point in app/services/guardian.py reads.
    is_child: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    #: R.11: a Data Principal who is a person with disability and has a lawful
    #: guardian. Independent of `is_child` - an adult can be one and not the
    #: other - and it routes to the LAWFUL_GUARDIAN variant of the same
    #: verifiable-consent requirement.
    is_person_with_disability: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )

    assurance_method: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Whether the method actually constituted due diligence. False for
    #: SELF_DECLARED (enforced above) and for any method whose evidence has
    #: not been completed.
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: Encrypted: a date of birth is directly identifying and, for a child, is
    #: the single most sensitive field in this schema. Nullable because
    #: SELF_DECLARED carries no date at all - the principal ticked a box, and
    #: storing a date we do not have would be fabrication.
    #: Stored as an ISO-8601 `YYYY-MM-DD` string rather than a DATE column,
    #: because EncryptedString encrypts text: the ciphertext is what reaches
    #: Postgres, so a native date type could not hold it. Parsed back through
    #: `app/services/guardian.py::parse_iso_date`.
    date_of_birth: Mapped[str | None] = mapped_column(
        EncryptedString(64), nullable=True
    )

    #: A document/token handle an auditor can check the finding against (a
    #: DigiLocker transaction reference, an internal KYC case id). Kept in the
    #: clear for the same reason `nominations.activation_evidence_ref` is: it
    #: is a reference to a record, not the record, and it must stay legible to
    #: whoever is checking the finding.
    assurance_reference: Mapped[str] = mapped_column(String(256), default="")
    #: R.10(2)(c): the authorised entity or Digital Locker service provider
    #: that issued the token. Required when assurance_method is VIRTUAL_TOKEN.
    token_issuer: Mapped[str | None] = mapped_column(String(128), nullable=True)

    assured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assured_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    #: Method-specific detail (which document class, which issuer response
    #: code). Encrypted because for a child every additional attribute is
    #: another identifier.
    details: Mapped[dict] = mapped_column(EncryptedJSON, default=dict)

    source_app: Mapped[str] = mapped_column(String(128), default="", index=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    @property
    def requires_guardian_consent(self) -> bool:
        """Whether s.9(1) (or R.11) applies to this principal at all."""
        return bool(self.is_child or self.is_person_with_disability)


# --------------------------------------------------------------------------- #
#  F-02 / F-03: verifiable parental and lawful-guardian consent
# --------------------------------------------------------------------------- #


class GuardianConsent(Base):
    """A verifiable parental (s.9(1)) or lawful-guardian (R.11) consent record.

    This row is the evidence. If the fiduciary is challenged on a child's
    consent, three questions get asked and this table answers each with a
    column rather than with a narrative:

      who consented   -> guardian_name / guardian_email / guardian_customer_id
      how we verified -> verification_method, plus the method-specific columns
                         each of which is required by its own CHECK
      when            -> verified_at, alongside the hash-chained AuditLog row
                         GUARDIAN_CONSENT_VERIFIED that services/guardian.py
                         writes in the same unit of work

    `evidence_hash` is SHA-256 over the canonical JSON of exactly those facts
    (`app/services/guardian.py::compute_guardian_evidence_hash`) and the same
    value is written into the immutable audit row, so a later edit to this
    table is detectable by recomputing - the identical mechanism
    `rights_requests.closure_hash` uses.

    **A record only counts while it is VERIFIED.** PENDING, REJECTED and
    REVOKED all read as "no verifiable parental consent" at every enforcement
    point, so a revocation takes effect on the next consent action without any
    additional bookkeeping.

    **This record permits consent; it does not permit everything.** s.9(3)
    prohibits tracking, behavioural monitoring and targeted advertising
    directed at children outright, and no row in this table unlocks those -
    see `app/services/guardian.py::child_prohibition_for`. A guardian consent
    that could authorise targeted advertising to a child would be this
    module's most dangerous bug, so the two checks are separate functions with
    separate call sites and neither is expressed in terms of the other.
    """

    __tablename__ = "guardian_consents"
    __table_args__ = (
        CheckConstraint(
            "guardian_type IN ('PARENT','LAWFUL_GUARDIAN')",
            name="ck_guardian_consents_type",
        ),
        CheckConstraint(
            "verification_method IN ('EXISTING_VERIFIED_ACCOUNT',"
            "'VOLUNTARILY_PROVIDED_DETAILS','VIRTUAL_TOKEN')",
            name="ck_guardian_consents_method",
        ),
        CheckConstraint(
            "status IN ('PENDING','VERIFIED','REJECTED','REVOKED')",
            name="ck_guardian_consents_status",
        ),
        # The load-bearing one. R.10(1): "verifiable consent" means the
        # fiduciary did due diligence that the consenting person is an
        # identifiable ADULT. A VERIFIED row that cannot name who did the
        # verifying, when, and with what evidence is precisely the claim a
        # regulator would reject, so it is not storable.
        CheckConstraint(
            "status <> 'VERIFIED' OR ("
            "verified_at IS NOT NULL AND verified_by IS NOT NULL "
            "AND guardian_is_adult AND evidence_ref IS NOT NULL "
            "AND evidence_hash IS NOT NULL)",
            name="ck_guardian_consents_verified_has_evidence",
        ),
        # R.10(2)(a): the route only exists because an already-verified adult
        # account is what is being relied on. Without the link there is
        # nothing to rely on.
        CheckConstraint(
            "verification_method <> 'EXISTING_VERIFIED_ACCOUNT' "
            "OR guardian_customer_id IS NOT NULL",
            name="ck_guardian_consents_existing_account_is_linked",
        ),
        # R.10(2)(b): "identity AND age details voluntarily provided". Both,
        # not either - an adult who gives a name but no age has not
        # established the one fact this route exists to establish.
        CheckConstraint(
            "status <> 'VERIFIED' OR verification_method <> 'VOLUNTARILY_PROVIDED_DETAILS' "
            "OR (guardian_date_of_birth IS NOT NULL AND guardian_identity_reference IS NOT NULL)",
            name="ck_guardian_consents_voluntary_has_identity_and_age",
        ),
        # R.10(2)(c): a token with no named issuer is an opaque string.
        CheckConstraint(
            "verification_method <> 'VIRTUAL_TOKEN' OR "
            "(virtual_token_issuer IS NOT NULL AND length(trim(virtual_token_issuer)) > 0)",
            name="ck_guardian_consents_virtual_token_has_issuer",
        ),
        # The placeholder's teeth. The Digital Locker route is an INTERFACE in
        # this build and nothing more; this constraint means an unimplemented
        # integration cannot be quietly treated as a completed one, because a
        # VERIFIED virtual-token row without a moment of actual token
        # confirmation is unwritable.
        CheckConstraint(
            "status <> 'VERIFIED' OR verification_method <> 'VIRTUAL_TOKEN' "
            "OR virtual_token_verified_at IS NOT NULL",
            name="ck_guardian_consents_virtual_token_verified_has_moment",
        ),
        # R.11: a lawful guardian is lawful because somebody appointed them.
        # An unappointed "guardian" is a stranger asserting authority over a
        # person with disability.
        CheckConstraint(
            "guardian_type <> 'LAWFUL_GUARDIAN' OR ("
            "appointment_authority IS NOT NULL "
            "AND length(trim(appointment_reference)) > 0)",
            name="ck_guardian_consents_lawful_guardian_is_appointed",
        ),
        CheckConstraint(
            "appointment_authority IS NULL OR appointment_authority IN "
            "('COURT','DESIGNATED_AUTHORITY','LOCAL_LEVEL_COMMITTEE')",
            name="ck_guardian_consents_appointment_authority",
        ),
        CheckConstraint(
            "status <> 'REJECTED' OR rejection_reason IS NOT NULL",
            name="ck_guardian_consents_rejected_has_reason",
        ),
        # The lookup every enforcement point performs: "is there a VERIFIED
        # guardian consent for this child, in this tenant".
        Index(
            "ix_guardian_consents_tenant_customer_status",
            "tenant_id",
            "customer_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )

    #: Opaque, CSPRNG-generated, unique, stored in the clear - the one
    #: indexable handle on an otherwise encrypted record, and the value quoted
    #: back on a consent's evidence row. Sequential would publish how many
    #: children the tenant has on its books.
    reference_no: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )

    #: The CHILD (or person with disability) this consent is about.
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id"), nullable=False, index=True
    )

    guardian_type: Mapped[str] = mapped_column(
        String(16), default="PARENT", nullable=False
    )
    verification_method: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="PENDING", nullable=False, index=True
    )

    # ---- who consented ---------------------------------------------------
    #: The adult's own personal data, encrypted exactly as Customer's is.
    guardian_name: Mapped[str] = mapped_column(EncryptedString(512), nullable=False)
    guardian_email: Mapped[str] = mapped_column(EncryptedString(512), default="")
    #: HMAC companion so the encrypted address stays findable. Nothing fills
    #: it automatically - the service sets it (docs/ARCHITECTURE.md, field encryption).
    guardian_email_search: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    guardian_phone: Mapped[str] = mapped_column(EncryptedString(256), default="")
    #: The finding that matters under R.10(1): this person is an ADULT.
    #: Required for VERIFIED by ck_guardian_consents_verified_has_evidence.
    guardian_is_adult: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    # ---- how we verified: R.10(2)(a) ------------------------------------
    #: The already-verified adult account being relied on.
    guardian_customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True, index=True
    )
    #: The verified ConsentContext that proves that account's identity was
    #: actually established (OTP or fiduciary assertion) rather than merely
    #: existing. ``ondelete="SET NULL"`` because `consent_contexts` is one of
    #: the record classes the R1-06 erasure engine hard deletes - a
    #: restricting key here would make erasing an adult raise.
    guardian_context_id: Mapped[int | None] = mapped_column(
        ForeignKey("consent_contexts.id", ondelete="SET NULL"), nullable=True
    )

    # ---- how we verified: R.10(2)(b) ------------------------------------
    #: Identity and age details voluntarily provided by the adult. Both
    #: encrypted: an identity-document reference for a named person is
    #: directly identifying, and unlike `appointment_reference` (a public
    #: instrument) nobody needs to search it.
    guardian_identity_reference: Mapped[str | None] = mapped_column(
        EncryptedString(256), nullable=True
    )
    #: ISO-8601 string, encrypted - see PrincipalAgeAssurance.date_of_birth.
    guardian_date_of_birth: Mapped[str | None] = mapped_column(
        EncryptedString(64), nullable=True
    )

    # ---- how we verified: R.10(2)(c) ------------------------------------
    #: The authorised entity / Digital Locker service provider named as the
    #: token's issuer.
    virtual_token_issuer: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    #: The token itself, encrypted: it is a handle to a verified identity and
    #: is a bearer credential for the duration of its validity.
    virtual_token_reference: Mapped[str | None] = mapped_column(
        EncryptedString(512), nullable=True
    )
    #: Set ONLY by a registered verifier actually confirming the token. Null
    #: while no integration exists, which is what keeps the placeholder
    #: honest - see ck_guardian_consents_virtual_token_verified_has_moment.
    virtual_token_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ---- R.11: lawful guardian appointment -------------------------------
    appointment_authority: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    #: A court order number, a committee decision reference. In the clear for
    #: the same reason `nominations.activation_evidence_ref` is: it names a
    #: public instrument an auditor checks the appointment against, and it
    #: must stay searchable.
    appointment_reference: Mapped[str] = mapped_column(String(256), default="")
    appointment_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # ---- the verification act -------------------------------------------
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verification_note: Mapped[str] = mapped_column(EncryptedText, default="")
    #: Quoted onto every ConsentEvidence row this record authorises, so a
    #: consent can be traced back to the parental consent that permitted it.
    evidence_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    rejection_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    revocation_reason: Mapped[str] = mapped_column(EncryptedText, default="")

    source_app: Mapped[str] = mapped_column(String(128), default="", index=True)
    created_by: Mapped[str] = mapped_column(String(128), default="")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    @property
    def is_effective(self) -> bool:
        return self.status == "VERIFIED"


# --------------------------------------------------------------------------- #
#  F-05: Fourth Schedule exemptions, per tenant
# --------------------------------------------------------------------------- #


class TenantChildExemption(Base):
    """One narrow R.12 / Fourth Schedule exemption claimed by one tenant.

    Read the module docstring for why this is a table and not a flag. The
    short version: an exemption is narrow in three directions simultaneously,
    and each is a required, non-empty column here -

      `exemption_class`        which class of Data Fiduciary the tenant is in
      `purpose_codes`          which purposes it covers, and no others
      `exempted_obligations`   which obligations it relaxes, and no others

    `ck_child_exemptions_purposes_not_empty` and
    `ck_child_exemptions_obligations_not_empty` are the ones that matter. An
    exemption with an empty purpose list would apply to everything, which is
    exactly the blanket switch this design exists to prevent, and Postgres
    refuses to store it.

    An exemption also has to be *claimed by somebody*: `is_active` requires a
    named authoriser, a moment, and the Fourth Schedule entry being relied on
    (`ck_child_exemptions_active_is_authorised`). Deciding a tenant is a
    "clinical establishment" is a legal judgement with a Rs. 200 crore penalty
    behind it if wrong, and it should carry a name.
    """

    __tablename__ = "tenant_child_exemptions"
    __table_args__ = (
        CheckConstraint(
            "exemption_class IN ('HEALTHCARE_PROVIDER','ALLIED_HEALTHCARE_PROVIDER',"
            "'EDUCATIONAL_INSTITUTION','CHILD_CARE_OR_CRECHE','CHILD_TRANSPORT',"
            "'SPECIFIED_PURPOSE')",
            name="ck_child_exemptions_class",
        ),
        # "Applies to nothing in particular" is how a narrow exemption becomes
        # a blanket one. Both lists are required to name at least one thing.
        CheckConstraint(
            "json_array_length(purpose_codes) > 0",
            name="ck_child_exemptions_purposes_not_empty",
        ),
        CheckConstraint(
            "json_array_length(exempted_obligations) > 0",
            name="ck_child_exemptions_obligations_not_empty",
        ),
        CheckConstraint(
            "is_active = false OR (authorised_by IS NOT NULL AND authorised_at IS NOT NULL "
            "AND length(trim(schedule_reference)) > 0)",
            name="ck_child_exemptions_active_is_authorised",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_child_exemptions_window",
        ),
        Index("ix_child_exemptions_tenant_active", "tenant_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: NOT NULL, unlike every other tenant_id in this module: an exemption
    #: that belonged to no tenant would apply to all of them.
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )

    exemption_class: Mapped[str] = mapped_column(String(48), nullable=False)
    #: The exact Fourth Schedule entry relied on, in the tenant's own words
    #: (e.g. "Fourth Schedule, Part A, entry 1"). Required before the
    #: exemption can go active. The platform routes on `exemption_class`; the
    #: citation is what a regulator reads.
    schedule_reference: Mapped[str] = mapped_column(String(128), default="")

    #: Purpose codes this exemption covers. Nothing outside the list is
    #: exempt, ever.
    purpose_codes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    #: Which of EXEMPTIBLE_OBLIGATIONS this relaxes. s.9(2) is not in that
    #: vocabulary at all - see the module docstring.
    exempted_obligations: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False
    )
    #: The Fourth Schedule's conditions - characteristically "to the extent
    #: necessary for..." - recorded so the limit travels with the exemption.
    conditions: Mapped[str] = mapped_column(Text, default="")

    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    effective_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    authorised_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    authorised_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    def covers(self, purpose_code: str, obligation: str, *, now: datetime) -> bool:
        """Whether this exemption is in force, and relaxes `obligation` for
        `purpose_code`. Every condition is ANDed - there is no path through
        this method that returns True on a partial match."""
        if not self.is_active:
            return False
        if self.effective_from and self.effective_from > now:
            return False
        if self.effective_to is not None and self.effective_to <= now:
            return False
        if obligation not in (self.exempted_obligations or []):
            return False
        return purpose_code in (self.purpose_codes or [])
