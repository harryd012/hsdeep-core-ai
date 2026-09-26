"""
Repository for NetworkRoute persistence (spec section 20).

Keyed on (resource_id, vrf, destination, next_hop, protocol) so a repeated
collection run refreshes a route instead of appending duplicates. Tenant-scoped
at every query.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.network_route import NetworkRoute


class NetworkRouteRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def upsert(
        self,
        *,
        tenant_id: uuid.UUID,
        resource_id: uuid.UUID,
        destination: str,
        protocol: str,
        vrf: str = "-",
        next_hop: str | None = None,
        prefix_length: int | None = None,
        gateway: str | None = None,
        outgoing_interface: str | None = None,
        interface_id: uuid.UUID | None = None,
        metric: int | None = None,
        administrative_distance: int | None = None,
        priority: int | None = None,
        state: str = "unknown",
        is_best: bool = False,
        learned: bool | None = None,
        advertised: bool | None = None,
        age_seconds: float | None = None,
        provider: str | None = None,
        external_id: str | None = None,
        attributes: dict | None = None,
        seen_at: datetime | None = None,
    ) -> tuple[NetworkRoute, bool]:
        """Create or refresh one route entry.

        ``last_change_at`` is only bumped when identity-bearing fields actually
        changed, preserving useful "since when has this route looked like this"
        semantics instead of resetting it on every poll.
        """
        now = seen_at or datetime.now(timezone.utc)
        stmt = select(NetworkRoute).where(
            NetworkRoute.resource_id == resource_id,
            NetworkRoute.vrf == vrf,
            NetworkRoute.destination == destination,
            NetworkRoute.next_hop == next_hop,
            NetworkRoute.protocol == protocol,
        )
        existing = (await self.db.execute(stmt)).scalar_one_or_none()

        if existing is None:
            row = NetworkRoute(
                tenant_id=tenant_id,
                resource_id=resource_id,
                interface_id=interface_id,
                provider=provider,
                external_id=external_id,
                vrf=vrf,
                destination=destination,
                prefix_length=prefix_length,
                next_hop=next_hop,
                gateway=gateway,
                outgoing_interface=outgoing_interface,
                protocol=protocol,
                metric=metric,
                administrative_distance=administrative_distance,
                priority=priority,
                state=state,
                is_best=is_best,
                learned=learned,
                advertised=advertised,
                age_seconds=age_seconds,
                first_seen_at=now,
                last_seen_at=now,
                last_change_at=now,
                attributes=attributes or {},
            )
            self.db.add(row)
            await self.db.flush()
            await self.db.refresh(row)
            return row, True

        changed = (
            existing.state != state
            or existing.metric != metric
            or existing.administrative_distance != administrative_distance
        )
        existing.last_seen_at = now
        existing.state = state
        existing.is_best = is_best
        existing.interface_id = interface_id or existing.interface_id
        existing.gateway = gateway or existing.gateway
        existing.outgoing_interface = outgoing_interface or existing.outgoing_interface
        if metric is not None:
            existing.metric = metric
        if administrative_distance is not None:
            existing.administrative_distance = administrative_distance
        if priority is not None:
            existing.priority = priority
        if learned is not None:
            existing.learned = learned
        if advertised is not None:
            existing.advertised = advertised
        if age_seconds is not None:
            existing.age_seconds = age_seconds
        if provider is not None:
            existing.provider = provider
        if attributes:
            merged = dict(existing.attributes or {})
            merged.update(attributes)
            existing.attributes = merged
        if changed:
            existing.last_change_at = now
        await self.db.flush()
        await self.db.refresh(existing)
        return existing, False

    async def list_for_resource(
        self,
        resource_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        protocol: str | None = None,
        vrf: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[NetworkRoute]:
        stmt = select(NetworkRoute).where(
            NetworkRoute.resource_id == resource_id,
            NetworkRoute.tenant_id == tenant_id,
            NetworkRoute.is_deleted.is_(False),
        )
        if protocol is not None:
            stmt = stmt.where(NetworkRoute.protocol == protocol)
        if vrf is not None:
            stmt = stmt.where(NetworkRoute.vrf == vrf)
        stmt = (
            stmt.order_by(NetworkRoute.destination, NetworkRoute.protocol)
            .limit(limit)
            .offset(offset)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def count_for_resource(
        self, resource_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> int:
        stmt = select(func.count()).select_from(NetworkRoute).where(
            NetworkRoute.resource_id == resource_id,
            NetworkRoute.tenant_id == tenant_id,
            NetworkRoute.is_deleted.is_(False),
        )
        return (await self.db.execute(stmt)).scalar_one()

    async def count_by_protocol_for_tenant(
        self, tenant_id: uuid.UUID
    ) -> dict[str, int]:
        stmt = (
            select(NetworkRoute.protocol, func.count())
            .where(
                NetworkRoute.tenant_id == tenant_id,
                NetworkRoute.is_deleted.is_(False),
            )
            .group_by(NetworkRoute.protocol)
        )
        return {
            protocol: count for protocol, count in (await self.db.execute(stmt)).all()
        }

    async def count_for_tenant(self, tenant_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(NetworkRoute).where(
            NetworkRoute.tenant_id == tenant_id,
            NetworkRoute.is_deleted.is_(False),
        )
        return (await self.db.execute(stmt)).scalar_one()
