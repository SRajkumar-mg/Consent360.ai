import requests, json

BASE = "http://127.0.0.1:8000"
# Login
r = requests.post(f"{BASE}/auth/login", json={"username": "admin", "password": "Admin@1234"})
token = r.json()["access_token"]
H = {"Authorization": f"Bearer {token}"}

print("=== 1. Create Terms & Conditions ===")
r = requests.post(f"{BASE}/legal-documents", headers=H, json={
    "tenant_id": 1, "document_type": "TERMS_AND_CONDITIONS", "version": "1.0",
    "title": "Consent360 Terms & Conditions", "content": "These are the Terms and Conditions...",
    "language": "en", "status": "PUBLISHED",
})
print(f"  Status: {r.status_code}, ID: {r.json().get('id')}, Hash: {r.json().get('content_hash', '')[:16]}")
terms_id = r.json().get("id")

print("\n=== 2. Create Privacy Policy ===")
r = requests.post(f"{BASE}/legal-documents", headers=H, json={
    "tenant_id": 1, "document_type": "PRIVACY_POLICY", "version": "1.0",
    "title": "Consent360 Privacy Policy", "content": "This Privacy Policy describes...",
    "language": "en", "status": "PUBLISHED",
})
print(f"  Status: {r.status_code}, ID: {r.json().get('id')}, Hash: {r.json().get('content_hash', '')[:16]}")
pp_id = r.json().get("id")

print("\n=== 3. List Legal Documents ===")
r = requests.get(f"{BASE}/legal-documents", headers=H)
print(f"  Status: {r.status_code}, Count: {len(r.json())}")

print("\n=== 4. Create Notice with all fields ===")
r = requests.post(f"{BASE}/notices", headers=H, json={
    "tenant_id": 1, "purpose_id": 1, "version_number": 1, "language": "en",
    "title": "Marketing Analytics Consent Notice",
    "body": "We collect your data for analytics purposes...",
    "consent_text": "I consent to the collection and processing of my data for marketing analytics.",
    "data_items": [{"category_id": 1, "necessity": True, "description": "email address"}],
    "services_enabled": "Email marketing, analytics dashboard",
    "retention_text": "Data retained for 2 years from last activity",
    "terms_document_id": terms_id, "privacy_document_id": pp_id,
    "status": "PUBLISHED",
})
print(f"  Status: {r.status_code}, ID: {r.json().get('id')}, Hash: {r.json().get('content_hash', '')[:16]}")
notice_id = r.json().get("id")

print("\n=== 5. Render Notice ===")
r = requests.get(f"{BASE}/notices/render/{notice_id}", headers=H)
d = r.json()
print(f"  Status: {r.status_code}")
print(f"  Title: {d.get('title')}")
print(f"  Terms: {d.get('legal_documents', {}).get('terms_and_conditions', {}).get('version')}")
print(f"  Privacy: {d.get('legal_documents', {}).get('privacy_policy', {}).get('version')}")
print(f"  Languages: {len(d.get('supported_languages', []))} supported")
print(f"  DPO: {d.get('tenant_contact', {}).get('dpo_name')}")

print("\n=== 6. Grant Consent with notice_id ===")
r = requests.post(f"{BASE}/consents/summary/CUST-abc123", headers=H)
if r.status_code == 200:
    consents = r.json().get("consents", [])
    if consents:
        cid = consents[0]["id"]
        r2 = requests.post(f"{BASE}/consents/{cid}/grant", headers=H, json={
            "collection_method": "UI", "consent_text": "I consent",
            "affirmative_action": "CLICK",
            "client_context": {"language": "en", "screen_id": "consent_portal", "ui_control_id": "consent_accept_marketing"},
            "notice_id": notice_id,
        })
        print(f"  Grant status: {r2.status_code}")
        if r2.status_code == 200:
            print(f"  Consent ID: {r2.json().get('id')}")
    else:
        print("  No consents to grant")
else:
    print(f"  Summary status: {r.status_code}")

print("\n=== 7. Verify evidence has legal doc references ===")
r = requests.get(f"{BASE}/consents/summary/CUST-abc123", headers=H)
if r.status_code == 200:
    consents = r.json().get("consents", [])
    for c in consents[:1]:
        r2 = requests.get(f"{BASE}/consents/{c['id']}", headers=H)
        if r2.status_code == 200:
            evidence_list = r2.json().get("evidence", [])
            for ev in evidence_list:
                print(f"  Evidence: {ev.get('evidence_ref')}")
                print(f"    notice_id: {ev.get('notice_id')}")
                print(f"    terms_hash: {ev.get('terms_hash', '')[:16]}")
                print(f"    privacy_hash: {ev.get('privacy_hash', '')[:16]}")
                print(f"    signature: {'present' if ev.get('signature') else 'missing'}")

print("\nDone!")
