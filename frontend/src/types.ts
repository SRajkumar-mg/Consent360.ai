export interface User {
  id: number
  username: string
  full_name: string
  email: string
  role_id: number
  role_name: string
  role_permissions: string[]
  is_active: boolean
  mfa_enabled?: boolean
  last_login_at?: string | null
  created_at: string
}

export interface LoginResponse {
  access_token: string
  refresh_token: string
  token_type: string
  user: User
}

export interface Customer {
  id: number
  external_id: string
  name: string
  email: string
  phone: string
  status: string
  source_app: string
  created_at: string
}

export interface DataCategory {
  id: number
  name: string
  code: string
  description: string
  is_active: boolean
}

export interface ProcessingActivity {
  id: number
  name: string
  code: string
  description: string
  is_active: boolean
}

export interface PurposeVersion {
  id: number
  purpose_id: number
  version_number: number
  name: string
  description: string
  legal_basis: string
  requires_consent: boolean
  retention_period_days: number
  data_category_ids: number[]
  processing_activity_ids: number[]
  consent_text: string
  effective_from?: string | null
  effective_to?: string | null
  is_current: boolean
  created_by: string
}

export interface Purpose {
  id: number
  name: string
  code: string
  description: string
  legal_basis: string
  requires_consent: boolean
  retention_period_days: number
  status: string
  current_version: number
  is_active: boolean
  created_at: string
  versions: PurposeVersion[]
  data_categories: DataCategory[]
  processing_activities: ProcessingActivity[]
}

export interface Consent {
  id: number
  customer_id: number
  customer_external_id: string
  purpose_id: number
  purpose_name: string
  purpose_code: string
  purpose_version: number
  data_category_id: number
  data_category_name: string
  processing_activity_id: number
  processing_activity_name: string
  consent_version: number
  status: string
  granted_at?: string | null
  expires_at?: string | null
  denied_at?: string | null
  withdrawn_at?: string | null
  renewed_at?: string | null
  requested_at?: string | null
  collection_method: string
  source_app: string
  policy_code: string
  policy_version?: number | null
  consent_text: string
  created_at: string
}

export interface ConsentHistory {
  id: number
  action: string
  from_status?: string | null
  to_status?: string | null
  reason: string
  consent_version: number
  actor_username: string
  source_app: string
  request_id?: string | null
  details: Record<string, unknown>
  created_at: string
}

export interface ConsentEvidence {
  id: number
  evidence_ref: string
  collected_at: string
  collected_by: string
  collection_method: string
  source_app: string
  consent_version: number
  purpose_version: number
  policy_version?: number | null
  request_id?: string | null
  details: Record<string, unknown>
}

export interface ConsentDetail {
  consent: Consent
  history: ConsentHistory[]
  evidence: ConsentEvidence[]
}

export interface CustomerConsentSummary {
  customer: Customer
  total_purposes: number
  status_counts: Record<string, number>
  expiring_soon: Consent[]
  consents: Consent[]
}

export interface AuditEvent {
  id: number
  event: string
  actor_username: string
  actor_role: string
  source_app: string
  customer_id?: number | null
  customer_external_id?: string | null
  consent_id?: number | null
  purpose_id?: number | null
  purpose_code?: string | null
  policy_id?: number | null
  policy_code?: string | null
  old_status?: string | null
  new_status?: string | null
  consent_version?: number | null
  policy_version?: number | null
  decision?: string | null
  reason: string
  request_id?: string | null
  details: Record<string, unknown>
  created_at: string
}

export interface DashboardMetrics {
  total_customers: number
  total_consents: number
  active_consents: number
  pending_consents: number
  withdrawn_consents: number
  denied_consents: number
  expired_consents: number
  expiring_soon: number
  granted_consents: number
  total_purposes: number
  total_policies: number
}

export interface StatusBucket {
  status: string
  count: number
}

export interface PurposeBucket {
  purpose_code: string
  purpose_name: string
  active: number
  total: number
}

export interface DashboardResponse {
  metrics: DashboardMetrics
  status_distribution: StatusBucket[]
  purpose_distribution: PurposeBucket[]
  recent_consent_activity: AuditEvent[]
  recent_audit_events: AuditEvent[]
  expiring_consents: Consent[]
}

export interface ContextResponse {
  context_token: string
  context_id: number
  customer_id: string
  name: string
  expires_in_minutes: number
  source_app: string
  ui_url: string
  request_id?: string | null
}

