import requests
import json

# Test login
resp = requests.post("http://localhost:8000/auth/login", json={"username": "admin", "password": "Admin@1234"})
print(f"Login status: {resp.status_code}")
print(f"Login body: {resp.text[:500]}")
