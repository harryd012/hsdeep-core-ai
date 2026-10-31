"""
Operational evidence + infrastructure graph hardening (Phase 7).

Two layers:

* **Pure** cases pin the vocabularies and derivations that decide what we are
  allowed to claim about a resource: identity stage, monitoring binding,
  telemetry freshness, evidence provenance and relationship evidence state.
* **Database** cases drive the real discovery service and assert the invariants
  the whole chain depends on: evidence is separated from canonical resources,
  identity does not duplicate, bindings map to real sources, relationship
  evidence carries provenance, duplicates are prevented, tenant isolation holds,
  and Failover Intelligence can still consume the result.

Every assertion is about honesty: a missing signal must never be reported as a
healthy one, and evidence must never be dressed up as an identified resource.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx

from app.core.db import AsyncSessionLocal
from app.models.monitoring_source import MonitoringSource
from app.repositories.device_repository import DeviceRepository
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.resource_relationship_repository import (
    ResourceRelationshipRepository,
)
from app.services.discovery.evidence import DiscoveryEvidence, NeighborEvidence
from app.services.discovery.service import DiscoveryService
from app.services.infrastructure_intelligence import operational_state as O
from app.services.infrastructure_intelligence import telemetry as T
from app.services.infrastructure_intelligence import vocabulary as V
from app.services.infrastructure_intelligence.service import (
    UniversalInfrastructureService,
)

# async tests are collected automatically (asyncio_mode = "auto"): no module-level
# mark, so the pure sync cases stay ordinary tests.
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def facts(**overrides) -> O.ResourceFacts:
    base = {
        "resource_id": "r-1",
        "resource_type": "firewall",
        "identity_key": "FDO1234",
        "recognized": True,
        "monitoring_source_id": "source-1",
        "monitoring_source_active": True,
        "supported_capabilities": ("availability",),
        "last_telemetry_at": NOW,
    }
    base.update(overrides)
    return O.ResourceFacts(**base)


async def make_source(db, tenant_id, *, name="prtg", active=True) -> MonitoringSource:
    source = MonitoringSource(
        tenant_id=tenant_id,
        name=f"{name}-{uuid.uuid4().hex[:6]}",
        source_type="prtg",
        is_active=active,
        poll_interval_seconds=60,
        status="connected",
    )
    db.add(source)
    await db.flush()
    return source


def discovered_device(*, address="10.0.0.5", **overrides) -> DiscoveryEvidence:
    base = {
        "address": address,
        "transport": "snmp",
        "provider": "fortigate",
        "source": "prtg",
        "hostname": "branch-fw-01",
        "sys_descr": "FortiGate-60F",
        "model": "FortiGate-60F",
        "serial": "FDO1234ABCD",
        "vendor": "Fortinet",
        "device_type": "firewall",
        "reachable": True,
        "raw_evidence": {"authentication": "success"},
    }
    base.update(overrides)
    return DiscoveryEvidence(**base)


# ---------------------------------------------------------------------------
# Telemetry freshness
# ---------------------------------------------------------------------------
def test_freshness_states_cover_the_whole_scale():
    def state_for(age_seconds):
        return O.assess_freshness(
            facts(last_telemetry_at=NOW - timedelta(seconds=age_seconds)), now=NOW
        ).state

    assert state_for(10) == V.FRESHNESS_FRESH
    assert state_for(V.TELEMETRY_AGING_AFTER_SECONDS + 60) == V.FRESHNESS_AGING
    assert state_for(V.TELEMETRY_STALE_AFTER_SECONDS + 60) == V.FRESHNESS_STALE
    assert state_for(V.TELEMETRY_EXPIRED_AFTER_SECONDS + 60) == V.FRESHNESS_EXPIRED


def test_missing_telemetry_is_never_reported_as_current():
    bound = O.assess_freshness(facts(last_telemetry_at=None), now=NOW)
    assert bound.state == V.FRESHNESS_NEVER_COLLECTED
    assert bound.is_current is False
    assert bound.reason == V.REASON_FRESHNESS_NEVER_COLLECTED

    unbound = O.assess_freshness(
        facts(last_telemetry_at=None, monitoring_source_id=None), now=NOW
    )
    assert unbound.state == V.FRESHNESS_UNAVAILABLE
    assert unbound.reason == V.REASON_FRESHNESS_UNAVAILABLE


def test_collector_failure_is_distinguishable_from_no_collector():
    failing = O.assess_freshness(
        facts(last_telemetry_at=None, last_collection_status=V.COLLECTION_FAILED),
        now=NOW,
    )
    assert failing.state == V.FRESHNESS_NEVER_COLLECTED
    assert failing.reason == V.REASON_COLLECTOR_FAILED


def test_missing_telemetry_is_not_converted_to_zero_or_healthy():
    """A resource with no sample must not look like a healthy one."""
    state = O.derive_operational_state(facts(last_telemetry_at=None), now=NOW)
    assert state.freshness.is_current is False
    assert state.is_operational_resource is False
    assert state.identity_stage == V.STAGE_COLLECTABLE  # never "monitored"


# ---------------------------------------------------------------------------
# Identity stage ladder
# ---------------------------------------------------------------------------
def test_unknown_probe_stays_at_discovered():
    state = O.derive_operational_state(
        O.ResourceFacts(
            resource_id="r-x",
            resource_type="unknown",
            identity_key=None,
            recognized=False,
            monitoring_source_id=None,
        ),
        now=NOW,
    )
    assert state.identity_stage == V.STAGE_DISCOVERED
    assert state.is_operational_resource is False
    assert any("identity key" in blocker for blocker in state.blockers)
    assert any("monitoring binding" in blocker for blocker in state.blockers)


def test_ladder_advances_only_with_the_evidence_for_each_rung():
    identified = O.derive_operational_state(
        facts(
            resource_type="unknown",
            monitoring_source_id=None,
            supported_capabilities=(),
        ),
        now=NOW,
    )
    assert identified.identity_stage == V.STAGE_IDENTIFIED

    classified = O.derive_operational_state(
        facts(monitoring_source_id=None, supported_capabilities=()), now=NOW
    )
    assert classified.identity_stage == V.STAGE_CLASSIFIED

    capability_resolved = O.derive_operational_state(
        facts(monitoring_source_id=None), now=NOW
    )
    assert capability_resolved.identity_stage == V.STAGE_CAPABILITY_RESOLVED

    collectable = O.derive_operational_state(facts(last_telemetry_at=None), now=NOW)
    assert collectable.identity_stage == V.STAGE_COLLECTABLE

    monitored = O.derive_operational_state(facts(), now=NOW)
    assert monitored.identity_stage == V.STAGE_MONITORED
    assert monitored.is_operational_resource is True


def test_was_monitored_but_telemetry_expired_is_stale_not_monitored():
    state = O.derive_operational_state(
        facts(last_telemetry_at=NOW - timedelta(days=2)), now=NOW
    )
    assert state.identity_stage == V.STAGE_STALE
    assert state.is_operational_resource is False
    assert any("expired" in blocker for blocker in state.blockers)


def test_retired_lifecycle_is_terminal():
    state = O.derive_operational_state(
        facts(lifecycle_state=V.LIFECYCLE_RETIRED), now=NOW
    )
    assert state.identity_stage == V.STAGE_RETIRED
    assert state.is_operational_resource is False


# ---------------------------------------------------------------------------
# Monitoring binding
# ---------------------------------------------------------------------------
def test_binding_states():
    assert O.resolve_binding(facts()).state == V.BINDING_BOUND
    assert O.resolve_binding(facts(monitoring_source_id=None)).state == V.BINDING_UNBOUND
    assert (
        O.resolve_binding(facts(monitoring_source_active=False)).state
        == V.BINDING_UNBOUND
    )


def test_competing_sources_are_reported_as_ambiguous_not_silently_resolved():
    binding = O.resolve_binding(facts(competing_source_ids=("source-9",)))
    assert binding.state == V.BINDING_AMBIGUOUS
    assert binding.is_bound is False
    assert "not provable" in binding.detail


def test_provider_identifiers_are_preserved_on_the_binding():
    binding = O.resolve_binding(
        facts(provider="fortigate", provider_resource_id="FG-100")
    )
    payload = binding.to_dict()
    assert payload["provider_resource_id"] == "FG-100"
    assert payload["provider"] == "fortigate"


# ---------------------------------------------------------------------------
# Operational evidence contract
# ---------------------------------------------------------------------------
def test_evidence_contract_carries_every_required_field():
    record = T.CanonicalTelemetry(
        capability="availability",
        metric_name="availability_pct",
        timestamp=NOW,
        resource_id=uuid.uuid4(),
        provider="fortigate",
        numeric_value=99.5,
        unit="%",
        source_timestamp=NOW,
    )
    evidence = record.to_evidence(now=NOW)
    for key in (
        "resource_id",
        "timestamp",
        "source",
        "metric",
        "value",
        "unit",
        "quality",
        "freshness",
        "provenance",
    ):
        assert key in evidence, key
    assert evidence["provenance"] == T.PROVENANCE_OBSERVED
    assert evidence["freshness"] == V.FRESHNESS_FRESH

    stale = T.CanonicalTelemetry(
        capability="availability",
        metric_name="availability_pct",
        timestamp=NOW - timedelta(hours=1),
        source_timestamp=NOW - timedelta(hours=1),
    )
    assert stale.to_evidence(now=NOW)["freshness"] == V.FRESHNESS_STALE


def test_evidence_provenance_vocabulary_is_shared_with_failover():
    from app.services.failover_intelligence import evidence as F
    from app.services.failover_intelligence import replay as R

    assert F.PROVENANCE_STATES == T.PROVENANCE_STATES
    assert F.PROVENANCE_OBSERVED == T.PROVENANCE_OBSERVED
    # Replay keeps its own tokens and exposes the shared ones alongside them.
    assert R.PROVENANCE_ALIASES[R.PROVENANCE_CAPTURED] == T.PROVENANCE_OBSERVED
    assert R.PROVENANCE_ALIASES[R.PROVENANCE_CONTEXT] == T.PROVENANCE_CONTEXT


def test_failover_evidence_items_declare_provenance_per_kind():
    from app.services.failover_intelligence import evidence as F

    link = type(
        "L", (), {"name": "wan-1", "oper_status": "up", "admin_status": "up"}
    )()
    items = F.collect(link_from=link, candidate_links=[link], now=NOW)
    by_kind = {item["kind"]: item["provenance"] for item in items}
    assert by_kind["link_from_state"] == F.PROVENANCE_OBSERVED
    assert by_kind["redundancy_depth"] == F.PROVENANCE_DERIVED
    # "we captured no routing changes" is about OUR collection, not the device.
    assert by_kind["route_transition"] == F.PROVENANCE_CONTEXT


# ---------------------------------------------------------------------------
# Discovery -> canonical resource
# ---------------------------------------------------------------------------
async def test_discovery_separates_probe_evidence_from_the_canonical_resource(
    tenant, db
):
    source = await make_source(db, tenant.id)
    result = await DiscoveryService(db).discover(
        tenant_id=tenant.id,
        monitoring_source_id=source.id,
        evidence=[discovered_device()],
    )

    assert result.created == 1
    outcome = result.resources[0]
    assert outcome.persisted is True
    assert outcome.recognition_status == "identified"
    assert outcome.identity_stage == "classified"
    # Evidence is retained verbatim, including the authentication outcome.
    assert outcome.probe_evidence["serial"] == "FDO1234ABCD"
    assert outcome.probe_evidence["authentication"] == "success"
    assert outcome.probe_evidence["transport"] == "snmp"
    # The canonical resource is a persisted identity, distinctly reported.
    assert outcome.canonical_resource is not None
    assert outcome.canonical_resource["created"] is True
    assert outcome.canonical_resource["external_id"] == outcome.external_id
    assert outcome.device_id == outcome.canonical_resource["device_id"]

    payload = result.as_dict()
    assert payload["persisted"] == 1
    assert payload["evidence_only"] == 0
    assert payload["recognized"] == 1
    # No adjacency evidence was observed, and that is stated rather than implied.
    assert payload["no_topology_evidence"] == 1


async def test_unrecognised_probe_is_evidence_not_a_classified_resource(tenant, db):
    source = await make_source(db, tenant.id)
    # Nothing but a reachable address: no hostname, vendor, model or serial.
    evidence = DiscoveryEvidence(
        address="10.9.9.9",
        transport="icmp",
        reachable=True,
        source="prtg",
        raw_evidence={"authentication": "not_attempted"},
    )
    result = await DiscoveryService(db).discover(
        tenant_id=tenant.id,
        monitoring_source_id=source.id,
        evidence=[evidence],
    )
    outcome = result.resources[0]
    assert outcome.recognition_status == "unknown"
    assert outcome.resource_type == "unknown"
    # The evidence survives even though nothing could be recognised.
    assert outcome.probe_evidence["address"] == "10.9.9.9"
    assert outcome.probe_evidence["reachable"] is True
    assert "note" in outcome.probe_evidence
    # It is NOT presented as a classified resource.
    assert outcome.identity_stage in {V.STAGE_DISCOVERED, V.STAGE_IDENTIFIED}
    assert outcome.device_type == "unknown"


async def test_the_same_device_seen_by_two_sources_is_not_duplicated(tenant, db):
    first = await make_source(db, tenant.id, name="snmp")
    second = await make_source(db, tenant.id, name="api")
    service = DiscoveryService(db)

    await service.discover(
        tenant_id=tenant.id,
        monitoring_source_id=first.id,
        evidence=[discovered_device()],
    )
    second_run = await service.discover(
        tenant_id=tenant.id,
        monitoring_source_id=second.id,
        evidence=[discovered_device(address="10.0.0.6")],
    )

    assert second_run.updated == 1
    assert second_run.created == 0
    devices = await DeviceRepository(db).list(tenant.id, limit=50)
    with_serial = [d for d in devices if d.serial_number == "FDO1234ABCD"]
    assert len(devices) == 1
    assert len(with_serial) == 1
    # The identity is stable: re-discovery could not move it to a new key.
    assert with_serial[0].external_id


async def test_repeated_discovery_is_idempotent(tenant, db):
    source = await make_source(db, tenant.id)
    service = DiscoveryService(db)
    for _ in range(3):
        result = await service.discover(
            tenant_id=tenant.id,
            monitoring_source_id=source.id,
            evidence=[discovered_device()],
        )
    assert result.updated == 1
    assert len(await DeviceRepository(db).list(tenant.id, limit=50)) == 1


# ---------------------------------------------------------------------------
# Relationship evidence (topology ingestion)
# ---------------------------------------------------------------------------
async def test_neighbour_evidence_is_written_with_provenance_and_a_ledger(
    tenant, db
):
    source = await make_source(db, tenant.id)
    edge = discovered_device(address="10.0.0.5")
    edge.neighbors = [
        NeighborEvidence(
            address="10.0.0.7",
            evidence_type=V.EVIDENCE_SOURCE_LLDP,
            local_interface="port5",
            remote_interface="Gi0/1",
        )
    ]
    neighbour = discovered_device(
        address="10.0.0.7", serial="FDO9999ZZZZ", hostname="core-sw-01",
        device_type="switch",
    )

    result = await DiscoveryService(db).discover(
        tenant_id=tenant.id,
        monitoring_source_id=source.id,
        evidence=[edge, neighbour],
    )
    assert result.relationships_written == 1
    assert result.unresolved_neighbors == 0

    edges = await ResourceRelationshipRepository(db).list(tenant.id, limit=10)
    assert len(edges) == 1
    written = edges[0]
    assert written.relationship_type == "connected_to"
    assert written.relationship_subtype == V.EVIDENCE_SOURCE_LLDP
    assert written.discovered_by == V.EVIDENCE_SOURCE_LLDP
    assert written.status == V.RELATIONSHIP_ACTIVE
    ledger = written.attributes[V.RELATIONSHIP_EVIDENCE_LEDGER_KEY]
    assert V.EVIDENCE_SOURCE_LLDP in ledger
    assert ledger[V.EVIDENCE_SOURCE_LLDP]["local_interface"] == "port5"
    assert ledger[V.EVIDENCE_SOURCE_LLDP]["last_seen"]
    assert written.first_seen is not None and written.last_seen is not None

    # The owning outcome reports that adjacency evidence exists.
    owner = next(
        outcome for outcome in result.resources if outcome.address == "10.0.0.5"
    )
    assert owner.relationship_evidence_state == V.RELATIONSHIP_ACTIVE


async def test_the_same_adjacency_is_not_duplicated_and_keeps_all_sources(
    tenant, db
):
    source = await make_source(db, tenant.id)
    service = DiscoveryService(db)
    neighbour = discovered_device(
        address="10.0.0.7", serial="FDO9999ZZZZ", device_type="switch"
    )

    def batch(evidence_type: str) -> list[DiscoveryEvidence]:
        edge = discovered_device(address="10.0.0.5")
        edge.neighbors = [
            NeighborEvidence(
                address="10.0.0.7",
                evidence_type=evidence_type,
                local_interface="port5",
            )
        ]
        return [edge, neighbour]

    await service.discover(
        tenant_id=tenant.id,
        monitoring_source_id=source.id,
        evidence=batch(V.EVIDENCE_SOURCE_LLDP),
    )
    await service.discover(
        tenant_id=tenant.id,
        monitoring_source_id=source.id,
        evidence=batch(V.EVIDENCE_SOURCE_ARP),
    )

    edges = await ResourceRelationshipRepository(db).list(tenant.id, limit=10)
    assert len(edges) == 1, "the same adjacency must not create a second edge"
    ledger = edges[0].attributes[V.RELATIONSHIP_EVIDENCE_LEDGER_KEY]
    # BOTH sources are retained: a later ARP poll cannot erase the LLDP evidence.
    assert set(ledger) == {V.EVIDENCE_SOURCE_LLDP, V.EVIDENCE_SOURCE_ARP}


async def test_stale_relationship_evidence_is_distinguishable(tenant, db):
    source = await make_source(db, tenant.id)
    edge = discovered_device(address="10.0.0.5")
    edge.neighbors = [
        NeighborEvidence(address="10.0.0.7", evidence_type=V.EVIDENCE_SOURCE_CDP)
    ]
    neighbour = discovered_device(
        address="10.0.0.7", serial="FDO9999ZZZZ", device_type="switch"
    )
    await DiscoveryService(db).discover(
        tenant_id=tenant.id,
        monitoring_source_id=source.id,
        evidence=[edge, neighbour],
    )
    relationship = (await ResourceRelationshipRepository(db).list(tenant.id))[0]
    assert O.relationship_evidence_state(relationship, now=NOW) == V.RELATIONSHIP_ACTIVE

    # Age the evidence: still recorded, but no longer current topology.
    relationship.last_seen = NOW - timedelta(days=30)
    await db.flush()
    assert O.relationship_evidence_state(relationship, now=NOW) == V.RELATIONSHIP_STALE

    relationship.status = V.RELATIONSHIP_REMOVED
    await db.flush()
    assert O.relationship_evidence_state(relationship, now=NOW) == V.RELATIONSHIP_REMOVED


def test_no_relationship_evidence_is_a_state_not_a_silent_empty_list():
    assessment = O.assess_topology(facts(relationship_count=0), [], now=NOW)
    assert assessment.state == V.NO_RELATIONSHIP_EVIDENCE
    assert "not a claim that it has no neighbours" in assessment.detail


# ---------------------------------------------------------------------------
# Service-level operational state (what the UI reads)
# ---------------------------------------------------------------------------
async def test_service_reports_binding_and_freshness_from_real_rows(tenant, db):
    source = await make_source(db, tenant.id)
    await DiscoveryService(db).discover(
        tenant_id=tenant.id,
        monitoring_source_id=source.id,
        evidence=[discovered_device()],
    )
    devices = await DeviceRepository(db).list(tenant.id, limit=50)
    service = UniversalInfrastructureService(db)

    states = await service.operational_states(tenant.id, devices)
    assert len(states) == 1
    state = next(iter(states.values()))
    # A discovered resource with a source but no telemetry yet.
    assert state["binding"]["state"] == V.BINDING_BOUND
    assert state["freshness"]["state"] == V.FRESHNESS_NEVER_COLLECTED
    assert state["freshness"]["is_current"] is False
    assert state["topology"]["state"] == V.NO_RELATIONSHIP_EVIDENCE
    assert state["is_operational_resource"] is False
    assert state["blockers"]

    # Now give it real, current telemetry and re-derive.
    device = devices[0]
    device.last_telemetry_at = datetime.now(timezone.utc)
    await db.flush()
    refreshed = await service.operational_states(tenant.id, [device])
    fresh_state = refreshed[str(device.id)]
    assert fresh_state["freshness"]["state"] == V.FRESHNESS_FRESH
    assert fresh_state["identity_stage"] == V.STAGE_MONITORED
    assert fresh_state["is_operational_resource"] is True

    # Age it past expiry: was monitored, is NOT usable now.
    device.last_telemetry_at = datetime.now(timezone.utc) - timedelta(days=2)
    await db.flush()
    expired = await service.operational_states(tenant.id, [device])
    expired_state = expired[str(device.id)]
    assert expired_state["freshness"]["state"] == V.FRESHNESS_EXPIRED
    assert expired_state["identity_stage"] == V.STAGE_STALE
    assert expired_state["is_operational_resource"] is False


async def test_inactive_source_unbinds_the_resource(tenant, db):
    source = await make_source(db, tenant.id, active=False)
    await DiscoveryService(db).discover(
        tenant_id=tenant.id,
        monitoring_source_id=source.id,
        evidence=[discovered_device()],
    )
    devices = await DeviceRepository(db).list(tenant.id, limit=50)
    state = await UniversalInfrastructureService(db).resource_operational_state(
        tenant.id, devices[0].id
    )
    assert state is not None
    assert state["binding"]["state"] == V.BINDING_UNBOUND
    assert state["freshness"]["state"] == V.FRESHNESS_UNAVAILABLE


async def test_resource_operational_state_is_tenant_scoped(tenant, db):
    source = await make_source(db, tenant.id)
    await DiscoveryService(db).discover(
        tenant_id=tenant.id,
        monitoring_source_id=source.id,
        evidence=[discovered_device()],
    )
    devices = await DeviceRepository(db).list(tenant.id, limit=50)
    service = UniversalInfrastructureService(db)
    assert await service.resource_operational_state(uuid.uuid4(), devices[0].id) is None
    assert await service.resource_operational_state(tenant.id, devices[0].id) is not None


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------
async def test_discovery_and_topology_never_leak_across_tenants(tenant, db):
    from sqlalchemy import delete

    from app.models.tenant import Tenant

    other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
    db.add(other)
    await db.flush()
    try:
        source = await make_source(db, tenant.id)
        edge = discovered_device(address="10.0.0.5")
        edge.neighbors = [
            NeighborEvidence(address="10.0.0.7", evidence_type=V.EVIDENCE_SOURCE_LLDP)
        ]
        peer = discovered_device(
            address="10.0.0.7", serial="FDO9999ZZZZ", device_type="switch"
        )
        await DiscoveryService(db).discover(
            tenant_id=tenant.id,
            monitoring_source_id=source.id,
            evidence=[edge, peer],
        )

        tenant_devices = await DeviceRepository(db).list(tenant.id, limit=50)
        other_devices = await DeviceRepository(db).list(other.id, limit=50)
        assert len(tenant_devices) == 2
        assert other_devices == []

        assert len(await ResourceRelationshipRepository(db).list(tenant.id)) == 1
        assert await ResourceRelationshipRepository(db).list(other.id) == []
        assert await MonitoringSourceRepository(db).list(other.id) == []

        # The read model is scoped too: no devices means no operational state.
        assert (
            await UniversalInfrastructureService(db).operational_states(
                other.id, other_devices
            )
            == {}
        )
    finally:
        await db.execute(delete(Tenant).where(Tenant.id == other.id))
        await db.flush()


# ---------------------------------------------------------------------------
# Failover Intelligence compatibility
# ---------------------------------------------------------------------------
async def test_failover_detector_consumes_discovery_created_resources(tenant, db):
    """The hardened identity chain must still feed the completed failover layer."""
    from app.models.site import Site
    from app.repositories.topology_link_repository import TopologyLinkRepository
    from app.services.failover_intelligence.loader import load_scope

    site = Site(tenant_id=tenant.id, name="Branch", code=f"BR-{uuid.uuid4().hex[:4]}")
    db.add(site)
    await db.flush()

    source = await make_source(db, tenant.id)
    edge = discovered_device(address="10.0.0.5")
    peer = discovered_device(
        address="10.0.0.9",
        serial="FDO5555YYYY",
        hostname="carrier-pe",
        device_type="router",
        provider="cisco",
        vendor="Cisco",
    )
    result = await DiscoveryService(db).discover(
        tenant_id=tenant.id,
        monitoring_source_id=source.id,
        evidence=[edge, peer],
        site_id=site.id,
    )
    assert result.created == 2
    edge_id = uuid.UUID(result.resources[0].device_id)
    peer_id = uuid.UUID(result.resources[1].device_id)

    link, _created = await TopologyLinkRepository(db).upsert(
        tenant.id,
        device_a_id=edge_id,
        device_b_id=peer_id,
        name="wan-1",
        link_type="wan",
        oper_status="up",
        admin_status="up",
        bandwidth_mbps=100.0,
        latency_ms=9.0,
        redundancy_group="wan-rg",
    )
    assert link.id

    snapshot = await load_scope(db, tenant.id, site_id=site.id)
    assert [candidate.name for candidate in snapshot.links] == ["wan-1"]
    assert snapshot.edge_device is not None
    assert snapshot.edge_device.id == edge_id
    # The same identity the resource layer reports is the one failover consumes.
    state = await UniversalInfrastructureService(db).resource_operational_state(
        tenant.id, edge_id
    )
    assert state is not None
    assert state["resource_id"] == str(edge_id)


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------
BASE = "/api/infrastructure/intelligence"
READ_PERMISSIONS = frozenset({"devices.read"})


def api_client(tenant_id, permissions=READ_PERMISSIONS):
    from app.core.auth import create_access_token
    from app.main import app

    headers = (
        {
            "Authorization": "Bearer "
            + create_access_token(
                user_id=uuid.uuid4(),
                tenant_id=tenant_id,
                email="phase7@hsdeep.local",
                is_superuser=False,
                permissions=frozenset(permissions),
            )
        }
        if tenant_id is not None
        else {}
    )
    return httpx.AsyncClient(app=app, base_url="http://test", headers=headers)


async def seed_committed_resource(tenant_id):
    """Discover one device in a COMMITTED session - the API opens its own."""
    async with AsyncSessionLocal() as db:
        source = await make_source(db, tenant_id)
        result = await DiscoveryService(db).discover(
            tenant_id=tenant_id,
            monitoring_source_id=source.id,
            evidence=[discovered_device()],
        )
        await db.commit()
        return uuid.UUID(result.resources[0].device_id)


async def test_operational_state_endpoint_reports_four_separate_axes(tenant):
    resource_id = await seed_committed_resource(tenant.id)
    async with api_client(tenant.id) as client:
        response = await client.get(f"{BASE}/resources/{resource_id}/operational-state")

    assert response.status_code == 200
    body = response.json()
    assert body["resource_id"] == str(resource_id)
    # Four separate answers - never one combined "status".
    assert body["identity_stage"] in {
        "discovered",
        "identified",
        "classified",
        "capability_resolved",
        "collectable",
    }
    # Capability evidence exists but no sample has ever arrived, so it is
    # collectable - and explicitly NOT monitored.
    assert body["identity_stage"] != "monitored"
    assert body["binding"]["state"] == "bound"
    assert body["freshness"]["state"] == "never_collected"
    assert body["freshness"]["is_current"] is False
    assert body["topology"]["state"] == "no_relationship_evidence"
    assert body["is_operational_resource"] is False
    # And the reason the resource has not advanced is stated, not implied.
    assert body["blockers"]


async def test_operational_state_endpoint_requires_authentication(tenant):
    resource_id = await seed_committed_resource(tenant.id)
    async with api_client(None) as client:
        response = await client.get(f"{BASE}/resources/{resource_id}/operational-state")
    assert response.status_code == 401


async def test_operational_state_endpoint_is_tenant_scoped(tenant, db):
    from sqlalchemy import delete

    from app.models.tenant import Tenant

    resource_id = await seed_committed_resource(tenant.id)
    other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
    db.add(other)
    await db.flush()
    try:
        async with api_client(other.id) as client:
            response = await client.get(
                f"{BASE}/resources/{resource_id}/operational-state"
            )
        # Another tenant's resource is never found - not returned, not leaked.
        assert response.status_code == 404
    finally:
        await db.execute(delete(Tenant).where(Tenant.id == other.id))
        await db.flush()


async def test_unknown_resource_operational_state_is_404(tenant):
    async with api_client(tenant.id) as client:
        response = await client.get(f"{BASE}/resources/{uuid.uuid4()}/operational-state")
    assert response.status_code == 404


async def test_resource_list_carries_operational_state_and_evidence_split(tenant):
    resource_id = await seed_committed_resource(tenant.id)
    async with api_client(tenant.id) as client:
        listed = await client.get(f"{BASE}/resources", params={"limit": 50})
        detail = await client.get(f"{BASE}/resources/{resource_id}")

    assert listed.status_code == 200
    items = listed.json()["items"]
    row = next(item for item in items if item["resource_id"] == str(resource_id))
    assert row["operational_state"] is not None
    # The list row carries the same evidence-gated stage as the detail view: the
    # device has capability evidence but no telemetry, so it is not "monitored".
    assert row["operational_state"]["identity_stage"] != "monitored"
    assert row["operational_state"]["freshness"]["state"] == "never_collected"
    assert row["operational_state"]["binding"]["state"] == "bound"
    assert detail.status_code == 200
    # Health stays on its own axis; operational state is a separate answer.
    assert "operational_state" in detail.json()
    assert detail.json()["operational_state"]["binding"]["state"] == "bound"
