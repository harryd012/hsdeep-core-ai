"""
Interface model. A network interface (physical port or logical) belonging to
a Device, optionally attributed to the MonitoringSource that reported it.
Normalized so interface_traffic sensors and dashboards can reference a
stable Interface row regardless of vendor (PRTG, Meraki, SNMP, etc).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.monitoring_source import MonitoringSource
    from app.models.tenant import Tenant


class Interface(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "interfaces"
    __table_args__ = (
        # A given device never reports the same interface twice.
        UniqueConstraint("device_id", "external_id", name="uq_interfaces_device_id_external_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable: the monitoring source that collected/reported this interface.
    # Left null if the interface was resolved without a direct source link.
    monitoring_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("monitoring_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # The ID of this interface inside the vendor's system (SNMP ifIndex,
    # Meraki port ID, etc) - stable across syncs, unique per device.
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mac_address: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # up | down | testing | unknown
    admin_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown", server_default="unknown"
    )
    # up | down | testing | unknown
    oper_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown", server_default="unknown", index=True
    )
    speed_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    in_octets: Mapped[float | None] = mapped_column(Float, nullable=True)
    out_octets: Mapped[float | None] = mapped_column(Float, nullable=True)
    in_errors: Mapped[float | None] = mapped_column(Float, nullable=True)
    out_errors: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_status_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ------------------------------------------------------------------
    # UNIVERSAL INTERFACE MODEL (spec section 15) - additive, all nullable.
    #
    # These carry the counter/rate snapshot the canonical interface contract
    # requires. They are the LATEST observed values only (a snapshot), not a
    # time series - history lives in the Metrics table keyed by interface_id.
    # Every field stays NULL when the provider did not report it, so
    # "0 ms latency" is never confused with "latency not measured".
    # ------------------------------------------------------------------
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: physical | logical | vlan | loopback | tunnel | virtual | wan |
    #: sdwan_member | vpn | unknown
    interface_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="unknown", server_default="unknown", index=True
    )
    vlan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Raw negotiated speed in bits per second, for providers that report bps
    #: rather than the Mbps ``speed_mbps`` column.
    speed_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    rx_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    utilization_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    discards_in: Mapped[float | None] = mapped_column(Float, nullable=True)
    discards_out: Mapped[float | None] = mapped_column(Float, nullable=True)
    packet_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    jitter_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_telemetry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    tenant: Mapped["Tenant"] = relationship()
    device: Mapped["Device"] = relationship()
    monitoring_source: Mapped["MonitoringSource | None"] = relationship()

    def __repr__(self) -> str:
        return f"<Interface id={self.id} device_id={self.device_id} external_id={self.external_id!r}>"
