from typing import List, Optional

from pydantic import BaseModel, Field


# ── Upload ──────────────────────────────────────────────────
class UploadResponse(BaseModel):
    doc_id: str
    status: str = "processing"
    num_chunks: int = 0


# ── Status ──────────────────────────────────────────────────
class DocumentStatusResponse(BaseModel):
    doc_id: str
    status: str  # processing | ready | error
    num_chunks: int = 0
    error: Optional[str] = None


# ── Ask ─────────────────────────────────────────────────────
class AskRequest(BaseModel):
    doc_id: str
    question: str = Field(..., min_length=1, max_length=2000)


class AskResponse(BaseModel):
    answer: str


# ── Summary ─────────────────────────────────────────────────
class SummaryRequest(BaseModel):
    doc_id: str


class SummaryResponse(BaseModel):
    summary: str


# ── Search ──────────────────────────────────────────────────
class SearchRequest(BaseModel):
    doc_id: str
    query: str = Field(..., min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)


class SearchHit(BaseModel):
    text: str
    score: float


class SearchResponse(BaseModel):
    hits: List[SearchHit]
