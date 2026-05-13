"""Enhanced PDF processing pipeline with OCR support, metadata extraction, and page analysis."""
import io
import logging
import hashlib
from typing import Optional
from dataclasses import dataclass, field

from PyPDF2 import PdfReader

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class PageInfo:
    """Metadata for a single PDF page."""
    page_number: int
    text: str
    word_count: int
    has_images: bool = False
    has_tables: bool = False


@dataclass
class PdfMetadata:
    """Extracted PDF metadata and content."""
    title: str = ""
    author: str = ""
    subject: str = ""
    creator: str = ""
    producer: str = ""
    page_count: int = 0
    total_words: int = 0
    file_size: int = 0
    file_hash: str = ""
    is_encrypted: bool = False
    has_text: bool = True
    language: str = ""


@dataclass
class ProcessedPdf:
    """Complete result of PDF processing."""
    metadata: PdfMetadata
    full_text: str
    pages: list[PageInfo] = field(default_factory=list)
    chunks: list[str] = field(default_factory=list)
    error: Optional[str] = None


class PdfProcessor:
    """
    Enhanced PDF processing pipeline.

    Handles:
    - Text extraction (PyPDF2)
    - Metadata extraction
    - Page-level analysis
    - Smart chunking with overlap
    - OCR fallback detection (flags when text extraction fails)
    """

    def __init__(self):
        self.chunk_size = settings.PDF_CHUNK_SIZE
        self.chunk_overlap = settings.PDF_CHUNK_OVERLAP
        self.max_pages = settings.MAX_PDF_PAGES

    def process(self, content: bytes, filename: str = "") -> ProcessedPdf:
        """
        Main processing pipeline. Extracts text, metadata, and chunks.
        Does NOT store anything permanently — returns data for the caller to use.
        """
        metadata = PdfMetadata(
            file_size=len(content),
            file_hash=hashlib.sha256(content).hexdigest()[:16],
        )

        try:
            reader = PdfReader(io.BytesIO(content))
        except Exception as e:
            logger.error(f"Failed to parse PDF '{filename}': {e}")
            return ProcessedPdf(
                metadata=metadata,
                full_text="",
                error=f"Failed to parse PDF: {str(e)}",
            )

        # Check encryption
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                metadata.is_encrypted = True
                return ProcessedPdf(
                    metadata=metadata,
                    full_text="",
                    error="PDF is encrypted and cannot be processed",
                )

        # Extract document metadata
        info = reader.metadata
        if info:
            metadata.title = str(info.get("/Title", "") or "")
            metadata.author = str(info.get("/Author", "") or "")
            metadata.subject = str(info.get("/Subject", "") or "")
            metadata.creator = str(info.get("/Creator", "") or "")
            metadata.producer = str(info.get("/Producer", "") or "")

        metadata.page_count = len(reader.pages)

        # Enforce page limit
        pages_to_process = min(len(reader.pages), self.max_pages)

        # Extract text page by page
        pages: list[PageInfo] = []
        all_text_parts: list[str] = []

        for i in range(pages_to_process):
            try:
                page = reader.pages[i]
                text = page.extract_text() or ""
                word_count = len(text.split())

                # Detect if page has images (heuristic)
                has_images = False
                if "/XObject" in (page.get("/Resources") or {}):
                    has_images = True

                page_info = PageInfo(
                    page_number=i + 1,
                    text=text,
                    word_count=word_count,
                    has_images=has_images,
                )
                pages.append(page_info)
                all_text_parts.append(text)

            except Exception as e:
                logger.warning(f"Error extracting page {i+1}: {e}")
                pages.append(PageInfo(page_number=i + 1, text="", word_count=0))

        full_text = "\n".join(all_text_parts)
        metadata.total_words = len(full_text.split())
        metadata.has_text = metadata.total_words > 10

        # If no text extracted, likely a scanned PDF (needs OCR)
        if not metadata.has_text:
            logger.info(f"PDF '{filename}' appears to be scanned (no extractable text), attempting Gemini OCR...")
            try:
                ocr_text = self._ocr_with_gemini(content, filename)
                if ocr_text and len(ocr_text.split()) > 10:
                    full_text = ocr_text
                    metadata.total_words = len(full_text.split())
                    metadata.has_text = True
                    logger.info(f"Gemini OCR extracted {metadata.total_words} words from '{filename}'")
                else:
                    logger.warning(f"Gemini OCR returned insufficient text for '{filename}'")
            except Exception as e:
                logger.error(f"Gemini OCR failed for '{filename}': {e}")

        # Chunk the text
        chunks = self.chunk_text(full_text)

        return ProcessedPdf(
            metadata=metadata,
            full_text=full_text,
            pages=pages,
            chunks=chunks,
        )

    def chunk_text(self, text: str) -> list[str]:
        """
        Split text into overlapping word-based chunks.
        Uses configurable chunk_size and chunk_overlap.
        """
        words = text.split()
        if not words:
            return []

        chunks: list[str] = []
        start = 0

        while start < len(words):
            end = start + self.chunk_size
            chunk = " ".join(words[start:end])
            if chunk.strip():
                chunks.append(chunk)

            # Move forward by (chunk_size - overlap)
            step = self.chunk_size - self.chunk_overlap
            if step <= 0:
                step = self.chunk_size  # Prevent infinite loop
            start += step

        return chunks

    def extract_text_only(self, content: bytes) -> str:
        """Quick text extraction without full processing."""
        try:
            reader = PdfReader(io.BytesIO(content))
            texts = []
            for page in reader.pages[:self.max_pages]:
                try:
                    texts.append(page.extract_text() or "")
                except Exception:
                    pass
            return "\n".join(texts)
        except Exception as e:
            logger.error(f"Text extraction failed: {e}")
            return ""

    def _ocr_with_gemini(self, content: bytes, filename: str) -> str:
        """
        Use Gemini's vision capability to extract text from a scanned/image-based PDF.
        Sends the PDF bytes directly to Gemini which can process PDF documents natively.
        """
        import os
        from google import genai
        from google.genai import types

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("GEMINI_API_KEY not set, cannot perform OCR")
            return ""

        client = genai.Client(api_key=api_key)

        prompt = (
            "Extract ALL text from this PDF document. "
            "The PDF contains scanned pages or images of text. "
            "Return only the extracted text content, preserving the original structure "
            "(paragraphs, headings, lists, tables). "
            "Do not add any commentary or explanation — only the text from the document."
        )

        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    types.Content(
                        parts=[
                            types.Part.from_bytes(data=content, mime_type="application/pdf"),
                            types.Part.from_text(text=prompt),
                        ]
                    )
                ],
                config=types.GenerateContentConfig(
                    temperature=0.0,
                ),
            )
            text = response.text or ""
            return text.strip()
        except Exception as e:
            logger.error(f"Gemini OCR API call failed: {e}")
            raise

    def get_page_text(self, content: bytes, page_number: int) -> str:
        """Extract text from a specific page (1-indexed)."""
        try:
            reader = PdfReader(io.BytesIO(content))
            if page_number < 1 or page_number > len(reader.pages):
                return ""
            return reader.pages[page_number - 1].extract_text() or ""
        except Exception as e:
            logger.error(f"Page text extraction failed: {e}")
            return ""


# Singleton instance
pdf_processor = PdfProcessor()
