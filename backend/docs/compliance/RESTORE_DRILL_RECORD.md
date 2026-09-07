# Restore Drill Record

This is a record of a drill that was **actually executed** — not a description of a drill someone
should run someday. Tooling: `backend/tests/restore_drill.py`. See that file's docstring for the full
step-by-step mechanism; this document records one real run's evidence.

## Run details

| | |
|---|---|
| Run ID | `20260904T002656Z` |
| Executed | 2026-09-04, this task (R3-12) |
| Postgres server | 16.0, local instance on port 5433 |
| `pg_dump`/`pg_restore` | `/Library/PostgreSQL/16/bin/{pg_dump,pg_restore}`, version 16.0 (exact match to server major version) |
| Drill database | `consent_platform_restore_drill` (created fresh, schema via `alembic upgrade head`, dropped at the end — never `consent_platform` or `consent_platform_test`) |
| Seed data | 4 organizations (tenants; 3 created implicitly by `resolve_tenant_id`/the audit seed loop, 1 pre-existing platform tenant), 1 purpose + version, 1 data category, 1 processing activity, 25 customers, 25 consents (mixed GRANTED/WITHDRAWN/NOT_REQUESTED), 36 audit log rows hash-chained across 3 tenants via the real `app.services.audit.log_audit` |

## Result: **PASSED**

### Timings (measured, not estimated)

| Step | Time |
|---|---|
| Schema provisioning (`alembic upgrade head` on empty DB) | 0.592 s |
| **Backup** (`pg_dump -Fc`, 150,524 bytes) | **0.148 s** |
| Simulated disaster (`DROP DATABASE`) | instantaneous |
| **Restore** (`pg_restore --no-owner --no-privileges`) | **0.565 s** |
| **Measured RTO** (disaster timestamp → restore command completion) | **0.686 s** |

These numbers are for a ~150KB drill database and demonstrate the backup/restore *mechanism* is correct
and its overhead is negligible at this scale — they are not a production-scale RTO estimate. See
`BACKUP_POLICY.md` §5 for how this relates to the policy's 240-minute RTO target and why production RTO
depends on data volume and `pg_restore` parallelism, neither exercised here.

### Integrity verification

| | Baseline (before backup) | After restore |
|---|---|---|
| `audit_logs` row count | 36 | 36 |
| `consents` row count | 25 | 25 |
| `customers` row count | 25 | 25 |
| `organizations` row count | 4 | 4 |
| **`verify_chain()` — checked** | 36 | 36 |
| **`verify_chain()` — broken links** | 0 | 0 |
| **`verify_chain()` — tenants** | 3 | 3 |
| Full row-count diff across all 31 tables | — | **zero differences** |

The append-only audit hash chain (`app/core/audit_chain.py::verify_chain`) was independently
recomputed against the **restored** database and matched exactly — the restore did not silently drop
rows, reorder them, or corrupt the hash chain (a `pg_dump -Fc` / `pg_restore` cycle preserves the
`audit_logs` append-only trigger too, since it is a first-class schema object created by the
`c5e6f7a8b9d0_harden_audit_ledger` migration and dumped/restored like any other database object).

## Full result JSON

```json
{
  "run_id": "20260904T002656Z",
  "started_at": "2026-09-04T00:26:56.113352+00:00",
  "drill_database": "consent_platform_restore_drill",
  "restored_database": "consent_platform_restore_drill_restored",
  "migration_seconds": 0.592,
  "baseline_row_counts": {
    "alembic_version": 1, "api_keys": 0, "audit_logs": 36, "consent_contexts": 0,
    "consent_decision_logs": 0, "consent_evidence": 0, "consent_history": 0,
    "consent_receipts": 0, "consents": 25, "crm_customers": 0, "customers": 25,
    "data_categories": 1, "data_sharing_events": 0, "kpi_snapshots": 0,
    "notice_versions": 0, "notices": 0, "notification_templates": 0, "notifications": 0,
    "objections": 0, "organization_users": 0, "organizations": 4, "otp_challenges": 0,
    "policies": 0, "policy_versions": 0, "processing_activities": 1, "processors": 1,
    "purpose_versions": 1, "purposes": 1, "roles": 0, "scheduler_runs": 0,
    "transfers": 1, "users": 0
  },
  "baseline_chain_verification": { "checked": 36, "broken": [], "tenants": 3 },
  "backup_seconds": 0.148,
  "backup_file_bytes": 150524,
  "disaster_at": "2026-09-04T00:26:57.677133+00:00",
  "restore_seconds": 0.565,
  "measured_rto_seconds": 0.686,
  "restored_row_counts": {
    "alembic_version": 1, "api_keys": 0, "audit_logs": 36, "consent_contexts": 0,
    "consent_decision_logs": 0, "consent_evidence": 0, "consent_history": 0,
    "consent_receipts": 0, "consents": 25, "crm_customers": 0, "customers": 25,
    "data_categories": 1, "data_sharing_events": 0, "kpi_snapshots": 0,
    "notice_versions": 0, "notices": 0, "notification_templates": 0, "notifications": 0,
    "objections": 0, "organization_users": 0, "organizations": 4, "otp_challenges": 0,
    "policies": 0, "policy_versions": 0, "processing_activities": 1, "processors": 1,
    "purpose_versions": 1, "purposes": 1, "roles": 0, "scheduler_runs": 0,
    "transfers": 1, "users": 0
  },
  "restored_chain_verification": { "checked": 36, "broken": [], "tenants": 3 },
  "row_counts_match": true,
  "chain_intact_after_restore": true,
  "drill_passed": true,
  "finished_at": "2026-09-04T00:26:58.394096+00:00"
}
```

## How to re-run this drill

```bash
cd cms/backend
export PG_DUMP=/path/to/pg_dump      # must match the running server's major version
export PG_RESTORE=/path/to/pg_restore
python -m tests.restore_drill          # add --keep to inspect the restored DB afterwards
```

Never run against `DATABASE_URL` pointed at `consent_platform` — the script always overrides the
database name to `consent_platform_restore_drill{,_restored}` regardless of what `DATABASE_URL` points
at, but pass a `DATABASE_URL` with the right host/port/credentials if 5433/postgres/postgres is not
correct for the target environment.

## Next drill

Recommend re-running after any Alembic migration lands (to confirm the new schema round-trips through
`pg_dump`/`pg_restore` cleanly) and at least quarterly once this platform is actually deployed to a real
environment with production-scale data, at which point re-measure RTO against real data volume rather
than this drill's ~150KB dataset.
