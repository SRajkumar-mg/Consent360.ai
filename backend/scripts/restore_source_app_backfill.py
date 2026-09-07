"""Reverse of ``backfill_source_app_misattribution.py``.

That script corrects ``Consent`` / ``ConsentHistory`` / ``ConsentEvidence``
rows mis-stamped ``source_app="CRM_PORTAL"`` and, before touching anything,
writes every affected row's PRIOR values to a timestamped snapshot file
(``backfill_source_app_snapshot_<UTC timestamp>.json``, written next to this
script). Until now that snapshot was write-only: nothing in the repo could
read it back, so a correction believed to be wrong after the fact had no way
to be undone short of hand-editing the database from the JSON.

This script is that missing reverse: given one such snapshot file, it writes
each row's ``old_source_app`` (and, for ``consents``/``consent_evidence``,
``old_tenant_id``) back onto the row it came from.

Idempotent in the same sense as the forward script: re-running against a
snapshot that has already been restored is a no-op, because the WHERE clause
below requires the row's CURRENT ``source_app`` to still be the *corrected*
(``new_source_app``) value - once restored, it no longer is, so the row is
skipped on a second run.

Never touches ``audit_logs`` - exactly like the forward script, this leaves
the immutable ledger alone and instead appends one new, clearly labelled
``CONSENT_SOURCE_APP_RESTORED`` event per affected tenant summarising the
rollback.

Usage:
    cd backend
    python -m scripts.restore_source_app_backfill <snapshot-file>            # dry run
    python -m scripts.restore_source_app_backfill <snapshot-file> --apply    # writes the changes
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.models.entities import Consent, ConsentEvidence, ConsentHistory
from app.services.audit import log_audit


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply_changes = "--apply" in sys.argv
    if not args:
        print("Usage: python -m scripts.restore_source_app_backfill <snapshot-file> [--apply]")
        sys.exit(1)

    snapshot_path = Path(args[0])
    if not snapshot_path.is_absolute():
        snapshot_path = Path(__file__).resolve().parent / snapshot_path
    if not snapshot_path.exists():
        print(f"Snapshot file not found: {snapshot_path}")
        sys.exit(1)

    snapshot = json.loads(snapshot_path.read_text())
    db = SessionLocal()

    restored = {"consents": 0, "consent_history": 0, "consent_evidence": 0}
    by_tenant: dict[str, int] = {}

    for row in snapshot.get("consents", []):
        consent = db.get(Consent, row["id"])
        if not consent or consent.source_app != row["new_source_app"]:
            continue  # already restored, or changed again since - do not clobber
        if apply_changes:
            consent.source_app = row["old_source_app"]
            consent.tenant_id = row["old_tenant_id"]
        restored["consents"] += 1
        by_tenant[row["new_source_app"]] = by_tenant.get(row["new_source_app"], 0) + 1

    for row in snapshot.get("consent_history", []):
        history = db.get(ConsentHistory, row["id"])
        if not history or history.source_app != row["new_source_app"]:
            continue
        if apply_changes:
            history.source_app = row["old_source_app"]
        restored["consent_history"] += 1

    for row in snapshot.get("consent_evidence", []):
        evidence = db.get(ConsentEvidence, row["id"])
        if not evidence or evidence.source_app != row["new_source_app"]:
            continue
        if apply_changes:
            evidence.source_app = row["old_source_app"]
            evidence.tenant_id = row["old_tenant_id"]
        restored["consent_evidence"] += 1

    print(
        f"{'Would restore' if not apply_changes else 'Restoring'} "
        f"{restored['consents']} Consent rows, {restored['consent_history']} ConsentHistory rows, "
        f"{restored['consent_evidence']} ConsentEvidence rows from {snapshot_path.name}."
    )
    for tenant, count in by_tenant.items():
        print(f"  -> {tenant}: {count} consents reverted to '{snapshot.get('mis_stamped_source_app')}'")

    if not apply_changes:
        print("Dry run only - re-run with --apply to write these changes.")
        db.close()
        return

    if restored["consents"] or restored["consent_history"] or restored["consent_evidence"]:
        for tenant, count in by_tenant.items():
            log_audit(
                db, "CONSENT_SOURCE_APP_RESTORED", actor_username="restore_source_app_backfill",
                actor_type="SYSTEM", source_app=tenant,
                reason=(
                    f"Reverted {count} consent rows from '{tenant}' back to "
                    f"'{snapshot.get('mis_stamped_source_app')}' - undoing "
                    f"backfill_source_app_misattribution.py using snapshot {snapshot_path.name}."
                ),
                metadata={"snapshot_file": str(snapshot_path)},
                commit=False,
            )
        db.commit()
        print("Applied.")
    else:
        print("Nothing to restore - no rows still matched their post-backfill values.")
    db.close()


if __name__ == "__main__":
    main()
