"""
NotificationPolicy model (observability plan P9 slice 1).

Routes incidents to notification destinations. A policy matches an incident
by minimum severity and, optionally, by priority and site; a matching policy
sends to its destinations:

  * the tenant's own email / Slack / PagerDuty channels (NotificationSetting),
  * its own Microsoft Teams webhook (stored encrypted, never returned), and
  * its own email recipients, when set (instead of the tenant's list).

A tenant without enabled policies keeps the original behaviour (every enabled
channel, at its minimum severity). Delivery, retries and "resolved only where
the opening was sent" stay in the dispatcher.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

MAX_POLICIES_PER_TENANT = 50


class NotificationPolicy(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "notification_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_notification_policies_tenant_name"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    #: Lower first; only for display (every matching policy sends).
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: critical | warning | info
    min_severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warning", server_default="warning")
    #: Empty = any. Priorities P1..P4; site ids as strings.
    priorities: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    site_ids: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    #: Of the tenant's channels: email | slack | pagerduty.
    channels: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    #: Comma-separated; when set, email from this policy goes here instead of the tenant's list.
    email_recipients: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Fernet ciphertext of the Teams incoming-webhook URL (SecretCrypto).
    teams_webhook_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Last 4 characters of the webhook URL, to show which one is stored.
    teams_webhook_hint: Mapped[str | None] = mapped_column(String(8), nullable=True)
