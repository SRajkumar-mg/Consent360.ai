import { useState } from 'react'
import { PageHead } from '../components/ui'

type SdkLang = 'curl' | 'python' | 'javascript'

interface ApiEndpoint {
  id: string
  method: 'GET' | 'POST' | 'PUT' | 'DELETE'
  path: string
  title: string
  description: string
  group: string
  auth: string
  sdk: Record<SdkLang, string>
  responseExample: string
  params?: Array<{ name: string; in: string; required: boolean; type: string; desc: string }>
}

const API_GROUP_DESCS: Record<string, { label: string; desc: string; icon: string }> = {
  integration: { label: 'Integration', desc: 'Server-to-server APIs for identifying customers and minting consent context tokens. Use these to hand off customers to the consent portal.', icon: '🔗' },
  portal: { label: 'Customer Portal', desc: 'Self-service APIs for customers to view and manage their own consents. Authenticated via context token.', icon: '👤' },
  crm: { label: 'CRM Consent', desc: 'Cookie consent preferences and CRM customer consent context APIs. Used by the CRM portal frontend.', icon: '🎯' },
  purge: { label: 'Data Purge', desc: 'GDPR/DPDP compliance APIs for permanently deleting customer consent data.', icon: '🗑️' },
}

const ENDPOINTS: ApiEndpoint[] = [
  {
    id: 'create-context',
    method: 'POST',
    path: '/consent/customer-context',
    title: 'Create Customer Context',
    description: 'Identify or create a customer and mint a short-lived context token (15 min) for consent management.',
    group: 'integration',
    auth: 'X-API-Key header',
    params: [
      { name: 'name', in: 'body', required: true, type: 'string', desc: 'Customer display name' },
      { name: 'email', in: 'body', required: false, type: 'string', desc: 'Customer email (used for identity lookup)' },
      { name: 'phone', in: 'body', required: false, type: 'string', desc: 'Customer phone (fallback for identity)' },
      { name: 'customer_id', in: 'body', required: false, type: 'string', desc: 'Explicit external customer ID' },
      { name: 'source_app', in: 'body', required: false, type: 'string', desc: 'Calling application identifier (default: EXTERNAL_APP)' },
    ],
    sdk: {
      curl: `curl -X POST http://localhost:8000/consent/customer-context \\
  -H "X-API-Key: YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "John Doe",
    "email": "john@example.com",
    "phone": "+91-9000000001",
    "source_app": "MY_APP"
  }'`,
      python: `from consent_360 import Consent360Client

client = Consent360Client(
    base_url="http://localhost:8000",
    api_key="YOUR_API_KEY",
)

result = client.create_customer_context(
    name="John Doe",
    email="john@example.com",
    phone="+91-9000000001",
    source_app="MY_APP",
)
print(result["context_token"])`,
      javascript: `import { Consent360Client } from 'consent360-sdk'

const client = new Consent360Client({
  baseUrl: 'http://localhost:8000',
  apiKey: 'YOUR_API_KEY',
})

const result = await client.createCustomerContext({
  name: 'John Doe',
  email: 'john@example.com',
  phone: '+91-9000000001',
  sourceApp: 'MY_APP',
})
console.log(result.context_token)`,
    },
    responseExample: `{
  "context_token": "eyJhbGciOiJIUzI1NiIs...",
  "context_id": 1,
  "customer_id": "CUST-A1B2C3D4E5",
  "name": "John Doe",
  "expires_in_minutes": 15,
  "source_app": "MY_APP",
  "ui_url": "/consent/context/eyJhbGciOiJIUzI1NiIs...",
  "request_id": null
}`,
  },
  {
    id: 'context-status',
    method: 'GET',
    path: '/consent/context/status/{token}',
    title: 'Check Context Status',
    description: 'Check whether a context token is VALID, CONSUMED, or EXPIRED.',
    group: 'integration',
    auth: 'JWT Bearer (context.use permission)',
    params: [
      { name: 'context_token', in: 'path', required: true, type: 'string', desc: 'The JWT context token to check' },
    ],
    sdk: {
      curl: `curl http://localhost:8000/consent/context/status/CONTEXT_TOKEN \\
  -H "Authorization: Bearer STAFF_JWT_TOKEN"`,
      python: `client.get_context_status("CONTEXT_TOKEN")`,
      javascript: `const status = await client.getContextStatus('CONTEXT_TOKEN')
// { message: "VALID" } | { message: "CONSUMED" } | { message: "EXPIRED" }`,
    },
    responseExample: `{
  "message": "VALID"
}`,
  },
  {
    id: 'portal-overview',
    method: 'GET',
    path: '/portal/overview',
    title: 'Portal Overview',
    description: 'Get all purposes and their consent statuses for the authenticated customer.',
    group: 'portal',
    auth: 'X-Context-Token header',
    sdk: {
      curl: `curl http://localhost:8000/portal/overview \\
  -H "X-Context-Token: CONTEXT_TOKEN"`,
      python: `overview = client.get_portal_overview("CONTEXT_TOKEN")
for purpose in overview["purposes"]:
    print(f"{purpose['name']}: {purpose['status']}")`,
      javascript: `const overview = await client.getPortalOverview(contextToken)
overview.purposes.forEach(p => {
  console.log(\`\${p.name}: \${p.status}\`)
})`,
    },
    responseExample: `{
  "customer": {
    "id": 1,
    "external_id": "CUST-A1B2C3D4E5",
    "name": "John Doe",
    "email": "john@example.com"
  },
  "purposes": [
    {
      "code": "functional",
      "name": "Functional Cookies",
      "status": "GRANTED",
      "granted_count": 3,
      "total_count": 3,
      "legal_basis": "CONSENT"
    }
  ]
}`,
  },
  {
    id: 'portal-grant',
    method: 'POST',
    path: '/portal/grant',
    title: 'Grant Consent',
    description: 'Grant all consents for a specific purpose (e.g., when customer clicks Accept).',
    group: 'portal',
    auth: 'X-Context-Token header',
    params: [
      { name: 'purpose_code', in: 'body', required: true, type: 'string', desc: 'Purpose code (e.g., "functional", "analytics")' },
    ],
    sdk: {
      curl: `curl -X POST http://localhost:8000/portal/grant \\
  -H "X-Context-Token: CONTEXT_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"purpose_code": "functional"}'`,
      python: `result = client.grant_consent("CONTEXT_TOKEN", "functional")
print(result["message"])
# "Granted consent for Functional Cookies (3 records)"`,
      javascript: `const result = await client.grantConsent(contextToken, 'functional')
console.log(result.message)
// "Granted consent for Functional Cookies (3 records)"`,
    },
    responseExample: `{
  "purpose_code": "functional",
  "action": "granted",
  "affected": 3,
  "message": "Granted consent for Functional Cookies (3 records)"
}`,
  },
  {
    id: 'portal-withdraw',
    method: 'POST',
    path: '/portal/withdraw',
    title: 'Withdraw Consent',
    description: 'Withdraw all active consents for a specific purpose.',
    group: 'portal',
    auth: 'X-Context-Token header',
    params: [
      { name: 'purpose_code', in: 'body', required: true, type: 'string', desc: 'Purpose code to withdraw consent for' },
    ],
    sdk: {
      curl: `curl -X POST http://localhost:8000/portal/withdraw \\
  -H "X-Context-Token: CONTEXT_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"purpose_code": "analytics"}'`,
      python: `result = client.withdraw_consent("CONTEXT_TOKEN", "analytics")
print(result["message"])`,
      javascript: `const result = await client.withdrawConsent(contextToken, 'analytics')
console.log(result.message)`,
    },
    responseExample: `{
  "purpose_code": "analytics",
  "action": "withdrawn",
  "affected": 3,
  "message": "Withdrawn consent for Analytics Cookies (3 records)"
}`,
  },
  {
    id: 'crm-get-preferences',
    method: 'GET',
    path: '/crm/customers/{id}/consent-preferences',
    title: 'Get Consent Preferences',
    description: 'Retrieve saved cookie-category preferences for a CRM customer.',
    group: 'crm',
    auth: 'None (internal)',
    params: [
      { name: 'customer_id', in: 'path', required: true, type: 'integer', desc: 'CRM customer database ID' },
    ],
    sdk: {
      curl: `curl http://localhost:8000/crm/customers/42/consent-preferences`,
      python: `prefs = client.get_consent_preferences(42)
print(prefs["preferences"])`,
      javascript: `const prefs = await client.getConsentPreferences(42)
console.log(prefs.preferences)`,
    },
    responseExample: `{
  "preferences": {
    "lang": "en",
    "categories": {
      "necessary": true,
      "functional": true,
      "analytics": false,
      "advertising": false
    },
    "at": "2026-08-20T10:30:00Z"
  }
}`,
  },
  {
    id: 'crm-save-preferences',
    method: 'PUT',
    path: '/crm/customers/{id}/consent-preferences',
    title: 'Save Consent Preferences',
    description: 'Save cookie-category preferences and sync them to consent platform records.',
    group: 'crm',
    auth: 'None (internal)',
    params: [
      { name: 'customer_id', in: 'path', required: true, type: 'integer', desc: 'CRM customer database ID' },
      { name: 'lang', in: 'body', required: false, type: 'string', desc: 'Language preference (default: en)' },
      { name: 'categories', in: 'body', required: false, type: 'object', desc: 'Category toggles: { necessary: bool, functional: bool, analytics: bool, advertising: bool }' },
    ],
    sdk: {
      curl: `curl -X PUT http://localhost:8000/crm/customers/42/consent-preferences \\
  -H "Content-Type: application/json" \\
  -d '{
    "lang": "en",
    "categories": {
      "necessary": true,
      "functional": true,
      "analytics": true,
      "advertising": false
    }
  }'`,
      python: `result = client.save_consent_preferences(42, {
    "necessary": True,
    "functional": True,
    "analytics": True,
    "advertising": False,
}, lang="en")
print(result["saved"])  # True`,
      javascript: `const result = await client.saveConsentPreferences(42, {
  lang: 'en',
  categories: {
    necessary: true,
    functional: true,
    analytics: true,
    advertising: false,
  },
})
console.log(result.saved)  // true`,
    },
    responseExample: `{
  "saved": true,
  "preferences": {
    "lang": "en",
    "categories": {
      "necessary": true,
      "functional": true,
      "analytics": true,
      "advertising": false
    },
    "at": "2026-08-20T10:30:00Z"
  }
}`,
  },
  {
    id: 'crm-context',
    method: 'POST',
    path: '/crm/customers/{id}/consent-context',
    title: 'Create CRM Consent Context',
    description: 'Mint a consent context token for an existing CRM customer to manage their consents.',
    group: 'crm',
    auth: 'None (internal)',
    params: [
      { name: 'customer_id', in: 'path', required: true, type: 'integer', desc: 'CRM customer database ID' },
    ],
    sdk: {
      curl: `curl -X POST http://localhost:8000/crm/customers/42/consent-context`,
      python: `result = client.create_crm_consent_context(42)
print(result["consent_portal_url"])`,
      javascript: `const result = await client.createCrmConsentContext(42)
window.open(result.consent_portal_url)`,
    },
    responseExample: `{
  "customer": { "id": 42, "name": "John Doe", "email": "john@example.com" },
  "created": false,
  "context_token": "eyJhbGciOiJIUzI1NiIs...",
  "consent_portal_url": "http://localhost:5173/portal/consent?ctx=eyJ...",
  "expires_in_minutes": 15
}`,
  },
  {
    id: 'delete-customer',
    method: 'DELETE',
    path: '/crm/customers/by-email/{email}',
    title: 'Delete Customer Consent Profile',
    description: 'Permanently purge a customer\'s entire consent profile (GDPR/DPDP right to erasure).',
    group: 'purge',
    auth: 'X-API-Key header',
    params: [
      { name: 'email', in: 'path', required: true, type: 'string', desc: 'Customer email address' },
    ],
    sdk: {
      curl: `curl -X DELETE http://localhost:8000/crm/customers/by-email/john@example.com \\
  -H "X-API-Key: YOUR_API_KEY"`,
      python: `result = client.delete_customer_by_email("john@example.com")
print(result["deleted"])  # True`,
      javascript: `const result = await client.deleteCustomerByEmail('john@example.com')
console.log(result.deleted)  // true`,
    },
    responseExample: `{
  "deleted": true,
  "external_id": "CUST-A1B2C3D4E5"
}`,
  },
]

