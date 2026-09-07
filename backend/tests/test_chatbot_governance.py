import time
from datetime import date, timedelta

import pytest

from app.models.entities import AuditLog, Customer, Processor, Transfer


def _activate_groq_processor(db):
    proc = db.query(Processor).filter(Processor.name == "Groq LLM Inference").first()
    proc.is_active = True
    proc.contract_valid_until = date.today() + timedelta(days=30)
    db.commit()
    return proc


def test_transfer_register_seeded_by_migration(db):
    proc = db.query(Processor).filter(Processor.name == "Groq LLM Inference").first()
    assert proc is not None
    assert proc.country == "US"
    transfer = db.query(Transfer).filter(Transfer.processor_id == proc.id).first()
    assert transfer is not None
    assert transfer.destination_country == "US"
    assert transfer.restricted is False


def test_platform_context_excludes_actor_usernames(db):
    from app.api.routes.chatbot import _gather_platform_context

    db.add(AuditLog(event="LOGIN", actor_username="alice.staff", actor_type="USER", reason="test"))
    db.commit()
    ctx = _gather_platform_context(db)
    assert "alice.staff" not in ctx
    assert "USER" in ctx or "SYSTEM" in ctx


def test_chatbot_disabled_without_active_contract(db, client, staff_token, monkeypatch):
    db.query(Processor).filter(Processor.name == "Groq LLM Inference").update({"is_active": False})
    db.commit()

    from app.api.routes import chatbot

    called = {"count": 0}

    def fake_call_groq(*args, **kwargs):
        called["count"] += 1
        return "should not be called"

    monkeypatch.setattr(chatbot, "_call_groq", fake_call_groq)

    resp = client.post("/chatbot", headers={"Authorization": f"Bearer {staff_token}"}, json={"message": "hi"})
    assert resp.status_code == 200
    assert "unavailable" in resp.json()["reply"]
    assert called["count"] == 0


