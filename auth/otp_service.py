"""OTP generation, hashing, and email sending."""
import hashlib
import secrets
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
import asyncio
from concurrent.futures import ThreadPoolExecutor

from auth.config import (
    OTP_LENGTH,
    OTP_EXPIRY_MINUTES,
    OTP_MAX_ATTEMPTS,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
    SMTP_FROM_NAME,
    SMTP_FROM_EMAIL,
)
from auth.firebase_admin_init import get_firestore_client

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=5)


def generate_otp() -> str:
    """Generate a secure random OTP."""
    return "".join([str(secrets.randbelow(10)) for _ in range(OTP_LENGTH)])


def hash_otp(otp: str) -> str:
    """Hash OTP using SHA-256 for secure storage."""
    return hashlib.sha256(otp.encode()).hexdigest()


def verify_otp_hash(otp: str, otp_hash: str) -> bool:
    """Verify OTP against its hash."""
    return hashlib.sha256(otp.encode()).hexdigest() == otp_hash


async def store_otp(email: str, otp: str) -> bool:
    """Store hashed OTP in Firestore with expiry and attempt tracking."""
    db = get_firestore_client()
    otp_ref = db.collection("otp_verifications").document(email)

    logger.debug(f"[OTP_STORE] Storing OTP for email: {email}")

    # Check rate limiting - max requests per hour
    try:
        existing = otp_ref.get()
        if existing.exists:
            data = existing.to_dict()
            created_at = data.get("created_at")
            if created_at:
                # Prevent rapid re-requests (60s cooldown)
                cooldown_threshold = datetime.now(timezone.utc) - timedelta(seconds=60)
                if created_at > cooldown_threshold:
                    logger.warning(f"[OTP_STORE] Rate limit hit for {email}. Created at: {created_at}")
                    return False
    except Exception as e:
        logger.error(f"[OTP_STORE] Error checking existing OTP: {e}")
        return False

    otp_hash = hash_otp(otp)
    otp_data = {
        "email": email,
        "otp_hash": otp_hash,
        "expiry_time": datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES),
        "attempt_count": 0,
        "created_at": datetime.now(timezone.utc),
        "verified": False,
    }
    try:
        otp_ref.set(otp_data)
        logger.debug(f"[OTP_STORE] OTP stored successfully for {email}. Expires in {OTP_EXPIRY_MINUTES} minutes")
        return True
    except Exception as e:
        logger.error(f"[OTP_STORE] Failed to store OTP in Firestore: {e}")
        return False


async def verify_stored_otp(email: str, otp: str) -> dict:
    """
    Verify OTP from Firestore.
    Returns: {"success": bool, "error": str | None}
    """
    logger.debug(f"[OTP_VERIFY] Attempting to verify OTP for email: {email}")
    db = get_firestore_client()
    otp_ref = db.collection("otp_verifications").document(email)
    
    try:
        doc = otp_ref.get()
    except Exception as e:
        logger.error(f"[OTP_VERIFY] Error retrieving OTP: {e}")
        return {"success": False, "error": "Error verifying OTP. Please try again."}

    if not doc.exists:
        logger.warning(f"[OTP_VERIFY] No OTP found for {email}")
        return {"success": False, "error": "No OTP found. Please request a new one."}

    data = doc.to_dict()
    logger.debug(f"[OTP_VERIFY] OTP record found. Data keys: {data.keys()}")

    # Check if already verified
    if data.get("verified"):
        logger.warning(f"[OTP_VERIFY] OTP already used for {email}")
        return {"success": False, "error": "OTP already used. Please request a new one."}

    # Check expiry
    expiry_time = data.get("expiry_time")
    if expiry_time and datetime.now(timezone.utc) > expiry_time:
        logger.warning(f"[OTP_VERIFY] OTP expired for {email}. Expiry: {expiry_time}")
        otp_ref.delete()
        return {"success": False, "error": "OTP has expired. Please request a new one."}

    # Check attempt limit
    attempt_count = data.get("attempt_count", 0)
    if attempt_count >= OTP_MAX_ATTEMPTS:
        logger.warning(f"[OTP_VERIFY] Max attempts exceeded for {email}. Attempts: {attempt_count}")
        otp_ref.delete()
        return {"success": False, "error": "Too many failed attempts. Please request a new OTP."}

    # Increment attempt count
    try:
        otp_ref.update({"attempt_count": attempt_count + 1})
        logger.debug(f"[OTP_VERIFY] Attempt count incremented to {attempt_count + 1}")
    except Exception as e:
        logger.error(f"[OTP_VERIFY] Error updating attempt count: {e}")

    # Verify OTP hash
    stored_hash = data.get("otp_hash", "")
    if not verify_otp_hash(otp, stored_hash):
        remaining = OTP_MAX_ATTEMPTS - (attempt_count + 1)
        logger.warning(f"[OTP_VERIFY] Invalid OTP provided for {email}. Remaining attempts: {remaining}")
        return {"success": False, "error": f"Invalid OTP. {remaining} attempts remaining."}

    # Mark as verified
    try:
        otp_ref.update({"verified": True})
        logger.info(f"[OTP_VERIFY] OTP verified successfully for {email}")
        return {"success": True, "error": None}
    except Exception as e:
        logger.error(f"[OTP_VERIFY] Error marking OTP as verified: {e}")
        return {"success": False, "error": "Error verifying OTP. Please try again."}


