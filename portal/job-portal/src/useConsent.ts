import { useState, useCallback, useRef } from 'react'

const CONSENT360_BASE = 'http://localhost:8000'
const API_KEY = 'dev-demo-integration-key-2026'

interface ConsentState {
  contextToken: string | null
  purposes: { code: string; name: string; description: string; granted: boolean }[]
  consented: boolean
}

const STORAGE_KEY = 'careerhub_consent'
const ANON_PREFS_KEY = 'careerhub_consent_anon_prefs'

/**
 * R1-12: Onboarding an anonymous visitor must NOT persist a synthetic customer
 * row. An anonymous banner interaction is tracked purely client-side/scoped in
 * localStorage, and the real Consent360 customer record is only created (and
 * consent decisions only persisted) once an identifying action — a real email /
 * signup — is supplied to `initConsent(email)`.
 */

interface LocalPurposePref { code: string; granted: boolean }

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

function loadAnonPrefs(): LocalPurposePref[] | null {
  try {
    const raw = localStorage.getItem(ANON_PREFS_KEY)
    if (!raw) return null
    return JSON.parse(raw)
  } catch {
    return null
  }
}

function saveAnonPrefs(prefs: LocalPurposePref[]) {
  localStorage.setItem(ANON_PREFS_KEY, JSON.stringify(prefs))
}

