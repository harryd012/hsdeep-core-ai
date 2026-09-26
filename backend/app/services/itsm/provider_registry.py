"""
ITSM Provider Registry and Factory.

The Registry is the in-memory catalog of available provider types,
populated at application startup from the ITSMProvider database table
(or from the built-in defaults if the table is empty).

The Factory instantiates a provider implementation from its class_path,
decrypts credentials via CredentialManager, and returns a ready-to-use
BaseITSMProvider instance.

This follows the same "registry + factory" pattern as the collector
framework (see collectors/base.py and the monitoring service's
collector registration).
"""
from __future__ import annotations

import importlib
import logging
from typing import Any

from app.services.itsm.base_provider import BaseITSMProvider, ITSMProviderError

logger = logging.getLogger(__name__)


BUILTIN_PROVIDERS: list[dict[str, Any]] = [
    {
        "provider_type": "servicenow",
        "display_name": "ServiceNow",
        "class_path": "app.services.itsm.providers.servicenow.ServiceNowProvider",
        "supported_auth_modes": ["oauth2", "basic"],
        "capabilities": {
            "create_ticket": True, "update_ticket": True, "close_ticket": True,
            "add_comment": True, "add_attachment": True, "bi_directional_sync": True,
            "webhooks": True, "cmdb": True, "polling": True,
        },
        "default_rate_limit_rpm": 100,
    },
    {
        "provider_type": "jira",
        "display_name": "Jira Service Management",
        "class_path": "app.services.itsm.providers.jira.JiraProvider",
        "supported_auth_modes": ["oauth2", "pat", "api_key"],
        "capabilities": {
            "create_ticket": True, "update_ticket": True, "close_ticket": True,
            "add_comment": True, "add_attachment": True, "bi_directional_sync": True,
            "webhooks": True, "cmdb": False, "polling": True,
        },
        "default_rate_limit_rpm": 60,
    },
    {
        "provider_type": "freshservice",
        "display_name": "Freshservice",
        "class_path": "app.services.itsm.providers.freshservice.FreshserviceProvider",
        "supported_auth_modes": ["api_key"],
        "capabilities": {
            "create_ticket": True, "update_ticket": True, "close_ticket": True,
            "add_comment": True, "add_attachment": True, "bi_directional_sync": True,
            "webhooks": True, "cmdb": True, "polling": True,
        },
        "default_rate_limit_rpm": 60,
    },
]


class ProviderRegistry:
    """In-memory registry of available ITSM provider types."""

    def __init__(self) -> None:
        self._providers: dict[str, dict[str, Any]] = {}
        self._initialized = False

    def register(self, provider_type: str, metadata: dict[str, Any]) -> None:
        self._providers[provider_type] = metadata
        logger.debug("Registered ITSM provider: %s", provider_type)

    def get(self, provider_type: str) -> dict[str, Any] | None:
        return self._providers.get(provider_type)

    def list_providers(self) -> list[dict[str, Any]]:
        return list(self._providers.values())

    def is_registered(self, provider_type: str) -> bool:
        return provider_type in self._providers

    def initialize_from_builtins(self) -> None:
        for provider in BUILTIN_PROVIDERS:
            self.register(provider["provider_type"], provider)
        self._initialized = True
        logger.info("Initialized ITSM provider registry with %d built-in providers", len(self._providers))

    async def refresh_from_db(self) -> None:
        try:
            from app.core.db import AsyncSessionLocal
            from app.models.itsm import ITSMProvider as ITSMProviderModel
            from sqlalchemy import select

            async with AsyncSessionLocal() as db:
                stmt = select(ITSMProviderModel).where(ITSMProviderModel.is_active.is_(True))
                result = await db.execute(stmt)
                db_providers = result.scalars().all()
                if not db_providers:
                    self.initialize_from_builtins()
                    return
                self._providers.clear()
                for p in db_providers:
                    self.register(p.provider_type, {
                        "provider_type": p.provider_type,
                        "display_name": p.display_name,
                        "class_path": p.class_path,
                        "supported_auth_modes": p.supported_auth_modes,
                        "capabilities": p.capabilities,
                        "default_rate_limit_rpm": p.default_rate_limit_rpm,
                    })
                self._initialized = True
                logger.info("Refreshed ITSM provider registry from DB: %d providers", len(self._providers))
        except Exception as exc:
            logger.warning("Failed to refresh ITSM provider registry from DB, using builtins: %s", exc)
            self.initialize_from_builtins()


_registry = ProviderRegistry()


def get_registry() -> ProviderRegistry:
    return _registry


class ProviderFactory:
    """Instantiates ITSM provider implementations from their class_path."""

    def __init__(self, registry: ProviderRegistry | None = None) -> None:
        self._registry = registry or get_registry()

    def create(
        self,
        provider_type: str,
        instance_url: str,
        auth_type: str,
        credentials: dict[str, Any],
        rate_limit_rpm: int = 100,
        config: dict[str, Any] | None = None,
    ) -> BaseITSMProvider:
        metadata = self._registry.get(provider_type)
        if not metadata:
            raise ITSMProviderError(
                f"Provider type '{provider_type}' is not registered",
                provider_type=provider_type,
            )
        class_path = metadata["class_path"]
        try:
            module_path, class_name = class_path.rsplit(".", 1)
            module = importlib.import_module(module_path)
            provider_class = getattr(module, class_name)
            if not issubclass(provider_class, BaseITSMProvider):
                raise ITSMProviderError(
                    f"Provider class {class_path} does not inherit from BaseITSMProvider",
                    provider_type=provider_type,
                )
            provider = provider_class(
                instance_url=instance_url,
                auth_type=auth_type,
                credentials=credentials,
                rate_limit_rpm=rate_limit_rpm,
                config=config,
            )
            logger.debug("Created ITSM provider: %s for %s", provider_type, instance_url)
            return provider
        except ImportError as exc:
            raise ITSMProviderError(
                f"Failed to import provider class {class_path}: {exc}",
                provider_type=provider_type,
            ) from exc
        except Exception as exc:
            if isinstance(exc, ITSMProviderError):
                raise
            raise ITSMProviderError(
                f"Failed to instantiate provider {class_path}: {exc}",
                provider_type=provider_type,
            ) from exc


def get_factory() -> ProviderFactory:
    return ProviderFactory(get_registry())