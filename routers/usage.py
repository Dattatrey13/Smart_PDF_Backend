"""Usage status endpoint — returns current quotas & usage for the Flutter client."""
import logging
from fastapi import APIRouter, Depends

from auth.dependencies import get_current_user
from auth.user_service import get_user_profile
from services.usage_service import get_usage_status
from models.usage_schemas import UsageStatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/usage", tags=["Usage"])


@router.get("/status", response_model=UsageStatusResponse)
async def usage_status(current_user: dict = Depends(get_current_user)):
    """
    Return the caller's current usage counters and plan limits.
    The Flutter client uses this to display remaining quota badges.
    """
    uid = current_user.get("uid")
    profile = await get_user_profile(uid)
    plan = (profile or {}).get("subscription_plan", "free")

    status = await get_usage_status(uid, plan)
    return UsageStatusResponse(**status)