async function fetchJson(url: string, opts?: RequestInit) {
  const res = await fetch(url, opts)
  const body = await res.json()
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`)
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
  const [isAnonymous, setIsAnonymous] = useState<boolean>(() => !loadConsent()?.contextToken)
  const [anonPrefs, setAnonPrefs] = useState<LocalPurposePref[]>(() => loadAnonPrefs() ?? [])
  const anonPrefsRef = useRef(anonPrefs)
  anonPrefsRef.current = anonPrefs

  // Build a local purposes list for anonymous visitors (non-identifying).
  const localPurposes = [
    { code: 'functional', name: 'Functional cookies', description: 'Remember your language and preferences.' },
    { code: 'analytics', name: 'Performance & analytics', description: 'Understand how the site is used to improve it.' },
    { code: 'advertising', name: 'Advertising & social media', description: 'Show relevant ads from our partners.' },
  ]

  /**
   * Initialize consent. When `email` is provided (an identifying action such as
   * a real signup or authenticated session), the backend customer context is
   * created and real consents are loaded. When `email` is absent (pure anonymous
   * banner), no backend call / customer row is created — we only track intent in
   * session-scoped localStorage.
   */
  const initConsent = useCallback(async (email?: string) => {
    setLoading(true)
    try {
      if (!email || !email.includes('@')) {
        // Anonymous visitor: NO backend customer row. Track locally only.
        const prefs = loadAnonPrefs() ?? []
        const grantedByCode = (c: string) => prefs.find((p) => p.code === c)?.granted ?? false
        setState({
          contextToken: null,
          purposes: localPurposes.map((p) => ({
            code: p.code,
            name: p.name,
            description: p.description,
            granted: grantedByCode(p.code),
          })),
          consented: prefs.length > 0,
        })
        setIsAnonymous(true)
        setShowBanner(true)
        return
      }

      // Real identifying action → create/persist the real customer consent profile.
      const realName = email.split('@')[0]
      const ctx = await fetchJson(`${CONSENT360_BASE}/consent/customer-context`, {
        method: 'POST',
        headers: { 'X-API-Key': API_KEY, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: realName,
          email,
          source_app: 'CAREER_HUB',
        }),
      })

      const token = ctx.context_token
      const overview = await fetchJson(`${CONSENT360_BASE}/portal/overview`, {
        headers: { 'X-Context-Token': token },
      })

      const purposes = overview.purposes.map((p: any) => ({
        code: p.code,
        name: p.name,
        description: p.description || `Consent for ${p.name.toLowerCase()} data processing`,
        granted: p.status === 'GRANTED',
      }))

      setState({
        contextToken: token,
        purposes,
        consented: false,
      })
      setIsAnonymous(false)
      // Clear any session-scoped anonymous prefs once a real identity exists.
      localStorage.removeItem(ANON_PREFS_KEY)
      setAnonPrefs([])
      setShowBanner(true)
    } catch (err) {
      console.error('Consent init failed:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  const grantPurpose = useCallback(async (code: string) => {
    if (!state.contextToken) {
      // Anonymous: update local-only prefs.
      const next = [...anonPrefsRef.current.filter((p) => p.code !== code), { code, granted: true }]
      setAnonPrefs(next)
      saveAnonPrefs(next)
      setState((prev) => ({
        ...prev,
        purposes: prev.purposes.map((p) => (p.code === code ? { ...p, granted: true } : p)),
      }))
      return
    }
    try {
      await fetchJson(`${CONSENT360_BASE}/portal/grant`, {
        method: 'POST',
        headers: {
          'X-Context-Token': state.contextToken,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ purpose_code: code }),
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
  }, [state.contextToken])

  const withdrawPurpose = useCallback(async (code: string) => {
    if (!state.contextToken) {
      const next = [...anonPrefsRef.current.filter((p) => p.code !== code), { code, granted: false }]
      setAnonPrefs(next)
      saveAnonPrefs(next)
      setState((prev) => ({
        ...prev,
        purposes: prev.purposes.map((p) => (p.code === code ? { ...p, granted: false } : p)),
      }))
      return
    }
    try {
      await fetchJson(`${CONSENT360_BASE}/portal/withdraw`, {
        method: 'POST',
        headers: {
          'X-Context-Token': state.contextToken,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ purpose_code: code }),
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
  }, [state.contextToken])

  const acceptAll = useCallback(async () => {
    if (!state.contextToken) {
      const codes = state.purposes.map((p) => p.code)
      const next = codes.map((code) => ({ code, granted: true }))
      setAnonPrefs(next)
      saveAnonPrefs(next)
      setState((prev) => ({
        ...prev,
        purposes: prev.purposes.map((p) => ({ ...p, granted: true })),
        consented: true,
      }))
      setShowBanner(false)
      return
    }
    for (const p of state.purposes) {
      if (!p.granted) await grantPurpose(p.code)
    }
    const newState = { ...state, consented: true }
    setState(newState)
    saveConsent(newState)
    setShowBanner(false)
  }, [state, grantPurpose])

  const rejectAll = useCallback(async () => {
    if (!state.contextToken) {
      const codes = state.purposes.map((p) => p.code)
      const next = codes.map((code) => ({ code, granted: false }))
      setAnonPrefs(next)
      saveAnonPrefs(next)
      setState((prev) => ({
        ...prev,
        purposes: prev.purposes.map((p) => ({ ...p, granted: false })),
        consented: true,
      }))
      setShowBanner(false)
      return
    }
    for (const p of state.purposes) {
      if (p.granted) await withdrawPurpose(p.code)
    }
    const newState = { ...state, consented: true }
    setState(newState)
    saveConsent(newState)
    setShowBanner(false)
  }, [state, withdrawPurpose])

  const savePreferences = useCallback(() => {
    const newState = { ...state, consented: true }
    if (isAnonymous) {
      saveAnonPrefs(anonPrefsRef.current)
    } else {
      saveConsent(newState)
    }
    setState(newState)
    setShowBanner(false)
  }, [state, isAnonymous])

  const resetConsent = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY)
    if (isAnonymous) {
      localStorage.removeItem(ANON_PREFS_KEY)
      setAnonPrefs([])
    }
    setState({ contextToken: null, purposes: [], consented: false })
    setShowBanner(true)
  }, [isAnonymous])

  // When the user authenticates (an email becomes available to a previously
  // anonymous session), promote to the real persisted flow.
  const promoteToIdentified = useCallback((email: string) => {
    initConsent(email)
  }, [initConsent])

  return {
    ...state,
    isAnonymous,
    loading,
    showBanner,
    setShowBanner,
    initConsent,
    promoteToIdentified,
    grantPurpose,
    withdrawPurpose,
    acceptAll,
    rejectAll,
    savePreferences,
    resetConsent,
  }
}
