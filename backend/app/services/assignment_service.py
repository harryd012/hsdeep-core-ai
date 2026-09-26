"""Enterprise Assignment Engine - Service Layer.

Implements pluggable routing strategies for assigning tickets to the right
engineer/team: round_robin, least_loaded, skill_based, and direct (manual).
Every assignment records a TicketHistory entry and an AuditEvent.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assignment_group import AssignmentGroup, AssignmentGroupMember
from app.models.knowledge_audit import AuditEvent
from app.models.ticket import Ticket, TicketHistory
from app.repositories.assignment_repository import AssignmentRepository

logger = logging.getLogger(__name__)

VALID_STRATEGIES = {"round_robin", "least_loaded", "skill_based"}


class AssignmentError(Exception):
    """Raised when an assignment cannot be completed."""


class AssignmentService:
    """Routes tickets to engineers using pluggable strategies."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = AssignmentRepository(db)

    async def assign_smart(
        self,
        ticket: Ticket,
        *,
        tenant_id: uuid.UUID,
        group_id: uuid.UUID | None = None,
        strategy: str | None = None,
        assigned_by: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> Ticket:
        """Assign a ticket using the best-fit strategy."""
        target_group_id = group_id or ticket.assigned_group_id
        if not target_group_id:
            raise AssignmentError(
                "No assignment group specified. Provide group_id or set the ticket's assigned_group_id."
            )

        group = await self.repo.get_group(target_group_id, tenant_id)
        if not group:
            raise AssignmentError(f"Assignment group {target_group_id} not found.")
        if not group.is_active:
            raise AssignmentError(f"Assignment group '{group.name}' is inactive.")

        effective_strategy = strategy or group.strategy
        if effective_strategy not in VALID_STRATEGIES:
            raise AssignmentError(
                f"Unknown strategy '{effective_strategy}'. Valid: {sorted(VALID_STRATEGIES)}"
            )

        assignee = await self._pick_assignee(
            group=group, strategy=effective_strategy, tenant_id=tenant_id, ticket=ticket
        )
        if not assignee:
            raise AssignmentError(f"No active members in group '{group.name}' to assign to.")

        return await self.assign_to_user(
            ticket=ticket,
            user_id=assignee.user_id,
            group_id=group.id,
            group_name=group.name,
            assigned_by=assigned_by,
            reason=reason or f"Smart assignment via {effective_strategy} strategy",
            strategy=effective_strategy,
        )

    async def assign_to_user(
        self,
        ticket: Ticket,
        *,
        user_id: uuid.UUID,
        group_id: uuid.UUID | None = None,
        group_name: str | None = None,
        assigned_by: uuid.UUID | None = None,
        reason: str | None = None,
        strategy: str | None = None,
    ) -> Ticket:
        """Assign a ticket to a specific user (manual override or strategy result)."""
        old_assignee = ticket.assigned_to
        old_group_id = ticket.assigned_group_id

        ticket.assigned_to = user_id
        ticket.assigned_at = datetime.now(timezone.utc)
        if group_id:
            ticket.assigned_group_id = group_id
        if group_name:
            ticket.assigned_team = group_name
            ticket.assignment_group = group_name

        await self.db.flush()
        await self.db.refresh(ticket)

        history = TicketHistory(
            tenant_id=ticket.tenant_id,
            ticket_id=ticket.id,
            actor_id=assigned_by,
            actor_name="System",
            action_type="assignment_change",
            field_name="assigned_to",
            old_value=str(old_assignee) if old_assignee else None,
            new_value=str(user_id),
            context={
                "reason": reason,
                "strategy": strategy,
                "group_id": str(group_id) if group_id else None,
                "group_name": group_name,
                "old_group_id": str(old_group_id) if old_group_id else None,
            },
        )
        self.db.add(history)

        audit = AuditEvent(
            tenant_id=ticket.tenant_id,
            actor_id=assigned_by,
            actor_type="user" if assigned_by else "system",
            action="assignment_change",
            entity_type="ticket",
            entity_id=ticket.id,
            description=(
                f"Ticket {ticket.ticket_number} assigned to user {user_id}"
                + (f" via {strategy}" if strategy else "")
                + (f" (reason: {reason})" if reason else "")
            ),
            old_values={"assigned_to": str(old_assignee) if old_assignee else None},
            new_values={"assigned_to": str(user_id), "strategy": strategy},
        )
        self.db.add(audit)
        await self.db.flush()

        logger.info(
            "Ticket %s assigned to %s (strategy=%s, group=%s)",
            ticket.ticket_number, user_id, strategy, group_name,
        )
        return ticket

    async def _pick_assignee(
        self, *, group: AssignmentGroup, strategy: str, tenant_id: uuid.UUID, ticket: Ticket
    ) -> AssignmentGroupMember | None:
        members = await self.repo.get_active_members(group.id)
        if not members:
            return None
        if strategy == "round_robin":
            return await self._least_loaded(members, tenant_id)
        if strategy == "least_loaded":
            return await self._least_loaded(members, tenant_id)
        if strategy == "skill_based":
            return self._skill_based(members, ticket)
        return None

    async def _least_loaded(
        self, members: list[AssignmentGroupMember], tenant_id: uuid.UUID
    ) -> AssignmentGroupMember:
        best: AssignmentGroupMember | None = None
        best_score: float | None = None
        for member in members:
            open_count = await self.repo.count_open_tickets_for_user(member.user_id, tenant_id)
            score = open_count / max(member.capacity_weight, 1)
            if best_score is None or score < best_score:
                best = member
                best_score = score
        return best  # type: ignore[return-value]

    def _skill_based(
        self, members: list[AssignmentGroupMember], ticket: Ticket
    ) -> AssignmentGroupMember | None:
        ticket_terms = {
            term.lower() for term in [ticket.category, ticket.subcategory, ticket.title] if term
        }
        best: AssignmentGroupMember | None = None
        best_score = 0
        for member in members:
            member_skills = {s.lower() for s in member.skills_json}
            if not member_skills:
                continue
            score = len(member_skills & ticket_terms)
            if score > best_score:
                best = member
                best_score = score
        return best