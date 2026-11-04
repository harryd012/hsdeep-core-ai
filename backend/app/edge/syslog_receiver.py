"""
Syslog receiver (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P7 slice 1).

A small, separately run process for the customer's network - never part of
the central API process. It listens for syslog over UDP, parses each datagram
(`app.services.logs.syslog`) and forwards batches to the platform's existing
collector ingest API (`POST /api/v1/ingest/events`) with a collector
credential. The platform derives the tenant from that credential and links a
log to a device when its sending host matches exactly one device.

Run (from `backend/`):

    HSDEEP_API_URL=https://platform.example.com \\
    HSDEEP_COLLECTOR_CREDENTIAL=<credential> \\
    python -m app.edge.syslog_receiver

Environment:
    HSDEEP_API_URL                 platform base URL (required)
    HSDEEP_COLLECTOR_CREDENTIAL    collector credential (required; never logged)
    SYSLOG_BIND                    listen address (default 0.0.0.0)
    SYSLOG_PORT                    UDP port (default 5514; 514 needs privileges)
    SYSLOG_BATCH                   events per request (default 200, max 500)
    SYSLOG_FLUSH_SECONDS           longest wait before sending a partial batch (default 2)
    SYSLOG_QUEUE_MAX               buffered events while the platform is unreachable
                                   (default 20000; the oldest are dropped beyond it, counted)

Delivery is at-least-once within the buffer: a failed batch is retried with
backoff and kept in order; events are only dropped when the buffer is full,
and every drop is counted in the periodic status line.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import os
import signal
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from app.services.logs.syslog import parse_syslog

logger = logging.getLogger("hsdeep.syslog_receiver")
CREDENTIAL_HEADER = "X-HSDEEP-Collector-Credential"
MAX_BATCH = 500


@dataclass
class Settings:
    api_url: str
    credential: str
    bind: str = "0.0.0.0"
    port: int = 5514
    batch: int = 200
    flush_seconds: float = 2.0
    queue_max: int = 20000

    def __repr__(self) -> str:  # the credential never reaches a log line
        return f"Settings(api_url={self.api_url!r}, bind={self.bind!r}, port={self.port}, batch={self.batch})"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        api_url = env.get("HSDEEP_API_URL", "").rstrip("/")
        credential = env.get("HSDEEP_COLLECTOR_CREDENTIAL", "")
        if not api_url or not credential:
            raise SystemExit("HSDEEP_API_URL and HSDEEP_COLLECTOR_CREDENTIAL must be set")
        if not api_url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise SystemExit("HSDEEP_API_URL must use https (http only for localhost)")
        return cls(
            api_url=api_url,
            credential=credential,
            bind=env.get("SYSLOG_BIND", "0.0.0.0"),
            port=int(env.get("SYSLOG_PORT", "5514")),
            batch=max(1, min(MAX_BATCH, int(env.get("SYSLOG_BATCH", "200")))),
            flush_seconds=max(0.2, float(env.get("SYSLOG_FLUSH_SECONDS", "2"))),
            queue_max=max(100, int(env.get("SYSLOG_QUEUE_MAX", "20000"))),
        )


@dataclass
class Buffer:
    """Bounded FIFO of parsed events; the oldest are dropped (and counted) when full."""

    limit: int
    items: collections.deque = field(default_factory=collections.deque)
    received: int = 0
    dropped: int = 0
    sent: int = 0
    rejected: int = 0

    def add(self, event: dict) -> None:
        self.received += 1
        if len(self.items) >= self.limit:
            self.items.popleft()
            self.dropped += 1
        self.items.append(event)

    def take(self, n: int) -> list[dict]:
        return [self.items.popleft() for _ in range(min(n, len(self.items)))]

    def put_back(self, events: list[dict]) -> None:
        """A failed batch returns to the front, in order, within the limit."""
        for event in reversed(events):
            if len(self.items) >= self.limit:
                self.dropped += 1
                continue
            self.items.appendleft(event)


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, buffer: Buffer, wake: asyncio.Event, batch: int) -> None:
        self.buffer, self.wake, self.batch = buffer, wake, batch

    def datagram_received(self, data: bytes, addr) -> None:
        self.buffer.add(parse_syslog(data, sender=addr[0], received_at=datetime.now(timezone.utc)))
        if len(self.buffer.items) >= self.batch:
            self.wake.set()


async def send_batch(client: httpx.AsyncClient, settings: Settings, events: list[dict]) -> tuple[bool, int]:
    """(delivered, rejected count). Not delivered on a network error, 429 or 5xx (retry);
    delivered on 2xx (individual rejections counted) and on other 4xx (not retryable)."""
    try:
        response = await client.post(
            f"{settings.api_url}/api/v1/ingest/events",
            json={"events": events},
            headers={CREDENTIAL_HEADER: settings.credential},
        )
    except httpx.HTTPError as exc:
        logger.warning("ingest unreachable: %s", type(exc).__name__)
        return False, 0
    if response.status_code == 429 or response.status_code >= 500:
        logger.warning("ingest returned %s; retrying", response.status_code)
        return False, 0
    if response.status_code >= 400:
        logger.error("ingest refused the batch with %s; dropping %d events", response.status_code, len(events))
        return True, len(events)
    return True, int(response.json().get("rejected", 0))


async def run(settings: Settings, stop: asyncio.Event) -> Buffer:
    buffer = Buffer(limit=settings.queue_max)
    wake = asyncio.Event()
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _Protocol(buffer, wake, settings.batch), local_addr=(settings.bind, settings.port)
    )
    logger.info("listening for syslog on udp %s:%d, forwarding to %s", settings.bind, settings.port, settings.api_url)
    backoff = 1.0
    last_status = loop.time()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(wake.wait(), timeout=settings.flush_seconds)
                except asyncio.TimeoutError:
                    pass
                wake.clear()
                while buffer.items and not stop.is_set():
                    events = buffer.take(settings.batch)
                    delivered, rejected = await send_batch(client, settings, events)
                    if not delivered:
                        buffer.put_back(events)
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 60.0)
                        break
                    backoff = 1.0
                    buffer.sent += len(events) - rejected
                    buffer.rejected += rejected
                if loop.time() - last_status >= 60:
                    logger.info("received=%d sent=%d rejected=%d dropped=%d buffered=%d",
                                buffer.received, buffer.sent, buffer.rejected, buffer.dropped, len(buffer.items))
                    last_status = loop.time()
    finally:
        transport.close()
    return buffer


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    stop = asyncio.Event()

    async def _main() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows: Ctrl+C raises KeyboardInterrupt instead
                pass
        await run(settings, stop)

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
