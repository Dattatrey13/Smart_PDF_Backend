"""
Subscription tier definitions and plan-based limit constants.

All rate limits, quotas, and feature flags for free and premium tiers
are defined here as the single source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict


class SubscriptionPlan(str, Enum):
    FREE = "free"
    PREMIUM = "premium"


@dataclass(frozen=True, slots=True)
class TierLimits:
    """Immutable limit set for a subscription tier."""

    # AI
    ai_requests_per_day: int
    token_budget_per_day: int
    ai_cooldown_seconds: int
    concurrent_ai_jobs: int
    ai_processable_pages_per_day: int

    # PDF
    pdf_upload_size_bytes: int
    pdf_max_pages: int

    # Upload
    upload_rate_per_hour: int

    # OTP
    otp_requests_per_hour: int
    otp_cooldown_seconds: int = 60
    otp_max_failed_attempts: int = 5

    # IP
    global_ip_rate_per_minute: int = 60

    # Storage
    storage_limit_mb: int = 5120          # total cloud storage in MB
    max_single_upload_mb: int = 20        # per-file limit in MB

    # Features
    priority_ai_queue: bool = False
    permanent_pdf_retention: bool = False
    cloud_sync_enabled: bool = True


# ─── Tier Definitions ────────────────────────────────────────────────────────

FREE_TIER = TierLimits(
    ai_requests_per_day=20,
    token_budget_per_day=300_000,
    ai_cooldown_seconds=8,
    concurrent_ai_jobs=1,
    ai_processable_pages_per_day=50,
    pdf_upload_size_bytes=20 * 1024 * 1024,       # 20 MB
    pdf_max_pages=150,
    upload_rate_per_hour=10,
    otp_requests_per_hour=5,
    otp_cooldown_seconds=60,
    otp_max_failed_attempts=5,
    global_ip_rate_per_minute=15,
    storage_limit_mb=5120,                            # 5 GB
    max_single_upload_mb=20,
    priority_ai_queue=False,
    permanent_pdf_retention=False,
    cloud_sync_enabled=True,
)

PREMIUM_TIER = TierLimits(
    ai_requests_per_day=200,
    token_budget_per_day=5_000_000,
    ai_cooldown_seconds=2,
    concurrent_ai_jobs=3,
    ai_processable_pages_per_day=500,
    pdf_upload_size_bytes=100 * 1024 * 1024,      # 100 MB
    pdf_max_pages=1000,
    upload_rate_per_hour=50,
    otp_requests_per_hour=10,
    otp_cooldown_seconds=60,
    otp_max_failed_attempts=5,
    global_ip_rate_per_minute=60,
    storage_limit_mb=51200,                           # 50 GB
    max_single_upload_mb=100,
    priority_ai_queue=True,
    permanent_pdf_retention=True,
    cloud_sync_enabled=True,
)

# ─── Lookup ──────────────────────────────────────────────────────────────────

PLAN_TIERS: Dict[str, TierLimits] = {
    SubscriptionPlan.FREE: FREE_TIER,
    SubscriptionPlan.PREMIUM: PREMIUM_TIER,
}


def get_tier_limits(plan: str) -> TierLimits:
    """Return the TierLimits for the given plan name, defaulting to FREE."""
    return PLAN_TIERS.get(plan, FREE_TIER)
