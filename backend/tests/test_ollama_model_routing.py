"""Focused tests for the AI model-routing layer (utility/standard/deep tiers).

Scope: the routing must be a deterministic configuration lookup driven by the
EXISTING request intent/type metadata (call-site purpose) - never another LLM
call. The existing configured OLLAMA_MODEL (deep/default, e.g. qwen3.6) stays
the fallback for every tier and the model used when nothing is configured.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

import app.services.ollama_client as ollama


DEFAULT_MODEL = "qwen3.6:latest"
FAST_MODEL = "qwen2.5:7b"
STANDARD_MODEL = "llama3.1:8b"
DEEP_MODEL = "deepseek-r1:14b"


class _Recorder:
    """Records every outbound request payload and decides the response."""

    def __init__(self, unavailable_models: tuple[str, ...] = ()):
        self.requests: list[dict] = []
        self.unavailable_models = unavailable_models

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        self.requests.append(payload)
        if payload.get("model") in self.unavailable_models:
            return httpx.Response(404, json={"error": "model not found"})
        # Valid content for both call sites: intent classification parses JSON,
        # summarize_context returns the raw string.
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
    """Wire a MockTransport into ollama_client's own AsyncClient usage."""

    def _install(unavailable: tuple[str, ...] = ()) -> _Recorder:
        recorder = _Recorder(unavailable)
        real_client = httpx.AsyncClient

        def factory(**kwargs):
            kwargs.pop("transport", None)
            return real_client(transport=httpx.MockTransport(recorder.handler), **kwargs)

        monkeypatch.setattr(ollama.httpx, "AsyncClient", factory)
        return recorder

    yield _install


@pytest.fixture
def settings(monkeypatch):
    """Deterministic settings stub (no .env / lru_cache interference)."""

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
        # Also patch where the OllamaAdapter reads settings directly.
        import app.ai.providers.ollama as _adapter_mod

        monkeypatch.setattr(_adapter_mod, "get_settings", lambda: cfg)
        return cfg

    yield _install


