"""
Resource Relationship model.

Generic topology representation for infrastructure dependencies.
Supports both PHYSICAL/NETWORK relationships and LOGICAL/SERVICE dependencies.

Relationship types:
  CONNECTED_TO      - Physical/network connection (switch port to server)
  DEPENDS_ON        - Service dependency (app depends on database)
  PROTECTED_BY      - Security relationship (network protected by firewall)
  MANAGED_BY        - Management relationship (device managed by FortiManager)
  MONITORED_BY      - Monitoring relationship (device monitored by PRTG)
  LOGGED_TO         - Logging relationship (device logs to FortiAnalyzer/Syslog)
  ROUTES_THROUGH    - Routing relationship (traffic routes through firewall)
  PEERS_WITH        - Peering relationship (BGP peers, HA peers)
  HOSTED_ON         - Hosting relationship (VM hosted on hypervisor)
  CONTAINS          - Containment relationship (site contains devices)
  MEMBER_OF         - Membership relationship (device member of zone)
  BACKUP_FOR        - Backup relationship (backup for primary)
  FAILOVER_TO       - Failover relationship (failover target)
  SERVES            - Service relationship (server serves application)

This is a core HSDEEP capability for impact analysis and root cause detection.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.tenant import Tenant


class ResourceRelationship(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    """A directed relationship between two resources (devices).

    The topology graph is built from these edges. Both physical connections
    (switch->server) and logical dependencies (app->database) are represented
    generically, letting the correlation engine traverse either kind.
    """

    __tablename__ = "resource_relationships"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_resource_id",
            "target_resource_id",
            "relationship_type",
            name="uq_resource_relationships",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # -- Edge endpoints --
    # Both endpoints are devices (generic — could be firewall, switch, server, VM, etc)
    source_resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # -- Relationship classification --
    # connected_to | depends_on | protected_by | managed_by | monitored_by |
    # logged_to | routes_through | peers_with | hosted_on | contains |
    # member_of | backup_for | failover_to | serves
    relationship_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # Free-form sub-type (e.g., "bgp", "ha", "lag", "vlan")
    relationship_subtype: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # -- Metadata --
    # Human-readable label (e.g., "eth0", "BGP session", "HA pair")
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Weight/capacity hint (bandwidth in Mbps, dependency strength 0-1, etc)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Confidence that this relationship is correct (0-100)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)

    # -- Discovery --
    # How was this relationship discovered? manual | lldp | bgp | snmp | api | inferred
    discovered_by: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # True if the relationship was inferred (not directly observed)
    is_inferred: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # -- Status --
    # active | degraded | failed | unknown
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active", index=True
    )

    # -- Additional context --
    attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    def __repr__(self) -> str:
        return (
            f"<ResourceRelationship {self.source_resource_id} -> "
            f"{self.target_resource_id} ({self.relationship_type})>"
        )
