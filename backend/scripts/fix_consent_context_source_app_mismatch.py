"""One-off correction: repoint `ConsentContext` rows whose `source_app`
disagrees with the `source_app` of the `Customer` they point at.

Found by a round-2 security review of the cross-tenant customer-context fix
(see app/services/context.py::create_context_for_customer and
app/api/routes/portal.py::_resolve_customer_and_context, which now both
refuse to resolve such a mismatched pair at read time). Five such rows exist
in the dev database today, all minted by app/api/routes/crm.py::_issue_context
for the CRM/Codex/SkillLearn shared-identity trio: `_infer_source_app_from_request`
guessed `CRM_PORTAL` from the request's Origin/Referer header at mint time,
while the linked Customer's own (authoritative - see the module comment in
crm.py) `source_app` was actually CODEX or SKILLLEARN. Every one of these
rows is already harmless *by accident*: `is_active=False`, its JWT `exp` has
passed (so `decode_token` refuses it before any row is even read), and
context_token_cleanup_job.py has already hashed the stored `token` column so
the original raw token no longer exists anywhere to replay. None of that was
a deliberate control against this specific mismatch, so this script closes
the mismatch itself rather than relying on those three accidents to keep
lining up.

Action taken: `ConsentContext.source_app` is repointed to match the linked
Customer's own `source_app` (the authoritative value, same convention as
backfill_source_app_misattribution.py already used for the analogous
Consent-row mislabeling), and `is_active` is explicitly re-affirmed False
(a no-op today, made explicit rather than assumed).

Reversible: before changing anything, every touched row's prior values are
written to a timestamped JSON snapshot file next to this script. Re-run with
--revert <snapshot-file> to restore the original source_app values (an
idempotent no-op for any row that no longer matches its post-fix value).

Idempotent going forward: the query below requires
ConsentContext.source_app != Customer.source_app, which no longer holds
once a row has been corrected.

Usage:
    cd backend
    python -m scripts.fix_consent_context_source_app_mismatch                    # dry run
    python -m scripts.fix_consent_context_source_app_mismatch --apply            # writes the changes
    python -m scripts.fix_consent_context_source_app_mismatch --revert <file>    # dry run of revert
    python -m scripts.fix_consent_context_source_app_mismatch --revert <file> --apply
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.models.entities import ConsentContext, Customer
from app.services.audit import log_audit


def _forward(db, apply_changes: bool) -> None:
    affected = (
        db.query(ConsentContext, Customer.source_app)
        .join(Customer, Customer.id == ConsentContext.customer_id)
        .filter(ConsentContext.source_app != Customer.source_app)
        .all()
    )
    if not affected:
        print("Nothing to fix - no ConsentContext/Customer source_app mismatches found.")
        return

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contexts": [
            {
                "id": ctx.id, "old_source_app": ctx.source_app, "new_source_app": true_source_app,
                "old_is_active": ctx.is_active, "customer_id": ctx.customer_id,
            }
            for ctx, true_source_app in affected
        ],
    }

    print(f"Found {len(affected)} ConsentContext rows whose source_app disagrees with their customer's:")
    by_tenant: dict[str, int] = {}
    for ctx, true_source_app in affected:
        print(f"  context.id={ctx.id} customer.id={ctx.customer_id} "
              f"{ctx.source_app!r} -> {true_source_app!r}  (is_active={ctx.is_active})")
        by_tenant[true_source_app] = by_tenant.get(true_source_app, 0) + 1

    snapshot_path = Path(__file__).resolve().parent / (
        f"consent_context_source_app_snapshot_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    )
    snapshot_path.write_text(json.dumps(snapshot, indent=2))
    print(f"Snapshot of prior values written to {snapshot_path}")

    if not apply_changes:
        print("Dry run only - re-run with --apply to write these changes.")
        return

    for ctx, true_source_app in affected:
        ctx.source_app = true_source_app
        ctx.is_active = False  # explicit re-affirmation, not an assumption

    for tenant, count in by_tenant.items():
        log_audit(
            db, "CONTEXT_SOURCE_APP_CORRECTED", actor_username="fix_consent_context_source_app_mismatch",
            actor_type="SYSTEM", source_app=tenant,
            reason=(
                f"Repointed {count} ConsentContext row(s) from a mismatched source_app to '{tenant}' "
                f"(the linked Customer's own, authoritative source_app) - crm.py::_issue_context's "
                f"Origin-header heuristic mis-guessed the source_app at mint time. All were already "
                f"inactive and expired; also explicitly re-affirmed is_active=False."
            ),
            metadata={"snapshot_file": str(snapshot_path)},
            commit=False,
        )
    db.commit()
    print(f"Applied. {len(affected)} ConsentContext rows corrected.")


def _revert(db, snapshot_file: str, apply_changes: bool) -> None:
    snapshot_path = Path(snapshot_file)
    if not snapshot_path.is_absolute():
        snapshot_path = Path(__file__).resolve().parent / snapshot_path
    if not snapshot_path.exists():
        print(f"Snapshot file not found: {snapshot_path}")
        sys.exit(1)
    snapshot = json.loads(snapshot_path.read_text())

    restored = 0
    for row in snapshot.get("contexts", []):
        ctx = db.get(ConsentContext, row["id"])
        if not ctx or ctx.source_app != row["new_source_app"]:
            continue  # already reverted, or changed again since - do not clobber
        if apply_changes:
            ctx.source_app = row["old_source_app"]
            ctx.is_active = row["old_is_active"]
        restored += 1

    print(f"{'Would restore' if not apply_changes else 'Restoring'} {restored} ConsentContext row(s) "
          f"from {snapshot_path.name}.")
    if not apply_changes:
        print("Dry run only - re-run with --apply to write these changes.")
        return
    if restored:
        log_audit(
            db, "CONTEXT_SOURCE_APP_RESTORED", actor_username="fix_consent_context_source_app_mismatch",
            actor_type="SYSTEM", source_app="",
            reason=f"Reverted {restored} ConsentContext row(s) using snapshot {snapshot_path.name}.",
            metadata={"snapshot_file": str(snapshot_path)},
            commit=False,
        )
        db.commit()
        print("Applied.")
    else:
        print("Nothing to restore.")


def main() -> None:
    apply_changes = "--apply" in sys.argv
    db = SessionLocal()
    if "--revert" in sys.argv:
        idx = sys.argv.index("--revert")
        if idx + 1 >= len(sys.argv):
            print("Usage: python -m scripts.fix_consent_context_source_app_mismatch --revert <snapshot-file> [--apply]")
            sys.exit(1)
        _revert(db, sys.argv[idx + 1], apply_changes)
    else:
        _forward(db, apply_changes)
    db.close()


if __name__ == "__main__":
    main()
