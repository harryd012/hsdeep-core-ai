"""
Dynamic baselines (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P9 slice 2).

Opt-in per sensor. Learning reads the sensor's own primary-channel history
(the readings the saver records, P1) over the last WINDOW_DAYS and stores the
mean / standard deviation per hour of the day (UTC) and overall. Evaluation
judges the sensor's latest reading against its hour (`baseline_math.judge`)
and passes the verdict to the Alert Engine (`AlertService.evaluate_baseline`).

A reading older than STALE_AFTER is not judged ("stale"); a paused or disabled
sensor is not judged ("paused"); in both cases an open anomaly alert resolves,
so nothing is left open on data the platform no longer sees.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metric import Metric
from app.models.sensor import Sensor
from app.models.sensor_baseline import SensorBaseline
from app.repositories.alert_repository import AlertRepository
from app.services.alert_service import AlertService
from app.services.monitoring.baseline_math import WINDOW_DAYS, Baseline, Stat, judge
from app.services.monitoring.sensor_service import channel_key
from app.services.monitoring.sensor_policy import PRIMARY_CHANNEL

STALE_AFTER = timedelta(hours=2)
RELEARN_AFTER = timedelta(hours=1)


async def learn(db: AsyncSession, sensor: Sensor, now: datetime) -> Baseline:
    """The sensor's baseline from its primary-channel history in the window."""
    since = now - timedelta(days=WINDOW_DAYS)
    hour = func.extract("hour", func.timezone("UTC", Metric.recorded_at))
    where = (
        Metric.tenant_id == sensor.tenant_id, Metric.sensor_id == sensor.id, channel_key() == PRIMARY_CHANNEL,
        Metric.recorded_at >= since, Metric.value.is_not(None),
    )
    rows = (
        await db.execute(
            select(hour, func.avg(Metric.value), func.coalesce(func.stddev_samp(Metric.value), 0.0), func.count())
            .where(*where).group_by(hour)
        )
    ).all()
    overall = (
        await db.execute(
            select(func.avg(Metric.value), func.coalesce(func.stddev_samp(Metric.value), 0.0), func.count(),
                   func.min(Metric.recorded_at), func.max(Metric.recorded_at))
            .where(*where)
        )
    ).one()
    mean, std, n, first, last = overall
    span = (last - first).total_seconds() / 86400 if first and last else 0.0
    return Baseline(
        hours={int(h): Stat(float(m), float(s), int(c)) for h, m, s, c in rows},
        overall=Stat(float(mean), float(std), int(n)) if n else None,
        span_days=round(span, 2),
    )


class BaselineService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def evaluate(self, row: SensorBaseline, sensor: Sensor, *, now: datetime | None = None,
                       relearn: bool = False) -> str:
        """Learn when due, judge the latest reading, update the alert; returns the state."""
        now = now or datetime.now(timezone.utc)
        if relearn or row.computed_at is None or now - row.computed_at >= RELEARN_AFTER:
            row.stats = (await learn(self.db, sensor, now)).to_json()
            row.computed_at = now
        baseline = Baseline.from_json(row.stats)
        alerts = AlertService(AlertRepository(self.db))
        row.last_evaluated_at = now

        if not row.enabled or sensor.admin_state != "active" or sensor.is_deleted:
            row.state = "paused"
            await alerts.evaluate_baseline(sensor, anomalous=False, value=None, expected=None, z=None, low=None, high=None)
            return row.state
        if sensor.last_value_at is None or now - sensor.last_value_at > STALE_AFTER:
            row.state = "stale" if baseline.ready else "learning"
            await alerts.evaluate_baseline(sensor, anomalous=False, value=None, expected=None, z=None, low=None, high=None)
            return row.state

        hour = sensor.last_value_at.astimezone(timezone.utc).hour
        verdict = judge(baseline, sensor.current_value, hour, sensitivity=row.sensitivity, direction=row.direction,
                        currently_anomalous=row.state == "anomalous")
        row.state = verdict.state
        row.last_value, row.last_expected, row.last_z = sensor.current_value, verdict.expected, verdict.z
        await alerts.evaluate_baseline(
            sensor, anomalous=verdict.state == "anomalous", value=sensor.current_value, expected=verdict.expected,
            z=verdict.z, low=verdict.low, high=verdict.high,
        )
        return row.state

    async def evaluate_all(self, *, now: datetime | None = None, limit: int = 2000) -> dict[str, int]:
        """One scheduler pass over enabled baselines (and disabled ones still holding an open state)."""
        now = now or datetime.now(timezone.utc)
        pairs = (
            await self.db.execute(
                select(SensorBaseline, Sensor)
                .join(Sensor, Sensor.id == SensorBaseline.sensor_id)
                .where((SensorBaseline.enabled.is_(True)) | (SensorBaseline.state == "anomalous"))
                .order_by(SensorBaseline.last_evaluated_at.asc().nulls_first())
                .limit(limit)
            )
        ).all()
        counts: dict[str, int] = {}
        for row, sensor in pairs:
            state = await self.evaluate(row, sensor, now=now)
            counts[state] = counts.get(state, 0) + 1
            await self.db.commit()
        return counts

    async def configure(self, tenant_id: uuid.UUID, sensor: Sensor, *, enabled: bool, direction: str,
                        sensitivity: float) -> tuple[SensorBaseline, dict, dict]:
        """Create or change a sensor's baseline, learn and judge at once; returns (row, old, new)."""
        row = (
            await self.db.execute(select(SensorBaseline).where(SensorBaseline.sensor_id == sensor.id))
        ).scalar_one_or_none()
        old: dict = {}
        if row is None:
            row = SensorBaseline(tenant_id=tenant_id, sensor_id=sensor.id, enabled=enabled, direction=direction,
                                 sensitivity=sensitivity, stats={}, state="learning")
            self.db.add(row)
        else:
            old = {"enabled": row.enabled, "direction": row.direction, "sensitivity": row.sensitivity}
            row.enabled, row.direction, row.sensitivity = enabled, direction, sensitivity
        await self.evaluate(row, sensor, relearn=True)
        await self.db.flush()
        new = {"enabled": row.enabled, "direction": row.direction, "sensitivity": row.sensitivity}
        return row, old, new


__all__ = ["BaselineService", "RELEARN_AFTER", "STALE_AFTER", "learn"]
