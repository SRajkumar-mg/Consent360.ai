import { useState } from 'react'
import { Accordion, CodeBlock, FooterRow, PageHeading, formatDate } from '../components/ui'

interface Endpoint {
  method: 'POST' | 'GET' | 'PUT' | 'DELETE'
  path: string
  auth: string
  authBadge: string
  title: string
  description: string
  requestBody?: { field: string; type: string; required: boolean; desc: string }[]
  responseBody: string
  curlExample: string
  pythonSdk: string
  jsSdk: string
}

// Never fall back to a real working secret here — this page's whole purpose is
// to display example values, and a fallback previously baked the actual dev
// integration key into the public bundle. Show an obvious placeholder instead
// when the env var is not configured for this build, so it's visibly wrong
// rather than silently a live credential.
const API_KEY = (import.meta.env.VITE_INTEGRATION_API_KEY as string) || '<YOUR_INTEGRATION_API_KEY>'

const ENDPOINTS: Endpoint[] = [
  {
    method: 'POST',
    path: '/consent/customer-context',
    auth: 'X-API-Key',
    authBadge: 'api-key',
    title: 'Create Customer Context',
    description: 'Create or look up a customer on the consent platform and receive a short-lived context token (15 min) for self-service consent management. The token is a JWT — no PII is placed in the URL.',
    requestBody: [
      { field: 'name', type: 'string', required: true, desc: 'Customer name' },
      { field: 'email', type: 'string', required: false, desc: 'Email (used for dedup lookup)' },
      { field: 'phone', type: 'string', required: false, desc: 'Phone (used for dedup lookup)' },
      { field: 'customer_id', type: 'string', required: false, desc: 'External ID from your system' },
      { field: 'source_app', type: 'string', required: false, desc: 'Source application identifier' },
    ],
    responseBody: `{
  "context_token": "eyJhbGci...",
  "context_id": 42,
  "customer_id": "CUST-a1b2c3d4",
  "name": "Aarav Patel",
  "expires_in_minutes": 15,
  "source_app": "MY_CRM",
  "ui_url": "/consent/context/eyJhbGci...",
  "request_id": "req-abc123"
}`,
    curlExample: `curl -X POST http://localhost:8000/consent/customer-context \\
  -H "X-API-Key: ${API_KEY}" \\
  -H "Content-Type: application/json" \\
  -d '{"name": "Aarav Patel", "email": "aarav@example.com", "source_app": "MY_CRM"}'`,
    pythonSdk: `from consent360 import Consent360

client = Consent360(api_key="${API_KEY}", base_url="http://localhost:8000")
ctx = client.create_customer_context(name="Aarav Patel", email="aarav@example.com", source_app="MY_CRM")
print(ctx.context_token)  # JWT token
print(ctx.ui_url)         # Open in browser`,
    jsSdk: `import { Consent360 } from '@consent360/sdk';

const client = new Consent360({ apiKey: '${API_KEY}', baseUrl: 'http://localhost:8000' });
const ctx = await client.createCustomerContext({ name: 'Aarav Patel', email: 'aarav@example.com', source_app: 'MY_CRM' });
console.log(ctx.context_token);
console.log(ctx.ui_url);`,
  },
  {
    method: 'GET',
    path: '/consent/context/status/{token}',
    auth: 'X-API-Key or Bearer JWT',
    authBadge: 'api-key',
    title: 'Check Context Status',
    description: 'Check whether a context token is still valid, has been consumed, or has expired. R3-11: this now accepts the SAME integration API key used to mint the context in the first place — the SDK method below always sent X-API-Key, but the endpoint used to require a staff bearer token instead, so this call 401’d for every third-party caller. A tenant-bound key only ever resolves a context that belongs to its own tenant (a mismatch reads as 404, same as a purge for someone else’s customer). A staff bearer token with the context.use permission still works too, unchanged, for the admin console.',
    responseBody: `{
  "message": "VALID"   // or "CONSUMED" | "EXPIRED"
}`,
    curlExample: `curl http://localhost:8000/consent/context/status/eyJhbGci... \\
  -H "X-API-Key: ${API_KEY}"`,
    pythonSdk: `status = client.get_context_status("eyJhbGci...")
print(status.status)  # "VALID" | "CONSUMED" | "EXPIRED"`,
    jsSdk: `const status = await client.getContextStatus('eyJhbGci...');
console.log(status.status);  // "VALID" | "CONSUMED" | "EXPIRED"`,
  },
  {
    method: 'DELETE',
    path: '/crm/customers/by-email/{email}',
    auth: 'X-API-Key',
    authBadge: 'api-key',
    title: 'Purge Customer Data',
    description: 'Delete ALL consent-platform records for a customer. Cascading deletes: audit logs, evidence, history, consents, contexts, and the customer record. Used by CRM directory services when a customer is deleted.',
    responseBody: `{
  "deleted": true,
  "external_id": "CUST-a1b2c3d4"
}`,
    curlExample: `curl -X DELETE "http://localhost:8000/crm/customers/by-email/aarav%40example.com" \\
  -H "X-API-Key: ${API_KEY}"`,
    pythonSdk: `purge = client.purge_customer(email="aarav@example.com")
print(f"Deleted: {purge.deleted}, ID: {purge.external_id}")`,
    jsSdk: `const result = await client.purgeCustomer('aarav@example.com');
console.log(result.deleted);  // true`,
  },
  {
    method: 'GET',
    path: '/portal/overview',
    auth: 'X-Context-Token',
    authBadge: 'context',
    title: 'Portal Overview',
    description: 'Return all purposes and their consent status for the authenticated customer. Shows GRANTED / PARTIAL / NOT_GRANTED per purpose with granted/total counts. IMPORTANT: read re_consent_required, not status, to decide whether to prompt — a consent flagged by a material change still reports GRANTED here while the decision engine refuses to rely on it.',
    responseBody: `{
  "customer": { "id": 1, "external_id": "CUST-...", "name": "..." },
  "source_app": "MY_CRM",
  "purposes": [
    {
      "code": "analytics",
      "name": "Analytics",
      "status": "GRANTED",
      "granted_count": 3,
      "total_count": 3,
      "legal_basis": "CONSENT",
      "requires_consent": true,
      "retention_period_days": 365,
      "purpose_version_number": 2,

      // R1-09/R2-11: this purpose changed materially. Processing under it is
      // already blocked; prompt the principal and send her answer to
      // /portal/grant (re-affirm) or /portal/withdraw (refuse).
      "re_consent_required": false,
      "re_consent_count": 0,
      "re_consent_requested_at": null,
      "re_consent_campaign_ref": null,
      "re_consent_reason": ""
    }
  ]
}`,
    curlExample: `curl http://localhost:8000/portal/overview \\
  -H "X-Context-Token: <context_token>"`,
    pythonSdk: `overview = client.get_portal_overview(context_token="eyJhbGci...")
for p in overview.purposes:
    print(f"{p.name}: {p.status} ({p.granted_count}/{p.total_count})")`,
    jsSdk: `const overview = await client.getPortalOverview('eyJhbGci...');
overview.purposes.forEach(p => {
  console.log(p.name + ': ' + p.status + ' (' + p.granted_count + '/' + p.total_count + ')');
});`,
  },
  {
    method: 'POST',
    path: '/portal/grant',
    auth: 'X-Context-Token',
    authBadge: 'context',
    title: 'Grant Consent',
    description: 'Grant consent for a specific purpose. Activates all consent records for the given purpose across all data categories and processing activities. Also the re-affirm path after a material change: a record flagged re_consent_required is renewed here (GRANTED is not a legal transition out of UPDATED), which is what lifts the decision engine block.',
    requestBody: [
      { field: 'purpose_code', type: 'string', required: true, desc: 'Purpose code (e.g. "analytics", "advertising")' },
    ],
    responseBody: `{
  "purpose_code": "analytics",
  "action": "granted",
  "affected": 3,
  "message": "Granted consent for Analytics (3 records)"
}`,
    curlExample: `curl -X POST http://localhost:8000/portal/grant \\
  -H "X-Context-Token: <context_token>" \\
  -H "Content-Type: application/json" \\
  -d '{"purpose_code": "analytics"}'`,
    pythonSdk: `result = client.grant_consent(context_token, purpose_code="analytics")
print(result.message)  # "Granted consent for Analytics (3 records)"`,
    jsSdk: `const result = await client.grantConsent('eyJhbGci...', 'analytics');
console.log(result.message);`,
  },
  {
    method: 'POST',
    path: '/portal/withdraw',
    auth: 'X-Context-Token',
    authBadge: 'context',
    title: 'Withdraw Consent',
    description: 'Withdraw consent for a specific purpose. Deactivates all active consent records for the given purpose.',
    requestBody: [
      { field: 'purpose_code', type: 'string', required: true, desc: 'Purpose code (e.g. "analytics", "advertising")' },
    ],
    responseBody: `{
  "purpose_code": "advertising",
  "action": "withdrawn",
  "affected": 2,
  "message": "Withdrawn consent for Advertising (2 records)"
}`,
    curlExample: `curl -X POST http://localhost:8000/portal/withdraw \\
  -H "X-Context-Token: <context_token>" \\
  -H "Content-Type: application/json" \\
  -d '{"purpose_code": "advertising"}'`,
    pythonSdk: `result = client.withdraw_consent(context_token, purpose_code="advertising")
print(result.message)  # "Withdrawn consent for Advertising (2 records)"`,
    jsSdk: `const result = await client.withdrawConsent('eyJhbGci...', 'advertising');
console.log(result.message);`,
  },
  {
    method: 'POST',
    path: '/decisions/evaluate',
    auth: 'X-API-Key',
    authBadge: 'api-key',
    title: 'Evaluate a Consent Decision',
    description: 'New: a fiduciary system asking "may I process this, right now?" before it acts, rather than trusting a cached status. Runs the same decision engine the platform uses internally (explicit DENY rule → consent status → rule/purpose exemptions → REQUIRE_CONSENT) and returns a plain boolean alongside the verdict. Every call writes a consent_decision_logs row and an audit entry, so "we checked before processing" is evidenced on our side. Requires the decision.evaluate API key scope — not yet wrapped by either SDK, call it directly. Identifiers are the codes/external ids your system already knows, never this platform\'s internal row ids.',
    requestBody: [
      { field: 'customer_id', type: 'string', required: true, desc: "The principal's external id at your system" },
      { field: 'purpose_code', type: 'string', required: true, desc: 'Purpose code (e.g. "analytics")' },
      { field: 'data_category_code', type: 'string', required: true, desc: 'Data category code' },
      { field: 'processing_activity_code', type: 'string', required: true, desc: 'Processing activity code' },
      { field: 'source_app', type: 'string', required: false, desc: 'Defaults to the tenant bound to the API key' },
    ],
    responseBody: `{
  "decision": "ALLOW",
  "allowed": true,
  "reason": "Consent GRANTED",
  "customer_id": "CUST-a1b2c3d4",
  "purpose_code": "analytics",
  "data_category_code": "usage_data",
  "processing_activity_code": "product_analytics",
  "source_app": "MY_CRM",
  "consent_status": "GRANTED",
  "consent_version": 3,
  "decision_log_id": 4821,
  "evaluated_at": "2026-09-04T02:10:00Z",
  "duration_ms": 4
}`,
    curlExample: `curl -X POST http://localhost:8000/decisions/evaluate \\
  -H "X-API-Key: ${API_KEY}" \\
  -H "Content-Type: application/json" \\
  -d '{"customer_id": "CUST-a1b2c3d4", "purpose_code": "analytics", "data_category_code": "usage_data", "processing_activity_code": "product_analytics"}'`,
    pythonSdk: `# Not yet wrapped by consent360 — call the HTTP API directly.
import requests

resp = requests.post(
    "http://localhost:8000/decisions/evaluate",
    headers={"X-API-Key": "${API_KEY}"},
    json={
        "customer_id": "CUST-a1b2c3d4",
        "purpose_code": "analytics",
        "data_category_code": "usage_data",
        "processing_activity_code": "product_analytics",
    },
)
print(resp.json()["allowed"])`,
    jsSdk: `// Not yet wrapped by @consent360/sdk — call the HTTP API directly.
const resp = await fetch('http://localhost:8000/decisions/evaluate', {
  method: 'POST',
  headers: { 'X-API-Key': '${API_KEY}', 'Content-Type': 'application/json' },
  body: JSON.stringify({
    customer_id: 'CUST-a1b2c3d4',
    purpose_code: 'analytics',
    data_category_code: 'usage_data',
    processing_activity_code: 'product_analytics',
  }),
});
const decision = await resp.json();
console.log(decision.allowed);`,
  },
  {
    method: 'GET',
    path: '/portal/notifications',
    auth: 'X-Context-Token',
    authBadge: 'context',
    title: 'List My Notifications',
    description: 'New: the notifications (renewal reminders, breach notices, grievance updates) sent to the authenticated customer, most recent first. Pairs with Portal Overview / Grant / Withdraw above — same X-Context-Token credential, same customer. Not yet wrapped by either SDK.',
    responseBody: `[
  {
    "id": 512,
    "event_type": "CONSENT_RENEWAL_REMINDER",
    "channel": "EMAIL",
    "language": "en",
    "subject": "Your consent for Analytics is expiring soon",
    "status": "DELIVERED",
    "retry_count": 0,
    "source_app": "MY_CRM",
    "sent_at": "2026-09-01T09:00:00Z",
    "acknowledged_at": null,
    "created_at": "2026-09-01T09:00:00Z"
  }
]`,
    curlExample: `curl http://localhost:8000/portal/notifications \\
  -H "X-Context-Token: <context_token>"`,
    pythonSdk: `# Not yet wrapped by consent360 — call the HTTP API directly.
import requests

notes = requests.get(
    "http://localhost:8000/portal/notifications",
    headers={"X-Context-Token": context_token},
).json()`,
    jsSdk: `// Not yet wrapped by @consent360/sdk — call the HTTP API directly.
const notes = await fetch('http://localhost:8000/portal/notifications', {
  headers: { 'X-Context-Token': contextToken },
}).then(r => r.json());`,
  },
  {
    method: 'POST',
    path: '/portal/notifications/{notification_id}/acknowledge',
    auth: 'X-Context-Token',
    authBadge: 'context',
    title: 'Acknowledge a Notification',
    description: 'New: mark one of the customer\'s own notifications (from the list above) as read/acknowledged.',
    responseBody: `{
  "id": 512,
  "status": "DELIVERED",
  "acknowledged_at": "2026-09-04T02:15:00Z",
  "...": "rest of the notification, unchanged"
}`,
    curlExample: `curl -X POST http://localhost:8000/portal/notifications/512/acknowledge \\
  -H "X-Context-Token: <context_token>"`,
    pythonSdk: `# Not yet wrapped by consent360 — call the HTTP API directly.
import requests

requests.post(
    "http://localhost:8000/portal/notifications/512/acknowledge",
    headers={"X-Context-Token": context_token},
)`,
    jsSdk: `// Not yet wrapped by @consent360/sdk — call the HTTP API directly.
await fetch('http://localhost:8000/portal/notifications/512/acknowledge', {
  method: 'POST',
  headers: { 'X-Context-Token': contextToken },
});`,
  },
]

