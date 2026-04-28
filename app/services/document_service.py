"""Document store backed by Redis (with in-memory fallback).

Each document is keyed by its doc_id and stores:
  - status: processing | ready | error
  - chunks: List[str]
  - embeddings: serialised numpy array (base64)
  - error: optional error message
"""

import base64
import io
import logging
from typing import Dict, List, Optional

import numpy as np

from app.services.cache_service import cache_get, cache_set

logger = logging.getLogger(__name__)

_DOC_PREFIX = "doc"


def _doc_key(doc_id: str) -> str:
    return f"{_DOC_PREFIX}:{doc_id}"


def _serialize_embeddings(arr: np.ndarray) -> str:
    buf = io.BytesIO()
    np.save(buf, arr)
    return base64.b64encode(buf.getvalue()).decode()


def _deserialize_embeddings(data: str) -> np.ndarray:
    buf = io.BytesIO(base64.b64decode(data))
    return np.load(buf)


# ── public API ──────────────────────────────────────────────

async def create_document(doc_id: str) -> None:
    await cache_set(
        _doc_key(doc_id),
        {"status": "processing", "chunks": [], "embeddings": None, "error": None},
        ttl=86400,  # 24 h
    )


async def mark_ready(
    doc_id: str, chunks: List[str], embeddings: np.ndarray
) -> None:
    await cache_set(
        _doc_key(doc_id),
        {
            "status": "ready",
            "chunks": chunks,
            "embeddings": _serialize_embeddings(embeddings),
            "error": None,
        },
        ttl=86400,
    )


async def mark_error(doc_id: str, error: str) -> None:
    await cache_set(
        _doc_key(doc_id),
        {"status": "error", "chunks": [], "embeddings": None, "error": error},
        ttl=3600,
    )


async def get_document(doc_id: str) -> Optional[Dict]:
    """Return document dict or None."""
    data = await cache_get(_doc_key(doc_id))
    if data is None:
        return None
    # Deserialise embeddings lazily
    if data.get("embeddings"):
        data["embeddings"] = _deserialize_embeddings(data["embeddings"])
    else:
        data["embeddings"] = None
    return data


async def get_status(doc_id: str) -> Optional[Dict]:
    data = await cache_get(_doc_key(doc_id))
    if data is None:
        return None
    return {
        "doc_id": doc_id,
        "status": data["status"],
        "num_chunks": len(data.get("chunks", [])),
        "error": data.get("error"),
    }
