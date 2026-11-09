"""
Platform (Prometheus) metrics, Phase 1: HTTP API instrumentation.

Pins:
  * ``hsdeep_http_*`` are recorded per request, labelled by route TEMPLATE;
  * paths that match no route collapse into ``__unmatched__``;
  * probes, the scrape itself and the docs are not counted;
  * 5xx responses and unhandled exceptions are counted as errors;
  * responses made before routing (a middleware's 429) keep their template;
  * ``/metrics`` on the real app exposes the platform metric families and the
    process's runtime role, and never a tenant / user UUID or an IP as a label;
  * a scrape skips the rate limiter and the API-key lookup.

No database is needed: the routes used here answer before any DB access.
"""
from __future__ import annotations

import importlib
import re
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from prometheus_client import REGISTRY
from prometheus_client.parser import text_string_to_metric_families
from starlette.responses import JSONResponse

import app.main as main_module
from app.core import metrics as platform_metrics
from app.core.http_metrics import HttpMetricsMiddleware

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _value(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _test_app(prefix: str) -> FastAPI:
    """A tiny app with the metrics middleware outermost, as in app.main.
    Each test uses its own path prefix so counters never collide."""
    app = FastAPI()

    @app.middleware("http")
    async def _early_429(request, call_next):
        # Stands in for the rate limiter: answers before routing.
        if request.headers.get("x-block"):
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        return await call_next(request)

    app.add_middleware(HttpMetricsMiddleware)

    @app.get(f"{prefix}/items/{{item_id}}")
    async def get_item(item_id: str):
        return {"id": item_id}

    @app.get(f"{prefix}/boom")
    async def boom():
        raise RuntimeError("handler failure")

    @app.get(f"{prefix}/five-hundred")
    async def five_hundred():
        return JSONResponse({"detail": "upstream"}, status_code=503)

    @app.get("/health/live")
    async def live():
        return {"ok": True}

    return app


def _client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


class TestHttpInstrumentation:
    async def test_route_template_not_raw_path(self):
        prefix = "/t1"
        async with _client(_test_app(prefix)) as client:
            for _ in range(3):
                assert (await client.get(f"{prefix}/items/{uuid.uuid4()}")).status_code == 200

        route = f"{prefix}/items/{{item_id}}"
        assert _value("hsdeep_http_requests_total", method="GET", route=route, status="200") == 3
        assert _value("hsdeep_http_request_duration_seconds_count", method="GET", route=route) == 3

    async def test_unmatched_paths_share_one_bucket(self):
        before = _value("hsdeep_http_requests_total", method="GET", route="__unmatched__", status="404")
        async with _client(_test_app("/t2")) as client:
            for i in range(4):
                assert (await client.get(f"/t2/scanner-probe-{i}.php")).status_code == 404
        after = _value("hsdeep_http_requests_total", method="GET", route="__unmatched__", status="404")
        assert after - before == 4

    async def test_probes_and_scrape_not_counted(self):
        before = _value("hsdeep_http_requests_total", method="GET", route="/health/live", status="200")
        async with _client(_test_app("/t3")) as client:
            assert (await client.get("/health/live")).status_code == 200
        assert _value("hsdeep_http_requests_total", method="GET", route="/health/live", status="200") == before

    async def test_5xx_and_exceptions_counted_as_errors(self):
        prefix = "/t4"
        async with _client(_test_app(prefix)) as client:
            assert (await client.get(f"{prefix}/five-hundred")).status_code == 503
            assert (await client.get(f"{prefix}/boom")).status_code == 500

        assert _value("hsdeep_http_errors_total", method="GET", route=f"{prefix}/five-hundred", status="503") == 1
        assert _value("hsdeep_http_errors_total", method="GET", route=f"{prefix}/boom", status="500") == 1
        assert _value("hsdeep_http_requests_total", method="GET", route=f"{prefix}/boom", status="500") == 1

    async def test_4xx_is_not_an_error(self):
        prefix = "/t5"
        async with _client(_test_app(prefix)) as client:
            await client.get(f"{prefix}/no-such-thing")
        assert _value("hsdeep_http_errors_total", method="GET", route="__unmatched__", status="404") == 0

    async def test_pre_routing_response_keeps_template(self):
        prefix = "/t6"
        async with _client(_test_app(prefix)) as client:
            resp = await client.get(f"{prefix}/items/abc", headers={"x-block": "1"})
        assert resp.status_code == 429
        assert _value(
            "hsdeep_http_requests_total", method="GET", route=f"{prefix}/items/{{item_id}}", status="429"
        ) == 1

    async def test_in_progress_returns_to_zero_and_odd_methods_bounded(self):
        prefix = "/t7"
        async with _client(_test_app(prefix)) as client:
            await client.get(f"{prefix}/items/x")
            await client.request("PROPFIND", f"{prefix}/items/x")
        assert _value("hsdeep_http_requests_in_progress", method="GET") == 0
        assert _value("hsdeep_http_requests_total", method="OTHER", route=f"{prefix}/items/{{item_id}}", status="405") == 1


def _use_settings(monkeypatch, *, token: str) -> None:
    fake = SimpleNamespace(app_env="development", metrics_bearer_token=token)
    monkeypatch.setattr(main_module, "get_settings", lambda: fake)


async def _scrape(headers=None) -> httpx.Response:
    async with _client(main_module.app) as client:
        return await client.get("/metrics", headers=headers or {})


class TestRealAppScrape:
    async def test_platform_families_and_runtime_role_exposed(self, monkeypatch):
        _use_settings(monkeypatch, token="")
        resp = await _scrape()
        assert resp.status_code == 200
        body = resp.text
        # core.metrics is loaded: its families are declared even before any run.
        for family in (
            "hsdeep_collection_run_total",
            "hsdeep_collector_phase_duration_seconds",
            "hsdeep_scheduler_last_tick_timestamp_seconds",
            "hsdeep_alert_created_total",
            "hsdeep_http_requests_total",
        ):
            assert f"# HELP {family}" in body, family
        role = main_module.settings.runtime_mode
        assert f'hsdeep_build_info{{runtime_mode="{role}",version="{main_module.app.version}"}} 1.0' in body

    async def test_no_tenant_ids_or_ips_in_labels(self, monkeypatch):
        _use_settings(monkeypatch, token="")
        async with _client(main_module.app) as client:
            # Unauthenticated requests on routes with UUID path params: answered
            # by auth (401) before any DB access.
            for _ in range(3):
                await client.get(f"/api/incidents/{uuid.uuid4()}")
                await client.get(f"/api/scan/{uuid.uuid4()}/10.0.0.1")
        body = (await _scrape()).text
        assert 'route="/api/incidents/{incident_id}"' in body
        for family in text_string_to_metric_families(body):
            if not family.name.startswith("hsdeep_"):
                continue
            for sample in family.samples:
                for value in sample.labels.values():
                    assert not UUID_RE.search(value), (sample.name, value)
                    assert not IP_RE.search(value), (sample.name, value)

    async def test_scrape_skips_limiter_and_api_key_lookup(self, monkeypatch):
        import app.services.metering.api_quota as api_quota

        async def _boom(*_a, **_k):
            raise AssertionError("a scrape must not look up API keys")

        monkeypatch.setattr(api_quota, "api_key_tenant", _boom)
        _use_settings(monkeypatch, token="scrape-secret")
        # Well past the 60 rpm per-IP window: none may wait or be refused.
        for _ in range(70):
            assert (await _scrape({"Authorization": "Bearer scrape-secret"})).status_code == 200


def test_metric_modules_register_once():
    """Re-importing never re-registers (which would raise Duplicated timeseries)."""
    again = importlib.import_module("app.core.metrics")
    assert again is platform_metrics
    importlib.import_module("app.main")
    names = [m.name for m in REGISTRY.collect() if m.name.startswith("hsdeep_http_requests")]
    assert names.count("hsdeep_http_requests") == 1
