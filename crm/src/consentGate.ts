// Consent gate utility (R2-10 / R2-07).
// Reads the stored consent state (COOKIE_CONSENT_KEY) and provides an
// enforcement API for gating non-essential scripts/tags, plus banner-event
// instrumentation and Global Privacy Control (GPC) handling.
//
// Storage shape (matching CookieBanner / UsersPage):
//   { all: boolean, categories: { necessary, functional, analytics, advertising },
//     at: number, source?: 'banner'|'gpc' }

export type ConsentCategory = 'necessary' | 'functional' | 'analytics' | 'advertising'

export const COOKIE_CONSENT_KEY = 'crm_cookie_consent'
export const GPC_KEY = 'crm_gpc_objection'

export interface ConsentState {
  all: boolean
  categories: Record<ConsentCategory, boolean>
  at: number
  source?: 'banner' | 'gpc'
}

export interface GpcSignal {
  present: boolean
  enabled: boolean
}

// ---- Global Privacy Control inspection -------------------------------
export function readGpcSignal(): GpcSignal {
  const nav = navigator as Navigator & {
    globalPrivacyControl?: boolean
  }
  if (typeof nav.globalPrivacyControl === 'boolean') {
    return { present: true, enabled: nav.globalPrivacyControl }
  }
  return { present: false, enabled: false }
}

// ---- State access -----------------------------------------------------
export function readStoredConsent(): ConsentState | null {
  const raw = localStorage.getItem(COOKIE_CONSENT_KEY)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as ConsentState
    if (!parsed || typeof parsed.categories !== 'object') return null
    return parsed
  } catch {
    return null
  }
}

export function applyGpcDefault(): ConsentState {
  const gpc = readGpcSignal()
  localStorage.setItem(GPC_KEY, String(gpc.enabled ? 1 : 0))
  if (!gpc.enabled) return readStoredConsent() as ConsentState
  // GPC present and enabled => implicit reject-all until the principal
  // later makes an explicit choice from the banner.
  return {
    all: false,
    categories: { necessary: true, functional: false, analytics: false, advertising: false },
    at: Date.now(),
    source: 'gpc',
  }
}

export function isConsented(category: ConsentCategory): boolean {
  const state = readStoredConsent()
  if (!state) return category === 'necessary'
  return state.categories[category] === true
}

export function getConsentState(): ConsentState {
  const stored = readStoredConsent()
  if (stored) return stored
  const gpc = applyGpcDefault()
  return gpc ? gpc : { all: true, categories: { necessary: true, functional: true, analytics: true, advertising: true }, at: Date.now() }
}

// ---- Global object / event for page scripts ---------------------------
declare global {
  interface Window {
    consent360?: {
      state: () => { all: boolean; categories: Record<ConsentCategory, boolean>; at: number }
      isConsented: (category: ConsentCategory) => boolean
    }
  }
}

export function initConsentGate(): void {
  const gpc = readGpcSignal()
  if (gpc.present) {
    localStorage.setItem(GPC_KEY, String(gpc.enabled ? 1 : 0))
  }
  syncConsentState()
  window.addEventListener('storage', (e) => {
    if (e.key === COOKIE_CONSENT_KEY) syncConsentState()
  })
}

// Refresh the global object + notify listeners after an explicit local change
// (e.g. accept/reject/save from the banner). Lightweight: no listener churn.
export function syncConsentState(): void {
  const state = getConsentState()
  window.consent360 = {
    state: () => state,
    isConsented: (category) => state.categories[category] === true,
  }
  window.dispatchEvent(new CustomEvent('consent360:change', { detail: state }))
}

// ---- Banner event instrumentation (R2-07) ------------------------------
export type BannerEventType = 'NOTICE_SHOWN' | 'ACCEPT_ALL' | 'REJECT_ALL' | 'GRANULAR_DECISION'

export function emitBannerEvent(eventType: BannerEventType, opts?: {
  sessionId?: string
  language?: string
  bannerVersion?: string
  category?: string
}): void {
  const sessionId = opts?.sessionId || sessionStorage.getItem('crm_session') || ''
  const payload = {
    event_type: eventType,
    session_id: sessionId,
    language: opts?.language || localStorage.getItem('crm_lang') || 'en',
    banner_version: opts?.bannerVersion || '1.0',
    control_id: opts?.category || '',
  }
  // Fire-and-forget; never block consent flow on telemetry.
  fetch('/cmp/banner-events', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).catch(() => {})
}

// ---- Loading gate helper ----------------------------------------------
// Call this before loading any non-essential third-party script/tag. It
// returns true only if the category is consented, so callers can decide to
// inject tags when true (and drop/reset them on the consent360:change event).
export function gateCategory(category: Exclude<ConsentCategory, 'necessary'>): boolean {
  return isConsented(category)
}

// Subscribe to consent changes so site code can load/unload tags reactively.
export function onConsentChange(
  category: Exclude<ConsentCategory, 'necessary'>,
  handler: (enabled: boolean) => void,
): () => void {
  const listener = (e: Event) => {
    const detail = (e as CustomEvent<ConsentState>).detail
    if (detail && typeof detail.categories === 'object') {
      handler(detail.categories[category] === true)
    }
  }
  window.addEventListener('consent360:change', listener)
  return () => window.removeEventListener('consent360:change', listener)
}
