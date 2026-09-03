import { useState } from 'react'
import { BANNER_COPY } from '../copy'
import { COOKIE_CATEGORIES, CRM_LANG_KEY, LANGUAGES } from '../languages'

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
  onSave: (lang: string, categories: Record<string, boolean>) => void
  initialLang?: string
  initialCats?: Record<string, boolean>
}

export function CookiePreferenceModal({ onClose, onAcceptAll, onSave, initialLang, initialCats }: CookiePreferenceModalProps) {
  const [lang, setLang] = useState<string>(() => {
    if (initialLang && LANGUAGES.some((l) => l.code === initialLang && l.enabled)) return initialLang
    const saved = localStorage.getItem(CRM_LANG_KEY)
    return saved && LANGUAGES.some((l) => l.code === saved && l.enabled) ? saved : 'en'
  })
  const [cats, setCats] = useState<Record<string, boolean>>(() =>
    initialCats ? { ...DEFAULT_CATS, ...initialCats, necessary: true } : { ...DEFAULT_CATS }
  )
  const t = BANNER_COPY[lang] || BANNER_COPY.en

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal modal-cookie" role="dialog" aria-modal="true" aria-label={t.modalTitle} onClick={(e) => e.stopPropagation()}>
        <div className="modal-cookie-header">
          <div>
            <h2>{t.modalTitle}</h2>
            <p className="subtitle">{t.bannerText}</p>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label={t.close}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="form-group cookie-lang">
          <label>{t.chooseLanguage}</label>
          <select
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
                />
                <span className="slider" />
              </label>
            </div>
          ))}
        </div>

        <div className="modal-cookie-footer">
          <button className="btn btn-primary" onClick={onAcceptAll}>{t.acceptAll}</button>
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
    </div>
  )
}
