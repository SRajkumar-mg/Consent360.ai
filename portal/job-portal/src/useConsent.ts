import { useState, useCallback, useEffect } from 'react'
import { getSessionId } from './session'
import { gpcSignalDetected, isConsentExpired, notifyConsentChanged } from './consentGate'
import { emitDecision, emitNoticeShown } from './bannerEvents'

interface ConsentState {
  contextToken: string | null
  purposes: { code: string; name: string; description: string; granted: boolean }[]
  consented: boolean
  // R2-10 / gap Q-05, Q-08: when this decision was made, so a stale one can
  // expire and trigger a re-prompt (see the mount effect below) instead of
  // being trusted indefinitely.
  decidedAt?: number
}

const STORAGE_KEY = 'careerhub_consent'
const TENANT_CODE = 'CAREER_HUB'
const BANNER_VERSION = 'career-hub-consent-banner-v1'
const SCREEN_ID = 'career-hub-consent-banner'

// Every grant/withdraw here is fired from a single click with no confirmation
// step in between, so "step 1" is accurate today. It is a named constant (not
// literal `1` inlined at each call site) precisely so that the moment a flow
// grows a second step, this stops compiling as a silent lie and has to be
// replaced with real per-flow instrumentation.
const SINGLE_CLICK_INTERACTION_STEP = 1

class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

function loadConsent(): ConsentState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    return JSON.parse(raw)
  } catch {
    return null
  }
}

function saveConsent(state: ConsentState) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
}

async function fetchJson(url: string, opts?: RequestInit) {
  const res = await fetch(url, opts)
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError(body.detail || `HTTP ${res.status}`, res.status)
  return body
}

