"""Shared pytest fixtures: a disposable Postgres database, a DB session bound
to it, a FastAPI TestClient wired to the same session, and a minimal staff
token. DATABASE_URL is overridden via environment variable *before* any
`app.*` module is imported, so app.core.database binds its engine to the test
database rather than backend/.env's dev database."""
import base64
import os
import re

from sqlalchemy.engine import make_url

_DEFAULT_DEV_URL = "postgresql+psycopg2://postgres:postgres@127.0.0.1:5433/consent_platform"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or re.sub(
    r"/[^/]+$", "/consent_platform_test", os.environ.get("DATABASE_URL", _DEFAULT_DEV_URL)
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("ALLOW_LEGACY_INTEGRATION_KEY", "true")
os.environ.setdefault("PORTAL_REQUIRE_VERIFICATION", "true")
os.environ.setdefault("FIELD_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())

import psycopg2  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from psycopg2 import sql  # noqa: E402


def _admin_connect(dsn_url):
    admin_url = dsn_url.set(database="postgres")
    conn = psycopg2.connect(
        host=admin_url.host, port=admin_url.port, user=admin_url.username,
        password=admin_url.password, dbname="postgres",
    )
    conn.autocommit = True
    return conn


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    url = make_url(TEST_DATABASE_URL)
    conn = _admin_connect(url)
    cur = conn.cursor()
    cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(url.database)))
    cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(url.database)))
    cur.close()
    conn.close()

    from alembic import command
    from alembic.config import Config
    from pathlib import Path

    backend_dir = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(cfg, "head")

    yield

    from app.core.database import engine
    engine.dispose()
    conn = _admin_connect(url)
    cur = conn.cursor()
    cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(url.database)))
    cur.close()
    conn.close()


@pytest.fixture()
def db():
    from app.core.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db):
    from app.core.database import get_db
    from app.main import app

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def staff_token(db):
    from app.core.encryption import hmac_digest
    from app.core.rbac import ALL_PERMISSIONS
    from app.core.security import create_access_token, hash_password
    from app.models.entities import Role, User

    role = db.query(Role).filter(Role.name == "admin").first()
    if not role:
        role = Role(name="admin", description="Full access", permissions=ALL_PERMISSIONS, is_system=True)
        db.add(role)
        db.flush()
    user = db.query(User).filter(User.username == "test-admin").first()
    if not user:
        user = User(
            username="test-admin",
            full_name="Test Admin",
            email="test-admin@example.com",
            email_search=hmac_digest("test-admin@example.com"),
            password_hash=hash_password("Test@1234"),
            role_id=role.id,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return create_access_token(user.id, user.username, role.name)


@pytest.fixture(autouse=True)
def _reset_process_global_rate_limiters():
    """Rate limiters are module-level and in-memory, so their state survives
    from one test to the next inside a single pytest process.

    Written while diagnosing two verification failures that appeared only in a
    full run. That diagnosis was wrong - another agent bisected the real cause
    to a fixture creating a Purpose with no current version, which made
    /portal/overview raise. This fixture is kept anyway because the leak it
    prevents is real (otp_send_limiter allows three sends per address per
    fifteen minutes, process-wide), but it has never been observed to fix a
    failing test, and it should not be cited as the fix for one.
    """
    from app.core import utils as _utils

    limiters = [v for v in vars(_utils).values() if isinstance(v, _utils.RateLimiter)]
    for mod in ("app.services.otp", "app.api.routes.auth", "app.services.context"):
        try:
            m = __import__(mod, fromlist=["*"])
        except Exception:
            continue
        limiters += [v for v in vars(m).values() if isinstance(v, _utils.RateLimiter)]
    for lim in limiters:
        lim._hits.clear()
    yield
    for lim in limiters:
        lim._hits.clear()
