# Consent Management Platform (Consent360)

A standalone, locally-run consent management platform that integrates with an existing
Customer Management application. Customers are managed in your business application;
consent is managed here. The integration is secure, server-to-server, and never exposes
sensitive customer information in URLs.

Built with **FastAPI + PostgreSQL + Alembic** (backend) and **React + TypeScript + Vite** (frontend).

> **Ports and roles drift out of date here faster than this file gets updated (T-11).** For the
> current, actively-checked ground truth, trust `docs/ARCHITECTURE.md` at the repo root over this section:
> it defers to each app's own `package.json` dev script / `vite.config.ts` for ports, and to
> `backend/app/core/rbac.py` for the real role list.

One FastAPI backend (`app.main:app`, port **8000**) serves every frontend below; there is also an
optional standalone CRM-directory service (`app.crm_app:app`, port **8001**) that the frontends do
not actually need, kept only for callers that want the `crm_directory` router on its own.

| App | What it is | Dev port |
| --- | --- | --- |
| `frontend` | Staff admin console | **8005** |
| `crm` | Demo client site with a cookie banner (`source_app=CRM_PORTAL`) | **8008** |
| `codex` | Demo client site, coding platform (`source_app=CODEX`) | **5174** |
| `skilllearn` | Demo client site, learning platform (`source_app=SKILLLEARN`) | **5175** |
| `portal/job-portal` | Demo client site, job board (`source_app=CAREER_HUB`) | **5180** |

Backend `config.py` defaults `CONSENT_PORTAL_URL` to `:5173` (a legacy value from before the admin
console moved to 8005) - override it in `.env` if the admin console is not on 5173, or context-token
UI URLs returned by the API will point at the wrong port.

---

## System overview

```
                    +-----------------------------------+
                    |   Consent Management Platform      |
                    |                                   |
  Existing app  --->|   POST /consent/customer-context  |  API key (X-API-Key)
  (Customer CRM)    |   (validates customer, upserts,   |
                    |    returns short-lived context)   |
                    +------------------+----------------+
                                       | context token (JWT, 15 min, single use,
                                       |  no PII in URL)
                                       v
                    +-----------------------------------+
                    |   Consent Management UI           |
                    |   /consent/context/<token>        |
                    |   customer consent dashboard      |
                    |   grant / deny / withdraw / renew |
                    +------------------+----------------+
                                       |
                                        v
                     +-----------------------------------+
                     |   Decision Engine                 |
                     |   evaluated automatically during  |
                     |   the consent lifecycle           |
                     |   ALLOW / DENY / REQUIRE_CONSENT /|
                     |   WITHDRAWN / EXPIRED             |
                     |   (writes decision log + audit)   |
                     +-----------------------------------+
```

Key features:

- **Consent lifecycle** with full state machine and immutable history:
  `NOT_REQUESTED → PENDING → GRANTED → ACTIVE → WITHDRAWN / EXPIRED / DENIED`,
  with renew/update/request transitions.
- **Deterministic decision engine**: given customer + purpose + data category + processing
  activity, returns a decision and reason, and records every evaluation in an audit trail.
- **Versioned purposes & policies**: changing a purpose or policy creates a new version;
  existing consents keep the version that was in force at collection time.
- **Evidence capture**: every consent event stores an evidence reference, collected-at
  timestamp, collector, method and the purpose/policy versions in force.
- **RBAC** with granular, permission-based roles defined in `backend/app/core/rbac.py` (the
  source of truth — see "Demo accounts" below for the current list): from full-access **Admin**
  down to read-only **Viewer**, plus specialised **DPO**, **Auditor** and **Operator** roles and
  per-tenant org-scoped admins.
- **Customer self-service portal**: customers sign in with name + email and grant or withdraw
  consent purpose-by-purpose through a secure, token-authenticated consent center.
- **Integration API**: server-to-server context handoff secured by an API key.
- **Append-only audit log** with request correlation IDs and an audit explorer UI.

---

## Repository layout

```
backend/
  app/
    core/        config, database, security, rbac, request-id middleware, rate limiters
    models/      SQLAlchemy entities
    schemas/     Pydantic schemas
    api/         routes + auth deps
    services/    consent lifecycle, decision engine, audit
  alembic/       database migrations
  seed.py        demo data (users, roles, customers, purposes, policies, consents, audit)
  requirements.txt
frontend/
  src/
    api/         typed API client (axios)
    components/  layout, UI kit, icons
    context/     auth context
    pages/       landing, customer portal (login + consent center), admin login,
                 dashboard, customers, consent dashboard, consent detail,
                 audit explorer, purposes, policies, decision tester, administration
  vite.config.ts (proxies /api -> http://127.0.0.1:8000)
crm/, codex/, skilllearn/, portal/job-portal/
  Demo client sites exercising the integration APIs with their own cookie banners
  and branding - see the ports table above. Each has its own src/, stylesheets and
  vite.config.ts; they do not share code with each other or with frontend/.
```

