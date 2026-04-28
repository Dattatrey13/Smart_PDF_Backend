"""Semantic search endpoint."""

import logging

import numpy as np
from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_llm_service
from app.models.schemas import SearchRequest, SearchResponse, SearchHit
from app.services import document_service
from app.services.llm_service import LLMService
from app.utils.similarity import top_k_chunks

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/search", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    llm: LLMService = Depends(get_llm_service),
):
    doc = await document_service.get_document(body.doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Unknown doc_id")
    if doc["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Document status: {doc['status']}")

    q_emb_list = await llm.embed([body.query])
    q_emb = np.array(q_emb_list[0], dtype="float32")

    results = top_k_chunks(q_emb, doc["embeddings"], doc["chunks"], k=body.top_k)
    hits = [SearchHit(text=text, score=score) for text, score in results]
    return SearchResponse(hits=hits)
