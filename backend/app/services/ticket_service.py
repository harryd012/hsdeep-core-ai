"""
Ticket Service - Core business logic for Enterprise Ticket Management Engine.

This service handles the complete ticket lifecycle:
  Alert → Incident → Ticket → Assignment → Approval → Execution → Resolution → Knowledge

Reuses existing platform services:
- ITSM ConnectorManager for external provider sync
- AI Insight Service for AI-generated content
- Alert Service for alert correlation
- Incident Service for incident linkage
- AssignmentService for smart assignment routing
- SLAService for SLA calculation and breach detection
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket import Ticket, TicketLink, TicketComment, TicketAttachment, TicketHistory
from app.models.incident import Incident
from app.models.itsm import ITSMConnection
from app.repositories.ticket_repository import TicketRepository
from app.services.itsm.connector_manager import ConnectorManager

logger = logging.getLogger(__name__)


class TicketService:
    """Service layer for Ticket operations."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.tickets = TicketRepository(db)
        self.itsm = ConnectorManager(db)

    async def create_ticket(
        self,
        tenant_id: uuid.UUID,
        title: str,
        description: str | None = None,
        incident_id: uuid.UUID | None = None,
        source: str = "manual",
        priority: str | None = None,
        category: str | None = None,
        subcategory: str | None = None,
        assigned_to: uuid.UUID | None = None,
        assigned_team: str | None = None,
        assignment_group: str | None = None,
        assigned_group_id: uuid.UUID | None = None,
        sla_due_at: datetime | None = None,
        custom_fields: dict | None = None,
        tags: list[str] | None = None,
        created_by: uuid.UUID | None = None,
    ) -> Ticket:
        """Create a new ticket."""
        from app.repositories.alert_repository import AlertRepository
        from app.repositories.incident_repository import IncidentRepository
        from app.services.incident_service import IncidentService
        incident_service = IncidentService(
            incident_repo=IncidentRepository(self.db),
            alert_repo=AlertRepository(self.db),
        )

        ticket_number = await self._generate_ticket_number(tenant_id)

        ticket = await self.tickets.create(
            tenant_id=tenant_id,
            incident_id=incident_id,
            ticket_number=ticket_number,
            title=title,
            description=description,
            source=source,
            status="open",
            priority=priority,
            category=category,
            subcategory=subcategory,
            assigned_to=assigned_to,
            assigned_team=assigned_team,
            assignment_group=assignment_group,
            assigned_group_id=assigned_group_id,
            sla_due_at=sla_due_at,
            opened_at=datetime.now(timezone.utc),
            custom_fields=custom_fields or {},
            tags=tags or [],
        )

        await self.tickets.create_history(
            tenant_id=tenant_id,
            ticket_id=ticket.id,
            action_type="status_change",
            field_name="status",
            old_value=None,
            new_value="open",
            actor_id=created_by,
            actor_name="System",
        )

        # Apply SLA policy if a priority is set
        if priority:
            try:
                from app.services.sla_service import SLAService
                sla_service = SLAService(self.db)
                await sla_service.apply_sla_to_ticket(ticket, tenant_id=tenant_id, priority=priority)
            except Exception as exc:
                logger.warning("SLA application failed for ticket %s: %s", ticket_number, exc)

        logger.info("Created ticket %s for tenant %s", ticket_number, tenant_id)
        return ticket

    async def get_ticket(self, ticket_id: uuid.UUID, tenant_id: uuid.UUID) -> Ticket | None:
        """Get a ticket by ID."""
        return await self.tickets.get(ticket_id, tenant_id)

    async def list_tickets(
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
    ) -> tuple[list[Ticket], int]:
        """List tickets with filters and pagination."""
        tickets = await self.tickets.list(
            tenant_id=tenant_id,
            status=status,
            priority=priority,
            assigned_to=assigned_to,
            category=category,
            incident_id=incident_id,
            limit=limit,
            offset=offset,
            search=search,
        )
        total = await self.tickets.count(
            tenant_id=tenant_id,
            status=status,
            priority=priority,
            assigned_to=assigned_to,
            category=category,
            incident_id=incident_id,
            search=search,
        )
        return tickets, total

    async def update_ticket(
        self,
        ticket: Ticket,
        **updates,
    ) -> Ticket:
        """Update a ticket and record history for changes.
        
        Note: Status updates are now handled by TicketStateMachine.
        Use transition() method for status changes.
        """
        old_priority = ticket.priority
        old_assigned_to = ticket.assigned_to

        # Prevent direct status updates - must use state machine
        if "status" in updates:
            raise ValueError(
                "Direct status updates are not allowed. Use the state machine's transition() method."
            )

        updated = await self.tickets.update(ticket, **updates)

        if "priority" in updates and updates["priority"] != old_priority:
            await self.tickets.create_history(
                tenant_id=ticket.tenant_id,
                ticket_id=ticket.id,
                action_type="priority_change",
                field_name="priority",
                old_value=old_priority,
                new_value=updates["priority"],
                actor_id=updates.get("updated_by"),
                actor_name="User",
            )
            # Re-evaluate SLA when priority changes
            try:
                from app.services.sla_service import SLAService
                sla_service = SLAService(self.db)
                await sla_service.apply_sla_to_ticket(updated, tenant_id=ticket.tenant_id, priority=updates["priority"])
            except Exception as exc:
                logger.warning("SLA re-evaluation failed for ticket %s: %s", ticket.ticket_number, exc)

        if "assigned_to" in updates and updates["assigned_to"] != old_assigned_to:
            await self.tickets.create_history(
                tenant_id=ticket.tenant_id,
                ticket_id=ticket.id,
                action_type="assignment_change",
                field_name="assigned_to",
                old_value=str(old_assigned_to) if old_assigned_to else None,
                new_value=str(updates["assigned_to"]) if updates["assigned_to"] else None,
                actor_id=updates.get("updated_by"),
                actor_name="User",
            )

        return updated

    async def transition_ticket(
        self,
        ticket: Ticket,
        to_state: str,
        actor_id: uuid.UUID | None = None,
        actor_name: str = "System",
        reason: str | None = None,
        context: dict | None = None,
    ) -> Ticket:
        """Transition a ticket to a new state using the state machine.
        
        All status changes must go through this method.
        """
        from app.services.ticket_state_machine import TicketStateMachine
        state_machine = TicketStateMachine(self.db, ticket)
        updated = await state_machine.transition(
            to_state=to_state,
            actor_id=actor_id,
            actor_name=actor_name,
            reason=reason,
            context=context,
        )

        # Handle SLA pause/resume on state transitions
        try:
            from app.services.sla_service import SLAService
            sla_service = SLAService(self.db)
            await sla_service.on_ticket_transition(updated, to_state)
        except Exception as exc:
            logger.warning("SLA transition handling failed for ticket %s: %s", ticket.ticket_number, exc)

        return updated

    async def assign_ticket(
        self,
        ticket: Ticket,
        assigned_to: uuid.UUID,
        assigned_by: uuid.UUID | None = None,
    ) -> Ticket:
        """Assign a ticket to a user using the state machine."""
        updated = await self.update_ticket(
            ticket,
            assigned_to=assigned_to,
            assigned_at=datetime.now(timezone.utc),
            updated_by=assigned_by,
        )
        # Transition to assigned state via state machine
        updated = await self.transition_ticket(
            updated,
            to_state="assigned",
            actor_id=assigned_by,
            actor_name="System",
            reason=f"Assigned to user {assigned_to}",
        )
        return updated

    async def smart_assign(
        self,
        ticket: Ticket,
        *,
        tenant_id: uuid.UUID,
        group_id: uuid.UUID | None = None,
        strategy: str | None = None,
        assigned_by: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> Ticket:
        """Assign a ticket using the smart assignment engine.

        Delegates to AssignmentService which picks the best assignee
        based on the group's configured strategy (round_robin, least_loaded,
        skill_based). After assignment, transitions the ticket to 'assigned'
        via the state machine.
        """
        from app.services.assignment_service import AssignmentService
        assignment_service = AssignmentService(self.db)
        updated = await assignment_service.assign_smart(
            ticket,
            tenant_id=tenant_id,
            group_id=group_id,
            strategy=strategy,
            assigned_by=assigned_by,
            reason=reason,
        )
        # Transition to assigned state via state machine
        updated = await self.transition_ticket(
            updated,
            to_state="assigned",
            actor_id=assigned_by,
            actor_name="System",
            reason=reason or "Smart assignment",
        )
        return updated

    async def resolve_ticket(
        self,
        ticket: Ticket,
        resolution_note: str | None = None,
        resolved_by: uuid.UUID | None = None,
    ) -> Ticket:
        """Resolve a ticket using the state machine."""
        updated = await self.update_ticket(
            ticket,
            resolved_at=datetime.now(timezone.utc),
            updated_by=resolved_by,
        )
        updated = await self.transition_ticket(
            updated,
            to_state="resolved",
            actor_id=resolved_by,
            actor_name="System",
            reason=resolution_note,
        )
        return updated

    async def close_ticket(
        self,
        ticket: Ticket,
        closed_by: uuid.UUID | None = None,
    ) -> Ticket:
        """Close a resolved ticket using the state machine."""
        updated = await self.update_ticket(
            ticket,
            closed_at=datetime.now(timezone.utc),
            updated_by=closed_by,
        )
        updated = await self.transition_ticket(
            updated,
            to_state="closed",
            actor_id=closed_by,
            actor_name="System",
            reason="Ticket closed",
        )
        return updated

    async def reopen_ticket(
        self,
        ticket: Ticket,
        reopened_by: uuid.UUID | None = None,
    ) -> Ticket:
        """Reopen a closed ticket using the state machine."""
        updated = await self.update_ticket(
            ticket,
            reopened_at=datetime.now(timezone.utc),
            updated_by=reopened_by,
        )
        updated = await self.transition_ticket(
            updated,
            to_state="reopened",
            actor_id=reopened_by,
            actor_name="System",
            reason="Ticket reopened",
        )
        return updated

    async def add_comment(
        self,
        ticket: Ticket,
        body: str,
        author_id: uuid.UUID | None = None,
        author_name: str | None = None,
        is_internal: bool = True,
    ) -> TicketComment:
        """Add a comment to a ticket."""
        return await self.tickets.create_comment(
            tenant_id=ticket.tenant_id,
            ticket_id=ticket.id,
            author_id=author_id,
            author_name=author_name,
            body=body,
            is_internal=is_internal,
        )

    async def link_to_provider(
        self,
        ticket: Ticket,
        connection_id: uuid.UUID,
        external_id: str,
        external_number: str | None = None,
        correlation_id: str | None = None,
    ) -> TicketLink:
        """Link a ticket to an external ITSM provider ticket."""
        existing = await self.tickets.get_link_by_connection(ticket.id, connection_id)
        if existing:
            return await self.tickets.update_link(
                existing,
                external_id=external_id,
                external_number=external_number,
                correlation_id=correlation_id,
            )
        return await self.tickets.create_link(
            tenant_id=ticket.tenant_id,
            ticket_id=ticket.id,
            connection_id=connection_id,
            external_id=external_id,
            external_number=external_number,
            correlation_id=correlation_id or str(uuid.uuid4()),
        )

    async def _generate_ticket_number(self, tenant_id: uuid.UUID) -> str:
        """Generate a unique ticket number."""
        from app.repositories.ticket_repository import TicketRepository
        import random

        for _ in range(10):
            number = f"TCK-{random.randint(100000, 999999)}"
            existing = await self.tickets.get_by_number(number, tenant_id)
            if not existing:
                return number

        raise RuntimeError("Failed to generate unique ticket number")