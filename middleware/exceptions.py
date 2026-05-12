"""Global exception handlers for the FastAPI application."""
import logging
import traceback
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from config import settings

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    """Register all global exception handlers on the app."""

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """Handle HTTP exceptions with consistent format."""
        request_id = getattr(request.state, "request_id", "unknown")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "request_id": request_id,
            },
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Handle Pydantic validation errors with user-friendly messages."""
        request_id = getattr(request.state, "request_id", "unknown")
        errors = exc.errors()

        # Simplify error messages for the client
        messages = []
        for error in errors:
            loc = " → ".join(str(l) for l in error.get("loc", []) if l != "body")
            msg = error.get("msg", "Invalid value")
            messages.append(f"{loc}: {msg}" if loc else msg)

        return JSONResponse(
            status_code=422,
            content={
                "detail": "; ".join(messages) if messages else "Validation error",
                "error_code": "validation_error",
                "request_id": request_id,
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        """
        Catch-all for unhandled exceptions.
        - Log full traceback for debugging
        - Return safe generic message to client (never leak internals)
        """
        request_id = getattr(request.state, "request_id", "unknown")

        logger.error(
            f"Unhandled exception [{request_id}]: {type(exc).__name__}: {exc}\n"
            f"{traceback.format_exc()}"
        )

        # In development, include the error type for debugging
        detail = "An internal error occurred. Please try again."
        if settings.DEBUG:
            detail = f"[{type(exc).__name__}] {str(exc)}"

        return JSONResponse(
            status_code=500,
            content={
                "detail": detail,
                "error_code": "internal_error",
                "request_id": request_id,
            },
        )
