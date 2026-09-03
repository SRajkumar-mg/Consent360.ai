/** Consent360 SDK — TypeScript types. */

export interface CustomerContextParams {
  name: string;
  email?: string;
  phone?: string;
  customer_id?: string;
  source_app?: string;
}

export interface CustomerContext {
  context_token: string;
  context_id: number;
  customer_id: string;
  name: string;
  expires_in_minutes: number;
  source_app: string;
  ui_url: string;
  request_id: string | null;
}

export interface ContextStatus {
  status: "VALID" | "CONSUMED" | "EXPIRED";
}

export interface PortalPurpose {
  code: string;
  name: string;
  description: string;
  legal_basis: string;
  requires_consent: boolean;
  retention_period_days: number;
  consent_text: string;
  translations: Record<string, string>;
  status: "GRANTED" | "PARTIAL" | "NOT_GRANTED";
  granted_count: number;
  total_count: number;
}

export interface PortalOverview {
  customer: {
    id: number;
    external_id: string;
    name: string;
    email: string;
    phone: string;
    status: string;
    source_app: string;
    created_at: string;
  };
  source_app: string;
  purposes: PortalPurpose[];
}

export interface PortalAction {
  purpose_code: string;
  action: string;
  affected: number;
  message: string;
}

export interface PurgeResult {
  deleted: boolean;
  external_id: string;
}

export interface Consent360Config {
  apiKey: string;
  baseUrl?: string;
  timeout?: number;
}
