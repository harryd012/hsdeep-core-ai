"""
OllamaAdapter — provider adapter for the local Ollama server.

Wraps the existing working Ollama transport (bounded fallback,
num_predict, think:false, keep_alive, compatibility retry)
and exposes the generic AIProvider interface.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from app.ai.schemas import AITier, AIRequest, AIResponse, AIUsage
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# num_predict caps preserved from the existing implementation.
_INTENT_NUM_PREDICT = 128
_SUMMARY_NUM_PREDICT = 512


def _ollama_url(path: str) -> str:
    settings = get_settings()
    host = settings.ollama_host.rstrip("/")
    return f"{host}{path}"


class OllamaAdapter:
    """AIProvider implementation backed by a local Ollama server."""

    name = "ollama"

    def __init__(self) -> None:
        pass

    def _resolve_timeout(self, request: AIRequest) -> float:
        if request.timeout is not None:
            return float(request.timeout)
        s = get_settings()
        return getattr(s, "ollama_timeout_seconds", 60.0) or 60.0

    def _resolve_num_predict(self, request: AIRequest) -> int | None:
        if request.max_tokens is not None:
            return request.max_tokens
        return None

    def _resolve_thinking(self, request: AIRequest) -> bool:
        # Explicit metadata override takes precedence; otherwise UTILITY
        # tier disables thinking (preserves existing behavior).
        explicit = request.metadata.get("disable_thinking")
        if explicit is not None:
            return bool(explicit)
        return request.tier is AITier.UTILITY

    def _extract_prompts(self, request: AIRequest) -> tuple[str, str]:
        system_prompt = request.metadata.get("system_prompt", "")
        user_content = request.metadata.get("user_content", "")
        if request.messages:
            if not system_prompt:
                for m in request.messages:
                    if m.role == "system":
                        system_prompt = m.content
                        break
            if not user_content:
                for m in reversed(request.messages):
                    if m.role == "user":
                        user_content = m.content
                        break
        return system_prompt, user_content

    def _select_model_for_tier(self, tier: AITier) -> tuple[str, bool]:
        from app.services.ollama_client import select_model, AIModelTier
        tier_map = {
            AITier.UTILITY: AIModelTier.UTILITY,
            AITier.STANDARD: AIModelTier.STANDARD,
            AITier.DEEP: AIModelTier.DEEP,
        }
        return select_model(tier_map[tier])

    def _build_payload(
        self, model: str, system_prompt: str, user_content: str,
        request: AIRequest, num_predict: int | None, disable_thinking: bool,
    ) -> dict[str, Any]:
        settings = get_settings()
        payload: dict[str, Any] = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "options": {"temperature": request.temperature},
        }
        if num_predict is not None:
            payload["options"]["num_predict"] = num_predict
        if disable_thinking:
            payload["think"] = False
        if request.response_format == "json":
            payload["format"] = "json"
        keep_alive = (settings.ollama_keep_alive or "").strip()
        if keep_alive:
            payload["keep_alive"] = keep_alive
        return payload
    async def chat(self, request: AIRequest) -> AIResponse:
        settings = get_settings()
        default_model = settings.ollama_model
        requested_model, _ = self._select_model_for_tier(request.tier)
        model = request.model or requested_model
        system_prompt, user_content = self._extract_prompts(request)
        timeout = self._resolve_timeout(request)
        num_predict = self._resolve_num_predict(request)
        disable_thinking = self._resolve_thinking(request)
        candidates = [model]
        if model != default_model:
            candidates.append(default_model)
        fallback_chain: list[str] = []
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = None
                for index, candidate in enumerate(candidates):
                    payload = self._build_payload(
                        candidate, system_prompt, user_content,
                        request, num_predict, disable_thinking)
                    response = await client.post(
                        _ollama_url("/api/chat"), json=payload,
                        headers={"Content-Type": "application/json"})
                    if response.status_code == 404 and index < len(candidates) - 1:
                        logger.warning(
                            "MODEL_FALLBACK tier=%s requested_model=%s "
                            "fallback_model=%s reason=model_not_found",
                            request.tier.value, candidate, candidates[index + 1])
                        fallback_chain.append(candidate)
                        continue
                    if disable_thinking and response.status_code in (400, 422):
                        payload = {k: v for k, v in payload.items() if k != "think"}
                        response = await client.post(
                            _ollama_url("/api/chat"), json=payload,
                            headers={"Content-Type": "application/json"})
                    break
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                response.raise_for_status()
                body = response.json()
                content_val = (
                    body.get("message", {}).get("content")
                    if isinstance(body, dict) else None)
                used_model = payload["model"]
                logger.info(
                    "OLLAMA_DURATION=%d model=%s tier=%s status=%s",
                    elapsed_ms, used_model, request.tier.value,
                    response.status_code)
                if fallback_chain:
                    logger.info(
                        "MODEL_SELECTED tier=%s requested_model=%s "
                        "selected_model=%s fallback=true",
                        request.tier.value, model, used_model)
                if not content_val or not str(content_val).strip():
                    logger.warning("Ollama returned an empty completion")
                    return AIResponse(
                        content=None, model=used_model, provider=self.name,
                        tier=request.tier, usage=AIUsage(duration_ms=elapsed_ms),
                        duration_ms=elapsed_ms, fallback_used=bool(fallback_chain),
                        fallback_chain=fallback_chain, request_id=request.request_id,
                        error="empty_completion")
                return AIResponse(
                    content=str(content_val).strip(), model=used_model,
                    provider=self.name, tier=request.tier,
                    usage=AIUsage(duration_ms=elapsed_ms),
                    duration_ms=elapsed_ms, fallback_used=bool(fallback_chain),
                    fallback_chain=fallback_chain, finish_reason="stop",
                    request_id=request.request_id)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.warning(
                "Ollama chat call failed: tier=%s model=%s %s: %s",
                request.tier.value, model, type(exc).__name__, exc)
            return AIResponse(
                content=None, model=model, provider=self.name,
                tier=request.tier, usage=AIUsage(duration_ms=elapsed_ms),
                duration_ms=elapsed_ms, fallback_used=bool(fallback_chain),
                fallback_chain=fallback_chain, request_id=request.request_id,
                error=f"{type(exc).__name__}")
