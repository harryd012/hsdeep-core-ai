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
        """Send a non-streaming chat request through the resolved provider.

        Privacy (security programme S5): a tenant that turned AI off gets no
        model call at all, and every prompt is redacted first: credentials
        always, email / IP addresses when the tenant's settings say so."""
        model, fell_back = ModelRouter.route(request.tier, request.model)
        request.model = model

        blocked = await _apply_privacy(request)
        if blocked is not None:
            return AIResponse(content=None, model=model, provider=self._default_provider, tier=request.tier,
                              request_id=request.request_id, error=blocked)

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


async def _apply_privacy(request: AIRequest) -> str | None:
    """Redact the prompt in place; return an error code when the tenant turned AI off."""
    from app.core.redaction import redact
    from app.core.tenant_context import current_tenant_id

    tenant_id = request.metadata.get("tenant_id") or current_tenant_id.get()
    emails, ips = True, False
    if tenant_id is not None:
        from app.core.db import AsyncSessionLocal
        from app.services.privacy import load_privacy

        async with AsyncSessionLocal() as db:
            privacy = await load_privacy(db, tenant_id)
        if privacy.ai_mode == "off":
            logger.info("AI request refused: AI is turned off for tenant %s", tenant_id)
            return "ai_disabled_by_tenant"
        emails, ips = privacy.ai_redact_emails, privacy.ai_redact_ips
    for message in request.messages:
        message.content = redact(message.content, emails=emails, ips=ips)
    return None
