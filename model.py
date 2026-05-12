"""Pydantic models for request/response schemas (legacy + new)."""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


# ─── Legacy Models (kept for backward compatibility with old /upload, /ask, /summary, /search) ─


class UploadResponse(BaseModel):
    doc_id: str
    num_chunks: int


class AskRequest(BaseModel):
    doc_id: str
    question: str


class AskResponse(BaseModel):
    answer: str


class SummaryRequest(BaseModel):
    doc_id: str


class SummaryResponse(BaseModel):
    summary: str


class SearchRequest(BaseModel):
    doc_id: str
    query: str


class SearchHit(BaseModel):
    text: str
    score: float


class SearchResponse(BaseModel):
    hits: List[SearchHit]


# ─── Error Response ──────────────────────────────────────────────────────────


class ErrorResponse(BaseModel):
    """Standard error response."""
    detail: str
    error_code: Optional[str] = None
    request_id: Optional[str] = None


# ─── Subscription Models ─────────────────────────────────────────────────────


class SubscriptionInfo(BaseModel):
    plan: str = "free"
    daily_limit: int = 20
    used_today: int = 0
    remaining: int = 20
    reset_at: Optional[str] = None


# ─── User Models ─────────────────────────────────────────────────────────────


class UserInfo(BaseModel):
    uid: str
    email: str
    name: Optional[str] = None
    subscription: SubscriptionInfo = SubscriptionInfo()
    account_status: str = "active"


# ─── PDF Processing Models ───────────────────────────────────────────────────


class PdfMetadataResponse(BaseModel):
    """PDF metadata returned after upload."""
    title: str = ""
    author: str = ""
    page_count: int = 0
    word_count: int = 0
    file_size: int = 0
    has_text: bool = True


class PdfListItem(BaseModel):
    """Single item in user's PDF list."""
    id: str
    file_name: str
    file_size: int = 0
    page_count: int = 0
    upload_time: Optional[str] = None
    processing_status: str = "completed"


# ─── AI Usage Models ─────────────────────────────────────────────────────────


class AiUsageResponse(BaseModel):
    """Current AI usage stats for the user."""
    used_today: int = 0
    daily_limit: int = 20
    remaining: int = 20
    total_requests: int = 0
    plan: str = "free"
    reset_at: Optional[str] = None
    blocked: bool = False