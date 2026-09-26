"""Phase 9.7B: controlled SNMP network evidence - real collection to real edges.

These tests drive the EXISTING SNMPCollector / BundleSaver / DiscoveryService
against a REAL SNMP agent (`scripts.lab_snmp_agent`: a real UDP SNMPv2c command
responder) and the REAL database, following the conventions in
`tests/test_discovery_integration.py` (committed rows under a per-test tenant,
cascade-deleted at teardown).

Why a real agent instead of a mocked pysnmp: the collector had two defects that
only a live PDU exchange exposes -

1. `nextCmd` returns a varbind TABLE (a list of ROWS) while the walk iterated it
   as a flat list, so its subtree test compared a row's text form
   ("<oid> = <value>") against an OID: every walk returned zero rows, silently;
2. the MIB constants carried a leading dot while pysnmp reports OIDs without
   one, so scalar lookups (sysDescr/sysName) never matched either.

Both were invisible to fake-`nextCmd` tests, which returned the flat, dotted
shape the buggy code expected. The assertions below use values that travelled
over UDP as real SNMP PDUs.

NOTE ON THE PROCESS EXIT CODE (pre-existing, unrelated to this phase)
--------------------------------------------------------------------
Any pytest process that imports the monitoring/collector stack AND uses asyncpg
in this Windows environment terminates with an access violation (0xC0000005)
AFTER printing its results - untouched suites behave identically:

    python -m pytest tests/test_collection_pipeline.py -q          # 5 passed, AV
    python -m pytest tests/test_universal_telemetry_pipeline.py -q # 6 passed, AV

The real-agent collection tests here import no database at all and exit cleanly:

    python -m pytest tests/test_snmp_evidence_pipeline.py -k TestRealSnmpCollection

The full file passes too; run it with `--junitxml=...` if a machine-readable
result is needed, because the exit code is unreliable for the reason above.
"""
from __future__ import annotations

import socket
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.collectors.snmp import (
    SNMPCollector,
    _iter_walk_var_binds,
    _normalize_oid,
    _suffix_after,
)

try:  # `scripts` is a package in this repo; fall back to a path insert if needed.
    from scripts.lab_snmp_agent import (
        LAB_SWITCH_A,
        LAB_SWITCH_B,
        LabCdpNeighbour,
        LabDeviceProfile,
        LabInterface,
        LabSnmpAgent,
    )
except ImportError:  # pragma: no cover - depends on how pytest is invoked
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from scripts.lab_snmp_agent import (
        LAB_SWITCH_A,
        LAB_SWITCH_B,
        LabCdpNeighbour,
        LabDeviceProfile,
        LabInterface,
        LabSnmpAgent,
    )

pytestmark = pytest.mark.asyncio

COMMUNITY = "public"


def _free_udp_port() -> int:
    """An unused local UDP port (the agent binds it, then the collector uses it)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def collector_source(host: str, port: int, community: str = COMMUNITY) -> SimpleNamespace:
    """The connection info a MonitoringSource hands to the collector."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name=f"lab-{host}:{port}",
        source_type="snmp",
        hostname=host,
        ip_address=None,
        port=port,
        protocol="snmp",
        snmp_version="v2c",
        username=None,
        api_key=community,
        password=None,
        config={},
        verify_ssl=None,
    )


@asynccontextmanager
async def lab_endpoint(
    profile: LabDeviceProfile,
    community: str = COMMUNITY,
    wrong_community_response: str = "drop",
):
    """A real SNMP endpoint on an ephemeral port, torn down after the test."""
    port = _free_udp_port()
    agent = LabSnmpAgent(
        profile,
        host="127.0.0.1",
        port=port,
        community=community,
        wrong_community_response=wrong_community_response,
    )
    await agent.start()
    try:
        yield agent, port
    finally:
        await agent.stop()


