"""
Repository Pattern: all direct SQLAlchemy access for AssignmentGroup and
AssignmentGroupMember lives here. The service layer talks to this class only,
following the same convention as TicketRepository / IncidentRepository.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assignment_group import AssignmentGroup, AssignmentGroupMember
from app.models.ticket import Ticket


class AssignmentGroupNotFound(Exception):
    """Raised when an assignment group does not exist."""


class AssignmentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- AssignmentGroup CRUD -------------------------------------------------

    async def create_group(
        self,
        *,
        tenant_id: uuid.UUID,
        name: str,
        description: str | None = None,
        strategy: str = "round_robin",
        is_active: bool = True,
    ) -> AssignmentGroup:
        """Create a new assignment group (tenant-scoped)."""
        group = AssignmentGroup(
            tenant_id=tenant_id,
            name=name,
            description=description,
            strategy=strategy,
            is_active=is_active,
        )
        self.db.add(group)
        await self.db.flush()
        await self.db.refresh(group)
        return group

    async def get_group(self, group_id: uuid.UUID, tenant_id: uuid.UUID) -> AssignmentGroup | None:
        """Get a group by ID (tenant-scoped)."""
        stmt = select(AssignmentGroup).where(
            AssignmentGroup.id == group_id,
            AssignmentGroup.tenant_id == tenant_id,
            AssignmentGroup.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_group_by_name(self, tenant_id: uuid.UUID, name: str) -> AssignmentGroup | None:
        """Get a group by name (tenant-scoped)."""
        stmt = select(AssignmentGroup).where(
            AssignmentGroup.tenant_id == tenant_id,
            AssignmentGroup.name == name,
            AssignmentGroup.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_groups(self, tenant_id: uuid.UUID, *, is_active: bool | None = None) -> list[AssignmentGroup]:
        """List all groups for a tenant, optionally filtered by active state."""
        stmt = select(AssignmentGroup).where(
            AssignmentGroup.tenant_id == tenant_id,
            AssignmentGroup.is_deleted.is_(False),
        )
        if is_active is not None:
            stmt = stmt.where(AssignmentGroup.is_active.is_(is_active))
        stmt = stmt.order_by(AssignmentGroup.created_at.asc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update_group(self, group: AssignmentGroup, **fields) -> AssignmentGroup:
        """Update an assignment group."""
        for key, value in fields.items():
            if hasattr(group, key):
                setattr(group, key, value)
        await self.db.flush()
        await self.db.refresh(group)
        return group

    async def soft_delete_group(self, group: AssignmentGroup) -> None:
        """Soft-delete an assignment group."""
        group.is_deleted = True
        await self.db.flush()

    # -- AssignmentGroupMember CRUD -------------------------------------------

    async def add_member(
        self,
        *,
        tenant_id: uuid.UUID,
        group_id: uuid.UUID,
        user_id: uuid.UUID,
        skills_json: list[str] | None = None,
        capacity_weight: int = 1,
        is_active: bool = True,
    ) -> AssignmentGroupMember:
        """Add a member to an assignment group."""
        member = AssignmentGroupMember(
            tenant_id=tenant_id,
            group_id=group_id,
            user_id=user_id,
            skills_json=skills_json or [],
            capacity_weight=capacity_weight,
            is_active=is_active,
        )
        self.db.add(member)
        await self.db.flush()
        await self.db.refresh(member)
        return member

    async def get_member(
        self, member_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> AssignmentGroupMember | None:
        """Get a member by ID (tenant-scoped)."""
        stmt = select(AssignmentGroupMember).where(
            AssignmentGroupMember.id == member_id,
            AssignmentGroupMember.tenant_id == tenant_id,
            AssignmentGroupMember.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_member_by_group_and_user(
        self, group_id: uuid.UUID, user_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> AssignmentGroupMember | None:
        """Get a member by group + user (tenant-scoped)."""
        stmt = select(AssignmentGroupMember).where(
            AssignmentGroupMember.group_id == group_id,
            AssignmentGroupMember.user_id == user_id,
            AssignmentGroupMember.tenant_id == tenant_id,
            AssignmentGroupMember.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_members(self, group_id: uuid.UUID, tenant_id: uuid.UUID) -> list[AssignmentGroupMember]:
        """List all members of a group (tenant-scoped)."""
        stmt = select(AssignmentGroupMember).where(
            AssignmentGroupMember.group_id == group_id,
            AssignmentGroupMember.tenant_id == tenant_id,
            AssignmentGroupMember.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_active_members(self, group_id: uuid.UUID) -> list[AssignmentGroupMember]:
        """Get all active members of a group (used for routing strategies)."""
        stmt = select(AssignmentGroupMember).where(
            AssignmentGroupMember.group_id == group_id,
            AssignmentGroupMember.is_active.is_(True),
            AssignmentGroupMember.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update_member(self, member: AssignmentGroupMember, **fields) -> AssignmentGroupMember:
        """Update an assignment group member."""
        for key, value in fields.items():
            if hasattr(member, key):
                setattr(member, key, value)
        await self.db.flush()
        await self.db.refresh(member)
        return member

    async def remove_member(self, member: AssignmentGroupMember) -> None:
        """Soft-delete a member from an assignment group."""
        member.is_deleted = True
        await self.db.flush()

    # -- Routing helpers ------------------------------------------------------

    async def count_open_tickets_for_user(self, user_id: uuid.UUID, tenant_id: uuid.UUID) -> int:
        """Count of non-terminal tickets assigned to a user (for least_loaded)."""
        stmt = select(func.count()).select_from(Ticket).where(
            Ticket.assigned_to == user_id,
            Ticket.tenant_id == tenant_id,
            Ticket.is_deleted.is_(False),
            Ticket.status.notin_(["resolved", "closed", "cancelled"]),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def count_open_tickets_for_group(self, group_id: uuid.UUID, tenant_id: uuid.UUID) -> int:
        """Count of non-terminal tickets routed to a group (for least_loaded)."""
        stmt = select(func.count()).select_from(Ticket).where(
            Ticket.assigned_group_id == group_id,
            Ticket.tenant_id == tenant_id,
            Ticket.is_deleted.is_(False),
            Ticket.status.notin_(["resolved", "closed", "cancelled"]),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one()