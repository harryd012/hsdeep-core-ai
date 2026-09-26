"""
User model, tenant-scoped. Holds identity/profile plus the authentication
columns added by the Auth + RBAC sprint (P1).

Auth columns are additive and nullable-safe: `password_hash` is nullable so
pre-P1 rows (created before local passwords existed, or users that will only
ever authenticate via a future SSO/IdP) remain valid - a null hash simply
means "cannot log in with a local password" and AuthService rejects such a
login rather than crashing. Password hashing/token logic itself lives in
`app/core/auth.py`, never on the model (the model stays persistence-only).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin
from app.models.role import user_roles

if TYPE_CHECKING:
    from app.models.refresh_token import RefreshToken
    from app.models.role import Role
    from app.models.tenant import Tenant


class User(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_id_email"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    # -- Auth + RBAC sprint (P1) additions ---------------------------------
    # Argon2id hash produced by app.core.auth.hash_password. Nullable: a null
    # hash means "no local password set" (login with a password is rejected).
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Cross-tenant operator (MSP model, interim). A superuser may target any
    # tenant via the X-Tenant-Id header; a normal user is pinned to
    # `tenant_id`. See app/core/auth.py::resolve_principal.
    is_superuser: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="users")
    roles: Mapped[list["Role"]] = relationship(secondary=user_roles, back_populates="users")
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} tenant_id={self.tenant_id} email={self.email!r}>"
