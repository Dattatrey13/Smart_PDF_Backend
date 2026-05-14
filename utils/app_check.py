"""
Firebase App Check verification for FastAPI.

Verifies the ``X-Firebase-AppCheck`` header using the Firebase Admin SDK.
Rejects requests from clients that cannot prove they are a genuine app instance.

Enforcement modes (APP_CHECK_ENFORCEMENT env var):
  • "strict"     — reject missing / invalid tokens with 403
  • "permissive" — log a warning but allow the request through when
                    the client is already authenticated via Firebase Auth

Usage:
    from utils.app_check import verify_app_check

    @router.post("/protected")
    async def protected(app_check=Depends(verify_app_check)):
        ...
"""
from __future__ import annotations

import logging
import os

from fastapi import Header, HTTPException, status

logger = logging.getLogger(__name__)

# Environment toggles
_APP_CHECK_ENABLED = os.getenv("APP_CHECK_ENABLED", "true").lower() == "true"
_APP_CHECK_ENFORCEMENT = os.getenv("APP_CHECK_ENFORCEMENT", "permissive").lower()


async def verify_app_check(
    x_firebase_appcheck: str = Header(default=""),
) -> dict | None:
    """
    FastAPI dependency that verifies the Firebase App Check token.

    Returns the decoded App Check claims on success, or ``None`` when
    App Check is disabled or the token is missing in permissive mode.

    In strict mode, raises 403 if the token is missing or invalid.
    In permissive mode, logs a warning and returns None so that
    Firebase-authenticated requests are not blocked by transient
    App Check failures on the client side.
    """
    if not _APP_CHECK_ENABLED:
        return None

    is_strict = _APP_CHECK_ENFORCEMENT == "strict"

    if not x_firebase_appcheck:
        if is_strict:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing App Check token. Please update the app.",
            )
        logger.warning("App Check token missing (permissive mode — allowing request)")
        return None

    try:
        from firebase_admin import app_check as fb_app_check
        from auth.firebase_admin_init import get_firebase_app

        get_firebase_app()  # ensure initialised
        decoded = fb_app_check.verify_token(x_firebase_appcheck)
        return decoded

    except ImportError:
        logger.error("firebase_admin.app_check not available — upgrade firebase-admin>=6.2")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="App Check verification unavailable.",
        )
    except ValueError as exc:
        if is_strict:
            logger.warning(f"App Check token invalid: {exc}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid App Check token. Unauthorized client.",
            )
        logger.warning(f"App Check token invalid (permissive mode — allowing request): {exc}")
        return None
    except Exception as exc:
        if is_strict:
            logger.error(f"App Check verification failed: {exc}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="App Check verification failed.",
            )
        logger.warning(f"App Check verification error (permissive mode — allowing request): {exc}")
        return None
