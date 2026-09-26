"""
Prometheus metrics for the Monitoring Collector Framework.

Tracks collection pipeline health, alert creation rates, and scheduler
status. Exposed at the /metrics endpoint for scraping by Prometheus or any
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


def observe_scheduler_tick(queue_depth: int) -> None:
    """Record scheduler tick metrics."""
    scheduler_queue_depth.set(queue_depth)
    scheduler_last_tick_timestamp.set(datetime.now(timezone.utc).timestamp())