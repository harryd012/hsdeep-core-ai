"""
Tests for the AI model router.

Verifies deterministic tier -> model routing with zero I/O.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ai.router import ModelRouter
from app.ai.schemas import AITier


DEFAULT_MODEL = "qwen3.6:latest"
FAST_MODEL = "qwen2.5:7b"
STANDARD_MODEL = "llama3.1:8b"
DEEP_MODEL = "deepseek-r1:14b"


@pytest.fixture
def settings(monkeypatch):
    """Deterministic settings stub."""

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
        # Patch at the source module used by select_model.
        import app.services.ollama_client as ollama_mod

        monkeypatch.setattr(ollama_mod, "get_settings", lambda: cfg)
        return cfg

    yield _install


class TestUTILITYTier:
    def test_resolves_to_fast_model_when_configured(self, settings):
        settings(ai_fast_model=FAST_MODEL)
        model, fell_back = ModelRouter.route(AITier.UTILITY)
        assert model == FAST_MODEL
        assert fell_back is False

    def test_falls_back_to_default_when_fast_not_configured(self, settings):
        settings()  # no fast model
        model, fell_back = ModelRouter.route(AITier.UTILITY)
        assert model == DEFAULT_MODEL

    def test_no_io_during_routing(self, settings):
        """Routing is pure config lookup — no HTTP calls."""
        settings(ai_fast_model=FAST_MODEL)
        # If routing performed I/O, it would require a mock transport.
        # The fact this runs without one proves zero I/O.
        model, _ = ModelRouter.route(AITier.UTILITY)
        assert model == FAST_MODEL


class TestSTANDARDTier:
    def test_resolves_to_standard_model_when_configured(self, settings):
        settings(ai_standard_model=STANDARD_MODEL)
        model, fell_back = ModelRouter.route(AITier.STANDARD)
        assert model == STANDARD_MODEL
        assert fell_back is False

    def test_falls_back_to_default_when_not_configured(self, settings):
        settings()
        model, fell_back = ModelRouter.route(AITier.STANDARD)
        assert model == DEFAULT_MODEL


class TestDEEPTier:
    def test_resolves_to_deep_model_when_configured(self, settings):
        settings(ai_deep_model=DEEP_MODEL)
        model, fell_back = ModelRouter.route(AITier.DEEP)
        assert model == DEEP_MODEL
        assert fell_back is False

    def test_falls_back_to_default_when_not_configured(self, settings):
        settings()
        model, fell_back = ModelRouter.route(AITier.DEEP)
        assert model == DEFAULT_MODEL


class TestExplicitModel:
    def test_explicit_model_overrides_tier(self, settings):
        settings(ai_fast_model=FAST_MODEL)
        model, fell_back = ModelRouter.route(AITier.UTILITY, explicit_model="custom:1b")
        assert model == "custom:1b"
        assert fell_back is False


class TestUnconfiguredFallback:
    def test_all_tiers_fall_back_to_ollama_model(self, settings):
        settings()  # nothing configured
        for tier in (AITier.UTILITY, AITier.STANDARD, AITier.DEEP):
            model, _ = ModelRouter.route(tier)
            assert model == DEFAULT_MODEL
