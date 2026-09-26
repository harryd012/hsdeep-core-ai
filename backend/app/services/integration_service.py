"""
Integration service layer.

Owns the business rules for managing external system integrations:
creation, lifecycle, health tracking, and entitlement enforcement.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.integration import Integration
from app.repositories.integration_repository import IntegrationRepository


class IntegrationNotFound(Exception):
    pass


class IntegrationService:
    def __init__(self, repo: IntegrationRepository):
        self.repo = repo

    async def get(self, integration_id: uuid.UUID, tenant_id: uuid.UUID) -> Integration:
        integration = await self.repo.get_by_tenant(integration_id, tenant_id)
        if integration is None:
            raise IntegrationNotFound(f"Integration {integration_id} not found")
        return integration

    async def list_integrations(
        self,
        tenant_id: uuid.UUID,
        *,
        provider_type: str | None = None,
        provider: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Integration]:
        return await self.repo.list(
            tenant_id,
            provider_type=provider_type,
            provider=provider,
            status=status,
            limit=limit,
            offset=offset,
        )

    async def count_integrations(
        self,
        tenant_id: uuid.UUID,
        *,
        provider_type: str | None = None,
        provider: str | None = None,
        status: str | None = None,
    ) -> int:
        return await self.repo.count(
            tenant_id,
            provider_type=provider_type,
            provider=provider,
            status=status,
        )

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        return await self.repo.summary(tenant_id)

    async def create(
        self,
        tenant_id: uuid.UUID,
        *,
        name: str,
        provider_type: str,
        provider: str,
        description: str | None = None,
        capabilities: list[str] | None = None,
        configuration: dict | None = None,
        credential_reference: str | None = None,
    ) -> Integration:
        """Create a new integration."""
        now = datetime.now(timezone.utc)
        integration = await self.repo.create(
            tenant_id=tenant_id,
            name=name,
            description=description,
            provider_type=provider_type,
            provider=provider,
            status="disabled",  # starts disabled until configured
            capabilities=capabilities or [],
            configuration=configuration or {},
            credential_reference=credential_reference,
            discovery_state="pending",
            synchronization_state="pending",
            last_successful_collection=None,
            last_discovery=None,
            last_synchronization=None,
        )
        return integration

    async def update(
        self,
        integration_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
        capabilities: list[str] | None = None,
        configuration: dict | None = None,
        credential_reference: str | None = None,
    ) -> Integration:
        integration = await self.get(integration_id, tenant_id)
        update_fields: dict = {}
        if name is not None:
            update_fields["name"] = name
        if description is not None:
            update_fields["description"] = description
        if status is not None:
            update_fields["status"] = status
        if capabilities is not None:
            update_fields["capabilities"] = capabilities
        if configuration is not None:
            update_fields["configuration"] = configuration
        if credential_reference is not None:
            update_fields["credential_reference"] = credential_reference
        if update_fields:
            integration = await self.repo.update(integration, **update_fields)
        return integration

    async def record_collection(
        self, integration_id: uuid.UUID, success: bool, error: str | None = None
    ) -> Integration | None:
        """Record a collection attempt (success or failure)."""
        return await self.repo.update_heartbeat(integration_id, success, error)

    async def record_events_ingested(
        self, integration_id: uuid.UUID, count: int
    ) -> Integration | None:
        """Record events ingested by this integration."""
        integration = await self.repo.get(integration_id)
        if integration is None:
            return None
        integration.total_events_ingested += count
        integration.event_rate_per_minute = count  # simplified
        await self.repo.db.flush()
        await self.repo.db.refresh(integration)
        return integration
