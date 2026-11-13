"""
In-process platform metrics history: the chart fallback used when Prometheus
is not running. Pure unit tests on a private registry; no database.
"""
from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from app.services import platform_metrics_history as h
from app.services.platform_timeseries import SERIES_BY_KEY


def _registry():
    reg = CollectorRegistry()
    req = Counter("hsdeep_http_requests", "r", ["method", "route", "status"], registry=reg)
    dur = Histogram("hsdeep_http_request_duration_seconds", "d", ["method", "route"],
                    buckets=(0.1, 0.5, 1.0), registry=reg)
    prog = Gauge("hsdeep_http_requests_in_progress", "p", ["method"], registry=reg)
    runs = Counter("hsdeep_collection_run", "c", ["source_type", "status"], registry=reg)
    depth = Gauge("hsdeep_scheduler_queue_depth", "q", registry=reg)
    tick = Gauge("hsdeep_scheduler_last_tick_timestamp_seconds", "t", registry=reg)
    return reg, req, dur, prog, runs, depth, tick


def _line(payload, key, name="total"):
    series = next(s for s in payload["series"] if s["key"] == key)
    return next((l["points"] for l in series["lines"] if l["name"] == name), [])


def test_rates_ratios_and_quantiles_come_from_deltas():
    reg, req, dur, prog, runs, depth, tick = _registry()
    req.labels("GET", "/a", "200").inc(10)
    first = h.take_sample(reg, now=1000.0)

    req.labels("GET", "/a", "200").inc(27)
    req.labels("GET", "/a", "500").inc(3)
    for _ in range(30):
        dur.labels("GET", "/a").observe(0.3)
    prog.labels("GET").set(2)
    runs.labels("prtg", "success").inc(3)
    runs.labels("prtg", "error").inc(1)
    depth.set(4)
    tick.set(1020.0)
    second = h.take_sample(reg, now=1030.0)

    out = h.history_timeseries("1h", history=[first, second], now=1030.0)

    assert out["source"] == "process"
    assert out["reachable"] is True
    assert _line(out, "http_request_rate") == [[1030.0, 1.0]]  # 30 requests / 30 s
    assert _line(out, "http_error_ratio") == [[1030.0, 0.1]]
    assert _line(out, "http_by_class", "5xx") == [[1030.0, 0.1]]
    p95 = _line(out, "http_latency", "p95")[0][1]
    assert 0.1 < p95 <= 0.5  # every observation fell in the (0.1, 0.5] bucket
    assert _line(out, "http_in_progress") == [[1000.0, 0.0], [1030.0, 2.0]]  # gauges: one point per sample
    assert _line(out, "collection_success_ratio") == [[1030.0, 0.75]]
    assert _line(out, "collection_runs", "success") == [[1030.0, 6.0]]  # 3 in 30 s = 6 / min
    assert _line(out, "scheduler_queue_depth")[-1] == [1030.0, 4.0]
    assert _line(out, "scheduler_tick_age") == [[1030.0, 10.0]]


def test_options_preflights_are_excluded():
    reg, req, *_ = _registry()
    first = h.take_sample(reg, now=0.0)
    req.labels("OPTIONS", "/a", "200").inc(50)
    second = h.take_sample(reg, now=30.0)
    out = h.history_timeseries("1h", history=[first, second], now=30.0)
    assert _line(out, "http_request_rate") == [[30.0, 0.0]]


def test_no_traffic_means_no_point_never_zero():
    """An idle step has no error ratio or latency; it must not read as 0."""
    reg, *_ = _registry()
    out = h.history_timeseries(
        "1h", history=[h.take_sample(reg, now=0.0), h.take_sample(reg, now=30.0)], now=30.0)
    assert _line(out, "http_error_ratio") == []
    assert _line(out, "http_latency", "p95") == []
    assert _line(out, "collection_success_ratio") == []
    # The scheduler has never ticked in this process: unknown, not "0 s ago".
    assert _line(out, "scheduler_tick_age") == []


def test_one_sample_draws_gauges_but_no_rates():
    reg, *_ = _registry()
    out = h.history_timeseries("1h", history=[h.take_sample(reg, now=10.0)], now=10.0)
    assert _line(out, "http_request_rate") == []
    assert _line(out, "http_in_progress") == [[10.0, 0.0]]


def test_window_and_step_bound_the_points():
    reg, req, *_ = _registry()
    history = []
    for i in range(0, 7200 + 1, 30):  # 2 h of samples
        req.labels("GET", "/a", "200").inc(30)
        history.append(h.take_sample(reg, now=float(i)))
    one_hour = _line(h.history_timeseries("1h", history=history, now=7200.0), "http_request_rate")
    assert 119 <= len(one_hour) <= 121
    assert all(ts >= 3600.0 for ts, _ in one_hour)
    assert all(value == pytest.approx(1.0) for _, value in one_hour)
    six_hours = _line(h.history_timeseries("6h", history=history, now=7200.0), "http_request_rate")
    assert len(six_hours) <= 41  # 180 s steps over the 2 h that exist


def test_payload_matches_the_prometheus_catalogue_shape():
    out = h.history_timeseries("24h", history=[], now=0.0)
    assert out["history_seconds"] is None
    assert {s["key"] for s in out["series"]} == set(h.HISTORY_KEYS)
    assert h.HISTORY_KEYS <= set(SERIES_BY_KEY)
    for s in out["series"]:
        assert set(s) == {"key", "label", "unit", "group", "error", "lines"}
        assert s["label"] == SERIES_BY_KEY[s["key"]]["label"]
    assert h.history_timeseries("bogus", history=[], now=0.0)["range"] == "1h"


def test_ring_is_bounded():
    h.reset()
    try:
        reg, *_ = _registry()
        for i in range(h.MAX_SAMPLES + 5):
            h.record(h.take_sample(reg, now=float(i)))
        assert len(h.samples()) == h.MAX_SAMPLES
        assert h.samples()[0].ts == 5.0
    finally:
        h.reset()
