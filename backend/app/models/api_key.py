"""
Tenant-scoped API keys (name, masked key, created_by).
"""
from __future__ import annotations

import uuid
import secrets
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, SoftDeleteMixin, UUIDPKMixin
from app.models.tenant import Tenant

if TYPE_CHECKING:
    from app.models.user import User


class ApiKey(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "api_keys"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Permission scopes granted to this key (subset of the permission catalog).
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    last_used_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
    expires_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Plain many-to-one (no back_populates): nothing in the codebase uses a
    # Tenant.api_keys collection, and declaring one would require resolving
    # 'ApiKey' across the circular tenant<->api_key import boundary.
    tenant: Mapped["Tenant"] = relationship()

    def __repr__(self) -> str:
        return f"<ApiKey id={self.id} tenant_id={self.tenant_id} name={self.name!r}>"