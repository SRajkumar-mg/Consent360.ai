/**
 * R2-07: banner impression and decision beacons.
 *
 * Five KPIs in the DPDP gap register (K-01's impression half, K-02, K-03,
 * K-04, K-47) are marked "data not captured" for one reason: nothing records
 * that a notice was *shown*. Every other consent metric starts from a consent
 * row, which only exists once somebody said yes - so the platform could see
 * the people who accepted and was blind to everyone who declined or walked
 * away. s.6(1) requires consent to be free and specific, and you cannot show
 * that declining was a real option using only the records of people who did
 * not decline.
 *
 * These two calls are that missing denominator. Three rules govern them:
 *
 * 1. **They send no personal data.** No customer id, no email, no IP, no user
 *    agent - the server does not read those from the request either. Only the
 *    tenant, the banner version, the language, the purposes offered and taken,
 *    the page's path (no query string), and an opaque per-tab nonce.
 *
 * 2. **The nonce is this file's own, not the consent session id.** `session.ts`
 *    exports `getSessionId()`, which is sent as `ClientContext.session_id` on
 *    every consent decision and therefore sits in `consent_evidence` next to a
 *    customer id. Reusing it here would let anyone with database access hash it
 *    and join an "anonymous" impression straight back to a named principal.
 *    A separate nonce costs nothing - no KPI needs that join - and keeps this
 *    table genuinely non-identifying.
 *
 * 3. **They never block and never throw.** Fire-and-forget, unawaited, errors
 *    swallowed: a banner must render, and a decision must save, whether or not
 *    telemetry reaches the server. `keepalive` so a decision that navigates
 *    away still reports.
 *
 * "No choice" is deliberately not emitted. That KPI counts impressions with no
 * decision, and it has to stay an absence - a script that stopped running
 * cannot report that it stopped, so anything this file could send would only
 * measure the sessions that were still alive.
 */
const TENANT_CODE = 'CODEX'
const ENDPOINT = `/public/${TENANT_CODE}/banner-events`
const NONCE_KEY = 'codex_banner_nonce'

export type BannerSurface = 'COOKIE_BANNER' | 'CONSENT_GATE' | 'PREFERENCE_CENTRE' | 'NOTICE_PAGE'
export type BannerDecision = 'ACCEPT_ALL' | 'REJECT_ALL' | 'GRANULAR' | 'DISMISSED'

function randomNonce(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`
}

function bannerNonce(): string {
  try {
    let id = sessionStorage.getItem(NONCE_KEY)
    if (!id) {
      id = randomNonce()
      sessionStorage.setItem(NONCE_KEY, id)
    }
    return id
  } catch {
    return randomNonce()
  }
}

function post(body: Record<string, unknown>): void {
  try {
    void fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...body,
        session_id: bannerNonce(),
        // Path only. The server re-normalises it and collapses every
        // identifier-looking segment, but there is no reason to hand it a
        // query string in the first place - that is where a site would carry
        // a tracking id or an address.
        page_path: typeof window !== 'undefined' ? window.location.pathname : undefined,
      }),
      keepalive: true,
    }).catch(() => {})
  } catch {
    /* telemetry must never break the banner */
  }
}

export function emitNoticeShown(options: {
  bannerVersion: string
  language: string
  purposesOffered: string[]
  surface?: BannerSurface
}): void {
  post({
    event_type: 'NOTICE_SHOWN',
    surface: options.surface ?? 'COOKIE_BANNER',
    banner_version: options.bannerVersion,
    language: options.language,
    purposes_offered: options.purposesOffered,
  })
}

export function emitDecision(options: {
  decision: BannerDecision
  bannerVersion: string
  language: string
  purposesOffered: string[]
  purposesGranted: string[]
  surface?: BannerSurface
}): void {
  post({
    event_type: 'DECISION',
    decision: options.decision,
    surface: options.surface ?? 'COOKIE_BANNER',
    banner_version: options.bannerVersion,
    language: options.language,
    purposes_offered: options.purposesOffered,
    // The server refuses a grant for a purpose that was never offered, so an
    // inconsistent pair here is a bug that fails loudly rather than one that
    // silently pushes K-01's numerator above its own denominator.
    purposes_granted: options.purposesGranted.filter((code) => options.purposesOffered.includes(code)),
  })
}

/**
 * The banner's toggles are cookie *categories*; the platform records consent
 * against Purpose codes. This is the same mapping the server already applies
 * to the very same decision
 * (`backend/app/api/routes/crm.py::COOKIE_CATEGORY_TO_PURPOSE`), mirrored here
 * so K-01's impression-based per-purpose rate lands on the same vocabulary as
 * the decision-based one computed from consent rows. Without it the two halves
 * of one KPI would be keyed differently and could never be compared.
 */
const CATEGORY_TO_PURPOSE: Record<string, string> = {
  necessary: 'strictly_necessary',
  functional: 'functional',
  analytics: 'analytics',
  advertising: 'advertising',
}

export const OFFERED_PURPOSE_CODES = Object.values(CATEGORY_TO_PURPOSE)

/** Purpose codes for the categories currently switched on. */
export function grantedPurposeCodes(categories: Record<string, boolean>): string[] {
  return Object.entries(categories)
    .filter(([, on]) => on)
    .map(([key]) => CATEGORY_TO_PURPOSE[key] || key)
}

/** 'accept-all' | 'reject-all' | 'save-preferences' -> the decision recorded. */
export function decisionFor(uiControlId: string): BannerDecision {
  if (uiControlId === 'accept-all') return 'ACCEPT_ALL'
  if (uiControlId === 'reject-all') return 'REJECT_ALL'
  return 'GRANULAR'
}
