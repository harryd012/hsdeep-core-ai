"""
Prometheus platform metrics for HSDEEP CORE AI (the platform itself, never
customer infrastructure, which stays in PostgreSQL).

Tracks HTTP API traffic, collection pipeline health, alert creation rates and
scheduler status. Exposed at the /metrics endpoint for scraping by Prometheus or any
OpenMetrics-compatible collector.

Security note: metrics are intentionally global counters, not tenant-scoped.
They measure platform health (how many collections succeeded/failed, how many
alerts were created) rather than exposing tenant data.
"""
from __future__ import annotations

import functools
import time
from datetime import datetime, timezone

from prometheus_client import Counter, Gauge, Histogram

# When this process loaded its metrics (Unix seconds). The standard
# process_start_time_seconds is Linux-only, so the summary API uses this.
PROCESS_STARTED_AT = time.time()

# -- Collection Pipeline -----------------------------------------------------

# Duration of each collection run in seconds, bucketed for SLO tracking.
# Buckets: 1s, 2.5s, 5s, 10s, 25s, 60s, 120s +Inf
collection_run_duration = Histogram(
    name="hsdeep_collection_run_duration_seconds",
    documentation="Duration of a single collection run in seconds",
    labelnames=["source_type", "status"],
    buckets=(1.0, 2.5, 5.0, 10.0, 25.0, 60.0, 120.0, float("inf")),
)

# Total collection runs by source type and final status.
collection_run_total = Counter(
    name="hsdeep_collection_run_total",
    documentation="Total number of collection runs",
    labelnames=["source_type", "status"],
)

# Number of devices, sensors, interfaces, metrics, events, and alerts
# upserted/recorded per run.
collection_items_upserted = Counter(
    name="hsdeep_collection_items_upserted_total",
    documentation="Total items upserted per collection run by entity type",
    labelnames=["entity_type", "source_type"],
)

# -- Alert Pipeline ----------------------------------------------------------

# Total alerts created via evaluate_sensor (threshold-derived).
alert_created_total = Counter(
    name="hsdeep_alert_created_total",
    documentation="Total alerts created by source and severity",
    labelnames=["source", "severity"],
)

# -- Collector Execution -----------------------------------------------------

# Total collector executions (authenticate + collect + normalize calls).
collector_execution_total = Counter(
    name="hsdeep_collector_execution_total",
    documentation="Total collector executions by source type and phase",
    labelnames=["source_type", "phase"],
)

# Duration of each collector phase (authenticate, collect, normalize).
collector_phase_duration = Histogram(
    name="hsdeep_collector_phase_duration_seconds",
    documentation="Duration of a collector phase in seconds",
    labelnames=["source_type", "phase"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 25.0, 60.0, float("inf")),
)

# -- Scheduler ---------------------------------------------------------------

# Number of due sources found in the current tick.
scheduler_queue_depth = Gauge(
    name="hsdeep_scheduler_queue_depth",
    documentation="Number of due sources found in the current scheduler tick",
)

# Timestamp (Unix epoch seconds) of the last successful scheduler tick.
scheduler_last_tick_timestamp = Gauge(
    name="hsdeep_scheduler_last_tick_timestamp_seconds",
    documentation="Unix timestamp of the last scheduler tick",
)

# Failed collection runs by the stage that failed: authenticate (credentials
# rejected), collect (vendor call), normalize (payload shape) or persist
# (saving / alert evaluation).
collection_failures_total = Counter(
    name="hsdeep_collection_failures_total",
    documentation="Failed collection runs by source type and failing stage",
    labelnames=["source_type", "stage"],
)

# Wall time of one scheduler tick that held the lock (all due sources).
scheduler_tick_duration = Histogram(
    name="hsdeep_scheduler_tick_duration_seconds",
    documentation="Duration of a scheduler tick in seconds",
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, float("inf")),
)

# Scheduler ticks that raised before finishing.
scheduler_tick_failures_total = Counter(
    name="hsdeep_scheduler_tick_failures_total",
    documentation="Scheduler ticks that raised an exception",
)

# Number of sources skipped due to concurrent-run guard.
collection_skipped_total = Counter(
    name="hsdeep_collection_skipped_total",
    documentation="Total collection runs skipped (concurrent run in progress)",
    labelnames=["source_type"],
)


