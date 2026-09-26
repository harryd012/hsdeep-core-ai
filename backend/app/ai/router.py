"""
ModelRouter — deterministic, configuration-driven model selection.

NO LLM call. NO AI classification. NO database call. NO I/O.

Pure in-memory routing based on tier and configured models.
Reuses the existing select_model() logic from ollama_client.
"""
from __future__ import annotations

import logging

from app.ai.schemas import AITier

logger = logging.getLogger(__name__)


class ModelRouter:
    """Deterministic router that resolves tier → model.

    Routing precedence:
      1. Explicit request.model if provided.
      2. Tier-specific configured model (AI_FAST_MODEL, etc.).
      3. Default configured model (ollama_model).
    """

    @staticmethod
    def route(tier: AITier, explicit_model: str | None = None) -> tuple[str, bool]:
        """Resolve (model, fell_back) for a tier.

        Pure config lookup — no I/O, no LLM call.
        """
        if explicit_model:
            return explicit_model, False

        # Reuse the existing tested routing function.
        from app.services.ollama_client import select_model, AIModelTier

        tier_map = {
            AITier.UTILITY: AIModelTier.UTILITY,
            AITier.STANDARD: AIModelTier.STANDARD,
            AITier.DEEP: AIModelTier.DEEP,
        }
        legacy_tier = tier_map[tier]
        return select_model(legacy_tier)
