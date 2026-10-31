"""
RefreshToken model (Auth + RBAC sprint, P1).

Refresh tokens are opaque random strings issued alongside a short-lived
access JWT. Only the SHA-256 hash of the token is persisted here - the
plaintext is returned to the client exactly once and never stored, so a
database compromise cannot yield usable refresh tokens (same "store only a
hash" rationale as password_hash, but a fast hash suffices because the token
is high-entropy and not user-chosen).

Server-side revocability is the whole point of persisting these: an access
JWT is stateless and valid until it expires, but a refresh token can be
revoked (logout, rotation, or admin action) by setting `revoked_at`, which
immediately stops it minting new access tokens. `tenant_id` is denormalized
here so a token can be revoked/queried without loading the parent User.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.user import User


class RefreshToken(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SHA-256 hex digest of the opaque token; unique so a presented token maps
    # to at most one row. The plaintext is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Set on logout / rotation / admin revocation. A non-null value makes the
    # token unusable regardless of expires_at.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # One sign-in session: every rotation of a refresh token keeps the same
    # session_id, and access tokens carry it as the "sid" claim. A session is
    # live while it has an unrevoked, unexpired row (see
    # RefreshTokenRepository.session_is_live). Null only on rows written
    # before sessions existed.
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")

    def __repr__(self) -> str:
        return f"<RefreshToken id={self.id} user_id={self.user_id} revoked={self.revoked_at is not None}>"
