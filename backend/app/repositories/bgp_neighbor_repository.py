"""
Repository for BgpNeighbor persistence (spec section 20).

Keyed on (resource_id, vrf, neighbor_ip). Tenant-scoped at every query.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bgp_neighbor import BgpNeighbor


class BgpNeighborRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def upsert(
        self,
        *,
        tenant_id: uuid.UUID,
        resource_id: uuid.UUID,
        neighbor_ip: str,
        vrf: str = "-",
        state: str = "unknown",
        local_ip: str | None = None,
        remote_as: str | None = None,
        local_as: str | None = None,
        description: str | None = None,
        admin_status: str = "unknown",
        uptime_seconds: float | None = None,
        prefixes_received: int | None = None,
        prefixes_advertised: int | None = None,
        last_state_change_at: datetime | None = None,
        provider: str | None = None,
        attributes: dict | None = None,
        seen_at: datetime | None = None,
    ) -> tuple[BgpNeighbor, bool]:
        """Create or refresh one BGP session.

        ``last_state_change_at`` only moves when the FSM state actually
        changes, so a flapping-then-stable session keeps a meaningful "up
        since" answer instead of being reset on every poll.
        """
        now = seen_at or datetime.now(timezone.utc)
        stmt = select(BgpNeighbor).where(
            BgpNeighbor.resource_id == resource_id,
            BgpNeighbor.vrf == vrf,
            BgpNeighbor.neighbor_ip == neighbor_ip,
        )
        existing = (await self.db.execute(stmt)).scalar_one_or_none()

        if existing is None:
            row = BgpNeighbor(
                tenant_id=tenant_id,
                resource_id=resource_id,
                provider=provider,
                vrf=vrf,
                neighbor_ip=neighbor_ip,
                local_ip=local_ip,
                remote_as=remote_as,
                local_as=local_as,
                description=description,
                state=state,
                admin_status=admin_status,
                uptime_seconds=uptime_seconds,
                prefixes_received=prefixes_received,
                prefixes_advertised=prefixes_advertised,
                last_state_change_at=last_state_change_at or now,
                first_seen_at=now,
                last_seen_at=now,
                attributes=attributes or {},
            )
            self.db.add(row)
            await self.db.flush()
            await self.db.refresh(row)
            return row, True

        state_changed = existing.state != state
        existing.last_seen_at = now
        existing.state = state
        existing.admin_status = admin_status
        existing.local_ip = local_ip or existing.local_ip
        existing.remote_as = remote_as or existing.remote_as
        existing.local_as = local_as or existing.local_as
        existing.description = description or existing.description
        if uptime_seconds is not None:
            existing.uptime_seconds = uptime_seconds
        if prefixes_received is not None:
            existing.prefixes_received = prefixes_received
        if prefixes_advertised is not None:
            existing.prefixes_advertised = prefixes_advertised
        if provider is not None:
            existing.provider = provider
        if attributes:
            merged = dict(existing.attributes or {})
            merged.update(attributes)
            existing.attributes = merged
        if state_changed:
            existing.last_state_change_at = last_state_change_at or now
        await self.db.flush()
        await self.db.refresh(existing)
        return existing, False

    async def list_for_resource(
        self,
        resource_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        vrf: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[BgpNeighbor]:
        stmt = select(BgpNeighbor).where(
            BgpNeighbor.resource_id == resource_id,
            BgpNeighbor.tenant_id == tenant_id,
            BgpNeighbor.is_deleted.is_(False),
        )
        if vrf is not None:
            stmt = stmt.where(BgpNeighbor.vrf == vrf)
        stmt = stmt.order_by(BgpNeighbor.neighbor_ip).limit(limit).offset(offset)
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_for_tenant(
        self, tenant_id: uuid.UUID, *, limit: int = 10_000
    ) -> list[BgpNeighbor]:
        """Every BGP neighbour recorded for a tenant, in ONE query.

        Added for Phase 9.6 relationship materialisation, which converts recorded
        peering evidence into edges for the whole tenant at once. Mirrors
        ``list_for_resource`` above; soft-deleted rows are excluded.
        """
        stmt = (
            select(BgpNeighbor)
            .where(
                BgpNeighbor.tenant_id == tenant_id,
                BgpNeighbor.is_deleted.is_(False),
            )
            .order_by(BgpNeighbor.resource_id, BgpNeighbor.neighbor_ip)
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def count_for_resource(
        self, resource_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> int:
        stmt = select(func.count()).select_from(BgpNeighbor).where(
            BgpNeighbor.resource_id == resource_id,
            BgpNeighbor.tenant_id == tenant_id,
            BgpNeighbor.is_deleted.is_(False),
        )
        return (await self.db.execute(stmt)).scalar_one()

    async def count_by_state_for_tenant(self, tenant_id: uuid.UUID) -> dict[str, int]:
        stmt = (
            select(BgpNeighbor.state, func.count())
            .where(
                BgpNeighbor.tenant_id == tenant_id,
                BgpNeighbor.is_deleted.is_(False),
            )
            .group_by(BgpNeighbor.state)
        )
        return {state: count for state, count in (await self.db.execute(stmt)).all()}