async def collect_bundle(profile: LabDeviceProfile, community: str = COMMUNITY):
    """Run the REAL collector against a REAL agent; return (check, raw, bundle, agent)."""
    async with lab_endpoint(profile, community=community) as (agent, port):
        collector = SNMPCollector(source=collector_source("127.0.0.1", port, community))
        try:
            check = await collector.authenticate()
            raw = await collector.collect()
            bundle = collector.normalize(raw)
        finally:
            # Always hand the SNMP engine back: a leaked dispatcher keeps a live
            # timer task and crashes the interpreter at shutdown.
            await collector.aclose()
        return check, raw, bundle, agent


@pytest_asyncio.fixture
async def snmp_collector():
    """Factory fixture: builds collectors and closes every one of them."""
    created = []

    def _make(host: str = "127.0.0.1", port: int = 1161, community: str = COMMUNITY):
        collector = SNMPCollector(source=collector_source(host, port, community))
        created.append(collector)
        return collector

    yield _make

    for collector in created:
        await collector.aclose()


async def collect_bundle_only(profile: LabDeviceProfile):
    """A real collected bundle, for tests that only need the evidence."""
    _, _, bundle, _ = await collect_bundle(profile)
    return bundle


class TestRealSnmpCollection:
    """Real UDP SNMP PDUs -> parsed interfaces and adjacency."""

    async def test_reachable_endpoint_answers_real_pdus(self):
        _, _, _, agent = await collect_bundle(LAB_SWITCH_A)
        assert agent.served > 0

    async def test_interfaces_are_collected_with_real_values(self):
        check, raw, bundle, _ = await collect_bundle(LAB_SWITCH_A)

        assert check.success is True
        assert raw["sys_name"] == "hsdeep-lab-sw-a"      # scalar lookup works
        assert len(bundle.interfaces) == 3

        by_id = {iface.external_id: iface for iface in bundle.interfaces}
        assert set(by_id) == {"1", "2", "3"}

        uplink = by_id["1"]
        assert uplink.name == "Gi0/1"
        assert uplink.admin_status == "up"
        assert uplink.oper_status == "up"
        assert uplink.speed_mbps == 1000.0
        # 64-bit counters survive the round trip exactly.
        assert uplink.in_octets == 123_456_789_012
        assert uplink.out_octets == 98_765_432_109

        access = by_id["3"]
        assert access.speed_mbps == 100.0
        assert access.in_errors == 3
        assert access.out_errors == 2

    async def test_admin_and_operational_status_are_distinguished(self):
        _, _, bundle, _ = await collect_bundle(LAB_SWITCH_A)
        by_id = {iface.external_id: iface for iface in bundle.interfaces}
        # Administratively down: both down.
        assert by_id["2"].admin_status == "down"
        assert by_id["2"].oper_status == "down"

        # Operationally down while still administratively up (LAB_SWITCH_B Gi1/2).
        _, _, bundle_b, _ = await collect_bundle(LAB_SWITCH_B)
        by_id_b = {iface.external_id: iface for iface in bundle_b.interfaces}
        assert by_id_b["2"].admin_status == "up"
        assert by_id_b["2"].oper_status == "down"

    async def test_lldp_adjacency_is_collected_as_observed_evidence(self):
        _, raw, bundle, _ = await collect_bundle(LAB_SWITCH_A)

        assert raw["lldp_status"] == "collected"
        assert bundle.neighbor_status["lldp"] == "collected"
        assert len(bundle.neighbors) == 1

        neighbour = bundle.neighbors[0]
        assert neighbour.evidence_type == "lldp"
        assert neighbour.local_interface == "Gi0/1"
        assert neighbour.remote_interface == "Te1/1"
        assert neighbour.remote_address == "127.0.0.2"
        assert neighbour.remote_identity == "hsdeep-lab-sw-b"
        assert neighbour.remote_chassis_id == "hsdeep-lab-sw-b"

    async def test_cdp_absence_is_not_a_collection_failure(self):
        """A device that does not speak CDP is reported honestly, not as a failure."""
        _, raw, bundle, _ = await collect_bundle(LAB_SWITCH_A)
        assert raw["cdp_status"] == "unavailable"
        assert bundle.neighbor_status["cdp"] == "unavailable"
        # ...and the interface collection still succeeded in the same run.
        assert len(bundle.interfaces) == 3

    async def test_cdp_is_parsed_when_a_device_exposes_it(self):
        profile = LabDeviceProfile(
            sys_name="hsdeep-lab-cdp",
            sys_descr="HSDEEP Lab SNMP Agent - CDP validation endpoint",
            interfaces=[LabInterface(1, "Gi1/0/1")],
            cdp_neighbours=[
                LabCdpNeighbour(
                    local_index=1,
                    remote_device_id="hsdeep-lab-sw-a",
                    remote_port_id="Gi0/1",
                    remote_platform="HSDEEP Lab",
                    remote_address="127.0.0.1",
                )
            ],
            expose_lldp_local_chassis=False,
        )
        _, raw, bundle, _ = await collect_bundle(profile)

        assert raw["cdp_status"] == "collected"
        assert len(bundle.neighbors) == 1
        cdp = bundle.neighbors[0]
        assert cdp.evidence_type == "cdp"
        assert cdp.local_interface == "Gi1/0/1"
        assert cdp.remote_interface == "Gi0/1"
        assert cdp.remote_address == "127.0.0.1"
        assert cdp.remote_platform == "HSDEEP Lab"

    async def test_wrong_community_fails_without_being_called_unsupported(self, snmp_collector):
        port = _free_udp_port()
        agent = LabSnmpAgent(LAB_SWITCH_A, host="127.0.0.1", port=port)
        await agent.start()
        try:
            collector = snmp_collector("127.0.0.1", port, "not-the-community")
            check = await collector.authenticate()
            dropped = agent.dropped_wrong_community
        finally:
            await agent.stop()

        assert check.success is False
        assert dropped > 0
        # The agent dropped the PDU (a real v2c agent answers nothing), so the
        # library cannot separate "wrong community" from "host unreachable" -
        # the message says exactly that rather than claiming a verdict.
        assert "unreachable or wrong community" in check.message
        # It must NOT be reported as a configuration/credential problem, which
        # would wrongly mark the capability unsupported.
        from app.services.monitoring import readiness

        assert readiness.configuration_problem(
            collector_source("127.0.0.1", port)
        ) is None

    async def test_unreachable_endpoint_fails_the_probe_without_raising(self, snmp_collector):
        """A dead target fails the probe; nothing propagates as an exception."""
        collector = snmp_collector("127.0.0.1", _free_udp_port())
        check = await collector.authenticate()
        assert check.success is False
        assert "no response" in check.message.lower()

    async def test_rejected_credentials_fail_the_run_without_raising(self, snmp_collector):
        """A v2c authorizationError is a failure, not a crash and not 'empty'."""
        async with lab_endpoint(
            LAB_SWITCH_A, community="public", wrong_community_response="reject"
        ) as (agent, port):
            collector = snmp_collector("127.0.0.1", port, "not-the-community")
            check = await collector.authenticate()
            raw = await collector.collect()
            bundle = collector.normalize(raw)
            rejected = agent.rejected_wrong_community

        assert rejected > 0
        assert check.success is False
        assert raw["lldp_status"] == "failed"       # a refused read is not "no neighbours"
        assert len(bundle.interfaces) == 0
        assert bundle.neighbors == []


