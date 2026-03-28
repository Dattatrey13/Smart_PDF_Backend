from fastapi import FastAPI, UploadFile, File, HTTPException
import tempfile
import os
from pydantic import BaseModel

from pdf_utils import extract_text_from_pdf
from vector_store import VectorStore
from ai_utils import ask_gemini, summarize_text

app = FastAPI(title="Smart PDF Backend")
# This is an in-memory store. For production, you'd want a persistent vector store.
store = VectorStore()

class QuestionRequest(BaseModel):
    question: str

def split_text(text: str, chunk_size: int = 1000, chunk_overlap: int = 200):
    """Splits the text into overlapping chunks."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - chunk_overlap
    return chunks

@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Invalid file type. Please upload a PDF.")

    try:
        # Use a temporary file to securely handle the upload
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            temp_file.write(await file.read())
            temp_file_path = temp_file.name

        text = extract_text_from_pdf(temp_file_path)
        
        # A simple chunking strategy
        chunks = split_text(text)
        
        # In a real app, you might want to clear the old store or handle multiple documents
        store.clear() 
        store.add_text(chunks)

        return {"message": "PDF processed successfully", "filename": file.filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {e}")
    finally:
        # Clean up the temporary file
        if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)

@app.post("/ask")
async def ask_question_endpoint(request: QuestionRequest):
    if not store.is_ready():
        raise HTTPException(status_code=400, detail="No PDF has been processed yet. Please upload a PDF first.")
    
    context_chunks = store.search(request.question, top_k=3)
    context = "\n\n".join(context_chunks)
    
    answer = ask_gemini(context, request.question)
    return {"answer": answer}

@app.post("/summary")
async def summary_endpoint():
    if not store.is_ready():
        raise HTTPException(status_code=400, detail="No PDF has been processed yet. Please upload a PDF first.")

    # Join all chunks to get the full text for summarization
    full_text = " ".join(store.get_all_chunks())
    # Limiting the text to avoid exceeding API limits for very large PDFs
    summary = summarize_text(full_text[:20000]) 
    return {"summary": summary}