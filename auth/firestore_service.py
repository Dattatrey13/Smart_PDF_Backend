"""Firestore service for AI usage, PDF metadata, chat history, and subscriptions."""
import logging
from datetime import datetime, timezone, timedelta

from auth.firebase_admin_init import get_firestore_client
from auth.config import MAX_AI_REQUESTS_FREE_DAILY

logger = logging.getLogger(__name__)


# ─── AI Usage ────────────────────────────────────────────────────────────────


async def get_ai_usage(uid: str) -> dict:
    """Get AI usage record for a user. Creates one if it doesn't exist."""
    db = get_firestore_client()
    usage_ref = db.collection("ai_usage").document(uid)
    usage_doc = usage_ref.get()

    today = datetime.now(timezone.utc).date().isoformat()

    if not usage_doc.exists:
        usage_data = {
            "uid": uid,
            "used_today": 0,
            "total_requests": 0,
            "token_usage": 0,
            "last_request_at": None,
            "last_reset_date": today,
            "blocked_until": None,
        }
        usage_ref.set(usage_data)
        return usage_data

    data = usage_doc.to_dict()

    # Auto-reset daily count if new day
    if data.get("last_reset_date") != today:
        usage_ref.update({
            "used_today": 0,
            "last_reset_date": today,
        })
        data["used_today"] = 0
        data["last_reset_date"] = today

    return data


async def increment_ai_usage(uid: str, token_count: int = 0) -> dict:
    """Increment AI usage counters after a successful AI request."""
    db = get_firestore_client()
    usage_ref = db.collection("ai_usage").document(uid)

    now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    # Ensure document exists
    usage_doc = usage_ref.get()
    if not usage_doc.exists:
        await get_ai_usage(uid)

    from google.cloud.firestore_v1 import Increment
    usage_ref.update({
        "used_today": Increment(1),
        "total_requests": Increment(1),
        "token_usage": Increment(token_count),
        "last_request_at": now,
        "last_reset_date": today,
    })

    # Also update the users collection for backward compatibility
    user_ref = db.collection("users").document(uid)
    user_ref.update({
        "ai_used_today": Increment(1),
        "last_reset_date": today,
    })

    return {"success": True}


async def check_ai_limit(uid: str, daily_limit: int) -> dict:
    """
    Check if user is within AI usage limits.
    Returns: {"allowed": bool, "used": int, "limit": int, "blocked_until": str|None}
    """
    usage = await get_ai_usage(uid)

    # Check if user is blocked
    blocked_until = usage.get("blocked_until")
    if blocked_until:
        if isinstance(blocked_until, str):
            blocked_dt = datetime.fromisoformat(blocked_until)
        else:
            blocked_dt = blocked_until
        if datetime.now(timezone.utc) < blocked_dt:
            return {
                "allowed": False,
                "used": usage.get("used_today", 0),
                "limit": daily_limit,
                "blocked_until": blocked_dt.isoformat(),
                "reason": "account_blocked",
            }

    used_today = usage.get("used_today", 0)
    allowed = used_today < daily_limit

    return {
        "allowed": allowed,
        "used": used_today,
        "limit": daily_limit,
        "blocked_until": None,
        "reason": None if allowed else "daily_limit_exceeded",
    }


async def block_user_ai(uid: str, hours: int = 24) -> None:
    """Block a user from AI access for a specified duration (abuse prevention)."""
    db = get_firestore_client()
    blocked_until = datetime.now(timezone.utc) + timedelta(hours=hours)
    db.collection("ai_usage").document(uid).update({
        "blocked_until": blocked_until.isoformat(),
    })
    logger.warning(f"User {uid} blocked from AI until {blocked_until}")


# ─── PDF Metadata ────────────────────────────────────────────────────────────


async def create_pdf_metadata(
    uid: str,
    file_name: str,
    file_size: int,
    page_count: int = 0,
    storage_url: str = None,
) -> str:
    """Create a PDF metadata record. Returns the document ID."""
    db = get_firestore_client()
    doc_ref = db.collection("pdf_metadata").document()

    metadata = {
        "uid": uid,
        "file_name": file_name,
        "file_size": file_size,
        "page_count": page_count,
        "upload_time": datetime.now(timezone.utc),
        "storage_url": storage_url,
        "processing_status": "processing",
        "summary_generated": False,
    }
    doc_ref.set(metadata)
    return doc_ref.id


