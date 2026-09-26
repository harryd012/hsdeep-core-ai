"""
Usage counter model for multi-tenant metering and billing hooks.

Counters are upserted idempotently by (tenant_id, event_type, bucket_start)
to support exactly-once counting in the ingestion pipeline.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UsageCounter(Base):
    """Aggregated usage counter bucketed by hour.

    Each row represents the count of a specific event type for a tenant
    within a single hourly bucket. Rows are upserted idempotently so that
    replaying ingestion never double-counts.
    """

    __tablename__ = "usage_counters"

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # Tenant
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Metering Event
    event_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )

    # Hour Bucket
    bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Counter
    count: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    # Audit Fields
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "event_type",
            "bucket_start",
            name="uq_usage_tenant_event_bucket",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<UsageCounter("
            f"id={self.id}, "
            f"tenant_id={self.tenant_id}, "
            f"event_type={self.event_type}, "
            f"bucket_start={self.bucket_start}, "
            f"count={self.count})>"
        )


# ============================================================
# Canonical Event Types
# ============================================================

EVENT_TYPE_ALERT_INGESTED = "alert.ingested"
EVENT_TYPE_METRIC_INGESTED = "metric.ingested"
EVENT_TYPE_COLLECTOR_RUN = "collector.run"
EVENT_TYPE_AUTH_LOGIN = "auth.login"
EVENT_TYPE_SOP_EXECUTED = "sop.executed"
EVENT_TYPE_WORKFLOW_EXECUTED = "workflow.executed"
EVENT_TYPE_API_CALL = "api.call"