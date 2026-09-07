"""A genuine staff/user action must be logged as actor_type="USER" with the
acting user's own id - not the default actor_type="SYSTEM" every log_audit
call site gets when it doesn't pass one explicitly. Before this, every
mutating call site in auth.py, policies.py, purposes.py, data_categories.py,
processing_activities.py and organizations.py logged as SYSTEM even though a
specific, authenticated staff (or org) user made the request, making the
audit ledger unable to distinguish a person from the system for these
events."""
from app.models.entities import AuditLog


def _last_event(db, event, source_app=None):
    q = db.query(AuditLog).filter(AuditLog.event == event)
    if source_app is not None:
        q = q.filter(AuditLog.source_app == source_app)
    return q.order_by(AuditLog.id.desc()).first()


def test_login_is_attributed_to_the_user(client, db):
    from app.core.encryption import hmac_digest
    from app.core.rbac import ALL_PERMISSIONS
    from app.core.security import hash_password
    from app.models.entities import Role, User

    role = db.query(Role).filter(Role.name == "admin").first()
    if not role:
        role = Role(name="admin", description="Full access", permissions=ALL_PERMISSIONS, is_system=True)
        db.add(role)
        db.flush()
    user = User(
        username="actor-type-login-test", full_name="Actor Type Login Test",
        email="actor-type-login-test@example.com",
        email_search=hmac_digest("actor-type-login-test@example.com"),
        password_hash=hash_password("Test@1234"), role_id=role.id, is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    resp = client.post("/auth/login", json={"username": "actor-type-login-test", "password": "Test@1234"})
    assert resp.status_code == 200

    event = _last_event(db, "LOGIN")
    assert event.actor_username == "actor-type-login-test"
    assert event.actor_type == "USER"
    assert event.actor_id == str(user.id)


def test_login_failed_is_attributed_to_a_user_not_the_system(client, db):
    resp = client.post("/auth/login", json={"username": "no-such-user", "password": "wrong"})
    assert resp.status_code == 401
    event = _last_event(db, "LOGIN_FAILED")
    assert event.actor_type == "USER"


def test_data_category_created_is_attributed_to_the_staff_user(client, staff_token, db):
    resp = client.post(
        "/data-categories", headers={"Authorization": f"Bearer {staff_token}"},
        json={"name": "Actor Type Category", "code": "actor_type_category"},
    )
    assert resp.status_code == 201, resp.text
    event = _last_event(db, "DATA_CATEGORY_CREATED")
    assert event.actor_type == "USER"
    assert event.actor_id is not None
    assert event.actor_username == "test-admin"


def test_processing_activity_created_is_attributed_to_the_staff_user(client, staff_token, db):
    resp = client.post(
        "/processing-activities", headers={"Authorization": f"Bearer {staff_token}"},
        json={"name": "Actor Type Activity", "code": "actor_type_activity"},
    )
    assert resp.status_code == 201, resp.text
    event = _last_event(db, "ACTIVITY_CREATED")
    assert event.actor_type == "USER"
    assert event.actor_id is not None


def test_purpose_created_is_attributed_to_the_staff_user(client, staff_token, db):
    resp = client.post(
        "/purposes", headers={"Authorization": f"Bearer {staff_token}"},
        json={"name": "Actor Type Purpose", "code": "actor_type_purpose"},
    )
    assert resp.status_code == 201, resp.text
    event = _last_event(db, "PURPOSE_CREATED")
    assert event.actor_type == "USER"
    assert event.actor_id is not None


def test_policy_created_is_attributed_to_the_staff_user(client, staff_token, db):
    resp = client.post(
        "/policies", headers={"Authorization": f"Bearer {staff_token}"},
        json={"name": "Actor Type Policy", "code": "actor_type_policy"},
    )
    assert resp.status_code == 201, resp.text
    event = _last_event(db, "POLICY_CREATED")
    assert event.actor_type == "USER"
    assert event.actor_id is not None


def test_organization_settings_update_is_attributed_to_the_staff_user(client, staff_token, db):
    from app.models.entities import Organization

    org = Organization(name="Actor Type Org", code="ACTOR_TYPE_ORG", is_active=True)
    db.add(org)
    db.commit()
    db.refresh(org)

    resp = client.put(
        f"/organizations/{org.id}/settings", headers={"Authorization": f"Bearer {staff_token}"},
        json={"dpo_name": "Actor Type DPO"},
    )
    assert resp.status_code == 200, resp.text
    event = _last_event(db, "ORGANIZATION_SETTINGS_UPDATED")
    assert event.actor_type == "USER"
    assert event.actor_id is not None
