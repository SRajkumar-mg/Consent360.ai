"""R1-10/S-05: run the retention scan on a schedule.

The scan is a read - it deletes nothing and enforces nothing on its own. Its
value here is that it runs unattended and leaves a `scheduler_runs` row plus a
RETENTION_SCAN_RUN audit entry every day, so "zero records deleted before
their floor" is a continuously re-checked fact with a dated trail behind it
rather than a number someone produced once for a report.

Enforcement is deliberately NOT scheduled. Destroying records is an authorised
act with a named actor behind it (POST /retention/enforce, gated on
policy.manage), not something a background job should do while nobody is
looking.
"""
from sqlalchemy.orm import Session

from app.services.retention import retention_scan


def run(db: Session) -> dict:
    result = retention_scan(db, actor_username="scheduler")
    return {
        "classes_scanned": result["classes_scanned"],
        "deleted_before_floor": result["deleted_before_floor"],
        "violations": len(result["violations"]),
        "compliant": result["compliant"],
    }
