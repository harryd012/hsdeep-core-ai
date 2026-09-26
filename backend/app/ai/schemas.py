"""
AI Gateway schemas — provider-agnostic internal contracts.

These types define the boundary between application services and the
AI transport layer. They intentionally avoid any provider-specific
concepts (Ollama endpoints, Anthropic headers, etc.).
"""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AITier(str, Enum):
    """Deterministic capability tier derived from call-site purpose.

    The tier is application metadata — never inferred from an LLM call:
      UTILITY  - small, structured, latency-sensitive work.
      STANDARD - ordinary generation.
      DEEP     - complex reasoning (RCA, correlation, diagnosis).
    """

    UTILITY = "utility"
    STANDARD = "standard"
    DEEP = "deep"


class AIMessage(BaseModel):
    """A single chat message in provider-neutral form."""

    role: str  # "system" | "user" | "assistant"
    content: str


class AIRequest(BaseModel):
    """Provider-agnostic request to the AI Gateway.

    Only fields actually consumed by the current system are required;
    the rest are optional future-compatibility hooks.
    """

    messages: list[AIMessage] = Field(default_factory=list)
    tier: AITier = AITier.DEEP
    model: str | None = None
    max_tokens: int | None = None
    temperature: float = 0.0
    timeout: float | None = None
    stream: bool = False
    response_format: str = "text"  # "text" | "json"
    metadata: dict[str, Any] = Field(default_factory=dict)
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])


class AIUsage(BaseModel):
    """Token / timing usage for one completed AI call."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    duration_ms: int | None = None


class AIResponse(BaseModel):
    """Normalized response returned by the AI Gateway."""

    content: str | None = None
    model: str | None = None
    provider: str = "ollama"
    tier: AITier = AITier.DEEP
    usage: AIUsage = Field(default_factory=AIUsage)
    duration_ms: int | None = None
    fallback_used: bool = False
    fallback_chain: list[str] = Field(default_factory=list)
    finish_reason: str | None = None
    request_id: str = ""
    error: str | None = None


class AIError(BaseModel):
    """Safe internal error representation.

    Never raised to the route layer — callers receive this inside
    an AIResponse with error set and fallback_content populated.
    """

    code: str = "PROVIDER_ERROR"
    message: str = ""
    provider: str = "ollama"
    retryable: bool = False
    fallback_content: str | None = None
