"""Carried item 6: a production seed profile with no demo users, no fixed
passwords. The pure helpers (is_production_profile / seed_admin_credentials)
are tested directly; the full seed() pipeline is exercised end-to-end
against its own disposable database (not the shared conftest.py test
database - seed() is a ~700-line function that inserts a large, fixed set
of purposes/policies/notices/users, which is too large a blast radius to
run inside the database every other test in this suite shares)."""
import os

import psycopg2
import pytest
from psycopg2 import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import seed as seed_module
from app.core.config import get_settings

settings = get_settings()


def test_is_production_profile_env_var(monkeypatch):
    monkeypatch.setenv("SEED_PROFILE", "production")
    assert seed_module.is_production_profile([]) is True
    monkeypatch.setenv("SEED_PROFILE", "demo")
    assert seed_module.is_production_profile([]) is False
    monkeypatch.delenv("SEED_PROFILE", raising=False)


def test_is_production_profile_cli_flag(monkeypatch):
    monkeypatch.delenv("SEED_PROFILE", raising=False)
    assert seed_module.is_production_profile(["--production"]) is True
    assert seed_module.is_production_profile([]) is False


def test_seed_admin_credentials_generates_when_password_is_still_the_demo_default(monkeypatch):
    monkeypatch.setattr(settings, "SEED_ADMIN_PASSWORD", seed_module._DEMO_ADMIN_PASSWORD_DEFAULT)
    password, generated = seed_module.seed_admin_credentials()
    assert generated is True
    assert password != seed_module._DEMO_ADMIN_PASSWORD_DEFAULT
    assert len(password) >= 20


def test_seed_admin_credentials_uses_operator_supplied_password(monkeypatch):
    monkeypatch.setattr(settings, "SEED_ADMIN_PASSWORD", "MyOwnStrongPassw0rd!")
    password, generated = seed_module.seed_admin_credentials()
    assert generated is False
    assert password == "MyOwnStrongPassw0rd!"


def test_generated_passwords_are_not_repeated():
    passwords = {seed_module._generate_strong_password() for _ in range(20)}
    assert len(passwords) == 20


@pytest.fixture()
def fresh_db(request):
    """A second, disposable Postgres database (alembic-migrated to head),
    independent of the shared conftest.py test database - mirrors that
    fixture's own approach at module scope instead of session scope."""
    base_url = os.environ["DATABASE_URL"]
    url = make_url(base_url)
    db_name = f"consent_platform_seedtest_{abs(hash(request.node.nodeid)) % 100000}"
    admin_url = url.set(database="postgres")
    conn = psycopg2.connect(
        host=admin_url.host, port=admin_url.port, user=admin_url.username,
        password=admin_url.password, dbname="postgres",
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name)))
    cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    cur.close()
    conn.close()

    new_url = str(url.set(database=db_name))
    # Deliberately NOT alembic here (unlike conftest.py's own equivalent
    # fixture): alembic/env.py calls logging.config.fileConfig(), whose
    # default disable_existing_loggers=True permanently disables every
    # ALREADY-REGISTERED logger not named in alembic.ini's own [loggers]
    # section - harmless the ONE time conftest.py's session-scoped fixture
    # runs it before any app module (and so any app.* logger) has been
    # imported, but running it a SECOND time mid-session (after
    # app.integrations.notifications.email's module-level logger already
    # exists) silently kills that logger's output for the rest of the test
    # process, breaking any later test asserting on it via caplog (found by
    # running the full suite - see the lane report). This fixture only
    # needs a schema matching the current ORM models to run seed() against,
    # not a proof that the real migration chain produces it (that is
    # already covered elsewhere), so app.core.database.Base.metadata.create_all
    # sidesteps the whole problem.
    from app.core.database import Base

    engine = create_engine(new_url)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        conn = psycopg2.connect(
            host=admin_url.host, port=admin_url.port, user=admin_url.username,
            password=admin_url.password, dbname="postgres",
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name)))
        cur.close()
        conn.close()


def test_production_profile_creates_exactly_one_admin_and_no_demo_data(fresh_db, monkeypatch):
    from app.core.security import verify_password
    from app.models.entities import Consent, CrmCustomer, Customer, User

    monkeypatch.setattr(settings, "SEED_ADMIN_PASSWORD", seed_module._DEMO_ADMIN_PASSWORD_DEFAULT)

    seed_module.seed(fresh_db, production=True)

    users = fresh_db.query(User).all()
    assert len(users) == 1
    admin = users[0]
    assert admin.username == settings.SEED_ADMIN_USERNAME
    # The generated password is never returned by seed() (only printed) -
    # what matters here is that it is NOT the published demo default.
    assert not verify_password(seed_module._DEMO_ADMIN_PASSWORD_DEFAULT, admin.password_hash)

    assert fresh_db.query(Customer).count() == 0
    assert fresh_db.query(CrmCustomer).count() == 0
    assert fresh_db.query(Consent).count() == 0


def test_demo_profile_still_creates_demo_users_and_customers(fresh_db):
    from app.models.entities import CrmCustomer, Customer, User

    seed_module.seed(fresh_db, production=False)

    assert fresh_db.query(User).count() == 7
    assert fresh_db.query(Customer).count() == 8
    assert fresh_db.query(CrmCustomer).count() == 5
    admin = fresh_db.query(User).filter(User.username == settings.SEED_ADMIN_USERNAME).first()
    assert admin is not None
