"""
Enterprise Assignment Engine models.

AssignmentGroup and AssignmentGroupMember power the automatic routing of
Tickets to the right engineering team/individual. A group owns a routing
strategy (round_robin | least_loaded | skill_based) and a set of active
members with optional skill tags and load weights for least-loaded/skill-based
routing.

Tenant-scoped like every other model in this codebase - a tenant's assignment
groups are never visible to another tenant.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant
    from app.models.user import User


class AssignmentGroup(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    """A group of engineers that tickets can be assigned to.

    Strategy controls how tickets are routed to members:
      - round_robin:   rotate through active members in order
      - least_loaded:  assign to the member with the fewest open tickets
      - skill_based:   match tickets to members by skill tags
    """

    __tablename__ = "assignment_groups"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_assignment_groups_tenant_id_name"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # round_robin | least_loaded | skill_based - free-form string, not an
    # enum, same convention as Incident.status / Ticket.status.
    strategy: Mapped[str] = mapped_column(
        String(30), nullable=False, default="round_robin", server_default="round_robin", index=True
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )

    members: Mapped[list["AssignmentGroupMember"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )

    tenant: Mapped["Tenant"] = relationship()

    def __repr__(self) -> str:
        return f"<AssignmentGroup id={self.id} name={self.name!r} strategy={self.strategy!r}>"


class AssignmentGroupMember(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    """A member of an AssignmentGroup.

    A user can belong to multiple groups. `skills_json` is a JSONB list of
    skill tags (e.g. ["network", "cisco", "linux"]) used by the skill_based
    routing strategy. `capacity_weight` is an integer multiplier for the
    least_loaded strategy - a member with weight 2 is treated as having twice
    the capacity of a weight-1 member, so tickets are routed proportionally.
    """

    __tablename__ = "assignment_group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_assignment_group_members_group_id_user_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assignment_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # List of skill tags (JSONB, same convention as SOP.tags).
    skills_json: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    # Higher = more capacity when routing with the least_loaded strategy.
    capacity_weight: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )

    group: Mapped["AssignmentGroup"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship()

    def __repr__(self) -> str:
        return f"<AssignmentGroupMember id={self.id} group_id={self.group_id} user_id={self.user_id}>"
