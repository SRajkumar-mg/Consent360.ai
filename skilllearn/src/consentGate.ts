// R2-10 / gap Q-04, Q-05, Q-07, Q-08: client-side cookie/tag enforcement.
//
// This is deliberately built as a small, dependency-free module inside the
// demo site rather than a new package under cms/sdk - the real @consent360/sdk
// package lives outside this lane's file scope. The GPC signal detected here
// (gpcSignalDetected(), below) is sent as ClientContext.gpc_signal on every
// grant/withdraw/preferences call and durably recorded server-side -
// ConsentEvidence.gpc_signal is the server's own observed Sec-GPC header;
// this function's claimed value is kept separately in
// ConsentEvidence.details.claimed_gpc_signal, never collapsed into the
// server-observed one (see backend/app/schemas/schemas.py::ClientContext
// .gpc_signal). The TTL/re-prompt event still has nowhere to go but this
// browser's own storage - ClientContext has no reprompt_reason field - so
// that part remains visible only on the visitor's own device (see the
// comments below).
//
// How a gated tag is authored: instead of a normal executable <script>, mark
// it inert so the browser never runs it on its own, and tag it with the
// category it needs consent for:
//   <script type="text/plain" data-consent-category="analytics" data-src="https://.../ga.js"></script>
//   <script type="text/plain" data-consent-category="analytics">inline code</script>
// activateGatedTags() finds every such tag and, once (and only once) the
// visitor has actually consented to that category, replaces it with a real,
// executable <script> - the same mechanism commercial CMPs (OneTrust,
// Cookiebot, etc.) use, because a browser never executes a script whose
// type it does not recognise until something deliberately re-creates it.
//
// WHAT THIS DOES NOT DO (read this before treating it as a security
// boundary): this module can only gate tags that were authored using the
// type="text/plain" convention above, and its K-48 scan (scanForUngatedTags
// / startTagScanMonitor, further down) can only recognise a fixed list of
// known tracker hostnames in whatever the DOM (or a wrapped
// navigator.sendBeacon) exposes to page JS at the moment it runs. It cannot
// stop, and does not claim to stop, a determined or already-compromised
// third-party script from firing a request directly - to an unlisted host,
// via an API this module does not wrap, or before this module has had a
// chance to run at all. The only real enforcement boundary for that class of
// problem is a server-set Content-Security-Policy (script-src/connect-src)
// header, which is outside a client-side module's reach. Say this plainly
// wherever this mechanism is described to a visitor or auditor (see the
// cookie policy pages).

export type ConsentCategory = 'necessary' | 'functional' | 'analytics' | 'advertising'

export interface ConsentGateAdapter {
  /** True only if the visitor has an actual, current (non-expired) decision on file for this category. */
  hasConsent(category: ConsentCategory): boolean
  /** Register a callback to re-run whenever a decision is saved; returns an unsubscribe function. */
  subscribe(cb: () => void): () => void
}

// Q-08: industry practice (IAB TCF policy) is to re-prompt at least every 13
// months; this demo uses a shorter, clearly-named window so expiry is easy to
// exercise, but the mechanism is identical either way.
export const CONSENT_TTL_DAYS = 180
const CONSENT_CHANGE_EVENT = 'consent360:change'

export function isConsentExpired(decidedAtMs: number | undefined | null): boolean {
  if (!decidedAtMs) return true
  return Date.now() - decidedAtMs > CONSENT_TTL_DAYS * 24 * 60 * 60 * 1000
}

/** Call this right after writing a new decision to storage so every subscriber (including the gate) re-evaluates. */
export function notifyConsentChanged() {
  window.dispatchEvent(new CustomEvent(CONSENT_CHANGE_EVENT))
}

export function onConsentChanged(cb: () => void): () => void {
  const handler = () => cb()
  window.addEventListener(CONSENT_CHANGE_EVENT, handler)
  // Same-tab writes only fire the custom event above; the native 'storage'
  // event additionally covers preferences changed in *another* tab.
  window.addEventListener('storage', handler)
  return () => {
    window.removeEventListener(CONSENT_CHANGE_EVENT, handler)
    window.removeEventListener('storage', handler)
  }
}

