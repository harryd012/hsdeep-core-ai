"""
L3 topology: pure builder, persistence (BundleSaver sync), the real SNMP agent,
and the API (tenant isolation, site scoping).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.collectors.base import (
    NormalizedBundle,
    NormalizedDevice,
    NormalizedInterface,
    NormalizedInterfaceAddress,
    NormalizedRoute,
)
from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.core.ip_addressing import parse_interface_address
from app.main import app
from app.models.device import Device
from app.models.interface_address import InterfaceAddress
from app.models.network_route import NetworkRoute
from app.models.site import Site
from app.models.tenant import Tenant
from app.services.topology_graph_service import PREFIX_DEVICE
from app.services.topology_l3_service import (
    PREFIX_NEXT_HOP,
    PREFIX_SUBNET,
    SiteNotFound,
    TopologyL3Service,
    build_l3_topology,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Pure builder
# ---------------------------------------------------------------------------
def _dev(name: str, site_id=None, device_type="router"):
    return SimpleNamespace(id=uuid.uuid4(), name=name, device_type=device_type, site_id=site_id)


def _addr(device, cidr: str, *, vrf="-", interface="eth0", provider="snmp"):
    p = parse_interface_address(cidr)
    return SimpleNamespace(
        device_id=device.id, address=p.address, prefix_length=p.prefix_length,
        network=p.network, family=p.family, vrf=vrf, interface_name=interface,
        is_primary=True, provider=provider,
    )


def _route(device, destination: str, next_hop: str | None, *, protocol="static", vrf="-"):
    return SimpleNamespace(
        resource_id=device.id, destination=destination, next_hop=next_hop,
        protocol=protocol, vrf=vrf, outgoing_interface="wan1", metric=None,
        is_best=True, provider="fortigate",
    )


def node(device) -> str:
    return f"{PREFIX_DEVICE}{device.id}"


class TestBuilder:
    def test_two_devices_on_a_subnet_get_a_direct_edge(self):
        a, b = _dev("a"), _dev("b")
        out = build_l3_topology([a, b], [_addr(a, "10.0.0.1/30"), _addr(b, "10.0.0.2/30")], [])
        assert out["nodes"] == []
        [edge] = out["edges"]
        assert {edge["source"], edge["target"]} == {node(a), node(b)}
        assert edge["kind"] == "subnet" and edge["network"] == "10.0.0.0/30"
        assert {edge["source_address"], edge["target_address"]} == {"10.0.0.1", "10.0.0.2"}

    def test_three_devices_make_a_pipe(self):
        devs = [_dev(n) for n in "abc"]
        out = build_l3_topology(devs, [_addr(d, f"10.0.0.{i + 1}/24") for i, d in enumerate(devs)], [])
        [pipe] = out["nodes"]
        assert pipe["kind"] == "subnet" and pipe["member_count"] == 3
        assert pipe["id"].startswith(PREFIX_SUBNET)
        assert {e["target"] for e in out["edges"]} == {node(d) for d in devs}
        assert all(e["source"] == pipe["id"] for e in out["edges"])

    def test_single_member_subnet_is_only_a_hover_detail(self):
        a = _dev("a")
        out = build_l3_topology([a], [_addr(a, "10.0.0.1/24")], [])
        assert out["edges"] == [] and out["nodes"] == []
        assert out["unlinked"] == [node(a)]
        assert out["devices"][node(a)]["addresses"][0]["network"] == "10.0.0.0/24"

    def test_loopbacks_and_host_routes_never_connect(self):
        a, b = _dev("a"), _dev("b")
        out = build_l3_topology(
            [a, b],
            [_addr(a, "127.0.0.1/8"), _addr(b, "127.0.0.1/8"),
             _addr(a, "10.255.0.1/32"), _addr(b, "10.255.0.1/32")],
            [],
        )
        assert out["edges"] == []

    def test_same_subnet_in_two_sites_stays_separate(self):
        site1, site2 = uuid.uuid4(), uuid.uuid4()
        a, b = _dev("a", site1), _dev("b", site2)
        out = build_l3_topology([a, b], [_addr(a, "192.168.1.1/24"), _addr(b, "192.168.1.2/24")], [])
        assert out["edges"] == []

    def test_vrfs_stay_separate(self):
        a, b = _dev("a"), _dev("b")
        out = build_l3_topology(
            [a, b], [_addr(a, "10.0.0.1/24", vrf="A"), _addr(b, "10.0.0.2/24", vrf="B")], []
        )
        assert out["edges"] == []

    def test_unknown_default_next_hop_is_a_diamond_and_the_root(self):
        fw, sw = _dev("fw", device_type="firewall"), _dev("sw")
        out = build_l3_topology(
            [fw, sw],
            [_addr(fw, "203.0.113.2/29"), _addr(fw, "10.0.0.1/24"), _addr(sw, "10.0.0.2/24")],
            [_route(fw, "0.0.0.0/0", "203.0.113.1"), _route(sw, "0.0.0.0/0", "10.0.0.1")],
        )
        [diamond] = [n for n in out["nodes"] if n["kind"] == "next_hop"]
        assert diamond["id"].startswith(PREFIX_NEXT_HOP) and diamond["address"] == "203.0.113.1"
        assert out["roots"] == [diamond["id"]]
        route_edges = [e for e in out["edges"] if e["kind"] == "route"]
        # sw's default route resolves to fw, which it already shares a subnet with.
        assert [(e["source"], e["target"]) for e in route_edges] == [(diamond["id"], node(fw))]
        assert out["devices"][node(sw)]["default_route"]["next_hop_node"] == node(fw)

    def test_shared_next_hop_diamond_is_deduplicated(self):
        a, b = _dev("a"), _dev("b")
        out = build_l3_topology(
            [a, b], [],
            [_route(a, "0.0.0.0/0", "198.51.100.1"), _route(b, "0.0.0.0/0", "198.51.100.1")],
        )
        diamonds = [n for n in out["nodes"] if n["kind"] == "next_hop"]
        assert len(diamonds) == 1 and diamonds[0]["route_count"] == 2
        assert len(out["edges"]) == 2

    def test_static_route_to_a_known_device_without_shared_subnet_draws_a_route_edge(self):
        a, b = _dev("a"), _dev("b")
        out = build_l3_topology(
            [a, b], [_addr(b, "10.9.0.1/24")], [_route(a, "172.16.0.0/12", "10.9.0.1")]
        )
        [edge] = out["edges"]
        assert edge["kind"] == "route" and edge["source"] == node(b) and edge["target"] == node(a)
        assert edge["destinations"] == ["172.16.0.0/12"]

    def test_dynamic_routes_are_counted_but_never_drawn(self):
        a = _dev("a")
        out = build_l3_topology([a], [], [_route(a, "10.1.0.0/16", "10.0.0.9", protocol="bgp")])
        assert out["edges"] == [] and out["nodes"] == []
        assert out["devices"][node(a)]["routes_by_protocol"] == {"bgp": 1}

    def test_coverage_and_determinism(self):
        a, b, c = _dev("a"), _dev("b"), _dev("c")
        addresses = [_addr(a, "10.0.0.1/24"), _addr(b, "10.0.0.2/24", provider="fortigate")]
        routes = [_route(a, "0.0.0.0/0", "10.0.0.254")]
        first = build_l3_topology([a, b, c], addresses, routes)
        second = build_l3_topology([c, b, a], list(reversed(addresses)), routes)
        assert first == second
        assert first["coverage"] == {
            "devices_total": 3, "devices_with_addresses": 2, "devices_with_routes": 1,
            "subnets": 1, "providers": ["fortigate", "snmp"],
        }


# ---------------------------------------------------------------------------
# Persistence: BundleSaver -> interface_addresses / network_routes
# ---------------------------------------------------------------------------
def _saver(db):
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


def _bundle(addresses, routes, status):
    return NormalizedBundle(
        devices=[NormalizedDevice(external_id="fw1", name="fw1", device_type="firewall", status="up")],
        interfaces=[NormalizedInterface(device_external_id="fw1", external_id="port1", name="port1")],
        addresses=addresses,
        routes=routes,
        l3_status=status,
    )


ADDR_A = NormalizedInterfaceAddress(device_external_id="fw1", interface_external_id="port1",
                                    interface_name="port1", address="10.0.0.1", prefix_length=24)
ADDR_B = NormalizedInterfaceAddress(device_external_id="fw1", interface_name="port2",
                                    address="10.0.1.1", prefix_length=24)
DEFAULT = NormalizedRoute(device_external_id="fw1", destination="0.0.0.0/0", prefix_length=0,
                          protocol="static", next_hop="203.0.113.1", outgoing_interface="port1")


async def _active(db, model, tenant_id):
    stmt = select(model).where(model.tenant_id == tenant_id, model.is_deleted.is_(False))
    return list((await db.execute(stmt)).scalars().all())


class TestPersistence:
    async def test_addresses_and_routes_are_stored_and_resolved_to_interfaces(
        self, db, monitoring_source_factory
    ):
        source = await monitoring_source_factory(source_type="fortigate")
        result = await _saver(db).save(
            source, _bundle([ADDR_A, ADDR_B], [DEFAULT], {"addresses": "collected", "routes": "collected"})
        )
        await db.commit()
        assert (result.addresses_upserted, result.routes_upserted) == (2, 1)

        rows = {r.address: r for r in await _active(db, InterfaceAddress, source.tenant_id)}
        assert rows["10.0.0.1"].network == "10.0.0.0/24"
        assert rows["10.0.0.1"].interface_id is not None  # port1 was in the bundle
        assert rows["10.0.1.1"].interface_id is None and rows["10.0.1.1"].interface_name == "port2"
        [route] = await _active(db, NetworkRoute, source.tenant_id)
        assert route.destination == "0.0.0.0/0" and route.provider == "fortigate"
        assert route.interface_id == rows["10.0.0.1"].interface_id

    async def test_rows_missing_from_a_successful_poll_are_retired_and_can_return(
        self, db, monitoring_source_factory
    ):
        source = await monitoring_source_factory(source_type="fortigate")
        saver = _saver(db)
        await saver.save(source, _bundle([ADDR_A, ADDR_B], [DEFAULT], {"addresses": "collected", "routes": "collected"}))
        result = await saver.save(source, _bundle([ADDR_A], [], {"addresses": "collected", "routes": "empty"}))
        await db.commit()
        assert (result.addresses_retired, result.routes_retired) == (1, 1)
        assert [r.address for r in await _active(db, InterfaceAddress, source.tenant_id)] == ["10.0.0.1"]
        assert await _active(db, NetworkRoute, source.tenant_id) == []

        await saver.save(source, _bundle([ADDR_A, ADDR_B], [DEFAULT], {"addresses": "collected", "routes": "collected"}))
        await db.commit()
        assert len(await _active(db, InterfaceAddress, source.tenant_id)) == 2
        assert len(await _active(db, NetworkRoute, source.tenant_id)) == 1

    async def test_a_failed_read_never_wipes_stored_rows(self, db, monitoring_source_factory):
        source = await monitoring_source_factory(source_type="fortigate")
        saver = _saver(db)
        await saver.save(source, _bundle([ADDR_A], [DEFAULT], {"addresses": "collected", "routes": "collected"}))
        for status in ({"addresses": "failed", "routes": "failed"},
                       {"addresses": "unavailable", "routes": "partial"},
                       {}):
            result = await saver.save(source, _bundle([], [], status))
            assert (result.addresses_retired, result.routes_retired) == (0, 0)
        await db.commit()
        assert len(await _active(db, InterfaceAddress, source.tenant_id)) == 1
        assert len(await _active(db, NetworkRoute, source.tenant_id)) == 1

    async def test_dynamic_routes_are_capped_but_static_are_kept(
        self, db, monitoring_source_factory, monkeypatch
    ):
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "l3_max_dynamic_routes_per_device", 2)
        source = await monitoring_source_factory(source_type="fortigate")
        bgp = [NormalizedRoute(device_external_id="fw1", destination=f"10.{i}.0.0/16", prefix_length=16,
                               protocol="bgp", next_hop="10.255.0.1") for i in range(5)]
        result = await _saver(db).save(source, _bundle([], [DEFAULT, *bgp], {"routes": "collected"}))
        await db.commit()
        assert result.routes_capped == 3
        assert result.routes_upserted == 3  # default + 2 bgp


# ---------------------------------------------------------------------------
# Real SNMP agent: the collector reads IP-MIB / IP-FORWARD-MIB end to end
# ---------------------------------------------------------------------------
class TestSnmpLabAgent:
    async def test_collector_reads_addresses_and_routes_from_a_real_agent(self):
        from scripts.lab_snmp_agent import LabAddress, LabDeviceProfile, LabInterface, LabRoute
        from tests.test_snmp_evidence_pipeline import collect_bundle

        profile = LabDeviceProfile(
            sys_name="hsdeep-lab-l3",
            sys_descr="HSDEEP Lab SNMP Agent - L3 validation endpoint",
            interfaces=[LabInterface(1, "Gi0/1"), LabInterface(2, "Gi0/2")],
            # 10.0.0.10 ends in 0x0a: the raw-octet IpAddress edge case.
            addresses=[LabAddress(1, "10.0.0.10", "255.255.255.0"),
                       LabAddress(2, "203.0.113.2", "255.255.255.248")],
            routes=[LabRoute("0.0.0.0", "0.0.0.0", "203.0.113.1", 2, proto=3),
                    LabRoute("10.0.0.0", "255.255.255.0", "0.0.0.0", 1, proto=2)],
        )
        _check, _raw, bundle, _agent = await collect_bundle(profile)

        assert {(a.address, a.prefix_length, a.interface_name) for a in bundle.addresses} == {
            ("10.0.0.10", 24, "Gi0/1"), ("203.0.113.2", 29, "Gi0/2"),
        }
        routes = {r.destination: r for r in bundle.routes}
        assert routes["0.0.0.0/0"].next_hop == "203.0.113.1"
        assert routes["0.0.0.0/0"].protocol == "static"
        assert routes["0.0.0.0/0"].outgoing_interface == "Gi0/2"
        assert routes["10.0.0.0/24"].protocol == "connected"
        assert bundle.l3_status == {"addresses": "collected", "routes": "collected"}

    async def test_agent_without_ip_tables_reports_empty_not_failed(self):
        from scripts.lab_snmp_agent import LAB_SWITCH_A
        from tests.test_snmp_evidence_pipeline import collect_bundle

        _check, _raw, bundle, _agent = await collect_bundle(LAB_SWITCH_A)
        assert bundle.addresses == [] and bundle.routes == []
        assert bundle.l3_status == {"addresses": "empty", "routes": "empty"}


# ---------------------------------------------------------------------------
# Service + API: tenant isolation and site scoping against the real database
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def other_tenant():
    async with AsyncSessionLocal() as db:
        t = Tenant(name="L3 Other", slug=f"l3-{uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.commit()
        await db.refresh(t)
    yield t
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == t.id))
        await db.commit()


async def _seed_pair(tenant_id, network_prefix="10.0.0", site_id=None) -> list[uuid.UUID]:
    """Two devices sharing <prefix>.0/24, committed."""
    ids = []
    async with AsyncSessionLocal() as db:
        for host in (1, 2):
            device = Device(tenant_id=tenant_id, name=f"l3-{uuid.uuid4().hex[:6]}",
                            device_type="router", status="up", site_id=site_id)
            db.add(device)
            await db.flush()
            p = parse_interface_address(f"{network_prefix}.{host}/24")
            db.add(InterfaceAddress(
                tenant_id=tenant_id, device_id=device.id, vrf="-", family=4,
                address=p.address, prefix_length=24, network=p.network,
                first_seen_at=device.created_at or _now(), last_seen_at=_now(),
            ))
            ids.append(device.id)
        await db.commit()
    return ids


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _headers(tenant_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        email=f"l3-{uuid.uuid4().hex[:8]}@test.local",
        is_superuser=False,
        permissions=frozenset({"infrastructure.read"}),
    )
    return {"Authorization": "Bearer " + token}


class TestServiceAndApi:
    async def test_identical_subnets_in_two_tenants_never_merge(self, db, tenant, other_tenant):
        mine = await _seed_pair(tenant.id)
        theirs = await _seed_pair(other_tenant.id)

        out = await TopologyL3Service(db).build(tenant.id)
        [edge] = out["edges"]
        assert {edge["source"], edge["target"]} == {f"{PREFIX_DEVICE}{d}" for d in mine}
        assert not any(f"{PREFIX_DEVICE}{d}" in out["devices"] for d in theirs)

    async def test_foreign_site_is_not_found(self, db, tenant, other_tenant):
        async with AsyncSessionLocal() as session:
            site = Site(tenant_id=other_tenant.id, name="theirs", code=f"T{uuid.uuid4().hex[:6]}")
            session.add(site)
            await session.commit()
            await session.refresh(site)
        with pytest.raises(SiteNotFound):
            await TopologyL3Service(db).build(tenant.id, site_id=site.id)

    async def test_site_filter_bounds_the_view(self, db, tenant):
        async with AsyncSessionLocal() as session:
            site = Site(tenant_id=tenant.id, name="mine", code=f"M{uuid.uuid4().hex[:6]}")
            session.add(site)
            await session.commit()
            await session.refresh(site)
        inside = await _seed_pair(tenant.id, "10.1.0", site_id=site.id)
        await _seed_pair(tenant.id, "10.2.0")

        out = await TopologyL3Service(db).build(tenant.id, site_id=site.id)
        assert set(out["devices"]) == {f"{PREFIX_DEVICE}{d}" for d in inside}
        assert out["site_id"] == str(site.id)

    async def test_query_budget_is_constant(self, tenant):
        from sqlalchemy import event

        await _seed_pair(tenant.id, "10.3.0")
        await _seed_pair(tenant.id, "10.4.0")
        async with AsyncSessionLocal() as session:
            counted: list[int] = []

            def _count(*_args):
                counted.append(1)

            engine = session.bind.sync_engine
            event.listen(engine, "before_cursor_execute", _count)
            try:
                await TopologyL3Service(session).build(tenant.id)
            finally:
                event.remove(engine, "before_cursor_execute", _count)
        assert len(counted) <= 3  # devices, addresses, routes (no site check unscoped)

    async def test_api_returns_typed_l3_graph(self, tenant):
        await _seed_pair(tenant.id, "10.5.0")
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            ok = await client.get("/api/infrastructure/topology/l3", headers=_headers(tenant.id))
            missing = await client.get(
                "/api/infrastructure/topology/l3",
                params={"site_id": str(uuid.uuid4())},
                headers=_headers(tenant.id),
            )
            anonymous = await client.get("/api/infrastructure/topology/l3")
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert len(body["edges"]) == 1
        assert body["coverage"]["devices_with_addresses"] == 2
        assert missing.status_code == 404
        assert anonymous.status_code == 401
