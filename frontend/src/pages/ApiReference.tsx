import { useState } from 'react'
import { PageHead } from '../components/ui'

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

const API_KEY = import.meta.env.VITE_INTEGRATION_API_KEY as string || ''

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
const ctx = await client.createCustomerContext({ name: 'Aarav Patel', email: 'aarav@example.com', sourceApp: 'MY_CRM' });
console.log(ctx.context_token);
console.log(ctx.ui_url);`,
  },
{
    method: 'GET',
    path: '/consent/context/status/{token}',
    auth: 'X-API-Key',
    authBadge: 'api-key',
    title: 'Check Context Status',
    description: 'Check whether a context token is still valid, has been consumed, or has expired. Requires a tenant-bound integration API key (see R3-01). The API key is tenant-scoped and scoped to the customer\'s tenant.',
    responseBody: `{
  "message": "VALID"   // or "CONSUMED" | "EXPIRED"
}`,
    curlExample: `curl http://localhost:8000/consent/context/status/eyJhbGci... \\
  -H "X-API-Key: <tenant_api_key>"`,
    pythonSdk: `status = client.get_context_status("eyJhbGci...")
print(status.status)  # "VALID" | "CONSUMED" | "EXPIRED"`,
    jsSdk: `const status = await client.getContextStatus('eyJhbGci...');
console.log(status.status);  # "VALID" | "CONSUMED" | "EXPIRED"`,
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
    description: 'Return all purposes and their consent status for the authenticated customer. Shows GRANTED / PARTIAL / NOT_GRANTED per purpose with granted/total counts.',
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
      "retention_period_days": 365
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
    description: 'Grant consent for a specific purpose. Activates all consent records for the given purpose across all data categories and processing activities.',
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
]

const methodColors: Record<string, string> = {
  POST: 'var(--success)',
  GET: 'var(--primary)',
  PUT: 'var(--warning)',
  DELETE: 'var(--danger)',
}

const authBadgeColors: Record<string, string> = {
  'api-key': 'var(--primary)',
  jwt: 'var(--purple)',
  context: 'var(--info)',
}

type Tab = 'curl' | 'python' | 'javascript'

