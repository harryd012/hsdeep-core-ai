"""
Phase 9.6 passive relationship-evidence materialisation tests.

The load-bearing claims:

  1. NOTHING IS INVENTED - only recorded rows produce edges; an endpoint that
     does not resolve to a device in the same tenant produces none, and the run
     explains itself.
  2. IDEMPOTENT - re-running re-observes the same edge (ledger + last_seen)
     instead of duplicating it.
  3. TENANT-SCOPED - one tenant's evidence can never create or touch another
     tenant's rows.
  4. USEFUL - a materialised edge is immediately rendered by the Phase 9.5 live
     topology read model.

Uses the real app and database (project convention).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.bgp_neighbor import BgpNeighbor
from app.models.device import Device
from app.services.infrastructure_intelligence import vocabulary as V
from app.services.relationship_evidence_service import RelationshipEvidenceService
from app.services.topology_graph_service import PREFIX_DEVICE
from app.services.topology_live_service import TopologyLiveService

# The materialisation endpoint is a write route: it is gated on an active
# subscription, exactly like the other topology write routes.
pytestmark = pytest.mark.usefixtures("seeded_subscription")

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


async def _add_device(
    tenant_id: uuid.UUID,
    name: str,
    *,
    ip_address: str | None = None,
    parent_resource_id: uuid.UUID | None = None,
    is_deleted: bool = False,
) -> uuid.UUID:
    device_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            Device(
                id=device_id,
                tenant_id=tenant_id,
                name=name,
                device_type="switch",
                status="up",
                ip_address=ip_address,
                parent_resource_id=parent_resource_id,
                is_deleted=is_deleted,
            )
        )
        await db.commit()
    return device_id


async def _add_bgp(
    tenant_id: uuid.UUID,
    resource_id: uuid.UUID,
    neighbor_ip: str,
    *,
    state: str = "established",
    vrf: str = "default",
    remote_as: str | None = "64512",
    local_as: str | None = "64500",
    local_ip: str | None = None,
    description: str | None = None,
    prefixes_received: int | None = 12,
) -> uuid.UUID:
    row_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            BgpNeighbor(
                id=row_id,
                tenant_id=tenant_id,
                resource_id=resource_id,
                vrf=vrf,
                neighbor_ip=neighbor_ip,
                local_ip=local_ip,
                remote_as=remote_as,
                local_as=local_as,
                description=description,
                state=state,
                admin_status="up",
                prefixes_received=prefixes_received,
                first_seen_at=NOW,
                last_seen_at=NOW,
                attributes={},
            )
        )
        await db.commit()
    return row_id


async def _edges_for(tenant_id: uuid.UUID) -> list[dict]:
    """Every relationship row this tenant has, read back through the repository."""
    async with AsyncSessionLocal() as db:
        service = RelationshipEvidenceService(db)
        devices = await service.devices.list(tenant_id, limit=1_000)
        by_id = {device.id: device for device in devices}
        edges = await service.relationships.list(tenant_id, limit=1_000)
        return [
            {
                "id": edge.id,
                "source": edge.source_resource_id,
                "target": edge.target_resource_id,
                "type": edge.relationship_type,
                "subtype": edge.relationship_subtype,
                "discovered_by": edge.discovered_by,
                "confidence": edge.confidence,
                "is_inferred": edge.is_inferred,
                "status": edge.status,
                "attributes": dict(edge.attributes or {}),
                "endpoints_are_devices": (
                    edge.source_resource_id in by_id and edge.target_resource_id in by_id
                ),
            }
            for edge in edges
        ]


async def _materialize(db, tenant_id: uuid.UUID, **kwargs):
    """Run the service the way the endpoint does, then commit.

    The endpoint commits before returning (``get_db`` does not), and committing
    here also releases the FK ``KEY SHARE`` locks our inserts hold on the device
    rows - without it the tenant fixture's teardown DELETE deadlocks against this
    session.
    """
    service = RelationshipEvidenceService(db)
    report = await service.materialize(tenant_id, **kwargs)
    await db.commit()
    return report


class TestNothingIsInvented:
    async def test_no_recorded_evidence_writes_nothing_and_says_why(self, db, tenant):
        await _add_device(tenant.id, "p96-plain")
        report = await _materialize(db, tenant.id)

        assert report["candidates_total"] == 0
        assert report["planned_total"] == 0
        assert report["written_total"] == 0
        assert await _edges_for(tenant.id) == []
        assert any(
            "No recorded adjacency evidence" in line for line in report["limitations"]
        )
        # Every source explains why it contributed nothing.
        for source in report["sources"]:
            assert source["note"], source

    async def test_an_endpoint_outside_the_queried_scope_is_unresolved(self, db, tenant):
        """A soft-deleted parent must not become an edge endpoint."""
        parent = await _add_device(tenant.id, "p96-dead-parent", is_deleted=True)
        await _add_device(tenant.id, "p96-orphan", parent_resource_id=parent)

        report = await _materialize(db, tenant.id)
        assert report["unresolved_total"] == 1
        assert report["written_total"] == 0
        assert await _edges_for(tenant.id) == []

    async def test_a_bgp_peer_address_matching_no_device_is_skipped(self, db, tenant):
        local = await _add_device(tenant.id, "p96-bgp-local", ip_address="10.96.0.1")
        await _add_bgp(tenant.id, local, "203.0.113.9")

        report = await _materialize(db, tenant.id)
        assert report["written_total"] == 0
        assert report["unresolved_total"] == 1


class TestRecordedStructureBecomesAnEdge:
    def test_inventory_evidence_is_deliberately_unclassified(self):
        # Nothing recorded HOW the binding was obtained, so the UI shows
        # "Unclassified" rather than claiming HSDEEP observed it itself.
        assert V.relationship_evidence_classification(V.EVIDENCE_SOURCE_INVENTORY) is None
        assert V.EVIDENCE_SOURCE_INVENTORY in V.RELATIONSHIP_EVIDENCE_SOURCES

    async def test_containment_creates_a_contains_edge_with_recorded_provenance(
        self, db, tenant
    ):
        parent = await _add_device(tenant.id, "p96-parent", ip_address="10.96.1.1")
        child = await _add_device(tenant.id, "p96-child", parent_resource_id=parent)

        report = await _materialize(db, tenant.id)
        assert report["written_total"] == 1

        edges = [e for e in await _edges_for(tenant.id) if e["type"] == V.REL_CONTAINS]
        assert len(edges) == 1
        edge = edges[0]
        assert edge["source"] == parent
        assert edge["target"] == child
        assert edge["discovered_by"] == V.EVIDENCE_SOURCE_INVENTORY
        assert edge["confidence"] == 100.0
        assert edge["is_inferred"] is False
        assert edge["status"] == V.RELATIONSHIP_ACTIVE
        assert edge["attributes"]["recorded_field"] == "devices.parent_resource_id"
        assert edge["endpoints_are_devices"] is True

    async def test_containment_is_idempotent(self, db, tenant):
        parent = await _add_device(tenant.id, "p96-parent")
        await _add_device(tenant.id, "p96-child", parent_resource_id=parent)

        first = await _materialize(db, tenant.id)
        second = await _materialize(db, tenant.id)

        assert first["written_total"] == 1
        assert second["written_total"] == 0
        assert second["refreshed_total"] == 1
        edges = await _edges_for(tenant.id)
        assert len(edges) == 1  # upsert on the unique key: never duplicated
        ledger = edges[0]["attributes"].get(V.RELATIONSHIP_EVIDENCE_LEDGER_KEY, {})
        assert V.EVIDENCE_SOURCE_INVENTORY in ledger

    async def test_bgp_peering_resolves_a_recorded_peer_address(self, db, tenant):
        local = await _add_device(tenant.id, "p96-bgp-a", ip_address="10.96.2.1")
        peer = await _add_device(tenant.id, "p96-bgp-b", ip_address="10.96.2.2")
        await _add_bgp(tenant.id, local, "10.96.2.2", description="transit")

        report = await _materialize(db, tenant.id)
        assert report["written_total"] == 1

        edge = [e for e in await _edges_for(tenant.id) if e["type"] == V.REL_PEERS_WITH][0]
        assert edge["source"] == local
        assert edge["target"] == peer
        assert edge["subtype"] == "bgp"
        assert edge["discovered_by"] == V.EVIDENCE_SOURCE_BGP
        assert edge["status"] == V.RELATIONSHIP_ACTIVE
        assert edge["attributes"]["remote_as"] == "64512"
        assert edge["attributes"]["vrf"] == "default"
        # A recorded BGP adjacency is ACTIVE evidence in the Phase 9.4 table.
        assert (
            V.relationship_evidence_classification(V.EVIDENCE_SOURCE_BGP)
            == V.EVIDENCE_ACTIVE
        )

    async def test_a_non_established_session_is_not_reported_as_active(self, db, tenant):
        local = await _add_device(tenant.id, "p96-bgp-a", ip_address="10.96.3.1")
        await _add_device(tenant.id, "p96-bgp-b", ip_address="10.96.3.2")
        await _add_bgp(tenant.id, local, "10.96.3.2", state="idle")

        await _materialize(db, tenant.id)
        edge = [e for e in await _edges_for(tenant.id) if e["type"] == V.REL_PEERS_WITH][0]
        # "not established" is not the same claim as "failed".
        assert edge["status"] == "unknown"
        assert edge["attributes"]["state"] == "idle"

    async def test_dry_run_plans_without_writing(self, db, tenant):
        parent = await _add_device(tenant.id, "p96-parent")
        await _add_device(tenant.id, "p96-child", parent_resource_id=parent)

        plan = await _materialize(db, tenant.id, dry_run=True)
        assert plan["dry_run"] is True
        assert plan["planned_total"] == 1
        assert plan["written_total"] == 0
        assert await _edges_for(tenant.id) == []

        real = await _materialize(db, tenant.id)
        assert real["written_total"] == 1


@pytest_asyncio.fixture
async def other_tenant():
    """A second real tenant, deleted at teardown (cascades to its devices)."""
    from sqlalchemy import delete

    from app.models.tenant import Tenant

    async with AsyncSessionLocal() as db:
        tenant = Tenant(name="P96 Other", slug=f"p96-{uuid.uuid4().hex[:8]}")
        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)
    yield tenant
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == tenant.id))
        await db.commit()


class TestTenantIsolation:
    async def test_evidence_never_crosses_a_tenant_boundary(
        self, db, tenant, other_tenant
    ):
        parent = await _add_device(tenant.id, "p96-mine-parent")
        await _add_device(tenant.id, "p96-mine-child", parent_resource_id=parent)
        their_parent = await _add_device(other_tenant.id, "p96-their-parent")
        await _add_device(other_tenant.id, "p96-their-child", parent_resource_id=their_parent)

        mine = await _materialize(db, tenant.id)
        theirs = await _materialize(db, other_tenant.id)

        assert mine["written_total"] == 1
        assert theirs["written_total"] == 1
        my_edges = await _edges_for(tenant.id)
        their_edges = await _edges_for(other_tenant.id)
        assert len(my_edges) == 1 and len(their_edges) == 1
        assert my_edges[0]["id"] != their_edges[0]["id"]
        assert my_edges[0]["source"] == parent
        assert their_edges[0]["source"] == their_parent


class TestTheGraphRendersTheEdge:
    async def test_a_materialised_edge_appears_in_the_live_topology(self, db, tenant):
        parent = await _add_device(tenant.id, "p96-core", ip_address="10.96.9.1")
        child = await _add_device(tenant.id, "p96-access", parent_resource_id=parent)
        await _materialize(db, tenant.id)

        graph = await TopologyLiveService(db).build(tenant.id)
        assert graph["edges"], "the materialised edge must reach the graph"
        edge = graph["edges"][0]
        assert edge["source_id"] == f"{PREFIX_DEVICE}{parent}"
        assert edge["target_id"] == f"{PREFIX_DEVICE}{child}"
        assert edge["relationship_type"] == V.REL_CONTAINS
        assert edge["discovered_by"] == V.EVIDENCE_SOURCE_INVENTORY
        assert edge["live"]["classification"] is None  # consumed, never guessed
        assert graph["status"] != "not_discovered"


class TestMaterializeEndpoint:
    async def test_requires_authentication(self, tenant):
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/api/topology/relationships/materialize")
        assert response.status_code == 401

    async def test_dry_run_is_the_default_and_writes_nothing(self, tenant):
        parent = await _add_device(tenant.id, "p96-api-parent")
        await _add_device(tenant.id, "p96-api-child", parent_resource_id=parent)
        token = create_access_token(
            user_id=uuid.uuid4(),
            tenant_id=tenant.id,
            email="p96@hsdeep.local",
            is_superuser=False,
            permissions=frozenset(),
        )
        async with httpx.AsyncClient(
            app=app,
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            response = await client.post("/api/topology/relationships/materialize")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["dry_run"] is True
        assert body["planned_total"] == 1
        assert body["written_total"] == 0
        assert await _edges_for(tenant.id) == []