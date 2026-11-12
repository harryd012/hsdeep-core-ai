"""
Platform metrics over time, from Prometheus, for the native charts on the
superuser-only Platform Observability page (so the HSDEEP app draws them
itself instead of sending people to Grafana).

Design (adapted from the parallel platform-obs-cc attempt, so there is one
implementation, not two):

  * PromQL is a FIXED catalogue chosen by key; the browser never sends a query;
  * only platform metrics with bounded labels are read, so no tenant, user,
    device or address can appear;
  * one ``query_range`` per series, server-side, short timeout; the Prometheus
    URL never reaches the browser and errors are categories, not messages;
  * NaN (0/0 when there is no traffic) is "no value", never 0;
  * counters go through rate(), histograms through histogram_quantile() on
    _bucket, gauges are read as-is; CORS preflights (OPTIONS) are excluded
    from HTTP figures, matching the recording rules.
"""
from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.prometheus_targets import _valid_base

TIMEOUT_SECONDS = 4.0
MAX_LINES = 12

#: Range -> (window seconds, step seconds): about 120 points per line.
RANGES: dict[str, tuple[int, int]] = {
    "1h": (3600, 30),
    "6h": (6 * 3600, 180),
    "24h": (24 * 3600, 720),
    "7d": (7 * 24 * 3600, 5040),
}
DEFAULT_RANGE = "1h"

H = 'method!="OPTIONS"'

#: ``{w}`` = rate window for the chosen range. Metric names: app/core/metrics.py.
SERIES: tuple[dict[str, str], ...] = (
    {"key": "http_request_rate", "label": "API request rate", "unit": "reqps", "group": "http",
     "query": f"sum(rate(hsdeep_http_requests_total{{{H}}}[{{w}}]))"},
    {"key": "http_by_class", "label": "API responses by status class", "unit": "reqps", "group": "http", "by": "class",
     "query": f'sum by (class) (label_replace(rate(hsdeep_http_requests_total{{{H}}}[{{w}}]), "class", "${{1}}xx", "status", "(.).."))'},
    {"key": "http_error_ratio", "label": "API 5xx error rate", "unit": "ratio", "group": "http",
     "query": f'sum(rate(hsdeep_http_requests_total{{{H}, status=~"5.."}}[{{w}}])) / sum(rate(hsdeep_http_requests_total{{{H}}}[{{w}}]))'},
    {"key": "http_latency", "label": "API latency", "unit": "seconds", "group": "http", "by": "quantile",
     "query": " or ".join(
         f'label_replace(histogram_quantile({q}, sum by (le) (rate(hsdeep_http_request_duration_seconds_bucket{{{H}}}[{{w}}]))), "quantile", "p{int(q * 100)}", "", "")'
         for q in (0.5, 0.95, 0.99))},
    {"key": "http_in_progress", "label": "API requests in progress", "unit": "count", "group": "http",
     "query": "sum(hsdeep_http_requests_in_progress)"},
    {"key": "collection_runs", "label": "Collection runs per minute", "unit": "count", "group": "collection", "by": "status",
     "query": "sum by (status) (rate(hsdeep_collection_run_total[{w}])) * 60"},
    {"key": "collection_success_ratio", "label": "Collection success rate", "unit": "ratio", "group": "collection",
     "query": 'sum(rate(hsdeep_collection_run_total{status="success"}[{w}])) / sum(rate(hsdeep_collection_run_total{status=~"success|error"}[{w}]))'},
    {"key": "collection_failures_by_stage", "label": "Collection failures per minute by stage", "unit": "count",
     "group": "collection", "by": "stage",
     "query": "sum by (stage) (rate(hsdeep_collection_failures_total[{w}])) * 60"},
    {"key": "collection_duration_p95", "label": "Collection run time (p95)", "unit": "seconds", "group": "collection",
     "by": "source_type",
     "query": "histogram_quantile(0.95, sum by (source_type, le) (rate(hsdeep_collection_run_duration_seconds_bucket[{w}])))"},
    {"key": "collection_items", "label": "Items saved per minute", "unit": "count", "group": "collection", "by": "entity_type",
     "query": "sum by (entity_type) (rate(hsdeep_collection_items_upserted_total[{w}])) * 60"},
    {"key": "scheduler_tick_age", "label": "Newest scheduler tick age", "unit": "seconds", "group": "scheduler",
     "query": "time() - max(hsdeep_scheduler_last_tick_timestamp_seconds > 0)"},
    {"key": "scheduler_queue_depth", "label": "Sources due per tick", "unit": "count", "group": "scheduler",
     "query": "max(hsdeep_scheduler_queue_depth and on (instance) (hsdeep_scheduler_last_tick_timestamp_seconds > 0))"},
    {"key": "db_pool_saturation", "label": "DB pool used", "unit": "ratio", "group": "scheduler",
     "query": "max(hsdeep_db_pool_saturation_ratio)"},
    {"key": "alerts_opened", "label": "Alerts opened per hour by severity", "unit": "count", "group": "alerts", "by": "severity",
     "query": "sum by (severity) (rate(hsdeep_alert_created_total[{w}])) * 3600"},
    {"key": "platform_alerts_firing", "label": "Platform alert rules firing", "unit": "count", "group": "alerts",
     "query": 'count(ALERTS{alertstate="firing"}) or vector(0)'},
)

