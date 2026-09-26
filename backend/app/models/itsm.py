"""
Universal ITSM (IT Service Management) Framework models.

This module implements the provider-agnostic ITSM architecture that lets
HSDEEP CORE AI integrate with ANY ITSM platform (ServiceNow, Jira,
Freshservice, HaloITSM, Zendesk, ManageEngine, etc.) through a single
abstract interface.

Three additive tables (never duplicate existing ServiceNow entities):

`ITSMProvider` - System-level catalog of available provider types
(servicenow, jira, freshservice, ...). Seeded once at startup. NOT
tenant-scoped (same as the existing `permissions` table pattern in
core/permissions.py). Each row declares the provider's capabilities,
supported auth modes, and the Python class path of its implementation.

`ITSMConnection` - Tenant-scoped connection to an ITSM provider instance.
This is the UNIVERSAL connection table: one row per (tenant, provider,
instance_url). It stores encrypted credentials in a JSONB column
(`credentials_encrypted`) rather than dedicated columns, so a new
provider's auth fields need no schema migration - the same "free-form
JSONB, not dedicated columns" convention the existing `Workflow.trigger_filter`
and `SOP` matching criteria already use.

`ITSMSyncJob` - The universal background queue / retry queue / dead letter
queue for ITSM sync operations. Same polymorphic design as the existing
`ServiceNowSyncJob` (job_type distinguishes push/pull/webhook), but
provider-agnostic so Jira/Freshservice/etc. sync jobs live in the same
queue. `status="dead_letter"` is reached after `retry_count >= max_retries`,
same audit-trail convention as ServiceNowSyncJob.

The existing `ServiceNowConnection`/`ServiceNowIncidentLink`/
`ServiceNowSyncJob` tables are REUSED (not replaced) - the ServiceNow
provider adapter wraps them for backward compatibility, while new
providers use these universal tables exclusively.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant


# ---------------------------------------------------------------------------
# System-level provider catalog (NOT tenant-scoped)
# ---------------------------------------------------------------------------

class ITSMProvider(Base, UUIDPKMixin, TimestampMixin):
    """System-level catalog of available ITSM provider types.

    Seeded once at application startup (see services/itsm/provider_registry.py).
    Each row declares a provider's capabilities and the Python class path
    of its `BaseITSMProvider` implementation. NOT tenant-scoped - same
    pattern as the `permissions` table (core/permissions.py).

    Adding a new provider = adding a row here + implementing the provider
    class. No schema migration needed for the provider catalog itself.
    """

    __tablename__ = "itsm_providers"
    __table_args__ = (
        UniqueConstraint("provider_type", name="uq_itsm_providers_provider_type"),
    )

    # servicenow | jira | freshservice | haloitsm | zendesk | manageengine
    # Free-form string, not an enum, so a new provider type needs no
    # migration - same convention as MonitoringSource.source_type.
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Human-readable display name (e.g. "ServiceNow", "Jira Service Management")
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Full Python class path of the BaseITSMProvider implementation,
    # e.g. "app.services.itsm.providers.servicenow.ServiceNowProvider".
    # The ProviderFactory imports this dynamically.
    class_path: Mapped[str] = mapped_column(String(500), nullable=False)

    # Supported authentication modes as a JSONB list of strings,
    # e.g. ["oauth2", "basic", "api_key", "pat"]. Free-form so a new
    # auth mode needs no migration.
    supported_auth_modes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    # Capability flags - what this provider can do. JSONB dict of
    # {capability: bool}, e.g. {"create_ticket": true, "attachments": true,
    # "bi_directional_sync": true, "webhooks": true, "cmdb": false}.
    # The ConnectorManager checks these before invoking provider methods.
    capabilities: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    # Default rate limit (requests per minute) for this provider type.
    # Per-connection overrides live on ITSMConnection.rate_limit_rpm.
    default_rate_limit_rpm: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )

    # Whether this provider is available for new connections. Set to False
    # to hide a provider from the connection UI without removing its row.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )

    def __repr__(self) -> str:
        return f"<ITSMProvider provider_type={self.provider_type!r} display_name={self.display_name!r}>"


# ---------------------------------------------------------------------------
# Tenant-scoped connection to a provider instance
# ---------------------------------------------------------------------------

class ITSMConnection(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    """Tenant-scoped connection to an ITSM provider instance.

    This is the UNIVERSAL connection table - one row per
    (tenant, provider_type, instance_url). Replaces the need for
    provider-specific connection tables for NEW providers; the existing
    `ServiceNowConnection` table is kept for backward compatibility and
    the ServiceNow provider adapter can work with either.

    Credentials are stored as encrypted JSONB (`credentials_encrypted`)
    rather than dedicated columns, so a new provider's auth fields need
    no schema migration. The JSONB structure is provider-specific:
    e.g. for ServiceNow: {"username": "...", "password_encrypted": "...",
    "oauth_client_id": "...", "oauth_client_secret_encrypted": "..."}.
    All secret values within the JSONB are Fernet-encrypted via
    SecretCrypto before storage, same "always ciphertext, never plaintext"
    convention as MonitoringSource/ServiceNowConnection.
    """

    __tablename__ = "itsm_connections"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider_type",
            "instance_url",
            name="uq_itsm_connections_tenant_provider_url",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # FK to ITSMProvider - which provider type this connection uses.
    # Nullable to allow connections created before the provider catalog
    # is seeded (edge case during migration).
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("itsm_providers.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Denormalized provider_type for fast filtering without a join.
    # Must match ITSMProvider.provider_type for the linked provider_id.
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Human-readable name for this connection (e.g. "Prod ServiceNow",
    # "Jira Cloud - ACME"). Same convention as ServiceNowConnection.name.
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # The base URL of the provider instance, e.g.
    # "https://acme.service-now.com" or "https://acme.atlassian.net".
    # No trailing slash, enforced by the provider implementation.
    instance_url: Mapped[str] = mapped_column(String(500), nullable=False)

    # Authentication mode: oauth2 | basic | api_key | pat | certificate
    # Free-form string, not an enum, same "new auth mode needs no
    # migration" convention as ServiceNowConnection.auth_type.
    auth_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="oauth2", server_default="oauth2"
    )

    # Encrypted credentials JSONB. Structure is provider/auth-type specific.
    # All secret values are Fernet-encrypted via SecretCrypto before
    # storage. Example for ServiceNow OAuth2:
    #   {"oauth_client_id": "abc",  (plaintext - not a secret)
    #    "oauth_client_secret_encrypted": "gAAAA...",
    #    "oauth_access_token_encrypted": "gAAAA...",
    #    "oauth_refresh_token_encrypted": "gAAAA...",
    #    "webhook_secret_encrypted": "gAAAA..."}
    credentials_encrypted: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    # OAuth2 token expiry (if applicable). Stored at the connection level
    # so the CredentialManager can proactively refresh before expiry.
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Inbound webhook signature verification secret - encrypted.
    # Same convention as ServiceNowConnection.webhook_secret_encrypted.
    # Stored within credentials_encrypted for most providers, but also
    # available as a top-level field for providers that need it separate.
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Rate limiting - configurable per-connection since different
    # provider instances/plans have different API quotas. Same convention
    # as ServiceNowConnection.rate_limit_requests_per_minute.
    rate_limit_rpm: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )

    # Whether this connection should participate in periodic pull ticks.
    # Defaults to True. Same convention as ServiceNowConnection.pull_enabled.
    pull_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )

    # pending | connected | error | disabled - mirrors
    # ServiceNowConnection.status and MonitoringSource.status.
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending", index=True
    )

    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Health check tracking - last time a health check was performed
    # and its result. The HealthMonitor updates these periodically.
    last_health_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_health_check_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Provider-specific configuration that is NOT credentials (e.g.
    # default assignment group, default project key, field mappings).
    # Free-form JSONB, same convention as Workflow.trigger_filter.
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    tenant: Mapped["Tenant"] = relationship()

    def __repr__(self) -> str:
        return f"<ITSMConnection id={self.id} provider_type={self.provider_type!r} instance_url={self.instance_url!r} status={self.status!r}>"


# ---------------------------------------------------------------------------
# Universal sync job queue (push/pull/webhook, provider-agnostic)
# ---------------------------------------------------------------------------

class ITSMSyncJob(Base, UUIDPKMixin, TimestampMixin):
    """The universal ITSM background queue + retry queue + dead letter queue.

    Same polymorphic design as the existing `ServiceNowSyncJob`:
    `job_type` distinguishes ticket_push | ticket_pull | webhook_inbound,
    and `status="dead_letter"` is reached after `retry_count >= max_retries`.
    Dead-letter rows are never auto-deleted - they are the audit trail of
    "this sync permanently failed" and require an explicit API-triggered
    retry.

    This table is used by ALL providers EXCEPT ServiceNow (which keeps
    using ServiceNowSyncJob for backward compatibility). The ServiceNow
    provider adapter can optionally route new jobs here in a future
    migration.
    """

    __tablename__ = "itsm_sync_jobs"
    __table_args__ = (
        # Retry-scheduler query path (due jobs by next attempt); declared here
        # so the model is the honest source of truth.
        Index("ix_itsm_sync_jobs_next_attempt_at", "next_attempt_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("itsm_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ticket_push | ticket_pull | webhook_inbound - free-form, not an
    # enum, so a future job type (problem_push, change_push, ...) needs
    # no migration. Same convention as ServiceNowSyncJob.job_type.
    job_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    # pending | processing | succeeded | failed | dead_letter
    # Same lifecycle as ServiceNowSyncJob.status.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending", index=True
    )

    # The real, already-known identifiers this job operates on - nullable
    # since a webhook_inbound job may not yet know our ticket_id until its
    # correlation_id is resolved. Same convention as ServiceNowSyncJob.
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # External ticket link ID (if a TicketLink row exists). Nullable for
    # the same reason as ticket_id.
    ticket_link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ticket_links.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # The job's own input - e.g. for webhook_inbound, the verified raw
    # webhook body; for ticket_push/pull, an empty dict (the job re-reads
    # the current Ticket record at execution time, never carries a stale
    # snapshot). Same convention as ServiceNowSyncJob.payload.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")

    # Exponential backoff target - the queue-drain tick only claims rows
    # where next_attempt_at <= now(). Same convention as
    # ServiceNowSyncJob.next_attempt_at.
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship()
    connection: Mapped["ITSMConnection"] = relationship()

    def __repr__(self) -> str:
        return f"<ITSMSyncJob id={self.id} job_type={self.job_type!r} status={self.status!r} retry_count={self.retry_count}>"