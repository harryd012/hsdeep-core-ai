"""
Repository Pattern: all direct SQLAlchemy access for TopologyLink lives here.

Tenant-scoped at every query; idempotent-discovery semantics respect the
(tenant_id, device_a_id, device_b_id, name) unique constraint — re-discovering
an existing bearer refreshes it instead of creating a duplicate.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.topology_link import TopologyLink
from app.services.topology_standard import normalize_link_order


class TopologyLinkRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _normalize(
        self, device_a_id: uuid.UUID, device_b_id: uuid.UUID,
    ) -> tuple[uuid.UUID, uuid.UUID]:
        return normalize_link_order(device_a_id, device_b_id)

    async def get(
        self, link_id: uuid.UUID, tenant_id: uuid.UUID,
    ) -> TopologyLink | None:
        stmt = select(TopologyLink).where(
            TopologyLink.id == link_id,
            TopologyLink.tenant_id == tenant_id,
            TopologyLink.is_deleted.is_(False),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def upsert(
        self,
        tenant_id: uuid.UUID,
        *,
        device_a_id: uuid.UUID,
        device_b_id: uuid.UUID,
        name: str,
        link_type: str = "other",
        source_interface_id: uuid.UUID | None = None,
        dest_interface_id: uuid.UUID | None = None,
        provider: str | None = None,
        circuit_id: str | None = None,
        external_id: str | None = None,
        bandwidth_mbps: float | None = None,
        latency_ms: float | None = None,
        packet_loss_pct: float | None = None,
        utilization_pct: float | None = None,
        admin_status: str = "up",
        oper_status: str = "up",
        routing_protocols: list | None = None,
        ospf: dict | None = None,
        bgp: dict | None = None,
        vrfs: list | None = None,
        vpn_name: str | None = None,
        vpn_type: str | None = None,
        redundancy_group: str | None = None,
        is_redundant: bool | None = None,
        discovered_by: str = "manual",
        attributes: dict | None = None,
    ) -> tuple[TopologyLink, bool]:
        a, b = self._normalize(device_a_id, device_b_id)
        stmt = select(TopologyLink).where(
            TopologyLink.tenant_id == tenant_id,
            TopologyLink.device_a_id == a,
            TopologyLink.device_b_id == b,
            TopologyLink.name == name,
            TopologyLink.is_deleted.is_(False),
        )
        existing = (await self.db.execute(stmt)).scalar_one_or_none()
        now = datetime.now(timezone.utc)

        if existing is not None:
            existing.link_type = link_type or existing.link_type
            existing.latency_ms = latency_ms if latency_ms is not None else existing.latency_ms
            existing.packet_loss_pct = (
                packet_loss_pct if packet_loss_pct is not None else existing.packet_loss_pct
            )
            existing.utilization_pct = (
                utilization_pct if utilization_pct is not None else existing.utilization_pct
            )
            if oper_status != existing.oper_status:
                existing.last_state_change_at = now
            existing.oper_status = oper_status
            existing.admin_status = admin_status
            if bandwidth_mbps is not None:
                existing.bandwidth_mbps = bandwidth_mbps
            if provider is not None:
                existing.provider = provider
            if circuit_id is not None:
                existing.circuit_id = circuit_id
            if external_id is not None:
                existing.external_id = external_id
            if routing_protocols is not None:
                existing.routing_protocols = routing_protocols
            if ospf is not None:
                existing.ospf = ospf
            if bgp is not None:
                existing.bgp = bgp
            if vrfs is not None:
                existing.vrfs = vrfs
            if vpn_name is not None:
                existing.vpn_name = vpn_name
            if vpn_type is not None:
                existing.vpn_type = vpn_type
            if redundancy_group is not None:
                existing.redundancy_group = redundancy_group
            if is_redundant is not None:
                existing.is_redundant = is_redundant
            if attributes is not None:
                merged = dict(existing.attributes or {})
                merged.update(attributes)
                existing.attributes = merged
            await self.db.flush()
            await self.db.refresh(existing)
            return existing, False

        link = TopologyLink(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            device_a_id=a,
            device_b_id=b,
            source_interface_id=source_interface_id,
            dest_interface_id=dest_interface_id,
            name=name,
            link_type=link_type,
            discovered_by=discovered_by,
            provider=provider,
            circuit_id=circuit_id,
            external_id=external_id,
            bandwidth_mbps=bandwidth_mbps,
            latency_ms=latency_ms,
            packet_loss_pct=packet_loss_pct,
            utilization_pct=utilization_pct,
            admin_status=admin_status,
            oper_status=oper_status,
            routing_protocols=routing_protocols,
            ospf=ospf,
            bgp=bgp,
            vrfs=vrfs,
            vpn_name=vpn_name,
            vpn_type=vpn_type,
            redundancy_group=redundancy_group,
            is_redundant=bool(is_redundant),
            attributes=attributes or {},
        )
        self.db.add(link)
        await self.db.flush()
        await self.db.refresh(link)
        return link, True

    async def update(self, link: TopologyLink, **fields) -> TopologyLink:
        for key, value in fields.items():
            if value is not None and hasattr(link, key):
                setattr(link, key, value)
        await self.db.flush()
        await self.db.refresh(link)
        return link

    async def delete(self, link: TopologyLink) -> None:
        link.is_deleted = True
        link.deleted_at = datetime.now(timezone.utc)
        await self.db.flush()

    async def list(
        self, tenant_id: uuid.UUID, *,
        limit: int = 100, offset: int = 0,
        link_type: str | None = None,
        oper_status: str | None = None,
        device_id: uuid.UUID | None = None,
    ) -> tuple[list[TopologyLink], int]:
        stmt = select(TopologyLink).where(
            TopologyLink.tenant_id == tenant_id,
            TopologyLink.is_deleted.is_(False),
        )
        cstmt = select(func.count()).where(
            TopologyLink.tenant_id == tenant_id,
            TopologyLink.is_deleted.is_(False),
        )
        if link_type:
            stmt = stmt.where(TopologyLink.link_type == link_type)
            cstmt = cstmt.where(TopologyLink.link_type == link_type)
        if oper_status:
            stmt = stmt.where(TopologyLink.oper_status == oper_status)
            cstmt = cstmt.where(TopologyLink.oper_status == oper_status)
        if device_id:
            stmt = stmt.where(
                (TopologyLink.device_a_id == device_id)
                | (TopologyLink.device_b_id == device_id)
            )
            cstmt = cstmt.where(
                (TopologyLink.device_a_id == device_id)
                | (TopologyLink.device_b_id == device_id)
            )
        total = (await self.db.execute(cstmt)).scalar_one()
        stmt = stmt.order_by(TopologyLink.created_at.desc()).limit(limit).offset(offset)
        rows = (await self.db.execute(stmt)).scalars().all()
        return list(rows), int(total)

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        stmt = select(TopologyLink).where(
            TopologyLink.tenant_id == tenant_id,
            TopologyLink.is_deleted.is_(False),
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        by_type: dict[str, int] = {}
        by_status: dict[str, int] = {}
        up = 0
        for link in rows:
            by_type[link.link_type] = by_type.get(link.link_type, 0) + 1
            by_status[link.oper_status] = by_status.get(link.oper_status, 0) + 1
            if link.oper_status == "up":
                up += 1
        return {
            "total": len(rows),
            "by_type": by_type,
            "by_status": by_status,
            "up": up,
            "down": len(rows) - up,
        }

