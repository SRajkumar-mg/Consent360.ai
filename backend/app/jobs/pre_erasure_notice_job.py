"""R1-06/G-03: send the DPDP Rules 2025 R.8(2) pre-erasure notice.

Every authorised erasure job that has not yet had its notice gets one, queued
through the notification service on the principal's registered channels. The
job's `execute_after` is then set to now + the policy's notice period, and
nothing can execute before that instant.

This job is what makes the 48 hours real rather than nominal, in both
directions: it is the only writer of `ErasureJob.notice_sent_at`, and
`execute_erasure_job` refuses any job where that field is null. A notice that
could not be queued on any channel leaves the field null and the erasure
blocked - no notice, no erasure.

Runs hourly. The interval must stay well inside the shortest notice period
configurable (48 hours), or a job could sit un-notified long enough that the
erasure it is holding up looks like a backlog rather than a wait.
"""
from sqlalchemy.orm import Session

from app.services.erasure import send_due_pre_erasure_notices


def run(db: Session) -> dict:
    return send_due_pre_erasure_notices(db, actor_username="scheduler")
