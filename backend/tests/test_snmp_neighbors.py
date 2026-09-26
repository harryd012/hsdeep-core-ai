"""
Phase 3A tests: LLDP/CDP neighbour evidence.

Covers the collector layer (parsing + normalization into `NormalizedNeighbor`)
and the persistence path (BundleSaver -> DiscoveryService -> ResourceRelationship
-> topology graph), using the SAME pipeline production uses.

Nothing here infers adjacency from subnets, hostnames or ping: every neighbour
in these tests comes from a protocol table fixture.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.collectors.base import (
    NormalizedBundle,
    NormalizedDevice,
    NormalizedNeighbor,
)
from app.models.device import Device
from app.models.monitoring_source import MonitoringSource
from app.models.tenant import Tenant
from app.repositories.device_repository import DeviceRepository
from app.repositories.interface_repository import InterfaceRepository
from app.repositories.resource_relationship_repository import (
    ResourceRelationshipRepository,
)
from app.repositories.sensor_repository import SensorRepository
from app.services.topology_graph_service import (
    MESSAGE_NOT_DISCOVERED,
    TopologyGraphService,
)
from app.collectors.snmp import (
    NEIGHBOR_COLLECTED,
    NEIGHBOR_EMPTY,
    NEIGHBOR_FAILED,
    NEIGHBOR_UNAVAILABLE,
    SNMPCollector,
    _coerce_ipv4,
    _parse_cdp_row_index,
    _parse_lldp_man_addr,
    _parse_lldp_row_index,
    _suffix_after,
)

_LLDP_BASE = ".1.0.8802.1.1.2.1.4.1.1"
_LLDP_MAN = ".1.0.8802.1.1.2.1.4.2.1.4"
_CDP_BASE = ".1.3.6.1.4.1.9.9.23.1.2.1.1"


def _source(hostname="sw-a.lab", ip="10.0.0.1"):
    return SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="snmp-a", source_type="snmp",
        hostname=hostname, ip_address=ip, port=None, protocol="snmp",
        snmp_version="v2c", username=None, api_key=None, password="public",
        config={}, verify_ssl=None,
    )


def _collector(**kw) -> SNMPCollector:
    return SNMPCollector(_source(**kw))


def _interfaces(descr: dict[str, str]) -> dict[str, dict[str, str]]:
    """Minimal raw interface block, as `collect()` produces it."""
    return {"descr": descr}


def _raw(
    *, lldp=None, cdp=None, descr=None,
    lldp_status=NEIGHBOR_COLLECTED, cdp_status=NEIGHBOR_UNAVAILABLE,
) -> dict:
    # `descr=None` means "use the default table"; an explicit empty dict means
    # "no interfaces were collected" and must NOT fall back to the default.
    descr_map = {"5": "Gi1/0/1", "6": "Gi1/0/2"} if descr is None else descr
    return {
        "sys_name": "SW-A",
        "sys_descr": "Cisco IOS",
        "interfaces": _interfaces(descr_map),
        "lldp": lldp or {k: {} for k in ("sys_name", "chassis_id", "port_id", "port_desc", "man_addr")},
        "cdp": cdp or {k: {} for k in ("address", "device_id", "device_port", "platform", "version")},
        "lldp_status": lldp_status,
        "cdp_status": cdp_status,
    }


def _lldp_row(
    row_index="100.5.1", *, sys_name="SW-B", chassis="aa:bb:cc:dd:ee:ff",
    port_id="Gi1/0/24", port_desc=None, mgmt_ip="10.0.0.2",
) -> dict:
    """Build an LLDP remote row the way `_collect_lldp` shapes it."""
    lldp = {k: {} for k in ("sys_name", "chassis_id", "port_id", "port_desc", "man_addr")}
    if sys_name is not None:
        lldp["sys_name"][row_index] = sys_name
    if chassis is not None:
        lldp["chassis_id"][row_index] = chassis
    if port_id is not None:
        lldp["port_id"][row_index] = port_id
    if port_desc is not None:
        lldp["port_desc"][row_index] = port_desc
    if mgmt_ip is not None:
        lldp["man_addr"][row_index] = mgmt_ip
    return lldp


def _cdp_row(
    row_index="5.1", *, ip="10.0.0.2", device_id="SW-B.lab",
    device_port="GigabitEthernet1/0/24", platform="cisco WS-C3750",
) -> dict:
    cdp = {k: {} for k in ("address", "device_id", "device_port", "platform", "version")}
    if ip is not None:
        cdp["address"][row_index] = ip
    if device_id is not None:
        cdp["device_id"][row_index] = device_id
    if device_port is not None:
        cdp["device_port"][row_index] = device_port
    if platform is not None:
        cdp["platform"][row_index] = platform
    return cdp


class TestParseHelpers:

    def test_suffix_after(self):
        assert _suffix_after(f"{_LLDP_BASE}.5.100.5.1", _LLDP_BASE) == "5.100.5.1"
        assert _suffix_after(".1.3.6.1.2.1.1.1.0", _LLDP_BASE) is None
        assert _suffix_after(_LLDP_BASE, _LLDP_BASE) is None

    def test_lldp_row_index(self):
        assert _parse_lldp_row_index("100.5.1") == ("100", "5", "1")

    def test_lldp_row_index_rejects_malformed(self):
        for bad in ("100.5", "100.5.1.2", "a.b.c", "", "100.5.x"):
            assert _parse_lldp_row_index(bad) is None, bad

    def test_lldp_man_addr_decodes_ipv4(self):
        key, ip = _parse_lldp_man_addr("100.5.1.1.4.10.0.0.2")
        assert key == "100.5.1"
        assert ip == "10.0.0.2"

    def test_lldp_man_addr_ignores_non_ipv4_family(self):
        # Well-formed IPv6 management address: subtype 6, length 16 octets.
        octets = [0x20, 0x01, 0x0D, 0xB8] + [0] * 12
        suffix = "100.5.1.6.16." + ".".join(str(o) for o in octets)
        key, ip = _parse_lldp_man_addr(suffix)
        assert key == "100.5.1"
        assert ip is None  # never guessed into IPv4

    def test_lldp_man_addr_rejects_truncated_address(self):
        assert _parse_lldp_man_addr("100.5.1.1.4.10.0") is None

    def test_cdp_row_index(self):
        assert _parse_cdp_row_index("5.1") == ("5", "1")
        assert _parse_cdp_row_index("5") is None
        assert _parse_cdp_row_index("5.1.2") is None


class TestIpv4Coercion:
    """Regression: an address whose last octet is 10/13/32/9 encodes as
    0x0a/0x0d/0x20/0x09. Stripping the raw bytes before the length check
    truncated or corrupted exactly those addresses."""

    def test_raw_octets_with_trailing_0x0a(self):
        assert _coerce_ipv4("\x0a\x00\x00\x0a") == "10.0.0.10"

    def test_raw_octets_with_trailing_0x0d(self):
        assert _coerce_ipv4("\x0a\x00\x00\x0d") == "10.0.0.13"

    def test_raw_octets_with_trailing_0x20(self):
        assert _coerce_ipv4("\x0a\x00\x00\x20") == "10.0.0.32"

    def test_raw_octets_generic(self):
        assert _coerce_ipv4("\xc0\xa8\x01\x0a") == "192.168.1.10"

    def test_dotted_text(self):
        assert _coerce_ipv4("192.168.1.1") == "192.168.1.1"

    def test_hex_form(self):
        assert _coerce_ipv4("c0a80101") == "192.168.1.1"
        assert _coerce_ipv4("0a00000a") == "10.0.0.10"

    def test_unparseable_returns_none_never_a_guess(self):
        for bad in (None, "", "   ", "not-an-address", "1.2.3", "999.999.999.999"):
            assert _coerce_ipv4(bad) is None, bad


class TestLldpNormalization:

    def test_single_neighbor(self):
        bundle = _collector().normalize(_raw(lldp=_lldp_row()))
        assert len(bundle.neighbors) == 1
        nb = bundle.neighbors[0]
        assert isinstance(nb, NormalizedNeighbor)
        assert nb.evidence_type == "lldp"
        assert nb.device_external_id == "sw-a.lab"
        assert nb.local_interface == "Gi1/0/1"      # ifIndex 5 -> ifDescr
        assert nb.remote_address == "10.0.0.2"
        assert nb.remote_identity == "SW-B"
        assert nb.remote_interface == "Gi1/0/24"
        assert nb.remote_chassis_id == "aa:bb:cc:dd:ee:ff"

    def test_multiple_neighbors(self):
        lldp = _lldp_row("100.5.1", sys_name="SW-B", mgmt_ip="10.0.0.2")
        lldp["sys_name"]["100.6.1"] = "SW-C"
        lldp["port_id"]["100.6.1"] = "Gi1/0/1"
        lldp["man_addr"]["100.6.1"] = "10.0.0.3"
        bundle = _collector().normalize(_raw(lldp=lldp))
        assert len(bundle.neighbors) == 2
        assert {n.remote_address for n in bundle.neighbors} == {"10.0.0.2", "10.0.0.3"}
        assert {n.local_interface for n in bundle.neighbors} == {"Gi1/0/1", "Gi1/0/2"}

    def test_port_desc_used_when_port_id_absent(self):
        bundle = _collector().normalize(
            _raw(lldp=_lldp_row(port_id=None, port_desc="to-SW-B"))
        )
        assert bundle.neighbors[0].remote_interface == "to-SW-B"

    def test_missing_management_address_still_yields_evidence(self):
        """The adjacency is real; only the identity is unknown. No edge will be
        written, but the neighbour is not discarded or invented."""
        bundle = _collector().normalize(_raw(lldp=_lldp_row(mgmt_ip=None)))
        assert len(bundle.neighbors) == 1
        assert bundle.neighbors[0].remote_address is None
        assert bundle.neighbors[0].remote_identity == "SW-B"

    def test_missing_chassis_id_is_tolerated(self):
        bundle = _collector().normalize(_raw(lldp=_lldp_row(chassis=None)))
        assert bundle.neighbors[0].remote_chassis_id is None

    def test_missing_local_port_yields_no_local_interface(self):
        """An unresolvable local port must not become an invented interface."""
        bundle = _collector().normalize(_raw(lldp=_lldp_row(), descr={}))
        assert bundle.neighbors[0].local_interface is None

    def test_malformed_row_index_is_skipped(self):
        lldp = _lldp_row()
        lldp["sys_name"]["not.an.index.at.all"] = "Bogus"
        bundle = _collector().normalize(_raw(lldp=lldp))
        assert len(bundle.neighbors) == 1
        assert bundle.neighbors[0].remote_identity == "SW-B"

    def test_empty_lldp_table_yields_no_neighbors(self):
        bundle = _collector().normalize(
            _raw(lldp_status=NEIGHBOR_EMPTY, cdp_status=NEIGHBOR_EMPTY)
        )
        assert bundle.neighbors == []
        assert bundle.neighbor_status == {"lldp": NEIGHBOR_EMPTY, "cdp": NEIGHBOR_EMPTY}

    def test_malformed_values_do_not_raise(self):
        lldp = {k: {} for k in ("sys_name", "chassis_id", "port_id", "port_desc", "man_addr")}
        lldp["sys_name"]["100.5.1"] = ""
        lldp["chassis_id"]["100.5.1"] = None
        lldp["port_id"]["100.5.1"] = "???"
        lldp["man_addr"]["100.5.1"] = "garbage"
        bundle = _collector().normalize(_raw(lldp=lldp))
        assert len(bundle.neighbors) == 1
        assert bundle.neighbors[0].remote_address is None


class TestCdpNormalization:

    def test_single_neighbor(self):
        bundle = _collector().normalize(
            _raw(cdp=_cdp_row(), lldp_status=NEIGHBOR_UNAVAILABLE,
                 cdp_status=NEIGHBOR_COLLECTED)
        )
        assert len(bundle.neighbors) == 1
        nb = bundle.neighbors[0]
        assert nb.evidence_type == "cdp"
        assert nb.local_interface == "Gi1/0/1"       # cdpCacheIfIndex 5
        assert nb.remote_address == "10.0.0.2"
        assert nb.remote_identity == "SW-B.lab"
        assert nb.remote_interface == "GigabitEthernet1/0/24"
        assert nb.remote_platform == "cisco WS-C3750"

    def test_multiple_neighbors(self):
        cdp = _cdp_row("5.1", device_id="SW-B", ip="10.0.0.2")
        cdp["address"]["6.1"] = "10.0.0.3"
        cdp["device_id"]["6.1"] = "SW-C"
        cdp["device_port"]["6.1"] = "Gi0/1"
        bundle = _collector().normalize(
            _raw(cdp=cdp, lldp_status=NEIGHBOR_UNAVAILABLE,
                 cdp_status=NEIGHBOR_COLLECTED)
        )
        assert len(bundle.neighbors) == 2
        assert {n.remote_identity for n in bundle.neighbors} == {"SW-B", "SW-C"}

    def test_cdp_absent_is_not_an_error(self):
        """A non-Cisco device has no CDP tables. That is `unavailable`, and the
        collection itself still succeeded."""
        bundle = _collector().normalize(
            _raw(lldp_status=NEIGHBOR_UNAVAILABLE, cdp_status=NEIGHBOR_UNAVAILABLE)
        )
        assert bundle.neighbors == []
        assert bundle.neighbor_status["cdp"] == NEIGHBOR_UNAVAILABLE
        assert bundle.devices and bundle.devices[0].status == "up"

    def test_empty_cdp_table_yields_no_neighbors(self):
        bundle = _collector().normalize(
            _raw(cdp_status=NEIGHBOR_EMPTY, lldp_status=NEIGHBOR_EMPTY)
        )
        assert bundle.neighbors == []

    def test_cdp_row_without_address_is_preserved_without_an_address(self):
        bundle = _collector().normalize(
            _raw(cdp=_cdp_row(ip=None), cdp_status=NEIGHBOR_COLLECTED,
                 lldp_status=NEIGHBOR_UNAVAILABLE)
        )
        assert len(bundle.neighbors) == 1
        assert bundle.neighbors[0].remote_address is None
        assert bundle.neighbors[0].remote_identity == "SW-B.lab"


class TestDuplicateAdjacency:
    """Step 9: LLDP and CDP commonly report the same link. It must stay one
    logical relationship."""

    def test_same_adjacency_from_both_protocols_collapses(self):
        lldp = _lldp_row("100.5.1", mgmt_ip="10.0.0.2", port_id="Gi1/0/24")
        cdp = _cdp_row("5.1", ip="10.0.0.2", device_port="GigabitEthernet1/0/24")
        bundle = _collector().normalize(
            _raw(lldp=lldp, cdp=cdp, cdp_status=NEIGHBOR_COLLECTED)
        )
        assert len(bundle.neighbors) == 1
        # LLDP wins provenance: it is the standard protocol.
        assert bundle.neighbors[0].evidence_type == "lldp"

    def test_genuinely_different_adjacencies_are_both_kept(self):
        lldp = _lldp_row("100.5.1", sys_name="SW-B", mgmt_ip="10.0.0.2")
        cdp = _cdp_row("5.1", ip="10.0.0.9", device_id="SW-Z")
        bundle = _collector().normalize(
            _raw(lldp=lldp, cdp=cdp, cdp_status=NEIGHBOR_COLLECTED)
        )
        assert len(bundle.neighbors) == 2
        assert {n.evidence_type for n in bundle.neighbors} == {"lldp", "cdp"}

    def test_self_adjacency_is_still_recorded_for_the_writer_to_reject(self):
        """If a device reports ITSELF as a neighbour the evidence is preserved
        here; DiscoveryService drops it (target == source), so no self-loop edge
        is written. The collector does not silently 'fix' the data."""
        bundle = _collector().normalize(_raw(lldp=_lldp_row(mgmt_ip="10.0.0.1")))
        assert len(bundle.neighbors) == 1
        assert bundle.neighbors[0].remote_address == "10.0.0.1"


class TestNeighborStatusSemantics:
    """Step 10: a FAILED poll must never be reported as 'no neighbours'."""

    def test_failed_is_distinct_from_empty(self):
        failed = _collector().normalize(
            _raw(lldp_status=NEIGHBOR_FAILED, cdp_status=NEIGHBOR_FAILED)
        )
        empty = _collector().normalize(
            _raw(lldp_status=NEIGHBOR_EMPTY, cdp_status=NEIGHBOR_EMPTY)
        )
        assert failed.neighbor_status != empty.neighbor_status
        assert failed.neighbor_status["lldp"] == NEIGHBOR_FAILED
        assert empty.neighbor_status["lldp"] == NEIGHBOR_EMPTY
        # Neither produced edges - but for different, stated reasons.
        assert failed.neighbors == [] and empty.neighbors == []

    def test_missing_status_defaults_to_unavailable_not_empty(self):
        raw = _raw()
        raw.pop("lldp_status")
        raw.pop("cdp_status")
        bundle = _collector().normalize(raw)
        assert bundle.neighbor_status == {
            "lldp": NEIGHBOR_UNAVAILABLE, "cdp": NEIGHBOR_UNAVAILABLE
        }

    def test_existing_interface_behaviour_is_unchanged(self):
        """Step 12/21: adding neighbours must not disturb interface output."""
        bundle = _collector().normalize(_raw(lldp=_lldp_row()))
        assert len(bundle.interfaces) == 2
        assert {i.name for i in bundle.interfaces} == {"Gi1/0/1", "Gi1/0/2"}
        assert all(i.device_external_id == "sw-a.lab" for i in bundle.interfaces)


# ---------------------------------------------------------------------------
# Persistence: BundleSaver -> DiscoveryService -> ResourceRelationship ->
# topology graph. Runs the REAL pipeline; nothing is bypassed or stubbed.
#
# Fixture order is (tenant, monitoring_source_factory, db) so the uncommitted
# FK locks in `db` roll back BEFORE the tenant/source teardown - see the
# deadlock note in tests/test_infrastructure_overview.py.
# ---------------------------------------------------------------------------

async def _seed_device(db, tenant, source, *, name, external_id, ip):
    device = Device(
        tenant_id=tenant.id, monitoring_source_id=source.id,
        external_id=external_id, name=name, device_type="switch",
        status="up", ip_address=ip,
    )
    db.add(device)
    await db.flush()
    return device


def _saver(db):
    from app.repositories.event_repository import EventRepository
    from app.repositories.metric_repository import MetricRepository
    from app.services.monitoring.bundle_saver import BundleSaver

    return BundleSaver(
        device_repo=DeviceRepository(db),
        interface_repo=InterfaceRepository(db),
        sensor_repo=SensorRepository(db),
        metric_repo=MetricRepository(db),
        event_repo=EventRepository(db),
    )


def _bundle_for_a(neighbors, *, status=None):
    """A bundle for device SW-A carrying its observed adjacencies.

    The local device MUST be present in `devices`: BundleSaver resolves
    `device_external_id` against the devices it upserts in THIS bundle, so a
    neighbour bundle without its local device has no endpoint to attach to.
    """
    return NormalizedBundle(
        devices=[NormalizedDevice(
            external_id="sw-a", name="SW-A", device_type="switch",
            ip_address="10.0.0.1", status="up",
        )],
        neighbors=neighbors,
        neighbor_status=status or {"lldp": NEIGHBOR_COLLECTED, "cdp": NEIGHBOR_UNAVAILABLE},
    )


def _lldp_neighbor(remote_address, *, local="Gi1/0/1", remote="Gi1/0/24",
                   identity="SW-B"):
    return NormalizedNeighbor(
        device_external_id="sw-a", local_interface=local,
        remote_address=remote_address, remote_identity=identity,
        remote_interface=remote, evidence_type="lldp",
    )


class TestNeighborPersistence:

    async def test_lldp_neighbor_creates_a_connected_to_edge(
        self, tenant, monitoring_source_factory, db
    ):
        """Step 15: the integration test this whole phase exists for."""
        source = await monitoring_source_factory(source_type="snmp")
        device_a = await _seed_device(
            db, tenant, source, name="SW-A", external_id="sw-a", ip="10.0.0.1"
        )
        device_b = await _seed_device(
            db, tenant, source, name="SW-B", external_id="sw-b", ip="10.0.0.2"
        )

        result = await _saver(db).save(source, _bundle_for_a([_lldp_neighbor("10.0.0.2")]))

        assert result.neighbors_written == 1
        assert result.neighbors_unresolved == 0

        edges = await ResourceRelationshipRepository(db).list(tenant.id)
        assert len(edges) == 1
        edge = edges[0]
        assert edge.relationship_type == "connected_to"
        assert edge.source_resource_id == device_a.id
        assert edge.target_resource_id == device_b.id
        assert edge.discovered_by == "lldp"          # provenance preserved
        assert edge.attributes["evidence_type"] == "lldp"
        assert edge.attributes["local_interface"] == "Gi1/0/1"
        assert edge.attributes["remote_interface"] == "Gi1/0/24"

    async def test_topology_graph_reports_ready_with_the_new_edge(
        self, tenant, monitoring_source_factory, db
    ):
        """The existing topology API must expose the generated relationship."""
        source = await monitoring_source_factory(source_type="snmp")
        await _seed_device(db, tenant, source, name="SW-A", external_id="sw-a", ip="10.0.0.1")
        await _seed_device(db, tenant, source, name="SW-B", external_id="sw-b", ip="10.0.0.2")

        await _saver(db).save(source, _bundle_for_a([_lldp_neighbor("10.0.0.2")]))

        graph = await TopologyGraphService(db).build(tenant.id)
        assert graph["summary"]["edges_total"] == 1
        assert graph["status"] == "ready"
        # The "no relationship evidence" limitation must be GONE now that an
        # edge exists. Limitations about interfaces/sites (0 collected, no site
        # association) are still true and still reported - they are unrelated to
        # edges, and suppressing them would hide real gaps.
        assert MESSAGE_NOT_DISCOVERED not in graph["limitations"]
        edge = graph["edges"][0]
        assert edge["relationship_type"] == "connected_to"
        assert edge["discovered_by"] == "lldp"
        assert edge["unknown_relationship_type"] is False
        for forbidden in ("speed_mbps", "vlan", "latency_ms", "bandwidth"):
            assert forbidden not in edge

    async def test_duplicate_lldp_and_cdp_evidence_yields_one_edge(
        self, tenant, monitoring_source_factory, db
    ):
        """Step 16: two protocols, one logical relationship."""
        source = await monitoring_source_factory(source_type="snmp")
        await _seed_device(db, tenant, source, name="SW-A", external_id="sw-a", ip="10.0.0.1")
        await _seed_device(db, tenant, source, name="SW-B", external_id="sw-b", ip="10.0.0.2")
        saver = _saver(db)

        await saver.save(source, _bundle_for_a([_lldp_neighbor("10.0.0.2")]))
        # Same adjacency, reported by CDP this time.
        await saver.save(source, _bundle_for_a([
            NormalizedNeighbor(
                device_external_id="sw-a", local_interface="Gi1/0/1",
                remote_address="10.0.0.2", remote_identity="SW-B",
                remote_interface="GigabitEthernet1/0/24", evidence_type="cdp",
            )
        ]))

        assert await ResourceRelationshipRepository(db).count(tenant.id) == 1
        assert (await TopologyGraphService(db).build(tenant.id))["summary"]["edges_total"] == 1

    async def test_unknown_remote_device_creates_no_edge_and_no_device(
        self, tenant, monitoring_source_factory, db
    ):
        """Step 5: an unresolvable neighbour is reported, never fabricated."""
        source = await monitoring_source_factory(source_type="snmp")
        await _seed_device(db, tenant, source, name="SW-A", external_id="sw-a", ip="10.0.0.1")
        devices_before = await DeviceRepository(db).count(tenant.id)

        result = await _saver(db).save(
            source, _bundle_for_a([_lldp_neighbor("192.0.2.99")])
        )

        assert result.neighbors_written == 0
        assert result.neighbors_unresolved == 1
        assert await ResourceRelationshipRepository(db).count(tenant.id) == 0
        assert await DeviceRepository(db).count(tenant.id) == devices_before

    async def test_neighbour_without_management_address_creates_no_edge(
        self, tenant, monitoring_source_factory, db
    ):
        source = await monitoring_source_factory(source_type="snmp")
        await _seed_device(db, tenant, source, name="SW-A", external_id="sw-a", ip="10.0.0.1")

        result = await _saver(db).save(
            source, _bundle_for_a([_lldp_neighbor(None)])
        )

        assert result.neighbors_written == 0
        assert result.neighbors_unresolved == 1
        assert await ResourceRelationshipRepository(db).count(tenant.id) == 0

    async def test_self_reported_neighbour_creates_no_self_loop(
        self, tenant, monitoring_source_factory, db
    ):
        source = await monitoring_source_factory(source_type="snmp")
        await _seed_device(db, tenant, source, name="SW-A", external_id="sw-a", ip="10.0.0.1")

        result = await _saver(db).save(
            source, _bundle_for_a([_lldp_neighbor("10.0.0.1")])
        )

        assert result.neighbors_written == 0
        assert await ResourceRelationshipRepository(db).count(tenant.id) == 0

    async def test_neighbour_in_another_tenant_is_never_linked(
        self, tenant, monitoring_source_factory, db
    ):
        """Step 17: an identical IP in another tenant must not resolve here."""
        source = await monitoring_source_factory(source_type="snmp")
        await _seed_device(db, tenant, source, name="SW-A", external_id="sw-a", ip="10.0.0.1")

        other_tenant = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
        db.add(other_tenant)
        await db.flush()
        other_source = MonitoringSource(
            tenant_id=other_tenant.id, name="other-src", source_type="snmp",
            is_active=True, poll_interval_seconds=60, status="pending",
        )
        db.add(other_source)
        await db.flush()
        # Same IP as the neighbour below, but a DIFFERENT tenant.
        await _seed_device(db, other_tenant, other_source, name="SW-B",
                          external_id="sw-b", ip="10.0.0.2")

        result = await _saver(db).save(
            source, _bundle_for_a([_lldp_neighbor("10.0.0.2")])
        )

        assert result.neighbors_written == 0
        assert result.neighbors_unresolved == 1
        assert await ResourceRelationshipRepository(db).count(tenant.id) == 0
        assert await ResourceRelationshipRepository(db).count(other_tenant.id) == 0

    async def test_neighbour_status_is_propagated_to_the_save_result(
        self, tenant, monitoring_source_factory, db
    ):
        """Step 10: callers can tell a failed poll from an empty table."""
        source = await monitoring_source_factory(source_type="snmp")
        await _seed_device(db, tenant, source, name="SW-A", external_id="sw-a", ip="10.0.0.1")

        result = await _saver(db).save(source, _bundle_for_a(
            [], status={"lldp": NEIGHBOR_FAILED, "cdp": NEIGHBOR_UNAVAILABLE},
        ))

        assert result.neighbor_status["lldp"] == NEIGHBOR_FAILED
        assert result.neighbor_status["lldp"] != NEIGHBOR_EMPTY

    async def test_bundle_without_neighbors_is_unaffected(
        self, tenant, monitoring_source_factory, db
    ):
        """A device that speaks no adjacency protocol behaves exactly as before
        Phase 3A."""
        source = await monitoring_source_factory(source_type="prtg")
        await _seed_device(db, tenant, source, name="PC", external_id="pc", ip="10.0.0.5")

        result = await _saver(db).save(source, NormalizedBundle(devices=[]))
        assert result.neighbors_written == 0
        assert result.neighbors_unresolved == 0
        assert result.neighbor_status == {}
        assert await ResourceRelationshipRepository(db).count(tenant.id) == 0