def test_chatbot_enabled_with_active_contract(db, client, staff_token, monkeypatch):
    proc = db.query(Processor).filter(Processor.name == "Groq LLM Inference").first()
    proc.is_active = True
    proc.contract_valid_until = date.today() + timedelta(days=30)
    db.commit()

    from app.api.routes import chatbot

    def fake_call_groq(system_prompt, message, history):
        return "canned reply"

    monkeypatch.setattr(chatbot, "_call_groq", fake_call_groq)

    resp = client.post("/chatbot", headers={"Authorization": f"Bearer {staff_token}"}, json={"message": "hi"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "canned reply"


def test_chatbot_blocked_call_writes_audit_entry(db, client, staff_token, monkeypatch):
    """The refusal path must not just fail silently - it must leave an audit
    trail, per the DoD ("... and an audit entry")."""
    db.query(Processor).filter(Processor.name == "Groq LLM Inference").update({"is_active": False})
    db.commit()

    from app.api.routes import chatbot

    monkeypatch.setattr(chatbot, "_call_groq", lambda *a, **k: "should not be called")

    before = db.query(AuditLog).filter(AuditLog.event == "CHATBOT_BLOCKED").count()
    resp = client.post("/chatbot", headers={"Authorization": f"Bearer {staff_token}"}, json={"message": "hi"})
    assert resp.status_code == 200

    entry = (
        db.query(AuditLog)
        .filter(AuditLog.event == "CHATBOT_BLOCKED")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert entry is not None
    after = db.query(AuditLog).filter(AuditLog.event == "CHATBOT_BLOCKED").count()
    assert after == before + 1
    # The audit record itself is internal accountability data (who tried to
    # use the chatbot while it was blocked) - it is not customer PII and is
    # not what gets sent to the LLM provider, so it is fine for it to name
    # the staff actor here.
    assert entry.actor_type == "USER"


def test_chatbot_redacts_free_text_pii_from_outbound_message_and_history(db, client, staff_token, monkeypatch):
    """Fix round 1, gap 1 (HIGH): the assembled system prompt is clean, but
    the free-text channel - the staff user's own message and prior history
    turns - was going to the provider verbatim. Seed a customer with a
    recognisable name/email, send a chat message that itself contains that
    email plus a phone number, put the email in a history turn too, and
    assert against the *actual* payload handed to `_call_groq` (message,
    history and system prompt) - not a helper's return value in isolation."""
    proc = db.query(Processor).filter(Processor.name == "Groq LLM Inference").first()
    proc.is_active = True
    proc.contract_valid_until = date.today() + timedelta(days=30)

    customer = Customer(
        external_id="ext-freetext-canary-77213",
        name="Rajesh Freetextcanary",
        email="rajesh.freetextcanary@example.com",
        source_app="FREETEXT_CANARY_TEST",
    )
    db.add(customer)
    db.commit()

    from app.api.routes import chatbot

    captured: dict = {}

    def fake_call_groq(system_prompt, message, history):
        captured["system_prompt"] = system_prompt
        captured["message"] = message
        captured["history"] = history
        return "canned reply"

    monkeypatch.setattr(chatbot, "_call_groq", fake_call_groq)

    email = "rajesh.freetextcanary@example.com"
    phone = "+91 9876543210"
    resp = client.post(
        "/chatbot",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={
            "message": f"What's the consent status for {email}, phone {phone}?",
            "history": [
                {"role": "user", "content": f"Earlier I looked up {email} as well."},
                {"role": "assistant", "content": "Sure, one moment."},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # message: 1 email + 1 phone; first history turn: 1 email. Total 3.
    assert body["redacted_count"] == 3
    assert email not in body["reply"]
    assert phone not in body["reply"]

    # The real payload handed to the provider - not the endpoint's own
    # response - is where the leak would actually happen.
    assert email not in captured["message"]
    assert phone not in captured["message"]
    assert "[redacted-email]" in captured["message"]
    assert "[redacted-phone]" in captured["message"]

    assert email not in captured["system_prompt"]
    assert phone not in captured["system_prompt"]

    for turn in captured["history"]:
        assert email not in turn.content
        assert phone not in turn.content
    assert "[redacted-email]" in captured["history"][0].content


@pytest.mark.parametrize(
    "phone",
    [
        "+91-98765-43210",
        "98765 43210",
        "98765.43210",
        "(98765) 43210",
    ],
)
def test_chatbot_redacts_phone_numbers_with_internal_separators(db, client, staff_token, monkeypatch, phone):
    """Fix round 2, leak 1: the round-1 pattern only tolerated a separator
    right after the country code, so every one of these - the most common
    ways people actually write an Indian mobile number - reached the
    provider verbatim with redacted_count=0. Reproduces the reviewer's own
    method: drive the live endpoint and capture the real payload."""
    _activate_groq_processor(db)

    from app.api.routes import chatbot

    captured: dict = {}

    def fake_call_groq(system_prompt, message, history):
        captured["message"] = message
        return "canned reply"

    monkeypatch.setattr(chatbot, "_call_groq", fake_call_groq)

    resp = client.post(
        "/chatbot",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"message": f"Please call this number: {phone}"},
    )
    assert resp.status_code == 200
    assert resp.json()["redacted_count"] == 1
    assert phone not in captured["message"]
    assert "[redacted-phone]" in captured["message"]


def test_chatbot_redacts_email_with_zero_width_space(db, client, staff_token, monkeypatch):
    """Fix round 2, leak 2: a zero-width space anywhere in an email - right
    before the '@', or inside the local part - previously defeated the
    pattern entirely (the reviewer's report: the local-part prefix was left
    unredacted). Normalisation now strips Unicode "format" characters
    (category Cf, which includes ZWSP) before matching."""
    _activate_groq_processor(db)

    from app.api.routes import chatbot

    zwsp = "​"
    evasive_emails = [
        f"jane{zwsp}@example.com",
        f"ja{zwsp}ne@example.com",
    ]

    for evasive_email in evasive_emails:
        captured: dict = {}

        def fake_call_groq(system_prompt, message, history):
            captured["message"] = message
            return "canned reply"

        monkeypatch.setattr(chatbot, "_call_groq", fake_call_groq)

        resp = client.post(
            "/chatbot",
            headers={"Authorization": f"Bearer {staff_token}"},
            json={"message": f"Look up account for {evasive_email} please"},
        )
        assert resp.status_code == 200
        assert resp.json()["redacted_count"] == 1
        assert evasive_email not in captured["message"]
        assert "jane" not in captured["message"]
        assert "example.com" not in captured["message"]
        assert "[redacted-email]" in captured["message"]


def test_chatbot_redacts_aadhaar_with_comma_separators(db, client, staff_token, monkeypatch):
    """Fix round 2, leak 3a: comma-separated Aadhaar-like groupings ("1234,
    5678, 9012") were not covered by the round-1 pattern (space/dash only)."""
    _activate_groq_processor(db)

    from app.api.routes import chatbot

    captured: dict = {}

    def fake_call_groq(system_prompt, message, history):
        captured["message"] = message
        return "canned reply"

    monkeypatch.setattr(chatbot, "_call_groq", fake_call_groq)

    aadhaar = "1234, 5678, 9012"
    resp = client.post(
        "/chatbot",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"message": f"ID number on file: {aadhaar}"},
    )
    assert resp.status_code == 200
    assert resp.json()["redacted_count"] == 1
    assert aadhaar not in captured["message"]
    assert "[redacted-aadhaar]" in captured["message"]


def test_chatbot_redacts_external_id_without_dash(db, client, staff_token, monkeypatch):
    """Fix round 2, leak 3b: "CUST123" (no dash) reached the provider
    verbatim - the round-1 pattern required the literal dash. Also guards
    against the false-positive this widening could introduce: ordinary
    words starting with "cust" ("customer", "custody") must NOT be caught."""
    _activate_groq_processor(db)

    from app.api.routes import chatbot

    captured: dict = {}

    def fake_call_groq(system_prompt, message, history):
        captured["message"] = message
        return "canned reply"

    monkeypatch.setattr(chatbot, "_call_groq", fake_call_groq)

    resp = client.post(
        "/chatbot",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"message": "The customer with id CUST123 called about custody of an old account"},
    )
    assert resp.status_code == 200
    assert resp.json()["redacted_count"] == 1
    assert "CUST123" not in captured["message"]
    assert "[redacted-id]" in captured["message"]
    # "customer" and "custody" must survive untouched - only the real id
    # shape is a match.
    assert "customer" in captured["message"]
    assert "custody" in captured["message"]


@pytest.mark.parametrize(
    "external_id",
    [
        "cust-123",
        "CUST123",
        "cust123",
        "CUST-A1B2C3",  # round 4: the hex-digest shape derive_customer_id
        # actually produces - starts with a letter, no digit immediately
        # after the dash, so round 3's "digit right after the dash" rule
        # missed it
        "CuSt-a1B2c3",  # round 4: mixed-case variant of the same shape
    ],
)
def test_chatbot_redacts_external_id_case_insensitively(db, client, staff_token, monkeypatch, external_id):
    """Fix round 3, gap 1 (regression): round 1 caught "cust-123"; round 2's
    fix for the "customer"/"custody" false positive over-corrected to
    uppercase-only matching, silently dropping lowercase-typed real ids.
    Requiring a digit immediately after the optional dash restored
    case-insensitive matching for "cust-123"/"CUST123"/"cust123", but still
    missed "CUST-A1B2C3" (round 4) since its first tail character is a
    letter. The final rule - a bounded lookahead requiring a digit
    *somewhere* in the tail, not necessarily first - redacts all five
    variants here without reopening the false-positive hole (see the
    paired test below, which must keep passing alongside this one)."""
    _activate_groq_processor(db)

    from app.api.routes import chatbot

    captured: dict = {}

    def fake_call_groq(system_prompt, message, history):
        captured["message"] = message
        return "canned reply"

    monkeypatch.setattr(chatbot, "_call_groq", fake_call_groq)

    resp = client.post(
        "/chatbot",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"message": f"Please check {external_id} for me"},
    )
    assert resp.status_code == 200
    assert resp.json()["redacted_count"] == 1
    assert external_id not in captured["message"]
    assert "[redacted-id]" in captured["message"]


def test_chatbot_external_id_guard_does_not_regress_on_plain_words(db, client, staff_token, monkeypatch):
    """Paired with the case-insensitivity fix above: "customer"/"custody"/
    "custom" must never be redacted just because they start with "cust" -
    kept as its own test (independent of the id-shape test above) so this
    guard cannot silently regress again, per the round 3 instruction. Still
    holds under round 4's "digit anywhere in the tail" rule: none of these
    three words contain a digit at all, so the lookahead never matches."""
    _activate_groq_processor(db)

    from app.api.routes import chatbot

    captured: dict = {}

    def fake_call_groq(system_prompt, message, history):
        captured["message"] = message
        return "canned reply"

    monkeypatch.setattr(chatbot, "_call_groq", fake_call_groq)

    resp = client.post(
        "/chatbot",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"message": "The customer called about custody of a custom order"},
    )
    assert resp.status_code == 200
    assert resp.json()["redacted_count"] == 0
    assert captured["message"] == "The customer called about custody of a custom order"


def test_chatbot_redacts_phone_number_in_non_ascii_digits(db, client, staff_token, monkeypatch):
    """Fix round 3, gap 2: the candidate regex is Unicode-digit-aware
    (Python's `\\d` matches any Unicode decimal digit by default), but
    _looks_like_phone previously compared a raw matched character against
    the literal ASCII string "6789", so a Bengali-numeral phone number
    matched the candidate shape but always failed validation. _digits_only
    now converts every digit via unicodedata.digit() before checking, which
    covers every Indic/Perso-Arabic/etc. digit script uniformly - this test
    uses Bengali numerals specifically because that is what was
    demonstrated, not because it is the only script that mattered."""
    _activate_groq_processor(db)

    from app.api.routes import chatbot

    captured: dict = {}

    def fake_call_groq(system_prompt, message, history):
        captured["message"] = message
        return "canned reply"

    monkeypatch.setattr(chatbot, "_call_groq", fake_call_groq)

    bengali_phone = "৯৮৭৬৫৪৩২১০"  # Bengali digits for 9876543210
    resp = client.post(
        "/chatbot",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"message": f"Call me at {bengali_phone} today"},
    )
    assert resp.status_code == 200
    assert resp.json()["redacted_count"] == 1
    assert bengali_phone not in captured["message"]
    assert "[redacted-phone]" in captured["message"]


