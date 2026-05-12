"""PDF upload and processing endpoints."""
import uuid
import logging
from typing import Dict

import numpy as np
from fastapi import APIRouter, File, UploadFile, HTTPException, Depends, BackgroundTasks

from auth.dependencies import get_current_user
from auth.storage_service import validate_pdf_upload
from auth.firestore_service import create_pdf_metadata, update_pdf_processing_status
from services.pdf_processor import pdf_processor
from services.background import task_manager

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
    user: dict = Depends(get_current_user),
):
    """
    Upload and process a PDF file.

    Steps:
    1. Validate file (type, size, magic bytes)
    2. Extract text with page-level analysis
    3. Chunk text with overlap
    4. Generate embeddings
    5. Store in memory for AI queries
    6. Save metadata to Firestore (background)
    """
    uid = user.get("uid")
    filename = file.filename or "document.pdf"

    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    try:
        content = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to read uploaded file")

    # ── Validate ─────────────────────────────────────────────────────────────
    is_valid, validation_error = validate_pdf_upload(content, filename)
    if not is_valid:
        logger.warning(f"Upload rejected for user {uid}: {validation_error}")
        raise HTTPException(status_code=400, detail=validation_error)

    # ── Process PDF ──────────────────────────────────────────────────────────
    result = pdf_processor.process(content, filename)

    if result.error:
        raise HTTPException(status_code=400, detail=result.error)

    if not result.chunks:
        raise HTTPException(status_code=400, detail="No readable text found in PDF")

    # ── Generate Embeddings ──────────────────────────────────────────────────
    try:
        embeddings = await _llm.embed(result.chunks)
        emb_array = np.array(embeddings, dtype="float32")
    except Exception as e:
        logger.error(f"Embedding generation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to process PDF for AI. Please try again.")

    # ── Store in Memory ──────────────────────────────────────────────────────
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

    # ── Background: Save metadata to Firestore ───────────────────────────────
    task_manager.submit(
        _save_pdf_metadata_background,
        uid=uid,
        doc_id=doc_id,
        filename=filename,
        file_size=result.metadata.file_size,
        page_count=result.metadata.page_count,
    )

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
