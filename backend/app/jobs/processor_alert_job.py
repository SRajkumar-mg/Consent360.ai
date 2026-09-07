"""R3-07/C-02: deliver queued processor instructions and escalate the ones
whose acknowledgement SLA has elapsed.

Runs both halves in one job so a single `scheduler_runs` row tells the whole
propagation story for that interval - how many instructions went out, how many
missed their SLA, and how many DPO escalations that produced. Splitting them
into two jobs would mean reading two rows to answer one question.

The interval (15 minutes) is deliberately much shorter than the shortest
sensible ack SLA: an escalation is only meaningful if it fires close to the
moment the SLA actually elapses, not up to an hour later.
"""
from sqlalchemy.orm import Session

from app.services.processors import dispatch_pending_alerts, escalate_overdue_alerts


def run(db: Session) -> dict:
    dispatched = dispatch_pending_alerts(db, limit=500)
    escalated = escalate_overdue_alerts(db)
    return {**dispatched, **escalated}
