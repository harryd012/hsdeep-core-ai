"""
CollectorRun model. An audit record of a single execution of a monitoring
source's collector (scheduled or manually triggered) - what it did, how long
it took, and whether it succeeded. This is what powers "last run" status in
the UI and lets the scheduler avoid starting a new run while one is already
in flight for the same source.

Not soft-deletable and has no updated_at churn - a run is an immutable
history record once finished, so it only uses UUIDPKMixin.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.monitoring_source import MonitoringSource
    from app.models.tenant import Tenant


class CollectorRun(Base, UUIDPKMixin):
    __tablename__ = "collector_runs"
    __table_args__ = (
        Index("ix_collector_runs_source_started", "monitoring_source_id", "started_at"),
        Index("ix_collector_runs_tenant_started", "tenant_id", "started_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    monitoring_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("monitoring_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # scheduler | manual
    trigger: Mapped[str] = mapped_column(
        String(20), nullable=False, default="scheduler", server_default="scheduler"
    )
    # running | success | partial | error | skipped
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running", server_default="running", index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    devices_upserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    interfaces_upserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sensors_upserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    metrics_recorded: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    events_recorded: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    alerts_upserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship()
    monitoring_source: Mapped["MonitoringSource"] = relationship()

    def __repr__(self) -> str:
        return f"<CollectorRun id={self.id} source_id={self.monitoring_source_id} status={self.status!r}>"
