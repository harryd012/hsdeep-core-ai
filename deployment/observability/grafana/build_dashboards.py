"""
Generate the HSDEEP platform Grafana dashboards (grafana/dashboards/*.json).

    python deployment/observability/grafana/build_dashboards.py

Original HSDEEP dashboards, written as code so every panel is reviewable and
consistent. Rules every panel follows:

  * it queries only metrics the backend emits (app/core/metrics.py) or the
    recording rules in prometheus/rules/hsdeep-recording.yml;
  * counters go through rate()/increase(); histograms through
    histogram_quantile() on _bucket; gauges are read as-is;
  * a missing series shows "No data", never a default value;
  * anything without a metric yet is a "Pending" text panel, not a chart.

Thresholds match the alert rules and the app's Platform Observability page.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "dashboards"
DS = {"type": "prometheus", "uid": "hsdeep-prometheus"}
TAG = "hsdeep-platform"
COMP = 'component=~"$component"'

_next_id = 0


def _id() -> int:
    global _next_id
    _next_id += 1
    return _next_id


def target(expr: str, legend: str = "", ref: str = "A", instant: bool = False) -> dict:
    return {"datasource": DS, "expr": expr, "legendFormat": legend or "__auto", "refId": ref,
            "range": not instant, "instant": instant}


def thresholds(*steps: tuple[str, float | None]) -> dict:
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in steps]}


GOOD_LOW = thresholds(("green", None), ("orange", 1), ("red", 2.5))


def stat(title, expr, *, x, y, w=4, h=4, unit="short", desc="", th=None, decimals=None, mappings=None) -> dict:
    defaults = {"unit": unit, "noValue": "Not measured", "thresholds": th or thresholds(("blue", None))}
    if decimals is not None:
        defaults["decimals"] = decimals
    if mappings:
        defaults["mappings"] = mappings
    return {
        "id": _id(), "type": "stat", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [target(expr, instant=True)],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "value", "graphMode": "none", "textMode": "auto", "justifyMode": "auto"},
    }


def series(title, targets_, *, x, y, w=12, h=8, unit="short", desc="", th=None, stack=False, min0=True) -> dict:
    custom = {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 10, "showPoints": "never",
              "spanNulls": False, "stacking": {"mode": "normal" if stack else "none", "group": "A"}}
    defaults = {"unit": unit, "custom": custom, "noValue": "No data",
                "thresholds": th or thresholds(("green", None))}
    if min0:
        defaults["min"] = 0
    if th:
        custom["thresholdsStyle"] = {"mode": "line+area"}
    return {
        "id": _id(), "type": "timeseries", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [target(e, l, chr(65 + i)) for i, (e, l) in enumerate(targets_)],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["mean", "max", "lastNotNull"]},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
    }


def table(title, expr, *, x, y, w=12, h=8, unit="short", desc="", rename=None, sort_by="Value") -> dict:
    return {
        "id": _id(), "type": "table", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [{**target(expr, instant=True), "format": "table"}],
        "fieldConfig": {"defaults": {"unit": unit, "noValue": "No data"}, "overrides": []},
        "options": {"showHeader": True, "sortBy": [{"displayName": sort_by, "desc": True}]},
        "transformations": [{"id": "organize", "options": {
            "excludeByName": {"Time": True, "__name__": True, "job": True},
            "renameByName": rename or {}}}],
    }


def text(title, body, *, x, y, w=24, h=3) -> dict:
    return {"id": _id(), "type": "text", "title": title, "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "options": {"mode": "markdown", "content": body}}


def row(title, y) -> dict:
    return {"id": _id(), "type": "row", "title": title, "collapsed": False, "panels": [],
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}}


def component_var() -> dict:
    return {"name": "component", "label": "Process role", "type": "query", "datasource": DS,
            "query": {"query": "label_values(hsdeep_build_info, component)", "refId": "component"},
            "definition": "label_values(hsdeep_build_info, component)",
            "includeAll": True, "multi": True, "allValue": ".*", "refresh": 2, "sort": 1,
            "current": {"selected": True, "text": "All", "value": "$__all"}}


def source_type_var() -> dict:
    q = "label_values(hsdeep_collection_run_total, source_type)"
    return {"name": "source_type", "label": "Source type", "type": "query", "datasource": DS,
            "query": {"query": q, "refId": "source_type"}, "definition": q,
            "includeAll": True, "multi": True, "allValue": ".*", "refresh": 2, "sort": 1,
            "current": {"selected": True, "text": "All", "value": "$__all"}}


def dashboard(uid: str, title: str, description: str, panels: list, variables: list) -> dict:
    return {
        "uid": uid, "title": title, "description": description, "tags": [TAG],
        "timezone": "browser", "editable": False, "graphTooltip": 1, "schemaVersion": 39, "version": 1,
        "time": {"from": "now-6h", "to": "now"},
        "refresh": "30s",
        "timepicker": {"refresh_intervals": ["15s", "30s", "1m", "5m", "15m", "1h"]},
        "templating": {"list": variables},
        "annotations": {"list": []},
        # Drill-down: every HSDEEP platform dashboard links to the others.
        "links": [{"type": "dashboards", "tags": [TAG], "asDropdown": True, "title": "HSDEEP Platform",
                   "includeVars": True, "keepTime": True}],
        "panels": panels,
    }


# --- 1. Executive Platform Health -------------------------------------------------

def executive() -> dict:
    p = [
        row("Availability", 0),
        stat("API availability", f'avg(avg_over_time(up{{job=~"hsdeep-backend|api", {COMP}}}[$__range])) * 100',
             x=0, y=1, unit="percent", decimals=2,
             desc="Share of successful Prometheus scrapes of the API over the selected range. A scrape proves the process answers; request errors are the next panel.",
             th=thresholds(("red", None), ("orange", 99), ("green", 99.9))),
        stat("Request rate", f'sum(hsdeep:http_requests:rate5m{{{COMP}}})', x=4, y=1, unit="reqps",
             desc="API requests per second (5 m rate), CORS preflights excluded."),
        stat("5xx error rate", f'sum(rate(hsdeep_http_requests_total{{method!="OPTIONS", status=~"5..", {COMP}}}[5m])) / sum(rate(hsdeep_http_requests_total{{method!="OPTIONS", {COMP}}}[5m]))',
             x=8, y=1, unit="percentunit", decimals=2,
             th=thresholds(("green", None), ("orange", 0.01), ("red", 0.05))),
        stat("API p95 latency", f'histogram_quantile(0.95, sum by (le) (rate(hsdeep_http_request_duration_seconds_bucket{{method!="OPTIONS", {COMP}}}[5m])))',
             x=12, y=1, unit="s", th=GOOD_LOW),
        stat("Collection success", f'sum(rate(hsdeep_collection_run_total{{status="success", {COMP}}}[1h])) / sum(rate(hsdeep_collection_run_total{{status=~"success|error", {COMP}}}[1h]))',
             x=16, y=1, unit="percentunit", decimals=1,
             desc="Successful collection runs over the last hour. Skipped runs are not counted.",
             th=thresholds(("red", None), ("orange", 0.8), ("green", 0.95))),
        stat("Scheduler last tick", "time() - max(hsdeep_scheduler_last_tick_timestamp_seconds > 0)",
             x=20, y=1, unit="s", desc="Age of the newest scheduler tick across every process. Ticks run each minute.",
             th=thresholds(("green", None), ("orange", 120), ("red", 300))),
        row("Dependencies", 5),
        stat("PostgreSQL", f'min(hsdeep_dependency_up{{dependency="database", {COMP}}})', x=0, y=6, w=6,
             desc="From the /health check readiness probes call.",
             mappings=[{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}],
             th=thresholds(("red", None), ("green", 1))),
        stat("Redis", f'min(hsdeep_dependency_up{{dependency="redis", {COMP}}})', x=6, y=6, w=6,
             mappings=[{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}],
             th=thresholds(("red", None), ("green", 1))),
        stat("DB pool used", f'max(hsdeep_db_pool_saturation_ratio{{{COMP}}})', x=12, y=6, w=6, unit="percentunit",
             desc="Checked-out connections / (pool size + max overflow), worst process.",
             th=thresholds(("green", None), ("orange", 0.7), ("red", 0.9))),
        stat("Processes up", f'sum(up{{job=~"hsdeep-backend|api|worker|scheduler", {COMP}}})', x=18, y=6, w=6,
             desc="Backend processes Prometheus can scrape right now."),
        row("Trends", 10),
        series("Requests and errors", [
            (f'sum by (component) (hsdeep:http_requests:rate5m{{{COMP}}})', "{{component}} requests/s"),
            (f'sum by (component) (rate(hsdeep_http_requests_total{{method!="OPTIONS", status=~"5..", {COMP}}}[5m]))', "{{component}} 5xx/s"),
        ], x=0, y=11, unit="reqps"),
        series("Scrape health by process", [(f'up{{job=~"hsdeep-backend|api|worker|scheduler", {COMP}}}', "{{component}} {{instance}}")],
               x=12, y=11, unit="short",
               desc="1 = scraped, 0 = not answering. Drops here explain gaps in every other panel."),
        text("Not shown yet", "**Active monitoring sources** and a single **platform health score** have no metric yet, so they are not drawn. "
             "The app's Platform Observability page explains its health rules; host CPU / memory / disk need node_exporter, "
             "container restarts need cAdvisor or kube-state-metrics.", x=0, y=19, h=3),
    ]
    return dashboard("hsdeep-executive", "HSDEEP · Executive Platform Health",
                     "Platform-level health of HSDEEP itself (not customer infrastructure).", p, [component_var()])


# --- 2. Collector Operations ------------------------------------------------------------

def collectors() -> dict:
    sel = f'{COMP}, source_type=~"$source_type"'
    p = [
        row("Outcomes", 0),
        stat("Runs (range)", f'sum(increase(hsdeep_collection_run_total{{status=~"success|error", {sel}}}[$__range]))', x=0, y=1, w=6, decimals=0),
        stat("Success rate", f'sum(increase(hsdeep_collection_run_total{{status="success", {sel}}}[$__range])) / sum(increase(hsdeep_collection_run_total{{status=~"success|error", {sel}}}[$__range]))',
             x=6, y=1, w=6, unit="percentunit", decimals=1, th=thresholds(("red", None), ("orange", 0.8), ("green", 0.95))),
        stat("Failed runs (range)", f'sum(increase(hsdeep_collection_run_total{{status="error", {sel}}}[$__range]))', x=12, y=1, w=6, decimals=0,
             th=thresholds(("green", None), ("red", 1))),
        stat("Skipped (already running)", f'sum(increase(hsdeep_collection_skipped_total{{{sel}}}[$__range]))', x=18, y=1, w=6, decimals=0,
             th=thresholds(("green", None), ("orange", 1))),
        series("Runs per minute by source type and outcome",
               [(f'sum by (source_type, status) (rate(hsdeep_collection_run_total{{{sel}}}[5m])) * 60', "{{source_type}} {{status}}")],
               x=0, y=5, unit="short", stack=True),
        series("Failures by stage", [(f'sum by (source_type, stage) (rate(hsdeep_collection_failures_total{{{sel}}}[15m])) * 60', "{{source_type}} {{stage}}")],
               x=12, y=5, unit="short",
               desc="authenticate = credentials rejected, collect = vendor call, normalize = payload, persist = save / alert evaluation."),
        row("Performance", 13),
        series("Run duration p50 / p95 / p99", [
            (f'histogram_quantile(0.50, sum by (le) (rate(hsdeep_collection_run_duration_seconds_bucket{{{sel}}}[15m])))', "p50"),
            (f'histogram_quantile(0.95, sum by (le) (rate(hsdeep_collection_run_duration_seconds_bucket{{{sel}}}[15m])))', "p95"),
            (f'histogram_quantile(0.99, sum by (le) (rate(hsdeep_collection_run_duration_seconds_bucket{{{sel}}}[15m])))', "p99"),
        ], x=0, y=14, unit="s", th=thresholds(("green", None), ("red", 60))),
        series("Mean phase duration", [(
            f'sum by (phase) (rate(hsdeep_collector_phase_duration_seconds_sum{{{sel}}}[15m])) / sum by (phase) (rate(hsdeep_collector_phase_duration_seconds_count{{{sel}}}[15m]))',
            "{{phase}}")], x=12, y=14, unit="s",
            desc="persist covers saving the bundle and alert evaluation, which is where PostgreSQL time shows up."),
        series("Items saved per minute", [(f'sum by (entity_type) (rate(hsdeep_collection_items_upserted_total{{{sel}}}[5m])) * 60', "{{entity_type}}")],
               x=0, y=22, unit="short"),
        table("p95 run duration by source type", f'histogram_quantile(0.95, sum by (source_type, le) (rate(hsdeep_collection_run_duration_seconds_bucket{{{sel}}}[1h])))',
              x=12, y=22, unit="s", rename={"source_type": "Source type", "Value": "p95 (1 h)"}, sort_by="p95 (1 h)"),
        text("Pending", "**Retry counts** are not instrumented yet (the collector retry/backoff has no metric), so no retry panel is drawn. "
             "Choose `prtg` in *Source type* for PRTG-only collection performance.", x=0, y=30, h=3),
    ]
    return dashboard("hsdeep-collectors", "HSDEEP · Collector Operations",
                     "Collection pipeline outcomes and performance by source type.", p, [component_var(), source_type_var()])


# --- 3. API Performance -------------------------------------------------------------

def api() -> dict:
    sel = f'method!="OPTIONS", {COMP}'
    p = [
        row("RED overview", 0),
        series("Requests per second", [(f'sum by (component) (rate(hsdeep_http_requests_total{{{sel}}}[5m]))', "{{component}}")],
               x=0, y=1, w=8, unit="reqps"),
        series("Responses by status class", [(f'sum by (class) (label_replace(rate(hsdeep_http_requests_total{{{sel}}}[5m]), "class", "${{1}}xx", "status", "(.).."))', "{{class}}")],
               x=8, y=1, w=8, unit="reqps", stack=True),
        series("Latency p50 / p95 / p99", [
            (f'histogram_quantile(0.50, sum by (le) (rate(hsdeep_http_request_duration_seconds_bucket{{{sel}}}[5m])))', "p50"),
            (f'histogram_quantile(0.95, sum by (le) (rate(hsdeep_http_request_duration_seconds_bucket{{{sel}}}[5m])))', "p95"),
            (f'histogram_quantile(0.99, sum by (le) (rate(hsdeep_http_request_duration_seconds_bucket{{{sel}}}[5m])))', "p99"),
        ], x=16, y=1, w=8, unit="s", th=GOOD_LOW),
        row("Routes", 9),
        table("Slowest routes (p95, 1 h)", f'topk(10, histogram_quantile(0.95, sum by (method, route, le) (rate(hsdeep_http_request_duration_seconds_bucket{{{sel}}}[1h]))))',
              x=0, y=10, unit="s", rename={"method": "Method", "route": "Route", "Value": "p95"}, sort_by="p95"),
        table("Busiest routes (req/s, 1 h)", f'topk(10, sum by (method, route) (rate(hsdeep_http_requests_total{{{sel}}}[1h])))',
              x=12, y=10, unit="reqps", rename={"method": "Method", "route": "Route", "Value": "req/s"}, sort_by="req/s"),
        series("5xx by route", [(f'topk(5, sum by (method, route) (rate(hsdeep_http_errors_total{{{COMP}}}[5m])))', "{{method}} {{route}}")],
               x=0, y=18, unit="reqps", desc="5xx responses and unhandled exceptions, by route template."),
        series("4xx by route", [(f'topk(5, sum by (method, route) (rate(hsdeep_http_requests_total{{{sel}, status=~"4.."}}[5m])))', "{{method}} {{route}}")],
               x=12, y=18, unit="reqps", desc="Client outcomes (auth, validation, not found). Not errors, but a spike is worth a look."),
        series("Requests in progress", [(f'sum by (component, method) (hsdeep_http_requests_in_progress{{{COMP}}})', "{{component}} {{method}}")],
               x=0, y=26, w=24, h=6, unit="short"),
    ]
    return dashboard("hsdeep-api", "HSDEEP · API Performance",
                     "Rate, errors and duration of the HSDEEP API by route template.", p, [component_var()])


# --- 4. Scheduler & Worker Health ------------------------------------------------------

def scheduler() -> dict:
    p = [
        row("Scheduler", 0),
        stat("Newest tick age", "time() - max(hsdeep_scheduler_last_tick_timestamp_seconds > 0)", x=0, y=1, w=6, unit="s",
             th=thresholds(("green", None), ("orange", 120), ("red", 300))),
        stat("Sources due (last tick)", f'max(hsdeep_scheduler_queue_depth{{{COMP}}} and on (instance) (hsdeep_scheduler_last_tick_timestamp_seconds > 0))',
             x=6, y=1, w=6, desc="Due sources found by the most recent tick, from the process that ran it."),
        stat("Failed ticks (range)", "sum(increase(hsdeep_scheduler_tick_failures_total[$__range]))", x=12, y=1, w=6, decimals=0,
             th=thresholds(("green", None), ("red", 1))),
        stat("Tick p95", "histogram_quantile(0.95, sum by (le) (rate(hsdeep_scheduler_tick_duration_seconds_bucket[1h])))",
             x=18, y=1, w=6, unit="s"),
        series("Tick duration p50 / p95", [
            ("histogram_quantile(0.50, sum by (le) (rate(hsdeep_scheduler_tick_duration_seconds_bucket[15m])))", "p50"),
            ("histogram_quantile(0.95, sum by (le) (rate(hsdeep_scheduler_tick_duration_seconds_bucket[15m])))", "p95"),
        ], x=0, y=5, unit="s"),
        series("Due sources per tick", [(f'hsdeep_scheduler_queue_depth{{{COMP}}} and on (instance) (hsdeep_scheduler_last_tick_timestamp_seconds > 0)', "{{component}} {{instance}}")],
               x=12, y=5, unit="short"),
        row("Processes", 13),
        series("Scrape health", [('up{job=~"hsdeep-backend|api|worker|scheduler"}', "{{component}} {{instance}}")],
               x=0, y=14, unit="short", desc="Worker and scheduler availability: 1 = scraped."),
        series("DB pool connections", [
            (f'sum by (component) (hsdeep_db_pool_checked_out{{{COMP}}})', "{{component}} checked out"),
            (f'sum by (component) (hsdeep_db_pool_size{{{COMP}}} + hsdeep_db_pool_max_overflow{{{COMP}}})', "{{component}} capacity"),
        ], x=12, y=14, unit="short"),
        series("Resident memory", [(f'process_resident_memory_bytes{{job=~"hsdeep-backend|api|worker|scheduler", {COMP}}}', "{{component}} {{instance}}")],
               x=0, y=22, unit="bytes", desc="From prometheus_client's process collector: Linux only, so empty for a Windows development backend."),
        series("CPU", [(f'rate(process_cpu_seconds_total{{job=~"hsdeep-backend|api|worker|scheduler", {COMP}}}[5m])', "{{component}} {{instance}}")],
               x=12, y=22, unit="percentunit", desc="CPU seconds per second (1.0 = one full core). Linux only, like memory."),
    ]
    return dashboard("hsdeep-scheduler", "HSDEEP · Scheduler & Worker Health",
                     "Scheduler freshness and duration, process availability and resources.", p, [component_var()])


# --- 5. Alert Pipeline ------------------------------------------------------------

def alerts() -> dict:
    p = [
        row("Alerts opened", 0),
        stat("Opened (range)", f'sum(increase(hsdeep_alert_created_total{{{COMP}}}[$__range]))', x=0, y=1, w=6, decimals=0),
        stat("Critical opened (range)", f'sum(increase(hsdeep_alert_created_total{{severity="critical", {COMP}}}[$__range]))',
             x=6, y=1, w=6, decimals=0, th=thresholds(("green", None), ("red", 1))),
        stat("Opened per hour (now)", f'sum(rate(hsdeep_alert_created_total{{{COMP}}}[1h])) * 3600', x=12, y=1, w=6, decimals=1),
        stat("Collection runs feeding alerts", f'sum(increase(hsdeep_collection_run_total{{status="success", {COMP}}}[$__range]))',
             x=18, y=1, w=6, decimals=0, desc="Alert evaluation runs inside each successful collection, so this is how many evaluations happened."),
        series("Opened per hour by severity", [(f'sum by (severity) (rate(hsdeep_alert_created_total{{{COMP}}}[15m])) * 3600', "{{severity}}")],
               x=0, y=5, unit="short", stack=True),
        series("Opened per hour by source", [(f'sum by (source) (rate(hsdeep_alert_created_total{{{COMP}}}[15m])) * 3600', "{{source}}")],
               x=12, y=5, unit="short", desc="monitoring_sync = threshold breaches on sensors; vendor_native = alarms reported by the vendor (e.g. PRTG messages)."),
        series("Alert evaluation time (persist phase)", [(
            f'sum(rate(hsdeep_collector_phase_duration_seconds_sum{{phase="persist", {COMP}}}[15m])) / sum(rate(hsdeep_collector_phase_duration_seconds_count{{phase="persist", {COMP}}}[15m]))',
            "mean persist")], x=0, y=13, unit="s",
            desc="Closest real signal today: the persist phase includes saving the bundle and evaluating alerts. A dedicated alert-processing histogram is pending."),
        series("Persist-stage failures", [(f'sum(rate(hsdeep_collection_failures_total{{stage="persist", {COMP}}}[15m])) * 60', "per minute")],
               x=12, y=13, unit="short", desc="Collections that failed while saving or evaluating alerts."),
        text("Pending", "**Alert processing latency**, **correlation activity** and **incident-creation failures** have no dedicated metric yet, "
             "so they are not drawn. The persist-phase panels above are the nearest real signal.", x=0, y=21, h=3),
    ]
    return dashboard("hsdeep-alerts", "HSDEEP · Alert Pipeline",
                     "Alerts opened by the HSDEEP alert engine, and the pipeline stage that produces them.", p, [component_var()])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, build in (("executive-platform-health", executive), ("collector-operations", collectors),
                        ("api-performance", api), ("scheduler-worker-health", scheduler), ("alert-pipeline", alerts)):
        global _next_id
        _next_id = 0
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(OUT.parent.parent.parent.parent)}")


if __name__ == "__main__":
    main()
