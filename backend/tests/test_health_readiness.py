"""Phase 9A: liveness stays dependency-free; readiness is dependency-aware."""
from __future__ import annotations

import httpx
import pytest
from fastapi import status

from app.api.routes import health as health_module
from app.core.config import get_settings
from app.main import app


def _asgi() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _patch_probes(monkeypatch, *, db: bool, redis: str, ollama: str = "up") -> None:
    async def _db():
        return db

    async def _redis():
        return redis

    async def _ollama():
        return ollama

    monkeypatch.setattr(health_module, "check_db_connection", _db)
    monkeypatch.setattr(health_module, "redis_health", _redis)
    monkeypatch.setattr(health_module, "_check_ollama", _ollama)


async def test_liveness_is_dependency_free(monkeypatch):
    """A broken database/Redis must NOT affect liveness (no restart storms)."""

    async def _explode(*args, **kwargs):
        raise RuntimeError("dependency must not be consulted")

    monkeypatch.setattr(health_module, "check_db_connection", _explode)
    monkeypatch.setattr(health_module, "redis_health", _explode)
    monkeypatch.setattr(health_module, "_check_ollama", _explode)

    async with _asgi() as client:
        resp = await client.get("/health/live")

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["status"] == "alive"


async def test_ready_200_when_required_dependencies_up(monkeypatch):
    _patch_probes(monkeypatch, db=True, redis="up")
    async with _asgi() as client:
        resp = await client.get("/health/ready")
    body = resp.json()
    assert resp.status_code == status.HTTP_200_OK
    assert body["ready"] is True
    assert body["not_ready"] == []
    assert body["checks"]["database"] == "up"
    assert body["checks"]["redis"] == "up"


async def test_ready_503_when_database_is_down(monkeypatch):
    _patch_probes(monkeypatch, db=False, redis="up")
    async with _asgi() as client:
        resp = await client.get("/health/ready")
    body = resp.json()
    assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert body["ready"] is False
    assert body["not_ready"] == ["database"]


async def test_ready_503_when_redis_is_required_and_down(monkeypatch):
    monkeypatch.setattr(get_settings(), "readiness_require_redis", True)
    _patch_probes(monkeypatch, db=True, redis="unreachable")
    async with _asgi() as client:
        resp = await client.get("/health/ready")
    body = resp.json()
    assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert body["not_ready"] == ["redis"]


async def test_ready_200_when_redis_not_required(monkeypatch):
    """A deployment that intentionally runs without Redis still becomes ready."""
    monkeypatch.setattr(get_settings(), "readiness_require_redis", False)
    _patch_probes(monkeypatch, db=True, redis="unreachable")
    async with _asgi() as client:
        resp = await client.get("/health/ready")
    body = resp.json()
    assert resp.status_code == status.HTTP_200_OK
    assert body["ready"] is True
    assert body["not_ready"] == []


async def test_ollama_never_affects_readiness(monkeypatch):
    _patch_probes(monkeypatch, db=True, redis="up", ollama="down")
    async with _asgi() as client:
        resp = await client.get("/health/ready")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["ready"] is True


async def test_legacy_ready_alias_matches_health_ready(monkeypatch):
    _patch_probes(monkeypatch, db=False, redis="up")
    async with _asgi() as client:
        legacy = await client.get("/ready")
        namespaced = await client.get("/health/ready")
    assert legacy.status_code == namespaced.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert legacy.json()["not_ready"] == namespaced.json()["not_ready"] == ["database"]


async def test_health_endpoint_contract_unchanged(monkeypatch):
    """The frontend consumes /health - its keys must not change."""
    _patch_probes(monkeypatch, db=True, redis="up")
    async with _asgi() as client:
        resp = await client.get("/health")
    body = resp.json()
    assert resp.status_code == status.HTTP_200_OK
    for key in (
        "status",
        "ready",
        "ai_ready",
        "system_online",
        "timestamp",
        "dependencies",
    ):
        assert key in body, f"/health lost its {key!r} key"
