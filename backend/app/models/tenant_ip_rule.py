"""
TenantIpRule (enterprise security programme S3): one allowed network for a
tenant's users and API keys. A tenant with no rules is open to every network;
with at least one rule, sign-in, refresh and every API call must come from an
address inside one of them. ``cidr`` is stored normalized ("10.0.0.0/8",
"203.0.113.7/32", "2001:db8::/32").
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin

MAX_IP_RULES_PER_TENANT = 100


class TenantIpRule(Base, UUIDPKMixin):
    __tablename__ = "tenant_ip_rules"
    __table_args__ = (UniqueConstraint("tenant_id", "cidr", name="uq_tenant_ip_rules_tenant_cidr"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cidr: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: No foreign key: users are soft-deleted and the audit log names the creator.
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