/** W3C Global Privacy Control. There is no client-side way to read the
 * actual Sec-GPC request header - the server reads that itself
 * (app/api/routes/portal.py::_read_gpc_signal) and stamps it as fact on the
 * evidence row. What this function reports is only the client's own claim,
 * sent alongside every consent call as ClientContext.gpc_signal. */
export function gpcSignalDetected(): boolean {
  return typeof navigator !== 'undefined' && (navigator as unknown as { globalPrivacyControl?: boolean }).globalPrivacyControl === true
}

function activate(el: HTMLScriptElement) {
  const real = document.createElement('script')
  for (const attr of Array.from(el.attributes)) {
    if (attr.name === 'type' || attr.name === 'data-consent-category') continue
    if (attr.name === 'data-src') { real.src = attr.value; continue }
    real.setAttribute(attr.name, attr.value)
  }
  if (!real.src) real.textContent = el.textContent
  el.replaceWith(real)
}

export function activateGatedTags(adapter: ConsentGateAdapter, root: ParentNode = document): void {
  const gpc = gpcSignalDetected()
  const gated = root.querySelectorAll<HTMLScriptElement>('script[type="text/plain"][data-consent-category]')
  for (const el of Array.from(gated)) {
    const category = el.dataset.consentCategory as ConsentCategory
    if (category === 'necessary') { activate(el); continue }
    // GPC is a live objection from the visitor's browser: never auto-activate
    // an optional tag while it is present, even over a stored prior "accept".
    if (gpc) continue
    if (adapter.hasConsent(category)) activate(el)
  }
}

/** Wires the gate up: runs once immediately (in case consent already exists)
 * and again every time a decision changes. Returns an unsubscribe function. */
export function initConsentGate(adapter: ConsentGateAdapter): () => void {
  activateGatedTags(adapter)
  return adapter.subscribe(() => activateGatedTags(adapter))
}

// Reviewer finding (fix round): with GPC on and an explicit "Accept All",
// activateGatedTags() correctly leaves optional tags inert (the `if (gpc)
// continue` above), but hasConsent()/getState() used to report the visitor's
// *stored* decision regardless of GPC - so an integrator reading the public
// API instead of reimplementing activateGatedTags' own loop would see
// `hasConsent('analytics') === true` and fire under GPC anyway. This mirrors
// the exact enforcement rule from activateGatedTags(): GPC overrides a stored
// "yes" for every category except 'necessary' (which the gate always runs).
function effectiveConsent(adapter: ConsentGateAdapter, category: ConsentCategory): boolean {
  if (category !== 'necessary' && gpcSignalDetected()) return false
  return adapter.hasConsent(category)
}

/** window.consent360: the "exposes consent state to the page" half of Q-04 -
 * first-party page code (or a real vendor snippet once activated) can check
 * this instead of needing its own copy of the storage/parsing logic. Every
 * field here reports what is actually enforced, not just what is on file. */
export function installGlobalConsentApi(adapter: ConsentGateAdapter, categories: readonly ConsentCategory[]) {
  const api = {
    hasConsent: (category: ConsentCategory) => effectiveConsent(adapter, category),
    getState: () => Object.fromEntries(categories.map((c) => [c, effectiveConsent(adapter, c)])) as Record<ConsentCategory, boolean>,
    gpcDetected: gpcSignalDetected(),
    onChange: (cb: () => void) => onConsentChanged(cb),
    getTagScanReport: () => getLastTagScanReport(),
    onTagScanViolation: (cb: (violation: TagScanViolation) => void) => onTagScanViolation(cb),
  }
  ;(window as unknown as { consent360?: typeof api }).consent360 = api
  onConsentChanged(() => { api.gpcDetected = gpcSignalDetected() })
  return api
}

export interface TagScanViolation {
  reason: string
  element: string
}

