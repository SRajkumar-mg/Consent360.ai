# Consent360 Python SDK

Zero-dependency Python client for the Consent360 integration APIs.

## Install

```bash
pip install consent360
```

Or install from source:

```bash
cd sdk/python
pip install -e .
```

## Quick Start

```python
from consent360 import Consent360

client = Consent360(
    api_key="your-integration-api-key",
    base_url="http://localhost:8000",
)

# 1. Create a customer context
ctx = client.create_customer_context(
    name="Aarav Patel",
    email="aarav@example.com",
    source_app="MY_CRM",
)
print(f"Token: {ctx.context_token}")
print(f"Open:  {ctx.ui_url}")

# 2. Check token status
status = client.get_context_status(ctx.context_token)
print(status.status)  # "VALID" | "CONSUMED" | "EXPIRED"

# 3. Portal operations (customer self-service)
overview = client.get_portal_overview(ctx.context_token)
for purpose in overview.purposes:
    print(f"{purpose.name}: {purpose.status} ({purpose.granted_count}/{purpose.total_count})")

# Grant consent
result = client.grant_consent(ctx.context_token, purpose_code="analytics")
print(result.message)

# Withdraw consent
result = client.withdraw_consent(ctx.context_token, purpose_code="advertising")
print(result.message)

# 4. Purge customer data
purge = client.purge_customer(email="aarav@example.com")
print(f"Deleted: {purge.deleted}, ID: {purge.external_id}")
```

## API Reference

### `Consent360(api_key, base_url, timeout)`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | `str` | required | Integration API key |
| `base_url` | `str` | `"http://localhost:8000"` | Consent360 backend URL |
| `timeout` | `int` | `30` | Request timeout (seconds) |

### Methods

| Method | Auth | Description |
|--------|------|-------------|
| `create_customer_context(name, email, phone, customer_id, source_app)` | X-API-Key | Create/lookup customer, get context token |
| `get_context_status(context_token)` | X-API-Key | Check token validity |
| `purge_customer(email)` | X-API-Key | Delete all consent data for a customer |
| `get_portal_overview(context_token)` | Context Token | Get all purposes + consent status |
| `grant_consent(context_token, purpose_code)` | Context Token | Grant consent for a purpose |
| `withdraw_consent(context_token, purpose_code)` | Context Token | Withdraw consent for a purpose |

### Exceptions

| Exception | Status | When |
|-----------|--------|------|
| `AuthError` | 401 | Invalid or missing API key |
| `NotFoundError` | 404 | Resource not found |
| `RateLimitError` | 429 | Too many requests |
| `ValidationError` | 422 | Invalid request body |
| `Consent360Error` | Any | Other HTTP or connection errors |
