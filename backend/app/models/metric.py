"""
Metric model. An append-only time series of raw readings emitted by
sensors/devices/monitoring sources. High write volume, read via time-range
queries scoped to a sensor or tenant - so this table intentionally skips
TimestampMixin/SoftDeleteMixin (no updated_at, no soft-delete) and only
carries a manual created_at, keeping the row shape lean.

CANONICAL TELEMETRY EXTENSION (universal infrastructure intelligence)
--------------------------------------------------------------------
This is ALSO the canonical telemetry store - there is deliberately no second
telemetry table. The columns added by the universal layer turn a numeric-only
sensor reading into the full canonical contract from spec section 16:

    identity  provider / capability (+ existing tenant/device/sensor/interface)
    value     numeric_value(=value) | text_value | boolean_value + unit
    quality   status / collection_status / quality / source_timestamp /
              received_timestamp
    context   dimensions

``value`` therefore becomes NULLABLE: a canonical record can legitimately carry
a text or boolean value instead of a number, and an unsupported capability is
recorded as an explicit state rather than as a fabricated 0.

BACKWARD COMPATIBILITY: every existing caller of ``MetricRepository.record()``
writes ``value`` exactly as before and simply leaves the new columns at their
defaults (``collection_status='collected'``). Nothing that worked before the
universal layer stops working.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class Metric(Base, UUIDPKMixin):
    __tablename__ = "metrics"
    __table_args__ = (
        Index("ix_metrics_sensor_recorded", "sensor_id", "recorded_at"),
        Index("ix_metrics_tenant_recorded", "tenant_id", "recorded_at"),
        # Canonical telemetry read paths (spec section 33): latest value and
        # time-range queries are always scoped to a capability, and usually to
        # a resource or an interface.
        Index("ix_metrics_tenant_capability_recorded", "tenant_id", "capability", "recorded_at"),
        Index("ix_metrics_device_capability_recorded", "device_id", "capability", "recorded_at"),
        Index("ix_metrics_interface_recorded", "interface_id", "recorded_at"),
        Index("ix_metrics_tenant_metric_recorded", "tenant_id", "metric_type", "recorded_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    monitoring_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("monitoring_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sensor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sensors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # NEW, additive: interface-scoped canonical telemetry (spec section 15).
    interface_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("interfaces.id", ondelete="SET NULL"),
        nullable=True,
    )

    metric_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # NEW, additive: the canonical metric name. Equals metric_type for legacy
    # rows written before the universal layer (backfilled by the migration).
    metric_name: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    #: Nullable since the universal layer: text/boolean readings are valid.
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # -- canonical capability + quality (spec section 16) -------------------
    capability: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    collection_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="collected", server_default="collected", index=True
    )
    quality: Mapped[str | None] = mapped_column(String(20), nullable=True)
    text_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    boolean_value: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dimensions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Metric id={self.id} type={self.metric_type!r} value={self.value}>"