def observe_collection_run(
    source_type: str,
    status: str,
    duration_seconds: float,
    devices_upserted: int = 0,
    interfaces_upserted: int = 0,
    sensors_upserted: int = 0,
    metrics_recorded: int = 0,
    events_recorded: int = 0,
    alerts_upserted: int = 0,
) -> None:
    """Record all metrics for a completed collection run."""
    collection_run_duration.labels(source_type=source_type, status=status).observe(duration_seconds)
    collection_run_total.labels(source_type=source_type, status=status).inc()

    if devices_upserted:
        collection_items_upserted.labels(entity_type="device", source_type=source_type).inc(devices_upserted)
    if interfaces_upserted:
        collection_items_upserted.labels(entity_type="interface", source_type=source_type).inc(interfaces_upserted)
    if sensors_upserted:
        collection_items_upserted.labels(entity_type="sensor", source_type=source_type).inc(sensors_upserted)
    if metrics_recorded:
        collection_items_upserted.labels(entity_type="metric", source_type=source_type).inc(metrics_recorded)
    if events_recorded:
        collection_items_upserted.labels(entity_type="event", source_type=source_type).inc(events_recorded)
    if alerts_upserted:
        collection_items_upserted.labels(entity_type="alert", source_type=source_type).inc(alerts_upserted)


def observe_collector_phase(source_type: str, phase: str, duration_seconds: float) -> None:
    """Record duration and execution count for a collector phase."""
    collector_execution_total.labels(source_type=source_type, phase=phase).inc()
    collector_phase_duration.labels(source_type=source_type, phase=phase).observe(duration_seconds)


def observe_alert_created(source: str, severity: str) -> None:
    """Record an alert creation event."""
    alert_created_total.labels(source=source, severity=severity).inc()


def observe_collection_skipped(source_type: str) -> None:
    """Record a skipped collection run (concurrent run in progress)."""
    collection_skipped_total.labels(source_type=source_type).inc()


def observe_collection_failure(source_type: str, stage: str) -> None:
    """Record a failed collection run and the stage it failed in."""
    collection_failures_total.labels(source_type=source_type, stage=stage).inc()


def observe_scheduler_tick(queue_depth: int, duration_seconds: float | None = None) -> None:
    """Record a completed scheduler tick."""
    scheduler_queue_depth.set(queue_depth)
    scheduler_last_tick_timestamp.set(datetime.now(timezone.utc).timestamp())
    if duration_seconds is not None:
        scheduler_tick_duration.observe(duration_seconds)


def observe_scheduler_tick_failure() -> None:
    """Record a scheduler tick that raised."""
    scheduler_tick_failures_total.inc()

# -- HTTP API ----------------------------------------------------------------
#
# Labels are bounded on purpose: ``route`` is the route TEMPLATE
# (``/api/incidents/{incident_id}``), never the raw path; anything that matches
# no route is ``__unmatched__`` so scanners cannot mint new series; ``method``
# outside the standard verbs is ``OTHER``. No tenant, user, IP or credential
# ever becomes a label. Recorded by ``app.core.http_metrics``.

UNMATCHED_ROUTE = "__unmatched__"

http_requests_total = Counter(
    name="hsdeep_http_requests_total",
    documentation="HTTP requests handled, by method, route template and status code",
    labelnames=["method", "route", "status"],
)

http_request_duration = Histogram(
    name="hsdeep_http_request_duration_seconds",
    documentation="HTTP request duration in seconds, by method and route template",
    labelnames=["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf")),
)

http_requests_in_progress = Gauge(
    name="hsdeep_http_requests_in_progress",
    documentation="HTTP requests currently being handled, by method",
    labelnames=["method"],
)

http_errors_total = Counter(
    name="hsdeep_http_errors_total",
    documentation="HTTP requests that ended in a 5xx or an unhandled exception",
    labelnames=["method", "route", "status"],
)

# -- Process identity ----------------------------------------------------------

build_info = Gauge(
    name="hsdeep_build_info",
    documentation="Always 1; labels identify the app version and runtime role of this process",
    labelnames=["version", "runtime_mode"],
)


def observe_http_request(method: str, route: str, status: int, duration_seconds: float) -> None:
    """Record one finished HTTP request."""
    code = str(status)
    http_requests_total.labels(method=method, route=route, status=code).inc()
    http_request_duration.labels(method=method, route=route).observe(duration_seconds)
    if status >= 500:
        http_errors_total.labels(method=method, route=route, status=code).inc()


def set_build_info(version: str, runtime_mode: str) -> None:
    """Publish this process's version and runtime role (api/worker/scheduler/all)."""
    build_info.labels(version=version, runtime_mode=runtime_mode).set(1)


# -- Dependencies ----------------------------------------------------------------
#
# Set by the existing /health check (which readiness probes call every few
# seconds), so no extra polling is added. ``dependency`` is database / redis.

dependency_up = Gauge(
    name="hsdeep_dependency_up",
    documentation="1 when the dependency answered the last health check, else 0",
    labelnames=["dependency"],
)

