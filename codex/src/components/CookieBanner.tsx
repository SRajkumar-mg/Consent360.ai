import { useEffect, useState } from 'react'
import { consentApi, noticeVersionFrom, publicApi, type PublicPrivacyContact, type PublicPurpose, type PublicRights } from '../api'
import { NoticeScreen, localize } from './NoticeScreen'
import { BANNER_COPY } from '../copy'
import { CODEX_LANG_KEY, COOKIE_CONSENT_KEY, LANGUAGES } from '../languages'
import { getSessionId } from '../session'
import { useFocusTrap } from '../hooks/useFocusTrap'
import { gpcSignalDetected, notifyConsentChanged } from '../consentGate'
import {
  OFFERED_PURPOSE_CODES, decisionFor, emitDecision, emitNoticeShown, grantedPurposeCodes,
} from '../bannerEvents'

const BANNER_VERSION = 'codex-cookie-banner-v1'
const TENANT_CODE = 'CODEX'
const SCREEN_ID = 'codex-cookie-banner'

const CAT_COLORS: Record<string, string> = {
  necessary: '#64748b',
  functional: '#6366f1',
  analytics: '#fbbf24',
  advertising: '#f87171',
}

const CATEGORY_IDS = ['necessary', 'functional', 'analytics', 'advertising'] as const

const DEFAULT_CATS: Record<string, boolean> = {
  necessary: true,
  functional: false,
  analytics: false,
  advertising: false,
}

const ALL_CATS: Record<string, boolean> = {
  necessary: true,
  functional: true,
  analytics: true,
  advertising: true,
}

interface CookieBannerProps {
  customerId: number
  onClose: () => void
}

