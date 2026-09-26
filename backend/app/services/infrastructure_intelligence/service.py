"""
Universal infrastructure read models (spec sections 22, 23, 31, 33, 34).

One service that every universal-infrastructure endpoint reads through, so the
tenant overview, site overview and resource views can never disagree about a
count.

Rules
-----
* Every number comes from a persisted row. Nothing is computed from a constant.
* Batch queries only: resource lists resolve capabilities, interfaces and health
  through one query per concern, never one per resource (spec section 34).
* A capability that was not collected is returned as ``supported=false`` (or
  ``supported/not_collected``) with a reason - never as 0.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.device_action import DeviceCapability
from app.repositories.alert_repository import AlertRepository
from app.repositories.bgp_neighbor_repository import BgpNeighborRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.incident_repository import IncidentRepository
from app.repositories.interface_repository import InterfaceRepository
from app.repositories.maintenance_window_repository import (
    MaintenanceWindowRepository,
)
from app.repositories.metric_repository import MetricRepository
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.network_route_repository import NetworkRouteRepository
from app.repositories.resource_capability_repository import (
    ResourceCapabilityRepository,
)
from app.repositories.resource_relationship_repository import (
    ResourceRelationshipRepository,
)
from app.repositories.site_repository import SiteRepository
from app.repositories.topology_link_repository import TopologyLinkRepository
from app.services.infrastructure_intelligence import capabilities as CAP
from app.services.infrastructure_intelligence import operational_state as O
from app.services.infrastructure_intelligence import providers as PROV
from app.services.infrastructure_intelligence import telemetry as T
from app.services.infrastructure_intelligence import vocabulary as V
from app.services.infrastructure_intelligence.health import HealthService
from app.services.infrastructure_intelligence.hsignce import HsignceContextBuilder
from app.services.infrastructure_intelligence.network import (
    BgpNeighborState,
    RouteEntry,
    UniversalInterface,
    VpnTunnel,
    WanMember,
    WAN_LINK_TYPES,
    VPN_LINK_TYPES,
    evaluate_sdwan_members,
)
from app.services.infrastructure_intelligence.resources import (
    UniversalResource,
    resource_type_catalog,
)

#: Hard ceiling on any unbounded list this service will ever return.
MAX_LIMIT = 500


class UniversalInfrastructureService:
    """Tenant-scoped read models for the universal infrastructure layer."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.device_repo = DeviceRepository(db)
        self.site_repo = SiteRepository(db)
        self.interface_repo = InterfaceRepository(db)
        self.metric_repo = MetricRepository(db)
        self.capability_repo = ResourceCapabilityRepository(db)
        self.route_repo = NetworkRouteRepository(db)
        self.bgp_repo = BgpNeighborRepository(db)
        self.link_repo = TopologyLinkRepository(db)
        self.alert_repo = AlertRepository(db)
        self.incident_repo = IncidentRepository(db)
        self.maintenance_repo = MaintenanceWindowRepository(db)
        self.relationship_repo = ResourceRelationshipRepository(db)
        self.source_repo = MonitoringSourceRepository(db)
        self.health_service = HealthService(
            device_repo=self.device_repo,
            site_repo=self.site_repo,
            relationship_repo=self.relationship_repo,
            maintenance_repo=self.maintenance_repo,
        )

    # -- catalogues --------------------------------------------------------
    def capability_catalog(self) -> dict:
        """Every capability the canonical model can represent, with its metrics."""
        return {
            "capabilities": [
                {
                    "capability": capability,
                    "metrics": list(T.metrics_for_capability(capability)),
                    "universal": capability in V.UNIVERSAL_CAPABILITIES,
                }
                for capability in V.ALL_CAPABILITIES
            ],
            "resource_families": list(V.RESOURCE_FAMILIES),
            "resource_types": resource_type_catalog(),
            "support_states": list(V.SUPPORT_STATES),
            "collection_states": list(V.COLLECTION_STATES),
            "reasons": list(V.REASONS),
            "relationship_types": list(V.RELATIONSHIP_TYPES),
            "interface_types": list(V.INTERFACE_TYPES),
            "route_protocols": list(V.ROUTE_PROTOCOLS),
            "bgp_states": list(V.BGP_STATES),
            "vpn_types": list(V.VPN_TYPES),
            "wan_roles": list(V.WAN_ROLES),
        }

    def provider_catalog(self) -> dict:
        """Declared providers with their REAL wired state."""
        rows = PROV.provider_catalog()
        return {
            "providers": rows,
            "wired": sorted(PROV.implemented_providers()),
            "declared_count": len(rows),
            "wired_count": sum(1 for row in rows if row["wired"]),
            "source_type_map": dict(PROV.SOURCE_TYPE_TO_PROVIDER),
        }

    # -- helpers -----------------------------------------------------------
    async def _provider_by_source(self, tenant_id: uuid.UUID) -> dict:
        """``monitoring_source_id`` -> provider token, in ONE query."""
        from app.repositories.monitoring_source_repository import (
            MonitoringSourceRepository,
        )

        sources = await MonitoringSourceRepository(self.db).list(
            tenant_id, limit=10000
        )
        return {
            source.id: PROV.provider_from_source_type(source.source_type)
            for source in sources
        }

    @staticmethod
    def _provider_of(device, provider_by_source: dict) -> str | None:
        """Provider token for a resource, from its own column or its source."""
        column = getattr(device, "provider", None)
        if column:
            return column
        source_id = getattr(device, "monitoring_source_id", None)
        if source_id is None:
            return None
        return provider_by_source.get(source_id)

    async def _resources(
        self, devices: list, provider_by_source: dict
    ) -> list[UniversalResource]:
        return [
            UniversalResource.from_device(
                device, provider=self._provider_of(device, provider_by_source)
            )
            for device in devices
        ]

    @staticmethod
    def _page(items: list, total: int, limit: int, offset: int) -> dict:
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(items) < total,
        }

    # -- operational state (Phase 7) ---------------------------------------
    #: Bounded read of relationship rows used to derive topology status. The
    #: index answers "which edges touch the resources on this page", so the list
    #: view never needs an N+1 query per resource.
    TOPOLOGY_EDGE_LIMIT = 2000

    async def _sources_index(self, tenant_id: uuid.UUID) -> dict:
        rows = await self.source_repo.list(tenant_id, limit=self.TOPOLOGY_EDGE_LIMIT)
        return {row.id: row for row in rows}

    async def _discovery_capability_index(
        self, tenant_id: uuid.UUID, device_ids: set
    ) -> dict:
        """``{device_id: (capability, ...)}`` from the DISCOVERY capability store.

        Discovery writes the capability flags it actually reached (ping/snmp/ssh/…)
        into ``device_capabilities``, while the universal capability model reads
        its own assertion store. Without this bridge a discovery-created resource
        could never be reported as collectable, which is exactly the kind of
        integration gap that makes a resource look permanently unready.

        Only flags asserted ``True`` count; a NULL flag is "we do not know" and is
        never evidence.
        """
        if not device_ids:
            return {}
        rows = (
            await self.db.execute(
                select(DeviceCapability).where(
                    DeviceCapability.tenant_id == tenant_id,
                    DeviceCapability.device_id.in_(list(device_ids)),
                )
            )
        ).scalars()
        index: dict = {}
        for row in rows:
            asserted = tuple(
                column
                for column in (
                    "supports_ping",
                    "supports_snmp",
                    "supports_ssh",
                    "supports_rest",
                    "supports_config",
                    "supports_reboot",
                    "supports_firmware",
                    "supports_backup",
                    "supports_rollback",
                )
                if getattr(row, column, None) is True
            )
            index[row.device_id] = asserted
        return index

    async def _relationship_index(
        self, tenant_id: uuid.UUID, device_ids: set
    ) -> dict:
        """``{device_id: [edge, ...]}`` for the resources in scope (bounded)."""
        if not device_ids:
            return {}
        rows = await self.relationship_repo.list(
            tenant_id, limit=self.TOPOLOGY_EDGE_LIMIT
        )
        index: dict = {}
        for edge in rows:
            for endpoint in (edge.source_resource_id, edge.target_resource_id):
                if endpoint in device_ids:
                    index.setdefault(endpoint, []).append(edge)
        return index

    @staticmethod
    def _identity_key(device) -> str | None:
        """The most authoritative identity key this resource holds.

        Serial first (manufacturer-assigned and stable), then the provider-native
        id, then the management address - the same precedence the discovery
        identity resolver uses, so the two can never disagree about what
        identifies a device.
        """
        return (
            getattr(device, "serial_number", None)
            or getattr(device, "provider_resource_id", None)
            or getattr(device, "external_id", None)
            or getattr(device, "ip_address", None)
        )

    @classmethod
    def _identity_confidence(cls, device) -> float:
        """Strength of the identity evidence, NOT a measured probability.

        1.0 = manufacturer serial, 0.6 = provider-native id, 0.3 = address only,
        0.0 = nothing usable. Exposed so the UI can rank identity quality.
        """
        if getattr(device, "serial_number", None):
            return 1.0
        if getattr(device, "provider_resource_id", None) or getattr(
            device, "external_id", None
        ):
            return 0.6
        if getattr(device, "ip_address", None):
            return 0.3
        return 0.0

    def _facts_for_device(
        self,
        device,
        *,
        sources_by_id: dict,
        capability_rows: list,
        edges: list,
        competing_source_ids: tuple[str, ...] = (),
        discovery_capabilities: tuple[str, ...] = (),
    ) -> O.ResourceFacts:
        source = sources_by_id.get(getattr(device, "monitoring_source_id", None))
        supported = tuple(
            row.capability
            for row in capability_rows
            if row.support == V.SUPPORT_SUPPORTED
        )
        unsupported = tuple(
            row.capability
            for row in capability_rows
            if row.support == V.SUPPORT_UNSUPPORTED
        )
        unknown = tuple(
            row.capability
            for row in capability_rows
            if row.support == V.SUPPORT_UNKNOWN
        )
        resource_type = str(getattr(device, "device_type", "") or "unknown")
        identity_key = self._identity_key(device)
        return O.ResourceFacts(
            resource_id=str(device.id),
            resource_type=resource_type,
            resource_name=getattr(device, "name", None),
            lifecycle_state=getattr(device, "lifecycle_state", None),
            identity_key=identity_key,
            identity_confidence=self._identity_confidence(device),
            recognized=str(resource_type).strip().lower()
            not in {"", "unknown", "unrecognized", "unrecognised"},
            vendor=getattr(device, "vendor", None),
            model=getattr(device, "model", None),
            monitoring_source_id=(
                str(device.monitoring_source_id)
                if getattr(device, "monitoring_source_id", None)
                else None
            ),
            monitoring_source_name=getattr(source, "name", None),
            monitoring_source_type=getattr(source, "source_type", None),
            monitoring_source_active=(
                getattr(source, "is_active", None) if source is not None else None
            ),
            provider=getattr(device, "provider", None),
            provider_resource_id=getattr(device, "provider_resource_id", None),
            competing_source_ids=competing_source_ids,
            last_telemetry_at=getattr(device, "last_telemetry_at", None),
            last_collection_status=getattr(source, "status", None),
            supported_capabilities=supported,
            unsupported_capabilities=unsupported,
            unknown_capabilities=unknown,
            discovery_capabilities=discovery_capabilities,
            relationship_count=len(edges or ()),
        )

    def _identity_claims(self, devices: list) -> dict:
        """``{authoritative_identity_key: (monitoring_source_id, ...)}``.

        Only AUTHORITATIVE keys (serial, then provider-native id) are used to
        detect competing collector claims. The management address is deliberately
        excluded: DHCP reassignment means two sources legitimately reporting the
        same IP at different times is not evidence of ambiguity.
        """
        claims: dict[str, list] = {}
        for device in devices:
            key = getattr(device, "serial_number", None) or getattr(
                device, "provider_resource_id", None
            ) or getattr(device, "external_id", None)
            if not key:
                continue
            source_id = getattr(device, "monitoring_source_id", None)
            bucket = claims.setdefault(str(key), [])
            if source_id is not None and str(source_id) not in bucket:
                bucket.append(str(source_id))
        return {key: tuple(values) for key, values in claims.items()}

    async def operational_states(
        self,
        tenant_id: uuid.UUID,
        devices: list,
        *,
        now: datetime | None = None,
    ) -> dict:
        """``{resource_id: operational state dict}`` for a bounded device set.

        One query each for sources, capabilities and relationships - never one per
        resource - so this can back a list view.
        """
        if not devices:
            return {}
        device_ids = {device.id for device in devices}
        sources_by_id = await self._sources_index(tenant_id)
        capability_rows = await self.capability_repo.list_for_resources(
            list(device_ids), tenant_id
        )
        edges_by_resource = await self._relationship_index(tenant_id, device_ids)
        discovery_caps = await self._discovery_capability_index(tenant_id, device_ids)
        claims = self._identity_claims(devices)

        states: dict = {}
        for device in devices:
            key = getattr(device, "serial_number", None) or getattr(
                device, "provider_resource_id", None
            ) or getattr(device, "external_id", None)
            claimants = claims.get(str(key), ()) if key else ()
            own_source = (
                str(device.monitoring_source_id)
                if getattr(device, "monitoring_source_id", None)
                else None
            )
            competing = tuple(
                source_id
                for source_id in claimants
                if own_source is None or source_id != own_source
            )
            edges = edges_by_resource.get(device.id, [])
            facts = self._facts_for_device(
                device,
                sources_by_id=sources_by_id,
                capability_rows=capability_rows.get(device.id, []),
                edges=edges,
                competing_source_ids=competing,
                discovery_capabilities=discovery_caps.get(device.id, ()),
            )
            states[str(device.id)] = O.derive_operational_state(
                facts, edges=edges, now=now
            ).to_dict()
        return states

    async def resource_operational_state(
        self,
        tenant_id: uuid.UUID,
        resource_id: uuid.UUID,
        *,
        now: datetime | None = None,
    ) -> dict | None:
        """Operational state for one resource, or None when it is not visible."""
        device = await self.device_repo.get(resource_id, tenant_id)
        if device is None:
            return None
        states = await self.operational_states(tenant_id, [device], now=now)
        return states.get(str(resource_id))


    async def tenant_overview(self, tenant_id: uuid.UUID) -> dict:
        """Tenant-wide universal infrastructure overview, from persisted rows."""
        provider_by_source = await self._provider_by_source(tenant_id)
        devices = await self.device_repo.list(tenant_id, limit=100_000)
        resources = await self._resources(devices, provider_by_source)

        by_type: dict[str, int] = {}
        by_family: dict[str, int] = {}
        by_provider: dict[str, int] = {}
        for resource in resources:
            by_type[resource.resource_type] = by_type.get(resource.resource_type, 0) + 1
            by_family[resource.family] = by_family.get(resource.family, 0) + 1
            if resource.provider:
                by_provider[resource.provider] = by_provider.get(resource.provider, 0) + 1

        health = await self.health_service.tenant_health(tenant_id)
        capability_summary = await self.capability_repo.summary(tenant_id)
        interfaces_total = await self.interface_repo.count_for_tenant(tenant_id)

        capability_by_resource = await self.capability_repo.list_for_resources(
            [device.id for device in devices], tenant_id
        )
        supported_by_capability: dict[str, int] = {}
        for rows in capability_by_resource.values():
            for row in rows:
                if row.support == V.SUPPORT_SUPPORTED:
                    supported_by_capability[row.capability] = (
                        supported_by_capability.get(row.capability, 0) + 1
                    )

        sites = await self.site_repo.list(tenant_id)
        last_telemetry_values = [
            device.last_telemetry_at
            for device in devices
            if getattr(device, "last_telemetry_at", None)
        ]
        newest_telemetry = max(last_telemetry_values) if last_telemetry_values else None
        incidents_open = await self.incident_repo.count(tenant_id, status="open")
        routing_by_protocol = await self.route_repo.count_by_protocol_for_tenant(
            tenant_id
        )
        bgp_by_state = await self.bgp_repo.count_by_state_for_tenant(tenant_id)

        return {
            "tenant_id": str(tenant_id),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "health": health.to_dict(),
            "sites": {"total": len(sites)},
            "resources": {
                "total": len(resources),
                "by_resource_type": dict(sorted(by_type.items())),
                "by_family": dict(sorted(by_family.items())),
                "by_provider": dict(sorted(by_provider.items())),
                "by_lifecycle_state": _count(
                    resource.lifecycle_state for resource in resources
                ),
                "by_operational_state": _count(
                    resource.status for resource in resources
                ),
            },
            "interfaces": {"total": interfaces_total},
            "network": {
                "routing_entries": sum(routing_by_protocol.values()),
                "routing_by_protocol": routing_by_protocol,
                "bgp_neighbors": sum(bgp_by_state.values()),
                "bgp_by_state": bgp_by_state,
            },
            "cloud": {
                "resources": sum(
                    count for family, count in by_family.items()
                    if family in (V.FAMILY_CLOUD, V.FAMILY_DATABASE)
                ),
                "by_resource_type": dict(sorted(
                    (rtype, count) for rtype, count in by_type.items()
                    if V.resource_family(rtype) in (V.FAMILY_CLOUD, V.FAMILY_DATABASE)
                )),
            },
            "capabilities": {
                "asserted": capability_summary["total"],
                "by_support": capability_summary["by_support"],
                "supported_by_capability": dict(sorted(supported_by_capability.items())),
            },
            "incidents": {"open": incidents_open},
            "telemetry": {
                "newest_telemetry_at": (
                    newest_telemetry.isoformat() if newest_telemetry else None
                ),
                "freshness_seconds": T.freshness_seconds(newest_telemetry),
                "quality": T.quality_for(newest_telemetry),
                "records_by_capability": (
                    await self.metric_repo.count_by_capability_for_tenant(tenant_id)
                ),
            },
            "providers": {
                "wired": sorted(PROV.implemented_providers()),
                "in_use": sorted(by_provider),
            },
        }


    # -- resource list + detail (spec section 31) --------------------------
    async def list_resources(
        self,
        tenant_id: uuid.UUID,
        *,
        site_id: uuid.UUID | None = None,
        resource_type: str | None = None,
        family: str | None = None,
        provider: str | None = None,
        lifecycle_state: str | None = None,
        health: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Paginated universal resource list.

        The SQL filters on the REAL columns (site, type, search); the
        provider-neutral concepts (unified resource type, family, provider,
        health) are applied after projection, because they are normalisations
        rather than stored columns. ``total`` is therefore the count of what was
        actually returned, not the raw SQL count - the two are only equal when
        no post-projection filter is used, and reporting the SQL count otherwise
        would exaggerate the result set.
        """
        limit = max(1, min(limit, MAX_LIMIT))
        provider_by_source = await self._provider_by_source(tenant_id)
        devices = await self.device_repo.list(
            tenant_id, site_id=site_id, search=search, limit=MAX_LIMIT
        )
        resources = await self._resources(devices, provider_by_source)
        wanted_type = V.universal_resource_type(resource_type) if resource_type else None

        health_rows: dict[str, dict] = {}
        if health:
            for device in devices:
                result = await self.health_service.resource_health(tenant_id, device)
                health_rows[str(device.id)] = result.to_dict()

        filtered: list[UniversalResource] = []
        for resource in resources:
            if wanted_type and resource.resource_type != wanted_type:
                continue
            if family and resource.family != family:
                continue
            if provider and resource.provider != provider:
                continue
            if lifecycle_state and resource.lifecycle_state != lifecycle_state:
                continue
            if search and search.strip().lower() not in resource.name.lower():
                continue
            if health and health_rows.get(str(resource.resource_id), {}).get(
                "health"
            ) != health:
                continue
            filtered.append(resource)

        page_items = filtered[offset: offset + limit]
        rows = [resource.to_dict() for resource in page_items]
        for row in rows:
            if row["resource_id"] in health_rows:
                row["health_detail"] = health_rows[row["resource_id"]]

        # Operational state (Phase 7): identity stage, monitoring binding,
        # telemetry freshness and topology evidence for exactly the devices on
        # this page, in three bounded queries total (never one per resource).
        device_by_id = {str(device.id): device for device in devices}
        page_devices = [
            device_by_id[str(resource.resource_id)]
            for resource in page_items
            if str(resource.resource_id) in device_by_id
        ]
        state_by_id = await self.operational_states(tenant_id, page_devices)
        for row in rows:
            row["operational_state"] = state_by_id.get(row["resource_id"])
        return self._page(rows, len(filtered), limit, offset)

    async def get_resource(self, tenant_id: uuid.UUID, resource_id: uuid.UUID):
        """Tenant-scoped single resource fetch, or None (never cross-tenant)."""
        devices = await self.device_repo.list_by_ids(tenant_id, [resource_id])
        return devices[0] if devices else None

    async def resource_capabilities(
        self, tenant_id: uuid.UUID, resource_id: uuid.UUID
    ) -> dict:
        """Capability report for one resource, from REAL evidence.

        Persisted assertions (what a collector actually established) take
        precedence; everything else is resolved from the resource-type profile
        against the wired provider's declared capabilities. A capability with no
        evidence and no profile is reported ``unknown`` with a reason.
        """
        device = await self.get_resource(tenant_id, resource_id)
        if device is None:
            return {"found": False, "resource_id": str(resource_id)}
        provider_by_source = await self._provider_by_source(tenant_id)
        provider = self._provider_of(device, provider_by_source)
        collected = await self.metric_repo.distinct_capabilities_for_device(
            tenant_id, resource_id
        )
        asserted = {}
        for row in await self.capability_repo.list_for_resource(
            resource_id, tenant_id
        ):
            asserted[row.capability] = CAP.CapabilityAssertion(
                capability=row.capability,
                support=row.support,
                status=row.status,
                reason=row.reason,
                provider=row.provider,
                source=row.source,
                asserted_at=row.asserted_at,
                evidence=dict(row.evidence or {}),
            )
        report = CAP.resolve_capabilities(
            device.device_type,
            provider,
            resource_id=resource_id,
            provider_capabilities=PROV.capabilities_for_provider(provider),
            collected=collected,
            asserted=asserted,
        )
        data = report.to_dict()
        data["found"] = True
        data["telemetry_capabilities"] = collected
        return data

    # -- interfaces, telemetry, network views (spec sections 15-20, 31, 33) --
    async def resource_interfaces(
        self, tenant_id: uuid.UUID, resource_id: uuid.UUID
    ) -> dict:
        """Canonical interface list for one resource (spec section 15)."""
        device = await self.get_resource(tenant_id, resource_id)
        if device is None:
            return {"found": False, "items": [], "total": 0}
        provider_by_source = await self._provider_by_source(tenant_id)
        provider = self._provider_of(device, provider_by_source)
        interfaces = await self.interface_repo.list_for_device(resource_id)
        items = [
            UniversalInterface.from_interface(interface, provider=provider).to_dict()
            for interface in interfaces
        ]
        return {
            "found": True,
            "resource_id": str(resource_id),
            "items": items,
            "total": len(items),
            "up": sum(1 for i in items if i["oper_state"] == V.OPER_STATE_UP),
            "down": sum(1 for i in items if i["oper_state"] == V.OPER_STATE_DOWN),
            "unknown": sum(
                1 for i in items if i["oper_state"] == V.OPER_STATE_UNKNOWN
            ),
            "supported": bool(items),
            "reason": None if items else V.REASON_PRIMARY_DATA_MISSING,
        }

    async def resource_telemetry(
        self,
        tenant_id: uuid.UUID,
        resource_id: uuid.UUID,
        *,
        capability: str | None = None,
        metric_name: str | None = None,
        interface_id: uuid.UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict:
        """Bounded, aggregated canonical telemetry for one resource.

        Two shapes are returned: ``current`` (newest value per metric, one
        DISTINCT ON query) and ``series`` (a bounded, summarised window). The raw
        stream is never returned unbounded.
        """
        limit = max(1, min(limit, MAX_LIMIT))
        device = await self.get_resource(tenant_id, resource_id)
        if device is None:
            return {"found": False, "current": [], "series": [], "total": 0}

        current_rows = await self.metric_repo.latest_canonical_per_metric(
            tenant_id, device_id=resource_id, interface_id=interface_id
        )
        window_rows = await self.metric_repo.list_canonical(
            tenant_id,
            device_id=resource_id,
            interface_id=interface_id,
            capability=capability,
            metric_name=metric_name,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )
        total = await self.metric_repo.count_canonical(
            tenant_id,
            device_id=resource_id,
            interface_id=interface_id,
            capability=capability,
            metric_name=metric_name,
            since=since,
            until=until,
        )

        grouped: dict[str, list[T.CanonicalTelemetry]] = {}
        for row in window_rows:
            record = _telemetry_from_row(row, tenant_id, device.site_id)
            grouped.setdefault(record.metric_name, []).append(record)

        return {
            "found": True,
            "resource_id": str(resource_id),
            "current": [
                _telemetry_from_row(row, tenant_id, device.site_id).to_dict()
                for row in current_rows
            ],
            "series": [
                T.summarize_series(records).to_dict()
                for _, records in sorted(grouped.items())
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(window_rows) < total,
            "quality": T.quality_for(
                getattr(device, "last_telemetry_at", None)
            ),
        }


    async def resource_network(
        self, tenant_id: uuid.UUID, resource_id: uuid.UUID
    ) -> dict:
        """WAN / SD-WAN / VPN / routing / BGP state for one resource.

        Each section carries an explicit ``supported`` flag: a resource with no
        WAN links returns ``supported: false`` and a reason, never an empty
        table that would look like a healthy but quiet network.
        """
        device = await self.get_resource(tenant_id, resource_id)
        if device is None:
            return {"found": False}
        links, _total = await self.link_repo.list(
            tenant_id, device_id=resource_id, limit=MAX_LIMIT
        )
        wan_members = [
            WanMember.from_link(link)
            for link in links if link.link_type in WAN_LINK_TYPES
        ]
        tunnels = [
            VpnTunnel.from_link(link)
            for link in links
            if link.link_type in VPN_LINK_TYPES or link.vpn_name
        ]
        routes = await self.route_repo.list_for_resource(
            resource_id, tenant_id, limit=MAX_LIMIT
        )
        neighbors = await self.bgp_repo.list_for_resource(
            resource_id, tenant_id, limit=MAX_LIMIT
        )
        sdwan_members = [m for m in wan_members if m.is_sdwan_member]
        return {
            "found": True,
            "resource_id": str(resource_id),
            "wan": _section(
                [m.to_dict() for m in wan_members],
                supported=bool(wan_members),
                reason=V.REASON_PRIMARY_DATA_MISSING,
            ),
            "sdwan": _sdwan_section(sdwan_members),
            "vpn": _section(
                [t.to_dict() for t in tunnels],
                supported=bool(tunnels),
                reason=V.REASON_PRIMARY_DATA_MISSING,
            ),
            "routing": {
                **_section(
                    [RouteEntry.from_route(r).to_dict() for r in routes],
                    supported=bool(routes),
                    reason=V.REASON_PRIMARY_DATA_MISSING,
                ),
                "by_protocol": _count(r.protocol for r in routes),
            },
            "bgp": {
                **_section(
                    [BgpNeighborState.from_neighbor(n).to_dict() for n in neighbors],
                    supported=bool(neighbors),
                    reason=V.REASON_PRIMARY_DATA_MISSING,
                ),
                "established": sum(
                    1 for n in neighbors
                    if V.normalize_bgp_state(n.state) == V.BGP_STATE_ESTABLISHED
                ),
            },
        }


    async def site_overview(self, tenant_id: uuid.UUID, site_id: uuid.UUID) -> dict:
        """Site health + resource breakdown + network state (spec section 22)."""
        site = await self.site_repo.get(site_id, tenant_id)
        if site is None:
            return {"found": False, "site_id": str(site_id)}
        provider_by_source = await self._provider_by_source(tenant_id)
        devices = await self.device_repo.list(
            tenant_id, site_id=site_id, limit=100_000
        )
        resources = await self._resources(devices, provider_by_source)
        rollup = await self.health_service.site_health(tenant_id, site_id)

        by_type: dict[str, int] = {}
        by_family: dict[str, int] = {}
        for resource in resources:
            by_type[resource.resource_type] = by_type.get(resource.resource_type, 0) + 1
            by_family[resource.family] = by_family.get(resource.family, 0) + 1

        device_ids = {device.id for device in devices}
        all_links, _total = await self.link_repo.list(tenant_id, limit=MAX_LIMIT * 4)
        site_links = [
            link for link in all_links
            if link.device_a_id in device_ids or link.device_b_id in device_ids
        ]
        wan_members = [
            WanMember.from_link(link)
            for link in site_links if link.link_type in WAN_LINK_TYPES
        ]
        tunnels = [
            VpnTunnel.from_link(link)
            for link in site_links
            if link.link_type in VPN_LINK_TYPES or link.vpn_name
        ]
        interfaces = 0
        for device_id in device_ids:
            interfaces += len(await self.interface_repo.list_for_device(device_id))

        return {
            "found": True,
            "site": {
                "site_id": str(site.id),
                "name": site.name,
                "code": site.code,
                "criticality": getattr(site, "criticality", None),
                "region": getattr(site, "region", None),
            },
            "health": rollup.to_dict(),
            "resources": {
                "total": len(resources),
                "by_resource_type": dict(sorted(by_type.items())),
                "by_family": dict(sorted(by_family.items())),
            },
            "interfaces": {"total": interfaces},
            "wan": _section(
                [m.to_dict() for m in wan_members],
                supported=bool(wan_members),
                reason=V.REASON_PRIMARY_DATA_MISSING,
            ),
            "sdwan": _sdwan_section(
                [m for m in wan_members if m.is_sdwan_member]
            ),
            "vpn": _section(
                [t.to_dict() for t in tunnels],
                supported=bool(tunnels),
                reason=V.REASON_PRIMARY_DATA_MISSING,
            ),
            "incidents": {
                "open": await self.incident_repo.count(
                    tenant_id, status="open", site_id=site_id
                )
            },
        }

    async def correlation_context(
        self,
        tenant_id: uuid.UUID,
        resource_ids: list[uuid.UUID],
        *,
        include_dependencies: bool = True,
    ) -> dict:
        """HSI-GNCE correlation context for a set of resources (spec section 28)."""
        builder = HsignceContextBuilder(
            device_repo=self.device_repo,
            relationship_repo=self.relationship_repo,
            link_repo=self.link_repo,
            alert_repo=self.alert_repo,
            incident_repo=self.incident_repo,
            health_service=self.health_service,
        )
        return await builder.build(
            tenant_id, resource_ids, include_dependencies=include_dependencies
        )


def _sdwan_section(members: list) -> dict:
    """SD-WAN answer: engine evaluation, or an explicit unsupported reason."""
    if not members:
        return {
            "supported": False,
            "status": V.COLLECTION_NOT_AVAILABLE,
            "reason": V.REASON_PROVIDER_CAPABILITY_NOT_SUPPORTED,
            "available": False,
            "items": [],
            "total": 0,
        }
    return {
        "items": [m.to_dict() for m in members],
        "total": len(members),
        **evaluate_sdwan_members(members),
    }


def _section(items: list, *, supported: bool, reason: str) -> dict:
    """Consistent supported/reason envelope so no empty list looks like a fact."""
    return {
        "supported": supported,
        "status": V.COLLECTION_COLLECTED if supported else V.COLLECTION_NOT_AVAILABLE,
        "reason": None if supported else reason,
        "items": items,
        "total": len(items),
    }


def _count(values) -> dict:
    """Count occurrences of non-null values, sorted by key."""
    counts: dict[str, int] = {}
    for value in values:
        if value:
            counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items()))


def _telemetry_from_row(row, tenant_id: uuid.UUID, site_id) -> T.CanonicalTelemetry:
    """Project a stored Metric row into the canonical telemetry shape.

    Module-level (not a method) because it is a pure mapping with no service
    state; ``metric_name`` falls back to ``metric_type`` for rows written before
    the canonical columns existed, which the migration deliberately did not
    backfill.
    """
    return T.CanonicalTelemetry(
        tenant_id=tenant_id,
        site_id=site_id,
        resource_id=row.device_id,
        device_id=row.device_id,
        interface_id=row.interface_id,
        provider=row.provider,
        capability=row.capability or "unclassified",
        metric_name=row.metric_name or row.metric_type,
        timestamp=row.recorded_at,
        numeric_value=row.value,
        text_value=row.text_value,
        boolean_value=row.boolean_value,
        unit=row.unit,
        status=row.collection_status or V.COLLECTION_COLLECTED,
        collection_status=row.collection_status or V.COLLECTION_COLLECTED,
        quality=row.quality or T.QUALITY_UNKNOWN,
        source_timestamp=row.source_timestamp,
        received_timestamp=row.received_timestamp,
        dimensions=dict(row.dimensions or {}),
    )
