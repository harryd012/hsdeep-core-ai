"""
AIGateway - the single provider-agnostic entry point for AI calls.

Responsibilities:
  - receive AIRequest
  - ask ModelRouter for model resolution
  - resolve provider through registry
  - call provider adapter
  - return normalized AIResponse

The gateway contains NO business logic, NO Copilot-specific logic,
NO tenant entitlement logic, NO billing logic.
"""
from __future__ import annotations

import logging

from app.ai.providers.registry import get_provider
from app.ai.router import ModelRouter
from app.ai.schemas import AITier, AIRequest, AIResponse

logger = logging.getLogger(__name__)


class AIGateway:
    """Provider-agnostic AI gateway.

    Initially supports only the Ollama provider. The architecture
    allows additional providers to be registered without modifying
    this class.
    """

    def __init__(self, provider_name: str = "ollama") -> None:
        self._default_provider = provider_name

    async def chat(self, request: AIRequest) -> AIResponse:
        """Send a non-streaming chat request through the resolved provider."""
        model, fell_back = ModelRouter.route(request.tier, request.model)
        request.model = model

        provider = get_provider(self._default_provider)
        if provider is None:
            logger.error("AI provider not registered: %s", self._default_provider)
            return AIResponse(
                content=None,
                model=model,
                provider=self._default_provider,
                tier=request.tier,
                request_id=request.request_id,
                error="provider_not_registered",
            )

        response = await provider.chat(request)
        return response
