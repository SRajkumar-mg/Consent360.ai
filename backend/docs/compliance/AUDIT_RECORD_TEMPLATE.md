# Audit Record Template

A standard structure for a DPO, auditor, or regulator-facing summary of the platform's audit trail —
for when someone needs to hand over "what happened" evidence rather than raw table rows. This is a
reporting template over the existing `audit_logs` ledger (`app/core/audit_chain.py`,
`app/services/audit.py`); it does not change how audit rows are written.

## 1. What the underlying record already guarantees (read before filling this in)

- **Append-only, hash-chained, per tenant.** Every row's `entry_hash = sha256(prev_hash +
  canonical(row))`; a database trigger blocks UPDATE and DELETE on `audit_logs` for every role,
  including the application's own. `app.core.audit_chain.verify_chain(db)` recomputes the chain and
  reports the first broken link per tenant, if any.
- **Restore-durable.** This task's restore drill (`RESTORE_DRILL_RECORD.md`) proved a full
  backup/restore cycle preserves both every row and the chain's integrity.
- **Attributable.** Every row carries `actor_username`, `actor_type` (`SYSTEM`/`USER`/opaque principal
  actor for self-service actions — see `tests/test_audit_chain.py::test_portal_grant_audit_row_has_opaque_principal_actor`),
  `source_app`, and (since R3-04) IP/user-agent where applicable.
- **Exportable with chain proof.** `GET /audit/export` (`PERM_AUDIT_EXPORT`) includes the chain fields
  and re-verifies on export (`tests/test_audit_chain.py::test_audit_export_includes_chain_fields_and_reverifies`).

When producing a record using this template, **use the export endpoint's output as the source of
truth**, not a hand-copied query result — the export's own re-verification is what makes the record
defensible.

## 2. Record header (fill in per report)

| Field | Value |
|---|---|
| Report period | `<start>` to `<end>` (UTC) |
| Tenant(s) covered | `<organization code(s)>` — audit_logs is chained **per tenant**; a report spanning multiple tenants must state each one's chain-verification result separately, not a single pooled figure |
| Requested by | `<name/role — e.g. DPO, external auditor, regulator request reference>` |
| Prepared by | `<staff username, role>` |
| Export basis | `GET /audit/export?...` params used, or the specific query if exported differently |
| Chain verification result | `checked=<n> broken=<list, expect empty> tenants=<n>` (from `verify_chain()` / the export's own re-verification) |

## 3. Event summary (fill in per report)

| Event | Count | Notes |
|---|---|---|
| `LOGIN` / `LOGIN_FAILED` | | Cross-reference `ALERT_FAILED_LOGIN_THRESHOLD` alerts fired in this window if investigating an incident |
| `CONSENT_GRANTED` / `CONSENT_WITHDRAWN` / `CONSENT_DENIED` | | |
| `ROLE_CHANGED` | | Every row includes the affected user/role in `reason` (encrypted column — filter by `event`, not `LIKE`, per `docs/ARCHITECTURE.md`) |
| `DECISION_EVALUATED` | | One row per `decision_engine.py` call — see `ALGORITHM_REGISTER.md` §1 |
| `CHATBOT_BLOCKED` | | Internal block reason only, never shown to the requesting user — see `ALGORITHM_REGISTER.md` §2 |
| `PURGE_*` | | Anonymisation events; the audit row itself is retained even though the customer record is anonymised (`tests/test_purge.py`) |
| *(add rows for any other `AUDIT_EVENTS` relevant to the request)* | | |

## 4. Anomalies / follow-up items

Document anything that does not fit the "expected" shape here: an unexplained gap in sequence, a broken
chain link (should never happen — treat as a P0 incident if it does), an unusually high
`BULK_EXPORT`/off-hours-admin alert count, or a request the export couldn't satisfy (e.g. a tenant with
no rows in the window).

## 5. Sign-off

| Role | Name | Date |
|---|---|---|
| Prepared by | | |
| Reviewed by (DPO or delegate) | | |

## 6. Retention of this record

Store alongside (not instead of) the underlying `audit_logs` export — this document is a *summary*
derived from the ledger, not a replacement source of truth. Retain per the same policy as
`LOG_RETENTION_DAYS` (`app/core/config.py`, default ~1 year) unless a specific regulatory or grievance
matter requires longer.
