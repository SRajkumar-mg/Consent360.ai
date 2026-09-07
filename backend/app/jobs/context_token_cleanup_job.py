import hashlib
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.entities import ConsentContext

TOKEN_HASH_PREFIX = "sha256:"


def hash_token(raw_token: str) -> str:
    """Deterministic, unsalted digest used to hash a consumed/expired context
    token at rest. Exported so callers that still need to find a context row
    by its original raw token (e.g. `GET /consent/context/status/{token}`)
    can compute the same digest and look up either form."""
    return TOKEN_HASH_PREFIX + hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def run(db: Session) -> dict:
    """Deactivate expired contexts, then hash the stored token of every
    inactive (consumed or expired) context row so a database dump does not
    expose a value that looks reusable.

    This changes what is stored in `ConsentContext.token` for any row this
    job touches: after it runs, a lookup by the original raw token will not
    find that row directly. Callers that accept a raw token from an external
    caller (the published SDK/API surface, not just internal code) and need
    to find a hashed row -- `GET /consent/context/status/{token}` is the one
    that matters today -- must also try `hash_token(raw_token)` themselves;
    see `app/api/routes/integration.py::context_status`. Active, still-valid
    contexts are left untouched by this job, but that only means *their*
    lookups are unaffected, not that raw-token lookups in general still work
    once a context has been consumed or has expired.
    """
    now = datetime.now(timezone.utc)
    expired = (
        db.query(ConsentContext)
        .filter(ConsentContext.expires_at < now, ConsentContext.is_active.is_(True))
        .all()
    )
    for ctx in expired:
        ctx.is_active = False
    db.flush()

    to_hash = (
        db.query(ConsentContext)
        .filter(ConsentContext.is_active.is_(False), ~ConsentContext.token.like(f"{TOKEN_HASH_PREFIX}%"))
        .all()
    )
    for ctx in to_hash:
        ctx.token = hash_token(ctx.token)
    db.commit()
    return {"deactivated": len(expired), "hashed": len(to_hash)}
