"""One-off backfill: correct Consent/ConsentHistory/ConsentEvidence rows that
were mis-stamped source_app="CRM_PORTAL" by a bug in
app/api/routes/crm.py::_sync_consent_preferences, which hardcoded
CRM_SOURCE_APP for every cookie-banner grant/withdraw regardless of which
demo site (CRM, Codex, SkillLearn) actually collected it - see the rework
report for the live counts this found in the dev database (81 CODEX-owned
and 81 SKILLLEARN-owned consents mis-stamped CRM_PORTAL).

Scope of the fix: Consent, ConsentHistory and ConsentEvidence rows written
by that path (``actor_username`` / ``collected_by`` == "crm") for a customer
whose OWN ``Customer.source_app`` is something other than CRM_PORTAL - i.e.
the customer authenticated through Codex or SkillLearn's ``/crm/login``,
which DOES send the real source_app and Consent360 recorded it correctly on
the Customer row (see crm_directory.py::_ensure_consent360_customer) - only
the consent rows collected afterwards through the shared cookie-banner sync
ignored it.

``AuditLog`` rows are deliberately left untouched: `source_app` there is
excluded from the tamper-evident hash chain (see
app/core/audit_chain.py::_canonical, which does not include it), so editing
it would be *safe* in the narrow sense of not breaking the chain - but
audit_logs is meant to be an immutable record of what the system actually
logged at the time, bug included. Rewriting history there would defeat the
point of an audit ledger. Instead this script appends ONE new, clearly
labelled audit event per affected tenant (event
"CONSENT_SOURCE_APP_CORRECTED") summarising the correction, which is how a
correction is supposed to show up in the ledger.

Reversible: before changing anything, every touched row's prior values are
written to a timestamped JSON snapshot file (path printed at the end).

Idempotent: the query that finds affected rows requires
``Consent.source_app == 'CRM_PORTAL'``, which no longer holds once a row has
been corrected, so re-running after ``--apply`` finds nothing left to do.

Usage:
    cd backend
    python -m scripts.backfill_source_app_misattribution           # dry run
    python -m scripts.backfill_source_app_misattribution --apply   # writes the changes
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.models.entities import Consent, ConsentEvidence, ConsentHistory, Customer
from app.services.audit import log_audit
from app.services.tenancy import resolve_tenant_id

MIS_STAMPED_SOURCE_APP = "CRM_PORTAL"
CULPRIT_ACTOR = "crm"


def main() -> None:
    apply_changes = "--apply" in sys.argv
    db = SessionLocal()

    affected = (
        db.query(Consent, Customer.source_app)
        .join(Customer, Customer.id == Consent.customer_id)
        .filter(
            Consent.source_app == MIS_STAMPED_SOURCE_APP,
            Consent.actor_username == CULPRIT_ACTOR,
            Customer.source_app.notin_(["", MIS_STAMPED_SOURCE_APP]),
        )
        .all()
    )

    if not affected:
        print("Nothing to backfill - no mis-stamped consent rows found.")
        db.close()
        return

    consent_real_source_app = {consent.id: real_source_app for consent, real_source_app in affected}
    consent_ids = list(consent_real_source_app.keys())

    history_rows = (
        db.query(ConsentHistory)
        .filter(
            ConsentHistory.consent_id.in_(consent_ids),
            ConsentHistory.source_app == MIS_STAMPED_SOURCE_APP,
            ConsentHistory.actor_username == CULPRIT_ACTOR,
        )
        .all()
    )
    evidence_rows = (
        db.query(ConsentEvidence)
        .filter(
            ConsentEvidence.consent_id.in_(consent_ids),
            ConsentEvidence.source_app == MIS_STAMPED_SOURCE_APP,
            ConsentEvidence.collected_by == CULPRIT_ACTOR,
        )
        .all()
    )

    by_tenant: dict[str, list[int]] = {}
    for consent_id, real_source_app in consent_real_source_app.items():
        by_tenant.setdefault(real_source_app, []).append(consent_id)

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mis_stamped_source_app": MIS_STAMPED_SOURCE_APP,
        "consents": [
            {"id": c.id, "old_source_app": c.source_app, "old_tenant_id": c.tenant_id,
             "new_source_app": consent_real_source_app[c.id]}
            for c, _real in affected
        ],
        "consent_history": [
            {"id": h.id, "old_source_app": h.source_app, "new_source_app": consent_real_source_app[h.consent_id]}
            for h in history_rows
        ],
        "consent_evidence": [
            {"id": e.id, "old_source_app": e.source_app, "old_tenant_id": e.tenant_id,
             "new_source_app": consent_real_source_app[e.consent_id]}
            for e in evidence_rows
        ],
    }

    print(f"Found {len(affected)} mis-stamped Consent rows, {len(history_rows)} ConsentHistory rows, "
          f"{len(evidence_rows)} ConsentEvidence rows.")
    for tenant, ids in by_tenant.items():
        print(f"  -> {tenant}: {len(ids)} consents")

    snapshot_path = Path(__file__).resolve().parent / (
        f"backfill_source_app_snapshot_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    )
    snapshot_path.write_text(json.dumps(snapshot, indent=2))
    print(f"Snapshot of prior values written to {snapshot_path}")

    if not apply_changes:
        print("Dry run only - re-run with --apply to write these changes.")
        db.close()
        return

    for consent, real_source_app in affected:
        consent.source_app = real_source_app
        consent.tenant_id = resolve_tenant_id(db, real_source_app)
    for h in history_rows:
        h.source_app = consent_real_source_app[h.consent_id]
    for e in evidence_rows:
        real_source_app = consent_real_source_app[e.consent_id]
        e.source_app = real_source_app
        e.tenant_id = resolve_tenant_id(db, real_source_app)

    for tenant, ids in by_tenant.items():
        log_audit(
            db, "CONSENT_SOURCE_APP_CORRECTED", actor_username="backfill_source_app_misattribution",
            actor_type="SYSTEM", source_app=tenant,
            reason=(
                f"Corrected {len(ids)} consent rows mis-stamped source_app='{MIS_STAMPED_SOURCE_APP}' by a bug "
                f"in crm.py's cookie-banner sync (_sync_consent_preferences hardcoded CRM_SOURCE_APP); the "
                f"affected customers actually authenticated through {tenant}."
            ),
            metadata={
                "consent_ids": ids, "old_source_app": MIS_STAMPED_SOURCE_APP,
                "snapshot_file": str(snapshot_path),
            },
            commit=False,
        )
    db.commit()
    print(f"Applied. {len(affected)} consents, {len(history_rows)} history rows, "
          f"{len(evidence_rows)} evidence rows corrected.")
    db.close()


if __name__ == "__main__":
    main()
