"""
Repository Pattern: all direct SQLAlchemy access for ResourceRelationship
lives here. Tenant-scoped at every query; upsert semantics respect the
(tenant_id, source, target, relationship_type) unique constraint.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resource_relationship import ResourceRelationship
from app.models.device import Device


class ResourceRelationshipRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- Writes --

    async def upsert(
        self,
        tenant_id: uuid.UUID,
        *,
        source_resource_id: uuid.UUID,
        target_resource_id: uuid.UUID,
        relationship_type: str,
        relationship_subtype: str | None = None,
        label: str | None = None,
        weight: float | None = None,
        confidence: float = 100.0,
        discovered_by: str = "manual",
        status: str = "active",
        attributes: dict | None = None,
    ) -> tuple[ResourceRelationship, bool]:
        """Create or refresh a relationship edge.

        Returns (edge, created). Re-upserting an existing edge updates
        last_seen/confidence/status rather than violating the unique
        constraint — the same idempotent-discovery contract the Device
        collector framework uses.
        """
        existing = await self.get_edge(
            tenant_id, source_resource_id, target_resource_id, relationship_type
        )
        now = datetime.now(timezone.utc)
        if existing is not None:
            existing.last_seen = now
            existing.confidence = max(existing.confidence, confidence)
            if status:
                existing.status = status
            if attributes is not None:
                merged = dict(existing.attributes or {})
                merged.update(attributes)
                existing.attributes = merged
            if relationship_subtype is not None:
                existing.relationship_subtype = relationship_subtype
            if label is not None:
                existing.label = label
            if weight is not None:
                existing.weight = weight
            await self.db.flush()
            await self.db.refresh(existing)
            return existing, False

        edge = ResourceRelationship(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            source_resource_id=source_resource_id,
            target_resource_id=target_resource_id,
            relationship_type=relationship_type,
            relationship_subtype=relationship_subtype,
            label=label,
            weight=weight,
            confidence=confidence,
            discovered_by=discovered_by,
            status=status,
            attributes=attributes or {},
            first_seen=now,
            last_seen=now,
        )
        self.db.add(edge)
        await self.db.flush()
        await self.db.refresh(edge)
        return edge, True

    async def update(self, edge: ResourceRelationship, **fields) -> ResourceRelationship:
        for key, value in fields.items():
            if value is not None and hasattr(edge, key):
                setattr(edge, key, value)
        await self.db.flush()
        await self.db.refresh(edge)
        return edge

    async def delete(self, edge: ResourceRelationship) -> None:
        """Soft delete (SoftDeleteMixin contract)."""
        edge.is_deleted = True
        edge.deleted_at = datetime.now(timezone.utc)
        await self.db.flush()

    # -- Reads --

    async def get(
        self, relationship_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> ResourceRelationship | None:
        stmt = select(ResourceRelationship).where(
            ResourceRelationship.id == relationship_id,
            ResourceRelationship.tenant_id == tenant_id,
            ResourceRelationship.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_edge(
        self,
        tenant_id: uuid.UUID,
        source_resource_id: uuid.UUID,
        target_resource_id: uuid.UUID,
        relationship_type: str,
    ) -> ResourceRelationship | None:
        stmt = select(ResourceRelationship).where(
            ResourceRelationship.tenant_id == tenant_id,
            ResourceRelationship.source_resource_id == source_resource_id,
            ResourceRelationship.target_resource_id == target_resource_id,
            ResourceRelationship.relationship_type == relationship_type,
            ResourceRelationship.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        source_resource_id: uuid.UUID | None = None,
        target_resource_id: uuid.UUID | None = None,
        relationship_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ResourceRelationship]:
        stmt = select(ResourceRelationship).where(
            ResourceRelationship.tenant_id == tenant_id,
            ResourceRelationship.is_deleted.is_(False),
        )
        if source_resource_id:
            stmt = stmt.where(ResourceRelationship.source_resource_id == source_resource_id)
        if target_resource_id:
            stmt = stmt.where(ResourceRelationship.target_resource_id == target_resource_id)
        if relationship_type:
            stmt = stmt.where(ResourceRelationship.relationship_type == relationship_type)
        if status:
            stmt = stmt.where(ResourceRelationship.status == status)
        stmt = stmt.order_by(ResourceRelationship.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count_linked_devices(self, tenant_id: uuid.UUID) -> int:
        """Devices with at least one recorded relationship or topology link.

        This is what a topology can actually connect; every other device is
        drawn unlinked. Counted in SQL (no row materialisation).
        """
        from sqlalchemy import union

        from app.models.topology_link import TopologyLink

        rel = (ResourceRelationship.tenant_id == tenant_id, ResourceRelationship.is_deleted.is_(False))
        link = (TopologyLink.tenant_id == tenant_id, TopologyLink.is_deleted.is_(False))
        ends = union(
            select(ResourceRelationship.source_resource_id.label("device_id")).where(*rel),
            select(ResourceRelationship.target_resource_id.label("device_id")).where(*rel),
            select(TopologyLink.device_a_id.label("device_id")).where(*link),
            select(TopologyLink.device_b_id.label("device_id")).where(*link),
        ).subquery()
        stmt = select(func.count()).select_from(Device).where(
            Device.tenant_id == tenant_id,
            Device.is_deleted.is_(False),
            Device.id.in_(select(ends.c.device_id)),
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def count(
        self,
        tenant_id: uuid.UUID,
        *,
        relationship_type: str | None = None,
        status: str | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(ResourceRelationship).where(
            ResourceRelationship.tenant_id == tenant_id,
            ResourceRelationship.is_deleted.is_(False),
        )
        if relationship_type:
            stmt = stmt.where(ResourceRelationship.relationship_type == relationship_type)
        if status:
            stmt = stmt.where(ResourceRelationship.status == status)
        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        total = await self.count(tenant_id)

        type_stmt = (
            select(ResourceRelationship.relationship_type, func.count())
            .where(
                ResourceRelationship.tenant_id == tenant_id,
                ResourceRelationship.is_deleted.is_(False),
            )
            .group_by(ResourceRelationship.relationship_type)
        )
        type_result = await self.db.execute(type_stmt)
        by_type = [
            {"relationship_type": rtype, "count": cnt}
            for rtype, cnt in type_result.all()
        ]

        status_stmt = (
            select(ResourceRelationship.status, func.count())
            .where(
                ResourceRelationship.tenant_id == tenant_id,
                ResourceRelationship.is_deleted.is_(False),
            )
            .group_by(ResourceRelationship.status)
        )
        status_result = await self.db.execute(status_stmt)
        by_status = {s: cnt for s, cnt in status_result.all()}

        return {
            "total": total,
            "by_type": by_type,
            "by_status": by_status,
        }

    # -- Traversal helpers (one BFS step per call; service owns the walk) --

    async def list_edges_from(
        self, device_id: uuid.UUID, tenant_id: uuid.UUID, limit: int = 500
    ) -> list[tuple[ResourceRelationship, uuid.UUID, str | None]]:
        """Edges where device_id is the SOURCE, with the target device's name.

        Direction semantics: source -> target reads "source depends on
        target", so these neighbours are the dependencies of device_id.
        Only active/degraded edges participate in the live graph.
        """
        stmt = (
            select(ResourceRelationship, Device.name)
            .join(Device, Device.id == ResourceRelationship.target_resource_id)
            .where(
                ResourceRelationship.source_resource_id == device_id,
                ResourceRelationship.tenant_id == tenant_id,
                ResourceRelationship.is_deleted.is_(False),
                ResourceRelationship.status.in_(("active", "degraded")),
            )
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return [(rel, target_name) for rel, target_name in result.all()]

    async def list_edges_to(
        self, device_id: uuid.UUID, tenant_id: uuid.UUID, limit: int = 500
    ) -> list[tuple[ResourceRelationship, uuid.UUID, str | None]]:
        """Edges where device_id is the TARGET, with the source device's name.

        Direction semantics: source -> target reads "source depends on
        target", so these neighbours are the dependents of device_id
        (the devices whose failure would impact them).
        """
        stmt = (
            select(ResourceRelationship, Device.name)
            .join(Device, Device.id == ResourceRelationship.source_resource_id)
            .where(
                ResourceRelationship.target_resource_id == device_id,
                ResourceRelationship.tenant_id == tenant_id,
                ResourceRelationship.is_deleted.is_(False),
                ResourceRelationship.status.in_(("active", "degraded")),
            )
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return [(rel, source_name) for rel, source_name in result.all()]