export function CookieBanner({ customerId, onClose }: CookieBannerProps) {
  const [showOptions, setShowOptions] = useState(false)
  const [showNotice, setShowNotice] = useState(false)
  const [cats, setCats] = useState<Record<string, boolean>>({ ...DEFAULT_CATS })
  const [saving, setSaving] = useState(false)
  const [noticeVersion, setNoticeVersion] = useState<number | undefined>(undefined)
  const [lang, setLang] = useState<string>(() => localStorage.getItem(CODEX_LANG_KEY) || 'en')

  // Rule 3 notice elements, fetched once and rendered directly on the banner so the
  // notice is *seen* before a decision, not merely reachable via "View full notice".
  const [noticePurposes, setNoticePurposes] = useState<PublicPurpose[]>([])
  const [noticeContact, setNoticeContact] = useState<PublicPrivacyContact | null>(null)
  const [noticeRights, setNoticeRights] = useState<PublicRights | null>(null)

  const t = BANNER_COPY[lang] || BANNER_COPY.en

  // R2-07: the impression. Fires once, when the banner is actually painted -
  // not on page load and not on decision - because it is the denominator for
  // K-02's ignore rate, and a notice that was never rendered was never
  // ignored. `lang` is read at mount, so this records the language the notice
  // first appeared in; the decision event carries whatever it was finally
  // taken in.
  useEffect(() => {
    emitNoticeShown({
      bannerVersion: BANNER_VERSION,
      language: lang,
      purposesOffered: OFFERED_PURPOSE_CODES,
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    publicApi
      .purposes(TENANT_CODE)
      .then((res) => {
        setNoticeVersion(noticeVersionFrom(res.data))
        setNoticePurposes(res.data)
      })
      .catch(() => {
        setNoticeVersion(undefined)
        setNoticePurposes([])
      })
    publicApi.privacyContact(TENANT_CODE).then((r) => setNoticeContact(r.data)).catch(() => setNoticeContact(null))
    publicApi.rights(TENANT_CODE).then((r) => setNoticeRights(r.data)).catch(() => setNoticeRights(null))
  }, [])

  const setLanguage = (next: string) => {
    localStorage.setItem(CODEX_LANG_KEY, next)
    setLang(next)
  }

  const save = async (categories: Record<string, boolean>, uiControlId: string) => {
    setSaving(true)
    // R2-07: report the decision before the save below. It carries no
    // identifier, so it is not evidence of this principal's consent - it is
    // the denominator for K-02/K-03/K-47, which must count a decision whose
    // save then fails exactly as much as one that succeeds.
    emitDecision({
      decision: decisionFor(uiControlId),
      bannerVersion: BANNER_VERSION,
      language: lang,
      purposesOffered: OFFERED_PURPOSE_CODES,
      purposesGranted: grantedPurposeCodes(categories),
    })
    // R2-10: cache the decision locally (with a timestamp for TTL/re-prompt)
    // so the consent gate can enforce it on this and future page loads
    // without waiting on a network round trip, then tell the gate to
    // re-evaluate any tags waiting on this category right now.
    localStorage.setItem(COOKIE_CONSENT_KEY, JSON.stringify({ categories, at: Date.now() }))
    notifyConsentChanged()
    try {
      await consentApi.savePreferences(customerId, {
        lang,
        categories,
        context: {
          language: lang,
          notice_version: noticeVersion,
          banner_version: BANNER_VERSION,
          ui_control_id: uiControlId,
          session_id: getSessionId(),
          screen_id: SCREEN_ID,
          gpc_signal: gpcSignalDetected(),
        },
      })
    } catch {
      /* preferences could not be saved - continue anyway */
    }
    onClose()
  }

  const categoryLabel: Record<(typeof CATEGORY_IDS)[number], { label: string; desc: string }> = {
    necessary: { label: t.necessary, desc: t.necessaryDesc },
    functional: { label: t.functional, desc: t.functionalDesc },
    analytics: { label: t.analytics, desc: t.analyticsDesc },
    advertising: { label: t.advertising, desc: t.advertisingDesc },
  }

  // No onEscape: this top-level banner requires an affirmative decision, so
  // there is no neutral "just close it" path — Escape must not stand in for one.
  const dialogRef = useFocusTrap<HTMLDivElement>(true)

  return (
    <div className="modal-overlay">
      <div
        ref={dialogRef}
        className="modal modal-cookie consent-banner-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="codex-cookie-banner-title"
        aria-describedby="codex-cookie-banner-text codex-cookie-banner-notice-essential codex-cookie-banner-notice-items"
      >
        <div className="consent-banner-head">
          <span className="cookie-banner-icon" aria-hidden="true">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#8b80f9" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2a10 10 0 1 0 10 10 4 4 0 0 1-5-5 4 4 0 0 1-5-5z" />
              <path d="M8.5 8.5h.01M16 15.5h.01M12 12h.01M7 14h.01M17 10h.01" />
            </svg>
          </span>
          <div>
            <h2 className="consent-banner-title" id="codex-cookie-banner-title">{t.bannerTitle}</h2>
            <p className="consent-banner-text" id="codex-cookie-banner-text">{t.bannerText}</p>
          </div>
        </div>

        {gpcSignalDetected() && (
          // R2-10 / gap Q-07: Global Privacy Control is a live opt-out signal from
          // the browser itself - optional categories stay off regardless of any
          // choice made here for as long as it is present. English-only: this is
          // a transparency note, not one of the Rule 3 notice elements the rest
          // of this banner already renders in every scheduled language.
          <div className="alert alert-info" style={{ marginBottom: 12, fontSize: 12.5 }}>
            Your browser is sending a Global Privacy Control signal. We treat it as an objection to optional
            cookies — analytics and advertising stay off no matter what you choose below — and record it
            alongside your decision, not only in this browser.
          </div>
        )}

        <div className="form-group cookie-lang">
          <label htmlFor="codex-consent-lang">{t.chooseLanguage}</label>
          <select
            id="codex-consent-lang"
            className="input"
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
          <div className="notice-summary-items" id="codex-cookie-banner-notice-items">
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
          <div className="notice-summary-essential" id="codex-cookie-banner-notice-essential">
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

        {showOptions && (
          <div className="cookie-categories">
            {CATEGORY_IDS.map((id) => (
              <div className="cookie-category" key={id}>
                <div className="cookie-category-text">
                  <span className="cookie-cat-dot" style={{ background: CAT_COLORS[id], color: CAT_COLORS[id] }} />
                  <div>
                    <div className="cookie-category-label">
                      {categoryLabel[id].label}
                      {id === 'necessary' && <span className="always-on-badge">{t.alwaysActive}</span>}
                    </div>
                    <div className="cookie-category-desc">{categoryLabel[id].desc}</div>
                  </div>
                </div>
                <label className="switch">
                  <input
                    type="checkbox"
                    checked={cats[id] ?? false}
                    disabled={id === 'necessary'}
                    onChange={(e) => setCats({ ...cats, [id]: e.target.checked })}
                    aria-label={`${categoryLabel[id].label}${id === 'necessary' ? ` (${t.alwaysActive})` : ''} — ${categoryLabel[id].desc}`}
                  />
                  <span className="slider" />
                </label>
              </div>
            ))}
          </div>
        )}

        <div className="consent-banner-actions">
          <button className="btn btn-primary" disabled={saving} onClick={() => save({ ...ALL_CATS }, 'accept-all')}>
            {saving ? t.saving : t.acceptAll}
          </button>
          <button className="btn btn-secondary" disabled={saving} onClick={() => save({ ...DEFAULT_CATS }, 'reject-all')}>
            {t.rejectAll}
          </button>
          {showOptions ? (
            <button className="btn btn-ghost" disabled={saving} onClick={() => save({ ...cats, necessary: true }, 'save-preferences')}>
              {t.savePreferences}
            </button>
          ) : (
            <button className="btn btn-ghost" onClick={() => setShowOptions(true)}>
              {t.moreOptions}
            </button>
          )}
        </div>
      </div>
      {showNotice && <NoticeScreen tenantCode={TENANT_CODE} lang={lang} onClose={() => setShowNotice(false)} />}
    </div>
  )
}
