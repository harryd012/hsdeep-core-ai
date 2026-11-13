"""
Enterprise Event Bus.

Asynchronous, decoupled event pipeline for the autonomous incident lifecycle.
Events are published to an in-memory queue with async handlers - no tight
coupling between producers and consumers.

Event Types:
  AlertCreated, AlertCorrelated, IncidentCreated, TicketCreated,
  AIRCAStarted, RCACompleted, SOPSelected, SOPStarted, SOPCompleted,
  ValidationStarted, ValidationPassed, ValidationFailed, TicketResolved,
  IncidentResolved, TicketClosed, IncidentClosed, ServiceNowSynced,
  AuditCreated, EngineerEscalated,
  SourceRecovered, SourceHealthDegraded, SourceRunTimeout,
  SourceRetryAttempt, SourceRateLimited
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """All event types in the autonomous lifecycle pipeline."""
    ALERT_CREATED = "alert_created"
    ALERT_CORRELATED = "alert_correlated"
    INCIDENT_CREATED = "incident_created"
    TICKET_CREATED = "ticket_created"
    AI_RCA_STARTED = "ai_rca_started"
    RCA_COMPLETED = "rca_completed"
    SOP_SELECTED = "sop_selected"
    SOP_STARTED = "sop_started"
    SOP_COMPLETED = "sop_completed"
    VALIDATION_STARTED = "validation_started"
    VALIDATION_PASSED = "validation_passed"
    VALIDATION_FAILED = "validation_failed"
    TICKET_RESOLVED = "ticket_resolved"
    INCIDENT_RESOLVED = "incident_resolved"
    TICKET_CLOSED = "ticket_closed"
    INCIDENT_CLOSED = "incident_closed"
    SERVICENOW_SYNCED = "servicenow_synced"
    AUDIT_CREATED = "audit_created"
    ENGINEER_ESCALATED = "engineer_escalated"
    # Monitoring source health events
    SOURCE_RECOVERED = "source_recovered"
    SOURCE_HEALTH_DEGRADED = "source_health_degraded"
    SOURCE_RUN_TIMEOUT = "source_run_timeout"
    SOURCE_RETRY_ATTEMPT = "source_retry_attempt"
    SOURCE_RATE_LIMITED = "source_rate_limited"
    # WAN failover intelligence (observed HA/SD-WAN path transitions)
    WAN_FAILOVER_DETECTED = "wan_failover_detected"
    WAN_FAILOVER_RECOVERED = "wan_failover_recovered"
    WAN_FAILOVER_VERIFICATION_FAILED = "wan_failover_verification_failed"
    # Operational SLA policy administration. Configuration changes, not
    # observations: these record that the thresholds the detector evaluates were
    # edited, so a policy change is auditable next to the episodes it influenced.
    WAN_SLA_POLICY_CREATED = "wan_sla_policy_created"
    WAN_SLA_POLICY_UPDATED = "wan_sla_policy_updated"
    WAN_SLA_POLICY_DELETED = "wan_sla_policy_deleted"


@dataclass
class DomainEvent:
    """Base event with metadata."""
    event_type: EventType
    tenant_id: uuid.UUID
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: Optional[str] = None  # Links related events in a lifecycle
    payload: Dict[str, Any] = field(default_factory=dict)


# Type alias for event handlers
EventHandler = Callable[[DomainEvent], Awaitable[None]]


def handler_name(handler: EventHandler) -> str:
    """Stable name for idempotency records (module + qualified name)."""
    return f"{getattr(handler, '__module__', '?')}.{getattr(handler, '__qualname__', repr(handler))}"


class EventBus:
    """Async event bus with fan-out to multiple handlers.

    Without a transport, events stay in this process (single-process
    deployments, local development). With a transport (Redis Streams, R2,
    ``EVENT_TRANSPORT=redis``) every publish goes to the shared stream and the
    processes that run consumers handle it, so an event raised in the API
    process reaches a separately deployed worker. The handler interface is the
    same either way.
    """

    def __init__(self, transport=None):
        self.transport = transport
        self._consumer_task: Optional[asyncio.Task] = None
        self._consumer_stop: Optional[asyncio.Event] = None
        self._handlers: Dict[EventType, List[EventHandler]] = {}
        self._all_handlers: List[EventHandler] = []
        self._queue: asyncio.Queue[DomainEvent] = asyncio.Queue()
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._metrics = {
            "published": 0,
            "processed": 0,
            "errors": 0,
        }

    def subscribe(self, event_type: Optional[EventType] = None) -> Callable[[EventHandler], EventHandler]:
        """Decorator to register an event handler.

        Args:
            event_type: Specific event type to handle, or None for all events.

        Returns:
            Decorator that registers the handler.
        """
        def decorator(handler: EventHandler) -> EventHandler:
            if event_type is None:
                self._all_handlers.append(handler)
                logger.debug("Registered global event handler %s", handler.__name__)
            else:
                if event_type not in self._handlers:
                    self._handlers[event_type] = []
                self._handlers[event_type].append(handler)
                logger.debug("Registered handler %s for event %s", handler.__name__, event_type)
            return handler
        return decorator

    async def publish(self, event: DomainEvent) -> None:
        """Publish an event to all subscribers.

        Non-blocking - queues the event for background processing.
        """
        self._metrics["published"] += 1
        if self.transport is not None:
            try:
                await self.transport.publish(event)
                logger.debug("Published event %s (id=%s) to the shared stream", event.event_type, event.event_id)
                return
            except Exception as exc:  # noqa: BLE001 - publish has never raised to callers
                self._metrics["publish_failed"] = self._metrics.get("publish_failed", 0) + 1
                if not self._running:
                    logger.error(
                        "Event %s (id=%s) could not be published (shared transport unavailable: %s) "
                        "and this process runs no consumers; the event is lost.",
                        event.event_type, event.event_id, exc,
                    )
                    return
                logger.warning(
                    "Shared transport unavailable (%s); handling event %s in this process.",
                    exc, event.event_id,
                )
        await self._queue.put(event)
        logger.debug("Published event %s (id=%s)", event.event_type, event.event_id)

    async def publish_and_wait(self, event: DomainEvent) -> None:
        """Publish an event and wait for all handlers to complete.

        Use for critical events where you need confirmation of processing.
        """
        await self.publish(event)
        # Brief yield to let the worker pick it up
        await asyncio.sleep(0.01)

    async def _process_event(self, event: DomainEvent) -> None:
        """Process a single event through all registered handlers."""
        handlers = self._all_handlers + self._handlers.get(event.event_type, [])

        if not handlers:
            logger.debug("No handlers for event %s", event.event_type)
            return

        tasks = []
        for handler in handlers:
            try:
                tasks.append(asyncio.create_task(handler(event)))
            except Exception as exc:
                logger.error("Failed to schedule handler for event %s: %s", event.event_type, exc)
                self._metrics["errors"] += 1

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._metrics["processed"] += 1

    async def dispatch_tracked(self, event: DomainEvent, skip: set) -> Dict[str, Optional[BaseException]]:
        """Run the handlers for ``event`` except those named in ``skip``, and
        report each handler's outcome (None = success). Used by the shared
        transport, which acknowledges only when every handler succeeded."""
        handlers = [h for h in self._all_handlers + self._handlers.get(event.event_type, [])
                    if handler_name(h) not in skip]
        results: Dict[str, Optional[BaseException]] = {name: None for name in skip}
        outcomes = await asyncio.gather(*(h(event) for h in handlers), return_exceptions=True)
        for handler, outcome in zip(handlers, outcomes):
            if isinstance(outcome, BaseException):
                self._metrics["errors"] += 1
                logger.error("Handler %s failed for event %s: %s", handler_name(handler), event.event_id, outcome)
                results[handler_name(handler)] = outcome
            else:
                results[handler_name(handler)] = None
        self._metrics["processed"] += 1
        return results

    async def _worker(self) -> None:
        """Background worker that processes events from the queue."""
        logger.info("Event bus worker started")
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._process_event(event)
            except asyncio.TimeoutError:
                continue
            except Exception as exc:
                logger.error("Event bus worker error: %s", exc, exc_info=True)
                self._metrics["errors"] += 1

    async def start(self) -> None:
        """Start the background event processing worker."""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        if self.transport is not None:
            self._consumer_stop = asyncio.Event()
            self._consumer_task = asyncio.create_task(
                self.transport.run(self.dispatch_tracked, self._consumer_stop)
            )
        logger.info("Event bus started (transport=%s)", "redis" if self.transport else "memory")

    async def stop(self) -> None:
        """Stop the background worker and drain the queue."""
        self._running = False
        if self._consumer_task is not None:
            self._consumer_stop.set()
            try:
                await asyncio.wait_for(self._consumer_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._consumer_task.cancel()
            self._consumer_task = None
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        # Process remaining events
        while not self._queue.empty():
            try:
                event = self._queue.get_nowait()
                await self._process_event(event)
            except asyncio.QueueEmpty:
                break

        logger.info("Event bus stopped")

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> bool:
        """Remove a previously registered handler (Phase 9A graceful shutdown).

        Returns True when a handler was removed. Comparison uses equality, not
        identity, because ``obj.method`` produces a *new* bound-method object on
        every attribute access - so a bound method unregisters correctly while
        the object it is bound to still exists. Removing an unknown handler is a
        no-op, which keeps shutdown idempotent.
        """
        removed = False
        handlers = self._handlers.get(event_type)
        if handlers:
            remaining = [h for h in handlers if h != handler]
            if len(remaining) != len(handlers):
                removed = True
                if remaining:
                    self._handlers[event_type] = remaining
                else:
                    self._handlers.pop(event_type, None)
        remaining_all = [h for h in self._all_handlers if h != handler]
        if len(remaining_all) != len(self._all_handlers):
            removed = True
            self._all_handlers = remaining_all
        return removed

    def get_metrics(self) -> Dict[str, int]:
        """Get event processing metrics (plus the transport's, when set)."""
        metrics = dict(self._metrics)
        if self.transport is not None:
            metrics.update({f"transport_{k}": v for k, v in self.transport.metrics.items()})
        return metrics

    def clear_handlers(self) -> None:
        """Remove all handlers - useful for testing."""
        self._handlers.clear()
        self._all_handlers.clear()


# Global singleton - initialized in main.py startup
_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get the global event bus instance."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus(transport=_configured_transport())
    return _event_bus


def _configured_transport():
    """The shared transport selected by EVENT_TRANSPORT (None = in-process)."""
    from app.core.config import get_settings

    cfg = get_settings()
    if cfg.event_transport != "redis":
        return None
    from app.core.redis import redis_client
    from app.services.events.redis_transport import RedisStreamTransport, default_source

    return RedisStreamTransport(
        redis_client,
        source=default_source(cfg.runtime_mode),
        maxlen=cfg.event_stream_maxlen,
        claim_idle_ms=cfg.event_claim_idle_seconds * 1000,
        max_deliveries=cfg.event_max_deliveries,
    )


def set_event_bus(bus: EventBus) -> None:
    """Set the global event bus instance."""
    global _event_bus
    _event_bus = bus
