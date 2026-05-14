"""
Smart PDF Backend — Application Factory

Production-ready FastAPI application for AI-powered PDF processing.
Handles: authentication, AI orchestration, PDF processing, usage limiting, security.
"""
import uuid
import logging
from typing import Dict, List
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, File, UploadFile, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from config import settings
from middleware.logging_mw import RequestLoggingMiddleware, setup_logging
from middleware.security import (
    SecurityHeadersMiddleware,
    RequestSizeLimitMiddleware,
    IPBlockMiddleware,
    GlobalRateLimitMiddleware,
)
from middleware.exceptions import register_exception_handlers

# Routers
from routers.ai import router as ai_router, init_ai_router
from routers.pdf import router as pdf_router, init_pdf_router
from routers.health import router as health_router, init_health_router
from routers.usage import router as usage_router
from routers.storage import router as storage_router
from auth.routes import router as auth_router

# Services
from llm_client import LLMClient
from services.cache import ai_response_cache
from services.background import task_manager

# Auth (for legacy endpoints)
from auth.dependencies import require_ai_access
from auth.rate_limiter import increment_ai_usage
from auth.firestore_service import save_chat_entry
from auth.storage_service import validate_pdf_upload
from pdf_utils import extract_text_from_pdf, chunk_text, top_k_chunks
from model import (
    UploadResponse,
    AskRequest,
    AskResponse,
    SummaryRequest,
    SummaryResponse,
    SearchRequest,
    SearchResponse,
    SearchHit,
)

# ─── Setup Logging ───────────────────────────────────────────────────────────
setup_logging()
logger = logging.getLogger(__name__)

# ─── Shared State ────────────────────────────────────────────────────────────
# In-memory document store: doc_id -> {chunks, embeddings, metadata}
# NOTE: This is ephemeral. Restarting the server loses all documents.
# For production with multiple instances, use Redis or a vector DB.
DOC_STORE: Dict[str, Dict[str, object]] = {}

llm = LLMClient()


# ─── Lifespan (startup/shutdown) ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    # Startup
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION} [{settings.ENVIRONMENT}]")

    # Initialize routers with shared resources
    init_ai_router(DOC_STORE, llm)
    init_pdf_router(DOC_STORE, llm)
    init_health_router(DOC_STORE)

    logger.info("All services initialized successfully")
    yield

    # Shutdown
    logger.info("Shutting down...")
    DOC_STORE.clear()
    ai_response_cache.clear()
    logger.info("Shutdown complete")


# ─── App Factory ─────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
    lifespan=lifespan,
)


# ─── Middleware Stack (order matters — first added = outermost) ──────────────

# 1. Request logging (outermost — logs all requests)
app.add_middleware(RequestLoggingMiddleware)

# 2. Security headers
app.add_middleware(SecurityHeadersMiddleware)

# 3. Request size limiting
app.add_middleware(RequestSizeLimitMiddleware)

# 4. IP-based blocking for repeated auth failures
app.add_middleware(IPBlockMiddleware)

# 5. Global rate limiting
app.add_middleware(GlobalRateLimitMiddleware, requests_per_minute=settings.GLOBAL_RATE_LIMIT_PER_MINUTE)

# 6. CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

# 7. Trusted hosts (only in production)
if settings.ENVIRONMENT == "production" and "*" not in settings.TRUSTED_HOSTS:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.TRUSTED_HOSTS)


# ─── Exception Handlers ─────────────────────────────────────────────────────

register_exception_handlers(app)


# ─── Include Routers ─────────────────────────────────────────────────────────

# New modular routers
app.include_router(ai_router)
app.include_router(pdf_router)
app.include_router(health_router)
app.include_router(usage_router)
app.include_router(storage_router)
app.include_router(auth_router)


# ═══════════════════════════════════════════════════════════════════════════════
# LEGACY ENDPOINTS — Kept for backward compatibility with existing Flutter app.
# These mirror the old API paths. New clients should use /ai/* and /pdf/* routes.
# ═══════════════════════════════════════════════════════════════════════════════


