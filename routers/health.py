"""Health check, status, and admin endpoints."""
import time
import logging
from fastapi import APIRouter, HTTPException, Header

from config import settings
from services.cache import ai_response_cache
from services.background import task_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health & Admin"])

# App start time (set during startup)
_start_time: float = time.time()
_doc_store = None


def init_health_router(doc_store: dict) -> None:
    """Initialize with shared doc store reference."""
    global _doc_store, _start_time
    _doc_store = doc_store
    _start_time = time.time()


# ─── Public Health Checks ────────────────────────────────────────────────────


@router.get("/health")
async def health_check():
    """Basic health check — used by load balancers and monitoring."""
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
    }


@router.get("/healthz")
async def healthz():
    """Kubernetes-style liveness probe."""
    return {"status": "ok"}


@router.get("/")
async def root():
    """Root endpoint — basic service info."""
    return {
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "docs": "/docs",
    }


# ─── Status (requires admin key) ────────────────────────────────────────────


@router.get("/admin/status")
async def admin_status(x_admin_key: str = Header(None)):
    """
    Detailed service status. Requires ADMIN_API_KEY header.
    Returns memory usage, cache stats, task status.
    """
    _verify_admin_key(x_admin_key)

    uptime_seconds = time.time() - _start_time
    docs_in_memory = len(_doc_store) if _doc_store else 0

    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "uptime_seconds": round(uptime_seconds),
        "documents_in_memory": docs_in_memory,
        "cache": ai_response_cache.stats,
        "background_tasks": task_manager.stats,
    }


@router.post("/admin/cache/clear")
async def admin_clear_cache(x_admin_key: str = Header(None)):
    """Clear all response caches."""
    _verify_admin_key(x_admin_key)
    ai_response_cache.clear()
    logger.info("Admin cleared response cache")
    return {"success": True, "message": "Cache cleared"}


@router.post("/admin/cache/cleanup")
async def admin_cleanup_cache(x_admin_key: str = Header(None)):
    """Remove expired cache entries."""
    _verify_admin_key(x_admin_key)
    removed = ai_response_cache.cleanup_expired()
    return {"success": True, "removed": removed}


@router.post("/admin/tasks/cleanup")
async def admin_cleanup_tasks(x_admin_key: str = Header(None)):
    """Remove completed background tasks from memory."""
    _verify_admin_key(x_admin_key)
    removed = task_manager.cleanup_completed()
    return {"success": True, "removed": removed}


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _verify_admin_key(key: str | None) -> None:
    """Verify the admin API key."""
    if not settings.ADMIN_API_KEY:
        raise HTTPException(status_code=503, detail="Admin endpoints not configured")
    if key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")
