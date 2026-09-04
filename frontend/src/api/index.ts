import { api } from './client'
import type {
  AuditEvent,
  ContextResponse,
  Customer,
  CustomerConsentSummary,
  DashboardResponse,
  DataCategory,
  LoginResponse,
  Organization,
  OrganizationDashboard,
  OrganizationUser,
  ProcessingActivity,
  Purpose,
  Role,
  AdminUser,
  User,
  Consent,
  ConsentDetail,
  ConsentEvidence,
  Policy,
  PolicyRulePayload,
  Tenant,
  Notice,
  LegalDocument,
  NoticeRendered,
  AuditChainResult,
  LawfulGatewayReport,
  DecisionsReport,
  ComplianceEvidencePack,
  Objection,
  ErasureJob,
  SharingEvent,
  ApiKey,
  ApiKeyCreateResponse,
  MfaEnrollResponse,
  NotificationTemplate,
  Notification,
  Processor,
  ProcessorAlert,
  ContractCoverageReport,
  Breach,
  BreachNotification,
  BoardReport,
} from '../types'

const authHeaders = () => ({})

export const authApi = {
  login: (username: string, password: string, otp_code?: string) =>
    api.post<LoginResponse>('/auth/login', { username, password, otp_code }),
  refresh: (refresh_token: string) =>
    api.post<LoginResponse>('/auth/refresh', { refresh_token }),
  me: () => api.get<User>('/auth/me'),
  logout: () => api.post('/auth/logout'),
}

export const customersApi = {
  list: (search = '', limit = 100, offset = 0) =>
    api.get<Customer[]>('/customers', { params: { search, limit, offset } }),
  get: (id: string) => api.get<Customer>(`/customers/${id}`),
}

export const integrationApi = {
  createContext: (data: {
    customer_id?: string
    name: string
    email?: string
    phone?: string
    status?: string
    source_app?: string
  }) =>
    api.post<ContextResponse>(
      '/consent/customer-context',
      data,
      // R3-01: tenant-bound API keys - no hardcoded shared-secret fallback.
      { headers: { 'X-API-Key': (import.meta.env.VITE_INTEGRATION_API_KEY as string) || '' } },
    ),
  consumeContext: (token: string) => api.get<{ customer: Customer; token_type: string }>(`/consent/context/consume/${token}`),
}

export const consentsApi = {
  summary: (customerId: string) =>
    api.get<CustomerConsentSummary>(`/consents/summary/${customerId}`),
  detail: (consentId: number) => api.get<ConsentDetail>(`/consents/${consentId}`),
  list: (params?: { customer_id?: string; purpose_id?: number; status?: string }) =>
    api.get<Consent[]>('/consents', { params }),
  expiring: (days = 30) => api.get<Consent[]>('/consents/expiring', { params: { days } }),
  grant: (id: number, data: { reason?: string; collection_method?: string; expires_in_days?: number; consent_text?: string }) =>
    api.post<Consent>(`/consents/${id}/grant`, data),
  deny: (id: number, data: { reason?: string }) =>
    api.post<Consent>(`/consents/${id}/deny`, data),
  withdraw: (id: number, data: { reason?: string }) =>
    api.post<Consent>(`/consents/${id}/withdraw`, data),
  renew: (id: number, data: { reason?: string; expires_in_days?: number }) =>
    api.post<Consent>(`/consents/${id}/renew`, data),
  request: (id: number, data: { reason?: string }) =>
    api.post<Consent>(`/consents/${id}/request`, data),
  expireBatch: () => api.post('/consents/expire-batch'),
  exportCustomer: (customerId: string) =>
    api.get(`/consents/export/${customerId}`, { responseType: 'blob' }),
}

export const purposesApi = {
  list: () => api.get<Purpose[]>('/purposes'),
  get: (id: number) => api.get<Purpose>(`/purposes/${id}`),
  create: (data: Record<string, unknown>) => api.post<Purpose>('/purposes', data),
  update: (id: number, data: Record<string, unknown>) => api.put<Purpose>(`/purposes/${id}`, data),
  addVersion: (id: number, data: { reason?: string }) => api.post<Purpose>(`/purposes/${id}/versions`, data),
}

