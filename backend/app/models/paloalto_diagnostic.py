"""Palo Alto diagnostic execution models.

Additive, tenant-scoped persistence for the Palo Alto Firewall Diagnostic
module. Nothing here alters existing tables: three new tables with
tenant-leading indexes, explicit lifecycle states, and bounded retention
support (retention enforced by a scheduled purge, not by this model).

Tables:
  pa_diagnostic_executions - one row per diagnostic run (incident/device).
  pa_diagnostic_steps      - per-step state for an execution.
  pa_evidence_refs         - evidence references attached to steps/executions.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class PaloAltoDiagnosticExecution(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "pa_diagnostic_executions"
    __table_args__ = (
        Index("ix_pa_diag_exec_tenant_status", "tenant_id", "status"),
        Index("ix_pa_diag_exec_tenant_incident", "tenant_id", "incident_id"),
        Index("ix_pa_diag_exec_tenant_device", "tenant_id", "device_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending",
        index=True,
    )
    risk: Mapped[str] = mapped_column(
        String(20), nullable=False, default="read_only", server_default="read_only",
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    current_step_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PaloAltoDiagnosticStep(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "pa_diagnostic_steps"
    __table_args__ = (
        Index("ix_pa_diag_step_exec", "execution_id", "step_id", unique=True),
        Index("ix_pa_diag_step_tenant", "tenant_id", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pa_diagnostic_executions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    step_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending",
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class PaloAltoEvidenceRef(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "pa_evidence_refs"
    __table_args__ = (
        Index("ix_pa_evidence_tenant_kind", "tenant_id", "kind"),
        Index("ix_pa_evidence_exec", "execution_id"),
        Index("ix_pa_evidence_device", "tenant_id", "source_device_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pa_diagnostic_executions.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    step_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    kind: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source_device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True,
    )
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    verified: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