class TestWalkShapeRegression:
    """The two defects the live agent exposed, pinned so they cannot return."""

    def test_walk_flattens_a_varbind_table(self):
        """pysnmp's nextCmd returns ROWS; iterating them as flat broke every walk."""
        table = [[("1.3.6.1.2.1.2.2.1.2.1", "Gi0/1")], [("1.3.6.1.2.1.2.2.1.2.2", "Gi0/2")]]
        assert list(_iter_walk_var_binds(table)) == [
            ("1.3.6.1.2.1.2.2.1.2.1", "Gi0/1"),
            ("1.3.6.1.2.1.2.2.1.2.2", "Gi0/2"),
        ]
        # A flat list (getCmd's shape) keeps working unchanged.
        flat = [("1.3.6.1.2.1.1.1.0", "text")]
        assert list(_iter_walk_var_binds(flat)) == flat
        assert list(_iter_walk_var_binds(None)) == []

    def test_oid_text_is_normalized_to_one_spelling(self):
        assert _normalize_oid(".1.3.6.1.2.1.1.1.0") == "1.3.6.1.2.1.1.1.0"
        assert _normalize_oid(" 1.3.6.1.2.1.1.1.0 ") == "1.3.6.1.2.1.1.1.0"

    def test_row_index_parsing_accepts_both_oid_spellings(self):
        """Row-index suffixes drive interface and neighbour mapping."""
        assert _suffix_after("1.3.6.1.2.1.2.2.1.2.5", ".1.3.6.1.2.1.2.2.1.2") == "5"
        assert _suffix_after(".1.3.6.1.2.1.2.2.1.2.5", "1.3.6.1.2.1.2.2.1.2") == "5"
        assert _suffix_after("1.3.6.1.4.1.9.9.1", "1.3.6.1.2.1.2.2.1.2") is None

    def test_a_row_text_form_is_never_accepted_as_an_oid(self):
        """The exact signature of the bug: "<oid> = <value>" is not an OID."""
        row_text = "SNMPv2-SMI::mib-2.2.2.1.2.1 = Gi0/1"
        assert _suffix_after(row_text, "1.3.6.1.2.1.2.2.1.2") is None
        assert not row_text.startswith("1.3.6.1.2.1.2.2.1.2.")


