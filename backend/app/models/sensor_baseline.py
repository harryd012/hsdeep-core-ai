"""
SensorBaseline model (observability plan P9 slice 2).

A sensor's opt-in dynamic baseline: its settings (direction, sensitivity),
the statistics learned from its own history (`stats`, see
`services.monitoring.baseline_math`) and its latest verdict. One row per
sensor; deleting the sensor deletes it.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class SensorBaseline(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "sensor_baselines"
    __table_args__ = (UniqueConstraint("sensor_id", name="uq_sensor_baselines_sensor_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sensor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sensors.id", ondelete="CASCADE"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true", index=True)
    #: both | above | below
    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="both", server_default="both")
    #: Standard deviations from the expected value that count as an anomaly.
    sensitivity: Mapped[float] = mapped_column(Float, nullable=False, default=3.0, server_default="3")
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: learning | normal | anomalous | stale | paused
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="learning", server_default="learning")
    last_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_expected: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
