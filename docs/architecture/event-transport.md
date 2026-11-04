# Shared event transport (R2)

Status: implemented on `feat/event-transport-r2` (R2.1-R2.3). Release
tracker: `docs/release/IMPLEMENTATION_STATUS.md`.

## Problem

`EventBus` (`app/services/events/event_bus.py`) kept events in an in-process
queue. In the Render deployment the API (`RUNTIME_MODE=api`) and the worker
(`RUNTIME_MODE=worker`) are separate services, so an event published by the
API (for example `INCIDENT_CREATED` from a monitoring sync) was queued in a
process that runs no consumers: it was never handled and the queue only grew.

## Design

`EVENT_TRANSPORT=redis` gives the bus a `RedisStreamTransport`
(`app/services/events/redis_transport.py`). The `publish` / `subscribe`
interface is unchanged, so no publisher or handler changed.

- **Publish**: `XADD hsdeep:events` with the envelope, trimmed to
  `EVENT_STREAM_MAXLEN` (approximate). Every process publishes, including the
  API.
- **Consume**: processes that start the bus (runtime modes `worker` and `all`)
  read through one consumer group, `hsdeep-consumers`, so each event is handled
  by one consumer. Consumer name: `<mode>@<host>:<pid>`.
- **Envelope** (JSON, `schema_version` 1): `event_id`, `event_type`,
  `tenant_id`, `source`, `created_at`, `correlation_id`, `trace_id`, `payload`.
  Non-JSON payload values (UUID, datetime) travel as strings; publishers already
  send ids as strings.

### Delivery: at least once

- A message is acknowledged only after every handler for it succeeded.
- Unacknowledged messages idle for `EVENT_CLAIM_IDLE_SECONDS` (handler failed,
  or the consumer died mid-way) are reclaimed by any live consumer
  (`XAUTOCLAIM`), so a worker restart does not lose acknowledged work and does
  not strand unacknowledged work.
- Deliveries are counted per message (`hsdeep:events:attempts`). After
  `EVENT_MAX_DELIVERIES` the message moves to `hsdeep:events:dead` with the last
  error and is acknowledged on the main stream.
- Undecodable or unknown-version messages are dead-lettered at once.
- **Idempotency per handler**: when a handler succeeds, its name is recorded in
  `hsdeep:events:done:<event_id>` (7-day TTL). A redelivery skips handlers that
  already succeeded, so a retry re-runs only what failed. A handler that fails
  part-way can still repeat its own side effects on retry, so handlers must be
  idempotent within one attempt. Some already guard their business state in
  PostgreSQL (alert/incident deduplication, the ticket pipeline race guard, the
  device-action claim); each handler on the stream still needs that review.

Exactly-once is not claimed.

### Failure behaviour

- **Redis down at publish**: if this process runs consumers, the event is
  handled in-process (logged). Otherwise it is logged at error level and counted
  (`publish_failed`) and is lost; `publish()` never raised to callers before
  and still does not. Closing this gap needs a transactional outbox (below).
- **Redis down while consuming**: the consumer loop backs off (1 s doubling to
  30 s) and resumes; nothing is acknowledged meanwhile.
- **Backpressure**: reads are batched (`batch_size` 20) and handled before the
  next read; the stream is length-capped.

### Responsibilities of Redis

Transport only for events (plus rate limiting and caching elsewhere). Business
records stay in PostgreSQL; a trimmed or flushed stream loses only events in
flight, never records.

## Configuration

| Setting | Default | Notes |
| --- | --- | --- |
| `EVENT_TRANSPORT` | `memory` | `redis` in production; production refuses `api`/`worker`/`scheduler` roles with `memory` |
| `EVENT_STREAM_MAXLEN` | 100000 | approximate cap of the main and dead-letter streams |
| `EVENT_CLAIM_IDLE_SECONDS` | 30 | idle time before another consumer takes a message over |
| `EVENT_MAX_DELIVERIES` | 5 | attempts before dead-lettering |

`render.yaml` sets `EVENT_TRANSPORT=redis` for both backend services.

## Observability

`EventBus.get_metrics()` includes the transport counters (`published`,
`publish_failed`, `acked`, `retried`, `dead_lettered`, `skipped_done`), and
`RedisStreamTransport.stats()` reports stream length, pending, lag and
dead-letter count.

## Not done yet

- Transactional outbox so a publish during a Redis outage is not lost.
- Dead-letter inspection and replay (API/CLI).
- Exposing `stats()` on `/health` and Prometheus `/metrics`.
- Worker concurrency limits beyond batch-at-a-time; tenant-level fairness.
