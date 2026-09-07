# Record and Log Retention Schedule (R1-10)

**Status: enforced in code for every class stored in this database; documented-only for the two log
classes that are not.** Each row below says which it is, and the API says the same thing at runtime
(`GET /retention/schedule`, field `enforcement`).

This document does **not** restate the backup policy. Backups, their encryption, their retention window
and the India-residency flags are governed by [`BACKUP_POLICY.md`](BACKUP_POLICY.md) and
[`DATA_RESIDENCY_SDF_READINESS.md`](DATA_RESIDENCY_SDF_READINESS.md), written for R3-12; §5 below only
records how this schedule interacts with them.

## 1. Floors versus periods

A retention **floor** is a statutory minimum: the record may not be destroyed before it elapses. It is
the opposite of the retention **ceiling** in s.8(7) ("erase as soon as the purpose is served"), and both
apply at once — the evidence that a consent was validly obtained has to outlive the processing it
authorised, or an audit two years later cannot be answered.

The floors live in code, in `app/services/retention.py::RETENTION_CLASSES`. They are deliberately not
rows an operator can edit: **a floor someone can lower from an admin screen is not a floor.** What an
operator *can* set, per class, is the retention **period** actually applied — held in the
`retention_schedules` table and settable through `PUT /retention/schedule/{record_class}`, which refuses
any value below the floor in force. Raising it is always allowed.

## 2. The schedule

| Record class | Table(s) | Floor | Basis | Enforcement |
|---|---|---|---|---|
| `consents` | `consents` | 1 year (raised to 7 by the overlay) | DPDP Act s.6/s.8(7): evidence of a valid consent | Scanned; **never deleted** (parent of history/evidence/receipts, which have their own floors) |
| `consent_history` | `consent_history` | 1 year (7 via overlay) | s.6(6)/s.8(7): proof of when each transition occurred | Scanned; deletable past the floor |
| `consent_evidence` | `consent_evidence` | 1 year (7 via overlay) | s.6(1): proof the consent was free, specific, informed, unambiguous | Scanned; **never deleted** (referenced by issued receipts) |
| `consent_receipts` | `consent_receipts` | 1 year (7 via overlay) | ISO/IEC TS 27560 receipt issued to the principal | Scanned; deletable past the floor |
| `audit_logs` | `audit_logs` | 1 year | s.8(4)/(5): accountability trail | Scanned; **never deleted at any age** — append-only trigger + hash chain |
| `consent_decision_logs` | `consent_decision_logs` | 1 year | Evidence processing was gated on a live consent decision | Scanned; deletable past the floor |
| `data_sharing_events` | `data_sharing_events` | 1 year | s.8(2)/s.11: record of who data was shared with | Scanned; deletable past the floor |
| `processor_alerts` | `processor_alerts` | 1 year | s.6(6): proof processors were caused to cease | Scanned; deletable past the floor |
| `notifications` | `notifications` | 1 year | s.5/s.8(6): proof a required communication was sent | Scanned; deletable past the floor |
| `consent_artefacts` | `consent_artefacts` | **7 years** | DPDP Rules 2025 First Schedule Part B 3: a Consent Manager's record of the consents it enabled | Scanned; **never deleted** (parent of its own signed event chain) |
| `consent_artefact_events` | `consent_artefact_events` | **7 years** | First Schedule Part B 4(c): record of every consent given, denied or withdrawn through the Consent Manager | Scanned; **never deleted** (immutable, HMAC-signed chain links) |
| `consent_manager_records` | *(overlay)* | **7 years** | DPDP Rules 2025, Consent Manager obligations | Raises the floor of the four consent record classes above |
| `access_logs` | *(none — stdout / rotating file)* | 1 year | CERT-In Directions 2022 (**180-day minimum**); 1 year applied, being the stricter | **NOT enforceable from this database** — see §4 |
| `application_logs` | *(none — stdout / rotating file)* | 1 year | CERT-In Directions 2022 (180-day minimum) | **NOT enforceable from this database** — see §4 |

The two `consent_artefact*` classes carry their 7-year floor **unconditionally**, not through the
overlay below: a consent artefact only exists at all because a Consent Manager is involved, so First
Schedule Part B applies to it by definition and the floor survives the overlay being switched off.
`app/models/artefacts.py` declares the requirement as `RETENTION_FLOOR_CLASSES` (that module owns the
tables; this schedule owns the mechanism) and a test reconciles the two so they cannot drift.

### The 7-year overlay

`consent_manager_records` has no table of its own. It is an overlay: when active, it raises the floor of
`consents`, `consent_history`, `consent_evidence` and `consent_receipts` to 7 years. It ships **active**,
which is the fail-safe direction — it can only ever cause longer retention. A deployment that is
definitively not operating as a registered Consent Manager may deactivate it (`retention_schedules`,
`is_active`), which drops the four covered classes back to their own 1-year floors.

