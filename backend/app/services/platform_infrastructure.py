"""
Host, container, database, cache and GPU telemetry for the HSDEEP platform
host, for the superuser-only Platform Observability page (Phase 22).

SCOPE: the machine(s) that RUN HSDEEP. Customer servers, containers and
network devices are collected by the collector framework into PostgreSQL and
shown in the tenant-scoped pages; they never appear here, and nothing here is
written to a tenant.

Every number is an instant PromQL query from a FIXED catalogue (``SIGNALS``)
against the platform Prometheus, read server-side. The browser never sends a
query. Each metric is produced by an exporter named in ``SOURCES``; the page
shows which one, when it was last scraped and why a state was assigned.

Telemetry states (``STATES``), per source and per signal:

  HEALTHY        measured, inside the documented thresholds
  DEGRADED       source only: some of its scrape targets are down
  WARNING        measured, past the warning threshold
  CRITICAL       measured, past the critical threshold
  UNKNOWN        Prometheus itself could not be asked (no evidence either way)
  STALE          the exporter answered within ``STALE_LOOKBACK`` but its last
                 successful scrape is older than ``FRESHNESS_SECONDS``
  NO DATA        a scrape target exists but returned no value for the signal
  NOT AVAILABLE  no scrape target (exporter not deployed), or the hardware /
                 driver does not provide the measurement

A missing value is never 0 and never healthy: NaN and empty results become
NO DATA. A signal without a threshold is reported as measured, with no
health state at all.
"""
from __future__ import annotations

import asyncio
import math
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.platform_health import _Prom, _valid

TIMEOUT_SECONDS = 4.0
#: A source is STALE when its newest successful scrape is older than this
#: (six missed 15 s scrapes).
FRESHNESS_SECONDS = 90
#: How far back a last successful scrape is searched for.
STALE_LOOKBACK = "6h"
MAX_ROWS = 50

STATES = ("HEALTHY", "DEGRADED", "WARNING", "CRITICAL", "UNKNOWN", "STALE", "NO DATA", "NOT AVAILABLE")
_SEVERITY = {"CRITICAL": 4, "WARNING": 3, "DEGRADED": 2, "HEALTHY": 1}

#: Where each group of metrics comes from. ``verified`` records whether the
#: metric names were checked against a running exporter when this catalogue
#: was written (deployment/observability/README.md, "Verification").
SOURCES: dict[str, dict[str, Any]] = {
    "host": {"name": "Platform host", "job": "node", "exporter": "node_exporter", "verified": True,
             "needs": "node_exporter on the host that runs HSDEEP (compose service node-exporter)"},
    "containers": {"name": "Containers", "job": "cadvisor", "exporter": "cAdvisor", "verified": False,
                   "needs": "cAdvisor with access to the container runtime (compose service cadvisor)"},
    "postgres": {"name": "PostgreSQL", "job": "postgres", "exporter": "postgres_exporter", "verified": True,
                 "needs": "postgres_exporter pointed at the HSDEEP database (compose service postgres-exporter)"},
    "redis": {"name": "Redis", "job": "redis", "exporter": "redis_exporter", "verified": True,
              "needs": "redis_exporter pointed at the HSDEEP Redis (compose service redis-exporter)"},
    "gpu_nvidia": {"name": "NVIDIA GPU", "job": "nvidia-gpu", "exporter": "NVIDIA DCGM exporter", "verified": False,
                   "needs": "an NVIDIA GPU, the NVIDIA container toolkit and dcgm-exporter (compose profile gpu)"},
}

#: Hardware with no supported telemetry path in this deployment. Listed so the
#: page says NOT AVAILABLE with the reason instead of omitting it.
UNSUPPORTED: tuple[dict[str, str], ...] = (
    {"key": "gpu_intel", "name": "Intel GPU",
     "reason": "No Prometheus exporter for Intel GPUs is deployed. intel_gpu_top needs privileged access to the "
               "i915 / xe performance counters and is not part of this stack, so utilisation and memory are not collected."},
    {"key": "npu_intel", "name": "Intel NPU",
     "reason": "The Intel NPU driver exposes no stable utilisation or memory telemetry interface that an exporter "
               "can scrape, so nothing is collected."},
)

