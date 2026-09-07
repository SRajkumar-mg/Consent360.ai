"""R2-11 / gap A-08 (K-26): the s.5(2) legacy-notice campaign.

--------------------------------------------------------------------------
WHY THIS EXISTS
--------------------------------------------------------------------------
s.5(2) of the DPDP Act is the provision nobody wants to read twice:

    "The Data Fiduciary shall, in respect of personal data processed on the
    basis of consent given by a Data Principal *before the commencement of
    this Act*, give to her, **as soon as reasonably practicable**, a notice
    [containing the s.5(1) particulars] ... and may continue to process the
    personal data **until the Data Principal withdraws her consent**."

Two things follow, and the second is the one products get wrong.

1. The Act does **not** grandfather old consent. A principal who ticked a box
   in 2019 was never shown a s.5(1) notice, so the fiduciary owes her one now.
   Not a marketing email; a notice with the itemised particulars.
2. "May continue to process ... until she withdraws" is only lawful if
   withdrawal is *actually available to her*. A notice that says "your consent
   continues unless you object" and offers no working way to object converts a
   statutory permission into a trap. So this module never sends a notice
   without the tenant's live withdrawal path in it, and the in-app copy of
   every notice lands in the principal's own portal, where the withdrawal
   button is one click away and is the same button the grant flow uses.

And because s.6(10) puts the burden of proving consent-and-notice on the
fiduciary, "we sent it" has to be a **record**, not an assertion: every notice
is a `Notification` row with its channel, its recipient, its attempts, its
send/delivery/acknowledgement timestamps and its last error - written by the
one dispatch path (`app/services/notifications.py`) that already carries
retries and backoff. There is no second sender here.

--------------------------------------------------------------------------
THE COHORT IS DERIVED, NOT FLAGGED - AND THAT IS DELIBERATE
--------------------------------------------------------------------------
The gap register (A-08) notes there is no "legacy" flag on consents. There
still is not, and this module does not add one, because a stored boolean
would be a *frozen opinion* about a legal date that is still moving: the Act's
commencement notification (G.S.R. 843(E)) brings the substantive sections into
force in two tranches, advisers disagree by a day on the anniversaries, and a
fiduciary may reasonably take a different view for its own sector. A column
written once by whoever ran the backfill first would then quietly outlive the
reasoning behind it.

Instead the cohort is a **query against an operator-supplied cut-off**:

    coalesce(renewed_at, granted_at, created_at) < cutoff

i.e. "the consent this platform is relying on today was last affirmed before
that date". A renewal after the cut-off is a fresh affirmative act and takes
the consent *out* of the cohort - which is right, because the principal did
consent again, under the current notice.

The cut-off actually used is stamped on every notification queued
(`details.legacy_cutoff`) and on the audit row that opened the campaign, so
the record answers "which date did you take as the commencement, and when did
you decide that?" rather than merely "who did you email?".

`DEFAULT_PRE_ACT_CUTOFF` below is the *engineering* default, not legal advice
- see its own comment.

--------------------------------------------------------------------------
A CAMPAIGN WITHOUT A CAMPAIGNS TABLE
--------------------------------------------------------------------------
A legacy-notice campaign is identified by a `campaign_ref` stamped into every
notification it queues (`Notification.details.legacy_campaign_ref`), and read
back by grouping on that. There is deliberately no new table and no new
migration:

* Every fact a campaign row would hold is already a fact about the
  notifications it sent - who, which channel, when, delivered or not - and a
  summary table would be a second copy that can disagree with them. The
  numbers on the admin screen are therefore *counted from the delivery rows
  every time*, never from a cached total that a failed retry could leave
  stale.
* `ReConsentCampaign` was considered and rejected. It is the drive started by
  one material change, and K-09 (`fresh_consents / consents_flagged`) is
  computed by summing over every row in that table - so parking legacy
  campaigns there would silently corrupt a published KPI with a denominator
  that has nothing to do with re-consent.

The one thing this costs is that a campaign with zero recipients leaves no
trace in `notifications`. That is why `send_legacy_notices` writes its
`LEGACY_NOTICE_CAMPAIGN_STARTED` audit row **before** it queues anything and
regardless of the cohort size: the decision to run a campaign is auditable
even when it turned out there was nobody to notify.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.entities import Consent, Customer, Notification, Organization, Purpose
from app.services.audit import log_audit
from app.services.notifications import queue_notification
from app.services.tenancy import resolve_tenant_id

logger = logging.getLogger("app.legacy_notice")

#: The statuses in which a consent is actually being *relied on* to process.
#: A DENIED/WITHDRAWN/EXPIRED consent authorises nothing, so s.5(2) - which is
#: about data "processed on the basis of consent given before commencement" -
#: has nothing to bite on. Same tuple the portal and the decision engine treat
#: as live.
RELIED_ON_STATUSES = ("GRANTED", "ACTIVE", "RENEWED", "UPDATED")

#: A notice that has actually left the building. PENDING is queued and not yet
#: attempted; FAILED exhausted its retries. Neither is delivery, and neither
#: may ever be counted as one - see `campaign_summary`.
REACHED_STATUSES = ("SENT", "DELIVERED", "ACKNOWLEDGED")

#: Engineering default for "the commencement of this Act" as this platform
#: computes the cohort. 13 May 2027 is the 18-month tranche of the Act's
#: commencement notification (G.S.R. 843(E)) under which the remaining
#: substantive sections - s.5 among them - become enforceable; the Rules
#: (G.S.R. 846(E), 13 Nov 2025) set the same date at Rule 1(3)-(4).
#:
#: It is a DEFAULT, not a determination. Some advisers compute the anniversary
#: as 12 May 2027, a fiduciary may take an earlier date for its own sector,
#: and the operator is the one who must decide. Every campaign therefore
#: records the date it was actually run with, and the admin screen makes the
#: field editable rather than presenting this constant as settled law.
DEFAULT_PRE_ACT_CUTOFF = date(2027, 5, 13)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _campaign_ref() -> str:
    return f"LNC-{uuid.uuid4().hex[:12].upper()}"


def as_cutoff_datetime(cutoff: date | datetime) -> datetime:
    """Midnight UTC at the start of `cutoff`, so the comparison is
    `< cutoff` - a consent affirmed *on* the commencement date is post-Act."""
    if isinstance(cutoff, datetime):
        return cutoff if cutoff.tzinfo else cutoff.replace(tzinfo=timezone.utc)
    return datetime.combine(cutoff, time.min, tzinfo=timezone.utc)


#: `coalesce(renewed_at, granted_at, created_at)` - the moment the consent
#: being relied on today was last affirmed. Defined once because the cohort
#: query, the metrics query and the per-principal detail must never drift.
def _last_affirmed():
    return func.coalesce(Consent.renewed_at, Consent.granted_at, Consent.created_at)


@dataclass
class CohortMember:
    """One principal in the pre-Act cohort, with what she is owed a notice
    about and whether she has already had one."""

    customer: Customer
    #: Copied off `customer` once, at construction.
    #:
    #: Not re-read off the nested Customer at each use, because
    #: `tests/test_customer_resolution_guard.py` treats a two-level identity
    #: traversal off some other object as the shape a second, unscoped identity
    #: resolution hides in - and that guard is right to, even though this
    #: module never resolves a Customer from a caller-supplied identifier at
    #: all (the cohort is built from consents by date, and `customer` comes off
    #: an already-fetched Consent row). Holding the value on the dataclass
    #: keeps the guard honest
    #: rather than earning an allow-list entry that would also cover any
    #: traversal a later edit adds here.
    external_id: str
    consent_count: int
    purpose_names: list[str] = field(default_factory=list)
    purpose_codes: list[str] = field(default_factory=list)
    oldest_consent_at: Optional[datetime] = None
    source_app: str = ""
    notified_at: Optional[datetime] = None
    last_notice_status: Optional[str] = None
    last_campaign_ref: Optional[str] = None

    @property
    def already_notified(self) -> bool:
        return self.last_notice_status in REACHED_STATUSES


def _legacy_notices_by_customer(db: Session, customer_ids: Iterable[int]) -> dict[int, Notification]:
    """The most recent LEGACY_NOTICE per customer, whatever its state.

    Deliberately returns the latest row even when it FAILED: "we tried and it
    bounced" is a materially different answer from "we never tried", and a
    screen that showed only successes would let the second hide inside the
    first.
    """
    ids = [c for c in customer_ids if c]
    if not ids:
        return {}
    rows = (
        db.query(Notification)
        .filter(Notification.event_type == "LEGACY_NOTICE", Notification.customer_id.in_(ids))
        .order_by(Notification.customer_id, Notification.created_at.desc(), Notification.id.desc())
        .all()
    )
    latest: dict[int, Notification] = {}
    for row in rows:
        # A customer's rows arrive newest-first; keep the first reached one if
        # there is any, otherwise the newest attempt.
        current = latest.get(row.customer_id)
        if current is None:
            latest[row.customer_id] = row
        elif current.status not in REACHED_STATUSES and row.status in REACHED_STATUSES:
            latest[row.customer_id] = row
    return latest


def build_cohort(
    db: Session,
    *,
    cutoff: date | datetime,
    source_app: Optional[str] = None,
    include_notified: bool = True,
    limit: Optional[int] = None,
) -> list[CohortMember]:
    """Every principal still being processed under a consent last affirmed
    before `cutoff`.

    `source_app` is the tenant filter (docs/ARCHITECTURE.md: every list endpoint scopes
    on it); `include_notified=False` is what a "send to the ones we have not
    reached yet" run uses, and it excludes only principals whose notice
    actually *reached* them - a FAILED notice leaves them in the cohort,
    because a bounce is not a notice.
    """
    cutoff_dt = as_cutoff_datetime(cutoff)
    q = (
        db.query(Consent)
        .filter(
            Consent.status.in_(RELIED_ON_STATUSES),
            _last_affirmed() < cutoff_dt,
        )
    )
    if source_app:
        q = q.filter(Consent.source_app == source_app)
    consents = q.order_by(Consent.customer_id).all()

    all_purposes = db.query(Purpose).all()
    purpose_names = {p.id: (p.name or p.code) for p in all_purposes}
    purpose_codes = {p.id: p.code for p in all_purposes}

    grouped: dict[int, CohortMember] = {}
    for consent in consents:
        customer = consent.customer
        if customer is None or customer.status == "PURGED":
            continue
        member = grouped.get(consent.customer_id)
        if member is None:
            member = CohortMember(
                customer=customer,
                external_id=customer.external_id,
                consent_count=0,
                source_app=consent.source_app or customer.source_app,
            )
            grouped[consent.customer_id] = member
        member.consent_count += 1
        name = purpose_names.get(consent.purpose_id)
        code = purpose_codes.get(consent.purpose_id)
        if name and name not in member.purpose_names:
            member.purpose_names.append(name)
        if code and code not in member.purpose_codes:
            member.purpose_codes.append(code)
        affirmed = consent.renewed_at or consent.granted_at or consent.created_at
        if affirmed and (member.oldest_consent_at is None or affirmed < member.oldest_consent_at):
            member.oldest_consent_at = affirmed

    notices = _legacy_notices_by_customer(db, grouped.keys())
    for customer_id, member in grouped.items():
        notice = notices.get(customer_id)
        if notice is not None:
            member.last_notice_status = notice.status
            member.notified_at = notice.delivered_at or notice.sent_at
            member.last_campaign_ref = (notice.details or {}).get("legacy_campaign_ref")

    members = sorted(grouped.values(), key=lambda m: (m.oldest_consent_at or utcnow()))
    if not include_notified:
        members = [m for m in members if not m.already_notified]
    if limit is not None:
        members = members[:limit]
    return members


def _tenant_for(db: Session, source_app: str) -> Optional[Organization]:
    if not source_app:
        return None
    return db.query(Organization).filter(Organization.code == source_app).first()


def compose_notice_body(
    *,
    tenant: Optional[Organization],
    member: CohortMember,
    cutoff: date | datetime,
) -> str:
    """The s.5(1) particulars, written out for one principal.

    Composed here rather than left to the template because the substance is
    per-principal (which purposes, since when) and because a template a staff
    user can edit must not be the only thing standing between the fiduciary
    and an incomplete statutory notice. The template still wraps this - see
    FALLBACK_TEMPLATES["LEGACY_NOTICE"] - so an operator can change the
    salutation and framing without being able to delete the particulars.
    """
    cutoff_text = as_cutoff_datetime(cutoff).date().isoformat()
    org_name = tenant.name if tenant else (member.source_app or "this organisation")
    purposes = ", ".join(member.purpose_names) or "the purposes you originally agreed to"
    since = member.oldest_consent_at.date().isoformat() if member.oldest_consent_at else "an earlier date"

    lines = [
        f"{org_name} holds personal data about you that we have been processing on the basis of "
        f"consent you gave on or before {since} - that is, before the Digital Personal Data "
        f"Protection Act's notice requirements took effect on {cutoff_text}. You were not given "
        f"the notice the Act now requires at the time, so we are giving it to you here.",
        "",
        f"What we process, and why: {purposes}. "
        f"This covers {member.consent_count} consent record(s) held under your account.",
        "",
        "Your rights: you may ask us for a summary of the personal data we hold about you and how "
        "we process it, ask us to correct or complete it, ask us to erase it, nominate someone to "
        "exercise these rights if you die or become incapacitated, and complain to us about how we "
        "have handled any of this.",
        "",
        "Withdrawing your consent: you can withdraw at any time, and it must be as easy to withdraw "
        "as it was to give consent. We may continue processing this data until you withdraw - so if "
        "you do not want us to, please withdraw. Nothing here asks you to do anything to keep your "
        "consent in place, and not replying is not agreement.",
    ]
    if tenant and tenant.withdraw_url:
        lines.append(f"Withdraw here: {tenant.withdraw_url}")
    lines.append(
        "You can also withdraw, and see every consent we hold for you, from the consent portal "
        "linked in this message - the same screen you use to give consent."
    )
    if tenant and tenant.rights_url:
        lines.append(f"Your rights and how to exercise them: {tenant.rights_url}")
    if tenant and (tenant.dpo_name or tenant.dpo_email):
        contact = " / ".join(x for x in (tenant.dpo_name, tenant.dpo_email, tenant.dpo_phone) if x)
        lines.append(f"Data Protection Officer: {contact}")
    if tenant and tenant.grievance_url:
        lines.append(f"To raise a grievance with us: {tenant.grievance_url}")
    if tenant and tenant.board_complaint_url:
        lines.append(
            f"If you are not satisfied with our response you may complain to the Data Protection "
            f"Board of India: {tenant.board_complaint_url}"
        )
    return "\n".join(lines)


def send_legacy_notices(
    db: Session,
    *,
    cutoff: date | datetime,
    source_app: Optional[str] = None,
    channels: Optional[list[str]] = None,
    language: str = "en",
    note: str = "",
    include_notified: bool = False,
    limit: Optional[int] = None,
    actor_username: str = "system",
    request_id: Optional[str] = None,
) -> dict:
    """Queue one s.5(2) notice per principal in the cohort.

    Returns the campaign ref and what was queued. **Queued**, not delivered:
    this function never claims delivery. `app/services/notifications.py::
    dispatch_pending` (the scheduled `notification_dispatch` job, or the staff
    "send queued notices now" action) is what attempts the transport, and the
    `Notification.status` it writes is the only thing the admin screen reads.

    A principal with no email is still notified: `queue_notification` skips a
    channel it has no recipient for, and IN_APP always has one, so the notice
    reaches her portal even when nothing can be mailed. That matters because
    the portal copy is the one that sits next to the withdrawal button.
    """
    cutoff_dt = as_cutoff_datetime(cutoff)
    ref = _campaign_ref()
    members = build_cohort(
        db, cutoff=cutoff_dt, source_app=source_app,
        include_notified=include_notified, limit=limit,
    )
    tenant_id = resolve_tenant_id(db, source_app) if source_app else None

    # Written BEFORE anything is queued and whether or not the cohort is
    # empty: "we ran a campaign on this cut-off and found nobody" is itself
    # the answer to an audit question, and it has to be on the record.
    log_audit(
        db, "LEGACY_NOTICE_CAMPAIGN_STARTED", actor_username=actor_username,
        source_app=source_app or "UI", tenant_id=tenant_id,
        reason=(
            f"s.5(2) legacy-notice campaign {ref} opened over consents last affirmed before "
            f"{cutoff_dt.date().isoformat()}: {len(members)} principal(s) in cohort"
        ),
        request_id=request_id,
        metadata={
            "legacy_campaign_ref": ref,
            "legacy_cutoff": cutoff_dt.date().isoformat(),
            "cohort_size": len(members),
            "source_app": source_app or "",
            "channels": channels or ["EMAIL", "IN_APP"],
            "include_already_notified": include_notified,
            "note": note[:500],
        },
    )

    queued_total = 0
    recipients = 0
    skipped: list[str] = []
    for member in members:
        member_source = member.source_app or member.customer.source_app or (source_app or "")
        tenant = _tenant_for(db, member_source)
        body = compose_notice_body(tenant=tenant, member=member, cutoff=cutoff_dt)
        context = {
            "legacy_campaign_ref": ref,
            "legacy_cutoff": cutoff_dt.date().isoformat(),
            "law_effective_date": cutoff_dt.date().isoformat(),
            "details": body,
            "purposes": ", ".join(member.purpose_names),
            "consent_count": member.consent_count,
            "consent_since": member.oldest_consent_at.date().isoformat() if member.oldest_consent_at else "",
            "organisation": tenant.name if tenant else member_source,
            "withdraw_url": tenant.withdraw_url if tenant else "",
            "rights_url": tenant.rights_url if tenant else "",
            "grievance_url": tenant.grievance_url if tenant else "",
            "board_complaint_url": tenant.board_complaint_url if tenant else "",
            "dpo_email": tenant.dpo_email if tenant else "",
            "campaign_note": note,
        }
        try:
            queued = queue_notification(
                db, customer=member.customer, event_type="LEGACY_NOTICE",
                source_app=member_source, channels=channels, language=language,
                context=context, request_id=request_id, actor_username=actor_username,
            )
        except Exception:  # noqa: BLE001 - one principal must not abort the campaign
            db.rollback()
            logger.exception(
                "Failed to queue the s.5(2) legacy notice for customer_id=%s in campaign %s",
                member.customer.id, ref,
            )
            skipped.append(member.external_id)
            continue
        if not queued:
            # No channel had a recipient at all. Recorded rather than silently
            # dropped: a principal we cannot reach on any channel is exactly
            # the one an auditor will ask about.
            skipped.append(member.external_id)
            continue
        recipients += 1
        queued_total += len(queued)
        log_audit(
            db, "LEGACY_NOTICE_SENT", actor_username=actor_username, source_app=member_source,
            tenant_id=queued[0].tenant_id, customer_id=member.customer.id,
            customer_external_id=member.external_id,
            reason=(
                f"s.5(2) legacy notice queued for delivery under campaign {ref} "
                f"({len(queued)} channel(s))"
            ),
            request_id=request_id,
            metadata={
                "legacy_campaign_ref": ref,
                "notification_ids": [n.id for n in queued],
                "channels": [n.channel for n in queued],
                "consent_count": member.consent_count,
            },
        )

    return {
        "campaign_ref": ref,
        "cutoff": cutoff_dt.date().isoformat(),
        "source_app": source_app or "",
        "cohort_size": len(members),
        "recipients": recipients,
        "notifications_queued": queued_total,
        "unreachable": skipped,
        "note": note,
        "started_by": actor_username,
    }


# ---------------------------------------------------------------------------
# Reading the record back
# ---------------------------------------------------------------------------
def _campaign_rows(db: Session, *, source_app: Optional[str] = None, limit: int = 5000) -> list[Notification]:
    q = db.query(Notification).filter(Notification.event_type == "LEGACY_NOTICE")
    if source_app:
        q = q.filter(Notification.source_app == source_app)
    return q.order_by(Notification.id.desc()).limit(limit).all()


def _summarise(ref: str, rows: list[Notification]) -> dict:
    """Delivery state counted from the rows themselves, every time.

    The four buckets are kept apart on purpose. PENDING is *queued and not yet
    attempted* and is never folded into "sent"; FAILED has exhausted its
    retries and is never folded into anything. The admin screen renders these
    verbatim, so it can never show "delivered" for a notice that has only been
    written to a queue.
    """
    by_status: dict[str, int] = {}
    by_channel: dict[str, dict[str, int]] = {}
    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
        channel = by_channel.setdefault(row.channel, {"queued": 0, "reached": 0, "failed": 0})
        channel["queued"] += 1
        if row.status in REACHED_STATUSES:
            channel["reached"] += 1
        elif row.status == "FAILED":
            channel["failed"] += 1

    reached_customers = {r.customer_id for r in rows if r.status in REACHED_STATUSES and r.customer_id}
    all_customers = {r.customer_id for r in rows if r.customer_id}
    acknowledged_customers = {r.customer_id for r in rows if r.status == "ACKNOWLEDGED" and r.customer_id}
    started = min((r.created_at for r in rows), default=None)
    details = rows[0].details or {} if rows else {}

    return {
        "campaign_ref": ref,
        "cutoff": details.get("legacy_cutoff", ""),
        "note": details.get("campaign_note", ""),
        "source_apps": sorted({r.source_app for r in rows if r.source_app}),
        "started_at": started,
        "started_by": rows[0].actor_username if rows else "",
        "recipients": len(all_customers),
        "notifications": len(rows),
        "pending": by_status.get("PENDING", 0),
        "sent": by_status.get("SENT", 0),
        "delivered": by_status.get("DELIVERED", 0),
        "failed": by_status.get("FAILED", 0),
        "acknowledged": by_status.get("ACKNOWLEDGED", 0),
        "principals_reached": len(reached_customers),
        "principals_acknowledged": len(acknowledged_customers),
        "by_channel": by_channel,
    }


def list_campaigns(db: Session, *, source_app: Optional[str] = None, limit: int = 100) -> list[dict]:
    rows = _campaign_rows(db, source_app=source_app)
    grouped: dict[str, list[Notification]] = {}
    for row in rows:
        ref = (row.details or {}).get("legacy_campaign_ref")
        if not ref:
            # A LEGACY_NOTICE queued through POST /notifications/trigger by
            # hand carries no campaign ref. It is still a notice that was
            # sent, so it is shown under an explicit bucket rather than
            # dropped - the admin screen labels it as ad-hoc.
            ref = "(ad-hoc)"
        grouped.setdefault(ref, []).append(row)
    summaries = [_summarise(ref, rows) for ref, rows in grouped.items()]
    summaries.sort(key=lambda s: (s["started_at"] or utcnow()), reverse=True)
    return summaries[:limit]


def campaign_detail(db: Session, campaign_ref: str, *, source_app: Optional[str] = None) -> Optional[dict]:
    """One campaign's summary plus its per-recipient delivery evidence."""
    rows = [
        r for r in _campaign_rows(db, source_app=source_app)
        if ((r.details or {}).get("legacy_campaign_ref") or "(ad-hoc)") == campaign_ref
    ]
    if not rows:
        return None
    summary = _summarise(campaign_ref, rows)
    customers = {
        c.id: c for c in db.query(Customer).filter(
            Customer.id.in_([r.customer_id for r in rows if r.customer_id])
        ).all()
    }
    from app.core.utils import mask_identifier

    deliveries = []
    for row in sorted(rows, key=lambda r: (r.customer_id or 0, r.channel)):
        customer = customers.get(row.customer_id) if row.customer_id else None
        deliveries.append({
            "notification_id": row.id,
            "customer_external_id": customer.external_id if customer else None,
            # The address is masked on the way out, and the principal's NAME is
            # not returned at all: a delivery-evidence screen has to prove a
            # notice went somewhere plausible, for a principal an operator can
            # identify by her external id. It does not need to reprint her
            # contact details to everyone holding `policy.view`.
            "recipient_masked": mask_identifier(row.recipient) if row.recipient else "",
            "channel": row.channel,
            "language": row.language,
            "status": row.status,
            "subject": row.subject,
            "retry_count": row.retry_count,
            "max_retries": row.max_retries,
            "last_error": row.last_error,
            "provider_ref": row.provider_ref,
            "queued_at": row.created_at,
            "sent_at": row.sent_at,
            "delivered_at": row.delivered_at,
            "acknowledged_at": row.acknowledged_at,
            "source_app": row.source_app,
        })
    summary["deliveries"] = deliveries
    return summary


