"""
Backward-compatibility tests for ollama_client.py.

Verifies the existing public API still works after the gateway refactor.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

import app.services.ollama_client as ollama


DEFAULT_MODEL = "qwen3.6:latest"
FAST_MODEL = "qwen2.5:7b"


class _Recorder:
    def __init__(self, unavailable_models: tuple[str, ...] = ()):
        self.requests: list[dict] = []
        self.unavailable_models = unavailable_models

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        self.requests.append(payload)
        if payload.get("model") in self.unavailable_models:
            return httpx.Response(404, json={"error": "model not found"})
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "intent": "query",
                            "confidence": 0.9,
                            "needs_disambiguation": False,
                            "department_hint": None,
                            "reason": "status question",
                        }
                    )
                },
                "done": True,
            },
        )

    @property
    def models(self) -> list[str]:
        return [p["model"] for p in self.requests]


@pytest.fixture
def transport(monkeypatch):
    def _install(unavailable: tuple[str, ...] = ()) -> _Recorder:
        recorder = _Recorder(unavailable)
        real_client = httpx.AsyncClient

        def factory(**kwargs):
            kwargs.pop("transport", None)
            return real_client(
                transport=httpx.MockTransport(recorder.handler), **kwargs
            )

        monkeypatch.setattr(ollama.httpx, "AsyncClient", factory)
        return recorder

    yield _install


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
        monkeypatch.setattr(ollama, "get_settings", lambda: cfg)
        # Also patch where the adapter reads settings.
        import app.ai.providers.ollama as adapter_mod

        monkeypatch.setattr(adapter_mod, "get_settings", lambda: cfg)
        return cfg

    yield _install



class TestBackwardCompatClassifyIntent:
    @pytest.mark.asyncio
    async def test_returns_intent_result_shape(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        transport()
        result = await ollama.classify_intent("show me alerts")
        assert result.intent == "query"
        assert result.confidence == 0.9
        assert result.fallback_used is False
        assert result.department_hint is None

    @pytest.mark.asyncio
    async def test_fallback_on_unreachable(self, settings, monkeypatch):
        settings(ai_fast_model=FAST_MODEL)
        calls = {"n": 0}

        def timeout_handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            raise httpx.ReadTimeout("read timed out", request=request)

        real_client = httpx.AsyncClient

        def factory(**kwargs):
            kwargs.pop("transport", None)
            return real_client(
                transport=httpx.MockTransport(timeout_handler), **kwargs
            )

        monkeypatch.setattr(ollama.httpx, "AsyncClient", factory)
        result = await ollama.classify_intent("show alerts")
        assert result.fallback_used is True
        assert result.intent == "query"
        assert result.confidence == 0.0
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_uses_fast_model(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport()
        await ollama.classify_intent("restart firewall")
        assert rec.models == [FAST_MODEL]


class TestBackwardCompatSummarizeContext:
    @pytest.mark.asyncio
    async def test_empty_context_returns_fallback(self, settings):
        text = await ollama.summarize_context({})
        assert text == "No context data was available to summarize."


class TestBackwardCompatSelectModel:
    def test_utility_resolves_to_fast_model(self, settings):
        settings(ai_fast_model=FAST_MODEL)
        model, fell_back = ollama.select_model(ollama.AIModelTier.UTILITY)
        assert model == FAST_MODEL
        assert fell_back is False

    def test_deep_resolves_to_deep_model(self, settings):
        settings(ai_deep_model="deepseek-r1:14b")
        model, fell_back = ollama.select_model(ollama.AIModelTier.DEEP)
        assert model == "deepseek-r1:14b"

    def test_unconfigured_falls_back_to_default(self, settings):
        settings()
        model, _ = ollama.select_model(ollama.AIModelTier.UTILITY)
        assert model == DEFAULT_MODEL

    def test_select_model_performs_no_io(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport()
        before = len(rec.requests)
        ollama.select_model(ollama.AIModelTier.UTILITY)
        ollama.select_model(ollama.AIModelTier.DEEP)
        assert len(rec.requests) == before


class TestBackwardCompatChat:
    @pytest.mark.asyncio
    async def test_chat_default_tier_is_deep(self):
        import inspect

        default_tier = inspect.signature(ollama._chat).parameters["tier"].default
        assert default_tier is ollama.AIModelTier.DEEP

    @pytest.mark.asyncio
    async def test_chat_keep_alive_omitted_when_unconfigured(self, settings, transport):
        settings()
        rec = transport()
        await ollama._chat(system_prompt="s", user_content="u", timeout=5.0)
        assert "keep_alive" not in rec.requests[0]

    @pytest.mark.asyncio
    async def test_chat_keep_alive_sent_when_configured(self, settings, transport):
        settings(ollama_keep_alive="10m")
        rec = transport()
        await ollama._chat(system_prompt="s", user_content="u", timeout=5.0)
        assert rec.requests[0]["keep_alive"] == "10m"

    @pytest.mark.asyncio
    async def test_copilot_call_site_signatures_unchanged(self):
        import inspect

        assert list(inspect.signature(ollama.classify_intent).parameters) == ["text"]
        assert list(inspect.signature(ollama.summarize_context).parameters) == [
            "context"
        ]
