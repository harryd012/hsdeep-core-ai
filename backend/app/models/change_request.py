"""
Change Request model (CAB - Change Advisory Board).

Generic change management for infrastructure operations:
- Firmware upgrades
- Firewall policy changes
- Routing/VPN changes
- Cloud network changes
- Configuration deployments

Lifecycle: draft -> submitted -> approved -> scheduled -> implementing -> verifying -> closed
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.tenant import Tenant
    from app.models.user import User


class ChangeRequest(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    """A formal change request with approval workflow."""

    __tablename__ = "change_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "change_number",
            name="uq_change_requests_tenant_id_change_number",
        ),
        # Risk-based filtering query path; declared here so the model is the
        # honest source of truth.
        Index("ix_change_requests_risk_level", "risk_level"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # -- Identity --
    change_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # -- Classification --
    # firmware_upgrade | policy_change | routing_change | vpn_change | cloud_change | configuration | other
    change_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # critical | high | medium | low
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    # standard | normal | emergency
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")

    # -- Lifecycle --
    # draft | submitted | approved | rejected | scheduled | implementing | verifying | closed | cancelled
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="draft", server_default="draft", index=True
    )

    # -- Planning --
    business_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    implementation_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    backout_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    test_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    security_impact: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Impact assessment --
    affected_devices: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    affected_services: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    downtime_expected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    downtime_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # -- Maintenance window --
    maintenance_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    maintenance_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # -- Approval workflow --
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Execution --
    implemented_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    implemented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    implementation_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    rollback_executed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # -- Verification --
    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_result: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Closure --
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closure_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Relationships --
    tenant: Mapped["Tenant"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<ChangeRequest id={self.id} number={self.change_number!r} "
            f"type={self.change_type!r} status={self.status!r}>"
        )
