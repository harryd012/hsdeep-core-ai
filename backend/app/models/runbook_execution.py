"""
RunbookExecution model - SOP & Runbook Intelligence Engine sprint. The
"ExecutionHistory" deliverable: one row per attempt to run a Runbook
against a specific Incident, tracking the full manual-approval-workflow
lifecycle and the outcome fields the sprint explicitly asks for (operator,
start/end time, duration, success/failure, rollback required).

Lifecycle: pending_approval -> approved -> running -> succeeded | failed
                            \\-> rejected                    \\-> rolled_back

`SOPMatcher`'s historical-success-rate input (see
services/sop/sop_matcher.py) is computed by aggregating
`succeeded`/`failed` counts from this table per `(tenant_id, sop_id)` -
this table is the ONLY source of that signal, never fabricated.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.incident import Incident
    from app.models.runbook import Runbook
    from app.models.tenant import Tenant
    from app.models.user import User


class RunbookExecution(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "runbook_executions"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    runbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("runbooks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # pending_approval | approved | rejected | running | succeeded | failed
    # | rolled_back
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending_approval", server_default="pending_approval", index=True
    )

    operator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Denormalized for cheap reporting queries (avg duration by SOP, etc.)
    # rather than recomputing from started_at/completed_at on every read.
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    result_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rollback_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    rollback_performed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    tenant: Mapped["Tenant"] = relationship()
    runbook: Mapped["Runbook"] = relationship(back_populates="executions")
    incident: Mapped["Incident | None"] = relationship()

    def __repr__(self) -> str:
        return f"<RunbookExecution id={self.id} runbook_id={self.runbook_id} status={self.status!r}>"
