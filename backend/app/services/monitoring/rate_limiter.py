"""
Per-source rate limiter using Redis sliding-window.

Prevents a single monitoring source from being polled too frequently across
all scheduler workers / replicas. Each source gets its own Redis key, so
the limit is per-source, not global.

Uses a Redis sorted-set (ZADD + ZREMRANGEBYSCORE + ZCARD) sliding-window
algorithm:
  - Each call adds a (score=now, member=request_id) entry to the set.
  - Entries older than the window (60s) are removed.
  - If the remaining count >= limit, the source is rate-limited.

The key expires automatically after the TTL so a crashed worker doesn't
leave orphaned keys.
"""
from __future__ import annotations

import logging
import time
import uuid

from app.core.config import get_settings
from app.core.redis import redis_client

logger = logging.getLogger(__name__)


class SourceRateLimiter:
    """Redis-backed per-source sliding-window rate limiter."""

    def __init__(self, redis_client=None, *, rpm: int | None = None):
        # Lazily resolve the shared redis client so tests can inject a mock
        # without needing a running Redis instance.
        self._redis = redis_client if redis_client is not None else _get_redis()
        settings = get_settings()
        self._rpm = rpm if rpm is not None else settings.source_rate_limit_rpm
        self._window_seconds = 60
        self._ttl = settings.source_rate_limit_ttl_seconds

    @staticmethod
    def _key(source_id: uuid.UUID) -> str:
        return f"source_rate_limit:{source_id}"

    async def is_allowed(self, source_id: uuid.UUID) -> bool:
        """Return True if the source is allowed to be polled now, False if
        it is rate-limited.

        Thread-safe / multi-worker safe: relies on Redis atomic operations.
        Returns True if rate limiting is disabled (rpm <= 0).
        """
        if self._rpm <= 0:
            return True

        now = time.time()
        window_start = now - self._window_seconds
        key = self._key(source_id)
        request_id = str(uuid.uuid4())

        # Use a Redis pipeline for atomicity: add the new entry, prune old
        # entries, then check the count.
        try:
            pipe = self._redis.pipeline()
            pipe.zadd(key, {request_id: now})
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            pipe.expire(key, self._ttl)
            _, _, count, _ = await pipe.execute()
        except Exception as exc:
            # If Redis is down, fail-open: allow the collection to proceed
            # rather than blocking all polling. This is a deliberate
            # availability-over-consistency choice for the rate-limiting path
            # — a source being polled slightly too often is far less harmful
            # than a source never being polled at all.
            logger.warning("SourceRateLimiter: Redis error for source_id=%s, failing open: %s", source_id, exc)
            return True

        if count > self._rpm:
            logger.info(
                "Source %s rate-limited: %d calls in the last %ds (limit=%d)",
                source_id, count, self._window_seconds, self._rpm,
            )
            return False

        return True

    async def acquire(self, source_id: uuid.UUID) -> bool:
        """Alias for is_allowed — makes call sites read naturally.
        ``if await limiter.acquire(source.id): do_collection()``"""
        return await self.is_allowed(source_id)

    @property
    def rpm(self) -> int:
        """Configured per-minute ceiling (0 = disabled)."""
        return self._rpm

    @property
    def enabled(self) -> bool:
        """True when a non-zero ceiling is configured."""
        return self._rpm > 0


def _get_redis():
    """Lazily import the global redis_client singleton. Kept as a function
    so module import doesn't fail at test collection time if Redis isn't
    configured."""
    from app.core.redis import redis_client as _client
    return _client


# Process-wide singleton, so a scheduler tick reuses one Redis client/pipeline
# instead of building one per source. Construction never touches Redis (the
# client is resolved lazily), and rate limiting stays DISABLED by default.
_limiter_singleton: "SourceRateLimiter | None" = None


def get_source_rate_limiter() -> SourceRateLimiter:
    """The process-wide per-source limiter used by the collection path."""
    global _limiter_singleton
    if _limiter_singleton is None:
        _limiter_singleton = SourceRateLimiter()
    return _limiter_singleton


def reset_source_rate_limiter() -> None:
    """Drop the cached singleton (tests / settings reloads)."""
    global _limiter_singleton
    _limiter_singleton = None