@pytest.mark.parametrize("host", ["192.168.1.1", "[203.0.113.5]"])
def test_chatbot_redacts_email_with_ip_literal_host(db, client, staff_token, monkeypatch, host):
    """Fix round 3, gap 3: an email whose host is a bare or bracketed IPv4
    literal (both valid per RFC 5321) reached the provider verbatim because
    the domain pattern required a letters-only final label."""
    _activate_groq_processor(db)

    from app.api.routes import chatbot

    captured: dict = {}

    def fake_call_groq(system_prompt, message, history):
        captured["message"] = message
        return "canned reply"

    monkeypatch.setattr(chatbot, "_call_groq", fake_call_groq)

    email = f"user@{host}"
    resp = client.post(
        "/chatbot",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"message": f"Send it to {email} please"},
    )
    assert resp.status_code == 200
    assert resp.json()["redacted_count"] == 1
    assert email not in captured["message"]
    assert "[redacted-email]" in captured["message"]


@pytest.mark.parametrize(
    "text",
    [
        "98765, 43210",           # comma-separated phone
        "+91 - 98765 - 43210",    # widely spaced phone
    ],
)
def test_chatbot_redacts_phone_with_wider_separator_gaps(db, client, staff_token, monkeypatch, text):
    """Fix round 3, gap 4a: a comma between digit groups, or more generous
    spacing than the round-2 candidate window absorbed, both leaked."""
    _activate_groq_processor(db)

    from app.api.routes import chatbot

    captured: dict = {}

    def fake_call_groq(system_prompt, message, history):
        captured["message"] = message
        return "canned reply"

    monkeypatch.setattr(chatbot, "_call_groq", fake_call_groq)

    resp = client.post(
        "/chatbot",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"message": f"Reach them on {text} anytime"},
    )
    assert resp.status_code == 200
    assert resp.json()["redacted_count"] == 1
    assert text not in captured["message"]
    assert "[redacted-phone]" in captured["message"]


