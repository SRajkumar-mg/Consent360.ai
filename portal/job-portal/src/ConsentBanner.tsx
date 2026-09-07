import { useEffect, useState } from 'react'
import { useConsent } from './useConsent'
import { NoticeScreen } from './NoticeScreen'
import { CookiePolicyScreen } from './CookiePolicyScreen'
import { publicApi, localize, type PublicPrivacyContact, type PublicPurpose, type PublicRights } from './publicApi'
import { BANNER_COPY } from './copy'
import { CAREERHUB_LANG_KEY, LANGUAGES } from './languages'
import { useFocusTrap } from './useFocusTrap'
import { gpcSignalDetected } from './consentGate'

const TENANT_CODE = 'CAREER_HUB'

interface ConsentBannerProps {
  purposes: { code: string; name: string; description: string; granted: boolean }[]
  onGrant: (code: string) => void
  onWithdraw: (code: string) => void
  onAcceptAll: () => void
  onRejectAll: () => void
  onSave: () => void
  onClose: () => void
}

export function ConsentBanner({
  purposes,
  onGrant,
  onWithdraw,
  onAcceptAll,
  onRejectAll,
  onSave,
  onClose,
}: ConsentBannerProps) {
  const [showNotice, setShowNotice] = useState(false)
  const [showCookiePolicy, setShowCookiePolicy] = useState(false)
  const [lang, setLang] = useState(() => localStorage.getItem(CAREERHUB_LANG_KEY) || 'en')
  const [noticePurposes, setNoticePurposes] = useState<PublicPurpose[]>([])
  const [noticeContact, setNoticeContact] = useState<PublicPrivacyContact | null>(null)
  const [noticeRights, setNoticeRights] = useState<PublicRights | null>(null)
  const t = BANNER_COPY[lang] || BANNER_COPY.en

  useEffect(() => {
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = '' }
  }, [])

  useEffect(() => {
    publicApi.purposes(TENANT_CODE).then(setNoticePurposes).catch(() => setNoticePurposes([]))
    publicApi.privacyContact(TENANT_CODE).then(setNoticeContact).catch(() => setNoticeContact(null))
    publicApi.rights(TENANT_CODE).then(setNoticeRights).catch(() => setNoticeRights(null))
  }, [])

  const setLanguage = (next: string) => {
    localStorage.setItem(CAREERHUB_LANG_KEY, next)
    setLang(next)
  }

  // The toggle list below is driven by /portal/overview (per-purpose grant/withdraw
  // state); localize its labels against the public, translated purpose list by
  // matching purpose code, falling back to the English text useConsent already has.
  const localizedName = (code: string, fallback: string) => {
    const match = noticePurposes.find((p) => p.code === code)
    return match ? localize(match, lang).name : fallback
  }
  const localizedDescription = (code: string, fallback: string) => {
    const match = noticePurposes.find((p) => p.code === code)
    return match ? localize(match, lang).description : fallback
  }

  // Escape mirrors the existing click-outside-to-dismiss affordance on the overlay
  // below — it is already a neutral "close without changing anything" action here,
  // not a stand-in for a decision.
  const dialogRef = useFocusTrap<HTMLDivElement>(true, onClose)

  return (
    <div className="consent-overlay" onClick={onClose}>
      <div
        ref={dialogRef}
        className="consent-banner"
        role="dialog"
        aria-modal="true"
        aria-labelledby="jp-consent-banner-title"
        aria-describedby="jp-consent-banner-text jp-consent-banner-notice-essential jp-consent-banner-notice-items"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="consent-header">
          <h2 id="jp-consent-banner-title">{t.bannerTitle}</h2>
          <p id="jp-consent-banner-text">{t.bannerText}</p>
        </div>

        <div className="consent-body">
          {gpcSignalDetected() && (
            // R2-10 / gap Q-07: Global Privacy Control is a live opt-out signal from
            // the browser itself - optional purposes stay off regardless of any
            // choice made here for as long as it is present. English-only: this is
            // a transparency note, not one of the Rule 3 notice elements above,
            // which are already rendered in every scheduled language.
            <div className="alert alert-info">
              Your browser is sending a Global Privacy Control signal. We treat it as an objection to optional
              processing — functional, analytics and advertising stay off no matter what you choose below.
            </div>
          )}

          <div className="form-group" style={{ marginBottom: 14 }}>
            <label htmlFor="ch-consent-lang">{t.chooseLanguage}</label>
            <select
              id="ch-consent-lang"
              value={lang}
              onChange={(e) => setLanguage(e.target.value)}
            >
              {LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.nameNative} ({l.nameEn})
                </option>
              ))}
            </select>
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
            <div className="notice-summary-items" id="jp-consent-banner-notice-items">
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
            <div className="notice-summary-essential" id="jp-consent-banner-notice-essential">
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
              <button className="cookie-link" onClick={() => setShowCookiePolicy(true)} type="button">Cookie Policy</button>
            </div>
          </div>

          {purposes.map((p) => (
            <div key={p.code} className="consent-purpose">
              <div className="consent-purpose-info">
                <h4>{localizedName(p.code, p.name)}</h4>
                <p>{localizedDescription(p.code, p.description)}</p>
              </div>
              <label className="consent-toggle">
                <input
                  type="checkbox"
                  checked={p.granted}
                  onChange={() => (p.granted ? onWithdraw(p.code) : onGrant(p.code))}
                  aria-label={`${localizedName(p.code, p.name)} — ${localizedDescription(p.code, p.description)}`}
                />
                <span className="slider" />
              </label>
            </div>
          ))}
          {purposes.length === 0 && (
            <div style={{ textAlign: 'center', padding: 24, color: 'var(--text-muted)', fontSize: 13 }}>
              {t.noPurposes}
            </div>
          )}
        </div>

        <div className="consent-footer">
          <button className="btn btn-success" onClick={onRejectAll}>{t.rejectAll}</button>
          <button className="btn btn-ghost" onClick={onSave}>{t.savePreferences}</button>
          <button className="btn btn-success" onClick={onAcceptAll}>{t.acceptAll}</button>
        </div>
      </div>
      {showNotice && <NoticeScreen tenantCode={TENANT_CODE} lang={lang} onClose={() => setShowNotice(false)} />}
      {showCookiePolicy && <CookiePolicyScreen tenantCode={TENANT_CODE} onClose={() => setShowCookiePolicy(false)} />}
    </div>
  )
}
