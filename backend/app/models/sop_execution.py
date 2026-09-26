"""
SOPExecution model — Sprint Next: Close the Automation Loop.

Tracks the live execution of an SOP's steps against a specific incident/ticket,
enabling the AutomationRunner to pause on manual/gated steps and resume later
(via a separate API call) rather than blocking the event bus.

Lifecycle:
    running -> awaiting_manual -> running -> ... -> succeeded | failed | abandoned
            -> awaiting_approval -> running -> ...

Mirrors RunbookExecution's lifecycle shape but is tied directly to SOPVersion.steps
rather than a Runbook, since the AutomationRunner operates on SOP steps.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.incident import Incident
    from app.models.sop import SOP
    from app.models.tenant import Tenant
    from app.models.ticket import Ticket
    from app.models.user import User


class SOPExecution(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "sop_executions"
    __table_args__ = (
        # Resume-path index (locate the paused step of a running execution);
        # declared here so the model is the honest source of truth.
        Index("ix_sop_executions_current_step_index", "current_step_index"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sops.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Correlation ID linking this execution to the lifecycle event chain.
    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    # running | awaiting_manual | awaiting_approval | succeeded | failed | abandoned
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running", server_default="running", index=True
    )

    # Index of the next step to execute (0-based). For manual/gated steps
    # this is the index of the step that is currently awaiting action.
    current_step_index: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # Step-by-step results: list of
    #   {"step_index": 0, "step_type": "auto", "action": "...",
    #    "status": "succeeded"|"failed", "output": {...}, "error": "...|None",
    #    "recorded_at": "ISO..."}
    step_results: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    # Who is assigned to handle manual steps.
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Operator who started / approved the execution.
    operator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    result_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rollback_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    rollback_performed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    tenant: Mapped["Tenant"] = relationship()
    sop: Mapped["SOP"] = relationship()
    ticket: Mapped["Ticket | None"] = relationship()
    incident: Mapped["Incident | None"] = relationship()

    def __repr__(self) -> str:
        return f"<SOPExecution id={self.id} sop_id={self.sop_id} status={self.status!r} step={self.current_step_index}>"
