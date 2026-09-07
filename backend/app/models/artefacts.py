"""R3-10 (CM-01, CM-02, CM-06, J-04): Consent Manager interoperability tables.

Separate module, not `entities.py`
----------------------------------
These tables live here rather than in `app/models/entities.py` only so this
work could be built in parallel with three other lanes editing that single
file. They use the same declarative `Base` (``app.core.database``), so they
are part of the same SQLAlchemy registry and the same metadata as every other
table - but Alembic's autogenerate only sees a table whose class has been
imported by the time `Base.metadata` is read, and `alembic/env.py` imports
`entities.py`, not this module. `entities.py` therefore ends with
``from app.models import artefacts``, alongside the same line for the breach
and grievance modules. Every foreign key into an existing table is declared by
*string* target (``"organizations.id"``, ``"customers.id"``, ``"consents.id"``,
``"purposes.id"``) so this module never imports `entities.py` and no import
cycle is possible.

What these tables are for
-------------------------
DPDP Act s.2(g)/s.6(7) and the DPDP Rules 2025 First Schedule Part B create a
**Consent Manager** (CM): a Board-registered intermediary through which a Data
Principal can *give, manage, review and withdraw* consent to Data Fiduciaries
that are **onboarded** onto that CM. Consent360 is the fiduciary-side consent
platform; this is the interoperability surface a registered CM talks to.

  consent_managers            - the registered CM itself, its Board
                                registration and its Part B 11 transparency
                                disclosures (J-04).
  consent_manager_fiduciaries - the onboarding model: which fiduciary
                                (tenant) each CM is authorised to act for,
                                and for which purposes. This row IS the
                                authorisation; nothing else grants a CM
                                reach into a tenant.
  consent_artefacts           - one brokered consent record (the "artefact"),
                                per fiduciary x principal x grant.
  consent_artefact_events     - the artefact's versions. Every version is an
                                immutable, HMAC-signed event (CREATED /
                                UPDATED / WITHDRAWN / EXPIRED), so "versioned
                                artefact API with signed events" is a
                                property of the stored record, not of the
                                response serialiser.
  consent_artefact_links      - which internal `consents` rows an artefact
                                actually stands for. The artefact is a
                                facade over the existing consent lifecycle:
                                every state change still goes through
                                `app/services/consent.py`, never through
                                these tables.
  consent_manager_api_calls   - per-call availability/latency samples for
                                CM-06 / K-42 / K-43.

Retention
---------
`consent_artefacts` and `consent_artefact_events` are Consent Manager records
under First Schedule Part B 3 and 4(c) and carry a **7-year retention floor**.
That floor is owned by R1-10 (`app/services/retention.py`), which already
defines a `consent_manager_records` overlay class at 7 years; these two tables
belong under it. Declared here as `RETENTION_FLOOR_CLASSES` so the requirement
is discoverable from the model definition, and reported to the integrating
lane (which routed it to R1-10) rather than wired in from here - there is
deliberately no second, competing retention mechanism in this module.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.encryption import EncryptedJSON, EncryptedString, EncryptedText


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
#  Enumerations
# --------------------------------------------------------------------------- #

# A CM is only allowed to act once the Board has actually registered it
# (s.6(9) / R.4). PENDING and SUSPENDED both mean "may not broker consent";
# they are distinguished so an operator can tell "not yet live" from
# "stopped", which matters for the Part B 13 reporting obligation.
CM_REGISTRATION_STATUSES = ["PENDING", "REGISTERED", "SUSPENDED", "DEREGISTERED"]

# The onboarding relationship's own lifecycle, independent of the CM's Board
# registration: a registered CM can still be un-onboarded from (or suspended
# by) any individual fiduciary.
CM_ONBOARDING_STATUSES = ["PENDING", "ACTIVE", "SUSPENDED", "TERMINATED"]

ARTEFACT_STATUSES = ["ACTIVE", "PARTIAL", "WITHDRAWN", "EXPIRED"]

# One event per artefact version. CREATED is always version 1.
ARTEFACT_EVENT_TYPES = ["CREATED", "UPDATED", "WITHDRAWN", "EXPIRED"]

ARTEFACT_LINK_STATUSES = ["ACTIVE", "WITHDRAWN"]

#: Tables in this module that are Consent Manager records under DPDP Rules
#: 2025 First Schedule Part B 3 / 4(c) and therefore carry the 7-year
#: retention floor. Consumed by R1-10's retention register, not by anything
#: here - see this module's docstring.
RETENTION_FLOOR_CLASSES = ("consent_artefacts", "consent_artefact_events")


class ConsentManager(Base):
    """A Board-registered Consent Manager (DPDP Act s.2(g), s.6(7)-(9), R.4).

    `tenant_id` is the Organization row whose API keys authenticate this CM.
    A CM is a tenant in its own right - it is NOT one of the fiduciaries it
    brokers for - which is what makes the cross-tenant question here real:
    the calling key's tenant is the CM, while the data being acted on belongs
    to a *different* tenant. `consent_manager_fiduciaries` is the only thing
    that bridges the two, and it must be an explicit, dated, revocable row.
    """

    __tablename__ = "consent_managers"
    __table_args__ = (
        CheckConstraint(
            "registration_status IN ('PENDING','REGISTERED','SUSPENDED','DEREGISTERED')",
            name="ck_consent_managers_registration_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cm_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    # One Organization authenticates exactly one CM: if two CM rows shared a
    # tenant, a key issued to that tenant would resolve ambiguously and the
    # onboarding check below would depend on which row happened to be found
    # first. Unique at the database level rather than by convention.
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), unique=True, nullable=False, index=True
    )
    board_registration_number: Mapped[str] = mapped_column(String(128), default="")
    registration_status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    contact_email: Mapped[str] = mapped_column(EncryptedString(512), default="")
    website_url: Mapped[str] = mapped_column(String(512), default="")
    # First Schedule Part B 11 (gap J-04): promoters, directors, key
    # managerial personnel and persons holding more than 2% shareholding,
    # published for transparency. A JSON blob rather than four more tables: it
    # is published verbatim and never queried by field.
    #
    # Encrypted at rest even though `GET /consent-manager/disclosures` publishes
    # it: the names of directors, promoters and shareholders are personal data
    # of those natural persons, and "we publish it anyway" is a statement about
    # one intentional disclosure channel, not a reason to leave it readable to
    # anyone who reaches the database by some other route. EncryptedJSON costs
    # nothing here because nothing filters on it.
    disclosures: Mapped[dict] = mapped_column(EncryptedJSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    fiduciaries: Mapped[list["ConsentManagerFiduciary"]] = relationship(
        "ConsentManagerFiduciary", back_populates="consent_manager", order_by="ConsentManagerFiduciary.id"
    )


class ConsentManagerFiduciary(Base):
    """The onboarding model: Data Fiduciary <-> Consent Manager.

    First Schedule Part B 1 lets a principal act "through" a CM only for
    fiduciaries **onboarded** onto it. This row is that onboarding, and it is
    the *sole* authorisation that lets a CM's API key touch a tenant that is
    not its own. Consequences that are deliberate:

    - No row, or a row not in status ACTIVE, means the CM cannot see that the
      tenant exists at all through this API (404/403, never a partial read).
    - `allowed_purpose_codes`, when non-empty, bounds what the CM may broker
      for that fiduciary. Empty means "every active purpose", which is the
      ordinary case; a bounded list is available for a fiduciary that only
      wants some purposes intermediated.
    - `terminated_at` is kept rather than the row deleted, because the
      artefacts the CM created while onboarded remain valid records that an
      auditor must be able to attribute.
    """

    __tablename__ = "consent_manager_fiduciaries"
    __table_args__ = (
        UniqueConstraint("consent_manager_id", "tenant_id", name="uq_cm_fiduciary_identity"),
        CheckConstraint(
            "status IN ('PENDING','ACTIVE','SUSPENDED','TERMINATED')",
            name="ck_cm_fiduciaries_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    consent_manager_id: Mapped[int] = mapped_column(
        ForeignKey("consent_managers.id"), nullable=False, index=True
    )
    tenant_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    # Denormalised from organizations.code for the same reason every other
    # table in this codebase carries source_app alongside tenant_id: it is
    # the value every query, filter and audit row is keyed on.
    source_app: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    allowed_purpose_codes: Mapped[list] = mapped_column(JSON, default=list)
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    onboarded_by: Mapped[str] = mapped_column(String(64), default="system")
    # Free text written by a staff user about a commercial relationship. Nobody
    # can promise what an operator will type into a notes field, so it is
    # encrypted like every other free-text column in this schema.
    notes: Mapped[str] = mapped_column(EncryptedText, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    consent_manager: Mapped["ConsentManager"] = relationship(
        "ConsentManager", back_populates="fiduciaries"
    )


class ConsentArtefact(Base):
    """One consent artefact: the interoperable record of one brokered consent.

    An artefact is a *facade* over the platform's own consent rows, never a
    parallel lifecycle. `consent_artefact_links` names the `consents` rows it
    stands for, and every transition on those rows still goes through
    `app/services/consent.py` (grant/withdraw/renew), which keeps the history,
    evidence, receipt, audit and processor-propagation behaviour identical to
    a consent captured through any other channel.

    `consent_manager_id` is NULL for a fiduciary using this API directly for
    its own tenant, and set when a CM brokered it. `principal_ref` is the
    pseudonymous principal identifier disclosed to a CM (see
    `app/services/consent_manager.py::principal_pseudonym` and the data-blind
    decision record) - it is stored so the same principal maps to the same
    reference across that CM's artefacts without the CM ever holding a direct
    identifier.
    """

    __tablename__ = "consent_artefacts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE','PARTIAL','WITHDRAWN','EXPIRED')",
            name="ck_consent_artefacts_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artefact_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # Wide enough for a URI: TS 27560 6.3.3.2 defines schema_version as "a
    # unique reference for the implementation documentation", so this holds a
    # URN/URL, not a version number.
    schema_version: Mapped[str] = mapped_column(String(256), nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    source_app: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    consent_manager_id: Mapped[int | None] = mapped_column(
        ForeignKey("consent_managers.id"), nullable=True, index=True
    )
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    principal_ref: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", index=True)
    artefact_version: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="consent-manager")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    events: Mapped[list["ConsentArtefactEvent"]] = relationship(
        "ConsentArtefactEvent",
        back_populates="artefact",
        order_by="ConsentArtefactEvent.artefact_version",
    )
    links: Mapped[list["ConsentArtefactLink"]] = relationship(
        "ConsentArtefactLink", back_populates="artefact", order_by="ConsentArtefactLink.id"
    )


class ConsentArtefactEvent(Base):
    """One immutable, signed version of an artefact.

    `payload` is a self-contained snapshot of the artefact as it stood at that
    version - it restates the purposes, notice hashes and state rather than
    referencing rows that may later change, exactly like `ConsentReceipt`.
    `payload_hash` is sha256 over the canonical JSON and `signature` is
    `app.core.encryption.hmac_signature(payload_hash)`; verification uses
    `hmac_signature_matches`, which accepts any key in the search-digest key
    ring so an HMAC key rotation does not turn every historical artefact into
    a false tamper alarm. No second signing scheme is introduced here.
    """

    __tablename__ = "consent_artefact_events"
    __table_args__ = (
        UniqueConstraint("artefact_id", "artefact_version", name="uq_artefact_event_version"),
        CheckConstraint(
            "event_type IN ('CREATED','UPDATED','WITHDRAWN','EXPIRED')",
            name="ck_consent_artefact_events_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artefact_id: Mapped[int] = mapped_column(
        ForeignKey("consent_artefacts.id"), nullable=False, index=True
    )
    event_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    artefact_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # EncryptedJSON, not JSON. The payload is a snapshot, and a snapshot of a
    # record about a person contains that person's data: for a fiduciary
    # reading its own tenant it carries the external id and a masked email, and
    # for every caller it carries the tenant's DPO contact - values that are
    # themselves EncryptedString columns on `organizations`, so storing the
    # snapshot as plain JSON would decrypt them on read and write them back out
    # in the clear. Hashing and signing are unaffected: the TypeDecorator
    # encrypts on write and returns the same dict on read, so
    # `canonical_payload_hash` sees identical input either way.
    payload: Mapped[dict] = mapped_column(EncryptedJSON, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    signature_alg: Mapped[str] = mapped_column(String(32), default="HMAC-SHA256")
    actor: Mapped[str] = mapped_column(String(64), default="consent-manager")
    source_app: Mapped[str] = mapped_column(String(128), default="")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    artefact: Mapped["ConsentArtefact"] = relationship("ConsentArtefact", back_populates="events")


class ConsentArtefactLink(Base):
    """Which internal `consents` row an artefact stands for, one row per
    (artefact, consent). Consent rows are per customer x purpose x data
    category x processing activity x source_app, so one artefact purpose
    normally expands to several links."""

    __tablename__ = "consent_artefact_links"
    __table_args__ = (
        UniqueConstraint("artefact_id", "consent_id", name="uq_artefact_link_identity"),
        CheckConstraint("status IN ('ACTIVE','WITHDRAWN')", name="ck_consent_artefact_links_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artefact_id: Mapped[int] = mapped_column(
        ForeignKey("consent_artefacts.id"), nullable=False, index=True
    )
    consent_id: Mapped[int] = mapped_column(ForeignKey("consents.id"), nullable=False, index=True)
    purpose_id: Mapped[int] = mapped_column(ForeignKey("purposes.id"), nullable=False, index=True)
    purpose_code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    artefact: Mapped["ConsentArtefact"] = relationship("ConsentArtefact", back_populates="links")


class ConsentManagerApiCall(Base):
    """One availability/latency sample per Consent-Manager-facing API call
    (CM-06, K-42 "CM API availability and latency", K-43 "record-retrieval
    time").

    Persisted rather than left to the Prometheus histogram in
    `app/core/metrics.py`: R.4's metric is a reportable figure about a
    registered CM's service, so it has to survive a process restart and be
    answerable without a Prometheus server in front of it. Deliberately holds
    no request or response body - only the route, outcome and duration.
    """

    __tablename__ = "consent_manager_api_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    consent_manager_id: Mapped[int | None] = mapped_column(
        ForeignKey("consent_managers.id"), nullable=True, index=True
    )
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True
    )
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(8), default="GET")
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), default="SUCCESS", index=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
