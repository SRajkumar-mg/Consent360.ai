"""R3-05: fiduciary-asserted handoff.

Before this, /portal/overview 403'd for any context that had not completed
email-OTP verification (see app/api/routes/portal.py::_require_verified),
and the job portal's own banner (portal/job-portal/src/useConsent.ts) calls
exactly that with no OTP-verification UI of its own - its whole consent
flow failed, silently. A tenant whose API key carries
SCOPE_FIDUCIARY_ASSERT can now assert that it already authenticated the
principal itself, marking the resulting context pre-verified under a
verification_method distinct from "EMAIL_OTP" (see
app/services/context.py::create_context_for_customer and
app/api/routes/integration.py::_resolve_fiduciary_assertion).
"""
from app.core.api_keys import SCOPE_FIDUCIARY_ASSERT, SCOPE_INTEGRATION_WRITE, generate_api_key
from app.models.entities import ApiKey, ConsentContext, Organization


def _make_org_with_key(db, code, scopes):
    org = Organization(name=code.title(), code=code, is_active=True)
    db.add(org)
    db.commit()
    db.refresh(org)
    plaintext, prefix, key_hash = generate_api_key(code)
    db.add(ApiKey(tenant_id=org.id, name="ci", key_prefix=prefix, key_hash=key_hash, scopes=scopes))
    db.commit()
    return org, plaintext


def test_fiduciary_assertion_defaults_off_for_a_key_without_the_scope(db, client):
    _org, key = _make_org_with_key(db, "FIDUCIARY_OFF", [SCOPE_INTEGRATION_WRITE])
    resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": key},
        json={"name": "No Scope Caller", "email": "no-scope-caller@example.com"},
    )
    assert resp.status_code == 200
    context = db.query(ConsentContext).filter(ConsentContext.source_app == "FIDUCIARY_OFF").first()
    assert context.verified_at is None
    assert context.verification_method is None


def test_fiduciary_assertion_is_automatic_for_a_scoped_tenant_key(db, client):
    """The job portal's own request body never sets fiduciary_asserted at
    all - it must work purely because its tenant's key carries the scope,
    with no frontend change required."""
    _org, key = _make_org_with_key(db, "FIDUCIARY_AUTO", [SCOPE_INTEGRATION_WRITE, SCOPE_FIDUCIARY_ASSERT])
    resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": key},
        json={"name": "Auto Caller", "email": "auto-caller@example.com"},
    )
    assert resp.status_code == 200
    context = db.query(ConsentContext).filter(ConsentContext.source_app == "FIDUCIARY_AUTO").first()
    assert context.verified_at is not None
    assert context.verification_method == "FIDUCIARY_ASSERTED"
    assert context.verification_method != "EMAIL_OTP"


def test_fiduciary_assertion_opt_out_per_request_even_with_the_scope(db, client):
    _org, key = _make_org_with_key(db, "FIDUCIARY_OPT_OUT", [SCOPE_INTEGRATION_WRITE, SCOPE_FIDUCIARY_ASSERT])
    resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": key},
        json={"name": "Opt Out Caller", "email": "opt-out-caller@example.com", "fiduciary_asserted": False},
    )
    assert resp.status_code == 200
    context = db.query(ConsentContext).filter(ConsentContext.source_app == "FIDUCIARY_OPT_OUT").first()
    assert context.verified_at is None


def test_fiduciary_assertion_requested_without_scope_is_rejected_loudly(db, client):
    """A caller that explicitly asks for assertion but is not scoped for it
    must get a loud 403, never a silent fall-back to unverified - the same
    principle as the rest of this rework (no silent security downgrades)."""
    _org, key = _make_org_with_key(db, "FIDUCIARY_DENIED", [SCOPE_INTEGRATION_WRITE])
    resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": key},
        json={"name": "Denied Caller", "email": "denied-caller@example.com", "fiduciary_asserted": True},
    )
    assert resp.status_code == 403


def test_legacy_key_cannot_assert_fiduciary_identity(client):
    resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": "dev-demo-integration-key-2026"},
        json={
            "name": "Legacy Caller", "email": "legacy-fiduciary-caller@example.com",
            "source_app": "LEGACY_FIDUCIARY", "fiduciary_asserted": True,
        },
    )
    assert resp.status_code == 403


def test_portal_overview_works_immediately_after_fiduciary_asserted_handoff(db, client):
    """The concrete bug this closes end to end: /portal/overview 403'd for
    any context lacking OTP verification; a fiduciary-asserted context must
    be treated as already verified."""
    _org, key = _make_org_with_key(db, "FIDUCIARY_PORTAL", [SCOPE_INTEGRATION_WRITE, SCOPE_FIDUCIARY_ASSERT])
    ctx_resp = client.post(
        "/consent/customer-context",
        headers={"X-API-Key": key},
        json={"name": "Portal Caller", "email": "portal-caller@example.com"},
    )
    assert ctx_resp.status_code == 200
    token = ctx_resp.json()["context_token"]

    overview_resp = client.get("/portal/overview", headers={"X-Context-Token": token})
    assert overview_resp.status_code == 200
