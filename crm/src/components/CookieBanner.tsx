import { useEffect, useState } from 'react'
import { CookiePreferenceModal } from './CookiePreferenceModal'
import { consentApi } from '../api'
import { BANNER_COPY } from '../copy'
import { CRM_LANG_KEY, LANGUAGES } from '../languages'

interface CookieBannerProps {
  customerId: number | null
  onAcceptAll: (lang: string) => void
  onSave: (lang: string, categories: Record<string, boolean>) => void
}

export function CookieBanner({ customerId, onAcceptAll, onSave }: CookieBannerProps) {
  const [open, setOpen] = useState(false)
  const [lang, setLang] = useState<string>(() => {
    const saved = localStorage.getItem(CRM_LANG_KEY)
    return saved && LANGUAGES.some((l) => l.code === saved && l.enabled) ? saved : 'en'
  })
  const [prefs, setPrefs] = useState<Record<string, boolean> | null>(null)

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

  const setLanguage = (next: string) => {
    if (!LANGUAGES.some((l) => l.code === next && l.enabled)) return
    localStorage.setItem(CRM_LANG_KEY, next)
    setLang(next)
  }

  return (
    <>
      <div className="modal-overlay">
        <div className="modal modal-cookie consent-banner-modal" role="dialog" aria-label="Cookie preferences">
          <div className="consent-banner-head">
            <span className="cookie-banner-icon" aria-hidden="true">
              <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#6d5df6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2a10 10 0 1 0 10 10 4 4 0 0 1-5-5 4 4 0 0 1-5-5z" />
                <path d="M8.5 8.5h.01M16 15.5h.01M12 12h.01M7 14h.01M17 10h.01" />
              </svg>
            </span>
            <div>
              <h2 className="consent-banner-title">{t.bannerTitle}</h2>
              <p className="consent-banner-text">{t.bannerText}</p>
            </div>
          </div>

          <div className="form-group cookie-lang">
            <label>{t.chooseLanguage}</label>
            <select className="input" value={lang} onChange={(e) => setLanguage(e.target.value)}>
              {LANGUAGES.map((l) => (
                <option key={l.code} value={l.code} disabled={!l.enabled}>
                  {l.nameNative} ({l.nameEn})
                </option>
              ))}
            </select>
            <div className="cookie-lang-note">{t.languageNote}</div>
          </div>

          <div className="consent-banner-actions">
            <button className="btn btn-primary" onClick={() => onAcceptAll(lang)}>{t.acceptAll}</button>
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
          onSave={(langCode, categories) => {
            onSave(langCode, categories)
            setOpen(false)
          }}
        />
      )}
    </>
  )
}