def test_chatbot_redacts_aadhaar_with_wider_separator_gaps(db, client, staff_token, monkeypatch):
    """Fix round 3, gap 4b: Aadhaar spaced more widely than the round-2
    candidate window absorbed."""
    _activate_groq_processor(db)

    from app.api.routes import chatbot

    captured: dict = {}

    def fake_call_groq(system_prompt, message, history):
        captured["message"] = message
        return "canned reply"

    monkeypatch.setattr(chatbot, "_call_groq", fake_call_groq)

    aadhaar = "1234,  5678,   9012"
    resp = client.post(
        "/chatbot",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"message": f"ID on file: {aadhaar}"},
    )
    assert resp.status_code == 200
    assert resp.json()["redacted_count"] == 1
    assert aadhaar not in captured["message"]
    assert "[redacted-aadhaar]" in captured["message"]


def test_chatbot_large_message_returns_promptly(db, client, staff_token, monkeypatch):
    """Fix round 2, gap 2: _EMAIL_RE previously degraded quadratically on a
    long run of "nothing to match" characters - the reviewer measured 5.62s
    at 50,000 characters and ~90s at 200,000, all before the provider call,
    on an endpoint any dashboard-permitted staff user can reach. A message
    at the new length cap, filled with worst-case content, must now return
    in a small fraction of a second - not tens of seconds - because every
    pattern's quantifiers are bounded. Uses the live endpoint, the same way
    the reviewer found the regression, with generous timing headroom so
    this fails loudly (rather than flakily) if the pathological path is
    ever reintroduced."""
    _activate_groq_processor(db)

    from app.api.routes import chatbot

    monkeypatch.setattr(chatbot, "_call_groq", lambda *a, **k: "canned reply")

    pathological_message = "a" * chatbot._MAX_MESSAGE_LENGTH
    start = time.perf_counter()
    resp = client.post(
        "/chatbot",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"message": pathological_message},
    )
    elapsed = time.perf_counter() - start
    assert resp.status_code == 200
    assert elapsed < 2.0, f"chatbot took {elapsed:.2f}s on a {len(pathological_message)}-char message"


