"""
Per-connection sliding-window rate limiter for ServiceNow API calls.

Uses a per-key deque of request timestamps plus an asyncio.Lock to ensure
no more than `requests_per_minute` calls complete within any rolling
60-second window. Matches the "PostgreSQL advisory lock is enough, no
distributed lock library needed yet" pragmatism from the rest of the
scheduler (see scheduler/jobs.py and the ServiceNow tick).

Usage pattern:
    limiter = RateLimiter()  # singleton or per-request

    async def call_service_now(connection):
        await limiter.acquire(connection.id, connection.rate_limit_requests_per_minute)
        # ... make HTTP request ...
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self) -> None:
        # Per-key lock + timestamp window
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}
        self._windows: dict[uuid.UUID, deque[float]] = {}

    async def acquire(self, key: uuid.UUID, requests_per_minute: int) -> None:
        """Block until at least one slot is available in the rolling 60s
        window for `key`. Guarantees no more than ``requests_per_minute``
        calls complete within any sliding minute."""

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            window = self._windows.setdefault(key, deque())
            # Remove timestamps older than 60s
            cutoff = now - 60.0
            while window and window[0] < cutoff:
                window.popleft()

            if len(window) >= requests_per_minute:
                # Wait until the oldest request in the window expires
                wait_time = window[0] + 60.0 - now
                if wait_time > 0:
                    logger.debug(
                        "RateLimiter: key=%s waiting %.2fs (at %d rpm)",
                        key, wait_time, requests_per_minute,
                    )
                    await asyncio.sleep(wait_time)
                # Prune after sleeping
                while window and window[0] < now:
                    window.popleft()

            window.append(now)


# Module-level singleton — callers just import and use.
default_limiter = RateLimiter()
