"""
Firewall gateway API (docs/architecture/firewall-gateway.md).

Read-only and tenant-scoped. Every firewall the tenant has is listed, including
ones no adapter has reported firewall state for yet: their fields are null and
`evidence` says why, so missing data never looks healthy.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id, require_permission
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.models.device import Device
from app.models.firewall import FirewallStatus, VpnTunnel
from app.services.operational_intelligence_service import DEVICE_FRESHNESS_WINDOW_SECONDS

router = APIRouter(
    prefix="/firewalls",
    tags=["firewalls"],
    dependencies=[Depends(require_active_subscription)],
)


class VpnTunnelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    tunnel_type: str
    remote_gateway: str | None
    status: str
    ever_up: bool
    phase2_up: int | None
    phase2_total: int | None
    last_change_at: datetime | None
    last_seen_at: datetime


class LicenceOut(BaseModel):
    feature: str
    expires_at: str | None
    status: str


class FirewallOut(BaseModel):
    device_id: uuid.UUID
    name: str
    vendor: str | None
    model: str | None
    ip_address: str | None
    device_status: str
    #: fresh | stale | none: whether the firewall tables were reported within
    #: the platform's freshness window.
    evidence: str
    observed_at: datetime | None
    table_status: dict[str, str]
    ha_mode: str | None = None
    ha_role: str | None = None
    ha_sync: str | None = None
    ha_peer_serial: str | None = None
    session_count: int | None = None
    session_limit: int | None = None
    cpu_percent: float | None = None
    memory_percent: float | None = None
    firmware_version: str | None = None
    licences: list[LicenceOut] | None = None
    tunnels_up: int | None = None
    tunnels_total: int | None = None


class FirewallDetailOut(FirewallOut):
    tunnels: list[VpnTunnelOut]


def _evidence(observed_at: datetime | None, now: datetime) -> str:
    if observed_at is None:
        return "none"
    return "fresh" if (now - observed_at).total_seconds() <= DEVICE_FRESHNESS_WINDOW_SECONDS else "stale"


def _out(device: Device, row: FirewallStatus | None, tunnels: list[VpnTunnel], now: datetime) -> dict:
    data: dict = {
        "device_id": device.id, "name": device.name, "vendor": device.vendor, "model": device.model,
        "ip_address": device.ip_address, "device_status": device.status,
        "evidence": _evidence(row.observed_at if row else None, now),
        "observed_at": row.observed_at if row else None,
        "table_status": dict(row.table_status or {}) if row else {},
    }
    if row is not None:
        for f in ("ha_mode", "ha_role", "ha_sync", "ha_peer_serial", "session_count", "session_limit",
                  "cpu_percent", "memory_percent", "firmware_version", "licences"):
            data[f] = getattr(row, f)
    # Tunnel counts only when a VPN table has ever been read for this device.
    if tunnels or (row is not None and "vpn" in (row.table_status or {})):
        data["tunnels_up"] = sum(1 for t in tunnels if t.status == "up")
        data["tunnels_total"] = len(tunnels)
    return data


async def _devices(db: AsyncSession, tenant_id: uuid.UUID, device_id: uuid.UUID | None = None) -> list[Device]:
    stmt = select(Device).where(
        Device.tenant_id == tenant_id, Device.device_type == "firewall", Device.is_deleted.is_(False)
    )
    if device_id is not None:
        stmt = stmt.where(Device.id == device_id)
    return list((await db.execute(stmt.order_by(Device.name))).scalars())


@router.get(
    "", response_model=list[FirewallOut],
    dependencies=[Depends(require_permission("devices.read"))],
)
async def list_firewalls(tenant_id: uuid.UUID = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    devices = await _devices(db, tenant_id)
    ids = [d.id for d in devices]
    if not ids:
        return []
    rows = {
        r.device_id: r
        for r in (await db.execute(select(FirewallStatus).where(
            FirewallStatus.tenant_id == tenant_id, FirewallStatus.device_id.in_(ids)
        ))).scalars()
    }
    tunnels: dict[uuid.UUID, list[VpnTunnel]] = {}
    for t in (await db.execute(select(VpnTunnel).where(
        VpnTunnel.tenant_id == tenant_id, VpnTunnel.device_id.in_(ids)
    ))).scalars():
        tunnels.setdefault(t.device_id, []).append(t)
    now = datetime.now(timezone.utc)
    return [_out(d, rows.get(d.id), tunnels.get(d.id, []), now) for d in devices]


@router.get(
    "/{device_id}", response_model=FirewallDetailOut,
    dependencies=[Depends(require_permission("devices.read"))],
)
async def get_firewall(
    device_id: uuid.UUID, tenant_id: uuid.UUID = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    devices = await _devices(db, tenant_id, device_id)
    if not devices:
        raise HTTPException(status_code=404, detail="Firewall not found")
    row = (await db.execute(select(FirewallStatus).where(
        FirewallStatus.tenant_id == tenant_id, FirewallStatus.device_id == device_id
    ))).scalar_one_or_none()
    tunnels = list((await db.execute(select(VpnTunnel).where(
        VpnTunnel.tenant_id == tenant_id, VpnTunnel.device_id == device_id
    ).order_by(VpnTunnel.name))).scalars())
    data = _out(devices[0], row, tunnels, datetime.now(timezone.utc))
    data["tunnels"] = tunnels
    return data
