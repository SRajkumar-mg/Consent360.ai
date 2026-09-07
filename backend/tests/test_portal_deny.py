"""`POST /portal/deny` - the missing third verb (B-01/B-02, A-09, s.6(10)).

THE DEFECT THESE PIN. `/portal/*` exposed `grant` and `withdraw` and nothing
else. The CareerHub banner's "Reject All" therefore looped the purposes and
called `withdraw` only for the ones already granted, so a FIRST-TIME visitor -
every consent row still NOT_REQUESTED, nothing to withdraw - produced ZERO
server calls. Reproduced live in a browser: pressing "Reject All" fired no
`/portal/*` request, raised no console error, and left all 33 of that
principal's consent rows in NOT_REQUESTED. The refusal existed only in that
one browser's localStorage.

Why that is a compliance defect and not a cosmetic one: the gap register rates
"reject as easy as accept" (B-01/B-02, A-09) as implemented because the control
is present and equal-weight - and it is. But the *decision* was not recorded,
so s.6(10)'s burden of proof could not be discharged for a refusal, and on a
new device the banner asked again as though nothing had ever been decided. A
refusal that leaves no trace is indistinguishable from never having been asked.

`app/services/consent.py::deny_consent` already existed and the reconciled
`CONSENT_TRANSITIONS` already permitted NOT_REQUESTED/REQUESTED/PENDING ->
DENIED. Only the API was missing.

These tests pin, in order: that a first-time refusal lands in the ledger as
DENIED with history, evidence and audit; that a refusal's evidence is no weaker
than a grant's; that the endpoint's tenant scoping is exactly `/portal/grant`'s
and `/portal/withdraw`'s (a foreign tenant's context token is refused by both
independent layers); and that a refusal never overwrites a record the principal
already made herself.
"""
from datetime import datetime, timedelta, timezone

from app.core.api_keys import SCOPE_FIDUCIARY_ASSERT, SCOPE_INTEGRATION_WRITE, generate_api_key
from app.core.security import create_context_token
from app.models.entities import (
    ApiKey,
    AuditLog,
    Consent,
    ConsentContext,
    ConsentHistory,
    Customer,
    DataCategory,
    Organization,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
)


def _make_purpose(db, code):
    category = DataCategory(name=f"cat-{code}", code=f"cat_{code}")
    activity = ProcessingActivity(name=f"act-{code}", code=f"act_{code}")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(name=f"Purpose {code}", code=code, requires_consent=True)
    db.add(purpose)
    db.flush()
    db.add(PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        data_category_ids=[category.id], processing_activity_ids=[activity.id],
        consent_text="I consent.", is_current=True, created_by="test",
    ))
    db.commit()
    return purpose, category, activity


def _verified_context(db, *, external_id, source_app):
    """A principal holding an OTP-verified context token for her own tenant -
    the credential the banner actually presents (see
    `app/api/deps.py`'s scheme 3)."""
    customer = Customer(external_id=external_id, name="Refusing Principal", source_app=source_app)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    token = create_context_token(customer.id, source_app)
    db.add(ConsentContext(
        customer_id=customer.id, token=token, source_app=source_app,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        verified_at=datetime.now(timezone.utc), verification_method="EMAIL_OTP",
    ))
    db.commit()
    return customer, token


def _make_org_with_key(db, code, scopes):
    org = Organization(name=code.title(), code=code, is_active=True)
    db.add(org)
    db.commit()
    db.refresh(org)
    plaintext, prefix, key_hash = generate_api_key(code)
    db.add(ApiKey(tenant_id=org.id, name="ci", key_prefix=prefix, key_hash=key_hash, scopes=scopes))
    db.commit()
    return org, plaintext


def _consents(db, customer, purpose):
    return (
        db.query(Consent)
        .filter(Consent.customer_id == customer.id, Consent.purpose_id == purpose.id)
        .all()
    )


