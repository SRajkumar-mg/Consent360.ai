# Consent Management Platform — Project Document

## 1. Overview

A two-part data-consent management system:

1. **Consent Management Platform (admin)** — central consent lifecycle, purpose/policy management, decision engine, audit & evidence trail for an organization's data processing.
2. **CRM Portal (demo client)** — a sample customer-facing website that exercises the consent platform's APIs through cookie-consent popups. It stands in for any future third-party client website: clients integrate via the platform's APIs, not by touching consent data directly.

| Component | Tech | Port |
|---|---|---|
| Consent platform backend | FastAPI + SQLAlchemy + PostgreSQL | **8000** |
| CRM backend (customer directory only) | FastAPI (separate app `app.crm_app`) | **8001** |
| Consent admin frontend | React + TypeScript + Vite | **5173** |
| CRM portal frontend | React + TypeScript + Vite | **8005** |

## 2. Architecture

```
                          ┌─────────────────────────────┐
   Admin user (browser)   │  Consent Admin Frontend     │  :5173
   ─────────────────────▶ │  React + Vite               │
                          └──────────────┬──────────────┘
                                         │ /api (REST + JWT)
                          ┌──────────────▼──────────────┐
                          │  CONSENT PLATFORM BACKEND   │  :8000
                          │  app.main:app               │
                          │  · purposes, policies,      │
                          │    consents, customers,     │
                          │    audit, dashboard, admin  │
                          │  · POPUP APIs:              │
                          │    consent-preferences,     │
                          │    consent-context          │
                          │  · integration APIs         │
                          │    (X-API-Key protected)    │
                          └──────┬──────────────┬───────┘
                                 │              │
                 /api (directory)│              │ /cmp (popup/consent APIs)
                          ┌──────▼──────┐  ┌────▼──────────────────┐
   Customer (browser)     │ CRM BACKEND │  │                      │
   ────────────────────▶  │ :8001       │  │  CRM Portal Frontend │  :8005
   (login: name + email)  │ directory   │  │  React + Vite        │
                          │ login/list/ │  └──────────────────────┘
                          │ delete      │
                          └─────────────┘
```

Key rules:
- **Consent data lives only on the consent platform backend (:8000).** The CRM backend (:8001) maintains only the client's customer directory.
- The CRM popups (**Accept consent / More Options**) call the consent platform APIs — served on :8000, proxied by the CRM frontend via `/cmp`.
- Deleting a CRM customer asks the consent platform to purge that customer's consent profile through its integration API (`DELETE /crm/customers/by-email/{email}`, guarded by `X-API-Key`).
- All services share one PostgreSQL database (`consent_platform`).

## 3. Features

### 3.1 Consent Admin Portal (:5173)

**Landing / Login**
- Public landing page and staff login. Local dev accounts are seeded by `backend/seed.py`
  (not in production); the admin login is created from `SEED_ADMIN_USERNAME` / `SEED_ADMIN_PASSWORD`
  env vars, or a random must-rotate credential under `ENVIRONMENT=production`.

**Dashboard (Overview)**
- Metric cards: total customers, consents by status (active/pending/denied/withdrawn/expired/expiring ≤30 days), purposes & policies count.
- Status distribution, purpose distribution, recent consent activity, recent audit events, expiring consents list.

**Customers**
- Searchable customer list; each customer has a consent profile view.
- **Customer Consent profile**: consent matrix per purpose × data category × processing activity with badges; per-row actions — Grant, Deny, Withdraw, Renew, Request; "Withdraw all" and "Grant" per purpose; expiring-consent warnings; consent detail drill-down (history + evidence).

**Consent Detail**
- Full lifecycle history (status transitions with reasons, actors, versions) and evidence records per consent.

**Consent Purposes**
- Purposes define *why* data is processed. Create/edit purposes with legal basis, retention period, data categories, processing activities, consent text, and 6-language translations (English + Tamil, Hindi, Kannada, Malayalam, Telugu).
- Versioned: editing creates a new purpose version; historical consents stay linked to their version.
- Built-in purposes (cookie categories): `strictly_necessary` (legitimate interest, consent not required), `functional`, `analytics`, `advertising` (consent required).

**Policies**
- Policies define decision rules: one rule per purpose × data category × processing activity with decision (ALLOW / DENY), active-consent requirement, priority; plus a default decision (ALLOW / DENY / REQUIRE_CONSENT).
- Create a policy for any purpose already defined on the Purposes page; edit (auto-versioned) and delete (hard delete, or automatic *retire* if consent records reference it).

**Audit Explorer**
- Searchable audit trail with filters: customer (name dropdown), purpose, date range; shows event, customer, from→to status, version, request ID; detail view with reason/metadata.

**Administration**
- Users & roles management (create/update staff users, assign roles).

