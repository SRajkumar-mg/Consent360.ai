# Secure deletion approach (R1-06 / G-01, G-04, G-06)

**Scope.** How Consent360 destroys a data principal's personal data when
DPDP Act s.8(7), s.12(3) or DPDP Rules 2025 R.8(1) require it, what that
destruction actually guarantees, and — stated as plainly as the guarantees —
what it does not.

Mechanism: `app/services/erasure.py`. Tables: `app/models/erasure.py`. This
document is the honest account of the mechanism, not a restatement of it.

---

## 1. Two obligations that pull in opposite directions

| | Floor (minimum) | Ceiling (maximum) |
|---|---|---|
| Says | this record **may not** be destroyed before *X* | this record **must** be destroyed after *Y* |
| Source | R.8(3), R.6(1)(e) (1 year), First Schedule Part B 3–4(c) (7 years, Consent Manager), CERT-In 2022 (180 days) | s.8(7) (purpose served / consent withdrawn), s.12(3) (erasure request), R.8(1) + Third Schedule (3-year inactivity) |
| Built by | R1-10, `app/services/retention.py` | R1-06, `app/services/erasure.py` |

They can disagree, and the resolution is not a matter of taste. s.8(7) itself
carves out retention that is *"necessary for compliance with any law for the
time being in force"*, and R.8(3) is such a law. **The floor wins**, and it
wins in code:

1. **At configuration time.** `assert_policy_respects_floors` refuses a
   retention policy whose `retention_days` is below the floor in force for a
   record class R1-10 governs, and refuses `action="ERASE"` for any class
   outside `HARD_DELETABLE_RECORD_CLASSES`. `PUT /erasure/policies` returns
   422; the row is never written. An impossible policy is not storable.
2. **At execution time.** `execute_erasure_job` recomputes the floor for every
   class it touches and destroys only rows older than the floor cutoff. Rows
   inside the floor go into `ErasureJob.records_retained` with the floor in
   days and its statutory basis.

The visible consequence: **identifiers are erased, evidence is retained
pseudonymised.** An erasure job says so on its own face, per class, so the
question "you told me you erased this person — what is this row still doing
here?" is answered from the job rather than from a policy document.

---

## 2. What is destroyed, and how

| Record class | Table | Action | Why that action |
|---|---|---|---|
| `principal_personal_data` | `customers` | **ANONYMISE** | The row is the parent of consents, consent history, evidence, receipts and audit rows, all inside their own floors. Deleting it would orphan records that may not yet be destroyed. Name, email, phone and external id are overwritten; `status` becomes `ANONYMISED` and a fresh `anonymised_ref` replaces the external id. Contexts are deactivated. |
| `directory_record` | `crm_customers` | **ERASE** | An operational directory row (name, email, phone, address, age, stored cookie preferences). Not evidence of a consent, not referenced by any consent record, no floor. This is the class that makes ERASE a real action rather than a synonym for anonymisation. |
| `consent_contexts` | `consent_contexts` | **ERASE** | A spent 15-minute credential. Its use is separately evidenced by `CONTEXT_CREATED` / `CONTEXT_CONSUMED` rows in the append-only ledger, which are retained, so deleting the credential destroys no evidence. |
| `notifications` | `notifications` | **RETAINED** in practice | A retention class in its own right with a 1-year floor: proof that a required communication was made — including the R.8(2) pre-erasure notice that evidences this very erasure. Reported as retained, with the floor, on every job. |

The anonymisation itself is `routes/crm.py::_purge_customer_data`, called
rather than reimplemented. There is exactly one definition of "this
principal's identifiers are gone" in this platform on purpose: two would
drift, and the day they drift is the day one of them leaves an identifier on a
record someone was told had been erased.

---

## 3. The audit ledger is not rewritten, and cannot be

`audit_logs` is append-only by construction: a Postgres trigger blocks UPDATE
and DELETE **for every role**, and the rows are hash-chained per tenant
(`app/core/audit_chain.py`). "Audit anonymisation" therefore cannot mean
mutating audit rows here, and this engine does not attempt it — an attempt
would raise, and a chain that could be rewritten would be worth nothing as
evidence.

What the engine does instead is record precisely what survives, so the residue
is **visible rather than implied**. Every executed job carries an
`audit_logs` entry in `records_retained` with the row count, the floor and
this explanation.

**The residual identifier, stated plainly.** `audit_logs.customer_external_id`
on a principal's historical rows still holds the external id they had *before*
erasure. It cannot be overwritten (the trigger) and it is not a free-text
field this engine can avoid having written. Three things bound it:

- It is an application-assigned external id, not a name, email or phone —
  those live in `audit_logs.reason`/`ip_address`, which are AES-256-GCM
  encrypted columns.
- After erasure nothing maps that id back to a person: the `customers` row no
  longer carries it, `external_id_search` is re-derived from the anonymisation
  reference, and the CRM directory row is gone.
- The rows themselves fall under the 1-year floor and are retained lawfully
  under s.8(7)'s compliance carve-out.

