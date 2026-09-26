"""
NOC Event model.

A Network Operations Center event — a normalized, severity-classified
operational signal derived from one or more Alerts (or from a direct
webhook/probe check). Tenant-scoped; carries denormalized site_id/device_id
(same denormalization pattern Alert uses) so list/filter queries never need
an extra join just to show "which site".

Lifecycle: open -> acknowledged -> resolved. A NOC event that keeps
receiving the same underlying signal on repeated syncs bumps
occurrence_count on the existing row instead of creating a new one —
same flapping-prevention rationale as Alert. The partial unique index
below enforces "at most one non-resolved NOC event per source+signature"
at the database level, so that invariant holds even under concurrent
syncs, not just in application code.

SOC analysts (Agent rows with department="soc", role="soc_analyst") are
assigned to NOC events via assigned_analyst_id — the SOC roster is
derived from the existing Agent table, not a separate model.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.alert import Alert
    from app.models.device import Device
    from app.models.site import Site
    from app.models.tenant import Tenant


class NocEvent(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "noc_events"
    __table_args__ = (
        Index(
            "uq_noc_events_active_per_signature",
            "source",
            "event_signature",
            unique=True,
            postgresql_where=text(
                "status <> 'resolved' AND event_signature IS NOT NULL AND is_deleted = false"
            ),
        ),
        # Analyst-lookup query paths; declared here so the model is the honest
        # source of truth.
        Index("ix_noc_events_acknowledged_by", "acknowledged_by"),
        Index("ix_noc_events_resolved_by", "resolved_by"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # -- Identity --
    # Free-form probe/signal signature — e.g. "ssl_security:host:993" or
    # "ping:10.0.0.1". Used for flapping dedup (same signature = same event).
    event_signature: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # critical | warning | info
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # open | acknowledged | resolved
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", server_default="open", index=True
    )

    # -- Lifecycle timestamps --
    triggered_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_occurred_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    acknowledged_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    # -- Assignment --
    # SOC analyst (Agent row with department="soc") currently assigned to this event.
    assigned_analyst_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The Agent (any department) that originally raised/owns this event —
    # typically a NOC L1/L2 agent or a monitoring-source automation.
    source_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # -- Metrics --
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # -- Relationships --
    tenant: Mapped["Tenant"] = relationship()
    site: Mapped["Site | None"] = relationship()
    device: Mapped["Device | None"] = relationship()
    assigned_analyst: Mapped["Agent | None"] = relationship(
        foreign_keys=[assigned_analyst_id],
    )
    source_agent: Mapped["Agent | None"] = relationship(
        foreign_keys=[source_agent_id],
    )

    def __repr__(self) -> str:
        return f"<NocEvent id={self.id} severity={self.severity!r} status={self.status!r} signature={self.event_signature!r}>"

    @property
    def is_open(self) -> bool:
        return self.status == "open" and not self.is_deleted

    @property
    def duration_minutes(self) -> float | None:
        """Minutes between trigger and resolution (or now if still open)."""
        if self.triggered_at is None:
            return None
        end = self.resolved_at or self.last_occurred_at
        if end is None:
            return None
        delta = end - self.triggered_at
        return delta.total_seconds() / 60.0
