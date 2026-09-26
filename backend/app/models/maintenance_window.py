"""
MaintenanceWindow model (AI Priority Engine sprint). A tenant-scoped,
optionally site- or device-scoped time range during which incidents should
be treated as expected/lower-priority (a planned outage, not an operational
emergency). Consumed by PriorityEngine.maintenance_window_factor() - never
mutates Incident/Alert data itself, purely a read input to scoring.

Scope resolution: a window with site_id/device_id set to NULL applies
tenant-wide; a window with only site_id set applies to every device at that
site; a window with device_id set applies to that device only. This mirrors
the existing nullable-scope pattern already used by
MonitoringSource.site_id (tenant/global-scoped when null).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.site import Site
    from app.models.tenant import Tenant


class MaintenanceWindow(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "maintenance_windows"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=True, index=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, index=True
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    tenant: Mapped["Tenant"] = relationship()
    site: Mapped["Site | None"] = relationship()
    device: Mapped["Device | None"] = relationship()

    def __repr__(self) -> str:
        return f"<MaintenanceWindow id={self.id} title={self.title!r} starts_at={self.starts_at} ends_at={self.ends_at}>"
