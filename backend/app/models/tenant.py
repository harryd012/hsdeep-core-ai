"""
Tenant model. A tenant is the top-level organizational boundary that all
other tenant-scoped entities (users, roles, sites, devices) hang off of.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.role import Role
    from app.models.site import Site
    from app.models.user import User


class Tenant(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    users: Mapped[list["User"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    roles: Mapped[list["Role"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    sites: Mapped[list["Site"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    devices: Mapped[list["Device"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    settings: Mapped[list["Setting"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    security_settings: Mapped[list["SecuritySetting"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    notification_settings: Mapped[list["NotificationSetting"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Tenant id={self.id} slug={self.slug!r}>"
