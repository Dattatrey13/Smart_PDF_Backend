import io
from PyPDF2 import PdfReader


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract text from PDF bytes without writing to disk."""
    pdf = PdfReader(io.BytesIO(file_bytes))
    pages_text = []
    for page in pdf.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        pages_text.append(text)
    return "\n".join(pages_text)
