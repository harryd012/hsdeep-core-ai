"""
Zone model, tenant-scoped.

A logical grouping within a site — e.g. a network zone, security zone,
or availability zone. Sites contain zones, zones contain resources.

Hierarchy: Tenant -> Site -> Zone -> Resource (Device)
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.site import Site
    from app.models.tenant import Tenant


class Zone(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "zones"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id", "code", name="uq_zones_tenant_id_site_id_code"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # network | security | availability | application | dmz | management
    zone_type: Mapped[str] = mapped_column(String(50), nullable=False, default="network")

    # Relationships
    tenant: Mapped["Tenant"] = relationship()
    site: Mapped["Site"] = relationship(back_populates="zones")
    devices: Mapped[list["Device"]] = relationship(
        back_populates="zone", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Zone id={self.id} tenant_id={self.tenant_id} code={self.code!r}>"
