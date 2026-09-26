"""
Topology graph service — the node/edge projection of REAL infrastructure data.

RELATIONSHIP EVIDENCE (Phase 3 Step 1, verified by inspection)
--------------------------------------------------------------
`resource_relationships` is the ONLY edge store this service reads, and it has
exactly ONE production writer: ``DiscoveryService._write_neighbors()``
(`app/services/discovery/service.py`), which persists an edge only for each
``NeighborEvidence`` a collector actually observed.

That writer is currently never reached in production: grepping the codebase,
``NeighborEvidence(...)`` is constructed **only in tests**
(`tests/test_discovery_integration.py`, `tests/test_resource_context.py`). The
discovery probes in `app/services/discovery/probes.py` observe reachability and
banner/SNMP facts — never an adjacency. So this graph is structurally empty
until a collector emits neighbour evidence.

The consequence, enforced here: edges are NEVER synthesised. Not from shared IP
subnet, not from hostname similarity, not from device type, not from insertion
order. `edges: []` with an explained status is the correct answer.

RELATIONSHIP VOCABULARY (the model's own, `models/resource_relationship.py`)
---------------------------------------------------------------------------
connected_to, depends_on, protected_by, managed_by, monitored_by, logged_to,
routes_through, peers_with, hosted_on, contains, member_of, backup_for,
failover_to, serves

An edge whose `relationship_type` is outside this vocabulary is still returned
(raw, under `unknown_relationship_type: true`) rather than dropped or coerced -
the UI must be able to show that something unexpected was recorded.

PROVENANCE HONESTY
------------------
* `confidence` and `is_inferred` are real NOT NULL columns, so they are
  returned as stored. Nothing else is scored - no invented 0.95/99% weights.
* An edge is emitted only when BOTH endpoints resolve to a node in this
  tenant's node set. Dangling edges are counted in `limitations`, not drawn.

Repository discipline: no SQL in this module; every read goes through an
existing repository (plus the two additive `counts_by_device` aggregates).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_types import (
    is_network_infrastructure,
    normalize_device_type,
)
from app.repositories.alert_repository import AlertRepository
from app.repositories.collector_run_repository import CollectorRunRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.incident_repository import IncidentRepository
from app.repositories.interface_repository import InterfaceRepository
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.resource_relationship_repository import (
    ResourceRelationshipRepository,
)
from app.repositories.sensor_repository import SensorRepository
from app.repositories.site_repository import SiteRepository
from app.services.infrastructure_overview_service import (
    ACTIVE_ALERT_STATUSES,
    device_health_bucket,
)

TOPOLOGY_READY = "ready"
TOPOLOGY_PARTIAL = "partial"
TOPOLOGY_NOT_DISCOVERED = "not_discovered"

PROVENANCE_RESOURCE_RELATIONSHIP = "resource_relationship"

#: Node id namespaces, so a device/site/source id can never be confused for
#: another kind in the graph or in the UI's selection state.
PREFIX_DEVICE = "device:"
PREFIX_SITE = "site:"
PREFIX_SOURCE = "source:"

#: The relationship vocabulary declared by ResourceRelationship's own
#: docstring. Anything else is surfaced as unknown rather than hidden.
KNOWN_RELATIONSHIP_TYPES: frozenset[str] = frozenset({
    "connected_to", "depends_on", "protected_by", "managed_by", "monitored_by",
    "logged_to", "routes_through", "peers_with", "hosted_on", "contains",
    "member_of", "backup_for", "failover_to", "serves",
})

#: Collector source types whose collectors emit `NormalizedInterface` (and so
#: are the ones that could ever contribute topology evidence). Verified by
#: grepping for `NormalizedInterface(` in `app/collectors/`: snmp, fortigate,
#: linux_ssh, windows_winrm, zabbix. `wmi` is listed too because its collector
#: SUBCLASSES WindowsWinRMCollector and inherits `normalize()`, so it emits the
#: same interfaces - grepping `wmi.py` alone would wrongly miss it. PRTG is
#: deliberately absent - it emits devices and sensors, never interfaces or
#: adjacencies.
TOPOLOGY_CAPABLE_SOURCE_TYPES: frozenset[str] = frozenset({
    "snmp", "fortigate", "linux_ssh", "windows_winrm", "wmi", "zabbix",
})

MESSAGE_NOT_DISCOVERED = (
    "No network relationship data has been discovered for this tenant."
)


class TopologyGraphService:
    """Builds the node/edge graph from real rows, with explicit limitations."""

    #: Bounded fetch, matching InfrastructureService/_DEVICE_FETCH_LIMIT.
    _FETCH_LIMIT = 100_000

    def __init__(self, db: AsyncSession):
        self.db = db
        self.devices = DeviceRepository(db)
        self.sites = SiteRepository(db)
        self.sources = MonitoringSourceRepository(db)
        self.sensors = SensorRepository(db)
        self.alerts = AlertRepository(db)
        self.incidents = IncidentRepository(db)
        self.interfaces = InterfaceRepository(db)
        self.relationships = ResourceRelationshipRepository(db)
        self.collector_runs = CollectorRunRepository(db)

    async def build(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        devices = await self.devices.list(tenant_id, limit=self._FETCH_LIMIT)
        sites = await self.sites.list(tenant_id)
        sources = await self.sources.list(tenant_id)
        sensors = await self.sensors.list_for_tenant(tenant_id, limit=self._FETCH_LIMIT)
        relationships = await self.relationships.list(
            tenant_id, limit=self._FETCH_LIMIT
        )

        # One query each, instead of one per node.
        alert_counts = await self.alerts.counts_by_device(
            tenant_id, statuses=ACTIVE_ALERT_STATUSES
        )
        interface_counts = await self.interfaces.counts_by_device(tenant_id)

        sensor_counts: dict[uuid.UUID, int] = {}
        for sensor in sensors:
            if sensor.device_id is not None:
                sensor_counts[sensor.device_id] = (
                    sensor_counts.get(sensor.device_id, 0) + 1
                )

        nodes = self._node_block(
            devices, sites, sources, sensor_counts, alert_counts, interface_counts
        )
        node_ids = {node["id"] for node in nodes}

        edges, dangling = self._edge_block(relationships, node_ids)
        status, limitations = await self._status_block(
            tenant_id=tenant_id,
            nodes_total=len(nodes),
            edges_total=len(edges),
            dangling=dangling,
            sites_total=len(sites),
            interface_total=sum(interface_counts.values()),
            sources=sources,
        )

        return {
            "tenant_id": str(tenant_id),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "nodes": nodes,
            "edges": edges,
            "summary": {
                "nodes_total": len(nodes),
                "edges_total": len(edges),
                "devices_total": len(devices),
                "sites_total": len(sites),
                "monitoring_sources_total": len(sources),
                "interfaces_total": sum(interface_counts.values()),
                "sensors_total": len(sensors),
            },
            "limitations": limitations,
        }

    # -- nodes -----------------------------------------------------------------

    def _node_block(
        self, devices, sites, sources, sensor_counts, alert_counts, interface_counts
    ) -> list[dict[str, Any]]:
        """One node per REAL row: devices, sites, monitoring sources.

        Device nodes carry the compact facts a node card needs (Step 8) and the
        per-type counts the detail drawer needs (Step 9). Counts are looked up
        from maps built with single aggregate queries, so node count does not
        drive query count.
        """
        source_by_id = {source.id: source for source in sources}
        nodes: list[dict[str, Any]] = []

        for device in devices:
            source = source_by_id.get(device.monitoring_source_id)
            nodes.append({
                "id": f"{PREFIX_DEVICE}{device.id}",
                "type": "device",
                "name": device.name,
                # Canonical vocabulary (models/device_types.py) so the UI filter
                # chips map 1:1; raw spelling kept for provenance.
                "device_type": normalize_device_type(device.device_type),
                "device_type_raw": device.device_type,
                "is_network_device": is_network_infrastructure(device.device_type),
                "status": device.status,
                # Phase 2 canonical vocabulary - never `status == "up"`.
                "health": device_health_bucket(device.status),
                "site_id": str(device.site_id) if device.site_id else None,
                "monitoring_source": None if source is None else {
                    "id": str(source.id),
                    "name": source.name,
                    "source_type": source.source_type,
                },
                "metadata": {
                    "ip": device.ip_address,
                    "vendor": device.vendor,
                    "model": device.model,
                    "hostname": device.hostname,
                    "last_seen_at": (
                        device.last_seen_at.isoformat()
                        if device.last_seen_at else None
                    ),
                },
                "counts": {
                    # Absent from the aggregate maps means a real 0.
                    "sensors": sensor_counts.get(device.id, 0),
                    "alerts": alert_counts.get(device.id, 0),
                    "interfaces": interface_counts.get(device.id, 0),
                },
            })

        for site in sites:
            nodes.append({
                "id": f"{PREFIX_SITE}{site.id}",
                "type": "site",
                "name": site.name,
                "code": site.code,
                "site_type": site.site_type,
                "region": site.region,
                "status": None,
                # Site health is the canonical overview's job; leaving it None
                # here is honest rather than guessing from a partial fetch.
                "health": None,
                "latitude": site.latitude,
                "longitude": site.longitude,
                # Only real coordinates ever count - Phase 4 rule.
                "has_coordinates": (
                    site.latitude is not None and site.longitude is not None
                ),
                "metadata": {
                    "address": site.address,
                    "city": site.city,
                    "country": site.country,
                    "criticality": site.criticality,
                },
                "counts": {
                    "devices": sum(1 for d in devices if d.site_id == site.id),
                },
            })

        for source in sources:
            nodes.append({
                "id": f"{PREFIX_SOURCE}{source.id}",
                "type": "monitoring_source",
                "name": source.name,
                "source_type": source.source_type,
                "status": source.status,
                "health": None,
                "site_id": str(source.site_id) if source.site_id else None,
                "metadata": {
                    "vendor": source.vendor,
                    "hostname": source.hostname,
                    "ip": source.ip_address,
                    "is_active": source.is_active,
                    "last_sync_at": (
                        source.last_sync_at.isoformat()
                        if source.last_sync_at else None
                    ),
                },
                "counts": {},
            })

        return nodes

    # -- edges -----------------------------------------------------------------

    def _edge_block(
        self, relationships, node_ids: set[str]
    ) -> tuple[list[dict[str, Any]], int]:
        """Normalize ResourceRelationship rows. Returns (edges, dangling_count).

        NO edge is ever synthesised. Every edge is a real row that already
        existed, carrying its own recorded provenance. An edge whose endpoints do
        not both resolve to a node in this tenant is excluded and counted as
        dangling (surfaced in `limitations`) rather than drawn between two
        missing endpoints.

        Link speed, VLAN, latency and bandwidth are NEVER derived - the model
        records none of them, so `metadata` is passed through untouched.
        """
        edges: list[dict[str, Any]] = []
        dangling = 0

        for rel in relationships:
            source_id = f"{PREFIX_DEVICE}{rel.source_resource_id}"
            target_id = f"{PREFIX_DEVICE}{rel.target_resource_id}"
            if source_id not in node_ids or target_id not in node_ids:
                # Resolves outside this tenant's node set: do not draw it.
                dangling += 1
                continue

            rel_type = rel.relationship_type
            edge: dict[str, Any] = {
                "id": str(rel.id),
                "source_id": source_id,
                "target_id": target_id,
                "relationship_type": rel_type,
                # Surfaced so the UI can label an unexpected value rather than
                # presenting it as a known relationship.
                "unknown_relationship_type": (
                    rel_type not in KNOWN_RELATIONSHIP_TYPES
                ),
                "relationship_subtype": rel.relationship_subtype,
                "label": rel.label,
                # Provenance.
                "evidence": PROVENANCE_RESOURCE_RELATIONSHIP,
                "discovered_by": rel.discovered_by,
                "is_inferred": rel.is_inferred,
                # Real NOT NULL columns, returned exactly as stored. No score is
                # invented anywhere else in this graph.
                "confidence": rel.confidence,
                "status": rel.status,
                "first_seen": rel.first_seen.isoformat() if rel.first_seen else None,
                "last_seen": rel.last_seen.isoformat() if rel.last_seen else None,
                "metadata": dict(rel.attributes or {}),
            }
            if rel.weight is not None:
                edge["weight"] = rel.weight
            edges.append(edge)

        return edges, dangling

    # -- status ----------------------------------------------------------------

    async def _status_block(
        self,
        *,
        tenant_id: uuid.UUID,
        nodes_total: int,
        edges_total: int,
        dangling: int,
        sites_total: int,
        interface_total: int,
        sources,
    ) -> tuple[str, list[str]]:
        """Honest status plus an explicit list of what is missing and why.

        Never returns an affirmative-sounding state when nothing was discovered:
        the UI must be able to say "no relationship data has been discovered",
        never "network is healthy" / "no issues".
        """
        limitations: list[str] = []

        if edges_total > 0 and nodes_total > 0:
            status = TOPOLOGY_READY
        elif nodes_total == 0:
            status = TOPOLOGY_NOT_DISCOVERED
            limitations.append("No devices, sites or monitoring sources exist yet.")
        else:
            # edges == 0. PARTIAL means a topology-capable collector actually ran
            # and still reported no adjacency (incomplete evidence);
            # NOT_DISCOVERED means no capable collector has produced anything.
            capable_succeeded, failing = await self._capable_source_evidence(
                tenant_id, sources
            )
            status = (
                TOPOLOGY_PARTIAL if capable_succeeded else TOPOLOGY_NOT_DISCOVERED
            )
            limitations.append(MESSAGE_NOT_DISCOVERED)

            if failing:
                limitations.append(
                    "Topology-capable monitoring source(s) currently failing, so "
                    "adjacency evidence cannot be collected: " + ", ".join(failing)
                )
            incapable = sorted({
                source.source_type for source in sources
                if source.source_type not in TOPOLOGY_CAPABLE_SOURCE_TYPES
            })
            if incapable:
                limitations.append(
                    "These monitoring source types do not collect interface or "
                    "adjacency data and cannot contribute edges: "
                    + ", ".join(incapable)
                )

        if interface_total == 0:
            limitations.append(
                "No interfaces have been collected for this tenant, so no "
                "interface-level links can be derived."
            )
        if sites_total == 0:
            limitations.append("Devices are not associated with a site.")
        if dangling:
            limitations.append(
                f"{dangling} relationship edge(s) reference resources outside this "
                "tenant's node set and were excluded from the graph."
            )

        return status, limitations

    async def _capable_source_evidence(
        self, tenant_id: uuid.UUID, sources
    ) -> tuple[bool, list[str]]:
        """Evidence about topology-capable sources: (any_succeeded, failing_names).

        Failure is decided the SAME way Phase 2's `effective_health` decides it -
        the collector's own latest run outranks the stored
        `consecutive_failures` counter. The live Development tenant proves why:
        its `network-discovery` SNMP source has `consecutive_failures = 0` while
        its most recent CollectorRun status is `error`, so a counter-only check
        would report nothing wrong with the one source capable of supplying
        adjacency evidence.
        """
        any_succeeded = False
        failing: list[str] = []

        for source in sources:
            if source.source_type not in TOPOLOGY_CAPABLE_SOURCE_TYPES:
                continue
            latest = await self.collector_runs.list(
                tenant_id, monitoring_source_id=source.id, limit=1
            )
            latest_run = latest[0] if latest else None
            latest_failed = bool(
                latest_run and latest_run.status in ("error", "partial")
            )
            if latest_run is not None and latest_run.status == "success":
                any_succeeded = True
            if (
                latest_failed
                or (source.consecutive_failures or 0) > 0
                or latest_run is None
            ):
                failing.append(source.name)

        return any_succeeded, failing
