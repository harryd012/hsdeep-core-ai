"""
A JSON summary of THIS process's platform metrics, for the superuser-only
Platform Observability page.

Read straight from the in-process Prometheus registry, so it needs no
Prometheus server and adds no storage. Its limits are stated in the payload
rather than hidden:

  * scope is this one process (``runtime_mode`` says which role). In a split
    deployment the worker and scheduler counters live in their own processes;
    in ``all`` mode (development) everything is here;
  * values are cumulative since the process started (``since``), not a rate
    over a window. Rates and windows are Prometheus/Grafana's job;
  * percentiles are estimated from histogram buckets exactly like PromQL's
    ``histogram_quantile()``: linear interpolation inside the bucket, lower
    bound 0 for the first bucket, error bounded by the bucket width;
  * anything with no observations is ``None`` (shown as "Not measured"),
    never 0, so an idle process is not reported as healthy.

Labels here are the same bounded platform labels as /metrics: no tenant,
user, device, IP or credential ever appears.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from prometheus_client import REGISTRY
from prometheus_client.registry import CollectorRegistry

from app.core import metrics as m

Samples = list[tuple[str, dict[str, str], float]]


def _samples(registry: CollectorRegistry, family: str) -> Samples:
    out: Samples = []
    for metric in registry.collect():
        if metric.name == family:
            out.extend((s.name, dict(s.labels), float(s.value)) for s in metric.samples)
    return out


def _sum(samples: Samples, name: str, **match: str) -> float:
    return sum(v for n, labels, v in samples if n == name and all(labels.get(k) == val for k, val in match.items()))


def histogram_quantile(q: float, buckets: Iterable[tuple[float, float]]) -> float | None:
    """PromQL ``histogram_quantile`` over cumulative ``(le, count)`` buckets.
    ``None`` when there are no observations or no +Inf bucket."""
    ordered = sorted(buckets, key=lambda b: b[0])
    if not ordered or not math.isinf(ordered[-1][0]):
        return None
    # Bucket counts must not decrease (PromQL repairs small float drift too).
    fixed: list[tuple[float, float]] = []
    running = 0.0
    for le, count in ordered:
        running = max(running, count)
        fixed.append((le, running))
    total = fixed[-1][1]
    if total <= 0:
        return None
    rank = q * total
    prev_le, prev_count = 0.0, 0.0
    for i, (le, count) in enumerate(fixed):
        if count >= rank:
            if math.isinf(le):
                # Quantile in the +Inf bucket: PromQL returns the last finite bound.
                return fixed[i - 1][0] if i > 0 else None
            if i == 0 and le <= 0:
                return le
            in_bucket = count - prev_count
            if in_bucket <= 0:
                return le
            return prev_le + (le - prev_le) * (rank - prev_count) / in_bucket
        prev_le, prev_count = le, count
    return None


def _buckets(samples: Samples, name: str, **match: str) -> list[tuple[float, float]]:
    by_le: dict[float, float] = defaultdict(float)
    for n, labels, v in samples:
        if n == name and all(labels.get(k) == val for k, val in match.items()):
            by_le[float(labels["le"])] += v
    return list(by_le.items())


def _ratio(part: float, whole: float) -> float | None:
    return part / whole if whole > 0 else None


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def _http(registry: CollectorRegistry) -> dict[str, Any]:
    req = _samples(registry, "hsdeep_http_requests")
    dur = _samples(registry, "hsdeep_http_request_duration_seconds")
    progress = _samples(registry, "hsdeep_http_requests_in_progress")

    total = _sum(req, "hsdeep_http_requests_total")
    classes = {f"{c}xx": 0.0 for c in "2345"}
    per_route: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {"requests": 0.0, "errors": 0.0})
    for n, labels, v in req:
        if n != "hsdeep_http_requests_total":
            continue
        cls = f"{labels['status'][:1]}xx"
        if cls in classes:
            classes[cls] += v
        row = per_route[(labels["method"], labels["route"])]
        row["requests"] += v
        if labels["status"].startswith("5"):
            row["errors"] += v

    duration_sum: dict[tuple[str, str], float] = defaultdict(float)
    duration_count: dict[tuple[str, str], float] = defaultdict(float)
    for n, labels, v in dur:
        key = (labels.get("method", ""), labels.get("route", ""))
        if n.endswith("_sum"):
            duration_sum[key] += v
        elif n.endswith("_count"):
            duration_count[key] += v

    routes = []
    for (method, route), row in per_route.items():
        count = duration_count.get((method, route), 0.0)
        mean = duration_sum[(method, route)] / count if count else None
        p95 = histogram_quantile(
            0.95, _buckets(dur, "hsdeep_http_request_duration_seconds_bucket", method=method, route=route)
        )
        routes.append({
            "method": method, "route": route, "requests": int(row["requests"]), "errors": int(row["errors"]),
            "mean_seconds": _round(mean), "p95_seconds": _round(p95),
        })

    all_buckets = _buckets(dur, "hsdeep_http_request_duration_seconds_bucket")
    errors = classes["5xx"]
    return {
        "requests_total": int(total),
        "errors_total": int(errors),
        "error_ratio": _round(_ratio(errors, total), 6),
        "in_progress": int(_sum(progress, "hsdeep_http_requests_in_progress")),
        "p50_seconds": _round(histogram_quantile(0.50, all_buckets)),
        "p95_seconds": _round(histogram_quantile(0.95, all_buckets)),
        "p99_seconds": _round(histogram_quantile(0.99, all_buckets)),
        "status_classes": {k: int(v) for k, v in classes.items()},
        "top_routes": sorted(routes, key=lambda r: r["requests"], reverse=True)[:8],
        # Slowest by p95, only routes with enough traffic for a percentile to mean anything.
        "slowest_routes": sorted(
            (r for r in routes if r["requests"] >= 5 and r["p95_seconds"] is not None),
            key=lambda r: r["p95_seconds"], reverse=True,
        )[:5],
    }


def _collection(registry: CollectorRegistry) -> dict[str, Any]:
    runs = _samples(registry, "hsdeep_collection_run")
    dur = _samples(registry, "hsdeep_collection_run_duration_seconds")
    skipped = _samples(registry, "hsdeep_collection_skipped")
    failures = _samples(registry, "hsdeep_collection_failures")
    phases = _samples(registry, "hsdeep_collector_phase_duration_seconds")
    items = _samples(registry, "hsdeep_collection_items_upserted")

    success = _sum(runs, "hsdeep_collection_run_total", status="success")
    error = _sum(runs, "hsdeep_collection_run_total", status="error")
    by_type: dict[str, dict[str, float]] = defaultdict(lambda: {"success": 0.0, "error": 0.0})
    for n, labels, v in runs:
        if n == "hsdeep_collection_run_total" and labels["status"] in ("success", "error"):
            by_type[labels["source_type"]][labels["status"]] += v

    source_types = []
    for source_type, row in sorted(by_type.items()):
        count = _sum(dur, "hsdeep_collection_run_duration_seconds_count", source_type=source_type)
        total = _sum(dur, "hsdeep_collection_run_duration_seconds_sum", source_type=source_type)
        source_types.append({
            "source_type": source_type, "success": int(row["success"]), "error": int(row["error"]),
            "success_ratio": _round(_ratio(row["success"], row["success"] + row["error"])),
            "mean_seconds": _round(total / count if count else None),
            "p95_seconds": _round(histogram_quantile(
                0.95, _buckets(dur, "hsdeep_collection_run_duration_seconds_bucket", source_type=source_type))),
        })

    stages: dict[str, int] = defaultdict(int)
    for n, labels, v in failures:
        if n == "hsdeep_collection_failures_total":
            stages[labels["stage"]] += int(v)

    phase_mean: dict[str, float | None] = {}
    for phase in ("authenticate", "collect", "normalize", "persist"):
        count = _sum(phases, "hsdeep_collector_phase_duration_seconds_count", phase=phase)
        total = _sum(phases, "hsdeep_collector_phase_duration_seconds_sum", phase=phase)
        phase_mean[phase] = _round(total / count if count else None)

    upserted: dict[str, int] = defaultdict(int)
    for n, labels, v in items:
        if n == "hsdeep_collection_items_upserted_total":
            upserted[labels["entity_type"]] += int(v)

    return {
        "runs_total": int(success + error),
        "success_total": int(success),
        "error_total": int(error),
        "skipped_total": int(_sum(skipped, "hsdeep_collection_skipped_total")),
        "success_ratio": _round(_ratio(success, success + error)),
        "p95_seconds": _round(histogram_quantile(0.95, _buckets(dur, "hsdeep_collection_run_duration_seconds_bucket"))),
        "by_source_type": source_types,
        "failures_by_stage": dict(stages),
        "phase_mean_seconds": phase_mean,
        "items_upserted": dict(upserted),
    }


def _scheduler(registry: CollectorRegistry, now: float) -> dict[str, Any]:
    last = _sum(_samples(registry, "hsdeep_scheduler_last_tick_timestamp_seconds"),
                "hsdeep_scheduler_last_tick_timestamp_seconds")
    ticks = _samples(registry, "hsdeep_scheduler_tick_duration_seconds")
    tick_count = _sum(ticks, "hsdeep_scheduler_tick_duration_seconds_count")
    tick_sum = _sum(ticks, "hsdeep_scheduler_tick_duration_seconds_sum")
    ticked = last > 0
    return {
        "last_tick_at": datetime.fromtimestamp(last, timezone.utc).isoformat() if ticked else None,
        "last_tick_age_seconds": _round(max(now - last, 0.0), 1) if ticked else None,
        # The gauge reads 0 before the first tick; that is "not measured", not an empty queue.
        "queue_depth": int(_sum(_samples(registry, "hsdeep_scheduler_queue_depth"), "hsdeep_scheduler_queue_depth"))
        if ticked else None,
        "ticks_total": int(tick_count),
        "tick_failures_total": int(_sum(_samples(registry, "hsdeep_scheduler_tick_failures"),
                                        "hsdeep_scheduler_tick_failures_total")),
        "tick_mean_seconds": _round(tick_sum / tick_count if tick_count else None),
    }


def _alerts(registry: CollectorRegistry) -> dict[str, Any]:
    samples = _samples(registry, "hsdeep_alert_created")
    by_severity: dict[str, int] = defaultdict(int)
    by_source: dict[str, int] = defaultdict(int)
    for n, labels, v in samples:
        if n == "hsdeep_alert_created_total":
            by_severity[labels["severity"]] += int(v)
            by_source[labels["source"]] += int(v)
    return {"created_total": sum(by_severity.values()), "by_severity": dict(by_severity), "by_source": dict(by_source)}


def build_summary(
    registry: CollectorRegistry = REGISTRY,
    *,
    version: str,
    runtime_mode: str,
    now: float | None = None,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    started = m.PROCESS_STARTED_AT
    return {
        "generated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "scope": {
            "kind": "process",
            "runtime_mode": runtime_mode,
            "version": version,
            "since": datetime.fromtimestamp(started, timezone.utc).isoformat(),
            "uptime_seconds": round(max(now - started, 0.0), 1),
            "note": "Cumulative since this process started; this process only. "
                    "Rates over time windows and other processes are in Prometheus/Grafana.",
        },
        "http": _http(registry),
        "collection": _collection(registry),
        "scheduler": _scheduler(registry, now),
        "alerts": _alerts(registry),
    }