type Tab = 'curl' | 'python' | 'javascript'

function EndpointBody({ ep }: { ep: Endpoint }) {
  const [tab, setTab] = useState<Tab>('curl')

  return (
    <div>
      <h4>{ep.title}</h4>
      <p className="text-sm text-secondary">{ep.description}</p>

      {ep.requestBody && (
        <>
          <h5 style={{ fontSize: 13, fontWeight: 700, margin: '0 0 8px', color: 'var(--text)' }}>Request Body</h5>
          <div className="table-wrap mb">
            <table className="table">
              <thead>
                <tr>
                  <th>Field</th>
                  <th>Type</th>
                  <th>Required</th>
                  <th>Description</th>
                </tr>
              </thead>
              <tbody>
                {ep.requestBody.map((f) => (
                  <tr key={f.field}>
                    <td><code>{f.field}</code></td>
                    <td><span className="badge b-info">{f.type}</span></td>
                    <td>{f.required ? <span style={{ color: 'var(--danger)', fontWeight: 600 }}>Yes</span> : 'No'}</td>
                    <td>{f.desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <div className="tabs">
        {(['curl', 'python', 'javascript'] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            className={`tab${tab === t ? ' active' : ''}`}
            onClick={() => setTab(t)}
          >
            {t === 'curl' ? 'cURL' : t === 'python' ? 'Python' : 'JavaScript'}
          </button>
        ))}
      </div>
      <CodeBlock
        code={tab === 'curl' ? ep.curlExample : tab === 'python' ? ep.pythonSdk : ep.jsSdk}
        language={tab === 'curl' ? 'bash' : tab}
      />

      <div className="chip-group-title">Response (200 OK)</div>
      <CodeBlock code={ep.responseBody} language="json" />
    </div>
  )
}

export function ApiReferencePage() {
  const [filter, setFilter] = useState<'all' | 'api-key' | 'jwt' | 'context'>('all')

  const filtered = filter === 'all' ? ENDPOINTS : ENDPOINTS.filter((ep) => ep.authBadge === filter)

  return (
    <div>
      <PageHeading
        title="API & SDKs"
        subtitle="Integration APIs for third-party applications and CRM systems"
      />

      <div className="grid-2 mb">
        <div className="card">
          <div className="card-body">
            <div className="flex" style={{ gap: 12, marginBottom: 12 }}>
              <div style={{ width: 36, height: 36, borderRadius: 10, background: 'var(--primary-soft)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--primary)', fontWeight: 700, fontSize: 14 }}>Py</div>
              <div>
                <div style={{ fontWeight: 700 }}>Python SDK</div>
                <div className="text-sm text-secondary">Zero dependencies, Python 3.10+</div>
              </div>
            </div>
            <CodeBlock
              code={`pip install consent360

from consent360 import Consent360

client = Consent360(api_key="YOUR_KEY", base_url="http://localhost:8000")
ctx = client.create_customer_context(name="John", email="john@example.com")`}
              language="python"
            />
          </div>
        </div>

        <div className="card">
          <div className="card-body">
            <div className="flex" style={{ gap: 12, marginBottom: 12 }}>
              <div style={{ width: 36, height: 36, borderRadius: 10, background: 'var(--warning-soft)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--warning)', fontWeight: 700, fontSize: 14 }}>JS</div>
              <div>
                <div style={{ fontWeight: 700 }}>JavaScript / TypeScript SDK</div>
                <div className="text-sm text-secondary">Zero dependencies, Node 18+ & browsers</div>
              </div>
            </div>
            <CodeBlock
              code={`npm install @consent360/sdk

import { Consent360 } from '@consent360/sdk';

const client = new Consent360({ apiKey: 'YOUR_KEY', baseUrl: 'http://localhost:8000' });
const ctx = await client.createCustomerContext({ name: 'John', email: 'john@example.com' });`}
              language="typescript"
            />
          </div>
        </div>
      </div>

      <div className="filter-chips mb">
        {([
          { key: 'all', label: 'All APIs' },
          { key: 'api-key', label: 'X-API-Key' },
          { key: 'jwt', label: 'Bearer JWT' },
          { key: 'context', label: 'Context Token' },
        ] as const).map((f) => (
          <button
            key={f.key}
            type="button"
            className={`filter-chip${filter === f.key ? ' active' : ''}`}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>

      <Accordion
        items={filtered.map((ep) => ({
          id: ep.method + ep.path,
          header: (
            <div className="flex" style={{ flex: 1, gap: 12, minWidth: 0 }}>
              <span className={`method-pill method-${ep.method}`}>{ep.method}</span>
              <code className="mono">{ep.path}</code>
              <span className="badge b-primary" style={{ marginLeft: 'auto' }}>{ep.auth}</span>
            </div>
          ),
          content: <EndpointBody ep={ep} />,
        }))}
      />

      <div className="card mb" style={{ marginTop: 24 }}>
        <div className="card-body">
          <h4 style={{ marginTop: 0 }}>Recently added (staff console APIs)</h4>
          <p className="text-sm text-secondary">
            These landed alongside the R3 hardening round. They authenticate with a <strong>staff bearer JWT</strong> and
            a specific permission (see <code>app/core/rbac.py</code>) rather than the integration API key or context
            token above, so they are not part of the third-party SDK surface documented in detail on this page — full
            request/response schemas are in the live <a href="/docs" target="_blank" rel="noreferrer">OpenAPI docs</a>.
            Two exceptions are already covered above because they DO take a context token or API key: Portal
            Notifications and <code>/decisions/evaluate</code>.
          </p>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Area</th>
                  <th>Base path</th>
                  <th>Self-service (context token)</th>
                  <th>What it's for</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Consent Manager interoperability</td>
                  <td><code>/consent-manager/*</code></td>
                  <td><code>/consent-manager/artefacts*</code> take the integration API key (<code>artefact.read</code> / <code>artefact.write</code>) instead</td>
                  <td>DPDP Consent Manager registration, fiduciary onboarding, and consent artefact issuance/withdrawal</td>
                </tr>
                <tr>
                  <td>Breach register</td>
                  <td><code>/breaches/*</code></td>
                  <td>—</td>
                  <td>Rule 7 breach lifecycle: detection, affected principals, Board/CERT-In filings, the three statutory clocks</td>
                </tr>
                <tr>
                  <td>Grievance register</td>
                  <td><code>/grievances/*</code></td>
                  <td><code>/grievances/me*</code></td>
                  <td>Principal grievances: filing, staff triage, escalation, resolution, feedback</td>
                </tr>
                <tr>
                  <td>Notifications (admin)</td>
                  <td><code>/notifications/*</code></td>
                  <td><code>/portal/notifications*</code> instead (documented above)</td>
                  <td>Delivery queue, templates, manual triggers, delivery metrics</td>
                </tr>
                <tr>
                  <td>Consent receipts</td>
                  <td><code>/receipts/*</code></td>
                  <td><code>/receipts/me*</code></td>
                  <td>Immutable, signed proof-of-consent records</td>
                </tr>
                <tr>
                  <td>Sharing events &amp; objections</td>
                  <td><code>/sharing-events/*</code>, <code>/objections/*</code></td>
                  <td><code>/objections/me*</code></td>
                  <td>Third-party data-sharing disclosures and principal objections to processing</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <FooterRow left={`Directory active: ${formatDate(new Date().toISOString())}`} />
    </div>
  )
}
