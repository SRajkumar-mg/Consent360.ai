import contextvars
import json
import logging
import logging.handlers
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import Request

# T-03: "RequestContext._request_id is a process-global class attribute" -
# every concurrent request sharing one worker process stomped on the same
# mutable class attribute, so a log line from request A could carry request
# B's id under any real concurrency (asyncio request handling, or a thread
# pool). A contextvars.ContextVar is per-async-task (and per-thread), so
# concurrent requests each see their own value with no locking needed - this
# is the stdlib-recommended replacement for exactly this pattern.
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="startup")


def get_request_id() -> str:
    return _request_id_var.get()


def set_request_id(request_id: str) -> None:
    _request_id_var.set(request_id)


# A Filter attached to `logging.getLogger()` (the root logger) only runs for
# records logged directly on the root logger. Records from any *named*
# logger (`logging.getLogger("app")`, `logging.getLogger("app.jobs")`, ...)
# propagate straight to root's handlers without ever going through root's
# own `.filter()` call, so they never get a `request_id` attribute and a
# formatter referencing it would raise `KeyError: 'request_id'` inside the
# handler, which logging swallows into a noisy "--- Logging error ---"
# traceback on stderr. A record factory runs for every LogRecord regardless
# of which logger created it or which handler processes it, so it is the
# smallest fix that covers named loggers (and any handler added later)
# without touching `CorrelationIdMiddleware` or the `X-Request-ID` contract.
_old_record_factory = logging.getLogRecordFactory()


def _record_factory(*args, **kwargs) -> logging.LogRecord:
    record = _old_record_factory(*args, **kwargs)
    record.request_id = get_request_id()
    return record


logging.setLogRecordFactory(_record_factory)

# Every standard LogRecord attribute, so JsonFormatter can tell them apart
# from caller-supplied `extra={...}` fields (which it also emits).
_STANDARD_RECORD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()) | {
    "message", "asctime", "request_id",
}


