"""Shared event transport over Redis Streams (R2).

Two EventBus instances stand for two processes (API and worker) sharing one
Redis: an event published by one is handled by the other. Uses fakeredis
locally; ``test_against_real_redis`` repeats the core path on a real server
when ``EVENT_TRANSPORT_TEST_REDIS_URL`` is set (CI provides Redis).
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import fakeredis
import pytest

from app.services.events.event_bus import DomainEvent, EventBus, EventType, handler_name
from app.services.events.redis_transport import (
    EnvelopeError,
    RedisStreamTransport,
    StreamNames,
    decode,
    encode,
)

pytestmark = pytest.mark.asyncio
TENANT = uuid.uuid4()


def _names() -> StreamNames:
    tag = uuid.uuid4().hex[:8]
    return StreamNames(stream=f"t:{tag}", dead=f"t:{tag}:dead", group="g", attempts=f"t:{tag}:att",
                       done_prefix=f"t:{tag}:done:")


def _transport(client, names, consumer, **kw):
    # fakeredis serves blocking reads synchronously (it would freeze the event
    # loop), so tests poll without blocking; production blocks for 1 s.
    return RedisStreamTransport(client, source=consumer, names=names, consumer=consumer,
                                block_ms=0, **{"claim_idle_ms": 0, **kw})


def _event(**payload) -> DomainEvent:
    return DomainEvent(event_type=EventType.INCIDENT_CREATED, tenant_id=TENANT,
                       correlation_id="corr-1", payload={"incident_id": str(uuid.uuid4()), **payload})


async def test_envelope_round_trip_and_validation():
    event = _event(extra=uuid.uuid4())
    back = decode(encode(event, source="api@host:1"))
    assert (back.event_id, back.event_type, back.tenant_id, back.correlation_id) == (
        event.event_id, event.event_type, event.tenant_id, "corr-1")
    assert back.payload["extra"] == str(event.payload["extra"])  # non-JSON values travel as strings
    data = json.loads(encode(event, source="api@host:1"))
    assert data["schema_version"] == 1 and data["source"] == "api@host:1"
    for bad in ("not json", json.dumps({**data, "schema_version": 99}),
                json.dumps({**data, "event_type": "no_such_event"}), json.dumps({**data, "tenant_id": "x"})):
        with pytest.raises(EnvelopeError):
            decode(bad)


async def test_api_publishes_worker_consumes():
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    names = _names()
    api = EventBus(transport=_transport(client, names, "api"))
    worker = EventBus(transport=_transport(client, names, "worker"))
    seen: list[str] = []

    @worker.subscribe(EventType.INCIDENT_CREATED)
    async def on_incident(event):
        seen.append(event.payload["incident_id"])

    event = _event()
    await api.publish(event)  # the API process runs no consumers
    assert api._queue.empty()
    handled = await worker.transport.poll_once(worker.dispatch_tracked)
    assert handled == 1 and seen == [event.payload["incident_id"]]
    stats = await worker.transport.stats()
    assert (stats["pending"], stats["dead_letter"], stats["acked"]) == (0, 0, 1)


async def test_failed_handler_is_retried_and_succeeded_handlers_are_not_rerun():
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    names = _names()
    bus = EventBus(transport=_transport(client, names, "worker"))
    calls = {"ok": 0, "flaky": 0}

    @bus.subscribe(EventType.INCIDENT_CREATED)
    async def ok(event):
        calls["ok"] += 1

    @bus.subscribe(EventType.INCIDENT_CREATED)
    async def flaky(event):
        calls["flaky"] += 1
        if calls["flaky"] < 3:
            raise RuntimeError("downstream busy")

    await bus.transport.publish(_event())
    for _ in range(3):
        await bus.transport.poll_once(bus.dispatch_tracked)
    assert calls == {"ok": 1, "flaky": 3}  # 'ok' ran once despite two redeliveries
    stats = await bus.transport.stats()
    assert (stats["pending"], stats["acked"], stats["retried"], stats["dead_letter"]) == (0, 1, 2, 0)


async def test_poison_message_is_dead_lettered_after_max_deliveries():
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    names = _names()
    bus = EventBus(transport=_transport(client, names, "worker", max_deliveries=3))

    @bus.subscribe(EventType.INCIDENT_CREATED)
    async def always_fails(event):
        raise ValueError("bad payload")

    event = _event()
    await bus.transport.publish(event)
    for _ in range(5):
        await bus.transport.poll_once(bus.dispatch_tracked)
    stats = await bus.transport.stats()
    assert (stats["pending"], stats["dead_letter"], stats["dead_lettered"]) == (0, 1, 1)
    [(_id, fields)] = await client.xrange(names.dead)
    assert fields["attempts"] == "3" and "bad payload" in fields["error"]
    assert json.loads(fields["envelope"])["event_id"] == event.event_id


async def test_undecodable_message_goes_straight_to_dead_letter():
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    names = _names()
    bus = EventBus(transport=_transport(client, names, "worker"))
    await client.xadd(names.stream, {"envelope": "{garbage"})
    await bus.transport.poll_once(bus.dispatch_tracked)
    stats = await bus.transport.stats()
    assert (stats["pending"], stats["dead_letter"]) == (0, 1)


async def test_messages_of_a_dead_consumer_are_reclaimed():
    """A worker that read a message and died before acknowledging it: another
    consumer takes it over once it has been idle long enough."""
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    names = _names()
    dead_worker = _transport(client, names, "worker-1")
    live = EventBus(transport=_transport(client, names, "worker-2"))
    seen = []

    @live.subscribe(EventType.INCIDENT_CREATED)
    async def on_incident(event):
        seen.append(event.event_id)

    event = _event()
    await dead_worker.publish(event)
    await dead_worker.ensure_group()
    await client.xreadgroup(names.group, "worker-1", {names.stream: ">"}, count=10)  # read, never acked
    await live.transport.poll_once(live.dispatch_tracked)
    assert seen == [event.event_id]
    assert (await live.transport.stats())["pending"] == 0


async def test_publish_falls_back_locally_only_where_consumers_run():
    class Down:
        async def xadd(self, *a, **k):
            raise ConnectionError("redis down")

    api = EventBus(transport=_transport(Down(), _names(), "api"))
    await api.publish(_event())  # no consumers here: logged as lost, never raised
    assert api.get_metrics()["publish_failed"] == 1 and api._queue.empty()

    worker = EventBus(transport=_transport(Down(), _names(), "worker"))
    worker._running = True  # this process runs consumers
    await worker.publish(_event())
    assert worker._queue.qsize() == 1  # handled in-process instead of lost


async def test_bus_start_stop_runs_the_consumer_loop():
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    names = _names()
    bus = EventBus(transport=_transport(client, names, "worker"))
    got = asyncio.Event()

    @bus.subscribe(EventType.INCIDENT_CREATED)
    async def on_incident(event):
        got.set()

    await bus.start()
    try:
        await bus.publish(_event())
        await asyncio.wait_for(got.wait(), timeout=5)
    finally:
        await bus.stop()
    assert bus._consumer_task is None


async def test_handler_names_are_stable():
    async def h(event):
        pass

    assert handler_name(h).endswith("test_handler_names_are_stable.<locals>.h")


@pytest.mark.skipif(not os.environ.get("EVENT_TRANSPORT_TEST_REDIS_URL"),
                    reason="set EVENT_TRANSPORT_TEST_REDIS_URL (CI does) to run against a real Redis")
async def test_against_real_redis():
    import redis.asyncio as aioredis

    client = aioredis.from_url(os.environ["EVENT_TRANSPORT_TEST_REDIS_URL"], decode_responses=True)
    names = _names()
    try:
        api = EventBus(transport=_transport(client, names, "api"))
        worker = EventBus(transport=_transport(client, names, "worker", max_deliveries=2))
        seen = []

        @worker.subscribe(EventType.INCIDENT_CREATED)
        async def on_incident(event):
            if event.payload.get("fail"):
                raise RuntimeError("poison")
            seen.append(event.event_id)

        good, bad = _event(), _event(fail=True)
        await api.publish(good)
        await api.publish(bad)
        for _ in range(4):
            await worker.transport.poll_once(worker.dispatch_tracked)
        stats = await worker.transport.stats()
        assert seen == [good.event_id]
        assert (stats["pending"], stats["dead_letter"]) == (0, 1)
    finally:
        await client.delete(names.stream, names.dead, names.attempts)
        await client.aclose()
