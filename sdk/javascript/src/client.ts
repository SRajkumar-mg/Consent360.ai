/** Consent360 SDK — JavaScript/TypeScript client. */

import type {
  Consent360Config,
  CustomerContext,
  CustomerContextParams,
  ContextStatus,
  PortalOverview,
  PortalPurpose,
  PortalAction,
  PurgeResult,
} from "./types";

export class Consent360Error extends Error {
  statusCode?: number;
  detail?: string;
  constructor(message: string, statusCode?: number, detail?: string) {
    super(message);
    this.name = "Consent360Error";
    this.statusCode = statusCode;
    this.detail = detail;
  }
}

export class AuthError extends Consent360Error {
  constructor(message: string, detail?: string) {
    super(message, 401, detail);
    this.name = "AuthError";
  }
}

export class NotFoundError extends Consent360Error {
  constructor(message: string, detail?: string) {
    super(message, 404, detail);
    this.name = "NotFoundError";
  }
}

export class RateLimitError extends Consent360Error {
  constructor(message: string, detail?: string) {
    super(message, 429, detail);
    this.name = "RateLimitError";
  }
}

/**
 * Consent360 integration client.
 *
 * @example
 * ```ts
 * import { Consent360 } from '@consent360/sdk';
 *
 * const client = new Consent360({ apiKey: 'your-key', baseUrl: 'http://localhost:8000' });
 * const ctx = await client.createCustomerContext({ name: 'John', email: 'john@example.com' });
 * console.log(ctx.ui_url);
 * ```
 */
export class Consent360 {
  private apiKey: string;
  private baseUrl: string;
  private timeout: number;

  constructor(config: Consent360Config) {
    this.apiKey = config.apiKey;
    this.baseUrl = (config.baseUrl || "http://localhost:8000").replace(/\/$/, "");
    this.timeout = config.timeout || 30000;
  }

  // ------------------------------------------------------------------
  // Integration APIs (X-API-Key protected)
  // ------------------------------------------------------------------

  /**
   * Create or look up a customer and mint a short-lived context token.
   */
  async createCustomerContext(params: CustomerContextParams): Promise<CustomerContext> {
    return this.request("POST", "/consent/customer-context", { jsonBody: params });
  }

  /**
   * Check whether a context token is valid, consumed, or expired.
   */
  async getContextStatus(contextToken: string): Promise<ContextStatus> {
    return this.request("GET", `/consent/context/status/${contextToken}`);
  }

  /**
   * Delete all consent-platform records for a customer by email.
   */
  async purgeCustomer(email: string): Promise<PurgeResult> {
    const encoded = encodeURIComponent(email);
    return this.request("DELETE", `/crm/customers/by-email/${encoded}`);
  }

  // ------------------------------------------------------------------
  // Portal APIs (X-Context-Token protected)
  // ------------------------------------------------------------------

  /**
   * Get all purposes and consent status for a customer.
   */
  async getPortalOverview(contextToken: string): Promise<PortalOverview> {
    return this.request("GET", "/portal/overview", {
      headers: { "X-Context-Token": contextToken },
    });
  }

  /**
   * Grant consent for a specific purpose.
   */
  async grantConsent(
    contextToken: string,
    purposeCode: string,
  ): Promise<PortalAction> {
    return this.request("POST", "/portal/grant", {
      jsonBody: { purpose_code: purposeCode },
      headers: { "X-Context-Token": contextToken },
    });
  }

  /**
   * Withdraw consent for a specific purpose.
   */
  async withdrawConsent(
    contextToken: string,
    purposeCode: string,
  ): Promise<PortalAction> {
    return this.request("POST", "/portal/withdraw", {
      jsonBody: { purpose_code: purposeCode },
      headers: { "X-Context-Token": contextToken },
    });
  }

  // ------------------------------------------------------------------
  // Internal HTTP helpers
  // ------------------------------------------------------------------

  private async request<T>(
    method: string,
    path: string,
    opts: {
      jsonBody?: unknown;
      headers?: Record<string, string>;
    } = {},
  ): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const headers: Record<string, string> = {
      "X-API-Key": this.apiKey,
      Accept: "application/json",
      ...opts.headers,
    };

    const fetchOpts: RequestInit = {
      method,
      headers,
      signal: AbortSignal.timeout(this.timeout),
    };

    if (opts.jsonBody !== undefined) {
      headers["Content-Type"] = "application/json";
      fetchOpts.body = JSON.stringify(opts.jsonBody);
    }

    let res: Response;
    try {
      res = await fetch(url, fetchOpts);
    } catch (err) {
      throw new Consent360Error(`Connection error: ${(err as Error).message}`);
    }

    const body = await res.json().catch(() => ({}));

    if (!res.ok) {
      const detail = (body as { detail?: string }).detail || JSON.stringify(body);
      if (res.status === 401) throw new AuthError(`Authentication failed: ${detail}`, detail);
      if (res.status === 404) throw new NotFoundError(`Not found: ${detail}`, detail);
      if (res.status === 429) throw new RateLimitError(`Rate limit exceeded: ${detail}`, detail);
      throw new Consent360Error(`HTTP ${res.status}: ${detail}`, res.status, detail);
    }

    return body as T;
  }
}

export type {
  Consent360Config,
  CustomerContext,
  CustomerContextParams,
  ContextStatus,
  PortalOverview,
  PortalPurpose,
  PortalAction,
  PurgeResult,
};
