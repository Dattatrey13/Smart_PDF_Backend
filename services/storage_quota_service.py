"""
Storage Quota Service — Tracks and enforces per-user cloud storage limits.

Firestore collection:  storage_quota/{uid}
Each document tracks used storage, limits, and PDF counts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from auth.firebase_admin_init import get_firestore_client
from models.tier_limits import get_tier_limits

logger = logging.getLogger(__name__)

# ─── Warning Thresholds (MB) ────────────────────────────────────────────────
LOW_STORAGE_THRESHOLD_MB = 1024       # 1 GB remaining → warning
CRITICAL_STORAGE_THRESHOLD_MB = 10    # 10 MB remaining → block uploads


# ─── Quota Document CRUD ────────────────────────────────────────────────────


async def get_storage_quota(uid: str, plan: str = "free") -> dict:
    """
    Fetch the storage quota document for a user.
    Creates with tier defaults if it doesn't exist.
    """
    db = get_firestore_client()
    ref = db.collection("storage_quota").document(uid)
    doc = ref.get()

    tier = get_tier_limits(plan)

    if not doc.exists:
        quota = {
            "uid": uid,
            "used_storage_mb": 0.0,
            "storage_limit_mb": tier.storage_limit_mb,
            "total_pdf_count": 0,
            "last_storage_update": datetime.now(timezone.utc),
            "subscription_plan": plan,
        }
        ref.set(quota)
        return quota

    data = doc.to_dict()

    # Sync storage limit if plan changed
    if data.get("storage_limit_mb") != tier.storage_limit_mb:
        ref.update({
            "storage_limit_mb": tier.storage_limit_mb,
            "subscription_plan": plan,
        })
        data["storage_limit_mb"] = tier.storage_limit_mb
        data["subscription_plan"] = plan

    return data


async def get_storage_status(uid: str, plan: str = "free") -> dict:
    """
    Build a StorageQuotaResponse-compatible dict with computed fields.
    """
    quota = await get_storage_quota(uid, plan)

    used = quota.get("used_storage_mb", 0.0)
    limit = quota.get("storage_limit_mb", 5120)
    remaining = max(limit - used, 0.0)
    percentage = min((used / limit) * 100, 100.0) if limit > 0 else 0.0

    # Determine warning level
    if remaining <= CRITICAL_STORAGE_THRESHOLD_MB:
        warning_level = "critical"
    elif remaining <= LOW_STORAGE_THRESHOLD_MB:
        warning_level = "low"
    else:
        warning_level = "normal"

    return {
        "used_storage_mb": round(used, 2),
        "storage_limit_mb": limit,
        "remaining_storage_mb": round(remaining, 2),
        "used_percentage": round(percentage, 1),
        "total_pdf_count": quota.get("total_pdf_count", 0),
        "plan": plan,
        "warning_level": warning_level,
    }


# ─── Quota Validation ───────────────────────────────────────────────────────


async def check_storage_quota(uid: str, file_size_bytes: int, plan: str = "free") -> dict:
    """
    Validate whether a file upload is allowed within the user's storage quota.

    Returns:
        {
            "allowed": bool,
            "error_code": str | None,
            "used_mb": float,
            "limit_mb": int,
            "remaining_mb": float,
            "file_size_mb": float,
            "warning_level": str,
        }
    """
    quota = await get_storage_quota(uid, plan)
    used = quota.get("used_storage_mb", 0.0)
    limit = quota.get("storage_limit_mb", 5120)
    remaining = max(limit - used, 0.0)
    file_size_mb = file_size_bytes / (1024 * 1024)

    # Determine warning level
    remaining_after = remaining - file_size_mb
    if remaining_after <= CRITICAL_STORAGE_THRESHOLD_MB:
        warning_level = "critical"
    elif remaining_after <= LOW_STORAGE_THRESHOLD_MB:
        warning_level = "low"
    else:
        warning_level = "normal"

    if file_size_mb > remaining:
        return {
            "allowed": False,
            "error_code": "STORAGE_QUOTA_EXCEEDED",
            "used_mb": round(used, 2),
            "limit_mb": limit,
            "remaining_mb": round(remaining, 2),
            "file_size_mb": round(file_size_mb, 2),
            "warning_level": "critical",
        }

    return {
        "allowed": True,
        "error_code": None,
        "used_mb": round(used, 2),
        "limit_mb": limit,
        "remaining_mb": round(remaining, 2),
        "file_size_mb": round(file_size_mb, 2),
        "warning_level": warning_level,
    }


# ─── Storage Accounting ─────────────────────────────────────────────────────


async def record_storage_upload(uid: str, file_size_bytes: int) -> dict:
    """
    Atomically increment used storage and PDF count after a successful upload.
    Returns updated quota summary.
    """
    from google.cloud.firestore_v1 import Increment

    db = get_firestore_client()
    ref = db.collection("storage_quota").document(uid)

    file_size_mb = file_size_bytes / (1024 * 1024)

    ref.update({
        "used_storage_mb": Increment(round(file_size_mb, 4)),
        "total_pdf_count": Increment(1),
        "last_storage_update": datetime.now(timezone.utc),
    })

    logger.info(
        f"Storage recorded: uid={uid[:8]}, +{file_size_mb:.2f} MB, "
        f"file_size={file_size_bytes} bytes"
    )

    return {"added_mb": round(file_size_mb, 2)}


async def record_storage_deletion(uid: str, file_size_bytes: int, count: int = 1) -> dict:
    """
    Atomically decrement used storage and PDF count after deletion.
    Ensures used_storage_mb never goes below 0.
    """
    from google.cloud.firestore_v1 import Increment

    db = get_firestore_client()
    ref = db.collection("storage_quota").document(uid)

    file_size_mb = file_size_bytes / (1024 * 1024)

    # Read current to prevent going negative
    doc = ref.get()
    if doc.exists:
        current_used = doc.to_dict().get("used_storage_mb", 0.0)
        actual_decrement = min(file_size_mb, current_used)
        current_count = doc.to_dict().get("total_pdf_count", 0)
        actual_count_dec = min(count, current_count)
    else:
        actual_decrement = 0.0
        actual_count_dec = 0

    if actual_decrement > 0 or actual_count_dec > 0:
        ref.update({
            "used_storage_mb": Increment(-round(actual_decrement, 4)),
            "total_pdf_count": Increment(-actual_count_dec),
            "last_storage_update": datetime.now(timezone.utc),
        })

    logger.info(
        f"Storage freed: uid={uid[:8]}, -{actual_decrement:.2f} MB, "
        f"-{actual_count_dec} PDFs"
    )

    return {"freed_mb": round(actual_decrement, 2), "deleted_count": actual_count_dec}


async def recalculate_storage(uid: str) -> dict:
    """
    Full recalculation of storage usage from pdf_metadata collection.
    Use as a reconciliation/anti-bypass mechanism.
    """
    db = get_firestore_client()

    docs = (
        db.collection("pdf_metadata")
        .where("uid", "==", uid)
        .stream()
    )

    total_bytes = 0
    total_count = 0
    for doc in docs:
        data = doc.to_dict()
        total_bytes += data.get("file_size", 0)
        total_count += 1

    total_mb = total_bytes / (1024 * 1024)

    ref = db.collection("storage_quota").document(uid)
    ref.set({
        "uid": uid,
        "used_storage_mb": round(total_mb, 4),
        "total_pdf_count": total_count,
        "last_storage_update": datetime.now(timezone.utc),
    }, merge=True)

    logger.info(
        f"Storage recalculated: uid={uid[:8]}, "
        f"total={total_mb:.2f} MB, count={total_count}"
    )

    return {
        "used_storage_mb": round(total_mb, 2),
        "total_pdf_count": total_count,
    }


# ─── Recent PDFs ─────────────────────────────────────────────────────────────


async def get_recent_pdfs(
    uid: str,
    sort_by: str = "recent",
    limit: int = 50,
) -> dict:
    """
    Fetch user's PDF metadata from Firestore with sorting options.

    sort_by: "recent" (newest first), "largest" (biggest first), "oldest" (oldest first)
    """
    db = get_firestore_client()

    # Base query
    if sort_by == "oldest":
        query = (
            db.collection("pdf_metadata")
            .where("uid", "==", uid)
            .order_by("upload_time")
            .limit(limit)
        )
    else:
        # Default newest first; for "largest" we sort in-memory after fetch
        query = (
            db.collection("pdf_metadata")
            .where("uid", "==", uid)
            .order_by("upload_time", direction="DESCENDING")
            .limit(limit)
        )

    docs = query.stream()
    pdfs = []
    total_size = 0

    for doc in docs:
        data = doc.to_dict()
        file_size = data.get("file_size", 0)
        total_size += file_size

        upload_time = data.get("upload_time")
        last_opened = data.get("last_opened")

        pdfs.append({
            "id": doc.id,
            "file_name": data.get("file_name", "Unknown"),
            "file_size_bytes": file_size,
            "file_size_mb": round(file_size / (1024 * 1024), 2),
            "page_count": data.get("page_count", 0),
            "upload_time": upload_time.isoformat() if upload_time else None,
            "last_opened": last_opened.isoformat() if last_opened else None,
            "storage_url": data.get("storage_url"),
            "thumbnail_url": data.get("thumbnail_url"),
        })

    # In-memory sort for "largest"
    if sort_by == "largest":
        pdfs.sort(key=lambda p: p["file_size_bytes"], reverse=True)

    return {
        "pdfs": pdfs,
        "total_count": len(pdfs),
        "total_size_mb": round(total_size / (1024 * 1024), 2),
    }


async def delete_pdfs_bulk(uid: str, pdf_ids: list[str]) -> dict:
    """
    Delete multiple PDFs and update storage quota atomically.
    Returns freed storage and updated quota.
    """
    db = get_firestore_client()

    total_freed_bytes = 0
    deleted_count = 0

    for pdf_id in pdf_ids:
        doc_ref = db.collection("pdf_metadata").document(pdf_id)
        doc = doc_ref.get()

        if not doc.exists:
            continue

        data = doc.to_dict()

        # Verify ownership
        if data.get("uid") != uid:
            logger.warning(f"Unauthorized delete attempt: uid={uid[:8]}, pdf={pdf_id}")
            continue

        file_size = data.get("file_size", 0)
        total_freed_bytes += file_size
        deleted_count += 1

        # Delete from Firestore
        doc_ref.delete()

        # Delete from Firebase Storage if URL exists
        storage_url = data.get("storage_url")
        if storage_url:
            try:
                _delete_from_cloud_storage(storage_url)
            except Exception as e:
                logger.warning(f"Failed to delete cloud file for pdf={pdf_id}: {e}")

    # Update storage quota
    if total_freed_bytes > 0:
        await record_storage_deletion(uid, total_freed_bytes, deleted_count)

    # Fetch updated quota
    status = await get_storage_status(uid)

    freed_mb = round(total_freed_bytes / (1024 * 1024), 2)
    return {
        "deleted_count": deleted_count,
        "freed_storage_mb": freed_mb,
        "new_used_storage_mb": status["used_storage_mb"],
        "new_remaining_storage_mb": status["remaining_storage_mb"],
    }


async def update_last_opened(uid: str, pdf_id: str) -> None:
    """Update the last_opened timestamp for a PDF."""
    db = get_firestore_client()
    doc_ref = db.collection("pdf_metadata").document(pdf_id)
    doc = doc_ref.get()

    if not doc.exists:
        return

    data = doc.to_dict()
    if data.get("uid") != uid:
        return

    doc_ref.update({"last_opened": datetime.now(timezone.utc)})


def _delete_from_cloud_storage(storage_url: str) -> None:
    """Best-effort delete a file from Firebase/Cloud Storage."""
    try:
        from google.cloud import storage as gcs
        import re

        # Extract bucket and path from gs:// or https:// URL
        if storage_url.startswith("gs://"):
            parts = storage_url.replace("gs://", "").split("/", 1)
            bucket_name = parts[0]
            blob_path = parts[1] if len(parts) > 1 else ""
        elif "firebasestorage.googleapis.com" in storage_url:
            match = re.search(r"/b/([^/]+)/o/(.+?)(?:\?|$)", storage_url)
            if not match:
                return
            bucket_name = match.group(1)
            blob_path = match.group(2).replace("%2F", "/")
        else:
            return

        client = gcs.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        blob.delete()
        logger.info(f"Deleted cloud file: {blob_path}")
    except Exception as e:
        logger.warning(f"Cloud storage deletion failed: {e}")
