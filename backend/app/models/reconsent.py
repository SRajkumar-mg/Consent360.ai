"""R1-09 (P-01, P-02, P-03, K-09): re-consent on material change.

Kept in its own module rather than in `entities.py` so these tables could be
built without contending for the single shared model file; every foreign key
names its target table as a string, so nothing here imports `entities.py` and
there is no import cycle. `entities.py` carries a single
`from app.models import reconsent  # noqa: F401` at its tail so Alembic's
autogenerate (which imports only `app.models.entities`) sees these tables.

--------------------------------------------------------------------------
WHY THIS EXISTS
--------------------------------------------------------------------------
BRD 4.1.3 puts it bluntly - *consent cannot be assumed* - and s.6(1) requires
consent to be "free, specific, informed, unconditional and unambiguous ...
signifying agreement to the processing of her personal data **for the
specified purpose**". A consent is agreement to a *particular* description of
processing. When that description changes materially, the agreement no longer
covers what is now being done, and continuing to process under it is
processing without consent - not a paperwork problem.

Before this, publishing a new `PurposeVersion` left every existing consent
untouched and silent: the consent kept pointing at the old version, the
decision engine kept answering ALLOW, and nobody was told. `PolicyChangeLog`,
`ReConsentCampaign` and the two new `consents` columns close that, and
`services/decision_engine.py` is where the closing actually bites - the flag
blocks processing, it does not merely decorate a screen.

--------------------------------------------------------------------------
MATERIALITY IS DECIDED IN ONE PLACE, AND IT IS WRITTEN DOWN
--------------------------------------------------------------------------
`app/services/material_change.py::classify_change` is the whole definition,
field by field, with the reasoning for each. It is not left implied by a diff
function. Three tiers:

* **Always material.** A widening or a change in kind: a data category or
  processing activity *added*, a data item added or its necessity changed, a
  retention period *increased*, the lawful basis changed at all,
  `requires_consent` going false -> true, `child_restricted` going false ->
  true. These cannot be overridden by a publisher, because each one means the
  principal is being asked to have agreed to something they were never shown.
* **Material by default, downgradeable on the record.** The three free-text
  fields a machine cannot judge - `description`, `services_enabled`,
  `consent_text`. A typo fix in consent text is cosmetic; a rewrite of what
  the data enables is not, and no diff algorithm can tell them apart. So the
  default is material, a publisher may downgrade one to cosmetic, and the
  downgrade is recorded on `PolicyChangeLog` with who did it and why. A
  reviewer sees the claim, not just its consequence.
* **Never material.** A pure narrowing (a category or activity removed, a
  retention period shortened, `requires_consent` true -> false,
  `child_restricted` true -> false) - the existing consent already covers a
  superset - plus `name`, `translations` and `checklist`, which are
  presentation and process metadata rather than the scope of processing.

`PolicyChangeLog` records the classification for *every* publication,
material or not, so "we decided this one was cosmetic" is itself an auditable
decision rather than an absence of one.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: What kind of versioned artefact changed. All four are things a principal
#: was shown and relied on, which is why a change to any of them can require
#: fresh consent.
CHANGE_ENTITY_TYPES = ["PURPOSE", "POLICY", "NOTICE", "COOKIE_POLICY"]

#: MATERIAL   - the change alters what the principal agreed to; affected
#:              consents are flagged and processing is blocked until fresh
#:              consent (s.6(1), BRD 4.1.3).
#: NARROWING  - the new version is a strict subset of the old one. The
#:              existing consent already covers it, so no re-consent is due -
#:              but it is recorded distinctly from COSMETIC so an auditor can
#:              see that a scope change happened and why it did not require
#:              re-consent.
#: COSMETIC   - wording, translations, review metadata. No scope change.
MATERIALITY = ["MATERIAL", "NARROWING", "COSMETIC"]

#: OPEN      - consents flagged, notifications queued, waiting for principals.
#: COMPLETED - every flagged consent has been re-consented or has otherwise
#:             left the flagged state (withdrawn, expired, denied).
#: CANCELLED - the change was rolled back or superseded before principals
#:             acted; the flags are cleared with a recorded reason.
CAMPAIGN_STATUSES = ["OPEN", "COMPLETED", "CANCELLED"]


class PolicyChangeLog(Base):
    """One row per publication of a versioned artefact, material or not.

    P-02 asks for a change log; this is it, and it is deliberately written for
    *every* publication rather than only the material ones. A log that records
    only the changes someone judged material cannot be used to check that
    judgement - the interesting question at an audit is usually "what did you
    decide was cosmetic, and why?", and a log with no row for it cannot
    answer.

    `changed_fields` is the structured diff: `{field: {"from": ..., "to": ...,
    "materiality": ..., "why": ...}}`. `materiality` on the row itself is the
    aggregate - MATERIAL if any field was.

    `overridden_by`/`override_justification` record a publisher downgrading
    one of the three free-text fields from MATERIAL to COSMETIC. Only those
    three can be downgraded (see the module docstring); an attempt to
    downgrade a widening is refused by the service, not merely discouraged.
    """

    __tablename__ = "policy_change_log"
    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('PURPOSE','POLICY','NOTICE','COOKIE_POLICY')",
            name="ck_policy_change_log_entity_type",
        ),
        CheckConstraint(
            "materiality IN ('MATERIAL','NARROWING','COSMETIC')",
            name="ck_policy_change_log_materiality",
        ),
        # An override has to say who and why; a downgrade with no justification
        # is the whole failure mode this column exists to prevent.
        CheckConstraint(
            "overridden_by IS NULL OR length(trim(override_justification)) > 0",
            name="ck_policy_change_log_override_justified",
        ),
        Index("ix_policy_change_log_entity", "entity_type", "entity_id", "to_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )
    change_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    entity_code: Mapped[str] = mapped_column(String(128), default="")
    from_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    to_version: Mapped[int] = mapped_column(Integer, nullable=False)
    materiality: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    #: {field: {"from", "to", "materiality", "why"}}
    changed_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    #: The prose summary of why this classification was reached, built from the
    #: per-field reasons so a reader gets the answer without re-deriving it.
    materiality_basis: Mapped[str] = mapped_column(Text, default="")
    overridden_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    override_justification: Mapped[str] = mapped_column(Text, default="")
    affected_consents: Mapped[int] = mapped_column(Integer, default=0)
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("re_consent_campaigns.id"), nullable=True
    )
    actor_username: Mapped[str] = mapped_column(String(64), default="system")
    source_app: Mapped[str] = mapped_column(String(128), default="")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class ReConsentCampaign(Base):
    """The re-consent drive started by one material change.

    K-09 (re-consent rate after a material change) is `fresh_consents /
    consents_flagged`, and both numbers live here: `consents_flagged` is
    frozen at launch, `fresh_consents` is counted from the consents whose flag
    has since been cleared by an actual grant or renewal. Neither is derived
    from a timestamp comparison after the fact, so a consent that was flagged,
    re-granted and later withdrawn still counts as a re-consent that happened.
    """

    __tablename__ = "re_consent_campaigns"
    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN','COMPLETED','CANCELLED')", name="ck_re_consent_campaigns_status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )
    campaign_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    entity_code: Mapped[str] = mapped_column(String(128), default="")
    from_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    to_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    #: Frozen at launch - K-09's denominator.
    consents_flagged: Mapped[int] = mapped_column(Integer, default=0)
    notifications_queued: Mapped[int] = mapped_column(Integer, default=0)
    #: Counted as flags are cleared by a real grant/renewal - K-09's numerator.
    fresh_consents: Mapped[int] = mapped_column(Integer, default=0)
    started_by: Mapped[str] = mapped_column(String(64), default="system")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str] = mapped_column(Text, default="")
    source_app: Mapped[str] = mapped_column(String(128), default="")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class CookiePolicyVersion(Base):
    """P-03: the cookie policy, versioned like every other thing a principal
    is shown.

    A cookie banner's stored preferences are a consent record with a shorter
    memory: the visitor ticked boxes against a particular description of what
    each category does and who it is shared with. Change that description and
    the stored ticks no longer mean what they meant - BRD 4.2 requires the
    change to trigger renewed consent.

    Publishing a MATERIAL version therefore does two things, both of them
    server-side and neither of them advisory:

    1. **The stored preferences are invalidated**, by clearing
       `crm_customers.consent_preferences` for the affected principals. The
       banner has nothing to read back, so it re-asks. This is why
       invalidation needs no cooperation from the four demo frontends - a
       client that never got the memo still re-asks, because the server no
       longer remembers.
    2. **The mirrored consent rows are flagged** `re_consent_required`, so the
       decision engine blocks processing under those cookie purposes until a
       fresh choice is recorded. Clearing the banner's memory alone would not
       do that: the platform's own consent rows would still say GRANTED.

    `content_hash` is SHA-256 over the canonical JSON of `categories` +
    `summary`, so "did this version actually change?" is answerable without
    trusting the publisher's version number.
    """

    __tablename__ = "cookie_policy_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "version_number", name="uq_cookie_policy_tenant_version"),
        CheckConstraint("version_number > 0", name="ck_cookie_policy_version_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    #: [{"key": "analytics", "label": ..., "description": ..., "purpose_code":
    #: ..., "shared_with": [...], "duration": ...}] - the itemised disclosure
    #: BRD 4.2 / Q-03 asks a cookie policy page to carry.
    categories: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_by: Mapped[str] = mapped_column(String(64), default="system")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    #: The change-log row for this publication, so the classification and the
    #: campaign it started are reachable from the version itself.
    change_log_id: Mapped[int | None] = mapped_column(
        ForeignKey("policy_change_log.id"), nullable=True
    )
    preferences_invalidated: Mapped[int] = mapped_column(Integer, default=0)
