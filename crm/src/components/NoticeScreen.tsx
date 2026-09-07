import { useEffect, useState } from 'react'
import { publicApi, type PublicPrivacyContact, type PublicPurpose, type PublicRights } from '../api'
import { useFocusTrap } from '../hooks/useFocusTrap'

interface NoticeScreenProps {
  tenantCode: string
  lang?: string
  onClose: () => void
}

// Purpose text falls back to the base (English) fields whenever the selected
// language has no translation entry, or is missing individual keys within it.
// Exported so the cookie banner can localize the same Rule 3 fields it renders
// inline, without a second copy of this fallback logic.
export function localize(p: PublicPurpose, lang: string | undefined) {
  const t = (lang && p.translations[lang]) || {}
  return {
    name: t.name || p.name,
    description: t.description || p.description,
    consent_text: t.consent_text || p.consent_text,
  }
}

export function NoticeScreen({ tenantCode, lang, onClose }: NoticeScreenProps) {
  const [purposes, setPurposes] = useState<PublicPurpose[]>([])
  const [contact, setContact] = useState<PublicPrivacyContact | null>(null)
  const [rights, setRights] = useState<PublicRights | null>(null)

  useEffect(() => {
    publicApi.purposes(tenantCode).then((r) => setPurposes(r.data)).catch(() => setPurposes([]))
    publicApi.privacyContact(tenantCode).then((r) => setContact(r.data)).catch(() => setContact(null))
    publicApi.rights(tenantCode).then((r) => setRights(r.data)).catch(() => setRights(null))
  }, [tenantCode])

  const dialogRef = useFocusTrap<HTMLDivElement>(true, onClose)

  return (
    <div
      className="modal-overlay"
      onClick={(e) => {
        // This overlay must only ever close *this* notice, never a banner it happens
        // to be nested inside — otherwise clicking here would also discard whatever
        // decision the parent screen was mid-way through.
        e.stopPropagation()
        onClose()
      }}
    >
      <div
        ref={dialogRef}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="notice-screen-title"
        style={{ maxWidth: 640, maxHeight: '80vh', overflowY: 'auto' }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="notice-screen-title">Full notice</h2>
        {purposes.map((p) => {
          const l = localize(p, lang)
          return (
            <div key={p.code} style={{ marginBottom: 16, paddingBottom: 16, borderBottom: '1px solid var(--border, #e2e8f0)' }}>
              <h3>{l.name}</h3>
              <p className="subtitle">{l.description}</p>
              <p><strong>Data items:</strong> {p.data_categories.join(', ') || '—'}</p>
              <p><strong>Services enabled:</strong> {p.processing_activities.join(', ') || '—'}</p>
              <p><strong>Retention:</strong> {p.retention_period_days} days</p>
              {l.consent_text && <p><strong>Consent text:</strong> {l.consent_text}</p>}
            </div>
          )
        })}
        {contact && (
          <p>
            <strong>Data Protection Officer:</strong> {contact.dpo_name || '—'}
            {contact.dpo_email && <> · <a href={`mailto:${contact.dpo_email}`}>{contact.dpo_email}</a></>}
            {contact.dpo_phone && <> · <a href={`tel:${contact.dpo_phone}`}>{contact.dpo_phone}</a></>}
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
        <button className="btn btn-primary" onClick={onClose}>Close</button>
      </div>
    </div>
  )
}
