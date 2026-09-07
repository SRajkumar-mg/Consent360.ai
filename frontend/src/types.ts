export interface User {
  id: number
  username: string
  full_name: string
  email: string
  role_id: number
  role_name: string
  role_permissions: string[]
  is_active: boolean
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

// R2-09 / gap A-09: the plain-language & dark-pattern review record, persisted
// server-side on PurposeVersion.checklist / PolicyVersion.checklist (see
// backend/app/schemas/schemas.py::ReviewChecklistOut) - snake_case to match
// the backend's own JSON field naming, unlike NoticeChecklistRecord's
// camelCase (components/NoticeChecklist.tsx), which is the in-progress-form
// shape used only while a reviewer is filling the checklist out.
export interface NoticeChecklistOut {
  reviewer: string
  completed_at: string
  items: string[]
}

/** Per-language rendering of a purpose: {lang: {name, description, consent_text}}.
 *  This exact shape is what every demo client's `localize()` reads
 *  (crm/codex/skilllearn/job-portal), so the admin editor must produce it. */
export interface PurposeLangContent {
  name?: string
  description?: string
  consent_text?: string
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
  /** [{data_category_id, necessity, description}] — the s.5(1)(i) itemisation. */
  data_items?: DataItem[]
  services_enabled?: string
  child_restricted?: boolean
  retention_policy_id?: string | null
  consent_text: string
  translations?: Record<string, PurposeLangContent>
  checklist?: NoticeChecklistOut | null
  effective_from?: string | null
  effective_to?: string | null
  is_current: boolean
  created_by: string
}

/** R1-09: how the backend classified the change this write produced. */
export interface ChangeClassification {
  change_ref: string
  materiality: 'MATERIAL' | 'NARROWING' | 'COSMETIC'
  materiality_basis: string
  campaign_ref: string | null
  consents_flagged?: number
  notifications_queued?: number
}

export interface Purpose {
  id: number
  name: string
  code: string
  description: string
  legal_basis: string
  requires_consent: boolean
  retention_period_days: number
  services_enabled?: string
  child_restricted?: boolean
  retention_policy_id?: string | null
  status: string
  current_version: number
  is_active: boolean
  created_at: string
  versions: PurposeVersion[]
  data_categories: DataCategory[]
  processing_activities: ProcessingActivity[]
  change?: ChangeClassification | null
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
  notice_version_id?: number | null
  notice_hash?: string | null
  /** Language the notice was actually rendered in when this was collected -
   *  DPDP s.5(3) evidence, and the field the principal portal shows so someone
   *  can see which language they consented in. */
  language?: string
  content_hash?: string | null
  /** Re-derived at read time: the stored evidence signature still matches. */
  signature_valid?: boolean | null
  request_id?: string | null
  details: Record<string, unknown>
  // R2-10 / gap Q-07: the server-observed Sec-GPC request header, stamped at
  // the moment this evidence row was written (app/api/routes/portal.py). Null
  // means the caller sent no header at all - distinct from the
  // client-*claimed* value the caller's request body may have carried, which
  // is kept separately under details.claimed_gpc_signal (see
  // ConsentEvidence.gpc_signal's docstring in backend/app/models/entities.py).
  gpc_signal?: boolean | null
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
  checklist?: NoticeChecklistOut | null
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
  dpo_name: string
  dpo_email: string
  dpo_phone: string
  withdraw_url: string
  rights_url: string
  grievance_url: string
  board_complaint_url: string
  grievance_response_days: number
  default_language: string
  environment: string
  settings: Record<string, unknown>
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

// R2-07 (K-series): the compliance KPI catalogue.
// Mirrors backend/app/schemas/analytics.py. The `status` field is the whole
// point of the shape: `value` is nullable and a null value must never be
// rendered as 0 - "we measured nothing" and "we measured zero" are opposite
// compliance findings. See KpiStatus below.
//
//   LIVE        - a real figure over a real denominator
//   NO_DATA     - instrumented and queried; the sample was empty
//   PARTIAL     - computed over less than the KPI's full definition (`coverage`)
//   UNAVAILABLE - no data source in this build (`unblocked_by`)
export type KpiStatus = 'LIVE' | 'NO_DATA' | 'PARTIAL' | 'UNAVAILABLE'

export interface KpiBreakdownRow {
  dimension: string
  label: string
  value: number | null
  numerator: number | null
  denominator: number | null
  unit: string
  status: KpiStatus
  detail: Record<string, unknown>
}

export interface KpiTrendPoint {
  period: string
  period_start: string
  value: number | null
  numerator: number | null
  denominator: number | null
  status: KpiStatus
}

export interface Kpi {
  id: string
  name: string
  formula: string
  domain: string
  unit: string
  target: string
  statutory_ref: string
  status: KpiStatus
  value: number | null
  /** Pre-formatted by the backend so the number and its unit cannot drift
   *  apart between screens. '—' whenever there is no value. */
  display: string
  numerator: number | null
  denominator: number | null
  sample_size: number | null
  coverage: string | null
  reason: string | null
  unblocked_by: string | null
  /** Which backend module owns this figure. The dashboard aggregates; it
   *  never recomputes a KPI another module already publishes. */
  computed_by: string | null
  supports_trend: boolean
  supports_breakdown: boolean
  breakdown: KpiBreakdownRow[]
  detail: Record<string, unknown>
}

export interface KpiCounts {
  total: number
  live: number
  no_data: number
  partial: number
  unavailable: number
}

export interface KpiCatalogue {
  generated_at: string
  scope: string | null
  scope_label: string
  scope_locked: boolean
  period_days: number
  period_start: string
  period_end: string
  purpose_filter: string | null
  language_filter: string | null
  counts: KpiCounts
  domains: string[]
  kpis: Kpi[]
  honesty_note: string
}

export interface KpiTrend {
  kpi_id: string
  name: string
  unit: string
  status: KpiStatus
  group_by: string
  scope_label: string
  points: KpiTrendPoint[]
  reason: string | null
  computed_by: string | null
}

export interface EvidencePackAvailability {
  endpoint: string
  method: string
  required_permission: string
  allowed: boolean
  scope: string | null
  scope_locked: boolean
  reason: string | null
  note: string
}

// ---------------------------------------------------------------------------
// R2-08: types for the modules that landed without an admin screen.
// Mirrors backend/app/schemas/schemas.py. Verified against
// http://127.0.0.1:8010/openapi.json rather than transcribed from a task list.
// ---------------------------------------------------------------------------

/** A per-language rendering of notice content: {lang: {title, body}}. */
export interface NoticeLangContent {
  title: string
  body: string
}

/** One itemised data element on a purpose/notice version (A-01/B-04).
 *  `necessity` marks strictly-needed vs merely-useful; the minimisation
 *  check and the principal-facing itemised list both read it. */
export interface DataItem {
  data_category_id: number
  necessity: boolean
  description: string
}

export interface NoticeVersion {
  id: number
  notice_id: number
  version_number: number
  language_default: string
  title: string
  body: string
  translations: Record<string, NoticeLangContent>
  data_items: DataItem[]
  purposes: Array<Record<string, unknown>>
  services_enabled: string
  retention_period_days: number | null
  retention_note: string
  child_restricted: boolean
  links: Record<string, string>
  contact_snapshot: Record<string, string>
  // A-09: null on a draft that hasn't been reviewed yet, or on a version
  // published before this gate existed (grandfathered - see backend
  // app/models/entities.py::NoticeVersion.checklist). POST /publish now
  // refuses without one.
  checklist?: NoticeChecklistOut | null
  content_hash: string | null
  effective_from: string | null
  effective_to: string | null
  is_current: boolean
  published_by: string | null
  published_at: string | null
  created_at: string
  created_by: string
}

export interface Notice {
  id: number
  purpose_id: number
  purpose_code: string
  purpose_name: string
  status: string
  current_version: number
  is_active: boolean
  created_at: string
  versions: NoticeVersion[]
}

// ---------- Grievances (R2-06) ----------
export interface GrievanceEvent {
  id: number
  event: string
  from_status: string
  to_status: string
  note: string
  actor_username: string
  created_at: string
}

export interface Grievance {
  reference_no: string
  status: string
  category: string
  channel: string
  subject: string
  description: string
  customer_id: number
  customer_external_id: string
  purpose_id: number | null
  consent_id: number | null
  source_app: string
  received_at: string
  acknowledged_at: string | null
  response_days: number
  due_at: string
  days_remaining: number
  overdue: boolean
  escalated_at: string | null
  escalated_to: string
  escalation_reason: string
  assigned_to: string
  resolved_at: string | null
  resolved_by: string
  resolution_summary: string
  closed_at: string | null
  feedback_rating: number | null
  feedback_comment: string
  feedback_at: string | null
  created_at: string
  updated_at: string
  events: GrievanceEvent[]
}

export interface GrievanceStats {
  total: number
  open: number
  overdue: number
  escalated: number
  by_status: Record<string, number>
  by_category: Record<string, number>
  resolved_total: number
  resolved_within_period: number
  on_time_closure_rate: number | null
  average_days_to_resolution: number | null
}

export interface Objection {
  id: number
  customer_id: number
  purpose_id: number
  purpose_code: string
  consent_id: number | null
  reason: string
  status: string
  source_app: string
  objected_at: string
  resolved_at: string | null
  resolved_by: string | null
  resolution_note: string
}

// ---------- Breach register (R3-08) ----------
/**
 * One tracked breach clock.
 *
 * `measured_against` is the field this UI must never flatten:
 *   STATUTORY_DEADLINE - a fixed period the law imposes (CERT-In's 6 hours,
 *                        R.7(2)'s 72 hours). Missing it is a legal breach.
 *   INTERNAL_TARGET    - the organisation's own target for an obligation the
 *                        law states as "without delay" with no fixed period.
 *                        Missing it is an internal SLA miss, not a statutory one.
 *   NONE               - neither is set.
 * `deadline_kind` carries the same distinction from the other direction
 * (STATUTORY vs WITHOUT_DELAY_NO_FIXED_PERIOD).
 */
export interface BreachClock {
  clock: string
  obligation: string
  basis: string
  starts_from: string
  started_at: string | null
  deadline_at: string | null
  deadline_kind: 'STATUTORY' | 'WITHOUT_DELAY_NO_FIXED_PERIOD'
  internal_target_at: string | null
  measured_against: 'STATUTORY_DEADLINE' | 'INTERNAL_TARGET' | 'NONE'
  satisfied_at: string | null
  elapsed_hours: number | null
  remaining_hours: number | null
  status: 'NOT_APPLICABLE' | 'NOT_STARTED' | 'MET' | 'MET_LATE' | 'OVERDUE' | 'OPEN'
  /** Present only on a clock the law gives a fixed period (CERT-In 6h,
   *  R.7(2)(b) 72h). This is the durable signal that the obligation is
   *  statutory: unlike `measured_against`, it is set even before the clock
   *  starts, so a not-yet-started CERT-In clock is never mislabelled as having
   *  no fixed period. */
  window_hours?: number
  /** Present only on a "without delay, no fixed period" clock — the hours are
   *  this organisation's own target, never the law's. */
  internal_target_hours?: number
  /** BOARD_DETAILED_72H: where the deadline would sit without a Board-granted
   *  extension, so an extended deadline never erases the original. */
  statutory_deadline_at?: string | null
  extension_granted?: boolean
  filing_status?: string
  not_applicable_reason?: string | null
  scope_note?: string | null
  [key: string]: unknown
}

export interface BreachTimelineEntry {
  at: string | null
  event: string
  detail: string
  [key: string]: unknown
}

export interface Breach {
  id: number
  breach_ref: string
  tenant_id: number | null
  source_app: string
  title: string
  status: string
  severity: string
  occurred_at: string | null
  detected_at: string
  aware_at: string | null
  nature: string
  extent: string
  location: string
  likely_impact: string
  likely_consequences: string
  mitigation_measures: string
  safety_measures: string
  cause: string
  findings_on_actor: string
  remedial_measures: string
  contact_name: string
  contact_email: string
  contact_phone: string
  cert_in_reportable: boolean
  cert_in_not_reportable_reason: string
  affected_count: number
  scope_finalised_at: string | null
  closed_at: string | null
  closure_note: string
  created_by: string
  created_at: string
}

export interface BreachDetail extends Breach {
  clocks: BreachClock[]
  timeline: BreachTimelineEntry[]
  outstanding_obligations: string[]
}

export interface BreachNotification {
  id: number
  breach_id: number
  recipient_type: string
  stage: string
  customer_id: number | null
  notification_id: number | null
  channel: string
  language: string
  subject: string
  clock_start_at: string | null
  deadline_at: string | null
  target_at: string | null
  deadline_basis: string
  content_hash: string
  status: string
  sent_at: string | null
  delivered_at: string | null
  filing_reference: string
  last_error: string
  generated_by: string
  created_at: string
}

export interface BreachReportPreview {
  breach_ref: string
  report_type: string
  provision: string
  payload: Record<string, unknown>
  content: string
  content_hash: string
  missing_mandated_sections: string[]
  deadline_at: string | null
  deadline_basis: string
}

export interface BreachExtension {
  id: number
  breach_id: number
  requested_at: string
  requested_by: string
  requested_until: string
  reason: string
  written_request_ref: string
  status: string
  decided_at: string | null
  decided_by: string
  granted_until: string | null
  board_reference: string
  decision_note: string
}

export interface BreachMetrics {
  generated_at: string
  scope: string
  breaches: number
  [key: string]: unknown
}

// ---------- Retention and erasure (R1-06 / R1-10) ----------
export interface RetentionPolicy {
  id: number
  policy_ref: string
  tenant_id: number | null
  record_class: string
  scope: string
  retention_days: number | null
  inactivity_days: number | null
  pre_erasure_notice_hours: number
  action: 'ERASE' | 'ANONYMISE'
  legal_basis_for_retention: string
  is_active: boolean
  notes: string
  updated_at: string
  updated_by: string
  floor_days: number | null
  hard_delete_permitted: boolean
}

export interface LegalHold {
  id: number
  hold_ref: string
  tenant_id: number | null
  customer_id: number | null
  record_class: string | null
  legal_basis: string
  reason: string
  placed_by: string
  placed_at: string
  expires_at: string | null
  released_at: string | null
  released_by: string | null
  release_reason: string
  is_active: boolean
}

export interface ErasureJob {
  id: number
  job_ref: string
  tenant_id: number | null
  customer_id: number
  purpose_id: number | null
  policy_id: number | null
  trigger: string
  trigger_ref: string
  action: string
  status: string
  authorised_by: string | null
  authorised_at: string | null
  authorisation_basis: string
  notice_required: boolean
  notice_hours: number
  notice_sent_at: string | null
  notification_ids: number[]
  execute_after: string | null
  executed_at: string | null
  executed_by: string | null
  records_erased: Record<string, unknown>
  records_retained: Record<string, unknown>
  processor_alert_ids: number[]
  anonymised_ref: string | null
  evidence_hash: string | null
  blocked_reason: string
  hold_id: number | null
  cancelled_at: string | null
  cancelled_by: string | null
  cancel_reason: string
  error: string | null
  source_app: string
  created_by: string
  reason: string
  created_at: string
}

export interface ErasureMetrics {
  generated_at: string
  erasure_backlog: number
  backlog_by_status: Record<string, number>
  jobs_executed: number
  median_tat_hours: number | null
  executed_with_compliant_notice: number
  /** Null when nothing has been executed: unknown, not 0%. The OpenAPI schema
   *  declares a plain float here; the live API returns null. */
  notice_compliance_pct: number | null
  inactivity_clock_breaches: number
  legal_holds_active: number
}

export interface ErasureScanResult {
  policies: number
  candidates: number
  jobs_proposed: number
  inactivity_clock_breaches: number | null
}

export interface RetentionScheduleRow {
  record_class: string
  label: string
  storage: string
  table: string | null
  floor_days: number
  effective_floor_days: number
  configured_retention_days: number
  is_active: boolean
  schedule_meets_floor: boolean
  deletable: boolean
  undeletable_reason: string
  basis: string
  notes: string
  covers: string[]
}

// ---------- Processors and contracts (R3-07) ----------
export interface Processor {
  id: number
  tenant_id: number | null
  name: string
  type: string
  country: string
  contact_name: string
  contact_email_masked: string | null
  contact_phone_masked: string | null
  escalation_email_masked: string | null
  contract_ref: string
  contract_signed_on: string | null
  contract_valid_from: string | null
  contract_valid_until: string | null
  contract_in_force: boolean
  security_clause_ref: string
  security_measures: string
  erasure_clause_ref: string
  erasure_sla_days: number | null
  webhook_url: string
  webhook_configured: boolean
  webhook_secret_fingerprint: string
  webhook_secret_set_at: string | null
  ack_sla_hours: number
  notes: string
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface ProcessorSecret {
  processor_id: number
  webhook_secret: string
  webhook_secret_fingerprint: string
  webhook_secret_set_at: string
  warning?: string
}

export interface ProcessorAlert {
  id: number
  alert_ref: string
  trigger_ref: string
  tenant_id: number | null
  processor_id: number
  processor_name: string
  customer_id: number | null
  consent_id: number | null
  purpose_id: number | null
  alert_type: string
  status: string
  payload_hash: string
  attempts: number
  max_attempts: number
  http_status: number | null
  last_error: string | null
  ack_sla_hours: number
  due_at: string
  acknowledged_at: string | null
  acknowledged_by: string
  ack_reference: string
  ack_method: string
  escalated_at: string | null
  within_sla: boolean | null
  created_at: string
  sent_at: string | null
  reason: string
}

export interface ProcessorCoverageRow {
  processor_id: number
  name: string
  type: string
  country: string
  contract_ref: string
  contract_valid_from: string | null
  contract_valid_until: string | null
  ack_sla_hours: number
  has_contract_ref: boolean
  contract_in_force: boolean
  has_security_clause: boolean
  has_erasure_clause: boolean
  reachable_for_instructions: boolean
  covered: boolean
  gaps: string[]
}

export interface ProcessorCoverage {
  generated_at: string
  processors_total: number
  processors_covered: number
  coverage_pct: number | null
  processors_with_gaps: number
  contracts_expiring_within_90_days: number[]
  processors: ProcessorCoverageRow[]
}

export interface ProcessorDispatchResult {
  attempted: number
  sent: number
  failed_or_retrying: number
  overdue?: number
  escalated?: number
  dpo_notifications_queued?: number
}

export interface DataSharingEvent {
  id: number
  customer_id: number
  processor_id: number
  processor_name: string
  purpose_id: number
  purpose_code: string
  consent_id: number | null
  data_category_ids: number[]
  data_category_codes: string[]
  event_type: string
  legal_basis: string
  reason: string
  actor_username: string
  source_app: string
  occurred_at: string
  signature_valid: boolean
}

// ---------- Re-consent and the change log (R1-09) ----------
export interface ReConsentCampaign {
  id: number
  campaign_ref: string
  tenant_id: number | null
  entity_type: string
  entity_id: number
  entity_code: string
  from_version: number | null
  to_version: number
  status: string
  reason: string
  consents_flagged: number
  notifications_queued: number
  fresh_consents: number
  started_by: string
  started_at: string
  completed_at: string | null
  cancelled_at: string | null
  cancel_reason: string
}

export interface ReConsentMetrics {
  generated_at: string
  campaigns: number
  open_campaigns: number
  consents_flagged: number
  fresh_consents: number
  re_consent_rate_pct: number | null
  consents_blocked_now: number
  material_changes: number
  changes_logged: number
}

export interface MaterialityRule {
  field: string
  kind: string
  always_material: boolean
  overridable: boolean
  why_material: string
  why_narrowing: string
}

export interface PolicyChange {
  id: number
  change_ref: string
  tenant_id: number | null
  entity_type: string
  entity_id: number
  entity_code: string
  from_version: number | null
  to_version: number
  materiality: 'MATERIAL' | 'NARROWING' | 'COSMETIC'
  /** Field name -> {from, to}. Shape varies by field, so kept opaque. */
  changed_fields: Record<string, unknown>
  materiality_basis: string
  overridden_by: string | null
  override_justification: string
  affected_consents: number
  campaign_id: number | null
  actor_username: string
  created_at: string
}

export interface CookiePolicyVersion {
  id: number
  tenant_id: number
  version_number: number
  categories: Array<Record<string, unknown>>
  summary: string
  content_hash: string | null
  is_current: boolean
  published_by: string
  published_at: string
  change_log_id: number | null
  preferences_invalidated: number
}

// ---------- R2-11 / A-08: the s.5(2) legacy-notice campaign ----------
export interface LegacyCohortMember {
  customer_external_id: string
  source_app: string
  consent_count: number
  purpose_names: string[]
  oldest_consent_at: string | null
  /** The last legacy notice for this principal in ANY state, FAILED included —
   *  "we tried and it bounced" must not hide inside "we never tried". */
  last_notice_status: string | null
  notified_at: string | null
  last_campaign_ref: string | null
  already_notified: boolean
}

export interface LegacyCohort {
  generated_at: string
  cutoff: string
  source_app: string
  cohort_size: number
  outstanding: number
  already_notified: number
  members: LegacyCohortMember[]
  truncated: boolean
}

/** One delivery attempt, as s.6(10) evidence rather than a boolean. */
export interface LegacyNoticeDelivery {
  notification_id: number
  customer_external_id: string | null
  recipient_masked: string
  channel: string
  language: string
  status: string
  subject: string
  retry_count: number
  max_retries: number
  last_error: string | null
  provider_ref: string | null
  queued_at: string | null
  sent_at: string | null
  delivered_at: string | null
  acknowledged_at: string | null
  source_app: string
}

export interface LegacyNoticeCampaign {
  campaign_ref: string
  cutoff: string
  note: string
  source_apps: string[]
  started_at: string | null
  started_by: string
  recipients: number
  notifications: number
  /** Kept apart deliberately: `pending` is queued and never yet attempted, and
   *  is never added to `sent` or `delivered` anywhere in this UI. */
  pending: number
  sent: number
  delivered: number
  failed: number
  acknowledged: number
  principals_reached: number
  principals_acknowledged: number
  by_channel: Record<string, { queued: number; reached: number; failed: number }>
  deliveries: LegacyNoticeDelivery[]
}

export interface LegacyNoticeSendResult {
  campaign_ref: string
  cutoff: string
  source_app: string
  cohort_size: number
  recipients: number
  notifications_queued: number
  unreachable: string[]
  note: string
  started_by: string
}

export interface LegacyNoticeMetrics {
  generated_at: string
  cutoff: string
  source_app: string
  pre_act_consents: number
  pre_act_principals: number
  consents_notified: number
  principals_notified: number
  principals_acknowledged: number
  /** K-26. null (not 0) when there is no pre-Act cohort at all. */
  legacy_notice_delivery_pct: number | null
  campaigns: number
}

export interface NotificationDispatchResult {
  attempted: number
  delivered: number
  failed_or_retrying: number
}

// ---------- Notifications and receipts ----------
export interface NotificationRow {
  id: number
  customer_id: number | null
  event_type: string
  channel: string
  language: string
  subject: string
  /** The message itself. A legacy notice under s.5(2) IS its body, so the
   *  portal renders this rather than only the subject line. */
  body: string
  status: string
  provider_ref: string | null
  retry_count: number
  last_error: string | null
  source_app: string
  sent_at: string | null
  delivered_at: string | null
  acknowledged_at: string | null
  created_at: string
}

export interface NotificationTemplate {
  id: number
  tenant_id: number | null
  event_type: string
  channel: string
  language: string
  subject: string
  body_template: string
  is_active: boolean
  created_at: string
  updated_at: string
  created_by: string
}

export interface NotificationMetrics {
  notifications_total_attempted: number
  notifications_delivered: number
  notifications_failed: number
  notifications_acknowledged: number
  notification_delivery_rate_pct: number | null
  notification_ack_rate_pct: number | null
  by_channel?: Record<string, Record<string, number>>
}

export interface ConsentReceipt {
  id: number
  receipt_ref: string
  consent_id: number
  evidence_id: number | null
  customer_id: number
  source_app: string
  action: string
  consent_version: number
  notice_version_id: number | null
  payload: Record<string, unknown>
  payload_hash: string
  signature: string
  issued_at: string
  /** Re-derived at read time: the stored signature still matches the payload. */
  valid: boolean
}

// ---------- Consent Managers (R3-10) ----------
export interface ConsentManagerRegistration {
  id: number
  cm_ref: string
  name: string
  tenant_id: number
  tenant_code: string
  board_registration_number: string
  registration_status: string
  registered_at: string | null
  website_url: string
  is_active: boolean
  onboarded_fiduciary_count: number
  created_at: string
}

export interface FiduciaryOnboarding {
  id: number
  consent_manager_id: number
  tenant_id: number
  source_app: string
  status: string
  allowed_purpose_codes: string[]
  onboarded_at: string | null
  terminated_at: string | null
  notes: string
}

export interface ConsentManagerMetrics {
  window_hours: number
  total_calls: number
  /** Null when no call was made in the window — unknown, not 100%. */
  availability_pct: number | null
  error_rate_pct: number | null
  latency_ms_p50: number | null
  latency_ms_p95: number | null
  latency_ms_max: number | null
  record_retrieval_ms_p95: number | null
  onboarded_fiduciary_count: number
  registered_consent_manager_count: number
  artefacts_total: number
  artefacts_active: number
  artefacts_withdrawn: number
  per_endpoint: Array<Record<string, unknown>>
}

export interface PropagationSla {
  generated_at: string
  date_from: string | null
  date_to: string | null
  withdrawals_total: number
  withdrawals_with_processor_alerts: number
  withdrawals_without_processors: number
  fully_acknowledged_within_sla: number
  sla_breached: number
  awaiting_acknowledgement_within_sla: number
  k08_propagation_sla_pct: number | null
  alerts_total: number
  alerts_acknowledged: number
  alerts_escalated: number
  mean_hours_to_acknowledge: number | null
  breached_trigger_refs: string[]
}

// ---------- Access review (R3-02/R-04) ----------
export interface AccessReviewUser {
  username: string
  full_name: string
  role: string | null
  permissions: string[]
  is_active: boolean
  last_login_at: string | null
  created_at: string | null
}

export interface AccessReviewRole {
  name: string
  description: string
  permissions: string[]
  [key: string]: unknown
}

export interface AccessReview {
  generated_at: string
  users: AccessReviewUser[]
  roles: AccessReviewRole[]
  [key: string]: unknown
}
