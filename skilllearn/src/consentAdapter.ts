import { COOKIE_CONSENT_KEY } from './components/CookieBanner'
import { isConsentExpired, onConsentChanged, type ConsentCategory, type ConsentGateAdapter } from './consentGate'

interface StoredConsent {
  categories?: Record<string, boolean>
  at?: number
}

function readStoredConsent(): StoredConsent | null {
  try {
    const raw = localStorage.getItem(COOKIE_CONSENT_KEY)
    return raw ? (JSON.parse(raw) as StoredConsent) : null
  } catch {
    return null
  }
}

// R2-10 / gap Q-05, Q-08: a decision on file that has passed CONSENT_TTL_DAYS
// is no longer treated as consent for anything beyond strictly necessary.
export const skilllearnConsentAdapter: ConsentGateAdapter = {
  hasConsent(category: ConsentCategory) {
    if (category === 'necessary') return true
    const stored = readStoredConsent()
    if (!stored || isConsentExpired(stored.at)) return false
    return stored.categories?.[category] === true
  },
  subscribe(cb) {
    return onConsentChanged(cb)
  },
}