export function useConsent() {
  const [state, setState] = useState<ConsentState>(() => loadConsent() ?? {
    contextToken: null,
    purposes: [],
    consented: false,
  })
  const [loading, setLoading] = useState(false)
  const [showBanner, setShowBanner] = useState(false)
  const [noticeVersion, setNoticeVersion] = useState<number | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)

  const clearError = useCallback(() => setError(null), [])

  useEffect(() => {
    fetchJson(`/public/${TENANT_CODE}/purposes`)
      .then((purposes: { purpose_version_id: number }[]) => {
        if (Array.isArray(purposes) && purposes.length) {
          setNoticeVersion(Math.max(...purposes.map((p) => p.purpose_version_id)))
        }
      })
      .catch(() => {
        /* notice version stays undefined - decisions still proceed */
      })
  }, [])

  const initConsent = useCallback(async (email: string) => {
    setLoading(true)
    setError(null)
    try {
      const ctx = await fetchJson('/consent/customer-context', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          // No fabricated fallback. A synthetic visitor address names nobody,
          // so the consent it carries can neither be honoured nor withdrawn -
          // the backend refuses it (gap B-04) and the caller must supply the
          // principal's own address.
          name: email.split('@')[0],
          email,
          source_app: 'CAREER_HUB',
        }),
      })

      const token = ctx.context_token
      const overview = await fetchJson('/portal/overview', {
        headers: { 'X-Context-Token': token },
      })

      const purposes = overview.purposes.map((p: any) => ({
        code: p.code,
        name: p.name,
        description: p.description || `Consent for ${p.name.toLowerCase()} data processing`,
        granted: p.status === 'GRANTED',
      }))

      const newState: ConsentState = {
        contextToken: token,
        purposes,
        consented: false,
      }
      setState(newState)
      // R2-07: the impression, emitted where the banner is actually shown -
      // not where initConsent is called - because it is the denominator for
      // K-02's ignore rate, and a handoff that failed above never rendered a
      // notice for anyone to ignore. `language: 'en'` matches the
      // ClientContext this hook already sends on every grant/withdraw; the
      // banner's own language selector is not plumbed through to here, so
      // claiming otherwise would put a language on the record that the
      // consent evidence contradicts.
      emitNoticeShown({
        bannerVersion: BANNER_VERSION,
        language: 'en',
        purposesOffered: purposes.map((p: { code: string }) => p.code),
        surface: 'CONSENT_GATE',
      })
      setShowBanner(true)
    } catch (err) {
      console.error('Consent init failed:', err)
      // A silent failure here leaves the user clicking a dead shield icon with
      // no idea why nothing happens. Surface it, and distinguish "we could not
      // verify who you are yet" (403 from /portal/overview, pending a
      // fiduciary-asserted handoff) from any other failure so the message is
      // honest about what went wrong.
      const status = err instanceof ApiError ? err.status : undefined
      setError(
        status === 403
          ? 'We could not verify your identity to load your consent preferences yet. Please try again.'
          : 'We could not load your consent preferences. Please check your connection and try again.',
      )
    } finally {
      setLoading(false)
    }
  }, [])

  const grantPurpose = useCallback(async (code: string) => {
    if (!state.contextToken) return
    try {
      await fetchJson('/portal/grant', {
        method: 'POST',
        headers: {
          'X-Context-Token': state.contextToken,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          purpose_code: code,
          context: {
            language: 'en',
            notice_version: noticeVersion,
            banner_version: BANNER_VERSION,
            ui_control_id: `grant-${code}`,
            session_id: getSessionId(),
            screen_id: SCREEN_ID,
            interaction_step: SINGLE_CLICK_INTERACTION_STEP,
            gpc_signal: gpcSignalDetected(),
          },
        }),
      })
      setState((prev) => ({
        ...prev,
        purposes: prev.purposes.map((p) =>
          p.code === code ? { ...p, granted: true } : p
        ),
      }))
    } catch (err) {
      console.error('Grant failed:', err)
    }
  }, [state.contextToken, noticeVersion])

  const withdrawPurpose = useCallback(async (code: string) => {
    if (!state.contextToken) return
    try {
      await fetchJson('/portal/withdraw', {
        method: 'POST',
        headers: {
          'X-Context-Token': state.contextToken,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          purpose_code: code,
          context: {
            language: 'en',
            notice_version: noticeVersion,
            banner_version: BANNER_VERSION,
            ui_control_id: `withdraw-${code}`,
            session_id: getSessionId(),
            screen_id: SCREEN_ID,
            interaction_step: SINGLE_CLICK_INTERACTION_STEP,
            gpc_signal: gpcSignalDetected(),
          },
        }),
      })
      setState((prev) => ({
        ...prev,
        purposes: prev.purposes.map((p) =>
          p.code === code ? { ...p, granted: false } : p
        ),
      }))
    } catch (err) {
      console.error('Withdraw failed:', err)
    }
  }, [state.contextToken, noticeVersion])

  // The third verb. A refusal is a decision, and until POST /portal/deny
  // existed this banner had nowhere to send one: a first-time visitor - every
  // purpose still NOT_REQUESTED - pressing "Reject all" made ZERO server
  // calls, because rejectAll only ever withdrew *already granted* purposes.
  // The refusal lived in this browser's localStorage and nowhere else, so the
  // fiduciary could not discharge s.6(10)'s burden of proving the person
  // refused, and on a second device the banner asked again as though nothing
  // had been decided. Sends the identical ClientContext as grant/withdraw, so
  // the evidence behind a refusal is no weaker than the evidence behind a
  // grant - which is the whole point of B-01/B-02's "reject as easy as
  // accept": equally easy to *press* was never the same as equally recorded.
  const denyPurpose = useCallback(async (code: string) => {
    if (!state.contextToken) return
    try {
      await fetchJson('/portal/deny', {
        method: 'POST',
        headers: {
          'X-Context-Token': state.contextToken,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          purpose_code: code,
          context: {
            language: 'en',
            notice_version: noticeVersion,
            banner_version: BANNER_VERSION,
            ui_control_id: `deny-${code}`,
            session_id: getSessionId(),
            screen_id: SCREEN_ID,
            interaction_step: SINGLE_CLICK_INTERACTION_STEP,
            gpc_signal: gpcSignalDetected(),
          },
        }),
      })
      setState((prev) => ({
        ...prev,
        purposes: prev.purposes.map((p) =>
          p.code === code ? { ...p, granted: false } : p
        ),
      }))
    } catch (err) {
      console.error('Deny failed:', err)
    }
  }, [state.contextToken, noticeVersion])

  // Bug fix (found while verifying R2-10's gate against this site): these two
  // previously built `newState` from the `state` closure captured when
  // acceptAll/rejectAll was called, which still had every purpose's stale
  // pre-grant `granted` value - grantPurpose/withdrawPurpose's own setState
  // calls landed on *later* renders this closure never saw. The result was
  // silently persisting "nothing was actually granted" to localStorage after
  // every "Accept all" click, which is also what the R2-10 consent gate reads
  // to decide whether to activate a gated tag. Compute the array this
  // function is about to produce explicitly instead of trusting the stale
  // closure.
  const acceptAll = useCallback(async () => {
    for (const p of state.purposes) {
      if (!p.granted) await grantPurpose(p.code)
    }
    const newState = {
      ...state,
      purposes: state.purposes.map((p) => ({ ...p, granted: true })),
      consented: true,
      decidedAt: Date.now(),
    }
    setState(newState)
    saveConsent(newState)
    notifyConsentChanged()
    emitDecision({
      decision: 'ACCEPT_ALL',
      bannerVersion: BANNER_VERSION,
      language: 'en',
      purposesOffered: state.purposes.map((p) => p.code),
      purposesGranted: state.purposes.map((p) => p.code),
      surface: 'CONSENT_GATE',
    })
    setShowBanner(false)
  }, [state, grantPurpose])

  const rejectAll = useCallback(async () => {
    for (const p of state.purposes) {
      // EVERY purpose, not just the granted ones. The old `if (p.granted)`
      // guard meant the commonest case by far - a first-time visitor with
      // nothing yet granted - produced no server call at all, so "Reject all"
      // recorded the refusal nowhere. Which verb applies is decided by what
      // the record currently says: a live consent is *withdrawn* (the
      // principal is taking back something she gave), one that was never
      // given is *denied* (she is refusing it for the first time). Both are
      // real, evidenced transitions; neither is a no-op.
      if (p.granted) await withdrawPurpose(p.code)
      else await denyPurpose(p.code)
    }
    const newState = {
      ...state,
      purposes: state.purposes.map((p) => ({ ...p, granted: false })),
      consented: true,
      decidedAt: Date.now(),
    }
    setState(newState)
    saveConsent(newState)
    notifyConsentChanged()
    emitDecision({
      decision: 'REJECT_ALL',
      bannerVersion: BANNER_VERSION,
      language: 'en',
      purposesOffered: state.purposes.map((p) => p.code),
      purposesGranted: [],
      surface: 'CONSENT_GATE',
    })
    setShowBanner(false)
  }, [state, withdrawPurpose, denyPurpose])

  const savePreferences = useCallback(() => {
    const newState = { ...state, consented: true, decidedAt: Date.now() }
    setState(newState)
    saveConsent(newState)
    notifyConsentChanged()
    // GRANULAR even when the toggles happen to add up to all-or-nothing: K-47
    // measures which *control* the principal used, and reaching for the
    // per-purpose switches is a different act from pressing Accept all. This
    // banner grants and withdraws each purpose as its toggle is flipped, so
    // `state.purposes` already carries the decision by the time Save is
    // pressed.
    emitDecision({
      decision: 'GRANULAR',
      bannerVersion: BANNER_VERSION,
      language: 'en',
      purposesOffered: state.purposes.map((p) => p.code),
      purposesGranted: state.purposes.filter((p) => p.granted).map((p) => p.code),
      surface: 'CONSENT_GATE',
    })
    setShowBanner(false)
  }, [state])

  // R2-10 / gap Q-05, Q-08: once the stored decision has passed the TTL
  // window, stop trusting it - drop back to "not yet decided" so the
  // persistent toast/FAB nudge the visitor to review their choices again,
  // and the consent gate (which reads this same localStorage record) stops
  // honouring the expired grants.
  useEffect(() => {
    if (state.consented && isConsentExpired(state.decidedAt)) {
      const newState = { ...state, consented: false }
      setState(newState)
      saveConsent(newState)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const resetConsent = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY)
    setState({ contextToken: null, purposes: [], consented: false })
    setShowBanner(true)
  }, [])

  return {
    ...state,
    loading,
    showBanner,
    setShowBanner,
    error,
    clearError,
    initConsent,
    grantPurpose,
    withdrawPurpose,
    denyPurpose,
    acceptAll,
    rejectAll,
    savePreferences,
    resetConsent,
  }
}
