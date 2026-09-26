"""
Tenant-scoped security settings (password policy, MFA, session timeout).
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin
from app.models.tenant import Tenant

if TYPE_CHECKING:
    pass


class SecuritySetting(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "security_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_security_settings_tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    min_password_length: Mapped[int] = mapped_column(nullable=False, default=8)
    require_uppercase: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    require_numbers: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    require_special_chars: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    session_timeout_minutes: Mapped[int] = mapped_column(nullable=False, default=60)
    max_login_attempts: Mapped[int] = mapped_column(nullable=False, default=5)

    tenant: Mapped["Tenant"] = relationship(back_populates="security_settings")

    def __repr__(self) -> str:
        return f"<SecuritySetting id={self.id} tenant_id={self.tenant_id} mfa_enabled={self.mfa_enabled}>"