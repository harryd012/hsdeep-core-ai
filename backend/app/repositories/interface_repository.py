"""
Repository for Interface persistence. Interfaces are keyed on
(device_id, external_id) per the model's UniqueConstraint - a given device
never reports the same interface twice, so upsert() resolves identity from
that pair rather than the primary key.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.interface import Interface


class InterfaceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_external_id(
        self, device_id: uuid.UUID, external_id: str
    ) -> Interface | None:
        stmt = select(Interface).where(
            Interface.device_id == device_id,
            Interface.external_id == external_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        external_id: str,
        name: str,
        monitoring_source_id: uuid.UUID | None = None,
        mac_address: str | None = None,
        admin_status: str = "unknown",
        oper_status: str = "unknown",
        speed_mbps: float | None = None,
        in_octets: float | None = None,
        out_octets: float | None = None,
        in_errors: float | None = None,
        out_errors: float | None = None,
        # -- universal interface model (additive, all optional) --------------
        description: str | None = None,
        interface_type: str | None = None,
        vlan_id: int | None = None,
        speed_bps: float | None = None,
        rx_bps: float | None = None,
        tx_bps: float | None = None,
        utilization_pct: float | None = None,
        discards_in: float | None = None,
        discards_out: float | None = None,
        packet_loss_pct: float | None = None,
        latency_ms: float | None = None,
        jitter_ms: float | None = None,
        telemetry_seen: bool = False,
    ) -> tuple[Interface, bool]:
        """Create or refresh an interface from a collector reading.

        Returns (interface, created) so callers can report accurate
        created/updated counts back to the caller.

        `last_status_change_at` is only bumped to now() when `oper_status`
        actually changes from the existing row's value - this preserves
        meaningful "since when has this interface been in this state"
        semantics instead of resetting it on every sync.
        """
        existing = await self.get_by_external_id(device_id, external_id)
        now = datetime.now(timezone.utc)

        if existing is None:
            interface = Interface(
                tenant_id=tenant_id,
                device_id=device_id,
                monitoring_source_id=monitoring_source_id,
                external_id=external_id,
                name=name,
                mac_address=mac_address,
                admin_status=admin_status,
                oper_status=oper_status,
                speed_mbps=speed_mbps,
                in_octets=in_octets,
                out_octets=out_octets,
                in_errors=in_errors,
                out_errors=out_errors,
                last_status_change_at=now,
                description=description,
                interface_type=interface_type or "unknown",
                vlan_id=vlan_id,
                speed_bps=speed_bps,
                rx_bps=rx_bps,
                tx_bps=tx_bps,
                utilization_pct=utilization_pct,
                discards_in=discards_in,
                discards_out=discards_out,
                packet_loss_pct=packet_loss_pct,
                latency_ms=latency_ms,
                jitter_ms=jitter_ms,
                last_telemetry_at=now if telemetry_seen else None,
            )
            self.db.add(interface)
            await self.db.flush()
            await self.db.refresh(interface)
            return interface, True

        oper_status_changed = existing.oper_status != oper_status

        existing.monitoring_source_id = monitoring_source_id
        existing.name = name
        existing.mac_address = mac_address
        existing.admin_status = admin_status
        existing.oper_status = oper_status
        existing.speed_mbps = speed_mbps
        existing.in_octets = in_octets
        existing.out_octets = out_octets
        existing.in_errors = in_errors
        existing.out_errors = out_errors
        # Universal interface model: enrich-only for optional facts so a
        # source that does not carry them cannot blank out what another did.
        if description is not None:
            existing.description = description
        if interface_type is not None:
            existing.interface_type = interface_type
        if vlan_id is not None:
            existing.vlan_id = vlan_id
        if speed_bps is not None:
            existing.speed_bps = speed_bps
        if rx_bps is not None:
            existing.rx_bps = rx_bps
        if tx_bps is not None:
            existing.tx_bps = tx_bps
        if utilization_pct is not None:
            existing.utilization_pct = utilization_pct
        if discards_in is not None:
            existing.discards_in = discards_in
        if discards_out is not None:
            existing.discards_out = discards_out
        if packet_loss_pct is not None:
            existing.packet_loss_pct = packet_loss_pct
        if latency_ms is not None:
            existing.latency_ms = latency_ms
        if jitter_ms is not None:
            existing.jitter_ms = jitter_ms
        if telemetry_seen:
            existing.last_telemetry_at = now
        if oper_status_changed:
            existing.last_status_change_at = now
        await self.db.flush()
        await self.db.refresh(existing)
        return existing, False

    async def list_for_device(self, device_id: uuid.UUID) -> list[Interface]:
        stmt = (
            select(Interface)
            .where(Interface.device_id == device_id, Interface.is_deleted.is_(False))
            .order_by(Interface.name)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_for_devices(
        self, tenant_id: uuid.UUID, device_ids: list[uuid.UUID]
    ) -> list[Interface]:
        """Every interface for a SET of devices, in ONE query.

        Added for the live topology read model, which needs per-device interface
        state (oper status, speed, rx/tx bps) for a whole site at once. Calling
        `list_for_device()` per node would be an N+1 - the same reason
        `counts_by_device()` exists above.

        Tenant-scoped, soft-delete aware, and an empty id set returns [] without
        touching the database.
        """
        if not device_ids:
            return []
        stmt = (
            select(Interface)
            .where(
                Interface.tenant_id == tenant_id,
                Interface.device_id.in_(device_ids),
                Interface.is_deleted.is_(False),
            )
            .order_by(Interface.device_id, Interface.name)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count_for_tenant(self, tenant_id: uuid.UUID) -> int:
        """Tenant-wide interface count, for the canonical infrastructure overview.

        Named to match `SensorRepository.count_for_tenant`. A count of 0 is a
        real answer ("interfaces have not been collected"), not a placeholder -
        the overview reports it as-is rather than omitting the row.
        """
        stmt = select(func.count()).select_from(Interface).where(
            Interface.tenant_id == tenant_id,
            Interface.is_deleted.is_(False),
        )
        return (await self.db.execute(stmt)).scalar_one()

    async def counts_by_device(
        self, tenant_id: uuid.UUID
    ) -> dict[uuid.UUID, int]:
        """Interface count per device, in ONE query.

        Added for the topology graph / device drawer, which shows a per-device
        interface count. `list_for_device()` per node would be an N+1. Mirrors
        `count_for_tenant()` above.

        A device with no interfaces is simply absent from the mapping - a real
        0, not a placeholder.
        """
        stmt = (
            select(Interface.device_id, func.count())
            .where(
                Interface.tenant_id == tenant_id,
                Interface.is_deleted.is_(False),
            )
            .group_by(Interface.device_id)
        )
        rows = (await self.db.execute(stmt)).all()
        return {device_id: count for device_id, count in rows}

