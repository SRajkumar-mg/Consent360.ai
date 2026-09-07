# Consent360 — architecture guide

This file is the actively-maintained ground truth for how this codebase actually works.

## What this is

Consent360, a DPDP/GDPR-style consent management platform. Everything lives under `cms/`, which **is** a git repository (the parent directory is not) —
current work is on the `build/s2-phase0` branch, with `.env` ignored and CI at
`.github/workflows/backend-ci.yml`.
`DPDP_COMPLIANCE_GAP_ANALYSIS.md` at the repository root is the DPDP Act/Rules compliance gap register for this
product (item IDs like A-01, CM-01, KPI IDs K-01); extend it rather than starting a new one. The same register is
exported to `Consent360_DPDP_Gap_Register.xlsx` (regenerate rather than hand-edit) and summarised for the team in
`EMAIL_REPLY_CONSENT_ARCHITECTURE_REVIEW.md`, which also maps consent entities onto the Infinity Governance schema
(`../../Infinity Governance/Data_Governance`).

`cms/README.md` and `cms/PROJECT.md` describe the product well but their **ports and role lists are stale**; trust
each app's `package.json` dev script and `vite.config.ts`, and `backend/app/core/rbac.py`, over the docs.

One FastAPI backend serves several React/Vite frontends:

| Directory | What it is | Dev port | Proxies |
|---|---|---|---|
| `cms/backend` (`app.main:app`) | Consent platform API, all routers | 8000 | – |
| `cms/backend` (`app.crm_app:app`) | Optional standalone CRM-directory API (same `crm_directory` router that `app.main` already mounts; the frontends do not need it) | 8001 | – |
| `cms/frontend` | Staff admin console | 8005 | `/api` → 8000 |
| `cms/codex` | Demo client site (coding platform), `source_app=CODEX` | 5174 (`npm run dev` passes `--port`, overriding the 8006 in `vite.config.ts`) | `/api`, `/cmp` → 8000 |
| `cms/skilllearn` | Demo client site (learning platform), `source_app=SKILLLEARN` | 5175 (same `--port` override; config says 8007) | `/api`, `/cmp` → 8000 |
| `cms/crm` | Demo CRM with cookie banner, `source_app=CRM_PORTAL` | 8008 | `/api`, `/cmp` → 8000 |
| `cms/portal/job-portal` | Demo job site (React 19), `source_app=CAREER_HUB`; no proxy, calls `http://localhost:8000` directly with the dev API key | 5180 | – |
| `cms/sdk/{python,javascript}` | Current packaged SDKs (`consent360`, `@consent360/sdk`) | – | – |
| `cms/sdks/{python,javascript}` | Older single-file SDK generation (`Consent360Client`); superseded by `sdk/` | – | – |

Backend `config.py` defaults `CONSENT_PORTAL_URL` to `:5173` and `CRM_PORTAL_URL` to `:8005`; these build the `ui_url`
returned by context endpoints, so override them in `.env` if the admin console is not on 5173.

## Commands

### Backend (`cms/backend`)

The checked-in `.venv` is a **Windows venv** (`Scripts/`, `.exe`); on macOS/Linux create a fresh one:

