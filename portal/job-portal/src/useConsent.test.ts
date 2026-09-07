/**
 * Regression tests for "Reject All records nothing" (B-01/B-02, A-09, s.6(10)).
 *
 * THE DEFECT. `rejectAll` used to be:
 *
 *     for (const p of state.purposes) {
 *       if (p.granted) await withdrawPurpose(p.code)
 *     }
 *
 * so a first-time visitor - every purpose still NOT_REQUESTED server-side,
 * nothing granted to withdraw - pressed "Reject All" and the hook made ZERO
 * server calls. Confirmed live in a browser: no `/portal/*` request, no
 * console error, and all 33 of that principal's consent rows still
 * NOT_REQUESTED afterwards. The refusal existed only in that one browser's
 * localStorage, so the fiduciary could not discharge s.6(10)'s burden of
 * proving she refused, and on a second device the banner asked again as
 * though nothing had ever been decided.
 *
 * These tests drive the real hook against a stubbed `fetch` and assert on the
 * requests it actually makes. The first one fails the moment `rejectAll`
 * stops recording a refusal for a purpose that was never granted - which is
 * exactly the state the code was in before this fix.
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useConsent } from './useConsent'

// React 19 refuses to run state updates outside act() unless this is set;
// @testing-library/react's own act wrapper relies on it.
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

interface RecordedCall {
  url: string
  method: string
  headers: Record<string, string>
  body: Record<string, unknown> | undefined
}

let calls: RecordedCall[] = []
let overviewPurposes: { code: string; name: string; status: string }[] = []

const OFFERED = ['functional', 'analytics', 'advertising']

function stubFetch() {
  calls = []
  const fetchStub = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({
      url,
      method,
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    })

    const ok = (payload: unknown) => ({ ok: true, status: 200, json: async () => payload }) as Response

    if (url.includes('/purposes')) return ok([{ purpose_version_id: 8 }])
    if (url.includes('/banner-events')) return ok({})
    if (url.includes('/consent/customer-context')) return ok({ context_token: 'ctx-token-abc' })
    if (url.includes('/portal/overview')) {
      return ok({ customer: { external_id: 'CUST-TEST' }, purposes: overviewPurposes })
    }
    if (url.includes('/portal/')) return ok({ affected: 4 })
    throw new Error(`unstubbed fetch: ${method} ${url}`)
  })
  vi.stubGlobal('fetch', fetchStub)
}

/** Mount the hook and complete the identity handoff, as the banner does. */
async function mountAndInit() {
  const { result } = renderHook(() => useConsent())
  await act(async () => {
    await result.current.initConsent('rejector@example.com')
  })
  await waitFor(() => expect(result.current.purposes.length).toBe(overviewPurposes.length))
  calls.length = 0 // drop the setup traffic; assert only on the decision
  return result
}

const decisionCalls = () => calls.filter((c) => /\/portal\/(grant|withdraw|deny)$/.test(c.url))
const urlsOf = (path: string) => decisionCalls().filter((c) => c.url.endsWith(path))

/**
 * An explicit in-memory Storage, installed over both `globalThis` and
 * `window`. Node 22+ ships its own experimental `localStorage` global that
 * shadows jsdom's and is inert without `--localstorage-file`, so relying on
 * the environment's own storage makes the suite depend on which Node runs it.
 */
function memoryStorage(): Storage {
  const data = new Map<string, string>()
  return {
    get length() { return data.size },
    key: (i: number) => [...data.keys()][i] ?? null,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, String(v)),
    removeItem: (k: string) => void data.delete(k),
    clear: () => data.clear(),
  } as Storage
}

beforeEach(() => {
  for (const name of ['localStorage', 'sessionStorage'] as const) {
    const store = memoryStorage()
    vi.stubGlobal(name, store)
    Object.defineProperty(window, name, { value: store, configurable: true, writable: true })
  }
  overviewPurposes = OFFERED.map((code) => ({ code, name: code, status: 'NOT_GRANTED' }))
  stubFetch()
})

