"""R2-06: request/response models for the grievance redressal module.

Kept in its own module (not `schemas/schemas.py`) for the same reason the
ORM models are - see `app/models/grievance.py`'s closing note.

Two conventions worth knowing before adding a field:

* A grievance is addressed by its `reference_no` everywhere in the API, never
  by its primary key. The reference is the handle a complainant already
  holds; exposing the surrogate id in URLs would hand out a sequential
  enumeration oracle over a register of complaints, which is precisely what
  `reference_no`'s CSPRNG generation exists to avoid.
* The narrative fields (`subject`, `description`, `resolution_summary`,
  `feedback_comment`) are encrypted at rest. They round-trip through these
  models in the clear because the ORM decrypts on read - which is exactly why
  every route that returns one is permission- or context-token-gated.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.grievance import GRIEVANCE_CATEGORIES, GRIEVANCE_CHANNELS, GRIEVANCE_STATUSES

_CATEGORY_PATTERN = "^(" + "|".join(GRIEVANCE_CATEGORIES) + ")$"
_CHANNEL_PATTERN = "^(" + "|".join(GRIEVANCE_CHANNELS) + ")$"
_STATUS_PATTERN = "^(" + "|".join(GRIEVANCE_STATUSES) + ")$"

# A complaint needs room to be a complaint. 20 000 characters is generous
# prose and still a hard bound on what one unauthenticated-ish portal
# submission can push into an encrypted column.
_MAX_DESCRIPTION = 20_000


# --------------------------------------------------------------------------- #
#  Inputs
# --------------------------------------------------------------------------- #

class GrievanceSelfIn(BaseModel):
    """The data principal's own submission, via the portal form
    (X-Context-Token). The complainant is taken from the context token, never
    from the body - a submission cannot name someone else as the aggrieved."""

    category: str = Field(pattern=_CATEGORY_PATTERN)
    subject: str = Field(default="", max_length=256)
    description: str = Field(min_length=1, max_length=_MAX_DESCRIPTION)
    purpose_code: Optional[str] = Field(default=None, max_length=64)
    consent_id: Optional[int] = None


class GrievanceStaffIn(BaseModel):
    """A handler logging a grievance that arrived off-platform (email, phone,
    post) on a principal's behalf. It enters the same register, gets the same
    reference, and starts the same clock."""

    customer_external_id: str = Field(min_length=1, max_length=256)
    category: str = Field(pattern=_CATEGORY_PATTERN)
    subject: str = Field(default="", max_length=256)
    description: str = Field(min_length=1, max_length=_MAX_DESCRIPTION)
    channel: str = Field(default="STAFF", pattern=_CHANNEL_PATTERN)
    purpose_code: Optional[str] = Field(default=None, max_length=64)
    consent_id: Optional[int] = None
    # Optional: the date the complaint actually reached the organisation, when
    # it is being back-entered. The clock runs from THIS, not from the moment
    # a handler got round to typing it in - a fiduciary cannot extend its own
    # statutory response period by being slow to log a complaint.
    received_at: Optional[datetime] = None


class GrievanceUpdateIn(BaseModel):
    """Triage: assign it, move it into progress, add an internal note."""

    assigned_to: Optional[str] = Field(default=None, max_length=128)
    status: Optional[str] = Field(default=None, pattern="^(IN_PROGRESS)$")
    note: str = Field(default="", max_length=4000)
    # An internal triage note is not shown to the complainant unless the
    # handler says so.
    visible_to_principal: bool = False


class GrievanceResolveIn(BaseModel):
    resolution_summary: str = Field(min_length=1, max_length=_MAX_DESCRIPTION)


class GrievanceCloseIn(BaseModel):
    note: str = Field(default="", max_length=4000)


class GrievanceEscalateIn(BaseModel):
    """Manual escalation. `reason` is free text for why a handler escalated
    ahead of the deadline; the automatic path uses the fixed reason
    "RESPONSE_PERIOD_ELAPSED"."""

    reason: str = Field(default="", max_length=64)
    note: str = Field(default="", max_length=4000)


class GrievanceFeedbackIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=4000)


# --------------------------------------------------------------------------- #
#  Outputs
# --------------------------------------------------------------------------- #

class GrievanceEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event: str
    from_status: str = ""
    to_status: str = ""
    note: str = ""
    actor_username: str = ""
    created_at: datetime


class GrievanceOut(BaseModel):
    """The full record. Returned to staff, and to the complainant for their
    own grievance (with the timeline filtered to principal-visible entries -
    see routes/grievances.py::_out)."""

    model_config = ConfigDict(from_attributes=True)

    reference_no: str
    status: str
    category: str
    channel: str
    subject: str = ""
    description: str
    customer_id: int
    customer_external_id: str = ""
    purpose_id: Optional[int] = None
    consent_id: Optional[int] = None
    source_app: str = ""

    received_at: datetime
    acknowledged_at: Optional[datetime] = None
    # The tenant's published response period, snapshotted at receipt. Echoed
    # on every read so a complainant (and an auditor) can see the deadline and
    # the promise it came from without a second lookup.
    response_days: int
    due_at: datetime
    days_remaining: int = 0
    overdue: bool = False

    escalated_at: Optional[datetime] = None
    escalated_to: str = ""
    escalation_reason: str = ""

    assigned_to: str = ""
    resolved_at: Optional[datetime] = None
    resolved_by: str = ""
    resolution_summary: str = ""
    closed_at: Optional[datetime] = None

    feedback_rating: Optional[int] = None
    feedback_comment: str = ""
    feedback_at: Optional[datetime] = None

    created_at: datetime
    updated_at: datetime
    events: list[GrievanceEventOut] = Field(default_factory=list)


class GrievanceAcknowledgementOut(BaseModel):
    """What a submission returns. Deliberately NOT just `GrievanceOut`: the
    DoD is that a grievance gets a reference number AND an acknowledgement on
    submission, and this is the acknowledgement - the reference to quote, the
    period we published, the date we owe an answer by, and who it escalates to
    if we miss it."""

    grievance: GrievanceOut
    reference_no: str
    acknowledged: bool
    acknowledgement_message: str
    response_days: int
    due_at: datetime
    grievance_officer: str = ""
    board_complaint_url: str = ""
    # Ids of the Notification rows queued for the complainant, so a caller can
    # follow delivery through GET /notifications.
    notification_ids: list[int] = Field(default_factory=list)


class GrievanceQueueStatsOut(BaseModel):
    """The admin queue's header numbers, and the DoD's "closed within the
    published period" measure."""

    total: int
    open: int
    overdue: int
    escalated: int
    by_status: dict[str, int]
    by_category: dict[str, int]
    resolved_total: int
    resolved_within_period: int
    # resolved_within_period / resolved_total, as a percentage. None when
    # nothing has been resolved yet - deliberately not 0.0, which would read
    # as "we close nothing on time" rather than "there is nothing to measure".
    on_time_closure_rate: Optional[float] = None
    average_days_to_resolution: Optional[float] = None