/** K-48: periodic scan for tags that fired (or could fire) before consent.
 * Things that count as a violation:
 *  1. A still-gated (type="text/plain") tag for a category the visitor HAS
 *     valid consent for - it should have already been activated and was not
 *     (a bug in the gate itself).
 *  2. An already-executable <script src>, <img src> or <iframe src> pointing
 *     at a known analytics/ad host with no consent-category gate at all -
 *     i.e. a tag, tracking pixel or embed a developer (or an injected
 *     script) added directly, bypassing the gate entirely.
 *
 * HONEST LIMIT: this only recognises hosts in KNOWN_TRACKER_HOSTS below and
 * only sees what is in the DOM at scan time (startTagScanMonitor() narrows
 * that second gap with a MutationObserver + a periodic re-scan, and a
 * navigator.sendBeacon wrapper - see below - but none of this is a security
 * boundary). A determined or compromised script can call a saved reference
 * to an original browser API before this module ever runs, or ship its
 * payload from a host not on the list. The only real enforcement boundary
 * for that is a server-set Content-Security-Policy (script-src/connect-src)
 * header, which this client-side module cannot provide. See the cookie
 * policy pages for the same disclosure aimed at visitors/auditors. */
const KNOWN_TRACKER_HOSTS = [
  'google-analytics.com', 'googletagmanager.com', 'doubleclick.net',
  'connect.facebook.net', 'facebook.net', 'hotjar.com', 'mixpanel.com',
  'segment.com', 'segment.io', 'clarity.ms',
]

function isTrackerUrl(url: string): boolean {
  return !!url && KNOWN_TRACKER_HOSTS.some((h) => url.includes(h))
}

/** Checks one already-rendered element (script/img/iframe) for an ungated
 * tracker reference. Shared by the full scan and the MutationObserver below
 * so newly injected nodes are held to the same rule as the initial scan. */
function checkElementForViolation(el: Element): TagScanViolation | null {
  if (el instanceof HTMLScriptElement) {
    if (el.type === 'text/plain') return null // gated by convention; scanned separately
    if (isTrackerUrl(el.src) && !el.dataset.consentCategory) {
      return { reason: 'tracker <script src> present with no consent-category gate', element: el.outerHTML.slice(0, 160) }
    }
  } else if (el instanceof HTMLImageElement) {
    if (isTrackerUrl(el.src) && !el.dataset.consentCategory) {
      return { reason: 'tracking pixel (<img src>) present with no consent-category gate', element: el.outerHTML.slice(0, 160) }
    }
  } else if (el instanceof HTMLIFrameElement) {
    if (isTrackerUrl(el.src) && !el.dataset.consentCategory) {
      return { reason: 'tracker <iframe src> present with no consent-category gate', element: el.outerHTML.slice(0, 160) }
    }
  }
  return null
}

const UNGATED_TRACKER_SELECTOR = 'script[src], img[src], iframe[src]'

export function scanForUngatedTags(adapter: ConsentGateAdapter, root: ParentNode = document): TagScanViolation[] {
  const violations: TagScanViolation[] = []
  const gpc = gpcSignalDetected()
  for (const el of Array.from(root.querySelectorAll<HTMLScriptElement>('script[type="text/plain"][data-consent-category]'))) {
    const category = el.dataset.consentCategory as ConsentCategory
    if (category !== 'necessary' && !gpc && adapter.hasConsent(category)) {
      violations.push({ reason: `gated tag for "${category}" has valid consent but was never activated`, element: el.outerHTML.slice(0, 160) })
    }
  }
  for (const el of Array.from(root.querySelectorAll<Element>(UNGATED_TRACKER_SELECTOR))) {
    const v = checkElementForViolation(el)
    if (v) violations.push(v)
  }
  return violations
}

export interface TagScanReport {
  scannedAt: number
  violations: TagScanViolation[]
}

let latestReport: TagScanReport = { scannedAt: 0, violations: [] }
const SCAN_REPORT_EVENT = 'consent360:scan-report'
const SCAN_VIOLATION_EVENT = 'consent360:scan-violation'

/** The current state of the periodic/observed scan - this is what lets K-48
 * "read zero" from something real: once every gated tag is properly wired
 * and nothing ungated is present, `getLastTagScanReport().violations` is an
 * empty array produced by an actual, running check, not an unused function. */
export function getLastTagScanReport(): TagScanReport {
  return latestReport
}

export function onTagScanReport(cb: (report: TagScanReport) => void): () => void {
  const handler = (e: Event) => cb((e as CustomEvent<TagScanReport>).detail)
  window.addEventListener(SCAN_REPORT_EVENT, handler)
  return () => window.removeEventListener(SCAN_REPORT_EVENT, handler)
}

/** Real-time, one-off violations (e.g. a sendBeacon call) that a DOM state
 * snapshot cannot represent - fired in addition to, not instead of, the
 * periodic report above. */
