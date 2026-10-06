"""
P0 hardening regression: the FAST topology summary and bounded graph.

Why this exists
---------------
`GET /api/infrastructure/topology/summary` originally ran its COUNTs through
`asyncio.gather()` on the single request-scoped AsyncSession (FastAPI caches
`get_db` per request). Concurrent operations on one AsyncSession are illegal in
SQLAlchemy — the request failed with
`IllegalStateChangeError: _connection_for_bind() is already in progress` and
returned 500. The route now gives each count its own short-lived pooled
session; these tests pin that behaviour so it cannot silently regress.

Also pins Task 2/3: `/infrastructure/topology/graph` is BOUNDED — `limit` /
`offset` / `site_id` / `node_type` are honoured and the response carries the
`pagination` envelope the UI divides by.

Runs against the real Postgres DB per project convention (conftest.py).
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.device import Device
from app.models.tenant import Tenant

pytestmark = pytest.mark.asyncio


def _headers(tenant_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        email=f"qs-{uuid.uuid4().hex[:8]}@test.local",
        is_superuser=False,
        permissions=frozenset({"infrastructure.read"}),
    )
    return {"Authorization": "Bearer " + token}


async def _seed_devices(db, tenant_id: uuid.UUID, count: int, prefix: str) -> None:
    for index in range(count):
        db.add(
            Device(
                tenant_id=tenant_id,
                name=f"{prefix}-{index}",
                device_type="switch",
                status="up" if index % 2 == 0 else "down",
            )
        )
    await db.commit()


async def test_quick_summary_returns_aggregates_not_rows(tenant: Tenant):
    """200 + COUNT-shaped JSON. Must never materialise the graph."""
    async with AsyncSessionLocal() as db:
        await _seed_devices(db, tenant.id, 3, "qs")

    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get(
            "/api/infrastructure/topology/summary", headers=_headers(tenant.id)
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tenant_id"] == str(tenant.id)
    assert body["devices_total"] == 3
    assert body["status"] == "ok"
    # Aggregate-only: no node/edge arrays may appear in a "summary".
    assert "nodes" not in body
    assert "edges" not in body
    assert set(body["by_health"]) <= {"healthy", "warning", "critical", "unknown"}
    assert sum(body["by_health"].values()) == body["devices_total"]


async def test_graph_endpoint_is_paginated_with_envelope(tenant: Tenant):
    """`limit`/`offset` bound the nodes; `pagination.total_devices` is the real total."""
    async with AsyncSessionLocal() as db:
        await _seed_devices(db, tenant.id, 5, "pg")

    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get(
            "/api/infrastructure/topology/graph",
            params={"limit": 2, "offset": 0},
            headers=_headers(tenant.id),
        )
        page2 = await client.get(
            "/api/infrastructure/topology/graph",
            params={"limit": 2, "offset": 2, "node_type": "device"},
            headers=_headers(tenant.id),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pagination"] == {"limit": 2, "offset": 0, "total_devices": 5}
    device_nodes = [n for n in body["nodes"] if n["type"] == "device"]
    assert len(device_nodes) == 2

    assert page2.status_code == 200, page2.text
    body2 = page2.json()
    assert body2["pagination"] == {"limit": 2, "offset": 2, "total_devices": 5}
    first_page_names = {n["name"] for n in device_nodes}
    second_page_names = {n["name"] for n in body2["nodes"] if n["type"] == "device"}
    assert len(second_page_names) == 2
    assert first_page_names.isdisjoint(second_page_names)
