import { useEffect, useState } from 'react'
import { consentApi, getErrorMessage } from '../api'

export const CODEX_LANG_KEY = 'codex_lang'

const LANGUAGES = [
  { code: 'as', label: 'অসমীয়া (Assamese)' },
  { code: 'bn', label: 'বাংলা (Bengali)' },
  { code: 'brx', label: 'बरʼ (Bodo)' },
  { code: 'doi', label: 'डोगरी (Dogri)' },
  { code: 'gu', label: 'ગુજરાતી (Gujarati)' },
  { code: 'en', label: 'English' },
  { code: 'hi', label: 'हिन्दी (Hindi)' },
  { code: 'kn', label: 'ಕನ್ನಡ (Kannada)' },
  { code: 'ks', label: 'कॉशुर (Kashmiri)' },
  { code: 'kok', label: 'कोंकणी (Konkani)' },
  { code: 'mai', label: 'मैथिली (Maithili)' },
  { code: 'ml', label: 'മലയാളം (Malayalam)' },
  { code: 'mni', label: 'ꯃꯤꯇꯩꯂꯣꯟ (Manipuri)' },
  { code: 'mr', label: 'मराठी (Marathi)' },
  { code: 'ne', label: 'नेपाली (Nepali)' },
  { code: 'or', label: 'ଓଡ଼ିଆ (Odia)' },
  { code: 'pa', label: 'ਪੰਜਾਬੀ (Punjabi)' },
  { code: 'sa', label: 'संस्कृतम् (Sanskrit)' },
  { code: 'sat', label: 'ᱥᱟᱱᱛᱟᱲᱤ (Santali)' },
  { code: 'sd', label: 'سنڌي (Sindhi)' },
  { code: 'ta', label: 'தமிழ் (Tamil)' },
  { code: 'te', label: 'తెలుగు (Telugu)' },
  { code: 'ur', label: 'اردو (Urdu)' },
]

const CAT_COLORS: Record<string, string> = {
  necessary: '#64748b',
  functional: '#6366f1',
  analytics: '#fbbf24',
  advertising: '#f87171',
}

const CATEGORY_META = [
  { id: 'necessary', label: 'Strictly Necessary', desc: 'Essential for the site to function, such as keeping you signed in. These cannot be disabled.' },
  { id: 'functional', label: 'Functional', desc: 'Remembers your preferences like editor theme, language and layout.' },
  { id: 'analytics', label: 'Analytics', desc: 'Helps us understand how you use Codex so we can improve the platform.' },
  { id: 'advertising', label: 'Advertising', desc: 'Used to show relevant coding challenges and measure campaign effectiveness.' },
]

const DEFAULT_CATS: Record<string, boolean> = {
  necessary: true,
  functional: true,
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
  const [cats, setCats] = useState<Record<string, boolean>>({ ...DEFAULT_CATS })
  const [lang, setLang] = useState(() => localStorage.getItem(CODEX_LANG_KEY) ?? 'en')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    consentApi
      .getPreferences(customerId)
      .then((res) => {
        if (cancelled) return
        const p = (res.data?.preferences ?? {}) as { lang?: string; categories?: Record<string, boolean> }
        if (p.categories && Object.keys(p.categories).length) setCats({ ...DEFAULT_CATS, ...p.categories, necessary: true })
        if (p.lang && LANGUAGES.some((l) => l.code === p.lang)) setLang(p.lang)
      })
      .catch(() => {
        /* no saved preferences yet - use defaults */
      })
    return () => {
      cancelled = true
    }
  }, [customerId])

  const save = async (categories: Record<string, boolean>) => {
    setSaving(true)
    setError('')
    try {
      await consentApi.savePreferences(customerId, { lang, categories })
      localStorage.setItem(CODEX_LANG_KEY, lang)
      onClose()
    } catch (err) {
      setError(getErrorMessage(err))
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay">
      <div className="modal modal-cookie consent-banner-modal" role="dialog" aria-label="Cookie preferences">
        <div className="consent-banner-head">
          <span className="cookie-banner-icon" aria-hidden="true">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#8b80f9" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2a10 10 0 1 0 10 10 4 4 0 0 1-5-5 4 4 0 0 1-5-5z" />
              <path d="M8.5 8.5h.01M16 15.5h.01M12 12h.01M7 14h.01M17 10h.01" />
            </svg>
          </span>
          <div>
            <h2 className="consent-banner-title">We value your privacy</h2>
            <p className="consent-banner-text">
              Codex uses cookies to enhance your browsing experience, keep you signed in, and understand how the
              platform is used. You can accept all cookies or choose which categories to allow.
            </p>
          </div>
        </div>

        <div className="form-group cookie-lang">
          <label htmlFor="cx-consent-lang">Choose language</label>
          <select
            id="cx-consent-lang"
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

        {showOptions && (
          <div className="cookie-categories">
            {CATEGORY_META.map((c) => (
              <div className="cookie-category" key={c.id}>
                <div className="cookie-category-text">
                  <span className="cookie-cat-dot" style={{ background: CAT_COLORS[c.id], color: CAT_COLORS[c.id] }} />
                  <div>
                    <div className="cookie-category-label">
                      {c.label}
                      {c.id === 'necessary' && <span className="always-on-badge">Always active</span>}
                    </div>
                    <div className="cookie-category-desc">{c.desc}</div>
                  </div>
                </div>
                <label className="switch">
                  <input
                    type="checkbox"
                    checked={cats[c.id] ?? false}
                    disabled={c.id === 'necessary'}
                    onChange={(e) => setCats({ ...cats, [c.id]: e.target.checked })}
                  />
                  <span className="slider" />
                </label>
              </div>
            ))}
          </div>
        )}

        {error && <div className="alert alert-error">{error}</div>}

        <div className="consent-banner-actions">
          <button className="btn btn-primary" disabled={saving} onClick={() => save({ ...ALL_CATS })}>
            {saving ? 'Saving…' : 'Accept All'}
          </button>
          {showOptions ? (
            <button className="btn btn-ghost" disabled={saving} onClick={() => save({ ...cats, necessary: true })}>
              Save Preferences
            </button>
          ) : (
            <button className="btn btn-ghost" onClick={() => setShowOptions(true)}>
              More Options
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