## 3. How "zero records deleted before their floor" is actually established

Three mechanisms, in descending order of strength.

1. **`audit_logs` cannot be deleted at all.** A database trigger blocks `UPDATE` and `DELETE` for every
   role, and the rows are hash-chained per tenant (`app/core/audit_chain.py`). Tampering is not merely
   recorded, it is refused, and any tampering that reached the storage layer directly would break the
   chain and be reported by `verify_chain`.
2. **`enforce_retention` is the only sanctioned deletion path** (`POST /retention/enforce`, gated on
   `policy.manage`). It refuses a cutoff inside the floor in force, refuses a wet run against a class
   marked non-deletable, defaults to `dry_run=true`, and writes a `retention_actions` ledger row for
   every run — dry or wet — recording the cutoff, the floor it was checked against, and the row count.
3. **`retention_scan` re-checks the ledger** (`GET /retention/scan`, and daily via the `retention_scan`
   scheduled job). For every recorded action it recomputes whether the cutoff fell inside the floor that
   was in force at execution, and reports `deleted_before_floor` plus a named violation per offending
   action. It also flags a schedule that has fallen below its floor, and a floor that was *raised* after
   a deletion that was compliant at the time.

**What this does not do.** It cannot detect a deletion made outside `enforce_retention` — a DBA with a
psql prompt, an ORM cascade, a dropped table. For classes other than `audit_logs` the scan reports
`id_sequence_gaps` (ids absent from the table's monotonic sequence) as a *signal to investigate*,
explicitly not as evidence: rolled-back inserts burn sequence values too, so a non-zero gap count means
"worth a look", never "rows were deleted".

## 4. Application and access logs

Access and application logs are emitted to stdout or a rotating file by `app/core/access_log.py` and
`app/core/utils.py`'s JSON formatter; they are not rows in this database. Their floor is therefore real
as a policy commitment but **not verifiable by the retention scan**, and the scan says so rather than
quietly reporting zero rows. Their enforcement point is `LOG_RETENTION_DAYS` (`app/core/config.py`,
default 366 — chosen at ~1 year, above CERT-In's 180-day rolling minimum) plus whatever log shipping,
rotation and object-lifecycle configuration the operator runs. Making the 1-year floor true in production
means setting the retention on the log sink to match, in India, and keeping the two numbers in agreement.

## 5. Backups

Backups are covered by [`BACKUP_POLICY.md`](BACKUP_POLICY.md), not restated here. Two interactions
matter for this schedule:

- **A backup is not a substitute for retention.** `BACKUP_RETENTION_DAYS` defaults to 35 days, far below
  every floor above. Backups exist for recovery, not for satisfying a retention floor; the floor is met
  by the live records and the ledger, not by the existence of a dump.
- **Backups are not a licence to delete early.** A record still inside its floor may not be deleted from
  the live database on the grounds that a backup copy exists — the backup is not queryable evidence, and
  it will itself expire in 35 days.
- **Residency.** The India-residency posture that applies to the primary database and its backups is the
  one recorded in `DATA_RESIDENCY_PRIMARY_DB_REGION` / `DATA_RESIDENCY_BACKUP_REGION` /
  `DATA_RESIDENCY_ASSERT_INDIA_ONLY`. Those flags are declarative operator statements, not enforcement —
  see `BACKUP_POLICY.md` §3, which is explicit about what code can and cannot attest to.

## 6. Regulator evidence pack

`GET /retention/evidence-pack?period_start=…&period_end=…` (gated on `audit.export`) produces the pack a
s.28 request would ask for: the audit ledger extract with its hashes, the notice/purpose/policy versions
in force during the period, the data-principal request log, the breach register, the retention actions
taken and the current scan, and integrity proofs.

Two properties of the pack are load-bearing:

- **Sections degrade honestly, in both directions.** A capability this build does not have is emitted as
  `"status": "UNAVAILABLE"` with `"records": null` and a stated reason, and is listed in the pack's
  top-level `unavailable_sections`. It is never an empty list, because an empty `records: []` handed to a
  regulator reads as a positive finding of nil — a claim only a register the platform actually keeps can
  support. The **breach register** section is read live from R3-08's own `app/services/breach.py`, so an
  empty list there *is* a real nil return; the UNAVAILABLE path remains only for the case where that
  register cannot be reached at all, so a broken import can never masquerade as a clean period. The
  request log is `PARTIAL` and carries its own `coverage` statement, since this build has no dedicated
  rights-request table.
- **Integrity proofs reuse the existing chain**, not a second scheme. `verify_chain` is the mechanism;
  the pack adds only its own manifest — a SHA-256 over the canonical JSON of every other section, signed
  with the platform HMAC key — so the pack is tamper-evident in transit and the recipient can recompute it.
