"""R2-06: escalate grievances that have passed their published response period.

This is the "overdue items escalate automatically" half of R2-06's definition
of done, and it is deliberately a scheduled sweep rather than a check on the
read path. A grievance nobody opens in the admin queue is precisely the one
most likely to run past its deadline; tying escalation to somebody looking at
the record would escalate the complaints already being worked and miss the
ones that were forgotten - which is the entire failure mode this exists to
catch.

Runs through `JobRunner` like every other job here, so each sweep leaves a
`scheduler_runs` row carrying how many grievances were overdue, how many were
escalated and how many DPO notifications that produced. The counts come
straight from `services/grievance.py::escalate_overdue_grievances`.

Interval: hourly. The response period is measured in days (1-90), so an hour
is comfortably fine-grained relative to the deadline it watches, while being
cheap enough that the sweep is effectively free on an empty queue. Note that
`SCHEDULER_ENABLED` defaults to **false** (tests rely on that), so a
deployment that wants automatic escalation must set it.
"""
from sqlalchemy.orm import Session

from app.services.grievance import escalate_overdue_grievances


def run(db: Session) -> dict:
    return escalate_overdue_grievances(db)
