# Data Protection Impact Assessment (DPIA) — Template and Consent360 Worked Example

A DPIA is required under the DPDP Rules for a Significant Data Fiduciary (SDF) before undertaking
processing likely to pose a significant risk to data principals, and is good practice generally before
any material change to processing. This document is both (a) a reusable template section structure and
(b) a worked-through first pass for Consent360 itself, so it is not a blank form nobody has tried to
fill in. Update the worked example whenever the described processing changes materially.

**Status: prepared ahead of being notified as an SDF, not a regulator-submitted filing.** See
`DATA_RESIDENCY_SDF_READINESS.md` for what SDF status actually requires and how the `SDF_NOTIFIED` flag
relates to this document's currency requirements.

---

## 1. Identification

| | |
|---|---|
| System name | Consent360 |
| DPIA version | 1.0 (first pass, R3-12) |
| Date | 2026-09-04 |
| Prepared by | Engineering (R3-12 task), pending DPO review and sign-off |
| Processing activities covered | Consent capture, storage, decisioning (`decision_engine.py`), and the AI assistant (`chatbot.py`) — see `ALGORITHM_REGISTER.md` for both in detail |
| Related documents | `ALGORITHM_REGISTER.md`, `BACKUP_POLICY.md`, `AUDIT_RECORD_TEMPLATE.md`, `DATA_RESIDENCY_SDF_READINESS.md`, repository-root `DPDP_COMPLIANCE_GAP_ANALYSIS.md` |

## 2. Description of processing

**What is processed:** customer identity fields (name, email, phone — `EncryptedString` columns),
consent records per (customer × purpose × data category × processing activity × source app), audit
events, and (for the CRM demo client) age and cookie-preference categories. See
`app/models/entities.py` for the authoritative field list (owned by another lane — do not treat this
DPIA as a substitute for reading the current schema).

**Why:** to record, evidence, and enforce data-principal consent for the purposes each connected
application (demo client) defines, and to give data principals self-service grant/withdraw/rights
controls (`/portal/*`, the CRM cookie banner).

**Who is affected:** customers/data principals of each connected demo application (CRM portal, Codex,
SkillLearn, JobHub) and, indirectly, staff users whose actions the audit trail records.

**Categories of personal data:** identity (name, email, phone), online identifiers (`source_app`,
`external_id`), consent/preference history, and — only in the free-text channel to the chatbot, and only
best-effort redacted before it leaves the platform — whatever a staff user might paste into a chat
message (see `ALGORITHM_REGISTER.md` §2's redaction section for exactly what is and is not caught).

**Special category / high-risk data:** none by design — Aadhaar collection was deliberately removed
(`265ec61f7106_drop_aadhar_number_from_crm_customers` migration) as unnecessary data minimisation risk
with no established lawful basis; no health, biometric, or genetic data field exists anywhere in the
schema.

## 3. Lawful basis

Recorded per-purpose, not platform-wide: `Purpose.legal_basis` (DPDP s.7 gateway) and
`Purpose.requires_consent`. See `tests/test_lawful_basis.py` for the enumerated s.7 clauses this
platform supports and enforces at the database-constraint level (`test_database_check_constraint_...`).
This DPIA does not re-derive the lawful basis for each purpose — that register is `Purpose`/
`PurposeVersion` itself, kept current by whoever manages purposes (`PERM_PURPOSE_MANAGE`).

## 4. Necessity and proportionality

- Consent is captured per purpose/category/activity, not blanket — a customer's grant for "marketing
  emails" does not imply anything about "analytics cookies" (`Consent` is scoped per tuple, see
  `docs/ARCHITECTURE.md`'s consent-lifecycle section).
- Retention is purpose-scoped (`Purpose.retention_period_days`), not indefinite.
- Aadhaar was removed as disproportionate to any established purpose (§2).
- The chatbot (§ below) is restricted to aggregate queries and refuses individual-customer lookups by
  design (system prompt) with a code-level redaction backstop — see `ALGORITHM_REGISTER.md`.

## 5. Risks identified and mitigations

| Risk | Likelihood | Impact | Mitigation | Residual risk |
|---|---|---|---|---|
| Cross-tenant data leakage (one `source_app`'s customer visible to another) | Was High (real, since-fixed exploit — see `tests/test_cross_tenant_context.py`) | High | `resolve_customer` choke point (`app/services/tenancy.py`), enforced and regression-tested (`tests/test_customer_resolution_guard.py` statically scans for any lookup bypassing it) | Low, contingent on that guard test staying green — see the R3-12 handoff note that this guard is *currently failing* due to an unrelated, concurrent in-progress change (`app/api/routes/notifications.py`) at the time of this DPIA's writing; must be re-verified green before this DPIA can be considered current. |
| Field-level plaintext exposure via a raw DB/backup leak | Medium | High | AES-256-GCM field encryption (`app/core/encryption.py`), fails closed in production if unset (`Settings.production_issues()`) | Low, contingent on `FIELD_ENCRYPTION_KEY` being managed via a real secret store in production (§ `_SECRET_FIELDS`/`*_FILE` convention exists; whether it's actually used is an operational fact this DPIA cannot verify) |
| Audit trail tampering (covering up an unauthorized action) | Low | High (defeats every other control's evidentiary value) | Append-only, hash-chained ledger with a DB trigger blocking UPDATE/DELETE for every role (`app/core/audit_chain.py`); verified in this task's restore drill to survive a full backup/restore cycle intact (`RESTORE_DRILL_RECORD.md`) | Low |
| PII sent to a third-party LLM processor (chatbot) | Medium (staff behaviour-dependent) | Medium | Processor/contract/restricted-country gate + best-effort redaction, both described in `ALGORITHM_REGISTER.md` §2 | **Medium — not fully mitigated.** Redaction is explicitly best-effort; an identifier split across turns, an obfuscated form, or a name in prose is not caught. Recommend staff training as a compensating control alongside the technical one. |
| Predictable OTP (identity verification) | Low–Medium | Medium (identity-verification bypass) | Attempt cap + rate limiting (`app/services/otp.py`) | **Not fully mitigated** — `_generate_code()` uses Python's `random` (not a CSPRNG) for the 6-digit code; see `SECURITY_SCAN_FINDINGS.md` for the specific finding and recommended fix (`secrets.randbelow`), reported to the owning lane, not yet fixed at the time of this DPIA. |
| Backup/restore failure or data-residency drift | Low (drill passed once) | High | See `BACKUP_POLICY.md`, `RESTORE_DRILL_RECORD.md` | Medium — no scheduled backup cadence exists yet in any real deployment (this is infra, not app code); residency claims are declarative flags only, not verified against real infrastructure. |
| Algorithmic decisioning without record (decision engine) | Low | Medium | Every decision is persisted (`ConsentDecisionLog`) and audited (`DECISION_EVALUATED`) — see `ALGORITHM_REGISTER.md` §1 | Low |

## 6. Consultation

- Data Protection Officer: sign-off pending (this is a first engineering pass, not a DPO-approved DPIA).
- Data principals: not directly consulted for this pass; the platform's own notice/policy publication
  flow (`Notice`/`NoticeVersion`, `routes/notices.py`) is the channel through which principals are
  informed of processing before consenting.

## 7. Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| DPO | _pending_ | | |
| Engineering lead | _pending_ | | |

## 8. Review triggers

Re-run/update this DPIA when: a new purpose or data category is added, the chatbot's system prompt or
redaction patterns change, a new third-party processor is added, the `resolve_customer` tenant-scoping
guard test starts failing again, or annually regardless of change.
