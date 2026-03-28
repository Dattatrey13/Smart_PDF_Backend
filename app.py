import uuid
import logging
from typing import Dict, List

import numpy as np
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from llm_client import LLMClient
from pdf_utils import extract_text_from_pdf, chunk_text
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
from pdf_utils import top_k_chunks


app = FastAPI(title="Smart PDF Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

llm = LLMClient()

# In-memory store: doc_id -> {chunks, embeddings}
DOC_STORE: Dict[str, Dict[str, object]] = {}


@app.post("/upload", response_model=UploadResponse)
async def upload_pdf(file: UploadFile = File(...)):
    logger.info(f"=== PDF Upload Started ===")
    logger.info(f"Filename: {file.filename}")
    logger.info(f"File content type: {file.content_type}")
    
    if not file.filename.lower().endswith(".pdf"):
        logger.error(f"Invalid file format: {file.filename} is not a PDF")
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    try:
        content = await file.read()
        logger.info(f"File read successfully. Size: {len(content)} bytes")
        
        text = extract_text_from_pdf(content)
        logger.info(f"PDF text extracted. Text length: {len(text)} characters")
        
        if not text.strip():
            logger.error("Extracted text is empty")
            raise HTTPException(status_code=400, detail="Could not extract text from PDF")

        chunks = chunk_text(text, max_tokens=400)
        logger.info(f"PDF chunked into {len(chunks)} chunks")
        
        if not chunks:
            logger.error("No chunks created from PDF text")
            raise HTTPException(status_code=400, detail="No chunks created")

        logger.info(f"Starting to embed {len(chunks)} chunks...")
        embeddings = await llm.embed(chunks)
        logger.info(f"Embeddings created successfully")
        
        emb_array = np.array(embeddings, dtype="float32")
        logger.info(f"Embedding array shape: {emb_array.shape}")

        doc_id = str(uuid.uuid4())
        DOC_STORE[doc_id] = {
            "chunks": chunks,
            "embeddings": emb_array,
        }
        
        logger.info(f"Upload successful! Generated doc_id: {doc_id}")
        logger.info(f"Current documents in store: {len(DOC_STORE)}")
        
        response = UploadResponse(doc_id=doc_id, num_chunks=len(chunks))
        logger.info(f"Returning response: {response}")
        return response
        
    except Exception as e:
        logger.error(f"Error during upload: {type(e).__name__} - {str(e)}", exc_info=True)
        raise


@app.post("/ask", response_model=AskResponse)
async def ask_question(body: AskRequest):
    logger.info(f"=== Ask Question Started ===")
    logger.info(f"Doc ID: {body.doc_id}")
    logger.info(f"Question: {body.question}")
    
    if body.doc_id not in DOC_STORE:
        logger.error(f"Doc ID not found: {body.doc_id}")
        logger.debug(f"Available doc IDs: {list(DOC_STORE.keys())}")
        raise HTTPException(status_code=404, detail="Unknown doc_id")

    store = DOC_STORE[body.doc_id]
    chunks: List[str] = store["chunks"]  # type: ignore
    emb_array: np.ndarray = store["embeddings"]  # type: ignore
    
    logger.info(f"Found document with {len(chunks)} chunks")

    try:
        # embed the question
        logger.info(f"Embedding question...")
        q_emb_list = await llm.embed([body.question])
        q_emb = np.array(q_emb_list[0], dtype="float32")
        logger.info(f"Question embedding created")
        
        logger.info(f"Finding top 5 matching chunks...")
        top_chunks = top_k_chunks(q_emb, emb_array, chunks, k=5)
        logger.info(f"Found {len(top_chunks)} matching chunks")

        context = "\n\n".join([c for c, _ in top_chunks])
        logger.debug(f"Context length: {len(context)} characters")
        
        system_prompt = (
            "You are an AI assistant that answers questions based ONLY on the "
            "provided PDF context. If the answer is not in the context, say you "
            "don't know."
        )
        user_prompt = f"Context:\n{context}\n\nQuestion: {body.question}\n\nAnswer in detail:"

        logger.info(f"Generating answer using LLM...")
        answer = await llm.generate(system_prompt, user_prompt)
        logger.info(f"Answer generated successfully")
        
        return AskResponse(answer=answer.strip())
        
    except Exception as e:
        logger.error(f"Error during question answering: {type(e).__name__} - {str(e)}", exc_info=True)
        raise


@app.post("/summary", response_model=SummaryResponse)
async def summarize(body: SummaryRequest):
    logger.info(f"=== Summary Request Started ===")
    logger.info(f"Doc ID: {body.doc_id}")
    
    if body.doc_id not in DOC_STORE:
        logger.error(f"Doc ID not found: {body.doc_id}")
        raise HTTPException(status_code=404, detail="Unknown doc_id")

    store = DOC_STORE[body.doc_id]
    chunks: List[str] = store["chunks"]  # type: ignore
    
    logger.info(f"Found document with {len(chunks)} chunks")

    try:
        # limit context to first N chunks
        max_chunks = 10
        context = "\n\n".join(chunks[:max_chunks])
        logger.info(f"Using first {min(len(chunks), max_chunks)} chunks for summary")
        logger.debug(f"Context length: {len(context)} characters")

        system_prompt = (
            "You are an expert summarizer. Create a concise, structured summary "
            "of the provided PDF content. Use headings and bullet points."
        )
        user_prompt = f"PDF Content:\n{context}\n\nWrite a high-level summary:"

        logger.info(f"Generating summary using LLM...")
        summary = await llm.generate(system_prompt, user_prompt)
        logger.info(f"Summary generated successfully")
        
        return SummaryResponse(summary=summary.strip())
        
    except Exception as e:
        logger.error(f"Error during summarization: {type(e).__name__} - {str(e)}", exc_info=True)
        raise


@app.post("/search", response_model=SearchResponse)
async def search(body: SearchRequest):
    logger.info(f"=== Search Request Started ===")
    logger.info(f"Doc ID: {body.doc_id}")
    logger.info(f"Query: {body.query}")
    
    if body.doc_id not in DOC_STORE:
        logger.error(f"Doc ID not found: {body.doc_id}")
        raise HTTPException(status_code=404, detail="Unknown doc_id")

    store = DOC_STORE[body.doc_id]
    chunks: List[str] = store["chunks"]  # type: ignore
    emb_array: np.ndarray = store["embeddings"]  # type: ignore
    
    logger.info(f"Found document with {len(chunks)} chunks")

    try:
        logger.info(f"Embedding search query...")
        q_emb_list = await llm.embed([body.query])
        q_emb = np.array(q_emb_list[0], dtype="float32")
        logger.info(f"Query embedding created")
        
        logger.info(f"Finding top 5 matching chunks...")
        top_chunks = top_k_chunks(q_emb, emb_array, chunks, k=5)
        logger.info(f"Found {len(top_chunks)} matching chunks")

        hits = [
            SearchHit(text=text, score=score)
            for text, score in top_chunks
        ]
        
        logger.info(f"Search completed successfully with {len(hits)} results")
        return SearchResponse(hits=hits)
        
    except Exception as e:
        logger.error(f"Error during search: {type(e).__name__} - {str(e)}", exc_info=True)
        raise


@app.get("/")
async def root():
    return JSONResponse({"status": "ok", "message": "Smart PDF backend running"})