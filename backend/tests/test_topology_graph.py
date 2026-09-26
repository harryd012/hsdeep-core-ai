"""
Phase 3 tests: topology graph nodes, edges, status and honesty guarantees.

The load-bearing claim under test is NEGATIVE: this graph never invents an
edge. `resource_relationships` has exactly one production writer
(`DiscoveryService._write_neighbors`) and nothing in production ever constructs
`NeighborEvidence`, so the correct output for a tenant with no relationship rows
is zero edges plus an explanation - never a graph inferred from subnets,
hostnames, device types or insertion order.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.device import Device
from app.models.tenant import Tenant
from app.repositories.device_repository import DeviceRepository
from app.repositories.interface_repository import InterfaceRepository
from app.repositories.resource_relationship_repository import (
    ResourceRelationshipRepository,
)
from app.services.infrastructure_overview_service import (
    InfrastructureOverviewService,
)
from app.services.topology_graph_service import (
    KNOWN_RELATIONSHIP_TYPES,
    PREFIX_DEVICE,
    PROVENANCE_RESOURCE_RELATIONSHIP,
    TOPOLOGY_NOT_DISCOVERED,
    TOPOLOGY_READY,
    TopologyGraphService,
)


def _service() -> TopologyGraphService:
    return TopologyGraphService(db=None)  # type: ignore[arg-type]


def _rel(**overrides):
    """Stand-in for a ResourceRelationship row."""
    now = datetime.now(timezone.utc)
    base = dict(
        id=uuid.uuid4(),
        source_resource_id=uuid.uuid4(),
        target_resource_id=uuid.uuid4(),
        relationship_type="connected_to",
        relationship_subtype=None,
        label=None,
        weight=None,
        confidence=100.0,
        discovered_by="lldp",
        is_inferred=False,
        status="active",
        first_seen=now,
        last_seen=now,
        attributes={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _device(**overrides):
    base = dict(
        id=uuid.uuid4(), name="sw-01", device_type="switch", status="up",
        site_id=None, monitoring_source_id=None, ip_address="10.0.0.1",
        vendor="Cisco", model="C9300", hostname="sw-01", last_seen_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _site(name="HQ", lat=None, lng=None):
    return SimpleNamespace(
        id=uuid.uuid4(), name=name, code=name.upper(), site_type="datacenter",
        region=None, latitude=lat, longitude=lng, address=None, city=None,
        country=None, criticality=None,
    )


class TestEdgeNormalization:

    def test_no_relationships_yields_no_edges(self):
        edges, dangling = _service()._edge_block([], {PREFIX_DEVICE + "x"})
        assert edges == []
        assert dangling == 0

    def test_real_edge_is_normalized_with_provenance(self):
        source, target = uuid.uuid4(), uuid.uuid4()
        node_ids = {f"{PREFIX_DEVICE}{source}", f"{PREFIX_DEVICE}{target}"}
        rel = _rel(
            source_resource_id=source, target_resource_id=target,
            relationship_type="connected_to", discovered_by="lldp",
            label="eth0", confidence=100.0, attributes={"index": 12},
        )
        edges, dangling = _service()._edge_block([rel], node_ids)

        assert dangling == 0
        edge = edges[0]
        assert edge["source_id"] == f"{PREFIX_DEVICE}{source}"
        assert edge["target_id"] == f"{PREFIX_DEVICE}{target}"
        assert edge["relationship_type"] == "connected_to"
        assert edge["evidence"] == PROVENANCE_RESOURCE_RELATIONSHIP
        assert edge["discovered_by"] == "lldp"
        assert edge["is_inferred"] is False
        # Real column, returned as stored.
        assert edge["confidence"] == 100.0
        assert edge["unknown_relationship_type"] is False
        # Collector-supplied attribute, passed through verbatim.
        assert edge["metadata"] == {"index": 12}

    def test_dangling_edge_is_excluded_and_counted(self):
        """An edge whose endpoint is not a node must not be drawn."""
        node_ids = {f"{PREFIX_DEVICE}{uuid.uuid4()}"}
        edges, dangling = _service()._edge_block([_rel()], node_ids)
        assert edges == []
        assert dangling == 1

    def test_unknown_relationship_type_is_flagged_not_hidden(self):
        source, target = uuid.uuid4(), uuid.uuid4()
        node_ids = {f"{PREFIX_DEVICE}{source}", f"{PREFIX_DEVICE}{target}"}
        rel = _rel(
            source_resource_id=source, target_resource_id=target,
            relationship_type="quantum_entangled_with",
        )
        edges, _ = _service()._edge_block([rel], node_ids)
        assert edges[0]["relationship_type"] == "quantum_entangled_with"
        assert edges[0]["unknown_relationship_type"] is True

    def test_every_declared_vocabulary_type_is_recognised(self):
        for rel_type in sorted(KNOWN_RELATIONSHIP_TYPES):
            source, target = uuid.uuid4(), uuid.uuid4()
            node_ids = {f"{PREFIX_DEVICE}{source}", f"{PREFIX_DEVICE}{target}"}
            edges, _ = _service()._edge_block(
                [_rel(source_resource_id=source, target_resource_id=target,
                      relationship_type=rel_type)], node_ids
            )
            assert edges[0]["unknown_relationship_type"] is False, rel_type

    def test_no_link_speed_vlan_latency_or_bandwidth_is_invented(self):
        """The model records none of these, so none may appear."""
        source, target = uuid.uuid4(), uuid.uuid4()
        node_ids = {f"{PREFIX_DEVICE}{source}", f"{PREFIX_DEVICE}{target}"}
        edges, _ = _service()._edge_block(
            [_rel(source_resource_id=source, target_resource_id=target)], node_ids
        )
        edge = edges[0]
        for forbidden in ("speed_mbps", "vlan", "latency_ms", "bandwidth",
                          "utilization", "packet_loss"):
            assert forbidden not in edge, forbidden
        assert edge["metadata"] == {}

    def test_weight_omitted_when_absent(self):
        source, target = uuid.uuid4(), uuid.uuid4()
        node_ids = {f"{PREFIX_DEVICE}{source}", f"{PREFIX_DEVICE}{target}"}
        edges, _ = _service()._edge_block(
            [_rel(source_resource_id=source, target_resource_id=target, weight=None)],
            node_ids,
        )
        assert "weight" not in edges[0]

    def test_malformed_relationship_attributes_are_survived(self):
        """None/odd attributes must not raise or fabricate a mapping."""
        source, target = uuid.uuid4(), uuid.uuid4()
        node_ids = {f"{PREFIX_DEVICE}{source}", f"{PREFIX_DEVICE}{target}"}
        edges, _ = _service()._edge_block(
            [_rel(source_resource_id=source, target_resource_id=target,
                  attributes=None)], node_ids
        )
        assert edges[0]["metadata"] == {}

    def test_self_loop_is_preserved_not_special_cased(self):
        """A recorded self-edge is real data; the graph must not 'fix' it."""
        one = uuid.uuid4()
        node_ids = {f"{PREFIX_DEVICE}{one}"}
        edges, _ = _service()._edge_block(
            [_rel(source_resource_id=one, target_resource_id=one)], node_ids
        )
        assert len(edges) == 1


class TestNodeBlock:
    """Step 14: node health must use the Phase 2 canonical vocabulary."""

    def _nodes(self, devices, sites=None, sources=None,
               sensor_counts=None, alert_counts=None, interface_counts=None):
        return _service()._node_block(
            devices, sites or [], sources or [],
            sensor_counts or {}, alert_counts or {}, interface_counts or {},
        )

    def test_online_device_is_healthy_not_unknown(self):
        """`online` is a healthy status in the live DB; the graph must agree."""
        nodes = self._nodes([_device(status="online")])
        assert nodes[0]["health"] == "healthy"

    def test_health_covers_the_whole_vocabulary(self):
        expected = {
            "up": "healthy", "online": "healthy",
            "warning": "warning", "degraded": "warning",
            "down": "critical", "failed": "critical",
            "unknown": "unknown", "something_new": "unknown",
        }
        nodes = self._nodes([_device(status=s) for s in expected])
        for node, (raw, bucket) in zip(nodes, expected.items()):
            assert node["status"] == raw
            assert node["health"] == bucket, raw

    def test_device_type_is_canonical_with_raw_provenance(self):
        nodes = self._nodes([_device(device_type="other")])
        assert nodes[0]["device_type"] == "unclassified"
        assert nodes[0]["device_type_raw"] == "other"
        assert nodes[0]["is_network_device"] is False

    def test_network_device_flag(self):
        nodes = self._nodes([_device(device_type="firewall")])
        assert nodes[0]["is_network_device"] is True

    def test_counts_default_to_zero_not_missing(self):
        nodes = self._nodes([_device()])
        assert nodes[0]["counts"] == {"sensors": 0, "alerts": 0, "interfaces": 0}

    def test_counts_are_attached_per_device(self):
        device = _device()
        nodes = self._nodes(
            [device], sensor_counts={device.id: 5}, alert_counts={device.id: 2},
            interface_counts={device.id: 48},
        )
        assert nodes[0]["counts"] == {"sensors": 5, "alerts": 2, "interfaces": 48}

    def test_node_ids_are_namespaced_by_kind(self):
        device, site = _device(), _site()
        nodes = self._nodes([device], [site])
        ids = {n["id"] for n in nodes}
        assert f"{PREFIX_DEVICE}{device.id}" in ids
        assert f"site:{site.id}" in ids

    def test_device_without_site_has_null_site_id(self):
        nodes = self._nodes([_device(site_id=None)])
        assert nodes[0]["site_id"] is None

    def test_site_node_has_no_fabricated_coordinates(self):
        nodes = self._nodes([], [_site("No coords")])
        assert nodes[0]["latitude"] is None
        assert nodes[0]["longitude"] is None
        assert nodes[0]["has_coordinates"] is False

    def test_partial_coordinates_do_not_count_as_mappable(self):
        nodes = self._nodes([], [_site("Half", lat=30.7, lng=None)])
        assert nodes[0]["has_coordinates"] is False

    def test_site_coordinates_pass_through_when_real(self):
        nodes = self._nodes([], [_site("HQ", lat=30.7333, lng=76.7794)])
        assert nodes[0]["has_coordinates"] is True
        assert nodes[0]["latitude"] == 30.7333

    def test_site_device_count_uses_only_that_site(self):
        site_a, site_b = _site("A"), _site("B")
        devices = [_device(site_id=site_a.id), _device(site_id=site_a.id),
                   _device(site_id=site_b.id), _device(site_id=None)]
        nodes = self._nodes(devices, [site_a, site_b])
        counts = {n["name"]: n["counts"]["devices"] for n in nodes if n["type"] == "site"}
        assert counts == {"A": 2, "B": 1}


# ---------------------------------------------------------------------------
# DB-backed: real nodes/edges/counts. Fixture order is (tenant, db) so the
# uncommitted FK locks roll back BEFORE the tenant DELETE - see the deadlock
# note in tests/test_infrastructure_overview.py.
# ---------------------------------------------------------------------------

class TestTopologyGraphDatabase:

    async def _seed(self, db, tenant, name_prefix, statuses):
        devices = []
        for index, status in enumerate(statuses):
            device = Device(
                tenant_id=tenant.id, name=f"{name_prefix}-{index}",
                device_type="switch", status=status,
            )
            db.add(device)
            devices.append(device)
        await db.flush()
        return devices

    async def test_graph_contains_only_this_tenants_devices(self, tenant, db):
        """Tenant isolation: another tenant's devices must not appear."""
        mine = await self._seed(db, tenant, "iso-mine", ["up"])

        other_tenant = Tenant(name="Other Tenant", slug=f"other-{uuid.uuid4().hex[:8]}")
        db.add(other_tenant)
        await db.flush()
        theirs = await self._seed(db, other_tenant, "iso-theirs", ["up"])

        graph = await TopologyGraphService(db).build(tenant.id)
        ids = {node["id"] for node in graph["nodes"]}

        assert f"{PREFIX_DEVICE}{mine[0].id}" in ids
        assert f"{PREFIX_DEVICE}{theirs[0].id}" not in ids
        assert graph["summary"]["devices_total"] == 1

    async def test_empty_tenant_is_not_discovered_with_reasons(self, tenant, db):
        graph = await TopologyGraphService(db).build(tenant.id)
        assert graph["summary"]["devices_total"] == 0
        assert graph["summary"]["edges_total"] == 0
        assert graph["status"] == TOPOLOGY_NOT_DISCOVERED
        assert graph["limitations"], "an empty graph must explain itself"

    async def test_device_count_matches_repository(self, tenant, db):
        await self._seed(db, tenant, "cnt", ["up", "online"])
        graph = await TopologyGraphService(db).build(tenant.id)
        assert graph["summary"]["devices_total"] == await DeviceRepository(
            db
        ).count(tenant.id)

    async def test_interface_count_matches_repository(self, tenant, db):
        await self._seed(db, tenant, "ifc", ["up"])
        graph = await TopologyGraphService(db).build(tenant.id)
        assert graph["summary"]["interfaces_total"] == await InterfaceRepository(
            db
        ).count_for_tenant(tenant.id)

    async def test_edge_count_matches_repository_and_none_is_invented(
        self, tenant, db
    ):
        await self._seed(db, tenant, "edge", ["up", "up"])
        graph = await TopologyGraphService(db).build(tenant.id)

        repo_count = await ResourceRelationshipRepository(db).count(tenant.id)
        assert graph["summary"]["edges_total"] == repo_count == 0
        assert graph["edges"] == []
        # Two devices in the same tenant are NOT a relationship.
        assert graph["status"] == TOPOLOGY_NOT_DISCOVERED

    async def test_real_edge_produces_ready_status_and_a_drawable_edge(
        self, tenant, db
    ):
        first, second = await self._seed(db, tenant, "real", ["up", "up"])
        await ResourceRelationshipRepository(db).upsert(
            tenant_id=tenant.id,
            source_resource_id=first.id,
            target_resource_id=second.id,
            relationship_type="connected_to",
            discovered_by="lldp",
        )
        await db.flush()

        graph = await TopologyGraphService(db).build(tenant.id)
        assert graph["summary"]["edges_total"] == 1
        assert graph["status"] == TOPOLOGY_READY
        edge = graph["edges"][0]
        assert edge["source_id"] == f"{PREFIX_DEVICE}{first.id}"
        assert edge["target_id"] == f"{PREFIX_DEVICE}{second.id}"
        assert edge["discovered_by"] == "lldp"
        assert edge["evidence"] == PROVENANCE_RESOURCE_RELATIONSHIP

    async def test_counts_agree_with_canonical_overview(self, tenant, db):
        """Phase 15: the topology graph and /infrastructure/overview must agree."""
        await self._seed(db, tenant, "agree", ["up", "online", "down"])

        graph = await TopologyGraphService(db).build(tenant.id)
        overview = await InfrastructureOverviewService(db).build(tenant.id)

        assert graph["summary"]["devices_total"] == overview["health"]["total_devices"]
        assert graph["summary"]["sites_total"] == overview["health"]["total_sites"]
        assert graph["summary"]["sensors_total"] == overview["health"]["total_sensors"]
        assert (
            graph["summary"]["edges_total"]
            == overview["health"]["total_relationships"]
        )
        assert (
            graph["summary"]["interfaces_total"]
            == overview["health"]["total_interfaces"]
        )
