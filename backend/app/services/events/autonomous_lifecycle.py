"""
Autonomous Incident Lifecycle Orchestrator.

Event-driven workflow that implements the complete enterprise incident lifecycle:
  Monitoring → Alert → Correlation → Incident → Ticket → AI RCA → SOP →
  Automation → Validation → Resolution → Closure → ServiceNow Sync

Each step is triggered by events and publishes new events - completely decoupled.

Race-condition fix (Task 1):
  Previously the orchestrator held a single DB session created at app startup.
  That session's PostgreSQL READ-COMMITTED snapshot was frozen at startup time,
  so it could NEVER see rows committed after startup — the retry logic was
  powerless.  Additionally, ``INCIDENT_CREATED`` was published *before* the
  creating transaction committed, so even a fresh session would race a
  not-yet-visible row.

  Fix: each event handler now creates its OWN fresh ``AsyncSession`` via the
  injected ``session_factory`` (``AsyncSessionLocal`` by default).  Combined
  with the deferred-event-publishing fix in ``AlertService`` /
  ``CollectionOrchestrator`` (events are published only *after* ``commit()``),
  the handler's fresh session sees the committed row immediately — no retries
  needed.  The old retry scaffolding is kept as a defensive backstop (reduced
  to 2 attempts) but is no longer the primary mechanism.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

from app.core.db import AsyncSessionLocal
from app.models.incident import Incident
from app.models.ticket import Ticket
from app.repositories.incident_repository import IncidentRepository
from app.repositories.ticket_repository import TicketRepository
from app.services.events.event_bus import EventBus, EventType, DomainEvent
from app.services.incident_service import IncidentService
from app.services.ticket_service import TicketService

logger = logging.getLogger(__name__)

# Reduced from 4 attempts — with post-commit publishing + fresh sessions the
# first read should succeed.  Kept as a defensive backstop only.
# Attempts land at ~0.1s, 0.2s after the first try (~0.3s worst case).
_INCIDENT_LOOKUP_MAX_ATTEMPTS = 2
_INCIDENT_LOOKUP_BASE_DELAY_SECONDS = 0.1

# Type alias for a session factory (AsyncSessionLocal is one such factory).
AsyncSessionFactory = Callable[[], Any]  # returns an async context manager yielding AsyncSession


class AutonomousLifecycleOrchestrator:
    """Event-driven orchestrator for the autonomous incident lifecycle.

    Subscribes to lifecycle events and automatically advances the workflow:
    - AlertCorrelated → creates Ticket
    - TicketCreated → triggers AI RCA
    - RCACompleted → selects SOP
    - SOPSelected → starts automation
    - SOPCompleted → starts validation
    - ValidationPassed → resolves ticket and incident
    - TicketResolved → closes ticket after delay
    - TicketClosed → closes incident and syncs ServiceNow
    """

    def __init__(
        self,
        event_bus: EventBus,
        session_factory: AsyncSessionFactory = AsyncSessionLocal,
    ):
        self.event_bus = event_bus
        self._session_factory = session_factory

        # Register event handlers
        self._register_handlers()

        # VALIDATION_STARTED -> PASSED/FAILED from fresh telemetry. Without it
        # nothing ever validated, so automated remediation could not resolve
        # (or, worse, would be resolved on the SOP's word alone).
        from app.core.config import get_settings
        from app.services.events.recovery_validator import RecoveryValidator

        cfg = get_settings()
        self.recovery_validator = RecoveryValidator(
            event_bus,
            session_factory=session_factory,
            attempts=cfg.recovery_validation_attempts,
            interval_seconds=cfg.recovery_validation_interval_seconds,
            consecutive_passes=cfg.recovery_validation_consecutive_passes,
        )
        self.recovery_validator.register()

    def _register_handlers(self) -> None:
        """Register all event handlers for the lifecycle."""
        # Alert → Incident → Ticket flow
        self.event_bus.subscribe(EventType.INCIDENT_CREATED)(self._on_incident_created)

        # AI RCA flow
        self.event_bus.subscribe(EventType.TICKET_CREATED)(self._on_ticket_created)

        # SOP flow — RCA_COMPLETED triggers SOP selection
        self.event_bus.subscribe(EventType.RCA_COMPLETED)(self._on_rca_completed)

        # Automation flow
        self.event_bus.subscribe(EventType.SOP_SELECTED)(self._on_sop_selected)

        # Validation flow
        self.event_bus.subscribe(EventType.SOP_COMPLETED)(self._on_sop_completed)

        # Resolution flow
        self.event_bus.subscribe(EventType.VALIDATION_PASSED)(self._on_validation_passed)
        self.event_bus.subscribe(EventType.VALIDATION_FAILED)(self._on_validation_failed)

        # Closure flow
        self.event_bus.subscribe(EventType.TICKET_RESOLVED)(self._on_ticket_resolved)
        self.event_bus.subscribe(EventType.INCIDENT_RESOLVED)(self._on_incident_resolved)
        self.event_bus.subscribe(EventType.TICKET_CLOSED)(self._on_ticket_closed)

    async def start(self) -> None:
        """Start the orchestrator."""
        await self.event_bus.start()
        logger.info("Autonomous Lifecycle Orchestrator started")

    async def stop(self) -> None:
        """Stop the orchestrator."""
        await self.event_bus.stop()
        logger.info("Autonomous Lifecycle Orchestrator stopped")

    # -- Alert → Incident → Ticket Flow ---------------------------------------

    async def _get_incident_with_retry(
        self, incident_id: str, tenant_id: str
    ) -> Optional[Incident]:
        """Re-fetches the incident with minimal backoff.

        With the post-commit event publishing fix, the incident row is
        guaranteed to be committed and visible to a fresh session by the
        time this handler runs.  The retry is kept as a narrow defensive
        backstop against sub-millisecond scheduler jitter.
        """
        delay = _INCIDENT_LOOKUP_BASE_DELAY_SECONDS

        for attempt in range(1, _INCIDENT_LOOKUP_MAX_ATTEMPTS + 1):
            async with self._session_factory() as db:
                incident_service = IncidentService(
                    incident_repo=IncidentRepository(db),
                    alert_repo=__import__(
                        "app.repositories.alert_repository", fromlist=["AlertRepository"]
                    ).AlertRepository(db),
                )
                try:
                    return await incident_service.get(incident_id, tenant_id)
                except Exception:
                    if attempt == _INCIDENT_LOOKUP_MAX_ATTEMPTS:
                        logger.error(
                            "Incident %s still not found after %d attempts - giving up.",
                            incident_id,
                            attempt,
                        )
                        return None
                    logger.warning(
                        "Incident %s not found on attempt %d/%d, retrying in %.2fs",
                        incident_id,
                        attempt,
                        _INCIDENT_LOOKUP_MAX_ATTEMPTS,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    delay *= 2
        return None

    async def _on_incident_created(self, event: DomainEvent) -> None:
        """Handle IncidentCreated event → automatically create ticket.

        Creates a FRESH DB session for this handler so the transaction
        snapshot includes the just-committed incident row.  Events are only
        published after this session commits, ensuring downstream handlers
        also see consistent data.
        """
        incident_id = event.payload.get("incident_id")
        if not incident_id:
            logger.warning("IncidentCreated event missing incident_id")
            return

        try:
            # Get the incident using a fresh session — the INCIDENT_CREATED
            # event is only published AFTER the creating transaction commits
            # (see AlertService / CollectionOrchestrator deferred publishing),
            # so a fresh session sees the committed row immediately.
            incident = await self._get_incident_with_retry(incident_id, event.tenant_id)

            if incident is None:
                logger.error(
                    "Failed to create ticket for incident %s: not found after retries",
                    incident_id,
                )
                return

            # Create the ticket in a fresh session tied to this handler's work.
            async with self._session_factory() as db:
                ticket_service = TicketService(db)
                ticket = await ticket_service.create_ticket(
                    tenant_id=event.tenant_id,
                    title=incident.title,
                    description=incident.description
                    or f"Automatically created from incident {incident.id}",
                    incident_id=incident.id,
                    source="incident_triggered",
                    priority=incident.priority,
                    category=incident.correlation_type,
                    subcategory=incident.root_cause_summary[:100]
                    if incident.root_cause_summary
                    else None,
                    assignment_group=incident.assigned_team,
                    assigned_to=incident.assigned_to,
                    custom_fields={
                        "affected_assets": incident.affected_assets,
                        "business_service_id": str(incident.business_service_id)
                        if incident.business_service_id
                        else None,
                    },
                )

                await db.commit()

                # Publish TicketCreated event AFTER the ticket row is committed,
                # so downstream handlers see it with a fresh session.
                await self.event_bus.publish(DomainEvent(
                    event_type=EventType.TICKET_CREATED,
                    tenant_id=event.tenant_id,
                    correlation_id=event.correlation_id,
                    payload={
                        "ticket_id": str(ticket.id),
                        "ticket_number": ticket.ticket_number,
                        "incident_id": str(incident.id),
                    }
                ))

                logger.info(
                    "Auto-created ticket %s for incident %s",
                    ticket.ticket_number,
                    incident.id,
                )

        except Exception as exc:
            logger.error(
                "Failed to create ticket for incident %s: %s",
                incident_id,
                exc,
                exc_info=True,
            )

    # -- AI RCA Flow ---------------------------------------------------------

    async def _on_ticket_created(self, event: DomainEvent) -> None:
        """Handle TicketCreated event → trigger AI RCA."""
        ticket_id = event.payload.get("ticket_id")
        if not ticket_id:
            logger.warning("TicketCreated event missing ticket_id")
            return

        # Publish AI_RCA_STARTED event — the RCAService listener picks this up
        await self.event_bus.publish(DomainEvent(
            event_type=EventType.AI_RCA_STARTED,
            tenant_id=event.tenant_id,
            correlation_id=event.correlation_id,
            payload={
                "ticket_id": ticket_id,
                "incident_id": event.payload.get("incident_id"),
            }
        ))

        logger.info("AI RCA started for ticket %s", ticket_id)

    async def _on_rca_completed(self, event: DomainEvent) -> None:
        """Handle RCACompleted event → publish SOP_SELECTED.

        Uses the ``recommended_sop_id`` from the RCA payload and the
        ``requires_approval`` flag to determine ``auto_remediate``:
        - Gated SOPs (requires_approval=True) → auto_remediate=False
          (always — an approval step must be completed first).
        - Non-gated SOPs → auto_remediate is governed by the
          ``auto_remediate_default`` tenant/global setting (default False).
        """
        ticket_id = event.payload.get("ticket_id")
        incident_id = event.payload.get("incident_id")
        sop_id = event.payload.get("recommended_sop_id")
        requires_approval = event.payload.get("requires_approval", False)

        if not sop_id or not incident_id:
            logger.warning(
                "RCA completed but missing recommended_sop_id or incident_id — "
                "cannot publish SOP_SELECTED"
            )
            return

        # Determine auto_remediate:
        #   - Gated SOPs (requires_approval=True) → always False
        #   - Non-gated SOPs → configurable via settings (default False)
        if requires_approval:
            auto_remediate = False
        else:
            from app.core.config import get_settings
            settings = get_settings()
            auto_remediate = settings.auto_remediate_default

        await self.event_bus.publish(DomainEvent(
            event_type=EventType.SOP_SELECTED,
            tenant_id=event.tenant_id,
            correlation_id=event.correlation_id,
            payload={
                "ticket_id": ticket_id,
                "incident_id": str(incident_id),
                "sop_id": str(sop_id),
                "auto_remediate": auto_remediate,
            }
        ))

        logger.info(
            "RCA completed for ticket %s — SOP %s selected (auto_remediate=%s)",
            ticket_id,
            sop_id,
            auto_remediate,
        )

    # -- SOP Flow -------------------------------------------------------------

    async def _on_sop_selected(self, event: DomainEvent) -> None:
        """Handle SOPSelected event → start automation if auto-remediation enabled."""
        ticket_id = event.payload.get("ticket_id")
        sop_id = event.payload.get("sop_id")
        auto_remediate = event.payload.get("auto_remediate", False)

        if not ticket_id or not sop_id:
            logger.warning("SOPSelected event missing required fields")
            return

        if auto_remediate:
            # Start automation
            await self.event_bus.publish(DomainEvent(
                event_type=EventType.SOP_STARTED,
                tenant_id=event.tenant_id,
                correlation_id=event.correlation_id,
                payload={
                    "ticket_id": ticket_id,
                    "sop_id": sop_id,
                    "incident_id": event.payload.get("incident_id"),
                }
            ))
            logger.info("Auto-remediation started for ticket %s with SOP %s", ticket_id, sop_id)
        else:
            logger.info("SOP selected for ticket %s but auto-remediation disabled", ticket_id)

    async def _on_sop_completed(self, event: DomainEvent) -> None:
        """Handle SOPCompleted event → start validation.

        A failed SOP is never validated as if it had worked: it goes straight
        to VALIDATION_FAILED (engineer escalation)."""
        ticket_id = event.payload.get("ticket_id")
        if event.payload.get("success") is False:
            await self.event_bus.publish(DomainEvent(
                event_type=EventType.VALIDATION_FAILED,
                tenant_id=event.tenant_id,
                correlation_id=event.correlation_id,
                payload={
                    "ticket_id": ticket_id,
                    "incident_id": event.payload.get("incident_id"),
                    "sop_id": event.payload.get("sop_id"),
                    "failure_reason": event.payload.get("result_notes") or "SOP execution failed",
                },
            ))
            logger.warning("SOP failed for ticket %s - skipping validation, escalating", ticket_id)
            return

        await self.event_bus.publish(DomainEvent(
            event_type=EventType.VALIDATION_STARTED,
            tenant_id=event.tenant_id,
            correlation_id=event.correlation_id,
            payload={
                "ticket_id": ticket_id,
                "incident_id": event.payload.get("incident_id"),
                "sop_id": event.payload.get("sop_id"),
            }
        ))

        logger.info("Validation started for ticket %s", ticket_id)

    # -- Validation Flow ------------------------------------------------------

    async def _on_validation_passed(self, event: DomainEvent) -> None:
        """Handle ValidationPassed event → resolve ticket and incident.

        Uses a FRESH session via the injected session_factory so it sees
        the latest committed state (the ticket was committed before
        TICKET_CREATED was published, and downstream events follow the same
        after-commit pattern).
        """
        ticket_id = event.payload.get("ticket_id")
        incident_id = event.payload.get("incident_id")

        if not ticket_id:
            logger.warning("ValidationPassed event missing ticket_id")
            return

        try:
            async with self._session_factory() as db:
                ticket_service = TicketService(db)

                # Resolve ticket
                ticket = await ticket_service.get_ticket(ticket_id, event.tenant_id)
                if ticket:
                    resolved_ticket = await ticket_service.resolve_ticket(
                        ticket,
                        resolution_note="Automatically resolved: validation passed",
                        resolved_by=None,
                    )
                    await db.commit()

                    await self.event_bus.publish(DomainEvent(
                        event_type=EventType.TICKET_RESOLVED,
                        tenant_id=event.tenant_id,
                        correlation_id=event.correlation_id,
                        payload={
                            "ticket_id": str(ticket.id),
                            "ticket_number": ticket.ticket_number,
                        }
                    ))

                # Resolve incident
                if incident_id:
                    incident_service = IncidentService(
                        incident_repo=IncidentRepository(db),
                        alert_repo=__import__(
                            "app.repositories.alert_repository", fromlist=["AlertRepository"]
                        ).AlertRepository(db),
                    )
                    await incident_service.resolve(incident_id, event.tenant_id)
                    await db.commit()
                    await self.event_bus.publish(DomainEvent(
                        event_type=EventType.INCIDENT_RESOLVED,
                        tenant_id=event.tenant_id,
                        correlation_id=event.correlation_id,
                        payload={
                            "incident_id": str(incident_id),
                        }
                    ))

                logger.info(
                    "Validation passed - resolved ticket %s and incident %s",
                    ticket_id,
                    incident_id,
                )

        except Exception as exc:
            logger.error(
                "Failed to resolve ticket/incident after validation: %s",
                exc,
                exc_info=True,
            )

    async def _on_validation_failed(self, event: DomainEvent) -> None:
        """Handle ValidationFailed event → escalate engineer."""
        ticket_id = event.payload.get("ticket_id")
        incident_id = event.payload.get("incident_id")
        failure_reason = event.payload.get("failure_reason", "Validation failed")

        await self.event_bus.publish(DomainEvent(
            event_type=EventType.ENGINEER_ESCALATED,
            tenant_id=event.tenant_id,
            correlation_id=event.correlation_id,
            payload={
                "ticket_id": ticket_id,
                "incident_id": incident_id,
                "reason": failure_reason,
            }
        ))

        logger.warning("Validation failed for ticket %s - escalated to engineer", ticket_id)

    # -- Closure Flow ---------------------------------------------------------

    async def _on_ticket_resolved(self, event: DomainEvent) -> None:
        """Handle TicketResolved event → schedule automatic closure.

        STAGE 4 NOTE: This stub will be replaced with a real delayed job
        using APScheduler (already in the stack) when the closure scheduler
        is implemented. For now it logs the pending closure.
        """
        ticket_id = event.payload.get("ticket_id")
        if not ticket_id:
            return

        logger.info("Ticket %s resolved - waiting for closure delay", ticket_id)

    async def _on_incident_resolved(self, event: DomainEvent) -> None:
        """Handle IncidentResolved event."""
        incident_id = event.payload.get("incident_id")
        if not incident_id:
            return

        logger.info("Incident %s resolved", incident_id)

    async def _on_ticket_closed(self, event: DomainEvent) -> None:
        """Handle TicketClosed event → close incident and sync ServiceNow.

        Uses a FRESH session to see the latest committed state.
        """
        ticket_id = event.payload.get("ticket_id")
        incident_id = event.payload.get("incident_id")

        try:
            async with self._session_factory() as db:
                incident_repo = IncidentRepository(db)

                # Close incident if it's linked
                if incident_id:
                    incident = await incident_repo.get(incident_id, event.tenant_id)
                    if incident and incident.status not in ("closed", "archived"):
                        from app.services.incident_lifecycle_service import (
                            IncidentLifecycleService,
                        )
                        lifecycle_service = IncidentLifecycleService(incident_repo)
                        await lifecycle_service.close(incident.id, event.tenant_id)
                        await db.commit()

                        await self.event_bus.publish(DomainEvent(
                            event_type=EventType.INCIDENT_CLOSED,
                            tenant_id=event.tenant_id,
                            correlation_id=event.correlation_id,
                            payload={"incident_id": str(incident.id)}
                        ))

                    # Sync to ServiceNow
                    await self.event_bus.publish(DomainEvent(
                        event_type=EventType.SERVICENOW_SYNCED,
                        tenant_id=event.tenant_id,
                        correlation_id=event.correlation_id,
                        payload={
                            "ticket_id": ticket_id,
                            "incident_id": str(incident_id) if incident_id else None,
                        }
                    ))

                    logger.info(
                        "Ticket %s closed, incident %s closed, ServiceNow synced",
                        ticket_id,
                        incident_id,
                    )

        except Exception as exc:
            logger.error(
                "Failed to close incident/sync ServiceNow: %s",
                exc,
                exc_info=True,
            )


# Singleton instance
_orchestrator: Optional[AutonomousLifecycleOrchestrator] = None


def get_orchestrator() -> Optional[AutonomousLifecycleOrchestrator]:
    """Get the global orchestrator instance."""
    return _orchestrator


def set_orchestrator(orchestrator: AutonomousLifecycleOrchestrator) -> None:
    """Set the global orchestrator instance."""
    global _orchestrator
    _orchestrator = orchestrator
