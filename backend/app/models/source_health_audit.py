"""
SourceHealthAudit model — an immutable audit trail of every health-state
transition and operational event for a MonitoringSource.

Every entry records:
  - What changed (from/to health_status, from/to consecutive_failures, etc.)
  - Why it changed (free-text reason)
  - Which CollectorRun triggered the transition (foreign key)
  - When it happened

This is the single source of truth for the "Audit trail of state
transitions" requirement. Rows are insert-only — never updated or deleted.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.collector_run import CollectorRun
    from app.models.monitoring_source import MonitoringSource
    from app.models.tenant import Tenant


# Operational event types logged in the audit trail. These are broader than
# just health-status transitions — they also cover timeouts, retries,
# rate-limiting, and duplicate-skip events.
SOURCE_HEALTH_EVENT_TRANSITION = "health_transition"
SOURCE_HEALTH_EVENT_RECOVERY = "recovery"
SOURCE_HEALTH_EVENT_RETRY = "retry_attempt"
SOURCE_HEALTH_EVENT_TIMEOUT = "timeout"
SOURCE_HEALTH_EVENT_RATE_LIMITED = "rate_limited"
SOURCE_HEALTH_EVENT_DUPLICATE_SKIPPED = "duplicate_skipped"

# Valid set for validation.
SOURCE_HEALTH_EVENT_TYPES = frozenset({
    SOURCE_HEALTH_EVENT_TRANSITION,
    SOURCE_HEALTH_EVENT_RECOVERY,
    SOURCE_HEALTH_EVENT_RETRY,
    SOURCE_HEALTH_EVENT_TIMEOUT,
    SOURCE_HEALTH_EVENT_RATE_LIMITED,
    SOURCE_HEALTH_EVENT_DUPLICATE_SKIPPED,
})


class SourceHealthAudit(Base, UUIDPKMixin):
    """Immutable audit row for a MonitoringSource health-event."""

    __tablename__ = "source_health_audits"
    __table_args__ = (
        Index(
            "ix_source_health_audits_source_created",
            "monitoring_source_id",
            "created_at",
        ),
        Index(
            "ix_source_health_audits_tenant_created",
            "tenant_id",
            "created_at",
        ),
        Index(
            "ix_source_health_audits_event_type",
            "event_type",
            "created_at",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    monitoring_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("monitoring_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The CollectorRun that triggered this audit entry, if any. NULL for
    # tick-level events (e.g. duplicate-skip) that don't produce a run.
    collector_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("collector_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # What kind of event this is (health_transition, recovery, retry_attempt,
    # timeout, rate_limited, duplicate_skipped).
    # NOTE: no index=True here - the composite index
    # ix_source_health_audits_event_type (event_type, created_at) declared in
    # __table_args__ already leads on event_type. Declaring both produced a
    # duplicate-name collision in the model and a redundant single-column
    # index in the database (ix_source_health_audits_event_type_2).
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)

    # Health status before/after (nullable — only set for health_transition
    # and recovery events).
    from_health_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_health_status: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Consecutive failure count before/after the event.
    from_consecutive_failures: Mapped[int | None] = mapped_column(Integer, nullable=True)
    to_consecutive_failures: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # MonitoringSource.status before/after (nullable — only relevant when the
    # run-level status also changed).
    from_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Human-readable reason for this audit entry (e.g. "collection timed out
    # after 300s", "source recovered after 2 consecutive successes").
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    tenant: Mapped["Tenant"] = relationship()
    monitoring_source: Mapped["MonitoringSource"] = relationship()
    collector_run: Mapped["CollectorRun | None"] = relationship()