def _send_otp_email_sync(email: str, otp: str) -> bool:
    """Send OTP email using SMTP (synchronous)."""
    logger.debug(f"[OTP_EMAIL] Starting email send process for {email}")
    
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.error(f"[OTP_EMAIL] SMTP credentials not configured. SMTP_USER: {bool(SMTP_USER)}, SMTP_PASSWORD: {bool(SMTP_PASSWORD)}")
        return False

    logger.debug(f"[OTP_EMAIL] SMTP Config - Host: {SMTP_HOST}, Port: {SMTP_PORT}, From: {SMTP_FROM_EMAIL}")

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Your Smart PDF Verification Code: {otp}"
        msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
        msg["To"] = email

        logger.debug(f"[OTP_EMAIL] Building email message for {email}")

        html_body = f"""
        <html>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 20px; background-color: #f5f5f5;">
            <div style="max-width: 500px; margin: 0 auto; background: white; border-radius: 16px; padding: 40px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
                <h2 style="color: #1a1a1a; text-align: center; margin-bottom: 8px;">Verification Code</h2>
                <p style="color: #666; text-align: center; margin-bottom: 32px;">Enter this code to verify your email</p>
                <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 12px; padding: 24px; text-align: center; margin-bottom: 24px;">
                    <span style="font-size: 36px; font-weight: bold; letter-spacing: 8px; color: white;">{otp}</span>
                </div>
                <p style="color: #999; text-align: center; font-size: 14px;">This code expires in {OTP_EXPIRY_MINUTES} minutes.</p>
                <p style="color: #999; text-align: center; font-size: 12px; margin-top: 24px;">If you didn't request this code, please ignore this email.</p>
            </div>
        </body>
        </html>
        """

        msg.attach(MIMEText(html_body, "html"))
        logger.debug(f"[OTP_EMAIL] Email message built, attempting SMTP connection to {SMTP_HOST}:{SMTP_PORT}")

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            logger.debug(f"[OTP_EMAIL] Connected to SMTP server")
            server.starttls()
            logger.debug(f"[OTP_EMAIL] TLS enabled")
            server.login(SMTP_USER, SMTP_PASSWORD)
            logger.debug(f"[OTP_EMAIL] Logged in with SMTP user")
            server.sendmail(SMTP_FROM_EMAIL, email, msg.as_string())
            logger.debug(f"[OTP_EMAIL] Email sent via SMTP")

        logger.info(f"[OTP_EMAIL] OTP email sent successfully to {email}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"[OTP_EMAIL] SMTP authentication failed: {e}. Check SMTP_USER and SMTP_PASSWORD")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"[OTP_EMAIL] SMTP error: {e}")
        return False
    except Exception as e:
        logger.error(f"[OTP_EMAIL] Failed to send OTP email: {type(e).__name__}: {e}", exc_info=True)
        return False


async def send_otp_email(email: str, otp: str) -> bool:
    """Send OTP email using SMTP (async wrapper)."""
    logger.debug(f"[OTP_EMAIL] Async wrapper called for {email}")
    try:
        # Run blocking SMTP operation in thread pool
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(_executor, _send_otp_email_sync, email, otp)
        logger.debug(f"[OTP_EMAIL] Email send result: {result}")
        return result
    except Exception as e:
        logger.error(f"[OTP_EMAIL] Async wrapper error: {e}", exc_info=True)
        return False


async def cleanup_expired_otps():
    """Clean up expired OTP records from Firestore."""
    logger.debug(f"[OTP_CLEANUP] Starting cleanup of expired OTPs")
    try:
        db = get_firestore_client()
        now = datetime.now(timezone.utc)
        expired = (
            db.collection("otp_verifications")
            .where("expiry_time", "<", now)
            .stream()
        )
        count = 0
        for doc in expired:
            doc.reference.delete()
            count += 1
        logger.info(f"[OTP_CLEANUP] Deleted {count} expired OTP records")
    except Exception as e:
        logger.error(f"[OTP_CLEANUP] Error during cleanup: {e}", exc_info=True)
