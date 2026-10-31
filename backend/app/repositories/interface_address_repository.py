"""
Repository for InterfaceAddress persistence (L3 topology).

Writes are a per-device sync: one SELECT for the device's stored addresses, then
insert / refresh / retire in memory, so a poll costs O(1) queries per device
instead of one per address. Tenant-scoped at every query.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ip_addressing import parse_interface_address
from app.models.interface_address import InterfaceAddress


@dataclass(frozen=True)
class AddressInput:
    address: str
    prefix_length: int
    vrf: str = "-"
    interface_id: uuid.UUID | None = None
    interface_name: str | None = None
    is_primary: bool = True


@dataclass(frozen=True)
class SyncResult:
    upserted: int = 0
    retired: int = 0
    rejected: int = 0


class InterfaceAddressRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def sync_for_device(
        self,
        *,
        tenant_id: uuid.UUID,
        device_id: uuid.UUID,
        addresses: list[AddressInput],
        monitoring_source_id: uuid.UUID | None = None,
        provider: str | None = None,
        retire_missing: bool,
        seen_at: datetime | None = None,
    ) -> SyncResult:
        """Make the device's active addresses match ``addresses``.

        ``retire_missing`` soft-deletes stored rows (from this source) that the
        poll did not report; pass it only when the address table was actually
        read, so a failed read never wipes earlier data. A re-reported address
        that had been retired comes back to life.
        """
        now = seen_at or datetime.now(timezone.utc)
        stmt = select(InterfaceAddress).where(
            InterfaceAddress.tenant_id == tenant_id,
            InterfaceAddress.device_id == device_id,
        )
        existing = {
            (row.vrf, row.address): row
            for row in (await self.db.execute(stmt)).scalars().all()
        }

        upserted = rejected = 0
        seen: set[tuple[str, str]] = set()
        for item in addresses:
            parsed = parse_interface_address(item.address, item.prefix_length)
            if parsed is None:
                rejected += 1
                continue
            key = (item.vrf or "-", parsed.address)
            if key in seen:
                continue  # last duplicate in one poll is ignored, first wins
            seen.add(key)
            row = existing.get(key)
            if row is None:
                row = InterfaceAddress(
                    tenant_id=tenant_id,
                    device_id=device_id,
                    vrf=key[0],
                    address=parsed.address,
                    first_seen_at=now,
                )
                self.db.add(row)
                existing[key] = row
            row.family = parsed.family
            row.prefix_length = parsed.prefix_length
            row.network = parsed.network
            row.interface_id = item.interface_id
            row.interface_name = item.interface_name
            row.is_primary = item.is_primary
            row.monitoring_source_id = monitoring_source_id
            row.provider = provider
            row.last_seen_at = now
            row.is_deleted = False
            row.deleted_at = None
            upserted += 1

        retired = 0
        if retire_missing:
            for key, row in existing.items():
                if key in seen or row.is_deleted:
                    continue
                if row.monitoring_source_id != monitoring_source_id:
                    continue  # another source owns it
                row.is_deleted = True
                row.deleted_at = now
                retired += 1

        await self.db.flush()
        return SyncResult(upserted=upserted, retired=retired, rejected=rejected)

    async def list_for_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        device_ids: list[uuid.UUID] | None = None,
    ) -> list[InterfaceAddress]:
        stmt = select(InterfaceAddress).where(
            InterfaceAddress.tenant_id == tenant_id,
            InterfaceAddress.is_deleted.is_(False),
        )
        if device_ids is not None:
            if not device_ids:
                return []
            stmt = stmt.where(InterfaceAddress.device_id.in_(device_ids))
        stmt = stmt.order_by(InterfaceAddress.device_id, InterfaceAddress.address)
        return list((await self.db.execute(stmt)).scalars().all())
