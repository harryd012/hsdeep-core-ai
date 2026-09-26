"""
Minimal in-process TTL cache for aggregate endpoints.
"""
from __future__ import annotations

import time
from typing import Any


class TTLCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._default_ttl = 30.0

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if not entry:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        self._store[key] = (time.monotonic() + (ttl or self._default_ttl), value)


cache = TTLCache()


def cache_key_prefix(prefix: str, **kwargs: Any) -> str:
    parts = [prefix]
    for k, v in sorted(kwargs.items()):
        parts.append(f"{k}={v}")
    return "|".join(parts)