def legacy_notice_metrics(
    db: Session,
    *,
    cutoff: date | datetime = DEFAULT_PRE_ACT_CUTOFF,
    source_app: Optional[str] = None,
) -> dict:
    """K-26: pre-Act consents notified / total pre-Act consents.

    The denominator is *consents*, as the register defines it, not principals
    - one principal with six pre-Act consents is six records that need a
    notice behind them. The numerator counts a consent as notified only when
    its principal has a LEGACY_NOTICE that actually reached her
    (SENT/DELIVERED/ACKNOWLEDGED). A queued-but-unsent notice counts for
    nothing here, which is the whole point of measuring it.
    """
    cutoff_dt = as_cutoff_datetime(cutoff)
    q = db.query(Consent).filter(
        Consent.status.in_(RELIED_ON_STATUSES),
        _last_affirmed() < cutoff_dt,
    )
    if source_app:
        q = q.filter(Consent.source_app == source_app)
    consents = q.all()
    total = len(consents)

    notice_q = db.query(Notification.customer_id).filter(
        Notification.event_type == "LEGACY_NOTICE",
        Notification.status.in_(REACHED_STATUSES),
    )
    if source_app:
        notice_q = notice_q.filter(Notification.source_app == source_app)
    reached = {row[0] for row in notice_q.all() if row[0]}

    acked_q = db.query(Notification.customer_id).filter(
        Notification.event_type == "LEGACY_NOTICE",
        Notification.status == "ACKNOWLEDGED",
    )
    if source_app:
        acked_q = acked_q.filter(Notification.source_app == source_app)
    acknowledged = {row[0] for row in acked_q.all() if row[0]}

    notified = sum(1 for c in consents if c.customer_id in reached)
    principals = {c.customer_id for c in consents}

    return {
        "generated_at": utcnow(),
        "cutoff": cutoff_dt.date().isoformat(),
        "source_app": source_app or "",
        "pre_act_consents": total,
        "pre_act_principals": len(principals),
        "consents_notified": notified,
        "principals_notified": len(principals & reached),
        "principals_acknowledged": len(principals & acknowledged),
        # None, not 0.0, when there is no pre-Act cohort at all: "nothing to
        # notify" is not "0% notified".
        "legacy_notice_delivery_pct": round(notified / total * 100, 2) if total else None,
        "campaigns": len({
            (r.details or {}).get("legacy_campaign_ref")
            for r in _campaign_rows(db, source_app=source_app)
            if (r.details or {}).get("legacy_campaign_ref")
        }),
    }
