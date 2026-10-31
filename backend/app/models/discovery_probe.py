"""Durable, tenant-scoped probe results and materialization state.

These rows are OBSERVATIONS, deliberately kept separate from canonical
inventory: ``DiscoveryProbeJob`` / ``DiscoveryProbeHost`` record what a scope
probe SAW, while the existing ``Device`` rows are what later got MATERIALIZED
through ``DiscoveryService.discover()`` / ``DeviceRepository.upsert()``. Nothing
here is a canonical resource; device identity/deduplication stays with the
existing ``devices`` table and its ``UNIQUE (tenant_id, serial_number)`` plus
``(monitoring_source_id, external_id)`` constraints.

No secrets are stored: a probe never records credentials or raw scan output,
only the per-host reachability evidence and explicit protocol states
(``reachable`` | ``timeout`` | ``not_configured``).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.discovery_scope import DiscoveryScope
    from app.models.tenant import Tenant


#: Forward-only job statuses. Only ``completed`` jobs are materializable.
JOB_STATUS_RUNNING = "running"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"

#: Per-host materialization outcomes. ``created``/``existing`` are terminal;
#: ``rejected``/``failed``/``None`` may be retried.
OUTCOME_CREATED = "created"
OUTCOME_EXISTING = "existing"
OUTCOME_REJECTED = "rejected"
OUTCOME_FAILED = "failed"
TERMINAL_OUTCOMES: frozenset[str] = frozenset({OUTCOME_CREATED, OUTCOME_EXISTING})

#: Job-level materialization rollup.
MATERIALIZATION_PENDING = "pending"
MATERIALIZATION_PARTIAL = "partial"
MATERIALIZATION_MATERIALIZED = "materialized"


class DiscoveryProbeJob(Base, UUIDPKMixin, TimestampMixin):
    """One completed/attempted scope probe sweep and its materialization state."""

    __tablename__ = "discovery_probe_jobs"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_discovery_probe_jobs_id_tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scope_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("discovery_scopes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalised snapshot so the job is self-describing even if the scope's
    # target/policy later change. Never used to infer ownership.
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=JOB_STATUS_RUNNING,
        server_default=JOB_STATUS_RUNNING, index=True,
    )
    hosts_probed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    hosts_total: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    reachable: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    materialization_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MATERIALIZATION_PENDING,
        server_default=MATERIALIZATION_PENDING,
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tenant: Mapped["Tenant"] = relationship()
    scope: Mapped["DiscoveryScope"] = relationship()
    hosts: Mapped[list["DiscoveryProbeHost"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<DiscoveryProbeJob {self.id} tenant={self.tenant_id} "
            f"scope={self.scope_id} status={self.status!r}>"
        )


class DiscoveryProbeHost(Base, UUIDPKMixin, TimestampMixin):
    """Reachability evidence for ONE address in one probe job."""

    __tablename__ = "discovery_probe_hosts"
    __table_args__ = (
        UniqueConstraint("job_id", "address", name="uq_discovery_probe_hosts_job_id_address"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("discovery_probe_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalised for cheap tenant-scoped reads; ownership always comes from
    # the job/tenant, never from the address.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    address: Mapped[str] = mapped_column(String(45), nullable=False)
    reachable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    packet_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: {protocol: reachable | timeout | not_configured} - explicit, never inferred.
    protocols: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: list[str] of concrete reachability evidence (e.g. "icmp:reply", "tcp/22:open").
    evidence: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    # -- materialization (populated only by the materialize endpoint) ---------
    materialization_outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
        nullable=True,
    )
    materialized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    job: Mapped["DiscoveryProbeJob"] = relationship(back_populates="hosts")
    device: Mapped["Device | None"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<DiscoveryProbeHost {self.address} job={self.job_id} "
            f"reachable={self.reachable} outcome={self.materialization_outcome!r}>"
        )