_FS = 'fstype!~"tmpfs|overlay|squashfs|ramfs|devtmpfs|nsfs|autofs|fuse.*"'
_NIC = 'device!~"lo|veth.*|docker.*|br-.*|virbr.*|cni.*|flannel.*"'
_C = 'job="cadvisor", name!=""'


def _s(key: str, source: str, label: str, unit: str, query: str, by: tuple[str, ...] = (), warn: float | None = None,
       crit: float | None = None, lower_is_worse: bool = False, note: str | None = None) -> dict[str, Any]:
    return {"key": key, "source": source, "label": label, "unit": unit, "query": query, "by": by,
            "warn": warn, "crit": crit, "lower_is_worse": lower_is_worse, "note": note}


#: The fixed catalogue. Thresholds are the documented defaults shown on the page.
SIGNALS: tuple[dict[str, Any], ...] = (
    # -- Platform host (node_exporter) ----------------------------------------------
    _s("host_cpu_util", "host", "CPU utilisation", "percent",
       '100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{job="node", mode="idle"}[5m])))',
       ("instance",), warn=85, crit=95),
    _s("host_cpu_cores", "host", "CPU cores", "count",
       'count by (instance) (node_cpu_seconds_total{job="node", mode="idle"})', ("instance",)),
    _s("host_cpu_frequency", "host", "CPU frequency", "hertz",
       'avg by (instance) (node_cpu_scaling_frequency_hertz{job="node"})', ("instance",),
       note="Only where the kernel exposes cpufreq (not in most virtual machines)."),
    _s("host_memory_total", "host", "Memory total", "bytes", 'node_memory_MemTotal_bytes{job="node"}', ("instance",)),
    _s("host_memory_used", "host", "Memory used", "bytes",
       'node_memory_MemTotal_bytes{job="node"} - node_memory_MemAvailable_bytes{job="node"}', ("instance",)),
    _s("host_memory_available", "host", "Memory available", "bytes",
       'node_memory_MemAvailable_bytes{job="node"}', ("instance",)),
    _s("host_memory_util", "host", "Memory utilisation", "percent",
       '100 * (1 - node_memory_MemAvailable_bytes{job="node"} / node_memory_MemTotal_bytes{job="node"})',
       ("instance",), warn=85, crit=95),
    _s("host_disk_size", "host", "Disk capacity", "bytes",
       f'node_filesystem_size_bytes{{job="node", {_FS}}}', ("instance", "mountpoint")),
    _s("host_disk_free", "host", "Disk free", "bytes",
       f'node_filesystem_avail_bytes{{job="node", {_FS}}}', ("instance", "mountpoint")),
    _s("host_disk_used_pct", "host", "Disk used", "percent",
       f'100 * (1 - node_filesystem_avail_bytes{{job="node", {_FS}}} / node_filesystem_size_bytes{{job="node", {_FS}}})',
       ("instance", "mountpoint"), warn=80, crit=90),
    _s("host_disk_read", "host", "Disk read throughput", "bytes_per_second",
       'sum by (instance) (rate(node_disk_read_bytes_total{job="node"}[5m]))', ("instance",)),
    _s("host_disk_write", "host", "Disk write throughput", "bytes_per_second",
       'sum by (instance) (rate(node_disk_written_bytes_total{job="node"}[5m]))', ("instance",)),
    _s("host_disk_iops", "host", "Disk IOPS", "per_second",
       'sum by (instance) (rate(node_disk_reads_completed_total{job="node"}[5m]) + rate(node_disk_writes_completed_total{job="node"}[5m]))',
       ("instance",)),
    _s("host_disk_latency", "host", "Disk latency per operation", "seconds",
       'sum by (instance) (rate(node_disk_read_time_seconds_total{job="node"}[5m]) + rate(node_disk_write_time_seconds_total{job="node"}[5m]))'
       ' / sum by (instance) (rate(node_disk_reads_completed_total{job="node"}[5m]) + rate(node_disk_writes_completed_total{job="node"}[5m]))',
       ("instance",), note="No value while the disks are idle (nothing to divide by)."),
    _s("host_net_rx", "host", "Network receive", "bytes_per_second",
       f'sum by (instance) (rate(node_network_receive_bytes_total{{job="node", {_NIC}}}[5m]))', ("instance",)),
    _s("host_net_tx", "host", "Network transmit", "bytes_per_second",
       f'sum by (instance) (rate(node_network_transmit_bytes_total{{job="node", {_NIC}}}[5m]))', ("instance",)),
    _s("host_net_errors", "host", "Network errors", "per_second",
       f'sum by (instance) (rate(node_network_receive_errs_total{{job="node", {_NIC}}}[5m]) + rate(node_network_transmit_errs_total{{job="node", {_NIC}}}[5m]))',
       ("instance",), warn=0.01, crit=1),
    _s("host_net_drops", "host", "Network dropped packets", "per_second",
       f'sum by (instance) (rate(node_network_receive_drop_total{{job="node", {_NIC}}}[5m]) + rate(node_network_transmit_drop_total{{job="node", {_NIC}}}[5m]))',
       ("instance",), warn=0.01, crit=1),
    _s("host_uptime", "host", "System uptime", "seconds",
       'node_time_seconds{job="node"} - node_boot_time_seconds{job="node"}', ("instance",)),
    _s("host_processes", "host", "Processes", "count", 'node_processes_pids{job="node"}', ("instance",),
       note="Needs node_exporter's processes collector (enabled in the compose file)."),
    # -- Containers (cAdvisor) ------------------------------------------------------------
    _s("container_cpu", "containers", "Container CPU", "cores",
       f"sum by (name) (rate(container_cpu_usage_seconds_total{{{_C}}}[5m]))", ("name",)),
    _s("container_memory", "containers", "Container memory", "bytes",
       f"sum by (name) (container_memory_working_set_bytes{{{_C}}})", ("name",)),
    _s("container_memory_limit", "containers", "Container memory limit", "bytes",
       f"sum by (name) (container_spec_memory_limit_bytes{{{_C}}} > 0)", ("name",),
       note="Containers without a memory limit have no row."),
    _s("container_cpu_limit", "containers", "Container CPU limit", "cores",
       f"sum by (name) (container_spec_cpu_quota{{{_C}}} / container_spec_cpu_period{{{_C}}})", ("name",),
       note="Containers without a CPU quota have no row."),
    _s("container_net_rx", "containers", "Container network receive", "bytes_per_second",
       f"sum by (name) (rate(container_network_receive_bytes_total{{{_C}}}[5m]))", ("name",)),
    _s("container_net_tx", "containers", "Container network transmit", "bytes_per_second",
       f"sum by (name) (rate(container_network_transmit_bytes_total{{{_C}}}[5m]))", ("name",)),
    _s("container_restarts", "containers", "Container restarts (1 h)", "count",
       f"sum by (name) (changes(container_start_time_seconds{{{_C}}}[1h]))", ("name",), warn=1, crit=5,
       note="Counted from changes of the container start time; cAdvisor has no restart counter."),
    _s("container_last_seen", "containers", "Container last seen", "seconds",
       f"time() - max by (name) (container_last_seen{{{_C}}})", ("name",), warn=60, crit=300,
       note="cAdvisor reports running containers only: a stopped container ages here, then disappears."),
    # -- PostgreSQL (postgres_exporter + the application's own pool and query timing) ---
    _s("pg_active_connections", "postgres", "Active connections", "count",
       'sum(pg_stat_activity_count{job="postgres", state="active"})'),
    _s("pg_idle_connections", "postgres", "Idle connections", "count",
       'sum(pg_stat_activity_count{job="postgres", state="idle"})'),
    _s("pg_connection_util", "postgres", "Connections used (of max_connections)", "ratio",
       'sum(pg_stat_activity_count{job="postgres"}) / max(pg_settings_max_connections{job="postgres"})',
       warn=0.8, crit=0.9),
    _s("pg_pool_util", "postgres", "Application connection pool used", "ratio",
       "max(hsdeep_db_pool_saturation_ratio)", warn=0.7, crit=0.9),
    _s("pg_query_p95", "postgres", "Query latency (p95)", "seconds",
       "histogram_quantile(0.95, sum by (le) (rate(hsdeep_db_query_duration_seconds_bucket[5m])))", warn=0.25, crit=1,
       note="Timed in the application around every SQL statement."),
    _s("pg_slow_queries", "postgres", "Slow queries per minute (over 1 s)", "per_minute",
       "sum(rate(hsdeep_db_slow_queries_total[5m])) * 60", warn=1, crit=10),
    _s("pg_transactions", "postgres", "Transactions per second", "per_second",
       'sum(rate(pg_stat_database_xact_commit{job="postgres"}[5m])) + sum(rate(pg_stat_database_xact_rollback{job="postgres"}[5m]))'),
    _s("pg_database_size", "postgres", "Database size", "bytes",
       'pg_database_size_bytes{job="postgres", datname!~"template.*|postgres"}', ("datname",)),
    _s("pg_deadlocks", "postgres", "Deadlocks (1 h)", "count",
       'sum(increase(pg_stat_database_deadlocks{job="postgres"}[1h]))', warn=1, crit=5),
    # -- Redis (redis_exporter) ----------------------------------------------------------
    _s("redis_memory", "redis", "Memory used", "bytes", 'sum(redis_memory_used_bytes{job="redis"})'),
    _s("redis_memory_util", "redis", "Memory used (of maxmemory)", "ratio",
       'sum(redis_memory_used_bytes{job="redis"}) / sum(redis_memory_max_bytes{job="redis"} > 0)', warn=0.8, crit=0.95,
       note="No value when Redis has no maxmemory limit."),
    _s("redis_clients", "redis", "Connected clients", "count", 'sum(redis_connected_clients{job="redis"})'),
    _s("redis_ops", "redis", "Operations per second", "per_second",
       'sum(rate(redis_commands_processed_total{job="redis"}[5m]))'),
    _s("redis_hit_ratio", "redis", "Cache hit ratio", "ratio",
       'sum(rate(redis_keyspace_hits_total{job="redis"}[5m])) / (sum(rate(redis_keyspace_hits_total{job="redis"}[5m])) + sum(rate(redis_keyspace_misses_total{job="redis"}[5m])))',
       note="No value while no key is being read."),
    _s("redis_evicted", "redis", "Evicted keys (1 h)", "count",
       'sum(increase(redis_evicted_keys_total{job="redis"}[1h]))', warn=1, crit=1000),
    _s("redis_rejected_connections", "redis", "Rejected connections (1 h)", "count",
       'sum(increase(redis_rejected_connections_total{job="redis"}[1h]))', warn=1, crit=10),
    # -- NVIDIA GPU (DCGM exporter) -------------------------------------------------------
    _s("gpu_util", "gpu_nvidia", "GPU utilisation", "percent",
       'DCGM_FI_DEV_GPU_UTIL{job="nvidia-gpu"}', ("gpu", "modelName"), warn=90, crit=99),
    _s("gpu_vram_used", "gpu_nvidia", "VRAM used", "bytes",
       'DCGM_FI_DEV_FB_USED{job="nvidia-gpu"} * 1048576', ("gpu", "modelName")),
    _s("gpu_vram_free", "gpu_nvidia", "VRAM available", "bytes",
       'DCGM_FI_DEV_FB_FREE{job="nvidia-gpu"} * 1048576', ("gpu", "modelName")),
    _s("gpu_memory_util", "gpu_nvidia", "GPU memory utilisation", "percent",
       '100 * DCGM_FI_DEV_FB_USED{job="nvidia-gpu"} / (DCGM_FI_DEV_FB_USED{job="nvidia-gpu"} + DCGM_FI_DEV_FB_FREE{job="nvidia-gpu"})',
       ("gpu", "modelName"), warn=90, crit=98),
    _s("gpu_temperature", "gpu_nvidia", "GPU temperature", "celsius",
       'DCGM_FI_DEV_GPU_TEMP{job="nvidia-gpu"}', ("gpu", "modelName"), warn=80, crit=90),
    _s("gpu_power", "gpu_nvidia", "Power draw", "watts",
       'DCGM_FI_DEV_POWER_USAGE{job="nvidia-gpu"}', ("gpu", "modelName")),
)
SIGNALS_BY_KEY = {s["key"]: s for s in SIGNALS}

