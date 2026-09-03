import logging
import time
import uuid
from typing import Optional

from fastapi import Request


class _CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(record, "request_id", None) or get_request_id()
        return True


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
logging.getLogger().addFilter(_CorrelationFilter())


def make_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def generate_request_id() -> str:
    return uuid.uuid4().hex[:24]


class RequestContext:
    """Holds correlation id for the current request via a thread-local."""

    _request_id: str = "startup"


def get_request_id() -> str:
    return RequestContext._request_id


def set_request_id(request_id: str) -> None:
    RequestContext._request_id = request_id


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
