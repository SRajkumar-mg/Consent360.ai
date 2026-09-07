# Backup Policy

**Status: policy statement + honestly-scoped code support.** This platform is not deployed to any
managed infrastructure in this environment — there is no production database, no cloud account, and no
backup service to point at. This document states the policy this platform commits to operating under
once deployed, and is explicit, section by section, about what is already true in the code today versus
what is an operational commitment that must be carried out by whoever runs the production environment.
See `RESTORE_DRILL_RECORD.md` for a real, executed backup/restore/verify cycle proving the *mechanics*
below actually work — against a throwaway database, since that is the only kind of database this
environment has.

## 1. What is backed up

The single Postgres database backing `app.main:app` (`consent_platform` in this repo's dev convention):
every application table, including the append-only, hash-chained `audit_logs` ledger
(`app/core/audit_chain.py`) and all `EncryptedString`/`EncryptedText`/`EncryptedJSON` columns — those are
backed up **as ciphertext**, exactly as stored; a database backup on its own is not a plaintext data
export (see §4).

Not covered by this policy: the FastAPI application code itself (governed by source control, not backup
policy) and any file the application writes outside Postgres (there is none in normal operation — no
uploaded files, no local file storage; see the security-scan note in `SECURITY_SCAN_FINDINGS.md` about
there being no file-upload endpoint in this codebase at all).

## 2. Backup method and cadence

- **Method:** `pg_dump` in custom format (`-Fc`), which supports selective/parallel restore via
  `pg_restore` and is what `backend/tests/restore_drill.py` uses. A managed Postgres service (e.g. RDS,
  Cloud SQL, or a managed instance in an Indian region — see §3) may additionally provide continuous
  WAL-archiving/point-in-time-recovery; where available, PITR should be enabled in addition to, not
  instead of, scheduled logical backups, since logical dumps are the portable format this policy's
  restore drill is written against.
- **Cadence (policy target, ENFORCED-IN-CODE: NO):** a full backup at least every
  `BACKUP_RPO_MINUTES` (default 60 minutes — see `app/core/config.py`) of production write activity.
  Nothing in this codebase currently schedules or triggers a backup; there is no cron job, no
  `pg_dump` invocation anywhere outside `backend/tests/restore_drill.py`'s manual drill tooling, and no
  background job in `app/jobs/` for this purpose. **This is an operational commitment for whoever
  operates production**, not a running feature of this application. A future task could add a
  scheduled `JobRunner` entry (see `docs/ARCHITECTURE.md`'s background-jobs section) that shells out to `pg_dump`
  and uploads the result, or — more realistically for most Postgres hosts — rely on the hosting
  provider's own managed backup product configured to this cadence.
- **Retention:** `BACKUP_RETENTION_DAYS` (default 35 days — `app/core/config.py`), matching common
  regulatory-evidence retention windows; align with whatever the operator's actual data-retention
  schedule and legal-hold obligations require and adjust in `.env`, not by editing the default.

## 3. Encrypted, in-India copies

**Status: documented intent + assertable config flags, NOT enforced or verified in code.** This
environment cannot provision Indian infrastructure, and no code in this repository can verify where a
backup file physically ends up once it leaves `pg_dump`'s stdout — that is entirely a function of where
the operator points the backup (which bucket, which region, which KMS key), not something an
application process can control or attest to from inside itself.

What the codebase *does* provide, for the sake of having a single, honest source of truth an SDF filing
or DPIA can cite instead of scattered infra config (see `app/core/config.py`, R3-12 section):

| Flag | Type | Default | What it means |
|---|---|---|---|
| `DATA_RESIDENCY_PRIMARY_DB_REGION` | str | `"unspecified"` | Operator-recorded region of the primary Postgres instance, e.g. `"IN-MUMBAI"`. |
| `DATA_RESIDENCY_BACKUP_REGION` | str | `"unspecified"` | Operator-recorded region backups are stored in. |
| `DATA_RESIDENCY_ASSERT_INDIA_ONLY` | bool | `False` | Operator's explicit claim that both of the above are India-resident. |
| `BACKUP_ENCRYPTION_ENABLED` | bool | `False` | Operator's explicit record of whether backups are encrypted at rest (e.g. SSE-KMS on the storage bucket, or `pg_dump \| gpg`). |
| `BACKUP_RETENTION_DAYS` | int | `35` | Policy retention window (§2). |
| `BACKUP_RPO_MINUTES` | int | `60` | Policy RPO target (§2, §5). |
| `BACKUP_RTO_MINUTES` | int | `240` | Policy RTO target (§5). |

None of these flags cause the application to *do* anything differently — they are read, honest,
settable statements. The one behavioural effect that exists is a narrow, **opt-in, no-op-by-default**
self-consistency check in `Settings.production_issues()`: if an operator sets
`DATA_RESIDENCY_ASSERT_INDIA_ONLY=true` in production without also recording an actual region in both
region fields, startup refuses (fail-closed) — catching the specific dishonesty of claiming India-only
residency while leaving the "which region" fields at their unexamined default. It does **not**, and
cannot, verify the claim is *true*; only that it was not left unexamined while being relied on. See
`DATA_RESIDENCY_SDF_READINESS.md` for the full readiness picture, including what SDF status actually
requires beyond these flags.

**Practically, achieving this policy's "encrypted in-India copies" requirement means:** provisioning the
Postgres instance itself in an Indian AWS/GCP/Azure region (or an Indian managed-Postgres provider),
pointing backup storage at a bucket in that same region with server-side encryption enabled (or
encrypting the dump client-side, e.g. `pg_dump ... | gpg --encrypt`, before it leaves the host), and then
recording that fact via the flags above so it is discoverable rather than tribal knowledge.

## 4. Encryption of backup contents

Application-level field encryption (`app/core/encryption.py`, AES-256-GCM via `FIELD_ENCRYPTION_KEY`) is
orthogonal to backup encryption and both matter: a `pg_dump` of a database with
`FIELD_ENCRYPTION_KEY` set already contains ciphertext for every sensitive column, so a backup file that
leaks *without* the key is not a plaintext PII leak — but the non-sensitive columns, the audit trail's
`event`/timestamps/`actor_type` metadata, and the schema itself are still fully readable, and if the
data-encryption key itself is ever backed up or stored alongside the dump, application-level encryption
provides no protection at all. Backup-at-rest encryption (§3's `BACKUP_ENCRYPTION_ENABLED`) is therefore
still required as a defence-in-depth control, not a redundant one.

## 5. RPO / RTO

| Metric | Policy target | Measured (this drill) |
|---|---|---|
| RPO (max acceptable data loss) | `BACKUP_RPO_MINUTES` = 60 min | N/A — no scheduled backup cadence exists yet to measure a real-world RPO against (§2); the target is a policy commitment for when one is scheduled. |
| RTO (time to restore service) | `BACKUP_RTO_MINUTES` = 240 min | **0.69 seconds**, disaster-to-restored-and-verified, on a ~150KB drill database — see `RESTORE_DRILL_RECORD.md`. This is not a production-scale estimate; it demonstrates the *mechanism* is correct and fast at this data volume. RTO at production scale depends on database size, network path to the backup store, and `pg_restore` parallelism (`-j`), none of which this drill measures. |

## 6. Restore drills

A restore drill must be run and recorded (not just documented) on a regular cadence once in production —
recommend quarterly, and after any material schema change. `backend/tests/restore_drill.py` is the
tooling: it creates a throwaway database, seeds real data (including a genuine, hash-chained audit
trail), backs it up, destroys it, restores it, and verifies both row counts and
`app.core.audit_chain.verify_chain()` integrity — exiting non-zero if either check fails, so a broken
restore is a loud failure, not a document nobody re-reads. See `RESTORE_DRILL_RECORD.md` for the actual
run performed for this task.

## 7. What this policy does not cover

- Disaster recovery for anything other than the Postgres database (application server redeployment,
  DNS, secrets-manager recovery) — out of scope for this document, which is backup/restore specifically.
- A verified claim of India-only residency or of backups actually being encrypted at rest — see §3;
  these are the operator's responsibility to make true and then record via the flags above.