def test_redact_pii_handles_oversized_pathological_input_quickly():
    """Exercises _redact_pii directly with input well beyond the HTTP-level
    cap (200,000 characters, matching the reviewer's own worst-case size),
    proving the fix is in the matching algorithm itself (bounded
    quantifiers) and not merely masked by the new max_length - so the
    function stays safe even if it is ever reused somewhere the cap does
    not apply."""
    from app.api.routes.chatbot import _redact_pii

    pathological = "a" * 200_000
    start = time.perf_counter()
    _, count = _redact_pii(pathological)
    elapsed = time.perf_counter() - start
    assert count == 0
    assert elapsed < 2.0, f"_redact_pii took {elapsed:.2f}s on 200,000 chars - possible ReDoS regression"


def test_chatbot_rejects_message_over_max_length(client, staff_token):
    """Gap 2's second, independent line of defence: an unbounded paste is
    rejected cleanly (422) at the API boundary rather than absorbed."""
    from app.api.routes import chatbot

    oversized = "a" * (chatbot._MAX_MESSAGE_LENGTH + 1)
    resp = client.post(
        "/chatbot",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"message": oversized},
    )
    assert resp.status_code == 422


def test_chatbot_rejects_oversized_history_and_too_many_turns(client, staff_token):
    from app.api.routes import chatbot

    resp = client.post(
        "/chatbot",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={
            "message": "hi",
            "history": [{"role": "user", "content": "a" * (chatbot._MAX_MESSAGE_LENGTH + 1)}],
        },
    )
    assert resp.status_code == 422

    too_many_turns = [{"role": "user", "content": "hi"} for _ in range(chatbot._MAX_HISTORY_TURNS + 1)]
    resp2 = client.post(
        "/chatbot",
        headers={"Authorization": f"Bearer {staff_token}"},
        json={"message": "hi", "history": too_many_turns},
    )
    assert resp2.status_code == 422


