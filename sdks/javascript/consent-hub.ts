/**
 * Consent360 JavaScript/TypeScript SDK
 * =====================================
 *
 * Client library for the Consent360 Consent Management Platform integration APIs.
 *
 * Installation:
 *   npm install consent360-sdk
 *   # or copy consent-360.ts directly into your project
 *
 * Usage:
 *   import { Consent360Client } from 'consent360-sdk'
 *
 *   const client = new Consent360Client({
 *     baseUrl: 'http://localhost:8000',
 *     apiKey: 'your-integration-api-key',
 *   })
 *
 *   const result = await client.createCustomerContext({
 *     name: 'John Doe',
 *     email: 'john@example.com',
 *   })
 *   console.log(result.context_token)
 */

export interface Consent360Config {
  baseUrl: string
  apiKey: string
  timeout?: number
}

export interface CustomerContextParams {
  name: string
  email?: string
  phone?: string
  customer_id?: string
  source_app?: string
  callback_url?: string
}

export interface CustomerContextResult {
  context_token: string
  context_id: number
  customer_id: string
  name: string
  expires_in_minutes: number
  source_app: string
  ui_url: string
  request_id?: string
}

export interface PortalOverviewResult {
  customer: {
    id: number
    external_id: string
    name: string
    email: string
    phone: string
    status: string
    source_app: string
    created_at: string
  }
  purposes: Array<{
    code: string
    name: string
    description: string
    legal_basis: string
    requires_consent: boolean
    retention_period_days: number
    consent_text: string
    translations: Record<string, string>
    status: string
    granted_count: number
    total_count: number
  }>
}

export interface PortalActionResult {
  purpose_code: string
  action: string
  affected: number
  message: string
}

export interface ConsentPreferences {
  lang?: string
  categories: Record<string, boolean>
}

export interface CrmCustomerResult {
  id: number
  name: string
  email: string
  age?: number
  aadhar_number?: string
  address?: string
  phone?: string
  created_at: string
}

export interface CrmConsentContextResult {
  customer: CrmCustomerResult
  created: boolean
  context_token: string
  consent_portal_url: string
  expires_in_minutes: number
}

export interface DeleteCustomerResult {
  deleted: boolean
  external_id: string
}

export class Consent360Error extends Error {
  constructor(
    public statusCode: number,
    message: string,
  ) {
    super(`[${statusCode}] ${message}`)
    this.name = 'Consent360Error'
  }
}

export class Consent360Client {
  private baseUrl: string
  private apiKey: string
  private timeout: number

  constructor(config: Consent360Config) {
    this.baseUrl = config.baseUrl.replace(/\/+$/, '')
    this.apiKey = config.apiKey
    this.timeout = config.timeout ?? 30000
  }

  private async request<T>(
    method: string,
    path: string,
    options?: {
      body?: unknown
      headers?: Record<string, string>
    },
  ): Promise<T> {
    const url = `${this.baseUrl}${path}`
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.timeout)