export const categoriesApi = {
  list: () => api.get<DataCategory[]>('/data-categories'),
  create: (data: Partial<DataCategory>) => api.post<DataCategory>('/data-categories', data),
  update: (id: number, data: Partial<DataCategory>) => api.put<DataCategory>(`/data-categories/${id}`, data),
}

export const activitiesApi = {
  list: () => api.get<ProcessingActivity[]>('/processing-activities'),
  create: (data: Partial<ProcessingActivity>) => api.post<ProcessingActivity>('/processing-activities', data),
  update: (id: number, data: Partial<ProcessingActivity>) => api.put<ProcessingActivity>(`/processing-activities/${id}`, data),
}

export const auditApi = {
  list: (params: Record<string, string | number | undefined>) =>
    api.get<AuditEvent[]>('/audit', { params }),
  events: () => api.get<string[]>('/audit/events'),
  actors: () => api.get<string[]>('/audit/actors'),
  exportFormats: (params: { format: string; customer_name?: string; purpose_code?: string; date_from?: string; date_to?: string }) =>
    api.get('/audit/export-formats', { params, responseType: 'blob' }),
}

export const policiesApi = {
  list: () => api.get<Policy[]>('/policies'),
  create: (data: {
    name: string
    code: string
    description?: string
    default_decision: string
    rules: PolicyRulePayload[]
  }) => api.post<Policy>('/policies', data),
  update: (id: number, data: Record<string, unknown>) =>
    api.put<Policy>(`/policies/${id}`, data),
  remove: (id: number) => api.delete<{ deleted: boolean; retired?: boolean; reason?: string }>(`/policies/${id}`),
}

export const dashboardApi = {
  get: () => api.get<DashboardResponse>('/dashboard'),
}

export const adminApi = {
  roles: () => api.get<Role[]>('/admin/roles'),
  users: () => api.get<AdminUser[]>('/auth/users'),
  createUser: (data: {
    username: string
    full_name: string
    email: string
    password: string
    role_id: number
    is_active: boolean
  }) => api.post<AdminUser>('/auth/users', data),
  updateUser: (id: number, data: Partial<AdminUser>) => api.put<AdminUser>(`/auth/users/${id}`, data),
}

export const organizationsApi = {
  list: () => api.get<Organization[]>('/organizations'),
  get: (id: number) => api.get<Organization>(`/organizations/${id}`),
  create: (data: { name: string; code: string; domain?: string; description?: string }) =>
    api.post<Organization>('/organizations', data),
  update: (id: number, data: Partial<Organization>) =>
    api.put<Organization>(`/organizations/${id}`, data),
  listUsers: (orgId: number) => api.get<OrganizationUser[]>(`/organizations/${orgId}/users`),
  createUser: (orgId: number, data: { username: string; full_name: string; email: string; password: string; role: string }) =>
    api.post<OrganizationUser>(`/organizations/${orgId}/users`, data),
  getDashboard: (orgId: number) =>
    api.get<OrganizationDashboard>(`/organizations/${orgId}/dashboard`),
  getPortalUsers: (orgId: number) =>
    api.get<{ customers: Customer[]; total_customers: number; consent_summary: { status: string; count: number }[] }>(
      `/organizations/${orgId}/portal-users`,
    ),
  getRoles: (orgId: number) =>
    api.get<{ value: string; label: string; description: string }[]>(`/organizations/${orgId}/roles`),
}

