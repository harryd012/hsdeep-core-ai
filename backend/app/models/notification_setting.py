"""
Tenant-scoped notification settings (email, Slack, PagerDuty).
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin
from app.models.tenant import Tenant

if TYPE_CHECKING:
    pass


class NotificationSetting(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "notification_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_notification_settings_tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_recipients: Mapped[str | None] = mapped_column(Text, nullable=True)
    slack_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    slack_webhook_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    slack_channel: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pagerduty_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pagerduty_integration_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pagerduty_severity: Mapped[str | None] = mapped_column(String(50), nullable=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="notification_settings")

    def __repr__(self) -> str:
        return f"<NotificationSetting id={self.id} tenant_id={self.tenant_id} email_enabled={self.email_enabled}>"