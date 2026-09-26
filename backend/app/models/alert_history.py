"""
AlertHistory model. An append-only audit trail of every lifecycle
transition an Alert goes through (opened, updated/re-occurred,
acknowledged, resolved, correlated into an incident, suppressed). Distinct
from Event (which records vendor/platform occurrences) and from Alert's
own mutable columns (which only ever hold the CURRENT state) - this table
exists purely so "what happened to this alert, and when" can be displayed
on an Alert Timeline without inferring it from `updated_at` diffs.

Never updated or soft-deleted after insert - same convention as Event
(TimestampMixin only, no SoftDeleteMixin).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.alert import Alert
    from app.models.tenant import Tenant


class AlertHistory(Base, UUIDPKMixin):
    __tablename__ = "alert_history"
    __table_args__ = (
        Index("ix_alert_history_alert_occurred", "alert_id", "occurred_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alert_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # opened | updated | acknowledged | resolved | correlated | suppressed |
    # unsuppressed. Free-form string (not an enum) so new lifecycle events
    # don't require a migration.
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # Human-readable summary of the transition, e.g. "severity changed
    # warning -> critical" or "grouped into incident <id>".
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Snapshot of whatever fields changed in this transition (e.g.
    # {"severity": {"from": "warning", "to": "critical"}}) - preserved
    # verbatim for audit/replay, same pattern as Event.raw_payload.
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    alert: Mapped["Alert"] = relationship()

    def __repr__(self) -> str:
        return f"<AlertHistory id={self.id} alert_id={self.alert_id} event_type={self.event_type!r}>"
