import { useRef, useState, useEffect } from 'react'
import { IconGlobe } from './icons'
import { LANGUAGES, LANG_STORAGE_KEY, type LanguageOption } from '../languages'

interface Props {
  value: string
  onChange: (code: string) => void
}

export function LanguageSelector({ value, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const selected = LANGUAGES.find((l) => l.code === value) || LANGUAGES[0]

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  return (
    <div className="lang-selector" ref={ref}>
      <button className="btn lang-btn" onClick={() => setOpen(!open)} title="Select language">
        <IconGlobe size={15} />
        <span className="lang-btn-label">{selected.nameNative}</span>
        <span className="lang-btn-code">{selected.code.toUpperCase()}</span>
      </button>
      {open && (
        <div className="lang-dropdown">
          {LANGUAGES.map((l) => (
            <button
              key={l.code}
              className={`lang-option${l.code === value ? ' active' : ''}`}
              onClick={() => { onChange(l.code); setOpen(false) }}
            >
              <span className="lang-option-native">{l.nameNative}</span>
              <span className="lang-option-en">{l.nameEn}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function usePersistedLang(): [string, (code: string) => void] {
  const [lang, setLang] = useState(() => localStorage.getItem(LANG_STORAGE_KEY) || 'en')
  const set = (code: string) => { localStorage.setItem(LANG_STORAGE_KEY, code); setLang(code) }
  return [lang, set]
}
