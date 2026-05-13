"""
Pydantic schemas for the usage-protection system.

These schemas are used in API responses, Firestore document mapping,
and internal data transfer between services.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field


# ─── Firestore: ai_usage/{uid} ──────────────────────────────────────────────


class AiUsageDoc(BaseModel):
    """
    Mirrors the Firestore document at  ai_usage/{uid}.

    Example Firestore document:
    {
        "uid": "abc123",
        "subscription_plan": "free",
        "used_today": 5,
        "token_usage_today": 42000,
        "input_tokens_today": 30000,
        "output_tokens_today": 12000,
        "processed_pages_today": 12,
        "last_request_at": "2026-05-13T10:32:00Z",
        "upload_count_hour": 2,
        "upload_hour_start": "2026-05-13T10:00:00Z",
        "otp_requests_hour": 1,
        "otp_hour_start": "2026-05-13T10:00:00Z",
        "concurrent_jobs": 0,
        "ai_daily_limit": 20,
        "token_limit": 300000,
        "reset_at": "2026-05-14T00:00:00Z",
        "last_reset_date": "2026-05-13",
        "total_requests": 150,
        "blocked_until": null
    }
    """
    uid: str
    subscription_plan: str = "free"

    # Daily AI counters (reset at UTC midnight)
    used_today: int = 0
    token_usage_today: int = 0
    input_tokens_today: int = 0
    output_tokens_today: int = 0
    processed_pages_today: int = 0
    last_request_at: Optional[datetime] = None

    # Hourly counters (sliding window)
    upload_count_hour: int = 0
    upload_hour_start: Optional[datetime] = None
    otp_requests_hour: int = 0
    otp_hour_start: Optional[datetime] = None

    # Concurrency
    concurrent_jobs: int = 0

    # Plan-derived limits (cached for quick reads)
    ai_daily_limit: int = 20
    token_limit: int = 300_000

    # Reset bookkeeping
    reset_at: Optional[datetime] = None
    last_reset_date: Optional[str] = None

    # Lifetime counters
    total_requests: int = 0

    # Abuse
    blocked_until: Optional[datetime] = None


# ─── Token Tracking ──────────────────────────────────────────────────────────


class TokenUsage(BaseModel):
    """Token counts returned by the LLM after a generation call."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


# ─── API Response Schemas ────────────────────────────────────────────────────


class UsageStatusResponse(BaseModel):
    """Returned to the client so the Flutter app can display usage info."""
    plan: str = "free"
    ai_requests_used: int = 0
    ai_requests_limit: int = 20
    tokens_used: int = 0
    tokens_limit: int = 300_000
    pages_processed: int = 0
    pages_limit: int = 50
    uploads_this_hour: int = 0
    uploads_limit: int = 10
    concurrent_jobs: int = 0
    concurrent_limit: int = 1
    cooldown_seconds: int = 8
    reset_at: Optional[str] = None
    blocked_until: Optional[str] = None


class LimitExceededDetail(BaseModel):
    """Structured 429 response body."""
    detail: str
    error_code: str
    current: int = 0
    limit: int = 0
    reset_at: Optional[str] = None
    retry_after: Optional[int] = None
