"""Authentication API routes for FastAPI - Email/Password with OTP verification."""
import re
import logging
import hashlib
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
    get_firestore_client,
)
from auth.user_service import create_or_update_user, get_user_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


# --- Request/Response Models ---


class SignUpRequest(BaseModel):
    first_name: str
    last_name: str
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        v = v.strip().lower()
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(pattern, v):
            raise ValueError("Invalid email format")
        return v

    @field_validator("first_name")
    @classmethod
    def validate_first_name(cls, v):
        v = v.strip()
        if len(v) < 2:
            raise ValueError("First name must be at least 2 characters")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters")
        return v


class SignInRequest(BaseModel):
    email: str
    password: str

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
    first_name: str | None = None
    last_name: str | None = None
    subscription_plan: str
    ai_daily_limit: int
    ai_used_today: int
    account_status: str


# --- Helpers ---


def _hash_password(password: str) -> str:
    """Hash password with SHA-256."""
    return hashlib.sha256(password.encode()).hexdigest()


# --- Routes ---


@router.post("/signup", response_model=AuthResponse)
async def signup(request: SignUpRequest):
    """Register a new user and send OTP for email verification."""
    email = request.email
    logger.info(f"[SIGNUP] Request received for email: {email}")

    db = get_firestore_client()

    # Check if user already exists and is verified
    pending_ref = db.collection("pending_signups").document(email)
    users_query = db.collection("users").where("email", "==", email).limit(1).get()

    if users_query:
        existing_user = users_query[0].to_dict()
        if existing_user.get("email_verified", False):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists. Please sign in.",
            )

    # Rate limiting
    if not check_otp_rate_limit(email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    # Store pending signup data
    pending_ref.set({
        "first_name": request.first_name.strip(),
        "last_name": request.last_name.strip(),
        "email": email,
        "password_hash": _hash_password(request.password),
        "created_at": None,  # Will set on verification
    })

    # Generate and send OTP
    otp = generate_otp()
    stored = await store_otp(email, otp)
    if not stored:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Please wait 60 seconds before requesting a new OTP.",
        )

    sent = await send_otp_email(email, otp)
    if not sent:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP email. Please try again.",
        )

    logger.info(f"[SIGNUP] OTP sent to {email}")
    return AuthResponse(
        success=True,
        message="OTP sent successfully. Please verify your email.",
    )


@router.post("/verify-signup-otp", response_model=AuthResponse)
async def verify_signup_otp(request: VerifyOtpRequest):
    """Verify OTP for signup and create the user account."""
    email = request.email
    otp = request.otp
    logger.info(f"[VERIFY_SIGNUP] Verification request for email: {email}")

    # Verify OTP
    result = await verify_stored_otp(email, otp)
    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["error"],
        )

    # Get pending signup data
    db = get_firestore_client()
    pending_ref = db.collection("pending_signups").document(email)
    pending_doc = pending_ref.get()

    if not pending_doc.exists:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Signup session expired. Please sign up again.",
        )

    pending_data = pending_doc.to_dict()

    # Create Firebase Auth user
    uid = get_or_create_user(email)

    # Create user profile in Firestore
    full_name = f"{pending_data['first_name']} {pending_data['last_name']}".strip()
    await create_or_update_user(
        uid=uid,
        email=email,
        auth_provider="email_password",
        name=full_name,
    )

    # Store credentials for sign-in
    cred_ref = db.collection("user_credentials").document(uid)
    cred_ref.set({
        "uid": uid,
        "email": email,
        "password_hash": pending_data["password_hash"],
        "first_name": pending_data["first_name"],
        "last_name": pending_data["last_name"],
        "email_verified": True,
    })

    # Update user doc with name fields
    user_ref = db.collection("users").document(uid)
    user_ref.update({
        "first_name": pending_data["first_name"],
        "last_name": pending_data["last_name"],
        "email_verified": True,
    })

    # Clean up pending signup
    pending_ref.delete()

    # Generate custom token for auto sign-in
    custom_token = create_custom_token(uid)

    logger.info(f"[VERIFY_SIGNUP] Account created for {email}")
    return AuthResponse(
        success=True,
        message="Account created successfully.",
        custom_token=custom_token,
        uid=uid,
    )


@router.post("/signin", response_model=AuthResponse)
async def signin(request: SignInRequest):
    """Sign in with email and password."""
    email = request.email
    logger.info(f"[SIGNIN] Request received for email: {email}")

    db = get_firestore_client()

    # Find user credentials by email
    creds_query = db.collection("user_credentials").where("email", "==", email).limit(1).get()

    if not creds_query:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    cred_data = creds_query[0].to_dict()

    # Verify password
    if cred_data["password_hash"] != _hash_password(request.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    # Check if email is verified
    if not cred_data.get("email_verified", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email first.",
        )

    uid = cred_data["uid"]

    # Update last login
    await create_or_update_user(uid=uid, email=email, auth_provider="email_password")

    # Generate custom token
    custom_token = create_custom_token(uid)

    logger.info(f"[SIGNIN] Successful sign-in for {email}")
    return AuthResponse(
        success=True,
        message="Sign in successful.",
        custom_token=custom_token,
        uid=uid,
    )


class ResendOtpRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        return v.strip().lower()


@router.post("/resend-otp", response_model=AuthResponse)
async def resend_otp_endpoint(request: ResendOtpRequest):
    """Resend OTP to email."""
    email = request.email
    logger.info(f"[RESEND_OTP] Request for email: {email}")

    if not check_otp_rate_limit(email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many OTP requests. Please try again later.",
        )

    otp = generate_otp()
    stored = await store_otp(email, otp)
    if not stored:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Please wait 60 seconds before requesting a new OTP.",
        )

    sent = await send_otp_email(email, otp)
    if not sent:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send OTP email. Please try again.",
        )

    return AuthResponse(
        success=True,
        message="OTP sent successfully.",
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
        first_name=profile.get("first_name"),
        last_name=profile.get("last_name"),
        subscription_plan=profile.get("subscription_plan", "free"),
        ai_daily_limit=profile.get("ai_daily_limit", 20),
        ai_used_today=profile.get("ai_used_today", 0),
        account_status=profile.get("account_status", "active"),
    )
