"""Ask endpoint – supports regular JSON and SSE streaming responses."""

import json
import logging

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.dependencies import get_llm_service
from app.models.schemas import AskRequest, AskResponse
from app.services import document_service, cache_service
from app.services.llm_service import LLMService
from app.utils.similarity import top_k_chunks
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()

SYSTEM_PROMPT = (
    "You are an AI assistant that answers questions based ONLY on the "
    "provided PDF context. If the answer is not in the context, say you "
    "don't know."
)


async def _get_context(doc_id: str, question: str, llm: LLMService) -> str:
    """Embed the question and retrieve relevant chunks."""
    doc = await document_service.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Unknown doc_id")
    if doc["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Document status: {doc['status']}")

    q_emb_list = await llm.embed([question])
    q_emb = np.array(q_emb_list[0], dtype="float32")

    top_chunks = top_k_chunks(q_emb, doc["embeddings"], doc["chunks"], k=settings.TOP_K_RESULTS)
    return "\n\n".join(text for text, _ in top_chunks)


@router.post("/ask", response_model=AskResponse)
async def ask_question(
    body: AskRequest,
    llm: LLMService = Depends(get_llm_service),
):
    # Check cache
    cache_key = LLMService.cache_key("ask", body.doc_id, body.question)
    cached = await cache_service.cache_get(cache_key)
    if cached:
        logger.info("Cache hit for ask query")
        return AskResponse(answer=cached)

    context = await _get_context(body.doc_id, body.question, llm)
    user_prompt = f"Context:\n{context}\n\nQuestion: {body.question}\n\nAnswer in detail:"
    answer = await llm.generate(SYSTEM_PROMPT, user_prompt)
    answer = answer.strip()

    await cache_service.cache_set(cache_key, answer)
    return AskResponse(answer=answer)


@router.post("/ask/stream")
async def ask_question_stream(
    body: AskRequest,
    llm: LLMService = Depends(get_llm_service),
):
    """Server-Sent Events endpoint for progressive token streaming."""
    context = await _get_context(body.doc_id, body.question, llm)
    user_prompt = f"Context:\n{context}\n\nQuestion: {body.question}\n\nAnswer in detail:"

    async def event_generator():
        async for token in llm.generate_stream(SYSTEM_PROMPT, user_prompt):
            yield f"data: {json.dumps({'token': token})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
