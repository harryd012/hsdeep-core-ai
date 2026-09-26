"""
Repository for ResourceCapability persistence.

Keyed on (resource_id, capability) per the model's UniqueConstraint, so a
repeated collection run refreshes a row instead of growing the table. Every
query is tenant-scoped - a capability assertion for another tenant's resource
must never be reachable (spec section 35).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resource_capability import ResourceCapability


class ResourceCapabilityRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(
        self, resource_id: uuid.UUID, capability: str
    ) -> ResourceCapability | None:
        stmt = select(ResourceCapability).where(
            ResourceCapability.resource_id == resource_id,
            ResourceCapability.capability == capability,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def upsert(
        self,
        *,
        tenant_id: uuid.UUID,
        resource_id: uuid.UUID,
        capability: str,
        support: str,
        status: str = "not_collected",
        reason: str | None = None,
        provider: str | None = None,
        source: str = "collector",
        evidence: dict | None = None,
        asserted_at: datetime | None = None,
    ) -> tuple[ResourceCapability, bool]:
        """Create or refresh one capability assertion.

        Raises ValueError when an unsupported/unknown assertion carries no
        reason - the same rule the database CHECK constraint enforces, raised
        early so the caller gets a useful error instead of an IntegrityError.
        """
        from app.services.infrastructure_intelligence import vocabulary as V

        if support != V.SUPPORT_SUPPORTED and not reason:
            raise ValueError(
                f"capability {capability!r} with support={support!r} requires a reason"
            )

        now = asserted_at or datetime.now(timezone.utc)
        existing = await self.get(resource_id, capability)
        if existing is None:
            row = ResourceCapability(
                tenant_id=tenant_id,
                resource_id=resource_id,
                capability=capability,
                support=support,
                status=status,
                reason=reason,
                provider=provider,
                source=source,
                asserted_at=now,
                evidence=evidence or {},
            )
            self.db.add(row)
            await self.db.flush()
            await self.db.refresh(row)
            return row, True

        existing.support = support
        existing.status = status
        existing.reason = reason
        existing.provider = provider
        existing.source = source
        existing.asserted_at = now
        if evidence is not None:
            merged = dict(existing.evidence or {})
            merged.update(evidence)
            existing.evidence = merged
        await self.db.flush()
        await self.db.refresh(existing)
        return existing, False

    async def list_for_resource(
        self, resource_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[ResourceCapability]:
        stmt = (
            select(ResourceCapability)
            .where(
                ResourceCapability.resource_id == resource_id,
                ResourceCapability.tenant_id == tenant_id,
            )
            .order_by(ResourceCapability.capability)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_for_resources(
        self, resource_ids: list[uuid.UUID], tenant_id: uuid.UUID
    ) -> dict[uuid.UUID, list[ResourceCapability]]:
        """Capabilities for MANY resources in one query (avoids an N+1).

        Resources with no rows are simply absent from the mapping - a real
        "nothing asserted yet", not a placeholder.
        """
        if not resource_ids:
            return {}
        stmt = (
            select(ResourceCapability)
            .where(
                ResourceCapability.resource_id.in_(resource_ids),
                ResourceCapability.tenant_id == tenant_id,
            )
            .order_by(ResourceCapability.capability)
        )
        grouped: dict[uuid.UUID, list[ResourceCapability]] = {}
        for row in (await self.db.execute(stmt)).scalars().all():
            grouped.setdefault(row.resource_id, []).append(row)
        return grouped

    async def count_supported_by_resource(
        self, tenant_id: uuid.UUID
    ) -> dict[uuid.UUID, int]:
        """Per-resource count of SUPPORTED capabilities, in one query."""
        stmt = (
            select(ResourceCapability.resource_id, func.count())
            .where(
                ResourceCapability.tenant_id == tenant_id,
                ResourceCapability.support == "supported",
            )
            .group_by(ResourceCapability.resource_id)
        )
        rows = (await self.db.execute(stmt)).all()
        return {resource_id: count for resource_id, count in rows}

    async def count_for_tenant(
        self, tenant_id: uuid.UUID, *, support: str | None = None
    ) -> int:
        stmt = select(func.count()).select_from(ResourceCapability).where(
            ResourceCapability.tenant_id == tenant_id
        )
        if support is not None:
            stmt = stmt.where(ResourceCapability.support == support)
        return (await self.db.execute(stmt)).scalar_one()

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        """Counts by support state and by capability, for the API overview."""
        by_support_stmt = (
            select(ResourceCapability.support, func.count())
            .where(ResourceCapability.tenant_id == tenant_id)
            .group_by(ResourceCapability.support)
        )
        by_support = {
            support: count
            for support, count in (await self.db.execute(by_support_stmt)).all()
        }
        by_capability_stmt = (
            select(
                ResourceCapability.capability,
                ResourceCapability.support,
                func.count(),
            )
            .where(ResourceCapability.tenant_id == tenant_id)
            .group_by(ResourceCapability.capability, ResourceCapability.support)
        )
        by_capability: dict[str, dict[str, int]] = {}
        for capability, support, count in (
            await self.db.execute(by_capability_stmt)
        ).all():
            by_capability.setdefault(capability, {})[support] = count
        return {
            "total": sum(by_support.values()),
            "by_support": by_support,
            "by_capability": by_capability,
        }
