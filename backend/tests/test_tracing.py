"""Traces: OTLP/HTTP JSON ingest and trace queries (observability plan P8 slice 1).

OTLP decoding (pure); and end to end through the real collector credential:
content-type and size rules, partial success, idempotent resends, then
services, traces, trace detail and the service map with RBAC and tenant
isolation; and spans following the metric retention.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.trace_span import TraceSpan
from app.services.tracing.otlp import UNKNOWN_SERVICE, decode
from tests.test_ingest_api import _drop, _make_tenant, _register_collector

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
OTHER_TRACE = "5bf92f3577b34da6a3ce929d0e0e4737"


def _ns(t: datetime) -> str:
    return str(int(t.timestamp() * 1e9))


def _span(span_id, name, start, ms, *, parent=None, kind=2, error=False, trace=TRACE, attrs=None):
    s = {
        "traceId": trace, "spanId": span_id, "name": name, "kind": kind,
        "startTimeUnixNano": _ns(start), "endTimeUnixNano": _ns(start + timedelta(milliseconds=ms)),
        "attributes": attrs or [], "status": {"code": 2, "message": "boom"} if error else {},
    }
    if parent:
        s["parentSpanId"] = parent
    return s


def _resource(service, spans):
    return {"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service}}]},
            "scopeSpans": [{"scope": {"name": "test"}, "spans": spans}]}


def test_decode_rules():
    now = datetime.now(timezone.utc)
    payload = {"resourceSpans": [
        {"resource": {"attributes": [{"key": "host.name", "value": {"stringValue": "web-1"}}]}, "scopeSpans": [{"spans": [
            _span("00f067aa0ba902b7", "GET /", now, 12.5, kind="SPAN_KIND_SERVER",
                  attrs=[{"key": "http.status_code", "value": {"intValue": "500"}}, {"key": "ok", "value": {"boolValue": True}},
                         {"key": "tags", "value": {"arrayValue": {"values": [{"stringValue": "a"}]}}}]),
        ]}]},
    ]}
    d = decode(payload)
    s = d.spans[0]
    assert (d.rejected, s["service_name"], s["kind"], s["status_code"], s["duration_ms"]) == (0, UNKNOWN_SERVICE, "server", "unset", 12.5)
    assert s["attributes"] == {"http.status_code": 500, "ok": True, "tags": ["a"]} and s["resource_attributes"] == {"host.name": "web-1"}

    bad = decode({"resourceSpans": [_resource("svc", [
        _span("00f067aa0ba902b7", "zero trace", now, 1, trace="0" * 32),
        _span("xyz", "bad span id", now, 1),
        {**_span("00f067aa0ba902b8", "ends early", now, 1), "endTimeUnixNano": _ns(now - timedelta(seconds=1))},
        _span("00f067aa0ba902b9", "fine", now, 1, kind=99, error=True),
    ])]})
    assert (bad.rejected, len(bad.spans)) == (3, 1) and "traceId" in bad.first_error
    assert (bad.spans[0]["kind"], bad.spans[0]["status_code"], bad.spans[0]["status_message"]) == ("unspecified", "error", "boom")
    assert decode([]).rejected == 1


def _client(tenant_id, permissions=frozenset({"monitoring.read"})):
    token = create_access_token(user_id=uuid.UUID(int=0), tenant_id=tenant_id, email="noc@hsdeep.local",
                                is_superuser=False, permissions=permissions)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


async def _export(credential, body, *, content_type="application/json"):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/v1/ingest/otlp/v1/traces", content=body if isinstance(body, bytes) else None,
                                 json=None if isinstance(body, bytes) else body,
                                 headers={"X-HSDEEP-Collector-Credential": credential, "Content-Type": content_type})


async def test_otlp_ingest_and_trace_queries():
    tenant, user, plan = await _make_tenant()
    try:
        credential = await _register_collector(user)
        t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
        # frontend -> api -> db, and api -> payments (fails); a second, fast trace.
        payload = {"resourceSpans": [
            _resource("frontend", [_span("a000000000000001", "GET /checkout", t0, 400)]),
            _resource("api", [
                _span("a000000000000002", "POST /orders", t0 + timedelta(milliseconds=10), 350, parent="a000000000000001"),
                _span("a000000000000003", "SELECT orders", t0 + timedelta(milliseconds=20), 40, parent="a000000000000002", kind=3),
                _span("b000000000000001", "GET /health", t0 + timedelta(seconds=30), 5, trace=OTHER_TRACE),
            ]),
            _resource("db", [_span("a000000000000004", "query", t0 + timedelta(milliseconds=21), 38, parent="a000000000000003")]),
            _resource("payments", [_span("a000000000000005", "charge", t0 + timedelta(milliseconds=100), 200,
                                         parent="a000000000000002", error=True)]),
        ]}

        assert (await _export(credential, b"\x0a\x00", content_type="application/x-protobuf")).status_code == 415
        assert (await _export("not-a-credential", payload)).status_code == 401
        assert (await _export(credential, b"{not json")).status_code == 400
        r = await _export(credential, payload)
        assert r.status_code == 200 and r.json() == {}, r.text
        # A resend is a no-op; a bad span is reported in OTLP's partialSuccess.
        bad = {"resourceSpans": [_resource("api", [_span("zz", "broken", t0, 1)])]}
        again = await _export(credential, {"resourceSpans": payload["resourceSpans"] + bad["resourceSpans"]})
        assert again.json()["partialSuccess"]["rejectedSpans"] == 1
        async with AsyncSessionLocal() as db:
            assert (await db.execute(select(func.count()).select_from(TraceSpan).where(TraceSpan.tenant_id == tenant.id))).scalar_one() == 6

        async with _client(tenant.id) as client:
            services = {s["service"]: s for s in (await client.get("/api/traces/services")).json()}
            assert set(services) == {"frontend", "api", "db", "payments"}
            assert (services["payments"]["error_spans"], services["payments"]["error_rate_pct"]) == (1, 100.0)
            assert services["api"]["traces"] == 2 and services["frontend"]["p95_ms"] == 400.0

            page = (await client.get("/api/traces")).json()
            assert page["total"] == 2
            first = {t["trace_id"]: t for t in page["items"]}[TRACE]
            assert (first["root_service"], first["root_name"], first["root_complete"]) == ("frontend", "GET /checkout", True)
            assert (first["spans"], first["errors"], first["services"]) == (5, 1, ["api", "db", "frontend", "payments"])
            assert first["duration_ms"] == 400.0
            assert [t["trace_id"] for t in (await client.get("/api/traces", params={"errors_only": True})).json()["items"]] == [TRACE]
            assert (await client.get("/api/traces", params={"min_duration_ms": 100})).json()["total"] == 1
            assert (await client.get("/api/traces", params={"service": "db"})).json()["total"] == 1
            assert (await client.get("/api/traces", params={"q": "health"})).json()["total"] == 1

            detail = (await client.get(f"/api/traces/{TRACE.upper()}")).json()
            assert [s["name"] for s in detail["spans"]][:2] == ["GET /checkout", "POST /orders"] and detail["orphans"] == 0
            assert (await client.get("/api/traces/" + "f" * 32)).status_code == 404
            assert (await client.get("/api/traces/nothex")).status_code == 422

            edges = {(e["source"], e["target"]): e for e in (await client.get("/api/traces/service-map")).json()["edges"]}
            assert set(edges) == {("frontend", "api"), ("api", "db"), ("api", "payments")}
            assert (edges[("api", "payments")]["calls"], edges[("api", "payments")]["errors"]) == (1, 1)
            assert (await client.get("/api/traces", params={"since": (t0 - timedelta(days=8)).isoformat()})).status_code == 422

        async with _client(tenant.id, permissions=frozenset()) as client:
            assert (await client.get("/api/traces")).status_code == 403
        async with _client(uuid.uuid4()) as client:
            assert (await client.get("/api/traces")).json()["total"] == 0
            assert (await client.get(f"/api/traces/{TRACE}")).status_code == 404
            assert (await client.get("/api/traces/service-map")).json()["edges"] == []
    finally:
        await _drop(tenant.id, plan.id)


async def test_spans_follow_metric_retention():
    from app.scheduler.metric_jobs import purge_old_spans

    tenant, user, plan = await _make_tenant()
    try:
        credential = await _register_collector(user)
        old = datetime.now(timezone.utc) - timedelta(days=40)
        new = datetime.now(timezone.utc) - timedelta(minutes=1)
        await _export(credential, {"resourceSpans": [_resource("api", [
            _span("c000000000000001", "old", old, 1, trace="6bf92f3577b34da6a3ce929d0e0e4738"),
            _span("c000000000000002", "new", new, 1, trace="7bf92f3577b34da6a3ce929d0e0e4739"),
        ])]})
        async with AsyncSessionLocal() as db:
            assert await purge_old_spans(db, days=0, tenant_id=tenant.id) == 0  # off by default
            assert await purge_old_spans(db, days=30, tenant_id=tenant.id) == 1
            left = (await db.execute(select(TraceSpan.name).where(TraceSpan.tenant_id == tenant.id))).scalars().all()
            assert left == ["new"]
    finally:
        await _drop(tenant.id, plan.id)
