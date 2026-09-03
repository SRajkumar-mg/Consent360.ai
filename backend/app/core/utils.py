import logging
import time
import uuid
from contextvars import ContextVar
from typing import Optional

try:
    import redis
    _redis_client = redis.Redis(host="redis", port=6379, db=0, decode_responses=True)
    _redis_available = _redis_client.ping()
except Exception:
    _redis_available = False
    _redis_client = None

from fastapi import Request

_request_id_var: ContextVar[str] = ContextVar("request_id", default="startup")


class _CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id") or not record.request_id:
            record.request_id = get_request_id()
        return True


class _SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "request_id") or not record.request_id:
            record.request_id = get_request_id()
        return super().format(record)


_handler = logging.StreamHandler()
_handler.setFormatter(_SafeFormatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"))
_handler.addFilter(_CorrelationFilter())

root = logging.getLogger()
root.setLevel(logging.INFO)
if not root.handlers:
    root.addHandler(_handler)
else:
    root.handlers[0].setFormatter(_SafeFormatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"))
    root.handlers[0].addFilter(_CorrelationFilter())


def make_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.filters:
        logger.addFilter(_CorrelationFilter())
    return logger


def generate_request_id() -> str:
    return uuid.uuid4().hex[:24]


class RequestContext:
    """Holds correlation id for the current request via contextvars (thread-safe).

    Uses the module-level _request_id_var ContextVar so that each request gets
    its own isolated id even under concurrent requests in the same process.
    """
    pass  # State is managed via _request_id_var ContextVar


def get_request_id() -> str:
    return _request_id_var.get()


def set_request_id(request_id: str) -> None:
    _request_id_var.set(request_id)


class RateLimiter:
    """Rate limiter backed by Redis for cross-worker consistency, with in-memory
    fallback when Redis is unavailable."""

    def __init__(self, limit: int = 20, window_seconds: int = 60, redis_client=None):
        self.limit = limit
        self.window = window_seconds
        self.redis = redis_client or _redis_client
        self._fallback = not self.redis or not _redis_available
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        if self._fallback or self.redis is None:
            # In-memory fallback (per-process, not shared across workers)
            return self._allow_in_memory(key)
        return self._allow_redis(key)

    def _allow_in_memory(self, key: str) -> bool:
        """In-memory fallback (original behaviour)."""
        now = time.monotonic()
        window_start = now - self.window
        hits_key = f"rl:{key}"
        hits = [t for t in self._hits.get(hits_key, []) if t > window_start]
        if len(hits) >= self.limit:
            self._hits[hits_key] = hits
            return False
        hits.append(now)
        self._hits[hits_key] = hits
        return True

    def _allow_redis(self, key: str) -> bool:
        """Redis-backed rate limit shared across workers."""
        pipe = self.redis.pipeline()
        now = int(time.time())
        window_start = now - self.window
        hits_key = f"rl:{key}"

        # Remove timestamps outside the window
        pipe.zremrangebyscore(hits_key, 0, window_start)
        # Count current timestamps
        pipe.zcard(hits_key)
        # Add current timestamp
        pipe.zadd(hits_key, {str(now): now})
        # Set expiry
        pipe.expireat(hits_key, now + self.window)
        # Execute and check limit
        results = pipe.execute()
        count = results[1]  # zcard result

        if count >= self.limit:
            return False

        # Add the current timestamp
        self.redis.zadd(hits_key, {str(now): now})
        self.redis.expireat(hits_key, now + self.window)
        return True


login_limiter = RateLimiter(limit=10, window_seconds=60)
context_limiter = RateLimiter(limit=60, window_seconds=60)


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


class CorrelationIdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        rid = request.headers.get("X-Request-ID") or generate_request_id()
        set_request_id(rid)

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                headers[b"x-request-id"] = rid.encode()
                message["headers"] = list(headers.items())
            return await send(message)

        return await self.app(scope, receive, send_with_header)


def __getattr__(name):
    if name == "log_audit":
        from app.services.audit import log_audit
        return log_audit
    raise AttributeError(name)