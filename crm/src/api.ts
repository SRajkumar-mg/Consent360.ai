import axios from 'axios'
import type { CrmCustomer, CrmLoginResponse } from './types'

export const api = axios.create({ baseURL: '/api' })
export const consentHttp = axios.create({ baseURL: '/cmp' })
const publicBackend = axios.create({ baseURL: '/public' })

export function getErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = (err.response?.data as { detail?: string })?.detail
    if (detail) return detail
    return err.message
  }
  return String(err)
}

export interface CrmLoginPayload {
  name: string
  email: string
  phone?: string
}

export const crmApi = {
  login: (data: CrmLoginPayload) => api.post<CrmLoginResponse>('/crm/login', data),
  customers: () => api.get<CrmCustomer[]>('/crm/customers'),
  deleteCustomer: (customerId: number) => api.delete(`/crm/customers/${customerId}`),
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
  getConsentPreferences: (customerId: number) =>
    consentHttp.get(`/crm/customers/${customerId}/consent-preferences`),
  saveConsentPreferences: (
    customerId: number,
    prefs: { lang: string; categories: Record<string, boolean>; context?: DecisionContext }
  ) =>
    consentHttp.put(`/crm/customers/${customerId}/consent-preferences`, prefs),
  getConsentContext: (customerId: number) =>
    consentHttp.post<{ consent_portal_url: string }>(`/crm/customers/${customerId}/consent-context`),
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