#: Every metric the catalogue reads that the backend does not emit itself, with
#: the exporter that provides it. Tests check the catalogue against this list
#: and against app/core/metrics.py.
EXPORTER_METRICS: dict[str, tuple[str, ...]] = {
    "node_exporter": (
        "node_cpu_seconds_total", "node_cpu_scaling_frequency_hertz", "node_memory_MemTotal_bytes",
        "node_memory_MemAvailable_bytes", "node_filesystem_size_bytes", "node_filesystem_avail_bytes",
        "node_disk_read_bytes_total", "node_disk_written_bytes_total", "node_disk_reads_completed_total",
        "node_disk_writes_completed_total", "node_disk_read_time_seconds_total", "node_disk_write_time_seconds_total",
        "node_network_receive_bytes_total", "node_network_transmit_bytes_total", "node_network_receive_errs_total",
        "node_network_transmit_errs_total", "node_network_receive_drop_total", "node_network_transmit_drop_total",
        "node_time_seconds", "node_boot_time_seconds", "node_processes_pids",
    ),
    "cAdvisor": (
        "container_cpu_usage_seconds_total", "container_memory_working_set_bytes", "container_spec_memory_limit_bytes",
        "container_spec_cpu_quota", "container_spec_cpu_period", "container_network_receive_bytes_total",
        "container_network_transmit_bytes_total", "container_start_time_seconds", "container_last_seen",
    ),
    "postgres_exporter": (
        "pg_stat_activity_count", "pg_settings_max_connections", "pg_stat_database_xact_commit",
        "pg_stat_database_xact_rollback", "pg_database_size_bytes", "pg_stat_database_deadlocks",
    ),
    "redis_exporter": (
        "redis_memory_used_bytes", "redis_memory_max_bytes", "redis_connected_clients", "redis_commands_processed_total",
        "redis_keyspace_hits_total", "redis_keyspace_misses_total", "redis_evicted_keys_total",
        "redis_rejected_connections_total",
    ),
    "NVIDIA DCGM exporter": (
        "DCGM_FI_DEV_GPU_UTIL", "DCGM_FI_DEV_FB_USED", "DCGM_FI_DEV_FB_FREE", "DCGM_FI_DEV_GPU_TEMP",
        "DCGM_FI_DEV_POWER_USAGE",
    ),
}

