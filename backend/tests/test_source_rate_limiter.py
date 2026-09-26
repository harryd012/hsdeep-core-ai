"""Phase 9A: SourceRateLimiter configuration, behaviour and collection wiring.

No Redis instance is required. The limiter accepts an injected client (existing
constructor argument), and the collection wiring is exercised with a fake
limiter plus a fake session factory.
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings
from app.scheduler import jobs
from app.services.monitoring.rate_limiter import (
    SourceRateLimiter,
    get_source_rate_limiter,
    reset_source_rate_limiter,
)


class _FakePipeline:
    """Minimal Redis pipeline stand-in that reports a fixed window count."""

    def __init__(self, count: int) -> None:
        self.count = count

    def zadd(self, key, mapping):  # noqa: ANN001 - mirror redis-py signature
        return self

    def zremrangebyscore(self, *args):  # noqa: ANN002
        return self

    def zcard(self, *args):  # noqa: ANN002
        return self

    def expire(self, *args):  # noqa: ANN002
        return self

    async def execute(self):
        return (1, 0, self.count, True)


def _client(count: int) -> MagicMock:
    client = MagicMock()
    client.pipeline = MagicMock(return_value=_FakePipeline(count))
    return client


# ---------------------------------------------------------------------------
# Settings contract
# ---------------------------------------------------------------------------

def test_defaults_disable_rate_limiting():
    """rpm=0 (disabled) by default, with a bounded key TTL."""
    settings = Settings(_env_file=None, secret_encryption_key="x")
    assert settings.source_rate_limit_rpm == 0
    assert settings.source_rate_limit_ttl_seconds == 120


def test_settings_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("SOURCE_RATE_LIMIT_RPM", "7")
    monkeypatch.setenv("SOURCE_RATE_LIMIT_TTL_SECONDS", "45")
    settings = Settings(_env_file=None, secret_encryption_key="x")
    assert settings.source_rate_limit_rpm == 7
    assert settings.source_rate_limit_ttl_seconds == 45


# ---------------------------------------------------------------------------
# Limiter behaviour
# ---------------------------------------------------------------------------

async def test_disabled_mode_allows_and_never_touches_redis():
    """rpm=0 must return True immediately - safe when disabled."""
    client = MagicMock()
    client.pipeline = MagicMock(side_effect=AssertionError("Redis must not be used"))
    limiter = SourceRateLimiter(client, rpm=0)
    assert limiter.enabled is False
    assert await limiter.acquire(uuid.uuid4()) is True
    client.pipeline.assert_not_called()


async def test_normal_acquisition_below_limit_is_allowed():
    limiter = SourceRateLimiter(_client(count=1), rpm=5)
    assert limiter.enabled is True
    assert limiter.rpm == 5
    assert await limiter.acquire(uuid.uuid4()) is True


async def test_source_is_rate_limited_when_window_is_full():
    limiter = SourceRateLimiter(_client(count=6), rpm=5)
    assert await limiter.acquire(uuid.uuid4()) is False


async def test_redis_failure_fails_open():
    """Availability first: a Redis outage must never stop collection."""
    client = MagicMock()
    client.pipeline = MagicMock(side_effect=ConnectionError("redis is down"))
    limiter = SourceRateLimiter(client, rpm=5)
    assert await limiter.acquire(uuid.uuid4()) is True


async def test_pipeline_execute_failure_fails_open():
    client = MagicMock()
    pipeline = MagicMock()
    pipeline.zadd = MagicMock(return_value=pipeline)
    pipeline.zremrangebyscore = MagicMock(return_value=pipeline)
    pipeline.zcard = MagicMock(return_value=pipeline)
    pipeline.expire = MagicMock(return_value=pipeline)
    pipeline.execute = AsyncMock(side_effect=TimeoutError("redis timeout"))
    client.pipeline = MagicMock(return_value=pipeline)
    limiter = SourceRateLimiter(client, rpm=5)
    assert await limiter.acquire(uuid.uuid4()) is True


async def test_singleton_is_cached_and_resettable():
    reset_source_rate_limiter()
    first = get_source_rate_limiter()
    assert get_source_rate_limiter() is first
    reset_source_rate_limiter()
    assert get_source_rate_limiter() is not first


# ---------------------------------------------------------------------------
# Collection-path wiring (app/scheduler/jobs.py::_run_bounded)
# ---------------------------------------------------------------------------

class _FakeLimiter:
    """Records acquisitions and answers with a fixed verdict."""

    def __init__(self, allow: bool) -> None:
        self.allow = allow
        self.acquired: list[uuid.UUID] = []
        self.rpm = 5

    async def acquire(self, source_id: uuid.UUID) -> bool:
        self.acquired.append(source_id)
        return self.allow


async def test_rate_limited_source_skips_collection_without_touching_db(monkeypatch):
    """A rate-limited source must not open a DB session for collection."""
    audited: list[tuple] = []

    async def _fake_audit(tenant_id, source_id):
        audited.append((tenant_id, source_id))

    monkeypatch.setattr(jobs, "_audit_collection_rate_limited", _fake_audit)

    db_factory = MagicMock()
    monkeypatch.setattr(jobs, "AsyncSessionLocal", db_factory)

    limiter = _FakeLimiter(allow=False)
    source_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    await jobs._run_bounded(source_id, tenant_id, None, limiter=limiter)

    assert limiter.acquired == [source_id]
    assert audited == [(tenant_id, source_id)]
    db_factory.assert_not_called()


async def test_allowed_source_proceeds_to_collection(monkeypatch):
    """When allowed, execution continues into the collection path."""
    reached = {"db": False}

    def _raise_when_used():
        reached["db"] = True
        raise RuntimeError("db section reached")

    monkeypatch.setattr(jobs, "AsyncSessionLocal", _raise_when_used)

    limiter = _FakeLimiter(allow=True)
    await jobs._run_bounded(
        uuid.uuid4(), uuid.uuid4(), asyncio.Semaphore(1), limiter=limiter
    )

    assert limiter.acquired != []
    assert reached["db"] is True, "allowed source must continue into collection"