dependency_last_check = Gauge(
    name="hsdeep_dependency_last_check_timestamp_seconds",
    documentation="Unix timestamp of the last health check of the dependency",
    labelnames=["dependency"],
)


def observe_dependency(dependency: str, up: bool) -> None:
    """Record one health-check result for a dependency."""
    dependency_up.labels(dependency=dependency).set(1 if up else 0)
    dependency_last_check.labels(dependency=dependency).set(time.time())


class _DbPoolCollector:
    """SQLAlchemy connection-pool state, read at scrape time from the pool
    object itself (no query, no polling). Exposes nothing for pools without
    QueuePool counters (e.g. NullPool in tests)."""

    def collect(self):
        from prometheus_client.core import GaugeMetricFamily

        try:
            from app.core.db import engine

            pool = engine.pool
            size = int(pool.size())
            checked_out = int(pool.checkedout())
            overflow = max(int(pool.overflow()), 0)
            max_overflow = int(getattr(pool, "_max_overflow", 0))
        except Exception:  # noqa: BLE001 - a scrape must never fail on pool introspection
            return
        capacity = size + max(max_overflow, 0)
        for name, doc, value in (
            ("hsdeep_db_pool_size", "Configured connection pool size", size),
            ("hsdeep_db_pool_max_overflow", "Connections allowed above the pool size", max_overflow),
            ("hsdeep_db_pool_checked_out", "Connections currently checked out of the pool", checked_out),
            ("hsdeep_db_pool_overflow", "Overflow connections currently open", overflow),
            ("hsdeep_db_pool_saturation_ratio", "Checked-out connections / (size + max_overflow)",
             checked_out / capacity if capacity > 0 else 0.0),
        ):
            yield GaugeMetricFamily(name, doc, value=value)


_db_pool_collector = _DbPoolCollector()
try:
    from prometheus_client import REGISTRY as _REGISTRY

    _REGISTRY.register(_db_pool_collector)
except ValueError:  # already registered (module re-executed): keep the first
    pass


# -- Database queries (Phase 22) -----------------------------------------------------
#
# Timed around every SQL statement by SQLAlchemy engine events
# (``install_db_query_metrics``). ``operation`` is the statement's first
# keyword, folded into a fixed set, so the label is bounded; the statement
# text, its parameters and the tenant never become labels.

DB_SLOW_QUERY_SECONDS = 1.0
_DB_OPERATIONS = frozenset({"select", "insert", "update", "delete"})

db_query_duration = Histogram(
    name="hsdeep_db_query_duration_seconds",
    documentation="Duration of a SQL statement in seconds, by operation",
    labelnames=["operation"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, float("inf")),
)

db_slow_queries_total = Counter(
    name="hsdeep_db_slow_queries_total",
    documentation="SQL statements slower than DB_SLOW_QUERY_SECONDS (1 s), by operation",
    labelnames=["operation"],
)

db_query_errors_total = Counter(
    name="hsdeep_db_query_errors_total",
    documentation="SQL statements that raised a database error, by operation",
    labelnames=["operation"],
)


def db_operation(statement: str) -> str:
    """select / insert / update / delete, else ``other`` (bounded label)."""
    head = (statement or "").lstrip()[:16].split(None, 1)
    word = head[0].lower() if head else ""
    if word == "with":
        return "select"
    return word if word in _DB_OPERATIONS else "other"


def observe_db_query(operation: str, duration_seconds: float) -> None:
    db_query_duration.labels(operation=operation).observe(duration_seconds)
    if duration_seconds >= DB_SLOW_QUERY_SECONDS:
        db_slow_queries_total.labels(operation=operation).inc()


_db_metrics_installed = False


def install_db_query_metrics(sync_engine) -> None:
    """Attach the timing listeners to an engine (idempotent)."""
    global _db_metrics_installed
    if _db_metrics_installed:
        return
    from sqlalchemy import event

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        conn.info["hsdeep_query_started"] = time.perf_counter()

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        started = conn.info.pop("hsdeep_query_started", None)
        if started is not None:
            observe_db_query(db_operation(statement), time.perf_counter() - started)

    @event.listens_for(sync_engine, "handle_error")
    def _error(exception_context):  # noqa: ANN001
        conn = exception_context.connection
        if conn is not None:
            conn.info.pop("hsdeep_query_started", None)
        db_query_errors_total.labels(operation=db_operation(exception_context.statement or "")).inc()

    # A counter child with no increments has no series. Creating the children
    # here makes "no slow queries" a measured 0 instead of an absent metric.
    for operation in sorted(_DB_OPERATIONS | {"other"}):
        db_slow_queries_total.labels(operation=operation)
        db_query_errors_total.labels(operation=operation)
    _db_metrics_installed = True
