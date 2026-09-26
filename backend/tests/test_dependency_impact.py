"""Phase 9.11 - dependency & impact over the canonical evidence graph."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import create_access_token
from app.main import app
from app.models.device import Device
from app.repositories.resource_relationship_repository import (
    ResourceRelationshipRepository,
)
from app.services.dependency_impact_service import (
    KIND_OPERATIONAL,
    KIND_PHYSICAL,
    KIND_UNCLASSIFIED,
    DependencyImpactService,
    ResourceNotFound,
    classify,
)
from app.services.infrastructure_intelligence import vocabulary as V

pytestmark = pytest.mark.asyncio

NOW = datetime.now(timezone.utc)


async def _device(db, tenant_id, name, parent=None):
    device = Device(
        tenant_id=tenant_id,
        monitoring_source_id=None,
        external_id=f"{name}-{uuid.uuid4().hex[:6]}",
        name=name,
        device_type="switch",
        status="up",
        parent_resource_id=parent,
    )
    db.add(device)
    await db.flush()
    return device


async def _edge(
    db,
    tenant_id,
    source_id,
    target_id,
    *,
    relationship_type="connected_to",
    discovered_by="lldp",
    local_interface=None,
):
    repo = ResourceRelationshipRepository(db)
    attributes = {"evidence_type": relationship_type}
    if local_interface:
        attributes["local_interface"] = local_interface
    edge, _ = await repo.upsert(
        tenant_id,
        source_resource_id=source_id,
        target_resource_id=target_id,
        relationship_type=relationship_type,
        relationship_subtype=relationship_type,
        discovered_by=discovered_by,
        confidence=100.0,
        status="active",
        attributes=attributes,
    )
    return edge


class TestClassification:
    async def test_adjacency_is_not_a_dependency(self):
        assert classify(V.REL_CONNECTED_TO) == KIND_PHYSICAL
        assert classify(V.REL_PEERS_WITH) == KIND_PHYSICAL
        assert classify(V.REL_HOSTED_ON) == KIND_OPERATIONAL
        assert classify(V.REL_DEPENDS_ON) == KIND_OPERATIONAL
        assert classify(V.REL_CONTAINS) == KIND_OPERATIONAL
        assert classify(V.REL_MONITORED_BY) == "monitoring"
        # An unexpected future type is never silently a dependency.
        assert classify("invented_future_type") == KIND_UNCLASSIFIED


class TestTraversal:
    async def test_a_switch_edge_is_impact_and_never_a_dependency(self, tenant, db):
        a = await _device(db, tenant.id, "sw-a")
        b = await _device(db, tenant.id, "sw-b")
        await _edge(db, tenant.id, a.id, b.id, local_interface="Gi0/1")
        await _edge(db, tenant.id, b.id, a.id, local_interface="Gi0/1")
        await db.commit()

        view = await DependencyImpactService(db).analyze(tenant.id, a.id, now=NOW)
        assert view["dependencies"] == [] and view["dependents"] == []
        assert view["summary"]["impacted_total"] == 1      # counted once, not twice
        assert view["impact"][0]["to_name"] == "sw-b"
        assert view["impact"][0]["kind"] == KIND_PHYSICAL
        assert view["summary"]["cycles_detected"] == 0     # one cable, not a loop

    async def test_upstream_downstream_and_transitive(self, tenant, db):
        host = await _device(db, tenant.id, "host-a")
        server = await _device(db, tenant.id, "srv-1")
        app = await _device(db, tenant.id, "app-1")
        await _edge(db, tenant.id, server.id, host.id, relationship_type=V.REL_HOSTED_ON,
                    discovered_by="provider_api")
        await _edge(db, tenant.id, app.id, server.id, relationship_type=V.REL_RUNS_ON,
                    discovered_by="provider_api")
        await db.commit()

        service = DependencyImpactService(db)
        middle = await service.analyze(tenant.id, server.id, now=NOW)
        assert [hop["to_name"] for hop in middle["dependencies"]] == ["host-a"]
        assert [hop["to_name"] for hop in middle["dependents"]] == ["app-1"]
        assert middle["summary"]["direct_dependencies"] == 1

        root = await service.analyze(tenant.id, host.id, now=NOW)
        assert [hop["to_name"] for hop in root["dependents"]] == ["srv-1", "app-1"]
        assert [hop["depth"] for hop in root["dependents"]] == [1, 2]
        assert root["summary"]["direct_dependents"] == 1

    async def test_containment_points_from_the_contained_to_the_container(self, tenant, db):
        parent = await _device(db, tenant.id, "cluster-1")
        child = await _device(db, tenant.id, "node-1")
        await _edge(db, tenant.id, parent.id, child.id, relationship_type=V.REL_CONTAINS,
                    discovered_by="provider_api")
        await db.commit()
        view = await DependencyImpactService(db).analyze(tenant.id, child.id, now=NOW)
        assert [hop["to_name"] for hop in view["dependencies"]] == ["cluster-1"]

    async def test_cycles_terminate_and_are_reported(self, tenant, db):
        a = await _device(db, tenant.id, "a")
        b = await _device(db, tenant.id, "b")
        c = await _device(db, tenant.id, "c")
        for source, target in ((a, b), (b, c), (c, a)):
            await _edge(db, tenant.id, source.id, target.id,
                        relationship_type=V.REL_DEPENDS_ON, discovered_by="provider_api")
        await db.commit()
        view = await DependencyImpactService(db).analyze(tenant.id, a.id, now=NOW)
        assert sorted(hop["to_name"] for hop in view["impact"]) == ["b", "c"]
        assert view["summary"]["cycles_detected"] >= 1

    async def test_max_depth_and_node_bounds(self, tenant, db):
        chain = [await _device(db, tenant.id, f"n{i}") for i in range(5)]
        for source, target in zip(chain, chain[1:]):
            await _edge(db, tenant.id, source.id, target.id,
                        relationship_type=V.REL_DEPENDS_ON, discovered_by="provider_api")
        await db.commit()
        service = DependencyImpactService(db)
        shallow = await service.analyze(tenant.id, chain[0].id, max_depth=1, now=NOW)
        assert [hop["to_name"] for hop in shallow["impact"]] == ["n1"]
        assert shallow["summary"]["max_depth_hit"] is True
        capped = await service.analyze(tenant.id, chain[0].id, max_nodes=2, now=NOW)
        assert capped["summary"]["truncated"] is True

    async def test_results_are_deterministic(self, tenant, db):
        a = await _device(db, tenant.id, "a")
        b = await _device(db, tenant.id, "b")
        c = await _device(db, tenant.id, "c")
        for target in (b, c):
            await _edge(db, tenant.id, target.id, a.id,
                        relationship_type=V.REL_DEPENDS_ON, discovered_by="provider_api")
        await db.commit()
        service = DependencyImpactService(db)
        first = await service.analyze(tenant.id, a.id, now=NOW)
        second = await service.analyze(tenant.id, a.id, now=NOW)
        assert first == second


class TestEvidenceAndHonesty:
    async def test_hop_carries_provenance_and_freshness(self, tenant, db):
        a = await _device(db, tenant.id, "a")
        b = await _device(db, tenant.id, "b")
        await _edge(db, tenant.id, b.id, a.id, relationship_type=V.REL_DEPENDS_ON,
                    discovered_by="provider_api", local_interface="Gi0/3")
        await db.commit()
        view = await DependencyImpactService(db).analyze(tenant.id, a.id, now=NOW)
        hop = view["dependents"][0]
        assert hop["discovered_by"] == "provider_api"
        # The hop reads in the traversed direction (a -> b), so the row's local
        # port appears on the far side; it is never dropped.
        assert "Gi0/3" in (hop["local_interface"], hop["remote_interface"])
        assert hop["confidence"] == 100.0
        assert hop["is_inferred"] is False
        assert hop["resolution"] == "resolved"
        assert hop["freshness"] == "fresh"
        assert view["freshness"]["worst"] == "fresh"

    async def test_an_unresolvable_endpoint_is_reported_not_traversed(self, tenant, db):
        a = await _device(db, tenant.id, "a")
        gone = await _device(db, tenant.id, "gone")
        await _edge(db, tenant.id, gone.id, a.id, relationship_type=V.REL_DEPENDS_ON,
                    discovered_by="provider_api")
        gone.is_deleted = True
        await db.commit()
        view = await DependencyImpactService(db).analyze(tenant.id, a.id, now=NOW)
        assert view["dependents"] == []
        assert view["summary"]["unresolved_evidence_total"] == 1
        assert view["unresolved_evidence"][0]["relationship_type"] == V.REL_DEPENDS_ON

    async def test_empty_tenant_and_unknown_resource(self, tenant, db):
        a = await _device(db, tenant.id, "lonely")
        await db.commit()
        view = await DependencyImpactService(db).analyze(tenant.id, a.id, now=NOW)
        assert view["dependencies"] == [] and view["impact"] == []
        assert any("No relationship rows" in text for text in view["limitations"])
        with pytest.raises(ResourceNotFound):
            await DependencyImpactService(db).analyze(tenant.id, uuid.uuid4(), now=NOW)

    async def test_traversal_never_crosses_tenants(self, tenant, db):
        from sqlalchemy import delete

        from app.models.tenant import Tenant

        other = Tenant(name="Phase911 Other", slug=f"p911-{uuid.uuid4().hex[:8]}")
        db.add(other)
        await db.flush()
        mine = await _device(db, tenant.id, "mine")
        theirs = await _device(db, other.id, "their-parent")
        await _edge(db, other.id, theirs.id, mine.id,
                    relationship_type=V.REL_DEPENDS_ON, discovered_by="provider_api")
        await db.commit()
        try:
            view = await DependencyImpactService(db).analyze(tenant.id, mine.id, now=NOW)
            assert view["dependencies"] == []
        finally:
            await db.execute(delete(Tenant).where(Tenant.id == other.id))
            await db.commit()

    async def test_endpoint_requires_auth_and_is_tenant_scoped(self, tenant, db):
        a = await _device(db, tenant.id, "api-a")
        b = await _device(db, tenant.id, "api-b")
        await _edge(db, tenant.id, b.id, a.id, relationship_type=V.REL_DEPENDS_ON,
                    discovered_by="provider_api")
        await db.commit()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            anonymous = await client.get(f"/api/infrastructure/dependencies/{a.id}")
            assert anonymous.status_code == 401

            token = create_access_token(
                user_id=uuid.uuid4(), tenant_id=tenant.id, email="p911@example.com",
                is_superuser=True, permissions=frozenset(),
            )
            ok = await client.get(
                f"/api/infrastructure/dependencies/{a.id}?tenant_id={tenant.id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert ok.status_code == 200, ok.text
            body = ok.json()
            assert body["resource"]["name"] == "api-a"
            assert set(body) >= {
                "dependencies", "dependents", "impact", "summary", "evidence_sources",
                "freshness", "unresolved_evidence", "limitations",
            }
            assert body["dependents"][0]["to_name"] == "api-b"

            stranger = create_access_token(
                user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), email="stranger@example.com",
                is_superuser=True, permissions=frozenset(),
            )
            denied = await client.get(
                f"/api/infrastructure/dependencies/{a.id}?tenant_id={tenant.id}",
                headers={"Authorization": f"Bearer {stranger}"},
            )
            assert denied.status_code == 404