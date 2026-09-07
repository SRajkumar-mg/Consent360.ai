"""Email-OTP identity verification for the principal self-service portal
(R3-05, interim implementation ahead of the fuller R3-06 notification
service).

The OTP itself is a credential and is never persisted or returned in plain
form: ``start_otp`` stores only ``hmac_digest(code)`` and hands the code to
the configured ``EmailSender`` (never to the caller, never into a log
outside ``ConsoleEmailSender``, never into a URL). ``confirm_otp`` compares
digests in constant time and reports only a plain boolean - a wrong code, an
expired challenge, an exhausted attempt cap and "no challenge at all" all
return ``False`` identically, so the response given to a caller never
reveals which of those it was.

The guess budget is enforced per **context**, not per challenge row:
``start_otp`` carries the attempts already spent on the most recent
challenge forward into any new one it issues, so requesting a fresh code
never resets an attacker's attempt count back to zero (fix round 1; see
test_attempt_cap_persists_across_new_verification_code).
"""
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.encryption import hmac_digest
from app.core.utils import RateLimiter
from app.integrations.notifications.email import get_email_sender
from app.models.entities import ConsentContext, Customer, OtpChallenge
from app.services.audit import log_audit

otp_send_limiter = RateLimiter(limit=3, window_seconds=900)  # 3 sends / 15 min per email

# Fixed reference digest used by confirm_otp so a "no pending challenge"
# guess still pays for a real constant-time comparison rather than
# short-circuiting immediately - see confirm_otp for the residual timing
# gap this does not close.
_DUMMY_CODE_HASH = hmac_digest("000000")


def _generate_code() -> str:
    """A six-digit code from the CSPRNG, never `random`.

    This code is the ONLY thing standing between a caller and another
    person's consent record on the self-service portal - it is a
    credential, not a nonce. `random.randint` is Mersenne Twister:
    deterministic, and its 19937-bit internal state is recoverable from
    a few hundred observed outputs, after which every subsequent code is
    predictable. An attacker only needs codes they are entitled to see
    (request one for their own address, repeatedly) to recover that
    state, and the same generator serves every other principal.
    `secrets.randbelow` draws from the OS CSPRNG, which has no such
    recoverable state; the uniform range is unchanged (0-999999).
    """
    return f"{secrets.randbelow(1_000_000):06d}"


def start_otp(db: Session, context: ConsentContext, customer: Customer) -> None:
    if not customer.email:
        raise HTTPException(status_code=422, detail="No email on file for this account - cannot send a verification code")

    # Resolve (and validate) the sender before spending any other state: a
    # production deploy with no SMTP configured must fail here, before it
    # burns a rate-limit slot or creates a challenge row.
    sender = get_email_sender()

    email_key = hmac_digest(customer.email.strip().lower())
    rate_limit_key = f"otp:{email_key}"
    if not otp_send_limiter.allow(rate_limit_key):
        raise HTTPException(status_code=429, detail="Too many verification codes requested; try again later")

    # Carry the attempts already spent on this context's most recent
    # challenge (if any) forward into the new one, so a fresh code never
    # resets the guess budget.
    prior = (
        db.query(OtpChallenge)
        .filter(OtpChallenge.context_id == context.id)
        .order_by(OtpChallenge.id.desc())
        .first()
    )
    carried_attempts = prior.attempts if prior else 0

    code = _generate_code()
    challenge = OtpChallenge(
        context_id=context.id,
        email_search=email_key,
        code_hash=hmac_digest(code),
        attempts=carried_attempts,
        max_attempts=5,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db.add(challenge)
    try:
        sender.send(
            to=customer.email,
            subject="Your Consent360 verification code",
            body=f"Your verification code is {code}. It expires in 10 minutes.",
        )
    except Exception as exc:
        # Never leave behind a phantom challenge (a code the customer never
        # actually received, still burning their attempt budget) or a burnt
        # rate-limit slot (blocking a retry until the window resets) after a
        # delivery failure.
        db.rollback()
        otp_send_limiter.release(rate_limit_key)
        log_audit(
            db,
            "OTP_SEND_FAILED",
            actor_username=f"principal:{customer.external_id}",
            actor_id=customer.external_id,
            actor_type="PRINCIPAL",
            source_app="PORTAL",
            customer_id=customer.id,
            customer_external_id=customer.external_id,
            reason="Verification email delivery failed",
        )
        raise HTTPException(
            status_code=502, detail="Could not send the verification email; please try again"
        ) from exc

    db.commit()
    log_audit(
        db,
        "OTP_SENT",
        actor_username=f"principal:{customer.external_id}",
        actor_id=customer.external_id,
        actor_type="PRINCIPAL",
        source_app="PORTAL",
        customer_id=customer.id,
        customer_external_id=customer.external_id,
        reason="Verification code sent via self-service portal",
    )


def _log_confirm_attempt(db: Session, context: ConsentContext, *, success: bool) -> None:
    log_audit(
        db,
        "OTP_VERIFIED" if success else "OTP_VERIFY_FAILED",
        actor_username=f"principal:context:{context.id}",
        actor_id=str(context.customer_id),
        actor_type="PRINCIPAL",
        source_app="PORTAL",
        customer_id=context.customer_id,
        reason=(
            "Identity verification succeeded via email OTP"
            if success
            else "Identity verification attempt failed via email OTP"
        ),
    )


def confirm_otp(db: Session, context: ConsentContext, code: str) -> bool:
    """Return True only if `code` matches the newest unconsumed challenge for
    `context`, that challenge has not expired, and it has not exhausted its
    attempt cap. Every other case - no pending challenge, an expired one, an
    exhausted one, or a simple mismatch - returns False with no distinction,
    so a caller (or an attacker probing the endpoint) cannot tell which
    applied. The digest comparison always uses `hmac.compare_digest` and
    every branch performs equivalent work (see module docstring / report for
    the one residual timing gap: a request with no pending challenge at all
    skips the challenge-row UPDATE/COMMIT that every other branch pays for).
    """
    challenge = (
        db.query(OtpChallenge)
        .filter(OtpChallenge.context_id == context.id, OtpChallenge.consumed_at.is_(None))
        .order_by(OtpChallenge.id.desc())
        .first()
    )
    supplied_hash = hmac_digest(code.strip()) or ""

    if challenge is None:
        # Nothing to increment or commit - but still spend a real
        # constant-time comparison against a fixed dummy digest, and still
        # emit the same shape of audit row as every other failure below.
        hmac.compare_digest(supplied_hash, _DUMMY_CODE_HASH)
        _log_confirm_attempt(db, context, success=False)
        return False

    now = datetime.now(timezone.utc)
    within_window = challenge.expires_at >= now and challenge.attempts < challenge.max_attempts

    # Always pay the same write - one increment, one commit - whether or not
    # the challenge is still within its window, so "expired" and "exhausted"
    # cannot be timed apart from a plain wrong-code guess.
    challenge.attempts += 1
    matches = hmac.compare_digest(supplied_hash, challenge.code_hash)
    success = within_window and matches
    if success:
        challenge.consumed_at = now
        context.verified_at = now
        context.verification_method = "EMAIL_OTP"
    db.commit()
    _log_confirm_attempt(db, context, success=success)
    return success
