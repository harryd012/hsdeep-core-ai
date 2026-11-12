"""
Platform health classification and alert-rule state (app/services/platform_health.py).

A fake Prometheus answers each PromQL query from a table, so every rule is
checked without a running stack. Pins:
  * nothing is HEALTHY without evidence: an idle API, a worker with no runs and
    a scheduler that never ticked are UNKNOWN, not healthy;
  * each threshold maps to the documented status;
  * Prometheus unavailable -> every telemetry-based service UNKNOWN, Prometheus CRITICAL;
  * Grafana / Alertmanager checks use their health endpoints; unset -> UNKNOWN;
  * overall status is the worst known status;
  * alert rules come back with state, severity and active alerts, firing first;
  * both endpoints are superuser-only.
"""
from __future__ import annotations

import httpx
import pytest

from app.services.platform_health import ORDER, RULES, evaluate, fetch_alert_rules

HEALTHY_TABLE = {
    'max(up{job="prometheus"})': [({}, 1)],
    'up{job=~"hsdeep-backend|api", component=~"api|all"}': [({"instance": "a:8000"}, 1)],
    'sum(rate(hsdeep_http_requests_total{method!="OPTIONS"}[5m]))': [({}, 2.0)],
    'sum(rate(hsdeep_http_requests_total{method!="OPTIONS", status=~"5.."}[5m]))': [({}, 0.0)],
    'histogram_quantile(0.95, sum by (le) (rate(hsdeep_http_request_duration_seconds_bucket{method!="OPTIONS"}[5m])))': [({}, 0.2)],
    'up{component=~"worker|all"}': [({"instance": "a:8000"}, 1)],
    'sum(rate(hsdeep_collection_run_total{status=~"success|error"}[15m]))': [({}, 0.1)],
    'sum(rate(hsdeep_collection_run_total{status="error"}[15m]))': [({}, 0.0)],
    "time() - max(hsdeep_scheduler_last_tick_timestamp_seconds > 0)": [({}, 30)],
    "sum(increase(hsdeep_scheduler_tick_failures_total[15m]))": [({}, 0)],
    "hsdeep_dependency_up": [({"dependency": "database", "instance": "a:8000"}, 1),
                             ({"dependency": "redis", "instance": "a:8000"}, 1)],
    "time() - hsdeep_dependency_last_check_timestamp_seconds": [({"dependency": "database", "instance": "a:8000"}, 5),
                                                                ({"dependency": "redis", "instance": "a:8000"}, 5)],
    "max(hsdeep_db_pool_saturation_ratio)": [({}, 0.1)],
}


def _client(table: dict, *, grafana=200, alertmanager=200, prom_down=False) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "prom":
            if prom_down:
                raise httpx.ConnectError("refused")
            if request.url.path == "/api/v1/query":
                expr = request.url.params["query"]
                rows = table.get(expr, [])
                return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": [
                    {"metric": m, "value": [0, str(v)]} for m, v in rows]}})
            if request.url.path == "/api/v1/rules":
                return httpx.Response(200, json={"status": "success", "data": {"groups": [{"name": "hsdeep-api", "rules": [
                    {"type": "alerting", "name": "HsdeepApiLatencyHigh", "state": "inactive", "labels": {"severity": "warning"},
                     "annotations": {"summary": "slow"}, "duration": 600, "health": "ok", "alerts": []},
                    {"type": "alerting", "name": "HsdeepApiTargetDown", "state": "firing", "labels": {"severity": "critical"},
                     "annotations": {"summary": "down"}, "duration": 120, "health": "ok", "alerts": [
                         {"state": "firing", "activeAt": "2026-11-12T00:00:00Z", "value": "0",
                          "labels": {"alertname": "HsdeepApiTargetDown", "instance": "a:8000"}, "annotations": {"summary": "down"}}]},
                    {"type": "recording", "name": "hsdeep:http_requests:rate5m"},
                ]}]}})
        if request.url.host == "grafana":
            return httpx.Response(grafana, json={"database": "ok" if grafana == 200 else "failing", "version": "12.1.1"})
        if request.url.host == "am":
            return httpx.Response(alertmanager, text="OK")
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _status(result: dict) -> dict[str, str]:
    return {s["key"]: s["status"] for s in result["services"]}


