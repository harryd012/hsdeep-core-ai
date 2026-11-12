"""
Platform health classification and Prometheus alert-rule state, for the
superuser-only Platform Observability page.

Every status comes from real telemetry, read server-side:
Prometheus instant queries over the backend's own metrics, Prometheus's
``/api/v1/rules``, Grafana's ``/api/health`` and Alertmanager's
``/-/healthy``. Nothing is assumed healthy.

Statuses, worst first: CRITICAL > WARNING > DEGRADED > HEALTHY. UNKNOWN means
"no evidence" and never counts as healthy. The rules (``RULES``) are returned
with the result so the page can show exactly why each service got its status.
A service is never HEALTHY just because it answers: the API needs measured
traffic, the worker needs measured collection runs, the scheduler a recent tick.

Thresholds match the Prometheus alert rules (deployment/observability/
prometheus/rules) and the page's tile colours.
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

TIMEOUT_SECONDS = 3.0
ORDER = {"CRITICAL": 4, "WARNING": 3, "DEGRADED": 2, "HEALTHY": 1, "UNKNOWN": 0}

# Human-readable rules, shown on the page next to each status.
RULES: dict[str, list[str]] = {
    "api": [
        "CRITICAL: no API process (component api or all) answers Prometheus scrapes",
        "WARNING: 5xx ratio > 5 % or p95 latency > 2.5 s over 5 minutes",
        "DEGRADED: 5xx ratio > 1 % or p95 latency > 1 s",
        "UNKNOWN: up, but no API traffic measured in the last 5 minutes",
        "HEALTHY: up, with measured traffic under both thresholds",
    ],
    "worker": [
        "CRITICAL: a worker process (component worker or all) does not answer scrapes",
        "WARNING: collection failure ratio > 20 % over 15 minutes",
        "DEGRADED: collection failure ratio > 5 %",
        "UNKNOWN: no worker process scraped, or no collection runs in 15 minutes",
        "HEALTHY: up, with collection runs and failures under 5 %",
    ],
    "scheduler": [
        "CRITICAL: newest tick across all processes is older than 5 minutes",
        "WARNING: newest tick older than 2 minutes",
        "DEGRADED: at least one tick failed in the last 15 minutes",
        "UNKNOWN: no process has completed a tick",
        "HEALTHY: ticked within 2 minutes, no failed ticks",
    ],
    "database": [
        "CRITICAL: a backend process's /health check reports PostgreSQL down",
        "WARNING: connection pool more than 90 % used",
        "DEGRADED: connection pool more than 70 % used",
        "UNKNOWN: no /health check in 10 minutes (nothing is probing)",
        "HEALTHY: reachable, pool under 70 %",
    ],
    "redis": [
        "WARNING: a backend process's /health check reports Redis down (the platform keeps serving)",
        "UNKNOWN: no /health check in 10 minutes",
        "HEALTHY: reachable",
    ],
    "prometheus": [
        "CRITICAL: configured but unreachable",
        "UNKNOWN: PROMETHEUS_URL not set",
        "HEALTHY: reachable and scraping itself",
    ],
    "grafana": [
        "CRITICAL: configured but /api/health fails or reports its database not ok",
        "UNKNOWN: GRAFANA_URL not set on the backend",
        "HEALTHY: /api/health ok",
    ],
    "alertmanager": [
        "CRITICAL: configured but /-/healthy fails",
        "UNKNOWN: ALERTMANAGER_URL not set (none is deployed today, so alerts are evaluated but not sent)",
        "HEALTHY: /-/healthy ok",
    ],
}

# Signals the brief asks for that have no exporter in this deployment.
NOT_AVAILABLE = [
    {"key": "frontend", "name": "Frontend availability", "needs": "a blackbox_exporter probe of the Next.js server"},
    {"key": "host", "name": "Host CPU / memory / disk", "needs": "node_exporter on each host"},
    {"key": "containers", "name": "Container status and restarts", "needs": "cAdvisor or kube-state-metrics"},
    {"key": "db_queries", "name": "Database query latency", "needs": "postgres_exporter or query instrumentation"},
]


#: NOT_AVAILABLE key -> a query that returns a series once the signal is collected.
_EXPORTER_JOBS = {"host": 'up{job="node"}', "containers": 'up{job="cadvisor"}',
                  "db_queries": "count(hsdeep_db_query_duration_seconds_count)"}


def _valid(url: str) -> str | None:
    if not url:
        return None
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
        return None
    return url.strip().rstrip("/")


def _svc(key: str, name: str, status: str, reason: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"key": key, "name": name, "status": status, "reason": reason, "evidence": evidence or {}, "rules": RULES[key]}


class _Prom:
    def __init__(self, base: str, client: httpx.AsyncClient) -> None:
        self.base, self.client = base, client

    async def vector(self, expr: str) -> list[tuple[dict[str, str], float]]:
        resp = await self.client.get(f"{self.base}/api/v1/query", params={"query": expr})
        resp.raise_for_status()
        out = []
        for item in resp.json()["data"]["result"]:
            value = float(item["value"][1])
            if not math.isnan(value):
                out.append((item["metric"], value))
        return out

    async def scalar(self, expr: str) -> float | None:
        values = await self.vector(expr)
        return values[0][1] if values else None


def _worst(*statuses: str) -> str:
    known = [s for s in statuses if s != "UNKNOWN"]
    return max(known, key=ORDER.__getitem__) if known else "UNKNOWN"


async def _http_ok(client: httpx.AsyncClient, url: str) -> tuple[bool, dict[str, Any]]:
    try:
        resp = await client.get(url)
    except httpx.HTTPError:
        return False, {"error": "unreachable"}
    body: dict[str, Any] = {}
    try:
        body = resp.json() if "json" in resp.headers.get("content-type", "") else {}
    except ValueError:
        body = {}
    return resp.status_code == 200, {"http_status": resp.status_code, **{k: v for k, v in body.items() if k in ("database", "version")}}


async def evaluate(
    prometheus_url: str, grafana_url: str = "", alertmanager_url: str = "",
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        return await _evaluate(prometheus_url, grafana_url, alertmanager_url, client)
    finally:
        if own:
            await client.aclose()


async def _evaluate(prometheus_url: str, grafana_url: str, alertmanager_url: str, client: httpx.AsyncClient) -> dict[str, Any]:
    services: list[dict[str, Any]] = []
    prom_base = _valid(prometheus_url)

    # --- Prometheus-derived services -------------------------------------------------
    prom_ok = False
    if not prometheus_url:
        services.append(_svc("prometheus", "Prometheus", "UNKNOWN", "PROMETHEUS_URL is not set on the backend"))
    elif prom_base is None:
        services.append(_svc("prometheus", "Prometheus", "CRITICAL", "PROMETHEUS_URL is not a valid http(s) URL"))
    else:
        prom = _Prom(prom_base, client)
        try:
            self_up = await prom.scalar('max(up{job="prometheus"})')
            prom_ok = True
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            services.append(_svc("prometheus", "Prometheus", "CRITICAL", "Configured but not answering queries"))
        if prom_ok:
            services.append(_svc("prometheus", "Prometheus", "HEALTHY" if self_up == 1 else "WARNING",
                                 "Answering queries and scraping itself" if self_up == 1 else "Answering, but its self-scrape is not up",
                                 {"self_scrape_up": self_up}))
            services.extend(await _from_prometheus(prom))

    if not prom_ok:
        for key, name in (("api", "Backend API"), ("worker", "Background workers"), ("scheduler", "Scheduler"),
                          ("database", "PostgreSQL"), ("redis", "Redis")):
            services.append(_svc(key, name, "UNKNOWN", "No telemetry: Prometheus is not available"))

    # --- Grafana and Alertmanager health endpoints --------------------------------
    graf = _valid(grafana_url)
    if not grafana_url:
        services.append(_svc("grafana", "Grafana", "UNKNOWN", "GRAFANA_URL is not set on the backend"))
    elif graf is None:
        services.append(_svc("grafana", "Grafana", "CRITICAL", "GRAFANA_URL is not a valid http(s) URL"))
    else:
        ok, ev = await _http_ok(client, f"{graf}/api/health")
        healthy = ok and ev.get("database") == "ok"
        services.append(_svc("grafana", "Grafana", "HEALTHY" if healthy else "CRITICAL",
                             "/api/health ok" if healthy else "/api/health failed or its database is not ok", ev))

    am = _valid(alertmanager_url)
    if not alertmanager_url:
        services.append(_svc("alertmanager", "Alertmanager", "UNKNOWN",
                             "Not deployed: alert rules are evaluated in Prometheus but no notifications are sent"))
    elif am is None:
        services.append(_svc("alertmanager", "Alertmanager", "CRITICAL", "ALERTMANAGER_URL is not a valid http(s) URL"))
    else:
        ok, ev = await _http_ok(client, f"{am}/-/healthy")
        services.append(_svc("alertmanager", "Alertmanager", "HEALTHY" if ok else "CRITICAL",
                             "/-/healthy ok" if ok else "/-/healthy failed", ev))

    order = ["api", "worker", "scheduler", "database", "redis", "prometheus", "grafana", "alertmanager"]
    services.sort(key=lambda s: order.index(s["key"]))

    # Phase 22: a signal stops being listed as NOT AVAILABLE once Prometheus
    # has a scrape target for its exporter. Without that evidence (or without
    # Prometheus) the list is unchanged.
    not_available = NOT_AVAILABLE
    if prom_ok:
        deployed = set()
        for key, probe in _EXPORTER_JOBS.items():
            try:
                if await prom.vector(probe):
                    deployed.add(key)
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                continue
        not_available = [item for item in NOT_AVAILABLE if item["key"] not in deployed]
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "overall": _worst(*(s["status"] for s in services)),
        "services": services,
        "not_available": not_available,
        "status_order": ["CRITICAL", "WARNING", "DEGRADED", "HEALTHY", "UNKNOWN"],
    }


async def _from_prometheus(prom: _Prom) -> list[dict[str, Any]]:
    q = await asyncio.gather(
        prom.vector('up{job=~"hsdeep-backend|api", component=~"api|all"}'),
        prom.scalar('sum(rate(hsdeep_http_requests_total{method!="OPTIONS"}[5m]))'),
        prom.scalar('sum(rate(hsdeep_http_requests_total{method!="OPTIONS", status=~"5.."}[5m]))'),
        prom.scalar('histogram_quantile(0.95, sum by (le) (rate(hsdeep_http_request_duration_seconds_bucket{method!="OPTIONS"}[5m])))'),
        prom.vector('up{component=~"worker|all"}'),
        prom.scalar('sum(rate(hsdeep_collection_run_total{status=~"success|error"}[15m]))'),
        prom.scalar('sum(rate(hsdeep_collection_run_total{status="error"}[15m]))'),
        prom.scalar("time() - max(hsdeep_scheduler_last_tick_timestamp_seconds > 0)"),
        prom.scalar("sum(increase(hsdeep_scheduler_tick_failures_total[15m]))"),
        prom.vector("hsdeep_dependency_up"),
        prom.vector("time() - hsdeep_dependency_last_check_timestamp_seconds"),
        prom.scalar("max(hsdeep_db_pool_saturation_ratio)"),
        return_exceptions=True,
    )
    q = [None if isinstance(v, BaseException) else v for v in q]
    (api_up, rps, err_rps, p95, worker_up, runs, run_err, tick_age, tick_fail, dep_up, dep_age, pool) = q
    out = []

    # API
    api_up = api_up or []
    up_count = sum(1 for _, v in api_up if v == 1)
    ratio = (err_rps or 0) / rps if rps else None
    ev = {"processes_up": up_count, "processes_scraped": len(api_up), "requests_per_second": rps,
          "error_ratio": ratio, "p95_seconds": p95}
    if not api_up or up_count == 0:
        out.append(_svc("api", "Backend API", "CRITICAL", "No API process answers Prometheus scrapes", ev))
    elif not rps:
        out.append(_svc("api", "Backend API", "UNKNOWN", "Up, but no API traffic measured in the last 5 minutes", ev))
    elif (ratio or 0) > 0.05 or (p95 or 0) > 2.5:
        out.append(_svc("api", "Backend API", "WARNING", f"5xx {ratio:.1%}, p95 {p95 or 0:.2f} s", ev))
    elif (ratio or 0) > 0.01 or (p95 or 0) > 1:
        out.append(_svc("api", "Backend API", "DEGRADED", f"5xx {ratio:.1%}, p95 {p95 or 0:.2f} s", ev))
    else:
        out.append(_svc("api", "Backend API", "HEALTHY", f"{rps:.2f} req/s, 5xx {ratio:.1%}, p95 {p95 or 0:.2f} s", ev))

    # Worker
    worker_up = worker_up or []
    down = [m.get("instance", "?") for m, v in worker_up if v != 1]
    fail = (run_err or 0) / runs if runs else None
    ev = {"processes_scraped": len(worker_up), "processes_down": down, "runs_per_second": runs, "failure_ratio": fail}
    if not worker_up:
        out.append(_svc("worker", "Background workers", "UNKNOWN", "No worker process is scraped", ev))
    elif down:
        out.append(_svc("worker", "Background workers", "CRITICAL", f"Not answering: {', '.join(down)}", ev))
    elif not runs:
        out.append(_svc("worker", "Background workers", "UNKNOWN", "Up, but no collection runs in 15 minutes", ev))
    elif fail > 0.2:
        out.append(_svc("worker", "Background workers", "WARNING", f"{fail:.0%} of collection runs failing", ev))
    elif fail > 0.05:
        out.append(_svc("worker", "Background workers", "DEGRADED", f"{fail:.0%} of collection runs failing", ev))
    else:
        out.append(_svc("worker", "Background workers", "HEALTHY", f"Collections running, {fail:.0%} failing", ev))

    # Scheduler
    ev = {"newest_tick_age_seconds": tick_age, "failed_ticks_15m": tick_fail}
    if tick_age is None:
        out.append(_svc("scheduler", "Scheduler", "UNKNOWN", "No process has completed a tick", ev))
    elif tick_age > 300:
        out.append(_svc("scheduler", "Scheduler", "CRITICAL", f"Newest tick {tick_age:.0f} s ago", ev))
    elif tick_age > 120:
        out.append(_svc("scheduler", "Scheduler", "WARNING", f"Newest tick {tick_age:.0f} s ago", ev))
    elif (tick_fail or 0) > 0:
        out.append(_svc("scheduler", "Scheduler", "DEGRADED", f"{tick_fail:.0f} failed tick(s) in 15 minutes", ev))
    else:
        out.append(_svc("scheduler", "Scheduler", "HEALTHY", f"Ticked {tick_age:.0f} s ago", ev))

    # Dependencies (from the /health check)
    dep_up = dep_up or []
    ages = {(m.get("dependency"), m.get("instance")): v for m, v in (dep_age or [])}

    def dep(name: str) -> tuple[list[float], float | None]:
        rows = [(m, v) for m, v in dep_up if m.get("dependency") == name]
        fresh = [v for m, v in rows if ages.get((name, m.get("instance")), 1e9) <= 600]
        newest = min((ages.get((name, m.get("instance")), 1e9) for m, _ in rows), default=None)
        return fresh, newest

    fresh, newest = dep("database")
    ev = {"checks": len(fresh), "newest_check_age_seconds": newest, "pool_saturation": pool}
    if not fresh:
        out.append(_svc("database", "PostgreSQL", "UNKNOWN", "No /health check in 10 minutes", ev))
    elif min(fresh) == 0:
        out.append(_svc("database", "PostgreSQL", "CRITICAL", "A backend process reports PostgreSQL down", ev))
    elif (pool or 0) > 0.9:
        out.append(_svc("database", "PostgreSQL", "WARNING", f"Connection pool {pool:.0%} used", ev))
    elif (pool or 0) > 0.7:
        out.append(_svc("database", "PostgreSQL", "DEGRADED", f"Connection pool {pool:.0%} used", ev))
    else:
        out.append(_svc("database", "PostgreSQL", "HEALTHY", f"Reachable, pool {(pool or 0):.0%} used", ev))

    fresh, newest = dep("redis")
    ev = {"checks": len(fresh), "newest_check_age_seconds": newest}
    if not fresh:
        out.append(_svc("redis", "Redis", "UNKNOWN", "No /health check in 10 minutes", ev))
    elif min(fresh) == 0:
        out.append(_svc("redis", "Redis", "WARNING", "A backend process reports Redis down; cache and rate limiting are degraded", ev))
    else:
        out.append(_svc("redis", "Redis", "HEALTHY", "Reachable", ev))
    return out


async def fetch_alert_rules(prometheus_url: str, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Alerting rules and their current state from Prometheus /api/v1/rules."""
    checked_at = datetime.now(timezone.utc).isoformat()
    base = _valid(prometheus_url)
    empty = {"checked_at": checked_at, "rules": [], "firing": 0, "pending": 0}
    if not prometheus_url:
        return {**empty, "configured": False, "reachable": False, "error": None}
    if base is None:
        return {**empty, "configured": True, "reachable": False, "error": "invalid_url"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        resp = await client.get(f"{base}/api/v1/rules", params={"type": "alert"})
        resp.raise_for_status()
        groups = resp.json()["data"]["groups"]
    except httpx.TimeoutException:
        return {**empty, "configured": True, "reachable": False, "error": "timeout"}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {**empty, "configured": True, "reachable": False, "error": "unreachable"}
    finally:
        if own:
            await client.aclose()

    rules = []
    for group in groups:
        for rule in group.get("rules", []):
            if rule.get("type") != "alerting":
                continue
            labels = rule.get("labels") or {}
            rules.append({
                "group": group.get("name"),
                "name": rule.get("name"),
                "state": rule.get("state", "inactive"),  # inactive | pending | firing
                "severity": labels.get("severity"),
                "summary": (rule.get("annotations") or {}).get("summary"),
                "description": (rule.get("annotations") or {}).get("description"),
                "for_seconds": rule.get("duration"),
                "health": rule.get("health"),
                "last_error": rule.get("lastError") or None,
                # Prometheus already deduplicates: one alert per distinct label set.
                "alerts": [{
                    "state": a.get("state"),
                    "active_at": a.get("activeAt"),
                    "value": a.get("value"),
                    "labels": {k: v for k, v in (a.get("labels") or {}).items() if k != "alertname"},
                    "summary": (a.get("annotations") or {}).get("summary"),
                } for a in rule.get("alerts", [])],
            })
    sev = {"critical": 0, "warning": 1}
    rules.sort(key=lambda r: ({"firing": 0, "pending": 1}.get(r["state"], 2), sev.get(r["severity"] or "", 2), r["name"] or ""))
    return {"checked_at": checked_at, "configured": True, "reachable": True, "error": None, "rules": rules,
            "firing": sum(1 for r in rules if r["state"] == "firing"),
            "pending": sum(1 for r in rules if r["state"] == "pending")}


# -- Telemetry pipeline (Phase 22) ----------------------------------------------------

PIPELINE_RULES: dict[str, list[str]] = {
    "otel_collector": [
        "CRITICAL: configured but its health_check extension does not answer 200",
        "NOT AVAILABLE: OTEL_COLLECTOR_HEALTH_URL is not set on the backend",
        "HEALTHY: health_check answers 200",
    ],
    "loki": [
        "CRITICAL: configured but /ready does not answer 200",
        "NOT AVAILABLE: LOKI_URL is not set on the backend (the Logs Explorer cannot search)",
        "HEALTHY: /ready answers 200",
    ],
    "tempo": [
        "CRITICAL: configured but /ready does not answer 200",
        "NOT AVAILABLE: TEMPO_URL is not set on the backend (the Trace Explorer cannot search)",
        "HEALTHY: /ready answers 200",
    ],
    "backend_export": [
        "NOT AVAILABLE: OTEL_EXPORTER_OTLP_ENDPOINT is not set, so this process exports no spans or logs",
        "UNKNOWN: this process exports, but the collector's health is not known",
        "HEALTHY: this process exports and the collector answers",
    ],
}


async def fetch_pipeline(collector_health_url: str, loki_url: str, tempo_url: str, *, exporting: bool,
                         client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Health of the log and trace pipeline. Reachability only: it says the
    components answer, not that any particular line or span arrived."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)

    async def component(key: str, name: str, url: str, path: str, setting: str) -> dict[str, Any]:
        base = _valid(url)
        row = {"key": key, "name": name, "rules": PIPELINE_RULES[key], "evidence": {}}
        if not url:
            return {**row, "status": "NOT AVAILABLE", "reason": f"{setting} is not set on the backend"}
        if base is None:
            return {**row, "status": "CRITICAL", "reason": f"{setting} is not a valid http(s) URL"}
        ok, evidence = await _http_ok(client, f"{base}{path}")
        return {**row, "status": "HEALTHY" if ok else "CRITICAL", "evidence": evidence,
                "reason": f"{path or '/'} answered 200" if ok else f"{path or '/'} did not answer 200"}

    try:
        collector, loki, tempo = await asyncio.gather(
            component("otel_collector", "OpenTelemetry Collector", collector_health_url, "", "OTEL_COLLECTOR_HEALTH_URL"),
            component("loki", "Loki (logs)", loki_url, "/ready", "LOKI_URL"),
            component("tempo", "Tempo (traces)", tempo_url, "/ready", "TEMPO_URL"),
        )
    finally:
        if own:
            await client.aclose()
    if not exporting:
        export = {"status": "NOT AVAILABLE",
                  "reason": "OTEL_EXPORTER_OTLP_ENDPOINT is not set: this process writes logs to stdout only and exports no spans"}
    elif collector["status"] == "HEALTHY":
        export = {"status": "HEALTHY", "reason": "This process exports spans and logs, and the collector answers"}
    else:
        export = {"status": "UNKNOWN", "reason": "This process exports, but the collector's health is not confirmed"}
    components = [{"key": "backend_export", "name": "Backend export (this process)", "rules": PIPELINE_RULES["backend_export"],
                   "evidence": {"exporting": exporting}, **export}, collector, loki, tempo]
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "components": components,
            "statuses": ["HEALTHY", "CRITICAL", "UNKNOWN", "NOT AVAILABLE"]}
