import { useEffect, useState } from 'react'
import { publicApi, type PublicPrivacyContact, type PublicPurpose, type PublicRights } from './publicApi'
import { CONSENT_TTL_DAYS, gpcSignalDetected } from './consentGate'
import { useFocusTrap } from './useFocusTrap'

interface CookiePolicyScreenProps {
  tenantCode: string
  onClose: () => void
}

// R2-10 / gap Q-04, Q-08: a per-tenant cookie policy screen, linked from the
// consent banner, that documents what each category actually does, how long
// a choice is remembered before we ask again, and how a Global Privacy
// Control signal is handled. CareerHub has no router (see main.tsx), so this
// follows the same overlay pattern as NoticeScreen rather than being a route.
export function CookiePolicyScreen({ tenantCode, onClose }: CookiePolicyScreenProps) {
  const [purposes, setPurposes] = useState<PublicPurpose[]>([])
  const [contact, setContact] = useState<PublicPrivacyContact | null>(null)
  const [rights, setRights] = useState<PublicRights | null>(null)
  const gpc = gpcSignalDetected()
  const dialogRef = useFocusTrap<HTMLDivElement>(true, onClose)

  useEffect(() => {
    publicApi.purposes(tenantCode).then(setPurposes).catch(() => setPurposes([]))
    publicApi.privacyContact(tenantCode).then(setContact).catch(() => setContact(null))
    publicApi.rights(tenantCode).then(setRights).catch(() => setRights(null))
  }, [tenantCode])

  return (
    <div
      className="consent-overlay"
      onClick={(e) => {
        e.stopPropagation()
        onClose()
      }}
    >
      <div
        ref={dialogRef}
        className="consent-banner"
        role="dialog"
        aria-modal="true"
        aria-labelledby="jp-cookie-policy-title"
        onClick={(e) => e.stopPropagation()}
        style={{ maxHeight: '85vh', overflowY: 'auto' }}
      >
        <div className="consent-header">
          <h2 id="jp-cookie-policy-title">Cookie Policy</h2>
          <p>How CareerHub uses cookies and similar technologies, and how your choices are enforced.</p>
        </div>
        <div className="consent-body">
          <h4>Categories we use</h4>
          {purposes.map((p) => (
            <p key={p.code}>
              <strong>{p.name}:</strong> {p.description || '—'} (retained {p.retention_period_days} days)
            </p>
          ))}

          <h4>How your choice is enforced</h4>
          <p>
            Strictly necessary cookies aside, no analytics, functional or advertising script runs in your browser
            until you actively choose to allow that category. This is enforced by code (a consent gate), not
            policy alone: the scripts are inert on the page and only activated once your consent is on file. A
            background check keeps re-scanning the page for anything that should have been gated and was not.
          </p>
          <p>
            <strong>What this does and does not cover:</strong> the gate and its background scan can only
            recognise tags, tracking pixels and embeds that either follow our gating convention or point at a
            known analytics/advertising host. They cannot detect or stop a compromised or maliciously injected
            third-party script that calls a browser API directly through a route the scan does not watch, or
            that runs before this page's own code does. This is a page-level control, not a security boundary —
            the only mechanism that can reliably stop that class of problem is a server-enforced
            Content-Security-Policy, which is a separate, infrastructure-level control from what is described
            here.
          </p>

          <h4>How long we remember your choice</h4>
          <p>
            Your choice is stored for up to {CONSENT_TTL_DAYS} days. After that it expires and we ask again the
            next time you visit, rather than continuing to rely on an old decision indefinitely.
          </p>

          <h4>Global Privacy Control</h4>
          <p>
            {gpc
              ? 'We detected a Global Privacy Control signal from your browser on this visit. We treat it as an objection to optional processing: functional, analytics and advertising stay off regardless of any other setting, and this signal is recorded with your consent decision, not only remembered by this browser.'
              : 'We did not detect a Global Privacy Control signal from your browser on this visit. If your browser or an extension sends one, we treat it as an objection to optional processing and keep those categories off.'}
            {' '}We publish our support for this signal at{' '}
            <a href="/.well-known/gpc.json" target="_blank" rel="noopener noreferrer">/.well-known/gpc.json</a>.
          </p>

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
        <div className="consent-footer">
          <button className="btn btn-primary" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}
