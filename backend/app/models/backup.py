"""
Backup monitoring state (docs/engineering/azure-backup-monitoring.md).

`backup_vaults`: one row per vault, linked to the vault's cloud_resource
Device so alerts and inventory share one identity. Posture settings the
provider did not report stay NULL.

`backup_items`: one row per protected item (VM, database, file share), with
the provider's last backup verdict and time. Items a successful read no longer
lists are deleted (the provider stopped reporting them).

`backup_jobs`: job history, upserted by the provider's job id. Rows older
than `backup_job_retention_days` are purged after each save.

`backup_policies`: one row per policy in a vault - its schedule and how long
each tier of restore points is kept. Items reference a policy by name.

`backup_settings`: per-tenant (per-customer) backup targets; at most one row
per tenant. A tenant without a row uses the platform default RPO.

The tables are provider-neutral: a "vault" is any backup storage location
(`kind`: vault | repository | bucket) and every status uses the vocabulary in
`app.collectors.backup_vocabulary`.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    #: vault | repository | bucket
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="vault", server_default="vault")
    #: A copy is kept off-site / in another region; NULL when not reported.
    offsite: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: Restore points are irreversibly immutable until they expire; NULL when not reported.
    immutable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resource_group: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subscription_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    soft_delete_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    immutability_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    storage_redundancy: Mapped[str | None] = mapped_column(String(30), nullable=True)
    #: Backup storage the vault reports as used, bytes; NULL until reported.
    storage_used_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Size and free space of a fixed-size location (Veeam repository); NULL otherwise.
    capacity_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    free_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
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
    recovery_point_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    oldest_recovery_point_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Backup data held for this item, bytes; NULL when not reported.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
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


class BackupPolicy(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "backup_policies"
    __table_args__ = (UniqueConstraint("vault_id", "name", name="uq_backup_policies_vault_name"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vault_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backup_vaults.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    workload_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    #: daily | weekly | hourly
    schedule_frequency: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: Start times, "HH:MM" UTC.
    schedule_times: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    daily_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weekly_retention_weeks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_retention_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    yearly_retention_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    protected_items_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BackupSettings(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "backup_settings"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_backup_settings_tenant"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    #: Hours a protected item may go without a successful backup; NULL uses
    #: the platform default (settings.backup_rpo_hours).
    rpo_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
