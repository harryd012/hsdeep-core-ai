"""
BgpNeighbor model - one BGP peering session per row (spec section 20).

Separate from ``NetworkRoute`` because a session and a prefix are different
entities with different lifecycles: a neighbour can be Established while
advertising nothing, and a route can exist with no session (static/connected).
Modelling them as one table would force nullable columns that mean different
things on each row.

Tenant-scoped, provider-neutral. Vendor-native detail lives in ``attributes``.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
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


class BgpNeighbor(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "bgp_neighbors"
    __table_args__ = (
        # One session per (device, VRF, neighbour address).
        UniqueConstraint(
            "resource_id", "vrf", "neighbor_ip", name="uq_bgp_neighbors_identity"
        ),
        Index("ix_bgp_neighbors_tenant_state", "tenant_id", "state"),
        Index("ix_bgp_neighbors_resource", "resource_id"),
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
    )
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)

    #: Routing context. '-' is the global table, stored explicitly so the
    #: unique key stays stable for devices that report no VRF.
    vrf: Mapped[str] = mapped_column(
        String(100), nullable=False, default="-", server_default="-"
    )
    neighbor_ip: Mapped[str] = mapped_column(String(80), nullable=False)
    local_ip: Mapped[str | None] = mapped_column(String(80), nullable=True)
    remote_as: Mapped[str | None] = mapped_column(String(30), nullable=True)
    local_as: Mapped[str | None] = mapped_column(String(30), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: Canonical FSM state: idle | connect | active | opensent | openconfirm |
    #: established | unknown
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown", server_default="unknown", index=True
    )
    #: admin_status: enabled | disabled (administratively shut).
    admin_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown", server_default="unknown"
    )
    uptime_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    prefixes_received: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prefixes_advertised: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_state_change_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    tenant: Mapped["Tenant"] = relationship()
    resource: Mapped["Device"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<BgpNeighbor resource_id={self.resource_id} "
            f"neighbor_ip={self.neighbor_ip!r} state={self.state!r}>"
        )