class JsonFormatter(logging.Formatter):
    """Structured JSON log line: timestamp, level, logger, message,
    request_id (from the contextvar above), plus any extra fields the
    caller attached via ``logger.info(..., extra={...})`` (e.g. the access
    log / alerting code in this module and app/core/alerting.py).

    R3-04: "Structured JSON logs with request id via contextvars."
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS or key in payload:
                continue
            try:
                json.dumps(value)
            except TypeError:
                value = repr(value)
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _configure_logging() -> None:
    """Root logger setup, called once at import time. ``LOG_FORMAT=json``
    (the default) emits JsonFormatter lines to stdout - what a log
    shipper/SIEM ingests. Setting ``LOG_FILE`` additionally writes the same
    structured lines to a midnight-rotating file with ``LOG_RETENTION_DAYS``
    (default ~1 year) of backups, a minimal local stand-in for R3-04's
    "1-year retention in India" requirement: actually shipping these to an
    India-region log store/SIEM and enforcing that residency is an
    infrastructure decision outside this application's code.
    """
    from app.core.config import get_settings

    settings = get_settings()
    if settings.LOG_FORMAT == "json":
        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    handlers[0].setFormatter(formatter)
    if settings.LOG_FILE:
        file_handler = logging.handlers.TimedRotatingFileHandler(
            settings.LOG_FILE, when="midnight", backupCount=settings.LOG_RETENTION_DAYS, utc=True,
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    level = getattr(logging, str(settings.LOG_LEVEL).upper(), logging.INFO)
    logging.basicConfig(level=level, handlers=handlers)


_configure_logging()


def make_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def generate_request_id() -> str:
    return uuid.uuid4().hex[:24]


class CorrelationIdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        request_id = request.headers.get("X-Request-ID") or generate_request_id()
        set_request_id(request_id)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"X-Request-ID", request_id.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


class SecurityHeadersMiddleware:
    """Baseline transport/browser security headers on every response
    (R3-03/T-07: HTTPS/HSTS, CSP for all frontends).

    HSTS is sent unconditionally — it is inert on a plain-HTTP response
    (browsers only honour it once received over HTTPS) and every real
    deployment of this API sits behind a TLS-terminating reverse proxy, so
    the header must originate in the app rather than depend on the proxy
    remembering to add it. This API returns JSON everywhere except its own
    interactive docs, so the default CSP is default-deny; `/docs` and
    `/redoc` (Swagger UI / ReDoc, which load their own bundle from a CDN and
    inline-execute it) get a narrowly scoped exception instead of disabling
    CSP for the whole app.
    """

    _DOCS_CSP = (
        b"default-src 'self'; "
        b"img-src 'self' data: https://fastapi.tiangolo.com; "
        b"script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
        b"style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'"
    )
    _DEFAULT_CSP = b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'"

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        is_docs = path.startswith("/docs") or path.startswith("/redoc") or path.startswith("/openapi.json")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"strict-transport-security", b"max-age=63072000; includeSubDomains; preload"))
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"x-frame-options", b"DENY"))
                headers.append((b"referrer-policy", b"no-referrer"))
                headers.append((b"content-security-policy", self._DOCS_CSP if is_docs else self._DEFAULT_CSP))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


class RateLimiter:
    """Simple in-memory sliding window rate limiter (per IP + optional route key)."""

    def __init__(self, limit: int = 20, window_seconds: int = 60):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window_start = now - self.window
        hits = [t for t in self._hits.get(key, []) if t > window_start]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True

    def release(self, key: str) -> None:
        """Undo the most recent `allow(key)` hit, as if it had never been
        recorded. For callers that consume a slot before attempting a
        side-effecting action (e.g. sending an email) and want to give the
        slot back if that action fails, so a delivery failure doesn't cost
        part of the caller's retry budget. A no-op if `key` has no hits.
        """
        hits = self._hits.get(key)
        if hits:
            hits.pop()


class RedisRateLimiter:
    """Sliding-window rate limiter backed by a Redis sorted set (R3-04:
    "Redis-backed rate limiter"). Same ``allow(key) -> bool`` /
    ``release(key) -> None`` interface as :class:`RateLimiter`, so every
    existing call site (login_limiter.allow(...), etc.) is unaffected by
    which backend it got.

    Unlike the in-memory limiter, state here is shared across every worker
    process and survives an app restart - the actual gap T-03 names
    ("rate limiters are in-memory per process"). Each key's sorted set is
    given a TTL so Redis reclaims it on its own once the caller goes quiet;
    no separate cleanup job is needed.
    """

    def __init__(self, client, limit: int, window_seconds: int, namespace: str = "ratelimit"):
        self._client = client
        self.limit = limit
        self.window = window_seconds
        self._namespace = namespace

    def _redis_key(self, key: str) -> str:
        return f"{self._namespace}:{key}"

    def allow(self, key: str) -> bool:
        now = time.time()
        rkey = self._redis_key(key)
        pipe = self._client.pipeline()
        pipe.zremrangebyscore(rkey, 0, now - self.window)
        pipe.zcard(rkey)
        _, count = pipe.execute()
        if count >= self.limit:
            self._client.expire(rkey, self.window)
            return False
        member = f"{now}:{uuid.uuid4().hex}"
        pipe = self._client.pipeline()
        pipe.zadd(rkey, {member: now})
        pipe.expire(rkey, self.window)
        pipe.execute()
        return True

    def release(self, key: str) -> None:
        rkey = self._redis_key(key)
        latest = self._client.zrange(rkey, -1, -1)
        if latest:
            self._client.zrem(rkey, latest[0])


def build_rate_limiter(name: str, limit: int, window_seconds: int):
    """Redis-backed when ``REDIS_URL`` is set and reachable; otherwise the
    in-memory :class:`RateLimiter` (unchanged default - a dev/test process
    or a single-worker deployment with no Redis keeps working exactly as
    before). A configured-but-unreachable Redis fails open to in-memory
    rather than breaking every rate-limited route.
    """
    from app.core.config import get_settings

    settings = get_settings()
    if settings.REDIS_URL:
        try:
            import redis

            client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5)
            client.ping()
            return RedisRateLimiter(client, limit, window_seconds, namespace=name)
        except Exception:
            logging.getLogger("consent360.ratelimit").warning(
                "REDIS_URL is configured but unreachable for limiter %r - falling back to "
                "the in-memory limiter (not shared across workers/restarts).", name,
            )
    return RateLimiter(limit=limit, window_seconds=window_seconds)


login_limiter = build_rate_limiter("login", limit=10, window_seconds=60)
context_limiter = build_rate_limiter("ctx", limit=60, window_seconds=60)
# The /public/* routes (privacy-contact, rights, purposes) are unauthenticated
# by design - anyone embedding a notice needs them - so, like login and
# context above, they need their own limiter rather than none at all.
public_limiter = build_rate_limiter("public", limit=60, window_seconds=60)


def mask_identifier(value: Optional[str]) -> Optional[str]:
    """Return a masked version of an identifier for display (e.g. ra***@gmail.com)."""
    if not value:
        return None
    if "@" in value:
        local, _, domain = value.partition("@")
        if len(local) <= 2:
            return "***@" + domain
        return local[:2] + "***@" + domain
    if len(value) <= 4:
        return "***"
    return value[:2] + "***" + value[-2:]