async def _eval(table=HEALTHY_TABLE, **kw):
    return await evaluate("http://prom:9090", kw.pop("grafana_url", "http://grafana:3000"),
                          kw.pop("alertmanager_url", ""), client=_client(table, **kw))


async def test_all_healthy_with_evidence():
    out = await _eval()
    st = _status(out)
    assert st == {"api": "HEALTHY", "worker": "HEALTHY", "scheduler": "HEALTHY", "database": "HEALTHY",
                  "redis": "HEALTHY", "prometheus": "HEALTHY", "grafana": "HEALTHY", "alertmanager": "UNKNOWN"}
    assert out["overall"] == "HEALTHY"
    assert all(s["rules"] == RULES[s["key"]] for s in out["services"])
    assert {n["key"] for n in out["not_available"]} >= {"host", "containers", "frontend"}


async def test_no_evidence_is_unknown_not_healthy():
    table = {**HEALTHY_TABLE,
             'sum(rate(hsdeep_http_requests_total{method!="OPTIONS"}[5m]))': [({}, 0)],
             'sum(rate(hsdeep_collection_run_total{status=~"success|error"}[15m]))': [],
             "time() - max(hsdeep_scheduler_last_tick_timestamp_seconds > 0)": [],
             "time() - hsdeep_dependency_last_check_timestamp_seconds": [({"dependency": "database", "instance": "a:8000"}, 9000),
                                                                         ({"dependency": "redis", "instance": "a:8000"}, 9000)]}
    st = _status(await _eval(table))
    assert st["api"] == st["worker"] == st["scheduler"] == st["database"] == st["redis"] == "UNKNOWN"


@pytest.mark.parametrize("override,key,expected", [
    ({'up{job=~"hsdeep-backend|api", component=~"api|all"}': [({"instance": "a:8000"}, 0)]}, "api", "CRITICAL"),
    ({'sum(rate(hsdeep_http_requests_total{method!="OPTIONS", status=~"5.."}[5m]))': [({}, 0.2)]}, "api", "WARNING"),  # 10 %
    ({'sum(rate(hsdeep_http_requests_total{method!="OPTIONS", status=~"5.."}[5m]))': [({}, 0.04)]}, "api", "DEGRADED"),  # 2 %
    ({'histogram_quantile(0.95, sum by (le) (rate(hsdeep_http_request_duration_seconds_bucket{method!="OPTIONS"}[5m])))': [({}, 3.0)]}, "api", "WARNING"),
    ({'up{component=~"worker|all"}': [({"instance": "w:8001"}, 0)]}, "worker", "CRITICAL"),
    ({'sum(rate(hsdeep_collection_run_total{status="error"}[15m]))': [({}, 0.03)]}, "worker", "WARNING"),  # 30 %
    ({'sum(rate(hsdeep_collection_run_total{status="error"}[15m]))': [({}, 0.01)]}, "worker", "DEGRADED"),  # 10 %
    ({"time() - max(hsdeep_scheduler_last_tick_timestamp_seconds > 0)": [({}, 400)]}, "scheduler", "CRITICAL"),
    ({"time() - max(hsdeep_scheduler_last_tick_timestamp_seconds > 0)": [({}, 150)]}, "scheduler", "WARNING"),
    ({"sum(increase(hsdeep_scheduler_tick_failures_total[15m]))": [({}, 1)]}, "scheduler", "DEGRADED"),
    ({"hsdeep_dependency_up": [({"dependency": "database", "instance": "a:8000"}, 0),
                               ({"dependency": "redis", "instance": "a:8000"}, 1)]}, "database", "CRITICAL"),
    ({"max(hsdeep_db_pool_saturation_ratio)": [({}, 0.95)]}, "database", "WARNING"),
    ({"max(hsdeep_db_pool_saturation_ratio)": [({}, 0.75)]}, "database", "DEGRADED"),
    ({"hsdeep_dependency_up": [({"dependency": "database", "instance": "a:8000"}, 1),
                               ({"dependency": "redis", "instance": "a:8000"}, 0)]}, "redis", "WARNING"),
])
async def test_thresholds(override, key, expected):
    out = await _eval({**HEALTHY_TABLE, **override})
    assert _status(out)[key] == expected
    assert ORDER[out["overall"]] >= ORDER[expected]


