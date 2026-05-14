"""
Reusable FastAPI dependency functions for the usage-protection system.

Three main guards:
  • require_ai_access   — AI endpoints (/ai/*)
  • require_upload_access — PDF upload  (/pdf/upload)
  • require_otp_access   — OTP sending  (/auth/signup, /auth/resend-otp)

Each guard:
  1. Authenticates the caller (Firebase ID token)
  2. Resolves the subscription plan  →  TierLimits
  3. Runs the relevant quota / rate / cooldown checks
  4. Raises proper HTTP errors (429, 403, 413) with structured bodies
  5. Returns an enriched user dict that downstream handlers can use
"""
from __future__ import annotations

import logging
from fastapi import Depends, HTTPException, Request, status

from auth.firebase_admin_init import verify_firebase_token
from auth.dependencies import get_current_user        # reuse existing token extractor
from auth.user_service import get_user_profile
from models.tier_limits import get_tier_limits
from services.usage_service import (
    check_ai_request_allowed,
    check_token_budget,
    check_cooldown,
    check_upload_rate,
    check_otp_rate,
    acquire_job_slot,
    get_usage_status,
)
from services.storage_quota_service import check_storage_quota
from utils.app_check import verify_app_check

logger = logging.getLogger(__name__)


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _resolve_plan(uid: str) -> str:
    """Fetch the user's subscription plan from Firestore."""
    profile = await get_user_profile(uid)
    if profile is None:
        return "free"
    return profile.get("subscription_plan", "free")


def _raise_limit(
    detail: str,
    error_code: str,
    *,
    current: int = 0,
    limit: int = 0,
    reset_at: str | None = None,
    retry_after: int | None = None,
    http_status: int = status.HTTP_429_TOO_MANY_REQUESTS,
) -> None:
    """Raise a structured HTTP error for limit violations."""
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)

    raise HTTPException(
        status_code=http_status,
        detail={
            "detail": detail,
            "error_code": error_code,
            "current": current,
            "limit": limit,
            "reset_at": reset_at,
            "retry_after": retry_after,
        },
        headers=headers or None,
    )


# ─── require_ai_access ──────────────────────────────────────────────────────


async def require_ai_access(
    request: Request,
    current_user: dict = Depends(get_current_user),
    _app_check: dict | None = Depends(verify_app_check),
) -> dict:
    """
    Full AI-access gate:
      1. Firebase auth  ✔  (via get_current_user)
      2. App Check      ✔  (via verify_app_check)
      3. Account status
      4. Cooldown
      5. Daily AI limit
      6. Token budget
      7. Concurrent jobs  → acquires a slot
    Returns an enriched user dict.
    """
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    plan = await _resolve_plan(uid)
    tier = get_tier_limits(plan)

    # ── Account status ───────────────────────────────────────────────────
    profile = await get_user_profile(uid)
    if profile and profile.get("account_status") != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account suspended. Contact support.",
        )

    # ── Cooldown ─────────────────────────────────────────────────────────
    cd = await check_cooldown(uid, plan)
    if not cd["allowed"]:
        _raise_limit(
            f"Please wait {cd['retry_after']}s between AI requests.",
            "COOLDOWN",
            retry_after=cd["retry_after"],
        )

    # ── Daily AI limit ───────────────────────────────────────────────────
    ai = await check_ai_request_allowed(uid, plan)
    if not ai["allowed"]:
        _raise_limit(
            f"Daily AI limit reached ({ai['used']}/{ai['limit']}). "
            f"Resets at {ai['reset_at']}. Upgrade to Premium for more.",
            ai["error_code"],
            current=ai["used"],
            limit=ai["limit"],
            reset_at=ai["reset_at"],
        )

    # ── Token budget ─────────────────────────────────────────────────────
    tb = await check_token_budget(uid, plan)
    if not tb["allowed"]:
        _raise_limit(
            f"Daily token budget exhausted ({tb['used']:,}/{tb['limit']:,}).",
            tb["error_code"],
            current=tb["used"],
            limit=tb["limit"],
            reset_at=ai.get("reset_at"),
        )

    # ── Concurrent jobs ──────────────────────────────────────────────────
    slot = await acquire_job_slot(uid, plan)
    if not slot["acquired"]:
        _raise_limit(
            f"Too many concurrent AI jobs ({slot['current']}/{slot['limit']}). "
            "Please wait for the current request to finish.",
            slot["error_code"],
            current=slot["current"],
            limit=slot["limit"],
        )

    return {
        **current_user,
        "subscription_plan": plan,
        "tier": tier,
        "ai_check": ai,
    }


# ─── require_upload_access ───────────────────────────────────────────────────


async def require_upload_access(
    request: Request,
    current_user: dict = Depends(get_current_user),
    _app_check: dict | None = Depends(verify_app_check),
) -> dict:
    """
    Upload gate:
      1. Firebase auth  ✔
      2. App Check      ✔
      3. Upload rate  (per hour)
      4. Returns tier so the route can enforce size / page limits
    """
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    plan = await _resolve_plan(uid)
    tier = get_tier_limits(plan)

    # ── Upload rate ──────────────────────────────────────────────────────
    ur = await check_upload_rate(uid, plan)
    if not ur["allowed"]:
        _raise_limit(
            f"Upload limit reached ({ur['used']}/{ur['limit']} per hour).",
            ur["error_code"],
            current=ur["used"],
            limit=ur["limit"],
        )

    return {
        **current_user,
        "subscription_plan": plan,
        "tier": tier,
    }


# ─── require_otp_access ─────────────────────────────────────────────────────


async def require_otp_access(
    request: Request,
) -> dict:
    """
    OTP gate (pre-auth — user isn't signed-in yet):
      1. App Check
      2. IP-level rate limit is handled by GlobalRateLimitMiddleware
      3. Per-email OTP rate is checked inside the route using
         ``check_otp_rate`` — we can't bind to a uid here because
         the user might not exist yet.

    Returns client IP for logging.
    """
    # App Check — call manually since there's no user token
    from utils.app_check import verify_app_check as _verify
    await _verify(
        x_firebase_appcheck=request.headers.get("x-firebase-appcheck", ""),
    )

    client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not client_ip:
        client_ip = request.client.host if request.client else "unknown"

    return {"client_ip": client_ip}