_INSTANCE = re.compile(r"^[A-Za-z0-9._:\-\[\]]{1,128}$")


def metric_names(query: str) -> set[str]:
    """Metric names a PromQL expression reads (identifiers that are not
    functions, keywords, label names or label values)."""
    stripped = re.sub(r'"[^"]*"', '""', query)
    stripped = re.sub(r"\{[^}]*\}", "", stripped)
    stripped = re.sub(r"\b(by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", "", stripped)
    stripped = re.sub(r"\[[^\]]*\]", "", stripped)
    words = set(re.findall(r"[A-Za-z_:][A-Za-z0-9_:]*", stripped))
    return {w for w in words if "_" in w and not w.endswith("_over_time") and w not in _PROMQL_WORDS}


_PROMQL_WORDS = frozenset({"histogram_quantile", "label_replace", "label_join", "count_values", "group_left", "group_right",
                           "day_of_week", "day_of_month", "days_in_month", "sort_desc"})


def classify(spec: dict[str, Any], value: float) -> str | None:
    """HEALTHY / WARNING / CRITICAL for a measured value, or ``None`` when the
    signal has no threshold (measured, deliberately not judged)."""
    warn, crit = spec["warn"], spec["crit"]
    if warn is None and crit is None:
        return None
    if spec["lower_is_worse"]:
        if crit is not None and value <= crit:
            return "CRITICAL"
        if warn is not None and value <= warn:
            return "WARNING"
        return "HEALTHY"
    if crit is not None and value >= crit:
        return "CRITICAL"
    if warn is not None and value >= warn:
        return "WARNING"
    return "HEALTHY"


