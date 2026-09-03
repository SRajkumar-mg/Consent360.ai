import axios from 'axios'
import type { CrmCustomer, CrmLoginResponse } from './types'

export const api = axios.create({ baseURL: '/api' })
export const consentHttp = axios.create({ baseURL: '/cmp' })

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

export const consentApi = {
  getConsentPreferences: (customerId: number) =>
    consentHttp.get(`/crm/customers/${customerId}/consent-preferences`),
  saveConsentPreferences: (
    customerId: number,
    prefs: { lang: string; categories: Record<string, boolean> }
  ) =>
    consentHttp.put(`/crm/customers/${customerId}/consent-preferences`, prefs),
}