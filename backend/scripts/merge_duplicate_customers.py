"""One-off: merge duplicate Customer rows that share the same email.

The CRM login used to create a fresh Customer reference (email-derived external id)
even when a seeded Customer with the same email already existed, splitting consents
between two rows. This merges each email group into the lowest id and moves/merges
consent, context and audit records via bulk SQL, then deletes the duplicates.

Run:  .venv\\Scripts\\python.exe scripts/merge_duplicate_customers.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func

from app.core.database import SessionLocal
from app.models.entities import (
    AuditLog,
    Consent,
    ConsentContext,
    ConsentEvidence,
    ConsentHistory,
    Customer,
)

db = SessionLocal()
try:
    groups = (
        db.query(func.lower(Customer.email), func.count(Customer.id))
        .group_by(func.lower(Customer.email))
        .having(func.count(Customer.id) > 1)
        .all()
    )
    merged = 0
    for _email, _count in groups:
        rows = db.query(Customer).filter(func.lower(Customer.email) == _email).order_by(Customer.id).all()
        keeper, dups = rows[0], rows[1:]
        for dup in dups:
            dup_consent_ids = [c.id for c in db.query(Consent.id).filter(Consent.customer_id == dup.id).all()]
            keeper_consent_ids = [c.id for c in db.query(Consent.id).filter(Consent.customer_id == keeper.id).all()]
            collisions = (
                db.query(Consent)
                .filter(
                    Consent.customer_id == dup.id,
                    Consent.purpose_id.in_(db.query(Consent.purpose_id).filter(Consent.id.in_(keeper_consent_ids))),
                )
                .all()
            ) if keeper_consent_ids else []
            collide = set()
            for c in collisions:
                same = (
                    db.query(Consent.id)
                    .filter(
                        Consent.customer_id == keeper.id,
                        Consent.purpose_id == c.purpose_id,
                        Consent.data_category_id == c.data_category_id,
                        Consent.processing_activity_id == c.processing_activity_id,
                    )
                    .first()
                )
                if same:
                    collide.add(same[0])
                    db.query(AuditLog).filter(AuditLog.consent_id == c.id).delete(synchronize_session=False)
                    db.query(ConsentEvidence).filter(ConsentEvidence.consent_id == c.id).delete(synchronize_session=False)
                    db.query(ConsentHistory).filter(ConsentHistory.consent_id == c.id).delete(synchronize_session=False)
                    db.query(Consent).filter(Consent.id == c.id).delete(synchronize_session=False)
                    dup_consent_ids = [x for x in dup_consent_ids if x != c.id]
            if collide:
                db.query(AuditLog).filter(AuditLog.consent_id.in_(collide)).delete(synchronize_session=False)
                db.query(ConsentEvidence).filter(ConsentEvidence.consent_id.in_(collide)).delete(synchronize_session=False)
                db.query(ConsentHistory).filter(ConsentHistory.consent_id.in_(collide)).delete(synchronize_session=False)
                db.query(Consent).filter(Consent.id.in_(collide)).delete(synchronize_session=False)
            if dup_consent_ids:
                db.query(Consent).filter(Consent.customer_id == dup.id).update(
                    {Consent.customer_id: keeper.id}, synchronize_session=False
                )
            db.query(ConsentContext).filter(ConsentContext.customer_id == dup.id).update(
                {ConsentContext.customer_id: keeper.id}, synchronize_session=False
            )
            db.query(AuditLog).filter(AuditLog.customer_id == dup.id).update(
                {AuditLog.customer_id: keeper.id}, synchronize_session=False
            )
            db.query(Customer).filter(Customer.id == dup.id).delete(synchronize_session=False)
            merged += 1
            print(f"merged {dup.email} (id={dup.id}) into id={keeper.id}")
    db.commit()
    print(f"done: {merged} duplicates merged")
finally:
    db.close()