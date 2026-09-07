import { useEffect, useState } from 'react'
import { publicApi, localize, type PublicPrivacyContact, type PublicPurpose, type PublicRights } from './publicApi'
import { BANNER_COPY } from './copy'
import { useFocusTrap } from './useFocusTrap'

interface NoticeScreenProps {
  tenantCode: string
  lang?: string
  onClose: () => void
}

export function NoticeScreen({ tenantCode, lang, onClose }: NoticeScreenProps) {
  const [purposes, setPurposes] = useState<PublicPurpose[]>([])
  const [contact, setContact] = useState<PublicPrivacyContact | null>(null)
  const [rights, setRights] = useState<PublicRights | null>(null)
  const t = BANNER_COPY[lang || 'en'] || BANNER_COPY.en

  useEffect(() => {
    publicApi.purposes(tenantCode).then(setPurposes).catch(() => setPurposes([]))
    publicApi.privacyContact(tenantCode).then(setContact).catch(() => setContact(null))
    publicApi.rights(tenantCode).then(setRights).catch(() => setRights(null))
  }, [tenantCode])

  const dialogRef = useFocusTrap<HTMLDivElement>(true, onClose)

  return (
    <div
      className="consent-overlay"
      onClick={(e) => {
        // This overlay must only ever close *this* notice, never a banner it
        // happens to be nested inside - otherwise clicking here would also
        // discard whatever decision the parent screen was mid-way through.
        e.stopPropagation()
        onClose()
      }}
    >
      <div
        ref={dialogRef}
        className="consent-banner"
        role="dialog"
        aria-modal="true"
        aria-labelledby="jp-notice-screen-title"
        onClick={(e) => e.stopPropagation()}
        style={{ maxHeight: '80vh', overflowY: 'auto' }}
      >
        <div className="consent-header">
          <h2 id="jp-notice-screen-title">Full notice</h2>
        </div>
        <div className="consent-body">
          {purposes.map((p) => {
            const l = localize(p, lang)
            return (
              <div key={p.code} className="consent-purpose" style={{ flexDirection: 'column', alignItems: 'flex-start' }}>
                <h4>{l.name}</h4>
                <p>{l.description}</p>
                <p><strong>{t.dataItemsLabel}:</strong> {p.data_categories.join(', ') || '—'}</p>
                <p><strong>{t.servicesLabel}:</strong> {p.processing_activities.join(', ') || '—'}</p>
                <p><strong>{t.retentionLabel}:</strong> {p.retention_period_days} {t.retentionDays}</p>
                {l.consent_text && <p><strong>{t.consentLabel}:</strong> {l.consent_text}</p>}
              </div>
            )
          })}
          {contact && (
            <p>
              <strong>{t.dpoLabel}:</strong> {contact.dpo_name || '—'}
              {contact.dpo_email && <> · <a href={`mailto:${contact.dpo_email}`}>{contact.dpo_email}</a></>}
              {contact.dpo_phone && <> · <a href={`tel:${contact.dpo_phone}`}>{contact.dpo_phone}</a></>}
            </p>
          )}
          {rights && (
            <p>
              {rights.withdraw_url && <a href={rights.withdraw_url}>{t.withdrawLink}</a>}
              {rights.rights_url && <> · <a href={rights.rights_url}>{t.rightsLink}</a></>}
              {rights.grievance_url && <> · <a href={rights.grievance_url}>{t.grievanceLink}</a></>}
              {rights.board_complaint_url && <> · <a href={rights.board_complaint_url}>{t.boardComplaintLink}</a></>}
            </p>
          )}
        </div>
        <div className="consent-footer">
          <button className="btn btn-primary" onClick={onClose}>{t.close}</button>
        </div>
      </div>
    </div>
  )
}
