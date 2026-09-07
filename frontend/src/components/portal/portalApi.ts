import axios, { type AxiosResponse } from 'axios'
import type { Consent, ConsentEvidence, ConsentHistory, ConsentReceipt, Customer, Grievance, NotificationRow } from '../../types'

/**
 * The data principal's own API surface. Deliberately its own axios instance,
 * NOT the shared staff client from api/client.ts: this page carries no staff
 * session, must never attach a staff Bearer token, and must never be
 * redirected to /login on a 401 (which the shared client's response
 * interceptor would do for anything outside its `/portal` exclusion - and
 * this page also calls /receipts, /grievances and /public, which are not in
 * that exclusion).
 *
 * The context token is the ONLY credential here. It is passed explicitly to
 * every call rather than held in a module-level variable, so there is no way
 * for it to outlive the component that read it out of the URL, and it is
 * never written to localStorage or sessionStorage.
 */
const client = axios.create({ baseURL: (import.meta.env.VITE_API_URL as string) || '/api' })

function auth(token: string) {
  return { headers: { 'X-Context-Token': token } }
}

// ---------------------------------------------------------------------------
// Shapes the principal endpoints return that the staff types.ts does not
// already cover.
// ---------------------------------------------------------------------------

export interface PortalPurpose {
  code: string
  name: string
  description: string
  legal_basis: string
  requires_consent: boolean
  retention_period_days: number
  consent_text: string
  /** {lang: {name, description, consent_text}} - the shape `localize` reads. */
  translations: Record<string, { name?: string; description?: string; consent_text?: string }>
  status: 'GRANTED' | 'PARTIAL' | 'NOT_GRANTED' | string
  granted_count: number
  total_count: number
  /**
   * R1-09/R2-11: this purpose changed materially and the consent behind it is
   * no longer being relied on.
   *
   * The status above can still read GRANTED while this is true - the consent
   * row was re-pointed at the new version and sits in UPDATED, which the
   * overview counts as live - so `status` alone cannot be used to decide
   * whether to prompt. Use these fields.
   */
  re_consent_required: boolean
  re_consent_count: number
  re_consent_requested_at: string | null
  re_consent_campaign_ref: string | null
  /** The classifier's own reason. This is the substantive notice the prompt
   *  must carry; "something changed" is not an informed consent moment. */
  re_consent_reason: string
  purpose_version_number: number | null
}

export interface PortalOverview {
  customer: Customer
  purposes: PortalPurpose[]
}

/** GET /portal/history - the complete record, which is also what
 *  GET /portal/export serialises. */
export interface PrincipalRecord {
  customer: Customer
  source_app: string
  generated_at: string
  consents: Consent[]
  history: ConsentHistory[]
  evidence: ConsentEvidence[]
  receipts: ConsentReceipt[]
}

export interface GrievanceAcknowledgement {
  grievance: Grievance
  reference_no: string
  acknowledged: boolean
  acknowledgement_message: string
  response_days: number
  due_at: string
  grievance_officer: string
  board_complaint_url: string
  notification_ids: number[]
}

export interface PublicRights {
  tenant_code: string
  tenant_name: string
  rights_url: string
  withdraw_url: string
  grievance_url: string
  board_complaint_url: string
  grievance_response_days: number
}

export interface PublicPrivacyContact {
  tenant_code: string
  tenant_name: string
  dpo_name: string
  dpo_email: string
  dpo_phone: string
}

export interface PublicNotice {
  tenant_code: string
  purpose_code: string
  purpose_name: string
  notice_version_id: number
  version_number: number
  language_requested: string
  language_served: string
  title: string
  body: string
  data_items: Array<Record<string, unknown>>
  services_enabled: string
  legal_basis: string
  requires_consent: boolean
  retention_period_days: number | null
  retention_note: string
  child_restricted: boolean
  links: Record<string, string>
  contact: Record<string, string>
  content_hash: string | null
  effective_from: string | null
}

/** The `context` block stamped on every grant/withdraw as consent evidence. */
export interface PortalClientContext {
  language: string
  ui_control_id: string
  screen_id: string
  affirmative_action: 'CLICK'
  interaction_step: number
  gpc_signal: boolean
}

