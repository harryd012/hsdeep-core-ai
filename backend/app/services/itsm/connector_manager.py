"""
ITSM Connector Manager.

The ConnectorManager is the main orchestration service for ITSM
operations. It bridges the database layer (ITSMConnection, Ticket,
TicketLink) and the provider layer (BaseITSMProvider instances).

Responsibilities:
- Create/update/delete ITSM connections (with credential encryption).
- Instantiate provider instances via ProviderFactory.
- Test connections (health checks).
- Create/update/close tickets in external systems.
- Manage ticket links (bidirectional sync mappings).
- Add comments and attachments to external tickets.
- Record audit events for all operations.

The ConnectorManager NEVER calls external APIs directly - it always
goes through a BaseITSMProvider instance. This ensures all providers
are interchangeable.

This follows the same service-layer pattern as the existing
ServiceNowIncidentSyncService: constructor takes repositories, methods
are async, tenant-scoped, and use the db session from the repository.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import SecretCrypto
from app.models.itsm import ITSMConnection, ITSMProvider
from app.models.ticket import Ticket, TicketLink, TicketHistory
from app.models.knowledge_audit import AuditEvent
from app.services.itsm.base_provider import (
    BaseITSMProvider,
    ITSMProviderError,
    ITSMTicketCreate,
    ITSMTicketUpdate,
    ITSMTicketResponse,
    ITSMCommentCreate,
    ITSMHealthCheckResult,
)
from app.services.itsm.credential_manager import CredentialManager
from app.services.itsm.provider_registry import get_factory, get_registry

logger = logging.getLogger(__name__)


class ConnectorManager:
    """Main orchestration service for ITSM operations.

    Bridges the database layer and the provider layer. All external API
    calls go through a BaseITSMProvider instance - this manager never
    calls external APIs directly.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._crypto = SecretCrypto()
        self._credential_manager = CredentialManager(self._crypto)
        self._factory = get_factory()
        self._registry = get_registry()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def create_connection(
        self,
        tenant_id: uuid.UUID,
        provider_type: str,
        name: str,
        instance_url: str,
        auth_type: str,
        credentials: dict[str, Any],
        *,
        rate_limit_rpm: int | None = None,
        pull_enabled: bool = True,
        config: dict[str, Any] | None = None,
        webhook_secret: str | None = None,
    ) -> ITSMConnection:
        """Create a new ITSM connection with encrypted credentials.

        Args:
            tenant_id: Tenant scope.
            provider_type: Provider type (e.g. "servicenow", "jira").
            name: Human-readable connection name.
            instance_url: Base URL of the provider instance.
            auth_type: Authentication mode.
            credentials: Plaintext credentials dict (will be encrypted).
            rate_limit_rpm: Rate limit override.
            pull_enabled: Whether to participate in pull ticks.
            config: Provider-specific non-credential configuration.
            webhook_secret: Optional webhook secret (will be encrypted).

        Returns:
            The created ITSMConnection row.

        Raises:
            ITSMProviderError: If the provider type is not registered.
            ValueError: If a duplicate connection already exists.
        """
        # Validate provider type is registered
        if not self._registry.is_registered(provider_type):
            raise ITSMProviderError(
                f"Provider type '{provider_type}' is not registered",
                provider_type=provider_type,
            )

        # Check for duplicate
        existing = await self._get_connection_by_url(tenant_id, provider_type, instance_url)
        if existing:
            raise ValueError(f"Connection for {provider_type} at {instance_url} already exists")

        # Get provider metadata for default rate limit
        metadata = self._registry.get(provider_type)
        default_rpm = metadata.get("default_rate_limit_rpm", 100) if metadata else 100

        # Encrypt credentials
        encrypted_creds = self._credential_manager.encrypt_credentials(credentials)

        # Encrypt webhook secret if provided
        webhook_secret_encrypted = None
        if webhook_secret:
            webhook_secret_encrypted = self._crypto.encrypt(webhook_secret)

        # Find the ITSMProvider row for the FK
        provider_id = await self._get_provider_id(provider_type)

        conn = ITSMConnection(
            tenant_id=tenant_id,
            provider_id=provider_id,
            provider_type=provider_type,
            name=name,
            instance_url=instance_url,
            auth_type=auth_type,
            credentials_encrypted=encrypted_creds,
            webhook_secret_encrypted=webhook_secret_encrypted,
            rate_limit_rpm=rate_limit_rpm or default_rpm,
            pull_enabled=pull_enabled,
            is_active=True,
            status="pending",
            config=config or {},
        )
        self.db.add(conn)
        await self.db.flush()
        await self.db.refresh(conn)

        # Record audit event
        await self._record_audit(
            tenant_id=tenant_id,
            action="create",
            entity_type="itsm_connection",
            entity_id=conn.id,
            description=f"Created ITSM connection '{name}' ({provider_type})",
            new_values={"provider_type": provider_type, "instance_url": instance_url, "name": name},
        )

        logger.info("Created ITSM connection %s for tenant %s (%s)", conn.id, tenant_id, provider_type)
        return conn

    async def get_connection(self, conn_id: uuid.UUID, tenant_id: uuid.UUID) -> ITSMConnection | None:
        """Get a connection by ID (tenant-scoped)."""
        stmt = select(ITSMConnection).where(
            ITSMConnection.id == conn_id,
            ITSMConnection.tenant_id == tenant_id,
            ITSMConnection.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_connections(self, tenant_id: uuid.UUID, *, provider_type: str | None = None) -> list[ITSMConnection]:
        """List active connections for a tenant, optionally filtered by provider type."""
        stmt = select(ITSMConnection).where(
            ITSMConnection.tenant_id == tenant_id,
            ITSMConnection.is_deleted.is_(False),
            ITSMConnection.is_active.is_(True),
        ).order_by(ITSMConnection.name)

        if provider_type:
            stmt = stmt.where(ITSMConnection.provider_type == provider_type)

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update_connection_status(
        self,
        conn: ITSMConnection,
        *,
        status: str | None = None,
        last_error: str | None = None,
        last_sync_at: datetime | None = None,
    ) -> ITSMConnection:
        """Update a connection's status fields."""
        if status is not None:
            conn.status = status
        if last_error is not None:
            conn.last_error = last_error
        if last_sync_at is not None:
            conn.last_sync_at = last_sync_at
        await self.db.flush()
        await self.db.refresh(conn)
        return conn

    async def delete_connection(self, conn: ITSMConnection) -> None:
        """Soft-delete a connection."""
        conn.is_deleted = True
        conn.is_active = False
        conn.status = "disabled"
        await self.db.flush()

        await self._record_audit(
            tenant_id=conn.tenant_id,
            action="delete",
            entity_type="itsm_connection",
            entity_id=conn.id,
            description=f"Deleted ITSM connection '{conn.name}'",
        )

    # ------------------------------------------------------------------
    # Provider instantiation
    # ------------------------------------------------------------------

    def _create_provider(self, conn: ITSMConnection) -> BaseITSMProvider:
        """Instantiate a provider from a connection row.

        Decrypts credentials and creates a provider instance via the
        ProviderFactory. This is a sync method since the factory and
        credential manager are sync.
        """
        decrypted_creds = self._credential_manager.decrypt_credentials(conn.credentials_encrypted)

        # Also decrypt the top-level webhook secret if present
        if conn.webhook_secret_encrypted:
            try:
                decrypted_creds["webhook_secret"] = self._crypto.decrypt(conn.webhook_secret_encrypted)
            except Exception:
                logger.warning("Failed to decrypt webhook secret for connection %s", conn.id)

        return self._factory.create(
            provider_type=conn.provider_type,
            instance_url=conn.instance_url,
            auth_type=conn.auth_type,
            credentials=decrypted_creds,
            rate_limit_rpm=conn.rate_limit_rpm,
            config=conn.config,
        )

    # ------------------------------------------------------------------
    # Connection testing / health check
    # ------------------------------------------------------------------

    async def test_connection(self, conn: ITSMConnection) -> ITSMHealthCheckResult:
        """Test connectivity to the provider instance.

        Instantiates a provider, calls test_connection(), and updates
        the connection's health check fields.
        """
        try:
            provider = self._create_provider(conn)
            result = await provider.test_connection()

            # Update connection health fields
            conn.last_health_check_at = datetime.now(timezone.utc)
            conn.last_health_check_ok = result.healthy
            if result.healthy:
                conn.status = "connected"
                conn.last_error = None
            else:
                conn.status = "error"
                conn.last_error = result.message
            await self.db.flush()

            logger.info("Health check for %s: healthy=%s", conn.name, result.healthy)
            return result

        except Exception as exc:
            conn.last_health_check_at = datetime.now(timezone.utc)
            conn.last_health_check_ok = False
            conn.status = "error"
            conn.last_error = str(exc)
            await self.db.flush()

            logger.warning("Health check failed for %s: %s", conn.name, exc)
            return ITSMHealthCheckResult(healthy=False, message=str(exc))

    # ------------------------------------------------------------------
    # Ticket operations (through provider)
    # ------------------------------------------------------------------

    async def push_ticket_create(
        self,
        conn: ITSMConnection,
        ticket: Ticket,
        correlation_id: str,
    ) -> ITSMTicketResponse:
        """Create a ticket in the external ITSM system.

        Maps our Ticket to ITSMTicketCreate, calls the provider's
        create_ticket, and returns the response.
        """
        provider = self._create_provider(conn)

        payload = ITSMTicketCreate(
            title=ticket.title,
            description=ticket.description or "",
            priority=ticket.priority or "P3",
            category=ticket.category,
            subcategory=ticket.subcategory,
            assignment_group=ticket.assignment_group,
            correlation_id=correlation_id,
            custom_fields=ticket.custom_fields,
        )

        response = await provider.create_ticket(payload)

        # Record audit
        await self._record_audit(
            tenant_id=conn.tenant_id,
            action="create",
            entity_type="ticket",
            entity_id=ticket.id,
            description=f"Created ticket in {conn.provider_type}: {response.external_number or response.external_id}",
            new_values={"external_id": response.external_id, "external_number": response.external_number},
        )

        return response

    async def push_ticket_update(
        self,
        conn: ITSMConnection,
        link: TicketLink,
        ticket: Ticket,
    ) -> ITSMTicketResponse:
        """Update a ticket in the external ITSM system."""
        provider = self._create_provider(conn)

        payload = ITSMTicketUpdate(
            title=ticket.title,
            description=ticket.description,
            priority=ticket.priority,
            status=ticket.status,
            category=ticket.category,
            subcategory=ticket.subcategory,
            assignment_group=ticket.assignment_group,
            custom_fields=ticket.custom_fields,
        )

        response = await provider.update_ticket(link.external_id, payload)

        # Record audit
        await self._record_audit(
            tenant_id=conn.tenant_id,
            action="update",
            entity_type="ticket",
            entity_id=ticket.id,
            description=f"Updated ticket in {conn.provider_type}: {link.external_number or link.external_id}",
        )

        return response

    async def push_ticket_close(
        self,
        conn: ITSMConnection,
        link: TicketLink,
        ticket: Ticket,
        resolution_note: str | None = None,
    ) -> ITSMTicketResponse:
        """Close a ticket in the external ITSM system."""
        provider = self._create_provider(conn)
        response = await provider.close_ticket(link.external_id, resolution_note)

        await self._record_audit(
            tenant_id=conn.tenant_id,
            action="close",
            entity_type="ticket",
            entity_id=ticket.id,
            description=f"Closed ticket in {conn.provider_type}: {link.external_number or link.external_id}",
        )

        return response

    async def push_comment(
        self,
        conn: ITSMConnection,
        link: TicketLink,
        body: str,
        is_internal: bool = True,
    ) -> dict[str, Any]:
        """Add a comment/work note to an external ticket."""
        provider = self._create_provider(conn)
        payload = ITSMCommentCreate(body=body, is_internal=is_internal)
        result = await provider.add_comment(link.external_id, payload)
        return result

    # ------------------------------------------------------------------
    # Pull operations (through provider)
    # ------------------------------------------------------------------

    async def pull_tickets(
        self,
        conn: ITSMConnection,
        *,
        limit: int = 50,
        updated_since: datetime | None = None,
    ) -> list[ITSMTicketResponse]:
        """Pull tickets from the external ITSM system.

        Returns a list of ITSMTicketResponse objects. The caller
        (SyncManager) is responsible for matching these to local
        TicketLink rows and applying updates.
        """
        provider = self._create_provider(conn)
        results = await provider.list_tickets(limit=limit, updated_since=updated_since)

        conn.last_sync_at = datetime.now(timezone.utc)
        await self.db.flush()

        return results

    # ------------------------------------------------------------------
    # Webhook processing
    # ------------------------------------------------------------------

    async def process_webhook(
        self,
        conn: ITSMConnection,
        body: bytes,
        signature_header: str,
    ) -> dict[str, Any]:
        """Process an inbound webhook from the external ITSM system.

        Verifies the signature, parses the payload, and returns the
        parsed data for the SyncManager to apply.
        """
        provider = self._create_provider(conn)

        # Verify signature if webhook secret is configured
        if conn.webhook_secret_encrypted:
            try:
                webhook_secret = self._crypto.decrypt(conn.webhook_secret_encrypted)
                valid = await provider.verify_webhook_signature(body, signature_header, webhook_secret)
                if not valid:
                    raise ITSMProviderError("Invalid webhook signature", provider_type=conn.provider_type)
            except ITSMProviderError:
                raise
            except Exception as exc:
                raise ITSMProviderError(f"Signature verification failed: {exc}", provider_type=conn.provider_type) from exc

        # Parse the payload
        parsed = await provider.parse_webhook_payload(body)

        logger.info("Processed webhook for %s: correlation_id=%s", conn.name, parsed.get("correlation_id"))
        return parsed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_connection_by_url(
        self, tenant_id: uuid.UUID, provider_type: str, instance_url: str
    ) -> ITSMConnection | None:
        """Check for an existing connection with the same URL."""
        stmt = select(ITSMConnection).where(
            ITSMConnection.tenant_id == tenant_id,
            ITSMConnection.provider_type == provider_type,
            ITSMConnection.instance_url == instance_url,
            ITSMConnection.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_provider_id(self, provider_type: str) -> uuid.UUID | None:
        """Get the ITSMProvider row ID for a provider type."""
        stmt = select(ITSMProvider.id).where(ITSMProvider.provider_type == provider_type)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _record_audit(
        self,
        tenant_id: uuid.UUID,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID,
        description: str | None = None,
        old_values: dict | None = None,
        new_values: dict | None = None,
        actor_id: uuid.UUID | None = None,
    ) -> None:
        """Record an audit event."""
        event = AuditEvent(
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_type="system",
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            description=description,
            old_values=old_values or {},
            new_values=new_values or {},
        )
        self.db.add(event)
        await self.db.flush()