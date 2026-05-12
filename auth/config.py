"""Authentication configuration constants."""
import os
from dotenv import load_dotenv

load_dotenv()

# OTP Configuration
OTP_LENGTH = 6
OTP_EXPIRY_MINUTES = 5
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN_SECONDS = 60

# Rate Limiting
MAX_OTP_REQUESTS_PER_HOUR = 10
MAX_AI_REQUESTS_FREE_DAILY = 20
AI_REQUEST_COOLDOWN_SECONDS = 5

# Firebase
FIREBASE_CREDENTIALS_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase_credentials.json")

# Email (SMTP - fallback)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Smart PDF")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USER)

# Email (Resend - preferred for cloud platforms like Render)
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "resend")  # "resend" or "smtp"
