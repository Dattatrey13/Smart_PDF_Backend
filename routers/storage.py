"""Storage quota management endpoints — quota status, recent PDFs, bulk delete."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query

from auth.dependencies import get_current_user
from auth.user_service import get_user_profile
from services.storage_quota_service import (
    get_storage_status,
    get_recent_pdfs,
    delete_pdfs_bulk,
    recalculate_storage,
    update_last_opened,
)
from models.usage_schemas import (
    StorageQuotaResponse,
    RecentPdfsResponse,
    DeletePdfsRequest,
    DeletePdfsResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/storage", tags=["Storage"])


# ─── Storage Quota Status ───────────────────────────────────────────────────


@router.get("/quota", response_model=StorageQuotaResponse)
async def storage_quota(current_user: dict = Depends(get_current_user)):
    """
    Return the caller's current storage usage, limits, and warning level.
    The Flutter client uses this to display the storage indicator
    and trigger warning/critical dialogs.
    """
    uid = current_user.get("uid")
    profile = await get_user_profile(uid)
    plan = (profile or {}).get("subscription_plan", "free")

    status = await get_storage_status(uid, plan)
    return StorageQuotaResponse(**status)


# ─── Recent PDFs ─────────────────────────────────────────────────────────────


@router.get("/recent-pdfs", response_model=RecentPdfsResponse)
async def recent_pdfs(
    sort_by: str = Query("recent", regex="^(recent|largest|oldest)$"),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """
    Fetch the user's recent PDFs with sorting options.
    Used by the Recent Viewed PDFs management screen.
    """
    uid = current_user.get("uid")
    result = await get_recent_pdfs(uid, sort_by=sort_by, limit=limit)
    return RecentPdfsResponse(**result)


# ─── Bulk Delete ─────────────────────────────────────────────────────────────


@router.post("/delete-pdfs", response_model=DeletePdfsResponse)
async def delete_pdfs(
    request: DeletePdfsRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Delete multiple PDFs and reclaim storage.
    Updates storage quota immediately and returns the new usage.
    """
    uid = current_user.get("uid")

    if not request.pdf_ids:
        raise HTTPException(status_code=400, detail="No PDF IDs provided")

    result = await delete_pdfs_bulk(uid, request.pdf_ids)
    return DeletePdfsResponse(**result)


# ─── Mark PDF Opened ─────────────────────────────────────────────────────────


@router.post("/pdf-opened/{pdf_id}")
async def mark_pdf_opened(
    pdf_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Record that a user opened a PDF (updates last_opened timestamp)."""
    uid = current_user.get("uid")
    await update_last_opened(uid, pdf_id)
    return {"success": True}


# ─── Recalculate (Admin/Anti-bypass) ─────────────────────────────────────────


@router.post("/recalculate")
async def recalculate_storage_endpoint(
    current_user: dict = Depends(get_current_user),
):
    """
    Full storage recalculation from pdf_metadata.
    Can be called periodically or on-demand to fix any drift
    between the quota counter and actual stored files.
    """
    uid = current_user.get("uid")
    result = await recalculate_storage(uid)
    return {"success": True, **result}
