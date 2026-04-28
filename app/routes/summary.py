"""Map-reduce summarisation endpoint."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_llm_service
from app.models.schemas import SummaryRequest, SummaryResponse
from app.services import document_service, cache_service
from app.services.llm_service import LLMService
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()

MAP_PROMPT = (
    "You are an expert summarizer. Summarize the following section of a PDF "
    "document in 3-5 bullet points. Be concise and factual."
)

REDUCE_PROMPT = (
    "You are an expert summarizer. Below are summaries of different sections "
    "of a PDF document. Combine them into a single, coherent, structured "
    "summary using headings and bullet points. Remove redundancy."
)


@router.post("/summary", response_model=SummaryResponse)
async def summarize(
    body: SummaryRequest,
    llm: LLMService = Depends(get_llm_service),
):
    # Check cache
    cache_key = LLMService.cache_key("summary", body.doc_id)
    cached = await cache_service.cache_get(cache_key)
    if cached:
        logger.info("Cache hit for summary")
        return SummaryResponse(summary=cached)

    doc = await document_service.get_document(body.doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Unknown doc_id")
    if doc["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Document status: {doc['status']}")

    chunks = doc["chunks"]
    selected = chunks[: settings.SUMMARY_MAX_CHUNKS]

    # ── MAP phase: summarise each chunk concurrently ────────
    async def summarise_chunk(chunk: str) -> str:
        return await llm.generate(MAP_PROMPT, f"Section:\n{chunk}")

    chunk_summaries = await asyncio.gather(
        *(summarise_chunk(c) for c in selected)
    )

    # ── REDUCE phase: combine chunk summaries ───────────────
    combined = "\n\n".join(
        f"Section {i+1}:\n{s.strip()}" for i, s in enumerate(chunk_summaries)
    )
    final_summary = await llm.generate(REDUCE_PROMPT, combined)
    final_summary = final_summary.strip()

    await cache_service.cache_set(cache_key, final_summary)
    return SummaryResponse(summary=final_summary)
