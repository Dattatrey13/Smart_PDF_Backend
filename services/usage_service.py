"""
Centralised AI-usage tracking service backed by Firestore.

Responsibilities:
  • Read / write the  ai_usage/{uid}  document
  • Automatic UTC midnight reset of daily counters
  • Hourly sliding-window counters for uploads and OTP
  • Token tracking  (input / output / total)
  • Page-processing tracking
  • Concurrent-job counter (acquire / release)
  • Cooldown validation
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from google.cloud.firestore_v1 import Increment

from auth.firebase_admin_init import get_firestore_client
from models.tier_limits import get_tier_limits, TierLimits

logger = logging.getLogger(__name__)

_COLLECTION = "ai_usage"


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _today_iso() -> str:
    return _now_utc().date().isoformat()


def _next_midnight_utc() -> datetime:
    return _now_utc().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def _one_hour_ago() -> datetime:
    return _now_utc() - timedelta(hours=1)


def _doc_ref(uid: str):
    return get_firestore_client().collection(_COLLECTION).document(uid)


# ─── Read / Bootstrap ────────────────────────────────────────────────────────


async def get_usage(uid: str, plan: str = "free") -> dict:
    """
    Return the current usage document, creating or daily-resetting as needed.
    The returned dict is a plain Python dict (not a Pydantic model) so callers
    can pass it around cheaply.
    """
    ref = _doc_ref(uid)
    doc = ref.get()
    today = _today_iso()
    tier = get_tier_limits(plan)

    if not doc.exists:
        data = _new_usage_doc(uid, plan, tier)
        ref.set(data)
        return data

    data = doc.to_dict()

    # ── Automatic daily reset ────────────────────────────────────────────
    if data.get("last_reset_date") != today:
        reset_fields = {
            "used_today": 0,
            "token_usage_today": 0,
            "input_tokens_today": 0,
            "output_tokens_today": 0,
            "processed_pages_today": 0,
            "concurrent_jobs": 0,
            "last_reset_date": today,
            "reset_at": _next_midnight_utc().isoformat(),
            # Re-sync plan limits in case they changed
            "ai_daily_limit": tier.ai_requests_per_day,
            "token_limit": tier.token_budget_per_day,
            "subscription_plan": plan,
        }
        ref.update(reset_fields)
        data.update(reset_fields)

    return data


def _new_usage_doc(uid: str, plan: str, tier: TierLimits) -> dict:
    return {
        "uid": uid,
        "subscription_plan": plan,
        "used_today": 0,
        "token_usage_today": 0,
        "input_tokens_today": 0,
        "output_tokens_today": 0,
        "processed_pages_today": 0,
        "last_request_at": None,
        "upload_count_hour": 0,
        "upload_hour_start": None,
        "otp_requests_hour": 0,
        "otp_hour_start": None,
        "concurrent_jobs": 0,
        "ai_daily_limit": tier.ai_requests_per_day,
        "token_limit": tier.token_budget_per_day,
        "reset_at": _next_midnight_utc().isoformat(),
        "last_reset_date": _today_iso(),
        "total_requests": 0,
        "blocked_until": None,
    }


# ─── Daily AI Request Check ─────────────────────────────────────────────────


async def check_ai_request_allowed(uid: str, plan: str = "free") -> dict:
    """
    Return ``{"allowed": True/False, ...}`` based on daily request count.
    """
    usage = await get_usage(uid, plan)
    tier = get_tier_limits(plan)

    # Blocked?
    blocked_until = usage.get("blocked_until")
    if blocked_until:
        if isinstance(blocked_until, str):
            blocked_dt = datetime.fromisoformat(blocked_until)
        else:
            blocked_dt = blocked_until
        if _now_utc() < blocked_dt:
            return {
                "allowed": False,
                "used": usage.get("used_today", 0),
                "limit": tier.ai_requests_per_day,
                "reset_at": _next_midnight_utc().isoformat(),
                "error_code": "ACCOUNT_BLOCKED",
            }

    used = usage.get("used_today", 0)
    allowed = used < tier.ai_requests_per_day

    return {
        "allowed": allowed,
        "used": used,
        "limit": tier.ai_requests_per_day,
        "reset_at": _next_midnight_utc().isoformat(),
        "error_code": None if allowed else "DAILY_AI_LIMIT",
    }


# ─── Token Budget Check ─────────────────────────────────────────────────────


async def check_token_budget(uid: str, plan: str = "free") -> dict:
    usage = await get_usage(uid, plan)
    tier = get_tier_limits(plan)
    used = usage.get("token_usage_today", 0)
    allowed = used < tier.token_budget_per_day

    return {
        "allowed": allowed,
        "used": used,
        "limit": tier.token_budget_per_day,
        "error_code": None if allowed else "DAILY_TOKEN_LIMIT",
    }


# ─── Page Processing Check ──────────────────────────────────────────────────


async def check_page_budget(uid: str, pages_requested: int, plan: str = "free") -> dict:
    usage = await get_usage(uid, plan)
    tier = get_tier_limits(plan)
    used = usage.get("processed_pages_today", 0)
    allowed = (used + pages_requested) <= tier.ai_processable_pages_per_day

    return {
        "allowed": allowed,
        "used": used,
        "limit": tier.ai_processable_pages_per_day,
        "error_code": None if allowed else "DAILY_PAGE_LIMIT",
    }


# ─── Cooldown ────────────────────────────────────────────────────────────────


async def check_cooldown(uid: str, plan: str = "free") -> dict:
    """
    Returns ``{"allowed": True, ...}`` if enough time has passed since the
    last AI request.  Uses Firestore ``last_request_at`` for accuracy across
    instances (not in-memory).
    """
    usage = await get_usage(uid, plan)
    tier = get_tier_limits(plan)
    last = usage.get("last_request_at")

    if last is None:
        return {"allowed": True, "retry_after": 0}

    if isinstance(last, str):
        last = datetime.fromisoformat(last)

    elapsed = (_now_utc() - last).total_seconds()
    remaining = tier.ai_cooldown_seconds - elapsed

    if remaining > 0:
        return {
            "allowed": False,
            "retry_after": int(remaining) + 1,
            "error_code": "COOLDOWN",
        }

    return {"allowed": True, "retry_after": 0}


# ─── Upload Rate ─────────────────────────────────────────────────────────────


async def check_upload_rate(uid: str, plan: str = "free") -> dict:
    usage = await get_usage(uid, plan)
    tier = get_tier_limits(plan)

    hour_start = usage.get("upload_hour_start")
    count = usage.get("upload_count_hour", 0)

    # Reset hourly window
    if hour_start is None or (isinstance(hour_start, str) and datetime.fromisoformat(hour_start) < _one_hour_ago()):
        count = 0

    allowed = count < tier.upload_rate_per_hour
    return {
        "allowed": allowed,
        "used": count,
        "limit": tier.upload_rate_per_hour,
        "error_code": None if allowed else "UPLOAD_RATE_LIMIT",
    }


# ─── OTP Rate ────────────────────────────────────────────────────────────────


async def check_otp_rate(uid: str, plan: str = "free") -> dict:
    usage = await get_usage(uid, plan)
    tier = get_tier_limits(plan)

    hour_start = usage.get("otp_hour_start")
    count = usage.get("otp_requests_hour", 0)

    if hour_start is None or (isinstance(hour_start, str) and datetime.fromisoformat(hour_start) < _one_hour_ago()):
        count = 0

    allowed = count < tier.otp_requests_per_hour
    return {
        "allowed": allowed,
        "used": count,
        "limit": tier.otp_requests_per_hour,
        "error_code": None if allowed else "OTP_RATE_LIMIT",
    }


# ─── Concurrent Jobs ────────────────────────────────────────────────────────


async def acquire_job_slot(uid: str, plan: str = "free") -> dict:
    """Try to acquire a concurrent-job slot.  Returns ``{"acquired": bool}``."""
    usage = await get_usage(uid, plan)
    tier = get_tier_limits(plan)
    current = usage.get("concurrent_jobs", 0)

    if current >= tier.concurrent_ai_jobs:
        return {
            "acquired": False,
            "current": current,
            "limit": tier.concurrent_ai_jobs,
            "error_code": "CONCURRENT_LIMIT",
        }

    _doc_ref(uid).update({"concurrent_jobs": Increment(1)})
    return {"acquired": True, "current": current + 1, "limit": tier.concurrent_ai_jobs}


async def release_job_slot(uid: str) -> None:
    """Release a concurrent-job slot (call in ``finally`` block)."""
    ref = _doc_ref(uid)
    doc = ref.get()
    if doc.exists:
        current = doc.to_dict().get("concurrent_jobs", 0)
        ref.update({"concurrent_jobs": max(0, current - 1)})


# ─── Increment After Success ────────────────────────────────────────────────


async def record_ai_request(
    uid: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    pages_processed: int = 0,
) -> None:
    """
    Call AFTER a successful AI call to increment all counters atomically.
    """
    total_tokens = input_tokens + output_tokens
    ref = _doc_ref(uid)

    ref.update({
        "used_today": Increment(1),
        "total_requests": Increment(1),
        "token_usage_today": Increment(total_tokens),
        "input_tokens_today": Increment(input_tokens),
        "output_tokens_today": Increment(output_tokens),
        "processed_pages_today": Increment(pages_processed),
        "last_request_at": _now_utc(),
        "last_reset_date": _today_iso(),
    })

    # Keep users collection in sync (backward compat)
    db = get_firestore_client()
    db.collection("users").document(uid).update({
        "ai_used_today": Increment(1),
        "last_reset_date": _today_iso(),
    })

    logger.debug(
        f"[USAGE] uid={uid[:8]} +1 request, tokens={total_tokens}, pages={pages_processed}"
    )


async def record_upload(uid: str) -> None:
    """Increment upload counter inside the hourly sliding window."""
    ref = _doc_ref(uid)
    doc = ref.get()
    data = doc.to_dict() if doc.exists else {}

    hour_start = data.get("upload_hour_start")
    if hour_start is None or (isinstance(hour_start, str) and datetime.fromisoformat(hour_start) < _one_hour_ago()):
        ref.update({
            "upload_count_hour": 1,
            "upload_hour_start": _now_utc().isoformat(),
        })
    else:
        ref.update({"upload_count_hour": Increment(1)})


async def record_otp_request(uid: str) -> None:
    """Increment OTP counter inside the hourly sliding window."""
    ref = _doc_ref(uid)
    doc = ref.get()
    data = doc.to_dict() if doc.exists else {}

    hour_start = data.get("otp_hour_start")
    if hour_start is None or (isinstance(hour_start, str) and datetime.fromisoformat(hour_start) < _one_hour_ago()):
        ref.update({
            "otp_requests_hour": 1,
            "otp_hour_start": _now_utc().isoformat(),
        })
    else:
        ref.update({"otp_requests_hour": Increment(1)})


# ─── Usage Status (for client display) ──────────────────────────────────────


async def get_usage_status(uid: str, plan: str = "free") -> dict:
    """Build a usage-status dict suitable for the Flutter client."""
    usage = await get_usage(uid, plan)
    tier = get_tier_limits(plan)

    return {
        "plan": plan,
        "ai_requests_used": usage.get("used_today", 0),
        "ai_requests_limit": tier.ai_requests_per_day,
        "tokens_used": usage.get("token_usage_today", 0),
        "tokens_limit": tier.token_budget_per_day,
        "pages_processed": usage.get("processed_pages_today", 0),
        "pages_limit": tier.ai_processable_pages_per_day,
        "uploads_this_hour": usage.get("upload_count_hour", 0),
        "uploads_limit": tier.upload_rate_per_hour,
        "concurrent_jobs": usage.get("concurrent_jobs", 0),
        "concurrent_limit": tier.concurrent_ai_jobs,
        "cooldown_seconds": tier.ai_cooldown_seconds,
        "reset_at": usage.get("reset_at"),
        "blocked_until": usage.get("blocked_until"),
    }


# ─── Block User ──────────────────────────────────────────────────────────────


async def block_user(uid: str, hours: int = 24) -> None:
    blocked_until = _now_utc() + timedelta(hours=hours)
    _doc_ref(uid).update({"blocked_until": blocked_until.isoformat()})
    logger.warning(f"User {uid} blocked until {blocked_until}")
