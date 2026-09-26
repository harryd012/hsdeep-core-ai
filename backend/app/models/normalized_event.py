"""
Normalized Event model.

A vendor-neutral, normalized representation of telemetry from any source
(PNETLab, FortiGate, Security Onion, AWS, Azure, GCP, SNMP, Syslog, etc).

This is NOT the same as NocEvent:
  - Raw Telemetry -> NormalizedEvent (this model) -> NocEvent (operational)
  - One NormalizedEvent may generate zero or one NocEvent (after correlation)
  - Many raw signals may deduplicate into one NormalizedEvent

The model is designed for high-volume ingestion with tenant isolation,
deduplication support, and correlation references.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant
    from app.models.site import Site
    from app.models.device import Device


class NormalizedEvent(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    """A single normalized telemetry event from any source.

    NormalizedEvents are the common currency of the telemetry plane — every
    adapter (PNETLab, FortiGate, Security Onion, SNMP, Syslog, etc) writes
    into this one shape. CorrelationEngine and NocEventService consume these.
    """

    __tablename__ = "normalized_events"
    __table_args__ = (
        # Dedup: at most one active (non-suppressed) event per source+signature
        Index(
            "uq_normalized_events_active_per_signature",
            "integration_id",
            "event_signature",
            unique=True,
            postgresql_where=text(
                "is_suppressed = false AND event_signature IS NOT NULL AND is_deleted = false"
            ),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # -- Source context --
    # The integration that produced this event (nullable for manually-created)
    integration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Denormalized location for query performance (same pattern as Alert/NocEvent)
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # -- Identity --
    # Vendor-native event identifier (e.g., FortiGate logid, Security Onion alert ID)
    source_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # Free-form signature for dedup (e.g., "fw:deny:10.0.0.1:443" or "ids:ssh-brute")
    event_signature: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)

    # -- Classification --
    # health | network | security | performance | routing | vpn | sdwan | configuration | cloud | system | application | topology
    event_category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # Free-form sub-type within category (e.g., "bgp_session_down", "fw_policy_deny")
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # critical | warning | info
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # -- Content --
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Structured attributes (protocol, src_ip, dst_ip, port, action, etc)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    # -- Temporal --
    event_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # -- Correlation --
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    # Reference to the NocEvent this normalized event contributed to
    noc_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("noc_events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # True when correlation has suppressed this as a symptom of a root cause
    is_suppressed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", index=True
    )

    # -- Telemetry metadata --
    # METRIC | LOG | EVENT | ALERT | SECURITY_EVENT | HEALTH | TOPOLOGY_CHANGE
    telemetry_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # Confidence that normalization correctly classified this event (0-100)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Raw source payload reference (for forensics/debugging)
    raw_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # -- Volume tracking --
    occurrence_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    last_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # -- Relationships --
    tenant: Mapped["Tenant"] = relationship()
    site: Mapped["Site | None"] = relationship()
    device: Mapped["Device | None"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<NormalizedEvent id={self.id} category={self.event_category!r} "
            f"type={self.event_type!r} severity={self.severity!r}>"
        )
