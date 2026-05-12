"""Rate limiting middleware and utilities."""
import time
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from fastapi import Request, HTTPException

from auth.config import (
    MAX_OTP_REQUESTS_PER_HOUR,
    AI_REQUEST_COOLDOWN_SECONDS,
)

logger = logging.getLogger(__name__)

# In-memory rate limit stores (use Redis in production for multi-instance)
_otp_requests: dict[str, list[float]] = defaultdict(list)
_ai_requests: dict[str, float] = {}


def check_otp_rate_limit(email: str) -> bool:
    """
    Check if email has exceeded OTP request rate limit.
    Returns True if allowed, False if rate limited.
    """
    now = time.time()
    one_hour_ago = now - 3600

    # Clean old entries
    _otp_requests[email] = [t for t in _otp_requests[email] if t > one_hour_ago]

    if len(_otp_requests[email]) >= MAX_OTP_REQUESTS_PER_HOUR:
        return False

    _otp_requests[email].append(now)
    return True


def check_ai_cooldown(uid: str) -> bool:
    """
    Check if user is within AI request cooldown period.
    Returns True if allowed, False if in cooldown.
    """
    now = time.time()
    last_request = _ai_requests.get(uid, 0)

    if now - last_request < AI_REQUEST_COOLDOWN_SECONDS:
        return False

    _ai_requests[uid] = now
    return True


async def check_daily_ai_limit(uid: str, daily_limit: int) -> dict:
    """
    Check if user has exceeded daily AI usage limit.
    Returns: {"allowed": bool, "used": int, "limit": int, "reset_at": str}
    """
    from auth.firestore_service import check_ai_limit

    result = await check_ai_limit(uid, daily_limit)

    # Calculate reset time (midnight UTC)
    tomorrow = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)

    return {
        "allowed": result["allowed"],
        "used": result["used"],
        "limit": result["limit"],
        "reset_at": tomorrow.isoformat(),
        "blocked_until": result.get("blocked_until"),
    }


async def increment_ai_usage(uid: str, token_count: int = 0):
    """Increment the daily AI usage counter for a user."""
    from auth.firestore_service import increment_ai_usage as _increment
    await _increment(uid, token_count)
