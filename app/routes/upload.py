"""PDF upload endpoint with background processing."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

from app.config import get_settings
from app.dependencies import get_llm_service
from app.models.schemas import DocumentStatusResponse, UploadResponse
from app.services import document_service, pdf_service
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()


@router.post("/upload", response_model=UploadResponse)
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    llm: LLMService = Depends(get_llm_service),
):
    # ── validate ────────────────────────────────────────────
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    content = await file.read()

    if len(content) > settings.MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {settings.MAX_FILE_SIZE_MB} MB limit",
        )

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    # ── create doc record and kick off background task ──────
    doc_id = pdf_service.generate_doc_id()
    await document_service.create_document(doc_id)

    background_tasks.add_task(pdf_service.process_pdf, doc_id, content, llm)

    logger.info("Upload accepted – doc_id=%s  size=%d bytes", doc_id, len(content))
    return UploadResponse(doc_id=doc_id, status="processing", num_chunks=0)


@router.get("/status/{doc_id}", response_model=DocumentStatusResponse)
async def document_status(doc_id: str):
    info = await document_service.get_status(doc_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Unknown doc_id")
    return DocumentStatusResponse(**info)
