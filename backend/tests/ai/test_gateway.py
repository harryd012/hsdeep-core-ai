"""
Tests for the AIGateway.

Verifies the gateway routes requests through the provider registry
and returns normalized AIResponse objects.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.ai import get_gateway, reset_gateway
from app.ai.schemas import AITier, AIMessage, AIRequest


DEFAULT_MODEL = "qwen3.6:latest"
FAST_MODEL = "qwen2.5:7b"


class _Recorder:
    def __init__(self):
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        self.requests.append(payload)
        return httpx.Response(
            200, json={"message": {"content": "gateway-ok"}, "done": True}
        )

    @property
    def models(self) -> list[str]:
        return [p["model"] for p in self.requests]


@pytest.fixture(autouse=True)
def _reset():
    """Ensure a fresh gateway per test."""
    reset_gateway()
    yield
    reset_gateway()


@pytest.fixture
def settings(monkeypatch):
    def _install(**overrides) -> SimpleNamespace:
        cfg = SimpleNamespace(
            ollama_host="http://localhost:11434",
            ollama_model=DEFAULT_MODEL,
            ai_fast_model="",
            ai_standard_model="",
            ai_deep_model="",
            ollama_keep_alive="",
            ollama_timeout_seconds=5.0,
            ollama_summary_timeout_seconds=5.0,
        )
        for k, v in overrides.items():
            setattr(cfg, k, v)
        import app.services.ollama_client as ollama_mod

        monkeypatch.setattr(ollama_mod, "get_settings", lambda: cfg)
        import app.ai.providers.ollama as adapter_mod

        monkeypatch.setattr(adapter_mod, "get_settings", lambda: cfg)
        return cfg

    yield _install


@pytest.fixture
def transport(monkeypatch):
    def _install() -> _Recorder:
        recorder = _Recorder()
        real_client = httpx.AsyncClient

        def factory(**kwargs):
            kwargs.pop("transport", None)
            return real_client(
                transport=httpx.MockTransport(recorder.handler), **kwargs
            )

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        return recorder

    yield _install


class TestGateway:
    @pytest.mark.asyncio
    async def test_gateway_returns_normalized_response(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport()
        gateway = get_gateway()
        request = AIRequest(
            messages=[
                AIMessage(role="system", content="sys"),
                AIMessage(role="user", content="hello"),
            ],
            tier=AITier.UTILITY,
        )
        response = await gateway.chat(request)
        assert response.content == "gateway-ok"
        assert response.model == FAST_MODEL
        assert response.provider == "ollama"
        assert response.tier == AITier.UTILITY
        assert response.fallback_used is False

    @pytest.mark.asyncio
    async def test_gateway_routes_utility_to_fast_model(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport()
        gateway = get_gateway()
        request = AIRequest(
            messages=[
                AIMessage(role="system", content="sys"),
                AIMessage(role="user", content="hello"),
            ],
            tier=AITier.UTILITY,
        )
        await gateway.chat(request)
        assert rec.models == [FAST_MODEL]

    @pytest.mark.asyncio
    async def test_gateway_routes_deep_to_deep_model(self, settings, transport):
        settings(ai_deep_model="deepseek-r1:14b")
        rec = transport()
        gateway = get_gateway()
        request = AIRequest(
            messages=[
                AIMessage(role="system", content="sys"),
                AIMessage(role="user", content="hello"),
            ],
            tier=AITier.DEEP,
        )
        await gateway.chat(request)
        assert rec.models == ["deepseek-r1:14b"]

    @pytest.mark.asyncio
    async def test_gateway_single_request_per_call(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport()
        gateway = get_gateway()
        request = AIRequest(
            messages=[
                AIMessage(role="system", content="sys"),
                AIMessage(role="user", content="hello"),
            ],
            tier=AITier.UTILITY,
        )
        await gateway.chat(request)
        assert len(rec.requests) == 1

    @pytest.mark.asyncio
    async def test_gateway_preserves_num_predict(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport()
        gateway = get_gateway()
        request = AIRequest(
            messages=[
                AIMessage(role="system", content="sys"),
                AIMessage(role="user", content="hello"),
            ],
            tier=AITier.UTILITY,
            max_tokens=128,
        )
        await gateway.chat(request)
        assert rec.requests[0]["options"]["num_predict"] == 128

    @pytest.mark.asyncio
    async def test_gateway_think_false_for_utility(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport()
        gateway = get_gateway()
        request = AIRequest(
            messages=[
                AIMessage(role="system", content="sys"),
                AIMessage(role="user", content="hello"),
            ],
            tier=AITier.UTILITY,
        )
        await gateway.chat(request)
        assert rec.requests[0]["think"] is False