**Consent context landing**
- Short-lived context-token pages for customer-facing consent flows (minted via integration API).

### 3.2 CRM Portal (:8005)

- **Login** — accepts only name and email (all other fields removed).
- **Customer directory cards** — name/email cards; delete icon with confirmation.
- **Cookie-consent popup (Accept consent)** — center modal after login, with language selector (23 languages defined, 6 enabled: English, Tamil, Hindi, Kannada, Malayalam, Telugu).
- **More Options (Consent preferences)** — toggle per cookie category (strictly necessary [locked], functional, analytics, advertising); language choice.
- **Per-customer persistence** — preferences are stored per customer on the consent platform, so each customer's popup reopens with their own saved choices, and the admin portal reflects the same grant/withdraw decisions.
- **Manage Consent** — opens the consent popup for any customer card.

### 3.3 Consent Platform APIs (:8000) — "the APIs we give to clients"

- `POST /consent/customer-context` — identify-or-create a customer and mint a short-lived consent-context token (API-key protected).
- `GET /consent/context/consume/{token}`, `GET /consent/context/status/{token}` — consume/check context tokens.
- `GET|PUT /crm/customers/{id}/consent-preferences` — the popup APIs: read/save cookie-category preferences; saving mirrors choices onto consent records (grant/activate or withdraw per purpose) so the admin view stays in sync.
- `POST /crm/customers/{id}/consent-context` — mint a context for an existing CRM customer.
- `DELETE /crm/customers/by-email/{email}` — purge a customer's full consent profile (API-key protected), used by the CRM backend on customer deletion.

## 4. Domain Model

Tables: `Role`, `User`, `Customer` (consent platform identity), `CrmCustomer` (CRM directory), `DataCategory`, `ProcessingActivity`, `Purpose` + `PurposeVersion` (translations, categories, activities, consent text), `Policy` + `PolicyVersion` (rule sets, default decision), `Consent`, `ConsentHistory`, `ConsentEvidence`, `ConsentContext`, `ConsentDecisionLog`, `AuditLog`.

- Customers are identified by an email-derived external ID (`CUST-<hash>`); identity lookup is email-first to avoid duplicate profiles.
- Consent rows are keyed by customer × purpose × data category × processing activity, with version links to the purpose & policy versions in force.

## 5. Consent Lifecycle

```
NOT_REQUESTED → REQUESTED → PENDING → GRANTED → ACTIVE
                    │                      │
                    ├─→ DENIED             └─→ WITHDRAWN / EXPIRED / RENEWED / UPDATED
```
- Every transition is validated, recorded in `ConsentHistory`, backed by `ConsentEvidence` (evidence ref), and written to `AuditLog`.
- The **decision engine** evaluates policy rules (ALLOW/DENY) against purpose × data-category × activity with priority ordering; consents without active status fall back to the policy default decision (REQUIRE_CONSENT / ALLOW / DENY).

## 6. Authentication & RBAC

- JWT (access + refresh) for staff; bcrypt password hashing.
- Context tokens (short-lived, purpose-bound) for customer-facing flows.
- Integration API key (`X-API-Key`) for server-to-server APIs.
- Roles:
  - `admin` — full view + manage purposes, categories, activities, policies, users.
  - `consent_manager` — dashboard, customers, consent view/manage, purpose/policy view, audit, context use.
  - `viewer` — read-only: dashboard, customers, consent, purpose, policy, audit (view/list only, no edits).

## 7. Multilingual Support

- CRM popups: 23 languages catalogued; **en, ta, hi, kn, ml, te** enabled — banner copy, category descriptions, and "Accept consent" / "Consent preferences" labels are translated.
- Purposes store translations for the same languages (en = default fields).

## 8. Running the Project

Backend (from `backend/`):

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app    --host 127.0.0.1 --port 8000   # consent platform
.venv\Scripts\python.exe -m uvicorn app.crm_app:app --host 127.0.0.1 --port 8001   # CRM directory
```

Frontends:

```powershell
# consent admin (:5173)
npm run dev          # from frontend/

# CRM portal (:8005) — proxies /api → :8001 and /cmp → :8000
npm run dev          # from crm/
```

- PostgreSQL database `consent_platform`; connection settings in `backend/.env` (see `app/core/config.py` for all settings: JWT secret, integration API key, cross-app URLs, `CONSENT_API_URL`).
- Seed data (admin user, purposes, policies, sample customers/consents) is applied via `backend/seed.py`.
- Health checks: `GET /health` on both backends.

## 9. Tech Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2, Pydantic v2, psycopg2, PostgreSQL
- **Frontend:** React 18, TypeScript, Vite 6, Axios, react-router
- **Security:** JWT, bcrypt, RBAC, API keys, request correlation IDs, rate limiting on context creation
