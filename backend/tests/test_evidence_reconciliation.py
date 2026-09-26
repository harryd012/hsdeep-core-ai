"""Phase 9.10 - canonical evidence reconciliation tests.

These exercise ``RelationshipEvidenceService.reconcile_canonical`` (and the
existing materialisation it extends) against the real database: deterministic
grouping, idempotency, identity classification (resolved / unresolved /
ambiguous), provenance preservation, freshness, conflict reporting, tenant
isolation, and the promise that reconciliation never invents a resource, an
endpoint or a relationship.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models.device import Device
from app.models.interface import Interface
from app.models.resource_relationship import ResourceRelationship
from app.repositories.resource_relationship_repository import (
    ResourceRelationshipRepository,
)
from app.services.relationship_evidence_service import (
    FRESHNESS_FRESH,
    FRESHNESS_STALE,
    RESOLUTION_AMBIGUOUS,
    RESOLUTION_RESOLVED,
    RelationshipEvidenceService,
)
from app.services.infrastructure_intelligence import vocabulary as V

pytestmark = pytest.mark.asyncio

NOW = datetime.now(timezone.utc)


async def _device(db, tenant_id: uuid.UUID, name: str, address: str | None = None, **extra):
    device = Device(
        tenant_id=tenant_id,
        monitoring_source_id=None,
        external_id=f"{name}-{uuid.uuid4().hex[:6]}",
        name=name,
        device_type="switch",
        status="up",
        ip_address=address,
        **extra,
    )
    db.add(device)
    await db.flush()
    return device


async def _edge(
    db,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    *,
    discovered_by: str = "lldp",
    subtype: str | None = "lldp",
    relationship_type: str = "connected_to",
    local_interface: str | None = None,
    remote_interface: str | None = None,
    last_seen: datetime | None = None,
    attributes: dict | None = None,
):
    repo = ResourceRelationshipRepository(db)
    payload = {"evidence_type": subtype}
    if local_interface:
        payload["local_interface"] = local_interface
    if remote_interface:
        payload["remote_interface"] = remote_interface
    if attributes:
        payload.update(attributes)
    edge, _created = await repo.upsert(
        tenant_id,
        source_resource_id=source_id,
        target_resource_id=target_id,
        relationship_type=relationship_type,
        relationship_subtype=subtype,
        discovered_by=discovered_by,
        confidence=100.0,
        status="active",
        attributes=payload,
    )
    if last_seen is not None:
        await repo.update(edge, last_seen=last_seen)
    return edge


async def _count(db, model) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


async def _canonical_of(db, edge_id: uuid.UUID) -> dict | None:
    edge = (
        await db.execute(
            select(ResourceRelationship).where(ResourceRelationship.id == edge_id)
        )
    ).scalar_one()
    return (edge.attributes or {}).get("canonical")


class TestNoEvidenceAndIdempotency:
    async def test_an_empty_tenant_is_reported_honestly(self, tenant, db):
        report = await RelationshipEvidenceService(db).reconcile_canonical(tenant.id)
        assert report["evidence_records_seen"] == 0
        assert report["canonical_relationships"] == 0
        assert report["relationships_created"] == 0
        assert report["limitations"], "an empty tenant must explain itself"

    async def test_bidirectional_rows_become_one_canonical_relationship(self, tenant, db):
        a = await _device(db, tenant.id, "sw-a", "10.10.0.1")
        b = await _device(db, tenant.id, "sw-b", "10.10.0.2")
        await _edge(db, tenant.id, a.id, b.id, local_interface="Gi0/1", remote_interface="Te1/1")
        await _edge(db, tenant.id, b.id, a.id, local_interface="Te1/1", remote_interface="Gi0/1")
        await db.commit()

        report = await RelationshipEvidenceService(db).reconcile_canonical(tenant.id)
        assert report["evidence_records_seen"] == 2
        assert report["resolved"] == 2
        # One physical relationship, stored - and preserved - as two rows.
        assert report["canonical_relationships"] == 1
        assert report["relationships_created"] == 0

    async def test_reconciliation_is_idempotent(self, tenant, db):
        a = await _device(db, tenant.id, "sw-a", "10.10.1.1")
        b = await _device(db, tenant.id, "sw-b", "10.10.1.2")
        edge = await _edge(db, tenant.id, a.id, b.id, local_interface="Gi1/0/1")
        await db.commit()

        service = RelationshipEvidenceService(db)
        first = await service.reconcile_canonical(tenant.id, now=NOW)
        await db.commit()
        annotations = await _canonical_of(db, edge.id)
        second = await service.reconcile_canonical(tenant.id, now=NOW)
        await db.commit()

        assert first["relationships_updated"] == 1
        assert first["relationships_unchanged"] == 0
        # Unchanged evidence: nothing is written a second time.
        assert second["relationships_updated"] == 0
        assert second["relationships_unchanged"] == 1
        assert await _canonical_of(db, edge.id) == annotations
        assert annotations["resolution"] == RESOLUTION_RESOLVED
        assert annotations["freshness"] == FRESHNESS_FRESH

    async def test_dry_run_counts_without_writing(self, tenant, db):
        a = await _device(db, tenant.id, "sw-a", "10.10.2.1")
        b = await _device(db, tenant.id, "sw-b", "10.10.2.2")
        edge = await _edge(db, tenant.id, a.id, b.id, local_interface="Gi0/1")
        await db.commit()

        service = RelationshipEvidenceService(db)
        dry = await service.reconcile_canonical(tenant.id, dry_run=True, now=NOW)
        await db.commit()
        assert dry["dry_run"] is True
        assert dry["relationships_updated"] == 1
        assert await _canonical_of(db, edge.id) is None, "a dry run must write nothing"

        real = await service.reconcile_canonical(tenant.id, now=NOW)
        await db.commit()
        assert real["relationships_updated"] == 1
        assert await _canonical_of(db, edge.id) is not None


class TestIdentityResolution:
    async def test_an_endpoint_that_is_no_longer_a_device_is_unresolved(
        self, tenant, db
    ):
        a = await _device(db, tenant.id, "sw-a", "10.10.3.1")
        gone = await _device(db, tenant.id, "sw-gone", "10.10.3.2")
        edge = await _edge(db, tenant.id, a.id, gone.id, local_interface="Gi0/1")
        # The device was removed from the tenant (soft delete), so the recorded
        # evidence no longer resolves - it must be reported, not rewritten.
        gone.is_deleted = True
        await db.commit()

        report = await RelationshipEvidenceService(db).reconcile_canonical(tenant.id)
        assert report["unresolved"] == 1
        assert report["resolved"] == 0
        assert report["relationships_updated"] == 0
        assert await _canonical_of(db, edge.id) is None
        row = (
            await db.execute(
                select(ResourceRelationship).where(ResourceRelationship.id == edge.id)
            )
        ).scalar_one()
        assert row.target_resource_id == gone.id, "unresolved evidence is preserved as recorded"

    async def test_a_duplicated_identity_key_is_reported_as_ambiguous(self, tenant, db):
        # Two devices recording the SAME address: the resolver's deterministic
        # order still picks one, which is exactly why this must be visible.
        a = await _device(db, tenant.id, "sw-a", "10.10.4.1")
        b = await _device(db, tenant.id, "sw-b", "10.10.4.1")
        edge = await _edge(db, tenant.id, a.id, b.id, local_interface="Gi0/1")
        await db.commit()

        report = await RelationshipEvidenceService(db).reconcile_canonical(tenant.id, now=NOW)
        await db.commit()
        assert report["ambiguous"] == 1
        assert any("recorded on more than one device" in text for text in report["limitations"])
        annotations = await _canonical_of(db, edge.id)
        assert annotations["resolution"] == RESOLUTION_AMBIGUOUS

    async def test_reconciliation_creates_no_resource_interface_or_relationship(
        self, tenant, db
    ):
        a = await _device(db, tenant.id, "sw-a", "10.10.5.1")
        b = await _device(db, tenant.id, "sw-b", "10.10.5.2")
        await _edge(db, tenant.id, a.id, b.id, local_interface="Gi0/1")
        await db.commit()
        before = (
            await _count(db, Device),
            await _count(db, Interface),
            await _count(db, ResourceRelationship),
        )

        await RelationshipEvidenceService(db).reconcile_canonical(tenant.id, now=NOW)
        await db.commit()

        assert (
            await _count(db, Device),
            await _count(db, Interface),
            await _count(db, ResourceRelationship),
        ) == before


class TestProvenanceFreshnessAndConflicts:
    async def test_provenance_is_merged_and_the_existing_ledger_survives(self, tenant, db):
        a = await _device(db, tenant.id, "sw-a", "10.10.6.1")
        b = await _device(db, tenant.id, "sw-b", "10.10.6.2")
        # The same pair reported by two sources: one canonical relationship,
        # both sources retained on it.
        await _edge(db, tenant.id, a.id, b.id, discovered_by="lldp", subtype="lldp",
                    local_interface="Gi0/1", remote_interface="Gi0/2")
        await _edge(db, tenant.id, b.id, a.id, discovered_by="pnetlab_link",
                    subtype="pnetlab_link", local_interface="Gi0/2", remote_interface="Gi0/1")
        await db.commit()

        report = await RelationshipEvidenceService(db).reconcile_canonical(tenant.id, now=NOW)
        await db.commit()
        assert report["canonical_relationships"] == 1
        rows = (
            await db.execute(
                select(ResourceRelationship).where(
                    ResourceRelationship.tenant_id == tenant.id
                )
            )
        ).scalars().all()
        assert len(rows) == 2
        for row in rows:
            attributes = row.attributes or {}
            canonical = attributes["canonical"]
            assert canonical["members"] == 2
            assert canonical["evidence_sources"] == ["lldp", "pnetlab_link"]
            # The pre-existing evidence ledger is still there, untouched.
            assert attributes.get("evidence_type")
            assert canonical["direction"] in {"forward", "reverse"}

    async def test_freshness_uses_the_project_threshold_and_never_upgrades(self, tenant, db):
        a = await _device(db, tenant.id, "sw-a", "10.10.7.1")
        b = await _device(db, tenant.id, "sw-b", "10.10.7.2")
        c = await _device(db, tenant.id, "sw-c", "10.10.7.3")
        fresh = await _edge(db, tenant.id, a.id, b.id, local_interface="Gi0/1",
                            last_seen=NOW - timedelta(seconds=60))
        old = NOW - timedelta(seconds=V.TELEMETRY_STALE_AFTER_SECONDS * 4)
        stale = await _edge(db, tenant.id, a.id, c.id, local_interface="Gi0/2", last_seen=old)
        await db.commit()

        report = await RelationshipEvidenceService(db).reconcile_canonical(tenant.id, now=NOW)
        await db.commit()
        assert report["stale"] == 1
        assert (await _canonical_of(db, fresh.id))["freshness"] == FRESHNESS_FRESH
        assert (await _canonical_of(db, stale.id))["freshness"] == FRESHNESS_STALE
        # Stale evidence is not deleted, and re-running does not make it fresh.
        again = await RelationshipEvidenceService(db).reconcile_canonical(tenant.id, now=NOW)
        await db.commit()
        assert (await _canonical_of(db, stale.id))["freshness"] == FRESHNESS_STALE
        assert again["relationships_unchanged"] == 2

    async def test_one_port_claiming_two_peers_is_reported_as_a_conflict(self, tenant, db):
        a = await _device(db, tenant.id, "sw-a", "10.10.8.1")
        b = await _device(db, tenant.id, "sw-b", "10.10.8.2")
        c = await _device(db, tenant.id, "sw-c", "10.10.8.3")
        first = await _edge(db, tenant.id, a.id, b.id, discovered_by="lldp",
                            local_interface="Gi0/9")
        second = await _edge(db, tenant.id, a.id, c.id, discovered_by="pnetlab_link",
                             subtype="pnetlab_link", local_interface="Gi0/9")
        await db.commit()

        report = await RelationshipEvidenceService(db).reconcile_canonical(tenant.id, now=NOW)
        await db.commit()
        assert report["conflicts"] == 1
        sample = report["conflict_samples"][0]
        assert sample["kind"] == "interface_claims_multiple_peers"
        assert sorted(sample["target_ids"]) == sorted([str(b.id), str(c.id)])
        # Both claims are preserved and flagged; neither source was preferred.
        assert (await _canonical_of(db, first.id))["conflict"] is True
        assert (await _canonical_of(db, second.id))["conflict"] is True
        assert (await _count(db, ResourceRelationship)) >= 2

    async def test_sources_disagreeing_on_the_interface_are_reported(self, tenant, db):
        a = await _device(db, tenant.id, "sw-a", "10.10.9.1")
        b = await _device(db, tenant.id, "sw-b", "10.10.9.2")
        await _edge(db, tenant.id, a.id, b.id, discovered_by="lldp", local_interface="Gi0/1")
        await _edge(db, tenant.id, b.id, a.id, discovered_by="pnetlab_link",
                    subtype="pnetlab_link", local_interface="port2")
        await db.commit()

        report = await RelationshipEvidenceService(db).reconcile_canonical(tenant.id, now=NOW)
        assert any(
            sample["kind"] == "sources_disagree_on_interface"
            for sample in report["conflict_samples"]
        )


class TestTenantScopeAndRegression:
    async def test_reconciliation_is_tenant_scoped(self, tenant, db):
        from sqlalchemy import delete

        from app.models.tenant import Tenant

        other = Tenant(name="Phase910 Other", slug=f"p910-{uuid.uuid4().hex[:8]}")
        db.add(other)
        await db.flush()
        a = await _device(db, tenant.id, "sw-a", "10.10.10.1")
        b = await _device(db, tenant.id, "sw-b", "10.10.10.2")
        mine = await _edge(db, tenant.id, a.id, b.id, local_interface="Gi0/1")
        oa = await _device(db, other.id, "other-a", "10.20.0.1")
        ob = await _device(db, other.id, "other-b", "10.20.0.2")
        theirs = await _edge(db, other.id, oa.id, ob.id, local_interface="Gi0/1")
        await db.commit()
        try:
            report = await RelationshipEvidenceService(db).reconcile_canonical(tenant.id, now=NOW)
            await db.commit()
            assert report["evidence_records_seen"] == 1
            assert await _canonical_of(db, mine.id) is not None
            # The other tenant's evidence is neither counted nor annotated.
            assert await _canonical_of(db, theirs.id) is None
        finally:
            await db.execute(delete(Tenant).where(Tenant.id == other.id))
            await db.commit()

    async def test_existing_materialisation_still_works_and_feeds_the_canonical_view(
        self, tenant, db
    ):
        parent = await _device(db, tenant.id, "parent-sw", "10.10.11.1")
        child = await _device(db, tenant.id, "child-sw", "10.10.11.2",
                              parent_resource_id=parent.id)
        await db.commit()

        service = RelationshipEvidenceService(db)
        materialised = await service.materialize(tenant.id, dry_run=False, now=NOW)
        await db.commit()

        contains = [
            report for report in materialised["sources"]
            if report["written"] or report["planned"]
        ]
        assert contains, "materialisation should report the source it converted"
        # Phase 9.10 is additive: the existing report keeps its keys AND now
        # carries the canonical view of what was recorded.
        assert materialised["written_total"] >= 1
        assert materialised["canonical"]["canonical_relationships"] >= 1
        assert materialised["canonical"]["resolved"] >= 1
        rows = (
            await db.execute(
                select(ResourceRelationship).where(
                    ResourceRelationship.tenant_id == tenant.id
                )
            )
        ).scalars().all()
        target = [row for row in rows if row.target_resource_id == child.id]
        assert target and target[0].relationship_type == "contains"
        assert (target[0].attributes or {})["canonical"]["resolution"] == RESOLUTION_RESOLVED