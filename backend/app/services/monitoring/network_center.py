"""
Network Monitoring Center (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P4 slice 1).

Tenant-scoped reads over `interfaces` (latest state and derived rates), the
per-interface history the saver records in `metrics` (rx / tx bits per second,
utilisation, error rates) and `vpn_tunnels`. Nothing is computed that a
collector did not measure: an interface with one counter sample has no rate,
and a rate older than STALE_AFTER is reported as stale rather than current.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, and_, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.firewall import VpnTunnel
from app.models.interface import Interface
from app.models.metric import Metric
from app.services.monitoring.sensor_service import bucket_seconds

#: A derived rate older than this is not presented as the link's current load.
STALE_AFTER = timedelta(minutes=30)
TOP_N = 10
HISTORY_CHANNELS = ("rx_bps", "tx_bps", "interface_utilization_pct", "errors_in", "errors_out")
SORTS = ("name", "utilization", "throughput", "status")
STATUS_FILTERS = ("up", "down", "unexpected_down", "admin_down")


class InterfaceNotFound(Exception):
    pass


def _iface_row(i: Interface, device_name: str, device_type: str | None, now: datetime) -> dict:
    fresh = i.last_telemetry_at is not None and now - i.last_telemetry_at <= STALE_AFTER
    throughput = None if i.rx_bps is None and i.tx_bps is None else (i.rx_bps or 0.0) + (i.tx_bps or 0.0)
    return {
        "id": i.id, "device_id": i.device_id, "device_name": device_name, "device_type": device_type,
        "name": i.name, "description": i.description, "interface_type": i.interface_type,
        "admin_status": i.admin_status, "oper_status": i.oper_status, "speed_mbps": i.speed_mbps,
        "rx_bps": i.rx_bps, "tx_bps": i.tx_bps, "throughput_bps": throughput,
        "utilization_pct": i.utilization_pct, "in_errors": i.in_errors, "out_errors": i.out_errors,
        "last_telemetry_at": i.last_telemetry_at, "last_status_change_at": i.last_status_change_at,
        "fresh": fresh,
    }


def _unexpected_down():
    """Enabled (admin up) but operationally down: the interfaces worth a look."""
    return and_(Interface.admin_status == "up", Interface.oper_status == "down")


class NetworkCenterService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _base(self, tenant_id: uuid.UUID):
        return (
            select(Interface, Device.name, Device.device_type)
            .join(Device, Device.id == Interface.device_id)
            .where(Interface.tenant_id == tenant_id, Interface.is_deleted.is_(False), Device.is_deleted.is_(False))
        )

    async def overview(self, tenant_id: uuid.UUID, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        fresh_cutoff = now - STALE_AFTER
        totals = (
            await self.db.execute(
                select(
                    func.count(),
                    func.count().filter(Interface.oper_status == "up"),
                    func.count().filter(Interface.oper_status == "down"),
                    func.count().filter(_unexpected_down()),
                    func.count().filter(Interface.admin_status == "down"),
                    func.count().filter(Interface.last_telemetry_at >= fresh_cutoff),
                    func.count().filter(and_(or_(Interface.rx_bps.is_not(None), Interface.tx_bps.is_not(None)),
                                             Interface.last_telemetry_at >= fresh_cutoff)),
                    func.count(func.distinct(Interface.device_id)),
                    func.max(Interface.last_telemetry_at),
                )
                .join(Device, Device.id == Interface.device_id)
                .where(Interface.tenant_id == tenant_id, Interface.is_deleted.is_(False), Device.is_deleted.is_(False))
            )
        ).one()
        total, up, down, unexpected, admin_down, fresh, measured, devices, last = totals

        fresh_rates = and_(Interface.last_telemetry_at >= fresh_cutoff)
        top_util = (
            await self.db.execute(
                self._base(tenant_id).where(fresh_rates, Interface.utilization_pct.is_not(None))
                .order_by(Interface.utilization_pct.desc()).limit(TOP_N)
            )
        ).all()
        throughput = cast(func.coalesce(Interface.rx_bps, 0.0) + func.coalesce(Interface.tx_bps, 0.0), Float)
        top_tp = (
            await self.db.execute(
                self._base(tenant_id).where(fresh_rates, or_(Interface.rx_bps.is_not(None), Interface.tx_bps.is_not(None)))
                .order_by(throughput.desc()).limit(TOP_N)
            )
        ).all()
        down_rows = (
            await self.db.execute(
                self._base(tenant_id).where(_unexpected_down())
                .order_by(Interface.last_status_change_at.desc().nulls_last(), Device.name, Interface.name).limit(50)
            )
        ).all()
        errors = (
            await self.db.execute(
                select(Metric.interface_id, func.sum(Metric.value))
                .where(
                    Metric.tenant_id == tenant_id, Metric.interface_id.is_not(None),
                    Metric.metric_name.in_(("errors_in", "errors_out")),
                    Metric.recorded_at >= now - timedelta(hours=1), Metric.value > 0,
                )
                .group_by(Metric.interface_id)
                .order_by(func.sum(Metric.value).desc())
                .limit(TOP_N)
            )
        ).all()
        error_rows = []
        if errors:
            by_id = {
                i.id: (i, n, t)
                for i, n, t in (await self.db.execute(self._base(tenant_id).where(Interface.id.in_([e[0] for e in errors])))).all()
            }
            for interface_id, rate_sum in errors:
                if interface_id in by_id:
                    i, n, t = by_id[interface_id]
                    error_rows.append({**_iface_row(i, n, t, now), "errors_per_s_1h": float(rate_sum)})

        tunnels = (
            await self.db.execute(
                select(VpnTunnel, Device.name)
                .join(Device, Device.id == VpnTunnel.device_id)
                .where(VpnTunnel.tenant_id == tenant_id, Device.is_deleted.is_(False))
                .order_by(case((VpnTunnel.status == "down", 0), else_=1), Device.name, VpnTunnel.name)
            )
        ).all()

        return {
            "as_of": now,
            "interfaces": {
                "total": total, "up": up, "down": down, "unexpected_down": unexpected, "admin_down": admin_down,
                "fresh": fresh, "measured": measured, "devices": devices, "last_telemetry_at": last,
            },
            "top_utilization": [_iface_row(i, n, t, now) for i, n, t in top_util],
            "top_throughput": [_iface_row(i, n, t, now) for i, n, t in top_tp],
            "unexpected_down": [_iface_row(i, n, t, now) for i, n, t in down_rows],
            "top_errors": error_rows,
            "tunnels": {
                "total": len(tunnels),
                "up": sum(1 for t, _ in tunnels if t.status == "up"),
                "down": sum(1 for t, _ in tunnels if t.status == "down"),
                "items": [
                    {
                        "id": t.id, "device_id": t.device_id, "device_name": n, "name": t.name,
                        "tunnel_type": t.tunnel_type, "remote_gateway": t.remote_gateway, "status": t.status,
                        "phase2_up": t.phase2_up, "phase2_total": t.phase2_total, "bytes_in": t.bytes_in,
                        "bytes_out": t.bytes_out, "last_change_at": t.last_change_at, "last_seen_at": t.last_seen_at,
                    }
                    for t, n in tunnels[:200]
                ],
            },
        }

    async def interfaces(
        self, tenant_id: uuid.UUID, *, status: str | None, device_id: uuid.UUID | None, q: str | None, sort: str,
        limit: int, offset: int, now: datetime | None = None,
    ) -> tuple[list[dict], int]:
        now = now or datetime.now(timezone.utc)
        query = self._base(tenant_id)
        if status == "up":
            query = query.where(Interface.oper_status == "up")
        elif status == "down":
            query = query.where(Interface.oper_status == "down")
        elif status == "unexpected_down":
            query = query.where(_unexpected_down())
        elif status == "admin_down":
            query = query.where(Interface.admin_status == "down")
        if device_id is not None:
            query = query.where(Interface.device_id == device_id)
        if q:
            like = f"%{q.strip().replace('%', '').replace('_', '')}%"
            query = query.where(or_(Interface.name.ilike(like), Interface.description.ilike(like), Device.name.ilike(like)))
        total = (await self.db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
        throughput = func.coalesce(Interface.rx_bps, 0.0) + func.coalesce(Interface.tx_bps, 0.0)
        order = {
            "name": (Device.name, Interface.name),
            "utilization": (Interface.utilization_pct.desc().nulls_last(), Device.name, Interface.name),
            "throughput": (throughput.desc(), Device.name, Interface.name),
            "status": (case((_unexpected_down(), 0), (Interface.oper_status == "down", 1), else_=2), Device.name, Interface.name),
        }[sort]
        rows = (await self.db.execute(query.order_by(*order).limit(limit).offset(offset))).all()
        return [_iface_row(i, n, t, now) for i, n, t in rows], total

    async def interface(self, tenant_id: uuid.UUID, interface_id: uuid.UUID, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        row = (await self.db.execute(self._base(tenant_id).where(Interface.id == interface_id))).first()
        if row is None:
            raise InterfaceNotFound()
        i, n, t = row
        channels = (
            await self.db.execute(
                select(Metric.metric_name, func.count(), func.max(Metric.recorded_at))
                .where(Metric.tenant_id == tenant_id, Metric.interface_id == interface_id, Metric.metric_name.in_(HISTORY_CHANNELS))
                .group_by(Metric.metric_name)
            )
        ).all()
        return {
            **_iface_row(i, n, t, now),
            "mac_address": i.mac_address, "vlan_id": i.vlan_id,
            "history": {name: {"samples": count, "last_recorded_at": last} for name, count, last in channels},
        }

    async def history(
        self, tenant_id: uuid.UUID, interface_id: uuid.UUID, *, hours: int, now: datetime | None = None,
    ) -> dict:
        """Every history channel bucketed over the window (avg / max per bucket)."""
        now = now or datetime.now(timezone.utc)
        exists = (
            await self.db.execute(
                select(Interface.id).where(Interface.id == interface_id, Interface.tenant_id == tenant_id, Interface.is_deleted.is_(False))
            )
        ).scalar_one_or_none()
        if exists is None:
            raise InterfaceNotFound()
        width = bucket_seconds(hours)
        bucket = func.to_timestamp(func.floor(func.extract("epoch", Metric.recorded_at) / width) * width)
        rows = (
            await self.db.execute(
                select(Metric.metric_name, bucket, func.avg(Metric.value), func.max(Metric.value))
                .where(
                    Metric.tenant_id == tenant_id, Metric.interface_id == interface_id,
                    Metric.metric_name.in_(HISTORY_CHANNELS), Metric.recorded_at >= now - timedelta(hours=hours),
                    Metric.value.is_not(None),
                )
                .group_by(Metric.metric_name, bucket)
                .order_by(bucket)
            )
        ).all()
        series: dict[str, list[dict]] = {c: [] for c in HISTORY_CHANNELS}
        for name, t, avg, peak in rows:
            series[name].append({"t": t, "avg": float(avg), "max": float(peak)})
        return {"hours": hours, "bucket_seconds": width, "series": series}


__all__ = ["HISTORY_CHANNELS", "InterfaceNotFound", "NetworkCenterService", "SORTS", "STALE_AFTER", "STATUS_FILTERS", "TOP_N"]
