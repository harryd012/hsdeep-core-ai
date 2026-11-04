"""
SavedDashboard model (observability plan P6 slice 2).

A named set of widgets an operator arranges from the platform's widget
registry (`schemas.dashboards.WIDGET_TYPES`). Private to its owner unless
`shared`, then listed for the whole tenant; only the owner changes it.

`owner_id` is the creating user's id with no foreign key, as for saved host
views: users are soft-deleted and service principals act with ids that are not
user rows. `widgets` holds only registry widgets validated on the way in; every
widget reads its data from an existing API, which enforces its own permission.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

MAX_DASHBOARDS_PER_OWNER = 30
MAX_WIDGETS = 24


class SavedDashboard(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "saved_dashboards"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_id", "name", name="uq_saved_dashboards_tenant_owner_name"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    widgets: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
