import { useEffect, useState } from 'react'
import { BANNER_COPY } from '../copy'
import { COOKIE_CATEGORIES, CRM_LANG_KEY, LANGUAGES } from '../languages'
import { NoticeScreen, localize } from './NoticeScreen'
import { publicApi, type PublicPrivacyContact, type PublicPurpose, type PublicRights } from '../api'
import { useFocusTrap } from '../hooks/useFocusTrap'

const TENANT_CODE = 'CRM_PORTAL'

const CAT_COLORS: Record<string, string> = {
  necessary: '#8b93b4',
  functional: '#6d5df6',
  analytics: '#ffb454',
  advertising: '#ff6b4a',
}

const DEFAULT_CATS: Record<string, boolean> = {
  necessary: true,
  functional: false,
  analytics: false,
  advertising: false,
}

interface CookiePreferenceModalProps {
  onClose: () => void
  onAcceptAll: () => void
  onRejectAll: () => void
  onSave: (lang: string, categories: Record<string, boolean>) => void
  initialLang?: string
  initialCats?: Record<string, boolean>
}

export function CookiePreferenceModal({ onClose, onAcceptAll, onRejectAll, onSave, initialLang, initialCats }: CookiePreferenceModalProps) {
  const [lang, setLang] = useState<string>(() => {
    if (initialLang && LANGUAGES.some((l) => l.code === initialLang && l.enabled)) return initialLang
    const saved = localStorage.getItem(CRM_LANG_KEY)
    return saved && LANGUAGES.some((l) => l.code === saved && l.enabled) ? saved : 'en'
  })
  const [cats, setCats] = useState<Record<string, boolean>>(() =>
    initialCats ? { ...DEFAULT_CATS, ...initialCats, necessary: true } : { ...DEFAULT_CATS }
  )
  const [showNotice, setShowNotice] = useState(false)
  const [noticePurposes, setNoticePurposes] = useState<PublicPurpose[]>([])
  const [noticeContact, setNoticeContact] = useState<PublicPrivacyContact | null>(null)
  const [noticeRights, setNoticeRights] = useState<PublicRights | null>(null)

  useEffect(() => {
    publicApi.purposes(TENANT_CODE).then((r) => setNoticePurposes(r.data)).catch(() => setNoticePurposes([]))
    publicApi.privacyContact(TENANT_CODE).then((r) => setNoticeContact(r.data)).catch(() => setNoticeContact(null))
    publicApi.rights(TENANT_CODE).then((r) => setNoticeRights(r.data)).catch(() => setNoticeRights(null))
  }, [])

  const t = BANNER_COPY[lang] || BANNER_COPY.en
  const dialogRef = useFocusTrap<HTMLDivElement>(true, onClose)

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        ref={dialogRef}
        className="modal modal-cookie"
        role="dialog"
        aria-modal="true"
        aria-labelledby="crm-cookie-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-cookie-header">
          <div>
            <h2 id="crm-cookie-modal-title">{t.modalTitle}</h2>
            <p className="subtitle">{t.bannerText}</p>
            <button className="cookie-link" onClick={() => setShowNotice(true)} type="button">{t.viewFullNotice}</button>
            {' · '}
            <a className="cookie-link" href="/cookie-policy" target="_blank" rel="noopener noreferrer">Cookie Policy</a>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label={t.close}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="form-group cookie-lang">
          <label htmlFor="crm-cookie-modal-lang">{t.chooseLanguage}</label>
          <select
            id="crm-cookie-modal-lang"
            className="input"
            value={lang}
            onChange={(e) => {
              const next = e.target.value
              if (LANGUAGES.some((l) => l.code === next && l.enabled)) setLang(next)
            }}
          >
            {LANGUAGES.map((l) => (
              <option key={l.code} value={l.code} disabled={!l.enabled}>
                {l.nameNative} ({l.nameEn})
              </option>
            ))}
          </select>
          <div className="cookie-lang-note">{t.languageNote}</div>
        </div>

        <div className="notice-summary" role="group" aria-label="Notice">
          <div className="notice-summary-items">
            {noticePurposes.length === 0 && <p className="notice-summary-loading">Loading notice…</p>}
            {noticePurposes.map((p) => {
              const l = localize(p, lang)
              return (
                <div className="notice-summary-item" key={p.code}>
                  <div className="notice-summary-item-title">{l.name}</div>
                  <div className="notice-summary-item-row"><strong>{t.dataItemsLabel}:</strong> {p.data_categories.join(', ') || '—'}</div>
                  <div className="notice-summary-item-row"><strong>{t.servicesLabel}:</strong> {p.processing_activities.join(', ') || '—'}</div>
                  <div className="notice-summary-item-row"><strong>{t.retentionLabel}:</strong> {p.retention_period_days} {t.retentionDays}</div>
                  {l.consent_text && <div className="notice-summary-item-row"><strong>{t.consentLabel}:</strong> {l.consent_text}</div>}
                </div>
              )
            })}
          </div>
          {/* Always visible, never scrolled out of view - the notice is seen, not merely reachable. */}
          <div className="notice-summary-essential">
            {noticeContact && (
              <div className="notice-summary-contact">
                <strong>{t.dpoLabel}:</strong> {noticeContact.dpo_name || '—'}
                {noticeContact.dpo_email && <> · <a href={`mailto:${noticeContact.dpo_email}`}>{noticeContact.dpo_email}</a></>}
                {noticeContact.dpo_phone && <> · <a href={`tel:${noticeContact.dpo_phone}`}>{noticeContact.dpo_phone}</a></>}
              </div>
            )}
            {noticeRights && (
              <div className="notice-summary-links">
                {noticeRights.withdraw_url && <a href={noticeRights.withdraw_url}>{t.withdrawLink}</a>}
                {noticeRights.rights_url && <> · <a href={noticeRights.rights_url}>{t.rightsLink}</a></>}
                {noticeRights.grievance_url && <> · <a href={noticeRights.grievance_url}>{t.grievanceLink}</a></>}
                {noticeRights.board_complaint_url && <> · <a href={noticeRights.board_complaint_url}>{t.boardComplaintLink}</a></>}
              </div>
            )}
          </div>
        </div>

        <div className="cookie-categories">
          {COOKIE_CATEGORIES.map((c) => (
            <div className="cookie-category" key={c.id}>
              <div className="cookie-category-text">
                <span className="cookie-cat-dot" style={{ background: CAT_COLORS[c.id] || '#8b93b4', color: CAT_COLORS[c.id] || '#8b93b4' }} />
                <div>
                  <div className="cookie-category-label">{t[c.labelKey]}</div>
                  <div className="cookie-category-desc">{t[c.descKey]}</div>
                </div>
              </div>
              <label className="switch">
                <input
                  type="checkbox"
                  checked={cats[c.id] ?? false}
                  disabled={!!c.locked}
                  onChange={(e) => setCats({ ...cats, [c.id]: e.target.checked })}
                  aria-label={`${t[c.labelKey]} — ${t[c.descKey]}`}
                />
                <span className="slider" />
              </label>
            </div>
          ))}
        </div>

        <div className="modal-cookie-footer">
          <button className="btn btn-primary" onClick={onAcceptAll}>{t.acceptAll}</button>
          <button className="btn btn-secondary" onClick={onRejectAll}>{t.rejectAll}</button>
          <button
            className="btn btn-ghost"
            onClick={() => {
              onSave(lang, { ...cats, necessary: true })
            }}
          >
            {t.save}
          </button>
          <button className="cookie-link" onClick={onClose}>{t.cancel}</button>
        </div>
      </div>
      {showNotice && <NoticeScreen tenantCode="CRM_PORTAL" lang={lang} onClose={() => setShowNotice(false)} />}
    </div>
  )
}
