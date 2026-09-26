"""
Live topology read-model tests.

Two load-bearing claims:

  1. NEGATIVE - nothing is invented. A value that was never measured stays None
     (not 0), an interface the relationship never recorded yields no telemetry,
     and an unclassified device stays UNIDENTIFIED however plausible its name or
     address looks.
  2. NO N+1 - the enrichment query count does not grow with the number of devices.

The pure functions are tested with in-memory rows (the same style
``test_topology_graph.py`` uses); the two behavioural guarantees that need the
database (tenant isolation and the query count) are tested against the real one,
because isolation must never be mocked.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, event

from app.core.db import AsyncSessionLocal
from app.models.device import Device
from app.models.site import Site
from app.models.tenant import Tenant
from app.services.infrastructure_intelligence import vocabulary as V
from app.services.topology_graph_service import PREFIX_DEVICE, PREFIX_SITE
from app.services.topology_live_service import (
    TopologyLiveService,
    build_summary,
    device_freshness,
    edge_interface_names,
    interface_facts,
    interface_summary,
    link_facts,
    live_edge,
    live_node,
    view_freshness,
)

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def _device(**overrides):
    base = dict(
        id=uuid.uuid4(),
        name="sw-01",
        device_type="switch",
        status="up",
        site_id=None,
        monitoring_source_id=None,
        ip_address="10.0.0.1",
        vendor="Cisco",
        model="C9300",
        hostname="sw-01",
        serial_number="FOC1234ABCD",
        lifecycle_state=V.LIFECYCLE_ACTIVE,
        health=V.HEALTH_HEALTHY,
        last_telemetry_at=None,
        last_seen_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _iface(**overrides):
    base = dict(
        id=uuid.uuid4(),
        device_id=uuid.uuid4(),
        name="Gi1/0/1",
        interface_type="ethernet",
        oper_status="up",
        admin_status="up",
        vlan_id=None,
        mac_address=None,
        speed_bps=None,
        rx_bps=None,
        tx_bps=None,
        utilization_pct=None,
        in_errors=None,
        out_errors=None,
        discards_in=None,
        discards_out=None,
        last_telemetry_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _node(**overrides):
    """A device graph node exactly as TopologyGraphService emits it."""
    device_id = overrides.pop("device_id", uuid.uuid4())
    base = dict(
        id=f"{PREFIX_DEVICE}{device_id}",
        type="device",
        name="sw-01",
        device_type="switch",
        device_type_raw="switch",
        health=V.HEALTH_HEALTHY,
        status="up",
        site_id=None,
        is_network_device=True,
        metadata={},
        counts={},
    )
    base.update(overrides)
    return base


def _edge(source_id: str, target_id: str, **overrides):
    base = dict(
        id=str(uuid.uuid4()),
        source_id=source_id,
        target_id=target_id,
        relationship_type="connected_to",
        unknown_relationship_type=False,
        relationship_subtype=None,
        label=None,
        evidence="resource_relationship",
        discovered_by="lldp",
        is_inferred=False,
        confidence=100.0,
        status="active",
        first_seen=NOW.isoformat(),
        last_seen=NOW.isoformat(),
        metadata={},
    )
    base.update(overrides)
    return base


class TestInterfaceSummary:
    def test_no_interfaces_measures_nothing(self):
        summary = interface_summary([])
        assert summary["total"] == 0
        assert summary["has_telemetry"] is False
        # None, not 0: nothing was measured.
        assert summary["rx_bps"] is None
        assert summary["tx_bps"] is None

    def test_interfaces_without_traffic_do_not_report_zero(self):
        summary = interface_summary([_iface(oper_status="up")])
        assert summary["total"] == 1
        assert summary["up"] == 1
        assert summary["has_telemetry"] is False
        assert summary["rx_bps"] is None and summary["tx_bps"] is None

    def test_measured_zero_is_distinguishable_from_not_measured(self):
        summary = interface_summary([_iface(rx_bps=0.0, tx_bps=0.0)])
        assert summary["has_telemetry"] is True
        assert summary["rx_bps"] == 0.0 and summary["tx_bps"] == 0.0

    def test_totals_sum_only_real_readings(self):
        summary = interface_summary([
            _iface(rx_bps=1_000.0, tx_bps=500.0),
            _iface(rx_bps=250.0, tx_bps=None),
        ])
        assert summary["rx_bps"] == 1250.0
        assert summary["tx_bps"] == 500.0

    def test_up_and_down_counts_use_stored_oper_status(self):
        summary = interface_summary([
            _iface(oper_status="up"),
            _iface(oper_status="DOWN"),
            _iface(oper_status=None),
        ])
        assert summary["up"] == 1
        assert summary["down"] == 1
        assert summary["total"] == 3

    def test_newest_interface_timestamp_wins(self):
        summary = interface_summary([
            _iface(last_telemetry_at=NOW - timedelta(hours=2)),
            _iface(last_telemetry_at=NOW),
        ])
        assert summary["last_telemetry_at"] == NOW.isoformat()

    def test_interface_facts_are_verbatim(self):
        facts = interface_facts(
            _iface(
                speed_bps=1_000_000_000.0, rx_bps=None, utilization_pct=None, vlan_id=42
            )
        )
        assert facts["speed_bps"] == 1_000_000_000.0
        assert facts["rx_bps"] is None
        assert facts["utilization_pct"] is None
        assert facts["vlan_id"] == 42
        assert facts["name"] == "Gi1/0/1"


class TestDeviceFreshness:
    def test_never_collected_when_no_timestamp_exists(self):
        out = device_freshness(_device(), [], now=NOW)
        assert out["state"] == V.FRESHNESS_NEVER_COLLECTED
        assert out["age_seconds"] is None
        assert out["last_telemetry_at"] is None

    def test_a_recent_reading_is_fresh(self):
        out = device_freshness(
            _device(last_telemetry_at=NOW - timedelta(seconds=30)), [], now=NOW
        )
        assert out["state"] == V.FRESHNESS_FRESH
        assert out["age_seconds"] == 30.0

    def test_stale_and_expired_use_the_shared_thresholds(self):
        stale = device_freshness(
            _device(
                last_telemetry_at=NOW
                - timedelta(seconds=V.TELEMETRY_STALE_AFTER_SECONDS + 5)
            ),
            [],
            now=NOW,
        )
        assert stale["state"] == V.FRESHNESS_STALE
        expired = device_freshness(
            _device(
                last_telemetry_at=NOW
                - timedelta(seconds=V.TELEMETRY_EXPIRED_AFTER_SECONDS + 5)
            ),
            [],
            now=NOW,
        )
        assert expired["state"] == V.FRESHNESS_EXPIRED

    def test_newest_of_device_and_interfaces_wins(self):
        out = device_freshness(
            _device(last_telemetry_at=NOW - timedelta(hours=6)),
            [_iface(last_telemetry_at=NOW - timedelta(seconds=10))],
            now=NOW,
        )
        assert out["state"] == V.FRESHNESS_FRESH
        assert out["last_telemetry_at"] == (NOW - timedelta(seconds=10)).isoformat()


class TestLiveNode:
    def _live(self, device=None, node=None, ifaces=(), relationships=0):
        return live_node(
            device=device or _device(),
            graph_node=node or _node(),
            ifaces=list(ifaces),
            relationship_count=relationships,
            now=NOW,
        )

    def test_operational_state_uses_the_canonical_mapping(self):
        def state(status: str) -> str:
            return self._live(device=_device(status=status))["operational_state"]

        assert state("up") == V.OPER_STATE_UP
        assert state("degraded") == V.OPER_STATE_DEGRADED
        assert state("offline") == V.OPER_STATE_DOWN
        assert state("banana") == V.OPER_STATE_UNKNOWN

    def test_unclassified_device_stays_evidence_only(self):
        """The Universal Discovery 'unknown resource' case must not read as identified."""
        live = self._live(
            device=_device(device_type="unknown", vendor=None, model=None),
            node=_node(device_type="unclassified", device_type_raw="unknown"),
        )
        assert live["recognition"]["status"] == V.RECOGNITION_UNKNOWN

    def test_classified_device_is_identified(self):
        assert (
            self._live(node=_node(device_type="switch"))["recognition"]["status"]
            == V.RECOGNITION_IDENTIFIED
        )

    def test_identity_carries_only_stored_columns(self):
        live = self._live(
            device=_device(
                vendor=None, model=None, serial_number=None, ip_address="10.9.9.9"
            ),
            node=_node(device_type="unclassified", device_type_raw="unknown"),
        )
        identity = live["identity"]
        assert identity["vendor"] is None
        assert identity["model"] is None
        assert identity["serial"] is None
        assert identity["ip"] == "10.9.9.9"
        for guessed in ("os", "firmware", "country", "site_name"):
            assert guessed not in identity

    def test_relationship_count_is_reported(self):
        assert self._live(relationships=3)["relationship_count"] == 3

    def test_interfaces_and_freshness_are_included(self):
        live = self._live(
            ifaces=[_iface(rx_bps=100.0, tx_bps=50.0, oper_status="up")]
        )
        assert live["interfaces"]["total"] == 1
        assert live["interfaces"]["has_telemetry"] is True
        assert live["freshness"]["state"] == V.FRESHNESS_NEVER_COLLECTED


class TestEdgeInterfaceNames:
    def test_local_remote_spelling(self):
        edge = _edge(
            "device:a",
            "device:b",
            metadata={"local_interface": "port3", "remote_interface": "e0/0"},
        )
        assert edge_interface_names(edge) == ("port3", "e0/0")

    def test_source_target_spelling(self):
        edge = _edge(
            "device:a",
            "device:b",
            metadata={"source_interface": "Gi1/0/1", "target_interface": "Gi1/0/2"},
        )
        assert edge_interface_names(edge) == ("Gi1/0/1", "Gi1/0/2")

    def test_no_recorded_interface_is_never_guessed(self):
        assert edge_interface_names(_edge("device:a", "device:b")) == (None, None)
        assert edge_interface_names(
            _edge("device:a", "device:b", metadata={"index": 12})
        ) == (None, None)

    def test_malformed_metadata_is_survived(self):
        assert edge_interface_names(
            _edge("device:a", "device:b", metadata=None)
        ) == (None, None)


class TestLinkFacts:
    def test_capacity_is_the_slower_recorded_end(self):
        facts = link_facts(
            {"speed_bps": 1_000_000_000.0}, {"speed_bps": 100_000_000.0}
        )
        assert facts["capacity_bps"] == 100_000_000.0

    def test_capacity_is_only_ever_recorded(self):
        assert link_facts({"speed_bps": None}, None)["capacity_bps"] is None
        assert link_facts(None, None)["capacity_bps"] is None

    def test_unmeasured_traffic_is_none_not_zero(self):
        facts = link_facts({"rx_bps": None, "tx_bps": None}, None)
        assert facts["has_telemetry"] is False
        assert facts["rx_bps"] is None
        assert facts["tx_bps"] is None

    def test_measured_traffic_totals_both_ends(self):
        facts = link_facts(
            {"rx_bps": 100.0, "tx_bps": 200.0}, {"rx_bps": 50.0, "tx_bps": None}
        )
        assert facts["has_telemetry"] is True
        assert facts["rx_bps"] == 150.0
        assert facts["tx_bps"] == 200.0

    def test_recorded_utilization_wins(self):
        facts = link_facts(
            {
                "utilization_pct": 69.0,
                "rx_bps": 1.0,
                "tx_bps": 1.0,
                "speed_bps": 1_000_000_000.0,
            },
            None,
        )
        assert facts["utilization_pct"] == 69.0
        assert facts["utilization_source"] == "recorded"

    def test_utilization_is_computed_only_from_that_end_own_numbers(self):
        facts = link_facts(
            {
                "speed_bps": 1_000_000_000.0,
                "rx_bps": 650_000_000.0,
                "tx_bps": 100_000_000.0,
            },
            None,
        )
        assert facts["utilization_source"] == "computed"
        assert facts["utilization_pct"] == 65.0

    def test_utilization_is_not_computed_without_both_directions(self):
        facts = link_facts(
            {"speed_bps": 1_000_000_000.0, "rx_bps": 650_000_000.0, "tx_bps": None},
            None,
        )
        assert facts["utilization_pct"] is None
        assert facts["utilization_source"] is None

    def test_utilization_is_not_computed_without_capacity(self):
        facts = link_facts({"speed_bps": None, "rx_bps": 10.0, "tx_bps": 10.0}, None)
        assert facts["utilization_pct"] is None

    def test_utilization_cannot_exceed_one_hundred(self):
        facts = link_facts(
            {"speed_bps": 1_000.0, "rx_bps": 10_000.0, "tx_bps": 10_000.0}, None
        )
        assert facts["utilization_pct"] == 100.0

    def test_observed_at_is_the_newest_endpoint_timestamp(self):
        facts = link_facts(
            {"last_telemetry_at": "2026-09-26T11:00:00+00:00"},
            {"last_telemetry_at": "2026-09-26T12:00:00+00:00"},
        )
        assert facts["observed_at"] == "2026-09-26T12:00:00+00:00"

    def test_link_freshness_is_never_collected_without_a_timestamp(self):
        """No interface telemetry is NOT the same as stale."""
        facts = link_facts({"rx_bps": 1.0, "tx_bps": 1.0}, None, now=NOW)
        assert facts["freshness"]["state"] == V.FRESHNESS_NEVER_COLLECTED
        assert facts["freshness"]["age_seconds"] is None

    def test_link_freshness_uses_the_shared_thresholds(self):
        fresh = link_facts(
            {"last_telemetry_at": (NOW - timedelta(seconds=5)).isoformat()},
            None,
            now=NOW,
        )
        assert fresh["freshness"]["state"] == V.FRESHNESS_FRESH
        expired = link_facts(
            {
                "last_telemetry_at": (
                    NOW - timedelta(seconds=V.TELEMETRY_EXPIRED_AFTER_SECONDS + 5)
                ).isoformat()
            },
            None,
            now=NOW,
        )
        assert expired["freshness"]["state"] == V.FRESHNESS_EXPIRED


class TestLiveEdge:
    def test_recorded_interfaces_are_resolved_to_their_rows(self):
        src, tgt = uuid.uuid4(), uuid.uuid4()
        edge = _edge(
            f"{PREFIX_DEVICE}{src}",
            f"{PREFIX_DEVICE}{tgt}",
            metadata={"local_interface": "port3", "remote_interface": "e0/0"},
        )
        ifaces = {
            f"{PREFIX_DEVICE}{src}": [
                _iface(
                    device_id=src,
                    name="port3",
                    oper_status="up",
                    speed_bps=1_000_000_000.0,
                    rx_bps=410_000_000.0,
                    tx_bps=280_000_000.0,
                )
            ],
            f"{PREFIX_DEVICE}{tgt}": [
                _iface(device_id=tgt, name="e0/0", oper_status="up", speed_bps=1e9)
            ],
        }
        live = live_edge(edge, ifaces)
        assert live["local_interface"]["name"] == "port3"
        assert live["remote_interface"]["name"] == "e0/0"
        assert live["link"]["capacity_bps"] == 1_000_000_000.0
        assert live["link"]["has_telemetry"] is True

    def test_interface_named_but_never_collected_yields_no_telemetry(self):
        src, tgt = uuid.uuid4(), uuid.uuid4()
        edge = _edge(
            f"{PREFIX_DEVICE}{src}",
            f"{PREFIX_DEVICE}{tgt}",
            metadata={"local_interface": "port3"},
        )
        live = live_edge(edge, {})
        # The relationship is still real even though its detail is missing.
        assert live["local_interface_name"] == "port3"
        assert live["local_interface"] is None
        assert live["link"]["has_telemetry"] is False

    def test_no_recorded_interfaces_means_no_telemetry(self):
        live = live_edge(_edge("device:a", "device:b"), {})
        assert live["local_interface"] is None
        assert live["remote_interface"] is None
        assert live["link"]["utilization_pct"] is None

    def test_classification_comes_from_the_recorded_source(self):
        def classification(source: str):
            return live_edge(
                _edge("device:a", "device:b", discovered_by=source), {}
            )["classification"]

        assert classification("lldp") == V.EVIDENCE_ACTIVE
        assert classification("snmp") == V.EVIDENCE_ACTIVE
        assert classification("sdwan") == V.EVIDENCE_API
        assert classification("cloud") == V.EVIDENCE_API
        assert classification("manual") is None
        assert classification("mystery") is None

    def test_inferred_is_reported_verbatim(self):
        live = live_edge(_edge("device:a", "device:b", is_inferred=True), {})
        assert live["is_inferred"] is True
        assert live["status_raw"] == "active"


def _live_node_with(device=None, node=None, ifaces=()):
    """A device node carrying its live block, exactly as the service emits it."""
    node = node or _node()
    return {
        **node,
        "live": live_node(
            device=device or _device(),
            graph_node=node,
            ifaces=list(ifaces),
            relationship_count=0,
            now=NOW,
        ),
    }


class TestBuildSummary:
    def test_counts_come_from_the_returned_nodes(self):
        nodes = [
            _live_node_with(),
            _live_node_with(
                device=_device(status="offline"),
                node=_node(health=V.HEALTH_CRITICAL),
            ),
            {"id": f"{PREFIX_SITE}x", "type": "site", "name": "HQ", "live": None},
            {"id": "source:y", "type": "monitoring_source", "name": "prtg", "live": None},
        ]
        summary = build_summary(nodes, [])
        assert summary["devices_total"] == 2
        assert summary["sites_total"] == 1
        assert summary["monitoring_sources_total"] == 1
        assert summary["by_health"] == {V.HEALTH_HEALTHY: 1, V.HEALTH_CRITICAL: 1}
        assert summary["by_operational_state"] == {V.OPER_STATE_UP: 1, V.OPER_STATE_DOWN: 1}
        assert summary["by_freshness"] == {V.FRESHNESS_NEVER_COLLECTED: 2}

    def test_unidentified_devices_are_counted(self):
        nodes = [
            _live_node_with(
                node=_node(device_type="unclassified", device_type_raw="unknown")
            ),
            _live_node_with(),
        ]
        assert build_summary(nodes, [])["unidentified_devices_total"] == 1

    def test_traffic_totals_are_none_when_nothing_was_measured(self):
        summary = build_summary([_live_node_with()], [])
        assert summary["traffic_measured"] is False
        assert summary["rx_bps_total"] is None
        assert summary["tx_bps_total"] is None

    def test_traffic_totals_sum_measured_devices(self):
        nodes = [
            _live_node_with(ifaces=[_iface(rx_bps=100.0, tx_bps=50.0)]),
            _live_node_with(ifaces=[_iface(rx_bps=300.0, tx_bps=200.0)]),
        ]
        summary = build_summary(nodes, [])
        assert summary["traffic_measured"] is True
        assert summary["rx_bps_total"] == 400.0
        assert summary["tx_bps_total"] == 250.0

    def test_link_and_interface_counts(self):
        nodes = [
            _live_node_with(ifaces=[_iface(oper_status="up"), _iface(oper_status="down")])
        ]
        summary = build_summary(nodes, [_edge("device:a", "device:b")])
        assert summary["links_total"] == 1
        assert summary["interfaces_total"] == 2
        assert summary["interfaces_up"] == 1
        assert summary["interfaces_down"] == 1


class TestViewFreshness:
    def test_never_collected_when_nothing_reported(self):
        out = view_freshness([None, None], now=NOW)
        assert out["state"] == V.FRESHNESS_NEVER_COLLECTED
        assert out["devices_reporting"] == 0

    def test_newest_stamp_wins_and_reporting_is_counted(self):
        out = view_freshness(
            [
                (NOW - timedelta(minutes=30)).isoformat(),
                (NOW - timedelta(seconds=20)).isoformat(),
                None,
            ],
            now=NOW,
        )
        assert out["state"] == V.FRESHNESS_FRESH
        assert out["devices_reporting"] == 2
        assert out["age_seconds"] == 20.0

    def test_a_stale_view_is_reported_as_stale(self):
        out = view_freshness(
            [(NOW - timedelta(seconds=V.TELEMETRY_STALE_AFTER_SECONDS + 1)).isoformat()],
            now=NOW,
        )
        assert out["state"] == V.FRESHNESS_STALE


class TestSiteFilter:
    def _graph(self, site_id, member_id, outsider_id):
        return {
            "nodes": [
                {
                    "id": f"{PREFIX_SITE}{site_id}",
                    "type": "site",
                    "name": "CHD",
                    "site_id": None,
                },
                {
                    "id": f"{PREFIX_DEVICE}{member_id}",
                    "type": "device",
                    "name": "member",
                    "site_id": str(site_id),
                },
                {
                    "id": f"{PREFIX_DEVICE}{outsider_id}",
                    "type": "device",
                    "name": "outsider",
                    "site_id": None,
                },
            ],
            "edges": [
                _edge(f"{PREFIX_DEVICE}{member_id}", f"{PREFIX_DEVICE}{outsider_id}"),
                _edge(f"{PREFIX_DEVICE}{member_id}", f"{PREFIX_DEVICE}{member_id}"),
            ],
        }

    def test_no_filter_returns_everything(self):
        graph = self._graph(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        nodes, edges, scope = TopologyLiveService._select(graph, None)
        assert len(nodes) == 3
        assert len(edges) == 2
        assert scope["filtered"] is False
        assert scope["nodes_hidden"] == 0

    def test_filter_keeps_the_site_and_its_members_only(self):
        site_id, member_id, outsider_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        nodes, edges, scope = TopologyLiveService._select(
            self._graph(site_id, member_id, outsider_id), site_id
        )
        assert {node["id"] for node in nodes} == {
            f"{PREFIX_SITE}{site_id}",
            f"{PREFIX_DEVICE}{member_id}",
        }
        # An edge with an endpoint outside the filter is not drawn, and what was
        # hidden is reported rather than silently dropped.
        assert len(edges) == 1
        assert scope == {
            "site_id": str(site_id),
            "filtered": True,
            "nodes_hidden": 1,
            "edges_hidden": 1,
        }


# ---------------------------------------------------------------------------
# Database-backed guarantees. Tenant isolation must never be mocked, and the
# "no N+1" claim is only meaningful against the real query path.
# ---------------------------------------------------------------------------
async def _add_devices(tenant_id: uuid.UUID, count: int, **overrides) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    async with AsyncSessionLocal() as db:
        for _ in range(count):
            device_id = uuid.uuid4()
            db.add(
                Device(
                    id=device_id,
                    tenant_id=tenant_id,
                    name=f"p95-{uuid.uuid4().hex[:6]}",
                    device_type=overrides.get("device_type", "switch"),
                    status=overrides.get("status", "up"),
                    site_id=overrides.get("site_id"),
                )
            )
            ids.append(device_id)
        await db.commit()
    return ids


async def _measured_build(tenant_id: uuid.UUID) -> int:
    """Number of statements one build issues, measured on a fresh session."""
    async with AsyncSessionLocal() as session:
        engine = session.bind.sync_engine
        counted: list[int] = []

        def _count(conn, cursor, statement, parameters, context, executemany):
            counted.append(1)

        event.listen(engine, "before_cursor_execute", _count)
        try:
            await TopologyLiveService(session).build(tenant_id)
        finally:
            event.remove(engine, "before_cursor_execute", _count)
        return len(counted)


@pytest_asyncio.fixture
async def other_tenant():
    """A second real tenant, deleted at teardown (cascades to its devices)."""
    async with AsyncSessionLocal() as db:
        tenant = Tenant(name="P95 Other", slug=f"p95-{uuid.uuid4().hex[:8]}")
        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)
    yield tenant
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == tenant.id))
        await db.commit()


class TestTenantIsolation:
    async def test_only_the_callers_tenant_is_returned(self, db, tenant, other_tenant):
        mine = await _add_devices(tenant.id, 1, device_type="firewall")
        theirs = await _add_devices(other_tenant.id, 1, device_type="firewall")

        out = await TopologyLiveService(db).build(tenant.id)
        node_ids = {node["id"] for node in out["nodes"]}

        assert f"{PREFIX_DEVICE}{mine[0]}" in node_ids
        assert f"{PREFIX_DEVICE}{theirs[0]}" not in node_ids

    async def test_a_site_filter_cannot_reach_another_tenant(
        self, db, tenant, other_tenant
    ):
        theirs = await _add_devices(other_tenant.id, 1)
        out = await TopologyLiveService(db).build(tenant.id, site_id=uuid.uuid4())
        assert out["nodes"] == []
        assert f"{PREFIX_DEVICE}{theirs[0]}" not in {n["id"] for n in out["nodes"]}

    async def test_the_site_filter_bounds_the_view_to_that_site(self, db, tenant):
        async with AsyncSessionLocal() as session:
            site = Site(
                tenant_id=tenant.id,
                name=f"p95-{uuid.uuid4().hex[:6]}",
                code=f"P95{uuid.uuid4().hex[:6]}",
            )
            session.add(site)
            await session.commit()
            await session.refresh(site)

        inside = await _add_devices(tenant.id, 1, site_id=site.id)
        outside = await _add_devices(tenant.id, 1)

        out = await TopologyLiveService(db).build(tenant.id, site_id=site.id)
        node_ids = {node["id"] for node in out["nodes"]}

        assert f"{PREFIX_DEVICE}{inside[0]}" in node_ids
        assert f"{PREFIX_DEVICE}{outside[0]}" not in node_ids
        assert out["scope"]["filtered"] is True

    async def test_no_relationship_data_reports_zero_edges_with_a_reason(
        self, db, tenant
    ):
        await _add_devices(tenant.id, 1)
        out = await TopologyLiveService(db).build(tenant.id)
        # An empty edge list is a real answer, and the response explains it.
        assert out["edges"] == []
        assert out["limitations"]
        for node in out["nodes"]:
            if node["type"] == "device":
                assert node["live"]["interfaces"]["has_telemetry"] is False
                assert node["live"]["interfaces"]["rx_bps"] is None

    async def test_a_device_with_no_interfaces_reports_no_traffic(self, db, tenant):
        devices = await _add_devices(tenant.id, 1)
        out = await TopologyLiveService(db).build(tenant.id)
        node = next(
            n for n in out["nodes"] if n["id"] == f"{PREFIX_DEVICE}{devices[0]}"
        )
        assert node["live"]["interfaces"] == {
            "total": 0,
            "up": 0,
            "down": 0,
            "has_telemetry": False,
            "rx_bps": None,
            "tx_bps": None,
            "last_telemetry_at": None,
        }
        assert node["live"]["freshness"]["state"] == V.FRESHNESS_NEVER_COLLECTED


class TestNoNPlusOne:
    async def test_query_count_does_not_grow_with_device_count(self, tenant):
        """Enrichment is batched: more devices must not mean more round trips.

        Measured on a FRESH session each time, because an external commit between
        two builds on one shared session can emit an extra statement for reasons
        unrelated to batching - which would make the comparison meaningless.
        """
        await _add_devices(tenant.id, 1)
        with_one = await _measured_build(tenant.id)

        await _add_devices(tenant.id, 3)
        with_four = await _measured_build(tenant.id)

        assert with_one > 0
        # The graph itself is 7 statements; devices and the batched interface list
        # add one each. Four devices must cost exactly what one device cost.
        assert with_four == with_one
