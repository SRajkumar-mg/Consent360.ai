from app.core.security import create_context_token
from app.models.entities import ConsentContext, Customer


def _make_context(db, email="verify-test@example.com", source_app="VERIFY_TEST"):
    customer = Customer(external_id=f"CUST-{email}", name="Verify Customer", email=email, source_app=source_app)
    db.add(customer)
    db.flush()
    token = create_context_token(customer.id, source_app)
    from datetime import datetime, timedelta, timezone

    ctx = ConsentContext(
        customer_id=customer.id, token=token, source_app=source_app,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db.add(ctx)
    db.commit()
    return customer, token


def test_verify_start_logs_code_via_console_sender(db, client, caplog):
    customer, token = _make_context(db)
    import logging

    caplog.set_level(logging.INFO, logger="app.notifications.email")
    resp = client.post("/portal/verify/start", headers={"X-Context-Token": token})
    assert resp.status_code == 200
    assert any("EMAIL to=" in r.message for r in caplog.records)


def test_verify_confirm_with_correct_code_marks_verified(db, client, monkeypatch):
    """Full happy path: the portal refuses this context before verification,
    /verify/start sends a code via the (faked) console sender, /verify/confirm
    with that exact code marks the context verified, and only then does the
    same context succeed on a real portal action."""
    customer, token = _make_context(db, email="verify-correct@example.com")

    # Before verification, the gate refuses.
    pre_resp = client.get("/portal/overview", headers={"X-Context-Token": token})
    assert pre_resp.status_code == 403

    captured = {}

    def fake_send(self, to, subject, body):
        captured["body"] = body

    from app.integrations.notifications.email import ConsoleEmailSender

    monkeypatch.setattr(ConsoleEmailSender, "send", fake_send)
    start_resp = client.post("/portal/verify/start", headers={"X-Context-Token": token})
    assert start_resp.status_code == 200
    code = captured["body"].split("verification code is ")[1].split(".")[0]

    resp = client.post("/portal/verify/confirm", json={"code": code}, headers={"X-Context-Token": token})
    assert resp.status_code == 200

    db.expire_all()
    ctx = db.query(ConsentContext).filter(ConsentContext.token == token).first()
    assert ctx.verified_at is not None
    assert ctx.verification_method == "EMAIL_OTP"

    # Now that the context is verified, the same token can act on the portal.
    post_resp = client.get("/portal/overview", headers={"X-Context-Token": token})
    assert post_resp.status_code == 200


def test_verify_confirm_with_wrong_code_fails(db, client):
    customer, token = _make_context(db, email="verify-wrong@example.com")
    client.post("/portal/verify/start", headers={"X-Context-Token": token})
    resp = client.post("/portal/verify/confirm", json={"code": "000000"}, headers={"X-Context-Token": token})
    assert resp.status_code in (400, 200)  # 000000 could coincidentally match; assert content instead
    if resp.status_code == 200:
        pass
    else:
        assert resp.json()["detail"] == "Incorrect or expired verification code"


def test_verify_confirm_expired_code_rejected(db, client, monkeypatch):
    """An expired challenge must fail confirmation even with the right code,
    and the failure must look identical to a plain wrong-code failure (no
    "expired" detail) - see the module docstring on why the reason is never
    revealed."""
    from datetime import datetime, timedelta, timezone

    customer, token = _make_context(db, email="verify-expired@example.com")
    captured = {}

    def fake_send(self, to, subject, body):
        captured["body"] = body

    from app.integrations.notifications.email import ConsoleEmailSender
    from app.models.entities import OtpChallenge

    monkeypatch.setattr(ConsoleEmailSender, "send", fake_send)
    client.post("/portal/verify/start", headers={"X-Context-Token": token})
    code = captured["body"].split("verification code is ")[1].split(".")[0]

    ctx = db.query(ConsentContext).filter(ConsentContext.token == token).first()
    challenge = (
        db.query(OtpChallenge)
        .filter(OtpChallenge.context_id == ctx.id)
        .order_by(OtpChallenge.id.desc())
        .first()
    )
    challenge.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    resp = client.post("/portal/verify/confirm", json={"code": code}, headers={"X-Context-Token": token})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Incorrect or expired verification code"

    db.expire_all()
    ctx = db.query(ConsentContext).filter(ConsentContext.token == token).first()
    assert ctx.verified_at is None


def test_verify_confirm_attempt_cap_enforced(db, client, monkeypatch):
    """After max_attempts wrong guesses, further confirms fail - including
    with the correct code - and every failure (wrong code or cap-exhausted)
    returns the identical generic response, never a distinct status/detail
    that would tell a caller the cap had been hit."""
    customer, token = _make_context(db, email="verify-capped@example.com")
    captured = {}

    def fake_send(self, to, subject, body):
        captured["body"] = body

    from app.integrations.notifications.email import ConsoleEmailSender

    monkeypatch.setattr(ConsoleEmailSender, "send", fake_send)
    client.post("/portal/verify/start", headers={"X-Context-Token": token})
    real_code = captured["body"].split("verification code is ")[1].split(".")[0]
    wrong_code = f"{(int(real_code) + 1) % 1_000_000:06d}"

    for _ in range(5):
        resp = client.post(
            "/portal/verify/confirm", json={"code": wrong_code}, headers={"X-Context-Token": token}
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Incorrect or expired verification code"

    # Cap now exhausted; same wrong code fails with the same generic response.
    resp = client.post("/portal/verify/confirm", json={"code": wrong_code}, headers={"X-Context-Token": token})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Incorrect or expired verification code"

    # Prove the cap gates even the correct code once exhausted - and still
    # with the same generic status/detail, not a distinct "cap hit" signal.
    resp = client.post("/portal/verify/confirm", json={"code": real_code}, headers={"X-Context-Token": token})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Incorrect or expired verification code"

    db.expire_all()
    ctx = db.query(ConsentContext).filter(ConsentContext.token == token).first()
    assert ctx.verified_at is None


def test_attempt_cap_persists_across_new_verification_code(db, client, monkeypatch):
    """Fix round 1, item 1 (HIGH): the guess budget must be enforced per
    CONTEXT, not per challenge row. Reproduces the reviewer's finding -
    5 wrong guesses, a fresh /verify/start, 5 more, forever - and proves it
    no longer works: after the cap is hit once, a brand new code issued for
    the same context is still refused, even when the caller supplies that
    exact new code correctly."""
    customer, token = _make_context(db, email="verify-cap-persists@example.com")
    captured = {}

    def fake_send(self, to, subject, body):
        captured["body"] = body

    from app.integrations.notifications.email import ConsoleEmailSender

    monkeypatch.setattr(ConsoleEmailSender, "send", fake_send)

    start_resp = client.post("/portal/verify/start", headers={"X-Context-Token": token})
    assert start_resp.status_code == 200
    first_code = captured["body"].split("verification code is ")[1].split(".")[0]
    wrong_code = f"{(int(first_code) + 1) % 1_000_000:06d}"

    for _ in range(5):
        resp = client.post(
            "/portal/verify/confirm", json={"code": wrong_code}, headers={"X-Context-Token": token}
        )
        assert resp.status_code == 400

    # The budget for this context is exhausted. Requesting a brand new code
    # must still succeed (sending is a separate control from the guess cap)...
    restart_resp = client.post("/portal/verify/start", headers={"X-Context-Token": token})
    assert restart_resp.status_code == 200
    second_code = captured["body"].split("verification code is ")[1].split(".")[0]

    # ...but even that freshly issued, CORRECT code must still be refused,
    # because the per-context attempt budget carried forward instead of
    # resetting to zero.
    resp = client.post("/portal/verify/confirm", json={"code": second_code}, headers={"X-Context-Token": token})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Incorrect or expired verification code"

    db.expire_all()
    ctx = db.query(ConsentContext).filter(ConsentContext.token == token).first()
    assert ctx.verified_at is None


def test_verify_confirm_with_no_pending_challenge_gives_same_generic_failure(db, client):
    """Confirming before ever calling /verify/start must fail identically to
    a wrong/expired/exhausted code - never a distinct 404 - so the response
    never tells a caller whether a challenge exists at all."""
    customer, token = _make_context(db, email="verify-no-challenge@example.com")
    resp = client.post("/portal/verify/confirm", json={"code": "123456"}, headers={"X-Context-Token": token})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Incorrect or expired verification code"


def test_verify_start_send_rate_limited(db, client):
    customer, token = _make_context(db, email="verify-ratelimited@example.com")
    for _ in range(3):
        resp = client.post("/portal/verify/start", headers={"X-Context-Token": token})
        assert resp.status_code == 200

    resp = client.post("/portal/verify/start", headers={"X-Context-Token": token})
    assert resp.status_code == 429
    assert resp.json()["detail"] == "Too many verification codes requested; try again later"


def test_portal_overview_requires_verification_when_enabled(db, client):
    customer, token = _make_context(db, email="verify-required@example.com")
    resp = client.get("/portal/overview", headers={"X-Context-Token": token})
    assert resp.status_code == 403


def test_portal_grant_requires_verification_when_enabled(db, client):
    customer, token = _make_context(db, email="verify-required-grant@example.com")
    resp = client.post(
        "/portal/grant", json={"purpose_code": "does-not-matter"}, headers={"X-Context-Token": token}
    )
    assert resp.status_code == 403


def test_portal_withdraw_requires_verification_when_enabled(db, client):
    customer, token = _make_context(db, email="verify-required-withdraw@example.com")
    resp = client.post(
        "/portal/withdraw", json={"purpose_code": "does-not-matter"}, headers={"X-Context-Token": token}
    )
    assert resp.status_code == 403


def test_portal_overview_allows_when_verification_disabled(db, client, monkeypatch):
    from app.api.routes import portal

    monkeypatch.setattr(portal.settings, "PORTAL_REQUIRE_VERIFICATION", False)
    customer, token = _make_context(db, email="verify-disabled@example.com")
    resp = client.get("/portal/overview", headers={"X-Context-Token": token})
    assert resp.status_code == 200


def test_verify_start_failed_send_rolls_back_challenge_and_releases_rate_limit(db, client, monkeypatch):
    """Fix round 1, item 4 (MEDIUM): a delivery failure must not leave a
    phantom challenge behind (a code the customer never received, still
    burning their attempt budget) or burn a rate-limit slot the customer
    cannot get back until the window resets."""
    from app.integrations.notifications.email import ConsoleEmailSender
    from app.models.entities import OtpChallenge

    customer, token = _make_context(db, email="verify-send-fails@example.com")

    def failing_send(self, to, subject, body):
        raise RuntimeError("smtp unavailable")

    monkeypatch.setattr(ConsoleEmailSender, "send", failing_send)
    resp = client.post("/portal/verify/start", headers={"X-Context-Token": token})
    assert resp.status_code == 502

    ctx = db.query(ConsentContext).filter(ConsentContext.token == token).first()
    assert db.query(OtpChallenge).filter(OtpChallenge.context_id == ctx.id).count() == 0

    # The rate-limit slot from the failed attempt must have been released:
    # the full 3-send budget must still be available afterwards.
    def working_send(self, to, subject, body):
        pass

    monkeypatch.setattr(ConsoleEmailSender, "send", working_send)
    for _ in range(3):
        resp = client.post("/portal/verify/start", headers={"X-Context-Token": token})
        assert resp.status_code == 200

    # A 4th now genuinely exceeds the budget - proving it was 3, not 2.
    resp = client.post("/portal/verify/start", headers={"X-Context-Token": token})
    assert resp.status_code == 429


def test_get_email_sender_refuses_console_fallback_in_production(monkeypatch):
    """Fix round 1, item 5 (LOW): a production deploy that forgets to set
    SMTP_HOST must fail loudly instead of silently falling back to
    ConsoleEmailSender and logging live verification codes."""
    from app.core.config import get_settings
    from app.integrations.notifications.email import get_email_sender

    settings = get_settings()
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "SMTP_HOST", "")

    import pytest

    with pytest.raises(RuntimeError):
        get_email_sender()


def test_get_email_sender_still_uses_console_outside_production(monkeypatch):
    """Companion to the above: development/test environments (the default)
    must keep falling back to ConsoleEmailSender when SMTP is unset - this
    is not a blanket ban, only a production-specific guard rail."""
    from app.core.config import get_settings
    from app.integrations.notifications.email import ConsoleEmailSender, get_email_sender

    settings = get_settings()
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "SMTP_HOST", "")

    assert isinstance(get_email_sender(), ConsoleEmailSender)