# ---------------------------------------------------------------------------
# DB-backed pipeline: real bundle -> BundleSaver -> interfaces + edges.
# ---------------------------------------------------------------------------

def bundle_saver(db):
    from app.repositories.device_repository import DeviceRepository
    from app.repositories.event_repository import EventRepository
    from app.repositories.interface_repository import InterfaceRepository
    from app.repositories.metric_repository import MetricRepository
    from app.repositories.sensor_repository import SensorRepository
    from app.services.monitoring.bundle_saver import BundleSaver

    return BundleSaver(
        device_repo=DeviceRepository(db),
        interface_repo=InterfaceRepository(db),
        sensor_repo=SensorRepository(db),
        metric_repo=MetricRepository(db),
        event_repo=EventRepository(db),
    )


async def persist_peer(db, tenant_id, source_id, address: str, name: str = "hsdeep-lab-sw-b"):
    """A peer device the tenant already knows about (the LLDP peer's record)."""
    from app.repositories.device_repository import DeviceRepository

    result = await DeviceRepository(db).upsert(
        tenant_id=tenant_id,
        monitoring_source_id=source_id,
        external_id=address,
        name=name,
        device_type="switch",
        ip_address=address,
        status="up",
    )
    return result[0] if isinstance(result, tuple) else result


async def device_by_ip(db, tenant_id, address: str):
    from sqlalchemy import select

    from app.models.device import Device

    return (
        await db.execute(
            select(Device).where(Device.tenant_id == tenant_id, Device.ip_address == address)
        )
    ).scalars().first()


async def interfaces_for(db, device_id):
    from sqlalchemy import select

    from app.models.interface import Interface

    return list(
        (
            await db.execute(select(Interface).where(Interface.device_id == device_id))
        ).scalars().all()
    )


async def relationships_for(db, tenant_id):
    from sqlalchemy import select

    from app.models.resource_relationship import ResourceRelationship

    return list(
        (
            await db.execute(
                select(ResourceRelationship).where(
                    ResourceRelationship.tenant_id == tenant_id
                )
            )
        ).scalars().all()
    )


