"""R2-11 / gap A-08: request and response models for the s.5(2) legacy-notice
campaign.

Kept out of `schemas/schemas.py` for the same reason `schemas/reconsent.py`,
`schemas/breach.py` and `schemas/erasure.py` are: one shared file that every
lane edits is a merge conflict waiting to happen, and these shapes are read by
exactly one router.

The delivery shapes below are deliberately verbose. `LegacyNoticeDelivery`
carries `status`, `retry_count`/`max_retries`, `last_error` and four separate
timestamps rather than a single "delivered: true", because the whole reason
this screen exists is s.6(10) - the fiduciary carries the burden of proving
what it told the principal - and a boolean cannot be the proof of anything.
"""
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class LegacyCohortMemberOut(BaseModel):
    """One principal in the pre-Act cohort as the admin screen sees her.

    No email or phone: the operator is choosing a *cohort*, not reading a
    contact list, and the external id plus purpose names are enough to decide
    whether the query is picking the right people.
    """

    customer_external_id: str
    source_app: str
    consent_count: int
    purpose_names: list[str] = []
    oldest_consent_at: Optional[datetime] = None
    #: The last LEGACY_NOTICE this principal has, whatever state it is in -
    #: including FAILED, because "we tried and it bounced" must not hide
    #: inside "we never tried".
    last_notice_status: Optional[str] = None
    notified_at: Optional[datetime] = None
    last_campaign_ref: Optional[str] = None
    already_notified: bool = False


class LegacyCohortOut(BaseModel):
    generated_at: datetime
    cutoff: date
    source_app: str = ""
    #: Everyone in the cohort, before the "skip the ones already reached"
    #: filter - so the screen can say "N in cohort, M still to notify".
    cohort_size: int
    outstanding: int
    already_notified: int
    members: list[LegacyCohortMemberOut] = []
    truncated: bool = False


class LegacyNoticeSendIn(BaseModel):
    """`cutoff` has no default here on purpose.

    The date on which the Act commenced for this fiduciary's purposes is a
    legal determination, not a constant this platform is entitled to make
    silently on an operator's behalf. The admin screen pre-fills the field
    with `services/legacy_notice.py::DEFAULT_PRE_ACT_CUTOFF` and shows what
    that date is, so the operator sees and confirms it; the API refuses to
    guess.
    """

    cutoff: date
    source_app: Optional[str] = None
    channels: Optional[list[Literal["EMAIL", "SMS", "IN_APP"]]] = None
    language: str = Field(default="en", max_length=8)
    note: str = Field(default="", max_length=1000)
    #: Re-notify principals whose notice already reached them. Off by default;
    #: turning it on is how a fiduciary re-issues after a material correction
    #: to the notice itself.
    include_notified: bool = False
    limit: Optional[int] = Field(default=None, ge=1, le=10000)


class LegacyNoticeSendOut(BaseModel):
    campaign_ref: str
    cutoff: str
    source_app: str = ""
    cohort_size: int
    recipients: int
    #: Queued. Not delivered - nothing has been attempted at this point.
    notifications_queued: int
    #: Principals for whom no channel had any recipient at all, listed rather
    #: than silently dropped.
    unreachable: list[str] = []
    note: str = ""
    started_by: str = ""


class LegacyNoticeDelivery(BaseModel):
    notification_id: int
    customer_external_id: Optional[str] = None
    recipient_masked: str = ""
    channel: str
    language: str = "en"
    status: str
    subject: str = ""
    retry_count: int = 0
    max_retries: int = 0
    last_error: Optional[str] = None
    provider_ref: Optional[str] = None
    queued_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    source_app: str = ""


class LegacyNoticeCampaignOut(BaseModel):
    campaign_ref: str
    cutoff: str = ""
    note: str = ""
    source_apps: list[str] = []
    started_at: Optional[datetime] = None
    started_by: str = ""
    recipients: int = 0
    notifications: int = 0
    #: The four delivery states, kept apart. `pending` is queued and never yet
    #: attempted; it is never added to `sent` or `delivered`.
    pending: int = 0
    sent: int = 0
    delivered: int = 0
    failed: int = 0
    acknowledged: int = 0
    principals_reached: int = 0
    principals_acknowledged: int = 0
    by_channel: dict = {}
    deliveries: list[LegacyNoticeDelivery] = []


class LegacyNoticeMetricsOut(BaseModel):
    generated_at: datetime
    cutoff: str
    source_app: str = ""
    pre_act_consents: int
    pre_act_principals: int
    consents_notified: int
    principals_notified: int
    principals_acknowledged: int
    #: K-26. None (not 0.0) when there is no pre-Act cohort at all.
    legacy_notice_delivery_pct: Optional[float] = None
    campaigns: int = 0


class NotificationDispatchOut(BaseModel):
    """What `dispatch_pending` actually did, echoed back verbatim.

    Named `failed_or_retrying` rather than `failed` because the dispatcher
    cannot yet tell those apart in one pass - a row that failed its second of
    five attempts is still in flight. Collapsing the two would make a transient
    SMTP blip look like a permanent non-delivery on a compliance screen.
    """

    attempted: int
    delivered: int
    failed_or_retrying: int
