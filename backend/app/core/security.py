from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid
import hashlib
import hmac as _hmac

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

AUTH_CONTEXT = "consent-auth"
INTEGRATION_CONTEXT = "integration"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def _make_jti() -> str:
    return uuid.uuid4().hex


def create_access_token(user_id: int, username: str, role: str, token_version: int = 1) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "type": "access",
        "ctx": AUTH_CONTEXT,
        "exp": expires,
        "iat": datetime.now(timezone.utc),
        "jti": _make_jti(),
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "token_version": token_version,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: int, token_version: int = 1) -> str:
    expires = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "ctx": AUTH_CONTEXT,
        "exp": expires,
        "iat": datetime.now(timezone.utc),
        "jti": _make_jti(),
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "token_version": token_version,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_org_refresh_token(user_id: int) -> str:
    expires = datetime.now(timezone.utc) + timedelta(days=settings.ORG_REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "ctx": AUTH_CONTEXT,
        "exp": expires,
        "iat": datetime.now(timezone.utc),
        "jti": _make_jti(),
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_context_token(customer_id: int, source_app: str, request_id: Optional[str] = None) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.CONTEXT_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(customer_id),
        "source_app": source_app,
        "type": "consent-context",
        "ctx": INTEGRATION_CONTEXT,
        "exp": expires,
        "iat": datetime.now(timezone.utc),
        "request_id": request_id,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER,
            audience=settings.JWT_AUDIENCE,
        )
    except JWTError:
        return None


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hash of a raw API key for storage."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def verify_api_key_hash(raw_key: str, key_hash: str) -> bool:
    """Constant-time comparison of raw key against stored hash."""
    computed = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return _hmac.compare_digest(computed, key_hash)


def generate_api_key() -> str:
    """Generate a secure random API key."""
    return f"c36k_{uuid.uuid4().hex}"


def hash_otp(otp: str) -> str:
    """Hash an OTP for storage."""
    return hashlib.sha256(otp.encode("utf-8")).hexdigest()


def generate_otp(length: int = 6) -> str:
    """Generate a numeric OTP."""
    import secrets
    return "".join(secrets.choice("0123456789") for _ in range(length))


def sign_payload(payload: dict) -> str:
    """Sign a payload with HMAC-SHA256 for webhook delivery."""
    import json
    message = json.dumps(payload, sort_keys=True, default=str)
    secret = settings.FIELD_ENCRYPTION_KEY.encode("utf-8") if settings.FIELD_ENCRYPTION_KEY else b"default"
    return _hmac.new(secret, message.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_payload_signature(payload: dict, signature: str) -> bool:
    """Verify HMAC-SHA256 signature on a payload."""
    import json
    message = json.dumps(payload, sort_keys=True, default=str)
    secret = settings.FIELD_ENCRYPTION_KEY.encode("utf-8") if settings.FIELD_ENCRYPTION_KEY else b"default"
    expected = _hmac.new(secret, message.encode("utf-8"), hashlib.sha256).hexdigest()
    return _hmac.compare_digest(expected, signature)
