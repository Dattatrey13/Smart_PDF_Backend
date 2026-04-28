from typing import List


def chunk_text(
    text: str,
    max_tokens: int = 400,
    overlap: int = 50,
) -> List[str]:
    """Token-aware chunking with overlap to preserve semantic context.

    Splits on sentence boundaries when possible, falling back to word
    boundaries.  Each chunk shares *overlap* words with its neighbours so
    that context is not lost at boundaries.
    """
    import re

    # Split into sentences first (crude but effective for most PDFs)
    sentences = re.split(r'(?<=[.!?])\s+', text)

    chunks: List[str] = []
    current_words: List[str] = []

    for sentence in sentences:
        words = sentence.split()
        if not words:
            continue

        # If adding this sentence exceeds the limit, flush current chunk
        if current_words and len(current_words) + len(words) > max_tokens:
            chunks.append(" ".join(current_words))
            # Keep the last `overlap` words for context continuity
            current_words = current_words[-overlap:] if overlap else []

        current_words.extend(words)

        # Safety: if a single sentence is very long, hard-split it
        while len(current_words) > max_tokens:
            chunks.append(" ".join(current_words[:max_tokens]))
            current_words = current_words[max_tokens - overlap:] if overlap else current_words[max_tokens:]

    if current_words:
        chunks.append(" ".join(current_words))

    return chunks