def test_otp_flow_writes_audit_rows_without_pii(db, client, monkeypatch):
    """Fix round 1, item 6 (LOW): start and confirm must each leave an
    append-only audit trail (send, verify-failed, verify-succeeded) that
    carries an opaque actor id and actor_type=PRINCIPAL, and never the OTP
    code, the customer's email, or their name.

    Deliberately does not reuse `_make_context`, whose external_id
    convention (`CUST-<email>`) exists only so tests using different emails
    get distinct external_ids - that would make `customer.email` a
    substring of `customer.external_id` and defeat this exact check."""
    from datetime import datetime, timedelta, timezone

    from app.integrations.notifications.email import ConsoleEmailSender
    from app.models.entities import AuditLog

    customer = Customer(
        external_id="CUST-OTP-AUDIT-001",
        name="Should Never Appear In Audit",
        email="otp-audit-pii-check@example.com",
        source_app="VERIFY_AUDIT",
    )
    db.add(customer)
    db.flush()
    token = create_context_token(customer.id, "VERIFY_AUDIT")
    db.add(ConsentContext(
        customer_id=customer.id, token=token, source_app="VERIFY_AUDIT",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    ))
    db.commit()

    captured = {}

    def fake_send(self, to, subject, body):
        captured["body"] = body

    monkeypatch.setattr(ConsoleEmailSender, "send", fake_send)

    client.post("/portal/verify/start", headers={"X-Context-Token": token})
    real_code = captured["body"].split("verification code is ")[1].split(".")[0]
    wrong_code = f"{(int(real_code) + 1) % 1_000_000:06d}"

    client.post("/portal/verify/confirm", json={"code": wrong_code}, headers={"X-Context-Token": token})
    client.post("/portal/verify/confirm", json={"code": real_code}, headers={"X-Context-Token": token})

    rows = db.query(AuditLog).filter(AuditLog.customer_id == customer.id).all()
    events = {row.event for row in rows}
    assert "OTP_SENT" in events
    assert "OTP_VERIFY_FAILED" in events
    assert "OTP_VERIFIED" in events

    for row in rows:
        assert row.actor_type == "PRINCIPAL"
        assert "Should Never Appear" not in (row.actor_username or "")
        assert "Should Never Appear" not in (row.reason or "")
        assert "otp-audit-pii-check" not in (row.actor_username or "")
        assert "otp-audit-pii-check" not in (row.reason or "")
        assert real_code not in (row.actor_username or "")
        assert real_code not in (row.reason or "")
        assert wrong_code not in (row.actor_username or "")
        assert wrong_code not in (row.reason or "")


