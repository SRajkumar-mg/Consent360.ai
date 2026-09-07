"""One-off: merge duplicate Customer rows that share the same email WITHIN
the same tenant.

The CRM login used to create a fresh Customer reference (email-derived
external id) even when a seeded Customer with the same email already
existed, splitting consents between two rows. This merges each (email,
source_app) group into the lowest id and moves/merges consent, context and
audit records via bulk SQL, then deletes the duplicates.

R3 fix, two bugs: (1) this used to group by `func.lower(Customer.email)`
ALONE, globally - so two customers who happen to share an email at two
DIFFERENT, unrelated tenants (an ordinary occurrence; see
app/services/context.py's tenant-scoping note) were treated as "duplicates"
and merged into one row, deleting one tenant's customer outright and
repointing their contexts, consents and audit rows onto the other tenant's
survivor. The grouping key now includes `source_app`, so a cross-tenant
"group" cannot exist in the first place - not merely guarded against after
the fact. Emails that DO appear under multiple source_apps are still
detected and reported (informational only, never acted on) so an operator
can see the shared-email-across-tenants cases this script now deliberately
leaves alone. (2) `Customer.email` is an AES-256-GCM `EncryptedString` with
a random nonce per write when `FIELD_ENCRYPTION_KEY` is set (see
app/core/encryption.py and docs/ARCHITECTURE.md's field-encryption note) - `func.lower()`
at the SQL level operated on ciphertext, so it silently never matched two
rows with the same plaintext email at all once encryption was enabled. This
now groups by `Customer.email_search` (the deterministic HMAC digest
companion column that exists exactly for this kind of equality/grouping
operation), the same idiom every other lookup in this codebase uses.

Reversible: before changing anything, every group about to be merged is
written to a timestamped JSON snapshot file next to this script (customer
ids, emails, source_apps, and which id survives) - full reconstruction of a
merge is not automatic (rows are deleted), but the snapshot is enough for a
human to know exactly what was merged and rebuild it from the (untouched)
audit_logs ledger if ever needed.

Usage:
    cd backend
    python -m scripts.merge_duplicate_customers            # dry run
    python -m scripts.merge_duplicate_customers --apply     # writes the changes
"""

import json
import sys
from datetime import datetime, timezone
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
from app.services.audit import log_audit


def main(snapshot_dir: Path | None = None) -> None:
    """``snapshot_dir`` defaults to this script's own directory (the normal
    CLI usage); tests pass a temporary directory so repeated test runs don't
    litter scripts/ with snapshot files from database states that were
    never real."""
    apply_changes = "--apply" in sys.argv
    snapshot_dir = snapshot_dir or Path(__file__).resolve().parent
    db = SessionLocal()
    try:
        # Cross-tenant email sharing: report only, never merge.
        cross_tenant = (
            db.query(Customer.email_search)
            .filter(Customer.email_search.isnot(None))
            .group_by(Customer.email_search)
            .having(func.count(func.distinct(Customer.source_app)) > 1)
            .all()
        )
        if cross_tenant:
            print(f"{len(cross_tenant)} email(s) shared across DIFFERENT tenants - left untouched "
                  f"(this is expected: two unrelated tenants may each have a customer with the same "
                  f"email; see app/services/context.py's tenant-scoping note):")
            for (_email_search,) in cross_tenant:
                rows = db.query(Customer.id, Customer.email, Customer.source_app).filter(
                    Customer.email_search == _email_search
                ).all()
                tenants = sorted({source_app for _id, _email, source_app in rows})
                print(f"  {rows[0].email}: {[cid for cid, _e, _s in rows]} across tenants {tenants}")

        groups = (
            db.query(Customer.email_search, Customer.source_app, func.count(Customer.id))
            .filter(Customer.email_search.isnot(None))
            .group_by(Customer.email_search, Customer.source_app)
            .having(func.count(Customer.id) > 1)
            .all()
        )
        if not groups:
            print("Nothing to merge - no same-tenant duplicate emails found.")
            return

        snapshot = {"generated_at": datetime.now(timezone.utc).isoformat(), "groups": []}
        print(f"Found {len(groups)} same-tenant duplicate-email group(s):")
        for _email_search, _source_app, _count in groups:
            rows = (
                db.query(Customer)
                .filter(Customer.email_search == _email_search, Customer.source_app == _source_app)
                .order_by(Customer.id)
                .all()
            )
            snapshot["groups"].append({
                "email": rows[0].email, "source_app": _source_app,
                "keeper_id": rows[0].id, "duplicate_ids": [r.id for r in rows[1:]],
            })
            print(f"  {rows[0].email} @ {_source_app}: keeping id={rows[0].id}, "
                  f"merging {[r.id for r in rows[1:]]} into it")

        snapshot_path = snapshot_dir / (
            f"merge_duplicate_customers_snapshot_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        )
        snapshot_path.write_text(json.dumps(snapshot, indent=2))
        print(f"Snapshot of the merge plan written to {snapshot_path}")

        if not apply_changes:
            print("Dry run only - re-run with --apply to write these changes.")
            return

        merged = 0
        for group in snapshot["groups"]:
            keeper = db.get(Customer, group["keeper_id"])
            source_app = group["source_app"]
            for dup_id in group["duplicate_ids"]:
                dup = db.get(Customer, dup_id)
                if not dup:
                    continue
                # Same-tenant merge only: the grouping key above already
                # guarantees dup.source_app == keeper.source_app == source_app,
                # but this is asserted explicitly rather than assumed - a
                # cross-tenant merge must be structurally impossible here,
                # not merely absent from today's query.
                if dup.source_app != source_app or keeper.source_app != source_app:
                    print(f"  REFUSING to merge id={dup.id} (source_app={dup.source_app!r}) into "
                          f"id={keeper.id} (source_app={keeper.source_app!r}) - tenant mismatch")
                    continue

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
                log_audit(
                    db, "CUSTOMER_MERGED", actor_username="merge_duplicate_customers", actor_type="SYSTEM",
                    source_app=source_app, customer_id=keeper.id, customer_external_id=keeper.external_id,
                    reason=f"Duplicate customer id={dup.id} (same email, same tenant) merged into id={keeper.id}",
                    metadata={"duplicate_id": dup.id, "snapshot_file": str(snapshot_path)},
                    commit=False,
                )
                db.query(Customer).filter(Customer.id == dup.id).delete(synchronize_session=False)
                merged += 1
                print(f"merged {dup.email} (id={dup.id}) into id={keeper.id}")
        db.commit()
        print(f"done: {merged} duplicates merged")
    finally:
        db.close()


if __name__ == "__main__":
    main()
