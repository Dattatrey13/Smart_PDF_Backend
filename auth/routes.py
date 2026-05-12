"""Authentication API routes for FastAPI - OTP-only email verification via Firebase."""
import re
import logging
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, field_validator

from auth.otp_service import (
    generate_otp,
    store_otp,
    verify_stored_otp,
    send_otp_email,
)
from auth.rate_limiter import check_otp_rate_limit
from auth.firebase_admin_init import (
    get_or_create_user,
    create_custom_token,
    verify_firebase_token,
)
from auth.user_service import create_or_update_user, get_user_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


# --- Request/Response Models ---


class SendOtpRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        v = v.strip().lower()
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(pattern, v):
            raise ValueError("Invalid email format")
        return v


class VerifyOtpRequest(BaseModel):
    email: str
    otp: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        return v.strip().lower()

    @field_validator("otp")
    @classmethod
    def validate_otp(cls, v):
        v = v.strip()
        if not v.isdigit() or len(v) != 6:
            raise ValueError("OTP must be a 6-digit number")
        return v


class AuthResponse(BaseModel):
    success: bool
    message: str
    custom_token: str | None = None
    uid: str | None = None


class UserProfileResponse(BaseModel):
    uid: str
    email: str
    name: str | None = None
    subscription_plan: str
    ai_daily_limit: int
    ai_used_today: int
    account_status: str


# --- Routes ---


@router.post("/send-otp", response_model=AuthResponse)
async def send_otp(request: SendOtpRequest):
    """Send OTP to user's email for verification."""
    email = request.email
    logger.info(f"[SEND_OTP] Request received for email: {email}")

    # Rate limiting
    if not check_otp_rate_limit(email):
        logger.warning(f"[SEND_OTP] Rate limit exceeded for {email}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP requests. Please try again later.",
        )

    # Generate OTP
    otp = generate_otp()

    # Store OTP (checks cooldown internally)
    stored = await store_otp(email, otp)
    if not stored:
        logger.warning(f"[SEND_OTP] Failed to store OTP for {email} (rate limited)")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Please wait 60 seconds before requesting a new OTP.",
        )

    # Send OTP email
    sent = await send_otp_email(email, otp)
    if not sent:
        logger.error(f"[SEND_OTP] Failed to send email to {email}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP email. Please try again.",
        )

    logger.info(f"[SEND_OTP] OTP sent successfully to {email}")
    return AuthResponse(
        success=True,
        message="OTP sent successfully. Check your email.",
    )


@router.post("/verify-otp", response_model=AuthResponse)
async def verify_otp(request: VerifyOtpRequest):
    """Verify OTP and return Firebase custom token."""
    email = request.email
    otp = request.otp
    logger.info(f"[VERIFY_OTP] Verification request for email: {email}")

    # Verify OTP
    result = await verify_stored_otp(email, otp)
    if not result["success"]:
        logger.warning(f"[VERIFY_OTP] OTP verification failed for {email}: {result['error']}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["error"],
        )

    # OTP verified - create/get Firebase user
    uid = get_or_create_user(email)

    # Create/update user profile in Firestore
    await create_or_update_user(uid=uid, email=email, auth_provider="email_otp")

    # Generate custom token for client sign-in
    custom_token = create_custom_token(uid)

    logger.info(f"[VERIFY_OTP] Verification successful for {email}")
    return AuthResponse(
        success=True,
        message="Email verified successfully.",
        custom_token=custom_token,
        uid=uid,
    )


@router.get("/profile", response_model=UserProfileResponse)
async def get_profile(authorization: str = None):
    """Get authenticated user's profile."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization required",
        )

    token = authorization[7:]
    try:
        decoded_token = verify_firebase_token(token)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )

    uid = decoded_token.get("uid")
    profile = await get_user_profile(uid)

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        )

    return UserProfileResponse(
        uid=profile.get("uid", uid),
        email=profile.get("email", ""),
        name=profile.get("name"),
        subscription_plan=profile.get("subscription_plan", "free"),
        ai_daily_limit=profile.get("ai_daily_limit", 20),
        ai_used_today=profile.get("ai_used_today", 0),
        account_status=profile.get("account_status", "active"),
    )