# -- Phase 22: platform host, containers, database, cache and GPU history ----------
# Same rules as above. These are requested per group (``groups=``), so the
# Phase 21 page keeps running exactly the queries it ran before. Metric names
# and their exporters: app/services/platform_infrastructure.py.
_FS = 'fstype!~"tmpfs|overlay|squashfs|ramfs|devtmpfs|nsfs|autofs|fuse.*"'
_NIC = 'device!~"lo|veth.*|docker.*|br-.*|virbr.*|cni.*|flannel.*"'
_C = 'job="cadvisor", name!=""'
INFRA_SERIES: tuple[dict[str, str], ...] = (
    {"key": "host_cpu", "label": "Host CPU utilisation", "unit": "percent", "group": "host", "by": "instance",
     "query": '100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{job="node", mode="idle"}[{w}])))'},
    {"key": "host_memory", "label": "Host memory utilisation", "unit": "percent", "group": "host", "by": "instance",
     "query": '100 * (1 - node_memory_MemAvailable_bytes{job="node"} / node_memory_MemTotal_bytes{job="node"})'},
    {"key": "host_disk_used", "label": "Disk used by mount point", "unit": "percent", "group": "host", "by": "mountpoint",
     "query": f'100 * (1 - max by (mountpoint) (node_filesystem_avail_bytes{{job="node", {_FS}}}) / max by (mountpoint) (node_filesystem_size_bytes{{job="node", {_FS}}}))'},
    {"key": "host_disk_io", "label": "Disk throughput", "unit": "bytes_per_second", "group": "host", "by": "direction",
     "query": 'label_replace(sum(rate(node_disk_read_bytes_total{job="node"}[{w}])), "direction", "read", "", "") or '
              'label_replace(sum(rate(node_disk_written_bytes_total{job="node"}[{w}])), "direction", "write", "", "")'},
    {"key": "host_disk_iops", "label": "Disk IOPS", "unit": "count", "group": "host",
     "query": 'sum(rate(node_disk_reads_completed_total{job="node"}[{w}]) + rate(node_disk_writes_completed_total{job="node"}[{w}]))'},
    {"key": "host_network", "label": "Network throughput", "unit": "bytes_per_second", "group": "host", "by": "direction",
     "query": f'label_replace(sum(rate(node_network_receive_bytes_total{{job="node", {_NIC}}}[{{w}}])), "direction", "receive", "", "") or '
              f'label_replace(sum(rate(node_network_transmit_bytes_total{{job="node", {_NIC}}}[{{w}}])), "direction", "transmit", "", "")'},
    {"key": "host_network_errors", "label": "Network errors and drops per second", "unit": "count", "group": "host", "by": "kind",
     "query": f'label_replace(sum(rate(node_network_receive_errs_total{{job="node", {_NIC}}}[{{w}}]) + rate(node_network_transmit_errs_total{{job="node", {_NIC}}}[{{w}}])), "kind", "errors", "", "") or '
              f'label_replace(sum(rate(node_network_receive_drop_total{{job="node", {_NIC}}}[{{w}}]) + rate(node_network_transmit_drop_total{{job="node", {_NIC}}}[{{w}}])), "kind", "dropped", "", "")'},
    {"key": "container_cpu", "label": "Container CPU (cores)", "unit": "count", "group": "containers", "by": "name",
     "query": f"sum by (name) (rate(container_cpu_usage_seconds_total{{{_C}}}[{{w}}]))"},
    {"key": "container_memory", "label": "Container memory", "unit": "bytes", "group": "containers", "by": "name",
     "query": f"sum by (name) (container_memory_working_set_bytes{{{_C}}})"},
    {"key": "container_network", "label": "Container network receive", "unit": "bytes_per_second", "group": "containers", "by": "name",
     "query": f"sum by (name) (rate(container_network_receive_bytes_total{{{_C}}}[{{w}}]))"},
    {"key": "pg_connections", "label": "PostgreSQL connections by state", "unit": "count", "group": "postgres", "by": "state",
     "query": 'sum by (state) (pg_stat_activity_count{job="postgres", state=~"active|idle|idle in transaction"})'},
    {"key": "pg_transactions", "label": "PostgreSQL transactions per second", "unit": "count", "group": "postgres", "by": "kind",
     "query": 'label_replace(sum(rate(pg_stat_database_xact_commit{job="postgres"}[{w}])), "kind", "commit", "", "") or '
              'label_replace(sum(rate(pg_stat_database_xact_rollback{job="postgres"}[{w}])), "kind", "rollback", "", "")'},
    {"key": "pg_query_latency", "label": "Query latency", "unit": "seconds", "group": "postgres", "by": "quantile",
     "query": " or ".join(
         f'label_replace(histogram_quantile({q}, sum by (le) (rate(hsdeep_db_query_duration_seconds_bucket[{{w}}]))), "quantile", "p{int(q * 100)}", "", "")'
         for q in (0.5, 0.95, 0.99))},
    {"key": "pg_slow_queries", "label": "Slow queries per minute (over 1 s)", "unit": "count", "group": "postgres", "by": "operation",
     "query": "sum by (operation) (rate(hsdeep_db_slow_queries_total[{w}])) * 60"},
    {"key": "pg_database_size", "label": "Database size", "unit": "bytes", "group": "postgres", "by": "datname",
     "query": 'pg_database_size_bytes{job="postgres", datname!~"template.*|postgres"}'},
    {"key": "redis_memory", "label": "Redis memory used", "unit": "bytes", "group": "redis",
     "query": 'sum(redis_memory_used_bytes{job="redis"})'},
    {"key": "redis_ops", "label": "Redis operations per second", "unit": "count", "group": "redis",
     "query": 'sum(rate(redis_commands_processed_total{job="redis"}[{w}]))'},
    {"key": "redis_hit_ratio", "label": "Redis cache hit ratio", "unit": "ratio", "group": "redis",
     "query": 'sum(rate(redis_keyspace_hits_total{job="redis"}[{w}])) / (sum(rate(redis_keyspace_hits_total{job="redis"}[{w}])) + sum(rate(redis_keyspace_misses_total{job="redis"}[{w}])))'},
    {"key": "redis_clients", "label": "Redis connected clients", "unit": "count", "group": "redis",
     "query": 'sum(redis_connected_clients{job="redis"})'},
    {"key": "gpu_util", "label": "GPU utilisation", "unit": "percent", "group": "gpu", "by": "gpu",
     "query": 'DCGM_FI_DEV_GPU_UTIL{job="nvidia-gpu"}'},
    {"key": "gpu_vram", "label": "GPU memory used", "unit": "bytes", "group": "gpu", "by": "gpu",
     "query": 'DCGM_FI_DEV_FB_USED{job="nvidia-gpu"} * 1048576'},
    {"key": "gpu_temperature", "label": "GPU temperature (°C)", "unit": "count", "group": "gpu", "by": "gpu",
     "query": 'DCGM_FI_DEV_GPU_TEMP{job="nvidia-gpu"}'},
    {"key": "gpu_power", "label": "GPU power draw (W)", "unit": "count", "group": "gpu", "by": "gpu",
     "query": 'DCGM_FI_DEV_POWER_USAGE{job="nvidia-gpu"}'},
)
#: The groups the Phase 21 page reads (the default), and the Phase 22 ones.
PLATFORM_GROUPS: tuple[str, ...] = ("http", "collection", "scheduler", "alerts")
INFRA_GROUPS: tuple[str, ...] = ("host", "containers", "postgres", "redis", "gpu")
ALL_GROUPS: tuple[str, ...] = PLATFORM_GROUPS + INFRA_GROUPS
SERIES_BY_KEY = {s["key"]: s for s in SERIES + INFRA_SERIES}


