import { isConsentExpired, onConsentChanged, type ConsentCategory, type ConsentGateAdapter } from './consentGate'

const STORAGE_KEY = 'careerhub_consent'

interface StoredConsent {
  purposes?: { code: string; granted: boolean }[]
  consented?: boolean
  decidedAt?: number
}

function readStoredConsent(): StoredConsent | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as StoredConsent) : null
  } catch {
    return null
  }
}

// R2-10 / gap Q-05, Q-08: CareerHub's purposes are backend-driven (the tenant's
// actual Purpose codes happen to line up with the shared "necessary /
// functional / analytics / advertising" taxonomy used elsewhere), and a
// decision on file that has passed CONSENT_TTL_DAYS is no longer treated as
// consent for anything beyond strictly necessary.
export const careerhubConsentAdapter: ConsentGateAdapter = {
  hasConsent(category: ConsentCategory) {
    if (category === 'necessary') return true
    const stored = readStoredConsent()
    if (!stored || !stored.consented || isConsentExpired(stored.decidedAt)) return false
    return stored.purposes?.find((p) => p.code === category)?.granted === true
  },
  subscribe(cb) {
    return onConsentChanged(cb)
  },
}
