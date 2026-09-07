import { useEffect, useState } from 'react'
import { consentApi, getErrorMessage, noticeVersionFrom, publicApi, type PublicPrivacyContact, type PublicPurpose, type PublicRights } from '../api'
import { NoticeScreen, localize } from './NoticeScreen'
import { BANNER_COPY } from '../copy'
import { getSessionId } from '../session'
import { useFocusTrap } from '../hooks/useFocusTrap'
import { gpcSignalDetected, notifyConsentChanged } from '../consentGate'
import {
  OFFERED_PURPOSE_CODES, decisionFor, emitDecision, emitNoticeShown, grantedPurposeCodes,
} from '../bannerEvents'

export const SKILL_LANG_KEY = 'skilllearn_lang'
// R2-10: exported so consentAdapter.ts (the consent gate's storage adapter)
// reads the same local cache this banner writes to.
export const COOKIE_CONSENT_KEY = 'skilllearn_cookie_consent'
const BANNER_VERSION = 'skilllearn-cookie-banner-v1'
const TENANT_CODE = 'SKILLLEARN'
const SCREEN_ID = 'skilllearn-cookie-banner'

const LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'hi', label: 'हिन्दी (Hindi)' },
  { code: 'ta', label: 'தமிழ் (Tamil)' },
  { code: 'te', label: 'తెలుగు (Telugu)' },
  { code: 'kn', label: 'ಕನ್ನಡ (Kannada)' },
  { code: 'ml', label: 'മലയാളം (Malayalam)' },
  { code: 'bn', label: 'বাংলা (Bengali)' },
  { code: 'mr', label: 'मराठी (Marathi)' },
  { code: 'gu', label: 'ગુજરાતી (Gujarati)' },
  { code: 'pa', label: 'ਪੰਜਾਬੀ (Punjabi)' },
]

interface CategoryDef {
  id: 'necessary' | 'functional' | 'analytics' | 'advertising'
  labelKey: 'necessary' | 'functional' | 'analytics' | 'advertising'
  descKey: 'necessaryDesc' | 'functionalDesc' | 'analyticsDesc' | 'advertisingDesc'
  locked?: boolean
}

const CATEGORIES: CategoryDef[] = [
  { id: 'necessary', labelKey: 'necessary', descKey: 'necessaryDesc', locked: true },
  { id: 'functional', labelKey: 'functional', descKey: 'functionalDesc' },
  { id: 'analytics', labelKey: 'analytics', descKey: 'analyticsDesc' },
  { id: 'advertising', labelKey: 'advertising', descKey: 'advertisingDesc' },
]

const CAT_COLORS: Record<string, string> = {
  necessary: '#94a3b8',
  functional: '#10b981',
  analytics: '#f59e0b',
  advertising: '#fb7185',
}

const DEFAULT_CATS: Record<string, boolean> = {
  necessary: true,
  functional: false,
  analytics: false,
  advertising: false,
}

interface CookieBannerProps {
  customerId: number
  onClose: () => void
  lang?: string
}