---

## Prerequisites

- Python 3.10+
- Node.js 18+ (tested with Node 24 / npm 11)
- PostgreSQL running locally (any port; default setup uses **5433**)

---

## 1. Backend setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create the database (adjust port/credentials to your PostgreSQL instance):

```powershell
psql -h localhost -p 5433 -U postgres -c "CREATE DATABASE consent_platform;"
```

Configure the environment:

```powershell
Copy-Item .env.example .env
# edit .env: DATABASE_URL, JWT_SECRET, INTEGRATION_API_KEY, SEED_ADMIN_*
```

Apply migrations and seed demo data:

```powershell
python -m alembic upgrade head
python seed.py
```

Start the API:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

- Health check: http://127.0.0.1:8000/health
- Interactive API docs: http://127.0.0.1:8000/docs

### Backend environment variables (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg2://postgres:postgres@localhost:5433/consent_platform` | SQLAlchemy connection string |
| `JWT_SECRET` | *(random at generation)* | Signing secret for access + refresh + context tokens |
| `JWT_EXPIRE_MINUTES` | `60` | Access token lifetime |
| `REFRESH_EXPIRE_MINUTES` | `10080` | Refresh token lifetime |
| `CONTEXT_EXPIRE_MINUTES` | `15` | Integration context token lifetime |
| `INTEGRATION_API_KEY` | `dev-demo-integration-key-2026` | Legacy shared key; unbound to any tenant, so any caller with it can stamp any `source_app`. Prefer a tenant-bound `c360_...` key from `python seed_api_keys.py` instead |
| `ALLOW_LEGACY_INTEGRATION_KEY` | `false` | Whether `INTEGRATION_API_KEY` is accepted at all. Off by default; the backend logs a warning at startup whenever it is turned on |
| `SCHEDULER_ENABLED` | `false` | Runs the background job scheduler in-process: consent expiry (every 15 min), context-token cleanup + KPI snapshots (hourly), renewal reminders + audit-chain verification (daily). Each run writes a row to `scheduler_runs`. Off by default so `pytest` stays hermetic - set to `true` for any real deployment (including local dev, if you want consents to actually expire on their own) |
| `SEED_ADMIN_USERNAME` | `admin` | Username `python seed.py` creates the initial admin account under |
| `SEED_ADMIN_PASSWORD` | *(placeholder in `.env.example`; falls back to a published, well-known demo value in `app/core/config.py` if the variable is unset entirely — R-07)* | Password for that account. `seed.py` is a **demo** data loader that also creates several other staff accounts with published, guessable passwords (see "Demo accounts" below) — never point it at a production database. Set your own strong value here before running it anywhere real |

---

## 2. Frontend setup

```powershell
cd frontend
npm install
```

Run the dev server:

```powershell
npm run dev
```

Open http://localhost:8005 (see the ports table near the top of this file). The site opens on a
**landing page** with two entries:

- **Customer → Continue to Consent Center** (`/portal/login`): a name + email form that opens the
  customer's self-service consent center (`/portal/consent`).
- **Consent Management Admin → Admin Sign in** (`/login`): staff login for Admins and Consent Managers.

`/api` calls are proxied to the backend on port 8000.

Production build:

```powershell
npm run build
```

Optional: set `VITE_INTEGRATION_API_KEY` in `frontend/.env` if you changed the backend key.

---

## Demo accounts (development only — R2-12 / gap R-07)

There are currently **nine roles** — count them yourself with
`python -c "from app.core.rbac import ROLE_PERMISSIONS; print(len(ROLE_PERMISSIONS))"` from
`backend/` rather than trusting this number, since it has drifted before and `rbac.py` is the only
source of truth: `admin` (full access), `consent_manager` (manages customer consent and audit
trails day to day), `dpo` (oversees policy, purpose design and the compliance audit trail),
`auditor` (independent, read-only compliance review across all data and audit trails), `operator`
(day-to-day consent operations: customers, consents and their own audit trail), `viewer`
(read-only across the platform), and three org-scoped admin roles — `jobhub_admin`, `codex_admin`,
`skilllearn_admin` — each restricted to its own `source_app` via `ORG_SCOPE_MAP`. Running
`seed_org_roles.py` deletes `consent_manager` and moves its users to `viewer`, so the table below
reflects a fresh `seed.py` run before that script is used.