def _window(step: int) -> str:
    """Rate window: at least the step and at least four 15 s scrapes."""
    return f"{max(step, 120)}s"


def _lines(result: list[dict[str, Any]], by: str | None) -> list[dict[str, Any]]:
    lines = []
    for row in result[:MAX_LINES]:
        points = []
        for ts, raw in row.get("values") or []:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                points.append([float(ts), value])
        name = (row.get("metric") or {}).get(by, "") if by else ""
        lines.append({"name": name or ("total" if not by else "unlabelled"), "points": points})
    return sorted(lines, key=lambda line: line["name"])


async def fetch_timeseries(prometheus_url: str, range_key: str = DEFAULT_RANGE,
                           client: httpx.AsyncClient | None = None, now: float | None = None,
                           groups: tuple[str, ...] | None = None) -> dict[str, Any]:
    """``groups`` selects catalogue groups; ``None`` is the Phase 21 set."""
    wanted = tuple(g for g in (groups or PLATFORM_GROUPS) if g in ALL_GROUPS) or PLATFORM_GROUPS
    catalogue = tuple(s for s in SERIES + INFRA_SERIES if s["group"] in wanted)
    range_key = range_key if range_key in RANGES else DEFAULT_RANGE
    seconds, step = RANGES[range_key]
    out = {"range": range_key, "step_seconds": step, "checked_at": datetime.now(timezone.utc).isoformat(), "series": []}
    if not prometheus_url:
        return {**out, "configured": False, "reachable": False, "error": None}
    base = _valid_base(prometheus_url)
    if base is None:
        return {**out, "configured": True, "reachable": False, "error": "invalid_url"}

    end = time.time() if now is None else now
    window = _window(step)
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)

    async def one(spec: dict[str, str]) -> dict[str, Any]:
        query = spec["query"].replace("{w}", window)
        row = {"key": spec["key"], "label": spec["label"], "unit": spec["unit"], "group": spec["group"],
               "error": None, "lines": []}
        try:
            resp = await client.get(f"{base}/api/v1/query_range",
                                    params={"query": query, "start": end - seconds, "end": end, "step": step})
            resp.raise_for_status()
            row["lines"] = _lines(resp.json()["data"]["result"], spec.get("by"))
        except httpx.TimeoutException:
            row["error"] = "timeout"
        except httpx.HTTPError:
            row["error"] = "unreachable"
        except (ValueError, KeyError, TypeError):
            row["error"] = "bad_response"
        return row

    try:
        series = await asyncio.gather(*(one(s) for s in catalogue))
    finally:
        if own:
            await client.aclose()
    answered = any(s["error"] is None for s in series)
    errors = {s["error"] for s in series if s["error"]}
    return {**out, "configured": True, "reachable": answered,
            "error": None if answered else (errors.pop() if len(errors) == 1 else "unreachable"),
            "series": list(series)}
