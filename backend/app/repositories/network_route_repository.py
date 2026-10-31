"""
Repository for NetworkRoute persistence (spec section 20).

Keyed on (resource_id, vrf, destination, next_hop, protocol) so a repeated
collection run refreshes a route instead of appending duplicates. Tenant-scoped
at every query.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.network_route import NetworkRoute


@dataclass(frozen=True)
class RouteInput:
    destination: str
    protocol: str
    prefix_length: int | None = None
    next_hop: str | None = None
    outgoing_interface: str | None = None
    interface_id: uuid.UUID | None = None
    metric: int | None = None
    administrative_distance: int | None = None
    vrf: str = "-"
    is_best: bool = True


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

    async def sync_for_resource(
        self,
        *,
        tenant_id: uuid.UUID,
        resource_id: uuid.UUID,
        provider: str | None,
        routes: list[RouteInput],
        retire_missing: bool,
        seen_at: datetime | None = None,
    ) -> tuple[int, int]:
        """Make one device's routes from ``provider`` match a polled table.

        One SELECT per device instead of one per route. Stored routes from the
        same provider that the poll did not report are marked ``withdrawn`` and
        soft-deleted, but only when ``retire_missing`` (the table was really
        read). A route that reappears is restored. Returns (upserted, retired).
        """
        now = seen_at or datetime.now(timezone.utc)
        stmt = select(NetworkRoute).where(
            NetworkRoute.tenant_id == tenant_id,
            NetworkRoute.resource_id == resource_id,
        )
        existing = {
            (row.vrf, row.destination, row.next_hop, row.protocol): row
            for row in (await self.db.execute(stmt)).scalars().all()
        }

        upserted = 0
        seen: set[tuple] = set()
        for item in routes:
            key = (item.vrf or "-", item.destination, item.next_hop, item.protocol)
            if key in seen:
                continue
            seen.add(key)
            row = existing.get(key)
            if row is None:
                row = NetworkRoute(
                    tenant_id=tenant_id,
                    resource_id=resource_id,
                    vrf=key[0],
                    destination=item.destination,
                    next_hop=item.next_hop,
                    protocol=item.protocol,
                    first_seen_at=now,
                    last_change_at=now,
                    attributes={},
                )
                self.db.add(row)
                existing[key] = row
            elif (
                row.is_deleted
                or row.metric != item.metric
                or row.administrative_distance != item.administrative_distance
            ):
                row.last_change_at = now
            row.prefix_length = item.prefix_length
            row.outgoing_interface = item.outgoing_interface
            row.interface_id = item.interface_id
            row.metric = item.metric
            row.administrative_distance = item.administrative_distance
            row.is_best = item.is_best
            row.state = "active"
            row.provider = provider
            row.last_seen_at = now
            row.is_deleted = False
            row.deleted_at = None
            upserted += 1

        retired = 0
        if retire_missing:
            for key, row in existing.items():
                if key in seen or row.is_deleted or row.provider != provider:
                    continue
                row.state = "withdrawn"
                row.is_deleted = True
                row.deleted_at = now
                row.last_change_at = now
                retired += 1

        await self.db.flush()
        return upserted, retired

    async def list_for_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        resource_ids: list[uuid.UUID] | None = None,
    ) -> list[NetworkRoute]:
        stmt = select(NetworkRoute).where(
            NetworkRoute.tenant_id == tenant_id,
            NetworkRoute.is_deleted.is_(False),
        )
        if resource_ids is not None:
            if not resource_ids:
                return []
            stmt = stmt.where(NetworkRoute.resource_id.in_(resource_ids))
        stmt = stmt.order_by(NetworkRoute.resource_id, NetworkRoute.destination)
        return list((await self.db.execute(stmt)).scalars().all())