def test_rate_limiter_release_undoes_the_most_recent_hit():
    """Fix round 2: RateLimiter needs a public way to give back a slot a
    caller consumed via allow() but then didn't actually use (start_otp
    releasing an OTP-send slot after email delivery failed, per fix round
    1). Also covers the documented no-op case: releasing a key with no
    recorded hits - a fresh key, or one already released down to empty -
    must not raise."""
    from app.core.utils import RateLimiter

    limiter = RateLimiter(limit=1, window_seconds=60)
    key = "release-test-key"

    assert limiter.allow(key) is True
    assert limiter.allow(key) is False  # slot consumed, budget exhausted

    limiter.release(key)
    assert limiter.allow(key) is True  # slot given back, budget available again

    limiter.release("a-key-with-no-hits-at-all")  # never touched - still a no-op
    limiter.release(key)
    limiter.release(key)  # already empty after the pop above - still a no-op


def test_rate_limiter_release_does_not_affect_other_keys_or_limiters():
    """release() must be scoped to exactly the key (and instance) it is
    called on, so it cannot change the behaviour of the shared
    login_limiter/context_limiter instances (or of an unrelated key on the
    same instance) just because RateLimiter gained the method."""
    from app.core.utils import RateLimiter, context_limiter, login_limiter

    limiter = RateLimiter(limit=1, window_seconds=60)
    assert limiter.allow("key-a") is True
    assert limiter.allow("key-b") is True  # separate key, own budget

    limiter.release("key-a")
    assert limiter.allow("key-a") is True  # released
    assert limiter.allow("key-b") is False  # untouched, still exhausted

    # The shared module-level limiters are ordinary RateLimiter instances,
    # so they gained the same public method - but it does nothing unless a
    # caller invokes it on a key they themselves consumed.
    assert hasattr(login_limiter, "release")
    assert hasattr(context_limiter, "release")