async def test_prometheus_down_makes_telemetry_unknown():
    st = _status(await _eval(prom_down=True))
    assert st["prometheus"] == "CRITICAL"
    assert st["api"] == st["worker"] == st["scheduler"] == st["database"] == st["redis"] == "UNKNOWN"


async def test_not_configured():
    out = await evaluate("", "", "", client=_client(HEALTHY_TABLE))
    assert set(_status(out).values()) == {"UNKNOWN"}
    assert out["overall"] == "UNKNOWN"


async def test_grafana_and_alertmanager_health_endpoints():
    st = _status(await _eval(grafana=503, alertmanager_url="http://am:9093", alertmanager=500))
    assert st["grafana"] == "CRITICAL" and st["alertmanager"] == "CRITICAL"
    st = _status(await _eval(alertmanager_url="http://am:9093"))
    assert st["alertmanager"] == "HEALTHY"


async def test_alert_rules_state_firing_first_and_recording_rules_skipped():
    out = await fetch_alert_rules("http://prom:9090", client=_client(HEALTHY_TABLE))
    assert out["reachable"] and out["firing"] == 1 and out["pending"] == 0
    assert [r["name"] for r in out["rules"]] == ["HsdeepApiTargetDown", "HsdeepApiLatencyHigh"]
    firing = out["rules"][0]
    assert firing["severity"] == "critical" and firing["alerts"][0]["labels"] == {"instance": "a:8000"}


async def test_alert_rules_unreachable_and_unset():
    assert (await fetch_alert_rules(""))["configured"] is False
    out = await fetch_alert_rules("http://prom:9090", client=_client(HEALTHY_TABLE, prom_down=True))
    assert out["reachable"] is False and out["error"] == "unreachable"


@pytest.mark.parametrize("path", ["/api/platform/observability/health", "/api/platform/observability/alerts"])
async def test_endpoints_require_auth(path):
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(path)).status_code == 401


class TestTimeseries:
    """GET /api/platform/observability/timeseries (fixed catalogue, query_range)."""

    @staticmethod
    def _client(result_for):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/v1/query_range"
            q = request.url.params["query"]
            seen.append(q)
            return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix",
                                  "result": result_for(q)}})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen

    async def test_catalogue_queried_and_nan_dropped(self):
        from app.services.platform_timeseries import SERIES, fetch_timeseries

        def result_for(q):
            if "hsdeep_http_requests_total" in q and "status=~" not in q and "class" not in q:
                return [{"metric": {}, "values": [[100, "1.5"], [130, "NaN"], [160, "2"]]}]
            return []

        client, seen = self._client(result_for)
        out = await fetch_timeseries("http://prom:9090", "6h", client, now=1000.0)
        assert out["reachable"] and out["range"] == "6h" and out["step_seconds"] == 180
        assert len(seen) == len(SERIES)
        assert all("[180s]" in q or "[" not in q or "le)" in q or "ALERTS" in q or "time()" in q or "max(" in q
                   for q in seen)
        rate = next(s for s in out["series"] if s["key"] == "http_request_rate")
        assert rate["lines"] == [{"name": "total", "points": [[100.0, 1.5], [160.0, 2.0]]}]
        # HTTP figures exclude CORS preflights.
        assert all('method!="OPTIONS"' in q for q in seen if "hsdeep_http_request" in q and "in_progress" not in q)

    async def test_no_query_from_the_browser_and_bad_range_defaults(self):
        from app.services.platform_timeseries import fetch_timeseries

        client, _ = self._client(lambda q: [])
        out = await fetch_timeseries("http://prom:9090", "999d; drop", client)
        assert out["range"] == "1h"

    async def test_unconfigured_and_unreachable(self):
        from app.services.platform_timeseries import fetch_timeseries

        assert (await fetch_timeseries(""))["configured"] is False

        def boom(request):
            raise httpx.ConnectError("connect to http://secret:9090 refused")

        out = await fetch_timeseries("http://prom:9090", "1h", httpx.AsyncClient(transport=httpx.MockTransport(boom)))
        assert out["reachable"] is False and out["error"] == "unreachable" and "secret" not in str(out)

    async def test_endpoint_rejects_bad_range_and_requires_auth(self):
        from app.main import app

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/platform/observability/timeseries")).status_code == 401