```bash
cd cms/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # then set DATABASE_URL, JWT_SECRET, FIELD_ENCRYPTION_KEY
python -m alembic upgrade head
python seed.py                    # idempotent demo data; re-syncs role permissions from rbac.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Other backend commands:

```bash
python -m alembic revision --autogenerate -m "<msg>"   # after model changes
python seed_org_roles.py          # org-scoped roles + platform users (deletes consent_manager role!)
python seed_orgs.py               # Organization + OrganizationUser rows (org portal login)
python -m scripts.encrypt_existing_data            # DRY RUN: reports plaintext residue, writes nothing
python -m scripts.encrypt_existing_data --apply    # actually re-encrypts (after setting FIELD_ENCRYPTION_KEY)
python clear_customers.py         # wipes customers/consents/audit tables
python -m uvicorn app.crm_app:app --port 8001   # only if you want the separate CRM service
```

`alembic/env.py` overrides the URL in `alembic.ini` with `DATABASE_URL` from `.env`, so `.env` is the source of truth
for the database (the current `.env` uses port 5432; docs and defaults say 5433).

There **is** a pytest suite: `backend/tests/` (48 `test_*.py` files; `pytest.ini` sets `testpaths = tests`) —
**756 passing**, verified by actually running `pytest` against a disposable Postgres database that `tests/conftest.py`
creates and drops itself (`TEST_DATABASE_URL`, else `DATABASE_URL` with its database name swapped for
`consent_platform_test`). CI is `.github/workflows/backend-ci.yml` (pytest + `pip-audit` + `bandit` + an alembic-drift
check on a fresh database); per its own header comment it has never actually run on a remote (this environment has no
git push target), so `.github/scripts/backend_ci_local.sh` runs the identical steps locally and is what has actually
gone green. There is still no style linter — `bandit` above is a security scanner, not one. Separately, the
`test_*.py` files directly under `backend/` (`test_e2e.py`, `test_login.py`, ...) are a different thing: ad-hoc scripts
outside the pytest suite (`testpaths = tests` doesn't collect them), still run by hand with `python test_e2e.py` etc.
against a live database (and a running server for `test_login.py`, which needs `requests`).
Health check: `GET /health`; OpenAPI docs at `/docs`.

Dependencies not in `requirements.txt`: `langchain-groq` (lazily imported by `routes/chatbot.py`, needs `GROQ_API_KEY`)
and `requests` (ad-hoc tests, `sdks/python`).

### Frontends (each of `cms/frontend`, `cms/crm`, `cms/codex`, `cms/skilllearn`, `cms/portal/job-portal`)

```bash
npm install
npm run dev          # port from the dev script's --port flag if present, else vite.config.ts
npm run build        # tsc -b && vite build  (type errors fail the build)
npm run typecheck    # frontend and crm only
```

Optional `frontend/.env`: `VITE_API_URL` (defaults to `/api`), `VITE_INTEGRATION_API_KEY` (defaults to the dev key).

### SDKs

```bash
cd cms/sdk/javascript && npm install && npm run build
cd cms/sdk/python && pip install -e .
```

Fixed: `sdk/python/setup.py` packages the `consent360/` directory, and its modules import `consent360.*`, so
`pip install -e .` installs and imports cleanly. (This used to be a real bug — the packaged directory was named
`consenthub/` while the code imported `consent360.*` — but the directory has since been renamed.)

## Backend architecture

Layering is `api/routes/*` → `services/*` → `models/entities.py` (single file, all tables) with `schemas/schemas.py`
(single file, all Pydantic models) plus a handful of split-out schema modules for routers whose shapes only one file
reads (`schemas/legacy_notice.py`, `schemas/reconsent.py`, `schemas/breach.py`, `schemas/erasure.py`, ...). `app/main.py`
mounts 34 router registrations; `GET /openapi.json` reports **219 paths** as of this writing (this file has drifted
badly on basic facts before — it once listed only ~14 prefixes here and, elsewhere in this file, claimed there was
no automated test suite at all when there was one all along; re-derive counts like this one from the live
`/openapi.json` rather than trusting a number written down here). Prefixes: `/auth` (also staff user CRUD at
`/auth/users`), `/customers`, `/consent` (integration), `/crm` (two routers: `crm.py` for consent preferences, context
and purge; `crm_directory.py` for login/list/delete), `/consents`, `/purposes`, `/data-categories`,
`/processing-activities`, `/policies`, `/notices`, `/receipts`, `/sharing-events`, `/objections`, `/grievances`,
`/breaches`, `/erasure`, `/re-consent` (material-change re-consent campaigns, and on the same router the s.5(2)
legacy-notice campaign under `/re-consent/legacy-notice/*` — see `services/legacy_notice.py`), `/rights`, `/children`
(guardian consent and child age assurance — the router file is `guardian.py`, the prefix is `/children`), `/retention`,
`/processors`, `/analytics` (plus an unauthenticated `/public` router of its own, `analytics.public_router`),
`/consent-manager`, `/decisions` (two routers: `decision_validation.py` and `decisions.py` both use this prefix),
`/notifications`, `/audit`, `/dashboard`, `/admin` (roles), `/organizations`, `/portal`, `/public` (`public.py`, plus
the `analytics.public_router` above sharing the same prefix), `/chatbot`.

### Four authentication schemes (`app/api/deps.py`)

1. **Staff JWT** (`Authorization: Bearer`, payload `ctx="consent-auth"`, `type="access"`, **`sub_type="staff_user"`**):
   `get_current_user`, `require_permission("consent.manage")`. Permission strings and per-role lists live in
   `app/core/rbac.py`; roles are rows in `roles` with a JSON permission list. `rbac.py::sync_roles` idempotently
   upserts every role in `ROLE_PERMISSIONS` and is called from `app/main.py`'s lifespan on **every startup**
   (re-syncing a role's permissions if `rbac.py` drifted from the DB; it never touches a custom role created via
   `POST /admin/roles`), so an edit to `rbac.py` takes effect on the next restart/reload — it no longer strictly needs
   a re-seed. `seed.py` still runs the same upsert itself (and is still what creates the demo users), so
   `python seed.py` remains the right thing to run after an `rbac.py` change; only the "you must re-seed or it won't
   take effect" framing is now inaccurate. The `sub_type` claim is mandatory and checked in
   `get_current_user`, `/auth/refresh` and `/auth/me`: it exists because `users.id` and `organization_users.id` are
   independent sequences that collide, and without it an organization user's token resolved to the staff row sharing
   its id. A token without the claim is rejected, so any token minted before that fix is invalid.
2. **Integration API key** (`X-API-Key`, `verify_integration_key`) returning a `ResolvedApiKey(tenant_code, tenant_id)`:
   `POST /consent/customer-context`, `DELETE /crm/customers/by-email/{email}`. Keys live in `api_keys`, are stored only
   as a SHA-256 hash, carry scopes that `require_scope` enforces, and are bound to one tenant — a request whose
   `source_app` differs from the key's tenant is refused. `ALLOW_LEGACY_INTEGRATION_KEY` (default **false**) is the only
   way the old shared `INTEGRATION_API_KEY` still works; leave it off outside local demos.
3. **Context token** (JWT `ctx="integration"`, `type="consent-context"`, 15 min): minted by the integration endpoint,
   sent as `X-Context-Token` to `/portal/*` or as the path param of `/consent/context/consume/{token}`. It is the
   customer's only credential; no PII goes in URLs.
4. **Organization token** (`ctx="org-auth"`, `type="org-access"`, `sub_type="org_user"`, `routes/organizations.py`):
   separate `OrganizationUser` table and `/organizations/auth/login`, which is now the **only** way an organization user
   authenticates — `/auth/login` no longer has an org branch. Route order matters there: `/portal/dashboard` is declared
   before `/{org_id}`.

### Tenancy: `organizations` is the tenant, `source_app` is its code

`organizations` is the tenant entity — it carries the DPO contact, the withdraw/rights/grievance/Board-complaint links,
`grievance_response_days` (a database check constraint enforces <= 90), `default_language`, `environment` and a settings
blob, and `tenant_id` now hangs off customers, consents, purposes, policies, audit rows, evidence and the job tables.
`app/services/tenancy.py` resolves it: `resolve_tenant_id(db, source_app)` (memoised per Session in `db.info`, and it
creates a tenant on first sight of an unseen `source_app`), `platform_tenant_id(db)` and `PLATFORM_TENANT_CODE`. Every
row-creating path must set `tenant_id` — `seed.py` included.

**Any lookup keyed on an email, external id or other caller-supplied identifier must be scoped by tenant.**
`create_context_for_customer` once matched on email globally, which let one tenant's API key mint a verified context for
another tenant's customer and read their consent record. It is now scoped, the fiduciary assertion separately re-checks
that the customer belongs to the asserting tenant, and both `get_customer_from_context` and
`portal.py::_resolve_customer_and_context` refuse a mismatch independently.

`source_app` is the tenant's code. Every demo client logs in through `POST /crm/login` with its own `source_app`, and the
integration API stamps `source_app` on customers and consents. Staff roles `jobhub_admin` / `codex_admin` /
`skilllearn_admin` map through `ORG_SCOPE_MAP` to a `source_app`; `get_org_scope(user)` returns it and the customers,
consents, audit and dashboard routes filter on it (`None` means unscoped). Add the filter to any new list endpoint.

`app/services/context.py::assert_identifiable_principal` refuses to create a customer record for an email matching
`visitor-<timestamp>@...` (`_is_synthetic_visitor_email`) — the placeholder a calling site fabricates for an anonymous
visitor who gave no real identifier (DPDP s.6(1): a consent needs an identifiable person behind it). An explicit
`customer_id` or a `phone` exempts the caller. The predicate **and** the 422 both live in that one function, called from
every path that first creates a customer record: `create_context_for_customer` (so `POST /consent/customer-context` and
`crm.py`'s own `.../consent-context`) and, since the B-04 follow-up, `crm_directory.py::crm_login` — the one
customer-creating endpoint that takes no credential at all, which used to write the `CrmCustomer`/`Customer` rows
directly via `_ensure_consent360_customer` and sail straight past the guard. `tests/test_data_minimisation.py` asks both
call sites about the same table of addresses and fails if their verdicts ever diverge.

Consent rows are per **customer × purpose × data category × processing activity × source_app**.
`_ensure_source_consent_matrix` (`routes/integration.py`) and `get_or_create_consent(exact_source=True)` create an
independent row per real source; placeholder rows whose `source_app` is `""`, `"SYSTEM"` or `"UI"` are adopted by the
first real source that touches them.

### Consent lifecycle and decisions

- Statuses and legal transitions: `CONSENT_STATUSES` / `CONSENT_TRANSITIONS` in `models/entities.py`.
- All transitions go through `services/consent.py` (`request_consent`, `grant_consent`, `deny_consent`,
  `withdraw_consent`, `renew_consent`, `expire_consents`). Each validates the transition, bumps `consent_version`,
  writes `ConsentHistory` + `ConsentEvidence`, and calls `log_audit`. Never set `Consent.status` directly.
- Purposes and policies are versioned (`PurposeVersion` / `PolicyVersion` with `is_current`); a consent pins the
  `purpose_version_id` / `policy_version_id` in force when it was created. `get_active_policy` returns the first active
  policy that has a current version, so in practice there is one global policy.
- `services/decision_engine.py::evaluate_decision` order: explicit policy rule `DENY` → consent status
  (WITHDRAWN / DENIED / EXPIRED / active → ALLOW) → rule `requires_active_consent=False` → `purpose.requires_consent=False`
  → `REQUIRE_CONSENT`. Writes `ConsentDecisionLog` and an audit row.
- `services/audit.py::log_audit(db, event, ..., commit=False)`; event names are in `AUDIT_EVENTS`.
- The CRM cookie banner maps categories `strictly_necessary` / `functional` / `analytics` / `advertising` to Purpose
  codes; `PUT /crm/customers/{id}/consent-preferences` mirrors toggles into consent rows via
  `_sync_consent_preferences`, which is why the admin console and the banner agree. A switched-off category is
  **recorded**, not merely skipped: `deny_consent` for a row that was never granted (`DENIABLE_STATUSES`),
  `withdraw_consent` for one that is live (`WITHDRAWABLE_STATUSES`) — both tuples derived from `CONSENT_TRANSITIONS` in
  `services/consent.py` and shared with `/portal/deny`, which is the only other refusal path. `deny_consent` writes a
  `ConsentEvidence` row like every other transition, so a refusal's record is as strong as a grant's (B-01/B-02,
  s.6(10)). The one carve-out is `gpc.py::purpose_is_consent_based`: the banner *infers* a refusal from a category map,
  and `categories.get(key, False)` would otherwise read an absent `necessary` as denying the seeded
  `strictly_necessary` purpose (s.7(a), `requires_consent=False`) — which `evaluate_decision` reads before it ever
  reaches the `requires_consent=False → ALLOW` rule, so a reject-all would switch off login and session security.
  `/portal/deny` names one purpose explicitly and needs no such guard.

### Background jobs (`app/jobs/`)

An in-process APScheduler (`app/jobs/scheduler.py::start_scheduler`) runs **13 jobs** through `JobRunner`, which writes
a `scheduler_runs` row per run with counts and any error, and gives every job an immediate first run at startup (not
only after its first full interval elapses) so a process shorter-lived than a job's interval still produces a row:
`expire_consents` (15 min), `notification_dispatch` (5 min — paced by the notification's own retry backoff, not this
interval), `processor_alert_dispatch` (15 min, kept under the shortest processor ack SLA), `renewal_reminders` (24h),
`context_token_cleanup` (hourly, which also hashes stored context tokens — `context_status` hashes the supplied token
so consumed and expired lookups keep working), `kpi_rollup` (hourly, writing a `KpiSnapshot`), `audit_chain_verify`
(24h), `retention_scan` (24h, read-only — enforcement is the separate, actor-attributed `POST /retention/enforce`),
`grievance_escalation` (hourly, escalates overdue grievances to the DPO), and the erasure engine's four jobs:
`erasure_retention_scan` (24h), `inactivity_scan` (24h), `pre_erasure_notices` (hourly) and `erasure_executor` (hourly
— the one background job that can destroy personal data, acting only on jobs a named actor already authorised).
**`SCHEDULER_ENABLED` defaults false**, so nothing expires (or dispatches, or escalates, or executes) by itself until
it is set — tests rely on that default, deployments must override it. `expire_consents` is no longer run from the
dashboard request.

### Audit ledger is append-only

`audit_logs` is hash-chained per tenant (`app/core/audit_chain.py`: `entry_hash = sha256(prev_hash + canonical(row))`)
and a database trigger blocks UPDATE and DELETE for every role, so **code that inserts an audit row and then mutates it
will raise**. `log_audit` pre-allocates the id, inserts once, and serialises appends per tenant with an advisory lock —
without it two concurrent writes chain off the same ancestor and `verify_chain` reports a false break. `created_at` is
normalised to UTC before hashing, and the migration mirrors that logic exactly; the two copies must never diverge.
Purge anonymises the customer and keeps every audit row. `scripts/harden_audit_role.sql` only helps if the application's
role does not own the table.

### Field-level encryption (`app/core/encryption.py`)

Sensitive columns use `EncryptedString` / `EncryptedText` / `EncryptedJSON` TypeDecorators (AES-256-GCM). Encryption is
active only when `FIELD_ENCRYPTION_KEY` is set; otherwise values are stored in plaintext with a warning. Consequences:

- Encrypted columns cannot be filtered by equality or `LIKE`. Use the companion `*_search` column with
  `hmac_digest(value)` (`Customer.email_search`, `Customer.external_id_search`, `User.email_search`,
  `CrmCustomer.email_search`, `OrganizationUser.email_search`).
- A SQLAlchemy `before_insert`/`before_update` listener (`app/models/entities.py::_fill_search_columns`, registered on
  `User`, `Customer`, `CrmCustomer`, `OrganizationUser` and `Notification`) now recomputes `email_search`,
  `external_id_search` and `Notification.recipient_search` from the corresponding plaintext attribute on every write.
  Setting the `_search` column by hand is therefore no longer required — this used to be manual (`seed.py` was once the
  cautionary example of forgetting, per this file's own history), but any write that sets the plaintext field now gets
  its digest for free.
- `scripts/add_search_columns.py` altered the schema outside Alembic; expect autogenerate to notice.

### Cross-cutting

- `CorrelationIdMiddleware` (`core/utils.py`) reads or generates `X-Request-ID`; `get_request_id()` is a module-level
  global, not a contextvar. Error responses always include `request_id`.
- Rate limiters (`login_limiter`, `context_limiter`) are in-memory per process.
- `utils.mask_identifier` and `crm/src/mask.ts` mask emails/phones for display; keep PII out of logs and URLs.

## Frontend architecture (`cms/frontend`)

- `src/api/client.ts` is the axios instance: tokens in `localStorage` (`cmp_access_token`, `cmp_refresh_token`),
  automatic refresh on 401 except for `/auth/login` and `/portal/*`. Every endpoint wrapper lives in `src/api/index.ts`
  (`authApi`, `consentsApi`, `organizationsApi`, ...); shared types in `src/types.ts`.
- `AuthContext.hasPermission(perm)` gates both the sidebar (`NAV` in `components/Layout.tsx`) and page actions; routes are
  wrapped in `Shell` (`RequireAuth` + `Layout`) in `App.tsx`.
- `/consent/context/:token` (`ContextLanding.tsx`) consumes an integration token and redirects to
  `/customers/:external_id?context=...`; it still requires a staff login.
- i18n: `src/translations/<lang>.ts` for 23 Indian languages typed by `translations/types.ts`; `useTranslation` picks the
  file via `getTranslations(lang)` (falls back to English per language, not per key). Adding a UI string means adding
  the key to `types.ts`, `en.ts` and every other language file or the build fails. The `_gen*.py` files there are
  one-off generators, not part of the build.
- `pages/ApiReference.tsx` and `pages/Consent360.tsx` contain hard-coded endpoint docs and SDK snippets; update them
  when integration or portal endpoints change.

The demo clients (`crm`, `codex`, `skilllearn`) are near-copies: two axios instances, `/api` for the CRM directory
(`/crm/login`) and `/cmp` for consent preferences, both proxied to port 8000. `job-portal` instead performs the full
integration handoff itself (`/consent/customer-context` → `/portal/overview|grant|withdraw`) with the API key in
client code, which is acceptable only because it is a demo.

## Seed data and accounts

`seed.py`'s role-provisioning loop actually iterates all of `rbac.ROLE_PERMISSIONS`, so it creates/upserts every role
defined there — `admin`, `consent_manager`, `dpo`, `auditor`, `operator`, `viewer`, `jobhub_admin`, `codex_admin`,
`skilllearn_admin` — not just `admin`/`consent_manager`/`viewer`. Immediately after that loop, though, `seed.py`'s own
`legacy_role_map` step remaps a role literally named `auditor` onto `consent_manager` and **deletes it** — verified by
reading `seed.py`: the map's `"auditor": "consent_manager"` entry treats `auditor` as a retired legacy name, so a bare
`python seed.py` run leaves no `auditor` role in the DB even though it was just created two steps earlier from
`rbac.py`. (`app/main.py`'s `sync_roles`, which has no such remap, recreates it on the next server startup — so the
role's survival currently depends on whether the server has restarted more recently than `seed.py` last ran.) Also
creates the demo users listed in `cms/README.md` (`admin` / `Admin@1234`). `seed_org_roles.py` then
**deletes `consent_manager`** and reassigns its users to `viewer`, so run it only if you want the org-scoped setup.
The README's claim of exactly two roles is outdated.