const METHOD_COLORS: Record<string, string> = {
  GET: 'var(--success)',
  POST: 'var(--primary)',
  PUT: 'var(--warning)',
  DELETE: 'var(--danger)',
}

function CodeBlock({ code, lang }: { code: string; lang: string }) {
  return (
    <div className="c360-code-block">
      <div className="c360-code-lang">{lang}</div>
      <pre><code>{code}</code></pre>
    </div>
  )
}

function EndpointCard({ ep }: { ep: ApiEndpoint }) {
  const [sdkTab, setSdkTab] = useState<SdkLang>('curl')
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="c360-endpoint" id={ep.id}>
      <div className="c360-ep-header" onClick={() => setExpanded(!expanded)}>
        <div className="c360-ep-method" style={{ background: METHOD_COLORS[ep.method], color: '#fff' }}>
          {ep.method}
        </div>
        <code className="c360-ep-path">{ep.path}</code>
        <span className="c360-ep-title">{ep.title}</span>
        <span className="c360-ep-auth-badge">{ep.auth}</span>
        <span className="c360-chevron" style={{ transform: expanded ? 'rotate(180deg)' : 'rotate(0)' }}>▾</span>
      </div>

      {expanded && (
        <div className="c360-ep-body">
          <p className="c360-ep-desc">{ep.description}</p>

          {ep.params && ep.params.length > 0 && (
            <div className="c360-params">
              <h4>Parameters</h4>
              <table className="table">
                <thead><tr><th>Name</th><th>In</th><th>Type</th><th>Required</th><th>Description</th></tr></thead>
                <tbody>
                  {ep.params.map((p) => (
                    <tr key={p.name}>
                      <td><code>{p.name}</code></td>
                      <td>{p.in}</td>
                      <td>{p.type}</td>
                      <td>{p.required ? <span className="badge b-DENIED">required</span> : <span className="badge b-PENDING">optional</span>}</td>
                      <td>{p.desc}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="c360-sdk-section">
            <h4>Code Examples</h4>
            <div className="c360-sdk-tabs">
              {(['curl', 'python', 'javascript'] as SdkLang[]).map((lang) => (
                <button
                  key={lang}
                  className={`c360-sdk-tab ${sdkTab === lang ? 'active' : ''}`}
                  onClick={() => setSdkTab(lang)}
                >
                  {lang === 'curl' ? 'cURL' : lang === 'python' ? 'Python' : 'JavaScript'}
                </button>
              ))}
            </div>
            <CodeBlock code={ep.sdk[sdkTab]} lang={sdkTab} />
          </div>

          <div className="c360-response-section">
            <h4>Response</h4>
            <CodeBlock code={ep.responseExample} lang="json" />
          </div>
        </div>
      )}
    </div>
  )
}

export function Consent360Page() {
  const groups = Object.keys(API_GROUP_DESCS)

  return (
    <div>
      <PageHead
        title="Consent 360"
        subtitle="Integration APIs & SDKs for third-party applications"
      />

      <div className="c360-hero">
        <div className="c360-hero-content">
          <h2>Build with Consent360</h2>
          <p>
            Integrate consent management into your applications using our REST APIs
            or client SDKs. Every consent action is fully audited and DPDP Act compliant.
          </p>
          <div className="c360-hero-badges">
            <span className="badge b-ACTIVE">REST API</span>
            <span className="badge b-primary">Python SDK</span>
            <span className="badge b-info">JavaScript SDK</span>
            <span className="badge b-PENDING">OpenAPI 3.1</span>
          </div>
        </div>
      </div>

      <div className="c360-quickstart">
        <h3>Quick Start</h3>
        <div className="c360-quickstart-grid">
          <div className="c360-qs-card">
            <div className="c360-qs-step">1</div>
            <h4>Get your API key</h4>
            <p>Use the <code>X-API-Key</code> header with your integration key. Find it in <strong>Settings → Integration</strong>.</p>
          </div>
          <div className="c360-qs-card">
            <div className="c360-qs-step">2</div>
            <h4>Create a context</h4>
            <p>Call <code>POST /consent/customer-context</code> to identify the customer and get a context token.</p>
          </div>
          <div className="c360-qs-card">
            <div className="c360-qs-step">3</div>
            <h4>Open consent portal</h4>
            <p>Redirect the customer to the <code>ui_url</code> from the response to manage their consents.</p>
          </div>
        </div>
      </div>

      <div className="c360-auth-info">
        <h3>Authentication</h3>
        <div className="c360-auth-grid">
          <div className="c360-auth-card">
            <h4>X-API-Key</h4>
            <p>Server-to-server authentication. Pass your integration API key in the <code>X-API-Key</code> header.</p>
            <CodeBlock code={`curl -H "X-API-Key: YOUR_API_KEY" http://localhost:8000/consent/customer-context`} lang="bash" />
          </div>
          <div className="c360-auth-card">
            <h4>Context Token</h4>
            <p>Self-authenticating JWT tokens. No additional auth header needed — the token IS the credential.</p>
            <CodeBlock code={`curl http://localhost:8000/portal/overview \\
  -H "X-Context-Token: eyJhbGciOiJIUzI1NiIs..."`} lang="bash" />
          </div>
        </div>
      </div>

      {groups.map((groupKey) => {
        const group = API_GROUP_DESCS[groupKey]
        const eps = ENDPOINTS.filter((e) => e.group === groupKey)
        if (eps.length === 0) return null
        return (
          <div key={groupKey} className="c360-group">
            <div className="c360-group-header">
              <span className="c360-group-icon">{group.icon}</span>
              <div>
                <h3>{group.label}</h3>
                <p>{group.desc}</p>
              </div>
            </div>
            <div className="c360-endpoints">
              {eps.map((ep) => <EndpointCard key={ep.id} ep={ep} />)}
            </div>
          </div>
        )
      })}

      <div className="c360-sdk-download">
        <h3>Download SDKs</h3>
        <div className="c360-sdk-cards">
          <div className="c360-sdk-card">
            <div className="c360-sdk-icon">🐍</div>
            <h4>Python SDK</h4>
            <p>Install via pip or copy <code>consent_360.py</code> into your project.</p>
            <CodeBlock code={`pip install requests\n# then copy consent_360.py into your project`} lang="bash" />
          </div>
          <div className="c360-sdk-card">
            <div className="c360-sdk-icon">📦</div>
            <h4>JavaScript / TypeScript SDK</h4>
            <p>Works with Node.js, Deno, Bun, and browsers. Copy <code>consent-360.ts</code>.</p>
            <CodeBlock code={`// ES Module\nimport { Consent360Client } from './consent-360'\n\n// CommonJS\nconst { Consent360Client } = require('./consent-360')`} lang="javascript" />
          </div>
        </div>
      </div>
    </div>
  )
}
