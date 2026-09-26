"""
AI Gateway package - provider-agnostic AI transport layer.

Usage:
    from app.ai import get_gateway
    gateway = get_gateway()
    response = await gateway.chat(request)
"""
from __future__ import annotations

import logging

from app.ai.gateway import AIGateway
from app.ai.providers.ollama import OllamaAdapter
from app.ai.providers.registry import register_provider

logger = logging.getLogger(__name__)

_gateway: AIGateway | None = None


def get_gateway() -> AIGateway:
    """Return a singleton AIGateway with Ollama registered."""
    global _gateway
    if _gateway is None:
        register_provider("ollama", OllamaAdapter())
        _gateway = AIGateway(provider_name="ollama")
        logger.debug("AI Gateway initialized with Ollama provider")
    return _gateway


def reset_gateway() -> None:
    """Reset the singleton. For test use only."""
    global _gateway
    _gateway = None
