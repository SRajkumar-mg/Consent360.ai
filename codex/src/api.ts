import axios from 'axios'

const crmBackend = axios.create({ baseURL: '/api' })
const consentBackend = axios.create({ baseURL: '/cmp' })

export const crmApi = {
  login: (data: { name: string; email: string; phone?: string; source_app?: string }) =>
    crmBackend.post('/crm/login', data),
}

export const consentApi = {
  getPreferences: (customerId: number) =>
    consentBackend.get(`/crm/customers/${customerId}/consent-preferences`),
  savePreferences: (customerId: number, data: { lang: string; categories: Record<string, boolean> }) =>
    consentBackend.put(`/crm/customers/${customerId}/consent-preferences`, data),
}

export function getErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) return err.response?.data?.detail || err.message
  if (err instanceof Error) return err.message
  return 'An unexpected error occurred'
}
