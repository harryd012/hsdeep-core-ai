"""
Provider registry — maps provider names to adapter instances.

Minimal and extensible: new providers are added by calling
register_provider(). No placeholder/fake adapters are created.
"""
from __future__ import annotations

import logging
from typing import Any

from app.ai.schemas import AIRequest, AIResponse

logger = logging.getLogger(__name__)

# name → adapter instance
_PROVIDERS: dict[str, Any] = {}


def register_provider(name: str, adapter: Any) -> None:
    """Register a provider adapter under a canonical name."""
    _PROVIDERS[name] = adapter
    logger.debug("Registered AI provider: %s", name)


def get_provider(name: str) -> Any | None:
    """Return the adapter for `name` or None if not registered."""
    return _PROVIDERS.get(name)


def list_providers() -> list[str]:
    """List all registered provider names."""
    return list(_PROVIDERS.keys())


def _clear() -> None:
    """Clear all registrations. For test use only."""
    _PROVIDERS.clear()
