"""R3-05: Principal identity verification service."""
from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import create_context_token, hash_otp, generate_otp
from app.core.utils import RateLimiter
from app.models.entities import Customer, VerificationToken
from app.schemas.schemas import CustomerContextOut
from app.services.audit import log_audit
from app.services.context import create_context_for_customer, derive_customer_id
from app.core.encryption import hmac_digest

settings = get_settings()
router = APIRouter(prefix="/portal", tags=["verification"])

otp_rate_limiter = RateLimiter(limit=settings.OTP_RATE_LIMIT_ATTEMPTS,
                                window_seconds=settings.OTP_RATE_LIMIT_WINDOW_MINUTES * 60)


class VerifyStartRequest(BaseModel):
    email: str | None = None
    phone: str | None = None
    method: str = Field(default="OTP_EMAIL", pattern=r"^(OTP_EMAIL|OTP_SMS|MAGIC_LINK)$")


class VerifyStartResponse(BaseModel):
    message: str
    expires_in_minutes: int


class VerifyConfirmRequest(BaseModel):
    identifier: str
    otp: str = Field(min_length=6, max_length=6)


class FiduciaryAssertRequest(BaseModel):
    assertion: str
    signature: str


def _send_otp_stub(identifier: str, otp: str, method: str) -> None:
    """Stub for OTP delivery - replace with R3-06 notification service."""
    import logging
    logging.getLogger("verification").info(
        "OTP %s sent to %s via %s (stub - no real delivery)", otp, identifier, method
    )


@router.post("/verify/start", response_model=VerifyStartResponse)
def verify_start(payload: VerifyStartRequest, request: Request, db: Session = Depends(get_db)):
    identifier = payload.email or payload.phone
    if not identifier:
        raise HTTPException(status_code=422, detail="email or phone is required")

    if not otp_rate_limiter.allow(f"otp:{identifier}"):
        raise HTTPException(status_code=429, detail="Too many OTP requests, please try again later")

    otp = generate_otp()
    token_hash = hash_otp(otp)

    verification = VerificationToken(
        identifier=identifier.strip().lower(),
        token_hash=token_hash,
        verification_method=payload.method,
        max_attempts=settings.OTP_MAX_ATTEMPTS,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.OTP_EXPIRE_MINUTES),
    )
    db.add(verification)
    db.commit()

    _send_otp_stub(identifier, otp, payload.method)

    log_audit(db, "VERIFY_OTP_SENT", actor_username="system", source_app="PORTAL",
              reason=f"OTP sent to {identifier} via {payload.method}",
              metadata={"method": payload.method})

    return VerifyStartResponse(
        message="Verification code sent",
        expires_in_minutes=settings.OTP_EXPIRE_MINUTES,
    )