export const portalApi = {
  verifyStart: (token: string) => client.post<{ message: string }>('/portal/verify/start', {}, auth(token)),
  verifyConfirm: (token: string, code: string) =>
    client.post<{ message: string }>('/portal/verify/confirm', { code }, auth(token)),
  overview: (token: string) => client.get<PortalOverview>('/portal/overview', auth(token)),
  history: (token: string) => client.get<PrincipalRecord>('/portal/history', auth(token)),
  grant: (token: string, purpose_code: string, context: PortalClientContext) =>
    client.post('/portal/grant', { purpose_code, context }, auth(token)),
  withdraw: (token: string, purpose_code: string, context: PortalClientContext) =>
    client.post('/portal/withdraw', { purpose_code, context }, auth(token)),
  notifications: (token: string) => client.get<NotificationRow[]>('/portal/notifications', auth(token)),
  acknowledgeNotification: (token: string, id: number) =>
    client.post<NotificationRow>(`/portal/notifications/${id}/acknowledge`, {}, auth(token)),
  /** Returns the raw bytes so the caller can hand the browser a real file with
   *  the filename the server chose, rather than re-inventing one. */
  exportRecord: (token: string, format: 'json' | 'csv' | 'pdf') =>
    client.get(`/portal/export?format=${format}`, { ...auth(token), responseType: 'blob' }),

  receipts: (token: string) => client.get<ConsentReceipt[]>('/receipts/me', auth(token)),
  receipt: (token: string, ref: string) => client.get<ConsentReceipt>(`/receipts/me/${encodeURIComponent(ref)}`, auth(token)),

  grievances: (token: string) => client.get<Grievance[]>('/grievances/me', auth(token)),
  submitGrievance: (
    token: string,
    body: { category: string; subject: string; description: string; purpose_code?: string | null },
  ) => client.post<GrievanceAcknowledgement>('/grievances/me', body, auth(token)),
  grievanceFeedback: (token: string, ref: string, rating: number, comment: string) =>
    client.post<Grievance>(`/grievances/me/${encodeURIComponent(ref)}/feedback`, { rating, comment }, auth(token)),

  // Unauthenticated, per tenant. These carry the statutory publication duties
  // (DPDP s.5(1), s.13, s.32) and are fetched without the context token on
  // purpose, so the rights and DPO contact still render on the verification
  // screen - before the principal has proved anything.
  rights: (tenantCode: string) => client.get<PublicRights>(`/public/${encodeURIComponent(tenantCode)}/rights`),
  privacyContact: (tenantCode: string) =>
    client.get<PublicPrivacyContact>(`/public/${encodeURIComponent(tenantCode)}/privacy-contact`),
  notice: (tenantCode: string, purposeCode: string, lang: string) =>
    client.get<PublicNotice>(
      `/public/${encodeURIComponent(tenantCode)}/notices/${encodeURIComponent(purposeCode)}?lang=${encodeURIComponent(lang)}`,
    ),
}

/**
 * The tenant code and expiry, read out of the context token's own payload.
 *
 * This decodes the JWT WITHOUT verifying it, which is safe here and nowhere
 * else, because of what the two values are used for:
 *
 *  - `source_app` is used only to fetch this tenant's PUBLIC rights and DPO
 *    contact (`/public/{tenant_code}/...`), which are published to the world
 *    and carry no authorisation. A tampered value yields a 404 and an empty
 *    rights panel - it cannot reveal anything.
 *  - `exp` drives a countdown so the principal is told the link is about to
 *    expire rather than discovering it through a failed action. The server
 *    enforces the real expiry on every call regardless.
 *
 * Nothing here is ever treated as proof of identity. The server verifies the
 * signature on every single /portal, /receipts and /grievances call.
 */
export function readTokenClaims(token: string): { sourceApp: string; expiresAt: number | null } {
  try {
    const payload = token.split('.')[1]
    if (!payload) return { sourceApp: '', expiresAt: null }
    const normalised = payload.replace(/-/g, '+').replace(/_/g, '/')
    const json = JSON.parse(decodeURIComponent(escape(atob(normalised))))
    return {
      sourceApp: typeof json.source_app === 'string' ? json.source_app : '',
      expiresAt: typeof json.exp === 'number' ? json.exp * 1000 : null,
    }
  } catch {
    return { sourceApp: '', expiresAt: null }
  }
}

/**
 * W3C Global Privacy Control (R2-10 / gap Q-07). There is no client-side way
 * to read the actual `Sec-GPC` request header - the server reads that itself
 * (app/api/routes/portal.py::_read_gpc_signal) and stamps it as fact on the
 * evidence row. This is only the client's own *claim*, sent alongside it as
 * ClientContext.gpc_signal and kept separately (details.claimed_gpc_signal)
 * so the two are never collapsed into one value.
 */
export function gpcSignalDetected(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    (navigator as unknown as { globalPrivacyControl?: boolean }).globalPrivacyControl === true
  )
}

/**
 * Purpose text in the principal's chosen language, falling back to the base
 * (English) fields when that language has no entry, or is missing individual
 * keys within it. Same contract as the demo clients' `localize()` - see
 * crm/src/components/NoticeScreen.tsx - so a purpose translated once renders
 * identically on the banner and here.
 */
export function localizePurpose(p: PortalPurpose, lang: string) {
  const t = (lang && p.translations?.[lang]) || {}
  return {
    name: t.name || p.name,
    description: t.description || p.description,
    consent_text: t.consent_text || p.consent_text,
  }
}

/**
 * Hands the browser a downloaded file from an already-fetched blob response,
 * preserving the filename the SERVER chose in Content-Disposition. That
 * filename carries the principal's external id and a UTC timestamp
 * (`consent-record-<id>-<stamp>.<fmt>`), which is what makes two exports
 * distinguishable on disk months later - re-inventing it client-side would
 * quietly drop that.
 */
export function saveBlobResponse(res: AxiosResponse<Blob>, fallbackName: string): string {
  const disposition = String(res.headers['content-disposition'] || '')
  const match = /filename="?([^"]+)"?/.exec(disposition)
  const name = match?.[1] || fallbackName
  const url = URL.createObjectURL(res.data)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  // Revoking synchronously can cancel the download in some browsers; one tick
  // is enough for the click to have been dispatched.
  setTimeout(() => URL.revokeObjectURL(url), 1000)
  return name
}