async def update_pdf_processing_status(
    pdf_id: str, status: str, summary_generated: bool = False
) -> None:
    """Update PDF processing status (processing, completed, failed)."""
    db = get_firestore_client()
    db.collection("pdf_metadata").document(pdf_id).update({
        "processing_status": status,
        "summary_generated": summary_generated,
    })


async def get_user_pdf_metadata(uid: str, limit: int = 50) -> list:
    """Get all PDF metadata for a user, newest first."""
    db = get_firestore_client()
    docs = (
        db.collection("pdf_metadata")
        .where("uid", "==", uid)
        .order_by("upload_time", direction="DESCENDING")
        .limit(limit)
        .stream()
    )
    results = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        results.append(data)
    return results


# ─── Chat History ────────────────────────────────────────────────────────────


async def save_chat_entry(
    uid: str, pdf_id: str, question: str, answer: str
) -> str:
    """Save a chat Q&A entry. Returns the document ID."""
    db = get_firestore_client()
    doc_ref = db.collection("chat_history").document()

    entry = {
        "uid": uid,
        "pdf_id": pdf_id,
        "question": question,
        "answer": answer,
        "timestamp": datetime.now(timezone.utc),
    }
    doc_ref.set(entry)
    return doc_ref.id


async def get_chat_history(uid: str, pdf_id: str, limit: int = 50) -> list:
    """Get chat history for a user's PDF."""
    db = get_firestore_client()
    docs = (
        db.collection("chat_history")
        .where("uid", "==", uid)
        .where("pdf_id", "==", pdf_id)
        .order_by("timestamp")
        .limit(limit)
        .stream()
    )
    results = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        results.append(data)
    return results


# ─── Subscriptions ───────────────────────────────────────────────────────────


async def get_subscription(uid: str) -> dict | None:
    """Get user's subscription data."""
    db = get_firestore_client()
    doc = db.collection("subscriptions").document(uid).get()
    if not doc.exists:
        return None
    return doc.to_dict()


async def create_or_update_subscription(
    uid: str,
    plan: str,
    billing_status: str = "active",
    expiry_date: datetime = None,
    transaction_id: str = None,
) -> None:
    """Create or update a user's subscription."""
    db = get_firestore_client()
    sub_ref = db.collection("subscriptions").document(uid)

    sub_data = {
        "uid": uid,
        "current_plan": plan,
        "billing_status": billing_status,
        "updated_at": datetime.now(timezone.utc),
    }
    if expiry_date:
        sub_data["expiry_date"] = expiry_date
    if transaction_id:
        sub_data["transaction_id"] = transaction_id

    sub_ref.set(sub_data, merge=True)

    # Also update the users collection
    user_ref = db.collection("users").document(uid)
    user_ref.update({"subscription_plan": plan})

    # Update AI limits based on plan
    limits = _get_plan_limits(plan)
    user_ref.update({"ai_daily_limit": limits["daily_limit"]})


def _get_plan_limits(plan: str) -> dict:
    """Get AI limits based on subscription plan."""
    plans = {
        "free": {"daily_limit": MAX_AI_REQUESTS_FREE_DAILY},
        "basic": {"daily_limit": 50},
        "premium": {"daily_limit": 200},
        "enterprise": {"daily_limit": 1000},
    }
    return plans.get(plan, plans["free"])


# ─── App Settings ────────────────────────────────────────────────────────────


async def get_app_settings() -> dict:
    """Get global app settings."""
    db = get_firestore_client()
    doc = db.collection("app_settings").document("global").get()
    if not doc.exists:
        # Return defaults
        return {
            "maintenance_mode": False,
            "force_update": False,
            "ai_enabled": True,
            "latest_version": "1.0.0",
        }
    return doc.to_dict()


async def update_app_settings(settings: dict) -> None:
    """Update global app settings (admin only)."""
    db = get_firestore_client()
    db.collection("app_settings").document("global").set(settings, merge=True)