    try {
      const resp = await fetch(url, {
        method,
        headers: {
          'X-API-Key': this.apiKey,
          'Content-Type': 'application/json',
          ...options?.headers,
        },
        body: options?.body ? JSON.stringify(options.body) : undefined,
        signal: controller.signal,
      })

      if (!resp.ok) {
        let detail: string
        try {
          const err = await resp.json()
          detail = err.detail || resp.statusText
        } catch {
          detail = resp.statusText
        }
        throw new Consent360Error(resp.status, detail)
      }

      return (await resp.json()) as T
    } finally {
      clearTimeout(timer)
    }
  }

  // ------------------------------------------------------------------
  // Integration APIs (server-to-server, X-API-Key auth)
  // ------------------------------------------------------------------

  /**
   * Identify or create a customer and mint a short-lived context token.
   *
   * The context token can be used to open the consent portal for the
   * customer to manage their consents.
   */
  async createCustomerContext(
    params: CustomerContextParams,
  ): Promise<CustomerContextResult> {
    return this.request<CustomerContextResult>(
      'POST',
      '/consent/customer-context',
      {
        body: {
          name: params.name,
          source_app: params.source_app ?? 'EXTERNAL_APP',
          ...(params.email && { email: params.email }),
          ...(params.phone && { phone: params.phone }),
          ...(params.customer_id && { customer_id: params.customer_id }),
          ...(params.callback_url && { callback_url: params.callback_url }),
        },
      },
    )
  }

  /**
   * Check the status of a context token.
   *
   * Returns `{ message: "VALID" | "CONSUMED" | "EXPIRED" }`.
   *
   * Note: Requires a staff JWT bearer token with `context.use` permission.
   */
  async getContextStatus(contextToken: string): Promise<{ message: string }> {
    return this.request<{ message: string }>(
      'GET',
      `/consent/context/status/${contextToken}`,
    )
  }

  // ------------------------------------------------------------------
  // Portal APIs (customer self-service, context token auth)
  // ------------------------------------------------------------------

  /**
   * Get the customer's consent overview (all purposes + statuses).
   */
  async getPortalOverview(
    contextToken: string,
  ): Promise<PortalOverviewResult> {
    return this.request<PortalOverviewResult>(
      'GET',
      '/portal/overview',
      { headers: { 'X-Context-Token': contextToken } },
    )
  }

  /**
   * Grant all consents for a specific purpose.
   */
  async grantConsent(
    contextToken: string,
    purposeCode: string,
  ): Promise<PortalActionResult> {
    return this.request<PortalActionResult>(
      'POST',
      '/portal/grant',
      {
        body: { purpose_code: purposeCode },
        headers: { 'X-Context-Token': contextToken },
      },
    )
  }

  /**
   * Withdraw all consents for a specific purpose.
   */
  async withdrawConsent(
    contextToken: string,
    purposeCode: string,
  ): Promise<PortalActionResult> {
    return this.request<PortalActionResult>(
      'POST',
      '/portal/withdraw',
      {
        body: { purpose_code: purposeCode },
        headers: { 'X-Context-Token': contextToken },
      },
    )
  }

  // ------------------------------------------------------------------
  // CRM Consent APIs (cookie preferences)
  // ------------------------------------------------------------------

  /**
   * Get saved cookie-category preferences for a CRM customer.
   */
  async getConsentPreferences(
    customerId: number,
  ): Promise<{ preferences: Record<string, unknown> }> {
    return this.request<{ preferences: Record<string, unknown> }>(
      'GET',
      `/crm/customers/${customerId}/consent-preferences`,
    )
  }

  /**
   * Save cookie-category preferences for a CRM customer.
   *
   * Mirrors the choices onto consent platform consent records.
   */
  async saveConsentPreferences(
    customerId: number,
    prefs: ConsentPreferences,
  ): Promise<{ saved: boolean; preferences: Record<string, unknown> }> {
    return this.request<{ saved: boolean; preferences: Record<string, unknown> }>(
      'PUT',
      `/crm/customers/${customerId}/consent-preferences`,
      { body: prefs },
    )
  }

  /**
   * Mint a consent context token for an existing CRM customer.
   */
  async createCrmConsentContext(
    customerId: number,
  ): Promise<CrmConsentContextResult> {
    return this.request<CrmConsentContextResult>(
      'POST',
      `/crm/customers/${customerId}/consent-context`,
    )
  }

  // ------------------------------------------------------------------
  // Customer Purge API (server-to-server, X-API-Key auth)
  // ------------------------------------------------------------------

  /**
   * Purge a customer's entire consent profile by email.
   *
   * Deletes all consent records, history, evidence, contexts, and
   * audit entries for the customer.
   */
  async deleteCustomerByEmail(email: string): Promise<DeleteCustomerResult> {
    return this.request<DeleteCustomerResult>(
      'DELETE',
      `/crm/customers/by-email/${encodeURIComponent(email)}`,
    )
  }
}
