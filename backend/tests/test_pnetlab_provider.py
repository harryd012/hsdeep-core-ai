"""PNETLab provider tests (Phase 9.7B).

Fixtures follow the REAL PNETLab contract, not an invented one:

* responses use PNETLab's JSend envelope ``{code, status, message, data}`` with
  ``status`` in ``success | fail | forbidden``, exactly as ``api.php`` returns it;
* a link is ONE object per connection, in the shape PNETLab's own topology
  implementation emits (``api_topology.php``)::

      {"type": "ethernet", "source": "node3", "source_type": "node",
       "source_label": "port2", "destination": "node1", "destination_type": "node",
       "destination_label": "e0/0", "network_id": 1}

* node detail uses the fields PNETLab's node API exposes (``id``, ``name``,
  ``type``/``ntype``, ``status``, ``ethernets[]`` with ``name``/``network_id``,
  ``template``/``image``).

HTTP goes through ``httpx.MockTransport``, so status codes, JSON parsing, cookies
and the XSRF header all pass through httpx's real stack; only the network is
replaced. No test claims a live PNETLab was contacted.
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from types import SimpleNamespace
from unittest import mock

import httpx
import pytest

from app.collectors import pnetlab
from app.collectors.base import CollectorConfigError, CollectorRegistry
from app.services.infrastructure_intelligence import providers as P
from app.services.infrastructure_intelligence import vocabulary as V

pytestmark = pytest.mark.asyncio

REAL_ASYNC_CLIENT = httpx.AsyncClient

LINKS = [
    {"type": "ethernet", "source": "node3", "source_type": "node", "source_label": "port2",
     "destination": "node1", "destination_type": "node", "destination_label": "e0/0",
     "network_id": 1},
    {"type": "ethernet", "source": "node3", "source_type": "node", "source_label": "port3",
     "destination": "node2", "destination_type": "node", "destination_label": "e0/0",
     "network_id": 2},
    {"type": "ethernet", "source": "node4", "source_type": "node", "source_label": "eth0",
     "destination": "network5", "destination_type": "network", "destination_label": "",
     "network_id": 5},
]

NODES = {
    "1": {"id": 1, "name": "R3", "type": "iol", "status": "started", "template": "iol",
          "image": "i86bi-linux-l3-adventerprisek9-15.5.2T.bin", "ip_address": "10.10.13.2",
          "ethernets": [{"id": 0, "name": "e0/0", "network_id": 1},
                        {"id": 1, "name": "e0/1"}]},
    "2": {"id": 2, "name": "R2", "type": "iol", "status": "started",
          "ip_address": "10.10.23.2", "ethernets": [{"id": 0, "name": "e0/0",
                                                     "network_id": 2}]},
    "3": {"id": 3, "name": "FW1", "type": "fortigate", "status": "started",
          "template": "fortigate", "ethernets": [{"id": 0, "name": "port2"},
                                                 {"id": 1, "name": "port3"}]},
    "4": {"id": 4, "name": "VPC1", "type": "vpcs", "status": "stopped", "ethernets": ["eth0"]},
}

TOPOLOGY_WITH_NODES = {"code": 200, "status": "success", "message": "Topology loaded",
                       "data": {"nodes": NODES, "links": LINKS}}
TOPOLOGY_LINKS_ONLY = {"code": 200, "status": "success", "message": "Topology loaded",
                       "data": LINKS}
LABS = {"code": 200, "status": "success", "message": "Labs loaded",
        "data": [{"id": "l1", "name": "CCIE-Lab", "path": "/CCIE-Lab.unl",
                  "status": "stopped"},
                 {"name": "entry-without-a-path"}]}
SESSION_OK = {"code": 200, "status": "success", "message": "ok",
              "data": {"username": "labadmin", "folder": "/"}}


def source(**overrides) -> SimpleNamespace:
    """Decrypted connection info, as the orchestrator hands it to a collector."""
    fields = {
        "id": uuid.uuid4(), "tenant_id": uuid.uuid4(), "name": "pnetlab-lab",
        "source_type": "pnetlab", "hostname": "192.168.159.130", "ip_address": None,
        "port": 443, "protocol": "https", "snmp_version": None, "username": "labadmin",
        "api_key": None, "password": "lab-secret-password", "config": {},
        "verify_ssl": False,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


@contextlib.contextmanager
def http(handler):
    """Route the collector's own AsyncClient through httpx.MockTransport."""

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return REAL_ASYNC_CLIENT(**kwargs)

    with mock.patch.object(pnetlab.httpx, "AsyncClient", factory):
        yield


