"""
Firebase App Check verification for FastAPI.

Verifies the ``X-Firebase-AppCheck`` header using the Firebase Admin SDK.
Rejects requests from clients that cannot prove they are a genuine app instance.

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

# Environment toggle — allows disabling App Check in development
_APP_CHECK_ENABLED = os.getenv("APP_CHECK_ENABLED", "true").lower() == "true"


async def verify_app_check(
    x_firebase_appcheck: str = Header(default=""),
) -> dict | None:
    """
    FastAPI dependency that verifies the Firebase App Check token.

    Returns the decoded App Check claims on success, or ``None`` when
    App Check is disabled (development only).

    Raises 403 if the token is missing or invalid.
    """
    if not _APP_CHECK_ENABLED:
        return None

    if not x_firebase_appcheck:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing App Check token. Please update the app.",
        )

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
        logger.warning(f"App Check token invalid: {exc}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid App Check token. Unauthorized client.",
        )
    except Exception as exc:
        logger.error(f"App Check verification failed: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="App Check verification failed.",
        )