# ---------------------------------------------------------------------------
# The defect itself
# ---------------------------------------------------------------------------
def test_first_time_refusal_is_recorded_as_denied_not_silently_dropped(db, client):
    """THE regression test for the reported defect: a principal who has never
    granted anything refuses, and the refusal must exist in the ledger
    afterwards. Before `/portal/deny` there was no request that could express
    this at all - the banner made none, and neither `/portal/grant` nor
    `/portal/withdraw` would have recorded anything if it had."""
    purpose, _cat, _act = _make_purpose(db, "deny_first_time")
    customer, token = _verified_context(
        db, external_id="CUST-DENY-FIRST-TIME", source_app="DENY_FIRST_TENANT"
    )

    resp = client.post(
        "/portal/deny",
        headers={"X-Context-Token": token},
        json={"purpose_code": purpose.code, "context": {"ui_control_id": f"deny-{purpose.code}"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "denied"
    assert body["affected"] >= 1, "a first-time refusal must affect at least one consent row"

    rows = _consents(db, customer, purpose)
    assert rows, "the consent matrix must be materialised so there is something to refuse"
    assert {r.status for r in rows} == {"DENIED"}
    assert all(r.denied_at is not None for r in rows)
    assert body["affected"] == len(rows)

    # ...and it is recorded as a real lifecycle transition, not a status poke:
    # ConsentHistory + ConsentEvidence + an audit row, exactly like a grant.
    for row in rows:
        history = (
            db.query(ConsentHistory)
            .filter(ConsentHistory.consent_id == row.id, ConsentHistory.action == "CONSENT_DENIED")
            .all()
        )
        assert len(history) == 1
        assert history[0].from_status == "NOT_REQUESTED"
        assert history[0].to_status == "DENIED"
        assert history[0].source_app == "PORTAL"
        assert history[0].actor_username == f"principal:{customer.external_id}"

        db.refresh(row)
        assert len(row.evidence) == 1, "a refusal must be evidenced like every other transition"
        # The evidence is reachable from the history row, so an auditor
        # reading the lifecycle can get to the artefact behind each step.
        assert history[0].details.get("evidence_ref") == row.evidence[0].evidence_ref

        audits = (
            db.query(AuditLog)
            .filter(AuditLog.consent_id == row.id, AuditLog.event == "CONSENT_DENIED")
            .all()
        )
        assert len(audits) == 1
        assert audits[0].actor_type == "PRINCIPAL"
        assert audits[0].actor_id == customer.external_id
        assert audits[0].old_status == "NOT_REQUESTED"
        assert audits[0].new_status == "DENIED"


def test_a_refusals_evidence_is_no_weaker_than_a_grants(db, client):
    """B-01/B-02 parity is about the *record*, not only the button. The same
    ClientContext a grant carries - language, session, banner/screen/control
    id, click depth - must survive a refusal, together with the server's own
    observed IP/user-agent, or "reject as easy as accept" is true of the UI
    and false of the evidence."""
    purpose, _cat, _act = _make_purpose(db, "deny_evidence_parity")
    customer, token = _verified_context(
        db, external_id="CUST-DENY-EVIDENCE", source_app="DENY_EVIDENCE_TENANT"
    )
    context_body = {
        "language": "ta",
        "banner_version": "career-hub-consent-banner-v1",
        "screen_id": "career-hub-consent-banner",
        "ui_control_id": f"deny-{purpose.code}",
        "session_id": "sess-deny-parity",
        "interaction_step": 1,
        "affirmative_action": "CLICK",
    }
    resp = client.post(
        "/portal/deny",
        headers={"X-Context-Token": token, "User-Agent": "DenyParityAgent/1.0"},
        json={"purpose_code": purpose.code, "context": context_body},
    )
    assert resp.status_code == 200

    row = _consents(db, customer, purpose)[0]
    db.refresh(row)
    evidence = row.evidence[0]
    assert evidence.collection_method == "PORTAL"
    assert evidence.collected_by == f"principal:{customer.external_id}"
    assert evidence.language == "ta"
    assert evidence.session_id == "sess-deny-parity"
    assert evidence.banner_version == "career-hub-consent-banner-v1"
    assert evidence.screen_id == "career-hub-consent-banner"
    assert evidence.ui_control_id == f"deny-{purpose.code}"
    assert evidence.details.get("interaction_step") == 1
    assert evidence.user_agent == "DenyParityAgent/1.0"
    assert evidence.content_hash and evidence.signature, "tamper-evidence must be populated"
    assert evidence.consent_version == row.consent_version


def test_deny_records_the_server_observed_gpc_header_not_the_clients_claim(db, client):
    """Same claimed-vs-observed split `/portal/grant` and `/portal/withdraw`
    apply: `Sec-GPC` is read from the request header and stored as fact; the
    body's `gpc_signal` is kept only as an unverified claim."""
    purpose, _cat, _act = _make_purpose(db, "deny_gpc")
    customer, token = _verified_context(db, external_id="CUST-DENY-GPC", source_app="DENY_GPC_TENANT")

    resp = client.post(
        "/portal/deny",
        headers={"X-Context-Token": token, "Sec-GPC": "1"},
        json={"purpose_code": purpose.code, "context": {"gpc_signal": False}},
    )
    assert resp.status_code == 200

    row = _consents(db, customer, purpose)[0]
    db.refresh(row)
    evidence = row.evidence[0]
    assert evidence.gpc_signal is True, "the header the server saw, not the body's claim"
    assert evidence.details.get("claimed_gpc_signal") is False


# ---------------------------------------------------------------------------
# Tenant scoping - identical to /portal/grant and /portal/withdraw
# ---------------------------------------------------------------------------
def test_deny_refuses_a_foreign_tenants_context_token(db, client):
    """Layer 3 of the cross-tenant fix, applied to the new endpoint: a context
    token whose own signed `source_app` claim disagrees with the customer it
    points at must be refused, disclosing nothing. A write endpoint that
    skipped this would let one tenant stamp DENIED on another tenant's
    consent rows."""
    purpose, _cat, _act = _make_purpose(db, "deny_foreign_tenant")
    victim = Customer(
        external_id="CUST-DENY-FOREIGN-VICTIM", name="Deny Victim Real Name",
        source_app="DENY_VICTIM_TENANT",
    )
    db.add(victim)
    db.commit()
    db.refresh(victim)

    mismatched_token = create_context_token(victim.id, "DENY_INTRUDER_TENANT")
    db.add(ConsentContext(
        customer_id=victim.id, token=mismatched_token, source_app="DENY_INTRUDER_TENANT",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        verified_at=datetime.now(timezone.utc), verification_method="EMAIL_OTP",
    ))
    db.commit()

    resp = client.post(
        "/portal/deny",
        headers={"X-Context-Token": mismatched_token},
        json={"purpose_code": purpose.code},
    )
    assert resp.status_code == 404
    assert "Deny Victim Real Name" not in resp.text
    assert victim.external_id not in resp.text
    # Nothing was written against the victim at all.
    assert db.query(Consent).filter(Consent.customer_id == victim.id).count() == 0


def test_deny_refuses_when_only_the_persisted_context_row_disagrees(db, client):
    """Layer 3b in isolation: the token's own claim agrees with the customer,
    but the persisted `ConsentContext.source_app` row does not. The endpoint
    must refuse on that independent check too, exactly as `/portal/overview`
    does - so the new route inherits both guards, not just the JWT one."""
    purpose, _cat, _act = _make_purpose(db, "deny_persisted_mismatch")
    customer = Customer(
        external_id="CUST-DENY-PERSISTED-MISMATCH", name="Another Owner",
        source_app="DENY_REAL_OWNER_TENANT",
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)

    token = create_context_token(customer.id, "DENY_REAL_OWNER_TENANT")
    db.add(ConsentContext(
        customer_id=customer.id, token=token, source_app="DENY_SOME_OTHER_TENANT",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        verified_at=datetime.now(timezone.utc), verification_method="EMAIL_OTP",
    ))
    db.commit()

    resp = client.post(
        "/portal/deny", headers={"X-Context-Token": token}, json={"purpose_code": purpose.code}
    )
    assert resp.status_code == 401
    assert db.query(Consent).filter(Consent.customer_id == customer.id).count() == 0


def test_one_tenants_refusal_never_touches_another_tenants_rows(db, client):
    """Two tenants may hold a customer sharing an email. A refusal recorded
    through one tenant's context must be scoped to that tenant's own
    `source_app`, leaving the other's consent record untouched."""
    purpose, _cat, _act = _make_purpose(db, "deny_tenant_isolation")
    _org_a, key_a = _make_org_with_key(
        db, "DENY_TENANT_A", [SCOPE_INTEGRATION_WRITE, SCOPE_FIDUCIARY_ASSERT]
    )
    _org_b, key_b = _make_org_with_key(
        db, "DENY_TENANT_B", [SCOPE_INTEGRATION_WRITE, SCOPE_FIDUCIARY_ASSERT]
    )
    shared_email = "deny-coincidence@example.com"

    resp_a = client.post(
        "/consent/customer-context", headers={"X-API-Key": key_a},
        json={"name": "Person At A", "email": shared_email},
    )
    resp_b = client.post(
        "/consent/customer-context", headers={"X-API-Key": key_b},
        json={"name": "Person At B", "email": shared_email},
    )
    assert resp_a.status_code == 200 and resp_b.status_code == 200

    # B grants; A refuses. Neither may see or move the other's rows.
    grant = client.post(
        "/portal/grant", headers={"X-Context-Token": resp_b.json()["context_token"]},
        json={"purpose_code": purpose.code},
    )
    assert grant.status_code == 200
    deny = client.post(
        "/portal/deny", headers={"X-Context-Token": resp_a.json()["context_token"]},
        json={"purpose_code": purpose.code},
    )
    assert deny.status_code == 200
    assert deny.json()["affected"] >= 1

    customer_a = db.query(Customer).filter(Customer.source_app == "DENY_TENANT_A").one()
    customer_b = db.query(Customer).filter(Customer.source_app == "DENY_TENANT_B").one()
    assert {r.status for r in _consents(db, customer_a, purpose)} == {"DENIED"}
    assert {r.status for r in _consents(db, customer_b, purpose)} == {"ACTIVE"}


# ---------------------------------------------------------------------------
# A refusal never overwrites a record the principal already made
# ---------------------------------------------------------------------------
def test_deny_leaves_an_active_consent_alone_and_says_so(db, client):
    """DENIED is not a legal transition out of ACTIVE, and forcing one would
    rewrite the state machine to suit a screen: taking back a consent already
    given is a *withdrawal*, which is a different act with different
    downstream effects (cease-processing propagation, erasure triggers). The
    endpoint leaves the row alone - and says so in the message rather than
    letting a bare `affected: 0` read as "nothing needed doing"."""
    purpose, _cat, _act = _make_purpose(db, "deny_after_grant")
    customer, token = _verified_context(
        db, external_id="CUST-DENY-AFTER-GRANT", source_app="DENY_AFTER_GRANT_TENANT"
    )
    assert client.post(
        "/portal/grant", headers={"X-Context-Token": token}, json={"purpose_code": purpose.code}
    ).status_code == 200

    resp = client.post(
        "/portal/deny", headers={"X-Context-Token": token}, json={"purpose_code": purpose.code}
    )
    assert resp.status_code == 200
    assert resp.json()["affected"] == 0
    assert "withdraw" in resp.json()["message"].lower()
    assert {r.status for r in _consents(db, customer, purpose)} == {"ACTIVE"}


def test_deny_does_not_overwrite_a_principals_own_earlier_withdrawal(db, client):
    """A withdrawal is the principal's own act. A later "reject all" sweeping
    over it must not restate it as a denial - that would rewrite what she did
    into something we did, in an append-only ledger."""
    purpose, _cat, _act = _make_purpose(db, "deny_after_withdraw")
    customer, token = _verified_context(
        db, external_id="CUST-DENY-AFTER-WITHDRAW", source_app="DENY_AFTER_WITHDRAW_TENANT"
    )
    headers = {"X-Context-Token": token}
    assert client.post("/portal/grant", headers=headers, json={"purpose_code": purpose.code}).status_code == 200
    assert client.post("/portal/withdraw", headers=headers, json={"purpose_code": purpose.code}).status_code == 200

    rows = _consents(db, customer, purpose)
    history_before = db.query(ConsentHistory).filter(
        ConsentHistory.consent_id.in_([r.id for r in rows])
    ).count()

    resp = client.post("/portal/deny", headers=headers, json={"purpose_code": purpose.code})
    assert resp.status_code == 200
    assert resp.json()["affected"] == 0
    assert {r.status for r in _consents(db, customer, purpose)} == {"WITHDRAWN"}
    history_after = db.query(ConsentHistory).filter(
        ConsentHistory.consent_id.in_([r.id for r in rows])
    ).count()
    assert history_after == history_before, "a no-op refusal must not write history either"


def test_deny_is_idempotent_and_does_not_double_record(db, client):
    """Pressing "Reject All" twice is one decision, not two. DENIED -> DENIED
    is not a legal transition, so the second call must be a clean no-op rather
    than a second denial row."""
    purpose, _cat, _act = _make_purpose(db, "deny_twice")
    customer, token = _verified_context(db, external_id="CUST-DENY-TWICE", source_app="DENY_TWICE_TENANT")
    headers = {"X-Context-Token": token}

    first = client.post("/portal/deny", headers=headers, json={"purpose_code": purpose.code})
    second = client.post("/portal/deny", headers=headers, json={"purpose_code": purpose.code})
    assert first.status_code == second.status_code == 200
    assert first.json()["affected"] >= 1
    assert second.json()["affected"] == 0

    rows = _consents(db, customer, purpose)
    assert {r.status for r in rows} == {"DENIED"}
    for row in rows:
        assert db.query(ConsentHistory).filter(
            ConsentHistory.consent_id == row.id, ConsentHistory.action == "CONSENT_DENIED"
        ).count() == 1
        db.refresh(row)
        assert len(row.evidence) == 1


# ---------------------------------------------------------------------------
# Same guards as the sibling endpoints
# ---------------------------------------------------------------------------
def test_deny_requires_identity_verification(db, client):
    """`_require_verified` gates every consent-changing portal call. A refusal
    is a consent-changing call: an unverified context must not be able to
    record a decision in someone else's name."""
    purpose, _cat, _act = _make_purpose(db, "deny_unverified")
    customer = Customer(
        external_id="CUST-DENY-UNVERIFIED", name="Unverified", source_app="DENY_UNVERIFIED_TENANT"
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    token = create_context_token(customer.id, "DENY_UNVERIFIED_TENANT")
    db.add(ConsentContext(
        customer_id=customer.id, token=token, source_app="DENY_UNVERIFIED_TENANT",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    ))
    db.commit()

    resp = client.post(
        "/portal/deny", headers={"X-Context-Token": token}, json={"purpose_code": purpose.code}
    )
    assert resp.status_code == 403
    assert db.query(Consent).filter(Consent.customer_id == customer.id).count() == 0


def test_deny_unknown_purpose_is_404(db, client):
    _customer, token = _verified_context(
        db, external_id="CUST-DENY-UNKNOWN-PURPOSE", source_app="DENY_UNKNOWN_PURPOSE_TENANT"
    )
    resp = client.post(
        "/portal/deny", headers={"X-Context-Token": token}, json={"purpose_code": "no-such-purpose"}
    )
    assert resp.status_code == 404


def test_deny_without_a_context_token_is_rejected(db, client):
    purpose, _cat, _act = _make_purpose(db, "deny_no_token")
    assert client.post("/portal/deny", json={"purpose_code": purpose.code}).status_code == 422
    assert client.post(
        "/portal/deny", headers={"X-Context-Token": "not-a-token"},
        json={"purpose_code": purpose.code},
    ).status_code == 401
