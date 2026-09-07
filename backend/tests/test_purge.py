from app.core.api_keys import SCOPE_CUSTOMER_PURGE, SCOPE_INTEGRATION_WRITE, generate_api_key
from app.models.entities import ApiKey, AuditLog, Consent, ConsentEvidence, ConsentHistory, Customer, DataCategory, Organization, ProcessingActivity, Purpose, PurposeVersion
from app.services import consent as consent_service


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
        consent_text="consent", is_current=True, created_by="test",
    ))
    db.commit()
    return purpose, category, activity


def _make_purge_scoped_key(db, source_app: str) -> str:
    """A real, tenant-bound API key scoped for SCOPE_CUSTOMER_PURGE, for the
    given source_app/tenant. R3 fix ("require a credential and a tenant to
    purge a customer") stopped the legacy, unbound INTEGRATION_API_KEY from
    being implicitly granted this scope (see
    app.api.deps.require_scope / test_api_keys.py's
    test_legacy_key_is_no_longer_exempt_from_scope_checks) - these purge
    mechanics tests need a properly-scoped key instead, exactly like any
    real integration caller would use."""
    org = db.query(Organization).filter(Organization.code == source_app).first()
    if not org:
        org = Organization(name=source_app.title(), code=source_app, is_active=True)
        db.add(org)
        db.commit()
        db.refresh(org)
    plaintext, prefix, key_hash = generate_api_key(org.code)
    db.add(ApiKey(
        tenant_id=org.id, name="test-purge-key", key_prefix=prefix, key_hash=key_hash,
        scopes=[SCOPE_INTEGRATION_WRITE, SCOPE_CUSTOMER_PURGE],
    ))
    db.commit()
    return plaintext


def test_purge_anonymises_customer_and_keeps_audit_and_consent_rows(db, client):
    purpose, category, activity = _make_purpose(db, "purge_test")
    customer = Customer(
        external_id="CUST-PURGE-001", name="Purge Me", email="purge-me@example.com", source_app="PURGE_TEST",
    )
    db.add(customer)
    db.flush()
    consent, _ = consent_service.get_or_create_consent(db, customer, purpose, category, activity, source_app="PURGE_TEST")
    consent_service.grant_consent(db, consent, source_app="PURGE_TEST")
    db.commit()

    consent_count_before = db.query(Consent).filter(Consent.customer_id == customer.id).count()
    history_count_before = db.query(ConsentHistory).filter(ConsentHistory.consent_id == consent.id).count()
    evidence_count_before = db.query(ConsentEvidence).filter(ConsentEvidence.consent_id == consent.id).count()
    audit_count_before = db.query(AuditLog).filter(AuditLog.customer_id == customer.id).count()

    resp = client.delete(
        "/crm/customers/by-email/purge-me@example.com",
        headers={"X-API-Key": _make_purge_scoped_key(db, "PURGE_TEST")},
    )
    assert resp.status_code == 200
    assert resp.json()["anonymised"] is True

    db.expire_all()
    refreshed = db.get(Customer, customer.id)
    assert refreshed.name == "[anonymised]"
    assert refreshed.status == "ANONYMISED"
    assert refreshed.anonymised_ref is not None

    assert db.query(Consent).filter(Consent.customer_id == customer.id).count() == consent_count_before
    assert db.query(ConsentHistory).filter(ConsentHistory.consent_id == consent.id).count() == history_count_before
    assert db.query(ConsentEvidence).filter(ConsentEvidence.consent_id == consent.id).count() == evidence_count_before
    assert db.query(AuditLog).filter(AuditLog.customer_id == customer.id).count() >= audit_count_before


def test_purge_leaves_audit_chain_verifying(db, client):
    """Regression test for the R1-02 requirement that anonymisation must
    never break the hash chain: audit_logs rows are never mutated by purge.

    verify_chain() is asserted per-tenant (this test's own PURGE_CHAIN_TEST
    tenant) rather than globally: the `db` fixture shares one database
    across the whole pytest session with no per-test rollback, and
    test_audit_chain.py's tamper-detection test deliberately leaves a
    permanently broken row behind (in its own TAMPER_TEST tenant) to prove
    detection works - that is expected, unrelated corruption, not something
    this test should be sensitive to.
    """
    from app.core.audit_chain import verify_chain
    from app.services.tenancy import resolve_tenant_id

    purpose, category, activity = _make_purpose(db, "purge_chain_test")
    customer = Customer(
        external_id="CUST-PURGE-CHAIN-001", name="Chain Purge Me",
        email="purge-chain-me@example.com", source_app="PURGE_CHAIN_TEST",
    )
    db.add(customer)
    db.flush()
    consent, _ = consent_service.get_or_create_consent(
        db, customer, purpose, category, activity, source_app="PURGE_CHAIN_TEST"
    )
    consent_service.grant_consent(db, consent, source_app="PURGE_CHAIN_TEST")
    db.commit()

    tenant_id = resolve_tenant_id(db, "PURGE_CHAIN_TEST")

    def _broken_for_this_tenant(result):
        return [b for b in result["broken"] if b["tenant_id"] == tenant_id]

    audit_count_before = db.query(AuditLog).count()
    before = verify_chain(db)
    assert _broken_for_this_tenant(before) == []

    resp = client.delete(
        "/crm/customers/by-email/purge-chain-me@example.com",
        headers={"X-API-Key": _make_purge_scoped_key(db, "PURGE_CHAIN_TEST")},
    )
    assert resp.status_code == 200

    db.expire_all()
    audit_count_after = db.query(AuditLog).count()
    assert audit_count_after == audit_count_before + 1  # the CUSTOMER_PURGED row

    after = verify_chain(db)
    assert _broken_for_this_tenant(after) == []


def test_portal_actions_never_write_customer_name_into_actor_username(db, client):
    """Regression test for the R1-02 fix: portal.py used to pass customer.name
    (PII) as actor_username. It must now be an opaque `principal:<external_id>`
    string, and the real name must never appear anywhere in audit_logs."""
    from app.core.security import create_context_token
    from app.models.entities import ConsentContext
    from datetime import datetime, timedelta, timezone

    purpose, category, activity = _make_purpose(db, "no_pii_actor")
    customer = Customer(
        external_id="CUST-NOPII-001", name="Sensitive Real Name", email="no-pii-actor@example.com",
        source_app="NO_PII_TEST",
    )
    db.add(customer)
    db.flush()
    token = create_context_token(customer.id, "NO_PII_TEST")
    db.add(ConsentContext(
        customer_id=customer.id, token=token, source_app="NO_PII_TEST",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        verified_at=datetime.now(timezone.utc), verification_method="EMAIL_OTP",
    ))
    db.commit()

    resp = client.post(
        "/portal/grant", headers={"X-Context-Token": token}, json={"purpose_code": purpose.code},
    )
    assert resp.status_code == 200

    rows = db.query(AuditLog).filter(AuditLog.customer_id == customer.id).all()
    assert len(rows) > 0
    for row in rows:
        assert "Sensitive Real Name" not in row.actor_username
        assert "Sensitive Real Name" not in (row.reason or "")
    assert any(row.actor_username.startswith("principal:") for row in rows)
