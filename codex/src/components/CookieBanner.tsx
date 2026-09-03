import { useState } from 'react'
import { consentApi } from '../api'

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
  const [cats, setCats] = useState<Record<string, boolean>>({ ...DEFAULT_CATS })
  const [saving, setSaving] = useState(false)

  const save = async (categories: Record<string, boolean>) => {
    setSaving(true)
    try {
      await consentApi.savePreferences(customerId, { lang: 'en', categories })
    } catch {
      /* preferences could not be saved - continue anyway */
    }
    onClose()
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

        <div className="consent-banner-actions">
          <button className="btn btn-primary" disabled={saving} onClick={() => save({ ...ALL_CATS })}>
            {saving ? 'Saving…' : 'Accept All'}
          </button>
          <button className="btn btn-primary" style={{ background: 'var(--muted, #64748b)', color: '#fff' }} disabled={saving} onClick={() => save({ necessary: true, functional: false, analytics: false, advertising: false })}>
            {saving ? 'Saving…' : 'Reject All'}
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
