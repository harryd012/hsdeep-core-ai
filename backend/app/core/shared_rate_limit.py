"""
Sliding-window rate limits shared by every API process (via Redis), with a
per-process fallback (enterprise security programme S3).

Redis is the shared counter, so a limit holds across replicas. When Redis is
unavailable the same limit is enforced per process (never fail open): with N
replicas an address can reach at most N x the limit during an outage. The
outage is logged once per transition.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


class LocalWindow:
    """In-process sliding window, the fallback when Redis is unavailable."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def acquire(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        if len(self._hits) > 100_000:  # bound memory under a flood of keys
            self._hits.clear()
        return True


_redis_down_logged = False


async def acquire_shared(key: str, limit: int, window_seconds: int, local: LocalWindow) -> bool:
    """Count one hit for `key`; False when it is already at `limit` in the window."""
    global _redis_down_logged
    from app.core.redis import redis_client  # looked up per call (tests swap it)

    now = time.time()
    try:
        pipe = redis_client.pipeline()
        pipe.zremrangebyscore(key, 0, now - window_seconds)
        pipe.zcard(key)
        count = (await pipe.execute())[1]
        if count >= limit:
            return False
        # Unique member per hit so two hits in the same instant count twice.
        await redis_client.zadd(key, {f"{now}:{uuid.uuid4().hex[:8]}": now})
        await redis_client.expire(key, window_seconds)
        if _redis_down_logged:
            logger.info("Shared rate limits: Redis is reachable again.")
            _redis_down_logged = False
        return True
    except Exception as exc:  # noqa: BLE001 - any Redis failure uses the local window
        if not _redis_down_logged:
            logger.error("Shared rate limits: Redis unavailable (%s); enforcing per-process limits.", type(exc).__name__)
            _redis_down_logged = True
    return local.acquire(key, limit, window_seconds)


class SharedRateLimiter:
    """A named limit: `limit` hits per `window_seconds` per key."""

    def __init__(self, prefix: str, limit: int, window_seconds: int) -> None:
        self.prefix = prefix
        self.limit = limit
        self.window_seconds = window_seconds
        self._local = LocalWindow()

    async def acquire(self, key: str) -> bool:
        return await acquire_shared(f"{self.prefix}{key}", self.limit, self.window_seconds, self._local)