export function CookieBanner({ customerId, onClose, lang: initialLang }: CookieBannerProps) {
  const [lang, setLang] = useState(() => initialLang ?? localStorage.getItem(SKILL_LANG_KEY) ?? 'en')
  const [expanded, setExpanded] = useState(false)
  const [cats, setCats] = useState<Record<string, boolean>>({ ...DEFAULT_CATS })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [showNotice, setShowNotice] = useState(false)
  const [noticeVersion, setNoticeVersion] = useState<number | undefined>(undefined)

  // Rule 3 notice elements, fetched once and rendered directly on the banner so the
  // notice is *seen* before a decision, not merely reachable via "View full notice".
  const [noticePurposes, setNoticePurposes] = useState<PublicPurpose[]>([])
  const [noticeContact, setNoticeContact] = useState<PublicPrivacyContact | null>(null)
  const [noticeRights, setNoticeRights] = useState<PublicRights | null>(null)

  const t = BANNER_COPY[lang] || BANNER_COPY.en

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
    let cancelled = false
    consentApi
      .getPreferences(customerId)
      .then((res) => {
        if (cancelled) return
        const p = (res.data?.preferences ?? {}) as { lang?: string; categories?: Record<string, boolean> }
        if (p.categories && Object.keys(p.categories).length) {
          setCats({ ...DEFAULT_CATS, ...p.categories, necessary: true })
        }
        if (p.lang && LANGUAGES.some((l) => l.code === p.lang)) setLang(p.lang)
      })
      .catch(() => {
        /* no saved preferences yet - use defaults */
      })
    return () => {
      cancelled = true
    }
  }, [customerId])

  const persist = async (categories: Record<string, boolean>, uiControlId: string) => {
    setSaving(true)
    setError('')
    // R2-07: report the decision before the save below. It carries no
    // identifier, so it is not evidence of this principal's consent - it is
    // the denominator for K-02/K-03/K-47, which must count a decision whose
    // save then fails exactly as much as one that succeeds. This banner only
    // caches locally on success, so reporting afterwards would have dropped
    // every decision the server refused.
    emitDecision({
      decision: decisionFor(uiControlId),
      bannerVersion: BANNER_VERSION,
      language: lang,
      purposesOffered: OFFERED_PURPOSE_CODES,
      purposesGranted: grantedPurposeCodes(categories),
    })
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
      localStorage.setItem(COOKIE_CONSENT_KEY, JSON.stringify({ categories, at: Date.now() }))
      localStorage.setItem(SKILL_LANG_KEY, lang)
      notifyConsentChanged()
      onClose()
    } catch (err) {
      setError(getErrorMessage(err))
      setSaving(false)
    }
  }

  const acceptAll = () =>
    persist({ necessary: true, functional: true, analytics: true, advertising: true }, 'accept-all')

  // No onEscape: this top-level banner requires an affirmative decision, so
  // there is no neutral "just close it" path — Escape must not stand in for one.
  const dialogRef = useFocusTrap<HTMLDivElement>(true)

  return (
    <div className="modal-overlay">
      <div
        ref={dialogRef}
        className="modal-cookie"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sl-cookie-banner-title"
        aria-describedby="sl-cookie-banner-text sl-cookie-banner-notice-essential sl-cookie-banner-notice-items"
      >
        <div className="cookie-head">
          <span className="cookie-icon" aria-hidden="true">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#10b981" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2a10 10 0 1 0 10 10 4 4 0 0 1-5-5 4 4 0 0 1-5-5z" />
              <path d="M8.5 8.5h.01M16 15.5h.01M12 12h.01M7 14h.01M17 10h.01" />
            </svg>
          </span>
          <div>
            <h2 className="cookie-title" id="sl-cookie-banner-title">{t.bannerTitle}</h2>
            <p className="cookie-text" id="sl-cookie-banner-text">{t.bannerText}</p>
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
          <label htmlFor="sl-consent-lang">{t.chooseLanguage}</label>
          <select
            id="sl-consent-lang"
            className="input"
            value={lang}
            onChange={(e) => setLang(e.target.value)}
          >
            {LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
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
          <div className="notice-summary-items" id="sl-cookie-banner-notice-items">
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
          <div className="notice-summary-essential" id="sl-cookie-banner-notice-essential">
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

        {expanded && (
          <div className="cookie-categories">
            {CATEGORIES.map((c) => (
              <div className="cookie-category" key={c.id}>
                <div className="cookie-category-text">
                  <span
                    className="cookie-cat-dot"
                    style={{ background: CAT_COLORS[c.id], color: CAT_COLORS[c.id] }}
                  />
                  <div>
                    <div className="cookie-category-label">
                      {t[c.labelKey]}
                      {c.locked ? ` · ${t.alwaysActive}` : ''}
                    </div>
                    <div className="cookie-category-desc">{t[c.descKey]}</div>
                  </div>
                </div>
                <label className="switch">
                  <input
                    type="checkbox"
                    checked={cats[c.id] ?? false}
                    disabled={!!c.locked}
                    onChange={(e) => setCats({ ...cats, [c.id]: e.target.checked })}
                    aria-label={`${t[c.labelKey]}${c.locked ? ` (${t.alwaysActive})` : ''} — ${t[c.descKey]}`}
                  />
                  <span className="slider" />
                </label>
              </div>
            ))}
          </div>
        )}

        {error && <div className="alert alert-error">{error}</div>}

        <div className="cookie-actions">
          <button className="btn btn-primary" onClick={acceptAll} disabled={saving}>
            {saving ? t.saving : t.acceptAll}
          </button>
          <button
            className="btn btn-secondary"
            disabled={saving}
            onClick={() => persist({ ...DEFAULT_CATS }, 'reject-all')}
          >
            {t.rejectAll}
          </button>
          {!expanded ? (
            <button className="btn btn-ghost" onClick={() => setExpanded(true)}>
              {t.moreOptions}
            </button>
          ) : (
            <>
              <button
                className="btn btn-ghost"
                disabled={saving}
                onClick={() => persist({ ...cats, necessary: true }, 'save-preferences')}
              >
                {t.savePreferences}
              </button>
              <button className="cookie-link" onClick={() => setExpanded(false)}>
                {t.back}
              </button>
            </>
          )}
        </div>
      </div>
      {showNotice && <NoticeScreen tenantCode={TENANT_CODE} lang={lang} onClose={() => setShowNotice(false)} />}
    </div>
  )
}