@router.post("/verify/confirm", response_model=CustomerContextOut)
def verify_confirm(payload: VerifyConfirmRequest, db: Session = Depends(get_db)):
    identifier = payload.identifier.strip().lower()
    token_hash = hash_otp(payload.otp)

    verification = (
        db.query(VerificationToken)
        .filter(
            VerificationToken.identifier == identifier,
            VerificationToken.is_consumed.is_(False),
        )
        .order_by(VerificationToken.created_at.desc())
        .first()
    )

    if not verification:
        raise HTTPException(status_code=401, detail="No pending verification found")

    if verification.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Verification code has expired")

    if verification.attempt_count >= verification.max_attempts:
        raise HTTPException(status_code=401, detail="Maximum attempts exceeded")

    verification.attempt_count += 1

    if verification.token_hash != token_hash:
        db.commit()
        log_audit(db, "VERIFY_OTP_FAILED", actor_username="system", source_app="PORTAL",
                  reason=f"Failed OTP attempt for {identifier}",
                  metadata={"attempt": verification.attempt_count})
        raise HTTPException(status_code=401, detail="Invalid verification code")

    verification.is_consumed = True
    verification.consumed_at = datetime.now(timezone.utc)

    # Find or create customer
    email = identifier if "@" in identifier else None
    phone = identifier if "@" not in identifier else None
    external_id = derive_customer_id(email=email, phone=phone)

    customer = None
    if email:
        customer = db.query(Customer).filter(Customer.email_search == hmac_digest(email)).first()
    if not customer:
        customer = db.query(Customer).filter(Customer.external_id_search == hmac_digest(external_id)).first()
    if not customer:
        customer = Customer(
            external_id=external_id,
            external_id_search=hmac_digest(external_id),
            name=identifier.split("@")[0] if email else f"User-{identifier[-4:]}",
            email=email or "",
            email_search=hmac_digest(email) if email else None,
            phone=phone or "",
            status="ACTIVE",
            source_app="PORTAL",
        )
        db.add(customer)
        db.flush()

    # Mark as verified
    customer.identity_verified_at = datetime.now(timezone.utc)
    customer.verification_method = verification.verification_method

    db.commit()

    log_audit(db, "VERIFY_OTP_CONFIRMED", actor_username="system", source_app="PORTAL",
              reason=f"Identity verified for {identifier}",
              customer_id=customer.id,
              metadata={"method": verification.verification_method})

    token = create_context_token(customer.id, "PORTAL")
    return CustomerContextOut(
        context_token=token,
        context_id=0,
        customer_id=customer.external_id,
        name=customer.name,
        expires_in_minutes=settings.CONTEXT_TOKEN_EXPIRE_MINUTES,
        source_app="PORTAL",
        ui_url=f"/consent/context/{token}",
        request_id=None,
    )


@router.post("/verify/fiduciary", response_model=CustomerContextOut)
def verify_fiduciary(payload: FiduciaryAssertRequest, db: Session = Depends(get_db)):
    """Fiduciary-asserted handoff: accept a signed assertion from a tenant's authenticated system."""
    import json
    from jose import jwt, JWTError

    try:
        assertion_data = json.loads(payload.assertion)
    except json.JSONDecodeError:
        raise HTTPException(status_code=401, detail="Invalid assertion format")

    # Verify the assertion signature
    signing_key = settings.FIDUCIARY_SIGNING_KEY
    if not signing_key:
        raise HTTPException(status_code=500, detail="Fiduciary signing key not configured")

    try:
        decoded = jwt.decode(payload.assertion, signing_key, algorithms=["HS256"],
                             issuer=assertion_data.get("iss", ""),
                             audience=settings.JWT_AUDIENCE)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or tampered assertion")

    # Check freshness
    exp = decoded.get("exp")
    if exp and datetime.now(timezone.utc).timestamp() > exp:
        raise HTTPException(status_code=401, detail="Assertion has expired")

    email = decoded.get("email")
    name = decoded.get("name", "Fiduciary User")
    if not email:
        raise HTTPException(status_code=401, detail="Email required in assertion")

    external_id = derive_customer_id(email=email)
    customer = db.query(Customer).filter(Customer.email_search == hmac_digest(email)).first()
    if not customer:
        customer = Customer(
            external_id=external_id,
            external_id_search=hmac_digest(external_id),
            name=name,
            email=email,
            email_search=hmac_digest(email),
            status="ACTIVE",
            source_app="FIDUCIARY",
        )
        db.add(customer)
        db.flush()

    customer.identity_verified_at = datetime.now(timezone.utc)
    customer.verification_method = "FIDUCIARY_ASSERTED"
    db.commit()

    token = create_context_token(customer.id, "FIDUCIARY")
    return CustomerContextOut(
        context_token=token,
        context_id=0,
        customer_id=customer.external_id,
        name=customer.name,
        expires_in_minutes=settings.CONTEXT_TOKEN_EXPIRE_MINUTES,
        source_app="FIDUCIARY",
        ui_url=f"/consent/context/{token}",
        request_id=None,
    )
