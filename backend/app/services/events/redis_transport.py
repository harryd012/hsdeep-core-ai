"""
Shared event transport over Redis Streams (R2).

The in-process EventBus cannot carry an event from the API process to a
separately deployed worker. With ``EVENT_TRANSPORT=redis`` the bus publishes to
a Redis stream instead, and every process that runs consumers (runtime modes
``worker`` and ``all``) reads it through one consumer group, so each event is
handled by one consumer.

Delivery is AT LEAST ONCE, never exactly once:

* a message is acknowledged only after every handler for it succeeded;
* a message that is not acknowledged within ``claim_idle_ms`` (handler failed,
  or the consumer died mid-way) is reclaimed by any live consumer;
* after ``max_deliveries`` attempts it moves to the dead-letter stream with the
  last error, and is acknowledged on the main stream;
* a handler that already succeeded for an event is recorded and skipped on
  redelivery (``done`` keys), so a retry re-runs only what failed. Handlers must
  still be idempotent for their own side effects inside one attempt.

Undecodable or unknown-version messages go straight to the dead-letter stream.
Redis is a transport, not the system of record: business state stays in
PostgreSQL, and the stream is trimmed to ``maxlen`` entries.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class EnvelopeError(ValueError):
    """A stream message that cannot be decoded into an event."""


@dataclass(frozen=True)
class StreamNames:
    stream: str = "hsdeep:events"
    dead: str = "hsdeep:events:dead"
    group: str = "hsdeep-consumers"
    attempts: str = "hsdeep:events:attempts"
    done_prefix: str = "hsdeep:events:done:"


def encode(event, *, source: str) -> str:
    """Versioned envelope (JSON). Payload values that are not JSON-native
    (UUID, datetime) are written as strings."""
    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "event_id": event.event_id,
            "event_type": event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type),
            "tenant_id": str(event.tenant_id),
            "source": source,
            "created_at": event.timestamp.isoformat(),
            "correlation_id": event.correlation_id,
            "trace_id": getattr(event, "trace_id", None),
            "payload": event.payload or {},
        },
        default=str,
    )


def decode(raw: str):
    """Envelope -> DomainEvent. Raises EnvelopeError for anything invalid."""
    from app.services.events.event_bus import DomainEvent, EventType

    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise EnvelopeError(f"not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise EnvelopeError("envelope is not an object")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise EnvelopeError(f"unsupported schema_version {data.get('schema_version')!r}")
    try:
        event_type = EventType(data["event_type"])
        tenant_id = uuid.UUID(str(data["tenant_id"]))
        created_at = datetime.fromisoformat(data["created_at"])
        event_id = str(data["event_id"])
    except (KeyError, ValueError, TypeError) as exc:
        raise EnvelopeError(f"invalid envelope field: {exc}") from exc
    payload = data.get("payload") or {}
    if not isinstance(payload, dict):
        raise EnvelopeError("payload is not an object")
    return DomainEvent(
        event_type=event_type, tenant_id=tenant_id, timestamp=created_at, event_id=event_id,
        correlation_id=data.get("correlation_id"), payload=payload,
    )


def default_source(runtime_mode: str) -> str:
    return f"{runtime_mode}@{socket.gethostname()}:{os.getpid()}"


#: Runs the handlers for one event; returns {handler_name: exception or None}.
Dispatch = Callable[[Any, set[str]], Awaitable[dict[str, BaseException | None]]]


class RedisStreamTransport:
    def __init__(
        self,
        client,
        *,
        source: str,
        names: StreamNames = StreamNames(),
        consumer: str | None = None,
        maxlen: int = 100_000,
        batch_size: int = 20,
        block_ms: int = 1000,
        claim_idle_ms: int = 30_000,
        max_deliveries: int = 5,
        done_ttl_seconds: int = 7 * 24 * 3600,
    ):
        self.client = client
        self.source = source
        self.names = names
        self.consumer = consumer or source
        self.maxlen = maxlen
        self.batch_size = batch_size
        self.block_ms = block_ms
        self.claim_idle_ms = claim_idle_ms
        self.max_deliveries = max_deliveries
        self.done_ttl_seconds = done_ttl_seconds
        self.metrics = {"published": 0, "publish_failed": 0, "acked": 0, "retried": 0,
                        "dead_lettered": 0, "skipped_done": 0}
        self._group_ready = False

    # -- producer ------------------------------------------------------------

    async def publish(self, event) -> str:
        """Append the event to the stream. Raises on Redis errors (the caller
        decides the fallback)."""
        msg_id = await self.client.xadd(
            self.names.stream, {"envelope": encode(event, source=self.source)},
            maxlen=self.maxlen, approximate=True,
        )
        self.metrics["published"] += 1
        return msg_id

    # -- consumer ------------------------------------------------------------

    async def ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self.client.xgroup_create(self.names.stream, self.names.group, id="0", mkstream=True)
        except Exception as exc:  # noqa: BLE001 - BUSYGROUP: the group already exists
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    async def poll_once(self, dispatch: Dispatch) -> int:
        """Reclaim stale messages, then read new ones; handle each. Returns
        the number of messages handled. One iteration of the consumer loop."""
        await self.ensure_group()
        messages: list[tuple[str, dict]] = []
        claimed = await self.client.xautoclaim(
            self.names.stream, self.names.group, self.consumer,
            min_idle_time=self.claim_idle_ms, start_id="0-0", count=self.batch_size,
        )
        messages.extend(claimed[1] if claimed else [])
        if not messages:
            # block_ms=0 reads without blocking (run() then pauses between
            # empty polls instead of spinning).
            read = await self.client.xreadgroup(
                self.names.group, self.consumer, {self.names.stream: ">"},
                count=self.batch_size, block=self.block_ms or None,
            )
            for _stream, entries in read or []:
                messages.extend(entries)
        for msg_id, fields in messages:
            await self._handle(msg_id, fields, dispatch)
        return len(messages)

    async def _handle(self, msg_id: str, fields: dict, dispatch: Dispatch) -> None:
        attempts = int(await self.client.hincrby(self.names.attempts, msg_id, 1))
        if attempts > 1:
            self.metrics["retried"] += 1
        raw = (fields or {}).get("envelope")
        try:
            event = decode(raw)
        except EnvelopeError as exc:
            await self._dead_letter(msg_id, raw, f"invalid envelope: {exc}", attempts)
            return

        done_key = f"{self.names.done_prefix}{event.event_id}"
        already = set(await self.client.smembers(done_key) or [])
        results = await dispatch(event, already)
        succeeded = [name for name, err in results.items() if err is None and name not in already]
        if succeeded:
            await self.client.sadd(done_key, *succeeded)
            await self.client.expire(done_key, self.done_ttl_seconds)
        self.metrics["skipped_done"] += len([n for n in results if n in already])
        failures = {name: err for name, err in results.items() if err is not None}
        if not failures:
            await self._ack(msg_id)
            return
        error = "; ".join(f"{name}: {type(err).__name__}: {err}" for name, err in failures.items())[:2000]
        if attempts >= self.max_deliveries:
            await self._dead_letter(msg_id, raw, error, attempts, event=event)
        else:
            logger.warning("event %s (%s) handler failed, attempt %d/%d: %s",
                           event.event_id, event.event_type.value, attempts, self.max_deliveries, error)

    async def _ack(self, msg_id: str, *, count: bool = True) -> None:
        await self.client.xack(self.names.stream, self.names.group, msg_id)
        await self.client.hdel(self.names.attempts, msg_id)
        if count:
            self.metrics["acked"] += 1

    async def _dead_letter(self, msg_id: str, raw, error: str, attempts: int, *, event=None) -> None:
        await self.client.xadd(
            self.names.dead,
            {"envelope": raw if isinstance(raw, str) else json.dumps(raw, default=str),
             "error": error, "attempts": str(attempts), "original_id": msg_id,
             "dead_at": datetime.now(timezone.utc).isoformat()},
            maxlen=self.maxlen, approximate=True,
        )
        await self._ack(msg_id, count=False)
        self.metrics["dead_lettered"] += 1
        logger.error("event %s dead-lettered after %d attempt(s): %s",
                     getattr(event, "event_id", msg_id), attempts, error)

    async def stats(self) -> dict:
        """Queue depth for health/metrics: stream length, pending (delivered,
        not acknowledged), lag (not yet delivered) and dead-letter count."""
        await self.ensure_group()
        groups = await self.client.xinfo_groups(self.names.stream)
        group = next((g for g in groups if g.get("name") == self.names.group), {}) or {}
        return {
            "stream_length": await self.client.xlen(self.names.stream),
            "pending": int(group.get("pending") or 0),
            "lag": group.get("lag"),
            "dead_letter": await self.client.xlen(self.names.dead),
            **self.metrics,
        }

    async def run(self, dispatch: Dispatch, stop: asyncio.Event) -> None:
        """Consumer loop until ``stop`` is set. Redis errors back off and retry:
        an outage pauses consumption, it never ends it."""
        backoff = 1.0
        logger.info("event transport consumer %s started on %s", self.consumer, self.names.stream)
        while not stop.is_set():
            try:
                handled = await self.poll_once(dispatch)
                backoff = 1.0
                if not handled and not self.block_ms:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=0.05)
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep consuming after Redis outages
                logger.error("event transport consumer error (retry in %.0fs): %s", backoff, exc)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)
        logger.info("event transport consumer %s stopped", self.consumer)
