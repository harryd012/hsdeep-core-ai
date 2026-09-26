"""
Narrow production RCA trigger.

Emits the EXISTING ``EventType.AI_RCA_STARTED`` for incidents eligible for RCA.
It does NOT run RCA itself - ``RCALifecycle`` remains responsible for execution
(see ``app/services/rca_lifecycle.py``).

Why this module exists: ``AutonomousLifecycleOrchestrator`` is the only code that
publishes ``AI_RCA_STARTED`` (from ``_on_ticket_created``), but starting that
orchestrator would also activate the ticket / SOP / automation / closure flows,
which is out of scope. This module provides only the RCA half.

Eligibility is grounded in existing code, not guessed:
  * ``models/incident.py`` defaults ``status`` to ``"open"`` (String(30)).
  * ``api/routes/incidents.py`` treats ``status="open"`` as the active-incident
    filter (``count(tenant_id, status="open")`` / ``list(tenant_id, status="open")``).

No new event bus, no new event type, no queue, no Redis, no migration.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from app.core.db import AsyncSessionLocal
from app.repositories.incident_repository import IncidentRepository
from app.services.events.event_bus import DomainEvent, EventBus, EventType, get_event_bus

logger = logging.getLogger(__name__)

#: The incident state in which RCA is eligible (see module docstring).
RCA_ELIGIBLE_STATUS = "open"

#: Terminal states must never trigger RCA, regardless of eligibility rules.
TERMINAL_INCIDENT_STATUSES = frozenset({"resolved", "closed", "archived"})

#: Prefix for the deterministic default correlation id (see below).
_CORRELATION_PREFIX = "rca-"


def is_rca_eligible(incident: Any) -> bool:
    """True only when the incident is in the real RCA-eligible state."""
    if incident is None:
        return False
    status = str(getattr(incident, "status", "") or "").strip().lower()
    if status in TERMINAL_INCIDENT_STATUSES:
        return False
    return status == RCA_ELIGIBLE_STATUS


def default_correlation_id(incident_id: Any) -> str:
    """Deterministic per-incident correlation id.

    Makes the two existing idempotency layers work together: a repeated trigger
    for the SAME incident reuses this id, so ``RCALifecycle``'s duplicate guard
    (key ``tenant_id:incident_id:correlation_id``) recognises it as a replay.
    Pass an explicit ``correlation_id`` to force a deliberate re-run.
    """
    return f"{_CORRELATION_PREFIX}{incident_id}"


class RCATrigger:
    """Validates an incident, then publishes AI_RCA_STARTED. Never runs RCA."""

    def __init__(
        self,
        incident_repo: Any | None = None,
        event_bus: EventBus | None = None,
        session_factory: Any = AsyncSessionLocal,
    ) -> None:
        self.incident_repo = incident_repo
        self.event_bus = event_bus if event_bus is not None else get_event_bus()
        self._session_factory = session_factory

    async def _load_incident(self, incident_id: str, tenant_id: Any):
        if self.incident_repo is not None:
            return await self.incident_repo.get(uuid.UUID(str(incident_id)), tenant_id)
        # Fresh session per event, matching the existing lifecycle convention.
        async with self._session_factory() as session:
            repo = IncidentRepository(session)
            return await repo.get(uuid.UUID(str(incident_id)), tenant_id)

    async def trigger_for_incident(
        self,
        incident_id: Any,
        tenant_id: Any,
        *,
        ticket_id: Any = None,
        correlation_id: str | None = None,
    ) -> bool:
        """Publish AI_RCA_STARTED for an eligible, same-tenant incident.

        Returns True when the event was published. Returns False (never raises
        into the caller's lifecycle) when the incident is missing, belongs to
        another tenant, is not eligible, or publishing failed.
        """
        try:
            incident = await self._load_incident(incident_id, tenant_id)
        except Exception as exc:  # noqa: BLE001 - never break the caller
            logger.error("RCA trigger could not load incident %s: %s", incident_id, exc)
            return False

        if incident is None:
            logger.warning(
                "RCA trigger: incident %s not found in tenant %s",
                incident_id,
                tenant_id,
            )
            return False

        # Tenant safety: the domain object decides, not the caller's argument.
        if str(getattr(incident, "tenant_id", "")) != str(tenant_id):
            logger.warning(
                "RCA trigger: tenant mismatch for incident %s - refusing", incident_id
            )
            return False

        if not is_rca_eligible(incident):
            logger.info(
                "RCA trigger: incident %s status=%r is not RCA-eligible - skipping",
                incident_id,
                getattr(incident, "status", None),
            )
            return False

        event = DomainEvent(
            event_type=EventType.AI_RCA_STARTED,
            tenant_id=getattr(incident, "tenant_id"),
            correlation_id=correlation_id or default_correlation_id(incident_id),
            payload={
                "incident_id": str(incident_id),
                "ticket_id": str(ticket_id) if ticket_id else None,
            },
        )
        try:
            await self.event_bus.publish(event)
        except Exception as exc:  # noqa: BLE001 - publishing must not crash callers
            logger.error("RCA trigger: publishing AI_RCA_STARTED failed: %s", exc)
            return False
        logger.info("RCA trigger: AI_RCA_STARTED published for incident %s", incident_id)
        return True


async def trigger_rca_for_incident(
    incident_id: Any,
    tenant_id: Any,
    *,
    incident_repo: Any | None = None,
    event_bus: EventBus | None = None,
    ticket_id: Any = None,
    correlation_id: str | None = None,
) -> bool:
    """Convenience entry point following the existing repository conventions."""
    return await RCATrigger(incident_repo, event_bus).trigger_for_incident(
        incident_id,
        tenant_id,
        ticket_id=ticket_id,
        correlation_id=correlation_id,
    )


class RCAIncidentCreatedBridge:
    """Translates the EXISTING ``INCIDENT_CREATED`` event into AI_RCA_STARTED.

    Reuses the existing event rather than inventing a second one: a newly created
    incident sits at the default ``status="open"``, which is the RCA-eligible
    state. Registered by application startup, and it activates RCA only - none of
    the ticket / SOP / automation / closure handlers are registered.
    """

    def __init__(
        self, trigger: RCATrigger | None = None, event_bus: EventBus | None = None
    ) -> None:
        self.event_bus = event_bus if event_bus is not None else get_event_bus()
        self.trigger = trigger or RCATrigger(event_bus=self.event_bus)

    def register(self) -> None:
        self.event_bus.subscribe(EventType.INCIDENT_CREATED)(self.handle_event)

    def unregister(self) -> bool:
        """Remove this bridge's INCIDENT_CREATED handler (Phase 9A shutdown).

        Idempotent and exception-free: unregistering a bridge that was never
        registered (or was already removed) is a no-op, so shutdown can always
        call it.
        """
        try:
            return self.event_bus.unsubscribe(
                EventType.INCIDENT_CREATED, self.handle_event
            )
        except Exception as exc:  # noqa: BLE001 - shutdown must never raise
            logger.warning("RCA bridge unregister failed (ignored): %s", exc)
            return False

    async def handle_event(self, event: DomainEvent) -> None:
        payload = getattr(event, "payload", None) or {}
        incident_id = payload.get("incident_id")
        if not incident_id:
            logger.warning("RCA bridge: INCIDENT_CREATED without incident_id - ignored")
            return
        try:
            await self.trigger.trigger_for_incident(
                incident_id,
                event.tenant_id,
                ticket_id=payload.get("ticket_id"),
                correlation_id=getattr(event, "correlation_id", None),
            )
        except Exception as exc:  # noqa: BLE001 - never break event infrastructure
            logger.error("RCA bridge failed for incident %s: %s", incident_id, exc)
