"""
TenantPrivacySetting (enterprise security programme S5): one row per tenant
that changed a privacy default. No row = defaults (AI on with secrets and email
addresses redacted, platform retention, no deletion pending).

AI data use: ``ai_mode`` "on" or "off" (every AI feature refused for the
tenant); secrets are always redacted from prompts, email and IP addresses too
when the flags say so.

Retention: metric and raw event history older than the given days is purged
for this tenant; null = the platform default (METRIC_RETENTION_DAYS, else keep).

Offboarding: ``deletion_scheduled_for`` set = the tenant asked to be deleted;
everything it owns is purged on that date unless the request is cancelled.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

AI_MODES = ("on", "off")
RETENTION_RANGE_DAYS = (7, 3650)
DELETION_GRACE_DAYS = 30


class TenantPrivacySetting(Base):
    __tablename__ = "tenant_privacy_settings"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    ai_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="on", server_default="on")
    ai_redact_emails: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    ai_redact_ips: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    metrics_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    events_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