def test_chatbot_response_always_carries_a_channel_notice(db, client, staff_token, monkeypatch):
    """Fix round 2, point 3: since regex shape-matching cannot catch
    everything (identifiers split across turns, obfuscated forms, PII
    shapes not on the list), every response - not just ones where something
    was actually redacted - carries a standing disclosure that this is a
    length-capped, best-effort-redacted channel to a third-party processor,
    so staff cannot infer "no redaction notice this time" means "nothing
    sensitive could have gone through"."""
    _activate_groq_processor(db)

    from app.api.routes import chatbot

    monkeypatch.setattr(chatbot, "_call_groq", lambda *a, **k: "canned reply")

    resp = client.post("/chatbot", headers={"Authorization": f"Bearer {staff_token}"}, json={"message": "hi"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["redacted_count"] == 0
    assert body["channel_notice"] == chatbot._CHANNEL_NOTICE
    assert "third-party" in body["channel_notice"]


def test_chatbot_blocked_when_transfer_destination_is_restricted(db, client, staff_token, monkeypatch):
    """Fix round 1, gap 2 (MEDIUM): RESTRICTED_COUNTRIES must actually gate
    the chatbot, not just sit in config. A processor that is otherwise in
    good standing (active, unexpired contract) must still be refused when
    its transfer register destination is a restricted country, and the
    register's `restricted` flag must be recomputed live against the
    current config rather than only reflecting whatever was seeded."""
    proc = db.query(Processor).filter(Processor.name == "Groq LLM Inference").first()
    proc.is_active = True
    proc.contract_valid_until = date.today() + timedelta(days=30)
    db.commit()

    from app.api.routes import chatbot

    monkeypatch.setattr(chatbot, "_call_groq", lambda *a, **k: "should not be called")
    monkeypatch.setattr(chatbot.settings, "RESTRICTED_COUNTRIES", "US")

    before = db.query(AuditLog).filter(AuditLog.event == "CHATBOT_BLOCKED").count()
    resp = client.post("/chatbot", headers={"Authorization": f"Bearer {staff_token}"}, json={"message": "hi"})
    assert resp.status_code == 200
    assert "unavailable" in resp.json()["reply"]

    after = db.query(AuditLog).filter(AuditLog.event == "CHATBOT_BLOCKED").count()
    assert after == before + 1

    transfer = (
        db.query(Transfer)
        .filter(Transfer.processor_id == proc.id, Transfer.destination_country == "US")
        .first()
    )
    db.refresh(transfer)
    assert transfer.restricted is True

    # Lifting the restriction re-enables the chatbot with no other change,
    # and recomputes the register entry back to unrestricted - proving the
    # check is live, not a one-time write.
    monkeypatch.setattr(chatbot.settings, "RESTRICTED_COUNTRIES", "")

    def fake_call_groq(system_prompt, message, history):
        return "canned reply"

    monkeypatch.setattr(chatbot, "_call_groq", fake_call_groq)

    resp2 = client.post("/chatbot", headers={"Authorization": f"Bearer {staff_token}"}, json={"message": "hi"})
    assert resp2.status_code == 200
    assert resp2.json()["reply"] == "canned reply"

    db.refresh(transfer)
    assert transfer.restricted is False


def test_chatbot_prompt_excludes_customer_pii_end_to_end(db, client, staff_token, monkeypatch):
    """Reproduces the real request path end to end: seeds a customer with a
    recognisable name/email/phone/external_id and a staff actor username on
    an audit row, then captures the *actual* system prompt the chat endpoint
    hands to the provider call (not a helper's return value in isolation -
    the real payload assembled inside the `chat` view function) and asserts
    none of that PII, nor the actor username, appears anywhere in it."""
    proc = db.query(Processor).filter(Processor.name == "Groq LLM Inference").first()
    proc.is_active = True
    proc.contract_valid_until = date.today() + timedelta(days=30)

    customer = Customer(
        external_id="ext-pii-canary-99182",
        name="Priyanka Recognisablename",
        email="priyanka.recognisable@example.com",
        phone="+91-9000000001",
        source_app="PII_CANARY_TEST",
    )
    db.add(customer)
    db.add(
        AuditLog(
            event="LOGIN",
            actor_username="bob.recognisable.staff",
            actor_type="USER",
            ip_address="203.0.113.42",
            reason="test",
        )
    )
    db.commit()

    from app.api.routes import chatbot

    captured: dict = {}

    def fake_call_groq(system_prompt, message, history):
        captured["system_prompt"] = system_prompt
        captured["message"] = message
        return "canned reply"

    monkeypatch.setattr(chatbot, "_call_groq", fake_call_groq)

    resp = client.post("/chatbot", headers={"Authorization": f"Bearer {staff_token}"}, json={"message": "hi"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "canned reply"

    prompt = captured["system_prompt"]
    assert prompt  # the fake was actually invoked with a real, non-empty prompt

    leaked_pii = [
        "Priyanka Recognisablename",
        "priyanka.recognisable@example.com",
        "+91-9000000001",
        "ext-pii-canary-99182",
        "bob.recognisable.staff",
        "203.0.113.42",
    ]
    for pii in leaked_pii:
        assert pii not in prompt, f"PII leaked into LLM prompt: {pii!r}"


def test_restricted_country_check_flags_configured_destinations(monkeypatch):
    from app.api.routes import chatbot

    monkeypatch.setattr(chatbot.settings, "RESTRICTED_COUNTRIES", "US, cn")
    restricted = chatbot.settings.restricted_country_list
    assert restricted == ["US", "CN"]

    monkeypatch.setattr(chatbot.settings, "RESTRICTED_COUNTRIES", "")
    assert chatbot.settings.restricted_country_list == []


def test_settings_is_restricted_destination_helper(monkeypatch):
    from app.api.routes import chatbot

    monkeypatch.setattr(chatbot.settings, "RESTRICTED_COUNTRIES", "US,CN")
    assert chatbot.settings.is_restricted_destination("us") is True
    assert chatbot.settings.is_restricted_destination("US") is True
    assert chatbot.settings.is_restricted_destination("IN") is False
    assert chatbot.settings.is_restricted_destination(None) is False
    assert chatbot.settings.is_restricted_destination("") is False


def test_seeded_transfer_destination_checked_against_restricted_countries(db):
    from app.api.routes import chatbot

    transfer = (
        db.query(Transfer)
        .join(Processor, Transfer.processor_id == Processor.id)
        .filter(Processor.name == "Groq LLM Inference")
        .first()
    )
    assert transfer is not None
    # With the default (empty) RESTRICTED_COUNTRIES, the seeded US transfer
    # is correctly not flagged as restricted.
    assert transfer.destination_country not in chatbot.settings.restricted_country_list
    assert transfer.restricted is False
