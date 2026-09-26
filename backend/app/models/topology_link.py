"""
TopologyLink model — a first-class infrastructure link / WAN / VPN edge.

This is the Section 17 (Topology Data Model) complement to
``ResourceRelationship``. Where ``ResourceRelationship`` is a generic,
type-tagged dependency/session edge between two devices, ``TopologyLink``
is a *physical or logical transport link* carrying the operational link
attributes the MPLS/OSPF/VPN topology standard requires:

* source/destination interfaces + admin/operational status
* bandwidth, latency, packet loss, utilization (live counter snapshot)
* provider + circuit identifier (MPLS/VPN circuits, WAN links)
* VPN / VRF association (multiple VPNs/VRFs per link), routing protocol,
  redundancy group (for redundant-path analysis)
* last state change + discovery provenance

The bearer is a single undirected link; the repository normalises
(device_a, device_b) deterministically so re-discovery upserts idempotently.

Everything is tenant-scoped; writes require an active subscription (enforced
at the API layer).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.tenant import Tenant


class TopologyLink(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "topology_links"
    __table_args__ = (
        # One bearer per device pair + link name is unique within a tenant,
        # so re-discovery/idempotent upserts never create duplicates.
        UniqueConstraint(
            "tenant_id", "device_a_id", "device_b_id", "name",
            name="uq_topology_links_tenant_devices_name",
        ),
        Index("ix_topology_links_device_a", "device_a_id"),
        Index("ix_topology_links_device_b", "device_b_id"),
        Index("ix_topology_links_link_type", "link_type"),
        Index("ix_topology_links_oper_status", "oper_status"),
        Index("ix_topology_links_tenant_status", "tenant_id", "oper_status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # -- Endpoints ----------------------------------------------------------
    device_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    device_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_interface_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interfaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    dest_interface_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interfaces.id", ondelete="SET NULL"),
        nullable=True,
    )

    # -- Identity / classification ------------------------------------------
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # wan | lan | serial | mpls | vpn_tunnel | backbone | cloud | loopback | other
    link_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="other", server_default="other", index=True
    )
    discovered_by: Mapped[str] = mapped_column(
        String(50), nullable=False, default="manual", server_default="manual"
    )

    # -- Provider / circuit (Section 17 Link attributes) --------------------
    provider: Mapped[str | None] = mapped_column(String(150), nullable=True)
    circuit_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # -- Operational telemetry ----------------------------------------------
    bandwidth_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    packet_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    utilization_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    admin_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="up", server_default="up"
    )
    oper_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="up", server_default="up", index=True
    )
    last_state_change_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # -- Routing / VPN attributes (Section 17) -------------------------------
    routing_protocols: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    ospf: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    bgp: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    vrfs: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    vpn_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    vpn_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    redundancy_group: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_redundant: Mapped[bool] = mapped_column(
        "is_redundant", nullable=False, default=False
    )

    # -- Free-form overflow -------------------------------------------------
    attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    tenant: Mapped["Tenant"] = relationship()
    device_a: Mapped["Device"] = relationship(foreign_keys=[device_a_id])
    device_b: Mapped["Device"] = relationship(foreign_keys=[device_b_id])

    def __repr__(self) -> str:
        return (
            f"<TopologyLink id={self.id} {self.device_a_id} <-> {self.device_b_id} "
            f"({self.link_type}) status={self.oper_status}>"
        )
