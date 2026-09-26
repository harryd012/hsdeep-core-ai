"""Enterprise SLA Engine - Service Layer.

Handles SLA policy matching, due-date calculation (with optional business
hours/holiday support), pause/resume on waiting states, breach detection,
and warning generation. Every SLA event records a TicketHistory entry and
an AuditEvent.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_audit import AuditEvent
from app.models.sla import BusinessHours, HolidayCalendar, SLAPolicy
from app.models.ticket import Ticket, TicketHistory
from app.repositories.sla_repository import SLARepository

logger = logging.getLogger(__name__)

# States that pause the SLA clock
SLA_PAUSE_STATES = {"pending_approval"}

# Day-of-week to column mapping (0=Monday)
_DAY_COLUMNS = [
    ("monday_start", "monday_end"),
    ("tuesday_start", "tuesday_end"),
    ("wednesday_start", "wednesday_end"),
    ("thursday_start", "thursday_end"),
    ("friday_start", "friday_end"),
    ("saturday_start", "saturday_end"),
    ("sunday_start", "sunday_end"),
]


class SLAService:
    """SLA calculation, countdown, breach detection, and pause/resume."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = SLARepository(db)

    async def apply_sla_to_ticket(
        self,
        ticket: Ticket,
        *,
        tenant_id: uuid.UUID,
        priority: str,
        business_service_id: uuid.UUID | None = None,
    ) -> Ticket | None:
        """Find the matching SLA policy and set due dates on the ticket."""
        policy = await self.repo.get_policy_for_priority(tenant_id, priority, business_service_id)
        if not policy:
            logger.debug("No SLA policy found for priority=%s tenant=%s", priority, tenant_id)
            return None

        now = datetime.now(timezone.utc)
        ticket.sla_policy_id = policy.id

        # Calculate response and resolution due dates
        if policy.use_business_hours and policy.business_hours_id:
            bh = await self.repo.get_business_hours(policy.business_hours_id, tenant_id)
            if bh:
                ticket.sla_response_due_at = await self._add_business_minutes(
                    now, policy.response_time_minutes, bh, tenant_id
                )
                ticket.sla_resolution_due_at = await self._add_business_minutes(
                    now, policy.resolution_time_minutes, bh, tenant_id
                )
            else:
                ticket.sla_response_due_at = now + timedelta(minutes=policy.response_time_minutes)
                ticket.sla_resolution_due_at = now + timedelta(minutes=policy.resolution_time_minutes)
        else:
            ticket.sla_response_due_at = now + timedelta(minutes=policy.response_time_minutes)
            ticket.sla_resolution_due_at = now + timedelta(minutes=policy.resolution_time_minutes)

        ticket.sla_breached = False
        ticket.sla_warning_sent = False
        ticket.sla_paused_at = None
        ticket.sla_paused_time_seconds = 0

        await self.db.flush()

        # Record history
        history = TicketHistory(
            tenant_id=ticket.tenant_id,
            ticket_id=ticket.id,
            actor_id=None,
            actor_name="SLA Engine",
            action_type="sla_applied",
            field_name="sla_policy_id",
            old_value=None,
            new_value=str(policy.id),
            context={
                "policy_name": policy.name,
                "priority": priority,
                "response_time_minutes": policy.response_time_minutes,
                "resolution_time_minutes": policy.resolution_time_minutes,
                "response_due_at": ticket.sla_response_due_at.isoformat() if ticket.sla_response_due_at else None,
                "resolution_due_at": ticket.sla_resolution_due_at.isoformat() if ticket.sla_resolution_due_at else None,
            },
        )
        self.db.add(history)
        await self.db.flush()

        logger.info(
            "SLA policy '%s' applied to ticket %s (response_due=%s, resolution_due=%s)",
            policy.name, ticket.ticket_number,
            ticket.sla_response_due_at, ticket.sla_resolution_due_at,
        )
        return ticket

    async def on_ticket_transition(self, ticket: Ticket, new_state: str) -> None:
        """Handle SLA pause/resume when a ticket transitions states."""
        if new_state in SLA_PAUSE_STATES:
            await self._pause_sla(ticket)
        else:
            await self._resume_sla(ticket)

        # Check for breach after any transition
        await self._check_breach(ticket)

    async def _pause_sla(self, ticket: Ticket) -> None:
        """Pause the SLA clock for a ticket."""
        if ticket.sla_paused_at:
            return  # Already paused
        if not ticket.sla_resolution_due_at:
            return  # No SLA applied

        ticket.sla_paused_at = datetime.now(timezone.utc)
        await self.db.flush()

        history = TicketHistory(
            tenant_id=ticket.tenant_id,
            ticket_id=ticket.id,
            actor_name="SLA Engine",
            action_type="sla_paused",
            field_name="sla_paused_at",
            old_value=None,
            new_value=ticket.sla_paused_at.isoformat(),
            context={"state": ticket.status},
        )
        self.db.add(history)
        await self.db.flush()
        logger.info("SLA paused for ticket %s at state=%s", ticket.ticket_number, ticket.status)

    async def _resume_sla(self, ticket: Ticket) -> None:
        """Resume the SLA clock and extend due dates by the paused duration."""
        if not ticket.sla_paused_at:
            return  # Not paused

        now = datetime.now(timezone.utc)
        paused_duration = now - ticket.sla_paused_at
        ticket.sla_paused_time_seconds = (ticket.sla_paused_time_seconds or 0) + int(paused_duration.total_seconds())

        # Extend due dates by the paused duration
        if ticket.sla_response_due_at:
            ticket.sla_response_due_at = ticket.sla_response_due_at + paused_duration
        if ticket.sla_resolution_due_at:
            ticket.sla_resolution_due_at = ticket.sla_resolution_due_at + paused_duration

        ticket.sla_paused_at = None
        await self.db.flush()

        history = TicketHistory(
            tenant_id=ticket.tenant_id,
            ticket_id=ticket.id,
            actor_name="SLA Engine",
            action_type="sla_resumed",
            field_name="sla_paused_at",
            old_value=paused_duration.total_seconds().__str__(),
            new_value=None,
            context={
                "paused_seconds": int(paused_duration.total_seconds()),
                "total_paused_seconds": ticket.sla_paused_time_seconds,
            },
        )
        self.db.add(history)
        await self.db.flush()
        logger.info("SLA resumed for ticket %s (paused for %ss)", ticket.ticket_number, int(paused_duration.total_seconds()))

    async def _check_breach(self, ticket: Ticket) -> None:
        """Check if the ticket's SLA has been breached and record it."""
        if ticket.sla_breached:
            return  # Already breached
        if not ticket.sla_resolution_due_at:
            return  # No SLA applied

        now = datetime.now(timezone.utc)
        if now > ticket.sla_resolution_due_at:
            ticket.sla_breached = True
            ticket.sla_breached_at = now
            await self.db.flush()

            # Record history
            history = TicketHistory(
                tenant_id=ticket.tenant_id,
                ticket_id=ticket.id,
                actor_name="SLA Engine",
                action_type="sla_breached",
                field_name="sla_breached",
                old_value="false",
                new_value="true",
                context={
                    "resolution_due_at": ticket.sla_resolution_due_at.isoformat(),
                    "breached_at": now.isoformat(),
                },
            )
            self.db.add(history)

            # Record audit
            audit = AuditEvent(
                tenant_id=ticket.tenant_id,
                actor_type="system",
                action="sla_breached",
                entity_type="ticket",
                entity_id=ticket.id,
                description=f"SLA breached for ticket {ticket.ticket_number} (due: {ticket.sla_resolution_due_at})",
                old_values={"sla_breached": "false"},
                new_values={"sla_breached": "true", "breached_at": now.isoformat()},
            )
            self.db.add(audit)
            await self.db.flush()
            logger.warning("SLA breached for ticket %s (due=%s)", ticket.ticket_number, ticket.sla_resolution_due_at)

    async def check_breaches_for_tenant(self, tenant_id: uuid.UUID) -> int:
        """Check all non-breached, non-paused tickets for SLA breaches.

        Called by the SLA scheduler job. Returns the number of newly
        breached tickets.
        """
        from app.models.ticket import Ticket
        stmt = (
            select(Ticket)
            .where(
                Ticket.tenant_id == tenant_id,
                Ticket.is_deleted.is_(False),
                Ticket.sla_breached.is_(False),
                Ticket.sla_paused_at.is_(None),
                Ticket.sla_resolution_due_at.is_not(None),
                Ticket.status.notin_(["resolved", "closed", "cancelled"]),
            )
        )
        tickets = list((await self.db.execute(stmt)).scalars().all())
        breach_count = 0
        for ticket in tickets:
            await self._check_breach(ticket)
            if ticket.sla_breached:
                breach_count += 1
        if breach_count:
            await self.db.flush()
        return breach_count

    async def get_sla_status(self, ticket: Ticket) -> dict:
        """Get the current SLA status for a ticket (for API display)."""
        now = datetime.now(timezone.utc)
        is_paused = ticket.sla_paused_at is not None

        response_remaining = None
        resolution_remaining = None
        response_percent = None
        resolution_percent = None

        if ticket.sla_response_due_at and not is_paused:
            response_remaining = int((ticket.sla_response_due_at - now).total_seconds())
            response_percent = min(100, max(0, int((1 - response_remaining / max(1, (ticket.sla_response_due_at - ticket.opened_at).total_seconds())) * 100)))

        if ticket.sla_resolution_due_at and not is_paused:
            resolution_remaining = int((ticket.sla_resolution_due_at - now).total_seconds())
            resolution_percent = min(100, max(0, int((1 - resolution_remaining / max(1, (ticket.sla_resolution_due_at - ticket.opened_at).total_seconds())) * 100)))

        return {
            "policy_id": str(ticket.sla_policy_id) if ticket.sla_policy_id else None,
            "response_due_at": ticket.sla_response_due_at.isoformat() if ticket.sla_response_due_at else None,
            "resolution_due_at": ticket.sla_resolution_due_at.isoformat() if ticket.sla_resolution_due_at else None,
            "is_breached": ticket.sla_breached,
            "breached_at": ticket.sla_breached_at.isoformat() if ticket.sla_breached_at else None,
            "is_paused": is_paused,
            "paused_at": ticket.sla_paused_at.isoformat() if ticket.sla_paused_at else None,
            "paused_time_seconds": ticket.sla_paused_time_seconds or 0,
            "warning_sent": ticket.sla_warning_sent,
            "response_remaining_seconds": response_remaining,
            "resolution_remaining_seconds": resolution_remaining,
            "response_percent_elapsed": response_percent,
            "resolution_percent_elapsed": resolution_percent,
        }

    # ------------------------------------------------------------------
    # Business hours calculation
    # ------------------------------------------------------------------

    async def _add_business_minutes(
        self,
        start: datetime,
        minutes: int,
        business_hours: BusinessHours,
        tenant_id: uuid.UUID,
    ) -> datetime:
        """Add `minutes` working minutes to `start`, skipping non-working
        days and holidays. This is a simplified implementation that
        iterates minute-by-minute for correctness; for large SLA values
        a day-level optimization can be added later.
        """
        # Get holidays for the next 365 days
        end_estimate = start + timedelta(minutes=minutes * 3)  # overestimate
        holidays = await self.repo.get_holidays_between(tenant_id, start, end_estimate)
        holiday_dates = {h.holiday_date.date() for h in holidays}
        # Also add recurring holiday month/day pairs
        recurring_holidays = {(h.holiday_date.month, h.holiday_date.day) for h in holidays if h.is_recurring}

        remaining = minutes
        current = start

        while remaining > 0:
            # Skip holidays
            if current.date() in holiday_dates or (current.month, current.day) in recurring_holidays:
                current = current.replace(hour=0, minute=0, second=0) + timedelta(days=1)
                continue

            # Check if current time is within business hours
            dow = current.weekday()
            start_col, end_col = _DAY_COLUMNS[dow]
            day_start = getattr(business_hours, start_col, None)
            day_end = getattr(business_hours, end_col, None)

            if day_start is None or day_end is None:
                # Non-working day, skip to next day
                current = current.replace(hour=0, minute=0, second=0) + timedelta(days=1)
                continue

            # Check if current time is within working hours
            current_time = current.time()
            if current_time < day_start:
                # Before working hours, jump to start
                current = current.replace(hour=day_start.hour, minute=day_start.minute, second=0)
                continue
            if current_time >= day_end:
                # After working hours, skip to next day
                current = current.replace(hour=0, minute=0, second=0) + timedelta(days=1)
                continue

            # We're within working hours - add one minute
            current = current + timedelta(minutes=1)
            remaining -= 1

        return current