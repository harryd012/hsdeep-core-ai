"""APM per-service reads over trace spans (observability plan P8 slice 2).

Spans are written straight to `trace_spans` so every bucket, percentile and
group below is exact: the time series (entry spans only, empty buckets kept as
measured zeros), operations, callers / callees, and errors grouped by the
OpenTelemetry `exception` event; with the window limits, RBAC and tenant
isolation the other trace reads have.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest_asyncio

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.tenant import Tenant
from app.models.trace_span import TraceSpan
from app.services.tracing.query import bucket_seconds

# A fixed, minute-aligned window ending a little before now, so every span is inside it.
UNTIL = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(second=0, microsecond=0)
SINCE = UNTIL - timedelta(hours=1)
WINDOW = {"since": SINCE.isoformat(), "until": UNTIL.isoformat()}


def _client(tenant_id, permissions=frozenset({"monitoring.read"})):
    token = create_access_token(user_id=uuid.UUID(int=0), tenant_id=tenant_id, email="noc@hsdeep.local",
                                is_superuser=False, permissions=permissions)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


def _span(tenant_id, trace, span_id, service, name, start, ms, *, parent=None, kind="server", error=False, exc=None):
    events = []
    if exc:
        events = [{"name": "exception", "time": start.isoformat(),
                   "attributes": {"exception.type": exc[0], "exception.message": exc[1]}}]
    return TraceSpan(
        tenant_id=tenant_id, trace_id=trace, span_id=span_id, parent_span_id=parent, service_name=service, name=name,
        kind=kind, start_time=start, end_time=start + timedelta(milliseconds=ms), duration_ms=ms,
        status_code="error" if error else "unset", status_message="failed" if error else None,
        attributes={}, resource_attributes={}, events=events,
    )


@pytest_asyncio.fixture
async def spans(tenant):
    """Service "shop/api": 10 requests in minute 0 (10..100 ms, the last two
    failing), 1 request in minute 30; each request calls "db". One more failure
    of another exception type."""
    t0, t30 = SINCE, SINCE + timedelta(minutes=30)
    rows = []
    for i in range(10):
        trace = f"{i:032x}"
        start = t0 + timedelta(seconds=i)
        error = i >= 8
        rows.append(_span(tenant.id, trace, f"{i:016x}", "shop/api", "GET /cart", start, (i + 1) * 10, parent=None,
                          error=error, exc=("TimeoutError", "db timed out") if error else None))
        rows.append(_span(tenant.id, trace, f"{i + 100:016x}", "db", "SELECT cart", start, 5,
                          parent=f"{i:016x}", kind="client"))
    rows.append(_span(tenant.id, f"{99:032x}", f"{99:016x}", "shop/api", "POST /order", t30, 200,
                      error=True, exc=("ValueError", "bad sku")))
    # Outside the window: never counted.
    rows.append(_span(tenant.id, f"{98:032x}", f"{98:016x}", "shop/api", "GET /cart", SINCE - timedelta(minutes=5), 999))
    async with AsyncSessionLocal() as db:
        db.add_all(rows)
        await db.commit()
    yield


def test_bucket_sizes():
    now = datetime.now(timezone.utc)
    assert bucket_seconds(now - timedelta(hours=1), now) == 60
    assert bucket_seconds(now - timedelta(hours=6), now) == 300
    assert bucket_seconds(now - timedelta(hours=24), now) == 900
    assert bucket_seconds(now - timedelta(days=7), now) == 10800


async def test_service_timeseries_operations_and_dependencies(tenant, spans):
    async with _client(tenant.id) as client:
        r = await client.get("/api/traces/services/shop/api/timeseries", params=WINDOW)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["service"] == "shop/api" and body["bucket_seconds"] == 60
        points = body["points"]
        assert len(points) == 60  # every minute of the hour, measured zeros included
        first, middle = points[0], points[30]
        assert (first["requests"], first["errors"], first["error_rate_pct"]) == (10, 2, 20.0)
        assert first["p50_ms"] == 55.0 and first["p99_ms"] > first["p95_ms"] > first["p50_ms"]
        assert (middle["requests"], middle["errors"]) == (1, 1)
        empty = points[10]
        assert (empty["requests"], empty["p50_ms"]) == (0, None)
        assert sum(p["requests"] for p in points) == 11  # the db client spans are not requests of shop/api

        ops = {o["name"]: o for o in (await client.get("/api/traces/services/shop/api/operations", params=WINDOW)).json()["items"]}
        assert set(ops) == {"GET /cart", "POST /order"}
        cart = ops["GET /cart"]
        assert (cart["calls"], cart["errors"], cart["entry"], cart["total_ms"]) == (10, 2, True, 550.0)
        assert round(cart["time_share_pct"] + ops["POST /order"]["time_share_pct"]) == 100

        deps = (await client.get("/api/traces/services/shop/api/dependencies", params=WINDOW)).json()
        assert deps["callers"] == []
        assert [(e["target"], e["calls"]) for e in deps["callees"]] == [("db", 10)]
        db_deps = (await client.get("/api/traces/services/db/dependencies", params=WINDOW)).json()
        assert [(e["source"], e["calls"]) for e in db_deps["callers"]] == [("shop/api", 10)] and db_deps["callees"] == []

        unknown = (await client.get("/api/traces/services/nothing/timeseries", params=WINDOW)).json()
        assert sum(p["requests"] for p in unknown["points"]) == 0


async def test_errors_grouped_by_exception(tenant, spans):
    async with _client(tenant.id) as client:
        items = (await client.get("/api/traces/errors", params=WINDOW)).json()["items"]
        assert [(g["operation"], g["exception_type"], g["count"]) for g in items] == [
            ("GET /cart", "TimeoutError", 2), ("POST /order", "ValueError", 1),
        ]
        timeout = items[0]
        assert timeout["message"] == "db timed out" and timeout["traces"] == 2
        assert timeout["sample_trace_id"] == f"{9:032x}"  # the most recent failing trace
        only_db = (await client.get("/api/traces/errors", params={**WINDOW, "service": "db"})).json()["items"]
        assert only_db == []


async def test_window_rbac_and_isolation(tenant, spans):
    async with _client(tenant.id) as client:
        too_long = {"since": (UNTIL - timedelta(days=8)).isoformat(), "until": UNTIL.isoformat()}
        assert (await client.get("/api/traces/services/shop/api/timeseries", params=too_long)).status_code == 422
        assert (await client.get("/api/traces/errors", params=too_long)).status_code == 422
    async with _client(tenant.id, frozenset({"incidents.read"})) as client:
        assert (await client.get("/api/traces/errors", params=WINDOW)).status_code == 403
        assert (await client.get("/api/traces/services/shop/api/operations", params=WINDOW)).status_code == 403

    async with AsyncSessionLocal() as db:
        other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
        db.add(other)
        await db.commit()
    try:
        async with _client(other.id) as client:
            assert (await client.get("/api/traces/errors", params=WINDOW)).json()["items"] == []
            assert (await client.get("/api/traces/services/shop/api/operations", params=WINDOW)).json()["items"] == []
            ts = (await client.get("/api/traces/services/shop/api/timeseries", params=WINDOW)).json()
            assert sum(p["requests"] for p in ts["points"]) == 0
    finally:
        from sqlalchemy import delete

        async with AsyncSessionLocal() as db:
            await db.execute(delete(Tenant).where(Tenant.id == other.id))
            await db.commit()
