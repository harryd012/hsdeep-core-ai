"""
ServiceNow Integration models - ServiceNow Foundation sprint.

Three additive tables:

`ServiceNowConnection` - one per tenant (per ServiceNow instance): connection
config + encrypted credentials, same "always Fernet ciphertext, never
plaintext" convention as MonitoringSource.api_key_encrypted/password_encrypted
(see models/monitoring_source.py, core/security.py).

`ServiceNowIncidentLink` - the bidirectional-sync mapping row: one per
(our Incident) <-> (ServiceNow incident sys_id) pair. This is the
"Correlation IDs" + "Duplicate Detection" + "Conflict Resolution" deliverable
- `correlation_id` is what ServiceNow's own `correlation_id` field on the
incident table gets set to (so a webhook update can look the link up by that
value alone, without needing our internal UUID), and `last_pushed_*`/
`last_pulled_*` hashes are what conflict resolution compares against to
detect "did the OTHER side change since we last saw it" without needing a
third system of record.

`ServiceNowSyncJob` - the Background Queue + Retry Queue + Dead Letter Queue,
unified into one polymorphic table (a DLQ needs real queryable/retriable
rows, not a JSONB blob) with `job_type` distinguishing incident_push |
incident_pull | webhook_inbound. `status="dead_letter"` is reached after
`retry_count >= max_retries` - conceptually similar to CollectorRun's
stale-run reconciliation, applied to a retriable queue instead of a single
long-running collection.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.incident import Incident
    from app.models.tenant import Tenant


class ServiceNowConnection(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "servicenow_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "instance_url", name="uq_servicenow_connections_tenant_id_instance_url"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # e.g. "https://acme.service-now.com" - no trailing slash, enforced by
    # ServiceNowClient, not this model (model stores exactly what's given).
    instance_url: Mapped[str] = mapped_column(String(500), nullable=False)

    # basic | oauth2 - free-form, not an enum, same "new auth mode needs no
    # migration" convention as MonitoringSource.source_type.
    auth_type: Mapped[str] = mapped_column(String(20), nullable=False, default="oauth2", server_default="oauth2")

    # Basic Auth credentials (used when auth_type="basic") - always Fernet
    # ciphertext, never plaintext, exact precedent as
    # MonitoringSource.password_encrypted.
    username: Mapped[str | None] = mapped_column(String(150), nullable=True)
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # OAuth 2.0 credentials (used when auth_type="oauth2") - client_secret
    # and both tokens are always Fernet ciphertext. client_id is not a
    # secret (ServiceNow OAuth client IDs are not sensitive on their own)
    # so it stays plaintext, same treatment MonitoringSource gives
    # `username` (not encrypted) vs `password` (encrypted).
    oauth_client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    oauth_client_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Inbound webhook signature verification secret (ServiceNow Business
    # Rule -> our /api/servicenow/webhooks/incoming endpoint signs its
    # payload with this, HMAC-SHA256, same "never plaintext" rule).
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Rate Limiting deliverable - configurable per-connection since
    # different ServiceNow instances/plans have different API quotas.
    rate_limit_requests_per_minute: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true", index=True)
    # Whether this connection should participate in periodic pull ticks.
    # Defaults to True for backward compatibility (existing connections keep
    # pulling). Phase 2 per-connection control: admin sets False to disable
    # auto-pull while keeping the connection active for push/webhook paths.
    pull_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending", index=True
    )  # pending | connected | error | disabled - mirrors MonitoringSource.status
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    tenant: Mapped["Tenant"] = relationship()

    def __repr__(self) -> str:
        return f"<ServiceNowConnection id={self.id} instance_url={self.instance_url!r} auth_type={self.auth_type!r}>"


class ServiceNowIncidentLink(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "servicenow_incident_links"
    __table_args__ = (
        UniqueConstraint("connection_id", "incident_id", name="uq_sn_incident_links_connection_id_incident_id"),
        UniqueConstraint("connection_id", "sn_sys_id", name="uq_sn_incident_links_connection_id_sn_sys_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("servicenow_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ServiceNow's own internal row identity for the linked `incident`
    # table record - nullable only in the brief instant between "push job
    # created" and "push job actually got a sys_id back from ServiceNow".
    sn_sys_id: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    # ServiceNow's human-readable incident number (e.g. "INC0010023") -
    # display-only, never used as a lookup key (sys_id is the real key).
    sn_number: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Set on BOTH our Incident's push (as `correlation_id` on the
    # ServiceNow record, via the Table API) and read back on every pull -
    # this is what lets a ServiceNow-side webhook update be matched back
    # to this link WITHOUT needing our internal UUID to be known to
    # ServiceNow at all (ServiceNow only ever sees this string).
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # our_incident_is_source | servicenow_is_source | bidirectional -
    # governs which side's value wins on a genuine conflict (see
    # incident_sync_service.py's conflict resolution). Free-form, not an
    # enum, so a future strategy needs no migration.
    sync_direction: Mapped[str] = mapped_column(
        String(30), nullable=False, default="bidirectional", server_default="bidirectional"
    )

    # Duplicate Detection / Conflict Resolution deliverable: a hash of the
    # fields we last pushed/pulled, so the sync service can tell "has the
    # OTHER side actually changed since we last synced" without needing a
    # third system of record or naive full-payload diffing on every sync.
    last_pushed_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_pulled_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_pulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Set when a genuine two-sided conflict is detected (both sides changed
    # since the last sync) and sync_direction="bidirectional" can't
    # unambiguously resolve it - surfaced via the API for manual review
    # rather than silently picking a winner.
    has_unresolved_conflict: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", index=True
    )
    conflict_details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    tenant: Mapped["Tenant"] = relationship()
    connection: Mapped["ServiceNowConnection"] = relationship()
    incident: Mapped["Incident"] = relationship()

    def __repr__(self) -> str:
        return f"<ServiceNowIncidentLink id={self.id} incident_id={self.incident_id} sn_sys_id={self.sn_sys_id!r}>"


class ServiceNowSyncJob(Base, UUIDPKMixin, TimestampMixin):
    """The unified Background Queue + Retry Queue + Dead Letter Queue.
    `status="dead_letter"` rows are never auto-deleted - they are the
    audit trail of "this sync permanently failed" and require an explicit
    API-triggered retry (see api/routes/servicenow.py) to re-enter the
    queue."""

    __tablename__ = "servicenow_sync_jobs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("servicenow_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # incident_push | incident_pull | webhook_inbound - free-form, not an
    # enum, so a future job type (problem_push, change_push, ...) needs no
    # migration.
    job_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    # pending | processing | succeeded | failed | dead_letter
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending", index=True
    )

    # The real, already-known identifiers this job operates on - nullable
    # since a webhook_inbound job may not yet know our incident_id until
    # its correlation_id is resolved against ServiceNowIncidentLink.
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    incident_link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("servicenow_incident_links.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # The job's own input - e.g. for webhook_inbound, the verified raw
    # webhook body; for incident_push/pull, an empty dict (the job re-reads
    # the current real Incident/ServiceNow record itself at execution
    # time, never carries a stale snapshot as its source of truth).
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")
    # Exponential backoff target - the queue-drain tick only claims rows
    # where next_attempt_at <= now(), same "list_due()"-style query
    # MonitoringSourceRepository already uses.
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship()
    connection: Mapped["ServiceNowConnection"] = relationship()
    incident: Mapped["Incident | None"] = relationship()
    incident_link: Mapped["ServiceNowIncidentLink | None"] = relationship()

    def __repr__(self) -> str:
        return f"<ServiceNowSyncJob id={self.id} job_type={self.job_type!r} status={self.status!r} retry_count={self.retry_count}>"
