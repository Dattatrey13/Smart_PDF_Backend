"""PDF processing pipeline – runs as a background task."""

import logging
import uuid

import numpy as np

from app.services.llm_service import LLMService
from app.services import document_service
from app.utils.pdf_parser import extract_text_from_pdf
from app.utils.chunking import chunk_text
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def generate_doc_id() -> str:
    return str(uuid.uuid4())


async def process_pdf(doc_id: str, file_bytes: bytes, llm: LLMService) -> None:
    """Full pipeline: extract → chunk → embed → store.

    Designed to run inside a FastAPI BackgroundTask so the upload
    endpoint can return immediately.
    """
    try:
        logger.info("[%s] Starting PDF processing (%d bytes)", doc_id, len(file_bytes))

        text = extract_text_from_pdf(file_bytes)
        if not text.strip():
            await document_service.mark_error(doc_id, "Could not extract text from PDF")
            return

        chunks = chunk_text(
            text,
            max_tokens=settings.CHUNK_MAX_TOKENS,
            overlap=settings.CHUNK_OVERLAP_TOKENS,
        )
        if not chunks:
            await document_service.mark_error(doc_id, "No chunks created from PDF")
            return

        logger.info("[%s] Created %d chunks – embedding…", doc_id, len(chunks))

        embeddings_list = await llm.embed(chunks)
        emb_array = np.array(embeddings_list, dtype="float32")

        await document_service.mark_ready(doc_id, chunks, emb_array)
        logger.info("[%s] Processing complete – %d chunks stored", doc_id, len(chunks))

    except Exception as exc:
        logger.error("[%s] Processing failed: %s", doc_id, exc, exc_info=True)
        await document_service.mark_error(doc_id, str(exc))
