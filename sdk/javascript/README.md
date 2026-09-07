# Consent360 JavaScript/TypeScript SDK

Zero-dependency client for the Consent360 integration APIs. Works in Node.js 18+ and browsers.

## Install

```bash
npm install @consent360/sdk
```

## Quick Start

```typescript
import { Consent360 } from '@consent360/sdk';

const client = new Consent360({
  apiKey: 'your-integration-api-key',
  baseUrl: 'http://localhost:8000',
});

// 1. Create a customer context
const ctx = await client.createCustomerContext({
  name: 'Aarav Patel',
  email: 'aarav@example.com',
  source_app: 'MY_CRM',
});
console.log(ctx.ui_url);

// 2. Check token status
const status = await client.getContextStatus(ctx.context_token);
console.log(status.status); // "VALID" | "CONSUMED" | "EXPIRED"

// 3. Portal operations (customer self-service)
const overview = await client.getPortalOverview(ctx.context_token);
overview.purposes.forEach(p => {
  console.log(`${p.name}: ${p.status} (${p.granted_count}/${p.total_count})`);
});

await client.grantConsent(ctx.context_token, 'analytics');
await client.withdrawConsent(ctx.context_token, 'advertising');

// 4. Purge customer data
const result = await client.purgeCustomer('aarav@example.com');
console.log(result.deleted);
```

## API Reference

### `new Consent360(config)`

| Config | Type | Default | Description |
|--------|------|---------|-------------|
| `apiKey` | `string` | required | Integration API key |
| `baseUrl` | `string` | `"http://localhost:8000"` | Consent360 backend URL |
| `timeout` | `number` | `30000` | Request timeout (ms) |

### Methods

| Method | Auth | Description |
|--------|------|-------------|
| `createCustomerContext(params)` | X-API-Key | Create/lookup customer, get context token |
| `getContextStatus(token)` | X-API-Key | Check token validity |
| `purgeCustomer(email)` | X-API-Key | Delete all consent data for a customer |
| `getPortalOverview(token)` | Context Token | Get all purposes + consent status |
| `grantConsent(token, purposeCode)` | Context Token | Grant consent for a purpose |
| `withdrawConsent(token, purposeCode)` | Context Token | Withdraw consent for a purpose |

### Error Classes

| Class | Status | When |
|-------|--------|------|
| `AuthError` | 401 | Invalid or missing API key |
| `NotFoundError` | 404 | Resource not found |
| `RateLimitError` | 429 | Too many requests |
| `Consent360Error` | Any | Other HTTP or connection errors |
