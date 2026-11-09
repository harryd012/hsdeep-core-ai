"""
TraceSpan model (observability plan P8 slice 1).

One OpenTelemetry span received through OTLP/HTTP (`/api/v1/ingest/otlp/v1/traces`).
The tenant comes from the collector credential, never from the payload; the
(tenant, trace, span) key makes a resent batch a no-op. Attributes and events
are kept bounded (see `services.tracing.otlp`), so one noisy service cannot
grow a row without limit. Retention follows the tenant's metric retention.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class TraceSpan(Base, UUIDPKMixin):
    __tablename__ = "trace_spans"
    __table_args__ = (
        UniqueConstraint("tenant_id", "trace_id", "span_id", name="uq_trace_spans_tenant_trace_span"),
        Index("ix_trace_spans_tenant_start", "tenant_id", "start_time"),
        Index("ix_trace_spans_tenant_service_start", "tenant_id", "service_name", "start_time"),
        Index("ix_trace_spans_tenant_trace", "tenant_id", "trace_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    monitoring_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("monitoring_sources.id", ondelete="SET NULL"), nullable=True
    )
    #: Lower-case hex: 32 characters for a trace, 16 for a span.
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    span_id: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    service_name: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    #: internal | server | client | producer | consumer | unspecified
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    #: unset | ok | error
    status_code: Mapped[str] = mapped_column(String(10), nullable=False)
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    resource_attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    events: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
