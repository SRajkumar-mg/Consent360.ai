import { useState, useCallback, useEffect } from 'react'

const JOB_PORTAL_BACKEND = 'http://localhost:5180/api'

interface ConsentState {
  contextToken: string | null
  purposes: { code: string; name: string; description: string; granted: boolean }[]
  consented: boolean
}

const STORAGE_KEY = 'careerhub_consent'

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

  const initConsent = useCallback(async (email?: string) => {
    setLoading(true)
    try {
      const ctx = await fetchJson(`${JOB_PORTAL_BACKEND}/consent/context`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: email?.split('@')[0] || 'Visitor',
          email: email || `visitor-${Date.now()}@careerhub.local`,
          source_app: 'CAREER_HUB',
        }),
      })

      const token = ctx.context_token
      const overview = await fetchJson(`${JOB_PORTAL_BACKEND}/portal/overview`, {
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
      setShowBanner(true)
    } catch (err) {
      console.error('Consent init failed:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  const grantPurpose = useCallback(async (code: string) => {
    if (!state.contextToken) return
    try {
      await fetchJson(`${JOB_PORTAL_BACKEND}/portal/grant`, {
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
    if (!state.contextToken) return
    try {
      await fetchJson(`${JOB_PORTAL_BACKEND}/portal/withdraw`, {
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
    for (const p of state.purposes) {
      if (!p.granted) await grantPurpose(p.code)
    }
    const newState = { ...state, consented: true }
    setState(newState)
    saveConsent(newState)
    setShowBanner(false)
  }, [state, grantPurpose])

  const rejectAll = useCallback(async () => {
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
    setState(newState)
    saveConsent(newState)
    setShowBanner(false)
  }, [state])

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
    initConsent,
    grantPurpose,
    withdrawPurpose,
    acceptAll,
    rejectAll,
    savePreferences,
    resetConsent,
  }
}
