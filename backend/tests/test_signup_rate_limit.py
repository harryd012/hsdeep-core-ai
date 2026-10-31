"""Signup rate limit (R1.7): a Redis outage never allows unlimited signups."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routes import billing

pytestmark = pytest.mark.asyncio


class _BrokenRedis:
    def pipeline(self):
        raise ConnectionError("redis down")


class _FakePipe:
    def __init__(self, store, key):
        self.store, self.key = store, key

    def zremrangebyscore(self, key, lo, hi):
        self.store[key] = [t for t in self.store.get(key, []) if t > hi]

    def zcard(self, key):
        pass

    async def execute(self):
        return [None, len(self.store.get(self.key, []))]


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, list[float]] = {}

    def pipeline(self):
        return _FakePipe(self.store, self._key)

    async def zadd(self, key, mapping):
        self.store.setdefault(key, []).extend(mapping.values())

    async def expire(self, key, seconds):
        pass


@pytest.fixture
def fresh_window(monkeypatch):
    monkeypatch.setattr(billing, "_signup_local_window", billing._LocalWindow())


async def test_redis_outage_falls_back_to_a_real_limit(monkeypatch, fresh_window):
    import app.core.redis as redis_mod

    monkeypatch.setattr(redis_mod, "redis_client", _BrokenRedis())
    for _ in range(billing._SIGNUP_RATE_LIMIT):
        await billing._check_signup_rate_limit("198.51.100.9")
    with pytest.raises(HTTPException) as exc:
        await billing._check_signup_rate_limit("198.51.100.9")
    assert exc.value.status_code == 429
    # Another IP is unaffected.
    await billing._check_signup_rate_limit("198.51.100.10")


async def test_shared_limit_when_redis_is_up(monkeypatch, fresh_window):
    import app.core.redis as redis_mod

    fake = _FakeRedis()
    fake._key = f"{billing._SIGNUP_RATE_KEY_PREFIX}203.0.113.5"
    monkeypatch.setattr(redis_mod, "redis_client", fake)
    for _ in range(billing._SIGNUP_RATE_LIMIT):
        await billing._check_signup_rate_limit("203.0.113.5")
    with pytest.raises(HTTPException):
        await billing._check_signup_rate_limit("203.0.113.5")
    assert len(fake.store[fake._key]) == billing._SIGNUP_RATE_LIMIT
