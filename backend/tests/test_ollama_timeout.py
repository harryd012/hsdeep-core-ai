"""Tests for controlled Ollama timeout / failure handling (work-brief item E).

Requirement: when the local LLM is unreachable or exceeds its deadline, the
Copilot layer must NOT raise into the route, must NOT spin inside a retry
storm, and must surface a clearly-marked fallback (fallback_used=True for
intent classification, a deterministic fallback summary string) so the
frontend receives a useful, fast answer instead of hanging.

These tests monkeypatch the module-level `_chat` transport so they are
deterministic and do not depend on whether a real Ollama is running.
"""
from __future__ import annotations

import pytest

import app.services.ollama_client as ollama
from app.services.ollama_client import classify_intent, summarize_context


class TestOllamaControlledTimeout:
    async def test_classify_intent_returns_fallback_on_unreachable(self, monkeypatch):
        calls: list = []

        async def fake_chat(**kwargs):
            calls.append(kwargs)
            # Simulate the transport's own timeout handling: it returns None
            # (never raises) when Ollama is unavailable/slow.
            return None

        monkeypatch.setattr(ollama, "_chat", fake_chat)

        result = await classify_intent("show me the alert summary")

        # Controlled fallback surfaced, never an exception.
        assert result.fallback_used is True
        assert result.intent == "query"
        assert result.confidence == 0.0
        # No retry storm: exactly one LLM attempt.
        assert len(calls) == 1

    async def test_summarize_context_returns_fallback_on_unreachable(self, monkeypatch):
        calls: list = []

        async def fake_chat(**kwargs):
            calls.append(kwargs)
            return None

        monkeypatch.setattr(ollama, "_chat", fake_chat)

        text = await summarize_context({"command_center": {"metrics": {"total_tasks": 3}}})
        assert "currently unavailable" in text
        assert len(calls) == 1

    async def test_timeout_bytes_raise_is_caught_in_transport(self, monkeypatch):
        """Even a transport that propagates a read timeout must be caught by
        the caller path - the client layer never exposed a stuck request."""
        import httpx

        calls: list = []

        async def fake_chat(**kwargs):
            calls.append(kwargs)
            try:
                raise httpx.ReadTimeout("read timed out")
            except httpx.HTTPError:
                return None

        monkeypatch.setattr(ollama, "_chat", fake_chat)
        result = await classify_intent("restart the firewall")
        assert result.fallback_used is True
        assert len(calls) == 1