"""
AIProvider protocol — the contract every provider adapter implements.

This interface is intentionally minimal. Only chat() is required for
the current system; the remaining methods are optional extension
points for future phases.
"""
from __future__ import annotations

from typing import Protocol

from app.ai.schemas import AIRequest, AIResponse


class AIProvider(Protocol):
    """Provider-agnostic interface for an AI backend.

    Implementations translate the generic AIRequest into provider-specific
    wire calls and return a normalized AIResponse.
    """

    name: str

    async def chat(self, request: AIRequest) -> AIResponse:
        """Send a non-streaming chat request. Never raises."""
        ...
