"""R1-14 (F-01..F-06): request/response models for the children and guardian
consent API.

Two things here are validation rather than typing, and both are deliberate.

**`extra="forbid"` on every input.** A typo'd field name on an age-assurance
or guardian-consent payload must be a 422, not a silently ignored key. On
these particular endpoints a dropped field is not a cosmetic bug: a caller
that meant to send `date_of_birth` and sent `dob` would otherwise get a
recorded finding based on whatever the defaults were.

**`is_child` is not an input field anywhere.** It is derived from the date of
birth by `app/services/guardian.py::record_age_assurance`, because a caller
who can assert "this person is not a child" alongside a 2015 date of birth can
switch off the entire s.9 regime for that principal with one boolean. The one
exception is the SELF_DECLARED route, where there is no date of birth to
derive from - and there the field is named `declared_is_child`, is required,
and produces a record that the database refuses to mark verified.

Defined in its own module, not `schemas/schemas.py`, so this could be built
alongside other lanes.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.guardian import (
    AGE_ASSURANCE_METHODS,
    EXEMPTIBLE_OBLIGATIONS,
    FOURTH_SCHEDULE_CLASSES,
    GUARDIAN_APPOINTMENT_AUTHORITIES,
    GUARDIAN_TYPES,
    GUARDIAN_VERIFICATION_METHODS,
)

AgeAssuranceMethod = Literal[
    "SELF_DECLARED",
    "DECLARED_DATE_OF_BIRTH",
    "FIDUCIARY_HELD_RECORD",
    "IDENTITY_DOCUMENT",
    "VIRTUAL_TOKEN",
]
GuardianType = Literal["PARENT", "LAWFUL_GUARDIAN"]
GuardianVerificationMethod = Literal[
    "EXISTING_VERIFIED_ACCOUNT", "VOLUNTARILY_PROVIDED_DETAILS", "VIRTUAL_TOKEN"
]
AppointmentAuthority = Literal["COURT", "DESIGNATED_AUTHORITY", "LOCAL_LEVEL_COMMITTEE"]
ExemptionClass = Literal[
    "HEALTHCARE_PROVIDER",
    "ALLIED_HEALTHCARE_PROVIDER",
    "EDUCATIONAL_INSTITUTION",
    "CHILD_CARE_OR_CRECHE",
    "CHILD_TRANSPORT",
    "SPECIFIED_PURPOSE",
]
ExemptibleObligation = Literal[
    "VERIFIABLE_PARENTAL_CONSENT", "TRACKING_AND_ADVERTISING_PROHIBITION"
]

# The Literals above and the model-layer lists must stay in step; if a value is
# added to one and not the other the API and the database disagree about what
# is representable, and the disagreement surfaces as a 500 at write time.
assert set(AGE_ASSURANCE_METHODS) == set(AgeAssuranceMethod.__args__)
assert set(GUARDIAN_TYPES) == set(GuardianType.__args__)
assert set(GUARDIAN_VERIFICATION_METHODS) == set(GuardianVerificationMethod.__args__)
assert set(GUARDIAN_APPOINTMENT_AUTHORITIES) == set(AppointmentAuthority.__args__)
assert set(FOURTH_SCHEDULE_CLASSES) == set(ExemptionClass.__args__)
assert set(EXEMPTIBLE_OBLIGATIONS) == set(ExemptibleObligation.__args__)


# --------------------------------------------------------------------------- #
#  F-01: age assurance
# --------------------------------------------------------------------------- #


class AgeAssuranceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The principal being assessed, named the way every other staff endpoint
    #: names one.
    customer_external_id: str = Field(min_length=1, max_length=256)
    assurance_method: AgeAssuranceMethod
    #: Required for every method except SELF_DECLARED, which by definition has
    #: no date behind it. `is_child` is derived from this, never asserted.
    date_of_birth: Optional[date] = None
    #: SELF_DECLARED only: the unverified claim the principal actually made.
    declared_is_child: Optional[bool] = None
    #: R.11: a person with disability who has a lawful guardian. Independent
    #: of age.
    is_person_with_disability: bool = False
    assurance_reference: str = Field(default="", max_length=256)
    token_issuer: Optional[str] = Field(default=None, max_length=128)
    details: dict = Field(default_factory=dict)

    @field_validator("date_of_birth")
    @classmethod
    def _not_in_the_future(cls, v: Optional[date]) -> Optional[date]:
        if v is not None and v > date.today():
            raise ValueError("date_of_birth cannot be in the future")
        return v

    @model_validator(mode="after")
    def _method_has_its_evidence(self):
        if self.assurance_method == "SELF_DECLARED":
            if self.declared_is_child is None:
                raise ValueError("declared_is_child is required for SELF_DECLARED")
            if self.date_of_birth is not None:
                # A date of birth IS derivable evidence. Accepting one under
                # the SELF_DECLARED label would file real evidence under the
                # one method the database refuses to call verified.
                raise ValueError(
                    "date_of_birth was supplied, so this is not a self-declaration - "
                    "use DECLARED_DATE_OF_BIRTH"
                )
        elif self.date_of_birth is None:
            raise ValueError(f"date_of_birth is required for {self.assurance_method}")
        if self.assurance_method == "VIRTUAL_TOKEN" and not (self.token_issuer or "").strip():
            raise ValueError("token_issuer is required for VIRTUAL_TOKEN")
        return self


class AgeAssuranceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    is_child: bool
    is_person_with_disability: bool
    assurance_method: str
    is_verified: bool
    assurance_reference: str
    token_issuer: Optional[str] = None
    assured_at: Optional[datetime] = None
    assured_by: Optional[str] = None
    source_app: str
    created_at: datetime
    updated_at: datetime
    #: Deliberately absent: `date_of_birth`. The finding (`is_child`) is what
    #: every consumer of this endpoint needs, and echoing a child's date of
    #: birth back on a staff-console response would spread the most sensitive
    #: field in this schema across logs, browser caches and screenshots for no
    #: operational gain. It stays encrypted in the table.


# --------------------------------------------------------------------------- #
#  F-02 / F-03: guardian consent
# --------------------------------------------------------------------------- #


class GuardianConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_external_id: str = Field(min_length=1, max_length=256)
    guardian_type: GuardianType = "PARENT"
    verification_method: GuardianVerificationMethod
    guardian_name: str = Field(min_length=1, max_length=256)
    guardian_email: str = Field(default="", max_length=256)
    guardian_phone: str = Field(default="", max_length=32)

    #: R.10(2)(a) - the already-verified adult account being relied on.
    guardian_customer_external_id: Optional[str] = Field(default=None, max_length=256)
    #: R.10(2)(b) - identity AND age details voluntarily provided.
    guardian_identity_reference: Optional[str] = Field(default=None, max_length=256)
    guardian_date_of_birth: Optional[date] = None
    #: R.10(2)(c) - the authorised entity / Digital Locker service provider,
    #: and the token it issued. PLACEHOLDER: no verifier ships with this
    #: build, so verification on this route returns 501 and the record stays
    #: PENDING. See app/services/guardian.py.
    virtual_token_issuer: Optional[str] = Field(default=None, max_length=128)
    virtual_token_reference: Optional[str] = Field(default=None, max_length=512)

    #: R.11 - required for a LAWFUL_GUARDIAN.
    appointment_authority: Optional[AppointmentAuthority] = None
    appointment_reference: str = Field(default="", max_length=256)
    appointment_date: Optional[date] = None

    @model_validator(mode="after")
    def _method_has_its_inputs(self):
        if self.verification_method == "EXISTING_VERIFIED_ACCOUNT" and not self.guardian_customer_external_id:
            raise ValueError(
                "guardian_customer_external_id is required for EXISTING_VERIFIED_ACCOUNT"
            )
        if self.verification_method == "VIRTUAL_TOKEN" and not (self.virtual_token_issuer or "").strip():
            raise ValueError("virtual_token_issuer is required for VIRTUAL_TOKEN")
        if self.guardian_type == "LAWFUL_GUARDIAN" and not (
            self.appointment_authority and self.appointment_reference.strip()
        ):
            raise ValueError(
                "A lawful guardian (Rules 2025 R.11) requires appointment_authority and "
                "appointment_reference"
            )
        return self


class GuardianConsentVerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_note: str = Field(default="", max_length=1024)


class GuardianConsentRejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rejection_reason: str = Field(min_length=1, max_length=48)


class GuardianConsentRevokeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=1024)


class GuardianConsentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reference_no: str
    customer_id: int
    guardian_type: str
    verification_method: str
    status: str
    #: Masked by the route (see `mask_identifier`) - a guardian's address is
    #: the guardian's personal data and a staff queue does not need it in the
    #: clear to work the record.
    guardian_name: str
    guardian_email: str
    guardian_is_adult: bool
    guardian_customer_id: Optional[int] = None
    guardian_context_id: Optional[int] = None
    virtual_token_issuer: Optional[str] = None
    virtual_token_verified_at: Optional[datetime] = None
    appointment_authority: Optional[str] = None
    appointment_reference: str
    appointment_date: Optional[date] = None
    verified_at: Optional[datetime] = None
    verified_by: Optional[str] = None
    evidence_ref: Optional[str] = None
    evidence_hash: Optional[str] = None
    rejection_reason: Optional[str] = None
    revoked_at: Optional[datetime] = None
    source_app: str
    created_at: datetime
    updated_at: datetime
    #: Deliberately absent: guardian_identity_reference, guardian_date_of_birth,
    #: guardian_phone, virtual_token_reference. They are the raw identity
    #: material the verification consumed; the *outcome* of consuming it is
    #: `guardian_is_adult` plus `evidence_hash`, and that is what a queue and
    #: an auditor need. Echoing a bearer token back over the API would be the
    #: worst of them.


# --------------------------------------------------------------------------- #
#  F-05: Fourth Schedule exemptions
# --------------------------------------------------------------------------- #


class ChildExemptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exemption_class: ExemptionClass
    #: The exact Fourth Schedule entry relied on. Required before the
    #: exemption can be activated - the platform routes on
    #: `exemption_class`, but the citation is what a regulator reads.
    schedule_reference: str = Field(min_length=1, max_length=128)
    #: Narrow by construction: at least one purpose, and nothing outside the
    #: list is ever exempt. An empty list is refused here and, independently,
    #: by ck_child_exemptions_purposes_not_empty.
    purpose_codes: list[str] = Field(min_length=1)
    exempted_obligations: list[ExemptibleObligation] = Field(min_length=1)
    conditions: str = Field(default="", max_length=4000)
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None

    @field_validator("purpose_codes")
    @classmethod
    def _codes_are_real(cls, v: list[str]) -> list[str]:
        cleaned = [c.strip() for c in v if (c or "").strip()]
        if not cleaned:
            raise ValueError("purpose_codes must name at least one purpose")
        return cleaned

    @model_validator(mode="after")
    def _window_is_coherent(self):
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to <= self.effective_from
        ):
            raise ValueError("effective_to must be after effective_from")
        return self


class ChildExemptionActivateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Activating an exemption is a legal judgement with a Rs. 200 crore
    #: penalty behind it if wrong, so the caller has to say, in writing, what
    #: they are relying on. Recorded on the audit row.
    justification: str = Field(min_length=1, max_length=4000)


class ChildExemptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    exemption_class: str
    schedule_reference: str
    purpose_codes: list[str]
    exempted_obligations: list[str]
    conditions: str
    is_active: bool
    effective_from: datetime
    effective_to: Optional[datetime] = None
    authorised_by: Optional[str] = None
    authorised_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
#  F-06: metrics
# --------------------------------------------------------------------------- #


class ChildrenMetricsOut(BaseModel):
    """K-24 and K-25. Both ratios are `None` rather than 0 or 100 when their
    denominator is empty - a tenant with no child accounts has not achieved
    100% parental-consent completion, it has nothing to report."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: Optional[int] = None
    metrics: dict
