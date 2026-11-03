"""
AuthSession model (enterprise security programme S1).

What a person sees on "Your sessions": where and when each sign-in session
started and when it was last used. One row per sign-in session
(`refresh_tokens.session_id`).

Liveness is NOT decided here: a session is live while it has an unrevoked,
unexpired refresh token (RefreshTokenRepository.session_is_live). This table
only adds the device and activity details, so a missing row never locks
anyone out and never keeps a session alive.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    #: Same value as refresh_tokens.session_id and the access token's "sid".
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    #: Sign-in or the last refresh (about every access-token lifetime while in use).
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
