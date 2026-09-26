"""
Repository Pattern: all direct SQLAlchemy access for Ticket lives here.
The service layer (TicketService) talks to this class only, following
the same convention as IncidentRepository.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket import Ticket, TicketLink, TicketComment, TicketAttachment, TicketHistory


class TicketRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **fields) -> Ticket:
        """Create a new ticket."""
        ticket = Ticket(**fields)
        self.db.add(ticket)
        await self.db.flush()
        await self.db.refresh(ticket)
        return ticket

    async def get(self, ticket_id: uuid.UUID, tenant_id: uuid.UUID) -> Ticket | None:
        """Get a ticket by ID (tenant-scoped)."""
        stmt = select(Ticket).where(
            Ticket.id == ticket_id,
            Ticket.tenant_id == tenant_id,
            Ticket.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_number(self, ticket_number: str, tenant_id: uuid.UUID) -> Ticket | None:
        """Get a ticket by ticket number (tenant-scoped)."""
        stmt = select(Ticket).where(
            Ticket.ticket_number == ticket_number,
            Ticket.tenant_id == tenant_id,
            Ticket.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        status: str | None = None,
        priority: str | None = None,
        assigned_to: uuid.UUID | None = None,
        category: str | None = None,
        incident_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
    ) -> list[Ticket]:
        """List tickets with filters."""
        stmt = select(Ticket).where(
            Ticket.tenant_id == tenant_id,
            Ticket.is_deleted.is_(False),
        )

        if status is not None:
            stmt = stmt.where(Ticket.status == status)
        if priority is not None:
            stmt = stmt.where(Ticket.priority == priority)
        if assigned_to is not None:
            stmt = stmt.where(Ticket.assigned_to == assigned_to)
        if category is not None:
            stmt = stmt.where(Ticket.category == category)
        if incident_id is not None:
            stmt = stmt.where(Ticket.incident_id == incident_id)
        if search is not None:
            stmt = stmt.where(
                or_(
                    Ticket.title.ilike(f"%{search}%"),
                    Ticket.ticket_number.ilike(f"%{search}%"),
                )
            )

        stmt = stmt.order_by(Ticket.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        tenant_id: uuid.UUID,
        *,
        status: str | None = None,
        priority: str | None = None,
        assigned_to: uuid.UUID | None = None,
        category: str | None = None,
        incident_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> int:
        """Count tickets matching filters."""
        stmt = select(func.count()).select_from(Ticket).where(
            Ticket.tenant_id == tenant_id,
            Ticket.is_deleted.is_(False),
        )

        if status is not None:
            stmt = stmt.where(Ticket.status == status)
        if priority is not None:
            stmt = stmt.where(Ticket.priority == priority)
        if assigned_to is not None:
            stmt = stmt.where(Ticket.assigned_to == assigned_to)
        if category is not None:
            stmt = stmt.where(Ticket.category == category)
        if incident_id is not None:
            stmt = stmt.where(Ticket.incident_id == incident_id)
        if search is not None:
            stmt = stmt.where(
                or_(
                    Ticket.title.ilike(f"%{search}%"),
                    Ticket.ticket_number.ilike(f"%{search}%"),
                )
            )

        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def update(self, ticket: Ticket, **fields) -> Ticket:
        """Update a ticket."""
        for key, value in fields.items():
            if hasattr(ticket, key):
                setattr(ticket, key, value)
        await self.db.flush()
        await self.db.refresh(ticket)
        return ticket

    async def soft_delete(self, ticket: Ticket) -> None:
        """Soft-delete a ticket."""
        ticket.is_deleted = True
        await self.db.flush()

    async def get_links(self, ticket_id: uuid.UUID) -> list[TicketLink]:
        """Get all ITSM links for a ticket."""
        stmt = select(TicketLink).where(TicketLink.ticket_id == ticket_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_link_by_connection(
        self, ticket_id: uuid.UUID, connection_id: uuid.UUID
    ) -> TicketLink | None:
        """Get a specific ticket link by connection ID."""
        stmt = select(TicketLink).where(
            TicketLink.ticket_id == ticket_id,
            TicketLink.connection_id == connection_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def create_link(self, **fields) -> TicketLink:
        """Create a new ticket link."""
        link = TicketLink(**fields)
        self.db.add(link)
        await self.db.flush()
        await self.db.refresh(link)
        return link

    async def update_link(self, link: TicketLink, **fields) -> TicketLink:
        """Update a ticket link."""
        for key, value in fields.items():
            if hasattr(link, key):
                setattr(link, key, value)
        await self.db.flush()
        await self.db.refresh(link)
        return link

    async def get_comments(self, ticket_id: uuid.UUID) -> list[TicketComment]:
        """Get all comments for a ticket."""
        stmt = select(TicketComment).where(
            TicketComment.ticket_id == ticket_id
        ).order_by(TicketComment.created_at.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create_comment(self, **fields) -> TicketComment:
        """Create a new ticket comment."""
        comment = TicketComment(**fields)
        self.db.add(comment)
        await self.db.flush()
        await self.db.refresh(comment)
        return comment

    async def get_attachments(self, ticket_id: uuid.UUID) -> list[TicketAttachment]:
        """Get all attachments for a ticket."""
        stmt = select(TicketAttachment).where(
            TicketAttachment.ticket_id == ticket_id
        ).order_by(TicketAttachment.created_at.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create_attachment(self, **fields) -> TicketAttachment:
        """Create a new ticket attachment."""
        attachment = TicketAttachment(**fields)
        self.db.add(attachment)
        await self.db.flush()
        await self.db.refresh(attachment)
        return attachment

    async def get_history(self, ticket_id: uuid.UUID) -> list[TicketHistory]:
        """Get all history entries for a ticket."""
        stmt = select(TicketHistory).where(
            TicketHistory.ticket_id == ticket_id
        ).order_by(TicketHistory.created_at.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create_history(self, **fields) -> TicketHistory:
        """Create a new ticket history entry."""
        history = TicketHistory(**fields)
        self.db.add(history)
        await self.db.flush()
        await self.db.refresh(history)
        return history

    async def list_prioritized(
        self,
        tenant_id: uuid.UUID,
        *,
        status: str | None = "open",
        priority: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Ticket]:
        """Tickets ordered by priority (P1 first) then most recent activity."""
        priority_rank = case(
            (Ticket.priority == "P1", 0),
            (Ticket.priority == "P2", 1),
            (Ticket.priority == "P3", 2),
            (Ticket.priority == "P4", 3),
            else_=4,
        )
        stmt = select(Ticket).where(
            Ticket.tenant_id == tenant_id,
            Ticket.is_deleted.is_(False),
        )
        if status is not None:
            stmt = stmt.where(Ticket.status == status)
        if priority is not None:
            stmt = stmt.where(Ticket.priority == priority)
        stmt = stmt.order_by(priority_rank, Ticket.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def priority_distribution(
        self, tenant_id: uuid.UUID, *, status: str | None = "open"
    ) -> dict[str, int]:
        """Counts of open tickets per priority level."""
        stmt = (
            select(Ticket.priority, func.count())
            .where(Ticket.tenant_id == tenant_id, Ticket.is_deleted.is_(False))
            .group_by(Ticket.priority)
        )
        if status is not None:
            stmt = stmt.where(Ticket.status == status)
        result = await self.db.execute(stmt)
        distribution = {"P1": 0, "P2": 0, "P3": 0, "P4": 0, "unscored": 0}
        for priority, count in result.all():
            key = priority or "unscored"
            distribution[key] = distribution.get(key, 0) + count
        return distribution

    async def get_sla_breached(self, tenant_id: uuid.UUID) -> list[Ticket]:
        """Get all SLA-breached tickets for a tenant."""
        stmt = select(Ticket).where(
            Ticket.tenant_id == tenant_id,
            Ticket.is_deleted.is_(False),
            Ticket.sla_breached.is_(True),
            Ticket.status.notin_(["resolved", "closed", "cancelled"]),
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_overdue(self, tenant_id: uuid.UUID) -> list[Ticket]:
        """Get all overdue tickets (past due date and not resolved/closed)."""
        stmt = select(Ticket).where(
            Ticket.tenant_id == tenant_id,
            Ticket.is_deleted.is_(False),
            Ticket.sla_due_at < datetime.now(timezone.utc),
            Ticket.status.notin_(["resolved", "closed", "cancelled"]),
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_escalated(self, tenant_id: uuid.UUID) -> list[Ticket]:
        """Get all escalated tickets."""
        stmt = select(Ticket).where(
            Ticket.tenant_id == tenant_id,
            Ticket.is_deleted.is_(False),
            Ticket.escalation_level > 0,
            Ticket.status.notin_(["resolved", "closed", "cancelled"]),
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())