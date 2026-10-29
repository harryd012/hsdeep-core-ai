"""
InterfaceAddress model - one IP address configured on a device interface.

The L3 topology needs to know which devices share a subnet. An interface can
carry several addresses (secondaries, IPv6 alongside IPv4), so addresses are
their own rows rather than columns on ``interfaces``.

``network`` is the normalised CIDR (``10.1.2.0/24``) computed by the repository
from ``address`` + ``prefix_length`` with the stdlib ``ipaddress`` module - it
is never taken from the vendor payload - so every collector groups the same way.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.interface import Interface
    from app.models.tenant import Tenant


class InterfaceAddress(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "interface_addresses"
    __table_args__ = (
        # A device never holds the same address twice in one routing context.
        UniqueConstraint("device_id", "vrf", "address", name="uq_interface_addresses_identity"),
        # Subnet grouping for the L3 read model.
        Index("ix_interface_addresses_tenant_vrf_network", "tenant_id", "vrf", "network"),
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
    #: Null when the collector named an interface that has no Interface row.
    interface_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("interfaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    monitoring_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("monitoring_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)

    #: Interface name as the collector reported it, kept even when it did not
    #: resolve to an Interface row (hover cards show it either way).
    interface_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Routing context; '-' is the global table (same convention as network_routes).
    vrf: Mapped[str] = mapped_column(String(100), nullable=False, default="-", server_default="-")
    #: 4 | 6
    family: Mapped[int] = mapped_column(Integer, nullable=False)
    address: Mapped[str] = mapped_column(String(45), nullable=False)
    prefix_length: Mapped[int] = mapped_column(Integer, nullable=False)
    network: Mapped[str] = mapped_column(String(49), nullable=False)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    device: Mapped["Device"] = relationship()
    interface: Mapped["Interface | None"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<InterfaceAddress device_id={self.device_id} "
            f"address={self.address}/{self.prefix_length} vrf={self.vrf!r}>"
        )
