"""
Platform (Prometheus) metrics, Phase 2: pipeline wiring and the summary API.

Pins:
  * a collection run records its phases and outcome, and a failure is
    attributed to the stage that failed (authenticate / collect / normalize);
  * a concurrent-run skip is counted as a skip, not a run;
  * a scheduler tick records queue depth, freshness and duration, and a
    raising tick is counted as a failure;
  * the summary's percentiles match PromQL's histogram_quantile, and anything
    unobserved is None ("Not measured"), never 0;
  * GET /api/platform/observability/summary is superuser-only.
"""
from __future__ import annotations

import math
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram

from app.collectors.base import CollectorRegistry as SourceCollectors
from app.collectors.base import ConnectionCheck
from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.services.platform_metrics_summary import build_summary, histogram_quantile
from tests.test_orchestrator import _FakeCollector, _make_orchestrator, _ns_from_source

ST = "fake_test_collector"


def _v(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def _run(source, fake):
    with (
        patch.object(SourceCollectors, "get", return_value=lambda _: fake),
        patch("app.services.monitoring.collection_orchestrator.decrypt_connection_info", return_value=MagicMock()),
        patch("app.services.monitoring.collection_orchestrator.count_collector_run", new_callable=AsyncMock),
        patch("app.services.monitoring.collection_orchestrator.flush_usage_counters", new_callable=AsyncMock),
    ):
        async with AsyncSessionLocal() as db:
            outcome = await _make_orchestrator(db, fake).run(source, trigger="manual")
            await db.commit()
    return outcome


class TestCollectionWiring:
    async def test_success_records_phases_and_run(self, tenant, monitoring_source_factory):
        source = _ns_from_source(await monitoring_source_factory())
        before_runs = _v("hsdeep_collection_run_total", source_type=ST, status="success")
        before_phase = {p: _v("hsdeep_collector_execution_total", source_type=ST, phase=p)
                        for p in ("authenticate", "collect", "normalize", "persist")}

        outcome = await _run(source, _FakeCollector())

        assert outcome.run_record.status == "success"
        assert _v("hsdeep_collection_run_total", source_type=ST, status="success") == before_runs + 1
        for phase, before in before_phase.items():
            assert _v("hsdeep_collector_execution_total", source_type=ST, phase=phase) == before + 1, phase

    @pytest.mark.parametrize("stage", ["authenticate", "collect", "normalize"])
    async def test_failure_attributed_to_stage(self, tenant, monitoring_source_factory, stage):
        source = _ns_from_source(await monitoring_source_factory())
        fake = _FakeCollector()
        if stage == "authenticate":
            fake._auth_result = ConnectionCheck(success=False, message="bad credentials")
        elif stage == "collect":
            fake._collect_side_effect = RuntimeError("vendor timeout")
        else:
            fake._normalize_side_effect = ValueError("unexpected payload")
        before = _v("hsdeep_collection_failures_total", source_type=ST, stage=stage)
        before_err = _v("hsdeep_collection_run_total", source_type=ST, status="error")

        outcome = await _run(source, fake)

        assert outcome.run_record.status == "error"
        assert _v("hsdeep_collection_failures_total", source_type=ST, stage=stage) == before + 1
        assert _v("hsdeep_collection_run_total", source_type=ST, status="error") == before_err + 1

    async def test_concurrent_skip_counted_as_skip(self, tenant, monitoring_source_factory):
        from app.repositories.collector_run_repository import CollectorRunRepository

        real = await monitoring_source_factory()
        source = _ns_from_source(real)
        async with AsyncSessionLocal() as db:
            await CollectorRunRepository(db).create(
                tenant_id=real.tenant_id, monitoring_source_id=real.id, trigger="manual"
            )
            await db.commit()
        before_skip = _v("hsdeep_collection_skipped_total", source_type=ST)
        before_runs = sum(_v("hsdeep_collection_run_total", source_type=ST, status=s) for s in ("success", "error"))

        outcome = await _run(source, _FakeCollector())

        assert outcome.run_record.status == "skipped"
        assert _v("hsdeep_collection_skipped_total", source_type=ST) == before_skip + 1
        assert sum(_v("hsdeep_collection_run_total", source_type=ST, status=s)
                   for s in ("success", "error")) == before_runs


class TestSchedulerWiring:
    async def test_tick_records_depth_freshness_duration(self, monkeypatch):
        import app.scheduler.jobs as jobs

        async def _three_due():
            return 3

        monkeypatch.setattr(jobs, "_run_due_sources", _three_due)
        before = _v("hsdeep_scheduler_tick_duration_seconds_count")
        await jobs.tick()
        assert _v("hsdeep_scheduler_queue_depth") == 3
        assert _v("hsdeep_scheduler_tick_duration_seconds_count") == before + 1
        assert _v("hsdeep_scheduler_last_tick_timestamp_seconds") > 0

    async def test_raising_tick_counted_as_failure(self, monkeypatch):
        import app.scheduler.jobs as jobs

        async def _boom():
            raise RuntimeError("db blip")

        monkeypatch.setattr(jobs, "_run_due_sources", _boom)
        before = _v("hsdeep_scheduler_tick_failures_total")
        with pytest.raises(RuntimeError):
            await jobs.tick()
        assert _v("hsdeep_scheduler_tick_failures_total") == before + 1


class TestHistogramQuantile:
    def test_matches_promql_interpolation(self):
        # 10 obs <=0.1, 10 more <=0.5, 0 more <=1, all 20 <=+Inf
        buckets = [(0.1, 10), (0.5, 20), (1.0, 20), (math.inf, 20)]
        assert histogram_quantile(0.5, buckets) == pytest.approx(0.1)
        assert histogram_quantile(0.75, buckets) == pytest.approx(0.3)  # 0.1 + 0.4 * 5/10
        assert histogram_quantile(0.25, buckets) == pytest.approx(0.05)  # lower bound 0

    def test_inf_bucket_returns_last_finite_bound(self):
        assert histogram_quantile(0.99, [(1.0, 1), (math.inf, 10)]) == 1.0

    def test_no_observations_or_no_inf_is_none(self):
        assert histogram_quantile(0.95, [(1.0, 0), (math.inf, 0)]) is None
        assert histogram_quantile(0.95, [(1.0, 5)]) is None
        assert histogram_quantile(0.95, []) is None


def _fresh_registry() -> tuple[CollectorRegistry, dict]:
    reg = CollectorRegistry()
    h = {
        "req": Counter("hsdeep_http_requests_total", "x", ["method", "route", "status"], registry=reg),
        "dur": Histogram("hsdeep_http_request_duration_seconds", "x", ["method", "route"], registry=reg,
                         buckets=(0.1, 0.5, 1.0, float("inf"))),
        "prog": Gauge("hsdeep_http_requests_in_progress", "x", ["method"], registry=reg),
        "runs": Counter("hsdeep_collection_run_total", "x", ["source_type", "status"], registry=reg),
        "run_dur": Histogram("hsdeep_collection_run_duration_seconds", "x", ["source_type", "status"],
                             registry=reg, buckets=(1.0, 10.0, float("inf"))),
        "last_tick": Gauge("hsdeep_scheduler_last_tick_timestamp_seconds", "x", registry=reg),
        "depth": Gauge("hsdeep_scheduler_queue_depth", "x", registry=reg),
        "alerts": Counter("hsdeep_alert_created_total", "x", ["source", "severity"], registry=reg),
    }
    return reg, h


class TestSummary:
    def test_idle_process_is_not_measured_not_zero(self):
        reg, _ = _fresh_registry()
        s = build_summary(reg, version="t", runtime_mode="api", now=2_000_000_000.0)
        assert s["scope"]["runtime_mode"] == "api"
        assert s["http"]["requests_total"] == 0
        assert s["http"]["error_ratio"] is None
        assert s["http"]["p95_seconds"] is None
        assert s["collection"]["success_ratio"] is None
        assert s["scheduler"]["last_tick_at"] is None
        assert s["scheduler"]["queue_depth"] is None

    def test_values_computed_from_registry(self):
        reg, h = _fresh_registry()
        for _ in range(9):
            h["req"].labels("GET", "/api/x/{id}", "200").inc()
            h["dur"].labels("GET", "/api/x/{id}").observe(0.05)
        h["req"].labels("GET", "/api/x/{id}", "503").inc()
        h["dur"].labels("GET", "/api/x/{id}").observe(0.8)
        h["runs"].labels("prtg", "success").inc(3)
        h["runs"].labels("prtg", "error").inc(1)
        h["run_dur"].labels("prtg", "success").observe(2.0)
        h["last_tick"].set(1_999_999_940.0)
        h["depth"].set(4)
        h["alerts"].labels("monitoring_sync", "critical").inc(2)

        s = build_summary(reg, version="t", runtime_mode="all", now=2_000_000_000.0)
        assert s["http"]["requests_total"] == 10
        assert s["http"]["errors_total"] == 1
        assert s["http"]["error_ratio"] == pytest.approx(0.1)
        assert s["http"]["status_classes"]["5xx"] == 1
        assert s["http"]["top_routes"][0]["route"] == "/api/x/{id}"
        assert s["collection"]["success_ratio"] == pytest.approx(0.75)
        assert s["collection"]["by_source_type"][0]["source_type"] == "prtg"
        assert s["scheduler"]["last_tick_age_seconds"] == pytest.approx(60.0)
        assert s["scheduler"]["queue_depth"] == 4
        assert s["alerts"]["by_severity"] == {"critical": 2}


class TestSummaryEndpoint:
    @staticmethod
    def _token(is_superuser: bool) -> str:
        return create_access_token(
            user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), email="obs@example.test",
            is_superuser=is_superuser, permissions=frozenset(),
        )

    async def test_requires_auth_and_superuser(self, monkeypatch):
        from app.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/platform/observability/summary")).status_code == 401
            resp = await client.get(
                "/api/platform/observability/summary",
                headers={"Authorization": f"Bearer {self._token(False)}"},
            )
            assert resp.status_code in (401, 403)
