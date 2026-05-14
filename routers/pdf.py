"""PDF upload and processing endpoints."""
import uuid
import logging
from typing import Dict

import numpy as np
from fastapi import APIRouter, File, UploadFile, HTTPException, Depends, BackgroundTasks

from auth.dependencies import get_current_user
from dependencies.guards import require_upload_access
from auth.storage_service import validate_pdf_upload
from auth.firestore_service import create_pdf_metadata, update_pdf_processing_status
from services.pdf_processor import pdf_processor
from services.background import task_manager
from services.usage_service import record_upload, check_page_budget
from services.storage_quota_service import check_storage_quota, record_storage_upload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pdf", tags=["PDF Processing"])

# Shared doc store reference — set by app factory
_doc_store: Dict[str, Dict[str, object]] = None
_llm = None


def init_pdf_router(doc_store: dict, llm_client) -> None:
    """Initialize the PDF router with shared resources."""
    global _doc_store, _llm
    _doc_store = doc_store
    _llm = llm_client


# ─── Models ──────────────────────────────────────────────────────────────────

from pydantic import BaseModel


class UploadResponse(BaseModel):
    doc_id: str
    num_chunks: int
    page_count: int = 0
    word_count: int = 0
    has_text: bool = True
    metadata: dict = {}


class DocumentInfoResponse(BaseModel):
    doc_id: str
    num_chunks: int
    available: bool = True


# ─── Upload ──────────────────────────────────────────────────────────────────


@router.post("/upload", response_model=UploadResponse)
async def upload_pdf(
    file: UploadFile = File(...),
    user: dict = Depends(require_upload_access),
):
    """
    Upload and process a PDF file.

    Steps:
    1. Validate file (type, size per tier, magic bytes)
    2. Validate page count per tier
    3. Check daily page budget
    4. Extract text with page-level analysis
    5. Chunk text with overlap
    6. Generate embeddings
    7. Store in memory for AI queries
    8. Save metadata to Firestore (background)
    9. Record upload in usage counters
    """
    uid = user.get("uid")
    tier = user.get("tier")
    plan = user.get("subscription_plan", "free")
    filename = file.filename or "document.pdf"

    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    try:
        content = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to read uploaded file")

    # ── Tier-aware size validation ───────────────────────────────────────
    if tier and len(content) > tier.pdf_upload_size_bytes:
        max_mb = tier.pdf_upload_size_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail={
                "detail": f"PDF exceeds {max_mb} MB limit for your plan.",
                "error_code": "FILE_TOO_LARGE",
                "current": len(content),
                "limit": tier.pdf_upload_size_bytes,
            },
        )

    # ── Basic PDF validation (magic bytes, extension) ────────────────────
    is_valid, validation_error = validate_pdf_upload(content, filename)
    if not is_valid:
        logger.warning(f"Upload rejected for user {uid}: {validation_error}")
        raise HTTPException(status_code=400, detail=validation_error)

    # ── Storage quota check ──────────────────────────────────────────────
    sq = await check_storage_quota(uid, len(content), plan)
    if not sq["allowed"]:
        raise HTTPException(
            status_code=413,
            detail={
                "detail": f"Storage quota exceeded. "
                          f"Used {sq['used_mb']:.1f} MB of {sq['limit_mb']} MB. "
                          f"Only {sq['remaining_mb']:.1f} MB remaining.",
                "error_code": sq["error_code"],
                "used_mb": sq["used_mb"],
                "limit_mb": sq["limit_mb"],
                "remaining_mb": sq["remaining_mb"],
                "file_size_mb": sq["file_size_mb"],
            },
        )

    # ── Process PDF ──────────────────────────────────────────────────────
    result = pdf_processor.process(content, filename)

    if result.error:
        raise HTTPException(status_code=400, detail=result.error)

    if not result.chunks:
        raise HTTPException(status_code=400, detail="No readable text found in PDF")

    # ── Tier-aware page-count validation ─────────────────────────────────
    if tier and result.metadata.page_count > tier.pdf_max_pages:
        raise HTTPException(
            status_code=413,
            detail={
                "detail": f"PDF has {result.metadata.page_count} pages; "
                          f"your plan allows {tier.pdf_max_pages}.",
                "error_code": "TOO_MANY_PAGES",
                "current": result.metadata.page_count,
                "limit": tier.pdf_max_pages,
            },
        )

    # ── Daily page budget check ──────────────────────────────────────────
    page_check = await check_page_budget(uid, result.metadata.page_count, plan)
    if not page_check["allowed"]:
        raise HTTPException(
            status_code=429,
            detail={
                "detail": f"Daily page-processing limit reached "
                          f"({page_check['used']}/{page_check['limit']}).",
                "error_code": page_check["error_code"],
                "current": page_check["used"],
                "limit": page_check["limit"],
            },
        )

    # ── Generate Embeddings ──────────────────────────────────────────────
    try:
        embeddings = await _llm.embed(result.chunks)
        emb_array = np.array(embeddings, dtype="float32")
    except Exception as e:
        logger.error(f"Embedding generation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to process PDF for AI. Please try again.")

    # ── Store in Memory ──────────────────────────────────────────────────
    doc_id = str(uuid.uuid4())
    _doc_store[doc_id] = {
        "chunks": result.chunks,
        "embeddings": emb_array,
        "metadata": {
            "filename": filename,
            "page_count": result.metadata.page_count,
            "word_count": result.metadata.total_words,
            "file_size": result.metadata.file_size,
            "uploaded_by": uid,
        },
    }

    # ── Background: Save metadata + record upload + update storage ──────
    task_manager.submit(
        _save_pdf_metadata_background,
        uid=uid,
        doc_id=doc_id,
        filename=filename,
        file_size=result.metadata.file_size,
        page_count=result.metadata.page_count,
    )
    task_manager.submit(record_upload, uid)
    task_manager.submit(record_storage_upload, uid, len(content))

    logger.info(
        f"PDF uploaded: doc_id={doc_id[:8]}, pages={result.metadata.page_count}, "
        f"chunks={len(result.chunks)}, user={uid[:8]}"
    )

    return UploadResponse(
        doc_id=doc_id,
        num_chunks=len(result.chunks),
        page_count=result.metadata.page_count,
        word_count=result.metadata.total_words,
        has_text=result.metadata.has_text,
        metadata={
            "title": result.metadata.title,
            "author": result.metadata.author,
            "file_hash": result.metadata.file_hash,
        },
    )