# --------------------------------------------------------------------------- #
# A / B / C / D - tier -> model selection                                     #
# --------------------------------------------------------------------------- #
class TestTierSelection:
    async def test_a_utility_request_selects_fast_model(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport()
        result = await ollama.classify_intent("show me the alerts")
        assert result is not None
        assert rec.models == [FAST_MODEL]  # utility -> fast model, first choice

    async def test_a_utility_summary_selects_fast_model(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport()
        text = await ollama.summarize_context({"metrics": {"total_tasks": 3}})
        assert isinstance(text, str) and text  # non-empty summary returned
        assert rec.models == [FAST_MODEL]

    async def test_b_standard_request_selects_standard_model(self, settings, transport):
        settings(ai_standard_model=STANDARD_MODEL)
        rec = transport()
        await ollama._chat(
            system_prompt="s", user_content="u", timeout=5.0,
            tier=ollama.AIModelTier.STANDARD,
        )
        assert rec.models == [STANDARD_MODEL]
        assert ollama.select_model(ollama.AIModelTier.STANDARD) == (STANDARD_MODEL, False)

    async def test_c_deep_request_selects_deep_model(self, settings):
        settings(ai_deep_model=DEEP_MODEL)
        assert ollama.select_model(ollama.AIModelTier.DEEP) == (DEEP_MODEL, False)

    async def test_c_deep_defaults_to_existing_qwen_when_unconfigured(self, settings):
        settings()  # nothing configured
        assert ollama.select_model(ollama.AIModelTier.DEEP) == (DEFAULT_MODEL, False)

    async def test_d_unknown_unconfigured_intent_preserves_default(self, settings):
        """No tier configured at all -> every tier uses the existing default
        model and reports no fallback (existing installations unchanged)."""
        settings()
        for tier in ollama.AIModelTier:
            assert ollama.select_model(tier) == (DEFAULT_MODEL, False)
# --------------------------------------------------------------------------- #
# E - fast model unavailable -> existing model fallback                       #
# --------------------------------------------------------------------------- #
class TestFastModelFallback:
    async def test_e_unavailable_fast_model_falls_back_to_default(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport(unavailable=(FAST_MODEL,))

        result = await ollama.classify_intent("status of the firewall")

        # Exactly two attempts: fast model (404) -> existing default (ok).
        assert rec.models == [FAST_MODEL, DEFAULT_MODEL]
        assert result is not None
        assert result.fallback_used is False

    async def test_e_default_also_unavailable_returns_controlled_fallback(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport(unavailable=(FAST_MODEL, DEFAULT_MODEL))

        result = await ollama.classify_intent("status of the firewall")

        # Bounded chain: fast -> default, then the existing controlled
        # fallback (fallback_used=True) - never a loop and never a raise.
        assert rec.models == [FAST_MODEL, DEFAULT_MODEL]
        assert result.fallback_used is True
        assert result.intent == "query"


# --------------------------------------------------------------------------- #
# F / J - routing adds no extra LLM request                                   #
# --------------------------------------------------------------------------- #
class TestNoExtraRoutingRequest:
    async def test_f_select_model_performs_no_http_request(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport()
        before = len(rec.requests)
        ollama.select_model(ollama.AIModelTier.UTILITY)
        ollama.select_model(ollama.AIModelTier.DEEP)
        assert len(rec.requests) == before  # pure config lookup, zero I/O

    async def test_j_single_ollama_request_per_call(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport()
        await ollama.classify_intent("show alerts")
        await ollama.summarize_context({"a": 1})
        assert len(rec.requests) == 2  # one per AI call, none for routing

    async def test_j_no_duplicate_request_when_fast_model_available(self, settings, transport):
        settings(ai_fast_model=FAST_MODEL)
        rec = transport()
        await ollama._chat(
            system_prompt="s", user_content="u", timeout=5.0,
            tier=ollama.AIModelTier.UTILITY,
        )
        assert rec.models == [FAST_MODEL]  # no fallback request was made


# --------------------------------------------------------------------------- #
# G / H - existing behaviour unchanged                                        #
# --------------------------------------------------------------------------- #
class TestExistingBehaviourPreserved:
    async def test_g_timeout_still_controlled_and_single_attempt(self, settings, monkeypatch):
        settings(ai_fast_model=FAST_MODEL)
        calls = {"n": 0}

        def timeout_handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            raise httpx.ReadTimeout("read timed out", request=request)

        real_client = httpx.AsyncClient

        def factory(**kwargs):
            kwargs.pop("transport", None)
            return real_client(transport=httpx.MockTransport(timeout_handler), **kwargs)

        monkeypatch.setattr(ollama.httpx, "AsyncClient", factory)

        result = await ollama.classify_intent("show alerts")

        # Unchanged timeout contract: one attempt, controlled fallback, no raise.
        assert calls["n"] == 1
        assert result.fallback_used is True
        assert result.intent == "query"

    async def test_h_default_chat_tier_is_deep(self):
        """Any caller that does not pass a tier keeps the deep/default model -
        existing Qwen 3.6 behaviour is the untouched default."""
        import inspect
        default_tier = inspect.signature(ollama._chat).parameters["tier"].default
        assert default_tier is ollama.AIModelTier.DEEP

    async def test_h_copilot_call_site_signatures_unchanged(self):
        import inspect
        assert list(inspect.signature(ollama.classify_intent).parameters) == ["text"]
        assert list(inspect.signature(ollama.summarize_context).parameters) == ["context"]

    async def test_h_keep_alive_omitted_when_unconfigured(self, settings, transport):
        settings()
        rec = transport()
        await ollama._chat(system_prompt="s", user_content="u", timeout=5.0)
        assert "keep_alive" not in rec.requests[0]

    async def test_h_keep_alive_sent_when_configured(self, settings, transport):
        settings(ollama_keep_alive="10m")
        rec = transport()
        await ollama._chat(system_prompt="s", user_content="u", timeout=5.0)
        assert rec.requests[0]["keep_alive"] == "10m"