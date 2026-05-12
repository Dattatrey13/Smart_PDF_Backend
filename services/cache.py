"""In-memory LRU cache with TTL for AI response caching."""
import time
import hashlib
import logging
from collections import OrderedDict
from typing import Any, Optional

from config import settings

logger = logging.getLogger(__name__)


class CacheEntry:
    """Single cache entry with value and expiry."""
    __slots__ = ("value", "expires_at", "created_at")

    def __init__(self, value: Any, ttl: int):
        self.value = value
        self.created_at = time.time()
        self.expires_at = self.created_at + ttl


class ResponseCache:
    """
    Thread-safe LRU cache with TTL eviction.

    Used for:
    - Caching AI responses (same question → same answer)
    - Caching embedding results
    - Reducing redundant API calls to Gemini/OpenAI

    NOT used for:
    - User sessions
    - Long-term storage
    - Anything that must survive restart
    """

    def __init__(self, max_size: int = None, default_ttl: int = None):
        self._max_size = max_size or settings.CACHE_MAX_SIZE
        self._default_ttl = default_ttl or settings.CACHE_TTL_SECONDS
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        """Get a value from cache. Returns None if expired or missing."""
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None

        # Check expiry
        if time.time() > entry.expires_at:
            del self._store[key]
            self._misses += 1
            return None

        # Move to end (most recently used)
        self._store.move_to_end(key)
        self._hits += 1
        return entry.value

    def set(self, key: str, value: Any, ttl: int = None) -> None:
        """Store a value in cache with TTL."""
        effective_ttl = ttl if ttl is not None else self._default_ttl

        # Remove old entry if exists
        if key in self._store:
            del self._store[key]

        # Evict oldest if at capacity
        while len(self._store) >= self._max_size:
            self._store.popitem(last=False)

        self._store[key] = CacheEntry(value, effective_ttl)

    def invalidate(self, key: str) -> bool:
        """Remove a specific key from cache."""
        if key in self._store:
            del self._store[key]
            return True
        return False

    def invalidate_prefix(self, prefix: str) -> int:
        """Remove all keys that start with the given prefix."""
        keys_to_remove = [k for k in self._store if k.startswith(prefix)]
        for k in keys_to_remove:
            del self._store[k]
        return len(keys_to_remove)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._store.clear()
        self._hits = 0
        self._misses = 0

    @property
    def stats(self) -> dict:
        """Return cache statistics."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        return {
            "size": len(self._store),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate_percent": round(hit_rate, 1),
        }

    def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        now = time.time()
        expired = [k for k, v in self._store.items() if now > v.expires_at]
        for k in expired:
            del self._store[k]
        return len(expired)


def make_cache_key(*parts: str) -> str:
    """Create a deterministic cache key from multiple parts."""
    combined = "|".join(str(p) for p in parts)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


# ─── Singleton Instances ─────────────────────────────────────────────────────

# Cache for AI-generated responses (questions, summaries)
ai_response_cache = ResponseCache(
    max_size=settings.CACHE_MAX_SIZE,
    default_ttl=settings.CACHE_TTL_SECONDS,
)

# Cache for embeddings (longer TTL since they don't change)
embedding_cache = ResponseCache(
    max_size=200,
    default_ttl=86400,  # 24 hours
)
