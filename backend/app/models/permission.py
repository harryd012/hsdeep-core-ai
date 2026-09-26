"""
Permission model. Permissions form a fixed, system-wide catalog (e.g.
"users.create", "devices.read") that roles are granted via the
role_permissions association table. Permissions are not tenant-scoped.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.role import Role


class Permission(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    roles: Mapped[list["Role"]] = relationship(
        secondary="role_permissions", back_populates="permissions"
    )

    def __repr__(self) -> str:
        return f"<Permission id={self.id} code={self.code!r}>"