function EndpointCard({ ep }: { ep: Endpoint }) {
  const [tab, setTab] = useState<Tab>('curl')
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div
        className="card-header"
        style={{ cursor: 'pointer', userSelect: 'none' }}
        onClick={() => setExpanded(!expanded)}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1, minWidth: 0 }}>
          <span
            style={{
              background: methodColors[ep.method] || '#666',
              color: '#fff',
              padding: '3px 8px',
              borderRadius: 6,
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: 0.5,
              flexShrink: 0,
            }}
          >
            {ep.method}
          </span>
          <code style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{ep.path}</code>
          <span
            style={{
              background: authBadgeColors[ep.authBadge] || '#666',
              color: '#fff',
              padding: '2px 7px',
              borderRadius: 6,
              fontSize: 10,
              fontWeight: 600,
              flexShrink: 0,
            }}
          >
            {ep.auth}
          </span>
        </div>
        <span style={{ color: 'var(--text-muted)', fontSize: 18 }}>{expanded ? '−' : '+'}</span>
      </div>

      {expanded && (
        <div className="card-body">
          <h4 style={{ margin: '0 0 6px', fontSize: 15 }}>{ep.title}</h4>
          <p style={{ color: 'var(--text-secondary)', fontSize: 13, margin: '0 0 16px', lineHeight: 1.6 }}>
            {ep.description}
          </p>

          {ep.requestBody && (
            <>
              <h5 style={{ fontSize: 13, fontWeight: 700, margin: '0 0 8px', color: 'var(--text)' }}>Request Body</h5>
              <div style={{ overflowX: 'auto', marginBottom: 16 }}>
                <table className="table" style={{ fontSize: 12 }}>
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

          <h5 style={{ fontSize: 13, fontWeight: 700, margin: '0 0 8px', color: 'var(--text)' }}>Response</h5>
          <pre style={codeBlockStyle}>{ep.responseBody}</pre>

          <h5 style={{ fontSize: 13, fontWeight: 700, margin: '16px 0 8px', color: 'var(--text)' }}>Code Examples</h5>
          <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
            {(['curl', 'python', 'javascript'] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                style={{
                  padding: '4px 12px',
                  borderRadius: 6,
                  border: tab === t ? '1px solid var(--primary)' : '1px solid var(--border)',
                  background: tab === t ? 'var(--primary-soft)' : 'transparent',
                  color: tab === t ? 'var(--primary)' : 'var(--text-secondary)',
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                {t === 'curl' ? 'cURL' : t === 'python' ? 'Python' : 'JavaScript'}
              </button>
            ))}
          </div>
          <pre style={codeBlockStyle}>
            {tab === 'curl' ? ep.curlExample : tab === 'python' ? ep.pythonSdk : ep.jsSdk}
          </pre>
        </div>
      )}
    </div>
  )
}

const codeBlockStyle: React.CSSProperties = {
  background: '#1e293b',
  color: '#e2e8f0',
  padding: '14px 16px',
  borderRadius: 10,
  fontSize: 12,
  lineHeight: 1.6,
  overflowX: 'auto',
  whiteSpace: 'pre',
  margin: 0,
  fontFamily: "'SF Mono', Consolas, 'Liberation Mono', Menlo, monospace",
}

export function ApiReferencePage() {
  const [filter, setFilter] = useState<'all' | 'api-key' | 'jwt' | 'context'>('all')

  const filtered = filter === 'all' ? ENDPOINTS : ENDPOINTS.filter((ep) => ep.authBadge === filter)

  return (
    <div>
      <PageHead
        title="API & SDKs"
        subtitle="Integration APIs for third-party applications and CRM systems"
      />

      <div className="grid-2" style={{ gridTemplateColumns: '1fr 1fr', marginBottom: 20 }}>
        <div className="card" style={{ padding: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
            <div style={{ width: 36, height: 36, borderRadius: 10, background: 'var(--primary-soft)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--primary)', fontWeight: 700, fontSize: 14 }}>Py</div>
            <div>
              <div style={{ fontWeight: 700, fontSize: 14 }}>Python SDK</div>
              <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Zero dependencies, Python 3.10+</div>
            </div>
          </div>
          <pre style={{ ...codeBlockStyle, fontSize: 11, padding: '10px 12px' }}>
{`pip install consent360

from consent360 import Consent360

client = Consent360(api_key="YOUR_KEY", base_url="http://localhost:8000")
ctx = client.create_customer_context(name="John", email="john@example.com")`}
          </pre>
        </div>

        <div className="card" style={{ padding: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
            <div style={{ width: 36, height: 36, borderRadius: 10, background: 'var(--warning-soft)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--warning)', fontWeight: 700, fontSize: 14 }}>JS</div>
            <div>
              <div style={{ fontWeight: 700, fontSize: 14 }}>JavaScript / TypeScript SDK</div>
              <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Zero dependencies, Node 18+ & browsers</div>
            </div>
          </div>
          <pre style={{ ...codeBlockStyle, fontSize: 11, padding: '10px 12px' }}>
{`npm install @consent360/sdk

import { Consent360 } from '@consent360/sdk';

const client = new Consent360({ apiKey: 'YOUR_KEY', baseUrl: 'http://localhost:8000' });
const ctx = await client.createCustomerContext({ name: 'John', email: 'john@example.com' });`}
          </pre>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20, padding: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)' }}>Filter by auth:</span>
          {([
            { key: 'all', label: 'All APIs' },
            { key: 'api-key', label: 'X-API-Key' },
            { key: 'jwt', label: 'Bearer JWT' },
            { key: 'context', label: 'Context Token' },
          ] as const).map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              style={{
                padding: '5px 12px',
                borderRadius: 8,
                border: filter === f.key ? '1.5px solid var(--primary)' : '1px solid var(--border)',
                background: filter === f.key ? 'var(--primary-soft)' : 'transparent',
                color: filter === f.key ? 'var(--primary)' : 'var(--text-secondary)',
                fontSize: 12,
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {filtered.map((ep) => (
        <EndpointCard key={ep.method + ep.path} ep={ep} />
      ))}
    </div>
  )
}
