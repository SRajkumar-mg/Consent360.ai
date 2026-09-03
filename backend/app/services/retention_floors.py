"""Retention floor enforcement (R1-10).

Guards every administrative purge/delete path with a statutory minimum
retention floor per record class. Principal-initiated erasure requests are
exempt (floor applies only to administrative cleanup, not rights fulfilment).
"""

from datetime import datetime, timezone

from app.models.entities import RetentionFloor

# Default floors (days) used when the retention_floors table is empty.
DEFAULT_FLOORS = {
    "consents": 365,
    "consent_evidence": 365,
    "consent_history": 365,
    "audit_logs": 365,
    "cm_records": 2555,  # 7 years if ever operating as a Consent Manager
    "application_logs": 365,
    "access_logs": 365,
}


def _today() -> datetime:
    return datetime.now(timezone.utc)


def get_minimum_days(db, record_class: str) -> int:
    """Return the configured floor for a record class (falling back to defaults)."""
    row = db.query(RetentionFloor).filter(RetentionFloor.record_class == record_class).first()
    if row:
        return row.minimum_days
    return DEFAULT_FLOORS.get(record_class, 365)


def days_since(dt: datetime | None, now: datetime | None = None) -> float:
    if dt is None:
        return float("inf")
    now = now or _today()
    return (now - dt).total_seconds() / 86400.0


def assert_above_retention_floor(
    db,
    record_class: str,
    row_age_days: float,
    *,
    allow_exception: bool = False,
) -> None:
    """Raise if the row is younger than its statutory floor.

    ``allow_exception`` is True for principal-initiated erasure (rights
    fulfilment), which overrides the floor. Administrative purge passes False.
    """
    if allow_exception:
        return
    floor = get_minimum_days(db, record_class)
    if row_age_days < floor:
        raise RuntimeError(
            f"Cannot delete/anonymise {record_class}: row age {row_age_days:.1f} days "
            f"is below the statutory retention floor of {floor} days."
        )