def _worst(states: list[str | None]) -> str | None:
    judged = [s for s in states if s in _SEVERITY]
    return max(judged, key=_SEVERITY.__getitem__) if judged else None


async def _source_state(prom: _Prom, key: str) -> dict[str, Any]:
    spec = SOURCES[key]
    job = spec["job"]
    base = {"key": key, "name": spec["name"], "exporter": spec["exporter"], "job": job, "names_verified": spec["verified"],
            "freshness_window_seconds": FRESHNESS_SECONDS, "targets_up": 0, "targets_total": 0,
            "last_success_age_seconds": None}
    up = await prom.vector(f'up{{job="{job}"}}')
    if not up:
        return {**base, "state": "NOT AVAILABLE",
                "reason": f"Prometheus has no scrape target for job \"{job}\". Needs {spec['needs']}."}
    total, ok = len(up), sum(1 for _, v in up if v == 1)
    base.update(targets_up=ok, targets_total=total)
    age = await prom.scalar(
        f'time() - max(max_over_time((timestamp(up{{job="{job}"}}) and (up{{job="{job}"}} == 1))[{STALE_LOOKBACK}:1m]))'
    )
    base["last_success_age_seconds"] = age
    if ok == 0:
        if age is None:
            return {**base, "state": "NO DATA",
                    "reason": f"{total} scrape target(s) configured, none has answered in the last {STALE_LOOKBACK}."}
        return {**base, "state": "STALE",
                "reason": f"No target is answering; the last successful scrape was {age:.0f} s ago."}
    if age is not None and age > FRESHNESS_SECONDS:
        return {**base, "state": "STALE",
                "reason": f"Last successful scrape {age:.0f} s ago (freshness window {FRESHNESS_SECONDS} s)."}
    if ok < total:
        return {**base, "state": "DEGRADED", "reason": f"{ok} of {total} scrape targets answering."}
    return {**base, "state": "HEALTHY", "reason": f"{ok} of {total} scrape targets answering."}


