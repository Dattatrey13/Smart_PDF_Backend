"""Authentication dependency for FastAPI - verifies Firebase token on protected routes."""
import logging
from fastapi import Depends, HTTPException, Header, status

from auth.firebase_admin_init import verify_firebase_token
from auth.rate_limiter import check_ai_cooldown, check_daily_ai_limit
from auth.user_service import get_user_ai_limits

logger = logging.getLogger(__name__)


async def get_current_user(authorization: str = Header(...)) -> dict:
    """
    FastAPI dependency: Extract and verify Firebase ID token from Authorization header.
    Returns decoded token claims (uid, email, etc.)
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Expected: Bearer <token>",
        )

    token = authorization[7:]  # Remove "Bearer " prefix

    try:
        decoded_token = verify_firebase_token(token)
        return decoded_token
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )


async def require_ai_access(current_user: dict = Depends(get_current_user)) -> dict:
    """
    FastAPI dependency: Verify user has AI access (valid account + within limits).
    Chain after get_current_user.
    Returns user info with access details.
    """
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user token",
        )

    # Check account status and limits
    limits = await get_user_ai_limits(uid)

    if limits.get("account_status") != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been suspended. Please contact support.",
        )

    # Check cooldown
    if not check_ai_cooldown(uid):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Please wait a few seconds between AI requests.",
        )

    # Check daily limit
    daily_limit = limits.get("daily_limit", 20)
    limit_check = await check_daily_ai_limit(uid, daily_limit)

    if not limit_check["allowed"]:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily AI limit reached ({limit_check['used']}/{limit_check['limit']}). "
                   f"Resets at {limit_check['reset_at']}. Upgrade to Premium for more.",
        )

    return {
        **current_user,
        "ai_limits": limit_check,
        "subscription_plan": limits.get("subscription_plan", "free"),
    }
