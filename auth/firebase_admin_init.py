"""Firebase Admin SDK initialization."""
import os
import json
import logging
import firebase_admin
from firebase_admin import credentials, auth, firestore

logger = logging.getLogger(__name__)

_app = None
_db = None


def get_firebase_app():
    """Initialize and return Firebase Admin app (singleton)."""
    global _app
    if _app is not None:
        return _app

    cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase_credentials.json")
    cred_json = os.getenv("FIREBASE_CREDENTIALS_JSON")

    try:
        if cred_json:
            # Use JSON string from environment variable (for deployment)
            cred_dict = json.loads(cred_json)
            cred = credentials.Certificate(cred_dict)
        elif os.path.exists(cred_path):
            # Use file path (for local development)
            cred = credentials.Certificate(cred_path)
        else:
            raise FileNotFoundError(
                f"Firebase credentials not found. Set FIREBASE_CREDENTIALS_PATH or FIREBASE_CREDENTIALS_JSON"
            )

        _app = firebase_admin.initialize_app(cred)
        logger.info("Firebase Admin SDK initialized successfully")
        return _app
    except Exception as e:
        logger.error(f"Failed to initialize Firebase Admin SDK: {e}")
        raise


def get_firestore_client():
    """Get Firestore client (singleton)."""
    global _db
    if _db is not None:
        return _db

    get_firebase_app()
    _db = firestore.client()
    return _db


def verify_firebase_token(id_token: str) -> dict:
    """Verify a Firebase ID token and return the decoded claims.
    Also checks if the token has been revoked."""
    get_firebase_app()
    try:
        decoded_token = auth.verify_id_token(id_token, check_revoked=True)
        return decoded_token
    except auth.RevokedIdTokenError:
        raise ValueError("Firebase ID token has been revoked. Please sign in again.")
    except auth.InvalidIdTokenError:
        raise ValueError("Invalid Firebase ID token")
    except auth.ExpiredIdTokenError:
        raise ValueError("Firebase ID token has expired")
    except Exception as e:
        raise ValueError(f"Token verification failed: {str(e)}")


def create_custom_token(uid: str) -> str:
    """Create a Firebase custom token for a user."""
    get_firebase_app()
    custom_token = auth.create_custom_token(uid)
    return custom_token.decode("utf-8") if isinstance(custom_token, bytes) else custom_token


def get_or_create_user(email: str) -> str:
    """Get existing Firebase user by email, or create a new one. Returns UID."""
    get_firebase_app()
    try:
        user = auth.get_user_by_email(email)
        return user.uid
    except auth.UserNotFoundError:
        user = auth.create_user(email=email, email_verified=True)
        return user.uid
