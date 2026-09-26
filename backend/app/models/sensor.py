"""
Sensor model. A single monitored metric or check reported by a
MonitoringSource (a PRTG sensor, a Meraki device health check, an Azure
Monitor metric, etc). Normalized to one shape regardless of vendor so the
rest of the platform (Alert Engine, Correlation Engine, dashboards) never
needs to know which vendor a reading came from.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.interface import Interface
    from app.models.monitoring_source import MonitoringSource
    from app.models.tenant import Tenant


class Sensor(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "sensors"
    __table_args__ = (
        # A given source never reports the same external object twice.
        UniqueConstraint(
            "monitoring_source_id", "external_id", name="uq_sensors_source_id_external_id"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    monitoring_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("monitoring_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Best-effort link to a known Device. Left null until identity resolution
    # matches this sensor to a device (by serial/hostname/IP) - a sensor can
    # exist and be alertable before that match happens.
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Best-effort link to a known Interface (e.g. for interface_traffic
    # sensors). Left null until identity resolution matches this sensor to
    # an interface, or for sensors that aren't interface-scoped at all.
    interface_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("interfaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # The ID of this object inside the vendor's system (PRTG sensor ID,
    # Meraki serial + metric name, etc) - stable across syncs.
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Normalized metric category: ping, cpu, memory, disk, interface_traffic,
    # wan_status, http, snmp_custom, etc. Free-form string (not an enum) so
    # new adapters can introduce new metric types without a migration.
    metric_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)

    current_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    warning_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    critical_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)

    # up | down | warning | unknown - computed by the sync service from the
    # raw adapter reading plus the thresholds above.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown", server_default="unknown", index=True
    )
    last_value_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship()
    monitoring_source: Mapped["MonitoringSource"] = relationship(back_populates="sensors")
    device: Mapped["Device | None"] = relationship()
    interface: Mapped["Interface | None"] = relationship()

    def __repr__(self) -> str:
        return f"<Sensor id={self.id} metric={self.metric_type!r} status={self.status!r}>"