async def capability_rows(db, device_id):
    from sqlalchemy import select

    from app.models.resource_capability import ResourceCapability

    rows = (
        await db.execute(
            select(ResourceCapability).where(ResourceCapability.resource_id == device_id)
        )
    ).scalars().all()
    return {row.capability: row for row in rows}


class TestSnmpEvidencePersistence:
    """Real collected evidence -> real rows, with provenance and no invention."""

    async def test_interfaces_are_persisted_for_the_collected_device(
        self, tenant, db, monitoring_source_factory
    ):
        source = await monitoring_source_factory(
            source_type="snmp", hostname="127.0.0.1", port=1161, snmp_version="v2c"
        )
        bundle = await collect_bundle_only(LAB_SWITCH_A)

        result = await bundle_saver(db).save(source, bundle)
        await db.commit()

        assert result.interfaces_upserted == 3
        local = await device_by_ip(db, tenant.id, "127.0.0.1")
        assert local is not None

        rows = {row.external_id: row for row in await interfaces_for(db, local.id)}
        assert set(rows) == {"1", "2", "3"}
        uplink = rows["1"]
        assert uplink.name == "Gi0/1"
        assert uplink.admin_status == "up"
        assert uplink.oper_status == "up"
        assert uplink.speed_mbps == 1000.0
        assert uplink.in_octets == 123_456_789_012
        assert uplink.out_octets == 98_765_432_109
        assert rows["2"].admin_status == "down"
        # Every interface stays attributed to the source that collected it.
        assert all(row.monitoring_source_id == source.id for row in rows.values())

    async def test_repeated_collection_updates_instead_of_duplicating(
        self, tenant, db, monitoring_source_factory
    ):
        source = await monitoring_source_factory(
            source_type="snmp", hostname="127.0.0.1", port=1161, snmp_version="v2c"
        )
        bundle = await collect_bundle_only(LAB_SWITCH_A)
        saver = bundle_saver(db)

        first = await saver.save(source, bundle)
        second = await saver.save(source, bundle)
        await db.commit()

        assert first.interfaces_upserted == second.interfaces_upserted == 3
        local = await device_by_ip(db, tenant.id, "127.0.0.1")
        rows = await interfaces_for(db, local.id)
        assert len(rows) == 3                       # no duplicates
        assert len({row.id for row in rows}) == 3
        assert len(await relationships_for(db, tenant.id)) == 0  # no peer => no edge

    async def test_lldp_neighbour_becomes_an_observed_edge_with_provenance(
        self, tenant, db, monitoring_source_factory
    ):
        source = await monitoring_source_factory(
            source_type="snmp", hostname="127.0.0.1", port=1161, snmp_version="v2c"
        )
        peer = await persist_peer(db, tenant.id, source.id, "127.0.0.2")
        bundle = await collect_bundle_only(LAB_SWITCH_A)

        result = await bundle_saver(db).save(source, bundle)
        await db.commit()

        assert result.neighbors_written == 1
        assert result.neighbors_unresolved == 0

        edges = await relationships_for(db, tenant.id)
        assert len(edges) == 1
        edge = edges[0]
        local = await device_by_ip(db, tenant.id, "127.0.0.1")
        assert edge.source_resource_id == local.id
        assert edge.target_resource_id == peer.id
        assert edge.relationship_type == "connected_to"
        assert edge.relationship_subtype == "lldp"
        # SNMP-observed LLDP adjacency is OBSERVED evidence, never inferred.
        assert edge.is_inferred is False
        assert edge.discovered_by == "lldp"
        assert edge.status == "active"
        assert edge.confidence == 100.0

        attributes = edge.attributes or {}
        assert attributes["local_interface"] == "Gi0/1"
        assert attributes["remote_interface"] == "Te1/1"
        assert attributes["evidence_type"] == "lldp"

        # The per-source evidence ledger keeps the observation itself, so a later
        # poll (or another protocol) cannot erase it.
        from app.services.infrastructure_intelligence import vocabulary as V

        ledger = attributes[V.RELATIONSHIP_EVIDENCE_LEDGER_KEY]
        entry = ledger["lldp"]
        assert entry["neighbour_address"] == "127.0.0.2"
        assert entry["local_interface"] == "Gi0/1"
        assert entry["remote_interface"] == "Te1/1"
        assert entry["confidence"] == 100.0
        assert entry["first_seen"] == entry["last_seen"]

    async def test_neighbour_without_a_known_device_writes_no_edge(
        self, tenant, db, monitoring_source_factory
    ):
        """No evidence-backed endpoint => no edge. Reported, never faked."""
        source = await monitoring_source_factory(
            source_type="snmp", hostname="127.0.0.1", port=1161, snmp_version="v2c"
        )
        bundle = await collect_bundle_only(LAB_SWITCH_A)

        result = await bundle_saver(db).save(source, bundle)
        await db.commit()

        assert result.neighbors_written == 0
        assert result.neighbors_unresolved == 1
        assert await relationships_for(db, tenant.id) == []

    async def test_adjacency_is_never_inferred_from_shared_ip_or_site(
        self, tenant, db, monitoring_source_factory
    ):
        """Two devices in one tenant, no adjacency evidence: no edge."""
        source = await monitoring_source_factory(
            source_type="snmp", hostname="127.0.0.1", port=1161, snmp_version="v2c"
        )
        await persist_peer(db, tenant.id, source.id, "10.10.13.9")
        from app.collectors.base import NormalizedBundle, NormalizedDevice

        no_neighbours = NormalizedBundle(
            devices=[NormalizedDevice(external_id="127.0.0.1", name="lab", device_type="switch")],
            interfaces=[],
            neighbors=[],
            neighbor_status={"lldp": "empty", "cdp": "unavailable"},
        )
        result = await bundle_saver(db).save(source, no_neighbours)
        await db.commit()

        assert result.neighbors_written == 0
        assert await relationships_for(db, tenant.id) == []

    async def test_neighbour_resolution_never_crosses_tenants(
        self, tenant, db, monitoring_source_factory
    ):
        """The same address in another tenant must never be resolved or linked."""
        from sqlalchemy import delete

        from app.models.monitoring_source import MonitoringSource
        from app.models.tenant import Tenant

        source = await monitoring_source_factory(
            source_type="snmp", hostname="127.0.0.1", port=1161, snmp_version="v2c"
        )
        own_peer = await persist_peer(db, tenant.id, source.id, "127.0.0.2")

        # A second tenant that happens to own a device at the SAME address.
        other_tenant = Tenant(name="Peer Tenant", slug=f"peer-{uuid.uuid4().hex[:8]}")
        db.add(other_tenant)
        await db.flush()
        other_source = MonitoringSource(
            tenant_id=other_tenant.id,
            name=f"other-snmp-{uuid.uuid4().hex[:6]}",
            source_type="snmp",
            hostname="127.0.0.2",
            port=1162,
            snmp_version="v2c",
            is_active=True,
            status="pending",
        )
        db.add(other_source)
        await db.flush()
        other_peer = await persist_peer(
            db, other_tenant.id, other_source.id, "127.0.0.2", name="other-tenant-switch"
        )

        try:
            bundle = await collect_bundle_only(LAB_SWITCH_A)
            result = await bundle_saver(db).save(source, bundle)
            await db.commit()

            assert result.neighbors_written == 1
            own_edges = await relationships_for(db, tenant.id)
            assert len(own_edges) == 1
            assert own_edges[0].target_resource_id == own_peer.id
            assert own_edges[0].target_resource_id != other_peer.id

            # Nothing was written into the other tenant.
            assert await relationships_for(db, other_tenant.id) == []
        finally:
            await db.execute(delete(Tenant).where(Tenant.id == other_tenant.id))
            await db.commit()

    async def test_capability_assertions_reflect_what_was_collected(
        self, tenant, db, monitoring_source_factory
    ):
        from app.services.infrastructure_intelligence import vocabulary as V

        source = await monitoring_source_factory(
            source_type="snmp", hostname="127.0.0.1", port=1161, snmp_version="v2c"
        )
        await persist_peer(db, tenant.id, source.id, "127.0.0.2")
        bundle = await collect_bundle_only(LAB_SWITCH_A)

        result = await bundle_saver(db).save(source, bundle)
        await db.commit()

        assert result.capabilities_asserted > 0
        local = await device_by_ip(db, tenant.id, "127.0.0.1")
        caps = await capability_rows(db, local.id)

        interfaces = caps[V.CAP_INTERFACES]
        assert interfaces.support == V.SUPPORT_SUPPORTED
        assert interfaces.status == "collected"
        assert interfaces.provider == V.PROVIDER_SNMP

        # Topology was collected too, because real adjacency arrived in the run.
        topology = caps[V.CAP_TOPOLOGY]
        assert topology.support == V.SUPPORT_SUPPORTED
        assert topology.status == "collected"

    async def test_topology_is_not_claimed_when_no_adjacency_was_collected(
        self, tenant, db, monitoring_source_factory
    ):
        from app.services.infrastructure_intelligence import vocabulary as V

        source = await monitoring_source_factory(
            source_type="snmp", hostname="127.0.0.1", port=1161, snmp_version="v2c"
        )
        bundle = await collect_bundle_only(LAB_SWITCH_B)
        assert bundle.neighbor_status["lldp"] == "collected"

        # Strip the adjacency: interfaces remain, adjacency is gone.
        bundle.neighbors = []
        await bundle_saver(db).save(source, bundle)
        await db.commit()

        local = await device_by_ip(db, tenant.id, "127.0.0.1")
        caps = await capability_rows(db, local.id)
        assert caps[V.CAP_INTERFACES].status == "collected"
        # Capability stays SUPPORTED (SNMP can collect topology) but the data is
        # explicitly not collected - never reported as observed.
        assert caps[V.CAP_TOPOLOGY].status != "collected"

    async def test_one_failing_device_does_not_stop_the_scope(
        self, tenant, db, monitoring_source_factory, snmp_collector
    ):
        """A failing endpoint does not stop the reachable one in the same scope."""
        dead = await monitoring_source_factory(
            source_type="snmp", hostname="127.0.0.1", port=_free_udp_port(), snmp_version="v2c"
        )
        live = await monitoring_source_factory(
            source_type="snmp", hostname="127.0.0.1", port=1161, snmp_version="v2c"
        )
        await persist_peer(db, tenant.id, live.id, "127.0.0.2")

        async with lab_endpoint(
            LAB_SWITCH_A, community="public", wrong_community_response="reject"
        ) as (_, dead_port):
            failing = snmp_collector("127.0.0.1", dead_port, "wrong")
            assert (await failing.authenticate()).success is False
        assert dead.id != live.id

        bundle = await collect_bundle_only(LAB_SWITCH_A)
        result = await bundle_saver(db).save(live, bundle)
        await db.commit()

        assert result.interfaces_upserted == 3        # the live device still collected
        assert result.neighbors_written == 1

    async def test_community_string_never_reaches_the_logs(self, caplog, snmp_collector):
        """A rejected credential must not leak into logs or the raw result."""
        import logging

        secret = "hsdeep-super-secret-community"
        caplog.set_level(logging.DEBUG)

        # The endpoint answers with authorizationError, so this covers the
        # credential-rejection path at full collection speed.
        async with lab_endpoint(
            LAB_SWITCH_A, community="public", wrong_community_response="reject"
        ) as (agent, port):
            collector = snmp_collector("127.0.0.1", port, secret)
            check = await collector.authenticate()
            raw = await collector.collect()

        assert check.success is False
        assert agent.rejected_wrong_community > 0
        assert secret not in caplog.text
        assert secret not in str(raw)




