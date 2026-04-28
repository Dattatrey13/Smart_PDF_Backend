"""Redis-backed cache service.

Falls back gracefully to an in-memory dict when Redis is unavailable,
so the app still works during local development without Redis.
"""

import json
import logging
from typing import Any, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_redis_client = None
_fallback_cache: dict = {}


async def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis.asyncio as aioredis

        _redis_client = aioredis.from_url(
            settings.REDIS_URL, decode_responses=True
        )
        await _redis_client.ping()
        logger.info("Connected to Redis at %s", settings.REDIS_URL)
        return _redis_client
    except Exception as exc:
        logger.warning("Redis unavailable (%s) – using in-memory fallback", exc)
        _redis_client = None
        return None


async def cache_get(key: str) -> Optional[Any]:
    r = await _get_redis()
    if r:
        try:
            val = await r.get(key)
            return json.loads(val) if val else None
        except Exception:
            return None
    return _fallback_cache.get(key)


async def cache_set(key: str, value: Any, ttl: int = 0) -> None:
    ttl = ttl or settings.CACHE_TTL_SECONDS
    r = await _get_redis()
    if r:
        try:
            await r.set(key, json.dumps(value), ex=ttl)
            return
        except Exception:
            pass
    _fallback_cache[key] = value


async def cache_delete(key: str) -> None:
    r = await _get_redis()
    if r:
        try:
            await r.delete(key)
            return
        except Exception:
            pass
    _fallback_cache.pop(key, None)
