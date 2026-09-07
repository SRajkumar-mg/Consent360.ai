"""R1-06/G-01: propose erasures for principals whose retention period has run.

Read-only with respect to personal data: it creates PROPOSED `erasure_jobs`
rows and nothing else. Every one of them is unauthorised, so the executor job
will not touch it until a named human signs it - a scan noticing that a period
elapsed is an inference about somebody else's data, not a decision anyone made.

Distinct from R1-10's `retention_scan` job, which asks a different question
(was any *record class* destroyed before its statutory floor?) and is also
read-only. Two scans, two questions, deliberately not one name.
"""
from sqlalchemy.orm import Session

from app.services.erasure import erasure_retention_scan


def run(db: Session) -> dict:
    return erasure_retention_scan(db, actor_username="scheduler")
