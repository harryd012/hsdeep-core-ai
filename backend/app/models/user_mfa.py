"""
Multi-factor authentication (enterprise security programme S2).

UserMfa: one authenticator-app (TOTP) enrolment per user. The shared secret is
encrypted at rest with the platform key (core.security.SecretCrypto) and is
never returned after set-up. ``confirmed_at`` is null while set-up is in
progress: an unconfirmed secret is not MFA and never asked for at sign-in.
``last_used_step`` is the TOTP time step of the last accepted code, so a code
cannot be used twice.

MfaRecoveryCode: one-time codes for a lost device, stored only as SHA-256
hashes (they are high-entropy random values, like refresh tokens).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class UserMfa(Base, TimestampMixin):
    __tablename__ = "user_mfa"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class MfaRecoveryCode(Base, UUIDPKMixin):
    __tablename__ = "mfa_recovery_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
