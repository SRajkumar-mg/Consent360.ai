"""R1-06/G-02: the DPDP Rules 2025 R.8(1) + Third Schedule three-year clock.

Finds principals who have neither approached this fiduciary for the specified
purpose nor exercised their rights within the period configured for their
tenant (three years by default, which is the Third Schedule period for the
classes it names), and proposes an erasure for each.

Proposes only - see `erasure_retention_scan_job` for why. Also returns
`inactivity_clock_breaches` (K-31): principals past the period with neither an
erasure in flight nor a legal hold explaining why not, which is the number
that should be zero.
"""
from sqlalchemy.orm import Session

from app.services.erasure import inactivity_scan


def run(db: Session) -> dict:
    return inactivity_scan(db, actor_username="scheduler")
