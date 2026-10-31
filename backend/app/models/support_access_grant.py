"""
Break-glass support access (R1.2).

A platform superuser may act inside another tenant (the X-Tenant-Id override)
only while holding a live grant for that tenant: a stated reason, a hard
expiry, and an audit record in the customer's own tenant. The customer's
admins can list and revoke grants on their tenant.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class SupportAccessGrant(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "support_access_grants"
    __table_args__ = (Index("ix_support_access_grants_user_tenant", "user_id", "tenant_id"),)

    #: The customer tenant being accessed.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The support (superuser) account the grant is for.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user_email: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