@app.post("/upload", response_model=UploadResponse, tags=["Legacy"])
async def upload_pdf(file: UploadFile = File(...)):
    """Legacy upload endpoint (unauthenticated for backward compat)."""
    logger.info(f"[Legacy] PDF Upload: {file.filename}")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    try:
        content = await file.read()

        # Validate PDF
        is_valid, validation_error = validate_pdf_upload(content, file.filename)
        if not is_valid:
            raise HTTPException(status_code=400, detail=validation_error)

        text = extract_text_from_pdf(content)
        if not text.strip():
            # Attempt OCR via Gemini for scanned/image-based PDFs
            try:
                from services.pdf_processor import pdf_processor
                result = pdf_processor.process(content, file.filename)
                if result.chunks:
                    text = result.full_text
                else:
                    raise HTTPException(status_code=400, detail="Could not extract text from PDF. The PDF may be image-based and OCR could not read it.")
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"[Legacy] OCR fallback failed: {e}")
                raise HTTPException(status_code=400, detail="Could not extract text from PDF")

        chunks = chunk_text(text, max_tokens=settings.AI_CHUNK_SIZE)
        if not chunks:
            raise HTTPException(status_code=400, detail="No chunks created")

        embeddings = await llm.embed(chunks)
        emb_array = np.array(embeddings, dtype="float32")

        doc_id = str(uuid.uuid4())
        DOC_STORE[doc_id] = {
            "chunks": chunks,
            "embeddings": emb_array,
        }

        return UploadResponse(doc_id=doc_id, num_chunks=len(chunks))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Legacy] Upload error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Upload processing failed")


@app.post("/ask", response_model=AskResponse, tags=["Legacy"])
async def ask_question(body: AskRequest, user: dict = Depends(require_ai_access)):
    """Legacy ask endpoint."""
    if body.doc_id not in DOC_STORE:
        raise HTTPException(status_code=404, detail="Unknown doc_id")

    store = DOC_STORE[body.doc_id]
    chunks: List[str] = store["chunks"]
    emb_array: np.ndarray = store["embeddings"]

    try:
        q_emb_list = await llm.embed([body.question])
        q_emb = np.array(q_emb_list[0], dtype="float32")
        top_chunks_result = top_k_chunks(q_emb, emb_array, chunks, k=5)

        context = "\n\n".join([c for c, _ in top_chunks_result])
        system_prompt = (
            "You are an AI assistant that answers questions based ONLY on the "
            "provided PDF context. If the answer is not in the context, say you "
            "don't know."
        )
        user_prompt = f"Context:\n{context}\n\nQuestion: {body.question}\n\nAnswer in detail:"

        answer = await llm.generate(system_prompt, user_prompt)

        # Increment usage + save chat (background)
        task_manager.submit(increment_ai_usage, user.get("uid"))
        task_manager.submit(
            save_chat_entry,
            uid=user.get("uid"),
            pdf_id=body.doc_id,
            question=body.question,
            answer=answer.strip(),
        )

        return AskResponse(answer=answer.strip())

    except Exception as e:
        logger.error(f"[Legacy] Ask error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="AI processing failed")


@app.post("/summary", response_model=SummaryResponse, tags=["Legacy"])
async def summarize(body: SummaryRequest, user: dict = Depends(require_ai_access)):
    """Legacy summary endpoint."""
    if body.doc_id not in DOC_STORE:
        raise HTTPException(status_code=404, detail="Unknown doc_id")

    store = DOC_STORE[body.doc_id]
    chunks: List[str] = store["chunks"]

    try:
        context = "\n\n".join(chunks[:10])
        system_prompt = (
            "You are an expert summarizer. Create a concise, structured summary "
            "of the provided PDF content. Use headings and bullet points."
        )
        user_prompt = f"PDF Content:\n{context}\n\nWrite a high-level summary:"

        summary = await llm.generate(system_prompt, user_prompt)
        task_manager.submit(increment_ai_usage, user.get("uid"))

        return SummaryResponse(summary=summary.strip())

    except Exception as e:
        logger.error(f"[Legacy] Summary error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Summary generation failed")


@app.post("/search", response_model=SearchResponse, tags=["Legacy"])
async def search(body: SearchRequest, user: dict = Depends(require_ai_access)):
    """Legacy search endpoint."""
    if body.doc_id not in DOC_STORE:
        raise HTTPException(status_code=404, detail="Unknown doc_id")

    store = DOC_STORE[body.doc_id]
    chunks: List[str] = store["chunks"]
    emb_array: np.ndarray = store["embeddings"]

    try:
        q_emb_list = await llm.embed([body.query])
        q_emb = np.array(q_emb_list[0], dtype="float32")
        top_chunks_result = top_k_chunks(q_emb, emb_array, chunks, k=5)

        hits = [SearchHit(text=text, score=score) for text, score in top_chunks_result]
        task_manager.submit(increment_ai_usage, user.get("uid"))

        return SearchResponse(hits=hits)

    except Exception as e:
        logger.error(f"[Legacy] Search error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Search failed")