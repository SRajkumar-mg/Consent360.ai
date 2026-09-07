import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { publicApi, type PublicPrivacyContact, type PublicPurpose, type PublicRights } from '../api'
import { CONSENT_TTL_DAYS, gpcSignalDetected } from '../consentGate'

const TENANT_CODE = 'SKILLLEARN'

// R2-10 / gap Q-04, Q-08: a per-tenant cookie policy page, linked from the
// cookie banner, that documents what each category actually does, how long a
// choice is remembered before we ask again, and how a Global Privacy Control
// signal is handled - the things a visitor (or an auditor) would look for
// beyond the in-the-moment banner text.
export function CookiePolicyPage() {
  const [purposes, setPurposes] = useState<PublicPurpose[]>([])
  const [contact, setContact] = useState<PublicPrivacyContact | null>(null)
  const [rights, setRights] = useState<PublicRights | null>(null)
  const gpc = gpcSignalDetected()

  useEffect(() => {
    publicApi.purposes(TENANT_CODE).then((r) => setPurposes(r.data)).catch(() => setPurposes([]))
    publicApi.privacyContact(TENANT_CODE).then((r) => setContact(r.data)).catch(() => setContact(null))
    publicApi.rights(TENANT_CODE).then((r) => setRights(r.data)).catch(() => setRights(null))
  }, [])

  return (
    <div style={{ maxWidth: 760, margin: '0 auto', padding: '48px 24px 80px', color: 'var(--text)' }}>
      <Link to="/" className="cookie-link">&larr; Back to SkillLearn</Link>
      <h1 style={{ marginTop: 16 }}>Cookie Policy</h1>
      <p className="subtitle">How SkillLearn uses cookies and similar technologies, and how your choices are enforced.</p>

      <h2 style={{ marginTop: 32 }}>Categories we use</h2>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left', padding: '8px 10px' }}>Category</th>
              <th style={{ textAlign: 'left', padding: '8px 10px' }}>What it's for</th>
              <th style={{ textAlign: 'left', padding: '8px 10px' }}>Retention</th>
            </tr>
          </thead>
          <tbody>
            {purposes.map((p) => (
              <tr key={p.code} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '8px 10px', fontWeight: 600 }}>{p.name}</td>
                <td style={{ padding: '8px 10px' }}>{p.description || '—'}</td>
                <td style={{ padding: '8px 10px' }}>{p.retention_period_days} days</td>
              </tr>
            ))}
            {purposes.length === 0 && (
              <tr><td colSpan={3} style={{ padding: '8px 10px' }}>Loading…</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <h2 style={{ marginTop: 32 }}>How your choice is enforced</h2>
      <p>
        Strictly necessary cookies aside, no analytics, functional or advertising script runs in your browser
        until you actively choose to allow that category. This is enforced by code (a consent gate), not policy
        alone: the scripts are inert on the page and only activated once your consent is on file. A background
        check keeps re-scanning the page for anything that should have been gated and was not.
      </p>
      <p>
        <strong>What this does and does not cover:</strong> the gate and its background scan can only
        recognise tags, tracking pixels and embeds that either follow our gating convention or point at a
        known analytics/advertising host. They cannot detect or stop a compromised or maliciously injected
        third-party script that calls a browser API directly through a route the scan does not watch, or that
        runs before this page's own code does. This is a page-level control, not a security boundary — the
        only mechanism that can reliably stop that class of problem is a server-enforced
        Content-Security-Policy, which is a separate, infrastructure-level control from what is described here.
      </p>

      <h2 style={{ marginTop: 32 }}>How long we remember your choice</h2>
      <p>
        Your choice is stored for up to {CONSENT_TTL_DAYS} days. After that it expires and we ask again the next
        time you visit, rather than continuing to rely on an old decision indefinitely.
      </p>

      <h2 style={{ marginTop: 32 }}>Global Privacy Control</h2>
      <p>
        {gpc
          ? 'We detected a Global Privacy Control signal from your browser on this visit. We treat it as an objection to optional processing: functional, analytics and advertising cookies stay off regardless of any other setting, and this signal is recorded with your consent decision, not only remembered by this browser.'
          : 'We did not detect a Global Privacy Control signal from your browser on this visit. If your browser or an extension sends one, we treat it as an objection to optional processing and keep those categories off.'}
        {' '}We publish our support for this signal at{' '}
        <a href="/.well-known/gpc.json" target="_blank" rel="noopener noreferrer">/.well-known/gpc.json</a>.
      </p>

      {(contact || rights) && <h2 style={{ marginTop: 32 }}>Your rights</h2>}
      {contact && (
        <p>
          <strong>Data Protection Officer:</strong> {contact.dpo_name || '—'}
          {contact.dpo_email && <> · <a href={`mailto:${contact.dpo_email}`}>{contact.dpo_email}</a></>}
        </p>
      )}
      {rights && (
        <p>
          {rights.withdraw_url && <a href={rights.withdraw_url}>Withdraw consent</a>}
          {rights.rights_url && <> · <a href={rights.rights_url}>Your rights</a></>}
          {rights.grievance_url && <> · <a href={rights.grievance_url}>Grievance</a></>}
          {rights.board_complaint_url && <> · <a href={rights.board_complaint_url}>Board complaint</a></>}
        </p>
      )}
    </div>
  )
}
