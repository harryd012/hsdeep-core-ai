"""
Backup monitoring state (docs/engineering/azure-backup-monitoring.md).

`backup_vaults`: one row per vault, linked to the vault's cloud_resource
Device so alerts and inventory share one identity. Posture settings the
provider did not report stay NULL.

`backup_items`: one row per protected item (VM, database, file share), with
the provider's last backup verdict and time. Items a successful read no longer
lists are deleted (the provider stopped reporting them).

`backup_jobs`: job history, upserted by the provider's job id. Retention is
applied separately (B2), never by a collection run.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class BackupVault(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "backup_vaults"
    __table_args__ = (UniqueConstraint("tenant_id", "external_id", name="uq_backup_vaults_tenant_external"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    monitoring_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("monitoring_sources.id", ondelete="SET NULL"), nullable=True
    )
    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="azure", server_default="azure")
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resource_group: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subscription_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    soft_delete_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    immutability_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    storage_redundancy: Mapped[str | None] = mapped_column(String(30), nullable=True)
    #: Outcome per table from the last poll: {"vaults": "collected", ...}
    table_status: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BackupItem(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "backup_items"
    __table_args__ = (UniqueConstraint("vault_id", "external_id", name="uq_backup_items_vault_external"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vault_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backup_vaults.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(700), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    workload_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    protection_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_backup_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_backup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_resource_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    policy_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    health_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BackupJob(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "backup_jobs"
    __table_args__ = (UniqueConstraint("vault_id", "external_id", name="uq_backup_jobs_vault_external"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vault_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backup_vaults.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(700), nullable=False)
    item_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operation: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
