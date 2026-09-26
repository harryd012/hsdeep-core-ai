"""
Task model — Virtual Company Multi-Agent Organization.

Every task follows a controlled lifecycle:
    CREATED → ASSIGNED → ACCEPTED → INVESTIGATING → EXECUTING →
    VERIFYING → VERIFIED → RECORDED

Failure path:
    EXECUTING → FAILED → RETRY / ESCALATE / REASSIGN
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.incident import Incident
    from app.models.tenant import Tenant
    from app.models.ticket import Ticket


class Task(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    """A unit of work in the virtual company organization."""

    __tablename__ = "tasks"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # -- Identity --
    task_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Task Type & Classification --
    task_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(
        String(10), nullable=False, default="P3", server_default="P3", index=True
    )
    complexity: Mapped[str] = mapped_column(
        String(20), nullable=False, default="medium", server_default="medium"
    )
    risk_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="low", server_default="low"
    )
    required_skills: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), nullable=False, default=list, server_default="{}"
    )
    required_capabilities: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), nullable=False, default=list, server_default="{}"
    )

    # -- Lifecycle State --
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="created", server_default="created", index=True
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")

    # -- Assignment --
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assigned_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    manager_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assigned_agent: Mapped["Agent | None"] = relationship(foreign_keys=[assigned_agent_id])
    assigned_by_agent: Mapped["Agent | None"] = relationship(foreign_keys=[assigned_by_agent_id])
    manager_agent: Mapped["Agent | None"] = relationship(foreign_keys=[manager_agent_id])

    # -- Command Source --
    owner_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    command_source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="message", server_default="message"
    )
    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    # -- Execution & Verification --
    execution_plan: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    execution_result: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    evidence: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    verification_result: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    # -- SOP Integration --
    recommended_sop_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sops.id", ondelete="SET NULL"), nullable=True
    )
    sop_recommendation_score: Mapped[float | None] = mapped_column(Integer, nullable=True)

    # -- Timestamps --
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # -- Audit --
    audit_log: Mapped[list[dict]] = mapped_column(
        MutableList.as_mutable(JSONB), nullable=False, default=list, server_default="[]"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    tenant: Mapped["Tenant"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<Task id={self.id} task_number={self.task_number!r} "
            f"status={self.status!r} type={self.task_type!r}>"
        )

    # -- Linked Entities --
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    incident: Mapped["Incident | None"] = relationship()
    ticket: Mapped["Ticket | None"] = relationship()

    # -- Customer/Project --
    customer_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)