// ---- R1-01 / R3-01: tenants + tenant-bound API key management ----
export const tenantsApi = {
  list: () => api.get<Tenant[]>('/tenants'),
  get: (id: number) => api.get<Tenant>(`/tenants/${id}`),
  create: (data: Record<string, unknown>) => api.post<Tenant>('/tenants', data),
  update: (id: number, data: Record<string, unknown>) => api.patch<Tenant>(`/tenants/${id}`, data),
  listApiKeys: (tenantId: number) =>
    api.get<ApiKey[]>(`/tenants/${tenantId}/api-keys`),
  createApiKey: (tenantId: number, data: { name?: string; scopes?: string; expires_in_days?: number | null }) =>
    api.post<ApiKeyCreateResponse>(`/tenants/${tenantId}/api-keys`, data),
  rotateApiKey: (tenantId: number, keyId: number) =>
    api.post<ApiKeyCreateResponse>(`/tenants/${tenantId}/api-keys/${keyId}/rotate`),
  revokeApiKey: (tenantId: number, keyId: number) =>
    api.delete<{ deleted: boolean; key_id: number }>(`/tenants/${tenantId}/api-keys/${keyId}`),
}

// ── R3-02: MFA ──────────────────────────────────────────────────────────────
export const mfaApi = {
  enroll: () => api.post<MfaEnrollResponse>('/auth/mfa/enroll'),
  verify: (otp_code: string) =>
    api.post<{ mfa_verified: boolean }>('/auth/mfa/verify', { otp_code }),
  recoveryCodes: () =>
    api.get<{ recovery_codes: string[]; count: number }>('/auth/mfa/recovery-codes'),
}

// ── R3-06: Notifications ────────────────────────────────────────────────────
export const notificationsApi = {
  listTemplates: (tenantId?: number) =>
    api.get<NotificationTemplate[]>('/notifications/templates', { params: tenantId ? { tenant_id: tenantId } : {} }),
  createTemplate: (data: {
    tenant_id: number; event_type: string; channel: string;
    language?: string; subject?: string; body: string
  }) => api.post<NotificationTemplate>('/notifications/templates', data),
  listPending: () => api.get<Notification[]>('/notifications/pending'),
  retry: () => api.post<{ retried: number }>('/notifications/retry'),
}

// ── R3-07: Processors ───────────────────────────────────────────────────────
export const processorsApi = {
  list: (tenantId?: number) =>
    api.get<Processor[]>('/processors', { params: tenantId ? { tenant_id: tenantId } : {} }),
  create: (data: {
    tenant_id: number; name: string; type?: string; country?: string;
    contact?: string; contract_ref?: string; webhook_url?: string; webhook_secret?: string
  }) => api.post<Processor>('/processors', data),
  listAlerts: (processorId: number) =>
    api.get<ProcessorAlert[]>(`/processors/${processorId}/alerts`),
  notifyErasure: (customerId: number, processorIds: number[]) =>
    api.post<{ alerts_sent: number }>('/processors/notify-erasure', null, {
      params: { customer_id: customerId, processor_ids: processorIds },
    }),
  checkEscalations: () =>
    api.post<{ escalated: number }>('/processors/check-escalations'),
  contractCoverage: (tenantId?: number) =>
    api.get<ContractCoverageReport>('/processors/reports/contract-coverage', {
      params: tenantId ? { tenant_id: tenantId } : {},
    }),
}

// ── R3-08: Breaches ─────────────────────────────────────────────────────────
export const breachesApi = {
  list: (params?: { tenant_id?: number; status?: string }) =>
    api.get<Breach[]>('/breaches', { params }),
  create: (data: {
    tenant_id: number; breach_type: string; detected_at: string; aware_at: string;
    nature?: string; extent?: string; timing?: string; location?: string;
    likely_impact?: string; cause?: string; mitigation?: string;
    remedial_measures?: string; findings_on_actor?: string
  }) => api.post<Breach>('/breaches', data),
  listNotifications: (breachId: number) =>
    api.get<BreachNotification[]>(`/breaches/${breachId}/notifications`),
  boardReport: (breachId: number) =>
    api.get<BoardReport>(`/breaches/${breachId}/board-report`),
  notifyPrincipal: (breachId: number, customerId: number, recipientEmail: string) =>
    api.post<{ sent: boolean }>(`/breaches/${breachId}/notify-principal`, null, {
      params: { customer_id: customerId, recipient_email: recipientEmail },
    }),
  createExtensionRequest: (breachId: number, data: {
    clock_type: string; reason: string; new_deadline_at?: string
  }) => api.post<{ created: boolean; id: number }>(`/breaches/${breachId}/extension-requests`, data),
  updateStatus: (breachId: number, newStatus: string) =>
    api.put<{ updated: boolean; status: string }>(`/breaches/${breachId}/status`, null, {
      params: { new_status: newStatus },
    }),
}