This is a known, bounded residue rather than a solved problem. Removing it
would require either breaking the append-only guarantee (a worse trade) or
tokenising `customer_external_id` at write time behind a per-principal key
that erasure destroys — the crypto-shredding design in §5, which is not built.

---

## 4. What a destruction physically guarantees

Honesty about the storage layer, because "deleted" means different things at
different depths:

| Layer | After ANONYMISE / ERASE | Bounded by |
|---|---|---|
| Live row | Overwritten / removed, committed | Immediate |
| Dead tuples (MVCC) | Old row version still on disk until `VACUUM` reclaims it | Postgres autovacuum; unreadable through SQL |
| WAL | The old value persists in write-ahead log segments until they are recycled or archived out | `wal_keep_size` / archive retention |
| Replicas | Same as primary once the change replicates | Replication lag |
| Backups | **Unchanged.** A backup taken before the erasure still contains the data | Backup expiry — see `docs/compliance/BACKUP_POLICY.md` |

The application cannot assert any of the lower four rows away, so it does not
try. The controlling protection below the live row is that
`customers.name/email/phone/external_id`, all of `crm_customers`,
`audit_logs.reason` and `consent_decision_logs.details` are stored under
AES-256-GCM with a single deployment-wide `FIELD_ENCRYPTION_KEY`
(`app/core/encryption.py`): a restored backup, a leaked WAL segment or a raw
page dump is ciphertext without that key.

**Operator obligations** this design depends on, none of which the code can
enforce:

1. Backup retention is bounded and documented, and the bound is shorter than
   any period in which an erased principal could plausibly be re-materialised
   from a restore.
2. `FIELD_ENCRYPTION_KEY` is held outside the database and outside the backup
   set. A backup that carries its own key is plaintext.
3. WAL archives are subject to the same expiry as backups.
4. A restore that predates an executed erasure is followed by replaying the
   `erasure_jobs` register against the restored database — every executed job
   carries the `trigger_ref`, the action and the anonymisation reference
   needed to redo it, which is one of the reasons the register is retained.

---

## 5. Residual gap: per-principal crypto-shredding (G-06, NOT BUILT)

The complete answer to backups is *cryptographic erasure*: a distinct data
encryption key per data principal, destroyed at erasure, so every copy of
their ciphertext everywhere — live rows, WAL, replicas, every backup ever
taken — becomes permanently unreadable in one act, with no dependency on
retention schedules or on a restore procedure being followed correctly.

This platform does **not** implement it. `FIELD_ENCRYPTION_KEY` is one
deployment-wide key; the `EncryptedString`/`EncryptedText`/`EncryptedJSON`
decorators have no per-row key selection, and adding one would mean a key
store, a key-per-principal lifecycle, re-encryption on rotation, and a
decision about what happens to a *shared* row (an audit entry naming two
principals) whose key half-exists.

Consequence, stated without hedging: **after an erasure, a restore from a
backup taken before it will contain that principal's data again.** The
mitigations are backup expiry and the operator obligations in §4, and they are
procedural, not cryptographic. This is recorded as gap G-06 in
`DPDP_COMPLIANCE_GAP_ANALYSIS.md` and is a prerequisite for any claim that
erasure here is irreversible at the storage layer rather than at the
application layer.

---

## 6. Procedural guarantees around every erasure

These are enforced, not advisory:

1. **An `erasure_jobs` row exists before anything is destroyed.** It is the
   authorisation and the evidence, not a report written afterwards.
2. **A named actor authorised it.** Either a staff user
   (`POST /erasure/jobs/{ref}/authorise`, gated on `erasure.manage`, granted
   only to `admin` and `dpo`) or the principal's own act — a withdrawal, or an
   approved s.12(3) request. A database CHECK
   (`ck_erasure_jobs_executed_is_authorised`) makes an executed job without an
   authoriser unrepresentable.
3. **The R.8(2) notice went out first.** `notice_sent_at` is written only from
   ids `queue_notification` actually returned; `execute_after` is
   `notice_sent_at + pre_erasure_notice_hours` (CHECK: `>= 48`); execution
   before that instant raises. A notice that could not be queued on any
   channel leaves the erasure blocked. **No notice, no erasure.**
4. **Legal holds block, they do not delay.** An active hold moves the job to
   `BLOCKED` naming the hold; releasing the hold lets the next executor pass
   proceed.
5. **A clock never erases on its own.** `erasure_retention_scan` and
   `inactivity_scan` create `PROPOSED` jobs with no authorisation, and
   `erasure_executor` will not touch a `PROPOSED` job however old it is. A
   scan noticing that a period elapsed is an inference about someone else's
   data, not a decision anybody made.
6. **Processors are instructed.** s.8(7)(b): `raise_erasure_instructions`
   raises one signed `ERASURE_INSTRUCTION` per processor recorded as holding
   the principal's data, tracked to acknowledgement with SLA escalation
   (R3-07). An empty list means "no processor on file to instruct", never
   "erasure complete".
7. **The outcome is hashed.** `evidence_hash` is SHA-256 over the canonical
   JSON of what the job did, written both onto the job and into its
   `ERASURE_EXECUTED` audit row — and *that* ledger is immutable and
   hash-chained, so a later edit to the job row is detectable by recomputing.
