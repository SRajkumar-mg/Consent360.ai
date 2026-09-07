# Algorithm Register

**Scope note:** this register describes the two systems in this codebase that make or influence a
decision about a data principal or a staff user's request, as required for SDF (Significant Data
Fiduciary) readiness (DPDP Act 2023). It is a direct read of the code, not a generic template — every
claim below cites the file and function it describes. There is no other rule-based or ML-based
decisioning anywhere else in `cms/backend` (searched: `app/services/`, `app/api/routes/`, `app/jobs/`);
scoring, ranking, profiling, or automated-eligibility logic does not exist in this codebase today.

Last reviewed against source: 2026-09-04 (commit tree at branch `build/s2-phase0`, ahead of that
branch's still-unmerged, in-progress work by other agents in the same session — re-check this register
after that work lands, since `decision_engine.py` and `chatbot.py` were not among the files being
edited by that work at the time of this review).

---

## 1. Consent Decision Engine

**File:** `backend/app/services/decision_engine.py`
**Entry point:** `evaluate_decision(db, customer, purpose, data_category, processing_activity, ...)`
**Type:** Deterministic, rule-based (no ML, no scoring, no learned weights). Every output is one of a
fixed set of string labels, each traceable to an explicit `if`/`elif` branch in the source.

### What it decides

Given a (customer, purpose, data category, processing activity) tuple, whether processing is currently
permitted. Called from the integration API (`routes/integration.py`) on every consent-context mint and
from the CRM/portal grant paths, and is the authoritative "can we do X with this person's data for this
purpose" answer the platform gives to an integrating application.

### Decision logic (exact order, from source)

1. **Active policy lookup** (`get_active_policy`): the first `Policy` row with `is_active=True`,
   `status="ACTIVE"`, and a `PolicyVersion` with `is_current=True`. In practice there is one global
   policy (see `docs/ARCHITECTURE.md`).
2. **Explicit policy rule** (`find_applicable_rule`): if the current policy version's `rules` JSON
   contains an entry matching this exact `(purpose_code, data_category_code, processing_activity_code)`
   triple with `"decision": "DENY"` → **DENY**, reason cites the policy code/version. This is checked
   *before* looking at consent status at all — an explicit deny always wins.
3. **Consent status lookup**: the most recent `Consent` row for the identity (highest
   `consent_version`, then most recent `created_at`).
   - `status == "WITHDRAWN"` → **WITHDRAWN**
   - `status == "DENIED"` → **DENY**
   - `status == "EXPIRED"` or `expires_at <= now` → **EXPIRED**
   - `status` in `{GRANTED, ACTIVE, RENEWED, UPDATED}` and not expired → **ALLOW**
4. **No consent row, or none of the above matched**, and the matched rule (if any) sets
   `requires_active_consent: false` → **ALLOW**, citing the policy.
5. **No rule, and `purpose.requires_consent is False`** (e.g. a Chapter III legitimate-use gateway
   under DPDP s.7) → **ALLOW**, citing the purpose's legal basis.
6. **Otherwise** → **REQUIRE_CONSENT**.

### Inputs

`Customer`, `Purpose`, `DataCategory`, `ProcessingActivity` rows (all staff/API-managed reference data,
not free text); the caller-supplied `requested_by`, `source_app`, `request_id` are logged but never
influence the decision. No demographic, behavioural, or derived/profiled attribute of the data
principal is read anywhere in this function — the decision is a function of policy configuration and
that one customer's own consent history for that exact purpose/category/activity, nothing else.

### Outputs and their effect

One of `DENY | WITHDRAWN | EXPIRED | ALLOW | REQUIRE_CONSENT`, each with a human-readable `reason`
string built from the actual policy/purpose/consent data (see `Decision.__init__` and the string
templates in `evaluate_decision`). `_finish()` persists every evaluation as a `ConsentDecisionLog` row
(full inputs, decision, reason, requesting actor, `source_app`, `request_id`) and an `AUDIT_EVENTS`
`DECISION_EVALUATED` row via `log_audit` — i.e. every decision this engine ever makes is retained,
attributable, and independently queryable, not just the aggregate outcome.

### Human oversight / contestability

- The decision is never final in the DPDP sense of denying a *right* — it gates whether an integrating
  application may process data for a purpose the principal was already asked to consent to; the
  principal's actual lever is the consent grant/withdraw action itself (`services/consent.py`), not an
  appeal against this function. A principal disputing a `REQUIRE_CONSENT`/`DENY`/`EXPIRED` result has a
  concrete, auditable trail to point to: the underlying consent record's status history and the
  `ConsentDecisionLog`/`reason` string this function wrote at the moment it decided.
- Policy rules (the one input that can force a DENY ahead of consent status) are staff-authored and
  version-controlled (`Policy`/`PolicyVersion`, `routes/policies.py`, `PERM_POLICY_MANAGE`) — a human,
  identifiable via `PolicyVersion.created_by` and the audit trail, is always responsible for any
  explicit-deny rule in force.
- `ConsentDecisionLog` + the audit trail (`tests/test_audit_chain.py`) give a DPO/auditor a complete,
  tamper-evident record of every decision made, for regulator or grievance-officer review.

### Known limitations (be honest about what this is NOT)

- It is a lookup/branch tree, not a model — there is no accuracy, bias, or drift to monitor in the ML
  sense. The register still tracks it because it is automated decision-making about a data principal
  within the meaning the Act's DPIA-trigger language is generally read to cover.
- It does not itself check purpose-limitation against what the *caller* actually intends to do with the
  data — that is enforced by the calling application's own use of the `ALLOW`/`REQUIRE_CONSENT` result,
  which this codebase cannot verify from the platform side.

