"""
Topology service.

Manages the infrastructure dependency graph built from ResourceRelationship
edges. Provides graph traversal for impact analysis and root cause detection.

Repository discipline: this module contains NO direct SQLAlchemy queries —
all edge access goes through ResourceRelationshipRepository, per the project
convention that every repository owns its model's SQL.
"""
from __future__ import annotations

import uuid
from collections import deque
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.device_repository import DeviceRepository
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.sensor_repository import SensorRepository
from app.repositories.site_repository import SiteRepository
from app.schemas.topology import (
    DeviceNode, MonitoringSourceNode, SensorNode, SiteNode, TopologyOut,
)
from app.repositories.resource_relationship_repository import (
    ResourceRelationshipRepository,
)

# Relationship types that express "this device depends on / is carried by
# that one". Traversal for impact analysis walks these; purely informational
# edges (managed_by, logged_to, monitored_by) do not carry outage impact.
IMPACT_RELATIONSHIP_TYPES = frozenset({
    "depends_on", "protected_by", "routes_through", "hosted_on",
    "serves", "backup_for", "failover_to", "connected_to",
})


class TopologyService:
    """Service for querying and analyzing the infrastructure topology graph.

    All queries are tenant-scoped at the repository layer; the BFS walk here
    never crosses a tenant boundary because every per-step edge fetch is
    filtered by tenant_id.
    """

    def __init__(self, db: AsyncSession):
        self.repo = ResourceRelationshipRepository(db)
        self.db = db

    async def tenant_topology(self, tenant_id: uuid.UUID) -> TopologyOut:
        """Build the site tree from tenant-scoped records, without invented sites.

        Unassigned devices/sources are not assigned a fabricated location. The
        existing site-tree contract contains only resources with a known site.
        Fetch devices without the repository's default 100-row page limit.
        """
        sites = await SiteRepository(self.db).list(tenant_id)
        nodes = {
            site.id: SiteNode(
                id=site.id, name=site.name, code=site.code,
                address=site.address, region=site.region,
            )
            for site in sites
        }
        if not nodes:
            return TopologyOut(tenant_id=tenant_id, generated_at=datetime.now(timezone.utc))

        devices = await DeviceRepository(self.db).list(tenant_id, limit=None)
        sources = await MonitoringSourceRepository(self.db).list(tenant_id)
        sensors = await SensorRepository(self.db).list_for_tenant(tenant_id)
        source_nodes = {}
        for source in sources:
            if source.site_id in nodes:
                node = MonitoringSourceNode(
                    id=source.id, name=source.name,
                    source_type=source.source_type, status=source.status,
                )
                nodes[source.site_id].monitoring_sources.append(node)
                source_nodes[source.id] = node
        for sensor in sensors:
            if sensor.monitoring_source_id in source_nodes:
                source_nodes[sensor.monitoring_source_id].sensors.append(
                    SensorNode.model_validate(sensor, from_attributes=True)
                )
        for device in devices:
            if device.site_id in nodes:
                nodes[device.site_id].devices.append(
                    DeviceNode.model_validate(device, from_attributes=True)
                )
        return TopologyOut(
            tenant_id=tenant_id, generated_at=datetime.now(timezone.utc),
            sites=list(nodes.values()),
        )

    async def get_upstream_dependencies(
        self, device_id: uuid.UUID, tenant_id: uuid.UUID, max_depth: int = 5
    ) -> list[dict]:
        """Get all upstream dependencies of a device (what it depends on).

        Edge convention (model docstring): source -> target reads "source
        depends on target" (e.g. app --depends_on--> database). So the
        upstream walk follows edges where the device is the SOURCE — the
        things it points AT are its dependencies. BFS order keeps the
        nearest dependency first; only IMPACT_RELATIONSHIP_TYPES traverse.
        """
        visited: set[uuid.UUID] = {device_id}
        results: list[dict] = []
        queue: deque[tuple[uuid.UUID, int]] = deque([(device_id, 0)])

        while queue:
            current_id, depth = queue.popleft()
            if depth >= max_depth:
                continue
            edges = await self.repo.list_edges_from(current_id, tenant_id)
            for rel, target_name in edges:
                if rel.relationship_type not in IMPACT_RELATIONSHIP_TYPES:
                    continue
                if rel.target_resource_id in visited:
                    continue
                visited.add(rel.target_resource_id)
                results.append({
                    "dependency_device_id": str(rel.target_resource_id),
                    "dependency_device_name": target_name,
                    "source_device_id": str(rel.source_resource_id),
                    "target_device_id": str(rel.target_resource_id),
                    "relationship_type": rel.relationship_type,
                    "depth": depth + 1,
                })
                queue.append((rel.target_resource_id, depth + 1))

        return results

    async def get_downstream_impact(
        self, device_id: uuid.UUID, tenant_id: uuid.UUID, max_depth: int = 5
    ) -> list[dict]:
        """Get all downstream impact of a device failure (what depends on it).

        Blast radius: edges where the device is the TARGET (its dependents
        point AT it), walked in reverse BFS order.
        """
        visited: set[uuid.UUID] = {device_id}
        results: list[dict] = []
        queue: deque[tuple[uuid.UUID, int]] = deque([(device_id, 0)])

        while queue:
            current_id, depth = queue.popleft()
            if depth >= max_depth:
                continue
            edges = await self.repo.list_edges_to(current_id, tenant_id)
            for rel, source_name in edges:
                if rel.relationship_type not in IMPACT_RELATIONSHIP_TYPES:
                    continue
                if rel.source_resource_id in visited:
                    continue
                visited.add(rel.source_resource_id)
                results.append({
                    "dependent_device_id": str(rel.source_resource_id),
                    "dependent_device_name": source_name,
                    "source_device_id": str(rel.source_resource_id),
                    "target_device_id": str(rel.target_resource_id),
                    "relationship_type": rel.relationship_type,
                    "depth": depth + 1,
                })
                queue.append((rel.source_resource_id, depth + 1))

        return results

    async def get_connected_devices(
        self, device_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[dict]:
        """All devices directly connected to this device (any edge type,
        depth 1 only)."""
        edges = await self.repo.list_edges_from(device_id, tenant_id)
        return [
            {
                "device_id": str(rel.target_resource_id),
                "device_name": target_name,
                "relationship_type": rel.relationship_type,
                "status": rel.status,
            }
            for rel, target_name in edges
        ]
