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
SERIES_BY_KEY = {s["key"]: s for s in SERIES}


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
                           client: httpx.AsyncClient | None = None, now: float | None = None) -> dict[str, Any]:
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
        series = await asyncio.gather(*(one(s) for s in SERIES))
    finally:
        if own:
            await client.aclose()
    answered = any(s["error"] is None for s in series)
    errors = {s["error"] for s in series if s["error"]}
    return {**out, "configured": True, "reachable": answered,
            "error": None if answered else (errors.pop() if len(errors) == 1 else "unreachable"),
            "series": list(series)}
