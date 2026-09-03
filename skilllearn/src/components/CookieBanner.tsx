import { useEffect, useState } from 'react'
import { consentApi, getErrorMessage } from '../api'

export const SKILL_LANG_KEY = 'skilllearn_lang'
const COOKIE_CONSENT_KEY = 'skilllearn_cookie_consent'

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
  id: string
  label: string
  desc: string
  locked?: boolean
}

const CATEGORIES: CategoryDef[] = [
  {
    id: 'necessary',
    label: 'Strictly necessary cookies',
    desc: 'Always active. Required for the platform to work — sign-in, security and consent records.',
    locked: true,
  },
  {
    id: 'functional',
    label: 'Functional cookies',
    desc: 'Remember your language, enrolled courses and learning preferences.',
  },
  {
    id: 'analytics',
    label: 'Performance & analytics cookies',
    desc: 'Help us understand how courses are used so we can improve the platform.',
  },
  {
    id: 'advertising',
    label: 'Advertising & social media cookies',
    desc: 'Used by partners to show relevant course recommendations and ads.',
  },
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

  const persist = async (categories: Record<string, boolean>) => {
    setSaving(true)
    setError('')
    try {
      await consentApi.savePreferences(customerId, { lang, categories })
      localStorage.setItem(COOKIE_CONSENT_KEY, JSON.stringify({ categories, at: Date.now() }))
      localStorage.setItem(SKILL_LANG_KEY, lang)
      onClose()
    } catch (err) {
      setError(getErrorMessage(err))
      setSaving(false)
    }
  }

  const acceptAll = () =>
    persist({ necessary: true, functional: true, analytics: true, advertising: true })

  const rejectAll = () =>
    persist({ necessary: true, functional: false, analytics: false, advertising: false })

  return (
    <div className="modal-overlay">
      <div className="modal-cookie" role="dialog" aria-modal="true" aria-label="Consent preferences">
        <div className="cookie-head">
          <span className="cookie-icon" aria-hidden="true">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#10b981" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2a10 10 0 1 0 10 10 4 4 0 0 1-5-5 4 4 0 0 1-5-5z" />
              <path d="M8.5 8.5h.01M16 15.5h.01M12 12h.01M7 14h.01M17 10h.01" />
            </svg>
          </span>
          <div>
            <h2 className="cookie-title">Your privacy matters at SkillLearn</h2>
            <p className="cookie-text">
              We and our partners use cookies to keep your learning experience secure, remember your
              preferences and understand how courses are used.
            </p>
          </div>
        </div>

        <div className="form-group cookie-lang">
          <label htmlFor="sl-consent-lang">Choose language</label>
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
                      {c.label}
                      {c.locked ? ' · Always active' : ''}
                    </div>
                    <div className="cookie-category-desc">{c.desc}</div>
                  </div>
                </div>
                <label className="switch">
                  <input
                    type="checkbox"
                    checked={cats[c.id] ?? false}
                    disabled={!!c.locked}
                    onChange={(e) => setCats({ ...cats, [c.id]: e.target.checked })}
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
            {saving ? 'Saving…' : 'Accept All'}
          </button>
          <button className="btn btn-primary" style={{ background: 'var(--muted, #64748b)', color: '#fff' }} onClick={rejectAll} disabled={saving}>
            {saving ? 'Saving…' : 'Reject All'}
          </button>
          {!expanded ? (
            <button className="btn btn-ghost" onClick={() => setExpanded(true)}>
              More Options
            </button>
          ) : (
            <>
              <button
                className="btn btn-ghost"
                disabled={saving}
                onClick={() => persist({ ...cats, necessary: true })}
              >
                Save Preferences
              </button>
              <button className="cookie-link" onClick={() => setExpanded(false)}>
                Back
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