export interface Role {
  id: number
  name: string
  description: string
  permissions: string[]
  is_system: boolean
}

export interface AdminUser {
  id: number
  username: string
  full_name: string
  email: string
  role_id: number
  role_name: string
  is_active: boolean
  created_at: string
}

export interface PolicyRule {
  purpose_code: string
  purpose_name: string
  data_category_code: string
  data_category_name: string
  processing_activity_code: string
  processing_activity_name: string
  decision: 'ALLOW' | 'DENY'
  requires_active_consent: boolean
  priority: number
}

export interface PolicyVersionItem {
  id: number
  policy_id: number
  version_number: number
  rules: PolicyRule[]
  default_decision: string
  effective_from?: string | null
  effective_to?: string | null
  is_current: boolean
  created_by: string
}

export interface Policy {
  id: number
  name: string
  code: string
  description: string
  status: string
  current_version: number
  is_active: boolean
  created_at: string
  versions: PolicyVersionItem[]
}

export interface PolicyRulePayload {
  purpose_code: string
  data_category_code: string
  processing_activity_code: string
  decision: 'ALLOW' | 'DENY'
  requires_active_consent: boolean
  priority: number
}

export interface Organization {
  id: number
  name: string
  code: string
  domain: string
  description: string
  logo_url: string
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface OrganizationUser {
  id: number
  organization_id: number
  username: string
  full_name: string
  role: string
  is_active: boolean
  last_login_at: string | null
  created_at: string
}

export interface OrganizationDashboard {
  organization: Organization
  users: OrganizationUser[]
  portal_users: Customer[]
  total_customers: number
  total_consents: number
  active_consents: number
  consent_summary: { status: string; count: number }[]
  recent_activity: any[]
}

// ── R3-01: Tenant & API Key types ──────────────────────────────────────────
export interface Tenant {
  id: number
  name: string
  code: string
  domain: string
  default_language: string
  is_active: boolean
  created_at: string
}

export interface ApiKey {
  id: number
  tenant_id: number
  name: string
  scopes: string
  created_at: string
  rotated_at: string | null
  expires_at: string | null
  revoked_at: string | null
  last_used_at: string | null
  is_active: boolean
}

export interface ApiKeyCreateResponse {
  key: string
  key_info: ApiKey
}

// ── R3-02: MFA types ───────────────────────────────────────────────────────
export interface MfaEnrollResponse {
  secret: string
  qr_code: string
  recovery_codes: string[]
}

// ── R3-06: Notification types ───────────────────────────────────────────────
export interface NotificationTemplate {
  id: number
  tenant_id: number
  event_type: string
  channel: string
  language: string
  subject: string
  body: string
  is_active: boolean
}

export interface Notification {
  id: number
  tenant_id: number
  event_type: string
  channel: string
  language: string
  reference_type: string
  reference_id: string
  subject: string
  status: string
  retry_count: number
  sent_at: string | null
  delivered_at: string | null
  created_at: string
}

// ── R3-07: Processor types ──────────────────────────────────────────────────
export interface Processor {
  id: number
  tenant_id: number
  name: string
  type: string
  country: string
  contact: string
  contract_ref: string | null
  contract_start: string | null
  contract_end: string | null
  is_active: boolean
  created_at: string
}

export interface ProcessorAlert {
  id: number
  processor_id: number
  alert_type: string
  status: string
  retry_count: number
  sent_at: string | null
  acknowledged_at: string | null
  escalated_at: string | null
  created_at: string
}

export interface ContractCoverageReport {
  total_processors: number
  covered_processors: number
  coverage_percentage: number
}

// ── R3-08: Breach types ─────────────────────────────────────────────────────
export interface Breach {
  id: number
  tenant_id: number
  reference_no: string
  breach_type: string
  detected_at: string
  aware_at: string
  nature: string
  extent: string
  status: string
  created_at: string
}

export interface BreachNotification {
  id: number
  breach_id: number
  recipient_type: string
  channel: string
  deadline_at: string
  sent_at: string | null
  acknowledged_at: string | null
  status: string
}

export interface BoardReport {
  overview: Record<string, unknown>
  impact: Record<string, unknown>
  timeline: Record<string, unknown>
  actions: Record<string, unknown>
  compliance: Record<string, unknown>
  next_steps: Record<string, unknown>
}