export function onTagScanViolation(cb: (violation: TagScanViolation) => void): () => void {
  const handler = (e: Event) => cb((e as CustomEvent<TagScanViolation>).detail)
  window.addEventListener(SCAN_VIOLATION_EVENT, handler)
  return () => window.removeEventListener(SCAN_VIOLATION_EVENT, handler)
}

function reportViolation(v: TagScanViolation) {
  // eslint-disable-next-line no-console
  console.warn('[consent360] tag scan violation:', v.reason, v.element)
  window.dispatchEvent(new CustomEvent<TagScanViolation>(SCAN_VIOLATION_EVENT, { detail: v }))
}

function runFullScan(adapter: ConsentGateAdapter): TagScanReport {
  const violations = scanForUngatedTags(adapter)
  latestReport = { scannedAt: Date.now(), violations }
  for (const v of violations) reportViolation(v)
  window.dispatchEvent(new CustomEvent<TagScanReport>(SCAN_REPORT_EVENT, { detail: latestReport }))
  return latestReport
}

/** Wraps navigator.sendBeacon so a beacon fired to a known tracker host with
 * no active analytics/advertising consent (or while GPC is present) is
 * reported as a violation before being allowed through unchanged - this is
 * detection and audit trail, not a block: a script that captured a
 * reference to the original sendBeacon before this wrapper installs (this
 * module installs it as early as possible, from main.tsx, before React even
 * mounts, but "as early as possible" is not "before all other JS") bypasses
 * it entirely. */
function installSendBeaconGuard(adapter: ConsentGateAdapter): () => void {
  if (typeof navigator === 'undefined' || typeof navigator.sendBeacon !== 'function') return () => {}
  const original = navigator.sendBeacon.bind(navigator)
  const guarded = (url: string | URL, data?: BodyInit | null): boolean => {
    const dest = typeof url === 'string' ? url : url.toString()
    const consented = !gpcSignalDetected() && (adapter.hasConsent('analytics') || adapter.hasConsent('advertising'))
    if (isTrackerUrl(dest) && !consented) {
      reportViolation({ reason: 'navigator.sendBeacon fired to a known tracker host with no active analytics/advertising consent', element: dest })
    }
    return original(url, data)
  }
  navigator.sendBeacon = guarded
  return () => { navigator.sendBeacon = original }
}

/** K-48: wires up continuous enforcement-gap detection so "periodic scan for
 * tags firing before consent" is a real, running check rather than a
 * function nobody calls. Combines three things:
 *  - an immediate scan plus a periodic re-scan (`intervalMs`, default 10s),
 *  - a MutationObserver that re-scans (debounced) whenever a script, img or
 *    iframe node is added anywhere in the document, so an injected pixel or
 *    iframe is caught close to when it appears rather than only on the next
 *    tick of the interval,
 *  - the navigator.sendBeacon guard above.
 * Call once, from each site's main.tsx, alongside initConsentGate(). Returns
 * a stop function that undoes all three. See scanForUngatedTags()'s doc
 * comment for what this cannot catch. */
export function startTagScanMonitor(adapter: ConsentGateAdapter, options: { intervalMs?: number } = {}): () => void {
  const intervalMs = options.intervalMs ?? 10_000

  runFullScan(adapter)
  const timer = window.setInterval(() => runFullScan(adapter), intervalMs)

  let debounceTimer: number | undefined
  const scheduleScan = () => {
    if (debounceTimer !== undefined) return
    debounceTimer = window.setTimeout(() => {
      debounceTimer = undefined
      runFullScan(adapter)
    }, 250)
  }
  const observer = new MutationObserver((mutations) => {
    const relevant = mutations.some((m) =>
      Array.from(m.addedNodes).some(
        (n) => n instanceof Element && (n.matches(UNGATED_TRACKER_SELECTOR) || n.querySelector(UNGATED_TRACKER_SELECTOR)),
      ),
    )
    if (relevant) scheduleScan()
  })
  observer.observe(document.documentElement, { childList: true, subtree: true })

  const restoreBeacon = installSendBeaconGuard(adapter)

  return () => {
    window.clearInterval(timer)
    if (debounceTimer !== undefined) window.clearTimeout(debounceTimer)
    observer.disconnect()
    restoreBeacon()
  }
}
