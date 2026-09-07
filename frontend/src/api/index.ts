import { api } from './client'
import type {
  AuditEvent,
  EvidencePackAvailability,
  Kpi,
  KpiCatalogue,
  KpiTrend,
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
} from '../types'

const authHeaders = () => ({})

export const authApi = {
  login: (username: string, password: string) =>
    api.post<LoginResponse>('/auth/login', { username, password }),
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
  // Customer-context creation is an integration-key call meant for a CRM's own
  // backend, not for staff browsing the admin console — no wrapper for it lives
  // here. Fetch it if the admin console ever needs to demonstrate the flow, but
  // pass the key in explicitly (e.g. from a staff-entered value); never bake a
  // default key into the browser bundle.
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
  addVersion: (id: number, data: { reason?: string; checklist?: Record<string, unknown> }) =>
    api.post<Purpose>(`/purposes/${id}/versions`, data),
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
}

export const policiesApi = {
  list: () => api.get<Policy[]>('/policies'),
  create: (data: {
    name: string
    code: string
    description?: string
    default_decision: string
    rules: PolicyRulePayload[]
    checklist?: Record<string, unknown>
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
  updateSettings: (
    id: number,
    data: Partial<Pick<Organization, 'dpo_name' | 'dpo_email' | 'dpo_phone' | 'withdraw_url' | 'rights_url' | 'grievance_url' | 'board_complaint_url' | 'grievance_response_days' | 'default_language' | 'environment'>>,
  ) => api.put<Organization>(`/organizations/${id}/settings`, data),
}


// R2-07: the compliance KPI catalogue (the K-series of the DPDP gap register).
//
// `evidencePack` deliberately targets `/retention/evidence-pack`, which R1-10
// owns, rather than anything under /analytics. That endpoint stamps the pack
// with a SHA-256 manifest hash and an HMAC signature a regulator has to be
// able to re-derive; a second export would produce a second hash over a
// second serialisation of the same period and the two would disagree. The
// dashboard links to the one real export and asks
// `evidencePackAvailability()` whether this role may use it.
export const kpiApi = {
  catalogue: (params: {
    period_days?: number
    source_app?: string
    purpose_code?: string
    language?: string
  } = {}) => api.get<KpiCatalogue>('/analytics/kpis', { params }),
  detail: (
    kpiId: string,
    params: { period_days?: number; source_app?: string; purpose_code?: string; language?: string } = {},
  ) => api.get<Kpi>(`/analytics/kpis/${kpiId}`, { params }),
  trend: (
    kpiId: string,
    params: { period_days?: number; source_app?: string; group_by?: 'day' | 'week' | 'month' } = {},
  ) => api.get<KpiTrend>(`/analytics/kpis/${kpiId}/trend`, { params }),
  evidencePackAvailability: () =>
    api.get<EvidencePackAvailability>('/analytics/evidence-pack-availability'),
  evidencePack: (params: { period_start?: string; period_end?: string; tenant_code?: string } = {}) =>
    api.get<Record<string, unknown>>('/retention/evidence-pack', { params }),
}

// ---------------------------------------------------------------------------
// R2-08: wrappers for the modules that landed without an admin screen.
//
// Import kept separate from the block at the top of the file so this section
// reads as one unit alongside the pages that consume it.
// ---------------------------------------------------------------------------
import type {
  AccessReview,
  Breach,
  BreachDetail,
  BreachExtension,
  BreachMetrics,
  BreachNotification,
  BreachReportPreview,
  ConsentManagerMetrics,
  ConsentManagerRegistration,
  ConsentReceipt,
  CookiePolicyVersion,
  DataItem,
  DataSharingEvent,
  ErasureJob,
  ErasureMetrics,
  ErasureScanResult,
  FiduciaryOnboarding,
  Grievance,
  GrievanceStats,
  LegacyCohort,
  LegacyNoticeCampaign,
  LegacyNoticeMetrics,
  LegacyNoticeSendResult,
  LegalHold,
  MaterialityRule,
  NotificationDispatchResult,
  Notice,
  NoticeLangContent,
  NotificationMetrics,
  NotificationRow,
  NotificationTemplate,
  Objection,
  PolicyChange,
  Processor,
  ProcessorAlert,
  ProcessorCoverage,
  ProcessorDispatchResult,
  ProcessorSecret,
  ReConsentCampaign,
  ReConsentMetrics,
  RetentionPolicy,
  RetentionScheduleRow,
} from '../types'

export interface NoticePayload {
  purpose_id: number
  language_default: string
  title: string
  body: string
  translations: Record<string, NoticeLangContent>
  data_items: DataItem[]
  services_enabled: string
  retention_period_days: number | null
  retention_note: string
  child_restricted: boolean
  // A-09: the plain-language & dark-pattern review record - required by
  // POST /notices/{id}/publish before the draft this targets can go live.
  checklist?: Record<string, unknown>
}

export const noticesApi = {
  list: () => api.get<Notice[]>('/notices'),
  get: (id: number) => api.get<Notice>(`/notices/${id}`),
  create: (data: NoticePayload) => api.post<Notice>('/notices', data),
  update: (id: number, data: Partial<NoticePayload> & { is_active?: boolean }) =>
    api.put<Notice>(`/notices/${id}`, data),
  publish: (id: number) => api.post<Notice>(`/notices/${id}/publish`),
  remove: (id: number) => api.delete<{ deleted?: boolean; retired?: boolean }>(`/notices/${id}`),
}

export const grievancesApi = {
  list: (params: {
    status?: string
    category?: string
    assigned_to?: string
    customer_external_id?: string
    open_only?: boolean
    overdue_only?: boolean
    limit?: number
    offset?: number
  } = {}) => api.get<Grievance[]>('/grievances', { params }),
  stats: () => api.get<GrievanceStats>('/grievances/stats'),
  get: (ref: string) => api.get<Grievance>(`/grievances/${ref}`),
  create: (data: {
    customer_external_id: string
    category: string
    subject?: string
    description: string
    channel?: string
    purpose_code?: string | null
    consent_id?: number | null
  }) => api.post<{ grievance: Grievance; reference_no: string; due_at: string }>('/grievances', data),
  update: (ref: string, data: { assigned_to?: string | null; status?: string | null; note?: string; visible_to_principal?: boolean }) =>
    api.patch<Grievance>(`/grievances/${ref}`, data),
  escalate: (ref: string, data: { reason?: string; note?: string }) =>
    api.post<Grievance>(`/grievances/${ref}/escalate`, data),
  resolve: (ref: string, data: { resolution_summary: string }) =>
    api.post<Grievance>(`/grievances/${ref}/resolve`, data),
  close: (ref: string, data: { note?: string }) =>
    api.post<Grievance>(`/grievances/${ref}/close`, data),
}

export const objectionsApi = {
  list: (params: { customer_external_id?: string } = {}) =>
    api.get<Objection[]>('/objections', { params }),
  resolve: (id: number, data: { resolution_note?: string }) =>
    api.post<Objection>(`/objections/${id}/resolve`, data),
}

// R3-08: the breach register. Report *content* (`report*`, `notificationContent`)
// is gated on audit.export, not breach.view — a principal notice quotes that
// principal's own data and a Board report aggregates every affected principal,
// so reading one is an export-grade act. The page asks before offering it.
export const breachesApi = {
  list: (params: { status?: string } = {}) => api.get<Breach[]>('/breaches', { params }),
  metrics: (params: { source_app?: string } = {}) =>
    api.get<BreachMetrics>('/breaches/metrics', { params }),
  register: (params: { source_app?: string; period_start?: string; period_end?: string } = {}) =>
    api.get<Record<string, unknown>>('/breaches/register', { params }),
  get: (ref: string) => api.get<BreachDetail>(`/breaches/${ref}`),
  create: (data: Record<string, unknown>) => api.post<BreachDetail>('/breaches', data),
  update: (ref: string, data: Record<string, unknown>) =>
    api.patch<BreachDetail>(`/breaches/${ref}`, data),
  markAware: (ref: string, data: { aware_at?: string | null }) =>
    api.post<BreachDetail>(`/breaches/${ref}/aware`, data),
  changeStatus: (ref: string, data: { status: string; note?: string }) =>
    api.post<BreachDetail>(`/breaches/${ref}/status`, data),
  clocks: (ref: string) => api.get<{ breach_ref: string; aware_at: string | null; detected_at: string; clocks: BreachDetail['clocks']; timeline: BreachDetail['timeline'] }>(`/breaches/${ref}/clocks`),
  addAffected: (ref: string, data: { external_ids: string[]; data_involved?: string }) =>
    api.post<{ added: number; already_present: number; unresolved: string[]; affected_count: number }>(`/breaches/${ref}/affected`, data),
  finaliseAffected: (ref: string) => api.post<BreachDetail>(`/breaches/${ref}/affected/finalise`),
  report: (ref: string, kind: 'board-initial' | 'board-detailed' | 'cert-in') =>
    api.get<BreachReportPreview>(`/breaches/${ref}/reports/${kind}`),
  notifyPrincipals: (ref: string, data: { language?: string | null }) =>
    api.post<{ generated: number; already_issued: number; breach_notification_ids: number[] }>(`/breaches/${ref}/notify/principals`, data),
  notifyBoardInitial: (ref: string) => api.post<BreachNotification>(`/breaches/${ref}/notify/board-initial`),
  notifyBoardDetailed: (ref: string) => api.post<BreachNotification>(`/breaches/${ref}/notify/board-detailed`),
  notifyCertIn: (ref: string) => api.post<BreachNotification>(`/breaches/${ref}/notify/cert-in`),
  notifications: (ref: string) => api.get<BreachNotification[]>(`/breaches/${ref}/notifications`),
  recordFiling: (ref: string, notificationId: number, data: { filing_reference: string; filed_at?: string | null; delivered?: boolean }) =>
    api.post<BreachNotification>(`/breaches/${ref}/notifications/${notificationId}/record-filing`, data),
  verifyNotification: (ref: string, notificationId: number) =>
    api.get<{ recorded_hash: string; recomputed_hash: string; matches: boolean; algorithm: string }>(`/breaches/${ref}/notifications/${notificationId}/verify`),
  extensions: (ref: string) => api.get<BreachExtension[]>(`/breaches/${ref}/extensions`),
  requestExtension: (ref: string, data: { requested_until: string; reason: string; written_request_ref?: string }) =>
    api.post<BreachExtension>(`/breaches/${ref}/extensions`, data),
  decideExtension: (ref: string, extensionId: number, data: { status: string; granted_until?: string | null; board_reference?: string; decision_note?: string }) =>
    api.post<BreachExtension>(`/breaches/${ref}/extensions/${extensionId}/decision`, data),
}

export const erasureApi = {
  policies: () => api.get<RetentionPolicy[]>('/erasure/policies'),
  upsertPolicy: (data: {
    record_class: string
    scope?: string
    retention_days?: number | null
    inactivity_days?: number | null
    pre_erasure_notice_hours?: number
    action?: 'ERASE' | 'ANONYMISE'
    legal_basis_for_retention: string
    is_active?: boolean
    notes?: string
  }) => api.put<RetentionPolicy>('/erasure/policies', data),
  holds: (params: { active_only?: boolean } = {}) =>
    api.get<LegalHold[]>('/erasure/holds', { params }),
  placeHold: (data: {
    legal_basis: string
    reason?: string
    customer_external_id?: string | null
    record_class?: string | null
    tenant_code?: string | null
    expires_at?: string | null
  }) => api.post<LegalHold>('/erasure/holds', data),
  releaseHold: (ref: string, data: { release_reason: string }) =>
    api.post<LegalHold>(`/erasure/holds/${ref}/release`, data),
  jobs: (params: { status?: string; customer_external_id?: string; limit?: number } = {}) =>
    api.get<ErasureJob[]>('/erasure/jobs', { params }),
  job: (ref: string) => api.get<ErasureJob>(`/erasure/jobs/${ref}`),
  raiseJob: (data: {
    customer_external_id: string
    trigger?: 'RIGHTS_REQUEST' | 'MANUAL'
    request_ref?: string | null
    reason?: string
    authorisation_basis: string
  }) => api.post<ErasureJob>('/erasure/jobs', data),
  authorise: (ref: string, data: { basis: string }) =>
    api.post<ErasureJob>(`/erasure/jobs/${ref}/authorise`, data),
  sendNotice: (ref: string) => api.post<ErasureJob>(`/erasure/jobs/${ref}/notice`),
  execute: (ref: string) => api.post<ErasureJob>(`/erasure/jobs/${ref}/execute`, {}),
  cancel: (ref: string, data: { reason: string }) =>
    api.post<ErasureJob>(`/erasure/jobs/${ref}/cancel`, data),
  metrics: () => api.get<ErasureMetrics>('/erasure/metrics'),
  scanRetention: (propose: boolean) =>
    api.post<ErasureScanResult>('/erasure/scan/retention', null, { params: { propose } }),
  scanInactivity: (propose: boolean) =>
    api.post<ErasureScanResult>('/erasure/scan/inactivity', null, { params: { propose } }),
}

export const retentionApi = {
  schedule: () => api.get<RetentionScheduleRow[]>('/retention/schedule'),
  updateSchedule: (recordClass: string, data: { retention_days: number }) =>
    api.put<RetentionScheduleRow>(`/retention/schedule/${recordClass}`, data),
  scan: () => api.get<Record<string, unknown>>('/retention/scan'),
}

export const processorsApi = {
  list: (params: { include_inactive?: boolean } = {}) =>
    api.get<Processor[]>('/processors', { params }),
  get: (id: number) => api.get<Processor>(`/processors/${id}`),
  create: (data: Record<string, unknown>) =>
    api.post<Processor & { webhook_secret: string }>('/processors', data),
  update: (id: number, data: Record<string, unknown>) =>
    api.put<Processor>(`/processors/${id}`, data),
  deactivate: (id: number) => api.delete<{ message: string }>(`/processors/${id}`),
  rotateSecret: (id: number) => api.post<ProcessorSecret>(`/processors/${id}/rotate-webhook-secret`),
  alerts: (params: { status?: string; alert_type?: string; processor_id?: number; trigger_ref?: string; limit?: number } = {}) =>
    api.get<ProcessorAlert[]>('/processors/alerts', { params }),
  ackManual: (ref: string, data: { acknowledged_by: string; reference?: string; note?: string }) =>
    api.post<ProcessorAlert>(`/processors/alerts/${ref}/ack-manual`, data),
  dispatch: () => api.post<ProcessorDispatchResult>('/processors/alerts/dispatch'),
  coverage: () => api.get<ProcessorCoverage>('/processors/reports/contract-coverage'),
  propagationSla: (params: { date_from?: string; date_to?: string } = {}) =>
    api.get<Record<string, unknown>>('/processors/reports/propagation-sla', { params }),
}

export const sharingEventsApi = {
  list: (params: { customer_external_id?: string } = {}) =>
    api.get<DataSharingEvent[]>('/sharing-events', { params }),
}

export const reConsentApi = {
  campaigns: (params: { status?: string; limit?: number } = {}) =>
    api.get<ReConsentCampaign[]>('/re-consent/campaigns', { params }),
  campaign: (ref: string) => api.get<ReConsentCampaign>(`/re-consent/campaigns/${ref}`),
  closeCampaign: (ref: string, data: { status?: 'COMPLETED' | 'CANCELLED'; reason?: string }) =>
    api.post<ReConsentCampaign>(`/re-consent/campaigns/${ref}/close`, data),
  changes: (params: { entity_type?: string; materiality?: string; limit?: number } = {}) =>
    api.get<PolicyChange[]>('/re-consent/changes', { params }),
  rules: () => api.get<MaterialityRule[]>('/re-consent/rules'),
  metrics: () => api.get<ReConsentMetrics>('/re-consent/metrics'),
  cookiePolicy: (params: { tenant_code?: string } = {}) =>
    api.get<CookiePolicyVersion | null>('/re-consent/cookie-policy', { params }),
  cookiePolicyVersions: (params: { tenant_code?: string } = {}) =>
    api.get<CookiePolicyVersion[]>('/re-consent/cookie-policy/versions', { params }),
}

/**
 * R2-11 / gap A-08: the s.5(2) legacy-notice campaign.
 *
 * Under `/re-consent/*` because it shares that router and that screen, not
 * because a legacy notice is re-consent - see the note at the head of
 * backend/app/api/routes/reconsent.py.
 *
 * `send` returns what was QUEUED. Nothing is delivered until `dispatch` (or
 * the scheduled notification job) has run, and only `campaign()`'s own
 * per-status counts may be read as delivery.
 */
export const legacyNoticeApi = {
  cohort: (params: { cutoff?: string; source_app?: string; limit?: number } = {}) =>
    api.get<LegacyCohort>('/re-consent/legacy-notice/cohort', { params }),
  send: (data: {
    cutoff: string
    source_app?: string | null
    channels?: string[] | null
    language?: string
    note?: string
    include_notified?: boolean
    limit?: number | null
  }) => api.post<LegacyNoticeSendResult>('/re-consent/legacy-notice/campaigns', data),
  campaigns: (params: { limit?: number } = {}) =>
    api.get<LegacyNoticeCampaign[]>('/re-consent/legacy-notice/campaigns', { params }),
  campaign: (ref: string) =>
    api.get<LegacyNoticeCampaign>(`/re-consent/legacy-notice/campaigns/${encodeURIComponent(ref)}`),
  metrics: (params: { cutoff?: string; source_app?: string } = {}) =>
    api.get<LegacyNoticeMetrics>('/re-consent/legacy-notice/metrics', { params }),
  dispatch: () => api.post<NotificationDispatchResult>('/re-consent/legacy-notice/dispatch'),
}

export const notificationsApi = {
  list: (params: { customer_external_id?: string; event_type?: string; status?: string; limit?: number } = {}) =>
    api.get<NotificationRow[]>('/notifications', { params }),
  metrics: () => api.get<NotificationMetrics>('/notifications/metrics'),
  templates: () => api.get<NotificationTemplate[]>('/notifications/templates'),
  createTemplate: (data: {
    event_type: string
    channel: string
    language?: string
    subject?: string
    body_template: string
    is_active?: boolean
  }) => api.post<NotificationTemplate>('/notifications/templates', data),
  updateTemplate: (id: number, data: { subject?: string | null; body_template?: string | null; is_active?: boolean | null }) =>
    api.put<NotificationTemplate>(`/notifications/templates/${id}`, data),
}

export const receiptsApi = {
  list: (params: { customer_external_id?: string; consent_id?: number } = {}) =>
    api.get<ConsentReceipt[]>('/receipts', { params }),
  get: (ref: string) => api.get<ConsentReceipt>(`/receipts/${ref}`),
}

export const consentManagerApi = {
  registrations: () => api.get<ConsentManagerRegistration[]>('/consent-manager/registrations'),
  register: (data: Record<string, unknown>) =>
    api.post<ConsentManagerRegistration>('/consent-manager/registrations', data),
  updateRegistration: (ref: string, data: Record<string, unknown>) =>
    api.patch<ConsentManagerRegistration>(`/consent-manager/registrations/${ref}`, data),
  fiduciaries: (ref: string) =>
    api.get<FiduciaryOnboarding[]>(`/consent-manager/registrations/${ref}/fiduciaries`),
  onboardFiduciary: (ref: string, data: Record<string, unknown>) =>
    api.post<FiduciaryOnboarding>(`/consent-manager/registrations/${ref}/fiduciaries`, data),
  updateOnboarding: (ref: string, sourceApp: string, data: Record<string, unknown>) =>
    api.patch<FiduciaryOnboarding>(`/consent-manager/registrations/${ref}/fiduciaries/${sourceApp}`, data),
  metrics: (params: { window_hours?: number } = {}) =>
    api.get<ConsentManagerMetrics>('/consent-manager/metrics', { params }),
}

// R3-02/R-04: role CRUD and the periodic access review, alongside the user
// CRUD already in `adminApi`. `sync_roles()` never touches roles outside
// rbac.py's ROLE_PERMISSIONS, so these endpoints manage custom roles; the
// seeded ones come back with is_system true and are refused by the API.
export const rolesApi = {
  list: () => api.get<import('../types').Role[]>('/admin/roles'),
  create: (data: { name: string; description?: string; permissions: string[] }) =>
    api.post<import('../types').Role>('/admin/roles', data),
  update: (id: number, data: { description?: string; permissions?: string[] }) =>
    api.put<import('../types').Role>(`/admin/roles/${id}`, data),
  remove: (id: number) => api.delete<{ deleted: boolean; role_id: number }>(`/admin/roles/${id}`),
  accessReview: () => api.get<AccessReview>('/admin/access-review'),
}
