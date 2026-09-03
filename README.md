# Consent Management Platform (Consent360)

A standalone, locally-run consent management platform that integrates with an existing
Customer Management application. Customers are managed in your business application;
consent is managed here. The integration is secure, server-to-server, and never exposes
sensitive customer information in URLs.

Built with **FastAPI + PostgreSQL + Alembic** (backend) and **React + TypeScript + Vite** (frontend).

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
- **RBAC** with 2 roles and granular permissions: **Admin** (view-only oversight + manages
  purposes, rules and policy) and **Consent Manager** (manages customer consents and audit trails).
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
| `INTEGRATION_API_KEY` | `dev-demo-integration-key-2026` | Key the business app uses to call the integration API |
| `SEED_ADMIN_USERNAME` | `admin` | Seeded admin username |
| `SEED_ADMIN_PASSWORD` | *(random at generation)* | Seeded admin password — set via env; generated randomly under `ENVIRONMENT=production` |

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

Open http://localhost:5173. The site opens on a **landing page** with two entries:

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

## Demo accounts

A local/dev seed (`backend/seed.py`) creates demo accounts so the platform looks functional. In
production (`ENVIRONMENT=production`) the seed **structurally refuses** to create demo accounts or
demo data — it provisions only the single admin user with a randomly generated, must-rotate password.
Do not publish the demo credentials; run the seed locally if you want demo data.

| Username | Role |
| --- | --- |
| `admin` | Admin |
| `privacy.officer` | Admin |
| `data.steward` | Admin |
| `customer.service` | Consent Manager |
| `auditor` | Consent Manager |
| `readonly` | Consent Manager |

*The passwords for these local demo accounts are set inside `backend/seed.py` and are only seeded in
non-production environments.*

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
3. The business app opens `http://localhost:5173/consent/context/<token>`.
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
