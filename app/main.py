"""FastAPI application factory with middleware and route registration."""

import logging
import time
from collections import defaultdict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.routes import upload, ask, summary, search

settings = get_settings()

# ── logging ─────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Smart PDF Backend",
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── CORS ────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── rate limiter (simple in-memory, per IP) ─────────────
    _rate_store: dict = defaultdict(list)

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        window = settings.RATE_LIMIT_WINDOW_SECONDS

        # Prune old entries
        _rate_store[client_ip] = [
            t for t in _rate_store[client_ip] if now - t < window
        ]

        if len(_rate_store[client_ip]) >= settings.RATE_LIMIT_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
            )

        _rate_store[client_ip].append(now)
        return await call_next(request)

    # ── request logging ─────────────────────────────────────
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        elapsed = time.time() - start
        logger.info(
            "%s %s → %d  (%.3fs)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
        )
        return response

    # ── global exception handler ────────────────────────────
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error("Unhandled error on %s: %s", request.url.path, exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    # ── routes ──────────────────────────────────────────────
    app.include_router(upload.router, tags=["Upload"])
    app.include_router(ask.router, tags=["Ask"])
    app.include_router(summary.router, tags=["Summary"])
    app.include_router(search.router, tags=["Search"])

    @app.get("/")
    async def root():
        return {"status": "ok", "message": "Smart PDF Backend v2 running"}

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    return app


app = create_app()
