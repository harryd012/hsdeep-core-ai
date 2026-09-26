"""
Enterprise Ticket State Machine.

Validates all ticket status transitions according to enterprise workflow rules.
No direct status updates are allowed - all transitions must go through this service.

State Machine Rules:
- open → assigned → in_progress → pending_approval → resolved → closed
- reopened ← closed/resolved/cancelled
- cancelled can only be reached from open/assigned/in_progress/pending_approval
- Illegal transitions fail with clear error messages
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

from app.models.knowledge_audit import AuditEvent
from app.models.ticket import Ticket, TicketHistory

logger = logging.getLogger(__name__)

# Valid states in the ticket lifecycle
VALID_STATUSES = {
    "open",
    "assigned",
    "in_progress",
    "pending_approval",
    "resolved",
    "closed",
    "cancelled",
    "reopened",
}

# State transition rules: from_state → set of allowed to_states
TRANSITION_RULES: dict[str, set[str]] = {
    "open": {"assigned", "in_progress", "cancelled"},
    "assigned": {"in_progress", "pending_approval", "cancelled", "open"},
    "in_progress": {"pending_approval", "resolved", "cancelled"},
    "pending_approval": {"in_progress", "resolved", "cancelled"},
    "resolved": {"closed", "reopened"},
    "closed": {"reopened"},
    "cancelled": set(),  # Terminal state - no transitions allowed
    "reopened": {"open"},  # Reopened always goes to open
}

# Transitions that require specific fields to be set
REQUIRED_FIELDS: dict[str, set[str]] = {
    "resolved": {"resolved_at"},
    "closed": {"closed_at"},
    "reopened": {"reopened_at"},
    "assigned": {"assigned_at"},
    "in_progress": {"in_progress_at"},
    "pending_approval": {"pending_approval_at"},
    "cancelled": set(),
}

# Transitions that should NOT update certain timestamp fields
# (e.g., reopening should not reset opened_at)
PRESERVE_TIMESTAMPS: dict[str, set[str]] = {
    "reopened": {"opened_at"},
}


class InvalidStateTransitionError(Exception):
    """Raised when an illegal state transition is attempted."""

    def __init__(self, from_state: str, to_state: str, ticket_id: str):
        self.from_state = from_state
        self.to_state = to_state
        self.ticket_id = ticket_id
        allowed = TRANSITION_RULES.get(from_state, set())
        message = (
            f"Illegal state transition for ticket {ticket_id}: "
            f"{from_state} → {to_state}. Allowed transitions from {from_state}: {allowed or 'none (terminal state)'}"
        )
        super().__init__(message)


class TicketStateMachine:
    """Validates and executes ticket state transitions.
    
    All transitions are validated against business rules before execution.
    Creates TicketHistory and AuditEvent for every transition.
    """

    def __init__(self, db, ticket: Ticket):
        self.db = db
        self.ticket = ticket

    def validate_transition(self, to_state: str) -> Literal[True]:
        """Validate that the transition from current state to to_state is allowed.
        
        Args:
            to_state: The target state
            
        Returns:
            True if transition is valid
            
        Raises:
            InvalidStateTransitionError: If transition is not allowed
            ValueError: If to_state is not a valid status
        """
        from_state = self.ticket.status
        
        # Validate target state is valid
        if to_state not in VALID_STATUSES:
            raise ValueError(
                f"Invalid target state '{to_state}'. Valid states: {VALID_STATUSES}"
            )
        
        # No-op transition (same state)
        if from_state == to_state:
            return True
        
        # Check if transition is allowed
        allowed_transitions = TRANSITION_RULES.get(from_state, set())
        if to_state not in allowed_transitions:
            raise InvalidStateTransitionError(
                from_state=from_state,
                to_state=to_state,
                ticket_id=str(self.ticket.id),
            )
        
        return True

    async def transition(
        self,
        to_state: str,
        actor_id: uuid.UUID | None = None,
        actor_name: str = "System",
        reason: str | None = None,
        context: dict | None = None,
    ) -> Ticket:
        """Execute a state transition.
        
        Args:
            to_state: Target state
            actor_id: User who initiated the transition (None for system)
            actor_name: Display name of the actor
            reason: Optional reason for the transition
            context: Additional context data
            
        Returns:
            Updated ticket
            
        Raises:
            InvalidStateTransitionError: If transition is not allowed
        """
        from_state = self.ticket.status
        
        # Validate transition
        self.validate_transition(to_state)
        
        # No-op
        if from_state == to_state:
            return self.ticket
        
        # Prepare updates
        updates: dict = {"status": to_state}
        now = datetime.now(timezone.utc)
        
        # Set required timestamps
        required = REQUIRED_FIELDS.get(to_state, set())
        for field in required:
            if field not in updates:
                updates[field] = now
        
        # Preserve certain timestamps if needed
        preserve = PRESERVE_TIMESTAMPS.get(to_state, set())
        for field in preserve:
            if field in updates:
                del updates[field]
        
        # Handle reopened state - reset certain timestamps
        if to_state == "reopened":
            updates["reopened_at"] = now
            # Reset resolution/closure timestamps
            updates["resolved_at"] = None
            updates["closed_at"] = None
        
        # Update ticket
        for key, value in updates.items():
            if hasattr(self.ticket, key):
                setattr(self.ticket, key, value)
        
        await self.db.flush()
        await self.db.refresh(self.ticket)
        
        # Record history
        await self._record_history(
            from_state=from_state,
            to_state=to_state,
            actor_id=actor_id,
            actor_name=actor_name,
            reason=reason,
            context=context,
        )
        
        # Record audit event
        await self._record_audit(
            from_state=from_state,
            to_state=to_state,
            actor_id=actor_id,
            actor_name=actor_name,
            reason=reason,
        )
        
        logger.info(
            "Ticket %s transitioned: %s → %s by %s",
            self.ticket.ticket_number,
            from_state,
            to_state,
            actor_name,
        )
        
        return self.ticket

    async def _record_history(
        self,
        from_state: str,
        to_state: str,
        actor_id: uuid.UUID | None,
        actor_name: str,
        reason: str | None,
        context: dict | None,
    ) -> None:
        """Record state transition in TicketHistory."""
        history = TicketHistory(
            tenant_id=self.ticket.tenant_id,
            ticket_id=self.ticket.id,
            actor_id=actor_id,
            actor_name=actor_name,
            action_type="status_change",
            field_name="status",
            old_value=from_state,
            new_value=to_state,
            context={
                "reason": reason,
                "transition": f"{from_state} → {to_state}",
                **(context or {}),
            },
        )
        self.db.add(history)
        await self.db.flush()

    async def _record_audit(
        self,
        from_state: str,
        to_state: str,
        actor_id: uuid.UUID | None,
        actor_name: str,
        reason: str | None,
    ) -> None:
        """Record state transition in AuditEvent."""
        event = AuditEvent(
            tenant_id=self.ticket.tenant_id,
            actor_id=actor_id,
            actor_type="user" if actor_id else "system",
            action="status_change",
            entity_type="ticket",
            entity_id=self.ticket.id,
            description=(
                f"Ticket {self.ticket.ticket_number} status changed from "
                f"{from_state} to {to_state}" + (f" (reason: {reason})" if reason else "")
            ),
            old_values={"status": from_state},
            new_values={"status": to_state},
        )
        self.db.add(event)
        await self.db.flush()

    def get_available_transitions(self) -> list[str]:
        """Get list of valid next states from current state.
        
        Returns:
            Sorted list of allowed target states
        """
        current = self.ticket.status
        allowed = TRANSITION_RULES.get(current, set())
        return sorted(allowed)

    @staticmethod
    def is_valid_status(status: str) -> bool:
        """Check if a status string is valid."""
        return status in VALID_STATUSES

    @staticmethod
    def get_all_statuses() -> list[str]:
        """Get all valid status values."""
        return sorted(VALID_STATUSES)