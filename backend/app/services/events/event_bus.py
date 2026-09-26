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


class EventBus:
    """In-memory async event bus with fan-out to multiple handlers.
    
    Designed for single-process deployments. For multi-process/multi-node,
    replace with Redis pub/sub or similar - the handler interface stays the same.
    """
    
    def __init__(self):
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
        logger.info("Event bus started")
    
    async def stop(self) -> None:
        """Stop the background worker and drain the queue."""
        self._running = False
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
        """Get event processing metrics."""
        return dict(self._metrics)
    
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
        _event_bus = EventBus()
    return _event_bus


def set_event_bus(bus: EventBus) -> None:
    """Set the global event bus instance."""
    global _event_bus
    _event_bus = bus
