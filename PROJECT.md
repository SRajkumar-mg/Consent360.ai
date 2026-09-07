# Consent Management Platform — Project Document

## 1. Overview

A data-consent management system with one admin platform and several demo client sites:

1. **Consent Management Platform (admin)** — central consent lifecycle, purpose/policy management, decision engine, audit & evidence trail for an organization's data processing.
2. **Demo client sites** (`crm`, `codex`, `skilllearn`, `portal/job-portal`) — sample customer-facing websites, each with its own branding and cookie banner, that exercise the consent platform's integration APIs. They stand in for any future third-party client website: clients integrate via the platform's APIs, not by touching consent data directly. `crm` is described in detail below as the representative example; the others follow the same pattern against their own `source_app`.

> **T-11: this table drifts out of date faster than this document gets updated.** Trust
> `docs/ARCHITECTURE.md` at the repo root over this section — it defers to each app's own `package.json`
> dev script / `vite.config.ts` for ports, and to `backend/app/core/rbac.py` for roles.

| Component | Tech | Port |
|---|---|---|
| Consent platform backend (`app.main:app`) | FastAPI + SQLAlchemy + PostgreSQL | **8000** |
| CRM backend, optional standalone (`app.crm_app`) — the frontends below don't need it | FastAPI, same `crm_directory` router `app.main` already mounts | **8001** |
| Consent admin frontend | React + TypeScript + Vite | **8005** |
| CRM demo client (cookie banner, `source_app=CRM_PORTAL`) | React + TypeScript + Vite | **8008** |
| Codex demo client (`source_app=CODEX`) | React + TypeScript + Vite | **5174** |
| SkillLearn demo client (`source_app=SKILLLEARN`) | React + TypeScript + Vite | **5175** |
| Job portal demo client (`source_app=CAREER_HUB`), no proxy — calls the backend directly | React 19 + Vite | **5180** |

## 2. Architecture

```
                          ┌─────────────────────────────┐
   Admin user (browser)   │  Consent Admin Frontend     │  :8005
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
   Customer (browser)     │ optional,   │  │                      │
   ────────────────────▶  │ standalone  │  │  CRM Demo Frontend   │  :8008
   (login: name + email)  │ app.crm_app │  │  React + Vite        │
                          │ :8001       │  └──────────────────────┘
                          └─────────────┘
```

Key rules:
- **Consent data lives only on the consent platform backend (:8000).** `app.crm_app` (:8001) is an
  optional, standalone copy of the same `crm_directory` router `app.main` already mounts — the CRM
  frontend's `/api` proxy, like every other demo site's, actually targets :8000 by default; :8001
  only matters if you deliberately run the separate service and repoint the proxy at it.
- The CRM popups (**Accept consent / More Options**) call the consent platform APIs — served on
  :8000, proxied by the CRM frontend via `/cmp`. Codex and SkillLearn follow the same `/api` + `/cmp`
  → :8000 pattern from their own ports (5174, 5175); the job portal (5180) has no proxy and calls
  :8000 directly from client code instead.
- Deleting a CRM customer asks the consent platform to purge that customer's consent profile through its integration API (`DELETE /crm/customers/by-email/{email}`, guarded by `X-API-Key`).
- All services share one PostgreSQL database (`consent_platform`).

## 3. Features

### 3.1 Consent Admin Portal (:8005)

**Landing / Login**
- Public landing page and staff login. `python seed.py` (demo/dev only — never run it against a
  production database) creates a demo `admin` account among several others; see README.md's
  "Demo accounts" section rather than a copy of the password here, which can and has gone stale.

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

### 3.2 CRM Portal (:8008)

- **Login** — accepts only name and email (all other fields removed).
- **Customer directory cards** — name/email cards; delete icon with confirmation.
- **Cookie-consent popup (Accept consent)** — center modal after login, with a language selector covering all 23 scheduled Indian languages (see section 7).
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
- Roles (source of truth: `backend/app/core/rbac.py` — this list has drifted before, see T-11):
  - `admin` — full view + manage purposes, categories, activities, policies, users.
  - `consent_manager` — dashboard, customers, consent view/manage, purpose/policy view, audit, context use.
    Removed by `seed_org_roles.py`, which reassigns its users to `viewer`.
  - `viewer` — read-only: dashboard, customers, consent, purpose, policy, audit (view/list only, no edits).
  - `jobhub_admin`, `codex_admin`, `skilllearn_admin` — org-scoped admin roles, each restricted to one
    `source_app` (JOBHUB, CODEX, SKILLLEARN respectively) via `ORG_SCOPE_MAP`.

## 7. Multilingual Support

- CRM popups: 23 scheduled Indian languages catalogued in `crm/src/languages.ts`, all enabled —
  banner copy, category descriptions, and "Accept consent" / "Consent preferences" labels are
  translated. Codex and SkillLearn ship their own, separate language lists.
- Purposes store translations for the same languages (en = default fields).

## 8. Running the Project

Backend (from `backend/`):

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app    --host 127.0.0.1 --port 8000   # consent platform - the only backend the frontends need
.venv\Scripts\python.exe -m uvicorn app.crm_app:app --host 127.0.0.1 --port 8001   # optional standalone CRM directory
```

Frontends (each `npm run dev` uses the port in that app's own `package.json` dev script / `vite.config.ts` — see the table in section 1):

```powershell
npm run dev          # from frontend/            -> :8005, admin console
npm run dev          # from crm/                 -> :8008, proxies /api, /cmp -> :8000
npm run dev          # from codex/                -> :5174, proxies /api, /cmp -> :8000
npm run dev          # from skilllearn/            -> :5175, proxies /api, /cmp -> :8000
npm run dev          # from portal/job-portal/     -> :5180, no proxy, calls :8000 directly
```

- PostgreSQL database `consent_platform`; connection settings in `backend/.env` (see `app/core/config.py` for all settings: JWT secret, integration API key, cross-app URLs, `CONSENT_API_URL`).
- Demo data (an admin user among several other staff accounts, purposes, policies, sample
  customers/consents) is applied via `backend/seed.py` — development only, never against a
  production database (see README.md's "Production deployment" section; there is currently no
  production-safe seed profile).
- Health checks: `GET /health` on both backends.

## 9. Tech Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2, Pydantic v2, psycopg2, PostgreSQL
- **Frontend:** React 18, TypeScript, Vite 6, Axios, react-router
- **Security:** JWT, bcrypt, RBAC, API keys, request correlation IDs, rate limiting on context creation
