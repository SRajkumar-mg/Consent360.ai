"""JTI-based token revocation (R3-02/H-02: "token revocation list (jti)
honoured on logout and deactivation").

Deactivation is honoured WITHOUT this module: `app.api.deps.get_current_user`
re-checks `user.is_active` against the database on every request, so
disabling a staff account takes effect on their very next request
regardless of how long their already-issued access token has left to live -
no revocation-list entry is needed for that half of the requirement.

Logout is the actual gap this closes: previously it only wrote an audit
event, and the presented token stayed valid until its natural expiry (up to
ACCESS_TOKEN_EXPIRE_MINUTES / REFRESH_TOKEN_EXPIRE_DAYS later). Every staff
access/refresh token now carries a `jti` (app/core/security.py);
`POST /auth/logout` revokes it here, and `get_current_user` / `/auth/refresh`
reject any token whose `jti` is listed.

Storage:
  - Redis-backed (via the same REDIS_URL as app/core/utils.py's rate
    limiter) when configured and reachable: shared across every worker
    process and durable across an app restart - genuinely durable
    revocation for a real multi-worker deployment. Each entry's TTL is set
    to the token's own remaining lifetime, so Redis reclaims it
    automatically once the token would have expired anyway.
  - An in-process set otherwise: correct for a single dev/test process
    (which is what this environment runs), but forgotten on restart and
    not shared across multiple worker processes. A `users.token_version`
    column (bumped on logout/deactivation, embedded as a token claim and
    compared on every request) would close that residual gap without
    needing Redis at all, but entities.py is outside this lane's file
    scope - see the report for the exact column this would add.
"""
from __future__ import annotations

import time

_in_memory_revoked: dict[str, float] = {}  # jti -> expiry (epoch seconds)

_client = None
_client_checked = False


def _redis_client():
    global _client, _client_checked
    if _client_checked:
        return _client
    _client_checked = True
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.REDIS_URL:
        return None
    try:
        import redis

        client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5)
        client.ping()
        _client = client
    except Exception:
        _client = None
    return _client


def revoke(jti: str | None, *, ttl_seconds: int) -> None:
    if not jti or ttl_seconds <= 0:
        return
    client = _redis_client()
    if client is not None:
        try:
            client.setex(f"revoked-jti:{jti}", ttl_seconds, "1")
            return
        except Exception:
            pass  # fall through to in-memory so logout still takes effect
    _prune()
    _in_memory_revoked[jti] = time.time() + ttl_seconds


def is_revoked(jti: str | None) -> bool:
    if not jti:
        return False
    client = _redis_client()
    if client is not None:
        try:
            return bool(client.exists(f"revoked-jti:{jti}"))
        except Exception:
            pass
    _prune()
    return jti in _in_memory_revoked


def _prune() -> None:
    now = time.time()
    for k in [k for k, exp in _in_memory_revoked.items() if exp <= now]:
        _in_memory_revoked.pop(k, None)


def _reset_for_tests() -> None:
    global _client, _client_checked
    _client = None
    _client_checked = False
    _in_memory_revoked.clear()
