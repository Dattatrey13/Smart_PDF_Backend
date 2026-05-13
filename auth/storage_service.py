"""Firebase Storage utilities for backend PDF processing and validation."""
import os
import logging
from datetime import datetime, timezone

from auth.firebase_admin_init import get_firebase_app

logger = logging.getLogger(__name__)

# Allowed MIME types
ALLOWED_PDF_TYPES = {"application/pdf"}
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# Size limits (bytes) — these are absolute maximums; tier-specific limits
# are enforced at the dependency / route layer via TierLimits.
MAX_PDF_SIZE = 100 * 1024 * 1024  # 100 MB (premium ceiling)
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_THUMBNAIL_SIZE = 1 * 1024 * 1024  # 1 MB

# Dangerous file signatures (magic bytes) to reject
_DANGEROUS_SIGNATURES = [
    b'\x4d\x5a',  # EXE/DLL
    b'\x7f\x45\x4c\x46',  # ELF
    b'\x23\x21',  # Shell scripts
    b'\x50\x4b\x03\x04',  # ZIP (could be disguised)
]

# PDF magic bytes
_PDF_SIGNATURE = b'%PDF'


def validate_pdf_upload(content: bytes, filename: str) -> tuple[bool, str]:
    """
    Validate a PDF upload on the backend.
    Returns (is_valid, error_message).
    """
    # Check file size
    if len(content) == 0:
        return False, "File is empty"
    if len(content) > MAX_PDF_SIZE:
        return False, f"PDF exceeds maximum size of {MAX_PDF_SIZE // (1024*1024)}MB"

    # Check file extension
    if not filename.lower().endswith('.pdf'):
        return False, "Only PDF files are allowed"

    # Verify PDF magic bytes
    if not content[:4].startswith(_PDF_SIGNATURE):
        return False, "File does not appear to be a valid PDF"

    # Check for dangerous embedded signatures
    for sig in _DANGEROUS_SIGNATURES:
        if content[:len(sig)] == sig:
            return False, "File type not allowed"

    return True, ""


def validate_image_upload(
    content: bytes, filename: str, is_thumbnail: bool = False
) -> tuple[bool, str]:
    """
    Validate an image upload on the backend.
    Returns (is_valid, error_message).
    """
    max_size = MAX_THUMBNAIL_SIZE if is_thumbnail else MAX_IMAGE_SIZE

    if len(content) == 0:
        return False, "File is empty"
    if len(content) > max_size:
        label = "Thumbnail" if is_thumbnail else "Image"
        return False, f"{label} exceeds maximum size of {max_size // (1024*1024)}MB"

    # Check extension
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
        return False, "Only JPEG, PNG, WebP, and GIF images are allowed"

    # Check magic bytes for common image formats
    if content[:2] == b'\xff\xd8':  # JPEG
        pass
    elif content[:8] == b'\x89PNG\r\n\x1a\n':  # PNG
        pass
    elif content[:4] == b'RIFF' and content[8:12] == b'WEBP':  # WebP
        pass
    elif content[:6] in (b'GIF87a', b'GIF89a'):  # GIF
        pass
    else:
        return False, "File does not match a supported image format"

    return True, ""


def generate_storage_path(uid: str, filename: str, folder: str = "pdfs") -> str:
    """
    Generate a secure storage path for a file upload.
    Format: users/{uid}/{folder}/{sanitized_filename}
    """
    # Sanitize filename
    safe_name = _sanitize_filename(filename)
    return f"users/{uid}/{folder}/{safe_name}"


def generate_unique_storage_path(uid: str, filename: str, folder: str = "pdfs") -> str:
    """
    Generate a unique storage path with timestamp to avoid collisions.
    Format: users/{uid}/{folder}/{timestamp}_{sanitized_filename}
    """
    safe_name = _sanitize_filename(filename)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"users/{uid}/{folder}/{timestamp}_{safe_name}"


def _sanitize_filename(filename: str) -> str:
    """Remove dangerous characters from filename."""
    # Get base name (no path traversal)
    name = os.path.basename(filename)
    # Remove special characters except dots, dashes, underscores
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
    # Remove leading dots (hidden files)
    safe = safe.lstrip(".")
    # Ensure non-empty
    if not safe:
        safe = f"file_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    return safe


def get_firebase_storage_bucket():
    """Get the Firebase Storage bucket reference (Admin SDK)."""
    from firebase_admin import storage
    get_firebase_app()
    return storage.bucket()


async def generate_signed_url(storage_path: str, expiry_hours: int = 1) -> str | None:
    """
    Generate a signed download URL for a storage file (for backend-to-backend use).
    """
    try:
        bucket = get_firebase_storage_bucket()
        blob = bucket.blob(storage_path)
        if not blob.exists():
            return None

        from datetime import timedelta
        url = blob.generate_signed_url(
            expiration=timedelta(hours=expiry_hours),
            method="GET",
        )
        return url
    except Exception as e:
        logger.error(f"Failed to generate signed URL: {e}")
        return None


async def delete_user_storage(uid: str) -> bool:
    """Delete all files for a user from Firebase Storage."""
    try:
        bucket = get_firebase_storage_bucket()
        prefix = f"users/{uid}/"
        blobs = bucket.list_blobs(prefix=prefix)
        for blob in blobs:
            blob.delete()
        logger.info(f"Deleted all storage files for user {uid}")
        return True
    except Exception as e:
        logger.error(f"Failed to delete user storage: {e}")
        return False
