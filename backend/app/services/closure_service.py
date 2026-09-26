"""
Closure Scheduler — Sprint Next: Close the Automation Loop, STAGE 4.

Subscribes to ``TICKET_RESOLVED`` (published by the orchestrator when
validation passes).  Schedules a delayed closure job via the running
APSheduler ``AsyncIOScheduler`` (``app.state.scheduler``) that, after
``closure_delay_hours`` (default 24h, configurable), publishes
``TICKET_CLOSED`` — which the orchestrator then uses to close the incident
and trigger ServiceNow sync.

The delay is configurable per-registration so tests can use a short
window (e.g. 0 seconds) instead of waiting 24 hours.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, TYPE_CHECKING

from app.core.config import get_settings

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

AsyncSessionFactory = Callable[[], Any]


class ClosureService:
    """Event listener that schedules automatic ticket closure.

    On ``TICKET_RESOLVED`` it registers a one-shot APScheduler job on the
    shared ``AsyncIOScheduler``.  When the job fires it publishes
    ``TICKET_CLOSED`` (with ``ticket_id`` and ``incident_id``), closing
    the loop: ticket closed → incident closed → ServiceNow sync.
    """

    def __init__(
        self,
        event_bus: Any,
        *,
        scheduler: Optional["AsyncIOScheduler"] = None,
        closure_delay_hours: Optional[float] = None,
    ):
        self.event_bus = event_bus
        # scheduler may be None in tests — callers can inject one or use
        # set_scheduler() after the app starts.
        self._scheduler = scheduler
        self._closure_delay_hours = closure_delay_hours

    def set_scheduler(self, scheduler: "AsyncIOScheduler") -> None:
        """Inject the APScheduler instance (called from app startup once
        ``app.state.scheduler`` exists)."""
        self._scheduler = scheduler

    def register(self) -> None:
        """Subscribe this service's handler to TICKET_RESOLVED."""
        self.event_bus.subscribe(self._event_type_ticket_resolved())(self._on_ticket_resolved)

    @staticmethod
    def _event_type_ticket_resolved():
        from app.services.events.event_bus import EventType
        return EventType.TICKET_RESOLVED

    def _get_delay_hours(self) -> float:
        if self._closure_delay_hours is not None:
            return self._closure_delay_hours
        settings = get_settings()
        return float(settings.closure_delay_hours)

    async def _on_ticket_resolved(self, event: Any) -> None:
        """Handle TicketResolved → schedule delayed closure."""
        ticket_id = event.payload.get("ticket_id")
        incident_id = event.payload.get("incident_id")

        if not ticket_id:
            logger.warning("TicketResolved event missing ticket_id")
            return

        delay_hours = self._get_delay_hours()
        run_at = datetime.now(timezone.utc) + timedelta(hours=delay_hours)

        if self._scheduler is None:
            logger.warning(
                "ClosureService has no scheduler — cannot schedule closure for ticket %s. "
                "Falling back to immediate closure.",
                ticket_id,
            )
            # Publish TICKET_CLOSED immediately as a fallback so the pipeline
            # doesn't get stuck if the scheduler isn't wired up.
            await self._publish_closed(event, ticket_id, incident_id)
            return

        job_id = f"close_ticket_{ticket_id}_{uuid.uuid4().hex[:8]}"
        self._scheduler.add_job(
            self._do_close,
            "date",
            run_date=run_at,
            args=(event, ticket_id, incident_id),
            id=job_id,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        logger.info(
            "Ticket %s resolved — closure scheduled for %s (delay %.1fh)",
            ticket_id, run_at.isoformat(), delay_hours,
        )

    async def _do_close(self, event: Any, ticket_id: str, incident_id: str | None) -> None:
        """The scheduled job body — publishes TICKET_CLOSED."""
        logger.info("Closure delay elapsed — closing ticket %s", ticket_id)
        await self._publish_closed(event, ticket_id, incident_id)

    async def _publish_closed(self, event: Any, ticket_id: str, incident_id: str | None) -> None:
        """Publish a TICKET_CLOSED event (and INCIDENT_CLOSED payload)."""
        from app.services.events.event_bus import DomainEvent, EventType

        await self.event_bus.publish(DomainEvent(
            event_type=EventType.TICKET_CLOSED,
            tenant_id=event.tenant_id,
            correlation_id=event.correlation_id,
            payload={
                "ticket_id": ticket_id,
                "incident_id": incident_id,
            },
        ))
        logger.info("Published TICKET_CLOSED for ticket %s", ticket_id)
