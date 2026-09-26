"""
AssetRelationship model. Represents a directed edge between two assets
(e.g. Device -> Interface, Device -> Sensor, Device -> Application,
Device -> BusinessService, Device -> MonitoringSource).

Sprint 16 (Service Dependency graph) will query this table directly to
build full dependency chains from a root asset.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class AssetRelationship(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "asset_relationships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "asset_id", "related_asset_id", "relationship_type", name="uq_asset_relationships"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The "source" asset in this directed relationship
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The "target" asset
    related_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Relationship type: interface | sensor | application | business_service | collector | dependency
    relationship_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # Optional display label (e.g. "eth0", "PRTG-01", "Payments API")
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Weight/capacity hint for graph algorithms (e.g. bandwidth, dependency weight)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)

    def __repr__(self) -> str:
        return f"<AssetRelationship {self.asset_id} -> {self.related_asset_id} ({self.relationship_type})>"
