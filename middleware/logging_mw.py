"""Request/response logging middleware with request ID tracking."""
import time
import uuid
import logging
import json
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from config import settings

logger = logging.getLogger("api.access")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Log every request with:
    - Unique request ID (X-Request-ID header)
    - Method, path, status code
    - Response time
    - Client IP
    - User agent
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Generate or extract request ID
        request_id = request.headers.get("x-request-id", str(uuid.uuid4())[:8])
        start_time = time.time()

        # Store request_id in state for downstream use
        request.state.request_id = request_id

        try:
            response = await call_next(request)
        except Exception as exc:
            # Log unhandled exceptions
            duration_ms = (time.time() - start_time) * 1000
            _log_request(request, 500, duration_ms, request_id, error=str(exc))
            raise

        duration_ms = (time.time() - start_time) * 1000

        # Add request ID to response headers
        response.headers["X-Request-ID"] = request_id

        # Log the request (skip health checks to reduce noise)
        if request.url.path not in ("/health", "/healthz", "/"):
            _log_request(request, response.status_code, duration_ms, request_id)
        elif response.status_code >= 400:
            # Always log errors, even on health endpoints
            _log_request(request, response.status_code, duration_ms, request_id)

        return response


def _log_request(
    request: Request,
    status_code: int,
    duration_ms: float,
    request_id: str,
    error: str = None,
) -> None:
    """Emit a structured log entry for the request."""
    client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not client_ip:
        client_ip = request.client.host if request.client else "unknown"

    log_data = {
        "request_id": request_id,
        "method": request.method,
        "path": str(request.url.path),
        "status": status_code,
        "duration_ms": round(duration_ms, 2),
        "client_ip": client_ip,
        "user_agent": request.headers.get("user-agent", "")[:100],
    }

    if error:
        log_data["error"] = error

    # Choose log level based on status code
    if status_code >= 500:
        if settings.LOG_FORMAT == "json":
            logger.error(json.dumps(log_data))
        else:
            logger.error(
                f"[{request_id}] {request.method} {request.url.path} → {status_code} "
                f"({duration_ms:.0f}ms) IP={client_ip} ERROR={error}"
            )
    elif status_code >= 400:
        if settings.LOG_FORMAT == "json":
            logger.warning(json.dumps(log_data))
        else:
            logger.warning(
                f"[{request_id}] {request.method} {request.url.path} → {status_code} "
                f"({duration_ms:.0f}ms) IP={client_ip}"
            )
    else:
        if settings.LOG_FORMAT == "json":
            logger.info(json.dumps(log_data))
        else:
            logger.info(
                f"[{request_id}] {request.method} {request.url.path} → {status_code} "
                f"({duration_ms:.0f}ms)"
            )


def setup_logging() -> None:
    """Configure structured logging for the application."""
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    if settings.LOG_FORMAT == "json":
        formatter = logging.Formatter(
            '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":%(message)s}'
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)
    logging.getLogger("firebase_admin").setLevel(logging.WARNING)
    logging.getLogger("grpc").setLevel(logging.WARNING)
