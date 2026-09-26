"""Approval Engine models (Phase 6)."""
from __future__ import annotations
import uuid
from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class ApprovalRequest(Base, UUIDPKMixin, TimestampMixin):
    """An approval request for a ticket, playbook, or execution job."""
    __tablename__ = "approval_requests"
    __table_args__ = (
        # Query-path indexes added by the approval-engine migrations; declared
        # here so the model is the honest source of truth (previously these
        # existed only in the database and `alembic check` wanted to drop them).
        Index("ix_approval_requests_deadline", "deadline"),
        Index("ix_approval_requests_required_role", "required_role"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    approval_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending", index=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    required_role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stage: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    total_stages: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_emergency: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    delegated_from: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    delegated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    tenant: Mapped["Tenant"] = relationship()
    history: Mapped[list["ApprovalHistory"]] = relationship(back_populates="approval_request", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<ApprovalRequest id={self.id} entity_type={self.entity_type!r} status={self.status!r}>"


class ApprovalHistory(Base, UUIDPKMixin, TimestampMixin):
    """The audit trail of every approval action."""
    __tablename__ = "approval_history"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    approval_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approval_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    stage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    approval_request: Mapped["ApprovalRequest"] = relationship(back_populates="history")

    def __repr__(self) -> str:
        return f"<ApprovalHistory id={self.id} action_type={self.action_type!r}>"