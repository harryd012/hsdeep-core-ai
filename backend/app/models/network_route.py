"""
NetworkRoute model - the universal routing/BGP table entry (spec section 20).

Provider-neutral: a route learned by FortiGate, Cisco, OSPF or read from a
cloud route table is the same row shape. Vendor-native detail lives in
``attributes``; nothing vendor-specific got a column.

BGP neighbour state is a DIFFERENT entity (a session, not a prefix) and lives
in ``BgpNeighbor`` (app/models/bgp_neighbor.py) rather than being folded into
this table as nullable columns - a route with no neighbour and a neighbour with
no route are both legitimate rows.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.tenant import Tenant


class NetworkRoute(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "network_routes"
    __table_args__ = (
        # A device never reports the same prefix+next-hop+protocol twice.
        UniqueConstraint(
            "resource_id", "vrf", "destination", "next_hop", "protocol",
            name="uq_network_routes_identity",
        ),
        Index("ix_network_routes_tenant_protocol", "tenant_id", "protocol"),
        Index("ix_network_routes_resource_protocol", "resource_id", "protocol"),
        Index("ix_network_routes_tenant_state", "tenant_id", "state"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    interface_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("interfaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    #: Vendor-native route identifier, when the provider exposes one.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: Routing context (VRF / VRF-lite / cloud route-table name). '-' is the
    #: global table and is stored explicitly so the unique key is stable.
    vrf: Mapped[str] = mapped_column(
        String(100), nullable=False, default="-", server_default="-"
    )
    destination: Mapped[str] = mapped_column(String(80), nullable=False)
    prefix_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_hop: Mapped[str | None] = mapped_column(String(80), nullable=True)
    gateway: Mapped[str | None] = mapped_column(String(80), nullable=True)
    outgoing_interface: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: connected | static | bgp | ospf | other
    protocol: Mapped[str] = mapped_column(
        String(30), nullable=False, default="other", server_default="other"
    )
    metric: Mapped[int | None] = mapped_column(Integer, nullable=True)
    administrative_distance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: active | inactive | withdrawn | unknown
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown", server_default="unknown"
    )
    is_best: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    learned: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    advertised: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    age_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_change_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    tenant: Mapped["Tenant"] = relationship()
    resource: Mapped["Device"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<NetworkRoute resource_id={self.resource_id} "
            f"destination={self.destination!r} protocol={self.protocol!r}>"
        )
