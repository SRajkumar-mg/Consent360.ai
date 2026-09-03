from consent_hub import Consent360Client

client = Consent360Client(
    base_url="http://localhost:8000",
    api_key="your-integration-api-key",
)

# Create a context token for a customer
result = client.create_customer_context(
    name="John Doe",
    email="john@example.com",
    phone="+91-9000000001",
    source_app="MY_APP",
)
print(f"Context token: {result['context_token']}")
print(f"Portal URL: {result['ui_url']}")

# Check context status
status = client.get_context_status(result["context_token"])
print(f"Status: {status['message']}")

# Purge a customer's consent profile
client.delete_customer_by_email("john@example.com")
