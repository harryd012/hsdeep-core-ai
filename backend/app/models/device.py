"""
Device model. Belongs to a Site (and, denormalized for query performance and
tenant-isolation enforcement, directly to a Tenant as well).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.business_service import BusinessService
    from app.models.site import Site
    from app.models.tenant import Tenant
    from app.models.zone import Zone


class Device(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "serial_number", name="uq_devices_tenant_id_serial_number"),
        Index(
            "uq_devices_monitoring_source_id_external_id",
            "monitoring_source_id",
            "external_id",
            unique=True,
            postgresql_where=(
                "monitoring_source_id IS NOT NULL AND external_id IS NOT NULL"
            ),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable: collector-created devices are attached directly to a
    # monitoring source and may not have a resolved site yet.
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sites.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("zones.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Nullable: the monitoring source that collected/owns this device (e.g.
    # a PRTG probe, a Meraki org). Devices created outside the Collector
    # Framework (manual entry) leave this null.
    monitoring_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("monitoring_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Vendor-native identifier for this device within its monitoring source,
    # used together with monitoring_source_id to upsert idempotently.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    device_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # Nullable: collector-created devices may lack a resolved vendor serial.
    serial_number: Mapped[str | None] = mapped_column(String(150), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="unknown", server_default="unknown")
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    mac_address: Mapped[str | None] = mapped_column(String(50), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    model: Mapped[str | None] = mapped_column(String(150), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # NEW, additive (AI Priority Engine sprint): critical | high | medium |
    # low. How important this specific device is (e.g. a core router vs. a
    # test lab switch) - one of BusinessImpactEngine's inputs. Nullable,
    # same "unclassified = medium/neutral" convention as Site.criticality.
    criticality: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    # NEW, additive: optional link to the BusinessService this device
    # supports (e.g. a DB server backing "Customer Portal"). Nullable -
    # most devices have no explicit service mapping yet; BusinessImpactEngine
    # falls back to device/site criticality alone when this is unset.
    business_service_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("business_services.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # NEW, additive (Sections 13-23 topology standard): enterprise routing/
    # security ROLE of the device within the topology (e.g. core | distribution |
    # access | ce | pe | p | firewall_edge | load_balancer | hypervisor | storage).
    # `role` is the human topology role; `role_class` groups it for filtering
    # (network | security | compute | storage | cloud | wireless | other). Both
    # nullable: existing devices are left unclassified rather than guessed.
    role: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    role_class: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)

    # NEW, additive (Sections 13-23 location hierarchy). Denormalised so the
    # topology/region/site drill-downs never need a join chain to Site. All
    # nullable and optional on create — populated by discovery or CMDB sync.
    country: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    campus: Mapped[str | None] = mapped_column(String(100), nullable=True)
    building: Mapped[str | None] = mapped_column(String(100), nullable=True)
    datacenter_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rack: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # CMDB fields introduced by revision 1be605e3f990. These columns already
    # exist in deployed databases and must be mapped for asset serialization.
    environment: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    business_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    support_team: Mapped[str | None] = mapped_column(String(255), nullable=True)
    maintenance_window: Mapped[str | None] = mapped_column(String(255), nullable=True)
    warranty_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    warranty_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True, server_default="[]")

    # NEW, additive: custom_metadata overflow used by collectors to persist
    # vendor-specific fields (routing protocols, serial provenance, etc).
    custom_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ------------------------------------------------------------------
    # UNIVERSAL INFRASTRUCTURE INTELLIGENCE (additive, nullable)
    #
    # These five columns make the existing Device row the canonical universal
    # RESOURCE (spec section 4) without creating a second device table:
    #   provider          the provider/collector token that owns this resource
    #   parent_resource_id hierarchy (cloud account -> resource, cluster -> node)
    #   lifecycle_state    discovered | active | maintenance | retired |
    #                      decommissioned
    #   health             last computed health bucket (healthy | warning |
    #                      critical | unknown | maintenance); NULL until a
    #                      health pass has actually run - never pre-filled
    #   discovered_at /    provenance + telemetry freshness. last_telemetry_at
    #   last_telemetry_at  is NULL when no canonical telemetry was ever
    #                      received, which the API reports as
    #                      reason=no_telemetry_ever_received rather than as
    #                      "healthy".
    #
    # The provider-native identifier is NOT duplicated here: it is the existing
    # ``external_id`` column, exposed through the ``provider_resource_id``
    # property below.
    # ------------------------------------------------------------------
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    parent_resource_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    lifecycle_state: Mapped[str] = mapped_column(
        String(30), nullable=False, default="discovered", server_default="discovered", index=True
    )
    health: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    discovered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_telemetry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="devices")
    site: Mapped["Site | None"] = relationship(back_populates="devices")
    zone: Mapped["Zone | None"] = relationship(back_populates="devices")
    business_service: Mapped["BusinessService | None"] = relationship()

    @property
    def provider_resource_id(self) -> str | None:
        """Provider-native identifier for this resource.

        Deliberately NOT a second column: ``external_id`` already holds the
        vendor's own id (PRTG objid, Meraki serial, Azure resource id) and is
        unique per monitoring source. Duplicating it would create two sources
        of truth for the same fact.
        """
        return self.external_id

    @property
    def resource_type(self) -> str:
        """Universal resource type for this row.

        ``device_type`` stays the stored vocabulary (models/device_types.py);
        this exposes the universal normalisation on top of it so callers never
        re-implement the alias mapping.
        """
        from app.services.infrastructure_intelligence.vocabulary import (
            universal_resource_type,
        )

        return universal_resource_type(self.device_type)

    def __repr__(self) -> str:
        return f"<Device id={self.id} tenant_id={self.tenant_id} serial_number={self.serial_number!r}>"
