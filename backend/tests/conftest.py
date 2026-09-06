"""Shared pytest fixtures.

Runs against the same PostgreSQL database configured in .env (there is no
separate test database wired up in this project) - tests are written to be
idempotent/additive and to clean up rows they create. This mirrors how
seed.py already operates against the dev database.
"""
import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _login(client: TestClient, username: str, password: str) -> dict:
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"login failed for {username}: {resp.status_code} {resp.text}"
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin_headers(client):
    # "admin" was retired in favor of the RBAC MVP role set (see seed.py) -
    # platform_super_admin is the closest equivalent (all permissions, global scope).
    return _login(client, "platform.superadmin.cms", "PlatformSuper@1234")


@pytest.fixture()
def viewer_headers(client):
    return _login(client, "viewer", "Viewer@1234")


@pytest.fixture()
def principal_headers(client):
    return _login(client, "principal.cms", "Principal@1234")


@pytest.fixture()
def guardian_headers(client):
    return _login(client, "guardian.cms", "Guardian@1234")


@pytest.fixture()
def tenant_admin_headers(client):
    return _login(client, "tenant.admin.cms", "TenantAdmin@1234")


@pytest.fixture()
def privacy_officer_headers(client):
    return _login(client, "privacy.officer.cms", "PrivacyOfficer@1234")


@pytest.fixture()
def tenant_support_headers(client):
    return _login(client, "tenant.support.cms", "TenantSupport@1234")


@pytest.fixture()
def platform_super_admin_headers(client):
    return _login(client, "platform.superadmin.cms", "PlatformSuper@1234")


@pytest.fixture()
def platform_compliance_headers(client):
    return _login(client, "platform.compliance.cms", "PlatformCompliance@1234")


@pytest.fixture()
def platform_auditor_headers(client):
    return _login(client, "platform.auditor.cms", "PlatformAuditor@1234")
