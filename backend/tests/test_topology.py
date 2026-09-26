"""
Topology (resource relationship) repository + service + API tests.

Covers:
  1. Repository: upsert idempotency (same pair+type updates, not duplicates)
  2. Repository: list/count/summary + tenant isolation
  3. Service: upstream BFS, downstream blast radius, connected devices
  4. Service: traversal is tenant-scoped (cross-tenant edges invisible)
  5. API: POST/PATCH/DELETE /topology/relationships + list + summary
  6. API: traversal endpoints (upstream/downstream/connected)
  7. API: subscription gate on writes (402), cross-tenant read (404)

Runs against the real Postgres DB per project convention (conftest.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.device import Device
from app.models.tenant import Tenant
from app.repositories.plan_repository import PlanRepository
from app.repositories.resource_relationship_repository import (
    ResourceRelationshipRepository,
)
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.topology_service import TopologyService

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _jwt(tenant_id: uuid.UUID, permissions: set[str], actor_id: uuid.UUID) -> str:
    return create_access_token(
        user_id=actor_id,
        tenant_id=tenant_id,
        email=f"test-{actor_id}@test.local",
        is_superuser=False,
        permissions=frozenset(permissions),
    )


async def _make_tenant(db) -> Tenant:
    t = Tenant(name=f"Other Tenant", slug=f"other-{uuid.uuid4().hex[:8]}")
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def _make_device(db, tenant_id: uuid.UUID, name: str) -> Device:
    device = Device(
        tenant_id=tenant_id,
        name=name,
        device_type="switch",
        status="online",
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return device


async def _seed_sub(db, tenant_id: uuid.UUID) -> None:
    plan = await PlanRepository(db).create(
        name=f"TOPO-{uuid.uuid4().hex[:8]}",
        tier=f"topo-{uuid.uuid4().hex[:8]}",
        description="Topology test plan",
        price_monthly=0.0,
        price_annual=0.0,
        device_limit=100,
        sensor_limit=1000,
        api_rate_limit=100000,
        collector_limit=10,
        seats=25,
        features_json={},
        is_public=False,
    )
    now = datetime.now(timezone.utc)
    await SubscriptionRepository(db).create(
        tenant_id=tenant_id,
        plan_id=plan.id,
        status="active",
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        billing_interval="month",
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Repository tests
# ---------------------------------------------------------------------------


class TestRelationshipRepository:
    async def test_upsert_idempotent(self, tenant: Tenant, db):
        repo = ResourceRelationshipRepository(db)
        a = await _make_device(db, tenant.id, "edge-a")
        b = await _make_device(db, tenant.id, "edge-b")

        edge1, created1 = await repo.upsert(
            tenant.id,
            source_resource_id=a.id,
            target_resource_id=b.id,
            relationship_type="depends_on",
        )
        edge2, created2 = await repo.upsert(
            tenant.id,
            source_resource_id=a.id,
            target_resource_id=b.id,
            relationship_type="depends_on",
            label="updated label",
        )
        await db.commit()

        assert created1 is True
        assert created2 is False
        assert edge1.id == edge2.id
        assert edge2.label == "updated label"

        total = await repo.count(tenant.id)
        assert total == 1

    async def test_upsert_different_type_same_pair(self, tenant: Tenant, db):
        """Same device pair, different type -> separate edges (not collapsed)."""
        repo = ResourceRelationshipRepository(db)
        a = await _make_device(db, tenant.id, "pair-a")
        b = await _make_device(db, tenant.id, "pair-b")

        await repo.upsert(tenant.id, source_resource_id=a.id,
                          target_resource_id=b.id, relationship_type="depends_on")
        await repo.upsert(tenant.id, source_resource_id=a.id,
                          target_resource_id=b.id, relationship_type="connected_to")
        await db.commit()

        assert await repo.count(tenant.id) == 2

    async def test_tenant_isolation(self, tenant: Tenant, db):
        repo = ResourceRelationshipRepository(db)
        a = await _make_device(db, tenant.id, "iso-a")
        b = await _make_device(db, tenant.id, "iso-b")
        edge, _ = await repo.upsert(
            tenant.id, source_resource_id=a.id,
            target_resource_id=b.id, relationship_type="depends_on",
        )
        await db.commit()

        other_tenant_id = uuid.uuid4()

        assert await repo.get(edge.id, other_tenant_id) is None
        assert await repo.count(other_tenant_id) == 0
        items = await repo.list(other_tenant_id)
        assert items == []
        summary = await repo.summary(other_tenant_id)
        assert summary["total"] == 0

        # Updating another tenant's edge object is refused at the route layer
        # (repo.get(id, tenant) -> 404 before update is ever called); the
        # repo itself never sees foreign-tenant edges because get/list never
        # return them cross-tenant.

    async def test_summary_aggregates(self, tenant: Tenant, db):
        repo = ResourceRelationshipRepository(db)
        a = await _make_device(db, tenant.id, "sum-a")
        b = await _make_device(db, tenant.id, "sum-b")
        c = await _make_device(db, tenant.id, "sum-c")
        await repo.upsert(tenant.id, source_resource_id=a.id,
                          target_resource_id=b.id, relationship_type="depends_on")
        await repo.upsert(tenant.id, source_resource_id=b.id,
                          target_resource_id=c.id, relationship_type="depends_on")
        await repo.upsert(tenant.id, source_resource_id=a.id,
                          target_resource_id=c.id, relationship_type="connected_to")
        await db.commit()

        summary = await repo.summary(tenant.id)
        assert summary["total"] == 3
        by_type = {t["relationship_type"]: t["count"] for t in summary["by_type"]}
        assert by_type["depends_on"] == 2
        assert by_type["connected_to"] == 1
        assert summary["by_status"].get("active") == 3


# ---------------------------------------------------------------------------
# Service tests (traversal)
# ---------------------------------------------------------------------------


class TestTopologyServiceTraversal:
    async def _chain(self, db, tenant_id: uuid.UUID):
        """A --depends_on--> B --depends_on--> C (A depends on B, B on C)."""
        a = await _make_device(db, tenant_id, "chain-a")
        b = await _make_device(db, tenant_id, "chain-b")
        c = await _make_device(db, tenant_id, "chain-c")
        repo = ResourceRelationshipRepository(db)
        await repo.upsert(tenant_id, source_resource_id=a.id,
                          target_resource_id=b.id, relationship_type="depends_on")
        await repo.upsert(tenant_id, source_resource_id=b.id,
                          target_resource_id=c.id, relationship_type="depends_on")
        await db.commit()
        return a, b, c

    async def test_upstream_dependencies(self, tenant: Tenant, db):
        a, b, c = await self._chain(db, tenant.id)
        service = TopologyService(db)

        upstream = await service.get_upstream_dependencies(a.id, tenant.id)
        names = {e["dependency_device_name"] for e in upstream}
        depths = {e["dependency_device_name"]: e["depth"] for e in upstream}

        assert names == {"chain-b", "chain-c"}
        assert depths["chain-b"] == 1
        assert depths["chain-c"] == 2

    async def test_downstream_blast_radius(self, tenant: Tenant, db):
        a, b, c = await self._chain(db, tenant.id)
        service = TopologyService(db)

        # If C fails, A and B are impacted (they depend on it transitively).
        downstream = await service.get_downstream_impact(c.id, tenant.id)
        names = {e["dependent_device_name"] for e in downstream}

        assert names == {"chain-a", "chain-b"}

    async def test_connected_devices_depth_1_only(self, tenant: Tenant, db):
        a, b, c = await self._chain(db, tenant.id)
        service = TopologyService(db)

        connected = await service.get_connected_devices(a.id, tenant.id)
        names = {n["device_name"] for n in connected}
        assert names == {"chain-b"}

    async def test_traversal_tenant_scoped(self, tenant: Tenant, db):
        """An edge belonging to another tenant must not appear in traversal."""
        a, b, _ = await self._chain(db, tenant.id)
        from datetime import datetime, timezone as _tz

        from app.models.resource_relationship import ResourceRelationship
        foreign_tenant = await _make_tenant(db)
        foreign_device = await _make_device(db, foreign_tenant.id, "foreign-node")
        # Foreign-tenant edge claiming the foreign device is a dependency of
        # tenant's device a. Traversal with tenant.id must NOT see it.
        edge = ResourceRelationship(
            tenant_id=foreign_tenant.id,
            source_resource_id=foreign_device.id,
            target_resource_id=a.id,
            relationship_type="depends_on",
            status="active",
            first_seen=datetime.now(_tz.utc),
            last_seen=datetime.now(_tz.utc),
        )
        db.add(edge)
        await db.commit()

        service = TopologyService(db)
        upstream = await service.get_upstream_dependencies(a.id, tenant.id)
        names = {e["dependency_device_name"] for e in upstream}
        # Own-tenant upstream intact; foreign node invisible.
        assert names == {"chain-b", "chain-c"}

    async def test_ignored_edge_types_not_traversed(self, tenant: Tenant, db):
        """Informational edges (managed_by etc.) do not carry outage impact."""
        a = await _make_device(db, tenant.id, "info-a")
        b = await _make_device(db, tenant.id, "info-b")
        repo = ResourceRelationshipRepository(db)
        await repo.upsert(tenant.id, source_resource_id=a.id,
                          target_resource_id=b.id, relationship_type="managed_by")
        await db.commit()

        service = TopologyService(db)
        assert await service.get_upstream_dependencies(a.id, tenant.id) == []
        assert await service.get_downstream_impact(b.id, tenant.id) == []
        # But connected (any type, depth 1) still shows it.
        connected = await service.get_connected_devices(a.id, tenant.id)
        assert len(connected) == 1

    async def test_max_depth_limit(self, tenant: Tenant, db):
        a, b, c = await self._chain(db, tenant.id)
        service = TopologyService(db)
        upstream = await service.get_upstream_dependencies(a.id, tenant.id, max_depth=1)
        names = {e["dependency_device_name"] for e in upstream}
        assert names == {"chain-b"}


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


class TestTopologyApi:
    async def test_crud_and_list(self, tenant: Tenant):
        async with AsyncSessionLocal() as db:
            a = await _make_device(db, tenant.id, "api-a")
            b = await _make_device(db, tenant.id, "api-b")
            await _seed_sub(db, tenant.id)
            a_id, b_id = str(a.id), str(b.id)

        headers = {
            "Authorization": "Bearer " + _jwt(tenant.id, {"topology.write"}, uuid.uuid4())
        }
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post(
                "/api/topology/relationships",
                headers=headers,
                json={
                    "source_resource_id": a_id,
                    "target_resource_id": b_id,
                    "relationship_type": "depends_on",
                    "label": "api edge",
                },
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            edge_id = body["id"]
            assert body["relationship_type"] == "depends_on"
            assert body["status"] == "active"

            resp = await client.get("/api/topology/relationships", headers=headers)
            assert resp.status_code == 200, resp.text
            listing = resp.json()
            assert listing["total"] >= 1
            assert any(item["id"] == edge_id for item in listing["items"])

            resp = await client.get(
                f"/api/topology/relationships/{edge_id}", headers=headers
            )
            assert resp.status_code == 200
            assert resp.json()["label"] == "api edge"

            resp = await client.patch(
                f"/api/topology/relationships/{edge_id}",
                headers=headers,
                json={"status": "degraded", "confidence": 80.0},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "degraded"

            resp = await client.delete(
                f"/api/topology/relationships/{edge_id}", headers=headers
            )
            assert resp.status_code == 200, resp.text

            resp = await client.get(
                f"/api/topology/relationships/{edge_id}", headers=headers
            )
            assert resp.status_code == 404

    async def test_create_invalid_type_400(self, tenant: Tenant):
        async with AsyncSessionLocal() as db:
            a = await _make_device(db, tenant.id, "bad-a")
            b = await _make_device(db, tenant.id, "bad-b")
            await _seed_sub(db, tenant.id)

        headers = {
            "Authorization": "Bearer " + _jwt(tenant.id, {"topology.write"}, uuid.uuid4())
        }
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post(
                "/api/topology/relationships",
                headers=headers,
                json={
                    "source_resource_id": str(a.id),
                    "target_resource_id": str(b.id),
                    "relationship_type": "nonexistent_type",
                },
            )
        assert resp.status_code == 400

    async def test_create_self_loop_400(self, tenant: Tenant):
        async with AsyncSessionLocal() as db:
            a = await _make_device(db, tenant.id, "self-a")
            await _seed_sub(db, tenant.id)

        headers = {
            "Authorization": "Bearer " + _jwt(tenant.id, {"topology.write"}, uuid.uuid4())
        }
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post(
                "/api/topology/relationships",
                headers=headers,
                json={
                    "source_resource_id": str(a.id),
                    "target_resource_id": str(a.id),
                    "relationship_type": "depends_on",
                },
            )
        assert resp.status_code == 400

    async def test_create_requires_subscription(self, tenant: Tenant):
        async with AsyncSessionLocal() as db:
            a = await _make_device(db, tenant.id, "nosub-a")
            b = await _make_device(db, tenant.id, "nosub-b")

        headers = {
            "Authorization": "Bearer " + _jwt(tenant.id, {"topology.write"}, uuid.uuid4())
        }
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post(
                "/api/topology/relationships",
                headers=headers,
                json={
                    "source_resource_id": str(a.id),
                    "target_resource_id": str(b.id),
                    "relationship_type": "depends_on",
                },
            )
        assert resp.status_code == 402, resp.text

    async def test_cross_tenant_get_404(self, tenant: Tenant):
        async with AsyncSessionLocal() as db:
            other_tenant = await _make_tenant(db)
            a = await _make_device(db, tenant.id, "cross-a")
            b = await _make_device(db, tenant.id, "cross-b")
            repo = ResourceRelationshipRepository(db)
            edge, _ = await repo.upsert(
                tenant.id, source_resource_id=a.id,
                target_resource_id=b.id, relationship_type="depends_on",
            )
            await db.commit()
            edge_id = str(edge.id)

        other_headers = {
            "Authorization": "Bearer " + _jwt(
                other_tenant.id, {"topology.write"}, uuid.uuid4()
            )
        }
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.get(
                f"/api/topology/relationships/{edge_id}", headers=other_headers
            )
        assert resp.status_code == 404

    async def test_traversal_endpoints(self, tenant: Tenant):
        async with AsyncSessionLocal() as db:
            a = await _make_device(db, tenant.id, "trav-a")
            b = await _make_device(db, tenant.id, "trav-b")
            c = await _make_device(db, tenant.id, "trav-c")
            repo = ResourceRelationshipRepository(db)
            await repo.upsert(tenant.id, source_resource_id=a.id,
                              target_resource_id=b.id, relationship_type="depends_on")
            await repo.upsert(tenant.id, source_resource_id=b.id,
                              target_resource_id=c.id, relationship_type="depends_on")
            await db.commit()
            a_id, b_id, c_id = str(a.id), str(b.id), str(c.id)

        headers = {
            "Authorization": "Bearer " + _jwt(tenant.id, {"topology.read"}, uuid.uuid4())
        }
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            upstream = await client.get(
                f"/api/topology/devices/{a_id}/upstream", headers=headers
            )
            assert upstream.status_code == 200, upstream.text
            up_data = upstream.json()
            assert up_data["direction"] == "upstream"
            affected = {n["device_id"] for n in up_data["affected"]}
            assert affected == {b_id, c_id}

            downstream = await client.get(
                f"/api/topology/devices/{c_id}/downstream", headers=headers
            )
            assert downstream.status_code == 200, downstream.text
            down_data = downstream.json()
            assert down_data["direction"] == "downstream"
            affected = {n["device_id"] for n in down_data["affected"]}
            assert affected == {a_id, b_id}

            connected = await client.get(
                f"/api/topology/devices/{a_id}/connected", headers=headers
            )
            assert connected.status_code == 200, connected.text
            conn_data = connected.json()
            neighbors = {n["device_id"] for n in conn_data["neighbors"]}
            assert neighbors == {b_id}

            summary = await client.get("/api/topology/summary", headers=headers)
            assert summary.status_code == 200, summary.text
            assert summary.json()["total_relationships"] >= 2

