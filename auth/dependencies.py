"""Authentication dependency for FastAPI - verifies Firebase token on protected routes."""
import logging
from fastapi import Depends, HTTPException, Header, status

from auth.firebase_admin_init import verify_firebase_token

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
    Legacy AI-access dependency kept for backward-compatible legacy endpoints in app.py.
    New routes should use ``dependencies.guards.require_ai_access`` instead.
    """
    from dependencies.guards import require_ai_access as _new_guard
    # Delegate to the new guard (will re-resolve the user via Depends chain,
    # but for legacy callers that pass ``current_user`` directly, we just
    # return what we have enriched with the plan).
    from auth.user_service import get_user_ai_limits
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user token",
        )

    limits = await get_user_ai_limits(uid)

    if limits.get("account_status") != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been suspended. Please contact support.",
        )

    return {
        **current_user,
        "ai_limits": limits,
        "subscription_plan": limits.get("subscription_plan", "free"),
    }
