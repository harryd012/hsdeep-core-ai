"""
Production RCA lifecycle - the missing construction + subscription path.

Before this module, ``RCAService`` and ``AutonomousLifecycleOrchestrator`` were
constructed **only in tests**. ``AutonomousLifecycleOrchestrator._on_ticket_created``
publishes ``AI_RCA_STARTED`` with the comment "the RCAService listener picks this
up", but nothing ever subscribed - so RCA never ran for a real incident.

This module supplies exactly that missing piece, reusing the existing
infrastructure and inventing no new mechanism:

* one production construction path (``create_rca_service``) so RCAService is not
  constructed in several places
* the existing global bus singleton ``get_event_bus()`` (see event_bus.py,
  "Global singleton - initialized in main.py startup")
* the existing subscription API ``EventBus.subscribe(EventType.X)(handler)``
* the existing RCA handler ``RCAService.register()`` / ``_on_ai_rca_started``
* the existing DB session factory ``app.core.db.AsyncSessionLocal``

No new event bus, no new database, no new RCA engine.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

from app.core.config import get_settings
from app.core.db import AsyncSessionLocal
from app.services.dependency_intelligence.integration import (
    DependencyEnrichedRootCauseAnalyser,
)
from app.services.events.event_bus import (
    DomainEvent,
    EventBus,
    EventType,
    get_event_bus,
)
from app.services.rca_service import RCAService, RootCauseAnalyser, RuleBasedRootCauseAnalyser

logger = logging.getLogger(__name__)

#: Bound on the duplicate-event guard so it can never grow without limit.
MAX_TRACKED_EVENTS = 1000


def create_rca_service(
    *,
    event_bus: EventBus | None = None,
    session_factory: Any = AsyncSessionLocal,
    analyser: RootCauseAnalyser | None = None,
) -> RCAService:
    """The single production construction path for ``RCAService``.

    Defaults to the dependency-enriched analyser wrapping the existing
    rule-based engine. An explicitly injected ``analyser`` is always honoured, so
    ``RCAService(analyser=test_analyser)`` keeps working unchanged.
    """
    return RCAService(
        event_bus=event_bus if event_bus is not None else get_event_bus(),
        session_factory=session_factory,
        analyser=analyser
        if analyser is not None
        else DependencyEnrichedRootCauseAnalyser(RuleBasedRootCauseAnalyser()),
    )


class RCALifecycle:
    """Owns the production RCA subscription and its duplicate-event guard.

    Idempotency: RCA events may be delivered more than once. The guard key is
    ``tenant_id:incident_id:correlation_id`` - a *replay of the same event* is
    ignored, while a genuinely new trigger (different correlation_id) still runs.
    In-memory and bounded; no Redis, no distributed lock.
    """

    def __init__(
        self,
        service: RCAService,
        event_bus: EventBus,
        *,
        max_tracked_events: int = MAX_TRACKED_EVENTS,
    ) -> None:
        self.service = service
        self.event_bus = event_bus
        self._order: deque[str] = deque(maxlen=max_tracked_events)
        self._seen: set[str] = set()
        # Phase 9A: makes stop() idempotent (safe to call twice, and safe when
        # start() never ran).
        self._stopped = False

    @staticmethod
    def event_key(event: DomainEvent) -> str:
        payload = getattr(event, "payload", None) or {}
        return "{}:{}:{}".format(
            getattr(event, "tenant_id", None),
            payload.get("incident_id"),
            getattr(event, "correlation_id", None),
        )

    def is_duplicate(self, event: DomainEvent) -> bool:
        return self.event_key(event) in self._seen

    def _track(self, event: DomainEvent) -> None:
        key = self.event_key(event)
        if len(self._order) == self._order.maxlen:
            self._seen.discard(self._order[0])
        self._order.append(key)
        self._seen.add(key)

    def register(self) -> None:
        """Subscribe the guarded handler to AI_RCA_STARTED (existing API)."""
        self.event_bus.subscribe(EventType.AI_RCA_STARTED)(self.handle_event)

    async def handle_event(self, event: DomainEvent) -> None:
        """Event -> incident lookup -> RCA -> RootCauseResult, failing safely."""
        if self.is_duplicate(event):
            logger.warning(
                "Ignoring duplicate %s for incident %s",
                EventType.AI_RCA_STARTED.value,
                (event.payload or {}).get("incident_id"),
            )
            return
        self._track(event)
        try:
            # The service's own registered handler; it resolves the incident by
            # (incident_id, event.tenant_id), loads the alerts, runs the analyser
            # and publishes the existing RCA_COMPLETED event.
            await self.service._on_ai_rca_started(event)
        except Exception as exc:  # noqa: BLE001 - never break event infrastructure
            logger.error(
                "RCA execution failed for incident %s: %s",
                (event.payload or {}).get("incident_id"),
                exc,
                exc_info=True,
            )

    async def start(self) -> None:
        """Register, then ensure the existing bus is running. Never fatal."""
        self.register()
        logger.info("RCA service subscribed to %s", EventType.AI_RCA_STARTED.value)
        try:
            await self.event_bus.start()
        except Exception as exc:  # noqa: BLE001 - a started bus may raise
            logger.warning("Event bus start skipped/again: %s", exc)

    def unregister(self) -> bool:
        """Remove this lifecycle's AI_RCA_STARTED handler (idempotent)."""
        try:
            return self.event_bus.unsubscribe(
                EventType.AI_RCA_STARTED, self.handle_event
            )
        except Exception as exc:  # noqa: BLE001 - shutdown must never raise
            logger.warning("RCA lifecycle unregister failed (ignored): %s", exc)
            return False

    async def stop(self, *, drain_timeout: float | None = None) -> None:
        """Graceful, bounded, idempotent shutdown of the RCA half (Phase 9A).

        * unregisters this lifecycle's ``AI_RCA_STARTED`` handler so nothing new
          is dispatched to it;
        * stops the existing event-bus worker, which drains events already
          queued, bounded by ``Settings.shutdown_drain_seconds`` so a hung
          handler can never block a rolling deploy;
        * never raises, is safe when ``start()`` was never called (e.g. wiring
          failed at startup, or a runtime role that does not consume events),
          and is safe to call more than once.

        Normal RCA behaviour is unchanged - this only runs at process shutdown.
        """
        if self._stopped:
            logger.debug("RCA lifecycle already stopped - no-op.")
            return
        self._stopped = True

        self.unregister()
        timeout = (
            drain_timeout
            if drain_timeout is not None
            else get_settings().shutdown_drain_seconds
        )
        try:
            await asyncio.wait_for(self.event_bus.stop(), timeout=timeout)
            logger.info(
                "RCA lifecycle stopped: bus worker cancelled and queued events "
                "drained within %ss.",
                timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "RCA lifecycle shutdown exceeded the %ss drain budget; remaining "
                "queued events are abandoned to process exit.",
                timeout,
            )
        except Exception as exc:  # noqa: BLE001 - shutdown must never raise
            logger.warning("RCA lifecycle shutdown error (ignored): %s", exc)


def start_rca_lifecycle(event_bus: EventBus | None = None) -> RCALifecycle:
    """Convenience entry point used by application startup."""
    bus = event_bus if event_bus is not None else get_event_bus()
    return RCALifecycle(create_rca_service(event_bus=bus), bus)
