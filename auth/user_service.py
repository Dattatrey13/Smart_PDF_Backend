"""User management service for Firestore."""
import logging
from datetime import datetime, timezone, timedelta

from auth.firebase_admin_init import get_firestore_client
from auth.config import MAX_AI_REQUESTS_FREE_DAILY
from models.tier_limits import get_tier_limits

logger = logging.getLogger(__name__)


async def create_or_update_user(
    uid: str,
    email: str,
    auth_provider: str = "email_otp",
    name: str = None,
    photo_url: str = None,
) -> dict:
    """Create or update user profile in Firestore."""
    db = get_firestore_client()
    user_ref = db.collection("users").document(uid)
    user_doc = user_ref.get()

    now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    if user_doc.exists:
        # Update existing user
        update_data = {
            "last_login": now,
            "updated_at": now,
        }
        if name:
            update_data["name"] = name
        if photo_url:
            update_data["profile_image"] = photo_url

        user_ref.update(update_data)
        return user_doc.to_dict() | update_data
    else:
        # Create new user
        user_data = {
            "uid": uid,
            "email": email,
            "name": name or "",
            "profile_image": photo_url or "",
            "created_at": now,
            "last_login": now,
            "updated_at": now,
            "auth_provider": auth_provider,
            "subscription_plan": "free",
            "ai_daily_limit": MAX_AI_REQUESTS_FREE_DAILY,
            "ai_used_today": 0,
            "last_reset_date": today,
            "account_status": "active",
            "device_count": 1,
        }
        user_ref.set(user_data)

        # Also initialize ai_usage document with full schema
        tier = get_tier_limits("free")
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        ai_usage_ref = db.collection("ai_usage").document(uid)
        ai_usage_ref.set({
            "uid": uid,
            "subscription_plan": "free",
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
            "reset_at": tomorrow.isoformat(),
            "last_reset_date": today,
            "total_requests": 0,
            "blocked_until": None,
        })

        # Initialize subscription document
        sub_ref = db.collection("subscriptions").document(uid)
        sub_ref.set({
            "uid": uid,
            "current_plan": "free",
            "billing_status": "none",
            "expiry_date": None,
            "transaction_id": None,
        })

        return user_data


async def get_user_profile(uid: str) -> dict | None:
    """Get user profile from Firestore."""
    db = get_firestore_client()
    user_ref = db.collection("users").document(uid)
    user_doc = user_ref.get()

    if not user_doc.exists:
        return None
    return user_doc.to_dict()


async def get_user_ai_limits(uid: str) -> dict:
    """Get user's AI usage limits and current usage."""
    db = get_firestore_client()
    user_ref = db.collection("users").document(uid)
    user_doc = user_ref.get()

    if not user_doc.exists:
        return {
            "daily_limit": MAX_AI_REQUESTS_FREE_DAILY,
            "used_today": 0,
            "subscription_plan": "free",
            "account_status": "active",
        }

    data = user_doc.to_dict()
    return {
        "daily_limit": data.get("ai_daily_limit", MAX_AI_REQUESTS_FREE_DAILY),
        "used_today": data.get("ai_used_today", 0),
        "subscription_plan": data.get("subscription_plan", "free"),
        "account_status": data.get("account_status", "active"),
    }
