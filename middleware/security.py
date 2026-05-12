"""Security middleware: request size limiting, security headers, suspicious activity detection."""
import time
import logging
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from config import settings

logger = logging.getLogger(__name__)

# Track IPs with repeated auth failures (in-memory, use Redis in multi-instance)
_failed_auth_attempts: dict[str, list[float]] = defaultdict(list)
_blocked_ips: dict[str, float] = {}

# Constants
MAX_AUTH_FAILURES_PER_HOUR = 20
IP_BLOCK_DURATION_SECONDS = 3600  # 1 hour


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        # XSS protection
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # Referrer policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Content Security Policy (API-only, no inline scripts)
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        # Strict Transport Security (HTTPS only)
        if settings.ENVIRONMENT == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # Remove server header
        response.headers.pop("server", None)

        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests that exceed the maximum body size."""

    async def dispatch(self, request: Request, call_next) -> Response:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > settings.MAX_REQUEST_SIZE:
            return JSONResponse(
                status_code=413,
                content={"detail": "Request body too large"},
            )
        return await call_next(request)


class IPBlockMiddleware(BaseHTTPMiddleware):
    """Block IPs with too many failed authentication attempts."""

    async def dispatch(self, request: Request, call_next) -> Response:
        client_ip = _get_client_ip(request)

        # Check if IP is blocked
        if client_ip in _blocked_ips:
            blocked_until = _blocked_ips[client_ip]
            if time.time() < blocked_until:
                logger.warning(f"Blocked IP attempted access: {client_ip}")
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Access temporarily blocked due to suspicious activity"},
                )
            else:
                # Block expired, remove
                del _blocked_ips[client_ip]
                _failed_auth_attempts.pop(client_ip, None)

        response = await call_next(request)

        # Track auth failures
        if response.status_code == 401:
            _record_auth_failure(client_ip)

        return response


class GlobalRateLimitMiddleware(BaseHTTPMiddleware):
    """Simple per-IP rate limiting across all endpoints."""

    def __init__(self, app, requests_per_minute: int = None):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute or settings.GLOBAL_RATE_LIMIT_PER_MINUTE
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next) -> Response:
        client_ip = _get_client_ip(request)
        now = time.time()
        window_start = now - 60

        # Clean old entries and check count
        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if t > window_start
        ]

        if len(self._requests[client_ip]) >= self.requests_per_minute:
            logger.warning(f"Rate limit exceeded for IP: {client_ip}")
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please slow down."},
                headers={"Retry-After": "60"},
            )

        self._requests[client_ip].append(now)
        return await call_next(request)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting proxy headers."""
    # Trust X-Forwarded-For from known proxies (Render, Railway, etc.)
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # First IP in the chain is the original client
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _record_auth_failure(ip: str) -> None:
    """Record an auth failure and block IP if threshold exceeded."""
    now = time.time()
    one_hour_ago = now - 3600

    _failed_auth_attempts[ip] = [
        t for t in _failed_auth_attempts[ip] if t > one_hour_ago
    ]
    _failed_auth_attempts[ip].append(now)

    if len(_failed_auth_attempts[ip]) >= MAX_AUTH_FAILURES_PER_HOUR:
        _blocked_ips[ip] = now + IP_BLOCK_DURATION_SECONDS
        logger.warning(
            f"IP {ip} blocked for {IP_BLOCK_DURATION_SECONDS}s "
            f"after {MAX_AUTH_FAILURES_PER_HOUR} auth failures"
        )


def record_suspicious_activity(ip: str, reason: str) -> None:
    """Manually record suspicious activity from an IP (callable from routes)."""
    logger.warning(f"Suspicious activity from {ip}: {reason}")
    # Add multiple failures at once to accelerate blocking
    now = time.time()
    _failed_auth_attempts[ip].extend([now] * 5)
    if len(_failed_auth_attempts[ip]) >= MAX_AUTH_FAILURES_PER_HOUR:
        _blocked_ips[ip] = now + IP_BLOCK_DURATION_SECONDS