---

## 2. Consent360 AI Assistant (chatbot)

**File:** `backend/app/api/routes/chatbot.py`
**Entry point:** `POST /chatbot` → `chat(body, db, current_user)`
**Type:** Third-party hosted LLM (Groq, model `openai/gpt-oss-120b` as pinned in `_call_groq`), wrapped
in this codebase's own pre-call gating, redaction, and post-call disclosure. The model weights
themselves are not this codebase's algorithm; what IS this codebase's algorithm, and what this register
covers, is the **governance wrapper** around that call: the processor-gate check, the PII-redaction
pipeline, and the context assembled and sent to it.

### Who can use it

Staff only, gated by `require_permission(PERM_DASHBOARD)` — no principal-facing or unauthenticated path
reaches this code at all.

### What it decides / does

Answers a staff user's free-text question about the platform (consent counts, purposes, policy rules,
DPDP concepts) using a live aggregate snapshot of platform data (`_gather_platform_context`) plus the
user's own message and prior turns, sent to Groq's hosted model and returned verbatim (modulo the
redaction note prepended when applicable). It does not take any action on a customer's data, does not
gate or influence any consent decision, and cannot look up or answer questions about a named individual
customer by design (see the system prompt's explicit refusal instructions and `_gather_platform_context`,
which only ever queries aggregate `COUNT`/`GROUP BY` statistics and never a specific customer row).

### Processor gate (`_llm_processor_block_reason`)

Before every call, checked in this order:
1. Is there an active `Processor` row with `type == PROCESSOR_TYPE_LLM_PROVIDER` and `is_active=True`?
   If not → blocked.
2. Has its `contract_valid_until` passed? If so → blocked.
3. For every `Transfer` row tied to that processor, is `destination_country` currently on
   `settings.RESTRICTED_COUNTRIES`? The stored `Transfer.restricted` flag is recomputed against the
   *live* config on every single call (not just read stale) — a country added to the restricted list
   after the transfer row was created is picked up immediately. If any transfer is currently
   restricted → blocked.

A block never raises or 500s; the user gets `_BLOCKED_REPLY` (a plain "temporarily unavailable, contact
your administrator" message, no internal detail) and a `CHATBOT_BLOCKED` audit row is written with the
real internal reason (for a DPO/auditor, never shown to the end user).

### Outbound PII redaction (`_redact_pii` and its regex set)

Before the user's message and conversation history are sent to Groq, four PII shapes are
pattern-matched and replaced with a stable placeholder: email addresses, `CUST-...`-shaped external
customer IDs, phone numbers (10-digit Indian mobile shape, with/without `+91`), and 12-digit
Aadhaar-shaped numbers. Matching runs against an NFKC-normalised, zero-width-character-stripped copy of
the text (closing a homoglyph/zero-width-insertion evasion) and every regex is deliberately
bounded-quantifier to keep the whole pass linear in input length (see the file's own extensive comments
on the ReDoS incident this fixes). This is explicitly documented as **best-effort, not a guarantee** —
see `_redact_pii`'s docstring for the enumerated gap: an identifier split across two chat turns, an
obfuscated form ("jane [at] example dot com"), postal addresses, PAN/passport numbers, or a name typed
in prose are none of them caught. `_CHANNEL_NOTICE` is returned on **every** response (not only when a
redaction actually fired) so staff cannot infer "nothing sensitive could have gone through" from an
absent redaction count.

### What is sent to the third party (Groq)

- The system prompt, which embeds `_gather_platform_context`'s output: counts/aggregates only (total
  customers, consent status distribution, purpose/policy/category/activity metadata, the last 15 audit
  events' *type and actor role*, never actor username or any customer-identifying field — see
  `tests/test_chatbot_governance.py::test_platform_context_excludes_actor_usernames`).
- The user's own message and up to the last 10 history turns (`_call_groq` slices `history[-10:]`),
  after redaction.
- Nothing else. No raw customer table rows, no encrypted-column plaintext, ever leave this function.

### Human oversight / contestability

- Every block decision is audited (`CHATBOT_BLOCKED`) with its real reason, for a DPO to review.
- The chatbot's answers are advisory only — it never writes to any table other than the audit log, so
  there is no automated action for a principal or staff member to contest; the worst-case failure mode
  is a wrong or incomplete answer to a staff question, not an incorrect determination about a person.
- The system prompt hard-codes a refusal policy for any individual-customer lookup; this is a prompt
  instruction, not a code-enforced guarantee — the redaction pipeline above is the actual code-level
  control against personal data leaving the platform, and it is a backstop, not a filter that catches
  everything (see its own documented limitations).

### Known limitations (be honest about what this is NOT)

- The redaction pipeline is shape-matching, not a DLP system: the documented gaps above are real and
  should not be characterised as fully solved anywhere else in the codebase or in policy documents.
- The processor/transfer gate defends against using an *unauthorised* processor or a *newly restricted*
  destination; it does not, and cannot, verify what Groq itself does with the data once received — that
  is a contractual/DPA matter (`Processor.contract_ref`), not something this code can enforce.
- No human review step exists before a reply reaches the requesting staff user (the "human oversight"
  above is after-the-fact audit review, not a pre-send approval gate) — appropriate for an internal,
  advisory tool answering aggregate questions, but worth stating plainly rather than implying real-time
  human-in-the-loop review exists when it does not.

---

## Register maintenance

Update this document whenever `decision_engine.py`'s branch logic or `chatbot.py`'s gating/redaction
logic changes in a way that alters what it decides, what data it sees, or what leaves the platform.
This file has no automated staleness check; a future task could add one (e.g. a test asserting this
file's "last reviewed" line is no older than the last commit touching either source file).
