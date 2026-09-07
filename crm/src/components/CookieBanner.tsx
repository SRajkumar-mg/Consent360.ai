import { useEffect, useState } from 'react'
import { CookiePreferenceModal } from './CookiePreferenceModal'
import { NoticeScreen, localize } from './NoticeScreen'
import { consentApi, publicApi, type PublicPrivacyContact, type PublicPurpose, type PublicRights } from '../api'
import { BANNER_COPY } from '../copy'
import { CRM_LANG_KEY, LANGUAGES } from '../languages'
import { useFocusTrap } from '../hooks/useFocusTrap'
import { gpcSignalDetected } from '../consentGate'
import { COOKIE_BANNER_VERSION, OFFERED_PURPOSE_CODES, emitNoticeShown } from '../bannerEvents'

const TENANT_CODE = 'CRM_PORTAL'

interface CookieBannerProps {
  customerId: number | null
  onAcceptAll: (lang: string) => void
  onRejectAll: (lang: string) => void
  onSave: (lang: string, categories: Record<string, boolean>) => void
}

export function CookieBanner({ customerId, onAcceptAll, onRejectAll, onSave }: CookieBannerProps) {
  const [open, setOpen] = useState(false)
  const [showNotice, setShowNotice] = useState(false)
  const [lang, setLang] = useState<string>(() => {
    const saved = localStorage.getItem(CRM_LANG_KEY)
    return saved && LANGUAGES.some((l) => l.code === saved && l.enabled) ? saved : 'en'
  })
  const [prefs, setPrefs] = useState<Record<string, boolean> | null>(null)

  // Rule 3 notice elements, fetched once and rendered directly on the banner so the
  // notice is *seen* before a decision, not merely reachable via "View full notice".
  const [noticePurposes, setNoticePurposes] = useState<PublicPurpose[]>([])
  const [noticeContact, setNoticeContact] = useState<PublicPrivacyContact | null>(null)
  const [noticeRights, setNoticeRights] = useState<PublicRights | null>(null)

  // R2-07: the impression. This fires once, when the banner is actually
  // painted - not when the page loads and not when a decision is taken -
  // because it is the denominator for K-02's ignore rate, and a notice that
  // was never rendered was never ignored. `lang` is read at mount rather than
  // tracked, so this records the language the notice first appeared in; the
  // decision event carries whatever language it was finally taken in.
  useEffect(() => {
    emitNoticeShown({
      bannerVersion: COOKIE_BANNER_VERSION,
      language: lang,
      purposesOffered: OFFERED_PURPOSE_CODES,
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    publicApi.purposes(TENANT_CODE).then((r) => setNoticePurposes(r.data)).catch(() => setNoticePurposes([]))
    publicApi.privacyContact(TENANT_CODE).then((r) => setNoticeContact(r.data)).catch(() => setNoticeContact(null))
    publicApi.rights(TENANT_CODE).then((r) => setNoticeRights(r.data)).catch(() => setNoticeRights(null))
  }, [])

  useEffect(() => {
    if (!customerId) return
    let cancelled = false
    consentApi
      .getConsentPreferences(customerId)
      .then((res) => {
        if (cancelled) return
        const p = (res.data?.preferences ?? {}) as { lang?: string; categories?: Record<string, boolean> }
        if (p.categories && Object.keys(p.categories).length) setPrefs(p.categories)
        if (p.lang && LANGUAGES.some((l) => l.code === p.lang && l.enabled)) setLang(p.lang)
      })
      .catch(() => {
        /* no saved preferences yet - use defaults */
      })
    return () => {
      cancelled = true
    }
  }, [customerId])

  const t = BANNER_COPY[lang] || BANNER_COPY.en

  // No onEscape: this top-level banner requires an affirmative decision, so
  // there is no neutral "just close it" path — Escape must not stand in for one.
  const dialogRef = useFocusTrap<HTMLDivElement>(true)

  const setLanguage = (next: string) => {
    if (!LANGUAGES.some((l) => l.code === next && l.enabled)) return
    localStorage.setItem(CRM_LANG_KEY, next)
    setLang(next)
  }

  return (
    <>
      <div className="modal-overlay">
        <div
          ref={dialogRef}
          className="modal modal-cookie consent-banner-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="crm-cookie-banner-title"
          aria-describedby="crm-cookie-banner-text crm-cookie-banner-notice-essential crm-cookie-banner-notice-items"
        >
          <div className="consent-banner-head">
            <span className="cookie-banner-icon" aria-hidden="true">
              <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#6d5df6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2a10 10 0 1 0 10 10 4 4 0 0 1-5-5 4 4 0 0 1-5-5z" />
                <path d="M8.5 8.5h.01M16 15.5h.01M12 12h.01M7 14h.01M17 10h.01" />
              </svg>
            </span>
            <div>
              <h2 className="consent-banner-title" id="crm-cookie-banner-title">{t.bannerTitle}</h2>
              <p className="consent-banner-text" id="crm-cookie-banner-text">{t.bannerText}</p>
            </div>
          </div>

          {gpcSignalDetected() && (
            // R2-10 / gap Q-07: Global Privacy Control is a live opt-out signal from
            // the browser itself - optional categories stay off regardless of any
            // choice made here for as long as it is present. English-only: this is
            // a transparency note, not one of the Rule 3 notice elements above,
            // which are already rendered in every scheduled language.
            <div className="alert alert-info">
              Your browser is sending a Global Privacy Control signal. We treat it as an objection to optional
              cookies — functional, analytics and advertising stay off no matter what you choose below — and
              record it alongside your decision, not only in this browser.
            </div>
          )}

          <div className="form-group cookie-lang">
            <label htmlFor="crm-cookie-banner-lang">{t.chooseLanguage}</label>
            <select id="crm-cookie-banner-lang" className="input" value={lang} onChange={(e) => setLanguage(e.target.value)}>
              {LANGUAGES.map((l) => (
                <option key={l.code} value={l.code} disabled={!l.enabled}>
                  {l.nameNative} ({l.nameEn})
                </option>
              ))}
            </select>
            <div className="cookie-lang-note">{t.languageNote}</div>
          </div>

          {/* R2-10 fix round / WCAG 1.3.1, 4.1.2: this substantive notice (purposes,
              retention, DPO contact) is included in the dialog's aria-describedby
              above so it is part of what a screen reader announces on focus, not
              something the visitor has to go find. No aria-label here - an
              aria-describedby reference resolves an aria-label instead of the
              element's content, which would silently defeat the point.
              aria-describedby lists this block's two ids in DPO/rights-first
              order (independent of their visual order below, which stays
              unchanged) because live testing showed Chromium truncates a very
              long computed accessible description around ~1000 characters -
              the fixed-size DPO/rights block must survive that cut even when
              the variable-length, potentially-long purpose list does not. */}
          <div className="notice-summary">
            <div className="notice-summary-items" id="crm-cookie-banner-notice-items">
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
            <div className="notice-summary-essential" id="crm-cookie-banner-notice-essential">
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
              <button className="cookie-link" onClick={() => setShowNotice(true)} type="button">{t.viewFullNotice}</button>
              {' · '}
              <a className="cookie-link" href="/cookie-policy" target="_blank" rel="noopener noreferrer">Cookie Policy</a>
            </div>
          </div>

          <div className="consent-banner-actions">
            <button className="btn btn-primary" onClick={() => onAcceptAll(lang)}>{t.acceptAll}</button>
            <button className="btn btn-secondary" onClick={() => onRejectAll(lang)}>{t.rejectAll}</button>
            <button className="btn btn-ghost" onClick={() => setOpen(true)}>{t.moreOptions}</button>
          </div>
        </div>
      </div>
      {open && (
        <CookiePreferenceModal
          initialLang={lang}
          initialCats={prefs ?? undefined}
          onClose={() => setOpen(false)}
          onAcceptAll={() => onAcceptAll(lang)}
          onRejectAll={() => onRejectAll(lang)}
          onSave={(langCode, categories) => {
            onSave(langCode, categories)
            setOpen(false)
          }}
        />
      )}
      {showNotice && <NoticeScreen tenantCode="CRM_PORTAL" lang={lang} onClose={() => setShowNotice(false)} />}
    </>
  )
}