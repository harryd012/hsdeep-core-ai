"""
Observability reports (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P11 slice 1).

Period reports computed in the database from what was actually recorded,
tenant-scoped, for a window of at most MAX_WINDOW:

  * availability - per device, the share of recorded reachability checks that
    were up (canonical `reachability` telemetry, one sample per collection);
    a device with no sample in the window is "not measured", never 100 %;
  * sensors      - per sensor, average / minimum / maximum / p95 of its
    primary-channel readings, and the time its status alerts were open within
    the window;
  * interfaces   - per interface, average and peak inbound / outbound traffic
    and utilisation, and average / peak error rates, from the P4 history.

Percentages are of samples, not of wall-clock time: a device polled every
5 minutes that missed one poll out of 288 is 99.65 % available.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.device import Device
from app.models.interface import Interface
from app.models.metric import Metric
from app.models.sensor import Sensor
from app.services.monitoring.sensor_service import channel_key
from app.services.monitoring.sensor_policy import PRIMARY_CHANNEL

MAX_WINDOW = timedelta(days=90)
DEFAULT_WINDOW = timedelta(days=7)
MAX_ROWS = 2000


class WindowInvalid(ValueError):
    pass


def window(since: datetime | None, until: datetime | None, now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(timezone.utc)
    until = until or now
    since = since or until - DEFAULT_WINDOW
    if since.tzinfo is None or until.tzinfo is None:
        raise WindowInvalid("since and until need a time zone")
    if since >= until:
        raise WindowInvalid("since must be before until")
    if until - since > MAX_WINDOW:
        raise WindowInvalid("the window can be at most 90 days")
    return since, until


def _f(v) -> float | None:
    return None if v is None else round(float(v), 4)


class ObservabilityReports:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def availability(self, tenant_id: uuid.UUID, since: datetime, until: datetime) -> dict:
        up = Metric.boolean_value.is_(True)
        rows = (
            await self.db.execute(
                select(Metric.device_id, func.count(), func.count().filter(up), func.min(Metric.recorded_at), func.max(Metric.recorded_at))
                .where(Metric.tenant_id == tenant_id, Metric.metric_name == "reachability", Metric.capability == "availability",
                       Metric.device_id.is_not(None), Metric.boolean_value.is_not(None),
                       Metric.recorded_at >= since, Metric.recorded_at < until)
                .group_by(Metric.device_id)
            )
        ).all()
        stats = {d: (n, u, first, last) for d, n, u, first, last in rows}
        devices = (
            await self.db.execute(
                select(Device.id, Device.name, Device.device_type, Device.ip_address, Device.criticality)
                .where(Device.tenant_id == tenant_id, Device.is_deleted.is_(False))
                .order_by(Device.name)
            )
        ).all()
        items, unmeasured = [], 0
        for d in devices:
            if d.id not in stats:
                unmeasured += 1
                continue
            n, u, first, last = stats[d.id]
            items.append({
                "device_id": d.id, "device": d.name, "device_type": d.device_type, "address": d.ip_address,
                "criticality": d.criticality, "samples": n, "up_samples": u, "down_samples": n - u,
                "availability_pct": round(u / n * 100, 3), "first_sample": first, "last_sample": last,
            })
        items.sort(key=lambda r: (r["availability_pct"], r["device"]))
        total = sum(r["samples"] for r in items)
        total_up = sum(r["up_samples"] for r in items)
        return {
            "summary": {
                "devices_measured": len(items), "devices_not_measured": unmeasured,
                "fleet_availability_pct": round(total_up / total * 100, 3) if total else None,
                "devices_below_99_9": sum(1 for r in items if r["availability_pct"] < 99.9),
                "devices_below_99": sum(1 for r in items if r["availability_pct"] < 99.0),
                "samples": total,
            },
            "items": items[:MAX_ROWS],
        }

    async def sensors(self, tenant_id: uuid.UUID, since: datetime, until: datetime, *, metric_type: str | None = None) -> dict:
        where = [Metric.tenant_id == tenant_id, Metric.sensor_id.is_not(None), channel_key() == PRIMARY_CHANNEL,
                 Metric.value.is_not(None), Metric.recorded_at >= since, Metric.recorded_at < until]
        stats = {
            sid: (n, avg, lo, hi, p95)
            for sid, n, avg, lo, hi, p95 in (
                await self.db.execute(
                    select(Metric.sensor_id, func.count(), func.avg(Metric.value), func.min(Metric.value), func.max(Metric.value),
                           func.percentile_cont(0.95).within_group(Metric.value))
                    .where(*where).group_by(Metric.sensor_id)
                )
            ).all()
        }
        # Minutes each sensor's status alerts were open inside the window.
        start = func.greatest(Alert.triggered_at, since)
        end = func.least(func.coalesce(Alert.resolved_at, until), until)
        alert_rows = (
            await self.db.execute(
                select(Alert.sensor_id, func.count(), func.sum(cast(func.extract("epoch", end - start), Float) / 60.0))
                .where(Alert.tenant_id == tenant_id, Alert.sensor_id.is_not(None), Alert.is_deleted.is_(False),
                       Alert.triggered_at < until, or_(Alert.resolved_at.is_(None), Alert.resolved_at > since))
                .group_by(Alert.sensor_id)
            )
        ).all()
        alerts = {sid: (n, m) for sid, n, m in alert_rows}
        q = (
            select(Sensor.id, Sensor.name, Sensor.metric_type, Sensor.unit, Sensor.status, Device.name)
            .outerjoin(Device, Device.id == Sensor.device_id)
            .where(Sensor.tenant_id == tenant_id, Sensor.is_deleted.is_(False),
                   Sensor.id.in_(list(set(stats) | set(alerts)) or [uuid.UUID(int=0)]))
        )
        if metric_type:
            q = q.where(Sensor.metric_type == metric_type)
        items = []
        for sid, name, mtype, unit, status, device in (await self.db.execute(q)).all():
            n, avg, lo, hi, p95 = stats.get(sid, (0, None, None, None, None))
            an, minutes = alerts.get(sid, (0, 0.0))
            items.append({
                "sensor_id": sid, "sensor": name, "metric_type": mtype, "unit": unit, "status": status, "device": device,
                "samples": n, "avg": _f(avg), "min": _f(lo), "max": _f(hi), "p95": _f(p95),
                "alerts": an, "alert_minutes": round(float(minutes or 0.0), 1),
            })
        items.sort(key=lambda r: (-r["alert_minutes"], r["sensor"]))
        period_minutes = (until - since).total_seconds() / 60
        return {
            "summary": {
                "sensors": len(items), "with_readings": sum(1 for r in items if r["samples"]),
                "with_alerts": sum(1 for r in items if r["alerts"]),
                "alert_minutes": round(sum(r["alert_minutes"] for r in items), 1),
                "period_minutes": round(period_minutes, 1),
            },
            "items": items[:MAX_ROWS],
        }

    async def interfaces(self, tenant_id: uuid.UUID, since: datetime, until: datetime) -> dict:
        def agg(name: str):
            pick = Metric.metric_name == name
            return func.avg(Metric.value).filter(pick), func.max(Metric.value).filter(pick)

        rx_avg, rx_max = agg("rx_bps")
        tx_avg, tx_max = agg("tx_bps")
        ut_avg, ut_max = agg("interface_utilization_pct")
        ei_avg, ei_max = agg("errors_in")
        eo_avg, eo_max = agg("errors_out")
        rows = (
            await self.db.execute(
                select(Metric.interface_id, func.count(func.distinct(Metric.recorded_at)),
                       rx_avg, rx_max, tx_avg, tx_max, ut_avg, ut_max, ei_avg, ei_max, eo_avg, eo_max)
                .where(Metric.tenant_id == tenant_id, Metric.interface_id.is_not(None), Metric.value.is_not(None),
                       Metric.metric_name.in_(("rx_bps", "tx_bps", "interface_utilization_pct", "errors_in", "errors_out")),
                       Metric.recorded_at >= since, Metric.recorded_at < until)
                .group_by(Metric.interface_id)
            )
        ).all()
        if not rows:
            return {"summary": {"interfaces": 0, "busy_over_80": 0, "with_errors": 0}, "items": []}
        names = {
            i: (iname, speed, dname, did)
            for i, iname, speed, dname, did in (
                await self.db.execute(
                    select(Interface.id, Interface.name, Interface.speed_mbps, Device.name, Device.id)
                    .join(Device, Device.id == Interface.device_id)
                    .where(Interface.tenant_id == tenant_id, Interface.id.in_([r[0] for r in rows]))
                )
            ).all()
        }
        items = []
        for iid, n, *v in rows:
            if iid not in names:
                continue
            iname, speed, dname, did = names[iid]
            items.append({
                "interface_id": iid, "interface": iname, "device_id": did, "device": dname, "speed_mbps": speed, "samples": n,
                "rx_avg_bps": _f(v[0]), "rx_peak_bps": _f(v[1]), "tx_avg_bps": _f(v[2]), "tx_peak_bps": _f(v[3]),
                "utilization_avg_pct": _f(v[4]), "utilization_peak_pct": _f(v[5]),
                "errors_in_avg_per_s": _f(v[6]), "errors_in_peak_per_s": _f(v[7]),
                "errors_out_avg_per_s": _f(v[8]), "errors_out_peak_per_s": _f(v[9]),
            })
        items.sort(key=lambda r: (-(r["utilization_peak_pct"] or -1), r["device"], r["interface"]))
        return {
            "summary": {
                "interfaces": len(items),
                "busy_over_80": sum(1 for r in items if (r["utilization_peak_pct"] or 0) >= 80),
                "with_errors": sum(1 for r in items if (r["errors_in_peak_per_s"] or 0) > 0 or (r["errors_out_peak_per_s"] or 0) > 0),
            },
            "items": items[:MAX_ROWS],
        }


__all__ = ["MAX_WINDOW", "ObservabilityReports", "WindowInvalid", "window"]