describe('rejectAll', () => {
  it('records a refusal on the server for a first-time visitor who has granted nothing', async () => {
    // THE regression: before the fix this produced no requests at all.
    const result = await mountAndInit()

    await act(async () => {
      await result.current.rejectAll()
    })

    const denies = urlsOf('/portal/deny')
    expect(denies.map((c) => c.body?.purpose_code).sort()).toEqual([...OFFERED].sort())
    expect(denies).toHaveLength(OFFERED.length)
    // Every offered purpose was acted on - none silently skipped...
    expect(decisionCalls()).toHaveLength(OFFERED.length)
    // ...and a refusal is never recorded as a grant.
    expect(urlsOf('/portal/grant')).toHaveLength(0)
  })

  it('withdraws what is live and denies what was never granted', async () => {
    // A returning principal: one purpose already GRANTED server-side. Taking
    // back something given is a withdrawal; refusing something never given is
    // a denial. Both are real transitions - neither may be a no-op.
    overviewPurposes = [
      { code: 'functional', name: 'functional', status: 'GRANTED' },
      { code: 'analytics', name: 'analytics', status: 'NOT_GRANTED' },
      { code: 'advertising', name: 'advertising', status: 'NOT_GRANTED' },
    ]
    const result = await mountAndInit()

    await act(async () => {
      await result.current.rejectAll()
    })

    expect(urlsOf('/portal/withdraw').map((c) => c.body?.purpose_code)).toEqual(['functional'])
    expect(urlsOf('/portal/deny').map((c) => c.body?.purpose_code).sort()).toEqual([
      'advertising',
      'analytics',
    ])
    expect(decisionCalls()).toHaveLength(3)
  })

  it("sends a refusal with the same ClientContext evidence a grant carries", async () => {
    // B-01/B-02 parity is about the record, not only the button: if the
    // refusal reached the server with less context than the grant, "reject as
    // easy as accept" would be true of the UI and false of the evidence.
    const result = await mountAndInit()

    await act(async () => {
      await result.current.rejectAll()
    })
    const deny = urlsOf('/portal/deny')[0]

    expect(deny.headers['X-Context-Token']).toBe('ctx-token-abc')
    expect(deny.method).toBe('POST')
    const context = deny.body?.context as Record<string, unknown>
    expect(context.language).toBe('en')
    expect(context.banner_version).toBe('career-hub-consent-banner-v1')
    expect(context.screen_id).toBe('career-hub-consent-banner')
    expect(context.ui_control_id).toBe(`deny-${deny.body?.purpose_code}`)
    expect(context.interaction_step).toBe(1)
    expect(context.notice_version).toBe(8)
    expect(typeof context.session_id).toBe('string')
    expect((context.session_id as string).length).toBeGreaterThan(0)
    expect(context.gpc_signal).toBe(false)
  })

  it('still persists the decision locally so the consent gate stops honouring it', async () => {
    // The pre-existing stale-closure fix (see the comment above acceptAll)
    // must survive: the stored record has to reflect the decision this call
    // just made, not the `state` closure captured before it ran.
    const result = await mountAndInit()

    await act(async () => {
      await result.current.rejectAll()
    })

    const stored = JSON.parse(localStorage.getItem('careerhub_consent') ?? '{}')
    expect(stored.consented).toBe(true)
    expect(typeof stored.decidedAt).toBe('number')
    expect(stored.purposes.every((p: { granted: boolean }) => p.granted === false)).toBe(true)
    expect(result.current.showBanner).toBe(false)
  })
})

describe('acceptAll', () => {
  it('still grants every purpose, unchanged by the reject-side fix', async () => {
    const result = await mountAndInit()

    await act(async () => {
      await result.current.acceptAll()
    })

    expect(urlsOf('/portal/grant').map((c) => c.body?.purpose_code).sort()).toEqual([...OFFERED].sort())
    expect(urlsOf('/portal/deny')).toHaveLength(0)
    const stored = JSON.parse(localStorage.getItem('careerhub_consent') ?? '{}')
    expect(stored.purposes.every((p: { granted: boolean }) => p.granted === true)).toBe(true)
  })
})
