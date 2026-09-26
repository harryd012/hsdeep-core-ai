"""
Collector enrollment token (Phase: collector enrollment core).

A one-time, tenant-scoped trust path that lets a collector running on customer
hardware register itself without a human session.

Security posture:
  * The plaintext token is returned EXACTLY ONCE, at creation. Only its HASH is
    persisted (``token_hash``), so a database leak cannot be replayed.
  * Single use (``used_at``), expiry (``expires_at``) and revocation
    (``revoked_at``) are all enforced server-side.
  * ``tenant_id`` is never supplied by the collector at registration time - it is
    taken from the validated token, which is what makes cross-tenant registration
    impossible.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class CollectorEnrollmentToken(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "collector_enrollment_tokens"
    __table_args__ = (
        UniqueConstraint(
            "token_hash", name="uq_collector_enrollment_tokens_token_hash"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: SHA-256 style digest of the plaintext token. NEVER the token itself.
    token_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: Set only when the token has been successfully consumed.
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    tenant: Mapped["Tenant"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CollectorEnrollmentToken tenant={self.tenant_id} used={self.used_at is not None}>"