// ---- R1-04/R4-001: notices (consolidated) ----
export const noticesApi = {
  list: (params?: { tenant_id?: number; purpose_id?: number; language?: string; status?: string }) =>
    api.get<Notice[]>('/notices', { params }),
  create: (data: {
    tenant_id: number; purpose_id: number; version_number?: number; language?: string;
    title: string; body?: string; data_items?: any[]; services_enabled?: string;
    retention_text?: string; consent_text?: string; terms_document_id?: number | null;
    privacy_document_id?: number | null; status?: string;
  }) => api.post<Notice>('/notices', data),
  addVersion: (data: {
    notice_id: number; version_number: number; language?: string; title: string;
    body?: string; retention_text?: string; services_enabled?: string; data_items?: any[];
    consent_text?: string; terms_document_id?: number | null;
    privacy_document_id?: number | null; is_current?: boolean;
  }) => api.post<Notice>('/notices/versions', data),
  render: (noticeId: number, language?: string) =>
    api.get<NoticeRendered>(`/notices/render/${noticeId}`, { params: { language: language || 'en' } }),
}

export const legalDocsApi = {
  list: (params?: { tenant_id?: number; document_type?: string; status?: string }) =>
    api.get<LegalDocument[]>('/legal-documents', { params }),
  create: (data: {
    tenant_id: number; document_type: string; version: string; title: string;
    content?: string; language?: string; status?: string;
  }) => api.post<LegalDocument>('/legal-documents', data),
  get: (id: number) => api.get<LegalDocument>(`/legal-documents/${id}`),
  update: (id: number, data: { title?: string; content?: string; status?: string }) =>
    api.patch<LegalDocument>(`/legal-documents/${id}`, data),
  publish: (id: number) => api.post<LegalDocument>(`/legal-documents/${id}/publish`),
  getPublic: (id: number) => api.get(`/legal-documents/public/${id}`),
}

// ---- R1-02 / R1-11: audit chain + export ----
export const auditChainApi = {
  verify: () => api.get<AuditChainResult>('/audit/verify-chain'),
  export: (params: { date_from?: string; date_to?: string }) =>
    api.get('/audit/export', { params, responseType: 'blob' }),
}

// ---- R1-05 / R1-11: reports ----
export const reportsApi = {
  lawfulGateway: () => api.get<LawfulGatewayReport>('/lawful-gateway'),
  decisions: (params: { date_from?: string; date_to?: string }) =>
    api.get<DecisionsReport>('/decisions', { params }),
  complianceEvidencePack: () => api.get<ComplianceEvidencePack>('/compliance-evidence-pack'),
}

// ---- R1-06 / R1-08: rights ----
export const rightsApi = {
  listObjections: (customerId?: number) =>
    api.get<Objection[]>('/rights/objections', { params: customerId ? { customer_id: customerId } : {} }),
  createObjection: (data: { customer_id: number; purpose_id: number; source?: string }) =>
    api.post<Objection>('/rights/objections', data),
  listErasureJobs: (params?: { customer_id?: number; status?: string }) =>
    api.get<ErasureJob[]>('/rights/erasure-jobs', { params }),
  cancelErasureJob: (jobId: number) =>
    api.post<{ message: string }>(`/rights/erasure-jobs/${jobId}/cancel`),
}

// ---- R1-08: sharing events ----
export const sharingEventsApi = {
  list: (consentId?: number) =>
    api.get<SharingEvent[]>('/sharing-events', { params: consentId ? { consent_id: consentId } : {} }),
}
