"""
Logs Explorer (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P7 slice 1).

Tenant-scoped search over log lines stored as normalized events with
telemetry_type LOG (syslog through the receiver, or any collector posting
logs to the ingest API). Every query is bounded to a window of at most
MAX_WINDOW, newest first. Text search is a case-insensitive substring match
over the message (title and the full text when longer); there is no full-text
index yet (pg_trgm needs a database superuser), so a search is limited to the
window and stated as such.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.normalized_event import NormalizedEvent
from app.services.monitoring.sensor_service import bucket_seconds

MAX_WINDOW = timedelta(days=7)
DEFAULT_WINDOW = timedelta(hours=1)
FACET_LIMIT = 10
LOG = "LOG"


class WindowInvalid(ValueError):
    pass


@dataclass(frozen=True)
class LogQuery:
    since: datetime
    until: datetime
    q: str | None = None
    severities: tuple[str, ...] = ()
    host: str | None = None
    app: str | None = None
    device_id: uuid.UUID | None = None
    event_type: str | None = None


def window(since: datetime | None, until: datetime | None, now: datetime | None = None) -> tuple[datetime, datetime]:
    """(since, until): defaults to the last hour; at most 7 days; since before until."""
    now = now or datetime.now(timezone.utc)
    until = until or now
    since = since or until - DEFAULT_WINDOW
    if since.tzinfo is None or until.tzinfo is None:
        raise WindowInvalid("since and until need a time zone")
    if since >= until:
        raise WindowInvalid("since must be before until")
    if until - since > MAX_WINDOW:
        raise WindowInvalid("the window can be at most 7 days")
    return since, until


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _host():
    return func.coalesce(NormalizedEvent.attributes["host"].astext, NormalizedEvent.attributes["sender"].astext)


class LogExplorer:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _where(self, tenant_id: uuid.UUID, q: LogQuery, *, skip: str | None = None):
        conds = [
            NormalizedEvent.tenant_id == tenant_id,
            NormalizedEvent.telemetry_type == LOG,
            NormalizedEvent.is_deleted.is_(False),
            NormalizedEvent.event_timestamp >= q.since,
            NormalizedEvent.event_timestamp < q.until,
        ]
        if q.q:
            like = f"%{_escape_like(q.q.strip())}%"
            conds.append(or_(NormalizedEvent.title.ilike(like, escape="\\"), NormalizedEvent.description.ilike(like, escape="\\")))
        if q.severities and skip != "severity":
            conds.append(NormalizedEvent.severity.in_(q.severities))
        if q.host and skip != "host":
            conds.append(_host() == q.host)
        if q.app and skip != "app":
            conds.append(NormalizedEvent.attributes["app"].astext == q.app)
        if q.device_id:
            conds.append(NormalizedEvent.device_id == q.device_id)
        if q.event_type and skip != "event_type":
            conds.append(NormalizedEvent.event_type == q.event_type)
        return conds

    async def search(self, tenant_id: uuid.UUID, q: LogQuery, *, limit: int, offset: int) -> tuple[list[dict], int]:
        where = self._where(tenant_id, q)
        total = (await self.db.execute(select(func.count()).select_from(NormalizedEvent).where(*where))).scalar_one()
        rows = (
            await self.db.execute(
                select(NormalizedEvent, Device.name)
                .outerjoin(Device, Device.id == NormalizedEvent.device_id)
                .where(*where)
                .order_by(NormalizedEvent.event_timestamp.desc(), NormalizedEvent.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        items = []
        for e, device_name in rows:
            a = e.attributes or {}
            items.append({
                "id": e.id, "timestamp": e.event_timestamp, "severity": e.severity, "event_type": e.event_type,
                "message": e.description or e.title, "host": a.get("host") or a.get("sender"), "app": a.get("app"),
                "facility": a.get("facility"), "syslog_severity": a.get("syslog_severity"),
                "device_id": e.device_id, "device_name": device_name, "attributes": a,
            })
        return items, total

    async def stats(self, tenant_id: uuid.UUID, q: LogQuery) -> dict:
        """Volume per time bucket by severity, and the top values of each facet
        (a facet ignores its own filter, so its other values stay visible)."""
        hours = max(1, int((q.until - q.since).total_seconds() // 3600) or 1)
        width = bucket_seconds(hours)
        bucket = func.to_timestamp(func.floor(func.extract("epoch", NormalizedEvent.event_timestamp) / width) * width)
        rows = (
            await self.db.execute(
                select(bucket, NormalizedEvent.severity, func.count())
                .where(*self._where(tenant_id, q))
                .group_by(bucket, NormalizedEvent.severity)
                .order_by(bucket)
            )
        ).all()
        series: dict[datetime, dict[str, int]] = {}
        for t, severity, n in rows:
            series.setdefault(t, {"critical": 0, "warning": 0, "info": 0})[severity] = n

        async def facet(expr, name: str) -> list[dict]:
            values = (
                await self.db.execute(
                    select(expr, func.count())
                    .where(*self._where(tenant_id, q, skip=name), expr.is_not(None))
                    .group_by(expr)
                    .order_by(func.count().desc())
                    .limit(FACET_LIMIT)
                )
            ).all()
            return [{"value": v, "count": n} for v, n in values]

        return {
            "bucket_seconds": width,
            "histogram": [{"t": t, **counts} for t, counts in series.items()],
            "facets": {
                "severity": await facet(NormalizedEvent.severity, "severity"),
                "host": await facet(_host(), "host"),
                "app": await facet(NormalizedEvent.attributes["app"].astext, "app"),
                "event_type": await facet(NormalizedEvent.event_type, "event_type"),
            },
        }


__all__ = ["DEFAULT_WINDOW", "LogExplorer", "LogQuery", "MAX_WINDOW", "WindowInvalid", "window"]
