from typing import List, Tuple

import numpy as np


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
