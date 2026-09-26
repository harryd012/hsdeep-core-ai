"""
Autonomous Remediation Engine models (Phase 7).

ExecutionJob, ExecutionLog, and RollbackJob for playbook/runbook
execution with dry run, blast radius protection, rollback, circuit
breaker, timeout, retry, and execution audit.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class ExecutionJob(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "execution_jobs"
    __table_args__ = (
        # Lookup + circuit-breaker query paths; declared here so the model is
        # the honest source of truth.
        Index("ix_execution_jobs_approval_request_id", "approval_request_id"),
        Index("ix_execution_jobs_circuit_breaker_tripped", "circuit_breaker_tripped"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    source_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending", index=True
    )
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    target: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    variables: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approval_requests.id", ondelete="SET NULL"), nullable=True
    )
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    blast_radius: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    circuit_breaker_tripped: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=3600, server_default="3600")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    rollback_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rollback_jobs.id", ondelete="SET NULL"), nullable=True
    )
    was_rolled_back: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    tenant: Mapped["Tenant"] = relationship()
    logs: Mapped[list["ExecutionLog"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="ExecutionLog.step_order"
    )

    def __repr__(self) -> str:
        return f"<ExecutionJob id={self.id} source_type={self.source_type!r} status={self.status!r}>"


class ExecutionLog(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "execution_logs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    execution_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("execution_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    step_title: Mapped[str] = mapped_column(String(255), nullable=False)
    executor: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    input_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    output_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    was_dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    job: Mapped["ExecutionJob"] = relationship(back_populates="logs")

    def __repr__(self) -> str:
        return f"<ExecutionLog id={self.id} step_order={self.step_order} status={self.status!r}>"


class RollbackJob(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "rollback_jobs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    execution_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("execution_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending", index=True
    )
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rollback_steps: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<RollbackJob id={self.id} execution_job_id={self.execution_job_id} status={self.status!r}>"