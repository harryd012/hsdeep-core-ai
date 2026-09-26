"""
Tenant-scoped general settings (platform name, timezone, branding).
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


class Setting(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "settings"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_settings_tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform_name: Mapped[str] = mapped_column(String(255), nullable=False, default="HSDEEP Core AI")
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="UTC")
    branding_logo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    primary_color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    locale: Mapped[str] = mapped_column(String(10), nullable=False, default="en")

    tenant: Mapped["Tenant"] = relationship(back_populates="settings")

    def __repr__(self) -> str:
        return f"<Setting id={self.id} tenant_id={self.tenant_id} platform_name={self.platform_name!r}>"