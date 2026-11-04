"""Command Center overview probes Redis and Ollama concurrently and reuses the
result for a short window instead of waiting on both on every poll."""
from __future__ import annotations

import asyncio

import pytest

from app.services import command_center_service as ccs

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(ccs, "_dependency_cache", None)


async def test_probes_run_concurrently_and_are_cached(monkeypatch):
    calls = {"redis": 0, "ollama": 0}

    async def slow_redis():
        calls["redis"] += 1
        await asyncio.sleep(0.2)
        return "unreachable"

    async def slow_ollama():
        calls["ollama"] += 1
        await asyncio.sleep(0.2)
        return "down"

    import app.api.routes.health as health

    monkeypatch.setattr(ccs, "redis_health", slow_redis)
    monkeypatch.setattr(health, "_check_ollama", slow_ollama)
    monkeypatch.setattr(ccs.get_settings(), "dependency_probe_cache_seconds", 60)

    loop = asyncio.get_running_loop()
    t0 = loop.time()
    first = await ccs.dependency_states()
    elapsed = loop.time() - t0
    assert first == ("unreachable", "down")
    assert elapsed < 0.35  # concurrent, not 0.4 s in sequence

    # Ten concurrent polls inside the window probe nothing new.
    results = await asyncio.gather(*(ccs.dependency_states() for _ in range(10)))
    assert set(results) == {first} and calls == {"redis": 1, "ollama": 1}


async def test_zero_ttl_always_probes(monkeypatch):
    calls = []

    async def redis():
        calls.append(1)
        return "up"

    async def ollama():
        return "up"

    import app.api.routes.health as health

    monkeypatch.setattr(ccs, "redis_health", redis)
    monkeypatch.setattr(health, "_check_ollama", ollama)
    monkeypatch.setattr(ccs.get_settings(), "dependency_probe_cache_seconds", 0)
    await ccs.dependency_states()
    await ccs.dependency_states()
    assert len(calls) == 2
