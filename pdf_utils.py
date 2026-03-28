from typing import List, Tuple
import io

import numpy as np
from PyPDF2 import PdfReader


def extract_text_from_pdf(file_bytes: bytes) -> str:
    pdf = PdfReader(io.BytesIO(file_bytes))
    pages_text = []
    for page in pdf.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        pages_text.append(text)
    return "\n".join(pages_text)


def chunk_text(text: str, max_tokens: int = 400) -> List[str]:
    # simple word-based chunks; you can improve later
    words = text.split()
    chunks: List[str] = []
    current: List[str] = []
    for w in words:
        current.append(w)
        if len(current) >= max_tokens:
            chunks.append(" ".join(current))
            current = []
    if current:
        chunks.append(" ".join(current))
    return chunks


def cosine_similarity_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-8)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-8)
    return a_norm @ b_norm.T


def top_k_chunks(
    query_emb: np.ndarray,
    doc_embs: np.ndarray,
    chunks: List[str],
    k: int = 5,
) -> List[Tuple[str, float]]:
    sims = cosine_similarity_matrix(query_emb[None, :], doc_embs)[0]
    idxs = np.argsort(-sims)[:k]
    return [(chunks[i], float(sims[i])) for i in idxs]