`python seed.py` creates several demo staff accounts under the `admin`, `consent_manager` and
`viewer` roles (not one-for-one with the full role list above), each with a `<Word>@1234`-style
password. `dpo`, `auditor` and `operator` are seeded as roles but have no demo user attached to
them here; the org-scoped roles get their demo accounts from `seed_org_roles.py` instead.
**Passwords are intentionally not printed here** — read them directly from `seed.py` (they are
plaintext there, since it is a demo-only script) rather than trusting a copy that can go stale. Do
not run `seed.py` against a database anyone outside your own machine can reach, and never run it
against a production database — every account it creates, and its password, is public information
the moment this repository is cloned.

---

## Customer self-service portal

Anyone can manage their own consent without a staff account:

1. On the landing page choose **Customer → Continue to Consent Center**.
2. Enter your **name and email** (no status, no staff login). The platform calls
   `POST /consent/customer-context` (secured by the integration API key), resolves a stable
   customer profile from the email and returns a short-lived context token, which is stored
   in the browser.
3. The **Consent Center** (`/portal/consent`) lists every active purpose with its legal basis,
   retention period and consent wording. Use the controls to **grant** or **withdraw** consent
   for each purpose. All changes are recorded in the audit trail with the customer as the actor.
4. **End session** clears the token; returning later with the same email shows the same profile.

Portal endpoints are public (no staff login) but are authenticated by the context token in the
`X-Context-Token` header, so only the identified customer can read or change their own consents.

---

## Integration flow (agent handoff)

Your existing business application can also hand a customer to a consent agent:

1. Call `POST /consent/customer-context` with the `X-API-Key` header:
   ```json
   {
     "name": "Aarav Patel",
     "email": "aarav@example.com",
     "phone": "+91-9000000001",
     "source_app": "CRM_APP"
   }
   ```
   If your application has its own customer id you can send it as `customer_id`. When it is
   omitted, the platform derives a **stable external id** from the email (or phone), so the
   same customer maps to the same consent profile on every visit.
2. The platform upserts the customer reference and returns a **short-lived context token**
   (15 minutes) plus a UI URL. No PII is placed in the URL.
3. The business app opens the returned UI URL, e.g. `http://localhost:8005/consent/context/<token>`
   (the admin console's actual port — see the note above about `CONSENT_PORTAL_URL`'s stale
   `:5173` default if the URL the backend returns doesn't match).
4. The platform resolves the context to the customer and shows the customer consent dashboard,
   where a **Consent Manager** agent grants/denies/withdraws consent on the customer's behalf.
   (Opening this dashboard requires a staff login; the context token alone cannot expose data.)

---

## Decision engine

The platform evaluates processing requests internally as part of the consent lifecycle, returning
one of:

| Decision | Meaning |
| --- | --- |
| `ALLOW` | A valid, in-force consent exists for the exact purpose/category/activity |
| `REQUIRE_CONSENT` | No consent recorded for this combination |
| `DENY` | Consent was explicitly denied |
| `WITHDRAWN` | Consent was withdrawn |
| `EXPIRED` | Consent exists but its validity period lapsed |

Every evaluation is written to the audit trail with the applicable purpose/policy versions. The
decision logic is driven by the rules defined on the **Consent Policies** page.

---

## Production deployment (R2-12 / gap R-07)

Everything above describes the **demo/dev setup**. Before pointing any of this at real data:

- Never run `python seed.py` against a production database — it creates multiple staff accounts
  with published passwords (see "Demo accounts" above) and sample customers/consents.
- Set your own `JWT_SECRET`, `FIELD_ENCRYPTION_KEY`, `SEED_ADMIN_PASSWORD` (if you do seed an
  initial admin) and a real, tenant-bound `INTEGRATION_API_KEY` per client — never enable
  `ALLOW_LEGACY_INTEGRATION_KEY`.
- There is currently no dedicated **production seed profile** — a loader that creates only
  reference data (roles, permissions, an initial tenant) with no demo users, sample customers or
  fixed passwords. Until one exists, provision the first real admin account by some other means
  (a one-off script you write and delete, or a manual DB insert with a hashed password you
  generate yourself) rather than adapting `seed.py`.

---

## Useful commands

```powershell
# new migration after model changes
cd backend
python -m alembic revision --autogenerate -m "<message>"
python -m alembic upgrade head

# re-seed demo data (idempotent; re-syncs system role permissions)
cd backend
python seed.py
```
