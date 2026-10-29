"""
One row per (incident, channel, event): the delivery log for incident
notifications. The unique key makes delivery idempotent across worker ticks
and processes; failed rows keep their error and are retried a bounded
number of times (app/services/notifications/dispatcher.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class IncidentNotification(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "incident_notifications"
    __table_args__ = (
        UniqueConstraint("incident_id", "channel", "event", name="uq_incident_notifications_incident_channel_event"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)  # email | slack | pagerduty
    event: Mapped[str] = mapped_column(String(20), nullable=False)  # opened | resolved
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # sent | failed
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
