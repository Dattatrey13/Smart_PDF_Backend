"""Central application configuration. All settings are loaded from environment variables."""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Application-wide settings loaded from environment."""

    # ─── App ─────────────────────────────────────────────────────────────────
    APP_NAME: str = "Smart PDF Backend"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "production")  # development | staging | production

    # ─── Server ──────────────────────────────────────────────────────────────
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    WORKERS: int = int(os.getenv("WORKERS", "1"))

    # ─── CORS ────────────────────────────────────────────────────────────────
    # Comma-separated list of allowed origins; "*" allows all (dev only)
    ALLOWED_ORIGINS: list[str] = [
        o.strip()
        for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")
        if o.strip()
    ]

    # ─── Security ────────────────────────────────────────────────────────────
    # Maximum request body size (bytes) — 55 MB (slightly above 50MB PDF limit)
    MAX_REQUEST_SIZE: int = int(os.getenv("MAX_REQUEST_SIZE", str(55 * 1024 * 1024)))
    # Trusted proxy headers (for Render, Railway, etc.)
    TRUSTED_HOSTS: list[str] = [
        h.strip()
        for h in os.getenv("TRUSTED_HOSTS", "*").split(",")
        if h.strip()
    ]
    # API key for admin endpoints (optional)
    ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "")

    # ─── Rate Limiting ───────────────────────────────────────────────────────
    GLOBAL_RATE_LIMIT_PER_MINUTE: int = int(os.getenv("GLOBAL_RATE_LIMIT_PER_MINUTE", "60"))
    UPLOAD_RATE_LIMIT_PER_HOUR: int = int(os.getenv("UPLOAD_RATE_LIMIT_PER_HOUR", "20"))

    # ─── AI ──────────────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    AI_MODEL: str = os.getenv("AI_MODEL", "models/gemini-2.5-flash")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "models/gemini-embedding-001")
    AI_MAX_CONTEXT_CHUNKS: int = int(os.getenv("AI_MAX_CONTEXT_CHUNKS", "10"))
    AI_CHUNK_SIZE: int = int(os.getenv("AI_CHUNK_SIZE", "400"))

    # ─── Firebase ────────────────────────────────────────────────────────────
    FIREBASE_CREDENTIALS_PATH: str = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase_credentials.json")
    FIREBASE_CREDENTIALS_JSON: str = os.getenv("FIREBASE_CREDENTIALS_JSON", "")
    FIREBASE_STORAGE_BUCKET: str = os.getenv("FIREBASE_STORAGE_BUCKET", "")

    # ─── Email / SMTP ────────────────────────────────────────────────────────
    SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_NAME: str = os.getenv("SMTP_FROM_NAME", "Smart PDF")
    SMTP_FROM_EMAIL: str = os.getenv("SMTP_FROM_EMAIL", os.getenv("SMTP_USER", ""))

    # ─── OTP ─────────────────────────────────────────────────────────────────
    OTP_LENGTH: int = 6
    OTP_EXPIRY_MINUTES: int = 5
    OTP_MAX_ATTEMPTS: int = 5
    OTP_RESEND_COOLDOWN_SECONDS: int = 60
    MAX_OTP_REQUESTS_PER_HOUR: int = 10

    # ─── AI Usage Limits ─────────────────────────────────────────────────────
    MAX_AI_REQUESTS_FREE_DAILY: int = int(os.getenv("MAX_AI_REQUESTS_FREE_DAILY", "20"))
    AI_REQUEST_COOLDOWN_SECONDS: int = int(os.getenv("AI_REQUEST_COOLDOWN_SECONDS", "5"))

    # ─── PDF Processing ──────────────────────────────────────────────────────
    MAX_PDF_SIZE: int = 50 * 1024 * 1024  # 50 MB
    MAX_PDF_PAGES: int = int(os.getenv("MAX_PDF_PAGES", "500"))
    PDF_CHUNK_SIZE: int = int(os.getenv("PDF_CHUNK_SIZE", "400"))
    PDF_CHUNK_OVERLAP: int = int(os.getenv("PDF_CHUNK_OVERLAP", "50"))

    # ─── Caching ─────────────────────────────────────────────────────────────
    CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", "3600"))  # 1 hour
    CACHE_MAX_SIZE: int = int(os.getenv("CACHE_MAX_SIZE", "500"))

    # ─── Logging ─────────────────────────────────────────────────────────────
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT: str = os.getenv("LOG_FORMAT", "json")  # json | text


settings = Settings()
