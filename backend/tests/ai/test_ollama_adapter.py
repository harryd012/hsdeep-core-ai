"""
Tests for the OllamaAdapter.

Verifies the adapter produces correct Ollama wire payloads,
preserves num_predict/think:false/keep_alive behavior.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.ai.schemas import AITier, AIMessage, AIRequest


DEFAULT_MODEL = "qwen3.6:latest"
FAST_MODEL = "qwen2.5:7b"


class _Recorder:
    def __init__(self, status_code: int = 200, content: str = "hello"):
        self.requests: list[dict] = []
        self.status_code = status_code
        self.content = content

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        self.requests.append(payload)
        return httpx.Response(
            self.status_code,
            json={"message": {"content": self.content}, "done": True},
        )

    @property
    def models(self) -> list[str]:
        return [p["model"] for p in self.requests]


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
        # Patch at the source of truth for select_model.
        import app.services.ollama_client as ollama_mod

        monkeypatch.setattr(ollama_mod, "get_settings", lambda: cfg)
        # Also patch where the adapter reads settings.
        import app.ai.providers.ollama as adapter_mod

        monkeypatch.setattr(adapter_mod, "get_settings", lambda: cfg)
        return cfg

    yield _install


@pytest.fixture
def transport(monkeypatch):
    def _install(status_code: int = 200, content: str = "hello") -> _Recorder:
        recorder = _Recorder(status_code, content)
        real_client = httpx.AsyncClient

        def factory(**kwargs):
            kwargs.pop("transport", None)
            return real_client(
                transport=httpx.MockTransport(recorder.handler), **kwargs
            )

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        return recorder

    yield _install


def _make_request(tier=AITier.UTILITY, **kwargs):
    return AIRequest(
        messages=[
            AIMessage(role="system", content="sys"),
            AIMessage(role="user", content="hello"),
        ],
        tier=tier,
        **kwargs,
    )

class TestOllamaAdapterModelSelection:
    @pytest.mark.asyncio
    async def test_utility_uses_fast_model(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport()
        from app.ai.providers.ollama import OllamaAdapter

        adapter = OllamaAdapter()
        response = await adapter.chat(_make_request())
        assert rec.models == [FAST_MODEL]
        assert response.model == FAST_MODEL
        assert response.provider == "ollama"

    @pytest.mark.asyncio
    async def test_deep_uses_deep_model(self, settings, transport):
        settings(ai_deep_model="deepseek-r1:14b")
        rec = transport()
        from app.ai.providers.ollama import OllamaAdapter

        adapter = OllamaAdapter()
        response = await adapter.chat(_make_request(tier=AITier.DEEP))
        assert rec.models == ["deepseek-r1:14b"]

    @pytest.mark.asyncio
    async def test_unconfigured_tier_uses_default(self, settings, transport):
        settings()
        rec = transport()
        from app.ai.providers.ollama import OllamaAdapter

        adapter = OllamaAdapter()
        response = await adapter.chat(_make_request())
        assert rec.models == [DEFAULT_MODEL]


class TestOllamaAdapterPayload:
    @pytest.mark.asyncio
    async def test_num_predict_sent_when_configured(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport()
        from app.ai.providers.ollama import OllamaAdapter

        adapter = OllamaAdapter()
        await adapter.chat(_make_request(max_tokens=128))
        assert rec.requests[0]["options"]["num_predict"] == 128

    @pytest.mark.asyncio
    async def test_think_false_for_utility_tier(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport()
        from app.ai.providers.ollama import OllamaAdapter

        adapter = OllamaAdapter()
        await adapter.chat(_make_request())
        assert rec.requests[0]["think"] is False

    @pytest.mark.asyncio
    async def test_think_not_sent_for_deep_tier(self, settings, transport):
        settings()
        rec = transport()
        from app.ai.providers.ollama import OllamaAdapter

        adapter = OllamaAdapter()
        await adapter.chat(_make_request(tier=AITier.DEEP))
        assert "think" not in rec.requests[0]

    @pytest.mark.asyncio
    async def test_keep_alive_omitted_when_unconfigured(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport()
        from app.ai.providers.ollama import OllamaAdapter

        adapter = OllamaAdapter()
        await adapter.chat(_make_request())
        assert "keep_alive" not in rec.requests[0]

    @pytest.mark.asyncio
    async def test_keep_alive_sent_when_configured(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL, ollama_keep_alive="10m")
        rec = transport()
        from app.ai.providers.ollama import OllamaAdapter

        adapter = OllamaAdapter()
        await adapter.chat(_make_request())
        assert rec.requests[0]["keep_alive"] == "10m"

    @pytest.mark.asyncio
    async def test_json_format_for_json_response_format(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport()
        from app.ai.providers.ollama import OllamaAdapter

        adapter = OllamaAdapter()
        await adapter.chat(_make_request(response_format="json"))
        assert rec.requests[0]["format"] == "json"

    @pytest.mark.asyncio
    async def test_normalized_response(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport(content="test response")
        from app.ai.providers.ollama import OllamaAdapter

        adapter = OllamaAdapter()
        response = await adapter.chat(_make_request())
        assert response.content == "test response"
        assert response.model == FAST_MODEL
        assert response.provider == "ollama"
        assert response.tier == AITier.UTILITY
        assert response.fallback_used is False
        assert response.finish_reason == "stop"


class TestOllamaAdapterError:
    @pytest.mark.asyncio
    async def test_error_returns_fallback_response(self, settings, monkeypatch):
        settings(ai_fast_model=FAST_MODEL)

        def error_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timed out", request=request)

        real_client = httpx.AsyncClient

        def factory(**kwargs):
            kwargs.pop("transport", None)
            return real_client(
                transport=httpx.MockTransport(error_handler), **kwargs
            )

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        from app.ai.providers.ollama import OllamaAdapter

        adapter = OllamaAdapter()
        response = await adapter.chat(_make_request())
        assert response.content is None
        assert response.error == "ReadTimeout"

    @pytest.mark.asyncio
    async def test_empty_completion_returns_none_content(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport(content="   ")
        from app.ai.providers.ollama import OllamaAdapter

        adapter = OllamaAdapter()
        response = await adapter.chat(_make_request())
        assert response.content is None
        assert response.error == "empty_completion"
