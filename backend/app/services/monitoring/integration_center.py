"""
Integration Center (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P12 slice 1).

One read-only view over everything that feeds the platform:

  * monitoring sources (`monitoring_sources`, collected by the collector
    framework) with their health state machine, the collector runs of the last
    24 hours, sensor and device counts;
  * generic integrations (`integrations`), with the status their own
    connectors maintain.

Each row gets one operator-facing `state`, derived only from what was
recorded, never assumed:

  disabled      the source / integration is switched off
  failing       the last collection failed (or the integration reports a
                connection / authentication / discovery failure)
  degraded      the health state machine marked the source degraded
  never_synced  nothing has ever been collected successfully
  stale         an active source whose last success is older than three poll
                intervals (at least 15 minutes)
  healthy       otherwise

Synthetic test sources (TEST_SOURCE_TYPES) are left out, as in the collector
health rollup.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, and_, cast, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collector_run import CollectorRun
from app.models.device import Device
from app.models.integration import Integration
from app.models.monitoring_source import HEALTH_STATUS_DEGRADED, MonitoringSource
from app.models.sensor import Sensor
from app.models.source_health_audit import (
    SOURCE_HEALTH_EVENT_RUN_RECORDED,
    SOURCE_HEALTH_EVENT_TRANSITION,
    SourceHealthAudit,
)
from app.services.collector_health_service import TEST_SOURCE_TYPES

RUN_WINDOW = timedelta(hours=24)
STALE_FLOOR = timedelta(minutes=15)
STALE_POLLS = 3
HISTORY_LIMIT = 50
ERROR_CHARS = 500

STATES = ("failing", "degraded", "stale", "never_synced", "disabled", "healthy")
_SEVERITY = {s: i for i, s in enumerate(STATES)}
_INTEGRATION_FAILING = frozenset({"disconnected", "authentication_failed", "discovery_failed"})


def _run_level_event():
    """Health events that record a run without changing any health state.

    `run_recorded` since 2026-11-12; before that the same rows were written as
    `health_transition` with an unchanged health status (`Degraded -> Degraded`
    for every failed run of an outage), so those count as run-level too.
    """
    return (SourceHealthAudit.event_type == SOURCE_HEALTH_EVENT_RUN_RECORDED) | and_(
        SourceHealthAudit.event_type == SOURCE_HEALTH_EVENT_TRANSITION,
        SourceHealthAudit.from_health_status.is_not_distinct_from(SourceHealthAudit.to_health_status),
    )


class IntegrationSourceNotFound(LookupError):
    pass


def _err(text: str | None) -> str | None:
    if not text:
        return None
    return text if len(text) <= ERROR_CHARS else text[: ERROR_CHARS - 1] + "…"


def source_state(source: MonitoringSource, now: datetime) -> str:
    if not source.is_active or source.status == "disabled":
        return "disabled"
    if source.status == "error":
        return "failing"
    if source.health_status == HEALTH_STATUS_DEGRADED:
        return "degraded"
    if source.last_successful_run_at is None:
        return "never_synced"
    stale_after = max(STALE_FLOOR, timedelta(seconds=(source.poll_interval_seconds or 60) * STALE_POLLS))
    if now - source.last_successful_run_at > stale_after:
        return "stale"
    return "healthy"


def integration_state(integration: Integration) -> str:
    if integration.status == "disabled":
        return "disabled"
    if integration.status in _INTEGRATION_FAILING:
        return "failing"
    if integration.status == "degraded":
        return "degraded"
    if integration.last_successful_collection is None:
        return "never_synced"
    return "healthy"


class IntegrationCenter:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _run_stats(self, tenant_id: uuid.UUID, since: datetime, source_ids: list[uuid.UUID] | None = None) -> dict:
        ok = CollectorRun.status.in_(("success", "partial"))
        q = (
            select(CollectorRun.monitoring_source_id, func.count(), func.count().filter(ok),
                   func.count().filter(CollectorRun.status == "error"),
                   func.avg(cast(func.extract("epoch", CollectorRun.finished_at - CollectorRun.started_at), Float) * 1000.0))
            .where(CollectorRun.tenant_id == tenant_id, CollectorRun.started_at >= since)
            .group_by(CollectorRun.monitoring_source_id)
        )
        if source_ids is not None:
            q = q.where(CollectorRun.monitoring_source_id.in_(source_ids))
        return {sid: (n, good, bad, avg) for sid, n, good, bad, avg in (await self.db.execute(q)).all()}

    async def _last_runs(self, tenant_id: uuid.UUID) -> dict:
        rows = (
            await self.db.execute(
                select(CollectorRun.monitoring_source_id, CollectorRun.status, CollectorRun.started_at)
                .where(CollectorRun.tenant_id == tenant_id)
                .distinct(CollectorRun.monitoring_source_id)
                .order_by(CollectorRun.monitoring_source_id, CollectorRun.started_at.desc())
            )
        ).all()
        return {sid: (status, started) for sid, status, started in rows}

    async def _counts(self, model, tenant_id: uuid.UUID) -> dict:
        rows = (
            await self.db.execute(
                select(model.monitoring_source_id, func.count())
                .where(model.tenant_id == tenant_id, model.is_deleted.is_(False), model.monitoring_source_id.is_not(None))
                .group_by(model.monitoring_source_id)
            )
        ).all()
        return dict(rows)

    def _source_row(self, s: MonitoringSource, now: datetime, runs: dict, last: dict, sensors: dict, devices: dict) -> dict:
        n, good, bad, avg = runs.get(s.id, (0, 0, 0, None))
        last_status, last_started = last.get(s.id, (None, None))
        return {
            "kind": "source", "id": s.id, "name": s.name, "type": s.source_type, "vendor": s.vendor,
            "endpoint": s.hostname or s.ip_address or (s.config or {}).get("base_url"),
            "state": source_state(s, now), "status": s.status, "health_status": s.health_status, "enabled": s.is_active,
            "credential_configured": s.credential_configured, "poll_interval_seconds": s.poll_interval_seconds,
            "last_success_at": s.last_successful_run_at, "last_failure_at": s.last_failed_run_at,
            "last_attempt_at": last_started or s.last_sync_at, "last_run_status": last_status, "next_run_at": s.next_run_at,
            "consecutive_failures": s.consecutive_failures, "last_error": _err(s.last_error),
            "sensors": sensors.get(s.id, 0), "devices": devices.get(s.id, 0),
            "runs_24h": n, "failed_runs_24h": bad,
            "success_rate_24h": round(good / n * 100, 1) if n else None,
            "avg_run_ms": None if avg is None else round(float(avg)),
        }

    @staticmethod
    def _integration_row(i: Integration) -> dict:
        return {
            "kind": "integration", "id": i.id, "name": i.name, "type": i.provider, "vendor": i.provider_type,
            "endpoint": (i.configuration or {}).get("endpoint"),
            "state": integration_state(i), "status": i.status, "health_status": None, "enabled": i.status != "disabled",
            "credential_configured": bool(i.credential_reference), "poll_interval_seconds": None,
            "last_success_at": i.last_successful_collection, "last_failure_at": i.last_error_at,
            "last_attempt_at": i.last_synchronization, "last_run_status": None, "next_run_at": None,
            "consecutive_failures": None, "last_error": _err(i.last_error),
            "sensors": None, "devices": None, "runs_24h": None, "failed_runs_24h": None,
            "success_rate_24h": None, "avg_run_ms": None, "health_score": i.health_score,
        }

    async def overview(self, tenant_id: uuid.UUID, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        sources = (
            await self.db.execute(
                select(MonitoringSource)
                .where(MonitoringSource.tenant_id == tenant_id, MonitoringSource.is_deleted.is_(False),
                       MonitoringSource.source_type.notin_(TEST_SOURCE_TYPES))
            )
        ).scalars().all()
        integrations = (
            await self.db.execute(
                select(Integration).where(Integration.tenant_id == tenant_id, Integration.is_deleted.is_(False))
            )
        ).scalars().all()
        runs = await self._run_stats(tenant_id, now - RUN_WINDOW, [s.id for s in sources]) if sources else {}
        last = await self._last_runs(tenant_id) if sources else {}
        sensors = await self._counts(Sensor, tenant_id) if sources else {}
        devices = await self._counts(Device, tenant_id) if sources else {}

        items = [self._source_row(s, now, runs, last, sensors, devices) for s in sources]
        items += [self._integration_row(i) for i in integrations]
        items.sort(key=lambda r: (_SEVERITY[r["state"]], r["name"].lower()))
        by_state = {s: sum(1 for r in items if r["state"] == s) for s in STATES}
        return {
            "generated_at": now,
            "summary": {
                "total": len(items), "sources": len(sources), "integrations": len(integrations), **by_state,
                "runs_24h": sum(r["runs_24h"] or 0 for r in items),
                "failed_runs_24h": sum(r["failed_runs_24h"] or 0 for r in items),
            },
            "items": items,
        }

    async def source_detail(self, tenant_id: uuid.UUID, source_id: uuid.UUID, now: datetime | None = None,
                            include_run_events: bool = False) -> dict:
        now = now or datetime.now(timezone.utc)
        source = (
            await self.db.execute(
                select(MonitoringSource).where(MonitoringSource.id == source_id, MonitoringSource.tenant_id == tenant_id,
                                               MonitoringSource.is_deleted.is_(False))
            )
        ).scalar_one_or_none()
        if source is None:
            raise IntegrationSourceNotFound(str(source_id))
        runs = await self._run_stats(tenant_id, now - RUN_WINDOW, [source.id])
        last = {}
        history = (
            await self.db.execute(
                select(CollectorRun).where(CollectorRun.tenant_id == tenant_id, CollectorRun.monitoring_source_id == source.id)
                .order_by(CollectorRun.started_at.desc()).limit(HISTORY_LIMIT)
            )
        ).scalars().all()
        if history:
            last[source.id] = (history[0].status, history[0].started_at)
        sensors = dict((await self.db.execute(
            select(Sensor.monitoring_source_id, func.count())
            .where(Sensor.monitoring_source_id == source.id, Sensor.is_deleted.is_(False))
            .group_by(Sensor.monitoring_source_id))).all())
        devices = dict((await self.db.execute(
            select(Device.monitoring_source_id, func.count())
            .where(Device.monitoring_source_id == source.id, Device.is_deleted.is_(False))
            .group_by(Device.monitoring_source_id))).all())
        of_source = (SourceHealthAudit.tenant_id == tenant_id, SourceHealthAudit.monitoring_source_id == source.id)
        run_level = _run_level_event()
        hidden = 0
        events_q = select(SourceHealthAudit).where(*of_source)
        if not include_run_events:
            events_q = events_q.where(not_(run_level))
            hidden = (await self.db.execute(
                select(func.count()).select_from(SourceHealthAudit).where(*of_source, run_level))).scalar_one()
        events = (
            await self.db.execute(events_q.order_by(SourceHealthAudit.created_at.desc()).limit(HISTORY_LIMIT))
        ).scalars().all()
        return {
            "source": self._source_row(source, now, runs, last, sensors, devices),
            "runs": [
                {
                    "id": r.id, "trigger": r.trigger, "status": r.status, "started_at": r.started_at, "finished_at": r.finished_at,
                    "duration_ms": None if r.finished_at is None else round((r.finished_at - r.started_at).total_seconds() * 1000),
                    "devices": r.devices_upserted, "interfaces": r.interfaces_upserted, "sensors": r.sensors_upserted,
                    "metrics": r.metrics_recorded, "events": r.events_recorded, "alerts": r.alerts_upserted,
                    "error": _err(r.error),
                }
                for r in history
            ],
            "health_events": [
                {
                    "id": e.id, "event_type": e.event_type, "created_at": e.created_at,
                    "from_health_status": e.from_health_status, "to_health_status": e.to_health_status,
                    "from_status": e.from_status, "to_status": e.to_status,
                    "consecutive_failures": e.to_consecutive_failures, "reason": _err(e.reason),
                }
                for e in events
            ],
            "include_run_events": include_run_events,
            "hidden_run_events": hidden,
        }


__all__ = ["IntegrationCenter", "IntegrationSourceNotFound", "STATES", "integration_state", "source_state"]