def _signal(spec: dict[str, Any], source: dict[str, Any], result: Any, instance: str | None) -> dict[str, Any]:
    out = {"key": spec["key"], "source": spec["source"], "label": spec["label"], "unit": spec["unit"],
           "thresholds": {"warning": spec["warn"], "critical": spec["crit"], "lower_is_worse": spec["lower_is_worse"]}
           if (spec["warn"] is not None or spec["crit"] is not None) else None,
           "note": spec["note"], "rows": [], "state": None, "reason": None}
    if source["state"] in ("NOT AVAILABLE",):
        return {**out, "state": "NOT AVAILABLE", "reason": source["reason"]}
    if isinstance(result, BaseException):
        return {**out, "state": "UNKNOWN", "reason": "Prometheus did not answer this query."}
    rows = []
    for labels, value in result:
        if not math.isfinite(value):
            continue
        if instance and "instance" in spec["by"] and labels.get("instance") != instance:
            continue
        rows.append({"labels": {k: labels.get(k, "") for k in spec["by"]}, "value": value,
                     "state": classify(spec, value)})
    rows.sort(key=lambda r: tuple(r["labels"].values()))
    out["rows"] = rows[:MAX_ROWS]
    out["truncated"] = len(rows) > MAX_ROWS
    if not rows:
        if source["state"] in ("STALE", "NO DATA"):
            return {**out, "state": source["state"], "reason": source["reason"]}
        return {**out, "state": "NO DATA",
                "reason": spec["note"] or f"{source['exporter']} is being scraped but returned no value for this signal."}
    out["state"] = _worst([r["state"] for r in rows])
    out["reason"] = (
        "Measured; no threshold is defined for this signal, so it is not judged." if out["state"] is None
        else f"Worst of {len(rows)} measured value(s) against the documented thresholds."
    )
    return out


