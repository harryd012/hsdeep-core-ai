"""Phase 9A: explicit, bounded, idempotent RCA/EventBus graceful shutdown."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import FastAPI

from app.services.events.event_bus import (
    DomainEvent,
    EventBus,
    EventType,
    get_event_bus,
)
from app.services.rca_lifecycle import start_rca_lifecycle
from app.services.rca_trigger import RCAIncidentCreatedBridge


class _Recorder:
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def handle(self, event: DomainEvent) -> None:
        self.events.append(event)


class _HangingBus(EventBus):
    """A bus whose stop() never returns, to prove shutdown is bounded."""

    async def stop(self) -> None:  # noqa: D102 - deliberate override
        await asyncio.sleep(30)


def _event() -> DomainEvent:
    return DomainEvent(
        event_type=EventType.AI_RCA_STARTED,
        tenant_id=uuid.uuid4(),
        payload={"incident_id": str(uuid.uuid4())},
    )


# ---------------------------------------------------------------------------
# EventBus.unsubscribe
# ---------------------------------------------------------------------------

async def test_unsubscribe_stops_delivery_to_that_handler_only():
    bus = EventBus()
    keep, drop = _Recorder(), _Recorder()
    bus.subscribe(EventType.AI_RCA_STARTED)(keep.handle)
    bus.subscribe(EventType.AI_RCA_STARTED)(drop.handle)
    await bus.start()

    await bus.publish(_event())
    await asyncio.sleep(0.25)
    assert len(keep.events) == 1 and len(drop.events) == 1

    assert bus.unsubscribe(EventType.AI_RCA_STARTED, drop.handle) is True

    await bus.publish(_event())
    await asyncio.sleep(0.25)
    assert len(drop.events) == 1, "unsubscribed handler must not receive events"
    assert len(keep.events) == 2, "other handlers must be unaffected"

    await bus.stop()


def test_unsubscribe_unknown_handler_is_a_noop():
    bus = EventBus()
    assert bus.unsubscribe(EventType.AI_RCA_STARTED, _Recorder().handle) is False


# ---------------------------------------------------------------------------
# RCALifecycle.stop
# ---------------------------------------------------------------------------

async def test_lifecycle_stop_unregisters_and_is_idempotent():
    bus = EventBus()
    life = start_rca_lifecycle(event_bus=bus)
    await life.start()
    assert life.unregister() is True  # was registered by start()
    life.register()  # re-register so stop() has something to remove

    await life.stop()
    assert life.unregister() is False, "stop() must have removed the handler"

    await life.stop()  # second call must not raise


async def test_stop_without_start_is_safe():
    life = start_rca_lifecycle(event_bus=EventBus())
    await life.stop()
    await life.stop()


async def test_stop_drains_events_already_queued():
    """Queued events are drained (not silently dropped) at shutdown."""
    bus = EventBus()
    recorder = _Recorder()
    bus.subscribe(EventType.AI_RCA_STARTED)(recorder.handle)
    life = start_rca_lifecycle(event_bus=bus)

    await bus.publish(_event())  # queued, worker not started
    assert recorder.events == []

    await life.stop()
    assert len(recorder.events) == 1, "queued event must be drained on stop"


async def test_stop_is_bounded_when_bus_hangs():
    life = start_rca_lifecycle(event_bus=_HangingBus())
    started = asyncio.get_running_loop().time()
    await life.stop(drain_timeout=0.2)
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 3.0, f"shutdown must respect its drain budget (took {elapsed:.1f}s)"


# ---------------------------------------------------------------------------
# RCAIncidentCreatedBridge.unregister
# ---------------------------------------------------------------------------

class _FakeTrigger:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def trigger_for_incident(self, incident_id, tenant_id, **kwargs):
        self.calls.append({"incident_id": incident_id, "tenant_id": tenant_id})
        return True


async def test_bridge_unregister_stops_rca_triggering():
    bus = EventBus()
    trigger = _FakeTrigger()
    bridge = RCAIncidentCreatedBridge(trigger=trigger, event_bus=bus)
    bridge.register()
    await bus.start()

    def _incident_event() -> DomainEvent:
        return DomainEvent(
            event_type=EventType.INCIDENT_CREATED,
            tenant_id=uuid.uuid4(),
            payload={"incident_id": str(uuid.uuid4())},
        )

    await bus.publish(_incident_event())
    await asyncio.sleep(0.25)
    assert len(trigger.calls) == 1

    assert bridge.unregister() is True
    assert bridge.unregister() is False  # idempotent

    await bus.publish(_incident_event())
    await asyncio.sleep(0.25)
    assert len(trigger.calls) == 1, "unregistered bridge must not trigger RCA"

    await bus.stop()


# ---------------------------------------------------------------------------
# app.main shutdown helper (safe when RCA was never wired)
# ---------------------------------------------------------------------------

async def test_main_shutdown_helper_safe_when_never_wired():
    import app.main as main

    bare = FastAPI()
    await main._stop_rca_consumers(bare)  # no state at all
    await main._stop_rca_consumers(bare)  # twice
    assert bare.state.rca_lifecycle is None
    assert bare.state.rca_trigger_bridge is None


async def test_main_shutdown_helper_releases_global_bus():
    """Even with no lifecycle object, a started bus worker is stopped."""
    import app.main as main

    bus = get_event_bus()
    await bus.start()
    assert bus._running is True

    await main._stop_rca_consumers(FastAPI())
    assert bus._running is False
