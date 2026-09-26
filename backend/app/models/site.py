"""
Site model, tenant-scoped. Represents a physical location/facility that
devices belong to.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.tenant import Tenant
    from app.models.zone import Zone


class Site(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "sites"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_sites_tenant_id_code"),
        # Region rollup query path (alert correlation); declared here so the
        # model is the honest source of truth.
        Index("ix_sites_region", "region"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # NEW, additive (Alert Correlation Engine sprint): free-form geographic
    # grouping label (e.g. "us-east", "emea", "apac"). Used to denormalize
    # Alert.region at alert-creation time so region-based filtering/rollups
    # never need a join back to Site. Nullable since existing sites have no
    # region assigned yet - not backfilled by this migration.
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # NEW, additive (AI Priority Engine sprint): critical | high | medium |
    # low. How important this physical site is to the business (e.g. a
    # flagship HQ vs. a small branch office) - one of the inputs to
    # BusinessImpactEngine.site_criticality_score(). Nullable - an
    # unclassified site is treated as "medium" (neutral) by the scoring
    # engine rather than forcing every existing row to be reclassified by
    # this migration.
    criticality: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    # NEW, additive (Sections 13-23 location hierarchy): country/city/campus/
    # building/datacenter/rack round out the Site level of the Tenant -> Region ->
    # Country -> City -> Campus -> Building -> Data Center -> Site hierarchy.
    country: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    campus: Mapped[str | None] = mapped_column(String(100), nullable=True)
    building: Mapped[str | None] = mapped_column(String(100), nullable=True)
    datacenter_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rack: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # NEW, additive (Section 17 topology standard): qualitative site role, e.g.
    # headquarters | branch | datacenter | colocation | campus_core.
    site_type: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    # NEW, additive (Section 17 hierarchy): parent site for site-to-site links.
    parent_site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="SET NULL"), nullable=True,
    )
    # NEW, additive: coordinates for the topology map.
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    # NEW, additive: rich physical site payload (ports, floors, power, links).
    site_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="sites")
    devices: Mapped[list["Device"]] = relationship(
        back_populates="site", cascade="all, delete-orphan"
    )
    zones: Mapped[list["Zone"]] = relationship(
        back_populates="site", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Site id={self.id} tenant_id={self.tenant_id} code={self.code!r}>"