async def fetch_infrastructure(prometheus_url: str, instance: str | None = None,
                               client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Current platform-host telemetry with an explicit state for every source
    and signal. ``instance`` narrows per-host rows to one scrape target."""
    checked_at = datetime.now(timezone.utc).isoformat()
    if instance is not None and not _INSTANCE.match(instance):
        instance = None
    base_out: dict[str, Any] = {
        "checked_at": checked_at, "scope": "platform_host", "instance": instance, "states": list(STATES),
        "freshness_window_seconds": FRESHNESS_SECONDS, "unsupported": list(UNSUPPORTED),
        "sources": [], "signals": [], "instances": [],
    }
    if not prometheus_url:
        return {**base_out, "configured": False, "reachable": False, "error": None}
    base = _valid(prometheus_url)
    if base is None:
        return {**base_out, "configured": True, "reachable": False, "error": "invalid_url"}

    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    prom = _Prom(base, client)
    try:
        try:
            # One cheap query first: if Prometheus is down, say so once rather
            # than marking fifty signals individually.
            await prom.vector("vector(1)")
        except httpx.TimeoutException:
            return {**base_out, "configured": True, "reachable": False, "error": "timeout"}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {**base_out, "configured": True, "reachable": False, "error": "unreachable"}

        source_results = await asyncio.gather(*(_source_state(prom, k) for k in SOURCES), return_exceptions=True)
        sources: dict[str, dict[str, Any]] = {}
        for key, result in zip(SOURCES, source_results):
            if isinstance(result, BaseException):
                spec = SOURCES[key]
                result = {"key": key, "name": spec["name"], "exporter": spec["exporter"], "job": spec["job"],
                          "names_verified": spec["verified"], "freshness_window_seconds": FRESHNESS_SECONDS,
                          "targets_up": 0, "targets_total": 0, "last_success_age_seconds": None,
                          "state": "UNKNOWN", "reason": "Prometheus did not answer the scrape-target query."}
            sources[key] = result

        wanted = [s for s in SIGNALS if sources[s["source"]]["state"] != "NOT AVAILABLE"]
        results = await asyncio.gather(*(prom.vector(s["query"]) for s in wanted), return_exceptions=True)
        by_key = {s["key"]: r for s, r in zip(wanted, results)}
        signals = [_signal(s, sources[s["source"]], by_key.get(s["key"], []), instance) for s in SIGNALS]
    finally:
        if own:
            await client.aclose()

    # A source that is being scraped is only HEALTHY when its judged signals are.
    for key, source in sources.items():
        if source["state"] != "HEALTHY":
            continue
        mine = [s for s in signals if s["source"] == key]
        worst = _worst([s["state"] for s in mine])
        if worst in ("WARNING", "CRITICAL"):
            bad = [s["label"] for s in mine if s["state"] == worst]
            source["state"] = worst
            source["reason"] = f"{worst.title()} threshold passed: {', '.join(bad[:4])}."
        elif not any(s["rows"] for s in mine):
            source["state"] = "NO DATA"
            source["reason"] = f"{source['exporter']} is being scraped but none of its signals returned a value."

    instances = sorted({r["labels"]["instance"] for s in signals if "instance" in SIGNALS_BY_KEY[s["key"]]["by"]
                        for r in s["rows"] if r["labels"].get("instance")})
    return {**base_out, "configured": True, "reachable": True, "error": None,
            "sources": list(sources.values()), "signals": signals, "instances": instances}
