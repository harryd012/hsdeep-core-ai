"""
HostSavedView model (observability plan P2 slice 2).

A named Hosts-explorer filter set: the facet / search filters and the table
layout an operator wants to come back to ("Production Cisco, critical").
Private to its owner unless `shared`, then listed for the whole tenant.

`owner_id` is the creating user's id with no foreign key: users are
soft-deleted (the row stays), and service principals act with ids that are not
user rows. Only the owner can change or delete a view.

`filters` holds only the explorer's own filter keys, validated on the way in
(`schemas.host_inventory.HostViewFilters`); the API never stores free-form JSON.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

HOST_VIEW_LAYOUTS = ("health", "inventory")
MAX_VIEWS_PER_OWNER = 50


class HostSavedView(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "host_saved_views"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_id", "name", name="uq_host_saved_views_tenant_owner_name"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    filters: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    layout: Mapped[str] = mapped_column(String(20), nullable=False, default="health", server_default="health")
    shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
