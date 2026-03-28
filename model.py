from pydantic import BaseModel
from typing import List


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