def envelope(payload: dict, request: httpx.Request, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=request)


def lab_routes(home=None, auth=None, labs=None, topology=None):
    """The endpoints a real collection walks, answered as PNETLab answers them."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if home is not None:
            # A test may override ANY endpoint (e.g. make login fail).
            handled = home(request)
            if handled is not None:
                return handled
        if request.method == "POST" and path == "/store/public/auth/login/login":
            return httpx.Response(
                200, json=SESSION_OK, request=request,
                headers={"set-cookie": "XSRF-TOKEN=xsrf1; Path=/"},
            )
        if path == "/api/auth":
            return envelope(auth or SESSION_OK, request)
        if path == "/api/labs/":
            return envelope(labs or LABS, request)
        if path.startswith("/api/labs/") and path.endswith("/topology"):
            if topology is None:
                return httpx.Response(404, json={"code": 404, "status": "fail",
                                                 "message": "no such lab"}, request=request)
            return envelope(topology, request)
        return httpx.Response(200, content=b"<html>PNETLab</html>", request=request)

    return handler


async def collect_with(handler) -> dict:
    with http(handler):
        instance = pnetlab.PNETLabCollector(source=source())
        try:
            return await instance.collect()
        finally:
            await instance.aclose()


class TestRegistrationAndVocabulary:
    async def test_provider_is_registered_through_the_existing_registry(self):
        assert CollectorRegistry.get("pnetlab") is pnetlab.PNETLabCollector

    async def test_provider_vocabulary_and_capabilities(self):
        assert P.provider_from_source_type("pnetlab") == V.PROVIDER_PNETLAB
        assert V.PROVIDER_PNETLAB in V.KNOWN_PROVIDERS
        assert "pnetlab" in P.wired_source_types()
        assert V.PROVIDER_PNETLAB in P.implemented_providers()
        capabilities = P.capabilities_for_provider(V.PROVIDER_PNETLAB) or frozenset()
        assert {V.CAP_INTERFACES, V.CAP_TOPOLOGY, V.CAP_INVENTORY} <= capabilities
        # A lab controller is source-observed API evidence, never "inferred".
        assert P.PROVIDER_EVIDENCE_CLASSIFICATION[V.PROVIDER_PNETLAB] == V.EVIDENCE_API


class TestConfiguration:
    async def test_base_url_from_config_wins(self):
        instance = pnetlab.PNETLabCollector(
            source=source(config={"base_url": "https://pnetlab.example:8443/"})
        )
        assert instance._base_url() == "https://pnetlab.example:8443"

    async def test_base_url_from_hostname_and_port(self):
        instance = pnetlab.PNETLabCollector(source=source(hostname="10.0.0.9", port=8443))
        assert instance._base_url() == "https://10.0.0.9:8443"

    async def test_missing_host_is_a_config_error(self):
        instance = pnetlab.PNETLabCollector(source=source(hostname=None, port=None))
        with pytest.raises(CollectorConfigError):
            instance._base_url()

    async def test_lab_path_and_timeout_come_from_configuration(self):
        instance = pnetlab.PNETLabCollector(
            source=source(config={"lab_path": "/MyLab.unl", "timeout": 3})
        )
        assert instance._lab_path() == "/MyLab.unl"
        assert instance._timeout() == 3.0

    async def test_credentials_never_reach_logs_or_messages(self, caplog):
        caplog.set_level(logging.DEBUG)
        with http(lab_routes()):
            instance = pnetlab.PNETLabCollector(source=source())
            check = await instance.authenticate()
            await instance.aclose()

        assert check.success is True
        assert "lab-secret-password" not in caplog.text
        assert "lab-secret-password" not in check.message


class TestAuthenticationFailureTaxonomy:
    """A credential or network problem is never reported as "unsupported"."""

    async def test_rejected_credentials_are_reported_as_authentication_failure(self):
        def home(request: httpx.Request) -> httpx.Response | None:
            if request.method == "POST" and "login" in request.url.path:
                return httpx.Response(401, json={"code": 401, "status": "fail",
                                                 "message": "wrong password"}, request=request)
            return None

        with http(lab_routes(home=home)):
            instance = pnetlab.PNETLabCollector(source=source())
            check = await instance.authenticate()
            await instance.aclose()

        assert check.success is False
        assert "authentication failed" in check.message
        assert "unsupported" not in check.message.lower()

    async def test_timeout_is_reported_as_a_timeout(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        with http(handler):
            instance = pnetlab.PNETLabCollector(source=source())
            check = await instance.authenticate()
            await instance.aclose()

        assert check.success is False
        assert "timed out" in check.message
        assert "unsupported" not in check.message.lower()

    async def test_unreachable_host_is_reported_as_unreachable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        with http(handler):
            instance = pnetlab.PNETLabCollector(source=source())
            check = await instance.authenticate()
            await instance.aclose()

        assert check.success is False
        assert "unreachable" in check.message
        assert "unsupported" not in check.message.lower()

    async def test_denied_permission_is_not_a_capability_statement(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/auth":
                return httpx.Response(403, json={"code": 403, "status": "forbidden",
                                                 "message": "not allowed"}, request=request)
            return httpx.Response(200, content=b"<html></html>", request=request)

        with http(handler):
            instance = pnetlab.PNETLabCollector(source=source())
            check = await instance.authenticate()
            await instance.aclose()

        assert check.success is False
        assert "unsupported" not in check.message.lower()

    async def test_malformed_json_is_reported_as_unusable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/auth":
                return httpx.Response(200, content=b"not json at all", request=request)
            return httpx.Response(200, content=b"<html></html>", request=request)

        with http(handler):
            instance = pnetlab.PNETLabCollector(source=source())
            check = await instance.authenticate()
            await instance.aclose()

        assert check.success is False
        assert "non-JSON" in check.message


class TestLabNodeInterfaceAndLinkParsing:
    async def test_labs_are_discovered_and_entries_without_a_path_are_dropped(self):
        raw = await collect_with(lab_routes(topology=TOPOLOGY_WITH_NODES))
        assert [lab["path"] for lab in raw["labs"]] == ["/CCIE-Lab.unl"]
        assert raw["labs"][0]["name"] == "CCIE-Lab"
        # The endpoint that actually answered is recorded, not summarised away.
        assert any(probe["result"] == "used" for probe in raw["probes"])

    async def test_configured_lab_path_skips_the_lab_listing(self):
        with http(lab_routes(topology=TOPOLOGY_WITH_NODES)):
            instance = pnetlab.PNETLabCollector(
                source=source(config={"lab_path": "/CCIE-Lab.unl"})
            )
            try:
                raw = await instance.collect()
            finally:
                await instance.aclose()
        assert raw["labs"] == [{"path": "/CCIE-Lab.unl", "source": "configured"}]
        assert raw["topologies"]

    async def test_nodes_become_devices_with_only_explicit_vendor_and_type(self):
        raw = await collect_with(lab_routes(topology=TOPOLOGY_WITH_NODES))
        bundle = pnetlab.PNETLabCollector(source=source()).normalize(raw)

        by_name = {device.name: device for device in bundle.devices}
        assert set(by_name) >= {"R3", "R2", "FW1", "VPC1"}
        assert by_name["FW1"].vendor == "Fortinet"      # PNETLab says fortigate
        assert by_name["FW1"].device_type == "firewall"
        assert by_name["R3"].vendor == "Cisco"          # PNETLab says iol
        assert by_name["R3"].device_type == "router"
        assert by_name["R3"].ip_address == "10.10.13.2"
        assert by_name["R3"].status == "up"
        assert by_name["VPC1"].status == "down"
        # Lab identity is preserved in the existing source-metadata field.
        assert by_name["R3"].custom_metadata["pnetlab_lab_path"] == "/CCIE-Lab.unl"
        assert by_name["R3"].custom_metadata["pnetlab_node_id"] == "1"
        assert by_name["R3"].external_id == "pnetlab:CCIE-Lab:node1"
        assert by_name["R2"].external_id == "pnetlab:CCIE-Lab:node2"

    async def test_unknown_vendor_stays_unknown_and_is_never_guessed(self):
        topology = {
            "code": 200, "status": "success", "message": "ok",
            "data": [{"id": 7, "name": "core-sw-01", "type": "mysterybox",
                      "status": "started", "ip_address": "10.0.0.7"}],
        }
        raw = await collect_with(lab_routes(topology=topology))
        bundle = pnetlab.PNETLabCollector(source=source()).normalize(raw)

        device = bundle.devices[0]
        assert device.vendor is None            # a hostname is not vendor evidence
        assert device.model is None
        assert device.device_type == "other"

    async def test_interfaces_come_from_node_detail_and_link_endpoints(self):
        raw = await collect_with(lab_routes(topology=TOPOLOGY_WITH_NODES))
        bundle = pnetlab.PNETLabCollector(source=source()).normalize(raw)

        keys = {(iface.device_external_id, iface.name) for iface in bundle.interfaces}
        assert ("pnetlab:CCIE-Lab:node1", "e0/0") in keys     # node detail
        assert ("pnetlab:CCIE-Lab:node3", "port2") in keys    # link endpoint
        assert ("pnetlab:CCIE-Lab:node4", "eth0") in keys

    async def test_a_link_becomes_observed_evidence_in_both_directions(self):
        raw = await collect_with(lab_routes(topology=TOPOLOGY_WITH_NODES))
        bundle = pnetlab.PNETLabCollector(source=source()).normalize(raw)

        assert bundle.neighbor_status[pnetlab.EVIDENCE_TYPE] == "collected"
        triples = {
            (nbr.device_external_id, nbr.local_interface, nbr.remote_external_id,
             nbr.remote_interface)
            for nbr in bundle.neighbors
        }
        assert ("pnetlab:CCIE-Lab:node3", "port2", "pnetlab:CCIE-Lab:node1", "e0/0") in triples
        assert ("pnetlab:CCIE-Lab:node1", "e0/0", "pnetlab:CCIE-Lab:node3", "port2") in triples
        # The peer is named by PNETLab's OWN node reference: an identity, never an
        # address and never a hostname guess.
        assert all(nbr.remote_address is None for nbr in bundle.neighbors)
        assert all(nbr.evidence_type == pnetlab.EVIDENCE_TYPE for nbr in bundle.neighbors)

    async def test_a_network_terminated_link_creates_no_device_adjacency(self):
        topology = {"code": 200, "status": "success", "message": "ok", "data": [
            {"type": "ethernet", "source": "node4", "source_type": "node",
             "source_label": "eth0", "destination": "network5",
             "destination_type": "network", "destination_label": "", "network_id": 5},
        ]}
        raw = await collect_with(lab_routes(topology=topology))
        bundle = pnetlab.PNETLabCollector(source=source()).normalize(raw)

        assert [device.name for device in bundle.devices] == ["node4"]
        assert bundle.neighbors == []

    async def test_links_only_payload_still_yields_the_nodes_it_references(self):
        raw = await collect_with(lab_routes(topology=TOPOLOGY_LINKS_ONLY))
        bundle = pnetlab.PNETLabCollector(source=source()).normalize(raw)

        assert sorted(device.name for device in bundle.devices) == [
            "node1", "node2", "node3", "node4"
        ]
        assert all(
            device.custom_metadata["pnetlab_node_name_source"] == "link_reference"
            for device in bundle.devices
        )
        assert len(bundle.neighbors) == 4

    async def test_partial_and_missing_topologies_are_reported_honestly(self):
        no_links = {"code": 200, "status": "success", "message": "ok",
                    "data": {"nodes": NODES, "links": []}}
        raw = await collect_with(lab_routes(topology=no_links))
        bundle = pnetlab.PNETLabCollector(source=source()).normalize(raw)
        assert bundle.devices and bundle.neighbors == []
        assert bundle.neighbor_status[pnetlab.EVIDENCE_TYPE] == "empty"

        empty = pnetlab.PNETLabCollector(source=source()).normalize(
            {"labs": [], "topologies": []}
        )
        assert empty.devices == [] and empty.interfaces == [] and empty.neighbors == []
        assert empty.neighbor_status[pnetlab.EVIDENCE_TYPE] == "unavailable"

    async def test_a_link_without_a_destination_is_ignored(self):
        """A row with no stated endpoints produces no device and no edge."""
        topology = {"code": 200, "status": "success", "message": "ok", "data": [
            {"type": "ethernet", "source": "node1", "source_type": "node",
             "source_label": "e0/0"},
        ]}
        raw = await collect_with(lab_routes(topology=topology))
        bundle = pnetlab.PNETLabCollector(source=source()).normalize(raw)
        assert bundle.neighbors == []
        assert bundle.devices == []
        assert bundle.interfaces == []

    async def test_a_lab_scoped_read_failure_falls_through_to_the_next_endpoint(self):
        """A 404 on the lab-scoped endpoint must not lose the topology."""
        def home(request: httpx.Request) -> httpx.Response | None:
            if request.url.path == "/api/labs/session/topology":
                return envelope(TOPOLOGY_WITH_NODES, request)
            return None

        raw = await collect_with(lab_routes(home=home, topology=None))
        assert raw["topologies"], "the session endpoint should have been used"
        assert raw["topologies"][0]["topology"]["endpoint"] == "GET /api/labs/session/topology"
        skipped = [probe for probe in raw["probes"] if probe["result"] == "skipped"]
        assert skipped, "the failed lab-scoped read must be recorded"


# ---------------------------------------------------------------------------
# DB-backed: PNETLab evidence through the EXISTING relationship pipeline.
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


async def interfaces_for_tenant(db, tenant_id):
    from sqlalchemy import select

    from app.models.interface import Interface

    return list(
        (
            await db.execute(select(Interface).where(Interface.tenant_id == tenant_id))
        ).scalars().all()
    )


async def lab_bundle():
    raw = await collect_with(lab_routes(topology=TOPOLOGY_WITH_NODES))
    return pnetlab.PNETLabCollector(source=source()).normalize(raw)


class TestPNETLabEvidencePipeline:
    async def test_links_materialise_as_observed_edges_for_every_local_device(
        self, tenant, db, monitoring_source_factory
    ):
        row = await monitoring_source_factory(
            source_type="pnetlab", hostname="192.168.159.130", port=443
        )
        result = await bundle_saver(db).save(row, await lab_bundle())
        await db.commit()

        assert result.devices_upserted >= 4
        assert result.interfaces_upserted >= 6
        # Two node-to-node links, each reported from both ends.
        assert result.neighbors_written == 4
        assert result.neighbors_unresolved == 0

        edges = await relationships_for(db, tenant.id)
        assert len(edges) == 4
        for edge in edges:
            assert edge.relationship_type == "connected_to"
            assert edge.relationship_subtype == pnetlab.EVIDENCE_TYPE
            assert edge.discovered_by == pnetlab.EVIDENCE_TYPE
            # A PNETLab-reported connection is OBSERVED evidence, never inferred.
            assert edge.is_inferred is False
            assert edge.confidence == 100.0
            assert edge.status == "active"
            assert (edge.attributes or {}).get("evidence_type") == pnetlab.EVIDENCE_TYPE

        labels = {edge.attributes.get("local_interface") for edge in edges}
        assert labels == {"port2", "e0/0", "port3"}

    async def test_repeated_discovery_is_idempotent(
        self, tenant, db, monitoring_source_factory
    ):
        row = await monitoring_source_factory(
            source_type="pnetlab", hostname="192.168.159.130", port=443
        )
        saver = bundle_saver(db)
        bundle = await lab_bundle()

        first = await saver.save(row, bundle)
        second = await saver.save(row, bundle)
        await db.commit()

        assert first.neighbors_written == second.neighbors_written == 4
        assert first.interfaces_upserted == second.interfaces_upserted
        assert len(await relationships_for(db, tenant.id)) == 4
        rows = await interfaces_for_tenant(db, tenant.id)
        assert len({row.id for row in rows}) == len(rows)

    async def test_edges_never_cross_tenants(self, tenant, db, monitoring_source_factory):
        from sqlalchemy import delete, select

        from app.models.device import Device
        from app.models.monitoring_source import MonitoringSource
        from app.models.tenant import Tenant

        row = await monitoring_source_factory(
            source_type="pnetlab", hostname="192.168.159.130", port=443
        )
        await bundle_saver(db).save(row, await lab_bundle())
        await db.commit()

        # Another tenant whose devices carry the SAME PNETLab identities.
        other = Tenant(name="Other Lab", slug=f"other-{uuid.uuid4().hex[:8]}")
        db.add(other)
        await db.flush()
        other_source = MonitoringSource(
            tenant_id=other.id, name=f"pnetlab-{uuid.uuid4().hex[:6]}",
            source_type="pnetlab", hostname="192.168.159.131", port=443,
            is_active=True, status="pending",
        )
        db.add(other_source)
        await db.flush()
        db.add(
            Device(
                tenant_id=other.id, monitoring_source_id=other_source.id,
                external_id="pnetlab:CCIE-Lab:node1", name="OtherR3",
                device_type="router", status="up",
            )
        )
        await db.commit()
        try:
            assert await relationships_for(db, other.id) == []
            ours = await relationships_for(db, tenant.id)
            assert len(ours) == 4
            our_ids = set(
                (
                    await db.execute(select(Device.id).where(Device.tenant_id == tenant.id))
                ).scalars().all()
            )
            for edge in ours:
                assert edge.source_resource_id in our_ids
                assert edge.target_resource_id in our_ids
        finally:
            await db.execute(delete(Tenant).where(Tenant.id == other.id))
            await db.commit()

    async def test_no_edge_is_written_when_the_peer_identity_is_unknown(
        self, tenant, db, monitoring_source_factory
    ):
        from app.collectors.base import (
            NormalizedBundle,
            NormalizedDevice,
            NormalizedNeighbor,
        )

        row = await monitoring_source_factory(
            source_type="pnetlab", hostname="192.168.159.130", port=443
        )
        bundle = NormalizedBundle(
            devices=[
                NormalizedDevice(
                    external_id="pnetlab:Lab:node1", name="R3", device_type="router"
                )
            ],
            interfaces=[],
            neighbors=[
                NormalizedNeighbor(
                    device_external_id="pnetlab:Lab:node1",
                    local_interface="e0/0",
                    remote_external_id="pnetlab:Lab:node999",   # never discovered
                    remote_interface="e0/1",
                    evidence_type=pnetlab.EVIDENCE_TYPE,
                )
            ],
            neighbor_status={pnetlab.EVIDENCE_TYPE: "collected"},
        )
        result = await bundle_saver(db).save(row, bundle)
        await db.commit()

        assert result.neighbors_written == 0
        assert result.neighbors_unresolved == 1
        assert await relationships_for(db, tenant.id) == []
