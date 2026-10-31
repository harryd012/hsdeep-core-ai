"""
Firewall gateway state (docs/architecture/firewall-gateway.md).

`firewall_status`: one row per firewall device, refreshed by every poll that
read the firewall tables. Columns the device did not report stay NULL.

`vpn_tunnels`: one row per (device, tunnel name). `ever_up` records that the
tunnel has been seen up at least once, so a tunnel that has never come up
(idle dial-up) is shown but never alerts.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class FirewallStatus(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "firewall_status"
    __table_args__ = (UniqueConstraint("device_id", name="uq_firewall_status_device"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    monitoring_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("monitoring_sources.id", ondelete="SET NULL"), nullable=True
    )
    ha_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ha_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ha_sync: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ha_peer_serial: Mapped[str | None] = mapped_column(String(100), nullable=True)
    session_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpu_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: [{"feature", "expires_at" (ISO or null), "status": valid|expiring|expired}]
    licences: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    #: Outcome per table from the last poll: {"status": "collected", ...}
    table_status: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    #: When the device last reported its firewall status (freshness).
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VpnTunnel(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "vpn_tunnels"
    __table_args__ = (UniqueConstraint("device_id", "name", name="uq_vpn_tunnels_device_name"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tunnel_type: Mapped[str] = mapped_column(String(20), nullable=False, default="ipsec")
    remote_gateway: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # up | down
    ever_up: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    phase2_up: Mapped[int | None] = mapped_column(Integer, nullable=True)
    phase2_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bytes_in: Mapped[float | None] = mapped_column(Float, nullable=True)
    bytes_out: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
