"""AI endpoints: Ask PDF, Summarize, Semantic Search."""
import logging
from typing import List

import numpy as np
from fastapi import APIRouter, Depends, HTTPException

from dependencies.guards import require_ai_access
from services.usage_service import record_ai_request, release_job_slot
from auth.firestore_service import save_chat_entry
from services.cache import ai_response_cache, make_cache_key
from services.background import task_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["AI Processing"])


# ─── Shared state (doc store injected from app) ─────────────────────────────
# This reference is set by the app factory during startup
_doc_store = None
_llm = None


def init_ai_router(doc_store: dict, llm_client) -> None:
    """Initialize the AI router with shared resources."""
    global _doc_store, _llm
    _doc_store = doc_store
    _llm = llm_client


def _get_doc(doc_id: str) -> dict:
    """Retrieve a document from the store or raise 404."""
    if _doc_store is None:
        raise HTTPException(status_code=503, detail="AI service not initialized")
    if doc_id not in _doc_store:
        raise HTTPException(status_code=404, detail="Unknown doc_id. Please re-upload the PDF.")
    return _doc_store[doc_id]


def _top_k_chunks(query_emb: np.ndarray, doc_embs: np.ndarray, chunks: list, k: int = 5):
    """Find top-k most relevant chunks by cosine similarity."""
    a = query_emb[None, :]
    a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-8)
    b_norm = doc_embs / (np.linalg.norm(doc_embs, axis=1, keepdims=True) + 1e-8)
    sims = (a_norm @ b_norm.T)[0]
    idxs = np.argsort(-sims)[:k]
    return [(chunks[i], float(sims[i])) for i in idxs]


# ─── Ask Question ────────────────────────────────────────────────────────────


from pydantic import BaseModel


class AskRequest(BaseModel):
    doc_id: str
    question: str


class AskResponse(BaseModel):
    answer: str
    cached: bool = False


@router.post("/ask", response_model=AskResponse)
async def ask_question(body: AskRequest, user: dict = Depends(require_ai_access)):
    """Answer a question about an uploaded PDF using AI."""
    uid = user.get("uid")
    store = _get_doc(body.doc_id)
    chunks: List[str] = store["chunks"]
    emb_array: np.ndarray = store["embeddings"]

    # Check cache
    cache_key = make_cache_key(body.doc_id, body.question)
    cached_answer = ai_response_cache.get(cache_key)
    if cached_answer:
        logger.info(f"Cache hit for question on doc {body.doc_id[:8]}")
        task_manager.submit(record_ai_request, uid)
        task_manager.submit(release_job_slot, uid)
        return AskResponse(answer=cached_answer, cached=True)

    try:
        # Embed the question
        q_emb_list = await _llm.embed([body.question])
        q_emb = np.array(q_emb_list[0], dtype="float32")

        # Find relevant chunks
        top_chunks = _top_k_chunks(q_emb, emb_array, chunks, k=5)
        context = "\n\n".join([c for c, _ in top_chunks])

        system_prompt = (
            "You are an AI assistant that answers questions based ONLY on the "
            "provided PDF context. If the answer is not in the context, say you "
            "don't know. Be detailed and accurate."
        )
        user_prompt = f"Context:\n{context}\n\nQuestion: {body.question}\n\nAnswer in detail:"

        answer = await _llm.generate(system_prompt, user_prompt)
        answer = answer.strip()

        # Estimate token usage (heuristic: 1 token ≈ 4 chars)
        input_tokens = (len(system_prompt) + len(user_prompt)) // 4
        output_tokens = len(answer) // 4

        # Cache the response
        ai_response_cache.set(cache_key, answer)

        # Background: record usage + save chat + release slot
        task_manager.submit(
            record_ai_request,
            uid,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        task_manager.submit(release_job_slot, uid)
        task_manager.submit(
            save_chat_entry,
            uid=uid,
            pdf_id=body.doc_id,
            question=body.question,
            answer=answer,
        )

        return AskResponse(answer=answer)

    except Exception as e:
        # Always release the job slot on failure
        task_manager.submit(release_job_slot, uid)
        logger.error(f"Ask question failed for user {uid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="AI processing failed. Please try again.")


# ─── Summary ─────────────────────────────────────────────────────────────────


class SummaryRequest(BaseModel):
    doc_id: str
    max_chunks: int = 10


class SummaryResponse(BaseModel):
    summary: str
    cached: bool = False


@router.post("/summary", response_model=SummaryResponse)
async def summarize(body: SummaryRequest, user: dict = Depends(require_ai_access)):
    """Generate a structured summary of the uploaded PDF."""
    uid = user.get("uid")
    store = _get_doc(body.doc_id)
    chunks: List[str] = store["chunks"]

    # Check cache
    cache_key = make_cache_key(body.doc_id, "summary", str(body.max_chunks))
    cached_summary = ai_response_cache.get(cache_key)
    if cached_summary:
        task_manager.submit(record_ai_request, uid)
        task_manager.submit(release_job_slot, uid)
        return SummaryResponse(summary=cached_summary, cached=True)

    try:
        context = "\n\n".join(chunks[:body.max_chunks])

        system_prompt = (
            "You are an expert summarizer. Create a concise, structured summary "
            "of the provided PDF content. Use headings and bullet points. "
            "Capture the key ideas, arguments, and conclusions."
        )
        user_prompt = f"PDF Content:\n{context}\n\nWrite a comprehensive summary:"

        summary = await _llm.generate(system_prompt, user_prompt)
        summary = summary.strip()

        input_tokens = (len(system_prompt) + len(user_prompt)) // 4
        output_tokens = len(summary) // 4

        ai_response_cache.set(cache_key, summary)
        task_manager.submit(
            record_ai_request,
            uid,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        task_manager.submit(release_job_slot, uid)

        return SummaryResponse(summary=summary)

    except Exception as e:
        task_manager.submit(release_job_slot, uid)
        logger.error(f"Summary failed for user {uid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Summary generation failed. Please try again.")


# ─── Semantic Search ─────────────────────────────────────────────────────────


class SearchRequest(BaseModel):
    doc_id: str
    query: str
    top_k: int = 5


class SearchHit(BaseModel):
    text: str
    score: float
    chunk_index: int = 0


class SearchResponse(BaseModel):
    hits: List[SearchHit]
    query: str


@router.post("/search", response_model=SearchResponse)
async def search(body: SearchRequest, user: dict = Depends(require_ai_access)):
    """Semantic search across PDF chunks."""
    uid = user.get("uid")
    store = _get_doc(body.doc_id)
    chunks: List[str] = store["chunks"]
    emb_array: np.ndarray = store["embeddings"]

    try:
        q_emb_list = await _llm.embed([body.query])
        q_emb = np.array(q_emb_list[0], dtype="float32")

        # Compute similarities
        a = q_emb[None, :]
        a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-8)
        b_norm = emb_array / (np.linalg.norm(emb_array, axis=1, keepdims=True) + 1e-8)
        sims = (a_norm @ b_norm.T)[0]
        idxs = np.argsort(-sims)[:body.top_k]

        hits = [
            SearchHit(text=chunks[i], score=float(sims[i]), chunk_index=int(i))
            for i in idxs
        ]

        task_manager.submit(record_ai_request, uid)
        task_manager.submit(release_job_slot, uid)

        return SearchResponse(hits=hits, query=body.query)

    except Exception as e:
        task_manager.submit(release_job_slot, uid)
        logger.error(f"Search failed for user {uid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Search failed. Please try again.")