# ─── Document Info ───────────────────────────────────────────────────────────


@router.get("/info/{doc_id}", response_model=DocumentInfoResponse)
async def get_document_info(doc_id: str, user: dict = Depends(get_current_user)):
    """Check if a document is still available in memory."""
    if _doc_store is None or doc_id not in _doc_store:
        return DocumentInfoResponse(doc_id=doc_id, num_chunks=0, available=False)

    store = _doc_store[doc_id]
    return DocumentInfoResponse(
        doc_id=doc_id,
        num_chunks=len(store.get("chunks", [])),
        available=True,
    )


# ─── Delete Document ─────────────────────────────────────────────────────────


@router.delete("/delete/{doc_id}")
async def delete_document(doc_id: str, user: dict = Depends(get_current_user)):
    """Remove a document from in-memory store."""
    uid = user.get("uid")

    if _doc_store is None or doc_id not in _doc_store:
        raise HTTPException(status_code=404, detail="Document not found")

    # Verify ownership
    doc_meta = _doc_store[doc_id].get("metadata", {})
    if doc_meta.get("uploaded_by") != uid:
        raise HTTPException(status_code=403, detail="Not authorized to delete this document")

    del _doc_store[doc_id]
    logger.info(f"Document {doc_id[:8]} deleted by user {uid[:8]}")

    return {"success": True, "message": "Document removed from processing"}


# ─── Background Helpers ──────────────────────────────────────────────────────


async def _save_pdf_metadata_background(
    uid: str, doc_id: str, filename: str, file_size: int, page_count: int
) -> None:
    """Save PDF metadata to Firestore in background."""
    try:
        await create_pdf_metadata(
            uid=uid,
            file_name=filename,
            file_size=file_size,
            page_count=page_count,
        )
    except Exception as e:
        logger.warning(f"Failed to save PDF metadata for {doc_id}: {e}")
