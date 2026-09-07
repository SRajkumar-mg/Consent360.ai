import axios from 'axios'

const crmBackend = axios.create({ baseURL: '/api' })
const consentBackend = axios.create({ baseURL: '/cmp' })
const publicBackend = axios.create({ baseURL: '/public' })

export const crmApi = {
  login: (data: { name: string; email: string; phone?: string; source_app?: string }) =>
    crmBackend.post('/crm/login', data),
}

export interface DecisionContext {
  language?: string
  notice_version?: number
  banner_version?: string
  ui_control_id?: string
  session_id?: string
  screen_id?: string
  // R2-10 / gap Q-07: client-*claimed* Global Privacy Control signal (see
  // gpcSignalDetected() in ../consentGate) - kept separately by the backend
  // from the server-observed Sec-GPC request header, never collapsed into it.
  gpc_signal?: boolean
}

export const consentApi = {
  getPreferences: (customerId: number) =>
    consentBackend.get(`/crm/customers/${customerId}/consent-preferences`),
  savePreferences: (
    customerId: number,
    data: { lang: string; categories: Record<string, boolean>; context?: DecisionContext }
  ) =>
    consentBackend.put(`/crm/customers/${customerId}/consent-preferences`, data),
}

export const consentContextApi = {
  getConsentContext: (customerId: number) =>
    consentBackend.post<{ consent_portal_url: string }>(`/crm/customers/${customerId}/consent-context`),
}

export interface PublicPurpose {
  purpose_version_id: number
  code: string
  name: string
  description: string
  legal_basis: string
  requires_consent: boolean
  retention_period_days: number
  consent_text: string
  data_categories: string[]
  processing_activities: string[]
  translations: Record<string, Record<string, string>>
}

export interface PublicPrivacyContact {
  tenant_code: string
  tenant_name: string
  dpo_name: string
  dpo_email: string
  dpo_phone: string
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

export const publicApi = {
  purposes: (tenantCode: string) => publicBackend.get<PublicPurpose[]>(`/${tenantCode}/purposes`),
  privacyContact: (tenantCode: string) => publicBackend.get<PublicPrivacyContact>(`/${tenantCode}/privacy-contact`),
  rights: (tenantCode: string) => publicBackend.get<PublicRights>(`/${tenantCode}/rights`),
}

export function noticeVersionFrom(purposes: PublicPurpose[]): number | undefined {
  if (!purposes.length) return undefined
  return Math.max(...purposes.map((p) => p.purpose_version_id))
}

export function getErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) return err.response?.data?.detail || err.message
  if (err instanceof Error) return err.message
  return 'An unexpected error occurred'
}
