"""
A short in-process history of THIS process's platform metrics, so the native
charts still have data when Prometheus is not running (a laptop without
Docker, a broken scrape, a fresh install).

It is a fallback, not a second metrics store:

  * source of truth stays the in-process Prometheus registry; this only keeps
    periodic snapshots of a few totals in a bounded ring (24 h at 30 s);
  * nothing is written to disk and it resets when the process restarts;
  * scope is this one process, exactly like the summary endpoint. Prometheus
    remains the answer for every process, longer windows and alert rules;
  * the payload has the same shape and series keys as
    ``platform_timeseries.fetch_timeseries`` and says ``source: "process"``,
    so the page can draw it with the same charts and label where it came from;
  * a rate needs two samples: with fewer, or no traffic in a step, a point is
    simply absent (never 0), matching the Prometheus path's NaN handling.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from prometheus_client import REGISTRY
from prometheus_client.registry import CollectorRegistry

from app.services.platform_metrics_summary import (
    EXCLUDED_METHODS,
    _buckets,
    _samples,
    _sum,
    histogram_quantile,
)
from app.services.platform_timeseries import DEFAULT_RANGE, RANGES, SERIES_BY_KEY

logger = logging.getLogger(__name__)

SAMPLE_INTERVAL_SECONDS = 30.0
#: 24 h of samples; the 7d range shows whatever part of it exists.
MAX_SAMPLES = int(24 * 3600 / SAMPLE_INTERVAL_SECONDS)

STATUS_CLASSES = ("2xx", "3xx", "4xx", "5xx")


@dataclass(frozen=True)
class Sample:
    ts: float
    requests: float
    by_class: dict[str, float]
    buckets: dict[float, float]
    in_progress: float
    collection: dict[str, float]
    queue_depth: float | None
    last_tick: float | None
    pool_saturation: float | None = None


_history: deque[Sample] = deque(maxlen=MAX_SAMPLES)


def take_sample(registry: CollectorRegistry = REGISTRY, now: float | None = None) -> Sample:
    """Snapshot the totals the charts need. Cheap: a few registry reads."""
    def keep(samples):
        return [s for s in samples if s[1].get("method") not in EXCLUDED_METHODS]

    req = keep(_samples(registry, "hsdeep_http_requests"))
    dur = keep(_samples(registry, "hsdeep_http_request_duration_seconds"))
    progress = keep(_samples(registry, "hsdeep_http_requests_in_progress"))
    runs = _samples(registry, "hsdeep_collection_run")
    depth = _samples(registry, "hsdeep_scheduler_queue_depth")
    tick = _samples(registry, "hsdeep_scheduler_last_tick_timestamp_seconds")
    pool = _samples(registry, "hsdeep_db_pool_saturation_ratio")

    by_class = {c: 0.0 for c in STATUS_CLASSES}
    for name, labels, value in req:
        cls = f"{labels.get('status', '')[:1]}xx"
        if name == "hsdeep_http_requests_total" and cls in by_class:
            by_class[cls] += value

    last_tick = max((v for _, _, v in tick), default=0.0)
    return Sample(
        ts=time.time() if now is None else now,
        requests=_sum(req, "hsdeep_http_requests_total"),
        by_class=by_class,
        buckets=dict(_buckets(dur, "hsdeep_http_request_duration_seconds_bucket")),
        in_progress=_sum(progress, "hsdeep_http_requests_in_progress"),
        collection={
            "success": _sum(runs, "hsdeep_collection_run_total", status="success"),
            "error": _sum(runs, "hsdeep_collection_run_total", status="error"),
        },
        queue_depth=max((v for _, _, v in depth), default=None) if depth else None,
        # 0 means "this process has never ticked", not "ticked at the epoch".
        last_tick=last_tick if last_tick > 0 else None,
        pool_saturation=max((v for _, _, v in pool), default=None) if pool else None,
    )


def record(sample: Sample | None = None) -> Sample:
    sample = sample or take_sample()
    _history.append(sample)
    return sample


def samples() -> list[Sample]:
    return list(_history)


def reset() -> None:
    """Clear the ring. Test hook only."""
    _history.clear()


async def run_sampler(interval: float = SAMPLE_INTERVAL_SECONDS) -> None:
    """Background task: one snapshot per interval until cancelled. A failed
    snapshot is logged and skipped; it must never take the app down."""
    while True:
        try:
            record()
        except Exception:  # noqa: BLE001 - observability must not break the app
            logger.warning("Platform metrics snapshot failed.", exc_info=True)
        await asyncio.sleep(interval)


def _steps(history: Iterable[Sample], start: float, step: int) -> list[Sample]:
    """The last sample of each step-wide slot from ``start`` on, oldest first,
    preceded by the newest sample before ``start`` (the first rate's baseline)."""
    before: Sample | None = None
    slots: dict[int, Sample] = {}
    for s in history:
        if s.ts < start:
            before = s
        else:
            slots[int((s.ts - start) // step)] = s
    ordered = [slots[k] for k in sorted(slots)]
    return ([before] if before else []) + ordered


def history_timeseries(range_key: str = DEFAULT_RANGE, history: list[Sample] | None = None,
                       now: float | None = None) -> dict[str, Any]:
    """The chart payload computed from the in-process ring."""
    range_key = range_key if range_key in RANGES else DEFAULT_RANGE
    seconds, step = RANGES[range_key]
    step = max(step, int(SAMPLE_INTERVAL_SECONDS))
    history = samples() if history is None else history
    end = time.time() if now is None else now
    points = _steps(history, end - seconds, step)

    lines: dict[str, dict[str, list[list[float]]]] = {}

    def add(key: str, name: str, ts: float, value: float | None) -> None:
        if value is not None:
            lines.setdefault(key, {}).setdefault(name, []).append([ts, value])

    for prev, cur in zip(points, points[1:]):
        dt = cur.ts - prev.ts
        if dt <= 0:
            continue
        d_req = cur.requests - prev.requests
        if d_req >= 0:
            add("http_request_rate", "total", cur.ts, d_req / dt)
        for cls in STATUS_CLASSES:
            d = cur.by_class[cls] - prev.by_class[cls]
            if d > 0:
                add("http_by_class", cls, cur.ts, d / dt)
        if d_req > 0:
            add("http_error_ratio", "total", cur.ts, (cur.by_class["5xx"] - prev.by_class["5xx"]) / d_req)
            delta = [(le, count - prev.buckets.get(le, 0.0)) for le, count in cur.buckets.items()]
            for q in (0.5, 0.95, 0.99):
                add("http_latency", f"p{int(q * 100)}", cur.ts, histogram_quantile(q, delta))
        d_ok = cur.collection["success"] - prev.collection["success"]
        d_err = cur.collection["error"] - prev.collection["error"]
        if d_ok > 0:
            add("collection_runs", "success", cur.ts, d_ok / dt * 60)
        if d_err > 0:
            add("collection_runs", "error", cur.ts, d_err / dt * 60)
        if d_ok + d_err > 0:
            add("collection_success_ratio", "total", cur.ts, d_ok / (d_ok + d_err))

    # Gauges need no baseline: every sample inside the window is a point.
    for s in points:
        if s.ts < end - seconds:
            continue
        add("http_in_progress", "total", s.ts, s.in_progress)
        add("scheduler_queue_depth", "total", s.ts, s.queue_depth)
        add("db_pool_saturation", "total", s.ts, s.pool_saturation)
        add("scheduler_tick_age", "total", s.ts, None if s.last_tick is None else max(0.0, s.ts - s.last_tick))

    series = []
    for key, spec in SERIES_BY_KEY.items():
        if key not in HISTORY_KEYS:
            continue
        series.append({
            "key": key, "label": spec["label"], "unit": spec["unit"], "group": spec["group"], "error": None,
            "lines": [{"name": name, "points": pts} for name, pts in sorted(lines.get(key, {}).items())],
        })

    oldest = history[0].ts if history else None
    return {
        "range": range_key, "step_seconds": step, "checked_at": datetime.now(timezone.utc).isoformat(),
        "source": "process", "configured": True, "reachable": True, "error": None,
        "history_seconds": None if oldest is None else round(end - oldest),
        "series": series,
    }


#: Catalogue keys this fallback can compute from one process's totals. The
#: rest (per-stage failures, per-type durations, alert rules) need Prometheus.
HISTORY_KEYS = frozenset({
    "http_request_rate", "http_by_class", "http_error_ratio", "http_latency", "http_in_progress",
    "collection_runs", "collection_success_ratio",
    "scheduler_tick_age", "scheduler_queue_depth", "db_pool_saturation",
})
