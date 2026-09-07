# Data Residency and SDF Readiness

## What "Significant Data Fiduciary" readiness actually requires

SDF status under the DPDP Act is a **government notification** (s.10), not a self-declaration this
codebase can flip a flag to claim. What a codebase *can* do ahead of being notified is make sure the
artifacts an SDF is expected to maintain — a DPIA, an algorithm/automated-decision register, a data
audit trail, and (per the Rules) a data-protection-officer point of contact — already exist and are kept
current, so notification doesn't trigger a scramble. That is the scope of this document and its
siblings:

| Requirement | Where it lives | Status |
|---|---|---|
| DPIA | `DPIA_TEMPLATE.md` | First engineering pass done; DPO sign-off pending |
| Algorithm register | `ALGORITHM_REGISTER.md` | Done — describes the two real algorithmic systems (`decision_engine.py`, `chatbot.py`) |
| Audit trail | `app/core/audit_chain.py` + `AUDIT_RECORD_TEMPLATE.md` | Already implemented and restore-drill-verified (`RESTORE_DRILL_RECORD.md`) |
| DPO contact | `Organization.dpo_name/dpo_email/dpo_phone` (per-tenant, already in the schema) | Implemented (owned by other lanes' earlier work, not this task) |
| Data-audit trigger (Rules' periodic audit requirement) | Not built | Out of scope for R3-12 — flagging for the DPDP gap register |

## Data residency: what is enforced vs. documented intent

**Nothing in this codebase pins any infrastructure to a physical region.** The database is wherever the
operator points `DATABASE_URL`; backups (once someone schedules them — see `BACKUP_POLICY.md`, no code
does this yet) go wherever the operator points them; the one third-party processor in this codebase, the
chatbot's LLM provider, is wherever Groq's API endpoint physically runs, which this codebase does not
and cannot control beyond the existing restricted-country/contract gate in `_llm_processor_block_reason`
(`ALGORITHM_REGISTER.md` §2).

What *is* implemented:

- `RESTRICTED_COUNTRIES` / `Settings.is_restricted_destination()` — already existed before this task —
  blocks the chatbot from calling out to a processor whose registered `Transfer.destination_country` is
  on the restricted list, re-checked live on every call.
- The `DATA_RESIDENCY_*`/`BACKUP_*` declarative flags described below (see "Settings specification").

## Settings specification (for the config.py owner to apply/reconcile)

R3-12 originally added these to `app/core/config.py`; ownership of that file was reassigned mid-task to
a concurrent security-fix lane (see the R3-12 handoff report for the exact circumstance). **As of this
writing the fields below are still present in `app/core/config.py` exactly as specified** — verify they
still exist under these names before relying on this document, since that file is now owned elsewhere.

| Setting | Type | Default | Read by |
|---|---|---|---|
| `DATA_RESIDENCY_PRIMARY_DB_REGION` | `str` | `"unspecified"` | `Settings.production_issues()` (self-consistency check only); cited by `BACKUP_POLICY.md`/this document |
| `DATA_RESIDENCY_BACKUP_REGION` | `str` | `"unspecified"` | Same as above |
| `DATA_RESIDENCY_ASSERT_INDIA_ONLY` | `bool` | `False` | `Settings.production_issues()` — when `True` in production, requires both region fields above to be non-`"unspecified"` or startup refuses (fail-closed); a no-op when `False` (default), so it cannot newly block any deployment that hasn't opted in |
| `SDF_NOTIFIED` | `bool` | `False` | Not read by any code path yet — a documentation-only flag today, intended for a future process/CI check gating on DPIA/register currency once actually notified as an SDF (see table above) |
| `BACKUP_ENCRYPTION_ENABLED` | `bool` | `False` | Not read by any code path — deliberately excluded from `production_issues()` (see the field's own comment in `config.py`: gating production startup on a flag nobody has been told to set yet would be a silent, cross-cutting breaking change to every deployment, not a narrow honesty check) |
| `BACKUP_RETENTION_DAYS` | `int` | `35` | Not read by any code path — policy documentation value only (`BACKUP_POLICY.md` §2) |
| `BACKUP_RPO_MINUTES` | `int` | `60` | Not read by any code path — policy target only (`BACKUP_POLICY.md` §5) |
| `BACKUP_RTO_MINUTES` | `int` | `240` | Not read by any code path — policy target only (`BACKUP_POLICY.md` §5) |

Test coverage for these fields: `backend/tests/test_data_residency_flags.py` (5 tests — defaults,
that they never newly block a production startup that hasn't opted in, the opt-in self-consistency
check firing/passing correctly, and that it's a no-op outside production). **This test file depends on
these exact field names existing in `config.py`** — if the config.py owner renames or restructures them,
update that test file to match (it is owned by this lane, `backend/tests/`).

## What an operator must still do (outside this codebase's reach)

1. Provision the primary Postgres instance in an Indian region.
2. Configure backup storage (bucket/volume) in an Indian region with encryption at rest.
3. Record both facts via `DATA_RESIDENCY_PRIMARY_DB_REGION`/`DATA_RESIDENCY_BACKUP_REGION`/
   `BACKUP_ENCRYPTION_ENABLED` so they are discoverable in one place.
4. Only then set `DATA_RESIDENCY_ASSERT_INDIA_ONLY=true` — the flag is a claim to be made *after* the
   infrastructure is true, not a switch that makes it true.
5. If/when notified as an SDF: set `SDF_NOTIFIED=true`, get DPO sign-off on `DPIA_TEMPLATE.md`, and
   establish the periodic data-audit process the Rules require (not built — see the gap noted above).
