"""Async Gemini LLM client with batched embeddings and retry logic."""

import asyncio
import hashlib
import logging
from typing import AsyncIterator, List

import google.generativeai as genai

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class LLMService:
    def __init__(self):
        if not settings.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY environment variable not set")
        genai.configure(api_key=settings.GEMINI_API_KEY)

        self.text_model = genai.GenerativeModel(settings.GEMINI_TEXT_MODEL)
        self.embedding_model = settings.GEMINI_EMBEDDING_MODEL
        logger.info(
            "LLMService initialised  text=%s  embed=%s",
            settings.GEMINI_TEXT_MODEL,
            settings.GEMINI_EMBEDDING_MODEL,
        )

    # ── helpers ──────────────────────────────────────────────

    @staticmethod
    async def _retry(coro_factory, max_retries: int = 3, initial_delay: float = 1.0):
        """Call *coro_factory()* with exponential back-off on 429 / quota errors."""
        for attempt in range(max_retries):
            try:
                return await coro_factory()
            except Exception as exc:
                msg = str(exc).lower()
                if any(kw in msg for kw in ("429", "quota", "rate limit")):
                    if attempt < max_retries - 1:
                        delay = initial_delay * (2 ** attempt)
                        logger.warning("Rate-limited – retrying in %.1fs  (attempt %d/%d)", delay, attempt + 1, max_retries)
                        await asyncio.sleep(delay)
                        continue
                raise

    # ── embeddings (batched) ─────────────────────────────────

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings in batches via asyncio.to_thread."""
        batch_size = settings.EMBEDDING_BATCH_SIZE
        all_embeddings: List[List[float]] = []

        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]

            async def _embed_batch(b=batch):
                return await asyncio.to_thread(
                    genai.embed_content,
                    model=self.embedding_model,
                    content=b,
                    task_type="retrieval_document",
                )

            try:
                result = await self._retry(_embed_batch)
                embs = result["embedding"]
                # Single text returns a flat list; batch returns list-of-lists
                if embs and not isinstance(embs[0], list):
                    embs = [embs]
                all_embeddings.extend(embs)
            except Exception as exc:
                logger.error("Embedding batch failed: %s", exc)
                # Zero-fill so downstream shapes stay consistent
                all_embeddings.extend([[0.0] * 768] * len(batch))

        return all_embeddings

    # ── text generation (non-blocking) ───────────────────────

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        full_prompt = f"{system_prompt}\n\n{user_prompt}"

        async def _gen():
            return await asyncio.to_thread(
                self.text_model.generate_content, full_prompt
            )

        try:
            response = await self._retry(_gen, max_retries=4, initial_delay=2.0)
            return response.text or "No response generated."
        except Exception as exc:
            logger.error("Generation error: %s", exc)
            return f"Error generating response: {exc}"

    # ── streaming generation (SSE-friendly) ──────────────────

    async def generate_stream(
        self, system_prompt: str, user_prompt: str
    ) -> AsyncIterator[str]:
        """Yield text chunks as the model streams its response."""
        full_prompt = f"{system_prompt}\n\n{user_prompt}"

        def _stream():
            return self.text_model.generate_content(full_prompt, stream=True)

        try:
            response = await asyncio.to_thread(_stream)
            for chunk in response:
                if chunk.text:
                    yield chunk.text
        except Exception as exc:
            logger.error("Stream generation error: %s", exc)
            yield f"\n[Error: {exc}]"

    # ── cache key helper ─────────────────────────────────────

    @staticmethod
    def cache_key(prefix: str, *parts: str) -> str:
        raw = ":".join(parts)
        return f"{prefix}:{hashlib.sha256(raw.encode()).hexdigest()}"
