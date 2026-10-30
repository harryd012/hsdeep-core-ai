"""Gate 1: Redis client timeouts, health classification, sanitized transition logs.

These cover the small, evidence-backed reliability improvements to
``app.core.redis``:
  * the client configures bounded connect/operation timeouts (fast failure on
    a hung Redis instead of blocking a request),
  * ``redis_health()`` classifies up / down / unreachable from a real PING,
  * degradation/recovery are logged exactly once per transition and never leak
    the configured URL or credentials.
"""
from __future__ import annotations

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core import redis as redis_module


class _FakeRedis:
    """Async Redis stand-in: ping() returns `result` or raises `exc`."""

    def __init__(self, *, result: bool | None = None, exc: Exception | None = None):
        self._result = result
        self._exc = exc

    async def ping(self):
        if self._exc is not None:
            raise self._exc
        return self._result


def _reset() -> None:
    """Reset the module's transition tracker so tests are independent."""
    redis_module._last_state = None


def test_client_configures_bounded_connect_and_operation_timeouts():
    """A hung Redis must fail fast: both handshake and operation are bounded."""
    kwargs = redis_module.redis_client.connection_pool.connection_kwargs
    assert kwargs["socket_connect_timeout"] == redis_module._REDIS_CONNECT_TIMEOUT_SECONDS
    assert kwargs["socket_timeout"] == redis_module._REDIS_SOCKET_TIMEOUT_SECONDS
    # Sensible, non-zero bounds (not redis-py's default of "no timeout").
    assert kwargs["socket_connect_timeout"] > 0
    assert kwargs["socket_timeout"] >= kwargs["socket_connect_timeout"]


@pytest.mark.asyncio
async def test_redis_health_up_only_on_real_ping_success(monkeypatch):
    _reset()
    monkeypatch.setattr(redis_module, "redis_client", _FakeRedis(result=True))
    assert await redis_module.redis_health() == "up"


@pytest.mark.asyncio
async def test_redis_health_unreachable_on_connection_or_timeout_error(monkeypatch):
    _reset()
    monkeypatch.setattr(
        redis_module, "redis_client", _FakeRedis(exc=RedisConnectionError("refused"))
    )
    assert await redis_module.redis_health() == "unreachable"

    _reset()
    monkeypatch.setattr(
        redis_module, "redis_client", _FakeRedis(exc=RedisTimeoutError("slow"))
    )
    assert await redis_module.redis_health() == "unreachable"


@pytest.mark.asyncio
async def test_redis_health_down_on_failed_ping_or_generic_error(monkeypatch):
    _reset()
    monkeypatch.setattr(redis_module, "redis_client", _FakeRedis(result=False))
    assert await redis_module.redis_health() == "down"

    _reset()
    monkeypatch.setattr(redis_module, "redis_client", _FakeRedis(exc=RedisError("err")))
    assert await redis_module.redis_health() == "down"


@pytest.mark.asyncio
async def test_degradation_logged_once_then_suppressed(monkeypatch, caplog):
    """Entering a bad state logs one warning; steady-state repeats do not spam."""
    _reset()
    # Start UP (recovery path primed), then degrade.
    monkeypatch.setattr(redis_module, "redis_client", _FakeRedis(result=True))
    with caplog.at_level("INFO", logger="app.core.redis"):
        assert await redis_module.redis_health() == "up"
        # Degrade.
        monkeypatch.setattr(
            redis_module, "redis_client", _FakeRedis(exc=RedisConnectionError("refused"))
        )
        assert await redis_module.redis_health() == "unreachable"
        assert await redis_module.redis_health() == "unreachable"  # repeat

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1, f"expected exactly one degradation log, got {len(warnings)}"
    assert "degraded" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_recovery_logged_once_when_redis_returns(monkeypatch, caplog):
    _reset()
    monkeypatch.setattr(
        redis_module, "redis_client", _FakeRedis(exc=RedisConnectionError("refused"))
    )
    with caplog.at_level("INFO", logger="app.core.redis"):
        assert await redis_module.redis_health() == "unreachable"
    caplog.clear()

    # Recover: transition unreachable -> up should log recovery exactly once.
    monkeypatch.setattr(redis_module, "redis_client", _FakeRedis(result=True))
    with caplog.at_level("INFO", logger="app.core.redis"):
        assert await redis_module.redis_health() == "up"
        assert await redis_module.redis_health() == "up"  # steady state, no repeat

    infos = [r for r in caplog.records if r.levelname == "INFO"]
    assert len(infos) == 1, f"expected exactly one recovery log, got {len(infos)}"
    assert "recovered" in infos[0].getMessage()


@pytest.mark.asyncio
async def test_health_logs_never_leak_url_or_credentials(monkeypatch, caplog):
    """Only state names are logged - the configured URL/credentials never are."""
    _reset()
    url = redis_module.settings.redis_url
    for fake in (
        _FakeRedis(result=True),
        _FakeRedis(exc=RedisConnectionError("refused")),
        _FakeRedis(exc=RedisError("boom")),
    ):
        monkeypatch.setattr(redis_module, "redis_client", fake)
        with caplog.at_level("DEBUG", logger="app.core.redis"):
            await redis_module.redis_health()
    for record in caplog.records:
        assert url not in record.getMessage(), "health log leaked the Redis URL"
        assert "redis://" not in record.getMessage().lower()
